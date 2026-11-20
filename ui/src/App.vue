<template>
  <div id="ns8-app">
    <cv-content id="main-content" class="app-content">
      <AppSideMenu />
      <AppMobileSideMenu />
      <router-view />
    </cv-content>
  </div>
</template>

<script>
import AppSideMenu from "./components/AppSideMenu";
import AppMobileSideMenu from "./components/AppMobileSideMenu";
import { mapState, mapActions } from "vuex";
import {
  QueryParamService,
  TaskService,
  UtilService,
} from "@nethserver/ns8-ui-lib";
import to from "await-to-js";

const COCKPIT_LANG_STORAGE_KEY = "ns8_mail_toolbox_cockpit_lang";

export default {
  name: "App",
  components: { AppSideMenu, AppMobileSideMenu },
  mixins: [QueryParamService, TaskService, UtilService],
  computed: {
    ...mapState(["instanceName", "instanceLabel", "core"]),
    coreLocale() {
      return this.core && this.core.$i18n ? this.core.$i18n.locale : null;
    },
  },
  watch: {
    // Follow runtime language switches triggered by the user in NS8 Cockpit
    // during the session, without overriding the English default on mount.
    coreLocale(newLang, oldLang) {
      if (!oldLang || !newLang || newLang === oldLang) {
        return;
      }
      try {
        const explicitChoice = window.localStorage.getItem(
          COCKPIT_LANG_STORAGE_KEY
        );
        if (explicitChoice === "en" || explicitChoice === "de") {
          return;
        }
      } catch (e) {
        // ignore storage access errors
      }
      const normalized = String(newLang).substring(0, 2).toLowerCase();
      if (normalized === "en" || normalized === "de") {
        this.$i18n.locale = normalized;
      }
    },
  },
  created() {
    const core = window.parent.core;
    this.setCoreInStore(core);
    const hashMatch = /#\/apps\/([a-zA-Z0-9_-]+)/.exec(
      window.parent.location.hash
    );
    const instanceName = hashMatch ? hashMatch[1] : "mail-toolbox1";
    this.setInstanceNameInStore(instanceName);
    this.getInstanceLabel();
    this.setAppName();

    // listen to change route events
    const context = this;
    window.addEventListener(
      "changeRoute",
      function (e) {
        const requestedPage = e.detail;
        context.$router.replace(requestedPage);
      },
      false
    );

    // configure global shortcuts
    if (core && core.$root) {
      core.$root.$emit("configureKeyboardShortcuts", window);
    }

    const queryParams = this.getQueryParamsForApp();
    const requestedPage = queryParams.page || "status";

    if (requestedPage !== "status") {
      this.$router.replace(requestedPage);
    }
  },
  methods: {
    ...mapActions([
      "setInstanceNameInStore",
      "setInstanceLabelInStore",
      "setCoreInStore",
      "setAppNameInStore",
    ]),
    async getInstanceLabel() {
      const taskAction = "get-name";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getInstanceLabelAborted
      );

      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getInstanceLabelCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: taskAction,
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];

      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
        return;
      }
    },
    getInstanceLabelAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
    },
    getInstanceLabelCompleted(taskContext, taskResult) {
      this.setInstanceLabelInStore(taskResult.output.name);
    },
    setAppName() {
      const metadata = require("../public/metadata.json");
      const appName = metadata.name;
      this.setAppNameInStore(appName);
    },
  },
};
</script>

<style lang="scss">
@import "styles/carbon-utils";
</style>
