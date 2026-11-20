<template>
  <NsModal
    size="large"
    :visible="visible"
    :isLoading="loading.saveSignature"
    :primaryButtonDisabled="loading.saveSignature"
    hasFormContent
    @modal-hidden="onModalHidden"
    @primary-click="saveSignature"
    @secondary-click="onModalHidden"
  >
    <template slot="label">{{ $t("signatures.modal_subtitle") }}</template>
    <template slot="title">
      {{
        signature && signature.id
          ? $t("signatures.edit_modal_title", { name: signature.name })
          : $t("signatures.create_modal_title")
      }}
    </template>
    <template slot="content">
      <cv-form @submit.prevent="saveSignature">
        <NsInlineNotification
          v-if="error.saveSignature"
          kind="error"
          :title="$t('action.save-signature')"
          :description="error.saveSignature"
          :showCloseButton="false"
          class="mg-bottom"
        />

        <div class="grid-2col mg-bottom">
          <NsTextInput
            :label="$t('signatures.name_label')"
            :placeholder="$t('signatures.name_placeholder')"
            v-model.trim="form.name"
            :invalidMessage="error.name"
            :disabled="loading.saveSignature"
            ref="name"
          />

          <NsToggle
            value="sigEnabled"
            :label="$t('signatures.enabled_label')"
            v-model="form.enabled"
            :disabled="loading.saveSignature"
          >
            <template slot="text-left">{{ $t("common.disabled") }}</template>
            <template slot="text-right">{{ $t("common.enabled") }}</template>
          </NsToggle>
        </div>

        <!-- Scope & Precedence -->
        <div class="grid-3col mg-bottom">
          <cv-select
            :label="$t('signatures.scope_type_label')"
            v-model="form.scope_type"
            :disabled="loading.saveSignature"
            @change="onScopeTypeChange"
          >
            <cv-select-option value="default">
              {{ $t("signatures.scope_default") }}
            </cv-select-option>
            <cv-select-option value="domain">
              {{ $t("signatures.scope_domain") }}
            </cv-select-option>
            <cv-select-option value="group">
              {{ $t("signatures.scope_group") }}
            </cv-select-option>
          </cv-select>

          <div v-if="form.scope_type === 'default'">
            <NsTextInput
              :label="$t('signatures.scope_value_label')"
              value="*"
              :disabled="true"
              :helperText="$t('signatures.scope_default_helper')"
            />
          </div>
          <div v-else-if="form.scope_type === 'domain'">
            <NsTextInput
              :label="$t('signatures.scope_domain_value_label')"
              :placeholder="userDomain || 'firma.de'"
              v-model.trim="form.scope_value"
              :invalidMessage="error.scope_value"
              :disabled="loading.saveSignature"
              ref="scope_value"
            />
          </div>
          <div v-else>
            <NsComboBox
              v-model="form.scope_value"
              :title="$t('signatures.scope_group_value_label')"
              :label="$t('signatures.scope_group_placeholder')"
              :invalidMessage="error.scope_value"
              :autoFilter="true"
              :autoHighlight="true"
              :acceptUserInput="true"
              :userInputLabel="$t('settings.custom_group_label')"
              :options="groupOptions"
              :disabled="loading.saveSignature"
              marginBottomOnOpen
              ref="scope_value"
            />
            <cv-checkbox
              v-if="systemGroupsCount > 0"
              value="showSigSystemGroups"
              :label="
                $t('settings.show_system_groups_label', {
                  count: systemGroupsCount,
                })
              "
              v-model="showSystemGroups"
              :disabled="loading.saveSignature"
              class="mg-top-xs"
            />
          </div>

          <NsTextInput
            type="number"
            :label="$t('signatures.priority_label')"
            :helperText="$t('signatures.priority_helper')"
            v-model="form.priority"
            :invalidMessage="error.priority"
            :disabled="loading.saveSignature"
          />
        </div>

        <!-- Reply-Chain & Placement -->
        <div class="grid-2col mg-bottom">
          <cv-select
            :label="$t('signatures.reply_mode_label')"
            v-model="form.reply_mode"
            :disabled="loading.saveSignature"
          >
            <cv-select-option value="append_once">
              {{ $t("signatures.reply_mode_append_once") }}
            </cv-select-option>
            <cv-select-option value="first_only">
              {{ $t("signatures.reply_mode_first_only") }}
            </cv-select-option>
            <cv-select-option value="always">
              {{ $t("signatures.reply_mode_always") }}
            </cv-select-option>
          </cv-select>

          <cv-select
            :label="$t('signatures.placement_label')"
            v-model="form.placement"
            :disabled="loading.saveSignature"
          >
            <cv-select-option value="below_reply">
              {{ $t("signatures.placement_below_reply") }}
            </cv-select-option>
            <cv-select-option value="bottom">
              {{ $t("signatures.placement_bottom") }}
            </cv-select-option>
          </cv-select>
        </div>

        <!-- Dynamic AD/LDAP Placeholder Chips -->
        <div class="placeholders-box mg-bottom">
          <div class="placeholders-title">
            {{ $t("signatures.ad_placeholders_title") }}
          </div>
          <div class="placeholders-hint">
            {{ $t("signatures.ad_placeholders_hint") }}
          </div>
          <div class="chips-wrap">
            <button
              v-for="chip in adPlaceholders"
              :key="chip"
              type="button"
              class="placeholder-chip"
              :disabled="loading.saveSignature"
              @click="insertPlaceholder(chip)"
            >
              <code>{{ chip }}</code>
            </button>
          </div>
        </div>

        <!-- HTML Template -->
        <div class="bx--form-item mg-bottom">
          <label class="bx--label" for="sig-html-editor">
            {{ $t("signatures.html_template_label") }}
          </label>
          <textarea
            id="sig-html-editor"
            rows="8"
            class="bx--text-area mono-textarea"
            :class="{ 'bx--text-area--invalid': !!error.html_template }"
            v-model="form.html_template"
            :disabled="loading.saveSignature"
            @focus="lastFocusedEditor = 'html'"
            ref="htmlEditor"
          ></textarea>
          <div v-if="error.html_template" class="bx--form-requirement">
            {{ error.html_template }}
          </div>
        </div>

        <!-- Plaintext Auto-Generation Toggle & Editor -->
        <NsToggle
          value="autoPlainFromHtml"
          :label="$t('signatures.auto_plain_label')"
          v-model="form.auto_plain_from_html"
          :disabled="loading.saveSignature"
          class="mg-bottom-sm"
        >
          <template slot="text-left">{{ $t("common.disabled") }}</template>
          <template slot="text-right">{{ $t("common.enabled") }}</template>
        </NsToggle>

        <div v-if="!form.auto_plain_from_html" class="bx--form-item mg-bottom">
          <label class="bx--label" for="sig-text-editor">
            {{ $t("signatures.text_template_label") }}
          </label>
          <textarea
            id="sig-text-editor"
            rows="5"
            class="bx--text-area mono-textarea"
            v-model="form.text_template"
            :disabled="loading.saveSignature"
            @focus="lastFocusedEditor = 'text'"
            ref="textEditor"
          ></textarea>
        </div>

        <!-- Live Preview Panel -->
        <div class="preview-section mg-bottom">
          <div class="preview-header">
            <div class="preview-title">
              {{ $t("signatures.preview_title") }}
            </div>
            <div class="preview-controls">
              <div class="preview-user-select">
                <cv-select
                  :label="$t('signatures.preview_user_label')"
                  v-model="previewUser"
                  :disabled="loading.previewSignature"
                >
                  <cv-select-option
                    v-for="mb in mailboxes"
                    :key="mb.user"
                    :value="mb.user"
                  >
                    {{ mb.display_name || mb.user }} ({{ mb.mail || mb.user }})
                  </cv-select-option>
                </cv-select>
              </div>
              <NsButton
                type="button"
                kind="secondary"
                size="field"
                :icon="View20"
                :loading="loading.previewSignature"
                :disabled="loading.previewSignature || !previewUser"
                @click="loadPreview"
              >
                {{ $t("signatures.load_preview_btn") }}
              </NsButton>
            </div>
          </div>

          <NsInlineNotification
            v-if="error.previewSignature"
            kind="error"
            :title="$t('action.preview-signature')"
            :description="error.previewSignature"
            :showCloseButton="false"
            class="mg-top-sm"
          />

          <div v-if="previewResult" class="preview-panes mg-top-sm">
            <div class="preview-pane">
              <div class="pane-caption">
                {{ $t("signatures.preview_html_caption") }}
              </div>
              <iframe
                sandbox=""
                :srcdoc="previewHtmlSrcdoc"
                class="html-preview-frame"
                :title="$t('signatures.preview_html_caption')"
              ></iframe>
            </div>
            <div class="preview-pane">
              <div class="pane-caption">
                {{ $t("signatures.preview_text_caption") }}
              </div>
              <pre class="text-preview-box">{{
                previewResult.rendered_text || "-"
              }}</pre>
            </div>
          </div>
        </div>
      </cv-form>
    </template>
    <template slot="secondary-button">{{ $t("common.cancel") }}</template>
    <template slot="primary-button">{{ $t("signatures.save_btn") }}</template>
  </NsModal>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import { TaskService, UtilService } from "@nethserver/ns8-ui-lib";
