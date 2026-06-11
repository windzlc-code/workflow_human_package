import {
  type ThreadsPromotedSample,
  type ThreadsPromotedSampleManifest,
  type ThreadsPublishSampleRegistryEntry,
  type ThreadsSampleAssertion,
  isThreadsPromotedSampleScreenSize,
  readThreadsPromotedSampleManifest,
  readThreadsPublishSampleIndex,
  refreshThreadsPublishSampleIndexFromRuntime,
  THREADS_PROMOTED_SAMPLE_SCREEN,
  THREADS_PROMOTED_SAMPLE_MANIFEST,
  THREADS_PROMOTED_SAMPLE_ROOT,
  writeThreadsPublishSampleIndex,
} from "../../src/lib/threads-sample-registry";
import {
  detectAndroidCameraFromUiXml,
  detectThreadsBlockedScreenFromUiXml,
  detectThreadsComposerLocally,
  detectThreadsFullscreenMediaViewerLocally,
  detectThreadsGalleryPickerLocally,
  detectThreadsHomeFeedLocally,
  detectThreadsPostActionSheetLocally,
  detectThreadsProfilePageLocally,
  detectThreadsReplyComposerLocally,
  detectThreadsSearchOverlayLocally,
  detectThreadsShareSheetLocally,
  detectThreadsSideDrawerLocally,
} from "../../src/lib/vmos-publisher";
import fs from "node:fs";
import path from "node:path";
import sharp from "sharp";

function parseArgs(argv: string[]) {
  const options: {
    limit?: number;
    scenario?: string;
    dryRun?: boolean;
    includePromoted?: boolean;
    refreshOnly?: boolean;
  } = {};

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") options.dryRun = true;
    else if (arg === "--include-promoted") options.includePromoted = true;
    else if (arg === "--refresh-only") options.refreshOnly = true;
    else if (arg === "--limit") options.limit = Number(argv[index + 1] || "0") || undefined, index += 1;
    else if (arg.startsWith("--limit=")) options.limit = Number(arg.slice("--limit=".length)) || undefined;
    else if (arg === "--scenario") options.scenario = argv[index + 1], index += 1;
    else if (arg.startsWith("--scenario=")) options.scenario = arg.slice("--scenario=".length);
  }

  return options;
}

const options = parseArgs(process.argv.slice(2));
const index = refreshThreadsPublishSampleIndexFromRuntime();

if (options.refreshOnly) {
  const latest = readThreadsPublishSampleIndex();
  console.log(JSON.stringify({
    ok: true,
    mode: "refresh-only",
    indexed: latest.samples.length,
  }, null, 2));
  process.exit(0);
}

function readScreenshotDataUrl(sample: ThreadsPublishSampleRegistryEntry): string {
  if (!sample.screenshotPath || !fs.existsSync(sample.screenshotPath)) return "";
  const ext = path.extname(sample.screenshotPath).toLowerCase();
  const mime = ext === ".png" ? "png" : ext === ".webp" ? "webp" : "jpeg";
  return `data:image/${mime};base64,${fs.readFileSync(sample.screenshotPath).toString("base64")}`;
}

function readXml(sample: ThreadsPublishSampleRegistryEntry): string {
  if (!sample.xmlPath || !fs.existsSync(sample.xmlPath)) return "";
  return fs.readFileSync(sample.xmlPath, "utf8");
}

async function readImageSize(filePath: string | null | undefined): Promise<{ width: number; height: number } | null> {
  if (!filePath || !fs.existsSync(filePath)) return null;
  try {
    const metadata = await sharp(filePath).metadata();
    return metadata.width && metadata.height ? { width: metadata.width, height: metadata.height } : null;
  } catch {
    return null;
  }
}

async function promotedSampleHasValidScreenshot(sample: ThreadsPromotedSample): Promise<boolean> {
  const needsScreenshot = (sample.assertions || []).some((assertion) => (
    assertion.detector !== "android_camera" && assertion.detector !== "blocked_screen"
  ));
  if (!needsScreenshot) return true;
  if (isThreadsPromotedSampleScreenSize(sample.screenshotSize)) return true;
  const screenshotPath = sample.screenshot
    ? path.join(THREADS_PROMOTED_SAMPLE_ROOT, sample.screenshot)
    : "";
  const size = await readImageSize(screenshotPath);
  return isThreadsPromotedSampleScreenSize(size);
}

