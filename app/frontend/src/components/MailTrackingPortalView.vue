<template>
  <div class="panel-card">
    <!-- Header -->
    <div class="tracking-header">
      <div>
        <h2 class="tracking-title">{{ t('tracking.title') }}</h2>
        <p class="tracking-subtitle">{{ t('tracking.subtitle') }}</p>
      </div>
      <span v-if="!loading && !forbiddenState" class="results-count-pill">
        {{ t('tracking.resultsCount', { count: totalCount }) }}
      </span>
    </div>

    <!-- Search & Filter Form -->
    <form
      v-if="!forbiddenState"
      class="tracking-filter-panel"
      @submit.prevent="runSearch"
    >
      <div class="tracking-inputs-grid">
        <div>
          <label class="field-label" for="trk-sender">
            {{ t('tracking.filters.senderLabel') }}
          </label>
          <input
            id="trk-sender"
            v-model="filters.sender"
            type="text"
            class="input-control"
            :placeholder="t('tracking.filters.senderPlaceholder')"
          />
        </div>

        <div>
          <label class="field-label" for="trk-recipient">
            {{ t('tracking.filters.recipientLabel') }}
          </label>
          <input
            id="trk-recipient"
            v-model="filters.recipient"
            type="text"
            list="portal-mailbox-addresses"
            class="input-control"
            :placeholder="t('tracking.filters.recipientPlaceholder')"
          />
          <datalist id="portal-mailbox-addresses">
            <option
              v-for="mb in mailboxAddressOptions"
              :key="mb.mail"
              :value="mb.mail"
            >
              {{ mb.display_name }}
            </option>
          </datalist>
        </div>

        <div>
          <label class="field-label" for="trk-subject">
            {{ t('tracking.filters.subjectLabel') }}
          </label>
          <input
            id="trk-subject"
            v-model="filters.subject"
            type="text"
            class="input-control"
            :placeholder="t('tracking.filters.subjectPlaceholder')"
          />
        </div>

        <div>
          <label class="field-label" for="trk-date-from">
            {{ t('tracking.filters.dateFromLabel') }}
          </label>
          <input
            id="trk-date-from"
            v-model="filters.date_from"
            type="date"
            class="input-control"
          />
        </div>

        <div>
          <label class="field-label" for="trk-date-to">
            {{ t('tracking.filters.dateToLabel') }}
          </label>
          <input
            id="trk-date-to"
            v-model="filters.date_to"
            type="date"
            class="input-control"
            :min="filters.date_from || undefined"
          />
        </div>
      </div>

      <div class="tracking-actions-bar">
        <!-- Status Quick Filter Pills -->
        <div class="filter-pills" role="group" :aria-label="t('tracking.columns.status')">
          <button
            v-for="st in statusOptions"
            :key="st.value"
            type="button"
            class="filter-pill"
            :class="{ 'is-active': filters.action_filter === st.value }"
            :aria-pressed="filters.action_filter === st.value"
            @click="selectStatusFilter(st.value)"
          >
            {{ st.label }}
          </button>
        </div>

        <div class="tracking-btns-right">
          <button
            type="button"
            class="btn btn-secondary btn-sm"
            :disabled="loading"
            @click="setTodayFilter"
          >
            {{ t('tracking.filters.todayBtn') }}
          </button>
          <button
            v-if="hasActiveFilters"
            type="button"
            class="btn btn-secondary btn-sm"
            :disabled="loading"
            @click="resetFilters"
          >
            {{ t('tracking.filters.resetBtn') }}
          </button>
          <button
            type="submit"
            class="btn btn-primary btn-sm"
            :disabled="loading"
          >
            {{
              loading
                ? t('tracking.filters.searchingBtn')
                : t('tracking.filters.searchBtn')
            }}
          </button>
        </div>
      </div>
    </form>

    <!-- 403 Disabled State -->
    <div v-if="forbiddenState" class="tracking-state-box">
      <div class="alert-box alert-box--info" role="status">
        <div>
          <div class="alert-box-title">{{ t('tracking.notices.forbiddenTitle') }}</div>
          <div>{{ t('tracking.notices.forbiddenBody') }}</div>
        </div>
      </div>
    </div>

    <!-- Error Banner -->
    <div v-else-if="errorMessage" class="tracking-state-box">
      <div class="alert-box alert-box--error" role="alert">
        <div>{{ errorMessage }}</div>
      </div>
    </div>

    <template v-else>
      <!-- Rspamd Offline / Cached Info Notice -->
      <div v-if="hasSearched && !rspamdConnected" class="rspamd-notice-wrap">
        <div class="alert-box alert-box--info" role="status">
          <div>
            <div class="alert-box-title">
              {{ t('tracking.notices.rspamdOfflineTitle') }}
            </div>
            <div>{{ t('tracking.notices.rspamdOfflineBody') }}</div>
            <div v-if="rspamdError" class="rspamd-tech-detail">
              {{ rspamdError }}
            </div>
          </div>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loading && entries.length === 0" class="tracking-state-box">
        <div class="tracking-muted-center">
          {{ t('tracking.filters.searchingBtn') }}
        </div>
      </div>

      <!-- Empty -->
      <div v-else-if="entries.length === 0" class="tracking-state-box tracking-empty">
        <h3 class="tracking-empty-title">{{ t('tracking.emptyTitle') }}</h3>
        <p class="tracking-empty-body">{{ t('tracking.emptyBody') }}</p>
      </div>

      <!-- Results Table -->
      <div v-else class="table-responsive">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t('tracking.columns.timestamp') }}</th>
              <th>{{ t('tracking.columns.sender') }}</th>
              <th>{{ t('tracking.columns.recipients') }}</th>
              <th>{{ t('tracking.columns.subject') }}</th>
              <th>{{ t('tracking.columns.status') }}</th>
              <th>{{ t('tracking.columns.score') }}</th>
              <th class="col-details-btn">{{ t('tracking.columns.details') }}</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in entries" :key="row.id">
              <tr>
                <td class="trk-time-cell">{{ formatTimestamp(row.timestamp) }}</td>
                <td class="trk-addr-cell">{{ row.sender || '—' }}</td>
                <td class="trk-addr-cell">{{ formatRecipients(row.recipients) }}</td>
                <td class="trk-subject-cell">
                  {{ row.subject || t('tracking.details.noSubject') }}
                </td>
                <td>
                  <span class="badge" :class="statusBadgeClass(row.status)">
                    <span class="badge-dot" />
                    <span>{{ statusLabel(row.status) }}</span>
                  </span>
                </td>
                <td class="trk-score-cell">
                  {{ formatScore(row.score, row.required_score) }}
                </td>
                <td class="col-details-btn">
                  <button
                    type="button"
                    class="btn btn-secondary btn-sm"
                    :aria-expanded="expandedId === row.id"
                    @click="toggleExpanded(row.id)"
                  >
                    {{
                      expandedId === row.id
                        ? t('tracking.details.hideBtn')
                        : t('tracking.details.showBtn')
                    }}
                  </button>
                </td>
              </tr>

              <!-- Expandable Technical Details Row -->
              <tr v-if="expandedId === row.id" class="trk-details-row">
                <td colspan="7">
                  <div class="trk-details-grid">
                    <div>
                      <div class="trk-detail-label">
                        {{ t('tracking.details.messageId') }}
                      </div>
                      <div class="trk-detail-mono">{{ row.message_id || '—' }}</div>
                    </div>

                    <div>
                      <div class="trk-detail-label">
                        {{ t('tracking.details.senderIp') }}
                      </div>
                      <div class="trk-detail-mono">{{ row.ip || '—' }}</div>
                    </div>

                    <div>
                      <div class="trk-detail-label">
                        {{ t('tracking.details.rspamdAction') }}
                      </div>
                      <div class="trk-detail-mono">{{ row.rspamd_action || '—' }}</div>
                    </div>

                    <div>
                      <div class="trk-detail-label">
                        {{ t('tracking.details.size') }}
                      </div>
                      <div class="trk-detail-mono">{{ formatBytes(row.size_bytes) }}</div>
                    </div>

                    <div
                      v-if="Array.isArray(row.symbols) && row.symbols.length > 0"
                      class="trk-symbols-block"
                    >
                      <div class="trk-detail-label">
                        {{ t('tracking.details.symbols') }}
                      </div>
                      <div class="trk-symbols-list">
                        <span
                          v-for="sym in row.symbols"
                          :key="sym"
                          class="trk-symbol-chip"
                        >
                          {{ sym }}
                        </span>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { api, ApiError } from '../api/client'
