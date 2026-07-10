import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { runPersonaWorkflow } from "@/core/persona/persona-workflow-service";
import { getArchivePendingPostsForPlatform, loadPersonaArchive, markFavoritePostsPublished, updatePersonaArchiveProfile } from "@/lib/persona-archives";
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
  postId?: string;
  postIds?: string[];
  padCode: string;
  padCodes?: string[];
  platform: Platform;
  postSource?: "posts" | "favorites";
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
  const postIds = [...new Set([...(Array.isArray(input.postIds) ? input.postIds : []), input.postId].map((item) => String(item || "").trim()).filter(Boolean))];
  const postId = postIds[0] || "";
  const padCodes = [...new Set([...(Array.isArray(input.padCodes) ? input.padCodes : []), input.padCode].map((item) => String(item || "").trim()).filter(Boolean))];
  const padCode = padCodes[0] || "";
  const platform = String(input.platform || "").trim() as Platform;
  const postSource = input.postSource === "favorites" ? "favorites" : "posts";
  if (!archiveId) throw new Error("missing archiveId");
  if (!postId) throw new Error("missing postId");
  if (!padCode) throw new Error("missing padCode");
  if (platform !== "threads" && platform !== "telegram") throw new Error("platform must be threads or telegram");
  if (input.dryRun !== false) throw new Error("dryRun=false must be supplied by the task runner");

  const archive = await loadPersonaArchive(archiveId);
  if (!archive) throw new Error("persona archive not found");
  const availablePosts = postSource === "favorites" ? archive.favoritePosts || [] : getArchivePendingPostsForPlatform(archive, platform);
  const posts = postIds.map((id) => availablePosts.find((item) => item.id === id)).filter(Boolean) as typeof availablePosts;
  if (posts.length !== postIds.length) throw new Error("one or more persona posts are not pending for this platform");

  const publishedContentById: Record<string, string> = {};
  const publishedMetaById: Record<string, any> = {};
  const allPublishResults: Array<{ postId: string; imageUrl: string; caption: string; padCode: string; result: any }> = [];
  const completedCheckpointKeys: string[] = [];
  for (const post of posts) {
    const caption = buildPersonaPublishCaption(post.content, archive.setup);
    const imageUrl = getStoredPostPrimaryMediaUrl(post) || post.imageUrl || "";
    publishedContentById[post.id] = caption;
    const publishResults = [];
    for (const targetPadCode of padCodes) {
      const checkpointKey = publishCheckpointKey(post.id, platform, targetPadCode);
      const existingCheckpoint = (await loadPublishCheckpoints(archiveId))[checkpointKey];
      const result = existingCheckpoint || await publishPost(
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
  if (postSource === "favorites") {
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
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
