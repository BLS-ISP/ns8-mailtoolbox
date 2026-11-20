"""LDAP / Active Directory client for NS8 OpenLDAP (rfc2307) and Samba AD (ad).

Supports:
- Bulk user + group resolution without N+1 queries (`list_directory_users`, `list_directory_groups`)
- Nested AD/LDAP group chains (`1.2.840.113556.1.4.1941` + iterative BFS graph traversal up to depth 10)
- Active Directory `primaryGroupID` resolution via `primaryGroupToken` and binary/string `objectSid` RIDs
- Full `user`, `DOMAIN\\user`, `user@custom-upn`, and `mail` authentication (`authenticate_portal_user`)
- Built-in AD system plumbing group classification (`is_system`) and `member_count` computation
"""

from __future__ import annotations

from collections import deque
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

# Built-in Active Directory plumbing groups hidden by default in UI (`is_system = True`).
# Note: `Domain Admins`, `Domain Users`, `Administrators`, and all custom groups stay `is_system = False`.
AD_SYSTEM_GROUPS_LOWER = frozenset(
    {
        "allowed rodc password replication group",
        "denied rodc password replication group",
        "cert publishers",
        "dnsadmins",
        "dnsupdateproxy",
        "enterprise read-only domain controllers",
        "read-only domain controllers",
        "domain controllers",
        "group policy creator owners",
        "ras and ias servers",
        "terminal server license servers",
        "incoming forest trust builders",
        "windows authorization access group",
        "pre-windows 2000 compatible access",
        "certificate service dcom access",
        "network configuration operators",
        "performance log users",
        "performance monitor users",
        "distributed com users",
        "iis_iusrs",
        "cryptographic operators",
        "event log readers",
        "account operators",
        "server operators",
        "print operators",
        "backup operators",
        "replicator",
        "guests",
        "domain guests",
        "domain computers",
        "schema admins",
        "enterprise admins",
        "key admins",
        "enterprise key admins",
        "cloneable domain controllers",
        "protected users",
    }
)

# Fallback RID map when minimal mock AD group entries omit `objectSid` / `primaryGroupToken`
_WELL_KNOWN_AD_GROUP_RIDS: dict[str, str] = {
    "enterprise read-only domain controllers": "498",
    "domain admins": "512",
    "domain users": "513",
    "domain guests": "514",
    "domain computers": "515",
    "domain controllers": "516",
    "cert publishers": "517",
    "schema admins": "518",
    "enterprise admins": "519",
    "group policy creator owners": "520",
    "read-only domain controllers": "521",
    "cloneable domain controllers": "522",
    "administrators": "544",
    "users": "545",
    "guests": "546",
}


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


@dataclass
class _GroupGraphNode:
    dn: str
    dn_lower: str
    name: str
    description: str
    gid_number: str
    rid: str
    member_dns_lower: set[str] = field(default_factory=set)
    member_uids_lower: set[str] = field(default_factory=set)
    parent_dns_lower: set[str] = field(default_factory=set)


ConnectionFactory = Callable[[LdapSettings, str | None, str | None], Connection]
_custom_connection_factory: ConnectionFactory | None = None


def set_ldap_connection_factory(factory: ConnectionFactory | None) -> None:
    """Override LDAP connection creation (used by unit/integration tests)."""
    global _custom_connection_factory
    _custom_connection_factory = factory


def get_ldap_connection_factory() -> ConnectionFactory | None:
    return _custom_connection_factory


def is_ad_system_group(group_name: str) -> bool:
    return (group_name or "").strip().lower() in AD_SYSTEM_GROUPS_LOWER


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


def _raw_attrs_lower(entry: Any) -> dict[str, list[Any]]:
    raw = getattr(entry, "entry_raw_attributes", None) or {}
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
                if isinstance(item, (bytes, bytearray)):
                    try:
                        s = item.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        continue
                else:
                    s = str(item).strip()
                if s:
                    return s
    return ""


def _all_strs(attrs: dict[str, list[Any]], *keys: str) -> list[str]:
    out: list[str] = []
    for key in keys:
        for item in attrs.get(key.lower(), []):
            if isinstance(item, (bytes, bytearray)):
                try:
                    s = item.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
            else:
                s = str(item).strip()
            if s:
                out.append(s)
    return out


