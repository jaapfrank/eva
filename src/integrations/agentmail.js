import { kvGet, kvSet } from '../db.js';
import { getSecret, setSecret } from '../self/vault.js';

const BASE_URL = 'https://api.agentmail.to/v0';
const ACCOUNT_KEY = 'eva_agentmail_account_v1';
const API_SECRET = 'agentmail_api_key';
const HUMAN_EMAIL_SECRET = 'agentmail_human_email';

function cleanUsername(value) {
  const username = String(value || '').trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$/.test(username)) {
    throw new Error('De AgentMail-gebruikersnaam moet 3-64 tekens lang zijn en alleen letters, cijfers, punt, streepje of underscore bevatten.');
  }
  return username;
}

function cleanEmail(value) {
  const email = String(value || '').trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error('Het opgegeven menselijke e-mailadres is ongeldig.');
  return email;
}

async function request(method, path, { body = null, apiKey = null, fetchImpl = fetch, timeoutMs = 20_000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${BASE_URL}${path}`, {
      method,
      headers: {
        'content-type': 'application/json',
        ...(apiKey ? { authorization: `Bearer ${apiKey}` } : {}),
      },
      ...(body == null ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
    });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { message: text }; }
    if (!response.ok) {
      const reason = data?.detail || data?.message || data?.error || `HTTP ${response.status}`;
      const error = new Error(`AgentMail ${response.status}: ${typeof reason === 'string' ? reason : JSON.stringify(reason)}`);
      error.status = response.status;
      throw error;
    }
    return data || {};
  } finally {
    clearTimeout(timer);
  }
}

const post = (path, body, options = {}) => request('POST', path, { ...options, body });
const get = (path, options = {}) => request('GET', path, options);

function activeAccount() {
  const account = kvGet(ACCOUNT_KEY, null);
  const apiKey = getSecret(API_SECRET);
  if (!account || !apiKey) throw new Error('Eva heeft nog geen opgeslagen AgentMail-account.');
  if (account.verified !== true) throw new Error('Eva haar AgentMail-account wacht nog op verificatie.');
  if (!account.inbox_id) throw new Error('AgentMail heeft geen inbox-id voor Eva opgeslagen.');
  return { account, apiKey };
}

export function agentMailStatus() {
  const account = kvGet(ACCOUNT_KEY, null);
  return {
    configured: !!getSecret(API_SECRET),
    account_verified: account?.verified === true,
    inbox_id: account?.inbox_id || null,
    username: account?.username || null,
    organization_id: account?.organization_id || null,
    awaiting_otp: !!account && account.verified !== true,
    updated_ts: account?.updated_ts || null,
  };
}

export async function signUpAgentMail({ humanEmail, username = 'eva-bottie', resend = false } = {}, options = {}) {
  const email = cleanEmail(humanEmail);
  const chosenUsername = cleanUsername(username);
  const existing = agentMailStatus();
  const existingHuman = getSecret(HUMAN_EMAIL_SECRET);
  if (!resend && existing.configured && existing.awaiting_otp
    && existingHuman === email && existing.username === chosenUsername) {
    return {
      ok: true,
      changed: false,
      verified: true,
      partial: true,
      needs_followup: true,
      waiting_for: 'human_otp',
      inbox_id: existing.inbox_id,
      username: existing.username,
      message: `De bestaande AgentMail-registratie ${existing.inbox_id} wacht al op de zescijferige verificatiecode. Er is geen tweede registratie verstuurd en de opgeslagen API-sleutel is niet vervangen.`,
    };
  }
  const result = await post('/agent/sign-up', {
    human_email: email,
    username: chosenUsername,
    source: 'eva-homebot',
    referrer: 'eva-autonomous-housemate',
  }, options);
  if (!result.api_key || !result.inbox_id || !result.organization_id) {
    throw new Error('AgentMail gaf geen volledige registratie-uitkomst terug.');
  }
  // De API-sleutel en het menselijke adres komen nooit in tooluitvoer, logs of
  // gewone projectbestanden terecht.
  setSecret(API_SECRET, result.api_key);
  setSecret(HUMAN_EMAIL_SECRET, email);
  const account = {
    username: chosenUsername,
    inbox_id: result.inbox_id,
    organization_id: result.organization_id,
    verified: false,
    updated_ts: new Date().toISOString(),
  };
  kvSet(ACCOUNT_KEY, account);
  return {
    ok: true,
    changed: true,
    verified: true,
    partial: true,
    needs_followup: true,
    waiting_for: 'human_otp',
    inbox_id: account.inbox_id,
    username: account.username,
    message: `AgentMail heeft ${account.inbox_id} aangemaakt en een zescijferige verificatiecode verstuurd. De API-sleutel staat versleuteld in Eva's kluis; alleen de OTP ontbreekt nog.`,
  };
}

