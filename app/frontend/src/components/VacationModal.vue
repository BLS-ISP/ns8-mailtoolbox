<template>
  <div
    class="modal-backdrop"
    @mousedown.self="handleBackdropClick"
  >
    <div
      ref="dialogRef"
      class="modal-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="vacation-modal-title"
      @keydown="handleDialogKeydown"
    >
      <!-- Modal Header -->
      <div class="modal-header">
        <div>
          <h2 id="vacation-modal-title" class="modal-title">
            {{ t('modal.title') }}
          </h2>
          <div class="employee-context">
            <span class="employee-context-name">
              {{ mailbox.display_name || mailbox.user }}
            </span>
            <span v-if="mailbox.mail" class="employee-context-mail">
              &lt;{{ mailbox.mail }}&gt;
            </span>
            <span
              v-if="mailbox.department || mailbox.title"
              class="employee-context-dept"
            >
              · {{ [mailbox.department, mailbox.title].filter(Boolean).join(' – ') }}
            </span>
          </div>
        </div>

        <button
          type="button"
          class="modal-close-btn"
          :aria-label="t('modal.closeAria')"
          :disabled="saving"
          @click="emit('close')"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M6 6L18 18M18 6L6 18"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            />
          </svg>
        </button>
      </div>

      <!-- Modal Scrollable Body -->
      <form class="modal-body" @submit.prevent="handleSave" novalidate>
        <!-- SOGo Coexistence Banner -->
        <div
          v-if="mailbox.sogo_status?.vacation_enabled"
          class="alert-box alert-box--info"
          role="status"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
            style="flex-shrink: 0; margin-top: 2px"
          >
            <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.9" />
            <path d="M12 11V16" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" />
            <circle cx="12" cy="8" r="1" fill="currentColor" />
          </svg>
          <div>
            <div class="alert-box-title">{{ t('modal.sogoCoexistenceTitle') }}</div>
            <div>{{ t('modal.sogoCoexistenceBody') }}</div>
          </div>
        </div>
        <div
          v-else-if="mailbox.sogo_status?.has_sogo_script"
          class="sogo-filters-note"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M5 13L9 17L19 7"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <span>{{ t('modal.sogoFiltersKeptNote') }}</span>
        </div>

        <!-- Server / Validation Error Alert -->
        <div
          v-if="errorMessage"
          class="alert-box alert-box--error"
          role="alert"
        >
          <svg
            width="19"
            height="19"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
            style="flex-shrink: 0; margin-top: 2px"
          >
            <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.9" />
            <path d="M12 8V12.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" />
            <circle cx="12" cy="16" r="1" fill="currentColor" />
          </svg>
          <div>{{ errorMessage }}</div>
        </div>

        <!-- 1. Master Switch -->
        <div
          class="master-switch-box"
          :class="{ 'is-enabled': form.enabled }"
        >
          <div class="master-switch-text">
            <label
              id="master-switch-label"
              class="master-switch-title"
              @click="toggleMasterEnabled"
            >
              {{ t('modal.masterToggleLabel') }}
            </label>
            <div class="master-switch-desc">
              {{
                form.enabled
                  ? t('modal.masterToggleDescEnabled')
                  : t('modal.masterToggleDescDisabled')
              }}
            </div>
          </div>
          <button
            ref="masterSwitchRef"
            type="button"
            role="switch"
            class="toggle-switch"
            :class="{ 'is-on': form.enabled }"
            :aria-checked="form.enabled"
            aria-labelledby="master-switch-label"
            @click="toggleMasterEnabled"
          >
            <span class="toggle-switch-thumb" />
          </button>
        </div>

        <!-- 2. Optional Date Range -->
        <section class="form-section">
          <div class="section-header-row">
            <div>
              <h3 class="section-title">{{ t('modal.dateSectionTitle') }}</h3>
              <p class="section-hint">{{ t('modal.dateSectionHint') }}</p>
            </div>
            <button
              v-if="form.start_date || form.end_date"
              type="button"
              class="btn btn-secondary btn-sm"
              @click="clearDates"
            >
              {{ t('modal.clearDates') }}
            </button>
          </div>

          <div class="date-presets-row">
            <span class="presets-label">{{ t('modal.presetsLabel') }}</span>
            <button
              type="button"
              class="preset-chip"
              @click="applyDatePreset('openFromToday')"
            >
              {{ t('modal.presetOpenFromToday') }}
            </button>
            <button
              type="button"
              class="preset-chip"
              @click="applyDatePreset('todayTomorrow')"
            >
              {{ t('modal.presetTodayTomorrow') }}
            </button>
            <button
              type="button"
              class="preset-chip"
              @click="applyDatePreset('endOfWeek')"
            >
              {{ t('modal.presetEndOfWeek') }}
            </button>
            <button
              type="button"
              class="preset-chip"
              @click="applyDatePreset('next7Days')"
            >
              {{ t('modal.presetNext7Days') }}
            </button>
          </div>

          <div class="date-grid">
            <div>
              <label class="field-label" for="vac-start-date">
                {{ t('modal.startDateLabel') }}
              </label>
              <div class="date-input-wrap">
                <input
                  id="vac-start-date"
                  v-model="form.start_date"
                  type="date"
                  class="input-control"
                />
                <button
                  v-if="form.start_date"
                  type="button"
                  class="inline-clear-btn"
                  :aria-label="t('modal.clearDates')"
                  @click="form.start_date = ''"
                >
                  ×
                </button>
              </div>
            </div>

            <div>
              <label class="field-label" for="vac-end-date">
                {{ t('modal.endDateLabel') }}
              </label>
              <div class="date-input-wrap">
                <input
                  id="vac-end-date"
                  v-model="form.end_date"
                  type="date"
                  class="input-control"
                  :min="form.start_date || undefined"
                />
                <button
                  v-if="form.end_date"
                  type="button"
                  class="inline-clear-btn"
                  :aria-label="t('modal.clearDates')"
                  @click="form.end_date = ''"
                >
                  ×
                </button>
              </div>
            </div>
          </div>

          <p v-if="dateOrderInvalid" class="validation-inline">
            {{ t('modal.validation.dateOrder') }}
          </p>
        </section>

        <!-- 3. Deputy Forwarding -->
        <section class="form-section">
          <h3 class="section-title">{{ t('modal.forwardSectionTitle') }}</h3>

          <label class="checkbox-row">
            <input
              v-model="form.forward_enabled"
              type="checkbox"
              class="checkbox-input"
            />
            <span class="checkbox-label-text">
              {{ t('modal.forwardToggleLabel') }}
            </span>
          </label>

          <div v-if="form.forward_enabled" class="forward-details-box">
            <div class="combobox-container">
              <label class="field-label" for="vac-deputy-email">
                {{ t('modal.deputyInputLabel') }}
              </label>
              <div class="deputy-input-wrap">
                <input
                  id="vac-deputy-email"
                  v-model="form.forward_address"
                  type="email"
                  class="input-control deputy-input"
                  autocomplete="off"
                  spellcheck="false"
                  role="combobox"
                  aria-autocomplete="list"
                  :aria-expanded="colleagueMenuOpen"
                  aria-controls="colleague-listbox"
                  :placeholder="t('modal.deputyInputPlaceholder')"
                  @focus="colleagueMenuOpen = true"
                  @input="handleDeputyInput"
                  @keydown="handleDeputyKeydown"
                  @blur="handleDeputyBlur"
                />
                <button
                  v-if="form.forward_address"
                  type="button"
                  class="inline-clear-btn"
                  :aria-label="t('modal.clearDeputy')"
                  @mousedown.prevent="clearDeputyAddress"
                >
                  ×
                </button>
              </div>

              <div
                v-if="colleagueMenuOpen && colleagueCandidates.length > 0"
                id="colleague-listbox"
                role="listbox"
                class="colleague-dropdown"
              >
                <div class="colleague-dropdown-header">
                  {{ t('modal.colleagueDropdownTitle') }}
                </div>
                <button
                  v-for="(col, idx) in filteredColleagues"
                  :key="col.user"
                  type="button"
                  role="option"
                  class="colleague-option"
                  :class="{ 'is-highlighted': idx === highlightedColleagueIndex }"
                  :aria-selected="form.forward_address === col.mail"
                  @mousedown.prevent="selectColleague(col)"
                  @mouseenter="highlightedColleagueIndex = idx"
                >
                  <div class="colleague-option-main">
                    <span class="colleague-option-name">
                      {{ col.display_name || col.user }}
                    </span>
                    <span class="colleague-option-mail">{{ col.mail }}</span>
                  </div>
                  <div class="colleague-option-meta">
                    <span v-if="col.department" class="colleague-option-dept">
                      {{ col.department }}
                    </span>
                    <span
                      v-if="col.vacation?.enabled || col.sogo_status?.vacation_enabled"
                      class="badge badge--warn colleague-absent-pill"
                    >
                      {{ t('modal.colleagueAbsentBadge') }}
                    </span>
                  </div>
                </button>
                <div
                  v-if="filteredColleagues.length === 0"
                  class="colleague-empty-hint"
                >
                  {{ t('modal.colleagueNoMatch') }}
                </div>
              </div>
            </div>

            <fieldset class="delivery-mode-fieldset">
              <legend class="field-label">{{ t('modal.deliveryModeLabel') }}</legend>
              <label
                class="radio-card"
                :class="{ 'is-selected': form.keep_copy === true }"
              >
                <input
                  v-model="form.keep_copy"
                  type="radio"
                  name="keep_copy"
                  :value="true"
                />
                <div>
                  <div class="radio-card-title">{{ t('modal.keepCopyTrueTitle') }}</div>
                  <div class="radio-card-hint">{{ t('modal.keepCopyTrueHint') }}</div>
                </div>
              </label>

              <label
                class="radio-card"
                :class="{ 'is-selected': form.keep_copy === false }"
              >
                <input
                  v-model="form.keep_copy"
                  type="radio"
                  name="keep_copy"
                  :value="false"
                />
                <div>
                  <div class="radio-card-title">{{ t('modal.keepCopyFalseTitle') }}</div>
                  <div class="radio-card-hint">{{ t('modal.keepCopyFalseHint') }}</div>
                </div>
              </label>
            </fieldset>
          </div>
        </section>

        <!-- 4. Template Quick-Select & Auto-Reply Message -->
        <section class="form-section">
          <div class="section-header-row">
            <h3 class="section-title">{{ t('modal.templateSectionTitle') }}</h3>
          </div>

          <div class="template-bar">
            <div class="template-select-group">
              <label class="sr-only" for="vac-template-select">
                {{ t('modal.templateSelectLabel') }}
              </label>
              <select
                id="vac-template-select"
                v-model="selectedTemplateId"
                class="select-control"
                @change="applySelectedTemplate"
              >
                <option
                  v-for="tpl in availableTemplates"
                  :key="tpl.id"
                  :value="tpl.id"
                >
                  {{ tpl.name }} ({{ (tpl.lang || 'en').toUpperCase() }}){{ tpl.is_default ? ' ★' : '' }}
                </option>
              </select>
            </div>

            <div class="template-actions">
              <button
                v-if="selectedTemplate && selectedTemplate.id !== defaultTemplateForLocale?.id"
                type="button"
                class="btn btn-secondary btn-sm"
                @click="applySelectedTemplate"
              >
                {{ t('modal.insertSelectedBtn') }}
              </button>
              <button
                type="button"
                class="btn btn-secondary btn-sm btn-template-default"
                @click="insertCompanyDefaultTemplate"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M14 3H6C4.89543 3 4 3.89543 4 5V19C4 20.1046 4.89543 21 6 21H18C19.1046 21 20 20.1046 20 19V9L14 3Z"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linejoin="round"
                  />
                  <path d="M14 3V9H20" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
                </svg>
                <span>{{ t('modal.insertDefaultBtn') }}</span>
              </button>
            </div>
          </div>

          <div v-if="templateAppliedFeedback" class="template-applied-feedback" role="status">
            ✓ {{ t('modal.templateAppliedHint') }}
          </div>

          <div class="message-fields">
            <div>
              <label class="field-label" for="vac-subject">
                {{ t('modal.subjectLabel') }}
              </label>
              <input
                id="vac-subject"
                v-model="form.subject"
                type="text"
                class="input-control"
                :placeholder="t('modal.subjectPlaceholder')"
              />
            </div>

            <div>
              <label class="field-label" for="vac-body">
                {{ t('modal.bodyLabel') }}
              </label>
              <textarea
                id="vac-body"
                v-model="form.body"
                rows="7"
                class="textarea-control"
                :placeholder="t('modal.bodyPlaceholder')"
              />
            </div>
          </div>

          <!-- Collapsible Live Preview -->
          <details class="live-preview-details" :open="previewOpen" @toggle="onPreviewToggle">
            <summary class="live-preview-summary">
              <span>{{ t('modal.previewSummary') }}</span>
              <span class="preview-period-pill">{{ formattedActivePeriod }}</span>
            </summary>
            <div class="live-preview-card">
              <div class="preview-meta-grid">
                <div class="preview-meta-label">{{ t('modal.previewFrom') }}:</div>
                <div class="preview-meta-val">
                  {{ mailbox.display_name || mailbox.user }}
                  <span v-if="mailbox.mail">&lt;{{ mailbox.mail }}&gt;</span>
                </div>

                <div class="preview-meta-label">{{ t('modal.previewSubject') }}:</div>
                <div class="preview-meta-val preview-subject-val">
                  {{ livePreviewSubject || '—' }}
                </div>

                <div class="preview-meta-label">{{ t('modal.previewPeriod') }}:</div>
                <div class="preview-meta-val">{{ formattedActivePeriod }}</div>

                <template v-if="form.forward_enabled && form.forward_address.trim()">
                  <div class="preview-meta-label">{{ t('modal.previewForward') }}:</div>
                  <div class="preview-meta-val">
                    {{ form.forward_address.trim() }}
                    ({{
                      form.keep_copy
                        ? t('dashboard.badges.forwardCopy')
                        : t('dashboard.badges.forwardDirect')
                    }})
                  </div>
                </template>
              </div>
              <div class="preview-body-content">{{ livePreviewBody }}</div>
            </div>
          </details>
        </section>
      </form>

      <!-- Modal Footer -->
      <div class="modal-footer">
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="saving"
          @click="emit('close')"
        >
          {{ t('modal.cancel') }}
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="saving || dateOrderInvalid"
          @click="handleSave"
        >
          <span v-if="saving">{{ t('modal.saving') }}</span>
          <span v-else>
            {{ form.enabled ? t('modal.saveEnable') : t('modal.saveDisable') }}
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { api, ApiError } from '../api/client'
import { useI18n } from '../i18n/messages'
import {
  FALLBACK_TEMPLATES,
  buildTemplateContext,
  renderTemplate,
  renderTemplateString,
  formatDateRange,
} from '../utils/templateEngine'

