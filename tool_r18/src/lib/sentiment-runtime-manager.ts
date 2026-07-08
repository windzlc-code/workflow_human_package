import fs from "node:fs";
import path from "node:path";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createRequire } from "node:module";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";

const VENDOR_DIR = path.resolve(process.cwd(), "vendor", "opinx-sentiment");
const DEFAULT_PORT = 18787;
const IDLE_MS = 5 * 60 * 1000;

let child: ChildProcess | null = null;
let shutdownTimer: NodeJS.Timeout | null = null;
const ownsLocalSentimentRuntime = () => !String(process.env.TOOL_R18_SENTIMENT_BACKEND_URL || "").trim();

export function resolveSentimentVendorDir(): string {
  return VENDOR_DIR;
}

export function resolveSentimentDataDir(): string {
  const dataDir = path.dirname(resolveRuntimeFile("sentiment-opinx/crm.db"));
  fs.mkdirSync(dataDir, { recursive: true });
  const dbPath = path.join(dataDir, "crm.db");
  const seedPath = path.join(VENDOR_DIR, "runtime-seed", "crm.db");
  if (!fs.existsSync(dbPath) && fs.existsSync(seedPath)) {
    fs.copyFileSync(seedPath, dbPath);
  }
  return dataDir;
}

export function resolveSentimentBackendUrl(): string {
  const configured = String(process.env.TOOL_R18_SENTIMENT_BACKEND_URL || "").trim();
  return configured || `http://127.0.0.1:${DEFAULT_PORT}`;
}

export function scheduleSentimentRuntimeShutdown(delayMs = IDLE_MS) {
  if (shutdownTimer) clearTimeout(shutdownTimer);
  shutdownTimer = setTimeout(() => {
    stopSentimentRuntime();
  }, delayMs);
  shutdownTimer.unref?.();
}

export function stopSentimentRuntime() {
  if (shutdownTimer) clearTimeout(shutdownTimer);
  shutdownTimer = null;
  if (child && child.exitCode === null && child.signalCode === null) {
    child.kill("SIGTERM");
  }
  cleanupSentimentBackendProcesses();
  child = null;
}

function cleanupSentimentBackendProcesses() {
  if (process.platform === "win32") return;
  if (!ownsLocalSentimentRuntime()) return;
  const backendDir = path.join(VENDOR_DIR, "standalone", "sentiment-backend", "src");
  const patterns = [
    path.join(backendDir, "scan-worker.js"),
    path.join(backendDir, "server.js"),
  ];
  const quotedPatterns = patterns.map((item) => `'${item.replace(/'/g, "'\\''")}'`).join(" ");
  try {
    execFileSync("sh", ["-lc", `for pattern in ${quotedPatterns}; do ps -eo pid=,args= | awk -v needle="$pattern" 'index($0, needle) { print $1 }'; done | sort -u | xargs -r kill -TERM`], {
      stdio: "ignore",
    });
  } catch {
    // Best effort cleanup. Some images may not have the expected process tools, and no backend is also fine.
  }
}

export async function ensureSentimentRuntime(): Promise<{ ok: boolean; url: string; warning?: string }> {
  const url = resolveSentimentBackendUrl();
  if (await isSentimentRuntimeHealthy(url)) {
    scheduleSentimentRuntimeShutdown();
    return { ok: true, url };
  }

  const serverPath = path.join(VENDOR_DIR, "standalone", "sentiment-backend", "src", "server.js");
  if (!fs.existsSync(serverPath)) {
    return { ok: false, url, warning: "舆情 vendor 后端文件缺失，已改用本地舆情资料库候选。" };
  }
  const vendorRequire = createRequire(serverPath);
  try {
    vendorRequire.resolve("hono");
    vendorRequire.resolve("@hono/node-server");
    vendorRequire.resolve("better-sqlite3");
  } catch {
    return { ok: false, url, warning: "舆情 vendor 依赖尚未安装，已改用本地舆情资料库候选。" };
  }
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    cleanupSentimentBackendProcesses();
    child = spawn(process.execPath, [serverPath], {
      cwd: VENDOR_DIR,
      stdio: "ignore",
      env: {
        ...process.env,
        HOST: "127.0.0.1",
        PORT: String(DEFAULT_PORT),
        SENTIMENT_DATA_DIR: resolveSentimentDataDir(),
        SENTIMENT_SCHEDULER: "0",
      },
    });
    child.once("exit", () => {
      child = null;
    });
  }

  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (await isSentimentRuntimeHealthy(url)) {
      scheduleSentimentRuntimeShutdown();
      return { ok: true, url };
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return { ok: false, url, warning: "舆情后端启动超时，已改用本地舆情资料库候选。" };
}

async function isSentimentRuntimeHealthy(url: string): Promise<boolean> {
  try {
    const response = await fetch(`${url}/health`, { signal: AbortSignal.timeout(2500) });
    if (!response.ok) return false;
    const json = await response.json().catch(() => ({}));
    return json?.ok === true || json?.status === "ok";
  } catch {
    return false;
  }
}
