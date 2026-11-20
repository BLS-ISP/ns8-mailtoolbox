"""Sieve script generator, sanitizer, and read-only SOGo script parser.

Targets Dovecot 2.4 (Pigeonhole) ManageSieve and enforces strict coexistence
with SOGo's ~/sieve/sogo.sieve and nightly `sogo-tool update-autoreply` cronjob.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any

TOOLBOX_SCRIPT_NAME = "toolbox-vacation"
SOGO_SCRIPT_NAME = "sogo"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL_RE = re.compile(r"^[^@\s\"\\;<>]+@[^@\s\"\\;<>]+\.[^@\s\"\\;<>]+$")


def validate_iso_date(value: str | None) -> str:
    """Validate optional YYYY-MM-DD date string; return '' if empty."""
    if value is None:
        return ""
    cleaned = value.strip()
    if not cleaned:
        return ""
    if not _ISO_DATE_RE.match(cleaned):
        raise ValueError(f"Invalid date format {value!r}; expected YYYY-MM-DD")
    try:
        date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid calendar date {value!r}") from exc
    return cleaned


def validate_email_address(value: str) -> str:
    """Validate an email address against control characters and Sieve injection."""
    cleaned = (value or "").strip()
    if not cleaned or any(ch in cleaned for ch in ("\x00", "\r", "\n")):
        raise ValueError("Invalid email address")
    if not _EMAIL_RE.match(cleaned):
        raise ValueError(f"Invalid email address: {value!r}")
    return cleaned


def escape_sieve_quoted(value: str, *, single_line: bool = False) -> str:
    """Escape a string as an RFC 5228 quoted string literal."""
    cleaned = (value or "").replace("\x00", "")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    if single_line:
        cleaned = cleaned.replace("\n", " ").replace("\t", " ").strip()
    escaped = cleaned.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def compute_active_now(
    *,
    enabled: bool,
    start_date: str | None,
    end_date: str | None,
    today: date | None = None,
) -> bool:
    """Determine whether a vacation rule is currently active on `today`."""
    if not enabled:
        return False
    ref_day = today or date.today()
    s_date = validate_iso_date(start_date)
    e_date = validate_iso_date(end_date)
    if s_date and ref_day < date.fromisoformat(s_date):
        return False
    if e_date and ref_day > date.fromisoformat(e_date):
        return False
    return True


def tokenize_sieve(script: str) -> list[tuple[str, str]]:
    """Tokenize a Sieve script per RFC 5228 (comments, quoted/text: strings, tags, idents)."""
    tokens: list[tuple[str, str]] = []
    n = len(script)
    i = 0

    while i < n:
        ch = script[i]

        # Whitespace
        if ch in (" ", "\t", "\r", "\n"):
            i += 1
            continue

        # Hash line comment: # ... \n
        if ch == "#":
            newline_pos = script.find("\n", i)
            i = n if newline_pos == -1 else newline_pos + 1
            continue

        # Bracket comment: /* ... */
        if ch == "/" and i + 1 < n and script[i + 1] == "*":
            end_pos = script.find("*/", i + 2)
            i = n if end_pos == -1 else end_pos + 2
            continue

        # Quoted string: "..."
        if ch == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                c = script[i]
                if c == "\\" and i + 1 < n:
                    buf.append(script[i + 1])
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                buf.append(c)
                i += 1
            tokens.append(("STRING", "".join(buf)))
            continue

        # Punctuation
        if ch in "[](){},;":
            tokens.append(("PUNCT", ch))
            i += 1
            continue

        # Tag: :identifier
        if ch == ":":
            j = i + 1
            while j < n and (script[j].isalnum() or script[j] in ("_", "-")):
                j += 1
            tokens.append(("TAG", script[i:j].lower()))
            i = j
            continue

        # Identifier or `text:` multi-line literal
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (script[j].isalnum() or script[j] in ("_", "-")):
                j += 1
            word = script[i:j]
            word_lower = word.lower()

            if word_lower == "text" and j < n and script[j] == ":":
                # RFC 5228 multi-line text: literal up to a line with single '.'
                k = j + 1
                while k < n and script[k] in (" ", "\t"):
                    k += 1
                if k < n and script[k] == "#":
                    nl = script.find("\n", k)
                    k = n if nl == -1 else nl + 1
                elif k < n and script[k] == "\r":
                    k += 2 if (k + 1 < n and script[k + 1] == "\n") else 1
                elif k < n and script[k] == "\n":
                    k += 1

                lines: list[str] = []
                while k < n:
                    nl = script.find("\n", k)
                    if nl == -1:
                        raw_line = script[k:]
                        k = n
                    else:
                        raw_line = script[k:nl]
                        k = nl + 1
                    line_clean = raw_line.rstrip("\r")
                    if line_clean == ".":
                        break
                    if line_clean.startswith(".."):
                        line_clean = line_clean[1:]
                    lines.append(line_clean)
                tokens.append(("STRING", "\n".join(lines)))
                i = k
                continue

            tokens.append(("IDENT", word_lower))
            i = j
            continue

        # Numbers / other tokens (e.g. 1, 7, 100K)
        j = i + 1
        while j < n and not script[j].isspace() and script[j] not in '[](){},;"#':
            j += 1
        tokens.append(("OTHER", script[i:j]))
        i = j

    return tokens


def default_sogo_status() -> dict[str, Any]:
    return {
        "has_sogo_script": False,
        "is_active": False,
        "vacation_enabled": False,
        "forward_enabled": False,
        "forward_address": "",
    }


def parse_sogo_script(
    script: str | None,
    *,
    has_sogo_script: bool,
    is_active: bool,
) -> dict[str, Any]:
    """Read-only inspection of SOGo's `sogo.sieve` script."""
    status = {
        "has_sogo_script": bool(has_sogo_script),
        "is_active": bool(is_active),
        "vacation_enabled": False,
        "forward_enabled": False,
        "forward_address": "",
    }
    if not has_sogo_script or not script:
        return status

    tokens = tokenize_sieve(script)
    num_tokens = len(tokens)
    idx = 0
    while idx < num_tokens:
        tok_type, tok_val = tokens[idx]
        if tok_type == "IDENT" and tok_val == "vacation":
            status["vacation_enabled"] = True
        elif tok_type == "IDENT" and tok_val == "redirect":
            status["forward_enabled"] = True
            lookahead = idx + 1
            while lookahead < num_tokens:
                l_type, l_val = tokens[lookahead]
                if l_type == "PUNCT" and l_val == ";":
                    break
                if l_type == "STRING":
                    if not status["forward_address"]:
                        status["forward_address"] = l_val.strip()
                    break
                lookahead += 1
        idx += 1

    return status


