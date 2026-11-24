"""Phase 3a Spam Quarantine Digest, Scanner-Safe Public Token Release & Sender Allowlist.

Connects to Dovecot via IMAP Master-User (`\x00user*vmail\x00pass`), inspects `Junk` headers via
`BODY.PEEK[HEADER]`, delivers daily Carbon HTML+Plaintext digest emails directly into local users'
`INBOX` via Dovecot Master-User IMAP `APPEND` (bypassing Rspamd false-positive spam scoring, with
SMTP fallback), provides two-step link-scanner-safe release endpoints
(`GET /api/quarantine/token/{token}` + `POST /api/quarantine/action`), and rescues allowlisted
senders from `Junk` to `INBOX`.
"""

from __future__ import annotations

from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import email
import email.policy
from email.utils import parseaddr, parsedate_to_datetime
import imaplib
import logging
import re
import secrets
import smtplib
import threading
import time
from typing import Any, Callable, Iterator

from app.backend.config import AppSettings, MailSettings, get_settings
from app.backend.db import (
    add_audit_entry,
    add_sender_allowlist,
    create_quarantine_token,
    delete_sender_allowlist_entry,
    get_digested_keys_for_user,
    get_meta,
    get_quarantine_token_row,
    list_sender_allowlist,
    mark_messages_digested,
    mark_quarantine_token_used,
    set_meta,
    utc_now_iso,
)
from app.backend.email_templates import (
    MAX_DIGEST_DISPLAY_ITEMS,
    build_quarantine_digest_email,
    resolve_sender_domain,
)
from app.backend.service import build_base_mailbox_map

logger = logging.getLogger(__name__)

_SPAMD_SCORE_RE = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*/\s*-?\d+(?:\.\d+)?\s*\]")
_SPAM_STATUS_SCORE_RE = re.compile(r"score=(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_PLAIN_FLOAT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)")
_IMAP_UID_TOKEN_RE = re.compile(rb"\bUID\s+(\d+)\b", re.IGNORECASE)
_IMAP_MONTHS_ASCII = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_FETCH_BATCH_CHUNK_SIZE = 50
_QUARANTINE_MAX_WORKERS = 6
_QUARANTINE_CACHE_TTL_SECONDS = 30.0
_QUARANTINE_SCAN_SOFT_DEADLINE_SECONDS = 20.0

_quarantine_cache_lock = threading.Lock()
_quarantine_scan_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

ImapFactory = Callable[[str, MailSettings], Any]
SmtpSenderFn = Callable[[bytes, str, str, MailSettings], None]

_custom_imap_factory: ImapFactory | None = None
_custom_smtp_sender: SmtpSenderFn | None = None


def clear_quarantine_cache() -> None:
    """Invalidate the 30-second in-memory admin quarantine scan cache."""
    with _quarantine_cache_lock:
        _quarantine_scan_cache.clear()


def set_imap_master_factory(factory: ImapFactory | None) -> None:
    """Override Dovecot Master-User IMAP client creation (for tests)."""
    global _custom_imap_factory
    _custom_imap_factory = factory
    clear_quarantine_cache()


def set_smtp_sender(sender: SmtpSenderFn | None) -> None:
    """Override SMTP delivery function (for tests)."""
    global _custom_smtp_sender
    _custom_smtp_sender = sender


