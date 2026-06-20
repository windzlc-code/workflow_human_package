import { describe, expect, it } from "vitest";
import { buildSentimentCandidateId } from "@/lib/sentiment-candidate-store";
import {
  buildSentimentHotKeywords,
  cleanSentimentCandidateContent,
  isChineseSentimentCandidate,
  parseThreadsSearchTextCandidates,
} from "@/lib/sentiment-hot-importer";

describe("sentiment hot importer", () => {
  it("builds persona-specific search keywords", () => {
    const beautyKeywords = buildSentimentHotKeywords({
      archive: {
        id: "beauty",
        name: "Beauty Persona",
        content: "分享护肤 穿搭 生活日常。",
        setup: { genres: ["护肤", "穿搭"], contentTheme: "生活" },
        posts: [],
      } as any,
    });
    const techKeywords = buildSentimentHotKeywords({
      archive: {
        id: "tech",
        name: "Tech Persona",
        content: "分享AI工具和自动化流程。",
        setup: { genres: ["AI", "自动化"], contentTheme: "效率" },
        posts: [],
      } as any,
    });

    expect(beautyKeywords).toContain("护肤");
    expect(techKeywords).toContain("自动化");
    expect(beautyKeywords.join("|")).not.toBe(techKeywords.join("|"));
  });

  it("turns fictional persona descriptions into searchable Chinese topic keywords", () => {
    const keywords = buildSentimentHotKeywords({
      archive: {
        id: "liwu",
        name: "李无",
        content: "李无是一位邪恶医生，黑暗医疗视频倾向，内容领域聚焦医疗阴谋、邪恶实验与黑色幽默故事。",
        setup: { genres: ["医疗黑暗", "邪恶医生故事"], contentTheme: "医院反派故事" },
        posts: [],
      } as any,
    });

    expect(keywords).not.toContain("李无");
    expect(keywords).toEqual(expect.arrayContaining(["醫療", "医疗", "醫生", "医生"]));
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

  it("cleans social search breadcrumbs from candidate content", () => {
    const cleaned = cleanSentimentCandidateContent(
      "www.threads.net › t › CuiVm72yO3g Threads ... Threads palantir vulnerability canonical site:threads.net 相關 廣告 www.ups.com/Luxury_Goods/Shipping",
    );

    expect(cleaned).not.toContain("www.threads.net");
    expect(cleaned).not.toContain("›");
    expect(cleaned).not.toContain("廣告");
    expect(cleaned).not.toContain("site:threads.net");
    expect(cleaned).not.toContain("CuiVm72yO3g");
    expect(cleaned).toContain("palantir vulnerability canonical");
  });

  it("keeps only Chinese sentiment copy candidates", () => {
    expect(isChineseSentimentCandidate("公路車的世界裡有兩種人是最強的，邊騎邊自拍的人真的很厲害。")).toBe(true);
    expect(isChineseSentimentCandidate("palantir vulnerability 原文")).toBe(false);
    expect(isChineseSentimentCandidate("gpt 爆料")).toBe(false);
  });

  it("parses Traditional Chinese Threads search page text as fallback candidates", () => {
    const candidates = parseThreadsSearchTextCandidates({
      query: "醫療",
      keywords: ["醫療", "醫生", "醫院"],
      sourceUrl: "https://www.threads.com/search?q=%E9%86%AB%E7%99%82",
      text: `
醫療
mls_muttering
醫療化驗
2天
[93]
有冇人知醫療化驗報告要等幾耐，最近身體狀況有點奇怪，想知道診所流程係點。
翻譯
4
5
bunundoc
2026-3-2
我走到病人床邊。你好，我是急診醫師，今天醫院真的塞滿人，醫療現場比想像中更混亂。
翻譯
3.5 萬
330
`,
    });

    expect(candidates.length).toBeGreaterThanOrEqual(2);
    expect(candidates[0].platform).toBe("threads");
    expect(candidates[0].content).toContain("醫療");
    expect(candidates[0].content).not.toContain("翻譯");
    expect(candidates[0].sourceUrl).toContain("threads.com/search");
  });
});
