import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { loadPersonaArchive } from "@/lib/persona-archives";
import { cleanSentimentCandidateContent, downloadCandidateMedia, fetchSentimentHotCandidates, preheatSentimentHotCandidates } from "@/lib/sentiment-hot-importer";
import {
  rememberSentimentHotImported,
  rememberSentimentHotSelected,
  type SentimentHotCandidate,
} from "@/lib/sentiment-candidate-store";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";
import {
  buildSentimentHotCandidateDetailText,
  appendSentimentHotCandidatePost,
  formatSentimentCookieLine,
  formatSentimentHotCandidateLine,
  formatSentimentMetricLine,
  loadSelectablePersonaMemories,
  type PendingSentimentHotImportState,
} from "@/telegram-bot";

installNodePersonaArchiveBridge();

type Input = {
  action: "fetch" | "preheat" | "import";
  archiveId: string;
  contentBranch?: "nonr18" | "r18";
  limit?: number;
  refresh?: boolean;
  fetchTaskId?: string;
  items?: Array<{
    candidate: SentimentHotCandidate;
    content?: string;
    media?: SentimentHotCandidate["media"];
    overrideMediaUrl?: string;
    overrideMediaType?: "image" | "video" | "unknown";
    edited?: boolean;
    sourceIndex?: number;
  }>;
};

function input(): Input {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  return JSON.parse((raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw).replace(/^\uFEFF/, ""));
}

export async function fetchCandidates(value: Input) {
  const archive = await loadPersonaArchive(value.archiveId);
  if (!archive) throw new Error(`persona archive not found: ${value.archiveId}`);
  const memorySummaries = (await loadSelectablePersonaMemories(archive.id).catch(() => []))
    .map((entry) => String(entry.summary || "").trim())
    .filter(Boolean)
    .slice(0, 8);
  const result = await fetchSentimentHotCandidates({
    archive,
    memorySummaries,
    limit: Math.min(Math.max(Number(value.limit || 10), 1), 10),
    refresh: value.refresh === true,
  });
  const pending: PendingSentimentHotImportState = {
    archiveId: archive.id,
    archiveName: archive.name,
    contentBranch: value.contentBranch,
    candidates: result.candidates,
    selectedIndexes: [],
    keywords: result.keywords,
    cookieStatuses: result.cookieStatuses,
    warnings: result.warnings,
  };
  return {
    ok: true,
    action: "fetch",
    archiveId: archive.id,
    archiveName: archive.name,
    candidates: result.candidates.map((candidate, index) => ({
      ...candidate,
      media: candidate.media.map((item) => ({ ...item })),
      listText: formatSentimentHotCandidateLine(candidate, index),
      detailText: buildSentimentHotCandidateDetailText({ pending, candidate, index }),
      metricLine: formatSentimentMetricLine(candidate),
    })),
    keywords: result.keywords,
    cookieStatuses: result.cookieStatuses,
    cookieLines: result.cookieStatuses.map(formatSentimentCookieLine),
    warnings: result.warnings,
  };
}

export async function preheatCandidates(value: Input) {
  const archive = await loadPersonaArchive(value.archiveId);
  if (!archive) throw new Error(`persona archive not found: ${value.archiveId}`);
  const result = await preheatSentimentHotCandidates({
    archive,
    limit: Math.min(Math.max(Number(value.limit || 10), 1), 10),
    refresh: value.refresh !== false,
  });
  return {
    ...result,
    action: "preheat" as const,
    archiveId: archive.id,
    archiveName: archive.name,
  };
}