import { useI18n } from '../i18n/messages'

const props = defineProps({
  mailboxes: {
    type: Array,
    default: () => [],
  },
})

const { locale, t } = useI18n()

const filters = reactive({
  sender: '',
  recipient: '',
  subject: '',
  action_filter: 'all',
  date_from: '',
  date_to: '',
})

const entries = ref([])
const totalCount = ref(0)
const rspamdConnected = ref(true)
const rspamdError = ref('')
const hasSearched = ref(false)
const loading = ref(false)
const errorMessage = ref('')
const forbiddenState = ref(false)
const expandedId = ref(null)

const mailboxAddressOptions = computed(() =>
  (props.mailboxes || []).filter((mb) => Boolean(mb.mail))
)

const statusOptions = computed(() => [
  { value: 'all', label: t('tracking.statusFilters.all') },
  { value: 'delivered', label: t('tracking.statusFilters.delivered') },
  { value: 'spam', label: t('tracking.statusFilters.spam') },
  { value: 'greylisted', label: t('tracking.statusFilters.greylisted') },
  { value: 'rejected', label: t('tracking.statusFilters.rejected') },
])

const hasActiveFilters = computed(() =>
  Boolean(
    filters.sender.trim() ||
      filters.recipient.trim() ||
      filters.subject.trim() ||
      filters.date_from ||
      filters.date_to ||
      filters.action_filter !== 'all'
  )
)

