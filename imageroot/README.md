# NS8 Mail Toolbox — Rootless Module Agent (`imageroot/`)

This directory contains the **NethServer 8 (NS8) rootless module agent** layer packaged into the `ghcr.io/bls-isp/mail-toolbox` OCI image (`/imageroot`). At installation time (`add-module`), NS8 extracts these files into the dedicated rootless module user home (`/home/mail-toolbox<N>/.config/`).

---

## NS8 OCI Metadata & RBAC Authorizations

Configured in [`../build-images.sh`](../build-images.sh):

| OCI Label | Value | Purpose |
| :--- | :--- | :--- |
| `org.nethserver.rootfull` | `0` | Runs strictly as an unprivileged rootless Linux user (`mail-toolbox<N>`) |
| `org.nethserver.min-core` | `3.20.1` | Targets NS8 Core 3.20+ environment & systemd user supervision |
| `org.nethserver.tcp-ports-demand` | `2` | Allocates `WEB_TCP_PORT` (FastAPI `:8080`) and `MILTER_TCP_PORT` (Milter `:8090`) |
| `org.nethserver.images` | `ghcr.io/bls-isp/mail-toolbox-app:<tag>` | Pre-pulls the runtime application container during install/update |
| `org.nethserver.authorizations` | `traefik@node:routeadm mail@any:mailadm cluster:accountconsumer imapsync@any:reader` | Grants Traefik HTTP route management, `ns8-mail` mailbox/credential discovery, local `ldapproxy` domain binding, and read-only `ns8-imapsync` bridge discovery |

---

## Directory Layout

```text
imageroot/
├── actions/                  # 27 NS8 module actions (4 lifecycle + 23 UI/API) & 41 Draft-07 JSON schemas
├── bin/                      # Executable discovery, cache sync, health-check & Restic backup helpers
│   ├── discover-services     # Resolves ns8-mail TCP providers, Podman 4/5 gateway, ldapproxy & master.creds
│   ├── toolbox_discovery.py  # Shared Rspamd URL discovery (3600s cache) & AD/OpenLDAP group/schema resolver
│   ├── toolbox_imapsync.py   # Shared NS8 IMAP-Sync (ns8-imapsync) Redis & list-tasks normalizer
│   ├── sync-mailboxes-cache  # Syncs mailboxes_cache.json & imapsync_cache.json (--min-age, --skip-imapsync)
│   ├── wait-healthy          # ExecStartPost readiness probe polling http://127.0.0.1:${WEB_TCP_PORT}/api/health
│   ├── module-dump-state     # Pre-backup hook creating online SQLite snapshot (data/toolbox-backup.sqlite)
│   └── module-cleanup-state  # Post-backup hook removing temporary SQLite backup snapshot
├── etc/
│   └── state-include.conf    # Restic cluster backup inclusion list
├── events/
│   ├── mail-settings-changed/10reload  # Refreshes discovery & mailbox cache on ns8-mail changes
│   └── user-domain-changed/10reload    # Refreshes LDAP discovery & mailbox cache on domain/user changes
├── systemd/user/
│   ├── mail-toolbox.service       # Rootless Podman supervision for mail-toolbox-app
│   ├── mail-toolbox-sync.service  # Oneshot runner executing sync-mailboxes-cache
│   └── mail-toolbox-sync.timer    # 60-minute periodic cache refresh timer
└── update-module.d/
    └── 20restart             # Post-upgrade hook refreshing discovery, timer schedule & container restart
```

### Runtime State Files (`~/.config/state/` / `%E/state`)

