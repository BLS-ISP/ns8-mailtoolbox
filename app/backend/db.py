"""SQLite WAL persistence layer for NS8 Mail Toolbox (Phases 1, 2, and 3).

Manages:
- Phase 1: vacation_rules, templates, audit_log
- Phase 2: signature_templates, signature_assignments (with dynamic rule columns)
- Phase 3a: quarantine_tokens, quarantine_digested, user_sender_allowlist
- Phase 3b: mail_history_cache
- Phase 3c: mail_connectors, connector_seen_uids
- Metadata: app_meta
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
import uuid

from app.backend.config import get_settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS vacation_rules (
    user TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    forward_enabled INTEGER NOT NULL DEFAULT 0,
    forward_address TEXT NOT NULL DEFAULT '',
    keep_copy INTEGER NOT NULL DEFAULT 1,
    previous_active_script TEXT NOT NULL DEFAULT '',
    sieve_installed INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'de',
    is_default INTEGER NOT NULL DEFAULT 0,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    target_user TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_target_user ON audit_log(target_user);

-- Phase 2: Central corporate email signatures
CREATE TABLE IF NOT EXISTS signature_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    html_body TEXT NOT NULL DEFAULT '',
    text_body TEXT NOT NULL DEFAULT '',
    auto_plain_from_html INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS signature_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    scope_type TEXT NOT NULL DEFAULT 'default',
    scope_value TEXT NOT NULL DEFAULT '*',
    template_id TEXT NOT NULL REFERENCES signature_templates(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 10,
    enabled INTEGER NOT NULL DEFAULT 0,
    html_template TEXT NOT NULL DEFAULT '',
    text_template TEXT NOT NULL DEFAULT '',
    auto_plain_from_html INTEGER NOT NULL DEFAULT 1,
    reply_mode TEXT NOT NULL DEFAULT 'append_once',
    placement TEXT NOT NULL DEFAULT 'below_reply',
    updated_at TEXT NOT NULL DEFAULT ''
);

-- Phase 3a: Rspamd quarantine digest, scanner-safe release tokens & sender allowlist
CREATE TABLE IF NOT EXISTS quarantine_tokens (
    token TEXT PRIMARY KEY,
    mail_id TEXT NOT NULL,
    uid TEXT NOT NULL DEFAULT '',
    recipient_user TEXT NOT NULL DEFAULT '',
    recipient TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT '',
    spam_score REAL,
    action TEXT NOT NULL DEFAULT 'release',
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quarantine_digested (
    user TEXT NOT NULL,
    message_key TEXT NOT NULL,
    digested_at TEXT NOT NULL,
    PRIMARY KEY (user, message_key)
);

CREATE TABLE IF NOT EXISTS user_sender_allowlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user, sender_email)
);

-- Phase 3b: Persistent Rspamd mail-tracking history cache
CREATE TABLE IF NOT EXISTS mail_history_cache (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    unix_time REAL NOT NULL DEFAULT 0.0,
    sender TEXT NOT NULL DEFAULT '',
    recipients_json TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'delivered',
    rspamd_action TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0.0,
    required_score REAL NOT NULL DEFAULT 0.0,
    symbols_json TEXT NOT NULL DEFAULT '[]',
    ip TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mail_history_unix_time ON mail_history_cache(unix_time DESC);
CREATE INDEX IF NOT EXISTS idx_mail_history_status ON mail_history_cache(status);

-- Phase 3c: External POP3/IMAP connectors & UID deduplication
CREATE TABLE IF NOT EXISTS mail_connectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target_user TEXT NOT NULL DEFAULT '',
    target_email TEXT NOT NULL,
    protocol TEXT NOT NULL DEFAULT 'imaps',
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 993,
    use_ssl INTEGER NOT NULL DEFAULT 1,
    remote_user TEXT NOT NULL,
    encrypted_password TEXT NOT NULL DEFAULT '',
    interval_minutes INTEGER NOT NULL DEFAULT 5,
    leave_on_server INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT 'idle',
    last_error TEXT NOT NULL DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    messages_fetched_total INTEGER NOT NULL DEFAULT 0,
    alert_sent_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS connector_seen_uids (
    connector_id INTEGER NOT NULL REFERENCES mail_connectors(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (connector_id, uid)
);

CREATE TABLE IF NOT EXISTS imapsync_alert_state (
    task_key TEXT PRIMARY KEY,
    last_exit_code INTEGER,
    last_timestamp INTEGER,
    last_alert_sent_at TEXT NOT NULL DEFAULT ''
);
"""

