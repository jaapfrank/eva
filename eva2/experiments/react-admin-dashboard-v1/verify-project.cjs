const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const origin = 'http://127.0.0.1:44712';
const output = process.env.EVA_PROJECT_SCREENSHOTS;

async function main() {
  const payload = await (await fetch(`${origin}/project-state`)).json();
  assert.equal(payload.contract, 'EvaProjectOverview/v1');
  const board = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../design/product/execution-workboard-v1.json')));
  assert.equal(payload.active.id, board.activeTaskId);
  assert.deepEqual(payload.project, board.audit.projectDashboard);
  assert.equal((await fetch(`${origin}/project-state`, { method: 'POST' })).status, 405);
  assert.equal((await fetch(`${origin}/project-state`, { headers: { Origin: 'https://external.invalid' } })).status, 403);
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    const page = await context.newPage();
    const errors = []; const external = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => { if (!request.url().startsWith(origin)) external.push(request.url()); });
    await page.goto(origin);
    await page.getByRole('heading', { name: 'Waar staat Eva écht?' }).waitFor();
    await page.getByRole('heading', { name: payload.active.title }).waitFor();
    assert.equal(await page.locator('button.capability-row').count(), payload.project.capabilities.length);
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: /Gesprekken.*Warmte/ }).click();
    await page.getByRole('dialog', { name: 'Gesprekken' }).waitFor();
    await page.getByRole('button', { name: 'Sluiten ×' }).click();
    await page.getByRole('button', { name: /Concurrenten & keuzes/ }).click();
    for (const name of ['Hermes', 'OpenClaw', 'Agent Zero']) await page.getByRole('heading', { name, exact: true }).waitFor();
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-keuzes.png'), fullPage: true });
    await page.getByRole('button', { name: /Werk & volgorde/ }).click();
    assert.equal(await page.locator('.roadmap-phase').count(), payload.project.roadmap.length);
    assert.equal(await page.locator('.roadmap-phase[open]').count(), 0);
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-werk.png'), fullPage: true });
    await page.locator('.roadmap-phase summary').first().click();
    await page.getByRole('heading', { name: 'Menselijke gesprekroute compleet maken' }).waitFor();
    await page.getByRole('textbox', { name: 'Zoek een taak' }).fill('CO-004');
    assert.equal(await page.locator('.task-grid article').count(), 1);
    assert.equal(await page.locator('.roadmap-phase').count(), 1);
    await page.getByRole('heading', { name: 'Eigen geheugen in het dashboard tonen' }).waitFor();
    await page.getByRole('textbox', { name: 'Zoek een taak' }).fill('bestaat-niet');
    await page.getByRole('status').filter({ hasText: 'Geen taken gevonden.' }).waitFor();
    await page.getByRole('textbox', { name: 'Zoek een taak' }).fill('');
    assert.equal(await page.locator('.roadmap-phase').count(), payload.project.roadmap.length);
    await page.getByRole('button', { name: /Overzicht/ }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: /Werk & volgorde/ }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-werk-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: /Overzicht/ }).click();
    // Synthetic response change proves the mounted UI consumes fresh source data,
    // not hardcoded task text; the actual workboard is never changed by this test.
    await page.route('**/project-state', (route) => route.fulfill({ json: { ...payload,
      project: { ...payload.project, runtimeLabel: 'Synchronisatie-slot' },
      active: { ...payload.active, title: 'Synchronisatiecontrole' }, revision: 'f'.repeat(64),
    } }));
    await page.getByRole('heading', { name: 'Synchronisatiecontrole' }).waitFor({ timeout: 12000 });
    await page.getByText(/Synchronisatie-slot · controle/).waitFor();
    await page.unroute('**/project-state');
    await page.route('**/project-state', (route) => route.fulfill({ status: 503, json: {} }));
    await page.getByRole('alert').waitFor({ timeout: 12000 });
    assert.match(await page.getByRole('alert').innerText(), /vorige controle/);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.equal((await context.cookies()).length, 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log(JSON.stringify({ status: 'passed', activeTask: payload.active.id,
      capabilities: payload.project.capabilities.length, roadmapPhases: payload.project.roadmap.length,
      viewports: ['1440x1100', '390x844'],
      sourceMatchesWorkboard: true, refreshAndFailureTested: true, externalRequests: 0,
      browserStorageEntries: 0, modelCalls: 0, dataMutations: 0 }));
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
