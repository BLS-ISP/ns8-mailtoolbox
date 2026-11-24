"""Unified Carbon-inspired HTML + Plaintext system email builder for NS8 Mail Toolbox.

Eliminates 998-byte 8bit HTML line-splitting corruption by emitting clean short HTML lines
encoded with lossless `quoted-printable`, sets full RFC 5322 / RFC 3834 auto-generated
headers, resolves real mail domains (stripping internal `ad.` / `ldap.` realms), and renders
SOGo / Outlook / Apple Mail / Thunderbird / Mobile-safe stacked card layouts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
import email.policy
from email.utils import format_datetime, make_msgid, parseaddr, parsedate_to_datetime
import html
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.backend.config import AppSettings

MAX_DIGEST_DISPLAY_ITEMS = 50


def _clean_mail_domain_candidate(raw_domain: str) -> str:
    dom = (raw_domain or "").strip().lower().strip(".")
    if not dom or dom in ("localhost", "localhost.localdomain") or "." not in dom:
        return ""
    if dom.startswith(("ad.", "ldap.")) and dom.count(".") >= 2:
        return ""
    return dom


def _extract_domain_from_address(addr: str) -> str:
    parsed = parseaddr(addr or "")[1].strip() or (addr or "").strip()
    if "@" not in parsed:
        return ""
    return parsed.split("@", 1)[1].strip().lower()


def resolve_sender_domain(
    recipient_email: str,
    settings: AppSettings,
    base_map: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Resolve clean public mail domain for From/Message-ID (never `ad.<domain>` when avoidable)."""
    rcpt_dom = _clean_mail_domain_candidate(_extract_domain_from_address(recipient_email))
    if rcpt_dom:
        return rcpt_dom

    alert_dom = _clean_mail_domain_candidate(
        _extract_domain_from_address(settings.runtime.admin_alert_email)
    )
    if alert_dom:
        return alert_dom

    if base_map:
        for meta in base_map.values():
            cand = _clean_mail_domain_candidate(_extract_domain_from_address(str(meta.get("mail") or "")))
            if cand:
                return cand
            for alias in meta.get("aliases") or []:
                alias_cand = _clean_mail_domain_candidate(_extract_domain_from_address(str(alias)))
                if alias_cand:
                    return alias_cand

    ldap_dom = (settings.ldap.domain or "").strip().lower().strip(".")
    if ldap_dom.startswith(("ad.", "ldap.")) and ldap_dom.count(".") >= 2:
        stripped = ldap_dom.split(".", 1)[1].strip()
        if stripped and stripped != "localhost":
            return stripped
    if ldap_dom and ldap_dom != "localhost":
        return ldap_dom

    portal_dom = (settings.portal_hostname or "").strip().lower().strip(".")
    if portal_dom and portal_dom not in ("localhost", "127.0.0.1"):
        return portal_dom

    return ldap_dom or "localhost"


