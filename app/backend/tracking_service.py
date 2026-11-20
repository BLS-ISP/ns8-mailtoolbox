"""Phase 3b Fast Mail-Tracking Search backed by Rspamd Controller `/history` + SQLite cache.

Supports both direct local Rspamd (`http://127.0.0.1:11334` with `Password` header) and
NS8 Traefik `/rspamd` HTTPS routes (with self-signed TLS `rspamd_tls_verify=False`,
HTTP Basic Auth `rspamd_user:rspamd_password`, URL `/history` normalization, and
Hairpin-NAT / Split-DNS host fallback).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import logging
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.backend.config import get_settings, translate_loopback_url
from app.backend.db import query_mail_history_cache, upsert_mail_history_rows

logger = logging.getLogger(__name__)


def normalize_rspamd_base_url(raw_url: str) -> str:
    """Normalize a user-entered Rspamd URL so `{base}/history` is always valid."""
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return ""
    try:
        parts = urlsplit(cleaned)
        path = (parts.path or "").rstrip("/")
        if path.lower().endswith("/history"):
            path = path[: -len("/history")].rstrip("/")
        return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")
    except Exception:
        return cleaned.split("#", 1)[0].split("?", 1)[0].rstrip("/")


def _is_ip_literal(hostname: str) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(hostname.strip("[]"))
        return True
    except ValueError:
        return False


def _build_hairpin_fallback_url(base_url: str, fallback_host: str) -> tuple[str, str] | None:
    """Build `(fallback_base_url, original_host_header)` when public FQDN fails hairpin NAT."""
    fb = (fallback_host or "").strip()
    if not fb:
        return None
    parts = urlsplit(base_url)
    orig_host = (parts.hostname or "").strip()
    if not orig_host or _is_ip_literal(orig_host) or orig_host.lower() == fb.lower():
        return None

    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password is not None:
            userinfo += f":{parts.password}"
        userinfo += "@"

    port_suffix = f":{parts.port}" if parts.port is not None else ""
    new_netloc = f"{userinfo}{fb}{port_suffix}"
    orig_host_hdr = f"{orig_host}{port_suffix}"
    fb_base = urlunsplit((parts.scheme, new_netloc, parts.path, "", "")).rstrip("/")
    return fb_base, orig_host_hdr


def normalize_rspamd_action(raw_action: str) -> str:
    """Map Rspamd action names onto canonical UI status values."""
    act = (raw_action or "").strip().lower()
    if act in ("no action", "no_action", "accept", ""):
        return "delivered"
    if act in ("add header", "add_header", "rewrite subject", "rewrite_subject", "quarantine"):
        return "spam"
    if act in ("greylist", "soft reject", "soft_reject"):
        return "greylisted"
    if act == "reject":
        return "rejected"
    return "delivered"


def _normalize_history_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    unix_time = 0.0
    ts_iso = ""
    raw_time = raw.get("unix_time", raw.get("time"))
    if isinstance(raw_time, (int, float)):
        unix_time = float(raw_time)
        ts_iso = (
            datetime.fromtimestamp(unix_time, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    elif isinstance(raw_time, str) and raw_time.strip():
        try:
            unix_time = float(raw_time.strip())
            ts_iso = (
                datetime.fromtimestamp(unix_time, tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except ValueError:
            ts_iso = raw_time.strip()

    if not ts_iso:
        raw_ts = str(raw.get("timestamp") or "").strip()
        if raw_ts:
            ts_iso = raw_ts
            try:
                dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                unix_time = dt.timestamp()
            except ValueError:
                unix_time = 0.0
        else:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            unix_time = now.timestamp()
            ts_iso = now.isoformat().replace("+00:00", "Z")

    sender = str(
        raw.get("sender_smtp") or raw.get("sender") or raw.get("from") or ""
    ).strip().strip("<>")

    raw_rcpts = raw.get("rcpt_smtp", raw.get("recipients", raw.get("rcpt", [])))
    if isinstance(raw_rcpts, list):
        recipients = [str(r).strip().strip("<>") for r in raw_rcpts if str(r).strip()]
    elif isinstance(raw_rcpts, str) and raw_rcpts.strip():
        recipients = [
            part.strip().strip("<>") for part in raw_rcpts.split(",") if part.strip()
        ]
    else:
        recipients = []

    subject = str(raw.get("subject") or "").strip()
    rspamd_action = str(raw.get("action") or raw.get("rspamd_action") or "no action").strip()
    status = normalize_rspamd_action(rspamd_action)

    try:
        score = round(float(raw.get("score") or 0.0), 2)
    except (TypeError, ValueError):
        score = 0.0

    try:
        required_score = round(
            float(raw.get("required_score", raw.get("thresholds", 15.0)) or 0.0), 2
        )
    except (TypeError, ValueError):
        required_score = 0.0

    raw_symbols = raw.get("symbols") or {}
    if isinstance(raw_symbols, dict):
        symbols = sorted(str(k) for k in raw_symbols.keys() if str(k).strip())
    elif isinstance(raw_symbols, list):
        symbols = [str(s) for s in raw_symbols if str(s).strip()]
    else:
        symbols = []

    ip = str(raw.get("ip") or "").strip()
    message_id = str(raw.get("message-id") or raw.get("message_id") or "").strip()
    try:
        size_bytes = int(raw.get("size", raw.get("size_bytes", 0)) or 0)
    except (TypeError, ValueError):
        size_bytes = 0

    explicit_id = str(raw.get("id") or "").strip()
    if explicit_id:
        row_id = explicit_id
    else:
        fingerprint = f"{unix_time:.3f}|{message_id}|{sender}|{','.join(recipients)}|{rspamd_action}|{subject}"
        row_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]

    return {
        "id": row_id,
        "timestamp": ts_iso,
        "unix_time": unix_time,
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "status": status,
        "rspamd_action": rspamd_action,
        "score": score,
        "required_score": required_score,
        "symbols": symbols,
        "ip": ip,
        "message_id": message_id,
        "size_bytes": size_bytes,
    }


def fetch_rspamd_history(
    rspamd_url: str,
    rspamd_password: str = "",
    *,
    rspamd_user: str = "",
    rspamd_tls_verify: bool = False,
    fallback_host: str = "",
) -> list[dict[str, Any]]:
    """Fetch `/history` from Rspamd Controller or NS8 Traefik `/rspamd` HTTPS route."""
    base = normalize_rspamd_base_url(rspamd_url)
    if not base:
        raise RuntimeError("Rspamd URL is not configured")

    user_clean = (rspamd_user or "").strip()
    pw_clean = rspamd_password or ""

    headers: dict[str, str] = {}
    auth: tuple[str, str] | None = None
    if user_clean:
        auth = (user_clean, pw_clean)
    elif pw_clean:
        headers["Password"] = pw_clean

    target_url = f"{base}/history"

    def _do_request(
        client: httpx.Client,
        url: str,
        req_headers: dict[str, str],
        req_auth: tuple[str, str] | None,
    ) -> httpx.Response:
        return client.get(url, headers=req_headers, auth=req_auth)

    with httpx.Client(
        timeout=10.0,
        verify=bool(rspamd_tls_verify),
        follow_redirects=True,
    ) as client:
        active_url = target_url
        active_headers = dict(headers)
        try:
            resp = _do_request(client, active_url, active_headers, auth)
        except (httpx.ConnectError, httpx.ConnectTimeout) as conn_exc:
            # Hairpin-NAT / Split-DNS fallback via mail_host or CONTAINER_HOST_GATEWAY
            gw = os.environ.get("CONTAINER_HOST_GATEWAY", "").strip()
            fb_target = (fallback_host or gw or "").strip()
            fb_pair = _build_hairpin_fallback_url(base, fb_target)
            if fb_pair is None:
                raise
            fb_base, orig_host_hdr = fb_pair
            active_url = f"{fb_base}/history"
            active_headers = {**headers, "Host": orig_host_hdr}
            try:
                resp = _do_request(client, active_url, active_headers, auth)
            except Exception:
                raise conn_exc

        # If `user:password` was entered into rspamd_password on a `/rspamd` route, retry Basic Auth
        if (
            resp.status_code == 401
            and not user_clean
            and ":" in pw_clean
            and "/rspamd" in base.lower()
        ):
            u_part, p_part = pw_clean.split(":", 1)
            if u_part.strip():
                retry_headers = {k: v for k, v in active_headers.items() if k != "Password"}
                resp = _do_request(client, active_url, retry_headers, (u_part.strip(), p_part))

        if resp.status_code == 401:
            www_auth = (resp.headers.get("www-authenticate") or "").lower()
            if "basic" in www_auth and not user_clean:
                raise RuntimeError(
                    "Rspamd HTTPS route requires HTTP Basic Auth — please enter an NS8 admin "
                    "username (e.g. admin) in Rspamd Username."
                )
            raise RuntimeError("Rspamd authentication failed (HTTP 401)")

        if resp.status_code == 403:
            raise RuntimeError("Rspamd authentication failed (HTTP 403)")

        resp.raise_for_status()
        payload = resp.json()

    if isinstance(payload, dict):
        raw_rows = payload.get("rows")
        if not isinstance(raw_rows, list):
            raw_rows = []
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raw_rows = []

    normalized: list[dict[str, Any]] = []
    for item in raw_rows:
        norm = _normalize_history_row(item)
        if norm is not None:
            normalized.append(norm)
    return normalized


def sync_and_search_mail_history(
    *,
    sender: str = "",
    recipient: str = "",
    subject: str = "",
    action_filter: str = "all",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Sync latest Rspamd `/history` into SQLite cache (best-effort) and query cached history."""
    settings = get_settings()
    rspamd_connected = False
    rspamd_error = ""

    if settings.runtime.rspamd_url:
        try:
            fresh_rows = fetch_rspamd_history(
                settings.runtime.effective_rspamd_url,
                settings.runtime.rspamd_password,
                rspamd_user=settings.runtime.rspamd_user,
                rspamd_tls_verify=settings.runtime.rspamd_tls_verify,
                fallback_host=settings.mail.host,
            )
            upsert_mail_history_rows(fresh_rows, settings.sqlite_path)
            rspamd_connected = True
        except Exception as exc:
            rspamd_error = str(exc)
            logger.debug("Rspamd history poll failed (serving cached history): %s", exc)

    total, entries = query_mail_history_cache(
        sender=sender,
        recipient=recipient,
        subject=subject,
        action_filter=action_filter,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        db_path=settings.sqlite_path,
    )
    return {
        "rspamd_connected": rspamd_connected,
        "rspamd_error": rspamd_error,
        "total": total,
        "entries": entries,
    }


