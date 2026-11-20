<template>
  <section class="quarantine-wrapper">
    <div class="quarantine-card panel-card">
      <!-- Card Header -->
      <div class="quarantine-card-header">
        <div class="quarantine-header-row">
          <span class="quarantine-shield-icon" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 3L4 7V12C4 16.97 7.41 21.6 12 22.8C16.59 21.6 20 16.97 20 12V7L12 3Z"
                stroke="currentColor"
                stroke-width="1.9"
                stroke-linejoin="round"
              />
              <path
                d="M8.5 12.2L10.9 14.6L15.8 9.7"
                stroke="currentColor"
                stroke-width="1.9"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <div>
            <h1 class="quarantine-title">{{ t('quarantine.heading') }}</h1>
            <p class="quarantine-subtitle">{{ t('quarantine.subtitle') }}</p>
          </div>
        </div>
      </div>

      <!-- Card Body -->
      <div class="quarantine-card-body">
        <!-- Loading State -->
        <div v-if="loading" class="quarantine-loading-box" role="status">
          <span class="quarantine-spinner" aria-hidden="true" />
          <span>{{ t('quarantine.loadingToken') }}</span>
        </div>

        <!-- Network / Unexpected Inspection Error -->
        <div
          v-else-if="inspectError"
          class="alert-box alert-box--error"
          role="alert"
        >
          <div>
            <div class="alert-box-title">{{ t('quarantine.states.actionErrorTitle') }}</div>
            <div>{{ inspectError }}</div>
          </div>
        </div>

        <!-- Completed Action Success State -->
        <template v-else-if="actionResult">
          <div class="success-banner" role="status">
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              style="flex-shrink: 0; margin-top: 2px"
            >
              <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.9" />
              <path
                d="M8.5 12.3L10.9 14.7L15.6 9.8"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <div class="success-banner-content">
              <div class="alert-box-title">{{ t('quarantine.success.title') }}</div>
              <div>
                {{
                  t('quarantine.success.deliveredBody', {
                    recipient:
                      tokenInfo?.recipient_email || tokenInfo?.recipient_user || 'INBOX',
                  })
                }}
              </div>
              <div v-if="actionResult.allowlisted" class="allowlist-confirm-box">
                <span class="badge badge--green">
                  <span class="badge-dot" />
                  <span>{{ t('quarantine.success.allowlistedBadge') }}</span>
                </span>
                <p class="allowlist-confirm-text">
                  {{
                    t('quarantine.success.allowlistedBody', {
                      sender: tokenInfo?.sender || '—',
                    })
                  }}
                </p>
              </div>
            </div>
          </div>

          <!-- Keep message metadata visible for user reference -->
          <div v-if="hasMessageMetadata" class="message-meta-card">
            <dl class="meta-dl">
              <div class="meta-row">
                <dt>{{ t('quarantine.meta.sender') }}</dt>
                <dd class="meta-mono">{{ tokenInfo.sender || '—' }}</dd>
              </div>
              <div class="meta-row">
                <dt>{{ t('quarantine.meta.recipient') }}</dt>
                <dd class="meta-mono">
                  {{ tokenInfo.recipient_email || tokenInfo.recipient_user || '—' }}
                </dd>
              </div>
              <div class="meta-row">
                <dt>{{ t('quarantine.meta.subject') }}</dt>
                <dd class="meta-subject">
                  {{ tokenInfo.subject || t('quarantine.meta.noSubject') }}
                </dd>
              </div>
            </dl>
          </div>
        </template>

        <!-- Already Used Token State -->
        <template v-else-if="tokenInfo?.already_used">
          <div class="alert-box alert-box--info" role="status">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              style="flex-shrink: 0; margin-top: 2px"
            >
              <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.9" />
              <path
                d="M8.5 12.3L10.9 14.7L15.6 9.8"
                stroke="currentColor"
                stroke-width="1.9"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <div>
              <div class="alert-box-title">{{ t('quarantine.states.alreadyUsedTitle') }}</div>
              <div>{{ t('quarantine.states.alreadyUsedBody') }}</div>
            </div>
          </div>

          <div v-if="hasMessageMetadata" class="message-meta-card">
            <dl class="meta-dl">
              <div v-if="tokenInfo.sender" class="meta-row">
                <dt>{{ t('quarantine.meta.sender') }}</dt>
                <dd class="meta-mono">{{ tokenInfo.sender }}</dd>
              </div>
              <div v-if="tokenInfo.subject" class="meta-row">
                <dt>{{ t('quarantine.meta.subject') }}</dt>
                <dd class="meta-subject">{{ tokenInfo.subject }}</dd>
              </div>
            </dl>
          </div>
        </template>

        <!-- Expired Token State -->
        <template v-else-if="tokenInfo?.expired">
          <div class="alert-box alert-box--forbidden" role="alert">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              style="flex-shrink: 0; margin-top: 2px"
            >
              <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.9" />
              <path
                d="M12 7.5V12L15 14"
                stroke="currentColor"
                stroke-width="1.9"
                stroke-linecap="round"
              />
            </svg>
            <div>
              <div class="alert-box-title">{{ t('quarantine.states.expiredTitle') }}</div>
              <div>{{ t('quarantine.states.expiredBody') }}</div>
            </div>
          </div>
        </template>

        <!-- Invalid Token State -->
        <template v-else-if="!tokenInfo?.valid">
          <div class="alert-box alert-box--error" role="alert">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              style="flex-shrink: 0; margin-top: 2px"
            >
              <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.9" />
              <path d="M12 8V12.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" />
              <circle cx="12" cy="16" r="1" fill="currentColor" />
            </svg>
            <div>
              <div class="alert-box-title">{{ t('quarantine.states.invalidTitle') }}</div>
              <div>{{ t('quarantine.states.invalidBody') }}</div>
            </div>
          </div>
        </template>

        <!-- Valid Token: Step 2 User Confirmation Card -->
        <template v-else>
          <div
            v-if="actionError"
            class="alert-box alert-box--error"
            role="alert"
          >
            <div>
              <div class="alert-box-title">{{ t('quarantine.states.actionErrorTitle') }}</div>
              <div>{{ actionError }}</div>
            </div>
          </div>

          <!-- Quarantined Message Metadata -->
          <div class="message-meta-card">
            <dl class="meta-dl">
              <div class="meta-row">
                <dt>{{ t('quarantine.meta.sender') }}</dt>
                <dd class="meta-mono">{{ tokenInfo.sender || '—' }}</dd>
              </div>

              <div class="meta-row">
                <dt>{{ t('quarantine.meta.recipient') }}</dt>
                <dd class="meta-mono">
                  {{ tokenInfo.recipient_email || tokenInfo.recipient_user || '—' }}
                </dd>
              </div>

              <div class="meta-row">
                <dt>{{ t('quarantine.meta.subject') }}</dt>
                <dd class="meta-subject">
                  {{ tokenInfo.subject || t('quarantine.meta.noSubject') }}
                </dd>
              </div>

              <div v-if="tokenInfo.received_at" class="meta-row">
                <dt>{{ t('quarantine.meta.receivedAt') }}</dt>
                <dd class="meta-tabular">{{ formatReceivedAt(tokenInfo.received_at) }}</dd>
              </div>

              <div
                v-if="tokenInfo.spam_score !== null && tokenInfo.spam_score !== undefined"
                class="meta-row"
              >
                <dt>{{ t('quarantine.meta.spamScore') }}</dt>
                <dd>
                  <span class="badge badge--amber">
                    {{ formatSpamScore(tokenInfo.spam_score) }}
                  </span>
                </dd>
              </div>
            </dl>
          </div>

          <!-- Scanner-safe Info Note -->
          <div class="scanner-safe-note">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              style="flex-shrink: 0; margin-top: 2px"
            >
              <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" />
              <path d="M12 11V16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
              <circle cx="12" cy="8" r="1" fill="currentColor" />
            </svg>
            <span>{{ t('quarantine.scannerSafeNote') }}</span>
          </div>

          <!-- Two Action Cards / Buttons -->
          <div class="quarantine-actions-grid">
            <!-- Option 1: Single Release -->
            <div
              class="action-option-box"
              :class="{ 'is-highlighted': preferredOp === 'release' }"
            >
              <div class="action-option-top">
                <span
                  v-if="preferredOp === 'release'"
                  class="badge badge--blue preselected-pill"
                >
                  {{ t('quarantine.preselectedBadge') }}
                </span>
                <p class="action-option-hint">{{ t('quarantine.releaseHint') }}</p>
              </div>

              <button
                type="button"
                class="btn action-submit-btn"
                :class="preferredOp === 'release' ? 'btn-primary' : 'btn-secondary'"
                :disabled="submittingOp !== ''"
                @click="handleAction('release')"
              >
                <span v-if="submittingOp === 'release'">
                  {{ t('quarantine.executing') }}
                </span>
                <span v-else>{{ t('quarantine.releaseBtn') }}</span>
              </button>
            </div>

            <!-- Option 2: Release & Allow Sender -->
            <div
              class="action-option-box"
              :class="{ 'is-highlighted': preferredOp === 'release_and_allow' }"
            >
              <div class="action-option-top">
                <span
                  v-if="preferredOp === 'release_and_allow'"
                  class="badge badge--blue preselected-pill"
                >
                  {{ t('quarantine.preselectedBadge') }}
                </span>
                <p class="action-option-hint">{{ t('quarantine.releaseAndAllowHint') }}</p>
              </div>

              <button
                type="button"
                class="btn action-submit-btn"
                :class="preferredOp === 'release_and_allow' ? 'btn-primary' : 'btn-secondary'"
                :disabled="submittingOp !== ''"
                @click="handleAction('release_and_allow')"
              >
                <span v-if="submittingOp === 'release_and_allow'">
                  {{ t('quarantine.executing') }}
                </span>
                <span v-else>{{ t('quarantine.releaseAndAllowBtn') }}</span>
              </button>
            </div>
          </div>
        </template>
      </div>

      <!-- Optional Footer Link Back to Secretariat Portal -->
      <div class="quarantine-card-footer">
        <button
          type="button"
          class="portal-switch-link"
          @click="emit('exit-quarantine')"
        >
          <span>
            {{
              isAuthenticated
                ? t('quarantine.goToPortalWorkspace')
                : t('quarantine.goToPortalLogin')
            }}
          </span>
          <span aria-hidden="true">→</span>
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api, ApiError } from '../api/client'
import { useI18n } from '../i18n/messages'

