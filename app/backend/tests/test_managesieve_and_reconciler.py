"""Integration tests for TCP ManageSieve protocol, SOGo coexistence, and 5-min reconciler."""

from __future__ import annotations

import base64
from pathlib import Path
import pytest

from app.backend.db import get_vacation_rule, list_audit_entries
from app.backend.models import VacationUpdateRequest
from app.backend.reconciler import run_reconcile_pass
from app.backend.service import list_mailboxes_with_status, update_mailbox_vacation
from app.backend.sieve_client import (
    ManageSieveAuthError,
    ManageSieveClient,
    build_master_sasl_plain,
)
from app.backend.tests.mock_managesieve import MockManageSieveServer


def test_master_user_sasl_plain_wire_format_and_auth(sieve_server: MockManageSieveServer) -> None:
    b64 = build_master_sasl_plain("max.muster", "vmail", "dovecot-master-pass")
    assert base64.b64decode(b64) == b"\x00max.muster*vmail\x00dovecot-master-pass"

    sieve_server.seed_script(
        "max.muster",
        "sogo",
        'require ["fileinto"];\nfileinto "Archiv — Grüße";\n',
        active=True,
    )

    with ManageSieveClient(
        host=sieve_server.host,
        port=sieve_server.port,
        master_user="vmail",
        master_password="dovecot-master-pass",
        target_user="max.muster",
    ) as client:
        scripts, active = client.listscripts()
        assert scripts == ["sogo"]
        assert active == "sogo"
        content = client.getscript("sogo")
        assert "Archiv — Grüße" in content

    assert sieve_server.auth_events[-1] == {
        "authzid": "",
        "authcid": "max.muster*vmail",
        "password": "dovecot-master-pass",
    }

    # Wrong master password must raise ManageSieveAuthError
    with pytest.raises(ManageSieveAuthError):
        with ManageSieveClient(
            host=sieve_server.host,
            port=sieve_server.port,
            master_user="vmail",
            master_password="wrong-password",
            target_user="max.muster",
        ):
            pass

    # Guard against overwriting or deleting sogo.sieve
    with ManageSieveClient(
        host=sieve_server.host,
        port=sieve_server.port,
        master_user="vmail",
        master_password="dovecot-master-pass",
        target_user="max.muster",
    ) as client:
        with pytest.raises(ValueError, match="sogo"):
            client.putscript("sogo", "# overwrite")
        with pytest.raises(ValueError, match="sogo"):
            client.deletescript("sogo")


def test_sogo_coexistence_without_and_with_vacation(
    toolbox_env: dict[str, Path],
    sieve_server: MockManageSieveServer,
) -> None:
    # Case 1: max.muster has SOGo folder filter WITHOUT vacation -> include :personal "sogo";
    sogo_filter_only = 'require ["fileinto"];\nif header :contains "Subject" "[Invoice]" { fileinto "Invoices"; }\n'
    sieve_server.seed_script("max.muster", "sogo", sogo_filter_only, active=True)

    # Case 2: erika.vertretung has SOGo personal vacation -> omit include :personal "sogo";
    sogo_with_vac = (
        'require ["vacation"];\n'
        'vacation :days 7 :addresses ["vertretung@firma.de"] "Bin im Urlaub";\n'
    )
    sieve_server.seed_script("erika.vertretung", "sogo", sogo_with_vac, active=True)

    # Enable central sick-leave on max.muster
    mb_max = update_mailbox_vacation(
        user="max.muster",
        payload=VacationUpdateRequest(
            enabled=True,
            start_date="2026-11-20",
            end_date="2026-11-27",
            subject="Abwesenheitsnotiz: Max Mustermann",
            body="Vielen Dank für Ihre E-Mail (Grüße aus der Zentrale).",
            forward_enabled=True,
            forward_address="vertretung@firma.de",
            keep_copy=True,
        ),
        actor="anna",
        source="portal",
    )
    assert mb_max["vacation"]["enabled"] is True
    assert mb_max["vacation"]["active_now"] is True
    assert mb_max["sogo_status"]["has_sogo_script"] is True
    assert mb_max["sogo_status"]["is_active"] is False
    assert mb_max["sogo_status"]["vacation_enabled"] is False

    max_state = sieve_server.get_mailbox_state("max.muster")
    assert max_state.active_script == "toolbox-vacation"
    assert max_state.scripts["sogo"] == sogo_filter_only  # sogo.sieve untouched!
    uploaded_max = max_state.scripts["toolbox-vacation"]
    assert 'include :personal "sogo";' in uploaded_max
    assert '"max.muster@firma.de"' in uploaded_max
    assert '"m.muster@firma.de"' in uploaded_max
    assert '"vertrieb-leitung@firma.de"' in uploaded_max

    # Verify CHECKSCRIPT ran before PUTSCRIPT and SETACTIVE
    max_cmds = [cmd for u, cmd, _ in sieve_server.command_history if u == "max.muster"]
    assert "CHECKSCRIPT" in max_cmds
    assert max_cmds.index("CHECKSCRIPT") < max_cmds.index("PUTSCRIPT") < max_cmds.index("SETACTIVE")

    # Enable central sick-leave on erika.vertretung (whose sogo.sieve HAS vacation)
    mb_erika = update_mailbox_vacation(
        user="erika.vertretung",
        payload=VacationUpdateRequest(
            enabled=True,
            start_date="",
            end_date="",
            subject="Krankmeldung",
            body="Derzeit außer Haus.",
            forward_enabled=False,
        ),
        actor="anna",
        source="portal",
    )
    assert mb_erika["sogo_status"]["vacation_enabled"] is True
    erika_state = sieve_server.get_mailbox_state("erika.vertretung")
    assert erika_state.active_script == "toolbox-vacation"
    assert erika_state.scripts["sogo"] == sogo_with_vac  # sogo.sieve untouched!
    assert 'include :personal "sogo";' not in erika_state.scripts["toolbox-vacation"]

    # Disable central sick-leave on max.muster -> restores "sogo" as ACTIVE and deletes toolbox-vacation
    mb_max_off = update_mailbox_vacation(
        user="max.muster",
        payload=VacationUpdateRequest(
            enabled=False,
            subject="Abwesenheitsnotiz: Max Mustermann",
            body="Vielen Dank.",
        ),
        actor="anna",
        source="portal",
    )
    assert mb_max_off["vacation"]["enabled"] is False
    assert mb_max_off["sogo_status"]["is_active"] is True
    max_after = sieve_server.get_mailbox_state("max.muster")
    assert max_after.active_script == "sogo"
    assert "toolbox-vacation" not in max_after.scripts
    assert max_after.scripts["sogo"] == sogo_filter_only


