import { defineConfig } from "playwright/test";

// The locked Playwright runner otherwise captures secret-bearing DOM on failure,
// even with screenshots/traces disabled. Keep failure context to safe errors/source.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";

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
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    // Invitations/configurations/passwords must not enter uploaded artifacts.
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
