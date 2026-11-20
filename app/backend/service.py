"""Core business service orchestrating LDAP, mailboxes_cache.json, ManageSieve, and SQLite."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import logging
import os
import threading
from typing import Any

from fastapi import HTTPException, status

from app.backend.config import AppSettings, MailSettings, get_settings, load_mailboxes_cache
from app.backend.db import (
    add_audit_entry,
    get_all_vacation_rules,
    get_enabled_vacation_rules,
    get_vacation_rule,
    upsert_vacation_rule,
)
from app.backend.ldap_client import DirectoryUser, list_directory_users
from app.backend.models import VacationUpdateRequest
from app.backend.sieve_client import (
    ManageSieveAuthError,
    ManageSieveClient,
    ManageSieveCommandError,
    ManageSieveConnectionError,
    ManageSieveError,
)
from app.backend.sieve_engine import (
    SOGO_SCRIPT_NAME,
    TOOLBOX_SCRIPT_NAME,
    compute_active_now,
    deduplicate_addresses,
    default_sogo_status,
    generate_toolbox_sieve,
    parse_sogo_script,
    validate_email_address,
    validate_iso_date,
)

logger = logging.getLogger(__name__)

# Shared lock protecting ManageSieve + SQLite state transitions across API calls & reconciler
mutation_lock = threading.RLock()


def get_current_date() -> date:
    """Return current calendar date (supports TOOLBOX_TODAY_OVERRIDE=YYYY-MM-DD in tests)."""
    override = os.environ.get("TOOLBOX_TODAY_OVERRIDE", "").strip()
    if override:
        return date.fromisoformat(override)
    return date.today()


def validate_mailbox_username(user: str) -> str:
    cleaned = (user or "").strip()
    if not cleaned or any(ch in cleaned for ch in ("*", "\x00", "\r", "\n", "/", "\\")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid mailbox username: {user!r}",
        )
    return cleaned


def _normalize_alias_list(
    raw_aliases: list[Any],
    primary_mail: str,
    fallback_domain: str,
) -> list[str]:
    domain = fallback_domain
    if not domain and "@" in primary_mail:
        domain = primary_mail.split("@", 1)[1].strip()

    candidates: list[str] = []
    for item in raw_aliases:
        s = str(item or "").strip()
        if not s:
            continue
        if "@" not in s and domain:
            s = f"{s}@{domain}"
        if "@" in s and s.lower() != (primary_mail or "").lower():
            candidates.append(s)
    return deduplicate_addresses(candidates)


def build_base_mailbox_map(settings: AppSettings | None = None) -> dict[str, dict[str, Any]]:
    """Merge LDAP user directory entries with mailboxes_cache.json metadata."""
    cfg = settings or get_settings()
    cache_items = load_mailboxes_cache(cfg.config_dir)
    ldap_users = list_directory_users(cfg.ldap)

    ldap_by_lower: dict[str, DirectoryUser] = {u.user.lower(): u for u in ldap_users}
    merged: dict[str, dict[str, Any]] = {}
    seen_lower: set[str] = set()

    for item in cache_items:
        raw_user = str(item.get("user") or "").strip()
        if not raw_user:
            continue
        key_low = raw_user.lower()
        seen_lower.add(key_low)
        lu = ldap_by_lower.get(key_low)

        user_id = lu.user if lu else raw_user
        primary_mail = (lu.mail if lu and lu.mail else "") or (
            f"{user_id}@{cfg.ldap.domain}" if cfg.ldap.domain else ""
        )
        aliases = _normalize_alias_list(
            item.get("aliases") or [],
            primary_mail,
            cfg.ldap.domain,
        )
        postfix_forwards = [
            str(f).strip() for f in (item.get("postfix_forwards") or []) if str(f).strip()
        ]

        merged[user_id] = {
            "user": user_id,
            "display_name": lu.display_name if lu else user_id,
            "given_name": lu.given_name if lu else "",
            "sn": lu.sn if lu else "",
            "mail": primary_mail,
            "aliases": aliases,
            "department": lu.department if lu else "",
            "title": lu.title if lu else "",
            "groups": list(lu.groups) if lu else [],
            "enabled": bool(item.get("enabled", True)),
            "postfix_forwards": postfix_forwards,
        }

    for lu in ldap_users:
        if lu.user.lower() in seen_lower:
            continue
        # Exclude default AD Administrator account unless explicitly listed in mailboxes_cache.json
        if lu.user.lower() == "administrator" and cache_items:
            continue
        primary_mail = lu.mail or (f"{lu.user}@{cfg.ldap.domain}" if cfg.ldap.domain else "")
        merged[lu.user] = {
            "user": lu.user,
            "display_name": lu.display_name or lu.user,
            "given_name": lu.given_name,
            "sn": lu.sn,
            "mail": primary_mail,
            "aliases": [],
            "department": lu.department,
            "title": lu.title,
            "groups": list(lu.groups),
            "enabled": True,
            "postfix_forwards": [],
        }

    return merged


def open_sieve_client(target_user: str, mail_cfg: MailSettings) -> ManageSieveClient:
    return ManageSieveClient(
        host=mail_cfg.host,
        port=mail_cfg.sieve_port,
        master_user=mail_cfg.master_user,
        master_password=mail_cfg.master_password,
        target_user=target_user,
        starttls=mail_cfg.sieve_starttls,
        tls_verify=mail_cfg.sieve_tls_verify,
        timeout=mail_cfg.sieve_timeout,
    )


def fetch_sogo_status_safe(target_user: str, mail_cfg: MailSettings) -> dict[str, Any]:
    """Query ManageSieve read-only for a mailbox's SOGo script state; never raises."""
    if not mail_cfg.host or not mail_cfg.master_password:
        return default_sogo_status()
    try:
        with open_sieve_client(target_user, mail_cfg) as client:
            scripts, active = client.listscripts()
            has_sogo = SOGO_SCRIPT_NAME in scripts
            sogo_content = client.getscript(SOGO_SCRIPT_NAME) if has_sogo else None
            return parse_sogo_script(
                sogo_content,
                has_sogo_script=has_sogo,
                is_active=(active == SOGO_SCRIPT_NAME),
            )
    except ManageSieveError as exc:
        logger.debug("ManageSieve status unavailable for %s: %s", target_user, exc)
        return default_sogo_status()
    except Exception as exc:
        logger.warning("Unexpected error inspecting ManageSieve for %s: %s", target_user, exc)
        return default_sogo_status()