@contextmanager
def open_master_imap(
    target_user: str,
    mail_cfg: MailSettings,
    *,
    timeout: float = 6.0,
) -> Iterator[Any]:
    """Authenticate to Dovecot IMAP as `target_user*master_user` via SASL PLAIN."""
    if _custom_imap_factory is not None:
        conn = _custom_imap_factory(target_user, mail_cfg)
        try:
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return

    if not mail_cfg.host or not mail_cfg.master_password:
        raise RuntimeError("Dovecot IMAP host or master credentials not configured")

    sock_timeout = max(0.5, float(timeout))
    if int(mail_cfg.imap_port) == 993:
        conn = imaplib.IMAP4_SSL(mail_cfg.host, int(mail_cfg.imap_port), timeout=sock_timeout)
    else:
        conn = imaplib.IMAP4(mail_cfg.host, int(mail_cfg.imap_port), timeout=sock_timeout)

    try:
        auth_bytes = (
            f"\x00{target_user}*{mail_cfg.master_user}\x00{mail_cfg.master_password}".encode("utf-8")
        )
        try:
            conn.authenticate("PLAIN", lambda _challenge: auth_bytes)
        except imaplib.IMAP4.error as auth_exc:
            err_upper = str(auth_exc).upper()
            stripped_cmd = err_upper.replace("AUTHENTICATE", "")
            if (
                ("BAD" in err_upper or "UNKNOWN" in err_upper)
                and "AUTHENTICATIONFAILED" not in err_upper
                and "AUTH" not in stripped_cmd
            ):
                conn.login(f"{target_user}*{mail_cfg.master_user}", mail_cfg.master_password)
            else:
                raise
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def deliver_smtp_raw(
    raw_bytes: bytes,
    mail_from: str,
    rcpt_to: str,
    mail_cfg: MailSettings,
) -> None:
    """Send an RFC 5322 message via internal SMTP submission."""
    if _custom_smtp_sender is not None:
        _custom_smtp_sender(raw_bytes, mail_from, rcpt_to, mail_cfg)
        return

    if not mail_cfg.host:
        raise RuntimeError("MAIL_HOST is not configured for SMTP delivery")

    with smtplib.SMTP(mail_cfg.host, int(mail_cfg.submission_port), timeout=10.0) as smtp:
        smtp.ehlo_or_helo_if_needed()
        smtp.sendmail(mail_from, [rcpt_to], raw_bytes)


def resolve_local_mailbox_user(
    address_or_user: str,
    base_map: dict[str, dict[str, Any]],
) -> str:
    """Resolve an email address, alias, or username to a local NS8 mailbox username (or '')."""
    raw = (address_or_user or "").strip()
    if not raw or not base_map:
        return ""
    candidate = (parseaddr(raw)[1] or raw).strip().lower()
    if not candidate:
        return ""

    for u_key, meta in base_map.items():
        if not meta.get("enabled", True):
            continue
        u_name = str(meta.get("user") or u_key).strip()
        if candidate in (u_key.lower(), u_name.lower()):
            return u_name
        primary_mail = (parseaddr(str(meta.get("mail") or ""))[1] or str(meta.get("mail") or "")).strip().lower()
        if primary_mail and candidate == primary_mail:
            return u_name
        for alias in meta.get("aliases") or []:
            alias_mail = (parseaddr(str(alias))[1] or str(alias)).strip().lower()
            if alias_mail and candidate == alias_mail:
                return u_name
    return ""


def deliver_to_user_inbox_or_smtp(
    raw_bytes: bytes,
    mail_from: str,
    rcpt_to: str,
    target_user: str,
    mail_cfg: MailSettings,
) -> str:
    """Deliver a system email directly into `INBOX` via Dovecot Master-User IMAP APPEND, or SMTP fallback."""
    if _custom_smtp_sender is not None:
        _custom_smtp_sender(raw_bytes, mail_from, rcpt_to, mail_cfg)
        return "smtp"

    clean_user = (target_user or "").strip()
    if clean_user and mail_cfg.host and mail_cfg.master_password:
        try:
            with open_master_imap(clean_user, mail_cfg) as conn:
                typ, _resp = conn.append(
                    "INBOX",
                    "",
                    imaplib.Time2Internaldate(time.time()),
                    raw_bytes,
                )
                if typ != "OK":
                    raise RuntimeError(f"IMAP APPEND to INBOX returned {typ!r}")
            return "imap_append"
        except Exception as exc:
            logger.warning(
                "Direct IMAP APPEND to INBOX failed for %s (%s); falling back to SMTP",
                clean_user,
                exc,
            )

    deliver_smtp_raw(raw_bytes, mail_from, rcpt_to, mail_cfg)
    return "smtp"


def _extract_spam_score(msg: email.message.Message) -> float | None:
    spamd_hdr = str(msg.get("X-Spamd-Result") or "")
    if spamd_hdr:
        m = _SPAMD_SCORE_RE.search(spamd_hdr)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass

    score_hdr = str(msg.get("X-Spam-Score") or "")
    if score_hdr:
        m = _PLAIN_FLOAT_RE.match(score_hdr)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass

    status_hdr = str(msg.get("X-Spam-Status") or "")
    if status_hdr:
        m = _SPAM_STATUS_SCORE_RE.search(status_hdr)
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass
    return None


