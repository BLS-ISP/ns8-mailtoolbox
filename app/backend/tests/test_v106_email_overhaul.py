"""v1.0.6 tests: Carbon HTML+Plaintext Email Overhaul, Quoted-Printable, Direct INBOX APPEND & Loop Prevention."""

from __future__ import annotations

import email
from email.message import EmailMessage
import email.policy
from pathlib import Path
import smtplib
from typing import Any

import pytest

from app.backend.config import MailSettings, get_settings, update_runtime_settings
from app.backend.connector_service import (
    check_imapsync_bridge_alerts,
    save_connector,
    set_external_imap_factory,
    sync_connector_by_id,
)
from app.backend.db import get_digested_keys_for_user
from app.backend.email_templates import (
    build_connector_failure_alert_email,
    build_imapsync_failure_alert_email,
    build_quarantine_digest_email,
    format_local_timestamp,
    resolve_sender_domain,
)
from app.backend.quarantine_service import (
    fetch_user_junk_items,
    run_quarantine_digest,
    set_imap_master_factory,
    set_smtp_sender,
)


class AppendCapableImapStore:
    """In-memory IMAP store tracking FETCH specs and APPEND calls to INBOX."""

    def __init__(self) -> None:
        self.folders: dict[str, dict[str, dict[str, bytes]]] = {}
        self.appended: list[dict[str, Any]] = []
        self.fetch_specs: list[str] = []
        self.fail_append: bool = False
        self._next_uid = 1000

    def add_message(self, user: str, folder: str, raw_bytes: bytes, uid: str | None = None) -> str:
        if uid is None:
            uid = str(self._next_uid)
            self._next_uid += 1
        ufolders = self.folders.setdefault(user, {"INBOX": {}, "Junk": {}})
        ufolders.setdefault(folder, {})[str(uid)] = raw_bytes
        return str(uid)


class AppendCapableImapConn:
    def __init__(self, store: AppendCapableImapStore, user: str) -> None:
        self.store = store
        self.user = user
        self.current_folder = "INBOX"

    def select(self, folder: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        _ = readonly
        clean = folder.strip('"')
        ufolders = self.store.folders.setdefault(self.user, {"INBOX": {}, "Junk": {}})
        if clean not in ufolders:
            return "NO", [b"Missing folder"]
        self.current_folder = clean
        return "OK", [str(len(ufolders[clean])).encode("ascii")]

    def uid(self, cmd: str, *args: Any) -> tuple[str, list[Any]]:
        upper = cmd.upper()
        fmap = self.store.folders.setdefault(self.user, {"INBOX": {}, "Junk": {}}).setdefault(
            self.current_folder, {}
        )
        if upper == "SEARCH":
            uids = sorted(fmap.keys(), key=lambda x: int(x))
            return "OK", [" ".join(uids).encode("ascii")]
        if upper == "FETCH":
            uid_spec = str(args[0])
            spec = str(args[1]) if len(args) > 1 else "(RFC822)"
            self.store.fetch_specs.append(spec)
            req_uids = [u.strip() for u in uid_spec.split(",") if u.strip()]
            out_tuples: list[Any] = []
            for seq_idx, target_uid in enumerate(req_uids, start=1):
                if target_uid not in fmap:
                    continue
                raw = fmap[target_uid]
                if "HEADER" in spec.upper():
                    sep = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
                    hdr_bytes = raw.split(sep, 1)[0] + b"\r\n\r\n"
                    out_tuples.append(
                        (
                            f"{seq_idx} (UID {target_uid} BODY[HEADER] {{{len(hdr_bytes)}}})".encode("ascii"),
                            hdr_bytes,
                        )
                    )
                else:
                    out_tuples.append(
                        (f"{seq_idx} (UID {target_uid} RFC822 {{{len(raw)}}})".encode("ascii"), raw)
                    )
            return ("OK", out_tuples) if out_tuples else ("NO", [])
        return "BAD", [b"Unsupported"]

    def append(
        self,
        mailbox: str,
        flags: str,
        date_time: str,
        message: bytes,
    ) -> tuple[str, list[bytes]]:
        if self.store.fail_append:
            raise RuntimeError("Simulated IMAP APPEND failure")
        clean_mb = mailbox.strip('"')
        self.store.appended.append(
            {
                "user": self.user,
                "mailbox": clean_mb,
                "flags": flags,
                "date_time": date_time,
                "raw": bytes(message),
            }
        )
        self.store.add_message(self.user, clean_mb, bytes(message))
        return "OK", [b"[APPENDUID 1 9999] Append completed"]

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b"Logged out"]