def deduplicate_addresses(addresses: list[str]) -> list[str]:
    """Deduplicate email addresses case-insensitively while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for addr in addresses:
        cleaned = (addr or "").strip()
        if not cleaned or "@" not in cleaned:
            continue
        if any(ch in cleaned for ch in ("\x00", "\r", "\n", '"', "\\", ";")):
            continue
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def generate_toolbox_sieve(
    *,
    addresses: list[str],
    subject: str,
    body: str,
    start_date: str | None = "",
    end_date: str | None = "",
    forward_enabled: bool = False,
    forward_address: str = "",
    keep_copy: bool = True,
    has_sogo_script: bool = False,
    sogo_vacation_enabled: bool = False,
) -> str:
    """Generate a Dovecot 2.4 compatible `toolbox-vacation` Sieve script."""
    s_date = validate_iso_date(start_date)
    e_date = validate_iso_date(end_date)
    if s_date and e_date and e_date < s_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    clean_addresses = deduplicate_addresses(addresses)
    has_vacation = bool(body and body.strip())
    has_dates = bool(s_date or e_date)
    has_forward = bool(forward_enabled and forward_address and forward_address.strip())
    clean_forward = validate_email_address(forward_address) if has_forward else ""

    if not has_vacation and not has_forward:
        raise ValueError("Either vacation body or forwarding must be provided when enabled")

    # Include personal "sogo" script ONLY when it exists and does NOT have its own vacation
    # statement, preventing double auto-replies while preserving SOGo folder filters.
    include_sogo = bool(has_sogo_script and not sogo_vacation_enabled)

    requires: list[str] = []
    if has_vacation:
        requires.append("vacation")
    if has_dates:
        requires.extend(["date", "relational"])
    if has_forward and keep_copy:
        requires.append("copy")
    if include_sogo:
        requires.append("include")

    lines: list[str] = ["# Managed by NS8 Mail Toolbox - DO NOT EDIT MANUALLY"]
    if requires:
        req_items = ", ".join(f'"{ext}"' for ext in requires)
        lines.append(f"require [{req_items}];")
        lines.append("")

    rule_statements: list[str] = []
    if has_vacation:
        vac_parts = ["vacation :days 1"]
        if clean_addresses:
            addr_list = ", ".join(
                escape_sieve_quoted(addr, single_line=True) for addr in clean_addresses
            )
            vac_parts.append(f":addresses [{addr_list}]")
        if subject and subject.strip():
            vac_parts.append(f":subject {escape_sieve_quoted(subject, single_line=True)}")
        vac_parts.append(f"{escape_sieve_quoted(body, single_line=False)};")
        rule_statements.append(" ".join(vac_parts))

    if has_forward:
        escaped_fwd = escape_sieve_quoted(clean_forward, single_line=True)
        if keep_copy:
            rule_statements.append(f"redirect :copy {escaped_fwd};")
        else:
            rule_statements.append(f"redirect {escaped_fwd};")
            rule_statements.append("stop;")

    if has_dates:
        if s_date and e_date:
            cond = (
                f'allof (currentdate :value "ge" "date" "{s_date}", '
                f'currentdate :value "le" "date" "{e_date}")'
            )
        elif s_date:
            cond = f'currentdate :value "ge" "date" "{s_date}"'
        else:
            cond = f'currentdate :value "le" "date" "{e_date}"'

        lines.append(f"if {cond} {{")
        for stmt in rule_statements:
            stmt_lines = stmt.split("\n")
            lines.append(f"    {stmt_lines[0]}")
            for extra in stmt_lines[1:]:
                lines.append(extra)
        lines.append("}")
    else:
        lines.extend(rule_statements)

    if include_sogo:
        lines.append("")
        lines.append('include :personal "sogo";')

    return "\n".join(lines) + "\n"
