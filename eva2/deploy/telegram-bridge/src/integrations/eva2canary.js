import { eva2IncomingTelegramPrompt } from '../telegramdialogue.js';

const DRAFT_MIN_GROWTH = 32;
const DRAFT_MAX_INTERVAL_MS = 300;

export function eva2CanaryPrompt(text, options) {
  return eva2IncomingTelegramPrompt(text, options);
}

export function createEva2DraftSink({ chatId, draftId, sendDraft, clock = Date.now }) {
  if (!Number.isSafeInteger(chatId) || chatId < 1
    || !Number.isSafeInteger(draftId) || draftId === 0 || typeof sendDraft !== 'function') return null;
  let previous = ''; let sentAt = 0; let enabled = true;
  let laneClosed = false; let pendingDraft = null; let activeDraft = null;
  let draftController = null; let offered = '';
  const sink = async (cumulative) => {
    const text = String(cumulative || '').slice(0, 4096);
    if (!enabled || !text || !text.startsWith(previous)) return false;
    const now = Number(clock());
    if (previous && text.length - previous.length < DRAFT_MIN_GROWTH
      && now - sentAt < DRAFT_MAX_INTERVAL_MS) return false;
    const controller = new AbortController();
    draftController = controller;
    try {
      if (await sendDraft({ chat_id: chatId, draft_id: draftId, text }, controller.signal) !== true) return false;
    } catch {
      enabled = false;
      return false;
    } finally {
      if (draftController === controller) draftController = null;
    }
    previous = text; sentAt = now;
    return true;
  };
  function pumpDrafts() {
    if (activeDraft || pendingDraft === null || laneClosed || !enabled) return;
    activeDraft = (async () => {
      while (pendingDraft !== null && !laneClosed && enabled) {
        const text = pendingDraft; pendingDraft = null;
        await sink(text);
      }
    })().catch(() => { enabled = false; pendingDraft = null; }).finally(() => {
      activeDraft = null; pumpDrafts();
    });
  }
  sink.enqueue = (cumulative) => {
    const text = String(cumulative || '').slice(0, 4096);
    if (laneClosed || !enabled || !text || !text.startsWith(offered)) return false;
    offered = text; pendingDraft = text; pumpDrafts();
    return true;
  };
  sink.finish = async () => {
    // The saved reply supersedes queued previews. Preserve ordering with the
    // one request already in flight; never drain every obsolete text fragment.
    laneClosed = true; pendingDraft = null;
    if (activeDraft) await activeDraft;
  };
  sink.cancel = () => {
    laneClosed = true; pendingDraft = null; draftController?.abort();
  };
  sink.map = (transform) => {
    // Presentation wrappers must retain the asynchronous lane lifecycle.
    const mapped = (text) => sink(transform(text));
    mapped.enqueue = (text) => sink.enqueue(transform(text));
    mapped.finish = () => sink.finish();
    mapped.cancel = () => sink.cancel();
    return mapped;
  };
  // A transient receipt is never part of the model's cumulative draft prefix.
  sink.receipt = async (text) => {
    if (!enabled || previous || typeof text !== 'string' || !text.trim()
        || text.length > 120 || /[\r\n\x00-\x1f]/u.test(text)) return false;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 750);
    try {
      return await sendDraft({ chat_id: chatId, draft_id: draftId, text }, controller.signal) === true;
    } catch {
      return false; // Receipt failure must not stop the actual answer.
    } finally {
      clearTimeout(timer);
    }
  };
  return sink;
}

export function labelEva2TelegramReply(text) {
  const value = String(text || '').trim();
  if (!value) throw new Error('Eva2 gaf een leeg antwoord');
  return /^Eva2:/iu.test(value) ? value : `Eva2: ${value}`;
}

export function labelEva1TelegramReply(text) {
  const value = String(text || '').trim();
  if (!value) throw new Error('Eva1 gaf een leeg antwoord');
  return /^Eva1:/iu.test(value) ? value : `Eva1: ${value}`;
}

export function labelEva2TelegramFailure(reason) {
  const value = String(reason || '').trim() || 'onbekende fout';
  return `Eva2-fout: ${value}. Eva1 heeft dit bericht niet verwerkt.`;
}

