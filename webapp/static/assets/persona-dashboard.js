function pdEl(id) {
  return document.getElementById(id);
}

async function pdApi(path, opts = {}) {
  const res = await fetch(path, { cache: "no-store", ...opts });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text || `HTTP ${res.status}` };
  }
  if (!res.ok) throw data || { detail: `HTTP ${res.status}` };
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
    .map(([label, count]) => ({ label, value: Number(count || 0) }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value);
}

function pdRangeDays() {
  const range = String((pdEl("personaDashboardRange") && pdEl("personaDashboardRange").value) || "all").trim();
  const days = Number(range || 0);
  return Number.isFinite(days) && days > 0 ? days : 0;
}

function pdFilterTrend(rows) {
  const days = pdRangeDays();
  if (!days) return rows || [];
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return (rows || []).filter((row) => {
    const date = new Date(row.date || "").getTime();
    return Number.isFinite(date) && date >= cutoff;
  });
}

function pdVisibleSummary(visiblePersonas, fallbackSummary = {}) {
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
    const hot = persona.hot || {};
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
  if (!visiblePersonas.length && Number(fallbackSummary.persona_count || 0) === 0) return { ...fallbackSummary, ...summary };
  return summary;
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
  const platform = String((pdEl("personaDashboardPlatform") && pdEl("personaDashboardPlatform").value) || "").trim().toLowerCase();
  const pad = String((pdEl("personaDashboardPad") && pdEl("personaDashboardPad").value) || "").trim();
  const range = String((pdEl("personaDashboardRange") && pdEl("personaDashboardRange").value) || "all").trim();
  const haystack = [persona.name, persona.content, persona.bound_pad_code, persona.bound_pad_name, persona.owner_bot_name].join(" ").toLowerCase();
  if (search && !haystack.includes(search)) return false;
  if (pad && String(persona.bound_pad_code || "") !== pad) return false;
  if (platform) {
    const platforms = (persona.hot_platforms || []).map((item) => String(item.platform || "").toLowerCase());
    const platformPosts = Object.keys((persona.counts && persona.counts.platform_posts) || {}).map((item) => item.toLowerCase());
    if (!platforms.includes(platform) && !platformPosts.includes(platform)) return false;
  }
  if (range !== "all") {
    const days = Number(range || 0);
    const updated = new Date(persona.updated_at || persona.created_at || 0).getTime();
    if (Number.isFinite(days) && days > 0 && Number.isFinite(updated)) {
      const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
      if (updated < cutoff) return false;
    }
  }
  return true;
}

function pdRenderSummary(data, visiblePersonas) {
  const host = pdEl("personaDashboardSummary");
  if (!host) return;
  const globalSummary = data.summary || {};
  const summary = pdVisibleSummary(visiblePersonas, globalSummary);
  const cards = [
    { label: "人设总数", value: summary.persona_count, hint: `全部 ${globalSummary.persona_count || 0}` },
    { label: "已生成帖子", value: summary.post_count, hint: "当前筛选归档帖子" },
    { label: "已发布", value: summary.published_count, hint: "当前筛选发布记录" },
    { label: "素材库图片", value: summary.image_count, hint: "当前筛选图片素材" },
    { label: "绑定云手机", value: summary.bound_pad_count, hint: "当前筛选设备数" },
    { label: "总互动量", value: summary.total_interactions, hint: "当前筛选赞评转分享" },
    { label: "主页浏览", value: summary.recent_views, hint: "recentViews，账号级" },
    { label: "帖子浏览", value: summary.post_views, hint: "views/viewCount，逐帖" },
    { label: "筛选热度", value: summary.hot_score, hint: "当前列表合计" },
  ];
  host.innerHTML = cards.map((card) => `
    <div class="kpi persona-kpi">
      <div class="label">${pdEscape(card.label)}</div>
      <div class="num">${pdEscape(pdNumber(card.value))}</div>
      <div class="small">${pdEscape(card.hint)}</div>
    </div>
  `).join("");
}

