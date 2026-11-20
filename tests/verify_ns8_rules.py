"""Static NS8 Architecture, UI Pitfall, Line-Ending, and Cross-Component Contract Audit (Phases 1–3).

Runnable via:
    pytest tests/ -v
    pytest tests/verify_ns8_rules.py -v
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

IMAGEROOT_DIR = ROOT_DIR / "imageroot"
ACTIONS_DIR = IMAGEROOT_DIR / "actions"
SYSTEMD_USER_DIR = IMAGEROOT_DIR / "systemd" / "user"
BUILD_IMAGES_SH = ROOT_DIR / "build-images.sh"
APP_CONTAINERFILE = ROOT_DIR / "app" / "Containerfile"
BACKEND_DIR = ROOT_DIR / "app" / "backend"
FRONTEND_DIR = ROOT_DIR / "app" / "frontend"
UI_DIR = ROOT_DIR / "ui"

# Built-in NS8 cluster/module actions provided by NS8 core
NS8_CORE_ACTIONS = {
    "get-status",
    "list-installed-modules",
    "get-module-info",
    "get-name",
    "list-backup-repositories",
    "list-backups",
}

# Complete Phase 1 + Phase 2 + Phase 3 module actions exposed to Cockpit UI
EXPECTED_UI_MODULE_ACTIONS = {
    # Phase 1: Core config, mailboxes, vacation, templates & audit
    "configure-module",
    "get-configuration",
    "discover-mail-instance",
    "list-mailboxes",
    "set-vacation",
    "get-templates",
    "set-templates",
    "list-audit-log",
    # Phase 2: Central company signatures & Before-DKIM Postfix milter
    "list-signatures",
    "save-signature",
    "delete-signature",
    "preview-signature",
    # Phase 3a: Spam quarantine & digest
    "list-quarantine",
    "release-quarantine",
    "run-digest-now",
    "delete-allowlist",
    # Phase 3b: Mail-tracking history via Rspamd
    "search-mail-history",
    "test-rspamd",
    # Phase 3c: External POP3/IMAP connectors
    "list-connectors",
    "save-connector",
    "delete-connector",
    "test-connector",
    "sync-connector-now",
}

EXPECTED_LIFECYCLE_ACTIONS = {
    "create-module",
    "destroy-module",
    "restore-module",
    "clone-module",
}

MANDATORY_NS8_I18N_KEYS = {
    "action.list-installed-modules",
    "task.cannot_create_task",
    "error.cannot_retrieve_installed_modules",
    "error.cannot_retrieve_module_info",
}

BINARY_OR_IGNORED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    ".git",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
}

BINARY_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".exe",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
}


def _iter_files(base: Path) -> list[Path]:
    """Iterate non-ignored files recursively under `base`."""
    if not base.exists():
        return []
    if base.is_file():
        return [base]
    results: list[Path] = []
    for path in sorted(base.rglob("*")):
        if any(part in BINARY_OR_IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file():
            results.append(path)
    return results


def _flatten_json_keys(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested translation dicts into dot-separated key paths."""
    items: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                items.update(_flatten_json_keys(v, full_key))
            else:
                items[full_key] = v
    return items


