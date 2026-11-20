"""FastAPI application entrypoint for NS8 Mail Toolbox (Port 8080 + Milter Port 8090)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse

from app.backend.auth import (
    AuthenticatedUser as SessionUser,
    SESSION_COOKIE_NAME,
    check_login_rate_limit,
    clear_failed_logins,
    clear_session_cookie,
    create_session_token,
    get_client_ip,
    record_failed_login,
    require_internal_token,
    require_portal_session,
    revoke_session_token,
    set_session_cookie,
)
from app.backend.config import (
    format_public_runtime_settings,
    get_settings,
    update_runtime_settings,
)
from app.backend.connector_service import (
    delete_connector,
    list_connectors,
    save_connector,
    sync_connector_by_id,
    test_connector_connection,
)
from app.backend.db import (
    add_audit_entry,
    delete_signature_rule,
    init_db,
    list_audit_entries,
    list_signature_rules,
    list_templates,
    save_signature_rule,
    save_templates,
)
from app.backend.ldap_client import (
    AuthBackendUnavailableError,
    AuthForbiddenGroupError,
    AuthInvalidCredentialsError,
    authenticate_portal_user,
    list_directory_groups,
)
from app.backend.milter_server import (
    MilterServer,
    get_global_milter_status,
    set_active_milter_server,
)
from app.backend.models import (
    AuditListResponse,
    AuthUserResponse,
    ConnectorSaveRequest,
    ConnectorSyncRequest,
    ConnectorTestRequest,
    IdDeleteRequest,
    LoginRequest,
    MailTrackingSearchRequest,
    MailboxModel,
    MailboxesListResponse,
    QuarantineAdminReleaseRequest,
    QuarantinePublicActionRequest,
    QuarantineRunDigestRequest,
    ReconcileResponse,
    RspamdTestRequest,
    RuntimeSettingsUpdateRequest,
    SignaturePreviewRequest,
    SignatureRuleSaveRequest,
    TemplatesPayload,
    VacationUpdateRequest,
)
from app.backend.quarantine_service import (
    admin_release_quarantine_item,
    execute_quarantine_token_action,
    inspect_quarantine_token,
    list_admin_quarantine,
    remove_allowlist_entry,
    run_quarantine_digest,
)
from app.backend.reconciler import reconciler_loop, run_reconcile_pass
from app.backend.service import (
    deactivate_all_vacations,
    list_mailboxes_with_status,
    update_mailbox_vacation,
)
from app.backend.signature_engine import preview_signature_for_user
from app.backend.tracking_service import sync_and_search_mail_history, test_rspamd_connection

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize SQLite WAL schema, Before-DKIM Milter server, and background reconciler."""
    try:
        init_db()
    except Exception as exc:
        logger.warning("Database initialization warning during startup: %s", exc)

    milter: MilterServer | None = None
    env_milter_raw = os.environ.get("MILTER_ENABLED", "1").strip().lower()
    if env_milter_raw not in ("0", "false", "no", "off"):
        settings = get_settings()
        milter = MilterServer(host="0.0.0.0", port=settings.runtime.milter_listen_port)
        try:
            await milter.start()
            set_active_milter_server(milter)
        except OSError as exc:
            logger.warning("Could not bind Milter TCP port %s: %s", milter.port, exc)
            milter = None

    stop_event = asyncio.Event()
    task = asyncio.create_task(reconciler_loop(stop_event), name="toolbox-reconciler")
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if milter is not None:
            await milter.stop()
            set_active_milter_server(None)


app = FastAPI(
    title="NS8 Mail Toolbox API",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/api/health")
async def get_health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Secretariat Web Portal Authentication
# ---------------------------------------------------------------------------


@app.post("/api/auth/login", response_model=AuthUserResponse)
async def portal_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    client_ip = get_client_ip(request)
    check_login_rate_limit(client_ip)
    settings = get_settings()

    try:
        user = await asyncio.to_thread(
            authenticate_portal_user,
            payload.username,
            payload.password,
        )
    except AuthInvalidCredentialsError as exc:
        record_failed_login(client_ip)
        await asyncio.to_thread(
            add_audit_entry,
            actor=(payload.username or "anonymous").strip() or "anonymous",
            source="portal",
            target_user="",
            action="login_failed",
            details=f"Invalid credentials from {client_ip}",
            db_path=settings.sqlite_path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        ) from exc
    except AuthForbiddenGroupError as exc:
        await asyncio.to_thread(
            add_audit_entry,
            actor=(payload.username or "").strip(),
            source="portal",
            target_user="",
            action="login_forbidden",
            details=f"Unauthorized group membership from {client_ip}",
            db_path=settings.sqlite_path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of an authorized secretariat group",
        ) from exc
    except AuthBackendUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Authentication directory unavailable: {exc}",
        ) from exc

    clear_failed_logins(client_ip)
    token = create_session_token(user)
    set_session_cookie(response, request, token)
    await asyncio.to_thread(
        add_audit_entry,
        actor=user.username,
        source="portal",
        target_user="",
        action="login_success",
        details=f"Portal login from {client_ip}",
        db_path=settings.sqlite_path,
    )
    return {
        "authenticated": True,
        "username": user.username,
        "display_name": user.display_name,
        "groups": user.groups,
        "portal_tracking_enabled": bool(settings.runtime.portal_tracking_enabled),
    }


