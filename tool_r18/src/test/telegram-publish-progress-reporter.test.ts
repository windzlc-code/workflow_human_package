import { describe, expect, it } from "vitest";
import { TelegramPublishProgressReporter } from "@/core/publish/telegram-publish-progress-reporter";

describe("TelegramPublishProgressReporter", () => {
  it("sends one status message, streams progress, and finalizes the same message", async () => {
    const sent: string[] = [];
    const edited: string[] = [];
    const reporter = new TelegramPublishProgressReporter({
      sendMessage: async (_chatId, text) => {
        sent.push(text);
        return { message_id: 42 };
      },
      editMessageText: async (text) => {
        edited.push(text);
        return true;
      },
    }, 1001, { taskId: "task-1783924608060-8ulkex", platform: "threads", padCode: "PAD-1" });

    await reporter.start();
    await reporter.progress("打开 Threads");
    await reporter.finish("done");

    expect(sent).toHaveLength(1);
    expect(sent[0]).toContain("定时发布执行中");
    expect(edited[edited.length - 1]).toContain("定时发布完成");
    expect(edited[edited.length - 1]).toContain("打开 Threads");
  });
});
