"""Tests for rootless host-gateway translation, uninstall Sieve cleanup, and portal mail tracking."""

from __future__ import annotations

from pathlib import Path
import pytest

from fastapi.testclient import TestClient

from app.backend.config import get_settings, translate_loopback_host, translate_loopback_url
from app.backend.db import list_audit_entries, upsert_mail_history_rows
from app.backend.main import app
from app.backend.tests.mock_managesieve import MockManageSieveServer


def test_container_host_gateway_loopback_translation(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Without CONTAINER_HOST_GATEWAY, 127.0.0.1 stays untouched
    monkeypatch.delenv("CONTAINER_HOST_GATEWAY", raising=False)
    cfg_default = get_settings()
    assert cfg_default.ldap.host == "127.0.0.1"
    assert cfg_default.runtime.rspamd_url == "http://127.0.0.1:11334"
    assert cfg_default.runtime.effective_rspamd_url == "http://127.0.0.1:11334"

    # 2. With CONTAINER_HOST_GATEWAY=10.0.2.2, loopback targets map to 10.0.2.2 while saved URL stays clean
    monkeypatch.setenv("CONTAINER_HOST_GATEWAY", "10.0.2.2")
    cfg_gw = get_settings()
    assert cfg_gw.ldap.host == "10.0.2.2"
    assert cfg_gw.mail.host == "10.0.2.2"
    assert cfg_gw.runtime.rspamd_url == "http://127.0.0.1:11334"
    assert cfg_gw.runtime.effective_rspamd_url == "http://10.0.2.2:11334"

    # Non-loopback WireGuard/cluster IPs must remain untouched
    assert translate_loopback_host("10.5.4.1") == "10.5.4.1"
    assert translate_loopback_url("http://10.5.4.1:11334") == "http://10.5.4.1:11334"
    assert translate_loopback_url("http://localhost:11334/api") == "http://10.0.2.2:11334/api"


def test_deactivate_all_vacations_clean_uninstall_endpoint(
    toolbox_env: dict[str, Path],
    sieve_server: MockManageSieveServer,
) -> None:
    sogo_script = 'require ["fileinto"];\nfileinto "Customers";\n'
    sieve_server.seed_script("max.muster", "sogo", sogo_script, active=True)

    internal_headers = {"X-Internal-Token": "internal-test-token-999"}
    with TestClient(app) as client:
        # Unauthenticated call rejected
        assert client.post("/api/internal/deactivate-all-vacations").status_code == 401

        # Activate central vacations on max.muster (had sogo) and erika.vertretung (no prior script)
        r1 = client.put(
            "/api/internal/mailboxes/max.muster/vacation",
            headers=internal_headers,
            json={
                "enabled": True,
                "subject": "Abwesend",
                "body": "Nicht im Büro.",
                "forward_enabled": False,
                "keep_copy": True,
            },
        )
        assert r1.status_code == 200
        r2 = client.put(
            "/api/internal/mailboxes/erika.vertretung/vacation",
            headers=internal_headers,
            json={
                "enabled": True,
                "subject": "Urlaub",
                "body": "Im Urlaub.",
                "forward_enabled": False,
                "keep_copy": True,
            },
        )
        assert r2.status_code == 200

        assert sieve_server.get_mailbox_state("max.muster").active_script == "toolbox-vacation"
        assert sieve_server.get_mailbox_state("erika.vertretung").active_script == "toolbox-vacation"

        # Call uninstall cleanup endpoint
        r_clean = client.post(
            "/api/internal/deactivate-all-vacations",
            headers=internal_headers,
        )
        assert r_clean.status_code == 200
        assert r_clean.json() == {"ok": True, "deactivated": 2, "errors": []}

        # Verify Dovecot Sieve state restored cleanly
        max_after = sieve_server.get_mailbox_state("max.muster")
        assert max_after.active_script == "sogo"
        assert "toolbox-vacation" not in max_after.scripts
        assert max_after.scripts["sogo"] == sogo_script

        erika_after = sieve_server.get_mailbox_state("erika.vertretung")
        assert erika_after.active_script is None
        assert "toolbox-vacation" not in erika_after.scripts

        audits = list_audit_entries()
        assert any(a["action"] == "uninstall_cleanup" and a["source"] == "system" for a in audits)


def test_secretariat_portal_readonly_mail_tracking_toggle(
    toolbox_env: dict[str, Path],
) -> None:
    upsert_mail_history_rows(
        [
            {
                "id": "hist_portal_1",
                "timestamp": "2026-11-20T09:15:00Z",
                "unix_time": 1763630100.0,
                "sender": "angebot@kunde.de",
                "recipients": ["max.muster@firma.de"],
                "subject": "Angebot Projekt Nord",
                "status": "delivered",
                "rspamd_action": "no action",
                "score": -1.2,
                "required_score": 15.0,
                "symbols": ["DKIM_SIGNED"],
                "ip": "198.51.100.25",
                "message_id": "<ang-1@kunde.de>",
                "size_bytes": 5120,
            }
        ]
    )

    internal_headers = {"X-Internal-Token": "internal-test-token-999"}
    with TestClient(app) as client:
        # Unauthenticated portal tracking search -> 401
        assert client.post("/api/mail-tracking/search", json={}).status_code == 401

        # Login as secretariat user `anna`
        r_login = client.post(
            "/api/auth/login",
            json={"username": "anna", "password": "anna-password-123"},
        )
        assert r_login.status_code == 200
        assert r_login.json()["portal_tracking_enabled"] is False
        assert client.get("/api/auth/me").json()["portal_tracking_enabled"] is False

        # While portal_tracking_enabled == False -> HTTP 403
        r_denied = client.post("/api/mail-tracking/search", json={"sender": "kunde.de"})
        assert r_denied.status_code == 403

        # Admin enables portal_tracking_enabled via /api/internal/settings
        r_cfg = client.put(
            "/api/internal/settings",
            headers=internal_headers,
            json={"portal_tracking_enabled": True},
        )
        assert r_cfg.status_code == 200
        assert r_cfg.json()["portal_tracking_enabled"] is True

        # /api/auth/me immediately reflects portal_tracking_enabled == True
        assert client.get("/api/auth/me").json()["portal_tracking_enabled"] is True

        # Secretariat user can now search read-only mail history
        r_ok = client.post("/api/mail-tracking/search", json={"sender": "kunde.de"})
        assert r_ok.status_code == 200
        assert r_ok.json()["total"] == 1
        assert r_ok.json()["entries"][0]["subject"] == "Angebot Projekt Nord"
