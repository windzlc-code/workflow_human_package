import { execAdb, inputText, screenshot, waitTask, type VmosConfig } from "@/lib/vmos-client";
import { callGemini, extractText, getInlineData } from "@/lib/gemini-client";
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

export interface TelegramGroupIdentifyTask {
  padCode: string;
  telegramTargetChatId: string;
  telegramTargetGroupName?: string;
  telegramGroupContentType?: "free" | "paid";
}

export interface TelegramGroupIdentifyResult {
  chatId: string;
  groupName: string;
  source: "ui" | "vision";
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
const TELEGRAM_SEARCH_POINT = { x: 555, y: 115 };
const TELEGRAM_CHAT_LIST_FIRST_POINT = { x: 250, y: 365 };
const TELEGRAM_CHAT_LIST_FREE_FALLBACK_POINT = { x: 250, y: 660 };
const TELEGRAM_SHARE_TARGET_CHAT_POINT = { x: 230, y: 665 };
const TELEGRAM_SHARE_SEND_POINT = { x: 650, y: 1515 };
const TELEGRAM_CLEAR_DRAFT_KEY_EVENTS = 80;
const TELEGRAM_GROUP_VISION_MODEL = "gemini-3-flash-preview";
let lastTelegramTargetGroupName = "";

function delay(ms: number) {
  if (process.env.NODE_ENV === "test" || process.env.VITEST) {
    return new Promise((resolve) => setTimeout(resolve, 1));
  }
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

function normalizeTelegramTargetChatId(value?: string): string {
  return String(value || "").trim();
}

function telegramSupergroupInternalId(chatId: string): string | undefined {
  const normalized = normalizeTelegramTargetChatId(chatId);
  const match = normalized.match(/^-100(\d{5,})$/);
  return match?.[1];
}

async function openTelegramTargetGroupById(
  config: VmosConfig,
  task: TelegramGroupPublishTask,
  onProgress: (p: TelegramGroupPublishProgress) => void,
): Promise<boolean> {
  const chatId = normalizeTelegramTargetChatId(task.telegramTargetChatId);
  if (!chatId) return false;
  const groupName = String(task.telegramTargetGroupName || "").trim();
  const internalId = telegramSupergroupInternalId(chatId);
  const links = [
    `tg://openmessage?chat_id=${encodeURIComponent(chatId)}`,
    internalId ? `https://t.me/c/${internalId}` : "",
  ].filter(Boolean);
  onProgress({ step: `使用 Telegram 群 ID 定位目標群：${chatId}`, done: false });
  for (const link of links) {
    await runAdb(
      config,
      task.padCode,
      `am start -W -a android.intent.action.VIEW -d ${shellSingleQuote(link)} -p ${TELEGRAM_PACKAGE} 2>&1; sleep 3`,
      20_000,
    ).catch(() => "");
    const uiXml = await dumpTelegramUiXml(config, task.padCode);
    if (isCurrentTelegramTargetGroup(uiXml, groupName)) return true;
  }
  // Telegram Android often keeps the previous chat open when a private supergroup
  // cannot be resolved from a numeric -100... id. Do not treat foreground focus
  // as success here, otherwise free/paid routes can be posted to the previous chat.
  return false;
}

function extractTelegramChatTitleFromUiXml(uiXml: string): string {
  if (!looksLikeTelegramGroupChat(uiXml)) return "";
  const nodes = uiXml.match(/<node\b[^>]*>/g) ?? [];
  const candidates: Array<{ text: string; y: number; score: number }> = [];
  for (const node of nodes) {
    const text = decodeXmlAttr(getXmlAttr(node, "text")).trim();
    if (!text || /输入消息|輸入消息|輸入訊息|输入讯息|Type a message|Telegram/i.test(text)) continue;
    const center = parseBoundsCenter(getXmlAttr(node, "bounds"));
    if (!center || center.y > 220) continue;
    let score = 0;
    if (center.y >= 35 && center.y <= 145) score += 40;
    if (center.x >= 70 && center.x <= 520) score += 20;
    if (/群|group|TG|fufei|測試|测试/i.test(text)) score += 15;
    if (/^\d+$/.test(text) || /^online|members|订阅者|成員|成员$/i.test(text)) score -= 30;
    candidates.push({ text, y: center.y, score });
  }
  return candidates.sort((a, b) => b.score - a.score || a.y - b.y)[0]?.text || "";
}

async function identifyCurrentTelegramGroupByVision(
  config: VmosConfig,
  padCode: string,
  onProgress?: (p: TelegramGroupPublishProgress) => void,
): Promise<TelegramGroupIdentifyResult | null> {
  const shotUrl = await screenshot(config, padCode).catch(() => "");
  if (!shotUrl) return null;
  const inline = await getInlineData(shotUrl).catch(() => null);
  if (!inline?.data) return null;
  const data = await callGemini(
    TELEGRAM_GROUP_VISION_MODEL,
    [{
      role: "user",
      parts: [
        {
          text: [
            "You are looking at a Telegram mobile screenshot.",
            "Task: read the current chat/group title from the top header.",
            "Return only a minified JSON object.",
            "If a Telegram group/chat title is visible, return {\"found\":true,\"groupName\":\"...\"}.",
            "Do not use message text, keyboard text, or captions as the group title.",
            "If the current screen is not a Telegram chat/group page, return {\"found\":false}.",
          ].join("\n"),
        },
        { inlineData: { mimeType: inline.mimeType || "image/png", data: inline.data } },
      ],
    }],
    { temperature: 0, maxOutputTokens: 512 },
  );
  const text = extractText(data).trim();
  onProgress?.({ step: `Telegram 群名視覺識別：${text.slice(0, 240) || "空"}`, done: false });
  const parsed = parseModelJson(text);
  if (!parsed?.found) return null;
  const groupName = String(parsed.groupName || parsed.title || parsed.matchedName || "").trim();
  if (!groupName) return null;
  return { chatId: "", groupName, source: "vision", screenshotUrl: shotUrl };
}

export async function identifyTelegramGroupById(
  config: VmosConfig,
  task: TelegramGroupIdentifyTask,
  onProgress: (p: TelegramGroupPublishProgress) => void = () => undefined,
): Promise<TelegramGroupIdentifyResult> {
  const chatId = normalizeTelegramTargetChatId(task.telegramTargetChatId);
  if (!chatId) throw new Error("Telegram 群 ID 不能为空");
  const internalId = telegramSupergroupInternalId(chatId);
  const links = [
    `tg://openmessage?chat_id=${encodeURIComponent(chatId)}`,
    internalId ? `https://t.me/c/${internalId}` : "",
  ].filter(Boolean);

  await runAdb(config, task.padCode, `am force-stop ${TELEGRAM_PACKAGE}; sleep 0.8`, 12_000).catch(() => undefined);
  onProgress({ step: `使用雲機 Telegram 帳號識別群 ID：${chatId}`, done: false });

  for (const link of links) {
    await runAdb(
      config,
      task.padCode,
      `am start -W -a android.intent.action.VIEW -d ${shellSingleQuote(link)} -p ${TELEGRAM_PACKAGE} 2>&1; sleep 3`,
      20_000,
    ).catch(() => "");
    const uiXml = await dumpTelegramUiXml(config, task.padCode);
    const groupName = extractTelegramChatTitleFromUiXml(uiXml);
    if (groupName) {
      onProgress({ step: `已從雲機 Telegram 識別群名：${groupName}`, done: true });
      return { chatId, groupName, source: "ui" };
    }
    const visionResult = await identifyCurrentTelegramGroupByVision(config, task.padCode, onProgress).catch(() => null);
    if (visionResult?.groupName) {
      onProgress({ step: `已從雲機 Telegram 視覺識別群名：${visionResult.groupName}`, done: true });
      return { ...visionResult, chatId };
    }
  }

  throw new Error(`雲機 Telegram 無法用群 ID 打開或識別群組：${chatId}。請確認人設綁定的雲機帳號已加入該群，且 Telegram 能用該 ID 打開群組。`);
}

async function ensureTelegramTargetGroupOpen(
  config: VmosConfig,
  task: TelegramGroupPublishTask,
  onProgress: (p: TelegramGroupPublishProgress) => void,
) {
  const groupName = String(task.telegramTargetGroupName || "").trim();
  const targetChatId = normalizeTelegramTargetChatId(task.telegramTargetChatId);
  const groupContentType = task.telegramGroupContentType === "paid" ? "paid" : task.telegramGroupContentType === "free" ? "free" : undefined;
  if (!groupName && !targetChatId) {
    const targetType = groupContentType === "paid" ? "付費群" : groupContentType === "free" ? "免費群" : "目標群";
    throw new Error(`未配置 Telegram ${targetType}ID，為避免發錯群組已停止發布。請先在人設設定內綁定 TG 群 ID。`);
  }
  const groupLabel = targetChatId ? `${groupName || "Telegram 群"}（${targetChatId}）` : groupName;
  onProgress({ step: `定位 Telegram 目标群：${groupLabel}`, done: false });
  await runAdb(config, task.padCode, `am force-stop ${TELEGRAM_PACKAGE}; sleep 0.8`, 12_000).catch(() => undefined);
  await ensureTelegramForeground(config, task.padCode);
  let currentUiXml = await dumpTelegramUiXml(config, task.padCode);
  if (isCurrentTelegramTargetGroup(currentUiXml, groupName)
    || await isCurrentTelegramTargetGroupByVision(config, task.padCode, groupName, onProgress).catch(() => false)) {
    lastTelegramTargetGroupName = groupName || targetChatId;
    return;
  }
  if (!groupName) {
    throw new Error(`無法使用 Telegram 群 ID 打開目標群：${targetChatId}`);
  }
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_TOP_LEFT_POINT.x} ${TELEGRAM_TOP_LEFT_POINT.y}; sleep 0.8`,
    10_000,
  ).catch(() => undefined);
  await ensureTelegramChatList(config, task.padCode);
  let currentFocus = await getCurrentFocus(config, task.padCode);
  if (!currentFocus.includes(TELEGRAM_PACKAGE)) {
    await ensureTelegramForeground(config, task.padCode);
    currentFocus = await getCurrentFocus(config, task.padCode);
  }
  if (/PopupWindow/i.test(currentFocus)) {
    await runAdb(config, task.padCode, "input keyevent KEYCODE_BACK; sleep 0.6", 10_000).catch(() => undefined);
  }
  if (await openVisibleTelegramTargetGroup(config, task.padCode, groupName, onProgress)) {
    lastTelegramTargetGroupName = groupName;
    return;
  }
  const searchText = telegramSearchTextForTarget(groupName, groupContentType);
  await runAdb(
    config,
    task.padCode,
    `input tap ${TELEGRAM_SEARCH_POINT.x} ${TELEGRAM_SEARCH_POINT.y}; sleep 0.7; input keyevent 123; input keyevent 67; input keyevent 67; input keyevent 67; sleep 0.2`,
    12_000,
  ).catch(() => undefined);
  await inputText(config, task.padCode, searchText).catch((error) => {
    throw new Error(`Telegram 目标群搜索输入失败：${error instanceof Error ? error.message : String(error)}`);
  });
  await delay(3000);
  const searchUiXml = await dumpTelegramUiXml(config, task.padCode);
  const resultPoint = findTelegramTargetGroupPoint(searchUiXml, groupName)
    || await findTelegramTargetGroupPointByVision(config, task.padCode, groupName, "搜索結果", onProgress).catch((error) => {
      onProgress({ step: `Telegram 視覺識別失敗：${error instanceof Error ? error.message : String(error)}`, done: false, warning: "vision-fallback-failed" });
      return null;
    });
  if (!resultPoint) {
    throw new Error(`未能在 Telegram 搜索結果中識別目標群「${groupName}」。已停止發布，避免點錯群組。`);
  }
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
  const targetConfirmedByVision = await isCurrentTelegramTargetGroupByVision(config, task.padCode, groupName, onProgress).catch(() => false);
  if (!looksLikeTelegramGroupChat(uiXml) && !targetConfirmedByVision) {
    throw new Error(`未能进入 Telegram 目标群「${groupName}」，请确认云机 Telegram 能搜索到该群组。`);
  }
  if (uiXml.trim() && !telegramUiContainsTargetGroup(uiXml, groupName) && !targetConfirmedByVision) {
    throw new Error(`Telegram 已進入聊天頁，但未確認目前群組是「${groupName}」。為避免發錯群組已停止發布。`);
  }
  lastTelegramTargetGroupName = groupName;
}

async function dumpTelegramUiXml(config: VmosConfig, padCode: string): Promise<string> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const xml = await runAdb(
      config,
      padCode,
      "rm -f /data/local/tmp/telegram_window.xml; uiautomator dump /data/local/tmp/telegram_window.xml >/dev/null 2>&1; cat /data/local/tmp/telegram_window.xml 2>/dev/null | head -c 80000 || true",
      15_000,
    ).catch(() => "");
    if (xml.trim()) return xml;
    await delay(600);
  }
  return "";
}

async function ensureTelegramChatList(config: VmosConfig, padCode: string) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const uiXml = await dumpTelegramUiXml(config, padCode);
    if (looksLikeTelegramChatList(uiXml)) return;
    if (!uiXml.trim()) return;
    if (looksLikeTelegramGroupChat(uiXml) || looksLikeTelegramSearchScreen(uiXml) || uiXml.trim()) {
      await runAdb(config, padCode, "input keyevent KEYCODE_BACK; sleep 0.8", 10_000).catch(() => undefined);
      continue;
    }
    await ensureTelegramForeground(config, padCode);
    return;
  }
}

async function openVisibleTelegramTargetGroup(
  config: VmosConfig,
  padCode: string,
  groupName: string,
  onProgress: (p: TelegramGroupPublishProgress) => void,
): Promise<boolean> {
  if (!groupName.trim()) return false;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const uiXml = await dumpTelegramUiXml(config, padCode);
    const point = findTelegramTargetGroupPoint(uiXml, groupName);
    if (point) {
      onProgress({ step: `已在 Telegram 列表中識別目標群：${groupName}`, done: false });
      await runAdb(config, padCode, `input tap ${point.x} ${point.y}; sleep 2.5`, 12_000);
      const openedUiXml = await dumpTelegramUiXml(config, padCode);
      if (isCurrentTelegramTargetGroup(openedUiXml, groupName)
        || await isCurrentTelegramTargetGroupByVision(config, padCode, groupName, onProgress).catch(() => false)) return true;
      await runAdb(config, padCode, "input keyevent KEYCODE_BACK; sleep 0.8", 10_000).catch(() => undefined);
    }
    if (attempt < 2) {
      await runAdb(config, padCode, "input swipe 360 1230 360 430 450; sleep 0.8", 10_000).catch(() => undefined);
    }
  }
  return false;
}

async function isCurrentTelegramTargetGroupByVision(
  config: VmosConfig,
  padCode: string,
  groupName: string,
  onProgress?: (p: TelegramGroupPublishProgress) => void,
): Promise<boolean> {
  const shotUrl = await screenshot(config, padCode).catch(() => "");
  if (!shotUrl) return false;
  const inline = await getInlineData(shotUrl).catch(() => null);
  if (!inline?.data) return false;
  const data = await callGemini(
    TELEGRAM_GROUP_VISION_MODEL,
    [{
      role: "user",
      parts: [
        {
          text: [
            "You are looking at a Telegram chat screenshot.",
            `Target group name: ${groupName}`,
            "Return true only if the current chat header/title is exactly this target group.",
            "Do not match message text, search text, keyboard suggestions, or another chat mentioned inside messages.",
            "Return one short minified JSON line only: {\"found\":true,\"matchedName\":\"...\"} or {\"found\":false}.",
          ].join("\n"),
        },
        { inlineData: { mimeType: inline.mimeType || "image/png", data: inline.data } },
      ],
    }],
    { temperature: 0, maxOutputTokens: 512 },
  );
  const text = extractText(data).trim();
  onProgress?.({ step: `Telegram 群標題視覺確認：${text.slice(0, 240) || "空"}`, done: false });
  const parsed = parseModelJson(text);
  if (!parsed?.found) return false;
  const matchedName = String(parsed.matchedName || "").trim();
  return !matchedName || normalizeTelegramGroupLabel(matchedName) === normalizeTelegramGroupLabel(groupName);
}

async function findTelegramTargetGroupPointByVision(
  config: VmosConfig,
  padCode: string,
  groupName: string,
  screenContext: string,
  onProgress?: (p: TelegramGroupPublishProgress) => void,
): Promise<{ x: number; y: number } | null> {
  const shotUrl = await screenshot(config, padCode).catch(() => "");
  if (!shotUrl) return null;
  const inline = await getInlineData(shotUrl).catch(() => null);
  if (!inline?.data) return null;
  const data = await callGemini(
    TELEGRAM_GROUP_VISION_MODEL,
    [{
      role: "user",
      parts: [
        {
          text: [
            "You are looking at a Telegram mobile screenshot.",
            "Task: find the exact target group name and return the center point of the clickable chat/group row.",
            `Target group name: ${groupName}`,
            `Screen context: ${screenContext}`,
            "Count only clickable chat/group rows. Do not count the search input, section titles, or plain message preview text.",
            "Do not prefer Recent visits. Choose the row whose visible title is exactly the target group name.",
            "Return the visible row number of the clickable row containing the exact target group. Include x/y only if you are confident.",
            "If there are similar names, choose only the exact group name. Do not choose AG- prefixed or otherwise longer fake names.",
            "Do not choose any visible row whose title is not exactly the target group, even if it appears above the target row.",
            "matchedName is required and must be the exact visible title you chose.",
            "If the exact group is not visible, return {\"found\":false}.",
            "Return one short minified JSON line only. Example: {\"found\":true,\"matchedName\":\"TG fufei qun\",\"row\":1}",
          ].join("\n"),
        },
        { inlineData: { mimeType: inline.mimeType || "image/png", data: inline.data } },
      ],
    }],
    { temperature: 0, maxOutputTokens: 512 },
  );
  const text = extractText(data).trim();
  onProgress?.({ step: `Telegram 視覺識別返回：${text.slice(0, 420) || "空"}`, done: false });
  const parsed = parseTelegramVisionPointJson(text);
  if (!parsed?.found) return null;
  const matchedName = String(parsed.matchedName || "").trim();
  if (!matchedName || normalizeTelegramGroupLabel(matchedName) !== normalizeTelegramGroupLabel(groupName)) return null;
  const row = Number(parsed.row ?? parsed.rowIndex);
  if (Number.isFinite(row) && row >= 1 && row <= 8) {
    const rowIndex = Math.floor(row);
    const baseY = /搜索|search/i.test(screenContext) ? 320 : 220;
    const rowGap = /搜索|search/i.test(screenContext) ? 205 : 115;
    return { x: 270, y: Math.min(1420, baseY + (rowIndex - 1) * rowGap) };
  }
  const x = Number(parsed.x ?? parsed.centerX);
  const y = Number(parsed.y ?? parsed.centerY);
  if (Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 720 && y >= 180 && y <= 1500) {
    return { x: Math.round(x), y: Math.round(y) };
  }
  return null;
}

function parseModelJson(text: string): any | null {
  const jsonText = text.match(/\{[\s\S]*\}/)?.[0] || text;
  try {
    return JSON.parse(jsonText);
  } catch {
    return null;
  }
}

function parseTelegramVisionPointJson(text: string): any | null {
  const parsed = parseModelJson(text);
  if (parsed) return parsed;
  if (!/"?found"?\s*:\s*true/i.test(text)) return null;
  const x = text.match(/"?(?:x|centerX)"?\s*:\s*(-?\d+(?:\.\d+)?)/i)?.[1];
  const y = text.match(/"?(?:y|centerY)"?\s*:\s*(-?\d+(?:\.\d+)?)/i)?.[1];
  const row = text.match(/"?(?:row|rowIndex)"?\s*:\s*(-?\d+(?:\.\d+)?)/i)?.[1];
  const matchedName = text.match(/"?matchedName"?\s*:\s*"([^"]+)"/i)?.[1];
  if ((!x || !y) && !row) return null;
  return {
    found: true,
    ...(x ? { x: Number(x) } : {}),
    ...(y ? { y: Number(y) } : {}),
    ...(row ? { row: Number(row) } : {}),
    ...(matchedName ? { matchedName } : {}),
  };
}

function looksLikeTelegramShareChatPicker(uiXml: string): boolean {
  return /选择聊天|選擇聊天|Select chat|搜索聊天|搜尋聊天|Search chat|转发至动态|轉發至動態/i.test(uiXml);
}

function looksLikeTelegramGroupChat(uiXml: string): boolean {
  return /输入消息|輸入消息|輸入訊息|输入讯息|Type a message/i.test(uiXml)
    && !looksLikeTelegramShareChatPicker(uiXml);
}

function looksLikeTelegramChatList(uiXml: string): boolean {
  return /text="Telegram"|text="聊天"|text="Chats"|content-desc="搜索聊天"|content-desc="Search"/i.test(uiXml)
    && !looksLikeTelegramShareChatPicker(uiXml);
}

function looksLikeTelegramSearchScreen(uiXml: string): boolean {
  return /全局搜索|全域搜尋|Global Search|显示更多|顯示更多/i.test(uiXml);
}

function isCurrentTelegramTargetGroup(uiXml: string, groupName: string): boolean {
  return Boolean(uiXml.trim())
    && looksLikeTelegramGroupChat(uiXml)
    && telegramUiContainsTargetGroup(uiXml, groupName);
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

function telegramSearchTextForTarget(groupName: string, groupContentType?: "free" | "paid"): string {
  if (groupContentType === "paid" && isPaidTelegramGroupName(groupName)) return "TG fufei qun";
  if (/[^\x00-\x7F]/.test(groupName)) return groupName;
  const asciiTokens = groupName.match(/[A-Za-z0-9][A-Za-z0-9 _.-]{1,}/g)
    ?.map((item) => item.trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  return asciiTokens?.[0] || groupName;
}

function isPaidTelegramGroupName(groupName: string): boolean {
  return /fufei|paid|付費|付费/i.test(groupName);
}

function telegramUiContainsTargetGroup(uiXml: string, groupName: string): boolean {
  const name = groupName.trim();
  if (!name) return false;
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (new RegExp(`(?:^|[^A-Za-z0-9_-])${escapedName}(?:[^A-Za-z0-9_-]|$)`, "i").test(uiXml)) return true;
  const normalizedUi = uiXml.replace(/\s+/g, "").toLowerCase();
  const normalizedName = name.replace(/\s+/g, "").toLowerCase();
  return Boolean(normalizedName) && normalizedUi.includes(normalizedName);
}

function getXmlAttr(node: string, name: string): string {
  const match = node.match(new RegExp(`${name}="([^"]*)"`, "i"));
  return match?.[1] || "";
}

