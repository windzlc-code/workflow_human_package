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
    .split(/[,，、。.!！?？；;：:\s#]+/g)
    .map((item) => item.trim())
    .filter((item) => item.length >= 2 && item.length <= 24)
    .slice(0, 12);
}

function hasHan(value: unknown): boolean {
  return /[\u3400-\u9fff]/u.test(String(value || ""));
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
    .filter((item) => item.length >= 2 && item.length <= 40 && !GENERIC_SENTIMENT_KEYWORDS.has(item))
    .slice(0, 12);
}

function addMedicalTopicKeywords(out: string[], text: string) {
  if (!/[医醫]|医生|醫生|医院|醫院|医疗|醫療|手术|手術|诊所|診所|医美|醫美/.test(text)) return;
  out.push(
    "醫療",
    "医疗",
    "醫生",
    "医生",
    "醫院",
    "医院",
    "醫療糾紛",
    "医疗纠纷",
    "醫療事故",
    "医疗事故",
    "黑心診所",
    "黑心诊所",
    "醫美",
    "医美",
    "手術",
    "手术",
  );
  if (/黑暗|邪恶|邪惡|反派|阴谋|陰謀|恐怖|壓迫|压迫/.test(text)) {
    out.push("黑心醫生", "黑心医生", "醫院爆料", "医院爆料", "醫療爭議", "医疗争议");
  }
}

function addGeneralTopicKeywords(out: string[], text: string) {
  const groups: Array<[RegExp, string[]]> = [
    [/(教师|老師|老师|校園|校园|學生|学生|课堂|課堂)/, ["老師", "教師", "學生", "校園", "課堂"]],
    [/(恋爱|戀愛|情感|感情|暧昧|曖昧|分手|关系|關係)/, ["戀愛", "感情", "曖昧", "分手", "關係"]],
    [/(穿搭|美妆|美妝|护肤|護膚|拍照|女生|日系)/, ["穿搭", "美妝", "護膚", "女生", "拍照"]],
    [/(AI|人工智能|人工智慧|自动化|自動化|科技|互联网|互聯網|职场|職場)/i, ["AI", "人工智慧", "自動化", "科技", "職場"]],
  ];
  for (const [pattern, values] of groups) {
    if (pattern.test(text)) out.push(...values);
  }
}

function buildSearchKeywordCandidates(args: {
  archiveName: string;
  pieces: string[];
}): string[] {
  const joined = args.pieces.join(" ");
  const out: string[] = [];
  addMedicalTopicKeywords(out, joined);
  addGeneralTopicKeywords(out, joined);
  for (const item of splitKeywords(joined)) {
    if (!hasHan(item)) continue;
    if (args.archiveName && item.includes(args.archiveName)) continue;
    out.push(item);
  }
  return [...new Set(out)]
    .filter((item) => item.length >= 2 && item.length <= 12 && !GENERIC_SENTIMENT_KEYWORDS.has(item.toLowerCase()))
    .slice(0, 10);
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
  const personaName = cleanText(archive.name);
  const defaults = ["生活", "情緒", "日常", "熱門"];
  const extracted = buildSearchKeywordCandidates({ archiveName: personaName, pieces });
  return [...new Set([...extracted, ...defaults].filter(Boolean))].slice(0, 10);
}

export function cleanSentimentCandidateContent(value: unknown): string {
  let text = cleanText(value);
  text = text
    .replace(/\s*登入以取得更多有關此主題的串文。.*$/i, "")
    .replace(/\s*登入或註冊 Threads.*$/i, "")
    .replace(/\s*登录以获取更多有关此话题的串文。.*$/i, "")
    .replace(/\s*登录或注册 Threads.*$/i, "")
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

export function isChineseSentimentCandidate(value: unknown): boolean {
  const text = cleanText(value);
  const hanCount = (text.match(/[\u3400-\u9fff]/gu) || []).length;
  if (hanCount < 6) return false;
  const latinCount = (text.match(/[A-Za-z]/g) || []).length;
  return hanCount >= 12 || hanCount >= latinCount * 0.3;
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
    await syncSentimentKeywords(keywords).catch((error) => {
      warnings.push(`舆情关键词同步失败：${error instanceof Error ? error.message : String(error)}`);
    });
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
      timeoutMs: 15_000,
    });
  }
  if (candidates.length === 0) {
    const fallbackCandidates = await fetchThreadsSearchPageCandidates({
      archiveId,
      keywords,
      limit: args.limit || 10,
    }).catch((error) => {
      warnings.push(`Threads 页面兜底抓取失败：${error instanceof Error ? error.message : String(error)}`);
      return [];
    });
    if (fallbackCandidates.length > 0) {
      warnings.push("主搜索源当前没有产出，已改用 Threads 页面兜底抓取中文热点。");
      candidates = fallbackCandidates;
    }
  }
  if (candidates.length === 0) {
    warnings.push("未找到符合当前人设关键词的中文 Threads / Instagram 热点；如果 Cookie 正常，通常是当前关键词扫描源没有产出，建议刷新或换更宽的中文关键词。");
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

async function syncSentimentKeywords(keywords: string[]) {
  const usableKeywords = meaningfulNeedles(keywords).slice(0, 6);
  for (const keyword of usableKeywords) {
    const response = await fetch(`${resolveSentimentBackendUrl()}/api/sentiment/keywords`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ keyword }),
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok && response.status !== 409) throw new Error(`HTTP ${response.status}`);
  }
}

