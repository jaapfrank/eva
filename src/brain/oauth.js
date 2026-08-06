// ChatGPT OAuth-login — dezelfde flow als OpenClaw/Codex CLI:
// PKCE-authorisatie op auth.openai.com met de officiële Codex client-id,
// callback op http://localhost:1455/auth/callback, tokens in data/auth.json.
import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { config } from '../config.js';
import { bus } from '../bus.js';

const CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann';
const AUTHORIZE_URL = 'https://auth.openai.com/oauth/authorize';
const TOKEN_URL = 'https://auth.openai.com/oauth/token';
const REDIRECT_URI = `http://localhost:${config.oauthPort}/auth/callback`;
const AUTH_FILE = path.join(config.dataDir, 'auth.json');

function loadAuth() {
  try {
    return JSON.parse(fs.readFileSync(AUTH_FILE, 'utf8'));
  } catch {
    return null;
  }
}

function saveAuth(auth) {
  fs.mkdirSync(config.dataDir, { recursive: true });
  fs.writeFileSync(AUTH_FILE, JSON.stringify(auth, null, 2));
  fs.chmodSync(AUTH_FILE, 0o600);
}

function decodeJwt(token) {
  try {
    const payload = token.split('.')[1];
    return JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
  } catch {
    return {};
  }
}

function enrich(tokens) {
  const claims = { ...decodeJwt(tokens.access_token), ...decodeJwt(tokens.id_token || '') };
  const authClaim = claims['https://api.openai.com/auth'] || {};
  return {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    id_token: tokens.id_token || null,
    expires_at: Date.now() + (tokens.expires_in || 3600) * 1000,
    account_id: authClaim.chatgpt_account_id || null,
    email: claims.email || null,
    plan: authClaim.chatgpt_plan_type || null,
  };
}

export function authStatus() {
  const auth = loadAuth();
  if (!auth || !auth.access_token) return { status: 'none' };
  if (auth.expires_at > Date.now() + 60_000 || auth.refresh_token) {
    return { status: 'ok', email: auth.email || undefined, plan: auth.plan || undefined };
  }
  return { status: 'expired', email: auth.email || undefined };
}

async function tokenRequest(params) {
  const res = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(params).toString(),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`OAuth token-request mislukt (${res.status}): ${text.slice(0, 300)}`);
  return JSON.parse(text);
}

async function refreshTokens(auth) {
  const tokens = await tokenRequest({
    grant_type: 'refresh_token',
    refresh_token: auth.refresh_token,
    client_id: CLIENT_ID,
    scope: 'openid profile email',
  });
  const updated = enrich({ ...auth, ...tokens, refresh_token: tokens.refresh_token || auth.refresh_token });
  // behoud velden die de refresh-response niet meestuurt
  updated.account_id = updated.account_id || auth.account_id;
  updated.email = updated.email || auth.email;
  updated.id_token = updated.id_token || auth.id_token;
  saveAuth(updated);
  return updated;
}

/** Geef een geldig access-token + account-id; ververst automatisch. */
export async function getAccess({ forceRefresh = false } = {}) {
  let auth = loadAuth();
  if (!auth || !auth.access_token) {
    throw new Error('Niet ingelogd bij ChatGPT. Log in via het dashboard of met: npm run login');
  }
  if (forceRefresh || auth.expires_at < Date.now() + 5 * 60_000) {
    if (!auth.refresh_token) throw new Error('ChatGPT-sessie verlopen en geen refresh-token. Log opnieuw in.');
    try {
      auth = await refreshTokens(auth);
    } catch (e) {
      bus.emit('auth', authStatus());
      throw new Error(`ChatGPT-token verversen mislukt: ${e.message}. Log opnieuw in via het dashboard.`);
    }
  }
  return { accessToken: auth.access_token, accountId: auth.account_id };
}

let loginServer = null;

/** Start de browser-loginflow; geeft de URL terug die de gebruiker moet openen. */
export async function startLoginFlow() {
  const verifier = crypto.randomBytes(64).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  const state = crypto.randomBytes(16).toString('hex');

  const authUrl =
    AUTHORIZE_URL +
    '?' +
    new URLSearchParams({
      response_type: 'code',
      client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI,
      scope: 'openid profile email offline_access',
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
      id_token_add_organizations: 'true',
      codex_cli_simplified_flow: 'true',
      originator: 'codex_cli_rs',
    }).toString();

  if (loginServer) {
    try { loginServer.close(); } catch { /* al dicht */ }
    loginServer = null;
  }

  const done = new Promise((resolve) => {
    const server = http.createServer(async (req, res) => {
      const url = new URL(req.url, `http://localhost:${config.oauthPort}`);
      if (url.pathname !== '/auth/callback') {
        res.writeHead(404).end();
        return;
      }
      const html = (title, body) =>
        `<!doctype html><meta charset="utf-8"><title>${title}</title><body style="font-family:system-ui;background:#1c2230;color:#f3e9dc;display:grid;place-items:center;height:100vh;margin:0"><div style="text-align:center;max-width:30rem"><h1>${title}</h1><p>${body}</p></div>`;
      try {
        if (url.searchParams.get('state') !== state) throw new Error('state komt niet overeen');
        const code = url.searchParams.get('code');
        if (!code) throw new Error(url.searchParams.get('error_description') || 'geen code ontvangen');
        const tokens = await tokenRequest({
          grant_type: 'authorization_code',
          code,
          redirect_uri: REDIRECT_URI,
          client_id: CLIENT_ID,
          code_verifier: verifier,
        });
        saveAuth(enrich(tokens));
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(html('Gelukt! 💚', 'Eva is ingelogd bij ChatGPT. Je kunt dit tabblad sluiten — ze gaat zo aan de slag.'));
        bus.emit('auth', authStatus());
        resolve(true);
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(html('Hm, dat lukte niet', `Inloggen mislukt: ${e.message}. Probeer het opnieuw vanaf het dashboard.`));
        resolve(false);
      } finally {
        setTimeout(() => {
          try { server.close(); } catch { /* al dicht */ }
          if (loginServer === server) loginServer = null;
        }, 500);
      }
    });
    server.listen(config.oauthPort);
    server.on('error', () => resolve(false));
    loginServer = server;
    // na 10 minuten opgeven
    setTimeout(() => {
      try { server.close(); } catch { /* al dicht */ }
      if (loginServer === server) loginServer = null;
      resolve(false);
    }, 10 * 60_000).unref();
  });

  startLoginFlow.lastPromise = done;
  return { authUrl };
}

// CLI: `npm run login` — print de URL en wacht op de callback.
if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1].replace(/\\/g, '/')}`).href) {
  const { authUrl } = await startLoginFlow();
  console.log('\nOpen deze link in je browser om Eva in te loggen bij ChatGPT:\n');
  console.log(authUrl + '\n');
  const ok = await startLoginFlow.lastPromise;
  const st = authStatus();
  console.log(ok && st.status === 'ok' ? `Ingelogd als ${st.email || 'onbekend'} (${st.plan || 'ChatGPT'}) ✔` : 'Inloggen niet gelukt.');
  process.exit(ok ? 0 : 1);
}
