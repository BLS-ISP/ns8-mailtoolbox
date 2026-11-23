# Changelog

All notable changes to **NS8 Mail Toolbox (`ns8-mail-toolbox`)** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.5] - 2026-11-21

### Added
- Optional `{"force_refresh": true}` input schema (`validate-input.json`) on `list-mailboxes` and `list-connectors` so manual **Refresh** clicks in NS8 Cockpit trigger an immediate live cache update while standard view loads remain zero-subtask cache hits.
- CLI flags `--min-age <seconds>` and `--skip-imapsync` on `imageroot/bin/sync-mailboxes-cache` for fine-grained TTL-gated synchronization.
- Persistent Rspamd endpoint discovery cache (`./config/discovery_cache.json`, `0600`, `3600`s TTL) shared across `get-configuration`, `discover-mail-instance`, and `configure-module`.
- Permanent AST and UI task static guards in `tests/verify_ns8_rules.py` plus end-to-end TTL caching tests in `tests/test_v105_notification_and_cache_ttl_e2e.py`.

### Changed
- **>90% Reduction in NS8 Sub-Task & `responses` Traffic:**
  - `list-mailboxes/20read` and `list-connectors/20read` now serve fresh caches (`< 300`s / 5 minutes) with **zero** child `agent.tasks.run` invocations.
  - `get-configuration/20read` performs **zero** `get-filter-configuration` sub-tasks once the module is configured or cached.
  - `toolbox_imapsync.discover_imapsync_bridge` always reads local Redis topology (`0` NS8 tasks) and only invokes `list-tasks` on coupled `ns8-imapsync` instances (`only_coupled=True`).
  - `mail-toolbox-sync.timer` interval relaxed from `10min` to `60min`, with `imageroot/update-module.d/20restart` automatically rescheduling the active user timer on in-place upgrades.
  - `app/backend/reconciler.py` splits background execution into a lightweight `60`s local SQLite tick (scheduled quarantine digest, due POP3/IMAP connectors, `ns8-imapsync` failure alerts) and a throttled `300`s network ManageSieve/IMAP reconciliation pass.
  - Cockpit `Status.vue` skips mailbox, signature, and connector queries until initial module configuration is saved.

