"""End-to-End Integration Tests for v1.0.4 NS8 IMAP-Sync (`ns8-imapsync`) Sync Bridge.

Covers:
1. `imageroot/bin/toolbox_imapsync.py` + `imageroot/bin/sync-mailboxes-cache` +
   `imageroot/actions/list-connectors/20read` across:
   - Cluster without `ns8-imapsync` (`installed: False, instances: []`)
   - Cluster with coupled + uncoupled `imapsync*` instances (`tasks_loaded: True` normalizing
     upstream `remotehostname`, `remoteusername`, `remoteport`, `cron`, `security`,
     `delete_remote`, `last_sync_timestamp`, `last_sync_exit_code`, `has_log`, AND graceful
     `tasks_loaded: False` fallback when `list-tasks` returns non-zero or raises),
     validated against `imageroot/actions/list-connectors/validate-output.json`.
2. `ui/src/utils/imapsyncBridge.js` executed in Node against both `toolbox_imapsync.py`
   normalized tasks and raw upstream `ns8-imapsync` `list-tasks` `user_properties` items.
3. End-to-end FastAPI `/api/internal/connectors` + real TCP SMTP 24h-cooldown
   `check_imapsync_bridge_alerts()` failure warning email & recovery reset.
"""

from __future__ import annotations

import http.server
import importlib.machinery
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import types
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import jsonschema
import pytest

from app.backend.connector_service import check_imapsync_bridge_alerts
from app.backend.main import app
from tests._mock_phase23_servers import MockPop3Server, MockSmtpSubmissionServer

ROOT_DIR = Path(__file__).resolve().parent.parent
IMAGEROOT_DIR = ROOT_DIR / "imageroot"
UI_DIR = ROOT_DIR / "ui"
LIST_CONNECTORS_SCHEMA_PATH = (
    IMAGEROOT_DIR / "actions" / "list-connectors" / "validate-output.json"
)


class _MockRedis:
    """In-memory Redis stub matching NS8 cluster & module hash/string keys."""

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


def _install_mock_agent(
    monkeypatch: pytest.MonkeyPatch,
    mock_rdb: _MockRedis,
    task_handler: Any,
) -> None:
    agent_mod = types.ModuleType("agent")
    tasks_mod = types.ModuleType("agent.tasks")

    def _redis_connect(*_args: Any, **_kwargs: Any) -> _MockRedis:
        return mock_rdb

    def _run_task(agent_id: str, action: str, **_kwargs: Any) -> dict[str, Any]:
        return task_handler(agent_id, action)

    agent_mod.redis_connect = _redis_connect  # type: ignore[attr-defined]
    tasks_mod.run = _run_task  # type: ignore[attr-defined]
    agent_mod.tasks = tasks_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "agent.tasks", tasks_mod)


