import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createSentimentBackendApp } from "../standalone/sentiment-backend/src/server.js";

const tempDirs = [];

function makeTempDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "standalone-sentiment-backend-"));
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

describe("standalone sentiment backend", () => {
  it("serves sentiment APIs without the Electron frontend", async () => {
    const dataDir = makeTempDir();
    const backend = createSentimentBackendApp({
      dataDir,
      enableScheduler: false,
      log: { info() {}, warn() {}, error() {} },
    });

    try {
      const home = await backend.app.request("/");
      expect(home.status).toBe(200);
      expect(home.headers.get("content-type")).toContain("text/html");
      expect(await home.text()).toContain("舆情全网搜索与研判");

      const appJs = await backend.app.request("/assets/app.js");
      expect(appJs.status).toBe(200);
      expect(appJs.headers.get("content-type")).toContain("text/javascript");

      const admin = await backend.app.request("/admin");
      expect(admin.status).toBe(200);
      expect(await admin.text()).toContain("舆情系统后台管理");

      const adminSettingsResponse = await backend.app.request("/api/admin-settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          scanDays: 45,
          reportDays: 60,
          sourceScopes: {
            fast: ["googleNews", "ptt", "googleNews"],
            full: ["googleNews", "publicProductRecallSources"],
            watch: ["threads"],
          },
        }),
      });
      expect(adminSettingsResponse.status).toBe(200);
      expect(await adminSettingsResponse.json()).toMatchObject({
        ok: true,
        settings: {
          scanDays: 45,
          reportDays: 60,
          sourceScopes: {
            fast: ["googleNews", "ptt"],
            full: ["googleNews", "publicProductRecallSources"],
            watch: ["threads"],
          },
        },
      });

      const dateOnlyAdminSettingsResponse = await backend.app.request("/api/admin-settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scanDays: 14, reportDays: 21 }),
      });
      expect(dateOnlyAdminSettingsResponse.status).toBe(200);
      expect(await dateOnlyAdminSettingsResponse.json()).toMatchObject({
        settings: {
          scanDays: 14,
          reportDays: 21,
          sourceScopes: {
            fast: ["googleNews", "ptt"],
            full: ["googleNews", "publicProductRecallSources"],
            watch: ["threads"],
          },
        },
      });

      const apiInfo = await backend.app.request("/api");
      expect(apiInfo.status).toBe(200);
      expect(await apiInfo.json()).toMatchObject({ ok: true, name: "beibeiying-sentiment-backend" });

      const health = await backend.app.request("/health");
      expect(health.status).toBe(200);
      expect(await health.json()).toMatchObject({ ok: true, status: "ok" });

      const settingsResponse = await backend.app.request("/api/sentiment/search-settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          sources: ["googleNews", "threads"],
          proxyEnabled: false,
        }),
      });
      expect(settingsResponse.status).toBe(200);
      expect(await settingsResponse.json()).toMatchObject({
        settings: { sources: ["googleNews", "threads"], proxyEnabled: false },
      });

      const aiSettingsResponse = await backend.app.request("/api/sentiment/ai-settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          enabled: true,
          baseUrl: "https://ai.example.test/v1",
          apiKey: "sk-test-secret",
          model: "report-model",
        }),
      });
      expect(aiSettingsResponse.status).toBe(200);

      const revealedAiSettingsResponse = await backend.app.request("/api/sentiment/ai-settings?reveal=1");
      expect(revealedAiSettingsResponse.status).toBe(200);
      const revealedAiSettings = await revealedAiSettingsResponse.json();
      expect(revealedAiSettings.settings.apiKey).toBe("sk-test-secret");
      expect(revealedAiSettings.settings.configured).toBe(true);

      const configResponse = await backend.app.request("/api/config");
      expect(configResponse.status).toBe(200);
      const config = await configResponse.json();
      expect(config.config.sentimentAi.apiKey).toBe("sk-***cret");

      const keywordResponse = await backend.app.request("/api/sentiment/keywords", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ keyword: "捐款 客服" }),
      });
      expect(keywordResponse.status).toBe(201);
      expect(await keywordResponse.json()).toMatchObject({
        keywords: ["捐款", "客服"],
        inserted: 2,
      });

      const ingestResponse = await backend.app.request("/api/sentiment/ingest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_type: "manual",
          items: [{
            platform: "news",
            url: "https://example.test/sentiment",
            title: "台灣捐款服務討論",
            content: "台灣公益捐款客服流程被公開討論。",
            keyword: "捐款",
            sentiment: "neutral",
            published_at: new Date().toISOString(),
          }],
        }),
      });
      expect(ingestResponse.status).toBe(201);
      expect(await ingestResponse.json()).toMatchObject({ ok: true, inserted: 1 });

      const dashboard = await backend.app.request("/api/sentiment/dashboard");
      expect(dashboard.status).toBe(200);
      const dashboardJson = await dashboard.json();
      expect(dashboardJson.stats.total).toBe(1);
      expect(dashboardJson.items[0].title).toBe("台灣捐款服務討論");

      expect(fs.existsSync(path.join(dataDir, "crm.db"))).toBe(true);
      expect(fs.existsSync(path.join(dataDir, "sentiment-config.json"))).toBe(true);
    } finally {
      backend.close();
    }
  });
});
