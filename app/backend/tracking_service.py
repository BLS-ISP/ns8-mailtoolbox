"""Phase 3b Fast Mail-Tracking Search backed by Rspamd Controller `/history` + SQLite cache."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from typing import Any

import httpx

from app.backend.config import get_settings, translate_loopback_url
from app.backend.db import query_mail_history_cache, upsert_mail_history_rows

logger = logging.getLogger(__name__)


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
            float(raw.get("required_score", raw.get(" thresholds", 15.0)) or 0.0), 2
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
) -> list[dict[str, Any]]:
    """Fetch `/history` from Rspamd Controller HTTP API."""
    base = (rspamd_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Rspamd URL is not configured")

    headers: dict[str, str] = {}
    if rspamd_password:
        headers["Password"] = rspamd_password

    with httpx.Client(timeout=8.0) as client:
        resp = client.get(f"{base}/history", headers=headers)
        if resp.status_code in (401, 403):
            raise RuntimeError(f"Rspamd authentication failed (HTTP {resp.status_code})")
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
    rspamd_password: str | None = None,
) -> dict[str, Any]:
    """Verify connectivity and authentication against Rspamd Controller `/history`."""
    settings = get_settings()
    url = translate_loopback_url((rspamd_url or "").strip() or settings.runtime.rspamd_url)
    pw = (
        rspamd_password
        if (rspamd_password is not None and rspamd_password != "")
        else settings.runtime.rspamd_password
    )
    try:
        rows = fetch_rspamd_history(url, pw)
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