def format_vacation_state(rule: dict[str, Any] | None, today: date | None = None) -> dict[str, Any]:
    ref_day = today or get_current_date()
    if not rule:
        return {
            "enabled": False,
            "active_now": False,
            "start_date": "",
            "end_date": "",
            "subject": "",
            "body": "",
            "forward_enabled": False,
            "forward_address": "",
            "keep_copy": True,
            "updated_by": "",
            "updated_at": "",
        }
    enabled = bool(rule.get("enabled", False))
    start_date = str(rule.get("start_date") or "")
    end_date = str(rule.get("end_date") or "")
    active_now = compute_active_now(
        enabled=enabled,
        start_date=start_date,
        end_date=end_date,
        today=ref_day,
    )
    return {
        "enabled": enabled,
        "active_now": active_now,
        "start_date": start_date,
        "end_date": end_date,
        "subject": str(rule.get("subject") or ""),
        "body": str(rule.get("body") or ""),
        "forward_enabled": bool(rule.get("forward_enabled", False)),
        "forward_address": str(rule.get("forward_address") or ""),
        "keep_copy": bool(rule.get("keep_copy", True)),
        "updated_by": str(rule.get("updated_by") or ""),
        "updated_at": str(rule.get("updated_at") or ""),
    }


def list_mailboxes_with_status() -> dict[str, Any]:
    """Assemble full mailbox overview + KPI summary from LDAP, cache, ManageSieve, and SQLite."""
    settings = get_settings()
    base_map = build_base_mailbox_map(settings)
    rules_map = get_all_vacation_rules(settings.sqlite_path)
    today = get_current_date()

    # Include any users that have a stored rule in SQLite even if temporarily missing from cache
    for r_user in rules_map:
        if r_user not in base_map and not any(k.lower() == r_user.lower() for k in base_map):
            primary_mail = f"{r_user}@{settings.ldap.domain}" if settings.ldap.domain else ""
            base_map[r_user] = {
                "user": r_user,
                "display_name": r_user,
                "given_name": "",
                "sn": "",
                "mail": primary_mail,
                "aliases": [],
                "department": "",
                "title": "",
                "groups": [],
                "enabled": True,
                "postfix_forwards": [],
            }

    sorted_users = sorted(
        base_map.values(),
        key=lambda item: (str(item.get("display_name") or "").lower(), str(item["user"]).lower()),
    )

    sogo_by_user: dict[str, dict[str, Any]] = {}
    if sorted_users and settings.mail.host and settings.mail.master_password:
        max_workers = min(8, len(sorted_users))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                u["user"]: pool.submit(fetch_sogo_status_safe, u["user"], settings.mail)
                for u in sorted_users
            }
            for u_name, fut in futures.items():
                sogo_by_user[u_name] = fut.result()
    else:
        for u in sorted_users:
            sogo_by_user[u["user"]] = default_sogo_status()

    rules_by_lower = {k.lower(): v for k, v in rules_map.items()}
    mailboxes_out: list[dict[str, Any]] = []
    central_active_count = 0
    sogo_active_count = 0
    forwards_active_count = 0

    for item in sorted_users:
        u_name = item["user"]
        sogo_st = sogo_by_user.get(u_name) or default_sogo_status()
        vac_st = format_vacation_state(rules_by_lower.get(u_name.lower()), today=today)

        if vac_st["active_now"]:
            central_active_count += 1
        if sogo_st["is_active"] and sogo_st["vacation_enabled"]:
            sogo_active_count += 1

        central_fwd_active = bool(
            vac_st["active_now"] and vac_st["forward_enabled"] and vac_st["forward_address"]
        )
        sogo_fwd_active = bool(sogo_st["is_active"] and sogo_st["forward_enabled"])
        postfix_fwd_active = bool(item.get("postfix_forwards"))
        if central_fwd_active or sogo_fwd_active or postfix_fwd_active:
            forwards_active_count += 1

        mailboxes_out.append(
            {
                **item,
                "sogo_status": sogo_st,
                "vacation": vac_st,
            }
        )

    return {
        "mailboxes": mailboxes_out,
        "summary": {
            "total": len(mailboxes_out),
            "central_vacation_active": central_active_count,
            "sogo_vacation_active": sogo_active_count,
            "forwards_active": forwards_active_count,
        },
    }


