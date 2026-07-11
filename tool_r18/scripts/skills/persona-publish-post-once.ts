import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { runPersonaWorkflow } from "@/core/persona/persona-workflow-service";
import { deleteArchiveEpisode, getArchivePendingPostsForPlatform, loadPersonaArchive, markFavoritePostsPublished, savePersonaArchive, updatePersonaArchiveProfile } from "@/lib/persona-archives";
import { publishPost, type Platform, type PublishProgress } from "@/lib/vmos-publisher";
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
  linkTemplateApplied?: boolean;
  uiContentType?: "free" | "paid";
  dryRun?: boolean;
};

installNodePersonaArchiveBridge();
const archiveMutationRepo = createNodePublishQueueRepository();

async function withArchiveMutationLock<T>(archiveId: string, operation: () => Promise<T>): Promise<T> {
  const lockCode = `__web_archive__:${archiveId}`;
  const owner = `web-archive-${process.pid}-${randomUUID()}`;
  if (!archiveMutationRepo.acquirePadLock(lockCode, owner)) {
    throw new Error("人設歸檔正在被其他發布任務更新，請稍後重試");
  }
  try {
    return await operation();
  } finally {
    archiveMutationRepo.releasePadLock(lockCode, owner);
  }
}

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

const publishProgressFile = String(process.env.WEB_PUBLISH_PROGRESS_FILE || "").trim();

function publishProgressIcon(progress: Pick<PublishProgress, "done" | "warning" | "error">) {
  if (progress.error) return "❌";
  if (progress.warning) return "⚠️";
  return progress.done ? "✅" : "▶️";
}

function emitPublishProgress(event: Record<string, unknown>) {
  if (!publishProgressFile) return;
  try {
    fs.appendFileSync(publishProgressFile, `${JSON.stringify({ at: Date.now(), ...event })}\n`, "utf8");
  } catch {
    // Progress reporting must never interrupt a real publication.
  }
}

function readInput(): Input {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as Input;
}

function legacyPublishCheckpointKey(postId: string, platform: Platform, padCode: string) {
  return `${postId}|${platform}|${padCode}`;
}

function publishCheckpointKey(args: {
  postId: string;
  platform: Platform;
  padCode: string;
  caption: string;
  mediaUrl: string;
  telegramTargetGroupName?: string;
  telegramGroupContentType?: string;
}) {
  const digest = createHash("sha256").update(JSON.stringify(args)).digest("hex").slice(0, 24);
  return `${args.postId}|${args.platform}|${args.padCode}|${digest}`;
}

async function loadPublishCheckpoints(archiveId: string): Promise<Record<string, any>> {
  const archive = await loadPersonaArchive(archiveId);
  const checkpoints = (archive?.setup as any)?.webPublishCheckpoints;
  return checkpoints && typeof checkpoints === "object" && !Array.isArray(checkpoints) ? checkpoints : {};
}

async function savePublishCheckpoint(archiveId: string, key: string, value: any) {
  await withArchiveMutationLock(archiveId, async () => {
    const archive = await loadPersonaArchive(archiveId);
    if (!archive) throw new Error("persona archive disappeared while saving publish checkpoint");
    const checkpoints = (archive.setup as any)?.webPublishCheckpoints || {};
    await updatePersonaArchiveProfile(archiveId, {
      setup: { ...(archive.setup || {}), webPublishCheckpoints: { ...checkpoints, [key]: value } } as any,
    });
  });
}

