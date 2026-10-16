import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { Console } from 'node:console';
import { Writable } from 'node:stream';
import { Store } from './store.mjs';
import { Core, tokenFromFile } from './core.mjs';
import { Bridge } from './bridge.mjs';

export function silenceSdkConsole() {
  // libsignal prints complete session objects through global console, outside
  // Baileys' configured logger. This dedicated process must never log those.
  const sink = new Writable({ write(_chunk, _encoding, done) { done(); } });
  globalThis.console = new Console({ stdout: sink, stderr: sink });
}

export function controlServer(bridge, token) {
  return http.createServer(async (req, res) => {
    const reply = (status, value) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }); res.end(JSON.stringify(value)); };
    try {
      const expected = Buffer.from('Bearer ' + token()), provided = Buffer.from(req.headers.authorization || '');
      if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) return reply(401, { error: 'authentication_required' });
      const url = new URL(req.url, 'http://bridge');
      if (url.search) return reply(422, { error: 'scope_is_server_owned' });
      if (req.method === 'GET' && url.pathname === '/status') return reply(200, bridge.status());
      if (req.method !== 'POST' || !['/configure', '/enable', '/disable', '/logout'].includes(url.pathname)) return reply(404, { error: 'not_found' });
      if (req.headers['content-type']?.split(';')[0] !== 'application/json') return reply(415, { error: 'json_required' });
      let size = 0; const chunks = [];
      for await (const chunk of req) { size += chunk.length; if (size > 2048) return reply(413, { error: 'request_too_large' }); chunks.push(chunk); }
      let payload;
      try { payload = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { return reply(422, { error: 'invalid_request' }); }
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return reply(422, { error: 'invalid_request' });
      if (url.pathname !== '/configure' && Object.keys(payload).length) return reply(422, { error: 'invalid_request' });
      // Token rotation while body intake was waiting invalidates this operation.
      if ('Bearer ' + token() !== req.headers.authorization) return reply(401, { error: 'authentication_required' });
      const value = await bridge[url.pathname.slice(1)](payload); reply(200, value);
    } catch (error) { reply(error.message === 'invalid_configuration' || error.message === 'contact_required' ? 422 : 503, { error: 'operation_not_confirmed' }); }
  });
}

export async function main() {
  process.umask(0o077);
  silenceSdkConsole();
  const sdk = await import('@whiskeysockets/baileys');
  const { default: pino } = await import('pino');
  const { default: qrcode } = await import('qrcode');
  const token = () => tokenFromFile(process.env.EVA_WHATSAPP_CONTROL_TOKEN_FILE || '/run/secrets/dashboard_access_token');
  token();
  const store = new Store(process.env.EVA_WHATSAPP_DATA_DIRECTORY || '/var/lib/eva-whatsapp', sdk.BufferJSON);
  const core = new Core(process.env.EVA_WHATSAPP_CORE_URL || 'http://core:8080', token);
  const bridge = new Bridge(store, core, { ...sdk, makeSocket: sdk.default }, {
    logger: pino({ level: 'silent' }), qr: value => qrcode.toDataURL(value, { width: 280, errorCorrectionLevel: 'M' }),
  });
  const server = controlServer(bridge, token);
  server.requestTimeout = 15000; server.headersTimeout = 10000;
  await new Promise(resolve => server.listen(Number(process.env.PORT || 8080), '0.0.0.0', resolve));
  try { await bridge.start(); } catch { bridge.connection = 'disconnected'; bridge.lastError = 'startup_connection_unavailable'; }
  const timer = setInterval(() => { void bridge.pollReturns(); }, 3000);
  let stopping = false;
  const stop = async () => {
    if (stopping) return; stopping = true; clearInterval(timer); bridge.stopSocket();
    await new Promise(resolve => server.close(resolve));
    await Promise.allSettled([...bridge.active]); store.close();
  };
  process.once('SIGTERM', () => { void stop(); }); process.once('SIGINT', () => { void stop(); });
  return { server, bridge, store, stop };
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { process.stderr.write('WhatsApp bridge could not start.\n'); process.exitCode = 1; });
}
