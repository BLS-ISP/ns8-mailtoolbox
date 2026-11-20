"""LDAP / Active Directory client for NS8 OpenLDAP (rfc2307) and Samba AD (ad).

Handles user directory discovery (including Phase 2 signature attributes:
telephoneNumber, mobile, company, title, department, groups) as well as portal
user authentication (LDAP bind) and group-based authorization (`ALLOWED_GROUPS`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable

from ldap3 import Connection, SUBTREE, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

from app.backend.config import LdapSettings, get_settings

logger = logging.getLogger(__name__)

_SYSTEM_USERS_LOWER = frozenset({"krbtgt", "guest", "nobody", "ldapservice"})


class AuthError(Exception):
    """Base portal authentication/authorization error."""


class AuthInvalidCredentialsError(AuthError):
    """Invalid username or password (HTTP 401)."""


class AuthForbiddenGroupError(AuthError):
    """Authenticated user is not in ALLOWED_GROUPS (HTTP 403)."""


class AuthBackendUnavailableError(AuthError):
    """LDAP backend unconfigured or unreachable."""


@dataclass
class AuthenticatedUser:
    username: str
    display_name: str
    groups: list[str] = field(default_factory=list)


@dataclass
class DirectoryUser:
    user: str
    dn: str
    display_name: str
    given_name: str = ""
    sn: str = ""
    mail: str = ""
    department: str = ""
    title: str = ""
    telephone_number: str = ""
    mobile: str = ""
    company: str = ""
    groups: list[str] = field(default_factory=list)


ConnectionFactory = Callable[[LdapSettings, str | None, str | None], Connection]
_custom_connection_factory: ConnectionFactory | None = None


def set_ldap_connection_factory(factory: ConnectionFactory | None) -> None:
    """Override LDAP connection creation (used by unit/integration tests)."""
    global _custom_connection_factory
    _custom_connection_factory = factory


def get_ldap_connection_factory() -> ConnectionFactory | None:
    return _custom_connection_factory


def _open_connection(
    settings: LdapSettings,
    bind_dn: str | None = None,
    bind_password: str | None = None,
) -> Connection:
    if _custom_connection_factory is not None:
        return _custom_connection_factory(settings, bind_dn, bind_password)

    if not settings.host:
        raise AuthBackendUnavailableError("LDAP_HOST is not configured")

    server = Server(
        settings.host,
        port=int(settings.port),
        use_ssl=bool(settings.use_tls),
        connect_timeout=settings.timeout,
    )
    if bind_dn:
        return Connection(
            server,
            user=bind_dn,
            password=bind_password or "",
            receive_timeout=int(settings.timeout),
            auto_bind=False,
        )
    return Connection(
        server,
        receive_timeout=int(settings.timeout),
        auto_bind=False,
    )


def _attrs_lower(entry: Any) -> dict[str, list[Any]]:
    raw = getattr(entry, "entry_attributes_as_dict", None) or {}
    result: dict[str, list[Any]] = {}
    for k, v in raw.items():
        if isinstance(v, (list, tuple)):
            result[k.lower()] = list(v)
        elif v is not None:
            result[k.lower()] = [v]
    return result


def _first_str(attrs: dict[str, list[Any]], *keys: str) -> str:
    for key in keys:
        vals = attrs.get(key.lower())
        if vals:
            for item in vals:
                s = str(item).strip()
                if s:
                    return s
    return ""


def _all_strs(attrs: dict[str, list[Any]], *keys: str) -> list[str]:
    out: list[str] = []
    for key in keys:
        for item in attrs.get(key.lower(), []):
            s = str(item).strip()
            if s:
                out.append(s)
    return out


def _extract_group_name_from_dn(dn_value: str) -> str:
    try:
        rdns = parse_dn(dn_value)
        if rdns and rdns[0]:
            return str(rdns[0][1]).strip()
    except Exception:
        pass
    first_part = dn_value.split(",", 1)[0]
    if "=" in first_part:
        return first_part.split("=", 1)[1].strip()
    return first_part.strip()


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = item.strip()
        if not cleaned:
            continue
        low = cleaned.lower()
        if low not in seen:
            seen.add(low)
            result.append(cleaned)
    return result


def _resolve_groups_for_entry(
    svc_conn: Connection,
    base_dn: str,
    user_dn: str,
    canonical_user: str,
    short_user: str,
    attrs: dict[str, list[Any]],
) -> list[str]:
    discovered_groups: list[str] = []
    for dn_item in _all_strs(attrs, "memberOf"):
        grp = _extract_group_name_from_dn(dn_item)
        if grp:
            discovered_groups.append(grp)

    esc_canon = escape_filter_chars(canonical_user)
    esc_short = escape_filter_chars(short_user)
    esc_dn = escape_filter_chars(user_dn)
    group_filter = (
        "(&(|(objectClass=posixGroup)(objectClass=groupOfNames)"
        "(objectClass=groupOfUniqueNames)(objectClass=group))"
        f"(|(memberUid={esc_canon})(memberUid={esc_short})"
        f"(member={esc_dn})(uniqueMember={esc_dn})))"
    )
    svc_conn.search(
        search_base=base_dn,
        search_filter=group_filter,
        search_scope=SUBTREE,
        attributes=["cn", "sAMAccountName"],
    )
    for g_entry in svc_conn.entries:
        g_attrs = _attrs_lower(g_entry)
        g_name = _first_str(g_attrs, "sAMAccountName", "cn") or _extract_group_name_from_dn(
            str(g_entry.entry_dn)
        )
        if g_name:
            discovered_groups.append(g_name)

    gid_number = _first_str(attrs, "gidNumber")
    if gid_number:
        esc_gid = escape_filter_chars(gid_number)
        svc_conn.search(
            search_base=base_dn,
            search_filter=f"(&(objectClass=posixGroup)(gidNumber={esc_gid}))",
            search_scope=SUBTREE,
            attributes=["cn"],
        )
        for pg_entry in svc_conn.entries:
            pg_attrs = _attrs_lower(pg_entry)
            pg_name = _first_str(pg_attrs, "cn")
            if pg_name:
                discovered_groups.append(pg_name)

    return _dedupe_preserve_order(discovered_groups)


def _entry_to_directory_user(
    entry: Any,
    settings: LdapSettings,
    groups: list[str] | None = None,
) -> DirectoryUser | None:
    attrs = _attrs_lower(entry)
    if settings.schema == "ad":
        raw_user = _first_str(attrs, "sAMAccountName", "uid")
    else:
        raw_user = _first_str(attrs, "uid", "sAMAccountName")

    if not raw_user:
        return None
    if "@" in raw_user:
        raw_user = raw_user.split("@", 1)[0]
    if raw_user.endswith("$") or raw_user.lower() in _SYSTEM_USERS_LOWER:
        return None
    if raw_user.lower().startswith("dns-"):
        return None

    given_name = _first_str(attrs, "givenName")
    sn = _first_str(attrs, "sn")
    full_from_parts = f"{given_name} {sn}".strip()
    display_name = _first_str(attrs, "displayName", "cn") or full_from_parts or raw_user

    mail = _first_str(attrs, "mail")
    if not mail and settings.schema == "ad":
        upn = _first_str(attrs, "userPrincipalName")
        if "@" in upn:
            mail = upn
    if not mail and settings.domain:
        mail = f"{raw_user}@{settings.domain}"

    department = _first_str(attrs, "department", "departmentNumber", "ou")
    title = _first_str(attrs, "title")
    telephone_number = _first_str(attrs, "telephoneNumber", "phone")
    mobile = _first_str(attrs, "mobile", "mobileTelephoneNumber")
    company = _first_str(attrs, "company", "o", "organizationName")

    resolved_groups = list(groups) if groups is not None else [
        _extract_group_name_from_dn(dn_val)
        for dn_val in _all_strs(attrs, "memberOf")
        if _extract_group_name_from_dn(dn_val)
    ]

    return DirectoryUser(
        user=raw_user,
        dn=str(entry.entry_dn),
        display_name=display_name,
        given_name=given_name,
        sn=sn,
        mail=mail,
        department=department,
        title=title,
        telephone_number=telephone_number,
        mobile=mobile,
        company=company,
        groups=_dedupe_preserve_order(resolved_groups),
    )


def list_directory_users(ldap_settings: LdapSettings | None = None) -> list[DirectoryUser]:
    """Query LDAP/AD for user accounts to merge with mailboxes_cache.json."""
    settings = ldap_settings or get_settings().ldap
    if _custom_connection_factory is None and (not settings.host or not settings.base_dn):
        return []
    if not settings.base_dn:
        return []

    conn: Connection | None = None
    try:
        conn = _open_connection(
            settings,
            settings.bind_dn or None,
            settings.bind_password if settings.bind_dn else None,
        )
        if not conn.bind():
            logger.warning("LDAP service bind failed while listing users")
            return []

        if settings.schema == "ad":
            search_filter = "(&(objectClass=user)(!(objectClass=computer))(sAMAccountName=*))"
        else:
            search_filter = "(&(|(objectClass=inetOrgPerson)(objectClass=posixAccount))(uid=*))"

        conn.search(
            search_base=settings.base_dn,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["*", "memberOf"],
        )

        users: list[DirectoryUser] = []
        for entry in conn.entries:
            du = _entry_to_directory_user(entry, settings)
            if du is not None:
                users.append(du)
        return users
    except (LDAPException, OSError, AuthBackendUnavailableError) as exc:
        logger.warning("LDAP directory listing failed: %s", exc)
        return []
    finally:
        if conn is not None:
            try:
                conn.unbind()
            except Exception:
                pass


def get_directory_user_with_groups(
    user_or_email: str,
    ldap_settings: LdapSettings | None = None,
) -> DirectoryUser | None:
    """Look up a single user's full AD/LDAP attributes and group memberships for signatures."""
    raw = (user_or_email or "").strip()
    if not raw:
        return None
    if "\\" in raw:
        raw = raw.split("\\", 1)[1].strip()
    short_user = raw.split("@", 1)[0].strip() if "@" in raw else raw
    if not short_user:
        return None

    settings = ldap_settings or get_settings().ldap
    if _custom_connection_factory is None and (not settings.host or not settings.base_dn):
        return None
    if not settings.base_dn:
        return None

    esc_short = escape_filter_chars(short_user)
    esc_full = escape_filter_chars(raw)

    conn: Connection | None = None
    try:
        conn = _open_connection(
            settings,
            settings.bind_dn or None,
            settings.bind_password if settings.bind_dn else None,
        )
        if not conn.bind():
            return None

        if settings.schema == "ad":
            u_filter = (
                "(&(objectClass=user)(!(objectClass=computer))"
                f"(|(sAMAccountName={esc_short})(userPrincipalName={esc_full})(mail={esc_full})))"
            )
        else:
            u_filter = (
                "(&(|(objectClass=inetOrgPerson)(objectClass=posixAccount))"
                f"(|(uid={esc_short})(uid={esc_full})(mail={esc_full})))"
            )

        conn.search(
            search_base=settings.base_dn,
            search_filter=u_filter,
            search_scope=SUBTREE,
            attributes=["*", "memberOf"],
        )
        if not conn.entries:
            return None

        entry = conn.entries[0]
        attrs = _attrs_lower(entry)
        du = _entry_to_directory_user(entry, settings)
        if du is None:
            return None

        du.groups = _resolve_groups_for_entry(
            conn,
            settings.base_dn,
            du.dn,
            du.user,
            short_user,
            attrs,
        )
        return du
    except (LDAPException, OSError, AuthBackendUnavailableError) as exc:
        logger.warning("LDAP lookup for %s failed: %s", user_or_email, exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.unbind()
            except Exception:
                pass


def authenticate_portal_user(
    username: str,
    password: str,
    *,
    ldap_settings: LdapSettings | None = None,
    allowed_groups: list[str] | None = None,
) -> AuthenticatedUser:
    """Authenticate user via LDAP bind and enforce membership in ALLOWED_GROUPS."""
    login_raw = (username or "").strip()
    if not login_raw or not password:
        # Never allow empty password (RFC 4513 unauthenticated bind hazard)
        raise AuthInvalidCredentialsError("Invalid username or password")

    app_cfg = get_settings()
    settings = ldap_settings or app_cfg.ldap
    permitted_groups = allowed_groups if allowed_groups is not None else app_cfg.allowed_groups

    if "\\" in login_raw:
        login_raw = login_raw.split("\\", 1)[1].strip()
    short_user = login_raw.split("@", 1)[0].strip() if "@" in login_raw else login_raw
    if not short_user:
        raise AuthInvalidCredentialsError("Invalid username or password")

    if not settings.base_dn:
        raise AuthBackendUnavailableError("LDAP directory is not configured")

    esc_short = escape_filter_chars(short_user)
    esc_full = escape_filter_chars(login_raw)

    svc_conn: Connection | None = None
    user_conn: Connection | None = None
    try:
        svc_conn = _open_connection(
            settings,
            settings.bind_dn or None,
            settings.bind_password if settings.bind_dn else None,
        )
        if not svc_conn.bind():
            raise AuthBackendUnavailableError("LDAP service bind failed")

        if settings.schema == "ad":
            user_filter = (
                "(&(objectClass=user)(!(objectClass=computer))"
                f"(|(sAMAccountName={esc_short})(userPrincipalName={esc_full})(mail={esc_full})))"
            )
        else:
            user_filter = (
                "(&(|(objectClass=inetOrgPerson)(objectClass=posixAccount))"
                f"(|(uid={esc_short})(uid={esc_full})(mail={esc_full})))"
            )

        svc_conn.search(
            search_base=settings.base_dn,
            search_filter=user_filter,
            search_scope=SUBTREE,
            attributes=["*", "memberOf"],
        )
        if not svc_conn.entries:
            raise AuthInvalidCredentialsError("Invalid username or password")

        entry = svc_conn.entries[0]
        user_dn = str(entry.entry_dn)
        attrs = _attrs_lower(entry)

        if settings.schema == "ad":
            canonical_user = _first_str(attrs, "sAMAccountName", "uid") or short_user
        else:
            canonical_user = _first_str(attrs, "uid", "sAMAccountName") or short_user
        if "@" in canonical_user:
            canonical_user = canonical_user.split("@", 1)[0]

        given_name = _first_str(attrs, "givenName")
        sn = _first_str(attrs, "sn")
        display_name = (
            _first_str(attrs, "displayName", "cn")
            or f"{given_name} {sn}".strip()
            or canonical_user
        )

        # Verify user credentials via direct LDAP SIMPLE bind as user_dn
        user_conn = _open_connection(settings, user_dn, password)
        if not user_conn.bind() or not user_conn.bound:
            raise AuthInvalidCredentialsError("Invalid username or password")
        user_conn.unbind()
        user_conn = None

        user_groups = _resolve_groups_for_entry(
            svc_conn,
            settings.base_dn,
            user_dn,
            canonical_user,
            short_user,
            attrs,
        )

        allowed_lower = {g.strip().lower() for g in permitted_groups if g and g.strip()}
        if not allowed_lower or not any(g.lower() in allowed_lower for g in user_groups):
            raise AuthForbiddenGroupError(
                f"User {canonical_user!r} is not a member of an authorized secretariat group"
            )

        return AuthenticatedUser(
            username=canonical_user,
            display_name=display_name,
            groups=user_groups,
        )
    except AuthError:
        raise
    except (LDAPException, OSError) as exc:
        logger.error("LDAP authentication error: %s", exc)
        raise AuthBackendUnavailableError(f"LDAP error: {exc}") from exc
    finally:
        if user_conn is not None:
            try:
                user_conn.unbind()
            except Exception:
                pass
        if svc_conn is not None:
            try:
                svc_conn.unbind()
            except Exception:
                pass
