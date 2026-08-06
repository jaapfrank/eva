import WebSocket from 'ws';
import dns from 'node:dns/promises';
import fs from 'node:fs/promises';
import https from 'node:https';
import net from 'node:net';
import path from 'node:path';
import { config } from '../config.js';
import { bus } from '../bus.js';
import { logTimeline } from '../db.js';
import { errMsg } from '../util.js';
import { getSecret } from '../self/vault.js';

// Een HA-basis-URL met een IP dat ándere apparaten (Sonos!) kunnen bereiken —
// niet het interne docker-IP en niet een .local-naam die een speaker niet resolvet.
let _reachable = null;
async function reachableBase() {
  if (_reachable) return _reachable;
  const u = new URL(config.haUrl);
  if (/^\d+\.\d+\.\d+\.\d+$/.test(u.hostname)) { _reachable = config.haUrl; return _reachable; }
  try {
    const { address } = await dns.lookup(u.hostname, { family: 4 });
    _reachable = `${u.protocol}//${address}:${u.port || 8123}`;
  } catch {
    _reachable = config.haUrl;
  }
  return _reachable;
}

/**
 * Home Assistant-integratie.
 * - WebSocket: live state_changed events + states-cache (arrival/departure, zon, deuren).
 * - REST: snapshots, services aanroepen, camera's, TTS.
 */

const WS_URL = config.haUrl.replace(/^http/, 'ws') + '/api/websocket';
const BACKOFF_START = 5_000;
const BACKOFF_MAX = 60_000;

let ws = null;
let connected = false;       // geauthenticeerd en geabonneerd
let started = false;         // connectHA al aangeroepen
let backoff = BACKOFF_START;
let reconnectTimer = null;
let pingTimer = null;
let msgId = 1;
const pending = new Map();   // id -> {resolve, reject, timer} voor WS-commando's
const states = new Map();    // entity_id -> state-object (cache)
let offlineLogged = false;   // voorkom timeline-spam bij herhaald falen
const TRIGGER_FRAME_DIR = path.join(config.workspaceDir, 'camera_trigger_frames');
const PERSON_INCIDENT_QUIET_MS = Math.max(30_000, Number(process.env.EVA_CAMERA_INCIDENT_QUIET_MS || 180_000));
const DOORBELL_INCIDENT_QUIET_MS = Math.max(3_000, Number(process.env.EVA_DOORBELL_INCIDENT_QUIET_MS || 10_000));
const TRIGGER_FRAME_MAX_FILES = Math.max(60, Number(process.env.EVA_TRIGGER_FRAME_MAX_FILES || 600));
const TRIGGER_FRAME_MAX_AGE_MS = Math.max(
  6 * 3600_000,
  Number(process.env.EVA_TRIGGER_FRAME_RETENTION_HOURS || 48) * 3600_000
);
const cameraIncidents = new Map(); // camera/sensor -> lopend Protect-incident
let triggerPrunePromise = null;
let lastTriggerPruneAt = 0;

// ---------- helpers ----------

const headers = () => ({
  Authorization: `Bearer ${config.haToken}`,
  'Content-Type': 'application/json',
});

const friendlyName = (st) =>
  st?.attributes?.friendly_name || st?.entity_id?.split('.')[1]?.replace(/_/g, ' ') || 'onbekend';

const safeFilePart = (s) =>
  String(s || 'camera')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80) || 'camera';

function imageExt(contentType = '') {
  if (/png/i.test(contentType)) return 'png';
  if (/webp/i.test(contentType)) return 'webp';
  return 'jpg';
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function cameraIncidentDecision(event, now = Date.now(), quietMs = null) {
  if (!['person_detected', 'doorbell'].includes(event?.type)) return { accepted: true, incident: null };

  const at = Number.isFinite(Number(now)) ? Number(now) : Date.now();
  const defaultQuiet = event.type === 'doorbell' ? DOORBELL_INCIDENT_QUIET_MS : PERSON_INCIDENT_QUIET_MS;
  const quiet = Math.max(1_000, Number(quietMs) || defaultQuiet);
  const cameraKey = String(event.camera || event.sensor || 'onbekende-camera');
  const key = event.type === 'doorbell' ? `doorbell:${cameraKey}` : cameraKey;
  const previous = cameraIncidents.get(key);

  if (previous && at - previous.lastSignalAt < quiet) {
    previous.lastSignalAt = at;
    previous.signalCount += 1;
    return {
      accepted: false,
      reason: 'same_incident',
      incident: { ...previous },
      quietMs: quiet,
    };
  }

  const incident = {
    id: `${safeFilePart(key)}:${new Date(at).toISOString()}`,
    key,
    startedAt: at,
    lastSignalAt: at,
    signalCount: 1,
  };
  cameraIncidents.set(key, incident);

  // Een camera die lang niets meer meldde hoeft niet voor altijd in het geheugen.
  for (const [oldKey, old] of cameraIncidents) {
    if (at - old.lastSignalAt > Math.max(24 * 3600_000, quiet * 10)) cameraIncidents.delete(oldKey);
  }
  return { accepted: true, incident: { ...incident }, quietMs: quiet };
}

async function pruneTriggerFrames() {
  let names;
  try {
    names = await fs.readdir(TRIGGER_FRAME_DIR);
  } catch (e) {
    if (e?.code === 'ENOENT') return 0;
    throw e;
  }

  const files = (await Promise.all(names.map(async (name) => {
    if (!/\.(?:jpe?g|png|webp)$/i.test(name)) return null;
    const file = path.join(TRIGGER_FRAME_DIR, name);
    try {
      const stat = await fs.stat(file);
      return { file, mtimeMs: stat.mtimeMs };
    } catch {
      return null;
    }
  }))).filter(Boolean).sort((a, b) => b.mtimeMs - a.mtimeMs);

  const cutoff = Date.now() - TRIGGER_FRAME_MAX_AGE_MS;
  const remove = files.filter((entry, index) => index >= TRIGGER_FRAME_MAX_FILES || entry.mtimeMs < cutoff);
  await Promise.all(remove.map((entry) => fs.unlink(entry.file).catch(() => {})));
  if (remove.length) {
    logTimeline('system', `${remove.length} oude triggerbeelden opgeruimd; ${files.length - remove.length} recente beelden blijven bewaard.`, {
      triggerFramePrune: true,
      removed: remove.length,
      retained: files.length - remove.length,
      maxFiles: TRIGGER_FRAME_MAX_FILES,
      retentionHours: Math.round(TRIGGER_FRAME_MAX_AGE_MS / 3600_000),
    });
  }
  return remove.length;
}

function scheduleTriggerFramePrune({ force = false } = {}) {
  if (triggerPrunePromise) return triggerPrunePromise;
  if (!force && Date.now() - lastTriggerPruneAt < 6 * 3600_000) return null;
  lastTriggerPruneAt = Date.now();
  triggerPrunePromise = pruneTriggerFrames()
    .catch((e) => {
      logTimeline('system', `Triggerbeelden opruimen mislukte: ${errMsg(e).slice(0, 120)}`);
      return 0;
    })
    .finally(() => { triggerPrunePromise = null; });
  return triggerPrunePromise;
}

export const __cameraIncidentTest = {
  decide: cameraIncidentDecision,
  reset() { cameraIncidents.clear(); },
  quietMs: PERSON_INCIDENT_QUIET_MS,
  doorbellQuietMs: DOORBELL_INCIDENT_QUIET_MS,
};

async function restGet(path) {
  const res = await fetch(config.haUrl + path, {
    headers: headers(),
    signal: AbortSignal.timeout(5_000),
  });
  if (!res.ok) throw new Error(`HA GET ${path} mislukt: HTTP ${res.status}`);
  return res.json();
}

/** Alle states: uit de WS-cache als die er is, anders via REST. */
export async function allStates() {
  if (connected && states.size) return [...states.values()];
  return restGet('/api/states');
}

// ---------- WebSocket-verbinding ----------

export function haConnected() {
  return connected;
}

/** Laat hogere lagen een reeds ontvangen HA-status gebruiken zonder opnieuw te
 * pollen. De adapter blijft eigenaar van de cache; bellers krijgen alleen het
 * actuele statusobject terug. */
export function cachedState(entityId) {
  return states.get(String(entityId || '').toLowerCase()) || null;
}

/** Alleen-lezen bronfoto voor lagen die een verstreken event-wachtpunt eenmaal
 * moeten verzoenen. De adapter blijft eigenaar van de cache. */
export function cachedStates() {
  return [...states.values()];
}

function tcpCheck(host, port, timeoutMs = 1200) {
  return new Promise((resolve) => {
    const sock = net.createConnection({ host, port });
    let done = false;
    const finish = (ok, detail) => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch { /* negeer */ }
      resolve({ ok, detail });
    };
    sock.setTimeout(timeoutMs, () => finish(false, 'timeout'));
    sock.on('connect', () => finish(true, 'open'));
    sock.on('error', (e) => finish(false, errMsg(e)));
  });
}

