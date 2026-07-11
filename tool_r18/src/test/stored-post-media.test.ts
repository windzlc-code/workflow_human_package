import { describe, expect, it } from "vitest";
import { buildStoredPostsListView, getStoredPostPrimaryMediaUrl } from "@/telegram-bot";

describe("stored post media preview selection", () => {
  it("prefers a public media URL over an inaccessible local path", () => {
    const post = {
      imageUrl: "E:\\missing\\generated-image.jpg",
      imageHistory: [
        { imageUrl: "https://cdn.example.com/generated-image.jpg" },
      ],
    };

    expect(getStoredPostPrimaryMediaUrl(post, true)).toBe("https://cdn.example.com/generated-image.jpg");
  });

  it("uses the public URL when a media item also contains a local cache path", () => {
    const post = {
      mediaItems: [
        {
          url: "https://cdn.example.com/post-image.png",
          localPath: "E:\\missing\\post-image.png",
          type: "image" as const,
        },
      ],
    };

    expect(getStoredPostPrimaryMediaUrl(post, true)).toBe("https://cdn.example.com/post-image.png");
  });

  it("does not add media availability warnings to the shared post list", () => {
    const post = {
      id: "missing-media-post",
      content: "測試推文",
      imageUrl: "E:\\missing\\post-image.png",
    };

    const view = buildStoredPostsListView("archive-id", [post]);
    expect(view.text).toContain("类型: 圖片");
    expect(view.text).not.toContain("圖片失效");
    expect(view.keyboard[0][0].text).toBe("👁 查看第1篇（圖片）");
  });

  it("keeps the ordinary persona list presentation unchanged", () => {
    const post = {
      id: "ordinary-post",
      content: "普通人設推文",
      imageUrl: "E:\\missing\\ordinary-image.png",
    };

    const view = buildStoredPostsListView("ordinary-persona", [post]);
    expect(view.text).toContain("类型: 圖片");
    expect(view.text).not.toContain("圖片失效");
    expect(view.keyboard[0][0].text).toBe("👁 查看第1篇（圖片）");
  });

});
