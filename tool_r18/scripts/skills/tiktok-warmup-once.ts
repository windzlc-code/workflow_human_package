import "@/runtime/node/browser-shim";
import fs from "node:fs";
import path from "node:path";
import {
  execAdb,
  listPads,
  screenshot,
  waitTask,
  type VmosConfig,
} from "@/lib/vmos-client";
import { resolveVmosCredentials } from "@/runtime/node/config";

const TIKTOK_PACKAGES = ["com.ss.android.ugc.trill", "com.zhiliaoapp.musically"];
const SCREEN = { width: 1080, height: 2340 };

type TiktokWarmupMode = "browse" | "like" | "comment" | "both";

interface TiktokWarmupInput {
  padCode: string;
  mode?: TiktokWarmupMode;
  browseCount?: number;
  minWatchSeconds?: number;
  maxWatchSeconds?: number;
  allowEngagement?: boolean;
  likeChance?: number;
  maxLikes?: number;
  commentChance?: number;
  maxComments?: number;
  commentText?: string;
  dryRun?: boolean;
  configPath?: string;
  dataDir?: string;
  screenshotDir?: string;
}

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

async function runAdb(config: VmosConfig, padCode: string, command: string, timeoutMs = 60_000) {
  const taskId = await execAdb(config, padCode, command);
  const result = await waitTask(config, taskId, timeoutMs);
  if (result.taskStatus !== 3) {
    throw new Error(result.errorMsg || result.taskResult || `ADB task ${taskId} failed`);
  }
  return String(result.taskResult || "");
}

async function saveScreenshot(config: VmosConfig, padCode: string, outputPath: string) {
  const url = await screenshot(config, padCode);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`screenshot download failed: ${response.status}`);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, Buffer.from(await response.arrayBuffer()));
  return outputPath;
}

async function dumpUiXml(config: VmosConfig, padCode: string) {
  return await runAdb(
    config,
    padCode,
    "uiautomator dump /data/local/tmp/tiktok-ui.xml >/dev/null 2>&1; cat /data/local/tmp/tiktok-ui.xml 2>/dev/null || true",
  );
}

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

async function detectTikTokPackage(config: VmosConfig, padCode: string) {
  const packages = await runAdb(
    config,
    padCode,
    `for p in ${TIKTOK_PACKAGES.map(shellQuote).join(" ")}; do if pm path "$p" >/dev/null 2>&1; then echo "$p"; exit 0; fi; done`,
  );
  const packageName = packages.trim().split(/\s+/)[0];
  if (!packageName) throw new Error("TikTok app is not installed on this cloud phone");
  return packageName;
}

function asInt(value: unknown, fallback: number) {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? Math.floor(num) : fallback;
}

function resolveMode(input: TiktokWarmupInput): TiktokWarmupMode {
  const mode = input.mode || "browse";
  if (!["browse", "like", "comment", "both"].includes(mode)) throw new Error(`invalid TikTok warmup mode: ${mode}`);
  return mode;
}

