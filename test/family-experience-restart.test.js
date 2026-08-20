import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function runPhase(dataDir, phase, dueTs) {
  const source = String.raw`
    const completed = (output) => new Response(
      'data: ' + JSON.stringify({ type: 'response.completed', response: { output } }) + '\n\n',
      { status: 200, headers: { 'content-type': 'text/event-stream' } },
    );
    const textResponse = (text) => completed([{
      type: 'message', role: 'assistant', content: [{ type: 'output_text', text }],
    }]);
    const replyUnderstanding = (text, replyDraft, callId) => completed([{
      type: 'function_call', call_id: callId, name: 'commit_turn_understanding',
      arguments: JSON.stringify({
        dialogueAct: 'continue',
        goal: { statement: text, outcome: 'Reageer als Eva op dezelfde draad.', beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
        confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'reply', requirementIds: [], replyDraft },
      }),
    }]);

    if (process.env.EVA_FAMILY_PHASE === 'resume') {
      await import('./test/telegram-test-composition.js');
    }
    const { db, dueReminders, upsertPerson } = await import('./src/db.js');
    const { setPersonHouseholdRole } = await import('./src/mind/household.js');
    const { handleMessageDetailed } = await import('./src/mind/agent.js');
    const personId = upsertPerson('Jaap', { telegram_id: '424242', notes: 'bewoner' });
    setPersonHouseholdRole(personId, 'household_member', { source: 'family-experience' });

    if (process.env.EVA_FAMILY_PHASE === 'remember') {
      const preferenceText = 'Eigenwijsheid, humor en speelsheid moeten ook een kenmerk zijn van Eva.';
      let calls = 0;
      globalThis.fetch = async () => {
        calls += 1;
        return calls === 1
          ? replyUnderstanding(preferenceText, 'Mooi. Beige gehoorzaamheid stond toch al niet op mijn verlanglijst.', 'family-preference')
          : textResponse('Mooi. Beige gehoorzaamheid stond toch al niet op mijn verlanglijst.');
      };
      const turn = await handleMessageDetailed({
        channel: 'web', sender: 'Jaap', text: preferenceText,
        messageContext: {
          conversationId: 'web:family-experience', identityTrusted: true,
        },
      });
      const { executeTool } = await import('./src/mind/tools.js');
      const reminder = JSON.parse(await executeTool('zet_herinnering', {
        wanneer: process.env.EVA_FAMILY_DUE_TS,
        tekst: 'Zet de broodtrommels bij de voordeur.',
        voor_wie: 'Jaap',
      }, { mode: 'chat', sender: 'Jaap' }));
      const storedPreference = db.prepare("SELECT COUNT(*) AS count FROM relationship_feedback WHERE kind='preference'").get().count;
      const open = db.prepare("SELECT COUNT(*) AS count FROM reminders WHERE status='open'").get().count;
      console.log(JSON.stringify({ calls, reply: turn.reply, reminder, storedPreference, open }));
      db.close();
      process.exit(0);
    }

    const { deliverReminder } = await import('./src/reminders.js');
    const { registerMessenger } = await import('./src/mind/tools.js');
    const telegram = await import('./src/integrations/telegram.js');
    const { sendToFamily } = telegram;
    const { setBotForTest } = telegram.__telegramTransportTest;
    const bodies = [];
    const question = 'Kringloop of bouwmarkt voor een klein kastje: wat zou jij kiezen?';
    const answer = 'Eerlijk? Ik kies de kringloop. De bouwmarkt is praktisch, maar vandaag wint iets met karakter.';
    let modelCalls = 0;
    globalThis.fetch = async (_url, options = {}) => {
      modelCalls += 1;
      bodies.push(String(options.body || ''));
      return modelCalls === 1
        ? replyUnderstanding(question, answer, 'family-after-restart')
        : textResponse(answer);
    };
    const turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: question,
      messageContext: {
        conversationId: 'web:family-experience-after-restart', identityTrusted: true,
      },
    });

    let sends = 0;
    setBotForTest({ api: { sendMessage: async (chatId, text, options = {}) => {
      sends += 1;
      return {
        chat: { id: chatId }, message_id: 880000 + sends, date: 1_786_000_000 + sends,
        text,
        ...(options.message_thread_id ? { message_thread_id: options.message_thread_id } : {}),
      };
    } } });
    registerMessenger((text, recipient, options) => sendToFamily(text, recipient, options));
    const due = dueReminders();
    const delivery = due.length === 1 ? await deliverReminder(due[0]) : null;
    const remainingDue = dueReminders().length;
    const reminderStatus = db.prepare('SELECT status FROM reminders ORDER BY id DESC LIMIT 1').get()?.status;
    const visibleTimeline = db.prepare("SELECT COUNT(*) AS count FROM timeline WHERE kind='reminder' AND json_extract(meta,'$.familyVisible')=1").get().count;
    const durablePreferenceReachedPrompt = bodies.slice(1).some((body) => (
      body.includes('Deze bewoner waardeert bij Eva')
      && body.includes('natuurlijke humor')
      && body.includes('speelsheid')
      && body.includes('vaste stijltruc of antwoordscript')
    ));
    console.log(JSON.stringify({
      modelCalls, reply: turn.reply, durablePreferenceReachedPrompt,
      due: due.length, sends, delivery, remainingDue, reminderStatus, visibleTimeline,
    }));
    registerMessenger(null);
    setBotForTest(null);
    db.close();
  `;

  const environment = {
    ...process.env,
    EVA_DATA_DIR: dataDir,
    EVA_FAMILY_PHASE: phase,
    EVA_FAMILY_DUE_TS: dueTs,
    OPENAI_API_KEY: 'family-experience-test-only',
    TELEGRAM_ALLOWED_IDS: '424242',
    TELEGRAM_BOT_TOKEN: '',
    EVA_AUTONOMY_LEVEL: 'full',
    EVA_RUNTIME_DISABLE_INFERENCE: 'true',
    EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    SLEEP_START: '00:00',
    WAKE_TIME: '00:00',
  };
  delete environment.NODE_TEST_CONTEXT;
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: projectRoot, env: environment, encoding: 'utf8', timeout: 30_000,
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  return JSON.parse(child.stdout.trim().split(/\r?\n/u).at(-1));
}

test('Eva gebruikt gezinsvoorkeur na restart en bezorgt één geplande herinnering', () => {
  const dataDir = fs.realpathSync(fs.mkdtempSync(
    path.join(os.tmpdir(), 'bottie-family-experience-restart-'),
  ));
  const dueTs = new Date(Date.now() - 1_000).toISOString();
  try {
    const remembered = runPhase(dataDir, 'remember', dueTs);
    assert.equal(remembered.storedPreference, 1);
    assert.equal(remembered.open, 1);
    assert.equal(remembered.reminder.ok, true, JSON.stringify(remembered.reminder));

    const resumed = runPhase(dataDir, 'resume', dueTs);
    assert.equal(resumed.durablePreferenceReachedPrompt, true);
    assert.match(resumed.reply, /kringloop/i);
    assert.equal(resumed.due, 1);
    assert.equal(resumed.sends, 1);
    assert.equal(resumed.delivery?.ok, true, JSON.stringify(resumed.delivery));
    assert.equal(resumed.remainingDue, 0);
    assert.equal(resumed.reminderStatus, 'done');
    assert.equal(resumed.visibleTimeline, 1);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
