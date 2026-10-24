// Built owner UI with synthetic state and mutations; no production service is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard';
const endpoint = base + '/household-provisions';
const dist = process.env.EVA_DASHBOARD_DIST || path.join(__dirname, 'dist');
const binding = 'a'.repeat(64), id = 'household-provision:synthetic';
const owner = 'private-owner-must-not-be-rendered';
const state = { provision_id: id, owner_principal_id: owner, status: 'proposed', revision: 1,
  evidence: { binding_sha256: binding }, adoption: null, verification_sha256: null,
  stopped: false, effect_status: 'unknown', pending_effects: {} };
const pageState = { contract: 'EvaOwnerHouseholdProvisions/v1', provision_id: id,
  enrollment: null, provision: null, enrolled_record_missing: false, actual_working_proof_available: false };
const history = { contract: 'EvaOwnerInitiativeHistory/v1', cards: [], latest_review: null,
  learning: { current_styles: [], reflections: [], components: [] }, truncated: false };

(async () => {
  const writes = [], errors = [], external = [];
  let failPause = true, denied = false, malformed = false;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Cache-Control', 'no-store');
    if (url.pathname === base || url.pathname === base + '/') {
      res.setHeader('Content-Type', 'text/html'); return res.end(fs.readFileSync(path.join(dist, 'index.html')));
    }
    if (url.pathname.startsWith(base + '/assets/')) {
      const file = path.join(dist, 'assets', path.basename(url.pathname));
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css');
      return res.end(fs.readFileSync(file));
    }
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === endpoint && req.method === 'GET') {
      if (denied) { res.writeHead(503); return res.end('{}'); }
      return res.end(JSON.stringify(malformed ? { ...pageState, enrolled_record_missing: true } : pageState));
    }
    if (url.pathname.startsWith(endpoint + '/') && req.method === 'POST') {
      let body = '';
      req.on('data', bytes => body += bytes);
      req.on('end', () => {
        const value = JSON.parse(body); writes.push(value);
        assert.equal(typeof value.request_id, 'string');
        if (url.pathname === endpoint + '/register') {
          assert.deepEqual(Object.keys(value), ['request_id']);
          pageState.enrollment = pageState.actual_working_proof_available
            ? { contract: 'EvaHouseholdProvisionEnrollment/v1', provision_id: id,
              owner_principal_id: owner, binding_sha256: binding } : null;
          pageState.provision ??= { ...state };
          return res.end(JSON.stringify({ contract: 'EvaOwnerHouseholdProvisionRegistration/v1',
            receipt: { contract: 'EvaHouseholdProvisionReceipt/v1', ...pageState.provision }, enrollment: pageState.enrollment }));
        }
        assert.deepEqual(Object.keys(value).sort(), ['action', 'binding_sha256', 'expected_revision', 'provision_id', 'request_id']);
        assert.equal(value.provision_id, id); assert.equal(value.binding_sha256, binding);
        assert.equal(value.expected_revision, pageState.provision.revision);
        if (value.action === 'pause' && failPause) { failPause = false; res.writeHead(503); return res.end('{}'); }
        assert.ok(['pause', 'stop'].includes(value.action));
        pageState.provision = { ...pageState.provision, status: 'paused', revision: pageState.provision.revision + 1,
          stopped: value.action === 'stop', effect_status: value.action === 'stop' ? 'stopped' : 'unknown' };
        return res.end(JSON.stringify({ contract: 'EvaHouseholdProvisionReceipt/v1', ...pageState.provision,
          in_flight_effect_may_complete: value.action === 'pause' }));
      }); return;
    }
    if (url.pathname === base + '/initiatives/history') return res.end(JSON.stringify(history));
    if (url.pathname === base + '/status') return res.end(JSON.stringify({ contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } }));
    if (url.pathname === base + '/identity') return res.end(JSON.stringify({ contract: 'EvaDashboardIdentity/v1', read_only: true,
      identity: { display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1,
        state: 'current', temperament: [], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: [] } } }));
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true,
      args: ['--no-sandbox', '--disable-gpu'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => {
      if (route.request().url().startsWith(origin + '/')) return route.continue();
      external.push(route.request().url()); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(12000);
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + base + '#/?section=initiative');
    const section = page.getByRole('region', { name: 'Huisvoorzieningen', exact: true });
    await section.getByRole('button', { name: 'Als voorstel registreren', exact: true }).waitFor();
    assert.equal(writes.length, 0);
    await section.getByRole('button', { name: 'Als voorstel registreren', exact: true }).click();
    await section.getByText('Registratie niet gekoppeld', { exact: true }).waitFor();
    assert.equal(writes.length, 1);
    for (const name of ['Registratie afronden', 'Pauzeren', 'Stoppen', 'Voorziening aannemen', 'Hervatten']) {
      assert.equal(await section.getByRole('button', { name, exact: true }).count(), 0);
    }
    assert.equal(pageState.enrollment, null);
    // Actual proof is supplied by the isolated host fixture, never by a UI POST.
    pageState.actual_working_proof_available = true; pageState.provision.effect_status = 'clear';
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByRole('button', { name: 'Registratie afronden', exact: true }).waitFor();
    assert.equal(writes.length, 1);
    await section.getByRole('button', { name: 'Registratie afronden', exact: true }).click();
    await section.getByText('Voorgesteld', { exact: true }).waitFor();
    await section.getByRole('button', { name: 'Voorziening aannemen', exact: true }).waitFor();
    assert.equal(writes.length, 2); assert.notEqual(writes[0].request_id, writes[1].request_id);
    assert.equal(pageState.provision.revision, 1); assert.equal(pageState.provision.adoption, null);
    if (process.env.EVA_DASHBOARD_PROOF_HANDOFF_ONLY === '1') {
      assert.equal((await section.innerText()).includes(owner), false);
      assert.deepEqual(errors, []); assert.deepEqual(external, []);
      console.log(JSON.stringify({ contract: 'EvaHouseholdProvisionProofHandoffBrowser/v1', status: 'passed',
        mode: 'isolated changed cause', proposalBeforeProofLeavesUnenrolled: true,
        noCompletionControlsBeforeProof: true, proofArrivalDoesNotAutoPost: true,
        explicitCompletionKeepsProposalUnadopted: true, registerBodyRequestIdOnly: true,
        syntheticPosts: writes.length, automaticPosts: 0, externalRequests: 0, modelCalls: 0 }));
      return;
    }
    pageState.actual_working_proof_available = false; pageState.provision.effect_status = 'unknown';
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByText('De werking is nog niet bevestigd. De voorziening kan daarom nog niet actief worden of hervatten.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('button', { name: 'Voorziening aannemen', exact: true }).count(), 0);
    assert.equal(await section.getByRole('button', { name: 'Hervatten', exact: true }).count(), 0);
    assert.equal((await section.innerText()).includes(owner), false);
    await section.getByRole('button', { name: 'Pauzeren', exact: true }).click();
    await section.getByRole('alert').waitFor();
    assert.equal(await section.getByRole('article').count(), 0);
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByRole('button', { name: 'Pauzeren', exact: true }).click();
    await section.getByText('Gepauzeerd', { exact: true }).waitFor();
    assert.equal(writes.length, 4); assert.equal(writes[2].request_id, writes[3].request_id);
    assert.equal(await section.getByRole('button', { name: 'Hervatten', exact: true }).count(), 0);
    await section.getByRole('button', { name: 'Stoppen', exact: true }).click();
    await section.getByText('Deze voorziening is gestopt en kan niet worden hervat.', { exact: true }).waitFor();
    assert.equal(writes[4].expected_revision, 2); assert.equal(writes.length, 5);
    assert.equal(await section.getByRole('button', { name: 'Stoppen', exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.EVA_DASHBOARD_SCREENSHOT) await section.screenshot({ path: process.env.EVA_DASHBOARD_SCREENSHOT });
    pageState.enrollment = null; pageState.provision = { ...state }; pageState.actual_working_proof_available = true;
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByText('Registratie niet gekoppeld', { exact: true }).waitFor();
    for (const name of ['Pauzeren', 'Stoppen', 'Voorziening beëindigen', 'Voorstel afwijzen', 'Voorziening aannemen', 'Hervatten']) {
      assert.equal(await section.getByRole('button', { name, exact: true }).count(), 0);
    }
    assert.equal(writes.length, 5);
    await section.getByRole('button', { name: 'Registratie afronden', exact: true }).click();
    await section.getByText('Voorgesteld', { exact: true }).waitFor();
    assert.equal(writes.length, 6); assert.notEqual(writes[5].request_id, writes[0].request_id);
    malformed = true;
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByRole('alert').waitFor(); assert.equal(await section.getByRole('article').count(), 0);
    malformed = false; denied = true;
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByText('Huisvoorzieningen zijn momenteel niet beschikbaar.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('article').count(), 0); assert.equal(writes.length, 6);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ contract: 'EvaHouseholdProvisionBrowserProof/v1', status: 'passed', mode: 'isolated',
      registerBodyHostDerived: true, unknownProofBlocksActivation: true, pauseStopRevisionBound: true,
      explicitRetryKeepsRequestId: true, errorsClearPriorState: true, privateOwnerNotRendered: true,
      unboundRecordHasNoControls: true, explicitPartialRegistrationRecovery: true,
      mobileWidth: 390, syntheticPosts: writes.length, automaticPosts: 0, externalRequests: 0, modelCalls: 0 }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
