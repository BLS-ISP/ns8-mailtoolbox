"""End-to-End Integration Suite for Phase 2 (Signatures & Milter) and Phase 3 (Quarantine, Tracking & Connectors).

Exercises real wire protocols over localhost TCP/HTTP:
- Phase 2: Binary Postfix Milter (`SMFIC_*` / `SMFIR_*`) + MIME PDF attachment preservation + empty AD line stripping + `append_once` dedup + inbound `SMFIR_ACCEPT`
- Phase 3a: Dovecot Master-User IMAP (`Junk` -> `INBOX`) + Daily Quarantine Digest + Scanner-Safe Two-Step Public Token Release + Sender Allowlist rescue
- Phase 3b: Rspamd HTTP Controller `/history` polling + filter search + SQLite persistence after Rspamd ring-buffer clear
- Phase 3c: External POP3/IMAP Connectors + Fernet password encryption + UID/UIDL dedup + automatic Admin Warning Email on auth failure (with cooldown)
"""

from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
import json
from pathlib import Path
import re
import socket
import sqlite3
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from ldap3 import Connection, MOCK_SYNC, Server
import pytest

from app.backend.config import LdapSettings
from app.backend.ldap_client import set_ldap_connection_factory
from app.backend.main import app
from tests._mock_managesieve import MockManageSieveServer
from tests._mock_phase23_servers import (
    MilterTestClient,
    MockImapServer,
    MockPop3Server,
    MockRspamdServer,
    MockSmtpSubmissionServer,
    SMFIR_ACCEPT,
    SMFIR_CONTINUE,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT_DIR / "app" / "frontend" / "dist"


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _build_rich_ldap_server() -> Server:
    """Mock OpenLDAP server with AD/LDAP contact attributes (`telephoneNumber`, `company`, no `mobile` for max.muster)."""
    server = Server("mock_phase23_ldap", get_info=None)
    conn = Connection(server, client_strategy=MOCK_SYNC)
    conn.bind()

    base_dn = "dc=firma,dc=de"
    conn.strategy.add_entry(
        f"cn=ldapservice,{base_dn}",
        {
            "objectClass": ["person", "simpleSecurityObject"],
            "cn": "ldapservice",
            "sn": "Service",
            "userPassword": "svc-bind-secret",
        },
    )
    conn.strategy.add_entry(
        f"uid=max.muster,ou=People,{base_dn}",
        {
            "objectClass": ["inetOrgPerson", "posixAccount"],
            "uid": "max.muster",
            "cn": "Max Mustermann",
            "displayName": "Max Mustermann",
            "givenName": "Max",
            "sn": "Mustermann",
            "mail": "max.muster@firma.de",
            "departmentNumber": "Vertrieb",
            "title": "Projektleiter",
            "telephoneNumber": "+49 89 12345-10",
            "o": "Muster GmbH",
            # Intentionally NO `mobile` attribute so `{{mobile}}` resolves to "" for empty-line stripping test!
            "uidNumber": 1002,
            "gidNumber": 2002,
            "homeDirectory": "/home/max.muster",
            "userPassword": "MaxSecret!2026",
        },
    )
    conn.strategy.add_entry(
        f"uid=claudia.schmidt,ou=People,{base_dn}",
        {
            "objectClass": ["inetOrgPerson", "posixAccount"],
            "uid": "claudia.schmidt",
            "cn": "Claudia Schmidt",
            "displayName": "Claudia Schmidt",
            "givenName": "Claudia",
            "sn": "Schmidt",
            "mail": "claudia.schmidt@firma.de",
            "departmentNumber": "Buchhaltung",
            "title": "Teamleiterin",
            "telephoneNumber": "+49 89 12345-20",
            "mobile": "+49 170 9876543",
            "o": "Muster GmbH",
            "uidNumber": 1003,
            "gidNumber": 2002,
            "homeDirectory": "/home/claudia.schmidt",
            "userPassword": "ClaudiaSecret!2026",
        },
    )
    conn.strategy.add_entry(
        f"uid=anna.sekretariat,ou=People,{base_dn}",
        {
            "objectClass": ["inetOrgPerson", "posixAccount"],
            "uid": "anna.sekretariat",
            "cn": "Anna Sekretariat",
            "displayName": "Anna Sekretariat",
            "givenName": "Anna",
            "sn": "Sekretariat",
            "mail": "anna.sekretariat@firma.de",
            "departmentNumber": "Verwaltung",
            "title": "Office Management",
            "uidNumber": 1001,
            "gidNumber": 2001,
            "homeDirectory": "/home/anna.sekretariat",
            "userPassword": "AnnaSecret!2026",
        },
    )
    conn.strategy.add_entry(
        f"cn=mail-office,ou=Groups,{base_dn}",
        {
            "objectClass": ["posixGroup"],
            "cn": "mail-office",
            "gidNumber": 3001,
            "memberUid": ["anna.sekretariat"],
        },
    )
    conn.strategy.add_entry(
        f"cn=vertrieb-team,ou=Groups,{base_dn}",
        {
            "objectClass": ["posixGroup"],
            "cn": "vertrieb-team",
            "gidNumber": 3010,
            "memberUid": ["max.muster"],
        },
    )
    conn.unbind()
    return server


@pytest.fixture()
def phase23_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin up full local stack with ManageSieve, IMAP, POP3, SMTP, Rspamd, LDAP, and Milter."""
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    sieve_srv = MockManageSieveServer(master_user="vmail", master_password="dovecot-master-secret")
    imap_srv = MockImapServer(master_user="vmail", master_password="dovecot-master-secret")
    pop3_srv = MockPop3Server()
    smtp_srv = MockSmtpSubmissionServer()
    rspamd_srv = MockRspamdServer(password="rspamd-secret-xyz")

    sieve_host, sieve_port = sieve_srv.start()
    _, imap_port = imap_srv.start()
    _, pop3_port = pop3_srv.start()
    _, smtp_port = smtp_srv.start()
    rspamd_url = rspamd_srv.start()
    milter_port = _find_free_tcp_port()

    internal_token = "phase23-internal-token-778899"
    (config_dir / "discovery_mail.env").write_text(
        f"MAIL_HOST={sieve_host}\n"
        f"MAIL_IMAP_PORT={imap_port}\n"
        f"MAIL_SIEVE_PORT={sieve_port}\n"
        f"MAIL_SUBMISSION_PORT={smtp_port}\n"
        "MAIL_MODULE_ID=mail1\n",
        encoding="utf-8",
    )
    (config_dir / "discovery_ldap.env").write_text(
        "LDAP_HOST=127.0.0.1\n"
        "LDAP_PORT=389\n"
        "LDAP_BASE=dc=firma,dc=de\n"
        "LDAP_USER=cn=ldapservice,dc=firma,dc=de\n"
        "LDAP_PASS=svc-bind-secret\n"
        "LDAP_SCHEMA=rfc2307\n"
        "LDAP_DOMAIN=firma.de\n",
        encoding="utf-8",
    )
    (config_dir / "master.creds").write_text("vmail:dovecot-master-secret\n", encoding="utf-8")
    (config_dir / "internal.token").write_text(f"{internal_token}\n", encoding="utf-8")
    (config_dir / "mailboxes_cache.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-11-20T12:00:00Z",
                "mailboxes": [
                    {
                        "user": "max.muster",
                        "enabled": True,
                        "aliases": ["m.muster@firma.de"],
                        "postfix_forwards": [],
                    },
                    {
                        "user": "claudia.schmidt",
                        "enabled": True,
                        "aliases": ["buchhaltung@firma.de"],
                        "postfix_forwards": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    if (FRONTEND_DIST / "index.html").is_file():
        monkeypatch.setenv("STATIC_DIR", str(FRONTEND_DIST))
    monkeypatch.setenv("APP_SECRET_KEY", "phase23-secret-key-0123456789abcdef0123456789")
    monkeypatch.setenv("ALLOWED_GROUPS", "mail-office")
    monkeypatch.setenv("PORTAL_HOSTNAME", "vertretung.firma.de")
    monkeypatch.setenv("MILTER_ENABLED", "1")
    monkeypatch.setenv("MILTER_LISTEN_PORT", str(milter_port))
    monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "3600")

    ldap_server = _build_rich_ldap_server()

    def _ldap_factory(_cfg: LdapSettings, bind_dn: str | None, bind_pw: str | None) -> Connection:
        return Connection(
            ldap_server,
            user=bind_dn,
            password=bind_pw or "",
            client_strategy=MOCK_SYNC,
            auto_bind=False,
        )

    set_ldap_connection_factory(_ldap_factory)

    try:
        yield {
            "data_dir": data_dir,
            "config_dir": config_dir,
            "internal_token": internal_token,
            "headers": {"X-Internal-Token": internal_token},
            "milter_port": milter_port,
            "sieve": sieve_srv,
            "imap": imap_srv,
            "pop3": pop3_srv,
            "smtp": smtp_srv,
            "rspamd": rspamd_srv,
            "rspamd_url": rspamd_url,
        }
    finally:
        set_ldap_connection_factory(None)
        rspamd_srv.stop()
        smtp_srv.stop()
        pop3_srv.stop()
        imap_srv.stop()
        sieve_srv.stop()


def test_phase2_before_dkim_milter_multipart_pdf_dedup_and_inbound_skip(
    phase23_stack: dict,
) -> None:
    """Verify binary Postfix Milter signing, empty AD line stripping, PDF preservation, reply dedup & inbound skip."""
    hdrs = phase23_stack["headers"]

    html_sig = (
        '<div class="corp-sig">\n'
        "  <p><strong>{{displayName}}</strong> — {{title}}</p>\n"
        "  <p>{{department}} | {{company}}</p>\n"
        "  <table>\n"
        "    <tr><td>Tel: {{telephoneNumber}}</td></tr>\n"
        "    <tr><td>Mobil: {{mobile}}</td></tr>\n"
        "    <tr><td>E-Mail: {{mail}}</td></tr>\n"
        "  </table>\n"
        "</div>"
    )
    text_sig = (
        "-- \n"
        "{{displayName}} — {{title}}\n"
        "{{department}} | {{company}}\n"
        "Tel: {{telephoneNumber}}\n"
        "Mobil: {{mobile}}\n"
        "E-Mail: {{mail}}"
    )

    with TestClient(app) as client:
        # 1. Create group-scoped signature rule for `vertrieb-team`
        create_res = client.post(
            "/api/internal/signatures",
            headers=hdrs,
            json={
                "id": None,
                "name": "Vertrieb Corporate Signatur",
                "scope_type": "group",
                "scope_value": "vertrieb-team",
                "priority": 5,
                "enabled": True,
                "html_template": html_sig,
                "text_template": text_sig,
                "auto_plain_from_html": False,
                "reply_mode": "append_once",
                "placement": "below_reply",
            },
        )
        assert create_res.status_code == 200, create_res.text

        # Check Milter listening port & preview endpoint
        list_res = client.get("/api/internal/signatures", headers=hdrs)
        assert list_res.status_code == 200
        milter_status = list_res.json()["milter_status"]
        assert milter_status["listening"] is True
        listen_port = int(milter_status.get("port") or phase23_stack["milter_port"])

        prev_res = client.post(
            "/api/internal/signatures/preview",
            headers=hdrs,
            json={
                "user": "max.muster",
                "sender_email": "max.muster@firma.de",
                "html_template": html_sig,
                "text_template": text_sig,
            },
        )
        assert prev_res.status_code == 200
        prev_json = prev_res.json()
        assert "Max Mustermann" in prev_json["rendered_html"]
        assert "+49 89 12345-10" in prev_json["rendered_html"]
        assert "Mobil:" not in prev_json["rendered_html"]
        assert "Mobil:" not in prev_json["rendered_text"]

        milter_client = MilterTestClient("127.0.0.1", listen_port)

        # 2a. Multipart email (`multipart/mixed` -> `multipart/alternative` + PDF attachment)
        pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
        msg = EmailMessage()
        msg["From"] = "Max Mustermann <max.muster@firma.de>"
        msg["To"] = "kunde@extern.de"
        msg["Subject"] = "Projektangebot 2026"
        msg["Message-ID"] = "<offerta-001@firma.de>"
        msg.set_content("Hallo Herr Kunde,\n\nanbei erhalten Sie unser Angebot.\n")
        msg.add_alternative(
            "<html><body><p>Hallo Herr Kunde,</p><p>anbei erhalten Sie unser Angebot.</p></body></html>",
            subtype="html",
        )
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename="Angebot-2026.pdf",
        )
        raw_outbound = msg.as_bytes(policy=SMTP_POLICY)

        signed_res = milter_client.send_message(
            raw_outbound,
            sender="max.muster@firma.de",
            recipients=["kunde@extern.de"],
            auth_authen="max.muster",
        )
        assert signed_res.disposition in {SMFIR_CONTINUE, SMFIR_ACCEPT}
        assert signed_res.body_replaced is True

        signed_msg = signed_res.parsed_message
        assert signed_msg.is_multipart()

        plain_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[tuple[str, bytes]] = []
        for part in signed_msg.walk():
            if part.is_multipart():
                continue
            disp = part.get_content_disposition()
            ctype = part.get_content_type()
            if disp == "attachment" or ctype == "application/pdf":
                attachments.append((part.get_filename() or "", part.get_payload(decode=True) or b""))
            elif ctype == "text/plain":
                plain_parts.append(part.get_content())
            elif ctype == "text/html":
                html_parts.append(part.get_content())

        assert len(attachments) == 1
        assert attachments[0] == ("Angebot-2026.pdf", pdf_bytes)

        joined_plain = "\n".join(plain_parts)
        joined_html = "\n".join(html_parts)
        for rendered in (joined_plain, joined_html):
            assert "Max Mustermann" in rendered
            assert "Projektleiter" in rendered
            assert "+49 89 12345-10" in rendered
            assert "Muster GmbH" in rendered
            assert "Mobil:" not in rendered, f"Empty AD field line was not stripped:\n{rendered}"

        assert "<!-- ns8-toolbox-sig -->" in joined_html
        assert joined_html.count("<!-- ns8-toolbox-sig -->") == 1

        # 2b. Reply-chain email ALREADY containing signature -> `append_once` must NOT double-append!
        reply_msg = EmailMessage()
        reply_msg["From"] = "Max Mustermann <max.muster@firma.de>"
        reply_msg["To"] = "kunde@extern.de"
        reply_msg["Subject"] = "Re: Projektangebot 2026"
        reply_msg["In-Reply-To"] = "<offerta-001@firma.de>"
        reply_msg["References"] = "<offerta-001@firma.de>"
        reply_msg.set_content("Kurze Rückfrage dazu.\n\n" + joined_plain)
        reply_msg.add_alternative(
            "<html><body><p>Kurze Rückfrage dazu.</p>"
            f'<blockquote class="gmail_quote">{joined_html}</blockquote></body></html>',
            subtype="html",
        )
        reply_res = milter_client.send_message(
            reply_msg.as_bytes(policy=SMTP_POLICY),
            sender="max.muster@firma.de",
            recipients=["kunde@extern.de"],
            auth_authen="max.muster",
        )
        reply_html = "".join(
            p.get_content()
            for p in reply_res.parsed_message.walk()
            if not p.is_multipart() and p.get_content_type() == "text/html"
        )
        assert reply_html.count("<!-- ns8-toolbox-sig -->") == 1

        # 2c. Unauthenticated external inbound email on port 25 (no `{auth_authen}`) -> immediate SMFIR_ACCEPT
        inbound_msg = EmailMessage()
        inbound_msg["From"] = "lieferant@extern.org"
        inbound_msg["To"] = "max.muster@firma.de"
        inbound_msg["Subject"] = "Lieferschein"
        inbound_msg.set_content("Guten Tag, hier ist Ihr Lieferschein.\n")
        raw_inbound = inbound_msg.as_bytes(policy=SMTP_POLICY)

        inbound_res = milter_client.send_message(
            raw_inbound,
            sender="lieferant@extern.org",
            recipients=["max.muster@firma.de"],
            auth_authen=None,
        )
        assert inbound_res.disposition == SMFIR_ACCEPT
        assert inbound_res.body_replaced is False
        assert inbound_res.raw_message == raw_inbound


def test_phase3a_quarantine_digest_and_scanner_safe_token_release(
    phase23_stack: dict,
) -> None:
    """Verify IMAP Junk digest deduplication, scanner-safe GET token inspection, POST release+allowlist & rescue."""
    hdrs = phase23_stack["headers"]
    imap_srv: MockImapServer = phase23_stack["imap"]
    smtp_srv: MockSmtpSubmissionServer = phase23_stack["smtp"]

    spam_mail = EmailMessage()
    spam_mail["From"] = "Newsletter Team <angebote@b2b-partner.de>"
    spam_mail["To"] = "max.muster@firma.de"
    spam_mail["Subject"] = "Wichtige Preisliste November"
    spam_mail["Date"] = "Fri, 20 Nov 2026 06:10:00 +0100"
    spam_mail["Message-ID"] = "<spam-nov-001@b2b-partner.de>"
    spam_mail["X-Spam-Score"] = "8.4"
    spam_mail.set_content("Sehr geehrter Kunde, hier finden Sie unsere Preisliste.\n")

    imap_srv.seed_message("max.muster", "Junk", spam_mail.as_bytes(policy=SMTP_POLICY))
    # claudia.schmidt has 0 messages in Junk -> must NOT receive a digest email!
    imap_srv.folders.setdefault("claudia.schmidt", {"INBOX": [], "Junk": []})
    smtp_srv.clear()

    with TestClient(app) as client:
        # 1. Admin quarantine overview lists the Junk mail
        q_list = client.get("/api/internal/quarantine", headers=hdrs)
        assert q_list.status_code == 200
        items = q_list.json()["items"]
        assert len(items) == 1
        assert items[0]["user"] == "max.muster"
        assert "angebote@b2b-partner.de" in items[0]["sender"]
        assert items[0]["subject"] == "Wichtige Preisliste November"

        # 2. Run Quarantine Digest -> sends 1 email ONLY to max.muster@firma.de
        run1 = client.post(
            "/api/internal/quarantine/run-digest",
            headers=hdrs,
            json={"user": ""},
        )
        assert run1.status_code == 200
        assert run1.json()["digests_sent"] == 1
        sent_mails = smtp_srv.get_messages()
        assert len(sent_mails) == 1
        assert sent_mails[0].rcpt_tos == ["max.muster@firma.de"]

        digest_raw_text = sent_mails[0].raw_bytes.decode("utf-8", errors="replace")
        # Extract URL from HTML/text digest (decode quoted-printable soft linebreaks if any)
        unwrapped = re.sub(r"=\r?\n", "", digest_raw_text).replace("=3D", "=")
        url_match = re.search(
            r"https://vertretung\.firma\.de/\?quarantine_token=([A-Za-z0-9._~-]+)",
            unwrapped,
        )
        assert url_match, f"Digest email missing quarantine_token URL:\n{unwrapped}"
        parsed_qs = parse_qs(urlparse(url_match.group(0)).query)
        token = parsed_qs["quarantine_token"][0]

        # 3. Re-running digest immediately sends 0 duplicate digests
        run2 = client.post(
            "/api/internal/quarantine/run-digest",
            headers=hdrs,
            json={"user": ""},
        )
        assert run2.status_code == 200
        assert run2.json()["digests_sent"] == 0
        assert len(smtp_srv.get_messages()) == 1

        # 4. Scanner-Safe Step 1: Corporate link-scanner prefetches GET twice WITHOUT consuming token!
        for _ in range(2):
            inspect_res = client.get(f"/api/quarantine/token/{token}")
            assert inspect_res.status_code == 200
            info = inspect_res.json()
            assert info["valid"] is True
            assert info["already_used"] is False
            assert info["expired"] is False
            assert info["subject"] == "Wichtige Preisliste November"
            assert "angebote@b2b-partner.de" in info["sender"].lower()

        assert len(imap_srv.list_folder_messages("max.muster", "Junk")) == 1
        assert len(imap_srv.list_folder_messages("max.muster", "INBOX")) == 0

        # 5. Scanner-Safe Step 2: Employee confirms release_and_allow via POST (no login cookie!)
        act_res = client.post(
            "/api/quarantine/action",
            json={"token": token, "op": "release_and_allow"},
        )
        assert act_res.status_code == 200, act_res.text
        act_json = act_res.json()
        assert act_json["ok"] is True
        assert act_json["delivered"] is True
        assert act_json["allowlisted"] is True

        # Message moved from Junk -> INBOX and expunged from Junk!
        assert len(imap_srv.list_folder_messages("max.muster", "Junk")) == 0
        inbox_msgs = imap_srv.list_folder_messages("max.muster", "INBOX")
        assert len(inbox_msgs) == 1
        assert b"<spam-nov-001@b2b-partner.de>" in inbox_msgs[0].raw_bytes

        # Token is now marked already_used on subsequent GET and rejected on replay POST
        after_get = client.get(f"/api/quarantine/token/{token}")
        assert after_get.status_code == 200
        assert after_get.json()["already_used"] is True

        replay_post = client.post(
            "/api/quarantine/action",
            json={"token": token, "op": "release"},
        )
        assert replay_post.status_code in {400, 409, 200}
        if replay_post.status_code == 200:
            assert replay_post.json().get("ok") is False or replay_post.json().get("already_used") is True

        # Allowlist entry exists and reconciler rescues future Junk mail from allowlisted sender!
        q_after = client.get("/api/internal/quarantine", headers=hdrs).json()
        allowlist = q_after["allowlist"]
        assert any(
            a["user"] == "max.muster" and a["sender_email"].lower() == "angebote@b2b-partner.de"
            for a in allowlist
        )

        future_spam = EmailMessage()
        future_spam["From"] = "angebote@b2b-partner.de"
        future_spam["To"] = "max.muster@firma.de"
        future_spam["Subject"] = "Dezember Update"
        future_spam["Message-ID"] = "<spam-dec-002@b2b-partner.de>"
        future_spam.set_content("Folgenachricht vom freigegebenen Absender.\n")
        imap_srv.seed_message("max.muster", "Junk", future_spam.as_bytes(policy=SMTP_POLICY))

        rec_res = client.post("/api/internal/reconcile", headers=hdrs)
        assert rec_res.status_code == 200
        assert len(imap_srv.list_folder_messages("max.muster", "Junk")) == 0
        assert len(imap_srv.list_folder_messages("max.muster", "INBOX")) == 2


def test_phase3b_mail_tracking_rspamd_search_and_sqlite_persistence(
    phase23_stack: dict,
) -> None:
    """Verify Rspamd `/history` status normalization, filtering, and SQLite cache survival after Rspamd reset."""
    hdrs = phase23_stack["headers"]
    rspamd_srv: MockRspamdServer = phase23_stack["rspamd"]
    rspamd_url: str = phase23_stack["rspamd_url"]

    rspamd_srv.set_rows(
        [
            {
                "id": "rsp-1",
                "unix_time": 1763636400,
                "sender_smtp": "rechnung@lieferant.de",
                "rcpt_smtp": ["claudia.schmidt@firma.de"],
                "subject": "Rechnung RE-2026-101",
                "action": "no action",
                "score": -0.8,
                "required_score": 6.0,
                "symbols": {"DKIM_ALLOW": {"score": -0.2}, "SPF_ALLOW": {"score": -0.2}},
                "ip": "198.51.100.10",
                "message-id": "<re-101@lieferant.de>",
                "size": 18420,
            },
            {
                "id": "rsp-2",
                "unix_time": 1763636500,
                "sender_smtp": "promo@casino-spam.example",
                "rcpt_smtp": ["max.muster@firma.de"],
                "subject": "Gewinnen Sie jetzt!",
                "action": "add header",
                "score": 9.5,
                "required_score": 6.0,
                "symbols": {"BAYES_SPAM": {"score": 5.1}, "RBL_SPAMHAUS": {"score": 4.4}},
                "ip": "203.0.113.55",
                "message-id": "<spam-99@casino-spam.example>",
                "size": 4120,
            },
            {
                "id": "rsp-3",
                "unix_time": 1763636600,
                "sender_smtp": "neu@unbekannt.org",
                "rcpt_smtp": ["max.muster@firma.de"],
                "subject": "Erstanfrage Projekt",
                "action": "greylist",
                "score": 4.2,
                "required_score": 6.0,
                "symbols": {"GREYLIST": {"score": 0.0}},
                "ip": "192.0.2.77",
                "message-id": "<grey-1@unbekannt.org>",
                "size": 2200,
            },
            {
                "id": "rsp-4",
                "unix_time": 1763636700,
                "sender_smtp": "malware@bad-host.invalid",
                "rcpt_smtp": ["claudia.schmidt@firma.de"],
                "subject": "Dringende Zahlung.exe",
                "action": "reject",
                "score": 18.7,
                "required_score": 6.0,
                "symbols": {"CLAM_VIRUS": {"score": 15.0}},
                "ip": "203.0.113.99",
                "message-id": "<virus-1@bad-host.invalid>",
                "size": 65000,
            },
        ]
    )

    with TestClient(app) as client:
        # 1. Save Rspamd URL + password via /api/internal/settings and verify secret masking/preservation
        put_cfg = client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={
                "milter_enabled": True,
                "rspamd_url": rspamd_url,
                "rspamd_password": "rspamd-secret-xyz",
                "digest_enabled": True,
                "digest_time": "07:30",
                "timezone": "Europe/Berlin",
                "admin_alert_email": "admin@firma.de",
            },
        )
        assert put_cfg.status_code == 200
        get_cfg = client.get("/api/internal/settings", headers=hdrs).json()
        assert get_cfg["rspamd_password_set"] is True
        assert "rspamd-secret-xyz" not in json.dumps(get_cfg)

        # Updating settings with empty rspamd_password preserves existing secret
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={"rspamd_url": rspamd_url, "rspamd_password": ""},
        )
        assert client.get("/api/internal/settings", headers=hdrs).json()["rspamd_password_set"] is True

        # 2. Test Rspamd connection endpoint
        test_res = client.post(
            "/api/internal/mail-tracking/test-rspamd",
            headers=hdrs,
            json={"rspamd_url": rspamd_url, "rspamd_password": ""},
        )
        assert test_res.status_code == 200
        assert test_res.json()["ok"] is True

        # 3. Search all history & verify normalized status mapping
        all_res = client.post(
            "/api/internal/mail-tracking/search",
            headers=hdrs,
            json={"action_filter": "all", "limit": 200},
        )
        assert all_res.status_code == 200
        all_json = all_res.json()
        assert all_json["rspamd_connected"] is True
        assert all_json["total"] == 4
        statuses = {e["message_id"]: e["status"] for e in all_json["entries"]}
        assert statuses == {
            "<re-101@lieferant.de>": "delivered",
            "<spam-99@casino-spam.example>": "spam",
            "<grey-1@unbekannt.org>": "greylisted",
            "<virus-1@bad-host.invalid>": "rejected",
        }

        # 4. Clear Rspamd in-memory ring buffer -> SQLite cache MUST still serve history & filters!
        rspamd_srv.clear_rows()

        rej_res = client.post(
            "/api/internal/mail-tracking/search",
            headers=hdrs,
            json={"action_filter": "rejected", "recipient": "claudia.schmidt@firma.de"},
        )
        assert rej_res.status_code == 200
        rej_entries = rej_res.json()["entries"]
        assert len(rej_entries) == 1
        assert rej_entries[0]["message_id"] == "<virus-1@bad-host.invalid>"
        assert "CLAM_VIRUS" in rej_entries[0]["symbols"]


def test_phase3c_external_pop3_imap_connectors_dedup_encryption_and_admin_alert(
    phase23_stack: dict,
) -> None:
    """Verify POP3/IMAP fetch, Fernet encryption at rest, UID/UIDL dedup, and failure admin warning email."""
    hdrs = phase23_stack["headers"]
    pop3_srv: MockPop3Server = phase23_stack["pop3"]
    imap_srv: MockImapServer = phase23_stack["imap"]
    smtp_srv: MockSmtpSubmissionServer = phase23_stack["smtp"]
    data_dir: Path = phase23_stack["data_dir"]

    pop3_srv.add_account("kontakt@alt-domain.de", "StratoSecret!99")
    for idx in (1, 2):
        ext_msg = EmailMessage()
        ext_msg["From"] = f"kunde{idx}@extern.de"
        ext_msg["To"] = "kontakt@alt-domain.de"
        ext_msg["Subject"] = f"Bestellung #{idx}"
        ext_msg["Message-ID"] = f"<pop3-order-{idx}@extern.de>"
        ext_msg.set_content(f"Inhalt der Bestellung #{idx}\n")
        pop3_srv.seed_message(
            "kontakt@alt-domain.de",
            f"UIDL-ORDER-{idx}",
            ext_msg.as_bytes(policy=SMTP_POLICY),
        )

    imap_srv.add_account("archiv@ionos-konto.de", "IonosSecret!88")
    imap_ext_msg = EmailMessage()
    imap_ext_msg["From"] = "steuerberater@kanzlei.de"
    imap_ext_msg["To"] = "archiv@ionos-konto.de"
    imap_ext_msg["Subject"] = "BWA Oktober"
    imap_ext_msg["Message-ID"] = "<ionos-bwa-1@kanzlei.de>"
    imap_ext_msg.set_content("Anbei die aktuelle BWA.\n")
    imap_srv.seed_message("archiv@ionos-konto.de", "INBOX", imap_ext_msg.as_bytes(policy=SMTP_POLICY))

    smtp_srv.clear()

    with TestClient(app) as client:
        # Configure admin_alert_email
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={"admin_alert_email": "it-notfall@firma.de"},
        )

        # 1. Test POP3 connector connection before saving
        test_pop = client.post(
            "/api/internal/connectors/test",
            headers=hdrs,
            json={
                "id": None,
                "protocol": "pop3",
                "host": "127.0.0.1",
                "port": pop3_srv.port,
                "use_ssl": False,
                "remote_user": "kontakt@alt-domain.de",
                "remote_password": "StratoSecret!99",
            },
        )
        assert test_pop.status_code == 200
        assert test_pop.json()["ok"] is True
        assert test_pop.json()["message_count"] == 2

        # 2. Save POP3 connector (`leave_on_server=True`)
        save_pop = client.post(
            "/api/internal/connectors",
            headers=hdrs,
            json={
                "id": None,
                "name": "Alt-Domain Strato POP3",
                "target_user": "max.muster",
                "target_email": "max.muster@firma.de",
                "protocol": "pop3",
                "host": "127.0.0.1",
                "port": pop3_srv.port,
                "use_ssl": False,
                "remote_user": "kontakt@alt-domain.de",
                "remote_password": "StratoSecret!99",
                "interval_minutes": 5,
                "leave_on_server": True,
                "enabled": True,
            },
        )
        assert save_pop.status_code == 200
        pop_cid = save_pop.json()["connector"]["id"]
        assert save_pop.json()["connector"]["password_set"] is True
        assert "StratoSecret!99" not in json.dumps(save_pop.json())

        # Verify password is encrypted at rest in SQLite (never stored in plaintext!)
        with sqlite3.connect(str(data_dir / "toolbox.sqlite")) as conn:
            raw_rows = str(conn.execute("SELECT * FROM mail_connectors").fetchall())
            assert "StratoSecret!99" not in raw_rows

        # Updating connector with blank password keeps saved encrypted password
        upd_pop = client.post(
            "/api/internal/connectors",
            headers=hdrs,
            json={
                "id": pop_cid,
                "name": "Alt-Domain Strato POP3",
                "target_user": "max.muster",
                "target_email": "max.muster@firma.de",
                "protocol": "pop3",
                "host": "127.0.0.1",
                "port": pop3_srv.port,
                "use_ssl": False,
                "remote_user": "kontakt@alt-domain.de",
                "remote_password": "",
                "interval_minutes": 5,
                "leave_on_server": True,
                "enabled": True,
            },
        )
        assert upd_pop.status_code == 200
        assert upd_pop.json()["connector"]["password_set"] is True

        # 3. Sync POP3 connector -> fetches 2 messages, leaves on server
        sync1 = client.post(
            "/api/internal/connectors/sync",
            headers=hdrs,
            json={"id": pop_cid},
        )
        assert sync1.status_code == 200
        assert sync1.json()["ok"] is True
        assert sync1.json()["fetched"] == 2
        assert pop3_srv.count_messages("kontakt@alt-domain.de") == 2
        assert len(smtp_srv.get_messages()) == 2
        assert all(m.rcpt_tos == ["max.muster@firma.de"] for m in smtp_srv.get_messages())

        # 4. Second sync immediately -> UIDL deduplication fetches 0 duplicates!
        sync2 = client.post(
            "/api/internal/connectors/sync",
            headers=hdrs,
            json={"id": pop_cid},
        )
        assert sync2.status_code == 200
        assert sync2.json()["ok"] is True
        assert sync2.json()["fetched"] == 0
        assert len(smtp_srv.get_messages()) == 2

        # 5. Also verify external IMAP connector with `leave_on_server=False`
        save_imap = client.post(
            "/api/internal/connectors",
            headers=hdrs,
            json={
                "id": None,
                "name": "IONOS Buchhaltung IMAP",
                "target_user": "claudia.schmidt",
                "target_email": "claudia.schmidt@firma.de",
                "protocol": "imap",
                "host": "127.0.0.1",
                "port": imap_srv.port,
                "use_ssl": False,
                "remote_user": "archiv@ionos-konto.de",
                "remote_password": "IonosSecret!88",
                "interval_minutes": 10,
                "leave_on_server": False,
                "enabled": True,
            },
        )
        assert save_imap.status_code == 200
        imap_cid = save_imap.json()["connector"]["id"]
        sync_imap = client.post(
            "/api/internal/connectors/sync",
            headers=hdrs,
            json={"id": imap_cid},
        )
        assert sync_imap.status_code == 200
        assert sync_imap.json()["ok"] is True
        assert sync_imap.json()["fetched"] == 1
        assert len(imap_srv.list_folder_messages("archiv@ionos-konto.de", "INBOX")) == 0

        # 6. Simulate external password change -> failure recorded + Admin Alert Email sent ONCE!
        smtp_srv.clear()
        pop3_srv.set_auth_failure("kontakt@alt-domain.de", True)

        fail1 = client.post(
            "/api/internal/connectors/sync",
            headers=hdrs,
            json={"id": pop_cid},
        )
        assert fail1.status_code == 200
        assert fail1.json()["ok"] is False
        assert fail1.json()["alert_sent"] is True

        alerts = smtp_srv.get_messages()
        assert len(alerts) == 1
        assert alerts[0].rcpt_tos == ["it-notfall@firma.de"]
        alert_body = alerts[0].raw_bytes.decode("utf-8", errors="replace")
        assert "kontakt@alt-domain.de" in alert_body

        # Immediate second failure increments consecutive_failures to 2 WITHOUT spamming duplicate alert!
        fail2 = client.post(
            "/api/internal/connectors/sync",
            headers=hdrs,
            json={"id": pop_cid},
        )
        assert fail2.status_code == 200
        assert fail2.json()["ok"] is False
        assert fail2.json()["alert_sent"] is False
        assert len(smtp_srv.get_messages()) == 1

        connectors_list = client.get("/api/internal/connectors", headers=hdrs).json()["connectors"]
        pop_state = next(c for c in connectors_list if c["id"] == pop_cid)
        assert pop_state["last_status"] == "error"
        assert pop_state["consecutive_failures"] == 2


def test_production_hardening_gateway_portal_tracking_and_uninstall_cleanup(
    phase23_stack: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify CONTAINER_HOST_GATEWAY translation, portal_tracking_enabled (403 -> 200), and deactivate-all-vacations."""
    from app.backend.config import get_settings

    hdrs = phase23_stack["headers"]
    sieve_srv: MockManageSieveServer = phase23_stack["sieve"]
    rspamd_srv: MockRspamdServer = phase23_stack["rspamd"]
    rspamd_url: str = phase23_stack["rspamd_url"]

    # 1. Verify CONTAINER_HOST_GATEWAY=10.0.2.2 host translation in config.py
    assert get_settings().mail.host == "127.0.0.1"
    assert get_settings().ldap.host == "127.0.0.1"

    monkeypatch.setenv("CONTAINER_HOST_GATEWAY", "10.0.2.2")
    gw_settings = get_settings()
    assert gw_settings.mail.host == "10.0.2.2"
    assert gw_settings.ldap.host == "10.0.2.2"
    monkeypatch.delenv("CONTAINER_HOST_GATEWAY", raising=False)
    assert get_settings().mail.host == "127.0.0.1"

    # Seed Rspamd row & SOGo filter script for max.muster
    rspamd_srv.set_rows(
        [
            {
                "id": "portal-trk-1",
                "unix_time": 1763640000,
                "sender_smtp": "kunde@auftraggeber.de",
                "rcpt_smtp": ["max.muster@firma.de"],
                "subject": "Auftragsbestätigung AB-77",
                "action": "no action",
                "score": -0.5,
                "required_score": 6.0,
                "symbols": {"DKIM_ALLOW": {"score": -0.2}},
                "ip": "198.51.100.42",
                "message-id": "<ab-77@auftraggeber.de>",
                "size": 9400,
            }
        ]
    )
    sogo_script = 'require ["fileinto"];\nif header :contains "Subject" "[VIP]" { fileinto "VIP"; }\n'
    sieve_srv.seed_script("max.muster", "sogo", sogo_script, active=True)

    with TestClient(app) as client:
        # Check stored rspamd_url stays clean even when CONTAINER_HOST_GATEWAY is active
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={
                "rspamd_url": "http://127.0.0.1:11334",
                "rspamd_password": "rspamd-secret-xyz",
                "portal_tracking_enabled": False,
            },
        )
        monkeypatch.setenv("CONTAINER_HOST_GATEWAY", "10.0.2.2")
        stored_cfg = client.get("/api/internal/settings", headers=hdrs).json()
        assert stored_cfg["rspamd_url"] == "http://127.0.0.1:11334"
        eff_settings = get_settings()
        eff_url = getattr(eff_settings, "effective_rspamd_url", None) or getattr(
            getattr(eff_settings, "runtime", None), "effective_rspamd_url", None
        )
        if eff_url is not None:
            assert "10.0.2.2:11334" in eff_url
        monkeypatch.delenv("CONTAINER_HOST_GATEWAY", raising=False)

        # Point Rspamd to live mock URL with portal_tracking_enabled=False
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={
                "rspamd_url": rspamd_url,
                "rspamd_password": "rspamd-secret-xyz",
                "portal_tracking_enabled": False,
            },
        )

        # 2. Secretariat Portal Login -> portal_tracking_enabled=False -> 403 on /api/mail-tracking/search
        login_res = client.post(
            "/api/auth/login",
            json={"username": "anna.sekretariat", "password": "AnnaSecret!2026"},
        )
        assert login_res.status_code == 200
        assert login_res.json()["portal_tracking_enabled"] is False
        assert client.get("/api/auth/me").json()["portal_tracking_enabled"] is False

        denied_trk = client.post(
            "/api/mail-tracking/search",
            json={"action_filter": "all", "limit": 50},
        )
        assert denied_trk.status_code == 403

        # Enable portal_tracking_enabled=True -> /api/auth/me reports True & /api/mail-tracking/search returns 200
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={"portal_tracking_enabled": True},
        )
        assert client.get("/api/auth/me").json()["portal_tracking_enabled"] is True

        allowed_trk = client.post(
            "/api/mail-tracking/search",
            json={"sender": "auftraggeber.de", "action_filter": "all", "limit": 50},
        )
        assert allowed_trk.status_code == 200
        trk_entries = allowed_trk.json()["entries"]
        assert len(trk_entries) == 1
        assert trk_entries[0]["subject"] == "Auftragsbestätigung AB-77"
        assert trk_entries[0]["status"] == "delivered"

        # 3. Clean Uninstall (`POST /api/internal/deactivate-all-vacations`) restores "sogo" & deletes "toolbox-vacation"
        en_vac = client.put(
            "/api/internal/mailboxes/max.muster/vacation",
            headers=hdrs,
            json={
                "enabled": True,
                "start_date": "",
                "end_date": "",
                "subject": "Abwesenheit",
                "body": "Ich bin abwesend.",
                "forward_enabled": False,
                "forward_address": "",
                "keep_copy": True,
                "updated_by": "cockpit-admin",
            },
        )
        assert en_vac.status_code == 200
        assert sieve_srv.get_user_box("max.muster").active == "toolbox-vacation"

        deact_res = client.post("/api/internal/deactivate-all-vacations", headers=hdrs)
        assert deact_res.status_code == 200, deact_res.text
        deact_json = deact_res.json()
        assert deact_json["ok"] is True
        assert deact_json["deactivated"] >= 1
        assert deact_json["errors"] == []

        box_after = sieve_srv.get_user_box("max.muster")
        assert box_after.active == "sogo"
        assert "toolbox-vacation" not in box_after.scripts
        assert box_after.scripts["sogo"] == sogo_script

        audit_entries = client.get("/api/internal/audit", headers=hdrs).json()["entries"]
        assert any(
            e["action"] == "uninstall_cleanup" and e["source"] == "system"
            for e in audit_entries
        )

