import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test, { after } from 'node:test';
import { GrammyError, HttpError } from 'grammy';

const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-telegram-outbox-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_RUNTIME_MODE = 'normal';
process.env.OPENAI_API_KEY = 'telegram-outbox-sidecar-test-only';
process.env.TELEGRAM_BOT_TOKEN = '';
process.env.TELEGRAM_ALLOWED_IDS = '42,501';
process.env.EVA_RUNTIME_DISABLE_INFERENCE = 'true';
process.env.EVA_RUNTIME_DISABLE_WORLD_LEARNING = 'true';
await import('./telegram-test-composition.js');

const {
  archiveTelegramMessage,
  canonicalTelegramText,
  claimTurnToolExecution,
  claimTelegramMessage,
  completeTelegramMessage,
  db,
  failTelegramMessage,
  logTimeline,
  markTelegramMessageDeliveryState,
  pendingPreparedTelegramMessages,
  telegramMessage,
  transitionTurnToolExecution,
  upsertPerson,
} = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');
const telegramModule = await import('../src/integrations/telegram.js');
const {
  deliveredReplyRecorded,
  recordDeliveredReply,
} = await import('../src/mind/agent.js');
const { turnUnderstandingAuthorityRecord } = await import('../src/turnauthority.js');
const {
  __telegramTransportTest, sendPhotoToFamily, sendToFamily, sendVoiceToFamily,
} = telegramModule;
const {
  registerMessenger,
  registerPhotoSender,
  registerVoiceSender,
  sendRegisteredMessage,
  sendRegisteredPhoto,
  sendRegisteredVoice,
  executeTool,
} = await import('../src/mind/tools.js');

function withoutExecutionTrigger(name, operation) {
  const trigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name);
  assert.ok(trigger?.sql, `missing execution trigger ${name}`);
  db.exec(`DROP TRIGGER main.${name}`);
  try { return operation(); } finally { db.exec(trigger.sql); }
}
const { manageRuntime } = await import('../src/mind/runtimecontroller.js');
const {
  createDurableWork,
  freezeEpisodeDelivery,
  goalEpisodeById,
  recordEpisodeDelivery,
  reconcileFrozenEpisodeDeliveries,
  recoverDeliveredEpisodeReceipts,
  recoverStrandedDeliveryWork,
  sealFinalDeliveryReply,
  selectPortfolioCommitment,
  transitionGoalEpisode,
} = await import('../src/mind/portfolio.js');

const {
  authenticatedTelegramMessageContext,
  prepareAuthenticatedTelegramTurnForTest,
  currentPreparedTelegramUnderstandingSource,
  deliverTelegramTurn,
  drainPreparedTelegramOutbox,
  issueTelegramDeliveryAuthority,
  preparedTelegramResult,
  persistPreparedTelegramReply,
  publishDirectTelegramReply,
  resumePreparedTelegramTurn,
  sendTelegramMedia,
  sendTelegramText,
  setBotForTest,
  telegramMediaIntent,
  telegramTextIntent,
  verifiedTelegramDeliveryAuthority,
} = __telegramTransportTest;

after(() => {
  registerMessenger(null);
  registerPhotoSender(null);
  registerVoiceSender(null);
  setBotForTest(null);
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function context(chatId, messageId, fromId = 42) {
  return {
    chat: { id: String(chatId) },
    from: { id: fromId, first_name: 'Jaap' },
    message: { message_id: String(messageId) },
  };
}

function handled(ctx, reply, extras = {}) {
  return {
    turnId: `telegram:${ctx.chat.id}:${ctx.message.message_id}`,
    reply,
    channel: 'telegram',
    sender: 'Jaap',
    conversationId: `telegram:${ctx.chat.id}`,
    originMessageId: String(ctx.message.message_id),
    tools: [],
    ...extras,
  };
}

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function understandingResponse(text, replyDraft, callId) {
  return completedModelResponse([{
    type: 'function_call', call_id: callId,
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Een exact antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft },
    }),
  }]);
}

function replyResponse(reply) {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text: reply }],
  }]);
}

