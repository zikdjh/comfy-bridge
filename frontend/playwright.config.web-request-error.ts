import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /web-fulltest-request-error\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/web-request-error.json" }]],
  use: {
    ...devices["Desktop Edge"],
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
});