DEFAULT_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "default_en",
        "name": "Default Out-of-Office Notice (EN)",
        "lang": "en",
        "is_default": True,
        "subject": "Out of Office: {{displayName}}",
        "body": (
            "Hello,\n\n"
            "Thank you for your message. I am currently out of the office"
            "{{#endDate}} (returning after {{endDate}}){{/endDate}}"
            " with limited access to my email.\n\n"
            "{{#deputyEmail}}For urgent matters, please contact "
            "{{deputyEmail}}.\n\n{{/deputyEmail}}"
            "Best regards,\n"
            "{{displayName}}"
        ),
        "sort_order": 0,
    },
    {
        "id": "default_de",
        "name": "Standard Abwesenheitsnotiz (DE)",
        "lang": "de",
        "is_default": False,
        "subject": "Abwesenheitsnotiz: {{displayName}}",
        "body": (
            "Guten Tag,\n\n"
            "vielen Dank für Ihre Nachricht. Ich bin derzeit nicht im Haus"
            "{{#endDate}} (voraussichtlich bis einschließlich {{endDate}}){{/endDate}}"
            " und habe keinen Zugriff auf meine E-Mails.\n\n"
            "{{#deputyEmail}}In dringenden Fällen wenden Sie sich bitte an meine "
            "Vertretung unter {{deputyEmail}}.\n\n{{/deputyEmail}}"
            "Mit freundlichen Grüßen\n"
            "{{displayName}}"
        ),
        "sort_order": 1,
    },
]

SEED_SIGNATURE_HTML = (
    '<div style="font-family: Arial, sans-serif; font-size: 13px; color: #262626; '
    'border-top: 1px solid #d0d7de; padding-top: 10px; margin-top: 14px;">\n'
    "  <p style=\"margin: 0 0 4px 0;\"><strong>{{displayName}}</strong><br>\n"
    "  {{title}}<br>\n"
    "  {{department}}</p>\n"
    "  <p style=\"margin: 6px 0 0 0; color: #525252;\">\n"
    "    {{company}}<br>\n"
    "    Phone: {{telephoneNumber}}<br>\n"
    "    Mobile: {{mobile}}<br>\n"
    '    Email: <a href="mailto:{{mail}}">{{mail}}</a> | Web: <a href="https://example.com">example.com</a>\n'
    "  </p>\n"
    "</div>"
)

