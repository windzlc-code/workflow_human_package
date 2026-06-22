import { describe, expect, it } from "vitest";
import { buildSentimentCandidateId } from "@/lib/sentiment-candidate-store";
import {
  buildSentimentHotKeywords,
  candidateMatchesCurrentKeywords,
  cleanSentimentCandidateContent,
  isChineseSentimentCandidate,
  parseThreadsReaderSearchMarkdownCandidates,
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

  it("rejects hot candidates that conflict with the persona topic", () => {
    const medicalKeywords = ["醫療", "医生", "醫院", "黑心医生"];
    const beautyCandidate = {
      id: "beauty-1",
      platform: "threads",
      sourceUrl: "https://www.threads.net/@demo/post/beauty",
      author: "beauty",
      content: "今天穿搭真的被問爆，護膚和美妝都整理好了，女生拍照角度分享給你們。",
      media: [],
      hotScore: 9000,
      metrics: {},
      capturedAt: new Date().toISOString(),
    } as const;
    const medicalCandidate = {
      ...beautyCandidate,
      id: "medical-1",
      sourceUrl: "https://www.threads.net/@demo/post/medical",
      author: "doctor",
      content: "急診醫生分享醫療現場，最近醫院化驗流程和病人等待時間又被討論。",
    } as const;

    expect(candidateMatchesCurrentKeywords(beautyCandidate, medicalKeywords)).toBe(false);
    expect(candidateMatchesCurrentKeywords(medicalCandidate, medicalKeywords)).toBe(true);
  });

  it("does not let weak generic words pass by themselves", () => {
    const keywords = ["醫療", "医生", "分享", "日常"];
    const genericCandidate = {
      id: "generic-1",
      platform: "threads",
      sourceUrl: "https://www.threads.net/@demo/post/generic",
      author: "daily",
      content: "今天日常分享一下最近心情，生活裡的小事也可以很有共鳴。",
      media: [],
      hotScore: 5000,
      metrics: {},
      capturedAt: new Date().toISOString(),
    } as const;

    expect(candidateMatchesCurrentKeywords(genericCandidate, keywords)).toBe(false);
  });

  it("keeps strongly matched candidates for model-level persona judgment", () => {
    const keywords = ["醫療", "醫生", "黑色幽默"];
    const candidate = {
      id: "mixed-1",
      platform: "threads",
      sourceUrl: "https://www.threads.net/@demo/post/mixed",
      author: "daily",
      content: "醫生朋友用黑色幽默吐槽醫療現場，也聊到今天自拍和生活碎片。",
      media: [],
      hotScore: 5000,
      metrics: {},
      capturedAt: new Date().toISOString(),
    } as const;

    expect(candidateMatchesCurrentKeywords(candidate, keywords)).toBe(true);
  });

  it("keeps engagement signals from Threads reader candidates", () => {
    const candidates = parseThreadsReaderSearchMarkdownCandidates({
      query: "醫療",
      keywords: ["醫療", "醫生", "醫院"],
      sourceUrl: "https://www.threads.net/search?q=%E9%86%AB%E7%99%82",
      text: `
Search • Threads

[Demo Doctor](https://www.threads.net/@demo_doctor)
[01/02/2026](https://www.threads.net/@demo_doctor/post/abc123)
醫生分享醫療現場，今天醫院急診真的塞滿人，病人等待和醫療流程都被拿出來討論。
1.2萬
340
88
`,
    });

    expect(candidates.length).toBe(1);
    expect(candidates[0].metrics.raw_engagement_signals).toEqual([12000, 340, 88]);
    expect(candidates[0].engagement?.rawSignals).toEqual([12000, 340, 88]);
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
