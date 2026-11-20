"""Phase 2 corporate email signature template renderer, selector, and MIME rewriter.

Supports dynamic AD/LDAP placeholders, conditional blocks (`{{#mobile}}...{{/mobile}}`),
smart empty-line/element stripping when AD attributes are empty, HTML->Plaintext generation,
reply-chain deduplication (`append_once` / `first_only` / `always`), `below_reply` vs `bottom`
placement, and attachment-safe multipart MIME rewriting before Rspamd DKIM signing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import email
from email.message import EmailMessage
import email.policy
from email.utils import parseaddr
import html
import re
from typing import Any

from app.backend.config import get_settings
from app.backend.db import list_signature_rules
from app.backend.ldap_client import get_directory_user_with_groups

SIGNATURE_HTML_MARKER = "<!-- ns8-toolbox-sig -->"

_COND_BLOCK_RE = re.compile(r"\{\{#\s*([a-zA-Z0-9_]+)\s*\}\}(.*?)\{\{/\s*\1\s*\}\}", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# Leaf HTML containers (no nested block tags inside) for smart empty-element removal
_LEAF_TR_RE = re.compile(r"[ \t]*<tr\b[^>]*>(?:(?!</?tr\b).)*?</tr>[ \t]*\r?\n?", re.IGNORECASE | re.DOTALL)
_LEAF_LI_RE = re.compile(r"[ \t]*<li\b[^>]*>(?:(?!</?li\b).)*?</li>[ \t]*\r?\n?", re.IGNORECASE | re.DOTALL)
_LEAF_BLOCK_RE = re.compile(
    r"[ \t]*<(p|div)\b[^>]*>(?:(?!</?(?:p|div|table|tr|ul|ol)\b).)*?</\1>[ \t]*\r?\n?",
    re.IGNORECASE | re.DOTALL,
)

# <br>-terminated segment within a container (e.g. `Mobil: {{mobile}}<br>\n`)
_BR_SEGMENT_RE = re.compile(
    r"(?:^|(?<=<br>)|(?<=<br/>)|(?<=<br />)|(?<=\n))[ \t]*([^<\r\n]*?\{\{[^}]+\}\}[^<\r\n]*?)<br\s*/?>[ \t]*\r?\n?",
    re.IGNORECASE,
)
# Trailing segment after a <br> right before closing </p>, </div>, or </td>
_TRAILING_BR_SEGMENT_RE = re.compile(
    r"<br\s*/?>[ \t]*\r?\n?[ \t]*([^<\r\n]*?\{\{[^}]+\}\}[^<\r\n]*?)(?=[ \t]*\r?\n?[ \t]*</(?:p|div|td|li)\b)",
    re.IGNORECASE,
)

_HTML_QUOTE_MARKERS_RE = re.compile(
    r"(<div\b[^>]*\bclass\s*=\s*[\"'][^\"']*(?:gmail_quote|moz-cite-prefix)[^\"']*[\"']"
    r"|<div\b[^>]*\bid\s*=\s*[\"'](?:appendonsend|divRplyFwdMsg)[\"']"
    r"|<blockquote\b)",
    re.IGNORECASE,
)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)

_PLAIN_QUOTE_LINE_RE = re.compile(
    r"^(?:On .+ wrote:|Am .+ schrieb .+:|\-{3,}\s*(?:Original Message|Ursprüngliche Nachricht)\s*\-{3,})\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class SignatureRewriteResult:
    signed: bool
    rule_name: str = ""
    new_body_bytes: bytes = b""
    changed_headers: dict[str, str] = field(default_factory=dict)
    added_headers: dict[str, str] = field(default_factory=dict)
    skip_reason: str = ""


def resolve_sender_attributes(
    auth_user: str,
    sender_email: str = "",
) -> tuple[dict[str, str], list[str], str]:
    """Fetch LDAP/AD user attributes, group memberships, and sender domain."""
    settings = get_settings()
    clean_sender = parseaddr(sender_email)[1].strip() if sender_email else ""
    if not clean_sender and "@" in (sender_email or ""):
        clean_sender = sender_email.strip().strip("<>")

    du = get_directory_user_with_groups(auth_user, settings.ldap)
    if du is None and clean_sender:
        du = get_directory_user_with_groups(clean_sender, settings.ldap)

    short_user = (auth_user or "").split("@", 1)[0].strip()
    if du is not None:
        mail_val = clean_sender or du.mail or (
            f"{du.user}@{settings.ldap.domain}" if settings.ldap.domain else ""
        )
        attrs = {
            "givenName": du.given_name or "",
            "sn": du.sn or "",
            "displayName": du.display_name or du.user,
            "mail": mail_val,
            "telephoneNumber": du.telephone_number or "",
            "mobile": du.mobile or "",
            "title": du.title or "",
            "department": du.department or "",
            "company": du.company or "",
        }
        groups = list(du.groups)
    else:
        mail_val = clean_sender or (
            auth_user if "@" in (auth_user or "") else (
                f"{short_user}@{settings.ldap.domain}" if (short_user and settings.ldap.domain) else ""
            )
        )
        attrs = {
            "givenName": "",
            "sn": "",
            "displayName": short_user or mail_val,
            "mail": mail_val,
            "telephoneNumber": "",
            "mobile": "",
            "title": "",
            "department": "",
            "company": "",
        }
        groups = []

    domain = ""
    if "@" in attrs["mail"]:
        domain = attrs["mail"].split("@", 1)[1].strip().lower()
    elif settings.ldap.domain:
        domain = settings.ldap.domain.strip().lower()

    return attrs, groups, domain


def _lookup_attr(attrs: dict[str, str], key: str) -> str:
    if key in attrs:
        return str(attrs[key] or "")
    low = key.lower()
    for k, v in attrs.items():
        if k.lower() == low:
            return str(v or "")
    if low == "phone":
        return str(attrs.get("telephoneNumber") or "")
    return ""


def _all_placeholders_empty(fragment: str, attrs: dict[str, str]) -> bool:
    names = _PLACEHOLDER_RE.findall(fragment)
    if not names:
        return False
    return all(not _lookup_attr(attrs, name).strip() for name in names)


def _apply_conditionals(template: str, attrs: dict[str, str]) -> str:
    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        inner = match.group(2)
        if _lookup_attr(attrs, key).strip():
            return inner
        return ""

    prev = None
    cur = template
    while prev != cur:
        prev = cur
        cur = _COND_BLOCK_RE.sub(_repl, cur)
    return cur


def render_signature_html(html_template: str, attrs: dict[str, str]) -> str:
    """Render HTML signature with conditionals, smart empty-line/element stripping, and escaping."""
    if not html_template or not html_template.strip():
        return ""

    working = _apply_conditionals(html_template, attrs)

    # 1. Strip <br>-delimited lines whose placeholders are all empty (e.g. `Mobil: {{mobile}}<br>`)
    def _strip_br_seg(m: re.Match[str]) -> str:
        if _all_placeholders_empty(m.group(1), attrs):
            return ""
        return m.group(0)

    working = _BR_SEGMENT_RE.sub(_strip_br_seg, working)
    working = _TRAILING_BR_SEGMENT_RE.sub(_strip_br_seg, working)

    # 2. Strip leaf <tr>, <li>, <p>, <div> blocks whose placeholders are all empty
    def _strip_leaf(m: re.Match[str]) -> str:
        if _all_placeholders_empty(m.group(0), attrs):
            return ""
        return m.group(0)

    working = _LEAF_TR_RE.sub(_strip_leaf, working)
    working = _LEAF_LI_RE.sub(_strip_leaf, working)
    working = _LEAF_BLOCK_RE.sub(_strip_leaf, working)

    # 3. Strip standalone text lines inside HTML that have no structural tags and only empty placeholders
    kept_lines: list[str] = []
    for line in working.splitlines():
        if "<" not in line and ">" not in line and _all_placeholders_empty(line, attrs):
            continue
        kept_lines.append(line)
    working = "\n".join(kept_lines)

    # 4. Substitute remaining placeholders with HTML-escaped attribute values
    def _sub_escaped(m: re.Match[str]) -> str:
        val = _lookup_attr(attrs, m.group(1))
        return html.escape(val, quote=True)

    return _PLACEHOLDER_RE.sub(_sub_escaped, working).strip()


def html_to_plaintext(rendered_html: str) -> str:
    """Convert rendered signature HTML into clean, readable plaintext."""
    if not rendered_html or not rendered_html.strip():
        return ""

    text = re.sub(r"<!--.*?-->", "", rendered_html, flags=re.DOTALL)
    # Preserve non-mailto links where href differs from visible label
    def _link_repl(m: re.Match[str]) -> str:
        href = html.unescape(m.group(1)).strip()
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        label = html.unescape(label)
        if href.lower().startswith("mailto:"):
            return label or href[7:]
        if href and href not in label:
            return f"{label} ({href})" if label else href
        return label

    text = re.sub(
        r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        _link_repl,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|tr|li|h[1-6])\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:td|th)\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = [ln.strip() for ln in text.splitlines()]
    # Collapse consecutive blank lines
    cleaned: list[str] = []
    prev_blank = True
    for ln in lines:
        if not ln:
            if not prev_blank:
                cleaned.append("")
            prev_blank = True
        else:
            cleaned.append(ln)
            prev_blank = False
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned)


def render_signature_text(
    text_template: str,
    attrs: dict[str, str],
    *,
    rendered_html_fallback: str = "",
    auto_plain_from_html: bool = False,
) -> str:
    """Render Plaintext signature with conditionals, smart empty-line stripping, or HTML conversion."""
    if auto_plain_from_html or not (text_template or "").strip():
        return html_to_plaintext(rendered_html_fallback)

    # Track lines that originally held conditional blocks so empty conditionals don't leave blank lines
    raw_lines = text_template.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out_lines: list[str] = []
    for raw_line in raw_lines:
        had_cond = bool(_COND_BLOCK_RE.search(raw_line))
        after_cond = _apply_conditionals(raw_line, attrs)
        if had_cond and not after_cond.strip():
            continue
        if _all_placeholders_empty(after_cond, attrs):
            continue
        rendered_line = _PLACEHOLDER_RE.sub(
            lambda m: _lookup_attr(attrs, m.group(1)),
            after_cond,
        ).rstrip()
        out_lines.append(rendered_line)

    joined = "\n".join(out_lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def select_signature_rule(
    rules: list[dict[str, Any]],
    *,
    user_groups: list[str],
    sender_domain: str,
) -> dict[str, Any] | None:
    """Pick highest-precedence enabled rule (`group` > `domain` > `default`, then `priority` ASC)."""
    groups_lower = {g.strip().lower() for g in user_groups if g and g.strip()}
    dom_lower = (sender_domain or "").strip().lstrip("@").lower()

    candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    for r in rules:
        if not r.get("enabled"):
            continue
        scope_type = str(r.get("scope_type") or "default").strip().lower()
        scope_val = str(r.get("scope_value") or "*").strip().lower()
        prio = int(r.get("priority", 10))
        rid = int(r.get("id", 0))

        if scope_type == "group":
            if scope_val and scope_val in groups_lower:
                candidates.append((0, prio, rid, r))
        elif scope_type == "domain":
            if dom_lower and scope_val.lstrip("@") == dom_lower:
                candidates.append((1, prio, rid, r))
        elif scope_type == "default":
            if scope_val in ("*", "", "all"):
                candidates.append((2, prio, rid, r))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _plain_signature_already_present(body_text: str, sig_text: str) -> bool:
    sig_lines = [
        re.sub(r"\s+", " ", ln.strip())
        for ln in sig_text.splitlines()
        if ln.strip() and ln.strip() != "--"
    ]
    if not sig_lines:
        return False
    norm_sig = "\n".join(sig_lines)
    # Normalize body lines stripping leading `>` quote markers for reply chains
    body_lines = [
        re.sub(r"\s+", " ", re.sub(r"^[>\s]+", "", ln).strip())
        for ln in body_text.splitlines()
    ]
    body_lines = [ln for ln in body_lines if ln and ln != "--"]
    norm_body = "\n".join(body_lines)
    return norm_sig in norm_body


def inject_html_signature(body_html: str, sig_html: str, *, placement: str = "below_reply") -> str:
    wrapped = f'\n{SIGNATURE_HTML_MARKER}<div class="ns8-toolbox-signature">{sig_html}</div>\n'
    if placement == "below_reply":
        m = _HTML_QUOTE_MARKERS_RE.search(body_html)
        if m:
            idx = m.start()
            return body_html[:idx] + wrapped + body_html[idx:]

    body_close = _BODY_CLOSE_RE.search(body_html)
    if body_close:
        idx = body_close.start()
        return body_html[:idx] + wrapped + body_html[idx:]
    return body_html + wrapped


def inject_plain_signature(body_text: str, sig_text: str, *, placement: str = "below_reply") -> str:
    clean_sig = sig_text.strip()
    if clean_sig.startswith("-- ") or clean_sig.startswith("--\n"):
        block = f"\n\n{clean_sig}\n"
    else:
        block = f"\n\n-- \n{clean_sig}\n"

    if placement == "below_reply":
        m = _PLAIN_QUOTE_LINE_RE.search(body_text)
        if m:
            idx = m.start()
            return body_text[:idx].rstrip() + block + "\n" + body_text[idx:]
    return body_text.rstrip() + block


def _find_body_parts(msg: EmailMessage) -> tuple[EmailMessage | None, EmailMessage | None]:
    """Locate the primary non-attachment text/plain and text/html parts."""
    plain_part: EmailMessage | None = None
    html_part: EmailMessage | None = None

    if not msg.is_multipart():
        ctype = msg.get_content_type()
        disp = (msg.get_content_disposition() or "").lower()
        if disp != "attachment":
            if ctype == "text/plain":
                plain_part = msg
            elif ctype == "text/html":
                html_part = msg
        return plain_part, html_part

    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        if disp == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain_part is None:
            plain_part = part
        elif ctype == "text/html" and html_part is None:
            html_part = part

    return plain_part, html_part


def _replace_text_part_content(part: EmailMessage, new_text: str, subtype: str) -> None:
    """Update a text/plain or text/html part's text payload while keeping non-MIME headers."""
    saved_id = part.get("Content-ID")
    saved_disp = part.get("Content-Disposition")
    part.set_content(new_text, subtype=subtype, charset="utf-8", cte="quoted-printable")
    if saved_id and not part.get("Content-ID"):
        part["Content-ID"] = saved_id
    if saved_disp and not part.get("Content-Disposition"):
        part["Content-Disposition"] = saved_disp


