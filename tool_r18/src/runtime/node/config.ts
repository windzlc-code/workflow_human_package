import fs from "node:fs";
import path from "node:path";
import { resolveRuntimeFile, type NodeRuntimePathsOptions } from "./data-dir";

export type SupportedApiProtocol = "gemini" | "gemini-text" | "openai" | "anthropic";

export interface RuntimeApiConfig {
  geminiKey?: string;
  geminiTextKey?: string;
  gptKey?: string;
  anthropicKey?: string;
  geminiEndpoint?: string;
  geminiTextEndpoint?: string;
  gptEndpoint?: string;
  anthropicEndpoint?: string;
  zhanhuKey?: string;
  zhanhuEndpoint?: string;
  runningHubKey?: string;
  runningHubEndpoint?: string;
  runningHubWorkflowId?: string;
  runningHubImageWebappId?: string;
  runningHubAccessPassword?: string;
  comfyWorkflowJupyterBase?: string;
  comfyWorkflowComfyBase?: string;
  comfyWorkflowToken?: string;
  comfyWorkflowLocalDir?: string;
  comfyWorkflowAuthHeader?: string;
  comfyWorkflowAuthValue?: string;
  comfyWorkflowGatewayToken?: string;
  personaWorkflowJupyterBase?: string;
  personaWorkflowComfyBase?: string;
  personaWorkflowToken?: string;
  personaWorkflowLocalDir?: string;
  personaWorkflowAuthHeader?: string;
  personaWorkflowAuthValue?: string;
  personaWorkflowGatewayToken?: string;
  retryCount?: number;
  retryDelayMs?: number;
  modelMappings?: Record<string, { modelId?: string; protocol?: SupportedApiProtocol | "auto" }>;
  vmosAk?: string;
  vmosSk?: string;
  vmosAccounts?: VmosCredentialConfig[];
}

export interface VmosCredentialConfig {
  name?: string;
  ak: string;
  sk: string;
}

export interface RuntimeConfigOptions extends NodeRuntimePathsOptions {
  configPath?: string;
}

export function resolveConfigPath(options: RuntimeConfigOptions = {}): string {
  return options.configPath || process.env.AUTO_TWEET_API_CONFIG_PATH || resolveRuntimeFile("api_config.json", options);
}

export function readRuntimeApiConfig(options: RuntimeConfigOptions = {}): RuntimeApiConfig {
  const configPath = resolveConfigPath(options);
  try {
    if (!fs.existsSync(configPath)) return {};
    return JSON.parse(fs.readFileSync(configPath, "utf-8")) || {};
  } catch {
    return {};
  }
}

export function getRuntimeApiConfigForProtocol(
  protocol: SupportedApiProtocol,
  options: RuntimeConfigOptions = {},
): { apiKey: string; endpoint: string } {
  const config = readRuntimeApiConfig(options);
  const fallbackEndpoint = config.zhanhuEndpoint || "http://202.90.21.53:13003";

  if (protocol === "gemini-text") {
    return {
      apiKey: config.geminiTextKey || config.geminiKey || config.zhanhuKey || process.env.GEMINI_TEXT_API_KEY || "",
      endpoint: config.geminiTextEndpoint || config.geminiEndpoint || fallbackEndpoint,
    };
  }

  if (protocol === "gemini") {
    return {
      apiKey: config.geminiKey || config.zhanhuKey || process.env.GEMINI_API_KEY || "",
      endpoint: config.geminiEndpoint || fallbackEndpoint,
    };
  }

  if (protocol === "anthropic") {
    return {
      apiKey: config.anthropicKey || process.env.ANTHROPIC_API_KEY || "",
      endpoint: config.anthropicEndpoint || fallbackEndpoint,
    };
  }

  return {
    apiKey: config.gptKey || config.zhanhuKey || process.env.OPENAI_API_KEY || "",
    endpoint: config.gptEndpoint || fallbackEndpoint,
  };
}

export function resolveModelProtocol(model: string, options: RuntimeConfigOptions = {}): SupportedApiProtocol {
  const config = readRuntimeApiConfig(options);
  const override = config.modelMappings?.[model]?.protocol;
  if (override && override !== "auto") return override;
  if (model.startsWith("claude")) return "anthropic";
  if (model.startsWith("xai/") || model.startsWith("grok-")) return "openai";
  if (model.startsWith("gpt-") || /dall-e/i.test(model)) return "openai";
  return "gemini";
}

export function readRuntimeTextGenConfig(options: RuntimeConfigOptions = {}): {
  retryCount: number;
  retryDelayMs: number;
} {
  const config = readRuntimeApiConfig(options);
  return {
    retryCount: Number(config.retryCount ?? 1) || 1,
    retryDelayMs: Number(config.retryDelayMs ?? 800) || 800,
  };
}

function normalizeVmosCredential(value: unknown): VmosCredentialConfig | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const ak = String(item.ak || item.accessKeyId || item.accessKeyID || "").trim();
  const sk = String(item.sk || item.secretAccessKey || item.secretKey || "").trim();
  if (!ak || !sk) return null;
  const name = String(item.name || item.label || "").trim();
  return name ? { name, ak, sk } : { ak, sk };
}

function dedupeVmosCredentials(accounts: VmosCredentialConfig[]): VmosCredentialConfig[] {
  const seen = new Set<string>();
  const result: VmosCredentialConfig[] = [];
  for (const account of accounts) {
    const key = `${account.ak}\n${account.sk}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(account);
  }
  return result;
}

export function resolveVmosCredentialList(options: RuntimeConfigOptions = {}): VmosCredentialConfig[] {
  const accounts: VmosCredentialConfig[] = [];
  const envAk = process.env.VMOS_AK || process.env.AUTO_TWEET_VMOS_AK || "";
  const envSk = process.env.VMOS_SK || process.env.AUTO_TWEET_VMOS_SK || "";
  if (envAk && envSk) accounts.push({ name: process.env.VMOS_ACCOUNT_NAME || "env", ak: envAk, sk: envSk });

  const runtimeConfig = readRuntimeApiConfig(options);
  if (runtimeConfig.vmosAk && runtimeConfig.vmosSk) {
    accounts.push({ name: "runtime-primary", ak: runtimeConfig.vmosAk, sk: runtimeConfig.vmosSk });
  }
  if (Array.isArray(runtimeConfig.vmosAccounts)) {
    accounts.push(...runtimeConfig.vmosAccounts.map(normalizeVmosCredential).filter(Boolean) as VmosCredentialConfig[]);
  }

  try {
    const localCredentialsPath = path.join(process.cwd(), "electron", "vmos-credentials.local.json");
    if (fs.existsSync(localCredentialsPath)) {
      const parsed = JSON.parse(fs.readFileSync(localCredentialsPath, "utf-8"));
      const legacy = normalizeVmosCredential(parsed);
      if (legacy) accounts.push({ name: legacy.name || "local-primary", ak: legacy.ak, sk: legacy.sk });
      if (Array.isArray(parsed?.accounts)) {
        accounts.push(...parsed.accounts.map(normalizeVmosCredential).filter(Boolean) as VmosCredentialConfig[]);
      }
    }
  } catch {
    // ignore fallback parse errors
  }

  return dedupeVmosCredentials(accounts);
}

export function resolveVmosCredentials(options: RuntimeConfigOptions = {}): { ak: string; sk: string; accounts: VmosCredentialConfig[] } {
  const accounts = resolveVmosCredentialList(options);
  const primary = accounts[0];
  return {
    ak: primary?.ak || "",
    sk: primary?.sk || "",
    accounts,
  };
}
