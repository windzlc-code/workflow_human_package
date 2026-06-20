import { closeDb, initDb } from "../../../plugins/sentiment/db/db.js";
import {
  ensureSentimentOperationalDefaults,
  readSentimentAiSettings,
  readSentimentNotificationSettings,
  readSentimentSearchSettings,
} from "../../../plugins/sentiment/sentiment-store.js";
import {
  configureSentimentRunner,
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

  const job = parseJob();
  await runSentimentScanNow({
    reason: job.reason || "manual",
    mode: job.mode || "fast",
    sources: Array.isArray(job.sources) ? job.sources : null,
    days: job.days,
  });
}

main()
  .catch(error => {
    log.error(error?.stack || error?.message || String(error));
    process.exitCode = 1;
  })
  .finally(() => {
    closeDb();
  });
