import './isolated-data-dir.js';

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  archiveTelegramMessage,
  claimTelegramMessage,
  failTelegramMessage,
  recordTurnToolObservation,
  searchTelegramMessages,
} from '../src/db.js';
import { createDurableWork } from '../src/mind/portfolio.js';
import { registerRuntimePrincipalValidator } from '../src/mind/runtimecontroller.js';
import { makeExecutor } from '../src/mind/tools.js';

const ATTESTED_SCHEDULER_CONTEXTS = new WeakSet();
registerRuntimePrincipalValidator('autonomy', ({ messageContext }) => (
  ATTESTED_SCHEDULER_CONTEXTS.has(messageContext) ? 'system:eva-autonomy' : null
));

let sequence = 0;

function archive({
  chatId, messageId, ts = Date.now(), sender = 'Jaap', role = 'user',
  content, processingStatus = role === 'user' ? 'failed' : 'completed', kind = 'text',
  mediaRef = null,
}) {
  return archiveTelegramMessage({
    chatId, messageId, ts, sender, role, kind, content, processingStatus, mediaRef,
  });
}

function parsedAction(output) {
  const parsed = JSON.parse(output);
  assert.equal(parsed.contract, 'ActionResult/v1');
  return parsed;
}

function boundAutonomyExecutor({ chatId, messageId, requester = 'Jaap' }) {
  sequence += 1;
  const { workItem, episode } = createDurableWork({
    source: 'telegram', owner: requester,
    title: `Herstel bewonerslus ${sequence}`,
    goal: 'Beantwoord precies de nog open bewonerslus uit de gebonden Telegramchat.',
    nextStep: 'Lees alleen de gebonden chat en herstel de open lus.',
    definitionOfDone: 'De oorspronkelijke verzoeker krijgt in dezelfde chat een bruikbaar antwoord.',
    visibleToFamily: true,
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: `telegram:${chatId}`, originMessageId: String(messageId),
    requester, deliveryPolicy: 'required', deliveryTarget: requester,
  });
  const messageContext = Object.freeze({
    reason: 'Host koos exact één duurzaam gebonden bewonerslus.',
    interactionEpoch: 0,
    pendingSignals: Object.freeze([]),
    attention: null,
    workId: Number(workItem.id),
    goalEpisodeId: Number(episode.id),
  });
  ATTESTED_SCHEDULER_CONTEXTS.add(messageContext);
  const turnId = `conversation-opportunity-${sequence}`;
  return {
    workItem,
    episode,
    messageContext,
    turnId,
    execute: makeExecutor({
      mode: 'autonomy', asker: 'Eva', turnId,
      activeWorkId: workItem.id, activeGoalEpisodeId: episode.id,
      messageContext,
    }),
  };
}

async function executeCommitted(bound, args) {
  const output = await bound.execute('zoek_gesprek', args);
  const parsed = parsedAction(output);
  if (parsed.ok && parsed.verified && !parsed.partial && !parsed.needs_followup) {
    recordTurnToolObservation({
      turnId: bound.turnId,
      tool: 'zoek_gesprek',
      args,
      output,
    });
  }
  return { output, parsed };
}

