import "@/runtime/node/browser-shim";
import fs from "node:fs";
import {
  updateThreadsProfileAvatar,
  updateThreadsProfileBio,
  updateThreadsProfileLink,
  updateThreadsProfileName,
  type PublishProgress,
} from "@/lib/vmos-publisher";
import { resolveVmosCredentials } from "@/runtime/node/config";

type ThreadsProfileKind = "link" | "bio" | "name" | "avatar";

type ThreadsProfileUpdateInput = {
  padCode: string;
  kind: ThreadsProfileKind;
  value: string;
  configPath?: string;
  dataDir?: string;
};

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readInput(): ThreadsProfileUpdateInput {
  const raw = process.argv[2];
  if (!raw) throw new Error("missing JSON input");
  const payload = raw.startsWith("@") ? fs.readFileSync(raw.slice(1), "utf8") : raw;
  return JSON.parse(payload.replace(/^\uFEFF/, "")) as ThreadsProfileUpdateInput;
}

function normalizeKind(value: unknown): ThreadsProfileKind {
  const kind = String(value || "").trim() as ThreadsProfileKind;
  if (!["link", "bio", "name", "avatar"].includes(kind)) throw new Error(`invalid Threads profile update kind: ${kind}`);
  return kind;
}

async function main() {
  const input = readInput();
  const padCode = String(input.padCode || "").trim();
  const value = String(input.value || "").trim();
  const kind = normalizeKind(input.kind);
  if (!padCode) throw new Error("missing padCode");
  if (!value) throw new Error("missing value");

  const credentials = resolveVmosCredentials({ configPath: input.configPath, dataDir: input.dataDir });
  const startedAt = Date.now();
  const logs: Array<PublishProgress & { elapsedMs: number }> = [];
  const progress = (item: PublishProgress) => logs.push({ ...item, elapsedMs: Date.now() - startedAt });
  const result = kind === "link"
    ? await updateThreadsProfileLink(credentials, padCode, value, progress)
    : kind === "bio"
      ? await updateThreadsProfileBio(credentials, padCode, value, progress)
      : kind === "name"
        ? await updateThreadsProfileName(credentials, padCode, value, progress)
        : await updateThreadsProfileAvatar(credentials, padCode, value, progress);

  printJson({
    ok: Boolean(result.ok),
    kind,
    padCode,
    result,
    elapsedMs: Date.now() - startedAt,
    logs,
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
