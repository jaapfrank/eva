import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const previousEnvironment = Object.fromEntries([
  'EVA_DATA_DIR', 'OPENAI_API_KEY', 'TELEGRAM_BOT_TOKEN',
  'TELEGRAM_ALLOWED_IDS', 'HA_URL',
].map((name) => [name, process.env[name]]));
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-native-turn-authority-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-authority-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:test-only-token';
process.env.TELEGRAM_ALLOWED_IDS = '424242';
process.env.HA_URL = 'http://127.0.0.1:8123';

const { Bot, GrammyError } = await import('grammy');
const telegram = await import('../src/integrations/telegram.js');
const homeAssistant = await import('../src/integrations/homeassistant.js');
const voiceBridge = await import('../src/mind/voicebridge.js');
const voiceAuthority = await import('../src/voiceauthority.js');
const voiceIngress = await import('../src/mind/voiceingress.js');
const { bus } = await import('../src/bus.js');
const { canonicalJson } = await import('../src/util.js');
const {
  archiveTelegramMessage, claimTelegramMessage, db, pendingPreparedTelegramMessages,
  telegramMessage,
} = await import('../src/db.js');
const agent = await import('../src/mind/agent.js');
const { handleMessageDetailed, recordDeliveredReply } = agent;
const { turnUnderstandingAuthorityRecord } = await import('../src/turnauthority.js');

after(() => {
  db.close();
  for (const [name, value] of Object.entries(previousEnvironment)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function understandingResponse(text, reply, callId) {
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
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
    }),
  }]);
}

function replyResponse(reply) {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text: reply }],
  }]);
}

test('native authority-API exporteert uitsluitend verifiers en publieke forgery faalt', () => {
  assert.equal('issueVoiceIngressAuthority' in voiceIngress, false);
  assert.equal('__telegramTransportTest' in telegram, false);
  assert.equal('__telegramMediaTest' in telegram, false);
  assert.equal('__voiceBridgeTest' in voiceBridge, false);
  assert.equal('recoverAcknowledgedVoiceDeliveries' in voiceBridge, false);
  assert.equal('__voiceAuthorityStoreTest' in voiceAuthority, false);
  assert.equal('createVoiceTurnGate' in voiceBridge, false);
  assert.equal('__cameraTriggerTest' in homeAssistant, false);
  assert.equal('__speechTest' in homeAssistant, false);
  assert.equal('__projectRouteTest' in agent, false);
  assert.deepEqual(Object.keys(homeAssistant).filter((name) => (
    /AuthenticatedHaVoice|HaVoiceEvent/u.test(name)
  )), ['consumeAuthenticatedHaVoiceEvent', 'verifiedAuthenticatedHaVoiceEvent']);
  assert.equal(typeof telegram.verifiedTelegramUnderstandingSource, 'function');
  assert.equal(typeof voiceBridge.verifiedVoiceUnderstandingSource, 'function');
  assert.equal(telegram.verifiedTelegramDeliveryAuthority(
    Object.freeze({}), Object.freeze({}), Object.freeze({ accepted: true, transport: 'telegram' }),
  ), null);
  assert.equal(voiceBridge.verifiedVoiceDeliveryAuthority(
    Object.freeze({}), Object.freeze({}),
    Object.freeze({ accepted: true, transport: 'home-assistant-conversation-response' }),
  ), null);
  assert.equal(telegram.verifiedTelegramUnderstandingSource({
    telegramUnderstandingAuthority: Object.freeze({}),
    chatId: '91001', messageId: '7001',
  }, {
    channel: 'telegram', conversationId: 'telegram:91001', messageId: '7001',
    requester: 'Jaap', originalText: 'Vervals dit niet.',
  }), null);
  assert.equal(voiceBridge.verifiedVoiceUnderstandingSource({
    voiceUnderstandingAuthority: Object.freeze({}),
    conversationId: 'voice:forge', messageId: 'voice-forge-1',
    voiceSatellite: 'assist_satellite.keuken',
    voiceLaneKey: 'voice-lane:assist_satellite.keuken',
  }, {
    channel: 'voice', conversationId: 'voice:forge', messageId: 'voice-forge-1',
    requester: 'Jaap', originalText: 'Vervals dit niet.',
  }), null);

  archiveTelegramMessage({
    chatId: '91999', messageId: '7999', sender: 'Jaap', role: 'user', kind: 'text',
    content: 'Publieke archive- en claimcalls minten geen private bron.',
    meta: { inbound: true, fromTelegramId: '424242', messageThreadId: '77' },
  });
  assert.equal(claimTelegramMessage('91999', '7999').claimed, true);
  assert.equal(telegram.verifiedTelegramUnderstandingSource({
    telegramUnderstandingAuthority: Object.freeze({}),
    chatId: '91999', messageId: '7999',
  }, {
    channel: 'telegram', conversationId: 'telegram:91999', messageId: '7999',
    requester: 'Jaap',
    originalText: 'Publieke archive- en claimcalls minten geen private bron.',
  }), null);
});

