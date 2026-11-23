# NS8 Mail Toolbox — Secretariat & Quarantine Web Portal (`app/frontend`)

Standalone **Vue 3 + Vite** single-page web application (`ns8-mail-toolbox-portal` `v1.0.5`) bundled into `app/frontend/dist` and served by the `mail-toolbox-app` FastAPI runtime behind NethServer 8 Traefik (`https://<portal_hostname>`).

It provides two dedicated entry flows without requiring NS8 Cluster-Admin or Cockpit access:

1. **Delegatable Secretariat Web Portal** — Office, HR, and front-desk staff belonging to an authorized Active Directory / OpenLDAP group (such as `mail-office`) sign in with their own directory credentials to manage employee out-of-office / sick-leave auto-replies, deputy email forwarding, audit history, and optional read-only mail delivery tracking without ever needing employee passwords.
2. **Public Self-Service Spam Quarantine Release** — Any mailbox user who receives a daily Spam Quarantine Digest email can open a signed, login-free link (`/?quarantine_token=<token>&op=release` or `/quarantine/action?token=<token>&op=release_and_allow`) to inspect and release a quarantined message using a link-scanner-safe two-step confirmation flow.

---

## Directory Layout

```text
app/frontend/
├── index.html                           # SPA HTML shell (<html lang="en">, English default title)
├── package.json                         # Vue 3.5 + Vite 6 scripts & metadata (v1.0.5)
├── package-lock.json                    # Deterministic npm lockfile
├── vite.config.js                       # Relative base ('./'), dist output, /api -> 127.0.0.1:8080 dev proxy
├── public/
│   └── favicon.svg                      # Vector portal icon
└── src/
    ├── main.js                          # Vue 3 application mount
    ├── App.vue                          # Quarantine token detection, session bootstrap, workspace tabs, toasts
    ├── api/
    │   └── client.js                    # Typed fetch client + ApiError status handling for /api/*
    ├── i18n/
    │   └── messages.js                  # Bilingual EN | DE dictionary (100% key parity, English default)
    ├── styles/
    │   └── main.css                     # Enterprise CSS tokens, WCAG AA badges, responsive grid & modal styles
    ├── utils/
    │   └── templateEngine.js            # Mustache-style placeholder & conditional block template renderer
    └── components/
        ├── AppHeader.vue                # Top bar with EN | DE language switcher, user/group chips & workspace tabs
        ├── LoginView.vue                # Directory sign-in with distinct 401 / 403 / 429 error states
        ├── MailboxDashboard.vue         # KPI summary strip, / search shortcut, filter tabs & mailbox table
        ├── VacationModal.vue            # One-click sick-leave & deputy forwarding modal with live preview
        ├── AuditLogView.vue             # Chronological activity & accountability log viewer
        ├── MailTrackingPortalView.vue   # Optional read-only mail tracking search (gated by portal_tracking_enabled)
        └── QuarantineActionView.vue     # Login-free two-step spam quarantine release view
```

---

## Views & Capabilities

### 1. Directory Authentication & Session Lifecycle (`LoginView.vue`, `App.vue`)

- Bootstraps existing cookie sessions on load via `GET /api/auth/me` (`HttpOnly`, `SameSite=Lax` signed session cookie).
- Authenticates against the coupled NS8 OpenLDAP (`rfc2307`) or Samba Active Directory (`ad`) domain via `POST /api/auth/login` (supporting `user`, `DOMAIN\user`, UPN, nested AD groups, and `primaryGroupID` resolution).
- Clearly differentiates authentication failures for front-desk staff:
  - **`401 Unauthorized`**: Invalid username or password.
  - **`403 Forbidden`**: Valid directory credentials, but the account is not a member of any authorized secretariat group configured in NS8 Cockpit Settings.
  - **`429 Too Many Requests`**: Temporary per-IP rate-limit protection.
- Intercepts mid-session `401` responses cleanly and returns to `LoginView.vue` with a session-expired notice.

### 2. Mailbox Overview Dashboard (`MailboxDashboard.vue`)

