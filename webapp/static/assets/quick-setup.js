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
let maskedNewPersonaRunningHubKeyValue = "";
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

function displayMaskedInput(maskedValue) {
  return maskedValue ? "••••••••••••••••••••••••" : "";
}

function runtimeFromResponse(resp) {
  if (resp && resp.runtime_config && typeof resp.runtime_config === "object") return resp.runtime_config;
  return resp && typeof resp === "object" ? resp : {};
}

function getRealInputValue(id, maskedValue) {
  const value = (qsEl(id)?.value || "").trim();
  if (!value || value === maskedValue || value === displayMaskedInput(maskedValue)) return "";
  return value;
}

function setBusy(busy, activeButtonId = "") {
  operationInFlight = busy;
  [
    "btnSaveToken",
    "btnClearToken",
    "btnClearGrokKey",
    "btnClearImageKey",
    "btnClearRunningHubKey",
    "btnClearVideoKey",
    "btnSaveModelConfig",
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
  if (tokenInput && document.activeElement !== tokenInput) tokenInput.value = displayMaskedInput(maskedTokenValue);
  qsEl("botTokenStatus").textContent = configuredText(runtime, "telegram_bot_token");
  qsEl("setupBotState").textContent = runtime.telegram_bot_token_configured ? "已配置" : "尚未配置";

  qsEl("llmBaseUrl").value = runtime.llm_base_url || "https://llm.runninghub.ai/v1";
  maskedTextKeyValue = runtime.llm_api_key_gpt_masked || runtime.llm_api_key_masked || "";
  const textKeyInput = qsEl("llmApiKey");
  if (textKeyInput && document.activeElement !== textKeyInput) textKeyInput.value = displayMaskedInput(maskedTextKeyValue);
  qsEl("grokKeyStatus").textContent = configuredText(runtime, "llm_api_key_gpt");
  qsEl("setupGrokState").textContent = runtime.llm_api_key_gpt_configured || runtime.llm_api_key_configured ? "已配置" : "尚未配置";

  qsEl("imageBaseUrl").value = runtime.image_model_provider_base_url || "http://202.90.21.53:3008";
  maskedImageKeyValue = runtime.image_model_provider_api_key_gemini_masked || "";
  const imageKeyInput = qsEl("imageApiKey");
  if (imageKeyInput && document.activeElement !== imageKeyInput) imageKeyInput.value = displayMaskedInput(maskedImageKeyValue);
  qsEl("imageKeyStatus").textContent = configuredText(runtime, "image_model_provider_api_key_gemini");
  if (qsEl("newPersonaRunningHubBaseUrl")) qsEl("newPersonaRunningHubBaseUrl").value = runtime.new_persona_runninghub_base_url || "https://www.runninghub.ai";
  maskedNewPersonaRunningHubKeyValue = runtime.new_persona_runninghub_api_key_masked || "";
  const runningHubKeyInput = qsEl("newPersonaRunningHubApiKey");
  if (runningHubKeyInput && document.activeElement !== runningHubKeyInput) runningHubKeyInput.value = displayMaskedInput(maskedNewPersonaRunningHubKeyValue);
  if (qsEl("newPersonaRunningHubKeyStatus")) qsEl("newPersonaRunningHubKeyStatus").textContent = configuredText(runtime, "new_persona_runninghub_api_key");

  if (qsEl("videoBaseUrl")) qsEl("videoBaseUrl").value = runtime.mulerouter_base_url || "https://api.mulerouter.ai";
  maskedVideoKeyValue = runtime.mulerouter_api_key_masked || "";
  const videoKeyInput = qsEl("videoApiKey");
  if (videoKeyInput && document.activeElement !== videoKeyInput) videoKeyInput.value = displayMaskedInput(maskedVideoKeyValue);
  if (qsEl("videoKeyStatus")) qsEl("videoKeyStatus").textContent = configuredText(runtime, "mulerouter_api_key");

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

async function saveModelConfig() {
  setBusy(true, "btnSaveModelConfig");
  setSetupMsg("正在保存模型基础配置...", true);
  try {
    const payload = {
      llm_base_url: qsEl("llmBaseUrl")?.value.trim() || "https://llm.runninghub.ai/v1",
      image_model_provider_base_url: qsEl("imageBaseUrl")?.value.trim() || "http://202.90.21.53:3008",
      new_persona_runninghub_base_url: qsEl("newPersonaRunningHubBaseUrl")?.value.trim() || "https://www.runninghub.ai",
      mulerouter_base_url: qsEl("videoBaseUrl")?.value.trim() || "https://api.mulerouter.ai",
    };
    const textKey = getRealInputValue("llmApiKey", maskedTextKeyValue);
    const imageKey = getRealInputValue("imageApiKey", maskedImageKeyValue);
    const runningHubKey = getRealInputValue("newPersonaRunningHubApiKey", maskedNewPersonaRunningHubKeyValue);
    const videoKey = getRealInputValue("videoApiKey", maskedVideoKeyValue);
    if (textKey) {
      payload.llm_api_key = textKey;
      payload.llm_api_key_gpt = textKey;
    }
    if (imageKey) payload.image_model_provider_api_key_gemini = imageKey;
    if (runningHubKey) payload.new_persona_runninghub_api_key = runningHubKey;
    if (videoKey) payload.mulerouter_api_key = videoKey;
    await savePartial(payload, "模型基础配置已保存。留空的 Key 已保留原配置。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
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

async function clearRuntimeKey(kind, buttonId, inputId, message) {
  if (!confirm(message)) return;
  setBusy(true, buttonId);
  try {
    const saved = await qsApi(`/api/quick_setup/${kind}_key`, { method: "DELETE" });
    if (inputId && qsEl(inputId)) qsEl(inputId).value = "";
    if (kind === "image") maskedImageKeyValue = "";
    if (kind === "runninghub") maskedNewPersonaRunningHubKeyValue = "";
    if (kind === "video") maskedVideoKeyValue = "";
    fillSetupForm(saved);
    setSetupMsg("Key 已清空。", true);
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

function initQuickSensitiveInputToggles() {
  [
    "telegramBotToken",
    "llmApiKey",
    "imageApiKey",
    "newPersonaRunningHubApiKey",
    "videoApiKey",
  ].forEach((id) => {
    const input = qsEl(id);
    if (!input || input.closest(".sensitive-input-wrap")) return;
    input.type = "password";
    input.autocomplete = "off";
    input.setAttribute("spellcheck", "false");
    const wrapper = document.createElement("div");
    wrapper.className = "sensitive-input-wrap";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    input.classList.add("sensitive-input");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost sensitive-toggle-btn";
    button.dataset.target = id;
    button.innerHTML = '<span class="sensitive-eye-icon" aria-hidden="true"></span>';
    button.setAttribute("aria-label", "显示密钥内容");
    button.title = "显示";
    button.setAttribute("aria-pressed", "false");
    wrapper.appendChild(button);
  });
}

function toggleQuickSensitiveInput(button) {
  const input = qsEl(button.dataset.target || "");
  if (!input) return;
  const willShow = input.type === "password";
  input.type = willShow ? "text" : "password";
  button.classList.toggle("is-visible", willShow);
  button.setAttribute("aria-label", willShow ? "隐藏密钥内容" : "显示密钥内容");
  button.title = willShow ? "隐藏" : "显示";
  button.setAttribute("aria-pressed", willShow ? "true" : "false");
  input.focus();
}

document.addEventListener("DOMContentLoaded", () => {
  bindTabs();
  initQuickSensitiveInputToggles();

  qsEl("btnReloadSetup")?.addEventListener("click", () => loadSetup(false));
  qsEl("btnSaveToken")?.addEventListener("click", saveToken);
  qsEl("btnClearToken")?.addEventListener("click", clearToken);
  qsEl("btnSaveModelConfig")?.addEventListener("click", saveModelConfig);
  qsEl("btnClearGrokKey")?.addEventListener("click", clearTextKey);
  qsEl("btnClearImageKey")?.addEventListener("click", () => clearRuntimeKey("image", "btnClearImageKey", "imageApiKey", "确定清空图片模型 Key？"));
  qsEl("btnClearRunningHubKey")?.addEventListener("click", () => clearRuntimeKey("runninghub", "btnClearRunningHubKey", "newPersonaRunningHubApiKey", "确定清空 RunningHub Key？"));
  qsEl("btnClearVideoKey")?.addEventListener("click", () => clearRuntimeKey("video", "btnClearVideoKey", "videoApiKey", "确定清空视频模型 Key？"));

  qsEl("btnStartProcess")?.addEventListener("click", () => controlProcess("start"));
  qsEl("btnStopProcess")?.addEventListener("click", () => controlProcess("stop"));
  [
    ["telegramBotToken", () => maskedTokenValue],
    ["llmApiKey", () => maskedTextKeyValue],
    ["imageApiKey", () => maskedImageKeyValue],
    ["newPersonaRunningHubApiKey", () => maskedNewPersonaRunningHubKeyValue],
    ["videoApiKey", () => maskedVideoKeyValue],
  ].forEach(([id, getter]) => {
    qsEl(id)?.addEventListener("focus", () => {
      const masked = getter();
      if (qsEl(id).value === masked || qsEl(id).value === displayMaskedInput(masked)) qsEl(id).value = "";
    });
  });
  document.addEventListener("click", (event) => {
    const target = event.target instanceof HTMLElement ? event.target : null;
    const sensitiveToggle = target?.closest(".sensitive-toggle-btn");
    if (!sensitiveToggle) return;
    toggleQuickSensitiveInput(sensitiveToggle);
  });

  loadSetup();
  setInterval(() => loadSetup(true), 8000);
});
