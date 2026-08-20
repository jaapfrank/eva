import './isolated-data-dir.js';

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import Database from 'better-sqlite3';

import {
  addAspiration, db, logContributionOutcome,
} from '../src/db.js';
import { config } from '../src/config.js';
import {
  completeIssuedExplorationContribution,
  noteExplorationAction, noteIssuedExplorationAction, startExploration,
} from '../src/mind/curiosity.js';
import {
  bindIssuedExplorationTerminalContribution,
  consumeIssuedExplorationTerminalContribution,
  understandTurn,
} from '../src/mind/understanding.js';
import {
  bindExplorationTerminalContributionExecution,
  capabilityCatalog,
  executeTool,
  planCapabilityAction,
} from '../src/mind/tools.js';
import { beginCognitiveEpisode } from '../src/mind/cognitive.js';
import {
  issueAutonomySelfAttentionReceipt,
  registerRuntimePrincipalValidator,
} from '../src/mind/runtimecontroller.js';
import {
  createIssuedExplorationAttention,
  issuedExplorationContinuationContext,
} from '../src/mind/selfattentioncontext.js';
import { familyTimelineEntry } from '../src/web/access.js';

const TURN_TEXT = 'Kijk naar de echte context en rond deze geverifieerde vondst precies één keer af.';
const SOURCE_SPANS = Object.freeze([{ start: 0, end: TURN_TEXT.length }]);
const attestedContexts = new WeakSet();
let sequence = 0;

registerRuntimePrincipalValidator('autonomy', ({ messageContext }) => (
  attestedContexts.has(messageContext) ? 'system:eva-autonomy' : null
));

function modelResponse(raw) {
  return { toolCalls: [{ name: 'commit_turn_understanding', arguments: JSON.stringify(raw) }] };
}

function terminalRaw(semanticInput) {
  return {
    dialogueAct: 'continue',
    goal: {
      statement: 'Rond één bestaande eigen onderzoekscyclus af.',
      outcome: 'De geverifieerde cyclus krijgt precies één eindresultaat.',
      beneficiary: 'household',
    },
    conditions: [],
    provenance: { sourceSpans: SOURCE_SPANS, contextHashes: [] },
    confidence: 0.99,
    actions: [{
      capabilityId: 'exploration.manage', operationId: 'afronden', polarity: 'request',
      semanticInput, sourceSpans: SOURCE_SPANS,
    }],
    requestedEffects: ['persistent'],
    deniedEffects: ['external', 'voice'],
    temporal: { mode: 'now', startSourceSpans: [] },
    durability: 'turn',
    delivery: { mode: 'silent', target: 'none' },
    proofNeeds: [], ambiguities: [],
    firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
  };
}

