# NS8 Mail Toolbox — Cockpit Admin UI (`ui/`)

This directory contains the NethServer 8 Cluster-Admin (Cockpit) frontend for **NS8 Mail Toolbox** (`ns8-mail-toolbox-ui`, `v1.0.5`). It runs as an embedded NS8 module application inside the NethServer 8 Cluster-Admin shell and communicates exclusively through the NS8 Cockpit WebSocket task API (`createModuleTaskForApp` and `createClusterTaskForApp`).

## Technology Stack

- **Framework**: Vue `2.7` + Vue Router `3.2` + Vuex `3.4` + Vue I18n `8.24`
- **Design System**: `@nethserver/ns8-ui-lib` (`^2.0.0`), Carbon Design System (`@carbon/vue` `^2.40.0`, `carbon-components` `^10.41.0`, `@carbon/icons-vue` `^10.37.0`)
- **Build Toolchain**: Vue CLI `4.5` (`NODE_OPTIONS=--openssl-legacy-provider`), ESLint + Prettier (`endOfLine: "lf"`), packaged via `ui/Containerfile` (`docker.io/library/node:20-bookworm` builder into `imageroot/ui/`)

---

## Directory Layout

```text
ui/
├── Containerfile                       # Multi-stage Node 20 build stage outputting dist/
├── package.json                        # Package manifest (version 1.0.5) & npm scripts
├── public/
│   ├── metadata.json                   # NS8 Software Center metadata (EN & DE descriptions, links)
│   ├── shortcuts.json                  # NS8 Cockpit keyboard/search navigation shortcuts
│   └── i18n/
│       ├── en/translation.json         # English translation catalog (default & fallback)
│       └── de/translation.json         # German translation catalog (100% key parity with en)
└── src/
    ├── App.vue                         # Root shell, Cockpit core bridge & runtime locale watcher
    ├── main.js                         # Vue 2 + Carbon + ns8-ui-lib + VueI18n bootstrap
    ├── i18n/index.js                   # Static EN/DE catalog bundler & fallback loader
    ├── router/index.js                 # Hash/query-bound routes (status, mailboxes, signatures, ...)
    ├── store/index.js                  # Vuex store (instanceName, instanceLabel, core, appName)
    ├── styles/                         # Carbon SCSS variables & shared utility classes
    ├── utils/
    │   └── imapsyncBridge.js           # NS8 IMAP-Sync task normalization & cross-app navigation
    ├── components/
    │   ├── AppSideMenu.vue             # Desktop side navigation wrapper
    │   ├── AppMobileSideMenu.vue       # Responsive mobile navigation wrapper
    │   ├── AppSideMenuContent.vue      # Navigation links + persistent EN | DE language switcher
    │   ├── EditVacationModal.vue       # Central vacation & deputy forwarding editor modal
    │   ├── EditSignatureModal.vue      # HTML/plaintext company signature editor & live AD preview
    │   ├── EditConnectorModal.vue      # External POP3/IMAP account modal + live connection test
    │   └── ImapsyncLogModal.vue        # NS8 IMAP-Sync task log viewer & .log download modal
    └── views/
        ├── Status.vue                  # Instance overview, KPI summary cards, backup & services
        ├── Mailboxes.vue               # Mailbox directory, absence/forwarding filters & actions
        ├── Signatures.vue              # Before-DKIM signature rules & Postfix Milter hook snippets
        ├── MailTracking.vue            # Rspamd mail history search + spam quarantine & allowlist
        ├── Connectors.vue              # Unified external POP3/IMAP connectors & NS8 IMAP-Sync bridge
        ├── Templates.vue               # Company absence templates editor + Audit Log viewer tabs
        ├── Settings.vue                # NS8 Mail coupling, Portal, Milter, Rspamd & Digest config
        └── About.vue                   # Module version, authors, license & upstream links
```

---

## Views & Modals Overview

