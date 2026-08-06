// Telegram: Eva's lijntje naar de telefoons van het gezin.
import { Bot, InputFile } from 'grammy';
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
  claimTelegramMessage,
  completeTelegramMessage,
  failTelegramMessage,
  logTimeline,
  markTelegramMessageDeleted,
  newerTelegramUserMessage,
  pendingPreparedTelegramMessages,
  personByTelegram,
  recentTelegramMessages,
  recoverInterruptedTelegramMessages,
  telegramOutboundByDedupeKey,
  telegramMessage,
  upsertPerson,
} from '../db.js';
import { handleMessageDetailed, recordDeliveredReply } from '../mind/agent.js';
import {
  registerMessenger,
  registerPhotoSender,
  registerVoiceSender,
} from '../mind/tools.js';
import {
  nameablePhotoForTelegramMessage,
  onthoudOntvangenFoto,
  rememberNameablePhoto,
} from '../mind/vision.js';
import { chatComplete, imageMsg } from '../brain/llm.js';
import { errMsg, tekstOverlap } from '../util.js';

const execFile = promisify(execFileCb);
let bot = null;
const holdDirectInteraction = () => () => {};

const allowed = (id) => !config.telegramAllowedIds.length || config.telegramAllowedIds.includes(id);

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
      ...(meta && typeof meta === 'object' ? meta : {}),
    },
  });
}