def _start_fastapi_bridge_http_server(client: TestClient) -> http.server.ThreadingHTTPServer:
    """Expose FastAPI TestClient over a real 127.0.0.1 TCP port for imageroot action scripts."""

    class _ProxyHandler(http.server.BaseHTTPRequestHandler):
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

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_imageroot_imapsync_discovery_list_connectors_and_sync_cache_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `toolbox_imapsync.py`, `sync-mailboxes-cache`, and `list-connectors/20read` with 0 and 2 imapsync modules."""
    list_conn_schema = json.loads(LIST_CONNECTORS_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(list_conn_schema)

    state_dir = tmp_path / "state"
    config_dir = state_dir / "config"
    data_dir = state_dir / "data"
    config_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    internal_token = "v104-e2e-internal-token"
    (config_dir / "internal.token").write_text(f"{internal_token}\n", encoding="utf-8")

    mail_uuid = "11111111-2222-3333-4444-555555555555"
    other_mail_uuid = "99999999-8888-7777-6666-555555555555"

    mock_rdb = _MockRedis()
    imapsync_task_responses: dict[str, dict[str, Any]] = {}

    def _handle_agent_task(agent_id: str, action: str) -> dict[str, Any]:
        if agent_id == "module/mail1" and action == "list-user-mailboxes":
            return {
                "exit_code": 0,
                "output": {
                    "user_mailboxes": [
                        {"user": "alice", "enabled": True, "aliases": ["alice@example.com"]},
                        {"user": "bob", "enabled": True, "aliases": ["bob@example.com"]},
                    ]
                },
            }
        if agent_id == "module/mail1" and action == "list-addresses":
            return {"exit_code": 0, "output": {"addresses": [], "domains": ["example.com"]}}
        if action == "list-tasks":
            return imapsync_task_responses.get(agent_id, {"exit_code": 2, "output": ""})
        return {"exit_code": 1, "output": ""}

    _install_mock_agent(monkeypatch, mock_rdb, _handle_agent_task)

    # Ensure fresh load of toolbox_imapsync with our mock `agent`
    monkeypatch.delitem(sys.modules, "toolbox_imapsync", raising=False)
    bin_dir = IMAGEROOT_DIR / "bin"
    monkeypatch.syspath_prepend(str(bin_dir))

    toolbox_imapsync = _load_script_module("toolbox_imapsync", bin_dir / "toolbox_imapsync.py")
    sync_cache_mod = _load_script_module("sync_mailboxes_cache_v104", bin_dir / "sync-mailboxes-cache")
    list_conn_mod = _load_script_module(
        "list_connectors_20read_v104",
        IMAGEROOT_DIR / "actions" / "list-connectors" / "20read",
    )

    monkeypatch.setenv("AGENT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("MILTER_ENABLED", "0")

    with TestClient(app) as client:
        proxy_srv = _start_fastapi_bridge_http_server(client)
        try:
            web_port = str(proxy_srv.server_address[1])

            # ----------------------------------------------------------------
            # Scenario A: Cluster with NO `ns8-imapsync` installed
            # ----------------------------------------------------------------
            mock_rdb.set_hash("cluster/module_node", {"mail1": "1", "mail-toolbox1": "1"})

            # Unconfigured list-connectors/20read still succeeds and returns empty bridge
            monkeypatch.setenv("MAIL_MODULE_UUID", "")
            monkeypatch.setenv("WEB_TCP_PORT", "")
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert list_conn_mod.main() == 0
            unconfigured_out = json.loads(buf.getvalue())
            assert unconfigured_out["connectors"] == []
            assert unconfigured_out["imapsync_bridge"]["installed"] is False
            assert unconfigured_out["imapsync_bridge"]["instances"] == []
            jsonschema.validate(instance=unconfigured_out, schema=list_conn_schema)

            # Configured sync-mailboxes-cache + list-connectors/20read with 0 imapsync instances
            (state_dir / "environment").write_text(
                f"MAIL_MODULE_UUID={mail_uuid}\nMAIL_MODULE_ID=mail1\n",
                encoding="utf-8",
            )
            monkeypatch.setenv("MAIL_MODULE_UUID", mail_uuid)
            monkeypatch.setenv("MAIL_MODULE_ID", "mail1")
            monkeypatch.setenv("WEB_TCP_PORT", web_port)

            assert sync_cache_mod.main() == 0
            cached_empty = json.loads((config_dir / "imapsync_cache.json").read_text(encoding="utf-8"))
            assert cached_empty["installed"] is False
            assert cached_empty["instances"] == []

            buf = io.StringIO()
            with redirect_stdout(buf):
                assert list_conn_mod.main() == 0
            configured_empty_out = json.loads(buf.getvalue())
            assert configured_empty_out["imapsync_bridge"]["installed"] is False
            assert configured_empty_out["imapsync_bridge"]["instances"] == []
            jsonschema.validate(instance=configured_empty_out, schema=list_conn_schema)

            # ----------------------------------------------------------------
            # Scenario B: Coupled `imapsync1` (`tasks_loaded=True`) + Uncoupled `imapsync2` (`tasks_loaded=False`)
            # ----------------------------------------------------------------
            mock_rdb.set_hash(
                "cluster/module_node",
                {
                    "mail1": "1",
                    "mail-toolbox1": "1",
                    "imapsync1": "1",
                    "imapsync2": "2",
                },
            )
            mock_rdb.set_hash(
                "module/imapsync1/environment",
                {
                    "MAIL_SERVER": mail_uuid,
                    "MAIL_HOST": "10.5.4.1",
                    "NODE_ID": "1",
                },
            )
            mock_rdb.set_str("module/imapsync1/ui_name", "Account Sync")

            mock_rdb.set_hash(
                "module/imapsync2/environment",
                {
                    "MAIL_SERVER": other_mail_uuid,
                    "MAIL_HOST": "10.5.4.2",
                    "NODE_ID": "2",
                },
            )
            mock_rdb.set_str("module/imapsync2/ui_name", "Branch Mail Sync")

            imapsync_task_responses["module/imapsync1"] = {
                "exit_code": 0,
                "output": json.dumps(
                    {
                        "enabled_mailboxes": [{"name": "alice", "label": "alice", "value": "alice"}],
                        "user_properties": [
                            {
                                "task_id": "a1b2c3",
                                "localuser": "alice",
                                "remotehostname": "imap.isp.example.com",
                                "remoteusername": "alice@isp.example.com",
                                "remoteport": "993",
                                "security": "--ssl1",
                                "cron": "15m",
                                "foldersynchronization": "all",
                                "exclude": "|Trash",
                                "delete_local": False,
                                "delete_remote": "--delete1older=14",
                                "sieve_enabled": "1",
                                "service_running": False,
                                "last_sync_timestamp": 1763665000,
                                "last_sync_exit_code": 0,
                                "has_log": True,
                            },
                            {
                                "props": {
                                    "localuser": "bob",
                                    "remotehostname": "mail.legacy.example.org",
                                    "remoteusername": "bob@legacy.example.org",
                                    "remoteport": "143",
                                    "security": "tls",
                                    "cron": "1h",
                                    "delete_local": True,
                                    "delete_remote": False,
                                },
                                "task_id": "f6e5d4",
                                "service_running": False,
                                "last_sync_timestamp": 1763665100,
                                "last_sync_exit_code": 11,
                                "has_log": True,
                            },
                        ],
                    }
                ),
            }
            # Simulate missing grant / node error on imapsync2 -> graceful `tasks_loaded: False`
            imapsync_task_responses["module/imapsync2"] = {
                "exit_code": 1,
                "output": "",
            }

            bridge_live = toolbox_imapsync.discover_imapsync_bridge(mail_uuid)
            assert bridge_live["installed"] is True
            assert [i["module_id"] for i in bridge_live["instances"]] == ["imapsync1", "imapsync2"]

            inst1, inst2 = bridge_live["instances"]
            assert inst1["coupled"] is True
            assert inst1["ui_name"] == "Account Sync"
            assert inst1["tasks_loaded"] is True
            assert len(inst1["tasks"]) == 2

            alice_task = next(t for t in inst1["tasks"] if t["localuser"] == "alice")
            assert alice_task["task_id"] == "a1b2c3"
            assert alice_task["remotehost"] == "imap.isp.example.com"
            assert alice_task["remoteport"] == 993
            assert alice_task["remoteuser"] == "alice@isp.example.com"
            assert alice_task["security"] == "ssl"
            assert alice_task["synchronization"] == "15m"
            assert alice_task["sieve_enabled"] is True
            assert alice_task["delete_remote"] is True
            assert alice_task["delete_remote_older"] == 14
            assert alice_task["last_sync_timestamp"] == 1763665000
            assert alice_task["last_sync_exit_code"] == 0
            assert alice_task["has_log"] is True

            bob_task = next(t for t in inst1["tasks"] if t["localuser"] == "bob")
            assert bob_task["security"] == "tls"
            assert bob_task["remoteport"] == 143
            assert bob_task["synchronization"] == "1h"
            assert bob_task["delete_local"] is True
            assert bob_task["last_sync_exit_code"] == 11

            assert inst2["coupled"] is False
            assert inst2["tasks_loaded"] is False
            assert inst2["tasks"] == []

            # Run sync-mailboxes-cache -> persists imapsync_cache.json for FastAPI backend
            assert sync_cache_mod.main() == 0
            persisted_cache = json.loads(
                (config_dir / "imapsync_cache.json").read_text(encoding="utf-8")
            )
            assert persisted_cache["installed"] is True
            assert len(persisted_cache["instances"]) == 2

            # Run list-connectors/20read against live FastAPI backend & validate Draft-07 output schema
            buf = io.StringIO()
            with redirect_stdout(buf):
                assert list_conn_mod.main() == 0
            merged_out = json.loads(buf.getvalue())
            jsonschema.validate(instance=merged_out, schema=list_conn_schema)
            assert merged_out["imapsync_bridge"]["installed"] is True
            assert merged_out["imapsync_bridge"]["instances"][0]["tasks_loaded"] is True
            assert len(merged_out["imapsync_bridge"]["instances"][0]["tasks"]) == 2
            assert merged_out["imapsync_bridge"]["instances"][1]["coupled"] is False
            assert merged_out["imapsync_bridge"]["instances"][1]["tasks_loaded"] is False

            # If live `list-tasks` transiently fails on a subsequent call while backend cache had tasks,
            # `_merge_bridge_data` preserves previously cached tasks cleanly!
            (config_dir / "imapsync_cache.json").write_text(
                json.dumps(persisted_cache),
                encoding="utf-8",
            )
            imapsync_task_responses["module/imapsync1"] = {"exit_code": 1, "output": ""}
            degraded_live = toolbox_imapsync.discover_imapsync_bridge(mail_uuid)
            assert degraded_live["instances"][0]["tasks_loaded"] is False
            fallback_merged = list_conn_mod._merge_bridge_data(degraded_live, persisted_cache)
            assert fallback_merged["instances"][0]["tasks_loaded"] is True
            assert len(fallback_merged["instances"][0]["tasks"]) == 2
            jsonschema.validate(
                instance={"connectors": [], "imapsync_bridge": fallback_merged},
                schema=list_conn_schema,
            )
        finally:
            proxy_srv.shutdown()
            proxy_srv.server_close()


def test_cockpit_imapsync_bridge_js_in_node() -> None:
    """Verify `ui/src/utils/imapsyncBridge.js` normalizes both `toolbox_imapsync.py` and raw `ns8-imapsync` tasks."""
    node_bin = shutil.which("node")
    assert node_bin is not None, "Node.js is required to test ui/src/utils/imapsyncBridge.js"

    node_script = """
