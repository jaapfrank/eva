import { config } from '../config.js';

const registeredSecrets = new Map();

function safeSecretName(value = '') {
  return String(value || 'SECRET').toUpperCase().replace(/[^A-Z0-9_-]+/g, '_').slice(0, 80);
}

export function registerSensitiveValues(entries = []) {
  for (const [name, value] of entries) {
    if (typeof value !== 'string' || value.length < 8) continue;
    registeredSecrets.set(safeSecretName(name), value);
  }
  return registeredSecrets.size;
}

const exactSecrets = () => [
  ['HA_TOKEN', config.haToken],
  ['TELEGRAM_BOT_TOKEN', config.telegramToken],
  ['UNIFI_API_KEY', config.unifiKey],
  ['OPENAI_API_KEY', config.openaiApiKey],
  ['EVA_WEB_TOKEN', config.webToken],
  ...registeredSecrets.entries(),
].filter(([, value]) => typeof value === 'string' && value.length >= 8);

export function redactSensitiveText(value = '') {
  let text = String(value ?? '');
  for (const [name, secret] of exactSecrets()) {
    text = text.replaceAll(secret, `[AFGESCHERMD:${name}]`);
  }
  return text
    .replace(/-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/gi, '[AFGESCHERMD:PRIVATE_KEY]')
    .replace(/\b(Bearer\s+)[A-Za-z0-9._~+/-]{12,}/gi, '$1[AFGESCHERMD:TOKEN]')
    .replace(/((?:api[-_ ]?key|access[-_ ]?token|password|wachtwoord|passphrase|secret)\s*(?:is|was|=|:)\s*["'`]?)\S{8,}/gi, '$1[AFGESCHERMD:SECRET]')
    .replace(/\b(?:sk-(?:proj-)?|ghp_|github_pat_)[A-Za-z0-9_-]{12,}\b/g, '[AFGESCHERMD:TOKEN]')
    .replace(/\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, '[AFGESCHERMD:JWT]');
}

export function redactSecrets(value) {
  if (typeof value === 'string') return redactSensitiveText(value);
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactSecrets(item)]));
  }
  return value;
}