export async function diagnoseHA({ timeoutMs = 1500 } = {}) {
  const out = {
    url: config.haUrl,
    connected,
    host: null,
    port: 8123,
    dns: null,
    tcp: null,
    rest: null,
    conclusie: '',
    advies: '',
  };
  try {
    const u = new URL(config.haUrl);
    out.host = u.hostname;
    out.port = Number(u.port || (u.protocol === 'https:' ? 443 : 80));
    try {
      const d = await dns.lookup(u.hostname, { family: 4 });
      out.dns = { ok: true, address: d.address };
    } catch (e) {
      out.dns = { ok: false, error: errMsg(e) };
      out.conclusie = `DNS voor ${u.hostname} faalt`;
      out.advies = 'controleer netwerknaam of HA-adres';
      return out;
    }

    out.tcp = await tcpCheck(out.dns.address || u.hostname, out.port, timeoutMs);
    if (!out.tcp.ok) {
      out.conclusie = `HA-host/poort ${out.host}:${out.port} is niet bereikbaar`;
      out.advies = 'controleer of de HA-machine aan staat of netwerk heeft';
      return out;
    }

    try {
      const res = await fetch(`${config.haUrl}/api/`, {
        headers: headers(),
        signal: AbortSignal.timeout(timeoutMs),
      });
      const text = await res.text().catch(() => '');
      out.rest = { ok: res.ok, status: res.status, body: text.slice(0, 120) };
      out.conclusie = res.ok
        ? 'HA API antwoordt'
        : `HA API antwoordt met HTTP ${res.status}`;
      out.advies = res.ok ? 'probeer de actie opnieuw' : 'controleer HA-auth/API-status';
      return out;
    } catch (e) {
      out.rest = { ok: false, error: errMsg(e) };
      out.conclusie = `HA-host leeft, maar de HA-app/API geeft geen antwoord`;
      out.advies = 'doe ff een power cycle van Home Assistant';
      return out;
    }
  } catch (e) {
    out.conclusie = `HA-diagnose mislukte: ${errMsg(e)}`;
    out.advies = 'controleer HA handmatig';
    return out;
  }
}

/** Verbindt met de HA WebSocket API; herverbindt zelf met backoff. Throwt nooit. */
export async function connectHA() {
  if (started) return;
  started = true;
  scheduleTriggerFramePrune({ force: true });
  if (!config.haToken) {
    logTimeline('system', 'Home Assistant: geen HA_TOKEN ingesteld, integratie inactief');
    return;
  }
  openSocket();
}

function openSocket() {
  try {
    // handshakeTimeout: anders kan een stil gedropte TCP-connect minutenlang hangen
    ws = new WebSocket(WS_URL, { handshakeTimeout: 15_000 });
  } catch (e) {
    onDisconnected(errMsg(e));
    return;
  }

  ws.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString());
    } catch {
      return;
    }
    // Vangnet: een fout in de afhandeling van één event (bv. een camera/person-event) mag NOOIT
    // de hele WS-message-handler platleggen — anders vallen ook de eva_voice_query-events stil
    // en werkt spraak niet meer. Elk event wordt geïsoleerd afgehandeld.
    try {
      handleMessage(msg);
    } catch (e) {
      logTimeline('system', `HA-event overgeslagen na fout: ${errMsg(e).slice(0, 160)}`);
    }
  });

  ws.on('pong', () => { ws._alive = true; });
  ws.on('error', (e) => { ws._lastError = errMsg(e); });
  ws.on('close', () => onDisconnected(ws?._lastError || 'verbinding gesloten'));
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'auth_required':
      ws.send(JSON.stringify({ type: 'auth', access_token: config.haToken }));
      break;

    case 'auth_ok':
      onAuthenticated();
      break;

    case 'auth_invalid':
      if (!offlineLogged) logTimeline('system', 'Home Assistant weigert Eva\'s toegang; huiswaarneming en bediening zijn niet beschikbaar.', {
        familyVisible: true, homeAssistantAuthFailure: true, technicalError: msg.message || 'auth_invalid',
      });
      offlineLogged = true; // niet nogmaals loggen bij de close die volgt of bij retries
      try { ws.close(); } catch { /* negeer */ }
      break;

    case 'result': {
      const p = pending.get(msg.id);
      if (!p) break;
      pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.success) p.resolve(msg.result);
      else p.reject(new Error(msg.error?.message || 'WS-commando mislukt'));
      break;
    }

    case 'event':
      if (msg.event?.event_type === 'state_changed') onStateChanged(msg.event.data);
      else if (msg.event?.event_type === 'eva_voice_query') {
        bus.emit('voice_query', msg.event.data || {});
      }
      break;
  }
}

/** Stuur een WS-commando en wacht op het result. Ook voor beheer (admin-API). */
export function wsCommand(payload, timeoutMs = 10_000) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return reject(new Error('WS niet verbonden'));
    const id = msgId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error('WS-commando timeout'));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ id, ...payload }));
  });
}

/** Lees de door Home Assistant beheerde personenconfiguratie. De gewone
 * person.*-state toont wel welke tracker momenteel wint, maar niet betrouwbaar
 * welke trackers aan de persoon gekoppeld zijn. Die broninformatie leeft in de
 * storage collection achter de officiële `person/list` WebSocket-opdracht. */
export async function listPersonConfigurations() {
  const result = await wsCommand({ type: 'person/list' });
  const storage = Array.isArray(result?.storage) ? result.storage : [];
  const configPersons = Array.isArray(result?.config) ? result.config : [];
  return {
    storage: storage.map((person) => ({
      id: String(person?.id || ''),
      name: String(person?.name || ''),
      device_trackers: Array.isArray(person?.device_trackers)
        ? person.device_trackers.map(String)
        : [],
      editable: true,
    })),
    config: configPersons.map((person) => ({
      id: String(person?.id || ''),
      name: String(person?.name || ''),
      device_trackers: Array.isArray(person?.device_trackers)
        ? person.device_trackers.map(String)
        : [],
      editable: false,
    })),
  };
}

function normalizePersonLookup(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/^person\./, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** Vervang de trackerbronnen van exact één bewerkbare HA-persoon en verifieer
 * daarna zowel de storageconfig als de zichtbare person.*-state. Deze functie
 * raadt nooit op een gedeeltelijke naam en accepteert alleen bestaande
 * device_tracker-entiteiten. */
export async function setPersonTrackers(personQuery, deviceTrackers) {
  const query = normalizePersonLookup(personQuery);
  const requested = [...new Set((Array.isArray(deviceTrackers) ? deviceTrackers : [])
    .map((entityId) => String(entityId || '').trim().toLowerCase())
    .filter(Boolean))];
  if (!query) return { ok: false, changed: false, verified: false, reason: 'persoon ontbreekt' };
  if (!requested.length) {
    return { ok: false, changed: false, verified: false, reason: 'minstens één aanwezigheidstracker is vereist' };
  }
  const invalid = requested.filter((entityId) => !/^device_tracker\.[a-z0-9_]+$/.test(entityId));
  if (invalid.length) {
    return { ok: false, changed: false, verified: false, reason: `ongeldige device_tracker: ${invalid.join(', ')}` };
  }
  const missing = requested.filter((entityId) => !states.has(entityId));
  if (missing.length) {
    return { ok: false, changed: false, verified: false, reason: `tracker bestaat niet in Home Assistant: ${missing.join(', ')}` };
  }

  const before = await listPersonConfigurations();
  const all = [...before.storage, ...before.config];
  const exact = all.filter((person) => {
    const id = normalizePersonLookup(person.id);
    const name = normalizePersonLookup(person.name);
    return query === id || query === name;
  });
  if (exact.length !== 1) {
    return {
      ok: false,
      changed: false,
      verified: false,
      reason: exact.length ? 'persoon is niet eenduidig' : `persoon ${personQuery} niet gevonden`,
      candidates: all.map((person) => ({ id: person.id, name: person.name, editable: person.editable })),
    };
  }
  const person = exact[0];
  if (!person.editable) {
    return { ok: false, changed: false, verified: false, reason: `${person.name} wordt via YAML beheerd en is niet via de API bewerkbaar` };
  }
  const oldTrackers = [...person.device_trackers].sort();
  const newTrackers = [...requested].sort();
  if (JSON.stringify(oldTrackers) === JSON.stringify(newTrackers)) {
    return {
      ok: true,
      changed: false,
      verified: true,
      person: { id: person.id, name: person.name },
      old_trackers: oldTrackers,
      device_trackers: newTrackers,
      message: `${person.name} gebruikte deze aanwezigheidstracker al.`,
    };
  }

  await wsCommand({
    type: 'person/update',
    person_id: person.id,
    device_trackers: newTrackers,
  });
  const after = await listPersonConfigurations();
  const stored = after.storage.find((candidate) => candidate.id === person.id);
  const verifiedTrackers = [...(stored?.device_trackers || [])].sort();
  const verified = JSON.stringify(verifiedTrackers) === JSON.stringify(newTrackers);
  const personState = states.get(`person.${person.id}`) || null;
  return {
    ok: verified,
    changed: verified,
    verified,
    person: { id: person.id, name: person.name },
    old_trackers: oldTrackers,
    device_trackers: verifiedTrackers,
    current_state: personState?.state || null,
    current_source: personState?.attributes?.source || null,
    reason: verified ? null : 'Home Assistant bevestigde de nieuwe trackerlijst niet',
    message: verified
      ? `${person.name} gebruikt nu ${verifiedTrackers.join(', ')} voor aanwezigheid; ${oldTrackers.filter((id) => !verifiedTrackers.includes(id)).join(', ') || 'geen oude tracker'} is niet meer gekoppeld.`
      : '',
  };
}

async function onAuthenticated() {
  connected = true;
  backoff = BACKOFF_START;
  if (offlineLogged) logTimeline('system', 'Weer verbonden met Home Assistant', { internalOnly: true, homeAssistantConnection: true });
  else logTimeline('system', 'Verbonden met Home Assistant', { internalOnly: true, homeAssistantConnection: true });
  offlineLogged = false;
  bus.emit('ha_connected', { ts: Date.now() });

  // keepalive: ping elke 30s, geen pong = verbinding kapot
  ws._alive = true;
  clearInterval(pingTimer);
  pingTimer = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (!ws._alive) { try { ws.terminate(); } catch { /* negeer */ } return; }
    ws._alive = false;
    try { ws.ping(); } catch { /* negeer */ }
  }, 30_000);

  try {
    await wsCommand({ type: 'subscribe_events', event_type: 'state_changed' });
    await wsCommand({ type: 'subscribe_events', event_type: 'eva_voice_query' });
    const all = await wsCommand({ type: 'get_states' }, 20_000);
    states.clear();
    for (const st of all) states.set(st.entity_id, st);
    // Live events kunnen tijdens downtime zijn gemist. Hogere lagen krijgen na
    // iedere reconnect één complete, actuele bronfoto om hun gewapende wachters
    // te verzoenen zonder zelf te gaan pollen.
    bus.emit('ha_snapshot_ready', { ts: Date.now(), states: all });
  } catch (e) {
    logTimeline('system', `Home Assistant: initialisatie mislukt (${errMsg(e)})`);
  }
}

