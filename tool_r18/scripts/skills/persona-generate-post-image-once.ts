import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import { regenerateArchivePostImage } from "@/telegram-bot";

type Input = { archiveId: string; postId: string };

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
  const postId = String(input.postId || "").trim();
  if (!archiveId) throw new Error("missing archiveId");
  if (!postId) throw new Error("missing postId");

  const result = await regenerateArchivePostImage({ archiveId, postId });
  printJson({ ok: true, archiveId, postId, imageUrl: result.imageUrl });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
