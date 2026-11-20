"""Phase 3a Spam Quarantine Digest, Scanner-Safe Public Token Release & Sender Allowlist.

Connects to Dovecot via IMAP Master-User (`\x00user*vmail\x00pass`), inspects `Junk`,
sends daily HTML+Plaintext digest emails ONLY when new spam arrived, provides two-step
link-scanner-safe release endpoints (`GET /api/quarantine/token/{token}` +
`POST /api/quarantine/action`), and rescues allowlisted senders from `Junk` to `INBOX`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import email
from email.message import EmailMessage
import email.policy
from email.utils import parseaddr, parsedate_to_datetime
import html
import imaplib
import logging
import re
import secrets
import smtplib
from typing import Any, Callable, Iterator

from app.backend.config import AppSettings, MailSettings, get_settings
from app.backend.db import (
    add_audit_entry,
    add_sender_allowlist,
    create_quarantine_token,
    delete_sender_allowlist_entry,
    get_digested_keys_for_user,
    get_meta,
    get_quarantine_token_row,
    list_sender_allowlist,
    mark_messages_digested,
    mark_quarantine_token_used,
    set_meta,
    utc_now_iso,
)
from app.backend.service import build_base_mailbox_map

logger = logging.getLogger(__name__)

_SPAMD_SCORE_RE = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*/\s*-?\d+(?:\.\d+)?\s*\]")
_SPAM_STATUS_SCORE_RE = re.compile(r"score=(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_PLAIN_FLOAT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)")

ImapFactory = Callable[[str, MailSettings], Any]
SmtpSenderFn = Callable[[bytes, str, str, MailSettings], None]

_custom_imap_factory: ImapFactory | None = None
_custom_smtp_sender: SmtpSenderFn | None = None


def set_imap_master_factory(factory: ImapFactory | None) -> None:
    """Override Dovecot Master-User IMAP client creation (for tests)."""
    global _custom_imap_factory
    _custom_imap_factory = factory


def set_smtp_sender(sender: SmtpSenderFn | None) -> None:
    """Override SMTP delivery function (for tests)."""
    global _custom_smtp_sender
    _custom_smtp_sender = sender


@contextmanager
def open_master_imap(target_user: str, mail_cfg: MailSettings) -> Iterator[Any]:
    """Authenticate to Dovecot IMAP as `target_user*master_user` via SASL PLAIN."""
    if _custom_imap_factory is not None:
        conn = _custom_imap_factory(target_user, mail_cfg)
        try:
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return

    if not mail_cfg.host or not mail_cfg.master_password:
        raise RuntimeError("Dovecot IMAP host or master credentials not configured")

    if int(mail_cfg.imap_port) == 993:
        conn = imaplib.IMAP4_SSL(mail_cfg.host, int(mail_cfg.imap_port), timeout=10.0)
    else:
        conn = imaplib.IMAP4(mail_cfg.host, int(mail_cfg.imap_port), timeout=10.0)

    try:
        auth_bytes = (
            f"\x00{target_user}*{mail_cfg.master_user}\x00{mail_cfg.master_password}".encode("utf-8")
        )
        try:
            conn.authenticate("PLAIN", lambda _challenge: auth_bytes)
        except imaplib.IMAP4.error as auth_exc:
            if "BAD" in str(auth_exc).upper() or "UNKNOWN" in str(auth_exc).upper():
                conn.login(f"{target_user}*{mail_cfg.master_user}", mail_cfg.master_password)
            else:
                raise
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def deliver_smtp_raw(
    raw_bytes: bytes,
    mail_from: str,
    rcpt_to: str,
    mail_cfg: MailSettings,
) -> None:
    """Send an RFC 5322 message via internal SMTP submission."""
    if _custom_smtp_sender is not None:
        _custom_smtp_sender(raw_bytes, mail_from, rcpt_to, mail_cfg)
        return

    if not mail_cfg.host:
        raise RuntimeError("MAIL_HOST is not configured for SMTP delivery")

    with smtplib.SMTP(mail_cfg.host, int(mail_cfg.submission_port), timeout=10.0) as smtp:
        smtp.ehlo_or_helo_if_needed()
        smtp.sendmail(mail_from, [rcpt_to], raw_bytes)


def _extract_spam_score(msg: email.message.Message) -> float | None:
    spamd_hdr = str(msg.get("X-Spamd-Result") or "")
    if spamd_hdr:
        m = _SPAMD_SCORE_RE.search(spamd_hdr)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass

    score_hdr = str(msg.get("X-Spam-Score") or "")
    if score_hdr:
        m = _PLAIN_FLOAT_RE.match(score_hdr)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass

    status_hdr = str(msg.get("X-Spam-Status") or "")
    if status_hdr:
        m = _SPAM_STATUS_SCORE_RE.search(status_hdr)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass
    return None


def _parse_received_date(date_hdr: str) -> str:
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    return utc_now_iso()


def _extract_fetch_bytes(fetch_data: Any) -> bytes | None:
    if not fetch_data:
        return None
    for part in fetch_data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            return bytes(part[1])
    return None


def fetch_user_junk_items(
    user: str,
    recipient_email: str,
    mail_cfg: MailSettings,
) -> list[dict[str, Any]]:
    """Read all messages currently sitting in `user`'s `Junk` IMAP folder."""
    items: list[dict[str, Any]] = []
    with open_master_imap(user, mail_cfg) as imap:
        typ, _ = imap.select(mail_cfg.spam_folder, readonly=True)
        if typ != "OK":
            return []
        s_typ, s_data = imap.uid("SEARCH", None, "ALL")
        if s_typ != "OK" or not s_data or not s_data[0]:
            return []

        raw_uids = s_data[0].decode("ascii", errors="ignore").split()
        for uid in raw_uids:
            f_typ, f_data = imap.uid("FETCH", uid, "(RFC822)")
            if f_typ != "OK":
                continue
            raw_msg = _extract_fetch_bytes(f_data)
            if not raw_msg:
                continue
            parsed = email.message_from_bytes(raw_msg, policy=email.policy.default)
            sender_raw = str(parsed.get("From") or "").strip()
            sender_addr = parseaddr(sender_raw)[1].strip() or sender_raw
            subject = str(parsed.get("Subject") or "(Kein Betreff)").strip()
            msg_id = str(parsed.get("Message-ID") or "").strip() or f"<uid-{user}-{uid}@quarantine>"
            received_at = _parse_received_date(str(parsed.get("Date") or ""))
            spam_score = _extract_spam_score(parsed)

            items.append(
                {
                    "id": f"{user}:{uid}",
                    "user": user,
                    "recipient": recipient_email or user,
                    "sender": sender_addr,
                    "sender_display": sender_raw or sender_addr,
                    "subject": subject,
                    "received_at": received_at,
                    "spam_score": spam_score,
                    "uid": str(uid),
                    "message_id": msg_id,
                }
            )

    items.sort(key=lambda x: x["received_at"], reverse=True)
    return items