import assert from "node:assert/strict";
import {
  normalizeImapsyncTask,
  parseImapsyncSchedule,
  formatImapsyncProtocol,
  extractImapsyncModulesFromCluster,
} from "./src/utils/imapsyncBridge.js";

const mailboxes = [
  { user: "alice", mail: "alice.meier@example.com" },
  { user: "bob", mail: "bob.schmidt@example.com" },
];

// 1. Normalized `toolbox_imapsync.py` task (`imapsync_bridge` payload)
const bridgedOk = normalizeImapsyncTask(
  {
    task_id: "a1b2c3",
    localuser: "alice",
    name: "alice@isp.example.com -> alice",
    remotehost: "imap.isp.example.com",
    remoteport: 993,
    remoteuser: "alice@isp.example.com",
    security: "ssl",
    synchronization: "15m",
    service_running: false,
    sieve_enabled: true,
    delete_local: false,
    delete_remote: true,
    delete_remote_older: 14,
    last_sync_timestamp: 1763665000,
    last_sync_exit_code: 0,
    has_log: true,
  },
  { module_id: "imapsync1", ui_name: "Account Sync", coupled: true },
  mailboxes
);
assert.equal(bridgedOk.source, "imapsync");
assert.equal(bridgedOk.imapsync_module_id, "imapsync1");
assert.equal(bridgedOk.target_email, "alice.meier@example.com");
assert.equal(bridgedOk.protocol, "IMAPS");
assert.equal(bridgedOk.interval_minutes, 15);
assert.equal(bridgedOk.last_status, "ok");
assert.equal(bridgedOk.delete_remote, true);
assert.equal(bridgedOk.delete_remote_older, 14);
assert.equal(bridgedOk.enabled, true);

