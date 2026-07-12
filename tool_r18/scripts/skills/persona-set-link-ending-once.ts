import fs from "node:fs";
import { loadPersonaArchive, updatePersonaArchiveProfile } from "@/lib/persona-archives";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";

installNodePersonaArchiveBridge();

type Input = {
  archiveId: string;
  action?: "add" | "delete";
  presetId?: string;
  name?: string;
  endingText?: string;
  linkUrl?: string;
};

function readInput(): Input {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as Input;
}

function printJson(value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    process.stdout.write(`${JSON.stringify(value, null, 2)}\n`, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  if (!archiveId) throw new Error("missing archiveId");
  const archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error(`persona archive not found: ${archiveId}`);
  const existing = Array.isArray((archive.setup as any)?.linkEndingPresets)
    ? (archive.setup as any).linkEndingPresets
    : [];
  if (input.action === "delete") {
    const presetId = String(input.presetId || "").trim();
    if (!presetId) throw new Error("missing presetId");
    if (!existing.some((preset: any) => String(preset?.id || "") === presetId)) {
      throw new Error(`link ending preset not found: ${presetId}`);
    }
    const activeId = String((archive.setup as any)?.activeLinkEndingPresetId || "");
    const updated = await updatePersonaArchiveProfile(archiveId, {
      setup: {
        ...(archive.setup || {}),
        linkEndingPresets: existing.filter((preset: any) => String(preset?.id || "") !== presetId),
        activeLinkEndingPresetId: activeId === presetId ? "" : activeId,
      } as any,
    });
    if (!updated) throw new Error("failed to update persona archive");
    await printJson({ ok: true, archiveId, deletedPresetId: presetId });
    return;
  }

  const endingText = String(input.endingText || "").trim().slice(0, 240);
  const linkUrl = String(input.linkUrl || "").trim();
  if (!endingText && !linkUrl) throw new Error("missing endingText or linkUrl");
  if (linkUrl && !/^https?:\/\/[^\s]+$/i.test(linkUrl)) throw new Error("invalid linkUrl");
  const suffix = [endingText, linkUrl].filter(Boolean).join("\n");
  if (Array.from(suffix).length > 500) throw new Error("link ending exceeds Threads 500 character limit");

  const now = new Date().toISOString();
  const preset = {
    id: `lp-${Date.now().toString(36)}`,
    name: String(input.name || endingText || linkUrl).trim().slice(0, 40),
    endingText,
    linkUrl,
    enabled: false,
    createdAt: now,
    updatedAt: now,
  };
  const updated = await updatePersonaArchiveProfile(archiveId, {
    setup: {
      ...(archive.setup || {}),
      linkEndingPresets: [...existing, preset],
      activeLinkEndingPresetId: String((archive.setup as any)?.activeLinkEndingPresetId || ""),
    } as any,
  });
  if (!updated) throw new Error("failed to update persona archive");
  await printJson({ ok: true, archiveId, preset });
}

main()
  .then(() => process.exit(0))
  .catch(async (error) => {
    await printJson({ ok: false, error: error instanceof Error ? error.message : String(error) }).catch(() => undefined);
    process.exit(1);
  });
