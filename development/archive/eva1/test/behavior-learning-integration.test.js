import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-behavior-integration-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';

const { db, upsertPerson } = await import('../src/db.js');
const {
  handleMessageDetailed, recordDeliveredReply,
} = await import('../src/mind/agent.js');
const { setPersonHouseholdRole } = await import('../src/mind/household.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const sha256 = (value) => createHash('sha256').update(String(value)).digest('hex');

function seedCanary(person, suffix) {
  upsertPerson(person);
  setPersonHouseholdRole(person, 'household_member');
  const personId = Number(db.prepare('SELECT id FROM people WHERE lower(name)=lower(?)')
    .get(person).id);
  const ts = new Date().toISOString();
  const candidateSha256 = sha256(`candidate:${person}:${suffix}`);
  const info = db.prepare(`INSERT INTO eva_behavior_experiments
    (experiment_key,created_ts,updated_ts,person_id,source_kind,source_outcome_key,
      source_evidence_sha256,gap_summary,hypothesis,motivation,lens_dimension,
      lens_direction,lens_intensity,context_scope,renderer_contract,
      expected_outcome_kind,negative_outcome_kind,candidate_sha256,status,revision,
      preflight_receipt_sha256)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'canary_pending',2,?)`).run(
    sha256(`experiment:${person}:${suffix}`), ts, ts, personId, 'self_initiated',
    sha256(`outcome:${person}:${suffix}`), sha256(`evidence:${person}:${suffix}`),
    `Het antwoord kan in deze situatie meer eigen oordeel gebruiken (${suffix}).`,
    `Een situationeel oordeel voelt natuurlijker zonder bewijs te veranderen (${suffix}).`,
    `Eva wil zelf een speelsere formulering in deze context toetsen (${suffix}).`,
    'playfulness', 'increase', 'moderate', 'any_direct_reply',
    'BehaviorLensRenderer/v1', 'direct_style_appreciation', 'direct_style_correction',
    candidateSha256, sha256(`preflight:${person}:${suffix}`),
  );
  return {
    id: Number(info.lastInsertRowid),
    promptText: 'Geef de formulering meer speelsheid; humor mag alleen als die natuurlijk past',
  };
}

function completed(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { headers: { 'content-type': 'text/event-stream' } });
}

function understandingResponse(text) {
  return completed([{
    type: 'function_call', call_id: `understanding-${sha256(text).slice(0, 8)}`,
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Geef een gewoon relationeel antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: 'Ik kijk er met je naar.' },
    }),
  }]);
}

function textResponse(text) {
  return completed([{
    type: 'message', role: 'assistant', content: [{ type: 'output_text', text }],
  }]);
}

