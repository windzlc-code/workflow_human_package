import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { Hono } from "hono";
import { serve } from "@hono/node-server";

import sentimentRoutes from "../../../plugins/sentiment/routes/sentiment.js";
import { closeDb, initDb } from "../../../plugins/sentiment/db/db.js";
import {
  ensureSentimentOperationalDefaults,
  maskSentimentAiSettings,
  maskSentimentOpenSearchSettings,
  readSentimentAiSettings,
  readSentimentNotificationSettings,
  readSentimentSearchSettings,
} from "../../../plugins/sentiment/sentiment-store.js";
import {
  configureSentimentRunner,
  getSentimentMonitorStatus,
  startSentimentScheduler,
  stopSentimentScheduler,
} from "../../../plugins/sentiment/scrapers/runner.js";
import { JsonConfigStore } from "./config-store.js";

const DEFAULT_PORT = 8787;
const DEFAULT_INTERVAL_MINUTES = 5;
const DEFAULT_ADMIN_SETTINGS = {
  scanDays: 30,
  reportDays: 30,
  sourceScopes: {
    fast: [],
    full: [],
    watch: [],
  },
};
const PUBLIC_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../public");
const SRC_DIR = path.dirname(fileURLToPath(import.meta.url));
const STATIC_TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml; charset=utf-8"],
]);

function resolveDataDir(input = "") {
  const raw = String(input || process.env.SENTIMENT_DATA_DIR || "").trim();
  if (raw) return path.resolve(raw);
  return path.join(os.homedir(), ".opinx-sentiment");
}

function createLogger() {
  return {
    info: (...args) => console.log("[sentiment-backend]", ...args),
    warn: (...args) => console.warn("[sentiment-backend]", ...args),
    error: (...args) => console.error("[sentiment-backend]", ...args),
  };
}

function createBus(log = createLogger()) {
  return {
    events: [],
    emit(event) {
      this.events.push({ ...event, emitted_at: new Date().toISOString() });
      if (event?.type) log.info("event", event.type);
    },
  };
}

function jsonError(c, error, status = 500) {
  const message = error instanceof Error ? error.message : String(error || "unknown error");
  return c.json({ ok: false, error: message }, status);
}

function readPublicFile(relativePath = "index.html") {
  const safePath = String(relativePath || "index.html").replace(/^\/+/, "");
  const resolved = path.resolve(PUBLIC_DIR, safePath);
  if (!resolved.startsWith(`${PUBLIC_DIR}${path.sep}`) && resolved !== PUBLIC_DIR) return null;
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) return null;
  return {
    body: fs.readFileSync(resolved),
    type: STATIC_TYPES.get(path.extname(resolved).toLowerCase()) || "application/octet-stream",
  };
}

function crc32Buffer(buffer) {
  let crc = -1;
  for (const byte of buffer) {
    crc ^= byte;
    for (let index = 0; index < 8; index += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ -1) >>> 0;
}

function dosDateTime(date = new Date()) {
  const year = Math.max(1980, date.getFullYear());
  const dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
  const dosDate = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { dosTime, dosDate };
}

function createStoredZip(files = []) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const file of files) {
    const nameBuffer = Buffer.from(file.name.replace(/^\/+/, ""), "utf8");
    const data = Buffer.isBuffer(file.data) ? file.data : Buffer.from(file.data || "");
    const crc = crc32Buffer(data);
    const { dosTime, dosDate } = dosDateTime(file.mtime || new Date());
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(dosTime, 10);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuffer.length, 26);
    local.writeUInt16LE(0, 28);
    localParts.push(local, nameBuffer, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(dosTime, 12);
    central.writeUInt16LE(dosDate, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuffer.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, nameBuffer);
    offset += local.length + nameBuffer.length + data.length;
  }
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, ...centralParts, end]);
}

function buildBrowserAuthExtensionZip() {
  const baseDir = path.resolve(PUBLIC_DIR, "browser-auth-extension");
  const fileNames = ["manifest.json", "background.js", "popup.html", "popup.js", "install.html"];
  const files = fileNames.map((name) => {
    const filePath = path.resolve(baseDir, name);
    const stat = fs.statSync(filePath);
    return {
      name: `opinx-browser-auth-helper/${name}`,
      data: fs.readFileSync(filePath),
      mtime: stat.mtime,
    };
  });
  return createStoredZip(files);
}

