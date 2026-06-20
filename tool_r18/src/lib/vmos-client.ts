/**
 * VMOSCloud OpenAPI 客戶端
 * 檔案：https://cloud.vmoscloud.com/vmoscloud/doc/zh/server/OpenAPI.html
 * 認證：HMAC-SHA256 簽名（AK/SK）— AWS-style
 */

const API_HOST = "api.vmoscloud.com";
const isViteDev = typeof import.meta !== "undefined" && !!import.meta.env?.DEV;
const API_BASE = isViteDev ? "/vmos-api" : `https://${API_HOST}`;
const SERVICE = "armcloud-paas";
const CONTENT_TYPE = "application/json;charset=UTF-8";
const SIGNED_HEADERS = "content-type;host;x-content-sha256;x-date";
const RETRYABLE_HTTP_STATUSES = new Set([429, 500, 502, 503, 504]);
const RETRY_DELAYS_MS = [1000, 2000, 4000];
const REQUEST_TIMEOUT_MS = 30000;
const DEFAULT_VMOS_SCREEN = { width: 720, height: 1600 };

// ─── 簽名工具 ────────────────────────────────────────────────────────────────

function utcTimestamp(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    now.getUTCFullYear().toString() +
    pad(now.getUTCMonth() + 1) +
    pad(now.getUTCDate()) +
    "T" +
    pad(now.getUTCHours()) +
    pad(now.getUTCMinutes()) +
    pad(now.getUTCSeconds()) +
    "Z"
  );
}

async function hmacSha256Bytes(key: ArrayBuffer, data: string): Promise<ArrayBuffer> {
  const keyMaterial = await crypto.subtle.importKey(
    "raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  return crypto.subtle.sign("HMAC", keyMaterial, new TextEncoder().encode(data));
}

async function hmacSha256FromStr(key: string, data: string): Promise<ArrayBuffer> {
  const keyMaterial = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  return crypto.subtle.sign("HMAC", keyMaterial, new TextEncoder().encode(data));
}

async function sha256Hex(data: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(data));
  return bufToHex(buf);
}

function bufToHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * 生成 vmoscloud HMAC-SHA256 Authorization 頭
 * 簽名演算法參考官方 Python 示例：
 * canonical = "host:{host}\nx-date:{xDate}\ncontent-type:{ct}\nsignedHeaders:{sh}\nx-content-sha256:{xcs}"
 * stringToSign = "HMAC-SHA256\n{xDate}\n{shortDate}/{service}/request\n{sha256(canonical)}"
 * signingKey = hmac(hmac(hmac(sk, shortDate), service), "request")
 */
async function buildAuthHeader(
  ak: string,
  sk: string,
  bodyStr: string,
  xDate: string
): Promise<{ authorization: string; xContentSha256: string }> {
  const xcs = await sha256Hex(bodyStr);
  const shortDate = xDate.substring(0, 8);

  // 1. canonical string
  const canonical = [
    `host:${API_HOST}`,
    `x-date:${xDate}`,
    `content-type:${CONTENT_TYPE}`,
    `signedHeaders:${SIGNED_HEADERS}`,
    `x-content-sha256:${xcs}`,
  ].join("\n");

  // 2. string to sign
  const credentialScope = `${shortDate}/${SERVICE}/request`;
  const canonicalHash = await sha256Hex(canonical);
  const stringToSign = `HMAC-SHA256\n${xDate}\n${credentialScope}\n${canonicalHash}`;

  // 3. signing key: hmac(hmac(hmac(sk, shortDate), service), "request")
  const k1 = await hmacSha256FromStr(sk, shortDate);
  const k2 = await hmacSha256Bytes(k1, SERVICE);
  const signingKey = await hmacSha256Bytes(k2, "request");

  // 4. signature
  const signature = bufToHex(await hmacSha256Bytes(signingKey, stringToSign));

  return {
    authorization: `HMAC-SHA256 Credential=${ak}, SignedHeaders=${SIGNED_HEADERS}, Signature=${signature}`,
    xContentSha256: xcs,
  };
}

