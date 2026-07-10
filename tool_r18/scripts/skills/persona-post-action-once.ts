import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { randomUUID } from "node:crypto";
import {
  deleteArchiveEpisode,
  deleteArchiveEpisodes,
  loadPersonaArchive,
  savePersonaArchive,
  updatePersonaArchivePostDraft,
} from "@/lib/persona-archives";
import type { PersonaArchive, PersonaArchivePost } from "@/core/archives/persona-archive-domain";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import { regenerateArchivePostContent, refreshStoredPostSentimentMetrics } from "@/telegram-bot";

installNodePersonaArchiveBridge();

type PostSource = "posts" | "favorites";
type PostAction =
  | "regenerate_content"
  | "favorite"
  | "delete"
  | "delete_many"
  | "update_content"
  | "refresh_metrics"
  | "delete_media"
  | "replace_media";
type StoredMediaItem = {
  url: string;
  type?: "image" | "video" | "unknown";
  localPath?: string;
  warning?: string;
};
type PersonaPostActionInput = {
  archiveId: string;
  postId?: string;
  postIds?: string[];
  action: PostAction;
  source?: PostSource;
  postSource?: PostSource;
  rewriteMode?: "source_structure" | "persona_style";
  content?: string;
  selectedIndexes?: number[];
  mediaUrl?: string;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function readInput(): PersonaPostActionInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as PersonaPostActionInput;
}

function sourcePosts(archive: PersonaArchive, source: PostSource): PersonaArchivePost[] {
  return source === "favorites" ? archive.favoritePosts || [] : archive.posts || [];
}

function inputSource(input: PersonaPostActionInput): PostSource {
  const source = String(input.postSource || input.source || "posts").trim();
  if (source !== "posts" && source !== "favorites") throw new Error("source must be posts or favorites");
  return source;
}

function findPost(archive: PersonaArchive, postId: string, source: PostSource): PersonaArchivePost | null {
  return sourcePosts(archive, source).find((post) => post.id === postId) || null;
}

function storedMediaItems(post: PersonaArchivePost): StoredMediaItem[] {
  const result: StoredMediaItem[] = [];
  const add = (url: unknown, type?: StoredMediaItem["type"], localPath?: unknown, warning?: unknown) => {
    const text = String(localPath || url || "").trim();
    if (!text || result.some((item) => item.url === text)) return;
    result.push({
      url: text,
      type,
      localPath: typeof localPath === "string" && localPath.trim() ? localPath : undefined,
      warning: typeof warning === "string" && warning.trim() ? warning : undefined,
    });
  };
  for (const item of Array.isArray(post.mediaItems) ? post.mediaItems : []) {
    add(item.url, item.type, item.localPath);
  }
  for (const item of Array.isArray(post.sourceMeta?.mediaItems) ? post.sourceMeta.mediaItems : []) {
    add(item.url, item.type, item.localPath);
  }
  add(post.imageUrl);
  add(post.mediaUrl);
  const history = Array.isArray(post.imageHistory) ? post.imageHistory : [];
  add(history.length ? history[history.length - 1]?.imageUrl : "");
  return result;
}

function patchFavoritePost(
  archive: PersonaArchive,
  postId: string,
  updater: (post: PersonaArchivePost) => PersonaArchivePost,
): PersonaArchive {
  const favorites = archive.favoritePosts || [];
  if (!favorites.some((post) => post.id === postId)) throw new Error(`favorite post not found: ${postId}`);
  return { ...archive, favoritePosts: favorites.map((post) => post.id === postId ? updater(post) : post) };
}

async function saveUpdatedContent(input: PersonaPostActionInput, source: PostSource): Promise<PersonaArchivePost> {
  const content = String(input.content || "").trim();
  if (!content) throw new Error("content is required");
  if (source === "favorites") {
    const archive = await loadPersonaArchive(input.archiveId);
    if (!archive) throw new Error(`persona archive not found: ${input.archiveId}`);
    const now = new Date().toISOString();
    const saved = await savePersonaArchive(patchFavoritePost(archive, input.postId!, (post) => ({
      ...post,
      content,
      wordCount: content.length,
      sourceMeta: { ...(post.sourceMeta || {}), edited: true },
      updatedAt: now,
    })));
    return findPost(saved, input.postId!, source)!;
  }
  const updated = await updatePersonaArchivePostDraft(input.archiveId, input.postId!, {
    content,
    sourceMetaPatch: { edited: true },
  });
  if (!updated) throw new Error(`post not found: ${input.postId}`);
  return updated;
}

