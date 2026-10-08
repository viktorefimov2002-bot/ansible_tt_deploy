import { defineConfig } from "playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  reporter: "line",
  use: {
    browserName: "chromium",
    headless: true,
    ignoreHTTPSErrors: true,
    // Invitations/configurations/passwords must not enter uploaded artifacts.
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
