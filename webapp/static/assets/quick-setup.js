async function qsApi(path, opts = {}) {
  const res = await fetch(path, { credentials: "include", cache: "no-store", ...opts });
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

const qsEl = (id) => document.getElementById(id);

let maskedTokenValue = "";
let maskedTextKeyValue = "";
let maskedImageKeyValue = "";
let maskedVideoKeyValue = "";
let operationInFlight = false;
let quickTextModels = [];
let quickImageModels = [];

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function parseModelList(value) {
  const seen = new Set();
  return String(value || "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => {
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });
}

function setSetupMsg(text, ok = true) {
  const box = qsEl("quickSetupMsg");
  if (!box) return;
  box.textContent = text || "";
  box.className = `msg ${ok ? "ok" : "err"}`;
}

function setProcessMsg(text) {
  const box = qsEl("processOperationStatus");
  if (box) box.textContent = text || "";
}

function configuredText(runtime, key) {
  return runtime[`${key}_configured`]
    ? `已配置：${runtime[`${key}_masked`] || "已保存"}`
    : "尚未配置";
}

function runtimeFromResponse(resp) {
  if (resp && resp.runtime_config && typeof resp.runtime_config === "object") return resp.runtime_config;
  return resp && typeof resp === "object" ? resp : {};
}

function getRealInputValue(id, maskedValue) {
  const value = (qsEl(id)?.value || "").trim();
  if (!value || value === maskedValue) return "";
  return value;
}

function setBusy(busy, activeButtonId = "") {
  operationInFlight = busy;
  [
    "btnSaveToken",
    "btnClearToken",
    "btnSaveGrok",
    "btnClearGrokKey",
    "btnBrowseQuickGrokModels",
    "btnAddQuickModel",
    "btnSaveImageModels",
    "btnBrowseQuickImageModels",
    "btnAddQuickImageModel",
    "btnSaveVideoModels",
    "btnBrowseQuickVideoModels",
    "btnApplyQuickVideoModel",
    "btnStartProcess",
    "btnStopProcess",
    "btnReloadSetup",
  ].forEach((id) => {
    const button = qsEl(id);
    if (!button) return;
    button.disabled = busy;
    button.classList.toggle("is-loading", busy && id === activeButtonId);
  });
}

function modelVendor(model) {
  const text = String(model || "").trim();
  const lower = text.toLowerCase();
  if (!text) return "其他";
  if (lower.includes("openai") || lower.startsWith("gpt") || /^o[1345](?:[-_.]|$)/i.test(text)) return "OpenAI";
  if (lower.includes("anthropic") || lower.includes("claude")) return "Anthropic";
  if (lower.includes("google") || lower.includes("gemini") || lower.includes("imagen")) return "Google";
  if (lower.includes("xai") || lower.includes("grok")) return "xAI";
  if (lower.includes("qwen")) return "Qwen";
  if (lower.includes("deepseek")) return "DeepSeek";
  if (lower.includes("doubao") || lower.includes("bytedance") || lower.includes("seedream") || lower.includes("seedance")) return "ByteDance";
  if (lower.includes("glm")) return "GLM";
  if (lower.includes("minimax")) return "MiniMax";
  if (lower.includes("mistral")) return "Mistral";
  if (lower.includes("flux")) return "Flux";
  if (lower.includes("dall-e")) return "DALL-E";
  if (lower.includes("wan")) return "Wan";
  if (lower.includes("kling")) return "Kling";
  if (lower.includes("hailuo")) return "Hailuo";
  if (lower.includes("veo")) return "Veo";
  const first = text.split(/[/:_.-]/).find(Boolean) || text;
  return first.slice(0, 24);
}

function filterModelPickerOptions(picker, query) {
  if (!picker) return;
  const normalized = String(query || "").trim().toLowerCase();
  const selectedVendor = String(picker.querySelector("[data-model-picker-vendor]")?.value || "");
  let visibleCount = 0;
  picker.querySelectorAll(".admin-model-picker-option").forEach((button) => {
    const text = String(button.textContent || "").toLowerCase();
    const vendor = String(button.dataset.vendor || "");
    const visible = !(normalized && !text.includes(normalized)) && !(selectedVendor && vendor !== selectedVendor);
    button.hidden = !visible;
    if (visible) visibleCount += 1;
  });
  const countNode = picker.querySelector("[data-model-picker-count]");
  if (countNode) countNode.textContent = `显示 ${visibleCount} / ${picker.querySelectorAll(".admin-model-picker-option").length} 个可用模型`;
}

