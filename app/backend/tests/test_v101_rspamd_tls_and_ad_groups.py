"""v1.0.1 tests: Rspamd HTTPS self-signed TLS + Basic Auth, AD nested/primary groups & UPN auth."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import struct
import threading
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from ldap3 import Connection, MOCK_SYNC, Server
import pytest

from app.backend.config import LdapSettings
from app.backend.ldap_client import (
    _extract_rid_from_sid_value,
    set_ldap_connection_factory,
)
from app.backend.main import app
from app.backend.tracking_service import normalize_rspamd_base_url


def _make_self_signed_ssl_context(tmp_path: Path) -> ssl.SSLContext:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mail.firma.local")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "rspamd_cert.pem"
    key_path = tmp_path / "rspamd_key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


def test_rspamd_url_normalization_self_signed_tls_and_basic_auth(
    toolbox_env: dict[str, Path],
    tmp_path: Path,
) -> None:
    assert normalize_rspamd_base_url("https://mail.firma.de/rspamd/") == "https://mail.firma.de/rspamd"
    assert normalize_rspamd_base_url("https://mail.firma.de/rspamd/#history") == "https://mail.firma.de/rspamd"
    assert normalize_rspamd_base_url("https://mail.firma.de/rspamd/history?x=1") == "https://mail.firma.de/rspamd"
    assert normalize_rspamd_base_url("http://127.0.0.1:11334/") == "http://127.0.0.1:11334"

    expected_basic = "Basic " + base64.b64encode(b"admin:Nethesis,1234").decode("ascii")

    class HttpsTraefikRspamdHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            auth_hdr = self.headers.get("Authorization", "")
            if auth_hdr != expected_basic:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="NS8 Rspamd"')
                self.end_headers()
                return
            if self.path == "/rspamd/history":
                payload = {
                    "rows": [
                        {
                            "unix_time": 1763640000,
                            "sender_smtp": "ceo@kunde.de",
                            "rcpt_smtp": ["max.muster@firma.de"],
                            "subject": "Vertrag unterzeichnet",
                            "action": "no action",
                            "score": -2.1,
                            "required_score": 15.0,
                            "symbols": ["DKIM_SIGNED"],
                            "ip": "198.51.100.88",
                            "message-id": "<vertrag-1@kunde.de>",
                            "size": 9400,
                        }
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, _fmt: str, *_args: Any) -> None:
            pass

    ssl_ctx = _make_self_signed_ssl_context(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), HttpsTraefikRspamdHandler)
    httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    headers = {"X-Internal-Token": "internal-test-token-999"}
    rspamd_https_url = f"https://127.0.0.1:{port}/rspamd/#history"
    try:
        with TestClient(app) as client:
            # 1. Default rspamd_tls_verify in settings is False
            s0 = client.get("/api/internal/settings", headers=headers).json()
            assert s0["rspamd_tls_verify"] is False
            assert s0["rspamd_user"] == ""

            # 2. Strict TLS verification (rspamd_tls_verify=True) fails on self-signed cert
            r_strict = client.post(
                "/api/internal/mail-tracking/test-rspamd",
                headers=headers,
                json={
                    "rspamd_url": rspamd_https_url,
                    "rspamd_user": "admin",
                    "rspamd_password": "Nethesis,1234",
                    "rspamd_tls_verify": True,
                },
            )
            assert r_strict.status_code == 200
            assert r_strict.json()["ok"] is False

            # 3. Missing rspamd_user on 401 Basic Auth route gives clear actionable guidance
            r_no_user = client.post(
                "/api/internal/mail-tracking/test-rspamd",
                headers=headers,
                json={
                    "rspamd_url": rspamd_https_url,
                    "rspamd_user": "",
                    "rspamd_password": "Nethesis,1234",
                    "rspamd_tls_verify": False,
                },
            )
            assert r_no_user.json()["ok"] is False
            assert "HTTP Basic Auth" in r_no_user.json()["message"]

            # 4. Save rspamd_tls_verify=False + rspamd_user="admin" + URL with #history -> succeeds!
            r_put = client.put(
                "/api/internal/settings",
                headers=headers,
                json={
                    "rspamd_url": rspamd_https_url,
                    "rspamd_user": "admin",
                    "rspamd_password": "Nethesis,1234",
                    "rspamd_tls_verify": False,
                },
            )
            assert r_put.status_code == 200
            assert r_put.json()["rspamd_user"] == "admin"
            assert r_put.json()["rspamd_tls_verify"] is False

            r_ok = client.post(
                "/api/internal/mail-tracking/test-rspamd",
                headers=headers,
                json={},
            )
            assert r_ok.json()["ok"] is True
            assert r_ok.json()["entries_available"] == 1

            r_search = client.post(
                "/api/internal/mail-tracking/search",
                headers=headers,
                json={"sender": "ceo@kunde.de"},
            )
            assert r_search.status_code == 200
            assert r_search.json()["rspamd_connected"] is True
            assert r_search.json()["total"] == 1
            assert r_search.json()["entries"][0]["subject"] == "Vertrag unterzeichnet"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ad_nested_groups_primary_group_sid_upn_and_bulk_group_listing(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verify binary SID RID extraction (revision=1, subauth_count=5, RID=513 uint32le)
    binary_sid_513 = (
        struct.pack("<BB", 1, 5)
        + b"\x00\x00\x00\x00\x00\x05"
        + struct.pack("<IIIII", 21, 111, 222, 333, 513)
    )
    assert _extract_rid_from_sid_value(binary_sid_513) == "513"
    assert _extract_rid_from_sid_value("S-1-5-21-111-222-333-512") == "512"

    # First check OpenLDAP bulk groups on mailboxes (from default toolbox_env)
    internal_headers = {"X-Internal-Token": "internal-test-token-999"}
    with TestClient(app) as client:
        mbs_openldap = client.get("/api/internal/mailboxes", headers=internal_headers).json()["mailboxes"]
        anna_mb = next(m for m in mbs_openldap if m["user"] == "anna")
        assert "mail-office" in anna_mb["groups"]
        assert "employees" in anna_mb["groups"]

    # Now switch to Active Directory with 3-level nested groups, primaryGroupID=513, and custom UPN suffix
    config_dir = toolbox_env["config_dir"]
    (config_dir / "discovery_ldap.env").write_text(
        "LDAP_HOST=127.0.0.1\n"
        "LDAP_PORT=389\n"
        "LDAP_BASE=DC=ad,DC=internal,DC=local\n"
        "LDAP_USER=CN=ldapservice,CN=Users,DC=ad,DC=internal,DC=local\n"
        "LDAP_PASS=ad_svc_secret\n"
        "LDAP_SCHEMA=ad\n"
        "LDAP_DOMAIN=ad.internal.local\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLOWED_GROUPS", "MailOffice")

    ad_srv = Server("mock_nested_ad")
    c = Connection(
        ad_srv,
        user="CN=ldapservice,CN=Users,DC=ad,DC=internal,DC=local",
        password="ad_svc_secret",
        client_strategy=MOCK_SYNC,
    )
    c.bind()
    c.strategy.add_entry(
        "CN=ldapservice,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "person", "user"],
            "cn": "ldapservice",
            "sAMAccountName": "ldapservice",
            "userPassword": "ad_svc_secret",
        },
    )
    # User `a.login` with UPN `anna@corp.upn` (!= `ad.internal.local`) and `primaryGroupID: 513`
    c.strategy.add_entry(
        "CN=Anna Login,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "cn": "Anna Login",
            "displayName": "Anna Login",
            "givenName": "Anna",
            "sn": "Login",
            "sAMAccountName": "a.login",
            "userPrincipalName": "anna@corp.upn",
            "mail": "anna.login@firma.de",
            "primaryGroupID": "513",
            "memberOf": ["CN=SubTeam-Reception,CN=Users,DC=ad,DC=internal,DC=local"],
            "userPassword": "anna-upn-secret",
        },
    )
    # 3-level nested group chain: a.login -> SubTeam-Reception -> Office-Leads -> MailOffice
    c.strategy.add_entry(
        "CN=SubTeam-Reception,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "group"],
            "cn": "SubTeam-Reception",
            "sAMAccountName": "SubTeam-Reception",
            "description": "Reception Desk",
            "member": [
                "CN=Anna Login,CN=Users,DC=ad,DC=internal,DC=local",
                "CN=Office-Leads,CN=Users,DC=ad,DC=internal,DC=local",  # cycle test!
            ],
        },
    )
    c.strategy.add_entry(
        "CN=Office-Leads,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "group"],
            "cn": "Office-Leads",
            "sAMAccountName": "Office-Leads",
            "member": ["CN=SubTeam-Reception,CN=Users,DC=ad,DC=internal,DC=local"],
        },
    )
    c.strategy.add_entry(
        "CN=MailOffice,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "group"],
            "cn": "MailOffice",
            "sAMAccountName": "MailOffice",
            "description": "Authorized Secretariat Portal Group",
            "member": ["CN=Office-Leads,CN=Users,DC=ad,DC=internal,DC=local"],
        },
    )
    # Primary group RID 513 (`Domain Users`) via string SID + system plumbing groups
    c.strategy.add_entry(
        "CN=Domain Users,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "group"],
            "cn": "Domain Users",
            "sAMAccountName": "Domain Users",
            "objectSid": "S-1-5-21-111-222-333-513",
        },
    )
    c.strategy.add_entry(
        "CN=DnsAdmins,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "group"],
            "cn": "DnsAdmins",
            "sAMAccountName": "DnsAdmins",
            "description": "DNS Administrators",
        },
    )
    c.strategy.add_entry(
        "CN=Cert Publishers,CN=Users,DC=ad,DC=internal,DC=local",
        {
            "objectClass": ["top", "group"],
            "cn": "Cert Publishers",
            "sAMAccountName": "Cert Publishers",
        },
    )
    c.unbind()

    set_ldap_connection_factory(
        lambda _s, bind_dn, bind_pw: Connection(
            ad_srv,
            user=bind_dn,
            password=bind_pw or "",
            client_strategy=MOCK_SYNC,
            auto_bind=False,
        )
    )

    with TestClient(app) as client:
        # 1. Login via custom UPN `anna@corp.upn` succeeds via 3-level nested `MailOffice`!
        r_login = client.post(
            "/api/auth/login",
            json={"username": "anna@corp.upn", "password": "anna-upn-secret"},
        )
        assert r_login.status_code == 200
        login_data = r_login.json()
        assert login_data["username"] == "a.login"
        resolved = set(login_data["groups"])
        assert {"SubTeam-Reception", "Office-Leads", "MailOffice", "Domain Users"}.issubset(resolved)

        # 2. Verify GET /api/internal/groups classifies `is_system` and computes nested/primary `member_count`
        r_grps = client.get("/api/internal/groups", headers=internal_headers)
        assert r_grps.status_code == 200
        grp_map = {g["group"]: g for g in r_grps.json()["groups"]}

        assert grp_map["MailOffice"]["is_system"] is False
        assert grp_map["MailOffice"]["member_count"] == 1  # `a.login` via 3-level nesting!
        assert grp_map["Domain Users"]["is_system"] is False
        assert grp_map["Domain Users"]["member_count"] == 1  # `a.login` via primaryGroupID=513!
        assert grp_map["DnsAdmins"]["is_system"] is True
        assert grp_map["Cert Publishers"]["is_system"] is True

        # Non-system groups sorted before system groups
        first_system_idx = next(
            i for i, g in enumerate(r_grps.json()["groups"]) if g["is_system"]
        )
        last_regular_idx = max(
            i for i, g in enumerate(r_grps.json()["groups"]) if not g["is_system"]
        )
        assert last_regular_idx < first_system_idx
