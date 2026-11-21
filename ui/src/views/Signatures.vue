<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("signatures.title") }}</h2>
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

    <cv-row v-if="error.deleteSignature">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.delete-signature')"
          :description="error.deleteSignature"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>

    <cv-row v-if="savedNotice">
      <cv-column>
        <NsInlineNotification
          kind="success"
          :title="$t('signatures.saved_title')"
          :description="savedNotice"
          @close="savedNotice = ''"
        />
      </cv-column>
    </cv-row>

    <!-- Before-DKIM Postfix Milter Status & Setup Card -->
    <cv-row class="mg-bottom">
      <cv-column>
        <cv-tile light>
          <div class="milter-card-header">
            <div>
              <h4>{{ $t("signatures.milter_card_title") }}</h4>
              <p class="milter-desc">
                {{ $t("signatures.milter_card_description") }}
              </p>
            </div>
            <div class="milter-badges">
              <NsTag
                :label="
                  milterStatus.listening
                    ? $t('signatures.milter_listening')
                    : $t('signatures.milter_stopped')
                "
                :kind="milterStatus.listening ? 'green' : 'warm-gray'"
                size="sm"
              />
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
                  $t('signatures.milter_skipped_count', {
                    count: milterStatus.messages_skipped || 0,
                  })
                "
                kind="cool-gray"
                size="sm"
              />
            </div>
          </div>

          <cv-accordion class="mg-top-sm">
            <cv-accordion-item :open="false">
              <template slot="title">
                {{ $t("signatures.postfix_hook_accordion_title") }}
              </template>
              <template slot="content">
                <p class="snippet-help">
                  {{ $t("signatures.postfix_hook_help") }}
                </p>
                <NsCodeSnippet
                  light
                  hideExpandButton
                  :copyTooltip="$t('signatures.copy_snippet')"
                  :copyFeedback="$t('signatures.copied_snippet')"
                  >{{ effectivePostfixSnippet }}</NsCodeSnippet
                >
              </template>
            </cv-accordion-item>
            <cv-accordion-item :open="false">
              <template slot="title">
                {{ $t("signatures.postfix_reset_accordion_title") }}
              </template>
              <template slot="content">
                <p class="snippet-help">
                  {{ $t("signatures.postfix_reset_help") }}
                </p>
                <NsCodeSnippet
                  light
                  hideExpandButton
                  :copyTooltip="$t('signatures.copy_snippet')"
                  :copyFeedback="$t('signatures.copied_snippet')"
                  >{{ effectivePostfixResetSnippet }}</NsCodeSnippet
                >
              </template>
            </cv-accordion-item>
          </cv-accordion>
        </cv-tile>
      </cv-column>
    </cv-row>

    <!-- Signature Rules Table -->
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
              {{ $t("signatures.add_rule") }}
            </NsButton>
            <NsButton
              kind="secondary"
              size="field"
              :icon="Renew20"
              :loading="loading.listSignatures"
              :disabled="loading.listSignatures"
              @click="reloadAll"
            >
              {{ $t("common.refresh") }}
            </NsButton>
          </div>

          <NsDataTable
            :allRows="signatures"
            :columns="i18nTableColumns"
            :rawColumns="tableColumns"
            :isSearchable="true"
            :searchPlaceholder="$t('signatures.search_placeholder')"
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
            :isLoading="loading.listSignatures"
            :skeletonRows="4"
            :isErrorShown="!!error.listSignatures"
            :errorTitle="$t('action.list-signatures')"
            :errorDescription="error.listSignatures"
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
              <NsEmptyState :title="$t('signatures.empty_title')">
                <template #description>
                  <div>{{ $t("signatures.empty_description") }}</div>
                </template>
              </NsEmptyState>
            </template>
            <template slot="data">
              <cv-data-table-row
                v-for="(row, idx) in tablePage"
                :key="row.id || String(idx)"
                :value="String(idx)"
              >
                <cv-data-table-cell>
                  <strong>{{ row.name }}</strong>
                </cv-data-table-cell>
                <cv-data-table-cell>
                  <NsTag
                    :label="formatScopeType(row.scope_type)"
                    :kind="scopeTagKind(row.scope_type)"
                    size="sm"
                  />
                  <span class="scope-val">{{ row.scope_value || "*" }}</span>
                </cv-data-table-cell>
                <cv-data-table-cell>
                  {{ row.priority !== undefined ? row.priority : 10 }}
                </cv-data-table-cell>
                <cv-data-table-cell>
                  <div>{{ formatReplyMode(row.reply_mode) }}</div>
                  <div class="sub-note">
                    {{ formatPlacement(row.placement) }}
                  </div>
                </cv-data-table-cell>
                <cv-data-table-cell>
                  <NsTag
                    :label="
                      row.enabled ? $t('common.enabled') : $t('common.disabled')
                    "
                    :kind="row.enabled ? 'green' : 'gray'"
                    size="sm"
                  />
                </cv-data-table-cell>
                <cv-data-table-cell>
                  <div class="row-actions">
                    <NsButton
                      kind="ghost"
                      size="small"
                      :icon="Edit20"
                      @click="openEditModal(row, false)"
                    >
                      {{ $t("mailboxes.edit_action") }}
                    </NsButton>
                    <NsButton
                      kind="ghost"
                      size="small"
                      :icon="View20"
                      @click="openEditModal(row, true)"
                    >
                      {{ $t("signatures.preview_action") }}
                    </NsButton>
                    <NsButton
                      kind="danger--ghost"
                      size="small"
                      :icon="TrashCan20"
                      :disabled="deletingId === row.id"
                      @click="deleteSignature(row)"
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

    <EditSignatureModal
      :visible="isModalVisible"
      :signature="selectedSignature"
      :availableGroups="availableGroups"
      :userDomain="userDomain"
      :mailboxes="mailboxes"
      :autoRunPreview="modalAutoPreview"
      @close="isModalVisible = false"
      @saved="onSignatureSaved"
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
import EditSignatureModal from "../components/EditSignatureModal.vue";
import Add20 from "@carbon/icons-vue/es/add/20";
import Renew20 from "@carbon/icons-vue/es/renew/20";
import Edit20 from "@carbon/icons-vue/es/edit/20";
import View20 from "@carbon/icons-vue/es/view/20";
import TrashCan20 from "@carbon/icons-vue/es/trash-can/20";

