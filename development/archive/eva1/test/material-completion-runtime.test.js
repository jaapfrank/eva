import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-material-runtime-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_RUNTIME_MODE = 'normal';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.OPENAI_API_KEY = 'test-only';

const { config } = await import('../src/config.js');
const { db } = await import('../src/db.js');
const { handleMessageDetailed } = await import('../src/mind/agent.js');
const {
  fixedMaterialAuthorizationReply,
  fixedMaterialIncompleteReply,
  materialRequestSentinel,
} = await import('../src/mind/materialcompletion.js');
const { issueVoiceIngressAuthority } = await import('../src/mind/voiceingress.js');

after(() => {
  if (db.open) db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function modelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { headers: { 'content-type': 'text/event-stream' } });
}

function span(source, needle) {
  const start = source.indexOf(needle);
  assert.notEqual(start, -1, `bronspan ontbreekt: ${needle}`);
  return [{ start, end: start + needle.length }];
}

function durableCounts() {
  return {
    work: db.prepare('SELECT COUNT(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT COUNT(*) AS count FROM eva_goal_episodes').get().count,
    handoffs: db.prepare(
      'SELECT COUNT(*) AS count FROM eva_interpretation_retry_handoffs',
    ).get().count,
  };
}

test('conditionele workspace-write blijft buiten success en exact-origin replay dupliceert niets', async () => {
  fs.mkdirSync(config.workspaceDir, { recursive: true });
  const sourceDisplayPath = 'data/workspace/material-bron.md';
  const targetDisplayPath = 'data/workspace/material-resultaat.md';
  const sourcePath = path.join(config.workspaceDir, 'material-bron.md');
  const targetPath = path.join(config.workspaceDir, 'material-resultaat.md');
  const sourceBytes = Buffer.from('# Bron\n\nHet huidige bewijs is nog niet veranderd.\n');
  fs.writeFileSync(sourcePath, sourceBytes);

  const readClause = `Lees ${sourceDisplayPath}`;
  const writeClause = `Corrigeer ${targetDisplayPath} als het bewijs verandert`;
  const userText = `${readClause}. ${writeClause}.`;
  const conversationId = 'web:material-conditional-runtime';
  const messageId = 'material-origin-1';
  const before = durableCounts();
  const originalFetch = globalThis.fetch;
  let understandingCalls = 0;
  let composerCalls = 0;
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    if (!request.tools?.some((tool) => tool.name === 'commit_turn_understanding')) {
      composerCalls += 1;
      return modelResponse([{
        type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'Deze composeruitkomst mag niet ontsnappen.' }],
      }]);
    }
    understandingCalls += 1;
    return modelResponse([{
      type: 'function_call', call_id: `material-understanding-${understandingCalls}`,
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: {
          statement: 'Controleer de bron en corrigeer alleen als het bewijs verandert.',
          outcome: 'Een bewijsgebonden intern resultaat.', beneficiary: 'requester',
        },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{
          capabilityId: 'workspace.file.read', operationId: 'read', polarity: 'request',
          semanticInput: { pad: sourceDisplayPath }, sourceSpans: span(userText, readClause),
        }, {
          capabilityId: 'workspace.file.write', operationId: 'write', polarity: 'request',
          semanticInput: { pad: targetDisplayPath }, sourceSpans: span(userText, writeClause),
        }],
        requestedEffects: ['read', 'workspace'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
      }),
    }]);
  };

  let first;
  let replay;
  try {
    const input = {
      channel: 'web', sender: 'Jaap', text: userText,
      messageContext: { identityTrusted: true, conversationId, messageId },
    };
    first = await handleMessageDetailed(input);
    replay = await handleMessageDetailed(input);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(understandingCalls, 2, 'iedere aanbieding gebruikt één geldige interpretatie');
  assert.equal(composerCalls, 0, 'onbewezen materiaalwerk bereikt de vrije composer niet');
  for (const turn of [first, replay]) {
    assert.equal(turn.reply, fixedMaterialIncompleteReply({
      hadVerifiedRead: true, handoffVerified: true,
    }));
    assert.equal(turn.error, 'material_completion_unproven');
    assert.equal(turn.actionFailed, true);
    assert.equal(turn.executionPending, true);
    assert.equal(turn.durableHandoff, true);
    assert.deepEqual(turn.tools.map(({ name, ok, verified }) => ({ name, ok, verified })), [{
      name: 'lees_bestand', ok: true, verified: true,
    }]);
  }

  assert.equal(replay.workItemId, first.workItemId);
  assert.equal(replay.goalEpisodeId, first.goalEpisodeId);
  assert.deepEqual(durableCounts(), {
    work: before.work + 1,
    episodes: before.episodes + 1,
    handoffs: before.handoffs + 1,
  });
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM eva_episode_origins
    WHERE episode_id=? AND origin_kind='user' AND origin_channel=?
      AND origin_conversation_id=? AND origin_message_id=?`).get(
    first.goalEpisodeId, 'web', conversationId, messageId,
  ).count, 1);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM turn_tool_executions
    WHERE tool='schrijf_bestand'`).get().count, 0);
  assert.equal(fs.existsSync(targetPath), false);
  assert.deepEqual(fs.readFileSync(sourcePath), sourceBytes);

  const work = db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(first.workItemId);
  const episode = db.prepare('SELECT * FROM eva_goal_episodes WHERE id=?')
    .get(first.goalEpisodeId);
  const retry = JSON.parse(work.state_json).interpretation_retry;
  assert.equal(work.status, 'open');
  assert.equal(episode.state, 'waiting');
  assert.equal(retry.phase, 'pending');
  assert.equal(retry.attempts, 0);
  assert.equal(retry.originalText, userText);
  assert.equal(retry.materialAuthoritySha256, undefined,
    'een onvolledige interpretatie wordt niet als herstelauthority vastgezet');
});

