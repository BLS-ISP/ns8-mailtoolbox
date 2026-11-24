"""ISP-Scale Wire-Protocol End-to-End Tests for v1.0.7 Quarantine & Digest Timeout Fixes.

Covers:
1. Multi-mailbox Samba AD / ISP server over TCP `MockImapServer`:
   - `15` enabled mailboxes + `5` disabled mailboxes in `mailboxes_cache.json` + `15` non-mailbox
     Samba AD accounts (`krbtgt`, `Guest`, `svc-backup`, `vpn-01`..`vpn-12`).
   - `250` spam messages in `mb01` `Junk` (`150` recent + `100` older than 30 days).
   - Verifies `imageroot/actions/list-quarantine/20read` completes in `< 3s` using chunked `50`-UID
     batch `UID FETCH` (`<= 2` FETCH commands per 100-message cap instead of `250`), makes **0**
     IMAP auth attempts on non-mailbox AD or disabled users, serves warm 30s cache hits in `< 50ms`
     with **0** new IMAP fetches, and performs a live rescan on `{"force_refresh": true}`.
2. `imageroot/actions/run-digest-now/20write` E2E:
   - Exits `0` cleanly when unconfigured (`MAIL_MODULE_UUID=""`).
   - Uses `UID SEARCH SINCE DD-Mon-YYYY` (14-day window) so the `100` old messages (`45` days old)
     are ignored (`items_included` counts only the `150` recent items on `mb01`), and delivers
     digests directly into `INBOX` via Dovecot Master-User IMAP `APPEND`.
3. Scheduled Daily Digest Resilience (`check_and_run_scheduled_digest`):
   - Does NOT mark `digest_last_scheduled_date` when `run_quarantine_digest` raises a transient
     error, enforces the 15-minute retry cooldown (`_last_scheduled_digest_error_mono`), and
     succeeds once the 15-minute backoff expires.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import email
from email.message import EmailMessage
import email.policy
from email.utils import format_datetime
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import types
from typing import Any

from fastapi.testclient import TestClient
import jsonschema
from ldap3 import Connection, MOCK_SYNC, Server
import pytest

from app.backend.config import LdapSettings, get_settings, update_runtime_settings
from app.backend.db import get_meta
from app.backend.ldap_client import set_ldap_connection_factory
from app.backend.main import app
from app.backend.quarantine_service import clear_quarantine_cache
import app.backend.reconciler as rec
from app.backend.reconciler import check_and_run_scheduled_digest, reset_scheduled_digest_backoff
from tests._mock_managesieve import MockManageSieveServer
from tests._mock_phase23_servers import (
    MockImapServer,
    MockSmtpSubmissionServer,
    format_imap_internaldate,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
ACTIONS_DIR = ROOT_DIR / "imageroot" / "actions"
SMTP_POLICY = email.policy.SMTP

ENABLED_MB_USERS = [f"mb{i:02d}" for i in range(1, 16)]
DISABLED_MB_USERS = [f"disabled{i:02d}" for i in range(1, 6)]
NON_MAILBOX_AD_USERS = ["krbtgt", "Guest", "svc-backup"] + [f"vpn-{i:02d}" for i in range(1, 13)]


def _load_script_module(module_name: str, script_path: Path) -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(module_name, str(script_path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _invoke_action_inprocess(
    script_path: Path,
    *,
    stdin_text: str,
    env_overrides: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, dict[str, Any]]:
    mod = _load_script_module(f"_act_{script_path.parent.name}_{int(time.time_ns())}", script_path)
    buf = io.StringIO()
    with monkeypatch.context() as mp:
        for k, v in env_overrides.items():
            mp.setenv(k, v)
        mp.setattr(sys, "stdin", io.StringIO(stdin_text))
        with redirect_stdout(buf):
            rc = int(mod.main())
    raw_out = buf.getvalue().strip()
    return rc, (json.loads(raw_out) if raw_out else {})


def _build_isp_samba_ad_server() -> Server:
    """Populate Samba AD with 15 enabled mailboxes, 5 disabled mailboxes, and 15 non-mailbox accounts."""
    ad_base = "DC=ad,DC=bls-isp,DC=net"
    server = Server("mock_v107_isp_ad", get_info=None)
    conn = Connection(server, client_strategy=MOCK_SYNC)
    conn.bind()

    conn.strategy.add_entry(
        f"CN=ldapservice,CN=Users,{ad_base}",
        {
            "objectClass": ["top", "person", "user"],
            "cn": "ldapservice",
            "sAMAccountName": "ldapservice",
            "userPassword": "ad-bind-secret",
        },
    )
    conn.strategy.add_entry(
        f"CN=Domain Users,CN=Users,{ad_base}",
        {
            "objectClass": ["top", "group"],
            "cn": "Domain Users",
            "sAMAccountName": "Domain Users",
            "objectSid": "S-1-5-21-909090-808080-707070-513",
        },
    )

    for u in ENABLED_MB_USERS + DISABLED_MB_USERS + NON_MAILBOX_AD_USERS:
        conn.strategy.add_entry(
            f"CN={u},CN=Users,{ad_base}",
            {
                "objectClass": ["top", "person", "organizationalPerson", "user"],
                "cn": u,
                "displayName": f"ISP Account {u}",
                "sAMAccountName": u,
                "userPrincipalName": f"{u}@ad.bls-isp.net",
                "mail": f"{u}@ad.bls-isp.net",
                "primaryGroupID": "513",
                "userPassword": f"Pass!{u}",
            },
        )

    conn.unbind()
    return server


def _make_spam_bytes(seq_num: int, rcpt_email: str, msg_dt: datetime) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"Spammer {seq_num:03d} <campaign-{seq_num:03d}@bulk-spam.example>"
    msg["To"] = rcpt_email
    msg["Subject"] = f"ISP Quarantined Offer #{seq_num:03d}"
    msg["Date"] = format_datetime(msg_dt)
    msg["Message-ID"] = f"<v107-spam-{rcpt_email}-{seq_num:04d}@bulk-spam.example>"
    msg["X-Spam-Score"] = "9.4"
    msg.set_content("Large spam body skipped by BODY.PEEK[HEADER] batch fetch.\n" * 8)
    return msg.as_bytes(policy=SMTP_POLICY)


def _start_fastapi_http_bridge(client: TestClient) -> tuple[http.server.ThreadingHTTPServer, int]:
    """Expose `TestClient(app)` on `127.0.0.1:<ephemeral>` for `imageroot/actions/*` stdlib HTTP calls."""
    call_lock = threading.Lock()

    class _BridgeHandler(http.server.BaseHTTPRequestHandler):
        def _forward(self, method: str) -> None:
            content_len = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_len) if content_len > 0 else None
            req_headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in {"host", "content-length", "connection"}
            }
            with call_lock:
                resp = client.request(method, self.path, content=body, headers=req_headers)
            self.send_response(resp.status_code)
            self.send_header("Content-Type", resp.headers.get("content-type", "application/json"))
            self.send_header("Content-Length", str(len(resp.content)))
            self.end_headers()
            self.wfile.write(resp.content)

        def do_GET(self) -> None:  # noqa: N802
            self._forward("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._forward("POST")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _BridgeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, int(srv.server_address[1])


@pytest.fixture()
def v107_isp_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin up ISP-scale Samba AD + TCP MockImapServer (35 AD users, 250 spam messages in mb01)."""
    clear_quarantine_cache()
    reset_scheduled_digest_backoff()

    state_dir = tmp_path / "state"
    data_dir = state_dir / "data"
    config_dir = state_dir / "config"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    sieve_srv = MockManageSieveServer(master_user="vmail", master_password="dovecot-master-secret")
    imap_srv = MockImapServer(master_user="vmail", master_password="dovecot-master-secret")
    smtp_srv = MockSmtpSubmissionServer()

    sieve_host, sieve_port = sieve_srv.start()
    _, imap_port = imap_srv.start()
    _, smtp_port = smtp_srv.start()

    # Arm a 2.5s delay + auth failure trap on ALL 15 non-mailbox AD accounts AND ALL 5 disabled mailboxes.
    # If any scan touches even a few of these accounts, the test will fail both auth-log and timing checks!
    for forbidden_user in NON_MAILBOX_AD_USERS + DISABLED_MB_USERS:
        imap_srv.set_user_login_delay(forbidden_user, 2.5)
        imap_srv.set_master_login_failure(forbidden_user, True)

    now_utc = datetime.now(timezone.utc)
    old_dt = now_utc - timedelta(days=45)
    recent_dt = now_utc - timedelta(days=2)
    old_internaldate = format_imap_internaldate(old_dt)
    recent_internaldate = format_imap_internaldate(recent_dt)

    # Seed 250 spam messages into `mb01` Junk: 100 older than 30 days (45d) + 150 recent (2d)
    for seq in range(1, 101):
        imap_srv.seed_message(
            "mb01",
            "Junk",
            _make_spam_bytes(seq, "mb01@bls-isp.net", old_dt + timedelta(minutes=seq)),
            internaldate=old_internaldate,
        )
    for seq in range(101, 251):
        imap_srv.seed_message(
            "mb01",
            "Junk",
            _make_spam_bytes(seq, "mb01@bls-isp.net", recent_dt + timedelta(minutes=seq)),
            internaldate=recent_internaldate,
        )

    # Seed 2 recent spam messages each into `mb02`, `mb03`, `mb04` (6 total; `mb05`..`mb15` have empty Junk)
    for idx, mb_user in enumerate(("mb02", "mb03", "mb04"), start=1):
        for copy_no in (1, 2):
            seq_id = 300 + idx * 10 + copy_no
            imap_srv.seed_message(
                mb_user,
                "Junk",
                _make_spam_bytes(seq_id, f"{mb_user}@bls-isp.net", recent_dt),
                internaldate=recent_internaldate,
            )

    token = "v107-isp-internal-token"
    (config_dir / "internal.token").write_text(f"{token}\n", encoding="utf-8")
    (config_dir / "master.creds").write_text("vmail:dovecot-master-secret\n", encoding="utf-8")
    (config_dir / "discovery_mail.env").write_text(
        f"MAIL_HOST={sieve_host}\n"
        f"MAIL_IMAP_PORT={imap_port}\n"
        f"MAIL_SIEVE_PORT={sieve_port}\n"
        f"MAIL_SUBMISSION_PORT={smtp_port}\n"
        "MAIL_MODULE_ID=mail1\n",
        encoding="utf-8",
    )
    (config_dir / "discovery_ldap.env").write_text(
        "LDAP_HOST=127.0.0.1\n"
        "LDAP_PORT=389\n"
        "LDAP_BASE=DC=ad,DC=bls-isp,DC=net\n"
        "LDAP_USER=CN=ldapservice,CN=Users,DC=ad,DC=bls-isp,DC=net\n"
        "LDAP_PASS=ad-bind-secret\n"
        "LDAP_SCHEMA=ad\n"
        "LDAP_DOMAIN=ad.bls-isp.net\n",
        encoding="utf-8",
    )

    # Authoritative `ns8-mail` cache: ONLY the 15 enabled + 5 disabled real mailboxes (zero non-mailbox AD users)
    mailboxes_entries: list[dict[str, Any]] = [
        {
            "user": u,
            "enabled": True,
            "aliases": [f"{u}@bls-isp.net"],
            "postfix_forwards": [],
        }
        for u in ENABLED_MB_USERS
    ] + [
        {
            "user": u,
            "enabled": False,
            "aliases": [f"{u}@bls-isp.net"],
            "postfix_forwards": [],
        }
        for u in DISABLED_MB_USERS
    ]
    (config_dir / "mailboxes_cache.json").write_text(
        json.dumps({"updated_at": "2026-11-24T07:00:00Z", "mailboxes": mailboxes_entries}),
        encoding="utf-8",
    )

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("APP_SECRET_KEY", "v107-isp-secret-key-0123456789abcdef")
    monkeypatch.setenv("PORTAL_ENABLED", "True")
    monkeypatch.setenv("PORTAL_HOSTNAME", "toolbox.bls-isp.net")
    monkeypatch.setenv("MILTER_ENABLED", "0")
    monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "3600")

    ad_server = _build_isp_samba_ad_server()

    def _ad_factory(_cfg: LdapSettings, bind_dn: str | None, bind_pw: str | None) -> Connection:
        return Connection(
            ad_server,
            user=bind_dn,
            password=bind_pw or "",
            client_strategy=MOCK_SYNC,
            auto_bind=False,
        )

    set_ldap_connection_factory(_ad_factory)

    try:
        yield {
            "state_dir": state_dir,
            "config_dir": config_dir,
            "data_dir": data_dir,
            "headers": {"X-Internal-Token": token},
            "imap": imap_srv,
            "smtp": smtp_srv,
        }
    finally:
        clear_quarantine_cache()
        reset_scheduled_digest_backoff()
        set_ldap_connection_factory(None)
        imap_srv.stop()
        smtp_srv.stop()
        sieve_srv.stop()


