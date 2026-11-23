# NS8 Mail Toolbox — Verification & End-to-End Test Suites (`tests/`)

This directory contains the **repository-wide NS8 static architecture & contract audit (`tests/verify_ns8_rules.py`)**, **in-process RFC wire-protocol mock servers**, and **full-stack end-to-end (E2E) integration suites** for **NS8 Mail Toolbox (`ns8-mail-toolbox`)**.

Together with the backend unit and component test suites under [`../app/backend/tests/`](../app/backend/tests/), the test harness runs **100% offline** in CI and local development without requiring Docker, Podman, a live NethServer 8 cluster, or external network access.

---

## Directory Layout

```text
tests/
├── README.md                                     # Test architecture & CLI reference (this file)
├── conftest.py                                   # Repo-root sys.path setup & verify_ns8_rules.py collector hook
├── verify_ns8_rules.py                           # Static NS8 architecture, AST, JSONSchema, UI, i18n & docs audit
├── _mock_managesieve.py                          # Threaded TCP RFC 5804 Dovecot 2.4 ManageSieve server (user*vmail)
├── _mock_phase23_servers.py                      # Binary Milter client + TCP SMTP, IMAP, POP3 & HTTPS X.509 Rspamd mocks
├── test_e2e_smoke.py                             # Phase 1 E2E: Secretariat Portal SPA, LDAP, ManageSieve, SOGo & backup
├── test_phase2_phase3_e2e.py                     # Phases 2–3 + v1.0.1 E2E: Milter, Quarantine, Rspamd TLS/Auth, Connectors & AD
├── test_v104_imapsync_bridge_e2e.py              # v1.0.4 E2E: NS8 IMAP-Sync (ns8-imapsync) bridge, Node JS & 24h SMTP alerts
└── test_v105_notification_and_cache_ttl_e2e.py   # v1.0.5 E2E: Silent NS8 sub-tasks & 300s/3600s smart TTL caching
```

Companion backend unit and service test suites in [`../app/backend/tests/`](../app/backend/tests/):

| Test Module | Coverage Scope |
| :--- | :--- |
| `test_sieve_and_sogo.py` | RFC 5228 Sieve generation, injection neutralization, `:copy` vs direct `redirect` + `stop;`, date bounds, and read-only `sogo.sieve` parser |
| `test_managesieve_and_reconciler.py` | Master-User SASL PLAIN `\x00user*vmail\x00pass` wire framing, `STARTTLS` upgrade, SOGo nightly `00:00` cron recovery, expiry restoration, and `reconciler_loop` `60`s SQLite tick vs `300`s ManageSieve throttling |
| `test_ldap_and_auth.py` | OpenLDAP (`rfc2307`) & Active Directory (`ad`) bind auth, `ALLOWED_GROUPS` RBAC, session revocation, and per-IP login rate limiting (`429`) |
| `test_api_and_backup.py` | Portal `/api/*` & internal `/api/internal/*` FastAPI contracts, SQLite WAL schema initialization, and online `python -m app.backend.backup_db` snapshots |
| `test_phase2_signatures_and_milter.py` | AD/LDAP placeholder rendering, smart empty-line/element stripping, rule priority, reply deduplication, and binary Postfix Milter rewriting |
| `test_phase3_quarantine_tracking_connectors.py` | Quarantine digest generation, link-scanner-safe two-step token release, Rspamd `/history` SQLite persistence, and Fernet-encrypted IMAP/POP3 connectors |
| `test_hardening_and_portal_tracking.py` | `CONTAINER_HOST_GATEWAY` loopback translation (`10.0.2.2` / `host.containers.internal`), `portal_tracking_enabled` RBAC, and `deactivate-all-vacations` clean uninstall |
| `test_v101_rspamd_tls_and_ad_groups.py` | Rspamd URL normalization, self-signed TLS & HTTP Basic Auth, AD multi-level nested groups, `primaryGroupID` / `objectSid` RIDs, UPN logins, and `/api/internal/groups` |
| `test_v104_imapsync_bridge.py` | `imapsync_cache.json` missing/corrupt/populated handling and 24-hour-cooldown `check_imapsync_bridge_alerts()` recovery state machine |

---

## Static NS8 Architecture, UI, Schema & Documentation Guards (`verify_ns8_rules.py`)

`tests/verify_ns8_rules.py` enforces NethServer 8 rootless module conventions, cross-layer API contracts, UI safety rules, translation parity, and documentation integrity on every test run:

| Test Function | What It Enforces |
| :--- | :--- |
| `test_no_custom_get_status_action` | Ensures `imageroot/actions/get-status` never exists so NS8 Core's built-in status action is never shadowed. |
| `test_action_scripts_set_env_and_no_deprecated_dump_env` | Verifies `create-module`, `configure-module`, and `restore-module` persist state via `agent.set_env` and zero scripts call deprecated `agent.dump_env()`. |
| `test_hostname_validation_and_tcp_ports_initialization` | Verifies `01hostname-validation` (`http_route_in_use`, exit code `2`) and `create-module/20initialize` (`TCP_PORTS` -> `WEB_TCP_PORT` + `MILTER_TCP_PORT`, `internal.token`). |
| `test_systemd_units_pull_missing_and_localhost_binding` | Audits `.service` and `.timer` units (`%E/state`, `--pull=missing`, `Restart=always`, no `--restart=`, no inline `\"` inside single quotes, `127.0.0.1` / `${MILTER_BIND_HOST}` bindings, `OnUnitActiveSec=60min`, `update-module.d/20restart` timer reschedule, and a live HTTP `/api/health` smoke execution of `imageroot/bin/wait-healthy`). |
| `test_all_imageroot_agent_tasks_run_hide_notifications` | AST-inspects every Python script in `imageroot/**` and verifies **100% (`11/11`)** of `agent.tasks.run(...)` calls pass `extra={'isNotificationHidden': True}`. |
| `test_build_images_sh_labels_and_permissions` | Checks `org.nethserver.authorizations` (`traefik@node:routeadm mail@any:mailadm cluster:accountconsumer imapsync@any:reader`), `tcp-ports-demand=2`, `rootfull=0`, `\r` stripping, and narrow `chmod +x` globs (`actions/*/[0-9]*`, `events/*/[0-9]*`, `update-module.d/*`, `bin/*`) that never touch `imageroot/README.md`. |
| `test_backup_restore_clone_and_python_syntax` | Validates `etc/state-include.conf`, `module-dump-state` / `module-cleanup-state`, all **27 NS8 action directories** (4 lifecycle + 23 UI/API), `#!` shebangs, Python AST syntax, and JSON files. |
| `test_zero_crlf_line_endings` | Scans all non-binary files across the repository and fails on any `\r\n` or stray `\r` byte. |
| `test_app_containerfile` | Verifies `python:3.12` base image, `/app/__init__.py`, exposed ports `8080` & `8090`, and `uvicorn app.backend.main:app`. |
| `test_ui_never_calls_create_success_notification_for_app` | Forbids `createSuccessNotificationForApp` (absent from `@nethserver/ns8-ui-lib`) and enforces 2-argument `createErrorNotificationForApp(err, msg)`. |
| `test_ui_tasks_set_is_notification_hidden` | Verifies all `createModuleTaskForApp` / `createClusterTaskForApp` calls across `ui/src/**` except `configure-module` in `Settings.vue` (`40/41`) set `isNotificationHidden: true`. |
| `test_ui_carbon_icons_registered` | Verifies every `@carbon/icons-vue` tag in `<template>` is registered in `components` and every `:icon` prop is exposed in `data()` / `computed`. |
| `test_ui_and_portal_i18n_translations_and_fallback` | Enforces 100% `en`/`de` key parity in both `ui/public/i18n/{en,de}/translation.json` and `app/frontend/src/i18n/messages.js`, mandatory NS8 keys, strict English (`en`) default locale without `navigator.language` override, and `<html lang="en">` in `app/frontend/index.html`. |
| `test_contract_cockpit_ui_to_imageroot_actions_and_schemas` | Cross-checks all 23 Cockpit module actions, `Settings.vue` / `Signatures.vue` / `Connectors.vue` / `Status.vue` fields, and ensures external `ns8-imapsync` bridge tasks (`list-tasks`, `start-task`, `stop-task`, `get-log`) target `targetModuleId` rather than `this.instanceName`. |
| `test_release_artifacts_license_and_ns8_repo_workflow` | Verifies `GPL-3.0` `LICENSE`, `.github/workflows/publish-ns8-repo.yml`, and clean Sieve cleanup in `destroy-module/20destroy`. |
| `test_github_release_alignment_and_english_readme` | Audits all **7 repository Markdown files** (`README.md`, `CHANGELOG.md`, `imageroot/README.md`, `ui/README.md`, `app/README.md`, `app/frontend/README.md`, `tests/README.md`) for 100% English-only prose, valid relative links, `BLS-ISP` OCI/Software Center metadata alignment, and `1.0.5` version consistency. |
| `test_contract_imageroot_to_backend_internal_routes` | Verifies every `/api/internal/*` URL called in `imageroot/` matches a registered FastAPI route in `app.backend.main:app`. |
| `test_contract_portal_frontend_to_backend_api_routes` | Verifies every `/api/*` endpoint called in `app/frontend/src/**` matches a registered FastAPI route. |
| `test_jsonschema_ui_inputs_and_live_backend_outputs` | Validates all **40 Draft-07 JSON schemas** (`validate-input.json` & `validate-output.json`), UI payloads (including `null` / `{}` / `force_refresh` on `list-mailboxes` & `list-connectors`), and live FastAPI `TestClient` responses. |