test('een vrije modelreply kan een expliciete materiaalmutatie niet als gedaan presenteren', async () => {
  const userText = 'Corrigeer data/workspace/sentinel-resultaat.md en sla het controleerbaar op.';
  const before = durableCounts();
  const originalFetch = globalThis.fetch;
  let composerCalls = 0;
  globalThis.fetch = async (_url, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    if (!request.tools?.some((tool) => tool.name === 'commit_turn_understanding')) {
      composerCalls += 1;
      return modelResponse([{
        type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'Ik heb alles gecorrigeerd.' }],
      }]);
    }
    return modelResponse([{
      type: 'function_call', call_id: 'sentinel-omitted-actions',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: { statement: userText, outcome: 'Een gecorrigeerd intern bestand.', beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: {
          kind: 'reply', requirementIds: [], replyDraft: 'Ik heb alles gecorrigeerd.',
        },
      }),
    }]);
  };

  let result;
  try {
    result = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: userText,
      messageContext: {
        identityTrusted: true,
        conversationId: 'web:material-sentinel-runtime',
        messageId: 'material-sentinel-origin-1',
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(composerCalls, 0);
  assert.equal(result.reply, fixedMaterialIncompleteReply({
    hadVerifiedRead: false, handoffVerified: true,
  }));
  assert.equal(result.actionFailed, true, JSON.stringify(result));
  assert.equal(result.durableProofMissing, true);
  assert.equal(result.durableHandoff, true);
  assert.deepEqual(result.tools, []);
  assert.deepEqual(durableCounts(), {
    work: before.work + 1,
    episodes: before.episodes + 1,
    handoffs: before.handoffs + 1,
  });
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM turn_tool_executions
    WHERE tool='schrijf_bestand'`).get().count, 0);
  assert.equal(fs.existsSync(path.join(config.workspaceDir, 'sentinel-resultaat.md')), false);
});

test('creëer/genereer zonder modelactions bereikt nooit een zichtbare succesclaim', async () => {
  for (const [index, userText] of [
    'Creëer data/workspace/offerte.txt en lever het op.',
    'Genereer data/workspace/rapport.txt.',
  ].entries()) {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => modelResponse([{
      type: 'function_call', call_id: `material-verb-omission-${index}`,
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: { statement: userText, outcome: 'Een nieuw intern bestand.', beneficiary: 'requester' },
        conditions: [], provenance: {
          sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [],
        }, confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: {
          kind: 'reply', requirementIds: [], replyDraft: 'Aangemaakt en opgeleverd.',
        },
      }),
    }]);
    let result;
    try {
      result = await handleMessageDetailed({
        channel: 'web', sender: 'Jaap', text: userText,
        messageContext: {
          identityTrusted: true,
          conversationId: `web:material-verb-${index}`,
          messageId: `material-verb-origin-${index}`,
        },
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
    assert.equal(result.actionFailed, true, `${userText}: ${JSON.stringify(result)}`);
    assert.equal(result.durableHandoff, true);
    assert.doesNotMatch(result.reply, /(?:^|\s)(?:aangemaakt en opgeleverd|klaar[.!]?)(?:\s|$)/iu);
  }
});

test('multiline transporttekst blijft exact bronmateriaal voor sentinel en handoff', async () => {
  const userText = [
    'Corrigeer alle inkomstenartefacten en sla het resultaat controleerbaar op.',
    'Context: gebruik alleen bronbewijs en publiceer niets.',
  ].join('\n');
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw Object.assign(new Error('provider tijdelijk niet beschikbaar'), { status: 503 });
  };
  let result;
  try {
    result = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: userText,
      messageContext: {
        identityTrusted: true,
        conversationId: 'web:material-multiline-runtime',
        messageId: 'material-multiline-origin-1',
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(result.error, 'material_request_uninterpreted');
  assert.equal(result.durableHandoff, true);
  assert.equal(result.actionFailed, true, JSON.stringify(result));
  assert.equal(result.reply, fixedMaterialIncompleteReply({
    hadVerifiedRead: false, handoffVerified: true,
  }));
  const work = db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(result.workItemId);
  const retry = JSON.parse(work.state_json).interpretation_retry;
  assert.equal(retry.originalText, userText);
  assert.equal(retry.rawOriginalTextSha256,
    createHash('sha256').update(userText).digest('hex'));
});

test('fysieke voice-satellite activeert material boundary zonder een persoon te verzinnen', async () => {
  const userText = 'Overschrijf data/workspace/voice-resultaat.md met een nieuwe tekst.';
  const voiceMessageId = 'voice-unidentified-material-1';
  const voiceSatellite = 'assist_satellite.voice_patio';
  const voiceLaneKey = `voice-lane:${voiceSatellite}`;
  const before = durableCounts();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => modelResponse([{
    type: 'function_call', call_id: 'voice-unidentified-material-omission',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'command',
      goal: { statement: userText, outcome: 'Een gewijzigd bestand.', beneficiary: 'household' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: 'Ik heb het aangepast.' },
    }),
  }]);
  let result;
  try {
    result = await handleMessageDetailed({
      channel: 'voice', sender: 'Huisgenoot', text: userText,
      messageContext: {
        identityTrusted: false,
        conversationId: 'voice:assist_satellite.voice_patio',
        messageId: voiceMessageId,
        voiceSatellite,
        voiceLaneKey,
        voiceIngressAuthority: issueVoiceIngressAuthority({
          messageId: voiceMessageId, satellite: voiceSatellite, laneKey: voiceLaneKey,
        }),
        isCurrent: () => true,
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(result.actionFailed, true, JSON.stringify(result));
  assert.equal(result.durableHandoff, true, JSON.stringify(result));
  assert.equal(result.reply, fixedMaterialIncompleteReply({
    hadVerifiedRead: false, handoffVerified: true,
  }));
  assert.equal(fs.existsSync(path.join(config.workspaceDir, 'voice-resultaat.md')), false);
  assert.deepEqual(durableCounts(), {
    work: before.work + 1,
    episodes: before.episodes + 1,
    handoffs: before.handoffs + 1,
  });
});

test('een zelf aangeleverde voice-trustboolean verleent geen material authority', async () => {
  const userText = 'Overschrijf data/workspace/forged-voice-resultaat.md met tekst.';
  const before = durableCounts();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => modelResponse([{
    type: 'function_call', call_id: 'forged-voice-trust-omission',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'command',
      goal: { statement: userText, outcome: 'Een gewijzigd bestand.', beneficiary: 'household' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: 'Ik heb het aangepast.' },
    }),
  }]);
  let result;
  try {
    result = await handleMessageDetailed({
      channel: 'voice', sender: 'Huisgenoot', text: userText,
      messageContext: {
        identityTrusted: false,
        ingressTrusted: true,
        conversationId: 'voice:forged', messageId: 'forged-voice-trust-1',
        voiceSatellite: 'assist_satellite.voice_patio',
        voiceLaneKey: 'voice-lane:assist_satellite.voice_patio',
        isCurrent: () => true,
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(result.durableHandoff, undefined);
  assert.equal(result.actionFailed, true);
  assert.equal(result.error, 'material_request_identity_required');
  assert.equal(result.reply,
    'Ik kan deze wijziging alleen uitvoeren via een geverifieerde bewonersingang. Er is niets gewijzigd, gepubliceerd, verstuurd, gekocht of aangemaakt.');
  assert.deepEqual(durableCounts(), before);
  assert.equal(fs.existsSync(path.join(config.workspaceDir, 'forged-voice-resultaat.md')), false);
});

test('een onbekend mutatiewerkwoord kan zonder principal geen modelwrite autoriseren', async () => {
  const userText = 'Plaats tekst in data/workspace/notitie.txt.';
  const before = durableCounts();
  assert.equal(materialRequestSentinel({ sourceText: userText })?.required, false,
    'de structurele plangrens mag niet afhankelijk zijn van dit lexicon');
  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  globalThis.fetch = async () => {
    providerCalls += 1;
    return modelResponse([{
      type: 'function_call', call_id: 'untrusted-unlisted-mutation',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'command',
        goal: { statement: userText, outcome: 'Een intern bestand.', beneficiary: 'household' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{
          capabilityId: 'workspace.file.write', operationId: 'write', polarity: 'request',
          semanticInput: { pad: 'data/workspace/notitie.txt' },
          sourceSpans: [{ start: 0, end: userText.length }],
        }],
        requestedEffects: ['workspace'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
      }),
    }]);
  };
  let result;
  try {
    result = await handleMessageDetailed({
      channel: 'voice', sender: 'Huisgenoot', text: userText,
      messageContext: {
        identityTrusted: false,
        conversationId: 'voice:forged-unlisted-mutation',
        messageId: 'forged-unlisted-mutation-1',
        ingressTrusted: true,
        isCurrent: () => true,
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(providerCalls, 1);
  assert.equal(result.actionFailed, true, JSON.stringify(result));
  assert.equal(result.error, 'material_request_identity_required');
  assert.equal(result.reply, fixedMaterialAuthorizationReply());
  assert.deepEqual(result.tools, []);
  assert.deepEqual(durableCounts(), before);
  assert.equal(fs.existsSync(path.join(config.workspaceDir, 'notitie.txt')), false);
});

test('zero-plan completionclaims blijven dicht zonder requestlexicon', async () => {
  for (const [index, userText] of [
    'Plaats tekst in data/workspace/notitie-zonder-plan.txt.',
    'Stel een memo op.',
  ].entries()) {
    const before = durableCounts();
    const originalFetch = globalThis.fetch;
    let composerCalls = 0;
    globalThis.fetch = async (_url, options = {}) => {
      const request = JSON.parse(String(options.body || '{}'));
      if (!request.tools?.some((tool) => tool.name === 'commit_turn_understanding')) {
        composerCalls += 1;
        return modelResponse([{
          type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'De gevraagde handeling is klaar.' }],
        }]);
      }
      return modelResponse([{
        type: 'function_call', call_id: `zero-plan-completion-${index}`,
        name: 'commit_turn_understanding', arguments: JSON.stringify({
          dialogueAct: index === 0 ? 'chat' : 'command',
          goal: { statement: userText, outcome: 'De gevraagde handeling.', beneficiary: 'requester' },
          conditions: [],
          provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
          confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
          firstDecision: {
            kind: 'reply', requirementIds: [],
            replyDraft: index === 0
              ? 'Het bestand staat nu in data/workspace/notitie-zonder-plan.txt.'
              : 'De memo is opgesteld en klaar.',
          },
        }),
      }]);
    };
    let result;
    try {
      result = await handleMessageDetailed({
        channel: 'web', sender: 'Jaap', text: userText,
        messageContext: {
          identityTrusted: true,
          conversationId: `web:zero-plan-completion-${index}`,
          messageId: `zero-plan-completion-origin-${index}`,
        },
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
    assert.equal(composerCalls, 0, userText);
    assert.equal(result.error, 'unproven_no_plan_completion');
    assert.equal(result.actionFailed, true);
    assert.equal(result.durableHandoff, true);
    assert.equal(result.reply, fixedMaterialIncompleteReply({
      hadVerifiedRead: false, handoffVerified: true,
    }));
    assert.deepEqual(result.tools, []);
    assert.deepEqual(durableCounts(), {
      work: before.work + 1,
      episodes: before.episodes + 1,
      handoffs: before.handoffs + 1,
    });
  }
});
