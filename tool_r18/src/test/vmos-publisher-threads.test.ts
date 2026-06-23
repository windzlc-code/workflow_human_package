import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import {
  assertWarmupMinimumCompletion,
  buildAcpWarmupFallbackRows,
  buildWarmupCommentTurnSchedule,
  buildWarmupInterestKeywords,
  buildWarmupSearchKeywordCandidates,
  chooseWarmupTimedInteraction,
  scoreWarmupPostRelevance,
  buildThreadsPublishSampleLabel,
  buildThreadsShareIntentCommand,
  detectAndroidCameraFromUiXml,
  detectThreadsBlockedScreenFromUiXml,
  detectThreadsComposerLocally,
  detectThreadsFullscreenMediaViewerLocally,
  detectThreadsGalleryPickerLocally,
  detectThreadsHomeFeedLocally,
  detectThreadsInAppBrowserLocally,
  detectThreadsPostSuccessToastLocally,
  detectThreadsPostActionSheetLocally,
  detectThreadsPrivateReplyPromptLocally,
  detectThreadsProfilePageLocally,
  detectThreadsReplyComposerLocally,
  detectThreadsSearchOverlayLocally,
  detectThreadsShareSheetLocally,
  detectThreadsSideDrawerLocally,
  detectThreadsWarmupCommentPostedCueFromUiXml,
  extractWarmupPostPreviewFromUiXml,
  extractThreadsProfileUsernameFromUiXml,
  findThreadsHomeFeedActionTargetsFromUiXml,
  findThreadsComposerInputTarget,
  findThreadsComposerPublishButtonTarget,
  findThreadsBottomSearchTabTarget,
  findThreadsReplyComposerInputTarget,
  findThreadsReplySendButtonTarget,
  findThreadsSearchInputTarget,
  findThreadsTopSearchButtonTarget,
  findAcpReplySendButtonPointFromScreenshot,
  findThreadsProfileVideoTabTarget,
  finalizeWarmupComment,
  chooseThreadsGalleryMarkerToKeep,
  getLocalVisualVerificationSupport,
  getThreadsProfileReferenceImageBestMatchForTest,
  getThreadsLauncherIconFallbackPoints,
  getThreadsLauncherIconTapCandidates,
  hasRepeatedWarmupCommentText,
  hasThreadsReplyComposerText,
  hasExplicitSuccessCue,
  isNearDuplicateWarmupComment,
  isUsableWarmupComment,
  isThreadsWarmupCommentExpectedSkipMessage,
  looksLikeThreadsActivityUiXml,
  looksLikeThreadsPostOptionsSheetUiXml,
  looksLikeThreadsReplyComposerUiXml,
  looksLikeThreadsSearchResultsUiXml,
  looksLikeThreadsThreadDetailUiXml,
  parsePublishVisionResult,
  planRiskManagedWarmupConfig,
  sanitizeWarmupComment,
  scalePointBetweenScreens,
  scalePointFromReferenceScreen,
  scaleScreenshotPointToAdbPointForSizes,
  shouldUseThreadsShareIntentPath,
} from "@/lib/vmos-publisher";

async function makePngDataUrl(
  width: number,
  height: number,
  input: { create: { width: number; height: number; channels: 3 | 4; background: string | { r: number; g: number; b: number; alpha?: number } } },
  composites: Array<{ input: Buffer; left: number; top: number }> = [],
): Promise<string> {
  const sharp = (await import("sharp")).default;
  const buffer = await sharp(input)
    .composite(composites)
    .png()
    .toBuffer();
  return `data:image/png;base64,${buffer.toString("base64")}`;
}

type PromotedSampleAssertion = {
  detector: string;
  expected: boolean | string;
};

type PromotedSample = {
  id: string;
  screenshot?: string;
  screenshotSize?: { width?: number; height?: number };
  xml?: string;
  assertions?: PromotedSampleAssertion[];
};

function loadPromotedThreadsSamples(): PromotedSample[] {
  const manifestPath = path.resolve("src", "test", "fixtures", "threads-publish-samples", "manifest.json");
  if (!fs.existsSync(manifestPath)) return [];
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  return Array.isArray(manifest?.samples) ? manifest.samples : [];
}

async function runPromotedSampleAssertion(sample: PromotedSample, assertion: PromotedSampleAssertion) {
  const fixtureRoot = path.resolve("src", "test", "fixtures", "threads-publish-samples");
  const screenshotPath = sample.screenshot ? path.join(fixtureRoot, sample.screenshot) : "";
  const xmlPath = sample.xml ? path.join(fixtureRoot, sample.xml) : "";
  const dataUrl = screenshotPath && fs.existsSync(screenshotPath)
    ? `data:image/${path.extname(screenshotPath).replace(".", "") || "jpeg"};base64,${fs.readFileSync(screenshotPath).toString("base64")}`
    : "";
  const xml = xmlPath && fs.existsSync(xmlPath) ? fs.readFileSync(xmlPath, "utf8") : "";

  let actual: boolean | string | null;
  switch (assertion.detector) {
    case "home_feed":
      actual = await detectThreadsHomeFeedLocally(dataUrl);
      break;
    case "gallery_picker":
      actual = await detectThreadsGalleryPickerLocally(dataUrl);
      break;
    case "composer":
      actual = await detectThreadsComposerLocally(dataUrl);
      break;
    case "reply_composer":
      actual = await detectThreadsReplyComposerLocally(dataUrl);
      break;
    case "profile_page":
      actual = await detectThreadsProfilePageLocally(dataUrl);
      break;
    case "share_sheet":
      actual = await detectThreadsShareSheetLocally(dataUrl);
      break;
    case "post_action_sheet":
      actual = await detectThreadsPostActionSheetLocally(dataUrl);
      break;
    case "fullscreen_media_viewer":
      actual = await detectThreadsFullscreenMediaViewerLocally(dataUrl);
      break;
    case "side_drawer":
      actual = await detectThreadsSideDrawerLocally(dataUrl);
      break;
    case "search_overlay":
      actual = await detectThreadsSearchOverlayLocally(dataUrl);
      break;
    case "android_camera":
      actual = detectAndroidCameraFromUiXml(xml);
      break;
    case "blocked_screen":
      actual = Boolean(detectThreadsBlockedScreenFromUiXml(xml));
      break;
    default:
      throw new Error(`Unknown promoted detector: ${assertion.detector}`);
  }

  if (assertion.expected === false) {
    expect(Boolean(actual), `${sample.id}:${assertion.detector}`).toBe(false);
  } else {
    expect(actual, `${sample.id}:${assertion.detector}`).toEqual(assertion.expected);
  }
}

