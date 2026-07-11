import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { autoReplyThreadsAccount, type WarmupCommentPersona } from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";
import { emitWebTaskProgress } from "./_web-task-progress";

type ThreadsAutoReplyOnceInput = {
  padCode: string;
  dryRun?: boolean;
  maxAgeDays?: number;
  maxPosts?: number;
  maxReplies?: number;
  commentPersona?: WarmupCommentPersona;
  replySuffix?: string;
  configPath?: string;
  dataDir?: string;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): ThreadsAutoReplyOnceInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@")
    ? fs.readFileSync(raw.slice(1), "utf8")
    : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as ThreadsAutoReplyOnceInput;
}

async function main() {
  const input = readInput();
  if (!input.padCode) throw new Error("missing padCode");

  const credentials = resolveVmosCredentials({
    configPath: input.configPath,
    dataDir: input.dataDir,
  });
  const options = {
    maxAgeDays: input.maxAgeDays,
    maxPosts: input.maxPosts,
    maxReplies: input.maxReplies,
    commentPersona: input.commentPersona,
    replySuffix: input.replySuffix,
  };

  if (input.dryRun !== false) {
    printJson({
      ok: true,
      dryRun: true,
      hasCredentials: Boolean(credentials.ak && credentials.sk),
      padCode: input.padCode,
      options,
    });
    return;
  }

  const startedAt = Date.now();
  const logs: Array<Record<string, unknown> & { elapsedMs: number }> = [];
  const result = await autoReplyThreadsAccount(
    credentials,
    input.padCode,
    options,
    (progress) => {
      logs.push({ ...progress, elapsedMs: Date.now() - startedAt });
      emitWebTaskProgress({
        step: progress.step,
        line: `💬 ${progress.step}｜已掃描推文 ${progress.scannedPosts}｜候選留言 ${progress.scannedComments}｜已回覆 ${progress.replied}｜已跳過 ${progress.skipped}`,
        padCode: input.padCode,
        scannedPosts: progress.scannedPosts,
        scannedComments: progress.scannedComments,
        replied: progress.replied,
        skipped: progress.skipped,
        targetReplies: progress.targetReplies,
        done: progress.done,
        error: Boolean(progress.error),
      });
    },
  );

  printJson({
    ok: true,
    result,
    elapsedMs: Date.now() - startedAt,
    logs,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
