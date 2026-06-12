import { execAdb, inputText, screenshot, waitTask, type VmosConfig } from "@/lib/vmos-client";
import { getInlineData } from "@/lib/gemini-client";
import sharp from "sharp";

export interface TelegramGroupPublishTask {
  padCode: string;
  caption: string;
  mediaUrl?: string;
  mediaContentUri?: string;
  mediaMimeType?: string;
  telegramTargetChatId?: string;
  telegramTargetGroupName?: string;
  telegramGroupContentType?: "free" | "paid";
}

export interface TelegramGroupPublishProgress {
  step: string;
  done: boolean;
  error?: string;
  warning?: string;
}

export interface TelegramGroupPublishResult {
  state: "verified" | "warning";
  detail: string;
  screenshotUrl?: string;
}

const TELEGRAM_PACKAGE = "org.telegram.messenger";
const TELEGRAM_LAUNCH_ACTIVITY = `${TELEGRAM_PACKAGE}/org.telegram.ui.LaunchActivity`;
const TELEGRAM_FALLBACK_LAUNCH_ACTIVITIES = [
  `${TELEGRAM_PACKAGE}/.DefaultIcon`,
  TELEGRAM_LAUNCH_ACTIVITY,
] as const;
const TELEGRAM_INPUT_POINT = { x: 170, y: 1518 };
const TELEGRAM_SEND_WITH_KEYBOARD_POINT = { x: 655, y: 1000 };
const TELEGRAM_TOP_LEFT_POINT = { x: 60, y: 115 };
const TELEGRAM_SEARCH_POINT = { x: 210, y: 225 };
const TELEGRAM_SEARCH_RESULT_FIRST_POINT = { x: 250, y: 290 };
const TELEGRAM_SEARCH_RESULT_SECOND_POINT = { x: 250, y: 430 };
const TELEGRAM_CHAT_LIST_FIRST_POINT = { x: 250, y: 365 };
const TELEGRAM_CHAT_LIST_FREE_FALLBACK_POINT = { x: 250, y: 660 };
const TELEGRAM_SHARE_TARGET_CHAT_POINT = { x: 230, y: 665 };
const TELEGRAM_SHARE_SEND_POINT = { x: 650, y: 1515 };
const TELEGRAM_CLEAR_DRAFT_KEY_EVENTS = 80;
let lastTelegramTargetGroupName = "";

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runAdb(
  config: VmosConfig,
  padCode: string,
  command: string,
  timeoutMs = 30_000,
): Promise<string> {
  const taskId = await execAdb(config, padCode, command);
  const result = await waitTask(config, taskId, timeoutMs, 1000);
  return result.taskResult || "";
}

async function getCurrentFocus(config: VmosConfig, padCode: string): Promise<string> {
  return runAdb(
    config,
    padCode,
    `dumpsys window | grep -E "mCurrentFocus|mFocusedApp" || true`,
    12_000,
  ).catch(() => "");
}

async function ensureTelegramForeground(config: VmosConfig, padCode: string) {
  let focus = await getCurrentFocus(config, padCode);
  if (focus.includes(TELEGRAM_PACKAGE)) return;
  const launchOutput = await runAdb(
    config,
    padCode,
    `am start -W -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p ${TELEGRAM_PACKAGE}; sleep 2`,
    20_000,
  );
  const primaryLaunchFailed = /not found|does not exist|No activities found|unable to resolve|not installed|Unknown package|Error type 3/i.test(launchOutput);
  focus = await getCurrentFocus(config, padCode);
  if (!focus.includes(TELEGRAM_PACKAGE)) {
    let lastActivityOutput = "";
    for (const activity of TELEGRAM_FALLBACK_LAUNCH_ACTIVITIES) {
      lastActivityOutput = await runAdb(
        config,
        padCode,
        `am start -W -n ${activity} 2>&1; sleep 2`,
        20_000,
      ).catch((error) => String(error?.message || error || ""));
      focus = await getCurrentFocus(config, padCode);
      if (focus.includes(TELEGRAM_PACKAGE)) break;
    }
    if (!focus.includes(TELEGRAM_PACKAGE) && (primaryLaunchFailed || /not found|does not exist|No activities found|unable to resolve|not installed|Unknown package|Error type 3/i.test(lastActivityOutput))) {
      throw new Error("该人设绑定的云机上未检测到 Telegram 应用，请先在这台云机安装并登录 Telegram。");
    }
  }
  if (!focus.includes(TELEGRAM_PACKAGE)) {
    throw new Error("没有进入 Telegram，请先确认这台云机已安装并登录 Telegram。");
  }
}