def _strip_js_comments(source: str) -> str:
    """Strip JS block and line comments while preserving string literals."""
    pattern = re.compile(
        r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`)|(/\*[\s\S]*?\*/|//[^\n]*)'
    )
    return pattern.sub(lambda m: m.group(1) if m.group(1) is not None else "", source)


def _strip_html_comments(source: str) -> str:
    return re.sub(r"<!--[\s\S]*?-->", "", source)


def _kebab_to_pascal(tag: str) -> str:
    return "".join(part.capitalize() for part in tag.split("-") if part)


def _route_template_to_regex(route_path: str) -> re.Pattern[str]:
    """Convert a FastAPI route path like `/api/mailboxes/{user}/vacation` into a regex."""
    escaped = re.sub(r"\{[^/{}]+\}", "__PATH_PARAM__", route_path)
    escaped = re.escape(escaped).replace("__PATH_PARAM__", r"[^/]+")
    return re.compile(f"^{escaped}$")


def _load_fastapi_routes() -> list[tuple[str, str]]:
    """Import `app.backend.main:app` and return `(METHOD, path)` pairs for all routes."""
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    mod = importlib.import_module("app.backend.main")
    fastapi_app = getattr(mod, "app", None)
    assert fastapi_app is not None, "app.backend.main must expose FastAPI instance `app`"

    routes: list[tuple[str, str]] = []
    for route in fastapi_app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods and path:
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    routes.append((method.upper(), path))
    return routes


# ============================================================================
# 1. NS8 Imageroot, Systemd & Build Rules
# ============================================================================


def test_no_custom_get_status_action():
    """NS8 Rule 1: `imageroot/actions/get-status` MUST NOT exist."""
    assert ACTIONS_DIR.is_dir(), f"Missing actions directory: {ACTIONS_DIR}"
    forbidden = ACTIONS_DIR / "get-status"
    assert not forbidden.exists(), (
        f"NS8 Rule Violation: `{forbidden.relative_to(ROOT_DIR)}` exists! "
        "NS8 core provides `get-status` natively; custom overrides break Status.vue."
    )


def test_action_scripts_set_env_and_no_deprecated_dump_env():
    """NS8 Rule 2 (NS8 3.20+): Scripts persist env via `agent.set_env` and MUST NOT call deprecated `agent.dump_env()`."""
    assert ACTIONS_DIR.is_dir(), f"Missing actions directory: {ACTIONS_DIR}"

    scripts_with_set_env: list[Path] = []
    dump_env_violations: list[str] = []

    for file_path in _iter_files(IMAGEROOT_DIR):
        if file_path.suffix == ".json":
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        rel = file_path.relative_to(ROOT_DIR)

        if re.search(r"\bdump_env\b", text):
            for lineno, line in enumerate(text.splitlines(), start=1):
                if re.search(r"\bdump_env\b", line):
                    dump_env_violations.append(f"{rel}:{lineno}: {line.strip()}")

        if "set_env" not in text:
            continue

        first_line = text.splitlines()[0] if text.splitlines() else ""
        is_python = file_path.suffix == ".py" or "python" in first_line
        if is_python:
            tree = ast.parse(text, filename=str(file_path))
            set_env_lines = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Attribute) and node.func.attr == "set_env")
                    or (isinstance(node.func, ast.Name) and node.func.id == "set_env")
                )
            ]
            if set_env_lines:
                scripts_with_set_env.append(file_path)
        elif re.search(r"\bset_env\b", text):
            scripts_with_set_env.append(file_path)

    assert scripts_with_set_env, (
        "Expected `create-module`, `configure-module`, and `restore-module` scripts to use `agent.set_env`."
    )
    action_dirs_with_set_env = {
        p.parent.name for p in scripts_with_set_env if ACTIONS_DIR in p.parents
    }
    for required_action in ("create-module", "configure-module", "restore-module"):
        assert required_action in action_dirs_with_set_env, (
            f"Expected `imageroot/actions/{required_action}` to persist env with `agent.set_env`."
        )

    assert not dump_env_violations, (
        "Deprecated `agent.dump_env()` must NOT be called in NS8 3.20+ imageroot scripts:\n  - "
        + "\n  - ".join(dump_env_violations)
    )


def test_hostname_validation_and_tcp_ports_initialization():
    """NS8 Rule 4 & multi-port demand: check `01hostname-validation` and `create-module/20initialize`."""
    hostname_val = ACTIONS_DIR / "configure-module" / "01hostname-validation"
    assert hostname_val.is_file(), f"Missing {hostname_val.relative_to(ROOT_DIR)}"
    hv_text = hostname_val.read_text(encoding="utf-8")
    assert "http_route_in_use" in hv_text, (
        "configure-module/01hostname-validation must check `agent.http_route_in_use(...)`"
    )
    assert "validation-failed" in hv_text, (
        'configure-module/01hostname-validation must call `agent.set_status("validation-failed")`'
    )
    assert re.search(r"\bexit\s*\(\s*2\s*\)", hv_text), (
        "configure-module/01hostname-validation must exit with code 2 on validation failure"
    )

    init_script = ACTIONS_DIR / "create-module" / "20initialize"
    assert init_script.is_file(), f"Missing {init_script.relative_to(ROOT_DIR)}"
    init_text = init_script.read_text(encoding="utf-8")
    assert "TCP_PORTS" in init_text, (
        "create-module/20initialize must parse comma-separated `TCP_PORTS` for tcp-ports-demand=2"
    )
    assert "WEB_TCP_PORT" in init_text and "MILTER_TCP_PORT" in init_text, (
        "create-module/20initialize must set `WEB_TCP_PORT` and `MILTER_TCP_PORT`"
    )
    assert "internal.token" in init_text, (
        "create-module/20initialize must generate `config/internal.token`"
    )


def test_systemd_units_pull_missing_and_localhost_binding():
    """NS8 Rules 3 & 5 + v1.0.2 systemd supervision: No `%S/state`, no `--pull=always`, no `--restart=`, `wait-healthy`, safe bindings."""
    assert SYSTEMD_USER_DIR.is_dir(), f"Missing {SYSTEMD_USER_DIR.relative_to(ROOT_DIR)}"
    service_files = sorted(SYSTEMD_USER_DIR.glob("*.service"))
    assert service_files, f"No systemd .service files found in {SYSTEMD_USER_DIR.relative_to(ROOT_DIR)}"

    main_service = SYSTEMD_USER_DIR / "mail-toolbox.service"
    sync_service = SYSTEMD_USER_DIR / "mail-toolbox-sync.service"
    sync_timer = SYSTEMD_USER_DIR / "mail-toolbox-sync.timer"
    wait_healthy = IMAGEROOT_DIR / "bin" / "wait-healthy"
    assert main_service.is_file(), "Missing imageroot/systemd/user/mail-toolbox.service"
    assert sync_service.is_file(), "Missing imageroot/systemd/user/mail-toolbox-sync.service"
    assert sync_timer.is_file(), "Missing imageroot/systemd/user/mail-toolbox-sync.timer"
    assert wait_healthy.is_file(), "Missing imageroot/bin/wait-healthy"

    wait_text = wait_healthy.read_text(encoding="utf-8")
    wait_first_line = wait_text.splitlines()[0] if wait_text.splitlines() else ""
    assert wait_first_line.startswith("#!") and "python" in wait_first_line, (
        f"imageroot/bin/wait-healthy must start with a Python `#!` shebang, got {wait_first_line!r}"
    )
    compile(wait_text, str(wait_healthy.relative_to(ROOT_DIR)), "exec")

    # Functional smoke check: `imageroot/bin/wait-healthy` exits 0 immediately when `/api/health` returns 200
    import http.server
    import os
    import threading

    class _HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    health_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    health_thread = threading.Thread(target=health_srv.serve_forever, daemon=True)
    health_thread.start()
    try:
        env = dict(os.environ)
        env["WEB_TCP_PORT"] = str(health_srv.server_address[1])
        proc = subprocess.run(
            [sys.executable, str(wait_healthy)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, (
            f"imageroot/bin/wait-healthy failed against healthy /api/health endpoint: "
            f"rc={proc.returncode}, stderr={proc.stderr!r}"
        )
    finally:
        health_srv.shutdown()
        health_srv.server_close()

    podman_run_services = 0
    for svc in service_files:
        rel = svc.relative_to(ROOT_DIR)
        content = svc.read_text(encoding="utf-8")

        assert "%S/state" not in content, (
            f"{rel}: Forbidden `%S/state` in NS8 user unit! Must use `%E/state` (`~/.config/state`)."
        )
        assert "%E/state" in content, (
            f"{rel}: Expected `%E/state` working/env directory in NS8 user systemd unit."
        )

        assert not re.search(r"--pull(?:=|\s+)always\b", content), (
            f"{rel}: Forbidden `--pull=always` in systemd unit! Must use `--pull=missing`."
        )
        assert not re.search(r"'[^']*\\\"[^']*'", content), (
            f"{rel}: Forbidden inline `\\\"` inside single quotes in `.service` file! "
            "systemd unescapes `\\\"` before invoking bash, breaking inner quotes."
        )

        if "podman run" in content:
            podman_run_services += 1
            assert re.search(r"--pull(?:=|\s+)missing\b", content), (
                f"{rel}: `podman run` must specify `--pull=missing`."
            )
            assert not re.search(r"--restart(?:=|\s+)", content), (
                f"{rel}: `podman run` MUST NOT specify `--restart=` (systemd supervises `--cgroups=no-conmon` units)."
            )

        publish_matches = re.findall(
            r"(?:^|\s)(?:-p|--publish)(?:=|\s+)([^\s\\]+)", content, flags=re.MULTILINE
        )
        if svc == main_service:
            assert re.search(r"^Restart=always\s*$", content, flags=re.MULTILINE), (
                f"{rel}: Must declare `Restart=always` in `[Service]`."
            )
            assert re.search(
                r"^ExecStartPost=/usr/local/bin/runagent\s+wait-healthy\s*$",
                content,
                flags=re.MULTILINE,
            ), f"{rel}: Must declare `ExecStartPost=/usr/local/bin/runagent wait-healthy`."
            assert re.search(
                r"^Environment=MILTER_BIND_HOST=127\.0\.0\.1\s*$",
                content,
                flags=re.MULTILINE,
            ), f"{rel}: Must provide default `Environment=MILTER_BIND_HOST=127.0.0.1`."
            assert re.search(
                r"^Environment=PODMAN_NETWORK_MODE=slirp4netns:allow_host_loopback=true\s*$",
                content,
                flags=re.MULTILINE,
            ), (
                f"{rel}: Must provide default `Environment=PODMAN_NETWORK_MODE=slirp4netns:allow_host_loopback=true`."
            )
            assert (
                "--network=${PODMAN_NETWORK_MODE}" in content
                or "--network=slirp4netns:allow_host_loopback=true" in content
            ), (
                f"{rel}: Must configure `--network=${{PODMAN_NETWORK_MODE}}` in `podman run`"
            )
            assert "--add-host=host.containers.internal:" not in content, (
                f"{rel}: Must NOT manually pass `--add-host=host.containers.internal:...` (conflicts with Podman 4.7+/5.x)."
            )
            assert "--add-host=accountprovider:10.0.2.2" in content, (
                f"{rel}: Must configure `--add-host=accountprovider:10.0.2.2`"
            )
            assert "CONTAINER_HOST_GATEWAY=10.0.2.2" in content, (
                f"{rel}: Must pass `-e CONTAINER_HOST_GATEWAY=10.0.2.2`"
            )
            assert len(publish_matches) >= 2, (
                f"{rel}: Expected two port mappings (WEB_TCP_PORT:8080 and MILTER_TCP_PORT:8090), got {publish_matches}"
            )
            assert any(
                p.strip("\"'").startswith("127.0.0.1:") and p.strip("\"'").endswith(":8080")
                for p in publish_matches
            ), f"{rel}: Web port 8080 must bind strictly to `127.0.0.1:${{WEB_TCP_PORT}}:8080`"

        for pub in publish_matches:
            unquoted = pub.strip("\"'")
            valid_bind = unquoted.startswith("127.0.0.1:") or unquoted.startswith(
                "${MILTER_BIND_HOST}:"
            )
            assert valid_bind and not unquoted.startswith("0.0.0.0:"), (
                f"{rel}: Port mapping `{pub}` MUST bind strictly to `127.0.0.1:` or `${{MILTER_BIND_HOST}}:` (never 0.0.0.0)."
            )

    assert podman_run_services >= 1, "Expected at least one systemd unit running `podman run`."

    # Ensure MILTER_BIND_HOST is never assigned 0.0.0.0 in any imageroot script
    for path in _iter_files(IMAGEROOT_DIR):
        if path.suffix == ".json":
            continue
        txt = path.read_text(encoding="utf-8")
        assert not re.search(r'MILTER_BIND_HOST.*0\.0\.0\.0', txt), (
            f"{path.relative_to(ROOT_DIR)} must NEVER set MILTER_BIND_HOST to 0.0.0.0"
        )


def test_build_images_sh_labels_and_permissions():
    """Verify `build-images.sh` NS8 labels, LF stripping (`\\r`), and executable bit (`chmod +x`)."""
    assert BUILD_IMAGES_SH.is_file(), "Missing root `build-images.sh`"
    content = BUILD_IMAGES_SH.read_text(encoding="utf-8")

    auth_match = re.search(
        r"org\.nethserver\.authorizations\s*=\s*[\"']?([^\"'\n\\]+)[\"']?", content
    )
    assert auth_match, "build-images.sh missing `org.nethserver.authorizations` label"
    auth_tokens = set(auth_match.group(1).strip().split())
    expected_auth = {
        "traefik@node:routeadm",
        "mail@any:mailadm",
        "cluster:accountconsumer",
        "imapsync@any:reader",
    }
    assert auth_tokens == expected_auth, (
        f"org.nethserver.authorizations mismatch: got {sorted(auth_tokens)}, expected {sorted(expected_auth)}"
    )

    assert re.search(r"org\.nethserver\.tcp-ports-demand\s*=\s*[\"']?2[\"']?", content), (
        "build-images.sh must declare `org.nethserver.tcp-ports-demand=2`"
    )
    assert re.search(r"org\.nethserver\.rootfull\s*=\s*[\"']?0[\"']?", content), (
        "build-images.sh must declare `org.nethserver.rootfull=0`"
    )
    assert re.search(r"org\.nethserver\.images\s*=.*mail-toolbox-app", content), (
        "build-images.sh must declare `org.nethserver.images` referencing `mail-toolbox-app`"
    )

    strips_cr = (
        r"\r" in content
        or "dos2unix" in content
        or re.search(r"tr\s+-d\s+['\"]\\r['\"]", content) is not None
    )
    assert strips_cr, (
        "build-images.sh must strip `\\r` line endings from imageroot scripts (NS8 Rule 8)"
    )
    assert re.search(r"\bchmod\s+(?:-R\s+)?(?:\+x|[0-7]*7[0-7]*5)", content), (
        "build-images.sh must set executable permissions (`chmod +x`) on imageroot scripts (NS8 Rule 8)"
    )


def test_backup_restore_clone_and_python_syntax():
    """NS8 Rule 7 & 8: Restic state-include (incl. toolbox_settings.json), dump/cleanup hooks, restore/clone, AST & JSON."""
    state_inc = IMAGEROOT_DIR / "etc" / "state-include.conf"
    dump_state = IMAGEROOT_DIR / "bin" / "module-dump-state"
    cleanup_state = IMAGEROOT_DIR / "bin" / "module-cleanup-state"
    assert state_inc.is_file(), "Missing imageroot/etc/state-include.conf"
    assert dump_state.is_file(), "Missing imageroot/bin/module-dump-state"
    assert cleanup_state.is_file(), "Missing imageroot/bin/module-cleanup-state"

    state_text = state_inc.read_text(encoding="utf-8")
    assert "environment" in state_text and "internal.token" in state_text, (
        "imageroot/etc/state-include.conf must include environment and internal.token"
    )
    assert "toolbox_settings.json" in state_text, (
        "imageroot/etc/state-include.conf must include `state/config/toolbox_settings.json` (Phase 2/3 settings)"
    )
    assert "sqlite" in state_text, (
        "imageroot/etc/state-include.conf must include SQLite database/backup path"
    )
    assert "backup_db" in dump_state.read_text(encoding="utf-8"), (
        "imageroot/bin/module-dump-state must invoke `app.backend.backup_db` for consistent SQLite backup"
    )

    for action_name in EXPECTED_LIFECYCLE_ACTIONS | EXPECTED_UI_MODULE_ACTIONS:
        action_dir = ACTIONS_DIR / action_name
        assert action_dir.is_dir(), f"Missing action directory: {action_dir.relative_to(ROOT_DIR)}"
        step_files = [p for p in action_dir.iterdir() if p.is_file() and re.match(r"^[0-9]", p.name)]
        assert step_files, f"Action `{action_name}` has no numbered step scripts (`[0-9]*`)"

    # Validate all JSON schemas and Python scripts in imageroot
    for path in _iter_files(IMAGEROOT_DIR):
        rel = path.relative_to(ROOT_DIR)
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                pytest.fail(f"Invalid JSON in {rel}: {exc}")
            if path.name.startswith("validate-"):
                assert isinstance(parsed, dict) and "type" in parsed, (
                    f"{rel}: JSON schema must be an object with a root `type`"
                )
        else:
            first_line = text.splitlines()[0] if text.splitlines() else ""
            is_exec_script = (
                (ACTIONS_DIR in path.parents and re.match(r"^[0-9]", path.name))
                or (IMAGEROOT_DIR / "bin") in path.parents
                or (IMAGEROOT_DIR / "events") in path.parents
                or (IMAGEROOT_DIR / "update-module.d") in path.parents
            )
            if is_exec_script:
                assert first_line.startswith("#!"), (
                    f"{rel}: Missing `#!` shebang on line 1 (got {first_line!r})"
                )
            if path.suffix == ".py" or "python" in first_line:
                try:
                    ast.parse(text, filename=str(rel))
                except SyntaxError as exc:
                    pytest.fail(f"Python SyntaxError in {rel}:{exc.lineno}: {exc.msg}")


# ============================================================================
# 2. Zero CRLF (`\r\n`) Line-Ending Audit & Containerfile Check
# ============================================================================


def test_zero_crlf_line_endings():
    """Verify ZERO `\\r\\n` or stray `\\r` bytes across the entire repository."""
    mandatory_targets = [
        IMAGEROOT_DIR,
        BUILD_IMAGES_SH,
        APP_CONTAINERFILE,
        BACKEND_DIR,
        FRONTEND_DIR / "src",
        UI_DIR / "src",
        UI_DIR / "public",
    ]
    for target in mandatory_targets:
        assert target.exists(), f"Missing required target for CRLF check: {target.relative_to(ROOT_DIR)}"

    crlf_offenders: list[str] = []
    for file_path in _iter_files(ROOT_DIR):
        if file_path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        raw = file_path.read_bytes()
        crlf_count = raw.count(b"\r\n")
        bare_cr_count = raw.count(b"\r") - crlf_count
        if crlf_count > 0 or bare_cr_count > 0:
            crlf_offenders.append(
                f"{file_path.relative_to(ROOT_DIR)} (CRLF={crlf_count}, bare_CR={bare_cr_count})"
            )

    assert not crlf_offenders, (
        "Found forbidden CR/CRLF (`\\r`) bytes! Linux shebangs and NS8 containers require strict LF (`\\n`):\n  - "
        + "\n  - ".join(crlf_offenders)
    )


def test_app_containerfile():
    """Verify `app/Containerfile` uses Python 3.12, initializes `/app/__init__.py`, exposes 8080 & 8090, and runs uvicorn."""
    assert APP_CONTAINERFILE.is_file(), "Missing `app/Containerfile`"
    text = APP_CONTAINERFILE.read_text(encoding="utf-8")
    assert "python:3.12" in text, "app/Containerfile must use python:3.12 base image"
    assert "/app/__init__.py" in text, "app/Containerfile must create `/app/__init__.py`"
    assert "8080" in text and "8090" in text, (
        "app/Containerfile must expose ports 8080 (web) and 8090 (Phase 2 milter)"
    )
    assert "uvicorn" in text and "app.backend.main:app" in text, (
        "app/Containerfile must run `uvicorn app.backend.main:app`"
    )


# ============================================================================
# 3. NS8 Cockpit UI (`ui/`) & Portal (`app/frontend/`) Pitfall & i18n Audit
# ============================================================================


def test_ui_never_calls_create_success_notification_for_app():
    """NS8 UI Rule 1: `createSuccessNotificationForApp` does not exist in `@nethserver/ns8-ui-lib`."""
    ui_src = UI_DIR / "src"
    assert ui_src.is_dir(), f"Missing {ui_src.relative_to(ROOT_DIR)}"

    source_files = [p for p in _iter_files(ui_src) if p.suffix in {".vue", ".js", ".ts"}]
    assert source_files, "No Vue/JS files found under ui/src/"

    forbidden_hits: list[str] = []
    bad_error_notif_calls: list[str] = []

    for path in source_files:
        rel = path.relative_to(ROOT_DIR)
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "createSuccessNotificationForApp" in line:
                forbidden_hits.append(f"{rel}:{lineno}: {line.strip()}")

        cleaned = _strip_js_comments(text)
        for match in re.finditer(r"createErrorNotificationForApp\s*\(", cleaned):
            start = match.end()
            depth = 1
            pos = start
            top_commas = 0
            while pos < len(cleaned) and depth > 0:
                ch = cleaned[pos]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "," and depth == 1:
                    top_commas += 1
                elif ch in "\"'`":
                    quote = ch
                    pos += 1
                    while pos < len(cleaned) and cleaned[pos] != quote:
                        if cleaned[pos] == "\\":
                            pos += 1
                        pos += 1
                pos += 1
            if top_commas < 1:
                lineno = cleaned[: match.start()].count("\n") + 1
                bad_error_notif_calls.append(
                    f"{rel}:{lineno} calls `createErrorNotificationForApp` with <2 arguments"
                )

    assert not forbidden_hits, (
        "NS8 UI Violation: `createSuccessNotificationForApp` throws TypeError in @nethserver/ns8-ui-lib!\n  - "
        + "\n  - ".join(forbidden_hits)
    )
    assert not bad_error_notif_calls, (
        "NS8 UI Violation: `createErrorNotificationForApp(err, message)` requires 2 arguments:\n  - "
        + "\n  - ".join(bad_error_notif_calls)
    )


def _extract_vue_sections(vue_text: str) -> tuple[str, str]:
    """Return `(template_content, script_content)` from a Vue SFC."""
    tmpl_match = re.search(r"<template\b[^>]*>([\s\S]*)</template>", vue_text)
    template = _strip_html_comments(tmpl_match.group(1)) if tmpl_match else ""

    script_match = re.search(r"<script\b[^>]*>([\s\S]*?)</script>", vue_text)
    script = _strip_js_comments(script_match.group(1)) if script_match else ""
    return template, script


def _extract_brace_block(script: str, key_pattern: str) -> str:
    """Extract balanced `{ ... }` block following `key_pattern` in JS object literal."""
    match = re.search(key_pattern, script)
    if not match:
        return ""
    idx = match.end() - 1
    depth = 0
    end = idx
    while end < len(script):
        ch = script[end]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return script[idx : end + 1]
        elif ch in "\"'`":
            quote = ch
            end += 1
            while end < len(script) and script[end] != quote:
                if script[end] == "\\":
                    end += 1
                end += 1
        end += 1
    return script[idx:]


def test_ui_carbon_icons_registered():
    """NS8 UI Rule 2: Every `@carbon/icons-vue` icon used in `<template>` MUST be registered in `components` (and `:icon` props in `data()`)."""
    ui_src = UI_DIR / "src"
    vue_files = [p for p in _iter_files(ui_src) if p.suffix == ".vue"]
    assert vue_files, "No .vue files found in ui/src/"

    errors: list[str] = []

    for vue_path in vue_files:
        rel = vue_path.relative_to(ROOT_DIR)
        template, script = _extract_vue_sections(vue_path.read_text(encoding="utf-8"))
        if not template:
            continue

        imported_icons: set[str] = set()
        for m in re.finditer(
            r"import\s+\{([^}]+)\}\s+from\s+['\"]@carbon/icons-vue[^'\"]*['\"]", script
        ):
            for spec in m.group(1).split(","):
                spec = spec.strip()
                if not spec:
                    continue
                parts = re.split(r"\s+as\s+", spec)
                imported_icons.add(parts[-1].strip())
        for m in re.finditer(
            r"import\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s+['\"]@carbon/icons-vue/[^'\"]+['\"]",
            script,
        ):
            imported_icons.add(m.group(1))

        components_block = _extract_brace_block(script, r"\bcomponents\s*:\s*\{")
        registered_components = set(
            re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b", components_block)
        )

        data_block = _extract_brace_block(script, r"\bdata\s*\([^)]*\)\s*\{")
        computed_block = _extract_brace_block(script, r"\bcomputed\s*:\s*\{")
        exposed_props = set(
            re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b", data_block + "\n" + computed_block)
        )

        template_tags = set(re.findall(r"<\s*([A-Za-z][A-Za-z0-9-]*)\b", template))
        for raw_tag in sorted(template_tags):
            pascal_tag = _kebab_to_pascal(raw_tag) if "-" in raw_tag else raw_tag
            is_carbon_tag = (
                raw_tag in imported_icons
                or pascal_tag in imported_icons
                or re.match(r"^[A-Z][A-Za-z0-9]*(?:16|20|24|32)$", pascal_tag) is not None
            )
            if not is_carbon_tag:
                continue

            if raw_tag not in imported_icons and pascal_tag not in imported_icons:
                errors.append(
                    f"{rel}: Carbon icon tag `<{raw_tag}>` used in <template> is NOT imported from `@carbon/icons-vue`!"
                )
            if raw_tag not in registered_components and pascal_tag not in registered_components:
                errors.append(
                    f"{rel}: Carbon icon tag `<{raw_tag}>` used in <template> is NOT registered in `components: {{ ... }}`!"
                )

        for prop_match in re.finditer(
            r":(?:icon|renderIcon|render-icon)\s*=\s*['\"]([A-Za-z_$][A-Za-z0-9_$]*)['\"]",
            template,
        ):
            icon_ident = prop_match.group(1)
            is_carbon_prop = (
                icon_ident in imported_icons
                or re.match(r"^[A-Z][A-Za-z0-9]*(?:16|20|24|32)$", icon_ident) is not None
            )
            if is_carbon_prop:
                if icon_ident not in imported_icons:
                    errors.append(
                        f"{rel}: `:icon=\"{icon_ident}\"` is NOT imported from `@carbon/icons-vue`!"
                    )
                if icon_ident not in exposed_props:
                    errors.append(
                        f"{rel}: `:icon=\"{icon_ident}\"` is NOT exposed in `data()` or `computed`!"
                    )

    assert not errors, "Carbon Icon Registration Errors:\n  - " + "\n  - ".join(errors)


def test_ui_and_portal_i18n_translations_and_fallback():
    """Verify 100% `de` & `en` key parity in BOTH `ui/public/i18n/*` AND `app/frontend/src/i18n/messages.js`."""
    # 1. Cockpit UI translations
    de_path = UI_DIR / "public" / "i18n" / "de" / "translation.json"
    en_path = UI_DIR / "public" / "i18n" / "en" / "translation.json"
    assert de_path.is_file(), f"Missing {de_path.relative_to(ROOT_DIR)}"
    assert en_path.is_file(), f"Missing {en_path.relative_to(ROOT_DIR)}"

    de_data = json.loads(de_path.read_text(encoding="utf-8"))
    en_data = json.loads(en_path.read_text(encoding="utf-8"))
    de_flat = _flatten_json_keys(de_data)
    en_flat = _flatten_json_keys(en_data)

    de_keys = set(de_flat.keys())
    en_keys = set(en_flat.keys())
    assert de_keys == en_keys, (
        f"Cockpit UI i18n key mismatch between de and en translation.json!\n"
        f"  Only in DE: {sorted(de_keys - en_keys)}\n"
        f"  Only in EN: {sorted(en_keys - de_keys)}"
    )

    missing_mandatory = sorted(MANDATORY_NS8_I18N_KEYS - en_keys)
    assert not missing_mandatory, (
        f"Missing mandatory NS8 i18n keys in translation.json: {missing_mandatory}"
    )

    # Check static $t('literal.key') references in ui/src/**/*.vue exist in translation.json
    missing_used_keys: list[str] = []
    for vue_path in _iter_files(UI_DIR / "src"):
        if vue_path.suffix not in {".vue", ".js"}:
            continue
        rel = vue_path.relative_to(ROOT_DIR)
        text = vue_path.read_text(encoding="utf-8")
        for m in re.finditer(
            r"(?<!\+\s)\$tc?\(\s*(['\"])([A-Za-z0-9_.-]+)\1\s*(?:,|\))", text
        ):
            key = m.group(2)
            if key.endswith("."):
                continue
            if key not in en_keys and not any(k.startswith(f"{key}.") for k in en_keys):
                lineno = text[: m.start()].count("\n") + 1
                missing_used_keys.append(f"{rel}:{lineno} -> `{key}`")

    assert not missing_used_keys, (
        "Cockpit UI references missing i18n keys not present in ui/public/i18n/{de,en}/translation.json:\n  - "
        + "\n  - ".join(missing_used_keys)
    )

    i18n_sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in _iter_files(UI_DIR / "src")
        if p.suffix in {".js", ".ts", ".vue"}
    )
    assert re.search(r"fallbackLocale\s*:\s*['\"]en['\"]", i18n_sources), (
        "ui/src must configure VueI18n `fallbackLocale: 'en'` (NS8 UI Rule 3)"
    )

    # Verify Cockpit `ui/src/main.js` defaults strictly to English ("en") and never uses `navigator.language`
    ui_main_path = UI_DIR / "src" / "main.js"
    assert ui_main_path.is_file(), f"Missing {ui_main_path.relative_to(ROOT_DIR)}"
    ui_main_cleaned = _strip_js_comments(ui_main_path.read_text(encoding="utf-8"))
    assert "navigator.language" not in ui_main_cleaned and "navigator.languages" not in ui_main_cleaned, (
        "ui/src/main.js must NOT use `navigator.language` to override the English ('en') default locale!"
    )
    assert "ns8_mail_toolbox_cockpit_lang" in ui_main_cleaned, (
        "ui/src/main.js must read explicit locale override from `ns8_mail_toolbox_cockpit_lang` in localStorage"
    )
    assert re.search(r"['\"]en['\"]", ui_main_cleaned), (
        "ui/src/main.js must initialize default locale to 'en'"
    )

    # 2. Secretariat & Quarantine Portal (`app/frontend/index.html` & `src/i18n/messages.js`) English default + DE/EN parity
    portal_index_html = FRONTEND_DIR / "index.html"
    assert portal_index_html.is_file(), f"Missing {portal_index_html.relative_to(ROOT_DIR)}"
    portal_index_text = portal_index_html.read_text(encoding="utf-8")
    assert re.search(r"<html\b[^>]*\blang=['\"]en['\"]", portal_index_text), (
        "app/frontend/index.html must declare `<html lang=\"en\">`"
    )
    assert "Secretariat Portal" in portal_index_text, (
        "app/frontend/index.html `<title>` must use English title (`Secretariat Portal · NS8 Mail Toolbox`)"
    )

    portal_msg_path = FRONTEND_DIR / "src" / "i18n" / "messages.js"
    assert portal_msg_path.is_file(), f"Missing {portal_msg_path.relative_to(ROOT_DIR)}"
    portal_msg_cleaned = _strip_js_comments(portal_msg_path.read_text(encoding="utf-8"))
    assert "navigator.language" not in portal_msg_cleaned and "navigator.languages" not in portal_msg_cleaned, (
        "app/frontend/src/i18n/messages.js must NOT auto-select German from `navigator.language`!"
    )

    node_bin = shutil.which("node")
    if node_bin and (FRONTEND_DIR / "node_modules").is_dir():
        proc = subprocess.run(
            [
                node_bin,
                "--input-type=module",
                "-e",
                "Object.defineProperty(globalThis, 'navigator', { value: { language: 'de-DE', languages: ['de-DE', 'de'] }, configurable: true }); "
                "globalThis.window = { localStorage: { getItem: () => null, setItem: () => {} } }; "
                "const mod = await import('./src/i18n/messages.js'); "
                "const initial = mod.detectInitialLocale ? mod.detectInitialLocale() : mod.useI18n().locale.value; "
                "console.log(JSON.stringify({ initial, de: mod.messages.de, en: mod.messages.en }));",
            ],
            cwd=str(FRONTEND_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"Failed importing portal messages.js in Node: {proc.stderr}"
        portal_i18n = json.loads(proc.stdout.strip().splitlines()[-1])
        assert portal_i18n["initial"] == "en", (
            f"Portal `detectInitialLocale()` must default to `'en'` when localStorage is empty (got {portal_i18n['initial']!r})"
        )
        p_de = set(_flatten_json_keys(portal_i18n["de"]).keys())
        p_en = set(_flatten_json_keys(portal_i18n["en"]).keys())
        assert p_de == p_en, (
            f"Portal `app/frontend/src/i18n/messages.js` DE/EN key mismatch!\n"
            f"  Only in DE: {sorted(p_de - p_en)}\n"
            f"  Only in EN: {sorted(p_en - p_de)}"
        )


# ============================================================================
# 4. Cross-Component Contract Verification (Phases 1 + 2 + 3)
# ============================================================================


EXTERNAL_BRIDGED_MODULE_ACTIONS = {
    "list-tasks",
    "start-task",
    "stop-task",
    "get-log",
}


def _extract_ui_module_actions() -> dict[str, set[Path]]:
    """Extract NS8 `this.instanceName` module action names invoked across `ui/src/**/*.vue`."""
    action_to_files: dict[str, set[Path]] = {}
    for vue_path in _iter_files(UI_DIR / "src"):
        if vue_path.suffix not in {".vue", ".js"}:
            continue
        _, script = (
            _extract_vue_sections(vue_path.read_text(encoding="utf-8"))
            if vue_path.suffix == ".vue"
            else ("", _strip_js_comments(vue_path.read_text(encoding="utf-8")))
        )
        var_map: dict[str, str] = {}
        for vm in re.finditer(
            r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*['\"]([a-z0-9-]+)['\"]",
            script,
        ):
            var_map[vm.group(1)] = vm.group(2)

        # Ensure external bridged actions (e.g. ns8-imapsync) are NEVER invoked against `this.instanceName`
        for own_call in re.finditer(
            r"createModuleTaskForApp\s*\(\s*this\.instanceName\s*,\s*\{([\s\S]*?)\}\s*\)",
            script,
        ):
            body = own_call.group(1)
            for am in re.finditer(
                r"\baction\s*:\s*(?:['\"]([a-z0-9-]+)['\"]|([A-Za-z_$][A-Za-z0-9_$]*))",
                body,
            ):
                resolved_own = am.group(1) or var_map.get(am.group(2) or "")
                assert resolved_own not in EXTERNAL_BRIDGED_MODULE_ACTIONS, (
                    f"{vue_path.relative_to(ROOT_DIR)} invokes external imapsync action `{resolved_own}` "
                    "on `this.instanceName` instead of the target `imapsync` module ID!"
                )

        for am in re.finditer(
            r"\baction\s*:\s*(?:['\"]([a-z0-9-]+)['\"]|([A-Za-z_$][A-Za-z0-9_$]*))", script
        ):
            literal_action, var_name = am.group(1), am.group(2)
            resolved = literal_action or var_map.get(var_name or "")
            if resolved and resolved not in EXTERNAL_BRIDGED_MODULE_ACTIONS:
                action_to_files.setdefault(resolved, set()).add(vue_path)

    return action_to_files


def test_contract_cockpit_ui_to_imageroot_actions_and_schemas():
    """Verify all 22 NS8 module actions & JSON fields in `ui/src/**` match `imageroot/actions/*/validate-*.json` & `[0-9]*`."""
    action_to_files = _extract_ui_module_actions()
    assert action_to_files, "Failed to detect any NS8 task actions in `ui/src/**`"

    # 1. Every non-core action called in UI must exist in imageroot/actions/<action>/ with a [0-9]* script
    for action_name, callers in sorted(action_to_files.items()):
        if action_name in NS8_CORE_ACTIONS:
            continue
        action_dir = ACTIONS_DIR / action_name
        caller_names = [str(p.relative_to(ROOT_DIR)) for p in sorted(callers)]
        assert action_dir.is_dir(), (
            f"UI calls action `{action_name}` (in {caller_names}), "
            f"but `imageroot/actions/{action_name}/` does NOT exist!"
        )
        step_scripts = [
            p for p in action_dir.iterdir() if p.is_file() and re.match(r"^[0-9]", p.name)
        ]
        assert step_scripts, (
            f"`imageroot/actions/{action_name}/` has no `[0-9]*` executable step script!"
        )

    # 2. All 22 required Cockpit module actions (Phases 1–3) must be wired in UI and present in imageroot/actions/
    missing_in_ui = sorted(EXPECTED_UI_MODULE_ACTIONS - set(action_to_files.keys()))
    assert not missing_in_ui, (
        f"Cockpit UI (`ui/src/**`) never invokes required module actions: {missing_in_ui}"
    )

    # 3. Verify new Cockpit views & side-menu navigation entries
    for view_name in ("Signatures.vue", "MailTracking.vue", "Connectors.vue"):
        view_path = UI_DIR / "src" / "views" / view_name
        assert view_path.is_file(), f"Missing Cockpit view `{view_path.relative_to(ROOT_DIR)}`"

    side_menu = (UI_DIR / "src" / "components" / "AppSideMenuContent.vue").read_text(
        encoding="utf-8"
    )
    for route_slug in ("status", "mailboxes", "signatures", "mail-tracking", "connectors", "templates", "settings"):
        assert route_slug in side_menu, (
            f"AppSideMenuContent.vue missing navigation link for `{route_slug}`"
        )

    # 4. Check `configure-module` Phase 1 + Phase 2/3 backward-compatible JSON schema & Settings.vue fields
    settings_vue = UI_DIR / "src" / "views" / "Settings.vue"
    settings_text = settings_vue.read_text(encoding="utf-8")
    cfg_input_schema_path = ACTIONS_DIR / "configure-module" / "validate-input.json"
    cfg_input_schema = json.loads(cfg_input_schema_path.read_text(encoding="utf-8"))
    cfg_props = set(cfg_input_schema.get("properties", {}).keys())

    phase1_cfg_fields = {
        "mail_module_uuid",
        "portal_hostname",
        "lets_encrypt",
        "http2https",
        "allowed_groups",
    }
    phase23_cfg_fields = {
        "portal_enabled",
        "milter_enabled",
        "rspamd_url",
        "rspamd_user",
        "rspamd_password",
        "rspamd_tls_verify",
        "clear_rspamd_password",
        "digest_enabled",
        "digest_time",
        "timezone",
        "admin_alert_email",
        "portal_tracking_enabled",
    }
    all_cfg_fields = phase1_cfg_fields | phase23_cfg_fields
    assert all_cfg_fields.issubset(cfg_props), (
        f"configure-module/validate-input.json missing properties: {sorted(all_cfg_fields - cfg_props)}"
    )
    cfg_required = set(cfg_input_schema.get("required", []))
    assert cfg_required.issubset(phase1_cfg_fields), (
        f"configure-module/validate-input.json must keep Phase 2/3 fields optional for backward compatibility; "
        f"unexpected required fields: {sorted(cfg_required - phase1_cfg_fields)}"
    )
    for ui_cfg_field in (
        "mail_module_uuid",
        "portal_enabled",
        "portal_hostname",
        "lets_encrypt",
        "http2https",
        "allowed_groups",
        "milter_enabled",
        "rspamd_url",
        "rspamd_user",
        "rspamd_password",
        "rspamd_tls_verify",
        "digest_enabled",
        "digest_time",
        "timezone",
        "admin_alert_email",
        "portal_tracking_enabled",
    ):
        assert ui_cfg_field in settings_text, (
            f"Settings.vue missing `configure-module` field `{ui_cfg_field}`"
        )
    assert "discover-mail-instance" in settings_text, (
        "Settings.vue must call `discover-mail-instance` for live AD/LDAP group & Rspamd discovery"
    )

    # Verify Signatures.vue renders both activation and rollback/reset Postfix Milter snippets
    signatures_vue_text = (UI_DIR / "src" / "views" / "Signatures.vue").read_text(
        encoding="utf-8"
    )
    assert "milter_postfix_snippet" in signatures_vue_text, (
        "Signatures.vue must display `milter_postfix_snippet`"
    )
    assert "milter_postfix_reset_snippet" in signatures_vue_text, (
        "Signatures.vue must display `milter_postfix_reset_snippet` rollback snippet"
    )

    # 5. Check `get-configuration` & `discover-mail-instance` output contracts (v1.0.1+)
    get_cfg_schema_path = ACTIONS_DIR / "get-configuration" / "validate-output.json"
    get_cfg_schema_obj = json.loads(get_cfg_schema_path.read_text(encoding="utf-8"))
    get_cfg_props = set(get_cfg_schema_obj.get("properties", {}).keys())
    get_cfg_script = (ACTIONS_DIR / "get-configuration" / "20read").read_text(encoding="utf-8")
    expected_get_cfg_fields = {
        "configured",
        "portal_enabled",
        "mail_module_uuid",
        "mail_module_id",
        "user_domain",
        "ldap_schema",
        "portal_hostname",
        "lets_encrypt",
        "http2https",
        "allowed_groups",
        "web_tcp_port",
        "milter_tcp_port",
        "available_mail_servers",
        "available_groups",
        "milter_enabled",
        "milter_postfix_snippet",
        "milter_postfix_reset_snippet",
        "discovered_rspamd_url",
        "rspamd_url",
        "rspamd_user",
        "rspamd_password_set",
        "rspamd_tls_verify",
        "digest_enabled",
        "digest_time",
        "timezone",
        "admin_alert_email",
        "portal_tracking_enabled",
    }
    assert expected_get_cfg_fields.issubset(get_cfg_props), (
        f"get-configuration/validate-output.json missing properties: {sorted(expected_get_cfg_fields - get_cfg_props)}"
    )
    for field in expected_get_cfg_fields:
        assert field in get_cfg_script, f"get-configuration/20read missing output field `{field}`"

    # Check enriched `available_groups` item schema (`member_count` & `is_system`) on both get-configuration and discover-mail-instance
    for act_with_groups in ("get-configuration", "discover-mail-instance"):
        out_schema = json.loads(
            (ACTIONS_DIR / act_with_groups / "validate-output.json").read_text(encoding="utf-8")
        )
        grp_items_props = (
            out_schema.get("properties", {})
            .get("available_groups", {})
            .get("items", {})
            .get("properties", {})
        )
        for gfield in ("group", "description", "member_count", "is_system"):
            assert gfield in grp_items_props, (
                f"{act_with_groups}/validate-output.json `available_groups.items` missing `{gfield}`"
            )

    disc_out_props = set(
        json.loads(
            (ACTIONS_DIR / "discover-mail-instance" / "validate-output.json").read_text(encoding="utf-8")
        )
        .get("properties", {})
        .keys()
    )
    for dfield in (
        "mail_module_uuid",
        "mail_module_id",
        "user_domain",
        "ldap_schema",
        "same_node",
        "discovered_rspamd_url",
        "available_groups",
    ):
        assert dfield in disc_out_props, (
            f"discover-mail-instance/validate-output.json missing property `{dfield}`"
        )

    # 6. Check `set-vacation` open-ended date compatibility
    set_vac_schema = json.loads(
        (ACTIONS_DIR / "set-vacation" / "validate-input.json").read_text(encoding="utf-8")
    )
    set_vac_props = set_vac_schema.get("properties", {})
    for date_field in ("start_date", "end_date"):
        prop_def = set_vac_props.get(date_field, {})
        is_bare_strict_date = (
            prop_def.get("type") == "string"
            and prop_def.get("format") == "date"
            and "oneOf" not in prop_def
            and "anyOf" not in prop_def
        )
        assert not is_bare_strict_date, (
            f"set-vacation/validate-input.json `{date_field}` rejects open-ended (`\"\"`/`null`) dates!"
        )

    # 7. Check v1.0.4 NS8 IMAP-Sync (`ns8-imapsync`) Sync Bridge contracts across imageroot & Cockpit UI
    toolbox_imapsync_py = IMAGEROOT_DIR / "bin" / "toolbox_imapsync.py"
    assert toolbox_imapsync_py.is_file(), "Missing `imageroot/bin/toolbox_imapsync.py`"
    toolbox_imapsync_text = toolbox_imapsync_py.read_text(encoding="utf-8")
    assert "def discover_imapsync_bridge" in toolbox_imapsync_text
    assert "def normalize_imapsync_task" in toolbox_imapsync_text

    sync_cache_py = IMAGEROOT_DIR / "bin" / "sync-mailboxes-cache"
    sync_cache_text = sync_cache_py.read_text(encoding="utf-8")
    assert "discover_imapsync_bridge" in sync_cache_text and "imapsync_cache.json" in sync_cache_text, (
        "`imageroot/bin/sync-mailboxes-cache` must discover and persist `imapsync_cache.json`"
    )

    list_conn_script = (ACTIONS_DIR / "list-connectors" / "20read").read_text(encoding="utf-8")
    assert "discover_imapsync_bridge" in list_conn_script and "imapsync_bridge" in list_conn_script, (
        "`imageroot/actions/list-connectors/20read` must discover and merge `imapsync_bridge`"
    )
    list_conn_schema = json.loads(
        (ACTIONS_DIR / "list-connectors" / "validate-output.json").read_text(encoding="utf-8")
    )
    assert list_conn_schema.get("required") == ["connectors"], (
        "`list-connectors/validate-output.json` must keep `required == ['connectors']` for backward compatibility"
    )
    assert "imapsync_bridge" in list_conn_schema.get("properties", {}), (
        "`list-connectors/validate-output.json` must define `imapsync_bridge` property"
    )

    imapsync_bridge_js = UI_DIR / "src" / "utils" / "imapsyncBridge.js"
    imapsync_log_modal = UI_DIR / "src" / "components" / "ImapsyncLogModal.vue"
    assert imapsync_bridge_js.is_file(), "Missing `ui/src/utils/imapsyncBridge.js`"
    assert imapsync_log_modal.is_file(), "Missing `ui/src/components/ImapsyncLogModal.vue`"
    bridge_js_text = imapsync_bridge_js.read_text(encoding="utf-8")
    for exported_fn in (
        "normalizeImapsyncTask",
        "parseImapsyncSchedule",
        "formatImapsyncProtocol",
        "extractImapsyncModulesFromCluster",
        "navigateToCockpitApp",
    ):
        assert exported_fn in bridge_js_text, (
            f"`ui/src/utils/imapsyncBridge.js` missing `{exported_fn}`"
        )

    connectors_vue_text = (UI_DIR / "src" / "views" / "Connectors.vue").read_text(encoding="utf-8")
    status_vue_text = (UI_DIR / "src" / "views" / "Status.vue").read_text(encoding="utf-8")
    assert "ImapsyncLogModal" in connectors_vue_text and "imapsyncInstalled" in connectors_vue_text, (
        "`Connectors.vue` must wire `ImapsyncLogModal` and `imapsyncInstalled`"
    )
    assert "runExternalImapsyncTask" in connectors_vue_text and "createModuleTaskForApp(targetModuleId" in connectors_vue_text, (
        "`Connectors.vue` must dispatch external `ns8-imapsync` actions against `targetModuleId` (never `this.instanceName`)"
    )
    assert "imapsyncTasks" in status_vue_text and "totalConnectorsCount" in status_vue_text, (
        "`Status.vue` must include bridged `imapsyncTasks` in `totalConnectorsCount` and `failingConnectorsCount`"
    )
    assert "createModuleTaskForApp(targetModuleId" in status_vue_text, (
        "`Status.vue` must dispatch external `list-tasks` against `targetModuleId` (never `this.instanceName`)"
    )

    en_i18n = _flatten_json_keys(
        json.loads((UI_DIR / "public" / "i18n" / "en" / "translation.json").read_text(encoding="utf-8"))
    )
    for ext_act in EXTERNAL_BRIDGED_MODULE_ACTIONS:
        assert f"action.{ext_act}" in en_i18n, (
            f"Missing `action.{ext_act}` in `ui/public/i18n/{{en,de}}/translation.json`"
        )


def test_release_artifacts_license_and_ns8_repo_workflow():
    """Verify root GPL-3.0 `LICENSE` and `.github/workflows/publish-ns8-repo.yml` release workflow."""
    license_path = ROOT_DIR / "LICENSE"
    assert license_path.is_file(), "Missing root `LICENSE` file"
    lic_text = license_path.read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in lic_text and "Version 3" in lic_text, (
        "Root `LICENSE` must contain the GNU General Public License v3.0 text"
    )

    repo_wf = ROOT_DIR / ".github" / "workflows" / "publish-ns8-repo.yml"
    assert repo_wf.is_file(), "Missing `.github/workflows/publish-ns8-repo.yml`"
    wf_text = repo_wf.read_text(encoding="utf-8")
    assert "repodata.json" in wf_text and "metadata.json" in wf_text, (
        "`.github/workflows/publish-ns8-repo.yml` must generate NS8 Software Center `repodata.json` + `metadata.json`"
    )

    destroy_script = ACTIONS_DIR / "destroy-module" / "20destroy"
    assert destroy_script.is_file(), "Missing `imageroot/actions/destroy-module/20destroy`"
    assert "/api/internal/deactivate-all-vacations" in destroy_script.read_text(encoding="utf-8"), (
        "`destroy-module/20destroy` must call `/api/internal/deactivate-all-vacations` before stopping service"
    )


def test_github_release_alignment_and_english_readme():
    """Verify English-only README.md, BLS-ISP/ns8-mailtoolbox OCI/metadata alignment, 0 legacy owner refs, .gitignore & CI workflows."""
    # 1. English-only README.md referencing BLS-ISP/ns8-mailtoolbox & ghcr.io/bls-isp
    readme_path = ROOT_DIR / "README.md"
    assert readme_path.is_file(), "Missing root `README.md`"
    readme_text = readme_path.read_text(encoding="utf-8")
    assert "BLS-ISP/ns8-mailtoolbox" in readme_text, (
        "README.md must reference `BLS-ISP/ns8-mailtoolbox`"
    )
    assert "ghcr.io/bls-isp" in readme_text, (
        "README.md must reference `ghcr.io/bls-isp`"
    )
    assert "https://raw.githubusercontent.com/BLS-ISP/ns8-nethforge/repomd/ns8/updates/" in readme_text, (
        "README.md must document the BLS-ISP/ns8-nethforge Software Center repository URL"
    )
    assert "ns8-imapsync" in readme_text and "imapsync@any:reader" in readme_text, (
        "README.md must document the NS8 IMAP-Sync (`ns8-imapsync`) Sync Bridge and `imapsync@any:reader` authorization"
    )

    forbidden_german_headings_or_phrases = (
        "Projektübersicht",
        "Installationsanleitung",
        "Konfiguration",
        "Voraussetzungen",
        "Schnellstart",
        "Architekturübersicht",
        "Sekretariats-Webansicht",
        "Krankheitsvertretung",
        "Datensicherung & Wiederherstellung",
    )
    found_german = [w for w in forbidden_german_headings_or_phrases if w in readme_text]
    assert not found_german, (
        f"README.md must be 100% English-only, but still contains German headings/terms: {found_german}"
    )

    # 2. OCI & Software Center metadata alignment across app/Containerfile, build-images.sh & ui/public/metadata.json
    containerfile_text = APP_CONTAINERFILE.read_text(encoding="utf-8")
    assert "org.opencontainers.image.source" in containerfile_text, (
        "app/Containerfile missing `org.opencontainers.image.source` label"
    )
    assert "https://github.com/BLS-ISP/ns8-mailtoolbox" in containerfile_text, (
        "app/Containerfile must reference `https://github.com/BLS-ISP/ns8-mailtoolbox`"
    )

    build_sh_text = BUILD_IMAGES_SH.read_text(encoding="utf-8")
    assert "ghcr.io/bls-isp" in build_sh_text, (
        "build-images.sh default repobase must be `ghcr.io/bls-isp`"
    )
    assert "https://github.com/BLS-ISP/ns8-mailtoolbox" in build_sh_text, (
        "build-images.sh must set `org.opencontainers.image.source=https://github.com/BLS-ISP/ns8-mailtoolbox`"
    )

    metadata_path = UI_DIR / "public" / "metadata.json"
    assert metadata_path.is_file(), "Missing `ui/public/metadata.json`"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata.get("source") == "ghcr.io/bls-isp/mail-toolbox", (
        f"ui/public/metadata.json `source` mismatch: {metadata.get('source')!r}"
    )
    assert metadata.get("upstream_name") == "NS8 Mail Toolbox", (
        f"ui/public/metadata.json `upstream_name` mismatch: {metadata.get('upstream_name')!r}"
    )
    docs = metadata.get("docs", {})
    assert docs.get("code_url") == "https://github.com/BLS-ISP/ns8-mailtoolbox", (
        f"ui/public/metadata.json `docs.code_url` mismatch: {docs.get('code_url')!r}"
    )
    assert docs.get("bug_url") == "https://github.com/BLS-ISP/ns8-mailtoolbox/issues", (
        f"ui/public/metadata.json `docs.bug_url` mismatch: {docs.get('bug_url')!r}"
    )
    assert docs.get("documentation_url") == "https://github.com/BLS-ISP/ns8-mailtoolbox#readme", (
        f"ui/public/metadata.json `docs.documentation_url` mismatch: {docs.get('documentation_url')!r}"
    )
    assert docs.get("terms_url") == "https://github.com/BLS-ISP/ns8-mailtoolbox/blob/main/LICENSE", (
        f"ui/public/metadata.json `docs.terms_url` mismatch: {docs.get('terms_url')!r}"
    )
    assert docs.get("relnotes_url") == "https://github.com/BLS-ISP/ns8-mailtoolbox/releases", (
        f"ui/public/metadata.json `docs.relnotes_url` mismatch: {docs.get('relnotes_url')!r}"
    )

    for rel_script in (
        "actions/create-module/20initialize",
        "actions/restore-module/06copyenv",
        "actions/clone-module/50configure",
    ):
        stext = (IMAGEROOT_DIR / rel_script).read_text(encoding="utf-8")
        assert "ghcr.io/bls-isp/mail-toolbox-app" in stext, (
            f"imageroot/{rel_script} fallback image must reference `ghcr.io/bls-isp/mail-toolbox-app`"
        )

    # 3. Assert ZERO remaining case-insensitive occurrences of the legacy personal namespace across all repo files
    legacy_owner = "genera" + "black"
    legacy_hits: list[str] = []
    for file_path in _iter_files(ROOT_DIR):
        if file_path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        if legacy_owner in text.lower():
            legacy_hits.append(str(file_path.relative_to(ROOT_DIR)))

    assert not legacy_hits, (
        f"Found unmigrated legacy owner references in repository files: {legacy_hits}"
    )

    # 4. Verify .gitignore does not ignore ui/.env
    gitignore_lines = [
        ln.strip()
        for ln in (ROOT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if ".env" in gitignore_lines:
        assert "!ui/.env" in gitignore_lines, (
            "`.gitignore` has bare `.env` without `!ui/.env`, which would ignore `ui/.env`!"
        )

    # 5. Validate all .github/workflows/*.yml files
    wf_dir = ROOT_DIR / ".github" / "workflows"
    wf_files = sorted(wf_dir.glob("*.yml"))
    assert len(wf_files) >= 3, f"Expected >=3 workflow files in .github/workflows, got {wf_files}"

    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        yaml = None

    for wf in wf_files:
        raw = wf.read_bytes()
        assert b"\r" not in raw, f"{wf.relative_to(ROOT_DIR)} contains CR bytes"
        wtext = raw.decode("utf-8")
        for idx, line in enumerate(wtext.splitlines(), 1):
            leading = line[: len(line) - len(line.lstrip())]
            assert "\t" not in leading, (
                f"{wf.relative_to(ROOT_DIR)}:{idx} contains forbidden tab character in YAML indentation"
            )
        assert re.search(r"^name\s*:", wtext, re.M), f"{wf.name} missing top-level `name:`"
        assert re.search(r"^on\s*:", wtext, re.M), f"{wf.name} missing top-level `on:`"
        assert re.search(r"^jobs\s*:", wtext, re.M), f"{wf.name} missing top-level `jobs:`"
        if yaml is not None:
            parsed_wf = yaml.safe_load(wtext)
            assert isinstance(parsed_wf, dict) and "jobs" in parsed_wf


def test_contract_imageroot_to_backend_internal_routes():
    """Verify `imageroot/actions/*/[0-9]*` HTTP calls match `app/backend` `/api/internal/*` FastAPI routes."""
    backend_routes = _load_fastapi_routes()
    route_matchers = [
        (method, path, _route_template_to_regex(path)) for method, path in backend_routes
    ]

    expected_internal = [
        ("GET", "/api/health"),
        # Extended settings
        ("GET", "/api/internal/settings"),
        ("PUT", "/api/internal/settings"),
        # Phase 1 + clean uninstall + directory groups
        ("GET", "/api/internal/mailboxes"),
        ("GET", "/api/internal/groups"),
        ("PUT", "/api/internal/mailboxes/max.muster/vacation"),
        ("POST", "/api/internal/deactivate-all-vacations"),
        ("GET", "/api/internal/templates"),
        ("PUT", "/api/internal/templates"),
        ("GET", "/api/internal/audit"),
        ("POST", "/api/internal/reconcile"),
        # Phase 2: Signatures & Milter
        ("GET", "/api/internal/signatures"),
        ("POST", "/api/internal/signatures"),
        ("POST", "/api/internal/signatures/delete"),
        ("POST", "/api/internal/signatures/preview"),
        # Phase 3a: Quarantine & Digest
        ("GET", "/api/internal/quarantine"),
        ("POST", "/api/internal/quarantine/release"),
        ("POST", "/api/internal/quarantine/run-digest"),
        ("POST", "/api/internal/allowlist/delete"),
        # Phase 3b: Mail-Tracking
        ("POST", "/api/internal/mail-tracking/search"),
        ("POST", "/api/internal/mail-tracking/test-rspamd"),
        # Phase 3c: External Connectors
        ("GET", "/api/internal/connectors"),
        ("POST", "/api/internal/connectors"),
        ("POST", "/api/internal/connectors/delete"),
        ("POST", "/api/internal/connectors/test"),
        ("POST", "/api/internal/connectors/sync"),
    ]
    for exp_method, sample_path in expected_internal:
        matched = any(
            m == exp_method and rx.match(sample_path) for m, _, rx in route_matchers
        )
        assert matched, (
            f"Backend `app.backend.main:app` is missing required route `{exp_method} {sample_path}`! "
            f"Registered routes: {backend_routes}"
        )

    imageroot_api_paths: list[tuple[Path, str]] = []
    for script_path in _iter_files(IMAGEROOT_DIR):
        if script_path.suffix == ".json":
            continue
        text = script_path.read_text(encoding="utf-8")
        for m in re.finditer(r"(/api/[A-Za-z0-9_./{}-]+)", text):
            raw_path = m.group(1).rstrip(".,)")
            normalized = re.sub(r"\{[^/]+\}", "sample_user", raw_path)
            imageroot_api_paths.append((script_path, normalized))

    assert imageroot_api_paths, "Expected imageroot scripts to call `/api/internal/...` and `/api/health`"
    for script_path, norm_path in imageroot_api_paths:
        if norm_path in {"/api", "/api/internal", "/api/internal/"}:
            continue
        norm_clean = norm_path.rstrip("/") if norm_path != "/" else norm_path
        matched = any(rx.match(norm_clean) for _, _, rx in route_matchers)
        assert matched, (
            f"{script_path.relative_to(ROOT_DIR)} calls `{norm_path}`, which does NOT match any FastAPI route: "
            f"{[p for _, p in backend_routes]}"
        )


def test_contract_portal_frontend_to_backend_api_routes():
    """Verify `app/frontend/src/**` (Secretariat Portal + Quarantine + Optional Mail Tracking) matches `app/backend` `/api/*`."""
    frontend_src = FRONTEND_DIR / "src"
    assert frontend_src.is_dir(), f"Missing {frontend_src.relative_to(ROOT_DIR)}"

    backend_routes = _load_fastapi_routes()
    route_matchers = [
        (method, path, _route_template_to_regex(path)) for method, path in backend_routes
    ]

    expected_portal_routes = [
        ("POST", "/api/auth/login"),
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/mailboxes"),
        ("PUT", "/api/mailboxes/max.muster/vacation"),
        ("GET", "/api/templates"),
        ("GET", "/api/audit"),
        # Optional Secretariat read-only mail tracking
        ("POST", "/api/mail-tracking/search"),
        # Phase 3a public scanner-safe quarantine release endpoints
        ("GET", "/api/quarantine/token/sample_token_123"),
        ("POST", "/api/quarantine/action"),
    ]
    for exp_method, sample_path in expected_portal_routes:
        matched = any(
            m == exp_method and rx.match(sample_path) for m, _, rx in route_matchers
        )
        assert matched, (
            f"Backend `app.backend.main:app` missing portal/quarantine route `{exp_method} {sample_path}`!"
        )

    quarantine_views = list(frontend_src.rglob("QuarantineActionView.vue"))
    assert quarantine_views, "Missing `QuarantineActionView.vue` in `app/frontend/src/`"
    tracking_views = list(frontend_src.rglob("MailTrackingPortalView.vue"))
    assert tracking_views, "Missing `MailTrackingPortalView.vue` in `app/frontend/src/`"

    app_vue_text = (frontend_src / "App.vue").read_text(encoding="utf-8")
    assert "quarantine_token" in app_vue_text, (
        "`app/frontend/src/App.vue` must detect `quarantine_token` in URL query before requiring secretariat login"
    )
    assert "portal_tracking_enabled" in app_vue_text, (
        "`app/frontend/src/App.vue` must conditionally enable Mail-Tracking tab via `portal_tracking_enabled`"
    )

    frontend_files = [p for p in _iter_files(frontend_src) if p.suffix in {".js", ".ts", ".vue"}]
    discovered_paths: set[str] = set()
    unmatched_calls: list[str] = []

    for fpath in frontend_files:
        rel = fpath.relative_to(ROOT_DIR)
        cleaned = _strip_js_comments(fpath.read_text(encoding="utf-8"))
        for m in re.finditer(r"(['\"`])(/api/[A-Za-z0-9_./${}()%-]+)\1", cleaned):
            raw_url = m.group(2).split("?")[0]
            normalized = re.sub(r"\$\{[^}]+\}", "sample_param", raw_url)
            if normalized in {"/api", "/api/"}:
                continue
            norm_clean = normalized.rstrip("/")
            discovered_paths.add(norm_clean)
            if not any(rx.match(norm_clean) for _, _, rx in route_matchers):
                lineno = cleaned[: m.start()].count("\n") + 1
                unmatched_calls.append(f"{rel}:{lineno} -> `{raw_url}` (normalized `{norm_clean}`)")

    assert not unmatched_calls, (
        "Portal frontend calls `/api/...` paths that do NOT exist on FastAPI backend:\n  - "
        + "\n  - ".join(unmatched_calls)
    )

    for required_norm in (
        "/api/auth/login",
        "/api/auth/me",
        "/api/auth/logout",
        "/api/mailboxes",
        "/api/mailboxes/sample_param/vacation",
        "/api/templates",
        "/api/audit",
        "/api/mail-tracking/search",
        "/api/quarantine/token/sample_param",
        "/api/quarantine/action",
    ):
        assert required_norm in discovered_paths, (
            f"Portal frontend (`app/frontend/src/**`) never calls expected endpoint `{required_norm}`! "
            f"Discovered endpoints: {sorted(discovered_paths)}"
        )


def test_jsonschema_ui_inputs_and_live_backend_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate Cockpit UI task payloads against `validate-input.json` and live FastAPI responses against `validate-output.json`."""
    jsonschema = pytest.importorskip("jsonschema")
    from fastapi.testclient import TestClient

    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from app.backend.main import app as fastapi_app

    # 1. Validate all UI action input payloads against imageroot/actions/<action>/validate-input.json
    ui_payloads: dict[str, dict[str, Any]] = {
        "configure-module": {
            "mail_module_uuid": "11111111-2222-3333-4444-555555555555",
            "portal_enabled": True,
            "portal_hostname": "mail-office.firma.de",
            "lets_encrypt": False,
            "http2https": True,
            "allowed_groups": ["mail-office"],
            "milter_enabled": True,
            "rspamd_url": "https://mail.firma.de/rspamd",
            "rspamd_user": "admin",
            "rspamd_password": "",
            "rspamd_tls_verify": False,
            "clear_rspamd_password": False,
            "digest_enabled": True,
            "digest_time": "07:30",
            "timezone": "Europe/Berlin",
            "admin_alert_email": "admin@firma.de",
            "portal_tracking_enabled": True,
        },
        "discover-mail-instance": {
            "mail_module_uuid": "11111111-2222-3333-4444-555555555555",
        },
        "set-vacation": {
            "user": "max.muster",
            "enabled": True,
            "start_date": "",
            "end_date": "",
            "subject": "Abwesenheit",
            "body": "Bin abwesend",
            "forward_enabled": True,
            "forward_address": "vertretung@firma.de",
            "keep_copy": True,
            "updated_by": "cockpit-admin",
        },
        "set-templates": {
            "templates": [
                {
                    "id": "default_de",
                    "name": "Standard",
                    "lang": "de",
                    "is_default": True,
                    "subject": "Abwesenheitsnotiz",
                    "body": "Nachricht",
                }
            ]
        },
        "save-signature": {
            "id": None,
            "name": "Company Default",
            "scope_type": "default",
            "scope_value": "*",
            "priority": 10,
            "enabled": True,
            "html_template": "<p>{{displayName}}</p>",
            "text_template": "",
            "auto_plain_from_html": True,
            "reply_mode": "append_once",
            "placement": "below_reply",
        },
        "delete-signature": {"id": 1},
        "preview-signature": {
            "user": "max.muster",
            "sender_email": "max.muster@firma.de",
            "html_template": "<p>{{displayName}}</p>",
            "text_template": "",
        },
        "release-quarantine": {
            "user": "max.muster",
            "uid": "101",
            "message_id": "<msg@firma.de>",
            "allow_sender": True,
        },
        "run-digest-now": {"user": ""},
        "delete-allowlist": {"id": 1},
        "search-mail-history": {
            "sender": "",
            "recipient": "",
            "subject": "",
            "action_filter": "all",
            "date_from": "",
            "date_to": "",
            "limit": 200,
        },
        "test-rspamd": {
            "rspamd_url": "https://mail.firma.de/rspamd/",
            "rspamd_user": "admin",
            "rspamd_password": "secret",
            "rspamd_tls_verify": False,
        },
        "save-connector": {
            "id": None,
            "name": "Strato",
            "target_user": "max.muster",
            "target_email": "max.muster@firma.de",
            "protocol": "imaps",
            "host": "imap.strato.de",
            "port": 993,
            "use_ssl": True,
            "remote_user": "info@firma.de",
            "remote_password": "",
            "interval_minutes": 5,
            "leave_on_server": True,
            "enabled": True,
        },
        "delete-connector": {"id": 1},
        "test-connector": {
            "id": None,
            "protocol": "imaps",
            "host": "imap.strato.de",
            "port": 993,
            "use_ssl": True,
            "remote_user": "info@firma.de",
            "remote_password": "secret",
        },
        "sync-connector-now": {"id": 1},
    }

    for action_name, payload in ui_payloads.items():
        schema_path = ACTIONS_DIR / action_name / "validate-input.json"
        assert schema_path.is_file(), f"Missing {schema_path.relative_to(ROOT_DIR)}"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft7Validator.check_schema(schema)
        jsonschema.validate(instance=payload, schema=schema)

    # Verify configure-module schema explicitly accepts empty `allowed_groups: []` AND Cockpit-only `portal_enabled: False, portal_hostname: ""`
    cfg_schema = json.loads((ACTIONS_DIR / "configure-module" / "validate-input.json").read_text(encoding="utf-8"))
    jsonschema.validate(
        instance={**ui_payloads["configure-module"], "allowed_groups": []},
        schema=cfg_schema,
    )
    jsonschema.validate(
        instance={
            **ui_payloads["configure-module"],
            "portal_enabled": False,
            "portal_hostname": "",
            "allowed_groups": [],
        },
        schema=cfg_schema,
    )

    # 2. Validate live FastAPI /api/internal/* responses against imageroot/actions/<action>/validate-output.json
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir()
    config_dir.mkdir()
    (config_dir / "internal.token").write_text("schema-audit-token\n", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("MILTER_ENABLED", "0")

    hdr = {"X-Internal-Token": "schema-audit-token"}
    with TestClient(fastapi_app) as client:
        live_outputs: dict[str, Any] = {
            "list-mailboxes": client.get("/api/internal/mailboxes", headers=hdr).json(),
            "get-templates": client.get("/api/internal/templates", headers=hdr).json(),
            "set-templates": client.put(
                "/api/internal/templates",
                headers=hdr,
                json=ui_payloads["set-templates"],
            ).json(),
            "list-audit-log": client.get("/api/internal/audit", headers=hdr).json(),
            "list-signatures": client.get("/api/internal/signatures", headers=hdr).json(),
            "save-signature": client.post(
                "/api/internal/signatures",
                headers=hdr,
                json=ui_payloads["save-signature"],
            ).json(),
            "preview-signature": client.post(
                "/api/internal/signatures/preview",
                headers=hdr,
                json=ui_payloads["preview-signature"],
            ).json(),
            "delete-signature": client.post(
                "/api/internal/signatures/delete",
                headers=hdr,
                json={"id": 1},
            ).json(),
            "list-quarantine": client.get("/api/internal/quarantine", headers=hdr).json(),
            "run-digest-now": client.post(
                "/api/internal/quarantine/run-digest",
                headers=hdr,
                json={"user": ""},
            ).json(),
            "delete-allowlist": client.post(
                "/api/internal/allowlist/delete",
                headers=hdr,
                json={"id": 999},
            ).json(),
            "search-mail-history": client.post(
                "/api/internal/mail-tracking/search",
                headers=hdr,
                json=ui_payloads["search-mail-history"],
            ).json(),
            "test-rspamd": client.post(
                "/api/internal/mail-tracking/test-rspamd",
                headers=hdr,
                json={"rspamd_url": "http://127.0.0.1:59999", "rspamd_password": ""},
            ).json(),
            "list-connectors": client.get("/api/internal/connectors", headers=hdr).json(),
            "save-connector": client.post(
                "/api/internal/connectors",
                headers=hdr,
                json={**ui_payloads["save-connector"], "remote_password": "secret"},
            ).json(),
            "test-connector": client.post(
                "/api/internal/connectors/test",
                headers=hdr,
                json={
                    "id": None,
                    "protocol": "imap",
                    "host": "127.0.0.1",
                    "port": 59998,
                    "use_ssl": False,
                    "remote_user": "u",
                    "remote_password": "p",
                },
            ).json(),
            "sync-connector-now": client.post(
                "/api/internal/connectors/sync",
                headers=hdr,
                json={"id": 1},
            ).json(),
            "delete-connector": client.post(
                "/api/internal/connectors/delete",
                headers=hdr,
                json={"id": 1},
            ).json(),
        }

    for action_name, resp_payload in live_outputs.items():
        schema_path = ACTIONS_DIR / action_name / "validate-output.json"
        assert schema_path.is_file(), f"Missing {schema_path.relative_to(ROOT_DIR)}"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft7Validator.check_schema(schema)
        jsonschema.validate(instance=resp_payload, schema=schema)

