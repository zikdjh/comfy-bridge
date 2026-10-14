import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /fulltest-(?:live|comfy)\.spec\.ts/,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: "list",
  outputDir: resolve("test-results/fulltest"),
  use: {
    baseURL: "http://127.0.0.1:8189",
    channel: "msedge",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