function renderSearchableModelPicker(picker, items, dataAttrName, emptyMessage, placeholder = "搜索模型") {
  if (!picker) return;
  const models = parseModelList((Array.isArray(items) ? items : []).join(",")).sort((a, b) => a.localeCompare(b));
  picker.hidden = false;
  if (!models.length) {
    picker.innerHTML = `<div class="admin-model-picker-status">${escapeHtml(emptyMessage)}</div>`;
    return;
  }
  const vendors = parseModelList(models.map((model) => modelVendor(model)).join(",")).sort((a, b) => a.localeCompare(b));
  picker.innerHTML = `
    <div class="admin-model-picker-toolbar">
      <div class="admin-model-picker-count" data-model-picker-count>共 ${models.length} 个可用模型</div>
      <select class="admin-model-picker-vendor" data-model-picker-vendor>
        <option value="">全部厂商</option>
        ${vendors.map((vendor) => `<option value="${escapeHtml(vendor)}">${escapeHtml(vendor)}</option>`).join("")}
      </select>
    </div>
    <input class="admin-model-picker-search" type="search" placeholder="${escapeHtml(placeholder)}" data-model-picker-search>
    <div class="admin-model-picker-options">
      ${models
        .map((model) => `<button type="button" class="ghost admin-model-picker-option" data-vendor="${escapeHtml(modelVendor(model))}" data-${dataAttrName}="${escapeHtml(model)}">${escapeHtml(model)}</button>`)
        .join("")}
    </div>
  `;
  filterModelPickerOptions(picker, "");
}

function bindPickerFilters(pickerId) {
  const picker = qsEl(pickerId);
  if (!picker) return;
  picker.addEventListener("input", (event) => {
    const target = event.target;
    if (target && target.matches("[data-model-picker-search]")) {
      filterModelPickerOptions(picker, target.value);
    }
  });
  picker.addEventListener("change", (event) => {
    const target = event.target;
    if (target && target.matches("[data-model-picker-vendor]")) {
      const query = picker.querySelector("[data-model-picker-search]")?.value || "";
      filterModelPickerOptions(picker, query);
    }
  });
}

function syncTextModelInputFromList() {
  const input = qsEl("llmModelOrder");
  if (input) input.value = quickTextModels.join(", ");
}

function syncImageModelInputFromList() {
  const input = qsEl("imageModelOrder");
  if (input) input.value = quickImageModels.join(", ");
}

function renderModelChips(wrapId, models, actionPrefix) {
  const wrap = qsEl(wrapId);
  if (!wrap) return;
  if (!models.length) {
    wrap.innerHTML = '<div class="admin-model-picker-status">尚未添加模型。</div>';
    return;
  }
  wrap.innerHTML = models
    .map(
      (model, index) => `
        <div class="admin-model-chip">
          <span>${escapeHtml(model)}</span>
          <div class="admin-model-chip-actions">
            <button type="button" class="ghost admin-model-chip-order" data-${actionPrefix}-action="up" data-${actionPrefix}-index="${index}" aria-label="上移">↑</button>
            <button type="button" class="ghost admin-model-chip-order" data-${actionPrefix}-action="down" data-${actionPrefix}-index="${index}" aria-label="下移">↓</button>
            <button type="button" class="ghost admin-model-chip-remove" data-${actionPrefix}-action="remove" data-${actionPrefix}-index="${index}" aria-label="删除">×</button>
          </div>
        </div>`,
    )
    .join("");
}

function renderTextModels() {
  renderModelChips("quickModelList", quickTextModels, "model");
  syncTextModelInputFromList();
}

function renderImageModels() {
  renderModelChips("quickImageModelList", quickImageModels, "image-model");
  syncImageModelInputFromList();
}

function addTextModel(model) {
  const value = String(model || "").trim();
  if (!value) return;
  if (!quickTextModels.includes(value)) quickTextModels.push(value);
  renderTextModels();
}

function addImageModel(model) {
  const value = String(model || "").trim();
  if (!value) return;
  if (!quickImageModels.includes(value)) quickImageModels.push(value);
  renderImageModels();
}

function setPickerStatus(pickerId, message, isError = false) {
  const picker = qsEl(pickerId);
  if (!picker) return;
  picker.hidden = false;
  picker.innerHTML = `<div class="admin-model-picker-status${isError ? " error" : ""}">${escapeHtml(message)}</div>`;
}

