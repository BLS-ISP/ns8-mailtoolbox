"""Pydantic request and response schemas for NS8 Mail Toolbox HTTP API (Phases 1–3)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=512)


class AuthUserResponse(BaseModel):
    authenticated: bool = True
    username: str
    display_name: str
    groups: list[str] = Field(default_factory=list)
    portal_tracking_enabled: bool = False


class SogoStatusModel(BaseModel):
    has_sogo_script: bool = False
    is_active: bool = False
    vacation_enabled: bool = False
    forward_enabled: bool = False
    forward_address: str = ""


class VacationStateModel(BaseModel):
    enabled: bool = False
    active_now: bool = False
    start_date: str = ""
    end_date: str = ""
    subject: str = ""
    body: str = ""
    forward_enabled: bool = False
    forward_address: str = ""
    keep_copy: bool = True
    updated_by: str = ""
    updated_at: str = ""


class MailboxModel(BaseModel):
    user: str
    display_name: str
    given_name: str = ""
    sn: str = ""
    mail: str
    aliases: list[str] = Field(default_factory=list)
    department: str = ""
    title: str = ""
    enabled: bool = True
    postfix_forwards: list[str] = Field(default_factory=list)
    sogo_status: SogoStatusModel = Field(default_factory=SogoStatusModel)
    vacation: VacationStateModel = Field(default_factory=VacationStateModel)


class MailboxesSummaryModel(BaseModel):
    total: int = 0
    central_vacation_active: int = 0
    sogo_vacation_active: int = 0
    forwards_active: int = 0


class MailboxesListResponse(BaseModel):
    mailboxes: list[MailboxModel]
    summary: MailboxesSummaryModel


class VacationUpdateRequest(BaseModel):
    user: str | None = None
    enabled: bool
    start_date: str | None = ""
    end_date: str | None = ""
    subject: str = ""
    body: str = ""
    forward_enabled: bool = False
    forward_address: str = ""
    keep_copy: bool = True
    updated_by: str | None = None


class TemplateItemModel(BaseModel):
    id: str | None = None
    name: str = Field(..., min_length=1, max_length=200)
    lang: str = Field(default="de", max_length=16)
    is_default: bool = False
    subject: str = ""
    body: str = ""


class TemplatesPayload(BaseModel):
    templates: list[TemplateItemModel]


class AuditEntryModel(BaseModel):
    id: int
    timestamp: str
    actor: str
    source: str
    target_user: str
    action: str
    details: str


class AuditListResponse(BaseModel):
    entries: list[AuditEntryModel]


class ReconcileResponse(BaseModel):
    reconciled: int
    expired: int
    reactivated: int


# ---------------------------------------------------------------------------
# Extended Module Runtime Settings
# ---------------------------------------------------------------------------


class RuntimeSettingsUpdateRequest(BaseModel):
    milter_enabled: bool | None = None
    rspamd_url: str | None = None
    rspamd_password: str | None = None
    clear_rspamd_password: bool = False
    digest_enabled: bool | None = None
    digest_time: str | None = None
    timezone: str | None = None
    admin_alert_email: str | None = None
    portal_tracking_enabled: bool | None = None


# ---------------------------------------------------------------------------
# Phase 2: Signatures & Before-DKIM Milter
# ---------------------------------------------------------------------------


class SignatureRuleSaveRequest(BaseModel):
    id: int | None = None
    name: str = Field(..., min_length=1, max_length=200)
    scope_type: str = "default"
    scope_value: str = "*"
    priority: int = 10
    enabled: bool = True
    html_template: str = ""
    text_template: str = ""
    auto_plain_from_html: bool = True
    reply_mode: str = "append_once"
    placement: str = "below_reply"


class IdDeleteRequest(BaseModel):
    id: int


class SignaturePreviewRequest(BaseModel):
    user: str
    sender_email: str = ""
    html_template: str | None = None
    text_template: str | None = None
    auto_plain_from_html: bool = True


# ---------------------------------------------------------------------------
# Phase 3a: Spam Quarantine Digest & Scanner-Safe Release
# ---------------------------------------------------------------------------


class QuarantinePublicActionRequest(BaseModel):
    token: str
    op: str = "release"


class QuarantineAdminReleaseRequest(BaseModel):
    user: str
    uid: str = ""
    message_id: str = ""
    allow_sender: bool = False


class QuarantineRunDigestRequest(BaseModel):
    user: str = ""


# ---------------------------------------------------------------------------
# Phase 3b: Fast Mail-Tracking Search (Rspamd History)
# ---------------------------------------------------------------------------


class MailTrackingSearchRequest(BaseModel):
    sender: str = ""
    recipient: str = ""
    subject: str = ""
    action_filter: str = "all"
    date_from: str = ""
    date_to: str = ""
    limit: int = Field(default=200, ge=1, le=1000)


class RspamdTestRequest(BaseModel):
    rspamd_url: str | None = None
    rspamd_password: str | None = None


# ---------------------------------------------------------------------------
# Phase 3c: External POP3/IMAP Connectors
# ---------------------------------------------------------------------------


class ConnectorSaveRequest(BaseModel):
    id: int | None = None
    name: str = Field(..., min_length=1, max_length=200)
    target_user: str = ""
    target_email: str = Field(..., min_length=3, max_length=256)
    protocol: str = "imaps"
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    remote_user: str = Field(..., min_length=1, max_length=256)
    remote_password: str = ""
    interval_minutes: int = Field(default=5, ge=1, le=1440)
    leave_on_server: bool = True
    enabled: bool = True


class ConnectorTestRequest(BaseModel):
    id: int | None = None
    protocol: str = "imaps"
    host: str
    port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    remote_user: str
    remote_password: str = ""


class ConnectorSyncRequest(BaseModel):
    id: int