async function deleteSelectedMedia(input: PersonaPostActionInput, source: PostSource): Promise<PersonaArchivePost> {
  const archive = await loadPersonaArchive(input.archiveId);
  const post = archive ? findPost(archive, input.postId!, source) : null;
  if (!archive) throw new Error(`persona archive not found: ${input.archiveId}`);
  if (!post) throw new Error(`post not found: ${input.postId}`);
  const selected = new Set(input.selectedIndexes || []);
  const currentMedia = storedMediaItems(post);
  const nextMedia = currentMedia.filter((_, index) => !selected.has(index));
  if (nextMedia.length === currentMedia.length) throw new Error("selectedIndexes did not match stored media");
  const primary = nextMedia[0];
  const primaryUrl = primary?.url || primary?.localPath || "";
  const sourceMeta = {
    ...(post.sourceMeta || {}),
    edited: true,
    mediaItems: nextMedia,
  };
  if (source === "favorites") {
    const saved = await savePersonaArchive(patchFavoritePost(archive, input.postId!, (current) => ({
      ...current,
      imageUrl: primaryUrl || undefined,
      mediaUrl: primaryUrl || undefined,
      mediaType: primary?.type,
      mediaItems: nextMedia,
      sourceMeta,
      updatedAt: new Date().toISOString(),
    })));
    return findPost(saved, input.postId!, source)!;
  }
  const updated = await updatePersonaArchivePostDraft(input.archiveId, input.postId!, {
    imageUrl: primaryUrl,
    mediaUrl: primaryUrl,
    mediaType: primary?.type,
    mediaItems: nextMedia,
    sourceMetaPatch: { edited: true, mediaItems: nextMedia },
  });
  if (!updated) throw new Error(`post not found: ${input.postId}`);
  return updated;
}

