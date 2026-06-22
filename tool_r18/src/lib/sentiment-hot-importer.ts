import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import type { PersonaArchive } from "@/core/archives/persona-archive-domain";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";
import { callTextUnderstandingModelWithFallback, extractText } from "@/lib/gemini-client";
import {
  buildSentimentCandidateId,
  getSentimentHotExcludedIds,
  getSentimentHotRefreshExcludedIds,
  getSentimentHotShownIds,
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
const MIN_SENTIMENT_HOT_SCORE = 1000;

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

function segmentPersonaWords(value: string): string[] {
  const text = cleanText(value);
  if (!text || !hasHan(text)) return [];
  const out: string[] = [];
  const add = (word: string) => {
    const item = cleanText(word);
    if (!item || !hasHan(item)) return;
    if (item.length < 2 || item.length > 12) return;
    if (isGenericSentimentKeyword(item)) return;
    if (WEAK_RELEVANCE_STOPWORDS.has(item)) return;
    if (!out.some((existing) => existing.toLowerCase() === item.toLowerCase())) out.push(item);
  };
  try {
    const Segmenter = (Intl as any).Segmenter;
    if (Segmenter) {
      const segmenter = new Segmenter("zh-Hant", { granularity: "word" });
      for (const part of segmenter.segment(text)) {
        if (part?.isWordLike) add(part.segment);
      }
    }
  } catch {
    // Intl.Segmenter is optional in older Node runtimes.
  }
  return out;
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
  "分享",
  "台灣",
  "心情",
  "今天",
  "最近",
  "穿搭",
  "美食",
  "遊戲",
  "戀愛",
  "動漫",
  "追劇",
  "旅行",
  "工作",
  "感情",
  "女生",
  "話題",
  "討論",
  "推薦",
  "好笑",
  "實用",
  "推文",
  "文案",
]);

const WEAK_RELEVANCE_STOPWORDS = new Set([
  "未來",
  "未来",
  "風格",
  "风格",
  "黑色",
  "白色",
  "視覺",
  "视觉",
  "呈現",
  "呈现",
  "內容",
  "内容",
  "故事",
  "日常",
  "生活",
  "分享",
  "心得",
  "討論",
  "讨论",
  "推薦",
  "推荐",
  "台灣",
  "台湾",
  "熱門",
  "热门",
]);

const DOMAIN_RELEVANCE_KEYWORDS = new Set([
  "遊戲",
  "游戏",
  "動漫",
  "动漫",
  "戀愛",
  "恋爱",
  "感情",
  "穿搭",
  "美食",
  "工作",
  "旅行",
  "旅遊",
  "旅游",
  "女生",
]);

const PRIORITY_DOMAIN_KEYWORDS = new Set([
  "醫療",
  "医疗",
  "醫生",
  "医生",
  "醫院",
  "医院",
  "診所",
  "诊所",
  "醫美",
  "医美",
  "護理",
  "护理",
  "護士",
  "护士",
  "急診",
  "急诊",
  "AI",
  "人工智慧",
  "人工智能",
  "自動化",
  "自动化",
  "護膚",
  "护肤",
  "美妝",
  "美妆",
  "穿搭",
  "遊戲",
  "游戏",
  "動漫",
  "动漫",
  "二次元",
  "職場",
  "职场",
]);

const BROAD_THREADS_SEARCH_QUERIES = ["生活", "日常", "熱門", "分享", "台灣", "心情", "今天", "最近", "穿搭", "美食", "遊戲", "戀愛"];

function isGenericSentimentKeyword(value: string): boolean {
  const key = cleanText(value).toLowerCase();
  return GENERIC_SENTIMENT_KEYWORDS.has(key) && !DOMAIN_RELEVANCE_KEYWORDS.has(key);
}

function isWeakRelevanceKeyword(value: string): boolean {
  const keyword = cleanText(value);
  const key = keyword.toLowerCase();
  if (!keyword) return true;
  if (PRIORITY_DOMAIN_KEYWORDS.has(keyword)) return false;
  if (WEAK_RELEVANCE_STOPWORDS.has(keyword) || WEAK_RELEVANCE_STOPWORDS.has(key)) return true;
  if (isGenericSentimentKeyword(keyword)) return true;
  return /^(?:日常|生活|分享|心情|今天|最近|話題|话题|熱門|热门|推薦|推荐|女生|男生|故事|內容|内容)$/u.test(keyword);
}

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
    .filter((item) => item.length >= 2 && item.length <= 40 && !isGenericSentimentKeyword(item))
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
  return rankSearchKeywords([...new Set(out)]
    .filter((item) => item.length >= 2 && item.length <= 12 && !isGenericSentimentKeyword(item))
  ).slice(0, 10);
}

function rankSearchKeywords(keywords: string[]): string[] {
  return keywords
    .map((keyword, index) => {
      let score = 0;
      if (PRIORITY_DOMAIN_KEYWORDS.has(keyword)) score += 100;
      if (!isWeakRelevanceKeyword(keyword)) score += 30;
      if (keyword.length <= 4) score += 20;
      if (keyword.length > 8) score -= 25;
      return { keyword, index, score };
    })
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((item) => item.keyword);
}

function extractDirectHanKeywords(args: { archiveName: string; text: string }): string[] {
  const out: string[] = [];
  const add = (value: string) => {
    const text = cleanText(value);
    if (!text || text.length < 2 || text.length > 12) return;
    if (args.archiveName && text.includes(args.archiveName)) return;
    if (isGenericSentimentKeyword(text)) return;
    out.push(text);
  };
  if (/[醫医]|醫生|医生|醫院|医院|醫療|医疗|護理|护理|護士|护士|急診|急诊|診所|诊所/.test(args.text)) {
    [
      "醫療",
      "医疗",
      "醫生",
      "医生",
      "醫院",
      "医院",
      "醫療事故",
      "医疗事故",
      "醫療糾紛",
      "医疗纠纷",
      "護理",
      "护理",
      "護士",
      "护士",
      "急診",
      "急诊",
    ].forEach(add);
  }
  for (const match of args.text.matchAll(/[\u3400-\u9fff]{2,12}/gu)) add(match[0]);
  return [...new Set(out)].slice(0, 10);
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
  const extracted = [
    ...buildSearchKeywordCandidates({ archiveName: personaName, pieces }),
    ...extractDirectHanKeywords({ archiveName: personaName, text: joined }),
  ];
  return rankSearchKeywords([...new Set(extracted.filter(Boolean))]).slice(0, 10);
}