def test_rspamd_connection(
    *,
    rspamd_url: str | None = None,
    rspamd_user: str | None = None,
    rspamd_password: str | None = None,
    rspamd_tls_verify: bool | None = None,
) -> dict[str, Any]:
    """Verify connectivity and authentication against Rspamd Controller `/history`."""
    settings = get_settings()
    raw_url = (rspamd_url or "").strip() or settings.runtime.rspamd_url
    url = translate_loopback_url(normalize_rspamd_base_url(raw_url))
    user = rspamd_user.strip() if rspamd_user is not None else settings.runtime.rspamd_user
    pw = (
        rspamd_password
        if (rspamd_password is not None and rspamd_password != "")
        else settings.runtime.rspamd_password
    )
    tls_verify = (
        bool(rspamd_tls_verify)
        if rspamd_tls_verify is not None
        else bool(settings.runtime.rspamd_tls_verify)
    )
    try:
        rows = fetch_rspamd_history(
            url,
            pw,
            rspamd_user=user,
            rspamd_tls_verify=tls_verify,
            fallback_host=settings.mail.host,
        )
        upsert_mail_history_rows(rows, settings.sqlite_path)
        return {
            "ok": True,
            "message": f"Connected to Rspamd ({len(rows)} history entries available).",
            "entries_available": len(rows),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": str(exc),
            "entries_available": 0,
        }
