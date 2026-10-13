import { WebSocketServer } from 'ws';

/**
 * Kleine fysieke HA-adapterfixture. Zij emuleert uitsluitend de officiële
 * auth/subscription-sequentie; tests krijgen nooit een issuer of capability.
 */
export async function startAuthenticHaVoiceTestServer() {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await new Promise((resolve, reject) => {
    server.once('listening', resolve);
    server.once('error', reject);
  });

  let currentSocket = null;
  const subscriptionIds = new WeakMap();
  let readyResolve;
  const ready = new Promise((resolve) => { readyResolve = resolve; });
  server.on('connection', (socket) => {
    currentSocket = socket;
    socket.send(JSON.stringify({ type: 'auth_required', ha_version: '2026.8.0' }));
    const subscriptions = new Map();
    subscriptionIds.set(socket, subscriptions);
    let statesRead = false;
    socket.on('message', (raw) => {
      const message = JSON.parse(raw.toString('utf8'));
      if (message.type === 'auth') {
        socket.send(JSON.stringify({ type: 'auth_ok', ha_version: '2026.8.0' }));
        return;
      }
      if (message.type === 'subscribe_events') {
        subscriptions.set(message.event_type, message.id);
        socket.send(JSON.stringify({
          id: message.id, type: 'result', success: true, result: null,
        }));
      } else if (message.type === 'get_states') {
        statesRead = true;
        socket.send(JSON.stringify({
          id: message.id, type: 'result', success: true, result: [],
        }));
      }
      if (statesRead && ['state_changed', 'eva_voice_query', 'eva_voice_reply_ack']
        .every((eventType) => subscriptions.has(eventType))) readyResolve(socket);
    });
  });

  return Object.freeze({
    url: `http://127.0.0.1:${server.address().port}`,
    ready,
    sendQuery(socket, {
      id, text, satellite, conversationId, sender = 'Jaap', speakerId = 'jaap',
    }) {
      if (socket !== currentSocket) throw new Error('ha_test_socket_not_current');
      const subscriptionId = subscriptionIds.get(socket)?.get('eva_voice_query');
      if (!Number.isSafeInteger(subscriptionId)) throw new Error('ha_test_subscription_not_ready');
      socket.send(JSON.stringify({
        id: subscriptionId,
        type: 'event',
        event: {
          event_type: 'eva_voice_query',
          time_fired: '2026-08-13T12:00:00.000Z',
          origin: 'LOCAL',
          data: {
            id, vraag: text, sender, speaker_id: speakerId,
            speaker_identity_verified: true, satellite,
            conversation_id: String(conversationId || '').replace(/^(?:voice:)+/u, ''),
          },
        },
      }));
    },
    async close() {
      currentSocket?.terminate();
      await new Promise((resolve) => server.close(resolve));
    },
  });
}
