# NS8 Mail Toolbox — Application Runtime & Backend (`app/`)

This directory contains the container definition (`app/Containerfile`), the Python 3.12 FastAPI + Asyncio backend (`app/backend/`), and the standalone Vue 3 Secretariat & Quarantine Web Portal (`app/frontend/`, documented in [`./frontend/README.md`](./frontend/README.md)) packaged into the `ghcr.io/bls-isp/mail-toolbox-app` OCI image.

---

## Container Architecture (`app/Containerfile`)

- **Base Image**: `docker.io/library/python:3.12-slim`
- **Exposed Ports**:
  - `8080/tcp` — FastAPI HTTP server serving `/api/*` (Secretariat & Quarantine Portal API), `/api/internal/*` (NS8 Cockpit internal action API), and the static Vue 3 SPA bundle mounted at `/app/static`.
  - `8090/tcp` — Pure-Python `asyncio` binary Postfix/Sendmail Milter server (`MilterServer`) for server-side Before-DKIM corporate signature injection.
- **Persistent Mounts** (bound from `%E/state` by `imageroot/systemd/user/mail-toolbox.service`):
  - `/app/data` — SQLite WAL database (`toolbox.sqlite`) and online Restic backup snapshots (`toolbox-backup.sqlite`).
  - `/app/config` — Runtime discovery files (`discovery_mail.env`, `discovery_ldap.env`), `0600` secrets (`master.creds`, `internal.token`, `toolbox_settings.json`), and JSON caches (`mailboxes_cache.json`, `imapsync_cache.json`).

---

## Backend Module Overview (`app/backend/`)

