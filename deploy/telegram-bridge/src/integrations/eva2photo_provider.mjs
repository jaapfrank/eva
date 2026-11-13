/**
 * Pure Eva2 Telegram photo perception provider (zero legacy Bottie/Eva1 dependencies, zero SQLite).
 * Uses Eva2's own OAuth credential file (`EVA_MODEL_OAUTH_FILE`) or `OPENAI_API_KEY` to describe
 * incoming Telegram images before passing grounded text observations to Eva2 Core.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann';
const TOKEN_URL = 'https://auth.openai.com/oauth/token';
const CHATGPT_RESPONSES_URL = 'https://chatgpt.com/backend-api/codex/responses';
const API_RESPONSES_URL = 'https://api.openai.com/v1/responses';
const SESSION_ID = crypto.randomUUID();
const CREDENTIAL_RE = /^[\x21-\x7E]+$/u;

export function imageMsg(text, dataUrl) {
  return {
    type: 'message',
    role: 'user',
    content: [
      { type: 'input_text', text: String(text || 'Beschrijf de aangeleverde foto.') },
      { type: 'input_image', image_url: String(dataUrl || ''), detail: 'auto' },
    ],
  };
}

function decodeJwtPayload(token) {
  try {
    const part = String(token || '').split('.')[1];
    if (!part) return {};
    return JSON.parse(Buffer.from(part, 'base64url').toString('utf8'));
  } catch {
    return {};
  }
}

function atomicWriteJson0600(filePath, data) {
  const dir = path.dirname(filePath);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const tmp = path.join(dir, `.auth.tmp.${process.pid}.${crypto.randomBytes(6).toString('hex')}`);
  const fd = fs.openSync(tmp, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL, 0o600);
  try {
    fs.fchmodSync(fd, 0o600);
    fs.writeFileSync(fd, JSON.stringify(data, null, 2) + '\n', 'utf8');
    fs.fsyncSync(fd);
  } catch (err) {
    try { fs.closeSync(fd); } catch { /* ignore */ }
    try { fs.unlinkSync(tmp); } catch { /* ignore */ }
    throw err;
  }
  fs.closeSync(fd);
  fs.renameSync(tmp, filePath);
}

async function resolveOauthAccess({ authFile, forceRefresh = false, signal, fetchImpl = fetch }) {
  let auth;
  try {
    auth = JSON.parse(fs.readFileSync(authFile, 'utf8'));
  } catch {
    return null;
  }
  if (!auth || typeof auth.access_token !== 'string' || !auth.access_token.trim()) {
    return null;
  }
  const expiresAt = Number(auth.expires_at) || 0;
  if (forceRefresh || expiresAt < Date.now() + 5 * 60_000) {
    if (typeof auth.refresh_token !== 'string' || !auth.refresh_token.trim()) {
      if (expiresAt <= Date.now()) {
        throw new Error('ChatGPT OAuth-sessie is verlopen; vernieuw de inlog via het dashboard.');
      }
    } else {
      const res = await fetchImpl(TOKEN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'refresh_token',
          refresh_token: auth.refresh_token.trim(),
          client_id: CLIENT_ID,
          scope: 'openid profile email',
        }).toString(),
        signal,
      });
      if (!res.ok) {
        throw new Error(`ChatGPT OAuth-verversing mislukt (HTTP ${res.status})`);
      }
      const tokens = await res.json();
      const claims = { ...decodeJwtPayload(tokens.access_token), ...decodeJwtPayload(tokens.id_token || auth.id_token) };
      const authClaim = claims['https://api.openai.com/auth'] || {};
      auth = {
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token || auth.refresh_token,
        id_token: tokens.id_token || auth.id_token || null,
        expires_at: Date.now() + (Number(tokens.expires_in) || 3600) * 1000,
        account_id: authClaim.chatgpt_account_id || auth.account_id || null,
        email: claims.email || auth.email || null,
        plan: authClaim.chatgpt_plan_type || auth.plan || null,
      };
      atomicWriteJson0600(authFile, auth);
    }
  }
  const accessToken = String(auth.access_token || '').trim();
  const accountId = String(auth.account_id || '').trim();
  if (!CREDENTIAL_RE.test(accessToken) || !CREDENTIAL_RE.test(accountId)) {
    throw new Error('Ongeldige OAuth-tokenstructuur voor fotowaarneming');
  }
  return { accessToken, accountId };
}

