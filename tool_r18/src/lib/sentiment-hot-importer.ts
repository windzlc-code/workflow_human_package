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

function resolvePreferredChromeExecutablePath(): string | undefined {
  const candidates = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean) as string[];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function buildLocalChromiumLaunchOptions() {
  const executablePath = resolvePreferredChromeExecutablePath();
  return {
    headless: true,
    ...(executablePath ? { executablePath } : {}),
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  };
}

export type SentimentCookieHealth = "healthy" | "watch" | "degraded" | "expired" | "missing" | "unknown";

export interface SentimentCookieStatus {
  platform: SentimentHotPlatform;
  profileKey?: string;
  health: SentimentCookieHealth;
  label: string;
  message: string;
  validCookieCount?: number;
  expiredCookieCount?: number;
  sessionCookieCount?: number;
  expiringSoonCookieCount?: number;
  hasRequiredSessionCookie?: boolean;
  authorizationNeedsRefresh?: boolean;
  recommendedAction?: string;
  lastAuthorizedAt?: string | null;
}

export interface FetchSentimentHotCandidatesResult {
  candidates: SentimentHotCandidate[];
  keywords: string[];
  cookieStatuses: SentimentCookieStatus[];
  warnings: string[];
}

export interface ThreadsBrowserProfilePublishedPostSnapshot {
  sourceUrl: string;
  hotScore: number;
  metrics: Record<string, unknown>;
  engagement: NonNullable<SentimentHotCandidate["engagement"]>;
  capturedAt: string;
}

export type ThreadsProfileHotMetrics = {
  platform: "threads";
  username: string;
  followers?: number;
  following?: number;
  recentViews?: number;
  posts?: number;
  likes?: number;
  comments?: number;
  reposts?: number;
  shares?: number;
  views?: number;
  scannedPosts?: number;
  refreshedAt: string;
  method: "browser" | "reader" | "failed";
  complete?: boolean;
  scope?: "authenticated_full_profile" | "public_partial" | "reader_public_partial" | "profile_visible_light" | "failed";
  lightRefreshedAt?: string;
  rawText?: string;
  error?: string;
};

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

function readSentimentBrowserFallbackConfig() {
  const configPath = path.join(resolveSentimentDataDir(), "sentiment-config.json");
  if (!fs.existsSync(configPath)) return {};
  try {
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    const fallback = config?.sentimentSearch?.browserFallback || config?.browserFallback || {};
    return fallback && typeof fallback === "object" ? fallback : {};
  } catch {
    return {};
  }
}

function readSentimentBrowserAuthProfilesConfig(): any[] {
  const fallback = readSentimentBrowserFallbackConfig();
  return Array.isArray((fallback as any).profiles) ? (fallback as any).profiles : [];
}

function readSentimentBrowserAuthToken(): string {
  const fallback = readSentimentBrowserFallbackConfig();
  return cleanText((fallback as any).authHelperToken || "");
}

function sentimentProfileMatchesPlatform(profile: any, platform: SentimentHotPlatform) {
  return profile?.platform === platform || profile?.sourceKey === platform || profile?.key === platform;
}

function hasValidCookieNamed(cookies: any[], name: string) {
  const target = String(name || "").toLowerCase();
  const nowSeconds = Date.now() / 1000;
  return (cookies || []).some((cookie: any) => {
    const expires = Number(cookie?.expires);
    return String(cookie?.name || "").toLowerCase() === target
      && String(cookie?.value || "").trim().length > 0
      && (!Number.isFinite(expires) || expires <= 0 || expires > nowSeconds);
  });
}

function cookieDomainMatchesAny(cookie: any, domains: string[]) {
  const raw = String(cookie?.domain || "").trim().toLowerCase().replace(/^\.+/, "");
  if (!raw) return false;
  return domains.some((domain) => raw === domain || raw.endsWith(`.${domain}`));
}

function hasValidThreadsSessionCookie(cookies: any[]) {
  const targetDomains = ["threads.net", "threads.com"];
  const nowSeconds = Date.now() / 1000;
  return (cookies || []).some((cookie: any) => {
    const expires = Number(cookie?.expires);
    return String(cookie?.name || "").toLowerCase() === "sessionid"
      && String(cookie?.value || "").trim().length > 0
      && cookieDomainMatchesAny(cookie, targetDomains)
      && (!Number.isFinite(expires) || expires <= 0 || expires > nowSeconds);
  });
}

function buildSentimentCookieStatusFromProfile(platform: SentimentHotPlatform, profile: any): SentimentCookieStatus {
  const cookies = Array.isArray(profile?.cookies) ? profile.cookies.filter((item: any) => item && typeof item === "object") : [];
  const nowSeconds = Date.now() / 1000;
  let valid = 0;
  let expired = 0;
  let session = 0;
  let expiringSoon = 0;
  let hasLoginSession = false;
  for (const cookie of cookies) {
    if (!cookie?.name || !cookie?.value) continue;
    const expires = Number(cookie.expires);
    if (String(cookie.name || "").toLowerCase() === "sessionid") hasLoginSession = true;
    if (!Number.isFinite(expires) || expires <= 0) {
      valid += 1;
      session += 1;
    } else if (expires <= nowSeconds) {
      expired += 1;
    } else {
      valid += 1;
      if (expires <= nowSeconds + 7 * 24 * 60 * 60) expiringSoon += 1;
    }
  }
  const missingThreadsLoginSession = platform === "threads" && valid > 0 && !hasLoginSession;
  const health: SentimentCookieHealth = cookies.length === 0
    ? "missing"
    : valid <= 0
      ? "expired"
      : missingThreadsLoginSession || expired > 0
        ? "degraded"
        : expiringSoon > 0
          ? "watch"
          : "healthy";
  const recommendedAction = health === "missing"
    ? "authorize-profile"
    : health === "expired"
      ? "reauthorize-profile"
      : health === "degraded"
        ? "refresh-profile-cookies"
        : health === "watch"
          ? "refresh-before-expiry"
          : "keep";
  return {
    platform,
    profileKey: cleanText(profile?.key || profile?.sourceKey || platform) || platform,
    health,
    label: platform === "threads" ? "Threads" : "Instagram",
    validCookieCount: valid,
    expiredCookieCount: expired,
    sessionCookieCount: session,
    expiringSoonCookieCount: expiringSoon,
    hasRequiredSessionCookie: platform !== "threads" || hasValidThreadsSessionCookie(cookies),
    authorizationNeedsRefresh: recommendedAction !== "keep",
    recommendedAction,
    lastAuthorizedAt: profile?.lastAuthorizedAt || null,
    message: profile
      ? `有效 Cookie ${valid} 個，過期 ${expired} 個。`
      : "缺少授權 Cookie，請到快捷配置頁面刷新。",
  };
}

export function getSentimentBrowserAuthProfileBinding(platform: SentimentHotPlatform): SentimentCookieStatus {
  const profile = readSentimentBrowserAuthProfilesConfig().find((item: any) => sentimentProfileMatchesPlatform(item, platform));
  return buildSentimentCookieStatusFromProfile(platform, profile);
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
  "內容主題",
  "内容主题",
  "風格",
  "风格",
  "視覺傾向",
  "视觉倾向",
  "圖片視覺傾向",
  "图片视觉倾向",
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
  "理性務實",
  "理性务实",
  "務實",
  "务实",
]);

const SENTIMENT_KEYWORD_NEGATION_RE = /(?:不做|不要|不是|不碰|避免|排除|禁止|拒絕|拒绝|非|無關|无关).{0,8}$/u;

function meaningfulNeedles(keywords: string[]): string[] {
  return keywords
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length >= 2 && item.length <= 40 && !isGenericSentimentKeyword(item))
    .slice(0, 12);
}