// ─── 通用請求 ────────────────────────────────────────────────────────────────

export interface VmosConfig {
  ak?: string;
  sk?: string;
  accounts?: VmosCredential[];
}

export interface VmosCredential {
  name?: string;
  ak: string;
  sk: string;
}

const vmosAPI = () => (typeof window !== "undefined" ? (window as any).electronAPI?.vmos : undefined);
const padCredentialCache = new Map<string, VmosCredential>();
const taskCredentialCache = new Map<string, VmosCredential>();

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createTimeoutSignal(timeoutMs: number): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(new Error(`VMOSCloud request timeout:${timeoutMs}`)), timeoutMs);
  return {
    signal: controller.signal,
    cleanup: () => globalThis.clearTimeout(timer),
  };
}

async function vmosRequest<T = unknown>(
  config: VmosConfig,
  path: string,
  body: Record<string, unknown>
): Promise<T> {
  const json = await vmosRequestEnvelope(config, path, body);
  return json.data as T;
}

async function vmosRequestEnvelope<T extends Record<string, unknown> = Record<string, unknown>>(
  config: VmosConfig,
  path: string,
  body: Record<string, unknown>
): Promise<T> {
  const accounts = normalizeCredentials(config);
  const credential = selectVmosCredential(config, body);
  if (!credential) {
    throw new Error("未設定固定 VMOS AK/SK，請在 Electron 主程式設定中寫入金鑰");
  }

  // JSON.stringify 預設就是緊湊 JSON；不要對整段 JSON 做空白替換，
  // 否則會破壞 scriptContent/text 等字串值裡的 ADB 命令和正文空格。
  const compactBody = JSON.stringify(body);
  let lastError: unknown;
  const triedCredentials = new Set<string>();
  const candidateCredentials = [credential];
  const padCodes = bodyPadCodes(body);
  if (accounts.length > 1 && padCodes.length > 0) {
    candidateCredentials.push(...accounts.filter((account) => account.ak !== credential.ak || account.sk !== credential.sk));
  }

  for (const candidate of candidateCredentials) {
    const credentialKey = `${candidate.ak}\n${candidate.sk}`;
    if (triedCredentials.has(credentialKey)) continue;
    triedCredentials.add(credentialKey);

    try {
      return await vmosRequestEnvelopeWithCredential<T>(candidate, path, body, compactBody);
    } catch (error) {
      lastError = error;
      if (!isVmosInstanceNotFoundError(error) || candidateCredentials.length <= 1) break;
    }
  }

  if (isVmosInstanceNotFoundError(lastError)) {
    throw new Error("当前人设绑定的云机不存在，请进入人设设置重新绑定可用云机。");
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

async function vmosRequestEnvelopeWithCredential<T extends Record<string, unknown> = Record<string, unknown>>(
  credential: VmosCredential,
  path: string,
  body: Record<string, unknown>,
  compactBody: string,
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < RETRY_DELAYS_MS.length; attempt++) {
    const xDate = utcTimestamp();
    const { authorization, xContentSha256 } = await buildAuthHeader(
      credential.ak, credential.sk, compactBody, xDate
    );

    const request = createTimeoutSignal(REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": CONTENT_TYPE,
          "x-date": xDate,
          "x-host": API_HOST,
          "x-content-sha256": xContentSha256,
          authorization,
        },
        body: compactBody,
        signal: request.signal,
      });

      if (RETRYABLE_HTTP_STATUSES.has(res.status) && attempt < RETRY_DELAYS_MS.length - 1) {
        lastError = new Error(`VMOSCloud HTTP ${res.status}`);
        await delay(RETRY_DELAYS_MS[attempt]);
        continue;
      }

      const text = await res.text();
      let json: any;
      try {
        json = text ? JSON.parse(text) : null;
      } catch {
        throw new Error(`VMOSCloud API 返回异常响应 HTTP ${res.status}，不是有效 JSON`);
      }
      if (!json) {
        throw new Error(`VMOSCloud API 返回空响应 HTTP ${res.status}`);
      }
      if (json.code !== 0 && json.code !== 200) {
        throw createVmosApiError(json.code, json.msg);
      }
      rememberCredentialFromResponse(body, json.data, credential);
      return json as T;
    } catch (error) {
      lastError = error;
      if (isVmosInstanceNotFoundError(error)) break;
      if (attempt >= RETRY_DELAYS_MS.length - 1) break;
      await delay(RETRY_DELAYS_MS[attempt]);
    } finally {
      request.cleanup();
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

function createVmosApiError(code: unknown, message: unknown): Error {
  const error = new Error(`VMOSCloud API 錯誤 [${code}]: ${message}`);
  (error as Error & { vmosCode?: string }).vmosCode = String(code ?? "");
  return error;
}

function isVmosInstanceNotFoundError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const vmosCode = (error as Error & { vmosCode?: string }).vmosCode;
  return vmosCode === "2020" || /VMOSCloud API 錯誤 \[2020\]|Instance not found/i.test(error.message);
}

