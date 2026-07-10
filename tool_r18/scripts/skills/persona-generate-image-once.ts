import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { loadPersonaArchive } from "@/lib/persona-archives";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import { generateAndPersistPersonaReferenceImage } from "@/telegram-bot";

type Input = { archiveId: string };

installNodePersonaArchiveBridge();

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): Input {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as Input;
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  if (!archiveId) throw new Error("missing archiveId");
  const archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error("persona archive not found");

  const result = await generateAndPersistPersonaReferenceImage(archiveId, archive.name);
  if (!result.ok || !result.imageUrl) throw new Error(result.error || "persona image generation failed");
  printJson({ ok: true, archiveId, imageUrl: result.imageUrl, mode: result.mode || "" });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