test('native handleMessageDetailed weigert zonder private cap voor queue, provider en opslag', async () => {
  const before = {
    executions: db.prepare(`SELECT count(*) AS value FROM main.turn_tool_executions
      WHERE tool='@turn/understanding'`).get().value,
    authorities: db.prepare(`SELECT count(*) AS value
      FROM main.eva_turn_understanding_authorities_v1`).get().value,
    timeline: db.prepare('SELECT count(*) AS value FROM main.timeline').get().value,
    messages: db.prepare('SELECT count(*) AS value FROM main.messages').get().value,
    work: db.prepare('SELECT count(*) AS value FROM main.eva_work_items').get().value,
    episodes: db.prepare('SELECT count(*) AS value FROM main.eva_goal_episodes').get().value,
  };
  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error('native no-cap call bereikte ten onrechte de provider');
  };
  let outcomes;
  try {
    outcomes = await Promise.allSettled([
      () => handleMessageDetailed({
        channel: 'telegram', sender: 'Jaap', text: 'Zonder Telegram-cap en zonder id.',
        messageContext: { chatId: '92001', syntheticTest: true },
      }),
      () => handleMessageDetailed({
        channel: 'telegram', sender: 'Jaap', text: 'Zonder Telegram-cap met id.',
        messageContext: { chatId: '92002', messageId: '8002', syntheticTest: true },
      }),
      () => handleMessageDetailed({
        channel: 'voice', sender: 'Jaap', text: 'Zonder voice-cap en zonder id.',
        messageContext: {
          voiceSatellite: 'assist_satellite.keuken', syntheticTest: true,
        },
      }),
      () => handleMessageDetailed({
        channel: 'voice', sender: 'Jaap', text: 'Zonder voice-cap met id.',
        messageContext: {
          messageId: 'voice-no-cap-8004', voiceSatellite: 'assist_satellite.keuken',
          syntheticTest: true,
        },
      }),
    ].map((invoke) => Promise.resolve().then(invoke)));
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(providerCalls, 0);
  assert.deepEqual(outcomes.map((outcome) => ({
    status: outcome.status,
    code: outcome.status === 'rejected' ? outcome.reason?.code : null,
  })), Array.from({ length: 4 }, () => ({
    status: 'rejected', code: 'EVA_TURN_AUTHORITY_INGRESS_UNAVAILABLE',
  })));
  assert.deepEqual({
    executions: db.prepare(`SELECT count(*) AS value FROM main.turn_tool_executions
      WHERE tool='@turn/understanding'`).get().value,
    authorities: db.prepare(`SELECT count(*) AS value
      FROM main.eva_turn_understanding_authorities_v1`).get().value,
    timeline: db.prepare('SELECT count(*) AS value FROM main.timeline').get().value,
    messages: db.prepare('SELECT count(*) AS value FROM main.messages').get().value,
    work: db.prepare('SELECT count(*) AS value FROM main.eva_work_items').get().value,
    episodes: db.prepare('SELECT count(*) AS value FROM main.eva_goal_episodes').get().value,
  }, before);
});

