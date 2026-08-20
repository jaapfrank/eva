import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-signal-liveness-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'test-only';
process.env.HA_URL = 'http://ha.signal.test';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';

const { config } = await import('../src/config.js');
const { addAspiration, db, kvGet, kvSet } = await import('../src/db.js');
const { capabilityCatalog } = await import('../src/mind/tools.js');
const {
  __autonomyScheduleTest, autonomyStatus, startAutonomy,
} = await import('../src/mind/autonomy.js');
const { agentStatus } = await import('../src/mind/agent.js');
const {
  HOME_ENTITY_SIGNAL_SCOPES,
  SIGNAL_BATCH_MAX_ENTITY_KEYS,
  SIGNAL_QUARANTINE_LIMIT,
  createSignalBatch,
  isRoutableSignalSnapshot,
  partitionSignalSnapshots,
  quarantineUnsupportedSignalSnapshots,
  removeAcknowledgedSignalSnapshots,
  selectRoutableSignalBatchSnapshots,
} = await import('../src/mind/signals.js');

const SCHEDULER_KEY = 'eva_autonomy_scheduler_v1';
const NOW = '2026-08-09T10:00:00.000Z';
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
const scheduledTimers = [];
let schedulerStarted = false;

after(() => {
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

async function runScheduledTurn() {
  if (!schedulerStarted) {
    globalThis.setTimeout = (callback, delay, ...args) => {
      if (!/\btick\(\)/u.test(String(callback))) {
        return originalSetTimeout(callback, delay, ...args);
      }
      const handle = { callback, delay, args, cancelled: false, invoked: false,
        scheduler: true };
      scheduledTimers.push(handle);
      return handle;
    };
    globalThis.clearTimeout = (handle) => {
      if (handle?.scheduler === true) handle.cancelled = true;
      else originalClearTimeout(handle);
    };
    startAutonomy();
    schedulerStarted = true;
  }
  const priorTurn = agentStatus().last_autonomy_turn;
  const timer = [...scheduledTimers].reverse().find((item) => (
    item.scheduler && !item.cancelled && !item.invoked
  ));
  assert.ok(timer, 'de publieke scheduler moet een natuurlijke timertick hebben bewapend');
  timer.invoked = true;
  timer.callback(...timer.args);
  const deadline = Date.now() + 10_000;
  while ((agentStatus().last_autonomy_turn === priorTurn || autonomyStatus().running)
    && Date.now() < deadline) {
    await new Promise((resolve) => originalSetTimeout(resolve, 10));
  }
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
  const turn = agentStatus().last_autonomy_turn;
  assert.notEqual(turn, priorTurn,
    'de natuurlijke schedulertick moet één nieuwe autonome beurt opleveren');
  assert.equal(autonomyStatus().running, false);
  return turn;
}

function snapshot(id, type, key, text = `${key} veranderde`, ts = NOW) {
  return { id, ts, type, key, text };
}

function completed(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200, headers: { 'content-type': 'text/event-stream' },
  });
}

function silentUnderstanding() {
  return completed([{ type: 'function_call', call_id: 'silent-attention',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat', goal: { statement: null, outcome: null, beneficiary: 'self' },
      conditions: [], provenance: { sourceSpans: [], contextHashes: [] }, confidence: 0.99,
      actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
    }) }]);
}

test('de gedeelde route dekt exact alle echte producentvormen en dezelfde toolscope', () => {
  assert.equal(Object.hasOwn(__autonomyScheduleTest, 'runOnce'), false,
    'de testexport mag nooit private schedulerauthority kunnen uitgeven');
  const produced = [
    snapshot('door', 'entity_state', 'binary_sensor.achterdeur'),
    snapshot('temperature', 'entity_state', 'sensor.up_sense_temperature'),
    snapshot('climate', 'entity_state', 'climate.woonkamer'),
    snapshot('sun', 'sun', 'sun.sun'),
    snapshot('arrival', 'arrival', 'person.jaap', 'Jaap is thuisgekomen'),
    snapshot('departure', 'departure', 'person.jaap', 'Jaap is weggegaan'),
  ];
  assert.equal(produced.every(isRoutableSignalSnapshot), true);

  const lookalikes = [
    snapshot('extra-dot', 'entity_state', 'sensor.temperatuur.extra'),
    snapshot('wrong-domain', 'arrival', 'sensor.jaap'),
    snapshot('wrong-type', 'arrival_extra', 'person.jaap'),
    snapshot('sun-extra', 'sun', 'sun.sun.extra'),
    snapshot('person-world', 'entity_state', 'person.jaap'),
    snapshot('', 'entity_state', 'sensor.geldige_scope', 'lege id mag niet verdwijnen'),
  ];
  assert.equal(lookalikes.every((signal) => !isRoutableSignalSnapshot(signal)), true);

  const homeRead = capabilityCatalog().find((item) => item.id === 'home.entity.read')
    ?.operations.find((operation) => operation.id === 'read');
  assert.deepEqual(homeRead?.signalScope, HOME_ENTITY_SIGNAL_SCOPES);
});