function onDisconnected(reason) {
  const wasConnected = connected;
  connected = false;
  clearInterval(pingTimer);
  for (const [id, p] of pending) {
    clearTimeout(p.timer);
    p.reject(new Error('WS-verbinding verbroken'));
    pending.delete(id);
  }
  ws = null;
  if ((wasConnected || !offlineLogged) && started) {
    logTimeline('system', `Home Assistant onbereikbaar (${reason}); ik blijf het proberen`, {
      internalOnly: true, homeAssistantConnection: true,
    });
    offlineLogged = true;
  }
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(openSocket, backoff);
  backoff = Math.min(backoff * 2, BACKOFF_MAX);
}

// ---------- state-changes verwerken ----------

function onStateChanged({ entity_id, old_state, new_state }) {
  if (!entity_id) return;
  if (new_state) states.set(entity_id, new_state);
  else states.delete(entity_id);
  if (!old_state || !new_state) return;

  const stateChanged = old_state.state !== new_state.state;
  // Klimaat-entiteiten bewaren hun gemeten temperatuur als attribuut terwijl
  // hun hoofdstatus gewoon "heat" blijft. Ook zo'n echte meetverandering moet
  // een event-gestuurde bewaking kunnen wekken.
  const observedAttributeKeys = [
    'current_temperature', 'temperature', 'current_humidity', 'humidity',
    'illuminance', 'battery_level',
  ];
  const observedAttributesChanged = observedAttributeKeys.some((key) =>
    old_state.attributes?.[key] !== new_state.attributes?.[key]);
  if (!stateChanged && !observedAttributesChanged) return;

  const naam = friendlyName(new_state);
  const changedAt = new_state.last_updated || new_state.last_changed || new Date().toISOString();
  const correlationId = `ha:${entity_id}:${changedAt}`;

  // Eén generieke zintuigstroom voor hogere lagen. Opvolging, projectregie en
  // leren hoeven Home Assistant zo niet opnieuw te pollen of zelf alle
  // entiteitstypen te kennen: ze kunnen wachten tot de adapter een echte
  // verandering meldt en daarna alleen gericht verifiëren.
  bus.emit('world_signal', {
    source: 'home_assistant',
    type: 'entity_state',
    entityId: entity_id,
    domain: entity_id.split('.')[0] || null,
    friendlyName: naam,
    correlationId,
    oldState: old_state.state,
    newState: new_state.state,
    changedAt,
    attributes: {
      deviceClass: new_state.attributes?.device_class || null,
      sourceType: new_state.attributes?.source_type || null,
      unit_of_measurement: new_state.attributes?.unit_of_measurement || null,
      current_temperature: new_state.attributes?.current_temperature ?? null,
      previous_current_temperature: old_state.attributes?.current_temperature ?? null,
      temperature: new_state.attributes?.temperature ?? null,
      previous_temperature: old_state.attributes?.temperature ?? null,
      current_humidity: new_state.attributes?.current_humidity ?? null,
      humidity: new_state.attributes?.humidity ?? null,
      illuminance: new_state.attributes?.illuminance ?? null,
      battery_level: new_state.attributes?.battery_level ?? null,
    },
  });

  // De specifieke aankomst-, deur- en camera-events hieronder horen alleen bij
  // een echte hoofdstatusovergang, niet bij een los bijgewerkt attribuut.
  if (!stateChanged) return;

  // personen: thuiskomen / weggaan
  if (entity_id.startsWith('person.')) {
    const was = old_state.state === 'home';
    const is = new_state.state === 'home';
    if (was !== is) {
      const text = is ? `${naam} is thuisgekomen 🏠` : `${naam} is weggegaan`;
      logTimeline('house', text);
      bus.emit('house', {
        type: is ? 'arrival' : 'departure',
        source: 'home_assistant',
        entityId: entity_id,
        correlationId,
        person: naam,
        oldState: old_state.state,
        newState: new_state.state,
        changedAt: new_state.last_changed || new Date().toISOString(),
        text,
      });
    }
    return;
  }

  // zon op/onder
  if (entity_id === 'sun.sun') {
    if (new_state.state === 'above_horizon' || new_state.state === 'below_horizon') {
      const text = new_state.state === 'above_horizon' ? 'De zon is opgekomen ☀️' : 'De zon is ondergegaan 🌙';
      logTimeline('house', text);
      bus.emit('house', {
        type: 'sun', source: 'home_assistant', entityId: entity_id,
        correlationId, changedAt, text,
      });
    }
    return;
  }

  // deurbel ingedrukt: event.*-entiteit met device_class 'doorbell' (state = tijdstempel)
  if (entity_id.startsWith('event.') && new_state.attributes?.device_class === 'doorbell') {
    const cam = camEntityVoorSensor(entity_id);
    const text = 'Er wordt aangebeld! 🔔';
    const accepted = captureAndEmit({
      type: 'doorbell',
      camera: cam,
      sensor: entity_id,
      correlationId,
      text,
      priority: true,
      triggerTs: new_state.last_changed || new Date().toISOString(),
    }, cam);
    if (accepted) logTimeline('house', text, { camera: cam });
    return;
  }

  if (entity_id.startsWith('binary_sensor.')) {
    const dc = new_state.attributes?.device_class;

    // deurbel ingedrukt: UniFi Protect exposeert de betrouwbare deurbelknop hier als
    // binary_sensor.g4_doorbell_pro_poe_doorbell (dezelfde entiteit die de HA-automatisering
    // "Deurbel" gebruikt). Reageer alleen op de overgang naar 'on'; niet op vaag
    // event.*-gedrag, zodat ik niet ten onrechte zeg dat er is aangebeld.
    if (/_doorbell$/.test(entity_id) && old_state.state !== 'on' && new_state.state === 'on') {
      const cam = camEntityVoorSensor(entity_id);
      const text = 'Er wordt aangebeld! 🔔';
      const accepted = captureAndEmit({
        type: 'doorbell',
        camera: cam,
        sensor: entity_id,
        correlationId,
        text,
        priority: true,
        triggerTs: new_state.last_changed || new Date().toISOString(),
      }, cam);
      if (accepted) logTimeline('house', text, { camera: cam });
      return;
    }

    // deuren: alleen op de bus (timeline zou spam worden)
    if ((dc === 'door' || dc === 'garage_door') && old_state.state === 'off' && new_state.state === 'on') {
      bus.emit('house', {
        type: 'door', source: 'home_assistant', entityId: entity_id,
        correlationId, changedAt, text: `De ${naam} is opengegaan`,
      });
      return;
    }

    // iemand gezien door een camera (UniFi person_detected): off -> on.
    // Let op de optionele _N-suffix: UniFi noemt de tweede camera op één toestel
    // bv. g5_pro_person_detected_2 (tuin) — zonder die suffix mee te nemen triggerde die nooit.
    if (/_person_detected(_\d+)?$/.test(entity_id) && old_state.state === 'off' && new_state.state === 'on') {
      const cam = camEntityVoorSensor(entity_id);
      const waar = (naam || '').replace(/person detected/i, '').trim() || 'een camera';
      logTimeline('house', `Camera zag mogelijk een persoon bij ${waar}`, {
        internalOnly: true,
        camera: cam,
        sensor: entity_id,
        personDetected: true,
      });
      captureAndEmit({
        type: 'person_detected',
        camera: cam,
        sensor: entity_id,
        correlationId,
        waar,
        text: `Ik zie iemand bij ${waar}`,
        priority: true,
        triggerTs: new_state.last_changed || new Date().toISOString(),
        detection: {
          source: 'unifi_protect',
          filter: 'person_detected',
          object: 'person',
          sensor: entity_id,
          startedAt: new_state.last_changed || null,
        },
      }, cam);
      return;
    }
  }
}

/** Raad de camera-entiteit die bij een sensor/event hoort op basis van de gedeelde naamstam.
 *  Suffix-bewust: een sensor met _N (bv. g5_pro_person_detected_2 = tuin) hoort bij de camera met
 *  diezelfde _N (g5_pro_high_resolution_channel_2), zodat de twee G5's (garage vs tuin) niet
 *  verwisseld raken. Een sensor zónder suffix hoort juist bij de camera-variant zónder _N. */
function camEntityVoorSensor(sensorEntity) {
  const naam = sensorEntity.split('.')[1];
  const suffix = (naam.match(/_(\d+)$/) || [])[1] || ''; // '' of bv '2'
  // strip de numerieke suffix én het trailing sensortype, zodat de stam het toestel is
  const stam = naam
    .replace(/_(\d+)$/, '')
    .replace(/_(doorbell|motion|person_detected|animal_detected|vehicle_detected)$/, '');
  const cams = [...states.keys()].filter((id) => id.startsWith('camera.') && id.includes(stam) && !/insecure/.test(id));
  // camera's met dezelfde suffix-positie als de sensor: _N → eindigt op _N; geen suffix → geen _N op het eind
  const zelfde = cams.filter((id) => (suffix ? new RegExp(`_${suffix}$`).test(id) : !/_\d+$/.test(id)));
  const pool = zelfde.length ? zelfde : cams;
  return pool.find((id) => /high_resolution/.test(id)) || pool[0] || null;
}

/** Leg het beeld METEEN bij de trigger vast (snapshots zijn snel) en emit dan pas,
 *  zodat de foto de persoon toont op het moment van aanbellen/bewegen — niet 15s later. */
