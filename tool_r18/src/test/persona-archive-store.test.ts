import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { installNodePersonaArchiveBridge } from "@/runtime/node/persona-archive-store";

const originalRuntimeDir = process.env.TOOL_R18_RUNTIME_DIR;

afterEach(() => {
  if (originalRuntimeDir === undefined) delete process.env.TOOL_R18_RUNTIME_DIR;
  else process.env.TOOL_R18_RUNTIME_DIR = originalRuntimeDir;
  delete (globalThis as any).window?.electronAPI?.personaArchives;
});

describe("node persona archive bridge", () => {
  it("preserves concurrent media while saving an intro update from an older snapshot", async () => {
    const runtimeDir = fs.mkdtempSync(path.join(os.tmpdir(), "persona-archive-store-"));
    process.env.TOOL_R18_RUNTIME_DIR = runtimeDir;
    const storePath = path.join(runtimeDir, "persona_archives.json");
    const base = {
      id: "persona-1",
      name: "Persona",
      content: "old intro",
      createdAt: "2026-07-10T00:00:00.000Z",
      updatedAt: "2026-07-10T00:00:00.000Z",
      setup: { personaDescription: "old intro" },
      posts: [{ id: "post-1", content: "post", updatedAt: "2026-07-10T00:00:00.000Z" }],
    };
    const overlap = { ...base, id: "persona-2" };
    const deleted = { ...base, id: "persona-3" };
    const deletedArchive = { ...base, id: "persona-4" };
    fs.writeFileSync(storePath, JSON.stringify([base, overlap, deleted, deletedArchive]), "utf8");
    delete (globalThis as any).window?.electronAPI?.personaArchives;
    installNodePersonaArchiveBridge();
    const api = (globalThis as any).window.electronAPI.personaArchives;
    const stale = await api.load("persona-1");
    const overlapFirst = await api.load("persona-2");
    const overlapSecond = await api.load("persona-2");
    const deletedStale = await api.load("persona-3");
    const deletedArchiveStale = await api.load("persona-4");

    const concurrent = {
      ...base,
      updatedAt: "2026-07-10T00:01:00.000Z",
      personaReferenceSheet: "https://cdn.example.com/persona.png",
      personaImageLibrary: [{ id: "image-1", imageUrl: "https://cdn.example.com/persona.png" }],
      posts: [{ ...base.posts[0], imageUrl: "https://cdn.example.com/post.png" }],
    };
    fs.writeFileSync(storePath, JSON.stringify([concurrent, overlap, deleted]), "utf8");
    const now = new Date(Date.now() + 2000);
    fs.utimesSync(storePath, now, now);

    const response = await api.save({
      ...stale,
      content: "new intro",
      setup: { ...stale.setup, personaDescription: "new intro" },
      updatedAt: "2026-07-10T00:02:00.000Z",
    }, { baseUpdatedAt: stale.updatedAt });

    const savedRows = JSON.parse(fs.readFileSync(storePath, "utf8"));
    const saved = savedRows.find((item: any) => item.id === "persona-1");
    expect(saved.content).toBe("new intro");
    expect(saved.personaReferenceSheet).toBe("https://cdn.example.com/persona.png");
    expect(saved.personaImageLibrary).toHaveLength(1);
    expect(saved.posts[0].imageUrl).toBe("https://cdn.example.com/post.png");
    expect(response.archive.personaReferenceSheet).toBe("https://cdn.example.com/persona.png");

    await api.save({
      ...overlapFirst,
      content: "first overlapping update",
      updatedAt: "2026-07-10T00:03:00.000Z",
    }, { baseUpdatedAt: overlapFirst.updatedAt });
    await api.save({
      ...overlapSecond,
      personaReferenceSheet: "https://cdn.example.com/overlap.png",
      updatedAt: "2026-07-10T00:04:00.000Z",
    }, { baseUpdatedAt: overlapSecond.updatedAt });
    const overlapSaved = JSON.parse(fs.readFileSync(storePath, "utf8")).find((item: any) => item.id === "persona-2");
    expect(overlapSaved.content).toBe("first overlapping update");
    expect(overlapSaved.personaReferenceSheet).toBe("https://cdn.example.com/overlap.png");

    const beforeDeleteSave = JSON.parse(fs.readFileSync(storePath, "utf8"));
    const deletedCurrent = beforeDeleteSave.find((item: any) => item.id === "persona-3");
    deletedCurrent.posts = [];
    deletedCurrent.updatedAt = "2026-07-10T00:05:00.000Z";
    fs.writeFileSync(storePath, JSON.stringify(beforeDeleteSave), "utf8");
    fs.utimesSync(storePath, new Date(Date.now() + 4000), new Date(Date.now() + 4000));
    await api.save({
      ...deletedStale,
      posts: [{ ...deletedStale.posts[0], imageUrl: "https://cdn.example.com/deleted.png" }],
      updatedAt: "2026-07-10T00:06:00.000Z",
    }, { baseUpdatedAt: deletedStale.updatedAt });
    const deletedSaved = JSON.parse(fs.readFileSync(storePath, "utf8")).find((item: any) => item.id === "persona-3");
    expect(deletedSaved.posts).toEqual([]);

    const beforeArchiveDeleteSave = JSON.parse(fs.readFileSync(storePath, "utf8"))
      .filter((item: any) => item.id !== "persona-4");
    fs.writeFileSync(storePath, JSON.stringify(beforeArchiveDeleteSave), "utf8");
    fs.utimesSync(storePath, new Date(Date.now() + 6000), new Date(Date.now() + 6000));
    await expect(api.save({
      ...deletedArchiveStale,
      content: "stale update",
      updatedAt: "2026-07-10T00:07:00.000Z",
    }, { baseUpdatedAt: deletedArchiveStale.updatedAt })).rejects.toThrow("was deleted");
    expect(JSON.parse(fs.readFileSync(storePath, "utf8")).some((item: any) => item.id === "persona-4")).toBe(false);

    expect((await api.list()).some((item: any) => item.id === "persona-2")).toBe(true);
    const deletedIdsPath = path.join(runtimeDir, "persona_dashboard_deleted_personas.json");
    fs.writeFileSync(deletedIdsPath, JSON.stringify(["persona-2"]), "utf8");
    const tombstoneTime = new Date(Date.now() + 8000);
    fs.utimesSync(deletedIdsPath, tombstoneTime, tombstoneTime);
    expect((await api.list()).some((item: any) => item.id === "persona-2")).toBe(false);

    fs.rmSync(runtimeDir, { recursive: true, force: true });
  });
});
