/** Transport-only Telegram composition: no legacy agent or autonomous loop. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { askEva2Canary, createEva2DraftSink, labelEva2TelegramReply, labelEva2TelegramFailure } from './src/integrations/eva2canary.js';
import { analyseEva2Photo } from './src/integrations/eva2media.js';
import { pollTelegramConcurrently } from './src/integrations/telegrampolling.js';

export class TransportJournal {
  constructor(filename) {
    this.db = new DatabaseSync(filename);
    this.db.exec(`PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS telegram_transport (
      update_id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, message_id INTEGER NOT NULL,
      original_update TEXT NOT NULL, state TEXT NOT NULL, media_context TEXT,
      response TEXT, delivery_receipt TEXT, changed_at TEXT NOT NULL);`);
    this.db.exec(`CREATE TABLE IF NOT EXISTS telegram_access_requests (
      telegram_user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
      username TEXT, seen_at TEXT NOT NULL);`);
  }
  noteAccessRequest(message) {
    const user = message?.from;
    if (message?.chat?.type !== 'private' || !Number.isSafeInteger(user?.id)
        || user.id < 1 || message.chat.id !== user.id || user.is_bot) return;
    // Account metadata only: no unadmitted message, photo, or conversation.
    this.db.prepare('DELETE FROM telegram_access_requests WHERE seen_at < ?')
      .run(new Date(Date.now() - 86400000).toISOString());
    this.db.prepare(`INSERT INTO telegram_access_requests VALUES (?,?,?,?)
      ON CONFLICT(telegram_user_id) DO UPDATE SET first_name=excluded.first_name,
      username=excluded.username,seen_at=excluded.seen_at`)
      .run(user.id, String(user.first_name ?? '').slice(0, 128),
        typeof user.username === 'string' ? user.username.slice(0, 64) : null, new Date().toISOString());
    this.db.exec(`DELETE FROM telegram_access_requests WHERE telegram_user_id NOT IN
      (SELECT telegram_user_id FROM telegram_access_requests ORDER BY seen_at DESC LIMIT 32)`);
  }
  claim(update) {
    const message = update.message;
    const found = this.db.prepare('SELECT * FROM telegram_transport WHERE update_id=?').get(update.update_id);
    if (found) return false; // Retain uncertain ingress/delivery; never blindly replay an external effect.
    this.db.prepare(`INSERT INTO telegram_transport
      (update_id,chat_id,message_id,original_update,state,changed_at) VALUES (?,?,?,?,?,?)`)
      .run(update.update_id, String(message.chat.id), message.message_id, JSON.stringify(update), 'processing', new Date().toISOString());
    return true;
  }
  record(updateId, state, { media = null, response = null, receipt = null } = {}) {
    this.db.prepare(`UPDATE telegram_transport SET state=?,media_context=COALESCE(?,media_context),
      response=COALESCE(?,response),delivery_receipt=COALESCE(?,delivery_receipt),changed_at=? WHERE update_id=?`)
      .run(state, media === null ? null : JSON.stringify(media), response, receipt, new Date().toISOString(), updateId);
  }
  close() { this.db.close(); }
}

export function permittedMessage(message, bindings) {
  return Boolean(message?.from && message?.chat && bindings.some(binding =>
    binding.telegram_user_id === message.from.id && binding.allowed_chat_ids.includes(String(message.chat.id))));
}

export function telegramReplyContext(message) {
  if (message.chat?.type !== 'private') return null;
  const source = message.reply_to_message;
  if (!source || String(source.chat?.id) !== String(message.chat?.id)
      || !Number.isSafeInteger(source.message_id) || source.message_id < 1
      || source.message_id >= message.message_id) return null;
  const text = source.text ?? source.caption;
  if (typeof text !== 'string' || !text.trim() || Buffer.byteLength(text, 'utf8') > 16384) return null;
  if (VAULT_CMD_RE.test(text.trim())) return null;
  if (!Number.isSafeInteger(source.date) || source.date < 0) return null;
  const occurredAt = new Date(source.date * 1000).toISOString().replace(/\.000Z$/u, 'Z');
  const result = {source_ref: `telegram:${message.chat.id}:${source.message_id}`,
    text, occurred_at: occurredAt};
  const quote = message.quote;
  if (typeof quote?.text === 'string' && quote.text.trim()
      && Number.isSafeInteger(quote.position) && quote.position >= 0
      && text.slice(quote.position, quote.position + quote.text.length) === quote.text)
    result.selection = quote.text;
  return result;
}

const INVITE_START_RE = /^\/start(?:@\w+)?\s+(inv_[A-Za-z0-9_-]{42,46})$/u;
const VAULT_CMD_RE = /^\/(?:kluis|wachtwoord)(?:@\w+)?(?:\s+(.*))?$/isu;
const VAULT_ARGS_RE = /^([A-Za-z0-9_.:-]{2,64})\s+(.+)$/su;

export function computeTelegramVaultProof(botToken) {
  return crypto.createHmac('sha256', String(botToken || '').trim()).update('EvaTelegramVaultAuth/v1').digest('hex');
}

export function createTransportHandler({ journal, bindings, baseUrl, photoProvider = null, downloadFile, makeDraft, coreRequest = askEva2Canary, redeemInvite = null, refreshBindings = null, storeVaultSecret = null, claimOutboundPhoto = null, sendPhoto = null }) {
  const dynamicAllowedIds = new Set();
  const pausedIds = new Set();
  let lastSyncAt = 0;

  async function maybeSyncBindings(force = false) {
    if (typeof refreshBindings !== 'function') return;
    const now = Date.now();
    if (!force && now - lastSyncAt < 5000) return;
    lastSyncAt = now;
    try {
      const state = await refreshBindings();
      if (Array.isArray(state?.active_telegram_user_ids)) {
        dynamicAllowedIds.clear();
        for (const uid of state.active_telegram_user_ids) {
          if (Number.isSafeInteger(uid) && uid > 0) dynamicAllowedIds.add(uid);
        }
      }
      if (Array.isArray(state?.paused_telegram_user_ids)) {
        pausedIds.clear();
        for (const uid of state.paused_telegram_user_ids) {
          if (Number.isSafeInteger(uid) && uid > 0) pausedIds.add(uid);
        }
      }
    } catch {
      // Keep last known bindings if core is momentarily unreachable.
    }
  }

  function isAdmittedMessage(message) {
    const uid = message?.from?.id;
    if (!Number.isSafeInteger(uid) || uid < 1 || !message?.chat) return false;
    if (pausedIds.has(uid)) return false;
    if (permittedMessage(message, bindings)) return true;
    return String(message.chat.id) === String(uid) && dynamicAllowedIds.has(uid);
  }

  return async ctx => {
    const message = ctx.message;
    await maybeSyncBindings(false);
    if (!isAdmittedMessage(message)) {
      const rawText = typeof message?.text === 'string' ? message.text.trim() : '';
      const inviteMatch = message?.chat && message?.from && String(message.chat.id) === String(message.from.id)
        ? INVITE_START_RE.exec(rawText)
        : null;
      if (inviteMatch && typeof redeemInvite === 'function') {
        const alreadyHandled = Boolean(
          journal.db.prepare('SELECT 1 FROM telegram_transport WHERE update_id=?').get(ctx.update.update_id)
        );
        if (alreadyHandled) return;
        let redeemed = null;
        try {
          redeemed = await redeemInvite({
            token: inviteMatch[1],
            telegramUserId: message.from.id,
            firstName: String(message.from.first_name ?? '').slice(0, 128),
            username: typeof message.from.username === 'string' ? message.from.username.slice(0, 64) : null,
          });
        } catch {
          redeemed = null;
        }
        if (redeemed?.admitted === true && typeof redeemed.welcome_text === 'string' && redeemed.welcome_text.trim()) {
          dynamicAllowedIds.add(message.from.id);
          pausedIds.delete(message.from.id);
          if (!journal.claim(ctx.update)) return;
          const welcomeId = ctx.update.update_id;
          const welcomeText = labelEva2TelegramReply(redeemed.welcome_text);
          try {
            journal.record(welcomeId, 'delivery_pending', { response: welcomeText });
            const res = await ctx.reply(welcomeText, {
              reply_parameters: { message_id: message.message_id, allow_sending_without_reply: false },
            });
            if (res?.chat?.id !== message.chat.id || typeof res?.message_id !== 'number') {
              throw new Error('Telegram welcome receipt mismatch');
            }
            journal.record(welcomeId, 'delivered', { receipt: `telegram:${res.chat.id}:${res.message_id}` });
          } catch {
            journal.record(welcomeId, 'delivery_uncertain');
          }
          return;
        }
      }
      journal.noteAccessRequest(message);
      return;
    }
    const rawCandidateText = String(message?.text ?? message?.caption ?? '').trim();
    const vaultCmdMatch = VAULT_CMD_RE.exec(rawCandidateText);
    if (vaultCmdMatch) {
      const argTail = typeof vaultCmdMatch[1] === 'string' ? vaultCmdMatch[1].trim() : '';
      const parsedArgs = argTail ? VAULT_ARGS_RE.exec(argTail) : null;
      const safeDeviceRef = parsedArgs ? parsedArgs[1] : '';
      const redactedUpdate = {
        update_id: ctx.update.update_id,
        message: {
          message_id: message.message_id,
          date: message.date,
          chat: { id: message.chat.id, type: message.chat.type },
          from: { id: message.from.id },
          text: safeDeviceRef ? `/kluis ${safeDeviceRef} [REDACTED]` : '/kluis [REDACTED]',
        },
      };
      if (!journal.claim(redactedUpdate)) return;
      const vid = ctx.update.update_id;
      let messageDeleted = false;
      if (argTail && typeof ctx.api?.deleteMessage === 'function') {
        messageDeleted = await ctx.api.deleteMessage(message.chat.id, message.message_id).then(() => true, () => false);
      }
      const replyOpts = {
        reply_parameters: { message_id: message.message_id, allow_sending_without_reply: true },
      };
      if (message.chat?.type !== 'private') {
        const groupWarn = labelEva2TelegramFailure(
          'Gebruik /kluis uitsluitend in een privéchat met mij. Verwijder het bericht uit deze groep en wijzig zo nodig het wachtwoord.'
        );
        try {
          journal.record(vid, 'failed', { response: groupWarn });
          await ctx.reply(groupWarn, replyOpts);
        } catch {
          journal.record(vid, 'delivery_uncertain');
        }
        return;
      }
      if (!parsedArgs || typeof storeVaultSecret !== 'function') {
        const usageMsg = labelEva2TelegramReply(
          'Gebruik: /kluis <apparaat-id> <wachtwoord-of-sleutel>\n'
          + 'Je bericht met het geheim wordt direct uit de chat gewist en versleuteld (AES-256-GCM) opgeslagen buiten het taalmodel om.'
        );
        try {
          journal.record(vid, 'delivery_pending', { response: usageMsg });
          const sent = await ctx.reply(usageMsg, replyOpts);
          if (sent?.chat?.id === message.chat.id && typeof sent?.message_id === 'number') {
            journal.record(vid, 'delivered', { receipt: `telegram:${sent.chat.id}:${sent.message_id}` });
          } else {
            journal.record(vid, 'delivery_uncertain');
          }
        } catch {
          journal.record(vid, 'delivery_uncertain');
        }
        return;
      }
      try {
        const stored = await storeVaultSecret({
          telegramUserId: message.from.id,
          chatId: String(message.chat.id),
          deviceId: parsedArgs[1],
          secretValue: parsedArgs[2],
          messageDeleted,
        });
        let baseReply = stored?.reply_text || 'Kon geheim niet opslaan in de kluis.';
        if (!messageDeleted && !baseReply.includes('handmatig')) {
          baseReply += '\n⚠️ Let op: verwijder je oorspronkelijke bericht nog even handmatig uit Telegram.';
        }
        const replyMsg = labelEva2TelegramReply(baseReply);
        journal.record(vid, 'delivery_pending', { response: replyMsg });
        const sent = await ctx.reply(replyMsg, replyOpts);
        if (sent?.chat?.id === message.chat.id && typeof sent?.message_id === 'number') {
          journal.record(vid, 'delivered', { receipt: `telegram:${sent.chat.id}:${sent.message_id}` });
        } else {
          journal.record(vid, 'delivery_uncertain');
        }
      } catch (err) {
        journal.record(vid, 'failed');
        const safeErr = typeof err?.safeDetail === 'string' && err.safeDetail.trim()
          ? err.safeDetail.trim().slice(0, 200)
          : 'Kluisopslag geweigerd of mislukt (alleen het gezinshoofd kan geheimen opslaan).';
        try {
          await ctx.reply(
            labelEva2TelegramFailure(safeErr),
            replyOpts
          );
        } catch {
          journal.record(vid, 'delivery_uncertain');
        }
      }
      return;
    }
    if (!journal.claim(ctx.update)) return;
    const id = ctx.update.update_id;
    let typing;
    async function deliver(text) {
      journal.record(id, 'delivery_pending', { response: text });
      const result = await ctx.reply(text, { reply_parameters: { message_id: message.message_id, allow_sending_without_reply: false } });
      if (result?.chat?.id !== message.chat.id || result?.reply_to_message?.message_id !== message.message_id || result?.text !== text)
        throw new Error('Telegram receipt does not match the original message');
      journal.record(id, 'delivered', { receipt: `telegram:${result.chat.id}:${result.message_id}` });
    }
    try {
      let text = message.text ?? message.caption ?? '';
      if (/^\/eva1(?:@\w+)?(?:\s|$)/iu.test(text)) {
        await deliver('Eva1 is gestopt. Stuur je vraag gewoon aan Eva.');
        return;
      }
      const file = message.photo?.at(-1) ?? (['image/jpeg', 'image/png', 'image/webp'].includes(message.document?.mime_type) ? message.document : null);
      if (typeof message.text !== 'string' && !file) {
        await deliver('Ik kan dit bestandstype via Telegram nog niet verwerken. Stuur de relevante tekst of een foto.');
        return;
      }
      const sayTyping = () => ctx.replyWithChatAction('typing').catch(() => {});
      sayTyping(); typing = setInterval(sayTyping, 4000);
      let media = null;
      if (file) {
        if (!photoProvider) throw new Error('Photo analysis provider is not configured');
        media = await analyseEva2Photo({ buffer: await downloadFile(ctx, file), contentType: file.mime_type || 'image/jpeg',
          sourceRef: `telegram:${message.chat.id}:${message.message_id}`, caption: text,
          complete: photoProvider.chatComplete, imageMessage: photoProvider.imageMsg });
        journal.record(id, 'processing', { media });
        if (!text.trim()) text = 'Bekijk de aangeleverde foto.';
      }
      const draft = makeDraft(ctx);
      const answer = await coreRequest({ baseUrl, chatId: message.chat.id, messageId: message.message_id,
        fromId: message.from.id, text, telegramDate: message.date, mediaContext: media,
        replyContext: telegramReplyContext(message),
        onDraft: draft?.map(labelEva2TelegramReply), onReceipt: draft ? value => draft.receipt(value) : null });
      if (!answer) throw new Error('Message was not admitted to Eva2');
      await deliver(labelEva2TelegramReply(answer.text));
      if (typeof claimOutboundPhoto === 'function') {
        const outbound = await claimOutboundPhoto({
          chatId: String(message.chat.id),
          messageId: message.message_id,
        }).catch(() => null);
        if (outbound?.has_photo && typeof outbound.image_base64 === 'string' && outbound.image_base64.length > 0) {
          const bytes = Buffer.from(outbound.image_base64, 'base64');
          const fname = String(outbound.filename || 'camera.jpg').replace(/[^A-Za-z0-9._-]/g, '_');
          const photoOpts = { reply_parameters: { message_id: message.message_id, allow_sending_without_reply: true } };
          if (typeof sendPhoto === 'function') {
            await sendPhoto(ctx, bytes, fname, photoOpts).catch(() => {});
          } else if (typeof ctx.replyWithPhoto === 'function') {
            await ctx.replyWithPhoto({ source: bytes, filename: fname }, photoOpts).catch(() => {});
          }
        }
      }
    } catch (error) {
      // A send in progress may already have reached Telegram. Preserve uncertainty instead of dispatching again.
      const row = journal.db.prepare('SELECT state FROM telegram_transport WHERE update_id=?').get(id);
      if (row?.state === 'delivery_pending') journal.record(id, 'delivery_uncertain');
      else if (String(error?.message || '').includes('HTTP 403')) {
        pausedIds.add(message.from.id);
        journal.record(id, 'failed');
      } else {
        journal.record(id, 'failed');
        try { await deliver(labelEva2TelegramFailure(String(error.message || error).slice(0, 160))); }
        catch { journal.record(id, 'delivery_uncertain'); }
      }
      console.error('Telegram transport failed:', error?.name || 'Error');
    } finally { if (typing) clearInterval(typing); }
  };
}

export async function main() {
  process.umask(0o077);
  const required = name => { const value = process.env[name]; if (!value) throw new Error(`${name} is required`); return value; };
  const require = createRequire(required('EVA_TELEGRAM_DEPENDENCY_MANIFEST'));
  const { Bot, InputFile } = require('grammy');
  const token = fs.readFileSync(required('EVA_TELEGRAM_BOT_TOKEN_FILE'), 'utf8').trim();
  const trust = JSON.parse(fs.readFileSync(required('EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE'), 'utf8'));
  if (!['EvaTelegramPrincipalBindings/v1','EvaTelegramPrincipalBindings/v2'].includes(trust.contract)
      || !Array.isArray(trust.bindings) || !trust.bindings.length) throw new Error('Telegram bindings are invalid');
  const journal = new TransportJournal(required('EVA_TELEGRAM_TRANSPORT_DB'));
  const photoProvider = process.env.EVA_TELEGRAM_PHOTO_PROVIDER_MODULE
    ? await import(pathToFileURL(process.env.EVA_TELEGRAM_PHOTO_PROVIDER_MODULE).href) : null;
  const baseUrl = required('EVA2_CANARY_URL');
  const bot = new Bot(token);
  try { await bot.init(); } catch { /* bot.init will also run inside polling if offline at boot */ }
  const botUsername = bot.botInfo?.username || '';
  const refreshBindings = async () => {
    const res = await fetch(`${baseUrl}/internal/v1/telegram/bindings/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ bot_username: botUsername }),
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    return res.json();
  };
  const redeemInvite = async ({ token: invToken, telegramUserId, firstName, username }) => {
    const res = await fetch(`${baseUrl}/internal/v1/telegram/invites/redeem`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        token: invToken,
        telegram_user_id: telegramUserId,
        first_name: firstName,
        username,
      }),
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) return null;
    return res.json();
  };
  const bridgeProof = computeTelegramVaultProof(token);
  const storeVaultSecret = async ({ telegramUserId, chatId, deviceId, secretValue, messageDeleted = true }) => {
    const res = await fetch(`${baseUrl}/internal/v1/telegram/vault/store`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-Eva-Telegram-Bridge-Proof': bridgeProof,
      },
      body: JSON.stringify({
        telegram_user_id: telegramUserId,
        chat_id: chatId,
        device_id: deviceId,
        secret_value: secretValue,
        message_deleted: Boolean(messageDeleted),
      }),
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const err = new Error(`HTTP ${res.status}`);
      if (typeof body?.detail === 'string' && body.detail.trim()) {
        err.safeDetail = body.detail.trim().slice(0, 200);
      }
      throw err;
    }
    return res.json();
  };
  await refreshBindings().catch(() => {});
  const claimOutboundPhoto = async ({ chatId, messageId }) => {
    const res = await fetch(`${baseUrl}/internal/v1/telegram/outbound-media/claim`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-Eva-Telegram-Bridge-Proof': bridgeProof,
      },
      body: JSON.stringify({ chat_id: String(chatId), message_id: Number(messageId) }),
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) return null;
    return res.json();
  };
  bot.on('message', createTransportHandler({ journal, bindings: trust.bindings, baseUrl, photoProvider,
    redeemInvite, refreshBindings, storeVaultSecret, claimOutboundPhoto,
    sendPhoto: (ctx, bytes, fname, opts) => ctx.replyWithPhoto(new InputFile(bytes, fname), opts),
    makeDraft: ctx => createEva2DraftSink({ chatId: ctx.chat.id, draftId: ctx.message.message_id, sendDraft: bot.api.raw.sendMessageDraft?.bind(bot.api.raw) }),
    downloadFile: async (ctx, file) => {
      const info = await ctx.api.getFile(file.file_id);
      if (!info.file_path || info.file_path.includes('..')) throw new Error('Telegram file path is invalid');
      const response = await fetch(`https://api.telegram.org/file/bot${token}/${info.file_path}`);
      if (!response.ok) throw new Error('Telegram file download failed');
      const bytes = Buffer.from(await response.arrayBuffer());
      if (bytes.length > 20 * 1024 * 1024) throw new Error('Telegram image is too large');
      return bytes;
    } }));
  const stop = new AbortController();
  process.once('SIGTERM', () => stop.abort()); process.once('SIGINT', () => stop.abort());
  console.log('Eva2 standalone Telegram transport ready; legacy agent/autonomy not started.');
  try { await pollTelegramConcurrently(bot, { signal: stop.signal, canOverlap: () => true,
    onError: error => console.error('Telegram polling failure:', error.error_code ?? error.name ?? 'Error') }); }
  finally { journal.close(); }
}
if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname))
  main().catch(error => { console.error('Telegram startup failed:', error.name); process.exitCode = 1; });