async function captureTriggerFrame(event, cam, { index = 0, total = 1 } = {}) {
  const shot = await cameraSnapshot(cam);
  const contentType = shot.contentType || 'image/jpeg';
  const capturedTs = new Date().toISOString();
  const cameraName = shot.cameraName || friendlyName(states.get(cam)) || cam;
  let file = null;
  try {
    await fs.mkdir(TRIGGER_FRAME_DIR, { recursive: true });
    const naam = safeFilePart(cameraName);
    const stamp = capturedTs.replace(/[:.]/g, '-');
    const burst = total > 1 ? `_b${index + 1}-van-${total}` : '';
    file = path.join(TRIGGER_FRAME_DIR, `${stamp}_${naam}_${safeFilePart(event.type)}${burst}.${imageExt(contentType)}`);
    await fs.writeFile(file, shot.buffer);
  } catch (e) {
    logTimeline('system', `Triggerbeeld kon niet worden opgeslagen voor ${cam}: ${errMsg(e).slice(0, 120)}`);
  }
  return {
    buffer: shot.buffer,
    contentType,
    entity: cam,
    path: file,
    bytes: shot.buffer.length,
    source: shot.source || null,
    cameraName,
    capturedTs,
    triggerTs: event.triggerTs || null,
    burstIndex: index,
    burstTotal: total,
    temporary: true,
    detection: event.detection || null,
  };
}

function captureAndEmit(event, cam) {
  const admission = cameraIncidentDecision(event);
  if (!admission.accepted) {
    const label = event.type === 'doorbell' ? 'Dubbel deurbel-signaal' : 'Protect-detectie';
    logTimeline('vision', `${label} samengevoegd met lopend camera-incident bij ${event.waar || cam || event.sensor || 'de camera'}.`, {
      internalOnly: true,
      cameraIncidentCoalesced: true,
      camera: cam || null,
      sensor: event.sensor || null,
      incident: admission.incident,
      quietMs: admission.quietMs,
    });
    return false;
  }
  if (admission.incident) {
    event.incident = admission.incident;
    scheduleTriggerFramePrune();
  }

  (async () => {
    if (cam) {
      const burstDelays = event.type === 'person_detected' ? [0, 700, 1600] : [0];
      const frames = [];
      for (let i = 0; i < burstDelays.length; i += 1) {
        if (burstDelays[i]) await delay(burstDelays[i]);
        try {
          frames.push(await captureTriggerFrame(event, cam, { index: i, total: burstDelays.length }));
        } catch (e) {
          logTimeline('system', `Geen triggerbeeld ${i + 1}/${burstDelays.length} voor ${cam}: ${errMsg(e).slice(0, 120)}`);
        }
      }
      if (frames.length) {
        event.frame = frames[0];
        event.frames = frames;
      }
    }
    bus.emit('house', event);
  })();
  return true;
}

// ---------- snapshot van het huis ----------

const OPEN_CLASSES = ['door', 'window', 'garage_door', 'opening'];

/** Compacte samenvatting van de huidige staat van het huis. */
export async function snapshot() {
  const all = await allStates();
  const byDomain = (d) => all.filter((s) => s.entity_id.startsWith(d + '.'));

  const personen = byDomain('person').map((s) => ({ naam: friendlyName(s), thuis: s.state === 'home' }));

  const lampenAan = byDomain('light')
    .filter((s) => s.state === 'on')
    .slice(0, 10)
    .map(friendlyName);

  const spelers = byDomain('media_player')
    .filter((s) => s.state === 'playing')
    .map((s) => ({ naam: friendlyName(s), status: s.state, titel: s.attributes?.media_title || null }));

  const klimaat = byDomain('climate').map((s) => ({
    entityId: s.entity_id,
    naam: friendlyName(s),
    huidigeTemp: s.attributes?.current_temperature ?? null,
    doelTemp: s.attributes?.temperature ?? null,
  }));

  const w = byDomain('weather')[0];
  const weer = w ? { conditie: w.state, temperatuur: w.attributes?.temperature ?? null } : null;

  const zon = all.find((s) => s.entity_id === 'sun.sun')?.state === 'below_horizon' ? 'onder' : 'op';

  const binSensors = byDomain('binary_sensor');
  const deurenOpen = binSensors
    .filter((s) => OPEN_CLASSES.includes(s.attributes?.device_class) && s.state === 'on')
    .map(friendlyName);
  const bewegingRecent = binSensors
    .filter((s) => s.attributes?.device_class === 'motion' && s.state === 'on')
    .map(friendlyName);

  return { personen, lampenAan, spelers, klimaat, weer, zon, deurenOpen, bewegingRecent };
}

// vertaling van veelvoorkomende weerstates naar het Nederlands
const WEER_NL = {
  'clear-night': 'helder',
  cloudy: 'bewolkt',
  exceptional: 'uitzonderlijk weer',
  fog: 'mistig',
  hail: 'hagel',
  lightning: 'onweer',
  'lightning-rainy': 'onweer met regen',
  partlycloudy: 'half bewolkt',
  pouring: 'stortregen',
  rainy: 'regenachtig',
  snowy: 'sneeuw',
  'snowy-rainy': 'natte sneeuw',
  sunny: 'zonnig',
  windy: 'winderig',
  'windy-variant': 'winderig',
};

/** Korte Nederlandse beschrijving van een snapshot, voor in een LLM-prompt. */
export function snapshotText(snap) {
  const zinnen = [];

  const thuis = snap.personen?.filter((p) => p.thuis).map((p) => p.naam) || [];
  zinnen.push(thuis.length ? `Thuis: ${thuis.join(', ')}.` : 'Er is niemand thuis.');

  const lampen = snap.lampenAan || [];
  if (lampen.length) zinnen.push(`${lampen.length} lamp${lampen.length === 1 ? '' : 'en'} aan (${lampen.join(', ')}).`);
  else zinnen.push('Alle lampen zijn uit.');

  for (const sp of snap.spelers || []) {
    zinnen.push(sp.titel ? `${sp.naam} speelt '${sp.titel}'.` : `${sp.naam} speelt iets af.`);
  }

  if (snap.weer) {
    const cond = WEER_NL[snap.weer.conditie] || snap.weer.conditie;
    zinnen.push(snap.weer.temperatuur != null ? `Het is ${snap.weer.temperatuur}°C en ${cond}.` : `Het weer: ${cond}.`);
  }

  zinnen.push(snap.zon === 'op' ? 'De zon is op.' : 'De zon is onder.');

  if (snap.deurenOpen?.length) zinnen.push(`Open: ${snap.deurenOpen.join(', ')}.`);
  if (snap.bewegingRecent?.length) zinnen.push(`Beweging bij: ${snap.bewegingRecent.join(', ')}.`);

  const binnen = (snap.klimaat || []).filter((k) => k.huidigeTemp != null).slice(0, 10);
  if (binnen.length) {
    zinnen.push(`Binnentemperaturen: ${binnen.map((k) => (
      `${k.naam} ${k.huidigeTemp}°C${k.entityId ? ` (${k.entityId})` : ''}`
    )).join(', ')}.`);
  }

  return zinnen.join(' ');
}

/** Namen van iedereen die nu thuis is (person.* met state 'home'). */
export async function whoIsHome() {
  const all = await allStates();
  return all.filter((s) => s.entity_id.startsWith('person.') && s.state === 'home').map(friendlyName);
}

/** Aanwezigheid met de werkelijk gekoppelde tracker- en telefoonbronnen. */
export async function presenceDetails(person = '') {
  const all = await allStates();
  const wanted = String(person || '').trim().toLocaleLowerCase('nl-NL');
  const people = all.filter((state) => {
    if (!state.entity_id.startsWith('person.')) return false;
    if (!wanted) return true;
    const id = state.entity_id.slice('person.'.length).replace(/[_-]+/g, ' ');
    const name = friendlyName(state).toLocaleLowerCase('nl-NL');
    return name === wanted || id === wanted || name.includes(wanted);
  });

  return people.map((state) => {
    const name = friendlyName(state);
    const nameKey = name.toLocaleLowerCase('nl-NL');
    const trackerIds = new Set([
      ...(Array.isArray(state.attributes?.device_trackers) ? state.attributes.device_trackers : []),
      state.attributes?.source,
    ].filter(Boolean));
    const sources = all.filter((candidate) => {
      if (trackerIds.has(candidate.entity_id)) return true;
      if (!/^(?:device_tracker|sensor|binary_sensor)\./.test(candidate.entity_id)) return false;
      const label = friendlyName(candidate).toLocaleLowerCase('nl-NL');
      if (!label.includes(nameKey)) return false;
      return candidate.entity_id.startsWith('device_tracker.')
        || /\b(?:connection|verbinding|ssid|bssid|wi-?fi|wifi|network|netwerk)\b/i
          .test(`${candidate.entity_id} ${label}`);
    }).map((candidate) => ({
      entity_id: candidate.entity_id,
      state: candidate.state,
      last_changed: candidate.last_changed || null,
      last_updated: candidate.last_updated || null,
      source_type: candidate.attributes?.source_type || null,
      battery_level: candidate.attributes?.battery_level ?? null,
    }));
    return {
      person: name,
      entity_id: state.entity_id,
      state: state.state,
      source: state.attributes?.source || null,
      last_changed: state.last_changed || null,
      last_updated: state.last_updated || null,
      sources,
    };
  });
}

// ---------- REST: services, states, camera's ----------

