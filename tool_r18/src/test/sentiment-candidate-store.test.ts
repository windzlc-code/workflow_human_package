import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

function waitForOutput(child: ChildProcess, expected: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`timed out waiting for child output: ${expected}`)), 5_000);
    child.stdout?.on("data", (chunk) => {
      if (!String(chunk).includes(expected)) return;
      clearTimeout(timeout);
      resolve();
    });
    child.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
  });
}

function waitForExit(child: ChildProcess): Promise<void> {
  return new Promise((resolve, reject) => {
    let stderr = "";
    child.stderr?.on("data", (chunk) => { stderr += String(chunk); });
    if (child.exitCode !== null) {
      if (child.exitCode === 0) resolve();
      else reject(new Error(`child exited with ${child.exitCode}: ${stderr}`));
      return;
    }
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`child exited with ${code}: ${stderr}`));
    });
  });
}

describe("sentiment candidate store", () => {
  it("recovers an abandoned lock before updating the shared store", async () => {
    const runtimeDir = fs.mkdtempSync(path.join(os.tmpdir(), "sentiment-candidate-stale-lock-"));
    const storePath = path.join(runtimeDir, "sentiment_hot_candidates.json");
    const lockPath = path.join(runtimeDir, "sentiment_hot_candidates.lock");
    const sourceUrl = pathToFileURL(path.resolve("src/lib/sentiment-candidate-store.ts")).href;
    try {
      fs.writeFileSync(storePath, JSON.stringify({ shown: {}, selected: {}, imported: {} }), "utf8");
      fs.writeFileSync(lockPath, `99999999 ${Date.now() - 10 * 60_000}\n`, "utf8");
      const script = [
        `import { rememberSentimentHotSelected } from ${JSON.stringify(sourceUrl)};`,
        `rememberSentimentHotSelected("persona-1", "candidate-1");`,
      ].join("\n");
      const child = spawn(process.execPath, ["--import", "tsx", "--input-type=module", "--eval", script], {
        cwd: process.cwd(),
        env: { ...process.env, TOOL_R18_RUNTIME_DIR: runtimeDir },
        stdio: ["ignore", "pipe", "pipe"],
      });
      await waitForExit(child);
      const state = JSON.parse(fs.readFileSync(storePath, "utf8"));
      expect(state.selected["persona-1"]).toEqual(["candidate-1"]);
      expect(fs.existsSync(lockPath)).toBe(false);
    } finally {
      fs.rmSync(runtimeDir, { recursive: true, force: true });
    }
  });

  it("preserves a TG update that starts while a Web update holds the store lock", async () => {
    const runtimeDir = fs.mkdtempSync(path.join(os.tmpdir(), "sentiment-candidate-store-"));
    const storePath = path.join(runtimeDir, "sentiment_hot_candidates.json");
    const lockPath = path.join(runtimeDir, "sentiment_hot_candidates.lock");
    const sourceUrl = pathToFileURL(path.resolve("src/lib/sentiment-candidate-store.ts")).href;
    let lockFd: number | undefined;
    let child: ChildProcess | undefined;

    try {
      fs.writeFileSync(storePath, JSON.stringify({ shown: {}, selected: {}, imported: {} }), "utf8");
      lockFd = fs.openSync(lockPath, "wx");
      fs.writeFileSync(lockFd, `${process.pid} ${Date.now()}\n`, "utf8");

      const script = [
        `import { rememberSentimentHotSelected } from ${JSON.stringify(sourceUrl)};`,
        `process.stdout.write("ready\\n");`,
        `rememberSentimentHotSelected("persona-1", "tg-candidate");`,
      ].join("\n");
      child = spawn(process.execPath, ["--import", "tsx", "--input-type=module", "--eval", script], {
        cwd: process.cwd(),
        env: { ...process.env, TOOL_R18_RUNTIME_DIR: runtimeDir },
        stdio: ["ignore", "pipe", "pipe"],
      });

      await waitForOutput(child, "ready");
      await new Promise((resolve) => setTimeout(resolve, 250));

      fs.writeFileSync(storePath, JSON.stringify({
        shown: {},
        selected: { "persona-1": ["web-candidate"] },
        imported: {},
      }), "utf8");
      fs.closeSync(lockFd);
      lockFd = undefined;
      fs.unlinkSync(lockPath);

      await waitForExit(child);
      child = undefined;

      const state = JSON.parse(fs.readFileSync(storePath, "utf8"));
      expect(state.selected["persona-1"].sort()).toEqual(["tg-candidate", "web-candidate"]);
    } finally {
      if (child && child.exitCode === null) child.kill();
      if (lockFd !== undefined) fs.closeSync(lockFd);
      try { fs.unlinkSync(lockPath); } catch {}
      fs.rmSync(runtimeDir, { recursive: true, force: true });
    }
  }, 10_000);
});
