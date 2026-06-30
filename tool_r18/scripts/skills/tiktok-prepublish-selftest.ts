import "@/runtime/node/browser-shim";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {
  execAdb,
  getPadInfo,
  listPads,
  screenshot,
  waitTask,
  type VmosConfig,
} from "@/lib/vmos-client";
import { resolveVmosCredentials } from "@/runtime/node/config";

const TIKTOK_PACKAGES = ["com.ss.android.ugc.trill", "com.zhiliaoapp.musically"];
const TIKTOK_SHARE_ACTIVITY_CLASS = "com.ss.android.ugc.aweme.share.SystemShareActivity";
const DEFAULT_SCREEN = { width: 1080, height: 2340 };

interface TiktokPrepublishInput {
  padCode: string;
  mediaPath?: string;
  caption?: string;
  confirmPublish?: boolean;
  dryRun?: boolean;
  stopBeforePost?: boolean;
  configPath?: string;
  dataDir?: string;
  screenshotPath?: string;
  afterPostScreenshotPath?: string;
  profileScreenshotPath?: string;
  skipNetworkPreflight?: boolean;
}

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\\''")}'`;
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
  const xml = await runAdb(
    config,
    padCode,
    "uiautomator dump /data/local/tmp/tiktok-ui.xml >/dev/null 2>&1; cat /data/local/tmp/tiktok-ui.xml 2>/dev/null || true",
  );
  return xml;
}

async function getFocusedWindow(config: VmosConfig, padCode: string) {
  return await runAdb(config, padCode, "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' || true");
}

function extractUploadProgress(xml: string) {
  const matches = Array.from(xml.matchAll(/(?:text|content-desc)="(\d{1,3})%"/g))
    .map((match) => Number(match[1]))
    .filter((value) => Number.isFinite(value) && value >= 0 && value <= 100);
  return matches.length > 0 ? Math.max(...matches) : undefined;
}

function isStillOnPublishEditor(xml: string) {
  return /(?:text|content-desc)="(?:Post|Drafts|Allow comments|More options|Describe your video|Write a caption|Add description\.\.\.|Next)"/i.test(xml);
}

function isTikTokPostForm(xml: string) {
  return /(?:text|content-desc)="Post"/i.test(xml)
    && /(?:text|content-desc)="(?:Add description\.\.\.|More options|Location|Drafts)"/i.test(xml);
}

function isTikTokEditorPage(xml: string) {
  return /(?:text|content-desc)="Next"/i.test(xml)
    && /(?:text|content-desc)="(?:Add sound|Your Story)"/i.test(xml);
}

function isTikTokProfilePage(xml: string) {
  return /(?:text|content-desc)="Profile"/i.test(xml)
    && /(?:text|content-desc)="Videos"/i.test(xml)
    && /(?:text|content-desc)="Private account"/i.test(xml);
}

function hasDraftOrUnpostedState(xml: string) {
  return /(?:text|content-desc)="(?:Drafts(?::\s*\d+)?|Keep editing your unposted video\?)"/i.test(xml);
}

function hasTikTokUploadFailure(xml: string) {
  return /(?:text|content-desc)="(?:Something went wrong|Try again later|Retry)"/i.test(xml);
}

async function assertTikTokDnsResolvable(config: VmosConfig, padCode: string) {
  const output = await runAdb(
    config,
    padCode,
    "ping -c 1 -W 5 www.tiktok.com >/dev/null 2>&1 && echo ok || echo dns_failed",
    30_000,
  );
  if (!/\bok\b/.test(output)) {
    throw new Error("TikTok 上传前网络预检失败：当前智能體手機 DNS 无法解析 www.tiktok.com，请先修复 VMOS 智能體手機网络/DNS 后再发布。");
  }
}