export function cleanSentimentCandidateContent(value: unknown): string {
  let text = cleanText(value);
  text = text
    .replace(/\s*Log in for more threads about this topic\.\s*Log in\s*Log in or sign up for Threads?.*$/i, "")
    .replace(/\s*Log in or sign up for Threads?.*$/i, "")
    .replace(/\s*Log in for more.*$/i, "")
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
  const kanaCount = (text.match(/[\u3040-\u30ff]/gu) || []).length;
  if (kanaCount > 0 && kanaCount >= hanCount * 0.25) return false;
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
  const poolLimit = Math.max(limit * 2, 20);
  const hasSearchKeywords = meaningfulNeedles(keywords).length > 0;

  let candidates = hasSearchKeywords
    ? await fetchThreadsSearchPageCandidates({
      archiveId,
      keywords,
      limit: poolLimit,
      refresh: args.refresh === true,
    }).catch((error) => {
      warnings.push("\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u0072\u0065\u0061\u0064\u0065\u0072\u0020\u6293\u53d6\u5931\u6557\uff1a" + (error instanceof Error ? error.message : String(error)));
      return [];
    })
    : [];
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

  if (hasSearchKeywords && candidates.length < limit) {
    const databaseCandidates = await readCandidatesFromDatabase({ archiveId, keywords, limit: poolLimit, excludeShown: args.refresh === true });
    if (databaseCandidates.length > 0) {
      const byId = new Map(candidates.map((candidate) => [candidate.id, candidate]));
      for (const candidate of databaseCandidates) {
        if (!byId.has(candidate.id)) byId.set(candidate.id, candidate);
        if (byId.size >= poolLimit) break;
      }
      candidates = sortRelevantHotCandidates([...byId.values()], keywords, poolLimit);
    }
  }
  if (!hasSearchKeywords) {
    warnings.push("\u7576\u524d\u4eba\u8a2d\u6c92\u6709\u89e3\u6790\u51fa\u53ef\u641c\u7d22\u95dc\u9375\u8a5e\uff0c\u5df2\u505c\u6b62\u6cdb\u5316\u641c\u7d22\uff1b\u8acb\u5148\u5728\u4eba\u8a2d\u7c21\u4ecb\u88dc\u5145\u660e\u78ba\u7684\u9818\u57df\u3001\u8208\u8da3\u6216\u8077\u696d\u8a2d\u5b9a\u3002");
  } else {
    candidates = await fillSentimentHotCandidatesToLimit({
      archiveId,
      keywords,
      candidates,
      limit,
      warnings,
    });
    candidates = await filterSentimentCandidatesWithModel({ archive, keywords, candidates, limit, warnings });
  }
  if (runtime.ok && usableSources.length > 0) {
    void syncSentimentKeywords(keywords).catch(() => undefined);
  } else if (runtime.ok) {
    warnings.push("\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u002f\u0020\u0049\u006e\u0073\u0074\u0061\u0067\u0072\u0061\u006d\u0020\u7f3a\u5c11\u6709\u6548\u0020\u0043\u006f\u006f\u006b\u0069\u0065\uff0c\u5df2\u8df3\u904e\u771f\u5be6\u6383\u63cf\uff1b\u8acb\u5148\u5728\u8206\u60c5\u0020\u0043\u006f\u006f\u006b\u0069\u0065\u0020\u914d\u7f6e\u4e2d\u6388\u6b0a\u5f8c\u518d\u5237\u65b0\u6293\u53d6\u3002");
  }

  if (candidates.length === 0) {
    warnings.push("\u672a\u627e\u5230\u7b26\u5408\u689d\u4ef6\u7684\u9ad8\u71b1\u5ea6\u4e2d\u6587\u71b1\u9ede\uff1b\u8acb\u5237\u65b0\u6216\u63db\u66f4\u4eba\u8a2d\u95dc\u9375\u8a5e\u3002");
  } else if (candidates.length < limit) {
    warnings.push(`\u672c\u6b21\u53ea\u627e\u5230\u0020${candidates.length}/${limit}\u0020\u7bc7\u9ad8\u71b1\u5ea6\u4e2d\u6587\u71b1\u9ede\uff0c\u5df2\u904e\u6ffe\u91cd\u8907\u3001\u975e\u4e2d\u6587\u6216\u4f4e\u71b1\u5ea6\u5167\u5bb9\u3002`);
  }
  candidates = candidates.slice(0, limit);
  rememberSentimentHotShown(archiveId, candidates);
  scheduleSentimentRuntimeShutdown();
  return { candidates, keywords, cookieStatuses, warnings };
}

async function fillSentimentHotCandidatesToLimit(args: {
  archiveId: string;
  keywords: string[];
  candidates: SentimentHotCandidate[];
  limit: number;
  warnings: string[];
}): Promise<SentimentHotCandidate[]> {
  const out: SentimentHotCandidate[] = [];
  const seen = new Set<string>();
  const add = (candidate: SentimentHotCandidate) => {
    const content = cleanSentimentCandidateContent(candidate.content || "");
    if (!candidate?.id || seen.has(candidate.id)) return;
    if (!content || isLowQualitySentimentContent(content) || !isChineseSentimentCandidate(content)) return;
    const normalized = { ...candidate, content };
    if (!isUsefulHotCandidate(normalized)) return;
    if (!candidateMatchesCurrentKeywords(normalized, args.keywords)) return;
    seen.add(candidate.id);
    out.push(normalized);
  };

  for (const candidate of args.candidates) add(candidate);
  if (out.length >= args.limit) return out.slice(0, args.limit);

  const fallbackCandidates = [
    ...readThreadsSearchCandidateCache(args.archiveId, args.keywords, args.limit * 4, false),
    ...(await readCandidatesFromDatabase({
      archiveId: args.archiveId,
      keywords: args.keywords,
      limit: args.limit * 4,
      excludeShown: false,
    }).catch(() => [])),
  ];
  for (const candidate of fallbackCandidates) {
    add(candidate);
    if (out.length >= args.limit) break;
  }

  if (out.length >= args.limit) {
    args.warnings.push("\u5373\u6642\u65b0\u7d50\u679c\u4e0d\u8db3\u0020" + args.limit + "\u0020\u7bc7\uff0c\u5df2\u7528\u540c\u4eba\u8a2d\u95dc\u9375\u8a5e\u7684\u9ad8\u71b1\u5ea6\u6b77\u53f2\u5019\u9078\u88dc\u9f4a\u3002");
    return out.slice(0, args.limit);
  }

  return out;
}

