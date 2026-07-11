import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { warmupThreadsAccount, buildWarmupInterestKeywords, type WarmupCommentPersona } from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";
import { emitWebTaskProgress } from "./_web-task-progress";

type ThreadsWarmupMode = "browse" | "like" | "comment" | "both";

type ThreadsWarmupInput = {
  padCode: string;
  mode?: ThreadsWarmupMode;
  browseCount?: number;
  minSessionMinutes?: number;
  maxSessionMinutes?: number;
  interactionEveryMinPosts?: number;
  interactionEveryMaxPosts?: number;
  maxLikes?: number;
  maxComments?: number;
  commentPersona?: WarmupCommentPersona;
  keywords?: string[];
  searchChance?: number;
  dryRun?: boolean;
  configPath?: string;
  dataDir?: string;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): ThreadsWarmupInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as ThreadsWarmupInput;
}

function asInt(value: unknown, fallback: number) {
  const num = Number(value);
  return Number.isFinite(num) && num >= 0 ? Math.floor(num) : fallback;
}

function resolveMode(value: unknown): ThreadsWarmupMode {
  const mode = String(value || "browse").trim() as ThreadsWarmupMode;
  if (!["browse", "like", "comment", "both"].includes(mode)) throw new Error(`invalid Threads warmup mode: ${mode}`);
  return mode;
}

async function main() {
  const input = readInput();
  if (!input.padCode) throw new Error("missing padCode");

  const mode = resolveMode(input.mode);
  const credentials = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });
  const commentPersona = input.commentPersona || { language: "台灣在地繁體中文" };
  const keywords = Array.isArray(input.keywords) && input.keywords.length
    ? input.keywords.map((item) => String(item || "").trim()).filter(Boolean)
    : buildWarmupInterestKeywords(commentPersona);
  const maxLikes = mode === "like" || mode === "both" ? asInt(input.maxLikes, 1) : 0;
  const maxComments = mode === "comment" || mode === "both" ? asInt(input.maxComments, 1) : 0;
  const options = {
    browseCount: asInt(input.browseCount, 80),
    minSessionMinutes: asInt(input.minSessionMinutes, 7),
    maxSessionMinutes: asInt(input.maxSessionMinutes, 10),
    minWatchSeconds: input.minSessionMinutes ? 12 : 2,
    maxWatchSeconds: input.minSessionMinutes ? 28 : 5,
    minAfterScrollPauseMs: input.minSessionMinutes ? 1200 : 600,
    maxAfterScrollPauseMs: input.minSessionMinutes ? 3600 : 1400,
    interactionEveryMinPosts: asInt(input.interactionEveryMinPosts, 2),
    interactionEveryMaxPosts: asInt(input.interactionEveryMaxPosts, 3),
    likeChance: maxLikes ? 100 : 0,
    maxLikes,
    minRequiredLikes: 0,
    commentChance: maxComments ? 100 : 0,
    maxComments,
    minRequiredComments: 0,
    minRequiredInteractions: maxLikes || maxComments ? 1 : 0,
    commentTemplates: [],
    commentPersona,
    keywords,
    searchChance: asInt(input.searchChance, 16),
    riskManaged: false,
    stopOnRiskLimit: true,
    strictCompletion: false,
    requireReadablePostForComment: true,
  };

  if (input.dryRun !== false) {
    printJson({
      ok: true,
      dryRun: true,
      hasCredentials: Boolean(credentials.ak && credentials.sk),
      padCode: input.padCode,
      mode,
      options,
    });
    return;
  }

  const startedAt = Date.now();
  const logs: Array<Record<string, unknown> & { elapsedMs: number }> = [];
  const result = await warmupThreadsAccount(
    credentials,
    input.padCode,
    options,
    (progress) => {
      logs.push({ ...progress, elapsedMs: Date.now() - startedAt });
      emitWebTaskProgress({
        step: progress.step,
        line: `🌱 ${progress.step}｜已瀏覽 ${progress.browsed} 條｜已點讚 ${progress.liked} 個｜已留言 ${progress.commented} 個`,
        padCode: input.padCode,
        browsed: progress.browsed,
        liked: progress.liked,
        commented: progress.commented,
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

