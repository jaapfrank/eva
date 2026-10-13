// Isolated browser proof: no real owner, worker, model, or copied dashboard token.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const dist = path.resolve(__dirname, 'dist');
const base = '/internal/v1/dashboard';
const work = (id, state, generation = 1) => ({ work_id: id, state, generation,
  stop_requested: false, task: 'Onderzoek de tuin', task_preview: 'Onderzoek de tuin',
  result_available: state === 'succeeded', result: state === 'succeeded'
    ? { output: '<img src="https://external.invalid/leak"> Bewaard tuinadvies' } : null });
let jobs = [work('work:active', 'admitted'), work('work:finished', 'succeeded'), work('work:running', 'call_started')];
let denied = false;
const calls = [];
let heldId = null, releaseHeld, heldStarted;
let active = 0, maximumActive = 0;
const mutations = [];
const fixtures = {
  '/status': { contract: 'EvaDashboardStatus/v1', read_only: true, runtime: { ready: true, state: 'ready' } },
  '/identity': { contract: 'EvaDashboardIdentity/v1', read_only: true, identity: {
    display_name: 'Eva', identity_id: 'fixture', language: 'nl', pack_sha256: '0'.repeat(64),
    pack_version: 1, state: 'current', temperament: ['warm'],
    voice_style: { maximum_paragraphs: 3, name: 'warm', qualities: ['helder'] } } },
  '/workboard': { contract: 'EvaWorkBoard/v1', schema_version: 1, read_only: true,
    status: 'ready', truncated: false, message: '', cards: [] },
  '/memory-sources': { contract: 'EvaOwnerMemorySources/v1', read_only: true, meaning: 'Eigen bronnen', sources: [], next_cursor: null },
  '/initiatives': { contract: 'EvaOwnerInitiatives/v1', cards: [], truncated: false, latest_review: null },
  '/browser/status': { contract: 'EvaBrowserSession/v1', state: 'stopped', revision: 0,
    owner_control: false, eva_control: false, viewer_path: null, browser_ready: false },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const route = url.pathname.slice(base.length);
  const json = data => { res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(data)); };
  if (route === '/work' || route.startsWith('/work/')) {
    active++; maximumActive = Math.max(maximumActive, active);
    res.once('finish', () => active--);
    calls.push(route);
    if (denied) { res.statusCode = 403; return json({ private: 'MUST NOT APPEAR' }); }
    const operation = route === '/work' ? 'list' : route.split('/')[2];
    const reply = { contract: 'EvaDelegatedWorkOwnerResult/v1', operation };
    if (operation === 'list') return json({ ...reply, jobs, truncated: false });
    let body = ''; for await (const chunk of req) body += chunk;
    const data = JSON.parse(body);
    assert.deepEqual(Object.keys(data), ['work_id']);
    let selected = jobs.find(job => job.work_id === data.work_id);
    if (operation === 'stop') {
      if (selected.state === 'call_started') selected.stop_requested = true;
      else selected.state = 'stopped';
      mutations.push(operation);
    }
    if (operation === 'resume') {
      selected = work('work:resumed', 'admitted', 2); jobs.unshift(selected); mutations.push(operation);
    }
    const snapshot = JSON.parse(JSON.stringify(selected));
    if (operation === 'get' && data.work_id === heldId) {
      heldStarted();
      await new Promise(resolve => { releaseHeld = resolve; });
    }
    if (denied) { res.statusCode = 403; return json({ private: 'MUST NOT APPEAR' }); }
    return json({ ...reply, work: snapshot, created: operation === 'resume' });
  }
  if (fixtures[route]) return json(fixtures[route]);
  const file = route.startsWith('/assets/') ? path.join(dist, route) : path.join(dist, 'index.html');
  res.setHeader('content-type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM,
    headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    await page.clock.install();
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => { if (!req.url().startsWith('http://127.0.0.1:')) external.push(req.url()); });
    await page.goto(`http://127.0.0.1:${server.address().port}${base}`);
    const area = page.getByRole('region', { name: 'Mijn hulpopdrachten' });
    await area.getByRole('button', { name: 'Opdracht openen' }).nth(1).click();
    await area.getByText('<img src="https://external.invalid/leak"> Bewaard tuinadvies', { exact: true }).waitFor();
    assert.equal(await area.locator('img').count(), 0);
    await area.getByRole('button', { name: 'Opdracht openen' }).first().click();
    await area.getByRole('button', { name: 'Stop deze hulpopdracht' }).click();
    await area.getByText('Gestopt voordat een modelcall begon.', { exact: true }).waitFor();
    assert.equal(jobs[0].state, 'stopped');
    await area.getByRole('button', { name: 'Hervatten met nieuwe poging' }).click();
    await area.getByText('Nieuwe poging ingepland. De vorige poging blijft bewaard.', { exact: true }).waitFor();
    assert.equal(jobs[0].generation, 2);
    await area.getByRole('button', { name: 'Werk vernieuwen' }).click();
    await area.getByRole('article', { name: 'Geopende hulpopdracht' }).getByText('Ingepland · poging 2', { exact: true }).waitFor();
    assert.equal(await area.getByRole('article', { name: 'Geopende hulpopdracht' }).count(), 1);
    await area.getByRole('button', { name: 'Opdracht openen' }).nth(3).click();
    await area.getByRole('button', { name: 'Stop deze hulpopdracht' }).click();
    await area.getByText('Stopverzoek opgeslagen. Een lopende berekening kan nog aflopen; het resultaat wordt niet geleverd.', { exact: true }).waitFor();
    assert.equal(jobs[3].state, 'call_started');
    assert.equal(jobs[3].stop_requested, true);
    assert.equal(await area.getByRole('button', { name: 'Hervatten met nieuwe poging' }).count(), 0);
    await area.getByRole('button', { name: 'Opdracht openen' }).nth(2).click();
    await area.getByText('<img src="https://external.invalid/leak"> Bewaard tuinadvies', { exact: true }).waitFor();
    const opened = area.getByRole('article', { name: 'Geopende hulpopdracht' });
    await area.getByRole('button', { name: 'Opdracht openen' }).first().click();
    await opened.getByText('Ingepland · poging 2', { exact: true }).waitFor();
    const getCount = () => calls.filter(route => route === '/work/get').length;
    const unchangedGets = getCount();
    let response = page.waitForResponse(res => res.url().endsWith('/work'));
    await page.clock.fastForward(5001); await response;
    await page.waitForTimeout(50);
    assert.equal(getCount(), unchangedGets);
    jobs[0].state = 'call_started';
    await page.clock.fastForward(5001);
    await opened.getByText('Bezig · poging 2', { exact: true }).waitFor();
    Object.assign(jobs[0], work('work:resumed', 'succeeded', 2));
    await page.clock.fastForward(5001);
    await opened.getByText('Resultaat klaar · poging 2', { exact: true }).waitFor();
    await opened.getByText('<img src="https://external.invalid/leak"> Bewaard tuinadvies', { exact: true }).waitFor();
    const finishedGets = getCount();
    response = page.waitForResponse(res => res.url().endsWith('/work'));
    await page.clock.fastForward(5001); await response;
    await page.waitForTimeout(50);
    assert.equal(getCount(), finishedGets);
    // Hold the old selection response while the owner chooses a newer task.
    heldId = 'work:finished';
    const started = new Promise(resolve => { heldStarted = resolve; });
    await area.getByRole('button', { name: 'Opdracht openen' }).nth(2).click();
    await started;
    await area.getByRole('button', { name: 'Opdracht openen' }).first().click();
    await page.clock.fastForward(15001);
    assert.equal(maximumActive, 1);
    releaseHeld(); heldId = null;
    await opened.getByText('Resultaat klaar · poging 2', { exact: true }).waitFor();
    assert.equal(await opened.getByText('Resultaat klaar · poging 1', { exact: true }).count(), 0);
    // Exercise document visibility independently of headless window activation.
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const hiddenCalls = calls.length;
    await page.clock.fastForward(20001);
    await page.waitForTimeout(50);
    assert.equal(calls.length, hiddenCalls);
    response = page.waitForResponse(res => res.url().endsWith('/work'));
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await response; await page.waitForTimeout(50);
    denied = true;
    await page.clock.fastForward(5001);
    await area.getByRole('alert').waitFor();
    assert.equal(await area.getByText('<img src="https://external.invalid/leak"> Bewaard tuinadvies', { exact: true }).count(), 0);
    assert.equal((await area.innerText()).includes('MUST NOT APPEAR'), false);
    const deniedCalls = calls.length;
    await page.clock.fastForward(20001);
    await page.waitForTimeout(50);
    assert.equal(calls.length, deniedCalls);
    assert.equal(await opened.count(), 0);
    assert.equal(maximumActive, 1);
    denied = false;
    await area.getByRole('button', { name: 'Werk vernieuwen' }).click();
    await area.getByRole('button', { name: 'Opdracht openen' }).first().waitFor();
    // A denied superseded request must also clear the pending newer selection.
    heldId = 'work:finished';
    const deniedStarted = new Promise(resolve => { heldStarted = resolve; });
    await area.getByRole('button', { name: 'Opdracht openen' }).nth(2).click();
    await deniedStarted;
    await area.getByRole('button', { name: 'Opdracht openen' }).first().click();
    denied = true; releaseHeld(); heldId = null;
    await area.getByRole('alert').waitFor();
    assert.equal(await opened.count(), 0);
    const staleDeniedCalls = calls.length;
    await page.clock.fastForward(10001); await page.waitForTimeout(50);
    assert.equal(calls.length, staleDeniedCalls);
    denied = false;
    await area.getByRole('button', { name: 'Werk vernieuwen' }).click();
    await area.getByRole('button', { name: 'Opdracht openen' }).first().waitFor();
    heldId = 'work:finished';
    const unmountStarted = new Promise(resolve => { heldStarted = resolve; });
    await area.getByRole('button', { name: 'Opdracht openen' }).nth(2).click();
    await unmountStarted;
    const aborted = page.waitForEvent('requestfailed', req => req.url().endsWith('/work/get'));
    // Switching away from the dashboard unmounts OwnerWork.
    await page.evaluate(() => { window.location.hash = '#/workboard'; });
    await area.waitFor({ state: 'detached' });
    await aborted;
    releaseHeld(); heldId = null;
    const unmountedCalls = calls.length;
    await page.clock.fastForward(20001); await page.waitForTimeout(50);
    assert.equal(calls.length, unmountedCalls);
    assert.deepEqual(mutations, ['stop', 'resume', 'stop']);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log(JSON.stringify({ status: 'passed', open: true, literalResult: true,
      durableFixtureStop: true, activeCallStopRequest: true, resumeNewAttempt: true, refreshPreservesSelection: true, automaticCompletion: true, unchangedResultNotRefetched: true,
      rapidSelectionIgnoresStaleResponse: true, maximumActiveRequests: maximumActive, hiddenPausesPolling: true,
      deniedClearsPrivateContentAndStopsPolling: true, supersededDenialClearsPrivateContent: true,
      unmountStopsPollingAndAbortsRequest: true, fixtureRequests: calls.length,
      realRuntimeOrOwnerAcceptance: false }));
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