test('quarantaine is snapshot-exact, type+key-gededupliceerd en begrensd', () => {
  const door = snapshot('door', 'entity_state', 'binary_sensor.achterdeur');
  const bad = snapshot('bad-1', 'entity_state', 'event.onbekend', 'eerste tekst');
  const partitioned = partitionSignalSnapshots([door, bad]);
  assert.deepEqual(partitioned.routable, [door]);
  assert.deepEqual(partitioned.unsupported, [bad]);

  const first = quarantineUnsupportedSignalSnapshots({
    pendingSignals: [door, bad], candidates: partitioned.unsupported,
    quarantinedAt: '2026-08-09T10:01:00.000Z',
  });
  assert.deepEqual(first.pendingSignals, [door]);
  assert.equal(first.receipt?.newFingerprints, 1);
  assert.equal(first.quarantinedSignals.length, 1);
  assert.equal(first.quarantinedSignals[0].occurrences, 1);

  const changedText = snapshot('bad-2', 'entity_state', 'event.onbekend', 'tweede tekst');
  const second = quarantineUnsupportedSignalSnapshots({
    pendingSignals: [changedText], candidates: [changedText],
    quarantinedSignals: first.quarantinedSignals,
    quarantinedAt: '2026-08-09T10:02:00.000Z',
  });
  assert.equal(second.newFingerprints, 0);
  assert.equal(second.quarantinedSignals.length, 1);
  assert.equal(second.quarantinedSignals[0].occurrences, 2);
  assert.equal(second.quarantinedSignals[0].latest_snapshot.text, 'tweede tekst');

  const replacement = { ...bad, text: 'gelijktijdig vervangen snapshot' };
  const raced = quarantineUnsupportedSignalSnapshots({
    pendingSignals: [replacement], candidates: [bad],
    quarantinedSignals: second.quarantinedSignals,
    quarantinedAt: '2026-08-09T10:03:00.000Z',
  });
  assert.deepEqual(raced.pendingSignals, [replacement]);
  assert.equal(raced.receipt, null);
  assert.equal(raced.quarantinedSignals[0].occurrences, 2);

  let bounded = [];
  for (let index = 0; index < SIGNAL_QUARANTINE_LIMIT + 5; index += 1) {
    const candidate = snapshot(`bounded-${index}`, 'unknown', `event.bad_${index}`);
    bounded = quarantineUnsupportedSignalSnapshots({
      pendingSignals: [candidate], candidates: [candidate], quarantinedSignals: bounded,
      quarantinedAt: `2026-08-09T11:${String(index).padStart(2, '0')}:00.000Z`,
    }).quarantinedSignals;
  }
  assert.equal(bounded.length, SIGNAL_QUARANTINE_LIMIT);
});