def _parse_received_date(date_hdr: str) -> str:
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    return utc_now_iso()


def _extract_fetch_bytes(fetch_data: Any) -> bytes | None:
    if not fetch_data:
        return None
    for part in fetch_data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            return bytes(part[1])
    return None


def _fetch_uid_header_bytes(imap: Any, uid: str) -> bytes | None:
    """Fetch only RFC 5322 headers via `BODY.PEEK[HEADER]`, falling back to `(RFC822)` if empty."""
    try:
        f_typ, f_data = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
        if f_typ == "OK":
            raw_hdr = _extract_fetch_bytes(f_data)
            if raw_hdr:
                return raw_hdr
    except Exception:
        pass

    f_typ2, f_data2 = imap.uid("FETCH", uid, "(RFC822)")
    if f_typ2 != "OK":
        return None
    return _extract_fetch_bytes(f_data2)


def _is_toolbox_system_email(
    parsed: email.message.Message,
    sender_raw: str,
    sender_addr: str,
    subject: str,
) -> bool:
    """Detect Mail Toolbox's own digests/alerts in `Junk` to prevent self-digest loops."""
    if str(parsed.get("X-NS8-Mail-Toolbox") or "").strip():
        return True
    addr_low = (sender_addr or "").strip().lower()
    raw_low = (sender_raw or "").strip().lower()
    if addr_low.startswith(("quarantine-noreply@", "connector-alert@")):
        return True
    if addr_low.startswith("noreply@") and "mail toolbox" in raw_low:
        return True
    if subject.startswith(("Spam Quarantine Digest (", "[NS8 Mail Toolbox]")):
        return True
    return False


def _format_imap_since_date(days: int) -> str:
    """Format an RFC 3501 English ASCII `DD-Mon-YYYY` date string (`SINCE` criterion)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).date()
    mon = _IMAP_MONTHS_ASCII[cutoff.month - 1]
    return f"{cutoff.day:02d}-{mon}-{cutoff.year:04d}"


def _fetch_uids_headers_batch(
    imap: Any,
    uids: list[str],
    chunk_size: int = _FETCH_BATCH_CHUNK_SIZE,
) -> dict[str, bytes]:
    """Fetch headers for `uids` in chunks of 50 via `(UID BODY.PEEK[HEADER])`, with per-UID fallback."""
    results: dict[str, bytes] = {}
    if not uids:
        return results

    step = max(1, int(chunk_size))
    for start in range(0, len(uids), step):
        chunk = [str(u) for u in uids[start : start + step] if str(u)]
        if not chunk:
            continue
        chunk_set = set(chunk)
        try:
            f_typ, f_data = imap.uid("FETCH", ",".join(chunk), "(UID BODY.PEEK[HEADER])")
            if f_typ == "OK" and f_data:
                for part in f_data:
                    if not (
                        isinstance(part, tuple)
                        and len(part) >= 2
                        and isinstance(part[1], (bytes, bytearray))
                        and part[1]
                    ):
                        continue
                    meta_bytes = (
                        bytes(part[0])
                        if isinstance(part[0], (bytes, bytearray))
                        else str(part[0]).encode("ascii", errors="ignore")
                    )
                    m_uid = _IMAP_UID_TOKEN_RE.search(meta_bytes)
                    if m_uid:
                        u_str = m_uid.group(1).decode("ascii")
                        if u_str in chunk_set:
                            results[u_str] = bytes(part[1])
                    elif len(chunk) == 1:
                        lead_m = re.match(rb"^\s*(\d+)\b", meta_bytes)
                        if lead_m and lead_m.group(1).decode("ascii") == chunk[0]:
                            results[chunk[0]] = bytes(part[1])
        except Exception:
            pass

        for uid in chunk:
            if uid not in results:
                raw_single = _fetch_uid_header_bytes(imap, uid)
                if raw_single:
                    results[uid] = raw_single

    return results


def fetch_user_junk_items(
    user: str,
    recipient_email: str,
    mail_cfg: MailSettings,
    *,
    max_uids: int = 100,
    since_days: int | None = None,
    imap_timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """Read recent message headers currently sitting in `user`'s `Junk` IMAP folder."""
    items: list[dict[str, Any]] = []
    with open_master_imap(user, mail_cfg, timeout=imap_timeout) as imap:
        typ, _ = imap.select(mail_cfg.spam_folder, readonly=True)
        if typ != "OK":
            return []

        s_typ = "NO"
        s_data: Any = None
        if since_days is not None and int(since_days) > 0:
            since_str = _format_imap_since_date(int(since_days))
            try:
                s_typ, s_data = imap.uid("SEARCH", None, "SINCE", since_str)
            except Exception:
                s_typ = "NO"
        if s_typ != "OK":
            s_typ, s_data = imap.uid("SEARCH", None, "ALL")
        if s_typ != "OK" or not s_data or not s_data[0]:
            return []

        raw_uids = s_data[0].decode("ascii", errors="ignore").split()
        if not raw_uids:
            return []
        if max_uids and int(max_uids) > 0 and len(raw_uids) > int(max_uids):
            raw_uids = raw_uids[-int(max_uids) :]

        hdr_map = _fetch_uids_headers_batch(imap, raw_uids)
        for uid in raw_uids:
            raw_msg = hdr_map.get(str(uid))
            if not raw_msg:
                continue
            parsed = email.message_from_bytes(raw_msg, policy=email.policy.default)
            sender_raw = str(parsed.get("From") or "").strip()
            sender_addr = parseaddr(sender_raw)[1].strip() or sender_raw
            subject = str(parsed.get("Subject") or "(No Subject)").strip()
            if _is_toolbox_system_email(parsed, sender_raw, sender_addr, subject):
                continue

            msg_id = str(parsed.get("Message-ID") or "").strip() or f"<uid-{user}-{uid}@quarantine>"
            received_at = _parse_received_date(str(parsed.get("Date") or ""))
            spam_score = _extract_spam_score(parsed)

            items.append(
                {
                    "id": f"{user}:{uid}",
                    "user": user,
                    "recipient": recipient_email or user,
                    "sender": sender_addr,
                    "sender_display": sender_raw or sender_addr,
                    "subject": subject,
                    "received_at": received_at,
                    "spam_score": spam_score,
                    "uid": str(uid),
                    "message_id": msg_id,
                }
            )

    items.sort(key=lambda x: x["received_at"], reverse=True)
    return items


