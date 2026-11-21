#!/usr/bin/env python3

"""Discover installed NethServer 8 `ns8-imapsync` instances and normalize their sync tasks."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

try:
    import agent
    import agent.tasks
except ImportError:
    agent = None  # type: ignore[assignment]


_IMAPSYNC_ID_RE = re.compile(r"^imapsync(\d+)$")
_HIDDEN_TASK_EXTRA = {"isNotificationHidden": True}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace").strip()
    return str(val).strip()


def _decode_dict(raw_map: Any) -> dict[str, str]:
    if not isinstance(raw_map, dict):
        return {}
    return {_decode_str(k): _decode_str(v) for k, v in raw_map.items()}


def _parse_task_output(result: dict[str, Any]) -> Any:
    output = result.get("output")
    if isinstance(output, (dict, list)):
        return output
    if isinstance(output, str):
        txt = output.strip()
        if txt:
            try:
                return json.loads(txt)
            except Exception:
                return {}
    return {}


def _parse_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    s = _decode_str(val).lower()
    if not s:
        return default
    if s in ("0", "false", "no", "off"):
        return False
    return True


def _parse_optional_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return int(val)
    try:
        return int(_decode_str(val))
    except (TypeError, ValueError):
        return None


def normalize_imapsync_task(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize an `ns8-imapsync` `user_properties` record into the shared `imapsync_bridge` task contract."""
    props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
    merged = {**props, **raw}

    task_id = _decode_str(merged.get("task_id"))
    localuser = _decode_str(merged.get("localuser") or merged.get("mailbox"))
    remotehost = _decode_str(merged.get("remotehost") or merged.get("remotehostname"))
    remoteuser = _decode_str(merged.get("remoteuser") or merged.get("remoteusername"))

    raw_sec = _decode_str(merged.get("security")).lower()
    if raw_sec in ("--ssl1", "ssl", "imaps"):
        security = "ssl"
    elif raw_sec in ("--tls1", "tls", "starttls"):
        security = "tls"
    else:
        security = ""

    default_port = 993 if security == "ssl" else 143
    remoteport = _parse_optional_int(merged.get("remoteport")) or default_port

    cron = _decode_str(merged.get("cron"))
    foldersync = _decode_str(merged.get("foldersynchronization")) or "all"
    exclude = _decode_str(merged.get("exclude")).lstrip(",")

    raw_sync = _decode_str(merged.get("synchronization"))
    if raw_sync:
        synchronization = raw_sync
    elif cron and cron != "0":
        synchronization = cron
    else:
        synchronization = "manual"

    raw_del_local = merged.get("delete_local")
    if isinstance(raw_del_local, str):
        delete_local = raw_del_local.strip() not in ("", "0", "false", "False")
    else:
        delete_local = bool(raw_del_local)

    raw_del_remote = merged.get("delete_remote")
    delete_remote_older = _parse_optional_int(merged.get("delete_remote_older")) or 0
    if isinstance(raw_del_remote, str):
        dr_str = raw_del_remote.strip()
        if dr_str.startswith("--delete1older="):
            delete_remote = True
            delete_remote_older = (
                _parse_optional_int(dr_str.removeprefix("--delete1older=")) or 0
            )
        else:
            delete_remote = dr_str not in ("", "0", "false", "False")
    else:
        delete_remote = bool(raw_del_remote)

    raw_sieve = merged.get("sieve_enabled")
    if isinstance(raw_sieve, str):
        sieve_enabled = raw_sieve.strip() not in ("", "0", "false", "False")
    else:
        sieve_enabled = bool(raw_sieve)

    service_running = _parse_bool(merged.get("service_running"), default=False)
    last_sync_timestamp = _parse_optional_int(merged.get("last_sync_timestamp"))
    last_sync_exit_code = _parse_optional_int(merged.get("last_sync_exit_code"))
    has_log = _parse_bool(merged.get("has_log"), default=False)

    custom_name = _decode_str(merged.get("name") or merged.get("description"))
    if custom_name:
        name = custom_name
    elif remoteuser and localuser:
        name = f"{remoteuser} -> {localuser}"
    elif remoteuser:
        name = remoteuser
    else:
        name = task_id

    return {
        "task_id": task_id,
        "localuser": localuser,
        "name": name,
        "remotehost": remotehost,
        "remotehostname": remotehost,
        "remoteport": remoteport,
        "remoteuser": remoteuser,
        "remoteusername": remoteuser,
        "security": security,
        "synchronization": synchronization,
        "cron": cron,
        "foldersynchronization": foldersync,
        "exclude": exclude,
        "service_running": service_running,
        "sieve_enabled": sieve_enabled,
        "delete_local": delete_local,
        "delete_remote": delete_remote,
        "delete_remote_older": delete_remote_older,
        "last_sync_timestamp": last_sync_timestamp,
        "last_sync_exit_code": last_sync_exit_code,
        "has_log": has_log,
    }


