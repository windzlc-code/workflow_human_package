import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { closeDb, initDb } from "../../../plugins/sentiment/db/db.js";
import {
  ensureSentimentOperationalDefaults,
  readSentimentAiSettings,
  readSentimentNotificationSettings,
  readSentimentSearchSettings,
} from "../../../plugins/sentiment/sentiment-store.js";
import {
  configureSentimentRunner,
  executeDueSentimentCollectionJobDrain,
  executeDueSentimentCollectionJobs,
  executeSentimentContinuousCollectionCycle,
  runSentimentScanNow,
} from "../../../plugins/sentiment/scrapers/runner.js";
import { JsonConfigStore } from "./config-store.js";

function parseJob() {
  try {
    return JSON.parse(process.env.SENTIMENT_SCAN_JOB || "{}");
  } catch {
    return {};
  }
}

function writeStatus(job = {}, patch = {}) {
  const statusPath = String(job.statusPath || job.status_path || "").trim();
  if (!statusPath) return;
  let current = {};
  try {
    current = JSON.parse(fs.readFileSync(statusPath, "utf8"));
  } catch {
    current = {};
  }
  const next = {
    ...current,
    ...patch,
    updated_at: new Date().toISOString(),
  };
  fs.mkdirSync(path.dirname(statusPath), { recursive: true });
  fs.writeFileSync(statusPath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
}

function acquireScanWorkerLock(dataDir, job = {}) {
  const scope = ["continuous-collection", "collection-jobs-execute-due"].includes(String(job.type || ""))
    ? String(job.type)
    : "scan";
  const lockPath = path.join(dataDir, `.scan-worker-${scope}.lock`);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const fd = fs.openSync(lockPath, "wx");
      fs.writeFileSync(fd, `${process.pid}\n`, "utf8");
      return { fd, lockPath };
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      let ownerPid = 0;
      try {
        ownerPid = Number(fs.readFileSync(lockPath, "utf8").trim()) || 0;
        if (ownerPid <= 0) {
          try { fs.unlinkSync(lockPath); } catch {}
          continue;
        }
        process.kill(ownerPid, 0);
      } catch (ownerError) {
        if (ownerError?.code !== "ESRCH" && ownerPid > 0) throw ownerError;
        try { fs.unlinkSync(lockPath); } catch {}
        continue;
      }
      throw new Error(`sentiment scan worker already running (pid=${ownerPid})`);
    }
  }
  throw new Error("unable to acquire sentiment scan worker lock");
}

function releaseScanWorkerLock(lock) {
  if (!lock) return;
  try { fs.closeSync(lock.fd); } catch {}
  try {
    const ownerPid = Number(fs.readFileSync(lock.lockPath, "utf8").trim()) || 0;
    if (ownerPid === process.pid) fs.unlinkSync(lock.lockPath);
  } catch {}
}

const log = {
  info: (...args) => console.log("[sentiment-scan-worker]", ...args),
  warn: (...args) => console.warn("[sentiment-scan-worker]", ...args),
  error: (...args) => console.error("[sentiment-scan-worker]", ...args),
};

const bus = {
  emit(event) {
    if (event?.type) log.info("event", event.type);
  },
};

async function main() {
  const dataDir = process.env.SENTIMENT_DATA_DIR;
  const configPath = process.env.SENTIMENT_CONFIG_PATH;
  if (!dataDir || !configPath) throw new Error("SENTIMENT_DATA_DIR and SENTIMENT_CONFIG_PATH are required");

  try {
    os.setPriority(0, 15);
  } catch {}

  const job = parseJob();
  const scanLock = acquireScanWorkerLock(dataDir, job);
  process.once("exit", () => releaseScanWorkerLock(scanLock));
  initDb(dataDir);
  ensureSentimentOperationalDefaults();
  const config = new JsonConfigStore(configPath);
  configureSentimentRunner({
    bus,
    log,
    aiSettings: () => readSentimentAiSettings(config),
    notificationSettings: () => readSentimentNotificationSettings(config),
    searchSettings: () => readSentimentSearchSettings(config),
  });

  const startedAt = Date.now();
  writeStatus(job, {
    id: job.runId || job.run_id || null,
    status: "running",
    started_at: new Date(startedAt).toISOString(),
    heartbeat_at: new Date(startedAt).toISOString(),
    heartbeat_elapsed_ms: 0,
    error: "",
  });
  const heartbeat = setInterval(() => {
    writeStatus(job, {
      status: "running",
      heartbeat_at: new Date().toISOString(),
      heartbeat_elapsed_ms: Math.max(0, Date.now() - startedAt),
    });
  }, 5000);
  heartbeat.unref?.();
  try {
    let result = null;
    if (job.type === "continuous-collection") {
      if (job.options?.deferContinuousCollectionExecution === true) {
        result = {
          ok: true,
          deferred: true,
          reason: job.options.deferContinuousCollectionReason || "continuous-collection-execution-deferred",
          mode: job.options.mode || "fast",
          sourceCoverageRefreshStage: {
            timed_out: false,
            stage: "source-coverage-refresh-followups",
            timeout_ms: 0,
            skipped: true,
            deferred: true,
          },
        };
      } else {
        result = await executeSentimentContinuousCollectionCycle(job.options || {});
      }
    } else if (job.type === "collection-jobs-execute-due") {
      const { drainBatches, collectionJobTimeoutMs, limit, concurrency, ...collectionOptions } = job.options || {};
      const safeDrainBatches = Math.max(1, Math.min(5, Number(drainBatches) || 1));
      result = safeDrainBatches > 1
        ? await executeDueSentimentCollectionJobDrain({
          batches: safeDrainBatches,
          limit,
          concurrency,
          collectionJobTimeoutMs,
          collectionOptions,
        })
        : await executeDueSentimentCollectionJobs(job.options || {});
    } else {
      result = await runSentimentScanNow({
        reason: job.reason || "manual",
        mode: job.mode || "fast",
        sources: Array.isArray(job.sources) ? job.sources : null,
        days: job.days,
      });
    }
    const finishedAt = new Date().toISOString();
    writeStatus(job, {
      status: "success",
      result,
      finished_at: finishedAt,
      duration_ms: Math.max(0, Date.parse(finishedAt) - startedAt),
      heartbeat_at: finishedAt,
      heartbeat_elapsed_ms: Math.max(0, Date.parse(finishedAt) - startedAt),
      error: "",
    });
  } catch (error) {
    const finishedAt = new Date().toISOString();
    writeStatus(job, {
      status: "failed",
      result: null,
      finished_at: finishedAt,
      duration_ms: Math.max(0, Date.parse(finishedAt) - startedAt),
      heartbeat_at: finishedAt,
      heartbeat_elapsed_ms: Math.max(0, Date.parse(finishedAt) - startedAt),
      error: error?.message || String(error),
    });
    throw error;
  } finally {
    clearInterval(heartbeat);
    releaseScanWorkerLock(scanLock);
  }
}

main()
  .catch(error => {
    log.error(error?.stack || error?.message || String(error));
    process.exitCode = 1;
  })
  .finally(() => {
    closeDb();
  });
