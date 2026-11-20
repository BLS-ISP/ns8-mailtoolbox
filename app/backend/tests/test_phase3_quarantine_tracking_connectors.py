"""Phase 3 tests: Spam Quarantine Digest + Two-Step Release, Rspamd Mail-Tracking, and Connectors."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
import email.policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from typing import Any

from fastapi.testclient import TestClient

from app.backend.config import MailSettings, update_runtime_settings
from app.backend.connector_service import set_external_imap_factory, set_external_pop3_factory
from app.backend.main import app
from app.backend.quarantine_service import set_imap_master_factory, set_smtp_sender
from app.backend.reconciler import check_and_run_scheduled_digest


class InMemoryImapStore:
    """In-memory multi-mailbox IMAP store (`Junk` + `INBOX`) for Quarantine & Connector tests."""

    def __init__(self) -> None:
        self.folders: dict[str, dict[str, dict[str, bytes]]] = {}
        self._next_uid = 100
        self.fail_login_password: str | None = None

    def add_message(self, user: str, folder: str, raw_bytes: bytes, uid: str | None = None) -> str:
        if uid is None:
            uid = str(self._next_uid)
            self._next_uid += 1
        user_folders = self.folders.setdefault(user, {"INBOX": {}, "Junk": {}})
        user_folders.setdefault(folder, {})[str(uid)] = raw_bytes
        return str(uid)


class FakeImapConnection:
    def __init__(self, store: InMemoryImapStore, user: str, expected_pw: str | None = None) -> None:
        self.store = store
        self.user = user
        self.expected_pw = expected_pw
        self.current_folder = "INBOX"
        self.deleted_uids: set[str] = set()

    def login(self, remote_user: str, remote_pw: str) -> tuple[str, list[bytes]]:
        if self.expected_pw is not None and remote_pw != self.expected_pw:
            raise RuntimeError("AUTHENTICATIONFAILED: Invalid external password")
        self.user = remote_user
        return "OK", [b"Logged in"]

    def select(self, folder: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        _ = readonly
        clean = folder.strip('"')
        user_folders = self.store.folders.setdefault(self.user, {"INBOX": {}, "Junk": {}})
        if clean not in user_folders:
            return "NO", [b"Folder not found"]
        self.current_folder = clean
        self.deleted_uids.clear()
        return "OK", [str(len(user_folders[clean])).encode("ascii")]

    def uid(self, cmd: str, *args: Any) -> tuple[str, list[Any]]:
        upper = cmd.upper()
        folder_map = self.store.folders.setdefault(self.user, {"INBOX": {}, "Junk": {}}).setdefault(
            self.current_folder, {}
        )
        if upper == "SEARCH":
            uids = sorted(folder_map.keys(), key=lambda x: int(x))
            return "OK", [" ".join(uids).encode("ascii")]
        if upper == "FETCH":
            target_uid = str(args[0])
            if target_uid not in folder_map:
                return "NO", []
            raw = folder_map[target_uid]
            return "OK", [(f"{target_uid} (RFC822 {{{len(raw)}}})".encode("ascii"), raw)]
        if upper == "COPY":
            target_uid = str(args[0])
            dest_folder = str(args[1]).strip('"')
            if target_uid not in folder_map:
                return "NO", [b"UID missing"]
            raw = folder_map[target_uid]
            self.store.add_message(self.user, dest_folder, raw)
            return "OK", [b"Copy completed"]
        if upper == "STORE":
            target_uid = str(args[0])
            if r"\Deleted" in str(args[2]):
                self.deleted_uids.add(target_uid)
            return "OK", [b"Flags updated"]
        return "BAD", [b"Unsupported UID command"]

    def expunge(self) -> tuple[str, list[bytes]]:
        folder_map = self.store.folders.setdefault(self.user, {"INBOX": {}, "Junk": {}}).setdefault(
            self.current_folder, {}
        )
        for d_uid in list(self.deleted_uids):
            folder_map.pop(d_uid, None)
        self.deleted_uids.clear()
        return "OK", [b"Expunged"]

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b"Logged out"]


class FakePop3Connection:
    def __init__(self, messages: dict[str, bytes], expected_pw: str) -> None:
        self.messages = messages
        self.expected_pw = expected_pw
        self._ordered_keys = list(messages.keys())
        self._deleted_indices: set[int] = set()

    def user(self, _u: str) -> bytes:
        return b"+OK"

    def pass_(self, pw: str) -> bytes:
        if pw != self.expected_pw:
            raise RuntimeError("-ERR [AUTH] Invalid POP3 password")
        return b"+OK"

    def stat(self) -> tuple[int, int]:
        return len(self._ordered_keys), sum(len(v) for v in self.messages.values())

    def uidl(self) -> tuple[bytes, list[bytes], int]:
        lines = [
            f"{idx + 1} {uidl}".encode("ascii")
            for idx, uidl in enumerate(self._ordered_keys)
        ]
        return b"+OK", lines, 0

    def retr(self, msg_num: int) -> tuple[bytes, list[bytes], int]:
        uidl = self._ordered_keys[msg_num - 1]
        raw = self.messages[uidl]
        return b"+OK", raw.splitlines(), len(raw)

    def dele(self, msg_num: int) -> bytes:
        self._deleted_indices.add(msg_num - 1)
        return b"+OK"

    def quit(self) -> bytes:
        for idx in sorted(self._deleted_indices, reverse=True):
            uidl = self._ordered_keys[idx]
            self.messages.pop(uidl, None)
        return b"+OK"


def _make_spam_bytes(sender: str, to_addr: str, subject: str, msg_id: str, score: float) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = to_addr
    m["Subject"] = subject
    m["Message-ID"] = msg_id
    m["Date"] = "Fri, 20 Nov 2026 06:30:00 +0000"
    m["X-Spamd-Result"] = f"default: True [{score:.2f} / 15.00]"
    m.set_content("Important invoice attached.")
    return m.as_bytes(policy=email.policy.SMTP)


def test_quarantine_digest_scanner_safe_release_and_allowlist_rescue(
    toolbox_env: dict[str, Path],
) -> None:
    imap_store = InMemoryImapStore()
    sent_emails: list[dict[str, Any]] = []

    def _imap_factory(user: str, _cfg: MailSettings) -> FakeImapConnection:
        return FakeImapConnection(imap_store, user)

    def _smtp_recorder(raw: bytes, mail_from: str, rcpt_to: str, _cfg: MailSettings) -> None:
        sent_emails.append({"raw": raw, "from": mail_from, "to": rcpt_to})

    set_imap_master_factory(_imap_factory)
    set_smtp_sender(_smtp_recorder)

    # Seed 1 spam message in max.muster's Junk; erika.vertretung & anna have 0 spam!
    imap_store.add_message(
        "max.muster",
        "Junk",
        _make_spam_bytes(
            "Partner Billing <billing@partner.example>",
            "max.muster@firma.de",
            "Rechnung #2026-99",
            "<inv-99@partner.example>",
            7.4,
        ),
        uid="501",
    )

    headers = {"X-Internal-Token": "internal-test-token-999"}
    with TestClient(app) as client:
        # 1. Admin quarantine list shows item
        r_list = client.get("/api/internal/quarantine", headers=headers)
        assert r_list.status_code == 200
        assert len(r_list.json()["items"]) == 1
        assert r_list.json()["items"][0]["spam_score"] == 7.4

        # 2. Run digest -> ONLY max.muster gets an email; re-running sends 0 duplicates!
        r_dig1 = client.post(
            "/api/internal/quarantine/run-digest",
            headers=headers,
            json={"user": ""},
        )
        assert r_dig1.status_code == 200
        assert r_dig1.json() == {"users_checked": 3, "digests_sent": 1, "items_included": 1}
        assert len(sent_emails) == 1
        assert sent_emails[0]["to"] == "max.muster@firma.de"

        r_dig2 = client.post(
            "/api/internal/quarantine/run-digest",
            headers=headers,
            json={"user": ""},
        )
        assert r_dig2.json() == {"users_checked": 3, "digests_sent": 0, "items_included": 0}
        assert len(sent_emails) == 1

        # Extract quarantine_token from sent digest email
        raw_text = sent_emails[0]["raw"].decode("utf-8", errors="replace")
        m_tok = re.search(r"quarantine_token=([A-Za-z0-9_\-]+)", raw_text)
        assert m_tok is not None
        token = m_tok.group(1)

        # 3. Scanner-safe GET /api/quarantine/token/{token} does NOT consume token!
        for _ in range(2):
            r_inspect = client.get(f"/api/quarantine/token/{token}")
            assert r_inspect.status_code == 200
            info = r_inspect.json()
            assert info["valid"] is True
            assert info["already_used"] is False
            assert info["expired"] is False
            assert info["recipient_user"] == "max.muster"
            assert info["subject"] == "Rechnung #2026-99"

        assert "501" in imap_store.folders["max.muster"]["Junk"]

        # 4. User clicks confirm -> POST /api/quarantine/action (`release_and_allow`)
        r_act = client.post(
            "/api/quarantine/action",
            json={"token": token, "op": "release_and_allow"},
        )
        assert r_act.status_code == 200
        act_body = r_act.json()
        assert act_body["ok"] is True
        assert act_body["delivered"] is True
        assert act_body["allowlisted"] is True

        # Message moved from Junk -> INBOX!
        assert len(imap_store.folders["max.muster"]["Junk"]) == 0
        assert len(imap_store.folders["max.muster"]["INBOX"]) == 1

        # Second POST marks already_used (409) and GET also shows already_used=True
        r_act_again = client.post(
            "/api/quarantine/action",
            json={"token": token, "op": "release"},
        )
        assert r_act_again.status_code == 409
        assert r_act_again.json()["already_used"] is True
        assert client.get(f"/api/quarantine/token/{token}").json()["already_used"] is True

        # 5. Future message from allowlisted `billing@partner.example` rescued automatically on reconcile!
        imap_store.add_message(
            "max.muster",
            "Junk",
            _make_spam_bytes(
                "billing@partner.example",
                "max.muster@firma.de",
                "Folgerechnung #100",
                "<inv-100@partner.example>",
                6.2,
            ),
            uid="502",
        )
        r_rec = client.post("/api/internal/reconcile", headers=headers)
        assert r_rec.status_code == 200
        assert len(imap_store.folders["max.muster"]["Junk"]) == 0
        assert len(imap_store.folders["max.muster"]["INBOX"]) == 2

        # 6. Verify daily scheduled digest runs at most once per calendar day
        update_runtime_settings({"digest_enabled": True, "digest_time": "07:30", "timezone": "UTC"})
        morning = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)
        assert check_and_run_scheduled_digest(now_utc=morning) is True
        assert check_and_run_scheduled_digest(now_utc=morning) is False

        # 7. Delete allowlist entry via admin API
        allow_id = client.get("/api/internal/quarantine", headers=headers).json()["allowlist"][0]["id"]
        r_del_al = client.post(
            "/api/internal/allowlist/delete",
            headers=headers,
            json={"id": allow_id},
        )
        assert r_del_al.json() == {"deleted": True}


def test_rspamd_mail_tracking_search_and_sqlite_cache_persistence(
    toolbox_env: dict[str, Path],
) -> None:
    rspamd_rows: list[dict[str, Any]] = [
        {
            "unix_time": 1763632800,
            "sender_smtp": "kunde@partner.de",
            "rcpt_smtp": ["max.muster@firma.de"],
            "subject": "Bestellung #1001",
            "action": "no action",
            "score": -0.5,
            "required_score": 15.0,
            "symbols": {"DKIM_SIGNED": {"score": -0.2}, "SPF_ALLOW": {"score": -0.3}},
            "ip": "198.51.100.10",
            "message-id": "<m1@partner.de>",
            "size": 4200,
        },
        {
            "unix_time": 1763632900,
            "sender_smtp": "promo@ads.example",
            "rcpt_smtp": ["anna@firma.de"],
            "subject": "Special Offer",
            "action": "add header",
            "score": 8.4,
            "required_score": 6.0,
            "symbols": ["BAYES_SPAM"],
            "ip": "203.0.113.5",
            "message-id": "<m2@ads.example>",
            "size": 8192,
        },
        {
            "unix_time": 1763633000,
            "sender_smtp": "new-mta@unknown.example",
            "rcpt_smtp": ["max.muster@firma.de"],
            "subject": "Anfrage",
            "action": "greylist",
            "score": 4.2,
            "required_score": 6.0,
            "symbols": ["GREYLIST"],
            "ip": "203.0.113.77",
            "message-id": "<m3@unknown.example>",
            "size": 1800,
        },
        {
            "unix_time": 1763633100,
            "sender_smtp": "bot@malware.example",
            "rcpt_smtp": ["max.muster@firma.de"],
            "subject": "Invoice.exe",
            "action": "reject",
            "score": 22.0,
            "required_score": 15.0,
            "symbols": ["CLAM_VIRUS", "RBL_SPAMHAUS"],
            "ip": "192.0.2.99",
            "message-id": "<m4@malware.example>",
            "size": 15200,
        },
    ]

    class MockRspamdHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("Password") != "rspamd-ctrl-secret":
                self.send_response(403)
                self.end_headers()
                return
            if self.path.startswith("/history"):
                body = json.dumps({"rows": rspamd_rows}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, _fmt: str, *_args: Any) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockRspamdHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    headers = {"X-Internal-Token": "internal-test-token-999"}
    try:
        with TestClient(app) as client:
            client.put(
                "/api/internal/settings",
                headers=headers,
                json={
                    "rspamd_url": f"http://127.0.0.1:{port}",
                    "rspamd_password": "rspamd-ctrl-secret",
                },
            )

            # Test connection endpoint
            r_test = client.post(
                "/api/internal/mail-tracking/test-rspamd",
                headers=headers,
                json={},
            )
            assert r_test.status_code == 200
            assert r_test.json()["ok"] is True
            assert r_test.json()["entries_available"] == 4

            # Search all -> 4 normalized statuses
            r_all = client.post(
                "/api/internal/mail-tracking/search",
                headers=headers,
                json={"action_filter": "all"},
            )
            assert r_all.status_code == 200
            assert r_all.json()["rspamd_connected"] is True
            assert r_all.json()["total"] == 4
            statuses = {e["status"] for e in r_all.json()["entries"]}
            assert statuses == {"delivered", "spam", "greylisted", "rejected"}

            # Simulate Rspamd clearing its in-memory ring buffer!
            rspamd_rows.clear()

            # SQLite cache STILL serves all historical entries & supports filtering!
            r_rej = client.post(
                "/api/internal/mail-tracking/search",
                headers=headers,
                json={"action_filter": "rejected", "recipient": "max.muster"},
            )
            assert r_rej.status_code == 200
            assert r_rej.json()["total"] == 1
            assert r_rej.json()["entries"][0]["sender"] == "bot@malware.example"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_external_imap_and_pop3_connectors_dedup_and_admin_failure_alert(
    toolbox_env: dict[str, Path],
) -> None:
    ext_imap_store = InMemoryImapStore()
    current_ext_pw = {"pw": "isp-secret-1"}
    delivered_smtp: list[dict[str, Any]] = []

    raw_ext_mail = (
        b"From: Externe Kundin <kundin@web.de>\r\n"
        b"To: info@strato-konto.de\r\n"
        b"Subject: Anfrage ueber Strato\r\n"
        b"Message-ID: <strato-1@web.de>\r\n\r\n"
        b"Guten Tag!\r\n"
    )
    ext_imap_store.add_message("info@strato-konto.de", "INBOX", raw_ext_mail, uid="9001")

    pop_messages = {
        "UIDL-POP-1": (
            b"From: Lieferant <sales@lieferant.de>\r\n"
            b"To: alt@t-online.de\r\n"
            b"Subject: Lieferschein\r\n\r\n"
            b"Paket unterwegs.\r\n"
        )
    }

    set_external_imap_factory(
        lambda _h, _p, _ssl: FakeImapConnection(
            ext_imap_store,
            "info@strato-konto.de",
            expected_pw=current_ext_pw["pw"],
        )
    )
    set_external_pop3_factory(
        lambda _h, _p, _ssl: FakePop3Connection(pop_messages, expected_pw="pop-pass-1")
    )
    set_smtp_sender(
        lambda raw, mfrom, rcpt, _cfg: delivered_smtp.append(
            {"raw": raw, "from": mfrom, "to": rcpt}
        )
    )

    headers = {"X-Internal-Token": "internal-test-token-999"}
    with TestClient(app) as client:
        client.put(
            "/api/internal/settings",
            headers=headers,
            json={"admin_alert_email": "admin-alert@firma.de"},
        )

        # 1. Create IMAPS connector (leave_on_server=True)
        r_save = client.post(
            "/api/internal/connectors",
            headers=headers,
            json={
                "name": "Strato Info-Postfach",
                "target_user": "max.muster",
                "target_email": "max.muster@firma.de",
                "protocol": "imaps",
                "host": "imap.strato.de",
                "port": 993,
                "use_ssl": True,
                "remote_user": "info@strato-konto.de",
                "remote_password": "isp-secret-1",
                "interval_minutes": 5,
                "leave_on_server": True,
                "enabled": True,
            },
        )
        assert r_save.status_code == 200
        conn_id = r_save.json()["connector"]["id"]
        assert r_save.json()["connector"]["password_set"] is True
        assert "encrypted_password" not in r_save.json()["connector"]

        # Test connection using saved encrypted password (`remote_password=""`)
        r_test = client.post(
            "/api/internal/connectors/test",
            headers=headers,
            json={
                "id": conn_id,
                "protocol": "imaps",
                "host": "imap.strato.de",
                "port": 993,
                "use_ssl": True,
                "remote_user": "info@strato-konto.de",
                "remote_password": "",
            },
        )
        assert r_test.json()["ok"] is True
        assert r_test.json()["message_count"] == 1

        # First sync fetches 1 message; second sync fetches 0 (UID deduplicated!)
        r_sync1 = client.post(
            "/api/internal/connectors/sync",
            headers=headers,
            json={"id": conn_id},
        )
        assert r_sync1.json() == {"ok": True, "fetched": 1, "error": "", "alert_sent": False}
        assert len(delivered_smtp) == 1
        assert delivered_smtp[0]["to"] == "max.muster@firma.de"

        r_sync2 = client.post(
            "/api/internal/connectors/sync",
            headers=headers,
            json={"id": conn_id},
        )
        assert r_sync2.json() == {"ok": True, "fetched": 0, "error": "", "alert_sent": False}
        assert len(delivered_smtp) == 1

        # 2. Simulate external ISP password change -> sync fails & sends admin warning mail!
        current_ext_pw["pw"] = "changed-at-strato-999"
        r_fail1 = client.post(
            "/api/internal/connectors/sync",
            headers=headers,
            json={"id": conn_id},
        )
        assert r_fail1.json()["ok"] is False
        assert r_fail1.json()["alert_sent"] is True
        assert len(delivered_smtp) == 2
        assert delivered_smtp[1]["to"] == "admin-alert@firma.de"
        assert b"Strato Info-Postfach" in delivered_smtp[1]["raw"]

        # Immediate second failure increments consecutive_failures to 2, but suppresses duplicate alert (24h cooldown)!
        r_fail2 = client.post(
            "/api/internal/connectors/sync",
            headers=headers,
            json={"id": conn_id},
        )
        assert r_fail2.json()["ok"] is False
        assert r_fail2.json()["alert_sent"] is False
        assert len(delivered_smtp) == 2

        conns = client.get("/api/internal/connectors", headers=headers).json()["connectors"]
        assert conns[0]["last_status"] == "error"
        assert conns[0]["consecutive_failures"] == 2

        # 3. Also test POP3S connector with leave_on_server=False
        r_pop = client.post(
            "/api/internal/connectors",
            headers=headers,
            json={
                "name": "Telekom Alt-Konto",
                "target_user": "anna",
                "target_email": "anna@firma.de",
                "protocol": "pop3s",
                "host": "securepop.t-online.de",
                "port": 995,
                "use_ssl": True,
                "remote_user": "alt@t-online.de",
                "remote_password": "pop-pass-1",
                "interval_minutes": 10,
                "leave_on_server": False,
                "enabled": True,
            },
        )
        pop_id = r_pop.json()["connector"]["id"]
        r_pop_sync = client.post(
            "/api/internal/connectors/sync",
            headers=headers,
            json={"id": pop_id},
        )
        assert r_pop_sync.json() == {"ok": True, "fetched": 1, "error": "", "alert_sent": False}
        assert len(pop_messages) == 0  # Deleted from POP3 server since leave_on_server=False!

        # Delete POP3 connector
        r_del = client.post(
            "/api/internal/connectors/delete",
            headers=headers,
            json={"id": pop_id},
        )
        assert r_del.json() == {"deleted": True, "id": pop_id}
