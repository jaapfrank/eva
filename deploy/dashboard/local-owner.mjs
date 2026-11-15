import http from 'node:http';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const VOICE_PREFIX = '/internal/v1/dashboard/holostage/voice';
const LOOPBACK_REMOTES = new Set(['127.0.0.1', '::ffff:127.0.0.1', '::1']);
const SHELL_NAVIGATION_PATHS = new Set([
  '/',
  '/internal/v1/dashboard',
  '/internal/v1/dashboard/',
  '/internal/v1/dashboard/holostage',
  '/internal/v1/dashboard/holostage/',
]);
const EMBEDDED_DESTINATIONS = new Set(['iframe', 'frame', 'embed', 'object', 'fencedframe']);

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
  let server;

  function allowedHost(rawHost) {
    if (typeof rawHost !== 'string') return null;
    const normalized = rawHost.trim().toLowerCase();
    if (!normalized) return null;
    const addr = server?.listening ? server.address() : null;
    const boundPort = addr && typeof addr === 'object' ? addr.port : null;
    const acceptedPorts = port === 0 ? (boundPort ? [0, boundPort] : [0]) : [port];
    for (const p of acceptedPorts) {
      if (normalized === `127.0.0.1:${p}` || normalized === `localhost:${p}` || normalized === `[::1]:${p}`) {
        return normalized;
      }
    }
    return null;
  }

  function parseSafeUrl(req, host) {
    if (
      typeof req.url !== 'string' ||
      !req.url.startsWith('/') ||
      req.url.startsWith('//') ||
      req.url.includes('..') ||
      req.url.includes('\\')
    ) {
      return null;
    }
    const rawPath = req.url.split('?', 1)[0];
    let decodedRawPath;
    try {
      decodedRawPath = decodeURIComponent(rawPath);
    } catch {
      return null;
    }
    if (decodedRawPath.includes('..') || decodedRawPath.includes('\\') || decodedRawPath.includes('\0')) {
      return null;
    }
    let urlObj;
    try {
      urlObj = new URL(req.url, `http://${host}`);
    } catch {
      return null;
    }
    if (urlObj.host.toLowerCase() !== host) return null;
    return urlObj;
  }

  function permitted(req, host, urlObj) {
    if (!LOOPBACK_REMOTES.has(req.socket.remoteAddress)) return false;
    if (!host || !urlObj) return false;
    const path = urlObj.pathname;
    const expectedOrigin = `http://${host}`;
    const rawOrigin = req.headers.origin;
    if (Array.isArray(rawOrigin)) return false;
    const normalizedOrigin = typeof rawOrigin === 'string' ? rawOrigin.trim().toLowerCase() : '';
    if (rawOrigin !== undefined && !normalizedOrigin) return false;

    const voiceSub = resolveVoicePath(req.method, urlObj);
    if (voiceSub === false) return false;
    if (voiceSub !== null && req.method === 'POST' && normalizedOrigin !== expectedOrigin) {
      return false;
    }
    // Only this GET returns an issued, one-time package authorization state.
    // The package host validates that state before forwarding any callback.
    const callback = req.method === 'GET' && /^\/internal\/v1\/dashboard\/owned-packages\/[0-9a-f]{64}\/callback$/.test(path);
    if (normalizedOrigin && normalizedOrigin !== expectedOrigin && !callback) return false;

    const rawSecSite = req.headers['sec-fetch-site'];
    const rawSecMode = req.headers['sec-fetch-mode'];
    const rawSecDest = req.headers['sec-fetch-dest'];
    if (Array.isArray(rawSecSite) || Array.isArray(rawSecMode) || Array.isArray(rawSecDest)) return false;
    const secFetchSite = typeof rawSecSite === 'string' ? rawSecSite.trim().toLowerCase() : '';
    const secFetchMode = typeof rawSecMode === 'string' ? rawSecMode.trim().toLowerCase() : '';
    const secFetchDest = typeof rawSecDest === 'string' ? rawSecDest.trim().toLowerCase() : '';

    if (EMBEDDED_DESTINATIONS.has(secFetchDest) && secFetchSite !== '' && secFetchSite !== 'same-origin') {
      return false;
    }

    const isDashboardPath =
      ((req.method === 'GET' || req.method === 'HEAD') && path === '/') ||
      path === '/internal/v1/dashboard' ||
      path.startsWith('/internal/v1/dashboard/');
    const isTopLevelGetDocument =
      req.method === 'GET' &&
      rawOrigin === undefined &&
      !req.headers.upgrade &&
      secFetchMode === 'navigate' &&
      secFetchDest === 'document';

    if (secFetchSite === 'cross-site') {
      if (!callback && !(isTopLevelGetDocument && SHELL_NAVIGATION_PATHS.has(path))) return false;
    } else if (secFetchSite === 'same-site') {
      if (!callback && !(isTopLevelGetDocument && isDashboardPath)) return false;
    } else if (secFetchSite !== '' && secFetchSite !== 'none' && secFetchSite !== 'same-origin') {
      return false;
    }

    return isDashboardPath;
  }

  function options(req, urlObj) {
    const voiceSub = resolveVoicePath(req.method, urlObj);
    const targetPort = typeof voiceSub === 'string' ? voiceUpstreamPort : upstreamPort;
    const targetPath = typeof voiceSub === 'string' ? voiceSub : `${urlObj.pathname}${urlObj.search}`;
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

  server = http.createServer((req, res) => {
    const host = allowedHost(req.headers.host);
    const urlObj = host ? parseSafeUrl(req, host) : null;
    if (!permitted(req, host, urlObj)) {
      res.writeHead(403, { 'cache-control': 'no-store', 'content-type': 'text/plain; charset=utf-8' })
        .end('Lokale dashboardtoegang vereist.');
      return;
    }
    if (urlObj.pathname === '/') {
      res.writeHead(302, { location: '/internal/v1/dashboard', 'cache-control': 'no-store' }).end();
      return;
    }
    if (
      (req.method === 'GET' || req.method === 'HEAD') &&
      (urlObj.pathname === '/internal/v1/dashboard/' || urlObj.pathname === '/internal/v1/dashboard/holostage/')
    ) {
      res.writeHead(302, { location: `${urlObj.pathname.slice(0, -1)}${urlObj.search}`, 'cache-control': 'no-store' }).end();
      return;
    }
    const reqOpts = options(req, urlObj);
    const upstream = http.request(reqOpts, response => {
      const headers = { ...response.headers, 'cache-control': 'no-store' };
      delete headers['www-authenticate'];
      delete headers['set-cookie'];
      if (typeof headers.location === 'string') {
        for (const upPrefix of [`http://localhost:${reqOpts.port}/`, `http://127.0.0.1:${reqOpts.port}/`]) {
          if (headers.location.startsWith(upPrefix)) {
            const rest = headers.location.slice(upPrefix.length).replace(/^\/+/, '');
            headers.location = `/${rest}`;
            break;
          }
        }
      }
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
    const host = allowedHost(req.headers.host);
    const urlObj = host ? parseSafeUrl(req, host) : null;
    if (!urlObj || urlObj.pathname.startsWith(`${VOICE_PREFIX}/`) || !permitted(req, host, urlObj) || !req.headers.origin) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    const upstream = http.request(options(req, urlObj));
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
