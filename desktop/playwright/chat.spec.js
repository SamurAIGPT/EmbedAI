const { test, expect } = require("./fixtures");

// These two use the real Claude CLI.
//   npm run e2e:ui         without Claude installed and signed in they are SKIPPED, and the
//                          list says so: a skip is not a pass and proves nothing about chat.
//   npm run e2e:ui:real    the same tests, but an unavailable or signed-out Claude FAILS them.
//                          Use this to show that a real conversation works.
const REQUIRED = process.env.OPEN_DOTS_E2E_REQUIRE_CLAUDE === "1";

test.describe("a conversation (real Claude)", () => {
  test.beforeEach(async ({ page }) => {
    await page.getByRole("button", { name: "Choose engine and model" }).waitFor();
    const ready = await page.evaluate(async () => {
      const status = await fetch("/api/v1/local/status").then((r) => r.json()).catch(() => null);
      return status?.available === true;
    });
    if (!ready && REQUIRED) throw new Error("Claude Code is not installed and signed in here, and this run requires the real CLI");
    test.skip(!ready, "SKIPPED, NOT TESTED: Claude Code is not installed and signed in here");
  });

  test("a message is streamed, answered and listed in the sidebar", async ({ page }) => {
    test.setTimeout(240_000);
    await page.getByRole("textbox", { name: "Message" }).fill("Reply with just the word: pong");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page).toHaveURL(/thread=/);
    await expect(page.getByRole("log", { name: "Conversation" })).toContainText(/pong/i, { timeout: 180_000 });
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible(); // the turn has ended
    await expect(page.getByRole("region", { name: "Recent" }).getByRole("listitem")).not.toHaveCount(0);
  });

  test("Stop ends a running answer and the conversation takes another message", async ({ page }) => {
    test.setTimeout(240_000);
    await page.getByRole("textbox", { name: "Message" }).fill("Count from 1 to 300, one number per line, slowly. Do not use tools.");
    await page.getByRole("button", { name: "Send" }).click();
    const stop = page.getByRole("button", { name: "Stop" });
    await expect(stop).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("log", { name: "Conversation" })).toContainText(/\b2\b/, { timeout: 120_000 }); // text is arriving
    await stop.click();
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("textbox", { name: "Message" })).toBeEnabled();
  });
});
