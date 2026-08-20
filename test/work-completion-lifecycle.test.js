import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-work-completion-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'test-only';

const { config } = await import('../src/config.js');
const { bus } = await import('../src/bus.js');
const { db } = await import('../src/db.js');
const { executeTool } = await import('../src/mind/tools.js');
const {
  createDurableWork,
  episodeHasDelivery,
  episodeHasFinalDelivery,
  goalEpisodeById,
  recordEpisodeDelivery,
  recoverDeliveredEpisodeReceipts,
  sealFinalDeliveryReply,
  transitionGoalEpisode,
} = await import('../src/mind/portfolio.js');
const { handleMessageDetailed, recordDeliveredReply } = await import('../src/mind/agent.js');
const { createPreparedUnderstandingTurn } = await import('../src/mind/understanding.js');
const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function modelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { headers: { 'content-type': 'text/event-stream' } });
}

function textResponse(text) {
  return modelResponse([{
    type: 'message', role: 'assistant', content: [{ type: 'output_text', text }],
  }]);
}

test('verborgen terminale toestand en onbewezen afronden muteren werk niet', async () => {
  const before = db.prepare('SELECT COUNT(*) AS count FROM eva_work_items').get().count;
  const forbiddenStart = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'start', titel: 'Mag niet ontstaan', doel: 'Dit mag geen nieuwe klus maken.',
    toestand_json: JSON.stringify({ status: 'completed' }),
  }, { mode: 'chat', sender: 'Jaap' }));
  assert.equal(forbiddenStart.ok, false);
  assert.match(JSON.stringify(forbiddenStart), /toestand_json.*afronden|afsluiten/iu);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_work_items').get().count, before);

  const { workItem } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Drie verkoopteksten',
    goal: 'Drie verkoopteksten zijn gecontroleerd teruggelezen.',
    nextStep: 'Lees het gecontroleerde bestand terug.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: 'web:work-state-boundary',
    originMessageId: 'work-state-1', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const forgedUpdate = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'update', id: workItem.id, toestand_json: JSON.stringify({ status: 'done' }),
  }, { mode: 'chat', sender: 'Jaap' }));
  assert.equal(forgedUpdate.ok, false);
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(workItem.id).status, 'open');

  const noProof = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id, resultaat: 'Dit is nog niet bewezen.',
  }, { mode: 'chat', sender: 'Jaap' }));
  assert.equal(noProof.ok, false);
  assert.match(JSON.stringify(noProof), /dezelfde beurt/iu);

  const messageOnly = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id, resultaat: 'Een bericht is geen bewijs.',
  }, {
    mode: 'chat', sender: 'Jaap',
    priorToolResults: [{
      name: 'stuur_bericht', ok: true, verified: true,
      output: JSON.stringify({ contract: 'ActionResult/v1', ok: true, verified: true,
        evidence: { summary: 'Drie verkoopteksten zijn gecontroleerd teruggelezen.' } }),
    }],
  }));
  assert.equal(messageOnly.ok, false);
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(workItem.id).status, 'open');
});

