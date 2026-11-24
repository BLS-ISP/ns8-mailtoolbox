"""Wire-Protocol End-to-End Tests for v1.0.6 Outgoing System Emails Overhaul.

Covers:
1. 60-Message SOGo Production Reproduction (`genera@bls-isp.net` on Samba AD `ad.bls-isp.net`):
   - Seeds 60 spam messages (220-char unbroken subjects, UTF-8/RFC-2047 headers, `<script>` /
     `</td><table>` injection strings) PLUS 1 legacy v1.0.5 `quarantine-noreply@ad.bls-isp.net`
     digest sitting in `Junk` on TCP `MockImapServer`.
   - Verifies `BODY.PEEK[HEADER]` scanning, legacy digest loop immunity (`items_included == 60`),
     50-item display cap + `+ 10 older quarantined messages` overflow banner, direct Dovecot
     Master-User IMAP `APPEND` into `INBOX` (0 `Junk` additions, 0 SMTP calls), `@bls-isp.net`
     sender domain (zero `ad.bls-isp.net`), `quoted-printable` wire lines, strict `html.parser`
     DOM tag balance with zero `<a>` links outside `<table>`, intact `quarantine_token` release,
     and Cockpit-only (`PORTAL_ENABLED=False`) linkless fallback.
2. External Connector & NS8 IMAP-Sync (`ns8-imapsync`) Failure Alerts:
   - Verifies multipart Plaintext + Carbon `#da1e28` HTML rendering, RFC 5322/3834 headers
     (`Date`, `Message-ID`, `Auto-Submitted`, `X-NS8-Mail-Toolbox`), direct `INBOX` `APPEND`
     when `admin_alert_email` matches a local mailbox alias, and automatic TCP SMTP delivery for
     external recipients or when IMAP `APPEND` fails.
"""

from __future__ import annotations

import email
from email.header import Header
from email.message import EmailMessage
import email.policy
from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from ldap3 import Connection, MOCK_SYNC, Server
import pytest

from app.backend.config import LdapSettings
from app.backend.connector_service import check_imapsync_bridge_alerts
from app.backend.ldap_client import set_ldap_connection_factory
from app.backend.main import app
from tests._mock_managesieve import MockManageSieveServer
from tests._mock_phase23_servers import (
    MockImapServer,
    MockPop3Server,
    MockSmtpSubmissionServer,
)

SMTP_POLICY = email.policy.SMTP
VOID_HTML_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)