function normalizeCredentials(config: VmosConfig): VmosCredential[] {
  const accounts = Array.isArray(config.accounts)
    ? config.accounts
        .map((item) => ({
          name: item?.name,
          ak: String(item?.ak || "").trim(),
          sk: String(item?.sk || "").trim(),
        }))
        .filter((item) => item.ak && item.sk)
    : [];
  const primaryAk = String(config.ak || "").trim();
  const primarySk = String(config.sk || "").trim();
  if (primaryAk && primarySk && !accounts.some((item) => item.ak === primaryAk && item.sk === primarySk)) {
    accounts.unshift({ ak: primaryAk, sk: primarySk });
  }
  return accounts;
}

function bodyPadCodes(body: Record<string, unknown>): string[] {
  if (Array.isArray(body.padCodes)) return body.padCodes.map((item) => String(item || "").trim()).filter(Boolean);
  if (body.padCode) return [String(body.padCode).trim()].filter(Boolean);
  return [];
}

function selectVmosCredential(config: VmosConfig, body: Record<string, unknown>): VmosCredential | null {
  const accounts = normalizeCredentials(config);
  if (accounts.length === 0) return null;
  if (accounts.length === 1) return accounts[0];

  const padCodes = bodyPadCodes(body);
  if (padCodes.length === 1) {
    const cached = padCredentialCache.get(padCodes[0]);
    if (cached) return cached;
  }

  const taskIds = Array.isArray(body.taskIds) ? body.taskIds.map((item) => String(item)) : [];
  if (taskIds.length === 1) {
    const cached = taskCredentialCache.get(taskIds[0]);
    if (cached) return cached;
  }

  return accounts[0];
}

function rememberCredentialFromResponse(body: Record<string, unknown>, data: unknown, credential: VmosCredential) {
  for (const padCode of bodyPadCodes(body)) {
    padCredentialCache.set(padCode, credential);
  }

  const scan = (value: unknown) => {
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach(scan);
      return;
    }
    if (typeof value !== "object") return;
    const obj = value as Record<string, unknown>;
    if (obj.padCode) padCredentialCache.set(String(obj.padCode), credential);
    if (obj.taskId !== undefined && obj.taskId !== null && obj.taskId !== "") {
      taskCredentialCache.set(String(obj.taskId), credential);
    }
  };
  scan(data);
}

async function listPadsForCredential(credential: VmosCredential): Promise<PadInfo[]> {
  const allPads: PadInfo[] = [];
  let pageNum = 1;
  let totalPage = 1;

  do {
    const data = await vmosRequest<{ pageData: PadInfo[]; totalPage?: number }>(
      { ak: credential.ak, sk: credential.sk },
      "/vcpcloud/api/padApi/infos",
      { pageNum, pageSize: 100 }
    );
    for (const pad of data.pageData ?? []) {
      if (pad?.padCode) padCredentialCache.set(pad.padCode, credential);
      allPads.push({ ...pad, vmosAccountName: credential.name });
    }
    totalPage = data.totalPage ?? pageNum;
    pageNum += 1;
  } while (pageNum <= totalPage);

  return allPads;
}

// ─── API 封裝 ────────────────────────────────────────────────────────────────