function parseModelIndexList(text: string, max: number): number[] {
  const raw = cleanText(text).replace(/^```(?:json)?/i, "").replace(/```$/i, "").trim();
  const fromJson = (() => {
    try {
      const parsed = JSON.parse(raw);
      const values = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.indexes) ? parsed.indexes : Array.isArray(parsed?.indices) ? parsed.indices : [];
      return values.map((value: unknown) => Number(value)).filter((value: number) => Number.isInteger(value));
    } catch {
      return [];
    }
  })();
  const values = fromJson.length > 0 ? fromJson : [...raw.matchAll(/\d+/g)].map((match) => Number(match[0]));
  return [...new Set(values)]
    .filter((value) => value >= 1 && value <= max)
    .map((value) => value - 1);
}

async function filterSentimentCandidatesWithModel(args: {
  archive?: PersonaArchive;
  keywords: string[];
  candidates: SentimentHotCandidate[];
  limit: number;
  warnings: string[];
}): Promise<SentimentHotCandidate[]> {
  const candidates = sortRelevantHotCandidates(args.candidates, args.keywords, Math.max(args.limit * 2, 20));
  if (candidates.length <= args.limit) return candidates;
  const personaText = [
    args.archive?.name ? `Name: ${args.archive.name}` : "",
    args.archive?.content ? `Profile: ${args.archive.content}` : "",
    args.archive?.setup ? `Setup: ${JSON.stringify(args.archive.setup)}` : "",
    `Keywords: ${args.keywords.join(" / ")}`,
  ].filter(Boolean).join("\n");
  const candidateText = candidates.slice(0, 20).map((candidate, index) => {
    const media = candidate.media?.length ? ` media=${candidate.media.length}` : "";
    return `${index + 1}. score=${candidate.hotScore}${media}
${cleanText(candidate.content).slice(0, 280)}`;
  }).join("\n\n");
  try {
    const result = await callTextUnderstandingModelWithFallback(
      "xai/grok-4.3",
      [{
        role: "user",
        parts: [{
          text: [
            "\u4f60\u662f\u4eba\u8bbe\u8206\u60c5\u7d20\u6750\u5ba1\u6838\u5668\u3002\u4f60\u9700\u8981\u5148\u7406\u89e3\u4eba\u8bbe\uff0c\u518d\u7b5b\u9009\u70ed\u70b9\uff0c\u800c\u4e0d\u662f\u673a\u68b0\u5339\u914d\u5173\u952e\u8bcd\u3002",
            "\u4efb\u52a1\uff1a\u4ece\u5019\u9009 Threads/Instagram \u70ed\u70b9\u4e2d\uff0c\u9009\u51fa\u4e0e\u5f53\u524d\u4eba\u8bbe\u771f\u6b63\u76f8\u5173\u3001\u4e0d\u51b2\u7a81\u3001\u53ef\u4ee5\u76f4\u63a5\u4f5c\u4e3a\u8be5\u4eba\u8bbe\u521b\u4f5c\u7d20\u6750\u7684\u5185\u5bb9\u3002",
            "\u5ba1\u6838\u6b65\u9aa4\uff08\u4e0d\u8981\u8f93\u51fa\u8fc7\u7a0b\uff09\uff1a",
            "1. \u4ece\u4eba\u8bbe\u4e2d\u63a8\u65ad\uff1a\u6838\u5fc3\u9886\u57df\u3001\u804c\u4e1a/\u8eab\u4efd\u3001\u5174\u8da3\u3001\u8bed\u6c14\u3001\u4e0d\u5e94\u8be5\u78b0\u7684\u51b2\u7a81\u4e3b\u9898\u3002",
            "2. \u9010\u6761\u5224\u65ad\u5019\u9009\u662f\u5426\u548c\u4eba\u8bbe\u7684\u6838\u5fc3\u8bbe\u5b9a\u4e00\u81f4\uff1b\u53ea\u6709\u5f31\u5173\u952e\u8bcd\uff08\u5982\u5206\u4eab\u3001\u65e5\u5e38\u3001\u751f\u6d3b\u3001\u5973\u751f\u3001\u70ed\u95e8\uff09\u4e0d\u80fd\u901a\u8fc7\u3002",
            "3. \u6392\u9664\u4e0e\u4eba\u8bbe\u8eab\u4efd\u3001\u4e16\u754c\u89c2\u3001\u5185\u5bb9\u65b9\u5411\u51b2\u7a81\u7684\u5019\u9009\uff1b\u5373\u4f7f\u70ed\u5ea6\u5f88\u9ad8\u4e5f\u4e0d\u8981\u9009\u3002",
            "4. \u4f18\u5148\u4fdd\u7559\u70ed\u5ea6\u9ad8\u3001\u4e2d\u6587\u5185\u5bb9\u3001\u4e3b\u9898\u660e\u786e\u3001\u80fd\u88ab\u8be5\u4eba\u8bbe\u81ea\u7136\u53d1\u5e03\u6216\u6539\u5199\u7684\u5019\u9009\u3002",
            `5. \u6700\u591a\u8fd4\u56de ${args.limit} \u4e2a\u5e8f\u53f7\uff1b\u5b81\u53ef\u5c11\u8fd4\u56de\uff0c\u4e5f\u4e0d\u8981\u51d1\u65e0\u5173\u5185\u5bb9\u3002\u5982\u679c\u6ca1\u6709\u5408\u683c\u5019\u9009\uff0c\u8fd4\u56de []\u3002`,
            "\u53ea\u8f93\u51fa JSON \u6570\u7ec4\uff0c\u4f8b\u5982\uff1a[1,3,5]\u6216[]\u3002\u4e0d\u8981\u89e3\u91ca\u3002",
            "",
            "\u4eba\u8bbe\uff1a",
            personaText,
            "",
            "\u5019\u9009\uff1a",
            candidateText,
          ].join("\n"),
        }],
      }],
      { temperature: 0.1, maxOutputTokens: 160 },
      AbortSignal.timeout(10_000),
      {
        isUsableResponse: (data) => Boolean(extractText(data).trim()),
        isRetryableError: () => false,
      },
    );
    const modelText = extractText(result.data);
    if (/^\s*```(?:json)?\s*\[\s*]\s*```?\s*$/i.test(modelText) || /^\s*\[\s*]\s*$/.test(modelText)) {
      args.warnings.push("\u6a21\u578b\u76f8\u5173\u6027\u8fc7\u6ee4\u8ba4\u4e3a\u6ca1\u6709\u5019\u9009\u4e0e\u4eba\u8bbe\u8db3\u591f\u76f8\u5173\u3002");
      return [];
    }
    const indexes = parseModelIndexList(modelText, candidates.length);
    const selected = indexes.map((index) => candidates[index]).filter(Boolean);
    if (selected.length > 0) return selected.slice(0, args.limit);
    args.warnings.push("\u6a21\u578b\u76f8\u5173\u6027\u8fc7\u6ee4\u672a\u8fd4\u56de\u53ef\u7528\u5e8f\u53f7\uff0c\u5df2\u4f7f\u7528\u5f3a\u5173\u952e\u8bcd\u8fc7\u6ee4\u5019\u9009\u3002");
  } catch (error) {
    args.warnings.push("\u6a21\u578b\u76f8\u5173\u6027\u8fc7\u6ee4\u5931\u8d25\uff0c\u5df2\u4f7f\u7528\u5f3a\u5173\u952e\u8bcd\u8fc7\u6ee4\u5019\u9009\uff1a" + (error instanceof Error ? error.message : String(error)));
  }
  return candidates.slice(0, args.limit);
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

function buildSentimentRefreshQueryPool(baseQueries: string[]): string[] {
  const dynamicQueries = buildDynamicSearchQueryVariants(baseQueries);
  return [...new Set((dynamicQueries.length ? dynamicQueries : baseQueries).map(cleanText).filter(Boolean))];
}

function rotateSentimentQueries(queries: string[], seed: number): string[] {
  if (queries.length <= 1) return queries;
  const offset = Math.abs(seed) % queries.length;
  return [...queries.slice(offset), ...queries.slice(0, offset)];
}

function buildOrderedSentimentQueries(baseQueries: string[], seed: number, refresh = false): string[] {
  const pool = buildSentimentRefreshQueryPool(baseQueries);
  if (refresh) return rotateSentimentQueries(pool, seed);
  const baseSet = new Set(baseQueries);
  const supplemental = pool.filter((query) => !baseSet.has(query));
  return [...baseQueries, ...rotateSentimentQueries(supplemental, seed)];
}

function buildDynamicSearchQueryVariants(baseQueries: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const add = (value: string) => {
    const text = cleanText(value)
      .replace(/[「」『』“”"'()[\]{}]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!text || !hasHan(text)) return;
    if (text.length < 2 || text.length > 14) return;
    const key = text.toLowerCase();
    if (isGenericSentimentKeyword(key)) return;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(text);
  };
  const addSplitParts = (value: string) => {
    const text = cleanText(value);
    for (const part of text.split(/\s+|和|與|与|及|以及|跟|、|，|,|\/|／|-|_|\+|&/g)) add(part);
    const hanRuns = text.match(/[\u3400-\u9fff]{2,}/gu) || [];
    for (const run of hanRuns) {
      add(run);
      for (const word of segmentPersonaWords(run)) add(word);
    }
  };

  for (const query of baseQueries) {
    add(query);
    addSplitParts(query);
  }

  const suffixes = ["分享", "心得", "討論", "推薦", "故事", "經驗", "日常", "台灣", "熱門", "吐槽"];
  const cores = out.filter((item) => item.length >= 2 && item.length <= 8).slice(0, 12);
  for (const core of cores) {
    for (const suffix of suffixes) add(`${core} ${suffix}`);
  }
  return out.slice(0, 80);
}

function buildRelevanceNeedles(keywords: string[]): string[] {
  const out: string[] = [];
  const add = (value: string) => {
    const keyword = cleanText(value);
    if (!keyword || !hasHan(keyword)) return;
    if (keyword.length < 2 || keyword.length > 14) return;
    const key = keyword.toLowerCase();
    if (isGenericSentimentKeyword(key)) return;
    if (WEAK_RELEVANCE_STOPWORDS.has(keyword)) return;
    if (!out.some((item) => item.toLowerCase() === key)) out.push(keyword);
  };
  for (const keyword of meaningfulNeedles(keywords).filter((item) => hasHan(item))) {
    add(keyword);
    for (const part of splitKeywords(keyword)) add(part);
    const runs = keyword.match(/[\u3400-\u9fff]{2,}/gu) || [];
    for (const run of runs) {
      add(run);
      for (const word of segmentPersonaWords(run)) add(word);
    }
  }
  return out
    .filter((keyword) => {
      const key = keyword.toLowerCase();
      if (keyword.length < 2 || keyword.length > 14) return false;
      if (isGenericSentimentKeyword(key)) return false;
      if (WEAK_RELEVANCE_STOPWORDS.has(keyword)) return false;
      return true;
    })
    .slice(0, 32);
}

function buildStrongRelevanceNeedles(keywords: string[]): string[] {
  return buildRelevanceNeedles(keywords).filter((keyword) => !isWeakRelevanceKeyword(keyword));
}

function isUsefulHotCandidate(candidate: SentimentHotCandidate): boolean {
  return Number(candidate.hotScore || 0) >= MIN_SENTIMENT_HOT_SCORE;
}

function sortUsefulHotCandidates(candidates: SentimentHotCandidate[], limit: number): SentimentHotCandidate[] {
  return candidates
    .filter(isUsefulHotCandidate)
    .sort((a, b) => b.hotScore - a.hotScore)
    .slice(0, limit);
}

function sortRelevantHotCandidates(candidates: SentimentHotCandidate[], keywords: string[], limit: number): SentimentHotCandidate[] {
  return sortUsefulHotCandidates(
    candidates.filter((candidate) => candidateMatchesCurrentKeywords(candidate, keywords)),
    limit,
  );
}

function candidateLooksOffTopic(candidate: SentimentHotCandidate): boolean {
  const text = [candidate.content, candidate.author].map(cleanText).join(" ");
  const offTopicGroups = [
    /(?:日本自由行|心齋橋|心斋桥|大阪|京都|東京|东京|旅遊|旅游|飯店|酒店|民宿|機票|景點|景点|行程|住宿|免稅|免税)/u,
    /(?:徵才|征才|招聘|招募|履歷|履历|職缺|职缺|面試|面试|薪資|薪资)/u,
    /(?:抽獎|抽奖|折扣|優惠|优惠|團購|团购|下單|下单|購買|购买|私訊購買|私讯购买)/u,
  ];
  return offTopicGroups.some((pattern) => pattern.test(text));
}

function countMatchedNeedles(candidate: SentimentHotCandidate, needles: string[]): number {
  const haystack = [
    candidate.content,
    candidate.author,
  ].map(cleanText).join(" ").toLowerCase();
  return needles.filter((needle) => haystack.includes(needle.toLowerCase())).length;
}

export function candidateMatchesCurrentKeywords(candidate: SentimentHotCandidate, keywords: string[]): boolean {
  const needles = buildRelevanceNeedles(keywords);
  if (needles.length === 0) return false;
  const strongNeedles = buildStrongRelevanceNeedles(keywords);
  const matchedCount = countMatchedNeedles(candidate, needles);
  const matchedStrongCount = countMatchedNeedles(candidate, strongNeedles);
  if (matchedCount <= 0) return false;
  if (strongNeedles.length > 0 && matchedStrongCount <= 0 && matchedCount < 2) return false;
  if (candidateLooksOffTopic(candidate) && matchedStrongCount < 1 && matchedCount < 2) return false;

  return matchedStrongCount > 0 || matchedCount >= 2;
}

async function fetchThreadsSearchPageCandidates(args: {
  archiveId: string;
  keywords: string[];
  limit: number;
  refresh?: boolean;
}): Promise<SentimentHotCandidate[]> {
  const baseQueries = buildThreadsSearchQueries(args.keywords);
  const shownIds = getSentimentHotShownIds(args.archiveId);
  const selectedOrImportedIds = getSentimentHotExcludedIds(args.archiveId);
  const primaryExcluded = args.refresh ? getSentimentHotRefreshExcludedIds(args.archiveId) : selectedOrImportedIds;
  const queries = buildOrderedSentimentQueries(baseQueries, args.refresh ? Date.now() + shownIds.size : shownIds.size, args.refresh === true);
  const results: SentimentHotCandidate[] = [];
  if (queries.length === 0) return results;

  let readerResults = await fetchThreadsReaderSearchCandidates({
    archiveId: args.archiveId,
    keywords: args.keywords,
    queries: queries.slice(0, 20),
    limit: args.limit,
    refresh: args.refresh,
    excludeIds: primaryExcluded,
  }).catch(() => []);

  if (readerResults.length < args.limit) {
    const existing = new Map(readerResults.map((candidate) => [candidate.id, candidate]));
    const remainingQueries = queries.slice(20);
    for (let offset = 0; offset < remainingQueries.length && existing.size < args.limit; offset += 20) {
      const extraResults = await fetchThreadsReaderSearchCandidates({
        archiveId: args.archiveId,
        keywords: args.keywords,
        queries: remainingQueries.slice(offset, offset + 20),
        limit: args.limit,
        refresh: args.refresh,
        excludeIds: primaryExcluded,
      }).catch(() => []);
      for (const candidate of extraResults) {
        if (!existing.has(candidate.id)) existing.set(candidate.id, candidate);
        if (existing.size >= args.limit) break;
      }
    }
    readerResults = [...existing.values()];
  }

  if (readerResults.length < args.limit) {
    const cachedResults = readThreadsSearchCandidateCache(args.archiveId, args.keywords, args.limit, args.refresh === true);
    const byId = new Map(readerResults.map((candidate) => [candidate.id, candidate]));
    for (const candidate of cachedResults) {
      if (!byId.has(candidate.id)) byId.set(candidate.id, candidate);
      if (byId.size >= args.limit) break;
    }
    readerResults = [...byId.values()];
  }

  if (readerResults.length > 0) {
    writeThreadsSearchCandidateCache(args.keywords, readerResults);
    return sortRelevantHotCandidates(readerResults, args.keywords, args.limit);
  }

  try {
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
          if (primaryExcluded.has(candidate.id)) continue;
          if (!candidateMatchesCurrentKeywords(candidate, args.keywords)) continue;
          if (results.some((item) => item.id === candidate.id || item.content === candidate.content)) continue;
          results.push(candidate);
          if (results.length >= args.limit) break;
        }
      }
      await context.close();
    } finally {
      await browser.close().catch(() => undefined);
    }
  } catch {
    // Playwright is only a fallback. Missing browser binaries must not break the Telegram flow.
  }
  const sorted = sortUsefulHotCandidates(results, args.limit);
  if (sorted.length > 0) writeThreadsSearchCandidateCache(args.keywords, sorted);
  return sorted.length > 0 ? sortRelevantHotCandidates(sorted, args.keywords, args.limit) : readThreadsSearchCandidateCache(args.archiveId, args.keywords, args.limit, args.refresh === true);
}

