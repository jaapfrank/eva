import http from 'node:http';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export function createLocalOwner({ token, port = 44711, upstreamPort = 44710 }) {
  const hosts = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
  function permitted(req) {
    if (!['127.0.0.1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress)) return false;
    if (!hosts.has(req.headers.host)) return false;
    if (req.headers.origin && req.headers.origin !== `http://${req.headers.host}`) return false;
    if (req.headers['sec-fetch-site'] === 'cross-site') return false;
    let path;
    try { path = new URL(req.url, `http://${req.headers.host}`).pathname; } catch { return false; }
    return path === '/' || path === '/internal/v1/dashboard' || path.startsWith('/internal/v1/dashboard/');
  }
  function options(req) {
    const headers = { ...req.headers, host: `localhost:${upstreamPort}`, authorization: `Bearer ${token}` };
    delete headers.cookie;
    delete headers['proxy-authorization'];
    delete headers['forwarded'];
    for (const key of Object.keys(headers)) if (key.startsWith('x-forwarded-')) delete headers[key];
    if (headers.origin) headers.origin = `http://localhost:${upstreamPort}`;
    return { hostname: '127.0.0.1', port: upstreamPort, path: req.url, method: req.method, headers };
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
    if (!permitted(req) || !req.headers.origin) { socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return; }
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
