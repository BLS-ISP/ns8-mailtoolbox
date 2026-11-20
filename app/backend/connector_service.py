"""Phase 3c External POP3/IMAP Connectors with Fernet Encryption & Admin Failure Alerts.

Polls external ISP mailboxes (IMAPS, IMAP, POP3S, POP3), deduplicates by UID/UIDL in SQLite,
delivers new RFC822 messages into local NS8 mailboxes via SMTP submission, and sends an
automatic warning email (with 24h cooldown) to `admin_alert_email` when an external login
or connection fails (e.g. changed password at ISP).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import email
from email.message import EmailMessage
import email.policy
import hashlib
import imaplib
import logging
import poplib
import ssl
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from app.backend.config import AppSettings, get_settings
from app.backend.db import (
    add_audit_entry,
    delete_connector_db,
    get_connector_db,
    get_seen_connector_uids,
    list_connectors_db,
    mark_connector_uids_seen,
    save_connector_db,
    update_connector_sync_result,
    utc_now_iso,
)
from app.backend.quarantine_service import deliver_smtp_raw

logger = logging.getLogger(__name__)

ExternalImapFactory = Callable[[str, int, bool], Any]
ExternalPop3Factory = Callable[[str, int, bool], Any]

_custom_ext_imap_factory: ExternalImapFactory | None = None
_custom_ext_pop3_factory: ExternalPop3Factory | None = None


def set_external_imap_factory(factory: ExternalImapFactory | None) -> None:
    """Override external IMAP client creation (for tests)."""
    global _custom_ext_imap_factory
    _custom_ext_imap_factory = factory


def set_external_pop3_factory(factory: ExternalPop3Factory | None) -> None:
    """Override external POP3 client creation (for tests)."""
    global _custom_ext_pop3_factory
    _custom_ext_pop3_factory = factory


def _get_fernet(secret_key: str | None = None) -> Fernet:
    raw_secret = secret_key or get_settings().secret_key
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw_secret.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt_connector_password(plaintext: str, secret_key: str | None = None) -> str:
    if not plaintext:
        return ""
    return _get_fernet(secret_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_connector_password(ciphertext: str, secret_key: str | None = None) -> str:
    if not ciphertext:
        return ""
    try:
        return _get_fernet(secret_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored connector password could not be decrypted") from exc


def _open_ext_imap(host: str, port: int, use_ssl: bool) -> Any:
    if _custom_ext_imap_factory is not None:
        return _custom_ext_imap_factory(host, int(port), bool(use_ssl))
    if use_ssl:
        ctx = ssl.create_default_context()
        return imaplib.IMAP4_SSL(host, int(port), ssl_context=ctx, timeout=15.0)
    return imaplib.IMAP4(host, int(port), timeout=15.0)


def _open_ext_pop3(host: str, port: int, use_ssl: bool) -> Any:
    if _custom_ext_pop3_factory is not None:
        return _custom_ext_pop3_factory(host, int(port), bool(use_ssl))
    if use_ssl:
        ctx = ssl.create_default_context()
        return poplib.POP3_SSL(host, int(port), context=ctx, timeout=15.0)
    return poplib.POP3(host, int(port), timeout=15.0)


def _extract_fetch_payload(fetch_data: Any) -> bytes | None:
    if not fetch_data:
        return None
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def list_connectors() -> list[dict[str, Any]]:
    settings = get_settings()
    return list_connectors_db(include_secret=False, db_path=settings.sqlite_path)


def save_connector(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    cid = payload.get("id")
    cid_int = int(cid) if cid is not None else None

    proto = str(payload.get("protocol") or "imaps").strip().lower()
    if proto not in ("imaps", "imap", "pop3s", "pop3"):
        proto = "imaps"
    use_ssl = bool(payload.get("use_ssl", proto in ("imaps", "pop3s")))
    if proto in ("imaps", "pop3s"):
        use_ssl = True

    raw_pw = str(payload.get("remote_password") or "")
    enc_pw: str | None
    if raw_pw:
        enc_pw = encrypt_connector_password(raw_pw, settings.secret_key)
    elif cid_int is not None:
        enc_pw = None  # preserve existing encrypted password
    else:
        enc_pw = ""

    saved = save_connector_db(
        connector_id=cid_int,
        name=str(payload.get("name") or "").strip() or str(payload.get("host") or "Connector"),
        target_user=str(payload.get("target_user") or "").strip(),
        target_email=str(payload.get("target_email") or "").strip(),
        protocol=proto,
        host=str(payload.get("host") or "").strip(),
        port=int(payload.get("port") or (993 if proto == "imaps" else 995 if proto == "pop3s" else 143)),
        use_ssl=use_ssl,
        remote_user=str(payload.get("remote_user") or "").strip(),
        encrypted_password=enc_pw,
        interval_minutes=int(payload.get("interval_minutes") or 5),
        leave_on_server=bool(payload.get("leave_on_server", True)),
        enabled=bool(payload.get("enabled", True)),
        db_path=settings.sqlite_path,
    )
    add_audit_entry(
        actor="cockpit-admin",
        source="cockpit",
        target_user=saved["target_user"],
        action="connector_saved",
        details=f"Saved external connector {saved['name']!r} ({saved['remote_user']}@{saved['host']}) -> {saved['target_email']}",
        db_path=settings.sqlite_path,
    )
    return saved


def delete_connector(connector_id: int) -> bool:
    settings = get_settings()
    ok = delete_connector_db(connector_id, settings.sqlite_path)
    if ok:
        add_audit_entry(
            actor="cockpit-admin",
            source="cockpit",
            target_user="",
            action="connector_deleted",
            details=f"Deleted external connector #{connector_id}",
            db_path=settings.sqlite_path,
        )
    return ok


def test_connector_connection(payload: dict[str, Any]) -> dict[str, Any]:
    """Test external POP3/IMAP credentials and report message count in remote inbox."""
    settings = get_settings()
    proto = str(payload.get("protocol") or "imaps").strip().lower()
    use_ssl = bool(payload.get("use_ssl", proto in ("imaps", "pop3s")))
    if proto in ("imaps", "pop3s"):
        use_ssl = True

    host = str(payload.get("host") or "").strip()
    port = int(payload.get("port") or 993)
    remote_user = str(payload.get("remote_user") or "").strip()
    remote_password = str(payload.get("remote_password") or "")

    if not remote_password and payload.get("id") is not None:
        existing = get_connector_db(int(payload["id"]), include_secret=True, db_path=settings.sqlite_path)
        if existing and existing.get("encrypted_password"):
            remote_password = decrypt_connector_password(
                existing["encrypted_password"],
                settings.secret_key,
            )

    try:
        if proto in ("imap", "imaps"):
            imap = _open_ext_imap(host, port, use_ssl)
            try:
                imap.login(remote_user, remote_password)
                typ, data = imap.select("INBOX", readonly=True)
                if typ != "OK":
                    raise RuntimeError("Failed selecting remote INBOX")
                msg_count = int(data[0].decode("ascii", errors="ignore")) if (data and data[0]) else 0
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        else:
            pop = _open_ext_pop3(host, port, use_ssl)
            try:
                pop.user(remote_user)
                pop.pass_(remote_password)
                msg_count, _ = pop.stat()
            finally:
                try:
                    pop.quit()
                except Exception:
                    pass

        return {
            "ok": True,
            "message": f"Connection successful ({msg_count} messages on server).",
            "message_count": int(msg_count),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": str(exc),
            "message_count": 0,
        }


def _should_send_failure_alert(alert_sent_at: str) -> bool:
    if not alert_sent_at:
        return True
    try:
        prev = datetime.fromisoformat(alert_sent_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - prev) >= timedelta(hours=24)
    except ValueError:
        return True


def _send_connector_failure_alert(
    conn_row: dict[str, Any],
    error_msg: str,
    failure_count: int,
    settings: AppSettings,
) -> bool:
    alert_to = (settings.runtime.admin_alert_email or "").strip()
    if not alert_to:
        return False
    if not _should_send_failure_alert(str(conn_row.get("alert_sent_at") or "")):
        return False

    domain = settings.ldap.domain or settings.portal_hostname or "localhost"
    mail_from = f"connector-alert@{domain}"

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = alert_to
    msg["Subject"] = f"[NS8 Mail Toolbox] External Mail Connector Alert: {conn_row['name']}"
    msg.set_content(
        "Warning: External POP3/IMAP mail connector synchronization failed.\n\n"
        f"Connector:            {conn_row['name']} (#{conn_row['id']})\n"
        f"Remote Account:       {conn_row['remote_user']} @ {conn_row['host']}:{conn_row['port']} ({conn_row['protocol']})\n"
        f"Target Mailbox:       {conn_row['target_email']}\n"
        f"Consecutive Failures: {failure_count}\n"
        f"Error Message:        {error_msg}\n\n"
        "Please check NS8 Cockpit -> Mail Toolbox -> Connectors to verify whether the external account password or server settings have changed.\n",
        cte="8bit",
    )
    try:
        smtp_policy = email.policy.SMTP.clone(max_line_length=998)
        deliver_smtp_raw(
            msg.as_bytes(policy=smtp_policy),
            mail_from,
            alert_to,
            settings.mail,
        )
        return True
    except Exception as smtp_exc:
        logger.warning("Failed sending admin connector alert to %s: %s", alert_to, smtp_exc)
        return False


def _prepare_forwarded_bytes(raw_bytes: bytes, connector_name: str) -> tuple[bytes, str]:
    parsed = email.message_from_bytes(raw_bytes, policy=email.policy.SMTP)
    orig_from = email.utils.parseaddr(str(parsed.get("From") or ""))[1].strip()
    header_line = f"X-NS8-Connector: {connector_name}\r\n".encode("utf-8")
    return header_line + raw_bytes, orig_from


def sync_connector_by_id(connector_id: int) -> dict[str, Any]:
    """Fetch unseen messages from an external POP3/IMAP account and deliver locally via SMTP."""
    settings = get_settings()
    conn_row = get_connector_db(connector_id, include_secret=True, db_path=settings.sqlite_path)
    if conn_row is None:
        return {"ok": False, "fetched": 0, "error": f"Connector #{connector_id} not found", "alert_sent": False}

    proto = str(conn_row["protocol"]).lower()
    use_ssl = bool(conn_row["use_ssl"]) or proto in ("imaps", "pop3s")
    host = str(conn_row["host"])
    port = int(conn_row["port"])
    remote_user = str(conn_row["remote_user"])
    leave_on_server = bool(conn_row["leave_on_server"])
    target_email = str(conn_row["target_email"])
    domain = settings.ldap.domain or "localhost"
    fallback_env_from = f"connector@{domain}"

    try:
        remote_password = decrypt_connector_password(
            str(conn_row.get("encrypted_password") or ""),
            settings.secret_key,
        )
        seen_uids = get_seen_connector_uids(connector_id, settings.sqlite_path)
        fetched_count = 0

        if proto in ("imap", "imaps"):
            imap = _open_ext_imap(host, port, use_ssl)
            try:
                imap.login(remote_user, remote_password)
                typ, _ = imap.select("INBOX", readonly=leave_on_server)
                if typ != "OK":
                    raise RuntimeError("Cannot select remote INBOX")
                s_typ, s_data = imap.uid("SEARCH", None, "ALL")
                uids = (
                    s_data[0].decode("ascii", errors="ignore").split()
                    if (s_typ == "OK" and s_data and s_data[0])
                    else []
                )
                newly_seen: list[str] = []
                for uid in uids:
                    if uid in seen_uids:
                        continue
                    f_typ, f_data = imap.uid("FETCH", uid, "(RFC822)")
                    if f_typ != "OK":
                        continue
                    raw_msg = _extract_fetch_payload(f_data)
                    if not raw_msg:
                        continue
                    out_bytes, orig_from = _prepare_forwarded_bytes(raw_msg, conn_row["name"])
                    deliver_smtp_raw(
                        out_bytes,
                        orig_from or fallback_env_from,
                        target_email,
                        settings.mail,
                    )
                    newly_seen.append(uid)
                    fetched_count += 1
                    if not leave_on_server:
                        imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")

                if newly_seen:
                    mark_connector_uids_seen(connector_id, newly_seen, settings.sqlite_path)
                if not leave_on_server and fetched_count > 0:
                    imap.expunge()
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        else:
            pop = _open_ext_pop3(host, port, use_ssl)
            try:
                pop.user(remote_user)
                pop.pass_(remote_password)
                _resp, uidl_lines, _octets = pop.uidl()
                newly_seen_pop: list[str] = []
                for raw_entry in uidl_lines:
                    decoded = (
                        raw_entry.decode("utf-8", errors="replace")
                        if isinstance(raw_entry, (bytes, bytearray))
                        else str(raw_entry)
                    ).strip()
                    parts = decoded.split(None, 1)
                    if len(parts) < 2:
                        continue
                    msg_num_str, uidl_val = parts[0], parts[1].strip()
                    if uidl_val in seen_uids:
                        continue
                    _r, msg_lines, _o = pop.retr(int(msg_num_str))
                    raw_msg = b"\r\n".join(
                        ln if isinstance(ln, (bytes, bytearray)) else str(ln).encode("utf-8")
                        for ln in msg_lines
                    ) + b"\r\n"
                    out_bytes, orig_from = _prepare_forwarded_bytes(raw_msg, conn_row["name"])
                    deliver_smtp_raw(
                        out_bytes,
                        orig_from or fallback_env_from,
                        target_email,
                        settings.mail,
                    )
                    newly_seen_pop.append(uidl_val)
                    fetched_count += 1
                    if not leave_on_server:
                        pop.dele(int(msg_num_str))

                if newly_seen_pop:
                    mark_connector_uids_seen(connector_id, newly_seen_pop, settings.sqlite_path)
            finally:
                try:
                    pop.quit()
                except Exception:
                    pass

        update_connector_sync_result(
            connector_id=connector_id,
            ok=True,
            fetched=fetched_count,
            db_path=settings.sqlite_path,
        )
        return {"ok": True, "fetched": fetched_count, "error": "", "alert_sent": False}
    except Exception as exc:
        err_str = str(exc)
        new_fail_count = int(conn_row.get("consecutive_failures") or 0) + 1
        alert_sent = _send_connector_failure_alert(conn_row, err_str, new_fail_count, settings)
        alert_ts = utc_now_iso() if alert_sent else None
        update_connector_sync_result(
            connector_id=connector_id,
            ok=False,
            error=err_str,
            alert_sent_at=alert_ts,
            db_path=settings.sqlite_path,
        )
        add_audit_entry(
            actor="system",
            source="connector",
            target_user=conn_row["target_user"],
            action="connector_sync_failed",
            details=f"Connector {conn_row['name']!r} failed ({err_str}); alert_sent={alert_sent}",
            db_path=settings.sqlite_path,
        )
        return {"ok": False, "fetched": 0, "error": err_str, "alert_sent": alert_sent}


def sync_due_connectors() -> int:
    """Poll all enabled connectors whose `interval_minutes` has elapsed since `last_sync_at`."""
    settings = get_settings()
    connectors = list_connectors_db(include_secret=False, db_path=settings.sqlite_path)
    now = datetime.now(timezone.utc)
    synced = 0

    for c in connectors:
        if not c["enabled"]:
            continue
        last_str = c["last_sync_at"]
        due = True
        if last_str:
            try:
                last_dt = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
                due = (now - last_dt) >= timedelta(minutes=max(1, int(c["interval_minutes"])))
            except ValueError:
                due = True
        if due:
            sync_connector_by_id(int(c["id"]))
            synced += 1
    return synced