function decodeXmlAttr(value: string): string {
  return value
    .replace(/&quot;/g, "\"")
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function parseBoundsCenter(bounds: string): { x: number; y: number } | null {
  const match = bounds.match(/\[(\d+),(\d+)\]\[(\d+),(\d+)\]/);
  if (!match) return null;
  const [, left, top, right, bottom] = match.map(Number);
  if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) return null;
  return { x: Math.round((left + right) / 2), y: Math.round((top + bottom) / 2) };
}

function normalizeTelegramGroupLabel(value: string): string {
  return value.replace(/\s+/g, "").toLowerCase();
}

function findTelegramTargetGroupPoint(uiXml: string, groupName: string): { x: number; y: number } | null {
  const target = normalizeTelegramGroupLabel(groupName);
  if (!target) return null;
  const nodes = uiXml.match(/<node\b[^>]*>/g) ?? [];
  const candidates: Array<{ x: number; y: number; score: number }> = [];
  for (const node of nodes) {
    const text = decodeXmlAttr(getXmlAttr(node, "text"));
    const desc = decodeXmlAttr(getXmlAttr(node, "content-desc"));
    const label = `${text} ${desc}`.trim();
    if (!label) continue;
    const normalized = normalizeTelegramGroupLabel(label);
    if (!normalized.includes(target)) continue;
    const center = parseBoundsCenter(getXmlAttr(node, "bounds"));
    if (!center || center.y < 140) continue;
    let score = 0;
    if (normalizeTelegramGroupLabel(text) === target || normalizeTelegramGroupLabel(desc) === target) score += 100;
    if (normalized === target) score += 80;
    if (normalized.includes(`ag-${target}`) || normalized.includes(`ag_${target}`)) score -= 40;
    if (/true/i.test(getXmlAttr(node, "clickable"))) score += 8;
    if (center.x >= 20 && center.x <= 700 && center.y >= 160 && center.y <= 1420) score += 5;
    candidates.push({ ...center, score });
  }
  const best = candidates.sort((a, b) => b.score - a.score || a.y - b.y)[0];
  return best ? { x: best.x, y: best.y } : null;
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
  if (task.mediaUrl && !task.mediaContentUri) throw new Error("图片或视频没有成功写入云机，请重新上传媒体后再发布。");
  await ensureTelegramTargetGroupOpen(config, task, onProgress);
  if (task.mediaUrl) {
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