const JINA_READER_PREFIX = "https://r.jina.ai/http://r.jina.ai/http://";

async function fetchThreadsReaderSearchCandidates(args: {
  archiveId: string;
  keywords: string[];
  queries: string[];
  limit: number;
  refresh?: boolean;
  excludeIds?: Set<string>;
}): Promise<SentimentHotCandidate[]> {
  const excluded = args.excludeIds || (args.refresh ? getSentimentHotRefreshExcludedIds(args.archiveId) : getSentimentHotExcludedIds(args.archiveId));
  const all: SentimentHotCandidate[] = [];
  const searches = await Promise.all(
    args.queries.map(async (query, index) => {
      const targetUrl = `https://www.threads.net/search?q=${encodeURIComponent(query)}`;
      const readerTargetUrl = args.refresh ? `${targetUrl}&__r=${Date.now().toString(36)}${index}` : targetUrl;
      try {
        const response = await fetch(`${JINA_READER_PREFIX}${readerTargetUrl}`, {
          headers: {
            "user-agent": "Mozilla/5.0",
            accept: "text/plain, text/markdown, */*",
            "cache-control": args.refresh ? "no-cache" : "max-age=300",
            pragma: args.refresh ? "no-cache" : "",
          },
          signal: AbortSignal.timeout(18_000),
        });
        if (!response.ok) return { query, targetUrl, text: "" };
        return { query, targetUrl, text: await response.text() };
      } catch {
        return { query, targetUrl, text: "" };
      }
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
      if (!candidateMatchesCurrentKeywords(candidate, args.keywords)) continue;
      if (all.some((item) => item.id === candidate.id || item.sourceUrl === candidate.sourceUrl || item.content === candidate.content)) continue;
      all.push(candidate);
      if (all.length >= args.limit) break;
    }
    if (all.length >= args.limit) break;
  }
  return sortUsefulHotCandidates(await enrichThreadsCandidateDetails(all), args.limit);
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

function parseMetricNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.round(value));
  const text = cleanText(value).replace(/,/g, "");
  if (!text) return undefined;
  const match = text.match(/(\d+(?:\.\d+)?)\s*([Kk萬万])?/);
  if (!match) return undefined;
  const base = Number(match[1]);
  if (!Number.isFinite(base)) return undefined;
  const unit = match[2] || "";
  const valueNumber = /[Kk]/.test(unit) ? base * 1000 : /[萬万]/.test(unit) ? base * 10000 : base;
  return Math.max(0, Math.round(valueNumber));
}

