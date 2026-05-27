function el(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = el(id);
  if (!node) return;
  node.textContent = String(value == null ? "" : value);
}

const ADMIN_PAGE_LABELS = {
  overview: "运营概览",
  users: "客户账号",
  tasks: "生成记录",
  pricing: "额度与计费",
  runtime: "系统配置",
  account: "账号设置",
};

const SENSITIVE_RUNTIME_INPUT_IDS = [
  "rtLlmApiKeyGpt",
  "rtImageModelProviderApiKeyGemini",
  "rtImageModelProviderApiKeyGpt",
  "rtRemoteComfyGatewayToken",
];

function normalizeAdminPage(value) {
  const raw = String(value || "").replace(/^#/, "").trim();
  const mapped = ADMIN_PAGE_ALIASES[raw] || raw.replace(/^admin-/, "");
  return ADMIN_PAGES.has(mapped) ? mapped : "overview";
}

function readAdminPageFromHash() {
  return normalizeAdminPage(location.hash || "");
}

function setActiveAdminPage(page, updateHash = true) {
  const nextPage = normalizeAdminPage(page);
  adminState.activePage = nextPage;
  const pageLabel = ADMIN_PAGE_LABELS[nextPage] || "运营概览";
  document.querySelectorAll("[data-page]").forEach((node) => {
    const active = String(node.dataset.page || "") === nextPage;
    node.classList.toggle("is-active", active);
    node.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll("[data-page-view]").forEach((node) => {
    const active = String(node.dataset.pageView || "") === nextPage;
    node.classList.toggle("is-active", active);
    node.style.display = active ? "" : "none";
    node.setAttribute("aria-hidden", active ? "false" : "true");
  });
  setText("adminCurrentPageLabel", pageLabel);
  document.title = `${pageLabel} - 运营后台 - 电商带货视频生成平台`;
  const targetHash = `admin-${nextPage}`;
  if (updateHash && String(location.hash || "").replace(/^#/, "") !== targetHash) {
    location.hash = targetHash;
  }
}

async function api(path, opts = {}) {
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

function setMsg(id, message, ok = true) {
  const node = el(id);
  if (!node) return;
  node.textContent = message || "";
  node.className = `msg ${ok ? "ok" : "err"}`;
}

function clearAccountMsgs() {
  setMsg("accountUsernameMsg", "");
  setMsg("accountPasswordMsg", "");
}

function getErrorMessage(err) {
  if (!err) return "未知错误";
  if (typeof err === "string") return err;
  if (typeof err.detail === "string" && err.detail.trim()) return err.detail.trim();
  if (typeof err.message === "string" && err.message.trim()) return err.message.trim();
  return String(err);
}

function formatRuntimeConfigError(action, err) {
  const detail = getErrorMessage(err);
  if (detail.includes("运行配置文件")) return `${action}失败：${detail}`;
  return `${action}运行配置失败：${detail}`;
}

function runtimeConfigResponseToConfig(resp) {
  if (resp && typeof resp.runtime_config === "object" && resp.runtime_config) {
    return resp.runtime_config;
  }
  if (resp && typeof resp === "object") return resp;
  return null;
}

function parseModelList(value) {
  return String(value || "")
    .split(/\s*[,，\n]+\s*/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function stringifyModelList(items) {
  return (Array.isArray(items) ? items : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .join(", ");
}

const RUNTIME_MODEL_DRAFT_KEY = "runtime_model_candidates_draft_v1";

function uniqueItems(items) {
  return Array.from(new Set((Array.isArray(items) ? items : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean)));
}

function readModelDraft() {
  try {
    const raw = localStorage.getItem(RUNTIME_MODEL_DRAFT_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function writeModelDraft() {
  try {
    localStorage.setItem(RUNTIME_MODEL_DRAFT_KEY, JSON.stringify({
      llmGeminiModels: uniqueItems(adminState.llmGeminiModels),
      llmGptModels: uniqueItems(adminState.llmGptModels),
      imageGeminiModels: uniqueItems(adminState.imageGeminiModels),
      imageGptModels: uniqueItems(adminState.imageGptModels),
      llmPriorityModels: uniqueItems(adminState.llmPriorityModels),
      imagePriorityModels: uniqueItems(adminState.imagePriorityModels),
    }));
  } catch {
    // localStorage can be unavailable in private browsing; config save still works.
  }
}

function clearModelDraft() {
  try {
    localStorage.removeItem(RUNTIME_MODEL_DRAFT_KEY);
  } catch {
    // Ignore storage failures.
  }
}

function mergeModelDraft() {
  const draft = readModelDraft();
  if (!draft) return false;
  let changed = false;
  ["llmGeminiModels", "llmGptModels", "imageGeminiModels", "imageGptModels", "llmPriorityModels", "imagePriorityModels"].forEach((key) => {
    const before = uniqueItems(adminState[key]);
    const after = uniqueItems([...before, ...(Array.isArray(draft[key]) ? draft[key] : [])]);
    adminState[key] = after;
    if (after.length !== before.length) changed = true;
  });
  return changed;
}

function normalizeWorkflowChain(value, fallback = []) {
  const source = Array.isArray(value)
    ? value
    : String(value || "")
      .replace(/->/g, ",")
      .replace(/>/g, ",")
      .split(",");
  const items = source
    .map((item) => {
      if (item && typeof item === "object") {
        const stage = parseWorkflowStage(item);
        return buildWorkflowStageValue(stage.type, stage.value);
      }
      return String(item || "").trim();
    })
    .filter(Boolean);
  if (items.length) return items;
  return (Array.isArray(fallback) ? fallback : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean);
}

const CLOSED_IMAGE_STAGE_PREFIX = "closed_image_model:";
const CLOSED_LLM_STAGE_PREFIX = "closed_llm_model:";

function parseWorkflowStage(item) {
  if (item && typeof item === "object") {
    const type = String(item.type || item.provider || "").trim();
    const value = String(item.value || item.model || item.workflow_id || item.id || "").trim();
    if (["closed_image_model", "closed_model_api", "closed_model", "image_model"].includes(type)) {
      return { type: "closed_image_model", value };
    }
    if (["closed_llm_model", "closed_text_model", "llm_model", "text_model"].includes(type)) {
      return { type: "closed_llm_model", value };
    }
    return { type: "closed_image_model", value };
  }
  const text = String(item || "").trim();
  if (text.startsWith(CLOSED_IMAGE_STAGE_PREFIX)) {
    return { type: "closed_image_model", value: text.slice(CLOSED_IMAGE_STAGE_PREFIX.length).trim() };
  }
  if (text.startsWith(CLOSED_LLM_STAGE_PREFIX)) {
    return { type: "closed_llm_model", value: text.slice(CLOSED_LLM_STAGE_PREFIX.length).trim() };
  }
  return { type: "closed_image_model", value: text };
}

function buildWorkflowStageValue(type, value) {
  const stageValue = String(value || "").trim();
  if (!stageValue) return "";
  if (type === "closed_image_model") return `${CLOSED_IMAGE_STAGE_PREFIX}${stageValue}`;
  if (type === "closed_llm_model") return `${CLOSED_LLM_STAGE_PREFIX}${stageValue}`;
  return stageValue;
}

function looksLikeLegacyWorkflowId(value) {
  return /^\d{10,}$/.test(String(value || "").trim());
}

function imageModelOptions() {
  const items = [
    ...(Array.isArray(adminState.imageGeminiModels) ? adminState.imageGeminiModels : []),
    ...(Array.isArray(adminState.imageGptModels) ? adminState.imageGptModels : []),
  ]
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  return Array.from(new Set(items));
}

function llmModelOptions() {
  const items = [
    ...(Array.isArray(adminState.llmGeminiModels) ? adminState.llmGeminiModels : []),
    ...(Array.isArray(adminState.llmGptModels) ? adminState.llmGptModels : []),
  ]
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  return Array.from(new Set(items));
}

function modelCatalogForPriority(type) {
  if (type === "image") return imageModelOptions();
  return llmModelOptions();
}

function normalizePriorityList(priorityItems, catalogItems, fallbackItems) {
  const normalized = [];
  const seen = new Set();
  const addItem = (value) => {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    normalized.push(text);
  };
  (Array.isArray(priorityItems) ? priorityItems : []).forEach(addItem);
  (Array.isArray(catalogItems) ? catalogItems : []).forEach(addItem);
  if (normalized.length === 0) {
    (Array.isArray(fallbackItems) ? fallbackItems : []).forEach(addItem);
  }
  return normalized;
}

function syncPriorityModelsFromCatalog(type) {
  if (type === "image") {
    adminState.imagePriorityModels = normalizePriorityList(
      adminState.imagePriorityModels,
      imageModelOptions(),
      ["gemini-3-pro-image-preview"],
    );
    return;
  }
  adminState.llmPriorityModels = normalizePriorityList(
    adminState.llmPriorityModels,
    llmModelOptions(),
    ["grok-4.2"],
  );
}

function defaultClosedImageModel() {
  const priority = Array.isArray(adminState.imagePriorityModels) ? adminState.imagePriorityModels : [];
  return priority[0] || imageModelOptions()[0] || "gemini-3-pro-image-preview";
}

function defaultClosedLlmModel() {
  const priority = Array.isArray(adminState.llmPriorityModels) ? adminState.llmPriorityModels : [];
  return priority[0] || llmModelOptions()[0] || "grok-4.2";
}

function normalizeWorkflowStageForType(type, value) {
  const stageType = String(type || "closed_image_model").trim();
  const text = String(value || "").trim();
  if (stageType === "closed_image_model") {
    return looksLikeLegacyWorkflowId(text) || !text ? defaultClosedImageModel() : text;
  }
  if (stageType === "closed_llm_model") {
    return looksLikeLegacyWorkflowId(text) || !text ? defaultClosedLlmModel() : text;
  }
  return (
    text.startsWith(CLOSED_IMAGE_STAGE_PREFIX)
    || text.startsWith(CLOSED_LLM_STAGE_PREFIX)
  ) ? "" : text;
}

function lastWorkflowStep(items) {
  const normalized = normalizeWorkflowChain(items);
  return normalized.length ? normalized[normalized.length - 1] : "";
}

function renderModelList(listKey, wrapId) {
  const wrap = el(wrapId);
  if (!wrap) return;
  wrap.innerHTML = "";
  (Array.isArray(adminState[listKey]) ? adminState[listKey] : []).forEach((model, index) => {
    const chip = document.createElement("div");
    chip.className = "admin-model-chip";
    chip.innerHTML = `<span>${escapeHtml(model)}</span><button type="button" class="ghost admin-model-chip-remove" data-list="${escapeHtml(listKey)}" data-idx="${index}" aria-label="删除模型">×</button>`;
    wrap.appendChild(chip);
  });
}

function renderPriorityModelList(listKey, wrapId) {
  const wrap = el(wrapId);
  if (!wrap) return;
  wrap.innerHTML = "";
  (Array.isArray(adminState[listKey]) ? adminState[listKey] : []).forEach((model, index) => {
    const chip = document.createElement("div");
    chip.className = "admin-model-chip";
    chip.innerHTML = `
      <span>${escapeHtml(model)}</span>
      <div class="admin-model-chip-actions">
        <button type="button" class="ghost admin-model-chip-order" data-priority-list="${escapeHtml(listKey)}" data-priority-idx="${index}" data-priority-action="up" aria-label="上移">↑</button>
        <button type="button" class="ghost admin-model-chip-order" data-priority-list="${escapeHtml(listKey)}" data-priority-idx="${index}" data-priority-action="down" aria-label="下移">↓</button>
        <button type="button" class="ghost admin-model-chip-remove" data-list="${escapeHtml(listKey)}" data-idx="${index}" aria-label="删除模型">×</button>
      </div>`;
    wrap.appendChild(chip);
  });
}

function renderAllModelLists() {
  syncPriorityModelsFromCatalog("llm");
  syncPriorityModelsFromCatalog("image");
  renderModelList("llmGeminiModels", "rtLlmGeminiModelList");
  renderModelList("llmGptModels", "rtLlmGptModelList");
  renderModelList("imageGeminiModels", "rtImageGeminiModelList");
  renderModelList("imageGptModels", "rtImageGptModelList");
  renderPriorityModelList("llmPriorityModels", "rtLlmPriorityModelList");
  renderPriorityModelList("imagePriorityModels", "rtImagePriorityModelList");
  renderModelSummaries();
}

function firstModel(listKey) {
  const items = Array.isArray(adminState[listKey]) ? adminState[listKey] : [];
  const first = String(items[0] || "").trim();
  return first;
}

function buildModelSummary(geminiListKey, gptListKey, label) {
  const priorityKey = label === "文字模型" ? "llmPriorityModels" : "imagePriorityModels";
  const priority = Array.isArray(adminState[priorityKey]) ? adminState[priorityKey] : [];
  if (priority.length > 0) return `当前默认执行：按优先级顺序依次尝试，当前首选 ${priority[0]}`;
  const geminiModel = firstModel(geminiListKey);
  const gptModel = firstModel(gptListKey);
  if (geminiModel) return `当前默认执行：Gemini · ${label}优先使用 ${geminiModel}`;
  if (gptModel) return `当前默认执行：GPT · ${label}回退使用 ${gptModel}`;
  return `当前默认执行：未配置 ${label}候选模型`;
}

function buildLlmModelSummary() {
  const priority = Array.isArray(adminState.llmPriorityModels) ? adminState.llmPriorityModels : [];
  if (priority.length > 0) return `当前 Grok 执行模型：${priority[0]}`;
  const grokModel = firstModel("llmGptModels") || firstModel("llmGeminiModels");
  if (grokModel) return `当前 Grok 执行模型：${grokModel}`;
  return "当前未配置 Grok 文字模型";
}

function renderModelSummaries() {
  const llmSummary = el("rtLlmModelSummary");
  if (llmSummary) {
    llmSummary.textContent = buildLlmModelSummary();
  }
  const imageSummary = el("rtImageModelSummary");
  if (imageSummary) {
    imageSummary.textContent = buildModelSummary("imageGeminiModels", "imageGptModels", "图片模型");
  }
}

function addModelFromInput(listKey, inputId) {
  const input = el(inputId);
  if (!input) return;
  const value = String(input.value || "").trim();
  if (!value) return;
  if (!Array.isArray(adminState[listKey])) {
    adminState[listKey] = [];
  }
  if (!adminState[listKey].includes(value)) {
    adminState[listKey].push(value);
    if (listKey === "llmGeminiModels" || listKey === "llmGptModels") {
      syncPriorityModelsFromCatalog("llm");
    }
    if (listKey === "imageGeminiModels" || listKey === "imageGptModels") {
      syncPriorityModelsFromCatalog("image");
    }
    writeModelDraft();
    renderAllModelLists();
  }
  input.value = "";
}

function setLlmModelPickerStatus(message, isError = false) {
  const picker = el("rtLlmGrokModelPicker");
  if (!picker) return;
  picker.hidden = false;
  picker.innerHTML = `<div class="admin-model-picker-status${isError ? " error" : ""}">${escapeHtml(message)}</div>`;
}

function hideLlmModelPicker() {
  const picker = el("rtLlmGrokModelPicker");
  if (picker) picker.hidden = true;
}

function addLlmModelFromPicker(model) {
  const value = String(model || "").trim();
  if (!value) return;
  if (!Array.isArray(adminState.llmGptModels)) adminState.llmGptModels = [];
  if (!Array.isArray(adminState.llmPriorityModels)) adminState.llmPriorityModels = [];
  if (!adminState.llmGptModels.includes(value)) adminState.llmGptModels.push(value);
  if (!adminState.llmPriorityModels.includes(value)) adminState.llmPriorityModels.push(value);
  syncPriorityModelsFromCatalog("llm");
  writeModelDraft();
  renderAllModelLists();
  hideLlmModelPicker();
  setMsg("runtimeMsg", `已加入 Grok 模型：${value}`, true);
}

function renderAvailableLlmModels(models) {
  const picker = el("rtLlmGrokModelPicker");
  if (!picker) return;
  const items = uniqueItems(Array.isArray(models) ? models : [])
    .filter((model) => /grok/i.test(model))
    .sort((a, b) => a.localeCompare(b));
  picker.hidden = false;
  if (!items.length) {
    picker.innerHTML = `<div class="admin-model-picker-status">没有查询到 Grok 可用模型</div>`;
    return;
  }
  picker.innerHTML = items
    .map((model) => `<button type="button" class="ghost admin-model-picker-option" data-llm-model="${escapeHtml(model)}">${escapeHtml(model)}</button>`)
    .join("");
}

async function toggleAvailableLlmModels() {
  const picker = el("rtLlmGrokModelPicker");
  if (!picker) return;
  if (!picker.hidden && picker.children.length > 0) {
    hideLlmModelPicker();
    return;
  }
  const baseUrl = el("rtLlmBaseUrl").value.trim();
  const apiKey = el("rtLlmApiKeyGpt").value.trim() || el("rtLlmApiKeyGemini").value.trim();
  if (!baseUrl || !apiKey) {
    setLlmModelPickerStatus("请先填写 Grok API Base URL 和密钥", true);
    return;
  }
  setLlmModelPickerStatus("正在查询可用 Grok 模型...");
  try {
    const resp = await api("/api/admin/llm_models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm_base_url: baseUrl, llm_api_key: apiKey }),
    });
    renderAvailableLlmModels(resp.models || []);
  } catch (err) {
    setLlmModelPickerStatus(err.detail || err.message || String(err), true);
  }
}

function addPriorityModelFromInput(listKey, inputId, type) {
  const input = el(inputId);
  if (!input) return;
  const value = String(input.value || "").trim();
  if (!value) return;
  if (!Array.isArray(adminState[listKey])) {
    adminState[listKey] = [];
  }
  if (!adminState[listKey].includes(value)) {
    adminState[listKey].push(value);
  }
  syncPriorityModelsFromCatalog(type);
  writeModelDraft();
  renderAllModelLists();
  input.value = "";
}

const TASK_POLL_INTERVAL_MS = 10000;
const taskState = {
  rows: [],
  inspectText: "",
};
const adminState = {
  rechargeTarget: null,
  activePage: "overview",
  llmGeminiModels: [],
  llmGptModels: [],
  imageGeminiModels: [],
  imageGptModels: [],
  llmPriorityModels: [],
  imagePriorityModels: [],
  workflowChains: {},
  remoteComfyWorkflowMappings: {},
  remoteComfyWorkflows: [],
  tgTrustedUsers: [],
};
const REMOTE_COMFY_TASKS = [
  ["text_to_image", "文字生成图片"],
  ["image_generate", "图片生成"],
  ["replace_model", "替换模特"],
  ["replace_product", "替换商品"],
  ["replace_productANDmodel", "联合替换"],
  ["create_audio", "生成音频"],
  ["create_video", "生成视频"],
  ["commerce_video", "带货视频"],
  ["get_nano_banana", "图片编辑"],
];
const ADMIN_PAGES = new Set(["overview", "users", "tasks", "pricing", "runtime", "account"]);
const ADMIN_PAGE_ALIASES = {
  secOverview: "overview",
  secUsers: "users",
  secTasks: "tasks",
  secPricing: "pricing",
  secRuntime: "runtime",
  secAccount: "account",
};
const WORKFLOW_CHAIN_META = [];
const WORKFLOW_CHAIN_META_BY_KEY = Object.fromEntries(
  WORKFLOW_CHAIN_META.map((item) => [item.key, item]),
);
const WORKFLOW_CHAIN_CONTAINER_IDS = Object.fromEntries(
  WORKFLOW_CHAIN_META.map((item) => [item.key, item.containerId]),
);

function syncWorkflowChainFromDom(key) {
  const container = el(WORKFLOW_CHAIN_CONTAINER_IDS[key]);
  if (!container) return normalizeWorkflowChain(adminState.workflowChains[key]);
  const values = Array.from(container.querySelectorAll(".workflow-step-row"))
    .map((row) => {
      const input = row.querySelector(`[data-chain-input="${key}"]`);
      const typeNode = row.querySelector(`[data-chain-type="${key}"]`);
      const modelNode = row.querySelector(`[data-chain-model="${key}"]`);
      const value = input ? String(input.value || "").trim() : "";
      const modelValue = modelNode ? String(modelNode.value || "").trim() : "";
      const type = typeNode ? String(typeNode.value || "closed_image_model") : "closed_image_model";
      return buildWorkflowStageValue(type, normalizeWorkflowStageForType(type, modelNode ? modelValue : value));
    });
  adminState.workflowChains[key] = values.length ? values : [""];
  return adminState.workflowChains[key];
}

function renderWorkflowChain(key) {
  const container = el(WORKFLOW_CHAIN_CONTAINER_IDS[key]);
  if (!container) return;
  const meta = WORKFLOW_CHAIN_META_BY_KEY[key] || {};
  const rawItems = Array.isArray(adminState.workflowChains[key]) ? adminState.workflowChains[key] : [];
  const items = rawItems.length ? rawItems : [""];
  adminState.workflowChains[key] = items;
  container.innerHTML = items.map((value, index) => {
    const stage = parseWorkflowStage(value);
    const typeOptions = [];
    if (meta.supportsClosedLlmModel) {
      typeOptions.push(`<option value="closed_llm_model"${stage.type === "closed_llm_model" ? " selected" : ""}>闭源文字模型</option>`);
    }
    if (meta.supportsClosedImageModel) {
      typeOptions.push(`<option value="closed_image_model"${stage.type === "closed_image_model" ? " selected" : ""}>闭源图片模型</option>`);
    }
    const stageTypeOptions = typeOptions.length > 1
      ? `
        <select class="workflow-step-type" data-chain-type="${key}" data-idx="${index}" aria-label="步骤类型">
          ${typeOptions.join("")}
        </select>
      `
      : "";
    const modelOptions = stage.type === "closed_llm_model" ? llmModelOptions() : imageModelOptions();
    let stageValue = stage.value;
    if (stage.type === "closed_image_model" || stage.type === "closed_llm_model") {
      stageValue = normalizeWorkflowStageForType(stage.type, stage.value);
      if (stageValue && !modelOptions.includes(stageValue)) modelOptions.push(stageValue);
    }
    let valueControl = "";
    if (stage.type === "closed_image_model" || stage.type === "closed_llm_model") {
      valueControl = `
        <select class="workflow-step-value" data-chain-model="${key}" data-idx="${index}" aria-label="${stage.type === "closed_llm_model" ? "选择闭源文字模型" : "选择闭源图片模型"}">
          ${modelOptions.map((model) => `<option value="${escapeHtml(model)}"${model === stageValue ? " selected" : ""}>${escapeHtml(model)}</option>`).join("")}
        </select>
      `;
    } else {
      valueControl = `
        <input
          type="text"
          value="${escapeHtml(stageValue)}"
          data-chain-input="${key}"
          data-idx="${index}"
          placeholder="Workflow ID"
        >
      `;
    }
    return `
    <div class="workflow-step-item">
      <div class="workflow-step-row${stageTypeOptions ? " workflow-step-row-with-type" : ""}">
        <span class="workflow-step-index">步骤 ${index + 1}</span>
        ${stageTypeOptions}
        ${valueControl}
        <div class="workflow-step-actions">
          <button type="button" class="ghost workflow-step-btn" data-workflow-action="insert" data-chain="${key}" data-idx="${index}" aria-label="在后面新增一步">+</button>
          <button type="button" class="ghost workflow-step-btn" data-workflow-action="remove" data-chain="${key}" data-idx="${index}" aria-label="删除当前步骤">-</button>
        </div>
      </div>
      ${index < items.length - 1 ? '<div class="workflow-step-sep">&gt;</div>' : ""}
    </div>
  `;
  }).join("");
}

function renderAllWorkflowChains() {
  WORKFLOW_CHAIN_META.forEach((item) => renderWorkflowChain(item.key));
}

function insertWorkflowChainStep(key, index) {
  const items = syncWorkflowChainFromDom(key).slice();
  items.splice(index + 1, 0, "");
  adminState.workflowChains[key] = items;
  renderWorkflowChain(key);
}

function removeWorkflowChainStep(key, index) {
  const items = syncWorkflowChainFromDom(key).slice();
  if (items.length <= 1) {
    adminState.workflowChains[key] = [""];
  } else {
    items.splice(index, 1);
    adminState.workflowChains[key] = items.length ? items : [""];
  }
  renderWorkflowChain(key);
}

function collectWorkflowChains() {
  const result = {};
  WORKFLOW_CHAIN_META.forEach((item) => {
    result[item.key] = normalizeWorkflowChain(syncWorkflowChainFromDom(item.key));
  });
  return result;
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch] || ch));
}

function safeJson(value) {
  try {
    return JSON.stringify(value == null ? {} : value, null, 2);
  } catch {
    return String(value == null ? "" : value);
  }
}

function formatTime(ts) {
  if (!ts) return "-";
  return new Date(Number(ts) * 1000).toLocaleString();
}

function statusPill(status) {
  const s = String(status || "").trim() || "unknown";
  const labels = { success: "已完成", failed: "失败", queued: "排队中", running: "生成中" };
  if (s === "success") return `<span class="pill success">${escapeHtml(labels[s])}</span>`;
  if (s === "failed") return `<span class="pill failed">${escapeHtml(labels[s])}</span>`;
  if (s === "queued") return `<span class="pill queued">${escapeHtml(labels[s])}</span>`;
  return `<span class="pill running">${escapeHtml(labels[s] || s)}</span>`;
}

function oneLine(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function taskStatusDetail(t) {
  const total = Number(t && t.total_count);
  const success = Number(t && t.success_count);
  const failed = Number(t && t.failed_count);
  const firstError = oneLine((t && t.first_error) || (t && t.error) || "");
  if (total > 0) {
    const parts = [`成功 ${success}/${total}`];
    if (failed > 0) parts.push(`失败 ${failed}`);
    if (firstError) parts.push(`首个失败：${firstError}`);
    return parts.join(" | ");
  }
  return firstError || "";
}

function taskStatusCell(t) {
  const detail = taskStatusDetail(t);
  return `${statusPill(t.status)}${detail ? `<div class="small">${detail}</div>` : ""}`;
}

function runninghubCell(t) {
  const ids = Array.isArray(t && t.runninghub_task_ids)
    ? t.runninghub_task_ids.map((x) => oneLine(x)).filter(Boolean)
    : [];
  if (!ids.length) {
    const single = oneLine(t && t.runninghub_task_id);
    return single || "-";
  }
  return ids.map((id) => `<div class="small">${id}</div>`).join("");
}

function runninghubList(t) {
  const ids = Array.isArray(t && t.runninghub_task_ids)
    ? t.runninghub_task_ids.map((x) => oneLine(x)).filter(Boolean)
    : [];
  if (ids.length) return ids;
  const single = oneLine(t && t.runninghub_task_id);
  return single ? [single] : [];
}

function buildExecutionTraceText(groups) {
  const items = Array.isArray(groups) ? groups : [];
  const lines = [];
  items.forEach((group) => {
    if (!group || typeof group !== "object") return;
    lines.push(`${group.title || "执行链路"}`);
    if (group.status) lines.push(`  状态：${group.status}`);
    if (group.message) lines.push(`  说明：${group.message}`);
    if (group.final_output_path) lines.push(`  最终产物：${group.final_output_path}`);
    const steps = Array.isArray(group.steps) ? group.steps : [];
    steps.forEach((step) => {
      if (!step || typeof step !== "object") return;
      const stepParts = [
        `步骤 ${step.step || "-"}`,
        step.workflow_id ? `流程=${step.workflow_id}` : "",
        step.runninghub_task_id ? `任务=${step.runninghub_task_id}` : "",
        step.status ? `状态=${step.status}` : "",
      ].filter(Boolean);
      lines.push(`  - ${stepParts.join(" | ")}`);
      if (step.input_ref) lines.push(`    输入：${step.input_ref}`);
      if (step.output_path) lines.push(`    输出：${step.output_path}`);
      if (step.uploaded_ref) lines.push(`    续链上传：${step.uploaded_ref}`);
      if (step.message) lines.push(`    说明：${step.message}`);
    });
    lines.push("");
  });
  return lines.filter((line, index, arr) => !(line === "" && arr[index - 1] === "")).join("\n").trim();
}

function buildExecutionTraceHtml(groups) {
  const items = Array.isArray(groups) ? groups : [];
  if (!items.length) {
    return `<div class="task-empty task-empty-inline">暂无执行链路详情</div>`;
  }
  return items.map((group) => {
    const steps = Array.isArray(group && group.steps) ? group.steps : [];
    const stepsHtml = steps.length
      ? steps.map((step) => {
        const meta = [
          step.workflow_id ? `流程：${oneLine(step.workflow_id)}` : "",
          step.runninghub_task_id ? `任务：${oneLine(step.runninghub_task_id)}` : "",
          step.status ? `状态：${oneLine(step.status)}` : "",
        ].filter(Boolean);
        const refs = [
          step.input_ref ? `输入：${oneLine(step.input_ref)}` : "",
          step.output_path ? `输出：${oneLine(step.output_path)}` : "",
          step.uploaded_ref ? `续链上传：${oneLine(step.uploaded_ref)}` : "",
          step.message ? `说明：${oneLine(step.message)}` : "",
        ].filter(Boolean);
        return `
          <article class="inspect-log-item">
            <div class="inspect-log-meta">
              <span>步骤 ${escapeHtml(String(step.step || "-"))}</span>
              ${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
            </div>
            ${refs.length ? `<div class="inspect-log-extra">${escapeHtml(refs.join(" | "))}</div>` : ""}
          </article>
        `;
      }).join("")
      : `<div class="task-empty task-empty-inline">暂无步骤明细</div>`;
    return `
        <div class="inspect-section-title">${escapeHtml(group.title || "执行链路")}</div>
        ${group.final_output_path ? `<div class="small" style="margin-bottom:8px">最终产物：${escapeHtml(oneLine(group.final_output_path))}</div>` : ""}
        ${group.message ? `<div class="small" style="margin-bottom:8px">说明：${escapeHtml(oneLine(group.message))}</div>` : ""}
        <div class="inspect-log-list">${stepsHtml}</div>
    `;
  }).join("");
}

function workflowCell(t) {
  const workflowName = oneLine(t.workflow_name || t.type || "-");
  const workflowId = oneLine(t.workflow_id || "-");
  const taskType = oneLine(t.type || "-");
  return `
    <div><strong>${workflowName}</strong></div>
    <div class="small">生成类型：${taskType}</div>
    <div class="small">内部流程编号：${workflowId}</div>
  `;
}

function taskActionOptions(task) {
  const status = String((task && task.status) || "");
  const options = [
    `<option value="">请选择</option>`,
    `<option value="detail">查看生成详情</option>`,
    `<option value="logs">查看处理记录</option>`,
    `<option value="export_logs">导出处理记录</option>`,
  ];
  if (task && task.has_download) {
    options.push(`<option value="download">下载结果</option>`);
  }
  if (String(status || "") === "failed") {
    options.push(`<option value="retry">重新生成</option>`);
  }
  options.push(`<option value="delete_task">删除生成记录</option>`);
  return options.join("");
}

function buildTaskDetailText(data) {
  const logs = Array.isArray(data.logs) ? data.logs : [];
  const executionTraceText = buildExecutionTraceText(data.execution_trace);
  const lines = [
    `生成编号：${data.id || "-"}`,
    `客户ID：${data.user_id || "-"}`,
    `生成类型：${data.type || "-"}`,
    `内部流程：${data.workflow_name || "-"}`,
    `内部流程编号：${data.workflow_id || "-"}`,
    `链路摘要：${data.workflow_chain_summary || "-"}`,
    `供应商记录编号：${data.runninghub_task_id || "-"}`,
    `供应商记录编号列表：${Array.isArray(data.runninghub_task_ids) && data.runninghub_task_ids.length ? data.runninghub_task_ids.join(", ") : "-"}`,
    `状态：${data.status || "-"}`,
    `批量结果：${data.total_count ? `成功 ${data.success_count || 0}/${data.total_count}，失败 ${data.failed_count || 0}` : "-"}`,
    `额度消耗(分)：${data.cost_cents || 0}`,
    `创建时间：${formatTime(data.created_at)}`,
    `更新时间：${formatTime(data.updated_at)}`,
    `错误：${data.error || "-"}`,
    `最近分析：${data.analysis_summary || "-"}`,
    "",
    "输入：",
    JSON.stringify(data.input || {}, null, 2),
    "",
    "输出：",
    JSON.stringify(data.output || {}, null, 2),
    "",
    "用量：",
    JSON.stringify(data.usage || {}, null, 2),
    "",
    "执行链路：",
    executionTraceText || "暂无执行链路详情",
    "",
    "详细处理记录：",
  ];
  logs.forEach((it) => {
    lines.push(`[${formatTime(it.created_at)}] [${it.kind}] ${oneLine(it.message || "-")}`);
    if (it && it.data && typeof it.data === "object") lines.push(JSON.stringify(it.data, null, 2));
  });
  if (!logs.length) lines.push("暂无详细处理记录");
  return lines.join("\n");
}

function buildTaskLogsText(payload) {
  const task = payload.task || {};
  const items = Array.isArray(payload.items) ? payload.items : [];
  const analysisSummary = oneLine(task.analysis_summary || "");
  const lines = [
    `生成编号：${task.id || "-"}`,
    `生成类型：${task.type || "-"}`,
    `内部流程：${task.workflow_name || "-"}`,
    `内部流程编号：${task.workflow_id || "-"}`,
    `供应商记录编号：${task.runninghub_task_id || "-"}`,
    `供应商记录编号列表：${Array.isArray(task.runninghub_task_ids) && task.runninghub_task_ids.length ? task.runninghub_task_ids.join(", ") : "-"}`,
    `状态：${task.status || "-"}`,
    `批量结果：${task.total_count ? `成功 ${task.success_count || 0}/${task.total_count}，失败 ${task.failed_count || 0}` : "-"}`,
    `错误：${task.error || "-"}`,
    `最近分析：${analysisSummary || "-"}`,
    "",
    "处理记录：",
  ];
  items.forEach((it) => {
    const data = it.data || {};
    const suffix = [
      data.stage ? `阶段=${oneLine(data.stage)}` : "",
      data.status ? `状态=${oneLine(data.status)}` : "",
      data.source ? `来源=${oneLine(data.source)}` : "",
      data.item_index ? `子项=${data.item_index}` : "",
      data.item_id ? `子项ID=${oneLine(data.item_id)}` : "",
      data.runninghub_task_id ? `供应商记录编号=${oneLine(data.runninghub_task_id)}` : "",
      data.error ? `错误=${oneLine(data.error)}` : "",
    ].filter(Boolean);
    lines.push(`[${formatTime(it.created_at)}] [${it.kind}] ${oneLine(it.message)}${suffix.length ? ` | ${suffix.join(" | ")}` : ""}`);
    if (Object.keys(data).length) lines.push(`  data: ${safeJson(data)}`);
  });
  if (!items.length) lines.push("暂无处理记录");
  return lines.join("\n");
}

function inspectItem(label, value) {
  return `
    <div class="inspect-item">
      <div class="inspect-label">${escapeHtml(label)}</div>
      <div class="inspect-value">${escapeHtml(value || "-")}</div>
    </div>
  `;
}

function inspectItemHtml(label, html) {
  return `
    <div class="inspect-item">
      <div class="inspect-label">${escapeHtml(label)}</div>
      <div class="inspect-value">${html || "-"}</div>
    </div>
  `;
}

function buildTaskDetailHtml(data) {
  const batchText = Number(data && data.total_count) > 0
    ? `成功 ${data.success_count || 0}/${data.total_count || 0}，失败 ${data.failed_count || 0}`
    : "-";
  const firstError = oneLine((data && data.error) || "");
  const logs = Array.isArray(data && data.logs) ? data.logs : [];
  const executionTraceHtml = buildExecutionTraceHtml(data && data.execution_trace);
  const logsHtml = logs.length
    ? logs.map((it) => {
      const detail = it && it.data && typeof it.data === "object" ? safeJson(it.data) : "";
      return `
        <article class="inspect-log-item">
          <div class="inspect-log-meta">
            <span>${escapeHtml(formatTime(it.created_at))}</span>
            <span>${escapeHtml(it.kind || "-")}</span>
          </div>
          <div class="inspect-log-text">${escapeHtml(oneLine(it.message || "-"))}</div>
          ${detail ? `<pre class="inspect-pre" style="margin-top:8px">${escapeHtml(detail)}</pre>` : ""}
        </article>
      `;
    }).join("")
    : `<div class="task-empty task-empty-inline">暂无详细处理记录</div>`;
  return `
    <div class="inspect-stack">
      <div class="inspect-grid">
        ${inspectItem("生成编号", data.id)}
        ${inspectItem("客户ID", data.user_id)}
        ${inspectItem("生成类型", data.type)}
        ${inspectItem("内部流程", data.workflow_name)}
        ${inspectItem("内部流程编号", data.workflow_id)}
        ${inspectItem("链路摘要", data.workflow_chain_summary)}
        ${inspectItemHtml("状态", statusPill(data.status))}
        ${inspectItem("供应商记录编号", data.runninghub_task_id)}
        ${inspectItem("供应商记录编号列表", Array.isArray(data.runninghub_task_ids) && data.runninghub_task_ids.length ? data.runninghub_task_ids.join(", ") : "-")}
        ${inspectItem("批量结果", batchText)}
        ${inspectItem("额度消耗(分)", data.cost_cents || 0)}
        ${inspectItem("创建时间", formatTime(data.created_at))}
        ${inspectItem("更新时间", formatTime(data.updated_at))}
        ${inspectItem("结果下载", data.has_download ? "可下载" : "暂无结果文件")}
      </div>
      ${firstError ? `<div class="inspect-note inspect-note-bad">错误：${escapeHtml(firstError)}</div>` : ""}
      ${data.analysis_summary ? `<div class="inspect-note">最近分析：${escapeHtml(oneLine(data.analysis_summary))}</div>` : ""}
      ${String(data.status || "") === "failed" ? `<div class="row" style="margin-top:4px"><button class="primary" type="button" data-act="analyze_error" data-id="${escapeHtml(data.id)}">错误分析</button></div>` : ""}
      <div class="inspect-section">
        <div class="inspect-section-title">输入</div>
        <pre class="inspect-pre">${escapeHtml(safeJson(data.input || {}))}</pre>
      </div>
      <div class="inspect-section">
        <div class="inspect-section-title">输出</div>
        <pre class="inspect-pre">${escapeHtml(safeJson(data.output || {}))}</pre>
      </div>
      <div class="inspect-section">
        <div class="inspect-section-title">用量</div>
        <pre class="inspect-pre">${escapeHtml(safeJson(data.usage || {}))}</pre>
      </div>
      <div class="inspect-section">
        <div class="inspect-section-title">执行链路</div>
        ${executionTraceHtml}
      </div>
      <div class="inspect-section">
        <div class="inspect-section-title">详细处理记录</div>
        <div class="inspect-log-list">${logsHtml}</div>
      </div>
    </div>
  `;
}

function buildTaskLogsHtml(payload) {
  const task = payload && payload.task ? payload.task : {};
  const items = Array.isArray(payload && payload.items) ? payload.items : [];
  const batchText = Number(task && task.total_count) > 0
    ? `成功 ${task.success_count || 0}/${task.total_count || 0}，失败 ${task.failed_count || 0}`
    : "-";
  const logsHtml = items.length
    ? items.map((it) => {
      const data = it && it.data && typeof it.data === "object" ? it.data : {};
      const extra = [];
      if (data.stage) extra.push(`阶段=${oneLine(data.stage)}`);
      if (data.status) extra.push(`状态=${oneLine(data.status)}`);
      if (data.source) extra.push(`来源=${oneLine(data.source)}`);
      if (data.workflow_name) extra.push(`内部流程=${data.workflow_name}`);
      if (data.workflow_id) extra.push(`内部流程编号=${data.workflow_id}`);
      if (data.runninghub_task_id) extra.push(`供应商记录编号=${data.runninghub_task_id}`);
      if (data.item_index) extra.push(`子项=${data.item_index}`);
      if (data.item_id) extra.push(`子项ID=${oneLine(data.item_id)}`);
      if (data.error) extra.push(`错误=${oneLine(data.error)}`);
      return `
        <article class="inspect-log-item">
          <div class="inspect-log-meta">
            <span>${escapeHtml(formatTime(it.created_at))}</span>
            <span>${escapeHtml(it.kind || "-")}</span>
          </div>
          <div class="inspect-log-text">${escapeHtml(oneLine(it.message || "-"))}</div>
          ${extra.length ? `<div class="inspect-log-extra">${escapeHtml(extra.join(" | "))}</div>` : ""}
          ${Object.keys(data).length ? `<pre class="inspect-pre" style="margin-top:8px">${escapeHtml(safeJson(data))}</pre>` : ""}
        </article>
      `;
    }).join("")
    : `<div class="task-empty task-empty-inline">暂无处理记录</div>`;
  return `
    <div class="inspect-stack">
      <div class="inspect-grid">
        ${inspectItem("生成编号", task.id)}
        ${inspectItem("生成类型", task.type)}
        ${inspectItem("内部流程", task.workflow_name)}
        ${inspectItemHtml("状态", statusPill(task.status))}
        ${inspectItem("内部流程编号", task.workflow_id)}
        ${inspectItem("供应商记录编号", task.runninghub_task_id)}
        ${inspectItem("批量结果", batchText)}
        ${inspectItem("错误", task.error || "-")}
      </div>
      ${task.analysis_summary ? `<div class="inspect-note">最近分析：${escapeHtml(oneLine(task.analysis_summary))}</div>` : ""}
      ${String(task.status || "") === "failed" ? `<div class="row" style="margin-top:4px"><button class="primary" type="button" data-act="analyze_error" data-id="${escapeHtml(task.id)}">错误分析</button></div>` : ""}
      <div class="inspect-section">
        <div class="inspect-section-title">处理时间线</div>
        <div class="inspect-log-list">${logsHtml}</div>
      </div>
    </div>
  `;
}

function openTaskInspectModal({ title, subtitle, html, rawText }) {
  const modal = el("taskInspectModal");
  if (!modal) return;
  el("taskInspectTitle").textContent = title || "生成详情";
  el("taskInspectSub").textContent = subtitle || "-";
  el("taskInspectBody").innerHTML = html || "";
  taskState.inspectText = rawText || "";
  modal.style.display = "grid";
  modal.setAttribute("aria-hidden", "false");
}

function closeTaskInspectModal() {
  const modal = el("taskInspectModal");
  if (!modal) return;
  modal.style.display = "none";
  modal.setAttribute("aria-hidden", "true");
  taskState.inspectText = "";
}

async function copyTaskInspectText() {
  if (!taskState.inspectText) {
    setMsg("taskMsg", "当前没有可复制内容", false);
    return;
  }
  await navigator.clipboard.writeText(taskState.inspectText);
  setMsg("taskMsg", "已复制当前生成内容", true);
}

function syncSelectOptions(id, values, defaultLabel) {
  const node = el(id);
  if (!node) return;
  const current = String(node.value || "");
  const options = [`<option value="">${escapeHtml(defaultLabel)}</option>`]
    .concat(values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`));
  node.innerHTML = options.join("");
  node.value = values.includes(current) ? current : "";
}

function getTaskFilterValues() {
  return {
    search: String((el("taskSearch") && el("taskSearch").value) || "").trim().toLowerCase(),
    status: String((el("taskStatusFilter") && el("taskStatusFilter").value) || "").trim(),
    workflow: String((el("taskWorkflowFilter") && el("taskWorkflowFilter").value) || "").trim(),
    user: String((el("taskUserFilter") && el("taskUserFilter").value) || "").trim(),
  };
}

function taskSearchText(task) {
  return [
    task && task.id,
    task && task.username,
    task && task.user_id,
    task && task.workflow_name,
    task && task.workflow_id,
    task && task.workflow_chain_summary,
    task && task.type,
    task && task.runninghub_task_id,
    ...(Array.isArray(task && task.runninghub_task_ids) ? task.runninghub_task_ids : []),
    task && task.error,
    task && task.first_error,
  ].map((value) => oneLine(value)).join(" ").toLowerCase();
}

function filterTasks(rows) {
  const filters = getTaskFilterValues();
  return rows.filter((task) => {
    if (filters.search && !taskSearchText(task).includes(filters.search)) return false;
    if (filters.status && String(task.status || "") !== filters.status) return false;
    if (filters.workflow && String(task.workflow_name || task.type || "") !== filters.workflow) return false;
    if (filters.user && String(task.username || task.user_id || "") !== filters.user) return false;
    return true;
  });
}

function renderTaskSummary(allRows, visibleRows) {
  const host = el("taskSummary");
  if (!host) return;
  const activeCount = visibleRows.filter((row) => ["queued", "running"].includes(String(row.status || ""))).length;
  const successCount = visibleRows.filter((row) => String(row.status || "") === "success").length;
  const failedCount = visibleRows.filter((row) => String(row.status || "") === "failed").length;
  const downloadCount = visibleRows.filter((row) => !!row.has_download).length;
  const cards = [
    { label: "当前显示", value: visibleRows.length, hint: `全部记录 ${allRows.length}` },
    { label: "运行中 / 排队", value: activeCount, hint: "running + queued" },
    { label: "已成功", value: successCount, hint: "已完成记录" },
    { label: "可下载结果", value: downloadCount, hint: `失败 ${failedCount}` },
  ];
  host.innerHTML = cards.map((card) => `
    <div class="kpi task-kpi">
      <div class="label">${escapeHtml(card.label)}</div>
      <div class="num">${escapeHtml(String(card.value))}</div>
      <div class="small">${escapeHtml(card.hint)}</div>
    </div>
  `).join("");
}

function taskActionButtons(task) {
  const taskType = String((task && task.type) || "");
  const buttons = [
    `<button class="ghost task-action-btn" type="button" data-act="detail" data-id="${escapeHtml(task.id)}">详情</button>`,
    `<button class="ghost task-action-btn" type="button" data-act="logs" data-id="${escapeHtml(task.id)}">处理记录</button>`,
    `<button class="ghost task-action-btn" type="button" data-act="export_logs" data-id="${escapeHtml(task.id)}">导出</button>`,
  ];
  if (task && task.has_download) {
    buttons.push(`<button class="blue task-action-btn" type="button" data-act="download" data-id="${escapeHtml(task.id)}">下载结果</button>`);
  }
  if (String((task && task.status) || "") === "failed") {
    buttons.push(`<button class="primary task-action-btn" type="button" data-act="retry" data-id="${escapeHtml(task.id)}">重试</button>`);
    if (taskType === "commerce_video") {
      buttons.push(`<button class="blue task-action-btn" type="button" data-act="retry_resume" data-id="${escapeHtml(task.id)}">断点重试</button>`);
    }
  }
  buttons.push(`<button class="ghost task-action-btn" type="button" data-act="delete_task" data-id="${escapeHtml(task.id)}">删除</button>`);
  return buttons.join("");
}

function renderTaskCard(task) {
  const status = String(task.status || "").trim() || "unknown";
  const workflowName = oneLine(task.workflow_name || task.type || "-");
  const taskType = oneLine(task.type || "-");
  const workflowId = oneLine(task.workflow_id || "-");
  const workflowChainSummary = oneLine(task.workflow_chain_summary || "");
  const userName = oneLine(task.username || task.user_id || "-");
  const batchText = Number(task.total_count) > 0
    ? `成功 ${task.success_count || 0}/${task.total_count || 0}，失败 ${task.failed_count || 0}`
    : "单任务";
  const runninghubIds = runninghubList(task);
  const errorText = oneLine(task.first_error || task.error || "");
  return `
    <article class="task-card task-card-status-${escapeHtml(status)}">
      <div class="task-card-head">
        <div class="task-card-main">
          <div class="task-card-title-row">
            <div class="task-card-title">${escapeHtml(workflowName)}</div>
            ${statusPill(task.status)}
          </div>
          <div class="task-card-subtitle">生成类型：${escapeHtml(taskType)} · 客户：${escapeHtml(userName)}</div>
          ${workflowChainSummary ? `<div class="small" style="margin-top:4px">链路摘要：${escapeHtml(workflowChainSummary)}</div>` : ""}
        </div>
        <div class="task-card-actions">
          ${taskActionButtons(task)}
        </div>
      </div>
      <div class="task-chip-row">
        <span class="meta-chip">生成编号：${escapeHtml(task.id)}</span>
        <span class="meta-chip">内部流程编号：${escapeHtml(workflowId)}</span>
        <span class="meta-chip">创建时间：${escapeHtml(formatTime(task.created_at))}</span>
        <span class="meta-chip">额度消耗：${escapeHtml(String(task.cost_cents || 0))} 分</span>
      </div>
      <div class="task-card-grid">
        <div class="task-card-item">
          <div class="task-card-label">批量进度</div>
          <div class="task-card-value">${escapeHtml(batchText)}</div>
        </div>
        <div class="task-card-item">
          <div class="task-card-label">更新时间</div>
          <div class="task-card-value">${escapeHtml(formatTime(task.updated_at || task.created_at))}</div>
        </div>
        <div class="task-card-item task-card-item-wide">
          <div class="task-card-label">供应商记录</div>
          <div class="task-card-value task-card-rh">
            ${runninghubIds.length
              ? runninghubIds.map((id) => `<span class="meta-chip meta-chip-code">${escapeHtml(id)}</span>`).join("")
              : `<span class="small">暂无供应商记录编号</span>`}
          </div>
        </div>
      </div>
      ${errorText ? `<div class="task-card-alert">错误：${escapeHtml(errorText)}</div>` : ""}
    </article>
  `;
}

function renderTasks() {
  const allRows = Array.isArray(taskState.rows) ? taskState.rows : [];
  const visibleRows = filterTasks(allRows);
  const list = el("taskList");
  const empty = el("taskEmpty");
  const meta = el("taskMetaLine");
  if (!list || !empty || !meta) return;
  renderTaskSummary(allRows, visibleRows);
  meta.textContent = visibleRows.length === allRows.length
    ? `共 ${allRows.length} 条生成记录，按创建时间倒序展示`
    : `显示 ${visibleRows.length} / ${allRows.length} 条生成记录`;
  empty.style.display = visibleRows.length ? "none" : "block";
  list.innerHTML = visibleRows.map((task) => renderTaskCard(task)).join("");
}

function collectRemoteComfyWorkflowMappings() {
  const result = {};
  REMOTE_COMFY_TASKS.forEach(([key]) => {
    const node = el(`rtRemoteComfyWorkflow_${key}`);
    const value = String((node && node.value) || "").trim();
    if (value) result[key] = value;
  });
  adminState.remoteComfyWorkflowMappings = result;
  return result;
}

function renderRemoteComfyWorkflowMappings() {
  const host = el("rtRemoteComfyWorkflowMappings");
  if (!host) return;
  const workflows = Array.isArray(adminState.remoteComfyWorkflows) ? adminState.remoteComfyWorkflows : [];
  const runnable = workflows.filter((item) => item && item.can_run);
  const convertible = workflows.filter((item) => item && item.kind === "ui_workflow");
  const apiWorkflows = workflows.filter((item) => item && item.root === "api");
  const options = [
    `<option value="">${escapeHtml("\u672a\u6620\u5c04")}</option>`,
    ...runnable.map((item) => {
      const path = String(item.path || "");
      const label = remoteComfyDisplayName(path);
      return `<option value="${escapeHtml(path)}">${escapeHtml(label)}</option>`;
    }),
  ].join("");
  const mappings = adminState.remoteComfyWorkflowMappings || {};
  host.innerHTML = REMOTE_COMFY_TASKS.map(([key, label]) => {
    const value = String(mappings[key] || "");
    return `
      <div class="remote-comfy-map-row">
        <label for="rtRemoteComfyWorkflow_${escapeHtml(key)}">${escapeHtml(label)}</label>
        <select id="rtRemoteComfyWorkflow_${escapeHtml(key)}" data-remote-comfy-task="${escapeHtml(key)}">
          ${options}
        </select>
      </div>
    `;
  }).join("");
  REMOTE_COMFY_TASKS.forEach(([key]) => {
    const node = el(`rtRemoteComfyWorkflow_${key}`);
    if (node) node.value = String(mappings[key] || "");
  });
  setRemoteComfyStatus("", workflows.length
    ? escapeHtml(`\u5df2\u8bfb\u53d6 API \u5de5\u4f5c\u6d41 ${apiWorkflows.length} \u4e2a\uff0c\u53ef\u76f4\u63a5\u8fd0\u884c ${runnable.length} \u4e2a\u3002\u539f\u59cb UI \u5de5\u4f5c\u6d41\u53ea\u7528\u4e8e\u4e00\u952e\u8f6c\u6362\uff0c\u4e0d\u8ba1\u5165\u8fd9\u91cc\u7684\u603b\u6570\u3002\u4e0b\u62c9\u6846\u4ec5\u663e\u793a\u53ef\u8fd0\u884c\u7684 API \u5de5\u4f5c\u6d41\uff0c\u4fdd\u5b58\u540e\u5b9e\u65f6\u751f\u6548\u3002`)
    : escapeHtml("\u5c1a\u672a\u5237\u65b0\u5de5\u4f5c\u6d41"));
}

function remoteComfyDisplayName(path) {
  const parts = String(path || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean)
    .filter((part) => part !== "__converted__");
  const filename = parts.pop() || "";
  const name = filename
    .replace(/\.api\.json$/i, "")
    .replace(/\.json$/i, "");
  const folder = parts.length ? parts[parts.length - 1] : "";
  return folder ? `${folder} / ${name}` : name;
}

function setRemoteComfyStatus(kind, html) {
  const status = el("rtRemoteComfyWorkflowStatus");
  if (!status) return;
  const suffix = kind ? ` is-${kind}` : "";
  status.className = `small remote-comfy-status${suffix}`;
  status.innerHTML = html || "";
}

function setRemoteComfyTextStatus(kind, text) {
  setRemoteComfyStatus(kind, escapeHtml(text || ""));
}

function setButtonLoading(buttonId, loading, loadingText) {
  const button = el(buttonId);
  if (!button) return;
  if (loading) {
    if (!button.dataset.idleText) button.dataset.idleText = button.textContent;
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    button.textContent = loadingText || button.dataset.idleText || "";
  } else {
    button.disabled = false;
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    if (button.dataset.idleText) button.textContent = button.dataset.idleText;
  }
}

function summarizeRemoteComfyNames(items, fieldNames) {
  const names = items.map((item) => {
    for (const field of fieldNames) {
      const value = String((item && item[field]) || "").trim();
      if (value) return value;
    }
    return "-";
  });
  const shown = names.slice(0, 8).map(escapeHtml).join("\u3001");
  return names.length > 8 ? `${shown}\u3001\u7b49 ${names.length} \u4e2a` : shown;
}

function renderRemoteComfyConvertResult(resp) {
  const items = Array.isArray(resp && resp.items) ? resp.items : [];
  const successItems = items.filter((item) => item && item.ok);
  const failedItems = items.filter((item) => !item || !item.ok);
  const skippedItems = successItems.filter((item) => item && item.skipped);
  const convertedItems = successItems.filter((item) => item && !item.skipped && !item.already_api);
  const converted = Number((resp && resp.converted) || convertedItems.length || 0);
  const skipped = Number((resp && resp.skipped) || skippedItems.length || 0);
  const failed = Number((resp && resp.failed) || failedItems.length || 0);
  const alreadyApi = successItems.filter((item) => item && item.already_api).length;
  const warningItems = successItems.filter((item) => Array.isArray(item.warnings) && item.warnings.length);
  const successNames = summarizeRemoteComfyNames(convertedItems, ["output_path", "source_path"]);
  const skippedNames = summarizeRemoteComfyNames(skippedItems, ["source_path", "output_path"]);
  const failureHtml = failedItems.length
    ? failedItems.slice(0, 10).map((item) => {
        const source = escapeHtml(String((item && item.source_path) || "-"));
        const reason = escapeHtml(String((item && (item.error || item.detail || item.message)) || "\u672a\u8fd4\u56de\u5931\u8d25\u539f\u56e0"));
        return `<div>${source}\uff1a${reason}</div>`;
      }).join("")
    : `<div>\u65e0</div>`;
  const warningHtml = warningItems.length
    ? `<div class="remote-comfy-status-list">\u8f6c\u6362\u8b66\u544a\uff1a${escapeHtml(String(warningItems.length))} \u4e2a\u5de5\u4f5c\u6d41\u6709\u8282\u70b9\u6620\u5c04\u8b66\u544a\uff0c\u5982\u679c\u540e\u7eed\u63d0\u4ea4\u5931\u8d25\u518d\u6309\u5355\u4e2a\u5de5\u4f5c\u6d41\u5904\u7406\u3002</div>`
    : "";
  return `
    <div class="remote-comfy-status-title">\u8f6c\u6362\u5b8c\u6210</div>
    <div>\u65b0\u8f6c\u6362\uff1a${escapeHtml(String(converted))} \u4e2a${alreadyApi ? `\uff0c\u5df2\u662f API \u683c\u5f0f ${escapeHtml(String(alreadyApi))} \u4e2a` : ""}</div>
    <div>\u8df3\u8fc7\uff1a${escapeHtml(String(skipped))} \u4e2a</div>
    <div>\u5931\u8d25\uff1a${escapeHtml(String(failed))} \u4e2a</div>
    ${convertedItems.length ? `<div class="remote-comfy-status-list">\u65b0\u8f6c\u6362\u6587\u4ef6\uff1a${successNames}</div>` : ""}
    ${skippedItems.length ? `<div class="remote-comfy-status-list">\u8df3\u8fc7\u6587\u4ef6\uff1a${skippedNames}</div>` : ""}
    <div class="remote-comfy-status-list">\u5931\u8d25\u539f\u56e0\uff1a${failureHtml}</div>
    ${warningHtml}
  `;
}

async function refreshRemoteComfyWorkflows() {
  setRemoteComfyTextStatus("running", "\u6b63\u5728\u8bfb\u53d6 4090 \u5de5\u4f5c\u6d41...");
  const resp = await api("/api/admin/remote_comfy/workflows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      remote_comfy_gateway_url: el("rtRemoteComfyGatewayUrl").value.trim(),
      remote_comfy_gateway_token: el("rtRemoteComfyGatewayToken").value.trim(),
    }),
  });
  adminState.remoteComfyWorkflows = Array.isArray(resp.items) ? resp.items : [];
  renderRemoteComfyWorkflowMappings();
  return resp;
}

async function convertRemoteComfyWorkflows(force = false) {
  const workflows = Array.isArray(adminState.remoteComfyWorkflows) ? adminState.remoteComfyWorkflows : [];
  const paths = workflows
    .filter((item) => item && item.kind === "ui_workflow")
    .map((item) => String(item.path || "").trim())
    .filter(Boolean);
  if (!paths.length) {
    const message = "\u5f53\u524d\u6ca1\u6709\u9700\u8981\u8f6c\u6362\u7684 UI \u5de5\u4f5c\u6d41\u3002\u8bf7\u5148\u5237\u65b0 4090 \u5de5\u4f5c\u6d41\u3002";
    setRemoteComfyTextStatus("error", message);
    throw new Error(message);
  }
  const buttonId = force ? "btnRemoteComfyForceConvertWorkflows" : "btnRemoteComfyConvertWorkflows";
  const otherButtonId = force ? "btnRemoteComfyConvertWorkflows" : "btnRemoteComfyForceConvertWorkflows";
  const otherButton = el(otherButtonId);
  if (otherButton) otherButton.disabled = true;
  setButtonLoading(buttonId, true, force ? "\u91cd\u65b0\u8f6c\u6362\u4e2d..." : "\u8f6c\u6362\u4e2d...");
  setRemoteComfyStatus("running", `
    <div class="remote-comfy-status-title">${force ? "\u6b63\u5728\u5f3a\u5236\u91cd\u65b0\u8f6c\u6362 API \u683c\u5f0f" : "\u6b63\u5728\u589e\u91cf\u8f6c\u6362 API \u683c\u5f0f"}</div>
    <div>\u5171 ${escapeHtml(String(paths.length))} \u4e2a UI \u5de5\u4f5c\u6d41\uff0c${force ? "\u5c06\u5ffd\u7565\u7f13\u5b58\u5e76\u91cd\u65b0\u751f\u6210\u3002" : "\u672a\u53d8\u5316\u7684\u5de5\u4f5c\u6d41\u4f1a\u81ea\u52a8\u8df3\u8fc7\u3002"}</div>
  `);
  try {
    const resp = await api("/api/admin/remote_comfy/convert_workflows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        remote_comfy_gateway_url: el("rtRemoteComfyGatewayUrl").value.trim(),
        remote_comfy_gateway_token: el("rtRemoteComfyGatewayToken").value.trim(),
        paths,
        overwrite: true,
        force,
      }),
    });
    await refreshRemoteComfyWorkflows();
    const failed = Number(resp.failed || 0);
    setRemoteComfyStatus(failed ? "error" : "ok", renderRemoteComfyConvertResult(resp));
    return resp;
  } catch (err) {
    setRemoteComfyTextStatus("error", `\u8f6c\u6362\u5931\u8d25\uff1a${getErrorMessage(err)}`);
    throw err;
  } finally {
    setButtonLoading(buttonId, false);
    if (otherButton) otherButton.disabled = false;
  }
}

async function runRemoteComfyMappedTest() {
  const mappings = collectRemoteComfyWorkflowMappings();
  const workflowPath = mappings.text_to_image || Object.values(mappings).find(Boolean) || "";
  if (!workflowPath) {
    const message = "\u8bf7\u5148\u7ed9\u81f3\u5c11\u4e00\u4e2a\u4efb\u52a1\u7c7b\u578b\u9009\u62e9\u53ef\u8fd0\u884c\u7684 4090 \u5de5\u4f5c\u6d41";
    setRemoteComfyTextStatus("error", message);
    throw new Error(message);
  }
  setButtonLoading("btnRemoteComfyRunTest", true, "\u6d4b\u8bd5\u4e2d...");
  setRemoteComfyTextStatus("running", `\u6b63\u5728\u6d4b\u8bd5 ${workflowPath}...`);
  try {
    const resp = await api("/api/admin/remote_comfy/run_test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        remote_comfy_gateway_url: el("rtRemoteComfyGatewayUrl").value.trim(),
        remote_comfy_gateway_token: el("rtRemoteComfyGatewayToken").value.trim(),
        workflow_path: workflowPath,
        prompt_text: "a simple red apple on a wooden table, studio lighting, high quality",
        negative_prompt: "low quality, blurry, distorted",
        width: 512,
        height: 512,
        steps: 6,
        batch_size: 1,
        timeout_seconds: 900,
      }),
    });
    const outputs = Array.isArray(resp.local_outputs) ? resp.local_outputs : [];
    const firstPath = outputs.map((item) => item && item.local_path).find(Boolean) || "";
    setRemoteComfyStatus("ok", firstPath
      ? `<div class="remote-comfy-status-title">\u6d4b\u8bd5\u6210\u529f</div><div>\u5df2\u4e0b\u8f7d\u7ed3\u679c\uff1a${escapeHtml(firstPath)}</div>`
      : `<div class="remote-comfy-status-title">\u6d4b\u8bd5\u6210\u529f</div><div>prompt_id=${escapeHtml(String(resp.prompt_id || "-"))}</div>`);
    return resp;
  } catch (err) {
    setRemoteComfyTextStatus("error", `\u6d4b\u8bd5\u5931\u8d25\uff1a${getErrorMessage(err)}`);
    throw err;
  } finally {
    setButtonLoading("btnRemoteComfyRunTest", false);
  }
}


function initSensitiveInputToggles() {
  SENSITIVE_RUNTIME_INPUT_IDS.forEach((id) => {
    const input = el(id);
    if (!input || input.type === "hidden" || input.closest(".sensitive-input-wrap")) return;
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
    button.innerHTML = `<span class="sensitive-eye-icon" aria-hidden="true"></span>`;
    button.setAttribute("aria-label", "\u663e\u793a\u5bc6\u94a5\u5185\u5bb9");
    button.title = "\u663e\u793a";
    button.setAttribute("aria-pressed", "false");
    wrapper.appendChild(button);
  });
}

function toggleSensitiveInput(button) {
  const input = el(button.dataset.target || "");
  if (!input) return;
  const willShow = input.type === "password";
  input.type = willShow ? "text" : "password";
  button.classList.toggle("is-visible", willShow);
  button.setAttribute("aria-label", willShow ? "\u9690\u85cf\u5bc6\u94a5\u5185\u5bb9" : "\u663e\u793a\u5bc6\u94a5\u5185\u5bb9");
  button.title = willShow ? "\u9690\u85cf" : "\u663e\u793a";
  button.setAttribute("aria-pressed", willShow ? "true" : "false");
  input.focus();
}

async function ensureAdmin() {
  const me = await api("/api/me");
  if (!me.is_admin) {
    location.href = "/index.html";
    return null;
  }
  el("adminName").textContent = me.username;
  if (el("accCurrentUsername")) el("accCurrentUsername").value = me.username || "";
  return me;
}

function runtimeFormToPayload() {
  const workflowChains = collectWorkflowChains();
  const digitalHumanChain = (workflowChains.digital_human_workflow_ids || [])
    .filter((stage) => parseWorkflowStage(stage).value);
  const imageGeminiModels = stringifyModelList(adminState.imageGeminiModels);
  const imageGptModels = stringifyModelList(adminState.imageGptModels);
  const llmGeminiModels = stringifyModelList(adminState.llmGeminiModels);
  const llmGptModels = stringifyModelList(adminState.llmGptModels);
  const llmPriorityModels = stringifyModelList(adminState.llmPriorityModels);
  const imagePriorityModels = stringifyModelList(adminState.imagePriorityModels);
  return {
    remote_comfy_gateway_url: el("rtRemoteComfyGatewayUrl").value.trim(),
    remote_comfy_gateway_token: el("rtRemoteComfyGatewayToken").value.trim(),
    remote_comfy_workflow_mappings: collectRemoteComfyWorkflowMappings(),
    image_generate_mode_default: "closed_model_api",
    digital_human_workflow_ids: digitalHumanChain,
    oral_digital_human_workflow_ids: [],
    image_generate_workflow_ids: [],
    replace_model_original_workflow_ids: [],
    replace_product_workflow_ids: [],
    replace_union_model_workflow_ids: [],
    replace_union_product_workflow_ids: [],
    image_model_provider_base_url: el("rtImageModelProviderBaseUrl").value.trim(),
    image_model_provider_api_key_gemini: el("rtImageModelProviderApiKeyGemini").value.trim(),
    image_model_provider_api_key_gpt: el("rtImageModelProviderApiKeyGpt").value.trim(),
    image_model_default_model_gemini: imageGeminiModels,
    image_model_default_model_gpt: imageGptModels,
    image_model_default_model: imageGeminiModels || imageGptModels,
    image_model_priority_order: imagePriorityModels || imageGeminiModels || imageGptModels,
    llm_base_url: el("rtLlmBaseUrl").value.trim(),
    llm_api_key_gemini: el("rtLlmApiKeyGemini").value.trim(),
    llm_api_key_gpt: el("rtLlmApiKeyGpt").value.trim(),
    llm_api_key: el("rtLlmApiKeyGemini").value.trim() || el("rtLlmApiKeyGpt").value.trim(),
    llm_default_model_gemini: llmGeminiModels,
    llm_default_model_gpt: llmGptModels,
    llm_default_model: llmGeminiModels || llmGptModels,
    llm_model_priority_order: llmPriorityModels || llmGeminiModels || llmGptModels,
    create_video_app_id: "",
    create_audio_app_id: "",
    video_app_id: "",
    replace_model_app_id: "",
    replace_model_original_app_id: "",
    replace_product_app_id: "",
    cleanup_enabled: !!el("rtCleanupEnabled").checked,
    cleanup_time: el("rtCleanupTime").value || "03:30",
    cleanup_retention_days: Number(el("rtCleanupRetentionDays").value || 7),
  };
}

function fillRuntimeForm(data) {
  const v = data || {};
  el("rtRemoteComfyGatewayUrl").value = v.remote_comfy_gateway_url || "";
  el("rtRemoteComfyGatewayToken").value = v.remote_comfy_gateway_token || "";
  adminState.remoteComfyWorkflowMappings = (v.remote_comfy_workflow_mappings && typeof v.remote_comfy_workflow_mappings === "object")
    ? { ...v.remote_comfy_workflow_mappings }
    : {};
  renderRemoteComfyWorkflowMappings();
  el("rtImageModelProviderBaseUrl").value = v.image_model_provider_base_url || "http://202.90.21.53:3008";
  el("rtImageModelProviderApiKeyGemini").value = v.image_model_provider_api_key_gemini || "";
  el("rtImageModelProviderApiKeyGpt").value = v.image_model_provider_api_key_gpt || "";
  adminState.imageGeminiModels = parseModelList(v.image_model_default_model_gemini || v.image_model_default_model || "gemini-3-pro-image-preview");
  adminState.imageGptModels = parseModelList(v.image_model_default_model_gpt || "");
  adminState.imagePriorityModels = parseModelList(v.image_model_priority_order || "");
  el("rtLlmBaseUrl").value = v.llm_base_url || "http://202.90.21.53:3008";
  el("rtLlmApiKeyGemini").value = Object.prototype.hasOwnProperty.call(v, "llm_api_key_gemini") ? (v.llm_api_key_gemini || "") : (v.llm_api_key || "");
  el("rtLlmApiKeyGpt").value = v.llm_api_key_gpt || "";
  adminState.llmGeminiModels = parseModelList(Object.prototype.hasOwnProperty.call(v, "llm_default_model_gemini") ? v.llm_default_model_gemini : (v.llm_default_model || "gemini-3.1-pro-preview"));
  adminState.llmGptModels = parseModelList(v.llm_default_model_gpt || "");
  adminState.llmPriorityModels = parseModelList(v.llm_model_priority_order || "");
  syncPriorityModelsFromCatalog("llm");
  syncPriorityModelsFromCatalog("image");
  const restoredModelDraft = mergeModelDraft();
  renderAllModelLists();
  if (restoredModelDraft) {
    setMsg("runtimeMsg", "已恢复浏览器中的未保存候选模型草稿，请确认后点击保存运行配置。", true);
  }
  adminState.workflowChains = {
    digital_human_workflow_ids: normalizeWorkflowChain(v.digital_human_workflow_ids, []),
  };
  renderAllWorkflowChains();
  el("rtCleanupEnabled").checked = v.cleanup_enabled !== false;
  el("rtCleanupTime").value = v.cleanup_time || "03:30";
  el("rtCleanupRetentionDays").value = String(v.cleanup_retention_days || 7);
}

async function loadRuntime() {
  const cfg = runtimeConfigResponseToConfig(await api("/api/admin/runtime_config"));
  fillRuntimeForm(cfg);
  return cfg;
}

async function saveRuntime() {
  const payload = runtimeFormToPayload();
  const resp = await api("/api/admin/runtime_config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const cfg = runtimeConfigResponseToConfig(resp);
  if (cfg) fillRuntimeForm(cfg);
  clearModelDraft();
  return cfg;
}

async function checkRemoteComfyHealth() {
  const statusNode = el("rtRemoteComfyHealthStatus");
  if (statusNode) {
    statusNode.className = "small";
    statusNode.textContent = "正在检测远程 ComfyUI 网关...";
  }
  const resp = await api("/api/admin/remote_comfy/health", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      remote_comfy_gateway_url: el("rtRemoteComfyGatewayUrl").value.trim(),
      remote_comfy_gateway_token: el("rtRemoteComfyGatewayToken").value.trim(),
    }),
  });
  const health = resp && typeof resp.health === "object" ? resp.health : {};
  const system = health && typeof health.system === "object" ? health.system : {};
  const comfyVersion = system.comfyui_version || "-";
  const osName = system.os || "-";
  const ramFree = Number(system.ram_free || 0);
  const ramTotal = Number(system.ram_total || 0);
  const ramText = ramTotal > 0 ? `${Math.round(ramFree / 1024 / 1024 / 1024)}GB / ${Math.round(ramTotal / 1024 / 1024 / 1024)}GB` : "-";
  if (statusNode) {
    statusNode.className = "msg ok";
    statusNode.textContent = `远程网关可用：ComfyUI ${comfyVersion}，${osName}，内存 ${ramText}`;
  }
  return resp;
}

async function loadPricing() {
  const p = await api("/api/admin/pricing");
  el("priceRhCoins").value = p.rh_coins_per_10rmb;
  el("priceUsdRmb").value = p.usd_to_rmb;
  el("priceNanoUsd").value = p.nano_usd_per_image;
  el("priceGemIn").value = p.gemini_input_usd_per_1m;
  el("priceGemOut").value = p.gemini_output_usd_per_1m;
}

async function savePricing() {
  const payload = {
    rh_coins_per_10rmb: Number(el("priceRhCoins").value || 2500),
    usd_to_rmb: Number(el("priceUsdRmb").value || 7.2),
    nano_usd_per_image: Number(el("priceNanoUsd").value || 0.134),
    gemini_input_usd_per_1m: Number(el("priceGemIn").value || 4.0),
    gemini_output_usd_per_1m: Number(el("priceGemOut").value || 18.0),
  };
  await api("/api/admin/pricing", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function renderTgSettings(data) {
  const settings = data || {};
  const rows = Array.isArray(settings.trusted_users) ? settings.trusted_users : [];
  adminState.tgTrustedUsers = rows;
  setText("tgBotTokenStatus", settings.bot_token_configured ? (settings.bot_token_masked || "已配置") : "未配置");
  setText("tgTrustedUserCount", rows.length);
  setText("tgBotDbPath", settings.db_path || "-");
  const envIds = Array.isArray(settings.allowed_chat_ids_env) ? settings.allowed_chat_ids_env : [];
  setText("tgBotAllowedIds", envIds.length ? envIds.join(", ") : "-");

  const body = el("tgTrustedUserBody");
  if (!body) return;
  body.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" class="small">暂无信任用户 ID</td>`;
    body.appendChild(tr);
    return;
  }
  rows.forEach((item) => {
    const chatId = String(item.chat_id || "");
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(chatId)}</td>
      <td>${escapeHtml(item.label || "")}</td>
      <td>${item.enabled ? '<span class="pill success">启用</span>' : '<span class="pill failed">停用</span>'}</td>
      <td>${item.notify_busy ? "开启" : "关闭"}</td>
      <td>${item.notify_available ? "开启" : "关闭"}</td>
      <td>
        <button class="ghost" data-act="tg_toggle" data-id="${escapeHtml(chatId)}" data-enabled="${item.enabled ? 1 : 0}">${item.enabled ? "停用" : "启用"}</button>
        <button class="ghost" data-act="tg_delete" data-id="${escapeHtml(chatId)}">删除</button>
      </td>
    `;
    body.appendChild(tr);
  });
}

async function loadTgSettings() {
  const data = await api("/api/admin/tg_settings");
  renderTgSettings(data);
  return data;
}

async function saveTgTrustedUser() {
  const chatId = Number(el("tgTrustedChatId").value || 0);
  if (!Number.isFinite(chatId) || chatId <= 0) {
    throw new Error("请填写有效的 TG 用户 ID");
  }
  const payload = {
    chat_id: Math.floor(chatId),
    label: el("tgTrustedLabel").value.trim(),
    enabled: !!el("tgTrustedEnabled").checked,
    notify_busy: !!el("tgTrustedNotifyBusy").checked,
    notify_available: !!el("tgTrustedNotifyAvailable").checked,
  };
  const resp = await api("/api/admin/tg_trusted_users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  renderTgSettings(resp.tg_settings || resp);
  el("tgTrustedChatId").value = "";
  el("tgTrustedLabel").value = "";
  el("tgTrustedEnabled").checked = true;
  el("tgTrustedNotifyBusy").checked = true;
  el("tgTrustedNotifyAvailable").checked = true;
}

async function loadUsers() {
  const rows = (await api("/api/admin/users?limit=500")).items || [];
  setText("adminUserCount", rows.length);
  setText("overviewUserCount", rows.length);
  setText("overviewUserCountMirror", rows.length);

  const body = el("userBody");
  body.innerHTML = "";
  rows.forEach((u) => {
    const tr = document.createElement("tr");
    const role = u.is_admin ? "管理员" : "客户";
    const state = u.is_disabled ? "禁用" : "正常";
    tr.innerHTML = `
      <td>${u.id}</td>
      <td>${u.username}</td>
      <td>${role}</td>
      <td>${state}</td>
      <td>${u.balance_cents}</td>
      <td>
        <button class="ghost" data-act="recharge" data-id="${u.id}" data-name="${escapeHtml(u.username)}">额度分配</button>
        <button class="ghost" data-act="toggle" data-id="${u.id}" data-disabled="${u.is_disabled ? 1 : 0}">${u.is_disabled ? "启用" : "禁用"}</button>
        <button class="ghost" data-act="delete_user" data-id="${u.id}" data-name="${u.username}">删除</button>
      </td>
    `;
    body.appendChild(tr);
  });
}

async function loadTasks() {
  const rows = (await api("/api/admin/tasks?limit=300")).items || [];
  taskState.rows = rows;
  const total = rows.length;
  const failed = rows.filter((row) => String(row.status || "") === "failed").length;
  const running = rows.filter((row) => ["running", "queued"].includes(String(row.status || ""))).length;
  setText("adminTaskCount", total);
  setText("overviewTaskCount", total);
  setText("overviewTaskCountMirror", total);
  setText("overviewFailedCount", failed);
  setText("overviewFailedCountMirror", failed);
  setText("overviewRunningCount", running);
  setText("overviewRunningCountMirror", running);
  syncSelectOptions(
    "taskStatusFilter",
    Array.from(new Set(rows.map((row) => String(row.status || "").trim()).filter(Boolean))).sort(),
    "全部状态",
  );
  syncSelectOptions(
    "taskWorkflowFilter",
    Array.from(new Set(rows.map((row) => String(row.workflow_name || row.type || "").trim()).filter(Boolean))).sort(),
    "全部类型",
  );
  syncSelectOptions(
    "taskUserFilter",
    Array.from(new Set(rows.map((row) => String(row.username || row.user_id || "").trim()).filter(Boolean))).sort(),
    "全部客户",
  );
  renderTasks();
  const lastUpdated = el("taskLastUpdated");
  if (lastUpdated) lastUpdated.textContent = `最近刷新：${new Date().toLocaleTimeString()}`;
}

async function createUser() {
  const payload = {
    username: el("newUserName").value.trim(),
    password: el("newUserPassword").value,
    is_admin: !!el("newUserIsAdmin").checked,
    balance_cents: Number(el("newUserBalance").value || 0),
  };
  if (!payload.username) throw new Error("客户用户名不能为空");
  if (!payload.password || payload.password.length < 6) throw new Error("密码至少 6 位");
  await api("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  el("newUserName").value = "";
  el("newUserPassword").value = "";
  el("newUserIsAdmin").checked = false;
  el("newUserBalance").value = "0";
}

async function runTaskAction(act, id) {
  if (act === "detail") {
    const data = await api(`/api/tasks/${id}`);
    openTaskInspectModal({
      title: "生成详情",
      subtitle: `${data.workflow_name || data.type || "任务"} · ${data.id || id}`,
      html: buildTaskDetailHtml(data),
      rawText: buildTaskDetailText(data),
    });
    return true;
  }
  if (act === "logs") {
    const data = await api(`/api/admin/tasks/${id}/logs?limit=500`);
    openTaskInspectModal({
      title: "处理记录",
      subtitle: `${(data.task && (data.task.workflow_name || data.task.type)) || "任务"} · ${id}`,
      html: buildTaskLogsHtml(data),
      rawText: buildTaskLogsText(data),
    });
    return true;
  }
  if (act === "export_logs") {
    window.open(`/api/admin/tasks/${id}/logs/export`, "_blank");
    return true;
  }
  if (act === "download") {
    window.open(`/api/tasks/${id}/download`, "_blank");
    return true;
  }
  if (act === "analyze_error") {
    setMsg("taskMsg", "");
    try {
      await api(`/api/admin/tasks/${id}/analyze_error`, { method: "POST" });
      const data = await api(`/api/tasks/${id}`);
      openTaskInspectModal({
        title: "生成详情",
        subtitle: `${data.workflow_name || data.type || "任务"} · ${data.id || id}`,
        html: buildTaskDetailHtml(data),
        rawText: buildTaskLogsText({ task: data, items: data.logs || [] }),
      });
      setMsg("taskMsg", "错误分析已生成", true);
      await loadTasks();
    } catch (err) {
      setMsg("taskMsg", err.detail || err.message || String(err), false);
    }
    return true;
  }
  if (act === "retry") {
    setMsg("taskMsg", "");
    try {
      const resp = await api(`/api/tasks/${id}/retry`, { method: "POST" });
      setMsg("taskMsg", `已创建重试记录，新生成编号：${resp.id}`, true);
      await loadTasks();
    } catch (err) {
      setMsg("taskMsg", err.detail || err.message || String(err), false);
    }
    return true;
  }
  if (act === "retry_resume") {
    setMsg("taskMsg", "");
    try {
      const resp = await api(`/api/tasks/${id}/retry_resume`, { method: "POST" });
      setMsg("taskMsg", `已创建断点重试记录，新生成编号：${resp.id}`, true);
      await loadTasks();
    } catch (err) {
      setMsg("taskMsg", err.detail || err.message || String(err), false);
    }
    return true;
  }
  if (act === "delete_task") {
    if (!confirm(`确认删除生成记录 ${id} 吗？`)) return true;
    await api(`/api/admin/tasks/${id}`, { method: "DELETE" });
    await loadTasks();
    return true;
  }
  return false;
}

function openRechargeModal(id, name) {
  adminState.rechargeTarget = { id: String(id || ""), name: String(name || id || "") };
  if (el("rechargeSub")) el("rechargeSub").textContent = `客户：${adminState.rechargeTarget.name}`;
  if (el("rechargeAmount")) el("rechargeAmount").value = "1000";
  if (el("rechargeNote")) el("rechargeNote").value = "额度分配";
  setMsg("rechargeMsg", "");
  const modal = el("rechargeModal");
  if (modal) {
    modal.style.display = "grid";
    modal.setAttribute("aria-hidden", "false");
  }
}

function closeRechargeModal() {
  const modal = el("rechargeModal");
  if (modal) {
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
  }
  adminState.rechargeTarget = null;
}

async function submitRecharge() {
  const target = adminState.rechargeTarget;
  if (!target || !target.id) return;
  const amount = Number(el("rechargeAmount").value || 0);
  const note = String(el("rechargeNote").value || "").trim();
  if (!Number.isFinite(amount) || amount <= 0) {
    setMsg("rechargeMsg", "额度必须为正整数（分）", false);
    return;
  }
  await api(`/api/admin/users/${target.id}/recharge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount_cents: Math.floor(amount), note }),
  });
  closeRechargeModal();
  await loadUsers();
}

function bindActions() {
  el("btnSaveRuntime").addEventListener("click", async () => {
    setMsg("runtimeMsg", "");
    try {
      await saveRuntime();
      setMsg("runtimeMsg", "运行配置已保存，并已按本地配置文件内容回填表单", true);
    } catch (err) {
      setMsg("runtimeMsg", formatRuntimeConfigError("保存", err), false);
    }
  });

  if (el("btnRemoteComfyWorkflows")) {
    el("btnRemoteComfyWorkflows").addEventListener("click", async () => {
      try {
        await refreshRemoteComfyWorkflows();
        setMsg("runtimeMsg", "4090 工作流已刷新", true);
      } catch (err) {
        setMsg("runtimeMsg", getErrorMessage(err), false);
      }
    });
  }
  if (el("btnRemoteComfyConvertWorkflows")) {
    el("btnRemoteComfyConvertWorkflows").addEventListener("click", async () => {
      try {
        await convertRemoteComfyWorkflows(false);
        setMsg("runtimeMsg", "\u589e\u91cf\u8f6c\u6362\u5b8c\u6210", true);
      } catch (err) {
        setMsg("runtimeMsg", getErrorMessage(err), false);
      }
    });
  }
  if (el("btnRemoteComfyForceConvertWorkflows")) {
    el("btnRemoteComfyForceConvertWorkflows").addEventListener("click", async () => {
      try {
        await convertRemoteComfyWorkflows(true);
        setMsg("runtimeMsg", "\u5f3a\u5236\u91cd\u65b0\u8f6c\u6362\u5b8c\u6210", true);
      } catch (err) {
        setMsg("runtimeMsg", getErrorMessage(err), false);
      }
    });
  }
  if (el("btnRemoteComfyHealth")) {
    el("btnRemoteComfyHealth").addEventListener("click", async () => {
      try {
        await checkRemoteComfyHealth();
        setMsg("runtimeMsg", "远程 ComfyUI 网关可用", true);
      } catch (err) {
        setMsg("runtimeMsg", getErrorMessage(err), false);
      }
    });
  }
  if (el("btnRemoteComfyRunTest")) {
    el("btnRemoteComfyRunTest").addEventListener("click", async () => {
      try {
        await runRemoteComfyMappedTest();
        setMsg("runtimeMsg", "远程 ComfyUI 测试完成", true);
      } catch (err) {
        setMsg("runtimeMsg", getErrorMessage(err), false);
      }
    });
  }

  [
    ["btnAddLlmGeminiModel", "rtLlmGeminiModelInput", "llmGeminiModels"],
    ["btnAddLlmGptModel", "rtLlmGptModelInput", "llmGptModels"],
    ["btnAddImageGeminiModel", "rtImageGeminiModelInput", "imageGeminiModels"],
    ["btnAddImageGptModel", "rtImageGptModelInput", "imageGptModels"],
  ].forEach(([buttonId, inputId, listKey]) => {
    if (el(buttonId)) {
      el(buttonId).addEventListener("click", () => addModelFromInput(listKey, inputId));
    }
    if (el(inputId)) {
      el(inputId).addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          addModelFromInput(listKey, inputId);
        }
      });
    }
  });

  if (el("btnBrowseLlmGrokModels")) {
    el("btnBrowseLlmGrokModels").addEventListener("click", toggleAvailableLlmModels);
  }
  if (el("rtLlmGrokModelPicker")) {
    el("rtLlmGrokModelPicker").addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const model = target.dataset.llmModel || "";
      if (model) addLlmModelFromPicker(model);
    });
  }

  [
    ["btnAddLlmPriorityModel", "rtLlmPriorityModelInput", "llmPriorityModels", "llm"],
    ["btnAddImagePriorityModel", "rtImagePriorityModelInput", "imagePriorityModels", "image"],
  ].forEach(([buttonId, inputId, listKey, type]) => {
    if (el(buttonId)) {
      el(buttonId).addEventListener("click", () => addPriorityModelFromInput(listKey, inputId, type));
    }
    if (el(inputId)) {
      el(inputId).addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          addPriorityModelFromInput(listKey, inputId, type);
        }
      });
    }
  });

  el("btnSavePricing").addEventListener("click", async () => {
    setMsg("pricingMsg", "");
    try {
      await savePricing();
      setMsg("pricingMsg", "计费参数已保存", true);
    } catch (err) {
      setMsg("pricingMsg", err.detail || String(err), false);
    }
  });

  if (el("btnRefreshTgTrustedUsers")) {
    el("btnRefreshTgTrustedUsers").addEventListener("click", async () => {
      setMsg("tgSettingsMsg", "");
      try {
        await loadTgSettings();
        setMsg("tgSettingsMsg", "TG Bot 设置已刷新", true);
      } catch (err) {
        setMsg("tgSettingsMsg", getErrorMessage(err), false);
      }
    });
  }

  if (el("btnAddTgTrustedUser")) {
    el("btnAddTgTrustedUser").addEventListener("click", async () => {
      setMsg("tgSettingsMsg", "");
      try {
        await saveTgTrustedUser();
        setMsg("tgSettingsMsg", "信任用户 ID 已保存", true);
      } catch (err) {
        setMsg("tgSettingsMsg", getErrorMessage(err), false);
      }
    });
  }

  el("btnCreateUser").addEventListener("click", async () => {
    setMsg("userMsg", "");
    try {
      await createUser();
      setMsg("userMsg", "客户账号已创建", true);
      await loadUsers();
    } catch (err) {
      setMsg("userMsg", err.detail || err.message || String(err), false);
    }
  });

  if (el("btnChangePassword")) {
    el("btnChangePassword").addEventListener("click", async () => {
      clearAccountMsgs();
      const oldPwd = el("accOldPassword").value || "";
      const newPwd = el("accNewPassword").value || "";
      const newPwd2 = el("accNewPassword2").value || "";
      if (!oldPwd) return setMsg("accountPasswordMsg", "请填写原密码", false);
      if (!newPwd || newPwd.length < 6) return setMsg("accountPasswordMsg", "新密码至少 6 位", false);
      if (newPwd !== newPwd2) return setMsg("accountPasswordMsg", "两次输入的新密码不一致", false);
      try {
        await api("/api/auth/change_password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
        });
        el("accOldPassword").value = "";
        el("accNewPassword").value = "";
        el("accNewPassword2").value = "";
        setMsg("accountPasswordMsg", "密码已修改", true);
      } catch (err) {
        setMsg("accountPasswordMsg", err.detail || String(err), false);
      }
    });
  }

  if (el("btnChangeUsername")) {
    el("btnChangeUsername").addEventListener("click", async () => {
      clearAccountMsgs();
      const newUsername = (el("accNewUsername").value || "").trim();
      const pwd = el("accUsernamePassword").value || "";
      if (!newUsername) return setMsg("accountUsernameMsg", "请填写新用户名", false);
      if (newUsername.length < 3 || newUsername.length > 32) return setMsg("accountUsernameMsg", "新用户名长度需在 3-32 之间", false);
      if (!/^[a-zA-Z0-9._-]+$/.test(newUsername)) return setMsg("accountUsernameMsg", "新用户名仅支持字母/数字/.-_", false);
      if (!pwd) return setMsg("accountUsernameMsg", "请填写当前密码用于确认", false);
      try {
        await api("/api/auth/change_username", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pwd, new_username: newUsername }),
        });
        el("accUsernamePassword").value = "";
        el("accNewUsername").value = "";
        const me = await api("/api/me");
        el("adminName").textContent = me.username;
        if (el("accCurrentUsername")) el("accCurrentUsername").value = me.username || "";
        setMsg("accountUsernameMsg", "用户名已修改", true);
      } catch (err) {
        setMsg("accountUsernameMsg", err.detail || String(err), false);
      }
    });
  }

  el("btnLogout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    location.href = "/login.html";
  });

  el("btnToUser").addEventListener("click", () => {
    location.href = "/index.html#app-generate";
  });

  if (el("btnTaskRefresh")) {
    el("btnTaskRefresh").addEventListener("click", async () => {
      try {
        setMsg("taskMsg", "");
        await loadTasks();
      } catch (err) {
        setMsg("taskMsg", err.detail || err.message || String(err), false);
      }
    });
  }

  ["taskSearch", "taskStatusFilter", "taskWorkflowFilter", "taskUserFilter"].forEach((id) => {
    const node = el(id);
    if (!node) return;
    node.addEventListener(id === "taskSearch" ? "input" : "change", () => {
      renderTasks();
    });
  });

  if (el("btnTaskFilterReset")) {
    el("btnTaskFilterReset").addEventListener("click", () => {
      if (el("taskSearch")) el("taskSearch").value = "";
      if (el("taskStatusFilter")) el("taskStatusFilter").value = "";
      if (el("taskWorkflowFilter")) el("taskWorkflowFilter").value = "";
      if (el("taskUserFilter")) el("taskUserFilter").value = "";
      renderTasks();
    });
  }

  if (el("btnTaskInspectClose")) {
    el("btnTaskInspectClose").addEventListener("click", () => closeTaskInspectModal());
  }
  if (el("btnTaskInspectDone")) {
    el("btnTaskInspectDone").addEventListener("click", () => closeTaskInspectModal());
  }
  if (el("btnTaskInspectCopy")) {
    el("btnTaskInspectCopy").addEventListener("click", async () => {
      try {
        await copyTaskInspectText();
      } catch (err) {
        setMsg("taskMsg", err.message || String(err), false);
      }
    });
  }
  if (el("taskInspectModal")) {
    el("taskInspectModal").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeTaskInspectModal();
    });
  }
  if (el("btnRechargeClose")) {
    el("btnRechargeClose").addEventListener("click", () => closeRechargeModal());
  }
  if (el("btnRechargeSubmit")) {
    el("btnRechargeSubmit").addEventListener("click", async () => {
      setMsg("rechargeMsg", "");
      try {
        await submitRecharge();
      } catch (err) {
        setMsg("rechargeMsg", err.detail || err.message || String(err), false);
      }
    });
  }
  if (el("rechargeModal")) {
    el("rechargeModal").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeRechargeModal();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeTaskInspectModal();
      closeRechargeModal();
    }
  });
  document.addEventListener("change", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const chainKey = target.dataset ? String(target.dataset.chainType || "") : "";
    if (chainKey && WORKFLOW_CHAIN_CONTAINER_IDS[chainKey]) {
      syncWorkflowChainFromDom(chainKey);
      renderWorkflowChain(chainKey);
    }
    if (target.dataset && target.dataset.remoteComfyTask) {
      collectRemoteComfyWorkflowMappings();
    }
  });

  document.querySelectorAll("[data-page]").forEach((node) => {
    node.addEventListener("click", () => {
      setActiveAdminPage(node.dataset.page || "overview");
    });
  });

  document.addEventListener("click", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const sensitiveToggle = target.closest(".sensitive-toggle-btn");
    if (sensitiveToggle instanceof HTMLElement) {
      toggleSensitiveInput(sensitiveToggle);
      return;
    }
    const btn = target;
    if (btn.classList.contains("admin-model-chip-remove")) {
      const idx = Number(btn.dataset.idx || -1);
      const listName = String(btn.dataset.list || "");
      const list = adminState[listName];
      if (idx >= 0 && Array.isArray(list)) {
        list.splice(idx, 1);
        if (listName === "llmGeminiModels" || listName === "llmGptModels" || listName === "llmPriorityModels") {
          syncPriorityModelsFromCatalog("llm");
        }
        if (listName === "imageGeminiModels" || listName === "imageGptModels" || listName === "imagePriorityModels") {
          syncPriorityModelsFromCatalog("image");
        }
        writeModelDraft();
        renderAllModelLists();
      }
      return;
    }
    if (btn.dataset.priorityAction) {
      const action = String(btn.dataset.priorityAction || "");
      const listName = String(btn.dataset.priorityList || "");
      const idx = Number(btn.dataset.priorityIdx || -1);
      const list = adminState[listName];
      if (!Array.isArray(list) || idx < 0 || idx >= list.length) return;
      if (action === "up" && idx > 0) {
        const item = list[idx];
        list[idx] = list[idx - 1];
        list[idx - 1] = item;
      } else if (action === "down" && idx < list.length - 1) {
        const item = list[idx];
        list[idx] = list[idx + 1];
        list[idx + 1] = item;
      }
      if (listName === "llmPriorityModels") syncPriorityModelsFromCatalog("llm");
      if (listName === "imagePriorityModels") syncPriorityModelsFromCatalog("image");
      writeModelDraft();
      renderAllModelLists();
      return;
    }
    if (btn.dataset.workflowAction) {
      const idx = Number(btn.dataset.idx || -1);
      const chainKey = String(btn.dataset.chain || "");
      if (idx < 0 || !WORKFLOW_CHAIN_CONTAINER_IDS[chainKey]) return;
      if (btn.dataset.workflowAction === "insert") {
        insertWorkflowChainStep(chainKey, idx);
      } else if (btn.dataset.workflowAction === "remove") {
        removeWorkflowChainStep(chainKey, idx);
      }
      return;
    }
    if (!btn.dataset) return;
    const act = btn.dataset.act;
    const id = btn.dataset.id;
    if (!act || !id) return;

    try {
      if (await runTaskAction(act, id, btn)) return;
    } catch (err) {
      setMsg("taskMsg", err.detail || err.message || String(err), false);
      return;
    }
    if (act === "recharge") {
      openRechargeModal(id, btn.dataset.name || id);
      return;
    }
    if (act === "toggle") {
      const disabled = String(btn.dataset.disabled || "0") === "1";
      await api(`/api/admin/users/${id}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_disabled: !disabled }),
      });
      await loadUsers();
      return;
    }
    if (act === "delete_user") {
      const name = btn.dataset.name || id;
      if (!confirm(`确认删除客户 ${name} 吗？该客户的会话、生成记录和额度流水都会删除。`)) return;
      await api(`/api/admin/users/${id}`, { method: "DELETE" });
      await loadUsers();
      await loadTasks();
      return;
    }
    if (act === "tg_toggle") {
      const enabled = String(btn.dataset.enabled || "0") === "1";
      const resp = await api(`/api/admin/tg_trusted_users/${id}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !enabled }),
      });
      renderTgSettings(resp.tg_settings || resp);
      setMsg("tgSettingsMsg", enabled ? "TG 用户已停用" : "TG 用户已启用", true);
      return;
    }
    if (act === "tg_delete") {
      if (!confirm(`确认删除 TG 信任用户 ${id} 吗？`)) return;
      const resp = await api(`/api/admin/tg_trusted_users/${id}`, { method: "DELETE" });
      renderTgSettings(resp.tg_settings || resp);
      setMsg("tgSettingsMsg", "TG 信任用户已删除", true);
      return;
    }
    if (act === "delete_task") {
      return;
    }
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  try {
    const me = await ensureAdmin();
    if (!me) return;
    initSensitiveInputToggles();
    bindActions();
    setActiveAdminPage(readAdminPageFromHash(), false);
  } catch {
    location.href = "/login.html";
    return;
  }

  try {
    await loadRuntime();
    try {
      await refreshRemoteComfyWorkflows();
    } catch {
      // Keep runtime page usable when the remote gateway is temporarily offline.
    }
    setMsg("runtimeMsg", "");
  } catch (err) {
    setMsg("runtimeMsg", formatRuntimeConfigError("读取", err), false);
  }

  try {
    await loadTgSettings();
    setMsg("tgSettingsMsg", "");
  } catch (err) {
    setMsg("tgSettingsMsg", getErrorMessage(err), false);
  }

  try {
    await loadPricing();
  } catch (err) {
    setMsg("pricingMsg", getErrorMessage(err), false);
  }

  try {
    await loadUsers();
  } catch (err) {
    setMsg("userMsg", getErrorMessage(err), false);
  }

  try {
    await loadTasks();
  } catch (err) {
    setMsg("taskMsg", getErrorMessage(err), false);
  }

  setInterval(async () => {
    try {
      await loadUsers();
      if (!el("taskAutoRefresh") || el("taskAutoRefresh").checked) {
        await loadTasks();
      }
    } catch {
      // ignore
    }
  }, TASK_POLL_INTERVAL_MS);
});

window.addEventListener("hashchange", () => {
  setActiveAdminPage(readAdminPageFromHash(), false);
});