/** Roept een HA-service aan via REST en geeft het JSON-antwoord terug. */
export async function callService(domain, service, data = {}, { timeoutMs = 10_000, target = null } = {}) {
  if (connected) {
    try {
      return await wsCommand({
        type: 'call_service',
        domain,
        service,
        service_data: data || {},
        ...(target ? { target } : {}),
        return_response: false,
      }, Math.min(Math.max(500, Number(timeoutMs) || 10_000), 3_000));
    } catch (error) {
      if (domain === 'assist_satellite' && service === 'announce') {
        error.uncertainExecution = true;
        throw error;
      }
      // REST blijft het vangnet wanneer de live verbinding net wegvalt.
    }
  }
  const res = await fetch(`${config.haUrl}/api/services/${domain}/${service}`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(target?.entity_id
      ? { ...(data || {}), entity_id: target.entity_id }
      : data || {}),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Service ${domain}.${service} mislukt: HTTP ${res.status}${body ? ` — ${body.slice(0, 200)}` : ''}`);
  }
  return res.json();
}

/** Generieke REST-call naar Home Assistant (voor beheer: automatiseringen, scripts, config). */
export async function restCall(method, path, body = null, { timeoutMs = 10_000 } = {}) {
  if (!path.startsWith('/api/')) throw new Error('pad moet met /api/ beginnen');
  const res = await fetch(config.haUrl + path, {
    method,
    headers: headers(),
    body: body != null ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(Math.max(250, Number(timeoutMs) || 10_000)),
  });
  const text = await res.text().catch(() => '');
  if (!res.ok) throw new Error(`HA ${method} ${path} mislukt: HTTP ${res.status}${text ? ` — ${text.slice(0, 300)}` : ''}`);
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Eén entiteit ophalen; null als die niet bestaat. */
export async function getState(entityId, { timeoutMs = 5_000, preferCache = true } = {}) {
  const cached = preferCache ? states.get(String(entityId || '').toLowerCase()) : null;
  if (connected && cached) return cached;
  const res = await fetch(`${config.haUrl}/api/states/${entityId}`, {
    headers: headers(),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HA GET state ${entityId} mislukt: HTTP ${res.status}`);
  return res.json();
}

function readableEntityState(state, fallbackId = '') {
  return {
    entity_id: String(state?.entity_id || fallbackId),
    name: String(state?.attributes?.friendly_name || state?.entity_id || fallbackId),
    state: String(state?.state || 'unknown'),
  };
}

/**
 * Beoordeel een readback op het werkelijke doel. Bij een HA-lichtgroep betekent
 * `on` alleen dat minstens één lid aan is; dat bewijst niet dat alle lampen aanstaan.
 */
export function assessEntityState(target, memberStates = [], expectedState = null) {
  const expected = expectedState ? String(expectedState) : null;
  const memberIds = Array.isArray(target?.attributes?.entity_id)
    ? target.attributes.entity_id.map(String).filter(Boolean)
    : [];
  const byId = new Map((memberStates || []).map((state) => [
    String(state?.entity_id || ''),
    readableEntityState(state),
  ]));
  const members = memberIds.map((id) => byId.get(id) || readableEntityState(null, id));
  const isGroup = members.length > 0;
  const unavailable = isGroup
    ? members.filter((member) => ['unavailable', 'unknown'].includes(member.state))
    : [];
  const reachable = isGroup
    ? members.filter((member) => !['unavailable', 'unknown'].includes(member.state))
    : [];
  const reachableUnresolved = expected
    ? reachable.filter((member) => member.state !== expected)
    : [];
  const unresolved = expected
    ? (isGroup
      ? members.filter((member) => member.state !== expected)
      : (target?.state === expected ? [] : [readableEntityState(target)]))
    : [];
  const ok = !!target && (!expected || unresolved.length === 0);
  const reachableVerified = !!target
    && !!expected
    && isGroup
    && reachable.length > 0
    && reachableUnresolved.length === 0;
  const targetState = String(target?.state || 'unknown');
  const effectiveState = reachableVerified ? expected : targetState;
  const details = unresolved.slice(0, 6).map((member) => `${member.name}=${member.state}`);

  return {
    ok,
    reachableVerified,
    partial: !ok && reachableVerified && unavailable.length > 0,
    entity_id: String(target?.entity_id || ''),
    state: effectiveState,
    target_state: targetState,
    expected,
    isGroup,
    members,
    reachable,
    unavailable,
    unresolved,
    evidence: ok
      ? (isGroup
        ? `${members.length} groepsleden teruggelezen als ${expected}.`
        : `${target?.entity_id || 'entiteit'}=${target?.state || 'unknown'}.`)
      : reachableVerified
        ? `${reachable.length} bereikbare groepsleden teruggelezen als ${expected}; ${unavailable.length} niet bereikbaar (${unavailable.slice(0, 6).map((member) => `${member.name}=${member.state}`).join(', ')})${targetState !== expected ? `; de groepssamenvatting zelf meldde ${targetState}` : ''}.`
        : `Niet volledig bewezen${details.length ? `: ${details.join(', ')}` : '.'}`,
  };
}

async function readEntityAndMembers(entityId, expectedState, timeoutMs, preferCache = true) {
  const target = await getState(entityId, { timeoutMs, preferCache });
  if (!target) return assessEntityState(null, [], expectedState);
  const memberIds = Array.isArray(target.attributes?.entity_id)
    ? target.attributes.entity_id.map(String).filter(Boolean)
    : [];
  const memberStates = await Promise.all(memberIds.map(async (id) => {
    try {
      return await getState(id, { timeoutMs, preferCache });
    } catch {
      return { entity_id: id, state: 'unknown', attributes: { friendly_name: id } };
    }
  }));
  return assessEntityState(target, memberStates, expectedState);
}

/** Lees een entiteit en eventuele groepsleden terug totdat het doel bewezen is. */
export async function verifyEntityState(entityId, expectedState, {
  timeoutMs = 2_200,
  pollMs = 300,
} = {}) {
  const deadline = Date.now() + Math.max(250, Number(timeoutMs) || 2_200);
  let latest = null;
  do {
    const remaining = Math.max(250, deadline - Date.now());
    latest = await readEntityAndMembers(
      entityId,
      expectedState,
      Math.min(1_200, remaining),
      remaining > 800,
    );
    if (latest.ok || latest.reachableVerified || Date.now() >= deadline) return latest;
    await new Promise((resolve) => setTimeout(resolve, Math.min(pollMs, deadline - Date.now())));
  } while (Date.now() < deadline);
  return latest;
}

// Ruis in de camera-lijst: stofzuigerkaarten en dubbele streams.
const CAM_UITSLUITEN = /erik|vacuum|_map|stofzuig/i;
const CAM_KANAAL_SUFFIX = /\s*(high|low)\s*resolution\s*channel.*$|\s*\(insecure\)\s*$/i;
const UNIFI_PROTECT_BASE = '/proxy/protect/integration/v1';
const UNIFI_PROTECT_FALLBACK_HOST = '10.0.20.133';
let unifiProtectCache = { ts: 0, cameras: null };
const unifiProtectMeldingen = new Set();

const schoneCamNaam = (st) =>
  friendlyName(st)
    .replace(CAM_KANAAL_SUFFIX, '')
    .replace(/\s*Package Camera\s*$/i, ' (pakketcam)')
    .replace(/\bPro PoE\b/i, '')
    .replace(/\s+/g, ' ')
    .trim();

/** Alle camera-entiteiten, ongefilterd (voor zoeken op (deel)naam). */
export async function rawCameras() {
  const all = await allStates();
  return all
    .filter((s) => s.entity_id.startsWith('camera.'))
    .map((s) => ({ entity_id: s.entity_id, name: friendlyName(s), schoon: schoneCamNaam(s) }));
}

/**
 * Opgeschoonde lijst van de échte camera's: stofzuigerkaarten eruit, en per
 * fysieke camera één entiteit (hoge resolutie, niet-insecure). Markeert de deurbel.
 */
export async function listCameras() {
  const all = await allStates();
  const cams = all.filter(
    (s) => s.entity_id.startsWith('camera.') && !CAM_UITSLUITEN.test(s.entity_id) && s.state !== 'unavailable'
  );

  // groepeer op schone naam; binnen een groep: voorkeur hoge-res, niet-insecure, niet-pakketcam
  const groepen = new Map();
  for (const s of cams) {
    const basis = schoneCamNaam(s).replace(/\s*\(pakketcam\)\s*$/i, '').trim();
    (groepen.get(basis) || groepen.set(basis, []).get(basis)).push(s);
  }
  const score = (s) => {
    const id = s.entity_id.toLowerCase();
    let p = 0;
    if (/high_resolution/.test(id)) p += 4;
    if (/insecure/.test(id)) p -= 3;
    if (/package/.test(id)) p -= 5;
    if (/low_resolution/.test(id)) p -= 1;
    return p;
  };

  const uit = [];
  for (const [basis, lijst] of groepen) {
    lijst.sort((a, b) => score(b) - score(a));
    const beste = lijst[0];
    const isDeurbel = /doorbell|deurbel/i.test(beste.entity_id + ' ' + basis);
    uit.push({
      entity_id: beste.entity_id,
      name: basis,
      isDoorbell: isDeurbel,
      speaker: isDeurbel ? doorbellSpeaker(all) : null,
    });
  }
  // deurbel bovenaan
  uit.sort((a, b) => (b.isDoorbell ? 1 : 0) - (a.isDoorbell ? 1 : 0));
  return uit;
}

/** Vind een entiteit (camera/sensor) terug op deel van entity_id of vriendelijke naam. */
export async function findCamera(zoek) {
  const cams = await rawCameras();
  const z = String(zoek || '').toLowerCase();
  if (!z) {
    const echte = await listCameras();
    return echte[0]?.entity_id || cams[0]?.entity_id || null;
  }
  const aliases = [z];
  if (/\b(deurbel|voordeur|bel)\b/.test(z)) aliases.push('doorbell', 'g4 doorbell');
  if (/\b(tuin|achtertuin|overkapping)\b/.test(z)) aliases.push('g5 tuin');
  if (/\b(garage|oprit|straat)\b/.test(z)) aliases.push('g5 garage');
  const hit =
    cams.find((c) => aliases.some((a) => c.entity_id.toLowerCase().includes(a))) ||
    cams.find((c) => aliases.some((a) => c.name.toLowerCase().includes(a))) ||
    cams.find((c) => aliases.some((a) => c.schoon.toLowerCase().includes(a)));
  return hit?.entity_id || null;
}

