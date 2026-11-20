"""End-to-End QA Smoke Suite for `ns8-mail-toolbox`.

Verifies the complete runtime stack:
- Built Vue 3 Secretariat Portal (`app/frontend/dist/index.html` + assets) served by FastAPI
- LDAP bind authentication + `ALLOWED_GROUPS` enforcement via `ldap3` MOCK_SYNC directory
- Real TCP RFC 5804 Dovecot 2.4 ManageSieve server (`user*vmail` SASL PLAIN Master-User auth)
- Template fetch & rendering -> Enable sick leave + copy forwarding -> Dovecot 2.4 Sieve verification
- SOGo `sogo.sieve` coexistence (both folder-filter inclusion AND double-vacation suppression)
- Nightly `00:00` SOGo cron override recovery via `/api/internal/reconcile`
- Disable sick leave -> `SETACTIVE "sogo"` restoration & `DELETESCRIPT "toolbox-vacation"`
- Audit log verification + online SQLite backup (`python -m app.backend.backup_db`)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from ldap3 import Connection, MOCK_SYNC, Server
import pytest

from app.backend.config import LdapSettings
from app.backend.ldap_client import set_ldap_connection_factory
from app.backend.main import app
from tests._mock_managesieve import MockManageSieveServer

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT_DIR / "app" / "frontend" / "dist"


def _build_e2e_ldap_server() -> Server:
    server = Server("mock_e2e_openldap", get_info=None)
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
            "uidNumber": 1003,
            "gidNumber": 2002,
            "homeDirectory": "/home/claudia.schmidt",
            "userPassword": "ClaudiaSecret!2026",
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
    conn.unbind()
    return server


def _render_portal_template(raw: str, ctx: dict[str, str]) -> str:
    """Render company template placeholders matching `app/frontend/src/utils/templateEngine.js`."""
    out = raw
    for key, val in ctx.items():
        block_re = re.compile(rf"\{{\{{#{key}\}}\}}([\s\S]*?)\{{\{{/{key}\}}\}}")
        out = block_re.sub(val and r"\1" or "", out)
    for key, val in ctx.items():
        out = out.replace(f"{{{{{key}}}}}", val)
    return out.strip()


def test_full_end_to_end_secretariat_portal_sogo_and_backup_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full E2E verification across static bundle, LDAP, ManageSieve, SOGo coexistence, reconciler, audit & backup."""
    assert (FRONTEND_DIST / "index.html").is_file(), (
        "Prebuilt `app/frontend/dist/index.html` must exist before running E2E smoke suite"
    )

    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    # 1. Start real TCP Dovecot 2.4 ManageSieve mock server
    sieve_srv = MockManageSieveServer(master_user="vmail", master_password="dovecot-master-secret")
    sieve_host, sieve_port = sieve_srv.start()

    try:
        # Seed max.muster with active SOGo script containing ONLY personal folder filter (no vacation)
        sogo_filters_only = (
            'require ["fileinto"];\n'
            'if header :contains "Subject" "[CRM]" {\n'
            '    fileinto "CRM";\n'
            '}\n'
        )
        sieve_srv.seed_script("max.muster", "sogo", sogo_filters_only, active=True)

        # Seed claudia.schmidt with active SOGo script containing personal vacation auto-reply
        sogo_with_vacation = (
            'require ["vacation"];\n'
            'vacation :days 7 :addresses ["claudia.schmidt@firma.de"] :subject "Urlaub" "Ich bin im Urlaub.";\n'
        )
        sieve_srv.seed_script("claudia.schmidt", "sogo", sogo_with_vacation, active=True)

        # 2. Write NS8 runtime discovery & cache files in CONFIG_DIR
        internal_token = "e2e-internal-token-998877"
        (config_dir / "discovery_mail.env").write_text(
            f"MAIL_HOST={sieve_host}\n"
            f"MAIL_IMAP_PORT=143\n"
            f"MAIL_SIEVE_PORT={sieve_port}\n"
            f"MAIL_SUBMISSION_PORT=587\n"
            f"MAIL_MODULE_ID=mail1\n",
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
                            "aliases": ["m.muster@firma.de", "vertrieb@firma.de"],
                            "postfix_forwards": [],
                        },
                        {
                            "user": "claudia.schmidt",
                            "enabled": True,
                            "aliases": ["buchhaltung@firma.de"],
                            "postfix_forwards": ["archiv@firma.de"],
                        },
                        {
                            "user": "anna.sekretariat",
                            "enabled": True,
                            "aliases": ["sekretariat@firma.de"],
                            "postfix_forwards": [],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONFIG_DIR", str(config_dir))
        monkeypatch.setenv("STATIC_DIR", str(FRONTEND_DIST))
        monkeypatch.setenv("APP_SECRET_KEY", "e2e-super-secret-signing-key-0123456789abcdef")
        monkeypatch.setenv("ALLOWED_GROUPS", "mail-office,sekretariat")
        monkeypatch.setenv("PORTAL_HOSTNAME", "vertretung.firma.de")
        monkeypatch.setenv("TOOLBOX_TODAY_OVERRIDE", "2026-11-20")
        monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "3600")

        ldap_server = _build_e2e_ldap_server()

        def _ldap_factory(_cfg: LdapSettings, bind_dn: str | None, bind_pw: str | None) -> Connection:
            return Connection(
                ldap_server,
                user=bind_dn,
                password=bind_pw or "",
                client_strategy=MOCK_SYNC,
                auto_bind=False,
            )

        set_ldap_connection_factory(_ldap_factory)

        with TestClient(app) as client:
            # 3. Verify mounted built Vue 3 frontend bundle + SPA fallback + /api 404
            assert client.get("/api/health").json() == {"status": "ok"}
            root_html = client.get("/")
            assert root_html.status_code == 200
            assert 'id="app"' in root_html.text
            spa_fallback = client.get("/dashboard/mailboxes")
            assert spa_fallback.status_code == 200
            assert 'id="app"' in spa_fallback.text
            assert client.get("/api/does-not-exist").status_code == 404

            # Verify built JS/CSS asset from index.html is served with 200
            asset_match = re.search(r'(?:src|href)="\.?(/assets/[^"]+)"', root_html.text)
            assert asset_match, "Expected Vite asset reference in app/frontend/dist/index.html"
            asset_resp = client.get(asset_match.group(1))
            assert asset_resp.status_code == 200
            assert len(asset_resp.content) > 100

            # 4. Portal Authentication (401 bad creds, 403 non-secretariat user, 200 secretariat user)
            assert client.get("/api/auth/me").status_code == 401
            assert (
                client.post(
                    "/api/auth/login",
                    json={"username": "anna.sekretariat", "password": "wrong-password"},
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/auth/login",
                    json={"username": "max.muster", "password": "MaxSecret!2026"},
                ).status_code
                == 403
            )

            login_res = client.post(
                "/api/auth/login",
                json={"username": "anna.sekretariat", "password": "AnnaSecret!2026"},
            )
            assert login_res.status_code == 200
            assert login_res.json()["username"] == "anna.sekretariat"
            assert "mail-office" in login_res.json()["groups"]

            # 5. List Mailboxes & Verify LDAP + Cache + SOGo Status Merge
            mb_res = client.get("/api/mailboxes")
            assert mb_res.status_code == 200
            mb_payload = mb_res.json()
            assert mb_payload["summary"] == {
                "total": 3,
                "central_vacation_active": 0,
                "sogo_vacation_active": 1,
                "forwards_active": 1,  # claudia.schmidt postfix_forwards
            }
            by_user = {m["user"]: m for m in mb_payload["mailboxes"]}
            assert by_user["max.muster"]["display_name"] == "Max Mustermann"
            assert by_user["max.muster"]["department"] == "Vertrieb"
            assert set(by_user["max.muster"]["aliases"]) == {
                "m.muster@firma.de",
                "vertrieb@firma.de",
            }
            assert by_user["max.muster"]["sogo_status"] == {
                "has_sogo_script": True,
                "is_active": True,
                "vacation_enabled": False,
                "forward_enabled": False,
                "forward_address": "",
            }
            assert by_user["claudia.schmidt"]["sogo_status"]["vacation_enabled"] is True

            # Verify Master-User SASL PLAIN login (<user>*vmail) occurred over TCP
            assert ("max.muster", "vmail") in sieve_srv.auth_log

            # 6. Fetch Templates & Render German Default Template
            tpl_res = client.get("/api/templates")
            assert tpl_res.status_code == 200
            templates = tpl_res.json()["templates"]
            de_tpl = next(t for t in templates if t["id"] == "default_de")
            ctx = {
                "displayName": "Max Mustermann",
                "givenName": "Max",
                "sn": "Mustermann",
                "mail": "max.muster@firma.de",
                "startDate": "20.11.2026",
                "endDate": "27.11.2026",
                "deputyEmail": "anna.sekretariat@firma.de",
            }
            rendered_subject = _render_portal_template(de_tpl["subject"], ctx)
            rendered_body = _render_portal_template(de_tpl["body"], ctx)
            assert "Max Mustermann" in rendered_subject
            assert "27.11.2026" in rendered_body
            assert "anna.sekretariat@firma.de" in rendered_body

            # 7. Enable Sick Leave + Copy Forwarding on max.muster (SOGo has filters only -> include "sogo")
            enable_max = client.put(
                "/api/mailboxes/max.muster/vacation",
                json={
                    "enabled": True,
                    "start_date": "2026-11-20",
                    "end_date": "2026-11-27",
                    "subject": rendered_subject,
                    "body": rendered_body,
                    "forward_enabled": True,
                    "forward_address": "anna.sekretariat@firma.de",
                    "keep_copy": True,
                },
            )
            assert enable_max.status_code == 200
            updated_max = enable_max.json()
            assert updated_max["vacation"]["enabled"] is True
            assert updated_max["vacation"]["active_now"] is True
            assert updated_max["vacation"]["updated_by"] == "anna.sekretariat"

            max_box = sieve_srv.get_user_box("max.muster")
            assert max_box.active == "toolbox-vacation"
            assert max_box.scripts["sogo"] == sogo_filters_only  # Never overwritten!
            gen_max_sieve = max_box.scripts["toolbox-vacation"]
            assert any(u == "max.muster" for u, _ in sieve_srv.checkscript_calls)
            assert 'require ["vacation", "date", "relational", "copy", "include"];' in gen_max_sieve
            assert (
                'if allof (currentdate :value "ge" "date" "2026-11-20", '
                'currentdate :value "le" "date" "2026-11-27")' in gen_max_sieve
            )
            for addr in ("max.muster@firma.de", "m.muster@firma.de", "vertrieb@firma.de"):
                assert f'"{addr}"' in gen_max_sieve
            assert 'redirect :copy "anna.sekretariat@firma.de";' in gen_max_sieve
            assert 'include :personal "sogo";' in gen_max_sieve

            # 8. Enable Sick Leave on claudia.schmidt (SOGo HAS vacation -> omit include "sogo" to prevent double reply!)
            enable_claudia = client.put(
                "/api/mailboxes/claudia.schmidt/vacation",
                json={
                    "enabled": True,
                    "start_date": "",
                    "end_date": "",
                    "subject": "Abwesenheitsnotiz: Claudia Schmidt",
                    "body": "Ich bin krankheitsbedingt abwesend.",
                    "forward_enabled": True,
                    "forward_address": "anna.sekretariat@firma.de",
                    "keep_copy": False,
                },
            )
            assert enable_claudia.status_code == 200
            claudia_box = sieve_srv.get_user_box("claudia.schmidt")
            assert claudia_box.active == "toolbox-vacation"
            assert claudia_box.scripts["sogo"] == sogo_with_vacation  # Preserved untouched!
            gen_claudia_sieve = claudia_box.scripts["toolbox-vacation"]
            assert 'include :personal "sogo";' not in gen_claudia_sieve
            assert '"include"' not in gen_claudia_sieve
            assert 'redirect "anna.sekretariat@firma.de";' in gen_claudia_sieve
            assert "stop;" in gen_claudia_sieve

            # 9. Simulate SOGo nightly 00:00 cron (`sogo-tool update-autoreply`) hijacking active script
            sieve_srv.seed_script("max.muster", "sogo", sogo_filters_only, active=True)
            assert sieve_srv.get_user_box("max.muster").active == "sogo"

            reconcile_res = client.post(
                "/api/internal/reconcile",
                headers={"X-Internal-Token": internal_token},
            )
            assert reconcile_res.status_code == 200
            assert reconcile_res.json()["reactivated"] >= 1
            assert sieve_srv.get_user_box("max.muster").active == "toolbox-vacation"

            # 10. Disable Sick Leave on max.muster -> Restores SETACTIVE "sogo" & deletes "toolbox-vacation"
            disable_max = client.put(
                "/api/mailboxes/max.muster/vacation",
                json={
                    "enabled": False,
                    "start_date": "",
                    "end_date": "",
                    "subject": rendered_subject,
                    "body": rendered_body,
                    "forward_enabled": False,
                    "forward_address": "",
                    "keep_copy": True,
                },
            )
            assert disable_max.status_code == 200
            assert disable_max.json()["vacation"]["enabled"] is False
            max_box_after = sieve_srv.get_user_box("max.muster")
            assert max_box_after.active == "sogo"
            assert "toolbox-vacation" not in max_box_after.scripts
            assert max_box_after.scripts["sogo"] == sogo_filters_only

            # 11. Verify Audit Log via both Portal and Internal NS8 Cockpit API
            audit_res = client.get("/api/audit")
            assert audit_res.status_code == 200
            actions_logged = {e["action"] for e in audit_res.json()["entries"]}
            for expected_act in (
                "login_failed",
                "login_forbidden",
                "login_success",
                "vacation_enabled",
                "vacation_disabled",
            ):
                assert expected_act in actions_logged, f"Missing `{expected_act}` in audit log: {actions_logged}"

            internal_audit = client.get(
                "/api/internal/audit",
                headers={"X-Internal-Token": internal_token},
            )
            assert internal_audit.status_code == 200
            assert len(internal_audit.json()["entries"]) == len(audit_res.json()["entries"])

            # 12. Verify Online SQLite Backup CLI (`python -m app.backend.backup_db`)
            backup_file = data_dir / "toolbox-backup.sqlite"
            proc = subprocess.run(
                [sys.executable, "-m", "app.backend.backup_db", str(backup_file)],
                cwd=str(ROOT_DIR),
                env={**os.environ, "DATA_DIR": str(data_dir), "CONFIG_DIR": str(config_dir)},
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0, f"backup_db CLI failed: {proc.stderr}"
            assert backup_file.is_file()

            with sqlite3.connect(str(backup_file)) as bconn:
                integrity = bconn.execute("PRAGMA integrity_check;").fetchone()[0]
                assert integrity == "ok"
                tables = {
                    r[0]
                    for r in bconn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                for required_tbl in (
                    "vacation_rules",
                    "templates",
                    "audit_log",
                    "signature_templates",
                    "signature_assignments",
                    "quarantine_tokens",
                ):
                    assert required_tbl in tables, f"Missing table `{required_tbl}` in backup DB"
                audit_cnt = bconn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
                assert audit_cnt >= 5

    finally:
        set_ldap_connection_factory(None)
        sieve_srv.stop()
