import "@/runtime/node/browser-shim";
import { ensureRuntimeApiConfig } from "@/runtime/node/ensure-runtime-config";
import { ensureRuntimeSecrets } from "@/runtime/node/ensure-runtime-secrets";
import { PublishSchedulerService, recoverInterruptedPublishQueue, type PublishTaskRunResult } from "@/core/publish/publish-scheduler";
import { createNodePublishQueueRepository } from "@/runtime/node/publish-queue-repository";
import { resolveVmosCredentials } from "@/runtime/node/config";
import { publishPost, type PublishCancellationToken, type PublishProgress } from "@/lib/vmos-publisher";
import { startTelegramBot, stopTelegramPolling, type TelegramBotInstanceOptions } from "@/telegram-bot";
import { markArchiveEpisodesPublished } from "@/lib/persona-archives";
import { screenshot as captureVmosScreenshot } from "@/lib/vmos-client";
import { refreshSentimentSourceMetrics } from "@/lib/sentiment-hot-importer";
import { stopSentimentRuntime } from "@/lib/sentiment-runtime-manager";
import fs from "node:fs";
import crypto from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";

const LOG_PREFIX = "[daemon]";
const TELEGRAM_BOT_DISABLED = process.env.TELEGRAM_BOT_DISABLED === "1";
const TELEGRAM_TOKEN_FILE = resolveRuntimeFile("telegram_bot_token.txt");
const TELEGRAM_TOKEN_DISABLED_FILE = process.env.TOOL_R18_TELEGRAM_BOT_DISABLED_FILE || resolveRuntimeFile("telegram_bot_token.disabled");
const TELEGRAM_BOTS_FILE = process.env.TOOL_R18_TELEGRAM_BOTS_FILE || resolveRuntimeFile("telegram_bots.local.json");
const TELEGRAM_PRIMARY_BOT_FILE = process.env.TOOL_R18_TELEGRAM_PRIMARY_BOT_FILE || resolveRuntimeFile("telegram_primary.local.json");
function readLocalTelegramBotToken(): string {
  try {
    if (fs.existsSync(TELEGRAM_TOKEN_DISABLED_FILE)) return "";
    if (!fs.existsSync(TELEGRAM_TOKEN_FILE)) return "";
    return fs.readFileSync(TELEGRAM_TOKEN_FILE, "utf-8").trim();
  } catch {
    return "";
  }
}
type TelegramBotRuntimeConfig = TelegramBotInstanceOptions & { token: string };

function normalizePersonaDataScope(value: any): "shared" | "isolated" {
  return String(value || "").trim().toLowerCase() === "isolated" ? "isolated" : "shared";
}

