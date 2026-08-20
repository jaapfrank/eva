// Eva's lokale of remote GPU-brein: één plek die weet waar haar zware zintuigen draaien.
//
// De Node-kant kan prima op een stille 24/7 mini-pc leven, terwijl dit brein op een
// RTX-machine draait. Daarom maken we de status expliciet: endpoint, device, local/remote,
// laatste gebruik en simpele succes/fout-tellers per soort werk.
import { config } from '../config.js';
import { kvGet, kvSet } from '../db.js';

const KEY = 'localbrain_status_v1';
const MAX_KINDS = 12;

function nowIso() {
  return new Date().toISOString();
}

function emptyStats() {
  return {
    url: config.brainUrl,
    remote: isRemoteBrain(config.brainUrl),
    reachable: false,
    ok: false,
    device: null,
    facesReady: false,
    encoderTrained: false,
    lastHealth: null,
    lastOk: null,
    lastError: null,
    healthFailures: 0,
    confirmedDown: false,
    calls: {},
  };
}

let status = { ...emptyStats(), ...(kvGet(KEY, null) || {}) };
status.url = config.brainUrl;
status.remote = isRemoteBrain(config.brainUrl);
export function isRemoteBrain(url = config.brainUrl) {
  try {
    const u = new URL(url);
    return !['127.0.0.1', 'localhost', '::1'].includes(u.hostname);
  } catch {
    return false;
  }
}

function persist() {
  const calls = Object.entries(status.calls || {}).slice(-MAX_KINDS);
  kvSet(KEY, { ...status, calls: Object.fromEntries(calls) });
}

function record(kind, patch = {}) {
  const key = String(kind || 'onbekend');
  const c = status.calls[key] || { ok: 0, fail: 0, lastMs: null, lastOk: null, lastError: null };
  if (patch.ok) {
    c.ok += 1;
    c.lastOk = nowIso();
    c.lastError = null;
    status.lastOk = c.lastOk;
  } else {
    c.fail += 1;
    c.lastError = patch.error || 'onbekende fout';
    status.lastError = c.lastError;
  }
  if (patch.ms != null) c.lastMs = patch.ms;
  status.calls[key] = c;
  persist();
}

export function brainServiceStatus() {
  return {
    url: status.url,
    remote: status.remote,
    bereikbaar: status.reachable,
    ok: status.ok,
    device: status.device,
    cuda: status.device === 'cuda',
    facesKlaar: status.facesReady,
    encoderGetraind: status.encoderTrained,
    laatsteHealth: status.lastHealth,
    laatsteGebruik: status.lastOk,
    laatsteFout: status.lastError,
    opeenvolgendeHealthFouten: Number(status.healthFailures || 0),
    bevestigdOnbereikbaar: status.confirmedDown === true,
    calls: status.calls || {},
  };
}

export async function brainPost(path, body, { kind = 'call', timeoutMs = 8000 } = {}) {
  const route = String(path || '').startsWith('/') ? String(path) : `/${path}`;
  const t0 = Date.now();
  try {
    const res = await fetch(`${config.brainUrl}${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: AbortSignal.timeout(timeoutMs),
    });
    let j = null;
    try { j = await res.json(); } catch { /* fouttekst hieronder blijft bruikbaar */ }
    if (!res.ok) {
      const detail = typeof j?.detail === 'string' ? j.detail : j?.detail?.message;
      const message = j?.fout || detail || `HTTP ${res.status}`;
      const error = Object.assign(new Error(message), {
        status: res.status,
        code: j?.code || j?.detail?.code || null,
        retryable: res.status === 408 || res.status === 429 || res.status >= 500,
      });
      throw error;
    }
    if (!j || typeof j !== 'object') throw new Error('Brein-service gaf geen geldig JSON-resultaat.');
    if (j.fout) {
      const error = Object.assign(new Error(j.fout), {
        code: j.code || null,
        retryable: false,
      });
      throw error;
    }
    status.reachable = true;
    status.ok = true;
    status.lastError = null;
    status.healthFailures = 0;
    status.confirmedDown = false;
    record(kind, { ok: true, ms: Date.now() - t0 });
    return j;
  } catch (e) {
    const msg = (e && e.message) || String(e);
    // Een mislukte specifieke operatie bewijst niet dat de hele service weg is.
    // De aanroepende zintuigroute beslist of en hoe zij tijdelijk terugvalt.
    if (Number.isInteger(e?.status)) status.reachable = true;
    status.lastError = msg;
    record(kind, { ok: false, ms: Date.now() - t0, error: msg });
    throw e;
  }
}
