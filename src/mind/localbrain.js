// Eva's lokale of remote GPU-brein: één plek die weet waar haar zware zintuigen draaien.
//
// De Node-kant kan prima op een stille 24/7 mini-pc leven, terwijl dit brein op een
// RTX-machine draait. Daarom maken we de status expliciet: endpoint, device, local/remote,
// laatste gebruik en simpele succes/fout-tellers per soort werk.
import { config } from '../config.js';
import { kvGet, kvSet, logTimeline } from '../db.js';

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
let monitorTimer = null;

export function brainServiceUrl() {
  return config.brainUrl;
}

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

function healthFailureDecision({ previousFailures = 0, lastOk = null, now = Date.now() } = {}) {
  const failures = Math.max(0, Number(previousFailures || 0)) + 1;
  const lastOkMs = Date.parse(lastOk || '');
  const staleSinceOk = !Number.isFinite(lastOkMs) || Number(now) - lastOkMs >= 8 * 60_000;
  return { failures, confirmedDown: failures >= 3 && staleSinceOk };
}

export async function brainHealth({ timeoutMs = 8000, logChange = false, authoritative = false } = {}) {
  const wasConfirmedDown = status.confirmedDown === true;
  const t0 = Date.now();
  try {
    const res = await fetch(`${config.brainUrl}/health`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const j = await res.json();
    status = {
      ...status,
      url: config.brainUrl,
      remote: isRemoteBrain(config.brainUrl),
      reachable: true,
      ok: !!j.ok,
      device: j.device || null,
      facesReady: !!j.faces_klaar,
      encoderTrained: !!j.encoder_getraind,
      lastHealth: nowIso(),
      lastError: null,
      healthFailures: 0,
      confirmedDown: false,
    };
    record('health', { ok: true, ms: Date.now() - t0 });
    if (logChange && wasConfirmedDown && status.ok) {
      logTimeline('system', 'Mijn lokale zicht- en leerfuncties zijn weer beschikbaar.', {
        localbrain: brainServiceStatus(), familyVisible: true,
      });
    }
    return brainServiceStatus();
  } catch (e) {
    const error = (e && e.message) || String(e);
    if (!authoritative) {
      return {
        ...brainServiceStatus(),
        bereikbaar: false,
        ok: false,
        laatsteFout: error,
        probeMislukt: true,
      };
    }
    const { failures, confirmedDown } = healthFailureDecision({
      previousFailures: status.healthFailures,
      lastOk: status.lastOk,
    });
    status = {
      ...status,
      url: config.brainUrl,
      remote: isRemoteBrain(config.brainUrl),
      reachable: confirmedDown ? false : status.reachable,
      ok: confirmedDown ? false : status.ok,
      lastHealth: nowIso(),
      lastError: error,
      healthFailures: failures,
      confirmedDown,
    };
    record('health', { ok: false, ms: Date.now() - t0, error: status.lastError });
    if (logChange && confirmedDown && !wasConfirmedDown) {
      logTimeline('system', 'Mijn lokale zicht- en leerfuncties zijn tijdelijk niet beschikbaar; ik blijf ze zelf opnieuw proberen.', {
        localbrain: brainServiceStatus(), technicalError: status.lastError, familyVisible: true,
      });
    }
    return brainServiceStatus();
  }
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
    // Alleen de centrale health-monitor mag die conclusie na meerdere fouten trekken.
    if (Number.isInteger(e?.status)) status.reachable = true;
    status.lastError = msg;
    record(kind, { ok: false, ms: Date.now() - t0, error: msg });
    throw e;
  }
}

export function startLocalBrainMonitor() {
  if (monitorTimer) return monitorTimer;
  brainHealth({ timeoutMs: 8000, logChange: true, authoritative: true }).catch(() => {});
  monitorTimer = setInterval(
    () => brainHealth({ timeoutMs: 8000, logChange: true, authoritative: true }).catch(() => {}),
    5 * 60_000,
  );
  return monitorTimer;
}

export const __localbrainTest = { healthFailureDecision };
