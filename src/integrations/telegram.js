import { pollTelegramConcurrently } from './telegrampolling.js';
// Telegram: Eva's lijntje naar de telefoons van het gezin.
import { Bot, GrammyError, HttpError, InputFile } from 'grammy';
import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFile as execFileCb } from 'node:child_process';
import { promisify } from 'node:util';
import { performance } from 'node:perf_hooks';
import ffmpegPath from 'ffmpeg-static';
import { config } from '../config.js';
import {
  allPeople,
  archiveTelegramMessage,
  canonicalTelegramText,
  claimTurnToolExecution,
  claimTelegramMessage,
  completeTurnToolExecution,
  completeTelegramMessage,
  db,
  failTelegramMessage,
  logTimeline,
  markTelegramMessageDeliveryState,
  markTelegramMessageDeleted,
  newerTelegramUserMessage,
  normalizeTelegramRecipient,
  pendingPreparedTelegramMessages,
  personByTelegram,
  recentTelegramMessages,
  recoverInterruptedTelegramMessages,
  resolveTelegramTargets,
  telegramOutboundByDedupeKey,
  telegramMessage,
  TELEGRAM_DELIVERY_STATUS,
  TELEGRAM_RECEIPT_PROTOCOL,
  TELEGRAM_TRANSPORT_MAX_ATTEMPTS,
  TELEGRAM_TRANSPORT_PROTOCOL,
  transitionTurnToolExecution,
  upsertPerson,
  validateTelegramAcceptedReceipt,
  validateExecutionRecord,
} from '../db.js';
import {
  createInterpretationRetryHandoff,
  freezeEpisodeDelivery,
  registeredInterpretationRetryHandoffForOrigin,
  reconcileFrozenEpisodeDeliveries,
  recoverInterruptedUnderstandingIngresses,
} from '../mind/portfolio.js';
import {
  registerMessenger,
  registerPhotoSender,
  registerVoiceSender,
} from '../mind/tools.js';
import {
  createFaceIdentityClaim,
  nameablePhotoForTelegramMessage,
  rememberNameablePhoto,
} from '../mind/vision.js';
import { chatComplete, imageMsg } from '../brain/llm.js';
import { canonicalJson, errMsg, tekstOverlap } from '../util.js';
import { redactSecrets, redactSensitiveText } from '../security/redaction.js';
import { registerRuntimePrincipalValidator } from '../mind/runtimecontroller.js';
import { householdMembers } from '../mind/household.js';
import { turnUnderstandingAuthorityRecord } from '../turnauthority.js';
import {
  archivedTelegramDialogueRoute,
  telegramDialogueRoute,
} from '../telegramdialogue.js';
import {
  askEva2Canary,
  createEva2DraftSink,
  eva2CanaryPrompt,
  labelEva1TelegramReply,
  labelEva2TelegramFailure,
  labelEva2TelegramReply,
} from './eva2canary.js';

const execFile = promisify(execFileCb);
let bot = null;
const telegramRuntimePrincipals = new WeakMap();
const telegramProcessingLeases = new WeakMap();
const issuedTelegramUnderstandingSources = new WeakMap();
const issuedTelegramIdentityContexts = new WeakMap();
const issuedTelegramDeliveryAuthorities = new WeakMap();
registerRuntimePrincipalValidator(
  'telegram',
  (context) => telegramRuntimePrincipals.get(context?.messageContext) || null,
);

const telegramAllowedPrincipals = Object.freeze(config.telegramAllowedIds
  .filter((id) => Number.isSafeInteger(id) && id > 0));
const telegramAllowlistReady = config.telegramAllowedIds.length > 0
  && telegramAllowedPrincipals.length === config.telegramAllowedIds.length;
const allowed = (id) => telegramAllowlistReady
  && telegramAllowedPrincipals.includes(Number(id));

function resolveName(from) {
  const bekend = personByTelegram(from.id);
  return bekend?.name || from.first_name || 'huisgenoot';
}

function telegramMessageKind(message = {}) {
  if (message.photo?.length) return 'photo';
  if (message.video) return 'video';
  if (message.video_note) return 'video_note';
  if (message.voice) return 'voice';
  if (message.audio) return 'audio';
  if (message.animation) return 'animation';
  if (message.sticker) return 'sticker';
  if (message.document) return 'document';
  if (message.location) return 'location';
  if (message.contact) return 'contact';
  if (typeof message.text === 'string') return 'text';
  return 'other';
}

function telegramMessageFile(message = {}) {
  if (message.photo?.length) return message.photo.at(-1);
  return message.video
    || message.video_note
    || message.voice
    || message.audio
    || message.animation
    || message.sticker
    || message.document
    || null;
}

function telegramMessageContent(message = {}) {
  if (typeof message.text === 'string') return message.text;
  if (typeof message.caption === 'string') return message.caption;
  if (message.sticker?.emoji) return `[sticker ${message.sticker.emoji}]`;
  if (message.location) return `[locatie ${message.location.latitude}, ${message.location.longitude}]`;
  if (message.contact) return `[contact ${message.contact.first_name || ''} ${message.contact.last_name || ''}]`.trim();
  return '';
}

function archiveTelegramPayload({
  chatId,
  message,
  sender = null,
  role = null,
  replyToMessageId = null,
  mediaRef = null,
  processingStatus = null,
  meta = null,
} = {}) {
  if (!message?.message_id || chatId == null) return null;
  const file = telegramMessageFile(message);
  const author = sender || (message.from?.is_bot
    ? 'Eva'
    : message.from
      ? resolveName(message.from)
      : 'huisgenoot');
  const resolvedRole = role || (message.from?.is_bot ? 'assistant' : 'user');
  const routeMeta = meta && typeof meta === 'object' ? meta : {};
  const dialogueRoute = telegramDialogueRoute(
    routeMeta.dialogueRoute,
    archivedTelegramDialogueRoute({
      role: resolvedRole,
      kind: telegramMessageKind(message),
      content: telegramMessageContent(message),
      meta: routeMeta,
    }),
  );
  return archiveTelegramMessage({
    chatId,
    messageId: message.message_id,
    ts: message.date || null,
    sender: author,
    role: resolvedRole,
    kind: telegramMessageKind(message),
    content: telegramMessageContent(message),
    replyToMessageId: replyToMessageId
      ?? message.reply_to_message?.message_id
      ?? null,
    mediaRef,
    fileId: file?.file_id || null,
    processingStatus: processingStatus || (resolvedRole === 'assistant' ? 'completed' : 'received'),
    meta: {
      ...(message.message_thread_id ? { messageThreadId: String(message.message_thread_id) } : {}),
      ...(message.media_group_id ? { mediaGroupId: String(message.media_group_id) } : {}),
      ...(file?.file_unique_id ? { fileUniqueId: String(file.file_unique_id) } : {}),
      ...(file?.mime_type ? { mimeType: String(file.mime_type) } : {}),
      ...routeMeta,
      dialogueRoute,
    },
  });
}

function archiveTelegramEnvelope(ctx) {
  if (!ctx?.message || !ctx?.chat) return;
  const fromTelegramId = positiveTelegramInteger(ctx?.from?.id);
  if (!fromTelegramId) throw new Error('Telegram-envelop mist een geldige afzender-principal');
  const current = archiveTelegramPayload({
    chatId: ctx.chat.id,
    message: ctx.message,
    sender: ctx.from ? resolveName(ctx.from) : null,
    role: 'user',
    meta: { inbound: true, fromTelegramId: String(fromTelegramId) },
  });
  if (ctx.message.reply_to_message) {
    archiveTelegramPayload({
      chatId: ctx.chat.id,
      message: ctx.message.reply_to_message,
      meta: { recoveredFromReply: true },
    });
  }
  return current;
}

/** Houd de "typt…"-indicator levend zolang Eva nadenkt. */
function typing(ctx) {
  const send = () => ctx.replyWithChatAction('typing').catch(() => {});
  send();
  const iv = setInterval(send, 4000);
  return () => clearInterval(iv);
}



// Telegram accepteert maximaal 4096 tekens per tekstbericht. Ik hou wat marge,
// zodat lange YAML/codeblokken niet meer stuklopen op “message is too long”.
const TELEGRAM_SAFE_TEXT_LIMIT = 3800;

export function splitTelegramText(tekst, max = TELEGRAM_SAFE_TEXT_LIMIT) {
  const raw = String(tekst ?? '');
  if (!raw) return [''];
  const chunks = [];
  let rest = raw;
  const minNiceCut = Math.floor(max * 0.6);

  while (rest.length > max) {
    let cut = rest.lastIndexOf('\n\n', max);
    if (cut < minNiceCut) cut = rest.lastIndexOf('\n', max);
    if (cut < minNiceCut) cut = rest.lastIndexOf(' ', max);
    if (cut < minNiceCut) cut = max;

    chunks.push(rest.slice(0, cut).trimEnd());
    rest = rest.slice(cut).replace(/^\n+/, '');
  }

  if (rest.length || !chunks.length) chunks.push(rest);
  return chunks;
}

function positiveTelegramInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function telegramTextSendOptions(opties = null, chunkIndex = 0) {
  const result = {};
  const threadId = positiveTelegramInteger(opties?.messageThreadId);
  const replyToMessageId = positiveTelegramInteger(opties?.replyToMessageId);
  if (threadId) result.message_thread_id = threadId;
  if (chunkIndex === 0 && replyToMessageId) {
    result.reply_parameters = {
      message_id: replyToMessageId,
      allow_sending_without_reply: true,
    };
  }
  return result;
}

const TELEGRAM_TRANSPORT_TOOLS = Object.freeze({
  text: '@transport/telegram.sendMessage',
  photo: '@transport/telegram.sendPhoto',
  audio: '@transport/telegram.sendAudio',
});
const TELEGRAM_SPLIT_VERSION = 'split-3800/v1';
const telegramTransportFlights = new Map();

function sha256(value) {
  return createHash('sha256').update(Buffer.isBuffer(value) ? value : String(value ?? '')).digest('hex');
}

function jsonObject(value) {
  if (!value) return null;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return null; }
}

class TelegramDeliveryError extends Error {
  /** @param {Record<string, any>} extras */
  constructor(classification, message, extras = {}) {
    super(message);
    this.name = 'TelegramDeliveryError';
    this.code = `TELEGRAM_DELIVERY_${String(classification).toUpperCase()}`;
    this.classification = classification;
    this.accepted = false;
    this.uncertain = classification === 'uncertain';
    this.retryable = classification === 'retryable';
    this.operationKey = extras.operationKey || null;
    Object.assign(this, extras);
  }
}

function deliveryError(classification, message, extras = {}) {
  return new TelegramDeliveryError(classification, message, extras);
}

function telegramTextIntent({ chatId, text, wholeText, deliveryKey, index, count, options }) {
  const normalizedChat = telegramId(chatId);
  const sendOptions = telegramTextSendOptions(options, index);
  const chunkKey = `${deliveryKey}:chunk:${index + 1}`;
  const intent = {
    contract: TELEGRAM_TRANSPORT_PROTOCOL,
    kind: 'text',
    chatId: normalizedChat,
    deliveryKey,
    chunkKey,
    segment: { index, count, splitVersion: TELEGRAM_SPLIT_VERSION },
    payloadSha256: sha256(text),
    wholeReplySha256: sha256(wholeText),
    sendOptions,
    directTurnSignalRef: String(options?.directTurnSignalRef || '').trim() || null,
    originPrincipal: String(options?.originPrincipal || '').trim() || null,
  };
  const operationKey = `telegram-text:${sha256(canonicalJson({
    contract: 'TelegramTextOperation/v1',
    chatId: normalizedChat,
    deliveryKey,
    index,
  }))}`;
  return {
    intent,
    operationKey,
    turnId: intent.directTurnSignalRef || `telegram-delivery:${deliveryKey}`,
    tool: TELEGRAM_TRANSPORT_TOOLS.text,
  };
}

function normalizedTelegramResponse(intent, message) {
  const messageId = positiveTelegramInteger(message?.message_id);
  const acceptedChat = telegramId(message?.chat?.id);
  const telegramDate = positiveTelegramInteger(message?.date);
  if (!messageId || acceptedChat !== intent.chatId || !telegramDate) {
    throw deliveryError('uncertain', 'Telegram gaf geen volledig en chatgebonden Message-resultaat terug.');
  }
  const response = {
    chatId: acceptedChat,
    messageId: String(messageId),
    telegramDate,
    messageThreadId: positiveTelegramInteger(message?.message_thread_id),
    replyToMessageId: positiveTelegramInteger(message?.reply_to_message?.message_id),
    textSha256: typeof message?.text === 'string' ? sha256(message.text) : null,
    captionSha256: typeof message?.caption === 'string' ? sha256(message.caption) : null,
    mediaFileId: intent.kind === 'photo'
      ? String(message?.photo?.at(-1)?.file_id || '').trim() || null
      : intent.kind === 'audio'
        ? String(message?.audio?.file_id || '').trim() || null : null,
  };
  const expectedThread = positiveTelegramInteger(intent.sendOptions?.message_thread_id);
  const expectedReply = positiveTelegramInteger(
    intent.sendOptions?.reply_parameters?.message_id,
  );
  const payloadEchoValid = intent.kind === 'text'
    ? response.textSha256 === intent.payloadSha256 && response.captionSha256 == null
    : response.textSha256 == null && (!intent.sendOptions?.caption
      ? response.captionSha256 == null || response.captionSha256 === intent.captionSha256
      : response.captionSha256 === intent.captionSha256);
  if (!payloadEchoValid) {
    throw deliveryError('uncertain', 'Telegram bevestigde andere tekst dan de gebonden payload.');
  }
  if (response.messageThreadId !== expectedThread || response.replyToMessageId !== expectedReply) {
    throw deliveryError('uncertain', 'Telegram bevestigde een andere thread of reply-oorsprong.');
  }
  if (intent.kind !== 'text' && !response.mediaFileId) {
    throw deliveryError('uncertain', 'Telegram gaf geen duurzaam media-bestandsbewijs terug.');
  }
  return response;
}

function acceptedTelegramReceipt(operationKey, intent, message, attempt) {
  const core = {
    contract: TELEGRAM_RECEIPT_PROTOCOL,
    accepted: true,
    operationKey,
    intentSha256: sha256(canonicalJson(intent)),
    attempt,
    response: normalizedTelegramResponse(intent, message),
  };
  return validateAcceptedTelegramReceipt(
    { ...core, receiptSha256: sha256(canonicalJson(core)) },
    operationKey,
    intent,
  );
}

function validateAcceptedTelegramReceipt(receipt, operationKey, intent) {
  try {
    return validateTelegramAcceptedReceipt(receipt, operationKey, intent);
  } catch {
    throw deliveryError('uncertain',
      'Duurzaam Telegram-acceptatiebewijs is gewijzigd, onvolledig of hoort bij een andere payload.',
      { operationKey, collision: true });
  }
}

function telegramMessageFromReceipt(receipt, intent, payload, recovered = false) {
  const response = receipt.response;
  return {
    chat: { id: response.chatId }, message_id: Number(response.messageId), date: response.telegramDate,
    ...(intent.kind === 'text' ? { text: payload.text }
      : { caption: payload.caption || undefined, [intent.kind]: intent.kind === 'photo'
        ? [{ file_id: response.mediaFileId }] : { file_id: response.mediaFileId } }),
    ...(response.messageThreadId ? { message_thread_id: response.messageThreadId } : {}),
    ...(response.replyToMessageId ? {
      reply_to_message: { message_id: response.replyToMessageId },
    } : {}),
    _evaTransport: {
      operationKey: receipt.operationKey,
    },
    ...(recovered ? { _evaRecovered: true } : {}),
  };
}

function reconcileAcceptedTelegramArchive(receipt, intent, payload) {
  validateAcceptedTelegramReceipt(receipt, receipt.operationKey, intent);
  const response = receipt.response;
  const existing = telegramMessage(response.chatId, response.messageId);
  if (existing) {
    const exact = !existing.deleted && existing.role === 'assistant'
      && !existing.meta?.retractionPending
      && !existing.meta?.retractionUncertain
      && !existing.meta?.retracted
      && existing.kind === intent.kind
      && existing.meta?.intentSha256 === receipt.intentSha256
      && existing.meta?.receiptSha256 === receipt.receiptSha256
      && existing.meta?.payloadSha256 === intent.payloadSha256
      && String(existing.meta?.messageThreadId || '') === String(response.messageThreadId || '')
      && String(existing.reply_to_message_id || '') === String(response.replyToMessageId || '')
      && (intent.kind !== 'text' || existing.content === payload.text)
      && (intent.kind === 'text' || (existing.content === payload.caption
        && existing.meta?.captionSha256 === intent.captionSha256))
      && (intent.kind === 'text' || existing.file_id === response.mediaFileId);
    if (!exact) throw deliveryError('uncertain',
      existing.deleted
        ? 'Het eerder geaccepteerde Telegram-bericht is ingetrokken en geldt niet als bewijs.'
        : 'Telegram message-id is lokaal al aan andere inhoud gebonden.',
      { operationKey: receipt.operationKey, collision: true, transportAccepted: true });
    return existing.meta?.dialogueRoute
      ? existing
      : archiveTelegramMessage({
          chatId: response.chatId,
          messageId: response.messageId,
          meta: {
            dialogueRoute: archivedTelegramDialogueRoute({
              role: 'assistant', kind: intent.kind,
              content: intent.kind === 'text' ? payload.text : payload.caption,
              meta: { deliveryKey: intent.deliveryKey, dedupeKey: intent.chunkKey },
            }),
          },
        });
  }
  const archived = archiveTelegramPayload({
    chatId: response.chatId,
    message: {
      ...telegramMessageFromReceipt(receipt, intent, payload),
    },
    sender: 'Eva',
    role: 'assistant',
    replyToMessageId: intent.kind === 'text' && intent.segment.index === 0
      ? intent.sendOptions?.reply_parameters?.message_id
      : null,
    meta: {
      outbound: true,
      transportAccepted: true,
      transportProtocol: TELEGRAM_TRANSPORT_PROTOCOL,
      operationKey: receipt.operationKey,
      intentSha256: receipt.intentSha256,
      receiptSha256: receipt.receiptSha256,
      payloadSha256: intent.payloadSha256,
      captionSha256: intent.captionSha256 || null,
      chunkIndex: intent.segment?.index ?? null,
      chunkCount: intent.segment?.count ?? null,
      dedupeKey: intent.chunkKey || intent.deliveryKey,
      deliveryKey: intent.deliveryKey,
      directTurnSignalRef: intent.directTurnSignalRef,
    },
  });
  if (!archived || archived.deleted) {
    throw deliveryError('uncertain', 'Telegram-acceptatie kon niet veilig lokaal worden gereconcilieerd.', {
      operationKey: receipt.operationKey, transportAccepted: true,
    });
  }
  return archived;
}

