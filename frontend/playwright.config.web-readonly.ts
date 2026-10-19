import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /web-readonly-clicks\.spec\.ts/,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: "list",
  outputDir: "test-results/web-readonly-clicks",
  use: {
    ...devices["Desktop Edge"],
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
});
