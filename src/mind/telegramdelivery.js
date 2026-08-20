import { createHash } from 'node:crypto';
import {
  allPeople,
  canonicalTelegramCaption,
  canonicalTelegramText,
  db,
  resolveTelegramTargets,
  TELEGRAM_DELIVERY_STATUS,
  TELEGRAM_TRANSPORT_PROTOCOL,
  validateExecutionRecord,
  validateTelegramDeliveryProof,
} from '../db.js';
import { canonicalJson } from '../util.js';
import { householdMembers } from './household.js';

function registeredTelegramTarget(recipient, options) {
  if (options?.directReply === true) {
    const chatId = String(options?.directReplyChatId || '').trim();
    return chatId ? {
      ok: true, chatIds: [chatId], broadcast: false, directReply: true,
    } : { ok: false, info: 'Direct Telegramtransport mist de exacte antwoordchat.' };
  }
  const people = options?.householdOnly === true ? householdMembers(allPeople()) : allPeople();
  const resolved = resolveTelegramTargets(people, recipient, {
    broadcast: options?.broadcast === true,
  });
  return resolved.ok ? {
    ok: true,
    chatIds: resolved.targets.map((person) => String(person.telegram_id)).sort(),
    broadcast: resolved.broadcast === true,
    directReply: false,
  } : { ok: false, info: resolved.info, code: resolved.code };
}

function sha256(value) {
  return createHash('sha256')
    .update(Buffer.isBuffer(value) ? value : String(value ?? ''))
    .digest('hex');
}

function withoutTrustedVisibility(result) {
  return {
    ...(result && typeof result === 'object' ? result : {}),
    acceptedChatIds: [], failedChatIds: [], familyVisible: false,
  };
}

function jsonObject(value) {
  if (value && typeof value === 'object') return value;
  try { return JSON.parse(String(value || '')); } catch { return null; }
}

function exactChatIds(values) {
  if (!Array.isArray(values) || !values.length) return null;
  const ids = values.map((value) => String(value || '').trim());
  return ids.every(Boolean) && new Set(ids).size === ids.length ? ids.sort() : null;
}

function groupedChatIds(values) {
  if (!Array.isArray(values) || !values.length) return null;
  const ids = values.map((value) => String(value || '').trim());
  return ids.every(Boolean) ? [...new Set(ids)].sort() : null;
}

function expectedTelegramTool(kind) {
  return `@transport/telegram.${kind === 'text'
    ? 'sendMessage' : kind === 'photo' ? 'sendPhoto' : 'sendAudio'}`;
}

function telegramOptionInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function rejectedChildIntentMatches(intent, expected, chatId) {
  if (!intent || intent.contract !== TELEGRAM_TRANSPORT_PROTOCOL
    || intent.kind !== expected.kind || String(intent.chatId || '') !== chatId
    || String(intent.deliveryKey || '') !== String(expected.deliveryKey || '')) return false;
  const threadId = telegramOptionInteger(intent.sendOptions?.message_thread_id);
  if (threadId !== telegramOptionInteger(expected.messageThreadId)) return false;
  if (expected.kind !== 'text') {
    return intent.payloadSha256 === sha256(expected.value)
      && intent.captionSha256 === sha256(expected.caption)
      && String(intent.sendOptions?.caption || '') === String(expected.caption || '');
  }
  const index = Number(intent.segment?.index);
  const count = Number(intent.segment?.count);
  const expectedReply = index === 0 ? telegramOptionInteger(expected.replyToMessageId) : null;
  return intent.wholeReplySha256 === sha256(expected.value)
    && intent.segment?.splitVersion === 'split-3800/v1'
    && Number.isSafeInteger(index) && index >= 0
    && Number.isSafeInteger(count) && count > index
    && telegramOptionInteger(intent.sendOptions?.reply_parameters?.message_id) === expectedReply
    && String(intent.directTurnSignalRef || '') === String(expected.directTurnSignalRef || '')
    && String(intent.originPrincipal || '') === String(expected.originPrincipal || '');
}

function validateDefinitiveRejectedChild(failure, expected) {
  const chatId = String(failure?.chatId || '').trim();
  const operationKey = String(failure?.operationKey || '').trim();
  if (!chatId || !operationKey || failure?.classification !== 'rejected'
    || failure?.retryable === true || failure?.uncertain !== false) {
    throw new Error('Telegram partial bevat geen definitief geweigerd childbewijs.');
  }
  const row = validateExecutionRecord(db.prepare(`
    SELECT * FROM main.turn_tool_executions WHERE execution_key=?
  `).get(operationKey));
  const intent = jsonObject(row?.args);
  const outcome = jsonObject(row?.output);
  if (row?.status !== TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED
    || row?.tool !== expectedTelegramTool(expected.kind)
    || outcome?.classification !== 'rejected'
    || !rejectedChildIntentMatches(intent, expected, chatId)) {
    throw new Error('Telegram partial verwijst niet naar het gebonden geweigerde child.');
  }
  return chatId;
}