SEED_SIGNATURE_TEXT = (
    "{{displayName}}\n"
    "{{title}}\n"
    "{{department}}\n"
    "{{company}}\n"
    "Phone: {{telephoneNumber}}\n"
    "Mobile: {{mobile}}\n"
    "Email: {{mail}} | Web: https://example.com"
)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_db_path(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return db_path
    return get_settings().sqlite_path


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a configured SQLite WAL connection and guarantee handle closure."""
    target = get_db_path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        yield conn
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_suffix: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")


def init_db(db_path: Path | None = None) -> Path:
    """Ensure SQLite database schema, migrations, and seed templates exist."""
    target = get_db_path(db_path)
    with get_connection(target) as conn:
        with conn:
            conn.executescript(SCHEMA_SQL)

            # Safe forward migrations for upgraded Phase 1 databases
            _ensure_column(conn, "signature_templates", "auto_plain_from_html", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(conn, "signature_assignments", "name", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "signature_assignments", "html_template", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "signature_assignments", "text_template", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "signature_assignments", "auto_plain_from_html", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(conn, "signature_assignments", "reply_mode", "TEXT NOT NULL DEFAULT 'append_once'")
            _ensure_column(conn, "signature_assignments", "placement", "TEXT NOT NULL DEFAULT 'below_reply'")

            _ensure_column(conn, "quarantine_tokens", "uid", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "quarantine_tokens", "recipient_user", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "quarantine_tokens", "received_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "quarantine_tokens", "spam_score", "REAL")

            now = utc_now_iso()
            row = conn.execute("SELECT COUNT(*) AS cnt FROM templates").fetchone()
            if row and int(row["cnt"]) == 0:
                for tpl in DEFAULT_TEMPLATES:
                    conn.execute(
                        """
                        INSERT INTO templates (
                            id, name, lang, is_default, subject, body, sort_order, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tpl["id"],
                            tpl["name"],
                            tpl["lang"],
                            1 if tpl["is_default"] else 0,
                            tpl["subject"],
                            tpl["body"],
                            tpl["sort_order"],
                            now,
                        ),
                    )

            sig_seeded = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'signatures_seeded'"
            ).fetchone()
            if sig_seeded is None:
                sig_cnt = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM signature_assignments"
                ).fetchone()
                if sig_cnt and int(sig_cnt["cnt"]) == 0:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO signature_templates (
                            id, name, html_body, text_body, auto_plain_from_html,
                            is_default, updated_by, updated_at
                        ) VALUES (?, ?, ?, ?, 1, 1, 'system', ?)
                        """,
                        (
                            "sig_example_default",
                            "Standard Company Signature",
                            SEED_SIGNATURE_HTML,
                            SEED_SIGNATURE_TEXT,
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO signature_assignments (
                            name, scope_type, scope_value, template_id, priority, enabled,
                            html_template, text_template, auto_plain_from_html,
                            reply_mode, placement, updated_at
                        ) VALUES (?, 'default', '*', 'sig_example_default', 10, 0, ?, ?, 1, 'append_once', 'below_reply', ?)
                        """,
                        (
                            "Standard Company Signature",
                            SEED_SIGNATURE_HTML,
                            SEED_SIGNATURE_TEXT,
                            now,
                        ),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('signatures_seeded', '1')"
                )
    return target


def get_meta(key: str, default: str = "", db_path: Path | None = None) -> str:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else default


def set_meta(key: str, value: str, db_path: Path | None = None) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                (key, str(value)),
            )


# ---------------------------------------------------------------------------
# Phase 1: Vacation Rules, Templates & Audit Log
# ---------------------------------------------------------------------------


def get_all_vacation_rules(db_path: Path | None = None) -> dict[str, dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM vacation_rules").fetchall()
        return {row["user"]: _row_to_vacation_rule(row) for row in rows}


def get_enabled_vacation_rules(db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM vacation_rules WHERE enabled = 1 ORDER BY user ASC"
        ).fetchall()
        return [_row_to_vacation_rule(row) for row in rows]


def get_vacation_rule(user: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM vacation_rules WHERE user = ?", (user,)).fetchone()
        return _row_to_vacation_rule(row) if row else None


def upsert_vacation_rule(
    *,
    user: str,
    enabled: bool,
    start_date: str,
    end_date: str,
    subject: str,
    body: str,
    forward_enabled: bool,
    forward_address: str,
    keep_copy: bool,
    previous_active_script: str,
    sieve_installed: bool,
    updated_by: str,
    updated_at: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    ts = updated_at or utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO vacation_rules (
                    user, enabled, start_date, end_date, subject, body,
                    forward_enabled, forward_address, keep_copy,
                    previous_active_script, sieve_installed, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user) DO UPDATE SET
                    enabled = excluded.enabled,
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    subject = excluded.subject,
                    body = excluded.body,
                    forward_enabled = excluded.forward_enabled,
                    forward_address = excluded.forward_address,
                    keep_copy = excluded.keep_copy,
                    previous_active_script = excluded.previous_active_script,
                    sieve_installed = excluded.sieve_installed,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    user,
                    1 if enabled else 0,
                    start_date or "",
                    end_date or "",
                    subject or "",
                    body or "",
                    1 if forward_enabled else 0,
                    forward_address or "",
                    1 if keep_copy else 0,
                    previous_active_script or "",
                    1 if sieve_installed else 0,
                    updated_by or "",
                    ts,
                ),
            )
        row = conn.execute("SELECT * FROM vacation_rules WHERE user = ?", (user,)).fetchone()
        return _row_to_vacation_rule(row)


def list_templates(db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, lang, is_default, subject, body FROM templates ORDER BY sort_order ASC, id ASC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "lang": row["lang"],
                "is_default": bool(row["is_default"]),
                "subject": row["subject"],
                "body": row["body"],
            }
            for row in rows
        ]