test('host-bound hybrid zet verse mislukte bewonerslus vóór oude Eva-proza zonder chatlek', async () => {
  const chatId = 'resident-chat';
  const otherChat = 'other-private-chat';
  const query = 'reflectie gezinsverbinding gesprek autonomie';
  const now = Date.now();
  archive({
    chatId, messageId: '100', ts: now - 21 * 24 * 3600_000,
    sender: 'Eva', role: 'assistant', processingStatus: 'completed',
    content: 'Mijn reflectie op gezinsverbinding, gesprek en autonomie is een lange beschouwing. '
      + 'Reflectie, gezinsverbinding, gesprek en autonomie verdienen verdere reflectie.',
  });
  archive({
    chatId, messageId: '101', ts: now - 20 * 24 * 3600_000,
    sender: 'Eva', role: 'assistant', processingStatus: 'completed',
    content: 'Nog een reflectie over gezinsverbinding, gesprek en autonomie, met veel oude woorden.',
  });
  for (let index = 0; index < 5; index += 1) {
    archive({
      chatId, messageId: `10${index + 3}`, ts: now - (90_000 - index * 1_000),
      content: `Los recent bewonersbericht ${index + 1} zonder de gezochte oude termen.`,
    });
  }
  archive({
    chatId, messageId: '109', ts: now - 30_000,
    content: 'Eva, dit sloeg nergens op. Het licht ging wel uit en je antwoord niet.',
  });
  archive({
    chatId: otherChat, messageId: '900', ts: now - 10_000, sender: 'Mila',
    content: 'PRIVE_CROSS_CHAT reflectie gezinsverbinding gesprek autonomie.',
  });
  const bound = boundAutonomyExecutor({ chatId, messageId: '109' });
  const args = { vraag: query, limiet: 5 };
  const { parsed: first } = await executeCommitted(bound, args);

  assert.equal(first.ok, true, JSON.stringify(first));
  assert.equal(first.data.messages[0].message_id, '109');
  assert.deepEqual({
    ...first.data.messages[0].search_metadata,
    row_revision: undefined,
  }, {
    lane: 'resident_open_loop', role: 'user',
    processing_status: 'failed', recency_rank: 1, row_revision: undefined,
  });
  assert.match(first.data.messages[0].search_metadata.row_revision, /^[a-f0-9]{64}$/u);
  assert.equal(first.data.source_binding.conversation_id, `telegram:${chatId}`);
  assert.equal(first.data.messages.some((message) => (
    message.chat_id === otherChat || message.content.includes('PRIVE_CROSS_CHAT')
  )), false);
  assert.equal(first.data.messages.some((message) => (
    message.role === 'assistant' && message.search_metadata.lane === 'lexical'
  )), true, 'de bewonerslane mag de lexicale lane niet verdringen');

  const second = parsedAction(await bound.execute('zoek_gesprek', args));
  assert.equal(second.ok, false, JSON.stringify(second));
  assert.equal(second.failure_class, 'no_new_data');
  assert.equal(second.data.suppressed, true);

  archive({
    chatId, messageId: '110', ts: now,
    content: 'En nu reageer je weer niet op wat ik net zei.',
  });
  const third = parsedAction(await bound.execute('zoek_gesprek', args));
  assert.equal(third.ok, true, JSON.stringify(third));
  assert.equal(third.data.messages[0].message_id, '110');

  const ordinary = searchTelegramMessages(query, { chatId, limit: 5 });
  assert.equal(ordinary[0].role, 'assistant');
  assert.equal(ordinary.some((message) => message.chat_id === otherChat), false);
  assert.equal(ordinary.some((message) => Object.hasOwn(message, 'search_metadata')), false);
});

test('vrije of caller-gespoofte schedulercontext faalt vóór inhoud uit twee privéchats', async () => {
  archive({
    chatId: 'private-a', messageId: '1', sender: 'Jaap',
    content: 'PRIVATE_A open gesprek over de achterdeur.',
  });
  archive({
    chatId: 'private-b', messageId: '1', sender: 'Mila',
    content: 'PRIVATE_B open gesprek over de voordeur.',
  });
  const freeContext = Object.freeze({
    reason: 'Vrije schedulerbeurt zonder duurzame inboundbinding.', interactionEpoch: 0,
    pendingSignals: Object.freeze([]), attention: null, workId: null, goalEpisodeId: null,
  });
  ATTESTED_SCHEDULER_CONTEXTS.add(freeContext);
  const freeOutput = await makeExecutor({
    mode: 'autonomy', asker: 'Eva', messageContext: freeContext,
  })('zoek_gesprek', { vraag: 'open gesprek deur', limiet: 8 });
  const free = parsedAction(freeOutput);
  assert.equal(free.ok, false, JSON.stringify(free));
  assert.equal(free.failure_class, 'conversation_scope_unbound');
  assert.doesNotMatch(freeOutput, /PRIVATE_A|PRIVATE_B/u);

  const spoofedOutput = await makeExecutor({
    mode: 'autonomy', asker: 'Eva',
    messageContext: { chatId: 'private-a', identityTrusted: true },
  })('zoek_gesprek', { vraag: 'open gesprek deur', limiet: 8 });
  const spoofed = parsedAction(spoofedOutput);
  assert.equal(spoofed.ok, false, JSON.stringify(spoofed));
  assert.equal(spoofed.failure_class, 'conversation_scope_unbound');
  assert.doesNotMatch(spoofedOutput, /PRIVATE_A|PRIVATE_B/u);
});

