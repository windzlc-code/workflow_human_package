const DEFAULT_API_BASE = "http://47.250.188.76";

const PROFILES = [
  {
    key: "youtube",
    sourceKey: "youtube",
    domain: "youtube.com",
    authUrl: "https://www.youtube.com/",
  },
  {
    key: "reddit",
    sourceKey: "reddit",
    domain: "reddit.com",
    authUrl: "https://www.reddit.com/",
  },
  {
    key: "dcard",
    sourceKey: "dcard",
    domain: "dcard.tw",
    authUrl: "https://www.dcard.tw/",
  },
  {
    key: "threads",
    sourceKey: "threads",
    domain: "threads.net",
    cookieDomains: ["threads.net", "instagram.com", "facebook.com"],
    matchDomains: ["threads.net", "instagram.com", "facebook.com"],
    authUrl: "https://www.threads.net/",
    authUrls: ["https://www.threads.net/", "https://www.instagram.com/accounts/login/"],
  },
  {
    key: "xSearch",
    sourceKey: "xSearch",
    domain: "x.com",
    authUrl: "https://x.com/",
  },
  {
    key: "nownews",
    label: "NOWnews 授权浏览器搜索",
    aliases: ["NOWnews", "NOWnews今日新聞"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "nownews.com",
    authUrl: "https://www.nownews.com/",
  },
  {
    key: "chinatimes",
    label: "中時新聞網 授权浏览器搜索",
    aliases: ["中時新聞網"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "chinatimes.com",
    authUrl: "https://www.chinatimes.com/",
  },
  {
    key: "storm",
    label: "風傳媒 授权浏览器搜索",
    aliases: ["風傳媒"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "storm.mg",
    authUrl: "https://www.storm.mg/",
  },
  {
    key: "upmedia",
    label: "上報 授权浏览器搜索",
    aliases: ["上報"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "upmedia.mg",
    authUrl: "https://www.upmedia.mg/",
  },
  {
    key: "businessweekly",
    label: "商業周刊 授权浏览器搜索",
    aliases: ["商業周刊"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "businessweekly.com.tw",
    authUrl: "https://www.businessweekly.com.tw/",
  },
  {
    key: "cw",
    label: "天下雜誌 授权浏览器搜索",
    aliases: ["天下雜誌"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "cw.com.tw",
    authUrl: "https://www.cw.com.tw/",
  },
  {
    key: "businesstoday",
    label: "今周刊 授权浏览器搜索",
    aliases: ["今周刊"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "businesstoday.com.tw",
    authUrl: "https://www.businesstoday.com.tw/",
  },
  {
    key: "ettoday",
    label: "ETtoday 授权浏览器搜索",
    aliases: ["ETtoday", "ETtoday新聞雲"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "ettoday.net",
    authUrl: "https://www.ettoday.net/",
  },
  {
    key: "udn",
    label: "聯合新聞網 授权浏览器搜索",
    aliases: ["聯合新聞網"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "udn.com",
    authUrl: "https://udn.com/",
  },
  {
    key: "ltn",
    label: "自由時報 授权浏览器搜索",
    aliases: ["自由時報", "自由時報電子報"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "ltn.com.tw",
    authUrl: "https://news.ltn.com.tw/",
  },
  {
    key: "mirrormedia",
    label: "鏡週刊 授权浏览器搜索",
    aliases: ["鏡週刊"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "mirrormedia.mg",
    authUrl: "https://www.mirrormedia.mg/",
  },
  {
    key: "thenewslens",
    label: "關鍵評論網 授权浏览器搜索",
    aliases: ["關鍵評論網"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "thenewslens.com",
    authUrl: "https://www.thenewslens.com/",
  },
  {
    key: "yahooNewsTaiwan",
    label: "Yahoo奇摩新聞 授权浏览器搜索",
    aliases: ["Yahoo奇摩新聞"],
    sourceKey: "yahooTaiwan",
    sourceKeys: ["taiwanNews", "yahooTaiwan", "rssFeeds"],
    domain: "tw.news.yahoo.com",
    authUrl: "https://tw.news.yahoo.com/",
  },
  {
    key: "wealth",
    label: "財訊 授权浏览器搜索",
    aliases: ["財訊"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "wealth.com.tw",
    authUrl: "https://www.wealth.com.tw/",
  },
  {
    key: "moneydj",
    label: "MoneyDJ 授权浏览器搜索",
    aliases: ["MoneyDJ", "MoneyDJ理財網"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "moneydj.com",
    authUrl: "https://www.moneydj.com/",
  },
  {
    key: "cnyes",
    label: "鉅亨網 授权浏览器搜索",
    aliases: ["鉅亨網"],
    sourceKey: "taiwanNews",
    sourceKeys: ["taiwanNews", "rssFeeds"],
    domain: "cnyes.com",
    authUrl: "https://news.cnyes.com/",
  },
];

function storageGet(keys) {
  return chrome.storage.local.get(keys);
}

function storageSet(values) {
  return chrome.storage.local.set(values);
}

async function apiBase() {
  const values = await storageGet(["apiBase"]);
  return String(values.apiBase || DEFAULT_API_BASE).replace(/\/+$/, "");
}

function profileForUrl(url = "") {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return PROFILES.find(profile => {
      const domains = [profile.domain, ...(profile.matchDomains || []), ...(profile.cookieDomains || [])]
        .map(domain => String(domain || "").replace(/^\.+/, "").replace(/^www\./, ""))
        .filter(Boolean);
      return [...new Set(domains)].some(domain => host === domain || host.endsWith(`.${domain}`));
    });
  } catch {
    return null;
  }
}

function cookieUrlForProfile(profile) {
  return `https://${profile.domain}/`;
}

function cookieKey(cookie = {}) {
  return [
    cookie.storeId || "",
    cookie.partitionKey ? JSON.stringify(cookie.partitionKey) : "",
    cookie.domain || "",
    cookie.path || "",
    cookie.name || "",
  ].join("|");
}

async function getCookiesForDomain(domain = "") {
  const normalized = String(domain || "").replace(/^\.+/, "").replace(/^www\./, "");
  if (!normalized) return [];
  const results = await Promise.allSettled([
    chrome.cookies.getAll({ domain: normalized }),
    chrome.cookies.getAll({ url: `https://${normalized}/` }),
    chrome.cookies.getAll({ url: `https://www.${normalized}/` }),
  ]);
  const seen = new Set();
  return results
    .filter(result => result.status === "fulfilled")
    .flatMap(result => result.value || [])
    .filter(cookie => {
      const key = cookieKey(cookie);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

async function syncProfileCookies(profile) {
  const domains = [profile.domain, ...(profile.cookieDomains || [])]
    .map(domain => String(domain || "").replace(/^\.+/, "").replace(/^www\./, ""))
    .filter(Boolean);
  const uniqueDomains = [...new Set(domains)];
  const cookieGroups = await Promise.all(uniqueDomains.map(async domain => ({
    domain,
    cookies: await getCookiesForDomain(domain),
  })));
  const cookies = cookieGroups.flatMap(group => group.cookies);
  const domainSummary = cookieGroups.map(group => `${group.domain} ${group.cookies.length}`).join("，");
  const usefulCookies = cookies
    .filter(cookie => cookie.name && cookie.value)
    .map(cookie => ({
      name: cookie.name,
      value: cookie.value,
      domain: cookie.domain || `.${profile.domain}`,
      path: cookie.path || "/",
      httpOnly: Boolean(cookie.httpOnly),
      secure: cookie.secure !== false,
      sameSite: cookie.sameSite === "strict" ? "Strict" : cookie.sameSite === "no_restriction" ? "None" : "Lax",
      expires: cookie.expirationDate,
    }));
  if (!usefulCookies.length) {
    await storageSet({ lastStatus: `${profile.key}: 未读取到 Cookie（${domainSummary}），请先登录或检查扩展站点权限` });
    return { ok: false, savedCookieCount: 0 };
  }
  const base = await apiBase();
  const response = await fetch(`${base}/api/sentiment/browser-auth/cookies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      profileKey: profile.key,
      sourceKey: profile.sourceKey,
      domain: profile.domain,
      cookies: usefulCookies,
    }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.ok) {
    throw new Error(result.error || `同步失败：${response.status}`);
  }
  await storageSet({
    lastStatus: `${profile.key}: 已同步 ${result.savedCookieCount || usefulCookies.length} 个 Cookie（${domainSummary}）`,
    [`lastSync:${profile.key}`]: new Date().toISOString(),
  });
  return result;
}

async function openAuthorizationPages() {
  for (const profile of PROFILES) {
    const urls = Array.isArray(profile.authUrls) && profile.authUrls.length ? profile.authUrls : [profile.authUrl];
    for (const url of urls.filter(Boolean)) {
      await chrome.tabs.create({ url, active: false });
    }
  }
  await storageSet({ lastStatus: "已打开授权页面，登录完成后扩展会自动同步 Cookie" });
}

chrome.runtime.onInstalled.addListener(async () => {
  await storageSet({ apiBase: DEFAULT_API_BASE, profiles: PROFILES, lastStatus: "授权助手已安装" });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab?.url) return;
  const profile = profileForUrl(tab.url);
  if (!profile) return;
  syncProfileCookies(profile).catch(async (error) => {
    await storageSet({ lastStatus: `${profile.key}: ${error.message}` });
  });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === "open-auth-pages") {
      await openAuthorizationPages();
      sendResponse({ ok: true });
      return;
    }
    if (message?.type === "sync-current-tab") {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const profile = profileForUrl(tab?.url || "");
      if (!profile) {
        sendResponse({ ok: false, error: "当前标签页不是受支持的授权站点" });
        return;
      }
      const result = await syncProfileCookies(profile);
      sendResponse({ ok: true, result });
      return;
    }
    if (message?.type === "set-api-base") {
      const base = String(message.apiBase || DEFAULT_API_BASE).replace(/\/+$/, "");
      await storageSet({ apiBase: base });
      sendResponse({ ok: true, apiBase: base });
      return;
    }
    sendResponse({ ok: false, error: "unknown message" });
  })().catch(error => sendResponse({ ok: false, error: error.message }));
  return true;
});