test('een muterend workspacewerk sluit niet op alleen een leesreceipt met woordoverlap', async () => {
  fs.mkdirSync(config.workspaceDir, { recursive: true });
  const artifactPath = path.join(config.workspaceDir, 'inkomstenkans-start.md');
  const staleArtifact = [
    '# Inkomstenkans start',
    '',
    'TODO: corrigeer de onbewezen verkoopclaim en voeg prijsbewijs toe.',
  ].join('\n');
  fs.writeFileSync(artifactPath, staleArtifact);

  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Corrigeer inkomstenartefact',
    goal: `Wijzig ${artifactPath}: corrigeer de onbewezen verkoopclaim en voeg prijsbewijs toe.`,
    nextStep: 'Schrijf de correctie naar het workspacebestand en lees exact die versie terug.',
    definitionOfDone: 'De gecorrigeerde verkoopclaim en het prijsbewijs staan aantoonbaar in de nieuwe bestandsversie.',
  }, {
    originKind: 'user', originChannel: 'web',
    originConversationId: 'web:mutating-work-read-only-proof',
    originMessageId: 'mutating-work-1', requester: 'Jaap', deliveryPolicy: 'none',
  });

  const readArgs = { pad: artifactPath };
  const readOutput = await executeTool('lees_bestand', readArgs, {
    mode: 'chat', sender: 'Jaap', userText: workItem.goal,
  });
  const readAction = JSON.parse(readOutput);
  const readReceipt = {
    name: 'lees_bestand', args: readArgs, output: readOutput,
    ok: readAction.ok, verified: readAction.verified, changed: readAction.changed,
    needs_followup: readAction.needs_followup, partial: readAction.partial,
  };
  assert.equal(readReceipt.ok, true);
  assert.equal(readReceipt.verified, true);
  assert.equal(readReceipt.changed, false);

  const finished = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id,
    resultaat: 'Het inkomstenartefact is gecorrigeerd en bevat nu prijsbewijs.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web',
    conversationId: 'web:mutating-work-read-only-proof',
    originMessageId: 'mutating-work-1', turnId: 'mutating-work-turn-1',
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
    priorToolResults: [readReceipt],
  }));

  assert.equal(finished.ok, false,
    'een muterende artifactopdracht vereist mutatieprovenance; woordoverlap in een leesreceipt is onvoldoende');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?')
    .get(workItem.id).status, 'open');
  assert.equal(fs.readFileSync(artifactPath, 'utf8'), staleArtifact);

  const unrelatedArgs = {
    pad: 'ongerelateerd-inkomstenbewijs.md',
    inhoud: 'De verkoopclaim en het prijsbewijs zijn woorden, maar dit is een ander bestand.\n',
  };
  const unrelatedOutput = await executeTool('schrijf_bestand', unrelatedArgs, {
    mode: 'chat', sender: 'Jaap', userText: workItem.goal,
  });
  const unrelatedAction = JSON.parse(unrelatedOutput);
  const unrelatedReceipt = {
    name: 'schrijf_bestand', args: unrelatedArgs, output: unrelatedOutput,
    ok: unrelatedAction.ok, verified: unrelatedAction.verified,
    changed: unrelatedAction.changed, needs_followup: unrelatedAction.needs_followup,
    partial: unrelatedAction.partial,
  };
  assert.equal(unrelatedReceipt.changed, true);
  const stillOpen = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id,
    resultaat: 'Het inkomstenartefact is gecorrigeerd en bevat nu prijsbewijs.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web',
    conversationId: 'web:mutating-work-read-only-proof',
    originMessageId: 'mutating-work-1', turnId: 'mutating-work-turn-1',
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
    priorToolResults: [readReceipt, unrelatedReceipt],
  }));
  assert.equal(stillOpen.ok, false,
    'een changed=true receipt voor een ander bestand bewijst het gevraagde doelpad niet');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?')
    .get(workItem.id).status, 'open');

  const binding = {
    understandingSha256: 'a'.repeat(64), requirementId: 'r1',
    addressesRequirementIds: ['r1'], basisReceiptIds: [],
    capabilityId: 'workspace.file.write', operationId: 'write',
    role: 'primary', executionType: 'write',
  };
  const correctedText = [
    '# Inkomstenkans start', '',
    'Voorstel: verifieer eerst bezit, staat en actuele prijs; dit is geen echte verkoopclaim.',
  ].join('\n');
  const writeArgs = { pad: artifactPath, inhoud: correctedText };
  const writeOutput = await executeTool('schrijf_bestand', writeArgs, {
    mode: 'chat', sender: 'Jaap', userText: workItem.goal,
  });
  const writeAction = JSON.parse(writeOutput);
  const writeReceipt = {
    name: 'schrijf_bestand', args: writeArgs, output: writeOutput,
    ok: writeAction.ok, verified: writeAction.verified, changed: writeAction.changed,
    needs_followup: writeAction.needs_followup, partial: writeAction.partial,
    semanticBinding: binding,
  };
  const finalReadOutput = await executeTool('lees_bestand', readArgs, {
    mode: 'chat', sender: 'Jaap', userText: workItem.goal,
  });
  const finalReadAction = JSON.parse(finalReadOutput);
  const finalReadReceipt = {
    name: 'lees_bestand', args: readArgs, output: finalReadOutput,
    ok: finalReadAction.ok, verified: finalReadAction.verified,
    changed: finalReadAction.changed, needs_followup: finalReadAction.needs_followup,
    partial: finalReadAction.partial, semanticBinding: binding,
  };
  const proven = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id,
    resultaat: 'Het inkomstenartefact bevat nu alleen het geverifieerde voorstelscenario.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web',
    conversationId: 'web:mutating-work-read-only-proof',
    originMessageId: 'mutating-work-1', turnId: 'mutating-work-turn-2',
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
    priorToolResults: [writeReceipt, finalReadReceipt],
  }));
  assert.equal(proven.ok, true,
    'alleen een exacte changed write gevolgd door volledige hashgelijke readback mag sluiten');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?')
    .get(workItem.id).status, 'done');
});