function canonicalTelegramTimestamp(unixSeconds) {
  const seconds = Number(unixSeconds);
  if (!Number.isSafeInteger(seconds) || seconds < 0) {
    throw new Error('Eva B-canary mist een geldige Telegram-tijd');
  }
  return new Date(seconds * 1000).toISOString().replace('.000Z', 'Z');
}

function telegramCanaryIdentity({ chatId, fromId }) {
  const chat = Number(chatId);
  const sender = Number(fromId);
  if (!Number.isSafeInteger(chat) || chat === 0
    || !Number.isSafeInteger(sender) || sender < 1) {
    throw new Error('Eva B-canary mist een geldige Telegram-identiteit');
  }
  if (chat > 0 && chat !== sender) {
    throw new Error('Eva B-canary privéchat hoort niet bij de afzender');
  }
  return Object.freeze({ chat: String(chat), sender });
}

function canaryDeadlines({ connectTimeoutMs, idleTimeoutMs, timeoutMs, signal }) {
  for (const value of [connectTimeoutMs, idleTimeoutMs, timeoutMs]) {
    if (!Number.isSafeInteger(value) || value < 1 || value > 300_000) {
      throw new Error('Eva2 heeft een ongeldige transporttijdslimiet');
    }
  }
  const controller = new AbortController();
  let connectTimer; let idleTimer;
  const abort = (phase) => controller.abort(Object.assign(
    new Error(`Eva2: wachttijd voor ${phase} verstreken; de verwerking kan nog lopen. Er is geen tweede verzoek verstuurd.`),
    { code: 'EVA2_TRANSPORT_TIMEOUT', phase },
  ));
  const totalTimer = setTimeout(() => abort('totale verwerking'), timeoutMs);
  connectTimer = setTimeout(() => abort('verbinding'), connectTimeoutMs);
  const cancelled = () => controller.abort(signal.reason || new Error('Eva2-verzoek onderbroken'));
  signal?.addEventListener('abort', cancelled, { once: true });
  if (signal?.aborted) cancelled();
  const touch = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => abort('voortgang'), idleTimeoutMs);
  };
  const wait = (pending) => new Promise((resolve, reject) => {
    const cleanup = () => controller.signal.removeEventListener('abort', stopped);
    const stopped = () => { cleanup(); reject(controller.signal.reason); };
    if (controller.signal.aborted) stopped();
    else controller.signal.addEventListener('abort', stopped, { once: true });
    // Observe late completion/rejection too; never forward it after a deadline.
    Promise.resolve(pending).then(
      (value) => { cleanup(); resolve(value); },
      (error) => { cleanup(); reject(error); },
    );
  });
  return {
    signal: controller.signal, wait, touch,
    cancel(reason) { controller.abort(reason); },
    connected() { clearTimeout(connectTimer); touch(); },
    close() {
      clearTimeout(connectTimer); clearTimeout(idleTimer); clearTimeout(totalTimer);
      signal?.removeEventListener('abort', cancelled);
    },
  };
}

async function readCanaryStream(response, onDraft, deadline) {
  const reader = response.body?.getReader?.();
  if (!reader) throw new Error('Eva B-canary gaf geen leesbare tekststream');
  const decoder = new TextDecoder();
  let buffered = ''; let cumulative = ''; let final = null; let completed = false;
  try {
  while (true) {
    const { value, done } = await deadline.wait(reader.read());
    if (value?.length) deadline.touch();
    buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffered.split('\n'); buffered = lines.pop() || '';
    for (const line of lines) {
      if (!line) continue;
      const item = JSON.parse(line);
      if (item?.contract === 'EvaTextDelta/v1' && typeof item.text === 'string') {
        cumulative += item.text;
        if (cumulative) {
          if (typeof onDraft.enqueue === 'function') onDraft.enqueue(cumulative);
          else await deadline.wait(onDraft(cumulative));
        }
      } else if (item?.contract === 'EvaInternalGroundedReply/v1') final = item;
      else if (item?.contract === 'EvaTextStreamError/v1') {
        if (item.code === 'provider_rate_limited') {
          throw new Error('De modelprovider weigert momenteel aanvragen wegens een gebruiks- of snelheidslimiet. Er is geen nieuw antwoord beschikbaar');
        }
        throw new Error('Eva2 kon haar antwoord niet afronden; de oorzaak is nog onbekend');
      } else throw new Error('Eva2 gaf een onbekend tekststreambericht');
    }
    if (done) break;
  }
  if (buffered.trim()) throw new Error('Eva B-canary tekststream eindigde onvolledig');
  completed = true;
  return final;
  } finally {
    if (!completed) {
      onDraft.cancel?.();
      try { Promise.resolve(reader.cancel?.()).catch(() => {}); } catch { /* keep original failure */ }
    }
    try { reader.releaseLock?.(); } catch { /* preserve the original outcome */ }
    if (completed && typeof onDraft.finish === 'function') {
      try { await deadline.wait(onDraft.finish()); }
      catch (error) { onDraft.cancel?.(); throw error; }
    }
  }
}

