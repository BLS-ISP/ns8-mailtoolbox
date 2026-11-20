<template>
  <div class="app-side-menu-content">
    <div class="instance-name">
      <div v-if="instanceLabel">{{ instanceLabel }}</div>
      <div v-else-if="instanceName">{{ instanceName }}</div>
      <cv-skeleton-text
        v-else
        :width="instanceNameSkeletonWidth"
      ></cv-skeleton-text>
    </div>

    <cv-side-nav-items>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'status')"
        :class="{ 'current-page': isLinkActive('status') }"
      >
        <template v-slot:nav-icon><Activity20 /></template>
        <span>{{ $t("status.title") }}</span>
      </cv-side-nav-link>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'mailboxes')"
        :class="{ 'current-page': isLinkActive('mailboxes') }"
      >
        <template v-slot:nav-icon><Email20 /></template>
        <span>{{ $t("mailboxes.title") }}</span>
      </cv-side-nav-link>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'signatures')"
        :class="{ 'current-page': isLinkActive('signatures') }"
      >
        <template v-slot:nav-icon><Pen20 /></template>
        <span>{{ $t("signatures.title") }}</span>
      </cv-side-nav-link>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'mail-tracking')"
        :class="{ 'current-page': isLinkActive('mail-tracking') }"
      >
        <template v-slot:nav-icon><Security20 /></template>
        <span>{{ $t("tracking.title") }}</span>
      </cv-side-nav-link>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'connectors')"
        :class="{ 'current-page': isLinkActive('connectors') }"
      >
        <template v-slot:nav-icon><Connect20 /></template>
        <span>{{ $t("connectors.title") }}</span>
      </cv-side-nav-link>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'templates')"
        :class="{ 'current-page': isLinkActive('templates') }"
      >
        <template v-slot:nav-icon><Catalog20 /></template>
        <span>{{ $t("templates.title") }}</span>
      </cv-side-nav-link>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'settings')"
        :class="{ 'current-page': isLinkActive('settings') }"
      >
        <template v-slot:nav-icon><Settings20 /></template>
        <span>{{ $t("settings.title") }}</span>
      </cv-side-nav-link>
      <cv-side-nav-link
        @click="goToAppPage(instanceName, 'about')"
        :class="{ 'current-page': isLinkActive('about') }"
      >
        <template v-slot:nav-icon><Information20 /></template>
        <span>{{ $t("about.title") }}</span>
      </cv-side-nav-link>
    </cv-side-nav-items>

    <div
      class="side-lang-switcher"
      role="group"
      :aria-label="$t('common.language')"
    >
      <button
        type="button"
        class="lang-btn"
        :class="{ active: $i18n.locale === 'en' }"
        @click="switchLanguage('en')"
      >
        EN
      </button>
      <span class="lang-sep">|</span>
      <button
        type="button"
        class="lang-btn"
        :class="{ active: $i18n.locale === 'de' }"
        @click="switchLanguage('de')"
      >
        DE
      </button>
    </div>
  </div>
</template>

<script>
import Activity20 from "@carbon/icons-vue/es/activity/20";
import Email20 from "@carbon/icons-vue/es/email/20";
import Pen20 from "@carbon/icons-vue/es/pen/20";
import Security20 from "@carbon/icons-vue/es/security/20";
import Connect20 from "@carbon/icons-vue/es/connect/20";
import Catalog20 from "@carbon/icons-vue/es/catalog/20";
import Settings20 from "@carbon/icons-vue/es/settings/20";
import Information20 from "@carbon/icons-vue/es/information/20";
import { mapState } from "vuex";
import { QueryParamService, UtilService } from "@nethserver/ns8-ui-lib";

const COCKPIT_LANG_STORAGE_KEY = "ns8_mail_toolbox_cockpit_lang";

export default {
  name: "AppSideMenuContent",
  components: {
    Activity20,
    Email20,
    Pen20,
    Security20,
    Connect20,
    Catalog20,
    Settings20,
    Information20,
  },
  mixins: [QueryParamService, UtilService],
  data() {
    return {
      instanceNameSkeletonWidth: "70%",
    };
  },
  computed: {
    ...mapState(["instanceName", "instanceLabel", "core"]),
  },
  created() {
    this.$root.$on("appNavigation", this.onAppNavigation);
  },
  beforeDestroy() {
    this.$root.$off("appNavigation");
  },
  methods: {
    isLinkActive(page) {
      return this.getPage() === page;
    },
    onAppNavigation() {
      this.$forceUpdate();
    },
    switchLanguage(lang) {
      if (lang !== "en" && lang !== "de") {
        return;
      }
      this.$i18n.locale = lang;
      try {
        window.localStorage.setItem(COCKPIT_LANG_STORAGE_KEY, lang);
      } catch (e) {
        // ignore storage errors
      }
    },
  },
};
</script>

<style scoped lang="scss">
.app-side-menu-content {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.instance-name span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.side-lang-switcher {
  margin-top: auto;
  padding: 0.75rem 1rem;
  display: flex;
  align-items: center;
  gap: 0.375rem;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
}

.lang-btn {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 2px;
  color: #c6c6c6;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.15rem 0.45rem;
  cursor: pointer;

  &:hover {
    color: #ffffff;
    background: rgba(255, 255, 255, 0.08);
  }

  &.active {
    color: #ffffff;
    border-color: #78a9ff;
    background: rgba(15, 98, 254, 0.28);
  }
}

.lang-sep {
  color: #6f6f6f;
  font-size: 0.75rem;
}
</style>
