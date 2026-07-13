import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let runtimeDir = "";

describe("sentiment hot execution lock", () => {
  beforeEach(() => {
    runtimeDir = fs.mkdtempSync(path.join(os.tmpdir(), "sentiment-hot-lock-"));
    process.env.TOOL_R18_RUNTIME_DIR = runtimeDir;
    process.env.SENTIMENT_HOT_LOCK_WAIT_MS = "2000";
    process.env.SENTIMENT_HOT_LOCK_POLL_MS = "10";
    vi.resetModules();
  });

  afterEach(() => {
    delete process.env.TOOL_R18_RUNTIME_DIR;
    delete process.env.SENTIMENT_HOT_LOCK_WAIT_MS;
    delete process.env.SENTIMENT_HOT_LOCK_POLL_MS;
    fs.rmSync(runtimeDir, { recursive: true, force: true });
  });

  it("serializes concurrent hotspot work without blocking timers", async () => {
    const { withSentimentHotExecutionLock } = await import("@/lib/sentiment-hot-execution-lock");
    let active = 0;
    let maxActive = 0;
    let timerTicks = 0;
    const timer = setInterval(() => { timerTicks += 1; }, 5);
    const run = (owner: string) => withSentimentHotExecutionLock(owner, async () => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => setTimeout(resolve, 60));
      active -= 1;
    });

    await Promise.all([run("first"), run("second")]);
    clearInterval(timer);

    expect(maxActive).toBe(1);
    expect(timerTicks).toBeGreaterThan(5);
    expect(fs.existsSync(path.join(runtimeDir, "sentiment-hot-execution.lock"))).toBe(false);
  });
});
