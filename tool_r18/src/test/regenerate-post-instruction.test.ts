import { describe, expect, it } from "vitest";
import { buildRegeneratePostInstruction, isRegeneratedPostTooSimilar } from "@/core/persona/regenerate-post-instruction";

describe("regenerate post instruction", () => {
  it("uses the original post for topic only instead of preserving the old style", () => {
    const instruction = buildRegeneratePostInstruction("今天塔捷通運動，看著大家都在滑 Threads。");

    expect(instruction).toContain("原推文只用于识别主题和信息点");
    expect(instruction).toContain("不得把原推文当作风格模板");
    expect(instruction).toContain("如果推文风格已经恢复初始状态，必须回到通用人设推文规则");
    expect(instruction).toContain("不要复用原文句式、段落节奏、口头禅、表情密度或结尾互动方式");
    expect(instruction).not.toContain("同一语言风格");
  });

  it("detects regenerated content that is still effectively unchanged", () => {
    const original = "今天塔捷通運動，看著大家都在滑 Threads，每個人看起來都有夠焦慮。";

    expect(isRegeneratedPostTooSimilar(
      original,
      "今天塔捷通運動，看著大家都在滑 Threads，每個人看起來都有夠焦慮。",
    )).toBe(true);
    expect(isRegeneratedPostTooSimilar(
      original,
      "早上看到大家討論通勤前的運動習慣，才發現很多人其實不是懶，是生活節奏被平台推著走。",
    )).toBe(false);
  });
});