function pdRenderPersonaCard(persona) {
  const hot = persona.hot || {};
  const counts = persona.counts || {};
  const platforms = (persona.hot_platforms || []).map((item) => `
    <div class="persona-platform-row">
      <strong>${pdEscape(item.platform || "-")}</strong>
      <span>主页浏览 ${pdEscape(pdNumber(item.recent_views))}</span>
      <span>帖子浏览 ${pdEscape(pdNumber(item.post_views))}</span>
      <span>赞 ${pdEscape(pdNumber(item.likes))}</span>
      <span>评 ${pdEscape(pdNumber(item.comments))}</span>
      <span>${item.complete ? "完整" : "部分/未知"}</span>
    </div>
  `).join("");
  const postRows = (persona.post_metrics || []).slice(0, 8).map((row) => `
    <tr>
      <td>${pdEscape(row.platform || "-")}</td>
      <td>${pdEscape(String(row.content || row.source_url || "-").slice(0, 80))}</td>
      <td>${pdEscape(pdNumber(row.like_count))}</td>
      <td>${pdEscape(pdNumber(row.comment_count))}</td>
      <td>${pdEscape(pdNumber(row.share_count || row.repost_count))}</td>
      <td>${pdEscape(pdNumber(row.view_count))}</td>
    </tr>
  `).join("");
  return `
    <article class="persona-detail-card">
      <div class="persona-detail-head">
        <div>
          <h3>${pdEscape(persona.name || "未命名人设")}</h3>
          <div class="small">云手机：${pdEscape(persona.bound_pad_name || persona.bound_pad_code || "未绑定")} · Bot：${pdEscape(persona.owner_bot_name || "-")}</div>
        </div>
        <div class="persona-score">
          <span>热度</span>
          <strong>${pdEscape(pdNumber(hot.hot_score))}</strong>
        </div>
      </div>
      <div class="persona-detail-grid">
        <div><span>帖子</span><strong>${pdEscape(pdNumber(counts.posts))}</strong></div>
        <div><span>发布</span><strong>${pdEscape(pdNumber(counts.published))}</strong></div>
        <div><span>素材</span><strong>${pdEscape(pdNumber(counts.images))}</strong></div>
        <div><span>互动</span><strong>${pdEscape(pdNumber(Number(hot.likes || 0) + Number(hot.comments || 0) + Number(hot.shares || 0) + Number(hot.reposts || 0)))}</strong></div>
        <div><span>主页浏览</span><strong>${pdEscape(pdNumber(hot.recent_views))}</strong></div>
        <div><span>帖子浏览</span><strong>${pdEscape(pdNumber(hot.post_views))}</strong></div>
      </div>
      <div class="persona-content-preview">${pdEscape(persona.content || "暂无人设描述")}</div>
      <div class="persona-platform-list">${platforms || `<div class="small">暂无平台热点指标</div>`}</div>
      <div class="persona-table-wrap">
        <table class="persona-post-table">
          <thead><tr><th>平台</th><th>帖子/来源</th><th>赞</th><th>评</th><th>转/分享</th><th>浏览</th></tr></thead>
          <tbody>${postRows || `<tr><td colspan="6">暂无逐帖指标</td></tr>`}</tbody>
        </table>
      </div>
    </article>
  `;
}

function pdRenderDashboard() {
  const data = personaDashboardData;
  const list = pdEl("personaDashboardList");
  const empty = pdEl("personaDashboardEmpty");
  const meta = pdEl("personaDashboardMeta");
  if (!data || !list || !empty) return;
  const visible = (data.personas || []).filter(pdMatches);
  pdRenderSummary(data, visible);
  pdRenderBarChart("personaHotRankChart", visible.map((item) => ({ label: item.name, value: item.hot && item.hot.hot_score })));
  pdRenderDonutChart("personaPlatformChart", data.charts && data.charts.platform_distribution);
  pdRenderDonutChart("personaCoverageChart", data.charts && data.charts.hot_coverage);
  pdRenderTrendChart("personaTrendChart", pdFilterTrend(data.charts && data.charts.trend));
  pdRenderDonutChart("personaEngagementChart", data.charts && data.charts.engagement_mix);
  pdRenderDonutChart("personaTaskStatusChart", data.charts && data.charts.task_status_distribution);
  if (meta) meta.textContent = `当前显示 ${visible.length} / ${(data.personas || []).length} 个人设`;
  empty.style.display = visible.length ? "none" : "block";
  list.innerHTML = visible.map(pdRenderPersonaCard).join("");
}

function pdSyncPadFilter(data) {
  const select = pdEl("personaDashboardPad");
  if (!select) return;
  const current = select.value;
  const pads = Array.from(new Set((data.personas || []).map((item) => String(item.bound_pad_code || "").trim()).filter(Boolean))).sort();
  select.innerHTML = `<option value="">全部云手机</option>${pads.map((pad) => `<option value="${pdEscape(pad)}">${pdEscape(pad)}</option>`).join("")}`;
  if (pads.includes(current)) select.value = current;
}

async function pdLoadDashboard() {
  const msg = pdEl("personaDashboardMsg");
  if (msg) {
    msg.textContent = "正在加载人设数据...";
    msg.className = "msg ok";
  }
  try {
    const data = await pdApi("/api/persona_dashboard/overview");
    personaDashboardData = data;
    pdSyncPadFilter(data);
    const updated = pdEl("personaDashboardUpdated");
    if (updated) {
      const latest = data.summary && data.summary.latest_data_at;
      updated.textContent = `缓存读取：${pdDate(data.updated_at)} · 最近数据：${pdDate(latest)}`;
    }
    if (msg) msg.textContent = "";
    pdRenderDashboard();
  } catch (err) {
    if (msg) {
      msg.textContent = String((err && (err.detail || err.message)) || err || "加载失败");
      msg.className = "msg err";
    }
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const refresh = pdEl("btnPersonaDashboardRefresh");
  if (refresh) refresh.addEventListener("click", () => pdLoadDashboard());
  ["personaDashboardSearch", "personaDashboardPlatform", "personaDashboardPad", "personaDashboardRange"].forEach((id) => {
    const node = pdEl(id);
    if (!node) return;
    node.addEventListener(id === "personaDashboardSearch" ? "input" : "change", () => pdRenderDashboard());
  });
  pdLoadDashboard();
});