test('een duurzaam voorbereid webantwoord herstelt na restart exact zijn finale aflevering', async () => {
  const conversationId = 'web:durable-final-delivery';
  const requestId = 'web-delivery:restart:0001';
  const turnId = 'web:durable-final-delivery-turn';
  const resultText = 'Het web-afleveringsbewijs  is gecontroleerd.\nDe afronding blijft exact.';
  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Web afleveringsbewijs',
    goal: 'Controleer het web-afleveringsbewijs en rond het afleveringswerk af.',
    nextStep: 'Lees het web-afleveringsbewijs en rond daarna exact dit werk af.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: requestId, requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const proofPath = path.join(config.workspaceDir, 'web-afleveringsbewijs.txt');
  fs.mkdirSync(config.workspaceDir, { recursive: true });
  fs.writeFileSync(proofPath, 'Het web afleveringsbewijs is gecontroleerd.\n');
  const readArgs = { pad: proofPath };
  const readOutput = await executeTool('lees_bestand', readArgs, {
    mode: 'chat', sender: 'Jaap', userText: workItem.goal,
  });
  const readAction = JSON.parse(readOutput);
  const readReceipt = {
    name: 'lees_bestand', args: readArgs, output: readOutput,
    ok: readAction.ok, verified: readAction.verified, changed: readAction.changed,
    needs_followup: readAction.needs_followup, partial: readAction.partial,
  };
  const closed = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id, resultaat: resultText,
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: requestId, turnId,
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
    priorToolResults: [readReceipt],
  }));
  assert.equal(closed.ok, true);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');
  const capability = sealFinalDeliveryReply(episode.id, {
    workItemId: workItem.id, turnId, channel: 'web', conversationId,
    originMessageId: requestId, resultText,
  });
  assert.ok(capability?.bindingSha256);

  const prepared = createPreparedUnderstandingTurn({
    reply: resultText, turnId, channel: 'web', sender: 'Jaap', conversationId,
    goalEpisodeId: episode.id, workItemId: workItem.id,
    originMessageId: requestId,
    finalDeliveryBindingSha256: capability.bindingSha256,
    understood: true,
  });
  seedAuthorizedTurnUnderstanding({
    database: db, channel: 'web', conversationId, messageId: requestId,
    requester: 'Jaap', originalText: workItem.goal, sessionRef: turnId,
    supersessionScope: `web:${conversationId}`, supersessionToken: requestId,
    output: prepared,
  });

  const replayResult = {
    reply: resultText, turnId, channel: 'web', sender: 'Jaap', conversationId,
    tools: [], goalEpisodeId: episode.id, workItemId: workItem.id,
    originMessageId: requestId, replayed: true,
    finalDeliveryBindingSha256: capability.bindingSha256,
  };
  const chatOutBeforeForgery = db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=?`).get(turnId).count;
  assert.equal(recordDeliveredReply({
    ...replayResult, reply: 'Een vervalst antwoord.',
  }, { accepted: true, transport: 'http-replay' }), false);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=?`).get(turnId).count,
  chatOutBeforeForgery, 'een afgewezen durable-recoveryclaim schrijft geen chat_out');
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const {recordDeliveredReply}=await import('./src/mind/agent.js');
    const {db}=await import('./src/db.js');
    const result=JSON.parse(process.env.REPLAY_RESULT);
    const accepted=recordDeliveredReply(result,{accepted:true,transport:'http-replay'});
    const episode=db.prepare('SELECT state FROM eva_goal_episodes WHERE id=?').get(result.goalEpisodeId);
    const work=db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(result.workItemId);
    console.log(JSON.stringify({accepted,episode:episode.state,work:work.status}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true', EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
      REPLAY_RESULT: JSON.stringify(replayResult),
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const outcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.deepEqual(outcome, { accepted: true, episode: 'closed', work: 'done' });
  assert.equal(episodeHasFinalDelivery(episode.id), true);
  assert.equal(recordDeliveredReply(replayResult, {
    accepted: true, transport: 'http-replay',
  }), false, 'de duurzame replay sluit exact eenmaal');
});

