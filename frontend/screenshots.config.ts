import { defineConfig } from "@playwright/test";
export default defineConfig({ testDir: ".", testMatch: "screenshots.spec.ts", timeout: 120_000,
  use: { baseURL: "http://127.0.0.1:4174" }, reporter: [["list"]] });
