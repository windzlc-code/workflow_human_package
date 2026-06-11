import fs from "node:fs";
import path from "node:path";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";

export interface EnsureRuntimeSecretsOptions {
  projectRoot?: string;
}

export function ensureRuntimeSecrets(options: EnsureRuntimeSecretsOptions = {}): void {
  const projectRoot = options.projectRoot || process.cwd();
  const runtimeDir = path.dirname(resolveRuntimeFile("api_config.json"));
  fs.mkdirSync(runtimeDir, { recursive: true });

  const apiConfigPath = resolveRuntimeFile("api_config.json");
  const electronDir = path.join(projectRoot, "electron");
  fs.mkdirSync(electronDir, { recursive: true });
  const credsPath = path.join(electronDir, "vmos-credentials.local.json");

  if (!fs.existsSync(apiConfigPath)) {
    fs.writeFileSync(apiConfigPath, JSON.stringify({ retryCount: 2, retryDelayMs: 1000 }, null, 2), "utf-8");
  }

  if (!fs.existsSync(credsPath)) {
    fs.writeFileSync(credsPath, JSON.stringify({ ak: process.env.VMOS_AK || "", sk: process.env.VMOS_SK || "" }, null, 2), "utf-8");
  }
}
