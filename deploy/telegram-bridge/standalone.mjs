/** Transport-only Telegram composition: no legacy agent or autonomous loop. */
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

export function createTransportHandler({ journal, bindings, baseUrl, photoProvider = null, downloadFile, makeDraft, coreRequest = askEva2Canary }) {
  return async ctx => {
    const message = ctx.message;
    if (!permittedMessage(message, bindings)) return;
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
        onDraft: draft?.map(labelEva2TelegramReply), onReceipt: draft ? value => draft.receipt(value) : null });
      if (!answer) throw new Error('Message was not admitted to Eva2');
      await deliver(labelEva2TelegramReply(answer.text));
    } catch (error) {
      // A send in progress may already have reached Telegram. Preserve uncertainty instead of dispatching again.
      const row = journal.db.prepare('SELECT state FROM telegram_transport WHERE update_id=?').get(id);
      if (row?.state === 'delivery_pending') journal.record(id, 'delivery_uncertain');
      else {
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
  const { Bot } = require('grammy');
  const token = fs.readFileSync(required('EVA_TELEGRAM_BOT_TOKEN_FILE'), 'utf8').trim();
  const trust = JSON.parse(fs.readFileSync(required('EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE'), 'utf8'));
  if (!['EvaTelegramPrincipalBindings/v1','EvaTelegramPrincipalBindings/v2'].includes(trust.contract)
      || !Array.isArray(trust.bindings) || !trust.bindings.length) throw new Error('Telegram bindings are invalid');
  const journal = new TransportJournal(required('EVA_TELEGRAM_TRANSPORT_DB'));
  const photoProvider = process.env.EVA_TELEGRAM_PHOTO_PROVIDER_MODULE
    ? await import(pathToFileURL(process.env.EVA_TELEGRAM_PHOTO_PROVIDER_MODULE).href) : null;
  const bot = new Bot(token);
  bot.on('message', createTransportHandler({ journal, bindings: trust.bindings, baseUrl: required('EVA2_CANARY_URL'), photoProvider,
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
