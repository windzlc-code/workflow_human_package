import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import type { PersonaArchive } from "@/core/archives/persona-archive-domain";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";
import {
  buildSentimentCandidateId,
  getSentimentHotExcludedIds,
  getSentimentHotRefreshExcludedIds,
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

const BROAD_THREADS_SEARCH_QUERIES = ["生活", "日常", "熱門", "分享", "台灣", "心情", "今天", "最近", "穿搭", "美食", "遊戲", "戀愛"];

const DYNAMIC_KEYWORD_STOPWORDS = new Set([
  "人設",
  "人设",
  "內容",
  "内容",
  "風格",
  "风格",
  "推文",
  "文案",
  "生成",
  "圖片",
  "图片",
  "指定",
  "不指定",
  "工作流",
  "角色",
  "設定",
  "设定",
  "目前風格",
  "目前风格",
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
    [/(動漫|动漫|二次元|遊戲|游戏|漫展|手辦|手办|cos|cosplay)/i, ["動漫", "二次元", "遊戲", "漫展", "cosplay"]],
    [/(美食|吃貨|吃货|餐廳|餐厅|咖啡|甜點|甜点|料理|宵夜)/, ["美食", "餐廳", "咖啡", "甜點", "宵夜"]],
    [/(旅行|旅遊|旅游|露營|露营|景點|景点|出遊|出游|城市|拍照)/, ["旅行", "旅遊", "景點", "露營", "城市"]],
    [/(寵物|宠物|貓|猫|狗|小動物|小动物|毛孩)/, ["寵物", "貓", "狗", "毛孩", "小動物"]],
    [/(家庭|媽媽|妈妈|太太|人妻|婚姻|育兒|育儿|家務|家务|親子|亲子)/, ["家庭", "媽媽", "婚姻", "育兒", "親子"]],
    [/(上班|職場|职场|同事|主管|公司|加班|辦公室|办公室|工作)/, ["職場", "上班", "同事", "加班", "辦公室"]],
    [/(健身|運動|运动|跑步|瑜伽|球類|球类|排球|籃球|篮球|足球)/, ["健身", "運動", "跑步", "排球", "籃球"]],
    [/(娛樂|娱乐|明星|偶像|八卦|追星|影劇|影剧|電影|电影|綜藝|综艺)/, ["娛樂", "明星", "八卦", "影劇", "電影"]],
    [/(理財|理财|投資|投资|股票|基金|幣圈|币圈|副業|副业|賺錢|赚钱)/, ["理財", "投資", "股票", "副業", "賺錢"]],
    [/(法律|律師|律师|法院|案件|糾紛|纠纷|警察|社會|社会|新聞|新闻)/, ["法律", "社會", "新聞", "案件", "糾紛"]],
  ];
  for (const [pattern, values] of groups) {
    if (pattern.test(text)) out.push(...values);
  }
}

function normalizeDynamicKeyword(value: string, archiveName: string): string {
  return cleanText(value)
    .replace(/^[-_*#\d.、\s]+/g, "")
    .replace(/^(人設|人设|類型|类型|性格|內容|内容|風格|风格|主題|主题|模式|記憶|记忆)[:：]/, "")
    .replace(/^(改成|改為|改为|換成|换成|修改成|修改為|修改为|內容以|内容以|以)/, "")
    .replace(archiveName ? new RegExp(archiveName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g") : /$^/, "")
    .replace(/(人設|人设|設定|设定|風格|风格|推文|文案|為主|为主)$/g, "")
    .trim();
}

function extractDynamicPersonaKeywords(args: { archiveName: string; pieces: string[] }): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const add = (value: string) => {
    const text = normalizeDynamicKeyword(value, args.archiveName);
    const key = text.toLowerCase();
    if (!hasHan(text)) return;
    if (text.length < 2 || text.length > 14) return;
    if (DYNAMIC_KEYWORD_STOPWORDS.has(text) || DYNAMIC_KEYWORD_STOPWORDS.has(key)) return;
    if (args.archiveName && text.includes(args.archiveName)) return;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(text);
  };

  for (const piece of args.pieces) {
    const cleaned = cleanText(piece)
      .replace(/https?:\/\/\S+/g, " ")
      .replace(/[「」『』“”"'()[\]{}]/g, " ");
    for (const segment of cleaned.split(/[,，、。.!！?？；;：:\n\r]+/g)) {
      const text = normalizeDynamicKeyword(segment, args.archiveName);
      if (!text) continue;
      add(text);
      for (const token of text.split(/\s+|和|與|与|及|以及|跟/g)) add(token);
    }
  }
  return out.slice(0, 8);
}

function buildSearchKeywordCandidates(args: {
  archiveName: string;
  pieces: string[];
}): string[] {
  const joined = args.pieces.join(" ");
  const out: string[] = [];
  out.push(...extractDynamicPersonaKeywords(args));
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
  const defaults = BROAD_THREADS_SEARCH_QUERIES;
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
  const limit = args.limit || 10;

  let candidates = await fetchThreadsSearchPageCandidates({
    archiveId,
    keywords,
    limit,
    refresh: args.refresh === true,
  }).catch((error) => {
    warnings.push("\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u0072\u0065\u0061\u0064\u0065\u0072\u0020\u6293\u53d6\u5931\u6557\uff1a" + (error instanceof Error ? error.message : String(error)));
    return [];
  });
  if (candidates.length > 0) {
    warnings.push(args.refresh ? "\u5df2\u5373\u6642\u5237\u65b0\u0020\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u0072\u0065\u0061\u0064\u0065\u0072\u0020\u4e2d\u6587\u71b1\u9ede\u3002" : "\u5df2\u4f7f\u7528\u0020\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u0072\u0065\u0061\u0064\u0065\u0072\u0020\u6293\u53d6\u4e2d\u6587\u71b1\u9ede\u3002");
  }

  const runtime = await withSentimentTimeout(ensureSentimentRuntime(), 6_000, {
    ok: false,
    url: resolveSentimentBackendUrl(),
    warning: "\u8206\u60c5\u5f8c\u53f0\u555f\u52d5\u8f03\u6162\uff0c\u5df2\u512a\u5148\u4f7f\u7528\u0020\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u0072\u0065\u0061\u0064\u0065\u0072\u0020\u5019\u9078\u3002",
  });
  if (!runtime.ok && runtime.warning) warnings.push(runtime.warning);

  const cookieStatuses = await withSentimentTimeout(fetchSentimentCookieStatuses(), 6_000, [
    { platform: "threads" as const, health: "unknown" as const, label: "Threads", message: "\u8206\u60c5\u0020\u0043\u006f\u006f\u006b\u0069\u0065\u0020\u72c0\u614b\u6aa2\u67e5\u8d85\u6642\u3002" },
    { platform: "instagram" as const, health: "unknown" as const, label: "Instagram", message: "\u8206\u60c5\u0020\u0043\u006f\u006f\u006b\u0069\u0065\u0020\u72c0\u614b\u6aa2\u67e5\u8d85\u6642\u3002" },
  ]);
  const usableSources = cookieStatuses
    .filter((status) => status.health === "healthy" || status.health === "watch")
    .map((status) => status.platform);

  if (!args.refresh && candidates.length < limit) {
    const databaseCandidates = await readCandidatesFromDatabase({ archiveId, keywords, limit });
    if (databaseCandidates.length > 0) {
      const byId = new Map(candidates.map((candidate) => [candidate.id, candidate]));
      for (const candidate of databaseCandidates) {
        if (!byId.has(candidate.id)) byId.set(candidate.id, candidate);
        if (byId.size >= limit) break;
      }
      candidates = [...byId.values()].sort((a, b) => b.hotScore - a.hotScore).slice(0, limit);
    }
  }

  if (runtime.ok && usableSources.length > 0) {
    void syncSentimentKeywords(keywords).catch(() => undefined);
  } else if (runtime.ok) {
    warnings.push("\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u002f\u0020\u0049\u006e\u0073\u0074\u0061\u0067\u0072\u0061\u006d\u0020\u7f3a\u5c11\u6709\u6548\u0020\u0043\u006f\u006f\u006b\u0069\u0065\uff0c\u5df2\u8df3\u904e\u771f\u5be6\u6383\u63cf\uff1b\u8acb\u5148\u5728\u8206\u60c5\u0020\u0043\u006f\u006f\u006b\u0069\u0065\u0020\u914d\u7f6e\u4e2d\u6388\u6b0a\u5f8c\u518d\u5237\u65b0\u6293\u53d6\u3002");
  }

  if (candidates.length === 0) {
    warnings.push("\u672a\u627e\u5230\u7b26\u5408\u7576\u524d\u4eba\u8a2d\u95dc\u9375\u8a5e\u7684\u4e2d\u6587\u0020\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u002f\u0020\u0049\u006e\u0073\u0074\u0061\u0067\u0072\u0061\u006d\u0020\u71b1\u9ede\uff1b\u5982\u679c\u0020\u0043\u006f\u006f\u006b\u0069\u0065\u0020\u6b63\u5e38\uff0c\u901a\u5e38\u662f\u7576\u524d\u95dc\u9375\u8a5e\u6383\u63cf\u6e90\u6c92\u6709\u7522\u51fa\uff0c\u5efa\u8b70\u5237\u65b0\u6216\u63db\u66f4\u5bec\u7684\u4e2d\u6587\u95dc\u9375\u8a5e\u3002");
  }
  rememberSentimentHotShown(archiveId, candidates);
  scheduleSentimentRuntimeShutdown();
  return { candidates, keywords, cookieStatuses, warnings };
}

async function withSentimentTimeout<T>(promise: Promise<T>, timeoutMs: number, fallback: T): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((resolve) => {
        timer = setTimeout(() => resolve(fallback), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
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

async function fetchThreadsSearchPageCandidates(args: {
  archiveId: string;
  keywords: string[];
  limit: number;
  refresh?: boolean;
}): Promise<SentimentHotCandidate[]> {
  const queries = buildThreadsSearchQueries(args.keywords).slice(0, 8);
  const excluded = args.refresh ? getSentimentHotRefreshExcludedIds(args.archiveId) : getSentimentHotExcludedIds(args.archiveId);
  const results: SentimentHotCandidate[] = [];
  if (queries.length === 0) return results;

  let readerResults = await fetchThreadsReaderSearchCandidates({
    archiveId: args.archiveId,
    keywords: args.keywords,
    queries,
    limit: args.limit,
    refresh: args.refresh,
  }).catch(() => []);
  if (readerResults.length < args.limit && queries.some((query) => !BROAD_THREADS_SEARCH_QUERIES.includes(query))) {
    const broadResults = await fetchThreadsReaderSearchCandidates({
      archiveId: args.archiveId,
      keywords: BROAD_THREADS_SEARCH_QUERIES,
      queries: BROAD_THREADS_SEARCH_QUERIES,
      limit: args.limit,
      refresh: args.refresh,
    }).catch(() => []);
    const byId = new Map(readerResults.map((candidate) => [candidate.id, candidate]));
    for (const candidate of broadResults) {
      if (!byId.has(candidate.id)) byId.set(candidate.id, candidate);
      if (byId.size >= args.limit) break;
    }
    readerResults = [...byId.values()];
  }
  if (args.refresh && readerResults.length < args.limit) {
    const relaxedResults = await fetchThreadsReaderSearchCandidates({
      archiveId: args.archiveId,
      keywords: [...args.keywords, ...BROAD_THREADS_SEARCH_QUERIES],
      queries: [...queries, ...BROAD_THREADS_SEARCH_QUERIES],
      limit: args.limit,
      refresh: false,
    }).catch(() => []);
    const byId = new Map(readerResults.map((candidate) => [candidate.id, candidate]));
    for (const candidate of relaxedResults) {
      if (!byId.has(candidate.id)) byId.set(candidate.id, candidate);
      if (byId.size >= args.limit) break;
    }
    readerResults = [...byId.values()];
  }
  if (readerResults.length > 0) {
    writeThreadsSearchCandidateCache(args.keywords, readerResults);
    return readerResults.sort((a, b) => b.hotScore - a.hotScore).slice(0, args.limit);
  }

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
    for (const query of queries.slice(0, 3)) {
      if (results.length >= args.limit) break;
      const search = await readThreadsSearchPageText(page, query);
      const parsed = parseThreadsSearchTextCandidates({
        text: search.text,
        query,
        keywords: args.keywords,
        limit: args.limit - results.length,
        sourceUrl: search.url,
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
  const sorted = results.sort((a, b) => b.hotScore - a.hotScore).slice(0, args.limit);
  if (sorted.length > 0) writeThreadsSearchCandidateCache(args.keywords, sorted);
  return sorted.length > 0 || args.refresh ? sorted : readThreadsSearchCandidateCache(args.archiveId, args.keywords, args.limit);
}

const JINA_READER_PREFIX = "https://r.jina.ai/http://r.jina.ai/http://";

async function fetchThreadsReaderSearchCandidates(args: {
  archiveId: string;
  keywords: string[];
  queries: string[];
  limit: number;
  refresh?: boolean;
}): Promise<SentimentHotCandidate[]> {
  const excluded = args.refresh ? getSentimentHotRefreshExcludedIds(args.archiveId) : getSentimentHotExcludedIds(args.archiveId);
  const all: SentimentHotCandidate[] = [];
  const searches = await Promise.all(
    args.queries.slice(0, 8).map(async (query) => {
      const targetUrl = `https://www.threads.net/search?q=${encodeURIComponent(query)}`;
      const response = await fetch(`${JINA_READER_PREFIX}${targetUrl}`, {
        headers: {
          "user-agent": "Mozilla/5.0",
          accept: "text/plain, text/markdown, */*",
        },
        signal: AbortSignal.timeout(18_000),
      });
      if (!response.ok) return { query, targetUrl, text: "" };
      return { query, targetUrl, text: await response.text() };
    }),
  );
  for (const search of searches) {
    const parsed = parseThreadsReaderSearchMarkdownCandidates({
      text: search.text,
      query: search.query,
      keywords: args.keywords,
      sourceUrl: search.targetUrl,
      limit: args.limit,
    });
    for (const candidate of parsed) {
      if (excluded.has(candidate.id)) continue;
      if (all.some((item) => item.id === candidate.id || item.sourceUrl === candidate.sourceUrl || item.content === candidate.content)) continue;
      all.push(candidate);
      if (all.length >= args.limit) break;
    }
    if (all.length >= args.limit) break;
  }
  return all.sort((a, b) => b.hotScore - a.hotScore).slice(0, args.limit);
}

function decodeMarkdownLinkText(value: string): string {
  return cleanText(
    value
      .replace(/!\[[^\]]*]\([^)]+\)/g, " ")
      .replace(/\[([^\]]+)]\([^)]+\)/g, "$1")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">"),
  );
}

function cleanThreadsReaderContent(value: string): string {
  const lines = String(value || "")
    .replace(/Sorry,\s*we.{0,8}re having trouble playing this video\.\s*Learn more/gi, " ")
    .replace(/\bVideo\s+\d+\b/gi, " ")
    .split(/\r?\n/g)
    .map((line) => decodeMarkdownLinkText(line))
    .filter(Boolean)
    .filter((line) => !/^(?:Translate|翻譯|翻译)$/i.test(line))
    .filter((line) => !/^Sorry,\s*we.{0,8}re having trouble playing this video\.\s*Learn more$/i.test(line))
    .filter((line) => !/^Video\s+\d+$/i.test(line))
    .filter((line) => !/^\d+(?:[.,]\d+)?\s*[Kk萬万]?$/.test(line))
    .filter((line) => !/^Image\s+\d+/i.test(line));
  return cleanSentimentCandidateContent(lines.join(" "))
    .replace(/Sorry,\s*we.{0,8}re having trouble playing this video\.\s*Learn more/gi, " ")
    .replace(/\bVideo\s+\d+\b/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseThreadsReaderHotScore(block: string): number {
  let score = 80;
  for (const match of block.matchAll(/(?:^|\n)\s*(\d+(?:[.,]\d+)?)(?:\s*([Kk萬万]))?\s*(?=\n|$)/g)) {
    const base = Number(String(match[1] || "0").replace(/,/g, ""));
    if (!Number.isFinite(base)) continue;
    const unit = match[2] || "";
    const value = /[Kk]/.test(unit) ? base * 1000 : /[萬万]/.test(unit) ? base * 10000 : base;
    score += Math.min(50_000, Math.round(value));
  }
  return score;
}

export function parseThreadsReaderSearchMarkdownCandidates(args: {
  text: string;
  query: string;
  keywords?: string[];
  limit?: number;
  sourceUrl: string;
}): SentimentHotCandidate[] {
  const text = String(args.text || "");
  if (!text || !/Search\s*•\s*Threads|Threads/i.test(text)) return [];
  const needles = meaningfulNeedles([...(args.keywords || []), args.query])
    .filter((keyword) => hasHan(keyword))
    .slice(0, 10);
  const postRegex = /\[(\d{2}\/\d{2}\/\d{2,4})]\((https:\/\/www\.threads\.net\/(?:@[^)\s]+\/post\/[^)\s]+|t\/[^)\s]+))\)\s*\n([\s\S]*?)(?=\n\[!\[Image\s+\d+:[^\]]*profile picture|\n\[[^\]\n]+]\(https:\/\/www\.threads\.net\/@|$)/g;
  const out: SentimentHotCandidate[] = [];
  let match: RegExpExecArray | null;
  while ((match = postRegex.exec(text)) !== null) {
    const before = text.slice(Math.max(0, match.index - 900), match.index);
    const authorMatches = [...before.matchAll(/\[([^\]\n]{2,80})]\((https:\/\/www\.threads\.net\/@[^)\s]+)\)/g)];
    const author = cleanText(authorMatches.at(-1)?.[1] || "Threads");
    const sourceUrl = match[2];
    const block = match[3] || "";
    const content = cleanThreadsReaderContent(block);
    if (isLowQualitySentimentContent(content)) continue;
    if (!isChineseSentimentCandidate(content)) continue;
    if ((content.match(/[\u3400-\u9fff]/gu) || []).length < 12) continue;
    const haystack = [content, author].join(" ").toLowerCase();
    const matchedNeedles = needles.filter((needle) => haystack.includes(needle.toLowerCase()));
    if (needles.length && matchedNeedles.length === 0) continue;
    const media: SentimentHotMedia[] = [];
    for (const imageMatch of block.matchAll(/!\[[^\]]*]\((https?:\/\/[^)\s]+)\)/g)) {
      const url = imageMatch[1];
      if (/profile_pic|profile|s150x150/i.test(url)) continue;
      if (media.some((item) => item.url === url)) continue;
      media.push({ type: "image", url });
      if (media.length >= 4) break;
    }
    const id = buildSentimentCandidateId({ platform: "threads", sourceUrl, content });
    out.push({
      id,
      platform: "threads",
      sourceUrl,
      author,
      content,
      media,
      hotScore: parseThreadsReaderHotScore(block) + matchedNeedles.length * 30,
      metrics: {
        source: "threads-reader-search",
        query: args.query,
        matchedKeywords: matchedNeedles,
        mediaCount: media.length,
      },
      capturedAt: new Date().toISOString(),
      warnings: [],
    });
    if (out.length >= (args.limit || 10)) break;
  }
  return out;
}

async function readThreadsSearchPageText(page: any, query: string): Promise<{ text: string; url: string }> {
  const searchUrl = `https://www.threads.net/search?q=${encodeURIComponent(query)}`;
  await page.goto(searchUrl, { waitUntil: "domcontentloaded", timeout: 20_000 });
  await page.waitForTimeout(2_500);
  const text = await page.locator("body").innerText({ timeout: 5_000 }).catch(() => "");
  return { text, url: page.url() || searchUrl };
}

const THREADS_SEARCH_CACHE_FILE = resolveRuntimeFile("sentiment_threads_search_cache.json");

function threadsSearchCacheKeys(keywords: string[]): string[] {
  return buildThreadsSearchQueries(keywords).slice(0, 8).map((keyword) => keyword.toLowerCase());
}

function readThreadsSearchCacheState(): Record<string, { at: string; candidates: SentimentHotCandidate[] }> {
  try {
    if (!fs.existsSync(THREADS_SEARCH_CACHE_FILE)) return {};
    const parsed = JSON.parse(fs.readFileSync(THREADS_SEARCH_CACHE_FILE, "utf8"));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeThreadsSearchCandidateCache(keywords: string[], candidates: SentimentHotCandidate[]) {
  const state = readThreadsSearchCacheState();
  const row = { at: new Date().toISOString(), candidates: candidates.slice(0, 20) };
  for (const key of threadsSearchCacheKeys(keywords)) state[key] = row;
  fs.mkdirSync(path.dirname(THREADS_SEARCH_CACHE_FILE), { recursive: true });
  fs.writeFileSync(THREADS_SEARCH_CACHE_FILE, JSON.stringify(state, null, 2), "utf8");
}

function readThreadsSearchCandidateCache(archiveId: string, keywords: string[], limit: number): SentimentHotCandidate[] {
  const state = readThreadsSearchCacheState();
  const excluded = getSentimentHotExcludedIds(archiveId);
  const byId = new Map<string, SentimentHotCandidate>();
  const maxAgeMs = 24 * 60 * 60 * 1000;
  for (const key of threadsSearchCacheKeys(keywords)) {
    const row = state[key];
    if (!row || Date.now() - new Date(row.at).getTime() > maxAgeMs) continue;
    for (const candidate of row.candidates || []) {
      if (!candidate?.id || excluded.has(candidate.id)) continue;
      const content = cleanThreadsReaderContent(candidate.content || "");
      if (!content || isLowQualitySentimentContent(content) || !isChineseSentimentCandidate(content)) continue;
      byId.set(candidate.id, {
        ...candidate,
        content,
        warnings: [...(candidate.warnings || []), "当前 Threads 搜索被限流，已使用 24 小时内缓存热点。"],
      });
    }
  }
  return [...byId.values()].sort((a, b) => b.hotScore - a.hotScore).slice(0, limit);
}

function readSentimentBrowserAuthCookies(platform: SentimentHotPlatform) {
  const configPath = path.join(resolveSentimentDataDir(), "sentiment-config.json");
  if (!fs.existsSync(configPath)) return [];
  try {
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    const profiles = config?.sentimentSearch?.browserFallback?.profiles || config?.browserFallback?.profiles || [];
    const profile = Array.isArray(profiles)
      ? profiles.find((item: any) => item?.platform === platform || item?.sourceKey === platform || item?.key === platform)
      : null;
    const nowSeconds = Date.now() / 1000;
    const cookies = (Array.isArray(profile?.cookies) ? profile.cookies : [])
      .filter((cookie: any) => {
        const expires = Number(cookie?.expires);
        return cookie?.name && cookie?.value && (!Number.isFinite(expires) || expires <= 0 || expires > nowSeconds);
      })
      .map((cookie: any) => {
        const sameSite = ["Strict", "Lax", "None"].includes(cookie.sameSite) ? cookie.sameSite : undefined;
        return {
          name: String(cookie.name),
          value: String(cookie.value),
          domain: String(cookie.domain || profile.domain || "threads.net"),
          path: String(cookie.path || "/"),
          expires: Number.isFinite(Number(cookie.expires)) ? Number(cookie.expires) : -1,
          httpOnly: Boolean(cookie.httpOnly || cookie.http_only),
          secure: cookie.secure !== false,
          sameSite,
        };
      });
    if (platform !== "threads") return cookies;
    const mirrored = cookies.map((cookie: any) => ({ ...cookie, domain: ".threads.com" }));
    return [...cookies, ...mirrored];
  } catch {
    return [];
  }
}

function buildThreadsSearchQueries(keywords: string[]): string[] {
  const joined = keywords.join(" ");
  const out: string[] = [];
  const add = (value: string) => {
    const text = cleanText(value);
    if (!text) return;
    if (!hasHan(text) && !/^AI$/i.test(text)) return;
    if (text.length > 14) return;
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
  if (out.length === 0) BROAD_THREADS_SEARCH_QUERIES.forEach(add);
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