export interface PadInfo {
  id?: number | string;
  padCode: string;
  equipmentId?: number | string;
  padName?: string;
  /** 10=執行中 20=關機 等 */
  padStatus: number;
  /** 例如 realdevice_720x1600x300 */
  screenLayoutCode?: string;
  padGrade?: string;
  padType?: string;
  deviceCode?: string;
  vmosAccountName?: string;
  [key: string]: unknown;
}

/** 取得雲機列表（分頁） */
export async function listPads(config: VmosConfig = {}): Promise<PadInfo[]> {
  const ipc = vmosAPI();
  if (ipc?.listPads) {
    return ipc.listPads();
  }

  const accounts = normalizeCredentials(config);
  if (accounts.length > 1) {
    const settled = await Promise.allSettled(accounts.map((account) => listPadsForCredential(account)));
    const byPadCode = new Map<string, PadInfo>();
    const errors: string[] = [];
    for (const item of settled) {
      if (item.status === "fulfilled") {
        for (const pad of item.value) {
          if (pad.padCode && !byPadCode.has(pad.padCode)) byPadCode.set(pad.padCode, pad);
        }
      } else {
        errors.push(item.reason instanceof Error ? item.reason.message : String(item.reason));
      }
    }
    if (byPadCode.size === 0 && errors.length > 0) {
      throw new Error(`VMOS 多账号云机列表读取失败：${errors.join("；")}`);
    }
    return Array.from(byPadCode.values());
  }

  const allPads: PadInfo[] = [];
  let pageNum = 1;
  let totalPage = 1;

  do {
    const data = await vmosRequest<{ pageData: PadInfo[]; totalPage?: number }>(
      config,
      "/vcpcloud/api/padApi/infos",
      { pageNum, pageSize: 100 }
    );
    allPads.push(...(data.pageData ?? []));
    totalPage = data.totalPage ?? pageNum;
    pageNum += 1;
  } while (pageNum <= totalPage);

  return allPads;
}

/** 啟動 App */
export async function startApp(
  config: VmosConfig,
  padCode: string,
  pkgName: string
): Promise<number> {
  const ipc = vmosAPI();
  if (ipc?.startApp) {
    return ipc.startApp({ padCode, pkgName });
  }

  const data = await vmosRequest<unknown>(config, "/vcpcloud/api/padApi/startApp", {
    padCodes: [padCode],
    pkgName,
  });
  return (data as Array<{ taskId: number }>)[0].taskId;
}

/** 模擬觸控點選（舊介面，保留相容） */
export async function simulateTouch(
  config: VmosConfig,
  padCode: string,
  x: number,
  y: number,
  width = 1080,
  height = 1920
): Promise<string> {
  const ipc = vmosAPI();
  if (ipc?.simulateTouch) {
    return ipc.simulateTouch({ padCode, x, y, width, height });
  }

  const data = await vmosRequest<{ taskId: string }>(
    config,
    "/vcpcloud/api/padApi/simulateTouch",
    {
      padCodes: [padCode],
      width,
      height,
      pointCount: 1,
      positions: [{ x: Math.round(x), y: Math.round(y) }],
    }
  );
  return data.taskId;
}

/**
 * 擬人化點選（推薦）
 * 注意：同一裝置 2 秒內重複呼叫會被拒絕（錯誤碼 1218）
 * 預設螢幕尺寸 720x1600 / 360dpi
 */
export async function simulateClick(
  config: VmosConfig,
  padCode: string,
  x: number,
  y: number,
  width = DEFAULT_VMOS_SCREEN.width,
  height = DEFAULT_VMOS_SCREEN.height
): Promise<void> {
  const ipc = vmosAPI();
  if (ipc?.simulateClick) {
    return ipc.simulateClick({ padCode, x, y, width, height });
  }

  await vmosRequest(config, "/vcpcloud/api/padApi/simulateClick", {
    padCodes: [padCode],
    x: Math.round(x),
    y: Math.round(y),
    width,
    height,
  });
}