test('een echte afronding bewaart delivery_pending tot een verse eindaflevering', async () => {
  const conversationId = 'web:work-finish-lifecycle';
  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Drie verkoopteksten',
    goal: 'Drie verkoopteksten zijn gecontroleerd teruggelezen.',
    nextStep: 'Lees het gecontroleerde bestand terug en bezorg de uitkomst.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'finish-1', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: 'finish-1',
    deliveredConversationId: conversationId,
    resultText: 'Nog open: ik noem dit nog niet afgerond.',
    idempotencyKey: `interim-${episode.id}`,
  });
  assert.equal(episodeHasDelivery(episode.id), true);
  assert.equal(episodeHasFinalDelivery(episode.id), false);

  fs.mkdirSync(config.workspaceDir, { recursive: true });
  const proofPath = path.join(config.workspaceDir, 'verkoopteksten-bewijs.txt');
  fs.writeFileSync(proofPath,
    'Drie verkoopteksten zijn gecontroleerd teruggelezen; elke tekst bevat aanbod, prijs en contactstap.\n');
  const userText = `Lees ${proofPath} terug en rond werk #${workItem.id} af met het gecontroleerde resultaat.`;
  const readText = `Lees ${proofPath} terug`;
  const closeText = `rond werk #${workItem.id} af met het gecontroleerde resultaat`;
  const readStart = userText.indexOf(readText);
  const closeStart = userText.indexOf(closeText);
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (_url, options = {}) => {
    calls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    if (body.tools?.[0]?.name === 'commit_turn_understanding') {
      return modelResponse([{
        type: 'function_call', call_id: 'work-finish-understanding',
        name: 'commit_turn_understanding',
        arguments: JSON.stringify({
          dialogueAct: 'command',
          goal: {
            statement: 'Rond het geverifieerde verkooptekstenwerk af.',
            outcome: 'Drie gecontroleerde verkoopteksten zijn aantoonbaar afgerond.',
            beneficiary: 'requester',
          },
          conditions: [],
          provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
          confidence: 0.99,
          actions: [{
            capabilityId: 'workspace.file.read', operationId: 'read', polarity: 'request',
            semanticInput: { pad: proofPath },
            sourceSpans: [{ start: readStart, end: readStart + readText.length }],
          }, {
            capabilityId: 'work.episode.manage', operationId: 'afronden', polarity: 'request',
            semanticInput: {
              id: workItem.id,
              resultaat: 'Drie verkoopteksten zijn gecontroleerd teruggelezen en afgerond.',
            },
            sourceSpans: [{ start: closeStart, end: closeStart + closeText.length }],
          }],
          requestedEffects: ['read', 'persistent'], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
        }),
      }]);
    }
    return textResponse('De drie verkoopteksten zijn gecontroleerd teruggelezen en nu afgerond.');
  };

  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: userText,
      messageContext: { identityTrusted: true, conversationId },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls, 2);
  assert.deepEqual(turn.tools.map((tool) => tool.name), ['lees_bestand', 'werk_bijhouden']);
  assert.equal(turn.tools[1].ok, true);
  assert.equal(turn.tools[1].verified, true);
  assert.equal(turn.tools[1].changed, true);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending',
    'finishTurn mag een verse eindaflevering niet terug naar waiting zetten');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(workItem.id).status, 'open');
  assert.equal(episodeHasFinalDelivery(episode.id), false);

  const armedState = JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItem.id).state_json);
  assert.equal(armedState.final_delivery_binding.stage, 'sealed');
  assert.equal(armedState.final_delivery_binding.turnId, turn.turnId);
  assert.equal(armedState.final_delivery_binding.bindingSha256,
    turn.finalDeliveryBindingSha256);

  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: turn.originMessageId,
    deliveredConversationId: conversationId,
    resultText: 'Oud voortgangsbericht dat deze afronding niet mag sluiten.',
    idempotencyKey: `oude-callback-${episode.id}`,
    deliveryTurnId: 'oude-beurt',
  }), /actuele antwoordbinding|eenmalige actuele|wacht nog/iu);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');

  assert.equal(recordDeliveredReply({ ...turn }, { accepted: true, transport: 'http' }), false,
    'een clone bezit de private eenmalige afleverbekwaamheid niet');
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');

  db.prepare(`INSERT INTO timeline (ts,kind,content,meta) VALUES (?,?,?,?)`).run(
    new Date().toISOString(), 'chat_out',
    `Eva -> Jaap: ${turn.reply}`,
    JSON.stringify({
      channel: 'web', conversationId, sender: 'Eva', turnId: turn.turnId,
      goalEpisodeId: episode.id, workItemId: workItem.id,
      originMessageId: turn.originMessageId,
      finalDeliveryBindingSha256: turn.finalDeliveryBindingSha256,
      delivery: { accepted: true, transport: 'http' },
    }),
  );
  assert.equal(recoverDeliveredEpisodeReceipts(), 0,
    'een web-chat_out-marker is zonder private in-memory capability geen herstelbewijs');
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');

  assert.equal(recordDeliveredReply(turn, { accepted: true, transport: 'http' }), false,
    'de exacte originele result sluit ondanks een al geschreven chat_out');
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(workItem.id).status, 'done');
  assert.equal(episodeHasFinalDelivery(episode.id), true);
  const recoveredCount = db.prepare('SELECT COUNT(*) AS count FROM eva_delivery_receipts WHERE episode_id=?')
    .get(episode.id).count;
  assert.equal(recoveredCount, 2);
  assert.equal(recordDeliveredReply(turn, { accepted: true, transport: 'http' }), false,
    'de private capability kan een finale aflevering niet nogmaals sluiten');
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_delivery_receipts WHERE episode_id=?')
    .get(episode.id).count, recoveredCount);
});

