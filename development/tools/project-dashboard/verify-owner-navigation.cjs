const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const dist = process.env.EVA_DASHBOARD_DIST || path.join(__dirname, 'dist');
const base = '/internal/v1/dashboard';
const sections = ['Gesprek', 'Personen & geheugen', 'Opdrachten & resultaten', 'Initiatief & leren', 'Verbindingen'];
const fixtures = {
  status: { contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } },
  identity: { contract: 'EvaDashboardIdentity/v1', read_only: true, identity: { display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1, state: 'current', temperament: ['warm'], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: ['helder'] } } },
  'memory-view': { contract: 'EvaOwnerMemoryView/v1', owner: { display_name: 'Jij' }, mentioned_names: [], memories: [], complete_directory: false },
  'people-memory': { contract: 'EvaPeopleMemoryOverview/v1', read_only: true, contains_raw_content: false, people: { dynamic_account: 'no_extra_account' }, sharing: { state: 'private_only' }, memory: { state: 'up_to_date' } },
  'memory-sources': { contract: 'EvaOwnerMemorySources/v1', read_only: true, meaning: 'Synthetic sources', sources: [{ source_id: '00000000-0000-0000-0000-000000000001', occurred_at: '2026-10-16T00:00:00Z', excerpt: 'Synthetic private source', truncated: false, retention_state: 'retained' }], next_cursor: null },
  work: { contract: 'EvaDelegatedWorkOwnerResult/v1', operation: 'list', jobs: [], truncated: false },
  initiatives: { contract: 'EvaOwnerInitiatives/v1', cards: [], checked_at: '2026-10-16T00:00:00Z', latest_review: { updated_at: '2026-10-16T00:00:00Z', phase: 'review', state: 'silence', reason: 'Synthetic deliberate silence', blocker: null, intent_id: null } },
  components: { contract: 'EvaComponentObservations/v1', checked_at: '2026-10-16T00:00:00Z', components: [{ id: 'telegram', label: 'Telegram', state: 'unknown', detail: 'Not measured', observed_at: null }] },
  browser: { state: 'stopped' },
};
(async () => {
  let server, origin = process.env.EVA_DASHBOARD_ORIGIN;
  const seen = [], errors = [], external = [];
  let denyPeople = false;
  if (!origin) {
    server = http.createServer((req, res) => {
      const url = new URL(req.url, 'http://127.0.0.1');
      seen.push(url.pathname); res.setHeader('Cache-Control', 'no-store');
      if (url.pathname === base || url.pathname === base + '/') {
        res.setHeader('Content-Type', 'text/html'); res.end(fs.readFileSync(path.join(dist, 'index.html'))); return;
      }
      if (url.pathname.startsWith(base + '/assets/')) {
        const file = path.join(dist, 'assets', path.basename(url.pathname));
        res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css');
        res.end(fs.readFileSync(file)); return;
      }
      if (req.method !== 'GET') { res.writeHead(405); res.end(); return; }
      const resource = url.pathname.slice(base.length + 1);
      if (denyPeople && resource === 'memory-view') { res.writeHead(503); res.end('{}'); return; }
      if (!fixtures[resource]) { res.writeHead(404); res.end('{}'); return; }
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(fixtures[resource]));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    origin = 'http://127.0.0.1:' + server.address().port;
  }
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 },
      ...(process.env.EVA_DASHBOARD_TOKEN_FILE ? { httpCredentials: { username: 'eva-owner', password: fs.readFileSync(process.env.EVA_DASHBOARD_TOKEN_FILE, 'utf8').trim() } } : {}) });
    await context.route('**/*', route => {
      if (route.request().url().startsWith(origin + '/')) return route.continue();
      external.push(new URL(route.request().url()).origin); return route.abort();
    });
    const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message)); page.setDefaultTimeout(12000);
    await page.goto(origin + base); const nav = page.getByRole('navigation', { name: 'Eva beheer' });
    await nav.waitFor(); await page.getByRole('heading', { name: 'Overzicht', exact: true }).waitFor();
    assert.equal(await nav.locator('a').count(), 6);
    if (server) assert.ok(!seen.some(url => /memory-sources|initiatives|components/.test(url)), 'overview does not fetch unseen private panels');
    for (const label of sections) {
      await nav.getByRole('link', { name: label, exact: true }).click();
      await page.getByRole('heading', { name: label, exact: true }).waitFor();
      assert.equal(await nav.getByRole('link', { name: label, exact: true }).getAttribute('aria-current'), 'page');
      if (label === 'Gesprek') assert.equal(await page.getByRole('link', { name: 'Open gesprek →' }).getAttribute('href'), base + '/conversation');
      if (label === 'Personen & geheugen') {
        await page.getByRole('heading', { name: 'Jouw geheugenbronnen' }).waitFor();
        if (server) await page.getByText('Synthetic private source', { exact: true }).waitFor();
      }
      if (label === 'Opdrachten & resultaten') await page.getByRole('region', { name: 'Mijn hulpopdrachten', exact: true }).waitFor();
      if (label === 'Initiatief & leren') await page.getByRole('heading', { name: 'Leren en dromen' }).waitFor();
      if (label === 'Verbindingen') {
        await page.getByRole('heading', { name: 'Verbindingen en onderdelen' }).waitFor();
        if (server) await page.getByText('Niet gemeten', { exact: true }).waitFor();
      }
    }
    assert.equal(await page.getByText('Synthetic private source', { exact: true }).count(), 0, 'memory is unmounted outside its section');
    await page.reload(); await page.getByRole('heading', { name: 'Verbindingen', exact: true }).waitFor();
    await page.goBack(); await page.getByRole('heading', { name: 'Initiatief & leren', exact: true }).waitFor();
    if (server) {
      denyPeople = true;
      await nav.getByRole('link', { name: 'Personen & geheugen', exact: true }).click();
      await page.getByText(/Dit geheugenoverzicht is nu niet beschikbaar/).waitFor();
      await page.getByText('Synthetic private source', { exact: true }).waitFor();
      await nav.getByRole('link', { name: 'Opdrachten & resultaten', exact: true }).click();
      await page.getByRole('region', { name: 'Mijn hulpopdrachten', exact: true }).waitFor();
    }
    await nav.getByRole('link', { name: 'Overzicht', exact: true }).click();
    if (process.env.EVA_DASHBOARD_SCREENSHOT) await page.screenshot({ path: process.env.EVA_DASHBOARD_SCREENSHOT });
    await page.setViewportSize({ width: 390, height: 844 });
    await nav.getByRole('link', { name: 'Personen & geheugen', exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, 'mobile has no horizontal page overflow');
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ contract: 'EvaOwnerNavigationBrowserProof/v1', status: 'passed', sections: 6, reloadAndBack: true, mobileWidth: 390, scopedFailureChecked: Boolean(server), sourceUnmountChecked: Boolean(server), javascriptErrors: errors.length, externalRequests: external.length, modelCalls: 0, mode: server ? 'isolated' : 'installed' }));
  } finally { await browser.close(); if (server) await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