function parseMetricNumberLoose(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.round(value));
  const text = cleanText(value).replace(/,/g, "");
  if (!text) return undefined;
  const match = text.match(/(\d+(?:\.\d+)?)\s*([KkMm\u842c\u4e07])?/);
  if (!match) return undefined;
  const base = Number(match[1]);
  if (!Number.isFinite(base)) return undefined;
  const unit = match[2] || "";
  const valueNumber = /[Kk]/.test(unit)
    ? base * 1000
    : /[Mm]/.test(unit)
      ? base * 1_000_000
      : /[\u842c\u4e07]/.test(unit)
        ? base * 10000
        : base;
  return Math.max(0, Math.round(valueNumber));
}

function extractEngagementMetricsFromText(value: string): NonNullable<SentimentHotCandidate["engagement"]> {
  const text = String(value || "");
  const engagement: NonNullable<SentimentHotCandidate["engagement"]> = {};
  const assign = (key: keyof NonNullable<SentimentHotCandidate["engagement"]>, pattern: RegExp) => {
    const match = text.match(pattern);
    const count = parseMetricNumber(match?.[1] || match?.[0]);
    if (typeof count === "number") (engagement as any)[key] = count;
  };
  assign("likeCount", /(?:like|likes|liked|讚|赞|喜歡|喜欢|愛心|爱心|點讚|点赞)\D{0,8}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)/i);
  assign("commentCount", /(?:comment|comments|reply|replies|留言|評論|评论|回覆|回复)\D{0,8}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)/i);
  assign("viewCount", /(?:view|views|watch|play|plays|瀏覽|浏览|觀看|观看|播放|閱讀|阅读|流量)\D{0,8}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)/i);
  assign("shareCount", /(?:share|shares|repost|reposts|轉發|转发|分享)\D{0,8}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)/i);
  const rawSignals = Array.from(text.matchAll(/(?:^|\n)\s*\[?(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)\]?\s*(?=\n|$)/g))
    .map((match) => parseMetricNumber(match[1]))
    .filter((item): item is number => typeof item === "number" && item > 0)
    .slice(0, 6);
  if (rawSignals.length) engagement.rawSignals = rawSignals;
  return engagement;
}

