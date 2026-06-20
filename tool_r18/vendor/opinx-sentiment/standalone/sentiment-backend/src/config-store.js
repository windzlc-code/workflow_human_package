import fs from "node:fs";
import path from "node:path";

export class JsonConfigStore {
  constructor(filePath) {
    this.filePath = filePath;
    this.data = {};
    this.loaded = false;
  }

  load() {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const raw = fs.readFileSync(this.filePath, "utf8");
      const parsed = JSON.parse(raw);
      this.data = parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
      if (error?.code !== "ENOENT") {
        throw error;
      }
      this.data = {};
    }
  }

  get(key) {
    this.load();
    return this.data[key];
  }

  set(key, value) {
    this.load();
    this.data[key] = value;
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, `${JSON.stringify(this.data, null, 2)}\n`, "utf8");
  }

  all() {
    this.load();
    return { ...this.data };
  }
}
