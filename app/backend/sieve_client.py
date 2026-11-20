"""RFC 5804 ManageSieve client with Dovecot 2.4 Master-User SASL PLAIN authentication."""

from __future__ import annotations

import base64
from io import BufferedReader
import re
import socket
import ssl
from typing import Any

from app.backend.sieve_engine import SOGO_SCRIPT_NAME

_LITERAL_END_RE = re.compile(r"^(.*?)\{(\d+)\+?\}\s*$")
_STATUS_LINE_RE = re.compile(r"^(OK|NO|BYE)(?:\s+(.*))?$", re.IGNORECASE)
_QUOTED_LIST_RE = re.compile(r'^"((?:\\.|[^"\\])*)"(?:\s+(ACTIVE))?\s*$', re.IGNORECASE)


class ManageSieveError(Exception):
    """Base error for ManageSieve protocol or network failures."""


class ManageSieveConnectionError(ManageSieveError):
    """Connection or socket-level ManageSieve error."""


class ManageSieveAuthError(ManageSieveError):
    """SASL PLAIN authentication failure."""


class ManageSieveCommandError(ManageSieveError):
    """ManageSieve server returned NO or BYE for a command."""


def build_master_sasl_plain(target_user: str, master_user: str, master_password: str) -> str:
    """Build base64 SASL PLAIN payload for Dovecot Master-User (`\\x00user*vmail\\x00pass`)."""
    user = (target_user or "").strip()
    m_user = (master_user or "").strip()
    if not user or any(ch in user for ch in ("*", "\x00", "\r", "\n")):
        raise ValueError(f"Invalid target mailbox user: {target_user!r}")
    if not m_user or any(ch in m_user for ch in ("\x00", "\r", "\n")):
        raise ValueError("Invalid master user")
    if "\x00" in (master_password or ""):
        raise ValueError("Invalid master password")

    payload = f"\x00{user}*{m_user}\x00{master_password}".encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def _quote_sieve_arg(value: str) -> str:
    if any(ch in value for ch in ("\x00", "\r", "\n")):
        raise ValueError("Control characters forbidden in ManageSieve argument")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _unescape_quoted(value: str) -> str:
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "\\" and i + 1 < n:
            out.append(value[i + 1])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