def _extract_rid_from_sid_value(val: Any) -> str:
    """Extract numeric RID from either string SID (`S-1-5-21-...-513`) or binary SID bytes."""
    if isinstance(val, (bytes, bytearray)):
        if val.startswith((b"S-1-", b"s-1-")):
            try:
                text = val.decode("ascii").strip()
                tail = text.rsplit("-", 1)[-1]
                if tail.isdigit():
                    return tail
            except UnicodeDecodeError:
                pass
        # Binary Windows SID: byte 0 = revision (1), byte 1 = sub-authority count, last 4 bytes = uint32le RID
        if len(val) >= 8 and val[0] == 1:
            return str(int.from_bytes(val[-4:], byteorder="little", signed=False))
        return ""

    if isinstance(val, str):
        cleaned = val.strip()
        if cleaned.upper().startswith("S-1-") and "-" in cleaned:
            tail = cleaned.rsplit("-", 1)[-1].strip()
            if tail.isdigit():
                return tail
    return ""


def _extract_group_rid(entry: Any, attrs: dict[str, list[Any]], group_name: str) -> str:
    pg_token = _first_str(attrs, "primaryGroupToken")
    if pg_token.isdigit():
        return pg_token

    for candidate in attrs.get("objectsid", []):
        rid = _extract_rid_from_sid_value(candidate)
        if rid:
            return rid

    raw_attrs = _raw_attrs_lower(entry)
    for raw_cand in raw_attrs.get("objectsid", []):
        rid = _extract_rid_from_sid_value(raw_cand)
        if rid:
            return rid

    return _WELL_KNOWN_AD_GROUP_RIDS.get(group_name.strip().lower(), "")


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


def _load_group_graph(svc_conn: Connection, base_dn: str) -> dict[str, _GroupGraphNode]:
    """Fetch all directory groups in a single query and wire nested parent/child edges."""
    group_filter = (
        "(|(objectClass=posixGroup)(objectClass=groupOfNames)"
        "(objectClass=groupOfUniqueNames)(objectClass=group))"
    )
    svc_conn.search(
        search_base=base_dn,
        search_filter=group_filter,
        search_scope=SUBTREE,
        attributes=["*", "memberOf", "objectSid", "primaryGroupToken"],
    )

    nodes: dict[str, _GroupGraphNode] = {}
    raw_member_of: dict[str, list[str]] = {}

    for g_entry in svc_conn.entries:
        g_dn = str(g_entry.entry_dn)
        g_dn_low = g_dn.lower()
        g_attrs = _attrs_lower(g_entry)
        g_name = _first_str(g_attrs, "sAMAccountName", "cn") or _extract_group_name_from_dn(g_dn)
        if not g_name:
            continue

        desc = _first_str(g_attrs, "description")
        gid_num = _first_str(g_attrs, "gidNumber")
        rid = _extract_group_rid(g_entry, g_attrs, g_name)
        m_dns = {d.lower() for d in _all_strs(g_attrs, "member", "uniqueMember") if d}
        m_uids = {u.lower() for u in _all_strs(g_attrs, "memberUid") if u}
        raw_member_of[g_dn_low] = [d.lower() for d in _all_strs(g_attrs, "memberOf") if d]

        nodes[g_dn_low] = _GroupGraphNode(
            dn=g_dn,
            dn_lower=g_dn_low,
            name=g_name,
            description=desc,
            gid_number=gid_num,
            rid=rid,
            member_dns_lower=m_dns,
            member_uids_lower=m_uids,
        )

    # Wire parent_dns_lower from both forward `member`/`uniqueMember` and `memberOf`
    for parent_dn_low, parent_node in nodes.items():
        for member_dn_low in parent_node.member_dns_lower:
            if member_dn_low in nodes and member_dn_low != parent_dn_low:
                nodes[member_dn_low].parent_dns_lower.add(parent_dn_low)

    for child_dn_low, parent_list in raw_member_of.items():
        if child_dn_low in nodes:
            for p_dn_low in parent_list:
                if p_dn_low != child_dn_low:
                    nodes[child_dn_low].parent_dns_lower.add(p_dn_low)

    return nodes