const props = defineProps({
  mailbox: {
    type: Object,
    required: true,
  },
  allMailboxes: {
    type: Array,
    default: () => [],
  },
  templates: {
    type: Array,
    default: () => [],
  },
  currentUser: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['close', 'saved'])

const { locale, t } = useI18n()

const dialogRef = ref(null)
const masterSwitchRef = ref(null)
const saving = ref(false)
const errorMessage = ref('')
const previewOpen = ref(true)
const templateAppliedFeedback = ref(false)

const colleagueMenuOpen = ref(false)
const highlightedColleagueIndex = ref(0)

let previousActiveElement = null
let feedbackTimer = null

const availableTemplates = computed(() =>
  Array.isArray(props.templates) && props.templates.length > 0
    ? props.templates
    : FALLBACK_TEMPLATES
)

const defaultTemplateForLocale = computed(() => {
  const list = availableTemplates.value
  return (
    list.find((tpl) => tpl.is_default && tpl.lang === locale.value) ||
    list.find((tpl) => tpl.lang === locale.value) ||
    list.find((tpl) => tpl.is_default) ||
    list[0] ||
    FALLBACK_TEMPLATES[0]
  )
})

const selectedTemplateId = ref(defaultTemplateForLocale.value?.id || 'default_en')

watch(defaultTemplateForLocale, (nextDef) => {
  if (nextDef?.id) {
    selectedTemplateId.value = nextDef.id
  }
})

const selectedTemplate = computed(
  () =>
    availableTemplates.value.find((tpl) => tpl.id === selectedTemplateId.value) ||
    defaultTemplateForLocale.value
)

const existingVac = props.mailbox?.vacation || {}

const form = reactive({
  enabled: Boolean(existingVac.enabled),
  start_date: existingVac.start_date || '',
  end_date: existingVac.end_date || '',
  forward_enabled: Boolean(existingVac.forward_enabled),
  forward_address: existingVac.forward_address || '',
  keep_copy: existingVac.keep_copy !== undefined ? Boolean(existingVac.keep_copy) : true,
  subject: existingVac.subject || '',
  body: existingVac.body || '',
})

const dateOrderInvalid = computed(
  () => Boolean(form.start_date && form.end_date && form.end_date < form.start_date)
)

const colleagueCandidates = computed(() =>
  (props.allMailboxes || []).filter(
    (m) => m.user !== props.mailbox.user && Boolean(m.mail)
  )
)

const filteredColleagues = computed(() => {
  const q = form.forward_address.trim().toLowerCase()
  if (!q) return colleagueCandidates.value.slice(0, 12)
  return colleagueCandidates.value
    .filter((m) => {
      const name = String(m.display_name || '').toLowerCase()
      const mail = String(m.mail || '').toLowerCase()
      const dept = String(m.department || '').toLowerCase()
      const usr = String(m.user || '').toLowerCase()
      return (
        name.includes(q) ||
        mail.includes(q) ||
        dept.includes(q) ||
        usr.includes(q)
      )
    })
    .slice(0, 10)
})

const liveContext = computed(() =>
  buildTemplateContext({
    mailbox: props.mailbox,
    startDate: form.start_date,
    endDate: form.end_date,
    forwardEnabled: form.forward_enabled,
    forwardAddress: form.forward_address,
    lang: locale.value,
  })
)

const livePreviewSubject = computed(() =>
  renderTemplateString(form.subject, liveContext.value, { isSubject: true })
)

const livePreviewBody = computed(() => {
  const rendered = renderTemplateString(form.body, liveContext.value, {
    isSubject: false,
  })
  return rendered || t('modal.previewEmptyBody')
})

const formattedActivePeriod = computed(() =>
  formatDateRange(form.start_date, form.end_date, locale.value, {
    openEnded: t('dashboard.badges.openEnded'),
    fromPrefix: t('dashboard.badges.fromPrefix'),
    untilPrefix: t('dashboard.badges.untilPrefix'),
  })
)

function showTemplateFeedback() {
  templateAppliedFeedback.value = true
  if (feedbackTimer) clearTimeout(feedbackTimer)
  feedbackTimer = setTimeout(() => {
    templateAppliedFeedback.value = false
  }, 3000)
}

function applyTemplateObject(tpl) {
  if (!tpl) return
  const rendered = renderTemplate(tpl, {
    mailbox: props.mailbox,
    startDate: form.start_date,
    endDate: form.end_date,
    forwardEnabled: form.forward_enabled,
    forwardAddress: form.forward_address,
    lang: tpl.lang || locale.value,
  })
  form.subject = rendered.subject
  form.body = rendered.body
  showTemplateFeedback()
}

function applySelectedTemplate() {
  applyTemplateObject(selectedTemplate.value)
}

function insertCompanyDefaultTemplate() {
  const def = defaultTemplateForLocale.value
  if (def) {
    selectedTemplateId.value = def.id
    applyTemplateObject(def)
  }
}

function toggleMasterEnabled() {
  form.enabled = !form.enabled
  if (form.enabled && !form.subject.trim() && !form.body.trim()) {
    insertCompanyDefaultTemplate()
  }
}

function clearDates() {
  form.start_date = ''
  form.end_date = ''
}

function toLocalIsoDate(dateObj) {
  const y = dateObj.getFullYear()
  const m = String(dateObj.getMonth() + 1).padStart(2, '0')
  const d = String(dateObj.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function applyDatePreset(preset) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())

  if (preset === 'openFromToday') {
    form.start_date = toLocalIsoDate(today)
    form.end_date = ''
    return
  }
  if (preset === 'todayTomorrow') {
    const tomorrow = new Date(today)
    tomorrow.setDate(today.getDate() + 1)
    form.start_date = toLocalIsoDate(today)
    form.end_date = toLocalIsoDate(tomorrow)
    return
  }
  if (preset === 'endOfWeek') {
    const dayOfWeek = today.getDay() // 0=Sun, 5=Fri
    const diffToFriday = dayOfWeek <= 5 ? 5 - dayOfWeek : 6
    const friday = new Date(today)
    friday.setDate(today.getDate() + diffToFriday)
    form.start_date = toLocalIsoDate(today)
    form.end_date = toLocalIsoDate(friday)
    return
  }
  if (preset === 'next7Days') {
    const plus6 = new Date(today)
    plus6.setDate(today.getDate() + 6)
    form.start_date = toLocalIsoDate(today)
    form.end_date = toLocalIsoDate(plus6)
  }
}

function handleDeputyInput() {
  colleagueMenuOpen.value = true
  highlightedColleagueIndex.value = 0
}

function handleDeputyBlur() {
  setTimeout(() => {
    colleagueMenuOpen.value = false
  }, 150)
}

function clearDeputyAddress() {
  form.forward_address = ''
  colleagueMenuOpen.value = true
}

function selectColleague(col) {
  form.forward_address = col.mail
  colleagueMenuOpen.value = false
}

function handleDeputyKeydown(e) {
  if (!colleagueMenuOpen.value && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    colleagueMenuOpen.value = true
    return
  }
  if (!colleagueMenuOpen.value) return

  const list = filteredColleagues.value
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    if (list.length > 0) {
      highlightedColleagueIndex.value =
        (highlightedColleagueIndex.value + 1) % list.length
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    if (list.length > 0) {
      highlightedColleagueIndex.value =
        (highlightedColleagueIndex.value - 1 + list.length) % list.length
    }
  } else if (e.key === 'Enter' && list.length > 0) {
    const picked = list[highlightedColleagueIndex.value]
    if (picked && form.forward_address !== picked.mail) {
      e.preventDefault()
      selectColleague(picked)
    }
  } else if (e.key === 'Escape') {
    e.stopPropagation()
    colleagueMenuOpen.value = false
  }
}

function onPreviewToggle(e) {
  previewOpen.value = e.target.open
}

function validateForm() {
  if (dateOrderInvalid.value) {
    return t('modal.validation.dateOrder')
  }

  const cleanDeputy = form.forward_address.trim()
  if (form.forward_enabled) {
    const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (!cleanDeputy || !emailRe.test(cleanDeputy)) {
      return t('modal.validation.forwardInvalid')
    }
    const ownAddresses = [
      props.mailbox.mail,
      ...(Array.isArray(props.mailbox.aliases) ? props.mailbox.aliases : []),
    ]
      .filter(Boolean)
      .map((a) => String(a).toLowerCase())

    if (ownAddresses.includes(cleanDeputy.toLowerCase())) {
      return t('modal.validation.forwardSelfLoop')
    }
  }

  if (form.enabled) {
    if (!form.subject.trim()) {
      return t('modal.validation.subjectRequired')
    }
    if (!form.body.trim()) {
      return t('modal.validation.bodyRequired')
    }
  }

  return ''
}

async function handleSave() {
  errorMessage.value = ''
  const validationErr = validateForm()
  if (validationErr) {
    errorMessage.value = validationErr
    return
  }

  saving.value = true
  try {
    // Resolve any remaining placeholders in subject/body if the user left {{...}} tags
    const finalSubject = renderTemplateString(form.subject, liveContext.value, {
      isSubject: true,
    })
    const finalBody = renderTemplateString(form.body, liveContext.value, {
      isSubject: false,
    })

    const payload = {
      user: props.mailbox.user,
      enabled: Boolean(form.enabled),
      start_date: form.start_date || '',
      end_date: form.end_date || '',
      subject: finalSubject,
      body: finalBody,
      forward_enabled: Boolean(form.forward_enabled && form.forward_address.trim()),
      forward_address: form.forward_enabled ? form.forward_address.trim() : '',
      keep_copy: Boolean(form.keep_copy),
      updated_by: props.currentUser?.username || '',
    }

    const updated = await api.updateVacation(props.mailbox.user, payload)
    emit('saved', {
      mailbox: updated?.mailbox || updated,
      enabled: payload.enabled,
    })
  } catch (err) {
    if (err instanceof ApiError) {
      errorMessage.value = err.detail || `HTTP ${err.status}`
    } else {
      errorMessage.value = err?.message || 'Error'
    }
  } finally {
    saving.value = false
  }
}

function handleBackdropClick() {
  if (!saving.value) {
    emit('close')
  }
}

function handleDialogKeydown(e) {
  if (e.key === 'Escape' && !saving.value) {
    if (colleagueMenuOpen.value) {
      colleagueMenuOpen.value = false
      return
    }
    e.preventDefault()
    emit('close')
    return
  }

  if (e.key === 'Tab' && dialogRef.value) {
    const focusables = dialogRef.value.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    )
    if (focusables.length === 0) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }
}