def update_mailbox_vacation(
    *,
    user: str,
    payload: VacationUpdateRequest,
    actor: str,
    source: str,
) -> dict[str, Any]:
    """Validate, compile/upload or remove ManageSieve script, persist rule, and log audit."""
    clean_user = validate_mailbox_username(user)

    try:
        start_date = validate_iso_date(payload.start_date)
        end_date = validate_iso_date(payload.end_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if start_date and end_date and end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be greater than or equal to start_date",
        )

    forward_enabled = bool(payload.forward_enabled)
    forward_address = (payload.forward_address or "").strip()
    if payload.enabled and forward_enabled:
        try:
            forward_address = validate_email_address(forward_address)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    elif forward_address and forward_enabled:
        try:
            forward_address = validate_email_address(forward_address)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    if payload.enabled and not (payload.body or "").strip() and not (
        forward_enabled and forward_address
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Vacation message body or forwarding address is required when enabled",
        )

    with mutation_lock:
        settings = get_settings()
        base_map = build_base_mailbox_map(settings)
        mb_meta = None
        for k, v in base_map.items():
            if k.lower() == clean_user.lower():
                mb_meta = v
                clean_user = v["user"]
                break

        if mb_meta is None:
            primary_mail = (
                f"{clean_user}@{settings.ldap.domain}" if settings.ldap.domain else ""
            )
            mb_meta = {
                "user": clean_user,
                "display_name": clean_user,
                "given_name": "",
                "sn": "",
                "mail": primary_mail,
                "aliases": [],
                "department": "",
                "title": "",
                "groups": [],
                "enabled": True,
                "postfix_forwards": [],
            }

        own_addresses = deduplicate_addresses(
            ([mb_meta["mail"]] if mb_meta.get("mail") else []) + list(mb_meta.get("aliases") or [])
        )
        if payload.enabled and forward_enabled and forward_address:
            if forward_address.lower() in {addr.lower() for addr in own_addresses}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Forwarding address cannot be one of the mailbox's own addresses",
                )

        existing_rule = get_vacation_rule(clean_user, settings.sqlite_path)

        try:
            with open_sieve_client(clean_user, settings.mail) as client:
                scripts, active_script = client.listscripts()
                has_sogo = SOGO_SCRIPT_NAME in scripts
                sogo_raw = client.getscript(SOGO_SCRIPT_NAME) if has_sogo else None
                sogo_status = parse_sogo_script(
                    sogo_raw,
                    has_sogo_script=has_sogo,
                    is_active=(active_script == SOGO_SCRIPT_NAME),
                )

                if active_script and active_script != TOOLBOX_SCRIPT_NAME:
                    prev_active = active_script
                elif (
                    existing_rule
                    and existing_rule.get("previous_active_script")
                    and existing_rule["previous_active_script"] != TOOLBOX_SCRIPT_NAME
                ):
                    prev_active = str(existing_rule["previous_active_script"])
                else:
                    prev_active = ""

                if payload.enabled:
                    sieve_script = generate_toolbox_sieve(
                        addresses=own_addresses,
                        subject=payload.subject,
                        body=payload.body,
                        start_date=start_date,
                        end_date=end_date,
                        forward_enabled=forward_enabled,
                        forward_address=forward_address,
                        keep_copy=bool(payload.keep_copy),
                        has_sogo_script=sogo_status["has_sogo_script"],
                        sogo_vacation_enabled=sogo_status["vacation_enabled"],
                    )
                    client.checkscript(sieve_script)
                    client.putscript(TOOLBOX_SCRIPT_NAME, sieve_script)
                    client.setactive(TOOLBOX_SCRIPT_NAME)
                    sogo_status["is_active"] = False
                    sieve_installed = True
                    stored_prev = prev_active
                else:
                    restore_target = prev_active if (prev_active and prev_active in scripts) else ""
                    client.setactive(restore_target)
                    if TOOLBOX_SCRIPT_NAME in scripts:
                        client.deletescript(TOOLBOX_SCRIPT_NAME)
                    sogo_status["is_active"] = restore_target == SOGO_SCRIPT_NAME
                    sieve_installed = False
                    stored_prev = restore_target

        except ManageSieveCommandError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"ManageSieve rejected script: {exc}",
            ) from exc
        except (ManageSieveConnectionError, ManageSieveAuthError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ManageSieve unavailable: {exc}",
            ) from exc

        saved_rule = upsert_vacation_rule(
            user=clean_user,
            enabled=bool(payload.enabled),
            start_date=start_date,
            end_date=end_date,
            subject=payload.subject or "",
            body=payload.body or "",
            forward_enabled=forward_enabled,
            forward_address=forward_address,
            keep_copy=bool(payload.keep_copy),
            previous_active_script=stored_prev,
            sieve_installed=sieve_installed,
            updated_by=actor,
            db_path=settings.sqlite_path,
        )

        if payload.enabled:
            was_enabled = bool(existing_rule and existing_rule.get("enabled"))
            action_name = "vacation_updated" if was_enabled else "vacation_enabled"
            period = (
                f"{start_date or 'immediate'}..{end_date or 'open'}"
                if (start_date or end_date)
                else "open-ended"
            )
            fwd_info = (
                f", forward={'copy' if payload.keep_copy else 'direct'}->{forward_address}"
                if (forward_enabled and forward_address)
                else ""
            )
            details = f"Enabled absence ({period}{fwd_info})"
        else:
            action_name = "vacation_disabled"
            details = (
                f"Disabled absence; restored active script {stored_prev!r}"
                if stored_prev
                else "Disabled absence; cleared active script"
            )

        add_audit_entry(
            actor=actor,
            source=source,
            target_user=clean_user,
            action=action_name,
            details=details,
            db_path=settings.sqlite_path,
        )

        return {
            **mb_meta,
            "sogo_status": sogo_status,
            "vacation": format_vacation_state(saved_rule, today=get_current_date()),
        }