async function chat(person, text, {
  conversationId = `web:behavior:${person.toLowerCase()}`,
  identityTrusted = true, syntheticTest = false,
  reply = 'Ik zie hier vooral ruimte voor een eerlijk eigen oordeel, zonder groter verhaal eromheen.',
  expectedComposerContext = null,
  forbiddenComposerContext = null,
} = {}) {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (_url, init = {}) => {
    requests.push(String(init.body || ''));
    return requests.length === 1 ? understandingResponse(text) : textResponse(reply);
  };
  try {
    const result = await handleMessageDetailed({
      channel: 'web', sender: person, text,
      messageContext: {
        conversationId, identityTrusted, syntheticTest, isCurrent: () => true,
      },
    });
    assert.ok(requests.length >= 2 && requests.length <= 3, requests.length);
    if (expectedComposerContext) {
      assert.doesNotMatch(requests[0], new RegExp(expectedComposerContext.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&'), 'u'));
      assert.ok(requests.slice(1).some((body) => body.includes(expectedComposerContext)));
    }
    if (forbiddenComposerContext) {
      assert.ok(requests.every((body) => !body.includes(forbiddenComposerContext)));
    }
    return result;
  } finally {
    globalThis.fetch = originalFetch;
  }
}

function experiment(id) {
  return db.prepare('SELECT * FROM eva_behavior_experiments WHERE id=?').get(id);
}

test('accepted webreply en expliciete bewonersuitkomst behouden of rollen één lens terug', async () => {
  const retainedCandidate = seedCanary('Jaap', 'retain');
  const first = await chat('Jaap', 'Hoe zou jij hier zelf naar kijken?', {
    expectedComposerContext: retainedCandidate.promptText,
  });
  assert.equal(experiment(retainedCandidate.id).status, 'canary_pending');
  assert.equal(db.prepare(`SELECT status FROM eva_behavior_canary_deliveries
    WHERE experiment_id=?`).get(retainedCandidate.id).status, 'prepared');
  assert.equal(recordDeliveredReply(first, { accepted: true, transport: 'http-test' }), true);
  assert.equal(experiment(retainedCandidate.id).status, 'canary_exposed');

  await chat('Jaap', 'Dit antwoord klinkt veel natuurlijker.');
  assert.equal(experiment(retainedCandidate.id).status, 'retained');
  assert.equal(experiment(retainedCandidate.id).outcome_class, 'helped');

  const rolledBackCandidate = seedCanary('Jaap', 'rollback');
  const second = await chat('Jaap', 'Wat valt jou nu zelf op?', {
    expectedComposerContext: rolledBackCandidate.promptText,
  });
  assert.equal(recordDeliveredReply(second, { accepted: true, transport: 'http-test' }), true);
  await chat('Jaap', 'Dit antwoord klinkt nog als een zakelijke bot.');
  assert.equal(experiment(rolledBackCandidate.id).status, 'rolled_back');
  assert.equal(experiment(rolledBackCandidate.id).outcome_class, 'harmed');
});

test('historische lof, synthetic en onvertrouwde ingress beslissen geen gedragsproef', async () => {
  const historical = seedCanary('Jaap', 'historical');
  const answer = await chat('Jaap', 'Vertel eens wat jij hiervan vindt.', {
    expectedComposerContext: historical.promptText,
  });
  recordDeliveredReply(answer, { accepted: true, transport: 'http-test' });
  await chat('Jaap', 'Laatst bij de deurbel was Eva haar reactie echt slim.');
  assert.equal(experiment(historical.id).status, 'inconclusive', JSON.stringify(
    db.prepare("SELECT content,meta FROM timeline WHERE kind='system' ORDER BY id DESC LIMIT 5").all(),
  ));
  assert.equal(experiment(historical.id).outcome_class, 'insufficient');

  const untouched = seedCanary('Mila', 'untrusted');
  await chat('Mila', 'Wat denk jij?', { syntheticTest: true });
  await chat('Mila', 'Wat denk jij nu?', { identityTrusted: false });
  assert.equal(experiment(untouched.id).status, 'canary_pending');
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM eva_behavior_canary_deliveries
    WHERE experiment_id=?`).get(untouched.id).count, 0);
});

test('retained lens is deliverygebonden en een directe stijlcorrrectie verwijdert haar vóór compositie', async () => {
  const candidate = seedCanary('Noor', 'retained-deactivation');
  const canary = await chat('Noor', 'Hoe zou jij dit zelf formuleren?', {
    expectedComposerContext: candidate.promptText,
  });
  assert.equal(recordDeliveredReply(canary, { accepted: true, transport: 'http-test' }), true);
  await chat('Noor', 'Dit antwoord klinkt veel natuurlijker.');
  assert.equal(experiment(candidate.id).status, 'retained');

  const retainedReply = await chat('Noor', 'Kun je jouw afweging kort toelichten?', {
    expectedComposerContext: 'Tijdelijk behouden relationele stijllens',
  });
  assert.equal(recordDeliveredReply(retainedReply, {
    accepted: true, transport: 'http-test',
  }), true);
  await chat('Noor', 'Dit antwoord klinkt te speels.', {
    forbiddenComposerContext: 'Tijdelijk behouden relationele stijllens',
  });
  assert.equal(experiment(candidate.id).status, 'deactivated');
  assert.equal(experiment(candidate.id).deactivation_reason, 'direct_style_correction');
});

test('een learning-DB-fout blokkeert nooit Eva haar gewone antwoord', async () => {
  const candidate = seedCanary('Sara', 'isolated-failure');
  db.exec('ALTER TABLE eva_behavior_canary_deliveries RENAME TO eva_behavior_canary_deliveries_broken');
  try {
    const result = await chat('Sara', 'Kun je hier gewoon op antwoorden?', {
      forbiddenComposerContext: 'relationele stijllens',
      reply: 'Ja. Mijn antwoord blijft beschikbaar, ook als de optionele leeradministratie hapert.',
    });
    assert.match(result.reply, /blijft beschikbaar/u);
    assert.equal(experiment(candidate.id).status, 'canary_pending');
  } finally {
    db.exec('ALTER TABLE eva_behavior_canary_deliveries_broken RENAME TO eva_behavior_canary_deliveries');
  }
});