async function runSearch() {
  loading.value = true
  errorMessage.value = ''
  forbiddenState.value = false
  expandedId.value = null

  try {
    const res = await api.searchMailTracking({
      sender: filters.sender.trim(),
      recipient: filters.recipient.trim(),
      subject: filters.subject.trim(),
      action_filter: filters.action_filter,
      date_from: filters.date_from || '',
      date_to: filters.date_to || '',
      limit: 100,
    })
    entries.value = Array.isArray(res?.entries) ? res.entries : []
    totalCount.value =
      typeof res?.total === 'number' ? res.total : entries.value.length
    rspamdConnected.value = res?.rspamd_connected !== false
    rspamdError.value = res?.rspamd_error || ''
    hasSearched.value = true
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      forbiddenState.value = true
      return
    }
    const detail =
      err instanceof ApiError ? err.detail || `HTTP ${err.status}` : err?.message
    errorMessage.value = t('tracking.notices.loadError', { detail })
  } finally {
    loading.value = false
  }
}

function selectStatusFilter(nextStatus) {
  filters.action_filter = nextStatus
  runSearch()
}

function setTodayFilter() {
  const now = new Date()
  const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  filters.date_from = iso
  filters.date_to = iso
  runSearch()
}

function resetFilters() {
  filters.sender = ''
  filters.recipient = ''
  filters.subject = ''
  filters.action_filter = 'all'
  filters.date_from = ''
  filters.date_to = ''
  runSearch()
}

function toggleExpanded(id) {
  expandedId.value = expandedId.value === id ? null : id
}

function statusBadgeClass(status) {
  const s = String(status || '').toLowerCase()
  if (s === 'delivered') return 'badge--green'
  if (s === 'spam') return 'badge--amber'
  if (s === 'greylisted') return 'badge--blue'
  if (s === 'rejected') return 'badge--red'
  return 'badge--neutral'
}

function statusLabel(status) {
  const s = String(status || '').toLowerCase()
  const key = `tracking.badges.${s}`
  const translated = t(key)
  return translated === key ? status || '—' : translated
}