async function stageVideo(config: VmosConfig, padCode: string, localPath: string) {
  const bytes = fs.readFileSync(localPath);
  const hash = crypto.createHash("sha1").update(bytes).digest("hex").slice(0, 10);
  const base = `tiktok-prepublish-${hash}`;
  const remoteB64 = `/data/local/tmp/${base}.b64`;
  const remotePath = `/sdcard/DCIM/Camera/${base}.mp4`;
  const b64 = bytes.toString("base64");

  await runAdb(config, padCode, `rm -f ${shellQuote(remoteB64)} ${shellQuote(remotePath)}; mkdir -p /sdcard/DCIM/Camera`);
  for (let offset = 0; offset < b64.length; offset += 3000) {
    const chunk = b64.slice(offset, offset + 3000);
    await runAdb(config, padCode, `printf %s ${shellQuote(chunk)} >> ${shellQuote(remoteB64)}`);
  }

  await runAdb(
    config,
    padCode,
    [
      `base64 -d ${shellQuote(remoteB64)} > ${shellQuote(remotePath)}`,
      `chmod 644 ${shellQuote(remotePath)}`,
      `am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://${remotePath}`,
      "sleep 2",
    ].join("; "),
  );

  const displayName = path.basename(remotePath);
  const query = await runAdb(
    config,
    padCode,
    [
      "content query --uri content://media/external/video/media --projection _id:_data:_display_name",
      `| grep -F ${shellQuote(displayName)}`,
      "| tail -n 1 || true",
    ].join(" "),
  );
  const id = query.match(/_id=(\d+)/)?.[1];
  if (!id) throw new Error(`media store did not index staged video: ${remotePath}`);
  return { remotePath, contentUri: `content://media/external/video/media/${id}` };
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

async function openFinalPostPage(config: VmosConfig, padCode: string, packageName: string, contentUri: string) {
  await runAdb(
    config,
    padCode,
    [
      `pm grant ${packageName} android.permission.READ_MEDIA_VIDEO >/dev/null 2>&1 || true`,
      `pm grant ${packageName} android.permission.READ_MEDIA_IMAGES >/dev/null 2>&1 || true`,
      `pm grant ${packageName} android.permission.READ_MEDIA_AUDIO >/dev/null 2>&1 || true`,
      `appops set ${packageName} READ_MEDIA_VIDEO allow >/dev/null 2>&1 || true`,
      `am force-stop ${packageName}`,
      "sleep 1",
      `am start -W -n ${packageName}/${TIKTOK_SHARE_ACTIVITY_CLASS} -a android.intent.action.SEND -t video/mp4 --grant-read-uri-permission --eu android.intent.extra.STREAM ${shellQuote(contentUri)}`,
      "sleep 6",
    ].join("; "),
    90_000,
  );
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const focus = await getFocusedWindow(config, padCode);
    if (!focus.includes(packageName)) {
      throw new Error(`TikTok share flow left TikTok before publish form: ${focus.trim()}`);
    }
    const xml = await dumpUiXml(config, padCode);
    if (isTikTokPostForm(xml)) return;
    if (isTikTokEditorPage(xml)) {
      await runAdb(
        config,
        padCode,
        `input tap ${Math.round(DEFAULT_SCREEN.width * 0.74)} ${Math.round(DEFAULT_SCREEN.height * 0.94)}; sleep 12`,
        60_000,
      );
      continue;
    }
    await runAdb(config, padCode, "sleep 5", 20_000);
  }
  throw new Error("TikTok did not reach the final Post page");
}

async function fillCaption(config: VmosConfig, padCode: string, caption: string) {
  if (!caption.trim()) return;
  await runAdb(
    config,
    padCode,
    [
      `input tap ${Math.round(DEFAULT_SCREEN.width * 0.26)} ${Math.round(DEFAULT_SCREEN.height * 0.122)}`,
      "sleep 1",
      `input text ${shellQuote(adbInputText(caption))}`,
      "sleep 1",
      "input keyevent KEYCODE_BACK",
      "sleep 1",
      "input keyevent KEYCODE_BACK",
      "sleep 1",
    ].join("; "),
  );
}

async function tapDialogCancelOrDismiss(config: VmosConfig, padCode: string) {
  await runAdb(
    config,
    padCode,
    [
      // Left-side dialog action covers "Don't allow" / "Dismiss" without granting contacts/passkey prompts.
      `input tap ${Math.round(DEFAULT_SCREEN.width * 0.29)} ${Math.round(DEFAULT_SCREEN.height * 0.64)}`,
      "sleep 2",
    ].join("; "),
  );
}