---

## Wire-Protocol End-to-End Suites

1. **`tests/test_e2e_smoke.py`**:
   - Mounts the compiled Vue 3 Secretariat Portal (`app/frontend/dist/index.html`), authenticates via `ldap3` OpenLDAP against `ALLOWED_GROUPS`, connects over real TCP to `MockManageSieveServer` (`<user>*vmail`), tests SOGo `sogo.sieve` filter inclusion (`include :personal "sogo";`) vs personal vacation suppression, simulates SOGo's nightly `00:00` cron override and `/api/internal/reconcile` recovery, disables absence to confirm `SETACTIVE "sogo"` restoration, verifies audit logs, and runs an online SQLite backup via `python -m app.backend.backup_db`.
2. **`tests/test_phase2_phase3_e2e.py`**:
   - **Phase 2 Before-DKIM Milter**: Streams multipart/mixed messages with PDF attachments over real TCP binary Milter packets (`MilterTestClient`), verifying AD placeholder injection, reply deduplication, and `SMFIR_ACCEPT` bypass on unauthenticated inbound port 25 mail.
   - **Phase 3a Quarantine Digest & Release**: Seeds spam into `MockImapServer` (`Junk`), runs `/api/internal/quarantine/run-digest` delivering via `MockSmtpSubmissionServer`, tests scanner-safe idempotent `GET /api/quarantine/token/{token}` prefetching, confirms `POST /api/quarantine/action` (`release_and_allow`) moves `Junk` -> `INBOX`, and verifies future allowlisted sender rescue.
   - **Phase 3b Rspamd Mail Tracking**: Queries `MockRspamdServer` `/history`, normalizes statuses (`delivered`, `spam`, `greylisted`, `rejected`), and confirms SQLite `mail_history_cache` serves results even after Rspamd's ring buffer is cleared.
   - **Phase 3c External POP3/IMAP Connectors**: Tests `MockPop3Server` and `MockImapServer` polling, Fernet password encryption at rest in SQLite, UID/UIDL deduplication, `leave_on_server` deletion, and 24h-cooldown admin failure alert emails.
   - **`v1.0.1` HTTPS Self-Signed Rspamd & Enterprise AD**: Generates an ephemeral RSA-2048 self-signed X.509 certificate (`MockHttpsTraefikRspamdServer`) with HTTP Basic Auth at `/rspamd/history`, verifying `rspamd_tls_verify=True` rejection vs `rspamd_tls_verify=False` + `rspamd_user` success, alongside Active Directory nested groups, `primaryGroupID=513` (`Domain Users` `objectSid` RID), and custom UPN login (`julia@custom-upn.de`).
3. **`tests/test_v104_imapsync_bridge_e2e.py`**:
   - Exercises `imageroot/bin/toolbox_imapsync.py`, `sync-mailboxes-cache`, and `list-connectors/20read` across clusters with zero vs coupled + uncoupled `ns8-imapsync` instances (validating outputs against `list-connectors/validate-output.json`), runs `ui/src/utils/imapsyncBridge.js` in Node.js against both normalized and raw upstream `user_properties` payloads, and verifies 24h-cooldown SMTP failure alerts and recovery resets.
4. **`tests/test_v105_notification_and_cache_ttl_e2e.py`**:
   - Verifies `sync-mailboxes-cache` (`--min-age 300`, `--skip-imapsync`, `only_coupled=True`), `list-mailboxes/20read`, `list-connectors/20read`, `get-configuration/20read`, and `discover-mail-instance/20read` execute **zero** child `agent.tasks.run` calls on warm cache hits and pass `extra={'isNotificationHidden': True}` on live/forced refreshes.

---

## Running the Test Suites

Run all commands from the repository root (see also [`../README.md`](../README.md), [`../imageroot/README.md`](../imageroot/README.md), [`../ui/README.md`](../ui/README.md), [`../app/README.md`](../app/README.md), and [`../app/frontend/README.md`](../app/frontend/README.md)):

```bash
# 1. Build the Secretariat Portal SPA first (required by tests/test_e2e_smoke.py)
npm --prefix app/frontend ci
npm --prefix app/frontend run build

# 2. Lint & build the NS8 Cockpit Admin UI
npm --prefix ui ci --legacy-peer-deps
npm --prefix ui run lint
npm --prefix ui run build

# 3. Install Python test dependencies
pip install -r app/backend/requirements.txt pytest pytest-asyncio httpx jsonschema pyyaml

# 4. Run the complete Backend + Static NS8 Rules + Wire-Protocol E2E suite
pytest app/backend/tests tests -v

# Run only the NS8 static architecture, UI, i18n, schema & documentation audit
pytest tests/verify_ns8_rules.py -v

# Run only backend unit & component tests
pytest app/backend/tests -v
```
