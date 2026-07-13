import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("daemon sentiment preheat defaults", () => {
  it("runs automatically at most once per day and does not restart after one minute", () => {
    const source = fs.readFileSync(path.resolve(process.cwd(), "src/daemon.ts"), "utf8");

    expect(source).toContain("SENTIMENT_HOT_PREHEAT_INTERVAL_MS || 24 * 60 * 60 * 1000");
    expect(source).toContain("SENTIMENT_HOT_PREHEAT_INITIAL_DELAY_MS || SENTIMENT_HOT_PREHEAT_INTERVAL_MS");
  });

  it("runs scheduled preheat in a child process instead of the Telegram event loop", () => {
    const source = fs.readFileSync(path.resolve(process.cwd(), "src/daemon.ts"), "utf8");

    expect(source).toContain('scripts/skills/persona-sentiment-hot-once.ts');
    expect(source).toContain('action: "preheat"');
    expect(source).toContain("runSentimentHotPreheatChild(archive.id)");
    expect(source).not.toContain("await preheatSentimentHotCandidates(");
  });
});