function telegramApiFailure(error) {
  if (error instanceof GrammyError || (error?.name === 'GrammyError' && error?.ok === false)) {
    const code = Number(error.error_code || 0);
    const retryable = code === 429 || code >= 500;
    const retryAfterSeconds = Math.max(0, Number(error.parameters?.retry_after || 0));
    return {
      classification: retryable ? 'retryable' : 'rejected',
      retryAt: retryable
        ? new Date(Date.now() + Math.max(1, retryAfterSeconds) * 1_000).toISOString()
        : null,
      code,
    };
  }
  if (error instanceof HttpError || error?.name === 'HttpError') {
    return { classification: 'uncertain', retryAt: null, code: null };
  }
  return { classification: 'uncertain', retryAt: null, code: null };
}

function throwStoredTelegramOutcome(row, operationKey) {
  const stored = jsonObject(row?.output) || {};
  const classification = row?.status === TELEGRAM_DELIVERY_STATUS.TRANSPORT_RETRYABLE ? 'retryable'
    : row?.status === TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED ? 'rejected'
      : 'uncertain';
  throw deliveryError(classification,
    stored.message || 'Een eerdere Telegram-poging heeft geen veilig herhaalbare uitkomst.',
    { operationKey, retryAt: stored.retryAt || null, attempts: Number(stored.attempts || 1) },
  );
}

function archivedTelegramIntent(intent) {
  const keys = intent.kind === 'text'
    ? [intent.chunkKey,
      `${intent.deliveryKey}:chunk:${intent.segment.index + 1}-of-${intent.segment.count}`,
      ...(intent.segment.count === 1 ? [intent.deliveryKey] : [])]
    : [intent.deliveryKey];
  return keys.map((key) => telegramOutboundByDedupeKey(key, intent.chatId)).find(Boolean) || null;
}

function shareTelegramTransport(operationKey, intent, execute) {
  const intentSha256 = sha256(canonicalJson(intent));
  const current = telegramTransportFlights.get(operationKey);
  if (current) {
    if (current.intentSha256 !== intentSha256) {
      return Promise.reject(deliveryError('uncertain',
        'Gelijktijdige Telegram-sleutel hoort bij een andere payload.', { operationKey, collision: true }));
    }
    return current.promise;
  }
  const entry = { intentSha256, promise: null };
  entry.promise = Promise.resolve().then(execute).finally(() => {
    if (telegramTransportFlights.get(operationKey) === entry) telegramTransportFlights.delete(operationKey);
  });
  telegramTransportFlights.set(operationKey, entry);
  return entry.promise;
}

async function callTelegramAdapter(intent, payload, signal = undefined) {
  if (intent.kind === 'text') {
    return bot.api.sendMessage(intent.chatId, payload.text, intent.sendOptions, signal);
  }
  const file = new InputFile(payload.buffer, intent.kind === 'photo' ? 'foto.jpg' : 'eva.mp3');
  return intent.kind === 'photo'
    ? bot.api.sendPhoto(intent.chatId, file, intent.sendOptions, signal)
    : bot.api.sendAudio(intent.chatId, file, intent.sendOptions, signal);
}

async function executeTelegramTransport({
  intent, operationKey, turnId, tool, payload, reconcileOnly = false, isCurrent = () => true,
  wireNotAfter = null,
}) {
  const claim = claimTurnToolExecution({
    executionKey: operationKey, turnId, tool, args: intent,
    initialStatus: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
  });
  let { row } = claim;
  let attempt = 1;
  if (claim.claimed && archivedTelegramIntent(intent)) {
    transitionTurnToolExecution(operationKey, {
      from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
      status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_UNCERTAIN,
      output: { classification: 'uncertain', attempts: 1, message: 'Lokaal archief zonder duurzaam acceptatiebewijs.' },
    });
    throw deliveryError('uncertain',
      'Er bestaat een lokaal Telegram-archief zonder volledig transportbewijs; opnieuw sturen is geblokkeerd.',
      { operationKey, collision: true });
  }
  if (reconcileOnly && claim.claimed) {
    row = transitionTurnToolExecution(operationKey, {
      from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
      status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED,
      output: {
        classification: 'rejected', attempts: 0, retryable: false,
        message: 'Er bestond vóór reconciliatie geen Telegram-childintent.',
      },
    }).row;
    throwStoredTelegramOutcome(row, operationKey);
  }
  if (!claim.claimed) {
    if (row.status === TELEGRAM_DELIVERY_STATUS.TRANSPORT_ACCEPTED) {
      const receipt = validateAcceptedTelegramReceipt(jsonObject(row.output), operationKey, intent);
      reconcileAcceptedTelegramArchive(receipt, intent, payload);
      return telegramMessageFromReceipt(receipt, intent, payload, true);
    }
    if (reconcileOnly) {
      if (row.status === TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING) {
        row = transitionTurnToolExecution(operationKey, {
          from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
          status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_UNCERTAIN,
          output: {
            classification: 'uncertain', attempts: 1,
            message: 'Proces stopte tijdens Telegram-transport; zichtbaarheid is onbekend.',
          },
        }).row;
      }
      throwStoredTelegramOutcome(row, operationKey);
    }
    if (row.status === TELEGRAM_DELIVERY_STATUS.TRANSPORT_RETRYABLE) {
      const previous = jsonObject(row.output) || {};
      attempt = Number(previous.attempts || 1) + 1;
      if (attempt > TELEGRAM_TRANSPORT_MAX_ATTEMPTS) {
        row = transitionTurnToolExecution(operationKey, {
          from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_RETRYABLE,
          status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED,
          output: { ...previous, classification: 'rejected', retryable: false },
        }).row;
        throwStoredTelegramOutcome(row, operationKey);
      }
      if (Date.parse(previous.retryAt || '') > Date.now()) throwStoredTelegramOutcome(row, operationKey);
      row = transitionTurnToolExecution(operationKey, {
        from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_RETRYABLE,
        status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
        output: null, finished: false,
      }).row;
    } else if (row.status === TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING) {
      row = transitionTurnToolExecution(operationKey, {
        from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
        status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_UNCERTAIN,
        output: { classification: 'uncertain', attempts: 1, message: 'Proces stopte tijdens Telegram-transport.' },
      }).row;
      throwStoredTelegramOutcome(row, operationKey);
    } else {
      throwStoredTelegramOutcome(row, operationKey);
    }
  }
  let current = false;
  try { current = isCurrent() === true; } catch { current = false; }
  if (!current) {
    transitionTurnToolExecution(operationKey, {
      from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
      status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED,
      output: { classification: 'rejected', attempts: attempt, retryable: false,
        message: 'Telegram-child werd vóór transport ingehaald of de doelbinding wijzigde.' },
    });
    throw deliveryError('rejected',
      'Telegram-child werd vóór transport ingehaald of de doelbinding wijzigde.',
      { operationKey, stale: true });
  }
  const deadlineMs = wireNotAfter == null ? Number.POSITIVE_INFINITY
    : Date.parse(String(wireNotAfter));
  if (wireNotAfter != null && (!Number.isFinite(deadlineMs)
    || new Date(deadlineMs).toISOString() !== wireNotAfter
    || Date.now() > deadlineMs)) {
    transitionTurnToolExecution(operationKey, {
      from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
      status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED,
      output: { classification: 'rejected', attempts: attempt, retryable: false,
        message: 'De gebonden Telegram-childdeadline is verstreken vóór nieuw transport.' },
    });
    throw deliveryError('rejected',
      'De gebonden Telegram-childdeadline is verstreken vóór nieuw transport.',
      { operationKey, code: 'TELEGRAM_MONITORING_CHILD_DEADLINE' });
  }
  let message;
  const abortController = Number.isFinite(deadlineMs) ? new AbortController() : null;
  const timeoutMs = Number.isFinite(deadlineMs)
    ? Math.max(0, Math.min(2_147_483_647, deadlineMs - Date.now())) : null;
  const abortTimer = abortController
    ? setTimeout(() => abortController.abort(), timeoutMs) : null;
  try {
    message = await callTelegramAdapter(intent, payload, abortController?.signal);
  } catch (error) {
    const failure = telegramApiFailure(error);
    const status = TELEGRAM_DELIVERY_STATUS[`TRANSPORT_${failure.classification.toUpperCase()}`];
    transitionTurnToolExecution(operationKey, {
      from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING, status,
      output: {
        classification: failure.classification, attempts: attempt, retryAt: failure.retryAt,
        telegramCode: failure.code, message: errMsg(error).slice(0, 300),
      },
    });
    throw deliveryError(failure.classification,
      failure.classification === 'uncertain'
        ? 'Telegram kan de payload al hebben ontvangen; automatisch herhalen is geblokkeerd.'
        : errMsg(error),
      { cause: error, operationKey, retryAt: failure.retryAt, attempts: attempt });
  } finally {
    if (abortTimer) clearTimeout(abortTimer);
  }
  let receipt;
  try {
    receipt = acceptedTelegramReceipt(operationKey, intent, message, attempt);
  } catch (error) {
    transitionTurnToolExecution(operationKey, {
      from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
      status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_UNCERTAIN,
      output: { classification: 'uncertain', attempts: attempt, message: errMsg(error).slice(0, 300) },
    });
    error.operationKey ||= operationKey;
    throw error;
  }
  transitionTurnToolExecution(operationKey, {
    from: TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING,
    status: TELEGRAM_DELIVERY_STATUS.TRANSPORT_ACCEPTED,
    output: receipt,
  });
  reconcileAcceptedTelegramArchive(receipt, intent, payload);
  return telegramMessageFromReceipt(receipt, intent, payload);
}

function runTelegramTransport(descriptor) {
  return shareTelegramTransport(descriptor.operationKey, descriptor.intent,
    () => executeTelegramTransport(descriptor));
}

async function sendTelegramText(chatId, tekst, opties = null) {
  const deliveryKey = String(opties?.dedupeKey || '').trim();
  if (!deliveryKey) {
    throw deliveryError('rejected', 'Tekst wordt zonder duurzame deduplicatiesleutel niet verzonden.', {
      code: 'TELEGRAM_DURABLE_KEY_REQUIRED',
    });
  }
  const text = canonicalTelegramText(tekst);
  const chunks = splitTelegramText(text);
  const sent = [];
  const monitoringLimits = telegramMonitoringTransportLimits(opties);
  if (monitoringLimits === null) {
    throw deliveryError('rejected', 'Telegram-monitoring mist canonieke transportgrenzen.', {
      code: 'TELEGRAM_MONITORING_DEADLINE_INVALID',
    });
  }
  // De gewone reply-lane bewaart haar bestaande crash/reconcile-semantiek:
  // daar wordt actualiteit op fanoutniveau gecontroleerd. Private incidenten
  // kunnen daarnaast een harde deadline/targetbinding dragen die ook vlak
  // vóór ieder afzonderlijk Telegram-child opnieuw moet gelden.
  const isCurrent = opties?.checkCurrentBeforeEachTransport === true
    ? telegramFanoutCurrentCheck(opties) : () => true;
  for (const [index, deel] of chunks.entries()) {
    const descriptor = telegramTextIntent({
      chatId, text: deel, wholeText: text, deliveryKey,
      index, count: chunks.length, options: opties,
    });
    try {
      sent.push(await runTelegramTransport({
        ...descriptor, payload: { text: deel }, isCurrent,
        wireNotAfter: monitoringLimits?.wireNotAfter || null,
      }));
    } catch (error) {
      error.operationKey ||= descriptor.operationKey;
      error.acceptedPrefix = [...sent];
      if (sent.length && error.classification === 'rejected') {
        throw deliveryError('uncertain',
          'Telegram accepteerde een prefix maar wees de rest af; de aflevering is gedeeltelijk en niet veilig herhaalbaar.',
          { cause: error, operationKey: error.operationKey, acceptedPrefix: [...sent], partial: true });
      }
      throw error;
    }
  }
  return sent;
}

async function reconcileTelegramText(chatId, tekst, opties = null) {
  const deliveryKey = String(opties?.dedupeKey || '').trim();
  if (!deliveryKey) {
    throw deliveryError('rejected', 'Reconciliatie mist een duurzame deduplicatiesleutel.');
  }
  const text = canonicalTelegramText(tekst);
  const chunks = splitTelegramText(text);
  const sent = [];
  for (const [index, deel] of chunks.entries()) {
    const descriptor = telegramTextIntent({
      chatId, text: deel, wholeText: text, deliveryKey,
      index, count: chunks.length, options: opties,
    });
    try {
      sent.push(await runTelegramTransport({
        ...descriptor, payload: { text: deel }, reconcileOnly: true,
      }));
    } catch (error) {
      error.operationKey ||= descriptor.operationKey;
      error.acceptedPrefix = [...sent];
      throw error;
    }
  }
  return sent;
}

async function reconcileTelegramMedia({
  kind, chatId, buffer, caption = '', deliveryKey, sendOptions = {},
}) {
  const safeCaption = redactSensitiveText(String(caption || '')).slice(0, 1_024);
  const descriptor = telegramMediaIntent({
    kind, chatId, buffer, caption: safeCaption, deliveryKey, sendOptions,
  });
  return runTelegramTransport({
    ...descriptor, payload: { buffer, caption: safeCaption }, reconcileOnly: true,
  });
}

function telegramMediaOptions(kind, caption, options = {}) {
  const sendOptions = {};
  if (caption) sendOptions.caption = caption;
  const thread = positiveTelegramInteger(options.message_thread_id || options.messageThreadId);
  if (thread) sendOptions.message_thread_id = thread;
  if (kind === 'audio') {
    const title = redactSensitiveText(String(options.title || '')).slice(0, 128);
    const performer = redactSensitiveText(String(options.performer || '')).slice(0, 128);
    if (title) sendOptions.title = title;
    if (performer) sendOptions.performer = performer;
  }
  return sendOptions;
}

function telegramMediaIntent({ kind, chatId, buffer, caption, deliveryKey, sendOptions }) {
  const normalizedChat = telegramId(chatId);
  const safeCaption = redactSensitiveText(String(caption || '')).slice(0, 1_024);
  const intent = {
    contract: TELEGRAM_TRANSPORT_PROTOCOL, kind, chatId: normalizedChat, deliveryKey,
    payloadSha256: sha256(buffer), captionSha256: sha256(safeCaption),
    sendOptions: telegramMediaOptions(kind, safeCaption, sendOptions),
  };
  return {
    intent,
    operationKey: `telegram-media:${sha256(canonicalJson({
      contract: 'TelegramMediaOperation/v2', kind, chatId: normalizedChat, deliveryKey,
    }))}`,
    turnId: `telegram-delivery:${deliveryKey}`,
    tool: TELEGRAM_TRANSPORT_TOOLS[kind],
  };
}

async function sendTelegramMedia({ kind, chatId, buffer, caption = '', deliveryKey, sendOptions = {} }) {
  const key = String(deliveryKey || '').trim();
  if (!key || !['photo', 'audio'].includes(kind) || !Buffer.isBuffer(buffer) || !buffer.length) {
    throw deliveryError('rejected', 'Media mist een geldige payload of duurzame deduplicatiesleutel.', {
      code: 'TELEGRAM_DURABLE_KEY_REQUIRED',
    });
  }
  const safeCaption = redactSensitiveText(String(caption || '')).slice(0, 1_024);
  const descriptor = telegramMediaIntent({
    kind, chatId, buffer, caption: safeCaption, deliveryKey: key, sendOptions,
  });
  return runTelegramTransport({
    ...descriptor, payload: { buffer, caption: safeCaption },
    isCurrent: Reflect.get(sendOptions, 'checkCurrentBeforeEachTransport') === true
      ? telegramFanoutCurrentCheck(sendOptions) : () => true,
  });
}

function directTurnIdentity(ctx) {
  const chatId = telegramId(ctx?.chat?.id);
  const messageId = telegramId(ctx?.message?.message_id);
  if (!chatId || !messageId) throw new Error('Telegram-beurt mist chat_id of message_id');
  const signalRef = `turn:telegram:${chatId}:${messageId}`;
  return {
    chatId,
    messageId,
    signalRef,
    dedupeKey: `user-reply:${signalRef}`,
  };
}

function inboundReplyDedupeKey(ctx, purpose) {
  const identity = directTurnIdentity(ctx);
  return `${String(purpose || 'reply').replace(/[^a-z0-9_-]/gi, '-').slice(0, 40)}:${identity.signalRef}`;
}

function authenticatedTelegramPrincipal(ctx) {
  const fromId = positiveTelegramInteger(ctx?.from?.id);
  if (!fromId || !allowed(fromId)) {
    throw new Error('Telegram-beurt mist een actuele toegestane afzender-principal');
  }
  return { fromId: String(fromId), principal: `telegram:${fromId}` };
}

function immutableTelegramEnvelope(row) {
  const fromId = positiveTelegramInteger(row?.meta?.fromTelegramId);
  if (!row || row.role !== 'user' || row.deleted === true
    || row.meta?.inbound !== true || !fromId || !allowed(fromId)) return null;
  const body = {
    contract: 'TelegramArchivedUserEnvelope/v1',
    chatId: String(row.chat_id || ''),
    messageId: String(row.message_id || ''),
    sender: String(row.sender || ''),
    role: row.role,
    kind: String(row.kind || ''),
    content: String(row.content || ''),
    replyToMessageId: row.reply_to_message_id == null
      ? null : String(row.reply_to_message_id),
    mediaRef: row.media_ref == null ? null : String(row.media_ref),
    fileId: row.file_id == null ? null : String(row.file_id),
    deleted: false,
    meta: row.meta,
  };
  const bodyJson = canonicalJson(body);
  return Object.freeze({
    body,
    bodyJson,
    fromId: String(fromId),
    sha256: sha256(bodyJson),
  });
}

function archivedTelegramUnderstandingSource(ctx) {
  const identity = directTurnIdentity(ctx);
  const auth = authenticatedTelegramPrincipal(ctx);
  const row = telegramMessage(identity.chatId, identity.messageId);
  const envelope = immutableTelegramEnvelope(row);
  const generation = Number(row?.processing_attempts);
  if (!envelope || envelope.fromId !== auth.fromId
    || !Number.isSafeInteger(generation) || generation < 1 || generation > 64) return null;
  const sourceMeta = { ...envelope.body.meta };
  delete sourceMeta.preparedReply;
  delete sourceMeta.deliveryRetryAt;
  if (sourceMeta.messageThreadId == null) delete sourceMeta.messageThreadId;
  const sourceBodyJson = canonicalJson({ ...envelope.body, meta: sourceMeta });
  const sourceSha256 = sha256(sourceBodyJson);
  const sourceRef = `telegram-ingress:${sha256(canonicalJson({
    contract: 'TelegramIngressIdentity/v1',
    chatId: identity.chatId,
    messageId: identity.messageId,
    fromTelegramId: envelope.fromId,
  }))}`;
  return Object.freeze({
    kind: 'telegram_ingress', ref: sourceRef,
    sha256: sourceSha256, bodyJson: sourceBodyJson, generation,
  });
}

