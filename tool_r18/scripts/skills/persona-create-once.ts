import "@/runtime/node/browser-shim";
import fs from "node:fs";
import {
  createPersonaBySpec,
  derivePersonaSpecWithCodex,
} from "@/telegram-bot";

type PersonaCreateInput = {
  name: string;
  prompt: string;
  selectedKeywords?: string[];
  ownerBotName?: string;
  chatId?: number;
  defaultPadCode?: string;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): PersonaCreateInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as PersonaCreateInput;
}

function normalizeKeywords(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error("selectedKeywords must be a list");
  return value
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .slice(0, 2);
}

function buildPersonaPrompt(name: string, prompt: string, selectedKeywords: string[]): string {
  return [
    `角色名稱：${name}`,
    "",
    prompt,
    "",
    selectedKeywords.length
      ? `使用者已選擇的人設走向核心關鍵詞：${selectedKeywords.join("、")}。請把這些方向作為最高優先級，生成完整人設。`
      : "使用者未額外選擇核心關鍵詞，請根據原始提示詞自主判斷最合理的人設走向。",
  ].join("\n");
}

async function main() {
  const input = readInput();
  const name = String(input.name || "").trim();
  const prompt = String(input.prompt || "").trim();
  if (!name) throw new Error("missing name");
  if (!prompt) throw new Error("missing prompt");

  const selectedKeywords = normalizeKeywords(input.selectedKeywords ?? []);
  const combinedPrompt = buildPersonaPrompt(name, prompt, selectedKeywords);
  const spec = await derivePersonaSpecWithCodex(combinedPrompt);
  spec.name = name;
  spec.setup = {
    ...spec.setup,
    personaName: name,
    customTopic: prompt,
    contentTheme: [
      spec.setup.contentTheme,
      selectedKeywords.length ? `核心走向：${selectedKeywords.join("、")}` : "",
    ].filter(Boolean).join("\n"),
  };

  const created = await createPersonaBySpec(spec, {
    ownerBotName: String(input.ownerBotName || "").trim() || undefined,
    chatId: Number.isFinite(Number(input.chatId)) && Number(input.chatId) > 0 ? Number(input.chatId) : undefined,
    defaultPadCode: String(input.defaultPadCode || "").trim() || undefined,
  });
  printJson({
    ok: true,
    archiveId: created.archiveId,
    name: created.name,
    content: spec.content,
    setup: spec.setup,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