function doorbellSpeaker(all) {
  const sp = all.find(
    (s) => s.entity_id.startsWith('media_player.') && /doorbell|deurbel/i.test(s.entity_id)
  );
  return sp?.entity_id || null;
}

/** Speaker-entity van de deurbel, of null. */
export async function getDoorbellSpeaker() {
  return doorbellSpeaker(await allStates());
}

const normCam = (s) =>
  String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();

function secret(sleutel) {
  try { return getSecret(sleutel); } catch { return ''; }
}

function unifiProtectKeys() {
  return [...new Set([secret('unifi_protect_api_key'), config.unifiKey].filter(Boolean))];
}

function unifiProtectHosts() {
  return [...new Set([config.unifiHost, secret('unifi_protect_host'), UNIFI_PROTECT_FALLBACK_HOST].filter(Boolean))];
}

function parseUnifiHost(raw) {
  const s = String(raw || '').trim();
  if (!s) return null;
  try {
    const u = new URL(/^https?:\/\//i.test(s) ? s : `https://${s}`);
    return { hostname: u.hostname, port: Number(u.port || 443) };
  } catch {
    const [hostname] = s.replace(/^https?:\/\//i, '').split('/');
    if (!hostname) return null;
    const [host, port] = hostname.split(':');
    return { hostname: host, port: Number(port || 443) };
  }
}

function unifiProtectRaw(host, key, pathname, accept) {
  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: host.hostname,
      port: host.port,
      method: 'GET',
      path: pathname,
      rejectUnauthorized: false,
      timeout: 10_000,
      headers: { 'X-API-KEY': key, Accept: accept, 'User-Agent': 'Eva/1.0' },
    }, (res) => {
      const chunks = [];
      res.on('data', (d) => chunks.push(d));
      res.on('end', () => {
        const body = Buffer.concat(chunks);
        if ((res.statusCode || 500) < 200 || (res.statusCode || 500) >= 300) {
          const preview = body.toString('utf8').slice(0, 160);
          const e = new Error(`UniFi Protect HTTP ${res.statusCode}: ${preview}`);
          e.statusCode = res.statusCode;
          return reject(e);
        }
        resolve({ body, headers: res.headers });
      });
    });
    req.on('error', reject);
    req.setTimeout(10_000, () => req.destroy(new Error('timeout naar UniFi Protect')));
    req.end();
  });
}

async function unifiProtectBuffer(pathname, { accept = 'application/json' } = {}) {
  const hosts = unifiProtectHosts().map(parseUnifiHost).filter(Boolean);
  const keys = unifiProtectKeys();
  if (!hosts.length || !keys.length) throw new Error('UniFi Protect niet ingesteld');

  let laatsteFout = null;
  for (const host of hosts) {
    for (const key of keys) {
      try {
        return await unifiProtectRaw(host, key, pathname, accept);
      } catch (e) {
        laatsteFout = e;
      }
    }
  }
  throw laatsteFout || new Error('UniFi Protect niet bereikbaar');
}

async function unifiProtectCameras() {
  if (unifiProtectCache.cameras && Date.now() - unifiProtectCache.ts < 60_000) return unifiProtectCache.cameras;
  const { body } = await unifiProtectBuffer(`${UNIFI_PROTECT_BASE}/cameras`);
  const json = JSON.parse(body.toString('utf8'));
  const lijst = Array.isArray(json) ? json : (Array.isArray(json?.data) ? json.data : []);
  const cameras = lijst
    .map((c) => ({ id: c.id, name: c.name || c.displayName || c.id, state: c.state, model: c.type || c.model || c.modelKey || '' }))
    .filter((c) => c.id);
  unifiProtectCache = { ts: Date.now(), cameras };
  return cameras;
}

function unifiNeedlesVoorCamera(entityId) {
  const st = states.get(entityId) || { entity_id: entityId };
  const id = normCam(entityId);
  const naam = normCam(friendlyName(st));
  const schoon = normCam(schoneCamNaam(st));
  const tekst = `${id} ${naam} ${schoon}`;
  const needles = [];

  if (schoon && !/^(camera|g5|g4|g5 pro|g4 doorbell pro poe)$/i.test(schoon)) needles.push(schoon);
  if (/doorbell|deurbel|voordeur|package|pakket/.test(tekst)) needles.push('g4 doorbell', 'doorbell', 'deurbel');
  if (/tuin|overkap|achtertuin/.test(tekst)) needles.push('g5 tuin', 'tuin', 'overkapping');
  if (/garage|oprit|straat/.test(tekst)) needles.push('g5 garage', 'garage', 'oprit');

  // Lokale HA/Protect naamgeving: g5_pro_*_2 is de tuincamera; zonder suffix is de garage/oprit-camera.
  if (/camera[ .]g5 pro/.test(id) && / 2$/.test(id)) needles.push('g5 tuin', 'tuin', 'overkapping');
  else if (/camera[ .]g5 pro/.test(id)) needles.push('g5 garage', 'garage', 'oprit');

  return [...new Set(needles.map(normCam).filter((n) => n.length >= 3))];
}

function kiesUnifiCamera(entityId, cameras) {
  const needles = unifiNeedlesVoorCamera(entityId);
  if (!needles.length) return null;
  for (const needle of needles) {
    const hit = cameras.find((c) => normCam(`${c.name} ${c.id} ${c.model}`).includes(needle));
    if (hit) return hit;
  }
  return null;
}

async function unifiProtectSnapshot(entityId) {
  try {
    const cameras = await unifiProtectCameras();
    const camera = kiesUnifiCamera(entityId, cameras);
    if (!camera) return null;
    const { body, headers } = await unifiProtectBuffer(`${UNIFI_PROTECT_BASE}/cameras/${camera.id}/snapshot`, { accept: 'image/jpeg' });
    const type = String(headers['content-type'] || 'image/jpeg');
    if (!/image/i.test(type) || body.length < 1000 || body[0] !== 0xff || body[1] !== 0xd8) return null;
    return { buffer: body, contentType: type, source: 'unifi-protect', cameraName: camera.name };
  } catch (e) {
    const sleutel = `protect:${entityId}`;
    if (!unifiProtectMeldingen.has(sleutel)) {
      unifiProtectMeldingen.add(sleutel);
      logTimeline('system', `UniFi Protect snapshot voor ${entityId} niet bruikbaar; val terug op Home Assistant (${errMsg(e).slice(0, 140)}).`);
    }
    return null;
  }
}

/** Stilstaand beeld van een camera: eerst direct via UniFi Protect, daarna HA /api/camera_proxy. */
export async function cameraSnapshot(entityId) {
  const direct = await unifiProtectSnapshot(entityId);
  if (direct) return direct;

  let res;
  try {
    res = await fetch(`${config.haUrl}/api/camera_proxy/${entityId}`, { headers: headers() });
  } catch (e) {
    throw new Error(`Camerabeeld van ${entityId} ophalen mislukt: ${errMsg(e)}`);
  }
  if (!res.ok) throw new Error(`Camerabeeld van ${entityId} ophalen mislukt: HTTP ${res.status}`);
  const buffer = Buffer.from(await res.arrayBuffer());
  return { buffer, contentType: res.headers.get('content-type') || 'image/jpeg', source: 'home-assistant' };
}

export async function listMediaPlayers() {
  const all = await allStates();
  return all
    .filter((s) => s.entity_id.startsWith('media_player.'))
    .map((s) => ({ entity_id: s.entity_id, name: friendlyName(s), state: s.state }));
}

// ---------- spraak door het huis ----------

const speelbareSpelers = (all) =>
  all.filter((s) => s.entity_id.startsWith('media_player.') && !['unavailable', 'unknown'].includes(s.state));

/** Ontdubbel spelers op vriendelijke naam (Sonos/Cast komen vaak dubbel voor). */
function dedupe(spelers) {
  const gezien = new Set();
  const uit = [];
  for (const s of spelers) {
    const sleutel = friendlyName(s).toLowerCase().replace(/\s*\d+$/, '');
    if (gezien.has(sleutel)) continue;
    gezien.add(sleutel);
    uit.push(s);
  }
  return uit;
}

/** Kies standaard-spelers: beschikbaar, voorkeur voor Sonos/Cast, ontdubbeld, max 3. */
function defaultSpeakers(all) {
  const speelbaar = speelbareSpelers(all).filter((s) => !/doorbell|deurbel/i.test(s.entity_id));
  // Music Assistant-groepen en hun fysieke spelers tegelijk aanroepen kan één
  // korte zin dubbel starten en de HA-call laten wachten. Kies fysieke spelers
  // wanneer die er zijn; gebruik een groep alleen als echte spelers ontbreken.
  const fysiek = speelbaar.filter((s) => String(s.attributes?.mass_player_type || '').toLowerCase() !== 'group');
  const kandidaten = fysiek.length ? fysiek : speelbaar;
  const isVoorkeur = (s) => /sonos|chromecast|cast|gc\b/i.test(s.entity_id + ' ' + friendlyName(s));
  const voorkeur = kandidaten.filter(isVoorkeur);
  return dedupe(voorkeur.length ? voorkeur : kandidaten).slice(0, 3).map((s) => s.entity_id);
}

function serviceTimeoutIsAmbiguous(error) {
  return /(?:timeout|timed out|aborted due to timeout|operation was aborted)/i.test(errMsg(error));
}

function normalizedVoiceTarget(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9.]+/g, ' ')
    .trim();
}

/**
 * Resolve a requested/default target only inside the configured allowlist.
 * There is deliberately no fuzzy media-player fallback in this contract.
 */
