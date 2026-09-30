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
