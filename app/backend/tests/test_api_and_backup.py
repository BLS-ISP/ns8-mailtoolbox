"""End-to-end FastAPI endpoint contract, static SPA serving, SQLite schema, and backup CLI tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient

from app.backend.backup_db import backup_database
from app.backend.db import init_db
from app.backend.main import app
from app.backend.tests.mock_managesieve import MockManageSieveServer


def test_full_portal_and_internal_api_contract(
    toolbox_env: dict[str, Path],
    sieve_server: MockManageSieveServer,
) -> None:
    sieve_server.seed_script(
        "max.muster",
        "sogo",
        'require ["fileinto"];\nfileinto "Vertrieb";\n',
        active=True,
    )

    internal_headers = {"X-Internal-Token": "internal-test-token-999"}

    with TestClient(app) as client:
        # 1. Health check
        r_health = client.get("/api/health")
        assert r_health.status_code == 200
        assert r_health.json() == {"status": "ok"}

        # 2. Internal endpoints reject missing/wrong X-Internal-Token
        assert client.get("/api/internal/mailboxes").status_code == 401
        assert (
            client.get("/api/internal/mailboxes", headers={"X-Internal-Token": "wrong"}).status_code
            == 401
        )
        # Portal endpoints reject unauthenticated requests
        assert client.get("/api/mailboxes").status_code == 401

        # 3. Seeded templates via internal API (English default first, German second)
        r_tpl = client.get("/api/internal/templates", headers=internal_headers)
        assert r_tpl.status_code == 200
        tpls = r_tpl.json()["templates"]
        assert [t["id"] for t in tpls] == ["default_en", "default_de"]
        assert tpls[0]["name"] == "Default Out-of-Office Notice (EN)"
        assert tpls[0]["is_default"] is True
        assert tpls[1]["name"] == "Standard Abwesenheitsnotiz (DE)"
        assert tpls[1]["is_default"] is False

        # 4. Login to Secretariat Portal as `anna`
        r_login = client.post(
            "/api/auth/login",
            json={"username": "anna", "password": "anna-password-123"},
        )
        assert r_login.status_code == 200

        # 5. GET /api/mailboxes merges LDAP + mailboxes_cache.json + ManageSieve + SQLite
        r_mbs = client.get("/api/mailboxes")
        assert r_mbs.status_code == 200
        data = r_mbs.json()
        assert data["summary"] == {
            "total": 3,
            "central_vacation_active": 0,
            "sogo_vacation_active": 0,
            "forwards_active": 1,  # `anna` has postfix_forwards: ["archiv@firma.de"]
        }
        max_entry = next(m for m in data["mailboxes"] if m["user"] == "max.muster")
        assert max_entry["display_name"] == "Max Mustermann"
        assert max_entry["given_name"] == "Max"
        assert max_entry["sn"] == "Mustermann"
        assert max_entry["mail"] == "max.muster@firma.de"
        assert "m.muster@firma.de" in max_entry["aliases"]
        assert max_entry["department"] == "Vertrieb"
        assert max_entry["title"] == "Projektleiter"
        assert max_entry["sogo_status"] == {
            "has_sogo_script": True,
            "is_active": True,
            "vacation_enabled": False,
            "forward_enabled": False,
            "forward_address": "",
        }

        # 6. Self-forwarding loop must be rejected with HTTP 400
        r_loop = client.put(
            "/api/mailboxes/max.muster/vacation",
            json={
                "enabled": True,
                "subject": "Loop",
                "body": "Test",
                "forward_enabled": True,
                "forward_address": "m.muster@firma.de",
                "keep_copy": True,
            },
        )
        assert r_loop.status_code == 400

        # 7. Portal PUT /api/mailboxes/{user}/vacation overrides `updated_by` with session user `anna`
        r_set = client.put(
            "/api/mailboxes/max.muster/vacation",
            json={
                "user": "max.muster",
                "enabled": True,
                "start_date": "2026-11-20",
                "end_date": "2026-11-27",
                "subject": "Abwesenheitsnotiz: Max Mustermann",
                "body": "Vielen Dank für Ihre Nachricht.",
                "forward_enabled": True,
                "forward_address": "vertretung@firma.de",
                "keep_copy": True,
                "updated_by": "spoofed-actor",
            },
        )
        assert r_set.status_code == 200
        updated_mb = r_set.json()
        assert updated_mb["vacation"]["enabled"] is True
        assert updated_mb["vacation"]["active_now"] is True
        assert updated_mb["vacation"]["updated_by"] == "anna"
        assert updated_mb["sogo_status"]["is_active"] is False

        # 8. Internal PUT /api/internal/mailboxes/{user}/vacation honors body `updated_by`
        r_int_set = client.put(
            "/api/internal/mailboxes/erika.vertretung/vacation",
            headers=internal_headers,
            json={
                "enabled": True,
                "start_date": None,
                "end_date": None,
                "subject": "Dienstreise",
                "body": "Heute auf Kundentermin.",
                "forward_enabled": False,
                "forward_address": "",
                "keep_copy": True,
                "updated_by": "cockpit-admin",
            },
        )
        assert r_int_set.status_code == 200
        assert r_int_set.json()["vacation"]["updated_by"] == "cockpit-admin"

        # 9. Update templates via PUT /api/internal/templates & verify via GET /api/templates
        new_templates = tpls + [
            {
                "id": "sick_short_de",
                "name": "Kurzfristige Krankmeldung (DE)",
                "lang": "de",
                "is_default": False,
                "subject": "Abwesenheit: {{displayName}}",
                "body": "Kurzfristig nicht erreichbar.",
            }
        ]
        r_put_tpl = client.put(
            "/api/internal/templates",
            headers=internal_headers,
            json={"templates": new_templates},
        )
        assert r_put_tpl.status_code == 200
        assert len(r_put_tpl.json()["templates"]) == 3
        assert len(client.get("/api/templates").json()["templates"]) == 3

        # 10. Trigger immediate reconcile via POST /api/internal/reconcile
        sieve_server.simulate_sogo_nightly_cron("max.muster")
        r_rec = client.post("/api/internal/reconcile", headers=internal_headers)
        assert r_rec.status_code == 200
        assert r_rec.json() == {"reconciled": 2, "expired": 0, "reactivated": 1}

        # 11. Verify Audit Log via both portal and internal endpoints
        r_audit_portal = client.get("/api/audit")
        r_audit_internal = client.get("/api/internal/audit", headers=internal_headers)
        assert r_audit_portal.status_code == 200
        assert r_audit_internal.status_code == 200
        entries = r_audit_portal.json()["entries"]
        sources = {e["source"] for e in entries}
        assert {"portal", "cockpit", "reconciler"}.issubset(sources)

        # 12. Static frontend serving & SPA fallback + unknown /api/* 404
        r_root = client.get("/")
        assert r_root.status_code == 200
        assert "NS8 Mail Toolbox" in r_root.text

        r_asset = client.get("/assets/app.js")
        assert r_asset.status_code == 200
        assert "console.log('portal')" in r_asset.text

        r_spa_route = client.get("/mailboxes/view")
        assert r_spa_route.status_code == 200
        assert "NS8 Mail Toolbox" in r_spa_route.text

        r_unknown_api = client.get("/api/does-not-exist")
        assert r_unknown_api.status_code == 404


def test_sqlite_phase2_phase3_tables_and_online_backup_cli(
    toolbox_env: dict[str, Path],
) -> None:
    db_file = init_db()
    conn = sqlite3.connect(str(db_file))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
    finally:
        conn.close()

    assert journal_mode == "wal"
    expected_tables = {
        "vacation_rules",
        "templates",
        "audit_log",
        "signature_templates",
        "signature_assignments",
        "quarantine_tokens",
    }
    assert expected_tables.issubset(tables)

    # Test programmatic backup_database() AND CLI `python -m app.backend.backup_db <dest>`
    snap_1 = toolbox_env["data_dir"] / "snap1.sqlite"
    backup_database(snap_1)
    assert snap_1.is_file()

    snap_2 = toolbox_env["data_dir"] / "toolbox-backup.sqlite"
    repo_root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [sys.executable, "-m", "app.backend.backup_db", str(snap_2)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert snap_2.is_file()

    snap_conn = sqlite3.connect(str(snap_2))
    try:
        cnt = snap_conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        assert cnt >= 2
    finally:
        snap_conn.close()
