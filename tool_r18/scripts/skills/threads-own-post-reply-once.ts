import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { runThreadsOwnPostReplyOnce } from "@/telegram-bot";

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
    (progress) => logs.push({ ...progress, elapsedMs: Date.now() - startedAt }),
  );
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