async function authenticHandled(ctx, reply, extras = {}) {
  const text = `Maak het exact voorbereide antwoord voor Telegram ${ctx.chat.id}:${ctx.message.message_id}.`;
  const replyDraft = reply.length <= 4_000
    ? reply : 'Ik maak het exact voorbereide lange Telegram-antwoord.';
  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  globalThis.fetch = async (url, options = {}) => {
    providerCalls += 1;
    assert.equal(String(url), 'https://api.openai.com/v1/responses');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers?.Authorization,
      'Bearer telegram-outbox-sidecar-test-only');
    assert.equal(options.headers?.Accept, 'text/event-stream');
    const body = JSON.parse(String(options.body || 'null'));
    assert.equal(body?.stream, true);
    assert.equal(body?.store, false);
    assert.ok(canonicalJson(body.input).includes(text));
    if (providerCalls === 1) {
      assert.equal(body.tools.some((tool) => tool?.name === 'commit_turn_understanding'), true);
      return understandingResponse(text, replyDraft,
        `telegram-outbox-${ctx.chat.id}-${ctx.message.message_id}`);
    }
    if (providerCalls === 2) {
      assert.equal(body.tools.some((tool) => tool?.name === 'commit_turn_understanding'), false);
      return replyResponse(reply);
    }
    throw new Error('onverwachte derde provider-call in sidecarfixture');
  };
  let result;
  try {
    result = await prepareAuthenticatedTelegramTurnForTest({
      ctx, sender: 'Jaap', text, extras,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(providerCalls, 2);
  assert.equal(result.reply, canonicalTelegramText(reply));
  assert.equal(result.channel, 'telegram');
  assert.equal(result.conversationId, `telegram:${ctx.chat.id}`);
  assert.equal(result.originMessageId, String(ctx.message.message_id));
  return result;
}

function assertCurrentTelegramSidecar(ctx, result) {
  const identity = {
    channel: 'telegram', conversationId: `telegram:${ctx.chat.id}`,
    messageId: String(ctx.message.message_id),
  };
  const execution = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')=?
      AND json_extract(args,'$.conversationId')=?
      AND CAST(json_extract(args,'$.messageId') AS TEXT)=?`).get(
    identity.channel, identity.conversationId, identity.messageId,
  );
  const authority = turnUnderstandingAuthorityRecord(db, execution);
  const current = currentPreparedTelegramUnderstandingSource(ctx);
  const archived = telegramMessage(ctx.chat.id, ctx.message.message_id);
  assert.ok(execution && authority);
  assert.equal(execution.status, 'done');
  assert.equal(result.turnId, `telegram:${ctx.chat.id}:${ctx.message.message_id}`);
  assert.equal(authority.sourceKind, 'telegram_ingress');
  assert.equal(createHash('sha256').update(authority.sourceBodyJson).digest('hex'),
    authority.sourceSha256);
  if (archived.processing_status === 'completed') {
    assert.equal(current, null,
      'na exact episode-afleverbewijs is de processing-lease afgesloten');
  } else {
    assert.equal(archived.processing_status, 'processing');
    assert.ok(current);
    assert.equal(current.sourceRef, authority.sourceRef);
    assert.equal(current.sourceSha256, authority.sourceSha256);
    assert.equal(current.sourceBodyJson, authority.sourceBodyJson);
    assert.equal(current.sourceGeneration, authority.sourceGeneration);
  }
  assert.equal(Number(archived.processing_attempts), authority.sourceGeneration);
}

const AUTHORITY_MUTATION_TABLES = Object.freeze([
  'turn_tool_executions',
  'eva_turn_understanding_authorities_v1',
  'eva_work_items',
  'eva_goal_episodes',
  'eva_episode_origins',
  'eva_interpretation_retry_handoffs',
  'eva_execution_effect_receipts',
  'eva_delivery_receipts',
  'messages',
  'timeline',
]);

function authorityMutationSnapshot() {
  return Object.freeze(Object.fromEntries(AUTHORITY_MUTATION_TABLES.map((table) => [
    table,
    Number(db.prepare(`SELECT count(*) AS count FROM main.${table}`).get().count),
  ])));
}

function route(chatId, text, options, index = 0, count = 1) {
  return telegramTextIntent({
    chatId: String(chatId), text, wholeText: text,
    deliveryKey: options.dedupeKey, index, count, options,
  });
}

function mockTextSender(fn) {
  setBotForTest({ api: { sendMessage: async (chatId, body, options = {}) => ({
    text: body,
    ...(options.message_thread_id ? { message_thread_id: options.message_thread_id } : {}),
    ...(options.reply_parameters?.message_id ? {
      reply_to_message: { message_id: options.reply_parameters.message_id },
    } : {}),
    ...await fn(chatId, body, options),
  }) } });
}

test('2. accepted receipt vóór archief reconstrueert lokaal zonder API-call', async () => {
  const text = 'Duurzaam geaccepteerd.';
  const options = { dedupeKey: 'fault-2' };
  mockTextSender(async (chatId, body) => ({
    chat: { id: chatId }, message_id: 202, date: 1_786_000_000, text: body,
  }));
  await sendTelegramText('102', text, options);
  const attempt = route('102', text, options);
  assert.equal(db.prepare('SELECT status FROM turn_tool_executions WHERE execution_key=?')
    .get(attempt.operationKey).status, 'transport_accepted');
  db.prepare("DELETE FROM telegram_messages WHERE chat_id='102' AND message_id='202'").run();

  let calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('geen netwerkherstel'); });
  const recovered = await sendTelegramText('102', text, options);
  assert.equal(calls, 0);
  assert.equal(recovered[0]._evaRecovered, true);
  assert.equal(telegramMessage('102', '202').content, text);
  assert.equal(telegramMessage('102', '202').meta.transportAccepted, true);
});

test('3. een outbound archief zonder receipt wordt nooit tot transportbewijs witgewassen', async () => {
  const text = 'Archief is transportbewijs.';
  const options = { dedupeKey: 'fault-3' };
  const attempt = route('103', text, options);
  claimTurnToolExecution({
    executionKey: attempt.operationKey, turnId: attempt.turnId,
    tool: '@transport/telegram.sendMessage', args: attempt.intent,
    initialStatus: 'transport_sending',
  });
  archiveTelegramMessage({
    chatId: '103', messageId: '303', ts: 1_786_000_100,
    sender: 'Eva', role: 'assistant', content: text, processingStatus: 'completed',
    meta: {
      outbound: true, dedupeKey: 'fault-3:chunk:1', deliveryKey: 'fault-3',
      payloadSha256: attempt.intent.chunkSha256, chunkIndex: 0, chunkCount: 1,
    },
  });
  let calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => sendTelegramText('103', text, options), (error) => {
    assert.equal(error.classification, 'uncertain');
    return true;
  });
  assert.equal(calls, 0);
  assert.equal(db.prepare('SELECT status FROM turn_tool_executions WHERE execution_key=?')
    .get(attempt.operationKey).status, 'transport_uncertain');
});

test('4. dezelfde delivery-key met andere tekst botst vóór transport', async () => {
  const options = { dedupeKey: 'fault-4' };
  mockTextSender(async (chatId) => ({ chat: { id: chatId }, message_id: 404, date: 1_786_000_200 }));
  await sendTelegramText('104', 'Eerste tekst.', options);
  let calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => sendTelegramText('104', 'Andere tekst.', options), /botst|andere handeling/i);
  assert.equal(calls, 0);
});

test('5. reply- en threadopties zijn onderdeel van dezelfde semantische intent', async () => {
  const text = 'Exacte thread.';
  mockTextSender(async (chatId) => ({ chat: { id: chatId }, message_id: 505, date: 1_786_000_300 }));
  await sendTelegramText('105', text, {
    dedupeKey: 'fault-5', replyToMessageId: 7, messageThreadId: 8,
  });
  let calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => sendTelegramText('105', text, {
    dedupeKey: 'fault-5', replyToMessageId: 9, messageThreadId: 8,
  }), /andere handeling/i);
  assert.equal(calls, 0);
});

test('6. lange reply stopt na een onzekere middelste chunk en verstuurt geen suffix', async () => {
  const text = `${'a'.repeat(3_800)}${'b'.repeat(3_800)}${'c'.repeat(300)}`;
  const options = { dedupeKey: 'fault-6', replyToMessageId: 6 };
  let calls = 0;
  mockTextSender(async (chatId) => {
    calls += 1;
    if (calls === 2) throw new HttpError('verbinding brak af', new Error('reset'));
    return { chat: { id: chatId }, message_id: 600 + calls, date: 1_786_000_400 + calls };
  });
  await assert.rejects(() => sendTelegramText('106', text, options), (error) => {
    assert.equal(error.classification, 'uncertain');
    assert.equal(error.acceptedPrefix.length, 1);
    return true;
  });
  assert.equal(calls, 2);
  calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => sendTelegramText('106', text, options));
  assert.equal(calls, 0);
  assert.equal(telegramMessage('106', '601').content.length, 3_800);
});

test('6b. actualiteit en deadline worden opnieuw gecontroleerd vóór ieder tekstfragment', async () => {
  const text = `${'eerste '.repeat(600)}${'tweede '.repeat(600)}`;
  let calls = 0;
  let checks = 0;
  mockTextSender(async (chatId) => {
    calls += 1;
    return { chat: { id: chatId }, message_id: 6_200 + calls, date: 1_786_000_450 + calls };
  });
  await assert.rejects(() => sendTelegramText('1062', text, {
    dedupeKey: 'fault-6b-current-per-chunk',
    isCurrent: () => ++checks === 1,
    checkCurrentBeforeEachTransport: true,
  }), (error) => {
    assert.equal(error.classification, 'uncertain');
    assert.equal(error.partial, true);
    assert.equal(error.acceptedPrefix.length, 1);
    return true;
  });
  assert.equal(calls, 1);
  assert.equal(checks, 2);
});

test('7. expliciete tijdelijke Telegramweigering hervat alleen ontbrekende chunks', async () => {
  const text = `${'d'.repeat(3_800)}${'e'.repeat(3_800)}${'f'.repeat(300)}`;
  const options = { dedupeKey: 'fault-7', replyToMessageId: 7 };
  let calls = 0;
  mockTextSender(async (chatId) => {
    calls += 1;
    if (calls === 2) {
      throw new GrammyError('tijdelijk', {
        ok: false, error_code: 500, description: 'tijdelijk niet beschikbaar',
      }, 'sendMessage', {});
    }
    return { chat: { id: chatId }, message_id: 700 + calls, date: 1_786_000_500 + calls };
  });
  await assert.rejects(() => sendTelegramText('107', text, options), (error) => {
    assert.equal(error.classification, 'retryable');
    return true;
  });
  const chunks = __telegramTransportTest.sendTelegramText ? [
    'd'.repeat(3_800), 'e'.repeat(3_800), 'f'.repeat(300),
  ] : [];
  const second = telegramTextIntent({
    chatId: '107', text: chunks[1], wholeText: text, deliveryKey: options.dedupeKey,
    index: 1, count: 3, options,
  });
  const row = db.prepare('SELECT output FROM turn_tool_executions WHERE execution_key=?')
    .get(second.operationKey);
  const outcome = JSON.parse(row.output);
  db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?').run(
    JSON.stringify({ ...outcome, retryAt: '2000-01-01T00:00:00.000Z' }), second.operationKey,
  );
  const retried = [];
  mockTextSender(async (chatId, body) => {
    retried.push(body);
    return { chat: { id: chatId }, message_id: 710 + retried.length, date: 1_786_000_600 + retried.length };
  });
  const delivered = await sendTelegramText('107', text, options);
  assert.equal(delivered.length, 3);
  assert.deepEqual(retried, chunks.slice(1));
});

test('8. prepared reply is immutable aan chat, bericht en echte Telegram-principal gebonden', () => {
  const ctx = context('108', '808', 42);
  const result = handled(ctx, 'Gebonden antwoord.');
  persistPreparedTelegramReply(ctx, 'Jaap', result);
  assert.equal(preparedTelegramResult(ctx).reply, result.reply);
  assert.throws(() => preparedTelegramResult(context('108', '808', 43)), /principal|oorsprong/i);
  assert.throws(() => authenticatedTelegramMessageContext(ctx, {
    authenticatedPrincipal: 'telegram:999', chatId: 'vals', messageId: 'vals', identityTrusted: false,
  }), /private processing-lease/i);
});

test('8a. prepared foutcode blijft exact en immutable aan de reply gebonden', () => {
  const ctx = context('108', '809', 42);
  const error = 'interpreter_unavailable';
  const prepared = persistPreparedTelegramReply(
    ctx, 'Jaap', handled(ctx, 'Ik heb je opdracht bewaard.', { error }),
  );
  assert.equal(prepared.error, error);
  const archived = telegramMessage('108', '809');
  assert.equal(archived.meta.preparedReply.error, error);

  archiveTelegramMessage({ chatId: '108', messageId: '809', meta: {
    preparedReply: { ...archived.meta.preparedReply, error: 'interpreter_timeout' },
  } });
  assert.throws(() => preparedTelegramResult(ctx), /gebonden|gewijzigd|andere principal/i);

  const boundedCtx = context('108', '810', 42);
  const boundedError = 'e'.repeat(500);
  persistPreparedTelegramReply(
    boundedCtx, 'Jaap', handled(boundedCtx, 'Begrensd antwoord.', { error: boundedError }),
  );
  const boundedArchived = telegramMessage('108', '810');
  archiveTelegramMessage({ chatId: '108', messageId: '810', meta: {
    preparedReply: { ...boundedArchived.meta.preparedReply, error: `${boundedError}suffix` },
  } });
  assert.throws(() => preparedTelegramResult(boundedCtx), /gebonden|gewijzigd|andere principal/i);

  for (const [messageId, invalidError] of [['811', null], ['812', '']]) {
    const absentCtx = context('108', messageId, 42);
    persistPreparedTelegramReply(absentCtx, 'Jaap', handled(absentCtx, 'Zonder foutcode.'));
    const absentArchived = telegramMessage('108', messageId);
    archiveTelegramMessage({ chatId: '108', messageId, meta: {
      preparedReply: { ...absentArchived.meta.preparedReply, error: invalidError },
    } });
    assert.throws(() => preparedTelegramResult(absentCtx), /gebonden|gewijzigd|andere principal/i);
  }
});

test('9. gewijzigde prepared metadata wordt niet heranalyseerd of verstuurd', () => {
  const ctx = context('109', '909', 42);
  persistPreparedTelegramReply(ctx, 'Jaap', handled(ctx, 'Origineel.'));
  const row = telegramMessage('109', '909');
  archiveTelegramMessage({
    chatId: '109', messageId: '909',
    meta: { preparedReply: { ...row.meta.preparedReply, conversationId: 'telegram:vals' } },
  });
  assert.throws(() => preparedTelegramResult(ctx), /gebonden|gewijzigd|andere principal/i);
});

test('9b. finale aflevermarker is onderdeel van de onveranderlijke prepared reply', () => {
  const ctx = context('109', '910', 42);
  const marker = 'a'.repeat(64);
  const prepared = persistPreparedTelegramReply(ctx, 'Jaap', handled(ctx, 'Exact finaal.', {
    goalEpisodeId: 901, workItemId: 902, finalDeliveryBindingSha256: marker,
  }));
  assert.equal(prepared.finalDeliveryBindingSha256, marker);
  assert.equal(telegramMessage('109', '910').meta.preparedReply.finalDeliveryBindingSha256, marker);

  const row = telegramMessage('109', '910');
  archiveTelegramMessage({ chatId: '109', messageId: '910', meta: {
    preparedReply: { ...row.meta.preparedReply, finalDeliveryBindingSha256: 'b'.repeat(64) },
  } });
  assert.throws(() => preparedTelegramResult(ctx), /gebonden|gewijzigd|andere principal/i);
});

test('9c. Telegram-herstel sluit alleen met exact gehashte prepared marker en transportproof', async () => {
  const ctx = context('109', '911', 42);
  const conversationId = 'telegram:109';
  archiveTelegramMessage({
    chatId: ctx.chat.id, messageId: ctx.message.message_id,
    sender: 'Jaap', role: 'user', content: 'Rond het actuele werk af.',
    meta: { inbound: true, fromTelegramId: '42' },
  });
  const { workItem, episode } = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Telegram finale herstelbinding',
    goal: 'Telegram finale herstelbinding wordt exact bewezen.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: conversationId, originMessageId: '911', requester: 'Jaap',
    deliveryPolicy: 'required', deliveryTarget: 'Jaap',
  });
  const proof = { name: 'lees_bestand', args: { pad: 'telegram-finaal.txt' },
    ok: true, verified: true,
    output: JSON.stringify({ contract: 'ActionResult/v1', ok: true, verified: true,
      data: { content: 'Telegram finale herstelbinding wordt exact bewezen.' },
      evidence: { summary: 'Telegram finale herstelbinding wordt exact bewezen.' } }) };
  const turnId = 'telegram:109:911';
  const finished = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: workItem.id, resultaat: 'Telegram finaal.',
  }, {
    mode: 'chat', sender: 'Jaap', channel: 'telegram', conversationId,
    originMessageId: '911', turnId, activeWorkId: workItem.id,
    activeGoalEpisodeId: episode.id, priorToolResults: [proof],
  }));
  assert.equal(finished.ok, true);
  const sealed = sealFinalDeliveryReply(episode.id, {
    workItemId: workItem.id, turnId, channel: 'telegram', conversationId,
    originMessageId: '911', resultText: 'Telegram finaal.',
  });
  assert.ok(sealed);
  const result = handled(ctx, 'Telegram finaal.', {
    goalEpisodeId: episode.id, workItemId: workItem.id,
    finalDeliveryBindingSha256: sealed.bindingSha256,
  });
  persistPreparedTelegramReply(ctx, 'Jaap', result);
  mockTextSender(async (chatId, body) => ({
    chat: { id: chatId }, message_id: 9_111, date: 1_786_009_111, text: body,
    reply_to_message: { message_id: 911 },
  }));
  const delivery = await publishDirectTelegramReply({ ctx, handled: result });

  const archived = telegramMessage('109', '911');
  archiveTelegramMessage({ chatId: '109', messageId: '911', meta: {
    preparedReply: {
      ...archived.meta.preparedReply,
      payload: { ...archived.meta.preparedReply.payload, reply: 'Gemanipuleerd.' },
    },
  } });
  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'telegram', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: '911',
    deliveredConversationId: conversationId, resultText: 'Telegram finaal.',
    idempotencyKey: delivery.dedupeKey,
    transport: delivery, deliveryTurnId: turnId,
    finalDeliveryBinding: { bindingSha256: sealed.bindingSha256, recovery: true },
  }), /duurzame prepared binding/iu);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');

  archiveTelegramMessage({ chatId: '109', messageId: '911', meta: {
    preparedReply: archived.meta.preparedReply,
  } });
  const receipt = recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'telegram', deliveryTarget: 'Jaap',
    originConversationId: conversationId, originMessageId: '911',
    deliveredConversationId: conversationId, resultText: 'Telegram finaal.',
    idempotencyKey: delivery.dedupeKey,
    transport: delivery, deliveryTurnId: turnId,
    finalDeliveryBinding: { bindingSha256: sealed.bindingSha256, recovery: true },
  });
  assert.ok(receipt);
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
});

test('10. uncertain direct reply blijft zichtbaar bevroren zonder chat_out of receipt', async () => {
  const ctx = context('110', '1010', 42);
  const { workItem, episode } = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Lever onzeker resultaat',
    goal: 'Bezorg exact één resultaat.', nextStep: 'Bezorg het resultaat.',
    definitionOfDone: 'Resultaat aantoonbaar bezorgd.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:110', originMessageId: '1010', requester: 'Jaap',
  });
  transitionGoalEpisode(episode.id, { state: 'delivery_pending' });
  mockTextSender(async () => { throw new HttpError('timeout', new Error('reset')); });
  const before = db.prepare("SELECT count(*) n FROM timeline WHERE kind='chat_out'").get().n;
  await assert.rejects(() => deliverTelegramTurn({
    ctx, sender: 'Jaap', handled: handled(ctx, 'Mogelijk bezorgd.', {
      goalEpisodeId: episode.id, workItemId: workItem.id,
    }),
  }));
  assert.equal(telegramMessage('110', '1010').processing_status, 'delivery_uncertain');
  assert.equal(pendingPreparedTelegramMessages().some((row) => row.chat_id === '110'), false);
  assert.equal(db.prepare("SELECT count(*) n FROM timeline WHERE kind='chat_out'").get().n, before);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(episode.id).n, 0);
  const work = db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(workItem.id);
  assert.equal(JSON.parse(work.wait_json).reason, 'delivery_uncertain');
  assert.equal(selectPortfolioCommitment([work]), null);
  assert.equal(recoverStrandedDeliveryWork(), 0);
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');
});

test('11. alleen alle geaccepteerde chunks schrijven één chat_out en één receipt', async () => {
  const ctx = context('111', '1111', 42);
  const { workItem, episode } = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Lever lang resultaat',
    goal: 'Bezorg het hele resultaat.', nextStep: 'Bezorg alles.',
    definitionOfDone: 'Volledig bezorgd.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:111', originMessageId: '1111', requester: 'Jaap',
  });
  transitionGoalEpisode(episode.id, { state: 'delivery_pending' });
  let messageId = 1_200;
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: ++messageId, date: 1_786_001_000 + messageId,
  }));
  const result = await authenticHandled(
    ctx,
    `${'x'.repeat(3_800)}${'y'.repeat(200)}`,
    { goalEpisodeId: episode.id, workId: workItem.id },
  );
  const first = await deliverTelegramTurn({ ctx, sender: 'Jaap', handled: result });
  assert.equal(first.accepted, true);
  assertCurrentTelegramSidecar(ctx, result);
  assert.equal(deliveredReplyRecorded(result, first), true);
  assert.equal(deliveredReplyRecorded({ ...result, reply: 'Turn-id clone.' }, first), false);
  assert.equal(deliveredReplyRecorded({ ...result, channel: 'web' }, first), false);
  assert.equal(deliveredReplyRecorded(result, { ...first, transport: 'http' }), false);
  await deliverTelegramTurn({ ctx, sender: 'Jaap', handled: result });
  assert.equal(db.prepare("SELECT count(*) n FROM timeline WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=?")
    .get(result.turnId).n, 1);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(episode.id).n, 1);
});

test('11b. rollback verbruikt native result/cap niet en recovery verstuurt niet opnieuw', async () => {
  const ctx = context('111', '1112', 42);
  let calls = 0;
  mockTextSender(async (chatId, body) => {
    calls += 1;
    return {
      chat: { id: chatId }, message_id: 1_212, date: 1_786_001_212,
      text: body, reply_to_message: { message_id: 1112 },
    };
  });
  const result = await authenticHandled(ctx, 'Exact één keer fysiek bezorgd.');
  const delivery = await publishDirectTelegramReply({ ctx, handled: result });
  const deliveryAuthority = issueTelegramDeliveryAuthority(result, delivery);
  assert.ok(deliveryAuthority);
  db.exec(`CREATE TEMP TRIGGER native_delivery_chat_out_rollback
    BEFORE INSERT ON main.timeline WHEN NEW.kind='chat_out'
    BEGIN SELECT RAISE(ABORT,'forced native chat_out rollback'); END`);
  try {
    assert.throws(
      () => recordDeliveredReply(result, delivery, deliveryAuthority),
      /forced native chat_out rollback/u,
    );
  } finally {
    db.exec('DROP TRIGGER temp.native_delivery_chat_out_rollback');
  }
  assert.ok(verifiedTelegramDeliveryAuthority(deliveryAuthority, result, delivery),
    'de private delivery-cap werd ondanks rollback verbruikt');
  assert.equal(recordDeliveredReply(result, delivery, deliveryAuthority), true);
  assert.equal(calls, 1);
  assert.equal(db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=?`).get(result.turnId).value, 1);
  assert.equal(deliveredReplyRecorded(result, delivery), true);
  assert.equal(deliveredReplyRecorded({ ...result, reply: 'Andere bytes.' }, delivery), false);
  assert.equal(deliveredReplyRecorded({ ...result, channel: 'web' }, delivery), false);
  assert.equal(deliveredReplyRecorded(result, { ...delivery, transport: 'http' }), false);
});

