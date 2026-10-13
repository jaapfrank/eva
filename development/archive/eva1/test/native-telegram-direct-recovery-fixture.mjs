import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const [phase, dataDir, variant, originMode = 'exact'] = process.argv.slice(2);
if (!['prepare-crash', 'recover'].includes(phase) || !dataDir
  || !['reply', 'photo', 'video', 'document'].includes(variant)
  || !['exact', 'zero', 'multiple', 'mismatch'].includes(originMode)) {
  throw new Error('native Telegram direct-recoveryfixture mist fase, datamap of variant');
}
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'native-telegram-direct-recovery-test-only';
process.env.TELEGRAM_BOT_TOKEN = '123456789:native-telegram-direct-recovery-test';
process.env.TELEGRAM_ALLOWED_IDS = '424242';

const { Bot } = await import('grammy');
const ffmpegPath = (await import('ffmpeg-static')).default;
const telegram = await import('../src/integrations/telegram.js');
const { canonicalJson } = await import('../src/util.js');
const { db, telegramMessage } = await import('../src/db.js');
const { turnUnderstandingAuthorityRecord } = await import('../src/turnauthority.js');
const portfolio = await import('../src/mind/portfolio.js');

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const variantIndex = ['reply', 'photo', 'video', 'document'].indexOf(variant) + 1;
const chatId = 93_000 + variantIndex;
const messageId = 82_000 + variantIndex;
const conversationId = `telegram:${chatId}`;
const requester = 'Jaap';
const proofPath = `data/workspace/telegram-direct-${variant}-proof.txt`;
const reply = `Het ${variant}-werk is gecontroleerd en gereed.`;
const instruction = `Lees ${proofPath} en rond het Telegram-werk af.`;

const commonMessage = {
  message_id: messageId, date: 1_786_030_400 + variantIndex, message_thread_id: 95,
  chat: { id: chatId, type: 'private', first_name: requester },
  from: { id: 424242, is_bot: false, first_name: requester, language_code: 'nl' },
};
const messages = {
  reply: {
    ...commonMessage,
    text: instruction,
    reply_to_message: {
      message_id: messageId - 100, date: 1_786_030_000,
      chat: commonMessage.chat,
      from: { id: 123456789, is_bot: true, first_name: 'Eva Test' },
      text: 'Dit eerdere antwoord is exact lokaal gearchiveerd.',
    },
  },
  photo: {
    ...commonMessage,
    caption: instruction,
    photo: [{
      file_id: `photo-file-${variantIndex}`, file_unique_id: `photo-unique-${variantIndex}`,
      file_size: 64, width: 8, height: 8,
    }],
  },
  video: {
    ...commonMessage,
    caption: instruction,
    video: {
      file_id: `video-file-${variantIndex}`, file_unique_id: `video-unique-${variantIndex}`,
      file_size: 1_024, width: 16, height: 16, duration: 1, mime_type: 'video/mp4',
    },
  },
  document: {
    ...commonMessage,
    caption: instruction,
    document: {
      file_id: `document-file-${variantIndex}`,
      file_unique_id: `document-unique-${variantIndex}`,
      file_name: 'bewijs.mp4', file_size: 1_024, mime_type: 'video/mp4',
    },
  },
};
const update = { update_id: 900 + variantIndex, message: messages[variant] };

function completedModelResponse(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function replyResponse(text) {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text }],
  }]);
}

function requestStrings(value, result = []) {
  if (typeof value === 'string') result.push(value);
  else if (Array.isArray(value)) value.forEach((item) => requestStrings(item, result));
  else if (value && typeof value === 'object') {
    Object.values(value).forEach((item) => requestStrings(item, result));
  }
  return result;
}

