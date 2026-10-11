import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 300_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: "list",
  outputDir: "test-results-queue",
  use: {
    channel: "msedge",
    viewport: { width: 1600, height: 1000 },
    trace: "off",
    screenshot: "off",
  },
});
