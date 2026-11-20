<template>
  <div class="panel-card">
    <div class="audit-header">
      <div>
        <h2 class="audit-title">{{ t('audit.title') }}</h2>
        <p class="audit-subtitle">{{ t('audit.subtitle') }}</p>
      </div>

      <button
        type="button"
        class="btn btn-secondary btn-sm"
        :disabled="loading"
        @click="fetchAudit"
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
        <span>{{ t('audit.refresh') }}</span>
      </button>
    </div>

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
          v-model="searchQuery"
          type="search"
          class="input-control search-input"
          :placeholder="t('audit.searchPlaceholder')"
        />
        <button
          v-if="searchQuery"
          type="button"
          class="search-clear-btn"
          @click="searchQuery = ''"
        >
          ×
        </button>
      </div>

      <button
        v-if="targetUserFilter"
        type="button"
        class="btn btn-secondary btn-sm"
        @click="emit('clear-target-filter')"
      >
        {{ t('audit.clearTargetFilter', { user: targetUserFilter }) }} ×
      </button>
    </div>

    <div v-if="errorMessage" class="audit-state-box">
      <div class="alert-box alert-box--error" role="alert">
        <div>{{ errorMessage }}</div>
      </div>
    </div>

    <div v-else-if="loading && entries.length === 0" class="audit-state-box">
      <div class="audit-muted-center">{{ t('dashboard.refreshing') }}</div>
    </div>

    <div v-else-if="filteredEntries.length === 0" class="audit-state-box audit-empty">
      <h3 class="audit-empty-title">{{ t('audit.emptyTitle') }}</h3>
      <p class="audit-empty-body">{{ t('audit.emptyBody') }}</p>
    </div>

    <div v-else class="table-responsive">
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ t('audit.columns.timestamp') }}</th>
            <th>{{ t('audit.columns.actor') }}</th>
            <th>{{ t('audit.columns.source') }}</th>
            <th>{{ t('audit.columns.target') }}</th>
            <th>{{ t('audit.columns.action') }}</th>
            <th>{{ t('audit.columns.details') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in filteredEntries" :key="entry.id || `${entry.timestamp}-${entry.target_user}`">
            <td class="audit-time-cell">{{ formatTimestamp(entry.timestamp) }}</td>
            <td class="audit-actor-cell">{{ entry.actor || '—' }}</td>
            <td>
              <span class="badge badge--neutral">
                {{ formatSource(entry.source) }}
              </span>
            </td>
            <td>
              <div class="audit-target-name">
                {{ resolveUserDisplayName(entry.target_user) }}
              </div>
              <div v-if="entry.target_user" class="audit-target-id">
                {{ entry.target_user }}
              </div>
            </td>
            <td>
              <span class="badge" :class="actionBadgeClass(entry.action)">
                {{ formatAction(entry.action) }}
              </span>
            </td>
            <td class="audit-details-cell">
              {{ formatDetails(entry.details) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, ApiError } from '../api/client'
import { useI18n } from '../i18n/messages'

const props = defineProps({
  mailboxes: {
    type: Array,
    default: () => [],
  },
  targetUserFilter: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['clear-target-filter'])

const { locale, t } = useI18n()

const entries = ref([])
const loading = ref(false)
const errorMessage = ref('')
const searchQuery = ref('')

const userNameMap = computed(() => {
  const map = new Map()
  for (const mb of props.mailboxes || []) {
    if (mb.user) {
      map.set(mb.user, mb.display_name || mb.user)
    }
  }
  return map
})

function resolveUserDisplayName(user) {
  if (!user) return '—'
  return userNameMap.value.get(user) || user
}

async function fetchAudit() {
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await api.getAuditLog()
    entries.value = Array.isArray(res?.entries) ? res.entries : []
  } catch (err) {
    const detail = err instanceof ApiError ? err.detail || `HTTP ${err.status}` : err?.message
    errorMessage.value = t('audit.loadError', { detail })
  } finally {
    loading.value = false
  }
}

const filteredEntries = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return entries.value.filter((item) => {
    if (props.targetUserFilter && item.target_user !== props.targetUserFilter) {
      return false
    }
    if (!q) return true
    const displayTarget = resolveUserDisplayName(item.target_user)
    const haystack = [
      item.actor,
      item.source,
      item.target_user,
      displayTarget,
      item.action,
      item.details,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return haystack.includes(q)
  })
})

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

function formatSource(source) {
  if (!source) return '—'
  const key = `audit.sources.${String(source).toLowerCase()}`
  const translated = t(key)
  return translated === key ? source : translated
}

function formatAction(action) {
  if (!action) return '—'
  const normalized = String(action).toLowerCase().replace(/[\s-]+/g, '_')
  const key = `audit.actions.${normalized}`
  const translated = t(key)
  return translated === key ? action : translated
}

function actionBadgeClass(action) {
  const a = String(action || '').toLowerCase()
  if (a.includes('failed') || a.includes('forbidden')) {
    return 'badge--warn'
  }
  if (a.includes('login_success')) {
    return 'badge--green'
  }
  if (a.includes('disable') || a.includes('expire') || a.includes('off')) {
    return 'badge--neutral'
  }
  if (a.includes('enable') || a.includes('set') || a.includes('update') || a.includes('reactivate')) {
    return 'badge--amber'
  }
  return 'badge--blue'
}

function formatDetails(details) {
  if (details === null || details === undefined || details === '') return '—'
  if (typeof details === 'object') {
    return Object.entries(details)
      .map(([k, v]) => `${k}: ${v}`)
      .join(', ')
  }
  const str = String(details).trim()
  if (str.startsWith('{') && str.endsWith('}')) {
    try {
      const obj = JSON.parse(str)
      const parts = []
      if (typeof obj.enabled === 'boolean') {
        parts.push(obj.enabled ? 'enabled=true' : 'enabled=false')
      }
      if (obj.start_date || obj.end_date) {
        parts.push(`${obj.start_date || '…'} – ${obj.end_date || '…'}`)
      }
      if (obj.forward_enabled && obj.forward_address) {
        parts.push(`→ ${obj.forward_address} (${obj.keep_copy !== false ? 'copy' : 'direct'})`)
      }
      if (parts.length > 0) return parts.join(' · ')
      return Object.entries(obj)
        .map(([k, v]) => `${k}: ${v}`)
        .join(', ')
    } catch {
      return str
    }
  }
  return str
}

onMounted(() => {
  fetchAudit()
})
</script>

<style scoped>
.audit-header {
  padding: 1.15rem 1.25rem;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.audit-title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text-primary);
}

.audit-subtitle {
  margin: 0.2rem 0 0;
  font-size: 0.84rem;
  color: var(--text-secondary);
}

.audit-state-box {
  padding: 2.25rem 1.5rem;
}

.audit-muted-center {
  text-align: center;
  color: var(--text-muted);
}

.audit-empty {
  text-align: center;
  max-width: 460px;
  margin: 0 auto;
}

.audit-empty-title {
  margin: 0 0 0.3rem;
  font-size: 0.98rem;
  font-weight: 700;
}

.audit-empty-body {
  margin: 0;
  font-size: 0.85rem;
  color: var(--text-muted);
}

.audit-time-cell {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  font-size: 0.82rem;
  color: var(--text-secondary);
}

.audit-actor-cell {
  font-weight: 600;
  font-size: 0.86rem;
}

.audit-target-name {
  font-weight: 600;
  font-size: 0.86rem;
  color: var(--text-primary);
}

.audit-target-id {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  color: var(--text-muted);
}

.audit-details-cell {
  font-size: 0.82rem;
  color: var(--text-secondary);
  max-width: 420px;
  word-break: break-word;
}
</style>