def deactivate_all_vacations() -> dict[str, Any]:
    """Best-effort deactivation of all enabled `toolbox-vacation` rules prior to module uninstall."""
    deactivated = 0
    errors: list[str] = []

    with mutation_lock:
        settings = get_settings()
        enabled_rules = get_enabled_vacation_rules(settings.sqlite_path)
        for rule in enabled_rules:
            user = str(rule["user"])
            prev = str(rule.get("previous_active_script") or "")
            if prev == TOOLBOX_SCRIPT_NAME:
                prev = ""
            try:
                with open_sieve_client(user, settings.mail) as client:
                    scripts, _ = client.listscripts()
                    restore_target = prev if (prev and prev in scripts) else ""
                    client.setactive(restore_target)
                    if TOOLBOX_SCRIPT_NAME in scripts:
                        client.deletescript(TOOLBOX_SCRIPT_NAME)

                upsert_vacation_rule(
                    user=user,
                    enabled=False,
                    start_date=str(rule.get("start_date") or ""),
                    end_date=str(rule.get("end_date") or ""),
                    subject=str(rule.get("subject") or ""),
                    body=str(rule.get("body") or ""),
                    forward_enabled=bool(rule.get("forward_enabled", False)),
                    forward_address=str(rule.get("forward_address") or ""),
                    keep_copy=bool(rule.get("keep_copy", True)),
                    previous_active_script=restore_target,
                    sieve_installed=False,
                    updated_by="system",
                    db_path=settings.sqlite_path,
                )
                add_audit_entry(
                    actor="system",
                    source="system",
                    target_user=user,
                    action="uninstall_cleanup",
                    details=(
                        f"Deactivated {TOOLBOX_SCRIPT_NAME} during module cleanup; "
                        f"restored {restore_target!r}"
                    ),
                    db_path=settings.sqlite_path,
                )
                deactivated += 1
            except Exception as exc:
                logger.warning("Uninstall cleanup failed for mailbox %s: %s", user, exc)
                errors.append(f"{user}: {exc}")

    return {
        "ok": True,
        "deactivated": deactivated,
        "errors": errors,
    }
