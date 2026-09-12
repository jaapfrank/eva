const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const playwrightPackage = process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE;
const chromiumBinary = process.env.EVA_DASHBOARD_CHROMIUM;
if (!playwrightPackage || !path.isAbsolute(playwrightPackage)) {
  throw new Error('EVA_DASHBOARD_PLAYWRIGHT_PACKAGE must be an absolute path');
}
if (!chromiumBinary || !path.isAbsolute(chromiumBinary)) {
  throw new Error('EVA_DASHBOARD_CHROMIUM must be an absolute path');
}
const { chromium } = require(playwrightPackage);
const dist = process.env.EVA_DASHBOARD_DIST
  ? path.resolve(process.env.EVA_DASHBOARD_DIST) : path.resolve(__dirname, 'dist');
const cursor = '2026-09-07T12:00:00Z|00000000-0000-0000-0000-000000000002';
const memoryPage = (sources, next_cursor = null) => ({
  contract: 'EvaOwnerMemorySources/v1', read_only: true,
  meaning: 'Eigen gespreksbronnen, geen overzicht van alle afgeleide herinneringen.', sources, next_cursor,
});
const source = (id, excerpt, retention_state, truncated = false) => ({
  source_id: `00000000-0000-0000-0000-${String(id).padStart(12, '0')}`,
  occurred_at: '2026-09-07T12:00:00Z', excerpt, retention_state, truncated,
});
const firstMemoryPage = memoryPage([
  source(1, 'Ik drink graag thee na het eten.', 'retained'),
  source(2, '<img src="https://external.invalid/tracker" onerror="alert(1)">', 'pending', true),
], cursor);

const fixtures = {
  '/internal/v1/dashboard/initiatives': {
    contract: 'EvaOwnerInitiatives/v1', cards: [], checked_at: '2026-09-12T10:01:00Z',
    latest_review: { updated_at: '2026-09-12T10:00:00Z', phase: 'admission', state: 'not_admitted',
      reason: 'De huidige beoordeling wacht.', blocker: 'background_budget', intent_id: null },
  },
  '/internal/v1/dashboard/browser/status': { contract: 'EvaBrowserSession/v1', state: 'stopped', revision: 0,
    owner_control: false, eva_control: false, viewer_path: null, browser_ready: false },
  '/internal/v1/dashboard/memory-sources': firstMemoryPage,
  '/internal/v1/dashboard/status': {
    contract: 'EvaDashboardStatus/v1', read_only: true,
    runtime: { ready: true, state: 'ready' },
  },
  '/internal/v1/dashboard/identity': {
    contract: 'EvaDashboardIdentity/v1', read_only: true,
    identity: {
      display_name: 'Eva', identity_id: 'test', language: 'nl',
      pack_sha256: '0'.repeat(64), pack_version: 1, state: 'current',
      temperament: ['warm'],
      voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: ['helder'] },
    },
  },
  '/internal/v1/dashboard/workboard': {
    contract: 'EvaWorkBoard/v1', schema_version: 1, read_only: true,
    status: 'ready', truncated: false, message: 'Actueel',
    cards: [{
      contract: 'EvaWorkBoardCard/v1', schema_version: 1,
      column: 'doing', title: 'Echte taak',
      summary: 'Veilige kandidaat vergelijken',
      worker_name: 'onderzoeker', worker_role: 'dashboard vergelijken',
      worker_health: 'active', result_status: 'not_received',
      attempt: 1, maximum_attempts: 2,
      deadline: '2026-08-31T12:00:00Z', duration_seconds: 12, overdue: false,
      usage: {
        contract: 'EvaWorkBoardUsage/v1', schema_version: 1,
        measurement: 'measured', input_tokens: 100, output_tokens: 20,
        total_tokens: 120, cost_usd_micros: 15,
      },
      reason: 'wordt uitgevoerd', changed: 'just_now',
    }],
  },
};