async function parseResponsesSse(res) {
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = null;
  let failed = null;
  const items = [];

  for await (const chunk of res.body) {
    buffer += decoder.decode(chunk, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line.startsWith('data:')) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === '[DONE]') continue;
      let ev;
      try { ev = JSON.parse(payload); } catch { continue; }
      if (ev.type === 'response.output_item.done' && ev.item) items.push(ev.item);
      else if (ev.type === 'response.completed') completed = ev.response;
      else if (ev.type === 'response.failed') failed = ev.response?.error || ev;
    }
  }
  if (failed) {
    throw new Error('Fotowaarneming gaf een providerfout in de antwoordstream');
  }
  const output = completed?.output?.length ? completed.output : items;
  const messages = (Array.isArray(output) ? output : [])
    .filter(item => item?.type === 'message')
    .map(item => (Array.isArray(item.content) ? item.content : [])
      .filter(c => c?.type === 'output_text')
      .map(c => String(c.text || ''))
      .join('')
      .trim())
    .filter(Boolean);
  const text = messages.at(-1) || '';
  if (!text) {
    throw new Error('Fotowaarneming leverde een leeg antwoord op');
  }
  return { text, output, usage: completed?.usage || null };
}

export async function chatComplete({
  instructions,
  input,
  model = 'gpt-6-astra',
  effort = 'medium',
  cacheKey = 'eva2-telegram-media',
  timeoutMs = 45_000,
  authFile = process.env.EVA_MODEL_OAUTH_FILE || '/var/lib/eva2-runtime-data/auth.json',
  apiKey = process.env.OPENAI_API_KEY || '',
  fetchImpl = fetch,
}) {
  const signal = AbortSignal.timeout(Math.max(1000, Number(timeoutMs) || 45_000));
  const effortStr = String(effort ?? 'medium').trim().toLowerCase();
  const body = {
    model,
    instructions,
    input,
    tools: [],
    tool_choice: 'none',
    parallel_tool_calls: false,
    store: false,
    stream: true,
    prompt_cache_key: cacheKey || 'eva2-telegram-media',
    include: ['reasoning.encrypted_content'],
    reasoning: { effort: effortStr === 'none' ? 'none' : effortStr, summary: 'auto' },
  };

  async function sendOnce(forceRefresh) {
    const oauth = await resolveOauthAccess({ authFile, forceRefresh, signal, fetchImpl });
    let url;
    let headers;
    if (oauth) {
      url = CHATGPT_RESPONSES_URL;
      headers = {
        Authorization: `Bearer ${oauth.accessToken}`,
        'chatgpt-account-id': oauth.accountId,
        'OpenAI-Beta': 'responses=experimental',
        originator: 'codex_cli_rs',
        session_id: SESSION_ID,
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      };
    } else if (typeof apiKey === 'string' && CREDENTIAL_RE.test(apiKey.trim())) {
      url = API_RESPONSES_URL;
      headers = {
        Authorization: `Bearer ${apiKey.trim()}`,
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      };
    } else {
      throw new Error('Geen modelinlog beschikbaar voor Telegram-fotowaarneming');
    }
    const res = await fetchImpl(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) {
      const err = new Error(`Fotowaarneming mislukt (HTTP ${res.status})`);
      err.status = res.status;
      err.viaOauth = Boolean(oauth);
      throw err;
    }
    return parseResponsesSse(res);
  }

  try {
    return await sendOnce(false);
  } catch (err) {
    if (err?.status === 401 && err?.viaOauth) {
      return sendOnce(true);
    }
    throw err;
  }
}
