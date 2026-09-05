import { defineConfig } from "@playwright/test";

const externalServer = process.env.PUW_E2E_EXTERNAL_SERVER === "1";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 30_000,
  globalTimeout: 300_000,
  expect: { timeout: 5_000 },
  outputDir: "node_modules/.cache/storage-picker-e2e/results",
  reporter: [["list"], ["html", { outputFolder: "node_modules/.cache/storage-picker-e2e/report", open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:4179",
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: { args: ["--disable-background-networking", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"] },
  },
  webServer: externalServer ? undefined : {
    // Invoke Vite's pinned entrypoint directly. The extra pnpm child process kept
    // Chromium runs alive during Playwright teardown on Windows worktrees.
    command: "node node_modules/vite/bin/vite.js build --config e2e/vite.config.mjs --configLoader runner && node node_modules/vite/bin/vite.js preview --config e2e/vite.config.mjs --configLoader runner --host 127.0.0.1 --port 4179 --strictPort",
    url: "http://127.0.0.1:4179/new/",
    reuseExistingServer: false,
    timeout: 90_000,
  },
});
