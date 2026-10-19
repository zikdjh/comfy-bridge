import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /web-darkmode\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: "list",
  outputDir: "test-results/web-darkmode",
  use: {
    ...devices["Desktop Edge"],
    channel: "msedge",
    headless: true,
  },
});