// 2. Raw upstream `ns8-imapsync` `list-tasks` `user_properties` items (live WebSocket fallback)
const rawUpstreamError = normalizeImapsyncTask(
  {
    task_id: "z9y8x7",
    localuser: "bob",
    remotehostname: "imap.legacy.example.org",
    remoteusername: "bob@legacy.example.org",
    remoteport: "143",
    security: "tls",
    cron: "2h",
    service_running: false,
    sieve_enabled: false,
    delete_local: true,
    delete_remote: false,
    delete_remote_older: 0,
    last_sync_timestamp: 1763665200,
    last_sync_exit_code: 11,
    has_log: true,
  },
  { module_id: "imapsync1", ui_name: "Account Sync", coupled: true },
  mailboxes
);
assert.equal(rawUpstreamError.host, "imap.legacy.example.org");
assert.equal(rawUpstreamError.remote_user, "bob@legacy.example.org");
assert.equal(rawUpstreamError.port, 143);
assert.equal(rawUpstreamError.protocol, "IMAP+STARTTLS");
assert.equal(rawUpstreamError.interval_minutes, 120);
assert.equal(rawUpstreamError.last_status, "error");
assert.equal(rawUpstreamError.last_sync_exit_code, 11);

const rawRunningManual = normalizeImapsyncTask(
  {
    task_id: "m0n1p2",
    localuser: "alice",
    remotehostname: "mail.plain.example.net",
    remoteusername: "alice@plain.example.net",
    remoteport: "",
    security: "",
    cron: "0",
    service_running: true,
    last_sync_timestamp: null,
    last_sync_exit_code: null,
    has_log: false,
  },
  { module_id: "imapsync2", coupled: false },
  mailboxes
);
assert.equal(rawRunningManual.protocol, "IMAP");
assert.equal(rawRunningManual.port, 143);
assert.equal(rawRunningManual.schedule.type, "manual");
assert.equal(rawRunningManual.last_status, "running");
assert.equal(rawRunningManual.coupled, false);
assert.equal(rawRunningManual.enabled, true);

