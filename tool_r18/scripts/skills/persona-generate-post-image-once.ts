import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { loadPersonaArchive, savePersonaArchive } from "@/lib/persona-archives";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import { attachSelectedImageCandidateToArchivePost, generateArchivePostImageCandidates, regenerateArchivePostImage } from "@/telegram-bot";

type Input = {
  archiveId: string;
  postId: string;
  action?: "generate_candidates" | "select_candidate";
  imageAspectRatio?: string;
  imageWidth?: number;
  imageHeight?: number;
  imageRatioLabel?: string;
  imageUrl?: string;
  postSource?: "posts" | "favorites";
  selectedIndexes?: number[];
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
  const postSource = input.postSource === "favorites" ? "favorites" : "posts";
  if (!archiveId) throw new Error("missing archiveId");
  if (!postId) throw new Error("missing postId");

  if (input.action === "generate_candidates") {
    const result = await generateArchivePostImageCandidates({
      archiveId,
      postId,
      source: postSource,
      imageAspectRatio: String(input.imageAspectRatio || "").trim() || undefined,
      imageWidth: Number(input.imageWidth) || undefined,
      imageHeight: Number(input.imageHeight) || undefined,
      imageRatioLabel: String(input.imageRatioLabel || "").trim() || undefined,
    });
    printJson({ ok: true, archiveId, postId, content: result.content, imageUrls: result.imageUrls });
    return;
  }
  if (input.action === "select_candidate") {
    const archive = await loadPersonaArchive(archiveId);
    const post = postSource === "favorites"
      ? (archive?.favoritePosts || []).find((item) => item.id === postId)
      : archive?.posts.find((item) => item.id === postId);
    const imageUrl = String(input.imageUrl || "").trim();
    if (!post) throw new Error("persona post not found");
    if (!imageUrl) throw new Error("missing imageUrl");
    const selectedIndexes = new Set((input.selectedIndexes || []).filter((item) => Number.isInteger(item) && item >= 0));
    if ((postSource === "favorites" || selectedIndexes.size > 0) && archive) {
      const now = new Date().toISOString();
      const currentMedia = Array.isArray(post.mediaItems) && post.mediaItems.length
        ? post.mediaItems
        : [post.mediaUrl || post.imageUrl].filter(Boolean).map((url) => ({ url: String(url), type: "image" as const }));
      const nextMedia = selectedIndexes.size
        ? currentMedia.map((item, index) => selectedIndexes.has(index) ? { url: imageUrl, type: "image" as const } : item)
        : [{ url: imageUrl, type: "image" as const }];
      const updatePost = (item: typeof post) => item.id === postId ? {
        ...item,
        imageUrl: nextMedia[0]?.url || imageUrl,
        mediaUrl: nextMedia[0]?.url || imageUrl,
        mediaType: nextMedia[0]?.type || "image",
        mediaItems: nextMedia,
        sourceMeta: { ...(item.sourceMeta || {}), edited: true, mediaItems: nextMedia },
        imageHistory: [...(item.imageHistory || []), { imageUrl, createdAt: now, query: item.content, source: "generated" as const }],
        updatedAt: now,
      } : item;
      await savePersonaArchive({
        ...archive,
        posts: postSource === "posts" ? archive.posts.map(updatePost) : archive.posts,
        favoritePosts: postSource === "favorites" ? (archive.favoritePosts || []).map(updatePost) : archive.favoritePosts,
      });
      printJson({ ok: true, archiveId, postId, imageUrl });
      return;
    }
    const result = await attachSelectedImageCandidateToArchivePost({ archiveId, postId, content: post.content, imageUrl });
    printJson({ ok: true, archiveId, postId, imageUrl: result.imageUrl });
    return;
  }
  const result = await regenerateArchivePostImage({ archiveId, postId });
  printJson({ ok: true, archiveId, postId, imageUrl: result.imageUrl });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
