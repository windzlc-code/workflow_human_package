import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  appendSentimentHotCandidatePost: vi.fn(),
  fetchSentimentHotCandidates: vi.fn(),
  loadPersonaArchive: vi.fn(),
  rememberSentimentHotImported: vi.fn(),
  rememberSentimentHotSelected: vi.fn(),
}));

vi.mock("@/runtime/node/browser-shim", () => ({}));
vi.mock("@/runtime/node/persona-archive-store", () => ({
  installNodePersonaArchiveBridge: vi.fn(),
}));
vi.mock("@/lib/persona-archives", () => ({
  loadPersonaArchive: mocks.loadPersonaArchive,
}));
vi.mock("@/lib/sentiment-hot-importer", () => ({
  cleanSentimentCandidateContent: (value: string) => String(value || "").trim(),
  fetchSentimentHotCandidates: mocks.fetchSentimentHotCandidates,
}));
vi.mock("@/lib/sentiment-candidate-store", () => ({
  rememberSentimentHotImported: mocks.rememberSentimentHotImported,
  rememberSentimentHotSelected: mocks.rememberSentimentHotSelected,
}));
vi.mock("@/telegram-bot", () => ({
  appendSentimentHotCandidatePost: mocks.appendSentimentHotCandidatePost,
  buildSentimentHotCandidateDetailText: vi.fn(() => "detail"),
  formatSentimentCookieLine: vi.fn(() => "cookie"),
  formatSentimentHotCandidateLine: vi.fn(() => "list"),
  formatSentimentMetricLine: vi.fn(() => "metric"),
  loadSelectablePersonaMemories: vi.fn(async () => []),
}));

import { fetchCandidates, importCandidates } from "../../scripts/skills/persona-sentiment-hot-once";

const archive = {
  id: "archive-hot",
  name: "Hot Persona",
  posts: [],
};

function candidate(overrides: Record<string, unknown> = {}) {
  return {
    id: "candidate-1",
    platform: "threads",
    sourceUrl: "https://www.threads.net/@author/post/1",
    author: "author",
    content: "hot content",
    media: [],
    hotScore: 12000,
    metrics: { view_count: 12000 },
    capturedAt: "2026-07-10T00:00:00Z",
    ...overrides,
  };
}

describe("persona sentiment hot one-shot adapter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loadPersonaArchive.mockResolvedValue(archive);
  });

  it("keeps both the original media URL and usable local path in the fetch snapshot", async () => {
    const media = {
      type: "image",
      url: "sentiment-assets/source.jpg",
      localPath: "E:\\runtime\\sentiment-assets\\source.jpg",
      warning: "source fallback",
    };
    mocks.fetchSentimentHotCandidates.mockResolvedValue({
      candidates: [candidate({ media: [media] })],
      keywords: ["keyword"],
      cookieStatuses: [],
      warnings: [],
    });

    const result = await fetchCandidates({ action: "fetch", archiveId: archive.id });

    expect(result.candidates[0].media).toEqual([media]);
    expect(result.candidates[0].media[0]).not.toBe(media);
  });

  it("returns failure semantics and reports the original sourceIndex when every import fails", async () => {
    const selected = candidate();
    mocks.appendSentimentHotCandidatePost.mockRejectedValue(new Error("archive write failed"));

    const result = await importCandidates({
      action: "import",
      archiveId: archive.id,
      fetchTaskId: "fetch-task-1",
      items: [{ candidate: selected, sourceIndex: 7 }],
    });

    expect(mocks.appendSentimentHotCandidatePost).toHaveBeenCalledWith(expect.objectContaining({
      candidate: selected,
      index: 7,
    }));
    expect(result).toMatchObject({
      ok: false,
      error: "all sentiment hot candidates failed to import",
      importedCount: 0,
      failedCount: 1,
      failures: [{ index: 7, candidateId: "candidate-1", error: "archive write failed" }],
    });
    expect(mocks.rememberSentimentHotImported).not.toHaveBeenCalled();
    expect(mocks.rememberSentimentHotSelected).not.toHaveBeenCalled();
  });

  it("passes uploaded replacement media through the existing Telegram append function", async () => {
    const selected = candidate();
    mocks.appendSentimentHotCandidatePost.mockResolvedValue({ finalContent: "hot content" });
    mocks.loadPersonaArchive
      .mockResolvedValueOnce(archive)
      .mockResolvedValueOnce(archive)
      .mockResolvedValueOnce({ ...archive, posts: [{ id: "post-1", content: "hot content" }] });

    const result = await importCandidates({
      action: "import",
      archiveId: archive.id,
      items: [{
        candidate: selected,
        sourceIndex: 2,
        overrideMediaUrl: "C:\\runtime\\replacement.png",
        overrideMediaType: "image",
        edited: true,
      }],
    });

    expect(mocks.appendSentimentHotCandidatePost).toHaveBeenCalledWith(expect.objectContaining({
      candidate: selected,
      index: 2,
      overrideMediaUrl: "C:\\runtime\\replacement.png",
      overrideMediaType: "image",
      edited: true,
    }));
    expect(result.ok).toBe(true);
  });

  it("does not terminate the shared sentiment runtime when the one-shot process finishes", () => {
    const scriptPath = path.resolve(process.cwd(), "scripts/skills/persona-sentiment-hot-once.ts");
    const source = fs.readFileSync(scriptPath, "utf8");

    expect(source).not.toContain("stopSentimentRuntime");
    expect(source).not.toContain("sentiment-runtime-manager");
  });

  it("allows a cold-started shared sentiment backend to outlive the one-shot caller", () => {
    const managerPath = path.resolve(process.cwd(), "src/lib/sentiment-runtime-manager.ts");
    const source = fs.readFileSync(managerPath, "utf8");

    expect(source).toContain("child.unref()");
    expect(source).toContain('detached: process.platform !== "win32"');
  });
});