const props = defineProps({
  token: {
    type: String,
    required: true,
  },
  initialOp: {
    type: String,
    default: 'release',
  },
  isAuthenticated: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['exit-quarantine'])

const { locale, t } = useI18n()

const loading = ref(true)
const inspectError = ref('')
const tokenInfo = ref(null)

const submittingOp = ref('')
const actionError = ref('')
const actionResult = ref(null)

const preferredOp = computed(() => {
  if (props.initialOp === 'release_and_allow') return 'release_and_allow'
  if (props.initialOp === 'release') return 'release'
  if (tokenInfo.value?.recommended_op === 'release_and_allow') {
    return 'release_and_allow'
  }
  return 'release'
})

const hasMessageMetadata = computed(() =>
  Boolean(
    tokenInfo.value &&
      (tokenInfo.value.sender ||
        tokenInfo.value.subject ||
        tokenInfo.value.recipient_email ||
        tokenInfo.value.recipient_user)
  )
)

async function loadTokenDetails() {
  const cleanToken = String(props.token || '').trim()
  if (!cleanToken) {
    tokenInfo.value = { valid: false, already_used: false, expired: false }
    loading.value = false
    return
  }

  loading.value = true
  inspectError.value = ''
  actionError.value = ''
  actionResult.value = null

  try {
    const res = await api.inspectQuarantineToken(cleanToken)
    tokenInfo.value = res || { valid: false, already_used: false, expired: false }
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.data && typeof err.data === 'object' && 'valid' in err.data) {
        tokenInfo.value = err.data
      } else if (err.status === 404 || err.status === 400 || err.status === 422) {
        tokenInfo.value = { valid: false, already_used: false, expired: false }
      } else if (err.status === 410) {
        tokenInfo.value = { valid: false, already_used: false, expired: true }
      } else if (err.status === 409) {
        tokenInfo.value = { valid: false, already_used: true, expired: false }
      } else {
        inspectError.value = err.detail || `HTTP ${err.status}`
      }
    } else {
      inspectError.value = err?.message || 'Error'
    }
  } finally {
    loading.value = false
  }
}