async function seededExploration({
  kind = 'development', beneficiary = 'het gezin', familyResult = undefined,
  result = 'De weekmeting bevestigt dat de vaste boodschappenlijst dubbele ritten voorkomt.',
  extra = {}, genuineReceipt = true, realEpisode = true,
} = {}) {
  sequence += 1;
  const aspirationId = addAspiration({
    text: `Gerichte gezinsbijdrage ${sequence}`,
    why: 'Deze test bindt zichtbaarheid aan één echte ontwikkelrichting.',
    kind, beneficiary, goalKey: `family-result-${sequence}`, cadenceHours: 24,
  });
  const started = startExploration({
    question: `Voorkomt de vaste boodschappenlijst ${sequence} dubbele ritten voor het gezin?`,
    whyNow: 'Er is één begrensde actuele meting beschikbaar.',
    hypothesis: 'De weekmeting kan bevestigen dat dezelfde boodschappenlijst dubbele ritten voorkomt.',
    expectedObservation: 'Een teruggelezen telling over boodschappenlijst en dubbele ritten.',
    stopRule: 'Stop na deze ene geverifieerde bronmeting.',
    maxActions: 2, aspirationId, source: 'self',
    topicKey: `family_result_${sequence}`,
    topicAnchors: [`event:family-result-${sequence}`, `entity:family_result_${sequence}`],
  });
  assert.equal(started.ok, true, JSON.stringify(started));
  const episodeId = realEpisode ? beginCognitiveEpisode({
    correlationId: `family-result-evidence-${sequence}`,
    kind: 'autonomous_attention', modality: 'internal', source: 'autonomy_clock',
    focusKind: 'exploration', focusRef: `attention:exploration:${started.exploration.id}`,
    goalKey: `exploration:${started.exploration.id}`,
    whyNow: started.exploration.why_now,
    expectedChange: started.exploration.expected_observation,
  }).id : 900_000 + sequence;
  let evidence;
  let observed;
  if (genuineReceipt) {
    const proofPath = path.join(config.workspaceDir,
      `gezinsmeting-${sequence}.txt`);
    fs.mkdirSync(path.dirname(proofPath), { recursive: true });
    fs.writeFileSync(proofPath,
      `Weekmeting ${sequence}: de vaste boodschappenlijst voorkwam twee dubbele ritten. `
      + 'Het gezin kan dezelfde lijst voor vertrek hergebruiken.\n');
    const readPlan = planCapabilityAction({
      requirementId: 'r1', capabilityId: 'workspace.file.read', operationId: 'read',
      authorization: 'allowed', semanticInput: { pad: proofPath },
    }, {
      mode: 'autonomy', fullAutonomy: true,
      understandingSha256: 'a'.repeat(64),
    });
    assert.ok(readPlan);
    const output = JSON.parse(await executeTool(readPlan.toolName, readPlan.args, {
      mode: 'autonomy', issuedPlan: readPlan,
      activeExplorationId: started.exploration.id,
      cognitiveEpisodeId: episodeId,
      userText: started.exploration.question,
    }));
    assert.equal(output.ok, true, JSON.stringify(output));
    assert.equal(output.verified, true);
    evidence = output.evidence.summary;
    const expected = issuedExplorationContinuationContext(
      createIssuedExplorationAttention(started.exploration),
    );
    assert.ok(expected);
    const settled = noteIssuedExplorationAction(started.exploration.id, expected, {
      tool: readPlan.toolName, ok: output.ok, verified: output.verified,
      evidence, episodeId,
    });
    assert.equal(settled.accepted, true, JSON.stringify(settled));
    observed = settled.exploration;
    const receipt = observed.evidence.at(-1)?.receipt;
    if (realEpisode) {
      assert.equal(receipt?.contract, 'ExplorationEvidenceReceipt/v1');
      assert.equal(receipt?.episodeId, episodeId);
      assert.equal(receipt?.understandingSha256, 'a'.repeat(64));
      assert.match(receipt?.receiptSha256 || '', /^[a-f0-9]{64}$/u);
      assert.match(receipt?.outputSha256 || '', /^[a-f0-9]{64}$/u);
    } else {
      assert.equal(receipt, undefined,
        'een los numeriek episode-id mag geen issued explorationreceipt worden');
    }
  } else {
    evidence = `Caller-geclaimde bronmeting ${sequence} zonder issued readreceipt.`;
    observed = noteExplorationAction(started.exploration.id, {
      tool: 'huis_status', ok: true, verified: true, evidence, episodeId,
    });
  }
  assert.equal(observed.action_count, 1);
  assert.equal(observed.status, 'running');
  return {
    aspirationId, exploration: observed, evidence,
    semanticInput: {
      id: observed.id, verwachte_revisie: observed.revision, resultaat: result,
      besluit: 'Gebruik deze vondst alleen zolang nieuw bewijs haar blijft bevestigen.',
      ...(familyResult === undefined ? {} : { gezinsresultaat: familyResult }),
      ...extra,
    },
  };
}

async function interpretTerminal(fixture) {
  const attention = createIssuedExplorationAttention(fixture.exploration);
  assert.ok(attention);
  const messageContext = Object.freeze({
    reason: `gerichte gezinsresultaatproef ${fixture.exploration.id}`,
    pendingSignals: Object.freeze([]), attention, workId: null, goalEpisodeId: null,
  });
  attestedContexts.add(messageContext);
  const receipt = issueAutonomySelfAttentionReceipt({
    mode: 'autonomy', messageContext, turnText: TURN_TEXT,
  });
  assert.ok(receipt);
  return understandTurn({ text: TURN_TEXT, channel: 'autonomy', origin: 'self' }, {
    capabilityCatalog: capabilityCatalog(), trustedSelfAttentionReceipt: receipt,
    maxAttempts: 1,
    complete: async () => modelResponse(terminalRaw(fixture.semanticInput)),
  });
}

