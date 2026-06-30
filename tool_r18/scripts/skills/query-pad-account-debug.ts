/**
 * 调试脚本：直接在智能體手機上跑各种 ADB 命令，打印原始输出
 * 用法：npm run skill:query-pad-account-debug -- '{"padCode":"ACP250430WZA6JZL"}'
 */
import { execAdb, waitTask, screenshot } from "@/lib/vmos-client";
import { resolveVmosCredentials } from "@/runtime/node/config";
import "@/runtime/node/browser-shim";

const input = JSON.parse(process.argv[2] || "{}");
const padCode: string = input.padCode || "ACP250430WZA6JZL";

const creds = resolveVmosCredentials();
const config = creds;

async function runAdb(label: string, cmd: string, timeoutMs = 20000) {
  console.log(`\n${"=".repeat(60)}`);
  console.log(`[CMD] ${label}`);
  console.log(`[SH]  ${cmd.slice(0, 200)}`);
  try {
    const taskId = await execAdb(config, padCode, cmd);
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 1500));
      const { waitTask } = await import("@/lib/vmos-client");
      const result = await waitTask(config, taskId, 3000, 500).catch(() => null);
      if (result) {
        console.log(`[OUT] ${JSON.stringify(result.taskResult || "").slice(0, 500)}`);
        console.log(`[ERR] ${JSON.stringify(result.errorMsg || "").slice(0, 200)}`);
        return result.taskResult || "";
      }
    }
    console.log("[TIMEOUT]");
    return "";
  } catch (e: any) {
    console.log(`[FAIL] ${e?.message || String(e)}`);
    return "";
  }
}

async function main() {
  console.log(`\n智能體手機账号调试 padCode=${padCode}`);
  console.log(`凭据 ak=${creds.ak ? creds.ak.slice(0, 8) + "..." : "MISSING"}`);

  // 1. AccountManager（不需要 root）
  await runAdb(
    "AccountManager - 全部账号",
    "dumpsys account 2>/dev/null | head -n 60",
  );

  // 2. AccountManager 过滤 instagram
  await runAdb(
    "AccountManager - instagram/threads 过滤",
    "dumpsys account 2>/dev/null | grep -i 'instagram\\|threads\\|barcelona' | head -n 20",
  );

  // 3. 当前前台 Activity
  await runAdb(
    "当前前台 Activity",
    "dumpsys activity activities 2>/dev/null | grep -E 'mResumedActivity|topResumedActivity' | head -n 5",
  );

  // 4. Threads 包是否安装
  await runAdb(
    "Threads 包是否安装",
    "pm list packages 2>/dev/null | grep -i 'instagram\\|barcelona\\|threads'",
  );

  // 5. 尝试打开 Threads 并截图
  console.log("\n[ACTION] 尝试打开 Threads 个人页...");
  await runAdb(
    "打开 Threads",
    "am start -n com.instagram.barcelona/.activity.MainActivity 2>/dev/null; sleep 3; echo DONE",
    15000,
  );

  // 6. 截图
  console.log("\n[ACTION] 截图...");
  try {
    const shotUrl = await screenshot(config, padCode);
    console.log(`[SCREENSHOT] ${shotUrl}`);
  } catch (e: any) {
    console.log(`[SCREENSHOT FAIL] ${e?.message}`);
  }

  // 7. UI dump
  await runAdb(
    "UI dump (Threads 个人页)",
    "uiautomator dump /sdcard/debug_ui.xml 2>&1; cat /sdcard/debug_ui.xml 2>/dev/null | grep -o 'text=\"[^\"]*\"' | head -n 30",
    30000,
  );

  // 8. sqlite3 Threads DB
  await runAdb(
    "sqlite3 Threads DB users",
    "sqlite3 /data/data/com.instagram.barcelona/databases/direct.db 'SELECT username,full_name FROM users LIMIT 10' 2>/dev/null || echo 'sqlite3 failed'",
    15000,
  );

  // 9. sqlite3 Instagram DB
  await runAdb(
    "sqlite3 Instagram DB users",
    "sqlite3 /data/data/com.instagram.android/databases/direct.db 'SELECT username,full_name FROM users LIMIT 10' 2>/dev/null || echo 'sqlite3 failed'",
    15000,
  );

  console.log("\n[DONE] 调试完成");
}

main().catch((e) => {
  console.error("[FATAL]", e?.message || String(e));
  process.exit(1);
});
