<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("mailboxes.title") }}</h2>
      </cv-column>
    </cv-row>

    <cv-row v-if="error.getTemplates">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-templates')"
          :description="error.getTemplates"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="error.setVacation">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.set-vacation')"
          :description="error.setVacation"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="savedNotice">
      <cv-column>
        <NsInlineNotification
          kind="success"
          :title="$t('mailboxes.saved_title')"
          :description="savedNotice"
          @close="savedNotice = ''"
        />
      </cv-column>
    </cv-row>

    <!-- Summary Strip -->
    <cv-row class="mg-bottom">
      <cv-column :md="2" :max="4">
        <cv-tile light class="kpi-tile">
          <div class="kpi-label">{{ $t("mailboxes.kpi_total") }}</div>
          <div class="kpi-value">{{ summary.total }}</div>
        </cv-tile>
      </cv-column>
      <cv-column :md="2" :max="4">
        <cv-tile light class="kpi-tile">
          <div class="kpi-label">{{ $t("mailboxes.kpi_central_active") }}</div>
          <div class="kpi-value">{{ summary.central_vacation_active }}</div>
        </cv-tile>
      </cv-column>
      <cv-column :md="2" :max="4">
        <cv-tile light class="kpi-tile">
          <div class="kpi-label">{{ $t("mailboxes.kpi_sogo_active") }}</div>
          <div class="kpi-value">{{ summary.sogo_vacation_active }}</div>
        </cv-tile>
      </cv-column>
      <cv-column :md="2" :max="4">
        <cv-tile light class="kpi-tile">
          <div class="kpi-label">{{ $t("mailboxes.kpi_forwards_active") }}</div>
          <div class="kpi-value">{{ summary.forwards_active }}</div>
        </cv-tile>
      </cv-column>
    </cv-row>

    <!-- Filter Bar + Data Table -->
    <cv-row>
      <cv-column>
        <cv-tile light>
          <div class="toolbar-top">
            <div
              class="status-filters"
              role="group"
              :aria-label="$t('mailboxes.filter_label')"
            >
              <cv-button
                v-for="filterOpt in statusFilterOptions"
                :key="filterOpt.value"
                size="small"
                :kind="statusFilter === filterOpt.value ? 'primary' : 'ghost'"
                @click="statusFilter = filterOpt.value"
              >
                {{ filterOpt.label }} ({{ filterOpt.count }})
              </cv-button>
            </div>
            <NsButton
              kind="secondary"
              size="field"
              :icon="Renew20"
              :loading="loading.listMailboxes"
              :disabled="loading.listMailboxes"
              @click="reloadAll(true)"
            >
              {{ $t("common.refresh") }}
            </NsButton>
          </div>

          <NsDataTable
            :allRows="statusFilteredMailboxes"
            :columns="i18nTableColumns"
            :rawColumns="tableColumns"
            :isSearchable="true"
            :searchPlaceholder="$t('mailboxes.search_placeholder')"
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
            :isLoading="loading.listMailboxes"
            :skeletonRows="6"
            :isErrorShown="!!error.listMailboxes"
            :errorTitle="$t('action.list-mailboxes')"
            :errorDescription="error.listMailboxes"
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
              <NsEmptyState :title="$t('mailboxes.empty_title')">
                <template #description>
                  <div>{{ $t("mailboxes.empty_description") }}</div>
                </template>
              </NsEmptyState>
            </template>
            <template slot="data">
              <cv-data-table-row
                v-for="(row, rowIndex) in tablePage"
                :key="row.user || String(rowIndex)"
                :value="String(rowIndex)"
              >
                <!-- Employee -->
                <cv-data-table-cell>
                  <div class="emp-name">{{ row.display_name || row.user }}</div>
                  <div class="emp-sub">{{ row.user }}</div>
                </cv-data-table-cell>

                <!-- Primary Mail & Aliases -->
                <cv-data-table-cell>
                  <div>{{ row.mail || "-" }}</div>
                  <div
                    v-if="row.aliases && row.aliases.length"
                    class="aliases-line"
                    :title="row.aliases.join(', ')"
                  >
                    +{{ row.aliases.length }}
                    {{ $tc("mailboxes.alias_count", row.aliases.length) }}:
                    {{ row.aliases.slice(0, 2).join(", ")
                    }}<span v-if="row.aliases.length > 2">...</span>
                  </div>
                </cv-data-table-cell>

                <!-- Department & AD/LDAP Groups -->
                <cv-data-table-cell>
                  <div>{{ row.department || "-" }}</div>
                  <div v-if="row.title" class="emp-sub">{{ row.title }}</div>
                  <div
                    v-if="row.groups && row.groups.length"
                    class="user-groups-wrap"
                    :title="row.groups.join(', ')"
                  >
                    <NsTag
                      v-for="grp in row.groups.slice(0, 3)"
                      :key="grp"
                      :label="grp"
                      kind="cool-gray"
                      size="sm"
                    />
                    <NsTag
                      v-if="row.groups.length > 3"
                      :label="'+' + (row.groups.length - 3)"
                      kind="gray"
                      size="sm"
                    />
                  </div>
                </cv-data-table-cell>

                <!-- Status Tags -->
                <cv-data-table-cell>
                  <div class="tags-cell">
                    <NsTag
                      v-if="
                        (!row.vacation || !row.vacation.enabled) &&
                        (!row.sogo_status || !row.sogo_status.vacation_enabled)
                      "
                      :label="$t('mailboxes.status_present')"
                      kind="green"
                      size="sm"
                    />
                    <NsTag
                      v-if="row.vacation && row.vacation.enabled"
                      :label="
                        row.vacation.active_now === false
                          ? $t('mailboxes.status_central_scheduled')
                          : $t('mailboxes.status_central_active')
                      "
                      kind="magenta"
                      size="sm"
                    />
                    <NsTag
                      v-if="row.sogo_status && row.sogo_status.vacation_enabled"
                      :label="$t('mailboxes.status_sogo_active')"
                      kind="blue"
                      size="sm"
                    />
                  </div>
                  <div
                    v-if="row.vacation && row.vacation.enabled"
                    class="period-note"
                  >
                    {{ formatPeriod(row.vacation) }}
                  </div>
                </cv-data-table-cell>

                <!-- Forwarding -->
                <cv-data-table-cell>
                  <div
                    v-if="
                      row.vacation &&
                      row.vacation.enabled &&
                      row.vacation.forward_enabled &&
                      row.vacation.forward_address
                    "
                    class="forward-entry"
                  >
                    <ArrowRight16 class="inline-arrow" />
                    <span>{{ row.vacation.forward_address }}</span>
                    <NsTag
                      :label="
                        row.vacation.keep_copy
                          ? $t('mailboxes.copy_badge')
                          : $t('mailboxes.redirect_badge')
                      "
                      kind="purple"
                      size="sm"
                    />
                  </div>
                  <div
                    v-if="
                      row.sogo_status &&
                      row.sogo_status.forward_enabled &&
                      row.sogo_status.forward_address
                    "
                    class="emp-sub"
                  >
                    SOGo: {{ row.sogo_status.forward_address }}
                  </div>
                  <div
                    v-if="row.postfix_forwards && row.postfix_forwards.length"
                    class="emp-sub"
                  >
                    Postfix: {{ row.postfix_forwards.join(", ") }}
                  </div>
                  <span
                    v-if="
                      !(
                        row.vacation &&
                        row.vacation.enabled &&
                        row.vacation.forward_enabled &&
                        row.vacation.forward_address
                      ) &&
                      !(
                        row.sogo_status &&
                        row.sogo_status.forward_enabled &&
                        row.sogo_status.forward_address
                      ) &&
                      !(row.postfix_forwards && row.postfix_forwards.length)
                    "
                    >-</span
                  >
                </cv-data-table-cell>

                <!-- Actions -->
                <cv-data-table-cell class="table-actions-cell">
                  <div class="row-actions">
                    <NsButton
                      kind="ghost"
                      size="small"
                      :icon="Edit20"
                      @click="openEditModal(row)"
                    >
                      {{ $t("mailboxes.edit_action") }}
                    </NsButton>
                    <NsButton
                      v-if="row.vacation && row.vacation.enabled"
                      kind="ghost"
                      size="small"
                      :icon="Reset20"
                      :disabled="disablingUser === row.user"
                      @click="quickDisableVacation(row)"
                    >
                      {{ $t("mailboxes.disable_action") }}
                    </NsButton>
                  </div>
                </cv-data-table-cell>
              </cv-data-table-row>
            </template>
          </NsDataTable>
        </cv-tile>
      </cv-column>
    </cv-row>

    <EditVacationModal
      :visible="isEditModalVisible"
      :mailbox="selectedMailbox"
      :allMailboxes="mailboxes"
      :templates="templates"
      @close="closeEditModal"
      @saved="onVacationSaved"
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
import EditVacationModal from "../components/EditVacationModal.vue";
import ArrowRight16 from "@carbon/icons-vue/es/arrow--right/16";
import Edit20 from "@carbon/icons-vue/es/edit/20";
import Renew20 from "@carbon/icons-vue/es/renew/20";
import Reset20 from "@carbon/icons-vue/es/reset/20";