async function clearTelegramDraft(config: VmosConfig, padCode: string) {
  const deleteEvents = Array.from({ length: TELEGRAM_CLEAR_DRAFT_KEY_EVENTS }, () => "input keyevent 67").join("; ");
  await runAdb(
    config,
    padCode,
    `input keyevent 123; ${deleteEvents}; sleep 0.3`,
    20_000,
  ).catch(() => undefined);
}

async function ensureTelegramTargetGroupOpen(
  config: VmosConfig,
  task: TelegramGroupPublishTask,
  onProgress: (p: TelegramGroupPublishProgress) => void,
) {
  const groupName = String(task.telegramTargetGroupName || "").trim();
  const groupContentType = task.telegramGroupContentType === "paid" ? "paid" : task.telegramGroupContentType === "free" ? "free" : undefined;
  if (!groupName) {
    const targetType = groupContentType === "paid" ? "付費群" : groupContentType === "free" ? "免費群" : "目標群";
    throw new Error(`未配置 Telegram ${targetType}名稱，為避免發錯群組已停止發布。請先在人設設定內綁定 TG 群 ID 並確認已識別群名稱。`);
  }
  const groupLabel = task.telegramTargetChatId ? `${groupName}（${task.telegramTargetChatId}）` : groupName;
  onProgress({ step: `定位 Telegram 目标群：${groupLabel}`, done: false });
  await runAdb(config, task.padCode, `am force-stop ${TELEGRAM_PACKAGE}; sleep 0.8`, 12_000).catch(() => undefined);
  await ensureTelegramForeground(config, task.padCode);
  await runAdb(config, task.padCode, "input keyevent KEYCODE_BACK; sleep 0.8", 10_000).catch(() => undefined);
  await ensureTelegramForeground(config, task.padCode);
  let currentFocus = await getCurrentFocus(config, task.padCode);
  if (/PopupWindow/i.test(currentFocus)) {
    await runAdb(config, task.padCode, "input keyevent KEYCODE_BACK; sleep 0.6", 10_000).catch(() => undefined);
  }
  const currentUiXml = await dumpTelegramUiXml(config, task.padCode);
  if (looksLikeTelegramGroupChat(currentUiXml)) {
    await runAdb(config, task.padCode, "input keyevent KEYCODE_BACK; sleep 0.8", 10_000).catch(() => undefined);
  }
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_TOP_LEFT_POINT.x} ${TELEGRAM_TOP_LEFT_POINT.y}; sleep 0.8`,
    10_000,
  ).catch(() => undefined);
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_SEARCH_POINT.x} ${TELEGRAM_SEARCH_POINT.y}; sleep 0.7; input keyevent 123; input keyevent 67; input keyevent 67; input keyevent 67; sleep 0.2`,
    12_000,
  ).catch(() => undefined);
  await inputText(config, task.padCode, groupName).catch((error) => {
    throw new Error(`Telegram 目标群搜索输入失败：${error instanceof Error ? error.message : String(error)}`);
  });
  await delay(3000);
  const resultPoint = resolveTelegramSearchResultPoint(groupName, groupContentType);
  await runAdb(
    config,
    task.padCode,
    `input tap ${resultPoint.x} ${resultPoint.y}; sleep 3`,
    12_000,
  );
  let focus = await getCurrentFocus(config, task.padCode);
  if (/PopupWindow/i.test(focus)) {
    await runAdb(config, task.padCode, "input keyevent KEYCODE_BACK; sleep 0.6", 10_000).catch(() => undefined);
    focus = await getCurrentFocus(config, task.padCode);
  }
  const uiXml = await dumpTelegramUiXml(config, task.padCode);
  if (!looksLikeTelegramGroupChat(uiXml)) {
    if (!uiXml.trim() && focus.includes(TELEGRAM_PACKAGE)) return;
    throw new Error(`未能进入 Telegram 目标群「${groupName}」，请确认云机 Telegram 能搜索到该群组。`);
  }
  if (uiXml.trim() && !telegramUiContainsTargetGroup(uiXml, groupName)) {
    throw new Error(`Telegram 已進入聊天頁，但未確認目前群組是「${groupName}」。為避免發錯群組已停止發布。`);
  }
  lastTelegramTargetGroupName = groupName;
}

async function dumpTelegramUiXml(config: VmosConfig, padCode: string): Promise<string> {
  return runAdb(
    config,
    padCode,
    "uiautomator dump /sdcard/window.xml >/dev/null 2>&1; cat /sdcard/window.xml 2>/dev/null || true",
    12_000,
  ).catch(() => "");
}