export async function importCandidates(value: Input) {
  const archive = await loadPersonaArchive(value.archiveId);
  if (!archive) throw new Error(`persona archive not found: ${value.archiveId}`);
  const items = (value.items || []).slice(0, 10);
  if (!items.length) throw new Error("no sentiment hot candidates selected");
  const pending: PendingSentimentHotImportState = {
    archiveId: archive.id,
    archiveName: archive.name,
    contentBranch: value.contentBranch,
    candidates: items.map((item) => item.candidate),
    selectedIndexes: [],
    keywords: [],
    cookieStatuses: [],
    warnings: [],
  };
  const prefetchedMedia: SentimentHotCandidate["media"][] = new Array(items.length);
  let nextPrefetchIndex = 0;
  await Promise.all(Array.from({ length: Math.min(3, items.length) }, async () => {
    while (nextPrefetchIndex < items.length) {
      const index = nextPrefetchIndex++;
      const item = items[index];
      const sourceMedia = item.media !== undefined ? item.media : item.candidate.media;
      prefetchedMedia[index] = await downloadCandidateMedia({ ...item.candidate, media: sourceMedia }).catch(() => sourceMedia);
    }
  }));
  const posts = [];
  const failures = [];
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const sourceIndex = Number.isInteger(item.sourceIndex) ? Number(item.sourceIndex) : index;
    try {
      const current = await loadPersonaArchive(archive.id);
      const existing = current?.posts.find((post) => post.sourceMeta?.source === "sentiment_hot_import"
        && post.sourceMeta?.platform === item.candidate.platform
        && (item.candidate.sourceUrl
          ? post.sourceMeta?.sourceUrl === item.candidate.sourceUrl
          : post.sourceMeta?.originalContent === cleanSentimentCandidateContent(item.candidate.content)));
      if (existing) {
        posts.push({
          candidateId: item.candidate.id,
          postId: existing.id,
          content: existing.content,
          sourceUrl: item.candidate.sourceUrl,
          platform: item.candidate.platform,
          metricLine: formatSentimentMetricLine(item.candidate),
          mediaUrl: existing.mediaUrl || existing.mediaItems?.[0]?.url || "",
          mediaType: existing.mediaType || existing.mediaItems?.[0]?.type || "",
          mediaItems: existing.mediaItems || [],
          hotScore: item.candidate.hotScore,
          edited: item.edited === true,
          duplicate: true,
        });
        continue;
      }
      const before = new Set(current?.posts.map((post) => post.id) || []);
      const saved = await appendSentimentHotCandidatePost({
        pending,
        candidate: item.candidate,
        index: sourceIndex,
        overrideContent: item.content,
        overrideMediaUrl: item.overrideMediaUrl,
        overrideMediaType: item.overrideMediaType,
        overrideMediaItems: prefetchedMedia[index],
        edited: item.edited === true,
      });
      rememberSentimentHotSelected(archive.id, item.candidate.id);
      rememberSentimentHotImported(archive.id, item.candidate.id);
      const updated = await loadPersonaArchive(archive.id);
      const post = updated?.posts.find((candidate) => !before.has(candidate.id));
      posts.push({
        candidateId: item.candidate.id,
        postId: post?.id || "",
        content: saved.finalContent,
        sourceUrl: item.candidate.sourceUrl,
        platform: item.candidate.platform,
        metricLine: formatSentimentMetricLine(item.candidate),
        mediaUrl: saved.mediaUrl,
        mediaType: saved.mediaType || "",
        mediaItems: saved.mediaItems,
        hotScore: item.candidate.hotScore,
        edited: item.edited === true,
      });
    } catch (error) {
      failures.push({ index: sourceIndex, candidateId: item.candidate.id, error: error instanceof Error ? error.message : String(error) });
    }
  }
  const ok = posts.length > 0;
  return {
    ok,
    ...(!ok ? { error: "all sentiment hot candidates failed to import" } : {}),
    action: "import",
    archiveId: archive.id,
    fetchTaskId: value.fetchTaskId || "",
    importedCount: posts.length,
    failedCount: failures.length,
    posts,
    failures,
  };
}

async function main() {
  const value = input();
  const result = await (value.action === "fetch"
    ? fetchCandidates(value)
    : value.action === "preheat"
      ? preheatCandidates(value)
    : value.action === "import"
      ? importCandidates(value)
      : Promise.reject(new Error("invalid action")));
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (!result.ok) process.exitCode = 1;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch((error) => {
    process.stdout.write(`${JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) })}\n`);
    process.exitCode = 1;
  });
}
