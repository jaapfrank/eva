// Home Assistant voice is alleen een adapter. De betekenis en uitvoering lopen
// door exact dezelfde agent als Telegram en het dashboard.
import { bus } from '../bus.js';
import { logTimeline } from '../db.js';
import { errMsg } from '../util.js';
import { restCall } from '../integrations/homeassistant.js';
import { handleMessageDetailed, recordDeliveredReply } from './agent.js';

let started = false;
const active = new Set();
const latestByConversation = new Map();
const pendingDelivery = new Map();

export function normalizeVoiceConversationId(value, sender) {
  const stripPrefix = (candidate) => String(candidate || '')
    .trim()
    .toLowerCase()
    .replace(/^(?:voice\s*:\s*)+/, '')
    .trim();
  const identity = stripPrefix(value) || stripPrefix(sender) || 'huisgenoot';
  return `voice:${identity}`;
}

export function voiceRequestInput(data = {}, id = '') {
  const rawText = String(data.vraag || data.text || data.command || '').trim();
  const markedSynthetic = /^__eva_test__\s+/i.test(rawText);
  const syntheticTest = markedSynthetic || [true, 'true', 1, '1'].includes(
    data.synthetic_test ?? data.syntheticTest,
  );
  return {
    text: markedSynthetic ? rawText.replace(/^__eva_test__\s+/i, '').trim() : rawText,
    syntheticTest,
    syntheticConversationId: syntheticTest ? `test:${id}` : '',
  };
}

async function returnVoiceEvent(id, payload) {
  return restCall('POST', '/api/events/eva_voice_reply', { id, ...payload });
}

export function startVoiceBridge() {
  if (started) return;
  started = true;
  bus.on('voice_reply_ack', (data = {}) => {
    const id = String(data.id || '').trim();
    const pending = pendingDelivery.get(id);
    if (!id || !pending || ![true, 'true', 1, '1'].includes(data.accepted)) return;
    pendingDelivery.delete(id);
    clearTimeout(pending.timer);
    recordDeliveredReply(pending.result, {
      accepted: true,
      transport: 'home-assistant-conversation-response',
      acknowledgement: 'eva_voice_reply_ack',
      satellite: String(data.satellite || '').trim() || null,
    });
  });
  bus.on('voice_query', (data = {}) => {
    const id = String(data.id || `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    if (active.has(id)) return;
    active.add(id);
    let isCurrent = () => true;

    (async () => {
      const request = voiceRequestInput(data, id);
      const { text, syntheticTest } = request;
      if (!text) return;
      const sender = String(data.sender || 'Jaap').trim() || 'Jaap';
      const conversationId = normalizeVoiceConversationId(
        data.conversation_id || data.session_id,
        request.syntheticConversationId || sender,
      );
      latestByConversation.set(conversationId, id);
      isCurrent = () => latestByConversation.get(conversationId) === id;
      const startedAt = performance.now();
      const result = await handleMessageDetailed({
        channel: 'voice',
        sender,
        text,
        messageContext: {
          identityTrusted: true,
          conversationId,
          // Gespreksgeheugen blijft per persoon/sessie, maar een trage oude
          // voicebeurt mag een nieuwe uitspraak niet achter dezelfde queue zetten.
          queueKey: `${conversationId}:${id}`,
          isCurrent,
          syntheticTest,
        },
      });
      if (!isCurrent() || result.yielded || result.superseded) {
        logTimeline('voice_superseded', `Voice-beurt ${id} is ingehaald; late tekst is niet afgeleverd.`, {
          internalOnly: true,
          id,
          conversationId,
          turnId: result.turnId || null,
        });
        return;
      }
      const timer = setTimeout(() => {
        const pending = pendingDelivery.get(id);
        if (!pending) return;
        pendingDelivery.delete(id);
        logTimeline('voice_delivery_unconfirmed', `Home Assistant bevestigde voiceantwoord ${id} niet binnen de luistertermijn.`, {
          internalOnly: true,
          id,
          conversationId,
          turnId: result.turnId || null,
        });
      }, 35_000);
      pendingDelivery.set(id, { result, timer });
      await returnVoiceEvent(id, {
        reply: result.reply,
        ok: true,
        vraag: text,
        response_time_ms: Math.round(performance.now() - startedAt),
        conversation_id: conversationId,
        conversation_active: true,
        continue_listening: true,
        listen_timeout_ms: 30_000,
      });
    })().catch(async (error) => {
      const pending = pendingDelivery.get(id);
      if (pending) {
        clearTimeout(pending.timer);
        pendingDelivery.delete(id);
      }
      logTimeline('system', `Voice-beurt liep vast: ${errMsg(error)}`, { internalOnly: true });
      try {
        if (!active.has(id) || !isCurrent()) return;
        await returnVoiceEvent(id, {
          reply: 'Mijn antwoord liep vast. Wil je die nog een keer zeggen?',
          ok: false,
          error: errMsg(error),
        });
      } catch {
        // HA is dan zelf ook niet bereikbaar.
      }
    }).finally(() => {
      setTimeout(() => active.delete(id), 60_000);
    });
  });
}

export const __voiceBridgeTest = {
  voiceConversationId: normalizeVoiceConversationId,
  voiceRequestInput,
};
