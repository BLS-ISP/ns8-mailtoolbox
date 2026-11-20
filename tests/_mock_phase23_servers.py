"""Real localhost TCP/HTTP protocol servers & Milter binary client for Phase 2 & 3 E2E tests.

Includes:
- `MilterTestClient`: Postfix-compatible binary Milter v2/v6 client (`SMFIC_*` / `SMFIR_*`)
- `MockSmtpSubmissionServer`: RFC 5321 SMTP server capturing outgoing digests, connector deliveries & alerts
- `MockImapServer`: IMAP4rev1 server supporting Dovecot Master-User `AUTHENTICATE PLAIN` (`user*vmail`) + `LOGIN`
- `MockPop3Server`: POP3 server with `UIDL` deduplication & toggleable auth failure
- `MockRspamdServer`: HTTP Rspamd Controller serving `/history` and `/ping` with `Password` header check
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from email.policy import default as default_email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
import socketserver
import struct
import threading
from typing import Any


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ============================================================================
# 1. Binary Postfix Milter Protocol Test Client
# ============================================================================

SMFIC_CONNECT = b"C"
SMFIC_MACRO = b"D"
SMFIC_HELO = b"H"
SMFIC_MAIL = b"M"
SMFIC_RCPT = b"R"
SMFIC_DATA = b"T"
SMFIC_HEADER = b"L"
SMFIC_EOH = b"N"
SMFIC_BODY = b"B"
SMFIC_BODYEOB = b"E"
SMFIC_OPTNEG = b"O"
SMFIC_QUIT = b"Q"

SMFIR_ADDRCPT = b"+"
SMFIR_DELRCPT = b"-"
SMFIR_ACCEPT = b"a"
SMFIR_REPLBODY = b"b"
SMFIR_CONTINUE = b"c"
SMFIR_DISCARD = b"d"
SMFIR_ADDHEADER = b"h"
SMFIR_INSHEADER = b"i"
SMFIR_CHGHEADER = b"m"
SMFIR_REJECT = b"r"
SMFIR_TEMPFAIL = b"t"

SMFIP_NOCONNECT = 0x00000001
SMFIP_NOHELO = 0x00000002
SMFIP_NOMAIL = 0x00000004
SMFIP_NORCPT = 0x00000008
SMFIP_NOBODY = 0x00000010
SMFIP_NOHDRS = 0x00000020
SMFIP_NOEOH = 0x00000040
SMFIP_NR_HDR = 0x00000080
SMFIP_NODATA = 0x00000200
SMFIP_NR_CONN = 0x00001000
SMFIP_NR_HELO = 0x00002000
SMFIP_NR_MAIL = 0x00004000
SMFIP_NR_RCPT = 0x00008000
SMFIP_NR_DATA = 0x00010000
SMFIP_NR_EOH = 0x00040000
SMFIP_NR_BODY = 0x00080000


@dataclass
class MilterResult:
    disposition: bytes
    body_replaced: bool
    raw_message: bytes
    parsed_message: Message
    changed_headers: list[tuple[int, str, str]] = field(default_factory=list)
    added_headers: list[tuple[str, str]] = field(default_factory=list)


class MilterTestClient:
    """Simulates Postfix speaking binary Milter protocol to `milter_server.py`."""

    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        chunks: list[bytes] = []
        rem = n
        while rem > 0:
            chunk = sock.recv(rem)
            if not chunk:
                raise EOFError("Unexpected EOF reading Milter packet")
            chunks.append(chunk)
            rem -= len(chunk)
        return b"".join(chunks)

    @classmethod
    def _send_pkt(cls, sock: socket.socket, cmd: bytes, payload: bytes = b"") -> None:
        header = struct.pack("!I", len(payload) + 1) + cmd
        sock.sendall(header + payload)

    @classmethod
    def _recv_pkt(cls, sock: socket.socket) -> tuple[bytes, bytes]:
        raw_len = cls._recv_exact(sock, 4)
        (pkt_len,) = struct.unpack("!I", raw_len)
        if pkt_len < 1:
            raise ValueError(f"Invalid Milter packet length: {pkt_len}")
        data = cls._recv_exact(sock, pkt_len)
        return data[:1], data[1:]

    def send_message(
        self,
        raw_rfc822: bytes,
        *,
        sender: str,
        recipients: list[str],
        auth_authen: str | None = None,
        client_ip: str = "10.0.0.25",
    ) -> MilterResult:
        # Split raw RFC 5322 message into headers and body
        sep = b"\r\n\r\n" if b"\r\n\r\n" in raw_rfc822 else b"\n\n"
        hdr_blob, orig_body = raw_rfc822.split(sep, 1)
        parsed_orig = message_from_bytes(hdr_blob + b"\r\n\r\n", policy=default_email_policy)
        headers: list[tuple[str, str]] = list(parsed_orig.items())

        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)

            # 1. Option negotiation
            opt_payload = struct.pack("!III", 6, 0x000001FF, 0x001FFFFF)
            self._send_pkt(sock, SMFIC_OPTNEG, opt_payload)
            resp_cmd, resp_data = self._recv_pkt(sock)
            assert resp_cmd == SMFIC_OPTNEG, f"Expected SMFIC_OPTNEG, got {resp_cmd!r}"
            version, _actions, proto_flags = struct.unpack("!III", resp_data[:12])

            def step(cmd: bytes, payload: bytes, skip_mask: int, nr_mask: int) -> bytes | None:
                if proto_flags & skip_mask:
                    return SMFIR_CONTINUE
                self._send_pkt(sock, cmd, payload)
                if proto_flags & nr_mask:
                    return SMFIR_CONTINUE
                rcmd, _ = self._recv_pkt(sock)
                return rcmd

            def send_macros(stage_cmd: bytes, kv: dict[str, str]) -> None:
                parts = [stage_cmd]
                for k, v in kv.items():
                    parts.append(k.encode("utf-8") + b"\x00" + v.encode("utf-8") + b"\x00")
                self._send_pkt(sock, SMFIC_MACRO, b"".join(parts))

            # 2. Connect
            conn_macros: dict[str, str] = {"j": "mail.firma.de", "_": client_ip}
            send_macros(SMFIC_CONNECT, conn_macros)
            conn_payload = (
                b"client.firma.de\x00"
                + b"4"
                + struct.pack("!H", 54321)
                + client_ip.encode("ascii")
                + b"\x00"
            )
            disp = step(SMFIC_CONNECT, conn_payload, SMFIP_NOCONNECT, SMFIP_NR_CONN)
            if disp != SMFIR_CONTINUE:
                self._send_pkt(sock, SMFIC_QUIT)
                return MilterResult(
                    disposition=disp or SMFIR_ACCEPT,
                    body_replaced=False,
                    raw_message=raw_rfc822,
                    parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                )

            # 3. HELO
            disp = step(SMFIC_HELO, b"client.firma.de\x00", SMFIP_NOHELO, SMFIP_NR_HELO)
            if disp != SMFIR_CONTINUE:
                self._send_pkt(sock, SMFIC_QUIT)
                return MilterResult(
                    disposition=disp or SMFIR_ACCEPT,
                    body_replaced=False,
                    raw_message=raw_rfc822,
                    parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                )

            # 4. MAIL FROM (+ {auth_authen} macro when authenticated!)
            mail_macros: dict[str, str] = {"{mail_addr}": sender}
            if auth_authen:
                mail_macros["{auth_authen}"] = auth_authen
                mail_macros["{auth_type}"] = "PLAIN"
            send_macros(SMFIC_MAIL, mail_macros)
            disp = step(
                SMFIC_MAIL,
                f"<{sender}>\x00".encode("utf-8"),
                SMFIP_NOMAIL,
                SMFIP_NR_MAIL,
            )
            if disp != SMFIR_CONTINUE:
                self._send_pkt(sock, SMFIC_QUIT)
                return MilterResult(
                    disposition=disp or SMFIR_ACCEPT,
                    body_replaced=False,
                    raw_message=raw_rfc822,
                    parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                )

            # 5. RCPT TO
            for rcpt in recipients:
                disp = step(
                    SMFIC_RCPT,
                    f"<{rcpt}>\x00".encode("utf-8"),
                    SMFIP_NORCPT,
                    SMFIP_NR_RCPT,
                )
                if disp != SMFIR_CONTINUE:
                    self._send_pkt(sock, SMFIC_QUIT)
                    return MilterResult(
                        disposition=disp or SMFIR_ACCEPT,
                        body_replaced=False,
                        raw_message=raw_rfc822,
                        parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                    )

            # 6. DATA (Milter v4+)
            if version >= 4:
                disp = step(SMFIC_DATA, b"", SMFIP_NODATA, SMFIP_NR_DATA)
                if disp != SMFIR_CONTINUE:
                    self._send_pkt(sock, SMFIC_QUIT)
                    return MilterResult(
                        disposition=disp or SMFIR_ACCEPT,
                        body_replaced=False,
                        raw_message=raw_rfc822,
                        parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                    )

            # 7. Headers
            for h_name, h_val in headers:
                h_payload = h_name.encode("utf-8") + b"\x00" + str(h_val).encode("utf-8") + b"\x00"
                disp = step(SMFIC_HEADER, h_payload, SMFIP_NOHDRS, SMFIP_NR_HDR)
                if disp != SMFIR_CONTINUE:
                    self._send_pkt(sock, SMFIC_QUIT)
                    return MilterResult(
                        disposition=disp or SMFIR_ACCEPT,
                        body_replaced=False,
                        raw_message=raw_rfc822,
                        parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                    )

            # 8. EOH
            disp = step(SMFIC_EOH, b"", SMFIP_NOEOH, SMFIP_NR_EOH)
            if disp != SMFIR_CONTINUE:
                self._send_pkt(sock, SMFIC_QUIT)
                return MilterResult(
                    disposition=disp or SMFIR_ACCEPT,
                    body_replaced=False,
                    raw_message=raw_rfc822,
                    parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                )

            # 9. BODY chunks
            for offset in range(0, max(1, len(orig_body)), 32768):
                chunk = orig_body[offset : offset + 32768]
                disp = step(SMFIC_BODY, chunk, SMFIP_NOBODY, SMFIP_NR_BODY)
                if disp != SMFIR_CONTINUE:
                    self._send_pkt(sock, SMFIC_QUIT)
                    return MilterResult(
                        disposition=disp or SMFIR_ACCEPT,
                        body_replaced=False,
                        raw_message=raw_rfc822,
                        parsed_message=message_from_bytes(raw_rfc822, policy=default_email_policy),
                    )

            # 10. BODYEOB -> collect modification commands until terminal disposition
            self._send_pkt(sock, SMFIC_BODYEOB, b"")
            new_body_chunks: list[bytes] = []
            changed_headers: list[tuple[int, str, str]] = []
            added_headers: list[tuple[str, str]] = []
            final_disp = SMFIR_CONTINUE

            while True:
                rcmd, rdata = self._recv_pkt(sock)
                if rcmd == SMFIR_REPLBODY:
                    new_body_chunks.append(rdata)
                elif rcmd == SMFIR_CHGHEADER:
                    (idx,) = struct.unpack("!I", rdata[:4])
                    parts = rdata[4:].split(b"\x00")
                    hname = parts[0].decode("utf-8", errors="replace")
                    hval = parts[1].decode("utf-8", errors="replace") if len(parts) > 1 else ""
                    changed_headers.append((idx, hname, hval))
                elif rcmd == SMFIR_ADDHEADER:
                    parts = rdata.split(b"\x00")
                    hname = parts[0].decode("utf-8", errors="replace")
                    hval = parts[1].decode("utf-8", errors="replace") if len(parts) > 1 else ""
                    added_headers.append((hname, hval))
                elif rcmd == SMFIR_INSHEADER:
                    parts = rdata[4:].split(b"\x00")
                    hname = parts[0].decode("utf-8", errors="replace")
                    hval = parts[1].decode("utf-8", errors="replace") if len(parts) > 1 else ""
                    added_headers.append((hname, hval))
                elif rcmd in {SMFIR_ADDRCPT, SMFIR_DELRCPT}:
                    continue
                elif rcmd in {
                    SMFIR_CONTINUE,
                    SMFIR_ACCEPT,
                    SMFIR_REJECT,
                    SMFIR_TEMPFAIL,
                    SMFIR_DISCARD,
                }:
                    final_disp = rcmd
                    break

            self._send_pkt(sock, SMFIC_QUIT)

        # Reconstruct resulting RFC 5322 message just like Postfix cleanup does
        working_headers: list[tuple[str, str]] = list(headers)
        for idx, hname, hval in changed_headers:
            target_low = hname.lower()
            occurrence = 0
            replaced = False
            for pos, (existing_k, _) in enumerate(working_headers):
                if existing_k.lower() == target_low:
                    occurrence += 1
                    if occurrence == max(1, idx):
                        if hval == "":
                            working_headers.pop(pos)
                        else:
                            working_headers[pos] = (hname, hval)
                        replaced = True
                        break
            if not replaced and hval != "":
                working_headers.append((hname, hval))

        for hname, hval in added_headers:
            working_headers.append((hname, hval))

        final_body = b"".join(new_body_chunks) if new_body_chunks else orig_body
        rebuilt_hdr = "".join(f"{k}: {v}\r\n" for k, v in working_headers).encode("utf-8")
        rebuilt_msg = rebuilt_hdr + b"\r\n" + final_body

        return MilterResult(
            disposition=final_disp,
            body_replaced=bool(new_body_chunks),
            raw_message=rebuilt_msg,
            parsed_message=message_from_bytes(rebuilt_msg, policy=default_email_policy),
            changed_headers=changed_headers,
            added_headers=added_headers,
        )


# ============================================================================
# 2. Mock SMTP Submission Server
# ============================================================================


@dataclass
class RecordedSmtpMail:
    mail_from: str
    rcpt_tos: list[str]
    raw_bytes: bytes
    parsed: Message


class MockSmtpSubmissionServer:
    """In-process RFC 5321 SMTP server capturing outgoing digests, fetched mails & failure alerts."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.messages: list[RecordedSmtpMail] = []
        self._server: _ThreadedTCPServer | None = None
        self.host = "127.0.0.1"
        self.port = 0

    def clear(self) -> None:
        with self.lock:
            self.messages.clear()

    def get_messages(self) -> list[RecordedSmtpMail]:
        with self.lock:
            return list(self.messages)

    def start(self) -> tuple[str, int]:
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def _send(self, line: str) -> None:
                self.wfile.write((line + "\r\n").encode("utf-8"))
                self.wfile.flush()

            def handle(self) -> None:
                self.request.settimeout(10.0)
                self._send("220 mock-submission.local ESMTP ready")
                mail_from = ""
                rcpt_tos: list[str] = []

                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    upper = line.upper()

                    if upper.startswith("EHLO") or upper.startswith("HELO"):
                        self._send("250-mock-submission.local")
                        self._send("250-8BITMIME")
                        self._send("250 AUTH PLAIN LOGIN")
                    elif upper.startswith("AUTH"):
                        self._send("235 2.7.0 Authentication successful")
                    elif upper.startswith("MAIL FROM:"):
                        m = re.search(r"<([^>]*)>", line)
                        mail_from = m.group(1) if m else line.split(":", 1)[1].strip()
                        rcpt_tos = []
                        self._send("250 2.1.0 Ok")
                    elif upper.startswith("RCPT TO:"):
                        m = re.search(r"<([^>]*)>", line)
                        rcpt = m.group(1) if m else line.split(":", 1)[1].strip()
                        rcpt_tos.append(rcpt)
                        self._send("250 2.1.5 Ok")
                    elif upper == "DATA":
                        self._send("354 End data with <CR><LF>.<CR><LF>")
                        data_lines: list[bytes] = []
                        while True:
                            dl = self.rfile.readline()
                            if not dl or dl in (b".\r\n", b".\n"):
                                break
                            if dl.startswith(b".."):
                                dl = dl[1:]
                            data_lines.append(dl)
                        raw_msg = b"".join(data_lines)
                        parsed = message_from_bytes(raw_msg, policy=default_email_policy)
                        with outer.lock:
                            outer.messages.append(
                                RecordedSmtpMail(
                                    mail_from=mail_from,
                                    rcpt_tos=list(rcpt_tos),
                                    raw_bytes=raw_msg,
                                    parsed=parsed,
                                )
                            )
                        self._send("250 2.0.0 Ok: queued")
                    elif upper.startswith("RSET"):
                        mail_from = ""
                        rcpt_tos = []
                        self._send("250 2.0.0 Ok")
                    elif upper.startswith("NOOP"):
                        self._send("250 2.0.0 Ok")
                    elif upper.startswith("QUIT"):
                        self._send("221 2.0.0 Bye")
                        break
                    else:
                        self._send("250 2.0.0 Ok")

        self._server = _ThreadedTCPServer((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.host, self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# ============================================================================
# 3. Mock IMAP4rev1 Server (Dovecot Master-User + External IMAP Connector)
# ============================================================================


@dataclass
class ImapMessageItem:
    uid: int
    raw_bytes: bytes
    flags: set[str] = field(default_factory=set)
    internaldate: str = "20-Nov-2026 06:15:00 +0000"


class MockImapServer:
    """In-process IMAP4rev1 TCP server supporting both `user*vmail` Master-User PLAIN and standard `LOGIN`."""

    def __init__(self, master_user: str = "vmail", master_password: str = "dovecot-master-secret") -> None:
        self.master_user = master_user
        self.master_password = master_password
        self.lock = threading.Lock()
        self.accounts: dict[str, str] = {}  # direct LOGIN user -> password
        self.fail_login_users: set[str] = set()
        # user -> folder -> list[ImapMessageItem]
        self.folders: dict[str, dict[str, list[ImapMessageItem]]] = {}
        self._next_uid: int = 100
        self._server: _ThreadedTCPServer | None = None
        self.host = "127.0.0.1"
        self.port = 0

    def add_account(self, username: str, password: str) -> None:
        with self.lock:
            self.accounts[username] = password
            self.folders.setdefault(username, {"INBOX": [], "Junk": []})

    def set_login_failure(self, username: str, fail: bool) -> None:
        with self.lock:
            if fail:
                self.fail_login_users.add(username)
            else:
                self.fail_login_users.discard(username)

    def seed_message(
        self,
        user: str,
        folder: str,
        raw_bytes: bytes,
        *,
        internaldate: str = "20-Nov-2026 06:15:00 +0000",
    ) -> int:
        with self.lock:
            self._next_uid += 1
            uid = self._next_uid
            ufolders = self.folders.setdefault(user, {"INBOX": [], "Junk": []})
            ufolders.setdefault(folder, []).append(
                ImapMessageItem(uid=uid, raw_bytes=raw_bytes, internaldate=internaldate)
            )
            return uid

    def list_folder_messages(self, user: str, folder: str) -> list[ImapMessageItem]:
        with self.lock:
            items = self.folders.get(user, {}).get(folder, [])
            return [
                ImapMessageItem(
                    uid=m.uid,
                    raw_bytes=m.raw_bytes,
                    flags=set(m.flags),
                    internaldate=m.internaldate,
                )
                for m in items
            ]

    @staticmethod
    def _unquote_imap(token: str) -> str:
        t = token.strip()
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            return t[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
        return t

    @staticmethod
    def _match_seq_or_uid(
        items: list[ImapMessageItem], set_spec: str, *, by_uid: bool
    ) -> list[tuple[int, ImapMessageItem]]:
        if not items:
            return []
        max_val = max(m.uid for m in items) if by_uid else len(items)
        matched_ids: set[int] = set()
        for part in set_spec.strip().split(","):
            if ":" in part:
                s_raw, e_raw = part.split(":", 1)
                s_val = max_val if s_raw == "*" else int(s_raw)
                e_val = max_val if e_raw == "*" else int(e_raw)
                lo, hi = min(s_val, e_val), max(s_val, e_val)
                matched_ids.update(range(lo, hi + 1))
            elif part == "*":
                matched_ids.add(max_val)
            else:
                matched_ids.add(int(part))

        results: list[tuple[int, ImapMessageItem]] = []
        for idx, item in enumerate(items, start=1):
            key = item.uid if by_uid else idx
            if key in matched_ids:
                results.append((idx, item))
        return results

    def start(self) -> tuple[str, int]:
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def _send(self, line: str) -> None:
                self.wfile.write((line + "\r\n").encode("utf-8"))
                self.wfile.flush()

            def handle(self) -> None:
                self.request.settimeout(10.0)
                authed_user: str | None = None
                selected_folder: str | None = None
                self._send("* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN MOVE UIDPLUS] Dovecot ready.")

                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        continue
                    parts = line.split(" ", 2)
                    if len(parts) < 2:
                        continue
                    tag = parts[0]
                    cmd = parts[1].upper()
                    rest = parts[2] if len(parts) > 2 else ""

                    if cmd == "CAPABILITY":
                        self._send("* CAPABILITY IMAP4rev1 AUTH=PLAIN MOVE UIDPLUS")
                        self._send(f"{tag} OK Capability completed.")

                    elif cmd == "NOOP":
                        self._send(f"{tag} OK NOOP completed.")

                    elif cmd == "LOGOUT":
                        self._send("* BYE Logging out")
                        self._send(f"{tag} OK Logout completed.")
                        break

                    elif cmd == "AUTHENTICATE":
                        mech_parts = rest.strip().split(" ", 1)
                        mech = mech_parts[0].upper().strip('"')
                        if mech != "PLAIN":
                            self._send(f"{tag} NO Unsupported auth mechanism")
                            continue
                        if len(mech_parts) > 1 and mech_parts[1].strip():
                            b64_data = mech_parts[1].strip()
                        else:
                            self._send("+ ")
                            cont = self.rfile.readline().decode("utf-8").strip()
                            b64_data = cont
                        try:
                            raw_auth = base64.b64decode(b64_data).decode("utf-8")
                            _authzid, authcid, passwd = raw_auth.split("\x00")
                        except Exception:
                            self._send(f"{tag} NO [AUTHENTICATIONFAILED] Bad SASL payload")
                            continue

                        if "*" in authcid:
                            target_u, master_u = authcid.rsplit("*", 1)
                            if (
                                master_u == outer.master_user
                                and passwd == outer.master_password
                                and target_u
                            ):
                                authed_user = target_u
                                with outer.lock:
                                    outer.folders.setdefault(
                                        authed_user, {"INBOX": [], "Junk": []}
                                    )
                                self._send(f"{tag} OK Logged in as master user.")
                            else:
                                self._send(f"{tag} NO [AUTHENTICATIONFAILED] Master auth failed.")
                        else:
                            with outer.lock:
                                should_fail = authcid in outer.fail_login_users
                                exp_pw = outer.accounts.get(authcid)
                            if not should_fail and exp_pw is not None and exp_pw == passwd:
                                authed_user = authcid
                                self._send(f"{tag} OK Logged in.")
                            else:
                                self._send(f"{tag} NO [AUTHENTICATIONFAILED] Authentication failed.")

                    elif cmd == "LOGIN":
                        # Parse user and password tokens (quoted or unquoted)
                        tokens = re.findall(r'"(?:\\.|[^"\\])*"|\S+', rest)
                        if len(tokens) < 2:
                            self._send(f"{tag} BAD Missing LOGIN arguments")
                            continue
                        u_name = outer._unquote_imap(tokens[0])
                        u_pass = outer._unquote_imap(tokens[1])
                        if "*" in u_name:
                            target_u, master_u = u_name.rsplit("*", 1)
                            if master_u == outer.master_user and u_pass == outer.master_password:
                                authed_user = target_u
                                with outer.lock:
                                    outer.folders.setdefault(
                                        authed_user, {"INBOX": [], "Junk": []}
                                    )
                                self._send(f"{tag} OK Logged in.")
                            else:
                                self._send(f"{tag} NO [AUTHENTICATIONFAILED] Authentication failed.")
                        else:
                            with outer.lock:
                                should_fail = u_name in outer.fail_login_users
                                exp_pw = outer.accounts.get(u_name)
                            if not should_fail and exp_pw is not None and exp_pw == u_pass:
                                authed_user = u_name
                                self._send(f"{tag} OK Logged in.")
                            else:
                                self._send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials.")

                    elif authed_user is None:
                        self._send(f"{tag} NO Authenticate first.")

                    elif cmd == "LIST":
                        with outer.lock:
                            fnames = sorted(outer.folders.get(authed_user, {}).keys())
                        for fn in fnames:
                            self._send(f'* LIST (\\HasNoChildren) "/" "{fn}"')
                        self._send(f"{tag} OK List completed.")

                    elif cmd in {"SELECT", "EXAMINE"}:
                        fname = outer._unquote_imap(rest)
                        with outer.lock:
                            ufolders = outer.folders.setdefault(
                                authed_user, {"INBOX": [], "Junk": []}
                            )
                            if fname not in ufolders:
                                self._send(f"{tag} NO [NONEXISTENT] Mailbox does not exist")
                                continue
                            count = len(ufolders[fname])
                        selected_folder = fname
                        self._send(f"* {count} EXISTS")
                        self._send("* 0 RECENT")
                        self._send("* OK [UIDVALIDITY 1] UIDs valid")
                        self._send("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
                        mode = "READ-ONLY" if cmd == "EXAMINE" else "READ-WRITE"
                        self._send(f"{tag} OK [{mode}] {cmd} completed.")

                    elif cmd == "CREATE":
                        fname = outer._unquote_imap(rest)
                        with outer.lock:
                            outer.folders.setdefault(authed_user, {}).setdefault(fname, [])
                        self._send(f"{tag} OK Create completed.")

                    elif cmd == "CLOSE":
                        if selected_folder:
                            with outer.lock:
                                items = outer.folders[authed_user][selected_folder]
                                outer.folders[authed_user][selected_folder] = [
                                    m for m in items if "\\Deleted" not in m.flags
                                ]
                        selected_folder = None
                        self._send(f"{tag} OK Close completed.")

                    elif cmd == "EXPUNGE":
                        if not selected_folder:
                            self._send(f"{tag} NO Select mailbox first")
                            continue
                        with outer.lock:
                            items = outer.folders[authed_user][selected_folder]
                            kept: list[ImapMessageItem] = []
                            expunged_seqs: list[int] = []
                            for idx, m in enumerate(items, start=1):
                                if "\\Deleted" in m.flags:
                                    expunged_seqs.append(idx)
                                else:
                                    kept.append(m)
                            outer.folders[authed_user][selected_folder] = kept
                        for seq_no in reversed(expunged_seqs):
                            self._send(f"* {seq_no} EXPUNGE")
                        self._send(f"{tag} OK Expunge completed.")

                    elif cmd in {"SEARCH", "FETCH", "STORE", "COPY", "MOVE", "UID"}:
                        if not selected_folder:
                            self._send(f"{tag} NO Select mailbox first")
                            continue

                        by_uid = cmd == "UID"
                        if by_uid:
                            sub_parts = rest.split(" ", 1)
                            sub_cmd = sub_parts[0].upper()
                            sub_rest = sub_parts[1] if len(sub_parts) > 1 else ""
                        else:
                            sub_cmd = cmd
                            sub_rest = rest

                        if sub_cmd == "SEARCH":
                            with outer.lock:
                                items = list(outer.folders[authed_user][selected_folder])
                            out_ids: list[str] = []
                            upper_crit = sub_rest.upper()
                            for idx, m in enumerate(items, start=1):
                                if "UNDELETED" in upper_crit and "\\Deleted" in m.flags:
                                    continue
                                out_ids.append(str(m.uid if by_uid else idx))
                            id_str = (" " + " ".join(out_ids)) if out_ids else ""
                            self._send(f"* SEARCH{id_str}")
                            self._send(f"{tag} OK Search completed.")

                        elif sub_cmd == "FETCH":
                            f_parts = sub_rest.split(" ", 1)
                            seq_spec = f_parts[0]
                            spec = f_parts[1] if len(f_parts) > 1 else "(UID FLAGS BODY[])"
                            spec_upper = spec.upper()
                            with outer.lock:
                                items = list(outer.folders[authed_user][selected_folder])
                            matched = outer._match_seq_or_uid(items, seq_spec, by_uid=by_uid)

                            for seq_no, m in matched:
                                resp_tokens: list[str] = [f"UID {m.uid}"]
                                if "FLAGS" in spec_upper:
                                    resp_tokens.append(f"FLAGS ({' '.join(sorted(m.flags))})")
                                if "INTERNALDATE" in spec_upper:
                                    resp_tokens.append(f'INTERNALDATE "{m.internaldate}"')
                                if "RFC822.SIZE" in spec_upper:
                                    resp_tokens.append(f"RFC822.SIZE {len(m.raw_bytes)}")

                                literal_bytes: bytes | None = None
                                literal_label = ""
                                if "HEADER" in spec_upper and "BODY[]" not in spec_upper and "BODY.PEEK[]" not in spec_upper and "RFC822" not in spec_upper.replace("RFC822.SIZE", "").replace("RFC822.HEADER", ""):
                                    sep = b"\r\n\r\n" if b"\r\n\r\n" in m.raw_bytes else b"\n\n"
                                    hdr_part = m.raw_bytes.split(sep, 1)[0] + b"\r\n\r\n"
                                    literal_bytes = hdr_part
                                    if "RFC822.HEADER" in spec_upper:
                                        literal_label = "RFC822.HEADER"
                                    else:
                                        bm = re.search(r"BODY(?:\.PEEK)?\[([^\]]*)\]", spec, flags=re.I)
                                        sec = bm.group(1) if bm else "HEADER"
                                        literal_label = f"BODY[{sec}]"
                                elif any(
                                    k in spec_upper
                                    for k in ("RFC822", "BODY[]", "BODY.PEEK[]")
                                ):
                                    literal_bytes = m.raw_bytes
                                    literal_label = (
                                        "RFC822"
                                        if ("RFC822" in spec_upper and "BODY" not in spec_upper)
                                        else "BODY[]"
                                    )

                                prefix_meta = " ".join(resp_tokens)
                                if literal_bytes is not None:
                                    head = (
                                        f"* {seq_no} FETCH ({prefix_meta} "
                                        f"{literal_label} {{{len(literal_bytes)}}}\r\n"
                                    ).encode("utf-8")
                                    self.wfile.write(head + literal_bytes + b")\r\n")
                                    self.wfile.flush()
                                else:
                                    self._send(f"* {seq_no} FETCH ({prefix_meta})")

                            self._send(f"{tag} OK Fetch completed.")

                        elif sub_cmd == "STORE":
                            st_parts = sub_rest.split(" ", 2)
                            seq_spec = st_parts[0]
                            mode_tok = st_parts[1].upper() if len(st_parts) > 1 else "+FLAGS"
                            flags_raw = st_parts[2] if len(st_parts) > 2 else ""
                            flag_list = set(re.findall(r"\\[A-Za-z]+", flags_raw))
                            with outer.lock:
                                items = outer.folders[authed_user][selected_folder]
                                matched = outer._match_seq_or_uid(items, seq_spec, by_uid=by_uid)
                                for _, m in matched:
                                    if mode_tok.startswith("+"):
                                        m.flags.update(flag_list)
                                    elif mode_tok.startswith("-"):
                                        m.flags.difference_update(flag_list)
                                    else:
                                        m.flags = set(flag_list)
                            self._send(f"{tag} OK Store completed.")

                        elif sub_cmd in {"COPY", "MOVE"}:
                            cp_parts = sub_rest.split(" ", 1)
                            seq_spec = cp_parts[0]
                            dest_folder = outer._unquote_imap(cp_parts[1])
                            with outer.lock:
                                ufolders = outer.folders[authed_user]
                                items = ufolders[selected_folder]
                                matched = outer._match_seq_or_uid(items, seq_spec, by_uid=by_uid)
                                dest_list = ufolders.setdefault(dest_folder, [])
                                moved_uids: set[int] = set()
                                for _, m in matched:
                                    outer._next_uid += 1
                                    dest_list.append(
                                        ImapMessageItem(
                                            uid=outer._next_uid,
                                            raw_bytes=m.raw_bytes,
                                            flags=m.flags - {"\\Deleted"},
                                            internaldate=m.internaldate,
                                        )
                                    )
                                    moved_uids.add(m.uid)
                                if sub_cmd == "MOVE":
                                    ufolders[selected_folder] = [
                                        m for m in items if m.uid not in moved_uids
                                    ]
                            self._send(f"{tag} OK {sub_cmd} completed.")

                    else:
                        self._send(f"{tag} BAD Unknown command {cmd}")

        self._server = _ThreadedTCPServer((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.host, self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# ============================================================================
# 4. Mock POP3 Server (for External POP3 Connector E2E)
# ============================================================================


@dataclass
class Pop3MessageItem:
    uidl: str
    raw_bytes: bytes


class MockPop3Server:
    """In-process POP3 server supporting UIDL deduplication, DELE, and simulated auth failure."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.accounts: dict[str, str] = {}
        self.fail_auth: set[str] = set()
        self.mailboxes: dict[str, list[Pop3MessageItem]] = {}
        self._server: _ThreadedTCPServer | None = None
        self.host = "127.0.0.1"
        self.port = 0

    def add_account(self, user: str, password: str) -> None:
        with self.lock:
            self.accounts[user] = password
            self.mailboxes.setdefault(user, [])

    def set_auth_failure(self, user: str, fail: bool) -> None:
        with self.lock:
            if fail:
                self.fail_auth.add(user)
            else:
                self.fail_auth.discard(user)

    def seed_message(self, user: str, uidl: str, raw_bytes: bytes) -> None:
        with self.lock:
            self.mailboxes.setdefault(user, []).append(Pop3MessageItem(uidl=uidl, raw_bytes=raw_bytes))

    def count_messages(self, user: str) -> int:
        with self.lock:
            return len(self.mailboxes.get(user, []))

    def start(self) -> tuple[str, int]:
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def _send(self, line: str) -> None:
                self.wfile.write((line + "\r\n").encode("utf-8"))
                self.wfile.flush()

            def handle(self) -> None:
                self.request.settimeout(10.0)
                self._send("+OK POP3 server ready")
                pending_user = ""
                authed_user: str | None = None
                session_msgs: list[Pop3MessageItem] = []
                deleted_indices: set[int] = set()

                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        continue
                    parts = line.split(" ", 1)
                    cmd = parts[0].upper()
                    arg = parts[1].strip() if len(parts) > 1 else ""

                    if cmd == "CAPA":
                        self._send("+OK Capability list follows")
                        self._send("USER")
                        self._send("UIDL")
                        self._send("TOP")
                        self._send(".")
                    elif cmd == "USER":
                        pending_user = arg
                        self._send("+OK User accepted")
                    elif cmd == "PASS":
                        with outer.lock:
                            fail = pending_user in outer.fail_auth
                            exp_pw = outer.accounts.get(pending_user)
                            msgs_copy = list(outer.mailboxes.get(pending_user, []))
                        if not fail and exp_pw is not None and exp_pw == arg:
                            authed_user = pending_user
                            session_msgs = msgs_copy
                            deleted_indices = set()
                            self._send("+OK Logged in")
                        else:
                            self._send("-ERR [AUTH] Invalid username or password")
                    elif cmd == "QUIT":
                        if authed_user and deleted_indices:
                            deleted_uidls = {
                                session_msgs[i - 1].uidl
                                for i in deleted_indices
                                if 1 <= i <= len(session_msgs)
                            }
                            with outer.lock:
                                outer.mailboxes[authed_user] = [
                                    m
                                    for m in outer.mailboxes.get(authed_user, [])
                                    if m.uidl not in deleted_uidls
                                ]
                        self._send("+OK Bye")
                        break
                    elif authed_user is None:
                        self._send("-ERR Authenticate first")
                    elif cmd == "STAT":
                        active = [
                            m for idx, m in enumerate(session_msgs, 1) if idx not in deleted_indices
                        ]
                        total_octets = sum(len(m.raw_bytes) for m in active)
                        self._send(f"+OK {len(active)} {total_octets}")
                    elif cmd == "LIST":
                        if arg:
                            idx = int(arg)
                            m = session_msgs[idx - 1]
                            self._send(f"+OK {idx} {len(m.raw_bytes)}")
                        else:
                            self._send(f"+OK {len(session_msgs)} messages")
                            for idx, m in enumerate(session_msgs, 1):
                                if idx not in deleted_indices:
                                    self._send(f"{idx} {len(m.raw_bytes)}")
                            self._send(".")
                    elif cmd == "UIDL":
                        if arg:
                            idx = int(arg)
                            m = session_msgs[idx - 1]
                            self._send(f"+OK {idx} {m.uidl}")
                        else:
                            self._send("+OK uidl list follows")
                            for idx, m in enumerate(session_msgs, 1):
                                if idx not in deleted_indices:
                                    self._send(f"{idx} {m.uidl}")
                            self._send(".")
                    elif cmd == "RETR":
                        idx = int(arg)
                        m = session_msgs[idx - 1]
                        self._send(f"+OK {len(m.raw_bytes)} octets")
                        for rline in m.raw_bytes.splitlines():
                            if rline.startswith(b"."):
                                rline = b"." + rline
                            self.wfile.write(rline + b"\r\n")
                        self.wfile.write(b".\r\n")
                        self.wfile.flush()
                    elif cmd == "DELE":
                        deleted_indices.add(int(arg))
                        self._send("+OK Marked deleted")
                    elif cmd == "NOOP":
                        self._send("+OK")
                    else:
                        self._send("-ERR Unknown command")

        self._server = _ThreadedTCPServer((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.host, self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# ============================================================================
# 5. Mock Rspamd HTTP Controller
# ============================================================================


class MockRspamdServer:
    """In-process HTTP server emulating Rspamd Controller `/history` & `/ping`."""

    def __init__(self, password: str = "rspamd-secret-123") -> None:
        self.password = password
        self.lock = threading.Lock()
        self.rows: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self.host = "127.0.0.1"
        self.port = 0

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        with self.lock:
            self.rows = list(rows)

    def clear_rows(self) -> None:
        with self.lock:
            self.rows.clear()

    def start(self) -> str:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/ping"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"pong\n")
                    return

                supplied_pw = self.headers.get("Password", "")
                if outer.password and supplied_pw != outer.password:
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "Unauthorized"}')
                    return

                if self.path.startswith("/history"):
                    with outer.lock:
                        payload = json.dumps({"rows": list(outer.rows)}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path.startswith("/stat"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"scanned": 42}')
                else:
                    self.send_response(404)
                    self.end_headers()

        self._server = ThreadingHTTPServer((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
