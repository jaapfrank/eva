import { sourceId } from './store.mjs';
import { boundedAudio, decodeAudio, encodeVoice, MAX_AUDIO_BYTES } from './audio.mjs';
import { createHash } from 'node:crypto';

const phone = value => typeof value === 'string' && /^[1-9][0-9]{6,14}$/.test(value);
const pn = jid => typeof jid === 'string' && /^\d+(?::\d+)?@s\.whatsapp\.net$/.test(jid) ? jid.split('@')[0].split(':')[0] : null;
const privateJid = jid => typeof jid === 'string' && /^\d+(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(jid);
const validId = id => typeof id === 'string' && id.length > 0 && id.length <= 240 && !/[\x00-\x20\x7f]/.test(id);
const at = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
const safeText = value => typeof value === 'string' && value.trim() && Buffer.byteLength(value) <= 65536;

export class Bridge {
  constructor(store, core, sdk, { logger, qr, decode = decodeAudio, encode = encodeVoice } = {}) {
    Object.assign(this, { store, core, sdk, logger, qr, decode, encode });
    this.socket = null; this.connection = 'disabled'; this.qrData = null; this.lastError = null;
    this.generation = 0; this.reconnect = null; this.backoff = 1000; this.polling = false; this.active = new Set();
  }
  status() {
    const cfg = this.store.settings();
    return { contract: 'EvaWhatsApp/v1', enabled: cfg.enabled, connection: this.connection,
      contact_phone: cfg.contact_phone, qr_data_url: this.qrData, audio_enabled: cfg.audio_enabled, last_error: this.lastError };
  }
  async start() { if (this.store.settings().enabled) await this.connect(); }
  stopSocket() {
    ++this.generation; clearTimeout(this.reconnect); this.reconnect = null; this.qrData = null;
    const socket = this.socket; this.socket = null;
    if (socket) { try { socket.end(new Error('local_channel_stopped')); } catch {} }
  }
  async configure(payload) {
    if (!payload || Object.keys(payload).sort().join(',') !== 'audio_enabled,contact_phone'
      || !phone(payload.contact_phone) || typeof payload.audio_enabled !== 'boolean') throw new Error('invalid_configuration');
    const cfg = this.store.settings(); this.stopSocket();
    this.store.put('settings', { ...cfg, ...payload, enabled: false, accept_after: 0 });
    this.connection = 'disabled'; this.lastError = null;
    return this.status();
  }
  async enable() {
    const cfg = this.store.settings();
    if (cfg.enabled && this.socket) return this.status();
    this.store.put('settings', { ...cfg, enabled: true, accept_after: Math.floor(Date.now() / 1000) });
    await this.connect(); return this.status();
  }
  async disable() {
    const cfg = this.store.settings(); this.store.put('settings', { ...cfg, enabled: false });
    this.stopSocket(); this.connection = 'disabled'; return this.status();
  }
  async logout() {
    const socket = this.socket; const cfg = this.store.settings();
    this.store.put('settings', { ...cfg, enabled: false });
    ++this.generation; clearTimeout(this.reconnect); this.reconnect = null; this.qrData = null; this.socket = null;
    let confirmed = true, timer;
    try { if (socket) await Promise.race([socket.logout(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error()), 10000); })]); }
    catch { confirmed = false; }
    finally { clearTimeout(timer); try { socket?.end(new Error('local_logout')); } catch {} }
    this.store.clearAuth(); this.connection = 'logged_out'; this.lastError = confirmed ? null : 'logout_unconfirmed';
    return this.status();
  }
  async connect() {
    this.stopSocket(); if (!this.store.settings().enabled) return;
    const generation = this.generation; this.connection = 'connecting'; this.lastError = null;
    const creds = this.store.get('auth-creds') || this.sdk.initAuthCreds();
    this.store.put('auth-creds', creds);
    let sessionSocket;
    const sessionCurrent = () => {
      if (generation !== this.generation || !this.store.settings().enabled || (sessionSocket && sessionSocket !== this.socket)) throw new Error('session_authority_changed');
    };
    const auth = { creds, keys: {
      get: async (type, ids) => { sessionCurrent(); return this.store.authKeys(type, ids, value => this.sdk.proto.Message.AppStateSyncKeyData.fromObject(value)); },
      set: async data => { sessionCurrent(); this.store.setAuthKeys(data); },
    } };
    const socket = this.sdk.makeSocket({ auth, logger: this.logger, printQRInTerminal: false,
      syncFullHistory: false, shouldSyncHistoryMessage: () => false, markOnlineOnConnect: false,
      shouldIgnoreJid: jid => !privateJid(jid), getMessage: async () => undefined,
      generateHighQualityLinkPreview: false, emitOwnEvents: false });
    this.socket = socket; sessionSocket = socket;
    socket.ev.on('creds.update', () => {
      if (generation !== this.generation) return;
      try { this.store.put('auth-creds', creds); } catch { this.stopSocket(); this.connection = 'disconnected'; this.lastError = 'session_storage_unavailable'; }
    });
    socket.ev.on('connection.update', update => {
      void this.connectionUpdate(update, socket, generation).catch(() => { this.lastError = 'connection_update_failed'; });
    });
    socket.ev.on('messages.upsert', event => {
      if (event.type !== 'notify' || generation !== this.generation) return;
      for (const message of event.messages || []) {
        const task = this.incoming(message, generation).catch(() => { this.lastError = 'message_processing_unconfirmed'; });
        this.active.add(task); task.finally(() => this.active.delete(task));
      }
    });
  }
  async connectionUpdate(update, socket, generation) {
    if (generation !== this.generation || socket !== this.socket) return;
    if (update.qr) {
      const data = await this.qr(update.qr);
      if (generation !== this.generation) return;
      this.qrData = data; this.connection = 'awaiting_qr';
    }
    if (update.connection === 'open') {
      const cfg = this.store.settings(), ownPhone = pn(socket.user?.id);
      if (!cfg.contact_phone && phone(ownPhone)) this.store.put('settings', { ...cfg, contact_phone: ownPhone });
      this.connection = 'connected'; this.qrData = null; this.lastError = null; this.backoff = 1000;
    }
    if (update.connection === 'close') {
      this.socket = null; this.qrData = null;
      const code = update.lastDisconnect?.error?.output?.statusCode;
      if (code === this.sdk.DisconnectReason.loggedOut) {
        this.store.put('settings', { ...this.store.settings(), enabled: false }); this.store.clearAuth();
        this.connection = 'logged_out'; this.lastError = 'account_logged_out'; return;
      }
      this.connection = 'disconnected'; this.lastError = code === this.sdk.DisconnectReason.connectionReplaced ? 'session_replaced' : 'connection_lost';
      if (code !== this.sdk.DisconnectReason.connectionReplaced && this.store.settings().enabled) {
        this.reconnect = setTimeout(() => { if (generation === this.generation) void this.connect().catch(() => { this.lastError = 'reconnect_failed'; }); }, this.backoff);
        this.backoff = Math.min(30000, this.backoff * 2);
      }
    }
  }
  account() { return this.socket?.user?.id || ''; }
  current(record, generation) {
    const cfg = this.store.settings(); this.core.token();
    if (generation !== this.generation || !cfg.enabled || cfg.contact_phone !== record.contact
      || this.connection !== 'connected' || !this.socket || this.account() !== record.account) throw new Error('channel_authority_changed');
    return cfg;
  }
  resolve(message) {
    const cfg = this.store.settings(), key = message?.key;
    if (!cfg.enabled || !phone(cfg.contact_phone) || !key || !validId(key.id) || !privateJid(key.remoteJid)
      || key.participant || this.store.ownMessage(key.id)) return null;
    const contact = pn(key.remoteJid) || pn(key.remoteJidAlt);
    if (contact !== cfg.contact_phone) return null;
    if (key.fromMe && pn(this.account()) !== cfg.contact_phone) return null;
    const stamp = Number(message.messageTimestamp?.toNumber?.() ?? message.messageTimestamp);
    if (!Number.isSafeInteger(stamp) || stamp < cfg.accept_after || stamp > Math.floor(Date.now() / 1000) + 300) return null;
    return { id: sourceId(key), contact, account: this.account(), occurred_at: new Date(stamp * 1000).toISOString().replace('.000Z', 'Z') };
  }
  quote(message, content) {
    return { key: { remoteJid: message.key.remoteJid, id: message.key.id, fromMe: Boolean(message.key.fromMe),
      ...(message.key.remoteJidAlt ? { remoteJidAlt: message.key.remoteJidAlt } : {}) },
      message: content.audioMessage ? { audioMessage: { mimetype: content.audioMessage.mimetype, ptt: Boolean(content.audioMessage.ptt) } }
        : { conversation: content.conversation || content.extendedTextMessage?.text || '' } };
  }
  async incoming(message, generation = this.generation) {
    const origin = this.resolve(message); if (!origin) return 'ignored';
    const content = this.sdk.normalizeMessageContent(message.message) || {};
    if (!content.conversation && !content.extendedTextMessage?.text && !content.audioMessage) return 'unsupported';
    const quote = this.quote(message, content);
    if (!this.store.admit(origin.id, origin.contact, origin.account, quote, origin.occurred_at)) return 'duplicate';
    let text = content.conversation || content.extendedTextMessage?.text, reply, voice = false;
    const record = this.store.inbound(origin.id);
    try {
      this.current(record, generation);
      if (content.audioMessage) {
        if (!this.store.settings().audio_enabled) {
          reply = 'Spraakberichten staan voor deze WhatsApp-verbinding uit. Stuur je vraag als tekst.';
        } else {
          if (Number(content.audioMessage.fileLength || 0) > MAX_AUDIO_BYTES || Number(content.audioMessage.seconds || 0) > 120) throw new Error('audio_size');
          const stream = await this.sdk.downloadMediaMessage(message, 'stream', {}, { logger: this.logger, reuploadRequest: this.socket.updateMediaMessage });
          const audio = await boundedAudio(stream); this.current(record, generation);
          this.store.evidence(origin.id, { audio_sha256: createHash('sha256').update(audio).digest('hex') });
          const pcm = await this.decode(audio); this.current(record, generation);
          const result = await this.core.call('transcribe', pcm, { pcm: true, contact: record.contact });
          if (!safeText(result.text)) throw new Error('audio_transcript_unavailable');
          this.store.evidence(origin.id, { transcript_sha256: createHash('sha256').update(result.text).digest('hex') });
          text = result.text; voice = true;
        }
      }
      if (!reply) {
        if (!safeText(text)) throw new Error('text_size');
        this.current(record, generation);
        const result = await this.core.call('respond', { contact_phone: record.contact, message_id: origin.id, text, occurred_at: origin.occurred_at });
        if (!safeText(result.text) || result.conversation_id !== 'whatsapp:' + record.contact || result.message_id !== 'whatsapp:' + record.contact + ':' + origin.id) throw new Error('core_response_binding');
        reply = result.text;
      }
    } catch {
      this.lastError = content.audioMessage ? 'audio_processing_unavailable' : 'core_response_unconfirmed';
      reply = content.audioMessage ? 'Dit spraakbericht kon niet worden verwerkt. Stuur je vraag als tekst.'
        : 'Ik kon dit bericht niet bevestigen. Controleer eerst of er al een opdracht is gestart.';
      voice = false;
    }
    this.store.prepared(origin.id, { text: reply });
    try {
      await this.send(record, generation, reply, { id: 'reply:' + origin.id, kind: 'reply', voice });
      this.store.finish(origin.id, 'sent'); return 'sent';
    } catch { this.store.finish(origin.id, 'ambiguous'); this.lastError = 'reply_delivery_unconfirmed'; return 'ambiguous'; }
  }
  async send(record, generation, text, { id, kind, voice = false, authorize } = {}) {
    this.current(record, generation);
    let content = { text };
    if (voice && this.store.settings().audio_enabled) {
      try {
        const audio = await this.core.call('synthesize', { contact_phone: record.contact, text }, { binary: true });
        if (!audio.mime || !/^audio\/(mpeg|wav|x-wav|ogg|flac)(;|$)/.test(audio.mime)) throw new Error('audio_type');
        const encoded = await this.encode(audio.bytes); this.current(record, generation);
        content = { audio: encoded, ptt: true, mimetype: 'audio/ogg; codecs=opus' };
      } catch { this.current(record, generation); content = { text: 'Spraakantwoord nu niet beschikbaar.\n\n' + text }; this.lastError = 'audio_reply_unavailable'; }
    }
    const auth = authorize ? await authorize() : await this.core.call('authorize', { contact_phone: record.contact, message_id: record.id });
    if (!authorize && auth.authorized !== true) throw new Error('reply_not_authorized');
    this.current(record, generation);
    const providerId = this.sdk.generateMessageIDV2(this.account());
    if (!this.store.claim(id, kind, record.id, providerId)) throw new Error('outbound_already_claimed');
    try {
      // This durable fence is before sendMessage; a lost receipt is never replayed.
      this.current(record, generation);
      const response = await this.socket.sendMessage(record.source.key.remoteJid, content, { quoted: record.source, messageId: providerId });
      if (response?.key?.id !== providerId || response.key.remoteJid !== record.source.key.remoteJid || response.key.fromMe !== true) throw new Error('provider_receipt_unconfirmed');
      this.store.sent(id, providerId); return providerId;
    } catch (error) { this.store.ambiguous(id); throw error; }
  }
  async pollReturns() {
    if (this.polling || this.connection !== 'connected' || !this.store.settings().enabled) return;
    this.polling = true; const generation = this.generation, contact = this.store.settings().contact_phone;
    try {
      const result = (await this.core.call('return/poll', { contact_phone: contact })).result;
      if (!result) return;
      if (!validId(result.work_id) || !validId(result.operation_id) || typeof result.summary !== 'string' || !result.summary.trim()
        || result.summary.length > 600 || typeof result.output_truncated !== 'boolean'
        || ![result.origin_message_id, result.output_sha256, result.result_sha256].every(value => /^[0-9a-f]{64}$/.test(value || ''))) throw new Error('return_binding_unavailable');
      const id = 'return:' + result.operation_id;
      const delivered = this.store.outbound(id);
      if (delivered && delivered.state !== 'sent') return;
      const record = this.store.inbound(result.origin_message_id);
      if (!record) throw new Error('return_origin_unavailable');
      this.current(record, generation);
      const payload = { contact_phone: contact, work_id: result.work_id, operation_id: result.operation_id };
      const authorize = async () => {
        const current = await this.core.call('return/authorize', payload);
        if (current.status !== 'authorized' || current.operation_id !== result.operation_id || current.output_sha256 !== result.output_sha256 || current.result_sha256 !== result.result_sha256) throw new Error('return_authority_changed');
        return current;
      };
      // Retry only the idempotent core acknowledgment after a confirmed send.
      // The network delivery itself is never replayed after a lost ack.
      if (delivered) {
        await authorize(); this.current(record, generation);
        const ack = await this.core.call('return/ack', payload);
        if (ack.status !== 'acknowledged') throw new Error('return_ack_unconfirmed');
        return;
      }
      const summary = (result.title || 'Je opdracht').slice(0, 160) + '\n\n' + result.summary
        + (result.output_truncated ? '…' : '') + '\n\nVolledig resultaat op verzoek. Opdracht: ' + result.work_id;
      await this.send(record, generation, summary, { id, kind: 'return', authorize });
      this.current(record, generation);
      const ack = await this.core.call('return/ack', payload);
      if (ack.status !== 'acknowledged') throw new Error('return_ack_unconfirmed');
    } catch { this.lastError = 'background_return_unconfirmed'; }
    finally { this.polling = false; }
  }
}