export function verifiedTelegramRecoveryOriginalText({
  sourceBodyJson = '', originalText = '', sender = '',
} = {}) {
  let body;
  try { body = JSON.parse(String(sourceBodyJson || '')); } catch { return false; }
  const text = String(originalText || '');
  const requester = redactSensitiveText(String(sender || '')).trim().slice(0, 160);
  if (!requester || !text.trim() || text.length > 16_000
    || canonicalJson(body) !== sourceBodyJson
    || body?.contract !== 'TelegramArchivedUserEnvelope/v1'
    || body.role !== 'user' || body.deleted !== false
    || body.sender !== requester || body.meta?.inbound !== true) return false;
  const content = String(body.content || '').trim();
  if (body.kind === 'text') {
    if (body.replyToMessageId == null) return text.trim() === content;
    return Boolean(content && text.startsWith(`[${requester} `)
      && text.endsWith(`\n${requester}: ${content}`));
  }
  if (body.kind === 'photo') {
    const expected = [
      `[${requester} stuurde een foto.${body.mediaRef
        ? ` Foto-ref: ${body.mediaRef}.` : ''} Het exacte beeld is aan deze beurt toegevoegd.]`,
      content ? `${requester}: ${content}` : '',
    ].filter(Boolean).join('\n');
    return text === expected;
  }
  if (!['video', 'document'].includes(body.kind)
    || !String(body.fileId || '') || !String(body.meta?.fileUniqueId || '')) return false;
  const media = canonicalJson({
    contract: 'TelegramDerivedVideoContext/v1', kind: body.kind,
    fileIdSha256: sha256(String(body.fileId)),
    fileUniqueIdSha256: sha256(String(body.meta.fileUniqueId)),
    replyToMessageId: body.replyToMessageId == null ? null : String(body.replyToMessageId),
  });
  const prefix = [
    content ? `${requester}: ${content}` : `${requester} stuurde een filmpje.`,
    `[Afgeleide media-analyse; dit is context, geen nieuwe tekst van ${requester}.`,
    `Media-identiteit: ${media}.`,
    'Begrensde visuele beschrijving: ',
  ].join('\n');
  return text.startsWith(prefix) && text.endsWith('.]')
    && text.slice(prefix.length, -2).trim().length > 0;
}

const FAILED_TELEGRAM_SCAN_LIMIT = 64;
const FAILED_TELEGRAM_MATERIALIZE_LIMIT = 8;
const FAILED_TELEGRAM_SCAN_KEY = 'episode_less_failed_ingress_v1';
const FAILED_TELEGRAM_CURSOR_TABLE = 'eva_failed_telegram_recovery_scan_cursor_v1';

db.exec(`CREATE TABLE IF NOT EXISTS ${FAILED_TELEGRAM_CURSOR_TABLE} (
  scan_key TEXT PRIMARY KEY CHECK(scan_key='${FAILED_TELEGRAM_SCAN_KEY}'),
  last_telegram_row_id INTEGER NOT NULL DEFAULT 0
    CHECK(typeof(last_telegram_row_id)='integer' AND last_telegram_row_id>=0),
  updated_ts TEXT NOT NULL
)`);
db.prepare(`INSERT OR IGNORE INTO ${FAILED_TELEGRAM_CURSOR_TABLE}
  (scan_key,last_telegram_row_id,updated_ts) VALUES (?,0,?)`).run(
  FAILED_TELEGRAM_SCAN_KEY, new Date().toISOString(),
);

function existingFailedTelegramReply(row) {
  return Boolean(db.prepare(`SELECT 1 FROM telegram_messages
    WHERE chat_id=? AND role='assistant' AND deleted=0 AND reply_to_message_id=?
    LIMIT 1`).get(String(row.chat_id), String(row.message_id)));
}

function existingFailedTelegramOrigin(row) {
  return Boolean(db.prepare(`SELECT 1 FROM eva_episode_origins
    WHERE origin_channel='telegram' AND origin_conversation_id=?
      AND origin_message_id=? LIMIT 1`).get(
    `telegram:${row.chat_id}`, String(row.message_id),
  ));
}

function registeredFailedTelegramHandoff(row) {
  return registeredInterpretationRetryHandoffForOrigin(
    'telegram', `telegram:${row.chat_id}`, String(row.message_id),
  );
}

function failedTelegramArchiveMetadataMatches(row, body) {
  const rowMeta = row?.meta && typeof row.meta === 'object' ? row.meta : {};
  const bodyMeta = body?.meta && typeof body.meta === 'object' ? body.meta : {};
  return bodyMeta.inbound === true && rowMeta.inbound === true
    && String(bodyMeta.fromTelegramId || '') === String(rowMeta.fromTelegramId || '')
    && String(bodyMeta.messageThreadId || '') === String(rowMeta.messageThreadId || '')
    && String(bodyMeta.fileUniqueId || '') === String(rowMeta.fileUniqueId || '')
    && String(bodyMeta.mediaGroupId || '') === String(rowMeta.mediaGroupId || '')
    && String(bodyMeta.mimeType || '') === String(rowMeta.mimeType || '');
}

function failedTelegramArchiveMatches(row, identity, execution, authority, body) {
  return Boolean(authority && execution?.status === 'started'
    && authority.channel === 'telegram'
    && authority.conversationId === identity.conversationId
    && String(authority.messageId) === identity.messageId
    && Number(authority.sourceGeneration) === Number(row.processing_attempts)
    && body?.contract === 'TelegramArchivedUserEnvelope/v1'
    && String(body.chatId) === String(row.chat_id)
    && String(body.messageId) === String(row.message_id)
    && body.sender === row.sender && body.role === 'user' && body.deleted === false
    && body.kind === row.kind && String(body.content || '') === String(row.content || '')
    && String(body.replyToMessageId || '') === String(row.reply_to_message_id || '')
    && String(body.mediaRef || '') === String(row.media_ref || '')
    && String(body.fileId || '') === String(row.file_id || '')
    && failedTelegramArchiveMetadataMatches(row, body));
}

function failedTelegramArgsMatch(row, identity, args, authority) {
  return args?.channel === 'telegram'
    && args.conversationId === identity.conversationId
    && String(args.messageId) === identity.messageId
    && args.requester === row.sender && args.syntheticTest !== true
    && verifiedTelegramRecoveryOriginalText({
      sourceBodyJson: authority.sourceBodyJson,
      originalText: args.originalText,
      sender: args.requester,
    });
}

function exactFailedTelegramAuthority(row) {
  const identity = {
    channel: 'telegram', conversationId: `telegram:${row.chat_id}`,
    messageId: String(row.message_id),
  };
  const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
  let execution;
  let authority;
  let args;
  let body;
  try {
    execution = validateExecutionRecord(db.prepare(`SELECT * FROM turn_tool_executions
      WHERE execution_key=? AND tool='@turn/understanding'`).get(executionKey));
    authority = turnUnderstandingAuthorityRecord(db, execution);
    args = JSON.parse(String(execution?.args || 'null'));
    body = JSON.parse(String(authority?.sourceBodyJson || 'null'));
  } catch { return null; }
  // Een afgeronde understanding heeft al haar eigen interrupted-done-route.
  // Alleen een nog gestarte interpretatie mag naar ChatInterpretationRetry.
  if (!failedTelegramArchiveMatches(row, identity, execution, authority, body)
    || !failedTelegramArgsMatch(row, identity, args, authority)) return null;
  return Object.freeze({
    originalText: args.originalText,
    supersessionScope: String(args.supersessionScope || ''),
    supersessionToken: String(args.supersessionToken || ''),
  });
}

function hasFailedTelegramAuthorityCollision(row) {
  const executionKey = `turn-understanding:${sha256(canonicalJson({
    channel: 'telegram', conversationId: `telegram:${row.chat_id}`,
    messageId: String(row.message_id),
  }))}`;
  try {
    return Boolean(db.prepare(`SELECT 1 FROM turn_tool_executions
      WHERE execution_key=? LIMIT 1`).get(executionKey)
      || db.prepare(`SELECT 1 FROM eva_turn_understanding_authorities_v1
        WHERE channel='telegram' AND conversation_id=? AND message_id=? LIMIT 1`).get(
        `telegram:${row.chat_id}`, String(row.message_id),
      ));
  } catch {
    // Een onleesbare authoritystore mag nooit text-fallbackbevoegdheid geven.
    return true;
  }
}

function failedTelegramTextFallback(row) {
  const meta = row.meta && typeof row.meta === 'object' ? row.meta : {};
  if (row.kind !== 'text' || row.reply_to_message_id != null
    || row.media_ref != null || row.file_id != null
    || typeof row.content !== 'string' || !row.content.trim()) return null;
  const envelope = immutableTelegramEnvelope(row);
  if (!envelope) return null;
  const sourceMeta = { ...envelope.body.meta };
  delete sourceMeta.preparedReply;
  delete sourceMeta.deliveryRetryAt;
  if (sourceMeta.messageThreadId == null) delete sourceMeta.messageThreadId;
  const sourceSha256 = sha256(canonicalJson({ ...envelope.body, meta: sourceMeta }));
  const messageThreadId = meta.messageThreadId == null ? '' : String(meta.messageThreadId);
  return Object.freeze({
    originalText: row.content,
    supersessionScope: `telegram:${sha256(canonicalJson({
      contract: 'TelegramIngressSupersessionLane/v1',
      chatId: String(row.chat_id), messageThreadId, fromTelegramId: envelope.fromId,
    }))}`,
    supersessionToken: `telegram-message:${sha256(canonicalJson({
      contract: 'TelegramIngressSupersessionToken/v1',
      messageId: String(row.message_id), sourceSha256,
    }))}`,
  });
}

function failedTelegramRecoveryInput(row) {
  const meta = row.meta && typeof row.meta === 'object' ? row.meta : {};
  const fromTelegramId = positiveTelegramInteger(meta.fromTelegramId);
  const messageThreadId = meta.messageThreadId == null
    ? null : positiveTelegramInteger(meta.messageThreadId);
  if (row.role !== 'user' || row.processing_status !== 'failed' || row.deleted
    || meta.inbound !== true || !fromTelegramId || !allowed(fromTelegramId)
    || String(meta.fromTelegramId) !== String(fromTelegramId)
    || (meta.messageThreadId != null
      && String(meta.messageThreadId) !== String(messageThreadId || ''))
    || typeof row.sender !== 'string' || !row.sender || row.sender.length > 80
    || row.sender.replace(/\s+/gu, ' ').trim() !== row.sender
    || meta.preparedReply || existingFailedTelegramOrigin(row)
    || existingFailedTelegramReply(row)
    || newerTelegramUserMessage(row.chat_id, row.message_id, {
      fromTelegramId: String(fromTelegramId),
      messageThreadId,
    })) return null;
  const authority = exactFailedTelegramAuthority(row);
  const source = authority || (!hasFailedTelegramAuthorityCollision(row)
    ? failedTelegramTextFallback(row) : null);
  return source ? Object.freeze({
    channel: 'telegram', conversationId: `telegram:${row.chat_id}`,
    messageId: String(row.message_id), requester: row.sender,
    originalText: source.originalText,
    failureClass: String(row.processing_error || 'telegram_ingress_failed').slice(0, 80),
    sessionRef: `telegram-backfill:${row.chat_id}:${row.message_id}`.slice(0, 180),
    supersessionScope: source.supersessionScope,
    supersessionToken: source.supersessionToken,
    visibleToFamily: false,
  }) : null;
}

const INTERRUPTED_TELEGRAM_PROCESSING_ERROR = 'proces herstartte tijdens verwerking';

function interruptedTelegramRecoveryAuthority(row) {
  const meta = row?.meta && typeof row.meta === 'object' ? row.meta : {};
  const fromTelegramId = positiveTelegramInteger(meta.fromTelegramId);
  const messageThreadId = meta.messageThreadId == null
    ? null : positiveTelegramInteger(meta.messageThreadId);
  if (!row || row.role !== 'user' || row.processing_status !== 'received' || row.deleted
    || row.processing_error !== INTERRUPTED_TELEGRAM_PROCESSING_ERROR
    || meta.inbound !== true || !fromTelegramId || !allowed(fromTelegramId)
    || String(meta.fromTelegramId) !== String(fromTelegramId)
    || (meta.messageThreadId != null
      && String(meta.messageThreadId) !== String(messageThreadId || ''))
    || typeof row.sender !== 'string' || !row.sender || row.sender.length > 80
    || row.sender.replace(/\s+/gu, ' ').trim() !== row.sender) return null;
  return exactFailedTelegramAuthority(row);
}

async function recoverInterruptedTelegramInterpretationHandoffs() {
  const candidates = /** @type {any[]} */ (db.prepare(`SELECT id,chat_id,message_id
    FROM telegram_messages
    WHERE role='user' AND processing_status='received' AND deleted=0
      AND processing_error=? ORDER BY id`).all(INTERRUPTED_TELEGRAM_PROCESSING_ERROR));
  if (!candidates.length) return Object.freeze({ examined: 0, adopted: 0, rejected: 0 });
  const { recoverInterruptedTelegramUnderstandingIngress } = await import('../mind/agent.js');
  let examined = 0;
  let adopted = 0;
  let rejected = 0;
  for (const candidate of candidates) {
    const received = telegramMessage(candidate.chat_id, candidate.message_id);
    const recovery = received && interruptedTelegramRecoveryAuthority(received);
    if (!recovery) continue;
    examined += 1;
    const claim = claimTelegramMessage(received.chat_id, received.message_id);
    if (!claim.claimed) continue;
    const ctx = {
      chat: { id: received.chat_id },
      from: { id: positiveTelegramInteger(received.meta.fromTelegramId) },
      message: {
        message_id: received.message_id,
        message_thread_id: received.meta.messageThreadId ?? null,
        text: received.kind === 'text' ? received.content : undefined,
        caption: received.kind === 'text' ? undefined : received.content,
      },
    };
    bindClaimedTelegramProcessingLease(ctx, claim);
    try {
      const turnAuthority = beginTelegramUnderstanding(ctx, received.sender);
      const messageContext = authenticatedTelegramMessageContext(ctx, {}, {
        authority: turnAuthority,
        requester: received.sender,
        originalText: recovery.originalText,
      });
      const replay = db.transaction(() => {
        const result = recoverInterruptedTelegramUnderstandingIngress({
          conversationId: `telegram:${received.chat_id}`,
          messageId: String(received.message_id), requester: received.sender,
          originalText: recovery.originalText, messageContext,
        });
        const disposition = String(result?.noPlanDecision || '').startsWith('recovery:')
          ? String(result.noPlanDecision).slice('recovery:'.length) : '';
        if (!result || (result.durableHandoff !== true && !disposition)) {
          throw new Error('onderbroken Telegram-understanding kon niet exact worden geadopteerd');
        }
        if (result.durableHandoff === true
          && (!result.workItemId || !result.goalEpisodeId)) {
          throw new Error('onderbroken Telegram-handoff mist exacte duurzame ids');
        }
        const settled = disposition === 'already_replied'
          ? completeTelegramMessage(received.chat_id, received.message_id)
          : failTelegramMessage(
            received.chat_id,
            received.message_id,
            disposition || 'runtime_interrupted',
          );
        if (!settled) {
          throw new Error('geadopteerde Telegram-ingress verloor haar processing-CAS');
        }
        return result;
      }).immediate();
      if (replay) adopted += 1;
    } catch {
      rejected += 1;
      failTelegramMessage(received.chat_id, received.message_id,
        'onderbroken Telegram-understanding faalde gesloten');
      logTimeline('system',
        'Onderbroken Telegram-interpretatie kon niet exact worden geadopteerd.', {
        internalOnly: true, telegramArchive: true,
        recoveryRejected: true, reason: 'interrupted_understanding_integrity_failure',
      });
    } finally {
      telegramProcessingLeases.delete(ctx);
    }
  }
  return Object.freeze({ examined, adopted, rejected });
}

function failedTelegramScanCandidates() {
  const cursor = Number(db.prepare(`SELECT last_telegram_row_id AS value
    FROM ${FAILED_TELEGRAM_CURSOR_TABLE} WHERE scan_key=?`).get(
    FAILED_TELEGRAM_SCAN_KEY,
  )?.value || 0);
  const query = (operator, after, limit) => db.prepare(`SELECT t.id,t.chat_id,t.message_id
    FROM telegram_messages t
    WHERE t.id ${operator} ? AND t.role='user' AND t.processing_status='failed'
      AND t.deleted=0 AND json_valid(t.meta) AND json_extract(t.meta, '$.inbound')=1
      AND json_type(t.meta, '$.preparedReply') IS NULL
      AND NOT EXISTS (SELECT 1 FROM eva_episode_origins o
        WHERE o.origin_channel='telegram'
          AND o.origin_conversation_id=('telegram:' || t.chat_id)
          AND o.origin_message_id=t.message_id)
      AND NOT EXISTS (SELECT 1 FROM telegram_messages reply
        WHERE reply.chat_id=t.chat_id AND reply.role='assistant' AND reply.deleted=0
          AND reply.reply_to_message_id=t.message_id)
    ORDER BY t.id LIMIT ?`).all(after, limit);
  const candidates = query('>', cursor, FAILED_TELEGRAM_SCAN_LIMIT);
  if (candidates.length < FAILED_TELEGRAM_SCAN_LIMIT && cursor > 0) {
    candidates.push(...query('<=', cursor, FAILED_TELEGRAM_SCAN_LIMIT - candidates.length));
  }
  return { cursor, candidates };
}

function commitFailedTelegramScanCursor(lastRowId) {
  db.prepare(`UPDATE ${FAILED_TELEGRAM_CURSOR_TABLE}
    SET last_telegram_row_id=?,updated_ts=? WHERE scan_key=?`).run(
    Number(lastRowId || 0), new Date().toISOString(), FAILED_TELEGRAM_SCAN_KEY,
  );
}

function recoverFailedTelegramInterpretationHandoffs() {
  const scan = failedTelegramScanCandidates();
  const candidates = scan.candidates;
  let materialized = 0;
  let eligible = 0;
  let scanned = 0;
  let cursorAfter = scan.cursor;
  for (const candidate of candidates) {
    if (materialized >= FAILED_TELEGRAM_MATERIALIZE_LIMIT) break;
    scanned += 1;
    cursorAfter = Number(candidate.id);
    const row = telegramMessage(candidate.chat_id, candidate.message_id);
    const input = row && failedTelegramRecoveryInput(row);
    if (!input) continue;
    eligible += 1;
    try {
      const created = createInterpretationRetryHandoff(input);
      if (created?.workItem?.id && created?.episode?.id) materialized += 1;
    } catch (error) {
      logTimeline('system', `Mislukte Telegram-ingress kon niet veilig worden overgedragen: ${errMsg(error).slice(0, 180)}`, {
        internalOnly: true, telegramArchive: true, recoveryRejected: true,
        chatId: String(row.chat_id), messageId: String(row.message_id),
      });
    }
  }
  if (scanned) commitFailedTelegramScanCursor(cursorAfter);
  return Object.freeze({
    scanned, eligible, materialized,
    cursorBefore: scan.cursor,
    cursorAfter,
  });
}