const rawIdleManual = normalizeImapsyncTask(
  {
    task_id: "q3w4e5",
    localuser: "unknown_user",
    remotehostname: "imap.example.net",
    remoteusername: "u@example.net",
    security: "ssl",
    cron: "",
    service_running: false,
    last_sync_timestamp: null,
    last_sync_exit_code: null,
  },
  { module_id: "imapsync1" },
  mailboxes
);
assert.equal(rawIdleManual.last_status, "pending");
assert.equal(rawIdleManual.enabled, false);
assert.equal(rawIdleManual.target_email, "unknown_user");

// 3. Cluster fallback discovery from `list-installed-modules`
const discovered = extractImapsyncModulesFromCluster({
  "ghcr.io/nethserver/imapsync": [
    { id: "imapsync1", module: "imapsync", ui_name: "ISP Sync", node: 1 },
  ],
  "ghcr.io/nethserver/mail": [
    { id: "mail1", module: "mail", ui_name: "Mail", node: 1 },
  ],
});
assert.equal(discovered.length, 1);
assert.equal(discovered[0].module_id, "imapsync1");
assert.equal(discovered[0].ui_name, "ISP Sync");
assert.equal(discovered[0].tasks_loaded, false);

assert.deepEqual(parseImapsyncSchedule("30m"), { type: "minutes", value: 30 });
assert.deepEqual(parseImapsyncSchedule("4h"), { type: "hours", value: 4 });
assert.deepEqual(parseImapsyncSchedule("0"), { type: "manual", value: 0 });
assert.equal(formatImapsyncProtocol("--ssl1"), "IMAPS");
assert.equal(formatImapsyncProtocol("--tls1"), "IMAP+STARTTLS");
assert.equal(formatImapsyncProtocol(""), "IMAP");

