"""RFC 5804 / Dovecot 2.4 Pigeonhole ManageSieve TCP mock server for E2E verification."""

from __future__ import annotations

import base64
import re
import socket
import socketserver
import threading
from dataclasses import dataclass, field


@dataclass
class UserSieveBox:
    scripts: dict[str, str] = field(default_factory=dict)
    active: str = ""


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _parse_sieve_token_and_rest(line: str) -> tuple[str, str]:
    line = line.strip()
    if not line:
        return "", ""
    parts = line.split(" ", 1)
    return parts[0].upper(), (parts[1].strip() if len(parts) > 1 else "")


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
    return s


class MockManageSieveServer:
    """In-process threaded TCP server speaking Dovecot 2.4 ManageSieve (RFC 5804)."""

    def __init__(self, master_user: str = "vmail", master_password: str = "secret-vmail-pass") -> None:
        self.master_user = master_user
        self.master_password = master_password
        self.lock = threading.Lock()
        self.mailboxes: dict[str, UserSieveBox] = {}
        self.auth_log: list[tuple[str, str]] = []
        self.checkscript_calls: list[tuple[str, str]] = []
        self._server: _ThreadedTCPServer | None = None
        self._thread: threading.Thread | None = None
        self.host = "127.0.0.1"
        self.port = 0

    def seed_script(self, user: str, name: str, content: str, active: bool = False) -> None:
        with self.lock:
            box = self.mailboxes.setdefault(user, UserSieveBox())
            box.scripts[name] = content
            if active:
                box.active = name

    def get_user_box(self, user: str) -> UserSieveBox:
        with self.lock:
            box = self.mailboxes.get(user)
            if not box:
                return UserSieveBox()
            return UserSieveBox(scripts=dict(box.scripts), active=box.active)

    def start(self) -> tuple[str, int]:
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def _send(self, line: str) -> None:
                self.wfile.write((line + "\r\n").encode("utf-8"))
                self.wfile.flush()

            def _send_capabilities(self) -> None:
                self._send('"IMPLEMENTATION" "Dovecot Pigeonhole"')
                self._send(
                    '"SIEVE" "fileinto reject envelope encoded-character vacation '
                    'subaddress comparator-i;ascii-numeric relational regex imap4flags '
                    'copy include variables body enotify environment mailbox date index"'
                )
                self._send('"SASL" "PLAIN"')
                self._send('"VERSION" "1.0"')
                self._send('OK "Dovecot ready."')

            def _read_string_or_literal(self, rest: str) -> str:
                rest = rest.strip()
                lit_match = re.match(r"^\{(\d+)\+?\}\s*$", rest)
                if lit_match:
                    nbytes = int(lit_match.group(1))
                    if not rest.endswith("+}"):
                        # Synchronizing literal
                        self._send("+ OK")
                    data = self.rfile.read(nbytes)
                    # Consume trailing CRLF after literal block
                    tail = self.rfile.readline()
                    _ = tail
                    return data.decode("utf-8")
                return _unquote(rest)

            def handle(self) -> None:
                self.request.settimeout(10.0)
                authed_user: str | None = None
                self._send_capabilities()

                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        continue
                    cmd, rest = _parse_sieve_token_and_rest(line)

                    if cmd == "CAPABILITY":
                        self._send_capabilities()

                    elif cmd == "NOOP":
                        self._send('OK "NOOP completed."')

                    elif cmd == "LOGOUT":
                        self._send('OK "Logout completed."')
                        break

                    elif cmd == "AUTHENTICATE":
                        # AUTHENTICATE "PLAIN" ["<b64>" | {N+}]
                        m = re.match(r'^"PLAIN"(?:\s+(.*))?$', rest, flags=re.IGNORECASE)
                        if not m:
                            self._send('NO "Unsupported SASL mechanism."')
                            continue
                        arg_part = (m.group(1) or "").strip()
                        if not arg_part:
                            self._send('""')
                            cont = self.rfile.readline().decode("utf-8").rstrip("\r\n")
                            b64_str = _unquote(cont)
                        else:
                            b64_str = self._read_string_or_literal(arg_part)

                        try:
                            decoded = base64.b64decode(b64_str).decode("utf-8")
                        except Exception:
                            self._send('NO "Invalid base64 SASL payload."')
                            continue

                        parts = decoded.split("\x00")
                        if len(parts) != 3:
                            self._send('NO "Malformed SASL PLAIN payload."')
                            continue
                        authzid, authcid, passwd = parts
                        # Dovecot master user format: empty authzid (or matching user), authcid = "<target_user>*<master_user>"
                        if "*" not in authcid:
                            self._send('NO "Expected master-user <user>*vmail login."')
                            continue
                        target_user, master = authcid.rsplit("*", 1)
                        if authzid and authzid != target_user:
                            self._send('NO "Unexpected authzid."')
                            continue
                        if master != outer.master_user or passwd != outer.master_password or not target_user:
                            self._send('NO "Authentication failed."')
                            continue

                        authed_user = target_user
                        with outer.lock:
                            outer.auth_log.append((target_user, master))
                            outer.mailboxes.setdefault(target_user, UserSieveBox())
                        self._send('OK "Logged in."')

                    elif authed_user is None:
                        self._send('NO "Authenticate first."')

                    elif cmd == "LISTSCRIPTS":
                        with outer.lock:
                            box = outer.mailboxes.setdefault(authed_user, UserSieveBox())
                            items = sorted(box.scripts.items())
                            active_name = box.active
                        for sname, _ in items:
                            if sname == active_name:
                                self._send(f'"{sname}" ACTIVE')
                            else:
                                self._send(f'"{sname}"')
                        self._send('OK "Listscripts completed."')

                    elif cmd == "GETSCRIPT":
                        sname = _unquote(rest)
                        with outer.lock:
                            box = outer.mailboxes.setdefault(authed_user, UserSieveBox())
                            script_body = box.scripts.get(sname)
                        if script_body is None:
                            self._send('NO (NONEXISTENT) "Script does not exist."')
                        else:
                            encoded = script_body.encode("utf-8")
                            self._send(f"{{{len(encoded)}}}")
                            self.wfile.write(encoded + b"\r\n")
                            self.wfile.flush()
                            self._send('OK "Getscript completed."')

                    elif cmd == "CHECKSCRIPT":
                        script_body = self._read_string_or_literal(rest)
                        err = outer._validate_sieve_syntax(script_body)
                        if err:
                            self._send(f'NO "{err}"')
                        else:
                            with outer.lock:
                                outer.checkscript_calls.append((authed_user, script_body))
                            self._send('OK "Checkscript completed."')

                    elif cmd == "PUTSCRIPT":
                        pm = re.match(r'^"((?:\\.|[^"\\])*)"\s+(.*)$', rest)
                        if not pm:
                            self._send('NO "Bad PUTSCRIPT syntax."')
                            continue
                        sname = _unquote(f'"{pm.group(1)}"')
                        script_body = self._read_string_or_literal(pm.group(2))
                        err = outer._validate_sieve_syntax(script_body)
                        if err:
                            self._send(f'NO "{err}"')
                            continue
                        with outer.lock:
                            box = outer.mailboxes.setdefault(authed_user, UserSieveBox())
                            box.scripts[sname] = script_body
                        self._send('OK "Putscript completed."')

                    elif cmd == "SETACTIVE":
                        sname = _unquote(rest)
                        with outer.lock:
                            box = outer.mailboxes.setdefault(authed_user, UserSieveBox())
                            if sname != "" and sname not in box.scripts:
                                self._send('NO (NONEXISTENT) "Script does not exist."')
                                continue
                            box.active = sname
                        self._send('OK "Setactive completed."')

                    elif cmd == "DELETESCRIPT":
                        sname = _unquote(rest)
                        with outer.lock:
                            box = outer.mailboxes.setdefault(authed_user, UserSieveBox())
                            if sname not in box.scripts:
                                self._send('NO (NONEXISTENT) "Script does not exist."')
                                continue
                            if box.active == sname:
                                self._send('NO (ACTIVE) "Cannot delete the active script."')
                                continue
                            del box.scripts[sname]
                        self._send('OK "Deletescript completed."')

                    else:
                        self._send(f'NO "Unknown command {cmd}."')

        self._server = _ThreadedTCPServer((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.host, self.port

    @staticmethod
    def _validate_sieve_syntax(script: str) -> str | None:
        """Basic Dovecot Pigeonhole Sieve capability & syntax sanity check."""
        req_match = re.search(r"require\s*\[([^\]]*)\]\s*;", script)
        declared: set[str] = set()
        if req_match:
            declared = set(re.findall(r'"([^"]+)"', req_match.group(1)))

        if re.search(r"\bvacation\b", script) and "vacation" not in declared:
            return "vacation used without require \"vacation\""
        if re.search(r"\bcurrentdate\b", script) and "date" not in declared:
            return "currentdate used without require \"date\""
        if re.search(r":value\b", script) and "relational" not in declared:
            return ":value used without require \"relational\""
        if re.search(r"redirect\s+:copy\b", script) and "copy" not in declared:
            return "redirect :copy used without require \"copy\""
        if re.search(r"\binclude\b", script) and "include" not in declared:
            return "include used without require \"include\""
        return None

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