export type SwipeDirection =
  | "LEFT_TO_RIGHT"
  | "RIGHT_TO_LEFT"
  | "TOP_TO_BOTTOM"
  | "BOTTOM_TO_TOP";

/**
 * 擬人化滑動
 * 支援固定方向（自動生成軌跡）或自訂起點終點
 */
export async function simulateSwipe(
  config: VmosConfig,
  padCode: string,
  direction: SwipeDirection,
  opts?: {
    width?: number;
    height?: number;
    startX?: number;
    startY?: number;
    endX?: number;
    endY?: number;
  }
): Promise<void> {
  const ipc = vmosAPI();
  if (ipc?.simulateSwipe) {
    return ipc.simulateSwipe({
      padCode,
      direction,
      width: opts?.width ?? DEFAULT_VMOS_SCREEN.width,
      height: opts?.height ?? DEFAULT_VMOS_SCREEN.height,
      ...(opts || {}),
    });
  }

  await vmosRequest(config, "/vcpcloud/api/padApi/simulateSwipe", {
    padCodes: [padCode],
    direction,
    width: opts?.width ?? DEFAULT_VMOS_SCREEN.width,
    height: opts?.height ?? DEFAULT_VMOS_SCREEN.height,
    ...(opts?.startX !== undefined && { startX: Math.round(opts.startX) }),
    ...(opts?.startY !== undefined && { startY: Math.round(opts.startY) }),
    ...(opts?.endX !== undefined && { endX: Math.round(opts.endX) }),
    ...(opts?.endY !== undefined && { endY: Math.round(opts.endY) }),
  });
}

/** 向輸入框注入文字 */
export async function inputText(
  config: VmosConfig,
  padCode: string,
  text: string
): Promise<string> {
  const ipc = vmosAPI();
  if (ipc?.inputText) {
    return ipc.inputText({ padCode, text });
  }

  const data = await vmosRequest<{ taskId?: string | number } | Array<{ taskId?: string | number }>>(config, "/vcpcloud/api/padApi/inputText", {
    padCodes: [padCode],
    text,
  });
  const taskId = Array.isArray(data) ? data[0]?.taskId : data.taskId;
  if (taskId === undefined || taskId === null || `${taskId}`.trim() === "") {
    throw new Error("VMOSCloud inputText 未返回 taskId，无法确认文本输入任务");
  }
  return String(taskId);
}

/** 透過 URL 上傳檔案到雲機指定路徑 */
export async function uploadFileByUrl(
  config: VmosConfig,
  padCode: string,
  fileUrl: string,
  /** 雲機內目標路徑，如 /sdcard/DCIM/post_image.jpg */
  targetPath: string
): Promise<string> {
  const ipc = vmosAPI();
  if (ipc?.uploadFileByUrl) {
    return ipc.uploadFileByUrl({ padCode, fileUrl, targetPath });
  }

  const data = await vmosRequest<{ taskId?: string | number } | Array<{ taskId?: string | number }>>(
    config,
    "/vcpcloud/api/padApi/uploadFileV3",
    {
      padCodes: [padCode],
      url: fileUrl,
      customizeFilePath: targetPath,
    }
  );
  const taskId = Array.isArray(data) ? data[0]?.taskId : data.taskId;
  if (taskId === undefined || taskId === null || taskId === "") {
    throw new Error("VMOSCloud uploadFileV3 未返回 taskId，无法确认文件上传任务");
  }
  return String(taskId);
}

export interface TaskResult {
  taskId: number;
  /** 實測：3=成功 4=失敗，其他值=執行中 */
  taskStatus: number;
  taskResult?: string;
  errorMsg?: string;
}

export interface VmosAutomationScriptSummary {
  id: number | string;
  name?: string;
  description?: string;
  category?: string;
  platform?: string;
  userId?: number | string;
  targetPackage?: string;
  minVersionCode?: number | string;
  maxVersionCode?: number | string;
  minVersionName?: string;
  maxVersionName?: string;
  version?: number | string;
  locale?: string;
  fallbackLocale?: string | null;
  [key: string]: unknown;
}

export interface VmosAutomationScriptDetail extends VmosAutomationScriptSummary {
  content?: string;
}

