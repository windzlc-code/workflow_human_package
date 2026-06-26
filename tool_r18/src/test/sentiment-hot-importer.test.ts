import { afterEach, describe, expect, it, vi } from "vitest";
import { buildSentimentCandidateId } from "@/lib/sentiment-candidate-store";
import {
  analyzeThreadsProfileVisibleSignals,
  buildSentimentHotKeywords,
  candidateMatchesCurrentKeywords,
  cleanSentimentCandidateContent,
  isChineseSentimentCandidate,
  parseInstagramReaderSearchMarkdownCandidates,
  matchThreadsBrowserProfilePublishedPost,
  parseThreadsBrowserPostDetailMetrics,
  parseThreadsBrowserProfilePublishedPosts,
  parseThreadsGraphqlProfilePagePayload,
  parseThreadsPostViewCountFromText,
  parseThreadsReaderSearchMarkdownCandidates,
  parseThreadsDetailEngagementMarkdown,
  parseThreadsDetailMediaMarkdown,
  parseThreadsSearchTextCandidates,
  refreshSentimentSourceMetrics,
  shouldTreatThreadsProfileAsLoginWall,
} from "@/lib/sentiment-hot-importer";

afterEach(() => {
  vi.unstubAllGlobals();
});

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
    expect(keywords).toEqual(expect.arrayContaining(["医疗黑暗", "邪恶医生故事", "邪恶实验"]));
    expect(keywords).not.toContain("醫療");
    expect(keywords).not.toContain("医生");
  });

  it("does not promote visual field labels or negated topics into hot search keywords", () => {
    const keywords = buildSentimentHotKeywords({
      archive: {
        id: "ken",
        name: "Ken 海外工薪金融干货",
        content: "面向海外工薪族，分享海外金融、工薪信貸、理財規劃、信用卡和贷款。不做美食娛樂。",
        setup: {
          genres: ["AI", "人工智慧", "自動化", "職場", "海外金融", "工薪信貸", "理財規劃"],
          contentTheme: "內容主題和圖片視覺傾向",
          personality: "理性務實",
        },
        posts: [],
      } as any,
    });

    expect(keywords).toEqual(expect.arrayContaining(["海外金融", "工薪信貸", "理財規劃"]));
    expect(keywords).not.toContain("內容主題");
    expect(keywords).not.toContain("圖片視覺傾向");
    expect(keywords).not.toContain("視覺傾向");
    expect(keywords).not.toContain("理性務實");
    expect(keywords).not.toContain("美食");
  });

  it("does not directly use interest tags as fallback hot search keywords", () => {
    const keywords = buildSentimentHotKeywords({
      archive: {
        id: "interest-drift",
        name: "海外工薪理財號",
        content: "面向海外工薪族，專注工薪信貸、信用卡、貸款和理財規劃。",
        setup: {
          interests: ["美食", "旅行"],
          genres: ["海外金融"],
          personaType: "海外工薪金融干貨",
        },
        posts: [],
      } as any,
    });

    expect(keywords).toEqual(expect.arrayContaining(["工薪信貸", "信用卡", "貸款", "理財規劃"]));
    expect(keywords).not.toContain("美食");
    expect(keywords).not.toContain("旅行");
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

  it("parses Instagram reader candidates as extra sentiment sources", () => {
    const candidates = parseInstagramReaderSearchMarkdownCandidates({
      query: "醫療",
      keywords: ["醫療", "醫生", "醫院"],
      sourceUrl: "https://www.instagram.com/explore/search/keyword/?q=%E9%86%AB%E7%99%82",
      text: `
Title: Instagram

[Demo Doctor](https://www.instagram.com/demo_doctor/)
[View post](https://www.instagram.com/p/abc123/)
急診醫生分享醫療現場，今天醫院等候區真的塞滿人，病人等待和醫療流程都被拿出來討論。
1.1K likes
82 comments
![Image 1](https://cdn.example.com/ig-a.jpg)
`,
    });

    expect(candidates.length).toBe(1);
    expect(candidates[0].platform).toBe("instagram");
    expect(candidates[0].sourceUrl).toBe("https://www.instagram.com/p/abc123/");
    expect(candidates[0].engagement?.likeCount).toBe(1100);
    expect(candidates[0].engagement?.commentCount).toBe(82);
    expect(candidates[0].media.map((item) => item.url)).toEqual(["https://cdn.example.com/ig-a.jpg"]);
  });

  it("parses Threads detail metrics from reader markdown", () => {
    const engagement = parseThreadsDetailEngagementMarkdown(`
Title: Demo on Threads

# [Thread 978K views](https://www.threads.net/@demo/post/abc)

Demo post body

31.9K

355

713

5.6K
`);

    expect(engagement.viewCount).toBe(978000);
    expect(engagement.likeCount).toBe(31900);
    expect(engagement.commentCount).toBeUndefined();
    expect(engagement.shareCount).toBeUndefined();
    expect(engagement.rawSignals).toEqual([31900, 355, 713, 5600]);
  });

  it("does not treat unlabeled Threads detail numbers as comments or reposts", () => {
    const engagement = parseThreadsDetailEngagementMarkdown(`
Title: Demo on Threads

# [Thread 269 views](https://www.threads.net/@demo/post/abc)

Demo post body

2

291

88

6
`);

    expect(engagement.viewCount).toBe(269);
    expect(engagement.likeCount).toBe(2);
    expect(engagement.commentCount).toBeUndefined();
    expect(engagement.shareCount).toBeUndefined();
    expect(engagement.rawSignals).toEqual([2, 291, 88, 6]);
  });

  it("matches old published Threads posts from the logged-in profile page", () => {
    const text = `
stevie875443
1天
2 足球運動與金融投資理財
翻譯
291
54
88
13
stevie875443
1天
你心目中一生必看的 動漫 神作？
翻譯
209
56
1
13
`;
    const links = [
      "https://www.threads.com/@stevie875443/post/DZ6zcSaErFb",
      "https://www.threads.com/@stevie875443/post/DZ6gGNAEqjT",
    ];

    const posts = parseThreadsBrowserProfilePublishedPosts({ username: "stevie875443", text, links });
    expect(posts.map((post) => post.sourceUrl)).toEqual([
      "https://www.threads.net/@stevie875443/post/DZ6zcSaErFb",
      "https://www.threads.net/@stevie875443/post/DZ6gGNAEqjT",
    ]);

    const matched = matchThreadsBrowserProfilePublishedPost({
      username: "stevie875443",
      text,
      links,
      content: "你心目中一生必看的 動漫 神作？",
    });
    expect(matched?.sourceUrl).toBe("https://www.threads.net/@stevie875443/post/DZ6gGNAEqjT");
    expect(matched?.engagement).toMatchObject({
      likeCount: 209,
      commentCount: 56,
      shareCount: 1,
      rawSignals: [209, 56, 1, 13],
    });
    expect(matched?.metrics).toMatchObject({
      like_count: 209,
      comment_count: 56,
      share_count: 1,
      send_count: 13,
    });
  });

  it("uses labeled Threads post detail buttons instead of guessing unlabeled numbers", () => {
    const detail = parseThreadsBrowserPostDetailMetrics({
      text: `
Log in
Thread
274 views
stevie875443
1d
2 足球運動與金融投資理財
Translate
2
`,
      actionTexts: ["Like", "Comment2", "Repost", "Share"],
    });

    expect(detail?.engagement).toMatchObject({
      likeCount: 0,
      commentCount: 2,
      shareCount: 0,
    });
    expect(detail?.metrics).toMatchObject({
      like_count: 0,
      comment_count: 2,
      share_count: 0,
      repost_count: 0,
      send_count: 0,
      view_count: 274,
    });
    expect(detail?.hotScore).toBe(274);
  });

  it("overwrites existing named metrics when refreshing a stored Threads source", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      text: async () => `
Title: Demo on Threads

# [Thread 250 views](https://www.threads.net/@demo/post/abc)

Demo post body

20

5

3

88
`,
    }));
    vi.stubGlobal("fetch", fetchMock);

    const refreshed = await refreshSentimentSourceMetrics({
      platform: "threads",
      sourceUrl: "https://www.threads.net/@demo/post/abc",
      existingHotScore: 100,
      existingEngagement: {
        viewCount: 100,
        likeCount: 10,
        commentCount: 1,
        shareCount: 1,
        rawSignals: [100, 10, 1],
      },
    });

    expect(fetchMock).toHaveBeenCalled();
    expect(refreshed.ok, JSON.stringify(refreshed)).toBe(true);
    expect(refreshed.engagement?.viewCount).toBe(250);
    expect(refreshed.engagement?.likeCount).toBe(20);
    expect(refreshed.engagement?.commentCount).toBeUndefined();
    expect(refreshed.engagement?.shareCount).toBeUndefined();
    expect(refreshed.metrics).toMatchObject({
      view_count: 250,
      like_count: 20,
      comment_count: 0,
      share_count: 0,
      repost_count: 0,
      send_count: 0,
    });
    expect(refreshed.hotScore).toBe(250);
  });

  it("keeps only top-level media files from Threads detail markdown", () => {
    const media = parseThreadsDetailMediaMarkdown(`
![Image 1: demo profile picture](https://cdn.example.com/profile_pic.jpg)
![Image 2](https://cdn.example.com/a.jpg)
![Image 3](https://cdn.example.com/b.webp)
![Image 4](https://cdn.example.com/c.jpg)
[![Image 5: reply_user's profile picture](https://cdn.example.com/reply-s150x150.jpg)](https://www.threads.net/@reply)
![Image 6](https://cdn.example.com/reply-body.jpg)
Log in to see more replies.
`);

    expect(media.map((item) => item.url)).toEqual([
      "https://cdn.example.com/a.jpg",
      "https://cdn.example.com/b.webp",
      "https://cdn.example.com/c.jpg",
    ]);
  });

  it("drops Threads link preview media from detail markdown", () => {
    const media = parseThreadsDetailMediaMarkdown(`
![Image 1](https://scontent-sea5-1.cdninstagram.com/v/t51.82787-15/post.jpg)
![Image 2](https://external-sea5-1.xx.fbcdn.net/emg1/v/t13/preview?url=https%3A%2F%2Fexample.com%2Fcover.jpg)
![Image 3](https://www.youtube.com/s/desktop/favicon_144x144.png)
`);

    expect(media.map((item) => item.url)).toEqual([
      "https://scontent-sea5-1.cdninstagram.com/v/t51.82787-15/post.jpg",
    ]);
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

  it("keeps visible Threads profile metrics from the page body", () => {
    const visible = analyzeThreadsProfileVisibleSignals({
      username: "stevie875443",
      bodyText: `
阿牛投資理財|挑戰10年財務自由
4 位粉絲
12 追蹤中
6.1 萬次最近瀏覽次數
      `,
      buttonText: ["追蹤", "分享"],
      links: [],
    });

    expect(visible.parsed.followers).toBe(4);
    expect(visible.parsed.following).toBe(12);
    expect(visible.parsed.recentViews).toBe(61000);
    expect(visible.hasUsableProfileSignals).toBe(true);
  });

  it("does not double-count duplicated Threads profile recent views", () => {
    const visible = analyzeThreadsProfileVisibleSignals({
      username: "stevie875443",
      bodyText: `
阿牛投資理財|挑戰10年財務自由
4 位粉絲
6.1 萬次最近瀏覽次數
Instagram
4位粉絲
6.1 萬次最近瀏覽次數
      `,
      buttonText: [],
      links: [],
    });

    expect(visible.parsed.recentViews).toBe(61000);
    expect(visible.parsed.views).toBeUndefined();
  });

  it("parses paginated Threads profile GraphQL payload into real post metrics", () => {
    const parsed = parseThreadsGraphqlProfilePagePayload({
      username: "stevie875443",
      payload: {
        data: {
          mediaData: {
            edges: [
              {
                node: {
                  thread_items: [{
                    post: {
                      pk: "3925594288747063183",
                      code: "DZ1ABCxyz",
                      canonical_url: "https://www.threads.com/@stevie875443/post/DZ1ABCxyz",
                      like_count: 954,
                      text_post_app_info: {
                        direct_reply_count: 68,
                        repost_count: 92,
                        reshare_count: 58,
                      },
                    },
                  }],
                },
              },
            ],
            page_info: {
              end_cursor: "cursor-1",
              has_next_page: true,
            },
          },
        },
      },
    });

    expect(parsed.posts).toEqual([{
      pk: "3925594288747063183",
      code: "DZ1ABCxyz",
      sourceUrl: "https://www.threads.com/@stevie875443/post/DZ1ABCxyz",
      likeCount: 954,
      commentCount: 68,
      repostCount: 92,
      shareCount: 58,
    }]);
    expect(parsed.endCursor).toBe("cursor-1");
    expect(parsed.hasNextPage).toBe(true);
  });

  it("parses Threads post view counts directly from the detail page text", () => {
    expect(parseThreadsPostViewCountFromText(`
串文
84次瀏覽
stevie875443
2天
超好笑到底誰寫的www
    `)).toBe(84);

    expect(parseThreadsPostViewCountFromText(`
Thread
6.1萬 views
    `)).toBe(61000);
  });

  it("does not treat a visible Threads profile as a login wall just because login CTA text is present", () => {
    const links = [
      "https://www.threads.com/@stevie875443/post/DZ6zcSaErFb",
      "https://www.threads.com/@stevie875443/post/DZ6gGNAEqjT",
    ];

    expect(shouldTreatThreadsProfileAsLoginWall({
      username: "stevie875443",
      bodyText: `
阿牛投資理財|挑戰10年財務自由
4 位粉絲
12 追蹤中
6.1 萬次最近瀏覽次數
登入以查看更多
      `,
      buttonText: ["Sign in", "追蹤"],
      links,
    })).toBe(false);
  });

  it("still treats a Threads login prompt without profile signals as a login wall", () => {
    expect(shouldTreatThreadsProfileAsLoginWall({
      username: "stevie875443",
      bodyText: "登入以查看更多",
      buttonText: ["Sign in", "使用 Instagram 帳號繼續"],
      links: [],
    })).toBe(true);
  });
});