def move_junk_to_inbox(
    *,
    user: str,
    uid: str = "",
    message_id: str = "",
    mail_cfg: MailSettings | None = None,
) -> bool:
    """Move a quarantined email from `Junk` to `INBOX` (`UID COPY` + `\\Deleted` + `EXPUNGE`)."""
    cfg = mail_cfg or get_settings().mail
    with open_master_imap(user, cfg) as imap:
        typ, _ = imap.select(cfg.spam_folder, readonly=False)
        if typ != "OK":
            return False

        s_typ, s_data = imap.uid("SEARCH", None, "ALL")
        if s_typ != "OK" or not s_data or not s_data[0]:
            return False
        existing_uids = s_data[0].decode("ascii", errors="ignore").split()
        if not existing_uids:
            return False

        target_uid: str | None = None
        if uid and uid in existing_uids:
            if not message_id:
                target_uid = uid
            else:
                f_typ, f_data = imap.uid("FETCH", uid, "(RFC822)")
                raw = _extract_fetch_bytes(f_data) if f_typ == "OK" else None
                if raw:
                    parsed = email.message_from_bytes(raw, policy=email.policy.default)
                    cur_mid = str(parsed.get("Message-ID") or "").strip()
                    if not cur_mid or cur_mid == message_id or message_id.startswith("<uid-"):
                        target_uid = uid

        if target_uid is None and message_id:
            for cand_uid in existing_uids:
                f_typ, f_data = imap.uid("FETCH", cand_uid, "(RFC822)")
                raw = _extract_fetch_bytes(f_data) if f_typ == "OK" else None
                if not raw:
                    continue
                parsed = email.message_from_bytes(raw, policy=email.policy.default)
                cur_mid = str(parsed.get("Message-ID") or "").strip()
                if cur_mid and cur_mid == message_id:
                    target_uid = cand_uid
                    break

        if target_uid is None:
            return False

        c_typ, _ = imap.uid("COPY", target_uid, "INBOX")
        if c_typ != "OK":
            raise RuntimeError(f"IMAP COPY to INBOX failed for {user} UID {target_uid}")
        imap.uid("STORE", target_uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        return True


def rescue_allowlisted_junk_messages(settings: AppSettings | None = None) -> int:
    """Move any Junk messages sent by allowlisted senders into the user's INBOX."""
    cfg = settings or get_settings()
    if not cfg.mail.host or not cfg.mail.master_password:
        return 0

    allow_entries = list_sender_allowlist(db_path=cfg.sqlite_path)
    if not allow_entries:
        return 0

    by_user: dict[str, set[str]] = {}
    for entry in allow_entries:
        by_user.setdefault(entry["user"], set()).add(entry["sender_email"].strip().lower())

    rescued = 0
    for user, allowed_senders in by_user.items():
        try:
            junk_items = fetch_user_junk_items(user, user, cfg.mail)
            for item in junk_items:
                norm_sender = parseaddr(item["sender"])[1].strip().lower() or item["sender"].strip().lower()
                if norm_sender in allowed_senders:
                    if move_junk_to_inbox(
                        user=user,
                        uid=item["uid"],
                        message_id=item["message_id"],
                        mail_cfg=cfg.mail,
                    ):
                        rescued += 1
                        add_audit_entry(
                            actor="system",
                            source="reconciler",
                            target_user=user,
                            action="quarantine_allowlist_rescued",
                            details=f"Auto-delivered allowlisted sender {norm_sender} to INBOX",
                            db_path=cfg.sqlite_path,
                        )
        except Exception as exc:
            logger.debug("Allowlist rescue skipped for %s: %s", user, exc)
    return rescued


def _build_digest_email(
    *,
    recipient_email: str,
    portal_hostname: str,
    items_with_tokens: list[tuple[dict[str, Any], str]],
    mail_from: str,
) -> bytes:
    base_url = (
        f"https://{portal_hostname}"
        if portal_hostname
        else "https://localhost"
    ).rstrip("/")

    plain_lines = [
        "Täglicher Spam-Quarantäne-Bericht / Daily Spam Quarantine Digest",
        "================================================================",
        "",
        f"Neue Nachrichten im Spam-Ordner ({len(items_with_tokens)}):",
        "",
    ]
    html_rows: list[str] = []

    for item, token in items_with_tokens:
        rel_url = f"{base_url}/?quarantine_token={token}&op=release"
        allow_url = f"{base_url}/?quarantine_token={token}&op=release_and_allow"
        score_str = f"{item['spam_score']:.1f}" if item["spam_score"] is not None else "-"

        plain_lines.extend(
            [
                f"- Von / From: {item['sender']}",
                f"  Betreff / Subject: {item['subject']}",
                f"  Empfangen / Received: {item['received_at']} (Score: {score_str})",
                f"  Zustellen / Release: {rel_url}",
                f"  Zustellen & Freigeben / Release & Allow: {allow_url}",
                "",
            ]
        )

        html_rows.append(
            "<tr>"
            f'<td style="padding:8px;border-bottom:1px solid #e0e0e0;">{html.escape(item["sender"])}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #e0e0e0;"><strong>{html.escape(item["subject"])}</strong></td>'
            f'<td style="padding:8px;border-bottom:1px solid #e0e0e0;white-space:nowrap;">{html.escape(item["received_at"])}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #e0e0e0;white-space:nowrap;">'
            f'<a href="{html.escape(rel_url, quote=True)}" style="color:#0f62fe;text-decoration:none;margin-right:10px;">Zustellen</a>'
            f'<a href="{html.escape(allow_url, quote=True)}" style="color:#198038;text-decoration:none;">Zustellen &amp; Freigeben</a>'
            "</td>"
            "</tr>"
        )

    html_body = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;font-size:14px;color:#161616;">'
        '<h2 style="font-size:18px;margin-bottom:8px;">Spam-Quarantäne-Bericht / Quarantine Digest</h2>'
        f"<p>Es befinden sich <strong>{len(items_with_tokens)}</strong> neue Nachricht(en) in Ihrem Spam-Ordner:</p>"
        '<table style="border-collapse:collapse;width:100%;max-width:860px;font-size:13px;">'
        "<thead><tr style=\"background:#f4f4f4;text-align:left;\">"
        '<th style="padding:8px;">Absender</th>'
        '<th style="padding:8px;">Betreff</th>'
        '<th style="padding:8px;">Datum</th>'
        '<th style="padding:8px;">Aktion</th>'
        "</tr></thead>"
        f"<tbody>{''.join(html_rows)}</tbody>"
        "</table>"
        "</body></html>"
    )

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = recipient_email
    msg["Subject"] = f"Spam-Quarantäne-Bericht ({len(items_with_tokens)} neue E-Mails)"
    msg.set_content("\n".join(plain_lines), cte="8bit")
    msg.add_alternative(html_body, subtype="html", cte="8bit")
    smtp_policy = email.policy.SMTP.clone(max_line_length=998)
    return msg.as_bytes(policy=smtp_policy)