async function clearPublishCheckpoints(archiveId: string, keys: string[]) {
  await withArchiveMutationLock(archiveId, async () => {
    const archive = await loadPersonaArchive(archiveId);
    if (!archive) return;
    const checkpoints = { ...((archive.setup as any)?.webPublishCheckpoints || {}) };
    for (const key of keys) delete checkpoints[key];
    await updatePersonaArchiveProfile(archiveId, {
      setup: { ...(archive.setup || {}), webPublishCheckpoints: checkpoints } as any,
    });
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
    const checkpoints = await loadPublishCheckpoints(archiveId);
    const prefix = `${postId}|${platform}|`;
    const recovered = Object.entries(checkpoints).find(([key, value]) => key.startsWith(prefix) && String(value?.mediaUrl || "").trim());
    customMediaUrl = recovered ? String(recovered[1]?.mediaUrl || "").trim() : "";
  }
  if (isCustomPublish && input.generateImage === true && !customMediaUrl) {
    const tempPostId = randomUUID();
    const tempLockCode = `__web_custom_image__:${archiveId}`;
    const tempLockOwner = `web-custom-image-${process.pid}-${tempPostId}`;
    if (!archiveMutationRepo.acquirePadLock(tempLockCode, tempLockOwner)) {
      throw new Error("這個人設正在生成另一個自定義發布圖片，請稍後重試");
    }
    try {
      await withArchiveMutationLock(archiveId, async () => {
        const latest = await loadPersonaArchive(archiveId);
        if (!latest) throw new Error("persona archive disappeared before custom image generation");
        const now = new Date().toISOString();
        const withoutStaleTemps = latest.posts.filter((post) => String(post.sourceMeta?.source || "") !== "web_custom_publish_temp");
        await savePersonaArchive({
          ...latest,
          posts: [...withoutStaleTemps, {
            id: tempPostId,
            title: "Web custom publish image",
            content: customContent,
            wordCount: customContent.length,
            orderIndex: withoutStaleTemps.length,
            createdAt: now,
            updatedAt: now,
            sourceMeta: { source: "web_custom_publish_temp" },
          }],
        });
      });
      const generated = await generateArchivePostImageCandidates({ archiveId, postId: tempPostId, source: "posts" });
      customMediaUrl = String(generated.imageUrls?.[0] || "").trim();
      if (!customMediaUrl) throw new Error("custom publish image generation returned no image");
    } finally {
      try {
        await withArchiveMutationLock(archiveId, async () => {
          await deleteArchiveEpisode(archiveId, tempPostId);
        });
      } finally {
        archiveMutationRepo.releasePadLock(tempLockCode, tempLockOwner);
      }
    }
    archive = await loadPersonaArchive(archiveId);
    if (!archive) throw new Error("persona archive disappeared after custom image generation");
  }
  const availablePosts = postSource === "favorites" ? archive.favoritePosts || [] : getArchivePendingPostsForPlatform(archive, platform);
  const historyByPostId = new Map((archive.publishHistory || []).map((record) => [String(record.archivePostId || ""), record]));
  const recoveredHistoryPostIds = new Set<string>();
  const posts = isCustomPublish
    ? [{ id: postId, content: customContent, imageUrl: customMediaUrl, mediaUrl: customMediaUrl, telegramGroupContentType: input.uiContentType === "paid" ? "paid" : "free" } as any]
    : postIds.map((id) => {
        const pending = availablePosts.find((item) => item.id === id);
        if (pending) return pending;
        const history = historyByPostId.get(id);
        if (!history) return null;
        recoveredHistoryPostIds.add(id);
        return {
          id,
          content: history.content,
          imageUrl: history.imageUrl,
          mediaUrl: history.imageUrl,
          telegramGroupContentType: history.telegramGroupContentType,
          sourceMeta: history.sourceMeta,
        } as any;
      }).filter(Boolean) as typeof availablePosts;
  if (posts.length !== postIds.length) throw new Error("one or more persona posts are not pending for this platform");

  const publishedContentById: Record<string, string> = {};
  const publishedMetaById: Record<string, any> = {};
  const allPublishResults: Array<{ postId: string; imageUrl: string; caption: string; padCode: string; result: any }> = [];
  const completedCheckpointKeys: string[] = [];
  const publishQueue = createNodePublishQueueRepository();
  for (const [postIndex, post] of posts.entries()) {
    const override = String(input.contentOverrides?.[post.id] || "").trim();
    const rawContent = isCustomPublish ? customContent : override || post.content;
    const caption = input.linkTemplateApplied
      ? String(rawContent || "").trim()
      : buildPersonaPublishCaption(rawContent, archive.setup);
    const imageUrl = isCustomPublish ? customMediaUrl : getStoredPostPrimaryMediaUrl(post) || post.imageUrl || "";
    publishedContentById[post.id] = caption;
    const publishResults = [];
    for (const targetPadCode of padCodes) {
      const telegramTargetGroupName = platform === "telegram" ? resolveTelegramTargetGroupNameForPost(archive, post) : undefined;
      const telegramGroupContentType = platform === "telegram" ? resolveTelegramGroupContentTypeForPost(post) : undefined;
      const checkpointKey = publishCheckpointKey({ postId: post.id, platform, padCode: targetPadCode, caption, mediaUrl: imageUrl, telegramTargetGroupName, telegramGroupContentType });
      const legacyCheckpointKey = legacyPublishCheckpointKey(post.id, platform, targetPadCode);
      const checkpoints = await loadPublishCheckpoints(archiveId);
      const existingCheckpoint = checkpoints[checkpointKey] || checkpoints[legacyCheckpointKey];
      if (recoveredHistoryPostIds.has(post.id) && !existingCheckpoint) {
        throw new Error(`推文 ${post.id} 已完成歸檔，但缺少可恢復的發布 checkpoint`);
      }
      const lockOwner = `web-publish-${process.pid}-${post.id}-${targetPadCode}`;
      if (!existingCheckpoint && !publishQueue.acquirePadLock(targetPadCode, lockOwner)) {
        throw new Error(`智能體手機 ${targetPadCode} 正在執行其他發布任務，請稍後重試`);
      }
      let result: any;
      try {
        emitPublishProgress({
          step: `開始發布第 ${postIndex + 1}/${posts.length} 篇`,
          line: `▶️ 開始發布第 ${postIndex + 1}/${posts.length} 篇（${targetPadCode}）`,
          platform,
          padCode: targetPadCode,
          postId: post.id,
          postIndex: postIndex + 1,
          postCount: posts.length,
        });
        result = existingCheckpoint || await publishPost(
            resolveVmosCredentials(),
            {
              padCode: targetPadCode,
              platform,
              caption,
              mediaUrl: imageUrl || undefined,
              telegramTargetGroupName,
              telegramGroupContentType,
            },
            (progress) => {
              const prefix = posts.length > 1 || padCodes.length > 1
                ? `[第 ${postIndex + 1}/${posts.length} 篇 · ${targetPadCode}] `
                : "";
              emitPublishProgress({
                step: progress.step,
                line: `${publishProgressIcon(progress)} ${prefix}${progress.step}`,
                platform,
                padCode: targetPadCode,
                postId: post.id,
                postIndex: postIndex + 1,
                postCount: posts.length,
                done: Boolean(progress.done),
                warning: Boolean(progress.warning),
                error: Boolean(progress.error),
              });
            },
          );
        if (existingCheckpoint) {
          emitPublishProgress({
            step: "已恢復完成的發布結果",
            line: `✅ 已恢復第 ${postIndex + 1}/${posts.length} 篇的發布結果（${targetPadCode}）`,
            platform,
            padCode: targetPadCode,
            postId: post.id,
            postIndex: postIndex + 1,
            postCount: posts.length,
            done: true,
          });
        }
        if (!existingCheckpoint) {
          await savePublishCheckpoint(archiveId, checkpointKey, {
            publishedUrl: String(result?.publishedUrl || "").trim(),
            screenshotUrl: result?.screenshotUrl,
            caption,
            mediaUrl: imageUrl,
            telegramTargetGroupName,
            telegramGroupContentType,
            completedAt: new Date().toISOString(),
          });
        }
      } finally {
        if (!existingCheckpoint) publishQueue.releasePadLock(targetPadCode, lockOwner);
      }
      completedCheckpointKeys.push(checkpointKey);
      if (checkpoints[legacyCheckpointKey]) completedCheckpointKeys.push(legacyCheckpointKey);
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
  const pendingPostIds = postIds.filter((id) => !recoveredHistoryPostIds.has(id));
  if (isCustomPublish || pendingPostIds.length === 0) {
    // Custom publishes are intentionally not moved through the stored-post archive.
  } else if (postSource === "favorites") {
    await withArchiveMutationLock(archiveId, async () => {
      await markFavoritePostsPublished(archiveId, pendingPostIds, publishedContentById, publishedMetaById);
    });
  } else {
    await withArchiveMutationLock(archiveId, async () => {
      await runPersonaWorkflow({
        action: "finalize-published",
        archiveId,
        postIds: pendingPostIds,
        publishedContentById,
        publishedMetaById,
      });
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
