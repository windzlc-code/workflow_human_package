import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { runPersonaWorkflow } from "@/core/persona/persona-workflow-service";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";

type PersonaGeneratePostsInput = {
  archiveId: string;
  count?: number;
  customInstruction?: string;
  selectedMemoryEntryIds?: string[];
  selectedMemorySummaries?: string[];
  textModelBranch?: "free" | "paid";
};

installNodePersonaArchiveBridge();

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): PersonaGeneratePostsInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as PersonaGeneratePostsInput;
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  if (!archiveId) throw new Error("missing archiveId");

  const result = await runPersonaWorkflow({
    action: "generate-posts",
    archiveId,
    count: input.count,
    customInstruction: input.customInstruction,
    selectedMemoryEntryIds: input.selectedMemoryEntryIds,
    selectedMemorySummaries: input.selectedMemorySummaries,
    textModelBranch: input.textModelBranch,
  });
  printJson(result);
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
