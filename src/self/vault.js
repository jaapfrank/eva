// Eva's kluis en identiteit. Geheimen staan versleuteld in de database; de
// lokale sleutel staat apart en is alleen leesbaar voor het service-account.
import { kvGet, kvSet } from '../db.js';
import { openThought, sealThought } from './secretmind.js';

const VAULT = 'vault';
const IDENT = 'identiteit';
const ENVELOPE = 'encrypted:';

function readVault() {
  const value = kvGet(VAULT, {});
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function sealed(value) {
  return typeof value === 'string' && value.startsWith(`${ENVELOPE}v1:`);
}

function encrypt(value) {
  return `${ENVELOPE}${sealThought(String(value))}`;
}

function decrypt(value) {
  if (!sealed(value)) return String(value);
  const opened = openThought(value.slice(ENVELOPE.length));
  if (opened == null) throw new Error('Eva\'s kluis kon een opgeslagen geheim niet ontsleutelen.');
  return opened;
}

// Migreert de oude platte JSON-vorm in één database-update. Namen van
// geheimen blijven zichtbaar voor capability-inventarisatie; waarden nooit.
export function migrateLegacySecrets() {
  const current = readVault();
  const legacy = Object.entries(current).filter(([, value]) => !sealed(value));
  if (!legacy.length) return { changed: false, count: 0 };
  const migrated = { ...current };
  for (const [name, value] of legacy) migrated[name] = encrypt(value);
  kvSet(VAULT, migrated);
  return { changed: true, count: legacy.length };
}

export function setSecret(sleutel, waarde) {
  migrateLegacySecrets();
  const v = readVault();
  v[String(sleutel)] = encrypt(waarde);
  kvSet(VAULT, v);
}

export function getSecret(sleutel) {
  migrateLegacySecrets();
  const value = readVault()[String(sleutel)];
  return value == null ? null : decrypt(value);
}

export function listSecretKeys() {
  migrateLegacySecrets();
  return Object.keys(readVault());
}

export function deleteSecret(sleutel) {
  migrateLegacySecrets();
  const v = readVault();
  delete v[String(sleutel)];
  kvSet(VAULT, v);
}

export function getIdentity() {
  return kvGet(IDENT, {});
}

export function setIdentity(partial) {
  kvSet(IDENT, { ...kvGet(IDENT, {}), ...partial });
  return getIdentity();
}