def run_quarantine_digest(*, user_filter: str = "") -> dict[str, int]:
    """Scan Junk folders and send digest emails ONLY to users with >=1 new spam message."""
    settings = get_settings()
    rescue_allowlisted_junk_messages(settings)

    base_map = build_base_mailbox_map(settings)
    target_user = (user_filter or "").strip()

    candidates: list[dict[str, Any]] = []
    for u_key, meta in sorted(base_map.items()):
        if not meta.get("enabled", True):
            continue
        if target_user and u_key.lower() != target_user.lower():
            continue
        candidates.append(meta)

    if target_user and not candidates:
        fallback_mail = (
            f"{target_user}@{settings.ldap.domain}" if settings.ldap.domain else target_user
        )
        candidates.append({"user": target_user, "mail": fallback_mail, "enabled": True})

    users_checked = 0
    digests_sent = 0
    items_included = 0
    now = datetime.now(timezone.utc)
    expires_iso = (now + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    domain = settings.ldap.domain or settings.portal_hostname or "localhost"
    sender_from = f"quarantine-noreply@{domain}"

    for mb in candidates:
        u_name = str(mb["user"])
        rcpt_email = str(mb.get("mail") or f"{u_name}@{domain}")
        users_checked += 1

        try:
            junk_items = fetch_user_junk_items(u_name, rcpt_email, settings.mail)
        except Exception as exc:
            logger.warning("Failed inspecting Junk folder for %s: %s", u_name, exc)
            continue

        if not junk_items:
            continue

        already_digested = get_digested_keys_for_user(u_name, settings.sqlite_path)
        new_items = [it for it in junk_items if it["message_id"] not in already_digested]
        if not new_items:
            continue

        items_with_tokens: list[tuple[dict[str, Any], str]] = []
        for it in new_items:
            token = secrets.token_urlsafe(24)
            create_quarantine_token(
                token=token,
                mail_id=it["message_id"],
                uid=it["uid"],
                recipient_user=u_name,
                recipient=rcpt_email,
                sender=it["sender"],
                subject=it["subject"],
                received_at=it["received_at"],
                spam_score=it["spam_score"],
                expires_at=expires_iso,
                db_path=settings.sqlite_path,
            )
            items_with_tokens.append((it, token))

        raw_digest = _build_digest_email(
            recipient_email=rcpt_email,
            portal_hostname=settings.portal_hostname or "localhost",
            items_with_tokens=items_with_tokens,
            mail_from=sender_from,
        )
        deliver_smtp_raw(raw_digest, sender_from, rcpt_email, settings.mail)
        mark_messages_digested(
            u_name,
            [it["message_id"] for it in new_items],
            settings.sqlite_path,
        )
        digests_sent += 1
        items_included += len(new_items)

    now_iso = utc_now_iso()
    set_meta("digest_last_run_at", now_iso, settings.sqlite_path)
    set_meta("digest_last_digests_sent", str(digests_sent), settings.sqlite_path)
    return {
        "users_checked": users_checked,
        "digests_sent": digests_sent,
        "items_included": items_included,
    }


def list_admin_quarantine(
    *,
    user_filter: str = "",
    search: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Return quarantined Junk items across mailboxes, digest status, and sender allowlist."""
    settings = get_settings()
    base_map = build_base_mailbox_map(settings)
    u_filt = (user_filter or "").strip().lower()
    q_search = (search or "").strip().lower()

    all_items: list[dict[str, Any]] = []
    if settings.mail.host and settings.mail.master_password:
        for u_name, meta in sorted(base_map.items()):
            if u_filt and u_name.lower() != u_filt:
                continue
            rcpt = str(meta.get("mail") or u_name)
            try:
                user_items = fetch_user_junk_items(u_name, rcpt, settings.mail)
            except Exception as exc:
                logger.debug("Quarantine listing skipped for %s: %s", u_name, exc)
                continue

            for item in user_items:
                if q_search:
                    haystack = (
                        f"{item['recipient']} {item['sender']} {item['subject']}".lower()
                    )
                    if q_search not in haystack:
                        continue
                all_items.append(
                    {
                        "id": item["id"],
                        "user": item["user"],
                        "recipient": item["recipient"],
                        "sender": item["sender"],
                        "subject": item["subject"],
                        "received_at": item["received_at"],
                        "spam_score": item["spam_score"],
                        "uid": item["uid"],
                        "message_id": item["message_id"],
                    }
                )

    all_items.sort(key=lambda x: x["received_at"], reverse=True)
    clamped = max(1, min(int(limit), 1000))

    last_sent_raw = get_meta("digest_last_digests_sent", "0", settings.sqlite_path)
    try:
        last_sent = int(last_sent_raw)
    except ValueError:
        last_sent = 0

    return {
        "items": all_items[:clamped],
        "digest_status": {
            "enabled": settings.runtime.digest_enabled,
            "digest_time": settings.runtime.digest_time,
            "timezone": settings.runtime.timezone,
            "last_run_at": get_meta("digest_last_run_at", "", settings.sqlite_path),
            "last_digests_sent": last_sent,
        },
        "allowlist": list_sender_allowlist(
            user=user_filter.strip() if user_filter.strip() else None,
            db_path=settings.sqlite_path,
        ),
    }


def admin_release_quarantine_item(
    *,
    user: str,
    uid: str,
    message_id: str,
    allow_sender: bool = False,
) -> dict[str, Any]:
    """Admin Cockpit release of a quarantined email from Junk to INBOX."""
    settings = get_settings()
    sender_to_allow = ""
    if allow_sender:
        try:
            items = fetch_user_junk_items(user, user, settings.mail)
            for it in items:
                if (uid and it["uid"] == uid) or (message_id and it["message_id"] == message_id):
                    sender_to_allow = parseaddr(it["sender"])[1].strip().lower() or it["sender"].strip().lower()
                    break
        except Exception:
            pass

    delivered = move_junk_to_inbox(user=user, uid=uid, message_id=message_id, mail_cfg=settings.mail)
    allowlisted = False
    if allow_sender and sender_to_allow and "@" in sender_to_allow:
        add_sender_allowlist(user, sender_to_allow, settings.sqlite_path)
        allowlisted = True

    add_audit_entry(
        actor="cockpit-admin",
        source="cockpit",
        target_user=user,
        action="quarantine_released",
        details=f"Released UID={uid} (delivered={delivered}, allowlisted={allowlisted})",
        db_path=settings.sqlite_path,
    )
    return {"ok": True, "delivered": delivered, "allowlisted": allowlisted}


def inspect_quarantine_token(token: str) -> dict[str, Any]:
    """Read-only inspection of a public quarantine token (NEVER marks token used!)."""
    settings = get_settings()
    row = get_quarantine_token_row((token or "").strip(), settings.sqlite_path)
    if row is None:
        return {
            "valid": False,
            "already_used": False,
            "expired": False,
            "recipient_user": "",
            "recipient_email": "",
            "sender": "",
            "subject": "",
            "received_at": "",
            "spam_score": None,
            "recommended_op": "release",
        }

    already_used = bool(row.get("used_at"))
    expired = False
    exp_str = str(row.get("expires_at") or "")
    if exp_str:
        try:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            expired = datetime.now(timezone.utc) > exp_dt
        except ValueError:
            expired = False

    rec_user = str(row.get("recipient_user") or "")
    if not rec_user and "@" in str(row.get("recipient") or ""):
        rec_user = str(row["recipient"]).split("@", 1)[0]

    return {
        "valid": bool(not already_used and not expired),
        "already_used": already_used,
        "expired": expired,
        "recipient_user": rec_user,
        "recipient_email": str(row.get("recipient") or ""),
        "sender": str(row.get("sender") or ""),
        "subject": str(row.get("subject") or ""),
        "received_at": str(row.get("received_at") or ""),
        "spam_score": float(row["spam_score"]) if row.get("spam_score") is not None else None,
        "recommended_op": "release",
    }


def execute_quarantine_token_action(token: str, op: str) -> tuple[int, dict[str, Any]]:
    """Consume quarantine token on explicit POST action, move mail to INBOX, and optionally allowlist."""
    norm_op = (op or "release").strip().lower()
    if norm_op not in ("release", "release_and_allow"):
        return 422, {"ok": False, "detail": f"Unsupported operation: {op!r}"}

    settings = get_settings()
    clean_tok = (token or "").strip()
    row = get_quarantine_token_row(clean_tok, settings.sqlite_path)
    if row is None:
        return 404, {
            "ok": False,
            "already_used": False,
            "expired": False,
            "detail": "Invalid quarantine token",
            "message": "Invalid quarantine token",
        }

    if row.get("used_at"):
        return 409, {
            "ok": False,
            "already_used": True,
            "expired": False,
            "detail": "Quarantine token already_used",
            "message": "This message has already been released.",
        }

    exp_str = str(row.get("expires_at") or "")
    if exp_str:
        try:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                return 410, {
                    "ok": False,
                    "already_used": False,
                    "expired": True,
                    "detail": "Quarantine token expired",
                    "message": "This release link has expired.",
                }
        except ValueError:
            pass

    rec_user = str(row.get("recipient_user") or "")
    if not rec_user and "@" in str(row.get("recipient") or ""):
        rec_user = str(row["recipient"]).split("@", 1)[0]

    delivered = move_junk_to_inbox(
        user=rec_user,
        uid=str(row.get("uid") or ""),
        message_id=str(row.get("mail_id") or ""),
        mail_cfg=settings.mail,
    )

    allowlisted = False
    sender_raw = str(row.get("sender") or "")
    sender_email = parseaddr(sender_raw)[1].strip().lower() or sender_raw.strip().lower()
    if norm_op == "release_and_allow" and rec_user and "@" in sender_email:
        add_sender_allowlist(rec_user, sender_email, settings.sqlite_path)
        allowlisted = True

    marked = mark_quarantine_token_used(clean_tok, action=norm_op, db_path=settings.sqlite_path)
    if not marked:
        return 409, {
            "ok": False,
            "already_used": True,
            "expired": False,
            "detail": "Quarantine token already_used",
            "message": "This message has already been released.",
        }

    add_audit_entry(
        actor=rec_user or "recipient",
        source="quarantine_portal",
        target_user=rec_user,
        action=f"quarantine_{norm_op}",
        details=f"Released {row.get('mail_id')!r} from {sender_email} (delivered={delivered}, allowlisted={allowlisted})",
        db_path=settings.sqlite_path,
    )

    return 200, {
        "ok": True,
        "op": norm_op,
        "delivered": True if delivered else False,
        "allowlisted": allowlisted,
        "already_used": False,
        "message": (
            "Message delivered to INBOX and sender added to allowlist."
            if allowlisted
            else "Message delivered to INBOX."
        ),
    }


def remove_allowlist_entry(entry_id: int) -> bool:
    settings = get_settings()
    return delete_sender_allowlist_entry(entry_id, settings.sqlite_path)
