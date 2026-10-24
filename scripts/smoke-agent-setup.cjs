const { chromium, expect } = require('../desktop/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const [url, statePath, root] = process.argv.slice(2);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    await context.addCookies(JSON.parse(fs.readFileSync(statePath)).cookies);
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(`${url}/chat/`);
    const later = page.getByRole('button', { name: 'Set up later' });
    if (await later.isVisible()) await later.click();
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible();
    await page.evaluate(async () => {
      const session = await fetch('/api/v1/auth/session').then(response => response.json());
      const response = await fetch('/api/v1/agents', { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-csrf-token': session.csrf_token }, body: JSON.stringify({ name: 'File reviewer', instructions: 'Review local files.' }) });
      if (!response.ok) throw new Error(await response.text());
    });
    await page.reload();
    await page.getByRole('button', { name: 'New agent', exact: true }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Name', { exact: true }).fill('Coordinator');
    const role = dialog.getByRole('checkbox', { name: 'Team lead (Chief of Staff)', exact: true });
    await expect(role).toBeEnabled();
    await role.check();
    await dialog.getByRole('checkbox', { name: 'File reviewer', exact: true }).check();
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(dialog.getByText('Coordinator', { exact: true }).first()).toBeVisible();
    const team = await page.evaluate(() => fetch('/api/v1/team').then(response => response.json()));
    expect(team.agents.map(agent => agent.name).sort()).toEqual(['Coordinator', 'File reviewer']);
    expect(team.agents.find(agent => agent.id === team.team.lead_agent_id).name).toBe('Coordinator');
    await dialog.getByRole('button', { name: 'Chat with Coordinator' }).click();
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(/I'd like to create an agent/);
    const threads = await page.evaluate(() => fetch('/api/v1/threads').then(response => response.json()));
    expect(threads).toHaveLength(0);
    await page.getByRole('button', { name: 'Settings for Coordinator', exact: true }).click();
    await expect(role).toBeChecked();
    await role.uncheck();
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect.poll(() => page.evaluate(() => fetch('/api/v1/team').then(response => response.json()).then(data => data.team.lead_agent_id))).toBeNull();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByRole('button', { name: 'Make Coordinator lead', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Remove lead role from Coordinator', exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(root, 'agent-setup-phone.png') });
    expect(pageErrors).toEqual([]);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
