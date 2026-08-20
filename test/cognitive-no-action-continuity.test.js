import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-cognitive-no-action-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  beginCognitiveEpisode,
  closeCognitiveEpisode,
  recentFreeNoActionContextText,
  recordCognitiveAction,
  recordCognitiveOutcome,
} = await import('../src/mind/cognitive.js');
const { db } = await import('../src/db.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

let sequence = 0;
function appendAutonomousEpisode({
  authenticFree = true,
  syntheticTest = false,
  status = 'completed',
  resultClass = 'no_action',
  changed = false,
  madeProgress = false,
  contribution = null,
  capabilityId = 'home.context.read',
  operationId = 'read',
} = {}) {
  sequence += 1;
  const secret = `PRIVE_ENTITY_${sequence}`;
  const episode = beginCognitiveEpisode({
    correlationId: `free-no-action-${sequence}`,
    kind: 'autonomous_attention',
    modality: 'internal',
    source: 'autonomy_clock',
    sourceRef: `private-conversation-${sequence}`,
    subjectKey: 'eva',
    focusKind: 'self_chosen_or_continuing_work',
    goalKey: 'free_attention',
    whyNow: `private aanleiding ${secret}`,
    expectedChange: `private verwacht resultaat ${secret}`,
    payload: { authenticFree, syntheticTest, privateValue: secret },
  });
  recordCognitiveAction(episode.id, {
    tool: 'huis_status',
    capabilityId,
    operationId,
    subject: `sensor.${secret.toLowerCase()}`,
    expected: `private verwachting ${secret}`,
    ok: true,
    verified: true,
    changed,
    effect: 'read',
    evidence: `private bewijs ${secret}`,
  });
  recordCognitiveOutcome(episode.id, {
    resultClass: 'huis_status',
    ok: true,
    verified: true,
    changed,
    effectType: 'read',
    evidence: `private uitkomst ${secret}`,
    effect: `private publieke tekst ${secret}`,
  });
  closeCognitiveEpisode(episode.id, {
    status,
    resultClass,
    evidence: { contribution, tools: [{ output: secret }] },
    effect: { madeProgress, replied: false, privateValue: secret },
  });
  return { episode, secret };
}

function appendTypedBoundaryPair({
  resultClass = 'partial_execution', tool = 'huis_status', outcomeClass = tool,
  capabilityId = 'home.context.read', operationId = 'read', effect = 'read',
  ok = true, verified = true,
} = {}) {
  sequence += 1;
  const episode = beginCognitiveEpisode({
    correlationId: `typed-boundary-${sequence}`, kind: 'autonomous_attention',
    modality: 'internal', source: 'autonomy_clock', sourceRef: 'test', subjectKey: 'eva',
    focusKind: 'self_chosen_or_continuing_work', goalKey: 'free_attention',
    whyNow: 'test', expectedChange: 'none',
    payload: { authenticFree: true, syntheticTest: false },
  });
  recordCognitiveAction(episode.id, {
    tool, capabilityId, operationId, ok, verified, changed: false,
    executionStarted: true, effect, effectUncertain: false,
  });
  recordCognitiveOutcome(episode.id, {
    resultClass: outcomeClass, ok, verified, changed: false,
    executionStarted: true, effectType: effect, effectUncertain: false,
  });
  closeCognitiveEpisode(episode.id, {
    status: 'completed', resultClass, evidence: { contribution: null },
    effect: { replied: false, madeProgress: false, continuationId: null,
      readOnly: effect === 'read', hadChanged: false,
      hadEffectUncertainty: false, hadDurableEffect: false },
  });
  return episode;
}

function stripTypedReadProvenance(episodeId) {
  for (const row of db.prepare(`SELECT id,payload_json FROM cognitive_events
    WHERE episode_id=? AND kind IN ('action','outcome')`).all(episodeId)) {
    const payload = JSON.parse(row.payload_json);
    delete payload.executionStarted;
    delete payload.effect;
    delete payload.effectType;
    db.prepare('UPDATE cognitive_events SET payload_json=? WHERE id=?')
      .run(JSON.stringify(payload), row.id);
  }
}

test('geldige recente vrije no-action projecteert alleen de canonieke actiebinding', () => {
  const { secret } = appendAutonomousEpisode();
  const context = recentFreeNoActionContextText();

  assert.match(context, /home\.context\.read\/read/u);
  assert.match(context, /geen bijdrage of voortgang/u);
  assert.match(context, /geen opdracht of bevoegdheid/u);
  assert.equal(context.includes(secret), false);
  assert.equal(context.includes('sensor.'), false);
  assert.equal(context.includes('huis_status'), false);
  assert.ok(context.length <= 400);
});

test('een nieuwere autonome uitkomst blokkeert terugval op een oudere no-action', () => {
  appendAutonomousEpisode({
    resultClass: 'huis_status',
    changed: true,
    madeProgress: true,
    contribution: 'een werkelijk resultaat',
  });
  assert.equal(recentFreeNoActionContextText(), '');
});

test('changed, progress en contribution kwalificeren afzonderlijk nooit', async (t) => {
  const cases = [
    ['changed', { changed: true }],
    ['progress', { madeProgress: true }],
    ['contribution', { contribution: 'materieel resultaat' }],
  ];
  for (const [name, options] of cases) {
    await t.test(name, () => {
      appendAutonomousEpisode(options);
      assert.equal(recentFreeNoActionContextText(), '');
    });
  }
});

test('synthetische, verlopen en malforme actiebindingen blijven afwezig', async (t) => {
  await t.test('synthetic', () => {
    appendAutonomousEpisode({ authenticFree: false, syntheticTest: true });
    assert.equal(recentFreeNoActionContextText(), '');
  });

  await t.test('ttl', () => {
    const { episode } = appendAutonomousEpisode();
    db.prepare('UPDATE cognitive_episodes SET closed_ts = ? WHERE id = ?').run(
      new Date(Date.now() - 24 * 60 * 60_000 - 1).toISOString(),
      episode.id,
    );
    assert.equal(recentFreeNoActionContextText(), '');
  });

  await t.test('malformed', () => {
    appendAutonomousEpisode({ capabilityId: 'home.context.read\nprivate' });
    assert.equal(recentFreeNoActionContextText(), '');
  });
});

test('aaneengesloten vrije partial read onthoudt alleen compleet gestarte reads cumulatief', () => {
  appendAutonomousEpisode();
  sequence += 1;
  const episode = beginCognitiveEpisode({
    correlationId: `free-no-action-${sequence}`,
    kind: 'autonomous_attention', modality: 'internal', source: 'autonomy_clock',
    sourceRef: 'production-shaped-partial', subjectKey: 'eva',
    focusKind: 'self_chosen_or_continuing_work', goalKey: 'free_attention',
    whyNow: 'vrije inspectie', expectedChange: 'alleen actuele administratie',
    payload: { authenticFree: true, syntheticTest: false },
  });
  const read = ({ tool, capabilityId, operationId, partial = false }) => {
    recordCognitiveAction(episode.id, {
      tool, capabilityId, operationId, ok: true, verified: true, changed: false,
      executionStarted: true, effect: 'read', partial, needsFollowup: partial,
    });
    recordCognitiveOutcome(episode.id, {
      resultClass: tool, ok: true, verified: true, changed: false,
      executionStarted: true, effectType: 'read', partial, needsFollowup: partial,
    });
  };
  read({
    tool: 'zoek_gesprek', capabilityId: 'conversation.history.read',
    operationId: 'search', partial: true,
  });
  read({ tool: 'toon_werk', capabilityId: 'work.episode.read', operationId: 'list' });
  // De policy-afgewezen goal-read is zichtbaar in het turnbewijs, maar omdat
  // uitvoering niet startte bestaat hier bewust geen cognitief action/outcome-paar.
  closeCognitiveEpisode(episode.id, {
    status: 'completed', resultClass: 'partial_execution',
    evidence: { contribution: null, tools: [{
      name: 'doel_bijhouden', execution_started: false, failure_class: 'policy',
    }] },
    effect: {
      madeProgress: false, replied: false, continuationId: null,
      readOnly: true, hadChanged: false, hadEffectUncertainty: false,
      hadDurableEffect: false,
    },
  });

  const context = recentFreeNoActionContextText();
  assert.match(context, /home\.context\.read\/read/u);
  assert.match(context, /work\.episode\.read\/list/u);
  assert.doesNotMatch(context, /conversation\.history\.read\/search/u);
  assert.doesNotMatch(context, /goal\.portfolio\.read\/list/u);
  assert.ok(context.length <= 400);
});

test('anti-repeat cumuleert uniek en stopt fail-closed op onveilig bewijs', async (t) => {
  await t.test('A-B-A dedupe', () => {
    appendTypedBoundaryPair();
    appendTypedBoundaryPair({
      tool: 'toon_werk', outcomeClass: 'toon_werk',
      capabilityId: 'work.episode.read', operationId: 'list',
    });
    appendTypedBoundaryPair();
    const context = recentFreeNoActionContextText();
    assert.equal(context.match(/home\.context\.read\/read/gu)?.length, 1);
    assert.equal(context.match(/work\.episode\.read\/list/gu)?.length, 1);
  });

  const blockers = [
    ['persistent no-change', () => appendTypedBoundaryPair({
      effect: 'persistent', capabilityId: 'work.episode.manage', operationId: 'update',
    })],
    ['executed read failure', () => appendTypedBoundaryPair({ ok: false, verified: false })],
    ['tool/outcome mismatch', () => appendTypedBoundaryPair({ outcomeClass: 'toon_werk' })],
    ['legacy wrong operation', () => {
      const episode = appendTypedBoundaryPair({ operationId: 'delete', resultClass: 'no_action' });
      stripTypedReadProvenance(episode.id);
    }],
    ['legacy unknown capability', () => {
      const episode = appendTypedBoundaryPair({
        capabilityId: 'unknown.context.read', resultClass: 'no_action',
      });
      stripTypedReadProvenance(episode.id);
    }],
  ];
  for (const [name, appendBlocker] of blockers) {
    await t.test(name, () => {
      appendTypedBoundaryPair();
      appendBlocker();
      assert.equal(recentFreeNoActionContextText(), '');
    });
  }
});
