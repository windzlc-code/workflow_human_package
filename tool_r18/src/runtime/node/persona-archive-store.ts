import fs from "node:fs";
import path from "node:path";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";

export interface PersonaArchiveRecord {
  id: string;
  name: string;
  content: string;
  createdAt: string;
  updatedAt: string;
  setup?: Record<string, unknown>;
  boundPadCode?: string;
  boundPadName?: string;
  boundTelegramChatId?: string;
  ownerBotName?: string;
  posts: unknown[];
  publishHistory?: unknown[];
  personaImageLibrary?: unknown[];
  personaReferenceSheet?: string;
}

function getStorePath() {
  return resolveRuntimeFile("persona_archives.json");
}

function ensureParentDir(filePath: string) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

const ARCHIVE_LOCK_TIMEOUT_MS = 30_000;
const ARCHIVE_LOCK_POLL_MS = 100;

function getLockPath() {
  return resolveRuntimeFile("persona_archives.lock");
}

function sleepSync(ms: number) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function withArchiveFileLock<T>(fn: () => T): T {
  const lockPath = getLockPath();
  ensureParentDir(lockPath);
  const started = Date.now();
  let fd: number | undefined;
  while (fd === undefined) {
    try {
      fd = fs.openSync(lockPath, "wx");
      fs.writeFileSync(fd, `${process.pid} ${Date.now()}\n`, "utf-8");
    } catch (error) {
      const code = (error as NodeJS.ErrnoException)?.code;
      if (code !== "EEXIST") throw error;
      if (Date.now() - started > ARCHIVE_LOCK_TIMEOUT_MS) {
        throw new Error("persona archive write lock timeout");
      }
      sleepSync(ARCHIVE_LOCK_POLL_MS);
    }
  }
  try {
    return fn();
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
    try { fs.unlinkSync(lockPath); } catch {}
  }
}

// ─── 内存缓存 ─────────────────────────────────────────────────────────────────
let _cache: PersonaArchiveRecord[] | null = null;
let _cacheFileMtime = 0;
const _loadedSnapshots = new Map<string, PersonaArchiveRecord>();

function snapshotKey(id: string, updatedAt: string | undefined): string {
  return `${id}|${String(updatedAt || "")}`;
}

function rememberSnapshot(archive: PersonaArchiveRecord): void {
  _loadedSnapshots.set(snapshotKey(archive.id, archive.updatedAt), cloneValue(archive));
}

function cloneValue<T>(value: T): T {
  return value === undefined ? value : JSON.parse(JSON.stringify(value)) as T;
}

function valuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function recordId(value: unknown): string {
  return value && typeof value === "object" ? String((value as Record<string, unknown>).id || "") : "";
}

function mergeConcurrentValue(base: unknown, current: unknown, incoming: unknown): unknown {
  const incomingChanged = !valuesEqual(base, incoming);
  const currentChanged = !valuesEqual(base, current);
  if (!incomingChanged) return cloneValue(current);
  if (!currentChanged) return cloneValue(incoming);

  if (Array.isArray(base) && Array.isArray(current) && Array.isArray(incoming)) {
    const allItems = [...base, ...current, ...incoming];
    const keyed = allItems.length > 0
      && allItems.every((item) => Boolean(item) && typeof item === "object" && Boolean(recordId(item)));
    if (!keyed) return cloneValue(incoming);
    const baseById = new Map(base.map((item) => [recordId(item), item]));
    const currentById = new Map(current.map((item) => [recordId(item), item]));
    const incomingById = new Map(incoming.map((item) => [recordId(item), item]));
    const orderedIds = [
      ...incoming.map(recordId),
      ...current.map(recordId).filter((id) => !incomingById.has(id)),
    ].filter((id, index, all) => id && all.indexOf(id) === index);
    const merged: unknown[] = [];
    for (const id of orderedIds) {
      const baseItem = baseById.get(id);
      const currentItem = currentById.get(id);
      const incomingItem = incomingById.get(id);
      if (baseItem !== undefined && (incomingItem === undefined || currentItem === undefined)) continue;
      if (incomingItem === undefined) merged.push(cloneValue(currentItem));
      else if (currentItem === undefined) merged.push(cloneValue(incomingItem));
      else merged.push(mergeConcurrentValue(baseItem, currentItem, incomingItem));
    }
    return merged;
  }

  const isObject = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  if (isObject(base) && isObject(current) && isObject(incoming)) {
    const merged: Record<string, unknown> = {};
    const keys = new Set([...Object.keys(base), ...Object.keys(current), ...Object.keys(incoming)]);
    for (const key of keys) {
      if (key === "updatedAt") {
        merged[key] = incoming[key] || current[key];
        continue;
      }
      const value = mergeConcurrentValue(base[key], current[key], incoming[key]);
      if (value !== undefined) merged[key] = value;
    }
    return merged;
  }
  return cloneValue(incoming);
}