function normalizeAdminSourceList(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(item => String(item || "").trim()).filter(Boolean))].slice(0, 300);
}

function normalizeAdminSourceScopes(input = {}) {
  const scopes = input?.sourceScopes || input?.source_scopes || {};
  return {
    fast: normalizeAdminSourceList(scopes.fast || scopes.quick || scopes.quickScan),
    full: normalizeAdminSourceList(scopes.full || scopes.depth || scopes.deep || scopes.fullScan),
    watch: normalizeAdminSourceList(scopes.watch || scopes.crisis || scopes.warning || scopes.watchScan),
  };
}

function normalizeAdminSettings(input = {}) {
  return {
    scanDays: Math.max(1, Math.min(365, Number(input.scanDays ?? input.scan_days ?? DEFAULT_ADMIN_SETTINGS.scanDays) || DEFAULT_ADMIN_SETTINGS.scanDays)),
    reportDays: Math.max(1, Math.min(365, Number(input.reportDays ?? input.report_days ?? DEFAULT_ADMIN_SETTINGS.reportDays) || DEFAULT_ADMIN_SETTINGS.reportDays)),
    sourceScopes: normalizeAdminSourceScopes(input),
  };
}

export function createSentimentBackendApp({
  dataDir = resolveDataDir(),
  configPath = "",
  enableScheduler = process.env.SENTIMENT_SCHEDULER === "1" || process.env.SENTIMENT_SCHEDULER === "true",
  intervalMinutes = Number(process.env.SENTIMENT_INTERVAL_MINUTES || DEFAULT_INTERVAL_MINUTES),
  log = createLogger(),
  bus = createBus(log),
} = {}) {
  const resolvedDataDir = resolveDataDir(dataDir);
  fs.mkdirSync(resolvedDataDir, { recursive: true });
  initDb(resolvedDataDir);
  ensureSentimentOperationalDefaults();

  const config = new JsonConfigStore(configPath || path.join(resolvedDataDir, "sentiment-config.json"));
  let scanChild = null;
  const startBackgroundScan = (job = {}) => {
    if (scanChild && scanChild.exitCode === null && scanChild.signalCode === null) return scanChild.pid;
    const child = spawn(process.execPath, [path.join(SRC_DIR, "scan-worker.js")], {
      cwd: process.cwd(),
      detached: true,
      stdio: "ignore",
      env: {
        ...process.env,
        SENTIMENT_DATA_DIR: resolvedDataDir,
        SENTIMENT_CONFIG_PATH: config.filePath,
        SENTIMENT_SCAN_JOB: JSON.stringify(job),
      },
    });
    scanChild = child;
    child.once("exit", () => {
      if (scanChild === child) scanChild = null;
    });
    child.unref();
    return child.pid;
  };
  const isBackgroundScanRunning = () => Boolean(scanChild && scanChild.exitCode === null && scanChild.signalCode === null);
  configureSentimentRunner({
    bus,
    log,
    aiSettings: () => readSentimentAiSettings(config),
    notificationSettings: () => readSentimentNotificationSettings(config),
    searchSettings: () => readSentimentSearchSettings(config),
  });

  if (enableScheduler) {
    const minutes = Math.max(1, Math.min(24 * 60, Number(intervalMinutes) || DEFAULT_INTERVAL_MINUTES));
    startSentimentScheduler({ intervalMs: minutes * 60 * 1000 });
  }

  const app = new Hono();
  app.use("*", async (c, next) => {
    c.set("pluginCtx", { config, bus, log, startBackgroundScan, isBackgroundScanRunning });
    await next();
  });

  app.get("/", (c) => {
    const file = readPublicFile("index.html");
    if (!file) return c.text("Sentiment web app is missing.", 500);
    return new Response(file.body, { headers: { "content-type": file.type } });
  });

  app.get("/admin", (c) => {
    const file = readPublicFile("admin.html");
    if (!file) return c.text("Sentiment admin page is missing.", 500);
    return new Response(file.body, { headers: { "content-type": file.type } });
  });

  app.get("/assets/:file", (c) => {
    const file = readPublicFile(c.req.param("file"));
    if (!file) return c.text("not found", 404);
    return new Response(file.body, { headers: { "content-type": file.type, "cache-control": "no-cache" } });
  });

  app.get("/browser-auth-extension/download", () => {
    const zip = buildBrowserAuthExtensionZip();
    return new Response(zip, {
      headers: {
        "content-type": "application/zip",
        "content-disposition": "attachment; filename=\"opinx-browser-auth-helper.zip\"",
        "cache-control": "no-cache",
      },
    });
  });

  app.get("/browser-auth-extension/:file", (c) => {
    const file = readPublicFile(`browser-auth-extension/${c.req.param("file")}`);
    if (!file) return c.text("not found", 404);
    return new Response(file.body, { headers: { "content-type": file.type, "cache-control": "no-cache" } });
  });

  app.get("/api", (c) => c.json({
    ok: true,
    name: "opinx-sentiment-backend",
    dataDir: resolvedDataDir,
    endpoints: [
      "GET /health",
      "GET /api/sentiment",
      "POST /api/sentiment/scan",
      "POST /api/sentiment/scan-start",
      "GET /api/sentiment/report",
      "GET/POST/DELETE /api/sentiment/keywords",
      "GET/PUT /api/sentiment/search-settings",
      "GET/PUT /api/admin-settings",
      "POST /api/sentiment/ingest",
      "POST /api/sentiment/monitor",
    ],
  }));

  app.get("/health", (c) => c.json({
    ok: true,
    status: "ok",
    dataDir: resolvedDataDir,
    scheduler: {
      ...getSentimentMonitorStatus(),
      running: getSentimentMonitorStatus().running || isBackgroundScanRunning(),
    },
  }));

  app.get("/api/config", (c) => {
    const allConfig = config.all();
    return c.json({
      ok: true,
      config: {
        ...allConfig,
        sentimentAi: maskSentimentAiSettings(allConfig.sentimentAi || {}),
        sentimentSearch: {
          ...(allConfig.sentimentSearch || {}),
          openSearch: maskSentimentOpenSearchSettings(allConfig.sentimentSearch?.openSearch || {}),
        },
      },
    });
  });
  app.get("/api/admin-settings", (c) => c.json({
    ok: true,
    settings: normalizeAdminSettings(config.get("adminSettings") || {}),
  }));
  app.put("/api/admin-settings", async (c) => {
    const body = await c.req.json().catch(() => ({}));
    const current = normalizeAdminSettings(config.get("adminSettings") || {});
    const settings = normalizeAdminSettings({
      ...current,
      ...body,
      sourceScopes: body.sourceScopes || body.source_scopes || current.sourceScopes,
    });
    config.set("adminSettings", settings);
    return c.json({ ok: true, settings });
  });
  app.route("/api/sentiment", sentimentRoutes);

  app.onError((error, c) => jsonError(c, error));

  return {
    app,
    dataDir: resolvedDataDir,
    config,
    bus,
    log,
    close() {
      stopSentimentScheduler();
      closeDb();
    },
  };
}

export function startSentimentBackend(options = {}) {
  const port = Math.max(1, Math.min(65535, Number(options.port || process.env.PORT || DEFAULT_PORT)));
  const hostname = String(options.hostname || options.host || process.env.HOST || "127.0.0.1").trim() || "127.0.0.1";
  const backend = createSentimentBackendApp(options);
  const server = serve({ fetch: backend.app.fetch, port, hostname });
  backend.log?.info?.(`listening on http://${hostname}:${port}`);
  return {
    ...backend,
    port,
    hostname,
    server,
    close() {
      server.close?.();
      backend.close();
    },
  };
}

const currentFile = fileURLToPath(import.meta.url);
if (process.argv[1] && path.resolve(process.argv[1]) === currentFile) {
  const backend = startSentimentBackend();
  const shutdown = () => {
    backend.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}
