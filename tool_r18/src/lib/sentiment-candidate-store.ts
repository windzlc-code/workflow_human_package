import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { resolveRuntimeFile } from "@/runtime/node/data-dir";

export type SentimentHotPlatform = "threads" | "instagram";
export type SentimentHotMediaType = "image" | "video" | "unknown";

export interface SentimentHotMedia {
  type: SentimentHotMediaType;
  url: string;
  localPath?: string;
  warning?: string;
}

export interface SentimentHotCandidate {
  id: string;
  platform: SentimentHotPlatform;
  sourceUrl: string;
  author: string;
  content: string;
  media: SentimentHotMedia[];
  hotScore: number;
  metrics: Record<string, unknown>;
  engagement?: {
    likeCount?: number;
    commentCount?: number;
    viewCount?: number;
    shareCount?: number;
    rawSignals?: number[];
  };
  publishedAt?: string;
  capturedAt: string;
  warnings?: string[];
}

type StoreState = {
  shown: Record<string, string[]>;
  selected: Record<string, string[]>;
  imported: Record<string, string[]>;
};

const STORE_FILE = resolveRuntimeFile("sentiment_hot_candidates.json");

function emptyState(): StoreState {
  return { shown: {}, selected: {}, imported: {} };
}

function readState(): StoreState {
  try {
    if (!fs.existsSync(STORE_FILE)) return emptyState();
    const parsed = JSON.parse(fs.readFileSync(STORE_FILE, "utf8"));
    return {
      shown: parsed?.shown && typeof parsed.shown === "object" ? parsed.shown : {},
      selected: parsed?.selected && typeof parsed.selected === "object" ? parsed.selected : {},
      imported: parsed?.imported && typeof parsed.imported === "object" ? parsed.imported : {},
    };
  } catch {
    return emptyState();
  }
}

function writeState(state: StoreState) {
  fs.mkdirSync(path.dirname(STORE_FILE), { recursive: true });
  fs.writeFileSync(STORE_FILE, JSON.stringify(state, null, 2), "utf8");
}

export function buildSentimentCandidateId(input: { platform: string; sourceUrl?: string; content?: string }): string {
  const stable = [input.platform, input.sourceUrl || "", input.content || ""].join("\n");
  return crypto.createHash("sha1").update(stable).digest("hex").slice(0, 20);
}

export function getSentimentHotExcludedIds(archiveId: string): Set<string> {
  const state = readState();
  return new Set([
    ...(state.selected[archiveId] || []),
    ...(state.imported[archiveId] || []),
  ]);
}

export function getSentimentHotRefreshExcludedIds(archiveId: string): Set<string> {
  const state = readState();
  return new Set([
    ...(state.shown[archiveId] || []),
    ...(state.selected[archiveId] || []),
    ...(state.imported[archiveId] || []),
  ]);
}

export function getSentimentHotShownIds(archiveId: string): Set<string> {
  const state = readState();
  return new Set(state.shown[archiveId] || []);
}

export function rememberSentimentHotShown(archiveId: string, candidates: SentimentHotCandidate[]) {
  const state = readState();
  const current = new Set(state.shown[archiveId] || []);
  for (const candidate of candidates) current.add(candidate.id);
  state.shown[archiveId] = [...current].slice(-500);
  writeState(state);
}

export function rememberSentimentHotSelected(archiveId: string, candidateId: string) {
  const state = readState();
  const selected = new Set(state.selected[archiveId] || []);
  selected.add(candidateId);
  state.selected[archiveId] = [...selected].slice(-500);
  writeState(state);
}

export function rememberSentimentHotImported(archiveId: string, candidateId: string) {
  const state = readState();
  const imported = new Set(state.imported[archiveId] || []);
  imported.add(candidateId);
  state.imported[archiveId] = [...imported].slice(-500);
  writeState(state);
}
