const EVA2_COMMAND = /^\/eva2(?:@\w+)?(?:\s+|$)/iu;
const TELEGRAM_DIALOGUE_ROUTES = new Set(['eva1', 'eva2']);

function telegramTurnKey(row) {
  const chatId = String(row?.chatId ?? row?.chat_id ?? '').trim();
  const messageId = String(row?.messageId ?? row?.message_id ?? '').trim();
  return chatId && messageId ? `${chatId}:${messageId}` : null;
}

function eva2ReplyTurnKey(row) {
  if (String(row?.role || '') !== 'assistant') return null;
  const delivery = [row?.meta?.deliveryKey, row?.meta?.dedupeKey]
    .map((value) => String(value || ''))
    .find((value) => value.toLocaleLowerCase('nl-NL').includes('eva2-canary'));
  const match = delivery?.match(/(?:^|:)turn:telegram:([^:]+):([^:]+)/iu);
  return match ? `${match[1]}:${match[2]}` : null;
}

export function telegramDialogueRoute(value, fallback = null) {
  const route = String(value || '').trim().toLocaleLowerCase('nl-NL');
  return TELEGRAM_DIALOGUE_ROUTES.has(route) ? route : fallback;
}

export function eva2TelegramPrompt(text) {
  const value = String(text || '');
  if (!EVA2_COMMAND.test(value)) return null;
  return value.replace(EVA2_COMMAND, '').trim();
}

/** Select new private text only; historical archive inference stays unchanged. */
export function eva2IncomingTelegramPrompt(text, {
  chatId, fromId, defaultPrivateIds = '', archivedMessage = null,
} = {}) {
  const explicit = eva2TelegramPrompt(text);
  if (archivedMessage) {
    if (archivedMessage.role !== 'user' || archivedMessage.content !== text) {
      throw new Error('Telegram dialogue route does not match its archived input');
    }
    return archivedTelegramDialogueRoute(archivedMessage) === 'eva2'
      ? (explicit ?? text.trim()) : null;
  }
  if (explicit !== null) return explicit;
  if (typeof text !== 'string' || !text.trim() || text.trimStart().startsWith('/')) return null;
  if (!Number.isSafeInteger(chatId) || chatId < 1 || chatId !== fromId) return null;
  if (typeof defaultPrivateIds !== 'string' || !defaultPrivateIds.trim()) return null;
  const ids = defaultPrivateIds.split(',').map((value) => value.trim());
  if (ids.some((value) => !/^[1-9]\d*$/u.test(value) || !Number.isSafeInteger(Number(value)))) {
    throw new Error('Eva2 default private Telegram IDs are invalid');
  }
  return ids.includes(String(fromId)) ? text.trim() : null;
}

/**
 * Leid alleen voor oude archiefregels zonder expliciete route een route af.
 * Nieuwe regels krijgen altijd meta.dialogueRoute; deze fallback houdt de
 * historische Eva1/Eva2-grens na een upgrade eveneens gesloten.
 */
export function archivedTelegramDialogueRoute(row, { legacyEva2TurnKeys = null } = {}) {
  const explicit = telegramDialogueRoute(row?.meta?.dialogueRoute);
  if (explicit) return explicit;

  const role = String(row?.role || '');
  const content = String(row?.content || '');
  if (role === 'user' && eva2TelegramPrompt(content) !== null) return 'eva2';
  if (role === 'user' && legacyEva2TurnKeys?.has(telegramTurnKey(row))) return 'eva2';

  const delivery = [row?.meta?.deliveryKey, row?.meta?.dedupeKey]
    .map((value) => String(value || '').toLocaleLowerCase('nl-NL'))
    .join('\n');
  if (role === 'assistant' && (
    /^eva2(?:-fout)?:/iu.test(content.trim())
    || delivery.includes('eva2-canary')
  )) return 'eva2';
  return 'eva1';
}

export function legacyEva2TelegramTurnKeys(rows) {
  return new Set((Array.isArray(rows) ? rows : [])
    .map(eva2ReplyTurnKey)
    .filter(Boolean));
}