test('een exacte live eindaflevering sluit eenmaal zonder herstelpad', async () => {
  const conversationId = 'web:work-live-final';
  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Live eindaflevering',
    goal: 'Het actuele resultaat wordt rechtstreeks afgeleverd.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'live-origin', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const proofPath = path.join(config.workspaceDir, 'live-final-bewijs.txt');
  fs.writeFileSync(proofPath, 'Het actuele live-resultaat is gecontroleerd.\n');
  const userText = `Lees ${proofPath} en rond werk #${workItem.id} af.`;
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (_url, options = {}) => {
    calls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    if (body.tools?.[0]?.name === 'commit_turn_understanding') {
      const readText = `Lees ${proofPath}`;
      const closeText = `rond werk #${workItem.id} af`;
      return modelResponse([{ type: 'function_call', call_id: 'live-final-understanding',
        name: 'commit_turn_understanding', arguments: JSON.stringify({
          dialogueAct: 'command', goal: { statement: 'Rond het actuele werk af.', outcome: 'Actueel resultaat bezorgd.', beneficiary: 'requester' },
          conditions: [], provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] }, confidence: 0.99,
          actions: [{ capabilityId: 'workspace.file.read', operationId: 'read', polarity: 'request', semanticInput: { pad: proofPath }, sourceSpans: [{ start: userText.indexOf(readText), end: userText.indexOf(readText) + readText.length }] },
            { capabilityId: 'work.episode.manage', operationId: 'afronden', polarity: 'request', semanticInput: { id: workItem.id, resultaat: 'Het actuele live-resultaat is gecontroleerd.' }, sourceSpans: [{ start: userText.indexOf(closeText), end: userText.indexOf(closeText) + closeText.length }] }],
          requestedEffects: ['read', 'persistent'], deniedEffects: [], temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn', delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [], firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
        }) }]);
    }
    return textResponse('Het actuele live-resultaat is gecontroleerd en afgerond.');
  };
  let turn;
  try {
    turn = await handleMessageDetailed({ channel: 'web', sender: 'Jaap', text: userText,
      messageContext: { identityTrusted: true, conversationId } });
  } finally { globalThis.fetch = originalFetch; }
  assert.equal(calls, 2);
  const other = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Andere legacy aflevering',
    goal: 'Deze andere episode mag de private capability niet lenen.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: turn.originMessageId, requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  transitionGoalEpisode(other.episode.id, { state: 'delivery_pending' });
  const originalEpisodeId = turn.goalEpisodeId;
  const originalWorkItemId = turn.workItemId;
  turn.goalEpisodeId = other.episode.id;
  turn.workItemId = other.workItem.id;
  assert.equal(recordDeliveredReply(turn, { accepted: true, transport: 'http' }), false,
    'een capability voor episode A volgt geen gemuteerd result-object naar episode B');
  assert.equal(goalEpisodeById(other.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_delivery_receipts WHERE episode_id=?')
    .get(other.episode.id).count, 0);
  turn.goalEpisodeId = originalEpisodeId;
  turn.workItemId = originalWorkItemId;
  assert.equal(recordDeliveredReply(turn, { accepted: true, transport: 'http' }), true);
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
  assert.equal(recordDeliveredReply(turn, { accepted: true, transport: 'http' }), false);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_delivery_receipts WHERE episode_id=?')
    .get(episode.id).count, 1);
});

test('multiline en dubbele spaties behouden de private reply-identiteit op web en voice', async (t) => {
  for (const channel of ['web', 'voice']) {
    await t.test(channel, async () => {
      const conversationId = `${channel}:exact-whitespace-final`;
      const { workItem, episode } = createDurableWork({
        source: channel, owner: 'Jaap', title: `Exacte ${channel}-reply`,
        goal: 'Het gecontroleerde resultaat wordt met exacte witruimte afgeleverd.',
      }, {
        originKind: 'user', originChannel: channel, originConversationId: conversationId,
        originMessageId: `${channel}-whitespace-origin`, requester: 'Jaap',
        deliveryPolicy: 'required', deliveryTarget: 'Jaap',
      });
      const proofPath = path.join(config.workspaceDir, `${channel}-whitespace-bewijs.txt`);
      fs.writeFileSync(proofPath,
        'Het gecontroleerde resultaat wordt met exacte witruimte afgeleverd.\n');
      const readText = `Lees ${proofPath}`;
      const closeText = `rond werk #${workItem.id} af`;
      const userText = `${readText} en ${closeText}.`;
      const finalReply = 'Eerste regel  bewaart dubbele ruimte.\nTweede regel blijft exact.';
      const originalFetch = globalThis.fetch;
      let calls = 0;
      globalThis.fetch = async (_url, options = {}) => {
        calls += 1;
        const body = JSON.parse(String(options.body || '{}'));
        if (body.tools?.[0]?.name === 'commit_turn_understanding') {
          return modelResponse([{ type: 'function_call', call_id: `${channel}-whitespace-understanding`,
            name: 'commit_turn_understanding', arguments: JSON.stringify({
              dialogueAct: 'command',
              goal: { statement: 'Rond het gecontroleerde werk af.',
                outcome: 'Het gecontroleerde resultaat is exact bezorgd.', beneficiary: 'requester' },
              conditions: [],
              provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
              confidence: 0.99,
              actions: [{
                capabilityId: 'workspace.file.read', operationId: 'read', polarity: 'request',
                semanticInput: { pad: proofPath },
                sourceSpans: [{ start: userText.indexOf(readText),
                  end: userText.indexOf(readText) + readText.length }],
              }, {
                capabilityId: 'work.episode.manage', operationId: 'afronden', polarity: 'request',
                semanticInput: { id: workItem.id,
                  resultaat: 'Het gecontroleerde resultaat is exact bezorgd.' },
                sourceSpans: [{ start: userText.indexOf(closeText),
                  end: userText.indexOf(closeText) + closeText.length }],
              }],
              requestedEffects: ['read', 'persistent'], deniedEffects: [],
              temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
              delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
              firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
            }) }]);
        }
        return textResponse(finalReply);
      };
      let turn;
      try {
        turn = await handleMessageDetailed({ channel, sender: 'Jaap', text: userText,
          messageContext: { identityTrusted: true, conversationId } });
      } finally {
        globalThis.fetch = originalFetch;
      }
      assert.equal(calls, 2);
      assert.equal(turn.reply, finalReply);
      assert.equal(recordDeliveredReply(turn, {
        accepted: true, transport: channel === 'voice' ? 'voice' : 'http',
      }), true);
      assert.equal(goalEpisodeById(episode.id).state, 'closed');
      assert.equal(episodeHasFinalDelivery(episode.id), true);
    });
  }
});