import View20 from "@carbon/icons-vue/es/view/20";

const DEFAULT_HTML_SIGNATURE = `<div style="font-family: Arial, sans-serif; font-size: 13px; color: #262626; border-top: 1px solid #d0d0d0; padding-top: 10px; margin-top: 14px;">
  <div><strong>{{displayName}}</strong></div>
  <div>{{title}}</div>
  <div>{{department}}</div>
  <div>{{company}}</div>
  <div>Tel.: {{telephoneNumber}}</div>
  {{#mobile}}<div>Mobil: {{mobile}}</div>{{/mobile}}
  <div>E-Mail: <a href="mailto:{{mail}}">{{mail}}</a></div>
</div>`;

export default {
  name: "EditSignatureModal",
  mixins: [TaskService, UtilService],
  props: {
    visible: {
      type: Boolean,
      default: false,
    },
    signature: {
      type: Object,
      default: null,
    },
    availableGroups: {
      type: Array,
      default: () => [],
    },
    userDomain: {
      type: String,
      default: "",
    },
    mailboxes: {
      type: Array,
      default: () => [],
    },
    autoRunPreview: {
      type: Boolean,
      default: false,
    },
  },
  data() {
    return {
      View20,
      adPlaceholders: [
        "{{givenName}}",
        "{{sn}}",
        "{{displayName}}",
        "{{mail}}",
        "{{telephoneNumber}}",
        "{{mobile}}",
        "{{title}}",
        "{{department}}",
        "{{company}}",
        "{{#mobile}}Mobil: {{mobile}}{{/mobile}}",
      ],
      lastFocusedEditor: "html",
      showSystemGroups: false,
      previewUser: "",
      previewResult: null,
      form: {
        id: null,
        name: "",
        scope_type: "default",
        scope_value: "*",
        priority: "10",
        enabled: true,
        html_template: DEFAULT_HTML_SIGNATURE,
        text_template: "",
        auto_plain_from_html: true,
        reply_mode: "append_once",
        placement: "below_reply",
      },
      loading: {
        saveSignature: false,
        previewSignature: false,
      },
      error: {
        saveSignature: "",
        previewSignature: "",
        name: "",
        scope_value: "",
        priority: "",
        html_template: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core"]),
    systemGroupsCount() {
      return this.availableGroups.filter(
        (item) => item && typeof item === "object" && item.is_system === true
      ).length;
    },
    groupOptions() {
      const opts = [];
      const seen = new Set();
      for (const item of this.availableGroups) {
        const g = typeof item === "string" ? item : item.group;
        if (!g || seen.has(g)) {
          continue;
        }
        const isSys = Boolean(item && item.is_system === true);
        if (isSys && !this.showSystemGroups && this.form.scope_value !== g) {
          continue;
        }
        seen.add(g);
        const hasCount =
          item && item.member_count !== null && item.member_count !== undefined;
        const countPart = hasCount ? ` (${item.member_count})` : "";
        const descPart =
          item && item.description ? ` — ${item.description}` : "";
        opts.push({
          name: g,
          label: `${g}${countPart}${descPart}`,
          value: g,
        });
      }
      if (
        this.form.scope_type === "group" &&
        this.form.scope_value &&
        !seen.has(this.form.scope_value)
      ) {
        opts.push({
          name: this.form.scope_value,
          label: this.form.scope_value,
          value: this.form.scope_value,
        });
      }
      return opts;
    },
    previewHtmlSrcdoc() {
      const html =
        (this.previewResult && this.previewResult.rendered_html) || "";
      return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:12px;font-family:Arial,sans-serif;font-size:13px;color:#161616;background:#fff;}</style></head><body>${html}</body></html>`;
    },
  },
  watch: {
    visible(isOpen) {
      if (isOpen) {
        this.initForm();
      }
    },
    signature() {
      if (this.visible) {
        this.initForm();
      }
    },
    mailboxes: {
      immediate: true,
      handler(list) {
        if (!this.previewUser && Array.isArray(list) && list.length) {
          this.previewUser = list[0].user;
        }
      },
    },
  },
  methods: {
    initForm() {
      this.clearModalErrors();
      this.previewResult = null;
      if (this.signature) {
        const hasCustomText = Boolean(
          this.signature.text_template &&
            this.signature.auto_plain_from_html === false
        );
        this.form = {
          id: this.signature.id !== undefined ? this.signature.id : null,
          name: this.signature.name || "",
          scope_type: this.signature.scope_type || "default",
          scope_value:
            this.signature.scope_type === "default"
              ? "*"
              : this.signature.scope_value || "",
          priority: String(
            this.signature.priority !== undefined ? this.signature.priority : 10
          ),
          enabled:
            this.signature.enabled !== undefined
              ? Boolean(this.signature.enabled)
              : true,
          html_template: this.signature.html_template || DEFAULT_HTML_SIGNATURE,
          text_template: this.signature.text_template || "",
          auto_plain_from_html: !hasCustomText,
          reply_mode: this.signature.reply_mode || "append_once",
          placement: this.signature.placement || "below_reply",
        };
      } else {
        this.form = {
          id: null,
          name: "",
          scope_type: "default",
          scope_value: "*",
          priority: "10",
          enabled: true,
          html_template: DEFAULT_HTML_SIGNATURE,
          text_template: "",
          auto_plain_from_html: true,
          reply_mode: "append_once",
          placement: "below_reply",
        };
      }

      if (!this.previewUser && this.mailboxes.length) {
        this.previewUser = this.mailboxes[0].user;
      }
      if (this.autoRunPreview && this.previewUser) {
        this.$nextTick(() => {
          this.loadPreview();
        });
      }
    },
    onScopeTypeChange() {
      if (this.form.scope_type === "default") {
        this.form.scope_value = "*";
      } else if (this.form.scope_type === "domain") {
        this.form.scope_value = this.userDomain || "";
      } else {
        this.form.scope_value = "";
      }
    },
    clearModalErrors() {
      this.error = {
        saveSignature: "",
        previewSignature: "",
        name: "",
        scope_value: "",
        priority: "",
        html_template: "",
      };
    },
    insertPlaceholder(chip) {
      if (
        this.lastFocusedEditor === "text" &&
        !this.form.auto_plain_from_html
      ) {
        const el = this.$refs.textEditor;
        const current = this.form.text_template || "";
        if (el && typeof el.selectionStart === "number") {
          const start = el.selectionStart;
          const end = el.selectionEnd;
          this.form.text_template =
            current.slice(0, start) + chip + current.slice(end);
        } else {
          this.form.text_template = current + chip;
        }
        return;
      }

      const el = this.$refs.htmlEditor;
      const current = this.form.html_template || "";
      if (el && typeof el.selectionStart === "number") {
        const start = el.selectionStart;
        const end = el.selectionEnd;
        this.form.html_template =
          current.slice(0, start) + chip + current.slice(end);
        this.$nextTick(() => {
          el.focus();
          const pos = start + chip.length;
          el.setSelectionRange(pos, pos);
        });
      } else {
        this.form.html_template = current + chip;
      }
    },
    validateForm() {
      this.clearModalErrors();
      let ok = true;

      if (!this.form.name || !this.form.name.trim()) {
        this.error.name = this.$t("common.required");
        ok = false;
      }
      if (
        this.form.scope_type !== "default" &&
        (!this.form.scope_value || !this.form.scope_value.trim())
      ) {
        this.error.scope_value = this.$t("common.required");
        ok = false;
      }
      const prio = Number(this.form.priority);
      if (isNaN(prio) || prio < 0) {
        this.error.priority = this.$t("signatures.invalid_priority");
        ok = false;
      }
      if (!this.form.html_template || !this.form.html_template.trim()) {
        this.error.html_template = this.$t("common.required");
        ok = false;
      }

      return ok;
    },
    async loadPreview() {
      if (!this.previewUser) {
        return;
      }
      this.loading.previewSignature = true;
      this.error.previewSignature = "";
      const taskAction = "preview-signature";
      const eventId = this.getUuid();

      const selectedMb = this.mailboxes.find(
        (m) => m.user === this.previewUser
      );
      const senderEmail =
        (selectedMb && selectedMb.mail) ||
        (this.userDomain
          ? `${this.previewUser}@${this.userDomain}`
          : this.previewUser);

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        (taskResult, taskContext) => {
          console.error(`${taskContext.action} aborted`, taskResult);
          this.error.previewSignature = this.$t("error.generic_error");
          this.loading.previewSignature = false;
        }
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.previewResult = (taskResult && taskResult.output) || null;
          this.loading.previewSignature = false;
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "preview-signature",
          data: {
            user: this.previewUser,
            sender_email: senderEmail,
            html_template: this.form.html_template,
            text_template: this.form.auto_plain_from_html
              ? ""
              : this.form.text_template,
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
        this.error.previewSignature = this.getErrorMessage(err);
        this.loading.previewSignature = false;
      }
    },
    onModalHidden() {
      if (!this.loading.saveSignature) {
        this.$emit("close");
      }
    },
    async saveSignature() {
      if (!this.validateForm()) {
        return;
      }

      this.loading.saveSignature = true;
      this.error.saveSignature = "";
      const taskAction = "save-signature";
      const eventId = this.getUuid();

      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        (taskResult, taskContext) => {
          console.error(`${taskContext.action} aborted`, taskResult);
          this.error.saveSignature = this.$t("error.generic_error");
          this.loading.saveSignature = false;
        }
      );
      this.core.$root.$once(
        `${taskAction}-validation-failed-${eventId}`,
        (validationErrors) => {
          this.loading.saveSignature = false;
          const first = (validationErrors || [])[0];
          this.error.saveSignature = first
            ? first.error || this.$t("error.validation_error")
            : this.$t("error.validation_error");
        }
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.loading.saveSignature = false;
          this.$emit("saved", taskResult ? taskResult.output : null);
        }
      );

      const payload = {
        id: this.form.id !== null ? Number(this.form.id) : null,
        name: this.form.name.trim(),
        scope_type: this.form.scope_type,
        scope_value:
          this.form.scope_type === "default"
            ? "*"
            : this.form.scope_value.trim(),
        priority: Number(this.form.priority),
        enabled: Boolean(this.form.enabled),
        html_template: this.form.html_template,
        text_template: this.form.auto_plain_from_html
          ? ""
          : this.form.text_template,
        auto_plain_from_html: Boolean(this.form.auto_plain_from_html),
        reply_mode: this.form.reply_mode,
        placement: this.form.placement,
      };

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "save-signature",
          data: payload,
          extra: {
            title: this.$t("action." + taskAction),
            description: payload.name,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.error.saveSignature = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
        this.loading.saveSignature = false;
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

.mg-bottom-sm {
  margin-bottom: $spacing-03;
}

.mg-top-sm {
  margin-top: $spacing-03;
}

.mg-top-xs {
  margin-top: $spacing-02;
}

.grid-2col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
  gap: $spacing-05;
}

.grid-3col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: $spacing-05;
}

.mono-textarea {
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.8125rem;
}

.placeholders-box,
.preview-section {
  padding: $spacing-04;
  background-color: $ui-01;
}

.placeholders-title,
.preview-title {
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
  padding: 0.2rem 0.6rem;
  font-size: 0.75rem;
  cursor: pointer;

  &:hover:not(:disabled) {
    background-color: $hover-ui;
    border-color: $interactive-01;
  }
}

.preview-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-end;
  gap: $spacing-04;
}

.preview-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: $spacing-03;
}

.preview-user-select {
  min-width: 16rem;
}

.preview-panes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: $spacing-04;
}

.pane-caption {
  font-size: 0.75rem;
  font-weight: 600;
  color: $text-02;
  margin-bottom: $spacing-02;
}

.html-preview-frame {
  width: 100%;
  min-height: 10rem;
  border: 1px solid $ui-03;
  background: #ffffff;
}

.text-preview-box {
  margin: 0;
  padding: $spacing-03;
  min-height: 10rem;
  border: 1px solid $ui-03;
  background: $ui-02;
  font-size: 0.75rem;
  white-space: pre-wrap;
  overflow-x: auto;
}
</style>