def format_local_timestamp(
    raw_iso_or_epoch: str | int | float | None,
    tz_name: str = "Europe/Berlin",
) -> str:
    """Format UTC ISO string, RFC 2822 date, or Unix epoch into `24 Nov 2026, 07:41 CET`."""
    if raw_iso_or_epoch is None or raw_iso_or_epoch == "":
        return "Unknown"

    try:
        tz = ZoneInfo((tz_name or "Europe/Berlin").strip())
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc

    dt: datetime | None = None
    if isinstance(raw_iso_or_epoch, (int, float)) and not isinstance(raw_iso_or_epoch, bool):
        if float(raw_iso_or_epoch) <= 0:
            return "Unknown"
        try:
            dt = datetime.fromtimestamp(float(raw_iso_or_epoch), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            dt = None
    else:
        raw_str = str(raw_iso_or_epoch).strip()
        if not raw_str:
            return "Unknown"
        if raw_str.isdigit():
            try:
                val = int(raw_str)
                if val > 0:
                    dt = datetime.fromtimestamp(val, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                dt = None
        if dt is None:
            try:
                parsed_iso = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
                if parsed_iso.tzinfo is None:
                    parsed_iso = parsed_iso.replace(tzinfo=timezone.utc)
                dt = parsed_iso
            except ValueError:
                try:
                    parsed_rfc = parsedate_to_datetime(raw_str)
                    if parsed_rfc.tzinfo is None:
                        parsed_rfc = parsed_rfc.replace(tzinfo=timezone.utc)
                    dt = parsed_rfc
                except Exception:
                    return raw_str

    if dt is None:
        return str(raw_iso_or_epoch)

    local_dt = dt.astimezone(tz)
    tz_abbr = local_dt.tzname() or ""
    formatted = local_dt.strftime("%d %b %Y, %H:%M")
    return f"{formatted} {tz_abbr}".strip()


def _truncate_text(text: str, max_chars: int = 180) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _score_badge_html(score: float | None) -> str:
    if score is None:
        bg, fg, border, label = "#f4f4f4", "#525252", "#d0d7de", "Score -"
    elif float(score) >= 8.0:
        bg, fg, border, label = "#fff1f1", "#da1e28", "#ffb3b8", f"Score {float(score):.1f}"
    else:
        bg, fg, border, label = "#fcf4d6", "#8e6a00", "#f1c21b", f"Score {float(score):.1f}"

    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;\n'
        f"             font-size:11px;font-weight:600;white-space:nowrap;\n"
        f'             background-color:{bg};color:{fg};border:1px solid {border};">'
        f"{html.escape(label)}</span>"
    )


def _serialize_system_email(
    *,
    from_display_name: str,
    sender_domain: str,
    recipient_email: str,
    subject: str,
    plain_body: str,
    html_body: str,
    toolbox_category: str,
) -> tuple[bytes, str]:
    """Build an RFC 5322 multipart/alternative message using lossless `quoted-printable`."""
    safe_domain = (sender_domain or "localhost").strip() or "localhost"
    envelope_from = f"noreply@{safe_domain}"
    now_utc = datetime.now(timezone.utc)

    msg = EmailMessage()
    msg["From"] = f"{from_display_name} <{envelope_from}>"
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg["Date"] = format_datetime(now_utc)
    msg["Message-ID"] = make_msgid(domain=safe_domain)
    msg["Auto-Submitted"] = "auto-generated"
    msg["Precedence"] = "bulk"
    msg["X-Auto-Response-Suppress"] = "All"
    msg["X-NS8-Mail-Toolbox"] = toolbox_category

    msg.set_content(
        plain_body,
        subtype="plain",
        charset="utf-8",
        cte="quoted-printable",
    )
    msg.add_alternative(
        html_body,
        subtype="html",
        charset="utf-8",
        cte="quoted-printable",
    )
    smtp_policy = email.policy.SMTP.clone(max_line_length=250)
    return msg.as_bytes(policy=smtp_policy), envelope_from


def build_quarantine_digest_email(
    *,
    recipient_email: str,
    sender_domain: str,
    portal_enabled: bool,
    portal_hostname: str,
    tz_name: str,
    displayed_items_with_tokens: list[tuple[dict[str, Any], str | None]],
    total_new_count: int,
) -> tuple[bytes, str]:
    """Render responsive Carbon-styled stacked card Spam Quarantine Digest (Plaintext + HTML)."""
    clean_portal_host = (portal_hostname or "").strip()
    has_portal_links = bool(
        portal_enabled
        and clean_portal_host
        and clean_portal_host.lower() not in ("localhost", "127.0.0.1", "::1")
    )
    base_url = f"https://{clean_portal_host}".rstrip("/") if has_portal_links else ""

    generated_str = format_local_timestamp(datetime.now(timezone.utc).isoformat(), tz_name)
    older_omitted = max(0, int(total_new_count) - len(displayed_items_with_tokens))

    plain_lines: list[str] = [
        "NS8 MAIL TOOLBOX - QUARANTINE SUMMARY",
        "Spam Quarantine Digest",
        "======================",
        f"Mailbox:   {recipient_email}",
        f"Generated: {generated_str}",
        f"Summary:   {total_new_count} new quarantined message(s) in your Spam folder",
        "",
    ]

    card_rows_html: list[str] = []
    for idx, (item, token) in enumerate(displayed_items_with_tokens):
        row_bg = "#ffffff" if idx % 2 == 0 else "#f8f9fa"
        raw_subj = str(item.get("subject") or "(No Subject)")
        subj_short = _truncate_text(raw_subj, 180)
        sender_disp = _truncate_text(
            str(item.get("sender_display") or item.get("sender") or "Unknown sender"),
            160,
        )
        local_recv = format_local_timestamp(item.get("received_at"), tz_name)
        score_val = item.get("spam_score")
        score_plain = f"{float(score_val):.1f}" if score_val is not None else "-"
        badge_html = _score_badge_html(score_val)

        plain_lines.extend(
            [
                f"- Subject:  {subj_short}",
                f"  From:     {sender_disp}",
                f"  Received: {local_recv} (Score: {score_plain})",
            ]
        )

        if has_portal_links and token:
            rel_url = f"{base_url}/?quarantine_token={token}&op=release"
            allow_url = f"{base_url}/?quarantine_token={token}&op=release_and_allow"
            plain_lines.append(f"  Deliver to Inbox: {rel_url}")
            plain_lines.append(f"  Deliver & Allow Sender: {allow_url}")
            esc_rel = html.escape(rel_url, quote=True)
            esc_allow = html.escape(allow_url, quote=True)
            actions_html = (
                '      <div style="margin-top:10px;">\n'
                f'        <a href="{esc_rel}"\n'
                '           style="display:inline-block;padding:6px 12px;margin:0 8px 4px 0;\n'
                '                  background-color:#0f62fe;color:#ffffff;border:1px solid #0f62fe;\n'
                '                  border-radius:4px;font-size:12px;font-weight:600;text-decoration:none;">'
                "Deliver to Inbox</a>\n"
                f'        <a href="{esc_allow}"\n'
                '           style="display:inline-block;padding:6px 12px;margin:0 0 4px 0;\n'
                '                  background-color:#edf5ff;color:#0043ce;border:1px solid #a6c8ff;\n'
                '                  border-radius:4px;font-size:12px;font-weight:600;text-decoration:none;">'
                "Deliver &amp; Allow Sender</a>\n"
                "      </div>"
            )
        else:
            plain_lines.append(
                "  Action:   Review your Spam folder in Webmail or IMAP to move this message to your Inbox."
            )
            actions_html = (
                '      <div style="margin-top:8px;font-size:12px;color:#6f6f6f;">\n'
                "        Review your <strong>Spam</strong> folder in Webmail or IMAP\n"
                "        to move this message to your Inbox.\n"
                "      </div>"
            )

        plain_lines.append("")

        card_rows_html.extend(
            [
                "  <tr>",
                f'    <td style="padding:14px 20px;border-bottom:1px solid #e0e0e0;background-color:{row_bg};">',
                '      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"',
                '             style="width:100%;table-layout:fixed;border-collapse:collapse;">',
                "        <tr>",
                '          <td width="80%" valign="top"',
                '              style="width:80%;padding-right:10px;font-size:14px;font-weight:600;',
                '                     line-height:1.4;color:#161616;word-break:break-word;overflow-wrap:anywhere;">',
                f"            {html.escape(subj_short)}",
                "          </td>",
                '          <td width="20%" valign="top" align="right" style="width:20%;text-align:right;">',
                f"            {badge_html}",
                "          </td>",
                "        </tr>",
                "      </table>",
                '      <div style="margin-top:6px;font-size:12px;line-height:1.5;color:#525252;',
                '                  word-break:break-word;overflow-wrap:anywhere;">',
                f"        <strong>From:</strong> {html.escape(sender_disp)} &middot;",
                f"        <strong>Received:</strong> {html.escape(local_recv)}",
                "      </div>",
                actions_html,
                "    </td>",
                "  </tr>",
            ]
        )

    overflow_html_lines: list[str] = []
    if older_omitted > 0:
        plain_lines.append(f"+ {older_omitted} older quarantined messages in your Spam folder.")
        plain_lines.append("")
        overflow_html_lines = [
            "  <tr>",
            '    <td style="padding:12px 20px;background-color:#edf5ff;border-bottom:1px solid #d0e2ff;',
            '               font-size:12px;color:#0043ce;font-weight:600;">',
            f"      + {older_omitted} older quarantined messages in your Spam folder",
            "    </td>",
            "  </tr>",
        ]

    if has_portal_links:
        footer_note = (
            "Release links remain valid for 7 days and use scanner-safe two-step confirmation "
            "so automated security scanners cannot release messages prematurely."
        )
    else:
        footer_note = (
            "Generated automatically by NS8 Mail Toolbox. Manage quarantined messages directly "
            "via your Webmail or IMAP client."
        )
    plain_lines.append(footer_note)

    html_lines: list[str] = [
        '<div style="background-color:#f4f4f4;padding:20px 12px;margin:0;',
        "            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#161616;\">",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"',
        '       style="max-width:680px;width:100%;margin:0 auto;table-layout:fixed;border-collapse:collapse;',
        '              background-color:#ffffff;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">',
        "  <tr>",
        '    <td style="background-color:#161616;border-top:4px solid #0f62fe;padding:20px 24px;color:#ffffff;">',
        '      <div style="font-size:11px;font-weight:600;letter-spacing:0.08em;color:#78a9ff;">',
        "        NS8 MAIL TOOLBOX &middot; QUARANTINE SUMMARY",
        "      </div>",
        '      <div style="font-size:20px;font-weight:600;margin:6px 0 10px 0;color:#ffffff;">',
        "        Spam Quarantine Digest",
        "      </div>",
        '      <div style="font-size:12px;line-height:1.6;color:#c6c6c6;word-break:break-word;">',
        '        <span style="display:inline-block;padding:2px 8px;background-color:#262626;',
        '                     border:1px solid #393939;border-radius:4px;color:#f4f4f4;margin-right:8px;">',
        f"          {html.escape(recipient_email)}</span>",
        f"        <span>{html.escape(generated_str)}</span>",
        "      </div>",
        '      <div style="margin-top:10px;font-size:13px;color:#f4f4f4;">',
        f"        You have <strong>{int(total_new_count)}</strong> new quarantined message(s) in your Spam folder.",
        "      </div>",
        "    </td>",
        "  </tr>",
        *card_rows_html,
        *overflow_html_lines,
        "  <tr>",
        '    <td style="padding:14px 20px;background-color:#f4f4f4;font-size:11px;line-height:1.5;color:#525252;">',
        f"      {html.escape(footer_note)}",
        "    </td>",
        "  </tr>",
        "</table>",
        "</div>",
    ]

    subject = f"Spam Quarantine Digest ({int(total_new_count)} new message(s))"
    return _serialize_system_email(
        from_display_name="Mail Toolbox Quarantine",
        sender_domain=sender_domain,
        recipient_email=recipient_email,
        subject=subject,
        plain_body="\n".join(plain_lines) + "\n",
        html_body="\n".join(html_lines) + "\n",
        toolbox_category="quarantine-digest",
    )


def _build_red_alert_html(
    *,
    kicker: str,
    title: str,
    subtitle: str,
    kv_rows: list[tuple[str, str]],
    error_box_title: str,
    error_box_text: str,
    footer_guidance: str,
) -> str:
    table_rows: list[str] = []
    for k_label, v_val in kv_rows:
        table_rows.extend(
            [
                "        <tr>",
                '          <td width="34%" valign="top"',
                '              style="width:34%;padding:7px 10px 7px 0;border-bottom:1px solid #e8e8e8;',
                '                     font-size:12px;font-weight:600;color:#525252;">',
                f"            {html.escape(k_label)}",
                "          </td>",
                '          <td width="66%" valign="top"',
                '              style="width:66%;padding:7px 0;border-bottom:1px solid #e8e8e8;',
                '                     font-size:13px;color:#161616;word-break:break-word;overflow-wrap:anywhere;">',
                f"            {html.escape(v_val)}",
                "          </td>",
                "        </tr>",
            ]
        )

    esc_kicker = html.escape(kicker).replace(" · ", " &middot; ")
    lines: list[str] = [
        '<div style="background-color:#f4f4f4;padding:20px 12px;margin:0;',
        "            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#161616;\">",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"',
        '       style="max-width:640px;width:100%;margin:0 auto;table-layout:fixed;border-collapse:collapse;',
        '              background-color:#ffffff;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">',
        "  <tr>",
        '    <td style="background-color:#161616;border-top:4px solid #da1e28;padding:18px 24px;color:#ffffff;">',
        '      <div style="font-size:11px;font-weight:600;letter-spacing:0.08em;color:#ff8389;">',
        f"        {esc_kicker}",
        "      </div>",
        '      <div style="font-size:19px;font-weight:600;margin:6px 0 6px 0;color:#ffffff;word-break:break-word;">',
        f"        {html.escape(title)}",
        "      </div>",
        '      <div style="font-size:12px;color:#c6c6c6;">',
        f"        {html.escape(subtitle)}",
        "      </div>",
        "    </td>",
        "  </tr>",
        "  <tr>",
        '    <td style="padding:18px 24px;">',
        '      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"',
        '             style="width:100%;table-layout:fixed;border-collapse:collapse;">',
        *table_rows,
        "      </table>",
        '      <div style="margin-top:16px;padding:12px 14px;background-color:#fff1f1;',
        '                  border:1px solid #ffb3b8;border-left:4px solid #da1e28;border-radius:4px;">',
        '        <div style="font-size:11px;font-weight:600;color:#a2191f;margin-bottom:4px;">',
        f"          {html.escape(error_box_title)}",
        "        </div>",
        '        <div style="font-family:Consolas,Menlo,Monaco,monospace;font-size:12px;line-height:1.5;',
        '                    color:#750e13;word-break:break-word;overflow-wrap:anywhere;">',
        f"          {html.escape(error_box_text)}",
        "        </div>",
        "      </div>",
        '      <div style="margin-top:14px;font-size:13px;line-height:1.5;color:#393939;">',
        f"        {html.escape(footer_guidance)}",
        "      </div>",
        "    </td>",
        "  </tr>",
        "  <tr>",
        '    <td style="padding:12px 24px;background-color:#f4f4f4;font-size:11px;color:#525252;">',
        "      Duplicate alerts for this connector/task are suppressed for 24 hours until the next successful sync.",
        "    </td>",
        "  </tr>",
        "</table>",
        "</div>",
    ]
    return "\n".join(lines) + "\n"


def build_connector_failure_alert_email(
    *,
    conn_row: dict[str, Any],
    error_msg: str,
    failure_count: int,
    recipient_email: str,
    sender_domain: str,
    tz_name: str = "Europe/Berlin",
) -> tuple[bytes, str]:
    """Build Plaintext + HTML red-accented External Connector Failure Alert email."""
    conn_name = str(conn_row.get("name") or "Connector")
    conn_id = conn_row.get("id", "")
    remote_acct = f"{conn_row.get('remote_user', '')} @ {conn_row.get('host', '')}:{conn_row.get('port', '')} ({conn_row.get('protocol', '')})"
    target_mb = str(conn_row.get("target_email") or "")
    detected_at = format_local_timestamp(datetime.now(timezone.utc).isoformat(), tz_name)

    subject = f"[NS8 Mail Toolbox] External Mail Connector Alert: {conn_name}"
    plain_body = (
        "Warning: External POP3/IMAP mail connector synchronization failed.\n\n"
        f"Connector:            {conn_name} (#{conn_id})\n"
        f"Remote Account:       {remote_acct}\n"
        f"Target Mailbox:       {target_mb}\n"
        f"Consecutive Failures: {failure_count}\n"
        f"Detected At:          {detected_at}\n"
        f"Error Message:        {error_msg}\n\n"
        "Please check NS8 Cockpit -> Mail Toolbox -> Connectors to verify whether the external "
        "account password or server settings have changed.\n"
        "Duplicate alerts for this connector are suppressed for 24 hours until recovery.\n"
    )
    html_body = _build_red_alert_html(
        kicker="NS8 MAIL TOOLBOX · CONNECTOR ALERT",
        title=f"External Connector Failed: {conn_name}",
        subtitle=f"Detected at {detected_at}",
        kv_rows=[
            ("Connector", f"{conn_name} (#{conn_id})"),
            ("Remote Account", remote_acct),
            ("Target Mailbox", target_mb),
            ("Consecutive Failures", str(failure_count)),
        ],
        error_box_title="REPORTED ERROR",
        error_box_text=error_msg or "Unknown connection or authentication failure",
        footer_guidance=(
            "Please open NS8 Cockpit -> Mail Toolbox -> External Connectors to verify "
            "whether the external ISP password or host settings have changed."
        ),
    )
    return _serialize_system_email(
        from_display_name="NS8 Mail Toolbox",
        sender_domain=sender_domain,
        recipient_email=recipient_email,
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        toolbox_category="connector-alert",
    )


def build_imapsync_failure_alert_email(
    *,
    module_id: str,
    ui_name: str,
    task: dict[str, Any],
    exit_code: int,
    last_ts: int | None,
    recipient_email: str,
    sender_domain: str,
    tz_name: str = "Europe/Berlin",
) -> tuple[bytes, str]:
    """Build Plaintext + HTML red-accented NS8 IMAP-Sync Task Failure Alert email."""
    task_id = str(task.get("task_id") or "")
    localuser = str(task.get("localuser") or "")
    remoteuser = str(task.get("remoteuser") or "")
    remotehost = str(task.get("remotehost") or "")
    remoteport = task.get("remoteport", 993)
    security = str(task.get("security") or "plain")
    task_label = str(task.get("name") or f"{remoteuser} -> {localuser}").strip()
    mod_display = f"{module_id} ({ui_name})" if ui_name else module_id
    last_sync_str = format_local_timestamp(last_ts, tz_name) if last_ts else "Unknown"

    subject = f"[NS8 Mail Toolbox] NS8 IMAP-Sync Task Failure Alert: {task_label}"
    plain_body = (
        "Warning: An NS8 IMAP-Sync (ns8-imapsync) synchronization task reported a non-zero exit code.\n\n"
        f"IMAP-Sync Module: {mod_display}\n"
        f"Task:             {task_label} ({task_id})\n"
        f"Remote Account:   {remoteuser} @ {remotehost}:{remoteport} ({security})\n"
        f"Local Mailbox:    {localuser}\n"
        f"Last Exit Code:   {exit_code}\n"
        f"Last Sync Time:   {last_sync_str}\n\n"
        f"Please inspect the task log in NS8 Cockpit under Mail Toolbox -> Connectors or {module_id} -> Tasks.\n"
        "Duplicate alerts for this task are suppressed for 24 hours until recovery.\n"
    )
    html_body = _build_red_alert_html(
        kicker="NS8 MAIL TOOLBOX · IMAP-SYNC BRIDGE ALERT",
        title=f"IMAP-Sync Task Failed: {task_label}",
        subtitle=f"Module {mod_display}",
        kv_rows=[
            ("IMAP-Sync Module", mod_display),
            ("Task", f"{task_label} ({task_id})" if task_id else task_label),
            ("Remote Account", f"{remoteuser} @ {remotehost}:{remoteport} ({security})"),
            ("Local Mailbox", localuser),
            ("Last Sync Time", last_sync_str),
        ],
        error_box_title="NON-ZERO EXIT STATUS",
        error_box_text=f"imapsync exited with status code {exit_code}",
        footer_guidance=(
            f"Please inspect the synchronization log in NS8 Cockpit under "
            f"Mail Toolbox -> External Connectors (Log button) or {module_id} -> Tasks."
        ),
    )
    return _serialize_system_email(
        from_display_name="NS8 Mail Toolbox",
        sender_domain=sender_domain,
        recipient_email=recipient_email,
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        toolbox_category="imapsync-alert",
    )
