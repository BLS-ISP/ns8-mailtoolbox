"""End-to-End Integration Tests for v1.0.5 NS8 Notification Silencing & Smart Cache TTLs.

Verifies:
1. `imageroot/bin/sync-mailboxes-cache` `--min-age 300` & `--skip-imapsync`:
   - 0 `agent.tasks.run` invocations when cache is `< 300`s old
   - `extra={"isNotificationHidden": True}` on every `agent.tasks.run` when cache is stale or forced
   - `only_coupled=True` skipping uncoupled `imapsync` instances
2. `list-mailboxes/20read`, `list-connectors/20read`, and `get-configuration/20read` cache-hit
   (`0` child `agent.tasks.run` calls!) vs `force_refresh: true` and `discover-mail-instance/20read`
   live refresh (`extra={"isNotificationHidden": True}` passed), validated against Draft-07 schemas.
"""

from __future__ import annotations

import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import types
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import jsonschema
import pytest

from app.backend.main import app

ROOT_DIR = Path(__file__).resolve().parent.parent
IMAGEROOT_DIR = ROOT_DIR / "imageroot"
ACTIONS_DIR = IMAGEROOT_DIR / "actions"
BIN_DIR = IMAGEROOT_DIR / "bin"


class _MockRedis:
    """In-memory Redis stub for NS8 cluster & module keys."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.strings: dict[str, bytes] = {}

    def set_hash(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes[key] = {
            k.encode("utf-8"): v.encode("utf-8") for k, v in mapping.items()
        }

    def set_str(self, key: str, value: str) -> None:
        self.strings[key] = value.encode("utf-8")

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        return dict(self.hashes.get(key, {}))

    def hget(self, key: str, field: str) -> bytes | None:
        return self.hashes.get(key, {}).get(field.encode("utf-8"))

    def get(self, key: str) -> bytes | None:
        return self.strings.get(key)

    def scan_iter(self, pattern: str) -> list[bytes]:
        rx = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
        all_keys = set(self.hashes.keys()) | set(self.strings.keys())
        return [k.encode("utf-8") for k in sorted(all_keys) if rx.match(k)]


def _load_script_module(module_name: str, script_path: Path) -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(module_name, str(script_path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install_recording_agent(
    monkeypatch: pytest.MonkeyPatch,
    mock_rdb: _MockRedis,
    recorded_tasks: list[dict[str, Any]],
    mail_providers: list[dict[str, Any]] | None = None,
    acl_subjects: list[dict[str, Any]] | None = None,
) -> None:
    agent_mod = types.ModuleType("agent")
    tasks_mod = types.ModuleType("agent.tasks")
    ldapproxy_mod = types.ModuleType("agent.ldapproxy")

    class _FakeLdapproxy:
        def get_domain(self, _domain: str) -> dict[str, Any] | None:
            return None

    def _redis_connect(*_args: Any, **_kwargs: Any) -> _MockRedis:
        return mock_rdb

    def _list_service_providers(
        _rdb: Any, service: str, proto: str, *_args: Any, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        if service == "imap" and proto == "tcp":
            return list(mail_providers or [])
        return []

    def _run_task(agent_id: str, action: str, **kwargs: Any) -> dict[str, Any]:
        recorded_tasks.append(
            {
                "agent_id": agent_id,
                "action": action,
                "extra": kwargs.get("extra"),
                "data": kwargs.get("data"),
            }
        )
        if action == "list-user-mailboxes":
            return {
                "exit_code": 0,
                "output": {
                    "user_mailboxes": [
                        {"user": "alice", "enabled": True, "aliases": ["alice@example.com"]},
                        {"user": "bob", "enabled": True, "aliases": ["bob@example.com"]},
                    ]
                },
            }
        if action == "list-addresses":
            return {"exit_code": 0, "output": {"addresses": [], "domains": ["example.com"]}}
        if action == "list-tasks" and agent_id == "module/imapsync1":
            return {
                "exit_code": 0,
                "output": {
                    "user_properties": [
                        {
                            "task_id": "t10001",
                            "localuser": "alice",
                            "remotehostname": "imap.isp.example.com",
                            "remoteusername": "alice@isp.example.com",
                            "remoteport": "993",
                            "security": "ssl",
                            "cron": "15m",
                            "service_running": False,
                            "sieve_enabled": True,
                            "delete_local": False,
                            "delete_remote": False,
                            "delete_remote_older": 0,
                            "last_sync_timestamp": 1763668000,
                            "last_sync_exit_code": 0,
                            "has_log": True,
                        }
                    ]
                },
            }
        if action == "get-filter-configuration":
            return {
                "exit_code": 0,
                "output": {"antispam": {"rspamd_url": "https://mail.example.com/rspamd"}},
            }
        if action == "list-acl-subjects":
            return {
                "exit_code": 0,
                "output": {
                    "subjects": list(
                        acl_subjects
                        or [{"dtype": "group", "name": "mail-office", "ui_name": "Mail Office"}]
                    )
                },
            }
        return {"exit_code": 0, "output": {}}

    agent_mod.redis_connect = _redis_connect  # type: ignore[attr-defined]
    agent_mod.list_service_providers = _list_service_providers  # type: ignore[attr-defined]
    tasks_mod.run = _run_task  # type: ignore[attr-defined]
    agent_mod.tasks = tasks_mod  # type: ignore[attr-defined]
    ldapproxy_mod.Ldapproxy = _FakeLdapproxy  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "agent.tasks", tasks_mod)
    monkeypatch.setitem(sys.modules, "agent.ldapproxy", ldapproxy_mod)


def _start_fastapi_http_bridge(client: TestClient) -> http.server.ThreadingHTTPServer:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hdrs = {
                k: v
                for k, v in self.headers.items()
                if k.lower() in {"accept", "x-internal-token"}
            }
            resp = client.get(self.path, headers=hdrs)
            body = resp.content
            self.send_response(resp.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_sync_mailboxes_cache_min_age_skip_imapsync_only_coupled_and_hidden_extra(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `--min-age 300`, `--skip-imapsync`, `only_coupled=True`, and `isNotificationHidden: True`."""
    state_dir = tmp_path / "state"
    config_dir = state_dir / "config"
    config_dir.mkdir(parents=True)

    mail_uuid = "11111111-2222-3333-4444-555555555555"
    other_uuid = "99999999-8888-7777-6666-555555555555"
    (state_dir / "environment").write_text(
        f"MAIL_MODULE_UUID={mail_uuid}\nMAIL_MODULE_ID=mail1\n",
        encoding="utf-8",
    )

    mock_rdb = _MockRedis()
    mock_rdb.set_hash(
        "cluster/module_node",
        {"mail1": "1", "mail-toolbox1": "1", "imapsync1": "1", "imapsync2": "2"},
    )
    mock_rdb.set_hash(
        "module/imapsync1/environment",
        {"MAIL_SERVER": mail_uuid, "MAIL_HOST": "10.5.4.1", "NODE_ID": "1"},
    )
    mock_rdb.set_str("module/imapsync1/ui_name", "Coupled Sync")
    mock_rdb.set_hash(
        "module/imapsync2/environment",
        {"MAIL_SERVER": other_uuid, "MAIL_HOST": "10.5.4.2", "NODE_ID": "2"},
    )
    mock_rdb.set_str("module/imapsync2/ui_name", "Uncoupled Sync")

    recorded: list[dict[str, Any]] = []
    _install_recording_agent(monkeypatch, mock_rdb, recorded)

    monkeypatch.delitem(sys.modules, "toolbox_imapsync", raising=False)
    monkeypatch.syspath_prepend(str(BIN_DIR))
    _load_script_module("toolbox_imapsync", BIN_DIR / "toolbox_imapsync.py")
    sync_mod = _load_script_module("sync_mailboxes_cache_v105", BIN_DIR / "sync-mailboxes-cache")

    monkeypatch.setenv("AGENT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("MAIL_MODULE_UUID", mail_uuid)
    monkeypatch.setenv("MAIL_MODULE_ID", "mail1")

    # 1. Full background sync (`main([])` — timer mode):
    #    Queries coupled `imapsync1/list-tasks`, `mail1/list-user-mailboxes`, `mail1/list-addresses`
    #    (NEVER uncoupled `imapsync2`!), all with `extra={'isNotificationHidden': True}`!
    assert sync_mod.main([]) == 0
    assert [(c["agent_id"], c["action"]) for c in recorded] == [
        ("module/imapsync1", "list-tasks"),
        ("module/mail1", "list-user-mailboxes"),
        ("module/mail1", "list-addresses"),
    ]
    assert all(c["extra"] == {"isNotificationHidden": True} for c in recorded)
    assert not any(c["agent_id"] == "module/imapsync2" for c in recorded)

    imapsync_cached = json.loads((config_dir / "imapsync_cache.json").read_text(encoding="utf-8"))
    by_id = {i["module_id"]: i for i in imapsync_cached["instances"]}
    assert by_id["imapsync1"]["coupled"] is True
    assert by_id["imapsync1"]["tasks_loaded"] is True
    assert by_id["imapsync2"]["coupled"] is False
    assert by_id["imapsync2"]["tasks_loaded"] is False

    # 2. Fresh cache (`< 300`s old) with `--min-age 300 --skip-imapsync` -> ZERO `agent.tasks.run` calls!
    recorded.clear()
    assert sync_mod.main(["--min-age", "300", "--skip-imapsync"]) == 0
    assert recorded == []

    # 3. Fresh cache (`< 300`s old) with `--min-age 300` (even without `--skip-imapsync`) -> ZERO `agent.tasks.run` calls!
    recorded.clear()
    assert sync_mod.main(["--min-age", "300"]) == 0
    assert recorded == []

    # 4. Forced mailbox refresh (`--skip-imapsync`, no `--min-age`) -> refreshes mailboxes silently, 0 imapsync calls!
    recorded.clear()
    assert sync_mod.main(["--skip-imapsync"]) == 0
    assert [(c["agent_id"], c["action"]) for c in recorded] == [
        ("module/mail1", "list-user-mailboxes"),
        ("module/mail1", "list-addresses"),
    ]
    assert all(c["extra"] == {"isNotificationHidden": True} for c in recorded)

    # 5. Backdate cache `mtime` past 300s (`305`s ago) -> `--min-age 300` refreshes silently!
    stale_ts = time.time() - 305
    os.utime(config_dir / "mailboxes_cache.json", (stale_ts, stale_ts))
    os.utime(config_dir / "imapsync_cache.json", (stale_ts, stale_ts))
    recorded.clear()
    assert sync_mod.main(["--min-age", "300"]) == 0
    assert [(c["agent_id"], c["action"]) for c in recorded] == [
        ("module/imapsync1", "list-tasks"),
        ("module/mail1", "list-user-mailboxes"),
        ("module/mail1", "list-addresses"),
    ]
    assert all(c["extra"] == {"isNotificationHidden": True} for c in recorded)


def test_list_mailboxes_list_connectors_and_get_configuration_cache_hit_vs_force_refresh_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify 0 child NS8 tasks on cache hits vs silent `isNotificationHidden: True` refresh on `force_refresh`."""
    list_mb_out_schema = json.loads(
        (ACTIONS_DIR / "list-mailboxes" / "validate-output.json").read_text(encoding="utf-8")
    )
    list_conn_out_schema = json.loads(
        (ACTIONS_DIR / "list-connectors" / "validate-output.json").read_text(encoding="utf-8")
    )
    get_cfg_out_schema = json.loads(
        (ACTIONS_DIR / "get-configuration" / "validate-output.json").read_text(encoding="utf-8")
    )
    disc_out_schema = json.loads(
        (ACTIONS_DIR / "discover-mail-instance" / "validate-output.json").read_text(encoding="utf-8")
    )

    state_dir = tmp_path / "state"
    config_dir = state_dir / "config"
    data_dir = state_dir / "data"
    config_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    token = "v105-actions-e2e-token"
    (config_dir / "internal.token").write_text(f"{token}\n", encoding="utf-8")

    mail_uuid = "11111111-2222-3333-4444-555555555555"
    other_uuid = "99999999-8888-7777-6666-555555555555"
    (state_dir / "environment").write_text(
        f"MAIL_MODULE_UUID={mail_uuid}\nMAIL_MODULE_ID=mail1\n",
        encoding="utf-8",
    )

    mock_rdb = _MockRedis()
    mock_rdb.set_hash(
        "cluster/module_node",
        {"mail1": "1", "mail-toolbox1": "1", "imapsync1": "1", "imapsync2": "2"},
    )
    mock_rdb.set_hash(
        "module/imapsync1/environment",
        {"MAIL_SERVER": mail_uuid, "MAIL_HOST": "10.5.4.1", "NODE_ID": "1"},
    )
    mock_rdb.set_str("module/imapsync1/ui_name", "Account Sync")
    mock_rdb.set_hash(
        "module/imapsync2/environment",
        {"MAIL_SERVER": other_uuid, "MAIL_HOST": "10.5.4.2", "NODE_ID": "2"},
    )

    mail_providers = [
        {
            "module_id": "mail1",
            "module_uuid": mail_uuid,
            "mail_hostname": "mail.example.com",
            "user_domain": "example.com",
            "node": 1,
        }
    ]
    recorded: list[dict[str, Any]] = []
    _install_recording_agent(
        monkeypatch,
        mock_rdb,
        recorded,
        mail_providers=mail_providers,
    )

    monkeypatch.delitem(sys.modules, "toolbox_imapsync", raising=False)
    monkeypatch.delitem(sys.modules, "toolbox_discovery", raising=False)
    monkeypatch.syspath_prepend(str(BIN_DIR))

    _load_script_module("toolbox_imapsync", BIN_DIR / "toolbox_imapsync.py")
    _load_script_module("toolbox_discovery", BIN_DIR / "toolbox_discovery.py")
    sync_mod = _load_script_module("sync_mailboxes_cache_e2e_v105", BIN_DIR / "sync-mailboxes-cache")
    list_mb_mod = _load_script_module(
        "list_mailboxes_20read_v105", ACTIONS_DIR / "list-mailboxes" / "20read"
    )
    list_conn_mod = _load_script_module(
        "list_connectors_20read_v105", ACTIONS_DIR / "list-connectors" / "20read"
    )
    get_cfg_mod = _load_script_module(
        "get_configuration_20read_v105", ACTIONS_DIR / "get-configuration" / "20read"
    )
    disc_inst_mod = _load_script_module(
        "discover_mail_instance_20read_v105", ACTIONS_DIR / "discover-mail-instance" / "20read"
    )

    # Route `subprocess.run(["sync-mailboxes-cache", ...])` inside `list-mailboxes/20read` to `sync_mod.main(...)`
    real_subprocess_run = subprocess.run
    spawned_sync_cmds: list[list[str]] = []

    def _intercept_subprocess_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, list) and cmd and cmd[0] == "sync-mailboxes-cache":
            spawned_sync_cmds.append(list(cmd))
            rc = sync_mod.main(cmd[1:])
            return subprocess.CompletedProcess(cmd, rc)
        return real_subprocess_run(cmd, *args, **kwargs)

    monkeypatch.setattr(list_mb_mod.subprocess, "run", _intercept_subprocess_run)

    monkeypatch.setenv("AGENT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("MILTER_ENABLED", "0")
    monkeypatch.setenv("NODE_ID", "1")

    with TestClient(app) as client:
        http_srv = _start_fastapi_http_bridge(client)
        try:
            web_port = str(http_srv.server_address[1])
            monkeypatch.setenv("WEB_TCP_PORT", web_port)
            monkeypatch.setenv("MAIL_MODULE_UUID", mail_uuid)
            monkeypatch.setenv("MAIL_MODULE_ID", "mail1")
            monkeypatch.setenv("USER_DOMAIN", "example.com")

            # Seed initial caches via timer sync (`sync_mod.main([])`)
            assert sync_mod.main([]) == 0
            recorded.clear()

            # ----------------------------------------------------------------
            # A. `list-mailboxes/20read`: Cache Hit (0 NS8 tasks) vs Force Refresh
            # ----------------------------------------------------------------
            monkeypatch.setattr(sys, "stdin", io.StringIO(""))
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert list_mb_mod.main() == 0
            mb_cached_out = json.loads(buf.getvalue())
            jsonschema.validate(instance=mb_cached_out, schema=list_mb_out_schema)
            assert spawned_sync_cmds[-1] == [
                "sync-mailboxes-cache",
                "--min-age",
                "300",
                "--skip-imapsync",
            ]
            assert recorded == [], "Fresh `list-mailboxes` MUST execute 0 child `agent.tasks.run` calls!"

            # Explicit `force_refresh: true` refreshes mailboxes silently (still skipping imapsync)
            monkeypatch.setattr(sys, "stdin", io.StringIO('{"force_refresh": true}'))
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert list_mb_mod.main() == 0
            mb_forced_out = json.loads(buf.getvalue())
            jsonschema.validate(instance=mb_forced_out, schema=list_mb_out_schema)
            assert spawned_sync_cmds[-1] == ["sync-mailboxes-cache", "--skip-imapsync"]
            assert [(c["agent_id"], c["action"]) for c in recorded] == [
                ("module/mail1", "list-user-mailboxes"),
                ("module/mail1", "list-addresses"),
            ]
            assert all(c["extra"] == {"isNotificationHidden": True} for c in recorded)

            # ----------------------------------------------------------------
            # B. `list-connectors/20read`: Cache Hit (0 NS8 tasks) vs Force Refresh
            # ----------------------------------------------------------------
            recorded.clear()
            monkeypatch.setattr(sys, "stdin", io.StringIO(""))
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert list_conn_mod.main() == 0
            conn_cached_out = json.loads(buf.getvalue())
            jsonschema.validate(instance=conn_cached_out, schema=list_conn_out_schema)
            assert recorded == [], "Fresh `list-connectors` MUST execute 0 child `agent.tasks.run` calls!"
            assert conn_cached_out["imapsync_bridge"]["installed"] is True
            assert conn_cached_out["imapsync_bridge"]["instances"][0]["tasks_loaded"] is True
            assert len(conn_cached_out["imapsync_bridge"]["instances"][0]["tasks"]) == 1

            # Explicit `force_refresh: true` refreshes coupled `imapsync1` silently (`isNotificationHidden: True`)
            monkeypatch.setattr(sys, "stdin", io.StringIO('{"force_refresh": true}'))
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert list_conn_mod.main() == 0
            conn_forced_out = json.loads(buf.getvalue())
            jsonschema.validate(instance=conn_forced_out, schema=list_conn_out_schema)
            assert [(c["agent_id"], c["action"]) for c in recorded] == [
                ("module/imapsync1", "list-tasks"),
            ]
            assert recorded[0]["extra"] == {"isNotificationHidden": True}

            # ----------------------------------------------------------------
            # C. `discover-mail-instance/20read` vs `get-configuration/20read`
            # ----------------------------------------------------------------
            recorded.clear()
            monkeypatch.setattr(
                sys, "stdin", io.StringIO(json.dumps({"mail_module_uuid": mail_uuid}))
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert disc_inst_mod.main() == 0
            disc_out = json.loads(buf.getvalue())
            jsonschema.validate(instance=disc_out, schema=disc_out_schema)
            assert disc_out["discovered_rspamd_url"] == "https://mail.example.com/rspamd"
            assert (config_dir / "discovery_cache.json").is_file()
            assert any(c["action"] == "get-filter-configuration" for c in recorded)
            assert all(c["extra"] == {"isNotificationHidden": True} for c in recorded)

            # Subsequent `get-configuration/20read` (both unconfigured with cache AND configured)
            # MUST execute 0 `get-filter-configuration` tasks!
            for is_configured in (False, True):
                recorded.clear()
                monkeypatch.setenv("MAIL_MODULE_UUID", mail_uuid if is_configured else "")
                monkeypatch.setenv("MAIL_MODULE_ID", "mail1" if is_configured else "")
                monkeypatch.setenv(
                    "RSPAMD_URL",
                    "https://mail.example.com/rspamd" if is_configured else "",
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    assert get_cfg_mod.main() == 0
                cfg_out = json.loads(buf.getvalue())
                jsonschema.validate(instance=cfg_out, schema=get_cfg_out_schema)
                assert cfg_out["discovered_rspamd_url"] == "https://mail.example.com/rspamd"
                assert not any(
                    c["action"] == "get-filter-configuration" for c in recorded
                ), f"`get-configuration` must NOT call `get-filter-configuration` (configured={is_configured})!"
                assert all(c["extra"] == {"isNotificationHidden": True} for c in recorded)
        finally:
            http_srv.shutdown()
            http_srv.server_close()