def test_reconciler_recovers_sogo_nightly_cron_override_and_expires_ended_rules(
    toolbox_env: dict[str, Path],
    sieve_server: MockManageSieveServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sogo_script = 'require ["fileinto"];\nfileinto "Projects";\n'
    sieve_server.seed_script("max.muster", "sogo", sogo_script, active=True)

    # Set absence from 2026-11-20 to 2026-11-25
    update_mailbox_vacation(
        user="max.muster",
        payload=VacationUpdateRequest(
            enabled=True,
            start_date="2026-11-20",
            end_date="2026-11-25",
            subject="Abwesend",
            body="Bis 25.11. abwesend.",
            forward_enabled=True,
            forward_address="vertretung@firma.de",
            keep_copy=True,
        ),
        actor="anna",
        source="portal",
    )
    assert sieve_server.get_mailbox_state("max.muster").active_script == "toolbox-vacation"

    # Simulate 00:00 nightly `sogo-tool update-autoreply` overriding ACTIVE script back to "sogo"
    sieve_server.simulate_sogo_nightly_cron("max.muster")
    assert sieve_server.get_mailbox_state("max.muster").active_script == "sogo"

    # Reconciler pass on 2026-11-21 must detect override and re-activate toolbox-vacation
    monkeypatch.setenv("TOOLBOX_TODAY_OVERRIDE", "2026-11-21")
    res_recover = run_reconcile_pass()
    assert res_recover == {"reconciled": 1, "expired": 0, "reactivated": 1}
    assert sieve_server.get_mailbox_state("max.muster").active_script == "toolbox-vacation"

    # Second pass with no changes is a clean no-op
    res_noop = run_reconcile_pass()
    assert res_noop == {"reconciled": 1, "expired": 0, "reactivated": 0}

    # Advance date past end_date (2026-11-26) -> reconciler expires rule, restores "sogo", deletes toolbox-vacation
    monkeypatch.setenv("TOOLBOX_TODAY_OVERRIDE", "2026-11-26")
    res_expire = run_reconcile_pass()
    assert res_expire == {"reconciled": 1, "expired": 1, "reactivated": 0}

    final_state = sieve_server.get_mailbox_state("max.muster")
    assert final_state.active_script == "sogo"
    assert "toolbox-vacation" not in final_state.scripts
    assert final_state.scripts["sogo"] == sogo_script

    stored = get_vacation_rule("max.muster")
    assert stored is not None
    assert stored["enabled"] is False
    assert stored["sieve_installed"] is False

    overview = list_mailboxes_with_status()
    assert overview["summary"]["central_vacation_active"] == 0

    audits = list_audit_entries(target_user="max.muster")
    actions = [a["action"] for a in audits]
    assert "vacation_expired" in actions
    assert "vacation_reactivated" in actions


def test_expiry_when_previous_script_removed_falls_back_to_empty_setactive(
    toolbox_env: dict[str, Path],
    sieve_server: MockManageSieveServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sieve_server.seed_script("anna", "old-custom", 'require ["fileinto"];\n', active=True)

    update_mailbox_vacation(
        user="anna",
        payload=VacationUpdateRequest(
            enabled=True,
            start_date="2026-11-20",
            end_date="2026-11-22",
            subject="Kurzurlaub",
            body="Am Montag zurück.",
        ),
        actor="anna",
        source="portal",
    )
    # Remove "old-custom" from server before expiry
    with sieve_server.lock:
        del sieve_server.mailboxes["anna"].scripts["old-custom"]

    monkeypatch.setenv("TOOLBOX_TODAY_OVERRIDE", "2026-11-23")
    res = run_reconcile_pass()
    assert res == {"reconciled": 1, "expired": 1, "reactivated": 0}
    anna_state = sieve_server.get_mailbox_state("anna")
    assert anna_state.active_script is None
    assert "toolbox-vacation" not in anna_state.scripts


def test_managesieve_starttls_upgrade(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone
    import ssl
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))

    with MockManageSieveServer(
        master_user="vmail",
        master_password="tls-secret",
        ssl_context=server_ctx,
    ) as tls_srv:
        tls_srv.seed_script("max.muster", "sogo", 'require ["fileinto"];\n', active=True)
        with ManageSieveClient(
            host=tls_srv.host,
            port=tls_srv.port,
            master_user="vmail",
            master_password="tls-secret",
            target_user="max.muster",
            starttls=True,
            tls_verify=False,
        ) as client:
            scripts, active = client.listscripts()
            assert scripts == ["sogo"]
            assert active == "sogo"
        assert tls_srv.tls_upgraded_connections == 1


def test_reconciler_loop_throttles_sieve_reconcile_while_running_60s_local_checks(
    toolbox_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from typing import Any
    import app.backend.reconciler as rec_mod

    counts = {"sieve": 0, "digest": 0, "connectors": 0, "imapsync": 0}
    fake_clock = {"t": 1000.0}
    waits: list[float] = []

    monkeypatch.setattr(rec_mod, "run_reconcile_pass", lambda: counts.__setitem__("sieve", counts["sieve"] + 1))
    monkeypatch.setattr(
        rec_mod, "check_and_run_scheduled_digest", lambda: counts.__setitem__("digest", counts["digest"] + 1)
    )
    monkeypatch.setattr(
        rec_mod, "sync_due_connectors", lambda: counts.__setitem__("connectors", counts["connectors"] + 1)
    )
    monkeypatch.setattr(
        rec_mod, "check_imapsync_bridge_alerts", lambda: counts.__setitem__("imapsync", counts["imapsync"] + 1)
    )
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: fake_clock["t"])

    stop_event = asyncio.Event()

    async def _fake_wait_for(coro: Any, timeout: float) -> Any:
        coro.close()
        waits.append(timeout)
        if len(waits) > 5:
            stop_event.set()
            return True
        fake_clock["t"] += timeout
        raise asyncio.TimeoutError

    monkeypatch.setattr(rec_mod.asyncio, "wait_for", _fake_wait_for)

    # 1. Default 300s Sieve interval -> five 60s ticks run local checks 5x, Sieve reconcile 1x!
    monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "300")
    asyncio.run(rec_mod.reconciler_loop(stop_event))
    assert waits[:5] == [60.0, 60.0, 60.0, 60.0, 60.0]
    assert counts == {"sieve": 1, "digest": 5, "connectors": 5, "imapsync": 5}

    # 2. Small 5s test override -> every 5s tick runs Sieve reconcile!
    counts = {"sieve": 0, "digest": 0, "connectors": 0, "imapsync": 0}
    waits.clear()
    stop_event2 = asyncio.Event()

    async def _fake_wait_for_fast(coro: Any, timeout: float) -> Any:
        coro.close()
        waits.append(timeout)
        if len(waits) > 2:
            stop_event2.set()
            return True
        fake_clock["t"] += timeout
        raise asyncio.TimeoutError

    monkeypatch.setattr(rec_mod.asyncio, "wait_for", _fake_wait_for_fast)
    monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "5")
    asyncio.run(rec_mod.reconciler_loop(stop_event2))
    assert waits[:2] == [5.0, 5.0]
    assert counts == {"sieve": 2, "digest": 2, "connectors": 2, "imapsync": 2}