describe("Threads publish verification", () => {
  it("allows local visual verification in the Node daemon runtime", () => {
    expect(getLocalVisualVerificationSupport()).toEqual({ supported: true });
  });

  it("runs promoted publish samples as detector regressions", async () => {
    const samples = loadPromotedThreadsSamples();
    for (const sample of samples) {
      expect(sample.assertions?.length || 0, `${sample.id}:assertions`).toBeGreaterThan(0);
      for (const assertion of sample.assertions || []) {
        await runPromotedSampleAssertion(sample, assertion);
      }
    }
  });

  it("only keeps promoted screenshot samples captured at the fixed 720x1600 VMOS resolution", async () => {
    const samples = loadPromotedThreadsSamples();
    const fixtureRoot = path.resolve("src", "test", "fixtures", "threads-publish-samples");
    const sharp = (await import("sharp")).default;

    for (const sample of samples) {
      if (!sample.screenshot) continue;
      const screenshotPath = path.join(fixtureRoot, sample.screenshot);
      expect(fs.existsSync(screenshotPath), `${sample.id}:screenshot exists`).toBe(true);
      const metadata = await sharp(screenshotPath).metadata();
      expect(
        { width: metadata.width, height: metadata.height },
        `${sample.id}:screenshot resolution`,
      ).toEqual({ width: 720, height: 1600 });
      if (sample.screenshotSize) {
        expect(sample.screenshotSize, `${sample.id}:manifest screenshotSize`).toEqual({ width: 720, height: 1600 });
      }
    }
  });

  it("scales tall-screen fallback tap points for different VMOS resolutions", () => {
    expect(scalePointFromReferenceScreen(
      { width: 720, height: 1600 },
      { x: 620, y: 1530 },
    )).toEqual({ x: 620, y: 1530 });

    expect(scalePointFromReferenceScreen(
      { width: 1080, height: 2400 },
      { x: 620, y: 1530 },
    )).toEqual({ x: 930, y: 2295 });

    expect(scalePointFromReferenceScreen(
      { width: 540, height: 1200 },
      { x: 360, y: 610 },
    )).toEqual({ x: 270, y: 458 });
  });

  it("maps VMOS screenshot coordinates onto the ADB tap coordinate grid", () => {
    const screenshotSize = { width: 720, height: 1600 };
    const adbSize = { width: 720, height: 1280 };

    expect(scaleScreenshotPointToAdbPointForSizes(
      { x: 360, y: 1000 },
      screenshotSize,
      adbSize,
    )).toEqual({ x: 360, y: 800 });

    expect(scalePointBetweenScreens(
      { x: 720, y: 1600 },
      screenshotSize,
      adbSize,
    )).toEqual({ x: 719, y: 1279 });
  });

  it("builds warmup interest keywords from persona content", () => {
    expect(buildWarmupInterestKeywords({
      description: "台股投資與財經觀察",
      personality: "理性但口語",
    })).toEqual(expect.arrayContaining(["台股", "投資", "財經"]));

    expect(buildWarmupInterestKeywords({
      description: "喜歡咖啡館、下午茶和城市生活",
    })).toEqual(expect.arrayContaining(["咖啡", "甜點", "手搖飲"]));
  });

  it("scores warmup post relevance against persona keywords", () => {
    const financePersona = {
      description: "台股投资与财经观察",
      personality: "理性但口语",
    };

    const financeResult = scoreWarmupPostRelevance("今天台股量能回温，半导体财报和市场行情都值得继续观察", financePersona);
    expect(financeResult.relevant).toBe(true);
    expect(financeResult.score).toBeGreaterThanOrEqual(5);

    const cafeResult = scoreWarmupPostRelevance("周末去巷口咖啡馆喝拿铁，甜点和下午茶都很放松", financePersona);
    expect(cafeResult.relevant).toBe(false);
  });

  it("scores real estate search results as relevant for a property agent persona", () => {
    const propertyPersona = {
      description: "房产中介，分享买房、租屋、不动产和工地人生观察",
      personality: "专业但自然",
    };

    const result = scoreWarmupPostRelevance("50歲紐約富豪年賺1億美金，真正的財富是世代持有不動產。你是做什麼才買得起這房子的？", propertyPersona);
    expect(result.relevant).toBe(true);
    expect(result.matched).toEqual(expect.arrayContaining(["real-estate-domain"]));
  });

  it("builds ACP warmup fallback rows in the fixed 720x1600 coordinate grid", () => {
    const rows = buildAcpWarmupFallbackRows(720, 1600);

    expect(rows[0]?.like).toEqual({ x: 158, y: 976 });
    expect(rows[0]?.comment).toEqual({ x: 277, y: 976 });
    expect(rows[rows.length - 1]?.like.y).toBeLessThan(1600);
  });

  it("builds stable filesystem-safe publish sample labels", () => {
    expect(buildThreadsPublishSampleLabel({
      platform: "threads",
      mediaKind: "video",
      scenario: "Gallery confirm stuck / 系統彈窗",
      timestamp: 123,
    })).toBe("threads-video-gallery-confirm-stuck-123");
  });

  it("keeps the Test1 launcher Threads icon as the first fallback target", () => {
    const points = getThreadsLauncherIconFallbackPoints(720, 1600);
    expect(points[0]).toEqual({ x: 444, y: 348 });
    expect(getThreadsLauncherIconFallbackPoints(720, 1600)[0]).toEqual({ x: 444, y: 348 });
    expect(points).toContainEqual({ x: 444, y: 430 });
  });

  it("keeps the newest visible gallery selection when multiple media are already selected", () => {
    const markers = [
      { x: 640, y: 500 },
      { x: 450, y: 260 },
      { x: 640, y: 260 },
      { x: 220, y: 500 },
    ];

    expect(chooseThreadsGalleryMarkerToKeep(markers)).toEqual({ x: 450, y: 260 });
    expect(chooseThreadsGalleryMarkerToKeep(markers, { x: 635, y: 255 })).toEqual({ x: 640, y: 260 });
  });

  it("keeps launcher icon taps on the fixed 720x1600 coordinate grid", () => {
    const points = getThreadsLauncherIconTapCandidates(
      { width: 720, height: 1600 },
      { width: 720, height: 1600 },
    );
    expect(points).toContainEqual({ x: 444, y: 348 });
    expect(points).toContainEqual({ x: 444, y: 430 });
  });

  it("recognizes Test1 Threads profile screenshots with onboarding cards", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "debug-shots",
      "threads-debug-1-1779589179347.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;
    expect(await detectThreadsProfilePageLocally(dataUrl)).toBe(true);
  });

  it("recognizes an empty own Threads profile page", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "debug-shots",
      "threads-debug-1-1779644347174.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;
    expect(await detectThreadsProfilePageLocally(dataUrl)).toBe(true);
  });

  it("recognizes public news profile pages during warmup", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "debug-shots",
      "test2-current-before-news-skip-patch.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;
    expect(await detectThreadsProfilePageLocally(dataUrl)).toBe(true);
  });

  it("classifies new thread composer ahead of reply composer when both heuristics match", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-composer-controls-missing",
      "threads-threads-image-composer-controls-missing-1779641290670-1779641294200.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBe("LOCAL_COMPOSER");
  });

  it("recognizes a keyboard-open image composer captured after share intent", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-composer-controls-missing",
      "threads-threads-image-composer-controls-missing-1779647070619-1779647075286.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;
    await expect(detectThreadsSideDrawerLocally(dataUrl)).resolves.toBe(false);
    await expect(detectThreadsGalleryPickerLocally(dataUrl)).resolves.toBeNull();
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBe("LOCAL_COMPOSER");
    await expect(detectThreadsHomeFeedLocally(dataUrl)).resolves.toBeNull();
  });

  it("recognizes the Threads search overlay and does not treat it as composer", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-composer-controls-missing",
      "threads-threads-image-composer-controls-missing-1779697460562-1779697465339.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;
    await expect(detectThreadsSearchOverlayLocally(dataUrl)).resolves.toBe(true);
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBeNull();
  });

  it("recognizes Taiwan Threads success wording", () => {
    expect(hasExplicitSuccessCue("已發佈。查看或分享你的串文", "threads")).toBe(true);
    expect(hasExplicitSuccessCue("Posted - View your thread", "threads")).toBe(true);
    expect(hasExplicitSuccessCue("個人主頁看到自己剛發的串文", "threads")).toBe(true);
  });

  it("does not treat pending video upload toast as success", () => {
    expect(hasExplicitSuccessCue("發布中...... 查看", "threads")).toBe(false);
    expect(hasExplicitSuccessCue("uploading video - View", "threads")).toBe(false);
  });

  it("recognizes Threads composing blockers with publish wording variants", () => {
    expect(parsePublishVisionResult("畫面仍停在新串文，右下角有發佈按鈕").status).toBe("STILL_COMPOSING");
    expect(parsePublishVisionResult("仍在编辑页，发布按钮还在").status).toBe("STILL_COMPOSING");
  });

  it("recognizes Threads captcha and security verification blockers", () => {
    expect(parsePublishVisionResult("驗證你是真人，請完成安全驗證").status).toBe("BLOCKED");
    expect(parsePublishVisionResult("請輸入圖像上的驗證碼").status).toBe("BLOCKED");
    expect(parsePublishVisionResult("captcha required before continuing").status).toBe("BLOCKED");
  });

  it("recognizes Threads appeal review pages as blockers", () => {
    expect(parsePublishVisionResult("你已於 2026年5月24日提出申訴，資料審查通常需要約一小時，你也無法使用該帳號").status).toBe("BLOCKED");
    expect(detectThreadsBlockedScreenFromUiXml(`
      <hierarchy>
        <node text="你已於 2026年5月24日提出申訴" class="android.widget.TextView" />
        <node text="資料審查通常需要約一小時的時間。請稍後再返回這裡查看結果。" class="android.widget.TextView" />
        <node text="你的帳號不會對其他 Threads 用戶顯示，你也無法使用該帳號。" class="android.widget.TextView" />
        <node text="社群守則" class="android.widget.TextView" />
      </hierarchy>
    `)).toContain("提出申訴");
  });

  it("recognizes Threads onboarding cards as blockers", () => {
    expect(parsePublishVisionResult("畫面顯示完成個人檔案與建立串文的新號啟動卡片").status).toBe("BLOCKED");
    expect(parsePublishVisionResult("只看到追蹤個人檔案和查看個人檔案的 onboarding card").status).toBe("BLOCKED");
  });

  it("does not treat normal profile history pages with setup cards as blockers", () => {
    expect(detectThreadsBlockedScreenFromUiXml(`
      <hierarchy>
        <node text="串文" class="android.widget.TextView" />
        <node text="回覆" class="android.widget.TextView" />
        <node text="影音內容" class="android.widget.TextView" />
        <node text="轉發" class="android.widget.TextView" />
        <node text="新增個人簡介" class="android.widget.TextView" />
        <node text="建立串文" class="android.widget.TextView" />
        <node text="第一則串文" class="android.widget.TextView" />
        <node text="fuyizhang1225" class="android.widget.TextView" />
        <node text="剛去小七買完微波宵夜" class="android.widget.TextView" />
      </hierarchy>
    `)).toBeNull();
  });

  it("detects Threads login state from UIAutomator output", () => {
    expect(detectThreadsBlockedScreenFromUiXml("ERROR: null root node returned by UiTestAutomationBridge.")).toContain("畫面結構不可讀");
    expect(detectThreadsBlockedScreenFromUiXml(`
      <hierarchy>
        <node text="使用 Instagram 帳號登入" class="android.widget.TextView" bounds="[180,1300][510,1390]" />
        <node text="建立 Instagram 帳號" class="android.widget.TextView" bounds="[230,1500][490,1560]" />
      </hierarchy>
    `)).toContain("Instagram 帳號登入");
    expect(detectThreadsBlockedScreenFromUiXml(`
      <hierarchy>
        <node text="用戶名稱、電子郵件地址或手機號碼" class="android.widget.EditText" bounds="[30,710][690,820]" />
        <node text="密碼" class="android.widget.EditText" bounds="[30,845][690,956]" />
        <node text="登入" class="android.widget.Button" bounds="[30,981][690,1062]" />
        <node text="Meta" class="android.widget.TextView" bounds="[318,1540][402,1580]" />
      </hierarchy>
    `)).toContain("Instagram 登入頁");
  });

  it("does not classify a fullscreen media viewer as home feed", async () => {
    const sharp = (await import("sharp")).default;
    const media = await sharp({
      create: {
        width: 640,
        height: 720,
        channels: 3,
        background: { r: 130, g: 80, b: 40 },
      },
    })
      .png()
      .toBuffer();
    const screenshot = await makePngDataUrl(
      720,
      1600,
      { create: { width: 720, height: 1600, channels: 3, background: "black" } },
      [{ input: media, left: 40, top: 245 }],
    );

    expect(await detectThreadsFullscreenMediaViewerLocally(screenshot)).toBe(true);
    expect(await detectThreadsHomeFeedLocally(screenshot)).toBeNull();
  });

  it("matches reference images when Threads profile shows a cropped media card", async () => {
    const sharp = (await import("sharp")).default;
    const svg = `
      <svg width="960" height="960" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#f97316"/>
            <stop offset="1" stop-color="#2563eb"/>
          </linearGradient>
        </defs>
        <rect width="960" height="960" fill="url(#g)"/>
        <circle cx="720" cy="220" r="120" fill="rgba(255,255,255,0.25)"/>
        <rect x="120" y="570" width="720" height="150" rx="30" fill="rgba(0,0,0,0.35)"/>
        <text x="480" y="430" text-anchor="middle" font-family="Arial" font-size="64" font-weight="700" fill="#fff">Threads Publish Test</text>
        <text x="480" y="665" text-anchor="middle" font-family="Arial" font-size="42" fill="#fff">2026-06-05T12-59-39-266Z</text>
      </svg>
    `;
    const reference = await sharp(Buffer.from(svg)).png().toBuffer();
    const croppedCard = await sharp(reference)
      .extract({ left: 0, top: 278, width: 960, height: 682 })
      .resize(576, 409, { fit: "fill" })
      .png()
      .toBuffer();
    const screenshot = await makePngDataUrl(
      720,
      1600,
      { create: { width: 720, height: 1600, channels: 3, background: "white" } },
      [{ input: croppedCard, left: 113, top: 241 }],
    );
    const referenceUrl = `data:image/png;base64,${reference.toString("base64")}`;

    const match = await getThreadsProfileReferenceImageBestMatchForTest(referenceUrl, screenshot);

    expect(match.matched).toBe(true);
    expect(match.mode).not.toBe("full");
  });

  it("does not classify captured fullscreen media viewer samples as gallery picker", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-gallery-fallback-opened-media-viewer",
      "threads-threads-image-gallery-fallback-opened-media-viewer-1779726857521-1779726862260.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsFullscreenMediaViewerLocally(dataUrl)).resolves.toBe(true);
    await expect(detectThreadsGalleryPickerLocally(dataUrl)).resolves.toBeNull();
  });

  it("recognizes the Threads in-app browser blank page from captured failure samples", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-open-gallery-unexpected",
      "threads-threads-image-open-gallery-unexpected-1779594847841-1779594851507.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsInAppBrowserLocally(dataUrl)).resolves.toBe(true);
  });

  it("recognizes the Threads share sheet overlay from captured failure samples", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-open-composer",
      "threads-threads-image-open-composer-1779596388472-1779596392106.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsShareSheetLocally(dataUrl)).resolves.toBe(true);
    await expect(detectThreadsHomeFeedLocally(dataUrl)).resolves.toBeNull();
  });

  it("recognizes the Threads activity side panel from captured failure samples", async () => {
    const samplePaths = [
      path.resolve(
        ".runtime",
        "automatic-script",
        "publish-samples",
        "threads",
        "threads-image-publish-top-level-failure",
        "threads-threads-image-publish-top-level-failure-1779596778286-1779596783495.jpg",
      ),
      path.resolve(
        ".runtime",
        "automatic-script",
        "publish-samples",
        "threads",
        "threads-image-publish-top-level-failure",
        "threads-threads-image-publish-top-level-failure-1779598180970-1779598186672.jpg",
      ),
    ].filter((samplePath) => fs.existsSync(samplePath));
    if (samplePaths.length === 0) return;

    for (const samplePath of samplePaths) {
      const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;
      await expect(detectThreadsSideDrawerLocally(dataUrl)).resolves.toBe(true);
      await expect(detectThreadsHomeFeedLocally(dataUrl)).resolves.toBeNull();
    }
  });

  it("does not treat a normal Threads feed with large media cards as a side panel", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-open-composer-retry",
      "threads-threads-image-open-composer-retry-1779597296230-1779597299768.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsSideDrawerLocally(dataUrl)).resolves.toBe(false);
    await expect(detectThreadsHomeFeedLocally(dataUrl)).resolves.toBe("LOCAL_HOME_FEED");
  });

  it("recognizes a normal Threads home feed captured from a top-level publish failure", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-publish-top-level-failure",
      "threads-threads-image-publish-top-level-failure-1779598502390-1779598510114.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsHomeFeedLocally(dataUrl)).resolves.toBe("LOCAL_HOME_FEED");
  });

  it("recognizes the latest Threads home feed top-level failure sample as home feed", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-publish-top-level-failure",
      "threads-threads-image-publish-top-level-failure-1779634396324-1779634402907.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsHomeFeedLocally(dataUrl)).resolves.toBe("LOCAL_HOME_FEED");
  });

  it("recognizes a text publish top-level failure sample as home feed, not composer", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-text-publish-top-level-failure",
      "threads-threads-text-publish-top-level-failure-1779700006865-1779700013139.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBeNull();
    await expect(detectThreadsHomeFeedLocally(dataUrl)).resolves.toBe("LOCAL_HOME_FEED");
  });

  it("recognizes a Threads search overlay even while the keyboard is open", async () => {
    const width = 720;
    const height = 1600;
    const svg = Buffer.from(`
      <svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">
        <rect width="720" height="1600" fill="#ffffff"/>
        <rect x="36" y="120" width="648" height="88" rx="20" fill="#eeeeee"/>
        <circle cx="76" cy="164" r="16" fill="none" stroke="#333333" stroke-width="6"/>
        <line x1="88" y1="176" x2="104" y2="192" stroke="#333333" stroke-width="6"/>
        <text x="128" y="178" font-size="38" font-family="Arial" fill="#111111">今天去遊樂園玩</text>
        <text x="642" y="178" font-size="42" font-family="Arial" fill="#111111">×</text>
        <rect x="0" y="980" width="720" height="620" fill="#dfe3e6"/>
        <g fill="#fafafa">
          <rect x="40" y="1040" width="56" height="72" rx="10"/>
          <rect x="112" y="1040" width="56" height="72" rx="10"/>
          <rect x="184" y="1040" width="56" height="72" rx="10"/>
          <rect x="256" y="1040" width="56" height="72" rx="10"/>
          <rect x="328" y="1040" width="56" height="72" rx="10"/>
          <rect x="400" y="1040" width="56" height="72" rx="10"/>
          <rect x="472" y="1040" width="56" height="72" rx="10"/>
          <rect x="544" y="1040" width="56" height="72" rx="10"/>
          <rect x="616" y="1040" width="56" height="72" rx="10"/>
          <rect x="180" y="1480" width="340" height="76" rx="14"/>
        </g>
      </svg>
    `);
    const dataUrl = await makePngDataUrl(
      width,
      height,
      { create: { width, height, channels: 4, background: "#ffffff" } },
      [{ input: svg, left: 0, top: 0 }],
    );

    await expect(detectThreadsSearchOverlayLocally(dataUrl)).resolves.toBe(true);
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBeNull();
  });

  it("does not treat a Threads composer with an open keyboard as a side panel", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-open-composer",
      "threads-threads-image-open-composer-1779599168035-1779599171509.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsSideDrawerLocally(dataUrl)).resolves.toBe(false);
  });

  it("recognizes a Threads post action sheet captured after a publish tap miss", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-publish-button-no-effect",
      "threads-threads-image-publish-button-no-effect-1779600024540-1779600028100.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsPostActionSheetLocally(dataUrl)).resolves.toBe(true);
  });

  it("does not treat a valid Threads image composer as a post action sheet", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-composer-controls-missing",
      "threads-threads-image-composer-controls-missing-1779608263418-1779608266925.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsPostActionSheetLocally(dataUrl)).resolves.toBe(false);
  });

  it("does not treat a selected Threads gallery page as a composer", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-publish-button-no-effect",
      "threads-threads-image-publish-button-no-effect-1779633805488-1779633809186.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsGalleryPickerLocally(dataUrl)).resolves.toBe("LOCAL_GALLERY_PICKER");
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBeNull();
  });

  it("recognizes a full-page Threads reply composer captured from publish flow", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-gallery-media-selection-missing",
      "threads-threads-image-gallery-media-selection-missing-1779623238392-1779623244153.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsReplyComposerLocally(dataUrl)).resolves.toBe("LOCAL_REPLY_COMPOSER");
  });

  it("does not treat a thread detail page with the bottom reply box as a profile page", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "debug-shots",
      "threads-warmup-acp-comment-reply-not-opened-1779691975786.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsProfilePageLocally(dataUrl)).resolves.toBe(false);
    await expect(detectThreadsReplyComposerLocally(dataUrl)).resolves.toBe("LOCAL_REPLY_COMPOSER");
  });

  it("does not treat a profile page showing the just-published post as a reply composer", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-publish-button-no-effect",
      "threads-threads-image-publish-button-no-effect-1779626328719-1779626332370.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsProfilePageLocally(dataUrl)).resolves.toBe(true);
    await expect(detectThreadsReplyComposerLocally(dataUrl)).resolves.toBeNull();
  });

  it("does not treat a profile page showing a just-published image post as a new composer", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-publish-button-no-effect",
      "threads-threads-image-publish-button-no-effect-1779649170673-1779649175341.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsProfilePageLocally(dataUrl)).resolves.toBe(true);
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBeNull();
  });

  it("recognizes Test1 profile after a just-published image post with guide cards", async () => {
    const samplePath = path.resolve(
      ".runtime",
      "automatic-script",
      "debug-shots",
      "threads-blocked-1779730718914.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsProfilePageLocally(dataUrl)).resolves.toBe(true);
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBeNull();
  });

  it("does not treat an image composer with a visible publish button as a profile page", async () => {
    const samplePath = path.resolve(
      "src",
      "test",
      "fixtures",
      "threads-publish-samples",
      "samples",
      "threads-image-composer-controls-missing-1779650054448-screenshot.jpg",
    );
    if (!fs.existsSync(samplePath)) return;
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(samplePath).toString("base64")}`;

    await expect(detectThreadsProfilePageLocally(dataUrl)).resolves.toBe(false);
    await expect(detectThreadsComposerLocally(dataUrl)).resolves.toBe("LOCAL_COMPOSER");
  });

  it("recognizes Android Camera after a gallery mis-tap", () => {
    const sampleXmlPath = path.resolve(
      ".runtime",
      "automatic-script",
      "publish-samples",
      "threads",
      "threads-image-composer-focus-outside-app",
      "threads-image-composer-focus-outside-app-1779621880623.xml",
    );
    const fallbackXml = `
      <hierarchy>
        <node package="com.android.camera2" resource-id="com.android.camera2:id/camera_app_root">
          <node resource-id="com.android.camera2:id/preview_content" />
        </node>
      </hierarchy>
    `;
    const sampleXml = fs.existsSync(sampleXmlPath) ? fs.readFileSync(sampleXmlPath, "utf8") : "";
    const xml = sampleXml.includes("com.android.camera2") ? sampleXml : fallbackXml;

    expect(detectAndroidCameraFromUiXml(xml)).toBe("LOCAL_ANDROID_CAMERA");
  });

  it("extracts account only from a Threads profile page", () => {
    expect(extractThreadsProfileUsernameFromUiXml(`
      <hierarchy>
        <node text="金君雅" class="android.widget.TextView" bounds="[32,112][188,154]" />
        <node text="@jinjunya.life" class="android.widget.TextView" bounds="[32,166][238,208]" />
        <node text="編輯個人檔案" class="android.widget.TextView" bounds="[28,410][334,468]" />
        <node text="串文" class="android.widget.TextView" bounds="[0,520][180,580]" />
        <node text="回覆" class="android.widget.TextView" bounds="[180,520][360,580]" />
      </hierarchy>
    `)).toBe("jinjunya.life");
  });

  it("does not treat Google or Chrome text as a Threads account", () => {
    expect(extractThreadsProfileUsernameFromUiXml(`
      <hierarchy>
        <node text="Google" class="android.widget.TextView" bounds="[80,120][260,180]" />
        <node text="Chrome" class="android.widget.TextView" bounds="[80,210][260,270]" />
        <node text="Search" class="android.widget.EditText" bounds="[32,82][688,156]" />
      </hierarchy>
    `)).toBeNull();
    expect(extractThreadsProfileUsernameFromUiXml(`
      <hierarchy>
        <node text="@Google" class="android.widget.TextView" bounds="[32,166][238,208]" />
        <node text="編輯個人檔案" class="android.widget.TextView" bounds="[28,410][334,468]" />
        <node text="粉絲" class="android.widget.TextView" bounds="[160,316][240,356]" />
      </hierarchy>
    `)).toBeNull();
  });

  it("locates Threads composer input from UIAutomator XML", () => {
    const target = findThreadsComposerInputTarget(`
      <hierarchy>
        <node index="0" text="bb697856" class="android.widget.TextView" bounds="[113,174][420,216]" />
        <node index="1" text="有什麼新鮮事？" class="android.widget.EditText" bounds="[113,220][607,336]" />
        <node index="2" text="發布" class="android.widget.Button" bounds="[580,1498][701,1578]" />
      </hierarchy>
    `);

    expect(target).toEqual({ x: 360, y: 278 });
  });

  it("does not use Threads search boxes as composer input", () => {
    const target = findThreadsComposerInputTarget(`
      <hierarchy>
        <node text="灑在中山站的巷弄" content-desc="" class="android.widget.EditText" bounds="[36,126][684,210]" />
        <node text="" content-desc="搜尋" class="android.widget.EditText" bounds="[60,126][640,210]" />
      </hierarchy>
    `);

    expect(target).toBeNull();
  });

  it("locates Threads search tab and search input from UIAutomator XML", () => {
    expect(findThreadsBottomSearchTabTarget(`
      <hierarchy>
        <node text="Home" content-desc="Home" class="android.view.View" clickable="true" bounds="[42,1168][118,1248]" />
        <node text="" content-desc="Search" class="android.view.View" clickable="true" bounds="[165,1168][245,1248]" />
        <node text="Create" content-desc="Create" class="android.view.View" clickable="true" bounds="[320,1168][400,1248]" />
      </hierarchy>
    `)).toEqual({ x: 205, y: 1208 });

    expect(findThreadsSearchInputTarget(`
      <hierarchy>
        <node text="" class="android.widget.EditText" content-desc="Search" bounds="[84,72][648,150]" />
        <node text="Cancel" class="android.widget.TextView" bounds="[654,72][710,150]" />
      </hierarchy>
    `)).toEqual({ x: 366, y: 111 });

    expect(findThreadsTopSearchButtonTarget(`
      <hierarchy>
        <node text="" content-desc="Search" class="android.widget.ImageView" clickable="true" bounds="[620,74][692,146]" />
      </hierarchy>
    `)).toEqual({ x: 656, y: 110 });
  });

  it("does not treat Threads activity page controls as search navigation", () => {
    const activityXml = `
      <hierarchy>
        <node text="動態消息" content-desc="" class="android.widget.TextView" bounds="[46,114][266,166]" />
        <node text="" content-desc="Search" class="android.widget.ImageView" clickable="true" bounds="[154,238][236,320]" />
        <node text="為你推薦" class="android.widget.TextView" bounds="[88,402][232,450]" />
        <node text="追蹤中" class="android.widget.TextView" bounds="[88,542][202,590]" />
        <node text="附帶原始貼文的回覆內容" class="android.widget.TextView" bounds="[88,680][410,730]" />
      </hierarchy>
    `;

    expect(looksLikeThreadsActivityUiXml(activityXml)).toBe(true);
    expect(findThreadsBottomSearchTabTarget(activityXml)).toBeNull();
    expect(findThreadsTopSearchButtonTarget(activityXml)).toBeNull();
    expect(findThreadsSearchInputTarget(activityXml)).toBeNull();
  });

  it("recognizes a Threads search results page after Chinese keyword submission", () => {
    const resultsXml = `
      <hierarchy>
        <node text="買房" class="android.widget.TextView" bounds="[128,74][244,134]" />
        <node text="熱門貼文" class="android.widget.TextView" bounds="[44,210][180,260]" />
        <node text="最新貼文" class="android.widget.TextView" bounds="[88,420][220,470]" />
        <node text="相關個人檔案" class="android.widget.TextView" bounds="[452,210][650,260]" />
        <node text="1小時" class="android.widget.TextView" bounds="[338,330][424,382]" />
        <node text="5月我提醒過的國巨，買到的人都買車買房了。" class="android.widget.TextView" bounds="[116,456][690,620]" />
      </hierarchy>
    `;

    expect(findThreadsSearchInputTarget(resultsXml)).toBeNull();
    expect(looksLikeThreadsSearchResultsUiXml(resultsXml, "買房")).toBe(true);
    expect(looksLikeThreadsSearchResultsUiXml(resultsXml, "咖啡")).toBe(false);
  });

  it("detects Threads detail pages and post option sheets before warmup search", () => {
    expect(looksLikeThreadsThreadDetailUiXml(`
      <hierarchy>
        <node text="串文" class="android.widget.TextView" bounds="[137,96][218,146]" />
        <node text="olina810 › 世界盃 46 分鐘" class="android.widget.TextView" bounds="[154,218][516,285]" />
        <node text="追蹤" class="android.widget.Button" bounds="[576,218][685,290]" />
      </hierarchy>
    `)).toBe(true);

    expect(looksLikeThreadsPostOptionsSheetUiXml(`
      <hierarchy>
        <node text="複製連結" class="android.widget.TextView" bounds="[72,648][260,700]" />
        <node text="使用標籤建立" class="android.widget.TextView" bounds="[72,788][360,840]" />
        <node text="沒興趣" class="android.widget.TextView" bounds="[72,1030][210,1082]" />
      </hierarchy>
    `)).toBe(true);
  });

  it("locates Threads publish button from UIAutomator XML", () => {
    const target = findThreadsComposerPublishButtonTarget(`
      <hierarchy>
        <node index="0" text="新串文" class="android.widget.TextView" bounds="[113,74][280,123]" />
        <node index="1" text="有什麼新鮮事？" class="android.widget.EditText" bounds="[113,220][607,336]" />
        <node index="2" text="替代文字" class="android.widget.Button" bounds="[144,789][338,837]" clickable="true" />
        <node index="3" text="發布" class="android.widget.Button" bounds="[580,1026][701,1108]" clickable="true" />
      </hierarchy>
    `);

    expect(target).toEqual({ x: 641, y: 1067 });
  });

  it("locates Threads reply composer controls from UIAutomator XML", () => {
    const uiXml = `
      <hierarchy>
        <node index="2" text="" resource-id="new_thread_screen_composer" class="android.widget.EditText" bounds="[120,702][688,798]" focused="true" />
        <node index="0" text="发布" resource-id="ig_text" class="android.widget.TextView" bounds="[604,1098][664,1142]" />
      </hierarchy>
    `;

    expect(findThreadsReplyComposerInputTarget(uiXml)).toEqual({ x: 404, y: 750 });
    expect(findThreadsReplySendButtonTarget(uiXml)).toEqual({ x: 634, y: 1120 });
  });

  it("locates the ACP visual reply send button above the keyboard", async () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1280">
      <rect width="720" height="1280" fill="white"/>
      <rect x="24" y="650" width="570" height="120" rx="60" fill="#f1f2f3"/>
      <circle cx="656" cy="700" r="56" fill="#000"/>
      <path d="M656 730 L656 670 M656 670 L628 698 M656 670 L684 698" stroke="#fff" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>
      <rect x="0" y="790" width="720" height="490" fill="#f6f7f9"/>
    </svg>`;
    const dataUrl = `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;

    const point = await findAcpReplySendButtonPointFromScreenshot(dataUrl);

    expect(point?.x).toBeGreaterThan(635);
    expect(point?.x).toBeLessThan(675);
    expect(point?.y).toBeGreaterThan(680);
    expect(point?.y).toBeLessThan(720);
  });

  it("detects Threads posted toast locally before visual AI verification", async () => {
    const sharp = (await import("sharp")).default;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1600">
      <rect width="720" height="1600" fill="#ffffff"/>
      <rect x="70" y="1290" width="580" height="82" rx="41" fill="#111111"/>
      <text x="360" y="1344" font-size="34" font-family="Arial" text-anchor="middle" fill="#ffffff">已發佈</text>
    </svg>`;
    const png = await sharp(Buffer.from(svg)).png().toBuffer();
    const dataUrl = `data:image/png;base64,${png.toString("base64")}`;

    await expect(detectThreadsPostSuccessToastLocally(dataUrl)).resolves.toBe(true);
  });

  it("does not mistake an open keyboard for a Threads posted toast", async () => {
    const sharp = (await import("sharp")).default;
    const keys = Array.from({ length: 30 }, (_, i) => {
      const col = i % 10;
      const row = Math.floor(i / 10);
      return `<rect x="${20 + col * 68}" y="${1290 + row * 88}" width="52" height="58" rx="8" fill="#111111"/>
        <text x="${46 + col * 68}" y="${1328 + row * 88}" font-size="24" font-family="Arial" text-anchor="middle" fill="#ffffff">A</text>`;
    }).join("");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1600">
      <rect width="720" height="1600" fill="#ffffff"/>
      <rect x="0" y="1260" width="720" height="340" fill="#f7f7f7"/>
      ${keys}
    </svg>`;
    const png = await sharp(Buffer.from(svg)).png().toBuffer();
    const dataUrl = `data:image/png;base64,${png.toString("base64")}`;

    await expect(detectThreadsPostSuccessToastLocally(dataUrl)).resolves.toBe(false);
  });

  it("prefers visible feed action buttons over clipped top-screen actions", () => {
    const target = findThreadsHomeFeedActionTargetsFromUiXml(`
      <hierarchy>
        <node text="赞" class="android.widget.ImageView" clickable="true" bounds="[112,260][156,304]" />
        <node text="回复" class="android.widget.ImageView" clickable="true" bounds="[226,260][270,304]" />
        <node text="赞" class="android.widget.ImageView" clickable="true" bounds="[112,690][156,734]" />
        <node text="回复" class="android.widget.ImageView" clickable="true" bounds="[226,690][270,734]" />
      </hierarchy>
    `);

    expect(target?.like).toEqual({ x: 134, y: 712 });
    expect(target?.comment).toEqual({ x: 248, y: 712 });
  });

  it("locates the inline permalink reply composer near the bottom", () => {
    const uiXml = `
      <hierarchy>
        <node index="1" text="" resource-id="permalink_inline_composer" class="android.widget.EditText" bounds="[112,1064][464,1160]" focused="false" />
        <node index="0" text="回复 monicachaplin_1120" resource-id="ig_text" class="android.widget.TextView" bounds="[112,1090][457,1134]" />
      </hierarchy>
    `;

    expect(findThreadsReplyComposerInputTarget(uiXml)).toEqual({ x: 288, y: 1112 });
  });

  it("locates the OP-TEST1 1600px bottom reply composer", () => {
    const uiXml = `
      <hierarchy>
        <node index="1" text="" resource-id="permalink_inline_composer" class="android.widget.EditText" bounds="[104,1484][480,1570]" focused="false" />
        <node index="0" text="回覆 keven.0.0.0" resource-id="ig_text" class="android.widget.TextView" bounds="[108,1500][456,1558]" />
      </hierarchy>
    `;

    expect(findThreadsReplyComposerInputTarget(uiXml)).toEqual({ x: 292, y: 1527 });
  });

  it("does not count an empty reply composer as a posted comment", () => {
    const emptyComposer = `
      <hierarchy>
        <node index="2" text="" resource-id="new_thread_screen_composer" class="android.widget.EditText" bounds="[120,702][688,798]" focused="true" />
        <node index="1" text="回复rowanfonzyr…" resource-id="ig_text" class="android.widget.TextView" bounds="[120,728][364,772]" />
        <node index="0" text="发布" resource-id="ig_text" class="android.widget.TextView" bounds="[604,1098][664,1142]" />
      </hierarchy>
    `;

    expect(hasThreadsReplyComposerText(emptyComposer, "日電貿這波真的強")).toBe(false);
    expect(detectThreadsWarmupCommentPostedCueFromUiXml(emptyComposer)).toBe(false);
  });

  it("treats private-profile reply prompts as expected warmup skips", () => {
    expect(isThreadsWarmupCommentExpectedSkipMessage("当前帖子为私密主页，无法回复非粉丝，已跳过")).toBe(true);
    expect(isThreadsWarmupCommentExpectedSkipMessage("Private profiles can only reply to their followers")).toBe(true);
    expect(isThreadsWarmupCommentExpectedSkipMessage("To respond to the author, you can message them on Threads instead.")).toBe(true);
    expect(isThreadsWarmupCommentExpectedSkipMessage("Update profile privacy")).toBe(true);
    expect(isThreadsWarmupCommentExpectedSkipMessage("留言发布成功")).toBe(false);
  });

  it("detects the English private-profile reply bottom sheet visually", async () => {
    const fixturePath = path.resolve("src", "test", "fixtures", "threads-publish-samples", "threads-private-reply-prompt-en.jpg");
    const dataUrl = `data:image/jpeg;base64,${fs.readFileSync(fixturePath).toString("base64")}`;
    expect(await detectThreadsPrivateReplyPromptLocally(dataUrl)).toBe(true);
  });

  it("recognizes typed and posted warmup comments", () => {
    const typedComposer = `
      <hierarchy>
        <node index="2" text="日電貿這波真的強" resource-id="new_thread_screen_composer" class="android.widget.EditText" bounds="[120,702][688,798]" focused="true" />
        <node index="0" text="发布" resource-id="ig_text" class="android.widget.TextView" bounds="[604,1098][664,1142]" />
      </hierarchy>
    `;
    const postedCue = `
      <hierarchy>
        <node index="0" text="已发布" resource-id="ig_text" class="android.widget.TextView" bounds="[132,948][222,992]" />
      </hierarchy>
    `;

    expect(hasThreadsReplyComposerText(typedComposer, "日電貿這波真的強")).toBe(true);
    expect(detectThreadsWarmupCommentPostedCueFromUiXml(postedCue)).toBe(true);
  });

  it("distinguishes a reply composer from a new thread composer using reply context", () => {
    const replyComposer = `
      <hierarchy>
        <node index="2" text="Good point worth noting" resource-id="new_thread_screen_composer" class="android.widget.EditText" bounds="[120,702][688,798]" focused="true" />
        <node index="1" text="回覆 keven.0.0.0" resource-id="ig_text" class="android.widget.TextView" bounds="[120,728][364,772]" />
        <node index="0" text="发布" resource-id="ig_text" class="android.widget.TextView" bounds="[604,1098][664,1142]" />
      </hierarchy>
    `;
    const newThreadComposer = `
      <hierarchy>
        <node index="2" text="Good point worth noting" resource-id="new_thread_screen_composer" class="android.widget.EditText" bounds="[120,220][688,336]" focused="true" />
        <node index="0" text="新串文" resource-id="ig_text" class="android.widget.TextView" bounds="[120,74][280,123]" />
        <node index="0" text="发布" resource-id="ig_text" class="android.widget.TextView" bounds="[604,1098][664,1142]" />
      </hierarchy>
    `;

    expect(looksLikeThreadsReplyComposerUiXml(replyComposer)).toBe(true);
    expect(looksLikeThreadsReplyComposerUiXml(newThreadComposer)).toBe(false);
  });

  it("extracts warmup post preview from UI XML before using vision", () => {
    const preview = extractWarmupPostPreviewFromUiXml(`
      <hierarchy>
        <node text="热门" class="android.widget.TextView" bounds="[0,0][80,40]" />
        <node text="阿銘分享華通今天高檔換手量能放大 這個位置要看後續承接" class="android.widget.TextView" bounds="[96,290][650,360]" />
        <node text="赞" class="android.widget.ImageView" bounds="[112,690][156,734]" />
        <node text="12" class="android.widget.TextView" bounds="[160,690][190,734]" />
      </hierarchy>
    `);

    expect(preview).toContain("阿銘分享華通今天高檔換手量能放大");
    expect(preview).not.toContain("热门");
    expect(preview).not.toContain("赞");
  });

  it("removes punctuation from warmup comments", () => {
    expect(sanitizeWarmupComment("華通這波，真的強！想問你怎麼看？🔥 #台股 @bot"))
      .toBe("華通這波真的強想問你怎麼看");
    expect(sanitizeWarmupComment("Space這段蠻有共鳴"))
      .toBe("這段蠻有共鳴");
    expect(sanitizeWarmupComment("TSMC looks good, right?"))
      .toBe("TSMC looks good right");
    expect(sanitizeWarmupComment("這個角度很自然這個角度很自然這個角度很自然"))
      .toBe("這個角度很自然");
    expect(sanitizeWarmupComment("這種小細節才真實這種小細節才真實"))
      .toBe("這種小細節才真實");
  });

  it("detects repeated warmup comment drafts before sending", () => {
    expect(hasRepeatedWarmupCommentText("這種小細節才真實這種小細節才真實", "這種小細節才真實"))
      .toBe(true);
    expect(hasRepeatedWarmupCommentText("這種小細節才真實", "這種小細節才真實"))
      .toBe(false);
  });

  it("detects near duplicate warmup comments in one run", () => {
    expect(isNearDuplicateWarmupComment("戶型這裡可以再拆細", ["戶型這邊可以再拆細"]))
      .toBe(true);
    expect(isNearDuplicateWarmupComment("採光和動線要一起看", ["戶型這邊可以再拆細"]))
      .toBe(false);
  });

  it("requires warmup comments to be complete enough after punctuation removal", () => {
    expect(isUsableWarmupComment("真的強")).toBe(false);
    expect(isUsableWarmupComment("這點我也有感")).toBe(false);
    expect(isUsableWarmupComment("量能續不續才是重點")).toBe(true);
    expect(isUsableWarmupComment("華通這波真的強想問你怎麼看")).toBe(true);
    expect(finalizeWarmupComment("真的強", "華通今天高檔換手量能放大"))
      .toMatch(/華通|量能|回撤|部位/);
    expect(finalizeWarmupComment("不错", "今天分享下班後整理心情的生活日常").length)
      .toBeGreaterThanOrEqual(6);
    expect(finalizeWarmupComment("不错", "", [], { language: "台灣地區繁體中文" }))
      .toMatch(/後續|脈絡|細節/);
    expect(finalizeWarmupComment("這個角度很自然這個角度很自然這個角度很自然", "朋友最近股票賺好多"))
      .not.toContain("這個角度很自然這個角度很自然");
    expect(finalizeWarmupComment("這個角度很自然", "朋友最近股票賺好多"))
      .toMatch(/風險|部位|熱度/);
  });

  it("allows very short natural reactions for very short posts", () => {
    expect(finalizeWarmupComment("哈哈", "嚇死人了要成發了"))
      .toBe("哈哈");
    expect(finalizeWarmupComment("真的欸", "好累"))
      .toBe("真的欸");
    expect(finalizeWarmupComment("不错", "嚇死人了要成發了", [], { language: "台灣地區繁體中文" }).length)
      .toBeLessThanOrEqual(8);
    expect(finalizeWarmupComment("哈哈", "華通今天高檔換手量能放大"))
      .not.toBe("哈哈");
  });

  it("uses simple hopeful replies for short market mood posts", () => {
    expect(finalizeWarmupComment("明天那個心臟明天那個心臟", "可能明天看到股市心情就會變好了"))
      .toMatch(/期待|希望明天|明天開盤/);
    expect(finalizeWarmupComment("期待", "可能明天看到股市心情就會變好了"))
      .toBe("期待");
    expect(isUsableWarmupComment("明天那個心臟")).toBe(false);
  });

  it("risk-manages warmup sessions instead of forcing mechanical engagement", () => {
    const plan = planRiskManagedWarmupConfig("TEST_RISK_PAD", {
      browseCount: 50,
      likeChance: 100,
      maxLikes: 5,
      commentChance: 100,
      maxComments: 3,
    }, new Date("2026-05-22T08:00:00.000Z"));

    expect(plan.allowed).toBe(true);
    expect(plan.cfg.browseCount).toBeLessThanOrEqual(12);
    expect(plan.cfg.likeChance).toBeLessThanOrEqual(35);
    expect(plan.cfg.commentChance).toBeLessThanOrEqual(15);
    expect(plan.cfg.maxLikes).toBeLessThanOrEqual(1);
    expect(plan.cfg.maxComments).toBeLessThanOrEqual(1);
    expect(plan.cfg.strictCompletion).toBe(false);
    expect(plan.cfg.requireReadablePostForComment).toBe(true);
  });

  it("requires at least one requested warmup interaction without treating risk caps as required targets", () => {
    expect(() => assertWarmupMinimumCompletion("Threads", { liked: 0, commented: 0 }, { minRequiredInteractions: 1 })).toThrow(
      /要求点赞或留言至少成功 1 次/,
    );

    expect(() => assertWarmupMinimumCompletion("Threads", { liked: 1, commented: 0 }, { minRequiredInteractions: 1 })).not.toThrow();
    expect(() => assertWarmupMinimumCompletion("Threads", { liked: 0, commented: 1 }, { minRequiredInteractions: 1 })).not.toThrow();
    expect(() => assertWarmupMinimumCompletion("Threads", { liked: 1, commented: 0 }, { minRequiredLikes: 1 })).not.toThrow();
    expect(() => assertWarmupMinimumCompletion("Threads", { liked: 0, commented: 1 }, { minRequiredComments: 1 })).not.toThrow();
  });

  it("builds persona-specific warmup search keywords instead of slicing generic persona text", () => {
    const keywords = buildWarmupSearchKeywordCandidates({
      name: "房产中介",
      description: "35岁房产中介，关注买房、租房、不动产和客户看房经验",
      style: "自然口语",
      language: "繁體中文",
      interests: ["不動產", "買房"],
    });

    expect(keywords.slice(0, 4)).toEqual(expect.arrayContaining(["房产中介", "不動產", "買房"]));
    expect(keywords).toContain("房产中介");
    expect(keywords.some((item) => /^[\x20-\x7E]+$/.test(item))).toBe(false);
    expect(keywords).not.toContain("35岁房产中介");
    expect(keywords).not.toContain("自然口语");
  });

  it("selects an interaction when timed warmup only requires total interactions", () => {
    expect(chooseWarmupTimedInteraction({
      liked: 0,
      commented: 0,
      minRequiredLikes: 0,
      minRequiredComments: 0,
      minRequiredInteractions: 1,
      likeChance: 100,
      commentChance: 100,
      maxLikes: 16,
      maxComments: 8,
      likeFailures: 0,
      commentFailures: 0,
      maxCommentFailures: 4,
      pickIndex: () => 0,
    })).toBe("comment");

    expect(chooseWarmupTimedInteraction({
      liked: 1,
      commented: 0,
      minRequiredLikes: 0,
      minRequiredComments: 0,
      minRequiredInteractions: 1,
      likeChance: 100,
      commentChance: 100,
      maxLikes: 16,
      maxComments: 8,
      likeFailures: 0,
      commentFailures: 0,
      maxCommentFailures: 4,
      pickIndex: () => 0,
    })).toBeNull();
  });

  it("spreads warmup comment turns across browsing instead of concentrating at the end", () => {
    expect(buildWarmupCommentTurnSchedule(3, 1, 1)).toEqual([1]);
    expect(buildWarmupCommentTurnSchedule(5, 2, 1)).toEqual([2, 3]);
    expect(buildWarmupCommentTurnSchedule(4, 4, 0)).toEqual([0, 1, 2, 3]);
  });

  it("keeps timed warmup sessions long-running and interval-based", () => {
    const plan = planRiskManagedWarmupConfig("TEST_TIMED_RISK_PAD", {
      browseCount: 80,
      minSessionMinutes: 7,
      maxSessionMinutes: 10,
      interactionEveryMinPosts: 2,
      interactionEveryMaxPosts: 3,
      likeChance: 100,
      maxLikes: 16,
      commentChance: 100,
      maxComments: 8,
      stopOnRiskLimit: true,
    }, new Date("2026-05-22T08:00:00.000Z"));

    expect(plan.allowed).toBe(true);
    expect(plan.cfg.browseCount).toBeGreaterThanOrEqual(60);
    expect(plan.cfg.minSessionMinutes).toBe(7);
    expect(plan.cfg.maxSessionMinutes).toBe(10);
    expect(plan.cfg.interactionEveryMinPosts).toBe(2);
    expect(plan.cfg.interactionEveryMaxPosts).toBe(3);
    expect(plan.cfg.likeChance).toBe(100);
    expect(plan.cfg.commentChance).toBe(100);
    expect(plan.cfg.maxLikes).toBeGreaterThan(1);
    expect(plan.cfg.maxComments).toBeGreaterThan(1);
    expect(plan.cfg.stopOnRiskLimit).toBe(true);
  });

  it("warns about recent high-risk pages without blocking warmup start", () => {
    const file = path.join(process.cwd(), ".runtime", "automatic-script-test", `warmup-risk-state-${Date.now()}-${Math.random().toString(36).slice(2)}.json`);
    const originalEnv = process.env.WARMUP_RISK_STATE_FILE;
    const padCode = "TEST_SOFT_RISK_PAD";
    try {
      process.env.WARMUP_RISK_STATE_FILE = file;
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, JSON.stringify({
        [`${padCode}:2026-05-22`]: {
          date: "2026-05-22",
          sessions: 1,
          browsed: 0,
          liked: 0,
          commented: 0,
          failures: 1,
          blockedUntil: "2026-05-22T20:00:00.000Z",
          lastBlockReason: "LOCAL_PHONE_VERIFICATION_PAGE",
        },
      }), "utf8");

      const plan = planRiskManagedWarmupConfig(padCode, {
        browseCount: 12,
        likeChance: 100,
        maxLikes: 1,
        commentChance: 100,
        maxComments: 1,
      }, new Date("2026-05-22T08:00:00.000Z"));

      expect(plan.allowed).toBe(true);
      expect(plan.cfg.browseCount).toBeGreaterThan(0);
      expect(plan.notes.join(" ")).toContain("不阻断本次操作");
    } finally {
      if (originalEnv === undefined) {
        delete process.env.WARMUP_RISK_STATE_FILE;
      } else {
        process.env.WARMUP_RISK_STATE_FILE = originalEnv;
      }
      fs.rmSync(file, { force: true });
    }
  });

  it("treats exhausted daily warmup budget as a warning instead of a blocker", () => {
    const file = path.join(process.cwd(), ".runtime", "automatic-script-test", `warmup-risk-budget-${Date.now()}-${Math.random().toString(36).slice(2)}.json`);
    const originalEnv = process.env.WARMUP_RISK_STATE_FILE;
    const padCode = "TEST_EXHAUSTED_BUDGET_PAD";
    try {
      process.env.WARMUP_RISK_STATE_FILE = file;
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, JSON.stringify({
        [`${padCode}:2026-05-22`]: {
          date: "2026-05-22",
          sessions: 99,
          browsed: 999,
          liked: 99,
          commented: 99,
          failures: 0,
          lastSessionAt: "2026-05-22T07:00:00.000Z",
        },
      }), "utf8");

      const plan = planRiskManagedWarmupConfig(padCode, {
        browseCount: 8,
        likeChance: 100,
        maxLikes: 1,
        commentChance: 100,
        maxComments: 1,
      }, new Date("2026-05-22T08:00:00.000Z"));

      expect(plan.allowed).toBe(true);
      expect(plan.cfg.browseCount).toBeGreaterThan(0);
      expect(plan.notes.join(" ")).toContain("不建议继续频繁操作");
    } finally {
      if (originalEnv === undefined) {
        delete process.env.WARMUP_RISK_STATE_FILE;
      } else {
        process.env.WARMUP_RISK_STATE_FILE = originalEnv;
      }
      fs.rmSync(file, { force: true });
    }
  });

  it("locates Threads profile video tab from UIAutomator XML", () => {
    const target = findThreadsProfileVideoTabTarget(`
      <hierarchy>
        <node index="0" text="串文" class="android.widget.TextView" bounds="[0,178][180,252]" clickable="true" />
        <node index="1" text="回覆" class="android.widget.TextView" bounds="[180,178][360,252]" clickable="true" />
        <node index="2" text="影音內容" class="android.widget.TextView" bounds="[360,178][540,252]" clickable="true" />
        <node index="3" text="轉發" class="android.widget.TextView" bounds="[540,178][720,252]" clickable="true" />
      </hierarchy>
    `);

    expect(target).toEqual({ x: 450, y: 215 });
  });

  it("builds a Threads text share intent with encoded caption", () => {
    const command = buildThreadsShareIntentCommand({
      caption: "台灣小吃測試 THREADS-MATRIX",
      mimeType: "text/plain",
    });

    expect(command).toContain("android.intent.action.SEND");
    expect(command).toContain("-n com.instagram.barcelona/.handleractivity.BarcelonaShareHandlerActivity");
    expect(command).not.toContain("--activity-new-task");
    expect(command).not.toContain("--activity-clear-top");
    expect(command).toContain("android.intent.extra.TEXT");
    expect(command).toContain("base64 -d");
    expect(command).not.toContain("台灣小吃測試");
  });

  it("builds a Threads media share intent with content uri permission", () => {
    const command = buildThreadsShareIntentCommand({
      contentUri: "content://media/external/images/media/123",
      mimeType: "image/jpeg",
    });

    expect(command).toContain("--grant-read-uri-permission");
    expect(command).toContain("--eu android.intent.extra.STREAM");
    expect(command).toContain("content://media/external/images/media/123");
    expect(command).not.toContain("android.intent.extra.TEXT");
  });

  it("routes Threads posts from every cloud phone class through system share", () => {
    const mediaUrls = ["data:image/png;base64,abc", "data:video/mp4;base64,abc"];
    const padCodes = [
      "ACP250322677KIRJ",
      "ATP64K6RON7LCGMR",
      "APP6476L6A25SQ4W",
      "UNKNOWN-PAD",
    ];

    for (const padCode of padCodes) {
      for (const mediaUrl of mediaUrls) {
        expect(shouldUseThreadsShareIntentPath(padCode, mediaUrl)).toBe(true);
      }
      expect(shouldUseThreadsShareIntentPath(padCode)).toBe(true);
    }
  });
});
