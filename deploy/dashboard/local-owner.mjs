import http from 'node:http';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const VOICE_PREFIX = '/internal/v1/dashboard/holostage/voice';

function resolveVoicePath(method, urlObj) {
  if (!urlObj.pathname.startsWith(`${VOICE_PREFIX}/`)) return null;
  if (urlObj.search) return false;
  const sub = urlObj.pathname.slice(VOICE_PREFIX.length);
  if (method === 'GET' && (sub === '/livekit-client.js' || sub === '/session/ready')) return sub;
  if (
    method === 'POST' &&
    (sub === '/session' ||
      sub === '/session/leased' ||
      sub === '/session/end' ||
      /^\/session\/(?:end|renew)\/[A-Za-z0-9_-]{1,96}$/.test(sub))
  ) {
    return sub;
  }
  return false;
}

export function createLocalOwner({ token, port = 44711, upstreamPort = 44710, voiceUpstreamPort = 44716 }) {
  const hosts = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
  function permitted(req) {
    if (!['127.0.0.1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress)) return false;
    if (!hosts.has(req.headers.host)) return false;
    if (typeof req.url !== 'string' || req.url.includes('..')) return false;
    let urlObj;
    try { urlObj = new URL(req.url, `http://${req.headers.host}`); } catch { return false; }
    const path = urlObj.pathname;
    const voiceSub = resolveVoicePath(req.method, urlObj);
    if (voiceSub === false) return false;
    if (voiceSub !== null && req.method === 'POST' && req.headers.origin !== `http://${req.headers.host}`) {
      return false;
    }
    // Only this GET returns an issued, one-time package authorization state.
    // The package host validates that state before forwarding any callback.
    const callback = req.method === 'GET' && /^\/internal\/v1\/dashboard\/owned-packages\/[0-9a-f]{64}\/callback$/.test(path);
    if (req.headers.origin && req.headers.origin !== `http://${req.headers.host}` && !callback) return false;
    if (req.headers['sec-fetch-site'] === 'cross-site' && !callback) return false;
    return path === '/' || path === '/internal/v1/dashboard' || path.startsWith('/internal/v1/dashboard/');
  }
  function options(req) {
    const urlObj = new URL(req.url, `http://${req.headers.host}`);
    const voiceSub = resolveVoicePath(req.method, urlObj);
    const targetPort = typeof voiceSub === 'string' ? voiceUpstreamPort : upstreamPort;
    const targetPath = typeof voiceSub === 'string' ? voiceSub : req.url;
    const headers = { ...req.headers, host: `localhost:${targetPort}`, authorization: `Bearer ${token}` };
    delete headers.cookie;
    delete headers['proxy-authorization'];
    delete headers['forwarded'];
    for (const key of Object.keys(headers)) if (key.startsWith('x-forwarded-')) delete headers[key];
    if (headers.origin || (typeof voiceSub === 'string' && req.method === 'POST')) {
      headers.origin = `http://localhost:${targetPort}`;
    }
    return { hostname: '127.0.0.1', port: targetPort, path: targetPath, method: req.method, headers };
  }
  const server = http.createServer((req, res) => {
    if (!permitted(req)) { res.writeHead(403).end('Lokale dashboardtoegang vereist.'); return; }
    if (req.url === '/') { res.writeHead(302, { location: '/internal/v1/dashboard' }).end(); return; }
    const upstream = http.request(options(req), response => {
      const headers = { ...response.headers, 'cache-control': 'no-store' };
      delete headers['www-authenticate'];
      delete headers['set-cookie'];
      res.writeHead(response.statusCode, headers);
      res.flushHeaders();
      response.pipe(res);
    });
    upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end('Eva is tijdelijk niet bereikbaar.'); });
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  });
  server.on('upgrade', (req, socket, head) => {
    let urlObj;
    try { urlObj = new URL(req.url, `http://${req.headers.host}`); } catch { urlObj = null; }
    if (!urlObj || urlObj.pathname.startsWith(`${VOICE_PREFIX}/`) || !permitted(req) || !req.headers.origin) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    const upstream = http.request(options(req));
    upstream.on('upgrade', (response, peer, initial) => {
      socket.write(`HTTP/1.1 101 Switching Protocols\r\n${Object.entries(response.headers).map(([k,v]) => `${k}: ${v}`).join('\r\n')}\r\n\r\n`);
      if (initial.length) socket.write(initial);
      if (head.length) peer.write(head);
      socket.pipe(peer).pipe(socket);
      socket.on('error', () => peer.destroy());
      peer.on('error', () => socket.destroy());
      socket.on('close', () => peer.destroy());
      peer.on('close', () => socket.destroy());
    });
    upstream.on('response', response => { socket.end(`HTTP/1.1 ${response.statusCode} Upstream Rejected\r\nConnection: close\r\n\r\n`); response.resume(); });
    upstream.on('error', () => socket.destroy());
    upstream.end();
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const token = readFileSync(`${process.env.CREDENTIALS_DIRECTORY}/dashboard-token`, 'utf8').trim();
  if (token.length < 32) throw new Error('Dashboard credential unavailable');
  createLocalOwner({ token }).listen(44711, '127.0.0.1');
}
