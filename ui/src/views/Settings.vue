<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("settings.title") }}</h2>
      </cv-column>
    </cv-row>

    <cv-row v-if="error.getConfiguration">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-configuration')"
          :description="error.getConfiguration"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="error.discoverMailInstance">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.discover-mail-instance')"
          :description="error.discoverMailInstance"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="savedConfigurationNotice">
      <cv-column>
        <NsInlineNotification
          kind="success"
          :title="$t('settings.saved_title')"
          :description="savedConfigurationNotice"
          @close="savedConfigurationNotice = ''"
        />
      </cv-column>
    </cv-row>

    <cv-row>
      <cv-column>
        <cv-tile light>
          <cv-skeleton-text
            v-show="loading.getConfiguration"
            heading
            paragraph
            :line-count="12"
            width="80%"
          />

          <cv-form
            v-show="!loading.getConfiguration"
            @submit.prevent="configureModule"
            class="maxwidth"
          >
            <NsInlineNotification
              v-if="!availableMailServers.length && !loading.getConfiguration"
              kind="warning"
              :title="$t('settings.no_mail_servers_title')"
              :description="$t('settings.no_mail_servers_description')"
              :showCloseButton="false"
              class="mg-bottom"
            />

            <h4 class="section-heading">
              {{ $t("settings.section_core_portal") }}
            </h4>

            <!-- Coupled ns8-mail Instance + Reload Discovery Button -->
            <div class="mail-discovery-row mg-bottom-sm">
              <div class="mail-select-col">
                <NsComboBox
                  v-model="mail_module_uuid"
                  :title="$t('settings.mail_server_label')"
                  :label="$t('settings.mail_server_placeholder')"
                  :helperText="mailServerHelperText"
                  :invalidMessage="error.mail_module_uuid"
                  :autoFilter="true"
                  :autoHighlight="true"
                  :options="mailServerOptions"
                  :disabled="stillLoading || !mailServerOptions.length"
                  marginBottomOnOpen
                  @change="onMailServerChange"
                  ref="mail_module_uuid"
                />
              </div>
              <NsButton
                type="button"
                kind="tertiary"
                size="field"
                :icon="Renew20"
                :loading="loading.discoverMailInstance"
                :disabled="stillLoading || !mail_module_uuid"
                @click="discoverMailInstance(mail_module_uuid)"
              >
                {{ $t("settings.reload_discovery_btn") }}
              </NsButton>
            </div>

            <!-- Live Discovered Domain & Group Count Info Strip -->
            <div v-if="effectiveUserDomain" class="domain-info-strip mg-bottom">
              <NsTag
                :label="
                  $t('settings.discovered_domain_badge', {
                    domain: effectiveUserDomain,
                    schema: formattedLdapSchema,
                    count: nonSystemGroupsCount,
                  })
                "
                kind="cyan"
                size="sm"
              />
            </div>

            <!-- Optional Traefik Secretariat & Quarantine Web Portal Toggle -->
            <NsToggle
              value="portalEnabled"
              :label="$t('settings.portal_enabled_label')"
              v-model="portal_enabled"
              :disabled="stillLoading"
              class="mg-bottom"
            >
              <template slot="tooltip">
                <span>{{ $t("settings.portal_enabled_tooltip") }}</span>
              </template>
              <template slot="text-left">{{ $t("common.disabled") }}</template>
              <template slot="text-right">{{ $t("common.enabled") }}</template>
            </NsToggle>

            <NsInlineNotification
              v-if="!portal_enabled && digest_enabled"
              kind="warning"
              :title="$t('settings.portal_disabled_digest_warn_title')"
              :description="
                $t('settings.portal_disabled_digest_warn_description')
              "
              :showCloseButton="false"
              class="mg-bottom"
            />

            <template v-if="portal_enabled">
              <!-- Secretariat Web Portal FQDN -->
              <NsTextInput
                :label="$t('settings.portal_hostname_label')"
                :placeholder="$t('settings.portal_hostname_placeholder')"
                :helperText="$t('settings.portal_hostname_helper')"
                v-model.trim="portal_hostname"
                :invalidMessage="error.portal_hostname"
                :disabled="stillLoading"
                class="mg-bottom"
                ref="portal_hostname"
              />

              <!-- Let's Encrypt Toggle -->
              <NsToggle
                value="letsEncrypt"
                :label="
                  coreT(
                    'apps_lets_encrypt.request_https_certificate',
                    'settings.lets_encrypt_label'
                  )
                "
                v-model="lets_encrypt"
                :disabled="stillLoading"
                class="mg-bottom"
              >
                <template slot="tooltip">
                  <span>{{ $t("settings.lets_encrypt_tooltip") }}</span>
                </template>
                <template slot="text-left">{{
                  $t("common.disabled")
                }}</template>
                <template slot="text-right">{{
                  $t("common.enabled")
                }}</template>
              </NsToggle>

              <!-- HTTP to HTTPS Toggle -->
              <NsToggle
                value="http2https"
                :label="$t('settings.http2https_label')"
                v-model="http2https"
                :disabled="stillLoading"
                class="mg-bottom"
              >
                <template slot="tooltip">
                  <span>{{ $t("settings.http2https_tooltip") }}</span>
                </template>
                <template slot="text-left">{{
                  $t("common.disabled")
                }}</template>
                <template slot="text-right">{{
                  $t("common.enabled")
                }}</template>
              </NsToggle>

              <!-- Allowed Secretariat AD/LDAP Groups (Optional!) -->
              <NsMultiSelect
                v-model="allowed_groups"
                :options="groupOptions"
                :title="$t('settings.allowed_groups_label')"
                :label="$t('settings.allowed_groups_placeholder')"
                :helperText="$t('settings.allowed_groups_helper')"
                :invalidMessage="error.allowed_groups"
                :filterable="true"
                :autoFilter="true"
                :autoHighlight="true"
                :acceptUserInput="true"
                :showSelectedItems="true"
                :userInputLabel="$t('settings.custom_group_label')"
                :selectedLabel="$t('settings.selected_label')"
                :clearFilterLabel="$t('common.clear_search')"
                :disabled="stillLoading"
                marginBottomOnOpen
                class="mg-bottom-xs"
                ref="allowed_groups"
              />

              <cv-checkbox
                v-if="systemGroupsCount > 0"
                value="showSystemGroups"
                :label="
                  $t('settings.show_system_groups_label', {
                    count: systemGroupsCount,
                  })
                "
                v-model="showSystemGroups"
                :disabled="stillLoading"
                class="mg-bottom-sm"
              />

              <NsInlineNotification
                v-if="!allowed_groups.length"
                kind="info"
                :title="$t('settings.empty_allowed_groups_info_title')"
                :description="
                  $t('settings.empty_allowed_groups_info_description')
                "
                :showCloseButton="false"
                class="mg-bottom"
              />

              <!-- Read-Only Mail-Tracking in Secretariat Portal -->
              <NsToggle
                value="portalTrackingEnabled"
                :label="$t('settings.portal_tracking_enabled_label')"
                v-model="portal_tracking_enabled"
                :disabled="stillLoading"
                class="mg-bottom"
              >
                <template slot="tooltip">
                  <span>{{
                    $t("settings.portal_tracking_enabled_tooltip")
                  }}</span>
                </template>
                <template slot="text-left">{{
                  $t("common.disabled")
                }}</template>
                <template slot="text-right">{{
                  $t("common.enabled")
                }}</template>
              </NsToggle>
            </template>

            <!-- Phase 2: Before-DKIM Signature Milter -->
            <h4 class="section-heading">
              {{ $t("settings.section_milter") }}
            </h4>
            <NsToggle
              value="milterEnabled"
              :label="$t('settings.milter_enabled_label')"
              v-model="milter_enabled"
              :disabled="stillLoading"
              class="mg-bottom"
            >
              <template slot="tooltip">
                <span>{{ $t("settings.milter_enabled_tooltip") }}</span>
              </template>
              <template slot="text-left">{{ $t("common.disabled") }}</template>
              <template slot="text-right">{{ $t("common.enabled") }}</template>
            </NsToggle>

            <!-- Phase 3b: Rspamd Controller API (Mail-Tracking Search) -->
            <h4 class="section-heading">
              {{ $t("settings.section_rspamd") }}
            </h4>
            <NsTextInput
              :label="$t('settings.rspamd_url_label')"
              :placeholder="
                effectiveDiscoveredRspamdUrl ||
                'https://mail.example.org/rspamd'
              "
              :helperText="$t('settings.rspamd_url_helper')"
              v-model.trim="rspamd_url"
              :invalidMessage="error.rspamd_url"
              :disabled="stillLoading"
              class="mg-bottom-xs"
              ref="rspamd_url"
            />

            <!-- Quick-Fill Mode Pills for Rspamd URL -->
            <div class="rspamd-quick-btns mg-bottom-sm">
              <cv-button
                v-if="effectiveDiscoveredRspamdUrl"
                type="button"
                size="small"
                kind="ghost"
                :disabled="stillLoading"
                @click="useDiscoveredRspamdUrl"
              >
                {{
                  $t("settings.use_discovered_rspamd_btn", {
                    url: effectiveDiscoveredRspamdUrl,
                  })
                }}
              </cv-button>
              <cv-button
                type="button"
                size="small"
                kind="ghost"
                :disabled="stillLoading"
                @click="useLocalDirectRspamdUrl"
              >
                {{ $t("settings.use_local_rspamd_btn") }}
              </cv-button>
            </div>

            <!-- Rspamd / NS8 Admin Username for HTTPS Traefik forward_auth -->
            <NsTextInput
              :label="$t('settings.rspamd_user_label')"
              :placeholder="$t('settings.rspamd_user_placeholder')"
              :helperText="$t('settings.rspamd_user_helper')"
              v-model.trim="rspamd_user"
              :disabled="stillLoading"
              class="mg-bottom-sm"
            />

            <NsTextInput
              type="password"
              :label="$t('settings.rspamd_password_label')"
              :placeholder="
                rspamdPasswordSet && !clear_rspamd_password
                  ? $t('connectors.password_keep_placeholder')
                  : $t('settings.rspamd_password_placeholder')
              "
              :helperText="
                rspamdPasswordSet
                  ? $t('settings.rspamd_password_saved_helper')
                  : $t('settings.rspamd_password_helper')
              "
              v-model="rspamd_password"
              :disabled="stillLoading || clear_rspamd_password"
              class="mg-bottom-sm"
            />

            <cv-checkbox
              v-if="rspamdPasswordSet"
              value="clearRspamdPassword"
              :label="$t('settings.clear_rspamd_password_label')"
              v-model="clear_rspamd_password"
              :disabled="stillLoading"
              class="mg-bottom-sm"
            />

            <!-- Self-Signed TLS Verification Toggle (Default OFF for NS8 Self-Signed Certs) -->
            <NsToggle
              value="rspamdTlsVerify"
              :label="$t('settings.rspamd_tls_verify_label')"
              v-model="rspamd_tls_verify"
              :disabled="stillLoading"
              class="mg-bottom-sm"
            >
              <template slot="tooltip">
                <span>{{ $t("settings.rspamd_tls_verify_tooltip") }}</span>
              </template>
              <template slot="text-left">{{
                $t("settings.tls_allow_self_signed")
              }}</template>
              <template slot="text-right">{{
                $t("settings.tls_strict_verify")
              }}</template>
            </NsToggle>

            <div class="mg-bottom">
              <NsButton
                type="button"
                kind="tertiary"
                size="field"
                :icon="Checkmark20"
                :loading="loading.testRspamd"
                :disabled="loading.testRspamd || stillLoading"
                @click="testRspamdConnection"
              >
                {{ $t("settings.test_rspamd_btn") }}
              </NsButton>
              <NsInlineNotification
                v-if="rspamdTestResult"
                :kind="rspamdTestResult.ok ? 'success' : 'error'"
                :title="
                  rspamdTestResult.ok
                    ? $t('settings.rspamd_test_ok_title')
                    : $t('settings.rspamd_test_failed_title')
                "
                :description="rspamdTestResult.message"
                @close="rspamdTestResult = null"
                class="mg-top-sm"
              />
            </div>

            <!-- Phase 3a: Daily Spam Quarantine Digest -->
            <h4 class="section-heading">
              {{ $t("settings.section_digest") }}
            </h4>
            <NsToggle
              value="digestEnabled"
              :label="$t('settings.digest_enabled_label')"
              v-model="digest_enabled"
              :disabled="stillLoading"
              class="mg-bottom-sm"
            >
              <template slot="tooltip">
                <span>{{ $t("settings.digest_enabled_tooltip") }}</span>
              </template>
              <template slot="text-left">{{ $t("common.disabled") }}</template>
              <template slot="text-right">{{ $t("common.enabled") }}</template>
            </NsToggle>

            <div class="digest-grid mg-bottom">
              <NsTextInput
                :label="$t('settings.digest_time_label')"
                placeholder="07:30"
                :helperText="$t('settings.digest_time_helper')"
                v-model.trim="digest_time"
                :invalidMessage="error.digest_time"
                :disabled="stillLoading"
                ref="digest_time"
              />
              <NsTextInput
                :label="$t('settings.timezone_label')"
                placeholder="Europe/Berlin"
                v-model.trim="timezone"
                :invalidMessage="error.timezone"
                :disabled="stillLoading"
                ref="timezone"
              />
            </div>

            <!-- Phase 3c: External Connector Failure Alert Email -->
            <h4 class="section-heading">
              {{ $t("settings.section_connector_alerts") }}
            </h4>
            <NsTextInput
              :label="$t('settings.admin_alert_email_label')"
              placeholder="it-admin@example.com"
              :helperText="$t('settings.admin_alert_email_helper')"
              v-model.trim="admin_alert_email"
              :invalidMessage="error.admin_alert_email"
              :disabled="stillLoading"
              class="mg-bottom"
              ref="admin_alert_email"
            />

            <cv-row v-if="error.configureModule">
              <cv-column>
                <NsInlineNotification
                  kind="error"
                  :title="$t('action.configure-module')"
                  :description="error.configureModule"
                  :showCloseButton="false"
                  class="mg-bottom"
                />
              </cv-column>
            </cv-row>

            <cv-row v-if="validationErrorDetails.length">
              <cv-column>
                <NsInlineNotification
                  kind="error"
                  :title="$t('error.validation_error')"
                  :description="validationErrorDetails.join('; ')"
                  :showCloseButton="false"
                  class="mg-bottom"
                />
              </cv-column>
            </cv-row>

            <NsButton
              kind="primary"
              :icon="Save20"
              :loading="loading.configureModule"
              :disabled="stillLoading"
            >
              {{ $t("settings.save") }}
            </NsButton>
          </cv-form>
        </cv-tile>
      </cv-column>
    </cv-row>
  </cv-grid>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import {
  QueryParamService,
  UtilService,
  TaskService,
  IconService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";
import Save20 from "@carbon/icons-vue/es/save/20";
import Renew20 from "@carbon/icons-vue/es/renew/20";
import Checkmark20 from "@carbon/icons-vue/es/checkmark/20";

export default {
  name: "Settings",
  mixins: [
    TaskService,
    IconService,
    UtilService,
    QueryParamService,
    PageTitleService,
  ],
  pageTitle() {
    return this.$t("settings.title") + " - " + this.appName;
  },
  data() {
    return {
      Save20,
      Renew20,
      Checkmark20,
      q: {
        page: "settings",
      },
      urlCheckInterval: null,
      isModuleConfigured: false,
      mail_module_uuid: "",
      portal_enabled: true,
      portal_hostname: "",
      lets_encrypt: false,
      http2https: true,
      allowed_groups: [],
      showSystemGroups: false,
      portal_tracking_enabled: false,
      milter_enabled: true,
      discovered_rspamd_url: "",
      ldap_schema: "",
      discoveredUserDomain: "",
      rspamd_url: "",
      rspamd_user: "",
      rspamd_password: "",
      rspamdPasswordSet: false,
      clear_rspamd_password: false,
      rspamd_tls_verify: false,
      digest_enabled: false,
      digest_time: "07:30",
      timezone: "Europe/Berlin",
      admin_alert_email: "",
      availableMailServers: [],
      availableGroups: [],
      validationErrorDetails: [],
      savedConfigurationNotice: "",
      rspamdTestResult: null,
      loading: {
        getConfiguration: false,
        discoverMailInstance: false,
        configureModule: false,
        testRspamd: false,
      },
      error: {
        getConfiguration: "",
        discoverMailInstance: "",
        configureModule: "",
        mail_module_uuid: "",
        portal_hostname: "",
        allowed_groups: "",
        rspamd_url: "",
        digest_time: "",
        timezone: "",
        admin_alert_email: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    stillLoading() {
      return this.loading.getConfiguration || this.loading.configureModule;
    },
    mailServerOptions() {
      return this.availableMailServers.map((srv) => {
        const hostPart = srv.mail_hostname ? ` — ${srv.mail_hostname}` : "";
        const domainPart = srv.user_domain ? ` (${srv.user_domain})` : "";
        const recPart = srv.same_node
          ? ` ★ ${this.$t("settings.same_node_recommended")}`
          : "";
        return {
          name: srv.module_id || srv.module_uuid,
          label: `${srv.module_id}${hostPart}${domainPart}${recPart}`,
          value: srv.module_uuid,
        };
      });
    },
    selectedMailServer() {
      return (
        this.availableMailServers.find(
          (srv) => srv.module_uuid === this.mail_module_uuid
        ) || null
      );
    },
    effectiveUserDomain() {
      return (
        this.discoveredUserDomain ||
        (this.selectedMailServer && this.selectedMailServer.user_domain) ||
        ""
      );
    },
    formattedLdapSchema() {
      if (this.ldap_schema === "ad") {
        return "Active Directory";
      }
      if (this.ldap_schema === "rfc2307") {
        return "OpenLDAP";
      }
      return "LDAP/AD";
    },
    effectiveDiscoveredRspamdUrl() {
      return (
        this.discovered_rspamd_url ||
        (this.selectedMailServer && this.selectedMailServer.rspamd_url) ||
        ""
      );
    },
    mailServerHelperText() {
      if (this.selectedMailServer && this.selectedMailServer.same_node) {
        return this.$t("settings.same_node_hint", {
          domain: this.selectedMailServer.user_domain || "-",
        });
      }
      if (this.selectedMailServer) {
        return this.$t("settings.selected_domain_hint", {
          domain: this.selectedMailServer.user_domain || "-",
        });
      }
      return this.$t("settings.mail_server_helper");
    },
    systemGroupsCount() {
      return this.availableGroups.filter(
        (item) => item && typeof item === "object" && item.is_system === true
      ).length;
    },
    nonSystemGroupsCount() {
      return this.availableGroups.filter(
        (item) => !(item && typeof item === "object" && item.is_system === true)
      ).length;
    },
    groupOptions() {
      const seen = new Set();
      const opts = [];

      for (const item of this.availableGroups) {
        const grp = typeof item === "string" ? item : item.group;
        if (!grp || seen.has(grp)) {
          continue;
        }
        const isSys = Boolean(item && item.is_system === true);
        if (
          isSys &&
          !this.showSystemGroups &&
          !this.allowed_groups.includes(grp)
        ) {
          continue;
        }
        seen.add(grp);
        const hasCount =
          item && item.member_count !== null && item.member_count !== undefined;
        const countPart = hasCount ? ` (${item.member_count})` : "";
        const descPart =
          item && item.description ? ` — ${item.description}` : "";
        opts.push({
          name: grp,
          label: `${grp}${countPart}${descPart}`,
          value: grp,
        });
      }

      for (const selected of this.allowed_groups) {
        if (selected && !seen.has(selected)) {
          seen.add(selected);
          opts.push({
            name: selected,
            label: selected,
            value: selected,
          });
        }
      }

      return opts;
    },
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      vm.watchQueryData(vm);
      vm.urlCheckInterval = vm.initUrlBindingForApp(vm, vm.q.page);
    });
  },
  beforeRouteLeave(to, from, next) {
    clearInterval(this.urlCheckInterval);
    next();
  },
  created() {
    this.getConfiguration();
  },
  methods: {
    coreT(coreKey, fallbackKey) {
      if (this.core && typeof this.core.$t === "function") {
        return this.core.$t(coreKey);
      }
      return this.$t(fallbackKey);
    },
    useDiscoveredRspamdUrl() {
      if (this.effectiveDiscoveredRspamdUrl) {
        this.rspamd_url = this.effectiveDiscoveredRspamdUrl;
        this.rspamd_tls_verify = false;
        this.error.rspamd_url = "";
      }
    },
    useLocalDirectRspamdUrl() {
      this.rspamd_url = "http://127.0.0.1:11334";
      this.rspamd_user = "";
      this.rspamd_tls_verify = false;
      this.error.rspamd_url = "";
    },
    onMailServerChange(newUuid) {
      if (newUuid) {
        this.discoverMailInstance(newUuid);
      }
    },
    async discoverMailInstance(targetUuid) {
      if (!targetUuid) {
        return;
      }
      this.loading.discoverMailInstance = true;
      this.error.discoverMailInstance = "";
      const taskAction = "discover-mail-instance";
      const eventId = this.getUuid();
      const prevDiscoveredUrl = this.effectiveDiscoveredRspamdUrl;

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.discoverMailInstance = false;
        this.error.discoverMailInstance = this.$t("error.generic_error");
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.loading.discoverMailInstance = false;
          const out = (taskResult && taskResult.output) || {};
          if (Array.isArray(out.available_groups)) {
            this.availableGroups = out.available_groups;
          }
          if (out.user_domain !== undefined) {
            this.discoveredUserDomain = out.user_domain || "";
          }
          if (out.ldap_schema !== undefined) {
            this.ldap_schema = out.ldap_schema || "";
          }
          if (out.discovered_rspamd_url) {
            this.discovered_rspamd_url = out.discovered_rspamd_url;
            if (
              !this.isModuleConfigured ||
              !this.rspamd_url ||
              this.rspamd_url === prevDiscoveredUrl
            ) {
              this.rspamd_url = out.discovered_rspamd_url;
              this.rspamd_tls_verify = false;
            }
          }
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "discover-mail-instance",
          data: {
            mail_module_uuid: targetUuid,
          },
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.loading.discoverMailInstance = false;
        this.error.discoverMailInstance = this.getErrorMessage(err);
      }
    },
    async getConfiguration() {
      this.loading.getConfiguration = true;
      this.error.getConfiguration = "";
      const taskAction = "get-configuration";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getConfigurationAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getConfigurationCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "get-configuration",
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
        this.error.getConfiguration = this.getErrorMessage(err);
        this.loading.getConfiguration = false;
      }
    },
    getConfigurationAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getConfiguration = this.$t("error.generic_error");
      this.loading.getConfiguration = false;
    },
    getConfigurationCompleted(taskContext, taskResult) {
      const config = taskResult.output || {};
      this.isModuleConfigured = Boolean(config.configured);
      this.availableMailServers = Array.isArray(config.available_mail_servers)
        ? config.available_mail_servers
        : [];
      this.availableGroups = Array.isArray(config.available_groups)
        ? config.available_groups
        : [];

      if (config.mail_module_uuid) {
        this.mail_module_uuid = config.mail_module_uuid;
      } else if (this.availableMailServers.length) {
        const sameNodeServer =
          this.availableMailServers.find((s) => s.same_node === true) ||
          this.availableMailServers[0];
        this.mail_module_uuid = sameNodeServer.module_uuid;
      } else {
        this.mail_module_uuid = "";
      }

      this.discoveredUserDomain = config.user_domain || "";
      this.ldap_schema = config.ldap_schema || "";
      this.discovered_rspamd_url = config.discovered_rspamd_url || "";

      this.portal_enabled =
        config.portal_enabled !== undefined
          ? Boolean(config.portal_enabled)
          : !config.configured || Boolean(config.portal_hostname);
      this.portal_hostname = config.portal_hostname || "";
      this.lets_encrypt = Boolean(config.lets_encrypt);
      this.http2https =
        config.http2https !== undefined ? Boolean(config.http2https) : true;
      this.allowed_groups = Array.isArray(config.allowed_groups)
        ? [...config.allowed_groups]
        : [];
      this.portal_tracking_enabled = Boolean(config.portal_tracking_enabled);

      this.milter_enabled =
        config.milter_enabled !== undefined
          ? Boolean(config.milter_enabled)
          : true;
      this.rspamd_url =
        config.rspamd_url ||
        config.discovered_rspamd_url ||
        "http://127.0.0.1:11334";
      this.rspamd_user = config.rspamd_user || "";
      this.rspamdPasswordSet = Boolean(config.rspamd_password_set);
      this.rspamd_password = "";
      this.clear_rspamd_password = false;
      this.rspamd_tls_verify =
        config.rspamd_tls_verify !== undefined
          ? Boolean(config.rspamd_tls_verify)
          : false;
      this.digest_enabled = Boolean(config.digest_enabled);
      this.digest_time = config.digest_time || "07:30";
      this.timezone = config.timezone || "Europe/Berlin";
      this.admin_alert_email = config.admin_alert_email || "";

      this.loading.getConfiguration = false;
      if (this.portal_enabled) {
        this.focusElement("portal_hostname");
      }
    },
    async testRspamdConnection() {
      this.error.rspamd_url = "";
      if (this.rspamd_url && !/^https?:\/\/.+/i.test(this.rspamd_url)) {
        this.error.rspamd_url = this.$t("settings.invalid_rspamd_url");
        this.focusElement("rspamd_url");
        return;
      }

      this.loading.testRspamd = true;
      this.rspamdTestResult = null;
      const taskAction = "test-rspamd";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.testRspamd = false;
        this.rspamdTestResult = {
          ok: false,
          message: this.$t("error.generic_error"),
        };
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.loading.testRspamd = false;
          const out = (taskResult && taskResult.output) || {};
          this.rspamdTestResult = {
            ok: Boolean(out.ok),
            message: out.ok
              ? this.$t("settings.rspamd_test_ok_message", {
                  count: out.entries_available || 0,
                })
              : out.message || this.$t("error.generic_error"),
          };
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "test-rspamd",
          data: {
            rspamd_url:
              this.rspamd_url ||
              this.effectiveDiscoveredRspamdUrl ||
              "http://127.0.0.1:11334",
            rspamd_user: this.rspamd_user || "",
            rspamd_password: this.rspamd_password || "",
            rspamd_tls_verify: Boolean(this.rspamd_tls_verify),
          },
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.loading.testRspamd = false;
        this.rspamdTestResult = {
          ok: false,
          message: this.getErrorMessage(err),
        };
      }
    },
    validateConfigureModule() {
      this.clearErrors(this);
      this.validationErrorDetails = [];
      let isValidationOk = true;

      if (!this.mail_module_uuid) {
        this.error.mail_module_uuid = this.$t("common.required");
        if (isValidationOk) {
          this.focusElement("mail_module_uuid");
          isValidationOk = false;
        }
      }

      if (this.portal_enabled) {
        const fqdnRe =
          /^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$/;
        if (!this.portal_hostname) {
          this.error.portal_hostname = this.$t("common.required");
          if (isValidationOk) {
            this.focusElement("portal_hostname");
            isValidationOk = false;
          }
        } else if (!fqdnRe.test(this.portal_hostname)) {
          this.error.portal_hostname = this.$t("settings.invalid_fqdn");
          if (isValidationOk) {
            this.focusElement("portal_hostname");
            isValidationOk = false;
          }
        }
      }

      if (this.rspamd_url && !/^https?:\/\/.+/i.test(this.rspamd_url)) {
        this.error.rspamd_url = this.$t("settings.invalid_rspamd_url");
        if (isValidationOk) {
          this.focusElement("rspamd_url");
          isValidationOk = false;
        }
      }

      const timeRe = /^([01]\d|2[0-3]):[0-5]\d$/;
      if (this.digest_time && !timeRe.test(this.digest_time)) {
        this.error.digest_time = this.$t("settings.invalid_digest_time");
        if (isValidationOk) {
          this.focusElement("digest_time");
          isValidationOk = false;
        }
      }

      const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (this.admin_alert_email && !emailRe.test(this.admin_alert_email)) {
        this.error.admin_alert_email = this.$t("mailboxes.invalid_email");
        if (isValidationOk) {
          this.focusElement("admin_alert_email");
          isValidationOk = false;
        }
      }

      return isValidationOk;
    },
    translateValidationError(errorCode) {
      if (!errorCode) {
        return this.$t("error.validation_error");
      }
      if (this.$te("settings." + errorCode)) {
        return this.$t("settings." + errorCode);
      }
      if (this.$te("error." + errorCode)) {
        return this.$t("error." + errorCode);
      }
      return errorCode;
    },
    configureModuleValidationFailed(validationErrors) {
      this.loading.configureModule = false;
      this.validationErrorDetails = [];
      let focusAlreadySet = false;

      for (const validationError of validationErrors || []) {
        const field = validationError.field || validationError.parameter;
        if (validationError.details) {
          const lines = String(validationError.details)
            .split("\n")
            .filter((d) => d.trim() !== "");
          this.validationErrorDetails.push(...lines);
        }
        if (
          field &&
          field !== "(root)" &&
          Object.prototype.hasOwnProperty.call(this.error, field)
        ) {
          this.error[field] = this.translateValidationError(
            validationError.error
          );
          if (!focusAlreadySet) {
            this.focusElement(field);
            focusAlreadySet = true;
          }
        } else if (validationError.error) {
          this.error.configureModule = this.translateValidationError(
            validationError.error
          );
        }
      }
    },
    async configureModule() {
      const isValidationOk = this.validateConfigureModule();
      if (!isValidationOk) {
        return;
      }

      this.loading.configureModule = true;
      this.error.configureModule = "";
      this.savedConfigurationNotice = "";
      const taskAction = "configure-module";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.configureModuleAborted
      );
      this.core.$root.$once(
        `${taskAction}-validation-failed-${eventId}`,
        this.configureModuleValidationFailed
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.configureModuleCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "configure-module",
          data: {
            mail_module_uuid: this.mail_module_uuid,
            portal_enabled: Boolean(this.portal_enabled),
            portal_hostname: this.portal_enabled ? this.portal_hostname : "",
            lets_encrypt: Boolean(this.lets_encrypt),
            http2https: Boolean(this.http2https),
            allowed_groups: this.portal_enabled
              ? this.allowed_groups.map((g) => g.trim()).filter(Boolean)
              : [],
            portal_tracking_enabled: Boolean(
              this.portal_enabled && this.portal_tracking_enabled
            ),
            milter_enabled: Boolean(this.milter_enabled),
            rspamd_url:
              this.rspamd_url ||
              this.effectiveDiscoveredRspamdUrl ||
              "http://127.0.0.1:11334",
            rspamd_user: this.rspamd_user || "",
            rspamd_password: this.clear_rspamd_password
              ? ""
              : this.rspamd_password || "",
            clear_rspamd_password: Boolean(this.clear_rspamd_password),
            rspamd_tls_verify: Boolean(this.rspamd_tls_verify),
            digest_enabled: Boolean(this.digest_enabled),
            digest_time: this.digest_time || "07:30",
            timezone: this.timezone || "Europe/Berlin",
            admin_alert_email: this.admin_alert_email || "",
          },
          extra: {
            title: this.$t("settings.configure_instance", {
              instance: this.instanceName,
            }),
            description: this.$t("common.processing"),
            eventId,
          },
        })
      );
      const err = res[0];

      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.configureModule = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
        this.loading.configureModule = false;
      }
    },
    configureModuleAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.configureModule = this.$t("error.generic_error");
      this.loading.configureModule = false;
    },
    configureModuleCompleted() {
      this.loading.configureModule = false;
      this.savedConfigurationNotice = this.$t("settings.saved_description");
      this.getConfiguration();
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.mg-bottom {
  margin-bottom: $spacing-06;
}

.mg-bottom-sm {
  margin-bottom: $spacing-04;
}

.mg-bottom-xs {
  margin-bottom: $spacing-02;
}

.mg-top-sm {
  margin-top: $spacing-04;
}

.maxwidth {
  max-width: 44rem;
}

.section-heading {
  margin-top: $spacing-05;
  margin-bottom: $spacing-04;
  padding-bottom: $spacing-02;
  border-bottom: 1px solid $ui-03;
}

.mail-discovery-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: $spacing-04;
}

.mail-select-col {
  flex: 1 1 22rem;
}

.domain-info-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: $spacing-02;
}

.rspamd-quick-btns {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.digest-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: $spacing-05;
}
</style>
