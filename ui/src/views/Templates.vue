<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("templates.title") }}</h2>
      </cv-column>
    </cv-row>

    <cv-row>
      <cv-column>
        <NsTabs :container="false" :aria-label="$t('templates.tabs_aria')">
          <!-- TAB 1: Company Vacation Templates -->
          <cv-tab
            id="tab-templates"
            :label="$t('templates.tab_templates')"
            :selected="true"
          >
            <div class="tab-panel">
              <NsInlineNotification
                v-if="error.getTemplates"
                kind="error"
                :title="$t('action.get-templates')"
                :description="error.getTemplates"
                :showCloseButton="false"
                class="mg-bottom"
              />

              <NsInlineNotification
                v-if="error.setTemplates"
                kind="error"
                :title="$t('action.set-templates')"
                :description="error.setTemplates"
                :showCloseButton="false"
                class="mg-bottom"
              />

              <NsInlineNotification
                v-if="templatesSavedNotice"
                kind="success"
                :title="$t('templates.saved_title')"
                :description="templatesSavedNotice"
                @close="templatesSavedNotice = ''"
                class="mg-bottom"
              />

              <cv-tile light>
                <cv-skeleton-text
                  v-if="loading.getTemplates"
                  heading
                  paragraph
                  :line-count="10"
                  width="80%"
                />
                <div v-else>
                  <div class="templates-header-bar mg-bottom">
                    <p class="section-intro">
                      {{ $t("templates.intro") }}
                    </p>
                    <div class="header-actions">
                      <NsButton
                        kind="tertiary"
                        size="small"
                        :icon="Add20"
                        :disabled="loading.setTemplates"
                        @click="addTemplate"
                      >
                        {{ $t("templates.add_template") }}
                      </NsButton>
                    </div>
                  </div>

                  <NsEmptyState
                    v-if="!editableTemplates.length"
                    :title="$t('templates.empty_templates')"
                  />

                  <div v-else class="templates-layout">
                    <!-- Left Selector List -->
                    <div class="template-list-col">
                      <div
                        v-for="(tpl, idx) in editableTemplates"
                        :key="tpl.id || idx"
                        class="template-item-card"
                        :class="{ active: idx === activeTemplateIndex }"
                        @click="activeTemplateIndex = idx"
                      >
                        <div class="tpl-card-title">
                          {{ tpl.name || $t("templates.unnamed_template") }}
                        </div>
                        <div class="tpl-card-badges">
                          <NsTag
                            :label="(tpl.lang || 'de').toUpperCase()"
                            kind="cyan"
                            size="sm"
                          />
                          <NsTag
                            v-if="tpl.is_default"
                            :label="$t('templates.default_badge')"
                            kind="green"
                            size="sm"
                          />
                        </div>
                      </div>
                    </div>

                    <!-- Right Editor Form -->
                    <div v-if="currentTemplate" class="template-editor-col">
                      <cv-form @submit.prevent="saveTemplates">
                        <div class="editor-top-grid mg-bottom">
                          <NsTextInput
                            :label="$t('templates.template_name_label')"
                            v-model.trim="currentTemplate.name"
                            :invalidMessage="error.templateName"
                            :disabled="loading.setTemplates"
                            ref="templateName"
                          />

                          <cv-select
                            :label="$t('templates.template_lang_label')"
                            v-model="currentTemplate.lang"
                            :disabled="loading.setTemplates"
                          >
                            <cv-select-option value="de">
                              Deutsch (DE)
                            </cv-select-option>
                            <cv-select-option value="en">
                              English (EN)
                            </cv-select-option>
                          </cv-select>
                        </div>

                        <div class="toggle-and-delete mg-bottom">
                          <NsToggle
                            value="isDefaultTemplate"
                            :label="$t('templates.is_default_label')"
                            v-model="currentTemplate.is_default"
                            :disabled="loading.setTemplates"
                          >
                            <template slot="text-left">{{
                              $t("common.disabled")
                            }}</template>
                            <template slot="text-right">{{
                              $t("common.enabled")
                            }}</template>
                          </NsToggle>

                          <NsButton
                            v-if="
                              editableTemplates.length > 1 &&
                              !isProtectedTemplate(currentTemplate)
                            "
                            type="button"
                            kind="danger--ghost"
                            size="small"
                            :icon="TrashCan20"
                            :disabled="loading.setTemplates"
                            @click="removeCurrentTemplate"
                          >
                            {{ $t("templates.delete_template") }}
                          </NsButton>
                        </div>

                        <!-- Clickable Placeholder Helper Chips -->
                        <div class="placeholders-box mg-bottom">
                          <div class="placeholders-title">
                            {{ $t("templates.placeholders_title") }}
                          </div>
                          <div class="placeholders-hint">
                            {{ $t("templates.placeholders_hint") }}
                          </div>
                          <div class="chips-wrap">
                            <button
                              v-for="ph in placeholders"
                              :key="ph.key"
                              type="button"
                              class="placeholder-chip"
                              :disabled="loading.setTemplates"
                              @click="insertPlaceholder(ph.token)"
                            >
                              <code>{{ ph.token }}</code>
                            </button>
                          </div>
                        </div>

                        <NsTextInput
                          :label="$t('templates.subject_label')"
                          v-model="currentTemplate.subject"
                          :invalidMessage="error.templateSubject"
                          :disabled="loading.setTemplates"
                          @focus="lastFocusedField = 'subject'"
                          class="mg-bottom"
                          ref="templateSubject"
                        />

                        <div class="bx--form-item mg-bottom">
                          <label class="bx--label" for="template-body-editor">
                            {{ $t("templates.body_label") }}
                          </label>
                          <textarea
                            id="template-body-editor"
                            rows="9"
                            class="bx--text-area"
                            :class="{
                              'bx--text-area--invalid': !!error.templateBody,
                            }"
                            v-model="currentTemplate.body"
                            :disabled="loading.setTemplates"
                            @focus="lastFocusedField = 'body'"
                            ref="templateBody"
                          ></textarea>
                          <div
                            v-if="error.templateBody"
                            class="bx--form-requirement"
                          >
                            {{ error.templateBody }}
                          </div>
                        </div>

                        <NsButton
                          kind="primary"
                          :icon="Save20"
                          :loading="loading.setTemplates"
                          :disabled="
                            loading.getTemplates || loading.setTemplates
                          "
                        >
                          {{ $t("templates.save_templates") }}
                        </NsButton>
                      </cv-form>
                    </div>
                  </div>
                </div>
              </cv-tile>
            </div>
          </cv-tab>

          <!-- TAB 2: Audit Log -->
          <cv-tab id="tab-audit" :label="$t('templates.tab_audit')">
            <div class="tab-panel">
              <cv-tile light>
                <div class="audit-toolbar mg-bottom">
                  <p class="section-intro">
                    {{ $t("templates.audit_intro") }}
                  </p>
                  <NsButton
                    kind="secondary"
                    size="field"
                    :icon="Renew20"
                    :loading="loading.listAuditLog"
                    :disabled="loading.listAuditLog"
                    @click="listAuditLog"
                  >
                    {{ $t("common.refresh") }}
                  </NsButton>
                </div>

                <NsDataTable
                  :allRows="auditEntries"
                  :columns="i18nAuditColumns"
                  :rawColumns="auditColumns"
                  :isSearchable="true"
                  :searchPlaceholder="$t('templates.audit_search_placeholder')"
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
                  :isLoading="loading.listAuditLog"
                  :skeletonRows="6"
                  :isErrorShown="!!error.listAuditLog"
                  :errorTitle="$t('action.list-audit-log')"
                  :errorDescription="error.listAuditLog"
                  :itemsPerPageLabel="
                    coreT(
                      'pagination.items_per_page',
                      'pagination.items_per_page'
                    )
                  "
                  :rangeOfTotalItemsLabel="
                    coreT(
                      'pagination.range_of_total_items',
                      'pagination.range_of_total_items'
                    )
                  "
                  :ofTotalPagesLabel="
                    coreT(
                      'pagination.of_total_pages',
                      'pagination.of_total_pages'
                    )
                  "
                  :backwardText="
                    coreT(
                      'pagination.previous_page',
                      'pagination.previous_page'
                    )
                  "
                  :forwardText="
                    coreT('pagination.next_page', 'pagination.next_page')
                  "
                  :pageNumberLabel="
                    coreT('pagination.page_number', 'pagination.page_number')
                  "
                  @updatePage="auditTablePage = $event"
                >
                  <template slot="empty-state">
                    <NsEmptyState :title="$t('templates.audit_empty_title')">
                      <template #description>
                        <div>{{ $t("templates.audit_empty_description") }}</div>
                      </template>
                    </NsEmptyState>
                  </template>
                  <template slot="data">
                    <cv-data-table-row
                      v-for="(entry, idx) in auditTablePage"
                      :key="entry.id || String(idx)"
                      :value="String(idx)"
                    >
                      <cv-data-table-cell class="nowrap-cell">
                        {{ entry.timestamp || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        <strong>{{ entry.actor || "-" }}</strong>
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        <NsTag
                          :label="entry.source || 'system'"
                          :kind="sourceTagKind(entry.source)"
                          size="sm"
                        />
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        {{ entry.target_user || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        <code>{{ entry.action || "-" }}</code>
                      </cv-data-table-cell>
                      <cv-data-table-cell class="details-cell">
                        {{ formatAuditDetails(entry.details) }}
                      </cv-data-table-cell>
                    </cv-data-table-row>
                  </template>
                </NsDataTable>
              </cv-tile>
            </div>
          </cv-tab>
        </NsTabs>
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
  UtilService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";
import Save20 from "@carbon/icons-vue/es/save/20";
import Add20 from "@carbon/icons-vue/es/add/20";
import TrashCan20 from "@carbon/icons-vue/es/trash-can/20";
import Renew20 from "@carbon/icons-vue/es/renew/20";
import { TEMPLATE_PLACEHOLDERS } from "../utils/templateRenderer";

export default {
  name: "Templates",
  mixins: [TaskService, QueryParamService, UtilService, PageTitleService],
  pageTitle() {
    return this.$t("templates.title") + " - " + this.appName;
  },
  data() {
    return {
      Save20,
      Add20,
      TrashCan20,
      Renew20,
      placeholders: TEMPLATE_PLACEHOLDERS,
      q: {
        page: "templates",
      },
      urlCheckInterval: null,
      editableTemplates: [],
      activeTemplateIndex: 0,
      lastFocusedField: "body",
      templatesSavedNotice: "",
      auditEntries: [],
      auditTablePage: [],
      auditColumns: [
        "timestamp",
        "actor",
        "source",
        "target_user",
        "action",
        "details",
      ],
      loading: {
        getTemplates: false,
        setTemplates: false,
        listAuditLog: false,
      },
      error: {
        getTemplates: "",
        setTemplates: "",
        listAuditLog: "",
        templateName: "",
        templateSubject: "",
        templateBody: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    currentTemplate() {
      return this.editableTemplates[this.activeTemplateIndex] || null;
    },
    i18nAuditColumns() {
      return [
        this.$t("templates.col_timestamp"),
        this.$t("templates.col_actor"),
        this.$t("templates.col_source"),
        this.$t("templates.col_target_user"),
        this.$t("templates.col_action"),
        this.$t("templates.col_details"),
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
    this.getTemplates();
    this.listAuditLog();
  },
  methods: {
    coreT(coreKey, fallbackKey) {
      if (this.core && typeof this.core.$t === "function") {
        return this.core.$t(coreKey);
      }
      return this.$t(fallbackKey);
    },
    isProtectedTemplate(tpl) {
      return tpl && (tpl.id === "default_de" || tpl.id === "default_en");
    },
    sourceTagKind(source) {
      if (source === "portal") {
        return "blue";
      }
      if (source === "cockpit") {
        return "purple";
      }
      return "cool-gray";
    },
    formatAuditDetails(details) {
      if (!details) {
        return "-";
      }
      if (typeof details === "object") {
        return JSON.stringify(details);
      }
      return String(details);
    },
    addTemplate() {
      const id = `custom_${Date.now()}`;
      this.editableTemplates.push({
        id,
        name: this.$t("templates.new_template_default_name"),
        lang: this.$i18n.locale === "de" ? "de" : "en",
        is_default: false,
        subject: "Abwesenheitsnotiz: {{displayName}}",
        body: "Guten Tag,\n\nvielen Dank für Ihre Nachricht. Ich bin derzeit nicht im Haus{{#endDate}} (voraussichtlich bis einschließlich {{endDate}}){{/endDate}}.\n{{#deputyEmail}}\nIn dringenden Fällen wenden Sie sich bitte an {{deputyEmail}}.\n{{/deputyEmail}}\nMit freundlichen Grüßen\n{{displayName}}",
      });
      this.activeTemplateIndex = this.editableTemplates.length - 1;
    },
    removeCurrentTemplate() {
      if (!this.currentTemplate || this.editableTemplates.length <= 1) {
        return;
      }
      this.editableTemplates.splice(this.activeTemplateIndex, 1);
      if (this.activeTemplateIndex >= this.editableTemplates.length) {
        this.activeTemplateIndex = Math.max(
          0,
          this.editableTemplates.length - 1
        );
      }
    },
    insertPlaceholder(token) {
      if (!this.currentTemplate) {
        return;
      }
      if (this.lastFocusedField === "subject") {
        this.currentTemplate.subject =
          (this.currentTemplate.subject || "") + token;
        return;
      }
      const textarea = this.$refs.templateBody;
      const existing = this.currentTemplate.body || "";
      if (textarea && typeof textarea.selectionStart === "number") {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        this.currentTemplate.body =
          existing.slice(0, start) + token + existing.slice(end);
        this.$nextTick(() => {
          textarea.focus();
          const cursor = start + token.length;
          textarea.setSelectionRange(cursor, cursor);
        });
      } else {
        this.currentTemplate.body = existing + token;
      }
    },
    validateTemplates() {
      this.error.templateName = "";
      this.error.templateSubject = "";
      this.error.templateBody = "";
      this.error.setTemplates = "";

      for (let i = 0; i < this.editableTemplates.length; i++) {
        const tpl = this.editableTemplates[i];
        if (!tpl.name || !tpl.name.trim()) {
          this.activeTemplateIndex = i;
          this.error.templateName = this.$t("common.required");
          return false;
        }
        if (!tpl.subject || !tpl.subject.trim()) {
          this.activeTemplateIndex = i;
          this.error.templateSubject = this.$t("common.required");
          return false;
        }
        if (!tpl.body || !tpl.body.trim()) {
          this.activeTemplateIndex = i;
          this.error.templateBody = this.$t("common.required");
          return false;
        }
      }
      return true;
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
      const list = Array.isArray(output.templates) ? output.templates : [];
      this.editableTemplates = list.map((tpl) => ({
        id: tpl.id,
        name: tpl.name || "",
        lang: tpl.lang || "de",
        is_default: Boolean(tpl.is_default),
        subject: tpl.subject || "",
        body: tpl.body || "",
      }));
      if (this.activeTemplateIndex >= this.editableTemplates.length) {
        this.activeTemplateIndex = 0;
      }
      this.loading.getTemplates = false;
    },
    async saveTemplates() {
      if (!this.validateTemplates()) {
        return;
      }

      this.loading.setTemplates = true;
      this.error.setTemplates = "";
      this.templatesSavedNotice = "";
      const taskAction = "set-templates";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.setTemplatesAborted
      );
      this.core.$root.$once(
        `${taskAction}-validation-failed-${eventId}`,
        this.setTemplatesValidationFailed
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.setTemplatesCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "set-templates",
          data: {
            templates: this.editableTemplates,
          },
          extra: {
            title: this.$t("action." + taskAction),
            description: this.$t("common.processing"),
            eventId,
          },
        })
      );
      const err = res[0];

      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.setTemplates = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
        this.loading.setTemplates = false;
      }
    },
    setTemplatesValidationFailed(validationErrors) {
      this.loading.setTemplates = false;
      const first = (validationErrors || [])[0];
      this.error.setTemplates = first
        ? first.error || this.$t("error.validation_error")
        : this.$t("error.validation_error");
    },
    setTemplatesAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.setTemplates = this.$t("error.generic_error");
      this.loading.setTemplates = false;
    },
    setTemplatesCompleted(taskContext, taskResult) {
      this.loading.setTemplates = false;
      this.templatesSavedNotice = this.$t("templates.saved_description");
      if (
        taskResult &&
        taskResult.output &&
        Array.isArray(taskResult.output.templates)
      ) {
        this.editableTemplates = taskResult.output.templates.map((tpl) => ({
          id: tpl.id,
          name: tpl.name || "",
          lang: tpl.lang || "de",
          is_default: Boolean(tpl.is_default),
          subject: tpl.subject || "",
          body: tpl.body || "",
        }));
      }
      this.listAuditLog();
    },
    async listAuditLog() {
      this.loading.listAuditLog = true;
      this.error.listAuditLog = "";
      const taskAction = "list-audit-log";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.listAuditLogAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.listAuditLogCompleted
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "list-audit-log",
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
        this.error.listAuditLog = this.getErrorMessage(err);
        this.loading.listAuditLog = false;
      }
    },
    listAuditLogAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.listAuditLog = this.$t("error.generic_error");
      this.loading.listAuditLog = false;
    },
    listAuditLogCompleted(taskContext, taskResult) {
      const output = taskResult.output || {};
      this.auditEntries = Array.isArray(output.entries) ? output.entries : [];
      this.loading.listAuditLog = false;
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.tab-panel {
  margin-top: $spacing-05;
}

.mg-bottom {
  margin-bottom: $spacing-05;
}

.templates-header-bar,
.audit-toolbar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: $spacing-04;
}

.section-intro {
  color: $text-02;
  max-width: 48rem;
}

.templates-layout {
  display: grid;
  grid-template-columns: minmax(14rem, 18rem) 1fr;
  gap: $spacing-06;

  @media (max-width: $breakpoint-medium) {
    grid-template-columns: 1fr;
  }
}

.template-list-col {
  display: flex;
  flex-direction: column;
  gap: $spacing-03;
}

.template-item-card {
  padding: $spacing-04;
  background-color: $ui-01;
  border-left: 3px solid transparent;
  cursor: pointer;
  transition: background-color 0.15s ease;

  &:hover {
    background-color: $hover-ui;
  }

  &.active {
    border-left-color: $interactive-01;
    background-color: $selected-ui;
  }
}

.tpl-card-title {
  font-weight: 600;
  margin-bottom: $spacing-02;
}

.tpl-card-badges {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.editor-top-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: $spacing-05;

  @media (max-width: $breakpoint-medium) {
    grid-template-columns: 1fr;
  }
}

.toggle-and-delete {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: $spacing-04;
}

.placeholders-box {
  padding: $spacing-04;
  background-color: $ui-01;
}

.placeholders-title {
  font-weight: 600;
  font-size: 0.875rem;
  margin-bottom: $spacing-01;
}

.placeholders-hint {
  font-size: 0.75rem;
  color: $text-02;
  margin-bottom: $spacing-03;
}

.chips-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.placeholder-chip {
  border: 1px solid $ui-04;
  background-color: $ui-02;
  color: $text-01;
  border-radius: 1rem;
  padding: 0.2rem 0.65rem;
  font-size: 0.75rem;
  cursor: pointer;

  &:hover:not(:disabled) {
    background-color: $hover-ui;
    border-color: $interactive-01;
  }
}

.nowrap-cell {
  white-space: nowrap;
}

.details-cell {
  max-width: 28rem;
  word-break: break-word;
}
</style>