async function confirmPublishAndOpenProfile(config: VmosConfig, padCode: string, paths: {
  afterPostScreenshotPath: string;
  profileScreenshotPath: string;
}) {
  await runAdb(
    config,
    padCode,
    [
      "input keyevent KEYCODE_BACK",
      "sleep 1",
      `input tap ${Math.round(DEFAULT_SCREEN.width * 0.73)} ${Math.round(DEFAULT_SCREEN.height * 0.94)}`,
      "sleep 35",
    ].join("; "),
    90_000,
  );
  await saveScreenshot(config, padCode, paths.afterPostScreenshotPath);
  let xml = await dumpUiXml(config, padCode);
  if (hasTikTokUploadFailure(xml)) {
    return { verified: false, state: "upload_failed_retry_later", uploadProgress: extractUploadProgress(xml) };
  }
  if (isStillOnPublishEditor(xml)) {
    return { verified: false, state: "post_tap_did_not_leave_editor", uploadProgress: extractUploadProgress(xml) };
  }
  await runAdb(
    config,
    padCode,
    [
      "input keyevent KEYCODE_BACK >/dev/null 2>&1 || true",
      "sleep 2",
      `input tap ${Math.round(DEFAULT_SCREEN.width * 0.91)} ${Math.round(DEFAULT_SCREEN.height * 0.94)}`,
      "sleep 10",
    ].join("; "),
  );
  await tapDialogCancelOrDismiss(config, padCode);
  await runAdb(config, padCode, `input tap ${Math.round(DEFAULT_SCREEN.width * 0.91)} ${Math.round(DEFAULT_SCREEN.height * 0.94)}; sleep 5`);
  let uploadProgress: number | undefined;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    xml = await dumpUiXml(config, padCode);
    if (hasTikTokUploadFailure(xml)) {
      return { verified: false, state: "upload_failed_retry_later", uploadProgress: extractUploadProgress(xml) };
    }
    uploadProgress = extractUploadProgress(xml);
    if (uploadProgress === undefined && isTikTokProfilePage(xml)) break;
    await runAdb(config, padCode, `sleep ${attempt < 2 ? 20 : 45}; input tap ${Math.round(DEFAULT_SCREEN.width * 0.91)} ${Math.round(DEFAULT_SCREEN.height * 0.94)}; sleep 3`, 70_000);
  }
  await saveScreenshot(config, padCode, paths.profileScreenshotPath);
  const focus = await getFocusedWindow(config, padCode);
  xml = await dumpUiXml(config, padCode);
  uploadProgress = extractUploadProgress(xml);
  if (!focus.includes("com.ss.android.ugc")) {
    return { verified: false, state: "left_tiktok_after_post" };
  }
  if (!isTikTokProfilePage(xml)) {
    return { verified: false, state: "profile_not_reached_after_post" };
  }
  if (hasDraftOrUnpostedState(xml)) {
    return { verified: false, state: "submitted_but_saved_as_draft" };
  }
  return uploadProgress === undefined
    ? { verified: true, state: "posted_profile_checked" }
    : { verified: false, state: "submitted_but_uploading", uploadProgress };
}

async function main() {
  const raw = process.argv[2];
  if (!raw) {
    printJson({ ok: false, error: "missing JSON input" });
    process.exitCode = 1;
    return;
  }

  const input = JSON.parse(raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw) as TiktokPrepublishInput;
  if (!input.padCode) throw new Error("missing padCode");
  if (input.stopBeforePost === false && input.confirmPublish !== true) {
    throw new Error("stopBeforePost=false requires confirmPublish=true");
  }

  const mediaPath = input.mediaPath || ".runtime-tiktok-test-video.mp4";
  if (!fs.existsSync(mediaPath)) throw new Error(`mediaPath not found: ${mediaPath}`);
  const config = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });

  if (input.dryRun !== false) {
    printJson({
      ok: true,
      dryRun: true,
      hasCredentials: Boolean(config.ak && config.sk),
      padCode: input.padCode,
      mediaPath,
      stopBeforePost: input.confirmPublish === true ? false : true,
      confirmPublish: Boolean(input.confirmPublish),
    });
    return;
  }

  const pads = await listPads(config);
  const pad = pads.find((item) => item.padCode === input.padCode);
  if (!pad) throw new Error("当前人设绑定的智能體手機不存在，请进入人设设置重新绑定可用智能體手機。");
  const padInfo = await getPadInfo(config, input.padCode).catch(() => undefined);
  const packageName = await detectTikTokPackage(config, input.padCode);
  if (input.skipNetworkPreflight !== true) {
    await assertTikTokDnsResolvable(config, input.padCode);
  }
  const staged = await stageVideo(config, input.padCode, mediaPath);
  await openFinalPostPage(config, input.padCode, packageName, staged.contentUri);
  await fillCaption(
    config,
    input.padCode,
    input.caption || "Healthy workflow note: plan small steps, review results, and improve one habit at a time.",
  );

  const screenshotPath = input.screenshotPath || `.runtime-tiktok-ready-before-post-${Date.now()}.jpg`;
  await saveScreenshot(config, input.padCode, screenshotPath);
  const result: Record<string, unknown> = {
    ok: true,
    state: input.confirmPublish ? "post_pending_confirmation" : "ready_before_post",
    stoppedBeforePost: input.confirmPublish ? false : true,
    padCode: input.padCode,
    padName: pad.padName,
    packageName,
    accountHint: padInfo?.accounts || [],
    remotePath: staged.remotePath,
    contentUri: staged.contentUri,
    screenshotPath,
  };
  if (input.confirmPublish) {
    const afterPostScreenshotPath = input.afterPostScreenshotPath || `.runtime-tiktok-after-post-${Date.now()}.jpg`;
    const profileScreenshotPath = input.profileScreenshotPath || `.runtime-tiktok-profile-after-post-${Date.now()}.jpg`;
    const publishCheck = await confirmPublishAndOpenProfile(config, input.padCode, { afterPostScreenshotPath, profileScreenshotPath });
    result.ok = publishCheck.verified;
    result.state = publishCheck.state;
    if (publishCheck.uploadProgress !== undefined) result.uploadProgress = publishCheck.uploadProgress;
    result.afterPostScreenshotPath = afterPostScreenshotPath;
    result.profileScreenshotPath = profileScreenshotPath;
  }
  printJson({
    ...result,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
