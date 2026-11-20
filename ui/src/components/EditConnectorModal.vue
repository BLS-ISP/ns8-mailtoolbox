<template>
  <NsModal
    size="default"
    :visible="visible"
    :isLoading="loading.saveConnector"
    :primaryButtonDisabled="loading.saveConnector"
    hasFormContent
    @modal-hidden="onModalHidden"
    @primary-click="saveConnector"
    @secondary-click="onModalHidden"
  >
    <template slot="label">{{ $t("connectors.modal_subtitle") }}</template>
    <template slot="title">
      {{
        connector && connector.id
          ? $t("connectors.edit_modal_title", { name: connector.name })
          : $t("connectors.create_modal_title")
      }}
    </template>
    <template slot="content">
      <cv-form @submit.prevent="saveConnector">
        <NsInlineNotification
          v-if="error.saveConnector"
          kind="error"
          :title="$t('action.save-connector')"
          :description="error.saveConnector"
          :showCloseButton="false"
          class="mg-bottom"
        />

        <NsInlineNotification
          v-if="testResult"
          :kind="testResult.ok ? 'success' : 'error'"
          :title="
            testResult.ok
              ? $t('connectors.test_ok_title')
              : $t('connectors.test_failed_title')
          "
          :description="testResult.message"
          @close="testResult = null"
          class="mg-bottom"
        />

        <div class="grid-2col mg-bottom">
          <NsTextInput
            :label="$t('connectors.name_label')"
            :placeholder="$t('connectors.name_placeholder')"
            v-model.trim="form.name"
            :invalidMessage="error.name"
            :disabled="loading.saveConnector"
            ref="name"
          />

          <cv-select
            :label="$t('connectors.preset_label')"
            v-model="selectedPreset"
            :disabled="loading.saveConnector"
            @change="applyProviderPreset"
          >
            <cv-select-option value="custom">
              {{ $t("connectors.preset_custom") }}
            </cv-select-option>
            <cv-select-option value="strato">
              Strato IMAPS (imap.strato.de:993)
            </cv-select-option>
            <cv-select-option value="ionos">
              IONOS IMAPS (imap.ionos.de:993)
            </cv-select-option>
            <cv-select-option value="telekom">
              Telekom IMAPS (secureimap.t-online.de:993)
            </cv-select-option>
          </cv-select>
        </div>

        <!-- Target Local Mailbox -->
        <NsComboBox
          v-model="selectedTargetMailbox"
          :title="$t('connectors.target_mailbox_label')"
          :label="$t('connectors.target_mailbox_placeholder')"
          :invalidMessage="error.target_email"
          :autoFilter="true"
          :autoHighlight="true"
          :options="mailboxOptions"
          :disabled="loading.saveConnector"
          marginBottomOnOpen
          class="mg-bottom"
          @change="onTargetMailboxChange"
          ref="target_email"
        />

        <!-- Protocol, Host, Port, SSL -->
        <div class="grid-3col mg-bottom">
          <cv-select
            :label="$t('connectors.protocol_label')"
            v-model="form.protocol"
            :disabled="loading.saveConnector"
            @change="onProtocolChange"
          >
            <cv-select-option value="imaps">IMAPS (SSL/TLS)</cv-select-option>
            <cv-select-option value="imap">IMAP</cv-select-option>
            <cv-select-option value="pop3s">POP3S (SSL/TLS)</cv-select-option>
            <cv-select-option value="pop3">POP3</cv-select-option>
          </cv-select>

          <NsTextInput
            :label="$t('connectors.host_label')"
            placeholder="imap.example.com"
            v-model.trim="form.host"
            :invalidMessage="error.host"
            :disabled="loading.saveConnector"
            ref="host"
          />

          <NsTextInput
            type="number"
            :label="$t('connectors.port_label')"
            v-model="form.port"
            :invalidMessage="error.port"
            :disabled="loading.saveConnector"
          />
        </div>

        <!-- Remote Credentials -->
        <div class="grid-2col mg-bottom">
          <NsTextInput
            :label="$t('connectors.remote_user_label')"
            placeholder="info@example.com"
            v-model.trim="form.remote_user"
            :invalidMessage="error.remote_user"
            :disabled="loading.saveConnector"
            ref="remote_user"
          />

          <NsTextInput
            type="password"
            :label="$t('connectors.remote_password_label')"
            :placeholder="
              hasSavedPassword
                ? $t('connectors.password_keep_placeholder')
                : $t('connectors.password_enter_placeholder')
            "
            :helperText="
              hasSavedPassword ? $t('connectors.password_keep_helper') : ''
            "
            v-model="form.remote_password"
            :invalidMessage="error.remote_password"
            :disabled="loading.saveConnector"
            ref="remote_password"
          />
        </div>

        <!-- Sync Interval & Toggles -->
        <div class="grid-3col mg-bottom">
          <NsTextInput
            type="number"
            :label="$t('connectors.interval_label')"
            v-model="form.interval_minutes"
            :invalidMessage="error.interval_minutes"
            :disabled="loading.saveConnector"
          />

          <NsToggle
            value="leaveOnServer"
            :label="$t('connectors.leave_on_server_label')"
            v-model="form.leave_on_server"
            :disabled="loading.saveConnector"
          >
            <template slot="text-left">{{ $t("common.disabled") }}</template>
            <template slot="text-right">{{ $t("common.enabled") }}</template>
          </NsToggle>

          <NsToggle
            value="connectorEnabled"
            :label="$t('connectors.enabled_label')"
            v-model="form.enabled"
            :disabled="loading.saveConnector"
          >
            <template slot="text-left">{{ $t("common.disabled") }}</template>
            <template slot="text-right">{{ $t("common.enabled") }}</template>
          </NsToggle>
        </div>

        <NsButton
          type="button"
          kind="tertiary"
          size="field"
          :icon="Checkmark20"
          :loading="loading.testConnector"
          :disabled="loading.testConnector || loading.saveConnector"
          @click="testConnection"
        >
          {{ $t("connectors.test_connection_btn") }}
        </NsButton>
      </cv-form>
    </template>
    <template slot="secondary-button">{{ $t("common.cancel") }}</template>
    <template slot="primary-button">{{ $t("connectors.save_btn") }}</template>
  </NsModal>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import { TaskService, UtilService } from "@nethserver/ns8-ui-lib";
