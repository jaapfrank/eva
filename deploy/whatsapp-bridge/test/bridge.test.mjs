import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { Store, sourceId } from '../store.mjs';
import { Bridge } from '../bridge.mjs';
import { Core, tokenFromFile } from '../core.mjs';
import { controlServer } from '../server.mjs';
import { boundedAudio, decodeAudio, encodeVoice, MAX_AUDIO_BYTES } from '../audio.mjs';
import * as realSdk from '@whiskeysockets/baileys';

const CONTACT = '31600000000', ACCOUNT = CONTACT + ':1@s.whatsapp.net';
function fixture(t, overrides = {}) {
  const directory = mkdtempSync(join(tmpdir(), 'eva-wa-test-'));
  const store = new Store(directory, realSdk.BufferJSON), sends = [], calls = [], sockets = [];
  let sequence = 0;
  const core = { token: () => 'synthetic-token', call: async (path, body, options) => {
    calls.push({ path, body, options });
    if (overrides.call) { const value = await overrides.call(path, body, options); if (value !== undefined) return value; }
    if (path === 'respond') return { text: 'Antwoord', conversation_id: 'whatsapp:' + body.contact_phone, message_id: 'whatsapp:' + body.contact_phone + ':' + body.message_id };
    if (path === 'authorize') return { authorized: true };
    if (path === 'transcribe') return { text: 'Synthetische spraakvraag' };
    if (path === 'synthesize') return { bytes: Buffer.from('encoded-audio'), mime: 'audio/mpeg' };
    if (path === 'return/poll') return { status: 'none' };
    throw new Error('unexpected_core_route:' + path);
  } };
  const sdk = { initAuthCreds: realSdk.initAuthCreds, proto: realSdk.proto, DisconnectReason: realSdk.DisconnectReason,
    normalizeMessageContent: realSdk.normalizeMessageContent, generateMessageIDV2: () => 'BOT-' + ++sequence,
    downloadMediaMessage: async () => Readable.from([Buffer.from('source-audio')]),
    makeSocket: options => {
      const socket = { user: { id: ACCOUNT }, ev: new EventEmitter(), options,
        end() {}, async logout() {}, updateMediaMessage() {},
        async sendMessage(jid, content, opts) { sends.push({ jid, content, opts }); if (overrides.send) return await overrides.send(jid, content, opts); return { key: { id: opts.messageId, remoteJid: jid, fromMe: true } }; },
      }; sockets.push(socket); return socket;
    } };
  const bridge = new Bridge(store, core, sdk, { logger: {}, qr: async () => 'data:image/png;base64,synthetic',
    decode: async bytes => { assert.equal(bytes.toString(), 'source-audio'); return Buffer.from([0, 0]); },
    encode: async () => Buffer.from('OggS-synthetic'), ...overrides.adapters });
  t.after(() => { bridge.stopSocket(); store.close(); rmSync(directory, { recursive: true, force: true }); });
  return { directory, store, bridge, sends, calls, sockets, core, sdk };
}
async function connected(f, { audio = false, configure = true } = {}) {
  if (configure) await f.bridge.configure({ contact_phone: CONTACT, audio_enabled: audio });
  await f.bridge.enable(); await f.bridge.connectionUpdate({ connection: 'open' }, f.bridge.socket, f.bridge.generation);
}
const message = (id = 'USER-1', extra = {}) => ({ key: { id, remoteJid: CONTACT + '@s.whatsapp.net', fromMe: false },
  messageTimestamp: Math.floor(Date.now() / 1000), message: { conversation: 'Synthetische vraag' }, ...extra });
