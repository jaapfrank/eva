const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const origin = process.env.EVA_PROJECT_ORIGIN || 'http://127.0.0.1:44712';
const output = process.env.EVA_PROJECT_SCREENSHOTS;

async function main() {
  const payload = await (await fetch(`${origin}/project-state`)).json();
  assert.equal(payload.contract, 'EvaProjectOverview/v1');
  const board = JSON.parse(fs.readFileSync(process.env.EVA_PROJECT_BOARD_FILE || path.resolve(__dirname, '../../design/product/execution-workboard-v1.json')));
  assert.equal(payload.active.id, board.activeTaskId);
  assert.equal(payload.project.acceptedDelivery?.taskId, 'CO-008');
  assert.equal(payload.project.nextOutcomes.some(item=>item.title==='Geheugen afronden'), false);
  const plannedPhases = payload.project.roadmap.filter(phase => [...payload.queued, ...payload.milestones].some(task => phase.taskIds.includes(task.id)));
  assert.deepEqual(payload.project.capabilities.map(item => item.id), ['answer-stream','conversation','memory','initiative','tools','voice','projects','browser','installation']);
  for (const id of ['voice','projects','installation']) {
    assert.notEqual(payload.project.capabilities.find(item => item.id === id).accepted, 'yes');
  }
  assert.equal(payload.completed.some(task => task.id === 'CO-008'), board.executionTasks.find(task => task.id === 'CO-008').status === 'done');
  const audit = JSON.parse(fs.readFileSync(path.join(__dirname, 'capability-audit.json')));
  assert.equal(payload.project.capabilityAudit.unavailable, undefined);
  for (const capability of payload.project.capabilities) {
    const finding = audit.findings.find(item => item.id === capability.id);
    assert.ok(capability.scope, 'each status must name its delivery scope');
    if (capability.id === 'initiative' && finding.latestObservation) {
      assert.equal(capability.visibleResult, finding.latestObservation.visibleResult);
      assert.equal(capability.gap, finding.latestObservation.remaining);
      assert.equal(payload.active.displayOutcome, capability.currentWork);
    } else if (capability.id === 'initiative' && board.audit.au008PrivateReview20260910?.live === true) {
      assert.match(capability.visibleResult, /private beoordeling/);
      assert.match(capability.gap, /Budgetmeting van/);
    } else if (capability.id === 'initiative' && board.audit.au008GeneralResearch20260910?.status === 'live') {
      assert.match(capability.visibleResult, /Algemeen publiek onderzoek is live/);
      assert.equal(capability.gap, board.audit.au008GeneralResearch20260910.remaining);
    } else if (capability.id === 'voice' && board.audit.voiceLaptopOwnerFeedback20260911?.laptopInterruptionAccepted === true) {
      assert.match(capability.visibleResult,/laptoponderbreken werkt/);
      assert.equal(capability.accepted,'no');
    } else if (capability.id === 'projects' && board.audit.au002Delivery) {
      const delivery = board.audit.au002Delivery;
      assert.equal(capability.visibleResult, delivery.currentWork || delivery.pendingSourceChange || delivery.result || finding.visibleResult);
      assert.equal(capability.gap, delivery.remaining || delivery.next || finding.gap);
      assert.ok(capability.historyNote.includes(finding.visibleResult));
      continue;
    } else assert.equal(capability.visibleResult, finding.visibleResult);
    for (const step of ['built','tested','live','accepted']) assert.equal(capability[step], finding.states[step]);
  }
  assert.deepEqual(payload.project.roadmap, board.audit.projectDashboard.roadmap);
  assert.notEqual(payload.project.practiceTests.find(test => test.id === 'BR-01').status, 'ready');
  assert.equal(JSON.stringify(payload.project.practiceTests).includes('localhost:44710'), false);
  assert.equal((await fetch(`${origin}/project-state`, { method: 'POST' })).status, 405);
  assert.equal((await fetch(`${origin}/project-state`, { headers: { Origin: 'https://external.invalid' } })).status, 403);
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM, headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    const page = await context.newPage();
    const errors = []; const external = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => { if (!request.url().startsWith(origin)) external.push(request.url()); });
    await page.goto(origin);
    await page.getByRole('heading', { name: 'Waar staat Eva écht?' }).waitFor();
    assert.equal(await page.getByRole('heading', { name:'Geheugenresultaat afgerond',exact:true,includeHidden:true }).count(),1);
    assert.equal(await page.getByRole('heading', { name: payload.active.displayTitle || payload.active.title,includeHidden:true }).count(),1);
    assert.equal(await page.locator('.matrix-toggle').count(), 9);
    assert.equal(await page.locator('.matrix-details:visible').count(), 0);
    assert.deepEqual(payload.project.completionSource.unmappedTaskIds, []);
    assert.deepEqual(await page.locator('.compact-matrix > thead th').allTextContents(), ['Volledig onderdeel', 'Klaar?', 'Getest', 'Door Jaap getest', 'Beter dan concurrentie onderzocht']);
    assert.ok((await page.locator('.matrix-scroll').boundingBox()).height < 850, 'compact nine-row overview');
    assert.equal(await page.locator('.matrix-category .completion-mark.open').count(),9);
    assert.equal(await page.locator('.matrix-category .completion-mark.done').count(),0);
    assert.equal(await page.locator('.matrix-category .proof-dot').count(),0);
    assert.equal(await page.locator('.step-count:visible').count(),9);
    for (const category of payload.project.capabilities) {
      assert.equal(category.product.complete, category.product.steps.filter(step=>step.required).every(step=>step.complete));
      assert.equal(category.product.comparison.state,'unknown');
    }
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-desktop.png'), fullPage: true });
    await page.locator('.matrix-toggle').filter({hasText:'Geheugen'}).click();
    assert.equal(await page.locator('#details-memory [data-step-id="CO-008"] .step-state.done').count(),1);
    assert.match(await page.locator('#details-memory [data-step-id="CO-008"]').innerText(),/door Jaap geaccepteerd/);
    assert.match(await page.locator('#details-memory [data-step-id="CO-004"]').innerText(),/optioneel, telt niet mee/);
    await page.locator('#details-memory').getByRole('button',{name:'Bewijs en grenzen'}).click();
    await page.getByRole('dialog',{name:'Geheugen'}).waitFor();
    await page.getByRole('button',{name:'Sluiten ×'}).click();
    await page.locator('.matrix-toggle').filter({hasText:'Geheugen'}).click();
    await page.locator('.matrix-toggle').filter({hasText:'Eigen initiatief'}).click();
    await page.getByRole('link',{name:'Open nieuwe storingschecklist ↗',exact:true}).waitFor();
    await page.locator('.matrix-toggle').filter({hasText:'Eigen initiatief'}).click();
    await page.locator('.matrix-toggle').filter({hasText:'Zelfstandig werk'}).click();
    assert.match(await page.locator('#details-projects').innerText(),/Telegram/);
    assert.equal(payload.project.capabilities.find(x=>x.id==='projects').product.jaap.state,'failed');
    await page.locator('.matrix-toggle').filter({hasText:'Zelfstandig werk'}).click();
    await page.getByRole('button', { name: /Concurrenten & keuzes/ }).click();
    for (const name of ['Hermes', 'OpenClaw', 'Agent Zero']) await page.getByRole('heading', { name, exact: true }).waitFor();
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-keuzes.png'), fullPage: true });
    await page.getByRole('button', { name: /Werk & volgorde/ }).click();
    assert.equal(await page.locator('.roadmap-phase').count(), plannedPhases.length);
    assert.equal(await page.locator('.roadmap-phase[open]').count(), 0);
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-werk.png'), fullPage: true });
    const nextTask = [...payload.queued, ...payload.milestones][0];
    assert.ok(nextTask);
    const nextPhase = plannedPhases.findIndex(phase => phase.taskIds.includes(nextTask.id));
    assert.notEqual(nextPhase, -1);
    const phase = page.locator('.roadmap-phase').nth(nextPhase);
    await phase.locator('summary').click();
    await phase.getByRole('heading', { name: nextTask.title, exact: true }).waitFor();
    await page.getByRole('textbox', { name: 'Zoek een taak' }).fill(nextTask.id);
    assert.equal(await page.locator('.task-grid article').count(), 1);
    assert.equal(await page.locator('.roadmap-phase').count(), 1);
    await page.locator('.roadmap-phase').getByRole('heading', { name: nextTask.title, exact: true }).waitFor();
    await page.getByRole('textbox', { name: 'Zoek een taak' }).fill('bestaat-niet');
    await page.getByRole('status').filter({ hasText: 'Geen taken gevonden.' }).waitFor();
    await page.getByRole('textbox', { name: 'Zoek een taak' }).fill('');
    assert.equal(await page.locator('.roadmap-phase').count(), plannedPhases.length);
    await page.getByRole('button', { name: /Overzicht/ }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-mobile.png'), fullPage: true });
    await page.locator('.matrix-toggle').filter({hasText: 'Stem & aanwezigheid'}).click();
    await page.locator('#details-voice [data-step-id="SP-01"]').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    assert.equal(await page.locator('#details-voice [data-step-id="SP-01"] .step-state.done').count(),1);
    assert.equal(await page.locator('#details-voice [data-step-id="SP-02"] .step-state.done').count(),1);
    await page.locator('.matrix-scroll').evaluate(el => { el.scrollLeft = el.scrollWidth; });
    if (output) await page.screenshot({path: path.join(output, 'eva-project-mobile-expanded.png'), fullPage: true});
    await page.locator('.matrix-toggle').filter({hasText: 'Stem & aanwezigheid'}).click();
    await page.getByRole('button', { name: /Werk & volgorde/ }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    if (output) await page.screenshot({ path: path.join(output, 'eva-project-werk-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: /Overzicht/ }).click();
    await page.locator('.focus-fold > summary').click();
    // Synthetic response change proves the mounted UI consumes fresh source data,
    // not hardcoded task text; the actual workboard is never changed by this test.
    await page.route('**/project-state', (route) => route.fulfill({ json: { ...payload,
      project: { ...payload.project, runtimeLabel: 'Synchronisatie-slot' },
      active: { ...payload.active, id:'TEST-NEXT', title: 'Synchronisatiecontrole', displayTitle:undefined, displayOutcome:undefined, displayKicker:undefined, displayStatus:undefined }, revision: 'f'.repeat(64),
    } }));
    await page.getByRole('heading', { name: 'Synchronisatiecontrole' }).waitFor({ timeout: 12000 });
    await page.getByText(/Synchronisatie-slot · controle/).waitFor();
    await page.route('**/project-state', (route) => route.fulfill({ json: { ...payload, browserAccess: { state: 'unavailable', checkedAt: payload.readAt, url: null } } }));
    await page.getByText('De juiste dashboardtoegang kan momenteel niet worden bevestigd. Wacht met deze proef.').waitFor({timeout: 12000});
    assert.equal(await page.locator('.owner-dashboard-link').count(), 0);
    await page.unroute('**/project-state');
    await page.route('**/project-state', (route) => route.fulfill({ status: 503, json: {} }));
    await page.getByRole('alert').waitFor({ timeout: 12000 });
    assert.match(await page.getByRole('alert').innerText(), /vorige controle/);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.equal((await context.cookies()).length, 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log(JSON.stringify({ status: 'passed', activeTask: payload.active.id,
      capabilities: payload.project.capabilities.length, roadmapPhases: payload.project.roadmap.length,
      viewports: ['1440x1100', '390x844'],
      sourceMatchesWorkboard: true, refreshAndFailureTested: true, externalRequests: 0,
      browserStorageEntries: 0, modelCalls: 0, dataMutations: 0 }));
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
