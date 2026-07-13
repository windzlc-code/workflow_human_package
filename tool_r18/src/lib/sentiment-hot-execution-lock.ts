import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";

const LOCK_FILE = resolveRuntimeFile("sentiment-hot-execution.lock");
const LOCK_WAIT_MS = Math.max(Number(process.env.SENTIMENT_HOT_LOCK_WAIT_MS || 90_000), 1_000);
const LOCK_STALE_MS = Math.max(Number(process.env.SENTIMENT_HOT_LOCK_STALE_MS || 15 * 60_000), 60_000);
const LOCK_POLL_MS = Math.max(Number(process.env.SENTIMENT_HOT_LOCK_POLL_MS || 500), 100);

type LockRecord = {
  token: string;
  owner: string;
  pid: number;
  createdAt: number;
};

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function readLockRecord(): Promise<LockRecord | null> {
  try {
    const parsed = JSON.parse(await fs.promises.readFile(LOCK_FILE, "utf8"));
    return parsed && typeof parsed === "object" ? parsed as LockRecord : null;
  } catch {
    return null;
  }
}

async function removeStaleLock(): Promise<boolean> {
  let ageMs = 0;
  const record = await readLockRecord();
  if (Number(record?.createdAt) > 0) {
    ageMs = Date.now() - Number(record?.createdAt);
  } else {
    try {
      ageMs = Date.now() - (await fs.promises.stat(LOCK_FILE)).mtimeMs;
    } catch {
      return true;
    }
  }
  if (ageMs < LOCK_STALE_MS) return false;
  const expectedToken = String(record?.token || "");
  const current = await readLockRecord();
  if (expectedToken && String(current?.token || "") !== expectedToken) return false;
  await fs.promises.unlink(LOCK_FILE).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
  return true;
}

async function acquireLock(owner: string): Promise<LockRecord> {
  await fs.promises.mkdir(path.dirname(LOCK_FILE), { recursive: true });
  const deadline = Date.now() + LOCK_WAIT_MS;
  const record: LockRecord = {
    token: crypto.randomUUID(),
    owner,
    pid: process.pid,
    createdAt: Date.now(),
  };
  let waitingLogged = false;
  while (Date.now() < deadline) {
    try {
      const handle = await fs.promises.open(LOCK_FILE, "wx", 0o600);
      try {
        await handle.writeFile(JSON.stringify(record));
      } finally {
        await handle.close();
      }
      return record;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== "EEXIST") throw error;
      if (await removeStaleLock()) continue;
      if (!waitingLogged) {
        waitingLogged = true;
        const holder = await readLockRecord();
        console.info(`[sentiment_hot_lock] waiting owner=${owner} holder=${holder?.owner || "unknown"} pid=${holder?.pid || "unknown"}`);
      }
      await wait(LOCK_POLL_MS);
    }
  }
  throw new Error("热点抓取队列繁忙，已有任务正在刷新，请稍后重试。");
}

async function releaseLock(record: LockRecord): Promise<void> {
  const current = await readLockRecord();
  if (String(current?.token || "") !== record.token) return;
  await fs.promises.unlink(LOCK_FILE).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
}

export async function withSentimentHotExecutionLock<T>(owner: string, task: () => Promise<T>): Promise<T> {
  const record = await acquireLock(owner);
  try {
    return await task();
  } finally {
    await releaseLock(record);
  }
}
