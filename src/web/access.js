import { timingSafeEqual } from 'node:crypto';

export function isLoopbackHost(host = '') {
  const value = String(host || '').trim().toLowerCase();
  return ['127.0.0.1', '::1', 'localhost'].includes(value);
}

export function assertSafeWebBinding({ host = '127.0.0.1', token = '' } = {}) {
  if (!isLoopbackHost(host) && !String(token || '').trim()) {
    throw new Error('Een dashboard buiten localhost vereist EVA_WEB_TOKEN. Eva start niet met een open gezins-API.');
  }
  return true;
}

export function requestAccessToken(req) {
  const authorization = String(req?.headers?.authorization || '');
  const bearer = authorization.match(/^Bearer\s+(.+)$/i)?.[1];
  return String(
    bearer
      || req?.headers?.['x-eva-token']
      || req?.query?.access_token
      || '',
  ).trim();
}

export function requestAuthorized(req, expectedToken = '') {
  const expected = String(expectedToken || '').trim();
  if (!expected) return true;
  const supplied = requestAccessToken(req);
  if (!supplied) return false;
  const left = Buffer.from(supplied);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

export function normalizeWebConversationId(value = '', sender = 'huisgenoot') {
  const requested = String(value || '').trim().replace(/^(?:web\s*:\s*)+/i, '');
  const fallback = String(sender || 'huisgenoot').trim();
  const identity = (requested || fallback)
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^a-z0-9._:-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120) || 'huisgenoot';
  return `web:${identity}`;
}

export function familyTimelineEntry(row) {
  if (!row || row.meta?.internalOnly === true) return false;
  if (row.meta?.syntheticTest === true
    || row.meta?.messageContext?.syntheticTest === true
    || row.meta?.quarantinedSyntheticContamination) return false;
  if (row.meta?.familyVisible === true) return true;
  return ['chat_in', 'chat_out', 'reminder'].includes(row.kind);
}
