import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { loadPersonaArchive, updatePersonaArchivePadBinding } from "@/lib/persona-archives";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";

installNodePersonaArchiveBridge();

type PersonaTelegramGroupInput = {
  archiveId: string;
  groupContentType?: "free" | "paid";
  groupName: string;
};

function readInput(): PersonaTelegramGroupInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as PersonaTelegramGroupInput;
}

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  const groupName = String(input.groupName || "").trim();
  if (!archiveId) throw new Error("missing archiveId");
  if (!groupName) throw new Error("missing groupName");

  const archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error(`persona archive not found: ${archiveId}`);
  const workflowPersona = Boolean(
    archive.setup?.imageWorkflow
    || (archive as typeof archive & { imageWorkflow?: unknown }).imageWorkflow
    || archive.id.startsWith("workflow-persona-"),
  );
  const requestedType = input.groupContentType === "paid" ? "paid" : "free";
  const groupContentType = workflowPersona ? requestedType : "free";
  const updated = await updatePersonaArchivePadBinding(archiveId, groupContentType === "paid"
    ? { telegramPaidGroupName: groupName, telegramPaidGroupId: "" }
    : { telegramFreeGroupName: groupName, telegramFreeGroupId: "" });
  if (!updated) throw new Error(`failed to update persona archive: ${archiveId}`);

  printJson({
    ok: true,
    archiveId,
    name: updated.name,
    workflowPersona,
    groupContentType,
    groupName,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