test('12. vervalst accepted receipt wordt niet als herstelbewijs gebruikt', async () => {
  const text = 'Niet vervalsbaar.';
  const options = { dedupeKey: 'fault-12' };
  mockTextSender(async (chatId) => ({ chat: { id: chatId }, message_id: 1_212, date: 1_786_001_200 }));
  await sendTelegramText('112', text, options);
  const attempt = route('112', text, options);
  db.prepare("DELETE FROM telegram_messages WHERE chat_id='112' AND message_id='1212'").run();
  const row = db.prepare('SELECT output FROM turn_tool_executions WHERE execution_key=?').get(attempt.operationKey);
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?').run(
      JSON.stringify({ ...JSON.parse(row.output), chatId: '999' }), attempt.operationKey,
    );
  });
  let calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => sendTelegramText('112', text, options), /onvolledig|andere payload/i);
  assert.equal(calls, 0);
});

test('14. prepared synthetic-vlag en toolbewijs zijn onderdeel van de onveranderlijke binding', () => {
  const syntheticCtx = context('115', '1515', 42);
  persistPreparedTelegramReply(syntheticCtx, 'Jaap', handled(syntheticCtx, 'Niet synthetisch.', {
    syntheticTest: false,
  }));
  const synthetic = telegramMessage('115', '1515').meta.preparedReply;
  archiveTelegramMessage({
    chatId: '115', messageId: '1515',
    meta: { preparedReply: { ...synthetic, syntheticTest: true } },
  });
  assert.throws(() => preparedTelegramResult(syntheticCtx), /gewijzigd|gebonden/i);

  const toolCtx = context('116', '1616', 42);
  persistPreparedTelegramReply(toolCtx, 'Jaap', handled(toolCtx, 'Met bewijs.', {
    tools: [{ name: 'zoek', ok: true, verified: true, output: 'bron A', evidence: { url: 'https://example.test/a' } }],
  }));
  const prepared = telegramMessage('116', '1616').meta.preparedReply;
  archiveTelegramMessage({
    chatId: '116', messageId: '1616',
    meta: { preparedReply: {
      ...prepared,
      payload: { ...prepared.payload, tools: [{ ...prepared.payload.tools[0], output: 'bron B' }] },
    } },
  });
  assert.throws(() => preparedTelegramResult(toolCtx), /gewijzigd|gebonden/i);
});

test('15. ongeldige prepared reply wordt direct terminaal afgewezen zonder API of vijf pogingen', async () => {
  const ctx = context('117', '1717', 42);
  persistPreparedTelegramReply(ctx, 'Jaap', handled(ctx, 'Nooit versturen.'));
  const prepared = telegramMessage('117', '1717').meta.preparedReply;
  archiveTelegramMessage({
    chatId: '117', messageId: '1717',
    meta: { preparedReply: {
      ...prepared, payload: { ...prepared.payload, reply: 'Gemuteerd.' },
    } },
  });
  let calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => resumePreparedTelegramTurn(ctx, 'Jaap'), (error) => {
    assert.equal(error.classification, 'rejected');
    return true;
  });
  assert.equal(calls, 0);
  assert.equal(telegramMessage('117', '1717').processing_status, 'delivery_rejected');
  assert.equal(claimTelegramMessage('117', '1717').reason, 'delivery_rejected');
  assert.equal(pendingPreparedTelegramMessages().some((row) => row.chat_id === '117'), false);
});

