// Synthetic mutations only; installed mode never changes household memory.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard';
const sid = '00000000-0000-0000-0000-000000000001';
const next = '00000000-0000-0000-0000-000000000002';
const at = '2026-10-16T00:00:00Z';
const learning = text => ({ kind: 'belief', interpretation: text, outcome: 'open', uncertainty_reason: 'Dit is een voorlopige aanname.' });
const fixtures = {
  status: { contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } },
  identity: { contract: 'EvaDashboardIdentity/v1', read_only: true, identity: { display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1, state: 'current', temperament: ['warm'], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: ['helder'] } } },
  'memory-sources': { contract: 'EvaOwnerMemorySources/v1', read_only: true, meaning: 'Synthetic sources', sources: [{ source_id: sid, occurred_at: at, excerpt: 'Synthetic private source', truncated: false, retention_state: 'retained' }], next_cursor: null },
};
(async () => {
  let origin = process.env.EVA_DASHBOARD_ORIGIN, server, changed = false, denied = false;
  const mutations = [], searches = [], errors = [], external = [];
  if (!origin) {
    server = http.createServer(async (req, res) => {
      const url = new URL(req.url, 'http://127.0.0.1');
      res.setHeader('Cache-Control', 'no-store');
      const resource = url.pathname.slice(base.length + 1);
      if (url.pathname === base || url.pathname === base + '/') {
        res.setHeader('Content-Type', 'text/html'); res.end(fs.readFileSync(path.join(__dirname, 'dist/index.html'))); return;
      }
      if (resource.startsWith('assets/')) {
        const file = path.join(__dirname, 'dist/assets', path.basename(resource));
        res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css'); res.end(fs.readFileSync(file)); return;
      }
      res.setHeader('Content-Type', 'application/json');
      let payload;
      if (req.method === 'POST') {
        let body = ''; for await (const chunk of req) body += chunk;
        payload = JSON.parse(body);
      }
      if (resource === 'memory-view' || resource === 'memory-view/search') {
        if (denied) { res.writeHead(503); res.end('{}'); return; }
        if (payload) searches.push(payload);
        res.end(JSON.stringify({ contract: 'EvaOwnerMemoryView/v1', owner: { display_name: 'Jij', scope: 'own_private_memory' }, mentioned_names: ['Mila'], complete_directory: false,
          memories: [{ id: 'synthetic-fact', text: changed ? 'Mila werkt graag zelfstandig.' : 'Mila vraagt misschien graag hulp.', kind: 'inference', source_id: changed ? next : sid, source_at: at }] })); return;
      }
      if (resource.startsWith('memory-view/sources/')) {
        const id = resource.split('/')[2], operation = resource.split('/')[3];
        if (operation) {
          mutations.push({ id, operation, payload }); changed = operation === 'correct';
          res.end(JSON.stringify({ contract: 'EvaOwnerMemoryChange/v1', source_id: id, state: operation === 'correct' ? 'correction_queued' : 'erased' })); return;
        }
        const text = id === next ? 'Mila werkt graag zelfstandig.' : 'Mila vraagt misschien graag hulp.';
        res.end(JSON.stringify({ contract: 'EvaOwnerMemorySourceView/v1', source_id: id, content: text, occurred_at: at, retention_state: 'retained', origin: 'human-understanding', kind: 'inference', trust: 'model_inference_unconfirmed', learning: learning(text), current_learning: learning(changed ? 'Mila werkt graag zelfstandig.' : text), current_source_id: changed ? next : sid, can_forget: true, erasure_scope: 'memory_document' })); return;
      }
      if (fixtures[resource]) { res.end(JSON.stringify(fixtures[resource])); return; }
      res.writeHead(404); res.end('{}');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + server.address().port;
  }
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => {
      if (route.request().url().startsWith(origin + '/')) return route.continue();
      external.push(new URL(route.request().url()).origin); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(45000); page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + base + '#/?section=memory');
    await page.getByRole('heading', { name: 'Personen en herinneringen', exact: true }).waitFor();
    const panel = page.getByRole('region', { name: 'Personen en herinneringen' });
    await panel.getByText(/jouw privégeheugen/).waitFor();
    const cards = panel.locator('.memory-view-card'); assert.ok(await cards.count() > 0);
    await cards.first().getByRole('button', { name: 'Bron bekijken en aanpassen' }).click();
    const detail = page.getByRole('region', { name: 'Geheugenbron bekijken en aanpassen' });
    await detail.locator('.memory-full-content').waitFor();
    if (server) {
      await detail.getByText('Afleiding van Eva · geen vastgesteld feit').waitFor();
      await detail.getByRole('button', { name: 'Informatie corrigeren' }).click();
      await detail.getByLabel('Wat moet Eva voortaan onthouden?').fill('Mila werkt graag zelfstandig.');
      await detail.getByRole('button', { name: 'Correctie doorgeven' }).click();
      await page.getByText(/Je correctie is ontvangen/).waitFor();
      await cards.getByText('Mila werkt graag zelfstandig.', { exact: true }).waitFor();
      assert.deepEqual(mutations, [{ id: sid, operation: 'correct', payload: { text: 'Mila werkt graag zelfstandig.' } }]);
      await page.getByRole('button', { name: 'Bekijken en aanpassen', exact: true }).click();
      await detail.getByRole('button', { name: 'Open actuele herziening' }).click();
      await detail.getByText('Mila werkt graag zelfstandig.', { exact: true }).first().waitFor();
      await detail.getByRole('button', { name: 'Deze bron vergeten', exact: true }).click();
      await detail.getByText(/Het oorspronkelijke gesprek blijft bewaard/).waitFor();
      await detail.getByRole('button', { name: 'Bevestigen: deze bron vergeten' }).click();
      await page.getByText('Deze geheugenbron of afleiding is vergeten. Het oorspronkelijke gesprek blijft bewaard.', { exact: true }).waitFor();
      assert.equal(mutations.length, 2); assert.deepEqual(mutations[1], { id: next, operation: 'forget', payload: { confirm: true } });
      await panel.getByRole('button', { name: 'Mila', exact: true }).click();
      await cards.first().waitFor(); assert.deepEqual(searches.at(-1), { text: 'Mila' });
    }
    if (process.env.EVA_DASHBOARD_SCREENSHOT) await page.screenshot({ path: process.env.EVA_DASHBOARD_SCREENSHOT });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
    if (server) {
      // A hidden tab clears private text before the next server request.
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
      await page.waitForFunction(() => !document.querySelector('.memory-view-card'));
      denied = true;
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')); });
      await panel.getByRole('alert').waitFor(); assert.equal(await cards.count(), 0);
      await page.getByText('Synthetic private source', { exact: true }).waitFor();
    }
    await page.getByRole('navigation', { name: 'Eva beheer' }).getByRole('link', { name: 'Overzicht', exact: true }).click();
    await page.getByRole('heading', { name: 'Overzicht', exact: true }).waitFor();
    assert.equal(await panel.count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ contract: 'EvaOwnerMemoryBrowserProof/v1', status: 'passed', mode: server ? 'isolated' : 'installed-read-only', exactSourceRead: true, syntheticCorrectionAndErasure: Boolean(server), namedSearch: Boolean(server), mobileWidth: 390, sourceUnmount: true, hiddenAndRevokedViewClears: Boolean(server), javascriptErrors: 0, externalRequests: 0, conversationalModelCalls: 0 }));
  } finally { await browser.close(); if (server) await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
