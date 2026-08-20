import { registerHooks } from 'node:module';

const voiceBridgeModuleUrl = new URL('../src/mind/voicebridge.js', import.meta.url).href;
const testComposition = String.raw`
export const __voiceBridgeTest = Object.freeze({
  createVoiceTurnGate,
  voiceConversationId: normalizeVoiceConversationId,
  voiceRequestInput,
  voiceRequestProvenance,
  voiceReplyPayload,
  voiceLaneKey,
  voiceDeliveryWindowMs: VOICE_DELIVERY_WINDOW_MS,
  issueVoiceDeliveryAuthority,
  verifiedVoiceDeliveryAuthority,
  recoverAcknowledgedVoiceDeliveries,
  scheduleAcknowledgedVoiceDeliveryDrain,
  waitForStartupRecovery() {
    return _startupVoiceRecovery;
  },
  issueSyntheticUnderstandingContext({
    messageId, satellite, conversationId, requester, text, isCurrent = () => true,
    syntheticTest = true,
  }) {
    const id = String(messageId || '');
    const endpoint = normalizedSatellite(satellite);
    const laneKey = voiceLaneKey(endpoint);
    const event = {
      id: 1, type: 'event', event: {
        event_type: 'eva_voice_query',
        data: { id, vraag: String(text || ''), satellite: endpoint },
      },
    };
    const frame = Buffer.from(JSON.stringify(event), 'utf8');
    const adapterInstanceSha256 = createHash('sha256')
      .update('explicit-isolated-voicebridge-test-composition').digest('hex');
    const sourceBodyJson = canonicalJson({
      contract: 'HomeAssistantVoiceEventEvidence/v1',
      adapterInstanceSha256,
      socketGeneration: 1,
      eventType: 'eva_voice_query',
      frameByteLength: frame.length,
      frameSha256: createHash('sha256').update(frame).digest('hex'),
      frameBase64: frame.toString('base64'),
      event,
    });
    const authenticatedSource = Object.freeze({
      eventType: 'eva_voice_query',
      sourceRef: 'home-assistant-websocket:' + createHash('sha256')
        .update(sourceBodyJson).digest('hex'),
      sourceSha256: createHash('sha256').update(sourceBodyJson).digest('hex'),
      sourceBodyJson,
      socketGeneration: 1,
      adapterInstanceSha256,
      isCurrent,
    });
    const ingressScope = 'voice:' + endpoint;
    const ingressToken = id;
    const voiceUnderstandingAuthority = issueVoiceUnderstandingSource({
      messageId: id, satellite: endpoint, laneKey,
      conversationId: String(conversationId || ''),
      requester: String(requester || ''), rawInput: String(text || ''),
      originalText: String(text || ''), isCurrent, ingressScope, ingressToken,
      authenticatedSource,
    });
    if (!voiceUnderstandingAuthority) throw new Error('synthetic_voice_authority_unavailable');
    return Object.freeze({
      conversationId: String(conversationId || ''), messageId: id,
      voiceSatellite: endpoint, voiceLaneKey: laneKey,
      voiceRawInput: String(text || ''), voiceUnderstandingAuthority,
      ingressScope, ingressToken, isCurrent, syntheticTest: syntheticTest === true,
    });
  },
});
`;

// Alleen tests die deze compositie vóór de productmodule importeren krijgen
// toegang tot de private gate. De production namespace bevat geen factory of
// test capability en kan daarmee geen authority-lifecycle fabriceren.
registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== voiceBridgeModuleUrl || loaded.format !== 'module') return loaded;
    const source = Buffer.isBuffer(loaded.source)
      ? loaded.source.toString('utf8') : String(loaded.source);
    return { ...loaded, source: `${source}\n${testComposition}` };
  },
});
