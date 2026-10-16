const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard';
const at = '2026-10-16T10:00:00Z';
const full = 'Een bewaard resultaat. ' + 'Toelichting. '.repeat(80) + 'ONLY_FULL_OLD_TAIL';
const states = { 'work:done': 'verified', 'work:failed': 'failed', 'work:uncertain': 'ambiguous', 'work:older': 'verified' };
const titles = { 'work:done': 'Bewaarde afgeronde taak', 'work:failed': 'Bewaarde mislukte taak',
  'work:uncertain': 'Bewaarde onzekere taak', 'work:older': 'Archiefzoekterm oudere opdracht' };
let denied = false;
const requests = [];
function work(id, detail = false) {
  return { work_id: id, state: states[id], generation: 1, parent_work_id: null,
    stop_requested: false, result_available: states[id] === 'verified', created_at: at,
    ...(detail ? { task: titles[id], result: states[id] === 'verified' ? { output: full, completed_at: at, verified: true } : null }
      : { task_preview: titles[id] }), scope: 'private_tool_free_worker' };
}
const overview = { contract: 'EvaOwnerWorkOverview/v1', checked_at: at, open_count: 0, closed_count: 25,
  total_count: 25, state_counts: { verified: 7, failed: 5, ambiguous: 13 } };
const planning = { schedules: [
  { schedule_id: 'schedule:future', state: 'scheduled', run_at: at, task: 'Echte wachtende startafspraak', can_cancel: true },
  { schedule_id: 'schedule:past', state: 'dispatched', run_at: at, task: 'Historisch overgedragen start',
    work_id: 'work:uncertain', can_cancel: false, linked_work: { state: 'ambiguous', closed: true, result_available: false } },
], truncated: false, current_truncated: false };
const list = (operation, jobs) => ({ contract: 'EvaDelegatedWorkOwnerResult/v1', operation, jobs,
  truncated: operation === 'list', overview, open_jobs: [], open_truncated: false, planning,
  ...(operation === 'search' ? { query: 'Archiefzoekterm' } : {}) });