function uniqueAssertions(assertions: ThreadsSampleAssertion[]): ThreadsSampleAssertion[] {
  const seen = new Set<string>();
  return assertions.filter((assertion) => {
    const key = `${assertion.detector}:${String(assertion.expected)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function pageFromAssertion(assertion: ThreadsSampleAssertion | undefined): { page?: string; reason?: string } {
  if (!assertion) return {};
  const reason = typeof assertion.expected === "string" ? assertion.expected : undefined;
  switch (assertion.detector) {
    case "home_feed":
      return { page: "home_feed", reason };
    case "gallery_picker":
      return { page: "gallery_picker", reason };
    case "composer":
      return { page: "compose_editor", reason };
    case "reply_composer":
      return { page: "reply_composer", reason };
    case "profile_page":
      return { page: "profile_page", reason };
    case "share_sheet":
    case "post_action_sheet":
    case "fullscreen_media_viewer":
    case "side_drawer":
    case "android_camera":
    case "blocked_screen":
      return { page: assertion.detector, reason };
    default:
      return {};
  }
}

function sanitizeSampleText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  return trimmed
    .replace(/[A-Z]:[\\/][^\s｜|]+\.runtime[\\/][^\s｜|]+/gi, ".runtime/automatic-script")
    .replace(/[A-Z]:[\\/][^\s｜|]+debug-shots[\\/]/gi, "debug-shots/");
}

async function inferAssertionsWithCurrentDetectors(sample: ThreadsPublishSampleRegistryEntry): Promise<ThreadsSampleAssertion[]> {
  const dataUrl = readScreenshotDataUrl(sample);
  const xml = readXml(sample);
  const assertions: ThreadsSampleAssertion[] = [];

  if (xml) {
    const camera = detectAndroidCameraFromUiXml(xml);
    if (camera) return [{ detector: "android_camera", expected: "LOCAL_ANDROID_CAMERA" }];
  }
  if (!dataUrl) return [];

  const gallery = await detectThreadsGalleryPickerLocally(dataUrl).catch(() => null);
  if (gallery) {
    assertions.push({ detector: "gallery_picker", expected: gallery });
    return assertions;
  }

  if (await detectThreadsProfilePageLocally(dataUrl).catch(() => false)) {
    assertions.push({ detector: "profile_page", expected: true });
    return assertions;
  }

  const composer = await detectThreadsComposerLocally(dataUrl).catch(() => null);
  if (composer) {
    assertions.push({ detector: "composer", expected: composer });
    return assertions;
  }

  const reply = await detectThreadsReplyComposerLocally(dataUrl).catch(() => null);
  if (reply) {
    assertions.push({ detector: "reply_composer", expected: reply });
    return assertions;
  }

  if (await detectThreadsShareSheetLocally(dataUrl).catch(() => false)) {
    assertions.push({ detector: "share_sheet", expected: true });
    return assertions;
  }

  if (await detectThreadsPostActionSheetLocally(dataUrl).catch(() => false)) {
    assertions.push({ detector: "post_action_sheet", expected: true });
    return assertions;
  }

  if (await detectThreadsFullscreenMediaViewerLocally(dataUrl).catch(() => false)) {
    assertions.push({ detector: "fullscreen_media_viewer", expected: true });
    return assertions;
  }

  if (await detectThreadsSideDrawerLocally(dataUrl).catch(() => false)) {
    assertions.push({ detector: "side_drawer", expected: true });
    return assertions;
  }

  if (await detectThreadsSearchOverlayLocally(dataUrl).catch(() => false)) {
    assertions.push({ detector: "search_overlay", expected: true });
    assertions.push({ detector: "composer", expected: false });
    return assertions;
  }

  const home = await detectThreadsHomeFeedLocally(dataUrl).catch(() => null);
  if (home) {
    assertions.push({ detector: "home_feed", expected: home });
  }

  if (!assertions.length && xml) {
    const blocked = detectThreadsBlockedScreenFromUiXml(xml);
    if (blocked && !/結構不可讀|结构不可读|null root/i.test(blocked)) {
      assertions.push({ detector: "blocked_screen", expected: true });
    }
  }

  return uniqueAssertions(assertions);
}

function copyArtifact(sourcePath: string | null | undefined, targetName: string): string | undefined {
  if (!sourcePath || !fs.existsSync(sourcePath)) return undefined;
  const ext = path.extname(sourcePath) || ".bin";
  const relativePath = path.join("samples", `${targetName}${ext}`).replace(/\\/g, "/");
  const targetPath = path.join(THREADS_PROMOTED_SAMPLE_ROOT, relativePath);
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.copyFileSync(sourcePath, targetPath);
  return relativePath;
}

async function promoteWithCurrentDetectors() {
  const latestIndex = readThreadsPublishSampleIndex();
  const manifest: ThreadsPromotedSampleManifest = readThreadsPromotedSampleManifest();
  let manifestChanged = false;
  if (!options.dryRun) {
    const beforeCount = manifest.samples.length;
    const kept: ThreadsPromotedSample[] = [];
    for (const sample of manifest.samples) {
      if (!Array.isArray(sample.assertions) || sample.assertions.length === 0) continue;
      if (!await promotedSampleHasValidScreenshot(sample)) continue;
      kept.push(sample);
    }
    manifest.samples = kept;
    manifestChanged = manifest.samples.length !== beforeCount;
  }
  const existingIds = new Set(manifest.samples.map((sample) => sample.id));
  const promoted: ThreadsPromotedSample[] = [];
  const skipped: Array<{ id: string; reason: string }> = [];
  const limit = Math.max(1, options.limit ?? 20);

  for (const sample of latestIndex.samples) {
    if (promoted.length >= limit) break;
    if (options.scenario && sample.scenario !== options.scenario) continue;
    if (!options.includePromoted && (sample.status === "promoted" || existingIds.has(sample.id))) {
      skipped.push({ id: sample.id, reason: "already promoted" });
      continue;
    }
    const assertions = await inferAssertionsWithCurrentDetectors(sample);
    if (!assertions.length) {
      skipped.push({ id: sample.id, reason: "no current detector assertion" });
      continue;
    }
    const needsScreenshot = assertions.some((assertion) => assertion.detector !== "android_camera" && assertion.detector !== "blocked_screen");
    const screenshotSize = needsScreenshot ? await readImageSize(sample.screenshotPath) : null;
    if (needsScreenshot && !isThreadsPromotedSampleScreenSize(screenshotSize)) {
      const actual = screenshotSize ? `${screenshotSize.width}x${screenshotSize.height}` : "missing";
      skipped.push({
        id: sample.id,
        reason: `screenshot resolution ${actual}; need ${THREADS_PROMOTED_SAMPLE_SCREEN.width}x${THREADS_PROMOTED_SAMPLE_SCREEN.height}`,
      });
      continue;
    }
    const currentPage = pageFromAssertion(assertions[0]);

    const promotedSample: ThreadsPromotedSample = {
      id: sample.id,
      scenario: sample.scenario,
      mediaKind: sample.mediaKind,
      page: sanitizeSampleText(currentPage.page || sample.page),
      reason: sanitizeSampleText(currentPage.reason || sample.reason),
      sourceJsonPath: path.relative(process.cwd(), sample.jsonPath).replace(/\\/g, "/"),
      assertions,
      promotedAt: new Date().toISOString(),
    };
    if (screenshotSize) promotedSample.screenshotSize = screenshotSize;
    if (!options.dryRun) {
      promotedSample.screenshot = copyArtifact(sample.screenshotPath, `${sample.id}-screenshot`);
      promotedSample.xml = copyArtifact(sample.xmlPath, `${sample.id}-ui`);
      manifest.samples = manifest.samples.filter((item) => item.id !== sample.id);
      manifest.samples.push(promotedSample);
      manifestChanged = true;
      sample.status = "promoted";
      sample.promotedAt = promotedSample.promotedAt;
      sample.assertions = assertions;
      sample.updatedAt = new Date().toISOString();
    }
    promoted.push(promotedSample);
  }

  if (!options.dryRun && manifestChanged) {
    fs.mkdirSync(path.dirname(THREADS_PROMOTED_SAMPLE_MANIFEST), { recursive: true });
    manifest.samples.sort((a, b) => a.id.localeCompare(b.id));
    fs.writeFileSync(THREADS_PROMOTED_SAMPLE_MANIFEST, `${JSON.stringify({
      ...manifest,
      updatedAt: new Date().toISOString(),
    }, null, 2)}\n`, "utf8");
    writeThreadsPublishSampleIndex(latestIndex);
  }

  return { promoted, skipped };
}

const result = await promoteWithCurrentDetectors();
console.log(JSON.stringify({
  ok: true,
  indexed: index.samples.length,
  promoted: result.promoted.length,
  skipped: result.skipped.length,
  manifest: THREADS_PROMOTED_SAMPLE_MANIFEST,
  promotedIds: result.promoted.map((sample) => sample.id),
  skippedPreview: result.skipped.slice(0, 20),
}, null, 2));