function mergeEngagementMetrics(
  base: NonNullable<SentimentHotCandidate["engagement"]>,
  extra: NonNullable<SentimentHotCandidate["engagement"]>,
): NonNullable<SentimentHotCandidate["engagement"]> {
  const merged: NonNullable<SentimentHotCandidate["engagement"]> = { ...base };
  if (typeof merged.likeCount !== "number" && typeof extra.likeCount === "number") merged.likeCount = extra.likeCount;
  if (typeof merged.commentCount !== "number" && typeof extra.commentCount === "number") merged.commentCount = extra.commentCount;
  if (typeof merged.viewCount !== "number" && typeof extra.viewCount === "number") merged.viewCount = extra.viewCount;
  if (typeof merged.shareCount !== "number" && typeof extra.shareCount === "number") merged.shareCount = extra.shareCount;
  const rawSignals = [...(base.rawSignals || []), ...(extra.rawSignals || [])]
    .filter((item): item is number => typeof item === "number" && Number.isFinite(item) && item > 0);
  if (rawSignals.length) merged.rawSignals = [...new Set(rawSignals)].slice(0, 8);
  return merged;
}

function hasNamedEngagementMetrics(engagement?: SentimentHotCandidate["engagement"]) {
  return Boolean(
    engagement
      && (
        typeof engagement.likeCount === "number"
        || typeof engagement.commentCount === "number"
        || typeof engagement.viewCount === "number"
        || typeof engagement.shareCount === "number"
      ),
  );
}

