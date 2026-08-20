import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-relationship-ingress-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';

const { db, upsertPerson } = await import('../src/db.js');
const { handleMessageDetailed } = await import('../src/mind/agent.js');
const { householdContextText, setPersonHouseholdRole } = await import('../src/mind/household.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { headers: { 'content-type': 'text/event-stream' } });
}

function textResponse(text) {
  return completedModelResponse([{
    type: 'message', role: 'assistant', content: [{ type: 'output_text', text }],
  }]);
}

function replyUnderstanding(userText, replyDraft = 'Dat neem ik mee.') {
  return completedModelResponse([{
    type: 'function_call', call_id: 'relationship-feedback-understanding',
    name: 'commit_turn_understanding',
    arguments: JSON.stringify({
      dialogueAct: 'continue',
      goal: {
        statement: userText, outcome: 'Reageer op dezelfde draad.', beneficiary: 'requester',
      },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: userText.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft },
    }),
  }]);
}

async function runFeedbackTurn({
  sender, text, identityTrusted = true, syntheticTest = false,
}) {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return calls === 1
      ? replyUnderstanding(text)
      : textResponse('Dat neem ik mee. Liever een eigen oordeel dan keurige beige soep.');
  };
  try {
    const turn = await handleMessageDetailed({
      channel: 'web', sender, text,
      messageContext: {
        conversationId: `web:relationship:${sender.toLowerCase()}`,
        identityTrusted, syntheticTest, isCurrent: () => true,
      },
    });
    assert.ok(calls >= 2 && calls <= 3, `onverwacht aantal providerfasen: ${calls}`);
    return turn;
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test('alleen vertrouwde echte feedback wordt één abstracte, promptcausale karakterles', async () => {
  upsertPerson('Jaap');
  setPersonHouseholdRole('Jaap', 'household_member');
  upsertPerson('Ada');
  setPersonHouseholdRole('Ada', 'household_member');

  const praise = 'Laatst kwam een pizzabezorger en toen begon ze zelf door de deurbel te praten. Dat was echt super slim van haar.';
  await runFeedbackTurn({ sender: 'Jaap', text: praise });

  const rows = db.prepare(`SELECT rf.* FROM relationship_feedback rf
    JOIN people p ON p.id=rf.person_id WHERE lower(p.name)=lower(?) ORDER BY rf.id`).all('Jaap');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kind, 'appreciation');
  assert.match(rows[0].topic, /situationeel initiatief/);
  assert.match(rows[0].summary, /actuele context zelf verbindt/);
  assert.doesNotMatch(rows[0].summary, /pizza|deurbel/i);

  const nextContext = householdContextText({
    subject: 'Jaap', includeAllMemberDetails: true, query: 'Wat speelt er?',
  });
  assert.match(nextContext, /Recente gedragscontext/);
  assert.match(nextContext, /situationeel initiatief/);
  assert.match(nextContext, /geen opdracht of bevoegdheid/);
  assert.doesNotMatch(nextContext, /pizza|deurbel/i);

  await runFeedbackTurn({ sender: 'Ada', text: praise, syntheticTest: true });
  await runFeedbackTurn({ sender: 'Onbekend', text: praise, identityTrusted: false });
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM relationship_feedback rf
    JOIN people p ON p.id=rf.person_id WHERE lower(p.name) IN ('ada','onbekend')`).get().count, 0);
});

test('één zin kan hoogstens één duurzame correctieles schrijven', async () => {
  const before = db.prepare('SELECT COUNT(*) AS count FROM relationship_feedback').get().count;
  await runFeedbackTurn({ sender: 'Jaap', text: 'Alweer.' });
  const after = db.prepare('SELECT COUNT(*) AS count FROM relationship_feedback').get().count;
  assert.equal(after, before + 1);
});

test('directe positieve vergelijking wordt geen correctieles', async () => {
  const before = Number(db.prepare('SELECT COALESCE(MAX(id),0) AS id FROM relationship_feedback')
    .get().id);
  await runFeedbackTurn({
    sender: 'Jaap', text: 'Dit antwoord klinkt veel natuurlijker.',
  });
  const row = db.prepare('SELECT * FROM relationship_feedback WHERE id>? ORDER BY id DESC LIMIT 1')
    .get(before);
  assert.equal(row.kind, 'appreciation');
  assert.equal(row.topic, 'direct antwoordresultaat');
});
