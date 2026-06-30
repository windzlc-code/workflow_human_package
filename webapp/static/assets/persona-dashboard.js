function pdEl(id) {
  return document.getElementById(id);
}

async function pdApi(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  let body = opts.body;
  if (body && typeof body !== "string") {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const res = await fetch(path, { cache: "no-store", ...opts, headers, body });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text || `接口状态 ${res.status}` };
  }
  if (!res.ok) throw data || { detail: `接口状态 ${res.status}` };
  return data;
}

function pdEscape(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch] || ch));
}

let personaDashboardData = null;
let personaDashboardSelectedId = "__overview__";
let personaDashboardPostPage = 1;
let personaDashboardPageSize = Number(localStorage.getItem("personaDashboardPageSize") || 10) || 10;
let personaDashboardRefreshTask = "";
let personaDashboardAccountPlatform = localStorage.getItem("personaDashboardAccountPlatform") || "threads";
let personaDashboardTabPage = 1;
let personaDashboardPostModalKey = "";

const PD_LABELS = {
  likes: "点赞",
  comments: "评论",
  shares: "分享",
  reposts: "转发",
  complete: "完整数据",
  partial_or_unknown: "部分/未知",
  none: "暂无数据",
  queued: "排队中",
  running: "运行中",
  done: "已完成",
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
  pending: "待处理",
  unknown: "未知",
};

function pdLabel(value) {
  const key = String(value || "").trim();
  return PD_LABELS[key] || key || "-";
}

function pdNumber(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0";
  if (Math.abs(n) >= 100000000) return `${(n / 100000000).toFixed(1)}亿`;
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(Math.round(n));
}

function pdDate(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const date = new Date(text);
  if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  return text;
}

