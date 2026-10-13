import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-failed-telegram-handoff-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'failed-telegram-handoff-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:failed-telegram-test';
process.env.TELEGRAM_ALLOWED_IDS = '424242,434343';

await import('./telegram-test-composition.js');
const {
  archiveTelegramMessage,
  claimTelegramMessage,
  db,
  failTelegramMessage,
  telegramMessage,
} = await import('../src/db.js');
const { redactSecrets } = await import('../src/security/redaction.js');
const { canonicalJson } = await import('../src/util.js');
const {
  goalEpisodeById,
  interpretationRetryResume,
  recordEpisodeDelivery,
  transitionGoalEpisode,
} = await import('../src/mind/portfolio.js');
const telegram = await import('../src/integrations/telegram.js');
const {
  failTelegramMessageAndRecover,
  recoverFailedTelegramInterpretationHandoffs,
  sendTelegramText,
  setBotForTest,
} = telegram.__telegramTransportTest;
const { Bot } = await import('grammy');

after(() => {
  setBotForTest(null);
  if (db.open) db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const sha256 = (value) => createHash('sha256').update(String(value)).digest('hex');
let nextMessageId = 10_000;

function messageThread(meta) {
  return meta.messageThreadId == null ? '' : String(meta.messageThreadId);
}

function supersession(row) {
  const sourceBodyJson = canonicalJson({
    contract: 'TelegramArchivedUserEnvelope/v1',
    chatId: String(row.chat_id), messageId: String(row.message_id),
    sender: row.sender, role: row.role, kind: row.kind, content: row.content,
    replyToMessageId: row.reply_to_message_id == null ? null : String(row.reply_to_message_id),
    mediaRef: row.media_ref == null ? null : String(row.media_ref),
    fileId: row.file_id == null ? null : String(row.file_id),
    deleted: false, meta: row.meta,
  });
  const sourceSha256 = sha256(sourceBodyJson);
  return {
    sourceBodyJson,
    sourceSha256,
    scope: `telegram:${sha256(canonicalJson({
      contract: 'TelegramIngressSupersessionLane/v1',
      chatId: String(row.chat_id), messageThreadId: messageThread(row.meta),
      fromTelegramId: String(row.meta.fromTelegramId),
    }))}`,
    token: `telegram-message:${sha256(canonicalJson({
      contract: 'TelegramIngressSupersessionToken/v1',
      messageId: String(row.message_id), sourceSha256,
    }))}`,
  };
}

function archiveFailed({
  chatId = '81001', messageId = String(nextMessageId++), sender = 'Jaap',
  fromTelegramId = '424242', threadId = '77', kind = 'text', content = 'Pak dit later weer op.',
  replyToMessageId = null, mediaRef = null, fileId = null, preparedReply = null,
} = {}) {
  archiveTelegramMessage({
    chatId, messageId, ts: 1_786_100_000 + Number(messageId), sender, role: 'user',
    kind, content, replyToMessageId, mediaRef, fileId,
    meta: {
      inbound: true, fromTelegramId: String(fromTelegramId),
      ...(threadId == null ? {} : { messageThreadId: String(threadId) }),
      ...(kind === 'text' ? {} : {
        fileUniqueId: `unique-${messageId}`, mimeType: 'image/jpeg',
      }),
      ...(preparedReply ? { preparedReply } : {}),
    },
  });
  const claim = claimTelegramMessage(chatId, messageId);
  assert.equal(claim.claimed, true);
  assert.equal(failTelegramMessage(chatId, messageId, 'provider stopte onverwacht'), true);
  return telegramMessage(chatId, messageId);
}

function archiveProcessing(options = {}) {
  const row = archiveFailed(options);
  db.prepare(`UPDATE telegram_messages SET processing_status='processing',
    processing_error=NULL WHERE chat_id=? AND message_id=?`).run(row.chat_id, row.message_id);
  return telegramMessage(row.chat_id, row.message_id);
}

function seedTelegramAuthority(row, { originalText = row.content, mutateBody = null } = {}) {
  const identity = {
    channel: 'telegram', conversationId: `telegram:${row.chat_id}`,
    messageId: String(row.message_id),
  };
  const source = supersession(row);
  let sourceBody = JSON.parse(source.sourceBodyJson);
  if (typeof mutateBody === 'function') sourceBody = mutateBody(sourceBody);
  const sourceBodyJson = canonicalJson(sourceBody);
  const sourceSha256 = sha256(sourceBodyJson);
  const args = {
    ...identity, requester: row.sender, originalText,
    sessionRef: `fixture:${row.chat_id}:${row.message_id}`,
    supersessionScope: source.scope, supersessionToken: source.token,
    syntheticTest: false,
  };
  const storedArgsValue = redactSecrets(args);
  const storedArgs = canonicalJson(storedArgsValue);
  const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
  const turnId = `ingress:telegram:${identity.conversationId}:${identity.messageId}`;
  const tool = '@turn/understanding';
  const executionSeq = Number(db.prepare(
    'SELECT coalesce(max(execution_seq),0)+1 AS value FROM turn_tool_executions',
  ).get().value);
  const semanticHash = sha256(canonicalJson({ turnId, tool, args, binding: null }));
  const intentRecordSha256 = sha256(canonicalJson({
    executionContract: 'ExecutionRecord/v2', executionSeq,
    turnId, tool, args: storedArgsValue, binding: null, semanticHash,
  }));
  const issuedTs = new Date().toISOString();
  db.prepare(`INSERT INTO turn_tool_executions (
    execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
    semantic_binding,intent_record_sha256,execution_seq,execution_contract,
    execution_verification
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    executionKey, turnId, tool, storedArgs, 'started', issuedTs, semanticHash,
    null, intentRecordSha256, executionSeq, 'ExecutionRecord/v2', 'verified',
  );
  const sourceRef = `telegram-ingress:${sha256(canonicalJson({
    contract: 'TelegramIngressIdentity/v1', chatId: String(row.chat_id),
    messageId: String(row.message_id), fromTelegramId: String(row.meta.fromTelegramId),
  }))}`;
  const authority = {
    contract: 'TurnUnderstandingExecutionAuthority/v1',
    execution_key: executionKey, execution_seq: executionSeq,
    intent_record_sha256: intentRecordSha256, channel: 'telegram',
    conversation_id: identity.conversationId, message_id: identity.messageId,
    source_kind: 'telegram_ingress', source_ref: sourceRef,
    source_sha256: sourceSha256, source_body_json: sourceBodyJson,
    source_generation: Number(row.processing_attempts), authority_sha256: '', issued_ts: issuedTs,
  };
  authority.authority_sha256 = sha256(canonicalJson({
    contract: authority.contract, executionKey, executionSeq, intentRecordSha256,
    channel: 'telegram', conversationId: identity.conversationId,
    messageId: identity.messageId, sourceKind: authority.source_kind,
    sourceRef, sourceSha256, sourceBodyJson,
    sourceGeneration: authority.source_generation, issuedTs,
    turnId, tool, args: storedArgs,
  }));
  db.prepare(`INSERT INTO eva_turn_understanding_authorities_v1 (
    contract,execution_key,execution_seq,intent_record_sha256,channel,
    conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,
    source_generation,authority_sha256,issued_ts
  ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
    @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,@source_body_json,
    @source_generation,@authority_sha256,@issued_ts)`).run(authority);
  return { executionKey, source };
}

function origin(chatId, messageId) {
  return db.prepare(`SELECT o.*,e.work_item_id,w.visible_to_family
    FROM eva_episode_origins o
    JOIN eva_goal_episodes e ON e.id=o.episode_id
    JOIN eva_work_items w ON w.id=e.work_item_id
    WHERE o.origin_channel='telegram' AND o.origin_conversation_id=?
      AND o.origin_message_id=?`).get(`telegram:${chatId}`, String(messageId));
}

function retryFor(row) {
  const bound = origin(row.chat_id, row.message_id);
  assert.ok(bound);
  const item = db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(bound.work_item_id);
  const episode = db.prepare('SELECT * FROM eva_goal_episodes WHERE id=?').get(bound.episode_id);
  return interpretationRetryResume(item, episode);
}

function reservedUnderstandingKey(row) {
  return `turn-understanding:${sha256(canonicalJson({
    channel: 'telegram', conversationId: `telegram:${row.chat_id}`,
    messageId: String(row.message_id),
  }))}`;
}

function seedReservedExecutionCollision(row, {
  tool = 'lees_bestand', status = 'started',
} = {}) {
  const executionKey = reservedUnderstandingKey(row);
  const turnId = `collision:${row.chat_id}:${row.message_id}`;
  const argsValue = { fixture: 'reserved-key-collision' };
  const args = canonicalJson(argsValue);
  const executionSeq = Number(db.prepare(
    'SELECT coalesce(max(execution_seq),0)+1 AS value FROM turn_tool_executions',
  ).get().value);
  const semanticHash = sha256(canonicalJson({
    turnId, tool, args: argsValue, binding: null,
  }));
  const intentRecordSha256 = sha256(canonicalJson({
    executionContract: 'ExecutionRecord/v2', executionSeq,
    turnId, tool, args: argsValue, binding: null, semanticHash,
  }));
  db.prepare(`INSERT INTO turn_tool_executions (
    execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
    semantic_binding,intent_record_sha256,execution_seq,execution_contract,
    execution_verification
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    executionKey, turnId, tool, args, status, new Date().toISOString(), semanticHash,
    null, intentRecordSha256, executionSeq, 'ExecutionRecord/v2', 'verified',
  );
  return executionKey;
}

function setRecoveryCursorBefore(row) {
  const id = Number(db.prepare(`SELECT id FROM telegram_messages
    WHERE chat_id=? AND message_id=?`).get(row.chat_id, row.message_id)?.id);
  assert.equal(Number.isSafeInteger(id) && id > 0, true);
  db.prepare(`UPDATE eva_failed_telegram_recovery_scan_cursor_v1
    SET last_telegram_row_id=?,updated_ts=?
    WHERE scan_key='episode_less_failed_ingress_v1'`).run(
    id - 1, new Date().toISOString(),
  );
}

test('scanner maakt episode-loze failed text exact eenmaal en verborgen hervatbaar', () => {
  const row = archiveFailed({ content: 'Controleer straks nog even de voordeur.' });
  assert.equal(origin(row.chat_id, row.message_id), undefined);
  const first = recoverFailedTelegramInterpretationHandoffs();
  assert.equal(first.materialized, 1);
  const created = origin(row.chat_id, row.message_id);
  assert.ok(created);
  assert.equal(created.visible_to_family, 0);
  assert.equal(telegramMessage(row.chat_id, row.message_id).processing_status, 'failed');
  const counts = {
    work: db.prepare('SELECT count(*) n FROM eva_work_items').get().n,
    episodes: db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n,
  };
  assert.equal(recoverFailedTelegramInterpretationHandoffs().materialized, 0);
  assert.deepEqual({
    work: db.prepare('SELECT count(*) n FROM eva_work_items').get().n,
    episodes: db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n,
  }, counts);
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    await import('./test/telegram-test-composition.js');
    const telegram = await import('./src/integrations/telegram.js');
    const { db } = await import('./src/db.js');
    const result = telegram.__telegramTransportTest.recoverFailedTelegramInterpretationHandoffs();
    process.stdout.write('FAILED_TELEGRAM_RESTART=' + JSON.stringify(result) + '\\n');
    db.close();
  `], {
    cwd: process.cwd(), env: { ...process.env, EVA_DATA_DIR: dataDir }, encoding: 'utf8',
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const restartResult = restarted.stdout.match(/FAILED_TELEGRAM_RESTART=(\{.*\})/u);
  assert.ok(restartResult, restarted.stdout);
  assert.equal(JSON.parse(restartResult[1]).materialized, 0);
});

test('echte failed overgang en startup roepen de begrensde backfill direct aan', async () => {
  const transitioned = archiveProcessing({
    chatId: '81101', threadId: '70', content: 'Herstel mij direct na de foutovergang.',
  });
  assert.equal(failTelegramMessageAndRecover(
    transitioned.chat_id, transitioned.message_id, 'echte handlerfout',
  ), true);
  assert.ok(origin(transitioned.chat_id, transitioned.message_id));
  assert.equal(telegramMessage(transitioned.chat_id, transitioned.message_id).processing_status, 'failed');

  const startup = archiveFailed({
    chatId: '81102', threadId: '71', content: 'Herstel mij eenmalig bij startup.',
  });
  const originalStart = Bot.prototype.start;
  const originalSetTimeout = globalThis.setTimeout;
  const originalSetInterval = globalThis.setInterval;
  Bot.prototype.start = function startWithoutPolling() { return Promise.resolve(); };
  globalThis.setTimeout = () => Object.freeze({ unref() {} });
  globalThis.setInterval = () => Object.freeze({ unref() {} });
  try {
    await telegram.startTelegram();
  } finally {
    Bot.prototype.start = originalStart;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.setInterval = originalSetInterval;
  }
  assert.ok(origin(startup.chat_id, startup.message_id));
  assert.equal(telegramMessage(startup.chat_id, startup.message_id).processing_status, 'failed');
});

test('authority herstelt replycontext, maar reply/media zonder exact authority faalt gesloten', () => {
  const replied = archiveFailed({
    content: 'Ja, doe dat.', replyToMessageId: '9000', messageId: String(nextMessageId++),
  });
  const originalText = `[Jaap reageert op mijn eerdere bericht: "Zal ik de deur controleren?"]\nJaap: Ja, doe dat.`;
  seedTelegramAuthority(replied, { originalText });
  assert.equal(recoverFailedTelegramInterpretationHandoffs().materialized, 1);
  assert.equal(JSON.parse(db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(origin(replied.chat_id, replied.message_id).work_item_id).state_json)
    .interpretation_retry.originalText, originalText);

  const noAuthorityReply = archiveFailed({ content: 'Doe dat.', replyToMessageId: '9001' });
  const noAuthorityPhoto = archiveFailed({
    kind: 'photo', content: 'Wat zie je?', mediaRef: 'photo:local', fileId: 'photo-file',
  });
  const forged = archiveFailed({ content: 'Dit authoritybewijs hoort niet bij deze bron.' });
  seedTelegramAuthority(forged, {
    mutateBody: (body) => ({ ...body, content: 'Andere, vervalste inhoud.' }),
  });
  recoverFailedTelegramInterpretationHandoffs();
  assert.equal(origin(noAuthorityReply.chat_id, noAuthorityReply.message_id), undefined);
  assert.equal(origin(noAuthorityPhoto.chat_id, noAuthorityPhoto.message_id), undefined);
  assert.equal(origin(forged.chat_id, forged.message_id), undefined);
});

test('principal, prepared reply, bestaand antwoord en same-sender-thread supersession sluiten af', () => {
  const unauthorized = archiveFailed({ fromTelegramId: '999999' });
  const prepared = archiveFailed({ preparedReply: { contract: 'PreparedReply/v1' } });
  const answered = archiveFailed({ content: 'Is dit al beantwoord?' });
  archiveTelegramMessage({
    chatId: answered.chat_id, messageId: String(nextMessageId++), sender: 'Eva', role: 'assistant',
    kind: 'text', content: 'Ja.', replyToMessageId: answered.message_id,
    meta: { outbound: true }, processingStatus: 'completed',
  });
  const superseded = archiveFailed({ content: 'Doe de eerste versie.' });
  archiveTelegramMessage({
    chatId: superseded.chat_id, messageId: String(Number(superseded.message_id) + 1),
    sender: superseded.sender, role: 'user', kind: 'text', content: 'Nee, doe de correctie.',
    meta: { inbound: true, fromTelegramId: superseded.meta.fromTelegramId,
      messageThreadId: superseded.meta.messageThreadId },
  });
  recoverFailedTelegramInterpretationHandoffs();
  for (const row of [unauthorized, prepared, answered, superseded]) {
    assert.equal(origin(row.chat_id, row.message_id), undefined);
  }
});

test('nieuwere andere chat, sender of thread lekt niet over de oorsprong heen', () => {
  const crossChat = archiveFailed({ chatId: '82001', threadId: '80' });
  archiveTelegramMessage({
    chatId: '82002', messageId: String(Number(crossChat.message_id) + 1), sender: 'Jaap', role: 'user',
    kind: 'text', content: 'Andere chat.', meta: { inbound: true, fromTelegramId: '424242', messageThreadId: '80' },
  });
  const otherSender = archiveFailed({ chatId: '82003', threadId: '81' });
  archiveTelegramMessage({
    chatId: otherSender.chat_id, messageId: String(Number(otherSender.message_id) + 1), sender: 'Sanne', role: 'user',
    kind: 'text', content: 'Andere afzender.', meta: { inbound: true, fromTelegramId: '434343', messageThreadId: '81' },
  });
  const otherThread = archiveFailed({ chatId: '82004', threadId: '82' });
  archiveTelegramMessage({
    chatId: otherThread.chat_id, messageId: String(Number(otherThread.message_id) + 1), sender: 'Jaap', role: 'user',
    kind: 'text', content: 'Andere thread.', meta: { inbound: true, fromTelegramId: '424242', messageThreadId: '83' },
  });
  const result = recoverFailedTelegramInterpretationHandoffs();
  assert.equal(result.materialized, 3);
  for (const row of [crossChat, otherSender, otherThread]) assert.ok(origin(row.chat_id, row.message_id));
});

test('resume supersession blijft exact aan afzender, chat, thread en bronhash gebonden', () => {
  const otherSender = archiveFailed({ chatId: '82501', threadId: '110' });
  const otherThread = archiveFailed({ chatId: '82502', threadId: '111' });
  const otherChat = archiveFailed({ chatId: '82503', threadId: '112' });
  const sameLane = archiveFailed({ chatId: '82504', threadId: '113' });
  const corruptedScope = archiveFailed({ chatId: '82505', threadId: '114' });
  recoverFailedTelegramInterpretationHandoffs();

  archiveTelegramMessage({
    chatId: otherSender.chat_id, messageId: String(Number(otherSender.message_id) + 50),
    sender: 'Sanne', role: 'user', kind: 'text', content: 'Andere afzender.',
    meta: { inbound: true, fromTelegramId: '434343', messageThreadId: '110' },
  });
  archiveTelegramMessage({
    chatId: otherThread.chat_id, messageId: String(Number(otherThread.message_id) + 50),
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Andere thread.',
    meta: { inbound: true, fromTelegramId: '424242', messageThreadId: '999' },
  });
  archiveTelegramMessage({
    chatId: '99982503', messageId: String(Number(otherChat.message_id) + 50),
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Andere chat.',
    meta: { inbound: true, fromTelegramId: '424242', messageThreadId: '112' },
  });
  archiveTelegramMessage({
    chatId: sameLane.chat_id, messageId: String(Number(sameLane.message_id) + 50),
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Zelfde lane corrigeert.',
    meta: { inbound: true, fromTelegramId: '424242', messageThreadId: '113' },
  });
  archiveTelegramMessage({
    chatId: corruptedScope.chat_id, messageId: corruptedScope.message_id,
    meta: { inbound: true, fromTelegramId: '424242', messageThreadId: 'changed-after-handoff' },
  });

  assert.equal(retryFor(otherSender).stopReason, null);
  assert.equal(retryFor(otherThread).stopReason, null);
  assert.equal(retryFor(otherChat).stopReason, null);
  assert.equal(retryFor(sameLane).stopReason, 'superseded');
  assert.equal(retryFor(corruptedScope).stopReason, 'superseded');
});

test('iedere reserved-key of store-authority collision blokkeert text fallback', () => {
  const wrongTool = archiveFailed({ chatId: '82601', threadId: '120' });
  seedReservedExecutionCollision(wrongTool);

  const invalidStatus = archiveFailed({ chatId: '82602', threadId: '121' });
  seedReservedExecutionCollision(invalidStatus, {
    tool: '@turn/understanding', status: 'invalid_fixture_status',
  });

  const orphanAuthority = archiveFailed({ chatId: '82603', threadId: '122' });
  const orphan = seedTelegramAuthority(orphanAuthority);
  setRecoveryCursorBefore(wrongTool);
  db.exec('SAVEPOINT orphan_authority_collision');
  let reservedCollisionsBlocked = false;
  try {
    db.exec('DROP TRIGGER turn_tool_executions_no_delete');
    db.prepare('DELETE FROM turn_tool_executions WHERE execution_key=?').run(orphan.executionKey);
    recoverFailedTelegramInterpretationHandoffs();
    reservedCollisionsBlocked = [wrongTool, invalidStatus, orphanAuthority]
      .every((row) => origin(row.chat_id, row.message_id) == null);
  } finally {
    db.exec('ROLLBACK TO orphan_authority_collision');
    db.exec('RELEASE orphan_authority_collision');
  }
  assert.equal(reservedCollisionsBlocked, true);

  const corruptStore = archiveFailed({ chatId: '82604', threadId: '123' });
  setRecoveryCursorBefore(corruptStore);
  db.exec('SAVEPOINT corrupt_authority_store');
  let corruptStoreBlocked = false;
  try {
    db.exec(`ALTER TABLE eva_turn_understanding_authorities_v1
      RENAME TO eva_turn_understanding_authorities_v1_corrupt_fixture`);
    recoverFailedTelegramInterpretationHandoffs();
    corruptStoreBlocked = origin(corruptStore.chat_id, corruptStore.message_id) == null;
  } finally {
    db.exec('ROLLBACK TO corrupt_authority_store');
    db.exec('RELEASE corrupt_authority_store');
  }
  assert.equal(corruptStoreBlocked, true);

  assert.equal(origin(wrongTool.chat_id, wrongTool.message_id), undefined);
  assert.equal(origin(invalidStatus.chat_id, invalidStatus.message_id), undefined);
  assert.equal(origin(orphanAuthority.chat_id, orphanAuthority.message_id), undefined);
  assert.equal(origin(corruptStore.chat_id, corruptStore.message_id), undefined);
});

test('scanner materialiseert maximaal 8 kandidaten per run zonder rijen over te slaan', () => {
  const eligible = Array.from({ length: 10 }, (_, index) => archiveFailed({
    chatId: '83003', threadId: String(200 + index), content: `Geldige herstelopdracht ${index}`,
  }));
  setRecoveryCursorBefore(eligible[0]);
  assert.equal(recoverFailedTelegramInterpretationHandoffs().materialized, 8);
  assert.equal(eligible.filter((row) => origin(row.chat_id, row.message_id)).length, 8);
  const second = recoverFailedTelegramInterpretationHandoffs();
  assert.equal(second.materialized >= 2 && second.materialized <= 8, true);
  assert.equal(eligible.filter((row) => origin(row.chat_id, row.message_id)).length, 10);
});

test('durable keysetcursor bereikt na restart een geldige rij achter 65 permanente blockers', () => {
  let firstBlocker = null;
  for (let index = 0; index < 65; index += 1) {
    const blocker = archiveFailed({
      chatId: '83501', fromTelegramId: '999999', threadId: String(500 + index),
      content: `Permanente blocker ${index}`,
    });
    firstBlocker ||= blocker;
  }
  const behindBlockers = archiveFailed({
    chatId: '83502', threadId: '700', content: 'Deze geldige rij moet na cursorwrap bereikbaar zijn.',
  });
  setRecoveryCursorBefore(firstBlocker);
  const first = recoverFailedTelegramInterpretationHandoffs();
  assert.equal(first.scanned, 64);
  assert.equal(origin(behindBlockers.chat_id, behindBlockers.message_id), undefined);

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    await import('./test/telegram-test-composition.js');
    const telegram = await import('./src/integrations/telegram.js');
    const { db } = await import('./src/db.js');
    const result = telegram.__telegramTransportTest.recoverFailedTelegramInterpretationHandoffs();
    const origin = db.prepare(\`SELECT count(*) n FROM eva_episode_origins
      WHERE origin_channel='telegram' AND origin_conversation_id='telegram:83502'
        AND origin_message_id=?\`).get(${JSON.stringify(String(nextMessageId - 1))}).n;
    process.stdout.write('FAILED_CURSOR_RESTART=' + JSON.stringify({ result, origin }) + '\\n');
    db.close();
  `], {
    cwd: process.cwd(), env: { ...process.env, EVA_DATA_DIR: dataDir }, encoding: 'utf8',
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const output = restarted.stdout.match(/FAILED_CURSOR_RESTART=(\{.*\})/u);
  assert.ok(output, restarted.stdout);
  const recovered = JSON.parse(output[1]);
  assert.equal(recovered.result.scanned <= 64, true);
  assert.equal(recovered.origin, 1);
  assert.equal(recoverFailedTelegramInterpretationHandoffs().materialized, 0);
  assert.equal(origin(behindBlockers.chat_id, behindBlockers.message_id) != null, true);
});

test('failed rij wordt pas completed na exact geaccepteerd transportreceipt', async () => {
  const row = archiveFailed({ chatId: '84001', threadId: '91', content: 'Bezorg dit resultaat later.' });
  setRecoveryCursorBefore(row);
  recoverFailedTelegramInterpretationHandoffs();
  const created = origin(row.chat_id, row.message_id);
  assert.ok(created);
  transitionGoalEpisode(created.episode_id, {
    state: 'delivery_pending', checkpoint: 'Resultaat is gecontroleerd.',
    proofSummary: 'Resultaat is gecontroleerd.', nextAction: 'Bezorg het resultaat.',
    work: { evidence: 'Resultaat is gecontroleerd.', lastAction: 'resultaat_gecontroleerd' },
  });
  assert.equal(telegramMessage(row.chat_id, row.message_id).processing_status, 'failed');

  const reply = 'Ik heb het gecontroleerde resultaat nu bezorgd.';
  let sentId = 90_000;
  setBotForTest({ api: { async sendMessage(chatId, text, options) {
    return {
      message_id: ++sentId, date: 1_786_200_000, chat: { id: Number(chatId) }, text,
      message_thread_id: options.message_thread_id,
      reply_to_message: { message_id: options.reply_parameters.message_id },
    };
  } } });
  const sent = await sendTelegramText(row.chat_id, reply, {
    replyToMessageId: row.message_id, messageThreadId: row.meta.messageThreadId,
    dedupeKey: `failed-handoff-delivery:${created.episode_id}`,
    directTurnSignalRef: `turn:telegram:${row.chat_id}:${row.message_id}`,
    originPrincipal: `telegram:${row.meta.fromTelegramId}`,
  });
  assert.equal(telegramMessage(row.chat_id, row.message_id).processing_status, 'failed');
  const delivery = {
    ok: true, accepted: true, transport: 'telegram', kind: 'text',
    sent: sent.map((message) => ({
      chatId: String(message.chat.id), messageId: String(message.message_id),
      ...message._evaTransport,
    })),
  };
  const receipt = recordEpisodeDelivery(created.episode_id, {
    deliveredChannel: 'telegram', deliveryTarget: row.sender,
    originConversationId: `telegram:${row.chat_id}`, originMessageId: row.message_id,
    deliveredConversationId: `telegram:${row.chat_id}`, resultText: reply,
    proofSummary: 'Resultaat is gecontroleerd.',
    idempotencyKey: `failed-handoff-receipt:${created.episode_id}`, transport: delivery,
  });
  assert.ok(receipt);
  assert.equal(telegramMessage(row.chat_id, row.message_id).processing_status, 'completed');
  assert.equal(goalEpisodeById(created.episode_id).state, 'closed');
});
