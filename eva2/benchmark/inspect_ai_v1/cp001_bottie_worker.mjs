// Offline host for the real Bottie route. Mirrors test/web-agent-ingress.test.js
// model responses; never substitutes the agent, HTTP handler or SQLite journal.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { generateKeyPairSync, sign, createPrivateKey, createPublicKey, createHash } from 'node:crypto';

const dataDir = fs.realpathSync(process.argv[2]);
const resume = process.argv[3] === '--resume';
if (!dataDir.startsWith('/tmp/cp001-bottie-') || (!resume && fs.readdirSync(dataDir).length)) {
  throw new Error('new private CP001 temporary directory required');
}
const keyFile = path.join(dataDir, '.cp001-client-key.pem');
if (resume && (!fs.lstatSync(keyFile).isFile() || !fs.lstatSync(path.join(dataDir, 'eva.db')).isFile())) {
  throw new Error('resume requires the fixture database and original private key');
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
  if (resume) throw new Error('provider generation forbidden after fixture restart');
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
const privateKey = resume ? createPrivateKey(fs.readFileSync(keyFile))
  : generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).privateKey;
if (!resume) {
  const fd = fs.openSync(keyFile, 'wx', 0o600);
  try {
    fs.writeFileSync(fd, privateKey.export({ type: 'pkcs8', format: 'pem' }));
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
}
const keyPair = { privateKey, publicKey: createPublicKey(privateKey) };
const key = keyPair.publicKey.export({ format: 'jwk' });
const { canonicalHttpClientAckPreimage, canonicalHttpWireSha256 } = await import('../../../src/web/httpack.js');
let lastExchange = null;
const publicKey = { contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256', kty: 'EC', crv: 'P-256', x: key.x, y: key.y };
const server = await startWeb();
const base = `http://127.0.0.1:${server.address().port}`;
const send = (value) => process.stdout.write(JSON.stringify(value) + '\n');
send({ ready: true, database: path.join(dataDir, 'eva.db'), public_key: publicKey,
  resumed: resume, pid: process.pid, network_namespace: fs.readlinkSync('/proc/self/ns/net'),
  key_file_sha256: createHash('sha256').update(fs.readFileSync(keyFile)).digest('hex') });
try {
  for await (const line of readline.createInterface({ input: process.stdin })) {
    const request = JSON.parse(line);
    if (request.stop) break;
    if (request.sign_ack) {
      if (!lastExchange || !['valid', 'wrong-key', 'changed-hash'].includes(request.sign_ack)) {
        throw new Error('known ACK case and prior successful chat required');
      }
      const envelope = lastExchange.body._httpDelivery;
      const ack = { contract: 'HttpClientAck/v3', requestRef: envelope.requestRef,
        responseRef: envelope.responseRef, responseSha256: envelope.responseSha256,
        deliveryRef: envelope.deliveryRef, challenge: envelope.challenge,
        wireSha256: lastExchange.wireSha256, writeAttemptRef: lastExchange.writeAttemptRef,
        writeGeneration: lastExchange.writeGeneration };
      if (request.sign_ack === 'changed-hash') {
        ack.responseSha256 = 'f'.repeat(64);
        ack.responseRef = `http-response:${ack.responseSha256}`;
      }
      const privateKey = request.sign_ack === 'wrong-key'
        ? generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).privateKey : keyPair.privateKey;
      const signature = sign('sha256', Buffer.from(canonicalHttpClientAckPreimage(ack), 'utf8'),
        { key: privateKey, dsaEncoding: 'ieee-p1363' }).toString('base64url');
      send({ ack: { ...ack, signature } });
      continue;
    }
    if (!['/api/chat', '/api/http-delivery/ack'].includes(request.path)) {
      throw new Error('only local chat and ACK fixture routes allowed');
    }
    if (request.path === '/api/chat') currentText = request.body.text;
    const response = await nativeFetch(base + request.path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer cp001-local-contract' },
      body: JSON.stringify(request.body), signal: AbortSignal.timeout(request.timeout_ms),
    });
    const text = await response.text();
    if (Buffer.byteLength(text) > request.max_bytes) throw new Error('response byte budget exceeded');
    const body = JSON.parse(text);
    if (request.path === '/api/chat' && response.status === 200) {
      lastExchange = { body, wireSha256: canonicalHttpWireSha256({ status: response.status,
        contentType: response.headers.get('content-type'), bodyBytes: Buffer.from(text, 'utf8') }),
      writeAttemptRef: response.headers.get('x-eva-http-write-attempt-ref'),
      writeGeneration: Number(response.headers.get('x-eva-http-write-generation')) };
    }
    send({ status: response.status, body, provider_stub_calls: providerCalls,
      replayed: response.headers.get('idempotency-replayed'), real_model_calls: 0 });
  }
} finally {
  await new Promise((resolve) => server.close(resolve));
  db.pragma('wal_checkpoint(TRUNCATE)');
  db.close();
}
// Imported product modules may own idle timers; the isolated fixture is finished.
process.exit(0);