function failTelegramMessageAndRecover(chatId, messageId, error = '') {
  const failed = failTelegramMessage(chatId, messageId, error);
  if (failed) recoverFailedTelegramInterpretationHandoffs();
  return failed;
}

function currentPreparedTelegramUnderstandingSource(ctx) {
  const identity = directTurnIdentity(ctx);
  const auth = authenticatedTelegramPrincipal(ctx);
  const row = telegramMessage(identity.chatId, identity.messageId);
  const source = archivedTelegramUnderstandingSource(ctx);
  if (!source || row.processing_status !== 'processing'
    || !row.meta?.preparedReply || typeof row.meta.preparedReply !== 'object') return null;
  const lease = telegramProcessingLeases.get(ctx);
  if (lease && (lease.chatId !== identity.chatId
    || lease.messageId !== identity.messageId
    || lease.fromId !== auth.fromId
    || lease.sourceRef !== source.ref
    || lease.sourceSha256 !== source.sha256
    || lease.generation !== source.generation)) return null;
  return Object.freeze({
    sourceRef: source.ref, sourceSha256: source.sha256,
    sourceBodyJson: source.bodyJson, sourceGeneration: source.generation,
  });
}

function currentTelegramUnderstandingSource(issued) {
  const row = telegramMessage(issued.chatId, issued.messageId);
  const envelope = immutableTelegramEnvelope(row);
  if (!envelope
    || envelope.fromId !== issued.fromId
    || envelope.sha256 !== issued.sourceSha256
    || row.processing_status !== 'processing'
    || Number(row.processing_attempts) !== issued.generation) return null;
  return Object.freeze({
    kind: 'telegram_ingress',
    ref: issued.sourceRef,
    sha256: issued.sourceSha256,
    bodyJson: issued.sourceBodyJson,
    generation: issued.generation,
    ingressScope: issued.ingressScope,
    ingressToken: issued.ingressToken,
  });
}

function bindClaimedTelegramProcessingLease(ctx, claim) {
  const claimed = telegramMessage(ctx?.chat?.id, ctx?.message?.message_id);
  const envelope = immutableTelegramEnvelope(claimed);
  const generation = Number(claim?.attempts);
  if (!claim?.claimed || !envelope || !Number.isSafeInteger(generation)
    || generation < 1 || generation > 64
    || envelope.fromId !== String(positiveTelegramInteger(ctx?.from?.id))) {
    failTelegramMessageAndRecover(ctx?.chat?.id, ctx?.message?.message_id,
      'processingclaim mist exacte archived principal/generation');
    throw new Error('Telegram-processingclaim kan geen private understanding-authority minten');
  }
  const sourceRef = `telegram-ingress:${sha256(canonicalJson({
    contract: 'TelegramIngressIdentity/v1',
    chatId: String(ctx.chat.id),
    messageId: String(ctx.message.message_id),
    fromTelegramId: envelope.fromId,
  }))}`;
  const lease = Object.freeze({
    chatId: String(ctx.chat.id),
    messageId: String(ctx.message.message_id),
    fromId: envelope.fromId,
    sourceRef,
    sourceSha256: envelope.sha256,
    generation,
  });
  telegramProcessingLeases.set(ctx, lease);
  return lease;
}

function issueTelegramUnderstandingSource(ctx, { requester = '' } = {}) {
  const lease = telegramProcessingLeases.get(ctx);
  if (!lease || typeof requester !== 'string') return null;
  const envelope = immutableTelegramEnvelope(telegramMessage(lease.chatId, lease.messageId));
  if (!envelope || envelope.fromId !== lease.fromId) return null;
  const messageThreadId = envelope.body.meta?.messageThreadId == null
    ? '' : String(envelope.body.meta.messageThreadId);
  const ingressScope = `telegram:${sha256(canonicalJson({
    contract: 'TelegramIngressSupersessionLane/v1',
    chatId: lease.chatId,
    messageThreadId,
    fromTelegramId: envelope.fromId,
  }))}`;
  const ingressToken = `telegram-message:${sha256(canonicalJson({
    contract: 'TelegramIngressSupersessionToken/v1',
    messageId: lease.messageId,
    sourceSha256: envelope.sha256,
  }))}`;
  const issuedLease = Object.freeze({
    ...lease,
    sourceSha256: envelope.sha256,
    sourceBodyJson: envelope.bodyJson,
    ingressScope,
    ingressToken,
  });
  const current = currentTelegramUnderstandingSource(issuedLease);
  if (!current) return null;
  const capability = Object.freeze({});
  issuedTelegramUnderstandingSources.set(capability, Object.freeze({
    ...issuedLease,
    requester,
    originalText: null,
  }));
  return capability;
}

/**
 * Read-only verifier for an authority that can only be minted by this module's
 * allowed-principal archive-and-claim middleware.
 */
export function verifiedTelegramUnderstandingSource(messageContext = null, expected = {}) {
  const capability = messageContext?.telegramUnderstandingAuthority;
  const issued = capability && typeof capability === 'object'
    ? issuedTelegramUnderstandingSources.get(capability) : null;
  if (!issued
    || expected?.channel !== 'telegram'
    || String(expected?.conversationId || '') !== `telegram:${issued.chatId}`
    || String(expected?.messageId || '') !== issued.messageId
    || String(expected?.requester || '') !== issued.requester
    || String(expected?.originalText ?? '') !== issued.originalText
    || String(messageContext?.chatId || '') !== issued.chatId
    || String(messageContext?.messageId || '') !== issued.messageId
    || String(messageContext?.ingressScope || '') !== issued.ingressScope
    || String(messageContext?.ingressToken || '') !== issued.ingressToken) return null;
  return currentTelegramUnderstandingSource(issued);
}

export function verifiedTelegramIdentityContext(messageContext = null, expected = {}) {
  const capability = messageContext?.telegramUnderstandingAuthority;
  const issued = capability && typeof capability === 'object'
    ? issuedTelegramUnderstandingSources.get(capability) : null;
  const identity = issuedTelegramIdentityContexts.get(
    messageContext?.telegramIdentityContextAuthority,
  );
  if (!issued || !identity || !currentTelegramUnderstandingSource(issued)
    || expected?.channel !== 'telegram'
    || String(expected?.conversationId || '') !== `telegram:${issued.chatId}`
    || String(expected?.messageId || '') !== issued.messageId
    || String(expected?.requester || '') !== issued.requester
    || String(expected?.originalText ?? '') !== issued.originalText) return null;
  const currentPhoto = nameablePhotoForTelegramMessage({
    chatId: issued.chatId,
    messageId: identity.photoMessageId,
  });
  if (!currentPhoto?.buffer || currentPhoto.id !== identity.photoRef
    || currentPhoto.buffer.length !== identity.byteLength) return null;
  const claim = createFaceIdentityClaim({
    name: expected?.name,
    text: identity.userText,
    sender: issued.requester,
    photoRef: identity.photoRef,
    bindingKind: identity.bindingKind,
    replyText: identity.replyText,
  });
  if (!claim.ok) return null;
  return Object.freeze({
    ...claim,
    binding: Object.freeze({ ...claim.binding }),
    subject: Object.freeze({ ...claim.subject }),
    object: Object.freeze({ ...claim.object }),
    source: Object.freeze({ ...claim.source }),
  });
}

function exactTelegramIdentityEvidence(ctx, identity, contextualEvidence) {
  const rawInboundText = String(ctx?.message?.text ?? ctx?.message?.caption ?? '');
  const userText = String(contextualEvidence?.userText ?? '');
  const evidence = contextualEvidence?.replyEvidence;
  const photoRef = String(evidence?.photoRef || '').trim();
  const bindingKind = String(evidence?.photoBinding?.kind || '').trim();
  const photoMessageId = String(evidence?.photoBinding?.messageId || '').trim();
  if (userText !== rawInboundText || !userText.trim() || !new Set([
    'direct_reply', 'reply_chain', 'same_upload', 'nearby_photo_followup',
  ]).has(bindingKind)) return null;
  const currentPhoto = photoMessageId ? nameablePhotoForTelegramMessage({
    chatId: identity.chatId,
    messageId: photoMessageId,
  }) : null;
  if (!currentPhoto?.buffer || currentPhoto.id !== photoRef
    || Number(evidence?.byteLength) !== currentPhoto.buffer.length
    || String(evidence?.photoBinding?.photoRef || '') !== photoRef
    || String(evidence?.sourceRef || '') !== `telegram:${identity.chatId}:${photoMessageId}`) {
    return null;
  }
  return Object.freeze({
    userText,
    replyText: String(contextualEvidence?.replyTo?.text || ''),
    photoRef,
    photoMessageId,
    bindingKind,
    byteLength: currentPhoto.buffer.length,
  });
}

function authenticatedTelegramMessageContext(
  ctx, extras = {}, understanding = {}, contextualEvidence = {},
) {
  const identity = directTurnIdentity(ctx);
  const auth = authenticatedTelegramPrincipal(ctx);
  /** @type {Record<string, unknown>} */
  const safeExtras = { ...extras };
  delete safeExtras.authenticatedPrincipal;
  delete safeExtras.identityTrusted;
  const authority = understanding.authority
    || issueTelegramUnderstandingSource(ctx, understanding);
  if (!authority) {
    throw new Error('Telegram-beurt mist een actuele private processing-lease');
  }
  const issued = issuedTelegramUnderstandingSources.get(authority);
  if (!issued) {
    throw new Error('Telegram-beurt mist een private supersessionbinding');
  }
  if (issued.requester !== String(understanding.requester || '')
    || typeof understanding.originalText !== 'string'
    || !currentTelegramUnderstandingSource(issued)) {
    throw new Error('Telegram-beurt mist een actuele inhoudsbinding');
  }
  if (issued.originalText != null && issued.originalText !== understanding.originalText) {
    throw new Error('Telegram-beurt kan niet aan andere inhoud worden hergebonden');
  }
  const finalized = issued.originalText == null ? Object.freeze({
    ...issued,
    originalText: understanding.originalText,
  }) : issued;
  issuedTelegramUnderstandingSources.set(authority, finalized);
  const identityEvidence = exactTelegramIdentityEvidence(ctx, identity, contextualEvidence);
  const identityContextAuthority = identityEvidence ? Object.freeze({}) : null;
  const messageContext = Object.freeze({
    ...safeExtras,
    chatId: identity.chatId,
    messageId: identity.messageId,
    telegramUnderstandingAuthority: authority,
    ...(identityContextAuthority ? { telegramIdentityContextAuthority: identityContextAuthority } : {}),
    ingressScope: issued.ingressScope,
    ingressToken: issued.ingressToken,
    // Deze waarde komt ná principalvalidatie uit deze ingressfactory. Vrije
    // caller-extras zijn hierboven verwijderd en kunnen haar dus niet minten.
    identityTrusted: true,
  });
  telegramRuntimePrincipals.set(messageContext, auth.principal);
  if (identityContextAuthority) {
    issuedTelegramIdentityContexts.set(identityContextAuthority, identityEvidence);
  }
  return messageContext;
}

function beginTelegramUnderstanding(ctx, requester) {
  const authority = issueTelegramUnderstandingSource(ctx, { requester });
  if (!authority) {
    throw new Error('Telegram-preprocessing mist actuele archived ingress-authority');
  }
  return authority;
}

async function interruptedDoneTelegramRecovery(ctx, archived) {
  const identity = directTurnIdentity(ctx);
  const source = archivedTelegramUnderstandingSource(ctx);
  const { inspectPreparedTelegramUnderstandingRecovery } = await import('../mind/agent.js');
  return inspectPreparedTelegramUnderstandingRecovery({
    chatId: identity.chatId,
    conversationId: `telegram:${identity.chatId}`,
    messageId: identity.messageId,
    sender: String(archived?.sender || resolveName(ctx.from)),
    source,
  });
}

async function resumeInterruptedDoneTelegramTurn(ctx, sender) {
  const identity = directTurnIdentity(ctx);
  const source = archivedTelegramUnderstandingSource(ctx);
  const archived = telegramMessage(identity.chatId, identity.messageId);
  if (!source || archived?.processing_status !== TELEGRAM_DELIVERY_STATUS.INBOUND_PROCESSING
    || await interruptedDoneTelegramRecovery(ctx, archived) !== 'exact') {
    throw new Error('onderbroken Telegram-beurt verloor haar actuele duurzame bron');
  }
  const {
    preparedUnderstandingIngressReplay,
    verifiedPreparedUnderstandingTurnForResult,
  } = await import('../mind/agent.js');
  const result = preparedUnderstandingIngressReplay({
    channel: 'telegram', conversationId: `telegram:${identity.chatId}`,
    messageId: identity.messageId, sender: String(sender || ''),
  });
  if (!result || !verifiedPreparedUnderstandingTurnForResult(result, {
    channel: 'telegram', conversationId: `telegram:${identity.chatId}`,
    messageId: identity.messageId, sender: String(sender || ''),
    projection: 'telegram-prepared-reply/v2',
    sourceRef: source.ref, sourceSha256: source.sha256,
    sourceBodyJson: source.bodyJson, sourceGeneration: source.generation,
  })) throw new Error('onderbroken Telegram-beurt mist exact duurzaam Prepared-resultaat');
  const prepared = persistPreparedTelegramReply(ctx, sender, result);
  await deliverTelegramTurn({ ctx, sender, handled: prepared });
  return true;
}

function preparedToolEvidence(tools) {
  return redactSecrets((Array.isArray(tools) ? tools : []).slice(-12).map((tool) => ({
    name: String(tool?.name || ''), ok: tool?.ok === true, verified: tool?.verified === true,
    changed: tool?.changed === true, output: String(tool?.output || '').slice(0, 2_000),
    evidence: tool?.evidence || null,
  })));
}

function directTelegramReplyText(reply) {
  const value = canonicalTelegramText(reply);
  return config.eva2CanaryUrl ? labelEva1TelegramReply(value) : value;
}

function preparedReplyBinding(ctx, handled) {
  const identity = directTurnIdentity(ctx);
  const auth = authenticatedTelegramPrincipal(ctx);
  const expectedTurnId = `telegram:${identity.chatId}:${identity.messageId}`;
  const conversationId = `telegram:${identity.chatId}`;
  const payload = {
    // Tijdens de aparte Eva2-canary moet ook de gewone route zichtbaar en
    // duurzaam haar naam dragen. Alleen directe bewonersantwoorden lopen door
    // deze binding; proactieve meldingen zoals de deurbel blijven ongewijzigd.
    reply: directTelegramReplyText(handled?.reply),
    replyToMessageId: identity.messageId,
    messageThreadId: positiveTelegramInteger(ctx?.message?.message_thread_id),
    caption: redactSensitiveText(String(handled?.caption || '')).slice(0, 1_024),
    tools: preparedToolEvidence(handled?.tools),
    evidence: redactSecrets(handled?.evidence ?? handled?.replyEvidence ?? handled?.proof ?? null),
  };
  if (!payload.reply.trim()
    || String(handled?.turnId || '') !== expectedTurnId
    || String(handled?.conversationId || '') !== conversationId
    || String(handled?.originMessageId || '') !== identity.messageId) {
    throw new Error('Telegram-antwoord is niet aan deze geauthenticeerde inbound beurt gebonden');
  }
  const core = {
    contract: 'PreparedTelegramReply/v2', turnId: expectedTurnId, conversationId,
    originMessageId: identity.messageId, fromTelegramId: auth.fromId,
    originPrincipal: auth.principal, payloadSha256: sha256(canonicalJson(payload)),
    syntheticTest: handled.syntheticTest === true,
    goalEpisodeId: Number(handled.goalEpisodeId) || null,
    workItemId: Number(handled.workItemId) || null,
    finalDeliveryBindingSha256: String(handled.finalDeliveryBindingSha256 || '') || null,
  };
  return { binding: { ...core, bindingSha256: sha256(canonicalJson(core)) }, payload };
}

function issueTelegramDeliveryAuthority(result, delivery) {
  if (!result || typeof result !== 'object' || delivery?.accepted !== true
    || delivery?.transport !== 'telegram') return null;
  const capability = Object.freeze({});
  issuedTelegramDeliveryAuthorities.set(capability, Object.freeze({
    result,
    deliverySha256: sha256(canonicalJson(delivery)),
  }));
  return capability;
}

/** Verifier-only: alleen het echte Telegram-sendpad kan deze one-shot cap uitgeven. */
export function verifiedTelegramDeliveryAuthority(capability, result, delivery) {
  const issued = capability && typeof capability === 'object'
    ? issuedTelegramDeliveryAuthorities.get(capability) : null;
  if (!issued || issued.result !== result || delivery?.accepted !== true
    || delivery?.transport !== 'telegram'
    || sha256(canonicalJson(delivery)) !== issued.deliverySha256) return null;
  return Object.freeze({
    contract: 'TelegramDeliveredReplyAuthority/v1',
    deliverySha256: issued.deliverySha256,
  });
}

export function consumeTelegramDeliveryAuthority(capability, result, delivery) {
  if (!verifiedTelegramDeliveryAuthority(capability, result, delivery)) return false;
  issuedTelegramDeliveryAuthorities.delete(capability);
  return true;
}

async function publishDirectTelegramReply({ ctx, handled }) {
  const identity = directTurnIdentity(ctx);
  const auth = authenticatedTelegramPrincipal(ctx);
  const { payload } = preparedReplyBinding(ctx, handled);
  const sent = await sendTelegramText(identity.chatId, payload.reply, {
    replyToMessageId: payload.replyToMessageId,
    messageThreadId: payload.messageThreadId,
    dedupeKey: identity.dedupeKey,
    directTurnSignalRef: identity.signalRef,
    originPrincipal: auth.principal,
  });
  if (sent.length !== splitTelegramText(payload.reply).length
    || sent.some((message) => telegramId(message?.chat?.id) !== identity.chatId
      || !positiveTelegramInteger(message?.message_id))) {
    throw deliveryError('uncertain',
      'Telegram bevestigde niet alle antwoordchunks in exact de oorspronkelijke chat.',
      { operationKey: identity.dedupeKey, acceptedPrefix: sent });
  }
  return {
    ok: true, accepted: true, transport: 'telegram', kind: 'text',
    deduplicated: sent.length > 0 && sent.every((message) => message._evaRecovered === true),
    sent: sent.map((message) => ({
      chatId: String(message.chat.id), messageId: String(message.message_id),
      ...message._evaTransport,
    })),
    ...identity, originPrincipal: auth.principal,
  };
}

