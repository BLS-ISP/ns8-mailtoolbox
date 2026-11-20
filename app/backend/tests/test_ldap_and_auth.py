"""Tests for LDAP (rfc2307) & Active Directory (ad) bind auth, group checks, and rate limiting."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from ldap3 import Connection, MOCK_SYNC
import pytest

from app.backend.config import LdapSettings
from app.backend.ldap_client import list_directory_users, set_ldap_connection_factory
from app.backend.main import app
from app.backend.tests.conftest import build_ad_mock_server


def test_rfc2307_portal_login_allow_deny_and_session_lifecycle(
    toolbox_env: dict[str, Path],
) -> None:
    with TestClient(app) as client:
        # Unauthenticated /api/auth/me -> 401
        assert client.get("/api/auth/me").status_code == 401

        # Empty password rejected with 401 (RFC 4513 unauthenticated bind guard)
        res_empty = client.post("/api/auth/login", json={"username": "anna", "password": ""})
        assert res_empty.status_code == 401

        # Wrong password -> 401
        res_bad = client.post("/api/auth/login", json={"username": "anna", "password": "wrong"})
        assert res_bad.status_code == 401

        # Valid user NOT in ALLOWED_GROUPS -> 403
        res_forbidden = client.post(
            "/api/auth/login",
            json={"username": "max.muster", "password": "max-password-456"},
        )
        assert res_forbidden.status_code == 403

        # Valid user in `mail-office` -> 200 + HttpOnly cookie
        res_ok = client.post(
            "/api/auth/login",
            json={"username": "anna", "password": "anna-password-123"},
        )
        assert res_ok.status_code == 200
        body = res_ok.json()
        assert body["authenticated"] is True
        assert body["username"] == "anna"
        assert body["display_name"] == "Anna Sekretariat"
        assert "mail-office" in body["groups"]
        set_cookie_hdr = res_ok.headers.get("set-cookie", "").lower()
        assert "httponly" in set_cookie_hdr
        assert "samesite=lax" in set_cookie_hdr

        # Session cookie authenticates /api/auth/me
        res_me = client.get("/api/auth/me")
        assert res_me.status_code == 200
        assert res_me.json()["username"] == "anna"

        # Tampered cookie rejected with 401
        good_cookie = client.cookies.get("ns8_toolbox_session")
        assert good_cookie is not None
        client.cookies.set("ns8_toolbox_session", good_cookie[:-2] + "ZZ")
        assert client.get("/api/auth/me").status_code == 401

        # Restore cookie and test logout
        client.cookies.set("ns8_toolbox_session", good_cookie)
        assert client.post("/api/auth/logout").json() == {"ok": True}
        assert client.get("/api/auth/me").status_code == 401


def test_active_directory_schema_upn_and_memberof_auth(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = toolbox_env["config_dir"]
    (config_dir / "discovery_ldap.env").write_text(
        "LDAP_HOST=127.0.0.1\n"
        "LDAP_PORT=389\n"
        "LDAP_BASE=DC=corp,DC=local\n"
        "LDAP_USER=CN=ldapservice,CN=Users,DC=corp,DC=local\n"
        "LDAP_PASS=ad_svc_secret\n"
        "LDAP_SCHEMA=ad\n"
        "LDAP_DOMAIN=corp.local\n",
        encoding="utf-8",
    )
    # Test JSON array ALLOWED_GROUPS with case-insensitive group name matching
    monkeypatch.setenv("ALLOWED_GROUPS", '["sekretariat"]')

    ad_server = build_ad_mock_server()

    def _ad_factory(_s: LdapSettings, bind_dn: str | None, bind_pw: str | None) -> Connection:
        return Connection(
            ad_server,
            user=bind_dn,
            password=bind_pw or "",
            client_strategy=MOCK_SYNC,
            auto_bind=False,
        )

    set_ldap_connection_factory(_ad_factory)

    # Verify AD directory user listing skips computer accounts (`WORKSTATION01$`) and `ldapservice`
    users = list_directory_users()
    usernames = {u.user for u in users}
    assert usernames == {"clara.office", "bernd"}

    with TestClient(app) as client:
        # Login via AD userPrincipalName (`clara.office@corp.local`)
        res_clara = client.post(
            "/api/auth/login",
            json={"username": "clara.office@corp.local", "password": "clara-ad-pass"},
        )
        assert res_clara.status_code == 200
        assert res_clara.json() == {
            "authenticated": True,
            "username": "clara.office",
            "display_name": "Clara Office",
            "groups": ["Sekretariat"],
            "portal_tracking_enabled": False,
        }

        # Valid AD user in IT-Team (not Sekretariat) -> 403
        res_bernd = client.post(
            "/api/auth/login",
            json={"username": "CORP\\bernd", "password": "bernd-ad-pass"},
        )
        assert res_bernd.status_code == 403


def test_login_ip_rate_limiting(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX", "3")
    with TestClient(app) as client:
        for _ in range(3):
            r = client.post(
                "/api/auth/login",
                json={"username": "anna", "password": "bad-password"},
                headers={"X-Forwarded-For": "198.51.100.42"},
            )
            assert r.status_code == 401

        r_blocked = client.post(
            "/api/auth/login",
            json={"username": "anna", "password": "anna-password-123"},
            headers={"X-Forwarded-For": "198.51.100.42"},
        )
        assert r_blocked.status_code == 429

        # Different client IP is unaffected
        r_other_ip = client.post(
            "/api/auth/login",
            json={"username": "anna", "password": "anna-password-123"},
            headers={"X-Forwarded-For": "198.51.100.99"},
        )
        assert r_other_ip.status_code == 200
