import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { publishPost, type PublishTask } from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";

interface PublishOnceInput extends PublishTask {
  dryRun?: boolean;
  configPath?: string;
  dataDir?: string;
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

  const payload = raw.startsWith("@")
    ? fs.readFileSync(raw.slice(1), "utf8")
    : raw;
  const input = JSON.parse(payload) as PublishOnceInput;
  const credentials = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });

  if (input.dryRun !== false) {
    printJson({
      ok: true,
      dryRun: true,
      hasCredentials: Boolean(credentials.ak && credentials.sk),
      task: input,
    });
    return;
  }

  const logs: Array<{ step: string; done: boolean; error?: string; warning?: string }> = [];
  const result = await publishPost(
    credentials,
    input,
    (progress) => logs.push(progress),
  );

  printJson({ ok: true, result, logs });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
