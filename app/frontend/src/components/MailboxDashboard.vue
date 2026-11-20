<template>
  <div>
    <!-- Top Summary KPI Strip -->
    <div class="kpi-grid">
      <button
        type="button"
        class="kpi-card kpi-card--total"
        :class="{ 'is-selected': activeFilter === 'all' }"
        @click="activeFilter = 'all'"
      >
        <div class="kpi-label">{{ t('dashboard.kpi.total') }}</div>
        <div class="kpi-value">{{ effectiveSummary.total }}</div>
        <div class="kpi-hint">{{ t('dashboard.kpi.totalHint') }}</div>
      </button>

      <button
        type="button"
        class="kpi-card kpi-card--central"
        :class="{ 'is-selected': activeFilter === 'central' }"
        @click="activeFilter = activeFilter === 'central' ? 'all' : 'central'"
      >
        <div class="kpi-label">{{ t('dashboard.kpi.centralActive') }}</div>
        <div class="kpi-value">{{ effectiveSummary.central_vacation_active }}</div>
        <div class="kpi-hint">{{ t('dashboard.kpi.centralActiveHint') }}</div>
      </button>

      <button
        type="button"
        class="kpi-card kpi-card--sogo"
        :class="{ 'is-selected': activeFilter === 'sogo' }"
        @click="activeFilter = activeFilter === 'sogo' ? 'all' : 'sogo'"
      >
        <div class="kpi-label">{{ t('dashboard.kpi.sogoActive') }}</div>
        <div class="kpi-value">{{ effectiveSummary.sogo_vacation_active }}</div>
        <div class="kpi-hint">{{ t('dashboard.kpi.sogoActiveHint') }}</div>
      </button>

      <button
        type="button"
        class="kpi-card kpi-card--forwards"
        :class="{ 'is-selected': activeFilter === 'forwarding' }"
        @click="activeFilter = activeFilter === 'forwarding' ? 'all' : 'forwarding'"
      >
        <div class="kpi-label">{{ t('dashboard.kpi.forwardsActive') }}</div>
        <div class="kpi-value">{{ effectiveSummary.forwards_active }}</div>
        <div class="kpi-hint">{{ t('dashboard.kpi.forwardsActiveHint') }}</div>
      </button>
    </div>

    <!-- Main Mailbox Table Card -->
    <div class="panel-card">
      <!-- Search + Refresh Toolbar -->
      <div class="toolbar-bar">
        <div class="search-box">
          <svg
            class="search-box-icon"
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2" />
            <path
              d="M16.5 16.5L21 21"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            />
          </svg>
          <input
            ref="searchInputRef"
            v-model="searchQuery"
            type="search"
            class="input-control search-input"
            :placeholder="t('dashboard.searchPlaceholder')"
            :aria-label="t('dashboard.searchPlaceholder')"
          />
          <button
            v-if="searchQuery"
            type="button"
            class="search-clear-btn"
            :aria-label="t('dashboard.clearSearch')"
            @click="searchQuery = ''"
          >
            ×
          </button>
        </div>

        <div class="toolbar-right">
          <span v-if="lastRefreshedTime" class="last-updated-text">
            {{ t('dashboard.lastUpdated') }}: {{ lastRefreshedTime }}
          </span>
          <button
            type="button"
            class="btn btn-secondary btn-sm"
            :disabled="loading"
            @click="emit('refresh')"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M20 11C19.5 6.5 15.7 3 11 3C6.03 3 2 7.03 2 12C2 16.97 6.03 21 11 21C15.2 21 18.7 18.1 19.7 14.2M20 4V11H13"
                stroke="currentColor"
                stroke-width="1.9"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <span>
              {{ loading ? t('dashboard.refreshing') : t('dashboard.refresh') }}
            </span>
          </button>
        </div>
      </div>

      <!-- Quick Filter Tabs -->
      <div class="filter-tabs-row">
        <div class="filter-pills" role="tablist" aria-label="Mailbox filters">
          <button
            v-for="tab in filterTabs"
            :key="tab.id"
            type="button"
            role="tab"
            class="filter-pill"
            :class="{ 'is-active': activeFilter === tab.id }"
            :aria-selected="activeFilter === tab.id"
            @click="activeFilter = tab.id"
          >
            <span>{{ tab.label }}</span>
            <span class="filter-pill-count">{{ tab.count }}</span>
          </button>
        </div>
      </div>

      <!-- Loading Skeleton / Error / Empty States -->
      <div v-if="error" class="table-state-box">
        <div class="alert-box alert-box--error" role="alert">
          <div>{{ error }}</div>
        </div>
      </div>

      <div v-else-if="loading && mailboxes.length === 0" class="table-state-box">
        <div class="loading-text">{{ t('dashboard.refreshing') }}</div>
      </div>

      <div v-else-if="mailboxes.length === 0" class="table-state-box empty-state">
        <h3 class="empty-state-title">{{ t('dashboard.empty.noMailboxesTitle') }}</h3>
        <p class="empty-state-body">{{ t('dashboard.empty.noMailboxesBody') }}</p>
      </div>

      <div v-else-if="filteredMailboxes.length === 0" class="table-state-box empty-state">
        <h3 class="empty-state-title">{{ t('dashboard.empty.noFilterMatchesTitle') }}</h3>
        <p class="empty-state-body">{{ t('dashboard.empty.noFilterMatchesBody') }}</p>
        <button
          type="button"
          class="btn btn-secondary btn-sm"
          @click="resetSearchAndFilter"
        >
          {{ t('dashboard.empty.resetFilters') }}
        </button>
      </div>

      <!-- Mailbox Table -->
      <div v-else class="table-responsive">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t('dashboard.columns.employee') }}</th>
              <th>{{ t('dashboard.columns.status') }}</th>
              <th>{{ t('dashboard.columns.forwarding') }}</th>
              <th class="col-actions">{{ t('dashboard.columns.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="mb in filteredMailboxes"
              :key="mb.user"
              :class="{ 'row-central-active': mb.vacation?.enabled }"
            >
              <!-- Employee Column -->
              <td>
                <div class="emp-name-row">
                  <span class="emp-name">{{ mb.display_name || mb.user }}</span>
                  <span
                    v-if="mb.enabled === false"
                    class="badge badge--neutral emp-disabled-badge"
                  >
                    {{ t('dashboard.badges.accountDisabled') }}
                  </span>
                </div>

                <div v-if="mb.department || mb.title" class="emp-dept-row">
                  {{ [mb.department, mb.title].filter(Boolean).join(' · ') }}
                </div>

                <div class="emp-mail-row">
                  <span class="emp-primary-mail">{{ mb.mail || mb.user }}</span>
                  <span
                    v-if="Array.isArray(mb.aliases) && mb.aliases.length > 0"
                    class="alias-tooltip-trigger"
                    tabindex="0"
                    :title="formatAliasesTooltip(mb.aliases)"
                  >
                    {{ t('dashboard.badges.aliasesCount', { count: mb.aliases.length }) }}
                    <span class="alias-popover" role="tooltip">
                      <strong>{{ t('dashboard.badges.aliasesTooltipPrefix') }}</strong>
                      <span
                        v-for="al in mb.aliases"
                        :key="al"
                        class="alias-popover-item"
                      >
                        {{ al }}
                      </span>
                    </span>
                  </span>
                </div>
              </td>

              <!-- Status Column -->
              <td>
                <div class="status-cell-stack">
                  <!-- Central Vacation Active -->
                  <template v-if="mb.vacation?.enabled">
                    <div class="status-badge-line">
                      <span class="badge badge--amber">
                        <span class="badge-dot" />
                        <span>{{ t('dashboard.badges.centralActive') }}</span>
                      </span>

                      <span
                        v-if="mb.vacation.active_now === false"
                        class="badge badge--warn"
                      >
                        {{ getInactiveWarningLabel(mb.vacation) }}
                      </span>
                    </div>

                    <div class="status-date-line">
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        aria-hidden="true"
                      >
                        <rect
                          x="3"
                          y="5"
                          width="18"
                          height="16"
                          rx="2"
                          stroke="currentColor"
                          stroke-width="1.8"
                        />
                        <path d="M3 10H21M8 3V7M16 3V7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
                      </svg>
                      <span>{{ formatMailboxDateRange(mb.vacation) }}</span>
                      <span
                        v-if="mb.vacation.updated_by"
                        class="status-actor-hint"
                      >
                        · {{ t('dashboard.badges.updatedBy', { actor: mb.vacation.updated_by }) }}
                      </span>
                    </div>

                    <!-- SOGo Paused Indicator if both exist -->
                    <div v-if="mb.sogo_status?.vacation_enabled" class="status-badge-line">
                      <span class="badge badge--blue">
                        <span class="badge-dot" />
                        <span>{{ t('dashboard.badges.sogoPaused') }}</span>
                      </span>
                    </div>
                  </template>

                  <!-- Personal SOGo Out-of-Office Only -->
                  <template v-else-if="mb.sogo_status?.vacation_enabled">
                    <div class="status-badge-line">
                      <span class="badge badge--blue">
                        <span class="badge-dot" />
                        <span>{{ t('dashboard.badges.sogoVacation') }}</span>
                      </span>
                    </div>
                  </template>

                  <!-- Available -->
                  <template v-else>
                    <div class="status-badge-line">
                      <span class="badge badge--green">
                        <span class="badge-dot" />
                        <span>{{ t('dashboard.badges.available') }}</span>
                      </span>
                    </div>
                  </template>
                </div>
              </td>

              <!-- Forwarding Column -->
              <td>
                <div class="forward-cell-stack">
                  <!-- Central Deputy Forwarding -->
                  <div
                    v-if="mb.vacation?.enabled && mb.vacation?.forward_enabled && mb.vacation?.forward_address"
                    class="forward-item"
                  >
                    <div class="forward-address-line">
                      <span class="forward-arrow" aria-hidden="true">→</span>
                      <span class="forward-target-mail">{{ mb.vacation.forward_address }}</span>
                      <span
                        v-if="mb.vacation.keep_copy !== false"
                        class="badge badge--copy"
                        :title="t('dashboard.badges.forwardCopyTitle')"
                      >
                        {{ t('dashboard.badges.forwardCopy') }}
                      </span>
                      <span
                        v-else
                        class="badge badge--direct"
                        :title="t('dashboard.badges.forwardDirectTitle')"
                      >
                        {{ t('dashboard.badges.forwardDirect') }}
                      </span>
                    </div>
                    <div
                      v-if="resolveColleagueName(mb.vacation.forward_address)"
                      class="forward-colleague-sub"
                    >
                      {{ resolveColleagueName(mb.vacation.forward_address) }}
                    </div>
                  </div>

                  <!-- Read-only SOGo Forwarding Indicator -->
                  <div
                    v-if="mb.sogo_status?.forward_enabled"
                    class="readonly-forward-pill"
                    :title="t('dashboard.badges.sogoForwardReadonly')"
                  >
                    <span class="readonly-tag">SOGo:</span>
                    <span class="readonly-addr">
                      {{ mb.sogo_status.forward_address || t('dashboard.badges.forwardActiveShort') }}
                    </span>
                  </div>

                  <!-- Read-only Postfix / Server Forwarding Indicator -->
                  <div
                    v-if="Array.isArray(mb.postfix_forwards) && mb.postfix_forwards.length > 0"
                    class="readonly-forward-pill"
                    :title="t('dashboard.badges.postfixForwardReadonly')"
                  >
                    <span class="readonly-tag">Server:</span>
                    <span class="readonly-addr">
                      {{ mb.postfix_forwards.join(', ') }}
                    </span>
                  </div>

                  <!-- No Forwarding -->
                  <span
                    v-if="
                      !(mb.vacation?.enabled && mb.vacation?.forward_enabled && mb.vacation?.forward_address) &&
                      !mb.sogo_status?.forward_enabled &&
                      !(Array.isArray(mb.postfix_forwards) && mb.postfix_forwards.length > 0)
                    "
                    class="no-forward-dash"
                    :aria-label="t('dashboard.badges.noForward')"
                  >
                    —
                  </span>
                </div>
              </td>

              <!-- Actions Column -->
              <td class="col-actions">
                <div v-if="confirmingDisableUser === mb.user" class="inline-confirm-box">
                  <span class="inline-confirm-label">
                    {{ t('dashboard.actions.confirmDisablePrompt') }}
                  </span>
                  <div class="inline-confirm-btns">
                    <button
                      type="button"
                      class="btn btn-danger-solid btn-sm"
                      :disabled="disablingUser === mb.user"
                      @click="executeQuickDisable(mb)"
                    >
                      {{
                        disablingUser === mb.user
                          ? t('dashboard.actions.disabling')
                          : t('dashboard.actions.confirmDisableYes')
                      }}
                    </button>
                    <button
                      type="button"
                      class="btn btn-secondary btn-sm"
                      :disabled="disablingUser === mb.user"
                      @click="confirmingDisableUser = null"
                    >
                      {{ t('dashboard.actions.confirmDisableCancel') }}
                    </button>
                  </div>
                </div>

                <div v-else class="row-actions-group">
                  <button
                    type="button"
                    class="btn btn-sm"
                    :class="mb.vacation?.enabled ? 'btn-secondary' : 'btn-primary'"
                    @click="openModal(mb)"
                  >
                    {{ t('dashboard.actions.editVacation') }}
                  </button>

                  <button
                    v-if="mb.vacation?.enabled"
                    type="button"
                    class="btn btn-danger-outline btn-sm"
                    @click="confirmingDisableUser = mb.user"
                  >
                    {{ t('dashboard.actions.quickDisable') }}
                  </button>

                  <button
                    type="button"
                    class="btn btn-secondary btn-sm btn-icon-only"
                    :title="t('dashboard.actions.viewAudit')"
                    :aria-label="`${t('dashboard.actions.viewAudit')}: ${mb.display_name || mb.user}`"
                    @click="emit('view-user-audit', mb.user)"
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <path
                        d="M12 8V12L15 14M21 12C21 16.97 16.97 21 12 21C7.03 21 3 16.97 3 12C3 7.03 7.03 3 12 3C16.97 3 21 7.03 21 12Z"
                        stroke="currentColor"
                        stroke-width="1.9"
                        stroke-linecap="round"
                      />
                    </svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- One-Click Sick-Leave Modal -->
    <VacationModal
      v-if="editingMailbox"
      :mailbox="editingMailbox"
      :all-mailboxes="mailboxes"
      :templates="templates"
      :current-user="currentUser"
      @close="editingMailbox = null"
      @saved="handleModalSaved"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api, ApiError } from '../api/client'
import { useI18n } from '../i18n/messages'
import { formatDateRange } from '../utils/templateEngine'
import VacationModal from './VacationModal.vue'

const props = defineProps({
  mailboxes: {
    type: Array,
    default: () => [],
  },
  summary: {
    type: Object,
    default: null,
  },
  templates: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
  lastRefreshedTime: {
    type: String,
    default: '',
  },
  currentUser: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['refresh', 'mailbox-updated', 'notify', 'view-user-audit'])

const { locale, t } = useI18n()

const searchQuery = ref('')
const activeFilter = ref('all')
const editingMailbox = ref(null)
const confirmingDisableUser = ref(null)
const disablingUser = ref(null)
const searchInputRef = ref(null)

function hasAnyForwarding(mb) {
  const centralFwd = Boolean(
    mb.vacation?.enabled &&
      mb.vacation?.forward_enabled &&
      mb.vacation?.forward_address
  )
  const sogoFwd = Boolean(mb.sogo_status?.forward_enabled)
  const postfixFwd = Array.isArray(mb.postfix_forwards) && mb.postfix_forwards.length > 0
  return centralFwd || sogoFwd || postfixFwd
}

const computedCounts = computed(() => {
  const list = props.mailboxes || []
  let central = 0
  let sogo = 0
  let forwards = 0

  for (const mb of list) {
    if (mb.vacation?.enabled) central += 1
    if (mb.sogo_status?.vacation_enabled) sogo += 1
    if (hasAnyForwarding(mb)) forwards += 1
  }

  return {
    total: list.length,
    central_vacation_active: central,
    sogo_vacation_active: sogo,
    forwards_active: forwards,
  }
})

const effectiveSummary = computed(() => {
  if (props.summary && typeof props.summary.total === 'number') {
    return {
      total: props.summary.total ?? computedCounts.value.total,
      central_vacation_active:
        props.summary.central_vacation_active ??
        computedCounts.value.central_vacation_active,
      sogo_vacation_active:
        props.summary.sogo_vacation_active ??
        computedCounts.value.sogo_vacation_active,
      forwards_active:
        props.summary.forwards_active ?? computedCounts.value.forwards_active,
    }
  }
  return computedCounts.value
})

const filterTabs = computed(() => [
  {
    id: 'all',
    label: t('dashboard.filters.all'),
    count: computedCounts.value.total,
  },
  {
    id: 'central',
    label: t('dashboard.filters.central'),
    count: computedCounts.value.central_vacation_active,
  },
  {
    id: 'sogo',
    label: t('dashboard.filters.sogo'),
    count: computedCounts.value.sogo_vacation_active,
  },
  {
    id: 'forwarding',
    label: t('dashboard.filters.forwarding'),
    count: computedCounts.value.forwards_active,
  },
])

const mailToColleagueMap = computed(() => {
  const map = new Map()
  for (const mb of props.mailboxes || []) {
    if (mb.mail) {
      map.set(mb.mail.toLowerCase(), mb.display_name || mb.user)
    }
    if (Array.isArray(mb.aliases)) {
      for (const al of mb.aliases) {
        if (al) map.set(String(al).toLowerCase(), mb.display_name || mb.user)
      }
    }
  }
  return map
})

function resolveColleagueName(email) {
  if (!email) return ''
  return mailToColleagueMap.value.get(String(email).toLowerCase()) || ''
}

const filteredMailboxes = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return (props.mailboxes || []).filter((mb) => {
    if (activeFilter.value === 'central' && !mb.vacation?.enabled) return false
    if (activeFilter.value === 'sogo' && !mb.sogo_status?.vacation_enabled) {
      return false
    }
    if (activeFilter.value === 'forwarding' && !hasAnyForwarding(mb)) {
      return false
    }

    if (!q) return true

    const haystack = [
      mb.display_name,
      mb.mail,
      mb.user,
      mb.department,
      mb.title,
      mb.vacation?.forward_address,
      ...(Array.isArray(mb.aliases) ? mb.aliases : []),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()

    return haystack.includes(q)
  })
})

function resetSearchAndFilter() {
  searchQuery.value = ''
  activeFilter.value = 'all'
}

function formatAliasesTooltip(aliases = []) {
  return `${t('dashboard.badges.aliasesTooltipPrefix')}\n${aliases.join('\n')}`
}

function formatMailboxDateRange(vacation) {
  return formatDateRange(vacation?.start_date, vacation?.end_date, locale.value, {
    openEnded: t('dashboard.badges.openEnded'),
    fromPrefix: t('dashboard.badges.fromPrefix'),
    untilPrefix: t('dashboard.badges.untilPrefix'),
  })
}

function getInactiveWarningLabel(vacation) {
  const now = new Date()
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  if (vacation?.end_date && vacation.end_date < todayIso) {
    return t('dashboard.badges.expiredWarning')
  }
  return t('dashboard.badges.scheduledWarning')
}

function openModal(mb) {
  confirmingDisableUser.value = null
  editingMailbox.value = mb
}

function handleModalSaved({ mailbox: updatedMailbox, enabled }) {
  const targetName =
    editingMailbox.value?.display_name || editingMailbox.value?.user || ''
  editingMailbox.value = null
  emit('mailbox-updated', updatedMailbox)
  emit('notify', {
    kind: 'success',
    text: enabled
      ? t('dashboard.toasts.savedEnabled', { name: targetName })
      : t('dashboard.toasts.savedDisabled', { name: targetName }),
  })
}

async function executeQuickDisable(mb) {
  disablingUser.value = mb.user
  try {
    const existing = mb.vacation || {}
    const payload = {
      user: mb.user,
      enabled: false,
      start_date: existing.start_date || '',
      end_date: existing.end_date || '',
      subject: existing.subject || '',
      body: existing.body || '',
      forward_enabled: Boolean(existing.forward_enabled),
      forward_address: existing.forward_address || '',
      keep_copy: existing.keep_copy !== undefined ? Boolean(existing.keep_copy) : true,
      updated_by: props.currentUser?.username || '',
    }
    const updated = await api.updateVacation(mb.user, payload)
    confirmingDisableUser.value = null
    emit('mailbox-updated', updated?.mailbox || updated)
    emit('notify', {
      kind: 'success',
      text: t('dashboard.toasts.savedDisabled', {
        name: mb.display_name || mb.user,
      }),
    })
  } catch (err) {
    const detail = err instanceof ApiError ? err.detail || `HTTP ${err.status}` : err?.message
    emit('notify', {
      kind: 'error',
      text: t('dashboard.toasts.disableError', { detail }),
    })
  } finally {
    disablingUser.value = null
  }
}

function handleGlobalSlashShortcut(e) {
  if (e.key !== '/' || editingMailbox.value) return
  const activeTag = document.activeElement?.tagName?.toLowerCase()
  if (
    activeTag === 'input' ||
    activeTag === 'textarea' ||
    activeTag === 'select' ||
    document.activeElement?.isContentEditable
  ) {
    return
  }
  e.preventDefault()
  searchInputRef.value?.focus()
}

onMounted(() => {
  window.addEventListener('keydown', handleGlobalSlashShortcut)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalSlashShortcut)
})
</script>

<style scoped>
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.last-updated-text {
  font-size: 0.78rem;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.table-state-box {
  padding: 2.25rem 1.5rem;
}

.loading-text {
  text-align: center;
  color: var(--text-muted);
  font-size: 0.92rem;
}

.empty-state {
  text-align: center;
  max-width: 480px;
  margin: 0 auto;
}

.empty-state-title {
  margin: 0 0 0.35rem;
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
}

.empty-state-body {
  margin: 0 0 1rem;
  font-size: 0.86rem;
  color: var(--text-muted);
}

/* Employee Cell */
.emp-name-row {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  flex-wrap: wrap;
}

.emp-name {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 0.94rem;
}

.emp-disabled-badge {
  font-size: 0.7rem;
  padding: 0.06rem 0.42rem;
}

.emp-dept-row {
  font-size: 0.8rem;
  color: var(--text-secondary);
  margin-top: 0.1rem;
}

.emp-mail-row {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  flex-wrap: wrap;
  margin-top: 0.22rem;
}

.emp-primary-mail {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--text-muted);
}

.alias-tooltip-trigger {
  position: relative;
  display: inline-flex;
  align-items: center;
  padding: 0.05rem 0.42rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--brand-primary);
  background-color: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: var(--radius-sm);
  cursor: help;
}

