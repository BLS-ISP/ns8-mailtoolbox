"""Phase 2 tests: extended settings, AD signature placeholder engine, and binary Milter E2E."""

from __future__ import annotations

import asyncio
import email
from email.message import EmailMessage
import email.policy
from pathlib import Path
import struct
from typing import Any

from fastapi.testclient import TestClient

from app.backend.db import save_signature_rule
from app.backend.main import app
from app.backend.milter_server import (
    MilterServer,
    SMFIC_BODY,
    SMFIC_BODYEOB,
    SMFIC_EOH,
    SMFIC_HEADER,
    SMFIC_MACRO,
    SMFIC_MAIL,
    SMFIC_OPTNEG,
    SMFIC_QUIT,
    SMFIR_ACCEPT,
    SMFIR_CHGHEADER,
    SMFIR_CONTINUE,
    SMFIR_OPTNEG,
    SMFIR_REPLBODY,
    pack_milter_packet,
)
from app.backend.signature_engine import render_signature_html, render_signature_text


def _read_milter_packet_sync(reader: asyncio.StreamReader) -> Any:
    async def _coro() -> tuple[int, bytes]:
        raw_len = await reader.readexactly(4)
        (pkt_len,) = struct.unpack("!I", raw_len)
        data = await reader.readexactly(pkt_len)
        return data[0], data[1:]

    return _coro()