function archiveTelegramEnvelope(ctx) {
  if (!ctx?.message || !ctx?.chat) return;
  const current = archiveTelegramPayload({
    chatId: ctx.chat.id,
    message: ctx.message,
    sender: ctx.from ? resolveName(ctx.from) : null,
    role: 'user',
    meta: { inbound: true },
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

async function sendTelegramText(chatId, tekst, opties = null) {
  const sent = [];
  const chunks = splitTelegramText(tekst);
  for (const [index, deel] of chunks.entries()) {
    const deliveryKey = String(opties?.dedupeKey || '').trim() || null;
    const chunkKey = deliveryKey
      ? `${deliveryKey}:chunk:${index + 1}`
      : null;
    const legacyChunkKey = deliveryKey
      ? `${deliveryKey}:chunk:${index + 1}-of-${chunks.length}`
      : null;
    const existing = chunkKey
      ? telegramOutboundByDedupeKey(chunkKey, chatId)
        || telegramOutboundByDedupeKey(legacyChunkKey, chatId)
        || (chunks.length === 1 ? telegramOutboundByDedupeKey(deliveryKey, chatId) : null)
      : null;
    if (existing) {
      sent.push({
        chat: { id: existing.chat_id },
        message_id: Number(existing.message_id) || existing.message_id,
        _evaRecovered: true,
      });
      continue;
    }

    const message = await bot.api.sendMessage(chatId, deel, telegramTextSendOptions(opties, index));
    try {
      archiveTelegramPayload({
        chatId,
        message,
        sender: 'Eva',
        role: 'assistant',
        replyToMessageId: index === 0 ? opties?.replyToMessageId : null,
        meta: {
          outbound: true,
          chunkIndex: index,
          chunkCount: chunks.length,
          dedupeKey: chunkKey || deliveryKey,
          deliveryKey,
          directTurnSignalRef: opties?.directTurnSignalRef || null,
        },
      });
    } catch (e) {
      // Telegram heeft het bericht dan al geaccepteerd. Een lokaal archieffoutje
      // mag nooit tot een tweede verzendpoging of een onterechte mislukking leiden.
      logTimeline('system', `Verzonden Telegram-tekst kon niet lokaal worden gearchiveerd: ${errMsg(e).slice(0, 180)}`, {
        internalOnly: true,
        telegramArchive: true,
        chatId: String(chatId),
        messageId: String(message.message_id),
      });
    }
    sent.push(message);
  }
  return sent;
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

async function publishDirectTelegramReply(
  { ctx, handled },
) {
  const identity = directTurnIdentity(ctx);
  const reply = String(handled?.reply || '');
  if (!reply.trim()) throw new Error('Agentbeurt leverde geen antwoord op');
  const messageThreadId = positiveTelegramInteger(ctx?.message?.message_thread_id);
  const sent = await sendTelegramText(identity.chatId, reply, {
    replyToMessageId: identity.messageId,
    messageThreadId,
    dedupeKey: identity.dedupeKey,
    directTurnSignalRef: identity.signalRef,
  });
  return {
    ok: true,
    accepted: true,
    deduplicated: sent.length > 0 && sent.every((message) => message._evaRecovered === true),
    sent: sent.map((message) => ({
      chatId: String(message.chat.id),
      messageId: String(message.message_id),
    })),
    ...identity,
  };
}

function preparedTelegramResult(ctx) {
  const archived = telegramMessage(ctx?.chat?.id, ctx?.message?.message_id);
  const prepared = archived?.meta?.preparedReply;
  if (!prepared?.turnId || !prepared?.reply) return null;
  return {
    turnId: String(prepared.turnId),
    reply: String(prepared.reply),
    channel: 'telegram',
    sender: String(prepared.sender || resolveName(ctx.from)),
    conversationId: String(prepared.conversationId || `telegram:${ctx.chat.id}`),
    tools: Array.isArray(prepared.tools) ? prepared.tools : [],
    goalEpisodeId: Number(prepared.goalEpisodeId) || null,
    workItemId: Number(prepared.workItemId) || null,
    originMessageId: String(prepared.originMessageId || ctx?.message?.message_id || '') || null,
    syntheticTest: prepared.syntheticTest === true,
    recovered: true,
  };
}

function persistPreparedTelegramReply(ctx, sender, handled) {
  archiveTelegramMessage({
    chatId: ctx.chat.id,
    messageId: ctx.message.message_id,
    meta: {
      preparedReply: {
        turnId: handled.turnId,
        reply: String(handled.reply || ''),
        sender,
        conversationId: handled.conversationId || `telegram:${ctx.chat.id}`,
        goalEpisodeId: Number(handled.goalEpisodeId) || null,
        workItemId: Number(handled.workItemId) || null,
        originMessageId: String(handled.originMessageId || ctx.message.message_id),
        syntheticTest: handled.syntheticTest === true,
        tools: (handled.tools || []).slice(-12).map((tool) => ({
          name: tool.name,
          ok: tool.ok,
          verified: tool.verified,
          changed: tool.changed,
          output: String(tool.output || '').slice(0, 2_000),
          evidence: tool.evidence || null,
        })),
      },
    },
  });
}

async function deliverTelegramTurn({ ctx, sender, handled }) {
  const superseding = newerTelegramUserMessage(ctx?.chat?.id, ctx?.message?.message_id);
  if (superseding) {
    archiveTelegramMessage({
      chatId: ctx.chat.id,
      messageId: ctx.message.message_id,
      meta: {
        preparedReply: null,
        supersededBy: String(superseding.message_id),
      },
    });
    logTimeline('system', `Telegram-antwoord op ${ctx.message.message_id} niet verstuurd: bericht ${superseding.message_id} had de beurt al gecorrigeerd.`, {
      internalOnly: true,
      telegramArchive: true,
      superseded: true,
      chatId: String(ctx.chat.id),
      messageId: String(ctx.message.message_id),
      supersededBy: String(superseding.message_id),
    });
    return {
      ok: true,
      accepted: false,
      superseded: true,
      supersededBy: String(superseding.message_id),
    };
  }
  persistPreparedTelegramReply(ctx, sender, handled);
  const afterPersist = newerTelegramUserMessage(ctx?.chat?.id, ctx?.message?.message_id);
  if (afterPersist) {
    archiveTelegramMessage({
      chatId: ctx.chat.id,
      messageId: ctx.message.message_id,
      meta: {
        preparedReply: null,
        supersededBy: String(afterPersist.message_id),
      },
    });
    return {
      ok: true,
      accepted: false,
      superseded: true,
      supersededBy: String(afterPersist.message_id),
    };
  }
  const delivery = await publishDirectTelegramReply({ ctx, sender, handled });
  recordDeliveredReply(handled, delivery);
  return delivery;
}

async function resumePreparedTelegramTurn(ctx, sender) {
  const handled = preparedTelegramResult(ctx);
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
      const claim = claimTelegramMessage(row.chat_id, row.message_id);
      if (!claim.claimed) continue;
      const ctx = {
        chat: { id: row.chat_id },
        from: { first_name: row.sender || 'huisgenoot' },
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
        failTelegramMessage(row.chat_id, row.message_id, errMsg(error));
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

// Kleine, vluchtige fotogeheugen-cache voor Telegram. Als iemand reageert op
// een foto die Eva zélf stuurde, levert Telegram niet altijd opnieuw netjes het
// beeld mee. Daarom onthouden we het verzonden beeld bij chat_id+message_id, en
// kunnen we het later alsnog bekijken. Bewust beperkt: geen eindeloze opslag.
const sentPhotoCache = new Map(); // key `${chatId}:${messageId}` -> { ts, contentType, caption, fileId, bufferBase64 }
const MAX_SENT_PHOTOS = 80;
const SENT_PHOTO_TTL_MS = 24 * 3600_000;

function photoKey(chatId, messageId) {
  return `${chatId}:${messageId}`;
}

function pruneSentPhotoCache() {
  const nu = Date.now();
  for (const [key, value] of sentPhotoCache) {
    if (nu - value.ts > SENT_PHOTO_TTL_MS) sentPhotoCache.delete(key);
  }
  while (sentPhotoCache.size > MAX_SENT_PHOTOS) {
    const oudste = sentPhotoCache.keys().next().value;
    if (!oudste) break;
    sentPhotoCache.delete(oudste);
  }
}

function rememberSentPhoto(chatId, messageId, { buffer, contentType = 'image/jpeg', caption = '', fileId = null } = {}) {
  if (!chatId || !messageId || !buffer) return;
  try {
    sentPhotoCache.set(photoKey(chatId, messageId), {
      ts: Date.now(),
      contentType: contentType || 'image/jpeg',
      caption: caption || '',
      fileId,
      bufferBase64: Buffer.from(buffer).toString('base64'),
    });
    pruneSentPhotoCache();
  } catch (e) {
    logTimeline('system', `Kon verzonden Telegram-foto niet onthouden: ${errMsg(e).slice(0, 160)}`);
  }
}

function lookupSentPhoto(chatId, messageId) {
  pruneSentPhotoCache();
  return sentPhotoCache.get(photoKey(chatId, messageId)) || null;
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

async function downloadTelegramPhoto(ctx, photoSizes) {
  const file = await ctx.api.getFile(photoSizes[photoSizes.length - 1].file_id);
  const url = `https://api.telegram.org/file/bot${config.telegramToken}/${file.file_path}`;
  return Buffer.from(await (await fetch(url)).arrayBuffer());
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

export const __telegramMediaTest = {
  inheritedPhotoForShortFollowup,
  isShortPhotoFollowup,
  localPhotoReply,
  telegramReplyChainPhoto,
  telegramPhotoReferenceRole,
};

async function describeRepliedMessage(ctx) {
  const currentArchived = telegramMessage(ctx.chat.id, ctx.message.message_id);
  const reply = ctx.message.reply_to_message || null;
  const replyMessageId = reply?.message_id || currentArchived?.reply_to_message_id || null;
  if (!replyMessageId) return null;

  const repliedArchived = telegramMessage(ctx.chat.id, replyMessageId);
  const photoArchived = telegramReplyChainPhoto(ctx.chat.id, replyMessageId);
  const photoMessageId = String(photoArchived?.message_id || replyMessageId);
  const cached = lookupSentPhoto(ctx.chat.id, photoMessageId);
  const caption = (reply?.photo?.length ? reply.caption : '')
    || cached?.caption
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
      caption,
      ref: durable.id,
      description: durable.description || '',
      relation,
    });
  }

  // Foto's van gezinsleden staan bij een Telegram-reply doorgaans opnieuw in
  // het bericht. Download ze eenmaal, archiveer ze lokaal met dezelfde
  // chat/message-sleutel en geef daarna de pixels aan de gewone Eva-beurt.
  if (reply?.photo?.length) {
    try {
      const buf = await downloadTelegramPhoto(ctx, reply.photo);
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
      return localPhotoReply({ target, buffer: buf, contentType: 'image/jpeg', caption, ref, relation });
    } catch (e) {
      logTimeline('system', `Telegram-replyfoto ophalen mislukte, probeer cache: ${errMsg(e).slice(0, 180)}`);
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
      });
    } catch (e) {
      logTimeline('system', `Gearchiveerde Telegram-replyfoto opnieuw ophalen mislukte: ${errMsg(e).slice(0, 180)}`);
    }
  }

  if (cached?.bufferBase64) {
    try {
      const buf = Buffer.from(cached.bufferBase64, 'base64');
      const ref = rememberNameablePhoto({
        buffer: buf,
        contentType: cached.contentType || 'image/jpeg',
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
        contentType: cached.contentType || 'image/jpeg',
        caption,
        ref,
        relation,
      });
    } catch (e) {
      logTimeline('system', `Gecachte Telegram-foto terughalen mislukte: ${errMsg(e).slice(0, 180)}`);
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
    || /^nee\s*[,;:]?\s+[\p{L}][\p{L}' -]{0,78}[.!?]*$/iu.test(value);
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

  bot = new Bot(config.telegramToken);
  const recovered = recoverInterruptedTelegramMessages();
  if (recovered) {
    logTimeline('system', `${recovered} onderbroken Telegram-bericht(en) zijn na de herstart weer hervatbaar.`, {
      internalOnly: true,
      telegramArchive: true,
      recovered,
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
      await next();
      return;
    }
    const claim = claimTelegramMessage(ctx.chat.id, ctx.message.message_id);
    if (!claim.claimed) {
      if (claim.reason === 'completed' || claim.reason === 'processing') {
        logTimeline('system', `Dubbele Telegram-update ${ctx.chat.id}:${ctx.message.message_id} niet opnieuw uitgevoerd.`, {
          internalOnly: true,
          telegramArchive: true,
          duplicateUpdate: true,
          reason: claim.reason,
        });
        return;
      }
      await next();
      return;
    }
    try {
      await next();
      completeTelegramMessage(ctx.chat.id, ctx.message.message_id);
    } catch (error) {
      failTelegramMessage(ctx.chat.id, ctx.message.message_id, errMsg(error));
      throw error;
    }
  });

  bot.command('start', async (ctx) => {
    if (!allowed(ctx.from.id)) return;
    upsertPerson(resolveName(ctx.from), { telegram_id: ctx.from.id });
    await sendTelegramText(
      ctx.chat.id,
      'Hoi! Ik ben Eva, ik woon hier in huis 💚 Zeg maar gewoon wat je wilt — en vertel me vooral hoe je heet, dan onthoud ik dat.',
      { replyToMessageId: ctx.message.message_id },
    );
  });

  bot.on('message:text', async (ctx) => {
    const tTelegramStart = performance.now();
    const latency = {};
    const stamp = (name, since) => { latency[name] = Math.round(performance.now() - since); };
    if (!allowed(ctx.from.id)) {
      await ctx.reply('Sorry, ik ken jou nog niet — vraag het gezin om je toe te voegen. 🙈');
      return;
    }
    const tPrep = performance.now();
    const naam = resolveName(ctx.from);
    upsertPerson(naam, { telegram_id: ctx.from.id });
    const stop = typing(ctx);
    const releaseDirect = holdDirectInteraction();
    stamp('prep_ms', tPrep);
    try {
      if (await resumePreparedTelegramTurn(ctx, naam)) {
        latency.resumed_prepared_reply = true;
        return;
      }
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
      const handled = await handleMessageDetailed({
        channel: 'telegram', sender: naam, text: tekst,
        replyTo: replied?.target || null,
        replyEvidence: replied?.evidence || null,
        messageContext: {
          identityTrusted: true,
          chatId: String(ctx.chat.id),
          messageId: String(ctx.message.message_id),
          replyToMessageId: replied?.target?.messageId || null,
        },
      });
      stamp('agent_total_ms', tAgent);
      const tSend = performance.now();
      await deliverTelegramTurn({ ctx, sender: naam, handled });
      stamp('telegram_send_ms', tSend);
    } catch (e) {
      logTimeline('system', 'Een directe Telegram-beurt liep onverwacht vast.', {
        internalOnly: true,
        directInteraction: true,
        error: errMsg(e).slice(0, 240),
      });
      if (preparedTelegramResult(ctx)) throw e;
      const tSend = performance.now();
      try {
        await sendTelegramText(
          ctx.chat.id,
          'Mijn antwoord liep net vast. Je bericht is wel opgeslagen; probeer het nog één keer.',
          { replyToMessageId: ctx.message.message_id },
        );
      } catch {
        throw e;
      }
      stamp('telegram_send_ms', tSend);
      latency.error = errMsg(e).slice(0, 180);
    } finally {
      stop();
      releaseDirect();
      latency.total_ms = Math.round(performance.now() - tTelegramStart);
      logTimeline('latency', `Telegram text: ${latency.total_ms}ms`, { channel: 'telegram', sender: resolveName(ctx.from), latency });
    }
  });

  bot.on('message:photo', async (ctx) => {
    if (!allowed(ctx.from.id)) return;
    const naam = resolveName(ctx.from);
    upsertPerson(naam, { telegram_id: ctx.from.id });
    const stop = typing(ctx);
    const releaseDirect = holdDirectInteraction();
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
      const media = localPhotoReply({ target, buffer: buf, contentType: 'image/jpeg', caption, ref });
      onthoudOntvangenFoto(buf); // houdt ook de bestaande korte gezicht-leerroute bruikbaar
      const tekst = [
        `[${naam} stuurde een foto.${ref ? ` Foto-ref: ${ref}.` : ''} Het exacte beeld is aan deze beurt toegevoegd.]`,
        caption ? `${naam}: ${caption}` : '',
      ].filter(Boolean).join('\n');
      const handled = await handleMessageDetailed({
        channel: 'telegram',
        sender: naam,
        text: tekst,
        replyEvidence: media?.evidence || null,
        messageContext: {
          identityTrusted: true,
          chatId: target.chatId,
          messageId: target.messageId,
          replyToMessageId: null,
        },
      });
      await deliverTelegramTurn({ ctx, sender: naam, handled });
    } catch (e) {
      logTimeline('system', 'Analyse van een ontvangen Telegram-foto mislukte.', {
        internalOnly: true,
        mediaAnalysis: true,
        error: errMsg(e).slice(0, 240),
      });
      if (preparedTelegramResult(ctx)) throw e;
      try {
        await sendTelegramText(
          ctx.chat.id,
          'Die foto is wel opgeslagen, maar het bekijken liep vast. Vraag me gerust nog eens naar dezelfde foto; opnieuw sturen hoeft niet.',
          { replyToMessageId: ctx.message.message_id },
        );
      } catch {
        throw e;
      }
    } finally {
      stop();
      releaseDirect();
    }
  });

  bot.on(['message:video', 'message:document'], async (ctx) => {
    if (!allowed(ctx.from.id)) return;
    const video = ctx.message.video || (ctx.message.document?.mime_type?.startsWith('video/') ? ctx.message.document : null);
    if (!video) return;

    const naam = resolveName(ctx.from);
    upsertPerson(naam, { telegram_id: ctx.from.id });
    const stop = typing(ctx);
    const releaseDirect = holdDirectInteraction();
    try {
      if (await resumePreparedTelegramTurn(ctx, naam)) return;
      const buf = await downloadTelegramFile(ctx, video.file_id);
      const beschrijving = await describeVideoBuffer(buf, video.mime_type || 'video/mp4');
      const bijschrift = ctx.message.caption ? ` met de tekst: "${ctx.message.caption}"` : '';
      const analyzedText = `[stuurde een filmpje${bijschrift} — ik heb er losse beelden uit bekeken; daarop is te zien: ${beschrijving}]`;
      const handled = await handleMessageDetailed({
        channel: 'telegram',
        sender: naam,
        text: analyzedText,
        messageContext: {
          identityTrusted: true,
          chatId: String(ctx.chat.id),
          messageId: String(ctx.message.message_id),
          replyToMessageId: ctx.message.reply_to_message?.message_id
            ? String(ctx.message.reply_to_message.message_id)
            : null,
          derivedText: analyzedText,
        },
      });
      await deliverTelegramTurn({ ctx, sender: naam, handled });
    } catch (e) {
      logTimeline('system', 'Analyse van een ontvangen Telegram-video mislukte.', {
        internalOnly: true,
        mediaAnalysis: true,
        error: errMsg(e).slice(0, 240),
      });
      if (preparedTelegramResult(ctx)) throw e;
      try {
        await sendTelegramText(
          ctx.chat.id,
          'Het filmpje is wel geregistreerd, maar mijn beeldanalyse liep vast. Je hoeft het niet opnieuw te sturen; ik kan het met het bewaarde Telegram-bestands-id opnieuw proberen.',
          { replyToMessageId: ctx.message.message_id },
        );
      } catch {
        throw e;
      }
    } finally {
      stop();
      releaseDirect();
    }
  });

  bot.catch((err) => logTimeline('system', `Telegram-fout: ${errMsg(err.error || err).slice(0, 200)}`));

  startPreparedTelegramOutbox();
  // long polling start (loopt voor altijd; niet awaiten)
  bot.start(telegramPollingOptions()).catch((e) => {
    logTimeline('system', `Telegram kon niet starten: ${errMsg(e).slice(0, 200)} — klopt het token?`);
  });
  logTimeline('system', 'Telegram verbonden — Eva is bereikbaar op je telefoon 📱');

  registerMessenger(sendToFamily);
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
  const z = normalizedRecipient(aan);
  const dedupeKey = String(opties?.dedupeKey || '').trim() || null;
  if (dedupeKey && recentVerstuurd.some((r) => (r.aan || '') === z && r.dedupeKey === dedupeKey)) return null;
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

const FAMILY_TARGET_ALIASES = new Set([
  'gezin',
  'het gezin',
  'familie',
  'iedereen',
  'allemaal',
]);

function normalizedRecipient(value) {
  return String(value ?? '').normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase();
}

function telegramId(value) {
  if (value === null || value === undefined) return '';
  return String(value).trim();
}

function uniqueTelegramTargets(people) {
  const byTelegramId = new Map();
  for (const person of people) {
    const key = telegramId(person?.telegram_id);
    if (!key || byTelegramId.has(key)) continue;
    byTelegramId.set(key, person);
  }
  return [...byTelegramId.values()];
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

function resolveTelegramTargets(people, aan, { broadcast = false } = {}) {
  const reachable = (Array.isArray(people) ? people : [])
    .filter((person) => telegramId(person?.telegram_id));
  const recipient = normalizedRecipient(aan);
  const explicitFamily = FAMILY_TARGET_ALIASES.has(recipient);

  if (!recipient && broadcast !== true) {
    return targetFailure(
      'TELEGRAM_RECIPIENT_REQUIRED',
      'Telegram-ontvanger ontbreekt; kies een persoon of vraag expliciet om het gezin.',
    );
  }

  if (!recipient || explicitFamily) {
    const targets = uniqueTelegramTargets(reachable);
    if (!targets.length) {
      return targetFailure(
        'TELEGRAM_NO_RECIPIENTS',
        'Nog niemand heeft met Eva ge-Telegramd; er is geen chat-id bekend.',
        explicitFamily ? recipient : null,
      );
    }
    return {
      ok: true,
      code: 'TELEGRAM_BROADCAST_RESOLVED',
      info: `expliciete gezinsontvanger met ${targets.length} uniek(e) chat-id(s)`,
      recipient: explicitFamily ? recipient : 'gezin',
      broadcast: true,
      targets,
    };
  }

  // Een niet-lege ontvanger blijft altijd een gerichte opdracht. Zelfs wanneer
  // de caller ook broadcast=true meegeeft, mag een typefout nooit uitwaaieren.
  const idMatches = reachable.filter((person) => telegramId(person.telegram_id) === recipient);
  const exactNameMatches = reachable.filter((person) => normalizedRecipient(person?.name) === recipient);
  const partialNameMatches = reachable.filter((person) => normalizedRecipient(person?.name).includes(recipient));
  const matches = idMatches.length
    ? idMatches
    : exactNameMatches.length
      ? exactNameMatches
      : partialNameMatches;
  const targets = uniqueTelegramTargets(matches);

  if (!targets.length) {
    return targetFailure(
      'TELEGRAM_RECIPIENT_UNKNOWN',
      `Onbekende Telegram-ontvanger "${String(aan).trim()}"; er is niets verstuurd.`,
      String(aan).trim(),
    );
  }
  if (targets.length > 1) {
    return targetFailure(
      'TELEGRAM_RECIPIENT_AMBIGUOUS',
      `Telegram-ontvanger "${String(aan).trim()}" past bij meerdere personen; gebruik de volledige naam.`,
      String(aan).trim(),
    );
  }
  return {
    ok: true,
    code: 'TELEGRAM_RECIPIENT_RESOLVED',
    info: `gerichte ontvanger ${targets[0].name}`,
    recipient: targets[0].name,
    broadcast: false,
    targets,
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
  return resolveTelegramTargets(allPeople(), aan, {
    broadcast: opties?.broadcast === true,
  });
}

export async function sendToFamily(tekst, aan = null, opties = null) {
  const resolved = targetsFor(aan, opties);
  if (!resolved.ok) return resolved;
  if (!bot) return { ok: false, info: 'Telegram is niet actief' };
  const reservation = reserveOutgoingMessage(tekst, aan, opties);
  if (!reservation) return { ok: false, info: 'bijna-hetzelfde net al gestuurd — niet herhaald' };
  const targets = resolved.targets;
  const isCurrent = typeof opties?.isCurrent === 'function' ? opties.isCurrent : () => true;
  const sent = [];
  const retract = async () => {
    let verwijderd = 0;
    for (const item of sent) {
      try {
        await bot.api.deleteMessage(item.chatId, item.messageId);
        try { markTelegramMessageDeleted(item.chatId, item.messageId); } catch { /* transport blijft leidend */ }
        verwijderd++;
      } catch { /* de aanroeper krijgt hieronder zichtbaar dat intrekken mislukte */ }
    }
    const ok = verwijderd === sent.length;
    if (ok) releaseOutgoingMessage(reservation);
    return { ok, removed: verwijderd, total: sent.length, visible: !ok && sent.length > verwijderd };
  };
  if (!isCurrent()) {
    releaseOutgoingMessage(reservation);
    return { ok: false, stale: true, info: 'uitgaande boodschap was al ingehaald door een correctie' };
  }
  let verzonden = 0;
  for (const p of targets) {
    try {
      const messages = await sendTelegramText(p.telegram_id, tekst, opties);
      sent.push(...messages.map((message) => ({ chatId: p.telegram_id, messageId: message.message_id })));
      verzonden++;
      if (!isCurrent()) {
        const undone = await retract();
        return {
          ok: false,
          stale: true,
          info: undone.ok ? 'verouderd projectantwoord ingetrokken' : 'verouderd projectantwoord kon niet volledig worden ingetrokken',
          retract,
          retraction: undone,
          visible: undone.visible,
          sent,
        };
      }
    } catch { /* deze persoon even niet bereikbaar */ }
  }
  if (verzonden) {
    try {
      logTimeline('transport', `Telegram accepteerde een verzendpoging voor ${aan || 'het gezin'}.`, {
        internalOnly: true,
        transportEvidence: true,
        channel: 'telegram',
        recipient: aan || null,
        transportAccepted: true,
        recipientConfirmed: false,
        contentRecordedSeparately: true,
        dedupeKey: opties?.dedupeKey || null,
        replyToMessageId: opties?.replyToMessageId || null,
        messageThreadId: opties?.messageThreadId || null,
        messageIds: sent.map((item) => String(item.messageId)),
        messageKeys: sent.map((item) => `${item.chatId}:${item.messageId}`),
      });
    } catch { /* een lokale logfout maakt een echt verzonden bericht niet mislukt */ }
    return { ok: true, info: `naar ${verzonden} persoon/personen`, retract, sent };
  }
  releaseOutgoingMessage(reservation);
  return { ok: false, info: 'versturen mislukte', retract };
}

export async function sendPhotoToFamily(buffer, contentType, caption, aan = null, opties = null) {
  const resolved = targetsFor(aan, opties);
  if (!resolved.ok) return resolved;
  if (!bot) return { ok: false, info: 'Telegram is niet actief' };
  const targets = resolved.targets;
  const isCurrent = typeof opties?.isCurrent === 'function' ? opties.isCurrent : () => true;
  const sent = [];
  const pendingCache = [];
  const retract = async () => {
    let removed = 0;
    for (const item of sent) {
      try {
        await bot.api.deleteMessage(item.chatId, item.messageId);
        try { markTelegramMessageDeleted(item.chatId, item.messageId); } catch { /* transport blijft leidend */ }
        removed++;
      } catch { /* zichtbaar wordt gerapporteerd */ }
    }
    return { ok: removed === sent.length, removed, total: sent.length, visible: removed < sent.length };
  };
  if (!isCurrent()) return { ok: false, stale: true, info: 'foto ingehaald door een direct gesprek' };
  let verzonden = 0;
  for (const p of targets) {
    try {
      if (!isCurrent()) break;
      const message = await bot.api.sendPhoto(p.telegram_id, new InputFile(buffer, 'foto.jpg'), { caption });
      const sizes = message.photo || [];
      sent.push({ chatId: p.telegram_id, messageId: message.message_id });
      pendingCache.push({ chatId: p.telegram_id, messageId: message.message_id, fileId: sizes.length ? sizes[sizes.length - 1].file_id : null });
      try {
        archiveTelegramPayload({
          chatId: p.telegram_id,
          message,
          sender: 'Eva',
          role: 'assistant',
          meta: {
            outbound: true,
          },
        });
      } catch (e) {
        logTimeline('system', `Verzonden Telegram-foto kon niet lokaal worden gearchiveerd: ${errMsg(e).slice(0, 180)}`, {
          internalOnly: true,
          telegramArchive: true,
          chatId: String(p.telegram_id),
          messageId: String(message.message_id),
        });
      }
      verzonden++;
      if (!isCurrent()) {
        const retraction = await retract();
        return { ok: false, stale: true, visible: retraction.visible, retraction, sent, info: 'foto ingehaald door een direct gesprek' };
      }
    } catch { /* overslaan */ }
  }
  if (!isCurrent() && sent.length) {
    const retraction = await retract();
    return { ok: false, stale: true, visible: retraction.visible, retraction, sent, info: 'foto ingehaald door een direct gesprek' };
  }
  for (const item of pendingCache) rememberSentPhoto(item.chatId, item.messageId, {
    buffer,
    contentType: contentType || 'image/jpeg',
    caption,
    fileId: item.fileId,
  });
  if (verzonden) {
    let photoRef = null;
    try {
      photoRef = rememberNameablePhoto({
        buffer,
        contentType: contentType || 'image/jpeg',
        caption,
        description: opties?.description || '',
        camera: cameraFromCaption(caption),
        cameraKey: opties?.cameraKey || null,
        suggestedPerson: opties?.suggestedPerson || null,
        candidateSimilarity: opties?.candidateSimilarity ?? null,
        candidateMargin: opties?.candidateMargin ?? null,
        telegramMessages: sent,
      });
    } catch (e) {
      logTimeline('system', `Verzonden Telegram-foto kon niet duurzaam worden gekoppeld: ${errMsg(e).slice(0, 180)}`);
    }
    if (photoRef) {
      for (const item of pendingCache) {
        try {
          archiveTelegramMessage({
            chatId: item.chatId,
            messageId: item.messageId,
            mediaRef: photoRef,
          });
        } catch (e) {
          logTimeline('system', `Telegram-fotoreferentie kon niet worden verrijkt: ${errMsg(e).slice(0, 180)}`, {
            internalOnly: true,
            telegramArchive: true,
            chatId: String(item.chatId),
            messageId: String(item.messageId),
            photoRef,
          });
        }
      }
    }
    try {
      logTimeline('transport', `Telegram accepteerde een foto voor ${aan || 'het gezin'}.`, {
        internalOnly: true, transportEvidence: true,
        channel: 'telegram', recipient: aan || null, transportAccepted: true, recipientConfirmed: false,
        transportType: 'photo',
        contentRecordedSeparately: true,
        messageIds: sent.map((item) => String(item.messageId)),
        messageKeys: sent.map((item) => `${item.chatId}:${item.messageId}`),
      });
    } catch { /* de echte aflevering blijft leidend */ }
    return { ok: true, info: `naar ${verzonden}`, sent, retract, photoRef };
  }
  return { ok: false, info: 'versturen mislukte', sent, retract };
}

export async function sendVoiceToFamily(buffer, caption, aan = null, opties = null) {
  const resolved = targetsFor(aan, opties);
  if (!resolved.ok) return resolved;
  if (!bot) return { ok: false, info: 'Telegram is niet actief' };
  const targets = resolved.targets;
  const isCurrent = typeof opties?.isCurrent === 'function' ? opties.isCurrent : () => true;
  const sent = [];
  const retract = async () => {
    let removed = 0;
    for (const item of sent) {
      try {
        await bot.api.deleteMessage(item.chatId, item.messageId);
        try { markTelegramMessageDeleted(item.chatId, item.messageId); } catch { /* transport blijft leidend */ }
        removed++;
      } catch { /* zichtbaar wordt gerapporteerd */ }
    }
    return { ok: removed === sent.length, removed, total: sent.length, visible: removed < sent.length };
  };
  if (!isCurrent()) return { ok: false, stale: true, info: 'spraakbericht ingehaald door een direct gesprek' };
  let verzonden = 0;
  for (const p of targets) {
    try {
      // mp3 → sendAudio (afspeelbaar spraakbericht; sendVoice vereist opus/ogg)
      if (!isCurrent()) break;
      const message = await bot.api.sendAudio(p.telegram_id, new InputFile(buffer, 'eva.mp3'), {
        caption: caption || undefined,
        title: 'Eva',
        performer: 'Eva',
      });
      sent.push({ chatId: p.telegram_id, messageId: message.message_id });
      try {
        archiveTelegramPayload({
          chatId: p.telegram_id,
          message,
          sender: 'Eva',
          role: 'assistant',
          meta: {
            outbound: true,
          },
        });
      } catch (e) {
        logTimeline('system', `Verzonden Telegram-audio kon niet lokaal worden gearchiveerd: ${errMsg(e).slice(0, 180)}`, {
          internalOnly: true,
          telegramArchive: true,
          chatId: String(p.telegram_id),
          messageId: String(message.message_id),
        });
      }
      verzonden++;
      if (!isCurrent()) {
        const retraction = await retract();
        return { ok: false, stale: true, visible: retraction.visible, retraction, sent, info: 'spraakbericht ingehaald door een direct gesprek' };
      }
    } catch { /* overslaan */ }
  }
  if (!isCurrent() && sent.length) {
    const retraction = await retract();
    return { ok: false, stale: true, visible: retraction.visible, retraction, sent, info: 'spraakbericht ingehaald door een direct gesprek' };
  }
  return verzonden ? { ok: true, info: `naar ${verzonden}`, sent, retract } : { ok: false, info: 'versturen mislukte', sent, retract };
}

export const __telegramTransportTest = Object.freeze({
  resolveTelegramTargets,
  directTurnIdentity,
  publishDirectTelegramReply,
  sendTelegramText,
  persistPreparedTelegramReply,
  preparedTelegramResult,
  setBotForTest(value) { bot = value; },
  telegramTextSendOptions,
});
