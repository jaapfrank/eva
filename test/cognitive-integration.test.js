import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-cognitive-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  beginCognitiveEpisode,
  closeCognitiveEpisode,
  cognitiveContextText,
  cognitiveState,
  recordCognitiveAction,
  recordCognitiveOutcome,
  startCognitiveCore,
} = await import('../src/mind/cognitive.js');
const { bus } = await import('../src/bus.js');
const { db } = await import('../src/db.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function eventsForEpisode(episodeId) {
  return db.prepare(`
    SELECT * FROM cognitive_events WHERE episode_id = ? ORDER BY id
  `).all(Number(episodeId));
}

test('stimulus, actie en uitkomst blijven in een gedeelde episode tot die sluit', () => {
  const correlationId = 'integration-stimulus-action-outcome';
  const episode = beginCognitiveEpisode({
    correlationId,
    kind: 'interaction',
    modality: 'linguistic',
    source: 'web',
    sourceRef: 'web:jaap',
    subjectKey: 'Jaap',
    focusKind: 'conversation',
    focusRef: 'web:jaap',
    goalKey: 'test:lampstatus',
    whyNow: 'Jaap vraagt om de actuele lampstatus',
    expectedChange: 'de toestand lezen en gegrond teruggeven',
    payload: { text: 'Staat de lamp aan?' },
  });

  const action = recordCognitiveAction(episode.id, {
    tool: 'ha_lees',
    subject: 'light.woonkamer',
    expected: 'de actuele toestand lezen',
    ok: true,
    verified: true,
    changed: false,
    evidence: 'light.woonkamer is on',
  });
  const outcome = recordCognitiveOutcome(episode.id, {
    resultClass: 'ha_lees',
    ok: true,
    verified: true,
    changed: false,
    evidence: 'light.woonkamer is on',
    effect: 'de actuele toestand is bekend',
  });
  assert.equal(closeCognitiveEpisode(episode.id, {
    status: 'completed',
    resultClass: 'ha_lees',
    evidence: { entity: 'light.woonkamer', state: 'on' },
    effect: { replied: true },
  }), true);

  const events = eventsForEpisode(episode.id);
  assert.deepEqual(events.map((event) => event.kind), [
    'observation',
    'action',
    'outcome',
  ]);
  assert.ok(events.every((event) => event.correlation_id === correlationId));
  assert.equal(events[0].modality, 'linguistic');
  assert.equal(events[1].modality, 'motor');
  assert.equal(JSON.parse(events[1].payload_json).tool, 'ha_lees');
  assert.equal(JSON.parse(events[2].payload_json).resultClass, 'ha_lees');

  const stored = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?')
    .get(episode.id);
  assert.equal(stored.status, 'completed');
  assert.equal(stored.trigger_event_id, events[0].id);
  assert.ok(stored.closed_ts);

  const state = cognitiveState();
  assert.equal(state.phase, 'idle');
  assert.equal(state.activeEpisodeId, null);
  assert.equal(state.lastEventId, outcome.id);
  assert.equal(state.lastOutcomeId, outcome.id);
  assert.equal(state.continuity.lastCompleted.episodeId, episode.id);
  assert.equal(action.episodeId, episode.id);
  assert.match(cognitiveContextText(), /zelf de gevraagde Home Assistant-entiteiten gelezen/i);
  assert.match(cognitiveContextText(), /light\.woonkamer is on/i);
});

test('preemptie bewaart de focus en dezelfde episode kan daarna hervatten', () => {
  const work = beginCognitiveEpisode({
    correlationId: 'integration-resumable-work',
    kind: 'autonomous_attention',
    modality: 'internal',
    source: 'autonomy_clock',
    focusKind: 'workspace_project',
    focusRef: 'work:42',
    goalKey: 'work:42',
    whyNow: 'de gerichte regressietest moet nog worden uitgevoerd',
    expectedChange: 'werk 42 aantoonbaar afronden',
  });
  const interruption = beginCognitiveEpisode({
    correlationId: 'integration-user-preemption',
    kind: 'interaction',
    modality: 'linguistic',
    source: 'telegram',
    sourceRef: 'telegram:jaap',
    subjectKey: 'Jaap',
    focusKind: 'conversation',
    focusRef: 'telegram:jaap',
    goalKey: 'chat:answer',
    whyNow: 'Jaap richt zich nu tot Eva',
    expectedChange: 'Jaap direct antwoorden',
  });

  const interruptedRow = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?')
    .get(work.id);
  assert.equal(interruptedRow.status, 'interrupted');
  assert.ok(interruptedRow.closed_ts);
  assert.deepEqual(
    cognitiveState().continuity.interrupted.find((item) => item.episodeId === work.id),
    {
      episodeId: work.id,
      focusKind: 'workspace_project',
      focusRef: 'work:42',
      goalKey: 'work:42',
      interruptedAt: interruptedRow.closed_ts,
    },
  );

  closeCognitiveEpisode(interruption.id, {
    status: 'completed',
    resultClass: 'reply',
  });
  const resumed = beginCognitiveEpisode({
    correlationId: 'integration-resumable-work',
  });
  assert.equal(resumed.resumed, true);
  assert.equal(resumed.id, work.id);
  assert.deepEqual(cognitiveState().focus, {
    kind: 'workspace_project',
    ref: 'work:42',
    goalKey: 'work:42',
    reason: 'de gerichte regressietest moet nog worden uitgevoerd',
  });

  recordCognitiveAction(work.id, {
    tool: 'voer_uit',
    subject: 'regressietest.js',
    ok: true,
    verified: true,
    changed: true,
  });
  assert.equal(cognitiveState().phase, 'acting');
  closeCognitiveEpisode(work.id, {
    status: 'completed',
    resultClass: 'voer_uit',
  });
  assert.equal(
    cognitiveState().continuity.interrupted.some((item) => item.episodeId === work.id),
    false,
  );
});

