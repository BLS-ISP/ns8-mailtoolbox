"""Unit tests for Sieve script generation, injection sanitization, and SOGo script parsing."""

from __future__ import annotations

from datetime import date
import pytest

from app.backend.sieve_engine import (
    compute_active_now,
    generate_toolbox_sieve,
    parse_sogo_script,
    tokenize_sieve,
)
from app.backend.tests.mock_managesieve import validate_dovecot_sieve_syntax


def test_generate_minimal_open_ended_vacation() -> None:
    script = generate_toolbox_sieve(
        addresses=["max.muster@firma.de", "m.muster@firma.de", "M.Muster@firma.de"],
        subject="Abwesenheitsnotiz: Max Mustermann",
        body="Vielen Dank für Ihre Nachricht.\nIch bin krankgeschrieben.",
        start_date="",
        end_date="",
        forward_enabled=False,
        has_sogo_script=False,
    )
    assert 'require ["vacation"];' in script
    assert '"date"' not in script
    assert '"relational"' not in script
    assert '"copy"' not in script
    assert '"include"' not in script
    assert 'if ' not in script
    assert ':addresses ["max.muster@firma.de", "m.muster@firma.de"]' in script
    assert validate_dovecot_sieve_syntax(script) is None


def test_generate_date_bounded_with_copy_forward_and_sogo_include() -> None:
    script = generate_toolbox_sieve(
        addresses=["max.muster@firma.de"],
        subject="Out of Office",
        body="Back next week.",
        start_date="2026-11-20",
        end_date="2026-11-27",
        forward_enabled=True,
        forward_address="vertretung@firma.de",
        keep_copy=True,
        has_sogo_script=True,
        sogo_vacation_enabled=False,
    )
    assert 'require ["vacation", "date", "relational", "copy", "include"];' in script
    assert (
        'if allof (currentdate :value "ge" "date" "2026-11-20", '
        'currentdate :value "le" "date" "2026-11-27") {'
    ) in script
    assert 'redirect :copy "vertretung@firma.de";' in script
    assert 'include :personal "sogo";' in script
    assert "stop;" not in script
    assert validate_dovecot_sieve_syntax(script) is None


def test_generate_direct_forward_without_copy_emits_stop_and_omits_copy_extension() -> None:
    script = generate_toolbox_sieve(
        addresses=["max@firma.de"],
        subject="Abwesend",
        body="Bitte an Vertretung wenden.",
        start_date="2026-11-20",
        end_date="",
        forward_enabled=True,
        forward_address="deputy@firma.de",
        keep_copy=False,
        has_sogo_script=True,
        sogo_vacation_enabled=False,
    )
    assert 'require ["vacation", "date", "relational", "include"];' in script
    assert '"copy"' not in script
    assert 'if currentdate :value "ge" "date" "2026-11-20" {' in script
    assert 'redirect "deputy@firma.de";' in script
    assert "stop;" in script
    assert 'include :personal "sogo";' in script
    assert validate_dovecot_sieve_syntax(script) is None


def test_sogo_with_vacation_omits_include_to_prevent_double_autoreply() -> None:
    script = generate_toolbox_sieve(
        addresses=["max@firma.de"],
        subject="Zentrale Krankmeldung",
        body="Derzeit nicht erreichbar.",
        start_date="",
        end_date="2026-11-30",
        has_sogo_script=True,
        sogo_vacation_enabled=True,
    )
    assert 'require ["vacation", "date", "relational"];' in script
    assert '"include"' not in script
    assert 'include :personal "sogo";' not in script
    assert 'if currentdate :value "le" "date" "2026-11-30" {' in script
    assert validate_dovecot_sieve_syntax(script) is None


def test_sieve_injection_payload_is_neutralized() -> None:
    malicious_subject = 'Urgent"; redirect "hacker@evil.example"; #\r\nInjected: yes'
    malicious_body = 'Line 1\\path\n"; redirect "leak@evil.example"; /*'
    script = generate_toolbox_sieve(
        addresses=["victim@firma.de"],
        subject=malicious_subject,
        body=malicious_body,
    )
    assert validate_dovecot_sieve_syntax(script) is None
    tokens = tokenize_sieve(script)
    ident_commands = [val for typ, val in tokens if typ == "IDENT"]
    assert ident_commands == ["require", "vacation"]


def test_parse_sogo_script_variants() -> None:
    # 1. Folder filter mentioning "vacation" inside string and comment -> NOT a vacation action
    folder_only = """
    require ["fileinto"];
    # vacation :days 7 "old commented rule";
    if header :contains "subject" "vacation schedule" {
        fileinto "HR";
    }
    """
    st1 = parse_sogo_script(folder_only, has_sogo_script=True, is_active=True)
    assert st1 == {
        "has_sogo_script": True,
        "is_active": True,
        "vacation_enabled": False,
        "forward_enabled": False,
        "forward_address": "",
    }

    # 2. SOGo multi-line `text:` vacation + `:copy` redirect
    sogo_vac_and_fwd = """
    require ["vacation", "copy", "fileinto"];
    redirect :copy "kollege@firma.de";
    vacation :days 3 :addresses ["max@firma.de"] text:
    Ich bin im Sommerurlaub!
    ..Punktzeile
    .
    ;
    """
    st2 = parse_sogo_script(sogo_vac_and_fwd, has_sogo_script=True, is_active=True)
    assert st2 == {
        "has_sogo_script": True,
        "is_active": True,
        "vacation_enabled": True,
        "forward_enabled": True,
        "forward_address": "kollege@firma.de",
    }


def test_compute_active_now_and_invalid_dates() -> None:
    ref = date(2026, 11, 20)
    assert compute_active_now(enabled=False, start_date="", end_date="", today=ref) is False
    assert compute_active_now(enabled=True, start_date="", end_date="", today=ref) is True
    assert compute_active_now(enabled=True, start_date="2026-11-21", end_date="2026-11-25", today=ref) is False
    assert compute_active_now(enabled=True, start_date="2026-11-20", end_date="2026-11-20", today=ref) is True
    assert compute_active_now(enabled=True, start_date="2026-11-10", end_date="2026-11-19", today=ref) is False

    with pytest.raises(ValueError):
        generate_toolbox_sieve(
            addresses=["a@b.de"],
            subject="x",
            body="y",
            start_date="2026-11-25",
            end_date="2026-11-20",
        )
