"""Pure-Python asyncio Postfix/Sendmail binary Milter server (Before-DKIM outgoing filter).

Implements Milter wire protocol v2–v6 on TCP 8090 (configurable). Inspects `{auth_authen}`
macro from Postfix SASL authentication:
- Unauthenticated inbound mail (port 25, no `{auth_authen}`) -> immediate `SMFIR_ACCEPT`.
- Authenticated outgoing submission -> rewrites MIME body with dynamic AD/LDAP signature,
  sends `SMFIR_CHGHEADER` / `SMFIR_ADDHEADER` + chunked `SMFIR_REPLBODY`, and finishes with
  `SMFIR_CONTINUE` so Postfix passes the signed body next to Rspamd (`127.0.0.1:11332`) for DKIM.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from app.backend.config import get_settings
from app.backend.signature_engine import process_outgoing_milter_message

logger = logging.getLogger(__name__)

# Milter Client -> Filter commands (mfdef.h)
SMFIC_ABORT = ord("A")
SMFIC_BODY = ord("B")
SMFIC_CONNECT = ord("C")
SMFIC_MACRO = ord("D")
SMFIC_BODYEOB = ord("E")
SMFIC_HELO = ord("H")
SMFIC_QUIT_NC = ord("K")
SMFIC_HEADER = ord("L")
SMFIC_MAIL = ord("M")
SMFIC_EOH = ord("N")
SMFIC_OPTNEG = ord("O")
SMFIC_QUIT = ord("Q")
SMFIC_RCPT = ord("R")
SMFIC_DATA = ord("T")
SMFIC_UNKNOWN = ord("U")

# Milter Filter -> MTA responses (mfdef.h)
SMFIR_ADDRCPT = ord("+")
SMFIR_DELRCPT = ord("-")
SMFIR_ACCEPT = ord("a")
SMFIR_REPLBODY = ord("b")
SMFIR_CONTINUE = ord("c")
SMFIR_ADDHEADER = ord("h")
SMFIR_CHGHEADER = ord("m")
SMFIR_OPTNEG = ord("O")

# Negotiated action bitmask
SMFIF_ADDHDRS = 0x00000001
SMFIF_CHGBODY = 0x00000002
SMFIF_CHGHDRS = 0x00000010
SMFIP_NONE = 0x00000000

MAX_REPLBODY_CHUNK = 65535


def pack_milter_packet(cmd: int, payload: bytes = b"") -> bytes:
    """Encode a binary Milter packet (`uint32be(len(payload)+1) + cmd_byte + payload`)."""
    return struct.pack("!IB", len(payload) + 1, cmd) + payload


def _parse_macro_pairs(payload: bytes) -> dict[str, str]:
    if len(payload) < 1:
        return {}
    parts = payload[1:].split(b"\x00")
    macros: dict[str, str] = {}
    idx = 0
    while idx + 1 < len(parts):
        raw_k = parts[idx].decode("utf-8", errors="replace").strip()
        raw_v = parts[idx + 1].decode("utf-8", errors="replace")
        if raw_k:
            macros[raw_k] = raw_v
            stripped_k = raw_k.strip("{}")
            if stripped_k:
                macros[stripped_k] = raw_v
        idx += 2
    return macros


def _parse_cstring_first(payload: bytes) -> str:
    if not payload:
        return ""
    first = payload.split(b"\x00", 1)[0]
    return first.decode("utf-8", errors="replace").strip()


class MilterServer:
    """Asyncio TCP server speaking the Postfix/Sendmail Milter binary protocol."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8090) -> None:
        self.host = host
        self.port = int(port)
        self._server: asyncio.AbstractServer | None = None
        self.listening: bool = False
        self.messages_signed: int = 0
        self.messages_skipped: int = 0

    async def __aenter__(self) -> MilterServer:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self.host,
            port=self.port,
        )
        sockets = self._server.sockets or []
        if sockets:
            bound_addr = sockets[0].getsockname()
            self.port = int(bound_addr[1])
        self.listening = True
        logger.info("Before-DKIM Signature Milter listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        self.listening = False
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    def status_dict(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "enabled": bool(settings.runtime.milter_enabled),
            "listening": bool(self.listening),
            "port": int(self.port),
            "host_port": int(settings.runtime.milter_host_port),
            "messages_signed": int(self.messages_signed),
            "messages_skipped": int(self.messages_skipped),
        }

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        conn_macros: dict[str, str] = {}
        msg_macros: dict[str, str] = {}
        mail_from: str = ""
        headers: list[tuple[str, str]] = []
        body_buf = bytearray()

        def _reset_message() -> None:
            nonlocal msg_macros, mail_from, headers, body_buf
            msg_macros = {}
            mail_from = ""
            headers = []
            body_buf = bytearray()

        def _auth_user() -> str:
            return (
                msg_macros.get("auth_authen")
                or conn_macros.get("auth_authen")
                or ""
            ).strip()

        try:
            while True:
                len_bytes = await reader.readexactly(4)
                (pkt_len,) = struct.unpack("!I", len_bytes)
                if pkt_len < 1 or pkt_len > 64 * 1024 * 1024:
                    break
                pkt_data = await reader.readexactly(pkt_len)
                cmd = pkt_data[0]
                payload = pkt_data[1:]

                if cmd == SMFIC_OPTNEG:
                    if len(payload) >= 12:
                        client_ver, _c_actions, _c_proto = struct.unpack("!III", payload[:12])
                        neg_ver = max(2, min(int(client_ver), 6))
                    else:
                        neg_ver = 6
                    actions = SMFIF_ADDHDRS | SMFIF_CHGBODY | SMFIF_CHGHDRS
                    resp_payload = struct.pack("!III", neg_ver, actions, SMFIP_NONE)
                    writer.write(pack_milter_packet(SMFIR_OPTNEG, resp_payload))
                    await writer.drain()
                    continue

                if cmd == SMFIC_MACRO:
                    # SMFIC_MACRO expects NO response packet from the filter
                    parsed = _parse_macro_pairs(payload)
                    macro_stage = payload[0] if payload else 0
                    if macro_stage in (SMFIC_CONNECT, SMFIC_HELO):
                        conn_macros.update(parsed)
                    else:
                        msg_macros.update(parsed)
                    continue

                if cmd in (SMFIC_CONNECT, SMFIC_HELO, SMFIC_UNKNOWN):
                    writer.write(pack_milter_packet(SMFIR_CONTINUE))
                    await writer.drain()
                    continue

                if cmd == SMFIC_MAIL:
                    headers = []
                    body_buf = bytearray()
                    mail_from = _parse_cstring_first(payload).strip("<>").strip()
                    settings = get_settings()
                    if not settings.runtime.milter_enabled or not _auth_user():
                        # Unauthenticated inbound mail (port 25) or disabled milter -> immediate ACCEPT
                        self.messages_skipped += 1
                        writer.write(pack_milter_packet(SMFIR_ACCEPT))
                    else:
                        writer.write(pack_milter_packet(SMFIR_CONTINUE))
                    await writer.drain()
                    continue

                if cmd in (SMFIC_RCPT, SMFIC_DATA):
                    writer.write(pack_milter_packet(SMFIR_CONTINUE))
                    await writer.drain()
                    continue

                if cmd == SMFIC_HEADER:
                    parts = payload.split(b"\x00")
                    h_name = parts[0].decode("utf-8", errors="replace") if len(parts) >= 1 else ""
                    h_val = parts[1].decode("utf-8", errors="replace") if len(parts) >= 2 else ""
                    if h_name:
                        headers.append((h_name, h_val))
                    writer.write(pack_milter_packet(SMFIR_CONTINUE))
                    await writer.drain()
                    continue

                if cmd == SMFIC_EOH:
                    writer.write(pack_milter_packet(SMFIR_CONTINUE))
                    await writer.drain()
                    continue

                if cmd == SMFIC_BODY:
                    body_buf.extend(payload)
                    writer.write(pack_milter_packet(SMFIR_CONTINUE))
                    await writer.drain()
                    continue

                if cmd == SMFIC_BODYEOB:
                    auth_u = _auth_user()
                    settings = get_settings()
                    if not settings.runtime.milter_enabled or not auth_u:
                        writer.write(pack_milter_packet(SMFIR_ACCEPT))
                        await writer.drain()
                        _reset_message()
                        continue

                    try:
                        header_lines = [
                            f"{k}: {v}".encode("utf-8") for k, v in headers
                        ]
                        raw_msg = b"\r\n".join(header_lines) + b"\r\n\r\n" + bytes(body_buf)
                        rewrite = await asyncio.to_thread(
                            process_outgoing_milter_message,
                            auth_user=auth_u,
                            mail_from=mail_from,
                            raw_message=raw_msg,
                        )
                        if rewrite.signed:
                            for h_name, h_val in rewrite.changed_headers.items():
                                chg_payload = (
                                    struct.pack("!I", 1)
                                    + h_name.encode("ascii")
                                    + b"\x00"
                                    + h_val.encode("utf-8")
                                    + b"\x00"
                                )
                                writer.write(pack_milter_packet(SMFIR_CHGHEADER, chg_payload))
                            for h_name, h_val in rewrite.added_headers.items():
                                add_payload = (
                                    h_name.encode("ascii")
                                    + b"\x00"
                                    + h_val.encode("utf-8")
                                    + b"\x00"
                                )
                                writer.write(pack_milter_packet(SMFIR_ADDHEADER, add_payload))

                            new_body = rewrite.new_body_bytes
                            if not new_body:
                                writer.write(pack_milter_packet(SMFIR_REPLBODY, b""))
                            else:
                                for offset in range(0, len(new_body), MAX_REPLBODY_CHUNK):
                                    chunk = new_body[offset : offset + MAX_REPLBODY_CHUNK]
                                    writer.write(pack_milter_packet(SMFIR_REPLBODY, chunk))

                            self.messages_signed += 1
                        else:
                            self.messages_skipped += 1
                    except Exception as exc:
                        # Fail-open so outgoing mail is never dropped on unexpected errors
                        logger.exception("Milter signing error for %s: %s", auth_u, exc)
                        self.messages_skipped += 1

                    writer.write(pack_milter_packet(SMFIR_CONTINUE))
                    await writer.drain()
                    _reset_message()
                    continue

                if cmd == SMFIC_ABORT:
                    _reset_message()
                    continue

                if cmd == SMFIC_QUIT_NC:
                    _reset_message()
                    conn_macros = {}
                    continue

                if cmd == SMFIC_QUIT:
                    break

                writer.write(pack_milter_packet(SMFIR_CONTINUE))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


_active_milter_server: MilterServer | None = None


def set_active_milter_server(srv: MilterServer | None) -> None:
    global _active_milter_server
    _active_milter_server = srv


def get_active_milter_server() -> MilterServer | None:
    return _active_milter_server


def get_global_milter_status() -> dict[str, Any]:
    if _active_milter_server is not None:
        return _active_milter_server.status_dict()
    settings = get_settings()
    return {
        "enabled": bool(settings.runtime.milter_enabled),
        "listening": False,
        "port": int(settings.runtime.milter_listen_port),
        "host_port": int(settings.runtime.milter_host_port),
        "messages_signed": 0,
        "messages_skipped": 0,
    }
