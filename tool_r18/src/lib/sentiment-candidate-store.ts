import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";

export type SentimentHotPlatform = "threads" | "instagram";
export type SentimentHotMediaType = "image" | "video" | "unknown";

export interface SentimentHotMedia {
  type: SentimentHotMediaType;
  url: string;
  localPath?: string;
  warning?: string;
}

export interface SentimentHotCandidate {
  id: string;
  platform: SentimentHotPlatform;
  sourceUrl: string;
  author: string;
  content: string;
  media: SentimentHotMedia[];
  hotScore: number;
  metrics: Record<string, unknown>;
  engagement?: {
    likeCount?: number;
    commentCount?: number;
    viewCount?: number;
    shareCount?: number;
    rawSignals?: number[];
  };
  publishedAt?: string;
  capturedAt: string;
  warnings?: string[];
  qaPassed?: boolean;
}

type StoreState = {
  shown: Record<string, Array<string | { id: string; at?: string }>>;
  selected: Record<string, string[]>;
  imported: Record<string, string[]>;
};

const STORE_FILE = resolveRuntimeFile("sentiment_hot_candidates.json");
const STORE_LOCK_FILE = resolveRuntimeFile("sentiment_hot_candidates.lock");
const STORE_LOCK_TIMEOUT_MS = 30_000;
const STORE_LOCK_POLL_MS = 100;
const STORE_LOCK_STALE_MS = 2 * 60_000;

function sleepSync(ms: number) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function clearStaleStoreLock(): boolean {
  try {
    const [pidText, createdAtText] = fs.readFileSync(STORE_LOCK_FILE, "utf8").trim().split(/\s+/);
    const pid = Number(pidText);
    const createdAt = Number(createdAtText);
    let ownerAlive = Number.isInteger(pid) && pid > 0;
    if (ownerAlive) {
      try {
        process.kill(pid, 0);
      } catch (error) {
        ownerAlive = (error as NodeJS.ErrnoException)?.code !== "ESRCH";
      }
    }
    if (ownerAlive && Number.isFinite(createdAt) && Date.now() - createdAt <= STORE_LOCK_STALE_MS) return false;
    fs.unlinkSync(STORE_LOCK_FILE);
    return true;
  } catch {
    return false;
  }
}

function withStoreFileLock<T>(fn: () => T): T {
  fs.mkdirSync(path.dirname(STORE_LOCK_FILE), { recursive: true });
  const started = Date.now();
  let fd: number | undefined;
  while (fd === undefined) {
    try {
      fd = fs.openSync(STORE_LOCK_FILE, "wx");
      fs.writeFileSync(fd, `${process.pid} ${Date.now()}\n`, "utf8");
    } catch (error) {
      const code = (error as NodeJS.ErrnoException)?.code;
      if (code !== "EEXIST") throw error;
      if (clearStaleStoreLock()) continue;
      if (Date.now() - started > STORE_LOCK_TIMEOUT_MS) {
        throw new Error("sentiment candidate store write lock timeout");
      }
      sleepSync(STORE_LOCK_POLL_MS);
    }
  }
  try {
    return fn();
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
    try { fs.unlinkSync(STORE_LOCK_FILE); } catch {}
  }
}

function emptyState(): StoreState {
  return { shown: {}, selected: {}, imported: {} };
}

function readState(): StoreState {
  try {
    if (!fs.existsSync(STORE_FILE)) return emptyState();
    const parsed = JSON.parse(fs.readFileSync(STORE_FILE, "utf8"));
    return {
      shown: parsed?.shown && typeof parsed.shown === "object" ? parsed.shown : {},
      selected: parsed?.selected && typeof parsed.selected === "object" ? parsed.selected : {},
      imported: parsed?.imported && typeof parsed.imported === "object" ? parsed.imported : {},
    };
  } catch {
    return emptyState();
  }
}

function writeState(state: StoreState) {
  fs.mkdirSync(path.dirname(STORE_FILE), { recursive: true });
  const tempFile = `${STORE_FILE}.tmp-${process.pid}-${crypto.randomUUID()}`;
  try {
    fs.writeFileSync(tempFile, JSON.stringify(state, null, 2), "utf8");
    fs.renameSync(tempFile, STORE_FILE);
  } finally {
    try { fs.unlinkSync(tempFile); } catch {}
  }
}

export function buildSentimentCandidateId(input: { platform: string; sourceUrl?: string; content?: string }): string {
  const stable = [input.platform, input.sourceUrl || "", input.content || ""].join("\n");
  return crypto.createHash("sha1").update(stable).digest("hex").slice(0, 20);
}

export function getSentimentHotExcludedIds(archiveId: string): Set<string> {
  const state = readState();
  return new Set([
    ...(state.selected[archiveId] || []),
    ...(state.imported[archiveId] || []),
  ]);
}

function shownEntryId(entry: string | { id: string; at?: string }): string {
  return typeof entry === "string" ? entry : String(entry?.id || "");
}

export function getSentimentHotRefreshExcludedIds(archiveId: string): Set<string> {
  const state = readState();
  return new Set([
    ...(state.shown[archiveId] || []).map(shownEntryId).filter(Boolean),
    ...(state.selected[archiveId] || []),
    ...(state.imported[archiveId] || []),
  ]);
}

export function getSentimentHotShownIds(archiveId: string): Set<string> {
  const state = readState();
  return new Set((state.shown[archiveId] || []).map(shownEntryId).filter(Boolean));
}

export function getSentimentHotShownAtMap(archiveId: string): Map<string, number> {
  const state = readState();
  const result = new Map<string, number>();
  for (const entry of state.shown[archiveId] || []) {
    const id = shownEntryId(entry);
    if (!id) continue;
    const at = typeof entry === "string" ? "" : String(entry.at || "");
    const time = Date.parse(at);
    result.set(id, Number.isFinite(time) ? time : 0);
  }
  return result;
}

export function rememberSentimentHotShown(archiveId: string, candidates: SentimentHotCandidate[]) {
  withStoreFileLock(() => {
    const state = readState();
    const now = new Date().toISOString();
    const current = new Map<string, { id: string; at: string }>();
    for (const entry of state.shown[archiveId] || []) {
      const id = shownEntryId(entry);
      if (!id) continue;
      const at = typeof entry === "string" ? "" : String(entry.at || "");
      current.set(id, { id, at });
    }
    for (const candidate of candidates) current.set(candidate.id, { id: candidate.id, at: now });
    state.shown[archiveId] = [...current.values()].slice(-500);
    writeState(state);
  });
}

export function rememberSentimentHotSelected(archiveId: string, candidateId: string) {
  withStoreFileLock(() => {
    const state = readState();
    const selected = new Set(state.selected[archiveId] || []);
    selected.add(candidateId);
    state.selected[archiveId] = [...selected].slice(-500);
    writeState(state);
  });
}

export function rememberSentimentHotImported(archiveId: string, candidateId: string) {
  withStoreFileLock(() => {
    const state = readState();
    const imported = new Set(state.imported[archiveId] || []);
    imported.add(candidateId);
    state.imported[archiveId] = [...imported].slice(-500);
    writeState(state);
  });
}
