<template>
  <section class="login-wrapper">
    <div class="login-card panel-card">
      <div class="login-card-header">
        <h1 class="login-title">{{ t('login.heading') }}</h1>
        <p class="login-subtitle">{{ t('login.subtitle') }}</p>
      </div>

      <div class="login-card-body">
        <div
          v-if="sessionExpiredNotice && !errorState"
          class="alert-box alert-box--info"
          role="status"
        >
          <div>{{ t('app.sessionExpired') }}</div>
        </div>

        <div
          v-if="errorState"
          class="alert-box"
          :class="errorState.kind === 'forbidden' ? 'alert-box--forbidden' : 'alert-box--error'"
          role="alert"
        >
          <svg
            v-if="errorState.kind === 'forbidden'"
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
            style="flex-shrink: 0; margin-top: 2px"
          >
            <path
              d="M12 3L4 7V12C4 16.97 7.41 21.6 12 22.8C16.59 21.6 20 16.97 20 12V7L12 3Z"
              stroke="currentColor"
              stroke-width="1.9"
              stroke-linejoin="round"
            />
            <path
              d="M9.5 9.5L14.5 14.5M14.5 9.5L9.5 14.5"
              stroke="currentColor"
              stroke-width="1.9"
              stroke-linecap="round"
            />
          </svg>
          <svg
            v-else
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
            <div class="alert-box-title">{{ errorState.title }}</div>
            <div>{{ errorState.body }}</div>
          </div>
        </div>

        <form class="login-form" @submit.prevent="handleSubmit" novalidate>
          <div class="form-group">
            <label class="field-label" for="login-username">
              {{ t('login.usernameLabel') }}
            </label>
            <input
              id="login-username"
              ref="usernameInputRef"
              v-model="username"
              type="text"
              class="input-control"
              autocomplete="username"
              autocapitalize="none"
              spellcheck="false"
              :placeholder="t('login.usernamePlaceholder')"
              :disabled="submitting"
              required
            />
          </div>

          <div class="form-group">
            <label class="field-label" for="login-password">
              {{ t('login.passwordLabel') }}
            </label>
            <div class="password-field-wrap">
              <input
                id="login-password"
                v-model="password"
                :type="showPassword ? 'text' : 'password'"
                class="input-control password-input"
                autocomplete="current-password"
                :placeholder="t('login.passwordPlaceholder')"
                :disabled="submitting"
                required
              />
              <button
                type="button"
                class="password-toggle-btn"
                :aria-label="showPassword ? t('login.hidePassword') : t('login.showPassword')"
                :title="showPassword ? t('login.hidePassword') : t('login.showPassword')"
                @click="showPassword = !showPassword"
              >
                <svg
                  v-if="!showPassword"
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  aria-hidden="true"
                >
                  <path
                    d="M2.5 12C4.4 7.9 8 5.5 12 5.5C16 5.5 19.6 7.9 21.5 12C19.6 16.1 16 18.5 12 18.5C8 18.5 4.4 16.1 2.5 12Z"
                    stroke="currentColor"
                    stroke-width="1.8"
                  />
                  <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8" />
                </svg>
                <svg
                  v-else
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  aria-hidden="true"
                >
                  <path
                    d="M3 3L21 21M10.6 10.6C10.2 11 10 11.5 10 12C10 13.1 10.9 14 12 14C12.5 14 13 13.8 13.4 13.4M9.9 5.7C10.6 5.57 11.3 5.5 12 5.5C16 5.5 19.6 7.9 21.5 12C20.7 13.7 19.5 15.2 18 16.3M6.2 6.3C4.6 7.5 3.3 9.5 2.5 12C4.4 16.1 8 18.5 12 18.5C13.8 18.5 15.5 18 17 17.1"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linecap="round"
                  />
                </svg>
              </button>
            </div>
          </div>

          <button
            type="submit"
            class="btn btn-primary login-submit-btn"
            :disabled="submitting"
          >
            {{ submitting ? t('login.submitting') : t('login.submit') }}
          </button>
        </form>
      </div>

      <div class="login-card-footer">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          style="flex-shrink: 0; margin-top: 2px"
        >
          <rect x="5" y="11" width="14" height="10" rx="2" stroke="currentColor" stroke-width="1.8" />
          <path
            d="M8 11V7C8 4.79086 9.79086 3 12 3C14.2091 3 16 4.79086 16 7V11"
            stroke="currentColor"
            stroke-width="1.8"
          />
        </svg>
        <span>{{ t('login.securityHint') }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, ApiError } from '../api/client'