| View / Component | Route (`?page=`) | Primary NS8 Actions Invoked | Purpose |
| :--- | :--- | :--- | :--- |
| `Status.vue` | `status` | `get-status`, `get-configuration`, `list-mailboxes`, `list-signatures`, `list-connectors`, `list-backup-repositories`, `list-backups`, external `imapsync*/list-tasks` | Displays coupled `ns8-mail` server, Secretariat Portal link & authorized groups, active absence/forwarding KPIs, Before-DKIM Milter status, unified Connector + `ns8-imapsync` health KPIs, backups, logs, systemd services, images, and volumes. |
| `Mailboxes.vue` + `EditVacationModal.vue` | `mailboxes` | `list-mailboxes`, `get-templates`, `set-vacation` | Searchable and filterable mailbox table showing central vacations, personal SOGo vacation state (`sogo.sieve` collision awareness), and forwardings. `EditVacationModal.vue` configures scheduled/open-ended auto-replies, placeholder templates, and deputy forwarding (`keep_copy` vs redirect-only). |
| `Signatures.vue` + `EditSignatureModal.vue` | `signatures` | `list-signatures`, `get-configuration`, `list-mailboxes`, `save-signature`, `delete-signature`, `preview-signature` | Manages central Before-DKIM outbound company signatures (`group` > `domain` > `default` priority), reply-chain behavior, placement, copyable Postfix Milter activation/rollback commands, and live AD/LDAP placeholder rendering preview. |
| `MailTracking.vue` | `mail-tracking` | `search-mail-history`, `list-quarantine`, `release-quarantine`, `run-digest-now`, `delete-allowlist` | Two-tab view: **Mail History Search** (queries Rspamd `/history` + persistent SQLite archive by sender, recipient, subject, date range, and delivery status with symbol inspection modal) and **Spam Quarantine & Digest** (lists `Junk` messages, triggers immediate INBOX delivery + optional sender allowlisting, manages allowlist entries, and triggers manual digest runs). |
| `Connectors.vue` + `EditConnectorModal.vue` + `ImapsyncLogModal.vue` | `connectors` | `list-connectors`, `list-mailboxes`, `save-connector`, `delete-connector`, `test-connector`, `sync-connector-now`, cluster `list-installed-modules`, external `imapsync*/{list-tasks,start-task,stop-task,get-log}` | Unified management of native POP3/IMAP fetch connectors (with provider presets, encrypted credentials, and live connection testing) alongside bridged `ns8-imapsync` synchronization tasks in a single filterable table. |
| `Templates.vue` | `templates` | `get-templates`, `set-templates`, `list-audit-log` | Two-tab view: **Company Templates** (manages bilingual out-of-office/sick-leave templates with clickable placeholder chips such as `{{displayName}}`, `{{endDate}}`, `{{deputyEmail}}`) and **Audit Log** (searchable chronological trail of Cockpit, Secretariat Portal, quarantine, and alert events). |
| `Settings.vue` | `settings` | `get-configuration`, `discover-mail-instance`, `test-rspamd`, `configure-module` | Configures coupled `ns8-mail` instance, optional Traefik Secretariat/Quarantine Web Portal (`portal_enabled`, FQDN, Let's Encrypt, HTTP-to-HTTPS, AD/LDAP `allowed_groups` with built-in system group filter, `portal_tracking_enabled`), Before-DKIM Milter toggle, Rspamd Controller URL/Basic Auth/self-signed TLS verification + live connection test, daily Quarantine Digest schedule, and Connector/IMAP-Sync failure alert email. |
| `About.vue` | `about` | `list-installed-modules` | Displays installed module version, categories, authors, documentation, issue tracker, source repository, and license metadata from `public/metadata.json`. |

---

## Dual-Layer `ns8-imapsync` Sync Bridge (`src/utils/imapsyncBridge.js`)

When `ns8-imapsync` is installed in the NethServer 8 cluster, `Connectors.vue` and `Status.vue` automatically surface and monitor its tasks without duplicating credentials or polling logic:

1. **Layer 1 — Agent Discovery & Cache (`list-connectors`)**:
   - `list-connectors` returns `imapsync_bridge` (`installed`, `updated_at`, and `instances[]` with `module_id`, `ui_name`, `coupled`, `tasks_loaded`, and normalized `tasks[]`).
   - If `imapsync_bridge` is absent (for example right after an in-place upgrade before agent reload), the UI falls back to cluster `list-installed-modules` via `extractImapsyncModulesFromCluster()`.
2. **Layer 2 — Live Cross-Module Cockpit WebSocket Tasks**:
   - Whenever a coupled `imapsync` instance reports `!inst.tasks_loaded` (or when uncoupled instances are explicitly toggled on in `Connectors.vue`), the UI invokes `list-tasks` directly on the target `imapsync` module ID (`createModuleTaskForApp(targetModuleId, ...)` — never `this.instanceName`).
   - `normalizeImapsyncTask()` seamlessly normalizes both `toolbox_imapsync.py` fields (`remotehost`, `remoteuser`, `synchronization`) and raw upstream `ns8-imapsync` `user_properties` fields (`remotehostname`, `remoteusername`, `security` -> `IMAPS` / `IMAP+STARTTLS` / `IMAP`, `cron` -> minutes/hours/manual, `sieve_enabled`, `delete_local`, `delete_remote`, `delete_remote_older`, `service_running`, `last_sync_timestamp`, `last_sync_exit_code`, `has_log`), resolving `localuser` to its primary email address via `mailboxes`.
   - Row actions on bridged tasks dispatch `start-task`, `stop-task`, and `get-log` (rendered inside `ImapsyncLogModal.vue` with 100 KB truncation notice, refresh, and `.log` download) directly to the owning `imapsync` instance, or jump straight to `/apps/<imapsyncN>?page=tasks` via `navigateToCockpitApp()`.
   - When `ns8-imapsync` is not installed (`imapsyncInstances.length === 0`), all bridge banners, badges, and source filters remain completely hidden.

---

## Cache-First `force_refresh` & Silent Task Discipline (`v1.0.5`)

To keep the NethServer 8 Cluster-Admin UI responsive and completely free of background toast notifications:

- **Silent Background & Inline-Notified Tasks (`isNotificationHidden: true`)**:
  - Every background read, discovery, log fetch, preview, connection test, and quick row/modal mutation (`28/29` task call sites across `ui/src/**`, plus `runExternalImapsyncTask` defaults) sets `extra: { ..., isNotificationHidden: true }`.
  - Quick write actions (`set-vacation`, `save-signature`, `delete-signature`, `set-templates`, `release-quarantine`, `run-digest-now`, `delete-allowlist`, `save-connector`, `sync-connector-now`, `delete-connector`, `start-task`, `stop-task`) surface immediate feedback via inline Carbon `NsInlineNotification` banners instead of stacking persistent notifications in the NS8 right-hand task drawer.
  - Only `configure-module` in `Settings.vue` uses a visible NS8 task notification so cluster-wide Traefik/module reconfiguration progress and validation feedback appear in the standard NS8 task bar.
- **Cache-First Initial Loads vs Explicit User Refresh (`force_refresh`)**:
  - Initial `created()` loads in `Mailboxes.vue`, `Connectors.vue`, `Signatures.vue`, and `Status.vue` call `list-mailboxes` and `list-connectors` **without** `force_refresh`, allowing `imageroot` to serve warm 300-second caches (`mailboxes_cache.json`, `imapsync_cache.json`) with zero child NS8 tasks.
  - Clicking the manual **Refresh** button in `Mailboxes.vue` or `Connectors.vue` passes `data: { force_refresh: true }` to trigger an immediate live sync.
  - `Status.vue` waits for `get-configuration` to complete and skips `list-mailboxes`, `list-signatures`, and `list-connectors` altogether when `configured === false`.
- **NS8 UI Library Safety**:
  - Never call `createSuccessNotificationForApp` (which does not exist in `@nethserver/ns8-ui-lib`); always use inline `NsInlineNotification` components.
  - Always pass two arguments `(err, message)` to `this.createErrorNotificationForApp(err, message)`.
  - Register every `<IconTag />` component in `components: { ... }` and expose every `:icon="IconRef"` binding in `data()`.

---

## Internationalization (`EN | DE`)

- **English Default & Fallback**: `src/main.js` initializes `VueI18n` with `locale: "en"` (unless overridden in `localStorage`) and `fallbackLocale: "en"`, without reading `navigator.language` on startup.
- **Bundled Catalogs with 100% Key Parity**: `src/i18n/index.js` bundles both `public/i18n/en/translation.json` and `public/i18n/de/translation.json` (including all mandatory NS8 keys under `action.*`, `task.cannot_create_task`, and `error.*`). Automated tests in `tests/verify_ns8_rules.py` enforce 100% EN/DE key parity and verify every `$t(...)` / `$tc(...)` key referenced in `src/**`.
- **Side-Menu Language Switcher & Cockpit Sync**:
  - `src/components/AppSideMenuContent.vue` provides a compact `EN | DE` toggle persisted in `window.localStorage` under `ns8_mail_toolbox_cockpit_lang`.
  - `src/App.vue` watches `core.$i18n.locale` during an active Cockpit session and follows runtime Cockpit language changes (`en` / `de`) when no explicit module override is stored in `localStorage`.

---

## Development, Linting & Build

Run all commands from `ui/` (or pass `--prefix ui` from the repository root):

```bash
# Install dependencies (Node 20 LTS recommended)
npm install --legacy-peer-deps

# Start local hot-reload development server
npm run serve

# Run ESLint + Prettier checks
npm run lint

# Build production assets into ui/dist/
npm run build
```

All source and JSON files under `ui/` must use strict Unix `LF` (`\n`) line endings.
