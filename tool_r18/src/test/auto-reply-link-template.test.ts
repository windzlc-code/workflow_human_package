import { describe, expect, it } from "vitest";
import {
  buildAutoReplyLinkPresetPickerRows,
  composeThreadsCustomReplyContent,
  parseOwnReplyContentEditArchiveId,
  publishHistoryMatchesThreadsPad,
} from "@/telegram-bot";
import { appendThreadsReplySuffix, normalizeThreadsManualReplyText } from "@/lib/vmos-publisher";

describe("auto reply link templates", () => {
  it("appends the selected template exactly once", () => {
    const suffix = "查看更多內容\nhttps://example.com/more";
    const once = appendThreadsReplySuffix("這個觀點很實用", suffix);

    expect(once).toBe(`這個觀點很實用\n${suffix}`);
    expect(appendThreadsReplySuffix(once, suffix)).toBe(once);
  });

  it("does not change a reply when no template is selected", () => {
    expect(appendThreadsReplySuffix("自然回覆", "")).toBe("自然回覆");
  });

  it("keeps the complete template while limiting a Threads reply to 500 characters", () => {
    const suffix = "查看详情\nhttps://example.com/more";
    const result = appendThreadsReplySuffix("正".repeat(600), suffix);

    expect(Array.from(result)).toHaveLength(500);
    expect(result.endsWith(suffix)).toBe(true);
  });

  it("rejects a template that cannot fit in a Threads reply", () => {
    expect(() => appendThreadsReplySuffix("正文", "链".repeat(501))).toThrow("链接模板超过 Threads 回复长度限制");
  });

  it("preserves template line breaks when used as the complete manual reply", () => {
    expect(normalizeThreadsManualReplyText("点击查看更多  \nhttps://example.com/more"))
      .toBe("点击查看更多\nhttps://example.com/more");
  });

  it("builds selectable template rows for both auto reply flows", () => {
    const archive = {
      id: "workflow-persona-jinjunya",
      setup: {
        linkEndingPresets: [{
          id: "more",
          name: "更多內容",
          endingText: "查看更多內容",
          linkUrl: "https://example.com/more",
          enabled: true,
        }],
      },
    } as any;

    const commentRows = buildAutoReplyLinkPresetPickerRows(8100401093, archive, "comments");
    const hotRows = buildAutoReplyLinkPresetPickerRows(8100401093, archive, "hot");
    expect(commentRows[0][0].callback_data).toMatch(/^arl_c_0_[a-f0-9]{10}$/);
    expect(commentRows[0][1].callback_data).toMatch(/^arld_c_0_[a-f0-9]{10}$/);
    expect(hotRows[0][0].callback_data).toMatch(/^arl_h_0_[a-f0-9]{10}$/);
    expect(hotRows[0][1].callback_data).toMatch(/^arld_h_0_[a-f0-9]{10}$/);
    expect(commentRows.flat().some((button) => button.text.includes("不添加链接模板"))).toBe(true);
  });

  it("allows creating a template when the persona has none", () => {
    const archive = { id: "persona-empty", setup: {} } as any;
    const rows = buildAutoReplyLinkPresetPickerRows(8100401093, archive, "comments");
    const labels = rows.flat().map((button) => button.text);

    expect(labels).toContain("➕ 新建链接模板");
    expect(labels).toContain("◀️ 返回自动回复确认");
  });

  it("uses a link template as the complete custom hot reply", () => {
    const archive = {
      id: "workflow-persona-jinjunya",
      setup: {
        linkEndingPresets: [{
          id: "custom-reply",
          name: "完整回复",
          endingText: "点击查看更多",
          linkUrl: "https://example.com/more",
          enabled: false,
        }],
      },
    } as any;

    const rows = buildAutoReplyLinkPresetPickerRows(8100401093, archive, "hot_content");
    const buttons = rows.flat();

    expect(buttons[0].callback_data).toMatch(/^arl_m_0_[a-f0-9]{10}$/);
    expect(buttons.some((button) => /^arld_m_0_[a-f0-9]{10}$/.test(button.callback_data))).toBe(true);
    expect(buttons.some((button) => button.text.includes("跳过链接模板"))).toBe(true);
    expect(buttons.some((button) => /^arladd_m_[a-f0-9]{10}$/.test(button.callback_data))).toBe(true);
    expect(buttons.some((button) => /^arlback_m_[a-f0-9]{10}$/.test(button.callback_data))).toBe(true);
  });

  it("keeps every template callback within Telegram's 64-byte limit for long persona ids", () => {
    const archive = {
      id: `工作流人设-${"很长".repeat(40)}`,
      setup: { linkEndingPresets: [{ id: "one", endingText: "模板" }] },
    } as any;

    const rows = buildAutoReplyLinkPresetPickerRows(8100401093, archive, "hot_content");
    for (const button of rows.flat()) {
      expect(Buffer.byteLength(button.callback_data, "utf8")).toBeLessThanOrEqual(64);
    }
  });

  it("composes optional manual text and link template without allowing hidden content", () => {
    expect(composeThreadsCustomReplyContent("手动回复", "")).toBe("手动回复");
    expect(composeThreadsCustomReplyContent("", "https://example.com/more")).toBe("https://example.com/more");
    expect(composeThreadsCustomReplyContent("手动回复", "查看更多\nhttps://example.com/more"))
      .toBe("手动回复\n查看更多\nhttps://example.com/more");
    expect(composeThreadsCustomReplyContent("  ", "\n")).toBe("");
  });

  it("does not route the continue callback into the generic content editor", () => {
    expect(parseOwnReplyContentEditArchiveId("ownreply_content_persona-1")).toBe("persona-1");
    expect(parseOwnReplyContentEditArchiveId("ownreply_content_continue_persona-1")).toBeNull();
  });
});

describe("own-post auto reply PAD isolation", () => {
  it("accepts only publish history belonging to the selected PAD", () => {
    expect(publishHistoryMatchesThreadsPad({ padCode: "PAD-A" } as any, "PAD-A")).toBe(true);
    expect(publishHistoryMatchesThreadsPad({ padCode: "PAD-A" } as any, "PAD-B")).toBe(false);
    expect(publishHistoryMatchesThreadsPad({ publishedTargets: [{ padCode: "PAD-B" }] } as any, "PAD-B")).toBe(true);
    expect(publishHistoryMatchesThreadsPad({} as any, "PAD-A")).toBe(false);
  });
});