def move_junk_to_inbox(
    *,
    user: str,
    uid: str = "",
    message_id: str = "",
    mail_cfg: MailSettings | None = None,
) -> bool:
    """Move a quarantined email from `Junk` to `INBOX` (`UID COPY` + `\\Deleted` + `EXPUNGE`)."""
    cfg = mail_cfg or get_settings().mail
    with open_master_imap(user, cfg) as imap:
        typ, _ = imap.select(cfg.spam_folder, readonly=False)
        if typ != "OK":
            return False

        s_typ, s_data = imap.uid("SEARCH", None, "ALL")
        if s_typ != "OK" or not s_data or not s_data[0]:
            return False
        existing_uids = s_data[0].decode("ascii", errors="ignore").split()
        if not existing_uids:
            return False

        target_uid: str | None = None
        if uid and uid in existing_uids:
            if not message_id:
                target_uid = uid
            else:
                raw = _fetch_uid_header_bytes(imap, uid)
                if raw:
                    parsed = email.message_from_bytes(raw, policy=email.policy.default)
                    cur_mid = str(parsed.get("Message-ID") or "").strip()
                    if not cur_mid or cur_mid == message_id or message_id.startswith("<uid-"):
                        target_uid = uid

        if target_uid is None and message_id:
            for cand_uid in existing_uids:
                raw = _fetch_uid_header_bytes(imap, cand_uid)
                if not raw:
                    continue
                parsed = email.message_from_bytes(raw, policy=email.policy.default)
                cur_mid = str(parsed.get("Message-ID") or "").strip()
                if cur_mid and cur_mid == message_id:
                    target_uid = cand_uid
                    break

        if target_uid is None:
            return False

        c_typ, _ = imap.uid("COPY", target_uid, "INBOX")
        if c_typ != "OK":
            raise RuntimeError(f"IMAP COPY to INBOX failed for {user} UID {target_uid}")
        imap.uid("STORE", target_uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        return True


def _resolve_public_mailbox_email(meta: dict[str, Any], fallback_domain: str) -> str:
    u_name = str(meta.get("user") or "").strip()
    raw_mb_mail = str(meta.get("mail") or "").strip()
    raw_mb_dom = raw_mb_mail.split("@", 1)[1].strip().lower() if "@" in raw_mb_mail else ""
    if not raw_mb_mail or (raw_mb_dom.startswith(("ad.", "ldap.")) and raw_mb_dom.count(".") >= 2):
        public_alias = next(
            (
                str(a).strip()
                for a in (meta.get("aliases") or [])
                if "@" in str(a)
                and not str(a).split("@", 1)[1].strip().lower().startswith(("ad.", "ldap."))
            ),
            "",
        )
        if public_alias:
            return public_alias
        return f"{u_name}@{fallback_domain}" if fallback_domain else (raw_mb_mail or u_name)
    return raw_mb_mail


def rescue_allowlisted_junk_messages(settings: AppSettings | None = None) -> int:
    """Move any Junk messages sent by allowlisted senders into the user's INBOX."""
    clear_quarantine_cache()
    cfg = settings or get_settings()
    if not cfg.mail.host or not cfg.mail.master_password:
        return 0

    allow_entries = list_sender_allowlist(db_path=cfg.sqlite_path)
    if not allow_entries:
        return 0

    try:
        base_map = build_base_mailbox_map(cfg)
    except Exception:
        base_map = {}
    base_by_lower = {k.lower(): v for k, v in base_map.items()}

    by_user: dict[str, set[str]] = {}
    for entry in allow_entries:
        by_user.setdefault(entry["user"], set()).add(entry["sender_email"].strip().lower())

    rescued = 0
    for user, allowed_senders in by_user.items():
        mb_meta = base_by_lower.get(user.lower())
        if base_by_lower and (mb_meta is None or not mb_meta.get("enabled", True)):
            continue
        try:
            junk_items = fetch_user_junk_items(user, user, cfg.mail, max_uids=100, imap_timeout=5.0)
            for item in junk_items:
                norm_sender = parseaddr(item["sender"])[1].strip().lower() or item["sender"].strip().lower()
                if norm_sender in allowed_senders:
                    if move_junk_to_inbox(
                        user=user,
                        uid=item["uid"],
                        message_id=item["message_id"],
                        mail_cfg=cfg.mail,
                    ):
                        rescued += 1
                        add_audit_entry(
                            actor="system",
                            source="reconciler",
                            target_user=user,
                            action="quarantine_allowlist_rescued",
                            details=f"Auto-delivered allowlisted sender {norm_sender} to INBOX",
                            db_path=cfg.sqlite_path,
                        )
        except Exception as exc:
            logger.debug("Allowlist rescue skipped for %s: %s", user, exc)
    if rescued > 0:
        clear_quarantine_cache()
    return rescued


def run_quarantine_digest(*, user_filter: str = "") -> dict[str, int]:
    """Scan Junk folders in parallel and deliver digest emails ONLY to users with >=1 new spam message."""
    clear_quarantine_cache()
    settings = get_settings()
    rescue_allowlisted_junk_messages(settings)

    base_map = build_base_mailbox_map(settings)
    target_user = (user_filter or "").strip()

    candidates: list[dict[str, Any]] = []
    for u_key, meta in sorted(base_map.items()):
        if not meta.get("enabled", True):
            continue
        if target_user and u_key.lower() != target_user.lower():
            continue
        candidates.append(meta)

    fallback_domain = resolve_sender_domain("", settings, base_map)
    if target_user and not candidates:
        fallback_mail = (
            f"{target_user}@{fallback_domain}" if fallback_domain else target_user
        )
        candidates.append({"user": target_user, "mail": fallback_mail, "enabled": True})

    scan_targets: list[tuple[str, str]] = [
        (str(mb["user"]), _resolve_public_mailbox_email(mb, fallback_domain))
        for mb in candidates
    ]

    scanned_by_user: dict[str, list[dict[str, Any]]] = {}
    failed_users: dict[str, Exception] = {}
    if scan_targets:
        workers = min(_QUARANTINE_MAX_WORKERS, len(scan_targets))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            fut_map = {
                executor.submit(
                    fetch_user_junk_items,
                    u_name,
                    rcpt_email,
                    settings.mail,
                    max_uids=100,
                    since_days=14,
                    imap_timeout=5.0,
                ): u_name
                for u_name, rcpt_email in scan_targets
            }
            for fut in as_completed(fut_map):
                u_name = fut_map[fut]
                try:
                    scanned_by_user[u_name] = fut.result()
                except Exception as exc:
                    failed_users[u_name] = exc

    users_checked = 0
    digests_sent = 0
    items_included = 0
    now = datetime.now(timezone.utc)
    expires_iso = (now + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for u_name, rcpt_email in scan_targets:
        users_checked += 1
        if u_name in failed_users:
            logger.warning("Failed inspecting Junk folder for %s: %s", u_name, failed_users[u_name])
            continue

        junk_items = scanned_by_user.get(u_name) or []
        if not junk_items:
            continue

        already_digested = get_digested_keys_for_user(u_name, settings.sqlite_path)
        new_items = [it for it in junk_items if it["message_id"] not in already_digested]
        if not new_items:
            continue

        displayed_items = new_items[:MAX_DIGEST_DISPLAY_ITEMS]
        displayed_items_with_tokens: list[tuple[dict[str, Any], str | None]] = []
        for it in displayed_items:
            token = secrets.token_urlsafe(24)
            create_quarantine_token(
                token=token,
                mail_id=it["message_id"],
                uid=it["uid"],
                recipient_user=u_name,
                recipient=rcpt_email,
                sender=it["sender"],
                subject=it["subject"],
                received_at=it["received_at"],
                spam_score=it["spam_score"],
                expires_at=expires_iso,
                db_path=settings.sqlite_path,
            )
            displayed_items_with_tokens.append((it, token))

        sender_domain = resolve_sender_domain(rcpt_email, settings, base_map)
        raw_digest, env_from = build_quarantine_digest_email(
            recipient_email=rcpt_email,
            sender_domain=sender_domain,
            portal_enabled=settings.portal_enabled,
            portal_hostname=settings.portal_hostname,
            tz_name=settings.runtime.timezone,
            displayed_items_with_tokens=displayed_items_with_tokens,
            total_new_count=len(new_items),
        )
        deliver_to_user_inbox_or_smtp(
            raw_digest,
            env_from,
            rcpt_email,
            u_name,
            settings.mail,
        )
        mark_messages_digested(
            u_name,
            [it["message_id"] for it in new_items],
            settings.sqlite_path,
        )
        digests_sent += 1
        items_included += len(new_items)

    now_iso = utc_now_iso()
    set_meta("digest_last_run_at", now_iso, settings.sqlite_path)
    set_meta("digest_last_digests_sent", str(digests_sent), settings.sqlite_path)
    clear_quarantine_cache()
    return {
        "users_checked": users_checked,
        "digests_sent": digests_sent,
        "items_included": items_included,
    }


def list_admin_quarantine(
    *,
    user_filter: str = "",
    search: str = "",
    limit: int = 200,
    force_refresh: bool = False,
    soft_deadline_seconds: float | None = None,
) -> dict[str, Any]:
    """Return quarantined Junk items across enabled mailboxes, digest status, and sender allowlist."""
    settings = get_settings()
    u_filt = (user_filter or "").strip().lower()
    q_search = (search or "").strip().lower()
    cache_key = (str(settings.sqlite_path), u_filt)

    raw_scanned: list[dict[str, Any]] | None = None
    if not force_refresh:
        with _quarantine_cache_lock:
            cached = _quarantine_scan_cache.get(cache_key)
            if cached is not None and (time.monotonic() - cached[0]) < _QUARANTINE_CACHE_TTL_SECONDS:
                raw_scanned = [dict(it) for it in cached[1]]

    if raw_scanned is None:
        raw_scanned = []
        timed_out_scan = False
        if settings.mail.host and settings.mail.master_password:
            base_map = build_base_mailbox_map(settings)
            fallback_domain = resolve_sender_domain("", settings, base_map)
            targets: list[tuple[str, str]] = []
            for u_name, meta in sorted(base_map.items()):
                if not meta.get("enabled", True):
                    continue
                if u_filt and u_name.lower() != u_filt:
                    continue
                rcpt = _resolve_public_mailbox_email(meta, fallback_domain)
                targets.append((u_name, rcpt))

            if targets:
                deadline = float(
                    soft_deadline_seconds
                    if soft_deadline_seconds is not None
                    else _QUARANTINE_SCAN_SOFT_DEADLINE_SECONDS
                )
                workers = min(_QUARANTINE_MAX_WORKERS, len(targets))
                executor = ThreadPoolExecutor(max_workers=workers)
                fut_map = {
                    executor.submit(
                        fetch_user_junk_items,
                        u_name,
                        rcpt,
                        settings.mail,
                        max_uids=100,
                        imap_timeout=5.0,
                    ): u_name
                    for u_name, rcpt in targets
                }
                try:
                    for fut in as_completed(fut_map, timeout=deadline):
                        u_name = fut_map[fut]
                        try:
                            user_items = fut.result()
                            for item in user_items:
                                raw_scanned.append(
                                    {
                                        "id": item["id"],
                                        "user": item["user"],
                                        "recipient": item["recipient"],
                                        "sender": item["sender"],
                                        "subject": item["subject"],
                                        "received_at": item["received_at"],
                                        "spam_score": item["spam_score"],
                                        "uid": item["uid"],
                                        "message_id": item["message_id"],
                                    }
                                )
                        except Exception as exc:
                            logger.debug("Quarantine listing skipped for %s: %s", u_name, exc)
                except FuturesTimeoutError:
                    timed_out_scan = True
                    pending = [u for f, u in fut_map.items() if not f.done()]
                    logger.warning(
                        "Quarantine IMAP scan hit %.1fs soft deadline; returning partial results (%d mailbox(es) pending: %s)",
                        deadline,
                        len(pending),
                        ", ".join(pending[:10]),
                    )
                finally:
                    executor.shutdown(wait=not timed_out_scan, cancel_futures=True)

        raw_scanned.sort(key=lambda x: x["received_at"], reverse=True)
        if not timed_out_scan:
            with _quarantine_cache_lock:
                _quarantine_scan_cache[cache_key] = (
                    time.monotonic(),
                    [dict(it) for it in raw_scanned],
                )

    if q_search:
        all_items = [
            it
            for it in raw_scanned
            if q_search in f"{it['recipient']} {it['sender']} {it['subject']}".lower()
        ]
    else:
        all_items = list(raw_scanned)

    all_items.sort(key=lambda x: x["received_at"], reverse=True)
    clamped = max(1, min(int(limit), 1000))

    last_sent_raw = get_meta("digest_last_digests_sent", "0", settings.sqlite_path)
    try:
        last_sent = int(last_sent_raw)
    except ValueError:
        last_sent = 0

    return {
        "items": all_items[:clamped],
        "digest_status": {
            "enabled": settings.runtime.digest_enabled,
            "digest_time": settings.runtime.digest_time,
            "timezone": settings.runtime.timezone,
            "last_run_at": get_meta("digest_last_run_at", "", settings.sqlite_path),
            "last_digests_sent": last_sent,
        },
        "allowlist": list_sender_allowlist(
            user=user_filter.strip() if user_filter.strip() else None,
            db_path=settings.sqlite_path,
        ),
    }


def admin_release_quarantine_item(
    *,
    user: str,
    uid: str,
    message_id: str,
    allow_sender: bool = False,
) -> dict[str, Any]:
    """Admin Cockpit release of a quarantined email from Junk to INBOX."""
    settings = get_settings()
    sender_to_allow = ""
    if allow_sender:
        try:
            items = fetch_user_junk_items(user, user, settings.mail)
            for it in items:
                if (uid and it["uid"] == uid) or (message_id and it["message_id"] == message_id):
                    sender_to_allow = parseaddr(it["sender"])[1].strip().lower() or it["sender"].strip().lower()
                    break
        except Exception:
            pass

    delivered = move_junk_to_inbox(user=user, uid=uid, message_id=message_id, mail_cfg=settings.mail)
    clear_quarantine_cache()
    allowlisted = False
    if allow_sender and sender_to_allow and "@" in sender_to_allow:
        add_sender_allowlist(user, sender_to_allow, settings.sqlite_path)
        allowlisted = True

    add_audit_entry(
        actor="cockpit-admin",
        source="cockpit",
        target_user=user,
        action="quarantine_released",
        details=f"Released UID={uid} (delivered={delivered}, allowlisted={allowlisted})",
        db_path=settings.sqlite_path,
    )
    return {"ok": True, "delivered": delivered, "allowlisted": allowlisted}


def inspect_quarantine_token(token: str) -> dict[str, Any]:
    """Read-only inspection of a public quarantine token (NEVER marks token used!)."""
    settings = get_settings()
    row = get_quarantine_token_row((token or "").strip(), settings.sqlite_path)
    if row is None:
        return {
            "valid": False,
            "already_used": False,
            "expired": False,
            "recipient_user": "",
            "recipient_email": "",
            "sender": "",
            "subject": "",
            "received_at": "",
            "spam_score": None,
            "recommended_op": "release",
        }

    already_used = bool(row.get("used_at"))
    expired = False
    exp_str = str(row.get("expires_at") or "")
    if exp_str:
        try:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            expired = datetime.now(timezone.utc) > exp_dt
        except ValueError:
            expired = False

    rec_user = str(row.get("recipient_user") or "")
    if not rec_user and "@" in str(row.get("recipient") or ""):
        rec_user = str(row["recipient"]).split("@", 1)[0]

    return {
        "valid": bool(not already_used and not expired),
        "already_used": already_used,
        "expired": expired,
        "recipient_user": rec_user,
        "recipient_email": str(row.get("recipient") or ""),
        "sender": str(row.get("sender") or ""),
        "subject": str(row.get("subject") or ""),
        "received_at": str(row.get("received_at") or ""),
        "spam_score": float(row["spam_score"]) if row.get("spam_score") is not None else None,
        "recommended_op": "release",
    }


def execute_quarantine_token_action(token: str, op: str) -> tuple[int, dict[str, Any]]:
    """Consume quarantine token on explicit POST action, move mail to INBOX, and optionally allowlist."""
    norm_op = (op or "release").strip().lower()
    if norm_op not in ("release", "release_and_allow"):
        return 422, {"ok": False, "detail": f"Unsupported operation: {op!r}"}

    settings = get_settings()
    clean_tok = (token or "").strip()
    row = get_quarantine_token_row(clean_tok, settings.sqlite_path)
    if row is None:
        return 404, {
            "ok": False,
            "already_used": False,
            "expired": False,
            "detail": "Invalid quarantine token",
            "message": "Invalid quarantine token",
        }

    if row.get("used_at"):
        return 409, {
            "ok": False,
            "already_used": True,
            "expired": False,
            "detail": "Quarantine token already_used",
            "message": "This message has already been released.",
        }

    exp_str = str(row.get("expires_at") or "")
    if exp_str:
        try:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                return 410, {
                    "ok": False,
                    "already_used": False,
                    "expired": True,
                    "detail": "Quarantine token expired",
                    "message": "This release link has expired.",
                }
        except ValueError:
            pass

    rec_user = str(row.get("recipient_user") or "")
    if not rec_user and "@" in str(row.get("recipient") or ""):
        rec_user = str(row["recipient"]).split("@", 1)[0]

    delivered = move_junk_to_inbox(
        user=rec_user,
        uid=str(row.get("uid") or ""),
        message_id=str(row.get("mail_id") or ""),
        mail_cfg=settings.mail,
    )

    allowlisted = False
    sender_raw = str(row.get("sender") or "")
    sender_email = parseaddr(sender_raw)[1].strip().lower() or sender_raw.strip().lower()
    if norm_op == "release_and_allow" and rec_user and "@" in sender_email:
        add_sender_allowlist(rec_user, sender_email, settings.sqlite_path)
        allowlisted = True

    marked = mark_quarantine_token_used(clean_tok, action=norm_op, db_path=settings.sqlite_path)
    if not marked:
        return 409, {
            "ok": False,
            "already_used": True,
            "expired": False,
            "detail": "Quarantine token already_used",
            "message": "This message has already been released.",
        }

    clear_quarantine_cache()
    add_audit_entry(
        actor=rec_user or "recipient",
        source="quarantine_portal",
        target_user=rec_user,
        action=f"quarantine_{norm_op}",
        details=f"Released {row.get('mail_id')!r} from {sender_email} (delivered={delivered}, allowlisted={allowlisted})",
        db_path=settings.sqlite_path,
    )

    return 200, {
        "ok": True,
        "op": norm_op,
        "delivered": True if delivered else False,
        "allowlisted": allowlisted,
        "already_used": False,
        "message": (
            "Message delivered to INBOX and sender added to allowlist."
            if allowlisted
            else "Message delivered to INBOX."
        ),
    }


def remove_allowlist_entry(entry_id: int) -> bool:
    settings = get_settings()
    return delete_sender_allowlist_entry(entry_id, settings.sqlite_path)
