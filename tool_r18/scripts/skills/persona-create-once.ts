import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import {
  createPersonaBySpec,
  derivePersonaDirectionKeywordsWithCodex,
  derivePersonaSpecFromPromptSelection,
} from "@/telegram-bot";

installNodePersonaArchiveBridge();

type PersonaCreateInput = {
  action?: "keywords" | "create";
  name: string;
  prompt: string;
  selectedKeywords?: string[];
  ownerBotName?: string;
  chatId?: number;
  defaultPadCode?: string;
};

function printJson(value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    process.stdout.write(`${JSON.stringify(value, null, 2)}\n`, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
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

async function main() {
  const input = readInput();
  const name = String(input.name || "").trim();
  const prompt = String(input.prompt || "").trim();
  if (!name) throw new Error("missing name");
  if (!prompt) throw new Error("missing prompt");

  if (input.action === "keywords") {
    const keywords = await derivePersonaDirectionKeywordsWithCodex(name, prompt);
    await printJson({ ok: true, name, prompt, keywords });
    return;
  }

  const selectedKeywords = normalizeKeywords(input.selectedKeywords ?? []);
  const spec = await derivePersonaSpecFromPromptSelection({
    personaName: name,
    userPrompt: prompt,
    selectedKeywords,
  });

  const created = await createPersonaBySpec(spec, {
    ownerBotName: String(input.ownerBotName || "").trim() || undefined,
    chatId: Number.isFinite(Number(input.chatId)) && Number(input.chatId) > 0 ? Number(input.chatId) : undefined,
    defaultPadCode: String(input.defaultPadCode || "").trim() || undefined,
  });
  await printJson({
    ok: true,
    archiveId: created.archiveId,
    name: created.name,
    content: spec.content,
    setup: spec.setup,
  });
}

main()
  .then(() => process.exit(0))
  .catch(async (error) => {
    await printJson({ ok: false, error: error instanceof Error ? error.message : String(error) }).catch(() => undefined);
    process.exit(1);
  });