onMounted(async () => {
  previousActiveElement = document.activeElement
  document.body.style.overflow = 'hidden'
  await nextTick()
  masterSwitchRef.value?.focus()
})

onBeforeUnmount(() => {
  if (feedbackTimer) clearTimeout(feedbackTimer)
  document.body.style.overflow = ''
  if (previousActiveElement && typeof previousActiveElement.focus === 'function') {
    previousActiveElement.focus()
  }
})
</script>

<style scoped>
.modal-title {
  margin: 0;
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text-primary);
}

.employee-context {
  margin-top: 0.25rem;
  font-size: 0.86rem;
  color: var(--text-secondary);
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  align-items: center;
}

.employee-context-name {
  font-weight: 600;
  color: var(--text-primary);
}

.employee-context-mail {
  font-family: var(--font-mono);
  font-size: 0.81rem;
  color: var(--text-muted);
}

.modal-close-btn {
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  padding: 0.3rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.modal-close-btn:hover:not(:disabled) {
  background-color: var(--bg-muted);
  color: var(--text-primary);
}

.sogo-filters-note {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.81rem;
  color: var(--status-green-text);
  background-color: var(--status-green-bg);
  border: 1px solid var(--status-green-border);
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-md);
}

/* Master Switch Box */
.master-switch-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.95rem 1.1rem;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background-color: var(--bg-subtle);
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.master-switch-box.is-enabled {
  background-color: #fffaf2;
  border-color: var(--status-amber-border);
}

