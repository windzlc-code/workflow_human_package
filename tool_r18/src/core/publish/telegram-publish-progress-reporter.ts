export type TelegramProgressMessageClient = {
  sendMessage(chatId: number, text: string, options?: any): Promise<{ message_id: number }>;
  editMessageText(text: string, options: any): Promise<unknown>;
};

export type ScheduledPublishProgressStatus = "publishing" | "done" | "failed" | "paused" | "retrying";

export type ScheduledPublishProgressMeta = {
  taskId: string;
  platform: string;
  padCode: string;
};

const MIN_UPDATE_INTERVAL_MS = 1200;

function compactLogs(logs: string[]): string[] {
  return logs.filter((line, index) => index === 0 || line !== logs[index - 1]).slice(-6);
}

function statusTitle(status: ScheduledPublishProgressStatus): string {
  if (status === "done") return "✅ 定时发布完成";
  if (status === "failed") return "❌ 定时发布失败";
  if (status === "paused") return "⚠️ 定时发布已暂停";
  if (status === "retrying") return "🔄 定时发布失败，等待自动重试";
  return "🚀 定时发布执行中";
}

export function formatScheduledPublishProgressMessage(
  meta: ScheduledPublishProgressMeta,
  status: ScheduledPublishProgressStatus,
  currentStep?: string,
  logs: string[] = [],
  detail?: string,
): string {
  return [
    statusTitle(status),
    `任务：${meta.taskId.slice(0, 12)}`,
    `平台：${meta.platform || "-"}`,
    `智能体手机：${meta.padCode || "-"}`,
    currentStep ? `当前进度：${currentStep}` : undefined,
    ...compactLogs(logs),
    detail ? `说明：${detail}` : undefined,
  ].filter(Boolean).join("\n");
}

export class TelegramPublishProgressReporter {
  private messageId?: number;
  private logs: string[] = [];
  private lastStep = "准备开始";
  private lastEditAt = 0;
  private editInFlight = false;
  private activeEdit: Promise<void> | null = null;

  constructor(
    private readonly client: TelegramProgressMessageClient,
    private readonly chatId: number,
    private readonly meta: ScheduledPublishProgressMeta,
  ) {}

  async start(): Promise<void> {
    if (!this.messageId) {
      const message = await this.client.sendMessage(
        this.chatId,
        formatScheduledPublishProgressMessage(this.meta, "publishing", this.lastStep, this.logs),
      );
      this.messageId = message.message_id;
      this.lastEditAt = Date.now();
      return;
    }
    await this.edit("publishing", undefined, true);
  }

  async progress(step: string): Promise<void> {
    const normalized = String(step || "执行中").trim() || "执行中";
    this.lastStep = normalized;
    if (this.logs[this.logs.length - 1] !== normalized) this.logs.push(normalized);
    await this.edit("publishing");
  }

  async finish(status: Exclude<ScheduledPublishProgressStatus, "publishing">, detail?: string): Promise<void> {
    await this.edit(status, detail, true);
  }

  private async edit(status: ScheduledPublishProgressStatus, detail?: string, force = false): Promise<void> {
    if (!this.messageId) return;
    if (this.editInFlight) {
      if (!force) return;
      await this.activeEdit?.catch(() => undefined);
    }
    const now = Date.now();
    if (!force && now - this.lastEditAt < MIN_UPDATE_INTERVAL_MS) return;
    this.editInFlight = true;
    const request = this.client.editMessageText(
        formatScheduledPublishProgressMessage(this.meta, status, this.lastStep, this.logs, detail),
        { chat_id: this.chatId, message_id: this.messageId },
      )
      .then(() => {
        this.lastEditAt = Date.now();
      })
      .finally(() => {
        this.editInFlight = false;
        this.activeEdit = null;
      });
    this.activeEdit = request;
    await request;
  }
}
