const CANARY_PREFIX = /^\/eva2(?:@\w+)?(?:\s+|$)/iu;
const OTHER_COMMAND = /^\/(?!eva2(?:@\w+)?(?:\s+|$))\S+/iu;
const CANARY_SESSION_MS = 30 * 60_000;
const DRAFT_MIN_GROWTH = 32;
const DRAFT_MAX_INTERVAL_MS = 300;

export function eva2CanaryPrompt(text) {
  const value = String(text || '');
  if (!CANARY_PREFIX.test(value)) return null;
  return value.replace(CANARY_PREFIX, '').trim();
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

/**
 * Keep a real Telegram conversation on Eva B after one explicit /eva2 turn.
 * The session is derived from the immutable Telegram archive, so a restart
 * cannot silently send the next short follow-up back to Eva A.
 */
export function eva2CanaryContinuation({
  text,
  telegramDate,
  fromId,
  priorMessages = [],
  sessionMs = CANARY_SESSION_MS,
} = {}) {
  const value = String(text || '').trim();
  if (!value || OTHER_COMMAND.test(value)) return false;
  const currentMs = Number(telegramDate) * 1000;
  const sender = String(fromId || '');
  if (!Number.isSafeInteger(Number(telegramDate)) || !sender
    || !Number.isSafeInteger(sessionMs) || sessionMs < 1_000) return false;
  for (let index = priorMessages.length - 1; index >= 0; index -= 1) {
    const row = priorMessages[index];
    if (row?.role !== 'user'
      || String(row?.meta?.fromTelegramId || '') !== sender
      || eva2CanaryPrompt(row?.content) === null) continue;
    const activatedMs = Date.parse(String(row.ts || ''));
    return Number.isFinite(activatedMs)
      && currentMs >= activatedMs
      && currentMs - activatedMs <= sessionMs;
  }
  return false;
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
      else throw new Error('Eva B-canary tekststream is ongeldig');
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
  recentMessages = [],
  continuation = false,
  fetchImpl = fetch,
  onDraft = null,
  timeoutMs = 20_000,
}) {
  const explicit = eva2CanaryPrompt(text);
  const prompt = explicit === null && continuation
    ? String(text || '').trim()
    : explicit;
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
    recent_messages: recentMessages,
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