.master-switch-title {
  font-size: 0.96rem;
  font-weight: 700;
  color: var(--text-primary);
  cursor: pointer;
}

.master-switch-desc {
  font-size: 0.81rem;
  color: var(--text-secondary);
  margin-top: 0.15rem;
}

.toggle-switch {
  position: relative;
  width: 48px;
  height: 26px;
  border-radius: 999px;
  border: 1px solid var(--border-strong);
  background-color: #cbd5e1;
  padding: 2px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.toggle-switch.is-on {
  background-color: var(--brand-primary);
  border-color: var(--brand-primary);
}

.toggle-switch-thumb {
  display: block;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background-color: #ffffff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  transition: transform 0.15s ease;
}

.toggle-switch.is-on .toggle-switch-thumb {
  transform: translateX(22px);
}

/* Sections */
.form-section {
  border-top: 1px solid var(--border-subtle);
  padding-top: 1.05rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.section-header-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.section-title {
  margin: 0;
  font-size: 0.92rem;
  font-weight: 700;
  color: var(--text-primary);
}

.section-hint {
  margin: 0.15rem 0 0;
  font-size: 0.8rem;
  color: var(--text-muted);
}

.date-presets-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.presets-label {
  font-size: 0.78rem;
  color: var(--text-muted);
  font-weight: 600;
}

.preset-chip {
  appearance: none;
  border: 1px solid var(--border-default);
  background-color: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 0.76rem;
  font-weight: 600;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  cursor: pointer;
}

.preset-chip:hover {
  background-color: var(--bg-muted);
  color: var(--text-primary);
}

.date-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.85rem;
}

.date-input-wrap,
.deputy-input-wrap {
  position: relative;
}

.inline-clear-btn {
  position: absolute;
  right: 2.15rem;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: var(--bg-muted);
  color: var(--text-secondary);
  width: 20px;
  height: 20px;
  border-radius: 50%;
  font-size: 0.9rem;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.deputy-input-wrap .inline-clear-btn {
  right: 0.55rem;
}

.inline-clear-btn:hover {
  background: var(--border-default);
  color: var(--text-primary);
}

.validation-inline {
  margin: 0;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--status-red-text);
}

/* Forwarding Controls */
.checkbox-row {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  cursor: pointer;
}

.checkbox-input {
  width: 17px;
  height: 17px;
  accent-color: var(--brand-primary);
  cursor: pointer;
}

.checkbox-label-text {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-primary);
}