function pdEntries(value) {
  return Object.entries(value || {})
    .map(([label, count]) => ({ label: pdLabel(label), value: Number(count || 0) }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value);
}

function pdRangeDays() {
  const range = String((pdEl("personaDashboardRange") && pdEl("personaDashboardRange").value) || "all").trim();
  const days = Number(range || 0);
  return Number.isFinite(days) && days > 0 ? days : 0;
}

function pdDateInRange(value) {
  const days = pdRangeDays();
  if (!days) return true;
  const ts = new Date(value || 0).getTime();
  if (!Number.isFinite(ts)) return false;
  return ts >= Date.now() - days * 24 * 60 * 60 * 1000;
}

function pdPlatformFilter() {
  return String((pdEl("personaDashboardPlatform") && pdEl("personaDashboardPlatform").value) || "").trim().toLowerCase();
}

function pdFilterTrend(rows) {
  return (rows || []).filter((row) => pdDateInRange(row.date));
}

function pdFilteredPostRows(persona) {
  const platform = pdPlatformFilter();
  return (persona.post_metrics || []).filter((row) => {
    if (platform && String(row.platform || "").toLowerCase() !== platform) return false;
    return pdDateInRange(row.published_at || row.captured_at);
  });
}

function pdPersonaHot(persona) {
  const platform = pdPlatformFilter();
  const base = persona.hot || {};
  if (!platform) return base;
  const rows = (persona.hot_platforms || []).filter((item) => String(item.platform || "").toLowerCase() === platform);
  if (!rows.length) return {
    likes: 0,
    comments: 0,
    shares: 0,
    reposts: 0,
    recent_views: 0,
    post_views: 0,
    hot_score: 0,
  };
  return rows.reduce((sum, row) => {
    sum.likes += Number(row.likes || 0);
    sum.comments += Number(row.comments || 0);
    sum.shares += Number(row.shares || 0);
    sum.reposts += Number(row.reposts || 0);
    sum.recent_views += Number(row.recent_views || 0);
    sum.post_views += Number(row.post_views || 0);
    sum.hot_score += Number(row.likes || 0) + Number(row.comments || 0) + Number(row.shares || 0) + Number(row.reposts || 0) + Number(row.post_views || 0);
    return sum;
  }, { likes: 0, comments: 0, shares: 0, reposts: 0, recent_views: 0, post_views: 0, hot_score: 0 });
}

function pdVisibleSummary(visiblePersonas) {
  const padSet = new Set();
  const summary = {
    persona_count: visiblePersonas.length,
    post_count: 0,
    published_count: 0,
    image_count: 0,
    bound_pad_count: 0,
    total_interactions: 0,
    recent_views: 0,
    post_views: 0,
    hot_score: 0,
  };
  visiblePersonas.forEach((persona) => {
    const counts = persona.counts || {};
    const hot = pdPersonaHot(persona);
    summary.post_count += Number(counts.posts || 0);
    summary.published_count += Number(counts.published || 0);
    summary.image_count += Number(counts.images || 0);
    summary.recent_views += Number(hot.recent_views || 0);
    summary.post_views += Number(hot.post_views || 0);
    summary.hot_score += Number(hot.hot_score || 0);
    summary.total_interactions += Number(hot.likes || 0) + Number(hot.comments || 0) + Number(hot.shares || 0) + Number(hot.reposts || 0);
    if (persona.bound_pad_code) padSet.add(String(persona.bound_pad_code));
  });
  summary.bound_pad_count = padSet.size;
  return summary;
}

function pdBuildFilteredCharts(visiblePersonas, data) {
  const platformDistribution = {};
  const engagement = { likes: 0, comments: 0, shares: 0, reposts: 0 };
  const taskStatus = {};
  const coverage = { complete: 0, partial_or_unknown: 0, none: 0 };

  visiblePersonas.forEach((persona) => {
    const hot = pdPersonaHot(persona);
    Object.keys(engagement).forEach((key) => { engagement[key] += Number(hot[key] || 0); });
    (persona.hot_platforms || []).forEach((item) => {
      const platform = String(item.platform || "").trim();
      if (platform) platformDistribution[platform] = (platformDistribution[platform] || 0) + 1;
    });
    Object.keys((persona.counts && persona.counts.platform_posts) || {}).forEach((platform) => {
      const count = Number(persona.counts.platform_posts[platform] || 0);
      if (count > 0) platformDistribution[platform] = (platformDistribution[platform] || 0) + count;
    });
    const platforms = persona.hot_platforms || [];
    if (!platforms.length) coverage.none += 1;
    else if (platforms.some((item) => item.complete)) coverage.complete += 1;
    else coverage.partial_or_unknown += 1;
    Object.entries((persona.queue && persona.queue.by_status) || {}).forEach(([status, count]) => {
      taskStatus[status] = (taskStatus[status] || 0) + Number(count || 0);
    });
  });

  return {
    platform_distribution: platformDistribution,
    engagement_mix: engagement,
    task_status_distribution: taskStatus,
    hot_coverage: coverage,
    trend: pdFilterTrend(data.charts && data.charts.trend),
  };
}

function pdRenderBarChart(hostId, rows) {
  const host = pdEl(hostId);
  if (!host) return;
  const items = (rows || []).filter((row) => Number(row.value || 0) > 0).slice(0, 12);
  if (!items.length) {
    host.innerHTML = `<div class="persona-chart-empty">暂无可展示数据</div>`;
    return;
  }
  const max = Math.max(...items.map((row) => Number(row.value || 0)), 1);
  host.innerHTML = `
    <div class="persona-bar-list">
      ${items.map((row, index) => {
        const pct = Math.max(3, Math.round((Number(row.value || 0) / max) * 100));
        return `
          <div class="persona-bar-row">
            <div class="persona-bar-label"><span>${index + 1}</span>${pdEscape(row.label || row.name || "-")}</div>
            <div class="persona-bar-track"><div class="persona-bar-fill" style="width:${pct}%"></div></div>
            <div class="persona-bar-value">${pdEscape(pdNumber(row.value))}</div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function pdRenderDonutChart(hostId, entries) {
  const host = pdEl(hostId);
  if (!host) return;
  const rows = pdEntries(entries);
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  if (!total) {
    host.innerHTML = `<div class="persona-chart-empty">暂无可展示数据</div>`;
    return;
  }
  const colors = ["#2563eb", "#f59e0b", "#16a34a", "#dc2626", "#7c3aed", "#0f766e"];
  let cursor = 0;
  const segments = rows.map((row, index) => {
    const start = cursor;
    const size = (row.value / total) * 100;
    cursor += size;
    return `${colors[index % colors.length]} ${start}% ${cursor}%`;
  }).join(", ");
  host.innerHTML = `
    <div class="persona-donut-wrap">
      <div class="persona-donut" style="background: conic-gradient(${segments})">
        <div><strong>${pdNumber(total)}</strong><span>总计</span></div>
      </div>
      <div class="persona-donut-legend">
        ${rows.map((row, index) => `
          <div><span style="background:${colors[index % colors.length]}"></span>${pdEscape(row.label)}<b>${pdEscape(pdNumber(row.value))}</b></div>
        `).join("")}
      </div>
    </div>
  `;
}

function pdRenderTrendChart(hostId, rows) {
  const host = pdEl(hostId);
  if (!host) return;
  const items = (rows || []).slice(-30);
  if (!items.length) {
    host.innerHTML = `<div class="persona-chart-empty">暂无走势数据</div>`;
    return;
  }
  const width = 720;
  const height = 220;
  const pad = 28;
  const series = [
    { key: "published", label: "发布", color: "#2563eb" },
    { key: "post_views", label: "帖子浏览", color: "#f59e0b" },
    { key: "likes", label: "点赞", color: "#16a34a" },
  ];
  const max = Math.max(1, ...items.flatMap((row) => series.map((s) => Number(row[s.key] || 0))));
  const x = (index) => pad + (items.length === 1 ? 0 : (index / (items.length - 1)) * (width - pad * 2));
  const y = (value) => height - pad - (Number(value || 0) / max) * (height - pad * 2);
  const pathFor = (key) => items.map((row, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(row[key]).toFixed(1)}`).join(" ");
  host.innerHTML = `
    <svg class="persona-line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="流量走势图">
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" class="persona-axis" />
      <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}" class="persona-axis" />
      ${series.map((s) => `<path d="${pathFor(s.key)}" fill="none" stroke="${s.color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />`).join("")}
      ${items.map((row, index) => `<text x="${x(index)}" y="${height - 6}" text-anchor="middle">${pdEscape(String(row.date || "").slice(5))}</text>`).join("")}
    </svg>
    <div class="persona-line-legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${s.label}</span>`).join("")}</div>
  `;
}

function pdMatches(persona) {
  const search = String((pdEl("personaDashboardSearch") && pdEl("personaDashboardSearch").value) || "").trim().toLowerCase();
  const platform = pdPlatformFilter();
  const pad = String((pdEl("personaDashboardPad") && pdEl("personaDashboardPad").value) || "").trim();
  const haystack = [persona.name, persona.content, persona.bound_pad_code, persona.bound_pad_name, persona.owner_bot_name, persona.threads_account && persona.threads_account.handle].join(" ").toLowerCase();
  if (search && !haystack.includes(search)) return false;
  if (pad && String(persona.bound_pad_code || "") !== pad) return false;
  if (platform) {
    const platforms = (persona.hot_platforms || []).map((item) => String(item.platform || "").toLowerCase());
    const platformPosts = Object.keys((persona.counts && persona.counts.platform_posts) || {}).map((item) => item.toLowerCase());
    if (!platforms.includes(platform) && !platformPosts.includes(platform)) return false;
  }
  return pdDateInRange(persona.updated_at || persona.created_at);
}

function pdRenderSummary(data, visiblePersonas) {
  const host = pdEl("personaDashboardSummary");
  if (!host) return;
  const globalSummary = data.summary || {};
  const summary = pdVisibleSummary(visiblePersonas);
  const cards = [
    { label: "人设总数", value: summary.persona_count, hint: `全部 ${globalSummary.persona_count || 0}` },
    { label: "已生成帖子", value: summary.post_count, hint: "当前筛选归档帖子" },
    { label: "已发布", value: summary.published_count, hint: "当前筛选发布记录" },
    { label: "素材库图片", value: summary.image_count, hint: "当前筛选图片素材" },
    { label: "绑定智能体手机", value: summary.bound_pad_count, hint: "当前筛选设备数" },
    { label: "总互动量", value: summary.total_interactions, hint: "点赞、评论、转发、分享" },
    { label: "账号主页浏览", value: summary.recent_views, hint: "账号主页级浏览" },
    { label: "逐帖浏览合计", value: summary.post_views, hint: "逐帖浏览，不与主页浏览合并" },
    { label: "筛选热度", value: summary.hot_score, hint: "逐帖浏览 + 点赞 + 评论 + 分享 + 转发" },
  ];
  host.innerHTML = cards.map((card) => `
    <div class="kpi persona-kpi">
      <div class="label">${pdEscape(card.label)}</div>
      <div class="num">${pdEscape(pdNumber(card.value))}</div>
      <div class="small">${pdEscape(card.hint)}</div>
    </div>
  `).join("");
}

function pdPersonaWarnings(persona) {
  const warnings = persona.warnings || [];
  if (!warnings.length) return "";
  return `
    <div class="persona-warning-list">
      ${warnings.map((item) => `<div class="persona-warning-item">${pdEscape(item)}</div>`).join("")}
    </div>
  `;
}

function pdRenderPersonaCard(persona) {
  const hot = pdPersonaHot(persona);
  const counts = persona.counts || {};
  const rows = pdFilteredPostRows(persona);
  const pageSize = Math.max(5, Math.min(100, Number(personaDashboardPageSize || 10)));
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  personaDashboardPostPage = Math.max(1, Math.min(pageCount, Number(personaDashboardPostPage || 1)));
  const start = (personaDashboardPostPage - 1) * pageSize;
  const threads = persona.threads_account || {};
  const accountPlatform = String(personaDashboardAccountPlatform || "threads").toLowerCase();
  const isThreadsPlatform = accountPlatform === "threads";
  const platforms = (persona.hot_platforms || []).map((item) => `
    <div class="persona-platform-row">
      <strong>${pdEscape(item.platform || "-")}</strong>
      <span>账号主页浏览 ${pdEscape(pdNumber(item.recent_views))}</span>
      <span>逐帖浏览 ${pdEscape(pdNumber(item.post_views))}</span>
      <span>赞 ${pdEscape(pdNumber(item.likes))}</span>
      <span>评 ${pdEscape(pdNumber(item.comments))}</span>
      <span>${item.complete ? "完整" : "部分/未知"}</span>
    </div>
  `).join("");
  const postRows = rows.slice(start, start + pageSize).map((row) => `
    <tr>
      <td class="persona-post-platform">${pdEscape(row.platform || "-")}</td>
      <td class="persona-post-source">
        <div>${pdEscape(String(row.content || row.source_url || "-").slice(0, 120))}</div>
      </td>
      <td class="persona-post-number">${pdEscape(pdNumber(row.like_count))}</td>
      <td class="persona-post-number">${pdEscape(pdNumber(row.comment_count))}</td>
      <td class="persona-post-number">${pdEscape(pdNumber(row.share_count || row.repost_count))}</td>
      <td class="persona-post-number">${pdEscape(pdNumber(row.view_count))}</td>
      <td class="persona-post-actions">
        <button class="ghost" type="button" data-post-view="${pdEscape(row.post_key || "")}">查看</button>
        <button class="ghost persona-post-delete" type="button" data-post-delete="${pdEscape(row.post_key || "")}">删除</button>
      </td>
    </tr>
  `).join("");
  return `
    <article class="persona-detail-card">
      <div class="persona-detail-head">
        <div>
          <h3>${pdEscape(persona.name || "未命名人设")}</h3>
          <div class="small">智能体手机：${pdEscape(persona.bound_pad_name || persona.bound_pad_code || "未绑定")} · 机器人：${pdEscape(persona.owner_bot_name || "-")}</div>
        </div>
        <div class="persona-account-compact">
          <div class="persona-account-title">
            <label for="personaAccountPlatform">账号平台</label>
            <span>${isThreadsPlatform ? "绑定后可刷新该账号热点" : "当前仅展示平台切换"}</span>
          </div>
          <div class="persona-account-grid">
            <select id="personaAccountPlatform">
              <option value="threads" ${isThreadsPlatform ? "selected" : ""}>Threads</option>
              <option value="telegram" ${accountPlatform === "telegram" ? "selected" : ""}>Telegram</option>
            </select>
            <input id="personaThreadsInput" type="text" value="${isThreadsPlatform ? pdEscape(threads.handle || "") : ""}" placeholder="${isThreadsPlatform ? "username" : "暂未接入 Telegram 绑定"}" ${isThreadsPlatform ? "" : "disabled"} />
          </div>
          <div class="persona-account-actions">
            <button class="ghost" type="button" id="personaBindThreadsBtn" ${isThreadsPlatform ? "" : "disabled"}>保存</button>
            <button class="ghost persona-unbind-btn" type="button" id="personaUnbindThreadsBtn" ${isThreadsPlatform && threads.handle ? "" : "disabled"}>解绑</button>
            <button class="primary" type="button" id="personaRefreshCurrentBtn">刷新人设</button>
            <button class="primary persona-hot-refresh-btn" type="button" id="personaRefreshBoundHotBtn" ${isThreadsPlatform && threads.handle ? "" : "disabled"}>刷新热点</button>
          </div>
        </div>
        <div class="persona-score">
          <span>热度</span>
          <strong>${pdEscape(pdNumber(hot.hot_score))}</strong>
          <small>${pdEscape(persona.hot_score_formula || "热度 = 逐帖浏览 + 点赞 + 评论 + 分享 + 转发")}</small>
        </div>
      </div>
      ${pdPersonaWarnings(persona)}
      <div class="persona-bind-hint">
        <span>${isThreadsPlatform ? "没有绑定时无法抓取该人设账号热点；刷新会使用服务器端已保存的浏览器授权。" : "Telegram 账号绑定和热点抓取暂未接入；切回 Threads 可保存、解绑和刷新热点。"}</span>
      </div>
      <div class="persona-detail-grid">
        <div><span>帖子</span><strong>${pdEscape(pdNumber(counts.posts))}</strong></div>
        <div><span>发布</span><strong>${pdEscape(pdNumber(counts.published))}</strong></div>
        <div><span>素材</span><strong>${pdEscape(pdNumber(counts.images))}</strong></div>
        <div><span>互动</span><strong>${pdEscape(pdNumber(Number(hot.likes || 0) + Number(hot.comments || 0) + Number(hot.shares || 0) + Number(hot.reposts || 0)))}</strong></div>
        <div><span>账号主页浏览</span><strong>${pdEscape(pdNumber(hot.recent_views))}</strong></div>
        <div><span>逐帖浏览</span><strong>${pdEscape(pdNumber(hot.post_views))}</strong></div>
      </div>
      <div class="persona-content-preview">${pdEscape(persona.content || "暂无人设描述")}</div>
      <div class="persona-platform-list">${platforms || `<div class="small">暂无平台热点指标</div>`}</div>
      <div class="persona-table-wrap">
        <div class="persona-table-toolbar">
          <strong>发送推文指标</strong>
          <span>第 ${pdEscape(String(personaDashboardPostPage))} / ${pdEscape(String(pageCount))} 页 · 共 ${pdEscape(String(rows.length))} 条</span>
        </div>
        <table class="persona-post-table">
          <thead><tr><th>平台</th><th>推文内容 / 来源</th><th>点赞</th><th>评论</th><th>转发/分享</th><th>逐帖浏览</th><th>操作</th></tr></thead>
          <tbody>${postRows || `<tr><td colspan="7">暂无发送推文指标</td></tr>`}</tbody>
        </table>
      </div>
      <div class="persona-pager">
        <button class="ghost" type="button" id="personaPostPrev" ${personaDashboardPostPage <= 1 ? "disabled" : ""}>上一页</button>
        <span>每页 ${pdEscape(String(pageSize))} 条</span>
        <button class="ghost" type="button" id="personaPostNext" ${personaDashboardPostPage >= pageCount ? "disabled" : ""}>下一页</button>
      </div>
      ${pdRenderPostModal(persona)}
    </article>
  `;
}

function pdPersonaKey(persona, index = 0) {
  return String((persona && (persona.id || persona.name || persona.bound_pad_code)) || `persona-${index}`);
}

function pdFindPostRow(persona, postKey) {
  const key = String(postKey || "");
  return (pdFilteredPostRows(persona) || []).find((row) => String(row.post_key || "") === key) || null;
}

function pdMediaType(item) {
  const text = `${(item && item.type) || ""} ${(item && item.url) || ""}`.toLowerCase();
  if (/(video|mp4|mov|m4v|webm)/.test(text)) return "video";
  if (/(image|photo|png|jpe?g|webp|gif)/.test(text)) return "image";
  return "link";
}

function pdRenderPostMedia(row) {
  const items = Array.isArray(row.media_items) ? row.media_items.filter((item) => item && item.url) : [];
  if (!items.length) {
    return `<div class="persona-post-media-empty">暂无媒体文件</div>`;
  }
  return `
    <div class="persona-post-media-grid">
      ${items.map((item, index) => {
        const url = String(item.url || "");
        const type = pdMediaType(item);
        const label = item.label || `媒体 ${index + 1}`;
        if (type === "image") {
          return `<a class="persona-post-media-item" href="${pdEscape(url)}" target="_blank" rel="noreferrer"><img src="${pdEscape(url)}" alt="${pdEscape(label)}" loading="lazy" /></a>`;
        }
        if (type === "video") {
          return `<div class="persona-post-media-item"><video src="${pdEscape(url)}" controls preload="metadata"></video><a href="${pdEscape(url)}" target="_blank" rel="noreferrer">打开视频</a></div>`;
        }
        return `<a class="persona-post-media-link" href="${pdEscape(url)}" target="_blank" rel="noreferrer">${pdEscape(label || url)}</a>`;
      }).join("")}
    </div>
  `;
}

function pdRenderPostInfo(row) {
  const items = [
    ["平台", row.platform || "-"],
    ["发布时间", pdDate(row.published_at)],
    ["采集时间", pdDate(row.captured_at)],
    ["原始链接", row.source_url || ""],
    ["帖子编号", row.id || row.code || row.pk || ""],
  ].filter((item) => String(item[1] || "").trim());
  return `
    <div class="persona-post-info-list">
      ${items.map(([label, value]) => `
        <div>
          <span>${pdEscape(label)}</span>
          ${label === "原始链接" ? `<a href="${pdEscape(value)}" target="_blank" rel="noreferrer">${pdEscape(value)}</a>` : `<strong>${pdEscape(value)}</strong>`}
        </div>
      `).join("")}
    </div>
  `;
}

function pdRenderPostModal(persona) {
  const row = personaDashboardPostModalKey ? pdFindPostRow(persona, personaDashboardPostModalKey) : null;
  if (!row) return "";
  return `
    <div class="persona-post-modal" role="dialog" aria-modal="true" aria-label="推文详情">
      <div class="persona-post-modal-card">
        <div class="persona-post-modal-head">
          <div>
            <strong>推文详情</strong>
            <span>${pdEscape(row.platform || "-")} · ${pdEscape(row.published_at || row.captured_at || "无时间")}</span>
          </div>
          <button class="ghost" type="button" id="personaPostModalClose">关闭</button>
        </div>
        <div class="persona-post-modal-grid">
          <div><span>点赞</span><strong>${pdEscape(pdNumber(row.like_count))}</strong></div>
          <div><span>评论</span><strong>${pdEscape(pdNumber(row.comment_count))}</strong></div>
          <div><span>转发/分享</span><strong>${pdEscape(pdNumber(row.share_count || row.repost_count))}</strong></div>
          <div><span>逐帖浏览</span><strong>${pdEscape(pdNumber(row.view_count))}</strong></div>
        </div>
        <section class="persona-post-section">
          <h4>完整推文内容</h4>
          <div class="persona-post-full-content">${pdEscape(row.full_content || row.content || "暂无内容")}</div>
        </section>
        <section class="persona-post-section">
          <h4>媒体文件</h4>
          ${pdRenderPostMedia(row)}
        </section>
        <section class="persona-post-section">
          <h4>相关信息</h4>
          ${pdRenderPostInfo(row)}
        </section>
      </div>
    </div>
  `;
}

function pdRenderPersonaTabs(visiblePersonas, selectedPersona) {
  const tabs = pdEl("personaDashboardTabs");
  if (!tabs) return;
  const tabPageSize = 10;
  const tabPageCount = Math.max(1, Math.ceil(visiblePersonas.length / tabPageSize));
  personaDashboardTabPage = Math.max(1, Math.min(tabPageCount, Number(personaDashboardTabPage || 1)));
  const tabStart = (personaDashboardTabPage - 1) * tabPageSize;
  const tabPersonas = visiblePersonas.slice(tabStart, tabStart + tabPageSize);
  tabs.innerHTML = `
    <div class="persona-tab-rail-head">
      <strong>分栏</strong>
      <span>${pdEscape(String(visiblePersonas.length))} 人设</span>
    </div>
    <div class="persona-tab-list">
      <div class="persona-tab-section persona-tab-section-system">
      <button class="persona-tab ${personaDashboardSelectedId === "__overview__" ? "is-active" : ""}" type="button" data-persona-id="__overview__">
        <span class="persona-tab-index">总</span>
        <span class="persona-tab-main"><strong>总览首页</strong><span>全部图表与指标</span></span>
        <span class="persona-tab-metrics"><b>${pdEscape(pdNumber((personaDashboardData.summary || {}).persona_count))}</b><span>人设</span></span>
      </button>
      </div>
      <div class="persona-tab-section persona-tab-section-personas">
      ${tabPersonas.map((persona, pageIndex) => {
        const index = tabStart + pageIndex;
        const hot = persona.hot || {};
        const counts = persona.counts || {};
        const key = pdPersonaKey(persona, index);
        const active = selectedPersona && pdPersonaKey(selectedPersona, index) === key;
        return `
          <button class="persona-tab ${active ? "is-active" : ""}" type="button" data-persona-id="${pdEscape(key)}">
            <span class="persona-tab-index">${index + 1}</span>
            <span class="persona-tab-main">
              <strong>${pdEscape(persona.name || "未命名人设")}</strong>
              <span>${pdEscape(persona.bound_pad_name || persona.bound_pad_code || "未绑定智能体手机")}</span>
            </span>
            <span class="persona-tab-metrics">
              <b>${pdEscape(pdNumber(hot.hot_score))}</b>
              <span>${pdEscape(pdNumber(counts.published))} 发布</span>
            </span>
          </button>
        `;
      }).join("")}
      ${visiblePersonas.length > tabPageSize ? `
        <div class="persona-tab-pager">
          <button class="ghost" type="button" id="personaTabPrev" ${personaDashboardTabPage <= 1 ? "disabled" : ""}>上一页</button>
          <span>第 ${pdEscape(String(personaDashboardTabPage))} / ${pdEscape(String(tabPageCount))} 页</span>
          <button class="ghost" type="button" id="personaTabNext" ${personaDashboardTabPage >= tabPageCount ? "disabled" : ""}>下一页</button>
        </div>
      ` : ""}
      </div>
      <div class="persona-tab-section persona-tab-section-system persona-tab-section-bottom">
      <button class="persona-tab persona-tab-settings ${personaDashboardSelectedId === "__settings__" ? "is-active" : ""}" type="button" data-persona-id="__settings__">
        <span class="persona-tab-index">设</span>
        <span class="persona-tab-main"><strong>设置</strong><span>分页、刷新与显示数量</span></span>
        <span class="persona-tab-metrics"><b>${pdEscape(String(personaDashboardPageSize))}</b><span>每页</span></span>
      </button>
      </div>
    </div>
  `;
  tabs.querySelectorAll("[data-persona-id]").forEach((node) => {
    node.addEventListener("click", () => {
      personaDashboardSelectedId = String(node.getAttribute("data-persona-id") || "");
      personaDashboardPostPage = 1;
      pdRenderDashboard();
    });
  });
  const tabPrev = pdEl("personaTabPrev");
  const tabNext = pdEl("personaTabNext");
  if (tabPrev) tabPrev.addEventListener("click", () => { personaDashboardTabPage -= 1; pdRenderDashboard(); });
  if (tabNext) tabNext.addEventListener("click", () => { personaDashboardTabPage += 1; pdRenderDashboard(); });
}

function pdRenderSettings() {
  const settings = pdEl("personaDashboardSettings");
  if (!settings) return;
  settings.innerHTML = `
    <div class="persona-settings-card">
      <div>
        <h3>设置</h3>
        <div class="small">调整单个人设推文表的分页数量，并可手动刷新全部已绑定账号。</div>
      </div>
      <label for="personaPageSizeInput">每页推文数量</label>
      <div class="persona-settings-row">
        <input id="personaPageSizeInput" type="number" min="5" max="100" step="5" value="${pdEscape(String(personaDashboardPageSize))}" />
        <button class="primary" type="button" id="personaPageSizeApply">应用</button>
      </div>
      <div class="persona-settings-row persona-settings-row-left">
        <button class="primary" type="button" id="personaRefreshAllBtn">全量刷新全部已绑定人设</button>
        <span class="small">会逐个读取已绑定 Threads 用户名的人设；无绑定的人设会跳过并提示。</span>
      </div>
      <div class="small">可设置 5 到 100 条。刷新过程中可留在页面查看任务状态。</div>
    </div>
  `;
  const apply = pdEl("personaPageSizeApply");
  if (apply) {
    apply.addEventListener("click", () => {
      const input = pdEl("personaPageSizeInput");
      const next = Math.max(5, Math.min(100, Number(input && input.value) || 10));
      personaDashboardPageSize = next;
      personaDashboardPostPage = 1;
      localStorage.setItem("personaDashboardPageSize", String(next));
      pdRenderDashboard();
    });
  }
  const refreshAll = pdEl("personaRefreshAllBtn");
  if (refreshAll) refreshAll.addEventListener("click", () => pdStartRefresh(""));
}

function pdRenderDashboard() {
  const data = personaDashboardData;
  const list = pdEl("personaDashboardList");
  const empty = pdEl("personaDashboardEmpty");
  const meta = pdEl("personaDashboardMeta");
  const overview = pdEl("personaOverviewPane");
  const settings = pdEl("personaDashboardSettings");
  if (!data || !list || !empty) return;
  const visible = (data.personas || []).filter(pdMatches);
  let selected = visible.find((persona, index) => pdPersonaKey(persona, index) === String(personaDashboardSelectedId || ""));
  if (!["__overview__", "__settings__"].includes(personaDashboardSelectedId) && !selected && visible.length) {
    selected = visible[0];
    personaDashboardSelectedId = pdPersonaKey(selected, 0);
  }
  const charts = pdBuildFilteredCharts(visible, data);
  pdRenderSummary(data, visible);
  pdRenderBarChart("personaHotRankChart", visible.map((item) => ({ label: item.name, value: item.hot && item.hot.hot_score })));
  pdRenderDonutChart("personaPlatformChart", charts.platform_distribution);
  pdRenderDonutChart("personaCoverageChart", charts.hot_coverage);
  pdRenderTrendChart("personaTrendChart", charts.trend);
  pdRenderDonutChart("personaEngagementChart", charts.engagement_mix);
  pdRenderDonutChart("personaTaskStatusChart", charts.task_status_distribution);
  pdRenderPersonaTabs(visible, selected);
  pdRenderSettings();
  const mode = personaDashboardSelectedId;
  if (overview) overview.style.display = mode === "__overview__" ? "grid" : "none";
  if (settings) settings.style.display = mode === "__settings__" ? "grid" : "none";
  list.style.display = selected && mode !== "__overview__" && mode !== "__settings__" ? "grid" : "none";
  if (meta) meta.textContent = selected ? `当前显示 ${visible.length} / ${(data.personas || []).length} 个人设 · 已选：${selected.name || "未命名人设"}` : `当前显示 ${visible.length} / ${(data.personas || []).length} 个人设`;
  empty.style.display = visible.length ? "none" : "block";
  list.innerHTML = selected ? pdRenderPersonaCard(selected) : "";
  const prev = pdEl("personaPostPrev");
  const next = pdEl("personaPostNext");
  const bind = pdEl("personaBindThreadsBtn");
  const unbind = pdEl("personaUnbindThreadsBtn");
  const accountPlatform = pdEl("personaAccountPlatform");
  const refreshCurrent = pdEl("personaRefreshCurrentBtn");
  const refreshBoundHot = pdEl("personaRefreshBoundHotBtn");
  const modalClose = pdEl("personaPostModalClose");
  if (prev) prev.addEventListener("click", () => { personaDashboardPostPage -= 1; pdRenderDashboard(); });
  if (next) next.addEventListener("click", () => { personaDashboardPostPage += 1; pdRenderDashboard(); });
  if (bind && selected) bind.addEventListener("click", () => pdBindThreads(selected));
  if (unbind && selected) unbind.addEventListener("click", () => pdUnbindThreads(selected));
  if (accountPlatform) {
    accountPlatform.addEventListener("change", () => {
      personaDashboardAccountPlatform = String(accountPlatform.value || "threads");
      localStorage.setItem("personaDashboardAccountPlatform", personaDashboardAccountPlatform);
      pdRenderDashboard();
    });
  }
  if (refreshCurrent && selected) refreshCurrent.addEventListener("click", () => pdStartRefresh(selected.id, "已请求刷新当前人设..."));
  if (refreshBoundHot && selected) refreshBoundHot.addEventListener("click", () => pdStartRefresh(selected.id, "已请求刷新该绑定账号的全量热点信息..."));
  if (modalClose) modalClose.addEventListener("click", () => { personaDashboardPostModalKey = ""; pdRenderDashboard(); });
  list.querySelectorAll("[data-post-view]").forEach((node) => {
    node.addEventListener("click", () => {
      personaDashboardPostModalKey = String(node.getAttribute("data-post-view") || "");
      pdRenderDashboard();
    });
  });
  list.querySelectorAll("[data-post-delete]").forEach((node) => {
    node.addEventListener("click", () => {
      const postKey = String(node.getAttribute("data-post-delete") || "");
      if (selected && postKey) pdDeletePost(selected, postKey);
    });
  });
}

function pdSyncPadFilter(data) {
  const select = pdEl("personaDashboardPad");
  if (!select) return;
  const current = select.value;
  const pads = Array.from(new Set((data.personas || []).map((item) => String(item.bound_pad_code || "").trim()).filter(Boolean))).sort();
  select.innerHTML = `<option value="">全部智能体手机</option>${pads.map((pad) => `<option value="${pdEscape(pad)}">${pdEscape(pad)}</option>`).join("")}`;
  if (pads.includes(current)) select.value = current;
}

function pdSetMsg(text, type = "ok") {
  const msg = pdEl("personaDashboardMsg");
  if (!msg) return;
  msg.textContent = text || "";
  msg.className = text ? `msg ${type}` : "msg";
}

async function pdLoadDashboard() {
  pdSetMsg("正在加载人设数据...", "ok");
  try {
    const data = await pdApi("/api/persona_dashboard/overview");
    personaDashboardData = data;
    pdSyncPadFilter(data);
    const updated = pdEl("personaDashboardUpdated");
    if (updated) {
      const latest = data.summary && data.summary.latest_data_at;
      updated.textContent = `缓存读取：${pdDate(data.updated_at)} · 最近数据：${pdDate(latest)}`;
    }
    pdSetMsg("");
    pdRenderDashboard();
  } catch (err) {
    pdSetMsg(String((err && (err.detail || err.message)) || err || "加载失败"), "err");
  }
}

async function pdBindThreads(persona) {
  const input = pdEl("personaThreadsInput");
  const username = input ? input.value : "";
  try {
    pdSetMsg("正在保存 Threads 绑定...", "ok");
    await pdApi(`/api/persona_dashboard/personas/${encodeURIComponent(persona.id)}/threads_binding`, {
      method: "POST",
      body: { username },
    });
    pdSetMsg("绑定已保存。可以点击刷新当前人设抓取数据。", "ok");
    await pdLoadDashboard();
  } catch (err) {
    pdSetMsg(String((err && (err.detail || err.message)) || err || "保存绑定失败"), "err");
  }
}

async function pdUnbindThreads(persona) {
  try {
    pdSetMsg("正在解除 Threads 绑定...", "ok");
    await pdApi(`/api/persona_dashboard/personas/${encodeURIComponent(persona.id)}/threads_binding`, {
      method: "DELETE",
    });
    pdSetMsg("账号绑定已解除，旧账号热点缓存已清理。", "ok");
    await pdLoadDashboard();
  } catch (err) {
    pdSetMsg(String((err && (err.detail || err.message)) || err || "解除绑定失败"), "err");
  }
}

async function pdDeletePost(persona, postKey) {
  const ok = window.confirm("确认删除这条推文记录？删除后会立即从当前看板缓存中移除。");
  if (!ok) return;
  try {
    pdSetMsg("正在删除推文记录...", "ok");
    await pdApi(`/api/persona_dashboard/personas/${encodeURIComponent(persona.id)}/posts/${encodeURIComponent(postKey)}`, {
      method: "DELETE",
    });
    personaDashboardPostModalKey = "";
    pdSetMsg("推文记录已删除，正在刷新看板...", "ok");
    await pdLoadDashboard();
  } catch (err) {
    pdSetMsg(String((err && (err.detail || err.message)) || err || "删除推文失败"), "err");
  }
}

async function pdStartRefresh(archiveId, message) {
  try {
    pdSetMsg(message || (archiveId ? "已请求刷新当前人设..." : "已请求全量刷新..."), "ok");
    const task = await pdApi("/api/persona_dashboard/refresh", {
      method: "POST",
      body: { archive_id: archiveId || "" },
    });
    personaDashboardRefreshTask = task.id;
    pdPollRefresh(task.id);
  } catch (err) {
    pdSetMsg(String((err && (err.detail || err.message)) || err || "启动刷新失败"), "err");
  }
}

async function pdPollRefresh(taskId) {
  if (!taskId || taskId !== personaDashboardRefreshTask) return;
  try {
    const task = await pdApi(`/api/persona_dashboard/refresh/${encodeURIComponent(taskId)}`);
    const status = pdLabel(task.status);
    const progress = Number(task.progress || 0);
    const step = task.step ? `步骤：${task.step} · ` : "";
    const elapsed = task.elapsed_seconds ? ` · 已执行 ${task.elapsed_seconds} 秒` : "";
    pdSetMsg(`刷新任务：${status} · ${step}进度 ${progress}%${elapsed}。${task.message || ""}`, task.status === "failed" ? "err" : "ok");
    if (["queued", "running"].includes(String(task.status))) {
      window.setTimeout(() => pdPollRefresh(taskId), 2500);
      return;
    }
    personaDashboardRefreshTask = "";
    await pdLoadDashboard();
    if (task.status === "failed") {
      pdSetMsg(`刷新失败：${task.message || "请检查浏览器授权或账号绑定。"}`, "err");
    } else {
      pdSetMsg("刷新完成，数据已重新读取。", "ok");
    }
  } catch (err) {
    personaDashboardRefreshTask = "";
    pdSetMsg(String((err && (err.detail || err.message)) || err || "查询刷新状态失败"), "err");
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const refresh = pdEl("btnPersonaDashboardRefresh");
  const refreshAll = pdEl("btnPersonaDashboardRefreshAll");
  if (refresh) refresh.addEventListener("click", () => pdLoadDashboard());
  if (refreshAll) refreshAll.addEventListener("click", () => pdStartRefresh(""));
  ["personaDashboardSearch", "personaDashboardPlatform", "personaDashboardPad", "personaDashboardRange"].forEach((id) => {
    const node = pdEl(id);
    if (!node) return;
    node.addEventListener(id === "personaDashboardSearch" ? "input" : "change", () => {
      personaDashboardPostPage = 1;
      personaDashboardTabPage = 1;
      pdRenderDashboard();
    });
  });
  pdLoadDashboard();
});