function preparedTelegramResult(ctx) {
  const archived = telegramMessage(ctx?.chat?.id, ctx?.message?.message_id);
  const prepared = archived?.meta?.preparedReply;
  if (prepared == null) return null;
  const auth = authenticatedTelegramPrincipal(ctx);
  if (!prepared || typeof prepared !== 'object' || !prepared.payload
    || archived?.role !== 'user' || archived.deleted || archived?.meta?.inbound !== true
    || String(archived?.meta?.fromTelegramId || '') !== auth.fromId) {
    throw new Error('Voorbereid Telegram-antwoord mist een geauthenticeerde inbound oorsprong');
  }
  const handled = {
    turnId: String(prepared.turnId), reply: String(prepared.payload.reply || ''), channel: 'telegram',
    sender: String(archived.sender || resolveName(ctx.from)),
    conversationId: String(prepared.conversationId || ''), tools: prepared.payload.tools,
    evidence: prepared.payload.evidence, caption: prepared.payload.caption,
    goalEpisodeId: Number(prepared.goalEpisodeId) || null,
    workItemId: Number(prepared.workItemId) || null,
    finalDeliveryBindingSha256: String(prepared.finalDeliveryBindingSha256 || '') || null,
    originMessageId: String(prepared.originMessageId || ''),
    syntheticTest: prepared.syntheticTest === true, recovered: true,
  };
  const expected = preparedReplyBinding(ctx, handled);
  if (canonicalJson(prepared.payload) !== canonicalJson(expected.payload)
    || prepared.originPrincipal !== expected.binding.originPrincipal
    || prepared.fromTelegramId !== expected.binding.fromTelegramId
    || prepared.payloadSha256 !== expected.binding.payloadSha256
    || prepared.bindingSha256 !== expected.binding.bindingSha256) {
    throw new Error('Voorbereid Telegram-antwoord is gewijzigd of hoort bij een andere principal');
  }
  return handled;
}

function persistPreparedTelegramReply(ctx, sender, handled) {
  const { binding, payload } = preparedReplyBinding(ctx, handled);
  const existing = telegramMessage(ctx.chat.id, ctx.message.message_id)?.meta?.preparedReply;
  if (existing && existing.bindingSha256 !== binding.bindingSha256) {
    throw new Error('Telegram-beurt heeft al een anders gebonden voorbereid antwoord');
  }
  archiveTelegramMessage({
    chatId: ctx.chat.id, messageId: ctx.message.message_id,
    meta: {
      inbound: true, fromTelegramId: binding.fromTelegramId,
      messageThreadId: payload.messageThreadId ? String(payload.messageThreadId) : null,
      preparedReply: { ...binding, payload, sender: redactSensitiveText(String(sender || '')).slice(0, 160) },
    },
  });
  return preparedTelegramResult(ctx);
}

async function deliverTelegramTurn({ ctx, sender, handled }) {
  const recoveringPrepared = handled?.recovered === true;
  const auth = authenticatedTelegramPrincipal(ctx);
  const supersedingScope = {
    fromTelegramId: auth.fromId,
    messageThreadId: positiveTelegramInteger(ctx?.message?.message_thread_id),
  };
  const supersedingMessage = () => newerTelegramUserMessage(
    ctx?.chat?.id,
    ctx?.message?.message_id,
    supersedingScope,
  );
  const markSuperseded = (message) => {
    const supersededBy = String(message.message_id);
    archiveTelegramMessage({
      chatId: ctx.chat.id, messageId: ctx.message.message_id,
      meta: { preparedReply: null, supersededBy },
    });
    recoverInterruptedUnderstandingIngresses({
      channel: 'telegram', conversationId: `telegram:${ctx.chat.id}`,
      messageId: ctx.message.message_id,
    });
    return supersededBy;
  };
  const superseding = supersedingMessage();
  if (superseding) {
    const supersededBy = markSuperseded(superseding);
    logTimeline('system', `Telegram-antwoord op ${ctx.message.message_id} niet verstuurd: bericht ${superseding.message_id} had de beurt al gecorrigeerd.`, {
      internalOnly: true,
      telegramArchive: true,
      superseded: true,
      chatId: String(ctx.chat.id),
      messageId: String(ctx.message.message_id),
      supersededBy,
    });
    return {
      ok: true,
      accepted: false,
      superseded: true,
      supersededBy,
    };
  }
  const boundHandled = persistPreparedTelegramReply(ctx, sender, handled);
  const afterPersist = supersedingMessage();
  if (afterPersist) {
    const supersededBy = markSuperseded(afterPersist);
    return {
      ok: true,
      accepted: false,
      superseded: true,
      supersededBy,
    };
  }
  try {
    const delivery = await publishDirectTelegramReply({ ctx, handled: boundHandled });
    const afterSend = supersedingMessage();
    if (afterSend) {
      const supersededBy = markSuperseded(afterSend);
      const retraction = await retractTelegramFanout(delivery.sent);
      if (!retraction.ok) {
        throw deliveryError('uncertain',
          'Het ingehaalde Telegram-antwoord kon niet aantoonbaar worden ingetrokken.', {
            operationKey: delivery.sent[0]?.operationKey || delivery.dedupeKey,
            acceptedPrefix: delivery.sent,
            retraction,
            partial: true,
          });
      }
      return {
        ...delivery, ok: true, accepted: false, stale: true, superseded: true,
        supersededBy, retraction,
      };
    }
    archiveTelegramMessage({
      chatId: ctx.chat.id, messageId: ctx.message.message_id,
      meta: { deliveryRetryAt: null },
    });
    const {
      deliveredReplyRecorded,
      preparedUnderstandingIngressReplay,
      recordDeliveredReply,
      verifiedPreparedUnderstandingTurnForResult,
    } = await import('../mind/agent.js');
    const currentSource = currentPreparedTelegramUnderstandingSource(ctx);
    const inboundCompleted = telegramMessage(
      ctx.chat.id, ctx.message.message_id,
    )?.processing_status === TELEGRAM_DELIVERY_STATUS.INBOUND_COMPLETED;
    if (!currentSource && inboundCompleted
      && deliveredReplyRecorded(boundHandled, delivery)) return delivery;
    if (!currentSource) {
      throw new Error('Telegram-delivery mist actuele prepared source-provenance');
    }
    const identity = directTurnIdentity(ctx);
    const deliveryResult = recoveringPrepared
      ? preparedUnderstandingIngressReplay({
          channel: 'telegram', conversationId: `telegram:${identity.chatId}`,
          messageId: identity.messageId, sender: String(sender || ''),
        })
      : boundHandled;
    if (!deliveryResult
      || String(deliveryResult.turnId || '') !== String(boundHandled.turnId || '')
      || String(deliveryResult.reply || '') !== String(boundHandled.reply || '')
      || !verifiedPreparedUnderstandingTurnForResult(deliveryResult, {
      channel: 'telegram', conversationId: `telegram:${identity.chatId}`,
      messageId: identity.messageId, sender: String(sender || ''),
      projection: 'telegram-prepared-reply/v2',
      ...currentSource,
    })) {
      throw new Error('Telegram-delivery mist exact duurzaam issued-result bewijs');
    }
    const deliveryAuthority = issueTelegramDeliveryAuthority(deliveryResult, delivery);
    if (!deliveryAuthority) {
      throw new Error('Telegram-delivery mist haar private record-authority');
    }
    if (!recordDeliveredReply(deliveryResult, delivery, deliveryAuthority)
      && !deliveredReplyRecorded(deliveryResult, delivery)) {
      throw new Error('Telegram-delivery kon niet exact worden vastgelegd');
    }
    return delivery;
  } catch (error) {
    if (error instanceof TelegramDeliveryError && error.classification === 'retryable') {
      archiveTelegramMessage({
        chatId: ctx.chat.id, messageId: ctx.message.message_id,
        meta: { deliveryRetryAt: error.retryAt || new Date(Date.now() + 30_000).toISOString() },
      });
    } else if (error instanceof TelegramDeliveryError
      && ['uncertain', 'rejected'].includes(error.classification)) {
      const state = error.classification === 'uncertain'
        ? TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN
        : TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED;
      if (Number(boundHandled.goalEpisodeId)) {
        const frozen = freezeEpisodeDelivery(boundHandled.goalEpisodeId, {
          reason: state, operationKey: error.operationKey,
          inbound: {
            chatId: ctx.chat.id, messageId: ctx.message.message_id, error: error.message,
          },
        });
        if (!frozen) markTelegramMessageDeliveryState(ctx.chat.id, ctx.message.message_id, {
          status: state, error: error.message,
        });
      } else {
        markTelegramMessageDeliveryState(ctx.chat.id, ctx.message.message_id, {
          status: state, error: error.message,
        });
      }
      logTimeline('system', `Telegram-aflevering ${state === TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN ? 'blijft onzeker' : 'is geweigerd'}; automatisch herhalen is gestopt.`, {
        internalOnly: true,
        telegramArchive: true,
        deliveryState: state,
        operationKey: error.operationKey || null,
        chatId: String(ctx.chat.id),
        messageId: String(ctx.message.message_id),
      });
    }
    throw error;
  }
}

async function exactDurableHandoffReplay(ctx, sender, handled) {
  const identity = directTurnIdentity(ctx);
  const { verifiedTelegramDurableHandoffProjection } = await import('../mind/agent.js');
  return verifiedTelegramDurableHandoffProjection(handled, {
    chatId: identity.chatId,
    conversationId: `telegram:${identity.chatId}`,
    messageId: identity.messageId,
    requester: String(sender || ''),
  });
}

async function settleTelegramTextTurn({ ctx, sender, handled }) {
  const durableHandoff = await exactDurableHandoffReplay(ctx, sender, handled);
  if (!durableHandoff) {
    return deliverTelegramTurn({ ctx, sender, handled });
  }
  return Object.freeze({
    ok: true,
    accepted: false,
    replayed: true,
    durableHandoff: true,
    workItemId: Number(durableHandoff.workItemId),
    goalEpisodeId: Number(durableHandoff.goalEpisodeId),
  });
}

async function resumePreparedTelegramTurn(ctx, sender) {
  let handled;
  try {
    handled = preparedTelegramResult(ctx);
  } catch (error) {
    markTelegramMessageDeliveryState(ctx?.chat?.id, ctx?.message?.message_id, {
      status: TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED,
      error: errMsg(error),
    });
    throw new TelegramDeliveryError(
      'rejected',
      'Het voorbereide Telegram-antwoord is ongeldig; automatisch herhalen is gestopt.',
      { cause: error },
    );
  }
  if (!handled) return false;
  await deliverTelegramTurn({ ctx, sender, handled });
  return true;
}

let preparedOutboxRunning = false;
let preparedOutboxTimer = null;

async function drainPreparedTelegramOutbox() {
  if (preparedOutboxRunning || !bot) return;
  preparedOutboxRunning = true;
  try {
    for (const row of pendingPreparedTelegramMessages(20)) {
      const claim = claimTelegramMessage(row.chat_id, row.message_id, { countAttempt: false });
      if (!claim.claimed) continue;
      const ctx = {
        chat: { id: row.chat_id },
        from: {
          id: positiveTelegramInteger(row.meta?.fromTelegramId),
          first_name: row.sender || 'huisgenoot',
        },
        message: {
          message_id: row.message_id,
          message_thread_id: row.meta?.messageThreadId || null,
        },
      };
      try {
        const resumed = await resumePreparedTelegramTurn(ctx, row.sender || 'huisgenoot');
        if (!resumed) throw new Error('voorbereid Telegram-antwoord ontbreekt');
        completeTelegramMessage(row.chat_id, row.message_id);
      } catch (error) {
        failTelegramMessageAndRecover(row.chat_id, row.message_id, errMsg(error));
        recoverInterruptedUnderstandingIngresses({
          channel: 'telegram', conversationId: `telegram:${row.chat_id}`,
          messageId: row.message_id,
        });
      }
    }
  } finally {
    preparedOutboxRunning = false;
  }
}

function startPreparedTelegramOutbox() {
  if (preparedOutboxTimer) return;
  setTimeout(() => drainPreparedTelegramOutbox().catch(() => {}), 5_000);
  preparedOutboxTimer = setInterval(
    () => drainPreparedTelegramOutbox().catch(() => {}),
    30_000,
  );
}

async function downloadTelegramFile(ctx, fileId) {
  const file = await ctx.api.getFile(fileId);
  const url = `https://api.telegram.org/file/bot${config.telegramToken}/${file.file_path}`;
  return Buffer.from(await (await fetch(url)).arrayBuffer());
}

async function describeVideoBuffer(buffer, contentType = 'video/mp4') {
  if (!ffmpegPath) throw new Error('ffmpeg is niet beschikbaar om videoframes te pakken');
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'eva-video-'));
  const input = path.join(dir, 'input.mp4');
  try {
    await fs.writeFile(input, buffer);
    // Pak een paar rustige kijkmomenten uit het filmpje. Ik beschrijf dus wat ik
    // in deze frames zie; audio/alle tussenliggende bewegingen hoor/zie ik nog niet volledig.
    const outPattern = path.join(dir, 'frame-%02d.jpg');
    await execFile(ffmpegPath, [
      '-hide_banner', '-loglevel', 'error', '-y',
      '-i', input,
      '-vf', 'fps=0.5,scale=768:-1',
      '-frames:v', '6',
      outPattern,
    ], { timeout: 30_000 });

    const names = (await fs.readdir(dir)).filter((n) => /^frame-\d+\.jpg$/.test(n)).sort();
    if (!names.length) throw new Error('ik kon geen losse beelden uit het filmpje halen');

    const inputMsgs = [];
    inputMsgs.push({ type: 'message', role: 'user', content: [{ type: 'input_text', text: `Dit is een Telegram-filmpje (${contentType}). Ik heb ${names.length} frames eruit gehaald. Beschrijf concreet wat er in het filmpje te zien lijkt; noem onzekerheid als de frames te weinig zeggen.` }] });
    for (const [i, name] of names.entries()) {
      const frame = await fs.readFile(path.join(dir, name));
      const dataUrl = `data:image/jpeg;base64,${frame.toString('base64')}`;
      inputMsgs.push(imageMsg(`Frame ${i + 1} van ${names.length}`, dataUrl));
    }

    const { text: beschrijving } = await chatComplete({
      instructions: 'Je bekijkt sampled frames uit een video. Beschrijf nuchter in het Nederlands wat er waarschijnlijk gebeurt (2-5 zinnen). Wees eerlijk: je hoort geen audio en ziet niet elk moment.',
      input: inputMsgs,
      tools: [],
      cacheKey: 'eva-chat-video',
      budgetClass: 'interactive',
    });
    return beschrijving;
  } finally {
    await fs.rm(dir, { recursive: true, force: true }).catch(() => {});
  }
}

function telegramVideoUnderstandingText({
  sender = '', caption = '', description = '', kind = 'video',
  fileId = '', fileUniqueId = '', replyToMessageId = null,
} = {}) {
  const requester = redactSensitiveText(String(sender || '')).trim().slice(0, 160)
    || 'huisgenoot';
  const originalCaption = redactSensitiveText(String(caption || '')).trim().slice(0, 4_000);
  const analysis = redactSensitiveText(String(description || '')).trim().slice(0, 4_000);
  const media = canonicalJson({
    contract: 'TelegramDerivedVideoContext/v1',
    kind: ['video', 'document'].includes(String(kind)) ? String(kind) : 'video',
    fileIdSha256: sha256(String(fileId || '')),
    fileUniqueIdSha256: sha256(String(fileUniqueId || '')),
    replyToMessageId: replyToMessageId == null ? null : String(replyToMessageId),
  });
  return [
    originalCaption ? `${requester}: ${originalCaption}` : `${requester} stuurde een filmpje.`,
    `[Afgeleide media-analyse; dit is context, geen nieuwe tekst van ${requester}.`,
    `Media-identiteit: ${media}.`,
    `Begrensde visuele beschrijving: ${analysis || 'geen betrouwbare beschrijving beschikbaar'}.]`,
  ].join('\n').slice(0, 16_000);
}

function cameraFromCaption(caption = '') {
  const m = String(caption || '').match(/\bCamera:\s*([^\n\r]+)/i);
  return m?.[1]?.trim() || null;
}

function localPhotoReply({
  target,
  buffer,
  contentType = 'image/jpeg',
  caption = '',
  ref = null,
  description = '',
  relation = 'reageert op een foto die lokaal bij dit Telegram-bericht is bewaard',
  bindingKind = 'implicit_reference',
} = {}) {
  if (!target || !buffer) return null;
  const bytes = Buffer.from(buffer);
  if (!bytes.length) return null;
  const mime = String(contentType || 'image/jpeg');
  const bijschrift = caption ? ` Het fotobericht had bijschrift: "${caption}".` : '';
  const fotoRef = ref ? ` Foto-ref: ${ref}.` : '';
  const photoMessageId = String(target.photoMessageId || target.messageId);
  return {
    context: `${relation}.${bijschrift}${fotoRef} Het exacte beeld is opnieuw aan deze beurt toegevoegd`,
    target,
    evidence: {
      description: String(description || '').slice(0, 2000),
      retrievalEvidence: `Exacte beeldbytes lokaal teruggelezen voor Telegram-bericht ${photoMessageId} (${bytes.length} bytes).`,
      sourceRef: `telegram:${target.chatId}:${photoMessageId}`,
      subjectRefs: [ref, 'telegram_reply_photo', `telegram_message_${photoMessageId}`].filter(Boolean),
      photoRef: ref || null,
      photoBinding: {
        kind: String(bindingKind || 'implicit_reference'),
        photoRef: ref || null,
        messageId: photoMessageId,
      },
      imageDataUrl: `data:${mime};base64,${bytes.toString('base64')}`,
      contentType: mime,
      byteLength: bytes.length,
    },
  };
}

function telegramReplyChainPhoto(chatId, startMessageId, maxDepth = 8) {
  let row = telegramMessage(chatId, startMessageId);
  const seen = new Set();
  for (let depth = 0; row && depth < maxDepth; depth += 1) {
    const key = `${row.chat_id}:${row.message_id}`;
    if (seen.has(key)) break;
    seen.add(key);
    if (row.kind === 'photo' || row.media_ref) return row;
    if (!row.reply_to_message_id) break;
    row = telegramMessage(chatId, row.reply_to_message_id);
  }
  return null;
}

function telegramPhotoReferenceRole(text = '') {
  const value = String(text || '');
  if (/\b(?:jouw|door jou)\b.{0,50}\b(?:foto|beeld)\b|\b(?:foto|beeld)\b.{0,80}\b(?:die|dat)\s+(?:jij|je)\s+(?:stuurde|liet\s+zien|deelde)\b/i.test(value)) {
    return 'assistant';
  }
  if (/\b(?:mijn|door mij)\b.{0,50}\b(?:foto|beeld)\b|\b(?:foto|beeld)\b.{0,80}\b(?:die|dat)\s+ik\s+(?:stuurde|deelde)\b/i.test(value)) {
    return 'user';
  }
  return null;
}

