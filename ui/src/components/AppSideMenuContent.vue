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
  },
};
</script>

<style scoped lang="scss">
.instance-name span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