def _resolve_user_groups_from_graph(
    *,
    graph: dict[str, _GroupGraphNode],
    user_dn: str,
    canonical_user: str,
    short_user: str,
    attrs: dict[str, list[Any]],
    max_depth: int = 10,
) -> tuple[list[str], set[str]]:
    """Resolve direct, POSIX gidNumber, AD primaryGroupID, and nested parent groups (BFS)."""
    user_dn_low = user_dn.lower()
    canon_low = canonical_user.lower()
    short_low = short_user.lower()
    gid_number = _first_str(attrs, "gidNumber")
    primary_group_id = _first_str(attrs, "primaryGroupID")

    seed_group_dns: list[str] = []
    extra_external_names: list[str] = []

    # 1. User's own `memberOf` DNs
    for mo_dn in _all_strs(attrs, "memberOf"):
        mo_low = mo_dn.lower()
        if mo_low in graph:
            seed_group_dns.append(mo_low)
        else:
            ext_name = _extract_group_name_from_dn(mo_dn)
            if ext_name:
                extra_external_names.append(ext_name)

    # 2. Direct membership via group `member`, `uniqueMember`, `memberUid`, `gidNumber`, or AD `primaryGroupID`
    for g_dn_low, g_node in graph.items():
        if (
            user_dn_low in g_node.member_dns_lower
            or canon_low in g_node.member_uids_lower
            or short_low in g_node.member_uids_lower
            or (gid_number and g_node.gid_number == gid_number)
            or (primary_group_id and g_node.rid == primary_group_id)
        ):
            seed_group_dns.append(g_dn_low)

    # 3. Iterative BFS traversal up parent groups (up to `max_depth`, cycle-safe)
    visited_dns: set[str] = set()
    ordered_names: list[str] = []
    queue: deque[tuple[str, int]] = deque()

    for s_dn in seed_group_dns:
        if s_dn not in visited_dns:
            visited_dns.add(s_dn)
            queue.append((s_dn, 0))

    while queue:
        cur_dn, depth = queue.popleft()
        node = graph.get(cur_dn)
        if node is not None:
            ordered_names.append(node.name)
            if depth < max_depth:
                for p_dn in sorted(node.parent_dns_lower):
                    if p_dn not in visited_dns:
                        visited_dns.add(p_dn)
                        queue.append((p_dn, depth + 1))
        else:
            ext_n = _extract_group_name_from_dn(cur_dn)
            if ext_n:
                ordered_names.append(ext_n)

    ordered_names.extend(extra_external_names)
    return _dedupe_preserve_order(ordered_names), visited_dns


def _resolve_groups_for_single_user(
    svc_conn: Connection,
    settings: LdapSettings,
    user_dn: str,
    canonical_user: str,
    short_user: str,
    attrs: dict[str, list[Any]],
) -> list[str]:
    graph = _load_group_graph(svc_conn, settings.base_dn)
    groups, _ = _resolve_user_groups_from_graph(
        graph=graph,
        user_dn=user_dn,
        canonical_user=canonical_user,
        short_user=short_user,
        attrs=attrs,
    )

    # Also attempt AD's native LDAP_MATCHING_RULE_IN_CHAIN OID in case cross-container groups exist
    if settings.schema == "ad":
        try:
            esc_dn = escape_filter_chars(user_dn)
            svc_conn.search(
                search_base=settings.base_dn,
                search_filter=f"(&(objectClass=group)(member:1.2.840.113556.1.4.1941:={esc_dn}))",
                search_scope=SUBTREE,
                attributes=["cn", "sAMAccountName"],
            )
            for g_entry in svc_conn.entries:
                g_attrs = _attrs_lower(g_entry)
                g_name = _first_str(g_attrs, "sAMAccountName", "cn") or _extract_group_name_from_dn(
                    str(g_entry.entry_dn)
                )
                if g_name:
                    groups.append(g_name)
        except Exception:
            pass

    return _dedupe_preserve_order(groups)


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

    resolved_groups = (
        list(groups)
        if groups is not None
        else [
            _extract_group_name_from_dn(dn_val)
            for dn_val in _all_strs(attrs, "memberOf")
            if _extract_group_name_from_dn(dn_val)
        ]
    )

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


def _search_user_entry(
    svc_conn: Connection,
    settings: LdapSettings,
    raw_identifier: str,
    short_user: str,
) -> Any | None:
    esc_short = escape_filter_chars(short_user)
    esc_full = escape_filter_chars(raw_identifier)

    if settings.schema == "ad":
        u_filter = (
            "(&(objectClass=user)(!(objectClass=computer))"
            f"(|(sAMAccountName={esc_short})(sAMAccountName={esc_full})"
            f"(userPrincipalName={esc_full})(mail={esc_full})))"
        )
    else:
        u_filter = (
            "(&(|(objectClass=inetOrgPerson)(objectClass=posixAccount))"
            f"(|(uid={esc_short})(uid={esc_full})(mail={esc_full})))"
        )

    svc_conn.search(
        search_base=settings.base_dn,
        search_filter=u_filter,
        search_scope=SUBTREE,
        attributes=["*", "memberOf", "primaryGroupID"],
    )
    if svc_conn.entries:
        return svc_conn.entries[0]

    # Secondary AD fallback when user entered short UPN prefix (without `@custom-upn`)
    if settings.schema == "ad" and "@" not in raw_identifier:
        svc_conn.search(
            search_base=settings.base_dn,
            search_filter=(
                "(&(objectClass=user)(!(objectClass=computer))"
                f"(|(userPrincipalName={esc_short}@*)(mail={esc_short}@*)))"
            ),
            search_scope=SUBTREE,
            attributes=["*", "memberOf", "primaryGroupID"],
        )
        if len(svc_conn.entries) == 1:
            return svc_conn.entries[0]

    return None