async function describeRepliedMessage(ctx) {
  const currentArchived = telegramMessage(ctx.chat.id, ctx.message.message_id);
  const reply = ctx.message.reply_to_message || null;
  const replyMessageId = reply?.message_id || currentArchived?.reply_to_message_id || null;
  if (!replyMessageId) return null;

  const repliedArchived = telegramMessage(ctx.chat.id, replyMessageId);
  const photoArchived = telegramReplyChainPhoto(ctx.chat.id, replyMessageId);
  const photoMessageId = String(photoArchived?.message_id || replyMessageId);
  const caption = (reply?.photo?.length ? reply.caption : '')
    || photoArchived?.content
    || '';
  const target = {
    channel: 'telegram',
    chatId: String(ctx.chat.id),
    messageId: String(replyMessageId),
    photoMessageId,
    text: String(reply?.text || reply?.caption || repliedArchived?.content || '').slice(0, 4000),
    sender: reply?.from?.is_bot
      ? 'Eva'
      : String(reply?.from?.first_name || reply?.from?.username || repliedArchived?.sender || '').trim(),
    isAssistant: reply?.from?.is_bot === true || repliedArchived?.role === 'assistant',
    kind: reply?.photo?.length ? 'photo' : (repliedArchived?.kind || 'text'),
  };
  const relation = photoMessageId === String(replyMessageId)
    ? 'reageert op een foto die lokaal bij dit Telegram-bericht is bewaard'
    : `reageert op Telegram-bericht ${replyMessageId}; via de reply-keten hoort daar exact de foto uit bericht ${photoMessageId} bij`;
  const bindingKind = photoMessageId === String(replyMessageId) ? 'direct_reply' : 'reply_chain';
  // Gebruik voor een eigen verzonden foto eerst de duurzame lokale koppeling.
  // Zo blijft een reply werken na een herstart en kost het bekijken geen aparte
  // modelcall voordat de eigenlijke gespreksbeurt überhaupt begint.
  const durable = nameablePhotoForTelegramMessage({
    chatId: target.chatId,
    messageId: photoMessageId,
  });
  if (durable?.buffer) {
    return localPhotoReply({
      target,
      buffer: durable.buffer,
      contentType: durable.contentType || 'image/jpeg',
      caption: caption || durable.caption || '',
      ref: durable.id,
      description: durable.description || '',
      relation,
      bindingKind,
    });
  }

  // Foto's van gezinsleden staan bij een Telegram-reply doorgaans opnieuw in
  // het bericht. Download ze eenmaal, archiveer ze lokaal met dezelfde
  // chat/message-sleutel en geef daarna de pixels aan de gewone Eva-beurt.
  if (reply?.photo?.length) {
    try {
      const buf = await downloadTelegramFile(ctx, reply.photo.at(-1).file_id);
      const ref = rememberNameablePhoto({
        buffer: buf,
        contentType: 'image/jpeg',
        caption,
        camera: cameraFromCaption(caption),
        telegramMessages: [{ chatId: target.chatId, messageId: photoMessageId }],
      });
      archiveTelegramMessage({
        chatId: target.chatId,
        messageId: photoMessageId,
        mediaRef: ref,
      });
      return localPhotoReply({ target, buffer: buf, contentType: 'image/jpeg', caption, ref, relation, bindingKind });
    } catch (e) {
      logTimeline('system', `Telegram-replyfoto ophalen mislukte, probeer lokaal archief: ${errMsg(e).slice(0, 180)}`);
    }
  }

  if (photoArchived?.file_id) {
    try {
      const buf = await downloadTelegramFile(ctx, photoArchived.file_id);
      const ref = rememberNameablePhoto({
        buffer: buf,
        contentType: photoArchived.meta?.mimeType || 'image/jpeg',
        caption,
        camera: cameraFromCaption(caption),
        telegramMessages: [{ chatId: target.chatId, messageId: photoMessageId }],
      });
      archiveTelegramMessage({
        chatId: target.chatId,
        messageId: photoMessageId,
        mediaRef: ref,
      });
      return localPhotoReply({
        target,
        buffer: buf,
        contentType: photoArchived.meta?.mimeType || 'image/jpeg',
        caption,
        ref,
        relation,
        bindingKind,
      });
    } catch (e) {
      logTimeline('system', `Gearchiveerde Telegram-replyfoto opnieuw ophalen mislukte: ${errMsg(e).slice(0, 180)}`);
    }
  }

  // Als het beeld echt niet beschikbaar is, geef in elk geval de context mee in
  // plaats van te doen alsof ik weet welke foto het was.
  if (caption) return {
    context: `reageert op mijn eerdere fotobericht met bijschrift: "${caption}". Ik kan het beeld zelf nu niet ophalen, dus ik moet voorzichtig blijven`,
    target,
  };
  if (reply?.text || repliedArchived?.content) {
    return {
      context: `reageert op mijn eerdere bericht: "${String(reply?.text || repliedArchived.content).slice(0, 500)}"`,
      target,
    };
  }
  return { context: null, target };
}

function refersToRecentTelegramPhoto(text = '') {
  return /\b(?:laatste|vorige|deze|die|dat)\s+(?:foto|afbeelding|plaatje|beeld|gezicht)\b|\b(?:foto|afbeelding|plaatje|beeld)\s+(?:die|dat|waarop|hiervan|daarvan)\b|\b(?:op|van)\s+(?:de|die|dat|het)\s+(?:foto|afbeelding|beeld)\b/i
    .test(String(text || ''));
}

function isShortPhotoFollowup(text = '') {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (!value || value.length > 100) return false;
  return /^(?:ja\s*[,;:]?\s*)?(?:dat\s+)?klopt(?:\s+(?:inderdaad|precies))?[.!?]*$/iu.test(value)
    || /^(?:nee\s*[,;:]?\s*)?(?:dit|dat)\s+(?:is|ben)\s+.+[.!?]*$/iu.test(value)
    // Een elliptische correctie als "Nee Jaap" is alleen foto-intent wanneer
    // het restant eruitziet als een eigennaam. Een gewone kanaal- of
    // apparaatcorrectie ("Nee via de Voice PE") mag nooit beeldcontext erven.
    || /^[Nn]ee\s*[,;:]?\s+\p{Lu}[\p{L}'-]{1,39}(?:\s+\p{Lu}[\p{L}'-]{1,39})?[.!?]*$/u.test(value);
}

function recentEnough(left, right, maxMs = 30 * 60_000) {
  const a = Date.parse(String(left || ''));
  const b = Date.parse(String(right || ''));
  return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= maxMs;
}

function inheritedPhotoForShortFollowup(ctx) {
  const current = telegramMessage(ctx.chat.id, ctx.message.message_id);
  const priorRows = recentTelegramMessages({
    chatId: ctx.chat.id,
    beforeMessageId: ctx.message.message_id,
    limit: 8,
  });
  const immediate = priorRows.at(-1);
  if (immediate?.kind === 'photo' && recentEnough(current?.ts, immediate.ts)) return immediate;

  const priorUser = [...priorRows].reverse().find((row) => row.role === 'user');
  if (priorUser?.reply_to_message_id && recentEnough(current?.ts, priorUser.ts, 10 * 60_000)) {
    const target = telegramReplyChainPhoto(ctx.chat.id, priorUser.reply_to_message_id);
    if (target) return target;
  }
  return null;
}

async function describeRecentPhotoReference(ctx, { force = false } = {}) {
  const text = ctx?.message?.text || '';
  if (!force && !refersToRecentTelegramPhoto(text)) return null;
  const inherited = force ? inheritedPhotoForShortFollowup(ctx) : null;
  // Een korte vervolgzin mag alleen de aantoonbaar nabije fotodraad erven.
  // Zonder die binding is de recentste willekeurige foto geen bewijsobject.
  if (force && !inherited) return null;
  const archived = inherited || recentTelegramMessages({
      chatId: ctx.chat.id,
      beforeMessageId: ctx.message.message_id,
      kind: 'photo',
      role: telegramPhotoReferenceRole(text),
      limit: 1,
    }).at(-1);
  if (!archived) return null;
  const target = {
    channel: 'telegram',
    chatId: String(ctx.chat.id),
    messageId: String(archived.message_id),
    text: String(archived.content || '').slice(0, 4000),
    sender: archived.sender || '',
    isAssistant: archived.role === 'assistant',
    kind: 'photo',
  };
  const caption = archived.content || '';
  const durable = nameablePhotoForTelegramMessage({
    chatId: target.chatId,
    messageId: target.messageId,
  });
  if (durable?.buffer) {
    return localPhotoReply({
      target,
      buffer: durable.buffer,
      contentType: durable.contentType || 'image/jpeg',
      caption,
      ref: durable.id,
      description: durable.description || '',
      relation: 'verwijst naar de laatste relevante foto uit dit Telegram-gesprek, die lokaal is bewaard',
      bindingKind: inherited ? 'nearby_photo_followup' : 'implicit_reference',
    });
  }
  if (archived.file_id) {
    try {
      const buf = await downloadTelegramFile(ctx, archived.file_id);
      const ref = rememberNameablePhoto({
        buffer: buf,
        contentType: archived.meta?.mimeType || 'image/jpeg',
        caption,
        camera: cameraFromCaption(caption),
        telegramMessages: [{ chatId: target.chatId, messageId: target.messageId }],
      });
      archiveTelegramMessage({
        chatId: target.chatId,
        messageId: target.messageId,
        mediaRef: ref,
      });
      return localPhotoReply({
        target,
        buffer: buf,
        contentType: archived.meta?.mimeType || 'image/jpeg',
        caption,
        ref,
        relation: 'verwijst naar de laatste relevante foto uit dit Telegram-gesprek, die opnieuw is opgehaald',
        bindingKind: inherited ? 'nearby_photo_followup' : 'implicit_reference',
      });
    } catch (e) {
      logTimeline('system', `Laatste Telegram-foto opnieuw ophalen mislukte: ${errMsg(e).slice(0, 180)}`);
    }
  }
  return {
    context: `verwijst naar het laatste lokale fotobericht${caption ? ` met bijschrift: "${caption}"` : ''}; de berichtkoppeling bestaat, maar de beeldbytes zijn niet beschikbaar`,
    target,
  };
}

export async function startTelegram() {
  if (!config.telegramToken) {
    logTimeline('system', 'Telegram staat uit (geen TELEGRAM_BOT_TOKEN in .env). Chatten kan via het dashboard; maak een bot via @BotFather voor Telegram.');
    return;
  }
  if (!telegramAllowlistReady) {
    logTimeline('system', 'Telegram blijft uit: TELEGRAM_ALLOWED_IDS moet minimaal één geldige positieve principal bevatten.', {
      internalOnly: true,
      telegramAuthorityUnavailable: true,
      configurationError: 'telegram_allowlist_required',
    });
    return;
  }

  bot = new Bot(config.telegramToken);
  const frozen = reconcileFrozenEpisodeDeliveries();
  if (frozen) logTimeline('system', `${frozen} terminale Telegram-aflevering(en) zijn met hun episodewacht gereconcilieerd.`, {
    internalOnly: true, telegramArchive: true, frozen,
  });
  const recovered = recoverInterruptedTelegramMessages();
  if (recovered) {
    logTimeline('system', `${recovered} onderbroken Telegram-bericht(en) zijn na de herstart weer hervatbaar.`, {
      internalOnly: true,
      telegramArchive: true,
      recovered,
    });
  }
  const interruptedRecovery = await recoverInterruptedTelegramInterpretationHandoffs();
  if (interruptedRecovery.adopted) {
    logTimeline('system', `${interruptedRecovery.adopted} onderbroken Telegram-interpretatie(s) zijn exact geadopteerd.`, {
      internalOnly: true, telegramArchive: true,
      interruptedUnderstandingRecovery: true, ...interruptedRecovery,
    });
  }
  const failedRecovery = recoverFailedTelegramInterpretationHandoffs();
  if (failedRecovery.materialized) {
    logTimeline('system', `${failedRecovery.materialized} mislukte Telegram-ingress(en) zijn veilig aan interpretatieherstel overgedragen.`, {
      internalOnly: true, telegramArchive: true,
      failedIngressRecovery: true, ...failedRecovery,
    });
  }

  // Archiveer vóór analyse of modelwerk. Als een beurt daarna vastloopt, blijft
  // het echte Telegram-bericht dus alsnog lokaal terugleesbaar.
  bot.use(async (ctx, next) => {
    if (!ctx?.message || !ctx?.from || !allowed(ctx.from.id)) {
      await next();
      return;
    }
    let archived = null;
    try {
      archived = archiveTelegramEnvelope(ctx);
    } catch (e) {
      logTimeline('system', `Telegram-bericht lokaal archiveren mislukte: ${errMsg(e).slice(0, 200)}`, {
        internalOnly: true,
        telegramArchive: true,
        chatId: String(ctx.chat?.id || ''),
        messageId: String(ctx.message?.message_id || ''),
      });
    }
    if (!archived) {
      // Een allowed update zonder immutable lokaal archief mag geen handler,
      // download, provider of antwoordtransport bereiken.
      return;
    }
    const registeredFailedHandoff = archived.processing_status === 'failed'
      ? registeredFailedTelegramHandoff(archived) : null;
    if (registeredFailedHandoff?.status === 'valid') {
      logTimeline('system',
        `Telegram-update ${ctx.chat.id}:${ctx.message.message_id} hoort al bij duurzaam interpretatieherstel.`, {
        internalOnly: true,
        telegramArchive: true,
        duplicateUpdate: true,
        failedIngressRecovery: true,
        reason: 'registered_interpretation_handoff',
        workItemId: registeredFailedHandoff.workItemId,
        episodeId: registeredFailedHandoff.episodeId,
      });
      return;
    }
    if (registeredFailedHandoff?.status === 'invalid') {
      logTimeline('system',
        'Duurzaam Telegram-interpretatieherstel heeft een ongeldige registrylevenscyclus.', {
        internalOnly: true,
        telegramArchive: true,
        duplicateUpdate: true,
        failedIngressRecovery: true,
        reason: 'registered_interpretation_handoff_integrity_failure',
      });
      return;
    }
    const preparedRecovery = archived.meta?.preparedReply
      && typeof archived.meta.preparedReply === 'object';
    const doneRecovery = preparedRecovery
      ? 'prepared' : await interruptedDoneTelegramRecovery(ctx, archived);
    if (doneRecovery === 'collision') {
      logTimeline('system', `Telegram-update ${ctx.chat.id}:${ctx.message.message_id} botst met onvolledig duurzaam herstelbewijs.`, {
        internalOnly: true,
        telegramArchive: true,
        duplicateUpdate: true,
        reason: 'durable_recovery_collision',
      });
      return;
    }
    const claim = claimTelegramMessage(ctx.chat.id, ctx.message.message_id, {
      countAttempt: doneRecovery === 'absent',
    });
    if (!claim.claimed) {
      if ([TELEGRAM_DELIVERY_STATUS.INBOUND_COMPLETED,
        TELEGRAM_DELIVERY_STATUS.INBOUND_PROCESSING,
        TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
        TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED]
        .includes(claim.reason)) {
        logTimeline('system', `Dubbele Telegram-update ${ctx.chat.id}:${ctx.message.message_id} niet opnieuw uitgevoerd.`, {
          internalOnly: true,
          telegramArchive: true,
          duplicateUpdate: true,
          reason: claim.reason,
        });
        return;
      }
      // Iedere niet-geclaimde allowed update blijft buiten de handlers; ook
      // een onbekende claimstatus verleent nooit impliciet processing-authority.
      return;
    }
    if (preparedRecovery) {
      try {
        const resumed = await resumePreparedTelegramTurn(
          ctx, archived.sender || resolveName(ctx.from),
        );
        if (!resumed) throw new Error('voorbereid Telegram-antwoord ontbreekt');
        completeTelegramMessage(ctx.chat.id, ctx.message.message_id);
      } catch (error) {
        failTelegramMessageAndRecover(ctx.chat.id, ctx.message.message_id, errMsg(error));
        recoverInterruptedUnderstandingIngresses({
          channel: 'telegram', conversationId: `telegram:${ctx.chat.id}`,
          messageId: ctx.message.message_id,
        });
        throw error;
      }
      return;
    }
    if (doneRecovery === 'exact') {
      bindClaimedTelegramProcessingLease(ctx, claim);
      try {
        await resumeInterruptedDoneTelegramTurn(
          ctx, archived.sender || resolveName(ctx.from),
        );
        completeTelegramMessage(ctx.chat.id, ctx.message.message_id);
      } catch (error) {
        failTelegramMessageAndRecover(ctx.chat.id, ctx.message.message_id, errMsg(error));
        recoverInterruptedUnderstandingIngresses({
          channel: 'telegram', conversationId: `telegram:${ctx.chat.id}`,
          messageId: ctx.message.message_id,
        });
        throw error;
      } finally {
        telegramProcessingLeases.delete(ctx);
      }
      return;
    }
    bindClaimedTelegramProcessingLease(ctx, claim);
    try {
      await next();
      completeTelegramMessage(ctx.chat.id, ctx.message.message_id);
    } catch (error) {
      failTelegramMessageAndRecover(ctx.chat.id, ctx.message.message_id, errMsg(error));
      recoverInterruptedUnderstandingIngresses({
        channel: 'telegram', conversationId: `telegram:${ctx.chat.id}`,
        messageId: ctx.message.message_id,
      });
      throw error;
    } finally {
      telegramProcessingLeases.delete(ctx);
    }
  });

  bot.command('start', async (ctx) => {
    if (!allowed(ctx.from.id)) return;
    upsertPerson(resolveName(ctx.from), { telegram_id: ctx.from.id });
    await sendTelegramText(
      ctx.chat.id,
      `Hoi! Ik ben ${config.assistantName}, ik woon hier in huis 💚 Zeg maar gewoon wat je wilt. Vertel me vooral hoe je heet, dan onthoud ik dat.`,
      { replyToMessageId: ctx.message.message_id, dedupeKey: inboundReplyDedupeKey(ctx, 'start') },
    );
  });

  bot.on('message:text', async (ctx) => {
    const tTelegramStart = performance.now();
    const latency = {};
    const stamp = (name, since) => { latency[name] = Math.round(performance.now() - since); };
    if (!allowed(ctx.from.id)) {
      await ctx.reply('Sorry, ik ken jou nog niet. Vraag het gezin om je toe te voegen. 🙈');
      return;
    }
    const tPrep = performance.now();
    const naam = resolveName(ctx.from);
    upsertPerson(naam, { telegram_id: ctx.from.id });
    const stop = typing(ctx);
    stamp('prep_ms', tPrep);
    try {
      if (eva2CanaryPrompt(ctx.message.text) !== null) {
        const tCanary = performance.now();
        try {
          const draftSink = createEva2DraftSink({
            chatId: ctx.chat.id,
            draftId: ctx.message.message_id,
            sendDraft: bot?.api?.raw?.sendMessageDraft?.bind(bot.api.raw),
          });
          const answer = await askEva2Canary({
            baseUrl: config.eva2CanaryUrl,
            chatId: ctx.chat.id,
            messageId: ctx.message.message_id,
            fromId: ctx.from.id,
            text: ctx.message.text,
            telegramDate: ctx.message.date,
            onDraft: draftSink
              ? async (text) => draftSink(labelEva2TelegramReply(text))
              : null,
            onReceipt: draftSink ? (text) => draftSink.receipt(text) : null,
          });
          await sendTelegramText(ctx.chat.id, labelEva2TelegramReply(answer.text), {
            replyToMessageId: ctx.message.message_id,
            dedupeKey: inboundReplyDedupeKey(ctx, 'eva2-canary'),
          });
          latency.eva2_canary = true;
          stamp('eva2_canary_ms', tCanary);
        } catch (error) {
          await sendTelegramText(
            ctx.chat.id,
            labelEva2TelegramFailure(errMsg(error).slice(0, 160)),
            {
              replyToMessageId: ctx.message.message_id,
              dedupeKey: inboundReplyDedupeKey(ctx, 'eva2-canary-error'),
            },
          );
        }
        return;
      }
      if (await resumePreparedTelegramTurn(ctx, naam)) {
        latency.resumed_prepared_reply = true;
        return;
      }
      const turnAuthority = beginTelegramUnderstanding(ctx, naam);
      let tekst = ctx.message.text;
      const tReplyContext = performance.now();
      const replied = await describeRepliedMessage(ctx) || await describeRecentPhotoReference(ctx, {
        force: isShortPhotoFollowup(ctx.message.text),
      });
      stamp('reply_context_ms', tReplyContext);
      if (replied?.context) {
        tekst = `[${naam} ${replied.context}]
${naam}: ${ctx.message.text}`;
      }
      const tAgent = performance.now();
      const { handleMessageDetailed } = await import('../mind/agent.js');
      const handled = await handleMessageDetailed({
        channel: 'telegram', sender: naam, text: tekst,
        replyTo: replied?.target || null,
        replyEvidence: replied?.evidence || null,
        messageContext: authenticatedTelegramMessageContext(ctx, {
          replyToMessageId: replied?.target?.messageId || null,
        }, {
          authority: turnAuthority,
          requester: naam,
          originalText: tekst,
        }, {
          userText: ctx.message.text,
          replyTo: replied?.target || null,
          replyEvidence: replied?.evidence || null,
        }),
      });
      stamp('agent_total_ms', tAgent);
      const tSend = performance.now();
      await settleTelegramTextTurn({ ctx, sender: naam, handled });
      stamp('telegram_send_ms', tSend);
    } finally {
      stop();
      latency.total_ms = Math.round(performance.now() - tTelegramStart);
      logTimeline('latency', `Telegram text: ${latency.total_ms}ms`, { channel: 'telegram', sender: resolveName(ctx.from), latency });
    }
  });

  bot.on('message:photo', async (ctx) => {
    if (!allowed(ctx.from.id)) return;
    const naam = resolveName(ctx.from);
    upsertPerson(naam, { telegram_id: ctx.from.id });
    const stop = typing(ctx);
    try {
      if (await resumePreparedTelegramTurn(ctx, naam)) return;
      const photos = ctx.message.photo;
      const buf = await downloadTelegramFile(ctx, photos[photos.length - 1].file_id);
      const caption = ctx.message.caption || '';
      const target = {
        channel: 'telegram',
        chatId: String(ctx.chat.id),
        messageId: String(ctx.message.message_id),
        text: caption,
        sender: naam,
        isAssistant: false,
        kind: 'photo',
      };
      const ref = rememberNameablePhoto({
        buffer: buf,
        contentType: 'image/jpeg',
        caption,
        telegramMessages: [{ chatId: target.chatId, messageId: target.messageId }],
      });
      archiveTelegramMessage({
        chatId: target.chatId,
        messageId: target.messageId,
        mediaRef: ref,
      });
      const media = localPhotoReply({
        target, buffer: buf, contentType: 'image/jpeg', caption, ref, bindingKind: 'same_upload',
      });
      // De lokale media-ref hoort bij het canonieke bronlichaam; mint pas na
      // deze archive-update, maar nog vóór de agent/providergrens.
      const turnAuthority = beginTelegramUnderstanding(ctx, naam);
      const tekst = [
        `[${naam} stuurde een foto.${ref ? ` Foto-ref: ${ref}.` : ''} Het exacte beeld is aan deze beurt toegevoegd.]`,
        caption ? `${naam}: ${caption}` : '',
      ].filter(Boolean).join('\n');
      const { handleMessageDetailed } = await import('../mind/agent.js');
      const handled = await handleMessageDetailed({
        channel: 'telegram',
        sender: naam,
        text: tekst,
        replyEvidence: media?.evidence || null,
        messageContext: authenticatedTelegramMessageContext(ctx, {
          replyToMessageId: null,
        }, {
          authority: turnAuthority,
          requester: naam,
          originalText: tekst,
        }, {
          userText: caption,
          replyTo: target,
          replyEvidence: media?.evidence || null,
        }),
      });
      await deliverTelegramTurn({ ctx, sender: naam, handled });
    } finally {
      stop();
    }
  });

  bot.on(['message:video', 'message:document'], async (ctx) => {
    if (!allowed(ctx.from.id)) return;
    const video = ctx.message.video || (ctx.message.document?.mime_type?.startsWith('video/') ? ctx.message.document : null);
    if (!video) return;

    const naam = resolveName(ctx.from);
    upsertPerson(naam, { telegram_id: ctx.from.id });
    const stop = typing(ctx);
    try {
      if (await resumePreparedTelegramTurn(ctx, naam)) return;
      // Mint en valideer de archived principal/claim-authority vóór downloads,
      // ffmpeg of het eerste vision-modelwerk. De latere inhoudsbinding kan
      // uitsluitend dit private, nog actuele capability afmaken.
      const turnAuthority = beginTelegramUnderstanding(ctx, naam);
      const buf = await downloadTelegramFile(ctx, video.file_id);
      const beschrijving = await describeVideoBuffer(buf, video.mime_type || 'video/mp4');
      const analyzedText = telegramVideoUnderstandingText({
        sender: naam,
        caption: ctx.message.caption,
        description: beschrijving,
        kind: telegramMessageKind(ctx.message),
        fileId: video.file_id,
        fileUniqueId: video.file_unique_id,
        replyToMessageId: ctx.message.reply_to_message?.message_id ?? null,
      });
      const { handleMessageDetailed } = await import('../mind/agent.js');
      const handled = await handleMessageDetailed({
        channel: 'telegram',
        sender: naam,
        text: analyzedText,
        messageContext: authenticatedTelegramMessageContext(ctx, {
          replyToMessageId: ctx.message.reply_to_message?.message_id
            ? String(ctx.message.reply_to_message.message_id)
            : null,
          derivedText: analyzedText,
        }, {
          authority: turnAuthority,
          requester: naam,
          originalText: analyzedText,
        }),
      });
      await deliverTelegramTurn({ ctx, sender: naam, handled });
    } finally {
      stop();
    }
  });

  bot.catch((err) => logTimeline('system', `Telegram-fout: ${errMsg(err.error || err).slice(0, 200)}`));

  startPreparedTelegramOutbox();
  // long polling start (loopt voor altijd; niet awaiten)
  pollTelegramConcurrently(bot, {
    onError: (error) => logTimeline('system', `Telegram-fout: ${errMsg(error.error || error).slice(0, 200)}`),
  }).catch((e) => {
    logTimeline('system', `Telegram kon niet starten: ${errMsg(e).slice(0, 200)}. Klopt het token?`);
  });
  logTimeline('system', 'Telegram verbonden. Eva is bereikbaar op je telefoon 📱');

  registerMessenger(sendToFamily, reconcileSupersededToFamily);
  registerPhotoSender(sendPhotoToFamily);
  registerVoiceSender(sendVoiceToFamily);
}

