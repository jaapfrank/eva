// Offline host for the real Bottie route. Mirrors test/web-agent-ingress.test.js
// model responses; never substitutes the agent, HTTP handler or SQLite journal.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { generateKeyPairSync, sign, createPrivateKey, createPublicKey, createHash } from 'node:crypto';

const args = process.argv.slice(3);
let sourceRoot = fileURLToPath(new URL('../../../', import.meta.url));
let explicitSource = false;
for (let index = 0; index < args.length; index += 1) {
  if (['--resume', '--sample-control'].includes(args[index])) continue;
  if (args[index] !== '--source-root' || explicitSource || !args[index + 1] || !path.isAbsolute(args[index + 1])) {
    throw new Error('expected one absolute existing --source-root or known fixture flags');
  }
  sourceRoot = fs.realpathSync(args[++index]);
  explicitSource = true;
}
sourceRoot = fs.realpathSync(sourceRoot);
if (!fs.statSync(sourceRoot).isDirectory()) throw new Error('source root must be a directory');
const sourceFiles = ['src/db.js', 'src/web/server.js', 'src/web/httpack.js',
  'src/mind/agent.js', 'src/brain/llm.js', 'src/config.js', 'package.json'];
const sourceHashes = Object.fromEntries(sourceFiles.map(relative => {
  const filename = fs.realpathSync(path.join(sourceRoot, relative));
  if (!filename.startsWith(sourceRoot + path.sep) || !fs.statSync(filename).isFile()) {
    throw new Error('native source must be a regular file inside source root');
  }
  return [relative, createHash('sha256').update(fs.readFileSync(filename)).digest('hex')];
}));
const nativeModule = relative => pathToFileURL(path.join(sourceRoot, relative)).href;
const dataDir = fs.realpathSync(process.argv[2]);
const resume = process.argv.includes('--resume');
const sampleControl = process.argv.includes('--sample-control');
if (!dataDir.startsWith('/tmp/cp001-bottie-') || (!resume && fs.readdirSync(dataDir).length)) {
  throw new Error('new private CP001 temporary directory required');
}
const keyFile = path.join(dataDir, '.cp001-client-key.pem');
if (resume && (!fs.lstatSync(keyFile).isFile() || !fs.lstatSync(path.join(dataDir, 'eva.db')).isFile())) {
  throw new Error('resume requires the fixture database and original private key');
}
// Never load the selected source tree's real dotenv configuration.
process.env.DOTENV_CONFIG_PATH = '/dev/null';
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
const send = (value) => process.stdout.write(JSON.stringify(value) + '\n');
let currentRequestId = null;
let currentRole = 'contact';
let stopping = false;
let eventSequence = 0;
const pendingControls = new Map();
const providerControllers = new Set();
const stopController = new AbortController();
function stopSample() {
  stopping = true;
  stopController.abort();
  for (const controller of providerControllers) controller.abort();
  for (const resolve of pendingControls.values()) resolve({ allow: false });
  pendingControls.clear();
}
function decision(event, controller) {
  if (stopping || controller.signal.aborted) { send(event); return Promise.resolve({ allow: false }); }
  return new Promise(resolve => {
    const abort = () => { pendingControls.delete(event.event_id); resolve({ allow: false }); };
    controller.signal.addEventListener('abort', abort, { once: true });
    pendingControls.set(event.event_id, value => { controller.signal.removeEventListener('abort', abort); resolve(value); });
    send(event);
  });
}