- Loads `GET /api/mailboxes` and `GET /api/templates` in parallel.
- **Interactive KPI Summary Strip**: Displays `total`, `central_vacation_active`, `sogo_vacation_active`, and `forwards_active`; clicking any KPI card toggles the corresponding table filter.
- **Instant Search & Filter Pills**: Keyboard shortcut `/` focuses the search input, filtering instantaneously across `display_name`, `mail`, `user`, `department`, `title`, `vacation.forward_address`, and all `aliases`.
- **WCAG AA Status & Coexistence Badges**:
  - **Available** (green) when neither central nor personal auto-reply is active.
  - **Sick-Leave / Absence Active** (amber) with localized date range or *Open-ended* indicator, plus a **Scheduled (future)** / **Period ended** warning pill when `enabled && !active_now`.
  - **Personal Out-of-Office (SOGo)** (blue) when `sogo_status.vacation_enabled` is active in SOGo, or **SOGo auto-reply paused** when safely superseded by an active central rule.
- **Forwarding Visibility**: Displays active deputy forwarding with **Copy** (`keep_copy: true`) vs **Direct** (`keep_copy: false`) badges alongside read-only indicators for personal SOGo forwardings (`sogo_status.forward_enabled`) and server-level Postfix forwardings (`postfix_forwards`).
- **Inline Quick-Disable**: Allows ending an active absence rule directly from the table row with a two-click inline confirmation guard.

### 3. One-Click Sick-Leave & Deputy Modal (`VacationModal.vue`, `templateEngine.js`)

- Accessible modal dialog (`role="dialog"`, `aria-modal="true"`, `Tab` focus trap, `Escape` dismissal, focus restoration on close) submitting `PUT /api/mailboxes/{user}/vacation`.
- **SOGo Safe Coexistence Banner**: When `sogo_status.vacation_enabled` is detected on the mailbox, informs staff that the employee's personal SOGo vacation reply is automatically paused (without deleting `sogo.sieve` or personal mail filters) to prevent duplicate auto-replies, and restored once the central rule ends.
- **Flexible Date Scheduling**: Supports open-ended sick leave (empty `start_date` / `end_date`) as well as quick presets (*From today (open-ended)*, *Today & tomorrow*, *Through Friday*, *Next 7 days*).
- **Searchable Colleague Combobox & Loop Protection**: Suggests directory colleagues (highlighting colleagues who are themselves currently absent), accepts custom external or shared-mailbox addresses, and blocks forwarding to any of the mailbox's own primary or alias addresses.
- **Client-Side Template Engine (`src/utils/templateEngine.js`)**:
  - Renders `{{displayName}}`, `{{givenName}}`, `{{sn}}`, `{{mail}}`, `{{startDate}}`, `{{endDate}}` (`YYYY-MM-DD` in `en`, `DD.MM.YYYY` in `de`), and `{{deputyEmail}}`.
  - Evaluates Mustache-style conditional blocks (`{{#startDate}}...{{/startDate}}`, `{{#endDate}}...{{/endDate}}`, `{{#deputyEmail}}...{{/deputyEmail}}`), cleanly omitting empty blocks without leaving stray blank lines.
  - Includes a collapsible **Live Preview** card showing the exact rendered sender, subject, active period, deputy mode, and body before saving.

### 4. Optional Read-Only Mail-Tracking Search (`MailTrackingPortalView.vue`)

- Conditionally displayed in the workspace navigation bar **only** when `currentUser.portal_tracking_enabled === true` (enabled by the administrator in NS8 Cockpit Settings).
- Queries `POST /api/mail-tracking/search` (`sender`, `recipient` with mailbox `<datalist>`, `subject`, `date_from`, `date_to`, and status pills `all`, `delivered`, `spam`, `greylisted`, `rejected`).
- Displays high-contrast delivery status badges, Rspamd score ratios (`score / required_score`), and expandable technical detail rows (`message_id`, connecting `ip`, `rspamd_action`, `size_bytes`, and inspection `symbols`) without exposing message bodies.