(async () => {
  const errors = [], external = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const resource = url.pathname.slice(base.length + 1);
    res.setHeader('cache-control', 'no-store');
    if (url.pathname === base || url.pathname === base + '/') {
      res.setHeader('content-type', 'text/html'); res.end(fs.readFileSync(path.join(__dirname, 'dist/index.html'))); return;
    }
    if (resource.startsWith('assets/')) {
      const file = path.join(__dirname, 'dist/assets', path.basename(resource));
      res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : 'text/css'); res.end(fs.readFileSync(file)); return;
    }
    let payload;
    if (req.method === 'POST') { let body = ''; for await (const chunk of req) body += chunk; payload = JSON.parse(body); }
    requests.push({ resource, payload }); res.setHeader('content-type', 'application/json');
    if (denied && resource.startsWith('work')) { res.writeHead(403); res.end('{}'); return; }
    if (resource === 'status') { res.end(JSON.stringify({ contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } })); return; }
    if (resource === 'identity') { res.end(JSON.stringify({ contract: 'EvaDashboardIdentity/v1', read_only: true, identity: {
      display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1,
      state: 'current', temperament: ['warm'], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: ['helder'] },
    } })); return; }
    if (resource === 'work') { res.end(JSON.stringify(list('list', ['work:done', 'work:failed', 'work:uncertain'].map(id => work(id))))); return; }
    if (resource === 'work/search') { assert.deepEqual(payload, { query: 'Archiefzoekterm' }); res.end(JSON.stringify(list('search', [work('work:older')]))); return; }
    if (resource === 'work/get') { res.end(JSON.stringify({ contract: 'EvaDelegatedWorkOwnerResult/v1', operation: 'get', work: work(payload.work_id, true) })); return; }
    if (resource === 'work/context') { res.end(JSON.stringify({ contract: 'EvaOwnerWorkContext/v1', work_id: payload.work_id,
      origin: { state: 'available', input: 'Oorspronkelijke eigen vraag', reply: 'Ik zoek het uit.', channel: 'Website', message_id: 'synthetic:source', occurred_at: at } })); return; }
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM,
    headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => {
      if (route.request().url().startsWith(origin + '/')) return route.continue();
      external.push(new URL(route.request().url()).origin); return route.abort();
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(15000);
    await page.goto(origin + base + '#/?section=work');
    const counting = page.getByRole('status', { name: 'Actuele werktelling' });
    await counting.getByText('0 open opdrachten', { exact: true }).waitFor();
    assert.match(await counting.innerText(), /25 afgesloten pogingen/);
    await page.getByText('Echte wachtende startafspraak', { exact: true }).waitFor();
    assert.equal(await page.getByText('Bewaarde afgeronde taak', { exact: true }).count(), 0);
    assert.equal(await page.getByText('Bewaarde onzekere taak', { exact: true }).count(), 0);
    const planHistory = page.locator('details').filter({ has: page.locator('summary', { hasText: 'Historische startafspraken (1)' }) });
    assert.equal(await planHistory.getAttribute('open'), null);
    assert.equal(await page.getByText('Historisch overgedragen start', { exact: true }).isVisible(), false);
    await planHistory.locator('summary').click();
    await planHistory.getByText('Opdracht: Afgesloten · uitkomst onzeker.', { exact: true }).waitFor();
    assert.equal(await planHistory.getByRole('button', { name: 'Stop deze stap en vervolg' }).count(), 0);
    await page.getByRole('button', { name: 'Recente afgesloten opdrachten tonen', exact: true }).click();
    await page.getByText('Bewaarde mislukte taak', { exact: true }).waitFor();
    await page.getByText('Afgesloten · mislukt · poging 1', { exact: true }).waitFor();
    await page.getByText('Afgesloten · uitkomst onzeker · poging 1', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Resultaat bekijken', exact: true }).click();
    const detail = page.getByRole('article', { name: 'Geopende hulpopdracht' });
    await detail.getByText('Jij: Oorspronkelijke eigen vraag', { exact: true }).waitFor();
    assert.equal(await page.getByText(/ONLY_FULL_OLD_TAIL/).count(), 0);
    await detail.getByRole('button', { name: 'Volledig resultaat tonen', exact: true }).click();
    await detail.getByText(/ONLY_FULL_OLD_TAIL/).waitFor();
    await page.getByRole('searchbox', { name: 'Zoek opdracht' }).fill('Archiefzoekterm');
    await page.getByRole('button', { name: 'Zoek ook in oudere opdrachten', exact: true }).click();
    await page.getByText('Archiefzoekterm oudere opdracht', { exact: true }).waitFor();
    assert.equal(requests.filter(item => item.resource === 'work/search').length, 1);
    await page.getByRole('searchbox', { name: 'Zoek opdracht' }).fill('');
    await page.getByRole('button', { name: 'Recente opdrachten', exact: true }).click();
    await page.getByText('Bewaarde mislukte taak', { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForFunction(() => !document.querySelector('.work-detail'));
    assert.equal(await page.getByText('Bewaarde mislukte taak', { exact: true }).count(), 0);
    denied = true;
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')); });
    await page.getByRole('alert').waitFor();
    assert.equal(await page.getByText(/ONLY_FULL_OLD_TAIL/).count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    assert.deepEqual(requests.filter(item => /stop|resume|schedule_cancel|revise/.test(item.resource)), []);
    console.log(JSON.stringify({ contract: 'EvaOwnerWorkOverviewBrowserProof/v1', status: 'passed',
      currentZeroOpenCount: true, historyExplicit: true, failedAndUncertainClosed: true,
      actualLinkedPlanningState: true, waitingPlanningVisible: true, historicalSearch: true,
      exactSourceAndFullResultPreserved: true, hiddenRevocationCleared: true, mobileWidth: 390,
      javascriptErrors: 0, externalRequests: 0, modelCalls: 0, liveWrites: 0 }));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
