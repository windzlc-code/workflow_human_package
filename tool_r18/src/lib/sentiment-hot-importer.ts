import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import type { PersonaArchive } from "@/core/archives/persona-archive-domain";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";
import {
  buildSentimentCandidateId,
  getSentimentHotExcludedIds,
  rememberSentimentHotShown,
  type SentimentHotCandidate,
  type SentimentHotMedia,
  type SentimentHotPlatform,
} from "@/lib/sentiment-candidate-store";
import {
  ensureSentimentRuntime,
  resolveSentimentBackendUrl,
  resolveSentimentDataDir,
  scheduleSentimentRuntimeShutdown,
} from "@/lib/sentiment-runtime-manager";

const require = createRequire(import.meta.url);
const Database = require("better-sqlite3");

export type SentimentCookieHealth = "healthy" | "watch" | "expired" | "missing" | "unknown";

export interface SentimentCookieStatus {
  platform: SentimentHotPlatform;
  health: SentimentCookieHealth;
  label: string;
  message: string;
}

export interface FetchSentimentHotCandidatesResult {
  candidates: SentimentHotCandidate[];
  keywords: string[];
  cookieStatuses: SentimentCookieStatus[];
  warnings: string[];
}

function cleanText(value: unknown): string {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function safeJson(value: unknown): any {
  if (!value || typeof value !== "string") return {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function splitKeywords(value: string): string[] {
  return value
    .split(/[,，、\s#]+/g)
    .map((item) => item.trim())
    .filter((item) => item.length >= 2)
    .slice(0, 12);
}

const GENERIC_SENTIMENT_KEYWORDS = new Set([
  "threads",
  "instagram",
  "thread",
  "ig",
  "生活",
  "情緒",
  "日常",
  "熱門",
  "熱點",
  "推文",
  "文案",
]);

function meaningfulNeedles(keywords: string[]): string[] {
  return keywords
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length >= 2 && !GENERIC_SENTIMENT_KEYWORDS.has(item))
    .slice(0, 12);
}

export function buildSentimentHotKeywords(args: {
  archive?: Partial<Pick<PersonaArchive, "name" | "content" | "setup">>;
  prompt?: string;
  memorySummaries?: string[];
}): string[] {
  const archive = args.archive || {};
  const setup = archive.setup || {};
  const pieces = [
    archive.name,
    Array.isArray((setup as any).genres) ? (setup as any).genres.join(" ") : "",
    (setup as any).contentTheme,
    (setup as any).personality,
    (setup as any).personaType,
    setup.tweetStyleProfile,
    setup.tweetStyleSample,
    archive.content,
    ...(args.memorySummaries || []),
    args.prompt,
  ].map(cleanText).filter(Boolean);
  const joined = pieces.join(" ");
  const extracted = splitKeywords(joined);
  const personaName = cleanText(archive.name);
  const defaults = ["生活", "情緒", "日常", "熱門"];
  return [...new Set([personaName, ...extracted, ...defaults].filter(Boolean))].slice(0, 10);
}

export function cleanSentimentCandidateContent(value: unknown): string {
  let text = cleanText(value);
  text = text
    .replace(/(?:https?:\/\/)?(?:www\.)?(?:threads\.net|instagram\.com)\s*[›>]\s*/gi, " ")
    .replace(/(?:^|\s)(?:@[\w.-]+|t)\s*[›>]\s*(?:post\s*)?/gi, " ")
    .replace(/\s*(?:相關|相关|广告|廣告)\s+.*$/i, "")
    .replace(/\s*&middot;\s*/gi, " ")
    .replace(/\bThreads\s*\.\.\.\s*Threads\b/gi, " ")
    .replace(/\bInstagram\s*\.\.\.\s*Instagram\b/gi, " ")
    .replace(/\bsite:(?:threads\.net|instagram\.com)\b/gi, " ")
    .replace(/^\s*[A-Za-z0-9_-]{8,}\s+/, "")
    .replace(/\s+/g, " ")
    .trim();
  return text;
}

function isLowQualitySentimentContent(value: string): boolean {
  const text = cleanText(value);
  if (text.length < 12) return true;
  if (/not all who wander are lost|link'?s not working|page is gone|go back to keep exploring/i.test(text)) return true;
  if (/^(?:Threads|Instagram)(?:\s*\.\.\.)?$/i.test(text)) return true;
  return false;
}

export async function fetchSentimentCookieStatuses(): Promise<SentimentCookieStatus[]> {
  const runtime = await ensureSentimentRuntime();
  if (!runtime.ok) {
    return [
      { platform: "threads", health: "unknown", label: "Threads", message: runtime.warning || "舆情後端未啟動，無法讀取 Cookie 狀態。" },
      { platform: "instagram", health: "unknown", label: "Instagram", message: runtime.warning || "舆情後端未啟動，無法讀取 Cookie 狀態。" },
    ];
  }
  try {
    const response = await fetch(`${resolveSentimentBackendUrl()}/api/sentiment/browser-auth/profiles`, {
      signal: AbortSignal.timeout(5000),
    });
    const json = await response.json().catch(() => ({}));
    const profiles = Array.isArray(json?.profiles) ? json.profiles : [];
    return (["threads", "instagram"] as SentimentHotPlatform[]).map((platform) => {
      const profile = profiles.find((item: any) => item?.platform === platform || item?.sourceKey === platform || item?.key === platform);
      const health = (profile?.authHealth || (profile ? "healthy" : "missing")) as SentimentCookieHealth;
      const valid = Number(profile?.validCookieCount || 0);
      const expired = Number(profile?.expiredCookieCount || 0);
      return {
        platform,
        health,
        label: platform === "threads" ? "Threads" : "Instagram",
        message: profile
          ? `有效 Cookie ${valid} 個，過期 ${expired} 個。`
          : "缺少授權 Cookie，請到快捷配置頁面刷新。",
      };
    });
  } catch {
    return [
      { platform: "threads", health: "unknown", label: "Threads", message: "無法讀取 Cookie 狀態。" },
      { platform: "instagram", health: "unknown", label: "Instagram", message: "無法讀取 Cookie 狀態。" },
    ];
  } finally {
    scheduleSentimentRuntimeShutdown();
  }
}

export async function fetchSentimentHotCandidates(args: {
  archive?: PersonaArchive;
  prompt?: string;
  memorySummaries?: string[];
  limit?: number;
  refresh?: boolean;
}): Promise<FetchSentimentHotCandidatesResult> {
  const warnings: string[] = [];
  const archive = args.archive;
  const archiveId = cleanText(archive?.id) || "default";
  const keywords = buildSentimentHotKeywords({ archive, prompt: args.prompt, memorySummaries: args.memorySummaries });
  const runtime = await ensureSentimentRuntime();
  if (!runtime.ok && runtime.warning) warnings.push(runtime.warning);
  const cookieStatuses = await fetchSentimentCookieStatuses();
  const usableSources = cookieStatuses
    .filter((status) => status.health === "healthy" || status.health === "watch")
    .map((status) => status.platform);

  if (runtime.ok && usableSources.length > 0) {
    await requestSentimentScanStart(keywords, usableSources).catch((error) => {
      warnings.push(`舆情掃描接口暫不可用：${error instanceof Error ? error.message : String(error)}`);
    });
  } else if (runtime.ok) {
    warnings.push("Threads / Instagram 缺少有效 Cookie，已跳過真實掃描；請先在舆情 Cookie 配置中授權後再刷新抓取。");
  }

  let candidates = await readCandidatesFromDatabase({
    archiveId,
    keywords,
    limit: args.limit || 10,
  });
  if (runtime.ok && usableSources.length > 0 && candidates.length === 0) {
    candidates = await waitForCandidates({
      archiveId,
      keywords,
      limit: args.limit || 10,
    });
  }
  rememberSentimentHotShown(archiveId, candidates);
  scheduleSentimentRuntimeShutdown();
  return { candidates, keywords, cookieStatuses, warnings };
}

async function requestSentimentScanStart(keywords: string[], sources: SentimentHotPlatform[]) {
  const response = await fetch(`${resolveSentimentBackendUrl()}/api/sentiment/scan-start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      keywords,
      keyword: keywords.slice(0, 4).join(" "),
      sources,
      sourceScopes: { fast: sources, full: sources, watch: sources },
      limit: 30,
      reason: "tool-r18-hot-import",
    }),
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
}

async function waitForCandidates(args: { archiveId: string; keywords: string[]; limit: number }): Promise<SentimentHotCandidate[]> {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5_000));
    const candidates = await readCandidatesFromDatabase(args);
    if (candidates.length > 0) return candidates;
  }
  return [];
}

async function readCandidatesFromDatabase(args: { archiveId: string; keywords: string[]; limit: number }): Promise<SentimentHotCandidate[]> {
  const dbPath = path.join(resolveSentimentDataDir(), "crm.db");
  if (!fs.existsSync(dbPath)) return [];
  const db = new Database(dbPath, { readonly: true, fileMustExist: true });
  try {
    const rows = db.prepare(`
      SELECT
        s.id,
        s.platform,
        s.url,
        s.title,
        s.content,
        s.author,
        s.keyword,
        s.keywords,
        s.published_at,
        s.found_at,
        s.first_seen_at,
        s.last_seen_at,
        s.seen_count,
        i.spread_score,
        i.influence_score,
        i.kol_score,
        i.emotions,
        i.extracted_keywords
      FROM crm_sentiment s
      LEFT JOIN crm_sentiment_insights i ON i.sentiment_id = s.id
      WHERE lower(s.platform) IN ('threads', 'instagram')
      ORDER BY
        COALESCE(i.spread_score, 0) + COALESCE(i.influence_score, 0) + COALESCE(i.kol_score, 0) + COALESCE(s.seen_count, 0) DESC,
        datetime(COALESCE(s.last_seen_at, s.found_at, s.first_seen_at)) DESC
      LIMIT 200
    `).all();
    const excluded = getSentimentHotExcludedIds(args.archiveId);
    const needles = meaningfulNeedles(args.keywords);
    const candidates: SentimentHotCandidate[] = [];
    for (const row of rows) {
      const platform = normalizePlatform(row.platform);
      if (!platform) continue;
      const contentCandidate = cleanSentimentCandidateContent(row.content);
      const titleCandidate = cleanSentimentCandidateContent(row.title);
      const content = !isLowQualitySentimentContent(contentCandidate)
        ? contentCandidate
        : !isLowQualitySentimentContent(titleCandidate)
          ? titleCandidate
          : "";
      const sourceUrl = cleanText(row.url);
      if (!content || !sourceUrl) continue;
      const id = buildSentimentCandidateId({ platform, sourceUrl, content });
      if (excluded.has(id)) continue;
      const haystack = [content, row.title, row.author, row.keyword, row.keywords, row.extracted_keywords].map(cleanText).join(" ").toLowerCase();
      const matchedNeedles = needles.filter((needle) => haystack.includes(needle));
      if (needles.length && matchedNeedles.length === 0) continue;
      const relevance = Math.min(60, matchedNeedles.length * 20);
      const media = readMediaForSentiment(db, Number(row.id));
      const hotScore = Math.round(
        Number(row.spread_score || 0)
        + Number(row.influence_score || 0)
        + Number(row.kol_score || 0)
        + Number(row.seen_count || 0)
        + relevance,
      );
      candidates.push({
        id,
        platform,
        sourceUrl,
        author: cleanText(row.author) || "unknown",
        content,
        media,
        hotScore,
        metrics: {
          seenCount: Number(row.seen_count || 0),
          spreadScore: Number(row.spread_score || 0),
          influenceScore: Number(row.influence_score || 0),
          kolScore: Number(row.kol_score || 0),
          emotions: safeJson(row.emotions),
          keywords: safeJson(row.keywords),
        },
        capturedAt: cleanText(row.last_seen_at || row.found_at || row.first_seen_at) || new Date().toISOString(),
        warnings: media.filter((item) => item.warning).map((item) => item.warning as string),
      });
    }
    return candidates
      .sort((a, b) => b.hotScore - a.hotScore || new Date(b.capturedAt).getTime() - new Date(a.capturedAt).getTime())
      .slice(0, args.limit);
  } finally {
    db.close();
  }
}

function normalizePlatform(value: unknown): SentimentHotPlatform | null {
  const text = String(value || "").toLowerCase();
  if (text.includes("thread")) return "threads";
  if (text.includes("instagram") || text === "ins") return "instagram";
  return null;
}

function readMediaForSentiment(db: any, sentimentId: number): SentimentHotMedia[] {
  try {
    const rows = db.prepare(`
      SELECT asset_type, image_url, thumbnail_url, metrics_json
      FROM sentiment_visual_assets
      WHERE sentiment_id = ?
      ORDER BY datetime(captured_at) DESC, id DESC
      LIMIT 4
    `).all(sentimentId);
    return rows.map((row: any) => {
      const url = cleanText(row.image_url || row.thumbnail_url);
      const type = String(row.asset_type || "").toLowerCase().includes("video") ? "video" : "image";
      if (!url) return null;
      return normalizeMedia({ type, url });
    }).filter(Boolean);
  } catch {
    return [];
  }
}

function normalizeMedia(media: { type: "image" | "video"; url: string }): SentimentHotMedia {
  if (/^https?:\/\//i.test(media.url)) {
    return { ...media, warning: "媒體仍為原始連結，寫入時會保留來源。" };
  }
  const resolved = path.isAbsolute(media.url) ? media.url : path.resolve(resolveSentimentDataDir(), media.url);
  return fs.existsSync(resolved) ? { ...media, localPath: resolved } : { ...media, warning: "媒體本地文件不存在，已保留原連結。" };
}

export async function downloadCandidatePrimaryMedia(candidate: SentimentHotCandidate): Promise<SentimentHotMedia | undefined> {
  const primary = candidate.media[0];
  if (!primary) return undefined;
  if (primary.localPath && fs.existsSync(primary.localPath)) return primary;
  if (!/^https?:\/\//i.test(primary.url)) return primary;
  try {
    const response = await fetch(primary.url, { signal: AbortSignal.timeout(15_000) });
    if (!response.ok) return primary;
    const contentType = response.headers.get("content-type") || "";
    if (!/^image\/|^video\//i.test(contentType)) return primary;
    const ext = extensionFromContentType(contentType, primary.type);
    const mediaDir = path.dirname(resolveRuntimeFile(`sentiment-hot-media/${candidate.id}${ext}`));
    fs.mkdirSync(mediaDir, { recursive: true });
    const localPath = path.join(mediaDir, `${candidate.id}${ext}`);
    const buffer = Buffer.from(await response.arrayBuffer());
    fs.writeFileSync(localPath, buffer);
    return { ...primary, localPath, warning: undefined };
  } catch {
    return primary;
  }
}

function extensionFromContentType(contentType: string, type: string): string {
  if (contentType.includes("png")) return ".png";
  if (contentType.includes("webp")) return ".webp";
  if (contentType.includes("gif")) return ".gif";
  if (contentType.includes("mp4")) return ".mp4";
  return type === "video" ? ".mp4" : ".jpg";
}