def apply_signature_to_raw_message(
    raw_message: bytes,
    *,
    rule: dict[str, Any],
    attrs: dict[str, str],
) -> SignatureRewriteResult:
    """Parse RFC 5322 message bytes, inject rendered signature, and return updated body/headers."""
    msg = email.message_from_bytes(raw_message, policy=email.policy.SMTP)
    orig_top_headers = {k.lower(): (k, str(v)) for k, v in msg.items()}

    reply_mode = str(rule.get("reply_mode") or "append_once").strip().lower()
    placement = str(rule.get("placement") or "below_reply").strip().lower()

    if reply_mode == "first_only":
        if msg.get("In-Reply-To") or msg.get("References"):
            return SignatureRewriteResult(signed=False, skip_reason="first_only_reply")

    sig_html = render_signature_html(str(rule.get("html_template") or ""), attrs)
    sig_text = render_signature_text(
        str(rule.get("text_template") or ""),
        attrs,
        rendered_html_fallback=sig_html,
        auto_plain_from_html=bool(rule.get("auto_plain_from_html", True)),
    )
    if not sig_html and not sig_text:
        return SignatureRewriteResult(signed=False, skip_reason="empty_rendered_signature")

    plain_part, html_part = _find_body_parts(msg)
    if plain_part is None and html_part is None:
        return SignatureRewriteResult(signed=False, skip_reason="no_body_part")

    # Anti-duplicate inspection for `append_once`
    if reply_mode == "append_once":
        if html_part is not None:
            cur_html = str(html_part.get_content())
            if SIGNATURE_HTML_MARKER in cur_html or "ns8-toolbox-signature" in cur_html:
                return SignatureRewriteResult(signed=False, skip_reason="already_signed_html")
        if plain_part is not None and sig_text:
            cur_plain = str(plain_part.get_content())
            if html_part is None and _plain_signature_already_present(cur_plain, sig_text):
                return SignatureRewriteResult(signed=False, skip_reason="already_signed_plain")

    modified = False
    if html_part is not None and sig_html:
        cur_html = str(html_part.get_content())
        updated_html = inject_html_signature(cur_html, sig_html, placement=placement)
        _replace_text_part_content(html_part, updated_html, "html")
        modified = True

    if plain_part is not None and sig_text:
        cur_plain = str(plain_part.get_content())
        if reply_mode != "append_once" or not _plain_signature_already_present(cur_plain, sig_text):
            updated_plain = inject_plain_signature(cur_plain, sig_text, placement=placement)
            _replace_text_part_content(plain_part, updated_plain, "plain")
            modified = True

    if not modified:
        return SignatureRewriteResult(signed=False, skip_reason="unmodified")

    serialized = msg.as_bytes(policy=email.policy.SMTP)
    sep = b"\r\n\r\n"
    sep_idx = serialized.find(sep)
    if sep_idx == -1:
        sep = b"\n\n"
        sep_idx = serialized.find(sep)
    new_body = serialized[sep_idx + len(sep) :] if sep_idx != -1 else serialized

    changed_headers: dict[str, str] = {}
    added_headers: dict[str, str] = {}
    for hdr_name in ("Content-Type", "Content-Transfer-Encoding", "MIME-Version"):
        new_val = msg.get(hdr_name)
        if new_val is None:
            continue
        new_val_str = str(new_val).strip()
        low = hdr_name.lower()
        if low in orig_top_headers:
            if orig_top_headers[low][1].strip() != new_val_str:
                changed_headers[hdr_name] = new_val_str
        else:
            added_headers[hdr_name] = new_val_str

    return SignatureRewriteResult(
        signed=True,
        rule_name=str(rule.get("name") or ""),
        new_body_bytes=new_body,
        changed_headers=changed_headers,
        added_headers=added_headers,
    )


