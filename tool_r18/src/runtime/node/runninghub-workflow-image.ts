import {
  createRunningHubAiAppTask,
  createRunningHubTask,
  getRunningHubAiAppCallDemo,
  getRunningHubWorkflowJson,
  resolveRunningHubConfig,
  waitRunningHubTaskOutputs,
  type RunningHubNodeInfo,
} from "./runninghub-client";
import type { RuntimeConfigOptions } from "./config";
import type { PersonaWorkflowImageConfig } from "./comfyui-workflow-client";

type RunningHubApiPrompt = Record<string, {
  class_type?: string;
  inputs?: Record<string, any>;
  _meta?: { title?: string };
}>;

type RunningHubImageResult = {
  ok: boolean;
  url?: string;
  outputs?: unknown;
  taskId?: string;
  error?: string;
  retryable?: boolean;
  reasonCode?: string;
};

const TEXT_INPUT_FIELDS = new Set(["text", "value", "prompt", "positive", "提示词文本", "user_prompt", "user_prompt_input"]);
const DEFAULT_IMAGE_WEBAPP_ID = "2034899011521482754";

function parsePromptPayload(raw: any): RunningHubApiPrompt {
  const payload = raw?.data?.prompt ?? raw?.prompt ?? raw?.data ?? raw;
  if (typeof payload === "string") return JSON.parse(payload);
  if (payload && typeof payload === "object") return payload;
  return {};
}

function extractOutputUrl(value: unknown): string | undefined {
  if (!value) return undefined;
  if (typeof value === "string") {
    if (/^https?:\/\//i.test(value) || /^data:image\//i.test(value)) return value;
    return undefined;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = extractOutputUrl(item);
      if (found) return found;
    }
    return undefined;
  }
  if (typeof value === "object") {
    const item: any = value;
    for (const key of ["fileUrl", "file_url", "url", "imageUrl", "image_url", "path"]) {
      const found = extractOutputUrl(item[key]);
      if (found) return found;
    }
    for (const child of Object.values(item)) {
      const found = extractOutputUrl(child);
      if (found) return found;
    }
  }
  return undefined;
}

function classifyRunningHubError(errorText: string): { retryable: boolean; reasonCode: string } {
  if (/API Key|TOKEN|auth|unauthor/i.test(errorText)) return { retryable: false, reasonCode: "auth_missing" };
  if (/timeout|超时|逾時/i.test(errorText)) return { retryable: true, reasonCode: "timeout" };
  if (/RunningHub 任务失败|fail|error/i.test(errorText)) return { retryable: true, reasonCode: "upstream_error" };
  return { retryable: false, reasonCode: "unknown" };
}

function directTextInputName(node: RunningHubApiPrompt[string]): string | null {
  const inputs = node.inputs || {};
  for (const field of TEXT_INPUT_FIELDS) {
    const value = inputs[field];
    if (typeof value === "string") return field;
  }
  return null;
}

function nodeTitle(node: RunningHubApiPrompt[string]): string {
  return `${node._meta?.title || ""} ${node.class_type || ""}`.trim();
}

function isNegativePromptText(value: unknown): boolean {
  return typeof value === "string" && /negative|worst|watermark|bad anatomy|extra digits|censor|ai-generated/i.test(value);
}

function buildRunningHubNodeInfoList(
  prompt: RunningHubApiPrompt,
  finalPrompt: string,
  aspectRatio?: string,
): RunningHubNodeInfo[] {
  const list: RunningHubNodeInfo[] = [];
  const directPromptNode = Object.entries(prompt).find(([, node]) => {
    const title = nodeTitle(node);
    if (/negative|負向|负向/i.test(title)) return false;
    const field = directTextInputName(node);
    if (!field || isNegativePromptText(node.inputs?.[field])) return false;
    return /PromptBatchQueue|PrimitiveString|Text Multiline|CR Prompt|promptLine/i.test(title)
      || (/CLIPTextEncode/i.test(title) && typeof node.inputs?.[field] === "string" && !isNegativePromptText(node.inputs[field]));
  });

  if (directPromptNode) {
    const [nodeId, node] = directPromptNode;
    const fieldName = directTextInputName(node);
    if (fieldName) {
      list.push({ nodeId, fieldName, fieldValue: finalPrompt, description: "persona prompt" });
    }
  }

  const latent = Object.entries(prompt).find(([, node]) => /Empty.*LatentImage/i.test(node.class_type || ""));
  if (latent) {
    const [nodeId] = latent;
    const size = imageSizeFromAspectRatio(aspectRatio);
    list.push(
      { nodeId, fieldName: "width", fieldValue: size.width, description: "image width" },
      { nodeId, fieldName: "height", fieldValue: size.height, description: "image height" },
      { nodeId, fieldName: "batch_size", fieldValue: 1, description: "batch size" },
    );
  }

  const sampler = Object.entries(prompt).find(([, node]) => /KSampler/i.test(node.class_type || ""));
  if (sampler) {
    list.push({
      nodeId: sampler[0],
      fieldName: "seed",
      fieldValue: Math.floor(Math.random() * 1_000_000_000_000_000),
      description: "random seed",
    });
  }

  return list;
}

function imageSizeFromAspectRatio(aspectRatio?: string): { width: number; height: number } {
  switch (aspectRatio) {
    case "4:5":
      return { width: 1024, height: 1280 };
    case "3:4":
      return { width: 960, height: 1280 };
    case "9:16":
      return { width: 832, height: 1472 };
    case "4:3":
      return { width: 1280, height: 960 };
    case "16:9":
      return { width: 1472, height: 832 };
    default:
      return { width: 1024, height: 1024 };
  }
}

