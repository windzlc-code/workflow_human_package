import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { loginThreadsAccount, type PublishProgress } from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";

type ThreadsLoginInput = {
  padCode: string;
  username: string;
  password: string;
  configPath?: string;
  dataDir?: string;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): ThreadsLoginInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as ThreadsLoginInput;
}

async function main() {
  const input = readInput();
  const padCode = String(input.padCode || "").trim();
  const username = String(input.username || "").trim();
  const password = String(input.password || "").trim();
  if (!padCode) throw new Error("missing padCode");
  if (!username) throw new Error("missing username");
  if (!password) throw new Error("missing password");

  const credentials = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });
  const startedAt = Date.now();
  const logs: Array<PublishProgress & { elapsedMs: number }> = [];
  const result = await loginThreadsAccount(
    credentials,
    padCode,
    { username, password },
    (item) => logs.push({ ...item, elapsedMs: Date.now() - startedAt }),
  );

  printJson({
    ok: Boolean(result.ok),
    padCode,
    username,
    result,
    elapsedMs: Date.now() - startedAt,
    logs,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
