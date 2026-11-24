"""v1.0.7 tests: Quarantine & Digest Timeout Fixes, Batch UID FETCH, Parallel Scan, Cache & Retry."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
import email.policy
import imaplib
import json
from pathlib import Path
import threading
import time
from typing import Any

from fastapi.testclient import TestClient
from ldap3 import Connection, MOCK_SYNC
import pytest

from app.backend.config import LdapSettings, MailSettings, get_settings, update_runtime_settings
from app.backend.db import add_sender_allowlist, get_meta
from app.backend.ldap_client import set_ldap_connection_factory
from app.backend.main import app
import app.backend.quarantine_service as qs
from app.backend.quarantine_service import (
    clear_quarantine_cache,
    fetch_user_junk_items,
    list_admin_quarantine,
    open_master_imap,
    rescue_allowlisted_junk_messages,
    run_quarantine_digest,
    set_imap_master_factory,
    set_smtp_sender,
)
import app.backend.reconciler as rec
from app.backend.reconciler import check_and_run_scheduled_digest, reset_scheduled_digest_backoff
from app.backend.service import build_base_mailbox_map
from app.backend.tests.conftest import build_rfc2307_mock_server


def _spam_msg(uid_num: int, rcpt: str = "max.muster@firma.de") -> bytes:
    msg = EmailMessage()
    msg["From"] = f"Sender {uid_num} <spam{uid_num}@example.org>"
    msg["To"] = rcpt
    msg["Subject"] = f"Quarantined Message #{uid_num}"
    msg["Message-ID"] = f"<q-{uid_num}@example.org>"
    msg["Date"] = "Tue, 24 Nov 2026 06:30:00 +0000"
    msg["X-Spam-Score"] = "8.5"
    msg.set_content("Body payload.")
    return msg.as_bytes(policy=email.policy.SMTP)


class BatchTrackingImapConn:
    """Thread-safe IMAP mock recording opened users, SEARCH criteria, and batch UID FETCH chunks."""

    def __init__(
        self,
        folders: dict[str, dict[str, dict[str, bytes]]],
        user: str,
        *,
        log_lock: threading.Lock,
        opened_users: list[str],
        search_calls: list[tuple[Any, ...]],
        fetch_calls: list[tuple[str, str]],
        slow_users: dict[str, float] | None = None,
    ) -> None:
        self.folders = folders
        self.user = user
        self.current_folder = "INBOX"
        self.log_lock = log_lock
        self.opened_users = opened_users
        self.search_calls = search_calls
        self.fetch_calls = fetch_calls
        self.slow_users = slow_users or {}
        with self.log_lock:
            self.opened_users.append(user)

    def select(self, folder: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        _ = readonly
        delay = self.slow_users.get(self.user, 0.0)
        if delay > 0:
            time.sleep(delay)
        clean = folder.strip('"')
        uf = self.folders.get(self.user, {"INBOX": {}, "Junk": {}})
        if clean not in uf:
            return "NO", [b"Missing"]
        self.current_folder = clean
        return "OK", [str(len(uf[clean])).encode("ascii")]

    def uid(self, cmd: str, *args: Any) -> tuple[str, list[Any]]:
        upper = cmd.upper()
        fmap = self.folders.get(self.user, {}).get(self.current_folder, {})
        if upper == "SEARCH":
            with self.log_lock:
                self.search_calls.append(args)
            uids = sorted(fmap.keys(), key=lambda x: int(x))
            return "OK", [" ".join(uids).encode("ascii")]
        if upper == "FETCH":
            uid_spec = str(args[0])
            spec = str(args[1]) if len(args) > 1 else "(RFC822)"
            with self.log_lock:
                self.fetch_calls.append((uid_spec, spec))
            out: list[Any] = []
            for idx, target_uid in enumerate(uid_spec.split(","), start=1):
                u_clean = target_uid.strip()
                if u_clean not in fmap:
                    continue
                raw = fmap[u_clean]
                sep = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
                hdr_bytes = raw.split(sep, 1)[0] + b"\r\n\r\n"
                out.append(
                    (
                        f"{idx} (UID {u_clean} BODY[HEADER] {{{len(hdr_bytes)}}})".encode("ascii"),
                        hdr_bytes,
                    )
                )
            return ("OK", out) if out else ("NO", [])
        return "BAD", []

    def append(self, mailbox: str, _flags: str, _dt: str, message: bytes) -> tuple[str, list[bytes]]:
        clean = mailbox.strip('"')
        uf = self.folders.setdefault(self.user, {"INBOX": {}, "Junk": {}})
        uf.setdefault(clean, {})[str(len(uf[clean]) + 5000)] = bytes(message)
        return "OK", [b"Appended"]

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b"Bye"]


def test_authoritative_mailboxes_cache_excludes_non_mailbox_ad_accounts_and_skips_disabled(
    toolbox_env: dict[str, Path],
) -> None:
    """Non-mailbox LDAP/AD accounts (`krbtgt`, `Guest`, `vpn-svc`) & disabled mailboxes must never hit IMAP."""
    ldap_srv = build_rfc2307_mock_server()
    conn = Connection(
        ldap_srv,
        user="cn=ldapservice,dc=firma,dc=de",
        password="ldap_svc_secret",
        client_strategy=MOCK_SYNC,
    )
    conn.bind()
    for svc_uid in ("krbtgt", "Guest", "vpn-svc"):
        conn.strategy.add_entry(
            f"uid={svc_uid},ou=People,dc=firma,dc=de",
            {
                "objectClass": ["inetOrgPerson", "posixAccount"],
                "uid": svc_uid,
                "cn": svc_uid,
                "sn": svc_uid,
                "uidNumber": "9999",
                "gidNumber": "5001",
                "userPassword": "x",
            },
        )
    conn.unbind()
    set_ldap_connection_factory(
        lambda _s, bind_dn, bind_pw: Connection(
            ldap_srv,
            user=bind_dn,
            password=bind_pw or "",
            client_strategy=MOCK_SYNC,
            auto_bind=False,
        )
    )

    cfg_dir = toolbox_env["config_dir"]
    cache_file = cfg_dir / "mailboxes_cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "updated_at": "2026-11-24T08:00:00Z",
                "mailboxes": [
                    {"user": "max.muster", "enabled": True, "aliases": ["m.muster@firma.de"], "postfix_forwards": []},
                    {"user": "anna", "enabled": True, "aliases": ["empfang@firma.de"], "postfix_forwards": []},
                    {"user": "erika.vertretung", "enabled": False, "aliases": [], "postfix_forwards": []},
                ],
            }
        ),
        encoding="utf-8",
    )

    base_map = build_base_mailbox_map()
    assert set(base_map.keys()) == {"max.muster", "anna", "erika.vertretung"}
    assert "krbtgt" not in base_map
    assert "Guest" not in base_map
    assert "vpn-svc" not in base_map

    # Also add stale allowlist rows for disabled `erika.vertretung` and non-mailbox `vpn-svc`
    settings = get_settings()
    add_sender_allowlist("erika.vertretung", "vip@partner.de", settings.sqlite_path)
    add_sender_allowlist("vpn-svc", "vip@partner.de", settings.sqlite_path)
    add_sender_allowlist("max.muster", "vip@partner.de", settings.sqlite_path)

    folders: dict[str, dict[str, dict[str, bytes]]] = {
        "max.muster": {"INBOX": {}, "Junk": {"1": _spam_msg(1)}},
        "anna": {"INBOX": {}, "Junk": {}},
    }
    lock = threading.Lock()
    opened_users: list[str] = []
    search_calls: list[tuple[Any, ...]] = []
    fetch_calls: list[tuple[str, str]] = []

    set_imap_master_factory(
        lambda u, _cfg: BatchTrackingImapConn(
            folders,
            u,
            log_lock=lock,
            opened_users=opened_users,
            search_calls=search_calls,
            fetch_calls=fetch_calls,
        )
    )

    q_data = list_admin_quarantine(force_refresh=True)
    assert len(q_data["items"]) == 1
    assert set(opened_users) == {"max.muster", "anna"}

    opened_users.clear()
    rescue_allowlisted_junk_messages(settings)
    assert opened_users == ["max.muster"]

    opened_users.clear()
    dig_res = run_quarantine_digest()
    assert dig_res["users_checked"] == 2
    assert dig_res["digests_sent"] == 1
    assert set(opened_users).issubset({"max.muster", "anna"})
    assert "erika.vertretung" not in opened_users

    # Fallback check: when mailboxes_cache.json is missing/empty, LDAP directory fallback still works!
    cache_file.unlink()
    fallback_map = build_base_mailbox_map()
    assert "max.muster" in fallback_map
    assert "vpn-svc" in fallback_map


def test_batch_uid_fetch_chunks_max_uids_cap_and_since_search(
    toolbox_env: dict[str, Path],
) -> None:
    """130 Junk UIDs must slice newest 100 (`31..130`), search `SINCE DD-Mon-YYYY`, and batch in 2 chunks of 50."""
    junk_map = {str(uid): _spam_msg(uid) for uid in range(1, 131)}
    folders = {"max.muster": {"INBOX": {}, "Junk": junk_map}}
    lock = threading.Lock()
    opened_users: list[str] = []
    search_calls: list[tuple[Any, ...]] = []
    fetch_calls: list[tuple[str, str]] = []

    set_imap_master_factory(
        lambda u, _cfg: BatchTrackingImapConn(
            folders,
            u,
            log_lock=lock,
            opened_users=opened_users,
            search_calls=search_calls,
            fetch_calls=fetch_calls,
        )
    )

    settings = get_settings()
    items = fetch_user_junk_items(
        "max.muster",
        "max.muster@firma.de",
        settings.mail,
        max_uids=100,
        since_days=14,
    )
    assert len(items) == 100
    assert {int(it["uid"]) for it in items} == set(range(31, 131))

    # Verify RFC 3501 English ASCII `SINCE DD-Mon-YYYY` search criterion
    assert len(search_calls) == 1
    assert search_calls[0][1] == "SINCE"
    assert len(search_calls[0][2]) == 11  # e.g. `10-Nov-2026`

    # Verify exactly 2 batch `UID FETCH` commands of 50 UIDs each (zero N+1 per-UID round-trips!)
    assert len(fetch_calls) == 2
    assert len(fetch_calls[0][0].split(",")) == 50
    assert len(fetch_calls[1][0].split(",")) == 50
    assert fetch_calls[0][1] == "(UID BODY.PEEK[HEADER])"
    assert fetch_calls[1][1] == "(UID BODY.PEEK[HEADER])"


def test_quarantine_30s_cache_force_refresh_and_soft_deadline_on_hung_mailbox(
    toolbox_env: dict[str, Path],
) -> None:
    folders = {
        "max.muster": {"INBOX": {}, "Junk": {"10": _spam_msg(10)}},
        "anna": {"INBOX": {}, "Junk": {"20": _spam_msg(20, "anna@firma.de")}},
        "erika.vertretung": {"INBOX": {}, "Junk": {"30": _spam_msg(30, "vertretung@firma.de")}},
    }
    lock = threading.Lock()
    opened_users: list[str] = []
    search_calls: list[tuple[Any, ...]] = []
    fetch_calls: list[tuple[str, str]] = []
    slow_users: dict[str, float] = {}

    set_imap_master_factory(
        lambda u, _cfg: BatchTrackingImapConn(
            folders,
            u,
            log_lock=lock,
            opened_users=opened_users,
            search_calls=search_calls,
            fetch_calls=fetch_calls,
            slow_users=slow_users,
        )
    )

    headers = {"X-Internal-Token": "internal-test-token-999"}
    with TestClient(app) as client:
        # 1. First request scans all 3 enabled mailboxes in parallel
        r1 = client.get("/api/internal/quarantine", headers=headers)
        assert r1.status_code == 200
        assert len(r1.json()["items"]) == 3
        assert len(opened_users) == 3

        # 2. Second request within 30s serves from cache (0 IMAP connections!) and filters search locally
        opened_users.clear()
        r2 = client.get("/api/internal/quarantine?search=spam20", headers=headers)
        assert r2.status_code == 200
        assert len(r2.json()["items"]) == 1
        assert r2.json()["items"][0]["user"] == "anna"
        assert opened_users == []

        # 3. `force_refresh=true` bypasses cache and re-scans IMAP
        r3 = client.get("/api/internal/quarantine?force_refresh=true", headers=headers)
        assert r3.status_code == 200
        assert len(r3.json()["items"]) == 3
        assert len(opened_users) == 3

    # 4. Soft deadline returns responsive mailboxes immediately when one mailbox hangs
    clear_quarantine_cache()
    slow_users["erika.vertretung"] = 1.5
    t0 = time.monotonic()
    partial = list_admin_quarantine(force_refresh=True, soft_deadline_seconds=0.15)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.8
    returned_users = {it["user"] for it in partial["items"]}
    assert {"max.muster", "anna"}.issubset(returned_users)
    assert "erika.vertretung" not in returned_users


def test_open_master_imap_skips_login_retry_on_authenticationfailed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_imap_master_factory(None)
    calls = {"auth": 0, "login": 0}
    auth_error_msg = {"msg": "NO [AUTHENTICATIONFAILED] Authentication failed."}
    real_imap_error = imaplib.IMAP4.error

    class FakeRawImap4:
        error = real_imap_error

        def __init__(self, host: str, port: int, timeout: float = 6.0) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout

        def authenticate(self, _mech: str, _cb: Any) -> tuple[str, list[bytes]]:
            calls["auth"] += 1
            raise real_imap_error(auth_error_msg["msg"])

        def login(self, _u: str, _p: str) -> tuple[str, list[bytes]]:
            calls["login"] += 1
            return "OK", [b"Logged in"]

        def logout(self) -> tuple[str, list[bytes]]:
            return "BYE", [b"Bye"]

    monkeypatch.setattr(imaplib, "IMAP4", FakeRawImap4)
    mail_cfg = MailSettings(
        host="127.0.0.1",
        imap_port=143,
        sieve_port=4190,
        submission_port=587,
        module_id="mail1",
        master_user="vmail",
        master_password="secret",
        spam_folder="Junk",
        sieve_starttls=False,
        sieve_tls_verify=False,
    )

    # 1. AUTHENTICATIONFAILED must NOT trigger a second `conn.login` tarpit!
    with pytest.raises(imaplib.IMAP4.error):
        with open_master_imap("nonexistent-ad-user", mail_cfg, timeout=5.0):
            pass
    assert calls == {"auth": 1, "login": 0}

    # 2. Pure SASL syntax `BAD` error still falls back to `conn.login`
    auth_error_msg["msg"] = "AUTHENTICATE command error: BAD [b'Unknown SASL mechanism']"
    with open_master_imap("max.muster", mail_cfg, timeout=5.0):
        pass
    assert calls == {"auth": 2, "login": 1}


def test_scheduled_digest_marks_done_only_after_success_with_15m_retry_cooldown(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_scheduled_digest_backoff()
    update_runtime_settings({"digest_enabled": True, "digest_time": "07:30", "timezone": "UTC"})
    settings = get_settings()

    fake_mono = {"now": 1000.0}
    monkeypatch.setattr(rec.time, "monotonic", lambda: fake_mono["now"])

    state = {"should_fail": True, "calls": 0}

    def _mock_run_digest(*, user_filter: str = "") -> dict[str, int]:
        _ = user_filter
        state["calls"] += 1
        if state["should_fail"]:
            raise RuntimeError("Transient Dovecot restart")
        return {"users_checked": 3, "digests_sent": 1, "items_included": 2}

    monkeypatch.setattr(rec, "run_quarantine_digest", _mock_run_digest)
    morning = datetime(2026, 11, 24, 7, 35, tzinfo=timezone.utc)

    # 1. Transient failure at 07:35 does NOT mark today done!
    assert check_and_run_scheduled_digest(now_utc=morning) is False
    assert state["calls"] == 1
    assert get_meta("digest_last_scheduled_date", "", settings.sqlite_path) == ""

    # 2. 60 seconds later, 15m cooldown suppresses immediate retry storm
    fake_mono["now"] += 60.0
    assert check_and_run_scheduled_digest(now_utc=morning) is False
    assert state["calls"] == 1

    # 3. 15+ minutes later, retry runs, succeeds, and records `2026-11-24`
    state["should_fail"] = False
    fake_mono["now"] += 900.0
    assert check_and_run_scheduled_digest(now_utc=morning) is True
    assert state["calls"] == 2
    assert get_meta("digest_last_scheduled_date", "", settings.sqlite_path) == "2026-11-24"

    # 4. Subsequent ticks on the same calendar day are no-ops
    fake_mono["now"] += 1000.0
    assert check_and_run_scheduled_digest(now_utc=morning) is False
    assert state["calls"] == 2
