// Isolated browser check: synthetic learning/source responses, no Eva/model call.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard';
const dist = process.env.EVA_DASHBOARD_DIST;
const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
const untrusted = '<img src="https://external.invalid/tracker" onerror="alert(1)">';
(async () => {
  let revision = 0, erased = false, denied = false;
  const mutations = [], errors = [], external = [];
  const item = () => ({ source_id: ids[revision], kind: 'person_hypothesis', text: revision ? 'Corrected fixture interpretation' : untrusted,
    uncertainty_reason: 'One explicit fixture response', outcome: 'open', recorded_at: '2026-10-24T10:00:00Z',
    review_at: null, revised: revision > 0, retention_state: 'retained', is_execution_permission: false });
  const history = () => ({ contract: 'EvaOwnerInitiativeHistory/v1', cards: [], latest_review: null, truncated: false,
    learning: { current_styles: [], reflections: [], components: [] },
    housemate: { contract: 'EvaOwnerHousemateState/v1', checked_at: '2026-10-24T10:00:00Z', current_learning: erased ? [] : [item()],
      learning_limit: 8, learning_coverage: 'recent_current_authorized_heads', personality_deltas: [], personality_deltas_state: 'no_separate_typed_delta_route',
      shared_trace: null, shared_trace_state: 'no_separate_registered_trace_route', curiosity_source: 'current_open_project_questions' } });
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://localhost'); res.setHeader('Cache-Control', 'no-store');
    if (u.pathname === base || u.pathname === base + '/') { res.setHeader('Content-Type', 'text/html'); return res.end(fs.readFileSync(path.join(dist, 'index.html'))); }
    if (u.pathname.startsWith(base + '/assets/')) {
      const file = path.join(dist, 'assets', path.basename(u.pathname)); res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css');
      return res.end(fs.readFileSync(file));
    }
    res.setHeader('Content-Type', 'application/json');
    if (u.pathname === base + '/status') return res.end(JSON.stringify({ contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } }));
    if (u.pathname === base + '/identity') return res.end(JSON.stringify({ contract: 'EvaDashboardIdentity/v1', read_only: true,
      identity: { display_name: 'Eva', identity_id: 'fixture', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1,
        state: 'current', temperament: [], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: [] } } }));
    if (u.pathname === base + '/initiatives/history' || u.pathname === base + '/initiatives/state') {
      assert.equal(req.method, 'GET'); assert.equal(u.search, '');
      if (denied) { res.writeHead(503); return res.end('{}'); }
      const data = history();
      if (u.pathname.endsWith('/state')) return res.end(JSON.stringify(data.housemate));
      delete data.housemate; return res.end(JSON.stringify(data));
    }
    if (u.pathname === base + '/household-provisions') { res.writeHead(503); return res.end('{}'); }
    if (u.pathname === base + '/memory-view') return res.end(JSON.stringify({ contract: 'EvaOwnerMemoryView/v1', complete_directory: false,
      owner: { display_name: 'Fixture owner' }, mentioned_names: [], memories: [], search: null }));
    if (u.pathname === base + '/memory-sources') return res.end(JSON.stringify({ contract: 'EvaOwnerMemorySources/v1', read_only: true,
      meaning: 'Synthetic source index', sources: [], next_cursor: null }));
    const match = u.pathname.match(/\/memory-view\/sources\/([0-9a-f-]+)(?:\/(correct|forget))?$/);
    if (match) {
      assert.equal(match[1], ids[revision]);
      if (req.method === 'GET') return res.end(JSON.stringify({ contract: 'EvaOwnerMemorySourceView/v1', source_id: ids[revision],
        content: item().text, occurred_at: item().recorded_at, retention_state: 'retained', origin: 'Synthetic private source', kind: 'inference',
        trust: 'model_inference_unconfirmed', learning: { kind: 'person_hypothesis', outcome: 'open', uncertainty_reason: 'Fixture uncertainty' },
        current_learning: { interpretation: item().text }, current_source_id: ids[revision], can_forget: true, erasure_scope: 'memory_document', raw_state: 'available' }));
      let body = ''; req.on('data', chunk => body += chunk); req.on('end', () => {
        const payload = JSON.parse(body); const old = ids[revision]; mutations.push({ source: old, action: match[2], payload });
        if (match[2] === 'correct') { assert.deepEqual(payload, { text: 'Corrected fixture interpretation' }); revision = 1; res.writeHead(202); }
        else { assert.deepEqual(payload, { confirm: true }); erased = true; }
        res.end(JSON.stringify({ contract: 'EvaOwnerMemoryChange/v1', source_id: old, state: match[2] === 'correct' ? 'correction_queued' : 'erased' }));
      }); return;
    }
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    await context.route('**/*', route => { if (route.request().url().startsWith(origin + '/')) return route.continue(); external.push(route.request().url()); return route.abort(); });
    const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + base + '#/?section=initiative');
    const panel = page.getByRole('region', { name: 'Actuele inzichten en eigen ontwikkeling' });
    await panel.getByText(untrusted, { exact: true }).waitFor();
    assert.equal(await panel.locator('img').count(), 0); assert.equal(mutations.length, 0);
    if (process.env.EVA_DASHBOARD_SCREENSHOT) await panel.screenshot({ path: process.env.EVA_DASHBOARD_SCREENSHOT });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.EVA_DASHBOARD_MOBILE_SCREENSHOT) await panel.screenshot({ path: process.env.EVA_DASHBOARD_MOBILE_SCREENSHOT });
    await panel.getByRole('button', { name: 'Bron bekijken, corrigeren of vergeten' }).click();
    const source = page.getByRole('region', { name: 'Geheugenbron bekijken en aanpassen' });
    await source.getByRole('button', { name: 'Informatie corrigeren', exact: true }).click();
    assert.match(page.url(), /section=memory/);
    await source.getByLabel('Wat moet Eva voortaan onthouden?').fill('Corrected fixture interpretation');
    await source.getByRole('button', { name: 'Correctie doorgeven', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.memory-source-control'));
    await page.evaluate(() => { location.hash = '#/?section=initiative'; });
    await panel.getByText('Corrected fixture interpretation', { exact: true }).waitFor();
    assert.equal(await panel.getByText(untrusted, { exact: true }).count(), 0);
    await panel.getByRole('button', { name: 'Bron bekijken, corrigeren of vergeten' }).click();
    await source.getByRole('button', { name: 'Deze bron vergeten', exact: true }).click();
    await source.getByRole('button', { name: 'Bevestigen: deze bron vergeten', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.memory-source-control'));
    await page.evaluate(() => { location.hash = '#/?section=initiative'; });
    await panel.getByText(/In de recente selectie zijn geen actuele leerbronnen beschikbaar/).waitFor();
    assert.equal(await panel.getByText('Corrected fixture interpretation', { exact: true }).count(), 0);
    assert.equal(mutations.length, 2); assert.deepEqual(mutations.map(m => m.source), ids);
    denied = true; await page.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Eigen initiatieven zijn tijdelijk niet beschikbaar' }).waitFor();
    assert.equal(await panel.count(), 0); assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ status: 'passed', correctedCurrentSourceNavigation: true, erasureVisibleAfterReload: true,
      unavailableOptionalRoutesHonest: true, failureClearsPrivatePanel: true, untrustedTextEscaped: true,
      syntheticSourceMutations: mutations.length, productionPosts: 0, modelCalls: 0, javascriptErrors: errors.length, externalRequests: external.length, mobileWidth: 390 }));
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