| File | Responsibility |
| :--- | :--- |
| `main.py` | FastAPI application entrypoint, `lifespan` startup/shutdown manager (SQLite WAL init, `MilterServer` listener on `:8090`, background `reconciler_loop`), all HTTP routes, and path-traversal-safe static SPA serving. |
| `config.py` | Dynamic hot-reload configuration loader merging environment variables, `discovery_mail.env`, `discovery_ldap.env`, `master.creds`, `internal.token`, `toolbox_settings.json`, `mailboxes_cache.json`, and `imapsync_cache.json`. Performs rootless Podman host-loopback translation (`127.0.0.1` / `localhost` -> `CONTAINER_HOST_GATEWAY`) while keeping stored UI URLs clean. |
| `db.py` | SQLite WAL persistence layer (`PRAGMA journal_mode=WAL`), idempotent schema migrations, English-default template & example signature seeding (`default_en` first), and CRUD helpers across all 10 tables (`app_meta`, `vacation_rules`, `templates`, `audit_log`, `signature_templates`, `signature_assignments`, `quarantine_tokens`, `quarantine_digested`, `user_sender_allowlist`, `mail_history_cache`, `mail_connectors`, `connector_seen_uids`, `imapsync_alert_state`). |
| `backup_db.py` | Online atomic SQLite snapshot CLI (`python -m app.backend.backup_db /app/data/toolbox-backup.sqlite`) using `sqlite3.Connection.backup()` for Restic `module-dump-state`. |
| `auth.py` | HMAC-SHA256 signed `HttpOnly` `SameSite=lax` portal session cookies (`ns8_toolbox_session`) with immediate server-side logout revocation, sliding-window per-IP failed login rate limiting (`429`), and constant-time `X-Internal-Token` verification. |
| `ldap_client.py` | Unified OpenLDAP (`rfc2307`) and Samba4 / Windows Active Directory (`ad`) directory client: single-pass bulk group graph builder (zero N+1 queries), AD `1.2.840.113556.1.4.1941` + iterative 10-level cycle-safe BFS nested group traversal, AD `primaryGroupID` resolution via `primaryGroupToken` and binary/string `objectSid` RIDs, full `user` / `DOMAIN\user` / `user@custom-upn` / `mail` bind authentication with RFC 4513 empty-password defense, and built-in AD system group classification (`is_system` + `member_count`). |
| `sieve_client.py` | RFC 5804 ManageSieve TCP client speaking Dovecot 2.4 Master-User SASL PLAIN (`\x00<target_user>*vmail\x00<master_password>`), optional `STARTTLS`, UTF-8 byte-length `{N+}` literal framing, `CHECKSCRIPT` pre-upload validation, and hard guards preventing modification or deletion of SOGo's `sogo` script. |
| `sieve_engine.py` | RFC 5228 Sieve lexer/tokenizer for read-only `sogo.sieve` inspection (`sogo_status`), injection-safe Sieve string escaping, minimal `require` declarations, `currentdate` `ge`/`le`/`allof` date blocks, `:copy` vs direct `redirect` + `stop;`, and conditional `include :personal "sogo";` when SOGo has no personal `vacation` action. |
| `service.py` | Core mailbox & vacation orchestration merging LDAP directory users, `mailboxes_cache.json`, live ManageSieve `sogo_status`, and SQLite `vacation_rules`, plus best-effort uninstall cleanup (`deactivate_all_vacations`). |
| `reconciler.py` | Periodic background scheduler decoupling a **60-second** lightweight local SQLite tick (`check_and_run_scheduled_digest()`, `sync_due_connectors()`, `check_imapsync_bridge_alerts()`) from the **300-second** (5-minute) network ManageSieve/IMAP reconciliation pass (`run_reconcile_pass()`: recovering from SOGo's nightly `00:00` `sogo-tool update-autoreply` cronjob, expiring ended absences, and rescuing allowlisted senders from `Junk`). |
| `signature_engine.py` | Dynamic AD/LDAP placeholder renderer (`{{givenName}}`, `{{sn}}`, `{{displayName}}`, `{{mail}}`, `{{telephoneNumber}}`, `{{mobile}}`, `{{title}}`, `{{department}}`, `{{company}}`) with conditional blocks (`{{#mobile}}...{{/mobile}}`), smart empty-line and HTML-element stripping (`<br>`, `<tr>`, `<li>`, `<p>`, `<div>`), HTML-to-Plaintext conversion, `group` > `domain` > `default` rule precedence, `append_once` / `first_only` / `always` reply-chain deduplication, `below_reply` / `bottom` placement, and attachment-safe multipart MIME rewriting. |
| `milter_server.py` | Pure-Python `asyncio` binary Milter v2–v6 server (`MilterServer`) inspecting `{auth_authen}`: immediately returns `SMFIR_ACCEPT` on unauthenticated port 25 inbound mail and applies `SMFIR_CHGHEADER` + chunked `SMFIR_REPLBODY` + `SMFIR_CONTINUE` on authenticated outgoing mail prior to Rspamd DKIM signing (`127.0.0.1:11332`), failing open on unexpected errors. |
| `email_templates.py` | Unified Carbon-inspired Plaintext + HTML system email builder (`quoted-printable` encoding with short `<200`-char source lines eliminating 998-byte `8bit` HTML tag splitting, RFC 5322 / RFC 3834 `Auto-Submitted` / `X-NS8-Mail-Toolbox` headers, `resolve_sender_domain` stripping internal `ad.`/`ldap.` realms, stacked card-row Quarantine Digests capped at 50 items with Cockpit-only safe fallback, and red-accented Connector & `ns8-imapsync` Failure Alerts). |
| `quarantine_service.py` | Lightweight `BODY.PEEK[HEADER]` Dovecot IMAP Master-User `Junk` inspection with self-digest loop filtering, daily English Carbon HTML + Plaintext Spam Quarantine Digest delivery via direct Dovecot Master-User `INBOX` IMAP `APPEND` (bypassing Rspamd spam scoring, with SMTP fallback), scanner-safe two-step public token release (`GET` read-only inspection + `POST` confirmation moving `Junk` -> `INBOX` via `UID COPY` + `\Deleted` + `EXPUNGE`), and per-user sender allowlist management. |
| `tracking_service.py` | Rspamd Controller `/history` client supporting local `:11334` (`Password` header) and NS8 Traefik `https://<mail-host>/rspamd` routes (`rspamd_user` HTTP Basic Auth, `rspamd_tls_verify=False` self-signed TLS support, URL normalization, and split-DNS / hairpin-NAT host fallback), backed by a persistent deduplicated SQLite `mail_history_cache`. |
| `connector_service.py` | External `imaps` / `imap` / `pop3s` / `pop3` mailbox poller with Fernet password encryption at rest (derived via SHA-256 from `APP_SECRET_KEY`), UID/UIDL deduplication, local SMTP delivery, `ns8-imapsync` bridge status reader (`imapsync_cache.json`), and unified 24-hour-cooldown English multipart HTML + Plaintext admin failure alert emails (delivered directly to local admins' `INBOX` via Master-User IMAP `APPEND` or SMTP fallback) for both native connectors and coupled `ns8-imapsync` tasks. |
| `models.py` | Pydantic v2 request and response validation schemas for all Portal, Public Quarantine, and Internal Cockpit endpoints. |

---

## Configuration & Environment Variables

| Variable / File | Default | Description |
| :--- | :--- | :--- |
| `DATA_DIR` | `/app/data` | Persistent directory containing `toolbox.sqlite` (WAL mode; `${DATA_DIR}/toolbox.sqlite`). |
| `CONFIG_DIR` | `/app/config` | Runtime config directory containing discovery `.env` files, secrets, and JSON caches. |
| `STATIC_DIR` | `/app/static` | Static Vue 3 Secretariat & Quarantine SPA directory (falls back to `app/frontend/dist` in local dev). |
| `APP_SECRET_KEY` | *(generated)* | 32-byte hex secret signing portal session cookies and deriving the Fernet key for connector passwords. |
| `ALLOWED_GROUPS` | `""` | Comma-separated list or JSON array of AD/LDAP groups authorized to sign into the Secretariat Portal. |
| `PORTAL_ENABLED` | `true` (when `PORTAL_HOSTNAME` set) | Indicates whether the public Traefik Secretariat & Quarantine Portal route is enabled; omits portal action buttons from Quarantine Digests when disabled. |
| `PORTAL_HOSTNAME` | `""` | Public Traefik FQDN used to build Quarantine Digest one-click release URLs. |
| `PORTAL_TRACKING_ENABLED` | `false` | Enables read-only Rspamd mail-tracking search inside the Secretariat Portal (`toolbox_settings.json` overrides). |
| `WEB_TCP_PORT` | *(NS8 allocated)* | Host loopback port (`127.0.0.1:${WEB_TCP_PORT}:8080`) proxied by Traefik and NS8 Cockpit actions. |
| `MILTER_TCP_PORT` | *(NS8 allocated)* | Host/WireGuard port (`${MILTER_BIND_HOST}:${MILTER_TCP_PORT}:8090`) for Postfix `POSTFIX_MILTERS`. |
| `MILTER_LISTEN_PORT` | `8090` | Container TCP port where `MilterServer` listens (`0` supported in tests for ephemeral port binding). |
| `MILTER_ENABLED` | `true` | Master toggle for Before-DKIM signature injection (`0` disables starting the Milter listener). |
| `CONTAINER_HOST_GATEWAY` | `""` (`10.0.2.2` in NS8) | Translates `127.0.0.1` / `localhost` targets (`ldapproxy`, local `rspamd`, `mail`) to the NS8 host gateway. |
| `DOVECOT_SPAM_FOLDER` | `Junk` | IMAP folder inspected for quarantined spam messages and allowlist rescues. |
| `IMAPSYNC_CACHE_PATH` | `${CONFIG_DIR}/imapsync_cache.json` | Path to the cached `ns8-imapsync` instance & task snapshot written by `toolbox_imapsync.py`. |
| `RECONCILE_INTERVAL_SECONDS` | `300` | Interval (seconds) for network ManageSieve/IMAP reconciliation; local SQLite checks tick every `min(60, value)`s. |
| `SESSION_TTL_SECONDS` | `43200` | Secretariat Portal session cookie lifetime (12 hours). |
| `LOGIN_RATE_LIMIT_MAX` | `10` | Maximum failed portal login attempts per client IP within `LOGIN_RATE_LIMIT_WINDOW_SECONDS` (`60`s). |
| `SIEVE_STARTTLS` / `SIEVE_TLS_VERIFY` | `false` / `false` | Optional ManageSieve `STARTTLS` upgrade and certificate verification flags. |
| `RSPAMD_URL` / `RSPAMD_USER` / `RSPAMD_TLS_VERIFY` | `http://127.0.0.1:11334` / `""` / `false` | Default Rspamd controller endpoint, optional Traefik Basic Auth username, and TLS verification toggle (persisted in `toolbox_settings.json`). |
| `DIGEST_ENABLED` / `DIGEST_TIME` / `TIMEZONE` | `false` / `07:30` / `Europe/Berlin` | Daily Spam Quarantine Digest schedule settings (persisted in `toolbox_settings.json`). |
| `ADMIN_ALERT_EMAIL` | `""` | Recipient email for 24h-rate-limited external POP3/IMAP connector and `ns8-imapsync` failure alerts. |

---

## HTTP API Endpoint Groups (Port `8080`)

### 1. Health & Static SPA
- `GET /api/health` — Readiness probe (`{"status": "ok"}`) polled by `imageroot/bin/wait-healthy`.
- `GET /{full_path}` — Serves static assets from `/app/static` with SPA `index.html` fallback for non-`/api/*` routes.

### 2. Secretariat Web Portal Auth (`/api/auth/*`)
- `POST /api/auth/login` — Authenticates via LDAP/AD bind, enforces `ALLOWED_GROUPS` membership (`401` invalid credentials, `403` unauthorized group, `429` rate-limited), and sets the signed session cookie.
- `GET /api/auth/me` — Validates active session cookie and returns `username`, `display_name`, `groups`, and `portal_tracking_enabled`.
- `POST /api/auth/logout` — Revokes session token server-side and clears cookie.

### 3. Authenticated Secretariat Portal API (Session Cookie Required)
- `GET /api/mailboxes` & `PUT /api/mailboxes/{user}/vacation` — Lists merged mailboxes + KPI summary and manages Dovecot `toolbox-vacation` Sieve scripts (`updated_by` enforced from authenticated session user).
- `GET /api/templates` & `PUT /api/templates` — Reads and updates out-of-office message templates.
- `GET /api/audit` — Returns recent audit log entries.
- `POST /api/mail-tracking/search` — Read-only Rspamd/SQLite mail history search (returns `403` unless `portal_tracking_enabled` is `true`).

### 4. Public Self-Service Quarantine Release (`/api/quarantine/*` — No Login Required)
- `GET /api/quarantine/token/{token}` — **Read-only** token inspection (`valid`, `already_used`, `expired`, message metadata); never consumes the token so automated email security link scanners cannot trigger premature releases.
- `POST /api/quarantine/action` — Consumes token on explicit user confirmation (`release` or `release_and_allow`), moves the message from `Junk` to `INBOX` via Dovecot Master-User IMAP, optionally adds the sender to `user_sender_allowlist`, and logs to `audit_log`.

### 5. Internal NS8 Cockpit Action API (`/api/internal/*` — `X-Internal-Token` Required)
- **Settings & Lifecycle**: `GET /api/internal/settings`, `PUT /api/internal/settings`, `POST /api/internal/reconcile`, `POST /api/internal/deactivate-all-vacations`, `GET /api/internal/groups`.
- **Mailboxes, Templates & Audit**: `GET /api/internal/mailboxes`, `PUT /api/internal/mailboxes/{user}/vacation`, `GET /api/internal/templates`, `PUT /api/internal/templates`, `GET /api/internal/audit`.
- **Corporate Signatures & Milter**: `GET /api/internal/signatures`, `POST /api/internal/signatures`, `POST /api/internal/signatures/delete`, `POST /api/internal/signatures/preview`.
- **Quarantine & Mail Tracking**: `GET /api/internal/quarantine`, `POST /api/internal/quarantine/release`, `POST /api/internal/quarantine/run-digest`, `POST /api/internal/allowlist/delete`, `POST /api/internal/mail-tracking/search`, `POST /api/internal/mail-tracking/test-rspamd`.
- **External Connectors & IMAP-Sync Bridge**: `GET /api/internal/connectors` (returns `connectors` + `imapsync_bridge`), `POST /api/internal/connectors`, `POST /api/internal/connectors/delete`, `POST /api/internal/connectors/test`, `POST /api/internal/connectors/sync`.

---

## Running Backend Tests Locally

From the repository root:

```bash
pip install -r app/backend/requirements.txt
pytest app/backend/tests -v
```

The backend test suite (`app/backend/tests/`) runs completely offline using an in-process threaded TCP RFC 5804 ManageSieve mock server (including `STARTTLS`), `ldap3` `MOCK_SYNC` OpenLDAP & Active Directory servers, a binary Milter protocol test harness, an HTTPS X.509 mock Rspamd server, and in-memory IMAP/POP3/SMTP stores.