async def _milter_exchange(
    port: int,
    *,
    auth_authen: str | None,
    mail_from: str,
    raw_msg: bytes,
) -> dict[str, Any]:
    """Speak real Sendmail/Postfix binary Milter protocol over localhost TCP."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        # 1. Option negotiation
        writer.write(pack_milter_packet(SMFIC_OPTNEG, struct.pack("!III", 6, 0x1F, 0)))
        await writer.drain()
        opt_cmd, opt_payload = await _read_milter_packet_sync(reader)
        assert opt_cmd == SMFIR_OPTNEG
        neg_ver, neg_actions, _ = struct.unpack("!III", opt_payload[:12])
        assert neg_ver == 6
        assert neg_actions & 0x02  # SMFIF_CHGBODY

        # 2. Optional SASL macro `{auth_authen}` (no response expected for SMFIC_MACRO)
        if auth_authen is not None:
            macro_payload = (
                bytes([SMFIC_MAIL])
                + b"{auth_authen}\x00"
                + auth_authen.encode("utf-8")
                + b"\x00"
            )
            writer.write(pack_milter_packet(SMFIC_MACRO, macro_payload))

        # 3. MAIL FROM
        writer.write(pack_milter_packet(SMFIC_MAIL, f"<{mail_from}>\x00".encode("utf-8")))
        await writer.drain()
        mail_cmd, _ = await _read_milter_packet_sync(reader)
        if mail_cmd == SMFIR_ACCEPT:
            writer.write(pack_milter_packet(SMFIC_QUIT))
            await writer.drain()
            return {"early_accept": True, "replbody": None, "final_cmd": mail_cmd}

        assert mail_cmd == SMFIR_CONTINUE

        # Split headers and body from raw RFC5322 message
        sep = b"\r\n\r\n"
        idx = raw_msg.find(sep)
        raw_hdr_block = raw_msg[:idx].decode("utf-8")
        raw_body = raw_msg[idx + 4 :]

        parsed_msg = email.message_from_bytes(raw_msg, policy=email.policy.SMTP)
        for h_name, h_val in parsed_msg.items():
            h_payload = h_name.encode("utf-8") + b"\x00" + str(h_val).encode("utf-8") + b"\x00"
            writer.write(pack_milter_packet(SMFIC_HEADER, h_payload))
            await writer.drain()
            h_cmd, _ = await _read_milter_packet_sync(reader)
            assert h_cmd == SMFIR_CONTINUE

        writer.write(pack_milter_packet(SMFIC_EOH))
        await writer.drain()
        eoh_cmd, _ = await _read_milter_packet_sync(reader)
        assert eoh_cmd == SMFIR_CONTINUE

        writer.write(pack_milter_packet(SMFIC_BODY, raw_body))
        await writer.drain()
        b_cmd, _ = await _read_milter_packet_sync(reader)
        assert b_cmd == SMFIR_CONTINUE

        writer.write(pack_milter_packet(SMFIC_BODYEOB))
        await writer.drain()

        repl_chunks: list[bytes] = []
        changed_headers: list[tuple[str, str]] = []
        while True:
            cmd, payload = await _read_milter_packet_sync(reader)
            if cmd == SMFIR_REPLBODY:
                repl_chunks.append(payload)
            elif cmd == SMFIR_CHGHEADER:
                parts = payload[4:].split(b"\x00")
                changed_headers.append(
                    (parts[0].decode("ascii"), parts[1].decode("utf-8"))
                )
            elif cmd in (SMFIR_CONTINUE, SMFIR_ACCEPT):
                final_cmd = cmd
                break

        writer.write(pack_milter_packet(SMFIC_QUIT))
        await writer.drain()
        return {
            "early_accept": False,
            "replbody": b"".join(repl_chunks) if repl_chunks else None,
            "changed_headers": changed_headers,
            "final_cmd": final_cmd,
            "raw_hdr_block": raw_hdr_block,
        }
    finally:
        writer.close()
        await writer.wait_closed()


def test_extended_settings_and_signature_api_contracts(
    toolbox_env: dict[str, Path],
) -> None:
    headers = {"X-Internal-Token": "internal-test-token-999"}
    with TestClient(app) as client:
        # 1. Default runtime settings
        r_get = client.get("/api/internal/settings", headers=headers)
        assert r_get.status_code == 200
        assert r_get.json()["rspamd_password_set"] is False
        assert "rspamd_password" not in r_get.json()

        # 2. Save settings with rspamd_password -> rspamd_password_set becomes True
        r_put1 = client.put(
            "/api/internal/settings",
            headers=headers,
            json={
                "milter_enabled": True,
                "rspamd_url": "http://127.0.0.1:11334",
                "rspamd_password": "secret-rspamd-pw",
                "digest_enabled": True,
                "digest_time": "07:15",
                "timezone": "Europe/Berlin",
                "admin_alert_email": "it-admin@firma.de",
            },
        )
        assert r_put1.status_code == 200
        assert r_put1.json()["rspamd_password_set"] is True
        assert r_put1.json()["digest_time"] == "07:15"

        # 3. Updating with empty rspamd_password preserves existing secret
        r_put2 = client.put(
            "/api/internal/settings",
            headers=headers,
            json={"rspamd_password": "", "digest_time": "08:00"},
        )
        assert r_put2.status_code == 200
        assert r_put2.json()["rspamd_password_set"] is True
        assert r_put2.json()["digest_time"] == "08:00"

        # 4. Explicit clear_rspamd_password clears it
        r_put3 = client.put(
            "/api/internal/settings",
            headers=headers,
            json={"rspamd_password": "", "clear_rspamd_password": True},
        )
        assert r_put3.json()["rspamd_password_set"] is False

        # 5. Seeded disabled signature exists (English); test CRUD + group precedence + preview
        r_sigs = client.get("/api/internal/signatures", headers=headers)
        assert r_sigs.status_code == 200
        assert len(r_sigs.json()["signatures"]) == 1
        seeded_sig = r_sigs.json()["signatures"][0]
        assert seeded_sig["enabled"] is False
        assert seeded_sig["name"] == "Standard Company Signature"
        assert "Phone:" in seeded_sig["html_template"]
        assert "Mobile:" in seeded_sig["html_template"]
        assert "example.com" in seeded_sig["html_template"]
        assert r_sigs.json()["milter_status"]["listening"] is True

        # Create domain signature and higher-precedence group signature (`employees`)
        r_dom = client.post(
            "/api/internal/signatures",
            headers=headers,
            json={
                "name": "Domain Fallback",
                "scope_type": "domain",
                "scope_value": "firma.de",
                "priority": 10,
                "enabled": True,
                "html_template": "<p>Domain: {{displayName}}</p>",
                "text_template": "",
                "auto_plain_from_html": True,
                "reply_mode": "append_once",
                "placement": "below_reply",
            },
        )
        assert r_dom.status_code == 200

        r_grp = client.post(
            "/api/internal/signatures",
            headers=headers,
            json={
                "name": "Mitarbeiter-Signatur",
                "scope_type": "group",
                "scope_value": "employees",
                "priority": 5,
                "enabled": True,
                "html_template": (
                    "<div><p><strong>{{givenName}} {{sn}}</strong><br>\n"
                    "{{title}} — {{department}}<br>\n"
                    "Tel: {{telephoneNumber}}<br>\n"
                    "Mobil: {{mobile}}<br>\n"
                    "{{company}}</p></div>"
                ),
                "text_template": "",
                "auto_plain_from_html": True,
                "reply_mode": "append_once",
                "placement": "below_reply",
            },
        )
        assert r_grp.status_code == 200
        grp_id = r_grp.json()["signature"]["id"]

        # Preview for `max.muster` -> matches group rule, strips empty `Mobil: {{mobile}}`!
        r_prev = client.post(
            "/api/internal/signatures/preview",
            headers=headers,
            json={"user": "max.muster", "sender_email": "max.muster@firma.de"},
        )
        assert r_prev.status_code == 200
        prev = r_prev.json()
        assert prev["matched_rule_name"] == "Mitarbeiter-Signatur"
        assert "Max Mustermann" in prev["rendered_html"]
        assert "Projektleiter" in prev["rendered_html"]
        assert "+49 89 12345-10" in prev["rendered_html"]
        assert "Mobil:" not in prev["rendered_html"]
        assert "Mobil:" not in prev["rendered_text"]

        # Delete group rule
        r_del = client.post(
            "/api/internal/signatures/delete",
            headers=headers,
            json={"id": grp_id},
        )
        assert r_del.json() == {"deleted": True, "id": grp_id}


def test_smart_empty_ad_line_and_element_stripping() -> None:
    attrs = {
        "givenName": "Max",
        "sn": "Mustermann",
        "displayName": "Max Mustermann",
        "mail": "max.muster@firma.de",
        "telephoneNumber": "+49 89 12345-10",
        "mobile": "",
        "title": "Projektleiter",
        "department": "Vertrieb",
        "company": "Muster GmbH & Co. KG",
    }
    html_tpl = (
        "<table>"
        "<tr><td>Name:</td><td>{{displayName}}</td></tr>"
        "<tr><td>Mobil:</td><td>{{mobile}}</td></tr>"
        "<tr><td>Firma:</td><td>{{company}}</td></tr>"
        "</table>"
        "<p>Mobil-Direkt: {{mobile}}</p>"
        "{{#mobile}}<p>Conditional Mobil: {{mobile}}</p>{{/mobile}}"
    )
    rendered_h = render_signature_html(html_tpl, attrs)
    assert "Max Mustermann" in rendered_h
    assert "Muster GmbH &amp; Co. KG" in rendered_h
    assert "Mobil" not in rendered_h

    text_tpl = (
        "{{displayName}}\n"
        "Tel: {{telephoneNumber}}\n"
        "Mobil: {{mobile}}\n"
        "{{#mobile}}Notfall: {{mobile}}{{/mobile}}\n"
        "{{company}}"
    )
    rendered_t = render_signature_text(text_tpl, attrs)
    assert "Tel: +49 89 12345-10" in rendered_t
    assert "Mobil:" not in rendered_t
    assert "Notfall:" not in rendered_t


def test_binary_milter_multipart_attachment_reply_dedup_and_inbound_bypass(
    toolbox_env: dict[str, Path],
) -> None:
    save_signature_rule(
        rule_id=None,
        name="Corporate Default",
        scope_type="default",
        scope_value="*",
        priority=10,
        enabled=True,
        html_template=(
            "<div><strong>{{givenName}} {{sn}}</strong><br>\n"
            "{{title}}<br>\n"
            "Tel: {{telephoneNumber}}<br>\n"
            "Mobil: {{mobile}}</div>"
        ),
        text_template="",
        auto_plain_from_html=True,
        reply_mode="append_once",
        placement="below_reply",
    )

    # Build multipart/mixed containing multipart/alternative + binary PDF attachment
    pdf_bytes = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    msg = EmailMessage()
    msg["From"] = "Max Mustermann <max.muster@firma.de>"
    msg["To"] = "kunde@extern.de"
    msg["Subject"] = "Angebot Q4"

    alt_body_plain = "Hallo Herr Kunde,\n\nanbei das Angebot.\n\nAm 19.11.2026 schrieb Kunde:\n> Bitte Angebot senden."
    alt_body_html = (
        "<html><body><p>Hallo Herr Kunde, anbei das Angebot.</p>"
        '<blockquote type="cite"><p>Bitte Angebot senden.</p></blockquote>'
        "</body></html>"
    )
    msg.set_content(alt_body_plain)
    msg.add_alternative(alt_body_html, subtype="html")
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename="angebot.pdf",
    )
    raw_msg = msg.as_bytes(policy=email.policy.SMTP)

    async def _run_scenarios() -> None:
        async with MilterServer(host="127.0.0.1", port=0) as srv:
            # Scenario A: Authenticated outgoing multipart mail with PDF attachment
            res_a = await _milter_exchange(
                srv.port,
                auth_authen="max.muster",
                mail_from="max.muster@firma.de",
                raw_msg=raw_msg,
            )
            assert res_a["early_accept"] is False
            assert res_a["final_cmd"] == SMFIR_CONTINUE
            assert res_a["replbody"] is not None

            rebuilt = res_a["raw_hdr_block"].encode("utf-8") + b"\r\n\r\n" + res_a["replbody"]
            signed_msg = email.message_from_bytes(rebuilt, policy=email.policy.SMTP)

            found_html = ""
            found_plain = ""
            found_pdf = b""
            for part in signed_msg.walk():
                if part.is_multipart():
                    continue
                if part.get_content_disposition() == "attachment":
                    found_pdf = part.get_content()
                elif part.get_content_type() == "text/html":
                    found_html = str(part.get_content())
                elif part.get_content_type() == "text/plain":
                    found_plain = str(part.get_content())

            assert found_pdf == pdf_bytes  # PDF attachment 100% intact!
            assert "<!-- ns8-toolbox-sig -->" in found_html
            assert "Max Mustermann" in found_html
            assert "Projektleiter" in found_html
            assert "+49 89 12345-10" in found_html
            assert "Mobil:" not in found_html  # Empty AD mobile stripped!
            # Placed BEFORE <blockquote> (`below_reply`)
            assert found_html.index("ns8-toolbox-sig") < found_html.index("<blockquote")
            assert "Max Mustermann" in found_plain
            assert "Mobil:" not in found_plain

            # Scenario B: Reply-chain containing existing signature -> `append_once` skips!
            res_b = await _milter_exchange(
                srv.port,
                auth_authen="max.muster",
                mail_from="max.muster@firma.de",
                raw_msg=rebuilt,
            )
            assert res_b["replbody"] is None
            assert res_b["final_cmd"] == SMFIR_CONTINUE

            # Scenario C: Unauthenticated inbound mail on port 25 (no {auth_authen}) -> immediate ACCEPT!
            res_c = await _milter_exchange(
                srv.port,
                auth_authen=None,
                mail_from="stranger@internet.org",
                raw_msg=raw_msg,
            )
            assert res_c["early_accept"] is True
            assert res_c["final_cmd"] == SMFIR_ACCEPT
            assert res_c["replbody"] is None

            assert srv.messages_signed == 1
            assert srv.messages_skipped >= 2

    asyncio.run(_run_scenarios())
