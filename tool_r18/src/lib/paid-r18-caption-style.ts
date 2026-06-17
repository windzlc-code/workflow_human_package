import type { DramaSetup } from "@/types/drama";
import { usesJinjunyaFreeContentStyle } from "@/lib/workflow-personas";

export function buildPersonaPaidCaptionToneGuide(setup?: Partial<DramaSetup> | null): string[] {
  if (!usesJinjunyaFreeContentStyle(setup)) return [];
  return [
    "【金君雅付費群口吻補充】",
    "延續免費內容那種台灣口語、像真人偷傳一句的感覺，但內容仍然是付費群福利導向。",
    "要像人在說話，不要像鏡頭描述、器官盤點或商品文案。",
    "優先寫成帶反應的小句子：可以自然用「這件…」「剛剛…」「結果…」「有點…」「真的…」這類口氣。",
    "服裝和視覺焦點一定要出現，但要融進同一句自然反應裡，不要拆成名詞清單。",
    "允許一點害羞、試探、撒嬌、偷放一張的語感，像在跟熟人說話。",
    "避免連續堆疊身體部位名詞；不要寫成「米白睡袍完全敞開，豐滿乳房和暗褐乳頭全露出」這種機械句。",
  ];
}

export function isMechanicalPaidCaption(text: string): boolean {
  const body = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !/^https?:\/\//i.test(line))
    .join(" ");
  if (!body) return false;

  const anatomyMatches = body.match(/乳房|乳頭|乳晕|乳暈|乳溝|陰部|陰唇|陰蒂|胸口|大腿|腿根/gi) || [];
  const hasMechanicalVerb = /全露出|完全敞開|清晰可見|明顯露出|自然清晰|完全打開|豐滿乳房|暗褐乳頭/i.test(body);
  const hasListyStructure = /[，、,].*(乳房|乳頭|乳晕|乳暈|乳溝|陰部|陰唇|陰蒂|胸口|大腿|腿根)/i.test(body);
  const hasColloquialBeat = /有點|真的|剛剛|結果|差點|根本|這件|這套|這張|太|欸|啦|喔|耶|好像|怎麼/i.test(body);

  if (anatomyMatches.length >= 2 && !hasColloquialBeat) return true;
  if (hasMechanicalVerb && hasListyStructure) return true;
  return false;
}