test('16. verkeerde response-chat of ontbrekende Telegram-date wordt onzeker en nooit geaccepteerd', async () => {
  let calls = 0;
  mockTextSender(async () => {
    calls += 1;
    return { chat: { id: '999' }, message_id: 1_600, date: 1_786_002_000 };
  });
  await assert.rejects(
    () => sendTelegramText('118', 'Verkeerde chat.', { dedupeKey: 'fault-16-chat' }),
    (error) => error.classification === 'uncertain',
  );
  const wrongChat = route('118', 'Verkeerde chat.', { dedupeKey: 'fault-16-chat' });
  assert.equal(db.prepare('SELECT status FROM turn_tool_executions WHERE execution_key=?')
    .get(wrongChat.operationKey).status, 'transport_uncertain');

  mockTextSender(async () => {
    calls += 1;
    return { chat: { id: '119' }, message_id: 1_601 };
  });
  await assert.rejects(
    () => sendTelegramText('119', 'Zonder datum.', { dedupeKey: 'fault-16-date' }),
    (error) => error.classification === 'uncertain',
  );
  const beforeReplay = calls;
  mockTextSender(async () => { calls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => sendTelegramText('119', 'Zonder datum.', { dedupeKey: 'fault-16-date' }));
  assert.equal(calls, beforeReplay);
});

test('17. terminale transportstatussen kunnen niet terug naar sending of accepted', () => {
  assert.throws(() => claimTurnToolExecution({
    executionKey: 'matrix-forged-accepted', turnId: 'turn:matrix:forged',
    tool: '@transport/not-telegram', args: {}, initialStatus: 'transport_accepted',
  }), /ongeldige|gereserveerd/i);
  assert.throws(() => claimTurnToolExecution({
    executionKey: 'matrix-forged-sending', turnId: 'turn:matrix:forged',
    tool: '@transport/not-telegram', args: {}, initialStatus: 'transport_sending',
  }), /gereserveerd/i);
  claimTurnToolExecution({
    executionKey: 'matrix-accepted', turnId: 'turn:matrix:a', tool: '@transport/telegram.test', args: { a: 1 },
    initialStatus: 'transport_sending',
  });
  transitionTurnToolExecution('matrix-accepted', {
    from: 'transport_sending', status: 'transport_accepted', output: { accepted: true },
  });
  assert.throws(() => transitionTurnToolExecution('matrix-accepted', {
    from: 'transport_accepted', status: 'transport_sending', finished: false,
  }), /verboden/i);

  claimTurnToolExecution({
    executionKey: 'matrix-uncertain', turnId: 'turn:matrix:u', tool: '@transport/telegram.test', args: { a: 2 },
    initialStatus: 'transport_sending',
  });
  transitionTurnToolExecution('matrix-uncertain', {
    from: 'transport_sending', status: 'transport_uncertain', output: { uncertain: true },
  });
  assert.throws(() => transitionTurnToolExecution('matrix-uncertain', {
    from: 'transport_uncertain', status: 'transport_accepted', output: { accepted: true },
  }), /verboden/i);

  claimTurnToolExecution({
    executionKey: 'matrix-legacy-started', turnId: 'turn:matrix:legacy',
    tool: '@transport/telegram.test', args: { legacy: true }, initialStatus: 'started',
  });
  assert.throws(() => transitionTurnToolExecution('matrix-legacy-started', {
    from: 'started', status: 'done', output: { accepted: true },
  }), /verboden/i);
  assert.throws(() => transitionTurnToolExecution('matrix-legacy-started', {
    from: 'started', status: 'transport_accepted', output: { accepted: true },
  }), /verboden/i);
});

test('18. foto en audio delen hetzelfde bewijsprotocol en media zonder sleutel faalt dicht', async () => {
  let photoCalls = 0;
  setBotForTest({ api: {
    sendPhoto: async (chatId, _file, options) => {
      photoCalls += 1;
      return {
        chat: { id: chatId }, message_id: 1_800, date: 1_786_003_000,
        caption: options.caption,
        photo: [{ file_id: 'photo-file-1800' }],
      };
    },
  } });
  await assert.rejects(() => sendTelegramMedia({
    kind: 'photo', chatId: '120', buffer: Buffer.from('zonder-sleutel'), caption: '',
  }), (error) => error.code === 'TELEGRAM_DURABLE_KEY_REQUIRED');
  assert.equal(photoCalls, 0);

  const photoArgs = {
    kind: 'photo', chatId: '120', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    caption: 'bewijsfoto', deliveryKey: 'fault-18-photo', sendOptions: { caption: 'bewijsfoto' },
  };
  await sendTelegramMedia(photoArgs);
  assert.equal(photoCalls, 1);
  db.prepare("DELETE FROM telegram_messages WHERE chat_id='120' AND message_id='1800'").run();
  setBotForTest({ api: { sendPhoto: async () => { photoCalls += 1; throw new Error('geen resend'); } } });
  const recovered = await sendTelegramMedia(photoArgs);
  assert.equal(photoCalls, 1);
  assert.equal(recovered._evaRecovered, true);
  assert.equal(telegramMessage('120', '1800').kind, 'photo');

  let audioCalls = 0;
  setBotForTest({ api: {
    sendAudio: async () => {
      audioCalls += 1;
      throw new HttpError('audioverbinding brak af', new Error('reset'));
    },
  } });
  const audioArgs = {
    kind: 'audio', chatId: '121', buffer: Buffer.from('mp3'), caption: 'spraak',
    deliveryKey: 'fault-18-audio', sendOptions: { caption: 'spraak', title: 'Eva', performer: 'Eva' },
  };
  await assert.rejects(() => sendTelegramMedia(audioArgs), (error) => error.classification === 'uncertain');
  setBotForTest({ api: { sendAudio: async () => { audioCalls += 1; throw new Error('geen resend'); } } });
  await assert.rejects(() => sendTelegramMedia(audioArgs));
  assert.equal(audioCalls, 1);
});

test('19. de volledige accepted receipt detecteert message-id, datum en attempt-mutatie', async () => {
  const text = 'Volledig receiptbewijs.';
  const options = { dedupeKey: 'fault-19' };
  mockTextSender(async (chatId, body) => ({
    chat: { id: chatId }, message_id: 1_900, date: 1_786_004_000, text: body,
  }));
  await sendTelegramText('122', text, options);
  const attempt = route('122', text, options);
  const original = JSON.parse(db.prepare('SELECT output FROM turn_tool_executions WHERE execution_key=?')
    .get(attempt.operationKey).output);
  db.prepare("DELETE FROM telegram_messages WHERE chat_id='122' AND message_id='1900'").run();
  let calls = 0;
  mockTextSender(async () => { calls += 1; throw new Error('geen resend'); });
  for (const mutated of [
    { ...original, response: { ...original.response, messageId: '1901' } },
    { ...original, response: { ...original.response, telegramDate: original.response.telegramDate + 1 } },
    { ...original, attempt: original.attempt + 1 },
  ]) {
    withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
      db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?')
        .run(JSON.stringify(mutated), attempt.operationKey);
    });
    await assert.rejects(() => sendTelegramText('122', text, options), /gewijzigd|onvolledig/i);
  }
  assert.equal(calls, 0);
});

test('20. prepared thread, reply, caption, tools en evidence vormen één gesaniteerde binding', async () => {
  const ctx = context('123', '2020', 42);
  ctx.message.message_thread_id = 55;
  const result = handled(ctx, 'api_key=abcdefghijklmnop', {
    caption: 'caption secret=abcdefghijklmnop',
    evidence: { note: 'password=abcdefghijklmnop' },
    tools: [{ name: 'bewijs', ok: true, output: 'token=abcdefghijklmnop' }],
  });
  persistPreparedTelegramReply(ctx, 'Jaap', result);
  const archived = telegramMessage('123', '2020');
  assert.equal(archived.meta.preparedReply.payload.messageThreadId, 55);
  assert.match(archived.meta.preparedReply.payload.reply, /AFGESCHERMD/);
  let offered = null;
  mockTextSender(async (chatId, body, options) => {
    offered = { chatId: String(chatId), body, options };
    return { chat: { id: chatId }, message_id: 2_021, date: 1_786_004_500, text: body };
  });
  await publishDirectTelegramReply({ ctx, handled: preparedTelegramResult(ctx) });
  assert.equal(offered.body, archived.meta.preparedReply.payload.reply);
  assert.equal(offered.options.message_thread_id, 55);
  assert.equal(offered.options.reply_parameters.message_id, 2020);
  const prepared = archived.meta.preparedReply;
  archiveTelegramMessage({
    chatId: '123', messageId: '2020',
    meta: { preparedReply: {
      ...prepared, payload: { ...prepared.payload, messageThreadId: 56 },
    } },
  });
  assert.throws(() => preparedTelegramResult(ctx), /gewijzigd|gebonden/i);
});

test('21. terminale inbound-afleverstatus kan niet worden overschreven of voltooid', () => {
  archiveTelegramMessage({
    chatId: '124', messageId: '2121', sender: 'Jaap', role: 'user',
    processingStatus: 'received', meta: { inbound: true },
  });
  assert.equal(claimTelegramMessage('124', '2121').claimed, true);
  assert.equal(markTelegramMessageDeliveryState('124', '2121', {
    status: 'delivery_uncertain', error: 'mogelijk zichtbaar',
  }), true);
  assert.equal(completeTelegramMessage('124', '2121'), false);
  assert.equal(failTelegramMessage('124', '2121', 'vals'), false);
  assert.throws(() => markTelegramMessageDeliveryState('124', '2121', {
    status: 'delivery_rejected', error: 'vals',
  }), /terminaal|botst/i);
  assert.equal(telegramMessage('124', '2121').processing_status, 'delivery_uncertain');
});

test('22. episodefreeze en inboundstatus zijn atomair en crashstatus wordt gereconcilieerd', () => {
  const makeEpisode = (title, chatId, messageId) => {
    const created = createDurableWork({
      source: 'telegram', owner: 'Jaap', title, goal: title,
      nextStep: 'bezorg', definitionOfDone: 'bezorgd',
    }, {
      originKind: 'user', originChannel: 'telegram',
      originConversationId: `telegram:${chatId}`, originMessageId: messageId, requester: 'Jaap',
    });
    transitionGoalEpisode(created.episode.id, { state: 'delivery_pending' });
    return created;
  };
  const atomic = makeEpisode('Atomair', '125', '2222');
  archiveTelegramMessage({
    chatId: '125', messageId: '2222', sender: 'Jaap', role: 'user',
    processingStatus: 'processing', meta: { inbound: true },
  });
  freezeEpisodeDelivery(atomic.episode.id, {
    reason: 'delivery_uncertain', operationKey: 'atomic-op',
    inbound: { chatId: '125', messageId: '2222', error: 'timeout' },
  });
  assert.equal(telegramMessage('125', '2222').processing_status, 'delivery_uncertain');
  assert.equal(JSON.parse(db.prepare('SELECT wait_json FROM eva_work_items WHERE id=?')
    .get(atomic.workItem.id).wait_json).reason, 'delivery_uncertain');

  const rollback = makeEpisode('Rollback', '125', '2223');
  assert.throws(() => freezeEpisodeDelivery(rollback.episode.id, {
    reason: 'delivery_rejected', operationKey: 'rollback-op',
    inbound: { chatId: '125', messageId: 'bestaat-niet', error: 'weigering' },
  }), /mist.*inbound/i);
  assert.equal(db.prepare('SELECT wait_json FROM eva_work_items WHERE id=?')
    .get(rollback.workItem.id).wait_json, null);

  const restart = makeEpisode('Restart', '125', '2224');
  archiveTelegramMessage({
    chatId: '125', messageId: '2224', sender: 'Jaap', role: 'user',
    meta: { inbound: true, preparedReply: { goalEpisodeId: restart.episode.id, turnId: 'restart-turn' } },
  });
  markTelegramMessageDeliveryState('125', '2224', {
    status: 'delivery_rejected', error: 'crash na inboundstatus',
  });
  assert.ok(reconcileFrozenEpisodeDeliveries() >= 1);
  assert.equal(JSON.parse(db.prepare('SELECT wait_json FROM eva_work_items WHERE id=?')
    .get(restart.workItem.id).wait_json).reason, 'delivery_rejected');
});

test('23. intrekken maakt deleted archief nooit opnieuw tot afleverbewijs', async () => {
  let sends = 0;
  let deletes = 0;
  setBotForTest({ api: {
    sendMessage: async (chatId, body) => ({
      chat: { id: chatId }, message_id: 2_300, date: 1_786_005_000,
      text: body, _call: ++sends,
    }),
    deleteMessage: async () => { deletes += 1; return true; },
  } });
  const options = {
    directReply: true, directReplyChatId: '126', dedupeKey: 'fault-23',
  };
  const delivered = await sendToFamily('Trek mij in.', 'Jaap', options);
  assert.equal(delivered.accepted, true);
  assert.equal((await delivered.retract()).ok, true);
  assert.equal(deletes, 1);
  assert.equal(telegramMessage('126', '2300').deleted, true);
  const replay = await sendToFamily('Trek mij in.', 'Jaap', options);
  assert.equal(replay.accepted, false);
  assert.equal(replay.uncertain, true);
  assert.equal(sends, 1);
});

test('24. accepted prefix plus definitieve 400 blijft partial/uncertain, nooit rejected', async () => {
  const ctx = context('127', '2424', 42);
  let calls = 0;
  mockTextSender(async (chatId, body) => {
    calls += 1;
    if (calls === 2) throw new GrammyError('ongeldig', {
      ok: false, error_code: 400, description: 'bad request',
    }, 'sendMessage', {});
    return { chat: { id: chatId }, message_id: 2_400, date: 1_786_006_000, text: body };
  });
  await assert.rejects(() => deliverTelegramTurn({
    ctx, sender: 'Jaap', handled: handled(ctx, `${'p'.repeat(3_800)}${'q'.repeat(200)}`),
  }), (error) => error.classification === 'uncertain' && error.partial === true);
  assert.equal(telegramMessage('127', '2424').processing_status, 'delivery_uncertain');
});