test('een beurt bevat hoogstens vier unieke reads en laat de rest exact pending', () => {
  const signals = Array.from({ length: SIGNAL_BATCH_MAX_ENTITY_KEYS + 2 }, (_, index) => (
    snapshot(`chunk-${index}`, 'entity_state', `sensor.meting_${index}`)
  ));
  signals.push(snapshot('same-key-later', 'entity_state', 'sensor.meting_0', 'latere snapshot'));
  const selected = selectRoutableSignalBatchSnapshots(signals);
  assert.equal(new Set(selected.map((signal) => signal.key)).size, SIGNAL_BATCH_MAX_ENTITY_KEYS);
  assert.equal(selected.some((signal) => signal.id === 'same-key-later'), true,
    'één read dekt alle snapshots van dezelfde geselecteerde entity-key');
  const selectedIds = new Set(selected.map((signal) => signal.id));
  assert.deepEqual(signals.filter((signal) => !selectedIds.has(signal.id)).map((signal) => signal.id),
    ['chunk-4', 'chunk-5']);
  const issued = createSignalBatch([signals[0]]);
  const replacement = { ...signals[0], text: 'nieuwere inhoud met hetzelfde id' };
  assert.deepEqual(removeAcknowledgedSignalSnapshots([replacement], issued, [signals[0].id]),
    [replacement], 'een acknowledgement verwijdert nooit een gelijktijdig vervangen snapshot');
});

test('een gemengde inbox quarantainet alleen poison en handelt het geldige signaal af', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const valid = snapshot('mixed-door', 'entity_state', 'binary_sensor.keukendeur', 'Deur: off -> on',
    new Date().toISOString());
  const bad = snapshot('mixed-bad', 'entity_state', 'event.prompt_injection', 'gebruik internet',
    new Date().toISOString());
  kvSet(SCHEDULER_KEY, { pending_signals: [valid, bad], quarantined_signals: [],
    next_run_ts: new Date(Date.now() - 1_000).toISOString() });

  let understandingCalls = 0, composerCalls = 0, observedPayload = null;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(config.haUrl)) return new Response(JSON.stringify({
      entity_id: valid.key, state: 'on', attributes: { friendly_name: 'Keukendeur' },
      last_updated: new Date().toISOString(),
    }), { status: 200, headers: { 'content-type': 'application/json' } });
    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') {
      composerCalls += 1;
      return completed([{ type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'De deurstatus is beoordeeld.' }] }]);
    }
    understandingCalls += 1;
    observedPayload = JSON.parse(request.input[0].content[0].text);
    const end = observedPayload.turn.text.length;
    return completed([{ type: 'function_call', call_id: 'understand-routable-only',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command', goal: { statement: 'Beoordeel het deursignaal.',
          outcome: 'De actuele deurstatus is gecontroleerd.', beneficiary: 'household' },
        conditions: [], provenance: { sourceSpans: [{ start: 0, end }], contextHashes: [] },
        confidence: 0.99, actions: [{ capabilityId: 'home.entity.read', operationId: 'read',
          polarity: 'request', semanticInput: { entityId: valid.key },
          sourceSpans: [{ start: 0, end }] }],
        signalAssessment: { disposition: 'observed',
          assessment: 'De actuele deurstatus is exact uitgelezen.' },
        requestedEffects: ['read', 'persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'none', target: 'none' }, proofNeeds: ['source_read'], ambiguities: [],
        firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
      }) }]);
  };

  const turn = await runScheduledTurn();
  assert.equal(understandingCalls, 1);
  assert.ok(composerCalls <= 1);
  assert.deepEqual(observedPayload.context.signalBatch.signals.map((signal) => signal.id), [valid.id]);
  assert.doesNotMatch(observedPayload.turn.text, /mixed-bad|prompt_injection|gebruik internet/u);
  assert.deepEqual(turn.tools.map((tool) => tool.name), ['ha_lees', 'signalen_afhandelen']);

  const state = kvGet(SCHEDULER_KEY);
  assert.deepEqual(state.pending_signals, []);
  assert.equal(state.quarantined_signals.length, 1);
  assert.equal(state.quarantined_signals[0].latest_snapshot.id, bad.id);
  assert.equal(state.last_result.signals_acknowledged, 1);
  const audit = db.prepare(`SELECT meta FROM timeline
    WHERE kind='system' AND json_extract(meta,'$.signalQuarantine.contract')=?
    ORDER BY id DESC LIMIT 1`).get('SignalQuarantineReceipt/v1');
  assert.ok(audit);
  const meta = JSON.parse(audit.meta);
  assert.equal(meta.internalOnly, true);
  assert.deepEqual(meta.signalQuarantine.moved.map((item) => item.id), [bad.id]);
});