export default {
  name: "Signatures",
  components: {
    EditSignatureModal,
  },
  mixins: [TaskService, QueryParamService, UtilService, PageTitleService],
  pageTitle() {
    return this.$t("signatures.title") + " - " + this.appName;
  },
  data() {
    return {
      Add20,
      Renew20,
      Edit20,
      View20,
      TrashCan20,
      q: {
        page: "signatures",
      },
      urlCheckInterval: null,
      signatures: [],
      milterStatus: {
        enabled: true,
        listening: false,
        port: 8090,
        host_port: 0,
        messages_signed: 0,
        messages_skipped: 0,
      },
      milterPostfixSnippet: "",
      milterPostfixResetSnippet: "",
      milterTcpPort: 0,
      mailModuleId: "mail1",
      userDomain: "",
      availableGroups: [],
      mailboxes: [],
      tablePage: [],
      tableColumns: [
        "name",
        "scope_value",
        "priority",
        "reply_mode",
        "enabled",
        "actions",
      ],
      selectedSignature: null,
      isModalVisible: false,
      modalAutoPreview: false,
      deletingId: null,
      savedNotice: "",
      loading: {
        listSignatures: false,
        getConfiguration: false,
        listMailboxes: false,
      },
      error: {
        listSignatures: "",
        getConfiguration: "",
        deleteSignature: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    i18nTableColumns() {
      return [
        this.$t("signatures.col_name"),
        this.$t("signatures.col_scope"),
        this.$t("signatures.col_priority"),
        this.$t("signatures.col_behavior"),
        this.$t("signatures.col_status"),
        this.$t("mailboxes.col_actions"),
      ];
    },
    effectivePostfixSnippet() {
      if (this.milterPostfixSnippet) {
        return this.milterPostfixSnippet;
      }
      const port =
        this.milterStatus.host_port ||
        this.milterTcpPort ||
        "<MILTER_TCP_PORT>";
      const mailId = this.mailModuleId || "mail1";
      return `runagent -m ${mailId} python3 -c "import agent; agent.set_env('POSTFIX_MILTERS', 'inet:127.0.0.1:${port}, inet:127.0.0.1:11332')" && runagent -m ${mailId} systemctl --user reload postfix`;
    },
    effectivePostfixResetSnippet() {
      if (this.milterPostfixResetSnippet) {
        return this.milterPostfixResetSnippet;
      }
      const mailId = this.mailModuleId || "mail1";
      return `runagent -m ${mailId} python3 -c "import agent; agent.unset_env('POSTFIX_MILTERS')" && runagent -m ${mailId} systemctl --user reload postfix`;
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
    scopeTagKind(scopeType) {
      if (scopeType === "group") {
        return "purple";
      }
      if (scopeType === "domain") {
        return "cyan";
      }
      return "teal";
    },
    formatScopeType(scopeType) {
      if (scopeType === "group") {
        return this.$t("signatures.scope_group_short");
      }
      if (scopeType === "domain") {
        return this.$t("signatures.scope_domain_short");
      }
      return this.$t("signatures.scope_default_short");
    },
    formatReplyMode(mode) {
      if (mode === "first_only") {
        return this.$t("signatures.reply_mode_first_only");
      }
      if (mode === "always") {
        return this.$t("signatures.reply_mode_always");
      }
      return this.$t("signatures.reply_mode_append_once");
    },
    formatPlacement(placement) {
      if (placement === "bottom") {
        return this.$t("signatures.placement_bottom");
      }
      return this.$t("signatures.placement_below_reply");
    },
    reloadAll() {
      this.listSignatures();
      this.getConfiguration();
      this.listMailboxes();
    },
    async listSignatures() {
      this.loading.listSignatures = true;
      this.error.listSignatures = "";
      const taskAction = "list-signatures";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        (taskResult, taskContext) => {
          console.error(`${taskContext.action} aborted`, taskResult);
          this.error.listSignatures = this.$t("error.generic_error");
          this.loading.listSignatures = false;
        }
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const out = (taskResult && taskResult.output) || {};
          this.signatures = Array.isArray(out.signatures) ? out.signatures : [];
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
      const err = res[0];
      if (err) {
        this.error.listSignatures = this.getErrorMessage(err);
        this.loading.listSignatures = false;
      }
    },
    async getConfiguration() {
      this.loading.getConfiguration = true;
      this.error.getConfiguration = "";
      const taskAction = "get-configuration";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.error.getConfiguration = this.$t("error.generic_error");
        this.loading.getConfiguration = false;
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const cfg = (taskResult && taskResult.output) || {};
          this.milterPostfixSnippet = cfg.milter_postfix_snippet || "";
          this.milterPostfixResetSnippet =
            cfg.milter_postfix_reset_snippet || "";
          this.milterTcpPort = cfg.milter_tcp_port || 0;
          this.mailModuleId = cfg.mail_module_id || "mail1";
          this.userDomain = cfg.user_domain || "";
          this.availableGroups = Array.isArray(cfg.available_groups)
            ? cfg.available_groups
            : [];
          this.loading.getConfiguration = false;
        }
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
        this.error.getConfiguration = this.getErrorMessage(err);
        this.loading.getConfiguration = false;
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
      this.selectedSignature = null;
      this.modalAutoPreview = false;
      this.isModalVisible = true;
    },
    openEditModal(row, autoPreview) {
      this.selectedSignature = row;
      this.modalAutoPreview = Boolean(autoPreview);
      this.isModalVisible = true;
    },
    onSignatureSaved() {
      this.isModalVisible = false;
      this.savedNotice = this.$t("signatures.saved_description");
      this.listSignatures();
    },
    async deleteSignature(row) {
      this.deletingId = row.id;
      this.error.deleteSignature = "";
      const taskAction = "delete-signature";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.deletingId = null;
        this.error.deleteSignature = this.$t("error.generic_error");
      });
      this.core.$root.$once(`${taskAction}-completed-${eventId}`, () => {
        this.deletingId = null;
        this.savedNotice = this.$t("signatures.deleted_description", {
          name: row.name,
        });
        this.listSignatures();
      });

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "delete-signature",
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
        this.error.deleteSignature = this.getErrorMessage(err);
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

.mg-top-sm {
  margin-top: $spacing-04;
}

.milter-card-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: $spacing-04;
}

.milter-desc,
.snippet-help {
  color: $text-02;
  font-size: 0.875rem;
  max-width: 52rem;
  margin-top: $spacing-02;
  margin-bottom: $spacing-03;
}

.milter-badges {
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

.scope-val {
  margin-left: $spacing-02;
  font-size: 0.875rem;
}

.sub-note {
  font-size: 0.75rem;
  color: $text-02;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}
</style>
