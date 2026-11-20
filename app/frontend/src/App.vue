<template>
  <div class="app-shell">
    <AppHeader
      :user="currentUser"
      :active-tab="activeTab"
      :logging-out="loggingOut"
      :quarantine-mode="Boolean(quarantineToken)"
      @logout="handleLogout"
      @change-tab="handleTabChange"
    />

    <!-- Public Self-Service Quarantine Release View (No Login Required) -->
    <main v-if="quarantineToken">
      <QuarantineActionView
        :token="quarantineToken"
        :initial-op="quarantineOp"
        :is-authenticated="Boolean(currentUser)"
        @exit-quarantine="exitQuarantineMode"
      />
    </main>

    <!-- Session Bootstrap Loading -->
    <main v-else-if="bootstrapping" class="bootstrap-screen" role="status">
      <div class="bootstrap-box">
        <span class="bootstrap-spinner" aria-hidden="true" />
        <span>{{ t('app.loadingSession') }}</span>
      </div>
    </main>

    <!-- Unauthenticated Login View -->
    <main v-else-if="!currentUser">
      <LoginView
        :session-expired-notice="sessionExpiredNotice"
        @authenticated="handleAuthenticated"
      />
    </main>

    <!-- Authenticated Secretariat Workspace -->
    <main v-else class="main-container">
      <MailboxDashboard
        v-if="activeTab === 'mailboxes'"
        :mailboxes="mailboxes"
        :summary="summary"
        :templates="templates"
        :loading="loadingData"
        :error="dataError"
        :last-refreshed-time="lastRefreshedTime"
        :current-user="currentUser"
        @refresh="loadDashboardData"
        @mailbox-updated="handleMailboxUpdated"
        @notify="pushToast"
        @view-user-audit="openAuditForUser"
      />

      <AuditLogView
        v-else-if="activeTab === 'audit'"
        :mailboxes="mailboxes"
        :target-user-filter="auditTargetFilter"
        @clear-target-filter="auditTargetFilter = ''"
      />

      <MailTrackingPortalView
        v-else-if="activeTab === 'tracking' && currentUser.portal_tracking_enabled === true"
        :mailboxes="mailboxes"
      />
    </main>

    <!-- Toast Region -->
    <div
      v-if="toasts.length > 0"
      class="toast-region"
      role="status"
      aria-live="polite"
    >
      <div
        v-for="item in toasts"
        :key="item.id"
        class="toast-item"
        :class="{ 'toast-item--error': item.kind === 'error' }"
      >
        <span>{{ item.text }}</span>
        <button
          type="button"
          class="toast-dismiss-btn"
          aria-label="Dismiss"
          @click="dismissToast(item.id)"
        >
          ×
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api, ApiError, setUnauthorizedHandler } from './api/client'
import { useI18n } from './i18n/messages'
import AppHeader from './components/AppHeader.vue'
import LoginView from './components/LoginView.vue'
import MailboxDashboard from './components/MailboxDashboard.vue'
import AuditLogView from './components/AuditLogView.vue'
import QuarantineActionView from './components/QuarantineActionView.vue'
import MailTrackingPortalView from './components/MailTrackingPortalView.vue'

const { locale, t } = useI18n()

function parseQuarantineQuery() {
  if (typeof window === 'undefined') {
    return { token: '', op: 'release' }
  }
  const params = new URLSearchParams(window.location.search || '')
  const rawToken = (params.get('quarantine_token') || '').trim()
  const rawOp = (params.get('op') || '').trim()
  const op = rawOp === 'release_and_allow' ? 'release_and_allow' : 'release'
  return { token: rawToken, op }
}

const initialQuarantine = parseQuarantineQuery()
const quarantineToken = ref(initialQuarantine.token)
const quarantineOp = ref(initialQuarantine.op)

const bootstrapping = ref(!initialQuarantine.token)
const currentUser = ref(null)
const sessionExpiredNotice = ref(false)
const loggingOut = ref(false)

const activeTab = ref('mailboxes')
const auditTargetFilter = ref('')

const mailboxes = ref([])
const summary = ref(null)
const templates = ref([])
const loadingData = ref(false)
const dataError = ref('')
const lastRefreshedTime = ref('')

const toasts = ref([])
let toastCounter = 0

function syncLocationQuery() {
  const next = parseQuarantineQuery()
  quarantineToken.value = next.token
  quarantineOp.value = next.op
}

