<template>
  <header class="top-header">
    <div class="top-header-inner">
      <div class="brand-group">
        <span class="brand-mark" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="5" width="18" height="14" rx="2" stroke="#FFFFFF" stroke-width="1.9" />
            <path
              d="M4 6.5L12 12.5L20 6.5"
              stroke="#FFFFFF"
              stroke-width="1.9"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </span>
        <span class="brand-title">{{ t('app.brand') }}</span>
        <span class="brand-portal-pill">
          {{ quarantineMode ? t('app.quarantineBadge') : t('app.portalBadge') }}
        </span>
      </div>

      <div class="header-controls">
        <div
          class="lang-switcher"
          role="group"
          :aria-label="t('app.langSwitchAria')"
        >
          <button
            type="button"
            class="lang-btn"
            :class="{ 'is-active': locale === 'de' }"
            :aria-pressed="locale === 'de'"
            @click="setLocale('de')"
          >
            DE
          </button>
          <button
            type="button"
            class="lang-btn"
            :class="{ 'is-active': locale === 'en' }"
            :aria-pressed="locale === 'en'"
            @click="setLocale('en')"
          >
            EN
          </button>
        </div>

        <template v-if="user">
          <div class="user-chip" :title="`${t('app.signedInAs')}: ${user.username}`">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="8" r="4" stroke="currentColor" stroke-width="1.8" />
              <path
                d="M5 20C5 16.6863 8.13401 14 12 14C15.866 14 19 16.6863 19 20"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
              />
            </svg>
            <span>{{ user.display_name || user.username }}</span>
            <span
              v-for="grp in displayedGroups"
              :key="grp"
              class="user-group-tag"
            >
              {{ grp }}
            </span>
          </div>

          <button
            type="button"
            class="btn btn-header-logout"
            :disabled="loggingOut"
            @click="$emit('logout')"
          >
            {{ loggingOut ? t('app.loggingOut') : t('app.logout') }}
          </button>
        </template>
      </div>
    </div>

    <nav
      v-if="user && !quarantineMode"
      class="workspace-nav"
      :aria-label="t('app.portalBadge')"
    >
      <div class="workspace-nav-inner" role="tablist">
        <button
          type="button"
          role="tab"
          class="workspace-tab"
          :class="{ 'is-active': activeTab === 'mailboxes' }"
          :aria-selected="activeTab === 'mailboxes'"
          @click="$emit('change-tab', 'mailboxes')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" stroke-width="1.8" />
            <path d="M3 10H21" stroke="currentColor" stroke-width="1.8" />
          </svg>
          <span>{{ t('app.tabs.mailboxes') }}</span>
        </button>

        <button
          v-if="user.portal_tracking_enabled === true"
          type="button"
          role="tab"
          class="workspace-tab"
          :class="{ 'is-active': activeTab === 'tracking' }"
          :aria-selected="activeTab === 'tracking'"
          @click="$emit('change-tab', 'tracking')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8" />
            <path
              d="M16.2 16.2L20.5 20.5"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
            />
          </svg>
          <span>{{ t('app.tabs.tracking') }}</span>
        </button>

        <button
          type="button"
          role="tab"
          class="workspace-tab"
          :class="{ 'is-active': activeTab === 'audit' }"
          :aria-selected="activeTab === 'audit'"
          @click="$emit('change-tab', 'audit')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M12 8V12L15 14M21 12C21 16.9706 16.9706 21 12 21C7.02944 21 3 16.9706 3 12C3 7.02944 7.02944 3 12 3C16.9706 3 21 7.02944 21 12Z"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <span>{{ t('app.tabs.audit') }}</span>
        </button>
      </div>
    </nav>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../i18n/messages'

const props = defineProps({
  user: {
    type: Object,
    default: null,
  },
  activeTab: {
    type: String,
    default: 'mailboxes',
  },
  loggingOut: {
    type: Boolean,
    default: false,
  },
  quarantineMode: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['logout', 'change-tab'])

const { locale, setLocale, t } = useI18n()

const displayedGroups = computed(() => {
  if (!Array.isArray(props.user?.groups)) return []
  return props.user.groups.slice(0, 2)
})
</script>