export function parseThreadsDetailEngagementMarkdown(text: string): NonNullable<SentimentHotCandidate["engagement"]> {
  const value = String(text || "");
  const engagement = extractEngagementMetricsFromText(value);
  const viewMatch = value.match(/Thread\s+(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s+views/i);
  const viewCount = parseMetricNumberLoose(viewMatch?.[1]);
  if (typeof viewCount === "number") engagement.viewCount = viewCount;
  const rawSignals = Array.from(value.matchAll(/(?:^|\n)\s*(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*(?=\n|$)/g))
    .map((match) => parseMetricNumberLoose(match[1]))
    .filter((item): item is number => typeof item === "number" && item > 0)
    .slice(0, 8);
  if (rawSignals.length) {
    engagement.rawSignals = [...new Set([...(engagement.rawSignals || []), ...rawSignals])].slice(0, 8);
    if (typeof engagement.likeCount !== "number") engagement.likeCount = rawSignals[0];
    if (typeof engagement.commentCount !== "number" && rawSignals.length >= 2) engagement.commentCount = rawSignals[1];
    if (typeof engagement.shareCount !== "number" && rawSignals.length >= 3) engagement.shareCount = rawSignals[2];
  }
  return engagement;
}

function extractThreadsMediaFromMarkdown(text: string, limit = 12): SentimentHotMedia[] {
  const media: SentimentHotMedia[] = [];
  for (const imageMatch of String(text || "").matchAll(/!\[[^\]]*]\((https?:\/\/[^)\s]+)\)/g)) {
    const url = imageMatch[1];
    if (/profile_pic|profile|s150x150/i.test(url)) continue;
    if (media.some((item) => item.url === url)) continue;
    const type = /\.(mp4|mov|webm)(?:$|[?#])/i.test(url) || /video/i.test(url) ? "video" : "image";
    media.push({ type, url });
    if (media.length >= limit) break;
  }
  return media;
}

function mergeCandidateMedia(base: SentimentHotMedia[], extra: SentimentHotMedia[]): SentimentHotMedia[] {
  const out: SentimentHotMedia[] = [];
  for (const item of [...base, ...extra]) {
    const url = String(item?.url || item?.localPath || "").trim();
    if (!url) continue;
    if (out.some((existing) => existing.url === item.url || (item.localPath && existing.localPath === item.localPath))) continue;
    out.push(item);
    if (out.length >= 12) break;
  }
  return out;
}

export function parseThreadsDetailMediaMarkdown(text: string): SentimentHotMedia[] {
  return extractThreadsMediaFromMarkdown(text, 12);
}

async function fetchThreadsDetailData(sourceUrl: string): Promise<{
  engagement: NonNullable<SentimentHotCandidate["engagement"]>;
  media: SentimentHotMedia[];
}> {
  if (!/^https:\/\/www\.threads\.net\/@[^/]+\/post\//i.test(sourceUrl)) return { engagement: {}, media: [] };
  try {
    const response = await fetch(`${JINA_READER_PREFIX}${sourceUrl}`, {
      headers: {
        "user-agent": "Mozilla/5.0",
        accept: "text/plain, text/markdown, */*",
        "cache-control": "max-age=300",
      },
      signal: AbortSignal.timeout(12_000),
    });
    if (!response.ok) return { engagement: {}, media: [] };
    const text = await response.text();
    return {
      engagement: parseThreadsDetailEngagementMarkdown(text),
      media: parseThreadsDetailMediaMarkdown(text),
    };
  } catch {
    return { engagement: {}, media: [] };
  }
}

async function enrichThreadsCandidateDetails(candidates: SentimentHotCandidate[]): Promise<SentimentHotCandidate[]> {
  const targets = candidates
    .map((candidate, index) => ({ candidate, index }))
    .filter(({ candidate }) => candidate.platform === "threads" && /^https:\/\/www\.threads\.net\/@[^/]+\/post\//i.test(candidate.sourceUrl))
    .slice(0, 10);
  if (!targets.length) return candidates;
  const enriched = [...candidates];
  await Promise.all(targets.map(async ({ candidate, index }) => {
    const detail = await fetchThreadsDetailData(candidate.sourceUrl);
    if (!hasNamedEngagementMetrics(detail.engagement) && !detail.engagement.rawSignals?.length && !detail.media.length) return;
    const engagement = mergeEngagementMetrics(candidate.engagement || {}, detail.engagement);
    const media = mergeCandidateMedia(candidate.media || [], detail.media);
    enriched[index] = {
      ...candidate,
      hotScore: Math.max(candidate.hotScore, engagement.viewCount || 0, engagement.likeCount || 0),
      media,
      engagement,
      metrics: {
        ...(candidate.metrics || {}),
        mediaCount: media.length,
        ...compactEngagementMetrics(engagement),
      },
    };
  }));
  return enriched;
}

function compactEngagementMetrics(engagement: NonNullable<SentimentHotCandidate["engagement"]>): Record<string, number | number[]> {
  const out: Record<string, number | number[]> = {};
  if (typeof engagement.likeCount === "number") out.like_count = engagement.likeCount;
  if (typeof engagement.commentCount === "number") out.comment_count = engagement.commentCount;
  if (typeof engagement.viewCount === "number") out.view_count = engagement.viewCount;
  if (typeof engagement.shareCount === "number") out.share_count = engagement.shareCount;
  if (engagement.rawSignals?.length) out.raw_engagement_signals = engagement.rawSignals;
  return out;
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
  const needleSource = args.keywords?.length ? args.keywords : [args.query];
  const needles = buildRelevanceNeedles(needleSource);
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
    const engagement = extractEngagementMetricsFromText(block);
    const media = extractThreadsMediaFromMarkdown(block, 12);
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
        ...compactEngagementMetrics(engagement),
      },
      engagement,
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
const THREADS_SEARCH_CACHE_VERSION = 3;

function threadsSearchCacheKeys(keywords: string[]): string[] {
  return buildThreadsSearchQueries(keywords).slice(0, 8).map((keyword) => keyword.toLowerCase());
}

function readThreadsSearchCacheState(): Record<string, { at: string; version?: number; candidates: SentimentHotCandidate[] }> {
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
  const row = { at: new Date().toISOString(), version: THREADS_SEARCH_CACHE_VERSION, candidates: candidates.slice(0, 20) };
  for (const key of threadsSearchCacheKeys(keywords)) state[key] = row;
  fs.mkdirSync(path.dirname(THREADS_SEARCH_CACHE_FILE), { recursive: true });
  fs.writeFileSync(THREADS_SEARCH_CACHE_FILE, JSON.stringify(state, null, 2), "utf8");
}

function readThreadsSearchCandidateCache(archiveId: string, keywords: string[], limit: number, excludeShown = false): SentimentHotCandidate[] {
  const state = readThreadsSearchCacheState();
  const excluded = excludeShown ? getSentimentHotRefreshExcludedIds(archiveId) : getSentimentHotExcludedIds(archiveId);
  const byId = new Map<string, SentimentHotCandidate>();
  const maxAgeMs = 24 * 60 * 60 * 1000;
  for (const key of threadsSearchCacheKeys(keywords)) {
    const row = state[key];
    if (row?.version !== THREADS_SEARCH_CACHE_VERSION) continue;
    if (!row || Date.now() - new Date(row.at).getTime() > maxAgeMs) continue;
    for (const candidate of row.candidates || []) {
      if (!candidate?.id || excluded.has(candidate.id)) continue;
      const content = cleanThreadsReaderContent(candidate.content || "");
      if (!content || isLowQualitySentimentContent(content) || !isChineseSentimentCandidate(content)) continue;
      if (!isUsefulHotCandidate(candidate)) continue;
      if (!candidateMatchesCurrentKeywords({ ...candidate, content }, keywords)) continue;
      byId.set(candidate.id, {
        ...candidate,
        content,
        warnings: [...(candidate.warnings || []), "当前 Threads 搜索被限流，已使用 24 小时内缓存热点。"],
      });
    }
  }
  if (byId.size < limit) {
    const recentKeys = Object.entries(state)
      .filter(([, row]) => row && row.version === THREADS_SEARCH_CACHE_VERSION && Date.now() - new Date(row.at).getTime() <= maxAgeMs)
      .sort((a, b) => new Date(b[1].at).getTime() - new Date(a[1].at).getTime())
      .map(([key]) => key);
    for (const key of recentKeys) {
      if (byId.size >= limit) break;
      const row = state[key];
      for (const candidate of row.candidates || []) {
        if (byId.size >= limit) break;
        if (!candidate?.id || excluded.has(candidate.id) || byId.has(candidate.id)) continue;
        const content = cleanThreadsReaderContent(candidate.content || "");
        if (!content || isLowQualitySentimentContent(content) || !isChineseSentimentCandidate(content)) continue;
        if (!isUsefulHotCandidate(candidate)) continue;
        if (!candidateMatchesCurrentKeywords({ ...candidate, content }, keywords)) continue;
        byId.set(candidate.id, {
          ...candidate,
          content,
          warnings: [
            ...(candidate.warnings || []),
            "\u7576\u524d\u0020Threads\u0020\u641c\u7d22\u6e90\u6ce2\u52d5\uff0c\u5df2\u4f7f\u7528\u002024\u0020\u5c0f\u6642\u5167\u9ad8\u71b1\u5ea6\u7de9\u5b58\u3002",
          ],
        });
      }
    }
  }
  return sortUsefulHotCandidates([...byId.values()], limit);
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
    [/(動漫|动漫|二次元|遊戲|游戏|電玩|电玩|手遊|手游|實況|实况|漫展|同人|手辦|手办|cos|cosplay|宅宅|宅男|宅女|VTuber|Vtuber|vtuber)/i, ["動漫", "二次元", "遊戲", "電玩", "手遊", "遊戲實況", "漫展", "同人", "cosplay", "VTuber"]],
  ];
  for (const [pattern, values] of synonymGroups) {
    if (pattern.test(joined)) values.forEach(add);
  }
  if (/[\u52d5\u52a8]\u6f2b|\u4e8c\u6b21\u5143|[\u904a\u6e38]\u6232|[\u96fb\u7535]\u73a9|[\u624b]\u904a|[\u624b]\u6e38|[\u5be6\u5b9e]\u6cc1|\u6f2b\u5c55|\u540c\u4eba|cos|cosplay|ACG|VTuber|Vtuber|vtuber/i.test(joined)) {
    [
      "\u52d5\u6f2b",
      "\u52a8\u6f2b",
      "\u4e8c\u6b21\u5143",
      "\u904a\u6232",
      "\u6e38\u620f",
      "\u96fb\u73a9",
      "\u7535\u73a9",
      "\u624b\u904a",
      "\u624b\u6e38",
      "\u904a\u6232\u5be6\u6cc1",
      "\u6e38\u620f\u5b9e\u51b5",
      "\u5be6\u6cc1\u4e3b",
      "\u5b9e\u51b5\u4e3b",
      "\u6f2b\u5c55",
      "\u52d5\u6f2b\u5c55",
      "\u52a8\u6f2b\u5c55",
      "\u540c\u4eba\u5c55",
      "\u540c\u4eba",
      "cosplay",
      "coser",
      "ACG",
      "VTuber",
      "\u5b85\u5b85",
    ].forEach(add);
  }
  for (const keyword of meaningfulNeedles(keywords)) {
    add(keyword);
    for (const part of splitKeywords(keyword)) add(part);
    for (const variant of buildDynamicSearchQueryVariants([keyword])) add(variant);
  }
  return [...new Set(out)].slice(0, 48);
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

  const needleSource = args.keywords?.length ? args.keywords : [query];
  const needles = buildRelevanceNeedles(needleSource);
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
    const engagement = extractEngagementMetricsFromText(chunk.lines.join("\n"));
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
        ...compactEngagementMetrics(engagement),
      },
      engagement,
      capturedAt: new Date().toISOString(),
      warnings: ["Threads 搜索页面未暴露稳定媒体地址，已先保留文字热点。"],
    });
    if (out.length >= (args.limit || 10)) break;
  }
  return out;
}