class ManageSieveClient:
    """Synchronous RFC 5804 ManageSieve client for Dovecot Pigeonhole."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 4190,
        master_user: str = "vmail",
        master_password: str = "",
        target_user: str,
        starttls: bool = False,
        tls_verify: bool = False,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.master_user = master_user
        self.master_password = master_password
        self.target_user = target_user
        self.starttls = starttls
        self.tls_verify = tls_verify
        self.timeout = timeout

        self._sock: socket.socket | None = None
        self._reader: BufferedReader | None = None
        self.capabilities: dict[str, str] = {}

    def __enter__(self) -> ManageSieveClient:
        self.connect_and_login()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def connect_and_login(self) -> None:
        """Open TCP socket, negotiate optional STARTTLS, and authenticate as target*master."""
        if not self.host:
            raise ManageSieveConnectionError("MAIL_HOST is not configured")
        try:
            raw_sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            raw_sock.settimeout(self.timeout)
            self._sock = raw_sock
            self._reader = raw_sock.makefile("rb")
        except OSError as exc:
            raise ManageSieveConnectionError(
                f"Cannot connect to ManageSieve at {self.host}:{self.port}: {exc}"
            ) from exc

        try:
            status, items, msg = self._read_response()
            if status != "OK":
                raise ManageSieveConnectionError(f"Unexpected ManageSieve greeting: {status} {msg}")
            self._parse_capabilities(items)

            if self.starttls and "STARTTLS" in self.capabilities:
                self._send_raw(b"STARTTLS\r\n")
                st_status, _, st_msg = self._read_response()
                if st_status != "OK":
                    raise ManageSieveConnectionError(f"STARTTLS failed: {st_status} {st_msg}")
                if self._reader is not None:
                    self._reader.close()
                ctx = ssl.create_default_context()
                if not self.tls_verify:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                assert self._sock is not None
                tls_sock = ctx.wrap_socket(self._sock, server_hostname=self.host)
                tls_sock.settimeout(self.timeout)
                self._sock = tls_sock
                self._reader = tls_sock.makefile("rb")
                post_status, post_items, post_msg = self._read_response()
                if post_status != "OK":
                    raise ManageSieveConnectionError(
                        f"Post-STARTTLS banner failed: {post_status} {post_msg}"
                    )
                self._parse_capabilities(post_items)

            auth_b64 = build_master_sasl_plain(
                self.target_user, self.master_user, self.master_password
            )
            self._send_raw(f'AUTHENTICATE "PLAIN" "{auth_b64}"\r\n'.encode("ascii"))
            auth_status, auth_items, auth_msg = self._read_response()
            if auth_status != "OK":
                raise ManageSieveAuthError(
                    f"ManageSieve authentication failed for {self.target_user}*{self.master_user}: {auth_msg}"
                )
            if auth_items:
                self._parse_capabilities(auth_items)
        except Exception:
            self.close()
            raise

    def listscripts(self) -> tuple[list[str], str | None]:
        """Return (all_script_names, active_script_name_or_None)."""
        self._send_raw(b"LISTSCRIPTS\r\n")
        status, items, msg = self._read_response()
        if status != "OK":
            raise ManageSieveCommandError(f"LISTSCRIPTS failed: {status} {msg}")

        scripts: list[str] = []
        active: str | None = None
        for prefix, literal, suffix in items:
            if literal is not None:
                name = literal.decode("utf-8", errors="replace")
                scripts.append(name)
                if suffix.strip().upper() == "ACTIVE":
                    active = name
            else:
                m = _QUOTED_LIST_RE.match(prefix.strip())
                if m:
                    name = _unescape_quoted(m.group(1))
                    scripts.append(name)
                    if m.group(2) and m.group(2).upper() == "ACTIVE":
                        active = name
        return scripts, active

    def getscript(self, name: str) -> str:
        """Fetch the UTF-8 contents of a named Sieve script."""
        cmd = f"GETSCRIPT {_quote_sieve_arg(name)}\r\n".encode("utf-8")
        self._send_raw(cmd)
        status, items, msg = self._read_response()
        if status != "OK":
            raise ManageSieveCommandError(f"GETSCRIPT {name!r} failed: {status} {msg}")

        for prefix, literal, _ in items:
            if literal is not None:
                return literal.decode("utf-8", errors="replace")
            stripped = prefix.strip()
            if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
                return _unescape_quoted(stripped[1:-1])
        return ""

    def checkscript(self, script_content: str) -> None:
        """Validate Sieve script syntax on Dovecot without storing it."""
        payload = script_content.encode("utf-8")
        header = f"CHECKSCRIPT {{{len(payload)}+}}\r\n".encode("ascii")
        self._send_raw(header + payload + b"\r\n")
        status, _, msg = self._read_response()
        if status != "OK":
            raise ManageSieveCommandError(f"CHECKSCRIPT rejected script: {status} {msg}")

    def putscript(self, name: str, script_content: str) -> None:
        """Upload a Sieve script (strictly refuses overwriting SOGo's `sogo` script)."""
        if name.strip().lower() in (SOGO_SCRIPT_NAME, f"{SOGO_SCRIPT_NAME}.sieve"):
            raise ValueError("Refusing to overwrite SOGo's personal sogo.sieve script")
        payload = script_content.encode("utf-8")
        header = f"PUTSCRIPT {_quote_sieve_arg(name)} {{{len(payload)}+}}\r\n".encode("utf-8")
        self._send_raw(header + payload + b"\r\n")
        status, _, msg = self._read_response()
        if status != "OK":
            raise ManageSieveCommandError(f"PUTSCRIPT {name!r} failed: {status} {msg}")

    def setactive(self, name: str) -> None:
        """Activate `name` (or deactivate all scripts when `name == ''`)."""
        cmd = f"SETACTIVE {_quote_sieve_arg(name or '')}\r\n".encode("utf-8")
        self._send_raw(cmd)
        status, _, msg = self._read_response()
        if status != "OK":
            raise ManageSieveCommandError(f"SETACTIVE {name!r} failed: {status} {msg}")

    def deletescript(self, name: str) -> None:
        """Delete a non-active Sieve script (strictly refuses deleting SOGo's `sogo` script)."""
        if name.strip().lower() in (SOGO_SCRIPT_NAME, f"{SOGO_SCRIPT_NAME}.sieve"):
            raise ValueError("Refusing to delete SOGo's personal sogo.sieve script")
        cmd = f"DELETESCRIPT {_quote_sieve_arg(name)}\r\n".encode("utf-8")
        self._send_raw(cmd)
        status, _, msg = self._read_response()
        if status != "OK":
            raise ManageSieveCommandError(f"DELETESCRIPT {name!r} failed: {status} {msg}")

    def close(self) -> None:
        """Send LOGOUT best-effort and close underlying socket."""
        if self._sock is not None:
            try:
                self._sock.sendall(b"LOGOUT\r\n")
            except OSError:
                pass
        if self._reader is not None:
            try:
                self._reader.close()
            except OSError:
                pass
            self._reader = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send_raw(self, data: bytes) -> None:
        if self._sock is None:
            raise ManageSieveConnectionError("ManageSieve socket is not connected")
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise ManageSieveConnectionError(f"Socket send failed: {exc}") from exc

    def _read_exact(self, num_bytes: int) -> bytes:
        assert self._reader is not None
        chunks: list[bytes] = []
        remaining = num_bytes
        while remaining > 0:
            chunk = self._reader.read(remaining)
            if not chunk:
                raise ManageSieveConnectionError("Unexpected EOF while reading ManageSieve literal")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_response(self) -> tuple[str, list[tuple[str, bytes | None, str]], str]:
        """Read ManageSieve lines/literals until terminal OK, NO, or BYE."""
        if self._reader is None:
            raise ManageSieveConnectionError("ManageSieve reader is not initialized")

        items: list[tuple[str, bytes | None, str]] = []
        while True:
            try:
                raw_line = self._reader.readline()
            except OSError as exc:
                raise ManageSieveConnectionError(f"Socket read failed: {exc}") from exc
            if not raw_line:
                raise ManageSieveConnectionError("ManageSieve connection closed by server")

            line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
            lit_match = _LITERAL_END_RE.match(line)
            if lit_match:
                prefix = lit_match.group(1)
                byte_len = int(lit_match.group(2))
                literal_bytes = self._read_exact(byte_len)
                trailing_raw = self._reader.readline()
                trailing = trailing_raw.rstrip(b"\r\n").decode("utf-8", errors="replace")

                st_match = _STATUS_LINE_RE.match(prefix.strip())
                if st_match:
                    status = st_match.group(1).upper()
                    msg = literal_bytes.decode("utf-8", errors="replace")
                    return status, items, msg

                items.append((prefix, literal_bytes, trailing))
                continue

            st_match = _STATUS_LINE_RE.match(line)
            if st_match:
                status = st_match.group(1).upper()
                msg = (st_match.group(2) or "").strip()
                return status, items, msg

            items.append((line, None, ""))

    def _parse_capabilities(self, items: list[tuple[str, bytes | None, str]]) -> None:
        caps: dict[str, str] = {}
        for prefix, _, _ in items:
            parts = re.findall(r'"((?:\\.|[^"\\])*)"', prefix)
            if len(parts) >= 2:
                caps[_unescape_quoted(parts[0]).upper()] = _unescape_quoted(parts[1])
            elif len(parts) == 1:
                caps[_unescape_quoted(parts[0]).upper()] = ""
        if caps:
            self.capabilities = caps
