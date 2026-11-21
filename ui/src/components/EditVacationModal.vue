<template>
  <NsModal
    size="default"
    :visible="visible"
    :isLoading="loading.setVacation"
    :primaryButtonDisabled="loading.setVacation"
    hasFormContent
    @modal-hidden="onModalHidden"
    @primary-click="saveVacation"
    @secondary-click="onModalHidden"
  >
    <template slot="label">{{ mailbox ? mailbox.mail : "" }}</template>
    <template slot="title">
      {{
        $t("mailboxes.edit_modal_title", {
          name: mailbox ? mailbox.display_name || mailbox.user : "",
        })
      }}
    </template>
    <template slot="content">
      <cv-form @submit.prevent="saveVacation">
        <NsInlineNotification
          v-if="hasSogoVacation"
          kind="info"
          :title="$t('mailboxes.sogo_collision_title')"
          :description="$t('mailboxes.sogo_collision_description')"
          :showCloseButton="false"
          class="mg-bottom"
        />

        <NsInlineNotification
          v-if="error.setVacation"
          kind="error"
          :title="$t('action.set-vacation')"
          :description="error.setVacation"
          :showCloseButton="false"
          class="mg-bottom"
        />

        <NsToggle
          value="vacationEnabled"
          :label="$t('mailboxes.vacation_enabled_label')"
          v-model="form.enabled"
          :disabled="loading.setVacation"
          class="mg-bottom"
        >
          <template slot="text-left">{{ $t("common.disabled") }}</template>
          <template slot="text-right">{{ $t("common.enabled") }}</template>
        </NsToggle>

        <div class="date-grid mg-bottom">
          <NsTextInput
            type="date"
            :label="$t('mailboxes.start_date_label')"
            :helperText="$t('mailboxes.date_optional_hint')"
            v-model.trim="form.start_date"
            :invalidMessage="error.start_date"
            :disabled="loading.setVacation"
            ref="start_date"
          />
          <NsTextInput
            type="date"
            :label="$t('mailboxes.end_date_label')"
            :helperText="$t('mailboxes.end_date_optional_hint')"
            v-model.trim="form.end_date"
            :invalidMessage="error.end_date"
            :disabled="loading.setVacation"
            ref="end_date"
          />
        </div>

        <!-- Forwarding to Deputy -->
        <div class="section-box mg-bottom">
          <NsToggle
            value="forwardEnabled"
            :label="$t('mailboxes.forward_enabled_label')"
            v-model="form.forward_enabled"
            :disabled="loading.setVacation"
            class="mg-bottom-sm"
          >
            <template slot="text-left">{{ $t("common.disabled") }}</template>
            <template slot="text-right">{{ $t("common.enabled") }}</template>
          </NsToggle>

          <div v-if="form.forward_enabled" class="forward-fields">
            <NsComboBox
              v-model="form.forward_address"
              :title="$t('mailboxes.forward_address_label')"
              :label="$t('mailboxes.forward_address_placeholder')"
              :helperText="$t('mailboxes.forward_address_helper')"
              :invalidMessage="error.forward_address"
              :autoFilter="true"
              :autoHighlight="true"
              :acceptUserInput="true"
              :userInputLabel="$t('mailboxes.custom_email_label')"
              :options="colleagueOptions"
              :disabled="loading.setVacation"
              marginBottomOnOpen
              class="mg-bottom-sm"
              ref="forward_address"
            />

            <NsToggle
              value="keepCopy"
              :label="$t('mailboxes.keep_copy_label')"
              v-model="form.keep_copy"
              :disabled="loading.setVacation"
            >
              <template slot="tooltip">
                <span>{{ $t("mailboxes.keep_copy_tooltip") }}</span>
              </template>
              <template slot="text-left">{{
                $t("mailboxes.keep_copy_redirect_only")
              }}</template>
              <template slot="text-right">{{
                $t("mailboxes.keep_copy_retain")
              }}</template>
            </NsToggle>
          </div>
        </div>

        <!-- Template Quick Selector -->
        <div class="template-toolbar mg-bottom">
          <div class="template-select-col">
            <cv-select
              :label="$t('mailboxes.template_select_label')"
              v-model="selectedTemplateId"
              :disabled="loading.setVacation || !templates.length"
            >
              <cv-select-option
                v-for="tpl in templates"
                :key="tpl.id"
                :value="tpl.id"
              >
                {{ tpl.name }} ({{ (tpl.lang || "en").toUpperCase() }})
              </cv-select-option>
            </cv-select>
          </div>
          <div class="template-btn-col">
            <NsButton
              type="button"
              kind="tertiary"
              size="field"
              :icon="Document20"
              :disabled="loading.setVacation || !templates.length"
              @click="applySelectedTemplate"
            >
              {{ $t("mailboxes.insert_default_template") }}
            </NsButton>
          </div>
        </div>

        <NsTextInput
          :label="$t('mailboxes.subject_label')"
          :placeholder="$t('mailboxes.subject_placeholder')"
          v-model.trim="form.subject"
          :invalidMessage="error.subject"
          :disabled="loading.setVacation"
          class="mg-bottom"
          ref="subject"
        />

        <div class="bx--form-item mg-bottom">
          <label class="bx--label" for="vacation-body-textarea">
            {{ $t("mailboxes.body_label") }}
          </label>
          <textarea
            id="vacation-body-textarea"
            rows="7"
            class="bx--text-area"
            :class="{ 'bx--text-area--invalid': !!error.body }"
            :placeholder="$t('mailboxes.body_placeholder')"
            v-model="form.body"
            :disabled="loading.setVacation"
            ref="body"
          ></textarea>
          <div v-if="error.body" class="bx--form-requirement">
            {{ error.body }}
          </div>
        </div>
      </cv-form>
    </template>
    <template slot="secondary-button">{{ $t("common.cancel") }}</template>
    <template slot="primary-button">{{ $t("mailboxes.save_rule") }}</template>
  </NsModal>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import { TaskService, UtilService } from "@nethserver/ns8-ui-lib";