export function telegramPollingOptions() {
  return { drop_pending_updates: false };
}

// Noodrem tegen herhaalde berichten: stuur niet bijna-hetzelfde binnen 2 uur.
// Per ONTVANGER bijhouden — anders onderdrukt een bericht aan A er één aan B
// alleen omdat de tekst leek op iets dat net naar B ging.
const recentVerstuurd = []; // {tekst, ts, aan, dedupeKey}
function reserveOutgoingMessage(tekst, aan = null, opties = null) {
  const nu = Date.now();
  while (recentVerstuurd.length && nu - recentVerstuurd[0].ts > 2 * 3600_000) recentVerstuurd.shift();
  const z = normalizeTelegramRecipient(aan);
  const dedupeKey = String(opties?.dedupeKey || '').trim() || null;
  if (dedupeKey && recentVerstuurd.some((r) => (
    (r.aan || '') === z && r.dedupeKey === dedupeKey
  ))) return { tekst, ts: nu, aan: z, dedupeKey, durableReplay: true };
  if (opties?.directReply !== true
    && recentVerstuurd.some((r) => (r.aan || '') === z && tekstOverlap(r.tekst, tekst) >= 0.6)) return null;
  const reservation = { tekst, ts: nu, aan: z, dedupeKey };
  recentVerstuurd.push(reservation);
  return reservation;
}

function releaseOutgoingMessage(reservation) {
  const index = recentVerstuurd.indexOf(reservation);
  if (index >= 0) recentVerstuurd.splice(index, 1);
}

function telegramId(value) {
  if (value === null || value === undefined) return '';
  return String(value).trim();
}

function targetFailure(code, info, recipient = null) {
  return {
    ok: false,
    code,
    info,
    recipient,
    broadcast: false,
    targets: [],
  };
}

function targetsFor(aan, opties = null) {
  if (opties?.directReply === true) {
    const chatId = telegramId(opties?.directReplyChatId);
    if (!chatId) {
      return targetFailure(
        'TELEGRAM_DIRECT_CHAT_REQUIRED',
        'Direct Telegram-antwoord mist de exacte chat-id.',
        aan,
      );
    }
    return {
      ok: true,
      code: 'TELEGRAM_DIRECT_REPLY_RESOLVED',
      info: `exacte antwoordchat voor ${aan || chatId}`,
      recipient: aan || chatId,
      broadcast: false,
      targets: [{ name: aan || chatId, telegram_id: chatId }],
    };
  }
  const people = opties?.householdOnly === true ? householdMembers(allPeople()) : allPeople();
  const resolved = resolveTelegramTargets(people, aan, {
    broadcast: opties?.broadcast === true,
  });
  if (!resolved.ok || opties?.householdOnly !== true) return resolved;
  const actual = resolved.targets.map((person) => telegramId(person.telegram_id)).sort();
  const expected = Array.isArray(opties?.expectedTargetIds)
    ? opties.expectedTargetIds.map(telegramId).sort() : [];
  return expected.length > 0 && canonicalJson(actual) === canonicalJson(expected)
    ? resolved
    : targetFailure(
      'TELEGRAM_HOUSEHOLD_TARGET_DRIFT',
      'De exact gebonden gezinsontvangers zijn intussen gewijzigd; er is niets verstuurd.',
      aan,
    );
}

async function retractTelegramFanout(sent, reservation = null) {
  let removed = 0;
  for (const item of sent) {
    const pending = archiveTelegramMessage({
      chatId: item.chatId, messageId: item.messageId, role: 'assistant',
      meta: { retractionPending: true, retractionUncertain: false },
    });
    if (!pending || pending.deleted) continue;
    try {
      const deleted = await bot.api.deleteMessage(item.chatId, item.messageId);
      if (deleted !== true) throw new Error('Telegram bevestigde intrekken niet');
      const marked = markTelegramMessageDeleted(item.chatId, item.messageId)
        || archiveTelegramMessage({
          chatId: item.chatId, messageId: item.messageId, role: 'assistant', deleted: true,
          meta: { retracted: true, retractionPending: false, retractionUncertain: false },
        })?.deleted;
      if (marked) {
        archiveTelegramMessage({
          chatId: item.chatId, messageId: item.messageId,
          meta: { retracted: true, retractionPending: false, retractionUncertain: false },
        });
        removed += 1;
      }
    } catch {
      try {
        archiveTelegramMessage({
          chatId: item.chatId, messageId: item.messageId,
          meta: { retractionPending: true, retractionUncertain: true },
        });
      } catch { /* lokale onzekerheid blijft zichtbaar in het resultaat */ }
    }
  }
  const ok = removed === sent.length;
  if (ok && reservation) releaseOutgoingMessage(reservation);
  return { ok, removed, total: sent.length, visible: removed < sent.length };
}

function telegramFanoutFailure(error, person) {
  return {
    recipient: person.name, chatId: telegramId(person.telegram_id),
    classification: error?.classification || 'uncertain',
    retryable: error?.retryable === true, uncertain: error?.uncertain !== false,
    operationKey: error?.operationKey || null, info: errMsg(error).slice(0, 240),
  };
}

function supersededTerminalFanoutResult(result) {
  if (!result || result.accepted === true) return null;
  if (result.stale === true) return { ...result, retryable: false };
  const sent = Array.isArray(result.sent) ? result.sent : [];
  const visible = result.visible === true || result.partial === true
    || result.uncertain === true || sent.length > 0;
  return {
    ...result,
    ok: false, accepted: false, stale: true, visible, uncertain: visible,
    retryable: false,
    retraction: result.retraction || {
      ok: !visible, removed: 0, total: sent.length, visible,
    },
    info: visible
      ? 'De terminale ingehaalde fanout kan zichtbaar zijn; opnieuw sturen is geblokkeerd.'
      : 'De terminale ingehaalde fanout had aantoonbaar geen zichtbaar effect.',
  };
}

function completedTelegramFanoutReconciliation({
  fanoutOperationKey, deliveryKey, kind, payloadSha256, captionSha256, targetIds,
}) {
  const reconciliationKey = `telegram-fanout-reconcile:${sha256(canonicalJson({
    contract: 'TelegramFanoutReconciliationOperation/v1', fanoutOperationKey,
  }))}`;
  try {
    const row = validateExecutionRecord(db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(reconciliationKey));
    const expectedIntent = {
      contract: 'TelegramFanoutReconciliationIntent/v1', fanoutOperationKey,
      deliveryKey, kind, payloadSha256, captionSha256, targetIds,
    };
    if (row.tool !== '@transport/telegram.reconcile'
      || canonicalJson(JSON.parse(row.args || '{}')) !== canonicalJson(expectedIntent)) return null;
    const terminal = JSON.parse(row.output || '{}');
    return terminal?.contract === 'TelegramFanoutReconciliationResult/v1'
      && terminal.result?.stale === true ? terminal.result : null;
  } catch { return null; }
}

async function revalidateAcceptedTelegramFanout({
  kind, value, caption, targetIds, options, terminal, fanoutOperationKey,
}) {
  const sent = [];
  try {
    for (const chatId of targetIds) {
      const messages = kind === 'text'
        ? await reconcileTelegramText(chatId, value, options)
        : [await reconcileTelegramMedia({
          kind, chatId, buffer: value, caption,
          deliveryKey: options.dedupeKey, sendOptions: options,
        })];
      sent.push(...messages.map((message) => ({
        chatId: telegramId(message?.chat?.id) || telegramId(chatId),
        messageId: message.message_id,
        fileId: kind === 'photo' ? message.photo?.at(-1)?.file_id : message.audio?.file_id,
        ...message._evaTransport,
      })));
    }
    const messageKeys = sent.map((item) => `${item.chatId}:${item.messageId}`);
    const operationKeys = sent.map((item) => String(item.operationKey || ''));
    const terminalOperationKeys = Array.isArray(terminal.result?.sent)
      ? terminal.result.sent.map((item) => String(item.operationKey || '')) : [];
    if (!sent.length
      || canonicalJson(messageKeys) !== canonicalJson(terminal.messageKeys || [])
      || canonicalJson(operationKeys) !== canonicalJson(terminalOperationKeys)) {
      throw deliveryError('uncertain',
        'Het fanoutmanifest wijkt af van de opnieuw gevalideerde childreceipts.');
    }
    return { ...terminal.result, sent, replayed: true };
  } catch (error) {
    return {
      ok: false, accepted: false, uncertain: true, visible: true,
      retryable: false, needs_followup: true, fanoutOperationKey,
      sent: [], failures: [],
      info: `Eerdere Telegramacceptatie kan niet meer exact worden bewezen: ${errMsg(error)}`,
    };
  }
}

function telegramFanoutReconciliationFailure(context, info, row = context.existingFanout) {
  const result = {
    ok: false, accepted: false, stale: true, visible: true, uncertain: true,
    retraction: { ok: false, removed: 0, total: 0, visible: true },
    sent: [], failures: [], info,
  };
  if (row?.status === 'started') {
    try {
      completeTurnToolExecution(context.fanoutOperationKey, canonicalJson({
        contract: 'TelegramFanoutResult/v1', accepted: false,
        targetIds: Array.isArray(context.targetIds) ? context.targetIds : [],
        messageKeys: [], result,
      }));
    } catch { /* de onzekere bestaande intent blijft in elk geval nooit herverstuurd */ }
  }
  return result;
}

function resolveTelegramFanoutIntent(context) {
  const {
    reconcileSuperseded, fanoutOperationKey, key, kind, payloadSha256,
    captionSha256, aan, opties,
  } = context;
  if (!reconcileSuperseded) {
    const resolved = targetsFor(aan, opties);
    if (!resolved.ok) return { failure: resolved };
    const targetIds = resolved.targets.map((person) => telegramId(person.telegram_id)).sort();
    return {
      existingFanout: null, resolved, targetIds,
      fanoutIntent: {
        contract: 'TelegramFanoutIntent/v1', kind, deliveryKey: key,
        broadcast: resolved.broadcast === true,
        directReply: opties?.directReply === true,
        payloadSha256, captionSha256, targetIds,
      },
    };
  }
  const existingFanout = db.prepare(`SELECT status,tool,args FROM main.turn_tool_executions
    WHERE execution_key=?`).get(fanoutOperationKey) || null;
  let frozenIntent = null;
  try { frozenIntent = JSON.parse(String(existingFanout?.args || '')); } catch { /* invalid */ }
  const targetIds = Array.isArray(frozenIntent?.targetIds)
    ? frozenIntent.targetIds.map(telegramId).sort() : [];
  const exactIntentKeys = [
    'broadcast', 'captionSha256', 'contract', 'deliveryKey', 'directReply',
    'kind', 'payloadSha256', 'targetIds',
  ];
  const frozenValid = existingFanout?.tool === '@transport/telegram.fanout'
    && ['started', 'done'].includes(String(existingFanout?.status || ''))
    && canonicalJson(Object.keys(frozenIntent || {}).sort()) === canonicalJson(exactIntentKeys)
    && frozenIntent.contract === 'TelegramFanoutIntent/v1'
    && frozenIntent.deliveryKey === key && frozenIntent.kind === kind
    && frozenIntent.payloadSha256 === payloadSha256
    && frozenIntent.captionSha256 === captionSha256
    && frozenIntent.broadcast === (opties?.broadcast === true)
    && frozenIntent.directReply === (opties?.directReply === true)
    && targetIds.length > 0 && new Set(targetIds).size === targetIds.length
    && canonicalJson(frozenIntent.targetIds) === canonicalJson(targetIds)
    && targetIds.every((id) => /^\d+$/u.test(id));
  const route = { existingFanout, targetIds };
  if (!frozenValid) return {
    ...route,
    failure: telegramFanoutReconciliationFailure(
      { ...context, ...route },
      'De bevroren Telegramfanout kon niet exact aan de superseded reply worden gebonden.',
    ),
  };
  return {
    ...route,
    fanoutIntent: frozenIntent,
    resolved: {
      ok: true, broadcast: frozenIntent.broadcast === true,
      recipient: aan || null,
      targets: targetIds.map((id) => ({ name: aan || id, telegram_id: id })),
    },
  };
}

