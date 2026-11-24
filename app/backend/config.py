"""Dynamic configuration loader for NS8 Mail Toolbox.

Reads environment variables and runtime discovery/settings files from CONFIG_DIR and DATA_DIR
without caching stale values across tests or NS8 hot-reload events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import secrets
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# Fallback ephemeral key used only when APP_SECRET_KEY is not set in environment.
_EPHEMERAL_SECRET_KEY = secrets.token_hex(32)


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a shell/systemd-compatible KEY=VALUE environment file."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed reading env file %s: %s", path, exc)
        return result

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def parse_allowed_groups(raw: str) -> list[str]:
    """Parse ALLOWED_GROUPS from either a JSON array or comma-separated string."""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


def _parse_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_int(raw: Any, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def translate_loopback_host(host: str, gateway: str | None = None) -> str:
    """Translate 127.0.0.1/localhost to CONTAINER_HOST_GATEWAY (e.g. 10.0.2.2) when set."""
    cleaned = (host or "").strip()
    gw = (gateway if gateway is not None else os.environ.get("CONTAINER_HOST_GATEWAY", "")).strip()
    if gw and cleaned.lower() in ("127.0.0.1", "localhost", "::1"):
        return gw
    return cleaned


def translate_loopback_url(url: str, gateway: str | None = None) -> str:
    """Translate 127.0.0.1/localhost URL host to CONTAINER_HOST_GATEWAY when set."""
    cleaned = (url or "").strip()
    gw = (gateway if gateway is not None else os.environ.get("CONTAINER_HOST_GATEWAY", "")).strip()
    if not gw or not cleaned:
        return cleaned
    try:
        parts = urlsplit(cleaned)
        hostname = (parts.hostname or "").lower()
        if hostname not in ("127.0.0.1", "localhost", "::1"):
            return cleaned
        userinfo = ""
        if parts.username:
            userinfo = parts.username
            if parts.password is not None:
                userinfo += f":{parts.password}"
            userinfo += "@"
        port_suffix = f":{parts.port}" if parts.port is not None else ""
        new_netloc = f"{userinfo}{gw}{port_suffix}"
        return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return cleaned


@dataclass
class MailSettings:
    host: str = ""
    imap_port: int = 143
    sieve_port: int = 4190
    submission_port: int = 587
    module_id: str = ""
    master_user: str = "vmail"
    master_password: str = ""
    spam_folder: str = "Junk"
    sieve_starttls: bool = False
    sieve_tls_verify: bool = False
    sieve_timeout: float = 10.0


@dataclass
class LdapSettings:
    host: str = ""
    port: int = 389
    base_dn: str = ""
    bind_dn: str = ""
    bind_password: str = ""
    schema: str = "rfc2307"
    domain: str = ""
    use_tls: bool = False
    timeout: float = 10.0


@dataclass
class ToolboxRuntimeSettings:
    milter_enabled: bool = True
    milter_listen_port: int = 8090
    milter_host_port: int = 8090
    rspamd_url: str = "http://127.0.0.1:11334"
    rspamd_user: str = ""
    rspamd_password: str = ""
    rspamd_tls_verify: bool = False
    digest_enabled: bool = False
    digest_time: str = "07:30"
    timezone: str = "Europe/Berlin"
    admin_alert_email: str = ""
    portal_tracking_enabled: bool = False

    @property
    def effective_rspamd_url(self) -> str:
        return translate_loopback_url(self.rspamd_url)


@dataclass
class AppSettings:
    data_dir: Path
    config_dir: Path
    static_dir: Path | None
    secret_key: str
    internal_token: str
    allowed_groups: list[str] = field(default_factory=list)
    portal_enabled: bool = True
    portal_hostname: str = ""
    session_ttl_seconds: int = 43200
    reconcile_interval_seconds: int = 300
    login_rate_limit_max: int = 10
    login_rate_limit_window_seconds: int = 60
    mail: MailSettings = field(default_factory=MailSettings)
    ldap: LdapSettings = field(default_factory=LdapSettings)
    runtime: ToolboxRuntimeSettings = field(default_factory=ToolboxRuntimeSettings)

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "toolbox.sqlite"

    @property
    def settings_json_path(self) -> Path:
        return self.config_dir / "toolbox_settings.json"

    @property
    def imapsync_cache_path(self) -> Path:
        env_override = os.environ.get("IMAPSYNC_CACHE_PATH", "").strip()
        if env_override:
            return Path(env_override)
        return self.config_dir / "imapsync_cache.json"


def _resolve_static_dir() -> Path | None:
    env_static = os.environ.get("STATIC_DIR", "").strip()
    if env_static:
        candidate = Path(env_static)
        if (candidate / "index.html").is_file():
            return candidate
        return None

    container_static = Path("/app/static")
    if (container_static / "index.html").is_file():
        return container_static

    repo_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if (repo_dist / "index.html").is_file():
        return repo_dist

    return None


def _read_settings_json(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "toolbox_settings.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed reading toolbox_settings.json: %s", exc)
        return {}


def load_runtime_settings(config_dir: Path | None = None) -> ToolboxRuntimeSettings:
    """Merge env defaults with persistent /app/config/toolbox_settings.json."""
    cfg_dir = config_dir or Path(os.environ.get("CONFIG_DIR", "/app/config"))
    persisted = _read_settings_json(cfg_dir)

    env_milter = _parse_bool(os.environ.get("MILTER_ENABLED"), True)
    milter_enabled = (
        _parse_bool(persisted["milter_enabled"], env_milter)
        if "milter_enabled" in persisted
        else env_milter
    )

    listen_port = _parse_int(os.environ.get("MILTER_LISTEN_PORT"), 8090)
    host_port = _parse_int(os.environ.get("MILTER_TCP_PORT"), listen_port)

    rspamd_url = str(
        persisted.get("rspamd_url")
        or os.environ.get("RSPAMD_URL")
        or "http://127.0.0.1:11334"
    ).strip()
    rspamd_user = (
        str(persisted["rspamd_user"]).strip()
        if "rspamd_user" in persisted
        else os.environ.get("RSPAMD_USER", "").strip()
    )
    rspamd_password = (
        str(persisted["rspamd_password"])
        if "rspamd_password" in persisted
        else os.environ.get("RSPAMD_PASSWORD", "")
    )
    env_rspamd_tls_verify = _parse_bool(os.environ.get("RSPAMD_TLS_VERIFY"), False)
    rspamd_tls_verify = (
        _parse_bool(persisted["rspamd_tls_verify"], env_rspamd_tls_verify)
        if "rspamd_tls_verify" in persisted
        else env_rspamd_tls_verify
    )

    env_digest = _parse_bool(os.environ.get("DIGEST_ENABLED"), False)
    digest_enabled = (
        _parse_bool(persisted["digest_enabled"], env_digest)
        if "digest_enabled" in persisted
        else env_digest
    )
    digest_time = str(
        persisted.get("digest_time") or os.environ.get("DIGEST_TIME") or "07:30"
    ).strip()
    tz_name = str(
        persisted.get("timezone") or os.environ.get("TIMEZONE") or "Europe/Berlin"
    ).strip()
    admin_alert_email = str(
        persisted.get("admin_alert_email")
        if "admin_alert_email" in persisted
        else os.environ.get("ADMIN_ALERT_EMAIL", "")
    ).strip()

    env_portal_tracking = _parse_bool(os.environ.get("PORTAL_TRACKING_ENABLED"), False)
    portal_tracking_enabled = (
        _parse_bool(persisted["portal_tracking_enabled"], env_portal_tracking)
        if "portal_tracking_enabled" in persisted
        else env_portal_tracking
    )

    return ToolboxRuntimeSettings(
        milter_enabled=milter_enabled,
        milter_listen_port=listen_port,
        milter_host_port=host_port,
        rspamd_url=rspamd_url,
        rspamd_user=rspamd_user,
        rspamd_password=rspamd_password,
        rspamd_tls_verify=rspamd_tls_verify,
        digest_enabled=digest_enabled,
        digest_time=digest_time,
        timezone=tz_name,
        admin_alert_email=admin_alert_email,
        portal_tracking_enabled=portal_tracking_enabled,
    )


def update_runtime_settings(
    updates: dict[str, Any],
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """Persist updated Phase 2/3 runtime settings to toolbox_settings.json atomically."""
    cfg_dir = config_dir or Path(os.environ.get("CONFIG_DIR", "/app/config"))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    current = load_runtime_settings(cfg_dir)

    milter_enabled = (
        bool(updates["milter_enabled"])
        if updates.get("milter_enabled") is not None
        else current.milter_enabled
    )
    rspamd_url = (
        str(updates["rspamd_url"]).strip()
        if updates.get("rspamd_url") is not None and str(updates["rspamd_url"]).strip()
        else current.rspamd_url
    )
    rspamd_user = (
        str(updates["rspamd_user"]).strip()
        if updates.get("rspamd_user") is not None
        else current.rspamd_user
    )

    clear_pw = bool(updates.get("clear_rspamd_password", False))
    incoming_pw = updates.get("rspamd_password")
    if clear_pw:
        rspamd_password = ""
    elif incoming_pw is not None and str(incoming_pw) != "":
        rspamd_password = str(incoming_pw)
    else:
        rspamd_password = current.rspamd_password

    rspamd_tls_verify = (
        bool(updates["rspamd_tls_verify"])
        if updates.get("rspamd_tls_verify") is not None
        else current.rspamd_tls_verify
    )

    digest_enabled = (
        bool(updates["digest_enabled"])
        if updates.get("digest_enabled") is not None
        else current.digest_enabled
    )
    digest_time = (
        str(updates["digest_time"]).strip()
        if updates.get("digest_time") is not None and str(updates["digest_time"]).strip()
        else current.digest_time
    )
    tz_name = (
        str(updates["timezone"]).strip()
        if updates.get("timezone") is not None and str(updates["timezone"]).strip()
        else current.timezone
    )
    admin_alert_email = (
        str(updates["admin_alert_email"]).strip()
        if updates.get("admin_alert_email") is not None
        else current.admin_alert_email
    )
    portal_tracking_enabled = (
        bool(updates["portal_tracking_enabled"])
        if updates.get("portal_tracking_enabled") is not None
        else current.portal_tracking_enabled
    )

    payload = {
        "milter_enabled": milter_enabled,
        "rspamd_url": rspamd_url,
        "rspamd_user": rspamd_user,
        "rspamd_password": rspamd_password,
        "rspamd_tls_verify": rspamd_tls_verify,
        "digest_enabled": digest_enabled,
        "digest_time": digest_time,
        "timezone": tz_name,
        "admin_alert_email": admin_alert_email,
        "portal_tracking_enabled": portal_tracking_enabled,
    }
    target = cfg_dir / "toolbox_settings.json"
    tmp_path = cfg_dir / f"toolbox_settings.json.tmp.{os.getpid()}"
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(str(tmp_path), str(target))
    return format_public_runtime_settings(load_runtime_settings(cfg_dir))


def format_public_runtime_settings(rt: ToolboxRuntimeSettings | None = None) -> dict[str, Any]:
    """Return runtime settings dict safe for API responses (never leaks rspamd_password)."""
    runtime = rt or load_runtime_settings()
    return {
        "milter_enabled": runtime.milter_enabled,
        "rspamd_url": runtime.rspamd_url,
        "rspamd_user": runtime.rspamd_user,
        "rspamd_password_set": bool(runtime.rspamd_password),
        "rspamd_tls_verify": runtime.rspamd_tls_verify,
        "digest_enabled": runtime.digest_enabled,
        "digest_time": runtime.digest_time,
        "timezone": runtime.timezone,
        "admin_alert_email": runtime.admin_alert_email,
        "portal_tracking_enabled": runtime.portal_tracking_enabled,
    }


def get_settings() -> AppSettings:
    """Load current application, mail, LDAP, and runtime settings from disk and environment."""
    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
    config_dir = Path(os.environ.get("CONFIG_DIR", "/app/config"))

    mail_env = parse_env_file(config_dir / "discovery_mail.env")
    ldap_env = parse_env_file(config_dir / "discovery_ldap.env")

    def mget(key: str, default: str = "") -> str:
        if key in mail_env and mail_env[key] != "":
            return mail_env[key]
        return os.environ.get(key, default)

    def lget(key: str, default: str = "") -> str:
        if key in ldap_env and ldap_env[key] != "":
            return ldap_env[key]
        return os.environ.get(key, default)

    master_user = os.environ.get("DOVECOT_MASTER_USER", "vmail")
    master_password = os.environ.get("DOVECOT_MASTER_PASS", "")
    creds_file = config_dir / "master.creds"
    if creds_file.is_file():
        try:
            raw_creds = creds_file.read_text(encoding="utf-8").splitlines()
            for line in raw_creds:
                cleaned = line.rstrip("\r\n")
                if ":" in cleaned and not cleaned.lstrip().startswith("#"):
                    u_part, p_part = cleaned.split(":", 1)
                    master_user = u_part.strip() or "vmail"
                    master_password = p_part
                    break
        except OSError as exc:
            logger.warning("Failed reading master.creds: %s", exc)

    internal_token = os.environ.get("INTERNAL_TOKEN", "").strip()
    token_file = config_dir / "internal.token"
    if token_file.is_file():
        try:
            file_token = token_file.read_text(encoding="utf-8").strip()
            if file_token:
                internal_token = file_token
        except OSError as exc:
            logger.warning("Failed reading internal.token: %s", exc)

    secret_key = os.environ.get("APP_SECRET_KEY", "").strip() or _EPHEMERAL_SECRET_KEY
    allowed_groups = parse_allowed_groups(os.environ.get("ALLOWED_GROUPS", ""))
    portal_hostname = os.environ.get("PORTAL_HOSTNAME", "").strip()
    portal_enabled = _parse_bool(os.environ.get("PORTAL_ENABLED"), bool(portal_hostname))

    mail_settings = MailSettings(
        host=translate_loopback_host(mget("MAIL_HOST", "")),
        imap_port=_parse_int(mget("MAIL_IMAP_PORT"), 143),
        sieve_port=_parse_int(mget("MAIL_SIEVE_PORT"), 4190),
        submission_port=_parse_int(mget("MAIL_SUBMISSION_PORT"), 587),
        module_id=mget("MAIL_MODULE_ID", ""),
        master_user=master_user,
        master_password=master_password,
        spam_folder=os.environ.get("DOVECOT_SPAM_FOLDER", "Junk").strip() or "Junk",
        sieve_starttls=_parse_bool(os.environ.get("SIEVE_STARTTLS"), False),
        sieve_tls_verify=_parse_bool(os.environ.get("SIEVE_TLS_VERIFY"), False),
        sieve_timeout=float(_parse_int(os.environ.get("SIEVE_TIMEOUT_SECONDS"), 10)),
    )

    schema_raw = lget("LDAP_SCHEMA", "rfc2307").strip().lower()
    if schema_raw not in ("rfc2307", "ad"):
        schema_raw = "rfc2307"

    ldap_settings = LdapSettings(
        host=translate_loopback_host(lget("LDAP_HOST", "")),
        port=_parse_int(lget("LDAP_PORT"), 389),
        base_dn=lget("LDAP_BASE", ""),
        bind_dn=lget("LDAP_USER", ""),
        bind_password=lget("LDAP_PASS", ""),
        schema=schema_raw,
        domain=lget("LDAP_DOMAIN", os.environ.get("USER_DOMAIN", "")).strip(),
        use_tls=_parse_bool(os.environ.get("LDAP_TLS"), False),
        timeout=float(_parse_int(os.environ.get("LDAP_TIMEOUT_SECONDS"), 10)),
    )

    return AppSettings(
        data_dir=data_dir,
        config_dir=config_dir,
        static_dir=_resolve_static_dir(),
        secret_key=secret_key,
        internal_token=internal_token,
        allowed_groups=allowed_groups,
        portal_enabled=portal_enabled,
        portal_hostname=portal_hostname,
        session_ttl_seconds=_parse_int(os.environ.get("SESSION_TTL_SECONDS"), 43200),
        reconcile_interval_seconds=_parse_int(os.environ.get("RECONCILE_INTERVAL_SECONDS"), 300),
        login_rate_limit_max=_parse_int(os.environ.get("LOGIN_RATE_LIMIT_MAX"), 10),
        login_rate_limit_window_seconds=_parse_int(
            os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS"), 60
        ),
        mail=mail_settings,
        ldap=ldap_settings,
        runtime=load_runtime_settings(config_dir),
    )


def load_mailboxes_cache(config_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read mailboxes_cache.json from CONFIG_DIR if present."""
    cfg_dir = config_dir or Path(os.environ.get("CONFIG_DIR", "/app/config"))
    cache_path = cfg_dir / "mailboxes_cache.json"
    if not cache_path.is_file():
        return []
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("mailboxes"), list):
            return [m for m in payload["mailboxes"] if isinstance(m, dict) and m.get("user")]
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed reading mailboxes_cache.json: %s", exc)
    return []
