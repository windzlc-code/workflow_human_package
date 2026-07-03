import "@/runtime/node/browser-shim";
import { startTelegramBot, stopTelegramPolling, type TelegramBotInstanceOptions } from "@/telegram-bot";

type WorkerConfig = TelegramBotInstanceOptions & { token: string };

function readWorkerConfig(): WorkerConfig {
  const raw = String(process.env.TELEGRAM_BOT_WORKER_CONFIG || "").trim();
  if (!raw) throw new Error("TELEGRAM_BOT_WORKER_CONFIG is required");
  const parsed = JSON.parse(raw);
  const token = String(parsed?.token || "").trim();
  if (!token) throw new Error("Telegram bot worker token is required");
  return {
    ...parsed,
    token,
    name: String(parsed?.name || "bot-worker").trim() || "bot-worker",
    enableWebhookServer: false,
  };
}

const config = readWorkerConfig();
const bot = startTelegramBot(config.token, config);

console.log(`[telegram-worker] started name=${config.name || "bot-worker"}`);

const heartbeat = setInterval(() => {
  console.log(`[telegram-worker] heartbeat name=${config.name || "bot-worker"} pid=${process.pid}`);
}, 30_000);
heartbeat.unref?.();

async function shutdown() {
  clearInterval(heartbeat);
  await bot.stopPolling().catch(() => undefined);
  await stopTelegramPolling(config.token).catch(() => undefined);
  console.log(`[telegram-worker] stopped name=${config.name || "bot-worker"}`);
  process.exit(0);
}

process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());