test('open en dicht delen nooit dezelfde canonieke zoekcursor', async () => {
  const chatId = 'query-spec-chat';
  archive({ chatId, messageId: '1', content: 'De deurvraag bleef onbeantwoord.' });
  const bound = boundAutonomyExecutor({ chatId, messageId: '1' });
  const openArgs = { vraag: 'deur open', limiet: 4 };
  const closedArgs = { vraag: 'deur dicht', limiet: 4 };
  const open = await executeCommitted(bound, openArgs);
  assert.equal(open.parsed.ok, true);
  const closed = parsedAction(await bound.execute('zoek_gesprek', closedArgs));
  assert.equal(closed.ok, true, JSON.stringify(closed));
  assert.notEqual(closed.data.search_spec_sha256,
    open.parsed.data.search_spec_sha256);
});

test('effectieve limiet heeft een eigen canonieke cursor en 1 → 6 → 1 blijft begrensd', async () => {
  const chatId = 'limit-spec-chat';
  for (let index = 1; index <= 6; index += 1) {
    archive({
      chatId, messageId: String(index), ts: Date.now() - (6 - index) * 1_000,
      content: `Open lampvraag ${index} wacht nog op een antwoord.`,
    });
  }
  const bound = boundAutonomyExecutor({ chatId, messageId: '6' });
  const narrowArgs = { vraag: 'lampvraag antwoord', limiet: 1 };
  const wideArgs = { vraag: 'lampvraag antwoord', limiet: 6 };
  const narrow = await executeCommitted(bound, narrowArgs);
  assert.equal(narrow.parsed.ok, true);
  assert.equal(narrow.parsed.data.messages.length, 1);
  const wide = await executeCommitted(bound, wideArgs);
  assert.equal(wide.parsed.ok, true);
  assert.equal(wide.parsed.data.messages.length, 6);

  const repeatedNarrow = parsedAction(await bound.execute('zoek_gesprek', narrowArgs));
  assert.equal(repeatedNarrow.ok, false, JSON.stringify(repeatedNarrow));
  assert.equal(repeatedNarrow.failure_class, 'no_new_data');
  assert.equal(repeatedNarrow.data.suppressed, true);
  assert.notEqual(narrow.parsed.data.search_spec_sha256,
    wide.parsed.data.search_spec_sha256);
});

test('statuswijziging op dezelfde Telegramrow opent de relevante fingerprint opnieuw', async () => {
  const chatId = 'same-row-status-chat';
  archive({
    chatId, messageId: '1', processingStatus: 'received',
    content: 'Mijn antwoord op de lampvraag ontbreekt nog.',
  });
  const bound = boundAutonomyExecutor({ chatId, messageId: '1' });
  const args = { vraag: 'lampvraag antwoord', limiet: 4 };
  const first = await executeCommitted(bound, args);
  assert.equal(first.parsed.ok, true);
  assert.equal(claimTelegramMessage(chatId, '1').claimed, true);
  assert.equal(failTelegramMessage(chatId, '1', 'provider viel uit'), true);

  const changed = parsedAction(await bound.execute('zoek_gesprek', args));
  assert.equal(changed.ok, true, JSON.stringify(changed));
  assert.equal(changed.data.messages[0].processing_status, 'failed');
  assert.notEqual(changed.data.opportunity_fingerprint,
    first.parsed.data.opportunity_fingerprint);
});