export function resolveVoiceAssistSatelliteTarget(requestedTarget = null, {
  allowedTargets = config.voiceAssistSatelliteTargets,
  defaultTarget = config.voiceAssistSatelliteDefault,
} = {}) {
  const allowed = (Array.isArray(allowedTargets) ? allowedTargets : [])
    .map((target) => ({
      alias: String(target?.alias || '').trim(),
      entityId: String(target?.entityId || '').trim().toLowerCase(),
    }))
    .filter((target) => /^assist_satellite\.[a-z0-9_]+$/.test(target.entityId));
  if (!allowed.length) {
    return { ok: false, reason: 'geen toegestane Voice PE-doelen geconfigureerd' };
  }

  const explicit = String(requestedTarget || '').trim();
  const selected = explicit || String(defaultTarget || '').trim();
  if (!selected) {
    return { ok: false, reason: 'geen Voice PE-doel opgegeven en geen standaarddoel geconfigureerd' };
  }
  const key = normalizedVoiceTarget(selected);
  let matches = allowed.filter((target) => (
    normalizedVoiceTarget(target.entityId) === key
    || normalizedVoiceTarget(target.alias) === key
  ));
  if (!matches.length && key) {
    const padded = ` ${key} `;
    matches = allowed.filter((target) => {
      const alias = normalizedVoiceTarget(target.alias);
      return alias && padded.includes(` ${alias} `);
    });
  }
  if (matches.length !== 1) {
    return {
      ok: false,
      requested: selected,
      reason: matches.length > 1
        ? `Voice PE-doel "${selected}" is dubbelzinnig`
        : `Voice PE-doel "${selected}" is niet bekend; kies ${allowed.map((target) => target.alias).join(' of ')}`,
    };
  }
  return {
    ok: true,
    requested: selected,
    source: explicit ? 'explicit' : 'default',
    alias: matches[0].alias,
    entityId: matches[0].entityId,
  };
}

/** Pure serviceplanning, apart testbaar zonder Home Assistant of geluid. */
export function voiceAssistSatelliteAnnouncementPlan(text, requestedTarget = null, options = {}) {
  const message = String(text || '').replace(/\s+/g, ' ').trim();
  if (!message) return { ok: false, reason: 'de omroeptekst is leeg' };
  const target = resolveVoiceAssistSatelliteTarget(requestedTarget, options);
  if (!target.ok) return target;
  return {
    ok: true,
    target,
    domain: 'assist_satellite',
    service: 'announce',
    serviceTarget: { entity_id: target.entityId },
    data: {
      message,
      preannounce: false,
    },
  };
}

export const __speechTest = {
  defaultSpeakers,
  resolveVoiceAssistSatelliteTarget,
  serviceTimeoutIsAmbiguous,
  voiceAssistSatelliteAnnouncementPlan,
};

/**
 * Omroep via exact één toegestane Assist satellite. Een expliciet of default
 * Voice PE-doel valt bij geen enkele fout terug op Sonos, Cast of versterkers.
 */
export async function announceOnVoiceAssistSatellite(text, requestedTarget = null, options = {}) {
  const plan = voiceAssistSatelliteAnnouncementPlan(text, requestedTarget, options);
  if (!plan.ok) {
    return { ...plan, verified: false, changed: false };
  }
  const targetLabel = plan.target.alias || plan.target.entityId;
  const attemptStartedAt = new Date(Date.now() - 1_000).toISOString();
  try {
    const currentStates = Array.isArray(options.states) ? options.states : await allStates();
    let targetState = currentStates.find((state) => state.entity_id === plan.target.entityId);
    if (!targetState) {
      return {
        ok: false,
        verified: false,
        changed: false,
        target: plan.target.entityId,
        target_alias: plan.target.alias,
        service: `${plan.domain}.${plan.service}`,
        reason: `Voice PE-doel ${targetLabel} bestaat niet in Home Assistant`,
      };
    }
    if (['unavailable', 'unknown'].includes(String(targetState.state))) {
      const refreshState = typeof options.refreshState === 'function'
        ? options.refreshState
        : !Array.isArray(options.states)
          ? (entityId) => restGet(`/api/states/${encodeURIComponent(entityId)}`)
          : null;
      const fresh = refreshState
        ? await refreshState(plan.target.entityId).catch(() => null)
        : null;
      if (fresh?.entity_id === plan.target.entityId
        && !['unavailable', 'unknown'].includes(String(fresh.state))) {
        targetState = fresh;
      } else {
        return {
          ok: false,
          verified: true,
          changed: false,
          target: plan.target.entityId,
          target_alias: plan.target.alias,
          service: `${plan.domain}.${plan.service}`,
          reason: `Home Assistant markeert Voice PE ${targetLabel} als ${fresh?.state || targetState.state}`,
        };
      }
    }

    const invoke = typeof options.callService === 'function' ? options.callService : callService;
    await invoke(plan.domain, plan.service, plan.data, { target: plan.serviceTarget });
    return {
      ok: true,
      verified: true,
      changed: true,
      accepted: true,
      target: plan.target.entityId,
      target_alias: plan.target.alias,
      target_source: plan.target.source,
      service: `${plan.domain}.${plan.service}`,
      message: `De omroepopdracht is door Home Assistant geaccepteerd voor ${targetLabel}.`,
    };
  } catch (error) {
    const uncertain = error?.uncertainExecution === true || serviceTimeoutIsAmbiguous(error);
    if (uncertain) {
      const verify = typeof options.verifyAnnouncement === 'function'
        ? options.verifyAnnouncement
        : async (entityId, since) => {
          // HA kan de service al gestart hebben terwijl de WS-bevestiging en
          // logbook-index nog enkele tellen achterlopen. Wacht kort op dat
          // bewijs; stuur dezelfde hoorbare omroep niet blind nog een keer.
          for (let attempt = 0; attempt < 4; attempt += 1) {
            const rows = await restGet(`/api/logbook/${encodeURIComponent(since)}?entity=${encodeURIComponent(entityId)}`);
            if (Array.isArray(rows) && rows.some((row) => (
              row?.context_domain === 'assist_satellite'
              && row?.context_service === 'announce'
            ))) return true;
            if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, 750));
          }
          return false;
        };
      const confirmed = await verify(plan.target.entityId, attemptStartedAt)
        .catch(() => false);
      if (confirmed) {
        return {
          ok: true,
          verified: true,
          changed: true,
          accepted: true,
          reconciled: true,
          target: plan.target.entityId,
          target_alias: plan.target.alias,
          service: `${plan.domain}.${plan.service}`,
          message: `De omroep is aantoonbaar gestart op ${targetLabel}.`,
        };
      }
    }
    return {
      ok: false,
      verified: false,
      changed: false,
      uncertain,
      target: plan.target.entityId,
      target_alias: plan.target.alias,
      service: `${plan.domain}.${plan.service}`,
      reason: uncertain
        ? `De omroep naar ${targetLabel} bleef onbevestigd; er is niet naar een andere speaker uitgeweken.`
        : `De omroep naar ${targetLabel} is mislukt: ${errMsg(error)}`,
    };
  }
}

/** Alle bruikbare speakers (ontdubbeld), zonder de deurbel. Voor "overal praten". */
export async function allSpeakers() {
  const all = await allStates();
  return dedupe(speelbareSpelers(all).filter((s) => !/doorbell|deurbel/i.test(s.entity_id))).map((s) => s.entity_id);
}

// ---------- kamer-routering: praten waar iemand is ----------

// Kamer (area) -> media_player-entity_ids, uit de HA area-registry. Gecachet (5 min);
// zo weet Eva welke speaker in welke kamer staat, ook als de naam dat niet verraadt.
let _areaMap = null, _areaMapTs = 0, _entArea = null;
async function areaSpeakerMap() {
  if (_areaMap && Date.now() - _areaMapTs < 5 * 60_000) return _areaMap;
  try {
    const [areas, ents, devs] = await Promise.all([
      wsCommand({ type: 'config/area_registry/list' }),
      wsCommand({ type: 'config/entity_registry/list' }),
      wsCommand({ type: 'config/device_registry/list' }),
    ]);
    const areaNaam = Object.fromEntries(areas.map((a) => [a.area_id, a.name]));
    const devArea = Object.fromEntries(devs.map((d) => [d.id, d.area_id]));
    const map = {};
    const entArea = {};
    for (const e of ents) {
      const aid = e.area_id || devArea[e.device_id];
      if (aid && areaNaam[aid]) entArea[e.entity_id] = areaNaam[aid];
      if (!e.entity_id.startsWith('media_player.') || !aid || !areaNaam[aid]) continue;
      (map[areaNaam[aid].toLowerCase()] ||= []).push(e.entity_id);
    }
    _areaMap = map; _entArea = entArea; _areaMapTs = Date.now();
    return map;
  } catch {
    return _areaMap || {};
  }
}

/** In welke kamer hoort een entiteit (camera/sensor/speaker)? Of null. */
export async function areaOfEntity(entityId) {
  await areaSpeakerMap();
  return (_entArea && _entArea[entityId]) || null;
}

// Hoe het gezin kamers noemt vs. hoe ze in HA heten.
const KAMER_SYNONIEM = { tuin: 'buiten', achtertuin: 'buiten', voortuin: 'buiten', overkapping: 'buiten', garage: 'schuur' };

/** Beschikbare, voorkeurs-gesorteerde speakers in een kamer (lege array = onbekend/leeg). */
export async function speakersInRoom(room) {
  if (!room) return [];
  const map = await areaSpeakerMap();
  let z = String(room).toLowerCase().trim();
  if (KAMER_SYNONIEM[z]) z = KAMER_SYNONIEM[z];
  let ids = map[z];
  if (!ids) { const k = Object.keys(map).find((n) => n.includes(z) || z.includes(n)); ids = k ? map[k] : []; }
  if (!ids || !ids.length) return [];
  const all = await allStates();
  const speelbaar = new Set(speelbareSpelers(all).filter((s) => !/doorbell|deurbel/i.test(s.entity_id)).map((s) => s.entity_id));
  const inRoom = ids.filter((id) => speelbaar.has(id));
  const voorkeur = inRoom.filter((id) => /sonos|cast|nest|gc\b|google|roam/i.test(id));
  const kies = voorkeur.length ? voorkeur : inRoom;
  return dedupe(kies.map((id) => all.find((s) => s.entity_id === id)).filter(Boolean)).map((s) => s.entity_id);
}