function adbInputText(value: string) {
  return value
    .replace(/\\/g, "\\\\")
    .replace(/ /g, "%s")
    .replace(/'/g, "")
    .replace(/&/g, "\\&")
    .replace(/;/g, "")
    .replace(/\n/g, "%s");
}

async function main() {
  const raw = process.argv[2];
  if (!raw) {
    printJson({ ok: false, error: "missing JSON input" });
    process.exitCode = 1;
    return;
  }

  const input = JSON.parse(raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw) as TiktokWarmupInput;
  if (!input.padCode) throw new Error("missing padCode");
  const mode = resolveMode(input);
  const config = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });
  const browseCount = asInt(input.browseCount, 3);
  const minWatch = asInt(input.minWatchSeconds, 3);
  const maxWatch = Math.max(minWatch, asInt(input.maxWatchSeconds, 6));
  const likeChance = Math.max(0, Math.min(100, Number.isFinite(Number(input.likeChance)) ? Number(input.likeChance) : 0));
  const maxLikes = asInt(input.maxLikes, mode === "like" || mode === "both" ? 1 : 0);
  const commentChance = Math.max(0, Math.min(100, Number.isFinite(Number(input.commentChance)) ? Number(input.commentChance) : 0));
  const maxComments = asInt(input.maxComments, mode === "comment" || mode === "both" ? 1 : 0);
  const commentText = String(input.commentText || "Useful reminder").trim().slice(0, 120);

  if (input.dryRun !== false) {
    printJson({
      ok: true,
      dryRun: true,
      hasCredentials: Boolean(config.ak && config.sk),
      padCode: input.padCode,
      mode,
      browseCount,
      allowEngagement: Boolean(input.allowEngagement),
      likeChance,
      maxLikes,
      commentChance,
      maxComments,
    });
    return;
  }

  if ((mode === "like" || mode === "comment" || mode === "both") && input.allowEngagement !== true) {
    throw new Error("TikTok like/comment warmup is disabled unless allowEngagement=true; use mode=browse for non-polluting validation");
  }

  const pads = await listPads(config);
  const pad = pads.find((item) => item.padCode === input.padCode);
  if (!pad) throw new Error("当前人设绑定的智能體手機不存在，请进入人设设置重新绑定可用智能體手機。");
  const packageName = await detectTikTokPackage(config, input.padCode);

  const shotDir = input.screenshotDir || ".runtime/automatic-script/tiktok-warmup";
  const screenshots: string[] = [];
  let liked = 0;
  let commented = 0;
  await runAdb(
    config,
    input.padCode,
    [
      `am force-stop ${packageName}`,
      "sleep 1",
      `monkey -p ${packageName} -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1`,
      "sleep 8",
      // If a previous pre-publish test left an unposted-video prompt, Cancel keeps the account clean.
      `input tap ${Math.round(SCREEN.width * 0.39)} ${Math.round(SCREEN.height * 0.119)}`,
      "sleep 2",
      "input keyevent KEYCODE_BACK >/dev/null 2>&1 || true",
      "sleep 1",
      `input tap ${Math.round(SCREEN.width * 0.09)} ${Math.round(SCREEN.height * 0.94)}`,
      "sleep 6",
      `input tap ${Math.round(SCREEN.width * 0.78)} ${Math.round(SCREEN.height * 0.067)}`,
      "sleep 3",
    ].join("; "),
  );
  screenshots.push(await saveScreenshot(config, input.padCode, path.join(shotDir, `tiktok-warmup-start-${Date.now()}.jpg`)));

  for (let index = 0; index < browseCount; index += 1) {
    const watch = minWatch + Math.floor(Math.random() * (maxWatch - minWatch + 1));
    const shouldLike = input.allowEngagement === true
      && (mode === "like" || mode === "both")
      && liked < maxLikes
      && Math.random() * 100 < likeChance;
    const shouldComment = input.allowEngagement === true
      && (mode === "comment" || mode === "both")
      && commented < maxComments
      && Boolean(commentText)
      && Math.random() * 100 < commentChance;
    await runAdb(config, input.padCode, `sleep ${watch}`);
    if (shouldLike) {
      await runAdb(
        config,
        input.padCode,
        [`input tap ${Math.round(SCREEN.width * 0.91)} ${Math.round(SCREEN.height * 0.55)}`, "sleep 2"].join("; "),
      );
      liked += 1;
      screenshots.push(await saveScreenshot(config, input.padCode, path.join(shotDir, `tiktok-warmup-like-${liked}-${Date.now()}.jpg`)));
    }
    if (shouldComment) {
      await runAdb(
        config,
        input.padCode,
        [
          `input tap ${Math.round(SCREEN.width * 0.91)} ${Math.round(SCREEN.height * 0.62)}`,
          "sleep 3",
          `input tap ${Math.round(SCREEN.width * 0.34)} ${Math.round(SCREEN.height * 0.94)}`,
          "sleep 1",
          `input text ${shellQuote(adbInputText(commentText))}`,
          "sleep 1",
          `input tap ${Math.round(SCREEN.width * 0.92)} ${Math.round(SCREEN.height * 0.65)}`,
          "sleep 3",
        ].join("; "),
      );
      commented += 1;
      screenshots.push(await saveScreenshot(config, input.padCode, path.join(shotDir, `tiktok-warmup-comment-${commented}-${Date.now()}.jpg`)));
      await runAdb(config, input.padCode, "input keyevent KEYCODE_BACK; sleep 1");
    }
    await runAdb(
      config,
      input.padCode,
      [
        `input swipe ${Math.round(SCREEN.width * 0.5)} ${Math.round(SCREEN.height * 0.78)} ${Math.round(SCREEN.width * 0.5)} ${Math.round(SCREEN.height * 0.28)} 450`,
        "sleep 2",
      ].join("; "),
    );
    screenshots.push(await saveScreenshot(config, input.padCode, path.join(shotDir, `tiktok-warmup-${index + 1}-${Date.now()}.jpg`)));
  }

  printJson({
    ok: true,
    result: {
      platform: "tiktok",
      mode,
      browsed: browseCount,
      liked,
      commented,
      engagementSkipped: input.allowEngagement !== true,
      padCode: input.padCode,
      padName: pad.padName,
      packageName,
      screenshots,
    },
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