| Path (relative to `%E/state`) | Mode | Backed Up | Purpose |
| :--- | :--- | :--- | :--- |
| `environment` | `0600` | Yes | Persistent NS8 module environment managed via `agent.set_env(...)` |
| `discovery_mail.env` | `0600` | Re-generated | Discovered `MAIL_HOST`, `MAIL_IMAP_PORT`, `MAIL_SIEVE_PORT`, `MAIL_SUBMISSION_PORT`, `PODMAN_NETWORK_MODE` |
| `discovery_ldap.env` | `0600` | Re-generated | Local `ldapproxy` bind settings (`LDAP_HOST`, `LDAP_PORT`, `LDAP_BASE`, `LDAP_SCHEMA`, etc.) |
| `config/internal.token` | `0600` | Yes | 32-byte hex shared secret authenticating NS8 actions via `X-Internal-Token` |
| `config/master.creds` | `0600` | Re-generated | Dovecot Master-User credentials (`vmail:<password>`) retrieved from `ns8-mail` |
| `config/toolbox_settings.json` | `0600` | Yes | Extended settings including encrypted/secret Rspamd credentials |
| `config/mailboxes_cache.json` | `0600` | Re-generated | Cached `ns8-mail` mailboxes, aliases, and Postfix forwards (`300`s smart TTL) |
| `config/imapsync_cache.json` | `0600` | Re-generated | Cached `ns8-imapsync` instances and normalized sync tasks (`300`s smart TTL) |
| `config/discovery_cache.json` | `0600` | Re-generated | Cached `mail_module_id -> rspamd_url` mapping (`3600`s TTL) |
| `data/toolbox.sqlite` | `0600` | Yes (+ snapshot) | Primary SQLite WAL database (vacations, templates, signatures, quarantine, tracking, connectors, audit) |

---

## Systemd User Units (`systemd/user/`)