async function executeTerminal(fixture) {
  fixture = await fixture;
  const interpreted = await interpretTerminal(fixture);
  assert.equal(interpreted.ok, true, JSON.stringify(interpreted));
  const action = interpreted.understanding.actions[0];
  const plan = planCapabilityAction(action, {
    mode: 'autonomy', fullAutonomy: true,
    understanding: interpreted.understanding,
    understandingSha256: interpreted.understanding.sha256,
  });
  assert.ok(plan);
  const claim = bindIssuedExplorationTerminalContribution(interpreted.understanding, action);
  assert.ok(claim);
  const execution = bindExplorationTerminalContributionExecution(claim, plan.args, () => true);
  assert.ok(execution);
  const output = JSON.parse(await executeTool(plan.toolName, plan.args, {
    mode: 'autonomy', issuedPlan: plan,
    explorationTerminalContribution: execution,
  }));
  assert.equal(output.ok, true, JSON.stringify(output));
  assert.equal(output.verified, true);
  return { fixture, interpreted, plan, output };
}

async function executeAndReplayTerminalContribution(fixture) {
  fixture = await fixture;
  const interpreted = await interpretTerminal(fixture);
  assert.equal(interpreted.ok, true, JSON.stringify(interpreted));
  const action = interpreted.understanding.actions[0];
  const plan = planCapabilityAction(action, {
    mode: 'autonomy', fullAutonomy: true,
    understanding: interpreted.understanding,
    understandingSha256: interpreted.understanding.sha256,
  });
  assert.ok(plan);
  const claim = bindIssuedExplorationTerminalContribution(interpreted.understanding, action);
  assert.ok(claim);
  const precommit = consumeIssuedExplorationTerminalContribution(claim, plan.args);
  assert.ok(precommit);
  const output = completeIssuedExplorationContribution(precommit, plan.args);
  assert.equal(output.ok, true, JSON.stringify(output));
  assert.equal(output.changed, true, 'de eerste volledige completion materialiseert echt');
  const persistedBeforeReplay = db.prepare(`SELECT content,meta,outcome_key FROM timeline
    WHERE kind='contribution' AND outcome_key=?`).get(output.contribution.outcome_key);
  assert.ok(persistedBeforeReplay);
  const replay = completeIssuedExplorationContribution(precommit, plan.args);
  assert.equal(replay.ok, true, JSON.stringify(replay));
  assert.equal(replay.changed, false, 'de tweede volledige completion is een exacte replay');
  assert.equal(replay.contribution.outcome_key, output.contribution.outcome_key);
  const persistedAfterReplay = db.prepare(`SELECT content,meta,outcome_key FROM timeline
    WHERE kind='contribution' AND outcome_key=?`).get(output.contribution.outcome_key);
  assert.deepEqual(persistedAfterReplay, persistedBeforeReplay,
    'full replay reconstrueert exact hetzelfde zichtbare resultaat en meta');
  return { fixture, interpreted, plan, precommit, output, replay };
}

function terminalOutcome(explorationId) {
  const rows = db.prepare(`SELECT * FROM timeline WHERE kind='contribution'
    AND json_extract(meta,'$.lifecycleContract')='ExplorationTerminalContribution/v1'
    AND json_extract(meta,'$.explorationId')=? ORDER BY id`).all(explorationId);
  assert.equal(rows.length, 1);
  return { ...rows[0], meta: JSON.parse(rows[0].meta) };
}