function serverFor(authorized, seen, memory = { mode: 'pages' }) {
  return http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const pathname = url.pathname;
    seen.push({ pathname, query: url.search, authorization: request.headers.authorization || null });
    if (pathname.startsWith('/internal/v1/dashboard/assets/')) {
      const name = pathname.slice('/internal/v1/dashboard/'.length);
      const file = path.resolve(dist, name);
      assert.ok(file.startsWith(`${path.resolve(dist, 'assets')}${path.sep}`));
      if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
        response.statusCode = 404;
        response.end();
        return;
      }
      response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css');
      response.end(fs.readFileSync(file));
      return;
    }
    if (pathname.startsWith('/internal/v1/dashboard/')) {
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('Content-Type', 'application/json');
      if (!authorized) {
        response.statusCode = 401;
        response.end(JSON.stringify({ detail: 'owner access required' }));
        return;
      }
      if (pathname === '/internal/v1/dashboard/initiatives') {
        assert.equal(request.method, 'GET');
        const base = fixtures[pathname];
        const mode = memory.initiativeMode;
        if (mode === 'denied') { response.statusCode = 403; response.end('{}'); return; }
        if (mode === 'older') { response.end(JSON.stringify({ contract: base.contract, cards: [] })); return; }
        if (mode === 'empty') { response.end(JSON.stringify({ ...base, latest_review: null })); return; }
        if (mode === 'malformed') {
          response.end(JSON.stringify({ ...base, latest_review: { ...base.latest_review, reason: { private: 'must not render' } } })); return;
        }
        if (mode === 'silence') {
          response.end(JSON.stringify({ ...base, latest_review: { ...base.latest_review, state: 'silence',
            reason: '<img src="https://external.invalid/initiative" onerror="alert(1)">', blocker: null } })); return;
        }
        if (mode === 'card') {
          response.end(JSON.stringify({ ...base, cards: [{ intent_id: 'private-initiative', title: 'Eigen bijdrage',
            status: 'done', motivation: 'Private motivatie', question: 'Private inhoud', has_artifact: true, can_stop: false }] })); return;
        }
      }
      if (pathname.startsWith('/internal/v1/dashboard/memory-sources/')) {
        if (memory.mode === 'sourceRevoked') { response.statusCode = 404; response.end('{}'); return; }
        const parts = pathname.split('/'); const id = parts[5]; const action = parts[6];
        assert.equal(id, source(1, '', 'retained').source_id);
        if (request.method === 'POST') {
          let body = ''; request.on('data', chunk => { body += chunk; });
          request.on('end', () => {
            const payload = JSON.parse(body); memory.changes = (memory.changes || 0) + 1;
            if (action === 'forget') assert.deepEqual(payload, { confirm: true });
            else { assert.equal(action, 'correct'); assert.deepEqual(payload, { text: 'Ik drink nu liever koffie.' }); }
            response.end(JSON.stringify({ contract: 'EvaOwnerMemoryChange/v1', source_id: id,
              state: action === 'forget' ? 'erased' : 'correction_queued' }));
          }); return;
        }
        response.end(JSON.stringify({ contract: 'EvaOwnerMemorySource/v1', source_id: id,
          content: 'Volledige eigen gespreksbron: ik drink graag thee.', occurred_at: '2026-09-07T12:00:00Z',
          retention_state: 'retained', origin: 'Telegram' })); return;
      }
      if (pathname === '/internal/v1/dashboard/memory-sources') {
        assert.equal(request.method, 'GET');
        assert.equal(url.searchParams.get('limit'), '20');
        assert.ok([...url.searchParams.keys()].every(key => ['limit', 'before'].includes(key)));
        if (memory.mode === 'denied' || memory.mode === 'error') {
          response.statusCode = memory.mode === 'denied' ? 403 : 503;
          response.end(JSON.stringify({ detail: 'PRIVATE FAILURE MUST NOT APPEAR' })); return;
        }
        if (memory.mode === 'empty') { response.end(JSON.stringify(memoryPage([]))); return; }
        if (memory.mode === 'malformed') {
          response.end(JSON.stringify({ ...firstMemoryPage, private_scope: 'PRIVATE FAILURE MUST NOT APPEAR' })); return;
        }
        const invalid = {
          invalidDate: memoryPage([{ ...source(1, 'Niet tonen', 'retained'), occurred_at: '2026-02-30T12:00:00Z' }]),
          invalidCursor: { ...firstMemoryPage, next_cursor: 'principal:other' },
          duplicateId: memoryPage([source(1, 'Niet tonen', 'retained'), source(1, 'Dubbel', 'pending')]),
          overlong: memoryPage([source(1, 'x'.repeat(2049), 'retained')]),
          unknownState: memoryPage([source(1, 'Niet tonen', 'unknown')]),
        };
        if (invalid[memory.mode]) { response.end(JSON.stringify(invalid[memory.mode])); return; }
        if (memory.mode === 'delayed') {
          setTimeout(() => response.end(JSON.stringify(firstMemoryPage)), 800); return;
        }
        if (url.searchParams.has('before')) {
          assert.equal(url.searchParams.get('before'), cursor);
          response.end(JSON.stringify(memoryPage([source(3, 'Tweede pagina: een andere bron.', 'needs_help')]))); return;
        }
      }
      const payload = fixtures[pathname];
      if (!payload) { response.statusCode = 404; response.end('{}'); return; }
      response.end(JSON.stringify(payload));
      return;
    }
    if (pathname === '/favicon.ico') {
      response.statusCode = 204;
      response.end();
      return;
    }
    const requested = pathname === '/' ? 'index.html' : pathname.slice(1);
    const file = path.resolve(dist, requested);
    assert.ok(
      file === path.resolve(dist, 'index.html')
        || file.startsWith(`${dist}${path.sep}`),
    );
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
      response.statusCode = 404;
      response.end();
      return;
    }
    response.setHeader(
      'Content-Type',
      file.endsWith('.js') ? 'text/javascript'
        : file.endsWith('.css') ? 'text/css' : 'text/html',
    );
    response.end(fs.readFileSync(file));
  });
}

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

