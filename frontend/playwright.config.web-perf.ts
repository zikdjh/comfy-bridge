import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /web-perf-nav\.spec\.ts/,
  timeout: 180_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: "list",
  outputDir: "test-results/web-perf-nav",
  use: {
    ...devices["Desktop Edge"],
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
});