function mergeConcurrentArchive(
  base: PersonaArchiveRecord | undefined,
  current: PersonaArchiveRecord | undefined,
  incoming: PersonaArchiveRecord,
): PersonaArchiveRecord {
  if (!base || !current || valuesEqual(base, current)) return incoming;
  return mergeConcurrentValue(base, current, incoming) as PersonaArchiveRecord;
}

function isCacheStale(): boolean {
  if (_cache === null) return true;
  try {
    const stat = fs.statSync(getStorePath());
    return stat.mtimeMs !== _cacheFileMtime;
  } catch {
    // 文件不存在或无法访问 → 缓存一定过期
    return true;
  }
}

function readAll(): PersonaArchiveRecord[] {
  if (!isCacheStale() && _cache !== null) return _cache;
  const filePath = getStorePath();
  if (!fs.existsSync(filePath)) {
    _cache = [];
    _cacheFileMtime = 0;
    return _cache;
  }
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    const parsed = JSON.parse(raw);
    _cache = Array.isArray(parsed) ? parsed : [];
    try { _cacheFileMtime = fs.statSync(filePath).mtimeMs; } catch {}
    return _cache;
  } catch {
    _cache = [];
    return _cache;
  }
}

function writeAllUnlocked(items: PersonaArchiveRecord[]) {
  const filePath = getStorePath();
  ensureParentDir(filePath);
  fs.writeFileSync(filePath, JSON.stringify(items, null, 2), "utf-8");
  _cache = items;
  try { _cacheFileMtime = fs.statSync(filePath).mtimeMs; } catch {}
}

export function installNodePersonaArchiveBridge() {
  const globalAny = globalThis as any;
  if (!globalAny.window) globalAny.window = {};
  if (!globalAny.window.electronAPI) globalAny.window.electronAPI = {};
  if (globalAny.window.electronAPI.personaArchives) return;

  globalAny.window.electronAPI.personaArchives = {
    async save(archive: PersonaArchiveRecord, options?: { baseUpdatedAt?: string }) {
      let savedArchive = archive;
      withArchiveFileLock(() => {
        const items = readAll();
        const idx = items.findIndex((item) => item.id === archive.id);
        const base = _loadedSnapshots.get(snapshotKey(archive.id, options?.baseUpdatedAt));
        if (idx < 0 && base) {
          throw new Error("persona archive was deleted while this update was running");
        }
        const saved = idx >= 0
          ? mergeConcurrentArchive(
              base,
              items[idx],
              archive,
            )
          : archive;
        if (idx >= 0) items[idx] = saved;
        else items.unshift(saved);
        writeAllUnlocked(items);
        rememberSnapshot(saved);
        savedArchive = saved;
      });
      return { ok: true, archive: cloneValue(savedArchive) };
    },
    async load(id: string) {
      const archive = readAll().find((item) => item.id === id) || null;
      if (archive) rememberSnapshot(archive);
      return archive;
    },
    async list() {
      const archives = readAll();
      for (const archive of archives) rememberSnapshot(archive);
      return archives;
    },
    async delete(id: string) {
      withArchiveFileLock(() => {
        const items = readAll().filter((item) => item.id !== id);
        writeAllUnlocked(items);
        for (const key of _loadedSnapshots.keys()) {
          if (key.startsWith(`${id}|`)) _loadedSnapshots.delete(key);
        }
      });
      return { ok: true };
    },
  };
}