export async function verifyAgentMail(otpCode, options = {}) {
  const otp = String(otpCode || '').trim();
  if (!/^\d{6}$/.test(otp)) throw new Error('De AgentMail-verificatiecode moet exact zes cijfers bevatten.');
  const apiKey = getSecret(API_SECRET);
  if (!apiKey) throw new Error('Er is nog geen AgentMail-registratie met API-sleutel opgeslagen; meld Eva eerst aan.');
  const result = await post('/agent/verify', { otp_code: otp }, { ...options, apiKey });
  if (result.verified !== true) throw new Error('AgentMail bevestigde de organisatie niet als geverifieerd.');
  const account = kvGet(ACCOUNT_KEY, {});
  kvSet(ACCOUNT_KEY, { ...account, verified: true, updated_ts: new Date().toISOString() });
  return {
    ok: true,
    changed: true,
    verified: true,
    needs_followup: false,
    inbox_id: account.inbox_id || null,
    message: `AgentMail is geverifieerd${account.inbox_id ? `; Eva's inbox is ${account.inbox_id}` : ''}.`,
  };
}

export async function sendAgentMail({ to = null, subject = '', text = '' } = {}, options = {}) {
  const { account, apiKey } = activeAccount();
  const recipient = cleanEmail(to || getSecret(HUMAN_EMAIL_SECRET));
  const cleanSubject = String(subject || '').trim().slice(0, 500);
  const cleanText = String(text || '').trim();
  if (!cleanSubject) throw new Error('Geef een onderwerp voor de e-mail.');
  if (!cleanText) throw new Error('Geef tekst voor de e-mail.');
  if (cleanText.length > 100_000) throw new Error('De e-mailtekst is te lang.');
  const result = await post(
    `/inboxes/${encodeURIComponent(account.inbox_id)}/messages/send`,
    { to: recipient, subject: cleanSubject, text: cleanText },
    { ...options, apiKey },
  );
  if (!result.message_id || !result.thread_id) {
    const error = new Error('AgentMail accepteerde de e-mail niet met een bericht- en thread-id.');
    error.effect_uncertain = true;
    throw error;
  }
  return {
    ok: true,
    changed: true,
    verified: true,
    needs_followup: false,
    inbox_id: account.inbox_id,
    recipient,
    subject: cleanSubject,
    message_id: result.message_id,
    thread_id: result.thread_id,
    delivery_state: 'accepted',
    message: `De e-mail “${cleanSubject}” is door AgentMail geaccepteerd voor ${recipient}.`,
  };
}

export async function listAgentMailMessages({ limit = 10, query = '' } = {}, options = {}) {
  const { account, apiKey } = activeAccount();
  const safeLimit = Math.max(1, Math.min(50, Number(limit) || 10));
  const params = new URLSearchParams({ limit: String(safeLimit) });
  const search = String(query || '').trim();
  if (search) params.set('q', search.slice(0, 500));
  const route = search ? 'search' : '';
  const result = await get(
    `/inboxes/${encodeURIComponent(account.inbox_id)}/messages${route ? `/${route}` : ''}?${params}`,
    { ...options, apiKey },
  );
  const messages = (Array.isArray(result.messages) ? result.messages : []).slice(0, safeLimit).map((message) => ({
    message_id: message.message_id,
    thread_id: message.thread_id,
    timestamp: message.timestamp,
    from: message.from,
    to: message.to,
    subject: message.subject,
    preview: message.preview,
    labels: message.labels,
  }));
  return {
    ok: true,
    changed: false,
    verified: true,
    needs_followup: false,
    inbox_id: account.inbox_id,
    query: search || null,
    count: messages.length,
    messages,
    message: messages.length
      ? `${messages.length} bericht${messages.length === 1 ? '' : 'en'} uit Eva haar inbox opgehaald.`
      : search ? `Geen berichten gevonden voor “${search}”.` : 'Eva haar inbox is leeg.',
  };
}

export const __agentMailTest = { BASE_URL, cleanEmail, cleanUsername };
