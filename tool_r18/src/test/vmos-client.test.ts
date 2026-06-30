import { webcrypto } from "node:crypto";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  execAdb,
  getTaskResult,
  inputText,
  listPads,
  simulateClick,
  simulateSwipe,
} from "@/lib/vmos-client";

const config = { ak: "test-ak", sk: "test-sk" };

function mockVmosResponse(data: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ code: 200, msg: "success", data }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestBody(fetchMock: ReturnType<typeof mockVmosResponse>) {
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  return JSON.parse(String(init.body));
}

describe("vmos-client request body serialization", () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: webcrypto,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves spaces in async ADB commands", async () => {
    const fetchMock = mockVmosResponse([{ taskId: 123 }]);
    const command = "monkey -p com.instagram.barcelona -c android.intent.category.LAUNCHER 1";

    await execAdb(config, "PAD001", command);

    expect(requestBody(fetchMock)).toMatchObject({
      padCodes: ["PAD001"],
      scriptContent: command,
    });
  });

  it("preserves spaces and unicode in text input", async () => {
    const fetchMock = mockVmosResponse({ taskId: "456" });
    const text = "中文 caption with spaces";

    await inputText(config, "PAD001", text);

    expect(requestBody(fetchMock)).toMatchObject({
      padCodes: ["PAD001"],
      text,
    });
  });

  it("defaults VMOS click and swipe commands to the fixed 720x1600 screen", async () => {
    const clickMock = mockVmosResponse({});
    await simulateClick(config, "PAD001", 620, 1530);
    expect(requestBody(clickMock)).toMatchObject({
      padCodes: ["PAD001"],
      x: 620,
      y: 1530,
      width: 720,
      height: 1600,
    });

    const swipeMock = mockVmosResponse({});
    await simulateSwipe(config, "PAD001", "BOTTOM_TO_TOP", {
      startX: 360,
      startY: 1400,
      endX: 360,
      endY: 760,
    });
    expect(requestBody(swipeMock)).toMatchObject({
      padCodes: ["PAD001"],
      direction: "BOTTOM_TO_TOP",
      width: 720,
      height: 1600,
      startX: 360,
      startY: 1400,
      endX: 360,
      endY: 760,
    });
  });

});

describe("listPads", () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: webcrypto,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses VMOS pagination parameters and follows all pages", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: 200,
        data: { totalPage: 2, pageData: [{ padCode: "PAD001", padStatus: 10 }] },
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: 200,
        data: { totalPage: 2, pageData: [{ padCode: "PAD002", padStatus: 10 }] },
      })));
    vi.stubGlobal("fetch", fetchMock);

    const pads = await listPads(config);

    expect(pads.map((pad) => pad.padCode)).toEqual(["PAD001", "PAD002"]);
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toMatchObject({
      pageNum: 1,
      pageSize: 100,
    });
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toMatchObject({
      pageNum: 2,
      pageSize: 100,
    });
  });

  it("keeps VMOS list id from the paginated pad list", async () => {
    const fetchMock = mockVmosResponse({
      totalPage: 1,
      pageData: [{ id: 195388, padCode: "PAD001", padStatus: 10 }],
    });

    const pads = await listPads(config);

    expect(pads[0]).toMatchObject({ id: 195388, padCode: "PAD001" });
    expect(requestBody(fetchMock)).toMatchObject({ pageNum: 1, pageSize: 100 });
  });

  it("uses Electron VMOS IPC without renderer-side credentials", async () => {
    const listPadsMock = vi.fn().mockResolvedValue([{ padCode: "PAD_IPC", padStatus: 10 }]);
    (window as any).electronAPI = { vmos: { listPads: listPadsMock } };
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const pads = await listPads({});

    expect(pads.map((pad) => pad.padCode)).toEqual(["PAD_IPC"]);
    expect(listPadsMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
    delete (window as any).electronAPI;
  });

  it("merges pads from multiple VMOS accounts and reuses the matched credential for pad tasks", async () => {
    const multiConfig = {
      accounts: [
        { name: "one", ak: "ak-one", sk: "sk-one" },
        { name: "two", ak: "ak-two", sk: "sk-two" },
      ],
    };
    const fetchMock = vi.fn(async (_input: unknown, init?: RequestInit) => {
      const auth = String(init?.headers?.["authorization" as any] || "");
      const body = JSON.parse(String(init?.body || "{}")) as Record<string, unknown>;
      if (body.pageNum) {
        const padCode = auth.includes("Credential=ak-one") ? "PAD_ONE" : "PAD_TWO";
        return new Response(JSON.stringify({
          code: 200,
          data: { totalPage: 1, pageData: [{ padCode, padStatus: 10 }] },
        }));
      }
      if (Array.isArray(body.padCodes)) {
        return new Response(JSON.stringify({
          code: 200,
          data: [{ padCode: "PAD_TWO", taskId: 222 }],
        }));
      }
      return new Response(JSON.stringify({
        code: 200,
        data: [{ taskId: 222, taskStatus: 3 }],
      }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const pads = await listPads(multiConfig);
    await execAdb(multiConfig, "PAD_TWO", "echo ok");
    await getTaskResult(multiConfig, 222);

    expect(pads.map((pad) => [pad.padCode, pad.vmosAccountName])).toEqual([
      ["PAD_ONE", "one"],
      ["PAD_TWO", "two"],
    ]);
    expect(String((fetchMock.mock.calls[2][1] as RequestInit).headers?.["authorization" as any] || "")).toContain("Credential=ak-two");
    expect(String((fetchMock.mock.calls[3][1] as RequestInit).headers?.["authorization" as any] || "")).toContain("Credential=ak-two");
  });

  it("falls back to another VMOS account when padCode is not found in the primary account", async () => {
    const multiConfig = {
      accounts: [
        { name: "one", ak: "ak-one-fallback", sk: "sk-one" },
        { name: "two", ak: "ak-two-fallback", sk: "sk-two" },
      ],
    };
    const fetchMock = vi.fn(async (_input: unknown, init?: RequestInit) => {
      const auth = String(init?.headers?.["authorization" as any] || "");
      if (auth.includes("Credential=ak-one-fallback")) {
        return new Response(JSON.stringify({ code: 2020, msg: "Instance not found", data: null }));
      }
      return new Response(JSON.stringify({
        code: 200,
        msg: "success",
        data: [{ padCode: "PAD_FALLBACK", taskId: 987 }],
      }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const taskId = await execAdb(multiConfig, "PAD_FALLBACK", "echo ok");

    expect(taskId).toBe(987);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String((fetchMock.mock.calls[1][1] as RequestInit).headers?.["authorization" as any] || "")).toContain("Credential=ak-two-fallback");
  });

  it("reports a clean missing-pad message after all VMOS accounts return instance not found", async () => {
    const multiConfig = {
      accounts: [
        { name: "one", ak: "ak-one-missing", sk: "sk-one" },
        { name: "two", ak: "ak-two-missing", sk: "sk-two" },
      ],
    };
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ code: 2020, msg: "Instance not found", data: null }))),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(execAdb(multiConfig, "PAD_MISSING", "echo ok")).rejects.toThrow("当前人设绑定的智能體手機不存在");

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
