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

// ─── 内存缓存 ─────────────────────────────────────────────────────────────────
let _cache: PersonaArchiveRecord[] | null = null;
let _cacheFileMtime = 0;

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

function writeAll(items: PersonaArchiveRecord[]) {
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
    async save(archive: PersonaArchiveRecord) {
      const items = readAll();
      const idx = items.findIndex((item) => item.id === archive.id);
      if (idx >= 0) items[idx] = archive;
      else items.unshift(archive);
      writeAll(items);
      return { ok: true };
    },
    async load(id: string) {
      return readAll().find((item) => item.id === id) || null;
    },
    async list() {
      return readAll();
    },
    async delete(id: string) {
      const items = readAll().filter((item) => item.id !== id);
      writeAll(items);
      return { ok: true };
    },
  };
}