function parseRuntimeList(value: any): string[] {
  return String(value || "")
    .split(/[,，\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeTelegramBotConfig(value: any, fallbackName: string): TelegramBotRuntimeConfig | null {
  const token = String(value?.token || value?.botToken || "").trim();
  if (!token) return null;
  if (value?.enabled === false) return null;
  return {
    token,
    name: String(value?.name || fallbackName).trim() || fallbackName,
    defaultPadCode: typeof value?.defaultPadCode === "string" ? value.defaultPadCode.trim() : undefined,
    defaultPublishPlatform: value?.defaultPublishPlatform,
    defaultWarmupPlatform: value?.defaultWarmupPlatform,
    allowedPublishPlatforms: Array.isArray(value?.allowedPublishPlatforms) ? value.allowedPublishPlatforms : undefined,
    allowedWarmupPlatforms: Array.isArray(value?.allowedWarmupPlatforms) ? value.allowedWarmupPlatforms : undefined,
    allowedVmosAccountNames: Array.isArray(value?.allowedVmosAccountNames) ? value.allowedVmosAccountNames : undefined,
    allowedPadCodes: Array.isArray(value?.allowedPadCodes) ? value.allowedPadCodes : undefined,
    personaDataScope: normalizePersonaDataScope(value?.personaDataScope || process.env.TELEGRAM_PERSONA_DATA_SCOPE),
  };
}

function readTelegramBotConfigsFromJson(raw: string): TelegramBotRuntimeConfig[] {
  const parsed = JSON.parse(raw);
  const list = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.bots) ? parsed.bots : [];
  return list
    .map((item: any, index: number) => normalizeTelegramBotConfig(item, `bot-${index + 1}`))
    .filter((item: TelegramBotRuntimeConfig | null): item is TelegramBotRuntimeConfig => Boolean(item?.token));
}

function readLocalPrimaryBotConfig(): Partial<TelegramBotRuntimeConfig> {
  try {
    if (!fs.existsSync(TELEGRAM_PRIMARY_BOT_FILE)) return {};
    const parsed = JSON.parse(fs.readFileSync(TELEGRAM_PRIMARY_BOT_FILE, "utf-8"));
    return {
      name: String(parsed?.name || "primary").trim() || "primary",
      allowedPadCodes: Array.isArray(parsed?.allowedPadCodes)
        ? parsed.allowedPadCodes.map((code: any) => String(code || "").trim()).filter(Boolean)
        : parseRuntimeList(parsed?.allowedPadCodes),
      personaDataScope: normalizePersonaDataScope(parsed?.personaDataScope),
    };
  } catch (error: any) {
    log(`⚠️  Telegram primary Bot 配置读取失败: ${error?.message || String(error)}`);
    return {};
  }
}

function readLocalTelegramBotConfigs(): TelegramBotRuntimeConfig[] {
  if (TELEGRAM_BOT_DISABLED) return [];

  const configs: TelegramBotRuntimeConfig[] = [];
  const seenTokens = new Set<string>();
  const appendConfig = (config: TelegramBotRuntimeConfig | null) => {
    const token = String(config?.token || "").trim();
    if (!config || !token || seenTokens.has(token)) return;
    seenTokens.add(token);
    configs.push(config);
  };

  const legacyToken = (readLocalTelegramBotToken() || process.env.TELEGRAM_BOT_TOKEN || "").trim();
  if (legacyToken) {
    const primaryConfig = readLocalPrimaryBotConfig();
    appendConfig({
      token: legacyToken,
      name: primaryConfig.name || "primary",
      allowedPadCodes: primaryConfig.allowedPadCodes || parseRuntimeList(process.env.TELEGRAM_PRIMARY_ALLOWED_PAD_CODES),
      personaDataScope: primaryConfig.personaDataScope || normalizePersonaDataScope(process.env.TELEGRAM_PERSONA_DATA_SCOPE),
    });
  }

  const envConfigs = String(process.env.TELEGRAM_BOTS_JSON || "").trim();
  if (envConfigs) {
    try {
      const parsed = readTelegramBotConfigsFromJson(envConfigs);
      parsed.forEach(appendConfig);
      return configs;
    } catch (error: any) {
      log(`⚠️  TELEGRAM_BOTS_JSON 解析失败: ${error?.message || String(error)}`);
    }
  }

  try {
    if (fs.existsSync(TELEGRAM_BOTS_FILE)) {
      const parsed = readTelegramBotConfigsFromJson(fs.readFileSync(TELEGRAM_BOTS_FILE, "utf-8"));
      parsed.forEach(appendConfig);
    }
  } catch (error: any) {
    log(`⚠️  Telegram 多 Bot 配置读取失败: ${error?.message || String(error)}`);
  }

  return configs;
}

let activeTelegramBots: Array<{ config: TelegramBotRuntimeConfig; bot: ReturnType<typeof startTelegramBot> }> = [];
let activeTelegramBotSignature = "";
let telegramBotLockClaimed = false;
let telegramBotConfigApplyInFlight = false;
let pendingTelegramBotConfigApply: { configs: TelegramBotRuntimeConfig[]; reason: string } | null = null;
const activeTelegramBotWorkers = new Map<string, { config: TelegramBotRuntimeConfig; signature: string; child: ChildProcess }>();
const manualInterventionNotifiedTaskIds = new Set<string>();

interface PublishFailureEvidence {
  failureStep?: string;
  screenshotUrl?: string;
  samplePath?: string;
  manualInterventionRequired?: boolean;
}

function normalizeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "Unknown error");
}