test('afleverreceipt, assistant-message en chat_out rollen samen terug', () => {
  const conversationId = 'web:atomic-delivery-projection';
  const originMessageId = 'atomic-delivery-origin';
  const turnId = 'atomic-delivery-turn';
  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Atomische webaflevering',
    goal: 'Projecteer één aantoonbaar afgeleverd antwoord atomisch.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId, requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  transitionGoalEpisode(episode.id, { state: 'delivery_pending' });
  const messageCount = db.prepare('SELECT count(*) AS n FROM main.messages').get().n;
  const publishedMessages = [];
  const onChat = (message) => publishedMessages.push(message);
  bus.on('chat', onChat);
  db.exec(`CREATE TEMP TRIGGER fail_atomic_chat_out
    BEFORE INSERT ON main.timeline WHEN NEW.kind='chat_out'
    BEGIN SELECT RAISE(ABORT,'forced chat_out failure'); END`);
  try {
    assert.throws(() => recordDeliveredReply({
      reply: 'Dit antwoord mag alleen volledig worden geprojecteerd.',
      turnId, channel: 'web', sender: 'Jaap', conversationId,
      originMessageId, workItemId: workItem.id, goalEpisodeId: episode.id,
      tools: [], syntheticTest: false,
    }, { accepted: true, transport: 'http-test' }), /forced chat_out failure/u);
  } finally {
    bus.off('chat', onChat);
    db.exec('DROP TRIGGER temp.fail_atomic_chat_out');
  }
  assert.equal(db.prepare('SELECT count(*) AS n FROM main.messages').get().n, messageCount,
    'een mislukte chat_out laat geen losse assistant-message achter');
  assert.equal(db.prepare('SELECT count(*) AS n FROM main.eva_delivery_receipts WHERE episode_id=?')
    .get(episode.id).n, 0, 'een mislukte conversatieprojectie laat geen deliveryreceipt achter');
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending',
    'de episode wordt zonder complete conversatieprojectie niet gesloten');
  assert.equal(publishedMessages.length, 0,
    'een teruggedraaide assistant-message wordt ook niet voortijdig gepubliceerd');
});

test('inhoudelijke vervolgmutatie vervalt een oude binding en re-armt met een nieuwe identiteit', async () => {
  const conversationId = 'web:work-rearm-lifecycle';
  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Herarmbare eindaflevering',
    goal: 'Een geverifieerde uitkomst wordt exact één keer actueel afgeleverd.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'rearm-origin', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const proof = {
    name: 'lees_bestand', ok: true, verified: true,
    output: JSON.stringify({ contract: 'ActionResult/v1', ok: true, verified: true,
      evidence: { summary: 'De geverifieerde uitkomst is exact teruggelezen.' } }),
  };
  const finish = (turnId, result = 'De geverifieerde uitkomst is klaar.') => executeTool(
    'werk_bijhouden', { bewerking: 'afronden', id: workItem.id, resultaat: result }, {
      mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
      originMessageId: 'rearm-origin', turnId,
      activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
      priorToolResults: [proof],
    },
  );

  assert.equal(JSON.parse(await finish('turn-rearm-1')).ok, true);
  const firstCapability = sealFinalDeliveryReply(episode.id, {
    workItemId: workItem.id, turnId: 'turn-rearm-1', channel: 'web', conversationId,
    originMessageId: 'rearm-origin', resultText: 'De geverifieerde uitkomst is klaar.',
  });
  assert.ok(firstCapability);
  const first = JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItem.id).state_json).final_delivery_binding;
  assert.equal(first.stage, 'sealed');

  const updated = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'update', id: workItem.id, volgende_stap: 'Controleer de gewijzigde uitkomst opnieuw.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: 'rearm-origin', turnId: 'turn-rearm-update',
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
  }));
  assert.equal(updated.ok, true);
  const revoked = JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItem.id).state_json).final_delivery_binding;
  assert.equal(revoked.stage, 'revoked');
  assert.equal(revoked.priorBindingSha256, first.bindingSha256);
  assert.match(revoked.reason, /^work_mutated:/u);
  assert.notEqual(revoked.bindingSha256, first.bindingSha256);
  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: 'rearm-origin',
    deliveredConversationId: conversationId,
    resultText: 'De geverifieerde uitkomst is klaar.',
    idempotencyKey: `turn:turn-rearm-1:episode:${episode.id}`,
    deliveryTurnId: 'turn-rearm-1', finalDeliveryBinding: firstCapability,
  }), /wacht nog op de exact gebonden antwoordtekst/iu);
  db.prepare('INSERT INTO timeline (ts,kind,content,meta) VALUES (?,?,?,?)').run(
    new Date().toISOString(), 'chat_out',
    'Eva -> Jaap: De geverifieerde uitkomst is klaar.',
    JSON.stringify({
      channel: 'web', conversationId, sender: 'Eva', turnId: 'turn-rearm-1',
      goalEpisodeId: episode.id, workItemId: workItem.id,
      originMessageId: 'rearm-origin',
      finalDeliveryBindingSha256: first.bindingSha256,
      delivery: { accepted: true, transport: 'http' },
    }),
  );
  assert.equal(recoverDeliveredEpisodeReceipts(), 0);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_delivery_receipts WHERE episode_id=?')
    .get(episode.id).count, 0);

  const updatedAgain = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'update', id: workItem.id,
    volgende_stap: 'Controleer de tweede gewijzigde uitkomst opnieuw.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: 'rearm-origin', turnId: 'turn-rearm-update-2',
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
  }));
  assert.equal(updatedAgain.ok, true);
  const revokedAgain = JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItem.id).state_json).final_delivery_binding;
  assert.equal(revokedAgain.stage, 'revoked');
  assert.equal(revokedAgain.priorBindingSha256, revoked.bindingSha256);

  assert.equal(JSON.parse(await finish('turn-rearm-2', 'De gewijzigde uitkomst is klaar.')).ok, true);
  const second = JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItem.id).state_json).final_delivery_binding;
  assert.equal(second.stage, 'awaiting_reply');
  assert.notEqual(second.bindingSha256, first.bindingSha256);
  assert.notEqual(second.turnId, first.turnId);
  const secondCapability = sealFinalDeliveryReply(episode.id, {
    workItemId: workItem.id, turnId: 'turn-rearm-2', channel: 'web', conversationId,
    originMessageId: 'rearm-origin', resultText: 'De gewijzigde uitkomst is klaar.',
  });
  assert.ok(secondCapability);
  const receipt = recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: 'rearm-origin',
    deliveredConversationId: conversationId, resultText: 'De gewijzigde uitkomst is klaar.',
    idempotencyKey: `turn:turn-rearm-2:episode:${episode.id}`,
    deliveryTurnId: 'turn-rearm-2', finalDeliveryBinding: secondCapability,
  });
  assert.ok(receipt);
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
  assert.equal(JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItem.id).state_json).final_delivery_binding, undefined);
});