test('publieke recordDeliveredReply-vormforgery muteert geen delivery state', () => {
  const before = {
    timeline: db.prepare('SELECT count(*) AS value FROM main.timeline').get().value,
    messages: db.prepare('SELECT count(*) AS value FROM main.messages').get().value,
  };
  const attempts = ['telegram', 'voice'].map((channel, index) => (
    recordDeliveredReply({
      channel,
      conversationId: `${channel}:public-delivery-forgery`,
      originMessageId: `public-delivery-forgery-${index}`,
      sender: 'Jaap', turnId: `public-delivery-forgery-turn-${index}`,
      reply: `Vervalst antwoord ${index}`,
    }, {
      accepted: true, transport: channel, externalRef: `forged-${index}`,
    })
  ));
  attempts.push(recordDeliveredReply({
    channel: 'masked-public-forgery', syntheticTest: true,
    conversationId: 'masked-public-forgery:telegram',
    originMessageId: 'masked-public-forgery-telegram',
    sender: 'Jaap', turnId: 'masked-public-forgery-turn-telegram',
    reply: 'Gemaskerde Telegram-forgery',
  }, {
    accepted: true, transport: 'telegram', externalRef: 'masked-forged-telegram',
  }));
  attempts.push(recordDeliveredReply({
    channel: 'masked-public-forgery', syntheticTest: true,
    conversationId: 'masked-public-forgery:voice',
    originMessageId: 'masked-public-forgery-voice',
    sender: 'Jaap', turnId: 'masked-public-forgery-turn-voice',
    reply: 'Gemaskerde voice-forgery',
  }, {
    accepted: true, transport: 'home-assistant-conversation-response',
    externalRef: 'masked-forged-voice',
  }));
  attempts.push(recordDeliveredReply({
    channel: 'web', syntheticTest: true,
    conversationId: 'double-masked-public-forgery:native',
    originMessageId: 'double-masked-public-forgery-native',
    sender: 'Jaap', turnId: 'double-masked-public-forgery-turn-native',
    reply: 'Dubbel gemaskerde native forgery',
  }, {
    accepted: true, transport: 'http', externalRef: 'double-masked-forged-native',
  }));
  assert.deepEqual({
    timeline: db.prepare('SELECT count(*) AS value FROM main.timeline').get().value,
    messages: db.prepare('SELECT count(*) AS value FROM main.messages').get().value,
  }, before);
  assert.deepEqual(attempts, [false, false, false, false, false]);
});