/** Welke kamers hebben (nu) een bruikbare speaker? Zodat Eva weet waar ze kan praten. */
export async function roomsWithSpeakers() {
  const map = await areaSpeakerMap();
  const uit = [];
  for (const naam of Object.keys(map)) if ((await speakersInRoom(naam)).length) uit.push(naam);
  return uit;
}

/** Praat in een specifieke kamer; matcht die niets, val terug op naam-matching/standaard. */
export async function speakInArea(room, text, options = {}) {
  const isCurrent = typeof options?.isCurrent === 'function' ? options.isCurrent : () => true;
  if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
  const ids = await speakersInRoom(room);
  if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
  return ids.length ? speak(text, ids, options) : speak(text, room || null, options);
}

/** Vertaal targets (entity_ids, naamfragmenten, of 'overal'/'alle') naar echte media_player-entity_ids. */
function resolveSpeakers(all, target) {
  if (target == null) return defaultSpeakers(all);
  const lijst = [].concat(target).filter(Boolean);
  if (!lijst.length) return defaultSpeakers(all);
  if (lijst.some((t) => /^(overal|alle|alles|huis|alle speakers)$/i.test(String(t).trim()))) {
    return dedupe(speelbareSpelers(all).filter((s) => !/doorbell|deurbel/i.test(s.entity_id))).map((s) => s.entity_id);
  }
  const spelers = speelbareSpelers(all);
  const uit = new Set();
  for (const t of lijst) {
    const z = String(t).toLowerCase().trim();
    if (z.startsWith('media_player.')) { uit.add(z); continue; }
    const hit = spelers.filter((s) => (s.entity_id + ' ' + friendlyName(s)).toLowerCase().includes(z));
    for (const s of dedupe(hit)) uit.add(s.entity_id);
  }
  return uit.size ? [...uit] : defaultSpeakers(all);
}

/**
 * Maak gesproken audio (mp3) van tekst via Home Assistant's TTS.
 * Geeft {buffer, contentType} terug; throwt met leesbare fout.
 */
export async function synthesizeSpeech(text, { engine = null } = {}) {
  const all = await allStates();
  const engines = engine
    ? [engine]
    : ['tts.openai_tts', 'tts.home_assistant_cloud', 'tts.piper']
        .filter((e) => all.some((s) => s.entity_id === e && s.state !== 'unavailable'));
  if (!engines.length) throw new Error('geen TTS-engine beschikbaar in Home Assistant');

  let laatsteFout = 'onbekend';
  for (const eng of engines) {
    try {
      const r = await restCall('POST', '/api/tts_get_url', { engine_id: eng, message: text });
      const path = r?.path || (r?.url ? new URL(r.url).pathname : null);
      if (!path) { laatsteFout = 'geen audiopad terug'; continue; }
      const res = await fetch(config.haUrl + path, { headers: headers() });
      if (!res.ok) { laatsteFout = `audio ophalen HTTP ${res.status}`; continue; }
      const buffer = Buffer.from(await res.arrayBuffer());
      return { buffer, contentType: res.headers.get('content-type') || 'audio/mpeg', engine: eng };
    } catch (e) {
      laatsteFout = errMsg(e);
    }
  }
  throw new Error(`spraak maken mislukt: ${laatsteFout}`);
}

// Voorkeursvolgorde van TTS-stemmen: haar eigen ElevenLabs-stem (zodra die er is),
// dan de natuurlijke Nabu Casa-cloudstem, dan OpenAI, dan het lokale Piper als
// robuuste terugval. Stuk/onbeschikbare engines worden vanzelf overgeslagen.
const TTS_VOORKEUR = ['tts.elevenlabs', 'tts.home_assistant_cloud', 'tts.openai_tts', 'tts.piper'];

/**
 * Maak spraak én bewijs dat de audio écht op te halen is (HTTP 200 + bytes) vóór we
 * een speaker ernaartoe sturen. Zo claimt Eva nooit meer dat ze sprak terwijl er een
 * kapot/onbereikbaar audio-pad uitkwam (de stille-Sonos-bug). Geeft {url, engine} of {error}.
 */
async function playableTtsUrl(all, text) {
  const base = await reachableBase();
  const kandidaten = TTS_VOORKEUR.filter((e) => all.some((s) => s.entity_id === e && s.state !== 'unavailable'));
  for (const s of all) {
    if (s.entity_id.startsWith('tts.') && s.state !== 'unavailable' && !kandidaten.includes(s.entity_id)) kandidaten.push(s.entity_id);
  }
  let laatste = 'geen werkende TTS-engine';
  for (const eng of kandidaten) {
    try {
      const r = await restCall('POST', '/api/tts_get_url', { engine_id: eng, message: text });
      const path = r?.path || (r?.url ? new URL(r.url).pathname : null);
      if (!path) { laatste = `${eng}: geen audiopad`; continue; }
      const url = base + path; // bereikbaar LAN-adres, niet het interne docker-IP
      const res = await fetch(url, { headers: headers() });
      if (!res.ok) { laatste = `${eng}: audio HTTP ${res.status}`; continue; }
      const bytes = (await res.arrayBuffer()).byteLength;
      if (bytes < 500) { laatste = `${eng}: lege audio (${bytes}b)`; continue; }
      return { url, engine: eng, bytes };
    } catch (e) {
      laatste = `${eng}: ${errMsg(e)}`;
    }
  }
  return { error: laatste };
}

/** Diagnose: welke TTS-stem werkt nu echt? Voor zelfcontrole, zonder geluid te maken. */
export async function speechSelfTest() {
  const all = await allStates();
  const audio = await playableTtsUrl(all, 'Dit is een stille test van Eva.');
  return audio.url
    ? { ok: true, engine: audio.engine, bytes: audio.bytes, speakers: resolveSpeakers(all, null) }
    : { ok: false, error: audio.error, speakers: resolveSpeakers(all, null) };
}

/**
 * Laat Eva praten door het huis.
 * 1) moderne tts.speak met een tts.*-entiteit; 2) anders legacy tts.*_say.
 * `entityIds` mag null (standaardspeakers), 'overal'/'alle', entity_ids of naamfragmenten zijn.
 * Geeft {ok, via?, error?} terug en throwt nooit.
 */
export async function speak(text, entityIds = null, options = {}) {
  const isCurrent = typeof options?.isCurrent === 'function' ? options.isCurrent : () => true;
  try {
    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
    const all = await allStates();
    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };

    let spelers = resolveSpeakers(all, entityIds);
    if (!spelers.length) return { ok: false, error: 'geen geschikte mediaspeler gevonden' };

    // 1) Beste route (werkt op Sonos én Cast): maak een TTS-bestand, controleer dat de
    //    audio écht op te halen is, geef de speaker die bereikbare URL en speel het als
    //    'announce' af — dan dempt de muziek even en hoor je écht woorden.
    const audio = await playableTtsUrl(all, text);
    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
    if (audio.url) {
      try {
        if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
        await callService('media_player', 'play_media', {
          entity_id: spelers,
          media_content_id: audio.url,
          media_content_type: 'music',
          announce: true,
        });
        return { ok: true, via: `${audio.engine} → ${spelers.join(', ')}`, engine: audio.engine };
      } catch (error) {
        // Een afgebroken HTTP-wachttermijn zegt niet dat HA de opdracht niet
        // uitvoerde. Meteen een tweede route proberen kan dezelfde zin dubbel
        // afspelen; laat de volgende gebeurtenis liever opnieuw proberen.
        if (serviceTimeoutIsAmbiguous(error)) {
          return { ok: false, uncertain: true, error: `speakeropdracht bleef onbevestigd: ${errMsg(error)}` };
        }
        // sommige spelers lusten geen 'announce' → probeer tts.speak met dezelfde engine
        try {
          if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
          await callService('tts', 'speak', { entity_id: audio.engine, media_player_entity_id: spelers, message: text });
          return { ok: true, via: `${audio.engine} (speak) → ${spelers.join(', ')}`, engine: audio.engine };
        } catch (fallbackError) {
          if (serviceTimeoutIsAmbiguous(fallbackError)) {
            return { ok: false, uncertain: true, error: `speakeropdracht bleef onbevestigd: ${errMsg(fallbackError)}` };
          }
          // val verder terug bij een expliciete afwijzing
        }
      }
    }

    // 2) terugval: tts-entiteit + tts.speak
    const ttsEntity = all.find((s) => s.entity_id.startsWith('tts.') && s.state !== 'unavailable');
    if (ttsEntity) {
      if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
      await callService('tts', 'speak', {
        entity_id: ttsEntity.entity_id,
        media_player_entity_id: spelers,
        message: text,
      });
      return { ok: true, via: `${ttsEntity.entity_id} → ${spelers.join(', ')}` };
    }

    // 3) legacy route: kijk welke *_say-service er bestaat
    const services = await restGet('/api/services');
    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
    const ttsDomain = Array.isArray(services)
      ? services.find((s) => s.domain === 'tts')
      : null;
    const beschikbaar = ttsDomain ? Object.keys(ttsDomain.services || {}) : [];
    const say =
      ['google_translate_say', 'cloud_say'].find((s) => beschikbaar.includes(s)) ||
      beschikbaar.find((s) => s.endsWith('_say'));
    if (!say) return { ok: false, error: 'geen TTS-dienst gevonden in Home Assistant' };

    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
    await callService('tts', say, { entity_id: spelers, message: text });
    return { ok: true, via: `tts.${say} → ${spelers.join(', ')}` };
  } catch (e) {
    return { ok: false, error: errMsg(e) };
  }
}