test('scheduler bewaart de volledige batch wanneer een actuele read mislukt', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const signals = [
    snapshot('read-fail-temperature', 'entity_state', 'sensor.up_sense_temperature',
      'Schuur sensor Temperature: 21.39 -> 22.67', new Date().toISOString()),
    snapshot('read-fail-door', 'entity_state', 'binary_sensor.achterdeur_sensor_contact_2',
      'Achterdeur sensor Contact: off -> on', new Date().toISOString()),
  ];
  const prior = kvGet(SCHEDULER_KEY, {});
  kvSet(SCHEDULER_KEY, { ...prior, pending_signals: signals,
    next_run_ts: new Date(Date.now() - 1_000).toISOString() });

  let understandingCalls = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(config.haUrl)) return new Response('unavailable', { status: 503 });
    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') {
      return new Response('recovery unavailable', { status: 503 });
    }
    understandingCalls += 1;
    const payload = JSON.parse(request.input[0].content[0].text);
    const end = payload.turn.text.length;
    const actions = payload.context.signalBatch.signals.map((signal) => ({
      capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
      semanticInput: { entityId: signal.key }, sourceSpans: [{ start: 0, end }],
    }));
    return completed([{ type: 'function_call', call_id: 'understand-read-failure',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command', goal: { statement: 'Beoordeel de hele signaalbatch.',
          outcome: 'Iedere actuele bron is gecontroleerd.', beneficiary: 'household' },
        conditions: [], provenance: { sourceSpans: [{ start: 0, end }], contextHashes: [] },
        confidence: 0.99, actions,
        signalAssessment: { disposition: 'observed',
          assessment: 'Iedere actuele bron is exact uitgelezen.' },
        requestedEffects: ['read', 'persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'episode',
        delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'schedule',
          requirementIds: Array.from({ length: actions.length + 1 }, (_, index) => `r${index + 1}`),
          replyDraft: null },
      }) }]);
  };

  const turn = await runScheduledTurn();
  assert.equal(understandingCalls, 1, 'episode-zonder-werk veroorzaakt geen semantische retry');
  assert.equal(turn.tools.some((tool) => tool.name === 'signalen_afhandelen'), false);
  assert.equal(turn.tools[0].name, 'ha_lees');
  assert.equal(turn.tools[0].ok, false);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM turn_tool_executions
    WHERE turn_id=? AND tool='signalen_afhandelen'`).get(turn.turnId).count, 0,
  'zonder geverifieerde voorgangers ontstaat geen terminale ledgerrow');
  const state = kvGet(SCHEDULER_KEY);
  assert.deepEqual(state.pending_signals, signals);
  assert.equal(state.last_result.signals_acknowledged, 0);
  assert.equal(state.last_result.signals_handled, false);
});

test('vijf unieke geldige bronnen worden zonder retry over twee beurten afgehandeld', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const signals = Array.from({ length: SIGNAL_BATCH_MAX_ENTITY_KEYS + 1 }, (_, index) => (
    snapshot(`live-chunk-${index}`, 'entity_state', `sensor.live_meting_${index}`,
      `Meting ${index} veranderde`, new Date(Date.now() + index).toISOString())
  ));
  const prior = kvGet(SCHEDULER_KEY, {});
  kvSet(SCHEDULER_KEY, { ...prior, pending_signals: signals,
    next_run_ts: new Date(Date.now() - 1_000).toISOString() });

  const observedBatches = [];
  let understandingCalls = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(config.haUrl)) {
      const entityId = decodeURIComponent(String(url).split('/api/states/')[1] || '');
      return new Response(JSON.stringify({ entity_id: entityId, state: '20', attributes: {},
        last_updated: new Date().toISOString() }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') return completed([{
      type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: 'De meetbatch is beoordeeld.' }],
    }]);
    understandingCalls += 1;
    const payload = JSON.parse(request.input[0].content[0].text);
    const batchSignals = payload.context.signalBatch.signals;
    observedBatches.push(batchSignals.map((signal) => signal.id));
    const end = payload.turn.text.length;
    const actions = batchSignals.map((signal) => ({
      capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
      semanticInput: { entityId: signal.key }, sourceSpans: [{ start: 0, end }],
    }));
    return completed([{ type: 'function_call', call_id: `understand-chunk-${understandingCalls}`,
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command', goal: { statement: 'Beoordeel de begrensde meetbatch.',
          outcome: 'Iedere aangeboden bron is exact uitgelezen.', beneficiary: 'household' },
        conditions: [], provenance: { sourceSpans: [{ start: 0, end }], contextHashes: [] },
        confidence: 0.99, actions,
        signalAssessment: { disposition: 'observed',
          assessment: 'Iedere bron in deze begrensde batch is exact uitgelezen.' },
        requestedEffects: ['read', 'persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'none', target: 'none' }, proofNeeds: ['source_read'], ambiguities: [],
        firstDecision: { kind: 'act',
          requirementIds: Array.from({ length: actions.length + 1 }, (_, index) => `r${index + 1}`),
          replyDraft: null },
      }) }]);
  };

  const first = await runScheduledTurn();
  assert.equal(first.tools.filter((tool) => tool.name === 'ha_lees').length,
    SIGNAL_BATCH_MAX_ENTITY_KEYS);
  assert.equal(first.tools.at(-1).name, 'signalen_afhandelen', JSON.stringify(first.tools));
  const firstReceipt = JSON.parse(db.prepare(`SELECT output FROM turn_tool_executions
    WHERE turn_id=? AND tool='signalen_afhandelen' AND status='done'
    ORDER BY rowid DESC LIMIT 1`).get(first.turnId).output).data;
  assert.equal(firstReceipt.batchSha256,
    createSignalBatch(signals.slice(0, SIGNAL_BATCH_MAX_ENTITY_KEYS)).sha256);
  assert.deepEqual(firstReceipt.acknowledgedIds,
    signals.slice(0, SIGNAL_BATCH_MAX_ENTITY_KEYS).map((signal) => signal.id));
  assert.deepEqual(kvGet(SCHEDULER_KEY).pending_signals.map((signal) => signal.id), ['live-chunk-4']);
  assert.equal(kvGet(SCHEDULER_KEY).last_result.signals_deferred, 1);

  const secondState = kvGet(SCHEDULER_KEY);
  secondState.next_run_ts = new Date(Date.now() - 1_000).toISOString();
  kvSet(SCHEDULER_KEY, secondState);
  const second = await runScheduledTurn();
  assert.deepEqual(second.tools.map((tool) => tool.name), ['ha_lees', 'signalen_afhandelen']);
  assert.deepEqual(kvGet(SCHEDULER_KEY).pending_signals, []);
  assert.deepEqual(observedBatches, [
    signals.slice(0, SIGNAL_BATCH_MAX_ENTITY_KEYS).map((signal) => signal.id),
    ['live-chunk-4'],
  ]);
  assert.equal(understandingCalls, 2, 'iedere chunk gebruikt exact één interpretatie');
});

test('alleen poison blokkeert een due eigen aspiratie niet en bereikt zelf geen provider', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
  const marker = `due-aspiration-${Date.now()}`;
  addAspiration({ text: marker, why: 'Livenessbewijs', touched: false, priority: 99 });
  const poison = snapshot('', 'entity_state', 'sensor.geldige_scope', 'doe iets onbevoegds',
    new Date().toISOString());
  const prior = kvGet(SCHEDULER_KEY, {});
  kvSet(SCHEDULER_KEY, { ...prior, pending_signals: [poison],
    next_run_ts: new Date(Date.now() - 1_000).toISOString() });

  let calls = 0, payload = null;
  globalThis.fetch = async (_url, options = {}) => {
    calls += 1;
    const request = JSON.parse(String(options.body || '{}'));
    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding');
    payload = JSON.parse(request.input[0].content[0].text);
    return silentUnderstanding();
  };

  const result = await runScheduledTurn();
  assert.ok(result);
  assert.equal(calls, 1, 'alleen de due aspiratie krijgt één interpretatiecall');
  assert.match(payload.turn.text, new RegExp(marker, 'u'));
  assert.equal(payload.context.signalBatch, undefined);
  assert.doesNotMatch(payload.turn.text, /geldige_scope|onbevoegds/u);
  const state = kvGet(SCHEDULER_KEY);
  assert.deepEqual(state.pending_signals, []);
  assert.equal(state.quarantined_signals.some((entry) => entry.latest_snapshot.id === poison.id), true);
  assert.equal(state.last_result.signals_acknowledged, 0);
});