- **`mail-toolbox.service`**:
  - Uses NS8 rootless Podman supervision (`PODMAN_SYSTEMD_UNIT=%n`, `Type=forking`, `Restart=always`, `--conmon-pidfile %t/mail-toolbox.pid`, `--cidfile %t/mail-toolbox.cid`, `--cgroups=no-conmon`, `--replace`, `--pull=missing`).
  - Runs `/usr/local/bin/runagent discover-services` in `ExecStartPre` and `/usr/local/bin/runagent wait-healthy` in `ExecStartPost`.
  - Publishes `-p 127.0.0.1:${WEB_TCP_PORT}:8080` (strictly localhost behind Traefik/agent) and `-p ${MILTER_BIND_HOST}:${MILTER_TCP_PORT}:8090` (`127.0.0.1` when co-located with `ns8-mail`, or the node's internal NS8 WireGuard VPN IP across nodes).
  - Mounts `-v ./data:/app/data:Z` and `-v ./config:/app/config:Z` inside `%E/state`.
- **`mail-toolbox-sync.service` & `mail-toolbox-sync.timer`**:
  - Fires `OnBootSec=2min` and `OnUnitActiveSec=60min` (`RandomizedDelaySec=30s`) to refresh `mailboxes_cache.json` and `imapsync_cache.json` quietly in the background.

---

## Helper Scripts (`bin/`)

- **`discover-services`**: Connects to Redis (`use_replica=True`), resolves `imap`, `submission`, and `sieve` TCP providers for `MAIL_MODULE_UUID`, detects Podman 4 `slirp4netns` vs Podman 5 `pasta:--map-gw`, queries `Ldapproxy().get_domain(USER_DOMAIN)`, fetches `reveal-master-credentials` silently from `module/<mail_id>`, and writes `discovery_mail.env`, `discovery_ldap.env`, and `config/master.creds` atomically (`0600`).
- **`toolbox_discovery.py`**: Shared library providing `discover_available_mail_servers(...)`, `discover_mail_rspamd_url(...)`, `load_discovery_cache(...)` / `save_discovery_cache(...)` (`3600`s TTL), and `discover_domain_groups_and_schema(...)` (resolving Active Directory & OpenLDAP groups with member counts and built-in Windows AD system group classification via local `ldapproxy`, falling back to `list-acl-subjects` before initial domain bind).
- **`toolbox_imapsync.py`**: Shared library discovering installed `imapsync*` modules from cluster Redis (`0` NS8 tasks for topology) and normalizing `list-tasks` `user_properties` on coupled instances (`only_coupled=True`).
- **`sync-mailboxes-cache`**: Queries `list-user-mailboxes` and `list-addresses` on `module/<mail_id>` (plus coupled `ns8-imapsync` tasks unless `--skip-imapsync` is passed) and atomically writes `config/mailboxes_cache.json` and `config/imapsync_cache.json`. Supports `--min-age <seconds>` to exit `0` with **zero** NS8 sub-tasks when the cache is fresh.
- **`wait-healthy`**: Polls `http://127.0.0.1:${WEB_TCP_PORT}/api/health` for up to 60 seconds during service startup.
- **`module-dump-state` & `module-cleanup-state`**: Invoked automatically by NS8's Restic backup wrapper to create (`sqlite3.Connection.backup()` via container or host fallback) and clean up `data/toolbox-backup.sqlite`.

---

## Silent Sub-Tasks & Cache-First Design

1. **100% Silent Internal Sub-Tasks:** Every NS8 Python agent task invocation across `imageroot/` (`11/11` calls via `NethServer/ns8-core` `agent/tasks/run.py`) passes `extra={"isNotificationHidden": True}`, ensuring child/timer tasks never emit untitled notifications in NS8 Cockpit.
2. **Smart Cache TTLs:** Read actions (`list-mailboxes`, `list-connectors`, `get-configuration`) serve cached metadata (`300`s / `3600`s TTL) with zero child NS8 tasks during normal navigation, while supporting explicit `{"force_refresh": true}` payloads for manual user refreshes.
3. **Built-in NS8 Core Actions:** `get-status` and `get-name` are intentionally **not** defined in `imageroot/actions/` because NS8 Core provides them natively for all rootless modules.

---

## Events, Upgrades & Backup State

- **`events/mail-settings-changed/10reload`**: Triggered by `ns8-mail` changes (such as new mailboxes, aliases, or Postfix forwards); re-runs `discover-services` and `sync-mailboxes-cache --skip-imapsync`, restarting `mail-toolbox.service` only if `discovery_mail.env` changed.
- **`events/user-domain-changed/10reload`**: Triggered by LDAP/AD account provider updates; refreshes `discovery_ldap.env` and `mailboxes_cache.json`, restarting `mail-toolbox.service` if LDAP endpoint parameters changed.
- **`update-module.d/20restart`**: Reloads user systemd units, refreshes `discover-services` and `sync-mailboxes-cache --min-age 300 --skip-imapsync`, reschedules `mail-toolbox-sync.timer`, and restarts `mail-toolbox.service` (including Cockpit-only deployments where `PORTAL_HOSTNAME` is empty).
- **`etc/state-include.conf`**: Instructs NS8 Restic backups to include `state/environment`, `state/config/internal.token`, `state/config/toolbox_settings.json`, `state/data/toolbox.sqlite`, and `state/data/toolbox-backup.sqlite`.

---

## Reference of All 27 NS8 Module Actions (`actions/`)

### Lifecycle Actions (4)

| Action | Step Scripts | Schemas | Description |
| :--- | :--- | :--- | :--- |
| `create-module` | `20initialize` | — | Parses `TCP_PORTS` into `WEB_TCP_PORT` & `MILTER_TCP_PORT`, generates `APP_SECRET_KEY` and `config/internal.token` (`0600`) |
| `restore-module` | `06copyenv`, `40restore_db`, `50configure` | — | Restores environment & `toolbox-backup.sqlite`, re-binds domain/Traefik/WireGuard IP, starts services, syncs `/api/internal/settings`, and runs `/api/internal/reconcile` |
| `clone-module` | `50configure` | — | Adapts ports & WireGuard `MILTER_BIND_HOST` on destination node, restores SQLite snapshot, configures Traefik, and triggers `/api/internal/reconcile` |
| `destroy-module` | `20destroy` | — | Calls `POST /api/internal/deactivate-all-vacations` to restore user `sogo` Sieve rules, stops systemd units, deletes Traefik route, and unbinds user domains |

### Configuration, Mailbox, Signature, Quarantine, Tracking & Connector Actions (23)

| Action | Step Scripts | Validation Schemas | Internal Backend Endpoint / Behavior |
| :--- | :--- | :--- | :--- |
| `configure-module` | `01hostname-validation`, `20configure`, `80start_services` | `validate-input.json` | Validates Traefik route, binds `USER_DOMAIN`, persists env & `toolbox_settings.json`, configures Traefik, starts systemd units, and `PUT /api/internal/settings` |
| `get-configuration` | `20read` | `validate-output.json` | Reads module env, `toolbox_settings.json`, cached `ns8-mail` servers (`discovery_cache.json`), AD/LDAP groups, and Postfix Milter CLI snippets |
| `discover-mail-instance` | `20read` | `validate-input.json`, `validate-output.json` | Live-discovers Rspamd URL, `USER_DOMAIN`, `ldap_schema`, and AD/LDAP groups for a selected `ns8-mail` UUID and updates `discovery_cache.json` |
| `list-mailboxes` | `20read` | `validate-input.json`, `validate-output.json` | Runs `sync-mailboxes-cache` (`--min-age 300 --skip-imapsync` or forced on `force_refresh`) and calls `GET /api/internal/mailboxes` |
| `set-vacation` | `20write` | `validate-input.json`, `validate-output.json` | Applies or disables central ManageSieve vacation & deputy forwarding via `PUT /api/internal/mailboxes/{user}/vacation` |
| `get-templates` | `20read` | `validate-output.json` | Lists standardized out-of-office templates via `GET /api/internal/templates` |
| `set-templates` | `20write` | `validate-input.json`, `validate-output.json` | Saves out-of-office templates via `PUT /api/internal/templates` |
| `list-audit-log` | `20read` | `validate-output.json` | Retrieves audit trail entries via `GET /api/internal/audit` |
| `list-signatures` | `20read` | `validate-output.json` | Lists company signature rules and Milter runtime metrics via `GET /api/internal/signatures` |
| `save-signature` | `20write` | `validate-input.json`, `validate-output.json` | Creates or updates a signature rule via `POST /api/internal/signatures` |
| `delete-signature` | `20write` | `validate-input.json`, `validate-output.json` | Deletes a signature rule via `POST /api/internal/signatures/delete` |
| `preview-signature` | `20read` | `validate-input.json`, `validate-output.json` | Renders live HTML & plaintext signature preview with AD/LDAP user attributes via `POST /api/internal/signatures/preview` |
| `list-quarantine` | `20read` | `validate-input.json`, `validate-output.json` | Lists quarantined spam messages and sender allowlist entries (`force_refresh` supported) via `GET /api/internal/quarantine` |
| `release-quarantine` | `20write` | `validate-input.json`, `validate-output.json` | Moves quarantined message from `Junk` to `INBOX` (with optional allowlist) via `POST /api/internal/quarantine/release` |
| `run-digest-now` | `20write` | `validate-input.json`, `validate-output.json` | Triggers immediate spam quarantine digest email delivery via `POST /api/internal/quarantine/run-digest` |
| `delete-allowlist` | `20write` | `validate-input.json`, `validate-output.json` | Removes a sender allowlist entry via `POST /api/internal/allowlist/delete` |
| `search-mail-history` | `20read` | `validate-input.json`, `validate-output.json` | Refreshes and queries cached Rspamd delivery history via `POST /api/internal/mail-tracking/search` |
| `test-rspamd` | `20write` | `validate-input.json`, `validate-output.json` | Tests live Rspamd `/history` connectivity, Basic Auth, and TLS settings via `POST /api/internal/mail-tracking/test-rspamd` |
| `list-connectors` | `20read` | `validate-input.json`, `validate-output.json` | Merges `GET /api/internal/connectors` with `300`s-cached (or `force_refresh`) `ns8-imapsync` bridge discovery |
| `save-connector` | `20write` | `validate-input.json`, `validate-output.json` | Creates or updates an encrypted external POP3/IMAP connector via `POST /api/internal/connectors` |
| `delete-connector` | `20write` | `validate-input.json`, `validate-output.json` | Deletes an external POP3/IMAP connector via `POST /api/internal/connectors/delete` |
| `test-connector` | `20write` | `validate-input.json`, `validate-output.json` | Tests live POP3/IMAP login and mailbox reachability via `POST /api/internal/connectors/test` |
| `sync-connector-now` | `20write` | `validate-input.json`, `validate-output.json` | Triggers immediate external POP3/IMAP connector fetch via `POST /api/internal/connectors/sync` |