async function exitQuarantineMode() {
  if (typeof window !== 'undefined' && window.history?.replaceState) {
    const params = new URLSearchParams(window.location.search || '')
    params.delete('quarantine_token')
    params.delete('op')
    const rest = params.toString()
    const cleanUrl = `${window.location.pathname || '/'}${rest ? `?${rest}` : ''}${window.location.hash || ''}`
    window.history.replaceState({}, '', cleanUrl)
  }
  quarantineToken.value = ''
  quarantineOp.value = 'release'

  if (!currentUser.value) {
    bootstrapping.value = true
    try {
      const me = await api.getMe()
      if (me && me.authenticated !== false && me.username) {
        currentUser.value = me
        await loadDashboardData()
      }
    } catch {
      currentUser.value = null
    } finally {
      bootstrapping.value = false
    }
  } else if (mailboxes.value.length === 0) {
    await loadDashboardData()
  }
}

function pushToast({ kind = 'success', text = '' }) {
  if (!text) return
  const id = ++toastCounter
  toasts.value.push({ id, kind, text })
  setTimeout(() => {
    dismissToast(id)
  }, 4500)
}

function dismissToast(id) {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

function formatNowTime() {
  try {
    return new Intl.DateTimeFormat(locale.value === 'de' ? 'de-DE' : 'en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(new Date())
  } catch {
    return new Date().toLocaleTimeString()
  }
}

async function loadDashboardData({ silent = false } = {}) {
  if (!silent) {
    loadingData.value = true
    dataError.value = ''
  }

  try {
    const [mbRes, tplRes] = await Promise.all([
      api.getMailboxes(),
      api.getTemplates().catch(() => null),
    ])

    mailboxes.value = Array.isArray(mbRes?.mailboxes) ? mbRes.mailboxes : []
    summary.value = mbRes?.summary || null

    if (Array.isArray(tplRes?.templates)) {
      templates.value = tplRes.templates
    }

    lastRefreshedTime.value = formatNowTime()
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      return
    }
    if (!silent) {
      const detail =
        err instanceof ApiError ? err.detail || `HTTP ${err.status}` : err?.message
      dataError.value = t('dashboard.toasts.loadError', { detail })
    }
  } finally {
    if (!silent) {
      loadingData.value = false
    }
  }
}

function handleMailboxUpdated(updatedMailbox) {
  if (updatedMailbox && updatedMailbox.user) {
    const idx = mailboxes.value.findIndex((m) => m.user === updatedMailbox.user)
    if (idx !== -1) {
      mailboxes.value.splice(idx, 1, updatedMailbox)
    }
    summary.value = null
  }
  loadDashboardData({ silent: true })
}

function handleAuthenticated(userPayload) {
  sessionExpiredNotice.value = false
  currentUser.value = userPayload
  activeTab.value = 'mailboxes'
  auditTargetFilter.value = ''
  loadDashboardData()
}

async function handleLogout() {
  loggingOut.value = true
  try {
    await api.logout()
  } catch {
    // Proceed with client state reset even if network fails
  } finally {
    loggingOut.value = false
    currentUser.value = null
    mailboxes.value = []
    summary.value = null
    templates.value = []
    auditTargetFilter.value = ''
    sessionExpiredNotice.value = false
  }
}

function handleTabChange(nextTab) {
  if (nextTab === 'mailboxes') {
    auditTargetFilter.value = ''
  }
  activeTab.value = nextTab
}

function openAuditForUser(username) {
  auditTargetFilter.value = username || ''
  activeTab.value = 'audit'
}

onMounted(async () => {
  if (typeof window !== 'undefined') {
    window.addEventListener('popstate', syncLocationQuery)
  }

  setUnauthorizedHandler(() => {
    if (currentUser.value) {
      currentUser.value = null
      mailboxes.value = []
      summary.value = null
      sessionExpiredNotice.value = true
    }
  })

  // When opened via ?quarantine_token=..., render QuarantineActionView immediately without requiring login
  if (quarantineToken.value) {
    bootstrapping.value = false
    return
  }

  try {
    const me = await api.getMe()
    if (me && me.authenticated !== false && me.username) {
      currentUser.value = me
      await loadDashboardData()
    }
  } catch {
    currentUser.value = null
  } finally {
    bootstrapping.value = false
  }
})

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('popstate', syncLocationQuery)
  }
})
</script>

<style scoped>
.bootstrap-screen {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 3rem 1rem;
}

.bootstrap-box {
  display: inline-flex;
  align-items: center;
  gap: 0.7rem;
  color: var(--text-secondary);
  font-size: 0.94rem;
  font-weight: 500;
}

.bootstrap-spinner {
  width: 18px;
  height: 18px;
  border: 2.5px solid var(--border-default);
  border-top-color: var(--brand-primary);
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
}

.toast-dismiss-btn {
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
  padding: 0.1rem 0.25rem;
}

.toast-dismiss-btn:hover {
  color: #ffffff;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
