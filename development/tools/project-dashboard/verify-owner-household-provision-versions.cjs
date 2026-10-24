// Actual compiled UI against isolated source-version fixtures, never production.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base = '/internal/v1/dashboard', endpoint = base + '/household-provisions';
const dist = process.env.EVA_DASHBOARD_DIST;
const id = 'household-provision:version-fixture', owner = 'private-owner-not-for-display';
const a = 'a'.repeat(64), b = 'b'.repeat(64), va = '1'.repeat(64), vb = '2'.repeat(64), choice = 'c'.repeat(64);
const versions = {
  [va]: { evidence: { binding_sha256: a, reference_id: 'private-reference-a' }, parent_version: null, recorded_at: '2026-10-24T01:00:00Z' },
  [vb]: { evidence: { binding_sha256: b, reference_id: 'private-reference-b' }, parent_version: va, recorded_at: '2026-10-24T01:10:00Z' },
};
const state = { provision_id: id, owner_principal_id: owner, revision: 1, status: 'active',
  evidence: versions[va].evidence, adoption: { owner_principal_id: owner }, verification_sha256: 'e'.repeat(64),
  stopped: false, pending_effects: {}, effect_status: 'clear', current_version: va, previous_version: null,
  versions: { [va]: versions[va] }, pending_change: null };
const pageState = { contract: 'EvaOwnerHouseholdProvisions/v1', provision_id: id, provision: state,
  enrollment: { contract: 'EvaHouseholdProvisionEnrollment/v1', provision_id: id, owner_principal_id: owner, binding_sha256: a },
  enrolled_record_missing: false, actual_working_proof_available: true };

