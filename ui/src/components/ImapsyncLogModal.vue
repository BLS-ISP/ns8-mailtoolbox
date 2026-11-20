<template>
  <NsModal
    size="large"
    :visible="visible"
    @modal-hidden="$emit('close')"
    @primary-click="$emit('close')"
  >
    <template slot="label">
      {{
        task
          ? `${task.imapsync_module_id} · ${task.remote_user} → ${task.localuser}`
          : ""
      }}
    </template>
    <template slot="title">
      {{ $t("connectors.imapsync_log_modal_title") }}
    </template>
    <template slot="content">
      <div class="log-toolbar mg-bottom-sm">
        <NsButton
          type="button"
          kind="secondary"
          size="small"
          :icon="Renew20"
          :loading="loading"
          :disabled="loading"
          @click="$emit('refresh')"
        >
          {{ $t("common.refresh") }}
        </NsButton>
        <NsButton
          v-if="logContent"
          type="button"
          kind="ghost"
          size="small"
          :icon="Download20"
          @click="downloadLogFile"
        >
          {{ $t("connectors.imapsync_download_log_btn") }}
        </NsButton>
      </div>

      <NsInlineNotification
        v-if="truncated"
        kind="warning"
        :title="$t('connectors.imapsync_log_truncated_title')"
        :description="$t('connectors.imapsync_log_truncated_desc')"
        :showCloseButton="false"
        class="mg-bottom-sm"
      />

      <NsInlineNotification
        v-if="errorMessage"
        kind="error"
        :title="$t('action.get-log')"
        :description="errorMessage"
        :showCloseButton="false"
        class="mg-bottom-sm"
      />

      <cv-skeleton-text
        v-if="loading"
        :paragraph="true"
        :line-count="10"
        width="95%"
      />
      <pre v-else class="imapsync-log-viewer">{{
        logContent || $t("connectors.imapsync_log_empty")
      }}</pre>
    </template>
    <template slot="primary-button">{{ $t("tracking.close_btn") }}</template>
  </NsModal>
</template>

<script>
import Renew20 from "@carbon/icons-vue/es/renew/20";
import Download20 from "@carbon/icons-vue/es/download/20";

export default {
  name: "ImapsyncLogModal",
  props: {
    visible: {
      type: Boolean,
      default: false,
    },
    task: {
      type: Object,
      default: null,
    },
    loading: {
      type: Boolean,
      default: false,
    },
    logContent: {
      type: String,
      default: "",
    },
    truncated: {
      type: Boolean,
      default: false,
    },
    errorMessage: {
      type: String,
      default: "",
    },
  },
  data() {
    return {
      Renew20,
      Download20,
    };
  },
  methods: {
    downloadLogFile() {
      if (!this.logContent || !this.task) {
        return;
      }
      const blob = new Blob([this.logContent], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `imapsync_${this.task.localuser}_${this.task.task_id}.log`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 150);
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.mg-bottom-sm {
  margin-bottom: $spacing-04;
}

.log-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-03;
}

.imapsync-log-viewer {
  margin: 0;
  padding: $spacing-04;
  max-height: 24rem;
  overflow: auto;
  background-color: #161616;
  color: #f4f4f4;
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.75rem;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