async function waitForCandidates(args: { archiveId: string; keywords: string[]; limit: number; timeoutMs?: number }): Promise<SentimentHotCandidate[]> {
  const deadline = Date.now() + (args.timeoutMs || 45_000);
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5_000));
    const candidates = await readCandidatesFromDatabase(args);
    if (candidates.length > 0) return candidates;
  }
  return [];
}

async function fetchThreadsSearchPageCandidates(args: {
  archiveId: string;
  keywords: string[];
  limit: number;
}): Promise<SentimentHotCandidate[]> {
  const queries = buildThreadsSearchQueries(args.keywords).slice(0, 6);
  const excluded = getSentimentHotExcludedIds(args.archiveId);
  const results: SentimentHotCandidate[] = [];
  if (queries.length === 0) return results;

  const { chromium } = await import("playwright");
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const context = await browser.newContext({
      locale: "zh-TW",
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    });
    const page = await context.newPage();
    for (const query of queries) {
      if (results.length >= args.limit) break;
      const searchUrl = `https://www.threads.net/search?q=${encodeURIComponent(query)}`;
      await page.goto(searchUrl, { waitUntil: "domcontentloaded", timeout: 20_000 });
      await page.waitForTimeout(2_500);
      const text = await page.locator("body").innerText({ timeout: 5_000 }).catch(() => "");
      const parsed = parseThreadsSearchTextCandidates({
        text,
        query,
        keywords: args.keywords,
        limit: args.limit - results.length,
        sourceUrl: page.url() || searchUrl,
      });
      for (const candidate of parsed) {
        if (excluded.has(candidate.id)) continue;
        if (results.some((item) => item.id === candidate.id || item.content === candidate.content)) continue;
        results.push(candidate);
        if (results.length >= args.limit) break;
      }
    }
    await context.close();
  } finally {
    await browser.close().catch(() => undefined);
  }
  return results.sort((a, b) => b.hotScore - a.hotScore).slice(0, args.limit);
}

function buildThreadsSearchQueries(keywords: string[]): string[] {
  const joined = keywords.join(" ");
  const out: string[] = [];
  const add = (value: string) => {
    const text = cleanText(value);
    if (!text) return;
    if (!hasHan(text) && !/^AI$/i.test(text)) return;
    if (text.length > 10) return;
    out.push(text);
  };
  const synonymGroups: Array<[RegExp, string[]]> = [
    [/(教师|老師|老师|校園|校园|學生|学生|课堂|課堂)/, ["老師", "教師", "學生", "校園", "課堂"]],
    [/(恋爱|戀愛|情感|感情|暧昧|曖昧|分手|关系|關係)/, ["戀愛", "感情", "曖昧", "分手", "關係"]],
    [/(穿搭|美妆|美妝|护肤|護膚|拍照|女生|日系)/, ["穿搭", "美妝", "護膚", "女生", "拍照"]],
    [/(医疗|醫療|医生|醫生|医院|醫院|手术|手術|医美|醫美)/, ["醫療", "醫生", "醫院", "醫療事故", "醫美"]],
    [/(AI|人工智能|人工智慧|自动化|自動化|科技|互联网|互聯網|职场|職場)/i, ["AI", "人工智慧", "自動化", "科技", "職場"]],
  ];
  for (const [pattern, values] of synonymGroups) {
    if (pattern.test(joined)) values.forEach(add);
  }
  for (const keyword of meaningfulNeedles(keywords)) {
    add(keyword);
    for (const part of splitKeywords(keyword)) add(part);
  }
  return [...new Set(out)].slice(0, 12);
}