test('25. retryAt-polls verbruiken geen inboundpoging en doen geen API-call', async () => {
  const ctx = context('128', '2525', 42);
  const result = handled(ctx, 'Probeer later.');
  persistPreparedTelegramReply(ctx, 'Jaap', result);
  assert.equal(claimTelegramMessage('128', '2525').attempts, 1);
  let calls = 0;
  mockTextSender(async (chatId, body) => {
    if (String(chatId) !== '128') return {
      chat: { id: chatId }, message_id: 2_529 + calls, date: 1_786_006_500, text: body,
    };
    calls += 1;
    throw new GrammyError('te snel', {
      ok: false, error_code: 429, description: 'retry later', parameters: { retry_after: 3_600 },
    }, 'sendMessage', {});
  });
  await assert.rejects(() => deliverTelegramTurn({ ctx, sender: 'Jaap', handled: result }),
    (error) => error.classification === 'retryable');
  assert.equal(failTelegramMessage('128', '2525', 'later'), true);
  for (let index = 0; index < 8; index += 1) await drainPreparedTelegramOutbox();
  const row = telegramMessage('128', '2525');
  assert.equal(row.processing_attempts, 1);
  assert.equal(row.processing_status, 'failed');
  assert.equal(calls, 1);
});

test('26. twee gelijktijdige identieke callers delen exact één in-process transportpromise', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let calls = 0;
  mockTextSender(async (chatId, body) => {
    calls += 1;
    await gate;
    return { chat: { id: chatId }, message_id: 2_600, date: 1_786_007_000, text: body };
  });
  const first = sendTelegramText('129', 'Samen delen.', { dedupeKey: 'fault-26' });
  await new Promise((resolve) => setImmediate(resolve));
  const second = sendTelegramText('129', 'Samen delen.', { dedupeKey: 'fault-26' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 1);
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(a[0].message_id, b[0].message_id);
  assert.equal(calls, 1);
});

test('27. keyless tekst en archive-only media falen dicht zonder API', async () => {
  let textCalls = 0;
  mockTextSender(async () => { textCalls += 1; throw new Error('mag niet'); });
  await assert.rejects(() => sendTelegramText('130', 'Geen sleutel.'),
    (error) => error.code === 'TELEGRAM_DURABLE_KEY_REQUIRED');
  assert.equal(textCalls, 0);

  archiveTelegramMessage({
    chatId: '130', messageId: '2700', sender: 'Eva', role: 'assistant', kind: 'photo',
    fileId: 'oud-bestand', content: 'ander bijschrift',
    meta: { outbound: true, dedupeKey: 'fault-27-media', payloadSha256: 'vals' },
  });
  let mediaCalls = 0;
  setBotForTest({ api: { sendPhoto: async () => { mediaCalls += 1; throw new Error('mag niet'); } } });
  await assert.rejects(() => sendTelegramMedia({
    kind: 'photo', chatId: '130', buffer: Buffer.from('nieuw'), caption: 'nieuw',
    deliveryKey: 'fault-27-media', sendOptions: { caption: 'nieuw' },
  }), (error) => error.classification === 'uncertain');
  assert.equal(mediaCalls, 0);
});

test('28. vrije callerContext-booleans minten geen runtime-authority; gemerkte ingress wel', async () => {
  const contract = {
    mode: 'chat', channel: 'telegram', turnId: 'telegram:131:2828', userText: 'runtime status',
    turnUnderstanding: { kind: 'self_repair', actionIntent: 'execute' },
  };
  await assert.rejects(() => manageRuntime({ bewerking: 'stage', patch: 'geen diff' }, {
    ...contract,
    messageContext: { identityTrusted: true, authenticatedPrincipal: 'telegram:42' },
  }), /niet geautoriseerd/i);
  assert.throws(() => authenticatedTelegramMessageContext(context('131', '2828', 42)),
    /private processing-lease/i);
});

test('29. subprocess-herstart zet abandoned sending onzeker zonder resend', () => {
  const restartDir = fs.mkdtempSync(path.join('/tmp', 'eva-telegram-restart-'));
  const env = {
    ...process.env, EVA_DATA_DIR: restartDir, TELEGRAM_BOT_TOKEN: '',
    EVA_RUNTIME_DISABLE_INFERENCE: 'true', EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
  };
  const first = spawnSync(process.execPath, ['--input-type=module', '-e', `
    await import('./test/telegram-test-composition.js');
    const { claimTurnToolExecution, db } = await import('./src/db.js');
    const { __telegramTransportTest: t } = await import('./src/integrations/telegram.js');
    const d = t.telegramTextIntent({ chatId:'132', text:'restart', wholeText:'restart', deliveryKey:'fault-29', index:0, count:1, options:{dedupeKey:'fault-29'} });
    claimTurnToolExecution({ executionKey:d.operationKey, turnId:d.turnId, tool:d.tool, args:d.intent, initialStatus:'transport_sending' });
    db.close();
  `], { cwd: process.cwd(), env, encoding: 'utf8' });
  assert.equal(first.status, 0, first.stderr);
  const second = spawnSync(process.execPath, ['--input-type=module', '-e', `
    await import('./test/telegram-test-composition.js');
    const { db } = await import('./src/db.js');
    const { __telegramTransportTest: t } = await import('./src/integrations/telegram.js');
    let calls=0; t.setBotForTest({api:{sendMessage:async()=>{calls++; throw new Error('geen resend')}}});
    let classification='none'; try { await t.sendTelegramText('132','restart',{dedupeKey:'fault-29'}); } catch(e) { classification=e.classification; }
    const d=t.telegramTextIntent({chatId:'132',text:'restart',wholeText:'restart',deliveryKey:'fault-29',index:0,count:1,options:{dedupeKey:'fault-29'}});
    const status=db.prepare('SELECT status FROM turn_tool_executions WHERE execution_key=?').get(d.operationKey).status;
    process.stdout.write(JSON.stringify({calls,classification,status})); db.close();
  `], { cwd: process.cwd(), env, encoding: 'utf8' });
  try {
    assert.equal(second.status, 0, second.stderr);
    assert.deepEqual(JSON.parse(second.stdout), {
      calls: 0, classification: 'uncertain', status: 'transport_uncertain',
    });
  } finally {
    fs.rmSync(restartDir, { recursive: true, force: true });
  }
});

test('30. ontbrekende tekst- of bijschriftecho wordt nooit acceptatiebewijs', async () => {
  setBotForTest({ api: { sendMessage: async (chatId) => ({
    chat: { id: chatId }, message_id: 3_001, date: 1_786_008_000,
  }) } });
  await assert.rejects(
    () => sendTelegramText('133', 'Echo verplicht.', { dedupeKey: 'fault-30-text' }),
    (error) => error.classification === 'uncertain',
  );
  const textAttempt = route('133', 'Echo verplicht.', { dedupeKey: 'fault-30-text' });
  assert.equal(db.prepare('SELECT status FROM turn_tool_executions WHERE execution_key=?')
    .get(textAttempt.operationKey).status, 'transport_uncertain');
  assert.equal(telegramMessage('133', '3001'), null);

  setBotForTest({ api: { sendPhoto: async (chatId) => ({
    chat: { id: chatId }, message_id: 3_002, date: 1_786_008_001,
    photo: [{ file_id: 'photo-3002' }],
  }) } });
  await assert.rejects(() => sendTelegramMedia({
    kind: 'photo', chatId: '133', buffer: Buffer.from('foto-30'),
    caption: 'Dit bijschrift hoort erbij.', deliveryKey: 'fault-30-photo',
    sendOptions: { caption: 'Dit bijschrift hoort erbij.' },
  }), (error) => error.classification === 'uncertain');
  assert.equal(telegramMessage('133', '3002'), null);
});

test('31. response-thread en reply-anchor moeten exact bij de intent horen', async () => {
  setBotForTest({ api: { sendMessage: async (chatId, body) => ({
    chat: { id: chatId }, message_id: 3_101, date: 1_786_008_100, text: body,
    message_thread_id: 999,
    reply_to_message: { message_id: 3100 },
  }) } });
  await assert.rejects(() => sendTelegramText('134', 'Verkeerde thread.', {
    dedupeKey: 'fault-31-thread', messageThreadId: 55, replyToMessageId: 3100,
  }), (error) => error.classification === 'uncertain');

  setBotForTest({ api: { sendMessage: async (chatId, body) => ({
    chat: { id: chatId }, message_id: 3_102, date: 1_786_008_101, text: body,
    reply_to_message: { message_id: 9999 },
  }) } });
  await assert.rejects(() => sendTelegramText('134', 'Verkeerd antwoordanker.', {
    dedupeKey: 'fault-31-reply', replyToMessageId: 3100,
  }), (error) => error.classification === 'uncertain');
});