function understandingResponse(turnText, workItemId) {
  const readText = `Lees ${proofPath}`;
  const closeText = 'rond het Telegram-werk af';
  const readStart = turnText.indexOf(readText);
  const closeStart = turnText.indexOf(closeText);
  assert.ok(readStart >= 0 && closeStart >= 0, turnText);
  return completedModelResponse([{
    type: 'function_call', call_id: `telegram-direct-${variant}-understanding`,
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'continue',
      goal: { statement: 'Rond het bestaande Telegram-werk af.', outcome: reply, beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: turnText.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{
        capabilityId: 'workspace.file.read', operationId: 'read', polarity: 'request',
        semanticInput: { pad: proofPath },
        sourceSpans: [{ start: readStart, end: readStart + readText.length }],
      }, {
        capabilityId: 'work.episode.manage', operationId: 'afronden', polarity: 'request',
        semanticInput: { id: workItemId, resultaat: reply },
        sourceSpans: [{ start: closeStart, end: closeStart + closeText.length }],
      }],
      requestedEffects: ['read', 'persistent'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
    }),
  }]);
}

let nativeBot = null;
let sends = 0;
let chatActions = 0;
let getFileCalls = 0;
const originalStart = Bot.prototype.start;
const originalSetTimeout = globalThis.setTimeout;
const originalSetInterval = globalThis.setInterval;
Bot.prototype.start = function startWithoutPolling() {
  nativeBot = this;
  this.botInfo = {
    id: 123456789, is_bot: true, first_name: 'Eva Test', username: 'eva_test_bot',
    can_join_groups: true, can_read_all_group_messages: false,
    supports_inline_queries: false,
  };
  this.api.config.use(async (_previous, method, payload) => {
    if (method === 'sendChatAction') {
      chatActions += 1;
      return { ok: true, result: true };
    }
    if (method === 'getFile') {
      getFileCalls += 1;
      return { ok: true, result: {
        file_id: String(payload.file_id), file_unique_id: `unique-${variant}`,
        file_size: 1_024, file_path: `native/${variant}.bin`,
      } };
    }
    if (method === 'sendMessage') {
      sends += 1;
      return { ok: true, result: {
        message_id: 96_000 + sends, date: 1_786_030_800,
        chat: { id: Number(payload.chat_id), type: 'private', first_name: requester },
        from: this.botInfo, text: String(payload.text || ''),
        message_thread_id: payload.message_thread_id,
        reply_to_message: { message_id: payload.reply_parameters?.message_id },
      } };
    }
    throw new Error(`onverwachte Telegram-API-call: ${method}`);
  });
  return Promise.resolve();
};
globalThis.setTimeout = () => Object.freeze({ unref() {} });
globalThis.setInterval = () => Object.freeze({ unref() {} });
try {
  await telegram.startTelegram();
} finally {
  Bot.prototype.start = originalStart;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.setInterval = originalSetInterval;
}
assert.ok(nativeBot);