### Fixed
- **Eliminated Untitled Cluster-Admin Toast Notifications:** Added `extra={"isNotificationHidden": True}` to **100% (`11/11`)** of internal `agent.tasks.run(...)` calls across `imageroot/` and `isNotificationHidden: true` to **`40/41`** Cockpit UI tasks across `ui/src/**` (leaving only `configure-module` in `Settings.vue` visible in NS8's task drawer), preventing background timer and read/row actions from broadcasting untitled completion banners across Cluster-Admin tabs.

---

## [1.0.4] - 2026-11-20

### Added
- **NS8 IMAP-Sync (`ns8-imapsync`) Sync Bridge:**
  - Added `imapsync@any:reader` to `org.nethserver.authorizations` in `build-images.sh`.
  - Added `imageroot/bin/toolbox_imapsync.py` to discover installed `imapsync*` instances from cluster Redis, resolve UI names and `ns8-mail` coupling (`MAIL_SERVER`), normalize `user_properties` sync tasks (protocol/SSL/TLS, cron schedule, folder exclusions, Sieve filter flag, remote/local deletion policies, running state, last exit code, and timestamp), and persist `./config/imapsync_cache.json` (`0600`).
  - Unified `Connectors.vue` and `Status.vue` in NS8 Cockpit (`ui/src/utils/imapsyncBridge.js` + `ui/src/components/ImapsyncLogModal.vue`) to display `ns8-imapsync` tasks alongside native POP3/IMAP connectors with direct **Run now (`start-task`)**, **Stop (`stop-task`)**, **Live Log (`get-log`)**, coupled/uncoupled filtering, and deep-link navigation to the `ns8-imapsync` app — including seamless Cockpit UI task fallback when `imapsync@any:reader` is absent after an in-place upgrade.
  - Extended `app/backend/connector_service.py` to monitor coupled `ns8-imapsync` task failures (`last_sync_exit_code != 0`) and send localized administrator alert emails to `admin_alert_email` with a 24-hour per-task cooldown.
  - Added wire-protocol and Node.js normalization E2E test suite (`tests/test_v104_imapsync_bridge_e2e.py`).

---

## [1.0.3] - 2026-11-20

### Added
- Instant **`EN | DE`** language switcher in the NS8 Cockpit side menu (`ui/src/components/AppSideMenuContent.vue`) with `localStorage` persistence (`ns8_mail_toolbox_cockpit_lang`) and live tracking of NS8 Cockpit shell language changes.
- Localized (`EN` / `DE`) HTML & plaintext system emails for the Daily Spam Quarantine Digest (`quarantine_service.py`) and Connector / IMAP-Sync Failure Alerts (`connector_service.py`).
- Dual-default out-of-office templates seeded out of the box (`default_en` as primary default alongside `default_de`).

### Changed
- Switched the default language across the NS8 Cockpit UI (`ui/`), the Secretariat Web Portal & Two-Step Quarantine Release SPA (`app/frontend/`), and backend system notifications from German (`de`) to **English (`en`)** while maintaining 100% `en`/`de` translation key parity.

---

## [1.0.2] - 2026-11-20

### Added
- Dedicated `imageroot/bin/wait-healthy` Python health-check script polling `http://127.0.0.1:${WEB_TCP_PORT}/api/health` in `ExecStartPost` without fragile systemd shell quoting.
- Automatic Podman 5 `pasta:--map-gw` (`host.containers.internal`) vs Podman 4 `slirp4netns:allow_host_loopback=true` (`10.0.2.2`) runtime detection in `imageroot/bin/discover-services` and `app/backend/config.py`.

### Fixed
- Hardened `imageroot/systemd/user/mail-toolbox.service` and `mail-toolbox-sync.service` to follow NS8 3.20+ rootless systemd conventions (`%E/state` paths, `PODMAN_SYSTEMD_UNIT=%n`, `--conmon-pidfile`, `--cidfile`, `--cgroups=no-conmon`, `Type=forking`, `Restart=always`, and removal of deprecated env-dump calls).

---

## [1.0.1] - 2026-11-20

### Added
- Live `discover-mail-instance` NS8 action (`imageroot/actions/discover-mail-instance/20read`) and shared `imageroot/bin/toolbox_discovery.py` helper to auto-discover Rspamd URLs, LDAP/AD schema, and groups (with resolved member counts and built-in Windows AD system group classification) as soon as an admin selects an `ns8-mail` server in **Settings** — even before initial configuration save.
- Optional Secretariat Web Portal toggle (`portal_enabled`) allowing Cockpit-only deployments without allocating a public Traefik hostname, plus empty `allowed_groups` support.
- Rspamd HTTP Basic Auth (`rspamd_user` + `rspamd_password` for Traefik `forward_auth`), self-signed TLS support (`rspamd_tls_verify=false` default), URL normalization, and split-DNS / hairpin-NAT local gateway fallback.
- Full Enterprise Active Directory authentication and group resolution (`app/backend/auth.py`): multi-level nested groups (`1.2.840.113556.1.4.1941` + iterative BFS), `primaryGroupID` / `objectSid` RIDs (e.g. `Domain Users`), custom `userPrincipalName` (`user@upn.domain`), and `DOMAIN\user` login formats.

---

## [1.0.0] - 2026-11-20

### Added
- Initial stable release of **NS8 Mail Toolbox (`ns8-mail-toolbox`)** published to `ghcr.io/bls-isp/mail-toolbox` / `ghcr.io/bls-isp/mail-toolbox-app` and indexed in the `BLS-ISP/ns8-nethforge` NethServer 8 Software Center repository:
  - **Delegated Sick-Leave & Forwarding Secretariat Portal** (`Vue 3 + Vite`) behind Traefik with AD/LDAP group RBAC, audit logging, and non-destructive Dovecot ManageSieve (`<user>*vmail`) coexistence with `sogo.sieve`.
  - **Central Before-DKIM Postfix Signature Milter** (`asyncio` binary Milter on `:8090`) with AD/LDAP attribute placeholders, conditional blocks, smart empty-line removal, reply-chain deduplication, attachment/`cid:` safety, and automatic multi-node NS8 WireGuard VPN IP binding.
  - **Daily Spam Quarantine Digest & Two-Step Self-Service Release** scanning Dovecot `Junk` folders via Master-User IMAP, with link-scanner-safe preview (`GET /api/quarantine/token/{token}`), explicit confirmation (`POST /api/quarantine/action`), `imapsieve` ham learning, and per-user sender allowlists.
  - **Rspamd Mail-Tracking Search** with persistent deduplicated SQLite history (`mail_history_cache`) and optional read-only Secretariat Portal access (`portal_tracking_enabled`).
  - **External POP3/IMAP Mail Connectors** with Fernet-encrypted credentials at rest, UID/UIDL deduplication, connection testing, manual sync, and failure alert emails.
  - Complete NS8 rootless lifecycle support: online SQLite Restic backups (`module-dump-state`), `restore-module`, `clone-module` (cluster node migration), `update-module.d/20restart`, cluster event handlers (`mail-settings-changed`, `user-domain-changed`), and clean Sieve restoration on `destroy-module`.

[1.0.5]: https://github.com/BLS-ISP/ns8-mailtoolbox/releases/tag/v1.0.5
[1.0.4]: https://github.com/BLS-ISP/ns8-mailtoolbox/releases/tag/v1.0.4
[1.0.3]: https://github.com/BLS-ISP/ns8-mailtoolbox/releases/tag/v1.0.3
[1.0.2]: https://github.com/BLS-ISP/ns8-mailtoolbox/releases/tag/v1.0.2
[1.0.1]: https://github.com/BLS-ISP/ns8-mailtoolbox/releases/tag/v1.0.1
[1.0.0]: https://github.com/BLS-ISP/ns8-mailtoolbox/releases/tag/v1.0.0
