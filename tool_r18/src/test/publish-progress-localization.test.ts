import { describe, expect, it } from "vitest";
import { formatPublishStepForTelegram } from "@/telegram-bot";

describe("publish progress localization", () => {
  it("translates Telegram cold-start progress into Chinese", () => {
    expect(formatPublishStepForTelegram("Telegram cold start: reset to desktop and clear the previous screen"))
      .toBe("冷启动 Telegram：返回智能体手机桌面并清理上一画面");
    expect(formatPublishStepForTelegram("Telegram cold start complete: normalize to the chat list"))
      .toBe("Telegram 冷启动完成：正在统一回到聊天列表");
  });

  it("localizes common technical status labels before showing them to users", () => {
    expect(formatPublishStepForTelegram("Threads 主頁變化 diff=12.5（best=18.2，state=ready），重新啟動 App"))
      .toBe("Threads 主頁變化 差异值=12.5（最佳值=18.2，状态=ready），重新啟動 应用");
  });

  it("does not expose an unknown English-only technical step", () => {
    expect(formatPublishStepForTelegram("waiting for remote task response"))
      .toBe("正在执行发布流程...");
  });
});
