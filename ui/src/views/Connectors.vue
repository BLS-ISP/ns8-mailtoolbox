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

    <cv-row>
      <cv-column>
        <cv-tile light>
          <div class="toolbar-top">
            <NsButton
              kind="primary"
              size="field"
              :icon="Add20"
              @click="openCreateModal"
            >
              {{ $t("connectors.add_connector") }}
            </NsButton>
            <NsButton
              kind="secondary"
              size="field"
              :icon="Renew20"
              :loading="loading.listConnectors"
              :disabled="loading.listConnectors"
              @click="reloadAll"
            >
              {{ $t("common.refresh") }}
            </NsButton>
          </div>

          <NsDataTable
            :allRows="connectors"
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
                <!-- Name -->
                <cv-data-table-cell>
                  <strong>{{ row.name }}</strong>
                </cv-data-table-cell>

                <!-- Remote Account & Protocol -->
                <cv-data-table-cell>
                  <div>{{ row.remote_user }}</div>
                  <div class="sub-note">
                    <NsTag
                      :label="(row.protocol || 'imaps').toUpperCase()"
                      kind="cyan"
                      size="sm"
                    />
                    <span>{{ row.host }}:{{ row.port }}</span>
                  </div>
                </cv-data-table-cell>

                <!-- Target Local Mailbox -->
                <cv-data-table-cell>
                  <div>{{ row.target_email }}</div>
                  <div class="sub-note">{{ row.target_user }}</div>
                </cv-data-table-cell>

                <!-- Interval & Enabled -->
                <cv-data-table-cell>
                  <div>
                    {{
                      $t("connectors.every_n_min", {
                        min: row.interval_minutes || 5,
                      })
                    }}
                  </div>
                  <NsTag
                    :label="
                      row.enabled ? $t('common.enabled') : $t('common.disabled')
                    "
                    :kind="row.enabled ? 'teal' : 'gray'"
                    size="sm"
                  />
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

                <!-- Fetched Total -->
                <cv-data-table-cell>
                  {{ row.messages_fetched_total || 0 }}
                </cv-data-table-cell>

                <!-- Actions -->
                <cv-data-table-cell>
                  <div class="row-actions">
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
import Add20 from "@carbon/icons-vue/es/add/20";
import Renew20 from "@carbon/icons-vue/es/renew/20";
import Play20 from "@carbon/icons-vue/es/play/20";
import Edit20 from "@carbon/icons-vue/es/edit/20";
import TrashCan20 from "@carbon/icons-vue/es/trash-can/20";

export default {
  name: "Connectors",
  components: {
    EditConnectorModal,
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
      Edit20,
      TrashCan20,
      q: {
        page: "connectors",
      },
      urlCheckInterval: null,
      connectors: [],
      mailboxes: [],
      tablePage: [],
      tableColumns: [
        "name",
        "remote_user",
        "target_email",
        "interval_minutes",
        "last_status",
        "messages_fetched_total",
        "actions",
      ],
      selectedConnector: null,
      isModalVisible: false,
      syncingId: null,
      deletingId: null,
      connectorNotice: "",
      loading: {
        listConnectors: false,
        listMailboxes: false,
      },
      error: {
        listConnectors: "",
        connectorAction: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    failingConnectors() {
      return this.connectors.filter((c) => c.last_status === "error");
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
    this.reloadAll();
  },
  methods: {
    coreT(coreKey, fallbackKey) {
      if (this.core && typeof this.core.$t === "function") {
        return this.core.$t(coreKey);
      }
      return this.$t(fallbackKey);
    },
    connectorStatusKind(status) {
      if (status === "ok") {
        return "green";
      }
      if (status === "error") {
        return "red";
      }
      return "cool-gray";
    },
    formatConnectorStatus(row) {
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
    reloadAll() {
      this.listConnectors();
      this.listMailboxes();
    },
    async listConnectors() {
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
      const err = res[0];
      if (err) {
        this.error.listConnectors = this.getErrorMessage(err);
        this.loading.listConnectors = false;
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
      this.listConnectors();
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
          this.listConnectors();
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
        this.listConnectors();
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

.toolbar-top {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: $spacing-04;
  margin-bottom: $spacing-05;
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
