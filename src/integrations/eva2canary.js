const CANARY_PREFIX = /^\/eva2(?:@\w+)?(?:\s+|$)/iu;
const OTHER_COMMAND = /^\/(?!eva2(?:@\w+)?(?:\s+|$))\S+/iu;
const CANARY_SESSION_MS = 30 * 60_000;

export function eva2CanaryPrompt(text) {
  const value = String(text || '');
  if (!CANARY_PREFIX.test(value)) return null;
  return value.replace(CANARY_PREFIX, '').trim();
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
  timeoutMs = 20_000,
}) {
  const explicit = eva2CanaryPrompt(text);
  const prompt = explicit === null && continuation
    ? String(text || '').trim()
    : explicit;
  if (prompt === null) return null;
  if (!prompt) throw new Error('Zet je vraag achter /eva2');
  if (!baseUrl) throw new Error('Eva B-canary is niet geconfigureerd');
  const response = await fetchImpl(`${baseUrl}/internal/v1/turns/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      audience: {
        audience_id: 'person:live-test-jaap',
        kind: 'private_person',
        principal_id: 'live-test-jaap',
      },
      conversation_id: `telegram:${chatId}`,
      installation_id: 'live-test-installation',
      message_id: `telegram:${chatId}:${messageId}`,
      occurred_at: canonicalTelegramTimestamp(telegramDate),
      principal_id: 'live-test-jaap',
      recent_messages: recentMessages,
      tenant_id: 'live-test-tenant',
      text: prompt,
    }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    throw new Error(`Eva B-canary antwoordde met HTTP ${response.status}`);
  }
  const payload = await response.json();
  if (payload?.contract !== 'EvaInternalGroundedReply/v1'
    || typeof payload.text !== 'string' || !payload.text.trim()
    || payload.conversation_id !== `telegram:${chatId}`
    || payload.message_id !== `telegram:${chatId}:${messageId}`) {
    throw new Error('Eva B-canary gaf geen geldig gesprekantwoord');
  }
  return Object.freeze({ ...payload, text: payload.text.trim(), fromId: String(fromId) });
}
