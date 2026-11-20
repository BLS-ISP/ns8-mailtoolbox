"""v1.0.4 tests: NS8 IMAP-Sync (`ns8-imapsync`) Sync Bridge & Admin Failure Alerts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.backend.config import MailSettings
from app.backend.connector_service import check_imapsync_bridge_alerts, get_imapsync_bridge_status
from app.backend.db import list_audit_entries
from app.backend.main import app
from app.backend.quarantine_service import set_smtp_sender


def test_imapsync_bridge_status_missing_corrupt_and_populated_cache(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {"X-Internal-Token": "internal-test-token-999"}
    config_dir = toolbox_env["config_dir"]

    with TestClient(app) as client:
        # 1. Missing imapsync_cache.json -> safe empty bridge state
        r0 = client.get("/api/internal/connectors", headers=headers)
        assert r0.status_code == 200
        assert r0.json() == {
            "connectors": [],
            "imapsync_bridge": {
                "installed": False,
                "updated_at": None,
                "instances": [],
            },
        }

        # 2. Invalid JSON in imapsync_cache.json -> graceful fallback, never crashes
        (config_dir / "imapsync_cache.json").write_text("{corrupt-json", encoding="utf-8")
        assert get_imapsync_bridge_status() == {
            "installed": False,
            "updated_at": None,
            "instances": [],
        }

        # 3. Valid imapsync_cache.json (plus custom IMAPSYNC_CACHE_PATH env var)
        custom_cache = toolbox_env["data_dir"] / "custom_imapsync.json"
        monkeypatch.setenv("IMAPSYNC_CACHE_PATH", str(custom_cache))
        bridge_payload = {
            "installed": True,
            "updated_at": "2026-11-20T20:15:00Z",
            "instances": [
                {
                    "module_id": "imapsync1",
                    "ui_name": "Account Sync",
                    "node_id": 1,
                    "mail_server": "uuid-mail-1",
                    "mail_host": "10.5.4.1",
                    "coupled": True,
                    "tasks_loaded": True,
                    "tasks": [
                        {
                            "task_id": "task-uuid-1",
                            "localuser": "max.muster",
                            "name": "max@legacy.example.com -> max.muster",
                            "remotehost": "imap.legacy.example.com",
                            "remoteport": 993,
                            "remoteuser": "max@legacy.example.com",
                            "security": "ssl",
                            "synchronization": "30m",
                            "service_running": False,
                            "sieve_enabled": True,
                            "delete_local": False,
                            "delete_remote": False,
                            "delete_remote_older": 0,
                            "last_sync_timestamp": 1763665000,
                            "last_sync_exit_code": 0,
                            "has_log": True,
                        }
                    ],
                }
            ],
        }
        custom_cache.write_text(json.dumps(bridge_payload), encoding="utf-8")

        r1 = client.get("/api/internal/connectors", headers=headers)
        assert r1.status_code == 200
        assert r1.json()["imapsync_bridge"] == bridge_payload


def test_imapsync_bridge_failure_alert_cooldown_and_recovery(
    toolbox_env: dict[str, Path],
) -> None:
    sent_alerts: list[dict[str, Any]] = []

    def _smtp_capture(raw: bytes, mail_from: str, rcpt_to: str, _cfg: MailSettings) -> None:
        sent_alerts.append({"raw": raw, "from": mail_from, "to": rcpt_to})

    set_smtp_sender(_smtp_capture)

    headers = {"X-Internal-Token": "internal-test-token-999"}
    cache_path = toolbox_env["config_dir"] / "imapsync_cache.json"

    def _write_cache(coupled_exit_code: int | None, uncoupled_exit_code: int = 1) -> None:
        payload = {
            "installed": True,
            "updated_at": "2026-11-20T20:20:00Z",
            "instances": [
                {
                    "module_id": "imapsync1",
                    "ui_name": "ISP Migration",
                    "node_id": 1,
                    "mail_server": "uuid-mail-1",
                    "mail_host": "10.5.4.1",
                    "coupled": True,
                    "tasks_loaded": True,
                    "tasks": [
                        {
                            "task_id": "t-coupled-1",
                            "localuser": "max.muster",
                            "name": "max@old-isp.example.com -> max.muster",
                            "remotehost": "imap.old-isp.example.com",
                            "remoteport": 993,
                            "remoteuser": "max@old-isp.example.com",
                            "security": "ssl",
                            "synchronization": "15m",
                            "service_running": False,
                            "sieve_enabled": False,
                            "delete_local": False,
                            "delete_remote": False,
                            "delete_remote_older": 0,
                            "last_sync_timestamp": 1763665200,
                            "last_sync_exit_code": coupled_exit_code,
                            "has_log": True,
                        }
                    ],
                },
                {
                    "module_id": "imapsync2",
                    "ui_name": "Other Mail Server Sync",
                    "node_id": 2,
                    "mail_server": "uuid-other-mail",
                    "mail_host": "10.5.4.2",
                    "coupled": False,
                    "tasks_loaded": True,
                    "tasks": [
                        {
                            "task_id": "t-uncoupled-2",
                            "localuser": "other",
                            "name": "other@isp.example.com -> other",
                            "remotehost": "imap.isp.example.com",
                            "remoteport": 993,
                            "remoteuser": "other@isp.example.com",
                            "security": "ssl",
                            "synchronization": "15m",
                            "service_running": False,
                            "sieve_enabled": False,
                            "delete_local": False,
                            "delete_remote": False,
                            "delete_remote_older": 0,
                            "last_sync_timestamp": 1763665200,
                            "last_sync_exit_code": uncoupled_exit_code,
                            "has_log": True,
                        }
                    ],
                },
            ],
        }
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    with TestClient(app) as client:
        client.put(
            "/api/internal/settings",
            headers=headers,
            json={"admin_alert_email": "ops-alerts@example.com"},
        )

        # 1. Coupled task fails with exit code 11 -> 1 alert sent (uncoupled task ignored!)
        _write_cache(coupled_exit_code=11)
        assert check_imapsync_bridge_alerts() == 1
        assert len(sent_alerts) == 1
        assert sent_alerts[0]["to"] == "ops-alerts@example.com"
        raw_mail = sent_alerts[0]["raw"].decode("utf-8", errors="replace")
        assert "[NS8 Mail Toolbox] NS8 IMAP-Sync Task Failure Alert:" in raw_mail
        assert "imapsync1 (ISP Migration)" in raw_mail
        assert "Last Exit Code:   11" in raw_mail

        # 2. Immediate second check suppressed by 24-hour cooldown -> 0 alerts
        assert check_imapsync_bridge_alerts() == 0
        assert len(sent_alerts) == 1

        # 3. Task recovers (`last_sync_exit_code == 0`) -> resets alert state
        _write_cache(coupled_exit_code=0)
        assert check_imapsync_bridge_alerts() == 0

        # 4. Subsequent new failure alerts immediately again
        _write_cache(coupled_exit_code=16)
        assert check_imapsync_bridge_alerts() == 1
        assert len(sent_alerts) == 2

        audits = list_audit_entries(target_user="max.muster")
        assert any(
            a["action"] == "imapsync_failure_alert" and a["source"] == "imapsync_bridge"
            for a in audits
        )