import Document20 from "@carbon/icons-vue/es/document/20";
import { renderVacationTemplate } from "../utils/templateRenderer";

export default {
  name: "EditVacationModal",
  mixins: [TaskService, UtilService],
  props: {
    visible: {
      type: Boolean,
      default: false,
    },
    mailbox: {
      type: Object,
      default: null,
    },
    allMailboxes: {
      type: Array,
      default: () => [],
    },
    templates: {
      type: Array,
      default: () => [],
    },
  },
  data() {
    return {
      Document20,
      selectedTemplateId: "",
      form: {
        enabled: false,
        start_date: "",
        end_date: "",
        subject: "",
        body: "",
        forward_enabled: false,
        forward_address: "",
        keep_copy: true,
      },
      loading: {
        setVacation: false,
      },
      error: {
        setVacation: "",
        start_date: "",
        end_date: "",
        forward_address: "",
        subject: "",
        body: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core"]),
    hasSogoVacation() {
      return Boolean(
        this.mailbox &&
          this.mailbox.sogo_status &&
          this.mailbox.sogo_status.vacation_enabled
      );
    },
    colleagueOptions() {
      const currentMail = this.mailbox ? this.mailbox.mail : "";
      const currentUser = this.mailbox ? this.mailbox.user : "";
      const options = [];
      for (const mb of this.allMailboxes) {
        if (!mb.mail || mb.user === currentUser || mb.mail === currentMail) {
          continue;
        }
        const labelName = mb.display_name || mb.user;
        const dept = mb.department ? ` (${mb.department})` : "";
        options.push({
          name: mb.user,
          label: `${labelName}${dept} — ${mb.mail}`,
          value: mb.mail,
        });
      }
      if (
        this.form.forward_address &&
        !options.some((o) => o.value === this.form.forward_address)
      ) {
        options.push({
          name: this.form.forward_address,
          label: this.form.forward_address,
          value: this.form.forward_address,
        });
      }
      return options;
    },
  },
  watch: {
    visible(isOpen) {
      if (isOpen && this.mailbox) {
        this.initFormFromMailbox();
      }
    },
    mailbox() {
      if (this.visible && this.mailbox) {
        this.initFormFromMailbox();
      }
    },
    templates: {
      immediate: true,
      handler(list) {
        if (!this.selectedTemplateId && Array.isArray(list) && list.length) {
          const preferred =
            list.find(
              (t) => t.lang === this.$i18n.locale && t.is_default === true
            ) ||
            list.find((t) => t.is_default === true) ||
            list[0];
          if (preferred) {
            this.selectedTemplateId = preferred.id;
          }
        }
      },
    },
  },
  methods: {
    initFormFromMailbox() {
      this.clearModalErrors();
      const vac = (this.mailbox && this.mailbox.vacation) || {};
      this.form = {
        enabled: Boolean(vac.enabled),
        start_date: vac.start_date || "",
        end_date: vac.end_date || "",
        subject: vac.subject || "",
        body: vac.body || "",
        forward_enabled: Boolean(vac.forward_enabled),
        forward_address: vac.forward_address || "",
        keep_copy: vac.keep_copy !== undefined ? Boolean(vac.keep_copy) : true,
      };

      if (this.templates.length) {
        const preferred =
          this.templates.find((t) => t.lang === this.$i18n.locale) ||
          this.templates.find((t) => t.is_default) ||
          this.templates[0];
        if (preferred) {
          this.selectedTemplateId = preferred.id;
        }
      }
    },
    clearModalErrors() {
      this.error = {
        setVacation: "",
        start_date: "",
        end_date: "",
        forward_address: "",
        subject: "",
        body: "",
      };
    },
    applySelectedTemplate() {
      if (!this.mailbox || !this.templates.length) {
        return;
      }
      const tpl =
        this.templates.find((t) => t.id === this.selectedTemplateId) ||
        this.templates[0];
      if (!tpl) {
        return;
      }
      const lang = tpl.lang || this.$i18n.locale || "en";
      const renderCtx = {
        mailbox: this.mailbox,
        startDate: this.form.start_date,
        endDate: this.form.end_date,
        forwardEnabled: this.form.forward_enabled,
        forwardAddress: this.form.forward_address,
        lang,
      };
      this.form.subject = renderVacationTemplate(tpl.subject || "", renderCtx);
      this.form.body = renderVacationTemplate(tpl.body || "", renderCtx);
    },
    validateForm() {
      this.clearModalErrors();
      let ok = true;
      const dateRe = /^\d{4}-\d{2}-\d{2}$/;

      if (this.form.start_date && !dateRe.test(this.form.start_date)) {
        this.error.start_date = this.$t("mailboxes.invalid_date");
        ok = false;
      }
      if (this.form.end_date && !dateRe.test(this.form.end_date)) {
        this.error.end_date = this.$t("mailboxes.invalid_date");
        ok = false;
      }
      if (
        this.form.start_date &&
        this.form.end_date &&
        dateRe.test(this.form.start_date) &&
        dateRe.test(this.form.end_date) &&
        this.form.end_date < this.form.start_date
      ) {
        this.error.end_date = this.$t("mailboxes.end_before_start");
        ok = false;
      }

      if (this.form.enabled && this.form.forward_enabled) {
        const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!this.form.forward_address || !this.form.forward_address.trim()) {
          this.error.forward_address = this.$t("common.required");
          ok = false;
        } else if (!emailRe.test(this.form.forward_address.trim())) {
          this.error.forward_address = this.$t("mailboxes.invalid_email");
          ok = false;
        }
      }

      if (this.form.enabled && !this.form.forward_enabled) {
        if (!this.form.subject || !this.form.subject.trim()) {
          this.error.subject = this.$t("common.required");
          ok = false;
        }
        if (!this.form.body || !this.form.body.trim()) {
          this.error.body = this.$t("common.required");
          ok = false;
        }
      } else if (this.form.enabled && this.form.body && !this.form.subject) {
        this.error.subject = this.$t("common.required");
        ok = false;
      }

      return ok;
    },
    onModalHidden() {
      if (!this.loading.setVacation) {
        this.$emit("close");
      }
    },
    setVacationValidationFailed(validationErrors) {
      this.loading.setVacation = false;
      for (const validationError of validationErrors || []) {
        const field = validationError.field || validationError.parameter;
        if (field && Object.prototype.hasOwnProperty.call(this.error, field)) {
          this.error[field] = this.$te("mailboxes." + validationError.error)
            ? this.$t("mailboxes." + validationError.error)
            : validationError.error;
        } else {
          this.error.setVacation =
            validationError.error || this.$t("error.validation_error");
        }
      }
    },
    async saveVacation() {
      if (!this.mailbox || !this.validateForm()) {
        return;
      }

      this.loading.setVacation = true;
      this.error.setVacation = "";
      const taskAction = "set-vacation";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.setVacationAborted
      );
      this.core.$root.$once(
        `${taskAction}-validation-failed-${eventId}`,
        this.setVacationValidationFailed
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.setVacationCompleted
      );

      const payload = {
        user: this.mailbox.user,
        enabled: Boolean(this.form.enabled),
        start_date: this.form.start_date || "",
        end_date: this.form.end_date || "",
        subject: this.form.subject || "",
        body: this.form.body || "",
        forward_enabled: Boolean(this.form.forward_enabled),
        forward_address: (this.form.forward_address || "").trim(),
        keep_copy: Boolean(this.form.keep_copy),
        updated_by: "cockpit-admin",
      };

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "set-vacation",
          data: payload,
          extra: {
            title: this.$t("action." + taskAction),
            description: this.mailbox.display_name || this.mailbox.user,
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];

      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.setVacation = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
        this.loading.setVacation = false;
      }
    },
    setVacationAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.setVacation = this.$t("error.generic_error");
      this.loading.setVacation = false;
    },
    setVacationCompleted(taskContext, taskResult) {
      this.loading.setVacation = false;
      this.$emit("saved", taskResult ? taskResult.output : null);
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.mg-bottom {
  margin-bottom: $spacing-05;
}

.mg-bottom-sm {
  margin-bottom: $spacing-04;
}

.date-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: $spacing-05;
}

.section-box {
  padding: $spacing-04;
  background-color: $ui-01;
  border-left: 3px solid $interactive-01;
}

.forward-fields {
  margin-top: $spacing-03;
}

.template-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: $spacing-04;
}

.template-select-col {
  flex: 1 1 15rem;
}

.template-btn-col {
  flex: 0 0 auto;
}
</style>
