import fs from "node:fs";
import {
  generatePersonaImage,
  generateReferenceSheet,
  type PersonaImageGenerationMode,
  type PersonaImageReferenceMode,
} from "@/lib/persona-image-production";
import { compressImage } from "@/lib/image-compress";
import { generateWorkflowPersonaImage } from "@/runtime/node/comfyui-workflow-client";
import { generateRunningHubAiAppImage } from "@/runtime/node/runninghub-workflow-image";
import { parseDataUrlMedia } from "@/lib/media-utils";
import type { DramaSetup } from "@/types/drama";

export interface GeneratePersonaImagesInput {
  setup: DramaSetup;
  content: string;
  customPrompt?: string;
  model?: string;
  aspectRatio?: string;
  mode?: PersonaImageGenerationMode;
  referenceMode?: PersonaImageReferenceMode;
  referenceImageUrl?: string;
  referenceSheetUrl?: string;
  generateReferenceSheet?: boolean;
  dryRun?: boolean;
  configPath?: string;
  dataDir?: string;
}

function printJson(value: unknown) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function readPositiveIntEnv(name: string, fallback: number): number {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : fallback;
}

const CLOSED_IMAGE_TIMEOUT_MS = readPositiveIntEnv("PERSONA_IMAGE_CLOSED_TIMEOUT_MS", 180_000);
const GENERATED_DATA_URL_TARGET_BYTES = readPositiveIntEnv("PERSONA_IMAGE_DATA_URL_TARGET_BYTES", 512 * 1024);
const RUNNINGHUB_IMAGE_WEBAPP_ID = process.env.RUNNINGHUB_IMAGE_WEBAPP_ID || "2034899011521482754";

function dataUrlBytes(url?: string): number | undefined {
  if (!url?.startsWith("data:")) return undefined;
  const parsed = parseDataUrlMedia(url);
  return parsed ? Buffer.byteLength(parsed.base64, "base64") : undefined;
}

async function compressGeneratedDataUrl(url?: string): Promise<{ url?: string; bytesBefore?: number; bytesAfter?: number; compressed?: boolean }> {
  const bytesBefore = dataUrlBytes(url);
  if (!url?.startsWith("data:image/") || !bytesBefore) return { url, bytesBefore };
  const compressed = await compressImage(url, {
    targetBytes: GENERATED_DATA_URL_TARGET_BYTES,
    maxDim: 1280,
    minQuality: 0.4,
  });
  const bytesAfter = dataUrlBytes(compressed);
  return {
    url: bytesAfter && bytesAfter < bytesBefore ? compressed : url,
    bytesBefore,
    bytesAfter: bytesAfter || bytesBefore,
    compressed: Boolean(bytesAfter && bytesAfter < bytesBefore),
  };
}

const unsupportedImageApi = {
  generate: async (payload: any) => {
    const startedAt = Date.now();
    if (payload?.workflowImage) {
      const result = await generateWorkflowPersonaImage({
        prompt: payload.prompt,
        workflowImage: payload.workflowImage,
        aspectRatio: payload.aspectRatio,
        timeoutMs: payload.timeoutMs,
        referenceImageBase64: payload.avatarBase64,
        referenceImageMimeType: payload.avatarMimeType,
      }, {
        configPath: payload.configPath,
        dataDir: payload.dataDir,
      });
      return {
        ...result,
        timings: {
          ...(result as any)?.timings,
          provider: (result as any)?.timings?.provider
            || (payload.workflowImage?.executionProvider === "comfyui" ? "comfyui-workflow" : "runninghub-workflow"),
          elapsedMs: Date.now() - startedAt,
          timeoutMs: payload.timeoutMs || 300_000,
        },
      };
    }
    const result = await generateRunningHubAiAppImage({
      prompt: payload.prompt,
      webappId: payload.webappId || RUNNINGHUB_IMAGE_WEBAPP_ID,
      aspectRatio: payload.aspectRatio,
      timeoutMs: payload.timeoutMs || CLOSED_IMAGE_TIMEOUT_MS,
    }, {
      configPath: payload.configPath,
      dataDir: payload.dataDir,
    });
    const normalized = await compressGeneratedDataUrl(result.url);
    return {
      ...result,
      url: normalized.url,
      timings: {
        provider: "runninghub-ai-app",
        webappId: payload.webappId || RUNNINGHUB_IMAGE_WEBAPP_ID,
        elapsedMs: Date.now() - startedAt,
        timeoutMs: payload.timeoutMs || CLOSED_IMAGE_TIMEOUT_MS,
        taskId: result.taskId,
        dataUrlBytesBefore: normalized.bytesBefore,
        dataUrlBytesAfter: normalized.bytesAfter,
        dataUrlCompressed: normalized.compressed,
      },
    };
  },
};

async function main() {
  const startedAt = Date.now();
  const rawArg = process.argv[2];
  if (!rawArg) {
    printJson({ ok: false, error: "missing JSON input" });
    process.exitCode = 1;
    return;
  }
  const raw = rawArg.startsWith("@")
    ? fs.readFileSync(rawArg.slice(1), "utf8")
    : rawArg;

  const input = JSON.parse(raw) as GeneratePersonaImagesInput;
  const model = input.model || process.env.PERSONA_IMAGE_MODEL || "gpt-image-2";
  const runtimeOptions = { configPath: input.configPath, dataDir: input.dataDir };

  let referenceSheetMs: number | undefined;
  const referenceSheet = input.generateReferenceSheet
    ? await (async () => {
        const refStartedAt = Date.now();
        const result = await generateReferenceSheet(
        unsupportedImageApi,
        input.setup,
        input.content,
        model,
        runtimeOptions,
        );
        referenceSheetMs = Date.now() - refStartedAt;
        return result;
      })()
    : undefined;

  if (input.generateReferenceSheet) {
    printJson({
      ok: Boolean(referenceSheet?.ok && referenceSheet?.url),
      dryRun: input.dryRun !== false,
      referenceSheet,
      imageResult: {
        ok: Boolean(referenceSheet?.ok && referenceSheet?.url),
        url: referenceSheet?.url,
        mode: "closed-person",
        error: referenceSheet?.error,
      },
      timings: {
        totalMs: Date.now() - startedAt,
        referenceSheetMs,
        provider: (referenceSheet as any)?.timings?.provider,
        detail: (referenceSheet as any)?.timings,
      },
    });
    return;
  }

  const imageStartedAt = Date.now();
  const imageResult = await generatePersonaImage(
    unsupportedImageApi,
    input.setup,
    input.content,
    input.mode || "auto",
    model,
    input.aspectRatio || "1:1",
    input.referenceMode || "none",
    input.referenceImageUrl,
    input.referenceSheetUrl,
    runtimeOptions,
    input.customPrompt,
  );

  printJson({
    ok: Boolean(imageResult?.ok && imageResult?.url),
    dryRun: input.dryRun !== false,
    referenceSheet,
    imageResult,
    timings: {
      totalMs: Date.now() - startedAt,
      imageMs: Date.now() - imageStartedAt,
      provider: (imageResult as any)?.timings?.provider,
      detail: (imageResult as any)?.timings,
    },
  });
}

main().catch((error) => {
  printJson({ ok: false, error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
