import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { runThreadsOwnPostReplyOnce } from "@/telegram-bot";
import { emitWebTaskProgress } from "./_web-task-progress";

type Input = {
  archiveId: string;
  padCode: string;
  replyMode: "manual" | "ai";
  replyText?: string;
  minViews: number;
  maxAgeDays: number;
  dryRun?: boolean;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): Input {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@")
    ? fs.readFileSync(raw.slice(1), "utf8")
    : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as Input;
}

async function main() {
  const input = readInput();
  const startedAt = Date.now();
  const logs: Array<Record<string, unknown> & { elapsedMs: number }> = [];
  const result = await runThreadsOwnPostReplyOnce(
    {
      ...input,
      dryRun: input.dryRun === true,
    },
    (progress) => {
      logs.push({ ...progress, elapsedMs: Date.now() - startedAt });
      emitWebTaskProgress({
        step: progress.step,
        line: `🔥 ${progress.step}｜已掃描 ${progress.scannedPosts}｜已回覆 ${progress.replied}｜已跳過 ${progress.skipped}`,
        padCode: input.padCode,
        scannedPosts: progress.scannedPosts,
        replied: progress.replied,
        skipped: progress.skipped,
        targetReplies: progress.targetReplies,
        done: progress.done,
        error: Boolean(progress.error),
      });
    },
  );
  if (!logs.length) {
    emitWebTaskProgress({
      step: result.error || "Threads 自動回覆熱點推文完成",
      line: result.error ? `ℹ️ ${result.error}` : "✅ Threads 自動回覆熱點推文完成",
      padCode: input.padCode,
      scannedPosts: result.executionScanned,
      matched: result.matched,
      replied: result.replied,
      skipped: result.skipped,
      done: true,
      error: false,
    });
  }
  printJson({
    ok: true,
    ...result,
    elapsedMs: Date.now() - startedAt,
    logs,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
