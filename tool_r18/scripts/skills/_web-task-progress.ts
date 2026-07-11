import fs from "node:fs";

const progressFile = String(process.env.WEB_TASK_PROGRESS_FILE || "").trim();

export function emitWebTaskProgress(event: Record<string, unknown>) {
  if (!progressFile) return;
  try {
    fs.appendFileSync(progressFile, `${JSON.stringify({ at: Date.now(), ...event })}\n`, "utf8");
  } catch {
    // Progress reporting must never interrupt the underlying automation.
  }
}