@app.get("/api/auth/me", response_model=AuthUserResponse)
async def portal_me(session: SessionUser = Depends(require_portal_session)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "authenticated": True,
        "username": session.username,
        "display_name": session.display_name,
        "groups": session.groups,
        "portal_tracking_enabled": bool(settings.runtime.portal_tracking_enabled),
    }


@app.post("/api/auth/logout")
async def portal_logout(request: Request, response: Response) -> dict[str, bool]:
    revoke_session_token(request.cookies.get(SESSION_COOKIE_NAME))
    clear_session_cookie(response)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Public Self-Service Quarantine Token Endpoints (Scanner-Safe Two-Step!)
# ---------------------------------------------------------------------------


@app.get("/api/quarantine/token/{token}")
async def public_inspect_quarantine_token(token: str) -> dict[str, Any]:
    return await asyncio.to_thread(inspect_quarantine_token, token)


@app.post("/api/quarantine/action", response_model=None)
async def public_execute_quarantine_action(
    payload: QuarantinePublicActionRequest,
) -> JSONResponse:
    http_code, body = await asyncio.to_thread(
        execute_quarantine_token_action,
        payload.token,
        payload.op,
    )
    return JSONResponse(status_code=http_code, content=body)


# ---------------------------------------------------------------------------
# Secretariat Web Portal Endpoints (Cookie Session Auth)
# ---------------------------------------------------------------------------


@app.get("/api/mailboxes", response_model=MailboxesListResponse)
async def portal_list_mailboxes(
    _session: SessionUser = Depends(require_portal_session),
) -> dict[str, Any]:
    return await asyncio.to_thread(list_mailboxes_with_status)


@app.put("/api/mailboxes/{user}/vacation", response_model=MailboxModel)
async def portal_update_vacation(
    user: str,
    payload: VacationUpdateRequest,
    session: SessionUser = Depends(require_portal_session),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        update_mailbox_vacation,
        user=user,
        payload=payload,
        actor=session.username,
        source="portal",
    )


@app.get("/api/templates")
async def portal_get_templates(
    _session: SessionUser = Depends(require_portal_session),
) -> dict[str, Any]:
    templates = await asyncio.to_thread(list_templates)
    return {"templates": templates}


@app.put("/api/templates")
async def portal_put_templates(
    payload: TemplatesPayload,
    session: SessionUser = Depends(require_portal_session),
) -> dict[str, Any]:
    settings = get_settings()
    raw_items = [item.model_dump() for item in payload.templates]
    saved = await asyncio.to_thread(save_templates, raw_items, settings.sqlite_path)
    await asyncio.to_thread(
        add_audit_entry,
        actor=session.username,
        source="portal",
        target_user="",
        action="templates_updated",
        details=f"Updated {len(saved)} vacation template(s)",
        db_path=settings.sqlite_path,
    )
    return {"templates": saved}


@app.get("/api/audit", response_model=AuditListResponse)
async def portal_get_audit(
    limit: int = Query(default=200, ge=1, le=1000),
    target_user: str | None = Query(default=None),
    _session: SessionUser = Depends(require_portal_session),
) -> dict[str, Any]:
    entries = await asyncio.to_thread(list_audit_entries, limit=limit, target_user=target_user)
    return {"entries": entries}


@app.post("/api/mail-tracking/search")
async def portal_search_mail_tracking(
    payload: MailTrackingSearchRequest,
    _session: SessionUser = Depends(require_portal_session),
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.runtime.portal_tracking_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mail tracking is not enabled for the secretariat portal",
        )
    return await asyncio.to_thread(
        sync_and_search_mail_history,
        sender=payload.sender,
        recipient=payload.recipient,
        subject=payload.subject,
        action_filter=payload.action_filter,
        date_from=payload.date_from,
        date_to=payload.date_to,
        limit=payload.limit,
    )


