import { createHash } from 'node:crypto';

export const PUBLIC_REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/u;
export const PUBLIC_REQUEST_REPLAY_SCOPE = 'durable_http_ingress';

const MAX_HOST_BINDINGS = 100_000;
const MAX_EXACT_REPLAYS = 4_096;
const hostRequests = new Map();
const exactReplayOrder = [];

function present(value) {
  return value !== undefined;
}

/**
 * @param {{bodyRequestId?: unknown, headerRequestId?: unknown, synthetic?: boolean}} options
 */
export function canonicalPublicRequestId({
  bodyRequestId,
  headerRequestId,
  synthetic = false,
} = {}) {
  const bodyPresent = present(bodyRequestId);
  const headerPresent = present(headerRequestId);
  if (!bodyPresent && !headerPresent) {
    return { ok: false, requestId: null, synthetic: false, reason: 'missing' };
  }
  if (bodyPresent && headerPresent && bodyRequestId !== headerRequestId) {
    return { ok: false, requestId: null, synthetic: false, reason: 'mismatch' };
  }
  const requestId = bodyPresent ? bodyRequestId : headerRequestId;
  if (typeof requestId !== 'string' || !PUBLIC_REQUEST_ID_PATTERN.test(requestId)) {
    return { ok: false, requestId: null, synthetic: false, reason: 'invalid' };
  }
  return { ok: true, requestId, synthetic: synthetic === true };
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(',')}}`;
  }
  return JSON.stringify(value);
}

function intentSha256(intent) {
  return createHash('sha256').update(canonicalJson(intent)).digest('hex');
}

function retainExactReplay(requestId, record) {
  exactReplayOrder.push(requestId);
  while (exactReplayOrder.length > MAX_EXACT_REPLAYS) {
    const oldest = exactReplayOrder.shift();
    const prior = hostRequests.get(oldest);
    if (!prior || prior === record || prior.state !== 'settled') continue;
    prior.response = null;
    prior.state = 'tombstone';
  }
}

/**
 * Process-lokale idempotencyseam. Hij voorkomt een tweede uitvoering zolang de
 * hostruntime leeft, maar beweert bewust geen crash- of herstartreplay.
 */
export async function executeHostBoundPublicRequest({ requestId, intent, execute }) {
  const sha256 = intentSha256(intent);
  const existing = hostRequests.get(requestId);
  if (existing) {
    if (existing.intentSha256 !== sha256) return { kind: 'conflict' };
    if (existing.state === 'tombstone') return { kind: 'replay_unavailable' };
    if (existing.state === 'settled') {
      return { kind: 'replay', response: existing.response, replayed: true };
    }
    return { kind: 'replay', response: await existing.pending, replayed: true };
  }
  if (hostRequests.size >= MAX_HOST_BINDINGS) return { kind: 'capacity' };

  const record = {
    intentSha256: sha256,
    state: 'started',
    pending: null,
    response: null,
  };
  record.pending = (async () => {
    let response;
    try {
      response = await execute();
    } catch {
      response = {
        status: 500,
        body: { error: 'De requestuitvoering stopte onverwacht.' },
      };
    }
    record.response = Object.freeze({
      status: Number(response?.status) || 500,
      body: Object.freeze({ ...(response?.body || {}) }),
    });
    record.state = 'settled';
    record.pending = null;
    retainExactReplay(requestId, record);
    return record.response;
  })();
  hostRequests.set(requestId, record);
  return { kind: 'executed', response: await record.pending, replayed: false };
}