function extractEvidencePathFromMessage(message: string, key: "sample" | "debug"): string | undefined {
  return message.match(new RegExp(`[｜|]${key}=([^｜\\s]+)`))?.[1];
}

function resolveSampleScreenshotPath(samplePath?: string): string | undefined {
  if (!samplePath) return undefined;
  try {
    if (/\.(png|jpe?g|webp)$/i.test(samplePath) && fs.existsSync(samplePath)) return samplePath;
    if (/\.json$/i.test(samplePath) && fs.existsSync(samplePath)) {
      const parsed = JSON.parse(fs.readFileSync(samplePath, "utf-8"));
      const screenshotPath = String(parsed?.screenshotPath || "").trim();
      if (screenshotPath && fs.existsSync(screenshotPath)) return screenshotPath;
    }
  } catch {}
  return undefined;
}

async function buildPublishFailureEvidence(
  credentials: any,
  task: any,
  error: unknown,
  lastProgressStep?: string,
  progressEvidence: PublishFailureEvidence = {},
): Promise<PublishFailureEvidence> {
  const message = normalizeErrorMessage(error);
  const samplePath = progressEvidence.samplePath
    || extractEvidencePathFromMessage(message, "sample")
    || extractEvidencePathFromMessage(message, "debug");
  const screenshotUrl = progressEvidence.screenshotUrl
    || await captureVmosScreenshot(credentials, task.pad_code).catch(() => undefined);
  return {
    failureStep: progressEvidence.failureStep || lastProgressStep || "自动化执行异常",
    screenshotUrl,
    samplePath,
    manualInterventionRequired: progressEvidence.manualInterventionRequired ?? true,
  };
}

async function notifyPublishManualIntervention(task: any, error: unknown, evidence: PublishFailureEvidence): Promise<void> {
  const chatId = task.telegram_chat_id;
  if (!chatId || manualInterventionNotifiedTaskIds.has(task.id)) return;
  const bot = activeTelegramBots[0]?.bot;
  if (!bot) return;
  manualInterventionNotifiedTaskIds.add(task.id);

  const reason = normalizeErrorMessage(error);
  const lines = [
    "⚠️ 需要人工介入",
    "",
    `任务：${task.id}`,
    `平台：${task.platform || "-"}`,
    `智能體手機：${task.pad_code || "-"}`,
    `失败步骤：${evidence.failureStep || "自动化执行异常"}`,
    `失败原因：${reason}`,
    "",
    "系统已停止继续误操作，并已保存失败截图/证据。请人工检查当前智能體手機界面后再重试。",
  ];
  await bot.sendMessage(chatId, lines.join("\n")).catch(() => undefined);

  const screenshotPath = resolveSampleScreenshotPath(evidence.samplePath);
  const photoInput = screenshotPath || evidence.screenshotUrl;
  if (photoInput) {
    await bot.sendPhoto(chatId, photoInput, { caption: "📸 自动化失败截图" }).catch(() => undefined);
  } else {
    await bot.sendMessage(chatId, "⚠️ 未能取得当前智能體手機截图，请直接进入智能體手機查看失败停留页面。").catch(() => undefined);
  }
}
const TELEGRAM_LOCK_FILE = resolveRuntimeFile("telegram_bot.lock");
const DAEMON_HEARTBEAT_FILE = resolveRuntimeFile("daemon.heartbeat.json");
const PROCESS_STATUS_FILE = resolveRuntimeFile("process-status.json");
const DAEMON_HEARTBEAT_STALE_MS = 90_000;
const TELEGRAM_BOT_CONFIG_RELOAD_MS = Math.max(Number(process.env.TELEGRAM_BOT_CONFIG_RELOAD_MS || 2000), 2000);

function isAllowedScheduledPublishPlatform(platform: unknown): platform is "threads" | "telegram" {
  return platform === "threads" || platform === "telegram";
}

