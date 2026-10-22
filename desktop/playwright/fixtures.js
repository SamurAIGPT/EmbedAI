// One real Open Duet window for the whole run: Electron started by e2e/app.js with a fresh
// data folder, attached to over DevTools. The first-run setup is dismissed once.
const { test: base, expect, chromium } = require("@playwright/test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { launchApp } = require("../e2e/app");

const test = base.extend({
  app: [async ({}, use) => {
    const dataDir = path.join(os.tmpdir(), "open-dots-e2e", "playwright");
    fs.rmSync(dataDir, { recursive: true, force: true });
    fs.mkdirSync(dataDir, { recursive: true });
    const app = await launchApp({ dataDir });
    let browser;
    try {
      await app.page(); // the window is up and the backend has proved itself
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${app.debugPort}`);
      const page = browser.contexts()[0].pages().find((p) => p.url().startsWith("http://127.0.0.1:"));
      await page.getByRole("button", { name: "Set up later" }).click();
      await expect(page.getByRole("log", { name: "Conversation" })).toBeVisible();
      app.playwrightPage = page;
      await use(app);
    } finally {
      await browser?.close().catch(() => {});
      app.destroy();
    }
  }, { scope: "worker", timeout: 180_000 }],

  // Every test starts on an empty chat screen, whatever the one before left open.
  page: async ({ app }, use) => {
    const page = app.playwrightPage;
    await page.goto(new URL("/chat/", page.url()).href);
    await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
    await use(page);
  },
});

module.exports = { test, expect };
