const { test, expect } = require("./fixtures");

test("the Usage inspector opens, closes with its button and with Escape, and gives focus back", async ({ page }) => {
  const opener = page.getByRole("button", { name: "Usage" });
  await opener.click();
  const inspector = page.getByRole("dialog", { name: "Usage" });
  await expect(inspector).toBeVisible();
  await expect(page).toHaveURL(/panel=usage/);
  await inspector.getByRole("button", { name: "Close" }).click();
  await expect(inspector).toBeHidden();
  await expect(page).not.toHaveURL(/panel=/);

  await opener.click();
  await expect(inspector).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();
  await expect(opener).toBeFocused();
});

test("Approvals dialog: focus moves in, Tab stays inside, Escape closes and focus returns", async ({ page }) => {
  const opener = page.getByRole("button", { name: "Approvals" });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Approvals" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(":focus")).toHaveCount(1); // focus is inside, not on the page behind

  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1); // never leaves the dialog
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("Approvals: a saved rule can be added and removed, and the tabs switch", async ({ page }) => {
  await page.getByRole("button", { name: "Approvals" }).click();
  const dialog = page.getByRole("dialog", { name: "Approvals" });
  await dialog.getByRole("textbox", { name: "Command" }).fill("echo playwright-smoke");
  await dialog.getByRole("button", { name: "Add rule" }).click();
  await expect(dialog.getByText("echo playwright-smoke")).toBeVisible();
  await dialog.getByRole("button", { name: /remove|delete/i }).first().click();
  await expect(dialog.getByText("echo playwright-smoke")).toBeHidden();
  await dialog.getByRole("tab", { name: "Decisions" }).click();
  await expect(dialog.getByRole("tab", { name: "Decisions" })).toHaveAttribute("aria-selected", "true");
  await dialog.getByRole("tab", { name: "Health" }).click();
  await expect(dialog.getByRole("tabpanel")).toBeVisible();
});

test("an agent is created in its dialog, then a routine for it is created, opened and deleted", async ({ page }) => {
  const sidebar = page.getByRole("navigation", { name: "Conversations" });
  await sidebar.getByRole("button", { name: "New agent" }).first().click(); // straight to the form
  const agents = page.getByRole("dialog", { name: "Agents" }); // where Save lands
  const form = page.getByRole("dialog", { name: "New agent" });
  await expect(form.getByRole("button", { name: "Save" })).toBeDisabled();
  await form.getByRole("textbox", { name: "Name" }).fill("Smoke agent");
  await form.getByRole("button", { name: "Save" }).click();
  await expect(agents.getByText("Smoke agent")).toBeVisible();
  await agents.getByRole("button", { name: "Close" }).click();
  await expect(sidebar.getByRole("region", { name: "Agents" }).getByText("Smoke agent")).toBeVisible();

  await sidebar.getByRole("button", { name: "Routines" }).click();
  const routines = page.getByRole("dialog", { name: "Routines" });
  await routines.getByRole("button", { name: "New routine" }).click();
  const routineForm = page.getByRole("dialog", { name: "New routine" }); // the dialog is named after what it shows
  await routineForm.getByLabel("Routine name").fill("Smoke routine");
  await routineForm.getByLabel("Prompt").fill("Say hello");
  await routineForm.getByRole("button", { name: "Save" }).click();
  const row = routines.getByRole("listitem", { name: "Smoke routine" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "History", exact: true }).click();
  await routines.getByRole("button", { name: "← Back" }).click();
  await row.getByRole("button", { name: "Delete", exact: true }).click();
  await row.getByRole("button", { name: "Keep" }).click(); // the confirmation can be declined
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Delete", exact: true }).click();
  await row.getByRole("button", { name: "Delete it and its history" }).click();
  await expect(row).toBeHidden();
});
