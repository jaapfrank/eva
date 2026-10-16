const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard', routeBase = base + '/model-providers';
const dist = process.env.EVA_DASHBOARD_DIST || path.join(__dirname, 'dist');
const at = '2026-10-16T16:00:00Z', secret = 'fixture-key-only-not-a-real-key';
const status = { contract: 'EvaModelProviders/v1', scope: 'private_text_conversations', selected_id: null,
  default: { label: 'Bestaande Eva-modelroute', ready: true, state: 'ready' }, connections: [],
  adapters: [{ kind: 'anthropic', label: 'Claude / Anthropic', installed: true },
    { kind: 'openai_compatible', label: 'OpenAI-compatible API', installed: true }],
  meaning: 'Alleen jouw gekozen privétekstmodel; bewaren is geen geslaagde verbindingstest.' };
const mutationLog = [];
let denied = false, corrupt = false, genericFails = true, reads = 0;
(async () => {
  let server, origin = process.env.EVA_DASHBOARD_ORIGIN;
  const errors = [], external = [], forbiddenLiveRequests = [];
  if (!origin) {
    server = http.createServer(async (req, res) => {
      const url = new URL(req.url, 'http://127.0.0.1'); res.setHeader('cache-control', 'no-store');
      if (url.pathname === base || url.pathname === base + '/') {
        res.setHeader('content-type', 'text/html'); res.end(fs.readFileSync(path.join(dist, 'index.html'))); return;
      }
      if (url.pathname.startsWith(base + '/assets/')) {
        const file = path.join(dist, 'assets', path.basename(url.pathname));
        res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : 'text/css'); res.end(fs.readFileSync(file)); return;
      }
      res.setHeader('content-type', 'application/json');
      if (url.pathname.startsWith(routeBase)) {
        if (denied) { res.writeHead(503); res.end('{}'); return; }
        if (req.method === 'POST') {
          let body = ''; for await (const chunk of req) body += chunk; const payload = JSON.parse(body);
          const operation = url.pathname.slice(routeBase.length + 1);
          if (operation === 'configure') {
            assert.deepEqual(Object.keys(payload).sort(), ['api_key', 'endpoint', 'kind', 'label', 'model', 'workspace_id']);
            assert.equal(payload.api_key, secret);
            assert.equal(payload.kind === 'anthropic' ? payload.endpoint : payload.endpoint.endsWith('/v1'),
              payload.kind === 'anthropic' ? 'https://api.anthropic.com' : true);
            status.connections.push({ id: 'synthetic:' + status.connections.length, label: payload.label,
              model: payload.model, kind: payload.kind, endpoint: payload.endpoint, enabled: true,
              checked_at: null, check_state: 'unchecked', selected: false });
            mutationLog.push({ operation, kind: payload.kind });
          } else {
            assert.deepEqual(Object.keys(payload).sort(), operation === 'check' ? ['allow_provider_request', 'id'] : ['id']);
            const connection = status.connections.find(item => item.id === payload.id);
            if (operation === 'check') {
              assert.equal(payload.allow_provider_request, true); assert(connection?.enabled);
              connection.checked_at = at;
              connection.check_state = connection.kind === 'openai_compatible' && genericFails ? 'failed' : 'ready';
            } else if (operation === 'select') {
              if (payload.id !== null) assert(connection?.enabled && connection.check_state === 'ready');
              status.selected_id = payload.id;
            } else if (operation === 'disable') {
              assert(connection); connection.enabled = false;
              if (status.selected_id === connection.id) status.selected_id = null;
            } else { assert.fail('Unexpected provider operation'); }
            status.connections.forEach(item => { item.selected = item.id === status.selected_id; });
            mutationLog.push({ operation, id: payload.id });
          }
        } else { assert.equal(req.method, 'GET'); reads++; }
        res.end(JSON.stringify(corrupt ? { ...status, api_key: 'MUST_NEVER_RENDER' } : status)); return;
      }
      if (url.pathname === base + '/status') { res.end(JSON.stringify({ contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } })); return; }
      if (url.pathname === base + '/identity') { res.end(JSON.stringify({ contract: 'EvaDashboardIdentity/v1', read_only: true,
        identity: { display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1,
          state: 'current', temperament: [], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: [] } } })); return; }
      if (url.pathname === base + '/components') { res.end(JSON.stringify({ contract: 'EvaComponentObservations/v1', checked_at: at, components: [] })); return; }
      if (url.pathname === base + '/browser') { res.end(JSON.stringify({ state: 'stopped' })); return; }
      res.writeHead(404); res.end('{}');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + server.address().port;
  }
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM,
    headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => {
      const request = route.request();
      if (!request.url().startsWith(origin + '/')) { external.push(new URL(request.url()).origin); return route.abort(); }
      if (!server && !['GET', 'HEAD'].includes(request.method())) {
        forbiddenLiveRequests.push(request.method() + ' ' + new URL(request.url()).pathname); return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(12000);
    await page.goto(origin + base + '#/?section=connections');
    const panel = page.getByRole('region', { name: 'Optionele modelverbindingen', exact: true });
    await panel.getByRole('heading', { name: 'Optionele modelverbindingen', exact: true }).waitFor();
    await panel.getByLabel('Gekozen tekstmodel', { exact: true }).waitFor();
    if (server) {
      assert.equal(mutationLog.length, 0);
      const form = panel.getByRole('form', { name: 'Modelverbinding toevoegen' });
      await form.getByLabel('Naam voor deze verbinding', { exact: true }).fill('Synthetic Claude');
      await form.getByLabel('Model-ID', { exact: true }).fill('synthetic-claude');
      await form.getByLabel('Workspace-ID, indien vereist', { exact: true }).fill('wrkspc_synthetic');
      await form.getByLabel('API-sleutel', { exact: true }).fill(secret);
      await form.getByRole('button', { name: 'Verbinding privé bewaren', exact: true }).click();
      const claude = panel.getByRole('article', { name: 'Modelverbinding: Synthetic Claude', exact: true });
      await claude.waitFor(); assert.equal(await form.getByLabel('API-sleutel', { exact: true }).inputValue(), '');
      assert.equal(await claude.getByRole('button', { name: 'Kies voor privétekstgesprekken', exact: true }).isDisabled(), true);
      assert.deepEqual(mutationLog, [{ operation: 'configure', kind: 'anthropic' }]);
      await claude.getByRole('button', { name: 'Controleer met één modelantwoord', exact: true }).click();
      await claude.getByText(/Modelcontrole geslaagd/).waitFor();
      await claude.getByRole('button', { name: 'Kies voor privétekstgesprekken', exact: true }).click();
      await panel.getByLabel('Gekozen tekstmodel', { exact: true }).getByText('Synthetic Claude', { exact: true }).waitFor();
      await panel.getByRole('button', { name: 'Gebruik bestaande modelroute', exact: true }).click();
      await panel.getByLabel('Gekozen tekstmodel', { exact: true }).getByText('Bestaande Eva-modelroute', { exact: true }).waitFor();
      await form.getByLabel('Provider', { exact: true }).selectOption('openai_compatible');
      await form.getByLabel('Naam voor deze verbinding', { exact: true }).fill('Synthetic compatible');
      await form.getByLabel('Model-ID', { exact: true }).fill('synthetic-model');
      await form.getByLabel('API-adres', { exact: true }).fill('https://api.example.invalid/v1');
      await form.getByLabel('API-sleutel', { exact: true }).fill(secret);
      await form.getByRole('button', { name: 'Verbinding privé bewaren', exact: true }).click();
      const generic = panel.getByRole('article', { name: 'Modelverbinding: Synthetic compatible', exact: true });
      await generic.waitFor();
      await generic.getByRole('button', { name: 'Controleer met één modelantwoord', exact: true }).click();
      await generic.getByText(/Modelcontrole niet geslaagd/).waitFor();
      assert.equal(await generic.getByRole('button', { name: 'Kies voor privétekstgesprekken', exact: true }).isDisabled(), true);
      genericFails = false;
      await generic.getByRole('button', { name: 'Controleer met één modelantwoord', exact: true }).click();
      await generic.getByText(/Modelcontrole geslaagd/).waitFor();
      await generic.getByRole('button', { name: 'Kies voor privétekstgesprekken', exact: true }).click();
      await generic.getByText(/^Gekozen/).waitFor();
      await generic.getByRole('button', { name: 'Verbinding uitschakelen', exact: true }).click();
      await generic.getByText(/^Uitgeschakeld/).waitFor();
      assert.equal(await generic.getByRole('button', { name: 'Controleer met één modelantwoord', exact: true }).isDisabled(), true);
      assert.deepEqual(mutationLog, [{ operation: 'configure', kind: 'anthropic' },
        { operation: 'check', id: 'synthetic:0' }, { operation: 'select', id: 'synthetic:0' }, { operation: 'select', id: null },
        { operation: 'configure', kind: 'openai_compatible' }, { operation: 'check', id: 'synthetic:1' },
        { operation: 'check', id: 'synthetic:1' }, { operation: 'select', id: 'synthetic:1' }, { operation: 'disable', id: 'synthetic:1' }]);
      assert.deepEqual(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) })), { local: [], session: [] });
      await form.getByLabel('API-sleutel', { exact: true }).fill(secret);
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
      assert.equal(await panel.getByRole('article').count(), 0);
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')); });
      await claude.waitFor(); assert.equal(await form.getByLabel('API-sleutel', { exact: true }).inputValue(), '');
      await form.getByLabel('API-sleutel', { exact: true }).fill(secret);
      await page.getByRole('navigation', { name: 'Eva beheer' }).getByRole('link', { name: 'Overzicht', exact: true }).click();
      assert.equal(await page.getByRole('region', { name: 'Optionele modelverbindingen', exact: true }).count(), 0);
      await page.getByRole('navigation', { name: 'Eva beheer' }).getByRole('link', { name: 'Verbindingen', exact: true }).click();
      await claude.waitFor(); assert.equal(await form.getByLabel('API-sleutel', { exact: true }).inputValue(), '');
      corrupt = true;
      await panel.getByRole('button', { name: 'Modelstatus vernieuwen', exact: true }).click();
      await panel.getByRole('alert').waitFor(); assert.equal(await panel.getByRole('article').count(), 0);
      assert.equal(await page.getByText('MUST_NEVER_RENDER', { exact: true }).count(), 0);
      corrupt = false;
      await panel.getByRole('button', { name: 'Modelstatus vernieuwen', exact: true }).click(); await claude.waitFor();
      denied = true;
      await panel.getByRole('button', { name: 'Modelstatus vernieuwen', exact: true }).click();
      await panel.getByRole('alert').waitFor(); assert.equal(await panel.getByRole('article').count(), 0);
      assert.equal(mutationLog.length, 9);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.EVA_DASHBOARD_SCREENSHOT) await page.screenshot({ path: process.env.EVA_DASHBOARD_SCREENSHOT, fullPage: true });
    assert.deepEqual(errors, []); assert.deepEqual(external, []); assert.deepEqual(forbiddenLiveRequests, []);
    console.log(JSON.stringify({ contract: 'EvaOwnerModelProvidersBrowserProof/v1', status: 'passed', mode: server ? 'isolated' : 'installed-read-only',
      fullLifecycle: Boolean(server), explicitProviderChecks: server ? 3 : 0, modelCalls: 0, liveWrites: 0,
      keyNotEchoedOrStored: Boolean(server), hiddenUnmountAndFailureClear: Boolean(server), mobileWidth: 390,
      javascriptErrors: 0, externalRequests: 0, metadataReads: server ? reads : undefined }));
  } finally { await browser.close(); if (server) await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
