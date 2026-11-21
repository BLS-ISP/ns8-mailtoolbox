<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("connectors.title") }}</h2>
      </cv-column>
    </cv-row>

    <cv-row v-if="failingConnectors.length">
      <cv-column>
        <NsInlineNotification
          kind="warning"
          :title="
            $t('connectors.failing_warning_title', {
              count: failingConnectors.length,
            })
          "
          :description="$t('connectors.failing_warning_description')"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="error.connectorAction">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('error.error')"
          :description="error.connectorAction"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="connectorNotice">
      <cv-column>
        <NsInlineNotification
          kind="success"
          :title="$t('common.success')"
          :description="connectorNotice"
          @close="connectorNotice = ''"
        />
      </cv-column>
    </cv-row>

    <!-- NS8 IMAP-Sync Bridge Bar (shown ONLY when ns8-imapsync is installed on cluster) -->
    <cv-row v-if="imapsyncInstalled" class="mg-bottom">
      <cv-column>
        <cv-tile light class="bridge-tile">
          <div class="bridge-bar">
            <div class="bridge-info">
              <div class="bridge-title-row">
                <strong>{{ $t("connectors.imapsync_bridge_title") }}</strong>
                <NsTag
                  v-for="inst in activeImapsyncInstances"
                  :key="inst.module_id"
                  :label="formatInstanceBadge(inst)"
                  kind="purple"
                  size="sm"
                />
              </div>
              <div class="bridge-desc">
                {{ $t("connectors.imapsync_bridge_description") }}
              </div>
              <cv-checkbox
                v-if="uncoupledInstancesCount > 0"
                value="showUncoupledImapsync"
                :label="
                  $t('connectors.show_uncoupled_imapsync', {
                    count: uncoupledInstancesCount,
                  })
                "
                v-model="showUncoupledImapsync"
                @change="onToggleUncoupledImapsync"
                class="mg-top-xs"
              />
            </div>
            <div class="bridge-actions">
              <NsButton
                v-for="inst in activeImapsyncInstances"
                :key="'open-' + inst.module_id"
                type="button"
                kind="tertiary"
                size="small"
                :icon="Launch20"
                @click="openImapsyncModule(inst.module_id)"
              >
                {{
                  $t("connectors.open_imapsync_app", {
                    name: inst.ui_name || inst.module_id,
                  })
                }}
              </NsButton>
            </div>
          </div>
        </cv-tile>
      </cv-column>
    </cv-row>

    <cv-row>
      <cv-column>
        <cv-tile light>
          <div class="toolbar-top">
            <div class="toolbar-left">
              <NsButton
                kind="primary"
                size="field"
                :icon="Add20"
                @click="openCreateModal"
              >
                {{ $t("connectors.add_connector") }}
              </NsButton>

              <div
                v-if="imapsyncInstalled"
                class="source-filters"
                role="group"
                :aria-label="$t('connectors.source_filter_aria')"
              >
                <cv-button
                  v-for="opt in sourceFilterOptions"
                  :key="opt.value"
                  size="small"
                  :kind="sourceFilter === opt.value ? 'primary' : 'ghost'"
                  @click="sourceFilter = opt.value"
                >
                  {{ opt.label }} ({{ opt.count }})
                </cv-button>
              </div>
            </div>

            <NsButton
              kind="secondary"
              size="field"
              :icon="Renew20"
              :loading="loading.listConnectors || loading.imapsyncTasks"
              :disabled="loading.listConnectors || loading.imapsyncTasks"
              @click="reloadAll(true)"
            >
              {{ $t("common.refresh") }}
            </NsButton>
          </div>

          <NsDataTable
            :allRows="displayedConnectors"
            :columns="i18nTableColumns"
            :rawColumns="tableColumns"
            :isSearchable="true"
            :searchPlaceholder="$t('connectors.search_placeholder')"
            :searchClearLabel="
              coreT('common.clear_search', 'common.clear_search')
            "
            :noSearchResultsLabel="
              coreT('common.no_results', 'common.no_results')
            "
            :noSearchResultsDescription="
              coreT(
                'common.no_results_description',
                'common.no_results_description'
              )
            "
            :isLoading="loading.listConnectors"
            :skeletonRows="5"
            :isErrorShown="!!error.listConnectors"
            :errorTitle="$t('action.list-connectors')"
            :errorDescription="error.listConnectors"
            :itemsPerPageLabel="
              coreT('pagination.items_per_page', 'pagination.items_per_page')
            "
            :rangeOfTotalItemsLabel="
              coreT(
                'pagination.range_of_total_items',
                'pagination.range_of_total_items'
              )
            "
            :ofTotalPagesLabel="
              coreT('pagination.of_total_pages', 'pagination.of_total_pages')
            "
            :backwardText="
              coreT('pagination.previous_page', 'pagination.previous_page')
            "
            :forwardText="coreT('pagination.next_page', 'pagination.next_page')"
            :pageNumberLabel="
              coreT('pagination.page_number', 'pagination.page_number')
            "
            @updatePage="tablePage = $event"
          >
            <template slot="empty-state">
              <NsEmptyState :title="$t('connectors.empty_title')">
                <template #description>
                  <div>{{ $t("connectors.empty_description") }}</div>
                </template>
              </NsEmptyState>
            </template>
            <template slot="data">
              <cv-data-table-row
                v-for="(row, idx) in tablePage"
                :key="row.id || String(idx)"
                :value="String(idx)"
              >
                <!-- Name + Source Badge -->
                <cv-data-table-cell>
                  <div>
                    <strong>{{ row.name }}</strong>
                  </div>
                  <div
                    v-if="imapsyncInstalled || row.source === 'imapsync'"
                    class="sub-note"
                  >
                    <NsTag
                      v-if="row.source === 'imapsync'"
                      :label="
                        $t('connectors.source_imapsync_instance', {
                          instance: row.imapsync_module_id,
                        })
                      "
                      kind="purple"
                      size="sm"
                    />
                    <NsTag
                      v-else
                      :label="$t('connectors.source_toolbox')"
                      kind="blue"
                      size="sm"
                    />
                  </div>
                </cv-data-table-cell>

                <!-- Remote Account & Protocol -->
                <cv-data-table-cell>
                  <div>{{ row.remote_user || "-" }}</div>
                  <div class="sub-note">
                    <NsTag
                      :label="String(row.protocol || 'imaps').toUpperCase()"
                      kind="cyan"
                      size="sm"
                    />
                    <span>{{ row.host }}:{{ row.port }}</span>
                  </div>
                </cv-data-table-cell>

                <!-- Target Local Mailbox -->
                <cv-data-table-cell>
                  <div>{{ row.target_email || row.target_user }}</div>
                  <div
                    v-if="
                      row.target_user && row.target_email !== row.target_user
                    "
                    class="sub-note"
                  >
                    {{ row.target_user }}
                  </div>
                </cv-data-table-cell>

                <!-- Schedule & Enabled -->
                <cv-data-table-cell>
                  <template v-if="row.source === 'imapsync'">
                    <div>{{ formatImapsyncScheduleLabel(row.schedule) }}</div>
                    <NsTag
                      :label="
                        row.service_running
                          ? $t('connectors.imapsync_running')
                          : row.schedule && row.schedule.type !== 'manual'
                          ? $t('common.enabled')
                          : $t('connectors.imapsync_manual')
                      "
                      :kind="
                        row.service_running
                          ? 'teal'
                          : row.schedule && row.schedule.type !== 'manual'
                          ? 'green'
                          : 'cool-gray'
                      "
                      size="sm"
                    />
                  </template>
                  <template v-else>
                    <div>
                      {{
                        $t("connectors.every_n_min", {
                          min: row.interval_minutes || 5,
                        })
                      }}
                    </div>
                    <NsTag
                      :label="
                        row.enabled
                          ? $t('common.enabled')
                          : $t('common.disabled')
                      "
                      :kind="row.enabled ? 'teal' : 'gray'"
                      size="sm"
                    />
                  </template>
                </cv-data-table-cell>

                <!-- Sync Health Status -->
                <cv-data-table-cell>
                  <NsTag
                    :label="formatConnectorStatus(row)"
                    :kind="connectorStatusKind(row.last_status)"
                    size="sm"
                  />
                  <div v-if="row.last_sync_at" class="sub-note">
                    {{ row.last_sync_at }}
                  </div>
                  <div
                    v-if="row.last_status === 'error' && row.last_error"
                    class="err-note"
                  >
                    {{ row.last_error }}
                  </div>
                </cv-data-table-cell>

                <!-- Fetched Total / Sync Mode -->
                <cv-data-table-cell>
                  <template v-if="row.source === 'imapsync'">
                    <div class="mode-tags">
                      <NsTag
                        :label="$t('connectors.imapsync_mode_sync')"
                        kind="cool-gray"
                        size="sm"
                      />
                      <NsTag
                        v-if="row.sieve_enabled"
                        :label="$t('connectors.imapsync_mode_sieve')"
                        kind="cyan"
                        size="sm"
                      />
                      <NsTag
                        :label="formatImapsyncRetention(row)"
                        kind="gray"
                        size="sm"
                      />
                    </div>
                  </template>
                  <template v-else>
                    {{ row.messages_fetched_total || 0 }}
                  </template>
                </cv-data-table-cell>

                <!-- Actions -->
                <cv-data-table-cell>
                  <div v-if="row.source === 'imapsync'" class="row-actions">
                    <NsButton
                      v-if="!row.service_running"
                      kind="ghost"
                      size="small"
                      :icon="Play20"
                      :disabled="togglingTaskKey === row.id"
                      @click="toggleImapsyncTask(row, true)"
                    >
                      {{ $t("connectors.imapsync_start_btn") }}
                    </NsButton>
                    <NsButton
                      v-else
                      kind="ghost"
                      size="small"
                      :icon="Stop20"
                      :disabled="togglingTaskKey === row.id"
                      @click="toggleImapsyncTask(row, false)"
                    >
                      {{ $t("connectors.imapsync_stop_btn") }}
                    </NsButton>
                    <NsButton
                      kind="ghost"
                      size="small"
                      :icon="Document20"
                      :disabled="
                        row.service_running ||
                        (!row.has_log && !row.last_sync_timestamp)
                      "
                      @click="openImapsyncLogModal(row)"
                    >
                      {{ $t("connectors.imapsync_log_btn") }}
                    </NsButton>
                    <NsButton
                      kind="ghost"
                      size="small"
                      :icon="Launch20"
                      @click="openImapsyncModule(row.imapsync_module_id)"
                    >
                      {{ $t("connectors.imapsync_open_btn") }}
                    </NsButton>
                  </div>
                  <div v-else class="row-actions">
                    <NsButton
                      kind="ghost"
                      size="small"
                      :icon="Play20"
                      :disabled="syncingId === row.id"
                      @click="syncConnectorNow(row)"
                    >
                      {{ $t("connectors.sync_now_btn") }}
                    </NsButton>
                    <NsButton
                      kind="ghost"
                      size="small"
                      :icon="Edit20"
                      @click="openEditModal(row)"
                    >
                      {{ $t("mailboxes.edit_action") }}
                    </NsButton>
                    <NsButton
                      kind="danger--ghost"
                      size="small"
                      :icon="TrashCan20"
                      :disabled="deletingId === row.id"
                      @click="deleteConnector(row)"
                    >
                      {{ $t("signatures.delete_action") }}
                    </NsButton>
                  </div>
                </cv-data-table-cell>
              </cv-data-table-row>
            </template>
          </NsDataTable>
        </cv-tile>
      </cv-column>
    </cv-row>

    <EditConnectorModal
      :visible="isModalVisible"
      :connector="selectedConnector"
      :mailboxes="mailboxes"
      @close="isModalVisible = false"
      @saved="onConnectorSaved"
    />

    <ImapsyncLogModal
      :visible="isLogModalVisible"
      :task="selectedImapsyncTask"
      :loading="loading.imapsyncLog"
      :logContent="imapsyncLogContent"
      :truncated="imapsyncLogTruncated"
      :errorMessage="error.imapsyncLog"
      @close="isLogModalVisible = false"
      @refresh="fetchSelectedImapsyncLog"
    />
  </cv-grid>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import {
  QueryParamService,
  TaskService,
  UtilService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";
import EditConnectorModal from "../components/EditConnectorModal.vue";
import ImapsyncLogModal from "../components/ImapsyncLogModal.vue";
import {
  normalizeImapsyncTask,
  extractImapsyncModulesFromCluster,
  navigateToCockpitApp,
} from "../utils/imapsyncBridge";
import Add20 from "@carbon/icons-vue/es/add/20";
import Renew20 from "@carbon/icons-vue/es/renew/20";
import Play20 from "@carbon/icons-vue/es/play/20";
import Stop20 from "@carbon/icons-vue/es/stop/20";
import Document20 from "@carbon/icons-vue/es/document/20";
import Launch20 from "@carbon/icons-vue/es/launch/20";
import Edit20 from "@carbon/icons-vue/es/edit/20";
import TrashCan20 from "@carbon/icons-vue/es/trash-can/20";

export default {
  name: "Connectors",
  components: {
    EditConnectorModal,
    ImapsyncLogModal,
  },
  mixins: [TaskService, QueryParamService, UtilService, PageTitleService],
  pageTitle() {
    return this.$t("connectors.title") + " - " + this.appName;
  },
  data() {
    return {
      Add20,
      Renew20,
      Play20,
      Stop20,
      Document20,
      Launch20,
      Edit20,
      TrashCan20,
      q: {
        page: "connectors",
      },
      urlCheckInterval: null,
      connectors: [],
      imapsyncInstances: [],
      showUncoupledImapsync: false,
      sourceFilter: "all",
      mailboxes: [],
      tablePage: [],
      tableColumns: [
        "name",
        "remote_user",
        "target_email",
        "interval_minutes",
        "last_status",
        "imapsync_module_id",
        "protocol",
      ],
      selectedConnector: null,
      isModalVisible: false,
      selectedImapsyncTask: null,
      isLogModalVisible: false,
      imapsyncLogContent: "",
      imapsyncLogTruncated: false,
      syncingId: null,
      deletingId: null,
      togglingTaskKey: "",
      connectorNotice: "",
      loading: {
        listConnectors: false,
        listMailboxes: false,
        imapsyncTasks: false,
        imapsyncLog: false,
      },
      error: {
        listConnectors: "",
        connectorAction: "",
        imapsyncLog: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    imapsyncInstalled() {
      return this.imapsyncInstances.length > 0;
    },
    uncoupledInstancesCount() {
      return this.imapsyncInstances.filter((inst) => inst.coupled === false)
        .length;
    },
    activeImapsyncInstances() {
      return this.imapsyncInstances.filter(
        (inst) => inst.coupled !== false || this.showUncoupledImapsync
      );
    },
    nativeConnectorRows() {
      return this.connectors.map((c) => ({
        ...c,
        source: "toolbox",
        imapsync_module_id: "",
      }));
    },
    imapsyncConnectorRows() {
      const rows = [];
      for (const inst of this.activeImapsyncInstances) {
        const taskList = Array.isArray(inst.tasks) ? inst.tasks : [];
        for (const rawTask of taskList) {
          rows.push(normalizeImapsyncTask(rawTask, inst, this.mailboxes));
        }
      }
      return rows;
    },
    allUnifiedRows() {
      return [...this.nativeConnectorRows, ...this.imapsyncConnectorRows];
    },
    displayedConnectors() {
      if (this.sourceFilter === "toolbox") {
        return this.nativeConnectorRows;
      }
      if (this.sourceFilter === "imapsync") {
        return this.imapsyncConnectorRows;
      }
      return this.allUnifiedRows;
    },
    sourceFilterOptions() {
      return [
        {
          value: "all",
          label: this.$t("connectors.filter_all"),
          count: this.allUnifiedRows.length,
        },
        {
          value: "toolbox",
          label: this.$t("connectors.source_toolbox"),
          count: this.nativeConnectorRows.length,
        },
        {
          value: "imapsync",
          label: this.$t("connectors.source_imapsync"),
          count: this.imapsyncConnectorRows.length,
        },
      ];
    },
    failingConnectors() {
      return this.allUnifiedRows.filter((c) => c.last_status === "error");
    },
    i18nTableColumns() {
      return [
        this.$t("connectors.col_name"),
        this.$t("connectors.col_remote"),
        this.$t("connectors.col_target"),
        this.$t("connectors.col_interval"),
        this.$t("mailboxes.col_status"),
        this.$t("connectors.col_fetched"),
        this.$t("mailboxes.col_actions"),
      ];
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
    this.reloadAll(false);
  },
  methods: {
    coreT(coreKey, fallbackKey) {
      if (this.core && typeof this.core.$t === "function") {
        return this.core.$t(coreKey);
      }
      return this.$t(fallbackKey);
    },
    runExternalImapsyncTask(
      targetModuleId,
      externalAction,
      payloadData,
      extra
    ) {
      const mergedExtra = {
        isNotificationHidden: true,
        ...(extra || {}),
      };
      const taskSpec = payloadData
        ? { action: externalAction, data: payloadData, extra: mergedExtra }
        : { action: externalAction, extra: mergedExtra };
      return this.createModuleTaskForApp(targetModuleId, taskSpec);
    },
    formatInstanceBadge(inst) {
      const count = Array.isArray(inst.tasks) ? inst.tasks.length : 0;
      const label =
        inst.ui_name && inst.ui_name !== inst.module_id
          ? `${inst.ui_name} (${inst.module_id})`
          : inst.module_id;
      return `${label}: ${count}`;
    },
    openImapsyncModule(moduleId) {
      navigateToCockpitApp(this.core, moduleId, "tasks");
    },
    onToggleUncoupledImapsync() {
      if (this.showUncoupledImapsync) {
        this.refreshMissingImapsyncTasks();
      }
    },
    formatImapsyncScheduleLabel(schedule) {
      if (!schedule || schedule.type === "manual") {
        return this.$t("connectors.imapsync_manual");
      }
      if (schedule.type === "hours") {
        return this.$t("connectors.every_n_hours", { hr: schedule.value });
      }
      return this.$t("connectors.every_n_min", { min: schedule.value });
    },
    formatImapsyncRetention(row) {
      if (row.delete_local) {
        return this.$t("connectors.imapsync_mode_clone");
      }
      if (row.delete_remote && row.delete_remote_older > 0) {
        return this.$t("connectors.imapsync_mode_delete_older", {
          days: row.delete_remote_older,
        });
      }
      if (row.delete_remote) {
        return this.$t("connectors.imapsync_mode_delete_remote");
      }
      return this.$t("connectors.imapsync_mode_keep_remote");
    },
    connectorStatusKind(status) {
      if (status === "running") {
        return "teal";
      }
      if (status === "ok") {
        return "green";
      }
      if (status === "error") {
        return "red";
      }
      return "cool-gray";
    },
    formatConnectorStatus(row) {
      if (row.source === "imapsync") {
        if (row.last_status === "running") {
          return this.$t("connectors.imapsync_running");
        }
        if (row.last_status === "ok") {
          return this.$t("connectors.status_ok");
        }
        if (row.last_status === "error") {
          return this.$t("connectors.imapsync_status_exit_error", {
            code: row.last_sync_exit_code,
          });
        }
        return this.$t("connectors.status_pending");
      }
      if (row.last_status === "ok") {
        return this.$t("connectors.status_ok");
      }
      if (row.last_status === "error") {
        return this.$t("connectors.status_error", {
          failures: row.consecutive_failures || 1,
        });
      }
      return this.$t("connectors.status_pending");
    },
    reloadAll(forceRefresh = false) {
      this.listConnectors(forceRefresh === true);
      this.listMailboxes();
    },
    async listConnectors(forceRefresh = false) {
      this.loading.listConnectors = true;
      this.error.listConnectors = "";
      const taskAction = "list-connectors";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.error.listConnectors = this.$t("error.generic_error");
        this.loading.listConnectors = false;
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const out = (taskResult && taskResult.output) || {};
          this.connectors = Array.isArray(out.connectors) ? out.connectors : [];
          this.loading.listConnectors = false;

          if (
            out.imapsync_bridge &&
            Array.isArray(out.imapsync_bridge.instances) &&
            out.imapsync_bridge.instances.length > 0
          ) {
            this.imapsyncInstances = out.imapsync_bridge.instances.map(
              (inst) => ({
                ...inst,
                tasks: Array.isArray(inst.tasks) ? inst.tasks : [],
              })
            );
            this.refreshMissingImapsyncTasks();
          } else if (
            !out.imapsync_bridge ||
            out.imapsync_bridge.installed === undefined
          ) {
            this.discoverImapsyncViaCluster();
          } else {
            this.imapsyncInstances = [];
          }
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "list-connectors",
          ...(forceRefresh === true ? { data: { force_refresh: true } } : {}),
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.error.listConnectors = this.getErrorMessage(err);
        this.loading.listConnectors = false;
      }
    },
    async discoverImapsyncViaCluster() {
      const taskAction = "list-installed-modules";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {});
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const discovered = extractImapsyncModulesFromCluster(
            taskResult && taskResult.output
          );
          if (discovered.length > 0) {
            this.imapsyncInstances = discovered;
            this.refreshMissingImapsyncTasks();
          }
        }
      );

      await to(
        this.createClusterTaskForApp({
          action: "list-installed-modules",
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
    },
    refreshMissingImapsyncTasks() {
      const targets = this.imapsyncInstances.filter(
        (inst) =>
          !inst.tasks_loaded &&
          (Boolean(inst.coupled) || this.showUncoupledImapsync)
      );
      for (const inst of targets) {
        this.fetchImapsyncInstanceTasks(inst.module_id);
      }
    },
    async fetchImapsyncInstanceTasks(imapsyncModuleId) {
      if (!imapsyncModuleId) {
        return;
      }
      this.loading.imapsyncTasks = true;
      const extAction = ["list", "tasks"].join("-");
      const eventId = this.getUuid();

      this.core.$root.$once(`${extAction}-aborted-${eventId}`, () => {
        this.loading.imapsyncTasks = false;
      });
      this.core.$root.$once(
        `${extAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const output = (taskResult && taskResult.output) || {};
          const userProps = Array.isArray(output.user_properties)
            ? output.user_properties
            : [];
          this.imapsyncInstances = this.imapsyncInstances.map((inst) =>
            inst.module_id === imapsyncModuleId
              ? { ...inst, tasks_loaded: true, tasks: userProps }
              : inst
          );
          this.loading.imapsyncTasks = false;
        }
      );

      const res = await to(
        this.runExternalImapsyncTask(imapsyncModuleId, extAction, null, {
          title: this.$t("action." + extAction),
          isNotificationHidden: true,
          eventId,
        })
      );
      if (res[0]) {
        this.loading.imapsyncTasks = false;
      }
    },
    async toggleImapsyncTask(row, shouldStart) {
      if (!row || !row.imapsync_module_id) {
        return;
      }
      this.togglingTaskKey = row.id;
      this.error.connectorAction = "";
      const extAction = [shouldStart ? "start" : "stop", "task"].join("-");
      const eventId = this.getUuid();

      this.core.$root.$once(`${extAction}-aborted-${eventId}`, () => {
        this.togglingTaskKey = "";
        this.error.connectorAction = this.$t("error.generic_error");
      });
      this.core.$root.$once(`${extAction}-completed-${eventId}`, () => {
        this.togglingTaskKey = "";
        this.connectorNotice = shouldStart
          ? this.$t("connectors.imapsync_started_notice", { name: row.name })
          : this.$t("connectors.imapsync_stopped_notice", { name: row.name });
        this.fetchImapsyncInstanceTasks(row.imapsync_module_id);
      });

      const res = await to(
        this.runExternalImapsyncTask(
          row.imapsync_module_id,
          extAction,
          {
            task_id: row.task_id,
            localuser: row.localuser,
          },
          {
            title: this.$t("action." + extAction),
            description: row.name,
            isNotificationHidden: true,
            eventId,
          }
        )
      );
      const err = res[0];
      if (err) {
        this.togglingTaskKey = "";
        this.error.connectorAction = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: extAction })
        );
      }
    },
    openImapsyncLogModal(row) {
      this.selectedImapsyncTask = row;
      this.imapsyncLogContent = "";
      this.imapsyncLogTruncated = false;
      this.error.imapsyncLog = "";
      this.isLogModalVisible = true;
      this.fetchSelectedImapsyncLog();
    },
    async fetchSelectedImapsyncLog() {
      const row = this.selectedImapsyncTask;
      if (!row || !row.imapsync_module_id) {
        return;
      }
      this.loading.imapsyncLog = true;
      this.error.imapsyncLog = "";
      const extAction = ["get", "log"].join("-");
      const eventId = this.getUuid();

      this.core.$root.$once(`${extAction}-aborted-${eventId}`, () => {
        this.loading.imapsyncLog = false;
        this.error.imapsyncLog = this.$t("error.generic_error");
      });
      this.core.$root.$once(
        `${extAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.loading.imapsyncLog = false;
          const out = (taskResult && taskResult.output) || {};
          this.imapsyncLogContent = out.log_content || "";
          this.imapsyncLogTruncated = Boolean(out.truncated);
        }
      );

      const res = await to(
        this.runExternalImapsyncTask(
          row.imapsync_module_id,
          extAction,
          {
            task_id: row.task_id,
            localuser: row.localuser,
          },
          {
            title: this.$t("action." + extAction),
            isNotificationHidden: true,
            eventId,
          }
        )
      );
      const err = res[0];
      if (err) {
        this.loading.imapsyncLog = false;
        this.error.imapsyncLog = this.getErrorMessage(err);
      }
    },
    async listMailboxes() {
      this.loading.listMailboxes = true;
      const taskAction = "list-mailboxes";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.listMailboxes = false;
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const out = (taskResult && taskResult.output) || {};
          this.mailboxes = Array.isArray(out.mailboxes) ? out.mailboxes : [];
          this.loading.listMailboxes = false;
        }
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
      if (res[0]) {
        this.loading.listMailboxes = false;
      }
    },
    openCreateModal() {
      this.selectedConnector = null;
      this.isModalVisible = true;
    },
    openEditModal(row) {
      this.selectedConnector = row;
      this.isModalVisible = true;
    },
    onConnectorSaved() {
      this.isModalVisible = false;
      this.connectorNotice = this.$t("connectors.saved_notice");
      this.listConnectors(false);
    },
    async syncConnectorNow(row) {
      this.syncingId = row.id;
      this.error.connectorAction = "";
      const taskAction = "sync-connector-now";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.syncingId = null;
        this.error.connectorAction = this.$t("error.generic_error");
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.syncingId = null;
          const out = (taskResult && taskResult.output) || {};
          if (out.ok === false) {
            this.error.connectorAction =
              out.error || this.$t("error.generic_error");
          } else {
            this.connectorNotice = this.$t("connectors.synced_notice", {
              name: row.name,
              fetched: out.fetched || 0,
            });
          }
          this.listConnectors(false);
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "sync-connector-now",
          data: {
            id: Number(row.id),
          },
          extra: {
            title: this.$t("action." + taskAction),
            description: row.name,
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.syncingId = null;
        this.error.connectorAction = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
      }
    },
    async deleteConnector(row) {
      this.deletingId = row.id;
      this.error.connectorAction = "";
      const taskAction = "delete-connector";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.deletingId = null;
        this.error.connectorAction = this.$t("error.generic_error");
      });
      this.core.$root.$once(`${taskAction}-completed-${eventId}`, () => {
        this.deletingId = null;
        this.connectorNotice = this.$t("connectors.deleted_notice", {
          name: row.name,
        });
        this.listConnectors(false);
      });

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "delete-connector",
          data: {
            id: Number(row.id),
          },
          extra: {
            title: this.$t("action." + taskAction),
            description: row.name,
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.deletingId = null;
        this.error.connectorAction = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
      }
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.mg-bottom {
  margin-bottom: $spacing-05;
}

.mg-top-xs {
  margin-top: $spacing-02;
}

.bridge-tile {
  border-left: 3px solid #8a3ffc;
}

.bridge-bar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: $spacing-04;
}

.bridge-title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: $spacing-02;
}

.bridge-desc {
  font-size: 0.8125rem;
  color: $text-02;
  margin-top: $spacing-01;
}

.bridge-actions {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.toolbar-top {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: $spacing-04;
  margin-bottom: $spacing-05;
}

.toolbar-left {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: $spacing-04;
}

.source-filters {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.sub-note {
  font-size: 0.75rem;
  color: $text-02;
  margin-top: 0.125rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: $spacing-02;
}

.mode-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.err-note {
  font-size: 0.75rem;
  color: #da1e28;
  margin-top: 0.25rem;
  max-width: 16rem;
  word-break: break-word;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}
</style>
