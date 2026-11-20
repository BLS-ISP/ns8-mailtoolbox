"""Portal session cookie signing, internal API token verification, and IP rate limiting."""

from __future__ import annotations

import base64
from collections import deque
import hashlib
import hmac
import json
import threading
import time
from typing import Any

from fastapi import HTTPException, Request, Response, status

from app.backend.config import get_settings
from app.backend.ldap_client import AuthenticatedUser

SESSION_COOKIE_NAME = "ns8_toolbox_session"

_rate_lock = threading.Lock()
_failed_login_attempts: dict[str, deque[float]] = {}
_revoked_lock = threading.Lock()
_revoked_tokens: dict[str, int] = {}


def reset_rate_limiter() -> None:
    """Clear in-memory failed login counters (useful in tests)."""
    with _rate_lock:
        _failed_login_attempts.clear()
    with _revoked_lock:
        _revoked_tokens.clear()


def revoke_session_token(token: str | None) -> None:
    """Revoke an active session token immediately upon logout."""
    if not token:
        return
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    ttl = get_settings().session_ttl_seconds
    with _revoked_lock:
        expired_keys = [k for k, exp in _revoked_tokens.items() if exp < now]
        for k in expired_keys:
            _revoked_tokens.pop(k, None)
        _revoked_tokens[digest] = now + max(60, ttl)


def get_client_ip(request: Request) -> str:
    """Resolve client IP, honoring X-Forwarded-For when behind local Traefik proxy."""
    direct_host = request.client.host if request.client else "unknown"
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff and direct_host in ("127.0.0.1", "::1", "localhost", "testclient"):
        first_hop = xff.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    return direct_host


def check_login_rate_limit(ip: str) -> None:
    """Raise HTTP 429 if IP has exceeded max failed login attempts in window."""
    settings = get_settings()
    now = time.monotonic()
    cutoff = now - float(settings.login_rate_limit_window_seconds)
    with _rate_lock:
        bucket = _failed_login_attempts.get(ip)
        if not bucket:
            return
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= settings.login_rate_limit_max:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Please wait before retrying.",
            )


def record_failed_login(ip: str) -> None:
    """Record a failed login timestamp for `ip`."""
    settings = get_settings()
    now = time.monotonic()
    cutoff = now - float(settings.login_rate_limit_window_seconds)
    with _rate_lock:
        bucket = _failed_login_attempts.setdefault(ip, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)


def clear_failed_logins(ip: str) -> None:
    """Clear failed login counter for `ip` after successful login."""
    with _rate_lock:
        _failed_login_attempts.pop(ip, None)


def create_session_token(
    user: AuthenticatedUser,
    *,
    secret_key: str | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Create an HMAC-SHA256 signed session cookie token."""
    settings = get_settings()
    key = (secret_key or settings.secret_key).encode("utf-8")
    ttl = ttl_seconds if ttl_seconds is not None else settings.session_ttl_seconds
    now = int(time.time())
    payload = {
        "u": user.username,
        "dn": user.display_name,
        "g": list(user.groups),
        "iat": now,
        "exp": now + int(ttl),
    }
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw_json).rstrip(b"=").decode("ascii")
    sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    return f"{payload_b64}.{sig_b64}"


def verify_session_token(
    token: str,
    *,
    secret_key: str | None = None,
) -> AuthenticatedUser | None:
    """Verify HMAC signature, revocation state, and expiry of a session cookie token."""
    if not token or "." not in token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _revoked_lock:
        if digest in _revoked_tokens:
            return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    payload_b64, sig_b64 = parts
    if not payload_b64 or not sig_b64:
        return None

    settings = get_settings()
    key = (secret_key or settings.secret_key).encode("utf-8")
    expected_sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected_sig).rstrip(b"=").decode("ascii")

    if not hmac.compare_digest(sig_b64, expected_b64):
        return None

    try:
        padding = "=" * (-len(payload_b64) % 4)
        raw_json = base64.urlsafe_b64decode((payload_b64 + padding).encode("ascii"))
        data: dict[str, Any] = json.loads(raw_json.decode("utf-8"))
    except Exception:
        return None

    exp = data.get("exp")
    username = data.get("u")
    if not isinstance(exp, int) or int(time.time()) > exp:
        return None
    if not isinstance(username, str) or not username.strip():
        return None

    display_name = str(data.get("dn") or username)
    groups_raw = data.get("g")
    groups = [str(g) for g in groups_raw] if isinstance(groups_raw, list) else []
    return AuthenticatedUser(username=username, display_name=display_name, groups=groups)


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    """Attach signed HttpOnly SameSite=lax session cookie."""
    settings = get_settings()
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    is_https = request.url.scheme == "https" or forwarded_proto == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Delete portal session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", samesite="lax")


def require_portal_session(request: Request) -> AuthenticatedUser:
    """FastAPI dependency enforcing a valid portal session cookie."""
    cookie_val = request.cookies.get(SESSION_COOKIE_NAME, "")
    user = verify_session_token(cookie_val)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


def require_internal_token(request: Request) -> str:
    """FastAPI dependency enforcing X-Internal-Token shared secret on /api/internal/*."""
    settings = get_settings()
    expected = settings.internal_token
    provided = (request.headers.get("x-internal-token") or "").strip()
    if not expected or not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Internal-Token",
        )
    return provided