export async function listAutomationScripts(
  config: VmosConfig,
  opts: { page?: number; size?: number; category?: "official" | "user" | string; platform?: string } = {}
): Promise<{ total?: number; list: VmosAutomationScriptSummary[]; [key: string]: unknown }> {
  const ipc = vmosAPI();
  if (ipc?.listAutomationScripts) {
    return ipc.listAutomationScripts(opts);
  }

  return vmosRequest(config, "/vcpcloud/api/padApi/automation/scripts/list", {
    page: opts.page ?? 1,
    size: opts.size ?? 20,
    ...(opts.category ? { category: opts.category } : {}),
    ...(opts.platform ? { platform: opts.platform } : {}),
  });
}

export async function getAutomationScript(
  config: VmosConfig,
  scriptId: number | string
): Promise<VmosAutomationScriptDetail> {
  const ipc = vmosAPI();
  if (ipc?.getAutomationScript) {
    return ipc.getAutomationScript({ scriptId });
  }

  return vmosRequest(config, "/vcpcloud/api/padApi/automation/scripts/get", { scriptId });
}

/** 非同步執行 ADB/Shell 命令，回傳數字 taskId */
export async function execAdb(
  config: VmosConfig,
  padCode: string,
  scriptContent: string
): Promise<number> {
  const ipc = vmosAPI();
  if (ipc?.execAdb) {
    return ipc.execAdb({ padCode, scriptContent });
  }

  // data 是陣列：[{ padCode, vmStatus, taskId }]
  const data = await vmosRequest<unknown>(
    config,
    "/vcpcloud/api/padApi/asyncCmd",
    { padCodes: [padCode], scriptContent }
  );
  return (data as Array<{ taskId: number }>)[0].taskId;
}

/** 查詢任務執行結果（taskIds 為陣列） */
/** 重啟雲機實例，用於 asyncCmd 任務卡死或系統無響應恢復。 */
export async function restartPad(
  config: VmosConfig,
  padCode: string
): Promise<unknown> {
  const ipc = vmosAPI();
  if (ipc?.restartPad) {
    return ipc.restartPad({ padCode });
  }

  return vmosRequest(config, "/vcpcloud/api/padApi/restart", {
    padCodes: [padCode],
  });
}

export async function getTaskResult(
  config: VmosConfig,
  taskId: number
): Promise<TaskResult> {
  const ipc = vmosAPI();
  if (ipc?.getTaskResult) {
    return ipc.getTaskResult({ taskId });
  }

  const data = await vmosRequest<unknown>(
    config,
    "/vcpcloud/api/padApi/padTaskDetail",
    { taskIds: [taskId] }
  );
  return (data as TaskResult[])[0];
}

/** 等待任務完成（輪詢） */
export async function waitTask(
  config: VmosConfig,
  taskId: number,
  timeoutMs = 60000,
  intervalMs = 2000
): Promise<TaskResult> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await getTaskResult(config, taskId);
    if (result.taskStatus === 3) return result;
    if (result.taskStatus === 4) throw new Error(`任務失敗: ${result.errorMsg}`);
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`任務超時（${timeoutMs / 1000}s）`);
}

/** 截圖，返回存取 URL */
export async function screenshot(
  config: VmosConfig,
  padCode: string
): Promise<string> {
  const ipc = vmosAPI();
  if (ipc?.screenshot) {
    return ipc.screenshot({ padCode });
  }

  const data = await vmosRequest<unknown>(
    config,
    "/vcpcloud/api/padApi/screenshot",
    { padCodes: [padCode] }
  );
  return (data as Array<{ accessUrl: string }>)[0].accessUrl;
}

/** 查詢雲機資訊 */
export async function getPadInfo(
  config: VmosConfig,
  padCode: string
): Promise<PadInfo> {
  const ipc = vmosAPI();
  if (ipc?.getPadInfo) {
    return ipc.getPadInfo({ padCode });
  }

  const data = await vmosRequest<PadInfo>(
    config,
    "/vcpcloud/api/padApi/padInfo",
    { padCode }
  );
  return data;
}
