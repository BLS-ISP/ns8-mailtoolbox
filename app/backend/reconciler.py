"""Periodic background reconciler & scheduler for NS8 Mail Toolbox (Phases 1–3).

Handles:
1. Phase 1 ManageSieve reconciliation: nightly 00:00 `sogo-tool update-autoreply` recovery
   and automatic `end_date` expiry restoration (`SETACTIVE` + `DELETESCRIPT`).
2. Phase 3a Allowlist rescue (`Junk` -> `INBOX`) and daily scheduled Spam Quarantine Digest
   at `digest_time` in configured `timezone` (tracked once per calendar day in SQLite).
3. Phase 3c Periodic polling of due external POP3/IMAP connectors (`interval_minutes`).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.backend.config import get_settings
from app.backend.connector_service import check_imapsync_bridge_alerts, sync_due_connectors
from app.backend.db import (
    add_audit_entry,
    get_enabled_vacation_rules,
    get_meta,
    set_meta,
    upsert_vacation_rule,
)
from app.backend.quarantine_service import (
    rescue_allowlisted_junk_messages,
    run_quarantine_digest,
)
from app.backend.service import (
    build_base_mailbox_map,
    get_current_date,
    mutation_lock,
    open_sieve_client,
)
from app.backend.sieve_client import ManageSieveError
from app.backend.sieve_engine import (
    SOGO_SCRIPT_NAME,
    TOOLBOX_SCRIPT_NAME,
    deduplicate_addresses,
    generate_toolbox_sieve,
    parse_sogo_script,
)

logger = logging.getLogger(__name__)


def check_and_run_scheduled_digest(now_utc: datetime | None = None) -> bool:
    """Run daily quarantine digest if `digest_enabled`, past `digest_time`, and not yet run today."""
    settings = get_settings()
    if not settings.runtime.digest_enabled:
        return False

    tz_name = settings.runtime.timezone or "Europe/Berlin"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    ref_utc = now_utc or datetime.now(timezone.utc)
    local_now = ref_utc.astimezone(tz)
    today_key = local_now.strftime("%Y-%m-%d")

    if get_meta("digest_last_scheduled_date", "", settings.sqlite_path) == today_key:
        return False

    raw_time = (settings.runtime.digest_time or "07:30").strip()
    try:
        hh_str, mm_str = raw_time.split(":", 1)
        target_minutes = int(hh_str) * 60 + int(mm_str)
    except ValueError:
        target_minutes = 7 * 60 + 30

    current_minutes = local_now.hour * 60 + local_now.minute
    if current_minutes < target_minutes:
        return False

    set_meta("digest_last_scheduled_date", today_key, settings.sqlite_path)
    run_quarantine_digest()
    return True


def run_reconcile_pass(today_override: date | None = None) -> dict[str, int]:
    """Execute synchronous ManageSieve reconciliation + allowlist rescue pass."""
    reconciled = 0
    expired = 0
    reactivated = 0

    with mutation_lock:
        settings = get_settings()

        # Best-effort allowlist rescue from Junk -> INBOX
        try:
            rescue_allowlisted_junk_messages(settings)
        except Exception as exc:
            logger.debug("Allowlist rescue pass skipped: %s", exc)

        enabled_rules = get_enabled_vacation_rules(settings.sqlite_path)
        if not enabled_rules:
            return {"reconciled": 0, "expired": 0, "reactivated": 0}

        if not settings.mail.host or not settings.mail.master_password:
            logger.warning("Skipping reconcile pass: ManageSieve credentials/host not configured")
            return {"reconciled": 0, "expired": 0, "reactivated": 0}

        today = today_override or get_current_date()
        base_map = build_base_mailbox_map(settings)
        base_by_lower: dict[str, dict[str, Any]] = {
            k.lower(): v for k, v in base_map.items()
        }

        for rule in enabled_rules:
            user = str(rule["user"])
            end_date_str = str(rule.get("end_date") or "").strip()
            is_expired = False
            if end_date_str:
                try:
                    is_expired = date.fromisoformat(end_date_str) < today
                except ValueError:
                    is_expired = False

            try:
                with open_sieve_client(user, settings.mail) as client:
                    scripts, active_script = client.listscripts()
                    prev = str(rule.get("previous_active_script") or "")
                    if prev == TOOLBOX_SCRIPT_NAME:
                        prev = ""

                    if is_expired:
                        restore_target = prev if (prev and prev in scripts) else ""
                        client.setactive(restore_target)
                        if TOOLBOX_SCRIPT_NAME in scripts:
                            client.deletescript(TOOLBOX_SCRIPT_NAME)

                        upsert_vacation_rule(
                            user=user,
                            enabled=False,
                            start_date=str(rule.get("start_date") or ""),
                            end_date=end_date_str,
                            subject=str(rule.get("subject") or ""),
                            body=str(rule.get("body") or ""),
                            forward_enabled=bool(rule.get("forward_enabled", False)),
                            forward_address=str(rule.get("forward_address") or ""),
                            keep_copy=bool(rule.get("keep_copy", True)),
                            previous_active_script=restore_target,
                            sieve_installed=False,
                            updated_by="reconciler",
                            db_path=settings.sqlite_path,
                        )
                        add_audit_entry(
                            actor="system",
                            source="reconciler",
                            target_user=user,
                            action="vacation_expired",
                            details=(
                                f"Absence ended on {end_date_str}; restored active script "
                                f"{restore_target!r}"
                            ),
                            db_path=settings.sqlite_path,
                        )
                        expired += 1
                        reconciled += 1
                        continue

                    if active_script and active_script != TOOLBOX_SCRIPT_NAME and not prev:
                        prev = active_script

                    has_sogo = SOGO_SCRIPT_NAME in scripts
                    sogo_raw = client.getscript(SOGO_SCRIPT_NAME) if has_sogo else None
                    sogo_status = parse_sogo_script(
                        sogo_raw,
                        has_sogo_script=has_sogo,
                        is_active=(active_script == SOGO_SCRIPT_NAME),
                    )

                    mb_meta = base_by_lower.get(user.lower())
                    if mb_meta:
                        addresses = deduplicate_addresses(
                            ([mb_meta["mail"]] if mb_meta.get("mail") else [])
                            + list(mb_meta.get("aliases") or [])
                        )
                    elif settings.ldap.domain:
                        addresses = [f"{user}@{settings.ldap.domain}"]
                    else:
                        addresses = []

                    expected_script = generate_toolbox_sieve(
                        addresses=addresses,
                        subject=str(rule.get("subject") or ""),
                        body=str(rule.get("body") or ""),
                        start_date=str(rule.get("start_date") or ""),
                        end_date=end_date_str,
                        forward_enabled=bool(rule.get("forward_enabled", False)),
                        forward_address=str(rule.get("forward_address") or ""),
                        keep_copy=bool(rule.get("keep_copy", True)),
                        has_sogo_script=sogo_status["has_sogo_script"],
                        sogo_vacation_enabled=sogo_status["vacation_enabled"],
                    )

                    was_overridden = (
                        active_script != TOOLBOX_SCRIPT_NAME
                        or TOOLBOX_SCRIPT_NAME not in scripts
                    )
                    script_drifted = False
                    if not was_overridden:
                        current_script = client.getscript(TOOLBOX_SCRIPT_NAME)
                        script_drifted = current_script.strip() != expected_script.strip()

                    if was_overridden or script_drifted:
                        client.checkscript(expected_script)
                        client.putscript(TOOLBOX_SCRIPT_NAME, expected_script)
                        client.setactive(TOOLBOX_SCRIPT_NAME)

                        upsert_vacation_rule(
                            user=user,
                            enabled=True,
                            start_date=str(rule.get("start_date") or ""),
                            end_date=end_date_str,
                            subject=str(rule.get("subject") or ""),
                            body=str(rule.get("body") or ""),
                            forward_enabled=bool(rule.get("forward_enabled", False)),
                            forward_address=str(rule.get("forward_address") or ""),
                            keep_copy=bool(rule.get("keep_copy", True)),
                            previous_active_script=prev,
                            sieve_installed=True,
                            updated_by=str(rule.get("updated_by") or "reconciler"),
                            updated_at=str(rule.get("updated_at") or "") or None,
                            db_path=settings.sqlite_path,
                        )

                        if was_overridden:
                            reactivated += 1
                            add_audit_entry(
                                actor="system",
                                source="reconciler",
                                target_user=user,
                                action="vacation_reactivated",
                                details=(
                                    f"Re-activated {TOOLBOX_SCRIPT_NAME} "
                                    f"(previous active was {active_script or 'none'!r})"
                                ),
                                db_path=settings.sqlite_path,
                            )
                        else:
                            add_audit_entry(
                                actor="system",
                                source="reconciler",
                                target_user=user,
                                action="vacation_reconciled",
                                details="Synchronized toolbox-vacation script with current aliases/SOGo state",
                                db_path=settings.sqlite_path,
                            )

                    reconciled += 1
            except ManageSieveError as exc:
                logger.warning("Reconcile skipped mailbox %s due to ManageSieve error: %s", user, exc)
            except Exception as exc:
                logger.exception("Unexpected error reconciling mailbox %s: %s", user, exc)

    return {
        "reconciled": reconciled,
        "expired": expired,
        "reactivated": reactivated,
    }


def _run_background_tick(*, run_sieve: bool = True) -> None:
    if run_sieve:
        try:
            run_reconcile_pass()
        except Exception as exc:
            logger.exception("Sieve reconcile pass failed: %s", exc)
    try:
        check_and_run_scheduled_digest()
    except Exception as exc:
        logger.exception("Scheduled quarantine digest check failed: %s", exc)
    try:
        sync_due_connectors()
    except Exception as exc:
        logger.exception("Scheduled connector sync failed: %s", exc)
    try:
        check_imapsync_bridge_alerts()
    except Exception as exc:
        logger.exception("Scheduled imapsync bridge alert check failed: %s", exc)


async def reconciler_loop(stop_event: asyncio.Event) -> None:
    """Background asyncio loop throttling network Sieve/IMAP reconcile vs 60s local SQLite ticks."""
    last_sieve_mono = time.monotonic()
    while not stop_event.is_set():
        reconcile_interval = max(5, get_settings().reconcile_interval_seconds)
        tick_interval = max(5, min(60, reconcile_interval))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(tick_interval))
            break
        except asyncio.TimeoutError:
            pass

        now_mono = time.monotonic()
        due_for_sieve = (now_mono - last_sieve_mono) >= (float(reconcile_interval) - 0.05)
        if due_for_sieve:
            last_sieve_mono = now_mono

        await asyncio.to_thread(_run_background_tick, run_sieve=due_for_sieve)