function verifiedPartialRegisteredDelivery(result, expected) {
  const untrusted = withoutTrustedVisibility(result);
  if (result?.ok !== false || result?.accepted !== false || result?.partial !== true
    || result?.retryable === true || result?.stale === true) return untrusted;
  try {
    const expectedChatIds = exactChatIds(expected.expectedChatIds);
    const acceptedChatIds = groupedChatIds((result.sent || []).map((item) => item?.chatId));
    const failedChatIds = exactChatIds((result.failures || []).map((item) => item?.chatId));
    if (!expectedChatIds || !acceptedChatIds || !failedChatIds
      || acceptedChatIds.some((id) => failedChatIds.includes(id))
      || canonicalJson([...acceptedChatIds, ...failedChatIds].sort())
        !== canonicalJson(expectedChatIds)) {
      throw new Error('Telegram partial heeft geen exacte ontvangerpartitionering.');
    }
    const acceptedProof = validateTelegramDeliveryProof({
      delivery: {
        ...result, accepted: true, transport: 'telegram', kind: expected.kind,
      },
      kind: expected.kind,
      value: expected.value,
      caption: expected.caption,
      deliveryKey: expected.deliveryKey,
      expectedChatIds: acceptedChatIds,
      messageThreadId: expected.messageThreadId,
      replyToMessageId: expected.replyToMessageId,
      directTurnSignalRef: expected.directTurnSignalRef,
      originPrincipal: expected.originPrincipal,
    });
    if (canonicalJson(acceptedProof.chatIds) !== canonicalJson(acceptedChatIds)) {
      throw new Error('Telegram partial childbewijs hoort bij andere ontvangers.');
    }
    const rejected = result.failures.map((failure) => (
      validateDefinitiveRejectedChild(failure, expected)
    )).sort();
    if (canonicalJson(rejected) !== canonicalJson(failedChatIds)) {
      throw new Error('Telegram partial mist een exact geweigerd childbewijs.');
    }
    return {
      ...result, acceptedChatIds, failedChatIds, familyVisible: true,
    };
  } catch {
    return {
      ...untrusted, uncertain: true, needs_followup: true,
    };
  }
}

function verifiedRegisteredDelivery(result, expected) {
  if (!result || typeof result !== 'object') {
    return {
      ok: false, accepted: false, familyVisible: false,
      acceptedChatIds: [], failedChatIds: [],
      info: 'Het externe transport gaf geen geldig resultaat.',
    };
  }
  if (result.ok !== true) return verifiedPartialRegisteredDelivery(result, expected);
  if (result.accepted !== true) {
    return {
      ...withoutTrustedVisibility(result), ok: false, accepted: false,
      info: result.info || 'Het externe transport gaf geen aantoonbaar acceptatiebewijs.',
    };
  }
  try {
    const proof = validateTelegramDeliveryProof({ delivery: result, ...expected });
    return {
      ...result,
      acceptedChatIds: proof.chatIds,
      failedChatIds: [],
      familyVisible: true,
    };
  } catch (error) {
    const info = `Telegramacceptatie kon niet worden bewezen: ${String(error?.message || error)}`;
    return {
      ...withoutTrustedVisibility(result),
      ok: false, accepted: false, uncertain: true, needs_followup: true,
      info, reason: info,
    };
  }
}

function householdDeliveryTargetIsCurrent(target, options) {
  if (options?.householdOnly !== true) return true;
  const expected = Array.isArray(options.expectedTargetIds)
    ? options.expectedTargetIds.map((id) => String(id).trim()).sort() : [];
  return expected.length > 0
    && canonicalJson(expected) === canonicalJson(target.chatIds);
}

/**
 * Maak één proceslokale transportregistry. De reconciliation-WeakSet blijft in
 * deze closure; een andere importer kan hoogstens een losstaande registry
 * maken en krijgt nooit authority over de instantie van tools.js.
 */