def save_templates(templates: list[dict[str, Any]], db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            conn.execute("DELETE FROM templates")
            for idx, item in enumerate(templates):
                tpl_id = (str(item.get("id") or "")).strip() or f"tpl_{uuid.uuid4().hex[:8]}"
                name = (str(item.get("name") or "")).strip() or tpl_id
                lang = (str(item.get("lang") or "de")).strip().lower() or "de"
                is_default = 1 if bool(item.get("is_default", False)) else 0
                subject = str(item.get("subject") or "")
                body = str(item.get("body") or "")
                conn.execute(
                    """
                    INSERT INTO templates (
                        id, name, lang, is_default, subject, body, sort_order, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tpl_id, name, lang, is_default, subject, body, idx, now),
                )
    return list_templates(db_path)


def add_audit_entry(
    *,
    actor: str,
    source: str,
    target_user: str,
    action: str,
    details: str,
    timestamp: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    ts = timestamp or utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO audit_log (timestamp, actor, source, target_user, action, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, actor, source, target_user, action, details),
            )
            entry_id = int(cur.lastrowid)
        return {
            "id": entry_id,
            "timestamp": ts,
            "actor": actor,
            "source": source,
            "target_user": target_user,
            "action": action,
            "details": details,
        }


def list_audit_entries(
    limit: int = 200,
    target_user: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_db(db_path)
    clamped = max(1, min(int(limit), 1000))
    with get_connection(db_path) as conn:
        if target_user:
            rows = conn.execute(
                """
                SELECT id, timestamp, actor, source, target_user, action, details
                FROM audit_log
                WHERE target_user = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (target_user, clamped),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, timestamp, actor, source, target_user, action, details
                FROM audit_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (clamped,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "timestamp": row["timestamp"],
                "actor": row["actor"],
                "source": row["source"],
                "target_user": row["target_user"],
                "action": row["action"],
                "details": row["details"],
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Phase 2: Signature Rules (signature_templates + signature_assignments)
# ---------------------------------------------------------------------------


def _row_to_signature_rule(row: sqlite3.Row) -> dict[str, Any]:
    html_tpl = row["html_template"] or row["tpl_html"] or ""
    text_tpl = row["text_template"] or row["tpl_text"] or ""
    name = row["name"] or row["tpl_name"] or f"Rule #{row['id']}"
    return {
        "id": int(row["id"]),
        "name": str(name),
        "scope_type": str(row["scope_type"] or "default"),
        "scope_value": str(row["scope_value"] or "*"),
        "priority": int(row["priority"]),
        "enabled": bool(row["enabled"]),
        "html_template": str(html_tpl),
        "text_template": str(text_tpl),
        "auto_plain_from_html": bool(row["auto_plain_from_html"]),
        "reply_mode": str(row["reply_mode"] or "append_once"),
        "placement": str(row["placement"] or "below_reply"),
        "updated_at": str(row["updated_at"] or ""),
    }


def list_signature_rules(db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.*,
                   t.name AS tpl_name,
                   t.html_body AS tpl_html,
                   t.text_body AS tpl_text
            FROM signature_assignments a
            LEFT JOIN signature_templates t ON t.id = a.template_id
            ORDER BY
                CASE a.scope_type
                    WHEN 'group' THEN 0
                    WHEN 'domain' THEN 1
                    ELSE 2
                END ASC,
                a.priority ASC,
                a.id ASC
            """
        ).fetchall()
        return [_row_to_signature_rule(r) for r in rows]


def save_signature_rule(
    *,
    rule_id: int | None,
    name: str,
    scope_type: str,
    scope_value: str,
    priority: int,
    enabled: bool,
    html_template: str,
    text_template: str,
    auto_plain_from_html: bool,
    reply_mode: str,
    placement: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    now = utc_now_iso()
    norm_scope = scope_type.strip().lower()
    if norm_scope not in ("group", "domain", "default"):
        norm_scope = "default"
    norm_val = "*" if norm_scope == "default" else (scope_value or "").strip()
    norm_reply = reply_mode.strip().lower()
    if norm_reply not in ("append_once", "always", "first_only"):
        norm_reply = "append_once"
    norm_place = placement.strip().lower()
    if norm_place not in ("below_reply", "bottom"):
        norm_place = "below_reply"

    with get_connection(db_path) as conn:
        with conn:
            if rule_id is not None:
                existing = conn.execute(
                    "SELECT template_id FROM signature_assignments WHERE id = ?",
                    (int(rule_id),),
                ).fetchone()
            else:
                existing = None

            tpl_id = str(existing["template_id"]) if existing else f"sig_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO signature_templates (
                    id, name, html_body, text_body, auto_plain_from_html,
                    is_default, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'cockpit', ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    html_body = excluded.html_body,
                    text_body = excluded.text_body,
                    auto_plain_from_html = excluded.auto_plain_from_html,
                    is_default = excluded.is_default,
                    updated_at = excluded.updated_at
                """,
                (
                    tpl_id,
                    name,
                    html_template,
                    text_template,
                    1 if auto_plain_from_html else 0,
                    1 if norm_scope == "default" else 0,
                    now,
                ),
            )

            if existing is not None and rule_id is not None:
                conn.execute(
                    """
                    UPDATE signature_assignments
                    SET name = ?, scope_type = ?, scope_value = ?, priority = ?,
                        enabled = ?, html_template = ?, text_template = ?,
                        auto_plain_from_html = ?, reply_mode = ?, placement = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        norm_scope,
                        norm_val,
                        int(priority),
                        1 if enabled else 0,
                        html_template,
                        text_template,
                        1 if auto_plain_from_html else 0,
                        norm_reply,
                        norm_place,
                        now,
                        int(rule_id),
                    ),
                )
                target_id = int(rule_id)
            else:
                cur = conn.execute(
                    """
                    INSERT INTO signature_assignments (
                        name, scope_type, scope_value, template_id, priority, enabled,
                        html_template, text_template, auto_plain_from_html,
                        reply_mode, placement, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        norm_scope,
                        norm_val,
                        tpl_id,
                        int(priority),
                        1 if enabled else 0,
                        html_template,
                        text_template,
                        1 if auto_plain_from_html else 0,
                        norm_reply,
                        norm_place,
                        now,
                    ),
                )
                target_id = int(cur.lastrowid)

        row = conn.execute(
            """
            SELECT a.*, t.name AS tpl_name, t.html_body AS tpl_html, t.text_body AS tpl_text
            FROM signature_assignments a
            LEFT JOIN signature_templates t ON t.id = a.template_id
            WHERE a.id = ?
            """,
            (target_id,),
        ).fetchone()
        return _row_to_signature_rule(row)


def delete_signature_rule(rule_id: int, db_path: Path | None = None) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        with conn:
            row = conn.execute(
                "SELECT template_id FROM signature_assignments WHERE id = ?",
                (int(rule_id),),
            ).fetchone()
            cur = conn.execute("DELETE FROM signature_assignments WHERE id = ?", (int(rule_id),))
            if row:
                conn.execute(
                    "DELETE FROM signature_templates WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM signature_assignments WHERE template_id = ?)",
                    (row["template_id"], row["template_id"]),
                )
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Phase 3a: Quarantine Tokens, Digested Tracking & Sender Allowlist
# ---------------------------------------------------------------------------


def create_quarantine_token(
    *,
    token: str,
    mail_id: str,
    uid: str,
    recipient_user: str,
    recipient: str,
    sender: str,
    subject: str,
    received_at: str,
    spam_score: float | None,
    expires_at: str,
    db_path: Path | None = None,
) -> None:
    init_db(db_path)
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO quarantine_tokens (
                    token, mail_id, uid, recipient_user, recipient,
                    sender, subject, received_at, spam_score,
                    action, expires_at, used_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'release', ?, NULL, ?)
                """,
                (
                    token,
                    mail_id,
                    uid,
                    recipient_user,
                    recipient,
                    sender,
                    subject,
                    received_at,
                    spam_score,
                    expires_at,
                    now,
                ),
            )


def get_quarantine_token_row(token: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM quarantine_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def mark_quarantine_token_used(
    token: str,
    action: str = "release",
    db_path: Path | None = None,
) -> bool:
    init_db(db_path)
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            cur = conn.execute(
                """
                UPDATE quarantine_tokens
                SET used_at = ?, action = ?
                WHERE token = ? AND used_at IS NULL
                """,
                (now, action, token),
            )
            return cur.rowcount > 0


def get_digested_keys_for_user(user: str, db_path: Path | None = None) -> set[str]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT message_key FROM quarantine_digested WHERE user = ?",
            (user,),
        ).fetchall()
        return {str(r["message_key"]) for r in rows}


def mark_messages_digested(
    user: str,
    message_keys: list[str],
    db_path: Path | None = None,
) -> None:
    if not message_keys:
        return
    init_db(db_path)
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO quarantine_digested (user, message_key, digested_at)
                VALUES (?, ?, ?)
                """,
                [(user, k, now) for k in message_keys],
            )


def add_sender_allowlist(
    user: str,
    sender_email: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    norm_sender = sender_email.strip().lower()
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_sender_allowlist (user, sender_email, created_at)
                VALUES (?, ?, ?)
                """,
                (user, norm_sender, now),
            )
        row = conn.execute(
            "SELECT id, user, sender_email, created_at FROM user_sender_allowlist WHERE user = ? AND sender_email = ?",
            (user, norm_sender),
        ).fetchone()
        return {
            "id": int(row["id"]),
            "user": str(row["user"]),
            "sender_email": str(row["sender_email"]),
            "created_at": str(row["created_at"]),
        }


def list_sender_allowlist(
    user: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        if user:
            rows = conn.execute(
                "SELECT id, user, sender_email, created_at FROM user_sender_allowlist WHERE user = ? ORDER BY id DESC",
                (user,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, user, sender_email, created_at FROM user_sender_allowlist ORDER BY id DESC"
            ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "user": str(r["user"]),
                "sender_email": str(r["sender_email"]),
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]


def delete_sender_allowlist_entry(entry_id: int, db_path: Path | None = None) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        with conn:
            cur = conn.execute(
                "DELETE FROM user_sender_allowlist WHERE id = ?",
                (int(entry_id),),
            )
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Phase 3b: Mail History Cache
# ---------------------------------------------------------------------------


def upsert_mail_history_rows(rows: list[dict[str, Any]], db_path: Path | None = None) -> None:
    if not rows:
        return
    init_db(db_path)
    with get_connection(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO mail_history_cache (
                    id, timestamp, unix_time, sender, recipients_json,
                    subject, status, rspamd_action, score, required_score,
                    symbols_json, ip, message_id, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["id"],
                        r["timestamp"],
                        float(r["unix_time"]),
                        r["sender"],
                        json.dumps(r["recipients"]),
                        r["subject"],
                        r["status"],
                        r["rspamd_action"],
                        float(r["score"]),
                        float(r["required_score"]),
                        json.dumps(r["symbols"]),
                        r["ip"],
                        r["message_id"],
                        int(r["size_bytes"]),
                    )
                    for r in rows
                ],
            )


def query_mail_history_cache(
    *,
    sender: str = "",
    recipient: str = "",
    subject: str = "",
    action_filter: str = "all",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
    db_path: Path | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    init_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []

    if sender.strip():
        clauses.append("LOWER(sender) LIKE ?")
        params.append(f"%{sender.strip().lower()}%")
    if recipient.strip():
        clauses.append("LOWER(recipients_json) LIKE ?")
        params.append(f"%{recipient.strip().lower()}%")
    if subject.strip():
        clauses.append("LOWER(subject) LIKE ?")
        params.append(f"%{subject.strip().lower()}%")
    if action_filter and action_filter.strip().lower() not in ("all", ""):
        clauses.append("status = ?")
        params.append(action_filter.strip().lower())
    if date_from.strip():
        df = date_from.strip()
        clauses.append("timestamp >= ?")
        params.append(df)
    if date_to.strip():
        dt = date_to.strip()
        if len(dt) == 10:
            dt = f"{dt}T23:59:59Z"
        clauses.append("timestamp <= ?")
        params.append(dt)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    clamped = max(1, min(int(limit), 1000))

    with get_connection(db_path) as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM mail_history_cache {where_sql}",
            params,
        ).fetchone()
        total = int(total_row["cnt"]) if total_row else 0

        rows = conn.execute(
            f"""
            SELECT * FROM mail_history_cache
            {where_sql}
            ORDER BY unix_time DESC, timestamp DESC
            LIMIT ?
            """,
            [*params, clamped],
        ).fetchall()

        entries: list[dict[str, Any]] = []
        for r in rows:
            try:
                recipients = json.loads(r["recipients_json"] or "[]")
            except json.JSONDecodeError:
                recipients = []
            try:
                symbols = json.loads(r["symbols_json"] or "[]")
            except json.JSONDecodeError:
                symbols = []
            entries.append(
                {
                    "id": str(r["id"]),
                    "timestamp": str(r["timestamp"]),
                    "sender": str(r["sender"]),
                    "recipients": [str(x) for x in recipients],
                    "subject": str(r["subject"]),
                    "status": str(r["status"]),
                    "rspamd_action": str(r["rspamd_action"]),
                    "score": float(r["score"]),
                    "required_score": float(r["required_score"]),
                    "symbols": [str(s) for s in symbols],
                    "ip": str(r["ip"]),
                    "message_id": str(r["message_id"]),
                    "size_bytes": int(r["size_bytes"]),
                }
            )
        return total, entries


# ---------------------------------------------------------------------------
# Phase 3c: External POP3/IMAP Connectors
# ---------------------------------------------------------------------------


def _row_to_connector(row: sqlite3.Row, *, include_secret: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "target_user": str(row["target_user"] or ""),
        "target_email": str(row["target_email"]),
        "protocol": str(row["protocol"]),
        "host": str(row["host"]),
        "port": int(row["port"]),
        "use_ssl": bool(row["use_ssl"]),
        "remote_user": str(row["remote_user"]),
        "password_set": bool(row["encrypted_password"]),
        "interval_minutes": int(row["interval_minutes"]),
        "leave_on_server": bool(row["leave_on_server"]),
        "enabled": bool(row["enabled"]),
        "last_sync_at": str(row["last_sync_at"] or ""),
        "last_status": str(row["last_status"] or "idle"),
        "last_error": str(row["last_error"] or ""),
        "consecutive_failures": int(row["consecutive_failures"] or 0),
        "messages_fetched_total": int(row["messages_fetched_total"] or 0),
    }
    if include_secret:
        out["encrypted_password"] = str(row["encrypted_password"] or "")
        out["alert_sent_at"] = str(row["alert_sent_at"] or "")
    return out


def list_connectors_db(
    *,
    include_secret: bool = False,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM mail_connectors ORDER BY id ASC").fetchall()
        return [_row_to_connector(r, include_secret=include_secret) for r in rows]


def get_connector_db(
    connector_id: int,
    *,
    include_secret: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM mail_connectors WHERE id = ?",
            (int(connector_id),),
        ).fetchone()
        return _row_to_connector(row, include_secret=include_secret) if row else None


def save_connector_db(
    *,
    connector_id: int | None,
    name: str,
    target_user: str,
    target_email: str,
    protocol: str,
    host: str,
    port: int,
    use_ssl: bool,
    remote_user: str,
    encrypted_password: str | None,
    interval_minutes: int,
    leave_on_server: bool,
    enabled: bool,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            if connector_id is not None:
                existing = conn.execute(
                    "SELECT encrypted_password FROM mail_connectors WHERE id = ?",
                    (int(connector_id),),
                ).fetchone()
            else:
                existing = None

            if existing is not None and connector_id is not None:
                final_enc = (
                    encrypted_password
                    if encrypted_password is not None
                    else str(existing["encrypted_password"] or "")
                )
                conn.execute(
                    """
                    UPDATE mail_connectors
                    SET name = ?, target_user = ?, target_email = ?, protocol = ?,
                        host = ?, port = ?, use_ssl = ?, remote_user = ?,
                        encrypted_password = ?, interval_minutes = ?,
                        leave_on_server = ?, enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        target_user,
                        target_email,
                        protocol,
                        host,
                        int(port),
                        1 if use_ssl else 0,
                        remote_user,
                        final_enc,
                        max(1, int(interval_minutes)),
                        1 if leave_on_server else 0,
                        1 if enabled else 0,
                        now,
                        int(connector_id),
                    ),
                )
                cid = int(connector_id)
            else:
                cur = conn.execute(
                    """
                    INSERT INTO mail_connectors (
                        name, target_user, target_email, protocol, host, port,
                        use_ssl, remote_user, encrypted_password, interval_minutes,
                        leave_on_server, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        target_user,
                        target_email,
                        protocol,
                        host,
                        int(port),
                        1 if use_ssl else 0,
                        remote_user,
                        encrypted_password or "",
                        max(1, int(interval_minutes)),
                        1 if leave_on_server else 0,
                        1 if enabled else 0,
                        now,
                    ),
                )
                cid = int(cur.lastrowid)

        row = conn.execute("SELECT * FROM mail_connectors WHERE id = ?", (cid,)).fetchone()
        return _row_to_connector(row, include_secret=False)


def update_connector_sync_result(
    *,
    connector_id: int,
    ok: bool,
    fetched: int = 0,
    error: str = "",
    alert_sent_at: str | None = None,
    db_path: Path | None = None,
) -> None:
    init_db(db_path)
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            if ok:
                conn.execute(
                    """
                    UPDATE mail_connectors
                    SET last_sync_at = ?, last_status = 'ok', last_error = '',
                        consecutive_failures = 0, alert_sent_at = '',
                        messages_fetched_total = messages_fetched_total + ?
                    WHERE id = ?
                    """,
                    (now, int(fetched), int(connector_id)),
                )
            else:
                if alert_sent_at is not None:
                    conn.execute(
                        """
                        UPDATE mail_connectors
                        SET last_sync_at = ?, last_status = 'error', last_error = ?,
                            consecutive_failures = consecutive_failures + 1,
                            alert_sent_at = ?
                        WHERE id = ?
                        """,
                        (now, error, alert_sent_at, int(connector_id)),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE mail_connectors
                        SET last_sync_at = ?, last_status = 'error', last_error = ?,
                            consecutive_failures = consecutive_failures + 1
                        WHERE id = ?
                        """,
                        (now, error, int(connector_id)),
                    )


def delete_connector_db(connector_id: int, db_path: Path | None = None) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        with conn:
            cur = conn.execute("DELETE FROM mail_connectors WHERE id = ?", (int(connector_id),))
            return cur.rowcount > 0


def get_seen_connector_uids(connector_id: int, db_path: Path | None = None) -> set[str]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT uid FROM connector_seen_uids WHERE connector_id = ?",
            (int(connector_id),),
        ).fetchall()
        return {str(r["uid"]) for r in rows}