async function handleAction(op) {
  if (submittingOp.value) return
  submittingOp.value = op
  actionError.value = ''

  try {
    const res = await api.executeQuarantineAction(props.token.trim(), op)
    if (res?.already_used) {
      tokenInfo.value = {
        ...(tokenInfo.value || {}),
        valid: false,
        already_used: true,
      }
      return
    }
    actionResult.value = {
      op: res?.op || op,
      delivered: res?.delivered !== false,
      allowlisted: Boolean(res?.allowlisted || op === 'release_and_allow'),
      message: res?.message || '',
    }
  } catch (err) {
    if (err instanceof ApiError) {
      const detailLower = String(err.detail || '').toLowerCase()
      if (
        err.status === 409 ||
        err.data?.already_used ||
        detailLower.includes('already used') ||
        detailLower.includes('already_used')
      ) {
        tokenInfo.value = {
          ...(tokenInfo.value || {}),
          valid: false,
          already_used: true,
        }
      } else if (err.status === 410 || detailLower.includes('expired')) {
        tokenInfo.value = {
          ...(tokenInfo.value || {}),
          valid: false,
          expired: true,
        }
      } else {
        actionError.value = err.detail || `HTTP ${err.status}`
      }
    } else {
      actionError.value = err?.message || 'Error'
    }
  } finally {
    submittingOp.value = ''
  }
}

function formatReceivedAt(ts) {
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
    }).format(parsed)
  } catch {
    return parsed.toISOString()
  }
}

