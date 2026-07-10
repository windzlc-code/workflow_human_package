import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { loadPersonaArchive, updatePersonaArchiveProfile } from "@/lib/persona-archives";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import { rewritePersonaIntroWithCodex } from "@/telegram-bot";

installNodePersonaArchiveBridge();

type PersonaRewriteIntroInput = {
  archiveId: string;
  direction: string;
  mode?: "direct" | "replace";
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): PersonaRewriteIntroInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as PersonaRewriteIntroInput;
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  const direction = String(input.direction || "").trim();
  const mode = String(input.mode || "direct").trim() === "replace" ? "replace" : "direct";
  if (!archiveId) throw new Error("missing archiveId");
  if (!direction) throw new Error("missing direction");

  const archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error(`persona archive not found: ${archiveId}`);

  const rewritten = mode === "replace"
    ? await rewritePersonaIntroWithCodex(archive, direction, "replace")
    : null;
  const updated = await updatePersonaArchiveProfile(archiveId, rewritten
    ? {
        content: rewritten.content,
        setup: rewritten.setup,
      }
    : {
        content: direction,
        setup: {
          personaDescription: direction,
          customTopic: direction,
        },
      });
  if (!updated) throw new Error(`failed to update persona archive: ${archiveId}`);

  printJson({
    ok: true,
    archiveId,
    name: updated.name,
    content: updated.content,
    setup: updated.setup,
    mode,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
