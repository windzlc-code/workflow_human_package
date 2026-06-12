import { beforeEach, describe, expect, it, vi } from "vitest";
import { publishTelegramGroupPost } from "@/lib/telegram-group-publisher";

const mockState = vi.hoisted(() => ({
  commands: [] as string[],
  input: [] as string[],
  taskResults: new Map<number, string>(),
  uiXmlQueue: [] as string[],
  sharePickerXml: "",
  screenshotBase64: "",
  launcherStartReturnsInstagram: false,
  explicitTelegramLaunchStarted: false,
}));

vi.mock("@/lib/vmos-client", () => ({
  execAdb: vi.fn(async (_config, _padCode, command: string) => {
    mockState.commands.push(command);
    if (command.includes("org.telegram.messenger/org.telegram.ui.LaunchActivity")) {
      mockState.explicitTelegramLaunchStarted = true;
    }
    const taskId = mockState.commands.length;
    const result = command.includes("uiautomator dump")
      ? (mockState.uiXmlQueue.length ? mockState.uiXmlQueue.shift() : mockState.sharePickerXml)
      : mockState.launcherStartReturnsInstagram && command.includes("dumpsys window") && !mockState.explicitTelegramLaunchStarted
        ? "com.instagram.barcelona/.mainactivity.BarcelonaActivity"
        : "org.telegram.messenger/.DefaultIcon";
    mockState.taskResults.set(taskId, result);
    return taskId;
  }),
  waitTask: vi.fn(async (_config, taskId: number) => ({ taskStatus: 3, taskResult: mockState.taskResults.get(taskId) || "" })),
  inputText: vi.fn(async (_config, _padCode, text: string) => {
    mockState.input.push(text);
    return "input-task";
  }),
  screenshot: vi.fn(async () => "https://example.test/screenshot.jpg"),
}));

vi.mock("@/lib/gemini-client", () => ({
  getInlineData: vi.fn(async () => ({
    mimeType: "image/png",
    data: mockState.screenshotBase64,
  })),
}));

