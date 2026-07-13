import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { createNodePublishQueueRepository } from "@/runtime/node/publish-queue-repository";

describe("publish queue Bot origin", () => {
  it("persists the source Bot key with a scheduled task", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "publish-bot-origin-"));
    const repo = createNodePublishQueueRepository(path.join(dir, "publish_queue.db"));
    const task = repo.enqueueTask({
      pad_code: "PAD-1",
      platform: "threads",
      caption: "scheduled post",
      telegram_chat_id: "1001",
      telegram_bot_key: "bot-key-123",
    });

    expect(repo.getTask(task.id)).toMatchObject({
      telegram_chat_id: "1001",
      telegram_bot_key: "bot-key-123",
    });
  });
});