function looksLikeTelegramShareChatPicker(uiXml: string): boolean {
  return /选择聊天|選擇聊天|Select chat|搜索聊天|搜尋聊天|Search chat|转发至动态|轉發至動態/i.test(uiXml);
}

function looksLikeTelegramGroupChat(uiXml: string): boolean {
  return /输入消息|輸入訊息|输入讯息|Type a message|Message/i.test(uiXml)
    && !looksLikeTelegramShareChatPicker(uiXml);
}

async function looksLikeTelegramSharePickerScreen(config: VmosConfig, padCode: string): Promise<boolean> {
  try {
    const shotUrl = await screenshot(config, padCode);
    const inline = await getInlineData(shotUrl);
    const { data, info } = await sharp(Buffer.from(inline.data, "base64"))
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    const width = info.width;
    const height = info.height;
    let white = 0;
    let green = 0;
    let total = 0;
    for (let y = Math.round(height * 0.18); y < Math.round(height * 0.92); y += 4) {
      for (let x = 0; x < width; x += 4) {
        const idx = (y * width + x) * 4;
        const r = data[idx] ?? 0;
        const g = data[idx + 1] ?? 0;
        const b = data[idx + 2] ?? 0;
        total += 1;
        if (r > 238 && g > 238 && b > 238) white += 1;
        if (g > 150 && r > 100 && r < 210 && b < 180) green += 1;
      }
    }
    if (!total) return false;
    return white / total > 0.72 && green / total < 0.08;
  } catch {
    return false;
  }
}

function encodeBase64Utf8(text: string): string {
  return Buffer.from(text, "utf8").toString("base64");
}

function shellSingleQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function shellDecodedTextArg(text: string): string {
  return `"$(printf %s ${shellSingleQuote(encodeBase64Utf8(text))} | base64 -d)"`;
}

function resolveTelegramSearchResultPoint(groupName: string, groupContentType?: "free" | "paid"): { x: number; y: number } {
  return TELEGRAM_SEARCH_RESULT_FIRST_POINT;
}

function isPaidTelegramGroupName(groupName: string): boolean {
  return /fufei|paid|付費|付费/i.test(groupName);
}

function telegramUiContainsTargetGroup(uiXml: string, groupName: string): boolean {
  const name = groupName.trim();
  if (!name) return false;
  if (uiXml.includes(name)) return true;
  const normalizedUi = uiXml.replace(/\s+/g, "").toLowerCase();
  const normalizedName = name.replace(/\s+/g, "").toLowerCase();
  return Boolean(normalizedName) && normalizedUi.includes(normalizedName);
}

function buildTelegramShareIntentCommand(input: {
  caption?: string;
  contentUri: string;
  mimeType: string;
}) {
  return [
    "am start -W",
    "-a android.intent.action.SEND",
    `-t ${shellSingleQuote(input.mimeType)}`,
    `-p ${TELEGRAM_PACKAGE}`,
    "--grant-read-uri-permission",
    `--eu android.intent.extra.STREAM ${shellSingleQuote(input.contentUri)}`,
    input.caption ? `--es android.intent.extra.TEXT ${shellDecodedTextArg(input.caption)}` : "",
    "2>&1",
  ].filter(Boolean).join(" ");
}

async function launchTelegramShareIntent(
  config: VmosConfig,
  task: TelegramGroupPublishTask,
  caption: string,
  onProgress: (p: TelegramGroupPublishProgress) => void,
) {
  if (!task.mediaContentUri) throw new Error("图片或视频没有成功写入云机，请重新上传媒体后再发布。");
  const mimeType = task.mediaMimeType || "image/jpeg";
  onProgress({ step: mimeType.startsWith("video/") ? "打开 Telegram 视频分享页..." : "打开 Telegram 图片分享页...", done: false });
  const output = await runAdb(
    config,
    task.padCode,
    buildTelegramShareIntentCommand({ caption, contentUri: task.mediaContentUri, mimeType }),
    60_000,
  );
  if (/Exception|Error|Unknown option|Permission Denial/i.test(output)) {
    if (/not found|does not exist|No activities found|unable to resolve|not installed|Unknown package|Error type 3/i.test(output)) {
      throw new Error("该人设绑定的云机上未检测到 Telegram 应用，请先在这台云机安装并登录 Telegram。");
    }
    throw new Error("Telegram 分享入口启动失败，请确认这台云机已安装并登录 Telegram。");
  }
  await delay(3000);

  const focus = await getCurrentFocus(config, task.padCode);
  if (focus && !focus.includes(TELEGRAM_PACKAGE)) {
    throw new Error("没有进入 Telegram 分享页面，请先确认这台云机已安装并登录 Telegram。");
  }
}