export default {
  name: "Mailboxes",
  components: {
    EditVacationModal,
    ArrowRight16,
  },
  mixins: [TaskService, QueryParamService, UtilService, PageTitleService],
  pageTitle() {
    return this.$t("mailboxes.title") + " - " + this.appName;
  },
  data() {
    return {
      Edit20,
      Renew20,
      Reset20,
      q: {
        page: "mailboxes",
      },
      urlCheckInterval: null,
      mailboxes: [],
      summary: {
        total: 0,
        central_vacation_active: 0,
        sogo_vacation_active: 0,
        forwards_active: 0,
      },
      templates: [],
      statusFilter: "all",
      tablePage: [],
      tableColumns: [
        "display_name",
        "mail",
        "department",
        "vacation",
        "aliases",
        "groups",
      ],
      selectedMailbox: null,
      isEditModalVisible: false,
      disablingUser: "",
      savedNotice: "",
      loading: {
        listMailboxes: false,
        getTemplates: false,
      },
      error: {
        listMailboxes: "",
        getTemplates: "",
        setVacation: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    i18nTableColumns() {
      return [
        this.$t("mailboxes.col_employee"),
        this.$t("mailboxes.col_mail"),
        this.$t("mailboxes.col_department"),
        this.$t("mailboxes.col_status"),
        this.$t("mailboxes.col_forwarding"),
        this.$t("mailboxes.col_actions"),
      ];
    },
    statusFilterOptions() {
      const centralCount = this.mailboxes.filter(
        (m) => m.vacation && m.vacation.enabled
      ).length;
      const sogoCount = this.mailboxes.filter(
        (m) => m.sogo_status && m.sogo_status.vacation_enabled
      ).length;
      const forwardCount = this.mailboxes.filter((m) =>
        this.hasAnyForward(m)
      ).length;
      return [
        {
          value: "all",
          label: this.$t("mailboxes.filter_all"),
          count: this.mailboxes.length,
        },
        {
          value: "central",
          label: this.$t("mailboxes.filter_central"),
          count: centralCount,
        },
        {
          value: "sogo",
          label: this.$t("mailboxes.filter_sogo"),
          count: sogoCount,
        },
        {
          value: "forwards",
          label: this.$t("mailboxes.filter_forwards"),
          count: forwardCount,
        },
      ];
    },
    statusFilteredMailboxes() {
      if (this.statusFilter === "central") {
        return this.mailboxes.filter((m) => m.vacation && m.vacation.enabled);
      }
      if (this.statusFilter === "sogo") {
        return this.mailboxes.filter(
          (m) => m.sogo_status && m.sogo_status.vacation_enabled
        );
      }
      if (this.statusFilter === "forwards") {
        return this.mailboxes.filter((m) => this.hasAnyForward(m));
      }
      return this.mailboxes;
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
    hasAnyForward(m) {
      const centralFwd = Boolean(
        m.vacation &&
          m.vacation.enabled &&
          m.vacation.forward_enabled &&
          m.vacation.forward_address
      );
      const sogoFwd = Boolean(
        m.sogo_status &&
          m.sogo_status.forward_enabled &&
          m.sogo_status.forward_address
      );
      const postfixFwd = Boolean(
        m.postfix_forwards && m.postfix_forwards.length > 0
      );
      return centralFwd || sogoFwd || postfixFwd;
    },
    formatPeriod(vac) {
      if (!vac) {
        return "";
      }
      if (vac.start_date && vac.end_date) {
        return `${vac.start_date} – ${vac.end_date}`;
      }
      if (vac.start_date) {
        return `${this.$t("mailboxes.since_prefix")} ${vac.start_date}`;
      }
      if (vac.end_date) {
        return `${this.$t("mailboxes.until_prefix")} ${vac.end_date}`;
      }
      return this.$t("mailboxes.open_ended");
    },
    reloadAll(forceRefresh = false) {
      this.listMailboxes(forceRefresh === true);
      this.getTemplates();
    },
    async listMailboxes(forceRefresh = false) {
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
      this.mailboxes = Array.isArray(output.mailboxes) ? output.mailboxes : [];
      if (output.summary) {
        this.summary = {
          total: output.summary.total || 0,
          central_vacation_active: output.summary.central_vacation_active || 0,
          sogo_vacation_active: output.summary.sogo_vacation_active || 0,
          forwards_active: output.summary.forwards_active || 0,
        };
      } else {
        this.summary = {
          total: this.mailboxes.length,
          central_vacation_active: this.mailboxes.filter(
            (m) => m.vacation && m.vacation.enabled
          ).length,
          sogo_vacation_active: this.mailboxes.filter(
            (m) => m.sogo_status && m.sogo_status.vacation_enabled
          ).length,
          forwards_active: this.mailboxes.filter((m) => this.hasAnyForward(m))
            .length,
        };
      }
      this.loading.listMailboxes = false;
    },
    async getTemplates() {
      this.loading.getTemplates = true;
      this.error.getTemplates = "";
      const taskAction = "get-templates";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getTemplatesAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getTemplatesCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "get-templates",
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
        this.error.getTemplates = this.getErrorMessage(err);
        this.loading.getTemplates = false;
      }
    },
    getTemplatesAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getTemplates = this.$t("error.generic_error");
      this.loading.getTemplates = false;
    },
    getTemplatesCompleted(taskContext, taskResult) {
      const output = taskResult.output || {};
      this.templates = Array.isArray(output.templates) ? output.templates : [];
      this.loading.getTemplates = false;
    },
    openEditModal(mailbox) {
      this.selectedMailbox = mailbox;
      this.isEditModalVisible = true;
    },
    closeEditModal() {
      this.isEditModalVisible = false;
    },
    onVacationSaved() {
      const targetName = this.selectedMailbox
        ? this.selectedMailbox.display_name || this.selectedMailbox.user
        : "";
      this.isEditModalVisible = false;
      this.savedNotice = this.$t("mailboxes.saved_description", {
        name: targetName,
      });
      this.listMailboxes();
    },
    async quickDisableVacation(row) {
      this.disablingUser = row.user;
      this.error.setVacation = "";
      const taskAction = "set-vacation";
      const eventId = this.getUuid();
      const vac = row.vacation || {};

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.disablingUser = "";
        this.error.setVacation = this.$t("error.generic_error");
      });
      this.core.$root.$once(`${taskAction}-completed-${eventId}`, () => {
        this.disablingUser = "";
        this.savedNotice = this.$t("mailboxes.disabled_description", {
          name: row.display_name || row.user,
        });
        this.listMailboxes();
      });

      const payload = {
        user: row.user,
        enabled: false,
        start_date: vac.start_date || "",
        end_date: vac.end_date || "",
        subject: vac.subject || "",
        body: vac.body || "",
        forward_enabled: Boolean(vac.forward_enabled),
        forward_address: vac.forward_address || "",
        keep_copy: vac.keep_copy !== undefined ? Boolean(vac.keep_copy) : true,
        updated_by: "cockpit-admin",
      };

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "set-vacation",
          data: payload,
          extra: {
            title: this.$t("action." + taskAction),
            description: row.display_name || row.user,
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];

      if (err) {
        this.disablingUser = "";
        this.error.setVacation = this.getErrorMessage(err);
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

.kpi-tile {
  padding: $spacing-04;
}

.kpi-label {
  font-size: 0.75rem;
  color: $text-02;
  margin-bottom: $spacing-02;
}

.kpi-value {
  font-size: 1.5rem;
  font-weight: 600;
}

.toolbar-top {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: $spacing-04;
  margin-bottom: $spacing-05;
}

.status-filters {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.emp-name {
  font-weight: 600;
}

.emp-sub,
.aliases-line,
.period-note {
  font-size: 0.75rem;
  color: $text-02;
  margin-top: 0.125rem;
}

.tags-cell {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.user-groups-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-top: 0.25rem;
}

.forward-entry {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: $spacing-02;
}

.inline-arrow {
  flex-shrink: 0;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}
</style>
