import { resolveVmosCredentials } from "@/runtime/node/config";
import { warmupThreadsAccount, type WarmupConfig } from "@/lib/vmos-publisher";
import { createNodePublishQueueRepository } from "@/runtime/node/publish-queue-repository";

function argValue(name: string): string | undefined {
  const prefix = `--${name}=`;
  return process.argv.find((arg) => arg.startsWith(prefix))?.slice(prefix.length);
}

const padCode = process.env.THREADS_WARMUP_MIDTEST_PAD_CODE || argValue("pad") || "ACP250322677KIRJ";
const keyword = process.env.THREADS_WARMUP_MIDTEST_KEYWORD || argValue("keyword") || "房仲";
const mode = process.env.THREADS_WARMUP_MIDTEST_MODE || argValue("mode") || "like";

const cfg: WarmupConfig = {
  browseCount: Math.max(1, Number(process.env.THREADS_WARMUP_MIDTEST_COUNT || argValue("count") || 1)),
  minWatchSeconds: 1,
  maxWatchSeconds: 2,
  minAfterScrollPauseMs: 350,
  maxAfterScrollPauseMs: 700,
  likeChance: mode === "comment" ? 0 : 100,
  maxLikes: mode === "comment" ? 0 : 1,
  minRequiredLikes: mode === "comment" ? 0 : 1,
  commentChance: mode === "comment" ? 100 : 0,
  maxComments: mode === "comment" ? 1 : 0,
  minRequiredComments: mode === "comment" ? 1 : 0,
  minRequiredInteractions: 1,
  commentPersona: {
    name: "房产中介",
    description: "关注房源、看房、买房、租房、房仲服务与房地产市场。",
    language: "zh",
    interests: ["房产", "房仲", "看房", "房源", "买房", "租房"],
  },
  keywords: ["房产", "房仲", "看房", "房源", "买房", "租房"],
  requireRelevantContent: true,
  relevanceProbePosts: 0,
  relevanceSearchAttempts: 1,
  strictCompletion: true,
  riskManaged: false,
  stopOnRiskLimit: false,
  assumeCurrentSearchKeyword: keyword,
};

async function main() {
  const repo = createNodePublishQueueRepository();
  if (repo.isPadLocked(padCode)) {
    throw new Error(`pad is locked: ${padCode}`);
  }
  const config = resolveVmosCredentials();
  const progress: string[] = [];
  console.log(`[midtest] pad=${padCode} keyword=${keyword} mode=${mode}`);
  const result = await warmupThreadsAccount(config, padCode, cfg, (progressEvent) => {
    const line = `[midtest][progress] browsed=${progressEvent.browsed ?? 0} liked=${progressEvent.liked ?? 0} commented=${progressEvent.commented ?? 0} done=${progressEvent.done ? 1 : 0} step=${progressEvent.step}${progressEvent.error ? ` error=${progressEvent.error}` : ""}`;
    progress.push(line);
    console.log(line);
  });
  console.log(JSON.stringify({ ok: true, result, progress }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }, null, 2));
  process.exitCode = 1;
});