import { useI18n } from '../i18n/messages'

defineProps({
  sessionExpiredNotice: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['authenticated'])

const { t } = useI18n()

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const errorCode = ref(null)
const errorDetail = ref('')
const usernameInputRef = ref(null)

onMounted(() => {
  usernameInputRef.value?.focus()
})

const errorState = computed(() => {
  if (!errorCode.value) return null
  if (errorCode.value === 'required') {
    return {
      kind: 'error',
      title: t('login.errors.genericTitle'),
      body: t('login.errors.required'),
    }
  }
  if (errorCode.value === 401) {
    return {
      kind: 'error',
      title: t('login.errors.invalidCredentialsTitle'),
      body: t('login.errors.invalidCredentialsBody'),
    }
  }
  if (errorCode.value === 403) {
    return {
      kind: 'forbidden',
      title: t('login.errors.forbiddenTitle'),
      body: t('login.errors.forbiddenBody'),
    }
  }
  if (errorCode.value === 429) {
    return {
      kind: 'error',
      title: t('login.errors.rateLimitedTitle'),
      body: t('login.errors.rateLimitedBody'),
    }
  }
  if (errorCode.value === 0) {
    return {
      kind: 'error',
      title: t('login.errors.networkTitle'),
      body: t('login.errors.networkBody'),
    }
  }
  return {
    kind: 'error',
    title: t('login.errors.genericTitle'),
    body: errorDetail.value || `HTTP ${errorCode.value}`,
  }
})

async function handleSubmit() {
  const cleanUser = username.value.trim()
  if (!cleanUser || !password.value) {
    errorCode.value = 'required'
    return
  }

  submitting.value = true
  errorCode.value = null
  errorDetail.value = ''

  try {
    const res = await api.login(cleanUser, password.value)
    password.value = ''
    emit('authenticated', res)
  } catch (err) {
    if (err instanceof ApiError) {
      errorCode.value = err.status
      errorDetail.value = err.detail || ''
    } else {
      errorCode.value = 500
      errorDetail.value = err?.message || ''
    }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2.5rem 1rem;
  min-height: calc(100vh - 64px);
}

.login-card {
  width: 100%;
  max-width: 460px;
}

.login-card-header {
  padding: 1.5rem 1.65rem 1.15rem;
  border-bottom: 1px solid var(--border-subtle);
}

.login-title {
  margin: 0 0 0.4rem;
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text-primary);
}

.login-subtitle {
  margin: 0;
  font-size: 0.88rem;
  color: var(--text-secondary);
}

.login-card-body {
  padding: 1.35rem 1.65rem;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.password-field-wrap {
  position: relative;
}

.password-input {
  padding-right: 2.6rem;
}

.password-toggle-btn {
  position: absolute;
  right: 0.45rem;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  color: var(--text-muted);
  padding: 0.28rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.password-toggle-btn:hover {
  color: var(--text-primary);
  background-color: var(--bg-muted);
}

.login-submit-btn {
  width: 100%;
  padding: 0.62rem 1rem;
  margin-top: 0.25rem;
  font-size: 0.94rem;
}

.login-card-footer {
  padding: 0.95rem 1.65rem;
  background-color: var(--bg-subtle);
  border-top: 1px solid var(--border-subtle);
  display: flex;
  align-items: flex-start;
  gap: 0.55rem;
  font-size: 0.79rem;
  color: var(--text-muted);
}
</style>