test('armed awaiting_reply weigert ook een eerder geaccepteerde callback vóór sealing', async () => {
  const conversationId = 'web:awaiting-old-callback';
  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Awaiting callbackgrens',
    goal: 'Alleen de finale tekst van de huidige beurt sluit.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'awaiting-origin', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const proof = {
    name: 'lees_bestand', ok: true, verified: true,
    args: { pad: 'awaiting-callback-bewijs.txt' },
    output: JSON.stringify({ contract: 'ActionResult/v1', ok: true, verified: true,
      data: { content: 'Alleen de finale tekst van de huidige beurt sluit.' },
      evidence: { summary: 'Alleen de finale tekst van de huidige beurt sluit.' } }),
  };
  const finished = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id, resultaat: 'Actuele uitkomst.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: 'awaiting-origin', turnId: 'awaiting-current-turn',
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
    priorToolResults: [proof],
  }));
  assert.equal(finished.ok, true);
  assert.equal(JSON.parse(finished.data.item.state_json).final_delivery_binding.stage, 'awaiting_reply');
  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: 'awaiting-origin',
    deliveredConversationId: conversationId, resultText: 'Oud geaccepteerd antwoord.',
    idempotencyKey: 'accepted-before-arming', deliveryTurnId: 'oude-turn',
    finalDeliveryBinding: { capabilityToken: 'vals', bindingSha256: '0'.repeat(64) },
  }), /wacht nog op de exact gebonden antwoordtekst/iu);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');

  assert.equal(sealFinalDeliveryReply(episode.id, {
    workItemId: workItem.id, turnId: 'verkeerde-turn', channel: 'web', conversationId,
    originMessageId: 'awaiting-origin', resultText: 'Actuele uitkomst.',
  }), null);
  assert.equal(JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItem.id).state_json).final_delivery_binding.stage, 'awaiting_reply');
});

test('een vóór arming bestaand same-key receipt kan de actuele binding niet omzeilen', async () => {
  const conversationId = 'web:preseed-same-key';
  const turnId = 'preseed-current-turn';
  const { workItem, episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Preseed receiptgrens',
    goal: 'Een oude ontvangstbon wordt nooit een nieuwe eindbon.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'preseed-origin', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: 'preseed-origin',
    deliveredConversationId: conversationId, resultText: 'Oude voortgang.',
    idempotencyKey: `turn:${turnId}:episode:${episode.id}`,
  });
  const proof = { name: 'lees_bestand', args: { pad: 'preseed-bewijs.txt' },
    ok: true, verified: true,
    output: JSON.stringify({ contract: 'ActionResult/v1', ok: true, verified: true,
      data: { content: 'Een oude ontvangstbon wordt nooit een nieuwe eindbon.' },
      evidence: { summary: 'Een oude ontvangstbon wordt nooit een nieuwe eindbon.' } }) };
  const finished = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id, resultaat: 'Nieuwe finale uitkomst.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: 'preseed-origin', turnId,
    activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
    priorToolResults: [proof],
  }));
  assert.equal(finished.ok, true);
  const sealed = sealFinalDeliveryReply(episode.id, {
    workItemId: workItem.id, turnId, channel: 'web', conversationId,
    originMessageId: 'preseed-origin', resultText: 'Nieuwe finale uitkomst.',
  });
  assert.ok(sealed);
  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: 'preseed-origin',
    deliveredConversationId: conversationId, resultText: 'Oude voortgang.',
    idempotencyKey: `turn:${turnId}:episode:${episode.id}`,
    deliveryTurnId: turnId,
    finalDeliveryBinding: { bindingSha256: sealed.bindingSha256,
      capabilityToken: sealed.capabilityToken },
  }), /bestaand afleveringsbewijs hoort niet bij de actuele antwoordbinding/iu);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');
  assert.equal(episodeHasFinalDelivery(episode.id), false);
});

