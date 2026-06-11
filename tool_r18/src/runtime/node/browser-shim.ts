/**
 * Node-compatible shim for gemini-client and vmos-publisher browser dependencies.
 * This file is loaded via tsx register hook before skill scripts that need publishPost.
 */

// Provide minimal window/document/Image globals so vmos-publisher.ts can load
if (typeof globalThis.window === "undefined") {
  (globalThis as any).window = {};
}
if (typeof globalThis.document === "undefined") {
  (globalThis as any).document = {
    createElement: (tag: string) => {
      if (tag === "canvas") {
        return {
          width: 0,
          height: 0,
          getContext: () => ({
            drawImage: () => {},
            getImageData: () => ({ data: new Uint8ClampedArray(0) }),
            putImageData: () => {},
            fillRect: () => {},
            clearRect: () => {},
          }),
          toDataURL: () => "data:image/png;base64,",
        };
      }
      return {};
    },
  };
}
if (typeof globalThis.Image === "undefined") {
  (globalThis as any).Image = class {
    src = "";
    onload: (() => void) | null = null;
    onerror: ((e: any) => void) | null = null;
    width = 1;
    height = 1;
    set crossOrigin(_v: string) {}
    constructor() {
      setTimeout(() => this.onload?.(), 0);
    }
  };
}
if (typeof globalThis.URL === "undefined" || !globalThis.URL.createObjectURL) {
  const OrigURL = globalThis.URL;
  (globalThis as any).URL = class extends OrigURL {
    static createObjectURL() { return "blob:node-shim"; }
    static revokeObjectURL() {}
  };
}
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  (globalThis as any).localStorage = {
    __nodeShim: true,
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
    get length() { return store.size; },
    key: (i: number) => [...store.keys()][i] ?? null,
  };
}

export {};
