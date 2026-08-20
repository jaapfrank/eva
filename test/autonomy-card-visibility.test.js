import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-autonomy-card-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.OPENAI_API_KEY = 'test-only';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
process.env.NODE_ENV = 'test';

const { db, kvSet, logContributionOutcome } = await import('../src/db.js');
const { createDurableWork, manageGoal } = await import('../src/mind/portfolio.js');
const {
  __autonomyScheduleTest, autonomyStatus, startAutonomy,
} = await import('../src/mind/autonomy.js');
const { startWeb } = await import('../src/web/server.js');
const {
  autonomyCardCopy, createLoadWork, renderAutonomyCard,
} = await import('../src/web/public/autonomycard.js');

const KEY = 'eva_autonomy_scheduler_v1';
let server = null;

after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function elements() {
  return {
    now: { textContent: '' },
    why: { textContent: '' },
    result: { textContent: '' },
  };
}

function baseUrl() {
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  return `http://127.0.0.1:${address.port}`;
}

async function workStatus(fetcher = fetch) {
  const response = await fetcher(`${baseUrl()}/api/workstatus`);
  const body = await response.json();
  assert.equal(response.status, 200, JSON.stringify(body));
  return body;
}

test('autonomiekaart toont alleen actueel gebonden keuze, reden en echt zichtbaar resultaat', async () => {
  assert.equal(typeof createLoadWork, 'function',
    'app en test gebruiken exact dezelfde loadWork-grens');
  const resultSummary = 'De vaste boodschappenlijst voorkomt voortaan een dubbele rit.';
  const outcomeKey = 'a'.repeat(64);
  const visible = logContributionOutcome(outcomeKey, `Eva ontdekte: ${resultSummary}`, {
    internalOnly: false,
    familyVisible: true,
    source: 'bijdrage', status: 'done', evidence: resultSummary,
    lifecycleContract: 'ExplorationTerminalContribution/v1',
    familyResult: {
      contract: 'ExplorationFamilyResult/v1', sourceOutcomeKey: outcomeKey,
      title: 'Vaste boodschappenlijst', familyBenefit: resultSummary,
    },
  });
  assert.equal(visible.created, true);
  const resultRunTs = new Date().toISOString();
  kvSet(KEY, {
    last_run_ts: resultRunTs,
    next_run_ts: new Date(Date.now() + 60 * 60_000).toISOString(),
    last_result: {
      turn_id: 'autonomy:internal:9381', productive: true, progressed: false,
      tool: 'verkenning_bijhouden', summary: resultSummary,
      error: 'Verborgen interne providerfout met herstelcontext.',
    },
    pending_signals: [{ id: 'signal:private:1', text: 'Verborgen gezinssignaal.' }],
    quarantined_signals: [{ id: 'signal:private:2', text: 'Verborgen quarantaine.' }],
  });
  server = await startWeb();

  const visibleStatus = await workStatus();
  assert.deepEqual(Object.keys(visibleStatus.autonomy).sort(), [
    'current_selection', 'display', 'enabled', 'last_result', 'last_run_ts', 'next_run_ts',
    'pending_signal_count', 'quarantined_signal_count', 'running', 'scheduler_started',
  ]);
  assert.equal(visibleStatus.autonomy.enabled, true);
  assert.equal(visibleStatus.autonomy.scheduler_started, false);
  assert.equal(Object.hasOwn(visibleStatus.autonomy, 'pending_signals'), false);
  assert.equal(Object.hasOwn(visibleStatus.autonomy, 'quarantined_signals'), false);
  assert.doesNotMatch(JSON.stringify(visibleStatus.autonomy),
    /Verborgen interne providerfout|Verborgen gezinssignaal|Verborgen quarantaine/iu);
  assert.deepEqual(Object.keys(visibleStatus.autonomy.display).sort(), [
    'choice_label', 'reason_label', 'result_expires_ts', 'result_label', 'result_ts',
    'selection_expires_ts', 'selection_ts',
  ]);
  assert.equal(visibleStatus.autonomy.display.choice_label, null);
  assert.equal(visibleStatus.autonomy.display.reason_label, null);
  assert.equal(visibleStatus.autonomy.display.selection_ts, null);
  assert.equal(visibleStatus.autonomy.display.selection_expires_ts, null);
  assert.equal(visibleStatus.autonomy.display.result_label, resultSummary);
  assert.equal(typeof visibleStatus.autonomy.display.result_ts, 'string');
  assert.ok(Date.parse(visibleStatus.autonomy.display.result_expires_ts)
    > Date.parse(visibleStatus.autonomy.display.result_ts));
  const resultElements = elements();
  renderAutonomyCard(resultElements, visibleStatus, {
    nowFallback: 'Ik heb nu geen zelfstandig werk openstaan.',
    whyFallback: 'Een reden wordt zichtbaar zodra ik echt iets kies.',
    resultPrefix: 'laatste werksessie zojuist', now: Date.now(),
  });
  assert.equal(resultElements.result.textContent,
    `laatste werksessie zojuist · Laatste resultaat: ${resultSummary}`);

  const internalOutcomeKey = 'd'.repeat(64);
  const internalDuplicate = logContributionOutcome(
    internalOutcomeKey, `Eva ontdekte: ${resultSummary}`, {
      internalOnly: true,
      familyVisible: false,
      source: 'bijdrage', status: 'done', evidence: resultSummary,
      lifecycleContract: 'ExplorationTerminalContribution/v1',
      familyResult: {
        contract: 'ExplorationFamilyResult/v1', sourceOutcomeKey: internalOutcomeKey,
        title: 'Interne dubbele uitkomst', familyBenefit: resultSummary,
      },
    },
  );
  assert.equal(internalDuplicate.created, true);
  kvSet(KEY, {
    last_run_ts: new Date().toISOString(),
    next_run_ts: new Date(Date.now() + 60 * 60_000).toISOString(),
    last_result: { productive: true, progressed: false, summary: resultSummary },
  });
  const shadowedResultStatus = await workStatus();
  assert.equal(shadowedResultStatus.autonomy.display.result_label, null,
    'de nieuwste matching interne bijdrage mag nooit terugvallen op een oudere zichtbare kopie');
  assert.equal(shadowedResultStatus.autonomy.display.result_ts, null);
  assert.equal(shadowedResultStatus.autonomy.display.result_expires_ts, null);

  kvSet(KEY, {
    last_run_ts: new Date(Date.now() - 2 * 24 * 60 * 60_000).toISOString(),
    last_result: { productive: true, progressed: false, summary: resultSummary },
  });
  const staleResultStatus = await workStatus();
  assert.equal(staleResultStatus.autonomy.display.result_label, null);
  assert.equal(staleResultStatus.autonomy.display.result_ts, null);
  assert.equal(staleResultStatus.autonomy.display.result_expires_ts, null);

  kvSet(KEY, {
    last_run_ts: new Date().toISOString(),
    last_result: { productive: true, progressed: false },
  });
  const summaryMissingStatus = await workStatus();
  assert.equal(summaryMissingStatus.autonomy.display.result_label, null,
    'productief zonder gebonden gewone samenvatting is geen resultaatclaim');

  for (const candidate of [
    'exploration:17', 'worker', randomUUID(), '9381', 'b'.repeat(64),
    '<img src=x onerror=alert(1)>',
  ]) {
    assert.equal(__autonomyScheduleTest.boundedAutonomyDisplayLabel(candidate), null,
      `intern token mag geen displaylabel worden: ${candidate}`);
  }
  for (const candidate of [
    'Controleer waarom het licht om 00:15 uitging.',
    'De woonkamer was met 18 °C ongewoon koel.',
    'Vergelijk 2 van 3 geschikte plekken voor de schooltassen.',
  ]) {
    assert.equal(__autonomyScheduleTest.boundedAutonomyDisplayLabel(candidate), candidate,
      `gewone relevante cijfers moeten zichtbaar kunnen zijn: ${candidate}`);
  }
  const alignedNow = Date.now();
  for (const candidate of [
    'Controleer waarom het licht om 00:15 uitging.',
    'De woonkamer was met 18 °C ongewoon koel.',
    'Vergelijk 2 van 3 geschikte plekken voor de schooltassen.',
  ]) {
    const copy = autonomyCardCopy({ autonomy: { running: true, display: {
      choice_label: candidate,
      reason_label: 'Deze concrete observatie was nu relevant voor het gezin.',
      selection_ts: new Date(alignedNow - 45 * 60_000).toISOString(),
      selection_expires_ts: new Date(alignedNow + 15 * 60_000).toISOString(),
      result_label: null, result_ts: null, result_expires_ts: null,
    } } }, { now: alignedNow });
    assert.equal(copy.now, `Nu gekozen: ${candidate}`,
      'de browser volgt de door de server uitgegeven geldigheid, niet een eigen kortere grens');
  }

  db.prepare('DELETE FROM aspirations').run();
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (71,?,?,?,?,?,'2020-01-01T00:00:00.000Z','development',?,?,'','',1,1)`).run(
    '2020-01-01T00:00:00.000Z',
    'Onderzoek hoe de rustige ochtend beter kan werken.',
    'De ochtend gaf deze week meerdere concrete knelpunten voor het gezin.',
    'actief', '', 'autonomy-card-attention', 'het gezin',
  );
  assert.deepEqual(__autonomyScheduleTest.schedulerSelectionLabels({
    attention: Object.freeze({
      kind: 'aspiration', id: 71,
      text: 'Onderzoek hoe de rustige ochtend beter kan werken.',
    }),
  }), {
    choice_label: 'Onderzoek hoe de rustige ochtend beter kan werken.',
    reason_label: 'De ochtend gaf deze week meerdere concrete knelpunten voor het gezin.',
  }, 'attention gebruikt haar echte opgeslagen keuze en reden');
  assert.deepEqual(__autonomyScheduleTest.schedulerSelectionLabels({
    work: { next_step: 'Werk de gekozen verbetering concreet uit.' },
    rationale: { deliveryRequired: true },
  }), {
    choice_label: 'Werk de gekozen verbetering concreet uit.',
    reason_label: 'De uitkomst moet na afronding met het gezin gedeeld worden.',
  }, 'afleverplicht beschrijft een toekomstige uitkomst en claimt geen afgerond resultaat');
  assert.deepEqual(__autonomyScheduleTest.schedulerSelectionLabels({
    work: { next_step: 'Hervat de eerder onderbroken controle zorgvuldig.' },
    rationale: { recovery: true, deliveryRequired: true },
  }), {
    choice_label: 'Hervat de eerder onderbroken controle zorgvuldig.',
    reason_label: 'Eerder vastgelopen werk was nu weer veilig uitvoerbaar.',
  }, 'een concrete herstelreden blijft eerlijk boven een toekomstige afleverplicht');

  db.prepare('DELETE FROM aspirations').run();
  db.prepare(`UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'
    WHERE status='active'`).run();
  const goalWhy = 'De avondroutine gaf een concrete kans om het gezin meer rust te geven.';
  const createdGoal = manageGoal({
    operation: 'create', title: 'Maak de avondroutine rustiger',
    intent: 'Onderzoek één haalbare verbetering voor een rustige avondroutine.',
    why: goalWhy, beneficiary: 'Eva en het gezin', horizon: 'de komende week',
    successMetrics: ['één gecontroleerde verbetering zonder extra onrust'], source: 'self',
    nextReviewTs: new Date(Date.now() - 60_000).toISOString(),
  });
  assert.equal(createdGoal.ok, true);
  kvSet(KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0,
    daily_preempted_ms: 0,
  });

  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const scheduler = /\btick\(\)/u.test(String(callback));
    if (!scheduler) return originalSetTimeout(callback, delay, ...args);
    const handle = {
      callback, delay, args, cancelled: false, scheduler: true,
      ref() { return this; },
      unref() { return this; },
      hasRef() { return false; },
    };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => {
    if (handle?.scheduler === true) handle.cancelled = true;
    else originalClearTimeout(handle);
  };
  let providerStartedResolve = null;
  let heldProvider = null;
  let failProviderImmediately = false;
  const providerFailure = () => new Response(JSON.stringify({ error: {
    code: 'focused_display_stop', message: 'gerichte providerstop',
  } }), { status: 400, headers: { 'content-type': 'application/json' } });
  globalThis.fetch = async (url) => {
    if (String(url).startsWith(baseUrl())) return originalFetch(url);
    if (failProviderImmediately) return providerFailure();
    return new Promise((resolve) => {
      let released = false;
      heldProvider = {
        release() {
          if (released) return;
          released = true;
          failProviderImmediately = true;
          resolve(providerFailure());
        },
      };
      providerStartedResolve?.(heldProvider);
      providerStartedResolve = null;
    });
  };

  async function fireScheduler() {
    failProviderImmediately = false;
    const providerStarted = new Promise((resolve) => { providerStartedResolve = resolve; });
    const armed = [...timers].reverse()
      .find((item) => item.scheduler && !item.cancelled);
    assert.ok(armed, 'de echte scheduler moet zijn beurt bewapenen');
    armed.cancelled = true;
    const beforeRun = autonomyStatus().last_run_ts;
    armed.callback(...armed.args);
    return { beforeRun, provider: await providerStarted };
  }

  async function finishScheduler({ beforeRun, provider }) {
    provider.release();
    const deadline = Date.now() + 10_000;
    while ((autonomyStatus().last_run_ts === beforeRun || autonomyStatus().running)
      && Date.now() < deadline) {
      await new Promise((resolve) => originalSetTimeout(resolve, 10));
    }
    assert.notEqual(autonomyStatus().last_run_ts, beforeRun);
    assert.equal(autonomyStatus().running, false);
    await new Promise((resolve) => originalSetTimeout(resolve, 0));
  }

  try {
    startAutonomy();
    const goalRun = await fireScheduler();
    const goalStatus = await workStatus(originalFetch);
    assert.equal(goalStatus.autonomy.running, true);
    assert.deepEqual(goalStatus.autonomy.current_selection.attention, {
      kind: 'goal', id: createdGoal.goal.id,
    });
    assert.equal(goalStatus.autonomy.display.choice_label, createdGoal.goal.title);
    assert.equal(goalStatus.autonomy.display.reason_label, goalWhy);
    assert.ok(Date.parse(goalStatus.autonomy.display.selection_expires_ts)
      > Date.parse(goalStatus.autonomy.display.selection_ts));

    const goalElements = elements();
    const focusElement = { textContent: '' };
    const loadGoalWork = createLoadWork({
      fetchStatus: () => workStatus(originalFetch),
      setCurrentFocus: (value) => { focusElement.textContent = value; },
      renderStatus: (status) => renderAutonomyCard(goalElements, status, {
        nowFallback: 'De actuele keuze is niet betrouwbaar zichtbaar.',
        whyFallback: 'De actuele reden is niet betrouwbaar zichtbaar.',
      }),
    });
    await loadGoalWork();
    assert.equal(focusElement.textContent,
      `Nu gekozen: ${createdGoal.goal.title}. Waarom: ${goalWhy}`);
    assert.equal(goalElements.now.textContent, `Nu gekozen: ${createdGoal.goal.title}`);
    assert.equal(goalElements.why.textContent, `Waarom: ${goalWhy}`);

    const rawFocus = 'worker: exploration:17 met interne uitvoercontext';
    const neutralFocusElement = { textContent: '' };
    const loadUnattestedWork = createLoadWork({
      fetchStatus: async () => ({
        current_focus: rawFocus,
        autonomy: { running: true, display: {
          choice_label: null, reason_label: null,
          selection_ts: null, selection_expires_ts: null,
          result_label: null, result_ts: null, result_expires_ts: null,
        } },
      }),
      setCurrentFocus: (value) => { neutralFocusElement.textContent = value; },
      renderStatus: () => {},
    });
    await loadUnattestedWork();
    assert.equal(neutralFocusElement.textContent,
      'Eva werkt zelfstandig; de actuele keuze is niet betrouwbaar zichtbaar.');
    assert.doesNotMatch(neutralFocusElement.textContent, /worker|exploration:17/iu,
      'de echte loadWork-grens rendert bij running nooit raw current_focus');
    await finishScheduler(goalRun);

    const { workItem } = createDurableWork({
      source: 'user', owner: 'Jaap',
      title: 'Maak de ochtendspits rustiger',
      goal: 'Geef het gezin een vaste haalbare plek voor de schooltassen.',
      nextStep: 'Vergelijk twee haalbare plekken voor de vaste schooltassen.',
      definitionOfDone: 'Eén vaste plek is praktisch beoordeeld.', priority: 100,
    }, { originKind: 'user', originChannel: 'web', requester: 'Jaap' });
    kvSet(KEY, {
      next_run_ts: new Date(Date.now() - 1_000).toISOString(),
      pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0,
      daily_preempted_ms: 0,
    });
    const workRun = await fireScheduler();

    const liveStatus = await workStatus(originalFetch);
    assert.equal(liveStatus.autonomy.running, true);
    assert.equal(liveStatus.autonomy.current_selection.work_id, workItem.id);
    assert.equal(liveStatus.autonomy.display.choice_label,
      'Vergelijk twee haalbare plekken voor de vaste schooltassen.');
    assert.equal(liveStatus.autonomy.display.reason_label,
      'Deze openstaande afspraak voor het gezin was nu uitvoerbaar.');
    assert.match(liveStatus.autonomy.display.selection_ts,
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u);
    assert.ok(Date.parse(liveStatus.autonomy.display.selection_expires_ts)
      > Date.parse(liveStatus.autonomy.display.selection_ts));

    const liveElements = elements();
    renderAutonomyCard(liveElements, liveStatus, {
      nowFallback: 'De actuele keuze is niet betrouwbaar zichtbaar.',
      whyFallback: 'De actuele reden is niet betrouwbaar zichtbaar.',
      now: Date.parse(liveStatus.autonomy.display.selection_ts) + 1,
    });
    assert.equal(liveElements.now.textContent,
      'Nu gekozen: Vergelijk twee haalbare plekken voor de vaste schooltassen.');
    assert.equal(liveElements.why.textContent,
      'Waarom: Deze openstaande afspraak voor het gezin was nu uitvoerbaar.');

    for (const untrusted of [
      'exploration:17', 'worker', randomUUID(), '9381', 'c'.repeat(64),
      '<img src=x onerror=alert(1)>',
    ]) {
      const neutral = elements();
      renderAutonomyCard(neutral, {
        current_focus: untrusted,
        autonomy: {
          running: true,
          current_selection: { work_id: workItem.id },
          last_result: { productive: true, summary: untrusted },
          display: {
            choice_label: null, reason_label: null,
            selection_ts: null, selection_expires_ts: null,
            result_label: null, result_ts: null, result_expires_ts: null,
          },
        },
      }, {
        nowFallback: 'De actuele keuze is niet betrouwbaar zichtbaar.',
        whyFallback: 'De actuele reden is niet betrouwbaar zichtbaar.',
        now: Date.now(),
      });
      assert.doesNotMatch(Object.values(neutral).map((node) => node.textContent).join(' '),
        new RegExp(untrusted.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&'), 'iu'));
      assert.doesNotMatch(neutral.result.textContent, /Laatste resultaat/iu);
    }

    const staleDisplay = elements();
    const staleNow = Date.now();
    renderAutonomyCard(staleDisplay, {
      autonomy: {
        running: true,
        display: {
          choice_label: 'Vergelijk twee haalbare plekken voor de vaste schooltassen.',
          reason_label: 'Deze openstaande afspraak voor het gezin was nu uitvoerbaar.',
          selection_ts: new Date(staleNow - 45 * 60_000).toISOString(),
          selection_expires_ts: new Date(staleNow - 1).toISOString(),
          result_label: null, result_ts: null, result_expires_ts: null,
        },
      },
    }, {
      nowFallback: 'De actuele keuze is niet betrouwbaar zichtbaar.',
      whyFallback: 'De actuele reden is niet betrouwbaar zichtbaar.',
      now: staleNow,
    });
    assert.equal(staleDisplay.now.textContent,
      'De actuele keuze is niet betrouwbaar zichtbaar.');
    assert.equal(staleDisplay.why.textContent,
      'De actuele reden is niet betrouwbaar zichtbaar.');

    await finishScheduler(workRun);
    assert.equal(autonomyStatus().display.choice_label, null,
      'een afgeronde run houdt geen actuele keuzeclaim vast');
    assert.equal(autonomyStatus().display.reason_label, null);
    assert.equal(autonomyStatus().display.selection_ts, null);
  } finally {
    heldProvider?.release();
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});