test('een bijna volle werktoestand wordt nooit pending-unarmed en een armed event blijft fail-closed', async () => {
  const conversationId = 'web:near-limit-final-binding';
  const proof = { name: 'lees_bestand', args: { pad: 'near-limit-bewijs.txt' },
    ok: true, verified: true,
    output: JSON.stringify({ contract: 'ActionResult/v1', ok: true, verified: true,
      data: { content: 'De bijna volle werktoestand is gecontroleerd.' },
      evidence: { summary: 'De bijna volle werktoestand is gecontroleerd.' } }) };
  const sealingBoundary = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Verzegelingscapaciteit',
    goal: 'De bijna volle werktoestand is gecontroleerd.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'sealed-capacity-origin', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const boundaryState = JSON.stringify({ keep: 'x'.repeat(11_200) });
  db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
    .run(boundaryState, sealingBoundary.workItem.id);
  const capacityRejected = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: sealingBoundary.workItem.id, resultaat: 'Grensresultaat.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: 'sealed-capacity-origin', turnId: 'sealed-capacity-turn',
    activeWorkId: sealingBoundary.workItem.id,
    activeGoalEpisodeId: sealingBoundary.episode.id,
    priorToolResults: [proof],
  }));
  assert.equal(capacityRejected.ok, false);
  assert.match(JSON.stringify(capacityRejected), /latere verzegeling past niet veilig/iu,
    'de awaiting-vorm past nog, maar de maximale sealed-vorm niet');
  assert.equal(goalEpisodeById(sealingBoundary.episode.id).state, 'committed');
  assert.equal(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(sealingBoundary.workItem.id).state_json, boundaryState);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM eva_episode_events
    WHERE episode_id=? AND kind='final_delivery_armed'`)
    .get(sealingBoundary.episode.id).count, 0);

  const crowded = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Bijna volle werktoestand',
    goal: 'De bijna volle werktoestand is gecontroleerd.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'near-limit-origin', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const largeState = JSON.stringify({ keep: 'x'.repeat(11_500) });
  db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
    .run(largeState, crowded.workItem.id);
  const rejected = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: crowded.workItem.id, resultaat: 'Bijna vol resultaat.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: 'near-limit-origin', turnId: 'near-limit-turn',
    activeWorkId: crowded.workItem.id, activeGoalEpisodeId: crowded.episode.id,
    priorToolResults: [proof],
  }));
  assert.equal(rejected.ok, false);
  assert.match(JSON.stringify(rejected), /past niet veilig/iu);
  assert.equal(goalEpisodeById(crowded.episode.id).state, 'committed');
  assert.equal(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(crowded.workItem.id).state_json, largeState);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM eva_episode_events
    WHERE episode_id=? AND kind='final_delivery_armed'`).get(crowded.episode.id).count, 0);

  const sticky = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Sticky armed episode',
    goal: 'Een ooit gewapende episode valt nooit terug naar legacy.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: 'sticky-origin', requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const armed = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: sticky.workItem.id, resultaat: 'Sticky finaal.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'web', conversationId,
    originMessageId: 'sticky-origin', turnId: 'sticky-turn',
    activeWorkId: sticky.workItem.id, activeGoalEpisodeId: sticky.episode.id,
    priorToolResults: [{ ...proof,
      output: JSON.stringify({ contract: 'ActionResult/v1', ok: true, verified: true,
        data: { content: 'Een ooit gewapende episode valt nooit terug naar legacy.' },
        evidence: { summary: 'Een ooit gewapende episode valt nooit terug naar legacy.' } }) }],
  }));
  assert.equal(armed.ok, true);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM eva_episode_events
    WHERE episode_id=? AND kind='final_delivery_armed'`).get(sticky.episode.id).count, 1);
  db.prepare("UPDATE eva_work_items SET state_json='{}' WHERE id=?").run(sticky.workItem.id);
  assert.throws(() => recordEpisodeDelivery(sticky.episode.id, {
    deliveredChannel: 'web', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: 'sticky-origin',
    deliveredConversationId: conversationId, resultText: 'Sticky finaal.',
    idempotencyKey: `sticky-lost-state-${sticky.episode.id}`,
  }), /hoort niet bij deze episode/iu);
  assert.equal(goalEpisodeById(sticky.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM eva_delivery_receipts WHERE episode_id=?')
    .get(sticky.episode.id).count, 0);
});
