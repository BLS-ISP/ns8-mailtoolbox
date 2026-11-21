<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("tracking.title") }}</h2>
      </cv-column>
    </cv-row>

    <cv-row>
      <cv-column>
        <NsTabs :container="false" :aria-label="$t('tracking.tabs_aria')">
          <!-- TAB 1: Fast Mail-Tracking Search (Rspamd History + SQLite Cache) -->
          <cv-tab
            id="tab-history"
            :label="$t('tracking.tab_history')"
            :selected="true"
          >
            <div class="tab-panel">
              <NsInlineNotification
                v-if="!loading.searchHistory && !rspamdConnected"
                kind="warning"
                :title="$t('tracking.rspamd_disconnected_title')"
                :description="
                  rspamdError || $t('tracking.rspamd_disconnected_description')
                "
                :actionLabel="$t('status.go_to_settings')"
                @action="goToAppPage(instanceName, 'settings')"
                :showCloseButton="false"
                class="mg-bottom"
              />

              <cv-tile light class="mg-bottom">
                <cv-form @submit.prevent="searchMailHistory">
                  <div class="filter-grid mg-bottom-sm">
                    <NsTextInput
                      :label="$t('tracking.filter_sender')"
                      :placeholder="$t('tracking.filter_sender_placeholder')"
                      v-model.trim="filters.sender"
                      :disabled="loading.searchHistory"
                    />
                    <NsTextInput
                      :label="$t('tracking.filter_recipient')"
                      :placeholder="$t('tracking.filter_recipient_placeholder')"
                      v-model.trim="filters.recipient"
                      :disabled="loading.searchHistory"
                    />
                    <NsTextInput
                      :label="$t('tracking.filter_subject')"
                      :placeholder="$t('tracking.filter_subject_placeholder')"
                      v-model.trim="filters.subject"
                      :disabled="loading.searchHistory"
                    />
                  </div>

                  <div class="filter-grid-bottom">
                    <NsTextInput
                      type="date"
                      :label="$t('tracking.filter_date_from')"
                      v-model.trim="filters.date_from"
                      :disabled="loading.searchHistory"
                    />
                    <NsTextInput
                      type="date"
                      :label="$t('tracking.filter_date_to')"
                      v-model.trim="filters.date_to"
                      :disabled="loading.searchHistory"
                    />
                    <cv-select
                      :label="$t('tracking.filter_status')"
                      v-model="filters.action_filter"
                      :disabled="loading.searchHistory"
                    >
                      <cv-select-option value="all">
                        {{ $t("tracking.status_all") }}
                      </cv-select-option>
                      <cv-select-option value="delivered">
                        {{ $t("tracking.status_delivered") }}
                      </cv-select-option>
                      <cv-select-option value="spam">
                        {{ $t("tracking.status_spam") }}
                      </cv-select-option>
                      <cv-select-option value="greylisted">
                        {{ $t("tracking.status_greylisted") }}
                      </cv-select-option>
                      <cv-select-option value="rejected">
                        {{ $t("tracking.status_rejected") }}
                      </cv-select-option>
                    </cv-select>

                    <div class="search-btn-wrap">
                      <NsButton
                        kind="primary"
                        size="field"
                        :icon="Search20"
                        :loading="loading.searchHistory"
                        :disabled="loading.searchHistory"
                      >
                        {{ $t("tracking.search_btn") }}
                      </NsButton>
                    </div>
                  </div>
                </cv-form>
              </cv-tile>

              <cv-tile light>
                <NsDataTable
                  :allRows="historyEntries"
                  :columns="i18nHistoryColumns"
                  :rawColumns="historyColumns"
                  :isSearchable="true"
                  :searchPlaceholder="$t('tracking.table_search_placeholder')"
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
                  :isLoading="loading.searchHistory"
                  :skeletonRows="6"
                  :isErrorShown="!!error.searchHistory"
                  :errorTitle="$t('action.search-mail-history')"
                  :errorDescription="error.searchHistory"
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
                  @updatePage="historyTablePage = $event"
                >
                  <template slot="empty-state">
                    <NsEmptyState :title="$t('tracking.empty_history_title')">
                      <template #description>
                        <div>
                          {{ $t("tracking.empty_history_description") }}
                        </div>
                      </template>
                    </NsEmptyState>
                  </template>
                  <template slot="data">
                    <cv-data-table-row
                      v-for="(row, idx) in historyTablePage"
                      :key="row.id || String(idx)"
                      :value="String(idx)"
                    >
                      <cv-data-table-cell class="nowrap-cell">
                        {{ row.timestamp || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell class="break-cell">
                        {{ row.sender || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell class="break-cell">
                        {{
                          Array.isArray(row.recipients) && row.recipients.length
                            ? row.recipients.join(", ")
                            : "-"
                        }}
                      </cv-data-table-cell>
                      <cv-data-table-cell class="break-cell">
                        {{ row.subject || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        <NsTag
                          :label="formatHistoryStatus(row.status)"
                          :kind="historyStatusKind(row.status)"
                          size="sm"
                        />
                      </cv-data-table-cell>
                      <cv-data-table-cell class="nowrap-cell">
                        {{ formatScore(row.score, row.required_score) }}
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        <NsButton
                          kind="ghost"
                          size="small"
                          :icon="View20"
                          @click="openHistoryDetail(row)"
                        >
                          {{ $t("tracking.details_btn") }}
                        </NsButton>
                      </cv-data-table-cell>
                    </cv-data-table-row>
                  </template>
                </NsDataTable>
              </cv-tile>
            </div>
          </cv-tab>

          <!-- TAB 2: Spam Quarantine, Daily Digest & Sender Allowlist -->
          <cv-tab id="tab-quarantine" :label="$t('tracking.tab_quarantine')">
            <div class="tab-panel">
              <NsInlineNotification
                v-if="error.quarantineAction"
                kind="error"
                :title="$t('error.error')"
                :description="error.quarantineAction"
                :showCloseButton="false"
                class="mg-bottom"
              />

              <NsInlineNotification
                v-if="quarantineNotice"
                kind="success"
                :title="$t('common.success')"
                :description="quarantineNotice"
                @close="quarantineNotice = ''"
                class="mg-bottom"
              />

              <!-- Digest Status Strip -->
              <cv-tile light class="mg-bottom">
                <div class="digest-bar">
                  <div>
                    <h4>{{ $t("tracking.digest_card_title") }}</h4>
                    <div class="digest-meta">
                      <NsTag
                        :label="
                          digestStatus.enabled
                            ? $t('tracking.digest_enabled_badge', {
                                time: digestStatus.digest_time || '07:30',
                              })
                            : $t('tracking.digest_disabled_badge')
                        "
                        :kind="digestStatus.enabled ? 'green' : 'cool-gray'"
                        size="sm"
                      />
                      <span v-if="digestStatus.last_run_at" class="meta-span">
                        {{
                          $t("tracking.digest_last_run", {
                            time: digestStatus.last_run_at,
                            sent: digestStatus.last_digests_sent || 0,
                          })
                        }}
                      </span>
                    </div>
                  </div>
                  <div class="digest-actions">
                    <NsButton
                      kind="tertiary"
                      size="field"
                      :icon="SendAlt20"
                      :loading="loading.runDigest"
                      :disabled="loading.runDigest"
                      @click="runDigestNow"
                    >
                      {{ $t("tracking.run_digest_now_btn") }}
                    </NsButton>
                    <NsButton
                      kind="secondary"
                      size="field"
                      :icon="Renew20"
                      :loading="loading.listQuarantine"
                      :disabled="loading.listQuarantine"
                      @click="listQuarantine"
                    >
                      {{ $t("common.refresh") }}
                    </NsButton>
                  </div>
                </div>
              </cv-tile>

              <!-- Quarantined Junk Messages Table -->
              <cv-tile light class="mg-bottom">
                <NsDataTable
                  :allRows="quarantineItems"
                  :columns="i18nQuarantineColumns"
                  :rawColumns="quarantineColumns"
                  :isSearchable="true"
                  :searchPlaceholder="
                    $t('tracking.quarantine_search_placeholder')
                  "
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
                  :isLoading="loading.listQuarantine"
                  :skeletonRows="5"
                  :isErrorShown="!!error.listQuarantine"
                  :errorTitle="$t('action.list-quarantine')"
                  :errorDescription="error.listQuarantine"
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
                  @updatePage="quarantineTablePage = $event"
                >
                  <template slot="empty-state">
                    <NsEmptyState
                      :title="$t('tracking.empty_quarantine_title')"
                    >
                      <template #description>
                        <div>
                          {{ $t("tracking.empty_quarantine_description") }}
                        </div>
                      </template>
                    </NsEmptyState>
                  </template>
                  <template slot="data">
                    <cv-data-table-row
                      v-for="(item, idx) in quarantineTablePage"
                      :key="item.id || String(idx)"
                      :value="String(idx)"
                    >
                      <cv-data-table-cell class="break-cell">
                        <div>{{ item.recipient || item.user }}</div>
                        <div class="sub-note">{{ item.user }}</div>
                      </cv-data-table-cell>
                      <cv-data-table-cell class="break-cell">
                        {{ item.sender || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell class="break-cell">
                        {{ item.subject || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell class="nowrap-cell">
                        {{ item.received_at || "-" }}
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        <NsTag
                          v-if="
                            item.spam_score !== null &&
                            item.spam_score !== undefined
                          "
                          :label="String(item.spam_score)"
                          kind="warm-gray"
                          size="sm"
                        />
                        <span v-else>-</span>
                      </cv-data-table-cell>
                      <cv-data-table-cell>
                        <div class="row-actions">
                          <NsButton
                            kind="ghost"
                            size="small"
                            :icon="Checkmark20"
                            :disabled="releasingId === item.id"
                            @click="releaseQuarantineItem(item, false)"
                          >
                            {{ $t("tracking.release_btn") }}
                          </NsButton>
                          <NsButton
                            kind="ghost"
                            size="small"
                            :icon="Checkmark20"
                            :disabled="releasingId === item.id"
                            @click="releaseQuarantineItem(item, true)"
                          >
                            {{ $t("tracking.release_and_allow_btn") }}
                          </NsButton>
                        </div>
                      </cv-data-table-cell>
                    </cv-data-table-row>
                  </template>
                </NsDataTable>
              </cv-tile>

              <!-- Collapsible Sender Allowlist -->
              <cv-tile light>
                <cv-accordion>
                  <cv-accordion-item :open="false">
                    <template slot="title">
                      {{
                        $t("tracking.allowlist_accordion_title", {
                          count: allowlistEntries.length,
                        })
                      }}
                    </template>
                    <template slot="content">
                      <div v-if="!allowlistEntries.length" class="sub-note">
                        {{ $t("tracking.allowlist_empty") }}
                      </div>
                      <cv-structured-list v-else>
                        <template slot="headings">
                          <cv-structured-list-heading>{{
                            $t("tracking.col_user")
                          }}</cv-structured-list-heading>
                          <cv-structured-list-heading>{{
                            $t("tracking.col_sender")
                          }}</cv-structured-list-heading>
                          <cv-structured-list-heading>{{
                            $t("templates.col_timestamp")
                          }}</cv-structured-list-heading>
                          <cv-structured-list-heading>{{
                            $t("mailboxes.col_actions")
                          }}</cv-structured-list-heading>
                        </template>
                        <template slot="items">
                          <cv-structured-list-item
                            v-for="al in allowlistEntries"
                            :key="al.id"
                          >
                            <cv-structured-list-data>{{
                              al.user
                            }}</cv-structured-list-data>
                            <cv-structured-list-data>{{
                              al.sender_email
                            }}</cv-structured-list-data>
                            <cv-structured-list-data>{{
                              al.created_at || "-"
                            }}</cv-structured-list-data>
                            <cv-structured-list-data>
                              <NsButton
                                kind="danger--ghost"
                                size="small"
                                :icon="TrashCan20"
                                :disabled="deletingAllowlistId === al.id"
                                @click="deleteAllowlistEntry(al)"
                              >
                                {{ $t("signatures.delete_action") }}
                              </NsButton>
                            </cv-structured-list-data>
                          </cv-structured-list-item>
                        </template>
                      </cv-structured-list>
                    </template>
                  </cv-accordion-item>
                </cv-accordion>
              </cv-tile>
            </div>
          </cv-tab>
        </NsTabs>
      </cv-column>
    </cv-row>

    <!-- Mail History Details Modal -->
    <NsModal
      size="default"
      :visible="!!selectedHistoryEntry"
      @modal-hidden="selectedHistoryEntry = null"
      @primary-click="selectedHistoryEntry = null"
    >
      <template slot="label">{{
        selectedHistoryEntry ? selectedHistoryEntry.timestamp : ""
      }}</template>
      <template slot="title">{{ $t("tracking.detail_modal_title") }}</template>
      <template slot="content">
        <div v-if="selectedHistoryEntry" class="detail-list">
          <div class="detail-row">
            <span class="detail-key">{{ $t("tracking.col_sender") }}:</span>
            <span>{{ selectedHistoryEntry.sender || "-" }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-key">{{ $t("tracking.col_recipients") }}:</span>
            <span>{{
              Array.isArray(selectedHistoryEntry.recipients)
                ? selectedHistoryEntry.recipients.join(", ")
                : "-"
            }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-key">{{ $t("tracking.col_subject") }}:</span>
            <span>{{ selectedHistoryEntry.subject || "-" }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-key"
              >{{ $t("tracking.detail_rspamd_action") }}:</span
            >
            <code>{{ selectedHistoryEntry.rspamd_action || "-" }}</code>
          </div>
          <div class="detail-row">
            <span class="detail-key">{{ $t("tracking.col_score") }}:</span>
            <span>{{
              formatScore(
                selectedHistoryEntry.score,
                selectedHistoryEntry.required_score
              )
            }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-key">{{ $t("tracking.detail_ip") }}:</span>
            <code>{{ selectedHistoryEntry.ip || "-" }}</code>
          </div>
          <div class="detail-row">
            <span class="detail-key"
              >{{ $t("tracking.detail_message_id") }}:</span
            >
            <code class="break-cell">{{
              selectedHistoryEntry.message_id || "-"
            }}</code>
          </div>
          <div class="detail-row">
            <span class="detail-key">{{ $t("status.size") }}:</span>
            <span
              v-if="
                selectedHistoryEntry.size_bytes !== undefined &&
                !isNaN(selectedHistoryEntry.size_bytes)
              "
              >{{ selectedHistoryEntry.size_bytes | byteFormat }}</span
            >
            <span v-else>-</span>
          </div>
          <div class="detail-symbols">
            <div class="detail-key mg-bottom-xs">
              {{ $t("tracking.detail_symbols") }}:
            </div>
            <div
              v-if="
                selectedHistoryEntry.symbols &&
                selectedHistoryEntry.symbols.length
              "
              class="symbols-wrap"
            >
              <NsTag
                v-for="(sym, sIdx) in selectedHistoryEntry.symbols"
                :key="sIdx"
                :label="sym"
                kind="cool-gray"
                size="sm"
              />
            </div>
            <span v-else>-</span>
          </div>
        </div>
      </template>
      <template slot="primary-button">{{ $t("tracking.close_btn") }}</template>
    </NsModal>
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
import Search20 from "@carbon/icons-vue/es/search/20";
import View20 from "@carbon/icons-vue/es/view/20";
import SendAlt20 from "@carbon/icons-vue/es/send--alt/20";
import Renew20 from "@carbon/icons-vue/es/renew/20";
import Checkmark20 from "@carbon/icons-vue/es/checkmark/20";
import TrashCan20 from "@carbon/icons-vue/es/trash-can/20";

export default {
  name: "MailTracking",
  mixins: [TaskService, QueryParamService, UtilService, PageTitleService],
  pageTitle() {
    return this.$t("tracking.title") + " - " + this.appName;
  },
  data() {
    return {
      Search20,
      View20,
      SendAlt20,
      Renew20,
      Checkmark20,
      TrashCan20,
      q: {
        page: "mail-tracking",
      },
      urlCheckInterval: null,
      filters: {
        sender: "",
        recipient: "",
        subject: "",
        action_filter: "all",
        date_from: "",
        date_to: "",
      },
      rspamdConnected: true,
      rspamdError: "",
      historyEntries: [],
      historyTablePage: [],
      historyColumns: [
        "timestamp",
        "sender",
        "recipients",
        "subject",
        "status",
        "score",
        "actions",
      ],
      selectedHistoryEntry: null,
      quarantineItems: [],
      quarantineTablePage: [],
      quarantineColumns: [
        "recipient",
        "sender",
        "subject",
        "received_at",
        "spam_score",
        "actions",
      ],
      digestStatus: {
        enabled: false,
        digest_time: "07:30",
        timezone: "Europe/Berlin",
        last_run_at: "",
        last_digests_sent: 0,
      },
      allowlistEntries: [],
      releasingId: "",
      deletingAllowlistId: null,
      quarantineNotice: "",
      loading: {
        searchHistory: false,
        listQuarantine: false,
        runDigest: false,
      },
      error: {
        searchHistory: "",
        listQuarantine: "",
        quarantineAction: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    i18nHistoryColumns() {
      return [
        this.$t("templates.col_timestamp"),
        this.$t("tracking.col_sender"),
        this.$t("tracking.col_recipients"),
        this.$t("tracking.col_subject"),
        this.$t("mailboxes.col_status"),
        this.$t("tracking.col_score"),
        this.$t("mailboxes.col_actions"),
      ];
    },
    i18nQuarantineColumns() {
      return [
        this.$t("tracking.col_recipient"),
        this.$t("tracking.col_sender"),
        this.$t("tracking.col_subject"),
        this.$t("tracking.col_received_at"),
        this.$t("tracking.col_score"),
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
    this.searchMailHistory();
    this.listQuarantine();
  },
  methods: {
    coreT(coreKey, fallbackKey) {
      if (this.core && typeof this.core.$t === "function") {
        return this.core.$t(coreKey);
      }
      return this.$t(fallbackKey);
    },
    historyStatusKind(status) {
      if (status === "delivered") {
        return "green";
      }
      if (status === "spam") {
        return "warm-gray";
      }
      if (status === "greylisted") {
        return "blue";
      }
      if (status === "rejected") {
        return "red";
      }
      return "cool-gray";
    },
    formatHistoryStatus(status) {
      if (status === "delivered") {
        return this.$t("tracking.status_delivered");
      }
      if (status === "spam") {
        return this.$t("tracking.status_spam");
      }
      if (status === "greylisted") {
        return this.$t("tracking.status_greylisted");
      }
      if (status === "rejected") {
        return this.$t("tracking.status_rejected");
      }
      return status || "-";
    },
    formatScore(score, requiredScore) {
      if (score === null || score === undefined || isNaN(score)) {
        return "-";
      }
      const s = Math.round(Number(score) * 100) / 100;
      if (
        requiredScore !== null &&
        requiredScore !== undefined &&
        !isNaN(requiredScore) &&
        Number(requiredScore) > 0
      ) {
        const req = Math.round(Number(requiredScore) * 100) / 100;
        return `${s} / ${req}`;
      }
      return String(s);
    },
    openHistoryDetail(row) {
      this.selectedHistoryEntry = row;
    },
    async searchMailHistory() {
      this.loading.searchHistory = true;
      this.error.searchHistory = "";
      const taskAction = "search-mail-history";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.error.searchHistory = this.$t("error.generic_error");
        this.loading.searchHistory = false;
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const out = (taskResult && taskResult.output) || {};
          this.rspamdConnected =
            out.rspamd_connected !== undefined
              ? Boolean(out.rspamd_connected)
              : true;
          this.rspamdError = out.rspamd_error || "";
          this.historyEntries = Array.isArray(out.entries) ? out.entries : [];
          this.loading.searchHistory = false;
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "search-mail-history",
          data: {
            sender: this.filters.sender || "",
            recipient: this.filters.recipient || "",
            subject: this.filters.subject || "",
            action_filter: this.filters.action_filter || "all",
            date_from: this.filters.date_from || "",
            date_to: this.filters.date_to || "",
            limit: 200,
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
        this.error.searchHistory = this.getErrorMessage(err);
        this.loading.searchHistory = false;
      }
    },
    async listQuarantine() {
      this.loading.listQuarantine = true;
      this.error.listQuarantine = "";
      const taskAction = "list-quarantine";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.error.listQuarantine = this.$t("error.generic_error");
        this.loading.listQuarantine = false;
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          const out = (taskResult && taskResult.output) || {};
          this.quarantineItems = Array.isArray(out.items) ? out.items : [];
          if (out.digest_status) {
            this.digestStatus = {
              ...this.digestStatus,
              ...out.digest_status,
            };
          }
          this.allowlistEntries = Array.isArray(out.allowlist)
            ? out.allowlist
            : [];
          this.loading.listQuarantine = false;
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "list-quarantine",
          data: {
            user: "",
            search: "",
            limit: 200,
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
        this.error.listQuarantine = this.getErrorMessage(err);
        this.loading.listQuarantine = false;
      }
    },
    async releaseQuarantineItem(item, allowSender) {
      this.releasingId = item.id || `${item.user}:${item.uid}`;
      this.error.quarantineAction = "";
      const taskAction = "release-quarantine";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.releasingId = "";
        this.error.quarantineAction = this.$t("error.generic_error");
      });
      this.core.$root.$once(`${taskAction}-completed-${eventId}`, () => {
        this.releasingId = "";
        this.quarantineNotice = allowSender
          ? this.$t("tracking.released_and_allowed_notice", {
              sender: item.sender,
            })
          : this.$t("tracking.released_notice", {
              recipient: item.recipient || item.user,
            });
        this.listQuarantine();
      });

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "release-quarantine",
          data: {
            user: item.user,
            uid: String(item.uid || ""),
            message_id: item.message_id || "",
            allow_sender: Boolean(allowSender),
          },
          extra: {
            title: this.$t("action." + taskAction),
            description: item.subject || item.sender,
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.releasingId = "";
        this.error.quarantineAction = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
      }
    },
    async runDigestNow() {
      this.loading.runDigest = true;
      this.error.quarantineAction = "";
      const taskAction = "run-digest-now";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.loading.runDigest = false;
        this.error.quarantineAction = this.$t("error.generic_error");
      });
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          this.loading.runDigest = false;
          const out = (taskResult && taskResult.output) || {};
          this.quarantineNotice = this.$t("tracking.digest_sent_notice", {
            sent: out.digests_sent || 0,
            checked: out.users_checked || 0,
          });
          this.listQuarantine();
        }
      );

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "run-digest-now",
          data: {
            user: "",
          },
          extra: {
            title: this.$t("action." + taskAction),
            description: this.$t("common.processing"),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.loading.runDigest = false;
        this.error.quarantineAction = this.getErrorMessage(err);
        this.createErrorNotificationForApp(
          err,
          this.$t("task.cannot_create_task", { action: taskAction })
        );
      }
    },
    async deleteAllowlistEntry(al) {
      this.deletingAllowlistId = al.id;
      this.error.quarantineAction = "";
      const taskAction = "delete-allowlist";
      const eventId = this.getUuid();

      this.core.$root.$once(`${taskAction}-aborted-${eventId}`, () => {
        this.deletingAllowlistId = null;
        this.error.quarantineAction = this.$t("error.generic_error");
      });
      this.core.$root.$once(`${taskAction}-completed-${eventId}`, () => {
        this.deletingAllowlistId = null;
        this.listQuarantine();
      });

      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: "delete-allowlist",
          data: {
            id: Number(al.id),
          },
          extra: {
            title: this.$t("action." + taskAction),
            description: al.sender_email,
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        this.deletingAllowlistId = null;
        this.error.quarantineAction = this.getErrorMessage(err);
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

.tab-panel {
  margin-top: $spacing-05;
}

.mg-bottom {
  margin-bottom: $spacing-05;
}

.mg-bottom-sm {
  margin-bottom: $spacing-04;
}

.mg-bottom-xs {
  margin-bottom: $spacing-02;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: $spacing-04;
}

.filter-grid-bottom {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
  align-items: end;
  gap: $spacing-04;
}

.search-btn-wrap {
  display: flex;
  justify-content: flex-end;
}

.digest-bar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: $spacing-04;
}

.digest-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: $spacing-03;
  margin-top: $spacing-02;
}

.meta-span,
.sub-note {
  font-size: 0.75rem;
  color: $text-02;
}

.digest-actions,
.row-actions,
.symbols-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-02;
}

.nowrap-cell {
  white-space: nowrap;
}

.break-cell {
  max-width: 18rem;
  word-break: break-word;
}

.detail-list {
  display: flex;
  flex-direction: column;
  gap: $spacing-03;
}

.detail-row {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-03;
}

.detail-key {
  font-weight: 600;
  min-width: 9rem;
}
</style>
