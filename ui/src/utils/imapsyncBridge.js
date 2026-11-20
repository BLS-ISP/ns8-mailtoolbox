export function formatUnixTimestamp(epochSec) {
  const num = Number(epochSec);
  if (!epochSec || isNaN(num) || num <= 0) {
    return "";
  }
  try {
    return new Date(num * 1000).toLocaleString(undefined, {
      dateStyle: "short",
      timeStyle: "short",
    });
  } catch (e) {
    return new Date(num * 1000).toISOString().replace("T", " ").slice(0, 16);
  }
}

export function parseImapsyncSchedule(rawCron) {
  if (rawCron === null || rawCron === undefined) {
    return { type: "manual", value: 0 };
  }
  const str = String(rawCron).trim().toLowerCase();
  if (!str || str === "0") {
    return { type: "manual", value: 0 };
  }
  const hourMatch = /^(\d+)\s*h$/.exec(str);
  if (hourMatch) {
    return { type: "hours", value: Number(hourMatch[1]) || 1 };
  }
  const minMatch = /^(\d+)\s*m?$/.exec(str);
  if (minMatch) {
    const mins = Number(minMatch[1]);
    if (mins > 0) {
      return { type: "minutes", value: mins };
    }
  }
  return { type: "manual", value: 0 };
}

export function formatImapsyncProtocol(security) {
  const sec = String(security || "").toLowerCase();
  if (sec === "ssl" || sec === "--ssl1") {
    return "IMAPS";
  }
  if (sec === "tls" || sec === "--tls1") {
    return "IMAP+STARTTLS";
  }
  return "IMAP";
}

export function normalizeImapsyncTask(rawTask, instance = {}, mailboxes = []) {
  const moduleId = instance.module_id || "imapsync1";
  const uiName = instance.ui_name || moduleId;
  const localuser = String(rawTask.localuser || "").trim();
  const taskId = String(rawTask.task_id || "").trim();
  const host = String(
    rawTask.remotehost || rawTask.remotehostname || ""
  ).trim();
  const security = String(rawTask.security || "").trim();
  const defaultPort = security === "ssl" || security === "--ssl1" ? 993 : 143;
  const port = Number(rawTask.remoteport) || defaultPort;
  const remoteUser = String(
    rawTask.remoteuser || rawTask.remoteusername || ""
  ).trim();

  const matchedMb = Array.isArray(mailboxes)
    ? mailboxes.find((m) => m.user === localuser)
    : null;
  const targetEmail = (matchedMb && matchedMb.mail) || localuser;

  const serviceRunning = Boolean(
    rawTask.service_running !== undefined
      ? rawTask.service_running
      : rawTask.service
  );

  const hasExitCode =
    rawTask.last_sync_exit_code !== null &&
    rawTask.last_sync_exit_code !== undefined &&
    rawTask.last_sync_exit_code !== "";
  const exitCode = hasExitCode ? Number(rawTask.last_sync_exit_code) : null;

  const hasTimestamp =
    rawTask.last_sync_timestamp !== null &&
    rawTask.last_sync_timestamp !== undefined &&
    rawTask.last_sync_timestamp !== "";
  const lastSyncTimestamp = hasTimestamp
    ? Number(rawTask.last_sync_timestamp)
    : null;

  let lastStatus = "pending";
  if (serviceRunning) {
    lastStatus = "running";
  } else if (exitCode === 0) {
    lastStatus = "ok";
  } else if (exitCode !== null && !isNaN(exitCode) && exitCode !== 0) {
    lastStatus = "error";
  }

  const cronRaw = rawTask.cron || rawTask.synchronization || "";
  const schedule = parseImapsyncSchedule(cronRaw);

  const deleteOlder = Number(rawTask.delete_remote_older || 0);
  const deleteRemote = Boolean(rawTask.delete_remote);
  const deleteLocal = Boolean(rawTask.delete_local);
  const sieveEnabled = Boolean(rawTask.sieve_enabled);

  const rawDisplayName = String(rawTask.name || "").replace(" -> ", " → ");
  const displayName =
    rawDisplayName ||
    (remoteUser
      ? `${remoteUser} → ${localuser}`
      : `${host || moduleId} → ${localuser}`);

  return {
    id: `imapsync:${moduleId}:${localuser}:${taskId}`,
    source: "imapsync",
    imapsync_module_id: moduleId,
    imapsync_ui_name: uiName,
    coupled: instance.coupled !== false,
    task_id: taskId,
    localuser,
    name: displayName,
    target_user: localuser,
    target_email: targetEmail,
    protocol: formatImapsyncProtocol(security),
    security,
    host,
    port,
    remote_user: remoteUser,
    schedule,
    interval_minutes:
      schedule.type === "hours" ? schedule.value * 60 : schedule.value,
    service_running: serviceRunning,
    sieve_enabled: sieveEnabled,
    delete_local: deleteLocal,
    delete_remote: deleteRemote,
    delete_remote_older: isNaN(deleteOlder) ? 0 : deleteOlder,
    last_sync_timestamp: lastSyncTimestamp,
    last_sync_at: formatUnixTimestamp(lastSyncTimestamp),
    last_sync_exit_code: exitCode,
    last_status: lastStatus,
    has_log: Boolean(rawTask.has_log),
    enabled: schedule.type !== "manual" || serviceRunning,
    messages_fetched_total: null,
  };
}

export function extractImapsyncModulesFromCluster(installedModulesOutput) {
  const discovered = [];
  const seen = new Set();
  for (const instanceList of Object.values(installedModulesOutput || {})) {
    if (!Array.isArray(instanceList)) {
      continue;
    }
    for (const inst of instanceList) {
      if (!inst || !inst.id) {
        continue;
      }
      const isImapsync =
        inst.module === "imapsync" || /^imapsync\d+$/.test(String(inst.id));
      if (isImapsync && !seen.has(inst.id)) {
        seen.add(inst.id);
        discovered.push({
          module_id: inst.id,
          ui_name: inst.ui_name || "",
          node_id: inst.node || null,
          coupled: true,
          tasks_loaded: false,
          tasks: [],
        });
      }
    }
  }
  return discovered;
}

export function navigateToCockpitApp(core, moduleId, page = "tasks") {
  if (!moduleId) {
    return;
  }
  const routePath = page
    ? `/apps/${moduleId}?page=${page}`
    : `/apps/${moduleId}`;
  if (core && core.$router && typeof core.$router.push === "function") {
    core.$router.push(routePath);
    return;
  }
  if (window.parent && window.parent.location) {
    window.parent.location.hash = `#${routePath}`;
  } else {
    window.location.hash = `#${routePath}`;
  }
}
