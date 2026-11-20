"""Real TCP RFC 5804 ManageSieve mock server for integration & e2e testing.

Implements Dovecot 2.4 Master-User SASL PLAIN authentication (`\x00user*vmail\x00pass`),
`LISTSCRIPTS`, `GETSCRIPT`, `CHECKSCRIPT` (with extension/syntax validation), `PUTSCRIPT`,
`SETACTIVE`, `DELETESCRIPT` (enforcing RFC 5804 non-active deletion rule), and `LOGOUT`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BufferedReader
import re
import socket
import socketserver
import ssl
import threading
from typing import Any

from app.backend.sieve_engine import tokenize_sieve

_AUTH_PLAIN_RE = re.compile(r'^AUTHENTICATE\s+"PLAIN"\s+"([^"]*)"\s*$', re.IGNORECASE)
_PUTSCRIPT_RE = re.compile(r'^PUTSCRIPT\s+"((?:\\.|[^"\\])*)"\s+\{(\d+)\+?\}\s*$', re.IGNORECASE)
_CHECKSCRIPT_RE = re.compile(r"^CHECKSCRIPT\s+\{(\d+)\+?\}\s*$", re.IGNORECASE)
_GETSCRIPT_RE = re.compile(r'^GETSCRIPT\s+"((?:\\.|[^"\\])*)"\s*$', re.IGNORECASE)
_SETACTIVE_RE = re.compile(r'^SETACTIVE\s+"((?:\\.|[^"\\])*)"\s*$', re.IGNORECASE)
_DELETESCRIPT_RE = re.compile(r'^DELETESCRIPT\s+"((?:\\.|[^"\\])*)"\s*$', re.IGNORECASE)


def _unescape(val: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(val):
        if val[i] == "\\" and i + 1 < len(val):
            out.append(val[i + 1])
            i += 2
        else:
            out.append(val[i])
            i += 1
    return "".join(out)


def validate_dovecot_sieve_syntax(script: str) -> str | None:
    """Perform realistic Dovecot Pigeonhole syntax and `require` checks."""
    # Check balanced braces outside strings/comments via tokenizer
    tokens = tokenize_sieve(script)
    brace_depth = 0
    paren_depth = 0
    bracket_depth = 0
    for t_type, t_val in tokens:
        if t_type == "PUNCT":
            if t_val == "{":
                brace_depth += 1
            elif t_val == "}":
                brace_depth -= 1
            elif t_val == "(":
                paren_depth += 1
            elif t_val == ")":
                paren_depth -= 1
            elif t_val == "[":
                bracket_depth += 1
            elif t_val == "]":
                bracket_depth -= 1
            if brace_depth < 0 or paren_depth < 0 or bracket_depth < 0:
                return "Unbalanced punctuation in Sieve script"
    if brace_depth != 0 or paren_depth != 0 or bracket_depth != 0:
        return "Unclosed block, parenthesis, or list in Sieve script"

    declared_requires: set[str] = set()
    idx = 0
    n = len(tokens)
    while idx < n:
        t_type, t_val = tokens[idx]
        if t_type == "IDENT" and t_val == "require":
            idx += 1
            while idx < n and not (tokens[idx][0] == "PUNCT" and tokens[idx][1] == ";"):
                if tokens[idx][0] == "STRING":
                    declared_requires.add(tokens[idx][1].lower())
                idx += 1
        elif t_type == "IDENT" and t_val == "vacation":
            if "vacation" not in declared_requires:
                return "vacation command used without require \"vacation\""
            # Find final string before ';' and ensure non-empty reason
            j = idx + 1
            last_str: str | None = None
            while j < n and not (tokens[j][0] == "PUNCT" and tokens[j][1] == ";"):
                if tokens[j][0] == "STRING":
                    last_str = tokens[j][1]
                j += 1
            if not last_str or not last_str.strip():
                return "vacation: empty reason string is not allowed"
        elif t_type == "IDENT" and t_val == "currentdate":
            if "date" not in declared_requires:
                return "currentdate test used without require \"date\""
        elif t_type == "TAG" and t_val == ":value":
            if "relational" not in declared_requires:
                return ":value match type used without require \"relational\""
        elif t_type == "TAG" and t_val == ":copy":
            if "copy" not in declared_requires:
                return ":copy tag used without require \"copy\""
        elif t_type == "IDENT" and t_val == "include":
            if "include" not in declared_requires:
                return "include command used without require \"include\""
        idx += 1

    return None


@dataclass
class MailboxSieveState:
    scripts: dict[str, str] = field(default_factory=dict)
    active_script: str | None = None


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class MockManageSieveServer:
    """In-process threaded TCP ManageSieve server for realistic Dovecot 2.4 tests."""

    def __init__(
        self,
        master_user: str = "vmail",
        master_password: str = "supersecret",
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.master_user = master_user
        self.master_password = master_password
        self.ssl_context = ssl_context
        self.tls_upgraded_connections = 0
        self.lock = threading.RLock()
        self.mailboxes: dict[str, MailboxSieveState] = {}
        self.command_history: list[tuple[str, str, str]] = []
        self.auth_events: list[dict[str, Any]] = []
        self._server: _ThreadedTCPServer | None = None
        self._thread: threading.Thread | None = None
        self.host = "127.0.0.1"
        self.port = 0

    def start(self) -> None:
        parent = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                parent._handle_client(self.request)

        self._server = _ThreadedTCPServer((self.host, 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def __enter__(self) -> MockManageSieveServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()

    def seed_script(self, user: str, name: str, content: str, *, active: bool = False) -> None:
        with self.lock:
            st = self.mailboxes.setdefault(user, MailboxSieveState())
            st.scripts[name] = content
            if active:
                st.active_script = name

    def get_mailbox_state(self, user: str) -> MailboxSieveState:
        with self.lock:
            st = self.mailboxes.setdefault(user, MailboxSieveState())
            return MailboxSieveState(scripts=dict(st.scripts), active_script=st.active_script)

    def simulate_sogo_nightly_cron(self, user: str, new_sogo_content: str | None = None) -> None:
        """Simulate SOGo's 00:00 `sogo-tool update-autoreply` overriding active script to `sogo`."""
        with self.lock:
            st = self.mailboxes.setdefault(user, MailboxSieveState())
            if new_sogo_content is not None:
                st.scripts["sogo"] = new_sogo_content
            if "sogo" in st.scripts:
                st.active_script = "sogo"

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        reader: BufferedReader = conn.makefile("rb")
        authed_user: str | None = None
        tls_active = False
        try:
            def _banner() -> bytes:
                lines = [
                    b'"IMPLEMENTATION" "Dovecot Pigeonhole"\r\n',
                    b'"SIEVE" "fileinto reject envelope encoded-character vacation '
                    b'comparator-i;ascii-numeric relational copy include date"\r\n',
                    b'"SASL" "PLAIN"\r\n',
                ]
                if self.ssl_context is not None and not tls_active:
                    lines.append(b'"STARTTLS"\r\n')
                lines.append(b'"VERSION" "1.0"\r\n')
                lines.append(b'OK "Dovecot ready."\r\n')
                return b"".join(lines)

            conn.sendall(_banner())

            while True:
                raw_line = reader.readline()
                if not raw_line:
                    break
                line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                upper = line.strip().upper()

                if upper == "LOGOUT":
                    conn.sendall(b'OK "Logout completed."\r\n')
                    break

                if upper == "CAPABILITY":
                    conn.sendall(_banner())
                    continue

                if upper == "STARTTLS" and self.ssl_context is not None and not tls_active:
                    conn.sendall(b'OK "Begin TLS negotiation now."\r\n')
                    reader.close()
                    conn = self.ssl_context.wrap_socket(conn, server_side=True)
                    conn.settimeout(5.0)
                    reader = conn.makefile("rb")
                    tls_active = True
                    with self.lock:
                        self.tls_upgraded_connections += 1
                    conn.sendall(_banner())
                    continue

                auth_match = _AUTH_PLAIN_RE.match(line.strip())
                if auth_match:
                    b64_data = auth_match.group(1)
                    try:
                        decoded = base64.b64decode(b64_data.encode("ascii"))
                        parts = decoded.split(b"\x00")
                        if len(parts) != 3:
                            raise ValueError("Expected 3 NUL-delimited SASL PLAIN parts")
                        authzid = parts[0].decode("utf-8")
                        authcid = parts[1].decode("utf-8")
                        passwd = parts[2].decode("utf-8")
                    except Exception:
                        conn.sendall(b'NO "Malformed SASL PLAIN payload."\r\n')
                        continue

                    with self.lock:
                        self.auth_events.append(
                            {"authzid": authzid, "authcid": authcid, "password": passwd}
                        )

                    # Enforce empty authzid and `<target_user>*<master_user>` format
                    if authzid != "" or "*" not in authcid:
                        conn.sendall(b'NO "Invalid master user syntax."\r\n')
                        continue

                    t_user, m_user = authcid.split("*", 1)
                    if not t_user or m_user != self.master_user or passwd != self.master_password:
                        conn.sendall(b'NO "Authentication failed."\r\n')
                        continue

                    authed_user = t_user
                    conn.sendall(b'OK "Logged in."\r\n')
                    continue

                if authed_user is None:
                    conn.sendall(b'NO "Authenticate first."\r\n')
                    continue

                if upper == "LISTSCRIPTS":
                    with self.lock:
                        self.command_history.append((authed_user, "LISTSCRIPTS", ""))
                        st = self.mailboxes.setdefault(authed_user, MailboxSieveState())
                        out_lines: list[bytes] = []
                        for s_name in sorted(st.scripts.keys()):
                            esc = s_name.replace("\\", "\\\\").replace('"', '\\"')
                            if st.active_script == s_name:
                                out_lines.append(f'"{esc}" ACTIVE\r\n'.encode("utf-8"))
                            else:
                                out_lines.append(f'"{esc}"\r\n'.encode("utf-8"))
                        out_lines.append(b'OK "Listscripts completed."\r\n')
                    conn.sendall(b"".join(out_lines))
                    continue

                get_match = _GETSCRIPT_RE.match(line.strip())
                if get_match:
                    s_name = _unescape(get_match.group(1))
                    with self.lock:
                        self.command_history.append((authed_user, "GETSCRIPT", s_name))
                        st = self.mailboxes.setdefault(authed_user, MailboxSieveState())
                        if s_name not in st.scripts:
                            conn.sendall(b'NO (NONEXISTENT) "Script does not exist."\r\n')
                            continue
                        script_bytes = st.scripts[s_name].encode("utf-8")
                    header = f"{{{len(script_bytes)}}}\r\n".encode("ascii")
                    conn.sendall(header + script_bytes + b'\r\nOK "Getscript completed."\r\n')
                    continue

                chk_match = _CHECKSCRIPT_RE.match(line.strip())
                if chk_match:
                    byte_len = int(chk_match.group(1))
                    body_bytes = self._read_exact(reader, byte_len)
                    reader.readline()  # trailing CRLF
                    script_text = body_bytes.decode("utf-8")
                    with self.lock:
                        self.command_history.append((authed_user, "CHECKSCRIPT", ""))
                    err = validate_dovecot_sieve_syntax(script_text)
                    if err:
                        err_bytes = err.encode("utf-8")
                        conn.sendall(
                            f"NO {{{len(err_bytes)}}}\r\n".encode("ascii") + err_bytes + b"\r\n"
                        )
                    else:
                        conn.sendall(b'OK "Checkscript completed."\r\n')
                    continue

                put_match = _PUTSCRIPT_RE.match(line.strip())
                if put_match:
                    s_name = _unescape(put_match.group(1))
                    byte_len = int(put_match.group(2))
                    body_bytes = self._read_exact(reader, byte_len)
                    reader.readline()  # trailing CRLF
                    script_text = body_bytes.decode("utf-8")
                    err = validate_dovecot_sieve_syntax(script_text)
                    if err:
                        conn.sendall(f'NO "{err}"\r\n'.encode("utf-8"))
                        continue
                    with self.lock:
                        self.command_history.append((authed_user, "PUTSCRIPT", s_name))
                        st = self.mailboxes.setdefault(authed_user, MailboxSieveState())
                        st.scripts[s_name] = script_text
                    conn.sendall(b'OK "Putscript completed."\r\n')
                    continue

                act_match = _SETACTIVE_RE.match(line.strip())
                if act_match:
                    s_name = _unescape(act_match.group(1))
                    with self.lock:
                        self.command_history.append((authed_user, "SETACTIVE", s_name))
                        st = self.mailboxes.setdefault(authed_user, MailboxSieveState())
                        if s_name == "":
                            st.active_script = None
                            conn.sendall(b'OK "Setactive completed."\r\n')
                        elif s_name not in st.scripts:
                            conn.sendall(b'NO (NONEXISTENT) "Script does not exist."\r\n')
                        else:
                            st.active_script = s_name
                            conn.sendall(b'OK "Setactive completed."\r\n')
                    continue

                del_match = _DELETESCRIPT_RE.match(line.strip())
                if del_match:
                    s_name = _unescape(del_match.group(1))
                    with self.lock:
                        self.command_history.append((authed_user, "DELETESCRIPT", s_name))
                        st = self.mailboxes.setdefault(authed_user, MailboxSieveState())
                        if s_name not in st.scripts:
                            conn.sendall(b'NO (NONEXISTENT) "Script does not exist."\r\n')
                        elif st.active_script == s_name:
                            # RFC 5804 Section 2.5: active script MUST NOT be deleted directly
                            conn.sendall(b'NO (ACTIVE) "Cannot delete active script."\r\n')
                        else:
                            del st.scripts[s_name]
                            conn.sendall(b'OK "Deletescript completed."\r\n')
                    continue

                conn.sendall(b'NO "Unknown command."\r\n')
        except OSError:
            pass
        finally:
            try:
                reader.close()
            except OSError:
                pass

    @staticmethod
    def _read_exact(reader: BufferedReader, num_bytes: int) -> bytes:
        chunks: list[bytes] = []
        rem = num_bytes
        while rem > 0:
            chunk = reader.read(rem)
            if not chunk:
                break
            chunks.append(chunk)
            rem -= len(chunk)
        return b"".join(chunks)
