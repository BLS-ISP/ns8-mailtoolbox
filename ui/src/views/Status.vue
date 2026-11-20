<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("status.title") }}</h2>
      </cv-column>
    </cv-row>

    <cv-row v-if="error.getStatus">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-status')"
          :description="error.getStatus"
          :showCloseButton="false"
        />
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

    <cv-row v-if="error.listMailboxes">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.list-mailboxes')"
          :description="error.listMailboxes"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="error.listBackupRepositories">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.list-backup-repositories')"
          :description="error.listBackupRepositories"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="error.listBackups">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.list-backups')"
          :description="error.listBackups"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="!loading.getConfiguration && !configuration.configured">
      <cv-column>
        <NsInlineNotification
          kind="warning"
          :title="$t('status.unconfigured_title')"
          :description="$t('status.unconfigured_description')"
          :actionLabel="$t('status.go_to_settings')"
          @action="goToAppPage(instanceName, 'settings')"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="failingConnectorsCount > 0">
      <cv-column>
        <NsInlineNotification
          kind="warning"
          :title="
            $t('connectors.failing_warning_title', {
              count: failingConnectorsCount,
            })
          "
          :description="$t('connectors.failing_warning_description')"
          :actionLabel="$t('status.manage_connectors_link')"
          @action="goToAppPage(instanceName, 'connectors')"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <!-- Mail Toolbox Domain, Secretariat, Milter & Connector Summary Cards -->
    <cv-row>
      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="
            configuration.configured && configuration.mail_module_id
              ? configuration.mail_module_id
              : $t('status.not_configured')
          "
          :description="$t('status.coupled_mail_server')"
          :icon="Email32"
          :loading="loading.getConfiguration"
          class="min-height-card"
        >
          <template slot="content">
            <div class="card-extra-content">
              <div v-if="configuration.configured && configuration.user_domain">
                <span class="meta-label">{{ $t("status.user_domain") }}:</span>
                <NsTag
                  :label="configuration.user_domain"
                  kind="cyan"
                  size="sm"
                  class="inline-tag"
                />
              </div>
              <div v-else class="muted-note">
                {{ $t("status.couple_mail_hint") }}
              </div>
            </div>
          </template>
        </NsInfoCard>
      </cv-column>

      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="
            configuration.configured && configuration.portal_hostname
              ? configuration.portal_hostname
              : $t('status.not_configured')
          "
          :description="$t('status.secretariat_portal')"
          :icon="Globe32"
          :loading="loading.getConfiguration"
          wrapTitle
          class="min-height-card"
        >
          <template slot="content">
            <div class="card-extra-content">
              <div
                v-if="configuration.configured && configuration.portal_hostname"
                class="portal-link-row"
              >
                <cv-link
                  :href="'https://' + configuration.portal_hostname"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="external-portal-link"
                >
                  <span>{{ $t("status.open_portal") }}</span>
                  <Launch16 class="launch-icon" />
                </cv-link>
              </div>
              <div
                v-if="
                  configuration.allowed_groups &&
                  configuration.allowed_groups.length
                "
                class="groups-wrap"
              >
                <span class="meta-label"
                  >{{ $t("status.allowed_groups") }}:</span
                >
                <div class="tags-list">
                  <NsTag
                    v-for="group in configuration.allowed_groups"
                    :key="group"
                    :label="group"
                    kind="blue"
                    size="sm"
                  />
                </div>
              </div>
              <div v-else class="muted-note">
                {{ $t("status.no_groups_configured") }}
              </div>
            </div>
          </template>
        </NsInfoCard>
      </cv-column>

      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="String(mailboxSummary.central_vacation_active)"
          :description="$t('status.absences_and_forwards')"
          :icon="Calendar32"
          :loading="loading.getConfiguration || loading.listMailboxes"
          class="min-height-card"
        >
          <template slot="content">
            <div class="card-extra-content">
              <div class="kpi-tags">
                <NsTag
                  :label="
                    $t('status.kpi_central', {
                      count: mailboxSummary.central_vacation_active,
                    })
                  "
                  kind="warm-gray"
                  size="sm"
                />
                <NsTag
                  :label="
                    $t('status.kpi_sogo', {
                      count: mailboxSummary.sogo_vacation_active,
                    })
                  "
                  kind="teal"
                  size="sm"
                />
                <NsTag
                  :label="
                    $t('status.kpi_forwards', {
                      count: mailboxSummary.forwards_active,
                    })
                  "
                  kind="purple"
                  size="sm"
                />
              </div>
              <div v-if="configuration.configured" class="portal-link-row">
                <cv-link @click="goToAppPage(instanceName, 'mailboxes')">
                  {{
                    $t("status.manage_mailboxes_link", {
                      total: mailboxSummary.total,
                    })
                  }}
                </cv-link>
              </div>
            </div>
          </template>
        </NsInfoCard>
      </cv-column>

      <!-- Phase 2: Signature Milter Card -->
      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="
            milterStatus.listening
              ? $t('signatures.milter_listening')
              : $t('signatures.milter_stopped')
          "
          :description="$t('status.signature_milter_card')"
          :icon="Pen32"
          :loading="loading.getConfiguration || loading.listSignatures"
          class="min-height-card"
        >
          <template slot="content">
            <div class="card-extra-content">
              <div class="kpi-tags">
                <NsTag
                  :label="
                    $t('signatures.milter_signed_count', {
                      count: milterStatus.messages_signed || 0,
                    })
                  "
                  kind="blue"
                  size="sm"
                />
                <NsTag
                  :label="
                    $t('status.signature_rules_count', {
                      count: signatureCount,
                    })
                  "
                  kind="cyan"
                  size="sm"
                />
              </div>
              <div v-if="configuration.configured" class="portal-link-row">
                <cv-link @click="goToAppPage(instanceName, 'signatures')">
                  {{ $t("status.manage_signatures_link") }}
                </cv-link>
              </div>
            </div>
          </template>
        </NsInfoCard>
      </cv-column>

      <!-- Phase 3c: External POP3/IMAP Connectors Card -->
      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="String(connectors.length)"
          :description="$t('status.connectors_card')"
          :icon="Connect32"
          :loading="loading.getConfiguration || loading.listConnectors"
          class="min-height-card"
        >
          <template slot="content">
            <div class="card-extra-content">
              <div class="kpi-tags">
                <NsTag
                  v-if="failingConnectorsCount > 0"
                  :label="
                    $t('status.connectors_error_badge', {
                      count: failingConnectorsCount,
                    })
                  "
                  kind="red"
                  size="sm"
                />
                <NsTag
                  v-else
                  :label="$t('status.connectors_healthy_badge')"
                  kind="green"
                  size="sm"
                />
              </div>
              <div v-if="configuration.configured" class="portal-link-row">
                <cv-link @click="goToAppPage(instanceName, 'connectors')">
                  {{ $t("status.manage_connectors_link") }}
                </cv-link>
              </div>
            </div>
          </template>
        </NsInfoCard>
      </cv-column>

      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="status.instance || instanceName || '-'"
          :description="$t('status.app_instance')"
          :icon="Application32"
          :loading="loading.getStatus"
          class="min-height-card"
        />
      </cv-column>

      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="installationNodeTitle"
          :titleTooltip="installationNodeTitleTooltip"
          :description="$t('status.installation_node')"
          :icon="Chip32"
          :loading="loading.getStatus"
          class="min-height-card"
        />
      </cv-column>

      <cv-column :md="4" :max="4">
        <NsBackupCard
          :title="coreT('backup.title', 'status.backup_title')"
          :noBackupMessage="
            coreT('backup.no_backup_configured', 'status.no_backup_configured')
          "
          :goToBackupLabel="coreT('backup.go_to_backup', 'status.go_to_backup')"
          :repositoryLabel="coreT('backup.repository', 'status.repository')"
          :statusLabel="coreT('common.status', 'status.title')"
          :statusSuccessLabel="coreT('common.success', 'common.success')"
          :statusNotRunLabel="
            coreT(
              'backup.backup_has_not_run_yet',
              'status.backup_has_not_run_yet'
            )
          "
          :statusErrorLabel="coreT('error.error', 'error.error')"
          :completedLabel="coreT('backup.completed', 'status.completed')"
          :durationLabel="coreT('backup.duration', 'status.duration')"
          :totalSizeLabel="coreT('backup.total_size', 'status.size')"
          :totalFileCountLabel="
            coreT('backup.total_file_count', 'status.total_file_count')
          "
          :backupDisabledLabel="coreT('common.disabled', 'common.disabled')"
          :showMoreLabel="coreT('common.show_more', 'common.show_more')"
          :multipleUncertainStatusLabel="
            coreT(
              'backup.some_backups_failed_or_are_pending',
              'status.some_backups_failed'
            )
          "
          :moduleId="instanceName"
          :moduleUiName="instanceLabel"
          :repositories="backupRepositories"
          :backups="backups"
          :loading="loading.listBackupRepositories || loading.listBackups"
          :coreContext="core"
          light
        />
      </cv-column>

      <cv-column v-if="core && core.$router" :md="4" :max="4">
        <NsSystemLogsCard
          :title="coreT('system_logs.card_title', 'status.system_logs')"
          :description="
            core && core.$t
              ? core.$t('system_logs.card_description', {
                  name: instanceLabel || instanceName,
                })
              : $t('status.system_logs_description', {
                  name: instanceLabel || instanceName,
                })
          "
          :buttonLabel="
            coreT('system_logs.card_button_label', 'status.open_logs')
          "
          :router="core.$router"
          context="module"
          :moduleId="instanceName"
          light
        />
      </cv-column>
    </cv-row>

    <!-- Services -->
    <cv-row>
      <cv-column class="page-subtitle">
        <h4>{{ $tc("status.services", 2) }}</h4>
      </cv-column>
    </cv-row>
    <cv-row v-if="!loading.getStatus">
      <cv-column v-if="!status.services || !status.services.length">
        <cv-tile light>
          <NsEmptyState :title="$t('status.no_services')" />
        </cv-tile>
      </cv-column>
      <cv-column
        v-else
        v-for="(service, index) in status.services"
        :key="service.name || index"
        :md="4"
        :max="4"
      >
        <NsSystemdServiceCard
          light
          class="min-height-card"
          :serviceName="service.name"
          :active="service.active"
          :failed="service.failed"
          :enabled="service.enabled"
          :icon="Cube32"
        />
      </cv-column>
    </cv-row>
    <cv-row v-else>
      <cv-column :md="4" :max="4">
        <cv-tile light>
          <cv-skeleton-text :paragraph="true" :line-count="4" />
        </cv-tile>
      </cv-column>
    </cv-row>

    <!-- Images -->
    <cv-row>
      <cv-column class="page-subtitle">
        <h4>{{ $tc("status.app_images", 2) }}</h4>
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <div v-if="!loading.getStatus">
            <NsEmptyState
              v-if="!status.images || !status.images.length"
              :title="$t('status.no_images')"
            />
            <cv-structured-list v-else>
              <template slot="headings">
                <cv-structured-list-heading>{{
                  $t("status.name")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.size")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.created")
                }}</cv-structured-list-heading>
              </template>
              <template slot="items">
                <cv-structured-list-item
                  v-for="(image, index) in status.images"
                  :key="image.name || index"
                >
                  <cv-structured-list-data class="break-word">{{
                    image.name
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>
                    <span
                      v-if="
                        image.size !== '' &&
                        image.size !== null &&
                        !isNaN(image.size)
                      "
                      >{{ image.size | byteFormat }}</span
                    >
                    <span v-else>{{ image.size || "-" }}</span>
                  </cv-structured-list-data>
                  <cv-structured-list-data class="break-word">{{
                    image.created
                  }}</cv-structured-list-data>
                </cv-structured-list-item>
              </template>
            </cv-structured-list>
          </div>
          <cv-skeleton-text v-else :paragraph="true" :line-count="5" />
        </cv-tile>
      </cv-column>
    </cv-row>

    <!-- Volumes -->
    <cv-row>
      <cv-column class="page-subtitle">
        <h4>{{ $tc("status.app_volumes", 2) }}</h4>
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <div v-if="!loading.getStatus">
            <NsEmptyState
              v-if="!status.volumes || !status.volumes.length"
              :title="$t('status.no_volumes')"
            />
            <cv-structured-list v-else>
              <template slot="headings">
                <cv-structured-list-heading>{{
                  $t("status.name")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.mount")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.created")
                }}</cv-structured-list-heading>
              </template>
              <template slot="items">
                <cv-structured-list-item
                  v-for="(volume, index) in status.volumes"
                  :key="volume.name || index"
                >
                  <cv-structured-list-data>{{
                    volume.name
                  }}</cv-structured-list-data>
                  <cv-structured-list-data class="break-word">{{
                    volume.mount
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>{{
                    volume.created
                  }}</cv-structured-list-data>
                </cv-structured-list-item>
              </template>
            </cv-structured-list>
          </div>
          <cv-skeleton-text v-else :paragraph="true" :line-count="5" />
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
  TaskService,
  IconService,
  UtilService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";
import Launch16 from "@carbon/icons-vue/es/launch/16";
import Application32 from "@carbon/icons-vue/es/application/32";
import Chip32 from "@carbon/icons-vue/es/chip/32";
import Cube32 from "@carbon/icons-vue/es/cube/32";
import Email32 from "@carbon/icons-vue/es/email/32";
import Globe32 from "@carbon/icons-vue/es/globe/32";
import Calendar32 from "@carbon/icons-vue/es/calendar/32";
import Pen32 from "@carbon/icons-vue/es/pen/32";
import Connect32 from "@carbon/icons-vue/es/connect/32";

export default {
  name: "Status",
  components: {
    Launch16,
  },
  mixins: [
    TaskService,
    QueryParamService,
    IconService,
    UtilService,
    PageTitleService,
  ],
  pageTitle() {
    return this.$t("status.title") + " - " + this.appName;
  },
  data() {
    return {
      Application32,
      Chip32,
      Cube32,
      Email32,
      Globe32,
      Calendar32,
      Pen32,
      Connect32,
      q: {
        page: "status",
      },
      urlCheckInterval: null,
      status: {
        instance: "",
        services: [],
        images: [],
        volumes: [],
      },
      configuration: {
        configured: false,
        mail_module_uuid: "",
        mail_module_id: "",
        user_domain: "",
        portal_hostname: "",
        lets_encrypt: false,
        http2https: true,
        allowed_groups: [],
      },
      mailboxSummary: {
        total: 0,
        central_vacation_active: 0,
        sogo_vacation_active: 0,
        forwards_active: 0,
      },
      signatureCount: 0,
      milterStatus: {
        enabled: true,
        listening: false,
        messages_signed: 0,
      },
      connectors: [],
      backupRepositories: [],
      backups: [],
      loading: {
        getStatus: false,
        getConfiguration: false,
        listMailboxes: false,
        listSignatures: false,
        listConnectors: false,
        listBackupRepositories: false,
        listBackups: false,
      },
      error: {
        getStatus: "",
        getConfiguration: "",
        listMailboxes: "",
        listBackupRepositories: "",
        listBackups: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "instanceLabel", "core", "appName"]),
    failingConnectorsCount() {
      return this.connectors.filter((c) => c.last_status === "error").length;
    },
    installationNodeTitle() {
      if (this.status && this.status.node) {
        if (this.status.node_ui_name) {
          return this.status.node_ui_name;
        }
        return this.$t("status.node") + " " + this.status.node;
      }
      return "-";
    },
    installationNodeTitleTooltip() {
      if (this.status && this.status.node_ui_name) {
        return this.$t("status.node") + " " + this.status.node;
      }
      return "";
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
    this.getStatus();
    this.getConfiguration();
    this.listBackupRepositories();
  },
  methods: {
    coreT(coreKey, fallbackKey) {
      if (this.core && typeof this.core.$t === "function") {
        return this.core.$t(coreKey);
      }
      return this.$t(fallbackKey);
    },
    async getStatus() {
      this.loading.getStatus = true;
      this.error.getStatus = "";
      const taskAction = "get-status";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getStatusAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getStatusCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "get-status",
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
        this.error.getStatus = this.getErrorMessage(err);
        this.loading.getStatus = false;
      }
    },
    getStatusAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getStatus = this.$t("error.generic_error");
      this.loading.getStatus = false;
    },
    getStatusCompleted(taskContext, taskResult) {
      this.status = taskResult.output;
      this.loading.getStatus = false;
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
      const output = taskResult.output || {};
      this.configuration = {
        ...this.configuration,
        ...output,
        allowed_groups: Array.isArray(output.allowed_groups)
          ? output.allowed_groups
          : [],
      };
      this.loading.getConfiguration = false;

      if (this.configuration.configured === true) {
        this.listMailboxes();
        this.listSignatures();
        this.listConnectors();
      }
    },
    async listMailboxes() {
      this.loading.listMailboxes = true;
      this.error.listMailboxes = "";
      const taskAction = "list-mailboxes";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.listMailboxesAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.listMailboxesCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "list-mailboxes",
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
        this.error.listMailboxes = this.getErrorMessage(err);
        this.loading.listMailboxes = false;
      }
    },
    listMailboxesAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.listMailboxes = this.$t("error.generic_error");
      this.loading.listMailboxes = false;
    },
    listMailboxesCompleted(taskContext, taskResult) {
      const output = taskResult.output || {};
      if (output.summary) {
        this.mailboxSummary = {
          total: output.summary.total || 0,
          central_vacation_active: output.summary.central_vacation_active || 0,
          sogo_vacation_active: output.summary.sogo_vacation_active || 0,
          forwards_active: output.summary.forwards_active || 0,
        };
      }
      this.loading.listMailboxes = false;
    },
    async listSignatures() {
      this.loading.listSignatures = true;
      const taskAction = "list-signatures";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.listSignatures = false;
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const out = (taskResult && taskResult.output) || {};
          const list = Array.isArray(out.signatures) ? out.signatures : [];
          this.signatureCount = list.filter((s) => s.enabled).length;
          if (out.milter_status) {
            this.milterStatus = {
              ...this.milterStatus,
              ...out.milter_status,
            };
          }
          this.loading.listSignatures = false;
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "list-signatures",
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      if (res[0]) {
        this.loading.listSignatures = false;
      }
    },
    async listConnectors() {
      this.loading.listConnectors = true;
      const taskAction = "list-connectors";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.listConnectors = false;
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const out = (taskResult && taskResult.output) || {};
          this.connectors = Array.isArray(out.connectors) ? out.connectors : [];
          this.loading.listConnectors = false;
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "list-connectors",
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      if (res[0]) {
        this.loading.listConnectors = false;
      }
    },
    async listBackupRepositories() {
      this.loading.listBackupRepositories = true;
      this.error.listBackupRepositories = "";
      const taskAction = "list-backup-repositories";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.listBackupRepositoriesAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.listBackupRepositoriesCompleted
      );

      const res = await to(
        this.createClusterTaskForApp({
          action: "list-backup-repositories",
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
        this.error.listBackupRepositories = this.getErrorMessage(err);
        this.loading.listBackupRepositories = false;
      }
    },
    listBackupRepositoriesAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.listBackupRepositories = this.$t("error.generic_error");
      this.loading.listBackupRepositories = false;
    },
    listBackupRepositoriesCompleted(taskContext, taskResult) {
      const repositories = (taskResult.output.repositories || []).sort(
        this.sortByProperty("name")
      );
      this.backupRepositories = repositories;
      this.loading.listBackupRepositories = false;
      this.listBackups();
    },
    async listBackups() {
      this.loading.listBackups = true;
      this.error.listBackups = "";
      const taskAction = "list-backups";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.listBackupsAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.listBackupsCompleted
      );

      const res = await to(
        this.createClusterTaskForApp({
          action: "list-backups",
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
        this.error.listBackups = this.getErrorMessage(err);
        this.loading.listBackups = false;
      }
    },
    listBackupsAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.listBackups = this.$t("error.generic_error");
      this.loading.listBackups = false;
    },
    listBackupsCompleted(taskContext, taskResult) {
      const backups = taskResult.output.backups || [];
      backups.sort(this.sortByProperty("name"));

      for (const backup of backups) {
        const repo = this.backupRepositories.find(
          (r) => r.id == backup.repository
        );
        if (repo) {
          backup.repoName = repo.name;
        }
      }
      this.backups = backups;
      this.loading.listBackups = false;
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.break-word {
  word-wrap: break-word;
  max-width: 30vw;
}

.card-extra-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  width: 100%;
  margin-top: $spacing-02;
}

.meta-label {
  font-size: 0.8125rem;
  color: $text-02;
  margin-right: $spacing-02;
}

.muted-note {
  font-size: 0.8125rem;
  color: $text-02;
}

.portal-link-row {
  margin-top: $spacing-02;
  margin-bottom: $spacing-02;
}

.external-portal-link {
  display: inline-flex;
  align-items: center;
  gap: $spacing-02;
}

.launch-icon {
  margin-left: $spacing-02;
}

.groups-wrap {
  margin-top: $spacing-02;
}

.tags-list,
.kpi-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: $spacing-02;
  margin-top: $spacing-02;
}

.inline-tag {
  vertical-align: middle;
}
</style>