test('een nieuwe irrelevante assistentrow opent een verbruikte bewonerslus niet', async () => {
  const chatId = 'irrelevant-assistant-chat';
  archive({ chatId, messageId: '1', content: 'Waarom kreeg mijn lampvraag geen antwoord?' });
  const bound = boundAutonomyExecutor({ chatId, messageId: '1' });
  const args = { vraag: 'lampvraag antwoord', limiet: 4 };
  const first = await executeCommitted(bound, args);
  assert.equal(first.parsed.ok, true);
  archive({
    chatId, messageId: '2', sender: 'Eva', role: 'assistant',
    processingStatus: 'completed', content: 'Een los bericht over boodschappen.',
  });
  const repeated = parsedAction(await bound.execute('zoek_gesprek', args));
  assert.equal(repeated.ok, false, JSON.stringify(repeated));
  assert.equal(repeated.failure_class, 'no_new_data');
  assert.equal(repeated.data.suppressed, true);
});

test('prepare zonder durable receipt wordt met hetzelfde token herhaald en pas bij ledgercommit verbruikt', async () => {
  const chatId = 'prepare-replay-chat';
  archive({ chatId, messageId: '1', content: 'Deze bewonerslus moet na een crash terugkomen.' });
  const bound = boundAutonomyExecutor({ chatId, messageId: '1' });
  const args = { vraag: 'bewonerslus crash', limiet: 4 };
  const firstOutput = await bound.execute('zoek_gesprek', args);
  const first = parsedAction(firstOutput);
  assert.equal(first.ok, true, JSON.stringify(first));

  const replayOutput = await bound.execute('zoek_gesprek', args);
  const replay = parsedAction(replayOutput);
  assert.equal(replay.ok, true, JSON.stringify(replay));
  assert.equal(replay.data.opportunity_cursor.replay, true);
  assert.equal(replay.data.opportunity_cursor.token,
    first.data.opportunity_cursor.token);

  recordTurnToolObservation({
    turnId: bound.turnId, tool: 'zoek_gesprek', args, output: replayOutput,
  });
  const committed = parsedAction(await bound.execute('zoek_gesprek', args));
  assert.equal(committed.ok, false, JSON.stringify(committed));
  assert.equal(committed.failure_class, 'no_new_data');
});

test('mediazware hybrid output blijft volledig en onder de 7k commitgrens', async () => {
  const chatId = 'bounded-media-chat';
  for (let index = 1; index <= 20; index += 1) {
    archive({
      chatId, messageId: String(index), ts: Date.now() - (20 - index) * 1_000,
      kind: 'photo', mediaRef: `media-${index}-${'x'.repeat(2_000)}`,
      content: `Lange open bewonersvraag ${index}: ${'inhoud '.repeat(2_500)}`,
    });
  }
  const bound = boundAutonomyExecutor({ chatId, messageId: '20' });
  const args = { vraag: 'lange open bewonersvraag', limiet: 50 };
  const output = await bound.execute('zoek_gesprek', args);
  const result = parsedAction(output);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.partial, false, JSON.stringify(result));
  assert.ok(Buffer.byteLength(output, 'utf8') < 7_000, Buffer.byteLength(output, 'utf8'));
  assert.ok(result.data.messages.length <= 6);
  assert.ok(result.data.messages.every((message) => message.content.length <= 360));
  assert.ok(result.data.messages.every((message) => !message.media_ref
    || message.media_ref.length <= 120));
  assert.equal(result.data.truncated, undefined);
});
