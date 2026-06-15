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

function setSetupMsg(text, ok = true) {
  const box = qsEl("quickSetupMsg");
  if (!box) return;
  box.textContent = text || "";
  box.className = `msg ${ok ? "ok" : "err"}`;
}

function configuredText(runtime, key) {
  return runtime[`${key}_configured`] ? `已配置：${runtime[`${key}_masked`] || "已保存"}` : "尚未配置";
}

function runtimeFromResponse(resp) {
  if (resp && resp.runtime_config && typeof resp.runtime_config === "object") return resp.runtime_config;
  return resp && typeof resp === "object" ? resp : {};
}

function fillSetupForm(resp) {
  const runtime = runtimeFromResponse(resp);
  const processInfo = resp && typeof resp.process === "object" ? resp.process : {};

  qsEl("telegramBotToken").value = "";
  qsEl("botTokenStatus").textContent = configuredText(runtime, "telegram_bot_token");
  qsEl("setupBotState").textContent = runtime.telegram_bot_token_configured ? "已配置" : "尚未配置";

  qsEl("llmBaseUrl").value = runtime.llm_base_url || "https://llm.runninghub.ai/v1";
  qsEl("llmApiKey").value = "";
  qsEl("llmModelOrder").value = runtime.llm_model_priority_order || runtime.llm_default_model_gpt || runtime.llm_default_model || "xai/grok-4.3, grok-4.2";
  qsEl("setupGrokState").textContent = runtime.llm_api_key_gpt_configured || runtime.llm_api_key_configured ? "已配置" : "尚未配置";

  const desired = processInfo.desired || "-";
  const running = processInfo.running ? "運行中" : "未運行";
  qsEl("setupProcessState").textContent = desired === "stopped" ? `${running} / 已要求停止` : running;
}

async function loadSetup(silent = false) {
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
}

async function saveToken() {
  const button = qsEl("btnSaveToken");
  button.disabled = true;
  setSetupMsg("正在保存 Bot Token...", true);
  try {
    const token = qsEl("telegramBotToken").value.trim();
    if (!token) throw new Error("請先輸入 Bot Token。");
    await savePartial({ telegram_bot_token: token }, "Bot Token 已保存並同步到後台。需要立即切換 Bot 時，請停止後再啟動 Bot 進程。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    button.disabled = false;
  }
}

async function clearToken() {
  if (!confirm("確定要清空 Bot Token？清空後 Bot 進程將無法連線 Telegram。")) return;
  const button = qsEl("btnClearToken");
  button.disabled = true;
  setSetupMsg("正在清空 Bot Token...", true);
  try {
    const saved = await qsApi("/api/quick_setup/telegram_bot_token", { method: "DELETE" });
    fillSetupForm(saved);
    setSetupMsg("Bot Token 已清空。", true);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    button.disabled = false;
  }
}

async function saveGrok() {
  const button = qsEl("btnSaveGrok");
  button.disabled = true;
  setSetupMsg("正在保存 Grok 設定...", true);
  try {
    const llmModels = qsEl("llmModelOrder").value.trim() || "xai/grok-4.3, grok-4.2";
    const payload = {
      llm_base_url: qsEl("llmBaseUrl").value.trim() || "https://llm.runninghub.ai/v1",
      llm_default_model: llmModels,
      llm_default_model_gpt: llmModels,
      llm_model_priority_order: llmModels,
    };
    const key = qsEl("llmApiKey").value.trim();
    if (key) {
      payload.llm_api_key = key;
      payload.llm_api_key_gpt = key;
    }
    await savePartial(payload, "Grok 設定已保存並同步到後台。");
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    button.disabled = false;
  }
}

async function controlProcess(action) {
  const startButton = qsEl("btnStartProcess");
  const stopButton = qsEl("btnStopProcess");
  startButton.disabled = true;
  stopButton.disabled = true;
  setSetupMsg(action === "start" ? "正在要求啟動 Bot 進程..." : "正在要求停止 Bot 進程...", true);
  try {
    const status = await qsApi("/api/quick_setup/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    fillSetupForm(status);
    setSetupMsg(action === "start" ? "已送出啟動指令，請稍候狀態刷新。" : "已送出停止指令，Web 設定面板會保持可用。", true);
    setTimeout(() => loadSetup(true), 2500);
  } catch (err) {
    setSetupMsg(err.detail || err.message || String(err), false);
  } finally {
    startButton.disabled = false;
    stopButton.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  qsEl("btnReloadSetup")?.addEventListener("click", () => loadSetup(false));
  qsEl("btnSaveToken")?.addEventListener("click", saveToken);
  qsEl("btnClearToken")?.addEventListener("click", clearToken);
  qsEl("btnSaveGrok")?.addEventListener("click", saveGrok);
  qsEl("btnStartProcess")?.addEventListener("click", () => controlProcess("start"));
  qsEl("btnStopProcess")?.addEventListener("click", () => controlProcess("stop"));
  loadSetup();
  setInterval(() => loadSetup(true), 8000);
});