import Checkmark20 from "@carbon/icons-vue/es/checkmark/20";

export default {
  name: "EditConnectorModal",
  mixins: [TaskService, UtilService],
  props: {
    visible: {
      type: Boolean,
      default: false,
    },
    connector: {
      type: Object,
      default: null,
    },
    mailboxes: {
      type: Array,
      default: () => [],
    },
  },
  data() {
    return {
      Checkmark20,
      selectedPreset: "custom",
      selectedTargetMailbox: "",
      testResult: null,
      form: {
        id: null,
        name: "",
        target_user: "",
        target_email: "",
        protocol: "imaps",
        host: "",
        port: "993",
        use_ssl: true,
        remote_user: "",
        remote_password: "",
        interval_minutes: "5",
        leave_on_server: true,
        enabled: true,
      },
      loading: {
        saveConnector: false,
        testConnector: false,
      },
      error: {
        saveConnector: "",
        name: "",
        target_email: "",
        host: "",
        port: "",
        remote_user: "",
        remote_password: "",
        interval_minutes: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core"]),
    hasSavedPassword() {
      return Boolean(
        this.connector && this.connector.id && this.connector.password_set
      );
    },
    mailboxOptions() {
      const opts = [];
      for (const mb of this.mailboxes) {
        if (!mb.mail) {
          continue;
        }
        const labelName = mb.display_name || mb.user;
        opts.push({
          name: mb.user,
          label: `${labelName} — ${mb.mail}`,
          value: mb.mail,
        });
      }
      if (
        this.form.target_email &&
        !opts.some((o) => o.value === this.form.target_email)
      ) {
        opts.push({
          name: this.form.target_user || this.form.target_email,
          label: this.form.target_email,
          value: this.form.target_email,
        });
      }
      return opts;
    },
  },
  watch: {
    visible(isOpen) {
      if (isOpen) {
        this.initForm();
      }
    },
    connector() {
      if (this.visible) {
        this.initForm();
      }
    },
  },
  methods: {
    initForm() {
      this.clearModalErrors();
      this.testResult = null;
      this.selectedPreset = "custom";
      if (this.connector) {
        this.form = {
          id: this.connector.id !== undefined ? this.connector.id : null,
          name: this.connector.name || "",
          target_user: this.connector.target_user || "",
          target_email: this.connector.target_email || "",
          protocol: this.connector.protocol || "imaps",
          host: this.connector.host || "",
          port: String(this.connector.port || 993),
          use_ssl:
            this.connector.use_ssl !== undefined
              ? Boolean(this.connector.use_ssl)
              : true,
          remote_user: this.connector.remote_user || "",
          remote_password: "",
          interval_minutes: String(this.connector.interval_minutes || 5),
          leave_on_server:
            this.connector.leave_on_server !== undefined
              ? Boolean(this.connector.leave_on_server)
              : true,
          enabled:
            this.connector.enabled !== undefined
              ? Boolean(this.connector.enabled)
              : true,
        };
        this.selectedTargetMailbox = this.form.target_email;
      } else {
        const defaultMb = this.mailboxes[0] || null;
        this.form = {
          id: null,
          name: "",
          target_user: defaultMb ? defaultMb.user : "",
          target_email: defaultMb ? defaultMb.mail : "",
          protocol: "imaps",
          host: "",
          port: "993",
          use_ssl: true,
          remote_user: "",
          remote_password: "",
          interval_minutes: "5",
          leave_on_server: true,
          enabled: true,
        };
        this.selectedTargetMailbox = this.form.target_email;
      }
    },
    applyProviderPreset() {
      const presets = {
        strato: {
          host: "imap.strato.de",
          port: "993",
          protocol: "imaps",
          use_ssl: true,
        },
        ionos: {
          host: "imap.ionos.de",
          port: "993",
          protocol: "imaps",
          use_ssl: true,
        },
        telekom: {
          host: "secureimap.t-online.de",
          port: "993",
          protocol: "imaps",
          use_ssl: true,
        },
      };
      const preset = presets[this.selectedPreset];
      if (preset) {
        this.form.host = preset.host;
        this.form.port = preset.port;
        this.form.protocol = preset.protocol;
        this.form.use_ssl = preset.use_ssl;
      }
    },
    onProtocolChange() {
      const defaultPorts = {
        imaps: { port: "993", ssl: true },
        imap: { port: "143", ssl: false },
        pop3s: { port: "995", ssl: true },
        pop3: { port: "110", ssl: false },
      };
      const cfg = defaultPorts[this.form.protocol];
      if (cfg) {
        this.form.port = cfg.port;
        this.form.use_ssl = cfg.ssl;
      }
    },
    onTargetMailboxChange(emailVal) {
      this.form.target_email = emailVal || "";
      const found = this.mailboxes.find((m) => m.mail === emailVal);
      if (found) {
        this.form.target_user = found.user;
      } else if (emailVal && emailVal.includes("@")) {
        this.form.target_user = emailVal.split("@")[0];
      }
    },
    clearModalErrors() {
      this.error = {
        saveConnector: "",
        name: "",
        target_email: "",
        host: "",
        port: "",
        remote_user: "",
        remote_password: "",
        interval_minutes: "",
      };
    },
    validateForm(forTestOnly = false) {
      this.clearModalErrors();
      let ok = true;

      if (!forTestOnly && (!this.form.name || !this.form.name.trim())) {
        this.error.name = this.$t("common.required");
        ok = false;
      }
      if (
        !forTestOnly &&
        (!this.form.target_email || !this.form.target_email.trim())
      ) {
        this.error.target_email = this.$t("common.required");
        ok = false;
      }
      if (!this.form.host || !this.form.host.trim()) {
        this.error.host = this.$t("common.required");
        ok = false;
      }
      const p = Number(this.form.port);
      if (!p || p < 1 || p > 65535) {
        this.error.port = this.$t("connectors.invalid_port");
        ok = false;
      }
      if (!this.form.remote_user || !this.form.remote_user.trim()) {
        this.error.remote_user = this.$t("common.required");
        ok = false;
      }
      if (!this.hasSavedPassword && !this.form.remote_password) {
        this.error.remote_password = this.$t("common.required");
        ok = false;
      }
      if (!forTestOnly) {
        const interval = Number(this.form.interval_minutes);
        if (!interval || interval < 1) {
          this.error.interval_minutes = this.$t("connectors.invalid_interval");
          ok = false;
        }
      }

      return ok;
    },
    async testConnection() {
      if (!this.validateForm(true)) {
        return;
      }
      this.loading.testConnector = true;
      this.testResult = null;
      const taskAction = "test-connector";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.testConnector = false;
        this.testResult = {
          ok: false,
          message: this.$t("error.generic_error"),
        };
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.loading.testConnector = false;
          const out = (taskResult && taskResult.output) || {};
          const msg = out.ok
            ? this.$t("connectors.test_ok_message", {
                count: out.message_count || 0,
              })
            : out.message || this.$t("error.generic_error");
          this.testResult = {
            ok: Boolean(out.ok),
            message: msg,
          };
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "test-connector",
          data: {
            id: this.form.id !== null ? Number(this.form.id) : null,
            protocol: this.form.protocol,
            host: this.form.host.trim(),
            port: Number(this.form.port),
            use_ssl: Boolean(this.form.use_ssl),
            remote_user: this.form.remote_user.trim(),
            remote_password: this.form.remote_password || "",
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
        this.loading.testConnector = false;
        this.testResult = {
          ok: false,
          message: this.getErrorMessage(err),
        };
      }
    },
    onModalHidden() {
      if (!this.loading.saveConnector) {
        this.$emit("close");
      }
    },
    async saveConnector() {
      if (!this.validateForm(false)) {
        return;
      }

      this.loading.saveConnector = true;
      this.error.saveConnector = "";
      const taskAction = "save-connector";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.saveConnector = false;
        this.error.saveConnector = this.$t("error.generic_error");
      });
      this.core.$root.$once(
        `${taskAction}-validation-failed-${eventId}`,
        (validationErrors) => {
          this.loading.saveConnector = false;
          const first = (validationErrors || [])[0];
          this.error.saveConnector = first
            ? first.error || this.$t("error.validation_error")
            : this.$t("error.validation_error");
        }
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.loading.saveConnector = false;
          this.$emit("saved", taskResult ? taskResult.output : null);
        }
      );

      const payload = {
        id: this.form.id !== null ? Number(this.form.id) : null,
        name: this.form.name.trim(),
        target_user:
          this.form.target_user || this.form.target_email.split("@")[0],
        target_email: this.form.target_email.trim(),
        protocol: this.form.protocol,
        host: this.form.host.trim(),
        port: Number(this.form.port),
        use_ssl: Boolean(this.form.use_ssl),
        remote_user: this.form.remote_user.trim(),
        remote_password: this.form.remote_password || "",
        interval_minutes: Number(this.form.interval_minutes),
        leave_on_server: Boolean(this.form.leave_on_server),
        enabled: Boolean(this.form.enabled),
      };

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "save-connector",
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
        this.loading.saveConnector = false;
        this.error.saveConnector = this.getErrorMessage(err);
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

.grid-2col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: $spacing-05;
}

.grid-3col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
  gap: $spacing-05;
}
</style>
