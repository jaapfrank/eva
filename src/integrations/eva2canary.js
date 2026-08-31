import { eva2TelegramPrompt } from '../telegramdialogue.js';

const DRAFT_MIN_GROWTH = 32;
const DRAFT_MAX_INTERVAL_MS = 300;

export function eva2CanaryPrompt(text) {
  return eva2TelegramPrompt(text);
}

export function createEva2DraftSink({ chatId, draftId, sendDraft, clock = Date.now }) {
  if (!Number.isSafeInteger(chatId) || chatId < 1
    || !Number.isSafeInteger(draftId) || draftId === 0 || typeof sendDraft !== 'function') return null;
  let previous = ''; let sentAt = 0; let enabled = true;
  return async (cumulative) => {
    const text = String(cumulative || '').slice(0, 4096);
    if (!enabled || !text || !text.startsWith(previous)) return false;
    const now = Number(clock());
    if (previous && text.length - previous.length < DRAFT_MIN_GROWTH
      && now - sentAt < DRAFT_MAX_INTERVAL_MS) return false;
    try {
      if (await sendDraft({ chat_id: chatId, draft_id: draftId, text }) !== true) return false;
    } catch {
      enabled = false;
      return false;
    }
    previous = text; sentAt = now;
    return true;
  };
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

async function readCanaryStream(response, onDraft) {
  const reader = response.body?.getReader?.();
  if (!reader) throw new Error('Eva B-canary gaf geen leesbare tekststream');
  const decoder = new TextDecoder();
  let buffered = ''; let cumulative = ''; let final = null;
  while (true) {
    const { value, done } = await reader.read();
    buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffered.split('\n'); buffered = lines.pop() || '';
    for (const line of lines) {
      if (!line) continue;
      const item = JSON.parse(line);
      if (item?.contract === 'EvaTextDelta/v1' && typeof item.text === 'string') {
        cumulative += item.text;
        if (cumulative) await onDraft(cumulative);
      } else if (item?.contract === 'EvaInternalGroundedReply/v1') final = item;
      else if (item?.contract === 'EvaTextStreamError/v1') {
        throw new Error('Eva2 kon haar antwoord niet veilig afronden');
      } else throw new Error('Eva2 gaf een onbekend tekststreambericht');
    }
    if (done) break;
  }
  if (buffered.trim()) throw new Error('Eva B-canary tekststream eindigde onvolledig');
  return final;
}

export async function askEva2Canary({
  baseUrl,
  chatId,
  messageId,
  fromId,
  text,
  telegramDate,
  fetchImpl = fetch,
  onDraft = null,
  timeoutMs = 20_000,
}) {
  const prompt = eva2CanaryPrompt(text);
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
  let response;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    response = await fetchImpl(url, {
      method: 'POST',
      headers: {
        Accept: streaming ? 'application/x-ndjson' : 'application/json',
        'Content-Type': 'application/json',
      },
      body,
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (response.status !== 503 || attempt > 0) break;
    try { await response.arrayBuffer?.(); } catch { /* discard the bounded error */ }
  }
  if (!response) throw new Error('Eva B-canary gaf geen antwoord');
  if (!response.ok) {
    throw new Error(`Eva B-canary antwoordde met HTTP ${response.status}`);
  }
  const streamed = streaming && response.headers?.get?.('content-type')
    ?.includes('application/x-ndjson');
  const payload = streamed ? await readCanaryStream(response, onDraft) : await response.json();
  if (payload?.contract !== 'EvaInternalGroundedReply/v1'
    || typeof payload.text !== 'string' || !payload.text.trim()
    || payload.conversation_id !== `telegram:${chatId}`
    || payload.message_id !== `telegram:${chatId}:${messageId}`) {
    throw new Error('Eva B-canary gaf geen geldig gesprekantwoord');
  }
  return Object.freeze({ ...payload, text: payload.text.trim(), fromId: String(fromId) });
}
