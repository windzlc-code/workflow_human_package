export function buildRegeneratePostInstruction(originalContent: string, attempt = 1): string {
  return [
    "重新生成下面这 1 篇待发布推文。",
    "要求：",
    "1. 只生成一篇新的推文文案，不要解释，不要输出编号。",
    "2. 保持同一个人设和同一个主题方向；文风必须以当前人设设置、当前推文风格设置和当前生成规则为准。",
    "3. 原推文只用于识别主题和信息点，不得把原推文当作风格模板；如果推文风格已经恢复初始状态，必须回到通用人设推文规则。",
    "4. 不要复用原文句式、段落节奏、口头禅、表情密度或结尾互动方式；表达必须明显不同。",
    attempt > 1 ? `5. 上一次重写与原文过于相似；这次必须重新组织开头、段落顺序、语气和结尾互动。` : "",
    "",
    `原推文：${String(originalContent || "").trim()}`,
  ].filter(Boolean).join("\n");
}

function normalizeSimilarityText(value: string): string {
  return String(value || "")
    .replace(/\b(?:https?:\/\/|www\.)\S+/gi, "")
    .replace(/[^\p{Letter}\p{Number}]+/gu, "")
    .toLowerCase();
}

function bigramSet(value: string): Set<string> {
  const set = new Set<string>();
  for (let index = 0; index < value.length - 1; index += 1) {
    set.add(value.slice(index, index + 2));
  }
  return set;
}

function hasLongSharedFragment(original: string, generated: string, minLength = 24): boolean {
  const shorter = original.length <= generated.length ? original : generated;
  const longer = original.length <= generated.length ? generated : original;
  if (shorter.length < minLength) return false;
  for (let index = 0; index <= shorter.length - minLength; index += 1) {
    if (longer.includes(shorter.slice(index, index + minLength))) return true;
  }
  return false;
}

function countListStructureMarkers(value: string): number {
  const text = String(value || "");
  const matches = text.match(/(?:^|[\n\s。！？；;，,])(?:\d{1,2}[.、)]|[一二三四五六七八九十]+[.、)]|[-•*])\s*/g);
  return matches?.length || 0;
}

function hasCopiedListStructure(originalContent: string, generatedContent: string): boolean {
  const originalMarkers = countListStructureMarkers(originalContent);
  const generatedMarkers = countListStructureMarkers(generatedContent);
  return originalMarkers >= 2 && generatedMarkers >= 2;
}

export function calculateRegeneratedPostSimilarity(originalContent: string, generatedContent: string): number {
  const original = normalizeSimilarityText(originalContent);
  const generated = normalizeSimilarityText(generatedContent);
  if (!original || !generated) return 0;
  if (original === generated) return 1;
  const originalSet = bigramSet(original);
  const generatedSet = bigramSet(generated);
  if (!originalSet.size || !generatedSet.size) return 0;
  let overlap = 0;
  for (const item of generatedSet) {
    if (originalSet.has(item)) overlap += 1;
  }
  return overlap / Math.min(originalSet.size, generatedSet.size);
}

export function isRegeneratedPostTooSimilar(
  originalContent: string,
  generatedContent: string,
  options: { allowSameListStructure?: boolean; similarityThreshold?: number } = {},
): boolean {
  const original = normalizeSimilarityText(originalContent);
  const generated = normalizeSimilarityText(generatedContent);
  if (!original || !generated) return false;
  if (original === generated) return true;
  const shortOriginal = original.slice(0, Math.min(original.length, 80));
  const shortGenerated = generated.slice(0, Math.min(generated.length, 80));
  if (shortOriginal.length >= 20 && (generated.includes(shortOriginal) || original.includes(shortGenerated))) return true;
  if (hasLongSharedFragment(original, generated)) return true;
  if (!options.allowSameListStructure && hasCopiedListStructure(originalContent, generatedContent)) return true;
  return calculateRegeneratedPostSimilarity(originalContent, generatedContent) >= (options.similarityThreshold ?? 0.72);
}