def _read_module_ui_name(rdb: Any, mod_id: str, env_map: dict[str, str]) -> str:
    for getter in (
        lambda: rdb.get(f"module/{mod_id}/ui_name"),
        lambda: rdb.hget("cluster/ui_names", mod_id),
        lambda: rdb.hget("cluster/module_ui_name", mod_id),
    ):
        try:
            val = _decode_str(getter())
            if val:
                return val
        except Exception:
            continue
    return _decode_str(env_map.get("UI_NAME"))


def discover_imapsync_bridge(
    mail_module_uuid: str = "",
    *,
    fetch_tasks: bool = True,
    only_coupled: bool = True,
    cached_bridge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover installed `ns8-imapsync` instances in Redis and optionally load tasks silently."""
    updated_at = _iso_now()
    if agent is None:
        return {
            "installed": False,
            "updated_at": updated_at,
            "instances": [],
        }

    try:
        rdb = agent.redis_connect(use_replica=True)
    except TypeError:
        try:
            rdb = agent.redis_connect()
        except Exception:
            return {
                "installed": False,
                "updated_at": updated_at,
                "instances": [],
            }
    except Exception:
        return {
            "installed": False,
            "updated_at": updated_at,
            "instances": [],
        }

    module_nodes: dict[str, str] = {}
    candidate_ids: set[str] = set()

    try:
        module_nodes = _decode_dict(rdb.hgetall("cluster/module_node"))
        for mod_id in module_nodes:
            if _IMAPSYNC_ID_RE.match(mod_id):
                candidate_ids.add(mod_id)
    except Exception:
        pass

    try:
        for raw_key in rdb.scan_iter("module/imapsync*/environment"):
            key_str = _decode_str(raw_key)
            if key_str.startswith("module/") and key_str.endswith("/environment"):
                mod_id = key_str[len("module/") : -len("/environment")]
                if _IMAPSYNC_ID_RE.match(mod_id):
                    candidate_ids.add(mod_id)
    except Exception:
        pass

    if not candidate_ids:
        return {
            "installed": False,
            "updated_at": updated_at,
            "instances": [],
        }

    cached_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(cached_bridge, dict):
        for c_inst in cached_bridge.get("instances", []) or []:
            if isinstance(c_inst, dict) and c_inst.get("module_id"):
                cached_by_id[str(c_inst["module_id"])] = c_inst

    def _sort_key(mid: str) -> tuple[int, str]:
        m = _IMAPSYNC_ID_RE.match(mid)
        return (int(m.group(1)) if m else 999999, mid)

    target_uuid = str(mail_module_uuid or "").strip()
    instances: list[dict[str, Any]] = []

    for mod_id in sorted(candidate_ids, key=_sort_key):
        env_map: dict[str, str] = {}
        try:
            env_map = _decode_dict(rdb.hgetall(f"module/{mod_id}/environment"))
        except Exception:
            env_map = {}

        inst_mail_server = _decode_str(env_map.get("MAIL_SERVER"))
        inst_mail_host = _decode_str(env_map.get("MAIL_HOST"))
        node_raw = module_nodes.get(mod_id) or env_map.get("NODE_ID") or "0"
        node_id = _parse_optional_int(node_raw) or 0
        ui_name = _read_module_ui_name(rdb, mod_id, env_map)

        coupled = (inst_mail_server == target_uuid) if target_uuid else True

        tasks_loaded = False
        tasks: list[dict[str, Any]] = []
        should_query_tasks = (
            fetch_tasks
            and bool(inst_mail_server)
            and (not only_coupled or coupled)
        )
        if should_query_tasks:
            try:
                res = agent.tasks.run(
                    agent_id=f"module/{mod_id}",
                    action="list-tasks",
                    extra=_HIDDEN_TASK_EXTRA,
                )
                if isinstance(res, dict) and res.get("exit_code") == 0:
                    payload = _parse_task_output(res)
                    raw_props = (
                        payload.get("user_properties", [])
                        if isinstance(payload, dict)
                        else []
                    )
                    if isinstance(raw_props, list):
                        for item in raw_props:
                            if isinstance(item, dict):
                                tasks.append(normalize_imapsync_task(item))
                        tasks.sort(
                            key=lambda t: (
                                t["localuser"].lower(),
                                t["remoteuser"].lower(),
                                t["task_id"],
                            )
                        )
                        tasks_loaded = True
            except Exception as exc:
                print(
                    f"Info: Opportunistic list-tasks on module/{mod_id} deferred to Cockpit UI: {exc}",
                    file=sys.stderr,
                )

        if not tasks_loaded and mod_id in cached_by_id:
            prev = cached_by_id[mod_id]
            if prev.get("tasks_loaded") and isinstance(prev.get("tasks"), list):
                tasks_loaded = True
                tasks = prev["tasks"]

        instances.append(
            {
                "module_id": mod_id,
                "ui_name": ui_name,
                "node_id": node_id,
                "mail_server": inst_mail_server,
                "mail_host": inst_mail_host,
                "coupled": coupled,
                "tasks_loaded": tasks_loaded,
                "tasks": tasks,
            }
        )

    return {
        "installed": len(instances) > 0,
        "updated_at": updated_at,
        "instances": instances,
    }


if __name__ == "__main__":
    import os

    json.dump(
        discover_imapsync_bridge(os.environ.get("MAIL_MODULE_UUID", "")),
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