function normalizeDynamicKeyword(value: string, archiveName: string): string {
  return cleanText(value)
    .replace(/^[-_*#\d.、\s]+/g, "")
    .replace(/^(人設|人设|類型|类型|性格|內容|内容|內容領域|内容领域|風格|风格|主題|主题|模式|記憶|记忆)[:：]?/, "")
    .replace(/^(改成|改為|改为|換成|换成|修改成|修改為|修改为|內容以|内容以|以|面向|面對|是一位|是一个|是一個|聚焦|專注|专注|圍繞|围绕)/, "")
    .replace(archiveName ? new RegExp(archiveName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g") : /$^/, "")
    .replace(/(人設|人设|設定|设定|風格|风格|推文|文案|傾向|倾向|為主|为主)$/g, "")
    .trim();
}

function hasNegatedKeywordContext(source: string, keyword: string): boolean {
  const key = cleanText(keyword);
  if (!key) return false;
  let index = cleanText(source).indexOf(key);
  while (index >= 0) {
    const prefix = source.slice(Math.max(0, index - 16), index);
    if (SENTIMENT_KEYWORD_NEGATION_RE.test(prefix)) return true;
    index = source.indexOf(key, index + key.length);
  }
  return false;
}

function normalizeSentimentSearchKeyword(value: unknown, options?: { archiveName?: string; sourceText?: string }): string {
  const text = normalizeDynamicKeyword(String(value || ""), options?.archiveName || "")
    .replace(/^[@#]+/g, "")
    .trim();
  const key = text.toLowerCase();
  if (!text) return "";
  if (!hasHan(text) && !/^AI$/i.test(text)) return "";
  if (text.length < 2 || text.length > 12) return "";
  if (DYNAMIC_KEYWORD_STOPWORDS.has(text) || DYNAMIC_KEYWORD_STOPWORDS.has(key)) return "";
  if (WEAK_RELEVANCE_STOPWORDS.has(text) || WEAK_RELEVANCE_STOPWORDS.has(key)) return "";
  if (isGenericSentimentKeyword(text)) return "";
  if (options?.archiveName && text.includes(options.archiveName)) return "";
  if (options?.sourceText && hasNegatedKeywordContext(options.sourceText, text)) return "";
  return text;
}

function extractDynamicPersonaKeywords(args: { archiveName: string; pieces: string[] }): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const add = (value: string) => {
    const text = normalizeSentimentSearchKeyword(value, { archiveName: args.archiveName, sourceText: args.pieces.join(" ") });
    const key = text.toLowerCase();
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
      for (const token of text.split(/\s+|和|與|与|及|以及|跟/g)) add(token);
      if (!/\s/.test(text) && text.length <= 8) add(text);
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
  for (const item of splitKeywords(joined)) {
    const keyword = normalizeSentimentSearchKeyword(item, { archiveName: args.archiveName, sourceText: joined });
    if (keyword) out.push(keyword);
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
    const text = normalizeSentimentSearchKeyword(value, { archiveName: args.archiveName, sourceText: args.text });
    if (!text) return;
    out.push(text);
  };
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
    (setup as any).personaType,
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

function parseModelKeywordList(text: string): string[] {
  const raw = cleanText(text).replace(/^```(?:json)?/i, "").replace(/```$/i, "").trim();
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.map(String);
    if (Array.isArray(parsed?.keywords)) return parsed.keywords.map(String);
  } catch {
    // Fall back to splitting free-form model text below.
  }
  return raw.split(/[,，、\n\r/／|]+/g).map((item) => item.trim());
}

async function buildSentimentHotKeywordsWithModel(args: {
  archive?: Partial<Pick<PersonaArchive, "name" | "content" | "setup">>;
  prompt?: string;
  memorySummaries?: string[];
  warnings: string[];
}): Promise<string[]> {
  const fallbackKeywords = buildSentimentHotKeywords(args);
  const archive = args.archive || {};
  const setup = archive.setup || {};
  const personaText = [
    archive.name ? `人設名稱：${archive.name}` : "",
    archive.content ? `人設簡介：${archive.content}` : "",
    Array.isArray((setup as any).interests) && (setup as any).interests.length ? `興趣標籤參考（只能作為參考，不能直接照抄）：${(setup as any).interests.join("、")}` : "",
    Array.isArray((setup as any).genres) && (setup as any).genres.length ? `類型：${(setup as any).genres.join("、")}` : "",
    (setup as any).personality ? `性格邊界（只用於理解語氣與排除衝突，絕不可直接輸出為關鍵詞）：${(setup as any).personality}` : "",
    (setup as any).personaType ? `身份：${(setup as any).personaType}` : "",
    setup.tweetStyleProfile ? `推文風格：${setup.tweetStyleProfile}` : "",
    setup.tweetStyleSample ? `推文樣例：${setup.tweetStyleSample}` : "",
    args.memorySummaries?.length ? `近期記憶：${args.memorySummaries.join("；")}` : "",
    args.prompt ? `本次補充要求：${args.prompt}` : "",
    fallbackKeywords.length ? `規則候選：${fallbackKeywords.join("、")}` : "",
  ].filter(Boolean).join("\n");
  if (!personaText.trim()) return fallbackKeywords;

  try {
    const result = await callTextUnderstandingModelWithFallback(
      "xai/grok-4.3",
      [{
        role: "user",
        parts: [{
          text: [
            "你是社群热点搜索关键词规划器。请根据人设核心内容生成 Threads / Instagram 中文热点搜索关键词。",
            "要求：",
            "1. 只输出 JSON 数组，数组内是字符串，例如：[\"海外金融\",\"工薪信贷\",\"信用卡\"]。",
            "2. 生成前先做判断：人设名称、人设简介、身份/类型、推文风格、近期记忆、兴趣标签参考用于确定领域；性格只用于判断语气边界和排除冲突，绝不能作为关键词输出。",
            "3. 关键词必须是用户会真实输入搜索框的简单名词短语，只允许从以下通用模板中生成：领域词、行业词、职业/身份受众词、具体场景词、具体痛点词、具体产品/制度/事件词。",
            "4. 输出的是搜索用关键词，不是人设标签、不是人物介绍、不是文案片段。优先选择能在 Threads / Instagram 搜到真实热点的领域词、场景词、受众痛点词。",
            "5. 严禁输出抽象人格词、性格词、语气词、风格词、自我描述、履历碎片、半截句子、代词句或泛词，例如：说话直白、犀利、不绕弯子、他自认为、年轻的前、内容主题、视觉倾向、生活、日常、热门、分享、科技。",
            "6. 每个关键词都必须同时满足：贴合人设核心领域；能支撑该人设持续创作；不偏离人设身份和受众；单独拿出来也能直接搜索。",
            "7. 如果某个词出现在否定、排除、边界描述里，例如“不做美食”，不要把它当关键词。",
            "8. 不要为了凑数扩展到无关领域；宁可少于 10 个，也不要宽泛。",
            "9. 输出前自检并删除不合格项：不是名词短语的删除；带“的/了/他/她/我/你/自认/说话/风格/性格”的删除；无法单独搜索的删除。",
            "10. 单个关键词 2-12 个中文字，最多 10 个，按优先级排序。",
            "",
            "人设资料：",
            personaText,
          ].join("\n"),
        }],
      }],
      { temperature: 0.1, maxOutputTokens: 256 },
      AbortSignal.timeout(8_000),
      {
        isUsableResponse: (data) => Boolean(extractText(data).trim()),
        isRetryableError: () => false,
      },
    );
    const archiveName = cleanText(archive.name);
    const sourceText = personaText;
    const keywords = parseModelKeywordList(extractText(result.data))
      .map((item) => normalizeSentimentSearchKeyword(item, { archiveName, sourceText }))
      .filter(Boolean);
    const unique = rankSearchKeywords([...new Set(keywords)]).slice(0, 10);
    if (unique.length > 0) return unique;
    args.warnings.push("模型未返回可用热点搜索关键词，已使用人设字段清洗后的规则关键词。");
  } catch (error) {
    args.warnings.push("模型生成热点搜索关键词失败，已使用人设字段清洗后的规则关键词：" + (error instanceof Error ? error.message : String(error)));
  }
  return fallbackKeywords;
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

function normalizeSentimentCandidateSourceUrl(value: unknown): string {
  return cleanText(value)
    .replace(/[)\].,，。]+$/g, "")
    .replace(/#.*$/g, "")
    .replace(/[?&]__r=[^&]+/g, "")
    .replace(/[?&]utm_[^=&]+=[^&]+/g, "")
    .replace(/[?&]$/, "")
    .toLowerCase();
}

function sentimentCandidateDedupeKey(candidate: SentimentHotCandidate, contentOverride?: string): string {
  const rawSourceUrl = cleanText(candidate.sourceUrl);
  const sourceUrl = normalizeSentimentCandidateSourceUrl(rawSourceUrl);
  if (sourceUrl && !/#candidate-\d+$/i.test(rawSourceUrl)) return `${candidate.platform}:url:${sourceUrl}`;
  const content = cleanSentimentCandidateContent(contentOverride ?? candidate.content)
    .replace(/[^\p{Letter}\p{Number}]+/gu, "")
    .toLowerCase()
    .slice(0, 120);
  return `${candidate.platform}:content:${content || candidate.id}`;
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
  const profiles = readSentimentBrowserAuthProfilesConfig();
  scheduleSentimentRuntimeShutdown();
  return (["threads", "instagram"] as SentimentHotPlatform[]).map((platform) => buildSentimentCookieStatusFromProfile(platform, profiles.find((item) => sentimentProfileMatchesPlatform(item, platform))));
}

function sentimentCookieStatusHasUsableCookies(status: SentimentCookieStatus): boolean {
  if (status.platform === "threads" && status.hasRequiredSessionCookie === false) return false;
  if (status.health === "healthy" || status.health === "watch" || status.health === "degraded") return true;
  const match = status.message.match(/有效 Cookie\s*(\d+)/);
  return Boolean(match && Number(match[1]) > 0);
}

function sentimentCookiePlatformLabel(platform: SentimentHotPlatform): string {
  return platform === "threads" ? "Threads" : "Instagram";
}

function sentimentCookieStatusNeedsRefresh(status: SentimentCookieStatus): boolean {
  if (!sentimentCookieStatusHasUsableCookies(status)) return false;
  if (status.authorizationNeedsRefresh === true) return true;
  if (Number(status.expiredCookieCount || 0) > 0) return true;
  if (Number(status.expiringSoonCookieCount || 0) > 0) return true;
  return status.recommendedAction === "refresh-profile-cookies";
}

function normalizeCookieForBrowserAuth(cookie: any, fallbackDomain: string) {
  if (!cookie?.name || !cookie?.value) return null;
  const expires = Number(cookie.expires);
  const sameSite = ["Strict", "Lax", "None"].includes(cookie.sameSite) ? cookie.sameSite : undefined;
  return {
    name: String(cookie.name),
    value: String(cookie.value),
    domain: String(cookie.domain || fallbackDomain || ".threads.net"),
    path: String(cookie.path || "/"),
    expires: Number.isFinite(expires) ? expires : -1,
    httpOnly: Boolean(cookie.httpOnly || cookie.http_only),
    secure: cookie.secure !== false,
    sameSite,
  };
}

export async function refreshSentimentBrowserCookiesForPlatform(platform: SentimentHotPlatform): Promise<{ ok: boolean; message: string }> {
  const configPath = path.join(resolveSentimentDataDir(), "sentiment-config.json");
  if (!fs.existsSync(configPath)) return { ok: false, message: `${sentimentCookiePlatformLabel(platform)} Cookie 配置不存在。` };
  const profile = readSentimentBrowserAuthProfilesConfig().find((item: any) => sentimentProfileMatchesPlatform(item, platform));
  if (!profile) return { ok: false, message: `${sentimentCookiePlatformLabel(platform)} Cookie 配置不存在。` };

  const cookies = readSentimentBrowserAuthCookies(platform)
    .map((cookie: any) => normalizeCookieForBrowserAuth(cookie, profile.domain || `${platform}.net`))
    .filter(Boolean);
  if (!cookies.length) return { ok: false, message: `${sentimentCookiePlatformLabel(platform)} 缺少有效 Cookie，无法自动刷新；需要先人工重新授权登录。` };

  const authUrl = cleanText(Array.isArray(profile.authUrls) ? profile.authUrls[0] : profile.authUrl)
    || (platform === "threads" ? "https://www.threads.net/" : "https://www.instagram.com/");
  const cookieUrls = [
    authUrl,
    platform === "threads" ? "https://www.threads.net/" : "https://www.instagram.com/",
    platform === "threads" ? "https://www.threads.com/" : "",
  ].filter(Boolean);

  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  try {
    const context = await browser.newContext({
      locale: "zh-TW",
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    });
    await context.addCookies(cookies as any[]);
    const page = await context.newPage();
    await page.goto(authUrl, { waitUntil: "domcontentloaded", timeout: 25_000 }).catch(() => undefined);
    await page.waitForTimeout(2500);
    const title = await page.title().catch(() => "");
    const href = page.url();
    const stillLoggedOut = /login|登入|登录|log in|accounts\/login/i.test(`${title} ${href}`);
    const refreshedCookies = activeUniqueCookies((await context.cookies(cookieUrls)).map((cookie) => normalizeCookieForBrowserAuth(cookie, profile.domain || `${platform}.net`)).filter(Boolean));
    await context.close().catch(() => undefined);
    if (stillLoggedOut || refreshedCookies.length === 0) {
      return { ok: false, message: `${sentimentCookiePlatformLabel(platform)} 自动刷新未保持登录态，需要人工重新登录授权。` };
    }
    if (platform === "threads" && !hasValidThreadsSessionCookie(refreshedCookies)) {
      return { ok: false, message: "Threads Cookie 已讀取，但缺少 threads.net/threads.com 的有效 sessionid；請在後台瀏覽器打開 Threads 並完成登入後重新同步。" };
    }
    const response = await fetch(`${resolveSentimentBackendUrl()}/api/sentiment/browser-auth/cookies`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-sentiment-browser-auth": readSentimentBrowserAuthToken(),
      },
      body: JSON.stringify({
        profileKey: profile.key || platform,
        sourceKey: profile.sourceKey || platform,
        domain: profile.domain || new URL(authUrl).hostname,
        cookies: refreshedCookies,
      }),
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return { ok: false, message: `${sentimentCookiePlatformLabel(platform)} Cookie 回写失败：HTTP ${response.status}` };
    return { ok: true, message: `${sentimentCookiePlatformLabel(platform)} Cookie 已自动刷新。` };
  } finally {
    await browser.close().catch(() => undefined);
  }
}

function activeUniqueCookies(cookies: any[]): any[] {
  const nowSeconds = Date.now() / 1000;
  const byKey = new Map<string, any>();
  for (const cookie of cookies) {
    const expires = Number(cookie?.expires);
    if (Number.isFinite(expires) && expires > 0 && expires <= nowSeconds) continue;
    if (!cookie?.name || !cookie?.value) continue;
    byKey.set(`${cookie.name}|${cookie.domain}|${cookie.path || "/"}`, cookie);
  }
  return [...byKey.values()].slice(0, 120);
}

async function refreshSentimentBrowserCookies(statuses: SentimentCookieStatus[], warnings: string[]) {
  const targets = statuses.filter(sentimentCookieStatusNeedsRefresh).slice(0, 2);
  if (!targets.length) return statuses;
  const refreshed: SentimentHotPlatform[] = [];
  for (const status of targets) {
    const result = await refreshSentimentBrowserCookiesForPlatform(status.platform).catch((error) => ({
      ok: false,
      message: `${sentimentCookiePlatformLabel(status.platform)} Cookie 自动刷新失败：${error instanceof Error ? error.message : String(error)}`,
    }));
    warnings.push(result.message);
    if (result.ok) refreshed.push(status.platform);
  }
  if (!refreshed.length) return statuses;
  const nextStatuses = await fetchSentimentCookieStatuses().catch(() => statuses);
  return nextStatuses;
}

async function triggerRealtimeSentimentScan(platforms: SentimentHotPlatform[], warnings: string[]) {
  const sources = [...new Set(platforms)].filter((platform) => platform === "threads" || platform === "instagram");
  if (!sources.length) return;
  try {
    const response = await fetch(`${resolveSentimentBackendUrl()}/api/sentiment/scan-start`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reason: "manual", mode: "fast", sources, days: 2 }),
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) {
      warnings.push(`实时扫描触发失败：HTTP ${response.status}`);
      return;
    }
    const json = await response.json().catch(() => ({}));
    warnings.push(json?.alreadyRunning ? "舆情后端已有实时扫描在运行，已复用当前任务。" : "已触发舆情后端实时扫描，结果会持续进入候选库。");
  } catch (error) {
    warnings.push("实时扫描触发失败：" + (error instanceof Error ? error.message : String(error)));
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
  const keywords = await buildSentimentHotKeywordsWithModel({ archive, prompt: args.prompt, memorySummaries: args.memorySummaries, warnings });
  const limit = args.limit || 10;
  const poolLimit = Math.max(limit * 10, 100);
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

  if (hasSearchKeywords && candidates.length < poolLimit) {
    const instagramCandidates = await fetchInstagramReaderSearchCandidates({
      archiveId,
      keywords,
      queries: buildOrderedSentimentQueries(buildThreadsSearchQueries(keywords), args.refresh ? Date.now() + candidates.length : candidates.length, args.refresh === true).slice(0, 12),
      limit: poolLimit,
      refresh: args.refresh === true,
    }).catch((error) => {
      warnings.push("Instagram reader 抓取失敗：" + (error instanceof Error ? error.message : String(error)));
      return [];
    });
    if (instagramCandidates.length > 0) {
      const byId = new Map(candidates.map((candidate) => [candidate.id, candidate]));
      const byKey = new Set(candidates.map((candidate) => sentimentCandidateDedupeKey(candidate)));
      for (const candidate of instagramCandidates) {
        const dedupeKey = sentimentCandidateDedupeKey(candidate);
        if (!byId.has(candidate.id) && !byKey.has(dedupeKey)) {
          byId.set(candidate.id, candidate);
          byKey.add(dedupeKey);
        }
        if (byId.size >= poolLimit) break;
      }
      candidates = sortRelevantHotCandidates([...byId.values()], keywords, poolLimit);
      warnings.push(args.refresh ? `已即時刷新 Instagram reader 候選 ${instagramCandidates.length} 篇。` : `已加入 Instagram reader 候選 ${instagramCandidates.length} 篇。`);
    }
  }

  const runtime = await withSentimentTimeout(ensureSentimentRuntime(), 6_000, {
    ok: false,
    url: resolveSentimentBackendUrl(),
    warning: "\u8206\u60c5\u5f8c\u53f0\u555f\u52d5\u8f03\u6162\uff0c\u5df2\u512a\u5148\u4f7f\u7528\u0020\u0054\u0068\u0072\u0065\u0061\u0064\u0073\u0020\u0072\u0065\u0061\u0064\u0065\u0072\u0020\u5019\u9078\u3002",
  });
  if (!runtime.ok && runtime.warning) warnings.push(runtime.warning);

  let cookieStatuses = await withSentimentTimeout(fetchSentimentCookieStatuses(), 6_000, [
    { platform: "threads" as const, health: "unknown" as const, label: "Threads", message: "\u8206\u60c5\u0020\u0043\u006f\u006f\u006b\u0069\u0065\u0020\u72c0\u614b\u6aa2\u67e5\u8d85\u6642\u3002" },
    { platform: "instagram" as const, health: "unknown" as const, label: "Instagram", message: "\u8206\u60c5\u0020\u0043\u006f\u006f\u006b\u0069\u0065\u0020\u72c0\u614b\u6aa2\u67e5\u8d85\u6642\u3002" },
  ]);
  if (args.refresh === true && runtime.ok) {
    cookieStatuses = await withSentimentTimeout(refreshSentimentBrowserCookies(cookieStatuses, warnings), 35_000, cookieStatuses);
  }
  const usableSources = cookieStatuses
    .filter(sentimentCookieStatusHasUsableCookies)
    .map((status) => status.platform);
  if (args.refresh === true && runtime.ok && usableSources.length > 0) {
    await withSentimentTimeout(triggerRealtimeSentimentScan(usableSources, warnings), 6_000, undefined);
  }

  if (hasSearchKeywords && candidates.length < limit) {
    const databaseCandidates = await readCandidatesFromDatabase({ archiveId, keywords, limit: poolLimit, excludeShown: args.refresh === true });
    if (databaseCandidates.length > 0) {
      const byId = new Map(candidates.map((candidate) => [candidate.id, candidate]));
      const byKey = new Set(candidates.map((candidate) => sentimentCandidateDedupeKey(candidate)));
      for (const candidate of databaseCandidates) {
        const dedupeKey = sentimentCandidateDedupeKey(candidate);
        if (!byId.has(candidate.id) && !byKey.has(dedupeKey)) {
          byId.set(candidate.id, candidate);
          byKey.add(dedupeKey);
        }
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
      refresh: args.refresh === true,
      warnings,
    });
    candidates = await filterSentimentCandidatesWithModel({ archive, keywords, candidates, limit, warnings });
  }
  if (runtime.ok && usableSources.length > 0) {
    void syncSentimentKeywords(keywords).catch(() => undefined);
    const missingSources = cookieStatuses
      .filter((status) => !sentimentCookieStatusHasUsableCookies(status))
      .map((status) => sentimentCookiePlatformLabel(status.platform));
    if (missingSources.length > 0 && missingSources.length < cookieStatuses.length) {
      warnings.push(`${missingSources.join(" / ")} 缺少有效 Cookie，已跳过对应平台真实扫描；其余平台仍会继续使用。`);
    }
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
  refresh?: boolean;
  warnings: string[];
}): Promise<SentimentHotCandidate[]> {
  const out: SentimentHotCandidate[] = [];
  const seen = new Set<string>();
  const seenDedupeKeys = new Set<string>();
  const add = (candidate: SentimentHotCandidate) => {
    const content = cleanSentimentCandidateContent(candidate.content || "");
    if (!candidate?.id || seen.has(candidate.id)) return;
    const dedupeKey = sentimentCandidateDedupeKey(candidate, content);
    if (seenDedupeKeys.has(dedupeKey)) return;
    if (!content || isLowQualitySentimentContent(content) || !isChineseSentimentCandidate(content)) return;
    const normalized = { ...candidate, content };
    if (!isUsefulHotCandidate(normalized)) return;
    if (!candidateMatchesCurrentKeywords(normalized, args.keywords)) return;
    seen.add(candidate.id);
    seenDedupeKeys.add(dedupeKey);
    out.push(normalized);
  };

  for (const candidate of args.candidates) add(candidate);
  if (out.length >= args.limit) return out.slice(0, args.limit);

  const fallbackCandidates = [
    ...readThreadsSearchCandidateCache(args.archiveId, args.keywords, args.limit * 4, args.refresh === true),
    ...(await readCandidatesFromDatabase({
      archiveId: args.archiveId,
      keywords: args.keywords,
      limit: args.limit * 4,
      excludeShown: args.refresh === true,
    }).catch(() => [])),
  ];
  for (const candidate of fallbackCandidates) {
    add(candidate);
    if (out.length >= args.limit) break;
  }

  if (out.length >= args.limit) {
    if (args.refresh === true) {
      args.warnings.push("\u5373\u6642\u65b0\u7d50\u679c\u4e0d\u8db3\u0020" + args.limit + "\u0020\u7bc7\uff0c\u5df2\u50c5\u4f7f\u7528\u672a\u5c55\u793a\u904e\u7684\u9ad8\u71b1\u5ea6\u5019\u9078\u88dc\u8db3\uff1b\u4e0d\u6703\u7528\u91cd\u8907\u820a\u63a8\u6587\u786c\u5145\u6578\u3002");
    } else {
      args.warnings.push("\u5373\u6642\u65b0\u7d50\u679c\u4e0d\u8db3\u0020" + args.limit + "\u0020\u7bc7\uff0c\u5df2\u7528\u540c\u4eba\u8a2d\u95dc\u9375\u8a5e\u7684\u9ad8\u71b1\u5ea6\u6b77\u53f2\u5019\u9078\u88dc\u9f4a\u3002");
    }
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

function backfillSentimentModelSelection(args: {
  selected: SentimentHotCandidate[];
  candidates: SentimentHotCandidate[];
  limit: number;
  warnings: string[];
  reason: string;
}): SentimentHotCandidate[] {
  const out: SentimentHotCandidate[] = [];
  const seen = new Set<string>();
  const add = (candidate: SentimentHotCandidate | undefined) => {
    if (!candidate?.id || seen.has(candidate.id)) return;
    seen.add(candidate.id);
    out.push(candidate);
  };
  for (const candidate of args.selected) {
    add(candidate);
    if (out.length >= args.limit) return out.slice(0, args.limit);
  }
  if (args.selected.length > 0 && out.length < args.limit) {
    args.warnings.push(`模型筛选保留 ${out.length}/${args.limit} 篇；不会为凑满数量补入模型未选中的候选。`);
  }
  return out.slice(0, args.limit);
}
async function filterSentimentCandidatesWithModel(args: {
  archive?: PersonaArchive;
  keywords: string[];
  candidates: SentimentHotCandidate[];
  limit: number;
  warnings: string[];
}): Promise<SentimentHotCandidate[]> {
  const candidates = sortRelevantHotCandidates(args.candidates, args.keywords, Math.max(args.limit * 10, 100));
  if (candidates.length <= args.limit) return candidates;
  const personaText = [
    args.archive?.name ? `Name: ${args.archive.name}` : "",
    args.archive?.content ? `Profile: ${args.archive.content}` : "",
    args.archive?.setup ? `Setup: ${JSON.stringify(args.archive.setup)}` : "",
    `Keywords: ${args.keywords.join(" / ")}`,
  ].filter(Boolean).join("\n");
  const candidateText = candidates.slice(0, 100).map((candidate, index) => {
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
            "5. \u5982\u679c\u5f3a\u76f8\u5173\u5019\u9009\u4e0d\u8db3\uff0c\u53ef\u4ee5\u88dc\u5145\u300c\u4e0d\u51b2\u7a81\u3001\u53ef\u88ab\u8a72\u4eba\u8a2d\u6539\u5beb\u3001\u540c\u9818\u57df\u6216\u540c\u53d7\u773e\u6703\u611f\u8208\u8da3\u300d\u7684\u5019\u9078\uff1b\u4f46\u4ecd\u7136\u5fc5\u9808\u6392\u9664\u7121\u95dc\u6216\u885d\u7a81\u5167\u5bb9\u3002",
            `6. \u8acb\u76e1\u91cf\u8fd4\u56de ${args.limit} \u500b\u5e8f\u865f\uff1b\u53ea\u6709\u5b8c\u5168\u6c92\u6709\u4e0d\u885d\u7a81\u4e14\u53ef\u6539\u5beb\u7684\u5019\u9078\u6642\uff0c\u624d\u8fd4\u56de []\u3002`,
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
      { temperature: 0.1, maxOutputTokens: 320 },
      AbortSignal.timeout(10_000),
      {
        isUsableResponse: (data) => Boolean(extractText(data).trim()),
        isRetryableError: () => false,
      },
    );
    const modelText = extractText(result.data);
    if (/^\s*```(?:json)?\s*\[\s*]\s*```?\s*$/i.test(modelText) || /^\s*\[\s*]\s*$/.test(modelText)) {
      args.warnings.push("模型筛选未返回可用候选；不会为凑满数量补入未通过筛选的内容。");
      return [];
    }
    const indexes = parseModelIndexList(modelText, candidates.length);
    const selected = indexes.map((index) => candidates[index]).filter(Boolean);
    if (selected.length > 0) {
      return backfillSentimentModelSelection({
        selected,
        candidates,
        limit: args.limit,
        warnings: args.warnings,
        reason: `模型筛选返回 ${selected.length}/${args.limit} 篇`,
      });
    }
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
    const existingKeys = new Set(readerResults.map((candidate) => sentimentCandidateDedupeKey(candidate)));
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
        const dedupeKey = sentimentCandidateDedupeKey(candidate);
        if (!existing.has(candidate.id) && !existingKeys.has(dedupeKey)) {
          existing.set(candidate.id, candidate);
          existingKeys.add(dedupeKey);
        }
        if (existing.size >= args.limit) break;
      }
    }
    readerResults = [...existing.values()];
  }

  if (readerResults.length < args.limit) {
    const cachedResults = readThreadsSearchCandidateCache(args.archiveId, args.keywords, args.limit, args.refresh === true);
    const byId = new Map(readerResults.map((candidate) => [candidate.id, candidate]));
    const byKey = new Set(readerResults.map((candidate) => sentimentCandidateDedupeKey(candidate)));
    for (const candidate of cachedResults) {
      const dedupeKey = sentimentCandidateDedupeKey(candidate);
      if (!byId.has(candidate.id) && !byKey.has(dedupeKey)) {
        byId.set(candidate.id, candidate);
        byKey.add(dedupeKey);
      }
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
      const cookies = readSentimentBrowserAuthCookies("threads");
      if (cookies.length) await context.addCookies(cookies as any[]).catch(() => undefined);
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
          if (results.some((item) => item.id === candidate.id || sentimentCandidateDedupeKey(item) === sentimentCandidateDedupeKey(candidate))) continue;
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
  const sorted = sortRelevantHotCandidates(results, args.keywords, args.limit);
  if (sorted.length > 0) writeThreadsSearchCandidateCache(args.keywords, sorted);
  return sorted.length > 0 ? sorted : readThreadsSearchCandidateCache(args.archiveId, args.keywords, args.limit, args.refresh === true);
}

const JINA_READER_PREFIX = "https://r.jina.ai/http://";

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
  const allKeys = new Set<string>();
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
      const dedupeKey = sentimentCandidateDedupeKey(candidate);
      if (all.some((item) => item.id === candidate.id) || allKeys.has(dedupeKey)) continue;
      allKeys.add(dedupeKey);
      all.push(candidate);
      if (all.length >= args.limit) break;
    }
    if (all.length >= args.limit) break;
  }
  return sortUsefulHotCandidates(await enrichThreadsCandidateDetails(all), args.limit);
}

async function fetchInstagramReaderSearchCandidates(args: {
  archiveId: string;
  keywords: string[];
  queries: string[];
  limit: number;
  refresh?: boolean;
  excludeIds?: Set<string>;
}): Promise<SentimentHotCandidate[]> {
  const excluded = args.excludeIds || (args.refresh ? getSentimentHotRefreshExcludedIds(args.archiveId) : getSentimentHotExcludedIds(args.archiveId));
  const all: SentimentHotCandidate[] = [];
  const allKeys = new Set<string>();
  const searches = await Promise.all(
    args.queries.map(async (query, index) => {
      const normalizedQuery = cleanText(query).replace(/^#/, "");
      const targets = [
        `https://www.instagram.com/explore/search/keyword/?q=${encodeURIComponent(normalizedQuery)}`,
        hasHan(normalizedQuery) ? `https://www.instagram.com/explore/tags/${encodeURIComponent(normalizedQuery)}/` : "",
      ].filter(Boolean);
      const texts: Array<{ query: string; targetUrl: string; text: string }> = [];
      for (const targetUrl of targets) {
        const readerTargetUrl = args.refresh ? `${targetUrl}${targetUrl.includes("?") ? "&" : "?"}__r=${Date.now().toString(36)}${index}` : targetUrl;
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
          if (response.ok) texts.push({ query, targetUrl, text: await response.text() });
        } catch {
          // Instagram reader is an opportunistic extra source.
        }
      }
      return texts;
    }),
  );
  for (const search of searches.flat()) {
    const parsed = parseInstagramReaderSearchMarkdownCandidates({
      text: search.text,
      query: search.query,
      keywords: args.keywords,
      sourceUrl: search.targetUrl,
      limit: args.limit,
    });
    for (const candidate of parsed) {
      if (excluded.has(candidate.id)) continue;
      if (!candidateMatchesCurrentKeywords(candidate, args.keywords)) continue;
      const dedupeKey = sentimentCandidateDedupeKey(candidate);
      if (all.some((item) => item.id === candidate.id) || allKeys.has(dedupeKey)) continue;
      allKeys.add(dedupeKey);
      all.push(candidate);
      if (all.length >= args.limit) break;
    }
    if (all.length >= args.limit) break;
  }
  return sortUsefulHotCandidates(all, args.limit);
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

function assignThreadsProfileHotMetric(out: Partial<ThreadsProfileHotMetrics>, label: string, value: number | undefined) {
  if (typeof value !== "number") return;
  if (/followers?|\u7c89\u7d72|\u7c89\u4e1d/i.test(label)) out.followers = value;
  else if (/following|\u8ffd\u8e64\u4e2d|\u5173\u6ce8\u4e2d/i.test(label)) out.following = value;
}

function parseThreadsProfileHotMetricsText(text: string): Partial<ThreadsProfileHotMetrics> {
  const lines = String(text || "")
    .split(/\r?\n+/g)
    .map(cleanText)
    .filter(Boolean)
    .slice(0, 250);
  const out: Partial<ThreadsProfileHotMetrics> = {};
  const joined = lines.join("\n");
  const labels = "followers?|following|\\u7c89\\u7d72|\\u7c89\\u4e1d|\\u8ffd\\u8e64\\u4e2d|\\u5173\\u6ce8\\u4e2d";
  const combined = new RegExp(`(\\d+(?:[.,]\\d+)?\\s*(?:[KkMm\\u842c\\u4e07])?)\\s*(${labels})`, "gi");
  for (const match of joined.matchAll(combined)) {
    assignThreadsProfileHotMetric(out, match[2] || "", parseMetricNumberLoose(match[1]));
  }
  for (const match of joined.matchAll(new RegExp(`(\\d+(?:[.,]\\d+)?\\s*(?:[KkMm\\u842c\\u4e07])?)\\s*(?:\\u4f4d)?\\s*(\\u7c89\\u7d72|\\u7c89\\u4e1d|followers?)`, "gi"))) {
    assignThreadsProfileHotMetric(out, match[2] || "", parseMetricNumberLoose(match[1]));
  }
  for (let index = 0; index < lines.length; index += 1) {
    const current = lines[index] || "";
    const next = lines[index + 1] || "";
    const prev = lines[index - 1] || "";
    const currentIsLabel = new RegExp(`^(${labels})$`, "i").test(current) || new RegExp(labels, "i").test(current);
    const nextIsLabel = new RegExp(`^(${labels})$`, "i").test(next) || new RegExp(labels, "i").test(next);
    if (currentIsLabel) assignThreadsProfileHotMetric(out, current, parseMetricNumberLoose(current) ?? parseMetricNumberLoose(next) ?? parseMetricNumberLoose(prev));
    else if (nextIsLabel) assignThreadsProfileHotMetric(out, next, parseMetricNumberLoose(current));
  }
  const readMetricRuns = (patterns: RegExp[], options?: { skip?: (matchText: string) => boolean }) => {
    const values: number[] = [];
    for (const pattern of patterns) {
      for (const match of joined.matchAll(pattern)) {
        if (options?.skip?.(match[0] || "")) continue;
        const value = parseMetricNumberLoose(match[1]);
        if (typeof value === "number") values.push(value);
      }
    }
    return values;
  };
  const uniqueMetricValues = (values: number[]) => [...new Set(values)];
  const metricNumber = "(\\d+(?:[.,]\\d+)?\\s*(?:[KkMm\\u842c\\u4e07])?)";
  const likeValues = readMetricRuns([
    new RegExp(`(?:讚|赞|likes?)\\s*${metricNumber}`, "gi"),
  ]);
  const commentValues = readMetricRuns([
    new RegExp(`(?:留言|評論|评论|comments?)\\s*${metricNumber}`, "gi"),
  ]);
  const shareValues = readMetricRuns([
    new RegExp(`(?:分享|轉發|转发|shares?|reposts?)\\s*${metricNumber}`, "gi"),
  ]);
  const recentViewValues = uniqueMetricValues(readMetricRuns([
    new RegExp(`${metricNumber}\\s*(?:次)?\\s*(?:最近瀏覽次數|最近浏览次数)`, "gi"),
  ]));
  const viewValues = readMetricRuns([
    new RegExp(`(?:瀏覽|浏览|觀看|观看|views?|plays?|impressions?)\\s*${metricNumber}`, "gi"),
    new RegExp(`${metricNumber}\\s*(?:次)?\\s*(?:最近瀏覽次數|最近浏览次数|瀏覽|浏览|觀看|观看|views?|plays?|impressions?)`, "gi"),
  ], { skip: (matchText) => /最近瀏覽次數|最近浏览次数/i.test(matchText) });
  const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
  const scannedPosts = Math.max(likeValues.length, commentValues.length, shareValues.length, viewValues.length);
  if (scannedPosts > 0) out.scannedPosts = scannedPosts;
  const likes = sum(likeValues);
  const comments = sum(commentValues);
  const shares = sum(shareValues);
  const views = sum(viewValues);
  if (recentViewValues.length) out.recentViews = Math.max(...recentViewValues);
  if (likes > 0) out.likes = likes;
  if (comments > 0) out.comments = comments;
  if (shares > 0) out.shares = shares;
  if (views > 0) out.views = views;
  return out;
}

function threadsProfileHotMetricsHasValue(metrics: Partial<ThreadsProfileHotMetrics>) {
  return ["followers", "following", "recentViews", "scannedPosts", "likes", "comments", "shares", "views"].some((key) => typeof (metrics as any)[key] === "number");
}

export function analyzeThreadsProfileVisibleSignals(args: {
  username: string;
  bodyText: string;
  buttonText: string[];
  links: string[];
}) {
  const text = [args.bodyText, ...(args.buttonText || [])]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .join("\n");
  const parsed = parseThreadsProfileHotMetricsText(text);
  const postUrls = extractUniqueThreadsPostUrlsFromProfileLinks(args.links || [], args.username);
  return {
    text,
    rawText: text,
    parsed,
    postUrls,
    hasUsableProfileSignals: threadsProfileHotMetricsHasValue(parsed) || postUrls.length > 0,
  };
}

export function shouldTreatThreadsProfileAsLoginWall(args: {
  username: string;
  bodyText: string;
  buttonText: string[];
  links: string[];
}) {
  const visible = analyzeThreadsProfileVisibleSignals(args);
  return detectThreadsProfileLoginWall(visible.text) && !visible.hasUsableProfileSignals;
}

function hasThreadsProfileLoginSessionCookie(cookies: any[]) {
  return hasValidThreadsSessionCookie(cookies);
}

function detectThreadsProfileLoginWall(text: string) {
  const rawText = String(text || "");
  if (/(?:編輯個人檔案|编辑个人档案|編輯主頁|编辑主页|洞察報告|成效分析)/i.test(rawText)) return false;
  return /login|log in|sign in|accounts\/login|登入以查看更多|使用 Instagram 帳號繼續|使用 Instagram 账号继续|登入 Instagram|登录 Instagram/i.test(rawText);
}

function buildThreadsProfileIncompleteMetrics(username: string, refreshedAt: string, scope: ThreadsProfileHotMetrics["scope"], rawText?: string): ThreadsProfileHotMetrics {
  return {
    platform: "threads",
    username,
    refreshedAt,
    method: "failed",
    complete: false,
    scope,
    rawText: rawText ? rawText.slice(0, 4000) : undefined,
    error: "Threads browser login is not valid for full account aggregation. Refresh Threads sentiment cookies with a logged-in sessionid before retrying.",
  };
}

type ThreadsGraphqlProfilePostAggregate = {
  pk: string;
  code: string;
  sourceUrl: string;
  likeCount: number;
  commentCount: number;
  repostCount: number;
  shareCount: number;
};

type ThreadsGraphqlProfilePageResult = {
  posts: ThreadsGraphqlProfilePostAggregate[];
  endCursor?: string;
  hasNextPage: boolean;
  pageInfoResolved: boolean;
};

type ThreadsGraphqlRequestTemplate = {
  params: Record<string, string>;
  variables: Record<string, any>;
};

function buildThreadsGraphqlProfileSourceUrl(username: string, post: any): string {
  const normalizedUsername = String(username || "").replace(/^@+/, "").trim();
  const canonicalUrl = cleanText(post?.canonical_url || post?.canonicalUrl);
  if (/^https?:\/\/(?:www\.)?threads\.(?:net|com)\//i.test(canonicalUrl)) {
    return canonicalUrl.replace(/^https:\/\/www\.threads\.com\//i, "https://www.threads.net/");
  }
  const code = cleanText(post?.code);
  if (!normalizedUsername || !code) return "";
  return `https://www.threads.net/@${encodeURIComponent(normalizedUsername)}/post/${encodeURIComponent(code)}`;
}

export function parseThreadsGraphqlProfilePagePayload(args: {
  username: string;
  payload: any;
}): ThreadsGraphqlProfilePageResult {
  const mediaData = args.payload?.data?.mediaData;
  const edges = Array.isArray(mediaData?.edges) ? mediaData.edges : [];
  const posts: ThreadsGraphqlProfilePostAggregate[] = [];
  for (const edge of edges) {
    const post = edge?.node?.thread_items?.[0]?.post;
    const pk = cleanText(post?.pk);
    const sourceUrl = buildThreadsGraphqlProfileSourceUrl(args.username, post);
    if (!pk || !sourceUrl) continue;
    posts.push({
      pk,
      code: cleanText(post?.code),
      sourceUrl,
      likeCount: Math.max(0, Number(post?.like_count) || 0),
      commentCount: Math.max(0, Number(post?.text_post_app_info?.direct_reply_count) || 0),
      repostCount: Math.max(0, Number(post?.text_post_app_info?.repost_count) || 0),
      shareCount: Math.max(0, Number(post?.text_post_app_info?.reshare_count) || 0),
    });
  }
  return {
    posts,
    endCursor: cleanText(mediaData?.page_info?.end_cursor),
    hasNextPage: mediaData?.page_info?.has_next_page === true,
    pageInfoResolved: typeof mediaData?.page_info?.has_next_page === "boolean",
  };
}

function parseThreadsGraphqlRequestTemplate(postData: string): ThreadsGraphqlRequestTemplate | null {
  const params = new URLSearchParams(String(postData || ""));
  const rawVariables = params.get("variables");
  if (!rawVariables) return null;
  const variables = safeJson(rawVariables);
  if (!variables || typeof variables !== "object") return null;
  const out: Record<string, string> = {};
  for (const [key, value] of params.entries()) out[key] = value;
  delete out.variables;
  return {
    params: out,
    variables,
  };
}

async function requestThreadsGraphqlProfilePage(args: {
  page: any;
  template: ThreadsGraphqlRequestTemplate;
  after: string;
}): Promise<any> {
  const params = new URLSearchParams(args.template.params);
  params.set("variables", JSON.stringify({
    ...args.template.variables,
    after: args.after,
  }));
  const text = await args.page.evaluate(async ({ body }) => {
    const response = await fetch("https://www.threads.com/graphql/query", {
      method: "POST",
      credentials: "include",
      headers: {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
      },
      body,
    });
    return await response.text();
  }, { body: params.toString() });
  return safeJson(text);
}

async function collectThreadsGraphqlProfilePosts(args: {
  page: any;
  username: string;
  initialPayload: any;
  initialTemplate: ThreadsGraphqlRequestTemplate;
}): Promise<{ posts: ThreadsGraphqlProfilePostAggregate[]; reachedEnd: boolean }> {
  const byPk = new Map<string, ThreadsGraphqlProfilePostAggregate>();
  let current = parseThreadsGraphqlProfilePagePayload({
    username: args.username,
    payload: args.initialPayload,
  });
  for (const post of current.posts) byPk.set(post.pk, post);
  let cursor = current.endCursor || "";
  let hasNextPage = current.hasNextPage;
  let pageInfoResolved = current.pageInfoResolved;
  let pages = 0;
  while (hasNextPage && cursor && pages < 120) {
    pages += 1;
    const payload = await requestThreadsGraphqlProfilePage({
      page: args.page,
      template: args.initialTemplate,
      after: cursor,
    }).catch(() => null);
    if (!payload) return { posts: [...byPk.values()], reachedEnd: false };
    current = parseThreadsGraphqlProfilePagePayload({
      username: args.username,
      payload,
    });
    if (current.posts.length === 0 && !current.endCursor) {
      return { posts: [...byPk.values()], reachedEnd: pages >= 2 || byPk.size >= 20 };
    }
    const beforeSize = byPk.size;
    for (const post of current.posts) byPk.set(post.pk, post);
    if (byPk.size === beforeSize && current.endCursor === cursor) {
      return { posts: [...byPk.values()], reachedEnd: current.pageInfoResolved && current.hasNextPage !== true };
    }
    cursor = current.endCursor || "";
    hasNextPage = current.hasNextPage;
    pageInfoResolved = current.pageInfoResolved;
  }
  return { posts: [...byPk.values()], reachedEnd: pageInfoResolved && hasNextPage !== true };
}

export function parseThreadsPostViewCountFromText(text: string): number | undefined {
  return parseMetricNumberLoose(
    String(text || "").match(/(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*(?:次瀏覽|次浏览|瀏覽|浏览|views?)/i)?.[1]
      || String(text || "").match(/Thread\s+(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s+views/i)?.[1]
      || String(text || "").match(/(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*views/i)?.[1],
  );
}

async function readThreadsViewCountFromPostPage(args: {
  page: any;
  sourceUrl: string;
}): Promise<number | undefined> {
  await args.page.goto(args.sourceUrl, {
    waitUntil: "domcontentloaded",
    timeout: 25_000,
  }).catch(() => null);
  await args.page.waitForFunction(() => {
    const text = String(document.body?.innerText || "");
    return /(\d+(?:[.,]\d+)?\s*(?:K|M|萬|万)?)\s*(次瀏覽|次浏览|瀏覽|浏览|views?)/i.test(text)
      || /回覆|回复|Replies?|尚無回覆|暂无回复/i.test(text);
  }, undefined, { timeout: 8_000 }).catch(() => null);
  const text = await args.page.locator("body").innerText({ timeout: 6_000 }).catch(() => "");
  return parseThreadsPostViewCountFromText(text);
}

async function collectThreadsViewCountsFromPostPages(args: {
  context: any;
  posts: ThreadsGraphqlProfilePostAggregate[];
}): Promise<{ totalViews: number; resolvedPosts: number }> {
  if (!args.posts.length) return { totalViews: 0, resolvedPosts: 0 };
  const page = await args.context.newPage();
  try {
    let totalViews = 0;
    let resolvedPosts = 0;
    for (const post of args.posts) {
      const viewCount = await readThreadsViewCountFromPostPage({
        page,
        sourceUrl: post.sourceUrl,
      }).catch(() => undefined);
      if (typeof viewCount === "number") {
        totalViews += viewCount;
        resolvedPosts += 1;
      }
    }
    return { totalViews, resolvedPosts };
  } finally {
    await page.close().catch(() => null);
  }
}

async function buildThreadsProfileAggregateMetrics(args: {
  username: string;
  text: string;
  links: string[];
}): Promise<Partial<ThreadsProfileHotMetrics>> {
  const postUrls = extractUniqueThreadsPostUrlsFromProfileLinks(args.links || [], args.username);
  const out: Partial<ThreadsProfileHotMetrics> = {};
  if (postUrls.length > 0) {
    out.scannedPosts = postUrls.length;
    out.posts = postUrls.length;
  }
  const detailResults = await Promise.all(
    postUrls.slice(0, 80).map(async (sourceUrl) => ({
      sourceUrl,
      detail: await fetchThreadsDetailData(sourceUrl).catch(() => ({ engagement: {}, media: [] })),
    })),
  );
  let views = 0;
  for (const result of detailResults) {
    const engagement = result.detail.engagement || {};
    views += typeof engagement.viewCount === "number" ? engagement.viewCount : 0;
  }
  if (views > 0) out.views = views;
  return out;
}

async function buildThreadsProfileAggregateMetricsFromBrowserPage(args: {
  page: any;
  username: string;
  links: string[];
}): Promise<Partial<ThreadsProfileHotMetrics>> {
  const postUrls = extractUniqueThreadsPostUrlsFromProfileLinks(args.links || [], args.username).slice(0, 120);
  const out: Partial<ThreadsProfileHotMetrics> = {};
  if (postUrls.length > 0) {
    out.scannedPosts = postUrls.length;
    out.posts = postUrls.length;
  }
  let likes = 0;
  let comments = 0;
  let reposts = 0;
  let shares = 0;
  let views = 0;
  for (const sourceUrl of postUrls) {
    await args.page.goto(sourceUrl, {
      waitUntil: "domcontentloaded",
      timeout: 25_000,
    }).catch(() => null);
    await args.page.waitForTimeout(2200);
    const detailText = await args.page.locator("body").innerText({ timeout: 8_000 }).catch(() => "");
    const actionTexts = await args.page.$$eval("[role=button],button,a", (items: any[]) => items
      .map((item: any) => (item.textContent || "").trim())
      .filter(Boolean)
      .slice(0, 120)).catch(() => []);
    const detail = parseThreadsBrowserPostDetailMetrics({ text: detailText, actionTexts });
    const engagement = detail?.engagement || {};
    const metrics = detail?.metrics || {};
    likes += typeof engagement.likeCount === "number" ? engagement.likeCount : 0;
    comments += typeof engagement.commentCount === "number" ? engagement.commentCount : 0;
    reposts += typeof metrics.repost_count === "number" ? metrics.repost_count : 0;
    shares += typeof metrics.send_count === "number" ? metrics.send_count : 0;
    views += typeof engagement.viewCount === "number" ? engagement.viewCount : 0;
  }
  if (postUrls.length > 0) {
    out.likes = likes;
    out.comments = comments;
    out.reposts = reposts;
    out.shares = shares;
  }
  if (views > 0) out.views = views;
  return out;
}

export async function fetchThreadsProfileLightMetrics(usernameInput: string): Promise<ThreadsProfileHotMetrics> {
  const username = String(usernameInput || "").replace(/^@+/, "").trim();
  const refreshedAt = new Date().toISOString();
  if (!username) {
    return {
      platform: "threads",
      username,
      refreshedAt,
      lightRefreshedAt: refreshedAt,
      method: "failed",
      error: "Threads 帐号未设定，无法刷新轻量热点数据",
    };
  }
  const profileUrl = `https://www.threads.net/@${encodeURIComponent(username)}`;
  const cookies = readSentimentBrowserAuthCookies("threads");
  if (!hasThreadsProfileLoginSessionCookie(cookies)) {
    return buildThreadsProfileIncompleteMetrics(username, refreshedAt, "failed");
  }
  if (!process.env.VITEST_WORKER_ID && cookies.length) {
    let browser: any = null;
    try {
      const playwright = await import("playwright");
      browser = await playwright.chromium.launch(buildLocalChromiumLaunchOptions());
      const context = await browser.newContext({
        viewport: { width: 900, height: 1400 },
        locale: "zh-TW",
        userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      });
      await context.addCookies(cookies as any).catch(() => undefined);
      const page = await context.newPage();
      await page.goto(`${profileUrl}?__r=${Date.now().toString(36)}`, {
        waitUntil: "domcontentloaded",
        timeout: 25_000,
      }).catch(() => null);
      await page.waitForTimeout(2200);
      const bodyText = await page.locator("body").innerText({ timeout: 8_000 }).catch(() => "");
      const buttonText = await page.$$eval("[role=button],button,a", (items) => items
        .map((item) => (item.textContent || "").trim())
        .filter(Boolean)
        .slice(0, 120)).catch(() => []);
      const links = await page.$$eval("a[href]", (items: any[]) => items
        .map((item: any) => item.href || item.getAttribute?.("href") || "")
        .filter(Boolean)).catch(() => []);
      const visible = analyzeThreadsProfileVisibleSignals({ username, bodyText, buttonText, links });
      if (detectThreadsProfileLoginWall(visible.text) && !visible.hasUsableProfileSignals) {
        return buildThreadsProfileIncompleteMetrics(username, refreshedAt, "failed", visible.rawText);
      }
      const parsed = visible.parsed || {};
      if (
        typeof parsed.followers === "number"
        || typeof parsed.following === "number"
        || typeof parsed.recentViews === "number"
        || typeof parsed.views === "number"
      ) {
        return {
          platform: "threads",
          username,
          followers: parsed.followers,
          following: parsed.following,
          recentViews: parsed.recentViews ?? parsed.views,
          refreshedAt,
          lightRefreshedAt: refreshedAt,
          method: "browser",
          complete: true,
          scope: "profile_visible_light",
          rawText: visible.rawText.slice(0, 4000),
        };
      }
    } catch {
      // Fall through to the explicit incomplete result below.
    } finally {
      await browser?.close().catch(() => undefined);
    }
  }
  return buildThreadsProfileIncompleteMetrics(username, refreshedAt, "failed");
}

export async function fetchThreadsProfileHotMetrics(usernameInput: string): Promise<ThreadsProfileHotMetrics> {
  const username = String(usernameInput || "").replace(/^@+/, "").trim();
  const refreshedAt = new Date().toISOString();
  if (!username) {
    return {
      platform: "threads",
      username,
      refreshedAt,
      method: "failed",
      error: "Threads 帳號未設定，無法刷新熱點資料",
    };
  }
  const profileUrl = `https://www.threads.net/@${encodeURIComponent(username)}`;
  const cookies = readSentimentBrowserAuthCookies("threads");
  if (!hasThreadsProfileLoginSessionCookie(cookies)) {
    return buildThreadsProfileIncompleteMetrics(username, refreshedAt, "failed");
  }
  if (!process.env.VITEST_WORKER_ID && cookies.length) {
    let browser: any = null;
    try {
      const playwright = await import("playwright");
      browser = await playwright.chromium.launch(buildLocalChromiumLaunchOptions());
      const context = await browser.newContext({
        viewport: { width: 900, height: 1400 },
        locale: "zh-TW",
        userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      });
      await context.addCookies(cookies as any).catch(() => undefined);
      const page = await context.newPage();
      const capturedGraphqlPages = new Map<string, { payload: any; template: ThreadsGraphqlRequestTemplate }>();
      let initialGraphqlPayload: any = null;
      let initialGraphqlTemplate: ThreadsGraphqlRequestTemplate | null = null;
      page.on("response", async (response: any) => {
        try {
          if (!/graphql\/query/i.test(String(response.url?.() || response.url || ""))) return;
          const request = response.request?.();
          const postData = request?.postData?.() || "";
          if (!/BarcelonaProfileThreadsTabRefetchableDirectQuery/.test(postData)) return;
          const template = parseThreadsGraphqlRequestTemplate(postData);
          if (!template) return;
          const payload = safeJson(await response.text().catch(() => ""));
          if (!payload?.data?.mediaData?.edges) return;
          const afterKey = cleanText(template.variables?.after) || "__FIRST__";
          capturedGraphqlPages.set(afterKey, { payload, template });
          if (template.variables?.after == null || template.variables?.after === "") {
            initialGraphqlTemplate = template;
            initialGraphqlPayload = payload;
            return;
          }
          if (!initialGraphqlPayload || !initialGraphqlTemplate) {
            initialGraphqlTemplate = template;
            initialGraphqlPayload = payload;
          }
        } catch {
          // Ignore listener failures and fall back to the existing partial path below.
        }
      });
      await page.goto(`${profileUrl}?__r=${Date.now().toString(36)}`, {
        waitUntil: "domcontentloaded",
        timeout: 25_000,
      }).catch(() => null);
      await page.waitForTimeout(3500);
      const bodyText = await page.locator("body").innerText({ timeout: 8_000 }).catch(() => "");
      const buttonText = await page.$$eval("[role=button],button,a", (items) => items
        .map((item) => (item.textContent || "").trim())
        .filter(Boolean)
        .slice(0, 120)).catch(() => []);
      const links = await page.$$eval("a[href]", (items: any[]) => items
        .map((item: any) => item.href || item.getAttribute?.("href") || "")
        .filter(Boolean)).catch(() => []);
      const visible = analyzeThreadsProfileVisibleSignals({ username, bodyText, buttonText, links });
      if (detectThreadsProfileLoginWall(visible.text) && !visible.hasUsableProfileSignals) {
        return buildThreadsProfileIncompleteMetrics(username, refreshedAt, "public_partial", visible.rawText);
      }
      let parsed = {
        ...visible.parsed,
        ...(await buildThreadsProfileAggregateMetricsFromBrowserPage({ page, username, links })),
      };
      if (initialGraphqlPayload && initialGraphqlTemplate) {
        const collection = await collectThreadsGraphqlProfilePosts({
          page,
          username,
          initialPayload: initialGraphqlPayload,
          initialTemplate: initialGraphqlTemplate,
        }).catch(() => ({ posts: [], reachedEnd: false }));
        const seededPosts = new Map<string, ThreadsGraphqlProfilePostAggregate>();
        const capturedPageCount = capturedGraphqlPages.size;
        let capturedReachedEnd = false;
        for (const { payload } of capturedGraphqlPages.values()) {
          const pageResult = parseThreadsGraphqlProfilePagePayload({ username, payload });
          if (pageResult.pageInfoResolved && pageResult.hasNextPage !== true) capturedReachedEnd = true;
          for (const post of pageResult.posts) {
            seededPosts.set(post.pk, post);
          }
        }
        for (const post of collection.posts) seededPosts.set(post.pk, post);
        const allPosts = [...seededPosts.values()];
        if (allPosts.length) {
          const views = await collectThreadsViewCountsFromPostPages({
            context,
            posts: allPosts,
          }).catch(() => ({ totalViews: 0, resolvedPosts: 0 }));
          parsed = {
            ...visible.parsed,
            posts: allPosts.length,
            scannedPosts: allPosts.length,
            likes: allPosts.reduce((sum, post) => sum + (post.likeCount || 0), 0),
            comments: allPosts.reduce((sum, post) => sum + (post.commentCount || 0), 0),
            reposts: allPosts.reduce((sum, post) => sum + (post.repostCount || 0), 0),
            shares: allPosts.reduce((sum, post) => sum + (post.shareCount || 0), 0),
            ...(views.resolvedPosts === allPosts.length ? { views: views.totalViews } : {}),
          };
          (parsed as any).profileReachedEnd = capturedReachedEnd
            || collection.reachedEnd
            || (capturedPageCount >= 2 && allPosts.length >= 20);
        }
      }
      const complete = threadsProfileHotMetricsHasValue(parsed)
        && typeof parsed.scannedPosts === "number"
        && parsed.scannedPosts > 0
        && typeof parsed.views === "number"
        && (parsed as any).profileReachedEnd === true;
      if (threadsProfileHotMetricsHasValue(parsed)) {
        const { profileReachedEnd: _profileReachedEnd, ...publicParsed } = parsed as any;
        return {
          platform: "threads",
          username,
          ...publicParsed,
          refreshedAt,
          method: "browser",
          complete,
          scope: complete ? "authenticated_full_profile" : "public_partial",
          rawText: visible.rawText.slice(0, 4000),
          error: complete ? undefined : "Threads 已抓到全量帖子互动，但仍有部分帖子浏览量未能完成读取。",
        };
      }
    } catch {
      // Fall through to the explicit incomplete result below.
    } finally {
      await browser?.close?.().catch?.(() => null);
    }
  }
  if (process.env.THREADS_PROFILE_ALLOW_PARTIAL_READER === "1") {
  try {
    const readerTargetUrl = `${profileUrl}?__r=${Date.now().toString(36)}`;
    const response = await fetch(`${JINA_READER_PREFIX}${readerTargetUrl}`, {
      headers: {
        "user-agent": "Mozilla/5.0",
        accept: "text/plain, text/markdown, */*",
        "cache-control": "no-cache",
        pragma: "no-cache",
      },
      signal: buildAbortSignalTimeout(15_000),
    });
    const text = response.ok ? await response.text() : "";
    const links = Array.from(text.matchAll(/https?:\/\/(?:www\.)?threads\.(?:net|com)\/@[^)\]\s]+\/post\/[^)\]\s]+/gi))
      .map((match) => match[0]);
    const parsed = {
      ...parseThreadsProfileHotMetricsText(text),
      ...(await buildThreadsProfileAggregateMetrics({ username, text, links })),
    };
    return {
      platform: "threads",
      username,
      ...parsed,
      refreshedAt,
      method: threadsProfileHotMetricsHasValue(parsed) ? "reader" : "failed",
      rawText: text.slice(0, 4000),
      error: threadsProfileHotMetricsHasValue(parsed) ? undefined : "未從 Threads Profile 讀取到可用熱點資料",
    };
  } catch (error: any) {
    return {
      platform: "threads",
      username,
      refreshedAt,
      method: "failed",
      error: error instanceof Error ? error.message : String(error),
    };
  }
  }
  return buildThreadsProfileIncompleteMetrics(username, refreshedAt, "failed");
}

function extractEngagementMetricsFromText(value: string): NonNullable<SentimentHotCandidate["engagement"]> {
  const text = String(value || "");
  const engagement: NonNullable<SentimentHotCandidate["engagement"]> = {};
  const assign = (key: keyof NonNullable<SentimentHotCandidate["engagement"]>, pattern: RegExp) => {
    const match = text.match(pattern);
    const count = parseMetricNumber(match?.[1] || match?.[0]);
    if (typeof count === "number") (engagement as any)[key] = count;
  };
  const metricSep = String.raw`[\s:：|｜·•,，。()\[\]{}<>]*`;
  assign("likeCount", new RegExp(String.raw`(?:like|likes|liked|讚|赞|喜歡|喜欢|愛心|爱心|點讚|点赞)${metricSep}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)`, "i"));
  assign("commentCount", new RegExp(String.raw`(?:comment|comments|reply|replies|留言|評論|评论|回覆|回复)${metricSep}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)`, "i"));
  assign("viewCount", new RegExp(String.raw`(?:view|views|watch|play|plays|瀏覽|浏览|觀看|观看|播放|閱讀|阅读|流量)${metricSep}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)`, "i"));
  assign("shareCount", new RegExp(String.raw`(?:share|shares|repost|reposts|轉發|转发|分享)${metricSep}(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)`, "i"));
  const rawSignals = Array.from(text.matchAll(/(?:^|\n)\s*\[?(\d+(?:[.,]\d+)?\s*(?:[Kk萬万])?)\]?\s*(?=\n|$)/g))
    .map((match) => parseMetricNumber(match[1]))
    .filter((item): item is number => typeof item === "number" && item > 0)
    .slice(0, 6);
  if (rawSignals.length) engagement.rawSignals = rawSignals;
  return engagement;
}

function extractInstagramEngagementMetricsFromText(value: string): NonNullable<SentimentHotCandidate["engagement"]> {
  const text = String(value || "");
  const engagement: NonNullable<SentimentHotCandidate["engagement"]> = {};
  const likeCount = parseMetricNumberLoose(text.match(/(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*(?:likes?|讚|赞|喜歡|喜欢)/i)?.[1]);
  const commentCount = parseMetricNumberLoose(text.match(/(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*(?:comments?|留言|評論|评论)/i)?.[1]);
  const viewCount = parseMetricNumberLoose(text.match(/(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*(?:views?|plays?|觀看|观看|播放|瀏覽|浏览)/i)?.[1]);
  if (typeof likeCount === "number") engagement.likeCount = likeCount;
  if (typeof commentCount === "number") engagement.commentCount = commentCount;
  if (typeof viewCount === "number") engagement.viewCount = viewCount;
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

function refreshEngagementMetrics(
  base: NonNullable<SentimentHotCandidate["engagement"]>,
  latest: NonNullable<SentimentHotCandidate["engagement"]>,
): NonNullable<SentimentHotCandidate["engagement"]> {
  const refreshed: NonNullable<SentimentHotCandidate["engagement"]> = {};
  if (typeof latest.likeCount === "number") refreshed.likeCount = latest.likeCount;
  if (typeof latest.commentCount === "number") refreshed.commentCount = latest.commentCount;
  if (typeof latest.viewCount === "number") refreshed.viewCount = latest.viewCount;
  if (typeof latest.shareCount === "number") refreshed.shareCount = latest.shareCount;
  const rawSignals = (latest.rawSignals || base.rawSignals || [])
    .filter((item): item is number => typeof item === "number" && Number.isFinite(item) && item > 0);
  if (rawSignals.length) refreshed.rawSignals = [...new Set(rawSignals)].slice(0, 8);
  for (const key of ["likeCount", "commentCount", "viewCount", "shareCount"] as const) {
    if (typeof refreshed[key] !== "number") (refreshed as any)[key] = undefined;
  }
  return refreshed;
}

function buildAbortSignalTimeout(ms: number): AbortSignal | undefined {
  const timeout = (AbortSignal as any)?.timeout;
  if (typeof timeout === "function") return timeout(ms);
  if (typeof AbortController !== "function") return undefined;
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms).unref?.();
  return controller.signal;
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
  }
  return engagement;
}

function normalizeThreadsPublishedHistoryText(value: unknown): string {
  return String(value || "")
    .normalize("NFKC")
    .replace(/\s+/g, "")
    .replace(/[，,。.!！?？:：;；"'“”‘’`·、（）()[\]{}<>《》【】]/g, "")
    .toLowerCase();
}

function isThreadsProfilePostTimeLine(value: string): boolean {
  return /^(?:\d+\s*)?(?:秒|分鐘|分钟|小時|小时|天|週|周|月|年|s|m|h|d|w|mo|y)\b/i.test(value)
    || /^\d+\s*(?:秒|分鐘|分钟|小時|小时|天|週|周|月|年|s|m|h|d|w|mo|y)$/i.test(value);
}

function normalizeThreadsPostUrl(raw: unknown): string {
  const value = String(raw || "").trim();
  const match = value.match(/^https?:\/\/(?:www\.)?threads\.(?:net|com)\/@[^/?#\s]+\/post\/[^/?#\s]+/i);
  if (!match) return "";
  return match[0].replace(/^https:\/\/www\.threads\.com\//i, "https://www.threads.net/");
}

function extractUniqueThreadsPostUrlsFromProfileLinks(links: string[], username: string): string[] {
  const normalizedUsername = String(username || "").replace(/^@+/, "").toLowerCase();
  const out: string[] = [];
  const seen = new Set<string>();
  for (const link of links || []) {
    const normalized = normalizeThreadsPostUrl(link);
    if (!normalized) continue;
    if (!new RegExp(`/@${normalizedUsername.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/post/`, "i").test(normalized)) continue;
    const code = normalized.match(/\/post\/([^/?#\s]+)/i)?.[1] || normalized;
    if (seen.has(code)) continue;
    seen.add(code);
    out.push(normalized);
  }
  return out;
}

function parseThreadsProfileMetricLines(lines: string[]): NonNullable<SentimentHotCandidate["engagement"]> {
  const numbers = lines
    .flatMap((line) => {
      const matches = Array.from(String(line || "").matchAll(/\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?/g));
      if (!matches.length) return [parseMetricNumberLoose(line)];
      return matches.map((match) => parseMetricNumberLoose(match[0]));
    })
    .filter((item): item is number => typeof item === "number" && item > 0)
    .slice(0, 8);
  const engagement: NonNullable<SentimentHotCandidate["engagement"]> = {};
  if (typeof numbers[0] === "number") engagement.likeCount = numbers[0];
  if (typeof numbers[1] === "number") engagement.commentCount = numbers[1];
  if (typeof numbers[2] === "number") engagement.shareCount = numbers[2];
  if (numbers.length) engagement.rawSignals = numbers;
  return engagement;
}

function buildThreadsBrowserProfileSnapshot(args: {
  sourceUrl: string;
  engagement: NonNullable<SentimentHotCandidate["engagement"]>;
  metrics?: Record<string, unknown>;
}): ThreadsBrowserProfilePublishedPostSnapshot {
  const engagement: NonNullable<SentimentHotCandidate["engagement"]> = { ...(args.engagement || {}) };
  if (typeof engagement.likeCount !== "number") engagement.likeCount = 0;
  if (typeof engagement.commentCount !== "number") engagement.commentCount = 0;
  if (typeof engagement.shareCount !== "number") engagement.shareCount = 0;
  const rawSignals = engagement.rawSignals || [];
  const sendCount = rawSignals[3];
  const hotScore = Math.max(
    engagement.likeCount || 0,
    engagement.commentCount || 0,
    engagement.shareCount || 0,
    typeof sendCount === "number" ? sendCount : 0,
  );
  return {
    sourceUrl: args.sourceUrl,
    hotScore,
    engagement,
    metrics: {
      ...compactEngagementMetrics(engagement),
      ...(args.metrics || {}),
      repost_count: engagement.shareCount,
      send_count: sendCount,
    },
    capturedAt: new Date().toISOString(),
  };
}

export function parseThreadsBrowserProfilePublishedPosts(args: {
  username: string;
  text: string;
  links: string[];
}): Array<ThreadsBrowserProfilePublishedPostSnapshot & { content: string }> {
  const username = String(args.username || "").replace(/^@+/, "").trim();
  if (!username) return [];
  const postUrls = extractUniqueThreadsPostUrlsFromProfileLinks(args.links || [], username);
  const lines = String(args.text || "")
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean);
  const out: Array<ThreadsBrowserProfilePublishedPostSnapshot & { content: string }> = [];
  let postIndex = 0;
  for (let index = 0; index < lines.length; index += 1) {
    if (lines[index].replace(/^@+/, "").toLowerCase() !== username.toLowerCase()) continue;
    const timeLine = lines[index + 1] || "";
    if (!isThreadsProfilePostTimeLine(timeLine)) continue;
    const contentLines: string[] = [];
    const metricLines: string[] = [];
    let cursor = index + 2;
    for (; cursor < lines.length; cursor += 1) {
      const line = lines[cursor];
      const next = lines[cursor + 1] || "";
      if (line.replace(/^@+/, "").toLowerCase() === username.toLowerCase() && isThreadsProfilePostTimeLine(next)) break;
      if (/^(翻譯|翻译|translate|translation)$/i.test(line)) continue;
      const metric = parseMetricNumberLoose(line);
      if (typeof metric === "number" && metric > 0 && contentLines.length > 0) {
        metricLines.push(line);
        continue;
      }
      if (!metricLines.length) contentLines.push(line);
    }
    const content = contentLines.join("\n").trim();
    const sourceUrl = postUrls[postIndex++] || "";
    if (content && sourceUrl) {
      out.push({
        content,
        ...buildThreadsBrowserProfileSnapshot({
          sourceUrl,
          engagement: parseThreadsProfileMetricLines(metricLines),
        }),
      });
    }
    index = Math.max(index, cursor - 1);
  }
  return out;
}

export function matchThreadsBrowserProfilePublishedPost(args: {
  username: string;
  text: string;
  links: string[];
  content: string;
}): ThreadsBrowserProfilePublishedPostSnapshot | null {
  const target = normalizeThreadsPublishedHistoryText(args.content);
  if (!target) return null;
  const targetHead = target.slice(0, Math.min(24, target.length));
  const posts = parseThreadsBrowserProfilePublishedPosts({
    username: args.username,
    text: args.text,
    links: args.links,
  });
  let best: (ThreadsBrowserProfilePublishedPostSnapshot & { content: string }) | null = null;
  let bestScore = 0;
  for (const post of posts) {
    const current = normalizeThreadsPublishedHistoryText(post.content);
    if (!current) continue;
    let score = 0;
    if (targetHead && current.includes(targetHead)) score += 100;
    if (current && target.includes(current.slice(0, Math.min(18, current.length)))) score += 60;
    for (let len = Math.min(30, target.length, current.length); len >= 8; len -= 2) {
      if (current.includes(target.slice(0, len)) || target.includes(current.slice(0, len))) {
        score += len;
        break;
      }
    }
    if (score > bestScore) {
      bestScore = score;
      best = post;
    }
  }
  if (!best || bestScore < 18) return null;
  const { content: _content, ...snapshot } = best;
  return snapshot;
}

async function readThreadsBrowserProfileMatchFromPage(args: {
  page: any;
  username: string;
  content: string;
}): Promise<ThreadsBrowserProfilePublishedPostSnapshot | null> {
  const profileText = await args.page.locator("body").innerText({ timeout: 10_000 }).catch(() => "");
  const links = await args.page.$$eval("a[href]", (items: any[]) => items
    .map((item: any) => item.href || item.getAttribute?.("href") || "")
    .filter(Boolean)).catch(() => []);
  return matchThreadsBrowserProfilePublishedPost({
    username: args.username,
    content: args.content,
    text: profileText,
    links,
  });
}

async function lookupThreadsPublishedPostFromBrowserSearchPage(args: {
  page: any;
  username: string;
  content: string;
}): Promise<ThreadsBrowserProfilePublishedPostSnapshot | null> {
  const username = String(args.username || "").replace(/^@+/, "").trim();
  const content = String(args.content || "").trim();
  if (!username || !content) return null;
  const queries = Array.from(new Set([
    content.replace(/\s+/g, " ").slice(0, 72),
    content.replace(/\s+/g, "").slice(0, 48),
    `${username} ${content.replace(/\s+/g, " ").slice(0, 48)}`,
  ].map((item) => item.trim()).filter((item) => item.length >= 6)));
  for (const query of queries.slice(0, 4)) {
    await args.page.goto(`https://www.threads.net/search?q=${encodeURIComponent(query)}`, {
      waitUntil: "domcontentloaded",
      timeout: 35_000,
    }).catch(() => null);
    await args.page.waitForTimeout(4500);
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const matched = await readThreadsBrowserProfileMatchFromPage({
        page: args.page,
        username,
        content,
      });
      if (matched) return matched;
      await args.page.mouse.wheel(0, 1800).catch(() => null);
      await args.page.waitForTimeout(1200);
    }
  }
  return null;
}

function parseThreadsActionMetricText(value: unknown, labelPattern: RegExp): number | undefined {
  const text = String(value || "").replace(/\s+/g, "").trim();
  if (!labelPattern.test(text)) return undefined;
  const withoutLabel = text.replace(labelPattern, "");
  const count = parseMetricNumberLoose(withoutLabel);
  return typeof count === "number" ? count : 0;
}

function findThreadsActionMetricSequence(actionTexts: string[]) {
  const normalized = (actionTexts || []).map((item) => String(item || "").replace(/\s+/g, "").trim()).filter(Boolean);
  for (let index = 0; index <= normalized.length - 4; index += 1) {
    const like = parseThreadsActionMetricText(normalized[index], /^(?:Like|Likes|讚|赞|喜歡|喜欢)/i);
    const comment = parseThreadsActionMetricText(normalized[index + 1], /^(?:Comment|Comments|Reply|Replies|留言|回覆|回复|評論|评论)/i);
    const repost = parseThreadsActionMetricText(normalized[index + 2], /^(?:Repost|Reposts|轉發|转发)/i);
    const send = parseThreadsActionMetricText(normalized[index + 3], /^(?:Share|Shares|分享|傳送|发送|傳送給|发送给)/i);
    if ([like, comment, repost, send].every((item) => typeof item === "number")) {
      return { likeCount: like, commentCount: comment, repostCount: repost, sendCount: send };
    }
  }
  return null;
}

export function parseThreadsBrowserPostDetailMetrics(args: {
  text: string;
  actionTexts: string[];
}): Pick<ThreadsBrowserProfilePublishedPostSnapshot, "hotScore" | "engagement" | "metrics"> | null {
  const sequence = findThreadsActionMetricSequence(args.actionTexts || []);
  if (!sequence) return null;
  const text = String(args.text || "");
  const viewCount = parseMetricNumberLoose(
    text.match(/Thread\s+(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s+views/i)?.[1]
    || text.match(/串文\s*(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*次瀏覽/i)?.[1]
    || text.match(/(\d+(?:[.,]\d+)?\s*(?:[KkMm\u842c\u4e07])?)\s*次瀏覽/i)?.[1],
  );
  const rawSignals = [sequence.likeCount, sequence.commentCount, sequence.repostCount, sequence.sendCount]
    .filter((item): item is number => typeof item === "number" && Number.isFinite(item) && item > 0);
  const engagement: NonNullable<SentimentHotCandidate["engagement"]> = {
    likeCount: sequence.likeCount,
    commentCount: sequence.commentCount,
    shareCount: sequence.repostCount,
  };
  if (typeof viewCount === "number") engagement.viewCount = viewCount;
  if (rawSignals.length) engagement.rawSignals = rawSignals;
  const interactionHotScore = Math.max(sequence.likeCount, sequence.commentCount, sequence.repostCount, sequence.sendCount);
  const hotScore = typeof viewCount === "number" ? viewCount : interactionHotScore;
  return {
    hotScore,
    engagement,
    metrics: {
      ...compactEngagementMetrics(engagement),
      repost_count: sequence.repostCount,
      send_count: sequence.sendCount,
      ...(typeof viewCount === "number" ? { view_count: viewCount } : {}),
    },
  };
}

async function fetchThreadsBrowserDetailMetrics(sourceUrl: string): Promise<Pick<ThreadsBrowserProfilePublishedPostSnapshot, "hotScore" | "engagement" | "metrics"> | null> {
  if (process.env.VITEST_WORKER_ID) return null;
  const normalizedSourceUrl = String(sourceUrl || "").replace(/^https:\/\/www\.threads\.com\//i, "https://www.threads.net/");
  if (!/^https:\/\/www\.threads\.net\/@[^/]+\/post\//i.test(normalizedSourceUrl)) return null;
  const cookies = readSentimentBrowserAuthCookies("threads");
  if (!cookies.length) return null;
  let browser: any = null;
  try {
    const playwright = await import("playwright");
    browser = await playwright.chromium.launch(buildLocalChromiumLaunchOptions());
    const context = await browser.newContext({
      viewport: { width: 900, height: 1400 },
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    });
    await context.addCookies(cookies as any);
    const page = await context.newPage();
    await page.goto(normalizedSourceUrl, {
      waitUntil: "domcontentloaded",
      timeout: 35_000,
    }).catch(() => null);
    await page.waitForTimeout(6500);
    const detailText = await page.locator("body").innerText({ timeout: 10_000 }).catch(() => "");
    const actionTexts = await page.$$eval("[role=button],button", (items) => items
      .map((item) => (item.textContent || "").trim())
      .filter(Boolean)).catch(() => []);
    return parseThreadsBrowserPostDetailMetrics({ text: detailText, actionTexts });
  } catch {
    return null;
  } finally {
    await browser?.close?.().catch?.(() => null);
  }
}

export async function lookupThreadsPublishedPostFromBrowserProfile(args: {
  username: string;
  content: string;
}): Promise<ThreadsBrowserProfilePublishedPostSnapshot | null> {
  const username = String(args.username || "").replace(/^@+/, "").trim();
  const content = String(args.content || "").trim();
  if (!username || !content) return null;
  const cookies = readSentimentBrowserAuthCookies("threads");
  if (!cookies.length) return null;
  let browser: any = null;
  try {
    const playwright = await import("playwright");
    browser = await playwright.chromium.launch(buildLocalChromiumLaunchOptions());
    const context = await browser.newContext({
      viewport: { width: 900, height: 1400 },
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    });
    await context.addCookies(cookies as any);
    const page = await context.newPage();
    await page.goto(`https://www.threads.net/@${encodeURIComponent(username)}`, {
      waitUntil: "domcontentloaded",
      timeout: 35_000,
    }).catch(() => null);
    await page.waitForTimeout(4500);
    let matched = await readThreadsBrowserProfileMatchFromPage({ page, username, content });
    for (let attempt = 0; !matched && attempt < 8; attempt += 1) {
      await page.mouse.wheel(0, 2200).catch(() => null);
      await page.waitForTimeout(1400);
      matched = await readThreadsBrowserProfileMatchFromPage({ page, username, content });
    }
    if (!matched) {
      matched = await lookupThreadsPublishedPostFromBrowserSearchPage({ page, username, content });
    }
    if (!matched) return null;
    await page.goto(matched.sourceUrl, {
      waitUntil: "domcontentloaded",
      timeout: 35_000,
    }).catch(() => null);
    await page.waitForTimeout(6500);
    const detailText = await page.locator("body").innerText({ timeout: 10_000 }).catch(() => "");
    const actionTexts = await page.$$eval("[role=button],button", (items) => items
      .map((item) => (item.textContent || "").trim())
      .filter(Boolean)).catch(() => []);
    const detailMetrics = parseThreadsBrowserPostDetailMetrics({ text: detailText, actionTexts });
    if (!detailMetrics) return matched;
    return {
      ...matched,
      hotScore: detailMetrics.hotScore,
      engagement: detailMetrics.engagement,
      metrics: {
        ...(matched.metrics || {}),
        ...(detailMetrics.metrics || {}),
      },
      capturedAt: new Date().toISOString(),
    };
  } catch {
    return null;
  } finally {
    await browser?.close?.().catch?.(() => null);
  }
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
  const normalizedSourceUrl = String(sourceUrl || "").replace(/^https:\/\/www\.threads\.com\//i, "https://www.threads.net/");
  if (!/^https:\/\/www\.threads\.net\/@[^/]+\/post\//i.test(normalizedSourceUrl)) return { engagement: {}, media: [] };
  try {
    const cacheBuster = `__r=${Date.now().toString(36)}`;
    const readerTargetUrl = `${normalizedSourceUrl}${normalizedSourceUrl.includes("?") ? "&" : "?"}${cacheBuster}`;
    const response = await fetch(`${JINA_READER_PREFIX}${readerTargetUrl}`, {
      headers: {
        "user-agent": "Mozilla/5.0",
        accept: "text/plain, text/markdown, */*",
        "cache-control": "no-cache",
        pragma: "no-cache",
      },
      signal: buildAbortSignalTimeout(12_000),
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

export async function refreshSentimentSourceMetrics(args: {
  platform?: string;
  sourceUrl: string;
  existingEngagement?: SentimentHotCandidate["engagement"];
  existingMedia?: SentimentHotMedia[];
  existingHotScore?: number;
}): Promise<{
  ok: boolean;
  message: string;
  hotScore?: number;
  metrics?: Record<string, unknown>;
  engagement?: NonNullable<SentimentHotCandidate["engagement"]>;
  media?: SentimentHotMedia[];
}> {
  const platform = String(args.platform || "").toLowerCase();
  const sourceUrl = String(args.sourceUrl || "").trim();
  if (!sourceUrl) return { ok: false, message: "缺少原帖链接，无法刷新热度。" };
  if (platform && platform !== "threads") {
    return { ok: false, message: "目前仅支持 Threads 原帖实时刷新热度。" };
  }
  const detail = await fetchThreadsDetailData(sourceUrl);
  const browserDetail = await fetchThreadsBrowserDetailMetrics(sourceUrl);
  const latestEngagement = browserDetail?.engagement || detail.engagement;
  const hasMetrics = hasNamedEngagementMetrics(latestEngagement);
  if (!hasMetrics && !detail.media.length) {
    return { ok: false, message: "暂时没有从原帖读取到新的热度数据，请稍后重试。" };
  }
  const engagement = refreshEngagementMetrics(args.existingEngagement || {}, latestEngagement);
  const media = mergeCandidateMedia(args.existingMedia || [], detail.media);
  const refreshedHotScore = Math.max(
    engagement.viewCount || 0,
    engagement.likeCount || 0,
    engagement.commentCount || 0,
    engagement.shareCount || 0,
  );
  const hotScore = typeof browserDetail?.hotScore === "number"
    ? browserDetail.hotScore
    : refreshedHotScore > 0
      ? refreshedHotScore
      : Number(args.existingHotScore || 0);
  return {
    ok: true,
    message: "已刷新原帖热度。",
    hotScore,
    engagement,
    media,
    metrics: {
      mediaCount: media.length,
      like_count: engagement.likeCount || 0,
      comment_count: engagement.commentCount || 0,
      share_count: engagement.shareCount || 0,
      repost_count: engagement.shareCount || 0,
      send_count: Number((browserDetail?.metrics as any)?.send_count || 0),
      ...(browserDetail?.metrics || {}),
      ...compactEngagementMetrics(engagement),
    },
  };
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
    if (!hasNamedEngagementMetrics(detail.engagement) && !detail.media.length) return;
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

function normalizeSentimentPublishedAt(value: unknown): string | undefined {
  const text = cleanText(value);
  if (!text) return undefined;
  const parsed = Date.parse(text);
  if (Number.isFinite(parsed)) return new Date(parsed).toISOString();
  const slash = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/);
  if (slash) {
    const month = Number(slash[1]);
    const day = Number(slash[2]);
    const yearRaw = Number(slash[3]);
    const year = yearRaw < 100 ? 2000 + yearRaw : yearRaw;
    if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
      return new Date(Date.UTC(year, month - 1, day)).toISOString();
    }
  }
  return undefined;
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
    const publishedAt = normalizeSentimentPublishedAt(match[1]);
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
      publishedAt,
      capturedAt: new Date().toISOString(),
      warnings: [],
    });
    if (out.length >= (args.limit || 10)) break;
  }
  return out;
}

export function parseInstagramReaderSearchMarkdownCandidates(args: {
  text: string;
  query: string;
  keywords?: string[];
  limit?: number;
  sourceUrl: string;
}): SentimentHotCandidate[] {
  const text = String(args.text || "");
  if (!text || !/Instagram/i.test(text)) return [];
  const needleSource = args.keywords?.length ? args.keywords : [args.query];
  const needles = buildRelevanceNeedles(needleSource);
  const out: SentimentHotCandidate[] = [];
  const postMatches = [...text.matchAll(/https:\/\/www\.instagram\.com\/(?:p|reel|tv)\/[A-Za-z0-9_-]+\/?/g)];
  const seenUrls = new Set<string>();
  for (const match of postMatches) {
    const sourceUrl = match[0].replace(/[)\].,]+$/g, "");
    if (seenUrls.has(sourceUrl)) continue;
    seenUrls.add(sourceUrl);
    const matchIndex = match.index || 0;
    const block = text.slice(Math.max(0, matchIndex - 900), Math.min(text.length, matchIndex + 1400));
    const authorMatches = [...block.matchAll(/\[([^\]\n@][^\]\n]{1,80})]\(https:\/\/www\.instagram\.com\/([^/)#?]+)\/?\)/g)]
      .filter((item) => isLikelyInstagramAuthor(item[2]));
    const author = cleanText(authorMatches.at(-1)?.[1] || authorMatches.at(-1)?.[2] || "Instagram");
    const content = cleanThreadsReaderContent(block
      .replace(/https:\/\/www\.instagram\.com\/(?:p|reel|tv)\/[A-Za-z0-9_-]+\/?/g, " ")
      .replace(/(?:Log in|Sign up|Explore|Search|Instagram)\s*/gi, " "));
    if (isLowQualitySentimentContent(content)) continue;
    if (!isChineseSentimentCandidate(content)) continue;
    if ((content.match(/[\u3400-\u9fff]/gu) || []).length < 12) continue;
    const haystack = [content, author].join(" ").toLowerCase();
    const matchedNeedles = needles.filter((needle) => haystack.includes(needle.toLowerCase()));
    if (needles.length && matchedNeedles.length === 0) continue;
    const engagement = mergeEngagementMetrics(
      extractInstagramEngagementMetricsFromText(block),
      extractEngagementMetricsFromText(block),
    );
    const media = extractThreadsMediaFromMarkdown(block, 12);
    const id = buildSentimentCandidateId({ platform: "instagram", sourceUrl, content });
    out.push({
      id,
      platform: "instagram",
      sourceUrl,
      author,
      content,
      media,
      hotScore: parseThreadsReaderHotScore(block) + matchedNeedles.length * 30,
      metrics: {
        source: "instagram-reader-search",
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

function isLikelyInstagramAuthor(value: string) {
  const text = cleanText(value).replace(/^@/, "");
  return Boolean(text && /^[A-Za-z0-9._]{2,30}$/.test(text) && !/^(?:p|reel|tv|explore|accounts|direct|stories|about|developer|legal|privacy)$/i.test(text));
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
  try {
    const profile = readSentimentBrowserAuthProfilesConfig().find((item: any) => sentimentProfileMatchesPlatform(item, platform));
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
    const mirrored = cookies.flatMap((cookie: any) => [
      { ...cookie, domain: ".threads.net" },
      { ...cookie, domain: ".threads.com" },
    ]);
    return activeUniqueCookies([...cookies, ...mirrored]);
  } catch {
    return [];
  }
}

function buildThreadsSearchQueries(keywords: string[]): string[] {
  const out: string[] = [];
  const add = (value: string) => {
    const text = cleanText(value);
    if (!text) return;
    if (!hasHan(text) && !/^AI$/i.test(text)) return;
    if (text.length > 14) return;
    out.push(text);
  };
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
        publishedAt: normalizeSentimentPublishedAt(row.published_at),
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

export async function downloadCandidateMedia(candidate: SentimentHotCandidate, limit = Number.POSITIVE_INFINITY): Promise<SentimentHotMedia[]> {
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
