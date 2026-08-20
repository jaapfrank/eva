import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-family-work-status-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'test-only';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
process.env.NODE_ENV = 'test';

const { addAspiration, db, logContributionOutcome } = await import('../src/db.js');
const { startExploration, updateExploration } = await import('../src/mind/curiosity.js');
const { bus } = await import('../src/bus.js');
const {
  createWorkItem, durableWorkStatus, updateWorkItem,
} = await import('../src/mind/work.js');
const { agentStatus, runAutonomousTurn } = await import('../src/mind/agent.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completedWork(title, visibleToFamily) {
  const work = createWorkItem({
    source: 'autonomy', owner: 'Eva', title,
    goal: `Rond ${title} aantoonbaar af.`,
    nextStep: 'Voer de concrete stap uit.',
    visibleToFamily,
  });
  return updateWorkItem(work.id, {
    status: 'done', proofAccepted: true,
    evidence: `${title} is gecontroleerd afgerond.`,
    result: `${title} leverde een concreet resultaat op.`,
    lastAction: 'controleer_resultaat',
  });
}

function pendingWork(title, visibleToFamily, status = 'open') {
  const work = createWorkItem({
    source: 'autonomy', owner: 'Eva', title,
    goal: `Werk ${title} zorgvuldig uit.`,
    nextStep: 'Voer de eerstvolgende stap uit.',
    visibleToFamily,
  });
  return status === 'blocked'
    ? updateWorkItem(work.id, { status: 'blocked', lastError: 'Wacht op concrete invoer.' })
    : work;
}

test('de interne status blijft volledig terwijl de familieprojectie intern werk uitsluit', () => {
  const hiddenWork = completedWork('Interne herstelcontrole', false);
  const visibleWork = completedWork('Zichtbare gezinsverbetering', true);
  const hiddenOpen = pendingWork('Interne open herstelstap', false);
  const visibleOpen = pendingWork('Zichtbare open gezinsstap', true);
  const hiddenBlocked = pendingWork('Interne geblokkeerde herstelstap', false, 'blocked');
  const visibleBlocked = pendingWork('Zichtbare geblokkeerde gezinsstap', true, 'blocked');

  const hiddenContribution = logContributionOutcome(
    '1'.repeat(64), 'Interne technische herstelstap.', {
      source: 'bijdrage', status: 'done', internalOnly: true,
      familyVisible: false, evidence: 'Alleen interne diagnose.',
      lastAction: 'interne_controle',
    },
  );
  const visibleContribution = logContributionOutcome(
    '2'.repeat(64), 'De plantencheck liet zien dat de varen vandaag water nodig heeft.', {
      source: 'bijdrage', status: 'done', internalOnly: false,
      familyVisible: true, evidence: 'De aarde van de varen is aantoonbaar droog.',
      lastAction: 'controleer_planten',
    },
  );
  const interestAspirationId = addAspiration({
    text: 'Ontwikkel een eigen smaak door kleine wetenschappelijke ervaringen te onderzoeken.',
    why: 'Een herkenbare huisgenoot heeft ook eigen interesses en concrete ontdekkingen.',
    kind: 'interest', touched: false,
  });
  const startedInterest = startExploration({
    question: 'Welke eenvoudige natuurkundeproef maakt een alledaags principe direct zichtbaar?',
    whyNow: 'Deze vrije aandacht kiest vandaag bewust een wetenschappelijk onderwerp.',
    hypothesis: 'Een ballonraket maakt actie en reactie zonder nieuwe spullen zichtbaar.',
    expectedObservation: 'De ballon beweegt vooruit terwijl lucht in de andere richting ontsnapt.',
    stopRule: 'Stop zodra een betrouwbare bron de opstelling en de les bevestigt.',
    topicKey: 'wetenschap-ballonraket',
    topicAnchors: ['natuurkundeproef', 'ballonraket'],
    maxActions: 2, aspirationId: interestAspirationId, source: 'self',
  });
  assert.equal(startedInterest.ok, true, JSON.stringify(startedInterest));
  const completedInterest = updateExploration(startedInterest.exploration.id, {
    status: 'completed', expectedRevision: startedInterest.exploration.revision,
    result: 'De ballonraket laat met een ballon, rietje, plakband en touw Newtons derde wet zien.',
    decision: 'De proef is concreet genoeg om als eigen ontdekking zichtbaar te maken.',
  });
  assert.equal(completedInterest.ok, true);

  const internal = durableWorkStatus(new Date());
  assert.ok(internal.doneToday.some((item) => item.id === hiddenWork.id),
    'de interne scheduler mag verborgen werk nog kunnen terugvinden');
  assert.ok(internal.doneToday.some((item) => item.id === visibleWork.id));
  assert.ok(internal.open.some((item) => item.id === hiddenOpen.id));
  assert.ok(internal.blocked.some((item) => item.id === hiddenBlocked.id));

  const family = durableWorkStatus(new Date(), { familyVisibleOnly: true });
  assert.deepEqual(family.doneToday.map((item) => item.id), [visibleWork.id]);
  assert.deepEqual(family.open.map((item) => item.id), [visibleOpen.id]);
  assert.deepEqual(family.blocked.map((item) => item.id), [visibleBlocked.id]);

  const internalStatus = agentStatus();
  assert.equal(internalStatus.open_commitments.some((item) => item.id === hiddenOpen.id), true,
    'de autonomiescheduler moet verborgen open werk via de standaardstatus blijven zien');
  assert.equal(internalStatus.blocked_items.some((item) => item.id === hiddenBlocked.id), true);
  assert.equal(internalStatus.today_real_steps.some((item) => item.id === hiddenWork.id), true);
  assert.equal(internalStatus.today_real_steps.some((item) => (
    item.id === `contribution:${hiddenContribution.row.id}`
  )), true, 'de interne status behoudt interne bijdragen');

  const familyStatus = agentStatus({ audience: 'family' });
  assert.equal(familyStatus.open_commitments.some((item) => item.id === hiddenOpen.id), false);
  assert.equal(familyStatus.open_commitments.some((item) => item.id === visibleOpen.id), true);
  assert.equal(familyStatus.blocked_items.some((item) => item.id === hiddenBlocked.id), false);
  assert.equal(familyStatus.blocked_items.some((item) => item.id === visibleBlocked.id), true);
  assert.equal(familyStatus.today_real_steps.some((item) => item.id === hiddenWork.id), false);
  assert.equal(familyStatus.today_real_steps.some((item) => item.id === visibleWork.id), true);
  assert.equal(familyStatus.today_real_steps.some((item) => (
    item.id === `contribution:${hiddenContribution.row.id}`
  )), false);
  assert.equal(familyStatus.today_real_steps.some((item) => (
    item.id === `contribution:${visibleContribution.row.id}`
  )), true);
  const visibleInterest = familyStatus.today_real_steps.find((item) => (
    item.id === `interest:${startedInterest.exploration.id}`
  ));
  assert.match(visibleInterest?.topic || '', /Eigen ontdekking.*natuurkundeproef/iu);
  assert.match(visibleInterest?.evidence || '', /ballonraket.*Newton/iu);
});