async function issuedReceiptForLineage(lineage) {
  sequence += 1;
  const aspirationId = addAspiration({
    text: `Receipt-lineage ${lineage} ${sequence}`,
    why: 'Deze gerichte proef bindt één echte read aan haar schedulerherkomst.',
    kind: 'development', beneficiary: 'het gezin',
    goalKey: `receipt-lineage-${lineage}-${sequence}`, cadenceHours: 24,
  });
  const wrongAspirationId = lineage === 'wrong_aspiration' ? addAspiration({
    text: `Andere receipt-lineage ${sequence}`,
    why: 'Deze aspiratie mag geen bewijs voor de eerste aspiratie uitgeven.',
    kind: 'development', beneficiary: 'het gezin',
    goalKey: `receipt-lineage-other-${sequence}`, cadenceHours: 24,
  }) : null;
  let sourceEpisodeId = null;
  if (lineage === 'wrong_source') {
    sourceEpisodeId = beginCognitiveEpisode({
      correlationId: `receipt-lineage-source-${sequence}`,
      kind: 'autonomous_attention', modality: 'internal', source: 'autonomy_clock',
      focusKind: 'self_chosen_or_continuing_work', focusRef: null,
      goalKey: `attention:aspiration:${aspirationId}`,
      whyNow: 'Oudere bron-episode', expectedChange: 'Geen authority voor de nieuwe episode.',
    }).id;
  }
  let episodeId = null;
  if (['aspiration', 'wrong_source', 'wrong_aspiration'].includes(lineage)) {
    episodeId = beginCognitiveEpisode({
      correlationId: `receipt-lineage-evidence-${sequence}`,
      kind: 'autonomous_attention', modality: 'internal', source: 'autonomy_clock',
      focusKind: 'self_chosen_or_continuing_work', focusRef: null,
      goalKey: `attention:aspiration:${wrongAspirationId || aspirationId}`,
      whyNow: 'Actuele aspiration-aandacht', expectedChange: 'Eén geverifieerde read.',
    }).id;
    if (lineage !== 'wrong_source') sourceEpisodeId = episodeId;
  }
  const started = startExploration({
    question: `Bevestigt lineage ${lineage} meting ${sequence} de boodschappenlijst?`,
    whyNow: 'Eén echte schedulergebonden bronread is nu beschikbaar.',
    hypothesis: 'De bronread bevestigt de boodschappenlijst voor het gezin.',
    expectedObservation: 'Een teruggelezen boodschappenlijst met concrete gezinswaarde.',
    stopRule: 'Stop na exact deze ene geverifieerde bronread.',
    maxActions: 2, aspirationId, sourceEpisodeId, source: 'self',
    topicKey: `receipt_lineage_${lineage}_${sequence}`,
    topicAnchors: [`event:receipt-${lineage}-${sequence}`, `entity:receipt_${sequence}`],
  });
  assert.equal(started.ok, true, JSON.stringify(started));
  if (!episodeId) {
    episodeId = beginCognitiveEpisode({
      correlationId: `receipt-lineage-evidence-${sequence}`,
      kind: 'autonomous_attention', modality: 'internal', source: 'autonomy_clock',
      focusKind: 'self_chosen_or_continuing_work',
      focusRef: lineage === 'legacy'
        ? `attention:exploration:${started.exploration.id}` : null,
      goalKey: lineage === 'wrong_goal'
        ? `attention:exploration:${started.exploration.id + 10_000}`
        : `attention:exploration:${started.exploration.id}`,
      whyNow: started.exploration.why_now,
      expectedChange: started.exploration.expected_observation,
    }).id;
  }
  const proofPath = path.join(config.workspaceDir,
    `receipt-lineage-${lineage}-${sequence}.txt`);
  fs.mkdirSync(path.dirname(proofPath), { recursive: true });
  fs.writeFileSync(proofPath, `Lineage ${lineage}: boodschappenlijst bevestigd.\n`);
  const readPlan = planCapabilityAction({
    requirementId: 'r1', capabilityId: 'workspace.file.read', operationId: 'read',
    authorization: 'allowed', semanticInput: { pad: proofPath },
  }, {
    mode: 'autonomy', fullAutonomy: true, understandingSha256: 'b'.repeat(64),
  });
  assert.ok(readPlan);
  const output = JSON.parse(await executeTool(readPlan.toolName, readPlan.args, {
    mode: 'autonomy', issuedPlan: readPlan,
    activeExplorationId: started.exploration.id, cognitiveEpisodeId: episodeId,
    userText: started.exploration.question,
  }));
  assert.equal(output.ok, true, JSON.stringify(output));
  const expected = issuedExplorationContinuationContext(
    createIssuedExplorationAttention(started.exploration),
  );
  const settled = noteIssuedExplorationAction(started.exploration.id, expected, {
    tool: readPlan.toolName, ok: output.ok, verified: output.verified,
    evidence: output.evidence.summary, episodeId,
  });
  assert.equal(settled.accepted, true, JSON.stringify(settled));
  return settled.exploration.evidence.at(-1)?.receipt;
}

test('issued explorationbewijs accepteert alleen de exacte schedulerlineage', async () => {
  for (const lineage of ['legacy', 'exploration', 'aspiration']) {
    const receipt = await issuedReceiptForLineage(lineage);
    assert.equal(receipt?.contract, 'ExplorationEvidenceReceipt/v1', lineage);
  }
  for (const lineage of ['wrong_goal', 'wrong_source', 'wrong_aspiration']) {
    assert.equal(await issuedReceiptForLineage(lineage), undefined, lineage);
  }
});