test('procesherstart maakt een actieve episode duurzaam hervatbaar', () => {
  const recoveryDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-cognitive-recovery-'));
  const cognitiveUrl = pathToFileURL(path.join(process.cwd(), 'src/mind/cognitive.js')).href;
  const dbUrl = pathToFileURL(path.join(process.cwd(), 'src/db.js')).href;
  const childEnv = { ...process.env, EVA_DATA_DIR: recoveryDir };
  const run = (source) => spawnSync(
    process.execPath,
    ['--input-type=module', '--eval', source],
    {
      cwd: process.cwd(),
      env: childEnv,
      encoding: 'utf8',
      timeout: 20_000,
    },
  );

  try {
    const first = run(`
      const cognitive = await import(${JSON.stringify(cognitiveUrl)});
      const { db } = await import(${JSON.stringify(dbUrl)});
      const episode = cognitive.beginCognitiveEpisode({
        correlationId: 'integration-process-restart',
        kind: 'autonomous_attention',
        focusKind: 'workspace_project',
        focusRef: 'work:restart',
        goalKey: 'work:restart',
        whyNow: 'een procesrestart mag de focus niet wissen'
      });
      console.log(JSON.stringify({ episodeId: episode.id, state: cognitive.cognitiveState() }));
      db.close();
    `);
    assert.equal(first.status, 0, first.stderr);
    const opened = JSON.parse(first.stdout.trim().split('\n').at(-1));
    assert.equal(opened.state.activeEpisodeId, opened.episodeId);

    const second = run(`
      const cognitive = await import(${JSON.stringify(cognitiveUrl)});
      const { db } = await import(${JSON.stringify(dbUrl)});
      cognitive.startCognitiveCore();
      const episode = db.prepare('SELECT * FROM cognitive_episodes WHERE correlation_id = ?')
        .get('integration-process-restart');
      console.log(JSON.stringify({ state: cognitive.cognitiveState(), episode }));
      db.close();
    `);
    assert.equal(second.status, 0, second.stderr);
    const recovered = JSON.parse(second.stdout.trim().split('\n').at(-1));
    assert.equal(recovered.episode.status, 'interrupted');
    assert.equal(recovered.episode.result_class, 'process_restart');
    assert.equal(recovered.state.activeEpisodeId, null);
    assert.equal(
      recovered.state.continuity.interrupted.some((item) => (
        item.episodeId === opened.episodeId
        && item.focusKind === 'workspace_project'
        && item.focusRef === 'work:restart'
        && item.goalKey === 'work:restart'
      )),
      true,
    );
  } finally {
    fs.rmSync(recoveryDir, { recursive: true, force: true });
  }
});

test('house en world_signal landen in hetzelfde duurzame eventledger', () => {
  assert.equal(startCognitiveCore(), true);
  const correlationId = 'integration-shared-sensor-event';
  bus.emit('house', {
    correlationId,
    type: 'door',
    entityId: 'binary_sensor.achterdeur',
    text: 'De achterdeur is opengegaan',
    confidence: 0.92,
  });
  bus.emit('world_signal', {
    correlationId,
    type: 'state_changed',
    domain: 'binary_sensor',
    entityId: 'binary_sensor.achterdeur',
    attributes: { deviceClass: 'door' },
    oldState: 'off',
    newState: 'on',
    text: 'binary_sensor.achterdeur veranderde van off naar on',
  });

  const events = db.prepare(`
    SELECT * FROM cognitive_events
    WHERE correlation_id = ? ORDER BY id
  `).all(correlationId);
  assert.equal(events.length, 2);
  assert.ok(events.every((event) => event.source === 'home_assistant'));
  assert.ok(events.every((event) => event.modality === 'environmental'));
  assert.deepEqual(events.map((event) => event.source_ref), [
    'binary_sensor.achterdeur',
    'binary_sensor.achterdeur',
  ]);
  assert.equal(JSON.parse(events[0].payload_json).type, 'door');
  assert.deepEqual(JSON.parse(events[1].payload_json), {
    type: 'state_changed',
    oldState: 'off',
    newState: 'on',
    text: 'binary_sensor.achterdeur veranderde van off naar on',
  });
  assert.equal(cognitiveState().lastEventId, events[1].id);
  assert.ok(events.every((event) => event.episode_id === null));

  const persisted = db.prepare(`
    SELECT count(*) AS count FROM cognitive_events
    WHERE correlation_id = ? AND status = 'integrated'
  `).get(correlationId);
  assert.equal(persisted.count, 2);
});

test('snelle lage telemetrie wordt per bron samengevat en overschrijft de aandacht niet', () => {
  const before = cognitiveState().lastEventId;
  bus.emit('world_signal', {
    correlationId: 'power-1',
    type: 'entity_state',
    domain: 'sensor',
    entityId: 'sensor.test_active_power',
    oldState: '100',
    newState: '101',
    text: 'vermogen 100 naar 101',
  });
  bus.emit('world_signal', {
    correlationId: 'power-2',
    type: 'entity_state',
    domain: 'sensor',
    entityId: 'sensor.test_active_power',
    oldState: '101',
    newState: '102',
    text: 'vermogen 101 naar 102',
  });
  const rows = db.prepare(`
    SELECT * FROM cognitive_events WHERE source_ref = 'sensor.test_active_power'
  `).all();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].episode_id, null);
  assert.equal(rows[0].correlation_id, 'power-2');
  assert.equal(JSON.parse(rows[0].payload_json).newState, '102');
  assert.equal(cognitiveState().lastEventId, before);
});