(async () => {
  const writes = [], errors = [], external = [];
  let firstChange = true, staleHistory = false, invalidPointer = false;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Cache-Control', 'no-store');
    if (url.pathname === base || url.pathname === base + '/') {
      res.setHeader('Content-Type', 'text/html'); return res.end(fs.readFileSync(path.join(dist, 'index.html')));
    }
    if (url.pathname.startsWith(base + '/assets/')) {
      const file = path.join(dist, 'assets', path.basename(url.pathname));
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css'); return res.end(fs.readFileSync(file));
    }
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === endpoint && req.method === 'GET') return res.end(JSON.stringify(invalidPointer
      ? { ...pageState, enrollment: { ...pageState.enrollment, binding_sha256: 'f'.repeat(64) } } : pageState));
    if (url.pathname === endpoint + '/versions' && req.method === 'GET') return res.end(JSON.stringify({
      contract: 'EvaHouseholdProvisionChoices/v1', choices: state.revision === 1 ? [{ choice_id: choice,
        label: 'Rustiger meldingen', state: 'reviewed_saved', reference_id: 'private-reference-b', review_id: 'private-review' }] : [] }));
    if (url.pathname === endpoint + '/' + encodeURIComponent(id) + '/history') return res.end(JSON.stringify({
      contract: 'EvaHouseholdProvisionHistory/v1', provision_id: id, revision: state.revision + Number(staleHistory),
      entries: [{ revision: state.revision, event: 'HOUSEHOLD_PROVISION_CHANGE_VERIFIED', at: '2026-10-24T01:20:00Z', status: state.status, version_id: state.current_version }], earlier_entries: 0 }));
    if (url.pathname.startsWith(endpoint + '/versions/') && req.method === 'POST') {
      let body = ''; req.on('data', bytes => body += bytes);
      req.on('end', () => {
        const value = JSON.parse(body), action = url.pathname.split('/').at(-1); writes.push({ action, ...value });
        const common = ['binding_sha256', 'expected_revision', 'provision_id', 'request_id'];
        assert.deepEqual(Object.keys(value).sort(), [...common, ...(action === 'stage' ? ['choice_id'] : action === 'change' ? ['version_id'] : [])].sort());
        assert.equal(value.provision_id, id); assert.equal(value.expected_revision, state.revision);
        assert.equal(value.binding_sha256, state.evidence.binding_sha256);
        if (action === 'stage') {
          assert.equal(value.choice_id, choice); state.versions[vb] = versions[vb]; state.revision += 1;
        } else if (action === 'change') {
          assert.equal(value.version_id, vb);
          if (firstChange) {
            firstChange = false; state.revision += 1; state.status = 'paused';
            state.pending_change = { from_version: va, to_version: vb, prior_status: 'active', request_id: value.request_id, undo: false };
            pageState.change_pending = { from_version: va, to_version: vb, expected_revision: state.revision,
              binding_sha256: a, needs_completion: true, undo: false, in_flight_effects_not_replayed: true };
            pageState.enrollment.binding_sha256 = b; pageState.actual_working_proof_available = false;
            res.writeHead(503); return res.end('{}');
          }
          assert.ok(state.pending_change); state.pending_change = null; delete pageState.change_pending;
          state.previous_version = va; state.current_version = vb; state.evidence = versions[vb].evidence;
          state.status = 'proposed'; state.verification_sha256 = null; state.revision += 1;
        } else {
          assert.equal(action, 'undo'); state.previous_version = vb; state.current_version = va;
          state.evidence = versions[va].evidence; state.status = 'active'; state.verification_sha256 = 'e'.repeat(64);
          state.revision += 1; pageState.enrollment.binding_sha256 = a; pageState.actual_working_proof_available = true;
        }
        return res.end(JSON.stringify({ contract: 'EvaOwnerHouseholdProvisionVersion/v1',
          receipt: { contract: 'EvaHouseholdProvisionReceipt/v1', ...state }, selected_version_id: state.current_version,
          enrollment: pageState.enrollment }));
      }); return;
    }
    if (url.pathname === base + '/initiatives/history') return res.end(JSON.stringify({ contract: 'EvaOwnerInitiativeHistory/v1', cards: [], latest_review: null, learning: { current_styles: [], reflections: [], components: [] }, truncated: false }));
    if (url.pathname === base + '/status') return res.end(JSON.stringify({ contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } }));
    if (url.pathname === base + '/identity') return res.end(JSON.stringify({ contract: 'EvaDashboardIdentity/v1', read_only: true, identity: { display_name: 'Eva', identity_id: 'synthetic', language: 'nl', pack_sha256: '0'.repeat(64), pack_version: 1, state: 'current', temperament: [], voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: [] } } }));
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port; let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => { if (route.request().url().startsWith(origin + '/')) return route.continue(); external.push(route.request().url()); return route.abort(); });
    const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + base + '#/?section=initiative');
    const section = page.getByRole('region', { name: 'Huisvoorzieningen', exact: true });
    await section.getByRole('button', { name: 'Wijziging klaarzetten: Rustiger meldingen', exact: true }).waitFor();
    assert.equal(writes.length, 0);
    assert.equal((await section.innerText()).includes('private-reference'), false);
    assert.equal((await section.innerText()).includes(owner), false);
    await section.getByRole('button', { name: 'Wijziging klaarzetten: Rustiger meldingen', exact: true }).click();
    await section.getByRole('button', { name: 'Deze versie gebruiken', exact: true }).click();
    await section.getByText('De wijziging is niet bevestigd. Vernieuw het overzicht voordat je opnieuw probeert.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('article').count(), 0);
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByRole('button', { name: 'Wijziging afronden', exact: true }).waitFor();
    assert.equal(await section.getByRole('button', { name: 'Voorziening beëindigen', exact: true }).count(), 0);
    assert.equal(await section.getByRole('button', { name: 'Hervatten', exact: true }).count(), 0);
    assert.equal(await section.getByRole('button', { name: 'Stoppen', exact: true }).count(), 1);
    assert.equal(writes.length, 2);
    await section.getByRole('button', { name: 'Wijziging afronden', exact: true }).click();
    await section.getByRole('button', { name: 'Vorige versie terugzetten', exact: true }).waitFor();
    assert.equal(writes[2].expected_revision, 3); assert.equal(writes[2].binding_sha256, a);
    assert.notEqual(writes[2].request_id, writes[1].request_id);
    assert.equal(state.status, 'proposed'); assert.equal(pageState.actual_working_proof_available, false);
    await section.getByRole('button', { name: 'Vorige versie terugzetten', exact: true }).click();
    await section.getByText('Actief', { exact: true }).waitFor();
    assert.equal(writes[3].binding_sha256, b); assert.equal(writes[3].expected_revision, 4);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    staleHistory = true;
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByText('De versiegeschiedenis is gewijzigd of niet beschikbaar. Vernieuw het overzicht.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('button', { name: 'Vorige versie terugzetten', exact: true }).count(), 0);
    staleHistory = false; invalidPointer = true;
    await section.getByRole('button', { name: 'Huisvoorzieningen vernieuwen', exact: true }).click();
    await section.getByText('De actuele voorziening kon niet worden gelezen.', { exact: true }).waitFor();
    assert.equal(await section.getByRole('article').count(), 0);
    assert.equal(writes.length, 4); assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ contract: 'EvaHouseholdProvisionVersionBrowserProof/v1', status: 'passed',
      serverChoicesOnly: true, noPrivateIdentifiersRendered: true, pendingPointerOnTargetStillRecoverable: true,
      pendingUsesOldBindingAndCurrentRevision: true, explicitUndoUsesCurrentBinding: true,
      staleMetadataAndUnknownPointerDisableVersionControls: true, mobileWidth: 390,
      syntheticPosts: writes.length, automaticPosts: 0, externalRequests: 0, modelCalls: 0 }));
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