.forward-details-box {
  background-color: var(--bg-subtle);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 0.95rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.combobox-container {
  position: relative;
}

.deputy-input {
  padding-right: 2.1rem;
}

.colleague-dropdown {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  z-index: 30;
  background-color: var(--bg-surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  max-height: 230px;
  overflow-y: auto;
}

.colleague-dropdown-header {
  padding: 0.4rem 0.75rem;
  font-size: 0.73rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
  background-color: var(--bg-subtle);
  border-bottom: 1px solid var(--border-subtle);
}

.colleague-option {
  appearance: none;
  width: 100%;
  border: none;
  border-bottom: 1px solid var(--border-subtle);
  background: transparent;
  padding: 0.55rem 0.75rem;
  text-align: left;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.colleague-option:last-child {
  border-bottom: none;
}

.colleague-option.is-highlighted,
.colleague-option:hover {
  background-color: #eff6ff;
}

.colleague-option-main {
  display: flex;
  flex-direction: column;
}

.colleague-option-name {
  font-size: 0.86rem;
  font-weight: 600;
  color: var(--text-primary);
}

.colleague-option-mail {
  font-size: 0.78rem;
  font-family: var(--font-mono);
  color: var(--text-muted);
}

.colleague-option-meta {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-shrink: 0;
}

.colleague-option-dept {
  font-size: 0.76rem;
  color: var(--text-muted);
}

.colleague-absent-pill {
  font-size: 0.7rem;
  padding: 0.08rem 0.42rem;
}

.colleague-empty-hint {
  padding: 0.6rem 0.75rem;
  font-size: 0.81rem;
  color: var(--text-muted);
}

.delivery-mode-fieldset {
  border: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.radio-card {
  display: flex;
  align-items: flex-start;
  gap: 0.65rem;
  padding: 0.65rem 0.8rem;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background-color: var(--bg-surface);
  cursor: pointer;
}

.radio-card.is-selected {
  border-color: var(--brand-primary);
  background-color: #f8fbff;
}

.radio-card input[type='radio'] {
  margin-top: 0.22rem;
  accent-color: var(--brand-primary);
}

.radio-card-title {
  font-size: 0.86rem;
  font-weight: 600;
  color: var(--text-primary);
}

.radio-card-hint {
  font-size: 0.78rem;
  color: var(--text-muted);
  margin-top: 0.1rem;
}

/* Templates & Message */
.template-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
  flex-wrap: wrap;
  background-color: var(--bg-subtle);
  padding: 0.65rem 0.75rem;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

.template-select-group {
  flex: 1 1 240px;
}

.template-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.btn-template-default {
  border-color: var(--brand-primary);
  color: var(--brand-primary);
}

.btn-template-default:hover {
  background-color: #eff6ff;
}

.template-applied-feedback {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--status-green-text);
}

.message-fields {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

/* Live Preview */
.live-preview-details {
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background-color: var(--bg-subtle);
  overflow: hidden;
}

.live-preview-summary {
  padding: 0.65rem 0.9rem;
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  user-select: none;
}

.preview-period-pill {
  font-size: 0.76rem;
  font-weight: 600;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  background-color: var(--bg-surface);
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
}

.live-preview-card {
  border-top: 1px solid var(--border-subtle);
  background-color: var(--bg-surface);
  padding: 0.9rem 1rem;
}

.preview-meta-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 0.75rem;
  font-size: 0.81rem;
  padding-bottom: 0.75rem;
  margin-bottom: 0.75rem;
  border-bottom: 1px dashed var(--border-default);
}

.preview-meta-label {
  font-weight: 600;
  color: var(--text-muted);
}

.preview-meta-val {
  color: var(--text-primary);
}

.preview-subject-val {
  font-weight: 600;
}

.preview-body-content {
  white-space: pre-wrap;
  font-size: 0.86rem;
  line-height: 1.55;
  color: var(--text-primary);
}

@media (max-width: 640px) {
  .date-grid {
    grid-template-columns: 1fr;
  }
}
</style>