describe("VMOS Telegram group publisher", () => {
  beforeEach(() => {
    mockState.commands = [];
    mockState.input = [];
    mockState.taskResults = new Map();
    mockState.uiXmlQueue = [];
    mockState.sharePickerXml = "";
    mockState.screenshotBase64 = "";
    mockState.launcherStartReturnsInstagram = false;
    mockState.explicitTelegramLaunchStarted = false;
  });

  it("posts text through the Telegram app on the VMOS pad", async () => {
    const progress: Array<{ step: string; done: boolean }> = [];
    mockState.sharePickerXml = '<node text="输入消息"/><node text="TG群測試"/>';

    const result = await publishTelegramGroupPost(
      {},
      {
        padCode: "ACP250322677KIRJ",
        caption: "Test1 VMOS Telegram 自动化发推文验证",
        telegramTargetGroupName: "TG群測試",
        telegramTargetChatId: "-1003812332642",
        telegramGroupContentType: "free",
      },
      (item) => progress.push(item),
    );

    expect(result).toEqual({
      state: "verified",
      detail: "已通过 VMOS Telegram App 发送到当前打开的群组",
      screenshotUrl: "https://example.test/screenshot.jpg",
    });
    expect(mockState.input).toEqual(["Test1 VMOS Telegram 自动化发推文验证"]);
    expect(mockState.commands.join("\n")).toContain("input tap 170 1518");
    expect(mockState.commands.join("\n")).toContain("input tap 655 1000");
    expect(progress.at(-1)).toEqual({ step: "Telegram 群组发布完成", done: true });
  });

  it("uses a safe search keyword then clicks the exact Chinese free group result", async () => {
    const chatListXml = '<node text="Telegram" content-desc="Search" bounds="[0,0][720,1600]"/>';
    mockState.uiXmlQueue = [
      "",
      "",
      chatListXml,
      chatListXml,
      chatListXml,
      chatListXml,
      [
        '<node text="AG-TG群測試" bounds="[40,300][500,380]" clickable="true"/>',
        '<node text="TG群測試" bounds="[40,520][500,600]" clickable="true"/>',
      ].join(""),
      '<node text="输入消息" bounds="[0,1450][720,1600]"/><node text="TG群測試" bounds="[40,60][500,130]"/>',
    ];

    await publishTelegramGroupPost(
      {},
      {
        padCode: "ACP250322677KIRJ",
        caption: "free route",
        telegramTargetGroupName: "TG群測試",
        telegramTargetChatId: "-1003812332642",
        telegramGroupContentType: "free",
      },
      () => undefined,
    );

    expect(mockState.input).toEqual(["TG", "free route"]);
    expect(mockState.input).not.toContain("TG群測試");
    expect(mockState.commands.join("\n")).toContain("input tap 270 560");
  });

  it("posts media through Telegram Android share intent", async () => {
    mockState.sharePickerXml = '<node text="输入消息"/><node text="TG群測試"/>';

    const result = await publishTelegramGroupPost(
      {},
      {
        padCode: "ACP250322677KIRJ",
        caption: "media",
        mediaUrl: "https://example.test/a.jpg",
        mediaContentUri: "content://media/external/images/media/123",
        mediaMimeType: "image/jpeg",
        telegramTargetGroupName: "TG群測試",
        telegramTargetChatId: "-1003812332642",
        telegramGroupContentType: "free",
      },
      () => undefined,
    );

    expect(result.detail).toContain("分享媒体到当前打开的群组");
    expect(result.state).toBe("verified");
    const commands = mockState.commands.join("\n");
    expect(commands).toContain("android.intent.action.SEND");
    expect(commands).toContain("org.telegram.messenger");
    expect(commands).toContain("content://media/external/images/media/123");
    expect(commands).toContain("input tap 230 665");
    expect(commands).toContain("input tap 650 1515");
  });

  it("still advances when Telegram share opens the media preview without UI XML", async () => {
    mockState.uiXmlQueue = [
      "",
      '<node text="输入消息"/><node text="TG群測試"/>',
      '<node text="test"/><node text="Telegram Image"/>',
    ];
    mockState.sharePickerXml = '<node text="test"/><node text="Telegram Image"/>';

    const result = await publishTelegramGroupPost(
      {},
      {
        padCode: "ACP250322677KIRJ",
        caption: "media",
        mediaUrl: "https://example.test/a.jpg",
        mediaContentUri: "content://media/external/images/media/123",
        mediaMimeType: "image/jpeg",
        telegramTargetGroupName: "TG群測試",
        telegramTargetChatId: "-1003812332642",
        telegramGroupContentType: "free",
      },
      () => undefined,
    );

    expect(result.state).toBe("warning");
    expect(result.detail).toContain("未提供可读群组消息确认");
    const commands = mockState.commands.join("\n");
    expect(commands).toContain("input tap 230 665");
    expect(commands).toContain("input tap 650 1515");
  });

  it("does not mark media as sent when Telegram remains on the share picker", async () => {
    const sharp = (await import("sharp")).default;
    mockState.uiXmlQueue = [
      "",
      '<node text="输入消息"/><node text="TG群測試"/>',
      '<node text="选择聊天"/><node text="搜索聊天"/>',
      '<node text="选择聊天"/><node text="搜索聊天"/>',
    ];
    mockState.sharePickerXml = '<node text="选择聊天"/><node text="搜索聊天"/>';
    mockState.screenshotBase64 = (await sharp({
      create: { width: 120, height: 120, channels: 3, background: "white" },
    }).png().toBuffer()).toString("base64");

    await expect(publishTelegramGroupPost(
      {},
      {
        padCode: "ACP250322677KIRJ",
        caption: "media",
        mediaUrl: "https://example.test/a.jpg",
        mediaContentUri: "content://media/external/images/media/123",
        mediaMimeType: "image/jpeg",
        telegramTargetGroupName: "TG群測試",
        telegramTargetChatId: "-1003812332642",
        telegramGroupContentType: "free",
      },
      () => undefined,
    )).rejects.toThrow("Telegram 分享页没有选中目标群组");
  });

  it("requires staged content uri for media posting", async () => {
    await expect(publishTelegramGroupPost(
      {},
      {
        padCode: "ACP250322677KIRJ",
        caption: "media",
        mediaUrl: "https://example.test/a.jpg",
        telegramTargetGroupName: "TG群測試",
        telegramTargetChatId: "-1003812332642",
        telegramGroupContentType: "free",
      },
      () => undefined,
    )).rejects.toThrow("图片或视频没有成功写入云机");
  });

  it("falls back to Telegram LaunchActivity when package launcher intent is not resolvable", async () => {
    mockState.launcherStartReturnsInstagram = true;
    mockState.sharePickerXml = '<node text="输入消息"/><node text="TG群測試"/>';

    await publishTelegramGroupPost(
      {},
      {
        padCode: "ATP64K6RON7LCGMR",
        caption: "金君雅2.0 Telegram 启动兼容验证",
        telegramTargetGroupName: "TG群測試",
        telegramTargetChatId: "-1003812332642",
        telegramGroupContentType: "free",
      },
      () => undefined,
    );

    expect(mockState.commands.join("\n")).toContain("org.telegram.messenger/org.telegram.ui.LaunchActivity");
  });
});