test('32. native Telegram kan zonder private @turn-authority geen provider of effect bereiken', async () => {
  const ctx = context('135', '3200', 42);
  ctx.message.message_thread_id = 55;
  archiveTelegramMessage({
    chatId: '135', messageId: '3200', sender: 'Jaap', role: 'user', content: 'Oude vraag',
    processingStatus: 'processing', meta: {
      inbound: true, fromTelegramId: '42', messageThreadId: '55',
    },
  });
  const before = authorityMutationSnapshot();
  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  let transportCalls = 0;
  let deletes = 0;
  setBotForTest({ api: {
    sendMessage: async () => { transportCalls += 1; throw new Error('transport mag niet'); },
    deleteMessage: async () => { deletes += 1; return true; },
  } });
  globalThis.fetch = async () => { providerCalls += 1; throw new Error('provider mag niet'); };
  try {
    assert.throws(() => authenticatedTelegramMessageContext(ctx, {
      conversationId: 'telegram:135', queueKey: 'telegram-native-authority-32',
    }), /private processing-lease/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(providerCalls, 0);
  assert.equal(transportCalls, 0);
  assert.equal(deletes, 0);
  assert.deepEqual(authorityMutationSnapshot(), before);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM main.telegram_messages
    WHERE chat_id='135' AND role='assistant'`).get().count, 0);
});

test('33. een andere afzender of forumthread supersedet deze beurt niet', async () => {
  const ctx = context('136', '3300', 42);
  ctx.message.message_thread_id = 55;
  archiveTelegramMessage({
    chatId: '136', messageId: '3301', sender: 'Lola', role: 'user', content: 'Andere persoon',
    meta: { inbound: true, fromTelegramId: '43', messageThreadId: '55' },
  });
  archiveTelegramMessage({
    chatId: '136', messageId: '3302', sender: 'Jaap', role: 'user', content: 'Ander topic',
    meta: { inbound: true, fromTelegramId: '42', messageThreadId: '56' },
  });
  let calls = 0;
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 3_303, date: 1_786_008_300 + ++calls,
  }));
  const result = await authenticHandled(ctx, 'Alleen voor Jaap in topic 55.');
  const delivery = await deliverTelegramTurn({
    ctx, sender: 'Jaap', handled: result,
  });
  assert.equal(delivery.accepted, true);
  assertCurrentTelegramSidecar(ctx, result);
  assert.equal(calls, 1);
});

test('34. startup en native Telegram blijven zonder private @turn-authority exact effectloos', async () => {
  const ctx = context('137', '3400', 42);
  const userText = 'Ben je er nog?';
  const conversationId = 'telegram:137';
  archiveTelegramMessage({
    chatId: ctx.chat.id,
    messageId: ctx.message.message_id,
    sender: 'Jaap',
    role: 'user',
    kind: 'text',
    content: userText,
    meta: { inbound: true, fromTelegramId: '42' },
  });
  assert.deepEqual(claimTelegramMessage(ctx.chat.id, ctx.message.message_id), {
    claimed: true, reason: 'claimed', attempts: 1,
  });
  const ingressArgs = {
    channel: 'telegram', conversationId, messageId: '3400',
    requester: 'Jaap', originalText: userText, sessionRef: 'telegram:137:3400',
    syntheticTest: false,
  };
  const ingressKey = `turn-understanding:${createHash('sha256').update(canonicalJson({
    channel: ingressArgs.channel,
    conversationId: ingressArgs.conversationId,
    messageId: ingressArgs.messageId,
  })).digest('hex')}`;
  assert.throws(() => claimTurnToolExecution({
    executionKey: ingressKey,
    turnId: 'ingress:telegram:telegram:137:3400',
    tool: '@turn/understanding',
    args: ingressArgs,
  }), (error) => error?.code === 'EVA_RESERVED_EXECUTION_AUTHORITY');
  const before = authorityMutationSnapshot();

  const startup = () => spawnSync(process.execPath, ['--input-type=module', '-e', `
    await import('./src/mind/agent.js');
    const { db } = await import('./src/db.js');
    db.close();
  `], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TELEGRAM_BOT_TOKEN: '', OPENAI_API_KEY: '',
    },
  });
  const firstStartup = startup();
  const duplicateStartup = startup();
  assert.equal(firstStartup.status, 0, firstStartup.stderr || firstStartup.stdout);
  assert.equal(duplicateStartup.status, 0, duplicateStartup.stderr || duplicateStartup.stdout);
  assert.deepEqual(authorityMutationSnapshot(), before,
    'startup mag een authorityloze native Telegram-ingress niet adopteren');
  let providerCalls = 0;
  let transportCalls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error('authorityloze native ingress mag geen provider aanroepen');
  };
  setBotForTest({ api: { sendMessage: async () => {
    transportCalls += 1;
    throw new Error('authorityloze native ingress mag geen transport aanroepen');
  } } });
  try {
    assert.throws(() => authenticatedTelegramMessageContext(ctx, {
      conversationId, queueKey: 'telegram-native-authority-34',
    }), /private processing-lease/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(providerCalls, 0);
  assert.equal(transportCalls, 0);
  assert.deepEqual(authorityMutationSnapshot(), before);
  assert.equal(telegramMessage(ctx.chat.id, ctx.message.message_id).processing_status, 'processing');
  assert.equal(db.prepare(`SELECT count(*) n FROM turn_tool_executions
    WHERE turn_id='ingress:telegram:telegram:137:3400' AND tool='@turn/understanding'`)
    .get().n, 0);
  assert.equal(db.prepare(`SELECT count(*) n FROM telegram_messages
    WHERE chat_id='137' AND role='assistant' AND reply_to_message_id='3400'`).get().n, 0);
});

test('35. fanout-key bevriest de canonieke targetset ook over een herstart', () => {
  const restartDir = fs.mkdtempSync(path.join('/tmp', 'eva-telegram-targets-'));
  const env = {
    ...process.env, EVA_DATA_DIR: restartDir, TELEGRAM_BOT_TOKEN: '',
    EVA_RUNTIME_DISABLE_INFERENCE: 'true', EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
  };
  const script = (chatId) => `
    await import('./test/telegram-test-composition.js');
    const { db, upsertPerson } = await import('./src/db.js');
    const { __telegramTransportTest: t, sendToFamily } = await import('./src/integrations/telegram.js');
    upsertPerson('Jaap', { telegram_id: '${chatId}' });
    const calls=[];
    t.setBotForTest({api:{sendMessage:async(chat,body)=>{calls.push(String(chat));return {chat:{id:chat},message_id:${chatId},date:1786008500,text:body}}}});
    let result;
    try { result=await sendToFamily('Bevroren gezin.','gezin',{dedupeKey:'fault-35-targets'}); }
    catch(error) { result={ok:false,accepted:false,info:error.message}; }
    const acceptedRows=db.prepare("SELECT count(*) n FROM turn_tool_executions WHERE status='transport_accepted'").get().n;
    process.stdout.write(JSON.stringify({accepted:result.accepted===true,calls,acceptedRows}));db.close();
  `;
  const first = spawnSync(process.execPath, ['--input-type=module', '-e', script('901')], {
    cwd: process.cwd(), env, encoding: 'utf8',
  });
  const second = spawnSync(process.execPath, ['--input-type=module', '-e', script('902')], {
    cwd: process.cwd(), env, encoding: 'utf8',
  });
  try {
    assert.equal(first.status, 0, first.stderr);
    assert.equal(second.status, 0, second.stderr);
    assert.deepEqual(JSON.parse(first.stdout), {
      accepted: true, calls: ['901'], acceptedRows: 1,
    });
    assert.deepEqual(JSON.parse(second.stdout), {
      accepted: false, calls: [], acceptedRows: 1,
    });
  } finally {
    fs.rmSync(restartDir, { recursive: true, force: true });
  }
});

test('36. een vrije chat_out-boolean kan geen episode-receipt aanmaken', async () => {
  const forged = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Niet werkelijk bezorgd',
    goal: 'Alleen echt transport sluit dit werk.', nextStep: 'Bezorg aantoonbaar.',
    definitionOfDone: 'Telegramtransport is cryptografisch gebonden.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:138', originMessageId: '3600', requester: 'Jaap',
  });
  transitionGoalEpisode(forged.episode.id, { state: 'delivery_pending' });
  logTimeline('chat_out', 'Eva -> Jaap: nooit werkelijk verstuurd', {
    channel: 'telegram', conversationId: 'telegram:138', turnId: 'forged-turn',
    goalEpisodeId: forged.episode.id, originMessageId: '3600',
    delivery: { accepted: true, transport: 'telegram' },
  });
  assert.equal(recoverDeliveredEpisodeReceipts(), 0);
  assert.equal(goalEpisodeById(forged.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(forged.episode.id).n, 0);

  const legitimate = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Wel werkelijk bezorgd',
    goal: 'Herstel na crash met transportbewijs.', nextStep: 'Bezorg aantoonbaar.',
    definitionOfDone: 'Telegramtransport is cryptografisch gebonden.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:139', originMessageId: '3601', requester: 'Jaap',
  });
  transitionGoalEpisode(legitimate.episode.id, { state: 'delivery_pending' });
  archiveTelegramMessage({
    chatId: '139', messageId: '3601', sender: 'Jaap', role: 'user',
    processingStatus: 'completed',
    meta: { inbound: true, fromTelegramId: '42' },
  });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 3_602, date: 1_786_008_600,
  }));
  const deliveryKey = 'user-reply:turn:telegram:139:3601';
  const delivery = await sendToFamily('Werkelijk verstuurd.', 'Jaap', {
    directReply: true, directReplyChatId: '139', dedupeKey: deliveryKey,
    directTurnSignalRef: 'turn:telegram:139:3601',
    originPrincipal: 'telegram:42', replyToMessageId: 3601,
  });
  assert.equal(delivery.accepted, true);
  logTimeline('chat_out', 'Eva -> Jaap: Werkelijk verstuurd.', {
    channel: 'telegram', conversationId: 'telegram:139', turnId: 'telegram:139:3601',
    goalEpisodeId: legitimate.episode.id, originMessageId: '3601',
    delivery: { ...delivery, dedupeKey: deliveryKey },
  });
  assert.equal(recoverDeliveredEpisodeReceipts(), 1);
  assert.equal(goalEpisodeById(legitimate.episode.id).state, 'closed');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(legitimate.episode.id).n, 1);
});

test('37. onverwachte text- of mediaresponsvelden worden vóór accepted onzeker', async () => {
  setBotForTest({ api: { sendMessage: async (chatId, body) => ({
    chat: { id: chatId }, message_id: 3_701, date: 1_786_008_701,
    text: body, caption: 'onverwacht bij tekst',
  }) } });
  const textRoute = route('141', 'Exact tekstbewijs.', { dedupeKey: 'fault-37-text' });
  await assert.rejects(
    () => sendTelegramText('141', 'Exact tekstbewijs.', { dedupeKey: 'fault-37-text' }),
    (error) => error.classification === 'uncertain',
  );
  assert.equal(db.prepare('SELECT status FROM turn_tool_executions WHERE execution_key=?')
    .get(textRoute.operationKey).status, 'transport_uncertain');

  const buffer = Buffer.from('media-37');
  const mediaRoute = telegramMediaIntent({
    kind: 'photo', chatId: '141', buffer, caption: 'Exact bijschrift.',
    deliveryKey: 'fault-37-photo', sendOptions: { caption: 'Exact bijschrift.' },
  });
  setBotForTest({ api: { sendPhoto: async (chatId, _file, options) => ({
    chat: { id: chatId }, message_id: 3_702, date: 1_786_008_702,
    caption: options.caption, text: 'onverwacht bij media',
    photo: [{ file_id: 'photo-3702' }],
  }) } });
  await assert.rejects(() => sendTelegramMedia({
    kind: 'photo', chatId: '141', buffer, caption: 'Exact bijschrift.',
    deliveryKey: 'fault-37-photo', sendOptions: { caption: 'Exact bijschrift.' },
  }), (error) => error.classification === 'uncertain');
  assert.equal(db.prepare('SELECT status FROM turn_tool_executions WHERE execution_key=?')
    .get(mediaRoute.operationKey).status, 'transport_uncertain');
});

test('38. deleteMessage=false blijft zichtbaar en maakt replay onzeker', async () => {
  let sends = 0;
  let deletes = 0;
  setBotForTest({ api: {
    sendMessage: async (chatId, body) => ({
      chat: { id: chatId }, message_id: 3_800, date: 1_786_008_800,
      text: body, call: ++sends,
    }),
    deleteMessage: async () => { deletes += 1; return false; },
  } });
  const options = {
    directReply: true, directReplyChatId: '142', dedupeKey: 'fault-38-delete-false',
  };
  const delivered = await sendToFamily('Niet aantoonbaar ingetrokken.', 'Jaap', options);
  const retraction = await delivered.retract();
  assert.deepEqual(retraction, { ok: false, removed: 0, total: 1, visible: true });
  assert.equal(deletes, 1);
  const archived = telegramMessage('142', '3800');
  assert.equal(archived.deleted, false);
  assert.equal(archived.meta.retractionPending, true);
  assert.equal(archived.meta.retractionUncertain, true);
  const replay = await sendToFamily('Niet aantoonbaar ingetrokken.', 'Jaap', options);
  assert.equal(replay.accepted, false);
  assert.equal(replay.uncertain, true);
  assert.equal(sends, 1);
});

test('39. crash na externe delete laat write-ahead pending en verbiedt resurrectie', () => {
  const restartDir = fs.mkdtempSync(path.join('/tmp', 'eva-telegram-retract-crash-'));
  const env = {
    ...process.env, EVA_DATA_DIR: restartDir, TELEGRAM_BOT_TOKEN: '',
    EVA_RUNTIME_DISABLE_INFERENCE: 'true', EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
  };
  const first = spawnSync(process.execPath, ['--input-type=module', '-e', `
    await import('./test/telegram-test-composition.js');
    const { db } = await import('./src/db.js');
    const { __telegramTransportTest:t, sendToFamily } = await import('./src/integrations/telegram.js');
    t.setBotForTest({api:{
      sendMessage:async(chat,text)=>({chat:{id:chat},message_id:3900,date:1786008900,text}),
      deleteMessage:async()=>{process.exit(0)},
    }});
    const delivered=await sendToFamily('Crash tussen delete en bevestiging.','Jaap',{
      directReply:true,directReplyChatId:'143',dedupeKey:'fault-39-crash'
    });
    await delivered.retract();
    db.close(); process.exit(9);
  `], { cwd: process.cwd(), env, encoding: 'utf8' });
  const second = spawnSync(process.execPath, ['--input-type=module', '-e', `
    await import('./test/telegram-test-composition.js');
    const { db } = await import('./src/db.js');
    const { __telegramTransportTest:t, sendToFamily } = await import('./src/integrations/telegram.js');
    let calls=0;
    t.setBotForTest({api:{sendMessage:async()=>{calls+=1;throw new Error('geen resend')}}});
    const result=await sendToFamily('Crash tussen delete en bevestiging.','Jaap',{
      directReply:true,directReplyChatId:'143',dedupeKey:'fault-39-crash'
    });
    const row=db.prepare("SELECT deleted,meta FROM telegram_messages WHERE chat_id='143' AND message_id='3900'").get();
    const meta=JSON.parse(row.meta);
    process.stdout.write(JSON.stringify({
      accepted:result.accepted===true,uncertain:result.uncertain===true,calls,
      pending:meta.retractionPending===true,deleted:row.deleted===1,
    }));
    db.close();
  `], { cwd: process.cwd(), env, encoding: 'utf8' });
  try {
    assert.equal(first.status, 0, first.stderr);
    assert.equal(second.status, 0, second.stderr);
    assert.deepEqual(JSON.parse(second.stdout), {
      accepted: false, uncertain: true, calls: 0, pending: true, deleted: false,
    });
  } finally {
    fs.rmSync(restartDir, { recursive: true, force: true });
  }
});

test('40. alias en Telegram-id delen één canonieke fanoutidentiteit', async () => {
  upsertPerson('Aliasbewoner Veertig', { telegram_id: '704' });
  let calls = 0;
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 4_000, date: 1_786_009_000 + ++calls,
  }));
  const first = await sendToFamily('Eén canonieke ontvanger.', 'Aliasbewoner Veertig', {
    dedupeKey: 'fault-40-canonical',
  });
  const replay = await sendToFamily('Eén canonieke ontvanger.', '704', {
    dedupeKey: 'fault-40-canonical',
  });
  const nearDuplicate = await sendToFamily('Eén canonieke ontvanger.', '704', {
    dedupeKey: 'fault-40-near-duplicate',
  });
  assert.equal(first.accepted, true);
  assert.equal(replay.accepted, true);
  assert.equal(replay.sent[0].messageId, first.sent[0].messageId);
  assert.equal(nearDuplicate.accepted, false);
  assert.equal(calls, 1);
});

test('41. legacy v2-receipt wordt onder het v3-contract fail-closed geweigerd', async () => {
  let calls = 0;
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 4_100, date: 1_786_009_100 + ++calls,
  }));
  const options = { dedupeKey: 'fault-41-v2' };
  await sendTelegramText('144', 'Alleen receipt v3.', options);
  const descriptor = route('144', 'Alleen receipt v3.', options);
  const row = db.prepare('SELECT output FROM turn_tool_executions WHERE execution_key=?')
    .get(descriptor.operationKey);
  const legacy = JSON.parse(row.output);
  legacy.contract = 'TelegramAcceptedReceipt/v2';
  delete legacy.receiptSha256;
  legacy.receiptSha256 = createHash('sha256').update(canonicalJson(legacy)).digest('hex');
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?').run(
      canonicalJson(legacy), descriptor.operationKey,
    );
  });
  db.prepare("DELETE FROM telegram_messages WHERE chat_id='144' AND message_id='4100'").run();
  await assert.rejects(() => sendTelegramText('144', 'Alleen receipt v3.', options),
    (error) => error.classification === 'uncertain');
  assert.equal(calls, 1);
  assert.equal(telegramMessage('144', '4100'), null);
});

test('42. lange multiline reply gebruikt exact één canonieke tekst voor send en receipt', async () => {
  const ctx = context('145', '4200', 42);
  const durable = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Lang exact resultaat',
    goal: 'Bewijs een lang multiline resultaat.', nextStep: 'Bezorg het resultaat.',
    definitionOfDone: 'Alle chunks zijn aantoonbaar bezorgd.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:145', originMessageId: '4200', requester: 'Jaap',
  });
  transitionGoalEpisode(durable.episode.id, { state: 'delivery_pending' });
  const reply = `  ${Array.from({ length: 420 }, (_, index) => (
    `regel ${index}: dit blijft exact staan.`
  )).join('\n')}  `;
  const canonical = canonicalTelegramText(reply);
  const expectedChunks = telegramModule.splitTelegramText(canonical);
  assert.ok(canonical.length > 12_000);
  assert.ok(expectedChunks.length > 3);
  let calls = 0;
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 4_201 + calls,
    date: 1_786_009_200 + ++calls,
  }));
  const result = await authenticHandled(ctx, reply, {
    goalEpisodeId: durable.episode.id, workId: durable.workItem.id,
  });
  const delivery = await deliverTelegramTurn({
    ctx,
    sender: 'Jaap',
    handled: result,
  });
  const receipt = db.prepare('SELECT * FROM eva_delivery_receipts WHERE episode_id=?')
    .get(durable.episode.id);
  assert.equal(delivery.accepted, true);
  assertCurrentTelegramSidecar(ctx, result);
  assert.equal(calls, expectedChunks.length);
  const childIntents = delivery.sent.map((item) => JSON.parse(db.prepare(
    'SELECT args FROM main.turn_tool_executions WHERE execution_key=?',
  ).get(item.operationKey).args));
  assert.deepEqual(childIntents.map((intent) => intent.segment),
    expectedChunks.map((_chunk, index) => ({
      index, count: expectedChunks.length, splitVersion: 'split-3800/v1',
    })));
  assert.deepEqual(childIntents.map((intent) => intent.payloadSha256),
    expectedChunks.map((chunk) => createHash('sha256').update(chunk).digest('hex')));
  assert.equal(receipt.result_text, canonical);
  assert.equal(goalEpisodeById(durable.episode.id).state, 'closed');
  assert.equal(JSON.parse(receipt.transport_json).sent.length, calls);
});

test('43. centrale foto- en spraakregistry weigert forged accepted en accepteert ledgerbewijs', async () => {
  const fake = async (_buffer, caption) => ({
    ok: true, accepted: true, transport: 'telegram',
    kind: caption === 'Foto 43' ? 'photo' : 'audio',
    sent: [{ chatId: '146', messageId: 'fake', operationKey: 'fake-operation' }],
  });
  registerPhotoSender(fake);
  registerVoiceSender(fake);
  const forgedPhoto = await sendRegisteredPhoto(
    Buffer.from('photo-fake'), 'image/jpeg', 'Foto 43', 'Jaap',
    { directReply: true, directReplyChatId: '146', dedupeKey: 'fault-43-fake-photo' },
  );
  const forgedVoice = await sendRegisteredVoice(
    Buffer.from('audio-fake'), 'spraak', 'Stem 43', 'Jaap',
    { directReply: true, directReplyChatId: '146', dedupeKey: 'fault-43-fake-audio' },
  );
  assert.equal(forgedPhoto.accepted, false);
  assert.equal(forgedVoice.accepted, false);

  registerPhotoSender(sendPhotoToFamily);
  registerVoiceSender(sendVoiceToFamily);
  setBotForTest({ api: {
    sendPhoto: async (chatId, _file, options) => ({
      chat: { id: chatId }, message_id: 4_301, date: 1_786_009_301,
      caption: options.caption, photo: [{ file_id: 'photo-4301' }],
    }),
    sendAudio: async (chatId, _file, options) => ({
      chat: { id: chatId }, message_id: 4_302, date: 1_786_009_302,
      caption: options.caption, audio: { file_id: 'audio-4302' },
    }),
  } });
  const photo = await sendRegisteredPhoto(
    Buffer.from('photo-real'), 'image/jpeg', 'Foto 43', 'Jaap',
    { directReply: true, directReplyChatId: '146', dedupeKey: 'fault-43-real-photo' },
  );
  const voice = await sendRegisteredVoice(
    Buffer.from('audio-real'), 'spraak', 'Stem 43', 'Jaap',
    { directReply: true, directReplyChatId: '146', dedupeKey: 'fault-43-real-audio' },
  );
  assert.equal(photo.accepted, true);
  assert.equal(voice.accepted, true);
});

test('44. echt bewijs voor Lola kan niet als aflevering aan Jaap worden witgewassen', async () => {
  upsertPerson('Jaap Bestemming 44', { telegram_id: '710' });
  upsertPerson('Lola Bestemming 44', { telegram_id: '711' });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 4_400, date: 1_786_009_400,
  }));
  const options = { dedupeKey: 'fault-44-wrong-recipient' };
  const lolaProof = await sendToFamily(
    'Dit hoort aantoonbaar bij Jaap.', 'Lola Bestemming 44', options,
  );
  registerMessenger(async () => lolaProof);
  const laundered = await sendRegisteredMessage(
    'Dit hoort aantoonbaar bij Jaap.', 'Jaap Bestemming 44', options,
  );
  assert.equal(laundered.ok, false);
  assert.equal(laundered.accepted, false);
  assert.match(laundered.info, /andere bestemming|niet worden bewezen/i);
});

test('45. echt bewijs uit een andere thread of reply-anchor wordt niet witgewassen', async () => {
  const text = 'Exact dezelfde payload en key.';
  const original = {
    directReply: true,
    directReplyChatId: '712',
    dedupeKey: 'fault-45-thread-text',
    messageThreadId: 111,
    replyToMessageId: 7_001,
    directTurnSignalRef: 'turn:telegram:712:7001',
  };
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 4_501, date: 1_786_009_501,
  }));
  const textProof = await sendToFamily(text, 'Jaap', original);
  registerMessenger(async () => textProof);
  const wrongTextEnvelope = await sendRegisteredMessage(text, 'Jaap', {
    ...original, messageThreadId: 222, replyToMessageId: 7_002,
    directTurnSignalRef: 'turn:telegram:712:7002',
  });
  assert.equal(wrongTextEnvelope.accepted, false);

  const photoBuffer = Buffer.from('photo-thread-45');
  setBotForTest({ api: { sendPhoto: async (chatId, _file, options) => ({
    chat: { id: chatId }, message_id: 4_502, date: 1_786_009_502,
    caption: options.caption, message_thread_id: options.message_thread_id,
    photo: [{ file_id: 'photo-4502' }],
  }) } });
  const photoOriginal = {
    directReply: true, directReplyChatId: '712',
    dedupeKey: 'fault-45-thread-photo', messageThreadId: 111,
  };
  const photoProof = await sendPhotoToFamily(
    photoBuffer, 'image/jpeg', 'Foto thread 45', 'Jaap', photoOriginal,
  );
  assert.equal(photoProof.accepted, true);
  registerPhotoSender(async () => photoProof);
  const wrongPhotoThread = await sendRegisteredPhoto(
    photoBuffer, 'image/jpeg', 'Foto thread 45', 'Jaap',
    { ...photoOriginal, messageThreadId: 222 },
  );
  assert.equal(wrongPhotoThread.accepted, false);

  const audioBuffer = Buffer.from('audio-thread-45');
  setBotForTest({ api: { sendAudio: async (chatId, _file, options) => ({
    chat: { id: chatId }, message_id: 4_503, date: 1_786_009_503,
    caption: options.caption, message_thread_id: options.message_thread_id,
    audio: { file_id: 'audio-4503' },
  }) } });
  const audioOriginal = {
    directReply: true, directReplyChatId: '712',
    dedupeKey: 'fault-45-thread-audio', messageThreadId: 111,
  };
  const audioProof = await sendVoiceToFamily(audioBuffer, 'Stem thread 45', 'Jaap', audioOriginal);
  assert.equal(audioProof.accepted, true);
  registerVoiceSender(async () => audioProof);
  const wrongAudioThread = await sendRegisteredVoice(
    audioBuffer, 'spraak', 'Stem thread 45', 'Jaap',
    { ...audioOriginal, messageThreadId: 222 },
  );
  assert.equal(wrongAudioThread.accepted, false);
});

test('46. required episode sluit met echt bewijs uit dezelfde topicthread en principal', async () => {
  const ctx = context('713', '7101', 501);
  ctx.message.message_thread_id = 111;
  const durable = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Topicresultaat afleveren',
    goal: 'Bezorg dit resultaat in dezelfde topicthread.', nextStep: 'Bezorg het resultaat.',
    definitionOfDone: 'Het resultaat is aantoonbaar in dezelfde topicthread bezorgd.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:713', originMessageId: '7101', requester: 'Jaap',
  });
  transitionGoalEpisode(durable.episode.id, { state: 'delivery_pending' });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 7_102, date: 1_786_009_600,
  }));

  const result = await authenticHandled(ctx, 'Dit hoort bij topic 111.', {
    goalEpisodeId: durable.episode.id, workId: durable.workItem.id,
  });
  const delivery = await deliverTelegramTurn({
    ctx,
    sender: 'Jaap',
    handled: result,
  });

  assert.equal(delivery.accepted, true);
  assertCurrentTelegramSidecar(ctx, result);
  assert.equal(delivery.originPrincipal, 'telegram:501');
  assert.equal(telegramMessage('713', '7101').meta.messageThreadId, '111');
  assert.equal(goalEpisodeById(durable.episode.id).state, 'closed');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(durable.episode.id).n, 1);
});

test('47. required episode weigert echt bewijs uit een andere Telegramtopicthread', async () => {
  archiveTelegramMessage({
    chatId: '714', messageId: '7201', sender: 'Jaap', role: 'user',
    processingStatus: 'processing',
    meta: { inbound: true, fromTelegramId: '42', messageThreadId: '111' },
  });
  const durable = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Verkeerde topic weigeren',
    goal: 'Accepteer alleen aflevering in topic 111.', nextStep: 'Bezorg in topic 111.',
    definitionOfDone: 'Alleen topic 111 telt als aflevering.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:714', originMessageId: '7201', requester: 'Jaap',
  });
  transitionGoalEpisode(durable.episode.id, { state: 'delivery_pending' });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 7_202, date: 1_786_009_700,
  }));
  const [message] = await sendTelegramText('714', 'Bewijs uit topic 222.', {
    dedupeKey: 'fault-47-required-wrong-thread',
    messageThreadId: 222,
    replyToMessageId: 7_201,
    directTurnSignalRef: 'turn:telegram:714:7201',
  });

  assert.throws(() => recordEpisodeDelivery(durable.episode.id, {
    deliveredChannel: 'telegram', deliveryTarget: 'Jaap',
    originConversationId: 'telegram:714', deliveredConversationId: 'telegram:714',
    resultText: 'Bewijs uit topic 222.',
    idempotencyKey: 'fault-47-required-wrong-thread',
    transport: {
      accepted: true, transport: 'telegram', kind: 'text',
      sent: [{ chatId: '714', messageId: '7202', ...message._evaTransport }],
    },
  }), /andere payload|acceptatiebewijs|Telegramaflevering/i);
  assert.equal(goalEpisodeById(durable.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(durable.episode.id).n, 0);
});

test('48. required episode weigert echt bewijs met een andere Telegramprincipal', async () => {
  archiveTelegramMessage({
    chatId: '8500', messageId: '7001', sender: 'Jaap', role: 'user',
    processingStatus: 'processing',
    meta: { inbound: true, fromTelegramId: '501', messageThreadId: '111' },
  });
  const durable = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Verkeerde principal weigeren',
    goal: 'Accepteer alleen aflevering namens de oorspronkelijke Telegramprincipal.',
    nextStep: 'Bezorg aan de oorspronkelijke beurt.',
    definitionOfDone: 'Alleen oorsprongsgebonden bewijs telt als aflevering.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:8500', originMessageId: '7001', requester: 'Jaap',
  });
  transitionGoalEpisode(durable.episode.id, { state: 'delivery_pending' });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 7_002, date: 1_786_009_800,
  }));
  const text = 'Chat, payload, thread, reply en turn kloppen.';
  const key = 'fault-48-required-wrong-principal';
  const [message] = await sendTelegramText('8500', text, {
    dedupeKey: key,
    messageThreadId: 111,
    replyToMessageId: 7_001,
    directTurnSignalRef: 'turn:telegram:8500:7001',
    originPrincipal: 'telegram:999',
  });

  assert.throws(() => recordEpisodeDelivery(durable.episode.id, {
    deliveredChannel: 'telegram', deliveryTarget: 'Jaap',
    originConversationId: 'telegram:8500', deliveredConversationId: 'telegram:8500',
    resultText: text, idempotencyKey: key,
    transport: {
      accepted: true, transport: 'telegram', kind: 'text',
      sent: [{ chatId: '8500', messageId: '7002', ...message._evaTransport }],
    },
  }), /andere beurt of principal|acceptatiebewijs|Telegramaflevering/i);
  assert.equal(goalEpisodeById(durable.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(durable.episode.id).n, 0);
});

test('49. required episode weigert geldig transport als het gepinde inboundbewijs ontbreekt', async () => {
  const durable = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Ontbrekend inboundbewijs weigeren',
    goal: 'Sluit alleen met een intacte Telegram-oorsprong.', nextStep: 'Controleer oorsprong.',
    definitionOfDone: 'Inbound- en outboundbewijs vormen één aantoonbare keten.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:8900', originMessageId: '7501', requester: 'Jaap',
  });
  transitionGoalEpisode(durable.episode.id, { state: 'delivery_pending' });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 7_502, date: 1_786_009_900,
  }));
  const text = 'Transport klopt, maar het inboundarchief ontbreekt.';
  const key = 'fault-49-required-absent-inbound';
  const [message] = await sendTelegramText('8900', text, {
    dedupeKey: key, replyToMessageId: 7_501,
    directTurnSignalRef: 'turn:telegram:8900:7501', originPrincipal: 'telegram:999',
  });

  assert.throws(() => recordEpisodeDelivery(durable.episode.id, {
    deliveredChannel: 'telegram', deliveryTarget: 'Jaap',
    originConversationId: 'telegram:8900', deliveredConversationId: 'telegram:8900',
    resultText: text, idempotencyKey: key,
    transport: {
      accepted: true, transport: 'telegram', kind: 'text',
      sent: [{ chatId: '8900', messageId: '7502', ...message._evaTransport }],
    },
  }), /mist het gepinde inboundbewijs/i);
  assert.equal(goalEpisodeById(durable.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(durable.episode.id).n, 0);
});

test('50. required episode weigert geldig transport bij corrupt gepind inboundbewijs', async () => {
  archiveTelegramMessage({
    chatId: '8901', messageId: '7601', sender: 'Jaap', role: 'user',
    processingStatus: 'processing',
    meta: { inbound: true, fromTelegramId: 'corrupt', messageThreadId: '111' },
  });
  const durable = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Corrupt inboundbewijs weigeren',
    goal: 'Sluit alleen met een intacte Telegram-oorsprong.', nextStep: 'Controleer oorsprong.',
    definitionOfDone: 'Inbound- en outboundbewijs vormen één aantoonbare keten.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:8901', originMessageId: '7601', requester: 'Jaap',
  });
  transitionGoalEpisode(durable.episode.id, { state: 'delivery_pending' });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 7_602, date: 1_786_010_000,
  }));
  const text = 'Transport klopt, maar het inboundarchief is corrupt.';
  const key = 'fault-50-required-corrupt-inbound';
  const [message] = await sendTelegramText('8901', text, {
    dedupeKey: key, messageThreadId: 111, replyToMessageId: 7_601,
    directTurnSignalRef: 'turn:telegram:8901:7601', originPrincipal: 'telegram:501',
  });

  assert.throws(() => recordEpisodeDelivery(durable.episode.id, {
    deliveredChannel: 'telegram', deliveryTarget: 'Jaap',
    originConversationId: 'telegram:8901', deliveredConversationId: 'telegram:8901',
    resultText: text, idempotencyKey: key,
    transport: {
      accepted: true, transport: 'telegram', kind: 'text',
      sent: [{ chatId: '8901', messageId: '7602', ...message._evaTransport }],
    },
  }), /Telegramprincipal is ongeldig/i);
  assert.equal(goalEpisodeById(durable.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(durable.episode.id).n, 0);
});

test('51. required episode weigert bewijs zonder exacte Telegramturnref', async () => {
  archiveTelegramMessage({
    chatId: '9300', messageId: '7901', sender: 'Jaap', role: 'user',
    processingStatus: 'processing',
    meta: { inbound: true, fromTelegramId: '501', messageThreadId: '111' },
  });
  const durable = createDurableWork({
    source: 'telegram', owner: 'Jaap', title: 'Ontbrekende turnbinding weigeren',
    goal: 'Accepteer alleen bewijs uit de exact gepinde Telegrambeurt.',
    nextStep: 'Bezorg aan de oorspronkelijke beurt.',
    definitionOfDone: 'De turnref is exact aan het inboundbericht gebonden.',
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: 'telegram:9300', originMessageId: '7901', requester: 'Jaap',
  });
  transitionGoalEpisode(durable.episode.id, { state: 'delivery_pending' });
  mockTextSender(async (chatId) => ({
    chat: { id: chatId }, message_id: 7_902, date: 1_786_010_100,
  }));
  const text = 'Chat, payload, thread, reply en principal kloppen.';
  const key = 'fault-51-required-missing-turn-ref';
  const delivery = await sendToFamily(text, 'Jaap', {
    directReply: true, directReplyChatId: '9300', dedupeKey: key,
    messageThreadId: 111, replyToMessageId: 7_901,
    originPrincipal: 'telegram:501',
  });
  assert.equal(delivery.accepted, true);

  assert.throws(() => recordEpisodeDelivery(durable.episode.id, {
    deliveredChannel: 'telegram', deliveryTarget: 'Jaap',
    originConversationId: 'telegram:9300', deliveredConversationId: 'telegram:9300',
    resultText: text, idempotencyKey: key,
    transport: delivery,
  }), /hoort niet bij deze episode of afleveringssleutel/i);
  assert.equal(goalEpisodeById(durable.episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_delivery_receipts WHERE episode_id=?')
    .get(durable.episode.id).n, 0);
});
