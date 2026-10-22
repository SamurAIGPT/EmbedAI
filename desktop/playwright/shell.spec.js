const { test, expect } = require("./fixtures");

test("the shell renders: sidebar, header and composer", async ({ page }) => {
  const sidebar = page.getByRole("navigation", { name: "Conversations" });
  await expect(sidebar.getByRole("button", { name: "New chat" })).toBeVisible();
  await expect(sidebar.getByRole("region", { name: "Agents" })).toBeVisible();
  await expect(sidebar.getByRole("region", { name: "Recent" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Agent for this chat" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Choose engine and model" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
});

test("sidebar navigation: Settings and back, New chat, Routines", async ({ page }) => {
  const sidebar = page.getByRole("navigation", { name: "Conversations" });
  await sidebar.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings\//);
  await expect(page.getByRole("heading", { name: "Settings", level: 2 })).toBeVisible();
  await page.getByRole("link", { name: "← Chat" }).click();
  await expect(page).toHaveURL(/\/chat\//);

  await sidebar.getByRole("button", { name: "New chat" }).click();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeFocused(); // New chat hands focus to the composer

  await sidebar.getByRole("button", { name: "Routines" }).click();
  await expect(page.getByRole("dialog", { name: "Routines" })).toBeVisible();
});

test("the theme switch changes the theme", async ({ page }) => {
  const theme = page.getByRole("radiogroup", { name: "Theme" });
  await theme.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await theme.getByRole("radio", { name: "System" }).click();
});

test("the skip link is the first Tab stop and goes to the conversation", async ({ page }) => {
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to the conversation" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#conversation$/);
});

test("the Notifications popover closes on Escape and focus returns to the bell", async ({ page }) => {
  const bell = page.getByRole("button", { name: "Notifications" });
  await bell.click();
  const popover = page.getByRole("dialog", { name: "Notifications" });
  await expect(popover).toBeVisible();
  await popover.getByRole("button", { name: "Mark all read" }).focus(); // Escape works from inside it too
  await page.keyboard.press("Escape");
  await expect(popover).toBeHidden();
  await expect(bell).toBeFocused();
});
