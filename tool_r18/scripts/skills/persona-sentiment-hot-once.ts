import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { loadPersonaArchive } from "@/lib/persona-archives";
import { cleanSentimentCandidateContent, fetchSentimentHotCandidates } from "@/lib/sentiment-hot-importer";
import {
  rememberSentimentHotImported,
  rememberSentimentHotSelected,
  type SentimentHotCandidate,
} from "@/lib/sentiment-candidate-store";
import { stopSentimentRuntime } from "@/lib/sentiment-runtime-manager";
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
  action: "fetch" | "import";
  archiveId: string;
  contentBranch?: "nonr18" | "r18";
  limit?: number;
  refresh?: boolean;
  fetchTaskId?: string;
  items?: Array<{
    candidate: SentimentHotCandidate;
    content?: string;
    media?: SentimentHotCandidate["media"];
    edited?: boolean;
    sourceIndex?: number;
  }>;
};

function input(): Input {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  return JSON.parse((raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw).replace(/^\uFEFF/, ""));
}

async function fetchCandidates(value: Input) {
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
      media: candidate.media.map(({ type, url, warning }) => ({ type, url, ...(warning ? { warning } : {}) })),
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

async function importCandidates(value: Input) {
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
  const posts = [];
  const failures = [];
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    try {
      const current = await loadPersonaArchive(archive.id);
      const existing = current?.posts.find((post) => post.sourceMeta?.source === "sentiment_hot_import"
        && post.sourceMeta?.platform === item.candidate.platform
        && (item.candidate.sourceUrl
          ? post.sourceMeta?.sourceUrl === item.candidate.sourceUrl
          : post.sourceMeta?.originalContent === cleanSentimentCandidateContent(item.candidate.content)));
      if (existing) {
        posts.push({ candidateId: item.candidate.id, postId: existing.id, content: existing.content, sourceUrl: item.candidate.sourceUrl, duplicate: true });
        continue;
      }
      const before = new Set(current?.posts.map((post) => post.id) || []);
      rememberSentimentHotSelected(archive.id, item.candidate.id);
      const saved = await appendSentimentHotCandidatePost({
        pending,
        candidate: item.candidate,
        index: Number.isInteger(item.sourceIndex) ? Number(item.sourceIndex) : index,
        overrideContent: item.content,
        overrideMediaItems: item.media,
        edited: item.edited === true,
      });
      rememberSentimentHotImported(archive.id, item.candidate.id);
      const updated = await loadPersonaArchive(archive.id);
      const post = updated?.posts.find((candidate) => !before.has(candidate.id));
      posts.push({ candidateId: item.candidate.id, postId: post?.id || "", content: saved.finalContent, sourceUrl: item.candidate.sourceUrl });
    } catch (error) {
      failures.push({ index, candidateId: item.candidate.id, error: error instanceof Error ? error.message : String(error) });
    }
  }
  return {
    ok: true,
    action: "import",
    archiveId: archive.id,
    fetchTaskId: value.fetchTaskId || "",
    importedCount: posts.length,
    failedCount: failures.length,
    posts,
    failures,
  };
}

const value = input();
(value.action === "fetch" ? fetchCandidates(value) : value.action === "import" ? importCandidates(value) : Promise.reject(new Error("invalid action")))
  .then((result) => process.stdout.write(`${JSON.stringify(result)}\n`))
  .catch((error) => {
    process.stdout.write(`${JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) })}\n`);
    process.exitCode = 1;
  })
  .finally(() => stopSentimentRuntime());