export async function askEva2Canary({
  baseUrl,
  chatId,
  messageId,
  fromId,
  text,
  defaultPrivateIds = '',
  archivedMessage = null,
  telegramDate,
  fetchImpl = fetch,
  onDraft = null,
  onReceipt = null,
  timeoutMs = 300_000,
  connectTimeoutMs = 20_000,
  idleTimeoutMs = 120_000,
  signal = null,
}) {
  const prompt = eva2CanaryPrompt(text, { chatId, fromId, defaultPrivateIds, archivedMessage });
  if (prompt === null) return null;
  if (!prompt) throw new Error('Zet je vraag achter /eva2');
  if (!baseUrl) throw new Error('Eva B-canary is niet geconfigureerd');
  const identity = telegramCanaryIdentity({ chatId, fromId });
  const streaming = identity.chat === String(identity.sender) && typeof onDraft === 'function';
  const url = `${baseUrl}/internal/v1/telegram/canary/respond`;
  const body = JSON.stringify({
    chat_id: identity.chat,
    installation_id: 'live-test-installation',
    message_id: Number(messageId),
    occurred_at: canonicalTelegramTimestamp(telegramDate),
    // De Eva1-transportgeschiedenis is geen Eva2-gesprekscontext. Eva2 leest
    // haar eigen duurzame gesprek uitsluitend server-side uit PostgreSQL.
    recent_messages: [],
    telegram_user_id: identity.sender,
    tenant_id: 'live-test-tenant',
    text: prompt,
  });
  const deadline = canaryDeadlines({ connectTimeoutMs, idleTimeoutMs, timeoutMs, signal });
  let response;
  try {
    // One request only: an uncertain or late result must never dispatch twice.
    if (deadline.signal.aborted) throw deadline.signal.reason;
    response = await deadline.wait(fetchImpl(url, {
      method: 'POST',
      headers: {
        Accept: streaming ? 'application/x-ndjson' : 'application/json',
        'Content-Type': 'application/json',
      },
      body,
      signal: deadline.signal,
    }));
    deadline.connected();
  if (!response) throw new Error('Eva B-canary gaf geen antwoord');
  if (!response.ok) {
    throw new Error(`Eva B-canary antwoordde met HTTP ${response.status}`);
  }
  const streamed = streaming && response.headers?.get?.('content-type')
    ?.includes('application/x-ndjson');
  if (streamed && typeof onReceipt === 'function') {
    const receipt = response.headers.get('x-eva-input-receipt');
    if (typeof receipt === 'string' && receipt.trim() && receipt.length <= 120
        && !/[\r\n\x00-\x1f]/u.test(receipt)) {
      try { await onReceipt(receipt); } catch { /* Optional draft receipt cannot abort work. */ }
    }
  }
  const payload = streamed ? await readCanaryStream(response, onDraft, deadline) : await deadline.wait(response.json());
  if (payload?.contract !== 'EvaInternalGroundedReply/v1'
    || typeof payload.text !== 'string' || !payload.text.trim()
    || payload.conversation_id !== `telegram:${chatId}`
    || payload.message_id !== `telegram:${chatId}:${messageId}`) {
    throw new Error('Eva B-canary gaf geen geldig gesprekantwoord');
  }
  return Object.freeze({ ...payload, text: payload.text.trim(), fromId: String(fromId) });
  } catch (error) {
    deadline.cancel(error);
    if (response?.body && !response.body.locked) {
      try { Promise.resolve(response.body.cancel?.()).catch(() => {}); } catch { /* original error wins */ }
    }
    throw error;
  } finally { deadline.close(); }
}