console.log("imapsyncBridge.js assertions passed");
"""

    proc = subprocess.run(
        [node_bin, "--input-type=module", "-e", node_script],
        cwd=str(UI_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Node imapsyncBridge.js test failed:\n{proc.stderr}\n{proc.stdout}"
    assert "imapsyncBridge.js assertions passed" in proc.stdout


def test_e2e_backend_connectors_imapsync_bridge_and_smtp_alert_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify native POP3 + bridged `ns8-imapsync` payload on `/api/internal/connectors` and real TCP SMTP alert cooldown."""
    list_conn_schema = json.loads(LIST_CONNECTORS_SCHEMA_PATH.read_text(encoding="utf-8"))

    smtp_srv = MockSmtpSubmissionServer()
    pop3_srv = MockPop3Server()
    smtp_srv.start()
    pop3_srv.start()

    try:
        data_dir = tmp_path / "data"
        config_dir = tmp_path / "config"
        data_dir.mkdir(parents=True)
        config_dir.mkdir(parents=True)

        token = "v104-smtp-e2e-token"
        (config_dir / "internal.token").write_text(f"{token}\n", encoding="utf-8")
        (config_dir / "discovery_mail.env").write_text(
            f"MAIL_HOST=127.0.0.1\n"
            f"MAIL_IMAP_PORT=143\n"
            f"MAIL_SIEVE_PORT=4190\n"
            f"MAIL_SUBMISSION_PORT={smtp_srv.port}\n"
            f"MAIL_MODULE_ID=mail1\n",
            encoding="utf-8",
        )
        (config_dir / "discovery_ldap.env").write_text(
            "LDAP_HOST=127.0.0.1\n"
            "LDAP_PORT=389\n"
            "LDAP_BASE=dc=example,dc=com\n"
            "LDAP_DOMAIN=example.com\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONFIG_DIR", str(config_dir))
        monkeypatch.setenv("APP_SECRET_KEY", "v104-secret-key-0123456789abcdef")
        monkeypatch.setenv("MILTER_ENABLED", "0")
        monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "3600")

        hdrs = {"X-Internal-Token": token}
        cache_file = config_dir / "imapsync_cache.json"

        def _write_imapsync_cache(coupled_exit: int | None) -> None:
            cache_file.write_text(
                json.dumps(
                    {
                        "installed": True,
                        "updated_at": "2026-11-20T20:25:00Z",
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
                                        "task_id": "task01",
                                        "localuser": "alice",
                                        "name": "alice@old-isp.example.com -> alice",
                                        "remotehost": "imap.old-isp.example.com",
                                        "remoteport": 993,
                                        "remoteuser": "alice@old-isp.example.com",
                                        "security": "ssl",
                                        "synchronization": "15m",
                                        "service_running": False,
                                        "sieve_enabled": True,
                                        "delete_local": False,
                                        "delete_remote": False,
                                        "delete_remote_older": 0,
                                        "last_sync_timestamp": 1763665500,
                                        "last_sync_exit_code": coupled_exit,
                                        "has_log": True,
                                    }
                                ],
                            },
                            {
                                "module_id": "imapsync2",
                                "ui_name": "Uncoupled Sync",
                                "node_id": 2,
                                "mail_server": "uuid-mail-other",
                                "mail_host": "10.5.4.2",
                                "coupled": False,
                                "tasks_loaded": True,
                                "tasks": [
                                    {
                                        "task_id": "task02",
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
                                        "last_sync_timestamp": 1763665500,
                                        "last_sync_exit_code": 9,
                                        "has_log": True,
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

        with TestClient(app) as client:
            client.put(
                "/api/internal/settings",
                headers=hdrs,
                json={"admin_alert_email": "it-alerts@example.com"},
            )

            # Save a native POP3 connector so both native & bridged connectors coexist
            save_pop = client.post(
                "/api/internal/connectors",
                headers=hdrs,
                json={
                    "id": None,
                    "name": "Legacy POP3 Account",
                    "target_user": "alice",
                    "target_email": "alice@example.com",
                    "protocol": "pop3",
                    "host": "127.0.0.1",
                    "port": pop3_srv.port,
                    "use_ssl": False,
                    "remote_user": "legacy@example.net",
                    "remote_password": "PopSecret!2026",
                    "interval_minutes": 10,
                    "leave_on_server": True,
                    "enabled": True,
                },
            )
            assert save_pop.status_code == 200

            # 1. Failing coupled task (`exit_code=11`) -> `/api/internal/connectors` schema valid + 1 SMTP alert sent
            _write_imapsync_cache(coupled_exit=11)
            conn_resp = client.get("/api/internal/connectors", headers=hdrs)
            assert conn_resp.status_code == 200
            conn_json = conn_resp.json()
            jsonschema.validate(instance=conn_json, schema=list_conn_schema)
            assert len(conn_json["connectors"]) == 1
            assert conn_json["imapsync_bridge"]["installed"] is True
            assert len(conn_json["imapsync_bridge"]["instances"]) == 2

            assert check_imapsync_bridge_alerts() == 1
            sent_msgs = smtp_srv.get_messages()
            assert len(sent_msgs) == 1
            assert sent_msgs[0].rcpt_tos == ["it-alerts@example.com"]
            raw_alert = re.sub(r"=\r?\n", "", sent_msgs[0].raw_bytes.decode("utf-8", errors="replace"))
            assert "[NS8 Mail Toolbox] NS8 IMAP-Sync Task Failure Alert:" in raw_alert
            assert "imapsync1 (Account Sync)" in raw_alert
            assert "Last Exit Code:   11" in raw_alert
            assert "imapsync2" not in raw_alert  # Uncoupled instance ignored!

            # 2. Immediate second check within 24h cooldown -> 0 duplicate emails
            assert check_imapsync_bridge_alerts() == 0
            assert len(smtp_srv.get_messages()) == 1

            # 3. Coupled task recovers (`last_sync_exit_code=0`) -> clears alert state
            _write_imapsync_cache(coupled_exit=0)
            assert check_imapsync_bridge_alerts() == 0

            # 4. Subsequent new failure (`last_sync_exit_code=16`) -> sends fresh alert immediately
            _write_imapsync_cache(coupled_exit=16)
            assert check_imapsync_bridge_alerts() == 1
            assert len(smtp_srv.get_messages()) == 2

            audit_entries = client.get("/api/internal/audit", headers=hdrs).json()["entries"]
            imapsync_audits = [
                e
                for e in audit_entries
                if e["action"] == "imapsync_failure_alert" and e["source"] == "imapsync_bridge"
            ]
            assert len(imapsync_audits) == 2
    finally:
        smtp_srv.stop()
        pop3_srv.stop()