async function replayTerminalTelegramFanout({
  context, fanoutClaim, reservation, value, caption,
}) {
  if (fanoutClaim.claimed || fanoutClaim.row?.status !== 'done') return null;
  try {
    const terminal = JSON.parse(String(fanoutClaim.row.output || ''));
    if (terminal?.contract !== 'TelegramFanoutResult/v1' || !terminal.result) return null;
    if (context.reconcileSuperseded && terminal.result.accepted === true) return null;
    if (terminal.result.accepted === true) {
      const reconciled = completedTelegramFanoutReconciliation({
        fanoutOperationKey: context.fanoutOperationKey,
        deliveryKey: context.key,
        kind: context.kind,
        payloadSha256: context.payloadSha256,
        captionSha256: context.captionSha256,
        targetIds: context.targetIds,
      });
      if (reconciled) {
        if (reconciled.visible !== true && reservation) releaseOutgoingMessage(reservation);
        return { ...reconciled, replayed: true };
      }
      const revalidated = await revalidateAcceptedTelegramFanout({
        kind: context.kind, value, caption, targetIds: context.targetIds,
        options: context.opties, terminal,
        fanoutOperationKey: context.fanoutOperationKey,
      });
      if (revalidated.accepted !== true && reservation) releaseOutgoingMessage(reservation);
      return revalidated;
    }
    const replay = context.reconcileSuperseded
      ? supersededTerminalFanoutResult(terminal.result) : terminal.result;
    if (replay?.accepted !== true && replay?.visible !== true && reservation) {
      releaseOutgoingMessage(reservation);
    }
    return { ...replay, replayed: true };
  } catch {
    // Oudere fanoutrecords worden veilig via chunkdedupe gereconcilieerd.
    return null;
  }
}

async function reconcileSupersededTelegramFanout({
  context, resolved, reservation, completeFanout, retract, sent, failures, value,
}) {
  const {
    fanoutOperationKey, key, kind, payloadSha256, captionSha256, targetIds, opties,
  } = context;
  const reconciliationKey = `telegram-fanout-reconcile:${sha256(canonicalJson({
    contract: 'TelegramFanoutReconciliationOperation/v1', fanoutOperationKey,
  }))}`;
  const reconciliationIntent = {
    contract: 'TelegramFanoutReconciliationIntent/v1', fanoutOperationKey,
    deliveryKey: key, kind, payloadSha256, captionSha256, targetIds,
  };
  let reconciliationClaim;
  try {
    reconciliationClaim = claimTurnToolExecution({
      executionKey: reconciliationKey,
      turnId: `telegram-fanout-reconcile:${key}`,
      tool: '@transport/telegram.reconcile', args: reconciliationIntent,
    });
  } catch (error) {
    return telegramFanoutReconciliationFailure(
      context, `Telegramreconciliatie botste: ${errMsg(error)}`,
    );
  }
  const completeReconciliation = (result) => completeTurnToolExecution(
    reconciliationKey,
    canonicalJson({ contract: 'TelegramFanoutReconciliationResult/v1', result }),
  );
  if (!reconciliationClaim.claimed) {
    if (reconciliationClaim.row?.status === 'done') {
      try {
        const terminal = JSON.parse(String(reconciliationClaim.row.output || ''));
        if (terminal?.contract === 'TelegramFanoutReconciliationResult/v1'
          && terminal.result?.stale === true) {
          if (terminal.result.visible !== true && reservation) {
            releaseOutgoingMessage(reservation);
          }
          return { ...terminal.result, replayed: true, retract };
        }
      } catch { /* beschadigd terminalbewijs wordt hieronder onzeker gesloten */ }
    }
    const interrupted = {
      ok: false, accepted: false, stale: true, visible: true, uncertain: true,
      retraction: { ok: false, removed: 0, total: 0, visible: true },
      sent: [], failures: [],
      info: 'Proces stopte tijdens Telegramreconciliatie; opnieuw sturen is geblokkeerd.',
    };
    if (reconciliationClaim.row?.status === 'started') completeReconciliation(interrupted);
    completeFanout(interrupted);
    return { ...interrupted, retract };
  }
  for (const person of resolved.targets) {
    try {
      const messages = kind === 'text'
        ? await reconcileTelegramText(person.telegram_id, value, opties) : [];
      sent.push(...messages.map((message) => ({
        chatId: telegramId(message?.chat?.id) || telegramId(person.telegram_id),
        messageId: message.message_id,
        ...message._evaTransport,
      })));
    } catch (error) {
      sent.push(...(error?.acceptedPrefix || []).map((message) => ({
        chatId: telegramId(message?.chat?.id) || telegramId(person.telegram_id),
        messageId: message.message_id,
        ...message._evaTransport,
      })));
      failures.push(telegramFanoutFailure(error, person));
    }
  }
  const retraction = sent.length
    ? await retract() : { ok: true, removed: 0, total: 0, visible: false };
  const visible = retraction.visible === true
    || failures.some((failure) => failure.uncertain);
  if (!visible && !sent.length && reservation) releaseOutgoingMessage(reservation);
  const result = {
    ok: false, accepted: false, stale: true, visible,
    uncertain: visible, retraction, sent, failures,
    info: visible
      ? 'ingehaalde Telegram-payload kan nog zichtbaar zijn; opnieuw sturen is geblokkeerd'
      : 'ingehaalde Telegram-payload is ingetrokken of aantoonbaar niet verstuurd',
  };
  completeReconciliation(result);
  completeFanout(result);
  return { ...result, retract };
}

function telegramFanoutPayloadBinding(kind, value, caption) {
  return {
    payloadSha256: kind === 'text'
      ? sha256(canonicalTelegramText(value)) : sha256(value),
    captionSha256: kind === 'text'
      ? null : sha256(redactSensitiveText(String(caption || '')).slice(0, 1_024)),
  };
}

function telegramMonitoringTransportLimits(options) {
  let present = false;
  let value;
  try {
    present = Boolean(options && Reflect.has(options, 'monitoringTransportLimits'));
    value = present ? Reflect.get(options, 'monitoringTransportLimits') : undefined;
  } catch { return null; }
  if (!present) return undefined;
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort()) !== canonicalJson([
      'horizonAt', 'latestWireStart', 'wireNotAfter',
    ])) return null;
  const values = [value.latestWireStart, value.wireNotAfter, value.horizonAt];
  const times = values.map((entry) => typeof entry === 'string' ? Date.parse(entry) : Number.NaN);
  if (times.some((entry, index) => !Number.isFinite(entry)
    || new Date(entry).toISOString() !== values[index]
    || (index > 0 && entry <= times[index - 1]))) return null;
  return Object.freeze({
    horizonAt: value.horizonAt,
    latestWireStart: value.latestWireStart,
    wireNotAfter: value.wireNotAfter,
  });
}

function telegramMonitoringStartCurrent(options) {
  const limits = telegramMonitoringTransportLimits(options);
  return limits === undefined || (limits !== null
    && Date.now() <= Date.parse(limits.latestWireStart));
}

function telegramFanoutCurrentCheck(options, binding = null) {
  const current = typeof options?.isCurrent === 'function' ? options.isCurrent : () => true;
  return () => {
    try {
      if (current() !== true) return false;
      if (options?.householdOnly !== true || !binding) return true;
      const resolved = targetsFor(binding.aan, options);
      const ids = resolved.ok
        ? resolved.targets.map((person) => telegramId(person.telegram_id)).sort() : [];
      return resolved.ok && canonicalJson(ids) === canonicalJson(binding.targetIds);
    } catch { return false; }
  };
}

function telegramFanoutMediaSendOptions(kind, caption, opties, isCurrent) {
  return kind === 'audio'
    ? { caption, title: 'Eva', performer: 'Eva',
      messageThreadId: opties?.messageThreadId, isCurrent,
      checkCurrentBeforeEachTransport:
        opties?.checkCurrentBeforeEachTransport === true }
    : { caption, messageThreadId: opties?.messageThreadId, isCurrent,
      checkCurrentBeforeEachTransport:
        opties?.checkCurrentBeforeEachTransport === true };
}

// De fanout-state-machine houdt claim, childreceipts, crashreplay en retractie
// bewust in één effectgrens; opsplitsen zou de atomair gecontroleerde toestand
// tussen helpers opnieuw als clonebare authority moeten doorgeven.
async function fanoutTelegram({ kind, value, caption = '', contentType = null, aan, opties }) {
  const key = String(opties?.dedupeKey || '').trim();
  if (!key) return {
    ok: false, accepted: false, code: 'TELEGRAM_DURABLE_KEY_REQUIRED',
    info: 'Telegram-payload niet verstuurd: duurzame deduplicatiesleutel ontbreekt',
  };
  const fanoutOperationKey = `telegram-fanout:${sha256(canonicalJson({
    contract: 'TelegramFanoutOperation/v1', deliveryKey: key,
  }))}`;
  const reconcileSuperseded = opties?.reconcileSuperseded === true;
  const { payloadSha256, captionSha256 } = telegramFanoutPayloadBinding(
    kind, value, caption,
  );
  const baseContext = {
    key, kind, aan, opties, reconcileSuperseded, fanoutOperationKey,
    payloadSha256, captionSha256,
  };
  const route = resolveTelegramFanoutIntent(baseContext);
  if (route.failure) return route.failure;
  const { resolved, targetIds, fanoutIntent } = route;
  const context = { ...baseContext, ...route };
  if (!bot) {
    return reconcileSuperseded
      ? telegramFanoutReconciliationFailure(
        context, 'Telegram is niet actief; zichtbaarheid van de oude fanout is onzeker.',
      )
      : { ok: false, accepted: false, info: 'Telegram is niet actief' };
  }
  const recipientKey = `telegram:${targetIds.join(',')}`;
  const reservation = kind === 'text'
    ? reserveOutgoingMessage(value, recipientKey, opties) : null;
  if (kind === 'text' && !reservation) {
    return { ok: false, accepted: false, info: 'bijna-hetzelfde net al gestuurd, niet herhaald' };
  }
  let fanoutClaim;
  try {
    fanoutClaim = claimTurnToolExecution({
      executionKey: fanoutOperationKey,
      turnId: `telegram-fanout:${key}`,
      tool: '@transport/telegram.fanout',
      args: fanoutIntent,
    });
  } catch (error) {
    if (reservation) releaseOutgoingMessage(reservation);
    if (reconcileSuperseded) {
      return telegramFanoutReconciliationFailure(
        context,
        `De bevroren Telegramfanout botste tijdens reconciliatie: ${errMsg(error)}`,
      );
    }
    return {
      ok: false, accepted: false, uncertain: true,
      code: 'TELEGRAM_FANOUT_BINDING_CONFLICT',
      info: errMsg(error), failures: [], sent: [],
    };
  }
  const replay = await replayTerminalTelegramFanout({
    context, fanoutClaim, reservation, value, caption,
  });
  if (replay) return replay;
  const fanoutAlreadyDone = !fanoutClaim.claimed && fanoutClaim.row?.status === 'done';
  const completeFanout = (result) => {
    if (fanoutAlreadyDone) return;
    completeTurnToolExecution(fanoutOperationKey, canonicalJson({
      contract: 'TelegramFanoutResult/v1',
      accepted: result?.accepted === true,
      targetIds,
      messageKeys: (result?.sent || []).map((item) => `${item.chatId}:${item.messageId}`),
      result,
    }));
  };
  if (!telegramMonitoringStartCurrent(opties)) {
    if (reservation) releaseOutgoingMessage(reservation);
    const result = {
      ok: false, accepted: false, uncertain: false, stale: false,
      code: 'TELEGRAM_MONITORING_WIRE_START_EXPIRED',
      info: 'De gebonden monitoringdeadline is verstreken vóór nieuw Telegramtransport.',
      sent: [], failures: [],
    };
    completeFanout(result);
    return result;
  }
  const isCurrent = telegramFanoutCurrentCheck(opties, { aan, targetIds });
  const guardedOptions = { ...(opties || {}), isCurrent };
  const sent = [];
  const failures = [];
  const retract = () => retractTelegramFanout(sent, reservation);
  const staleResult = async () => {
    const retraction = await retract();
    const uncertain = retraction.visible === true;
    const result = {
      ok: false, accepted: false, stale: true, visible: retraction.visible,
      uncertain,
      retraction, sent, failures, info: 'ingehaalde Telegram-payload ingetrokken',
    };
    completeFanout(result);
    return { ...result, retract };
  };
  if (reconcileSuperseded) {
    return reconcileSupersededTelegramFanout({
      context, resolved, reservation, completeFanout, retract, sent, failures, value,
    });
  }
  if (!isCurrent()) return staleResult();
  for (const person of resolved.targets) {
    if (!isCurrent()) return staleResult();
    try {
      const messages = kind === 'text'
        ? await sendTelegramText(person.telegram_id, value, guardedOptions)
        : [await sendTelegramMedia({
          kind, chatId: person.telegram_id, buffer: value, caption, deliveryKey: key,
          sendOptions: telegramFanoutMediaSendOptions(kind, caption, opties, isCurrent),
        })];
      sent.push(...messages.map((message) => ({
        chatId: telegramId(message?.chat?.id) || telegramId(person.telegram_id),
        messageId: message.message_id,
        fileId: kind === 'photo' ? message.photo?.at(-1)?.file_id : message.audio?.file_id,
        ...message._evaTransport,
      })));
    } catch (error) {
      sent.push(...(error?.acceptedPrefix || []).map((message) => ({
        chatId: telegramId(message?.chat?.id) || telegramId(person.telegram_id),
        messageId: message.message_id,
        ...message._evaTransport,
      })));
      failures.push(telegramFanoutFailure(error, person));
    }
    if (!isCurrent()) return staleResult();
  }
  const accepted = !failures.length && sent.length > 0;
  if (accepted) {
    const result = {
      ok: true, accepted: true, transport: 'telegram', kind,
      fanoutOperationKey,
      info: `naar ${resolved.targets.length}`, sent,
    };
    completeFanout(result);
    try {
      logTimeline('transport', `Telegram accepteerde ${kind} voor ${aan || 'het gezin'}.`, {
        internalOnly: true, transportEvidence: true, channel: 'telegram',
        recipient: aan || null, transportAccepted: true, recipientConfirmed: false,
        transportType: kind, contentRecordedSeparately: true, dedupeKey: key,
        messageKeys: sent.map((item) => `${item.chatId}:${item.messageId}`),
      });
    } catch { /* het duurzame transportbewijs blijft leidend */ }
    return { ...result, retract };
  }
  const partial = sent.length > 0;
  const retryable = !partial && failures.length > 0 && failures.every((item) => item.retryable);
  if (retryable && reservation) releaseOutgoingMessage(reservation);
  const result = {
    ok: false, accepted: false, partial,
    uncertain: partial || failures.some((item) => item.uncertain),
    retryable, needs_followup: partial || failures.some((item) => item.uncertain),
    info: partial ? 'Telegram accepteerde slechts een deel; er is geen volledig afleverbewijs.' : 'versturen mislukte',
    failures, sent, contentType,
  };
  if (!retryable) completeFanout(result);
  return { ...result, retract };
}

function rememberTelegramPhotoFanout(result, buffer, contentType, caption, opties) {
  if (result.stale || !result.sent?.length) return null;
  let photoRef = null;
  try {
    photoRef = rememberNameablePhoto({
      buffer, contentType: contentType || 'image/jpeg', caption,
      description: opties?.description || '', camera: cameraFromCaption(caption),
      cameraKey: opties?.cameraKey || null, suggestedPerson: opties?.suggestedPerson || null,
      candidateSimilarity: opties?.candidateSimilarity ?? null,
      candidateMargin: opties?.candidateMargin ?? null, telegramMessages: result.sent,
    });
    if (photoRef) for (const item of result.sent) {
      archiveTelegramMessage({ chatId: item.chatId, messageId: item.messageId, mediaRef: photoRef });
    }
  } catch (error) {
    logTimeline('system', `Verzonden Telegram-foto kon niet duurzaam worden gekoppeld: ${errMsg(error).slice(0, 180)}`);
  }
  return photoRef;
}

function publicTelegramOptions(opties) {
  const safe = { ...(opties || {}) };
  // Dit veld is geen publieke transportoptie. Alleen de module-private
  // reconciliatiecallback, aangeroepen met een eenmalig tools-attest, mag het
  // fanoutprotocol in retract-only modus zetten.
  delete safe.reconcileSuperseded;
  return safe;
}

function publicReconciliationRejection() {
  return {
    ok: false, accepted: false, stale: false, visible: false, uncertain: false,
    code: 'TELEGRAM_RECONCILIATION_AUTHORITY_REQUIRED',
    info: 'Telegramreconciliatie is alleen beschikbaar via de intern gebonden reply-gate.',
  };
}

function hasPublicReconciliationFlag(opties) {
  if (!opties) return false;
  try {
    const prototype = Object.getPrototypeOf(opties);
    if (prototype !== Object.prototype && prototype !== null) return true;
    return Reflect.has(opties, 'reconcileSuperseded')
      || Reflect.get(opties, 'reconcileSuperseded') !== undefined;
  } catch {
    // Een Proxy/getter die haar private-veldstatus niet betrouwbaar laat
    // inspecteren krijgt evenmin normale sendbevoegdheid.
    return true;
  }
}

function reconcileSupersededToFamily(tekst, aan = null, opties = null) {
  return fanoutTelegram({
    kind: 'text', value: tekst, aan,
    opties: { ...(opties || {}), reconcileSuperseded: true },
  });
}

// De scheduler kan al recoverywerk zien vóór startTelegram() de normale
// messenger activeert. Installeer daarom alleen deze module-private callback
// deterministisch bij module-evaluatie; zonder gewone messenger kan zij nog
// steeds niet publiek of buiten de reply-gate worden aangeroepen.
registerMessenger(undefined, reconcileSupersededToFamily);

export function sendToFamily(tekst, aan = null, opties = null) {
  if (hasPublicReconciliationFlag(opties)) {
    return Promise.resolve(publicReconciliationRejection());
  }
  return fanoutTelegram({ kind: 'text', value: tekst, aan, opties: publicTelegramOptions(opties) });
}

export async function sendPhotoToFamily(buffer, contentType, caption, aan = null, opties = null) {
  if (hasPublicReconciliationFlag(opties)) return publicReconciliationRejection();
  const safeOptions = publicTelegramOptions(opties);
  const result = await fanoutTelegram({
    kind: 'photo', value: buffer, caption, contentType, aan, opties: safeOptions,
  });
  return { ...result, photoRef: rememberTelegramPhotoFanout(result, buffer, contentType, caption, safeOptions) };
}

export function sendVoiceToFamily(buffer, caption, aan = null, opties = null) {
  if (hasPublicReconciliationFlag(opties)) {
    return Promise.resolve(publicReconciliationRejection());
  }
  return fanoutTelegram({
    kind: 'audio', value: buffer, caption, aan, opties: publicTelegramOptions(opties),
  });
}
