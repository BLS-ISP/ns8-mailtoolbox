"""Shared pytest fixtures for NS8 Mail Toolbox backend tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Iterator

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from ldap3 import Connection, MOCK_SYNC, Server
import pytest

from app.backend.auth import reset_rate_limiter
from app.backend.config import LdapSettings
from app.backend.connector_service import set_external_imap_factory, set_external_pop3_factory
from app.backend.ldap_client import set_ldap_connection_factory
from app.backend.quarantine_service import set_imap_master_factory, set_smtp_sender
from app.backend.tests.mock_managesieve import MockManageSieveServer


def build_rfc2307_mock_server() -> Server:
    """Build an in-memory ldap3 MOCK_SYNC server emulating NS8 OpenLDAP (rfc2307)."""
    server = Server("mock_openldap")
    conn = Connection(
        server,
        user="cn=ldapservice,dc=firma,dc=de",
        password="ldap_svc_secret",
        client_strategy=MOCK_SYNC,
    )
    conn.bind()

    conn.strategy.add_entry(
        "cn=ldapservice,dc=firma,dc=de",
        {
            "objectClass": ["simpleSecurityObject", "organizationalRole"],
            "cn": "ldapservice",
            "userPassword": "ldap_svc_secret",
        },
    )
    conn.strategy.add_entry(
        "uid=anna,ou=People,dc=firma,dc=de",
        {
            "objectClass": ["inetOrgPerson", "posixAccount"],
            "uid": "anna",
            "cn": "Anna Sekretariat",
            "displayName": "Anna Sekretariat",
            "givenName": "Anna",
            "sn": "Sekretariat",
            "mail": "anna@firma.de",
            "ou": "Empfang",
            "title": "Teamassistenz",
            "uidNumber": "1001",
            "gidNumber": "5000",
            "userPassword": "anna-password-123",
        },
    )
    conn.strategy.add_entry(
        "uid=max.muster,ou=People,dc=firma,dc=de",
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
            "uidNumber": "1002",
            "gidNumber": "5001",
            "userPassword": "max-password-456",
        },
    )
    conn.strategy.add_entry(
        "uid=erika.vertretung,ou=People,dc=firma,dc=de",
        {
            "objectClass": ["inetOrgPerson", "posixAccount"],
            "uid": "erika.vertretung",
            "cn": "Erika Vertretung",
            "displayName": "Erika Vertretung",
            "givenName": "Erika",
            "sn": "Vertretung",
            "mail": "vertretung@firma.de",
            "departmentNumber": "Vertrieb",
            "title": "Stellvertretung",
            "uidNumber": "1003",
            "gidNumber": "5001",
            "userPassword": "erika-password-789",
        },
    )
    conn.strategy.add_entry(
        "cn=mail-office,ou=Groups,dc=firma,dc=de",
        {
            "objectClass": ["posixGroup"],
            "cn": "mail-office",
            "gidNumber": "6001",
            "memberUid": ["anna"],
        },
    )
    conn.strategy.add_entry(
        "cn=employees,ou=Groups,dc=firma,dc=de",
        {
            "objectClass": ["posixGroup"],
            "cn": "employees",
            "gidNumber": "5001",
            "memberUid": ["anna", "max.muster", "erika.vertretung"],
        },
    )
    conn.unbind()
    return server


def build_ad_mock_server() -> Server:
    """Build an in-memory ldap3 MOCK_SYNC server emulating NS8 Samba Active Directory (ad)."""
    server = Server("mock_samba_ad")
    conn = Connection(
        server,
        user="CN=ldapservice,CN=Users,DC=corp,DC=local",
        password="ad_svc_secret",
        client_strategy=MOCK_SYNC,
    )
    conn.bind()

    conn.strategy.add_entry(
        "CN=ldapservice,CN=Users,DC=corp,DC=local",
        {
            "objectClass": ["top", "person", "user"],
            "cn": "ldapservice",
            "sAMAccountName": "ldapservice",
            "userPassword": "ad_svc_secret",
        },
    )
    conn.strategy.add_entry(
        "CN=Clara Office,CN=Users,DC=corp,DC=local",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "cn": "Clara Office",
            "displayName": "Clara Office",
            "givenName": "Clara",
            "sn": "Office",
            "sAMAccountName": "clara.office",
            "userPrincipalName": "clara.office@corp.local",
            "mail": "clara@corp.local",
            "department": "Sekretariat",
            "title": "Office Manager",
            "memberOf": ["CN=Sekretariat,CN=Users,DC=corp,DC=local"],
            "userPassword": "clara-ad-pass",
        },
    )
    conn.strategy.add_entry(
        "CN=Bernd Techniker,CN=Users,DC=corp,DC=local",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "cn": "Bernd Techniker",
            "displayName": "Bernd Techniker",
            "givenName": "Bernd",
            "sn": "Techniker",
            "sAMAccountName": "bernd",
            "userPrincipalName": "bernd@corp.local",
            "mail": "bernd@corp.local",
            "department": "IT",
            "title": "Sysadmin",
            "memberOf": ["CN=IT-Team,CN=Users,DC=corp,DC=local"],
            "userPassword": "bernd-ad-pass",
        },
    )
    conn.strategy.add_entry(
        "CN=WORKSTATION01$,CN=Computers,DC=corp,DC=local",
        {
            "objectClass": ["top", "person", "organizationalPerson", "user", "computer"],
            "cn": "WORKSTATION01",
            "sAMAccountName": "WORKSTATION01$",
            "userPassword": "machine-pass",
        },
    )
    conn.strategy.add_entry(
        "CN=Sekretariat,CN=Users,DC=corp,DC=local",
        {
            "objectClass": ["top", "group"],
            "cn": "Sekretariat",
            "sAMAccountName": "Sekretariat",
            "member": ["CN=Clara Office,CN=Users,DC=corp,DC=local"],
        },
    )
    conn.unbind()
    return server


@pytest.fixture()
def sieve_server() -> Iterator[MockManageSieveServer]:
    with MockManageSieveServer(master_user="vmail", master_password="dovecot-master-pass") as srv:
        yield srv


@pytest.fixture()
def toolbox_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sieve_server: MockManageSieveServer,
) -> Iterator[dict[str, Path]]:
    """Configure isolated DATA_DIR, CONFIG_DIR, mock LDAP (rfc2307), and mock ManageSieve."""
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    static_dir = tmp_path / "static"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><html><body><div id='app'>NS8 Mail Toolbox</div></body></html>\n",
        encoding="utf-8",
    )
    (static_dir / "assets").mkdir()
    (static_dir / "assets" / "app.js").write_text("console.log('portal');\n", encoding="utf-8")

    (config_dir / "discovery_mail.env").write_text(
        f"MAIL_HOST={sieve_server.host}\n"
        "MAIL_IMAP_PORT=143\n"
        f"MAIL_SIEVE_PORT={sieve_server.port}\n"
        "MAIL_SUBMISSION_PORT=587\n"
        "MAIL_MODULE_ID=mail1\n",
        encoding="utf-8",
    )
    (config_dir / "discovery_ldap.env").write_text(
        "LDAP_HOST=127.0.0.1\n"
        "LDAP_PORT=389\n"
        "LDAP_BASE=dc=firma,dc=de\n"
        "LDAP_USER=cn=ldapservice,dc=firma,dc=de\n"
        "LDAP_PASS=ldap_svc_secret\n"
        "LDAP_SCHEMA=rfc2307\n"
        "LDAP_DOMAIN=firma.de\n",
        encoding="utf-8",
    )
    (config_dir / "master.creds").write_text("vmail:dovecot-master-pass\n", encoding="utf-8")
    (config_dir / "internal.token").write_text("internal-test-token-999\n", encoding="utf-8")
    (config_dir / "mailboxes_cache.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-11-20T12:00:00Z",
                "mailboxes": [
                    {
                        "user": "max.muster",
                        "enabled": True,
                        "aliases": ["m.muster@firma.de", "vertrieb-leitung@firma.de"],
                        "postfix_forwards": [],
                    },
                    {
                        "user": "erika.vertretung",
                        "enabled": True,
                        "aliases": [],
                        "postfix_forwards": [],
                    },
                    {
                        "user": "anna",
                        "enabled": True,
                        "aliases": ["empfang@firma.de"],
                        "postfix_forwards": ["archiv@firma.de"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
    monkeypatch.setenv("ALLOWED_GROUPS", "mail-office,sekretariat")
    monkeypatch.setenv("PORTAL_HOSTNAME", "vertretung.firma.de")
    monkeypatch.setenv("TOOLBOX_TODAY_OVERRIDE", "2026-11-20")
    monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("MILTER_LISTEN_PORT", "0")

    mock_ldap_server = build_rfc2307_mock_server()

    def _factory(_settings: LdapSettings, bind_dn: str | None, bind_pw: str | None) -> Connection:
        return Connection(
            mock_ldap_server,
            user=bind_dn,
            password=bind_pw or "",
            client_strategy=MOCK_SYNC,
            auto_bind=False,
        )

    set_ldap_connection_factory(_factory)
    reset_rate_limiter()
    try:
        yield {"data_dir": data_dir, "config_dir": config_dir, "static_dir": static_dir}
    finally:
        set_ldap_connection_factory(None)
        set_imap_master_factory(None)
        set_smtp_sender(None)
        set_external_imap_factory(None)
        set_external_pop3_factory(None)
        reset_rate_limiter()