const executionRow = () => db.prepare(`SELECT * FROM main.turn_tool_executions
  WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(
  String(messageId),
);
const generations = () => {
  const execution = executionRow();
  return execution ? db.prepare(`SELECT source_generation FROM
    main.eva_turn_understanding_authorities_v1 WHERE execution_key=?
    ORDER BY source_generation`).all(execution.execution_key)
    .map((row) => Number(row.source_generation)) : [];
};
const receiptCount = () => Number(db.prepare(`SELECT count(*) AS value FROM main.timeline
  WHERE kind='chat_out' AND json_extract(meta,'$.originMessageId')=?`)
  .get(String(messageId)).value);

let providerCalls = 0;
let visionCalls = 0;
let downloadFetches = 0;
let mediaBytes = Buffer.from('native-photo-bytes');
let created = null;
let recoveryError = null;
if (phase === 'prepare-crash') {
  created = portfolio.createDurableWork({
    source: 'telegram', owner: requester, title: `Telegram ${variant} direct herstel`,
    goal: reply,
  }, {
    originKind: 'user', originChannel: 'telegram',
    originConversationId: conversationId, originMessageId: `seed-${messageId}`,
    requester, deliveryPolicy: 'required', deliveryTarget: requester,
  });
  fs.mkdirSync(path.join(dataDir, 'workspace'), { recursive: true });
  fs.writeFileSync(path.join(dataDir, 'workspace', `telegram-direct-${variant}-proof.txt`),
    `${reply}\n`);
  if (['video', 'document'].includes(variant)) {
    const videoPath = path.join(dataDir, `native-${variant}.mp4`);
    execFileSync(ffmpegPath, [
      '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
      '-i', 'color=c=blue:s=16x16:d=1', '-pix_fmt', 'yuv420p', videoPath,
    ], { timeout: 10_000 });
    mediaBytes = fs.readFileSync(videoPath);
  }
  let understandingDone = false;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).includes('api.telegram.org/file/bot')) {
      downloadFetches += 1;
      return new Response(mediaBytes, { status: 200 });
    }
    providerCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const understanding = body.tools?.some((tool) => tool?.name === 'commit_turn_understanding');
    if (understanding) {
      const candidates = requestStrings(body.input)
        .filter((text) => text.includes(proofPath) && text.includes('rond het Telegram-werk af'))
        .sort((left, right) => left.length - right.length);
      assert.ok(candidates[0], canonicalJson(body.input).slice(0, 2_000));
      understandingDone = true;
      return understandingResponse(candidates[0], created.workItem.id);
    }
    if (['video', 'document'].includes(variant) && !understandingDone) {
      visionCalls += 1;
      return replyResponse('Een stil blauw testbeeld zonder aanvullende details.');
    }
    return replyResponse(reply);
  };
  db.exec(`CREATE TEMP TRIGGER abort_native_telegram_direct_prepared_sidecar
    BEFORE UPDATE OF meta ON main.telegram_messages
    WHEN OLD.chat_id='${chatId}' AND OLD.message_id='${messageId}'
      AND json_extract(OLD.meta,'$.preparedReply') IS NULL
      AND json_type(NEW.meta,'$.preparedReply')='object'
    BEGIN SELECT RAISE(ABORT,'forced direct-recovery Telegram sidecar crash'); END`);
  await assert.rejects(nativeBot.handleUpdate(update),
    /forced direct-recovery Telegram sidecar crash/u);
} else {
  const exactOrigin = db.prepare(`SELECT * FROM main.eva_episode_origins
    WHERE origin_channel='telegram' AND origin_conversation_id=? AND origin_message_id=?`)
    .get(conversationId, String(messageId));
  assert.ok(exactOrigin, 'actuele Telegram-episodeoorsprong ontbreekt vóór fixturemutatie');
  if (originMode === 'zero') {
    db.prepare('DELETE FROM main.eva_episode_origins WHERE id=?').run(exactOrigin.id);
  } else if (originMode === 'multiple') {
    db.prepare(`INSERT INTO main.eva_episode_origins (
      episode_id,origin_key,origin_kind,origin_channel,origin_conversation_id,
      origin_message_id,requester,attached_ts
    ) VALUES (?,?,?,?,?,?,?,?)`).run(
      exactOrigin.episode_id, `${exactOrigin.origin_key}|fixture-duplicate`,
      exactOrigin.origin_kind, exactOrigin.origin_channel,
      exactOrigin.origin_conversation_id, exactOrigin.origin_message_id,
      exactOrigin.requester, exactOrigin.attached_ts,
    );
  } else if (originMode === 'mismatch') {
    db.prepare(`UPDATE main.eva_episode_origins SET origin_message_id=? WHERE id=?`)
      .run(`mismatch-${messageId}`, exactOrigin.id);
  }
  const exactOriginCount = Number(db.prepare(`SELECT count(*) AS value
    FROM main.eva_episode_origins WHERE origin_channel='telegram'
      AND origin_conversation_id=? AND origin_message_id=?`)
    .get(conversationId, String(messageId)).value);
  assert.equal(exactOriginCount, {
    exact: 1, zero: 0, multiple: 2, mismatch: 0,
  }[originMode]);
  globalThis.fetch = async (url) => {
    if (String(url).includes('api.telegram.org/file/bot')) downloadFetches += 1;
    else providerCalls += 1;
    throw new Error('direct Telegram-herstel mag geen download, vision of provider bereiken');
  };
  try {
    await nativeBot.handleUpdate(update);
    await nativeBot.handleUpdate(update);
  } catch (error) {
    recoveryError = String(error?.message || error);
  }
}

const archived = telegramMessage(chatId, messageId);
const execution = executionRow();
const prepared = (() => {
  try { return JSON.parse(String(execution?.output || 'null')); } catch { return null; }
})();
const episodeId = Number(prepared?.goalEpisodeId);
const workItemId = Number(prepared?.workItemId);
const report = {
  phase, variant, originMode,
  status: archived?.processing_status || null,
  attempts: Number(archived?.processing_attempts || 0),
  prepared: Boolean(archived?.meta?.preparedReply),
  executionStatus: execution?.status || null,
  generations: generations(), sends, receipts: receiptCount(),
  providerCalls, visionCalls, getFileCalls, downloadFetches, chatActions,
  episodeState: Number.isSafeInteger(episodeId)
    ? portfolio.goalEpisodeById(episodeId)?.state || null : null,
  workStatus: Number.isSafeInteger(workItemId)
    ? db.prepare('SELECT status FROM main.eva_work_items WHERE id=?').get(workItemId)?.status || null
    : null,
  finalDeliveryBindingSha256: String(prepared?.finalDeliveryBindingSha256 || '') || null,
  authorityGeneration: turnUnderstandingAuthorityRecord(db, execution)?.sourceGeneration || null,
  issuedWorkItemId: created?.workItem?.id || null,
  issuedEpisodeId: created?.episode?.id || null,
  issuedEpisodeState: created?.episode?.id
    ? portfolio.goalEpisodeById(created.episode.id)?.state || null : null,
  issuedWorkStatus: created?.workItem?.id
    ? db.prepare('SELECT status FROM main.eva_work_items WHERE id=?')
      .get(created.workItem.id)?.status || null : null,
  preparedOutput: prepared ? {
    reply: prepared.reply,
    error: prepared.error || null,
    metadata: prepared.metadata || null,
    goalEpisodeId: prepared.goalEpisodeId || null,
    workItemId: prepared.workItemId || null,
    finalDeliveryBindingSha256: prepared.finalDeliveryBindingSha256 || null,
  } : null,
  executionError: execution?.error || null,
  recoveryError,
  deliveryFailure: db.prepare(`SELECT content FROM main.timeline
    WHERE kind='system' AND content LIKE 'Delivery-receipt voor episode %'
    ORDER BY id DESC LIMIT 1`).get()?.content || null,
  exactOriginCount: Number(db.prepare(`SELECT count(*) AS value
    FROM main.eva_episode_origins WHERE origin_channel='telegram'
      AND origin_conversation_id=? AND origin_message_id=?`)
    .get(conversationId, String(messageId)).value),
  authoritySource: (() => {
    const authority = turnUnderstandingAuthorityRecord(db, execution);
    let body;
    try { body = JSON.parse(String(authority?.sourceBodyJson || 'null')); } catch { body = null; }
    return authority && body ? {
      kind: body.kind,
      content: body.content,
      fileId: body.fileId,
      fileUniqueId: body.meta?.fileUniqueId || null,
      mimeType: body.meta?.mimeType || null,
      replyToMessageId: body.replyToMessageId,
      sourceBodySha256: sha256(authority.sourceBodyJson),
      sourceSha256: authority.sourceSha256,
    } : null;
  })(),
};
process.stdout.write(`NATIVE_TELEGRAM_DIRECT_RECOVERY=${JSON.stringify(report)}\n`);
db.close();
process.exit(0);
