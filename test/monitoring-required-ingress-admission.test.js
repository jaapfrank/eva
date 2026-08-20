import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-monitoring-required-admission-');
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_RUNTIME_MODE = 'normal';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.ALLOW_HOME_CONTROL = 'true';
process.env.OPENAI_API_KEY = 'test-only';
process.env.EVA_VOICE_ASSIST_SATELLITES =
  'woonkamer=assist_satellite.voice_living_room';
process.env.EVA_VOICE_ASSIST_SATELLITE_DEFAULT = 'woonkamer';
await import('./voicebridge-test-composition.js');
await import('./homeassistant-test-composition.js');
await import('./agent-test-composition.js');

const { config } = await import('../src/config.js');
const { db } = await import('../src/db.js');
const { handleMessageDetailed } = await import('../src/mind/agent.js');
const { __voiceBridgeTest } = await import('../src/mind/voicebridge.js');
const { beginHttpIngress, releaseHttpIngressAuthority } = await import('../src/web/httpingress.js');

const REQUEST = 'geef aan wanneer ik de ramen moet openen als het buiten kouder is dan binnen.';

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completedModelResponse(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function requiredMonitoringUnderstanding(text) {
  const conditionStart = text.indexOf('wanneer');
  const outsideStart = text.indexOf('het buiten');
  const insideStart = text.lastIndexOf('binnen');
  return {
    dialogueAct: 'command',
    goal: {
      statement: 'Bewaak de verhouding tussen buiten- en binnentemperatuur.',
      outcome: 'De requester krijgt bericht zodra het buiten kouder is dan binnen.',
      beneficiary: 'requester',
    },
    conditions: [{
      statement: 'Buiten is kouder dan binnen.', role: 'guard', state: 'required',
      sourceSpans: [{ start: conditionStart, end: text.length - 1 }],
      requirementIds: [], afterRequirementIds: [],
    }],
    provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
    confidence: 0.99,
    actions: [{
      capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
      semanticInput: { query: 'actuele buiten- en binnentemperatuur met exacte entity-id' },
      sourceSpans: [{ start: outsideStart, end: text.length - 1 }],
    }, {
      capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
      semanticInput: { entityId: '<buitentemperatuur_resultaat>' },
      sourceSpans: [{ start: outsideStart, end: text.indexOf(' kouder') }],
    }, {
      capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
      semanticInput: { entityId: '<binnentemperatuur_resultaat>' },
      sourceSpans: [{ start: insideStart, end: text.length - 1 }],
    }, {
      capabilityId: 'work.episode.manage', operationId: 'start', polarity: 'request',
      semanticInput: {
        titel: 'Buiten kouder dan binnen',
        doel: 'Vergelijk verse buiten- en binnentemperaturen.',
        volgende_stap: 'Lees beide temperaturen opnieuw.',
        definitie_klaar: 'De eerste geverifieerde koudere-buitenmeting is bezorgd.',
        fase: 'Wacht op de volgende meting.',
        hervatten_na: new Date(Date.now() + 5 * 60_000).toISOString(),
        afleveren_aan: 'requester', toestand_json: '{}',
      },
      sourceSpans: [{ start: 0, end: text.length }],
    }],
    requestedEffects: ['read', 'persistent'], deniedEffects: [],
    temporal: { mode: 'now', startSourceSpans: [] }, durability: 'episode',
    delivery: { mode: 'later', target: 'requester' },
    proofNeeds: [], ambiguities: [],
    firstDecision: {
      kind: 'schedule', requirementIds: ['r1', 'r2', 'r3', 'r4'],
      replyDraft: 'Ik geef het door zodra het buiten kouder is dan binnen.',
    },
  };
}

async function rejectedRequiredMonitoringTurn({ channel, sender, messageContext }) {
  const originalFetch = globalThis.fetch;
  let understandingCalls = 0;
  let homeCalls = 0;
  let otherModelCalls = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).startsWith(config.haUrl)) {
      homeCalls += 1;
      throw new Error('required monitoring mag Home Assistant niet bereiken');
    }
    const body = JSON.parse(String(options.body || '{}'));
    if (body.tools?.[0]?.name === 'commit_turn_understanding') {
      understandingCalls += 1;
      return completedModelResponse([{
        type: 'function_call', call_id: `required-${channel}-understanding`,
        name: 'commit_turn_understanding',
        arguments: JSON.stringify(requiredMonitoringUnderstanding(REQUEST)),
      }]);
    }
    otherModelCalls += 1;
    throw new Error(`onverwachte modelroute na monitoringadmission: ${body.tools?.[0]?.name || 'free'}`);
  };
  const before = {
    work: db.prepare('SELECT count(*) AS n FROM eva_work_items').get().n,
    episodes: db.prepare('SELECT count(*) AS n FROM eva_goal_episodes').get().n,
  };
  try {
    const result = await handleMessageDetailed({
      channel, sender, text: REQUEST, messageContext,
    });
    assert.equal(result.actionFailed, true);
    assert.match(result.reply, /niet veilig.*niets ingepland/iu);
    assert.deepEqual(result.tools, []);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(understandingCalls, 1);
  assert.equal(homeCalls, 0);
  assert.equal(otherModelCalls, 0);
  assert.equal(db.prepare('SELECT count(*) AS n FROM eva_work_items').get().n, before.work);
  assert.equal(db.prepare('SELECT count(*) AS n FROM eva_goal_episodes').get().n, before.episodes);
  assert.equal(db.prepare(`SELECT count(*) AS n FROM turn_tool_executions
    WHERE tool IN ('huis_status','ha_lees','werk_bijhouden','stuur_bericht')`).get().n, 0);
}

test('required voice-monitoring faalt vóór HA-read, work, wake en aflevering', async () => {
  const conversationId = 'voice:monitoring-required';
  const messageId = 'voice-monitoring-required-1';
  const sender = 'Huisgenoot';
  const authority = __voiceBridgeTest.issueSyntheticUnderstandingContext({
    messageId, satellite: 'assist_satellite.voice_living_room',
    conversationId, requester: sender, text: REQUEST,
  });
  await rejectedRequiredMonitoringTurn({
    channel: 'voice', sender,
    messageContext: { ...authority, conversationId, messageId },
  });
});

test('required web-monitoring faalt vóór HA-read, work, wake en aflevering', async () => {
  const conversationId = 'web:monitoring-required';
  const messageId = 'web-monitoring-required-1';
  const publicKey = generateKeyPairSync('ec', { namedCurve: 'P-256' })
    .publicKey.export({ format: 'jwk' });
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Jaap',
    rawText: REQUEST, conversationId, syntheticTest: false,
    clientAckPublicKey: {
      contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
      kty: 'EC', crv: 'P-256', x: publicKey.x, y: publicKey.y,
    },
  };
  const ingress = beginHttpIngress({
    channel: 'web', conversationId, requestId: messageId, intent,
  });
  assert.equal(ingress.kind, 'started');
  try {
    await rejectedRequiredMonitoringTurn({
      channel: 'web', sender: 'Jaap',
      messageContext: {
        conversationId, messageId, identityTrusted: true,
        httpIngressAuthority: ingress.preparationAuthority,
        httpIngressIntent: intent,
      },
    });
  } finally {
    releaseHttpIngressAuthority(ingress.preparationAuthority);
  }
});