def test_resolve_sender_domain_strips_ad_ldap_realms_and_formats_timestamps(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_dir = toolbox_env["config_dir"]
    (cfg_dir / "discovery_ldap.env").write_text(
        "LDAP_HOST=127.0.0.1\n"
        "LDAP_PORT=389\n"
        "LDAP_BASE=dc=ad,dc=bls-isp,dc=net\n"
        "LDAP_USER=cn=svc,dc=ad,dc=bls-isp,dc=net\n"
        "LDAP_PASS=secret\n"
        "LDAP_SCHEMA=ad\n"
        "LDAP_DOMAIN=ad.bls-isp.net\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PORTAL_HOSTNAME", "")
    settings = get_settings()

    # 1. Recipient email domain takes top priority
    assert resolve_sender_domain("genera@bls-isp.net", settings) == "bls-isp.net"
    assert resolve_sender_domain("User <staff@custom-domain.de>", settings) == "custom-domain.de"

    # 2. When recipient has no usable public domain, `ad.bls-isp.net` strips `ad.` -> `bls-isp.net`
    assert resolve_sender_domain("", settings) == "bls-isp.net"
    assert resolve_sender_domain("admin@ad.bls-isp.net", settings) == "bls-isp.net"

    # 3. Local timestamp formatting with Europe/Berlin CET/CEST
    formatted = format_local_timestamp("2026-11-24T06:41:02Z", "Europe/Berlin")
    assert formatted.startswith("24 Nov 2026, 07:41")
    epoch_formatted = format_local_timestamp(1795502462, "UTC")
    assert "24 Nov 2026, 06:41 UTC" == epoch_formatted


def test_56_item_digest_quoted_printable_50_item_cap_and_cockpit_only_mode(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the exact 56-item SOGo production scenario (`genera@bls-isp.net`, `ad.bls-isp.net`)."""
    import json

    cfg_dir = toolbox_env["config_dir"]
    (cfg_dir / "discovery_ldap.env").write_text(
        "LDAP_HOST=127.0.0.1\n"
        "LDAP_PORT=389\n"
        "LDAP_BASE=dc=ad,dc=bls-isp,dc=net\n"
        "LDAP_USER=cn=svc,dc=ad,dc=bls-isp,dc=net\n"
        "LDAP_PASS=secret\n"
        "LDAP_SCHEMA=ad\n"
        "LDAP_DOMAIN=ad.bls-isp.net\n",
        encoding="utf-8",
    )
    (cfg_dir / "mailboxes_cache.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-11-24T07:00:00Z",
                "mailboxes": [
                    {
                        "user": "genera",
                        "enabled": True,
                        "aliases": ["genera@bls-isp.net"],
                        "postfix_forwards": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PORTAL_HOSTNAME", "toolbox.bls-isp.net")
    monkeypatch.setenv("PORTAL_ENABLED", "True")
    update_runtime_settings({"timezone": "Europe/Berlin"})

    store = AppendCapableImapStore()
    set_imap_master_factory(lambda user, _cfg: AppendCapableImapConn(store, user))

    # Seed 56 spam messages for `genera` with long unbroken URLs/subjects and varying scores
    for i in range(1, 57):
        msg = EmailMessage()
        msg["From"] = f"Promo Sender {i} <campaign-{i}@very-long-marketing-domain-example.com>"
        msg["To"] = "genera@bls-isp.net"
        long_tail = "X" * 210
        msg["Subject"] = f"Urgent Offer #{i:02d} {long_tail}"
        msg["Message-ID"] = f"<spam-56-{i:03d}@marketing.example>"
        msg["Date"] = f"Tue, 24 Nov 2026 06:{i % 60:02d}:00 +0000"
        if i % 3 == 0:
            msg["X-Spam-Score"] = "12.4"
        elif i % 3 == 1:
            msg["X-Spam-Score"] = "6.5"
        msg.set_content("Spam body content that should never be downloaded via RFC822.")
        store.add_message("genera", "Junk", msg.as_bytes(policy=email.policy.SMTP), uid=str(2000 + i))

    res = run_quarantine_digest(user_filter="genera")
    assert res == {"users_checked": 1, "digests_sent": 1, "items_included": 56}

    # Delivered directly into INBOX via Dovecot Master-User IMAP APPEND!
    assert len(store.appended) == 1
    assert store.appended[0]["user"] == "genera"
    assert store.appended[0]["mailbox"] == "INBOX"
    raw_digest = store.appended[0]["raw"]

    # Every raw wire line MUST be well under RFC 5322's 998-byte limit
    max_wire_line = max(len(line) for line in raw_digest.splitlines())
    assert max_wire_line <= 260, f"Wire line exceeded safe length: {max_wire_line}"

    parsed = email.message_from_bytes(raw_digest, policy=email.policy.default)
    assert parsed["From"] == "Mail Toolbox Quarantine <noreply@bls-isp.net>"
    assert "ad.bls-isp.net" not in str(parsed["From"])
    assert "ad.bls-isp.net" not in str(parsed["Message-ID"])
    assert "@bls-isp.net>" in str(parsed["Message-ID"])
    assert parsed["Date"] is not None
    assert parsed["Auto-Submitted"] == "auto-generated"
    assert parsed["Precedence"] == "bulk"
    assert parsed["X-Auto-Response-Suppress"] == "All"
    assert parsed["X-NS8-Mail-Toolbox"] == "quarantine-digest"

    parts = list(parsed.iter_parts())
    assert len(parts) == 2
    for part in parts:
        assert part["Content-Transfer-Encoding"] == "quoted-printable"

    plain_body = parts[0].get_content()
    html_body = parts[1].get_content()

    # Source HTML lines must all be clean & short (<200 chars)
    assert max(len(ln) for ln in html_body.splitlines()) < 200
    assert html_body.count("Deliver to Inbox") == 50
    assert html_body.count("Deliver &amp; Allow Sender") == 50
    assert "+ 6 older quarantined messages in your Spam folder" in html_body
    assert "+ 6 older quarantined messages in your Spam folder." in plain_body
    assert "Score 12.4" in html_body
    assert "Score 6.5" in html_body
    assert "Score -" in html_body
    assert "#da1e28" in html_body
    assert "#8e6a00" in html_body

    # All 56 items were marked digested so re-running sends 0 duplicate digests
    settings = get_settings()
    assert len(get_digested_keys_for_user("genera", settings.sqlite_path)) == 56
    assert run_quarantine_digest(user_filter="genera")["digests_sent"] == 0

    # Verify Cockpit-only mode (`portal_enabled=False` or `localhost`) omits dead links
    raw_no_portal, env_from = build_quarantine_digest_email(
        recipient_email="genera@bls-isp.net",
        sender_domain="bls-isp.net",
        portal_enabled=False,
        portal_hostname="localhost",
        tz_name="Europe/Berlin",
        displayed_items_with_tokens=[
            (
                {
                    "subject": "Quarantined Item",
                    "sender_display": "Sender <s@example.com>",
                    "received_at": "2026-11-24T06:41:02Z",
                    "spam_score": 9.1,
                },
                "tok-unused",
            )
        ],
        total_new_count=1,
    )
    assert env_from == "noreply@bls-isp.net"
    parsed_np = email.message_from_bytes(raw_no_portal, policy=email.policy.default)
    np_html = list(parsed_np.iter_parts())[1].get_content()
    assert "localhost" not in np_html
    assert "quarantine_token=" not in np_html
    assert "Review your <strong>Spam</strong> folder in Webmail or IMAP" in np_html


def test_direct_imap_append_for_local_admin_and_smtp_fallback_on_error(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AppendCapableImapStore()
    set_imap_master_factory(lambda user, _cfg: AppendCapableImapConn(store, user))
    set_smtp_sender(None)  # Exercise real `deliver_to_user_inbox_or_smtp` routing!

    smtp_calls: list[dict[str, Any]] = []

    class DummySMTP:
        def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self) -> DummySMTP:
            return self

        def __exit__(self, *_exc: Any) -> None:
            pass

        def ehlo_or_helo_if_needed(self) -> None:
            pass

        def sendmail(self, mail_from: str, rcpts: list[str], raw_bytes: bytes) -> None:
            smtp_calls.append({"from": mail_from, "rcpts": rcpts, "raw": raw_bytes})

    monkeypatch.setattr(smtplib, "SMTP", DummySMTP)

    class FailingExtImap:
        def login(self, _u: str, _p: str) -> None:
            raise RuntimeError("ISP authentication rejected")

        def logout(self) -> None:
            pass

    set_external_imap_factory(lambda _h, _p, _s: FailingExtImap())

    # 1. Local admin alias `empfang@firma.de` resolves to mailbox user `anna` -> direct IMAP APPEND to INBOX!
    update_runtime_settings({"admin_alert_email": "empfang@firma.de"})
    saved = save_connector(
        {
            "name": "ISP Mailbox",
            "target_user": "anna",
            "target_email": "anna@firma.de",
            "protocol": "imaps",
            "host": "imap.isp.example",
            "port": 993,
            "remote_user": "anna@isp.example",
            "remote_password": "wrong",
        }
    )
    res1 = sync_connector_by_id(int(saved["id"]))
    assert res1["ok"] is False
    assert res1["alert_sent"] is True
    assert len(store.appended) == 1
    assert store.appended[0]["user"] == "anna"
    assert store.appended[0]["mailbox"] == "INBOX"
    assert len(smtp_calls) == 0

    # 2. If IMAP APPEND raises an error, gracefully falls back to SMTP submission!
    store.fail_append = True
    saved2 = save_connector(
        {
            "name": "Second ISP Mailbox",
            "target_user": "anna",
            "target_email": "anna@firma.de",
            "protocol": "imaps",
            "host": "imap2.isp.example",
            "port": 993,
            "remote_user": "anna2@isp.example",
            "remote_password": "wrong",
        }
    )
    res2 = sync_connector_by_id(int(saved2["id"]))
    assert res2["alert_sent"] is True
    assert len(smtp_calls) == 1
    assert smtp_calls[0]["rcpts"] == ["empfang@firma.de"]
    assert smtp_calls[0]["from"] == "noreply@firma.de"


def test_body_peek_header_and_self_digest_loop_protection_in_junk(
    toolbox_env: dict[str, Path],
) -> None:
    store = AppendCapableImapStore()
    set_imap_master_factory(lambda user, _cfg: AppendCapableImapConn(store, user))

    # 1. Real spam email
    real_spam = EmailMessage()
    real_spam["From"] = "Phisher <bad@attacker.example>"
    real_spam["To"] = "max.muster@firma.de"
    real_spam["Subject"] = "Verify your account"
    real_spam["Message-ID"] = "<real-spam-1@attacker.example>"
    real_spam["X-Spam-Score"] = "9.8"
    real_spam.set_content("Body")
    store.add_message("max.muster", "Junk", real_spam.as_bytes(policy=email.policy.SMTP), uid="10")

    # 2. New v1.0.6 digest accidentally moved to Junk (`X-NS8-Mail-Toolbox` header)
    v106_digest, _ = build_quarantine_digest_email(
        recipient_email="max.muster@firma.de",
        sender_domain="firma.de",
        portal_enabled=True,
        portal_hostname="vertretung.firma.de",
        tz_name="Europe/Berlin",
        displayed_items_with_tokens=[],
        total_new_count=1,
    )
    store.add_message("max.muster", "Junk", v106_digest, uid="11")

    # 3. Legacy v1.0.5 digest sitting in Junk (`quarantine-noreply@ad.bls-isp.net`)
    legacy_digest = EmailMessage()
    legacy_digest["From"] = "quarantine-noreply@ad.bls-isp.net"
    legacy_digest["To"] = "max.muster@firma.de"
    legacy_digest["Subject"] = "Spam Quarantine Digest (56 new message(s))"
    legacy_digest["Message-ID"] = "<legacy-digest@ad.bls-isp.net>"
    legacy_digest.set_content("Old digest")
    store.add_message("max.muster", "Junk", legacy_digest.as_bytes(policy=email.policy.SMTP), uid="12")

    # 4. Admin Connector Alert sitting in Junk
    legacy_alert = EmailMessage()
    legacy_alert["From"] = "connector-alert@firma.de"
    legacy_alert["To"] = "max.muster@firma.de"
    legacy_alert["Subject"] = "[NS8 Mail Toolbox] External Mail Connector Alert: Test"
    legacy_alert["Message-ID"] = "<legacy-alert@firma.de>"
    legacy_alert.set_content("Alert")
    store.add_message("max.muster", "Junk", legacy_alert.as_bytes(policy=email.policy.SMTP), uid="13")

    settings = get_settings()
    items = fetch_user_junk_items("max.muster", "max.muster@firma.de", settings.mail)

    # Only the 1 genuine spam message is returned; all 3 Toolbox system emails are skipped!
    assert len(items) == 1
    assert items[0]["message_id"] == "<real-spam-1@attacker.example>"

    # Lightweight header-only batch scan (`(UID BODY.PEEK[HEADER])`), zero `(RFC822)` full-body downloads!
    assert store.fetch_specs
    assert all("BODY.PEEK[HEADER]" in spec and "RFC822" not in spec for spec in store.fetch_specs)


def test_multipart_html_and_plaintext_connector_and_imapsync_alerts(
    toolbox_env: dict[str, Path],
) -> None:
    raw_conn, env_conn = build_connector_failure_alert_email(
        conn_row={
            "id": 7,
            "name": "Strato Sales",
            "remote_user": "sales@strato.example",
            "host": "imap.strato.de",
            "port": 993,
            "protocol": "imaps",
            "target_email": "max.muster@firma.de",
        },
        error_msg="AUTHENTICATIONFAILED: Invalid credentials",
        failure_count=3,
        recipient_email="admin@firma.de",
        sender_domain="firma.de",
        tz_name="Europe/Berlin",
    )
    assert env_conn == "noreply@firma.de"
    msg_conn = email.message_from_bytes(raw_conn, policy=email.policy.default)
    assert msg_conn["From"] == "NS8 Mail Toolbox <noreply@firma.de>"
    assert msg_conn["Subject"] == "[NS8 Mail Toolbox] External Mail Connector Alert: Strato Sales"
    assert msg_conn["X-NS8-Mail-Toolbox"] == "connector-alert"
    assert msg_conn["Auto-Submitted"] == "auto-generated"
    conn_parts = list(msg_conn.iter_parts())
    assert len(conn_parts) == 2
    assert conn_parts[0]["Content-Transfer-Encoding"] == "quoted-printable"
    assert conn_parts[1]["Content-Transfer-Encoding"] == "quoted-printable"
    assert "#da1e28" in conn_parts[1].get_content()
    assert "AUTHENTICATIONFAILED: Invalid credentials" in conn_parts[1].get_content()

    raw_sync, env_sync = build_imapsync_failure_alert_email(
        module_id="imapsync1",
        ui_name="Legacy Sync",
        task={
            "task_id": "t-123",
            "name": "info@old.example -> anna",
            "localuser": "anna",
            "remoteuser": "info@old.example",
            "remotehost": "imap.old.example",
            "remoteport": 993,
            "security": "ssl",
        },
        exit_code=11,
        last_ts=1763966462,
        recipient_email="admin@firma.de",
        sender_domain="firma.de",
        tz_name="Europe/Berlin",
    )
    assert env_sync == "noreply@firma.de"
    msg_sync = email.message_from_bytes(raw_sync, policy=email.policy.default)
    assert msg_sync["From"] == "NS8 Mail Toolbox <noreply@firma.de>"
    assert msg_sync["Subject"] == "[NS8 Mail Toolbox] NS8 IMAP-Sync Task Failure Alert: info@old.example -> anna"
    assert msg_sync["X-NS8-Mail-Toolbox"] == "imapsync-alert"
    sync_parts = list(msg_sync.iter_parts())
    assert len(sync_parts) == 2
    assert sync_parts[1]["Content-Transfer-Encoding"] == "quoted-printable"
    assert "#da1e28" in sync_parts[1].get_content()
    assert "imapsync exited with status code 11" in sync_parts[1].get_content()
