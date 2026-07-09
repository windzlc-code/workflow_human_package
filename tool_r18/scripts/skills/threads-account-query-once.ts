import "@/runtime/node/browser-shim";
import fs from "node:fs";
import { queryThreadsAccount } from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";

type ThreadsAccountQueryInput = {
  padCode: string;
  configPath?: string;
  dataDir?: string;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): ThreadsAccountQueryInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as ThreadsAccountQueryInput;
}

async function main() {
  const input = readInput();
  const padCode = String(input.padCode || "").trim();
  if (!padCode) throw new Error("missing padCode");

  const credentials = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });
  const result = await queryThreadsAccount(credentials, padCode);
  printJson({
    ok: Boolean(result.loggedIn),
    result,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