async function buildInitialPublishedMeta(platform: string, publishedUrl: unknown) {
  const sourceUrl = String(publishedUrl || "").trim();
  if (!sourceUrl) return { publishedUrl: undefined, publishedMeta: undefined };
  const baseMeta = {
    source: "published_post",
    platform,
    sourceUrl,
    capturedAt: new Date().toISOString(),
  };
  if (platform !== "threads") return { publishedUrl: sourceUrl, publishedMeta: baseMeta };
  const refreshed = await refreshSentimentSourceMetrics({
    platform,
    sourceUrl,
  }).catch(() => null);
  if (!refreshed?.ok) return { publishedUrl: sourceUrl, publishedMeta: baseMeta };
  return {
    publishedUrl: sourceUrl,
    publishedMeta: {
      ...baseMeta,
      hotScore: refreshed.hotScore,
      metrics: refreshed.metrics || {},
      engagement: refreshed.engagement || {},
      mediaItems: refreshed.media || [],
      capturedAt: new Date().toISOString(),
    },
  };
}

function readDaemonHeartbeat(): { pid?: number; updatedAt?: string } | null {
  try {
    if (!fs.existsSync(DAEMON_HEARTBEAT_FILE)) return null;
    const parsed = JSON.parse(fs.readFileSync(DAEMON_HEARTBEAT_FILE, "utf-8"));
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function isFreshDaemonHeartbeatForPid(pid: number): boolean {
  const heartbeat = readDaemonHeartbeat();
  if (Number(heartbeat?.pid) !== pid) return false;
  const updatedAt = Date.parse(String(heartbeat?.updatedAt || ""));
  return Number.isFinite(updatedAt) && Date.now() - updatedAt <= DAEMON_HEARTBEAT_STALE_MS;
}

function canStartTelegramBot(): boolean {
  try {
    if (!fs.existsSync(TELEGRAM_LOCK_FILE)) return true;
    const raw = fs.readFileSync(TELEGRAM_LOCK_FILE, "utf-8").trim();
    const pid = Number(raw);
    if (!Number.isFinite(pid) || pid <= 0) return true;
    try {
      process.kill(pid, 0);
      if (pid === process.pid) return true;
      return !isFreshDaemonHeartbeatForPid(pid);
    } catch {
      return true;
    }
  } catch {
    return true;
  }
}

function claimTelegramBotLock() {
  try {
    fs.writeFileSync(TELEGRAM_LOCK_FILE, String(process.pid), "utf-8");
  } catch {}
}

function releaseTelegramBotLock() {
  try {
    if (!fs.existsSync(TELEGRAM_LOCK_FILE)) return;
    const raw = fs.readFileSync(TELEGRAM_LOCK_FILE, "utf-8").trim();
    if (Number(raw) === process.pid) fs.unlinkSync(TELEGRAM_LOCK_FILE);
  } catch {}
}

function writeDaemonHeartbeat(extra: Record<string, unknown> = {}) {
  const state = String(extra.state || "running");
  const now = new Date();
  try {
    fs.writeFileSync(
      DAEMON_HEARTBEAT_FILE,
      JSON.stringify({ pid: process.pid, updatedAt: now.toISOString(), ...extra }, null, 2),
      "utf-8",
    );
  } catch {}
  try {
    fs.writeFileSync(
      PROCESS_STATUS_FILE,
      JSON.stringify({ state, pid: String(process.pid), updated_at: now.toISOString() }, null, 2),
      "utf-8",
    );
  } catch {}
}

function removeDaemonHeartbeat() {
  try {
    if (!fs.existsSync(DAEMON_HEARTBEAT_FILE)) return;
    const raw = JSON.parse(fs.readFileSync(DAEMON_HEARTBEAT_FILE, "utf-8"));
    if (Number(raw?.pid) === process.pid) fs.unlinkSync(DAEMON_HEARTBEAT_FILE);
  } catch {}
}

function telegramBotConfigSignature(configs: TelegramBotRuntimeConfig[]): string {
  return JSON.stringify(configs.map((config) => ({
    token: config.token,
    name: config.name || "primary",
    defaultPadCode: config.defaultPadCode || "",
    defaultPublishPlatform: config.defaultPublishPlatform || "",
    defaultWarmupPlatform: config.defaultWarmupPlatform || "",
    allowedPublishPlatforms: config.allowedPublishPlatforms || [],
    allowedWarmupPlatforms: config.allowedWarmupPlatforms || [],
    allowedVmosAccountNames: config.allowedVmosAccountNames || [],
    allowedPadCodes: config.allowedPadCodes || [],
    personaDataScope: config.personaDataScope || "shared",
  })));
}

function telegramBotWorkerKey(token: string): string {
  return crypto.createHash("sha256").update(token).digest("hex").slice(0, 16);
}

function telegramBotWorkerConfigSignature(config: TelegramBotRuntimeConfig): string {
  return telegramBotConfigSignature([config]);
}

function stopTelegramBotWorker(key: string) {
  const worker = activeTelegramBotWorkers.get(key);
  if (!worker) return;
  activeTelegramBotWorkers.delete(key);
  try {
    worker.child.kill("SIGTERM");
  } catch {}
  writeTelegramBotRuntimeHeartbeat();
}

function stopAllTelegramBotWorkers() {
  for (const key of Array.from(activeTelegramBotWorkers.keys())) {
    stopTelegramBotWorker(key);
  }
}

function startTelegramBotWorker(config: TelegramBotRuntimeConfig, reason: string) {
  const key = telegramBotWorkerKey(config.token);
  const signature = telegramBotWorkerConfigSignature(config);
  const existing = activeTelegramBotWorkers.get(key);
  if (existing?.signature === signature && !existing.child.killed) return;
  if (existing) stopTelegramBotWorker(key);

  const workerConfig = { ...config, enableWebhookServer: false };
  const logSafeName = String(config.name || key).replace(/[^\w.-]+/g, "_").slice(0, 80) || key;
  const stdoutPath = resolveRuntimeFile(`telegram-bot-worker-${logSafeName}-${key}.stdout.log`);
  const stderrPath = resolveRuntimeFile(`telegram-bot-worker-${logSafeName}-${key}.stderr.log`);
  fs.mkdirSync(resolveRuntimeFile("."), { recursive: true });
  const stdout = fs.openSync(stdoutPath, "a");
  const stderr = fs.openSync(stderrPath, "a");
  const child = spawn(process.execPath, ["--import", "tsx", "src/telegram-bot-worker.ts"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      TELEGRAM_BOT_WORKER_CONFIG: JSON.stringify(workerConfig),
    },
    stdio: ["ignore", stdout, stderr],
    detached: false,
  });
  activeTelegramBotWorkers.set(key, { config, signature, child });
  child.on("exit", (code, signal) => {
    const current = activeTelegramBotWorkers.get(key);
    if (current?.child === child) activeTelegramBotWorkers.delete(key);
    log(`Telegram Bot worker exited ${config.name || key} code=${code ?? "-"} signal=${signal ?? "-"} reason=${reason}`);
    writeTelegramBotRuntimeHeartbeat();
  });
  log(`Telegram Bot worker started ${config.name || key}${reason ? ` (${reason})` : ""}`);
  writeTelegramBotRuntimeHeartbeat();
}

function syncTelegramBotWorkers(configs: TelegramBotRuntimeConfig[], reason: string) {
  const desired = new Set<string>();
  for (const config of configs) {
    const key = telegramBotWorkerKey(config.token);
    desired.add(key);
    startTelegramBotWorker(config, reason);
  }
  for (const key of Array.from(activeTelegramBotWorkers.keys())) {
    if (!desired.has(key)) stopTelegramBotWorker(key);
  }
  writeTelegramBotRuntimeHeartbeat();
}

async function stopActiveTelegramBots(): Promise<void> {
  const current = activeTelegramBots;
  activeTelegramBots = [];
  for (const item of current) {
    try {
      await item.bot.stopPolling().catch(() => undefined);
      await stopTelegramPolling(item.config.token).catch(() => undefined);
    } catch {}
  }
}

async function applyTelegramBotRuntimeConfig(configs: TelegramBotRuntimeConfig[], reason: string): Promise<void> {
  const mainConfigs = configs.slice(0, 1);
  const workerConfigs = configs.slice(1);
  syncTelegramBotWorkers(workerConfigs, reason);
  const signature = telegramBotConfigSignature(mainConfigs);
  if (signature === activeTelegramBotSignature) return;

  if (!mainConfigs.length) {
    await stopActiveTelegramBots();
    activeTelegramBotSignature = signature;
    if (telegramBotLockClaimed) {
      releaseTelegramBotLock();
      telegramBotLockClaimed = false;
    }
    log("⚠️  未配置 TELEGRAM_BOT_TOKEN，Bot 未启动");
    writeTelegramBotRuntimeHeartbeat();
    return;
  }

  if (!telegramBotLockClaimed) {
    if (!canStartTelegramBot()) {
      log("⚠️  检测到另一实例已持有 Telegram Bot 锁，跳过当前进程的 Bot 启动");
      return;
    }
    claimTelegramBotLock();
    telegramBotLockClaimed = true;
  }

  await stopActiveTelegramBots();
  activeTelegramBotSignature = signature;
  for (const botConfig of mainConfigs) {
    await stopTelegramPolling(botConfig.token).catch(() => undefined);
    const bot = startTelegramBot(botConfig.token, {
      name: botConfig.name,
      defaultPadCode: botConfig.defaultPadCode,
      defaultPublishPlatform: botConfig.defaultPublishPlatform,
      defaultWarmupPlatform: botConfig.defaultWarmupPlatform,
      allowedPublishPlatforms: botConfig.allowedPublishPlatforms,
      allowedWarmupPlatforms: botConfig.allowedWarmupPlatforms,
      allowedVmosAccountNames: botConfig.allowedVmosAccountNames,
      allowedPadCodes: botConfig.allowedPadCodes,
      personaDataScope: botConfig.personaDataScope,
    });
    activeTelegramBots.push({ config: botConfig, bot });
    log(`✓ Telegram Bot 已启动: ${botConfig.name || "unnamed"}${botConfig.defaultPadCode ? ` / 默认智能體手機 ${botConfig.defaultPadCode}` : ""}${reason ? `（${reason}）` : ""}`);
  }
  writeTelegramBotRuntimeHeartbeat();
}

async function queueTelegramBotRuntimeConfigApply(configs: TelegramBotRuntimeConfig[], reason: string): Promise<void> {
  pendingTelegramBotConfigApply = { configs, reason };
  if (telegramBotConfigApplyInFlight) return;
  telegramBotConfigApplyInFlight = true;
  try {
    while (pendingTelegramBotConfigApply) {
      const next = pendingTelegramBotConfigApply;
      pendingTelegramBotConfigApply = null;
      await applyTelegramBotRuntimeConfig(next.configs, next.reason);
    }
  } finally {
    telegramBotConfigApplyInFlight = false;
  }
}

function log(msg: string) {
  const ts = new Date().toISOString().slice(0, 19).replace("T", " ");
  console.log(`${ts} ${LOG_PREFIX} ${msg}`);
}

function activeTelegramBotCount() {
  return activeTelegramBots.length + activeTelegramBotWorkers.size;
}

function writeTelegramBotRuntimeHeartbeat() {
  writeDaemonHeartbeat({ state: "running", telegramBot: activeTelegramBotCount() > 0 ? `configured:${activeTelegramBotCount()}` : "missing-token" });
}

async function main() {
  writeDaemonHeartbeat({ state: "starting" });
  log("自動化推文營運控制台 — 後台服務啟動中...");
  const configPath = ensureRuntimeApiConfig();
  ensureRuntimeSecrets();
  log(`✓ 运行时 API 配置已就绪: ${configPath}`);

  const credentials = resolveVmosCredentials();
  if (!credentials.ak || !credentials.sk) {
    log("⚠️  VMOS 凭据未配置，发布功能将不可用");
    log("   请设置环境变量 VMOS_AK / VMOS_SK 或在 electron/vmos-credentials.local.json 中配置");
  } else {
    log(`✓ VMOS 凭据已加载（${Math.max(credentials.accounts?.length || 0, 1)} 组）`);
  }

  const repo = createNodePublishQueueRepository();
  log("✓ 发布队列数据库已连接");
  const recovered = recoverInterruptedPublishQueue(repo);
  if (recovered.interrupted || recovered.expiredPaused || recovered.clearedLocks) {
    log(`↻ 重启恢复：publishing=${recovered.interrupted} paused_expired=${recovered.expiredPaused} requeued=${recovered.requeued} post_publish_paused=${recovered.postPublishPaused} failed=${recovered.failed} locks_cleared=${recovered.clearedLocks}`);
  }

  const runner = async (task: any): Promise<PublishTaskRunResult> => {
    if (!credentials.ak || !credentials.sk) {
      const error = "VMOS 凭据未配置";
      const evidence = { failureStep: "启动前检查", manualInterventionRequired: true };
      await notifyPublishManualIntervention(task, error, evidence);
      return { status: "failed", error, ...evidence };
    }
    if (!isAllowedScheduledPublishPlatform(task.platform)) {
      const error = `Unsupported publish platform: ${task.platform || "(empty)"}`;
      const evidence = { failureStep: "启动前检查", manualInterventionRequired: true };
      await notifyPublishManualIntervention(task, error, evidence);
      return { status: "failed", error, ...evidence };
    }
    let postPublishVerificationStarted = false;
    let lastProgressStep = "准备开始";
    let progressEvidence: PublishFailureEvidence = {};
    try {
      const cancellationToken: PublishCancellationToken = {
        throwIfCancelled: () => {
          const current = repo.getTask(task.id);
          if (current && current.status !== "publishing") {
            throw new Error("用户强制中止当前任务");
          }
        },
      };
      const result = await publishPost(
        credentials,
        {
          padCode: task.pad_code,
          platform: task.platform,
          caption: task.caption,
          mediaUrl: task.media_url || undefined,
          telegramChatId: task.telegram_chat_id || undefined,
          telegramTargetChatId: task.telegram_target_chat_id || undefined,
          telegramTargetGroupName: task.telegram_target_group_name || undefined,
          telegramGroupContentType: task.telegram_group_content_type === "paid" ? "paid" : task.telegram_group_content_type === "free" ? "free" : undefined,
        },
        (progress) => {
          cancellationToken.throwIfCancelled?.();
          lastProgressStep = progress.step || lastProgressStep;
          if (progress.error || progress.warning || progress.manualIntervention) {
            progressEvidence = {
              failureStep: progress.step || lastProgressStep,
              screenshotUrl: progress.screenshotUrl || progressEvidence.screenshotUrl,
              samplePath: progress.samplePath || progressEvidence.samplePath,
              manualInterventionRequired: progress.manualIntervention ?? progressEvidence.manualInterventionRequired,
            };
          }
          const icon = progress.error ? "✗" : progress.warning ? "⚠" : progress.done ? "✓" : "→";
          log(`  ${icon} [${task.id}] ${progress.step}`);
          if (
            !postPublishVerificationStarted
            && /校驗發布結果|校验发布结果|已執行發布|已执行发布|已點選發布|已点击发布|待人工確認|待人工确认/.test(progress.step)
          ) {
            postPublishVerificationStarted = true;
            repo.updateTaskStatus(task.id, "publishing", {
              last_error: "发布动作已执行，等待结果校验；如 daemon 重启将暂停任务以避免重复发布",
            });
          }
          cancellationToken.throwIfCancelled?.();
        },
        { cancellationToken },
      );
      if (result && typeof result === "object" && "state" in result) {
        if (result.state === "verified") {
          if (task.archive_id && task.archive_post_id) {
            const screenshotUrl = result.screenshotUrl || await captureVmosScreenshot(credentials, task.pad_code).catch(() => undefined);
            const initialPublishedMeta = await buildInitialPublishedMeta(task.platform, result.publishedUrl);
            await markArchiveEpisodesPublished(
              task.archive_id,
              [task.archive_post_id],
              { [task.archive_post_id]: task.caption },
              {
                [task.archive_post_id]: {
                  platform: task.platform,
                  padCode: task.pad_code,
                  mediaUrl: task.media_url,
                  screenshotUrl,
                  ...initialPublishedMeta,
                },
              },
            ).catch(() => null);
          }
          return { status: "done" };
        }
        if (result.state === "warning") {
          const evidence = await buildPublishFailureEvidence(
            credentials,
            task,
            result.detail || "发布动作已执行但自动校验未能确认",
            lastProgressStep,
            { ...progressEvidence, screenshotUrl: result.screenshotUrl || progressEvidence.screenshotUrl },
          );
          await notifyPublishManualIntervention(task, result.detail || "发布动作已执行但自动校验未能确认", evidence);
          return {
            status: "paused",
            pauseType: "post_publish_verification",
            error: result.detail || "发布动作已执行但自动校验未能确认，已暂停以避免重复发布",
            ...evidence,
          };
        }
      }
      if (task.archive_id && task.archive_post_id) {
        const screenshotUrl = result && typeof result === "object" && "screenshotUrl" in result && result.screenshotUrl
          ? result.screenshotUrl
          : await captureVmosScreenshot(credentials, task.pad_code).catch(() => undefined);
        const resultPublishedUrl = result && typeof result === "object" && "publishedUrl" in result ? result.publishedUrl : undefined;
        const initialPublishedMeta = await buildInitialPublishedMeta(task.platform, resultPublishedUrl);
        await markArchiveEpisodesPublished(
          task.archive_id,
          [task.archive_post_id],
          { [task.archive_post_id]: task.caption },
          {
            [task.archive_post_id]: {
                  platform: task.platform,
                  padCode: task.pad_code,
                  mediaUrl: task.media_url,
                  screenshotUrl,
                  ...initialPublishedMeta,
                },
              },
            ).catch(() => null);
      }
      return { status: "done" };
    } catch (error: any) {
      const evidence = await buildPublishFailureEvidence(credentials, task, error, lastProgressStep, progressEvidence);
      await notifyPublishManualIntervention(task, error, evidence);
      if (postPublishVerificationStarted) {
        return {
          status: "paused",
          pauseType: "post_publish_verification",
          error: `发布动作已执行但结果校验失败，已停止自动重试以避免重复发布：${error?.message || String(error)}`,
          ...evidence,
        };
      }
      return { status: "failed", error: error?.message || String(error), ...evidence };
    }
  };

  const scheduler = new PublishSchedulerService(repo, runner, {
    onTaskStatusChange: (taskId, status, extra) => {
      const detail = extra?.error ? ` (${extra.error})` : "";
      log(`📋 任务 ${taskId} → ${status}${detail}`);
    },
  });

  scheduler.start();
  log("✓ 发布排程器已启动（每 10 秒轮询）");

  const pending = repo.listTasks({ status: "pending" });
  log(`  当前待发布任务: ${pending.length} 条`);

  // Start Telegram bot and keep token reloadable from runtime files.
  try {
    await queueTelegramBotRuntimeConfigApply(readLocalTelegramBotConfigs(), "初始配置");
  } catch (error: any) {
    log(`⚠️  Telegram Bot 启动失败: ${error?.message || String(error)}`);
  }

  log("");
  log("后台服务运行中。按 Ctrl+C 退出。");
  writeDaemonHeartbeat({ state: "running", telegramBot: activeTelegramBotCount() > 0 ? `configured:${activeTelegramBotCount()}` : "missing-token" });

  const shutdown = () => {
    log("\n正在关闭...");
    scheduler.stop();
    stopSentimentRuntime();
    void stopActiveTelegramBots().catch(() => undefined);
    stopAllTelegramBotWorkers();
    releaseTelegramBotLock();
    removeDaemonHeartbeat();
    log("✓ 排程器已停止");
    process.exit(0);
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  // Keep alive
  setInterval(() => {
    writeDaemonHeartbeat({ state: "running", telegramBot: activeTelegramBotCount() > 0 ? `configured:${activeTelegramBotCount()}` : "missing-token" });
  }, 30_000);

  setInterval(() => {
    queueTelegramBotRuntimeConfigApply(readLocalTelegramBotConfigs(), "配置热更新").catch((error: any) => {
      log(`⚠️  Telegram Bot 配置热更新失败: ${error?.message || String(error)}`);
    });
  }, TELEGRAM_BOT_CONFIG_RELOAD_MS);
}

main().catch((error) => {
  console.error(`${LOG_PREFIX} 启动失败:`, error);
  process.exit(1);
});
