import { createRequire } from "node:module";
import type { EnqueueTaskInput, PublishTask, TaskFilter } from "../../../electron/publish-queue-db";

const nodeRequire = createRequire(import.meta.url);

export interface PublishQueueRepository {
  enqueueTask(input: EnqueueTaskInput): PublishTask;
  listTasks(filter?: TaskFilter): PublishTask[];
  getTask(id: string): PublishTask | null;
  updateTaskStatus: typeof import("../../../electron/publish-queue-db").updateTaskStatus;
  acquirePadLock: typeof import("../../../electron/publish-queue-db").acquirePadLock;
  releasePadLock: typeof import("../../../electron/publish-queue-db").releasePadLock;
  isPadLocked: typeof import("../../../electron/publish-queue-db").isPadLocked;
  getDuePendingTasks: typeof import("../../../electron/publish-queue-db").getDuePendingTasks;
  getStuckPublishingTasks: typeof import("../../../electron/publish-queue-db").getStuckPublishingTasks;
  getExpiredPausedTasks: typeof import("../../../electron/publish-queue-db").getExpiredPausedTasks;
}

export function createPublishQueueRepository(): PublishQueueRepository {
  const mod = nodeRequire("../../../electron/publish-queue-db.cjs") as typeof import("../../../electron/publish-queue-db");
  mod.getPublishQueueDb();
  return {
    enqueueTask: mod.enqueueTask,
    listTasks: mod.listTasks,
    getTask: mod.getTask,
    updateTaskStatus: mod.updateTaskStatus,
    acquirePadLock: mod.acquirePadLock,
    releasePadLock: mod.releasePadLock,
    isPadLocked: mod.isPadLocked,
    getDuePendingTasks: mod.getDuePendingTasks,
    getStuckPublishingTasks: mod.getStuckPublishingTasks,
    getExpiredPausedTasks: mod.getExpiredPausedTasks,
  };
}
