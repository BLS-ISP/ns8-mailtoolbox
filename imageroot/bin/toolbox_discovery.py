#!/usr/bin/env python3

"""Shared NS8 mail-server Rspamd auto-discovery and live AD/OpenLDAP group discovery helpers."""

from __future__ import annotations

import json
import sys
from typing import Any

import agent
import agent.tasks
from agent.ldapproxy import Ldapproxy

try:
    from agent.ldapclient import Ldapclient
except ImportError:
    Ldapclient = None  # type: ignore[assignment]

try:
    from ldap3 import Connection, NONE, Server
except ImportError:
    Connection = None  # type: ignore[assignment]
    NONE = None  # type: ignore[assignment]
    Server = None  # type: ignore[assignment]

# Built-in Active Directory / Windows Server plumbing groups hidden by default in UI
# (Note: Domain Admins, Domain Users, Administrators, and all custom groups remain is_system=False)
AD_SYSTEM_GROUPS_LOWER: frozenset[str] = frozenset(
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

WELL_KNOWN_AD_GROUP_RIDS: dict[str, int] = {
    "domain admins": 512,
    "domain users": 513,
    "domain guests": 514,
    "domain computers": 515,
    "domain controllers": 516,
    "cert publishers": 517,
    "schema admins": 518,
    "enterprise admins": 519,
    "group policy creator owners": 520,
    "read-only domain controllers": 521,
    "cloneable domain controllers": 522,
    "protected users": 525,
    "key admins": 526,
    "enterprise key admins": 527,
}


def is_system_group(group_name: str) -> bool:
    return group_name.strip().lower() in AD_SYSTEM_GROUPS_LOWER


def normalize_rspamd_url(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    cleaned = str(raw_url).strip()
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    if "?" in cleaned:
        cleaned = cleaned.split("?", 1)[0]
    cleaned = cleaned.rstrip("/")
    if cleaned.lower().endswith("/history"):
        cleaned = cleaned[: -len("/history")].rstrip("/")
    return cleaned


def _parse_task_output(result: dict[str, Any]) -> Any:
    output = result.get("output")
    if isinstance(output, (dict, list)):
        return output
    if isinstance(output, str):
        txt = output.strip()
        if txt:
            try:
                return json.loads(txt)
            except Exception:
                return {}
    return {}


def discover_mail_rspamd_url(mail_module_id: str, mail_hostname: str = "") -> str:
    """Query `module/<mail_id>/get-filter-configuration` for Rspamd URL with smart HTTPS fallback."""
    if mail_module_id:
        try:
            res = agent.tasks.run(
                agent_id=f"module/{mail_module_id}",
                action="get-filter-configuration",
            )
            if res.get("exit_code") == 0:
                payload = _parse_task_output(res)
                if isinstance(payload, dict):
                    antispam = payload.get("antispam")
                    raw = (
                        antispam.get("rspamd_url")
                        if isinstance(antispam, dict)
                        else None
                    ) or payload.get("rspamd_url")
                    norm = normalize_rspamd_url(raw)
                    if norm:
                        return norm
        except Exception as exc:
            print(
                f"Warning: get-filter-configuration on module/{mail_module_id} skipped: {exc}",
                file=sys.stderr,
            )

    host = str(mail_hostname or "").strip()
    if host and "." in host and host not in ("127.0.0.1", "localhost"):
        return f"https://{host}/rspamd"
    return "http://127.0.0.1:11334"


def discover_available_mail_servers(rdb: Any, current_node_id: int) -> list[dict[str, Any]]:
    """List coupled-candidate `ns8-mail` instances with `same_node` and auto-discovered `rspamd_url`."""
    imap_providers = agent.list_service_providers(rdb, "imap", "tcp") or []
    sub_providers = agent.list_service_providers(rdb, "submission", "tcp") or []
    sub_by_uuid = {
        str(p.get("module_uuid", "")): p
        for p in sub_providers
        if isinstance(p, dict) and p.get("module_uuid")
    }

    servers: list[dict[str, Any]] = []
    seen_uuids: set[str] = set()
    for provider in imap_providers:
        if not isinstance(provider, dict):
            continue
        module_uuid = str(provider.get("module_uuid", "")).strip()
        module_id = str(provider.get("module_id", "")).strip()
        if not module_uuid or module_uuid in seen_uuids:
            continue
        seen_uuids.add(module_uuid)

        sub_info = sub_by_uuid.get(module_uuid, {})
        mail_hostname = str(
            provider.get("mail_hostname")
            or provider.get("hostname")
            or sub_info.get("mail_hostname")
            or sub_info.get("hostname")
            or provider.get("host")
            or module_id
        ).strip()
        user_domain = str(provider.get("user_domain", "")).strip()
        try:
            node_id = int(provider.get("node", 0) or 0)
        except (TypeError, ValueError):
            node_id = 0
        same_node = bool(current_node_id and node_id == current_node_id)
        rspamd_url = discover_mail_rspamd_url(module_id, mail_hostname)

        servers.append(
            {
                "module_id": module_id,
                "module_uuid": module_uuid,
                "mail_hostname": mail_hostname,
                "user_domain": user_domain,
                "node_id": node_id,
                "same_node": same_node,
                "rspamd_url": rspamd_url,
            }
        )

    servers.sort(key=lambda s: (not s["same_node"], s["module_id"]))
    return servers


def _extract_rid(raw_sid: Any, raw_pgt: Any, group_name: str) -> int | None:
    if raw_pgt is not None:
        val = raw_pgt[0] if isinstance(raw_pgt, (list, tuple)) and raw_pgt else raw_pgt
        try:
            return int(val)
        except (TypeError, ValueError):
            pass

    sid_val = raw_sid[0] if isinstance(raw_sid, (list, tuple)) and raw_sid else raw_sid
    if isinstance(sid_val, bytes) and len(sid_val) >= 8:
        return int.from_bytes(sid_val[-4:], "little")
    if isinstance(sid_val, str) and sid_val.startswith("S-1-5-") and "-" in sid_val:
        try:
            return int(sid_val.rsplit("-", 1)[-1])
        except ValueError:
            pass

    return WELL_KNOWN_AD_GROUP_RIDS.get(group_name.strip().lower())


def _first_str(attr_val: Any) -> str:
    if attr_val is None:
        return ""
    if isinstance(attr_val, (list, tuple)):
        return str(attr_val[0]).strip() if attr_val else ""
    if isinstance(attr_val, bytes):
        return attr_val.decode("utf-8", errors="replace").strip()
    return str(attr_val).strip()


def _as_str_list(attr_val: Any) -> list[str]:
    if not attr_val:
        return []
    if isinstance(attr_val, (list, tuple, set)):
        out: list[str] = []
        for item in attr_val:
            s = _first_str(item)
            if s:
                out.append(s)
        return out
    s = _first_str(attr_val)
    return [s] if s else []


def _query_groups_via_ldap3(domain_info: dict[str, Any]) -> list[dict[str, Any]] | None:
    if Server is None or Connection is None:
        return None

    host = str(domain_info.get("host") or "127.0.0.1").strip()
    port = int(domain_info.get("port") or 389)
    base_dn = str(domain_info.get("base_dn") or "").strip()
    bind_dn = str(domain_info.get("bind_dn") or "").strip()
    bind_password = str(domain_info.get("bind_password") or "")
    use_tls = str(domain_info.get("tls") or "0").strip().lower() in ("1", "true", "yes")
    if not base_dn:
        return None

    server = Server(host, port=port, use_ssl=use_tls, get_info=NONE, connect_timeout=5)
    conn = Connection(
        server,
        user=bind_dn if bind_dn else None,
        password=bind_password if bind_dn else None,
        auto_bind=True,
        receive_timeout=10,
    )
    try:
        # 1. Fetch users to map DN/uid -> user and count AD primaryGroupID & RFC2307 primary gidNumber
        primary_rid_counts: dict[int, set[str]] = {}
        primary_gid_counts: dict[str, set[str]] = {}
        user_dns_lower: set[str] = set()

        conn.search(
            search_base=base_dn,
            search_filter="(&(|(objectClass=person)(objectClass=posixAccount)(objectClass=inetOrgPerson)(objectClass=user))(!(objectClass=computer)))",
            attributes=["uid", "sAMAccountName", "primaryGroupID", "gidNumber"],
        )
        for entry in conn.response or []:
            if entry.get("type") != "searchResEntry":
                continue
            dn_low = str(entry.get("dn") or "").strip().lower()
            attrs = entry.get("attributes") or {}
            uid_key = (
                _first_str(attrs.get("sAMAccountName"))
                or _first_str(attrs.get("uid"))
                or dn_low
            ).lower()
            if dn_low:
                user_dns_lower.add(dn_low)

            pgid_raw = _first_str(attrs.get("primaryGroupID"))
            if pgid_raw:
                try:
                    primary_rid_counts.setdefault(int(pgid_raw), set()).add(uid_key)
                except ValueError:
                    pass

            gid_raw = _first_str(attrs.get("gidNumber"))
            if gid_raw:
                primary_gid_counts.setdefault(gid_raw, set()).add(uid_key)

        # 2. Fetch all groups
        conn.search(
            search_base=base_dn,
            search_filter="(|(objectClass=group)(objectClass=posixGroup)(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))",
            attributes=[
                "cn",
                "sAMAccountName",
                "description",
                "member",
                "uniqueMember",
                "memberUid",
                "gidNumber",
                "objectSid",
                "primaryGroupToken",
            ],
        )

        # Build group structure for direct + nested member counting
        group_entries: dict[str, dict[str, Any]] = {}
        for entry in conn.response or []:
            if entry.get("type") != "searchResEntry":
                continue
            dn = str(entry.get("dn") or "").strip()
            dn_low = dn.lower()
            raw_attrs = entry.get("raw_attributes") or {}
            attrs = entry.get("attributes") or {}
            g_name = _first_str(attrs.get("sAMAccountName")) or _first_str(attrs.get("cn"))
            if not g_name:
                continue

            desc = _first_str(attrs.get("description"))
            rid = _extract_rid(
                raw_attrs.get("objectSid") or attrs.get("objectSid"),
                attrs.get("primaryGroupToken"),
                g_name,
            )
            gid_num = _first_str(attrs.get("gidNumber"))
            dn_members = [
                m.lower()
                for m in (_as_str_list(attrs.get("member")) + _as_str_list(attrs.get("uniqueMember")))
            ]
            uid_members = [u.lower() for u in _as_str_list(attrs.get("memberUid"))]

            group_entries[dn_low or g_name.lower()] = {
                "name": g_name,
                "description": desc,
                "rid": rid,
                "gid_num": gid_num,
                "dn_members": dn_members,
                "uid_members": uid_members,
            }

        def resolve_members(g_key: str, visited: set[str]) -> set[str]:
            if g_key in visited or g_key not in group_entries:
                return set()
            visited.add(g_key)
            info = group_entries[g_key]
            members: set[str] = set(info["uid_members"])
            if info["rid"] is not None and info["rid"] in primary_rid_counts:
                members.update(primary_rid_counts[info["rid"]])
            if info["gid_num"] and info["gid_num"] in primary_gid_counts:
                members.update(primary_gid_counts[info["gid_num"]])

            for member_dn in info["dn_members"]:
                if member_dn in group_entries:
                    members.update(resolve_members(member_dn, visited))
                elif not user_dns_lower or member_dn in user_dns_lower:
                    members.add(member_dn)
            return members

        results: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for g_key, info in group_entries.items():
            g_name = info["name"]
            if g_name.lower() in seen_names:
                continue
            seen_names.add(g_name.lower())
            resolved = resolve_members(g_key, set())
            results.append(
                {
                    "group": g_name,
                    "description": info["description"],
                    "member_count": len(resolved),
                    "is_system": is_system_group(g_name),
                }
            )

        results.sort(key=lambda item: (bool(item["is_system"]), item["group"].lower()))
        return results
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


def discover_domain_groups_and_schema(
    user_domain: str,
    mail_module_id: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Return `(ldap_schema, available_groups)` for `user_domain` even before initial configure-module."""
    ldap_schema = ""
    domain_info: dict[str, Any] | None = None

    if user_domain:
        try:
            domain_info = Ldapproxy().get_domain(user_domain)
        except Exception:
            domain_info = None

    if domain_info:
        ldap_schema = str(domain_info.get("schema") or "rfc2307").strip().lower()
        try:
            rich_groups = _query_groups_via_ldap3(domain_info)
            if rich_groups is not None:
                return ldap_schema, rich_groups
        except Exception as exc:
            print(f"Warning: Direct ldap3 group discovery fallback triggered: {exc}", file=sys.stderr)

        if Ldapclient is not None:
            try:
                raw_groups = Ldapclient.factory(**domain_info).list_groups() or []
                groups_out: list[dict[str, Any]] = []
                seen: set[str] = set()
                for item in raw_groups:
                    if not isinstance(item, dict):
                        continue
                    g_name = str(item.get("group") or item.get("cn") or "").strip()
                    if not g_name or g_name.lower() in seen:
                        continue
                    seen.add(g_name.lower())
                    users_val = item.get("users")
                    m_count = len(users_val) if isinstance(users_val, list) else None
                    groups_out.append(
                        {
                            "group": g_name,
                            "description": str(item.get("description") or "").strip(),
                            "member_count": m_count,
                            "is_system": is_system_group(g_name),
                        }
                    )
                groups_out.sort(key=lambda g: (bool(g["is_system"]), g["group"].lower()))
                return ldap_schema, groups_out
            except Exception as exc:
                print(f"Warning: Ldapclient.list_groups failed: {exc}", file=sys.stderr)

    # Fallback when ldapproxy is not yet bound on this node for a cross-node mailX instance:
    # query `module/<mail_id>/list-acl-subjects` (authorized via `mail@any:mailadm` "list-*").
    if mail_module_id:
        try:
            acl_res = agent.tasks.run(
                agent_id=f"module/{mail_module_id}",
                action="list-acl-subjects",
            )
            if acl_res.get("exit_code") == 0:
                payload = _parse_task_output(acl_res)
                raw_items = (
                    payload.get("subjects", [])
                    if isinstance(payload, dict)
                    else (payload if isinstance(payload, list) else [])
                )
                fallback_groups: list[dict[str, Any]] = []
                seen_fb: set[str] = set()
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    dtype = str(item.get("dtype") or item.get("type") or "").strip().lower()
                    if dtype != "group":
                        continue
                    g_name = str(item.get("name") or item.get("group") or "").strip()
                    if not g_name or g_name.lower() in seen_fb:
                        continue
                    seen_fb.add(g_name.lower())
                    desc = str(item.get("ui_name") or item.get("description") or "").strip()
                    if desc.lower() == g_name.lower():
                        desc = ""
                    fallback_groups.append(
                        {
                            "group": g_name,
                            "description": desc,
                            "member_count": None,
                            "is_system": is_system_group(g_name),
                        }
                    )
                fallback_groups.sort(
                    key=lambda g: (bool(g["is_system"]), g["group"].lower())
                )
                return ldap_schema, fallback_groups
        except Exception as exc:
            print(
                f"Warning: list-acl-subjects fallback on module/{mail_module_id} skipped: {exc}",
                file=sys.stderr,
            )

    return ldap_schema, []