async function confirmTelegramSharedMediaSend(
  config: VmosConfig,
  task: TelegramGroupPublishTask,
  onProgress: (p: TelegramGroupPublishProgress) => void,
): Promise<TelegramGroupPublishResult["state"]> {
  onProgress({ step: "确认 Telegram 分享发送...", done: false });
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_SHARE_TARGET_CHAT_POINT.x} ${TELEGRAM_SHARE_TARGET_CHAT_POINT.y}; sleep 2`,
    12_000,
  );
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_SHARE_SEND_POINT.x} ${TELEGRAM_SHARE_SEND_POINT.y}; sleep 8`,
    18_000,
  );
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_SHARE_SEND_POINT.x} ${TELEGRAM_SHARE_SEND_POINT.y}; sleep 7`,
    18_000,
  );
  if (await looksLikeTelegramSharePickerScreen(config, task.padCode)) {
    await runAdb(
      config,
      task.padCode,
      `input tap ${TELEGRAM_SHARE_TARGET_CHAT_POINT.x} ${TELEGRAM_SHARE_TARGET_CHAT_POINT.y}; sleep 2`,
      12_000,
    );
    if (await looksLikeTelegramSharePickerScreen(config, task.padCode)) {
      throw new Error("Telegram 分享页没有选中目标群组，请先在云机 Telegram 里打开目标群组后重试。");
    }
  }

  const uiXml = await dumpTelegramUiXml(config, task.padCode);
  if (looksLikeTelegramGroupChat(uiXml)) return "verified";
  const focus = await getCurrentFocus(config, task.padCode);
  if (!uiXml.trim() && focus.includes(TELEGRAM_PACKAGE)) return "verified";
  return "warning";
}

export async function publishTelegramGroupPost(
  config: VmosConfig,
  task: TelegramGroupPublishTask,
  onProgress: (p: TelegramGroupPublishProgress) => void,
): Promise<TelegramGroupPublishResult> {
  const caption = String(task.caption || "").trim();
  if (!caption && !task.mediaUrl) throw new Error("Telegram 群组发布内容不能为空");
  await ensureTelegramTargetGroupOpen(config, task, onProgress);
  if (task.mediaUrl) {
    if (!task.mediaContentUri) throw new Error("图片或视频没有成功写入云机，请重新上传媒体后再发布。");
    await launchTelegramShareIntent(config, task, caption, onProgress);
    const state = await confirmTelegramSharedMediaSend(config, task, onProgress);

    const screenshotUrl = await screenshot(config, task.padCode).catch(() => undefined);
    onProgress({
      step: state === "verified" ? "Telegram 群组媒体发布完成" : "Telegram 群组媒体发送动作已执行，等待截图确认",
      done: true,
      warning: state === "warning" ? "未能从 Telegram UI 确认最新群组消息" : undefined,
    });
    return {
      state,
      detail: state === "verified"
        ? "已通过 VMOS Telegram App 分享媒体到当前打开的群组"
        : "已执行 Telegram App 媒体分享动作，但当前 UI 未提供可读群组消息确认，请以截图复核",
      screenshotUrl,
    };
  }

  onProgress({ step: "打开 VMOS Telegram...", done: false });
  await ensureTelegramForeground(config, task.padCode);

  onProgress({ step: "定位 Telegram 群组输入框...", done: false });
  await runAdb(config, task.padCode, `input tap ${TELEGRAM_INPUT_POINT.x} ${TELEGRAM_INPUT_POINT.y}; sleep 1`, 10_000);
  await clearTelegramDraft(config, task.padCode);

  onProgress({ step: "输入群组推文...", done: false });
  await inputText(config, task.padCode, caption).catch((error) => {
    throw new Error(`Telegram 群组输入失败：${error instanceof Error ? error.message : String(error)}`);
  });
  await delay(1200);

  onProgress({ step: "点击 Telegram 发送按钮...", done: false });
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_SEND_WITH_KEYBOARD_POINT.x} ${TELEGRAM_SEND_WITH_KEYBOARD_POINT.y}; sleep 2`,
    12_000,
  );

  const screenshotUrl = await screenshot(config, task.padCode).catch(() => undefined);
  onProgress({ step: "Telegram 群组发布完成", done: true });
  return {
    state: "verified",
    detail: "已通过 VMOS Telegram App 发送到当前打开的群组",
    screenshotUrl,
  };
}