def mark_connector_uids_seen(
    connector_id: int,
    uids: list[str],
    db_path: Path | None = None,
) -> None:
    if not uids:
        return
    init_db(db_path)
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO connector_seen_uids (connector_id, uid, fetched_at)
                VALUES (?, ?, ?)
                """,
                [(int(connector_id), u, now) for u in uids],
            )


def get_imapsync_alert_state(
    task_key: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM imapsync_alert_state WHERE task_key = ?",
            (task_key,),
        ).fetchone()
        return dict(row) if row is not None else None


def upsert_imapsync_alert_state(
    *,
    task_key: str,
    last_exit_code: int | None,
    last_timestamp: int | None,
    last_alert_sent_at: str,
    db_path: Path | None = None,
) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO imapsync_alert_state (
                    task_key, last_exit_code, last_timestamp, last_alert_sent_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    last_exit_code = excluded.last_exit_code,
                    last_timestamp = excluded.last_timestamp,
                    last_alert_sent_at = excluded.last_alert_sent_at
                """,
                (task_key, last_exit_code, last_timestamp, last_alert_sent_at or ""),
            )


def clear_imapsync_alert_state(
    task_key: str,
    db_path: Path | None = None,
) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        with conn:
            conn.execute("DELETE FROM imapsync_alert_state WHERE task_key = ?", (task_key,))


def _row_to_vacation_rule(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "user": row["user"],
        "enabled": bool(row["enabled"]),
        "start_date": row["start_date"] or "",
        "end_date": row["end_date"] or "",
        "subject": row["subject"] or "",
        "body": row["body"] or "",
        "forward_enabled": bool(row["forward_enabled"]),
        "forward_address": row["forward_address"] or "",
        "keep_copy": bool(row["keep_copy"]),
        "previous_active_script": row["previous_active_script"] or "",
        "sieve_installed": bool(row["sieve_installed"]),
        "updated_by": row["updated_by"] or "",
        "updated_at": row["updated_at"] or "",
    }