const reply = 'Ik geef een lokaal controleantwoord.';
function completed(output) {
  return new Response(`data: ${JSON.stringify({ type: 'response.completed', response: { output, ...(sampleControl ? { usage: { input_tokens: 17, output_tokens: 5, total_tokens: 22 } } : {}) } })}\n\n`,
    { status: 200, headers: { 'content-type': 'text/event-stream' } });
}
const fixtureFetch = async (input) => {
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
globalThis.fetch = async (input, init = {}) => {
  if (!sampleControl) return fixtureFetch(input);
  if (stopping) throw new DOMException('sample stopped', 'AbortError');
  const body = JSON.parse(init.body || '{}');
  const eventId = `provider:${++eventSequence}`;
  const controller = new AbortController();
  providerControllers.add(controller);
  const abort = () => controller.abort();
  init.signal?.addEventListener('abort', abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  let attemptId = null;
  let settled = false;
  const settle = async (status, usage) => {
    settled = true;
    return decision({ provider_event: 'settle', event_id: eventId,
      request_id: currentRequestId, attempt_id: attemptId, status, usage }, controller);
  };
  try {
    const admission = await decision({ provider_event: 'begin', event_id: eventId,
      request_id: currentRequestId, role: currentRole, model: body.model ?? null,
      reasoning_effort: body.reasoning?.effort ?? null }, controller);
    if (!admission.allow || !Number.isSafeInteger(admission.attempt_id) || admission.attempt_id < 0 || controller.signal.aborted || stopping) {
      stopSample();
      throw new DOMException('sample dispatch denied', 'AbortError');
    }
    attemptId = admission.attempt_id;
    const response = await fixtureFetch(input);
    // Clone observes exact native SSE; the original response bytes remain untouched.
    const raw = await response.clone().text();
    let usage = null;
    for (const line of raw.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      try { const event = JSON.parse(line.slice(6));
        if (event.type === 'response.completed') usage = event.response?.usage ?? null;
      } catch { /* Unknown events do not manufacture accounting. */ }
    }
    const released = await settle('completed', usage);
    if (!released.allow || stopping || controller.signal.aborted) {
      stopSample();
      throw new DOMException('sample continuation denied', 'AbortError');
    }
    return response;
  } catch (error) {
    if (!settled) await settle(error?.name === 'AbortError' ? 'aborted' : 'error', null);
    throw error;
  } finally {
    init.signal?.removeEventListener('abort', abort);
    providerControllers.delete(controller);
  }
};
const { db } = await import(nativeModule('src/db.js'));
const { startWeb } = await import(nativeModule('src/web/server.js'));
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
const { canonicalHttpClientAckPreimage, canonicalHttpWireSha256 } = await import(nativeModule('src/web/httpack.js'));
let lastExchange = null;
const publicKey = { contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256', kty: 'EC', crv: 'P-256', x: key.x, y: key.y };
const server = await startWeb();
const base = `http://127.0.0.1:${server.address().port}`;
send({ ready: true, source_root: sourceRoot, source_sha256: sourceHashes, explicit_source_root: explicitSource, database: path.join(dataDir, 'eva.db'), public_key: publicKey,
  ...(sampleControl ? { sample_control: true, role_scope: 'controlled contact fixture only' } : {}),
  resumed: resume, pid: process.pid, network_namespace: fs.readlinkSync('/proc/self/ns/net'),
  key_file_sha256: createHash('sha256').update(fs.readFileSync(keyFile)).digest('hex') });
async function handle(request) {
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
      return;
    }
    if (!['/api/chat', '/api/http-delivery/ack'].includes(request.path)) {
      throw new Error('only local chat and ACK fixture routes allowed');
    }
    if (request.path === '/api/chat') { currentText = request.body.text; currentRequestId = request.body.request_id; currentRole = request.sample_role || 'contact'; }
    const response = await nativeFetch(base + request.path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer cp001-local-contract' },
      body: JSON.stringify(request.body), signal: sampleControl ? AbortSignal.any([AbortSignal.timeout(request.timeout_ms), stopController.signal]) : AbortSignal.timeout(request.timeout_ms),
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
let chain = Promise.resolve();
let handlerError = null;
let ingressActive = false;
const input = readline.createInterface({ input: process.stdin });
try {
  for await (const line of input) {
    const request = JSON.parse(line);
    if (sampleControl && request.provider_control) {
      const resolve = pendingControls.get(request.provider_control);
      if (!resolve) throw new Error('provider control has no pending event');
      pendingControls.delete(request.provider_control); resolve(request); continue;
    }
    if (request.stop) { stopSample(); break; }
    if (sampleControl) {
      if (ingressActive) throw new Error('only one controlled ingress may be active');
      if (request.sample_role && request.sample_role !== 'contact') throw new Error('controlled fixture supports contact role only');
      ingressActive = true;
      chain = chain.then(() => stopping ? undefined : handle(request)).catch(error => {
        if (!stopping) { handlerError = error; stopSample(); }
      }).finally(() => { ingressActive = false; });
    } else await handle(request);
  }
  await chain;
  if (handlerError) throw handlerError;
} finally {
  input.close();
  if (sampleControl) { stopSample(); server.closeAllConnections(); }
  await new Promise((resolve) => server.close(resolve));
  db.pragma('wal_checkpoint(TRUNCATE)');
  db.close();
  if (sampleControl) send({ stopped: true, provider_stub_calls: providerCalls, pending_provider_controls: pendingControls.size, active_provider_attempts: providerControllers.size, real_model_calls: 0 });
}
// Imported product modules may own idle timers; the isolated fixture is finished.
process.exit(0);
