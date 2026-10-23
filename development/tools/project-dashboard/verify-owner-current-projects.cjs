// Isolated built-UI check. All account/project data and mutations are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard';
const dist = process.env.EVA_DASHBOARD_DIST || path.join(__dirname, 'dist');
const id = c => 'personal-interest:' + c.repeat(64);
const card = (c, title, status, at) => ({ intent_id: id(c), title, status,
  motivation: 'Synthetic recorded purpose', question: 'Synthetic next exploration',
  updated_at: at, adopted_at: at, result_at: null,
  has_artifact: false, can_stop: status === 'working' || status === 'attention',
  feedback: null, feedback_later_use: 'Not assessed.' });
const history = { contract: 'EvaOwnerInitiativeHistory/v1', checked_at: '2026-10-23T10:00:00Z',
  latest_review: null, truncated: false,
  cards: [card('a', 'Recent eigen project', 'working', '2026-10-23T09:00:00Z'),
    card('b', 'Project met aandachtspunt', 'attention', '2026-10-22T09:00:00Z'),
    card('c', 'Ouder open project', 'working', '2026-10-21T09:00:00Z'),
    card('d', 'Afgerond project', 'done', '2026-10-23T10:00:00Z')],
  learning: { current_styles: [], reflections: [], components: [] } };
history.cards[0].has_artifact = true;
history.cards[0].result_preview = 'Synthetic stored result';

(async () => {
  let denied = false, writes = 0, artifactReads = 0;
  const errors = [], external = [];
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://localhost');
    res.setHeader('Cache-Control', 'no-store');
    if (u.pathname === base || u.pathname === base + '/') {
      res.setHeader('Content-Type', 'text/html');
      return res.end(fs.readFileSync(path.join(dist, 'index.html')));
    }
    if (u.pathname.startsWith(base + '/assets/')) {
      const file = path.join(dist, 'assets', path.basename(u.pathname));
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css');
      return res.end(fs.readFileSync(file));
    }
    res.setHeader('Content-Type', 'application/json');
    if (u.pathname === base + '/initiatives/history') {
      if (denied) { res.writeHead(503); return res.end('{}'); }
      return res.end(JSON.stringify(history));
    }
    if (u.pathname.endsWith('/artifact')) { artifactReads++; return res.end('Synthetic artifact'); }
    if (u.pathname === base + '/initiatives/stop' && req.method === 'POST') {
      let body = '';
      req.on('data', bytes => body += bytes);
      req.on('end', () => {
        const value = JSON.parse(body);
        assert.deepEqual(value, { intent_id: id('a') });
        writes++;
        history.cards[0].status = 'stopped'; history.cards[0].can_stop = false;
        res.end(JSON.stringify({ status: 'stopped', intent_id: value.intent_id }));
      });
      return;
    }
    if (u.pathname === base + '/status') return res.end(JSON.stringify({ contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } }));
    if (u.pathname === base + '/identity') return res.end(JSON.stringify({ contract: 'EvaDashboardIdentity/v1', read_only: true,
      identity: { display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1,
        state: 'current', temperament: [], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: [] } } }));
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM,
      headless: true, args: ['--no-sandbox', '--disable-gpu'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    await context.route('**/*', route => {
      if (route.request().url().startsWith(origin + '/')) return route.continue();
      external.push(route.request().url()); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(12000);
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + base + '#/?section=initiative');
    const summary = page.getByRole('region', { name: 'Open eigen projecten', exact: true });
    await summary.getByText('2 van 3 open projecten in dit overzicht.', { exact: true }).waitFor();
    assert.equal(await summary.getByRole('article').count(), 2);
    assert.equal(await summary.getByText('Afgerond project', { exact: true }).count(), 0);
    assert.equal(await summary.getByText('Ouder open project', { exact: true }).count(), 0);
    await summary.getByText('Synthetic stored result', { exact: true }).waitFor();
    if (process.env.EVA_DASHBOARD_SCREENSHOT) await summary.screenshot({ path: process.env.EVA_DASHBOARD_SCREENSHOT });
    assert.equal(writes, 0); assert.equal(artifactReads, 0);
    await summary.getByRole('button', { name: 'Project bekijken', exact: true }).first().click();
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Initiatief: Recent eigen project');
    assert.match(page.url(), /#\/\?section=initiative$/);
    const detail = page.getByRole('article', { name: 'Initiatief: Recent eigen project', exact: true });
    await detail.getByRole('button', { name: 'Stop dit initiatief', exact: true }).click();
    await summary.getByText('2 van 2 open projecten in dit overzicht.', { exact: true }).waitFor();
    assert.equal(writes, 1);
    assert.equal(await summary.getByText('Recent eigen project', { exact: true }).count(), 0);
    await summary.getByText('Ouder open project', { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.equal(await summary.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    if (process.env.EVA_DASHBOARD_MOBILE_SCREENSHOT) await summary.screenshot({ path: process.env.EVA_DASHBOARD_MOBILE_SCREENSHOT });
    history.cards = [];
    await page.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await summary.getByText('In dit opgehaalde overzicht staan geen open eigen projecten.', { exact: true }).waitFor();
    denied = true;
    await page.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await page.getByRole('alert').waitFor();
    assert.equal(await summary.count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ contract: 'EvaCurrentProjectsBrowserProof/v1', status: 'passed', mode: 'isolated',
      maximumCurrentProjects: 2, existingHistoryNavigation: true, existingStopRoute: true,
      automaticArtifactReads: artifactReads, syntheticWrites: writes, noProjectsAndFailureClear: true,
      mobileWidth: 390, javascriptErrors: errors.length, externalRequests: external.length, modelCalls: 0 }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
