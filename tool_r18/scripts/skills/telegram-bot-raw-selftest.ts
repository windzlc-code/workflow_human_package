import { execFile } from "node:child_process";

async function main() {
  const prompt = process.argv.slice(2).join(" ").trim() || "帮我创建一个理财专家人设，女性，知性优雅，故事化表达，繁体中文";
  const instructions = [
    "你是自動化推文營運控制台的AI助手。你通过执行shell命令和skill脚本来完成用户需求。",
    "## 可用skill:",
    "npm run skill:persona -- '<JSON>'（优先用于创建人设、查看人设、生成推文、管理人设）",
    "npm run skill:generate-persona -- '<JSON>'（仅用于dry-run prompt诊断）",
    "npm run skill:generate-persona-images -- '<JSON>'",
    "npm run skill:publish-once -- '<JSON>' (padCode默认ACP250801768QX47, platform默认threads)",
    "npm run skill:memory -- '<JSON>'",
    "npm run skill:publish-queue -- '<JSON>'",
    "npm run skill:verify-path -- '<JSON>'",
    "## 规则：",
    "- 遇到创建人设、列出人设、查看人设、生成推文时，必须优先调用 skill:persona，不要自己即兴组织流程。",
    "- 只有在明确是 prompt 诊断时才调用 skill:generate-persona。",
    "## 回复：用简洁中文回复结果，不要输出原始JSON。",
    `用户指令: ${prompt}`,
  ].join("\n");

  await new Promise<void>((resolve) => {
    const child = execFile("codex", [
      "exec",
      "--skip-git-repo-check",
      "-p", "m27",
      "-s", "read-only",
      "-o", "/tmp/codex-bot-raw-out.txt",
      "-",
    ], {
      cwd: "/opt/Automatic-script",
      timeout: 120_000,
      env: {
        ...process.env,
        MINIMAX_API_KEY: process.env.MINIMAX_API_KEY || "",
        OPENAI_API_KEY: "",
        OPENAI_BASE_URL: "",
      },
    }, (error, stdout, stderr) => {
      process.stdout.write("===STDOUT===\n");
      process.stdout.write(stdout || "<empty stdout>\n");
      process.stdout.write("\n===STDERR===\n");
      process.stdout.write(stderr || "<empty stderr>\n");
      if (error) process.stdout.write(`\n===ERROR===\n${error.message}\n`);
      resolve();
    });

    child.stdin?.write(instructions + "\n\n用户指令: " + prompt);
    child.stdin?.end();
  });
}

main();
