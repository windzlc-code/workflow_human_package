import { describe, expect, it } from "vitest";
import { buildSentimentCandidateId } from "@/lib/sentiment-candidate-store";
import { buildSentimentHotKeywords } from "@/lib/sentiment-hot-importer";

describe("sentiment hot importer", () => {
  it("builds persona-specific search keywords", () => {
    const beautyKeywords = buildSentimentHotKeywords({
      archive: {
        id: "beauty",
        name: "Beauty Persona",
        content: "Shares skincare, outfits, and lifestyle notes.",
        setup: { genres: ["skincare", "outfit"], contentTheme: "makeup" },
        posts: [],
      } as any,
    });
    const techKeywords = buildSentimentHotKeywords({
      archive: {
        id: "tech",
        name: "Tech Persona",
        content: "Shares AI tools and productivity workflows.",
        setup: { genres: ["AI", "automation"], contentTheme: "productivity" },
        posts: [],
      } as any,
    });

    expect(beautyKeywords).toContain("skincare");
    expect(techKeywords).toContain("AI");
    expect(beautyKeywords.join("|")).not.toBe(techKeywords.join("|"));
  });

  it("creates stable candidate ids from platform, url, and content", () => {
    const first = buildSentimentCandidateId({
      platform: "threads",
      sourceUrl: "https://www.threads.net/@demo/post/1",
      content: "demo content",
    });
    const second = buildSentimentCandidateId({
      platform: "threads",
      sourceUrl: "https://www.threads.net/@demo/post/1",
      content: "demo content",
    });
    const other = buildSentimentCandidateId({
      platform: "instagram",
      sourceUrl: "https://www.instagram.com/p/demo",
      content: "demo content",
    });

    expect(first).toBe(second);
    expect(first).not.toBe(other);
  });
});
