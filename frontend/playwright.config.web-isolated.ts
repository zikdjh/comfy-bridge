import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /web-wizard-isolated\.spec\.ts/,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  workers: 1,
  reporter: "list",
  use: {
    ...devices["Desktop Edge"],
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 900 },
    baseURL: "http://127.0.0.1:18389",
  },
});
