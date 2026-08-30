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
const dist = path.resolve(__dirname, 'dist');

const fixtures = {
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
      column: 'doing', title: 'Echte taak',
      summary: 'Veilige kandidaat vergelijken',
      worker_health: 'active', result_status: 'not_received',
    }],
  },
};

function serverFor(authorized, seen) {
  return http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    seen.push({ pathname, authorization: request.headers.authorization || null });
    if (pathname.startsWith('/internal/v1/dashboard/')) {
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('Content-Type', 'application/json');
      if (!authorized) {
        response.statusCode = 401;
        response.end(JSON.stringify({ detail: 'owner access required' }));
        return;
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
    server = serverFor(true, seen);
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