function fillSetupForm(resp) {
  if (operationInFlight) return;
  const runtime = runtimeFromResponse(resp);
  const processInfo = resp && typeof resp.process === "object" ? resp.process : {};

  maskedTokenValue = runtime.telegram_bot_token_masked || "";
  const tokenInput = qsEl("telegramBotToken");
  if (tokenInput && document.activeElement !== tokenInput) tokenInput.value = maskedTokenValue;
  qsEl("botTokenStatus").textContent = configuredText(runtime, "telegram_bot_token");
  qsEl("setupBotState").textContent = runtime.telegram_bot_token_configured ? "已配置" : "尚未配置";

  qsEl("llmBaseUrl").value = runtime.llm_base_url || "https://llm.runninghub.ai/v1";
  maskedTextKeyValue = runtime.llm_api_key_gpt_masked || runtime.llm_api_key_masked || "";
  const textKeyInput = qsEl("llmApiKey");
  if (textKeyInput && document.activeElement !== textKeyInput) textKeyInput.value = maskedTextKeyValue;
  qsEl("grokKeyStatus").textContent = configuredText(runtime, "llm_api_key_gpt");
  quickTextModels = parseModelList(runtime.llm_model_priority_order || runtime.llm_default_model_gpt || runtime.llm_default_model || "xai/grok-4.3, grok-4.2");
  renderTextModels();
  qsEl("setupGrokState").textContent = runtime.llm_api_key_gpt_configured || runtime.llm_api_key_configured ? "已配置" : "尚未配置";

  qsEl("imageBaseUrl").value = runtime.image_model_provider_base_url || "http://202.90.21.53:3008";
  maskedImageKeyValue = runtime.image_model_provider_api_key_gemini_masked || "";
  const imageKeyInput = qsEl("imageApiKey");
  if (imageKeyInput && document.activeElement !== imageKeyInput) imageKeyInput.value = maskedImageKeyValue;
  qsEl("imageKeyStatus").textContent = configuredText(runtime, "image_model_provider_api_key_gemini");
  quickImageModels = parseModelList(runtime.image_model_priority_order || runtime.image_model_default_model_gemini || runtime.image_model_default_model || "gemini-3-pro-image-preview");
  renderImageModels();

  qsEl("videoApiName").value = runtime.mulerouter_api_name || "";
  qsEl("videoBaseUrl").value = runtime.mulerouter_base_url || "https://api.mulerouter.ai";
  qsEl("videoModelName").value = runtime.mulerouter_wan_i2v_model || "wan2.7-i2v-spicy";
  qsEl("videoEndpoint").value = runtime.mulerouter_wan_i2v_endpoint || "/vendors/carrothub/v1/wan2.7-i2v-spicy/generation";
  qsEl("videoNegativePrompt").value = runtime.mulerouter_wan_i2v_negative_prompt || "low quality, blurry, distorted, watermark, text, logo";
  maskedVideoKeyValue = runtime.mulerouter_api_key_masked || "";
  const videoKeyInput = qsEl("videoApiKey");
  if (videoKeyInput && document.activeElement !== videoKeyInput) videoKeyInput.value = maskedVideoKeyValue;

  const desired = processInfo.desired || "-";
  const running = processInfo.running ? "运行中" : "未运行";
  const heartbeat = Number(processInfo.heartbeat_age_seconds || 0);
  let label = desired === "stopped" ? `${running} / 已要求停止` : running;
  if (processInfo.running && heartbeat > 90) label = "运行中 / 心跳延迟";
  qsEl("setupProcessState").textContent = label;
}

async function loadSetup(silent = false) {
  if (operationInFlight) return;
  if (!silent) setSetupMsg("正在读取配置...", true);
  try {
    const status = await qsApi("/api/quick_setup/status");
    fillSetupForm(status);
    if (!silent) setSetupMsg("配置已读取。", true);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  }
}