# ---------------------------------------------------------------------------
# Internal NS8 Cockpit Action Endpoints (X-Internal-Token Auth)
# ---------------------------------------------------------------------------


@app.get("/api/internal/settings")
async def internal_get_settings(
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(format_public_runtime_settings)


@app.put("/api/internal/settings")
async def internal_put_settings(
    payload: RuntimeSettingsUpdateRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(update_runtime_settings, payload.model_dump())


@app.get("/api/internal/mailboxes", response_model=MailboxesListResponse)
async def internal_list_mailboxes(
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(list_mailboxes_with_status)


@app.get("/api/internal/groups")
async def internal_list_groups(
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    groups = await asyncio.to_thread(list_directory_groups)
    return {"groups": groups}


@app.put("/api/internal/mailboxes/{user}/vacation", response_model=MailboxModel)
async def internal_update_vacation(
    user: str,
    payload: VacationUpdateRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    actor = (payload.updated_by or "").strip() or "admin"
    return await asyncio.to_thread(
        update_mailbox_vacation,
        user=user,
        payload=payload,
        actor=actor,
        source="cockpit",
    )


@app.get("/api/internal/templates")
async def internal_get_templates(
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    templates = await asyncio.to_thread(list_templates)
    return {"templates": templates}


@app.put("/api/internal/templates")
async def internal_put_templates(
    payload: TemplatesPayload,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    settings = get_settings()
    raw_items = [item.model_dump() for item in payload.templates]
    saved = await asyncio.to_thread(save_templates, raw_items, settings.sqlite_path)
    await asyncio.to_thread(
        add_audit_entry,
        actor="cockpit-admin",
        source="cockpit",
        target_user="",
        action="templates_updated",
        details=f"Updated {len(saved)} vacation template(s)",
        db_path=settings.sqlite_path,
    )
    return {"templates": saved}


@app.get("/api/internal/audit", response_model=AuditListResponse)
async def internal_get_audit(
    limit: int = Query(default=200, ge=1, le=1000),
    target_user: str | None = Query(default=None),
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    entries = await asyncio.to_thread(list_audit_entries, limit=limit, target_user=target_user)
    return {"entries": entries}


@app.post("/api/internal/reconcile", response_model=ReconcileResponse)
async def internal_reconcile(
    _token: str = Depends(require_internal_token),
) -> dict[str, int]:
    return await asyncio.to_thread(run_reconcile_pass)


@app.post("/api/internal/deactivate-all-vacations")
async def internal_deactivate_all_vacations(
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(deactivate_all_vacations)


# ---------------------------------------------------------------------------
# Phase 2 Internal Endpoints: Corporate Signatures & Before-DKIM Milter
# ---------------------------------------------------------------------------


@app.get("/api/internal/signatures")
async def internal_list_signatures(
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    sigs = await asyncio.to_thread(list_signature_rules)
    return {
        "signatures": sigs,
        "milter_status": get_global_milter_status(),
    }


@app.post("/api/internal/signatures")
async def internal_save_signature(
    payload: SignatureRuleSaveRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    settings = get_settings()
    saved = await asyncio.to_thread(
        save_signature_rule,
        rule_id=payload.id,
        name=payload.name,
        scope_type=payload.scope_type,
        scope_value=payload.scope_value,
        priority=payload.priority,
        enabled=payload.enabled,
        html_template=payload.html_template,
        text_template=payload.text_template,
        auto_plain_from_html=payload.auto_plain_from_html,
        reply_mode=payload.reply_mode,
        placement=payload.placement,
        db_path=settings.sqlite_path,
    )
    await asyncio.to_thread(
        add_audit_entry,
        actor="cockpit-admin",
        source="cockpit",
        target_user="",
        action="signature_saved",
        details=f"Saved signature rule {saved['name']!r} ({saved['scope_type']}:{saved['scope_value']})",
        db_path=settings.sqlite_path,
    )
    return {"signature": saved}


@app.post("/api/internal/signatures/delete")
async def internal_delete_signature(
    payload: IdDeleteRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    settings = get_settings()
    deleted = await asyncio.to_thread(delete_signature_rule, payload.id, settings.sqlite_path)
    if deleted:
        await asyncio.to_thread(
            add_audit_entry,
            actor="cockpit-admin",
            source="cockpit",
            target_user="",
            action="signature_deleted",
            details=f"Deleted signature rule #{payload.id}",
            db_path=settings.sqlite_path,
        )
    return {"deleted": bool(deleted), "id": int(payload.id)}


@app.post("/api/internal/signatures/preview")
async def internal_preview_signature(
    payload: SignaturePreviewRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        preview_signature_for_user,
        user=payload.user,
        sender_email=payload.sender_email,
        html_template=payload.html_template,
        text_template=payload.text_template,
        auto_plain_from_html=payload.auto_plain_from_html,
    )


# ---------------------------------------------------------------------------
# Phase 3a Internal Endpoints: Quarantine Admin & Digest
# ---------------------------------------------------------------------------


@app.get("/api/internal/quarantine")
async def internal_get_quarantine(
    user: str = Query(default=""),
    search: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        list_admin_quarantine,
        user_filter=user,
        search=search,
        limit=limit,
    )


@app.post("/api/internal/quarantine/release")
async def internal_release_quarantine(
    payload: QuarantineAdminReleaseRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        admin_release_quarantine_item,
        user=payload.user,
        uid=payload.uid,
        message_id=payload.message_id,
        allow_sender=payload.allow_sender,
    )


@app.post("/api/internal/quarantine/run-digest")
async def internal_run_quarantine_digest(
    payload: QuarantineRunDigestRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, int]:
    return await asyncio.to_thread(run_quarantine_digest, user_filter=payload.user)


@app.post("/api/internal/allowlist/delete")
async def internal_delete_allowlist(
    payload: IdDeleteRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, bool]:
    deleted = await asyncio.to_thread(remove_allowlist_entry, payload.id)
    return {"deleted": bool(deleted)}


# ---------------------------------------------------------------------------
# Phase 3b Internal Endpoints: Rspamd Mail-Tracking Search
# ---------------------------------------------------------------------------


@app.post("/api/internal/mail-tracking/search")
async def internal_search_mail_tracking(
    payload: MailTrackingSearchRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        sync_and_search_mail_history,
        sender=payload.sender,
        recipient=payload.recipient,
        subject=payload.subject,
        action_filter=payload.action_filter,
        date_from=payload.date_from,
        date_to=payload.date_to,
        limit=payload.limit,
    )


@app.post("/api/internal/mail-tracking/test-rspamd")
async def internal_test_rspamd(
    payload: RspamdTestRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        test_rspamd_connection,
        rspamd_url=payload.rspamd_url,
        rspamd_user=payload.rspamd_user,
        rspamd_password=payload.rspamd_password,
        rspamd_tls_verify=payload.rspamd_tls_verify,
    )


# ---------------------------------------------------------------------------
# Phase 3c Internal Endpoints: External POP3/IMAP Connectors
# ---------------------------------------------------------------------------


@app.get("/api/internal/connectors")
async def internal_get_connectors(
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    items = await asyncio.to_thread(list_connectors)
    return {"connectors": items}


@app.post("/api/internal/connectors")
async def internal_save_connector(
    payload: ConnectorSaveRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    saved = await asyncio.to_thread(save_connector, payload.model_dump())
    return {"connector": saved}


@app.post("/api/internal/connectors/delete")
async def internal_delete_connector(
    payload: IdDeleteRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    deleted = await asyncio.to_thread(delete_connector, payload.id)
    return {"deleted": bool(deleted), "id": int(payload.id)}


@app.post("/api/internal/connectors/test")
async def internal_test_connector(
    payload: ConnectorTestRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(test_connector_connection, payload.model_dump())


@app.post("/api/internal/connectors/sync")
async def internal_sync_connector(
    payload: ConnectorSyncRequest,
    _token: str = Depends(require_internal_token),
) -> dict[str, Any]:
    return await asyncio.to_thread(sync_connector_by_id, payload.id)


# ---------------------------------------------------------------------------
# Static Secretariat Portal Serving & SPA Fallback
# ---------------------------------------------------------------------------


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa_frontend(full_path: str) -> FileResponse:
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    static_dir = get_settings().static_dir
    if static_dir is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Static frontend bundle is not available",
        )

    static_root = static_dir.resolve()
    if full_path:
        candidate = (static_root / full_path).resolve()
        if candidate.is_relative_to(static_root) and candidate.is_file():
            return FileResponse(str(candidate))

    index_file = static_root / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="index.html missing")
    return FileResponse(str(index_file))