const THREADS_SEARCH_NOISE_LINES = new Set([
  "threads",
  "instagram",
  "登入",
  "登录",
  "註冊",
  "注册",
  "翻譯",
  "翻译",
  "搜尋",
  "搜索",
  "搜尋 Threads",
  "搜索 Threads",
  "使用 Instagram 帳號繼續",
  "使用 Instagram 账号继续",
  "建立新帳號",
  "创建新帐号",
  "隱私政策",
  "隐私政策",
  "Cookie 政策",
  "使用條款",
  "使用条款",
  "回報問題",
  "报告问题",
]);

function isThreadsSearchNoiseLine(line: string, query: string): boolean {
  const text = cleanText(line);
  if (!text) return true;
  if (text === query) return true;
  if (THREADS_SEARCH_NOISE_LINES.has(text)) return true;
  if (/^©\s*\d{4}/.test(text)) return true;
  if (/^[\d,.，]+(?:\s*[萬万])?$/.test(text)) return true;
  if (/^\[\d+\]$/.test(text)) return true;
  if (/^(?:\d+\s*(?:秒|分鐘|分钟|小時|小时|天|週|周|月|年)|昨天|前天)$/.test(text)) return true;
  if (/^\d{4}[-/]\d{1,2}[-/]\d{1,2}$/.test(text)) return true;
  if (/^(?:所有|最新|热门|熱門)$/.test(text)) return true;
  return false;
}

function isLikelyThreadsHandle(line: string): boolean {
  const text = line.trim();
  if (!/^@?[A-Za-z0-9_.]{2,32}$/.test(text)) return false;
  if (!/[A-Za-z_]/.test(text)) return false;
  return !/^(?:threads|instagram|search|login|home|profile|www|net|com|t)$/i.test(text);
}

function parseThreadsHotScore(lines: string[]): number {
  let score = 30;
  for (const line of lines) {
    const text = line.replace(/,/g, "").trim();
    const wan = text.match(/^(\d+(?:\.\d+)?)\s*[萬万]$/);
    if (wan) score += Math.round(Number(wan[1]) * 10_000);
    const plain = text.match(/^\[?(\d{1,6})\]?$/);
    if (plain) score += Math.min(20_000, Number(plain[1]));
  }
  return score;
}

export function parseThreadsSearchTextCandidates(args: {
  text: string;
  query: string;
  keywords?: string[];
  limit?: number;
  sourceUrl: string;
}): SentimentHotCandidate[] {
  const query = cleanText(args.query);
  const lines = String(args.text || "")
    .split(/\r?\n/g)
    .map((line) => cleanText(line))
    .filter(Boolean);
  const chunks: Array<{ author: string; lines: string[] }> = [];
  let current: { author: string; lines: string[] } | null = null;

  for (const line of lines) {
    if (isLikelyThreadsHandle(line)) {
      if (current?.lines.length) chunks.push(current);
      current = { author: line.replace(/^@/, ""), lines: [] };
      continue;
    }
    if (!current) continue;
    current.lines.push(line);
  }
  if (current?.lines.length) chunks.push(current);

  const needles = meaningfulNeedles([...(args.keywords || []), query])
    .filter((keyword) => hasHan(keyword))
    .slice(0, 10);
  const out: SentimentHotCandidate[] = [];
  for (const [index, chunk] of chunks.entries()) {
    const contentLines = chunk.lines
      .filter((line) => !isThreadsSearchNoiseLine(line, query))
      .filter((line) => hasHan(line));
    const content = cleanSentimentCandidateContent(contentLines.join(" "));
    if (isLowQualitySentimentContent(content)) continue;
    if (!isChineseSentimentCandidate(content)) continue;
    if ((content.match(/[\u3400-\u9fff]/gu) || []).length < 18) continue;
    const haystack = [content, chunk.author].join(" ").toLowerCase();
    const matchedNeedles = needles.filter((needle) => haystack.includes(needle.toLowerCase()));
    if (needles.length && matchedNeedles.length === 0) continue;
    const sourceUrl = `${args.sourceUrl}#candidate-${index + 1}`;
    const id = buildSentimentCandidateId({ platform: "threads", sourceUrl, content });
    out.push({
      id,
      platform: "threads",
      sourceUrl,
      author: chunk.author || "unknown",
      content,
      media: [],
      hotScore: parseThreadsHotScore(chunk.lines) + matchedNeedles.length * 20,
      metrics: {
        source: "threads-search-page",
        matchedKeywords: matchedNeedles,
      },
      capturedAt: new Date().toISOString(),
      warnings: ["Threads 搜索页面未暴露稳定媒体地址，已先保留文字热点。"],
    });
    if (out.length >= (args.limit || 10)) break;
  }
  return out;
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
      if (!isChineseSentimentCandidate(content)) continue;
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