def list_directory_users(ldap_settings: LdapSettings | None = None) -> list[DirectoryUser]:
    """Query LDAP/AD for user accounts and bulk-resolve direct, primary, and nested groups."""
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
            attributes=["*", "memberOf", "primaryGroupID"],
        )
        user_entries = list(conn.entries)
        graph = _load_group_graph(conn, settings.base_dn)

        users: list[DirectoryUser] = []
        for entry in user_entries:
            du = _entry_to_directory_user(entry, settings)
            if du is None:
                continue
            attrs = _attrs_lower(entry)
            resolved_groups, _ = _resolve_user_groups_from_graph(
                graph=graph,
                user_dn=du.dn,
                canonical_user=du.user,
                short_user=du.user,
                attrs=attrs,
            )
            du.groups = resolved_groups
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


def list_directory_groups(ldap_settings: LdapSettings | None = None) -> list[dict[str, Any]]:
    """Return directory groups with `member_count` (including primary & nested) and `is_system`."""
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
            return []

        if settings.schema == "ad":
            u_filter = "(&(objectClass=user)(!(objectClass=computer))(sAMAccountName=*))"
        else:
            u_filter = "(&(|(objectClass=inetOrgPerson)(objectClass=posixAccount))(uid=*))"

        conn.search(
            search_base=settings.base_dn,
            search_filter=u_filter,
            search_scope=SUBTREE,
            attributes=["*", "memberOf", "primaryGroupID"],
        )
        user_entries = list(conn.entries)
        graph = _load_group_graph(conn, settings.base_dn)

        members_per_group_dn: dict[str, set[str]] = {g_dn: set() for g_dn in graph}
        for entry in user_entries:
            du = _entry_to_directory_user(entry, settings)
            if du is None:
                continue
            attrs = _attrs_lower(entry)
            _, visited_group_dns = _resolve_user_groups_from_graph(
                graph=graph,
                user_dn=du.dn,
                canonical_user=du.user,
                short_user=du.user,
                attrs=attrs,
            )
            for g_dn_low in visited_group_dns:
                if g_dn_low in members_per_group_dn:
                    members_per_group_dn[g_dn_low].add(du.user.lower())

        items: list[dict[str, Any]] = []
        seen_names_low: set[str] = set()
        for g_dn_low, g_node in graph.items():
            low_name = g_node.name.lower()
            if low_name in seen_names_low:
                continue
            seen_names_low.add(low_name)

            resolved_user_cnt = len(members_per_group_dn.get(g_dn_low, set()))
            # If no user entries resolved, count non-group direct member DNs/UIDs if present
            direct_non_group = {
                d for d in g_node.member_dns_lower if d not in graph
            } | set(g_node.member_uids_lower)
            member_count = max(resolved_user_cnt, len(direct_non_group))

            items.append(
                {
                    "group": g_node.name,
                    "description": g_node.description,
                    "member_count": int(member_count),
                    "is_system": is_ad_system_group(g_node.name),
                }
            )

        items.sort(key=lambda x: (bool(x["is_system"]), str(x["group"]).lower()))
        return items
    except (LDAPException, OSError, AuthBackendUnavailableError) as exc:
        logger.warning("LDAP group listing failed: %s", exc)
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

    conn: Connection | None = None
    try:
        conn = _open_connection(
            settings,
            settings.bind_dn or None,
            settings.bind_password if settings.bind_dn else None,
        )
        if not conn.bind():
            return None

        entry = _search_user_entry(conn, settings, raw, short_user)
        if entry is None:
            return None

        attrs = _attrs_lower(entry)
        du = _entry_to_directory_user(entry, settings)
        if du is None:
            return None

        du.groups = _resolve_groups_for_single_user(
            conn,
            settings,
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

        entry = _search_user_entry(svc_conn, settings, login_raw, short_user)
        if entry is None:
            raise AuthInvalidCredentialsError("Invalid username or password")

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
        bound_ok = bool(user_conn.bind() and user_conn.bound)
        user_conn.unbind()
        user_conn = None

        # Fallback to UPN bind on real AD only when DN contains escaped special characters
        if not bound_ok and settings.schema == "ad" and "\\" in user_dn:
            upn_val = _first_str(attrs, "userPrincipalName")
            if upn_val and _custom_connection_factory is None:
                user_conn = _open_connection(settings, upn_val, password)
                bound_ok = bool(user_conn.bind() and user_conn.bound)
                user_conn.unbind()
                user_conn = None

        if not bound_ok:
            raise AuthInvalidCredentialsError("Invalid username or password")

        user_groups = _resolve_groups_for_single_user(
            svc_conn,
            settings,
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