export function createTelegramDeliveryRegistry() {
  let messenger = null;
  let reconciliationMessenger = null;
  let photoSender = null;
  let voiceSender = null;
  const issuedReconciliationOptions = new WeakSet();

  function registerMessenger(fn, reconcileFn = undefined) {
    if (fn !== undefined) messenger = typeof fn === 'function' ? fn : null;
    if (typeof reconcileFn === 'function') reconciliationMessenger = reconcileFn;
    else if (fn === null) reconciliationMessenger = null;
  }
  const registerPhotoSender = (fn) => { photoSender = typeof fn === 'function' ? fn : null; };
  const registerVoiceSender = (fn) => { voiceSender = typeof fn === 'function' ? fn : null; };
  const issueReconciliationOptions = (options) => {
    if (!options || typeof options !== 'object') return false;
    issuedReconciliationOptions.add(options);
    return true;
  };

  async function sendRegisteredMessage(text, recipient = null, options = null) {
    const value = canonicalTelegramText(text);
    if (options?.reconcileSuperseded === true) {
      const issued = issuedReconciliationOptions.has(options);
      issuedReconciliationOptions.delete(options);
      if (!issued || typeof reconciliationMessenger !== 'function') {
        return {
          ok: false, accepted: false, stale: true, visible: true, uncertain: true,
          info: 'Telegramreconciliatie mist een intern uitgegeven supersessionattest.',
        };
      }
      return reconciliationMessenger(value, recipient, options);
    }
    if (!messenger) return { ok: false, info: 'Telegram is niet actief' };
    const target = registeredTelegramTarget(recipient, options);
    if (!target.ok) return { ...target, accepted: false };
    if (!householdDeliveryTargetIsCurrent(target, options)) {
      return {
        ok: false, accepted: false, code: 'TELEGRAM_HOUSEHOLD_TARGET_DRIFT',
        info: 'De exact gebonden gezinsontvangers zijn intussen gewijzigd; er is niets verstuurd.',
      };
    }
    return verifiedRegisteredDelivery(await messenger(value, recipient, options), {
      kind: 'text', value, deliveryKey: options?.dedupeKey || null,
      expectedChatIds: target.chatIds, broadcast: target.broadcast, directReply: target.directReply,
      messageThreadId: options?.messageThreadId || null,
      replyToMessageId: options?.replyToMessageId || null,
      directTurnSignalRef: options?.directTurnSignalRef ?? null,
      originPrincipal: options?.originPrincipal ?? null,
    });
  }

  async function sendRegisteredPhoto(buffer, contentType = 'image/jpeg', caption = '',
    recipient = null, options = null) {
    if (!photoSender) return { ok: false, info: 'Telegram-fotoverzending is niet actief' };
    const safeCaption = canonicalTelegramCaption(caption);
    const target = registeredTelegramTarget(recipient, options);
    if (!target.ok) return { ...target, accepted: false };
    if (!householdDeliveryTargetIsCurrent(target, options)) {
      return {
        ok: false, accepted: false, code: 'TELEGRAM_HOUSEHOLD_TARGET_DRIFT',
        info: 'De exact gebonden gezinsontvangers zijn intussen gewijzigd; er is niets verstuurd.',
      };
    }
    return verifiedRegisteredDelivery(
      await photoSender(buffer, contentType, safeCaption, recipient, options),
      {
        kind: 'photo', value: buffer, caption: safeCaption, deliveryKey: options?.dedupeKey || null,
        expectedChatIds: target.chatIds, broadcast: target.broadcast, directReply: target.directReply,
        messageThreadId: options?.messageThreadId || null,
        replyToMessageId: options?.replyToMessageId || null,
        directTurnSignalRef: options?.directTurnSignalRef ?? null,
        originPrincipal: options?.originPrincipal ?? null,
      },
    );
  }

  async function sendRegisteredVoice(buffer, spokenText, caption = 'Eva',
    recipient = null, options = null) {
    if (!voiceSender) return { ok: false, info: 'Telegram-spraakverzending is niet actief' };
    const safeCaption = canonicalTelegramCaption(caption || spokenText || 'Eva');
    const target = registeredTelegramTarget(recipient, options);
    if (!target.ok) return { ...target, accepted: false };
    return verifiedRegisteredDelivery(
      await voiceSender(buffer, safeCaption, recipient, options),
      {
        kind: 'audio', value: buffer, caption: safeCaption, deliveryKey: options?.dedupeKey || null,
        expectedChatIds: target.chatIds, broadcast: target.broadcast, directReply: target.directReply,
        messageThreadId: options?.messageThreadId || null,
        replyToMessageId: options?.replyToMessageId || null,
        directTurnSignalRef: options?.directTurnSignalRef ?? null,
        originPrincipal: options?.originPrincipal ?? null,
      },
    );
  }

  return Object.freeze({
    issueReconciliationOptions,
    registerMessenger,
    registerPhotoSender,
    registerVoiceSender,
    sendRegisteredMessage,
    sendRegisteredPhoto,
    sendRegisteredVoice,
  });
}
