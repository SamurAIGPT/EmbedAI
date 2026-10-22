// Playwright regression checks of the redesigned UI, run against the real Electron app
// (its own backend and data folder, driven over DevTools). No browser download is needed.
//
//   npm run e2e:ui                      all of them
//   npm run e2e:ui -- chat              only playwright/chat.spec.js
//
// `npm run setup` and `npm run build` must have been run. chat.spec.js uses the real Claude
// CLI (two short requests) and skips itself when Claude is not installed and signed in.
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "playwright",
  testMatch: "*.spec.js",
  workers: 1, // one app (one window, one backend) shared by every test
  fullyParallel: false,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  outputDir: "playwright/.results",
});