class _StrictEmailDomInspector(HTMLParser):
    """Strict HTML5-subset tag balancer ensuring zero `<a>` links foster-parent outside `<table>`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.mismatches: list[str] = []
        self.links: list[dict[str, Any]] = []
        self.script_tags_seen: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "script":
            self.script_tags_seen += 1
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if t == "a":
            table_depth = sum(1 for open_t in self.stack if open_t == "table")
            td_depth = sum(1 for open_t in self.stack if open_t in {"td", "th"})
            self.links.append(
                {
                    "href": attr_map.get("href", ""),
                    "table_depth": table_depth,
                    "td_depth": td_depth,
                }
            )
        if t not in VOID_HTML_TAGS:
            self.stack.append(t)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in VOID_HTML_TAGS:
            return
        if not self.stack:
            self.mismatches.append(f"Unexpected closing </{t}> with empty stack")
            return
        top = self.stack.pop()
        if top != t:
            self.mismatches.append(f"Mismatched closing </{t}> for open <{top}>")


def _build_bls_samba_ad_server() -> Server:
    """Mock Samba AD directory where internal realm is `ad.bls-isp.net` while public mail alias is `@bls-isp.net`."""
    ad_base = "DC=ad,DC=bls-isp,DC=net"
    server = Server("mock_v106_bls_ad", get_info=None)
    conn = Connection(server, client_strategy=MOCK_SYNC)
    conn.bind()

    conn.strategy.add_entry(
        f"CN=ldapservice,CN=Users,{ad_base}",
        {
            "objectClass": ["top", "person", "user"],
            "cn": "ldapservice",
            "sAMAccountName": "ldapservice",
            "userPassword": "ad-bind-secret",
        },
    )
    conn.strategy.add_entry(
        f"CN=Genera Admin,CN=Users,{ad_base}",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "cn": "Genera Admin",
            "displayName": "Genera Admin",
            "sAMAccountName": "genera",
            "userPrincipalName": "genera@ad.bls-isp.net",
            # Samba AD default internal mail attribute before alias resolution!
            "mail": "genera@ad.bls-isp.net",
            "primaryGroupID": "513",
            "userPassword": "GeneraSecret!2026",
        },
    )
    conn.strategy.add_entry(
        f"CN=Cockpit User,CN=Users,{ad_base}",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "cn": "Cockpit User",
            "displayName": "Cockpit User",
            "sAMAccountName": "cockpitonly",
            "mail": "cockpitonly@bls-isp.net",
            "primaryGroupID": "513",
            "userPassword": "CockpitSecret!2026",
        },
    )
    conn.strategy.add_entry(
        f"CN=Domain Users,CN=Users,{ad_base}",
        {
            "objectClass": ["top", "group"],
            "cn": "Domain Users",
            "sAMAccountName": "Domain Users",
            "objectSid": "S-1-5-21-101010-202020-303030-513",
        },
    )
    conn.unbind()
    return server


@pytest.fixture()
def v106_ad_mail_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin up real TCP IMAP, POP3, SMTP, ManageSieve, and Samba AD (`ad.bls-isp.net`)."""
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    sieve_srv = MockManageSieveServer(master_user="vmail", master_password="dovecot-master-secret")
    imap_srv = MockImapServer(master_user="vmail", master_password="dovecot-master-secret")
    pop3_srv = MockPop3Server()
    smtp_srv = MockSmtpSubmissionServer()

    sieve_host, sieve_port = sieve_srv.start()
    _, imap_port = imap_srv.start()
    _, pop3_port = pop3_srv.start()
    _, smtp_port = smtp_srv.start()

    token = "v106-e2e-internal-token"
    (config_dir / "internal.token").write_text(f"{token}\n", encoding="utf-8")
    (config_dir / "master.creds").write_text("vmail:dovecot-master-secret\n", encoding="utf-8")
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
        "LDAP_BASE=DC=ad,DC=bls-isp,DC=net\n"
        "LDAP_USER=CN=ldapservice,CN=Users,DC=ad,DC=bls-isp,DC=net\n"
        "LDAP_PASS=ad-bind-secret\n"
        "LDAP_SCHEMA=ad\n"
        "LDAP_DOMAIN=ad.bls-isp.net\n",
        encoding="utf-8",
    )
    (config_dir / "mailboxes_cache.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-11-24T07:00:00Z",
                "mailboxes": [
                    {
                        "user": "genera",
                        "enabled": True,
                        "aliases": ["genera@bls-isp.net", "ops@bls-isp.net"],
                        "postfix_forwards": [],
                    },
                    {
                        "user": "cockpitonly",
                        "enabled": True,
                        "aliases": ["cockpitonly@bls-isp.net"],
                        "postfix_forwards": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("APP_SECRET_KEY", "v106-e2e-secret-key-0123456789abcdef")
    monkeypatch.setenv("PORTAL_ENABLED", "True")
    monkeypatch.setenv("PORTAL_HOSTNAME", "toolbox.bls-isp.net")
    monkeypatch.setenv("MILTER_ENABLED", "0")
    monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "3600")

    ad_server = _build_bls_samba_ad_server()

    def _ad_factory(_cfg: LdapSettings, bind_dn: str | None, bind_pw: str | None) -> Connection:
        return Connection(
            ad_server,
            user=bind_dn,
            password=bind_pw or "",
            client_strategy=MOCK_SYNC,
            auto_bind=False,
        )

    set_ldap_connection_factory(_ad_factory)

    try:
        yield {
            "config_dir": config_dir,
            "data_dir": data_dir,
            "headers": {"X-Internal-Token": token},
            "imap": imap_srv,
            "pop3": pop3_srv,
            "smtp": smtp_srv,
        }
    finally:
        set_ldap_connection_factory(None)
        imap_srv.stop()
        pop3_srv.stop()
        smtp_srv.stop()
        sieve_srv.stop()


def test_60_item_sogo_production_reproduction_digest_html_dom_and_inbox_append(
    v106_ad_mail_stack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce 60-item `genera@bls-isp.net` digest over TCP IMAP, verifying DOM integrity & loop protection."""
    hdrs = v106_ad_mail_stack["headers"]
    imap_srv: MockImapServer = v106_ad_mail_stack["imap"]
    smtp_srv: MockSmtpSubmissionServer = v106_ad_mail_stack["smtp"]

    # 1. Seed 1 legacy v1.0.5 Quarantine Digest that Rspamd previously routed into `Junk`
    legacy_digest = EmailMessage()
    legacy_digest["From"] = "quarantine-noreply@ad.bls-isp.net"
    legacy_digest["To"] = "genera@bls-isp.net"
    legacy_digest["Subject"] = "Spam Quarantine Digest (55 new message(s))"
    legacy_digest["Date"] = "Mon, 23 Nov 2026 07:30:00 +0000"
    legacy_digest["Message-ID"] = "<old-v105-digest@ad.bls-isp.net>"
    legacy_digest.set_content("Legacy digest trapped in Junk folder.\n")
    imap_srv.seed_message("genera", "Junk", legacy_digest.as_bytes(policy=SMTP_POLICY))

    # 2. Seed 60 real spam messages with unbroken 210-char strings, RFC-2047 UTF-8, and HTML injection chars
    for idx in range(1, 61):
        spam = EmailMessage()
        if idx == 59:
            spam["From"] = '"Evil </td></tr><table> <script>" <attacker-59@bad-sender.example>'
            spam["Subject"] = (
                "<script>alert('xss')</script> </td><table> "
                + ("UNBROKEN_TOKEN_" * 15)
            )
            spam["X-Spamd-Result"] = "default: True [14.80 / 6.00];"
        elif idx == 58:
            spam["From"] = "Sonderangebot Büro & Co. <promo58@intl.example>"
            spam["Subject"] = "Dringende Rückmeldung zur Überweisung #2026 — Bitte prüfen!"
            spam["X-Spam-Score"] = "7.2"
        else:
            spam["From"] = f"Bulk Mailer {idx:02d} <newsletter-{idx:02d}@very-long-campaign-host.example>"
            spam["Subject"] = f"Quarantined Campaign #{idx:02d} " + ("Z" * 195)
            if idx % 2 == 0:
                spam["X-Spam-Score"] = "9.6"

        spam["To"] = "genera@bls-isp.net"
        spam["Date"] = f"Tue, 24 Nov 2026 05:{(idx - 1) % 60:02d}:15 +0000"
        spam["Message-ID"] = f"<prod-repro-{idx:03d}@spam-source.example>"
        spam.set_content("Large spam body that BODY.PEEK[HEADER] does not need to transfer.\n")
        imap_srv.seed_message("genera", "Junk", spam.as_bytes(policy=SMTP_POLICY))

    assert len(imap_srv.list_folder_messages("genera", "Junk")) == 61
    smtp_srv.clear()

    with TestClient(app) as client:
        # Verify `/api/internal/quarantine` ignores the legacy digest in `Junk` -> lists 60 items (not 61!)
        q_overview = client.get("/api/internal/quarantine", headers=hdrs)
        assert q_overview.status_code == 200
        assert len(q_overview.json()["items"]) == 60

        # Run digest for `genera`
        run_res = client.post(
            "/api/internal/quarantine/run-digest",
            headers=hdrs,
            json={"user": "genera"},
        )
        assert run_res.status_code == 200
        assert run_res.json() == {
            "users_checked": 1,
            "digests_sent": 1,
            "items_included": 60,
        }

        # Confirm `BODY.PEEK[HEADER]` was used over TCP IMAP and 0 SMTP messages were sent!
        assert smtp_srv.get_messages() == []
        assert len(imap_srv.list_folder_messages("genera", "Junk")) == 61
        assert all(
            "HEADER" in spec.upper()
            for u, folder, spec in imap_srv.fetch_specs_log
            if u == "genera" and folder == "Junk"
        )

        inbox_items = imap_srv.list_folder_messages("genera", "INBOX")
        assert len(inbox_items) == 1
        raw_digest = inbox_items[0].raw_bytes

        # Every raw wire line MUST be short (`quoted-printable`, never 998-byte hard-split `8bit`)
        max_wire_len = max(len(line) for line in raw_digest.splitlines())
        assert max_wire_len < 300, f"Found unsafe wire line length: {max_wire_len}"

        parsed_digest = email.message_from_bytes(raw_digest, policy=email.policy.default)
        assert parsed_digest["From"] == "Mail Toolbox Quarantine <noreply@bls-isp.net>"
        assert parsed_digest["To"] == "genera@bls-isp.net"
        assert parsed_digest["Subject"] == "Spam Quarantine Digest (60 new message(s))"
        assert parsed_digest["Date"] is not None
        assert "@bls-isp.net>" in str(parsed_digest["Message-ID"])
        assert "ad.bls-isp.net" not in str(parsed_digest["From"])
        assert "ad.bls-isp.net" not in str(parsed_digest["To"])
        assert "ad.bls-isp.net" not in str(parsed_digest["Message-ID"])
        assert parsed_digest["Auto-Submitted"] == "auto-generated"
        assert parsed_digest["Precedence"] == "bulk"
        assert parsed_digest["X-Auto-Response-Suppress"] == "All"
        assert parsed_digest["X-NS8-Mail-Toolbox"] == "quarantine-digest"

        parts = list(parsed_digest.iter_parts())
        assert len(parts) == 2
        assert parts[0].get_content_type() == "text/plain"
        assert parts[1].get_content_type() == "text/html"
        assert parts[0]["Content-Transfer-Encoding"] == "quoted-printable"
        assert parts[1]["Content-Transfer-Encoding"] == "quoted-printable"

        plain_body = parts[0].get_content()
        html_body = parts[1].get_content()

        # Verify 50-item cap + `+ 10 older quarantined messages` banner in both parts
        assert "+ 10 older quarantined messages in your Spam folder" in html_body
        assert "+ 10 older quarantined messages in your Spam folder." in plain_body

        # Parse decoded HTML with strict tag-balancing DOM inspector
        dom = _StrictEmailDomInspector()
        dom.feed(html_body)
        dom.close()
        assert dom.mismatches == [], f"HTML tag balance errors: {dom.mismatches}"
        assert dom.stack == [], f"Unclosed HTML tags remaining: {dom.stack}"
        assert dom.script_tags_seen == 0, "Unescaped <script> tag found in digest HTML!"
        assert "&lt;script&gt;" in html_body

        # Every single `<a>` action button MUST sit cleanly inside `<table>` & `<td>` (zero foster-parenting!)
        assert len(dom.links) == 100  # 50 items * 2 action buttons
        for link in dom.links:
            assert link["table_depth"] >= 1, f"Link foster-parented outside <table>: {link}"
            assert link["td_depth"] >= 1, f"Link outside <td>: {link}"
            assert link["href"].startswith("https://toolbox.bls-isp.net/?quarantine_token=")
            qs = parse_qs(urlparse(link["href"]).query)
            assert "quarantine_token" in qs and len(qs["quarantine_token"][0]) >= 16
            assert qs.get("op") in (["release"], ["release_and_allow"])

        # Verify extracted token from the HTML DOM works end-to-end to release message from Junk -> INBOX!
        first_token = parse_qs(urlparse(dom.links[0]["href"]).query)["quarantine_token"][0]
        preview_res = client.get(f"/api/quarantine/token/{first_token}")
        assert preview_res.status_code == 200
        assert preview_res.json()["valid"] is True

        release_res = client.post(
            "/api/quarantine/action",
            json={"token": first_token, "op": "release"},
        )
        assert release_res.status_code == 200
        assert release_res.json()["delivered"] is True
        assert len(imap_srv.list_folder_messages("genera", "Junk")) == 60
        assert len(imap_srv.list_folder_messages("genera", "INBOX")) == 2

        # 3. Verify Cockpit-only deployment (`PORTAL_ENABLED=False`, `PORTAL_HOSTNAME=""`) omits broken links
        monkeypatch.setenv("PORTAL_ENABLED", "False")
        monkeypatch.setenv("PORTAL_HOSTNAME", "")
        cp_spam = EmailMessage()
        cp_spam["From"] = "ads@promo.example"
        cp_spam["To"] = "cockpitonly@bls-isp.net"
        cp_spam["Subject"] = "Cockpit-Only Mode Test"
        cp_spam["Message-ID"] = "<cockpit-only-1@promo.example>"
        cp_spam.set_content("Spam test.\n")
        imap_srv.seed_message("cockpitonly", "Junk", cp_spam.as_bytes(policy=SMTP_POLICY))

        cp_run = client.post(
            "/api/internal/quarantine/run-digest",
            headers=hdrs,
            json={"user": "cockpitonly"},
        )
        assert cp_run.status_code == 200
        assert cp_run.json()["digests_sent"] == 1
        cp_inbox = imap_srv.list_folder_messages("cockpitonly", "INBOX")
        assert len(cp_inbox) == 1
        cp_parsed = email.message_from_bytes(cp_inbox[0].raw_bytes, policy=email.policy.default)
        cp_html = list(cp_parsed.iter_parts())[1].get_content()
        assert "quarantine_token=" not in cp_html
        assert "localhost" not in cp_html
        assert "Webmail or IMAP" in cp_html


def test_connector_and_imapsync_failure_alerts_multipart_html_local_inbox_append_and_smtp_fallback(
    v106_ad_mail_stack: dict[str, Any],
) -> None:
    """Verify multipart HTML+Plaintext failure alerts via local INBOX APPEND, external SMTP, and APPEND fallback."""
    hdrs = v106_ad_mail_stack["headers"]
    config_dir: Path = v106_ad_mail_stack["config_dir"]
    imap_srv: MockImapServer = v106_ad_mail_stack["imap"]
    pop3_srv: MockPop3Server = v106_ad_mail_stack["pop3"]
    smtp_srv: MockSmtpSubmissionServer = v106_ad_mail_stack["smtp"]

    pop3_srv.add_account("broken@external-isp.example", "RightPass!1")
    pop3_srv.set_auth_failure("broken@external-isp.example", True)

    with TestClient(app) as client:
        # --------------------------------------------------------------------
        # Case A: `admin_alert_email` matches local mailbox alias `ops@bls-isp.net` (`genera`)
        #         -> Direct Dovecot Master-User IMAP APPEND into `genera`'s INBOX (0 SMTP!)
        # --------------------------------------------------------------------
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={"admin_alert_email": "ops@bls-isp.net"},
        )

        save_conn = client.post(
            "/api/internal/connectors",
            headers=hdrs,
            json={
                "id": None,
                "name": "ISP POP3 Collector",
                "target_user": "genera",
                "target_email": "genera@bls-isp.net",
                "protocol": "pop3",
                "host": "127.0.0.1",
                "port": pop3_srv.port,
                "use_ssl": False,
                "remote_user": "broken@external-isp.example",
                "remote_password": "WrongPassword!99",
                "interval_minutes": 5,
                "leave_on_server": True,
                "enabled": True,
            },
        )
        assert save_conn.status_code == 200
        cid = save_conn.json()["connector"]["id"]

        sync_fail = client.post(
            "/api/internal/connectors/sync",
            headers=hdrs,
            json={"id": cid},
        )
        assert sync_fail.status_code == 200
        assert sync_fail.json()["ok"] is False
        assert sync_fail.json()["alert_sent"] is True
        assert smtp_srv.get_messages() == []  # Delivered via IMAP APPEND, not SMTP!

        # Trigger coupled `ns8-imapsync` task failure alert to the same local `ops@bls-isp.net` alias
        (config_dir / "imapsync_cache.json").write_text(
            json.dumps(
                {
                    "installed": True,
                    "updated_at": "2026-11-24T07:45:00Z",
                    "instances": [
                        {
                            "module_id": "imapsync1",
                            "ui_name": "Migration Sync",
                            "node_id": 1,
                            "mail_server": "uuid-mail-1",
                            "mail_host": "10.5.4.1",
                            "coupled": True,
                            "tasks_loaded": True,
                            "tasks": [
                                {
                                    "task_id": "ims901",
                                    "localuser": "genera",
                                    "name": "legacy@oldhost.example -> genera",
                                    "remotehost": "imap.oldhost.example",
                                    "remoteport": 993,
                                    "remoteuser": "legacy@oldhost.example",
                                    "security": "ssl",
                                    "synchronization": "15m",
                                    "service_running": False,
                                    "sieve_enabled": True,
                                    "delete_local": False,
                                    "delete_remote": False,
                                    "delete_remote_older": 0,
                                    "last_sync_timestamp": 1763970000,
                                    "last_sync_exit_code": 11,
                                    "has_log": True,
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        assert check_imapsync_bridge_alerts() == 1
        assert smtp_srv.get_messages() == []  # Also delivered via IMAP APPEND!

        inbox_alerts = imap_srv.list_folder_messages("genera", "INBOX")
        assert len(inbox_alerts) == 2

        expected_categories = ["connector-alert", "imapsync-alert"]
        for raw_item, exp_cat in zip(inbox_alerts, expected_categories):
            msg = email.message_from_bytes(raw_item.raw_bytes, policy=email.policy.default)
            assert msg["From"] == "NS8 Mail Toolbox <noreply@bls-isp.net>"
            assert msg["To"] == "ops@bls-isp.net"
            assert msg["Date"] is not None
            assert "@bls-isp.net>" in str(msg["Message-ID"])
            assert "ad.bls-isp.net" not in str(msg["From"])
            assert msg["Auto-Submitted"] == "auto-generated"
            assert msg["X-NS8-Mail-Toolbox"] == exp_cat

            parts = list(msg.iter_parts())
            assert len(parts) == 2
            assert parts[0].get_content_type() == "text/plain"
            assert parts[1].get_content_type() == "text/html"
            assert parts[0]["Content-Transfer-Encoding"] == "quoted-printable"
            assert parts[1]["Content-Transfer-Encoding"] == "quoted-printable"

            html_alert = parts[1].get_content()
            assert "#da1e28" in html_alert
            dom = _StrictEmailDomInspector()
            dom.feed(html_alert)
            dom.close()
            assert dom.mismatches == []
            assert dom.stack == []

        # --------------------------------------------------------------------
        # Case B: External `admin_alert_email` (`noc@external-msp.example`) AND
        #         local IMAP APPEND failure fallback -> both deliver via TCP SMTP!
        # --------------------------------------------------------------------
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={"admin_alert_email": "noc@external-msp.example"},
        )
        # Clear imapsync alert state via recovery (`exit_code=0`) then trigger fresh failure (`exit_code=16`)
        for code in (0, 16):
            cache_doc = json.loads((config_dir / "imapsync_cache.json").read_text(encoding="utf-8"))
            cache_doc["instances"][0]["tasks"][0]["last_sync_exit_code"] = code
            (config_dir / "imapsync_cache.json").write_text(json.dumps(cache_doc), encoding="utf-8")
            check_imapsync_bridge_alerts()

        assert len(smtp_srv.get_messages()) == 1
        assert smtp_srv.get_messages()[0].rcpt_tos == ["noc@external-msp.example"]

        # Simulate Dovecot IMAP APPEND error on local user `genera` -> automatic SMTP fallback!
        client.put(
            "/api/internal/settings",
            headers=hdrs,
            json={"admin_alert_email": "ops@bls-isp.net"},
        )
        imap_srv.set_append_failure("genera", True)
        for code in (0, 22):
            cache_doc = json.loads((config_dir / "imapsync_cache.json").read_text(encoding="utf-8"))
            cache_doc["instances"][0]["tasks"][0]["last_sync_exit_code"] = code
            (config_dir / "imapsync_cache.json").write_text(json.dumps(cache_doc), encoding="utf-8")
            check_imapsync_bridge_alerts()

        assert len(smtp_srv.get_messages()) == 2
        assert smtp_srv.get_messages()[1].rcpt_tos == ["ops@bls-isp.net"]
