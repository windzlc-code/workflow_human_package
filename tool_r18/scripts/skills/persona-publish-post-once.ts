import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { runPersonaWorkflow } from "@/core/persona/persona-workflow-service";
import { deleteArchiveEpisode, getArchivePendingPostsForPlatform, loadPersonaArchive, markFavoritePostsPublished, savePersonaArchive, updatePersonaArchiveProfile } from "@/lib/persona-archives";
import { publishPost, type Platform } from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import { createNodePublishQueueRepository } from "@/runtime/node/publish-queue-repository";
import {
  buildPersonaPublishCaption,
  generateArchivePostImageCandidates,
  getStoredPostPrimaryMediaUrl,
  resolveTelegramGroupContentTypeForPost,
  resolveTelegramTargetGroupNameForPost,
} from "@/telegram-bot";

type Input = {
  archiveId: string;
  postId?: string;
  postIds?: string[];
  padCode: string;
  padCodes?: string[];
  platform: Platform;
  postSource?: "posts" | "favorites";
  contentOverrides?: Record<string, string>;
  customContent?: string;
  customMediaUrl?: string;
  generateImage?: boolean;
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

function publishCheckpointKey(postId: string, platform: Platform, padCode: string) {
  return `${postId}|${platform}|${padCode}`;
}

async function loadPublishCheckpoints(archiveId: string): Promise<Record<string, any>> {
  const archive = await loadPersonaArchive(archiveId);
  const checkpoints = (archive?.setup as any)?.webPublishCheckpoints;
  return checkpoints && typeof checkpoints === "object" && !Array.isArray(checkpoints) ? checkpoints : {};
}

async function savePublishCheckpoint(archiveId: string, key: string, value: any) {
  const archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error("persona archive disappeared while saving publish checkpoint");
  const checkpoints = await loadPublishCheckpoints(archiveId);
  await updatePersonaArchiveProfile(archiveId, {
    setup: { ...(archive.setup || {}), webPublishCheckpoints: { ...checkpoints, [key]: value } } as any,
  });
}

async function clearPublishCheckpoints(archiveId: string, keys: string[]) {
  const archive = await loadPersonaArchive(archiveId);
  if (!archive) return;
  const checkpoints = { ...await loadPublishCheckpoints(archiveId) };
  for (const key of keys) delete checkpoints[key];
  await updatePersonaArchiveProfile(archiveId, {
    setup: { ...(archive.setup || {}), webPublishCheckpoints: checkpoints } as any,
  });
}

async function main() {
  const input = readInput();
  const archiveId = String(input.archiveId || "").trim();
  const customContent = String(input.customContent || "").trim();
  let customMediaUrl = String(input.customMediaUrl || "").trim();
  const isCustomPublish = Boolean(customContent || customMediaUrl);
  const postIds = isCustomPublish
    ? [`web-custom-${createHash("sha1").update(`${customContent}\n${customMediaUrl}`).digest("hex").slice(0, 16)}`]
    : [...new Set([...(Array.isArray(input.postIds) ? input.postIds : []), input.postId].map((item) => String(item || "").trim()).filter(Boolean))];
  const postId = postIds[0] || "";
  const padCodes = [...new Set([...(Array.isArray(input.padCodes) ? input.padCodes : []), input.padCode].map((item) => String(item || "").trim()).filter(Boolean))];
  const padCode = padCodes[0] || "";
  const platform = String(input.platform || "").trim() as Platform;
  const postSource = input.postSource === "favorites" ? "favorites" : "posts";
  if (!archiveId) throw new Error("missing archiveId");
  if (!postId) throw new Error("missing postId or custom content");
  if (!padCode) throw new Error("missing padCode");
  if (platform !== "threads" && platform !== "telegram") throw new Error("platform must be threads or telegram");
  if (input.dryRun !== false) throw new Error("dryRun=false must be supplied by the task runner");

  let archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error("persona archive not found");
  if (isCustomPublish && input.generateImage === true && !customMediaUrl) {
    const tempPostId = randomUUID();
    const now = new Date().toISOString();
    await savePersonaArchive({
      ...archive,
      posts: [...archive.posts, {
        id: tempPostId,
        title: "Web custom publish image",
        content: customContent,
        wordCount: customContent.length,
        orderIndex: archive.posts.length,
        createdAt: now,
        updatedAt: now,
        sourceMeta: { source: "web_custom_publish" },
      }],
    });
    try {
      const generated = await generateArchivePostImageCandidates({ archiveId, postId: tempPostId, source: "posts" });
      customMediaUrl = String(generated.imageUrls?.[0] || "").trim();
      if (!customMediaUrl) throw new Error("custom publish image generation returned no image");
    } finally {
      await deleteArchiveEpisode(archiveId, tempPostId).catch(() => undefined);
    }
    archive = await loadPersonaArchive(archiveId);
    if (!archive) throw new Error("persona archive disappeared after custom image generation");
  }
  const availablePosts = postSource === "favorites" ? archive.favoritePosts || [] : getArchivePendingPostsForPlatform(archive, platform);
  const posts = isCustomPublish
    ? [{ id: postId, content: customContent, imageUrl: customMediaUrl, mediaUrl: customMediaUrl } as any]
    : postIds.map((id) => availablePosts.find((item) => item.id === id)).filter(Boolean) as typeof availablePosts;
  if (posts.length !== postIds.length) throw new Error("one or more persona posts are not pending for this platform");

  const publishedContentById: Record<string, string> = {};
  const publishedMetaById: Record<string, any> = {};
  const allPublishResults: Array<{ postId: string; imageUrl: string; caption: string; padCode: string; result: any }> = [];
  const completedCheckpointKeys: string[] = [];
  const publishQueue = createNodePublishQueueRepository();
  for (const post of posts) {
    const override = String(input.contentOverrides?.[post.id] || "").trim();
    const caption = isCustomPublish
      ? buildPersonaPublishCaption(customContent, archive.setup)
      : buildPersonaPublishCaption(override || post.content, archive.setup);
    const imageUrl = isCustomPublish ? customMediaUrl : getStoredPostPrimaryMediaUrl(post) || post.imageUrl || "";
    publishedContentById[post.id] = caption;
    const publishResults = [];
    for (const targetPadCode of padCodes) {
      const checkpointKey = publishCheckpointKey(post.id, platform, targetPadCode);
      const existingCheckpoint = (await loadPublishCheckpoints(archiveId))[checkpointKey];
      const lockOwner = `web-publish-${process.pid}-${post.id}-${targetPadCode}`;
      if (!existingCheckpoint && !publishQueue.acquirePadLock(targetPadCode, lockOwner)) {
        throw new Error(`智能體手機 ${targetPadCode} 正在執行其他發布任務，請稍後重試`);
      }
      let result: any;
      try {
        result = existingCheckpoint || await publishPost(
            resolveVmosCredentials(),
            {
              padCode: targetPadCode,
              platform,
              caption,
              mediaUrl: imageUrl || undefined,
              telegramTargetGroupName: platform === "telegram" ? resolveTelegramTargetGroupNameForPost(archive, post) : undefined,
              telegramGroupContentType: platform === "telegram" ? resolveTelegramGroupContentTypeForPost(post) : undefined,
            },
            () => undefined,
          );
      } finally {
        if (!existingCheckpoint) publishQueue.releasePadLock(targetPadCode, lockOwner);
      }
      if (!existingCheckpoint) {
        await savePublishCheckpoint(archiveId, checkpointKey, {
          publishedUrl: String(result?.publishedUrl || "").trim(),
          screenshotUrl: result?.screenshotUrl,
          completedAt: new Date().toISOString(),
        });
      }
      completedCheckpointKeys.push(checkpointKey);
      publishResults.push({ padCode: targetPadCode, result });
      allPublishResults.push({ postId: post.id, imageUrl, caption, padCode: targetPadCode, result });
    }
    const firstResult = publishResults[0]?.result;
    const publishedUrl = String(firstResult?.publishedUrl || "").trim();
    publishedMetaById[post.id] = {
        platform,
        padCode,
        imageUrl: imageUrl || undefined,
        screenshotUrl: firstResult?.screenshotUrl,
        sourceMeta: post.sourceMeta,
        publishedUrl: publishedUrl || undefined,
        publishedMeta: publishedUrl
          ? { source: "published_post", platform, sourceUrl: publishedUrl, capturedAt: new Date().toISOString() }
          : undefined,
        publishedTargets: publishResults.map(({ padCode: targetPadCode, result }) => ({
          platform,
          padCode: targetPadCode,
          imageUrl: imageUrl || undefined,
          screenshotUrl: result?.screenshotUrl,
          publishedUrl: String(result?.publishedUrl || "").trim() || undefined,
          publishedMeta: result?.publishedUrl
            ? { source: "published_post", platform, sourceUrl: String(result.publishedUrl), capturedAt: new Date().toISOString() }
            : undefined,
        })),
    };
  }
  if (isCustomPublish) {
    // Custom publishes are intentionally not moved through the stored-post archive.
  } else if (postSource === "favorites") {
    await markFavoritePostsPublished(archiveId, postIds, publishedContentById, publishedMetaById);
  } else {
    await runPersonaWorkflow({
      action: "finalize-published",
      archiveId,
      postIds,
      publishedContentById,
      publishedMetaById,
    });
  }
  await clearPublishCheckpoints(archiveId, completedCheckpointKeys);

  const firstPublished = allPublishResults[0];
  const imageUrl = firstPublished?.imageUrl || "";
  const firstResult = firstPublished?.result;
  const publishedUrl = String(firstResult?.publishedUrl || "").trim();

  printJson({
    ok: true,
    archiveId,
    postId,
    postIds,
    imageUrl,
    screenshotUrl: firstResult?.screenshotUrl,
    publishedUrl,
    publishedCount: allPublishResults.length,
    customPublish: isCustomPublish,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