### 5. Activity & Audit Log (`AuditLogView.vue`)

- Fetches `GET /api/audit` to provide full accountability across secretariat actions, Cockpit administrator changes, quarantine releases, and background reconciler events, with instant text search and per-mailbox history filtering.

### 6. Login-Free Two-Step Spam Quarantine Release (`QuarantineActionView.vue`)

- Triggered automatically in `App.vue` before authentication whenever `?quarantine_token=<token>` (or `/quarantine/action?token=<token>`) is present in the URL.
- **Step 1 — Read-Only Inspection (`GET /api/quarantine/token/{token}`)**: Validates the token and displays message metadata (`sender`, `recipient_email`, `subject`, `received_at`, `spam_score`) without consuming the token, ensuring corporate email security link scanners cannot accidentally release spam via HTTP `GET` prefetching. Handles `already_used`, `expired`, and invalid token states explicitly.
- **Step 2 — Explicit User Action (`POST /api/quarantine/action`)**: Highlights the preselected operation from `op` (`release` vs `release_and_allow`) and provides one-click buttons to either deliver the single message from `Junk` to `INBOX` or deliver the message and add the sender to the user's personal allowlist.

---

## HTTP API Endpoints Used (`src/api/client.js`)

| Method | Endpoint | Auth | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/auth/me` | Session Cookie | Bootstrap current user, groups, and `portal_tracking_enabled` flag |
| `POST` | `/api/auth/login` | Public | Authenticate AD / LDAP user and verify secretariat group membership |
| `POST` | `/api/auth/logout` | Session Cookie | Revoke server session and clear session cookie |
| `GET` | `/api/mailboxes` | Session Cookie | List directory mailboxes, SOGo Sieve status, active rules, and KPI summary |
| `PUT` | `/api/mailboxes/{user}/vacation` | Session Cookie | Enable, update, or disable central absence auto-reply & deputy forwarding |
| `GET` | `/api/templates` | Session Cookie | Fetch company out-of-office templates (`default_en`, `default_de`, custom) |
| `GET` | `/api/audit` | Session Cookie | Retrieve chronological audit log entries |
| `POST` | `/api/mail-tracking/search` | Session Cookie | Read-only Rspamd / SQLite mail history search (requires `portal_tracking_enabled`) |
| `GET` | `/api/quarantine/token/{token}` | Public (Signed Token) | Non-mutating quarantine token inspection (scanner-safe Step 1) |
| `POST` | `/api/quarantine/action` | Public (Signed Token) | Execute `release` or `release_and_allow` quarantine delivery (Step 2) |

---

## Internationalization (`EN | DE`) Architecture

- **Strict English Default (`src/i18n/messages.js`, `index.html`)**: `detectInitialLocale()` checks `window.localStorage.getItem('ns8_mail_toolbox_lang')` for an explicit `'en'` or `'de'` user preference and otherwise **always defaults to `'en'`** (`<html lang="en">`), never overriding with browser or OS `navigator.language` settings.
- **Header Language Switcher (`AppHeader.vue`)**: An accessible `EN | DE` toggle is visible on every screen (Login, Secretariat Workspace, and Public Quarantine Release), updating reactive UI copy, `<html lang>`, `<title>`, date formatting, and `localStorage` immediately.
- **100% Key Parity**: `messages.en` and `messages.de` maintain identical nested key structures (verified automatically in `tests/verify_ns8_rules.py`).

---

## Development & Build Commands

Requirements: **Node.js 20 LTS** (or newer) and **npm**.

```bash
# Install exact dependencies from lockfile
npm --prefix app/frontend ci

# Start local Vite dev server on http://127.0.0.1:5173 (proxies /api -> http://127.0.0.1:8080)
npm --prefix app/frontend run dev

# Build production bundle into app/frontend/dist/
npm --prefix app/frontend run build
```

During container image builds (`build-images.sh` / `app/Containerfile`), the compiled static assets in `app/frontend/dist/` are copied into `/app/static/` inside `mail-toolbox-app` and served at `/` with SPA fallback for non-`/api` routes.