function formatTimestamp(ts) {
  if (!ts) return '—'
  const parsed = new Date(ts)
  if (Number.isNaN(parsed.getTime())) return String(ts)
  try {
    return new Intl.DateTimeFormat(locale.value === 'de' ? 'de-DE' : 'en-GB', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(parsed)
  } catch {
    return parsed.toISOString()
  }
}

function formatRecipients(recipients) {
  if (!Array.isArray(recipients) || recipients.length === 0) return '—'
  return recipients.join(', ')
}

function formatScore(score, requiredScore) {
  if (score === null || score === undefined || Number.isNaN(Number(score))) {
    return '—'
  }
  const sc = Number(score).toFixed(1)
  if (
    requiredScore !== null &&
    requiredScore !== undefined &&
    !Number.isNaN(Number(requiredScore)) &&
    Number(requiredScore) > 0
  ) {
    return `${sc} / ${Number(requiredScore).toFixed(1)}`
  }
  return sc
}

function formatBytes(bytes) {
  const n = Number(bytes)
  if (!bytes || Number.isNaN(n) || n <= 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}

onMounted(() => {
  runSearch()
})
</script>

<style scoped>
.tracking-header {
  padding: 1.15rem 1.25rem;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.tracking-title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text-primary);
}

.tracking-subtitle {
  margin: 0.2rem 0 0;
  font-size: 0.84rem;
  color: var(--text-secondary);
}

.results-count-pill {
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.22rem 0.65rem;
  border-radius: 999px;
  background-color: var(--bg-muted);
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.tracking-filter-panel {
  padding: 1rem 1.25rem;
  background-color: var(--bg-subtle);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.tracking-inputs-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.75rem;
}

.tracking-actions-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.tracking-btns-right {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.rspamd-notice-wrap {
  padding: 0.85rem 1.25rem 0;
}

.rspamd-tech-detail {
  margin-top: 0.25rem;
  font-family: var(--font-mono);
  font-size: 0.76rem;
  opacity: 0.9;
}

.tracking-state-box {
  padding: 2.25rem 1.5rem;
}

.tracking-muted-center {
  text-align: center;
  color: var(--text-muted);
}

.tracking-empty {
  text-align: center;
  max-width: 480px;
  margin: 0 auto;
}

.tracking-empty-title {
  margin: 0 0 0.3rem;
  font-size: 0.98rem;
  font-weight: 700;
}

.tracking-empty-body {
  margin: 0;
  font-size: 0.85rem;
  color: var(--text-muted);
}

.trk-time-cell {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  font-size: 0.81rem;
  color: var(--text-secondary);
}

.trk-addr-cell {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--text-primary);
  word-break: break-all;
  max-width: 240px;
}

.trk-subject-cell {
  font-weight: 600;
  font-size: 0.86rem;
  color: var(--text-primary);
  max-width: 320px;
  word-break: break-word;
}

.trk-score-cell {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.col-details-btn {
  text-align: right;
  white-space: nowrap;
}

.trk-details-row td {
  background-color: var(--bg-subtle);
  padding: 0.85rem 1.25rem;
}

.trk-details-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.75rem 1rem;
}

.trk-detail-label {
  font-size: 0.74rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--text-muted);
  margin-bottom: 0.2rem;
}

.trk-detail-mono {
  font-family: var(--font-mono);
  font-size: 0.79rem;
  color: var(--text-primary);
  word-break: break-all;
}

.trk-symbols-block {
  grid-column: 1 / -1;
}

.trk-symbols-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.trk-symbol-chip {
  font-family: var(--font-mono);
  font-size: 0.73rem;
  padding: 0.1rem 0.45rem;
  border-radius: var(--radius-sm);
  background-color: var(--bg-surface);
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
}

@media (max-width: 1100px) {
  .tracking-inputs-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .trk-details-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .tracking-inputs-grid,
  .trk-details-grid {
    grid-template-columns: 1fr;
  }
}
</style>
