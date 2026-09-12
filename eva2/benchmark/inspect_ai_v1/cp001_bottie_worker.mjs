// Offline host for the real Bottie route. Mirrors test/web-agent-ingress.test.js
// model responses; never substitutes the agent, HTTP handler or SQLite journal.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { generateKeyPairSync } from 'node:crypto';

const dataDir = fs.realpathSync(process.argv[2]);
if (!dataDir.startsWith('/tmp/cp001-bottie-') || fs.readdirSync(dataDir).length) {
  throw new Error('new private CP001 temporary directory required');
}
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.NODE_ENV = 'test';
process.env.EVA_RUNTIME_MODE = 'normal';
process.env.EVA_WEB_TOKEN = 'cp001-local-contract';
console.log = (...args) => console.error(...args);
const nativeFetch = globalThis.fetch;
let providerCalls = 0;
let currentText = '';
const reply = 'Ik geef een lokaal controleantwoord.';
function completed(output) {
  return new Response(`data: ${JSON.stringify({ type: 'response.completed', response: { output } })}\n\n`,
    { status: 200, headers: { 'content-type': 'text/event-stream' } });
}
globalThis.fetch = async (input) => {
  if (!['https://api.openai.com/v1/responses', 'https://chatgpt.com/backend-api/codex/responses'].includes(String(input))) {
    throw new Error('external network disabled in CP001 fixture');
  }
  providerCalls += 1;
  if (providerCalls % 2 === 1) {
    return completed([{ type: 'function_call', call_id: 'cp001-understanding',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'chat',
        goal: { statement: currentText, outcome: 'Geef een gewoon antwoord.', beneficiary: 'requester' },
        conditions: [], provenance: { sourceSpans: [{ start: 0, end: currentText.length }], contextHashes: [] },
        confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
      }) }]);
  }
  return completed([{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: reply }] }]);
};
const { db } = await import('../../../src/db.js');
const { startWeb } = await import('../../../src/web/server.js');
const key = generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).publicKey.export({ format: 'jwk' });
const publicKey = { contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256', kty: 'EC', crv: 'P-256', x: key.x, y: key.y };
const server = await startWeb();
const base = `http://127.0.0.1:${server.address().port}`;
const send = (value) => process.stdout.write(JSON.stringify(value) + '\n');
send({ ready: true, database: path.join(dataDir, 'eva.db'), public_key: publicKey });
try {
  for await (const line of readline.createInterface({ input: process.stdin })) {
    const request = JSON.parse(line);
    if (request.stop) break;
    if (request.path !== '/api/chat') throw new Error('only local chat fixture route allowed');
    currentText = request.body.text;
    const response = await nativeFetch(base + request.path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer cp001-local-contract' },
      body: JSON.stringify(request.body), signal: AbortSignal.timeout(request.timeout_ms),
    });
    const text = await response.text();
    if (Buffer.byteLength(text) > request.max_bytes) throw new Error('response byte budget exceeded');
    send({ status: response.status, body: JSON.parse(text), provider_stub_calls: providerCalls,
      replayed: response.headers.get('idempotency-replayed'), real_model_calls: 0 });
  }
} finally {
  await new Promise((resolve) => server.close(resolve));
  db.pragma('wal_checkpoint(TRUNCATE)');
  db.close();
}
// Imported product modules may own idle timers; the isolated fixture is finished.
process.exit(0);
