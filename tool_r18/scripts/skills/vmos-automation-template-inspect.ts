import "@/runtime/node/browser-shim";
import fs from "node:fs";
import path from "node:path";
import { getAutomationScript, listAutomationScripts } from "@/lib/vmos-client";
import { resolveVmosCredentials } from "@/runtime/node/config";

interface InspectInput {
  platform?: string;
  category?: "official" | "user" | string;
  size?: number;
  scriptId?: number | string;
  saveDir?: string;
  configPath?: string;
  dataDir?: string;
}

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function summarizeContent(content: string | undefined) {
  if (!content) return { hasContent: false, length: 0 };
  const lower = content.toLowerCase();
  const keywords = [
    "click",
    "swipe",
    "input",
    "upload",
    "post",
    "publish",
    "caption",
    "description",
    "like",
    "comment",
    "confirm",
    "draft",
    "com.zhiliaoapp.musically",
    "com.ss.android.ugc.trill",
  ];
  const matchedKeywords = keywords.filter((item) => lower.includes(item));
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    parsed = undefined;
  }
  return {
    hasContent: true,
    length: content.length,
    matchedKeywords,
    jsonType: Array.isArray(parsed) ? "array" : parsed && typeof parsed === "object" ? "object" : "text",
    preview: content.slice(0, 500),
  };
}

async function main() {
  const raw = process.argv[2] || "{}";
  const input = JSON.parse(raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw) as InspectInput;
  const config = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });
  const platform = input.platform || "tiktok";
  const category = input.category || "official";
  const saveDir = input.saveDir || ".runtime/automatic-script/vmos-templates";
  fs.mkdirSync(saveDir, { recursive: true });

  const list = await listAutomationScripts(config, {
    page: 1,
    size: input.size ?? 20,
    category,
    platform,
  });
  const candidates = list.list || [];
  const details = [];
  const ids = input.scriptId ? [input.scriptId] : candidates.map((item) => item.id).filter(Boolean);
  for (const id of ids.slice(0, input.size ?? 20)) {
    const detail = await getAutomationScript(config, id);
    const outputPath = path.join(saveDir, `${platform}-${id}.json`);
    fs.writeFileSync(outputPath, JSON.stringify(detail, null, 2));
    details.push({
      id: detail.id,
      name: detail.name,
      description: detail.description,
      targetPackage: detail.targetPackage,
      version: detail.version,
      outputPath,
      content: summarizeContent(detail.content),
    });
  }

  printJson({
    ok: true,
    platform,
    category,
    total: list.total,
    count: candidates.length,
    scripts: candidates.map((item) => ({
      id: item.id,
      name: item.name,
      description: item.description,
      targetPackage: item.targetPackage,
      version: item.version,
    })),
    details,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
