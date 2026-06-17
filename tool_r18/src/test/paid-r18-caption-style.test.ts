import { describe, expect, it } from "vitest";

import { buildPersonaPaidCaptionToneGuide, isMechanicalPaidCaption } from "@/lib/paid-r18-caption-style";

describe("paid-r18-caption-style", () => {
  it("adds colloquial paid-caption guidance only for 金君雅", () => {
    const guide = buildPersonaPaidCaptionToneGuide({
      personaName: "金君雅",
      freePostTemplate: "jinjunya-hook",
      tweetStyleLinkUrl: "https://t.me/gy_night_flight_bot",
    });

    expect(guide.join("\n")).toContain("台灣口語");
    expect(guide.join("\n")).toContain("像真人偷傳一句");
    expect(guide.join("\n")).toContain("不要像鏡頭描述");
  });

  it("does not add paid-caption guidance for other personas", () => {
    const guide = buildPersonaPaidCaptionToneGuide({
      personaName: "瑜伽老師",
      personaStyle: "繁體中文、溫柔、自律",
    });

    expect(guide).toEqual([]);
  });

  it("detects dry anatomical list captions as mechanical", () => {
    expect(isMechanicalPaidCaption("米白睡袍完全敞開，豐滿乳房和暗褐乳頭全露出\nhttps://x.test")).toBe(true);
  });

  it("keeps colloquial reactions from being flagged as mechanical", () => {
    expect(isMechanicalPaidCaption("這件睡袍一鬆開，胸口真的有點太犯規\nhttps://x.test")).toBe(false);
  });
});
