import http from 'node:http';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const STAGE_COOKIE_NAME = 'eva_holostage_session';
const STAGE_TOKEN_RE = /^stg_[A-Za-z0-9_-]{40,48}$/;
const VOICE_PREFIX = '/internal/v1/dashboard/holostage/voice';
const TAILSCALE_HOST_RE = /^[a-z0-9-]+(?:\.[a-z0-9-]+)+\.ts\.net(?::(?:443|8443|10000))?$/i;
const LIVEKIT_JWT_RE = /^[A-Za-z0-9_-]{8,1024}\.[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{8,1024}$/;

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

function allowedStagePath(method, pathname) {
  if (method === 'GET') {
    return (
      pathname === '/' ||
      pathname === '/internal/v1/dashboard/holostage' ||
      pathname === '/internal/v1/dashboard/holostage/manifest.json' ||
      pathname === '/internal/v1/dashboard/holostage/state' ||
      /^\/internal\/v1\/dashboard\/holostage\/assets\/[A-Za-z0-9._-]{1,64}$/.test(pathname)
    );
  }
  if (method === 'POST') {
    return (
      pathname === '/internal/v1/dashboard/holostage/session' ||
      pathname === '/internal/v1/dashboard/holostage/action' ||
      pathname === '/internal/v1/dashboard/holostage/turn-stream' ||
      pathname === '/internal/v1/dashboard/holostage/vision-turn' ||
      pathname === '/internal/v1/dashboard/holostage/speech-audio'
    );
  }
  return false;
}

function extractScopedStageCookie(rawCookie) {
  if (typeof rawCookie !== 'string' || !rawCookie) return null;
  for (const part of rawCookie.split(';')) {
    const eq = part.indexOf('=');
    if (eq <= 0) continue;
    const name = part.slice(0, eq).trim();
    const val = part.slice(eq + 1).trim();
    if (name === STAGE_COOKIE_NAME && STAGE_TOKEN_RE.test(val)) {
      return `${STAGE_COOKIE_NAME}=${val}`;
    }
  }
  return null;
}

function isRtcValidateRequest(method, urlObj) {
  if (method !== 'GET') return false;
  if (urlObj.pathname !== '/rtc/validate' && urlObj.pathname !== '/rtc/v1/validate') return false;
  const rtcToken = urlObj.searchParams.get('access_token') || '';
  return LIVEKIT_JWT_RE.test(rtcToken);
}

export function createHoloStageLanProxy({
  bindHost = '10.0.20.46',
  lanHost = '10.0.20.46',
  port = 44712,
  upstreamPort = 44710,
  voiceUpstreamPort = 44716,
  livekitUpstreamPort = 7880,
  voiceToken = null,
  allowedSubnetPrefix = '10.0.20.',
  publicHost = null,
  publicOriginFile = null,
} = {}) {
  const hosts = new Set([
    `${lanHost}:${port}`,
    `${bindHost}:${port}`,
    `127.0.0.1:${port}`,
    `localhost:${port}`,
  ]);

  function resolvePinnedPublicHost() {
    if (typeof publicHost === 'string' && publicHost.trim()) {
      return publicHost.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/$/, '');
    }
    if (typeof publicOriginFile === 'string' && publicOriginFile.trim()) {
      try {
        const raw = readFileSync(publicOriginFile, 'utf8').trim().toLowerCase();
        if (raw.startsWith('https://')) {
          const parsed = new URL(raw);
          if (parsed.host && TAILSCALE_HOST_RE.test(parsed.host)) return parsed.host;
        }
      } catch { /* file not yet written */ }
    }
    return null;
  }

  function normalizeRemote(req) {
    return (req.socket.remoteAddress || '').replace(/^::ffff:/, '');
  }

  function isLoopbackRemote(remote) {
    return remote === '127.0.0.1' || remote === '::1';
  }

  function isAllowedHost(req, remote) {
    const host = (req.headers.host || '').toLowerCase();
    if (hosts.has(host)) return true;
    if (isLoopbackRemote(remote) && TAILSCALE_HOST_RE.test(host)) {
      const pinned = resolvePinnedPublicHost();
      if (!pinned) return true;
      const bareHost = host.split(':')[0];
      const barePinned = pinned.split(':')[0];
      return bareHost === barePinned;
    }
    return false;
  }

  function isHttpsRequest(req, remote) {
    if (isLoopbackRemote(remote)) {
      const xfProto = String(req.headers['x-forwarded-proto'] || '').toLowerCase();
      if (xfProto === 'https' || TAILSCALE_HOST_RE.test(req.headers.host || '')) return true;
    }
    return String(req.headers.origin || '').startsWith('https://');
  }

  function isAllowedOrigin(req) {
    const origin = req.headers.origin;
    if (!origin) return true;
    const rawHost = String(req.headers.host || '').toLowerCase();
    const bareHttpsHost = rawHost.endsWith(':443') ? rawHost.slice(0, -4) : rawHost;
    const bareHttpHost = rawHost.endsWith(':80') ? rawHost.slice(0, -3) : rawHost;
    const lowerOrigin = origin.toLowerCase();
    return (
      lowerOrigin === `http://${rawHost}` ||
      lowerOrigin === `http://${bareHttpHost}` ||
      lowerOrigin === `https://${rawHost}` ||
      lowerOrigin === `https://${bareHttpsHost}`
    );
  }

  function permitted(req) {
    const remote = normalizeRemote(req);
    if (!isLoopbackRemote(remote) && !remote.startsWith(allowedSubnetPrefix)) return false;
    if (!isAllowedHost(req, remote)) return false;
    if (typeof req.url !== 'string' || req.url.includes('..')) return false;
    let urlObj;
    try { urlObj = new URL(req.url, `http://${req.headers.host}`); } catch { return false; }
    const voiceSub = resolveVoicePath(req.method, urlObj);
    if (voiceSub === false) return false;
    if (voiceSub !== null && (!voiceToken || voiceToken.length < 32)) return false;
    const isRtcVal = voiceSub === null && isRtcValidateRequest(req.method, urlObj);
    if (voiceSub === null && !isRtcVal && !allowedStagePath(req.method, urlObj.pathname)) return false;
    if (req.method === 'POST' && urlObj.search) return false;
    if (voiceSub !== null && req.method === 'POST' && !req.headers.origin) return false;
    if (!isAllowedOrigin(req)) return false;
    if (req.headers['sec-fetch-site'] === 'cross-site') return false;
    return true;
  }

  function hasScopedStageCredential(req) {
    const headerTok = typeof req.headers['x-holostage-token'] === 'string'
      ? req.headers['x-holostage-token'].trim()
      : '';
    return Boolean(extractScopedStageCookie(req.headers.cookie) || STAGE_TOKEN_RE.test(headerTok));
  }

  function isBootstrapStageRoute(method, pathname) {
    return (
      (method === 'GET' && (pathname === '/' || pathname === '/internal/v1/dashboard/holostage')) ||
      (method === 'POST' && pathname === '/internal/v1/dashboard/holostage/session')
    );
  }

  function coreOptions(req) {
    const remote = normalizeRemote(req);
    const headers = { ...req.headers };
    // Never expose or accept owner Authorization headers on the LAN/Funnel stage port.
    delete headers.authorization;
    delete headers['proxy-authorization'];
    delete headers.forwarded;
    const httpsReq = isHttpsRequest(req, remote);
    for (const key of Object.keys(headers)) if (key.startsWith('x-forwarded-')) delete headers[key];
    headers['x-holostage-lan-proxy'] = '1';
    if (httpsReq) headers['x-holostage-https'] = '1';
    else delete headers['x-holostage-https'];
    const cleanCookie = extractScopedStageCookie(req.headers.cookie);
    if (cleanCookie) headers.cookie = cleanCookie;
    else delete headers.cookie;
    return { hostname: '127.0.0.1', port: upstreamPort, path: req.url, method: req.method, headers };
  }

  function verifyPrivateStageVoiceGrant(req) {
    return new Promise(resolve => {
      const checkHeaders = {
        host: req.headers.host || `127.0.0.1:${port}`,
        'x-holostage-lan-proxy': '1',
      };
      const cleanCookie = extractScopedStageCookie(req.headers.cookie);
      if (cleanCookie) checkHeaders.cookie = cleanCookie;
      const headerTok = typeof req.headers['x-holostage-token'] === 'string'
        ? req.headers['x-holostage-token'].trim()
        : '';
      if (STAGE_TOKEN_RE.test(headerTok)) checkHeaders['x-holostage-token'] = headerTok;

      const checkReq = http.request(
        {
          hostname: '127.0.0.1',
          port: upstreamPort,
          path: '/internal/v1/dashboard/holostage/voice-grant-check',
          method: 'GET',
          headers: checkHeaders,
          timeout: 4000,
        },
        checkRes => {
          const status = checkRes.statusCode || 502;
          if (status === 200) {
            checkRes.resume();
            resolve({ allowed: true, status: 200, detail: null });
            return;
          }
          const chunks = [];
          let total = 0;
          checkRes.on('data', chunk => {
            total += chunk.length;
            if (total <= 4096) chunks.push(chunk);
          });
          checkRes.on('end', () => {
            let detail = null;
            try {
              const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
              if (parsed && typeof parsed.detail === 'string' && parsed.detail.trim()) {
                detail = parsed.detail.trim().slice(0, 280);
              }
            } catch { /* fallback detail below */ }
            resolve({ allowed: false, status, detail });
          });
        }
      );
      checkReq.on('timeout', () => {
        checkReq.destroy();
        resolve({ allowed: false, status: 503, detail: 'Eva HoloStage spraakcontrole is tijdelijk niet bereikbaar.' });
      });
      checkReq.on('error', () => {
        resolve({ allowed: false, status: 503, detail: 'Eva HoloStage spraakcontrole is tijdelijk niet bereikbaar.' });
      });
      checkReq.end();
    });
  }

  function proxyVoiceRequest(req, res, voiceSub) {
    if (!voiceToken || voiceToken.length < 32) {
      res.writeHead(503, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
        .end(JSON.stringify({ detail: 'Spraakbrug is niet geconfigureerd op deze poort.' }));
      return;
    }
    const remote = normalizeRemote(req);
    const httpsReq = isHttpsRequest(req, remote);
    const vHeaders = {
      ...req.headers,
      host: `localhost:${voiceUpstreamPort}`,
      authorization: `Bearer ${voiceToken}`,
    };
    delete vHeaders.cookie;
    delete vHeaders['x-holostage-token'];
    delete vHeaders['proxy-authorization'];
    delete vHeaders.forwarded;
    for (const key of Object.keys(vHeaders)) if (key.startsWith('x-forwarded-')) delete vHeaders[key];
    if (vHeaders.origin || req.method === 'POST') {
      vHeaders.origin = `http://localhost:${voiceUpstreamPort}`;
    }

    const isSessionStart = req.method === 'POST' && (voiceSub === '/session' || voiceSub === '/session/leased');
    const upstream = http.request(
      {
        hostname: '127.0.0.1',
        port: voiceUpstreamPort,
        path: voiceSub,
        method: req.method,
        headers: vHeaders,
      },
      response => {
        const headers = { ...response.headers, 'cache-control': 'no-store' };
        delete headers['www-authenticate'];
        delete headers['set-cookie'];
        if (isSessionStart && response.statusCode === 200) {
          const chunks = [];
          let total = 0;
          response.on('data', chunk => {
            total += chunk.length;
            if (total <= 65536) chunks.push(chunk);
          });
          response.on('end', () => {
            try {
              const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
              const wsScheme = httpsReq ? 'wss' : 'ws';
              parsed.url = `${wsScheme}://${req.headers.host}`;
              const out = Buffer.from(JSON.stringify(parsed), 'utf8');
              delete headers['content-length'];
              delete headers['transfer-encoding'];
              headers['content-type'] = 'application/json; charset=utf-8';
              headers['content-length'] = String(out.length);
              res.writeHead(200, headers);
              res.end(out);
            } catch {
              res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' })
                .end(JSON.stringify({ detail: 'Ongeldig antwoord van spraakserver.' }));
            }
          });
          return;
        }
        res.writeHead(response.statusCode, headers);
        res.flushHeaders();
        response.pipe(res);
      }
    );
    upstream.on('error', () => {
      if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ detail: 'Eva spraakservice is tijdelijk niet bereikbaar.' }));
    });
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  }

  const server = http.createServer(async (req, res) => {
    if (!permitted(req)) {
      res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' })
        .end('Alleen gekoppelde HoloStage-schermen zijn toegestaan.');
      return;
    }
    const urlObj = new URL(req.url, `http://${req.headers.host}`);
    const voiceSub = resolveVoicePath(req.method, urlObj);

    if (isRtcValidateRequest(req.method, urlObj)) {
      const rtcHeaders = { accept: req.headers.accept || '*/*', host: `127.0.0.1:${livekitUpstreamPort}` };
      const upVal = http.request({
        hostname: '127.0.0.1',
        port: livekitUpstreamPort,
        path: req.url,
        method: 'GET',
        headers: rtcHeaders,
      }, upRes => {
        const hdrs = { ...upRes.headers };
        delete hdrs['set-cookie'];
        res.writeHead(upRes.statusCode || 200, hdrs);
        upRes.pipe(res);
      });
      upVal.on('error', () => {
        if (!res.headersSent) res.writeHead(502);
        res.end('LiveKit signalering niet bereikbaar.');
      });
      req.on('aborted', () => upVal.destroy());
      upVal.end();
      return;
    }

    if (!isBootstrapStageRoute(req.method, urlObj.pathname) && !hasScopedStageCredential(req)) {
      res.writeHead(401, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
        .end(JSON.stringify({ detail: 'Geldige HoloStage-schermkoppeling vereist.' }));
      return;
    }
    if (req.url === '/') {
      const remote = normalizeRemote(req);
      const defaultMode = TAILSCALE_HOST_RE.test(req.headers.host || '') && isLoopbackRemote(remote)
        ? 'pocket'
        : 'tv_cast';
      res.writeHead(302, { location: `/internal/v1/dashboard/holostage?mode=${defaultMode}` }).end();
      return;
    }

    if (typeof voiceSub === 'string') {
      // Pause incoming body stream while verifying private_stage grant on Core
      req.pause();
      const grantCheck = await verifyPrivateStageVoiceGrant(req);
      if (!grantCheck.allowed) {
        req.resume();
        const status = grantCheck.status === 401 || grantCheck.status === 403 || grantCheck.status === 503
          ? grantCheck.status
          : 403;
        res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
          .end(JSON.stringify({
            detail: grantCheck.detail || 'Tweerichtingsspraak is alleen toegestaan op gekoppelde privé-gezinsapparaten.',
          }));
        return;
      }
      proxyVoiceRequest(req, res, voiceSub);
      req.resume();
      return;
    }

    const upstream = http.request(coreOptions(req), response => {
      const headers = { ...response.headers };
      delete headers['www-authenticate'];
      const isSessionExchange = req.method === 'POST' && urlObj.pathname === '/internal/v1/dashboard/holostage/session';
      if (!isSessionExchange) delete headers['set-cookie'];
      res.writeHead(response.statusCode, headers);
      res.flushHeaders();
      response.pipe(res);
    });
    upstream.on('error', () => {
      if (!res.headersSent) res.writeHead(502);
      res.end('Eva HoloStage is tijdelijk niet bereikbaar.');
    });
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  });

  server.on('upgrade', (req, socket, head) => {
    const remote = normalizeRemote(req);
    if (!isLoopbackRemote(remote) && !remote.startsWith(allowedSubnetPrefix)) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    if (!isAllowedHost(req, remote) || !req.headers.origin || !isAllowedOrigin(req)) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    let urlObj;
    try { urlObj = new URL(req.url, `http://${req.headers.host}`); } catch { urlObj = null; }
    const rtcToken = urlObj ? (urlObj.searchParams.get('access_token') || '') : '';
    if (
      !urlObj ||
      (urlObj.pathname !== '/rtc' && urlObj.pathname !== '/rtc/v1') ||
      !LIVEKIT_JWT_RE.test(rtcToken)
    ) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    const wsHeaders = { ...req.headers, host: `127.0.0.1:${livekitUpstreamPort}` };
    delete wsHeaders.cookie;
    delete wsHeaders['x-holostage-token'];
    delete wsHeaders.forwarded;
    for (const key of Object.keys(wsHeaders)) if (key.startsWith('x-forwarded-')) delete wsHeaders[key];

    const upstream = http.request({
      hostname: '127.0.0.1',
      port: livekitUpstreamPort,
      path: req.url,
      method: 'GET',
      headers: wsHeaders,
    });
    upstream.on('upgrade', (response, peer, initial) => {
      socket.write(
        `HTTP/1.1 101 Switching Protocols\r\n${Object.entries(response.headers).map(([k, v]) => `${k}: ${v}`).join('\r\n')}\r\n\r\n`
      );
      if (initial.length) socket.write(initial);
      if (head.length) peer.write(head);
      socket.pipe(peer).pipe(socket);
      socket.on('error', () => peer.destroy());
      peer.on('error', () => socket.destroy());
      socket.on('close', () => peer.destroy());
      peer.on('close', () => socket.destroy());
    });
    upstream.on('response', response => {
      socket.end(`HTTP/1.1 ${response.statusCode} Upstream Rejected\r\nConnection: close\r\n\r\n`);
      response.resume();
    });
    upstream.on('error', () => socket.destroy());
    upstream.end();
  });

  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const bindHost = process.env.EVA_HOLOSTAGE_BIND_HOST || process.env.EVA_HOLOSTAGE_LAN_HOST || '10.0.20.46';
  const lanHost = process.env.EVA_HOLOSTAGE_LAN_HOST || '10.0.20.46';
  const port = Number(process.env.EVA_HOLOSTAGE_LAN_PORT || '44712');
  const funnelPort = Number(process.env.EVA_HOLOSTAGE_FUNNEL_PORT || '44714');
  let voiceToken = null;
  if (process.env.CREDENTIALS_DIRECTORY) {
    try {
      const tok = readFileSync(`${process.env.CREDENTIALS_DIRECTORY}/dashboard-token`, 'utf8').trim();
      if (tok.length >= 32) voiceToken = tok;
    } catch { /* voice bridge stays disabled if credential absent */ }
  }
  const publicHost = process.env.EVA_HOLOSTAGE_PUBLIC_HOST || null;
  const publicOriginFile =
    process.env.EVA_HOLOSTAGE_PUBLIC_ORIGIN_FILE || '/run/eva2-holostage-public-origin';
  createHoloStageLanProxy({ bindHost, lanHost, port, voiceToken, publicHost, publicOriginFile }).listen(port, bindHost);
  if (funnelPort && (funnelPort !== port || bindHost !== '127.0.0.1')) {
    createHoloStageLanProxy({
      bindHost: '127.0.0.1',
      lanHost,
      port: funnelPort,
      voiceToken,
      publicHost,
      publicOriginFile,
    }).listen(funnelPort, '127.0.0.1');
  }
}