function formatSpamScore(score) {
  const num = Number(score)
  if (Number.isNaN(num)) return String(score)
  return num.toFixed(1)
}

watch(
  () => props.token,
  () => {
    loadTokenDetails()
  }
)

onMounted(() => {
  loadTokenDetails()
})
</script>

<style scoped>
.quarantine-wrapper {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 2.5rem 1rem;
  min-height: calc(100vh - 64px);
}

.quarantine-card {
  width: 100%;
  max-width: 660px;
}

.quarantine-card-header {
  padding: 1.35rem 1.65rem;
  border-bottom: 1px solid var(--border-subtle);
  background-color: var(--bg-subtle);
}

.quarantine-header-row {
  display: flex;
  align-items: flex-start;
  gap: 0.9rem;
}

.quarantine-shield-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  background-color: #e0f2fe;
  border: 1px solid #bae6fd;
  color: var(--brand-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.quarantine-title {
  margin: 0 0 0.25rem;
  font-size: 1.2rem;
  font-weight: 700;
  color: var(--text-primary);
}

.quarantine-subtitle {
  margin: 0;
  font-size: 0.88rem;
  color: var(--text-secondary);
}

.quarantine-card-body {
  padding: 1.45rem 1.65rem;
  display: flex;
  flex-direction: column;
  gap: 1.15rem;
}

.quarantine-loading-box {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.7rem;
  padding: 1.8rem 1rem;
  color: var(--text-secondary);
  font-weight: 500;
}

.quarantine-spinner {
  width: 18px;
  height: 18px;
  border: 2.5px solid var(--border-default);
  border-top-color: var(--brand-primary);
  border-radius: 50%;
  animation: qspin 0.75s linear infinite;
}

/* Metadata Card */
.message-meta-card {
  background-color: var(--bg-subtle);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 1rem 1.15rem;
}

.meta-dl {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.meta-row {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  gap: 0.75rem;
  align-items: baseline;
}

.meta-row dt {
  font-size: 0.81rem;
  font-weight: 700;
  color: var(--text-muted);
}

.meta-row dd {
  margin: 0;
  font-size: 0.88rem;
  color: var(--text-primary);
  word-break: break-word;
}

.meta-mono {
  font-family: var(--font-mono);
  font-size: 0.83rem;
}

.meta-subject {
  font-weight: 600;
}

.meta-tabular {
  font-variant-numeric: tabular-nums;
}

/* Scanner Safe Note */
.scanner-safe-note {
  display: flex;
  align-items: flex-start;
  gap: 0.55rem;
  font-size: 0.8rem;
  color: var(--text-secondary);
  background-color: #f8fafc;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 0.65rem 0.85rem;
}

/* Action Option Cards */
.quarantine-actions-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.9rem;
}

.action-option-box {
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 0.95rem 1rem;
  background-color: var(--bg-surface);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 0.85rem;
}

.action-option-box.is-highlighted {
  border-color: var(--brand-primary);
  background-color: #f8fbff;
  box-shadow: 0 0 0 1px var(--brand-primary);
}

.action-option-top {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  align-items: flex-start;
}

.preselected-pill {
  font-size: 0.72rem;
  padding: 0.1rem 0.48rem;
}

.action-option-hint {
  margin: 0;
  font-size: 0.8rem;
  color: var(--text-secondary);
  line-height: 1.45;
}

.action-submit-btn {
  width: 100%;
  white-space: normal;
  text-align: center;
  line-height: 1.3;
  padding: 0.6rem 0.85rem;
}

/* Success Banner */
.success-banner {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 1rem 1.1rem;
  border-radius: var(--radius-md);
  background-color: var(--status-green-bg);
  border: 1px solid var(--status-green-border);
  color: var(--status-green-text);
  font-size: 0.9rem;
}

.success-banner-content {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.allowlist-confirm-box {
  margin-top: 0.5rem;
  padding-top: 0.55rem;
  border-top: 1px solid var(--status-green-border);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.3rem;
}

.allowlist-confirm-text {
  margin: 0;
  font-size: 0.83rem;
}

/* Footer Link */
.quarantine-card-footer {
  padding: 0.85rem 1.65rem;
  background-color: var(--bg-subtle);
  border-top: 1px solid var(--border-subtle);
  display: flex;
  justify-content: flex-end;
}

.portal-switch-link {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--brand-primary);
  font-size: 0.83rem;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.25rem 0.4rem;
  border-radius: var(--radius-sm);
}

.portal-switch-link:hover {
  background-color: var(--bg-muted);
  color: var(--brand-primary-hover);
}

@keyframes qspin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .meta-row {
    grid-template-columns: 1fr;
    gap: 0.15rem;
  }
  .quarantine-actions-grid {
    grid-template-columns: 1fr;
  }
}
</style>
