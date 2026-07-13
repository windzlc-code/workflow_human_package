import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { runPersonaWorkflow, type PersonaWorkflowInput } from "@/core/persona/persona-workflow-service";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import { parseScheduledDate } from "@/core/publish/schedule-time";

type EnqueuePostsWorkflowInput = Extract<PersonaWorkflowInput, { action: "enqueue-posts" }>;

type Input = {
  archiveId: string;
  postIds: string[];
  platform: string;
  padCode: string;
  scheduledAt?: string;
  idempotencyKey?: string;
  telegramChatId?: string;
};

type EnqueuePostsResult = {
  archiveId: string;
  enqueued: Array<{ taskId: string; postId: string }>;
  skipped: Array<{ postId: string; reason: string }>;
};

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

function normalizeScheduledAt(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !value.trim()) throw new Error("scheduledAt must be a valid date-time string");
  try {
    return parseScheduledDate(value).toISOString();
  } catch {
    throw new Error("scheduledAt must be a valid date-time string");
  }
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  const padCode = String(input.padCode || "").trim();
  const platform = String(input.platform || "").trim();
  if (!archiveId) throw new Error("missing archiveId");
  if (!Array.isArray(input.postIds) || !input.postIds.length) throw new Error("postIds must be a non-empty array");
  const postIds = [...new Set(input.postIds.map((postId) => String(postId || "").trim()))];
  if (postIds.some((postId) => !postId)) throw new Error("postIds must contain only non-empty strings");
  if (!padCode) throw new Error("missing padCode");
  if (platform !== "threads" && platform !== "telegram") throw new Error("platform must be threads or telegram");
  const scheduledAt = normalizeScheduledAt(input.scheduledAt);
  if (input.idempotencyKey !== undefined && (typeof input.idempotencyKey !== "string" || !input.idempotencyKey.trim())) {
    throw new Error("idempotencyKey must be a non-empty string");
  }
  const idempotencyKey = input.idempotencyKey?.trim();

  const workflowInput: EnqueuePostsWorkflowInput = {
    action: "enqueue-posts",
    archiveId,
    postIds,
    platform,
    padCode,
    scheduledAt,
    idempotencyKey,
    telegramChatId: String(input.telegramChatId || "").trim() || undefined,
  };
  const result = await runPersonaWorkflow(workflowInput) as EnqueuePostsResult;

  printJson({
    archiveId: result.archiveId,
    enqueued: result.enqueued,
    skipped: result.skipped,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
