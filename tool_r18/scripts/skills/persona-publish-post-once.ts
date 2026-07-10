import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { runPersonaWorkflow } from "@/core/persona/persona-workflow-service";
import { getArchivePendingPostsForPlatform, loadPersonaArchive } from "@/lib/persona-archives";
import { publishPost, type Platform } from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import {
  buildPersonaPublishCaption,
  getStoredPostPrimaryMediaUrl,
  resolveTelegramGroupContentTypeForPost,
  resolveTelegramTargetGroupNameForPost,
} from "@/telegram-bot";

type Input = {
  archiveId: string;
  postId: string;
  padCode: string;
  platform: Platform;
  dryRun?: boolean;
};

installNodePersonaArchiveBridge();

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): Input {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as Input;
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  const postId = String(input.postId || "").trim();
  const padCode = String(input.padCode || "").trim();
  const platform = String(input.platform || "").trim() as Platform;
  if (!archiveId) throw new Error("missing archiveId");
  if (!postId) throw new Error("missing postId");
  if (!padCode) throw new Error("missing padCode");
  if (platform !== "threads" && platform !== "telegram") throw new Error("platform must be threads or telegram");
  if (input.dryRun !== false) throw new Error("dryRun=false must be supplied by the task runner");

  const archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error("persona archive not found");
  const post = getArchivePendingPostsForPlatform(archive, platform).find((item) => item.id === postId);
  if (!post) throw new Error("persona post is not pending for this platform");

  const caption = buildPersonaPublishCaption(post.content, archive.setup);
  const imageUrl = getStoredPostPrimaryMediaUrl(post) || post.imageUrl || "";
  const result = await publishPost(
    resolveVmosCredentials(),
    {
      padCode,
      platform,
      caption,
      mediaUrl: imageUrl || undefined,
      telegramTargetGroupName: platform === "telegram" ? resolveTelegramTargetGroupNameForPost(archive, post) : undefined,
      telegramGroupContentType: platform === "telegram" ? resolveTelegramGroupContentTypeForPost(post) : undefined,
    },
    () => undefined,
  );
  const publishedUrl = String(result?.publishedUrl || "").trim();
  await runPersonaWorkflow({
    action: "finalize-published",
    archiveId,
    postIds: [postId],
    publishedContentById: { [postId]: caption },
    publishedMetaById: {
      [postId]: {
        platform,
        padCode,
        imageUrl: imageUrl || undefined,
        screenshotUrl: result?.screenshotUrl,
        sourceMeta: post.sourceMeta,
        publishedUrl: publishedUrl || undefined,
        publishedMeta: publishedUrl
          ? { source: "published_post", platform, sourceUrl: publishedUrl, capturedAt: new Date().toISOString() }
          : undefined,
      },
    },
  });

  printJson({
    ok: true,
    archiveId,
    postId,
    imageUrl,
    screenshotUrl: result?.screenshotUrl,
    publishedUrl,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