test('de familieprojectie lekt geen ruwe autonome runtimevelden', async () => {
  db.prepare("UPDATE eva_work_items SET status='cancelled'").run();
  const secret = 'verborgen-autonomie-doel-9d2c7e';
  const originalFetch = globalThis.fetch;
  let duringActivity = null;
  const captureActivity = () => {
    if (agentStatus().active_turns !== 1) return;
    duringActivity = {
      internal: agentStatus(),
      family: agentStatus({ audience: 'family' }),
    };
  };
  bus.on('activity', captureActivity);
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    const payload = JSON.parse(request.input[0].content[0].text);
    const span = { start: 0, end: payload.turn.text.length };
    const understanding = {
      dialogueAct: 'inform',
      goal: {
        statement: 'Laat deze projectietest zonder zelfstandige actie.',
        outcome: 'Geen onbewezen effect.',
        beneficiary: 'self',
      },
      conditions: [],
      provenance: { sourceSpans: [span], contextHashes: [] },
      confidence: 0.99,
      actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
    };
    const event = { type: 'response.completed', response: { output: [{
      type: 'function_call', call_id: 'family-projection-understanding',
      name: 'commit_turn_understanding', arguments: JSON.stringify(understanding),
    }] } };
    return new Response(`data: ${JSON.stringify(event)}\n\n`, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    });
  };

  try {
    await runAutonomousTurn({ reason: `Werk zelfstandig aan ${secret}.` });
    assert.ok(duringActivity, 'de activity-projectie moet tijdens de autonome beurt zijn gelezen');
    assert.match(duringActivity.internal.current_focus, new RegExp(secret));
    assert.equal(duringActivity.family.current_focus,
      'Eva werkt zelfstandig aan haar huidige aandachtspunt.');
    assert.doesNotMatch(JSON.stringify(duringActivity.family), new RegExp(secret));

    const internalAfter = agentStatus();
    assert.match(JSON.stringify(internalAfter.last_autonomy_turn), new RegExp(secret));
    const familyAfter = agentStatus({ audience: 'family' });
    assert.equal(familyAfter.last_autonomy_turn, null);
    assert.doesNotMatch(JSON.stringify(familyAfter.last_turn), new RegExp(secret));
    assert.doesNotMatch(JSON.stringify(familyAfter), new RegExp(secret));
  } finally {
    bus.off('activity', captureActivity);
    globalThis.fetch = originalFetch;
  }
});