async function replaceSelectedMedia(input: PersonaPostActionInput, source: PostSource): Promise<PersonaArchivePost> {
  const archive = await loadPersonaArchive(input.archiveId);
  const post = archive ? findPost(archive, input.postId!, source) : null;
  if (!archive) throw new Error(`persona archive not found: ${input.archiveId}`);
  if (!post) throw new Error(`post not found: ${input.postId}`);
  const mediaUrl = String(input.mediaUrl || "").trim();
  if (!mediaUrl) throw new Error("mediaUrl is required");
  const selected = new Set(input.selectedIndexes || []);
  const currentMedia = storedMediaItems(post);
  if (!selected.size || !currentMedia.some((_, index) => selected.has(index))) throw new Error("selectedIndexes did not match stored media");
  const replacement: StoredMediaItem = {
    url: mediaUrl,
    type: /^(?:data:video\/)|\.(?:mp4|mov|m4v|webm)(?:[?#].*)?$/i.test(mediaUrl) ? "video" : "image",
  };
  const nextMedia = currentMedia.map((item, index) => selected.has(index) ? replacement : item);
  const primary = nextMedia[0];
  if (source === "favorites") {
    const saved = await savePersonaArchive(patchFavoritePost(archive, input.postId!, (current) => ({
      ...current,
      imageUrl: primary.url,
      mediaUrl: primary.url,
      mediaType: primary.type,
      mediaItems: nextMedia,
      sourceMeta: { ...(current.sourceMeta || {}), edited: true, mediaItems: nextMedia },
      updatedAt: new Date().toISOString(),
    })));
    return findPost(saved, input.postId!, source)!;
  }
  const updated = await updatePersonaArchivePostDraft(input.archiveId, input.postId!, {
    imageUrl: primary.url,
    mediaUrl: primary.url,
    mediaType: primary.type,
    mediaItems: nextMedia,
    sourceMetaPatch: { edited: true, mediaItems: nextMedia },
  });
  if (!updated) throw new Error(`post not found: ${input.postId}`);
  return updated;
}

async function runAction(input: PersonaPostActionInput): Promise<PersonaArchivePost | null> {
  const source = inputSource(input);
  const archive = await loadPersonaArchive(input.archiveId);
  if (!archive) throw new Error(`persona archive not found: ${input.archiveId}`);

  if (input.action === "delete_many") {
    const ids = [...new Set(input.postIds || [])];
    const existingIds = new Set(sourcePosts(archive, source).map((post) => post.id));
    if (!ids.some((id) => existingIds.has(id))) throw new Error("no matching posts found");
    if (source === "favorites") {
      const idSet = new Set(ids);
      await savePersonaArchive({
        ...archive,
        favoritePosts: (archive.favoritePosts || []).filter((post) => !idSet.has(post.id)),
      });
    } else {
      await deleteArchiveEpisodes(input.archiveId, ids);
    }
    return null;
  }

  const post = findPost(archive, input.postId!, source);
  if (!post) throw new Error(`post not found: ${input.postId}`);
  if (input.action === "regenerate_content") {
    return regenerateArchivePostContent({
      archiveId: input.archiveId,
      postId: input.postId!,
      source,
      rewriteMode: input.rewriteMode,
    });
  }
  if (input.action === "favorite") {
    const favorites = archive.favoritePosts || [];
    const alreadyFavorite = source !== "favorites" && favorites.some((favorite) =>
      favorite.id === post.id || String((favorite.sourceMeta as any)?.favoriteSourcePostId || "") === post.id
    );
    if (!alreadyFavorite) {
      const now = new Date().toISOString();
      const clone = JSON.parse(JSON.stringify(post)) as PersonaArchivePost;
      await savePersonaArchive({
        ...archive,
        favoritePosts: [...favorites, {
          ...clone,
          id: randomUUID(),
          title: clone.title || `Favorite post #${favorites.length + 1}`,
          orderIndex: favorites.length,
          createdAt: now,
          updatedAt: now,
          publishedAt: undefined,
          publishedMemory: undefined,
          sourceMeta: {
            ...(clone.sourceMeta || {}),
            favoriteSourcePostId: post.id,
            favoriteAddedAt: now,
          } as any,
        }],
      });
    }
    return post;
  }
  if (input.action === "delete") {
    if (source === "favorites") {
      await savePersonaArchive({
        ...archive,
        favoritePosts: (archive.favoritePosts || []).filter((item) => item.id !== input.postId),
      });
    } else {
      await deleteArchiveEpisode(input.archiveId, input.postId!);
    }
    return null;
  }
  if (input.action === "update_content") return saveUpdatedContent(input, source);
  if (input.action === "refresh_metrics") {
    const updated = await refreshStoredPostSentimentMetrics({ archiveId: input.archiveId, postId: input.postId! });
    if (!updated) throw new Error(`post metrics cannot be refreshed: ${input.postId}`);
    return updated;
  }
  if (input.action === "delete_media") return deleteSelectedMedia(input, source);
  if (input.action === "replace_media") return replaceSelectedMedia(input, source);
  throw new Error(`unsupported action: ${input.action}`);
}

async function main() {
  const input = readInput();
  const updatedPost = await runAction(input);
  const source = inputSource(input);
  const archive = await loadPersonaArchive(input.archiveId);
  if (!archive) throw new Error(`persona archive not found after action: ${input.archiveId}`);
  const resultPost = updatedPost || (input.postId ? findPost(archive, input.postId, source) : null);
  printJson({
    ok: true,
    archiveId: input.archiveId,
    postId: input.postId || "",
    action: input.action,
    remaining: sourcePosts(archive, source).length,
    favoriteCount: (archive.favoritePosts || []).length,
    content: resultPost?.content || "",
    mediaUrls: resultPost ? storedMediaItems(resultPost).map((item) => item.url) : [],
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
