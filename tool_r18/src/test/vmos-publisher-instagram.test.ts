import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  buildInstagramTextCardDataUrl,
  detectInstagramAcpReelFlowStateLocally,
  findInstagramWarmupActionTarget,
  hasStrongInstagramPublishEvidence,
  shouldUseInstagramAcpFeedDirectPath,
} from "@/lib/vmos-publisher";

describe("Instagram publish verification", () => {
  it("treats homepage image and caption match as strong publish evidence", () => {
    expect(
      hasStrongInstagramPublishEvidence("圖1至圖5的首頁動態中已出現與圖6相符的圖片，且文案完全匹配。"),
    ).toBe(true);
  });

  it("treats homepage post appearance with matching image cues as strong evidence", () => {
    expect(
      hasStrongInstagramPublishEvidence("貼文已出現在首頁且包含目標文案，圖片邊緣色塊與參考圖一致。"),
    ).toBe(true);
  });

  it("treats local homepage reference-image matches as strong evidence", () => {
    expect(
      hasStrongInstagramPublishEvidence("首頁第 2 張截圖已命中參考圖，diff=18.6"),
    ).toBe(true);
  });

  it("does not treat Reels profile changes alone as strong publish evidence", () => {
    expect(
      hasStrongInstagramPublishEvidence("Reels 頁已變化，貼文數增加且個人主頁出現新的 Reel 縮圖。"),
    ).toBe(false);
  });

  it("does not use ACP direct image path for generated caption cards", () => {
    expect(shouldUseInstagramAcpFeedDirectPath("ACP250322677KIRJ", "data:image/png;base64,abc", true)).toBe(false);
    expect(shouldUseInstagramAcpFeedDirectPath("ACP250322677KIRJ", "data:image/png;base64,abc", false)).toBe(true);
  });

  it("does not use ACP direct image path for video media", () => {
    expect(shouldUseInstagramAcpFeedDirectPath("ACP250322677KIRJ", "https://cdn.example.com/demo.mp4", false)).toBe(false);
    expect(shouldUseInstagramAcpFeedDirectPath("ACP250322677KIRJ", "data:video/mp4;base64,abc", false)).toBe(false);
  });

  it("classifies bright Reels browse as browse instead of draft dialog", async () => {
    const image = readFileSync("src/test/fixtures/instagram-reel-flow-samples/bright-reels-browse-720x1600.jpg");
    const dataUrl = `data:image/jpeg;base64,${image.toString("base64")}`;
    await expect(detectInstagramAcpReelFlowStateLocally(dataUrl)).resolves.toBe("reels_browse");
  });

  it("classifies dark Reels browse as browse before gallery rules", async () => {
    const image = readFileSync("src/test/fixtures/instagram-reel-flow-samples/dark-reels-browse-720x1600.jpg");
    const dataUrl = `data:image/jpeg;base64,${image.toString("base64")}`;
    await expect(detectInstagramAcpReelFlowStateLocally(dataUrl)).resolves.toBe("reels_browse");
  });

  it("classifies video Reels browse as browse before gallery rules", async () => {
    const image = readFileSync("src/test/fixtures/instagram-reel-flow-samples/video-reels-browse-720x1600.jpg");
    const dataUrl = `data:image/jpeg;base64,${image.toString("base64")}`;
    await expect(detectInstagramAcpReelFlowStateLocally(dataUrl)).resolves.toBe("reels_browse");
  });

  it("classifies bright Reel camera as camera before bright browse rules", async () => {
    const image = readFileSync("src/test/fixtures/instagram-reel-flow-samples/reel-camera-white-720x1600.jpg");
    const dataUrl = `data:image/jpeg;base64,${image.toString("base64")}`;
    await expect(detectInstagramAcpReelFlowStateLocally(dataUrl)).resolves.toBe("reel_camera");
  });

  it("classifies recent Reel gallery before bottom-blue editor rules", async () => {
    const image = readFileSync("src/test/fixtures/instagram-reel-flow-samples/reel-gallery-recent-720x1600.jpg");
    const dataUrl = `data:image/jpeg;base64,${image.toString("base64")}`;
    await expect(detectInstagramAcpReelFlowStateLocally(dataUrl)).resolves.toBe("reel_gallery");
  });

  it("renders Chinese Instagram text cards as PNG data URLs", async () => {
    const dataUrl = await buildInstagramTextCardDataUrl("今天想把生活過得乾淨一點，也把心情慢慢切回自己的節奏");
    expect(dataUrl).toMatch(/^data:image\/png;base64,/);

    const sharp = (await import("sharp")).default;
    const image = Buffer.from(dataUrl.split(",", 2)[1] || "", "base64");
    const metadata = await sharp(image).metadata();
    expect(metadata.width).toBe(960);
    expect(metadata.height).toBe(960);
  });

  it("finds like and comment targets from instagram ui xml", () => {
    const xml = [
      "<hierarchy>",
      "<node index=\"0\" text=\"Like\" content-desc=\"Like\" class=\"android.widget.ImageView\" clickable=\"true\" enabled=\"true\" bounds=\"[120,980][160,1020]\"/>",
      "<node index=\"1\" text=\"Comment\" content-desc=\"Comment\" class=\"android.widget.ImageView\" clickable=\"true\" enabled=\"true\" bounds=\"[250,980][290,1020]\"/>",
      "</hierarchy>",
    ].join("");
    expect(findInstagramWarmupActionTarget(xml, "like")).toEqual({ x: 140, y: 1000 });
    expect(findInstagramWarmupActionTarget(xml, "comment")).toEqual({ x: 270, y: 1000 });
  });

  it("finds Simplified Chinese Instagram action targets", () => {
    const xml = [
      "<hierarchy>",
      "<node index=\"0\" text=\"赞\" content-desc=\"赞\" class=\"android.widget.ImageView\" clickable=\"true\" enabled=\"true\" bounds=\"[120,980][160,1020]\"/>",
      "<node index=\"1\" text=\"评论\" content-desc=\"评论\" class=\"android.widget.ImageView\" clickable=\"true\" enabled=\"true\" bounds=\"[250,980][290,1020]\"/>",
      "</hierarchy>",
    ].join("");
    expect(findInstagramWarmupActionTarget(xml, "like")).toEqual({ x: 140, y: 1000 });
    expect(findInstagramWarmupActionTarget(xml, "comment")).toEqual({ x: 270, y: 1000 });
  });

  it("does not treat already-liked node as like target", () => {
    const xml = [
      "<hierarchy>",
      "<node index=\"0\" text=\"Liked\" content-desc=\"Liked\" class=\"android.widget.ImageView\" clickable=\"true\" enabled=\"true\" bounds=\"[120,980][160,1020]\"/>",
      "</hierarchy>",
    ].join("");
    expect(findInstagramWarmupActionTarget(xml, "like")).toBeNull();
  });

  it("does not treat already-liked Simplified Chinese node as like target", () => {
    const xml = [
      "<hierarchy>",
      "<node index=\"0\" text=\"已点赞\" content-desc=\"已点赞\" class=\"android.widget.ImageView\" clickable=\"true\" enabled=\"true\" bounds=\"[120,980][160,1020]\"/>",
      "</hierarchy>",
    ].join("");
    expect(findInstagramWarmupActionTarget(xml, "like")).toBeNull();
  });
});
