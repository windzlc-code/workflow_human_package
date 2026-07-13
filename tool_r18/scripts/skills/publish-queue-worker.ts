import fs from "node:fs";
import { createNodePublishQueueRepository, type EnqueueTaskInput } from "@/runtime/node/publish-queue-repository";
import { parseScheduledDate } from "@/core/publish/schedule-time";

interface PublishQueueWorkerInput {
  action: "enqueue" | "list" | "retry" | "cancel" | "reschedule" | "batch-retry" | "batch-reschedule" | "batch-cancel";
  task?: EnqueueTaskInput;
  taskId?: string;
  status?: string | string[];
  archiveId?: string;
  telegramChatId?: string;
  scheduledAt?: string;
  scheduledOnly?: boolean;
  limit?: number;
}

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

async function main() {
  const raw = process.argv[2];
  if (!raw) {
    printJson({ ok: false, error: "missing JSON input" });
    process.exitCode = 1;
    return;
  }

  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  const input = JSON.parse(payload.replace(/^\uFEFF/, "")) as PublishQueueWorkerInput;
  const repo = createNodePublishQueueRepository();
  const telegramChatId = String(input.telegramChatId || "").trim();

  const requireOwnedTask = (taskId: string) => {
    const task = repo.getTask(taskId);
    if (!task) throw new Error("publish task not found");
    if (!telegramChatId || String(task.telegram_chat_id || "") !== telegramChatId) {
      throw new Error("publish task does not belong to this Web Bot session");
    }
    return task;
  };
  const normalizeScheduledAt = () => {
    const value = String(input.scheduledAt || "").trim();
    if (!value) throw new Error("scheduledAt must be a valid date-time string");
    try {
      return parseScheduledDate(value).toISOString();
    } catch {
      throw new Error("scheduledAt must be a valid date-time string");
    }
  };
  const listOwnedTasks = () => {
    if (!telegramChatId) throw new Error("missing telegramChatId");
    let tasks = repo.listTasks({
      status: input.status,
      archive_id: String(input.archiveId || "").trim() || undefined,
      telegram_chat_id: telegramChatId,
      limit: Number(input.limit) > 0 ? Math.max(1, Math.min(Number(input.limit), 100_000)) : undefined,
    });
    if (input.scheduledOnly) {
      tasks = tasks.filter((task) => new Date(task.scheduled_at).getTime() - new Date(task.created_at).getTime() > 60_000);
    }
    return tasks;
  };
  const updateOwnedTask = (
    taskId: string,
    expectedStatuses: Array<"pending" | "publishing" | "done" | "failed" | "paused" | "cancelled">,
    status: "pending" | "publishing" | "done" | "failed" | "paused" | "cancelled",
    opts: Parameters<typeof repo.updateTaskStatusIfCurrent>[3],
  ) => {
    requireOwnedTask(taskId);
    if (!repo.updateTaskStatusIfCurrent(taskId, expectedStatuses, status, opts)) {
      throw new Error(`publish task state changed; current status is ${repo.getTask(taskId)?.status || "missing"}`);
    }
  };

  if (input.action === "enqueue") {
    if (!input.task) {
      printJson({ ok: false, error: "missing task" });
      process.exitCode = 1;
      return;
    }
    printJson({ ok: true, task: repo.enqueueTask(input.task) });
    return;
  }

  if (input.action === "list") {
    printJson({ ok: true, tasks: listOwnedTasks() });
    return;
  }

  if (input.action === "retry") {
    if (!input.taskId) {
      printJson({ ok: false, error: "missing taskId" });
      process.exitCode = 1;
      return;
    }
    updateOwnedTask(input.taskId, ["failed"], "pending", {
      scheduled_at: new Date().toISOString(),
      last_error: "",
      finished_at: "",
    });
    printJson({ ok: true, task: repo.getTask(input.taskId) });
    return;
  }

  if (input.action === "cancel") {
    if (!input.taskId) {
      printJson({ ok: false, error: "missing taskId" });
      process.exitCode = 1;
      return;
    }
    updateOwnedTask(input.taskId, ["pending", "paused", "failed"], "cancelled", {
      last_error: "已手动取消",
      finished_at: new Date().toISOString(),
    });
    printJson({ ok: true, task: repo.getTask(input.taskId) });
    return;
  }

  if (input.action === "reschedule") {
    if (!input.taskId) throw new Error("missing taskId");
    updateOwnedTask(input.taskId, ["pending", "paused", "failed"], "pending", {
      scheduled_at: normalizeScheduledAt(),
      last_error: "",
      finished_at: "",
    });
    printJson({ ok: true, task: repo.getTask(input.taskId) });
    return;
  }

  if (input.action === "batch-retry") {
    const tasks = listOwnedTasks().filter((task) => task.status === "failed");
    const changedIds: string[] = [];
    for (const task of tasks) {
      if (repo.updateTaskStatusIfCurrent(task.id, ["failed"], "pending", { scheduled_at: new Date().toISOString(), last_error: "", finished_at: "" })) changedIds.push(task.id);
    }
    printJson({ ok: true, count: changedIds.length, tasks: changedIds.map((taskId) => repo.getTask(taskId)) });
    return;
  }

  if (input.action === "batch-reschedule" || input.action === "batch-cancel") {
    if (!String(input.archiveId || "").trim()) throw new Error("missing archiveId");
    const tasks = listOwnedTasks().filter((task) => task.status === "pending");
    const scheduledAt = input.action === "batch-reschedule" ? normalizeScheduledAt() : "";
    const changedIds: string[] = [];
    for (const task of tasks) {
      if (input.action === "batch-reschedule") {
        if (repo.updateTaskStatusIfCurrent(task.id, ["pending"], "pending", { scheduled_at: scheduledAt, last_error: "", finished_at: "" })) changedIds.push(task.id);
      } else {
        if (repo.updateTaskStatusIfCurrent(task.id, ["pending"], "cancelled", { last_error: "已批量取消", finished_at: new Date().toISOString() })) changedIds.push(task.id);
      }
    }
    printJson({ ok: true, count: changedIds.length, tasks: changedIds.map((taskId) => repo.getTask(taskId)) });
    return;
  }

  printJson({ ok: false, error: `unsupported action: ${input.action}` });
  process.exitCode = 1;
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