async function savePartial(payload, successText) {
  const saved = await qsApi("/api/quick_setup/runtime_config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  fillSetupForm(saved);
  setSetupMsg(successText, true);
  return saved;
}

async function saveToken() {
  setBusy(true, "btnSaveToken");
  setSetupMsg("正在保存 Bot Token...", true);
  try {
    const token = getRealInputValue("telegramBotToken", maskedTokenValue);
    if (!token) throw new Error("请输入新的 Bot Token。");
    await savePartial({ telegram_bot_token: token }, "Bot Token 已保存。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function clearToken() {
  if (!confirm("确定清空 Bot Token？清空前会停止 Bot 进程。")) return;
  setBusy(true, "btnClearToken");
  try {
    const saved = await qsApi("/api/quick_setup/telegram_bot_token", { method: "DELETE" });
    fillSetupForm(saved);
    setSetupMsg("Bot Token 已清空，Bot 进程已停止。", true);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function saveTextModels() {
  setBusy(true, "btnSaveGrok");
  try {
    const manualModels = parseModelList(qsEl("llmModelOrder").value);
    quickTextModels = manualModels.length ? manualModels : quickTextModels;
    const models = quickTextModels.length ? quickTextModels.join(", ") : "xai/grok-4.3, grok-4.2";
    const payload = {
      llm_base_url: qsEl("llmBaseUrl").value.trim() || "https://llm.runninghub.ai/v1",
      llm_default_model: models,
      llm_default_model_gpt: models,
      llm_model_priority_order: models,
    };
    const key = getRealInputValue("llmApiKey", maskedTextKeyValue);
    if (key) {
      payload.llm_api_key = key;
      payload.llm_api_key_gpt = key;
    }
    await savePartial(payload, key ? "文字模型配置和新 Key 已保存。" : "文字模型配置已保存，Key 保持原配置。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function saveImageModels() {
  setBusy(true, "btnSaveImageModels");
  try {
    const manualModels = parseModelList(qsEl("imageModelOrder").value);
    quickImageModels = manualModels.length ? manualModels : quickImageModels;
    const models = quickImageModels.length ? quickImageModels.join(", ") : "gemini-3-pro-image-preview";
    const payload = {
      image_model_provider_base_url: qsEl("imageBaseUrl").value.trim() || "http://202.90.21.53:3008",
      image_model_default_model: models,
      image_model_default_model_gemini: models,
      image_model_priority_order: models,
    };
    const key = getRealInputValue("imageApiKey", maskedImageKeyValue);
    if (key) payload.image_model_provider_api_key_gemini = key;
    await savePartial(payload, key ? "图片模型配置和新 Key 已保存。" : "图片模型配置已保存，Key 保持原配置。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

function applyVideoModel(model) {
  const value = String(model || "").trim();
  if (!value) return;
  qsEl("videoModelName").value = value;
  const endpointInput = qsEl("videoEndpoint");
  const current = String(endpointInput.value || "").trim();
  if (/\/vendors\/[^/]+\/v\d+\//i.test(current) && /\/generation(?:[/?#]|$)/i.test(current)) {
    endpointInput.value = current.replace(/(\/vendors\/[^/]+\/v\d+\/)([^/?#]+)(\/generation.*)$/i, `$1${value}$3`);
  }
}

async function saveVideoModels() {
  setBusy(true, "btnSaveVideoModels");
  try {
    const payload = {
      mulerouter_api_name: qsEl("videoApiName").value.trim(),
      mulerouter_base_url: qsEl("videoBaseUrl").value.trim() || "https://api.mulerouter.ai",
      mulerouter_wan_i2v_model: qsEl("videoModelName").value.trim() || "wan2.7-i2v-spicy",
      mulerouter_wan_i2v_endpoint: qsEl("videoEndpoint").value.trim() || "/vendors/carrothub/v1/wan2.7-i2v-spicy/generation",
      mulerouter_wan_i2v_negative_prompt: qsEl("videoNegativePrompt").value.trim(),
    };
    const key = getRealInputValue("videoApiKey", maskedVideoKeyValue);
    if (key) payload.mulerouter_api_key = key;
    await savePartial(payload, key ? "视频模型配置和新 Key 已保存。" : "视频模型配置已保存，Key 保持原配置。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function browseTextModels() {
  const picker = qsEl("quickAvailableModelPicker");
  if (!picker) return;
  if (!picker.hidden && picker.children.length > 0) {
    picker.hidden = true;
    return;
  }
  setBusy(true, "btnBrowseQuickGrokModels");
  setPickerStatus("quickAvailableModelPicker", "正在识别当前 API 支持的文字模型...");
  try {
    const resp = await qsApi("/api/quick_setup/llm_models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        llm_base_url: qsEl("llmBaseUrl").value.trim(),
        llm_api_key: getRealInputValue("llmApiKey", maskedTextKeyValue),
      }),
    });
    renderSearchableModelPicker(picker, resp.models || [], "quick-model", "没有查询到可用文字模型", "搜索文字模型");
  } catch (err) {
    setPickerStatus("quickAvailableModelPicker", err.detail || err.message || String(err), true);
  } finally {
    setBusy(false);
  }
}

async function browseImageModels() {
  const picker = qsEl("quickAvailableImageModelPicker");
  if (!picker) return;
  if (!picker.hidden && picker.children.length > 0) {
    picker.hidden = true;
    return;
  }
  setBusy(true, "btnBrowseQuickImageModels");
  setPickerStatus("quickAvailableImageModelPicker", "正在识别当前 API 支持的图片模型...");
  try {
    const resp = await qsApi("/api/quick_setup/image_models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_url: qsEl("imageBaseUrl").value.trim(),
        api_key: getRealInputValue("imageApiKey", maskedImageKeyValue),
        provider: "openai-compatible",
      }),
    });
    renderSearchableModelPicker(picker, resp.models || [], "quick-image-model", "没有查询到可用图片模型", "搜索图片模型");
  } catch (err) {
    setPickerStatus("quickAvailableImageModelPicker", err.detail || err.message || String(err), true);
  } finally {
    setBusy(false);
  }
}

async function browseVideoModels() {
  const picker = qsEl("quickAvailableVideoModelPicker");
  if (!picker) return;
  if (!picker.hidden && picker.children.length > 0) {
    picker.hidden = true;
    return;
  }
  setBusy(true, "btnBrowseQuickVideoModels");
  setPickerStatus("quickAvailableVideoModelPicker", "正在识别当前 API 支持的视频模型...");
  try {
    const resp = await qsApi("/api/quick_setup/video_models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "video",
        base_url: qsEl("videoBaseUrl").value.trim(),
        api_key: getRealInputValue("videoApiKey", maskedVideoKeyValue),
        endpoint: qsEl("videoEndpoint").value.trim(),
      }),
    });
    renderSearchableModelPicker(picker, resp.models || [], "quick-video-model", "没有查询到可用视频模型", "搜索视频模型");
  } catch (err) {
    setPickerStatus("quickAvailableVideoModelPicker", err.detail || err.message || String(err), true);
  } finally {
    setBusy(false);
  }
}

async function clearTextKey() {
  if (!confirm("确定清空文字模型 Key？")) return;
  setBusy(true, "btnClearGrokKey");
  try {
    const saved = await qsApi("/api/quick_setup/grok_key", { method: "DELETE" });
    maskedTextKeyValue = "";
    qsEl("llmApiKey").value = "";
    fillSetupForm(saved);
    setSetupMsg("文字模型 Key 已清空。", true);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function controlProcess(action) {
  setBusy(true, action === "start" ? "btnStartProcess" : "btnStopProcess");
  const starting = action === "start";
  setSetupMsg(starting ? "正在启动 Bot 进程..." : "正在停止 Bot 进程...", true);
  try {
    const status = await qsApi("/api/quick_setup/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    fillSetupForm(status);
    setSetupMsg(starting ? "Bot 启动流程已完成。" : "Bot 停止流程已完成。", true);
    setProcessMsg(starting ? "Bot 进程已启动，请在 Telegram 发送 /start 测试。" : "Bot 进程已停止。");
    setTimeout(() => loadSetup(true), 1800);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
  }
}

function moveModelItem(list, index, action) {
  if (!Number.isInteger(index) || index < 0 || index >= list.length) return;
  if (action === "remove") list.splice(index, 1);
  if (action === "up" && index > 0) [list[index - 1], list[index]] = [list[index], list[index - 1]];
  if (action === "down" && index < list.length - 1) [list[index], list[index + 1]] = [list[index + 1], list[index]];
}

function bindTabs() {
  const tabs = Array.from(document.querySelectorAll("[data-quick-model-tab]"));
  const panels = Array.from(document.querySelectorAll("[data-quick-model-panel]"));
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.quickModelTab || "text";
      tabs.forEach((item) => item.classList.toggle("is-active", item.dataset.quickModelTab === name));
      panels.forEach((panel) => panel.classList.toggle("is-active", panel.dataset.quickModelPanel === name));
    });
  });
}

function bindOutsideClickToCloseModelPickers() {
  const pickerPairs = [
    ["quickAvailableModelPicker", "btnBrowseQuickGrokModels"],
    ["quickAvailableImageModelPicker", "btnBrowseQuickImageModels"],
    ["quickAvailableVideoModelPicker", "btnBrowseQuickVideoModels"],
  ];
  document.addEventListener("click", (event) => {
    const target = event.target instanceof HTMLElement ? event.target : null;
    if (!target) return;
    pickerPairs.forEach(([pickerId, triggerId]) => {
      const picker = qsEl(pickerId);
      if (!picker || picker.hidden) return;
      if (target.closest(`#${pickerId}`) || target.closest(`#${triggerId}`)) return;
      picker.hidden = true;
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindTabs();
  bindPickerFilters("quickAvailableModelPicker");
  bindPickerFilters("quickAvailableImageModelPicker");
  bindPickerFilters("quickAvailableVideoModelPicker");
  bindOutsideClickToCloseModelPickers();

  qsEl("btnReloadSetup")?.addEventListener("click", () => loadSetup(false));
  qsEl("btnSaveToken")?.addEventListener("click", saveToken);
  qsEl("btnClearToken")?.addEventListener("click", clearToken);
  qsEl("btnSaveGrok")?.addEventListener("click", saveTextModels);
  qsEl("btnClearGrokKey")?.addEventListener("click", clearTextKey);
  qsEl("btnBrowseQuickGrokModels")?.addEventListener("click", browseTextModels);
  qsEl("btnSaveImageModels")?.addEventListener("click", saveImageModels);
  qsEl("btnBrowseQuickImageModels")?.addEventListener("click", browseImageModels);
  qsEl("btnSaveVideoModels")?.addEventListener("click", saveVideoModels);
  qsEl("btnBrowseQuickVideoModels")?.addEventListener("click", browseVideoModels);
  qsEl("btnApplyQuickVideoModel")?.addEventListener("click", () => applyVideoModel(qsEl("videoModelName")?.value));

  qsEl("btnAddQuickModel")?.addEventListener("click", () => {
    addTextModel(qsEl("quickModelInput")?.value);
    if (qsEl("quickModelInput")) qsEl("quickModelInput").value = "";
  });
  qsEl("btnAddQuickImageModel")?.addEventListener("click", () => {
    addImageModel(qsEl("quickImageModelInput")?.value);
    if (qsEl("quickImageModelInput")) qsEl("quickImageModelInput").value = "";
  });
  qsEl("quickModelInput")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addTextModel(qsEl("quickModelInput")?.value);
    qsEl("quickModelInput").value = "";
  });
  qsEl("quickImageModelInput")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addImageModel(qsEl("quickImageModelInput")?.value);
    qsEl("quickImageModelInput").value = "";
  });
  qsEl("llmModelOrder")?.addEventListener("change", () => {
    quickTextModels = parseModelList(qsEl("llmModelOrder").value);
    renderTextModels();
  });
  qsEl("imageModelOrder")?.addEventListener("change", () => {
    quickImageModels = parseModelList(qsEl("imageModelOrder").value);
    renderImageModels();
  });
  qsEl("quickModelList")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-model-action]");
    if (!button) return;
    moveModelItem(quickTextModels, Number(button.dataset.modelIndex), button.dataset.modelAction);
    renderTextModels();
  });
  qsEl("quickImageModelList")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-image-model-action]");
    if (!button) return;
    moveModelItem(quickImageModels, Number(button.dataset.imageModelIndex), button.dataset.imageModelAction);
    renderImageModels();
  });
  qsEl("quickAvailableModelPicker")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quick-model]");
    if (!button) return;
    addTextModel(button.dataset.quickModel);
    qsEl("quickAvailableModelPicker").hidden = true;
  });
  qsEl("quickAvailableImageModelPicker")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quick-image-model]");
    if (!button) return;
    addImageModel(button.dataset.quickImageModel);
    qsEl("quickAvailableImageModelPicker").hidden = true;
  });
  qsEl("quickAvailableVideoModelPicker")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quick-video-model]");
    if (!button) return;
    applyVideoModel(button.dataset.quickVideoModel);
    qsEl("quickAvailableVideoModelPicker").hidden = true;
  });

  qsEl("btnStartProcess")?.addEventListener("click", () => controlProcess("start"));
  qsEl("btnStopProcess")?.addEventListener("click", () => controlProcess("stop"));
  [
    ["telegramBotToken", () => maskedTokenValue],
    ["llmApiKey", () => maskedTextKeyValue],
    ["imageApiKey", () => maskedImageKeyValue],
    ["videoApiKey", () => maskedVideoKeyValue],
  ].forEach(([id, getter]) => {
    qsEl(id)?.addEventListener("focus", () => {
      if (qsEl(id).value === getter()) qsEl(id).value = "";
    });
  });

  loadSetup();
  setInterval(() => loadSetup(true), 8000);
});