function extractAiAppNodeInfoList(response: any): any[] {
  const list = response?.data?.nodeInfoList;
  return Array.isArray(list) ? list : [];
}

function isPromptLikeAiAppNode(node: any): boolean {
  const fieldName = String(node?.fieldName || "");
  const nodeName = String(node?.nodeName || "");
  const description = `${node?.description || ""} ${node?.descriptionEn || ""}`;
  if (!TEXT_INPUT_FIELDS.has(fieldName)) return false;
  if (/negative|負向|负向/i.test(`${nodeName} ${description}`)) return false;
  return /prompt|text|提示|文本|輸入|输入|CR Text/i.test(`${fieldName} ${nodeName} ${description}`);
}

function buildAiAppNodeInfoList(nodes: any[], prompt: string, aspectRatio?: string): RunningHubNodeInfo[] {
  const size = imageSizeFromAspectRatio(aspectRatio);
  let replacedPrompt = false;
  return nodes.map((node) => {
    const next: any = { ...node };
    if (String(next.fieldName) === "width") {
      next.fieldValue = size.width;
    } else if (String(next.fieldName) === "height") {
      next.fieldValue = size.height;
    } else if (!replacedPrompt && isPromptLikeAiAppNode(next)) {
      next.fieldValue = prompt;
      replacedPrompt = true;
    }
    return next;
  });
}

export async function generateRunningHubAiAppImage(
  params: {
    prompt: string;
    webappId?: string;
    aspectRatio?: string;
    timeoutMs?: number;
  },
  runtimeOptions: RuntimeConfigOptions = {},
): Promise<RunningHubImageResult> {
  const config = resolveRunningHubConfig(runtimeOptions);
  const webappId = params.webappId || config.imageWebappId || DEFAULT_IMAGE_WEBAPP_ID;
  try {
    const demo = await getRunningHubAiAppCallDemo(config, webappId);
    const nodes = extractAiAppNodeInfoList(demo);
    if (nodes.length === 0) {
      return { ok: false, error: `RunningHub AI 应用 ${webappId} 未返回 nodeInfoList`, retryable: false, reasonCode: "workflow_no_prompt_input" };
    }
    const nodeInfoList = buildAiAppNodeInfoList(nodes, params.prompt, params.aspectRatio);
    const hasPrompt = nodeInfoList.some((node: any) => isPromptLikeAiAppNode(node) && node.fieldValue === params.prompt);
    if (!hasPrompt) {
      return { ok: false, error: `RunningHub AI 应用 ${webappId} 未定位到可写入提示词的节点`, retryable: false, reasonCode: "workflow_no_prompt_input" };
    }
    const created = await createRunningHubAiAppTask(config, webappId, nodeInfoList);
    const taskId = String(created?.data?.taskId || created?.data?.task_id || created?.data || "");
    if (!taskId) throw new Error(`RunningHub AI 应用未返回 taskId：${JSON.stringify(created).slice(0, 500)}`);
    const outputs = await waitRunningHubTaskOutputs(config, taskId, params.timeoutMs || 300_000, 5000);
    const url = extractOutputUrl(outputs);
    if (!url) {
      return { ok: false, taskId, outputs, error: `RunningHub AI 应用任务完成但未返回图片 URL：${JSON.stringify(outputs).slice(0, 500)}`, retryable: true, reasonCode: "output_missing" };
    }
    return { ok: true, taskId, outputs, url };
  } catch (error: any) {
    const message = error?.message || String(error);
    return { ok: false, error: message, ...classifyRunningHubError(message) };
  }
}

export async function generateRunningHubWorkflowImage(
  params: {
    prompt: string;
    workflowImage: PersonaWorkflowImageConfig;
    aspectRatio?: string;
    timeoutMs?: number;
  },
  runtimeOptions: RuntimeConfigOptions = {},
): Promise<RunningHubImageResult> {
  const workflowId = params.workflowImage.workflowId;
  if (!workflowId) return { ok: false, error: "缺少 RunningHub workflowId", retryable: false, reasonCode: "config_missing" };
  const config = resolveRunningHubConfig(runtimeOptions);
  try {
    const apiFormat = await getRunningHubWorkflowJson(config, workflowId);
    const apiPrompt = parsePromptPayload(apiFormat);
    const finalPrompt = [params.prompt, params.workflowImage.promptSuffix].filter(Boolean).join(", ");
    const nodeInfoList = buildRunningHubNodeInfoList(apiPrompt, finalPrompt, params.aspectRatio);
    const created = await createRunningHubTask(config, nodeInfoList, workflowId);
    const taskId = String(created?.data?.taskId || created?.data?.task_id || created?.data || "");
    if (!taskId) throw new Error(`RunningHub 未返回 taskId：${JSON.stringify(created).slice(0, 500)}`);
    const outputs = await waitRunningHubTaskOutputs(config, taskId, params.timeoutMs || 300_000, 5000);
    const url = extractOutputUrl(outputs);
    if (!url) {
      return { ok: false, taskId, outputs, error: `RunningHub 任务完成但未返回图片 URL：${JSON.stringify(outputs).slice(0, 500)}`, retryable: true, reasonCode: "output_missing" };
    }
    return { ok: true, taskId, outputs, url };
  } catch (error: any) {
    const message = error?.message || String(error);
    return { ok: false, error: message, ...classifyRunningHubError(message) };
  }
}
