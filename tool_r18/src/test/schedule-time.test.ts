import { beforeEach, describe, expect, it, vi } from "vitest";
import { addScheduleCalendarDays, createScheduledDate, formatScheduledDate, getScheduleTimeParts, parseScheduledDate } from "@/core/publish/schedule-time";
import { runPersonaWorkflow, stripInactiveLinkEndings } from "@/core/persona/persona-workflow-service";

const queueMocks = vi.hoisted(() => ({
  enqueueTask: vi.fn(),
  listTasks: vi.fn(),
}));

const archiveMocks = vi.hoisted(() => ({
  loadPersonaArchive: vi.fn(),
  getArchivePendingPostsForPlatform: vi.fn(),
}));

vi.mock("@/runtime/node/publish-queue-repository", () => ({
  createNodePublishQueueRepository: () => queueMocks,
}));

vi.mock("@/lib/persona-archives", () => ({
  createPersonaArchive: vi.fn(),
  listPersonaArchives: vi.fn(),
  loadPersonaArchive: archiveMocks.loadPersonaArchive,
  appendEpisodesToArchive: vi.fn(),
  updatePersonaArchiveProfile: vi.fn(),
  deletePersonaArchive: vi.fn(),
  getArchivePendingPostsForPlatform: archiveMocks.getArchivePendingPostsForPlatform,
  markArchiveEpisodesPublished: vi.fn(),
}));

beforeEach(() => {
  queueMocks.enqueueTask.mockReset();
  queueMocks.listTasks.mockReset();
  archiveMocks.loadPersonaArchive.mockReset();
  archiveMocks.getArchivePendingPostsForPlatform.mockReset();
});

describe("schedule time", () => {
  it("stores and formats China local scheduled time independently of the daemon timezone", () => {
    const date = createScheduledDate({ year: 2026, month: 7, day: 13, hour: 14, minute: 40 }, "Asia/Shanghai");

    expect(date.toISOString()).toBe("2026-07-13T06:40:00.000Z");
    expect(formatScheduledDate(date, "Asia/Shanghai")).toBe("2026-07-13 14:40");
  });

  it("moves a date by calendar days without using the daemon local timezone", () => {
    expect(addScheduleCalendarDays({ year: 2026, month: 7, day: 13 }, 1)).toEqual({ year: 2026, month: 7, day: 14 });
    expect(getScheduleTimeParts("2026-07-13T06:40:00.000Z", "Asia/Shanghai")).toMatchObject({ hour: 14, minute: 40 });
  });

  it("treats timezone-less API and script input as Shanghai time", () => {
    expect(parseScheduledDate("2026-07-13 14:40").toISOString()).toBe("2026-07-13T06:40:00.000Z");
    expect(parseScheduledDate("2026-07-13 14:40:59").toISOString()).toBe("2026-07-13T06:40:59.000Z");
    expect(parseScheduledDate("2026-07-13T06:40:00.000Z").toISOString()).toBe("2026-07-13T06:40:00.000Z");
  });

  it.each([
    "2026-13-13 14:40",
    "2026-02-29 14:40",
    "2026-07-13 24:00",
    "2026-07-13 14:60",
    "2026-07-13 14:40:60",
    "2026-02-30T06:40:00.000Z",
  ])("rejects invalid schedule components in %s", (value) => {
    expect(() => parseScheduledDate(value)).toThrow("Invalid scheduled time");
  });

  it("rejects invalid wall-clock parts passed directly", () => {
    expect(() => createScheduledDate({ year: 2026, month: 2, day: 29, hour: 14, minute: 40 })).toThrow("Invalid scheduled time");
  });
});

describe("inactive link ending cleanup", () => {
  const setup = {
    activeLinkEndingPresetId: "active",
    linkEndingPresets: [
      { id: "active", endingText: "Active ending", linkUrl: "https://active.example/post", enabled: true },
      { id: "inactive", endingText: "Disabled ending", linkUrl: "https://inactive.example/post", enabled: false },
    ],
  };

  it("only strips complete inactive fragments from the end of post content", () => {
    const content = [
      "Disabled ending remains when quoted in the body.",
      "Reference: https://inactive.example/post remains in the body.",
      "Disabled ending",
      "https://inactive.example/post",
    ].join("\n");

    expect(stripInactiveLinkEndings(content, setup)).toBe([
      "Disabled ending remains when quoted in the body.",
      "Reference: https://inactive.example/post remains in the body.",
    ].join("\n"));
  });

  it("does not strip partial or inline trailing matches", () => {
    expect(stripInactiveLinkEndings("Body text with Disabled ending", setup)).toBe("Body text with Disabled ending");
    expect(stripInactiveLinkEndings("Body\nhttps://inactive.example/post/details", setup)).toBe("Body\nhttps://inactive.example/post/details");
  });
});

describe("persona enqueue task input", () => {
  it("passes scheduling and idempotency fields to the queue repository", async () => {
    archiveMocks.loadPersonaArchive.mockResolvedValue({ id: "archive-1", setup: {} });
    archiveMocks.getArchivePendingPostsForPlatform.mockReturnValue([{ id: "post-1", content: "Post body" }]);
    queueMocks.listTasks.mockReturnValue([]);
    queueMocks.enqueueTask.mockReturnValue({ id: "task-1" });

    await runPersonaWorkflow({
      action: "enqueue-posts",
      archiveId: "archive-1",
      postIds: ["post-1"],
      platform: "threads",
      padCode: "PAD-1",
      scheduledAt: "2026-07-13T06:40:00.000Z",
      idempotencyKey: "request-1",
    });

    expect(queueMocks.enqueueTask).toHaveBeenCalledWith(expect.objectContaining({
      scheduled_at: "2026-07-13T06:40:00.000Z",
      idempotency_key: "request-1:post-1:threads:PAD-1",
    }));
  });

  it("uses a distinct idempotency key for every post in one request", async () => {
    archiveMocks.loadPersonaArchive.mockResolvedValue({ id: "archive-1", setup: {} });
    archiveMocks.getArchivePendingPostsForPlatform.mockReturnValue([
      { id: "post-1", content: "First post" },
      { id: "post-2", content: "Second post" },
    ]);
    queueMocks.listTasks.mockReturnValue([]);
    queueMocks.enqueueTask
      .mockReturnValueOnce({ id: "task-1" })
      .mockReturnValueOnce({ id: "task-2" });

    await runPersonaWorkflow({
      action: "enqueue-posts",
      archiveId: "archive-1",
      postIds: ["post-1", "post-2"],
      platform: "threads",
      padCode: "PAD-1",
      scheduledAt: "2026-07-13T06:40:00.000Z",
      idempotencyKey: "request-2",
    });

    expect(queueMocks.enqueueTask.mock.calls.map(([input]) => input.idempotency_key)).toEqual([
      "request-2:post-1:threads:PAD-1",
      "request-2:post-2:threads:PAD-1",
    ]);
  });
});