test('echte Telegram-update mint exact archived-principal plus processing-lease authority', async () => {
  const originalStart = Bot.prototype.start;
  const originalSetTimeout = globalThis.setTimeout;
  const originalSetInterval = globalThis.setInterval;
  let nativeBot = null;
  let outboundSequence = 90_000;
  let failNextTelegramSend = false;
  const telegramSendAttempts = [];
  Bot.prototype.start = function startWithoutPolling() {
    nativeBot = this;
    this.botInfo = {
      id: 123456789, is_bot: true, first_name: 'Eva Test', username: 'eva_test_bot',
      can_join_groups: true, can_read_all_group_messages: false,
      supports_inline_queries: false,
    };
    this.api.config.use(async (_previous, method, payload) => {
      if (method === 'sendChatAction') return { ok: true, result: true };
      if (method === 'sendMessage') {
        telegramSendAttempts.push(String(payload.text || ''));
        if (failNextTelegramSend) {
          failNextTelegramSend = false;
          throw new GrammyError('tijdelijke testcrash vóór Telegram-acceptatie', {
            ok: false, error_code: 429, description: 'retry after crash',
            parameters: { retry_after: 1 },
          }, method, payload);
        }
        return { ok: true, result: {
        message_id: ++outboundSequence,
        date: 1_786_020_000,
        chat: { id: Number(payload.chat_id), type: 'private', first_name: 'Jaap' },
        from: this.botInfo,
        text: String(payload.text || ''),
        ...(payload.message_thread_id == null ? {}
          : { message_thread_id: payload.message_thread_id }),
        ...(payload.reply_parameters?.message_id == null ? {} : {
          reply_to_message: { message_id: payload.reply_parameters.message_id },
        }),
        } };
      }
      throw new Error(`onverwachte Telegram-API-call: ${method}`);
    });
    return Promise.resolve();
  };
  // startTelegram installeert alleen de duurzame outboxtimers. De test stuurt
  // de update zelf door grammY's echte middlewarestack en heeft geen scheduler nodig.
  globalThis.setTimeout = () => Object.freeze({ unref() {} });
  globalThis.setInterval = () => Object.freeze({ unref() {} });
  try {
    await telegram.startTelegram();
  } finally {
    Bot.prototype.start = originalStart;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.setInterval = originalSetInterval;
  }
  assert.ok(nativeBot, 'de echte Telegram-integratie heeft geen grammY Bot gestart');

  const chatId = 91001;
  const messageId = 7001;
  const text = 'Geef via de echte Telegram-ingang één kort antwoord.';
  const reply = 'Dit antwoord hoort exact bij het gearchiveerde Telegram-bericht.';
  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  const update = {
    update_id: 1,
    message: {
      message_id: messageId, date: 1_786_020_100,
      message_thread_id: 77,
      chat: { id: chatId, type: 'private', first_name: 'Jaap' },
      from: { id: 424242, is_bot: false, first_name: 'Jaap', language_code: 'nl' },
      text,
    },
  };
  globalThis.fetch = async (url) => {
    assert.equal(String(url).includes('/api/events/'), false);
    providerCalls += 1;
    return providerCalls === 1
      ? understandingResponse(text, reply, 'native-telegram-understanding')
      : replyResponse(reply);
  };
  try {
    await nativeBot.handleUpdate(update);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(providerCalls, 2);
  const delivered = db.prepare(`SELECT meta FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`).all(
    String(messageId),
  ).map((row) => JSON.parse(row.meta));
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].delivery.transport, 'telegram');
  assert.equal(delivered[0].delivery.accepted, true);
  const archived = telegramMessage(chatId, messageId);
  assert.equal(archived.role, 'user');
  assert.equal(archived.meta.fromTelegramId, '424242');
  assert.equal(archived.processing_status, 'completed');
  assert.equal(archived.processing_attempts, 1);
  const execution = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(
    String(messageId),
  );
  const source = db.prepare(`SELECT * FROM main.eva_turn_understanding_authorities_v1
    WHERE execution_key=?`).get(execution.execution_key);
  assert.equal(execution.status, 'done');
  assert.equal(source.channel, 'telegram');
  assert.equal(source.source_kind, 'telegram_ingress');
  assert.equal(source.source_generation, 1);
  assert.equal(typeof source.source_body_json, 'string');
  assert.equal(canonicalJson(JSON.parse(source.source_body_json)), source.source_body_json);
  assert.equal(createHash('sha256').update(source.source_body_json).digest('hex'),
    source.source_sha256);
  const durableBeforeArchiveMutation = turnUnderstandingAuthorityRecord(db, execution);
  assert.ok(durableBeforeArchiveMutation);
  archiveTelegramMessage({
    chatId, messageId,
    meta: { postIssueMutableProjection: 'mag append-only bronbewijs niet wijzigen' },
  });
  const durableAfterArchiveMutation = turnUnderstandingAuthorityRecord(db, execution);
  assert.equal(durableAfterArchiveMutation?.sourceBodyJson, source.source_body_json);
  const args = JSON.parse(execution.args);
  assert.equal(args.supersessionScope, `telegram:${createHash('sha256')
    .update(canonicalJson({
      contract: 'TelegramIngressSupersessionLane/v1',
      chatId: String(chatId), messageThreadId: '77', fromTelegramId: '424242',
    })).digest('hex')}`);
  assert.equal(args.supersessionToken, `telegram-message:${createHash('sha256')
    .update(canonicalJson({
      contract: 'TelegramIngressSupersessionToken/v1',
      messageId: String(messageId), sourceSha256: source.source_sha256,
    })).digest('hex')}`);

  // Een al afgeleverde historische done-uitvoer botst vóór een nieuwe claim:
  // de duurzame source blijft generatie 1 en attempts worden niet opgehoogd.
  const originalSourceMeta = JSON.parse(source.source_body_json).meta;
  db.prepare(`UPDATE main.telegram_messages SET processing_status='failed',meta=?
    WHERE chat_id=? AND message_id=?`).run(
    canonicalJson(originalSourceMeta), String(chatId), String(messageId),
  );
  const outboundBeforeHistoricalReplay = outboundSequence;
  providerCalls = 0;
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error('historische done-source mag provider noch replaydelivery bereiken');
  };
  try {
    await nativeBot.handleUpdate(update);
  } finally {
    globalThis.fetch = originalFetch;
  }
  const reopened = telegramMessage(chatId, messageId);
  assert.equal(providerCalls, 0);
  assert.equal(outboundSequence, outboundBeforeHistoricalReplay);
  assert.equal(reopened.processing_status, 'failed');
  assert.equal(reopened.processing_attempts, 1);
  const generations = db.prepare(`SELECT source_generation FROM
    main.eva_turn_understanding_authorities_v1 WHERE execution_key=?
    ORDER BY source_generation`).all(execution.execution_key);
  assert.deepEqual(generations.map((row) => row.source_generation), [1]);

  // Crash exact nadat de Prepared-sidecar duurzaam is, maar vóór een fysieke
  // acceptatie. De identieke Bot-update moet vóór de timer-outbox rechtstreeks
  // dezelfde generatie claimen, zonder provider of nieuwe understandingbron.
  const crashMessageId = 7002;
  const crashText = 'Hoe gaat het vandaag?';
  const crashReply = 'Goed, dank je. Ik hoop dat het met jou ook goed gaat.';
  const crashUpdate = {
    update_id: 2,
    message: {
      message_id: crashMessageId, date: 1_786_020_200,
      message_thread_id: 77,
      chat: { id: chatId, type: 'private', first_name: 'Jaap' },
      from: { id: 424242, is_bot: false, first_name: 'Jaap', language_code: 'nl' },
      text: crashText,
    },
  };
  let crashProviderCalls = 0;
  globalThis.fetch = async () => {
    crashProviderCalls += 1;
    return crashProviderCalls === 1
      ? understandingResponse(crashText, crashReply, 'native-telegram-crash-understanding')
      : replyResponse(crashReply);
  };
  failNextTelegramSend = true;
  try {
    await assert.rejects(nativeBot.handleUpdate(crashUpdate), /retry|crash|tijdelijk/iu);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.ok(crashProviderCalls >= 1 && crashProviderCalls <= 2);
  const failedPrepared = telegramMessage(chatId, crashMessageId);
  assert.equal(failedPrepared.processing_status, 'failed');
  assert.equal(failedPrepared.processing_attempts, 1);
  assert.ok(failedPrepared.meta?.preparedReply);
  const preparedCrashReply = String(failedPrepared.meta.preparedReply.payload?.reply || '');
  assert.ok(preparedCrashReply.length > 0);
  const crashExecution = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(
    String(crashMessageId),
  );
  const crashSource = turnUnderstandingAuthorityRecord(db, crashExecution);
  assert.equal(crashExecution.status, 'done');
  assert.equal(crashSource.sourceGeneration, 1);
  const sendsBeforeRecovery = telegramSendAttempts.filter(
    (body) => body === preparedCrashReply,
  ).length;
  await new Promise((resolve) => setTimeout(resolve, 1_050));
  crashProviderCalls = 0;
  globalThis.fetch = async () => {
    crashProviderCalls += 1;
    throw new Error('Prepared Bot-recovery mag de provider niet opnieuw bereiken');
  };
  try {
    await nativeBot.handleUpdate(crashUpdate);
  } finally {
    globalThis.fetch = originalFetch;
  }
  const recoveredPrepared = telegramMessage(chatId, crashMessageId);
  const recoveredSource = turnUnderstandingAuthorityRecord(db, crashExecution);
  assert.equal(crashProviderCalls, 0);
  assert.equal(recoveredPrepared.processing_status, 'completed');
  assert.equal(recoveredPrepared.processing_attempts, 1);
  assert.equal(recoveredSource.sourceGeneration, 1);
  assert.equal(recoveredSource.sourceSha256, crashSource.sourceSha256);
  assert.equal(recoveredSource.sourceBodyJson, crashSource.sourceBodyJson);
  assert.equal(telegramSendAttempts.filter((body) => body === preparedCrashReply).length,
    sendsBeforeRecovery + 1);
  assert.equal(db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`).get(
    String(crashMessageId),
  ).value, 1);
  assert.equal(pendingPreparedTelegramMessages(100).some((row) => (
    row.chat_id === String(chatId) && row.message_id === String(crashMessageId)
  )), false);
});

test('publieke voice-query en ACK minten geen authority, provider of delivery', async () => {
  const id = 'native-public-bus-voice-1';
  const text = 'Deze publieke businput is geen fysieke voice-ingang.';
  const satellite = 'assist_satellite.keuken';
  const originalFetch = globalThis.fetch;
  let providerCalls = 0;
  let replyOffers = 0;
  globalThis.fetch = async (url) => {
    if (String(url).includes('/api/events/eva_voice_reply')) {
      replyOffers += 1;
      return new Response('{}', { status: 200 });
    }
    providerCalls += 1;
    throw new Error('publieke voice-bus mag de provider niet bereiken');
  };
  try {
    voiceBridge.startVoiceBridge({ deliveryWindowMs: 5_000 });
    bus.emit('voice_query', {
      id, vraag: text, sender: 'Jaap', speaker_id: 'jaap',
      speaker_identity_verified: true, satellite,
      conversation_id: 'public-bus',
    });
    bus.emit('voice_reply_ack', { id, accepted: true, satellite });
    await new Promise((resolve) => setTimeout(resolve, 100));
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(providerCalls, 0);
  assert.equal(replyOffers, 0);
  const execution = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(id);
  assert.equal(execution, undefined);
  assert.equal(db.prepare(`SELECT count(*) AS value FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`).get(id).value, 0);
});