.alias-popover {
  display: none;
  position: absolute;
  left: 0;
  top: calc(100% + 5px);
  z-index: 25;
  min-width: 210px;
  padding: 0.5rem 0.65rem;
  background-color: #0f172a;
  color: #f8fafc;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  font-size: 0.76rem;
  font-weight: 400;
  flex-direction: column;
  gap: 0.2rem;
}

.alias-tooltip-trigger:hover .alias-popover,
.alias-tooltip-trigger:focus .alias-popover {
  display: flex;
}

.alias-popover-item {
  font-family: var(--font-mono);
  font-size: 0.74rem;
  color: #cbd5e1;
}

/* Status Cell */
.status-cell-stack {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.status-badge-line {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.status-date-line {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
  font-size: 0.8rem;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.status-actor-hint {
  color: var(--text-muted);
  font-size: 0.76rem;
}

/* Forwarding Cell */
.forward-cell-stack {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.forward-address-line {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.forward-arrow {
  color: var(--brand-primary);
  font-weight: 700;
}

.forward-target-mail {
  font-family: var(--font-mono);
  font-size: 0.81rem;
  color: var(--text-primary);
  font-weight: 600;
}

.forward-colleague-sub {
  font-size: 0.77rem;
  color: var(--text-muted);
  padding-left: 1.1rem;
}

.readonly-forward-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.76rem;
  padding: 0.14rem 0.5rem;
  background-color: var(--bg-muted);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  width: fit-content;
}

.readonly-tag {
  font-weight: 700;
  color: var(--text-muted);
}

.readonly-addr {
  font-family: var(--font-mono);
  font-size: 0.74rem;
}

.no-forward-dash {
  color: var(--border-strong);
}

/* Actions Cell */
.col-actions {
  text-align: right;
  white-space: nowrap;
}

.row-actions-group {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.45rem;
}

.btn-icon-only {
  padding: 0.34rem 0.5rem;
}

.inline-confirm-box {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.35rem;
  background-color: #fff5f5;
  border: 1px solid var(--status-red-border);
  padding: 0.45rem 0.6rem;
  border-radius: var(--radius-md);
}

.inline-confirm-label {
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--status-red-text);
}

.inline-confirm-btns {
  display: flex;
  gap: 0.35rem;
}
</style>