async function main() {
  const browser = await chromium.launch({
    executablePath: chromiumBinary,
    headless: true,
    timeout: 15_000,
  });
  let server;
  let deniedServer;
  try {
    const seen = [];
    const memory = { mode: 'pages' };
    server = serverFor(true, seen, memory);
    await listen(server);
    const origin = `http://127.0.0.1:${server.address().port}`;
    const page = await browser.newPage();
    const external = [];
    const errors = [];
    const consoleErrors = [];
    page.on('request', (request) => {
      if (!request.url().startsWith(origin)) external.push(request.url());
    });
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.setDefaultTimeout(10_000);
    await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 10_000 });
    await page.getByText('Echte taak').waitFor({ timeout: 10_000 });
    assert.match(
      await page.locator('body').innerText(),
      /Eva[\s\S]*Werkbord[\s\S]*Echte taak/u,
    );
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, []);
    assert.equal(await page.evaluate(() => localStorage.length), 0);
    assert.equal(await page.evaluate(() => sessionStorage.length), 0);
    assert.equal((await page.context().cookies()).length, 0);
    for (const endpoint of Object.keys(fixtures)) {
      assert.ok(seen.some((row) => row.pathname === endpoint));
    }
    assert.ok(seen.every((row) => row.authorization === null));
    const initiatives = page.getByRole('region', { name: 'Eigen initiatieven', exact: true });
    await initiatives.getByText('Wacht op achtergrondbudget', { exact: true }).waitFor();
    assert.equal(await initiatives.locator('article').count(), 0);
    assert.equal(await initiatives.locator('time').first().getAttribute('datetime'), '2026-09-12T10:00:00Z');
    await initiatives.getByText('De huidige beoordeling wacht.', { exact: true }).waitFor();
    assert.match(await initiatives.innerText(), /Een beoordeling is nog geen bijdrage/u);
    memory.initiativeMode = 'silence';
    await initiatives.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await initiatives.getByText('Bewust geen actie', { exact: true }).waitFor();
    await initiatives.getByText('<img src="https://external.invalid/initiative" onerror="alert(1)">', { exact: true }).waitFor();
    assert.equal(await initiatives.locator('img').count(), 0);
    memory.initiativeMode = 'older';
    await initiatives.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await initiatives.getByText('De laatste beoordeling is via deze versie nog niet beschikbaar.', { exact: true }).waitFor();
    memory.initiativeMode = 'empty';
    await initiatives.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await initiatives.getByText('Er is nog geen opgeslagen beoordeling beschikbaar.', { exact: true }).waitFor();
    for (const mode of ['denied', 'malformed']) {
      memory.initiativeMode = 'card';
      await initiatives.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
      await initiatives.getByText('Eigen bijdrage', { exact: true }).waitFor();
      memory.initiativeMode = mode;
      await initiatives.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
      await initiatives.getByRole('alert').waitFor();
      assert.equal(await initiatives.locator('article').count(), 0);
      assert.equal(await initiatives.locator('time').count(), 0);
      assert.doesNotMatch(await initiatives.innerText(), /Private motivatie|Private inhoud|De huidige beoordeling wacht/u);
    }
    memory.initiativeMode = undefined;
    await initiatives.getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await initiatives.getByText('Wacht op achtergrondbudget', { exact: true }).waitFor();
    await page.getByRole('heading', { name: 'Jouw geheugenbronnen', exact: true }).waitFor();
    await page.getByText('Ik drink graag thee na het eten.', { exact: true }).waitFor();
    assert.equal(await page.locator('.memory-list img').count(), 0);
    assert.equal(await page.getByText('<img src="https://external.invalid/tracker" onerror="alert(1)">', { exact: true }).count(), 1);
    await page.getByText('Fragment — deze bron is langer dan hier getoond.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Bekijken en aanpassen', exact: true }).first().click();
    await page.getByText('Volledige eigen gespreksbron: ik drink graag thee.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Deze bron vergeten', exact: true }).click();
    assert.equal(memory.changes || 0, 0);
    await page.getByRole('button', { name: 'Annuleren', exact: true }).click();
    assert.equal(memory.changes || 0, 0);
    await page.getByRole('button', { name: 'Informatie corrigeren', exact: true }).click();
    await page.getByLabel('Wat moet Eva voortaan onthouden?').fill('Ik drink nu liever koffie.');
    await page.getByRole('button', { name: 'Correctie doorgeven', exact: true }).click();
    await page.getByText(/Je correctie is ontvangen. De nieuwe bron/).waitFor();
    assert.equal(memory.changes, 1);
    await page.getByRole('button', { name: 'Bekijken en aanpassen', exact: true }).first().click();
    await page.getByRole('button', { name: 'Deze bron vergeten', exact: true }).click();
    await page.getByRole('button', { name: 'Bevestigen: deze bron vergeten', exact: true }).click();
    await page.getByText('Deze bron en het daarvan afgeleide geheugen zijn gewist.', { exact: true }).waitFor();
    assert.equal(memory.changes, 2);
    assert.equal(await page.getByRole('button', { name: 'Vorige', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: 'Volgende', exact: true }).click();
    await page.getByText('Tweede pagina: een andere bron.', { exact: true }).waitFor();
    assert.equal(await page.getByText('Ik drink graag thee na het eten.', { exact: true }).count(), 0);
    await page.getByText('Hulp nodig', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Volgende', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: 'Vorige', exact: true }).click();
    await page.getByText('Ik drink graag thee na het eten.', { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.keyboard.press('Escape');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
      JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('body *')]
        .filter(element => element.getBoundingClientRect().right > innerWidth + 1)
        .slice(0, 12).map(element => ({ tag: element.tagName, class: element.className, width: element.getBoundingClientRect().width })))));
    if (process.env.EVA_DASHBOARD_SCREENSHOT) await page.screenshot({ path: process.env.EVA_DASHBOARD_SCREENSHOT, fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.getByRole('button', { name: 'Bekijken en aanpassen', exact: true }).first().click();
    await page.getByText('Volledige eigen gespreksbron: ik drink graag thee.', { exact: true }).waitFor();
    memory.mode = 'sourceRevoked';
    await page.locator('.memory-sources').getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await page.getByText('Deze bron is niet beschikbaar. Sluit dit venster en vernieuw de lijst.', { exact: true }).waitFor();
    assert.equal(await page.getByText('Volledige eigen gespreksbron: ik drink graag thee.', { exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Sluiten', exact: true }).click();
    memory.mode = 'pages';
    const cases = [];
    for (const [mode, message] of [
      ['denied', 'Je eigenaarstoegang is niet meer geldig. De bronnen zijn verborgen.'],
      ['error', 'Geheugenbronnen zijn nu niet beschikbaar. Probeer opnieuw; eerdere inhoud is verborgen.'],
      ['malformed', 'Geheugenbronnen zijn nu niet beschikbaar. Probeer opnieuw; eerdere inhoud is verborgen.'],
      ...['invalidDate', 'invalidCursor', 'duplicateId', 'overlong', 'unknownState'].map(mode =>
        [mode, 'Geheugenbronnen zijn nu niet beschikbaar. Probeer opnieuw; eerdere inhoud is verborgen.']),
      ['empty', 'Nog geen zichtbare geheugenbronnen.'],
    ]) {
      memory.mode = mode;
      await page.locator('.memory-sources').getByRole('button', { name: 'Vernieuwen', exact: true }).click();
      await page.getByText(message, { exact: true }).waitFor();
      assert.equal(await page.locator('.memory-list li').count(), 0);
      assert.equal((await page.locator('body').innerText()).includes('PRIVATE FAILURE MUST NOT APPEAR'), false);
      cases.push(mode);
      memory.mode = 'pages';
      await page.locator('.memory-sources').getByRole('button', { name: 'Vernieuwen', exact: true }).click();
      await page.getByText('Ik drink graag thee na het eten.', { exact: true }).waitFor();
    }
    memory.mode = 'delayed';
    const delayed = page.waitForRequest(request => request.url().includes('/memory-sources?'));
    await page.locator('.memory-sources').getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await delayed;
    assert.equal(await page.locator('.memory-list li').count(), 0);
    memory.mode = 'denied';
    await page.locator('.memory-sources').getByRole('button', { name: 'Vernieuwen', exact: true }).click();
    await page.getByText('Je eigenaarstoegang is niet meer geldig. De bronnen zijn verborgen.', { exact: true }).waitFor();
    await page.waitForTimeout(1000);
    assert.equal(await page.locator('.memory-list li').count(), 0);
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.equal((await page.context().cookies()).length, 0);
    await page.close();
    await close(server);
    server = undefined;

    const denied = [];
    deniedServer = serverFor(false, denied);
    await listen(deniedServer);
    const deniedPage = await browser.newPage();
    deniedPage.setDefaultTimeout(10_000);
    await deniedPage.goto(
      `http://127.0.0.1:${deniedServer.address().port}`,
      { waitUntil: 'domcontentloaded', timeout: 10_000 },
    );
    await deniedPage.waitForTimeout(1_000);
    assert.ok(denied.some((row) => row.pathname === '/internal/v1/dashboard/status'));
    assert.equal(denied.some((row) => row.pathname === '/internal/v1/dashboard/identity'), false);
    assert.equal(denied.some((row) => row.pathname === '/internal/v1/dashboard/workboard'), false);
    assert.equal(denied.some((row) => row.pathname === '/internal/v1/dashboard/memory-sources'), false);
    assert.equal(await deniedPage.evaluate(() => localStorage.length), 0);
    assert.equal(await deniedPage.evaluate(() => sessionStorage.length), 0);
    assert.equal((await deniedPage.context().cookies()).length, 0);
    await deniedPage.close();
    await close(deniedServer);
    deniedServer = undefined;
    console.log(JSON.stringify({
      contract: 'EvaDashboardCandidateBrowserProof/v1',
      authenticated: {
        browserStorageEntries: 0,
        externalRequests: 0,
        pageErrors: 0,
        protectedEndpointsRead: Object.keys(fixtures),
      },
      anonymous: {
        identityRead: false,
        statusRequestObserved: true,
        workboardRead: false,
      },
      memorySources: { fullSource: true, staleDetailRevokedWhileListAvailable: true, explicitCorrection: true, eraseConfirmationAndCancellation: true, pagination: true, literalText: true, revokedAccessClearsContent: true,
        negativeCases: cases, lateReplyCannotRestoreRevokedContent: true, mobileWidth: 390, serverScopeOnly: true },
      status: 'passed',
    }));
  } finally {
    if (server) await close(server);
    if (deniedServer) await close(deniedServer);
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