const defer = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test('disabled startup does not connect; QR-only enable chooses authenticated own PN', async t => {
  const f = fixture(t); await f.bridge.start(); assert.equal(f.sockets.length, 0);
  assert.equal(f.bridge.status().enabled, false);
  await f.bridge.enable(); assert.equal(f.bridge.status().contact_phone, null);
  assert.equal(f.bridge.resolve(message()), null);
  await f.bridge.connectionUpdate({ qr: 'private-qr' }, f.bridge.socket, f.bridge.generation);
  assert.equal(f.bridge.status().connection, 'awaiting_qr');
  await f.bridge.connectionUpdate({ connection: 'open' }, f.bridge.socket, f.bridge.generation);
  assert.equal(f.bridge.status().contact_phone, CONTACT); assert.equal(f.bridge.status().qr_data_url, null);
});
test('configured contact is never replaced; LID does not invent own phone', async t => {
  const f = fixture(t); await f.bridge.configure({ contact_phone: '31600000001', audio_enabled: false });
  await f.bridge.enable(); await f.bridge.connectionUpdate({ connection: 'open' }, f.bridge.socket, f.bridge.generation);
  assert.equal(f.bridge.status().contact_phone, '31600000001');
  await f.bridge.disable(); f.store.put('settings', { ...f.store.settings(), contact_phone: null });
  await f.bridge.enable(); f.bridge.socket.user.id = '12345@lid';
  await f.bridge.connectionUpdate({ connection: 'open' }, f.bridge.socket, f.bridge.generation);
  assert.equal(f.bridge.status().contact_phone, null); assert.equal(f.bridge.resolve(message()), null);
});
test('exact quotes, stable digest, concurrent questions and durable deduplication', async t => {
  const f = fixture(t); await connected(f);
  const first = message(), second = message('USER-2');
  assert.deepEqual(await Promise.all([f.bridge.incoming(first), f.bridge.incoming(second)]), ['sent', 'sent']);
  assert.equal(await f.bridge.incoming(first), 'duplicate'); assert.equal(f.sends.length, 2);
  assert.deepEqual(f.sends[0].opts.quoted.key, first.key);
  assert.equal(f.calls.find(c => c.path === 'respond').body.message_id, sourceId(first.key));
  assert.equal(f.store.inbound(sourceId(first.key)).state, 'sent');
  assert.equal(f.store.outbound('reply:' + sourceId(first.key)).state, 'sent');
  assert.equal(f.calls.filter(c => c.path === 'authorize').length, 2);
});
test('only live configured private contact; own bot messages and history are excluded', async t => {
  const f = fixture(t); await connected(f);
  for (const key of [{ remoteJid: '31600000001@s.whatsapp.net' }, { remoteJid: '123@g.us' }, { remoteJid: 'status@broadcast' }, { participant: CONTACT + '@s.whatsapp.net' }]) {
    const m = message(); Object.assign(m.key, key); assert.equal(await f.bridge.incoming(m), 'ignored');
  }
  assert.equal(await f.bridge.incoming(message('OLD', { messageTimestamp: f.store.settings().accept_after - 1 })), 'ignored');
  f.bridge.socket.ev.emit('messages.upsert', { type: 'append', messages: [message('HISTORY')] });
  assert.equal(f.calls.length, 0);
  await f.bridge.incoming(message());
  assert.equal(await f.bridge.incoming(message(f.sends[0].opts.messageId, { key: { id: f.sends[0].opts.messageId, remoteJid: CONTACT + '@s.whatsapp.net', fromMe: true } })), 'ignored');
  const self = message('SELF'); self.key.fromMe = true; assert.equal(await f.bridge.incoming(self), 'sent');
  const lid = message('LID'); lid.key.remoteJid = '12345@lid'; lid.key.remoteJidAlt = CONTACT + '@s.whatsapp.net';
  assert.equal(await f.bridge.incoming(lid), 'sent'); assert.equal(f.sends.at(-1).jid, '12345@lid');
});
test('disabling during core response blocks all sends and duplicate replay', async t => {
  const entered = defer(), release = defer();
  const f = fixture(t, { call: async path => { if (path === 'respond') { entered.resolve(); await release.promise; } } });
  await connected(f); const task = f.bridge.incoming(message()); await entered.promise;
  await f.bridge.disable(); release.resolve(); assert.equal(await task, 'ambiguous'); assert.equal(f.sends.length, 0);
  await connected(f); assert.equal(await f.bridge.incoming(message()), 'duplicate');
});
test('fresh core rejection and token revocation prevent dispatch', async t => {
  const f = fixture(t, { call: async path => path === 'authorize' ? { authorized: false } : undefined });
  await connected(f); assert.equal(await f.bridge.incoming(message()), 'ambiguous'); assert.equal(f.sends.length, 0);
  f.core.token = () => { throw new Error('revoked'); };
  assert.equal(await f.bridge.incoming(message('REVOKED')), 'ambiguous'); assert.equal(f.sends.length, 0);
});
test('uncertain provider receipt and restart never replay', async t => {
  const f = fixture(t, { send: async () => { throw new Error('lost-receipt'); } }); await connected(f);
  assert.equal(await f.bridge.incoming(message()), 'ambiguous'); assert.equal(f.sends.length, 1);
  assert.equal(f.store.outbound('reply:' + sourceId(message().key)).state, 'ambiguous');
  assert.equal(await f.bridge.incoming(message()), 'duplicate'); assert.equal(f.sends.length, 1);
  const dir = mkdtempSync(join(tmpdir(), 'eva-wa-restart-')); let store = new Store(dir);
  store.admit('id', CONTACT, ACCOUNT, {}, '2026-10-16T00:00:00Z'); store.claim('out', 'reply', 'id', 'P'); store.close();
  store = new Store(dir); assert.equal(store.inbound('id').state, 'ambiguous'); assert.equal(store.outbound('out').state, 'ambiguous'); store.close(); rmSync(dir, { recursive: true });
});
test('wrong provider receipt contact remains uncertain and is never acknowledged as sent', async t => {
  const f = fixture(t, { send: async (_jid, _content, options) => ({ key: { id: options.messageId, remoteJid: '31600000001@s.whatsapp.net', fromMe: true } }) });
  await connected(f); assert.equal(await f.bridge.incoming(message()), 'ambiguous');
  assert.equal(f.store.outbound('reply:' + sourceId(message().key)).state, 'ambiguous');
});
test('SQLite auth codec preserves real SDK credentials and app-state keys; logout clears only auth', async t => {
  const f = fixture(t); await connected(f); const creds = f.store.get('auth-creds');
  assert.ok(Buffer.isBuffer(creds.noiseKey.private));
  f.store.setAuthKeys({ 'app-state-sync-key': { abc: { keyData: Buffer.from([1, 2, 3]) } }, session: { a: Buffer.from([4, 5]) } });
  const keys = await f.bridge.socket.options.auth.keys.get('app-state-sync-key', ['abc']); assert.deepEqual([...keys.abc.keyData], [1, 2, 3]);
  await f.bridge.incoming(message()); const stale = f.bridge.socket.options.auth.keys; await f.bridge.disable(); assert.ok(f.store.get('auth-creds'));
  await assert.rejects(stale.set({ session: { stale: Buffer.from([9]) } }), /session_authority_changed/);
  await assert.rejects(stale.get('session', ['a']), /session_authority_changed/);
  await f.bridge.enable(); await f.bridge.logout(); assert.equal(f.store.get('auth-creds'), undefined);
  assert.equal(f.store.get('auth-key:session:a'), undefined); assert.ok(f.store.inbound(sourceId(message().key)));
  await assert.rejects(stale.set({ session: { stale: Buffer.from([9]) } }), /session_authority_changed/);
});
test('audio disabled is explicit without model; enabled uses bounded media and hashes source/transcript', async t => {
  const f = fixture(t); await connected(f);
  const audio = message('AUDIO', { message: { audioMessage: { mimetype: 'audio/ogg', ptt: true, seconds: 1, fileLength: 12 } } });
  await f.bridge.incoming(audio); assert.match(f.sends[0].content.text, /staan.*uit/);
  assert.equal(f.calls.filter(c => ['respond', 'transcribe', 'synthesize'].includes(c.path)).length, 0);
  await connected(f, { audio: true }); audio.key.id = 'AUDIO-ON'; await f.bridge.incoming(audio);
  assert.equal(f.sends.at(-1).content.ptt, true); assert.equal(f.sends.at(-1).content.audio.toString(), 'OggS-synthetic');
  const evidence = f.store.inbound(sourceId(audio.key)).source.evidence;
  assert.equal(evidence.audio_sha256, createHash('sha256').update('source-audio').digest('hex'));
  assert.equal(evidence.transcript_sha256, createHash('sha256').update('Synthetische spraakvraag').digest('hex'));
  assert.equal(f.calls.find(c => c.path === 'transcribe').options.contact, CONTACT);
});
test('oversized audio and unavailable transcription do not start a conversation; TTS fails honestly', async t => {
  let mode = 'transcribe';
  const f = fixture(t, { call: async path => { if (path === mode) throw new Error('unavailable'); } }); await connected(f, { audio: true });
  const audio = message('BAD-AUDIO', { message: { audioMessage: { mimetype: 'audio/ogg', fileLength: MAX_AUDIO_BYTES + 1 } } });
  await f.bridge.incoming(audio); assert.equal(f.calls.some(c => c.path === 'respond'), false);
  audio.key.id = 'NO-STT'; audio.message.audioMessage.fileLength = 12; await f.bridge.incoming(audio);
  assert.equal(f.calls.some(c => c.path === 'respond'), false); assert.match(f.sends.at(-1).content.text, /niet worden verwerkt/);
  mode = 'synthesize'; audio.key.id = 'NO-TTS'; await f.bridge.incoming(audio); assert.match(f.sends.at(-1).content.text, /Spraakantwoord nu niet beschikbaar/);
  await assert.rejects(boundedAudio(Readable.from([Buffer.alloc(MAX_AUDIO_BYTES + 1)])), /audio_size/);
});
test('background return exact source, fresh digest authorization, summary and ack failure never resend', async t => {
  let result, badHash = false, ackFail = true;
  const f = fixture(t, { call: async path => {
    if (path === 'return/poll') return { status: 'available', result };
    if (path === 'return/authorize') return { status: 'authorized', operation_id: result.operation_id, output_sha256: badHash ? 'bad' : result.output_sha256, result_sha256: result.result_sha256 };
    if (path === 'return/ack') { if (ackFail) throw new Error('ack-lost'); return { status: 'acknowledged' }; }
  } }); await connected(f); const m = message(); await f.bridge.incoming(m);
  result = { work_id: 'work-1', operation_id: 'operation-1', summary: 'x'.repeat(600), output_truncated: true, title: 'Klaar', origin_message_id: sourceId(m.key), output_sha256: 'a'.repeat(64), result_sha256: 'b'.repeat(64) };
  badHash = true; await f.bridge.pollReturns(); assert.equal(f.sends.length, 1);
  badHash = false; await f.bridge.pollReturns(); assert.equal(f.sends.length, 2);
  assert.deepEqual(f.sends[1].opts.quoted.key, m.key); assert.ok(f.sends[1].content.text.length < 800);
  assert.match(f.sends[1].content.text, /Volledig resultaat op verzoek/);
  ackFail = false; await f.bridge.pollReturns(); assert.equal(f.sends.length, 2);
  assert.equal(f.calls.filter(c => c.path === 'return/ack').length, 2);
  assert.equal(f.store.outbound('return:operation-1').state, 'sent');
});
test('all control RPCs require current bearer, no-store status and strict body', async t => {
  const f = fixture(t); let token = 'a'.repeat(40); const server = controlServer(f.bridge, () => token);
  await new Promise(r => server.listen(0, '127.0.0.1', r)); t.after(() => new Promise(r => server.close(r)));
  const url = 'http://127.0.0.1:' + server.address().port;
  for (const path of ['/status', '/enable', '/disable', '/logout', '/configure']) assert.equal((await fetch(url + path, { method: path === '/status' ? 'GET' : 'POST' })).status, 401);
  const headers = { authorization: 'Bearer ' + token, 'content-type': 'application/json' };
  const status = await fetch(url + '/status', { headers }); assert.equal(status.status, 200); assert.equal(status.headers.get('cache-control'), 'no-store'); assert.equal((await status.json()).enabled, false);
  assert.equal((await fetch(url + '/enable', { method: 'POST', headers, body: '{"tenant":"x"}' })).status, 422);
  assert.equal((await fetch(url + '/status?contact=x', { headers })).status, 422);
  token = 'b'.repeat(40); assert.equal((await fetch(url + '/status', { headers })).status, 401);
  assert.equal(f.sockets.length, 0);
});
test('private token refuses symlink and unsafe file; core uses exact auth-bound route', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'eva-wa-token-')); t.after(() => rmSync(dir, { recursive: true }));
  const file = join(dir, 'token'); writeFileSync(file, 't'.repeat(40), { mode: 0o600 }); assert.equal(tokenFromFile(file), 't'.repeat(40));
  symlinkSync(file, join(dir, 'link')); assert.throws(() => tokenFromFile(join(dir, 'link')));
  assert.throws(() => new Core('https://example.invalid/path', () => 'x'));
  const server = (await import('node:http')).createServer(async (req, res) => {
    assert.equal(req.url, '/internal/v1/whatsapp/authorize'); assert.equal(req.headers.authorization, 'Bearer ' + 't'.repeat(40));
    let body = ''; for await (const c of req) body += c; assert.equal(JSON.parse(body).contact_phone, CONTACT);
    res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"authorized":true}');
  }); await new Promise(r => server.listen(0, '127.0.0.1', r)); t.after(() => new Promise(r => server.close(r)));
  const core = new Core('http://127.0.0.1:' + server.address().port, () => tokenFromFile(file));
  assert.deepEqual(await core.call('authorize', { contact_phone: CONTACT }), { authorized: true });
});
test('actual ffmpeg converts WAV to PCM16k mono and Ogg Opus without network', async () => {
  const pcm = Buffer.alloc(3200), wav = Buffer.alloc(44 + pcm.length);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(pcm.length, 40); pcm.copy(wav, 44);
  assert.equal((await decodeAudio(wav)).length, 3200); assert.equal((await encodeVoice(wav)).subarray(0, 4).toString(), 'OggS');
  await assert.rejects(decodeAudio(Buffer.from('invalid audio')), /audio_conversion_failed/);
});
test('dedicated production console suppresses SDK session objects on stdout and stderr', () => {
  const moduleUrl = new URL('../server.mjs', import.meta.url).href;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `import {silenceSdkConsole} from ${JSON.stringify(moduleUrl)}; silenceSdkConsole();
     console.info('Closing session:',{privateKey:'synthetic-secret'});
     console.error('synthetic-private-error'); console.warn('synthetic-private-warning');
     console.dir({qr:'synthetic-private-qr'}); process.stdout.write('safe-marker');`], { encoding: 'utf8' });
  assert.equal(result.status, 0); assert.equal(result.stdout, 'safe-marker');
  assert.doesNotMatch(result.stderr, /synthetic-private|synthetic-secret|Closing session/);
});