async function readCandidatesFromDatabase(args: { archiveId: string; keywords: string[]; limit: number; excludeShown?: boolean }): Promise<SentimentHotCandidate[]> {
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
    const excluded = args.excludeShown ? getSentimentHotRefreshExcludedIds(args.archiveId) : getSentimentHotExcludedIds(args.archiveId);
    const needles = buildRelevanceNeedles(args.keywords);
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
      const matchedNeedles = needles.filter((needle) => haystack.includes(needle.toLowerCase()));
      if (needles.length && matchedNeedles.length === 0) continue;
      const relevance = Math.min(60, matchedNeedles.length * 20);
      const media = readMediaForSentiment(db, Number(row.id));
      const engagement = {
        likeCount: parseMetricNumber((safeJson(row.keywords) as any)?.like_count || (safeJson(row.extracted_keywords) as any)?.like_count),
        commentCount: parseMetricNumber((safeJson(row.keywords) as any)?.comment_count || (safeJson(row.extracted_keywords) as any)?.comment_count),
        viewCount: parseMetricNumber((safeJson(row.keywords) as any)?.view_count || row.seen_count),
      };
      const hotScore = Math.round(
        Number(row.spread_score || 0)
        + Number(row.influence_score || 0)
        + Number(row.kol_score || 0)
        + Number(row.seen_count || 0)
        + relevance,
      );
      if (hotScore < MIN_SENTIMENT_HOT_SCORE) continue;
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
          ...compactEngagementMetrics(engagement),
        },
        engagement,
        capturedAt: cleanText(row.last_seen_at || row.found_at || row.first_seen_at) || new Date().toISOString(),
        warnings: media.filter((item) => item.warning).map((item) => item.warning as string),
      });
    }
    return candidates
      .filter(isUsefulHotCandidate)
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
      LIMIT 12
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

export async function downloadCandidateMedia(candidate: SentimentHotCandidate, limit = 12): Promise<SentimentHotMedia[]> {
  const media = (candidate.media || []).slice(0, limit);
  const downloaded: SentimentHotMedia[] = [];
  for (let index = 0; index < media.length; index += 1) {
    const item = media[index];
    if (item.localPath && fs.existsSync(item.localPath)) {
      downloaded.push(item);
      continue;
    }
    if (!/^https?:\/\//i.test(item.url)) {
      downloaded.push(item);
      continue;
    }
    try {
      const response = await fetch(item.url, { signal: AbortSignal.timeout(15_000) });
      if (!response.ok) {
        downloaded.push(item);
        continue;
      }
      const contentType = response.headers.get("content-type") || "";
      if (!/^image\/|^video\//i.test(contentType)) {
        downloaded.push(item);
        continue;
      }
      const ext = extensionFromContentType(contentType, item.type);
      const mediaDir = path.dirname(resolveRuntimeFile(`sentiment-hot-media/${candidate.id}-${index + 1}${ext}`));
      fs.mkdirSync(mediaDir, { recursive: true });
      const localPath = path.join(mediaDir, `${candidate.id}-${index + 1}${ext}`);
      const buffer = Buffer.from(await response.arrayBuffer());
      fs.writeFileSync(localPath, buffer);
      downloaded.push({ ...item, localPath, warning: undefined });
    } catch {
      downloaded.push(item);
    }
  }
  return downloaded;
}

function extensionFromContentType(contentType: string, type: string): string {
  if (contentType.includes("png")) return ".png";
  if (contentType.includes("webp")) return ".webp";
  if (contentType.includes("gif")) return ".gif";
  if (contentType.includes("mp4")) return ".mp4";
  return type === "video" ? ".mp4" : ".jpg";
}