def process_outgoing_milter_message(
    *,
    auth_user: str,
    mail_from: str,
    raw_message: bytes,
) -> SignatureRewriteResult:
    """Resolve user LDAP attributes + matching signature rule and rewrite message."""
    settings = get_settings()
    if not settings.runtime.milter_enabled:
        return SignatureRewriteResult(signed=False, skip_reason="milter_disabled")

    rules = list_signature_rules(settings.sqlite_path)
    attrs, groups, domain = resolve_sender_attributes(auth_user, mail_from)
    rule = select_signature_rule(rules, user_groups=groups, sender_domain=domain)
    if rule is None:
        return SignatureRewriteResult(signed=False, skip_reason="no_matching_rule")

    return apply_signature_to_raw_message(raw_message, rule=rule, attrs=attrs)


def preview_signature_for_user(
    *,
    user: str,
    sender_email: str = "",
    html_template: str | None = None,
    text_template: str | None = None,
    auto_plain_from_html: bool = True,
) -> dict[str, Any]:
    """Render live preview of signature HTML and Plaintext for a mailbox user."""
    settings = get_settings()
    attrs, groups, domain = resolve_sender_attributes(user, sender_email)

    matched_rule_name = ""
    if html_template is None and text_template is None:
        rules = list_signature_rules(settings.sqlite_path)
        rule = select_signature_rule(rules, user_groups=groups, sender_domain=domain)
        if rule is not None:
            matched_rule_name = str(rule.get("name") or "")
            html_tpl = str(rule.get("html_template") or "")
            text_tpl = str(rule.get("text_template") or "")
            auto_plain = bool(rule.get("auto_plain_from_html", True))
        else:
            html_tpl = ""
            text_tpl = ""
            auto_plain = True
    else:
        rules = list_signature_rules(settings.sqlite_path)
        rule = select_signature_rule(rules, user_groups=groups, sender_domain=domain)
        matched_rule_name = str(rule["name"]) if rule else "Preview"
        html_tpl = html_template or ""
        text_tpl = text_template or ""
        auto_plain = auto_plain_from_html if not text_tpl.strip() else False

    rendered_html = render_signature_html(html_tpl, attrs)
    rendered_text = render_signature_text(
        text_tpl,
        attrs,
        rendered_html_fallback=rendered_html,
        auto_plain_from_html=auto_plain,
    )
    return {
        "matched_rule_name": matched_rule_name,
        "attributes": attrs,
        "rendered_html": rendered_html,
        "rendered_text": rendered_text,
    }
