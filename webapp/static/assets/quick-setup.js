async function qsApi(path, opts = {}) {
  const res = await fetch(path, { credentials: "include", ...opts });
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
let maskedGrokKeyValue = "";
let operationInFlight = false;
let quickModels = [];

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

function setBusy(busy, activeButtonId = "") {
  operationInFlight = busy;
  [
    "btnSaveToken",
    "btnClearToken",
    "btnSaveGrok",
    "btnClearGrokKey",
    "btnBrowseQuickGrokModels",
    "btnAddQuickModel",
    "btnStartProcess",
    "btnStopProcess",
    "btnReloadSetup",
  ].forEach((id) => {
    const button = qsEl(id);
    if (!button) return;
    button.disabled = busy;
    if (id === activeButtonId) button.classList.toggle("is-loading", busy);
    else button.classList.remove("is-loading");
  });
}

function syncModelInputFromList() {
  const input = qsEl("llmModelOrder");
  if (input) input.value = quickModels.join(", ");
}

function renderQuickModels() {
  const wrap = qsEl("quickModelList");
  if (!wrap) return;
  if (!quickModels.length) {
    wrap.innerHTML = '<div class="admin-model-picker-status">尚未添加模型，保存時會使用默認 Grok 模型。</div>';
    syncModelInputFromList();
    return;
  }
  wrap.innerHTML = quickModels
    .map(
      (model, index) => `
        <div class="admin-model-chip">
          <span>${escapeHtml(model)}</span>
          <div class="admin-model-chip-actions">
            <button type="button" class="ghost admin-model-chip-order" data-model-action="up" data-model-index="${index}" aria-label="上移">↑</button>
            <button type="button" class="ghost admin-model-chip-order" data-model-action="down" data-model-index="${index}" aria-label="下移">↓</button>
            <button type="button" class="ghost admin-model-chip-remove" data-model-action="remove" data-model-index="${index}" aria-label="刪除">×</button>
          </div>
        </div>`,
    )
    .join("");
  syncModelInputFromList();
}

function addQuickModel(model) {
  const value = String(model || "").trim();
  if (!value) return;
  if (!quickModels.includes(value)) {
    quickModels.push(value);
    renderQuickModels();
  }
}

function setModelPickerStatus(message, isError = false) {
  const picker = qsEl("quickAvailableModelPicker");
  if (!picker) return;
  picker.hidden = false;
  picker.innerHTML = `<div class="admin-model-picker-status${isError ? " error" : ""}">${escapeHtml(message)}</div>`;
}

function renderAvailableModels(models) {
  const picker = qsEl("quickAvailableModelPicker");
  if (!picker) return;
  const items = parseModelList((Array.isArray(models) ? models : []).join(",")).sort((a, b) => a.localeCompare(b));
  picker.hidden = false;
  if (!items.length) {
    picker.innerHTML = '<div class="admin-model-picker-status">沒有查詢到可用模型。</div>';
    return;
  }
  picker.innerHTML = items
    .map((model) => `<button type="button" class="ghost admin-model-picker-option" data-quick-model="${escapeHtml(model)}">${escapeHtml(model)}</button>`)
    .join("");
}

function fillSetupForm(resp) {
  if (operationInFlight) return;
  const runtime = runtimeFromResponse(resp);
  const processInfo = resp && typeof resp.process === "object" ? resp.process : {};

  maskedTokenValue = runtime.telegram_bot_token_masked || "";
  const tokenInput = qsEl("telegramBotToken");
  if (tokenInput && document.activeElement !== tokenInput) {
    tokenInput.value = maskedTokenValue;
  }
  qsEl("botTokenStatus").textContent = configuredText(runtime, "telegram_bot_token");
  qsEl("setupBotState").textContent = runtime.telegram_bot_token_configured ? "已配置" : "尚未配置";

  qsEl("llmBaseUrl").value = runtime.llm_base_url || "https://llm.runninghub.ai/v1";
  maskedGrokKeyValue = runtime.llm_api_key_gpt_masked || runtime.llm_api_key_masked || "";
  const keyInput = qsEl("llmApiKey");
  if (keyInput && document.activeElement !== keyInput) {
    keyInput.value = maskedGrokKeyValue;
  }
  qsEl("grokKeyStatus").textContent = configuredText(runtime, "llm_api_key_gpt");
  quickModels = parseModelList(runtime.llm_model_priority_order || runtime.llm_default_model_gpt || runtime.llm_default_model || "xai/grok-4.3, grok-4.2");
  renderQuickModels();
  qsEl("setupGrokState").textContent = runtime.llm_api_key_gpt_configured || runtime.llm_api_key_configured ? "已配置" : "尚未配置";

  const desired = processInfo.desired || "-";
  const running = processInfo.running ? "運行中" : "未運行";
  const heartbeat = Number(processInfo.heartbeat_age_seconds || 0);
  let label = desired === "stopped" ? `${running} / 已要求停止` : running;
  if (processInfo.running && heartbeat > 90) label = "運行中 / 心跳延遲";
  qsEl("setupProcessState").textContent = label;
}

async function loadSetup(silent = false) {
  if (operationInFlight) return;
  if (!silent) setSetupMsg("正在讀取配置...", true);
  try {
    const status = await qsApi("/api/quick_setup/status");
    fillSetupForm(status);
    if (!silent) setSetupMsg("配置已讀取。", true);
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

function getRealInputValue(id, maskedValue) {
  const value = (qsEl(id)?.value || "").trim();
  if (!value || value === maskedValue) return "";
  return value;
}

async function saveToken() {
  setBusy(true, "btnSaveToken");
  setSetupMsg("正在保存 Bot Token，並檢查目前 Bot 進程...", true);
  setProcessMsg("更換 Token 時會先停止舊 Bot，避免殘留進程。");
  try {
    const token = getRealInputValue("telegramBotToken", maskedTokenValue);
    if (!token) throw new Error("請輸入新的 Bot Token。");
    const saved = await savePartial(
      { telegram_bot_token: token },
      "Bot Token 已保存，系統已按目前狀態完成 Bot 切換。",
    );
    fillSetupForm(saved);
    setProcessMsg(saved.process?.running ? "新 Bot 進程已啟動。" : "Token 已更新；目前 Bot 仍保持停止狀態。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
    setProcessMsg("Token 保存失敗，請檢查輸入或後台日誌。");
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function clearToken() {
  if (!confirm("確定要清空 Bot Token？清空前會停止 Bot 進程，之後 Bot 將無法連接 Telegram。")) return;
  setBusy(true, "btnClearToken");
  setSetupMsg("正在停止 Bot 並清空 Token...", true);
  setProcessMsg("正在停止 Bot 進程...");
  try {
    const saved = await qsApi("/api/quick_setup/telegram_bot_token", { method: "DELETE" });
    fillSetupForm(saved);
    setSetupMsg("Bot Token 已清空，Bot 進程已停止。", true);
    setProcessMsg("Bot 已停止。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
    setProcessMsg("清空 Token 失敗。");
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function saveGrok() {
  setBusy(true, "btnSaveGrok");
  setSetupMsg("正在保存 Grok 設定...", true);
  try {
    const manualModels = parseModelList(qsEl("llmModelOrder").value);
    quickModels = manualModels.length ? manualModels : quickModels;
    const llmModels = quickModels.length ? quickModels.join(", ") : "xai/grok-4.3, grok-4.2";
    const payload = {
      llm_base_url: qsEl("llmBaseUrl").value.trim() || "https://llm.runninghub.ai/v1",
      llm_default_model: llmModels,
      llm_default_model_gpt: llmModels,
      llm_model_priority_order: llmModels,
    };
    const key = getRealInputValue("llmApiKey", maskedGrokKeyValue);
    if (key) {
      payload.llm_api_key = key;
      payload.llm_api_key_gpt = key;
    }
    const saved = await savePartial(payload, key ? "Grok 設定與新 Key 已保存。" : "Grok 設定已保存，Key 保持原配置。");
    fillSetupForm(saved);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function clearGrokKey() {
  if (!confirm("確定要清空 Grok Key？清空後推文、提示詞理解與文案生成會無法調用模型，直到重新配置。")) return;
  setBusy(true, "btnClearGrokKey");
  setSetupMsg("正在清空 Grok Key...", true);
  try {
    const saved = await qsApi("/api/quick_setup/grok_key", { method: "DELETE" });
    maskedGrokKeyValue = "";
    const keyInput = qsEl("llmApiKey");
    if (keyInput) keyInput.value = "";
    fillSetupForm(saved);
    setSetupMsg("Grok Key 已清空。", true);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    setBusy(false);
    loadSetup(true);
  }
}

async function browseGrokModels() {
  const picker = qsEl("quickAvailableModelPicker");
  if (!picker) return;
  if (!picker.hidden && picker.children.length > 0) {
    picker.hidden = true;
    return;
  }
  setBusy(true, "btnBrowseQuickGrokModels");
  setModelPickerStatus("正在識別目前 API 可用模型...");
  try {
    const resp = await qsApi("/api/quick_setup/llm_models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        llm_base_url: qsEl("llmBaseUrl").value.trim(),
        llm_api_key: getRealInputValue("llmApiKey", maskedGrokKeyValue),
      }),
    });
    renderAvailableModels(resp.models || []);
  } catch (err) {
    setModelPickerStatus(err.detail || err.message || String(err), true);
  } finally {
    setBusy(false);
  }
}

async function controlProcess(action) {
  setBusy(true, action === "start" ? "btnStartProcess" : "btnStopProcess");
  const starting = action === "start";
  setSetupMsg(starting ? "正在啟動 Bot 進程..." : "正在停止 Bot 進程...", true);
  setProcessMsg(starting ? "正在檢查 Token，清理舊進程，準備啟動..." : "正在通知進程停止，並清理殘留 daemon...");
  try {
    const status = await qsApi("/api/quick_setup/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    fillSetupForm(status);
    setSetupMsg(starting ? "Bot 啟動流程已完成。" : "Bot 停止流程已完成。", true);
    setProcessMsg(starting ? "Bot 進程已啟動，請在 Telegram 發送 /start 測試。" : "Bot 進程已停止；/start 只會提示進入快捷配置。");
    setTimeout(() => loadSetup(true), 1800);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
    setProcessMsg(starting ? "Bot 啟動失敗，請檢查 Token 或後台日誌。" : "Bot 停止失敗，請檢查後台日誌。");
  } finally {
    setBusy(false);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  qsEl("btnReloadSetup")?.addEventListener("click", () => loadSetup(false));
  qsEl("btnSaveToken")?.addEventListener("click", saveToken);
  qsEl("btnClearToken")?.addEventListener("click", clearToken);
  qsEl("btnSaveGrok")?.addEventListener("click", saveGrok);
  qsEl("btnClearGrokKey")?.addEventListener("click", clearGrokKey);
  qsEl("btnBrowseQuickGrokModels")?.addEventListener("click", browseGrokModels);
  qsEl("btnAddQuickModel")?.addEventListener("click", () => {
    addQuickModel(qsEl("quickModelInput")?.value);
    if (qsEl("quickModelInput")) qsEl("quickModelInput").value = "";
  });
  qsEl("quickModelInput")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addQuickModel(qsEl("quickModelInput")?.value);
    qsEl("quickModelInput").value = "";
  });
  qsEl("llmModelOrder")?.addEventListener("change", () => {
    quickModels = parseModelList(qsEl("llmModelOrder").value);
    renderQuickModels();
  });
  qsEl("quickModelList")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-model-action]");
    if (!button) return;
    const index = Number(button.dataset.modelIndex);
    const action = button.dataset.modelAction;
    if (!Number.isInteger(index) || index < 0 || index >= quickModels.length) return;
    if (action === "remove") quickModels.splice(index, 1);
    if (action === "up" && index > 0) [quickModels[index - 1], quickModels[index]] = [quickModels[index], quickModels[index - 1]];
    if (action === "down" && index < quickModels.length - 1) [quickModels[index], quickModels[index + 1]] = [quickModels[index + 1], quickModels[index]];
    renderQuickModels();
  });
  qsEl("quickAvailableModelPicker")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quick-model]");
    if (!button) return;
    addQuickModel(button.dataset.quickModel);
    qsEl("quickAvailableModelPicker").hidden = true;
  });
  document.addEventListener("click", (event) => {
    const picker = qsEl("quickAvailableModelPicker");
    if (!picker || picker.hidden) return;
    if (event.target.closest("#quickAvailableModelPicker")) return;
    if (event.target.closest("#btnBrowseQuickGrokModels")) return;
    picker.hidden = true;
  });
  qsEl("btnStartProcess")?.addEventListener("click", () => controlProcess("start"));
  qsEl("btnStopProcess")?.addEventListener("click", () => controlProcess("stop"));
  qsEl("telegramBotToken")?.addEventListener("focus", () => {
    if (qsEl("telegramBotToken").value === maskedTokenValue) qsEl("telegramBotToken").value = "";
  });
  qsEl("llmApiKey")?.addEventListener("focus", () => {
    if (qsEl("llmApiKey").value === maskedGrokKeyValue) qsEl("llmApiKey").value = "";
  });
  loadSetup();
  setInterval(() => loadSetup(true), 8000);
});
