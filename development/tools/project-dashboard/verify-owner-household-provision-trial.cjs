// Compiled owner UI with isolated trial fixtures; no production requests.
const assert = require('node:assert/strict'), fs = require('node:fs'), http = require('node:http'), path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard', endpoint = base + '/household-provisions', dist = process.env.EVA_DASHBOARD_DIST;
const id = 'household-provision:trial-fixture', owner = 'private-owner', binding = 'a'.repeat(64), version = 'b'.repeat(64);
const state = { provision_id: id, owner_principal_id: owner, revision: 1, status: 'proposed', evidence: { binding_sha256: binding },
  adoption: { method: 'earlier-version' }, stopped: false, pending_effects: {}, effect_status: 'clear', verification_sha256: null,
  current_version: version, trial: null };
const overview = { contract: 'EvaOwnerHouseholdProvisions/v1', provision_id: id, provision: state,
  enrollment: { contract: 'EvaHouseholdProvisionEnrollment/v1', provision_id: id, owner_principal_id: owner, binding_sha256: binding },
  enrolled_record_missing: false, actual_working_proof_available: false };
(async () => {
  const posts = [], errors = [], external = []; let rejectAck = false;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost'); res.setHeader('Cache-Control', 'no-store');
    if ([base, base + '/'].includes(url.pathname)) { res.setHeader('Content-Type', 'text/html'); return res.end(fs.readFileSync(path.join(dist, 'index.html'))); }
    if (url.pathname.startsWith(base + '/assets/')) { const file = path.join(dist, 'assets', path.basename(url.pathname)); res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css'); return res.end(fs.readFileSync(file)); }
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === endpoint) return res.end(JSON.stringify(overview));
    if (url.pathname === endpoint + '/versions') return res.end(JSON.stringify({ contract: 'EvaHouseholdProvisionChoices/v1', choices: [] }));
    if (url.pathname === endpoint + '/' + encodeURIComponent(id) + '/history') return res.end(JSON.stringify({ contract: 'EvaHouseholdProvisionHistory/v1', provision_id: id, revision: state.revision, entries: [], earlier_entries: 0 }));
    if (url.pathname.startsWith(endpoint + '/trial/') && req.method === 'POST') {
      let body = ''; req.on('data', bytes => body += bytes); req.on('end', () => {
        const payload = JSON.parse(body), action = url.pathname.split('/').at(-1); posts.push({ action, ...payload });
        const extra = { authorize: ['expires_at_epoch', 'max_effects'], evaluate: ['working', 'useful', 'feedback'], acknowledge: ['notice_id', 'nonce'] }[action];
        assert.deepEqual(Object.keys(payload).sort(), ['provision_id', 'expected_revision', 'binding_sha256', 'request_id', ...extra].sort());
        assert.equal(payload.expected_revision, state.revision); assert.equal(payload.binding_sha256, binding); assert.equal(payload.provision_id, id);
        if (action === 'authorize') {
          assert.equal(payload.max_effects, 2); assert.ok(Math.abs(payload.expires_at_epoch - Date.now() / 1000 - 7200) < 5);
          state.trial = { trial_id: 'c'.repeat(64), version_id: version, binding_sha256: binding, owner_principal_id: owner,
            expires_at_epoch: payload.expires_at_epoch, max_effects: 2, effect_count: 0, phase: 'authorized', evaluation: null, notice: null };
        } else if (action === 'evaluate') {
          assert.equal(payload.working, true); assert.equal(payload.useful, true); assert.equal(payload.feedback, 'De melding hielp op tijd.');
          state.trial.effect_count = 1; state.trial.phase = 'evaluated_awaiting_notice';
          state.trial.evaluation = { working: true, useful: true, feedback: payload.feedback, owner_principal_id: owner, proof_sha256: 'd'.repeat(64) };
          state.trial.notice = { notice_id: 'e'.repeat(64), nonce: 'f'.repeat(64), message: 'De proef werkt en was nuttig. De voorziening blijft binnen dezelfde afspraken bestaan.',
            trial_id: state.trial.trial_id, version_id: version, owner_principal_id: owner, acknowledged_at: null };
        } else {
          assert.equal(payload.notice_id, state.trial.notice.notice_id); assert.equal(payload.nonce, state.trial.notice.nonce);
          if (rejectAck) { res.writeHead(503); return res.end('{}'); }
          state.status = 'active'; state.trial.phase = 'adopted'; state.trial.notice.acknowledged_at = new Date().toISOString();
          state.adoption = { method: 'prior_authorized_trial' }; state.verification_sha256 = 'd'.repeat(64); overview.actual_working_proof_available = true;
        }
        state.revision += 1; return res.end(JSON.stringify({ contract: 'EvaHouseholdProvisionReceipt/v1', ...state }));
      }); return;
    }
    if (url.pathname === base + '/initiatives/history') return res.end(JSON.stringify({ contract: 'EvaOwnerInitiativeHistory/v1', cards: [], latest_review: null, learning: { current_styles: [], reflections: [], components: [] }, truncated: false }));
    if (url.pathname === base + '/status') return res.end(JSON.stringify({ contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } }));
    if (url.pathname === base + '/identity') return res.end(JSON.stringify({ contract: 'EvaDashboardIdentity/v1', read_only: true, identity: { display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1, state: 'current', temperament: [], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: [] } } }));
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); const origin = 'http://127.0.0.1:' + server.address().port; let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await context.route('**/*', route => { if (route.request().url().startsWith(origin + '/')) return route.continue(); external.push(route.request().url()); return route.abort(); });
    const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + base + '#/?section=initiative');
    const section = page.getByRole('region', { name: 'Huisvoorzieningen', exact: true });
    await section.getByRole('button', { name: 'Proef toestaan', exact: true }).waitFor(); assert.equal(posts.length, 0);
    await section.getByLabel('Duur in uren', { exact: true }).fill('2'); await section.getByLabel('Maximale uitvoeringen', { exact: true }).fill('2');
    await section.getByRole('button', { name: 'Proef toestaan', exact: true }).click();
    await section.getByLabel('Werkte de voorziening?', { exact: true }).waitFor(); assert.equal(posts.length, 1); assert.equal(state.status, 'proposed');
    assert.equal(await section.getByRole('button', { name: 'Beoordeling vastleggen', exact: true }).isEnabled(), false);
    await section.getByLabel('Werkte de voorziening?', { exact: true }).selectOption('yes'); await section.getByLabel('Was de voorziening nuttig?', { exact: true }).selectOption('yes');
    await section.getByLabel('Wat heb je ervaren?', { exact: true }).fill('De melding hielp op tijd.');
    await section.getByRole('button', { name: 'Beoordeling vastleggen', exact: true }).click();
    await section.getByRole('button', { name: 'Melding gelezen', exact: true }).waitFor();
    await section.getByText(state.trial.notice.message, { exact: true }).waitFor();
    assert.equal(posts.length, 2); assert.equal(state.status, 'proposed'); assert.equal(state.adoption.method, 'earlier-version');
    const text = await section.innerText(); assert.equal(text.includes(state.trial.notice.nonce), false); assert.equal(text.includes(owner), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    rejectAck = true; await section.getByRole('button', { name: 'Melding gelezen', exact: true }).click();
    await section.getByText('De wijziging is niet bevestigd. Vernieuw het overzicht voordat je opnieuw probeert.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('article').count(), 0); assert.equal(posts.length, 3); assert.equal(state.status, 'proposed');
    rejectAck = false; await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByRole('button', { name: 'Melding gelezen', exact: true }).click();
    await section.getByText('De beoordeelde proef is blijvend aangenomen.', { exact: true }).waitFor();
    assert.equal(posts[2].request_id, posts[3].request_id); assert.equal(posts.length, 4); assert.equal(state.status, 'active');
    state.status = 'proposed'; state.trial.phase = 'authorized'; state.trial.expires_at_epoch = Math.floor(Date.now()/1000) - 2;
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByText('De toestemming voor deze proef is verlopen.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('button', { name: 'Beoordeling vastleggen', exact: true }).count(), 0);
    state.stopped = true; await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByText('Deze voorziening is gestopt en kan niet worden hervat.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('button', { name: 'Proef toestaan', exact: true }).count(), 0);
    assert.equal(posts.length, 4); assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ contract: 'EvaHouseholdProvisionTrialBrowserProof/v1', status: 'passed',
      explicitGrantOnly: true, positiveEvaluationDoesNotAdopt: true, exactServerNoticeRenderedBeforeExplicitAck: true,
      failedAckReusesRequestIdOnlyAfterExplicitRefresh: true, expiryAndStopDisableTrial: true, mobileWidth: 390,
      syntheticPosts: posts.length, automaticPosts: 0, externalRequests: 0, modelCalls: 0 }));
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