test('geverifieerd gezinsresultaat wordt één zichtbaar resultaat zonder push of werk', async () => {
  const surfaceBefore = {
    messages: db.prepare('SELECT count(*) AS count FROM messages').get().count,
    chatOut: db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='chat_out'").get().count,
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  };

  const negative = await executeTerminal(seededExploration({
    result: 'De actuele meting bevat geen nieuwe bruikbare gezinsvondst; deze cyclus stopt zonder melding.',
    familyResult: {
      titel: 'Toch zichtbaar ondanks een negatieve uitkomst',
      gezinsnut: 'Deze model-opt-in mag een negatieve vondst nooit publiek maken.',
    },
  }));
  const negativeOutcome = terminalOutcome(negative.fixture.exploration.id);
  assert.equal(negativeOutcome.meta.internalOnly, true);
  assert.equal(negativeOutcome.meta.familyVisible ?? false, false);
  assert.equal(Object.hasOwn(negativeOutcome.meta, 'familyResult'), false);
  assert.equal(familyTimelineEntry(negativeOutcome), false);

  const interest = await executeTerminal(seededExploration({
    kind: 'interest', beneficiary: 'het gezin',
    familyResult: {
      titel: 'Mijn eigen muzieksmaak werd iets scherper',
      gezinsnut: 'Dit is een interne interesse en geen concreet gezinsresultaat.',
    },
    result: 'Ik ontdekte welke klankkleur ik zelf prettiger vind tijdens een stille middag.',
  }));
  const interestOutcome = terminalOutcome(interest.fixture.exploration.id);
  assert.equal(interestOutcome.meta.internalOnly, true);
  assert.equal(interestOutcome.meta.familyVisible ?? false, false);
  assert.equal(Object.hasOwn(interestOutcome.meta, 'familyResult'), false);
  assert.equal(familyTimelineEntry(interestOutcome), false);

  const callerSeeded = await executeTerminal(seededExploration({
    genuineReceipt: true, realEpisode: false,
    familyResult: {
      titel: 'Callerflags zijn geen gezinsbewijs',
      gezinsnut: 'Alleen een echte issued en opgeslagen readreceipt mag dit zichtbaar maken.',
    },
  }));
  const callerSeededOutcome = terminalOutcome(callerSeeded.fixture.exploration.id);
  assert.equal(callerSeededOutcome.meta.internalOnly, true);
  assert.equal(callerSeededOutcome.meta.familyVisible ?? false, false);
  assert.equal(Object.hasOwn(callerSeededOutcome.meta, 'familyResult'), false);
  assert.equal(familyTimelineEntry(callerSeededOutcome), false);

  const mismatch = await executeTerminal(seededExploration({
    result: 'De weekmeting noemt de boodschappenlijst, maar beschrijft alleen de kleur van een oude archiefmap.',
    familyResult: {
      titel: 'Vaste boodschappenlijst voorkomt dubbele ritten',
      gezinsnut: 'Het gezin kan dezelfde lijst voortaan voor vertrek hergebruiken.',
    },
  }));
  const mismatchOutcome = terminalOutcome(mismatch.fixture.exploration.id);
  assert.equal(mismatchOutcome.meta.internalOnly, true,
    'twee losse gedeelde woorden bewijzen geen inhoudelijke receipt-topicrelatie');
  assert.equal(mismatchOutcome.meta.familyVisible ?? false, false);

  for (const extra of [{
    economisch_vervolg: {
      titel: 'Dubbel vervolg is verboden', uitkomst: 'Er ontstaat geen tweede resultaat.',
      definitie_klaar: 'De terminale route blijft exact enkelvoudig.',
      volgende_stap: 'Kies hoogstens één terminale resultaatsoort.',
    },
  }, {
    gedragsproef: {
      doelpersoon: 'Jaap',
      waargenomen_kloof: 'Mijn antwoord klinkt soms te glad voor een gewone gezinssituatie.',
      hypothese: 'Iets meer eigenheid maakt één antwoord natuurlijker.',
      motivatie: 'Ik wil deze kleine relationele stijlrichting zelf begrensd toetsen.',
      dimensie: 'independent_voice', richting: 'increase', intensiteit: 'subtle',
      contextscope: 'any_direct_reply',
    },
  }]) {
    const exclusive = await seededExploration({
      familyResult: {
        titel: 'Eén zichtbaar gezinsresultaat',
        gezinsnut: 'De vondst levert één concrete gezinsles op.',
      },
      extra,
    });
    const rejected = await interpretTerminal(exclusive);
    assert.equal(rejected.ok, false, JSON.stringify(rejected));
    assert.equal(rejected.failure.validation,
      'exploration_terminal_followups_are_exclusive');
    assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
      WHERE kind='contribution' AND json_extract(meta,'$.explorationId')=?`)
      .get(exclusive.exploration.id).count, 0);
  }

  const familyResult = {
    titel: 'Vaste boodschappenlijst voorkomt dubbele ritten',
    gezinsnut: 'Het gezin kan dezelfde lijst voortaan vóór vertrek hergebruiken.',
  };
  const visible = await executeAndReplayTerminalContribution(
    seededExploration({ familyResult }),
  );
  const visibleOutcome = terminalOutcome(visible.fixture.exploration.id);
  assert.equal(visibleOutcome.meta.internalOnly, false);
  assert.equal(visibleOutcome.meta.familyVisible, true);
  assert.equal(visibleOutcome.meta.familyResult.contract, 'ExplorationFamilyResult/v1');
  assert.equal(visibleOutcome.meta.familyResult.sourceOutcomeKey, visibleOutcome.outcome_key);
  assert.equal(visibleOutcome.meta.familyResult.title, familyResult.titel);
  assert.equal(visibleOutcome.meta.familyResult.familyBenefit, familyResult.gezinsnut);
  assert.match(visibleOutcome.meta.familyResult.evidenceSha256, /^[a-f0-9]{64}$/u);
  const issuedEvidenceReceipt = visible.fixture.exploration.evidence.at(-1).receipt;
  assert.equal(visibleOutcome.meta.familyResult.evidenceReceiptId,
    issuedEvidenceReceipt.receiptId);
  assert.equal(visibleOutcome.meta.familyResult.evidenceReceiptSha256,
    issuedEvidenceReceipt.receiptSha256);
  assert.equal(visibleOutcome.meta.familyResult.evidenceOutputSha256,
    issuedEvidenceReceipt.outputSha256);
  assert.equal(visibleOutcome.meta.familyResult.evidenceEpisodeId,
    issuedEvidenceReceipt.episodeId);
  assert.equal(familyTimelineEntry(visibleOutcome), true);
  assert.equal(visibleOutcome.content,
    `Eva ontdekte: ${visible.fixture.semanticInput.resultaat}`);

  const duplicate = await executeTerminal(seededExploration({ familyResult }));
  const duplicateOutcome = terminalOutcome(duplicate.fixture.exploration.id);
  assert.equal(duplicateOutcome.meta.internalOnly, true,
    'dezelfde genormaliseerde family-result/topicclaim blijft bij een tweede exploration intern');
  assert.equal(duplicateOutcome.meta.familyVisible ?? false, false);
  assert.equal(Object.hasOwn(duplicateOutcome.meta, 'familyResult'), false);
  assert.equal(familyTimelineEntry(duplicateOutcome), false);

  const replayOne = logContributionOutcome(
    visibleOutcome.outcome_key, visibleOutcome.content, visibleOutcome.meta,
  );
  const replayTwo = logContributionOutcome(
    visibleOutcome.outcome_key, visibleOutcome.content, visibleOutcome.meta,
  );
  assert.equal(replayOne.created, false);
  assert.equal(replayTwo.created, false);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='contribution' AND outcome_key=?`).get(visibleOutcome.outcome_key).count, 1);

  const reopened = new Database(path.join(process.env.EVA_DATA_DIR, 'eva.db'), {
    readonly: true, fileMustExist: true,
  });
  try {
    const durable = reopened.prepare(`SELECT content,meta,outcome_key FROM timeline
      WHERE kind='contribution' AND outcome_key=?`).all(visibleOutcome.outcome_key);
    assert.equal(durable.length, 1, 'een verse DB-handle ziet na restart exact één resultaat');
    assert.equal(JSON.parse(durable[0].meta).familyVisible, true);
  } finally {
    reopened.close();
  }

  assert.deepEqual({
    messages: db.prepare('SELECT count(*) AS count FROM messages').get().count,
    chatOut: db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='chat_out'").get().count,
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  }, surfaceBefore, 'geen intern onderzoek of zichtbaar resultaat mint push, werk of episode');
  const visibleFamilyRows = db.prepare(`SELECT * FROM timeline WHERE kind='contribution'
    AND json_extract(meta,'$.lifecycleContract')='ExplorationTerminalContribution/v1'`)
    .all().map((row) => ({ ...row, meta: JSON.parse(row.meta) }))
    .filter(familyTimelineEntry);
  assert.deepEqual(visibleFamilyRows.map((row) => row.outcome_key), [visibleOutcome.outcome_key]);
});