def test_isp_scale_list_quarantine_batch_fetch_ad_skip_and_30s_cache_e2e(
    v107_isp_stack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `<3s` parallel batch UID FETCH, 0 non-mailbox/disabled IMAP logins, `<50ms` cache & `force_refresh`."""
    imap_srv: MockImapServer = v107_isp_stack["imap"]
    state_dir: Path = v107_isp_stack["state_dir"]
    hdrs: dict[str, str] = v107_isp_stack["headers"]

    list_quar_script = ACTIONS_DIR / "list-quarantine" / "20read"
    list_quar_out_schema = json.loads(
        (ACTIONS_DIR / "list-quarantine" / "validate-output.json").read_text(encoding="utf-8")
    )

    # 0. Unconfigured OS-process check: `list-quarantine/20read` exits 0 immediately with empty schema-valid JSON
    unconf_env = dict(os.environ)
    unconf_env["MAIL_MODULE_UUID"] = ""
    unconf_env["WEB_TCP_PORT"] = ""
    unconf_env["AGENT_STATE_DIR"] = str(state_dir)
    unconf_proc = subprocess.run(
        [sys.executable, str(list_quar_script)],
        input="",
        env=unconf_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert unconf_proc.returncode == 0, unconf_proc.stderr
    unconf_json = json.loads(unconf_proc.stdout)
    jsonschema.validate(instance=unconf_json, schema=list_quar_out_schema)
    assert unconf_json["items"] == []
    assert imap_srv.auth_attempts_log == []

    with TestClient(app) as client:
        bridge_srv, bridge_port = _start_fastapi_http_bridge(client)
        try:
            act_env = {
                "MAIL_MODULE_UUID": "mail-uuid-isp-01",
                "WEB_TCP_PORT": str(bridge_port),
                "AGENT_STATE_DIR": str(state_dir),
            }

            # 1. Cold ISP-scale `list-quarantine/20read` scan across 15 enabled mailboxes (250 items in `mb01`)
            t0 = time.perf_counter()
            rc_1, out_1 = _invoke_action_inprocess(
                list_quar_script,
                stdin_text="",
                env_overrides=act_env,
                monkeypatch=monkeypatch,
            )
            elapsed_cold = time.perf_counter() - t0

            assert rc_1 == 0
            assert elapsed_cold < 3.0, f"Cold ISP quarantine scan took {elapsed_cold:.2f}s (expected < 3.0s)"
            jsonschema.validate(instance=out_1, schema=list_quar_out_schema)

            # 100 newest capped from `mb01` + 6 from `mb02`..`mb04` = 106 total items
            assert len(out_1["items"]) == 106

            # ZERO IMAP auth attempts against the 15 non-mailbox AD accounts OR the 5 disabled mailboxes!
            authed_set = set(imap_srv.auth_attempts_log)
            assert authed_set == set(ENABLED_MB_USERS)
            assert authed_set.isdisjoint(NON_MAILBOX_AD_USERS)
            assert authed_set.isdisjoint(DISABLED_MB_USERS)

            # `mb01` has 250 messages in Junk -> capped to 100 newest and fetched in 2 chunks of 50 (NOT 250 fetches!)
            mb01_fetches = [
                cmd for cmd in imap_srv.fetch_commands_log if cmd[0] == "mb01" and cmd[1] == "Junk"
            ]
            assert len(mb01_fetches) <= 2, f"Expected <= 2 batch UID FETCH commands on mb01, got {len(mb01_fetches)}"
            assert len(mb01_fetches) == 2
            for _u, _f, seq_spec, fetch_spec in mb01_fetches:
                assert len(seq_spec.split(",")) == 50
                assert fetch_spec == "(UID BODY.PEEK[HEADER])"

            # 2. Warm 30-second cache hit: `< 50ms` response and ZERO new TCP IMAP connections or fetches!
            imap_srv.auth_attempts_log.clear()
            imap_srv.fetch_commands_log.clear()

            t_warm = time.perf_counter()
            warm_http = client.get("/api/internal/quarantine", headers=hdrs)
            elapsed_backend_warm = time.perf_counter() - t_warm
            assert warm_http.status_code == 200
            assert elapsed_backend_warm < 0.05, (
                f"Cached `/api/internal/quarantine` took {elapsed_backend_warm * 1000:.1f}ms (expected < 50ms)"
            )

            rc_2, out_2 = _invoke_action_inprocess(
                list_quar_script,
                stdin_text="{}",
                env_overrides=act_env,
                monkeypatch=monkeypatch,
            )
            assert rc_2 == 0
            assert len(out_2["items"]) == 106
            assert imap_srv.auth_attempts_log == []
            assert imap_srv.fetch_commands_log == []

            # 3. Manual Refresh (`{"force_refresh": true}`) bypasses 30s cache and runs a fresh batch scan
            rc_3, out_3 = _invoke_action_inprocess(
                list_quar_script,
                stdin_text='{"force_refresh": true}',
                env_overrides=act_env,
                monkeypatch=monkeypatch,
            )
            assert rc_3 == 0
            assert len(out_3["items"]) == 106
            assert set(imap_srv.auth_attempts_log) == set(ENABLED_MB_USERS)
            mb01_refresh_fetches = [
                cmd for cmd in imap_srv.fetch_commands_log if cmd[0] == "mb01" and cmd[1] == "Junk"
            ]
            assert len(mb01_refresh_fetches) == 2
        finally:
            bridge_srv.shutdown()
            bridge_srv.server_close()


def test_run_digest_now_since_14_days_filter_inbox_append_and_unconfigured_guard_e2e(
    v107_isp_stack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `run-digest-now/20write` unconfigured exit-0 guard, `SINCE` 14-day filter, and direct `INBOX` APPEND."""
    imap_srv: MockImapServer = v107_isp_stack["imap"]
    smtp_srv: MockSmtpSubmissionServer = v107_isp_stack["smtp"]
    state_dir: Path = v107_isp_stack["state_dir"]

    run_dig_script = ACTIONS_DIR / "run-digest-now" / "20write"
    run_dig_out_schema = json.loads(
        (ACTIONS_DIR / "run-digest-now" / "validate-output.json").read_text(encoding="utf-8")
    )

    # 1. Unconfigured guard: `MAIL_MODULE_UUID=""` exits 0 with zeroed digest summary
    unconf_env = dict(os.environ)
    unconf_env["MAIL_MODULE_UUID"] = ""
    unconf_env["WEB_TCP_PORT"] = ""
    unconf_env["AGENT_STATE_DIR"] = str(state_dir)
    unconf_proc = subprocess.run(
        [sys.executable, str(run_dig_script)],
        input='{"user": ""}',
        env=unconf_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert unconf_proc.returncode == 0, unconf_proc.stderr
    unconf_out = json.loads(unconf_proc.stdout)
    jsonschema.validate(instance=unconf_out, schema=run_dig_out_schema)
    assert unconf_out == {"users_checked": 0, "digests_sent": 0, "items_included": 0}

    # Seed additional 45-day-old spam into `mb05` (20 old, 0 recent) and `mb02` (10 old + 2 recent)
    # to prove `UID SEARCH SINCE` 14-day filtering excludes every message older than 14 days!
    old_45d_dt = datetime.now(timezone.utc) - timedelta(days=45)
    old_45d_internaldate = format_imap_internaldate(old_45d_dt)
    for seq in range(401, 421):
        imap_srv.seed_message(
            "mb05",
            "Junk",
            _make_spam_bytes(seq, "mb05@bls-isp.net", old_45d_dt),
            internaldate=old_45d_internaldate,
        )
    for seq in range(501, 511):
        imap_srv.seed_message(
            "mb02",
            "Junk",
            _make_spam_bytes(seq, "mb02@bls-isp.net", old_45d_dt),
            internaldate=old_45d_internaldate,
        )

    imap_srv.auth_attempts_log.clear()
    imap_srv.search_criteria_log.clear()
    imap_srv.fetch_commands_log.clear()
    smtp_srv.clear()

    with TestClient(app) as client:
        bridge_srv, bridge_port = _start_fastapi_http_bridge(client)
        try:
            act_env = {
                "MAIL_MODULE_UUID": "mail-uuid-isp-01",
                "WEB_TCP_PORT": str(bridge_port),
                "AGENT_STATE_DIR": str(state_dir),
            }

            rc, dig_out = _invoke_action_inprocess(
                run_dig_script,
                stdin_text='{"user": ""}',
                env_overrides=act_env,
                monkeypatch=monkeypatch,
            )
            assert rc == 0
            jsonschema.validate(instance=dig_out, schema=run_dig_out_schema)

            # 15 enabled mailboxes checked; `mb05` (20 old-only) gets 0 digests;
            # `mb01` caps at 100 newest recent + `mb02` (2 recent, 10 old ignored) + `mb03` (2) + `mb04` (2) = 106!
            assert dig_out == {
                "users_checked": 15,
                "digests_sent": 4,
                "items_included": 106,
            }

            # Zero non-mailbox AD or disabled mailbox auth attempts; zero SMTP calls (all APPENDed to INBOX)
            assert set(imap_srv.auth_attempts_log) == set(ENABLED_MB_USERS)
            assert smtp_srv.get_messages() == []
            assert imap_srv.list_folder_messages("mb05", "INBOX") == []

            # Every Junk search used `SINCE DD-Mon-YYYY` (English ASCII month) rather than `ALL`
            junk_searches = [
                crit for _u, folder, crit in imap_srv.search_criteria_log if folder == "Junk"
            ]
            assert len(junk_searches) == 15
            for crit in junk_searches:
                assert re.search(
                    r'\bSINCE\s+"?\d{2}-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{4}"?',
                    crit,
                ), f"Expected RFC 3501 English ASCII SINCE criterion, got {crit!r}"

            # Verify digest landed directly in `mb01` & `mb02` INBOX via IMAP APPEND with old messages excluded
            mb01_inbox = imap_srv.list_folder_messages("mb01", "INBOX")
            assert len(mb01_inbox) == 1
            parsed_digest = email.message_from_bytes(mb01_inbox[0].raw_bytes, policy=email.policy.default)
            assert parsed_digest["X-NS8-Mail-Toolbox"] == "quarantine-digest"
            assert parsed_digest["Subject"] == "Spam Quarantine Digest (100 new message(s))"
            plain_part = next(p for p in parsed_digest.iter_parts() if p.get_content_type() == "text/plain")
            assert "+ 50 older quarantined messages" in plain_part.get_content()

            mb02_inbox = imap_srv.list_folder_messages("mb02", "INBOX")
            assert len(mb02_inbox) == 1
            parsed_mb02 = email.message_from_bytes(mb02_inbox[0].raw_bytes, policy=email.policy.default)
            assert parsed_mb02["Subject"] == "Spam Quarantine Digest (2 new message(s))"
        finally:
            bridge_srv.shutdown()
            bridge_srv.server_close()


def test_scheduled_digest_transient_failure_does_not_mark_done_and_retries_after_15m_backoff(
    v107_isp_stack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient scheduled digest errors must NOT set `digest_last_scheduled_date` and must retry after 15m."""
    _ = v107_isp_stack
    with TestClient(app):
        settings = get_settings()
        update_runtime_settings(
            {
                "digest_enabled": True,
                "digest_time": "07:30",
                "timezone": "Europe/Berlin",
            },
            settings.config_dir,
        )
        reset_scheduled_digest_backoff()

        # 2026-11-24 07:00 UTC == 08:00 CET (past 07:30 scheduled digest time)
        now_utc = datetime(2026, 11, 24, 7, 0, 0, tzinfo=timezone.utc)

        transient_calls = 0

        def _failing_digest() -> dict[str, Any]:
            nonlocal transient_calls
            transient_calls += 1
            raise RuntimeError("Transient Dovecot master socket timeout")

        with monkeypatch.context() as mp:
            mp.setattr(rec, "run_quarantine_digest", _failing_digest)

            # 1. First scheduled tick fails transiently -> returns False and does NOT mark today done!
            assert check_and_run_scheduled_digest(now_utc) is False
            assert transient_calls == 1
            assert get_meta("digest_last_scheduled_date", "", settings.sqlite_path) == ""
            assert rec._last_scheduled_digest_error_mono is not None

            # 2. Next 60s reconciler tick within the 15-minute cooldown -> skipped without hammering IMAP
            assert check_and_run_scheduled_digest(now_utc + timedelta(minutes=1)) is False
            assert transient_calls == 1

        # 3. Advance monotonic error timestamp past the 900s (15m) backoff -> live retry runs and marks today done!
        assert rec._last_scheduled_digest_error_mono is not None
        rec._last_scheduled_digest_error_mono -= 905.0

        assert check_and_run_scheduled_digest(now_utc + timedelta(minutes=16)) is True
        assert get_meta("digest_last_scheduled_date", "", settings.sqlite_path) == "2026-11-24"
        assert rec._last_scheduled_digest_error_mono is None

        # 4. Subsequent tick on the same local date is a no-op
        assert check_and_run_scheduled_digest(now_utc + timedelta(minutes=30)) is False
