// Home Assistant voice is alleen een adapter. De betekenis en uitvoering lopen
// door exact dezelfde agent als Telegram en het dashboard.
import { bus } from '../bus.js';
import { logTimeline } from '../db.js';
import { errMsg } from '../util.js';
import { restCall } from '../integrations/homeassistant.js';
import { handleMessageDetailed, recordDeliveredReply } from './agent.js';

let started = false;
const active = new Set();

export function normalizeVoiceConversationId(value, sender) {
  const stripPrefix = (candidate) => String(candidate || '')
    .trim()
    .toLowerCase()
    .replace(/^(?:voice\s*:\s*)+/, '')
    .trim();
  const identity = stripPrefix(value) || stripPrefix(sender) || 'huisgenoot';
  return `voice:${identity}`;
}

async function returnVoiceEvent(id, payload) {
  return restCall('POST', '/api/events/eva_voice_reply', { id, ...payload });
}

export function startVoiceBridge() {
  if (started) return;
  started = true;
  bus.on('voice_query', (data = {}) => {
    const id = String(data.id || `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    if (active.has(id)) return;
    active.add(id);

    (async () => {
      const text = String(data.vraag || data.text || data.command || '').trim();
      if (!text) return;
      const sender = String(data.sender || 'Jaap').trim() || 'Jaap';
      const conversationId = normalizeVoiceConversationId(
        data.conversation_id || data.session_id,
        sender,
      );
      const startedAt = performance.now();
      const result = await handleMessageDetailed({
        channel: 'voice',
        sender,
        text,
        messageContext: {
          identityTrusted: true,
          conversationId,
        },
      });
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
      recordDeliveredReply(result, { accepted: true, transport: 'home-assistant-voice' });
    })().catch(async (error) => {
      logTimeline('system', `Voice-beurt liep vast: ${errMsg(error)}`, { internalOnly: true });
      try {
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

export const __voiceBridgeTest = { voiceConversationId: normalizeVoiceConversationId };
