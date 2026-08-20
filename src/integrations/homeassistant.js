import WebSocket from 'ws';
import { createHash, randomBytes } from 'node:crypto';
import dns from 'node:dns/promises';
import fs from 'node:fs/promises';
import https from 'node:https';
import path from 'node:path';
import { config } from '../config.js';
import { bus } from '../bus.js';
import { logTimeline } from '../db.js';
import { canonicalJson, errMsg, normalizeUserFacingText } from '../util.js';
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
let socketGeneration = 0;
const haSocketStates = new WeakMap();
const issuedHaVoiceEvents = new WeakMap();
const consumedHaVoiceEvents = new WeakSet();
const verifiedHaVoiceEventSources = new WeakMap();
const acceptedHaVoiceEventIdentities = new Map();
const HA_ADAPTER_INSTANCE_SHA256 = createHash('sha256').update(randomBytes(32)).digest('hex');
const HA_VOICE_FRAME_MAX_BYTES = 32 * 1024;
const HA_VOICE_REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/u;
let offlineLogged = false;   // voorkom timeline-spam bij herhaald falen
const TRIGGER_FRAME_DIR = path.join(config.dataDir, 'private', 'camera_trigger_frames');
const PERSON_INCIDENT_QUIET_MS = Math.max(30_000, config.cameraIncidentQuietMs);
const DOORBELL_INCIDENT_QUIET_MS = Math.max(3_000, config.doorbellIncidentQuietMs);
const TRIGGER_FRAME_MAX_FILES = Math.max(60, config.triggerFrameMaxFiles);
const TRIGGER_FRAME_MAX_AGE_MS = Math.max(
  6 * 3600_000,
  config.triggerFrameRetentionHours * 3600_000
);
const CAMERA_SNAPSHOT_TIMEOUT_MS = 12_000;
const CAMERA_SNAPSHOT_MAX_BYTES = 12 * 1024 * 1024;
const UNIFI_CAMERA_LIST_MAX_BYTES = 4 * 1024 * 1024;
const TTS_PREFLIGHT_TIMEOUT_MS = 8_000;
const TTS_AUDIO_MAX_BYTES = 8 * 1024 * 1024;
const cameraIncidents = new Map(); // camera/sensor -> lopend Protect-incident
const ISSUED_CAMERA_TRIGGER_EVENTS = new WeakSet();
const CONSUMED_CAMERA_TRIGGER_EVENTS = new WeakSet();
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

async function restGet(path) {
  const res = await fetch(config.haUrl + path, {
    headers: headers(),
    signal: AbortSignal.timeout(5_000),
  });
  if (!res.ok) throw new Error(`HA GET ${path} mislukt: HTTP ${res.status}`);
  return res.json();
}

/** Alle states: uit de WS-cache als die er is, anders via REST. */
export async function allStates({ forceRefresh = false } = {}) {
  if (!forceRefresh && connected && states.size) return [...states.values()];
  return restGet('/api/states');
}

// ---------- WebSocket-verbinding ----------

export function haConnected() {
  return connected;
}

/** Alleen-lezen bronfoto voor lagen die een verstreken event-wachtpunt eenmaal
 * moeten verzoenen. De adapter blijft eigenaar van de cache. */
export function cachedStates() {
  return [...states.values()];
}

/** Verbindt met de HA WebSocket API; herverbindt zelf met backoff. Throwt nooit.
 * Een read-only kwalificatieproces kan frameonderhoud expliciet overslaan. */
export async function connectHA({ runMaintenance = true } = {}) {
  if (started) return;
  started = true;
  if (runMaintenance) scheduleTriggerFramePrune({ force: true });
  if (!config.haToken) {
    logTimeline('system', 'Home Assistant: geen HA_TOKEN ingesteld, integratie inactief');
    return;
  }
  openSocket();
}

function openSocket() {
  let socket;
  try {
    // handshakeTimeout: anders kan een stil gedropte TCP-connect minutenlang hangen
    socket = new WebSocket(WS_URL, { handshakeTimeout: 15_000 });
  } catch (e) {
    onDisconnected(errMsg(e));
    return;
  }
  const generation = ++socketGeneration;
  ws = socket;
  connected = false;
  haSocketStates.set(socket, {
    generation, authenticated: false, subscribed: false,
    subscriptionIds: new Map(),
  });

  socket.on('message', (raw) => {
    const frame = Buffer.isBuffer(raw) ? Buffer.from(raw) : Buffer.from(raw);
    let msg;
    try {
      msg = JSON.parse(frame.toString('utf8'));
    } catch {
      return;
    }
    // Vangnet: een fout in de afhandeling van één event (bv. een camera/person-event) mag NOOIT
    // de hele WS-message-handler platleggen — anders vallen ook de eva_voice_query-events stil
    // en werkt spraak niet meer. Elk event wordt geïsoleerd afgehandeld.
    try {
      handleMessage(socket, generation, msg, frame);
    } catch (e) {
      logTimeline('system', `HA-event overgeslagen na fout: ${errMsg(e).slice(0, 160)}`);
    }
  });

  socket.on('pong', () => { socket._alive = true; });
  socket.on('error', (e) => { socket._lastError = errMsg(e); });
  socket.on('close', () => onDisconnected(
    socket._lastError || 'verbinding gesloten', socket, generation,
  ));
}

function currentHaSocket(socket, generation, { requireSubscribed = false } = {}) {
  const state = socket && haSocketStates.get(socket);
  return Boolean(state && ws === socket && state.generation === generation
    && socket.readyState === WebSocket.OPEN
    && state.authenticated === true
    && (!requireSubscribed || (state.subscribed === true && connected === true)));
}

function issueHaVoiceEvent(socket, generation, msg, frame) {
  const eventType = String(msg?.event?.event_type || '');
  const state = socket && haSocketStates.get(socket);
  if (!['eva_voice_query', 'eva_voice_reply_ack'].includes(eventType)
    || !Number.isSafeInteger(Number(msg?.id))
    || Number(msg.id) !== Number(state?.subscriptionIds?.get(eventType))
    || !currentHaSocket(socket, generation, { requireSubscribed: true })
    || !Buffer.isBuffer(frame) || frame.length < 2 || frame.length > HA_VOICE_FRAME_MAX_BYTES) {
    return null;
  }
  const frameText = frame.toString('utf8');
  if (!Buffer.from(frameText, 'utf8').equals(frame)) return null;
  const frameSha256 = createHash('sha256').update(frame).digest('hex');
  const bodyJson = canonicalJson({
    contract: 'HomeAssistantVoiceEventEvidence/v1',
    adapterInstanceSha256: HA_ADAPTER_INSTANCE_SHA256,
    socketGeneration: generation,
    eventType,
    frameByteLength: frame.length,
    frameSha256,
    frameBase64: frame.toString('base64'),
    event: msg,
  });
  if (Buffer.byteLength(bodyJson, 'utf8') > 64 * 1024) return null;
  const requestId = String(msg?.event?.data?.id || '');
  if (!HA_VOICE_REQUEST_ID.test(requestId)) return null;
  const eventIdentity = `${eventType}:${requestId}`;
  // Een request-id is gedurende deze boot exact één fysiek query- of ACK-event.
  // Een identieke replay of gewijzigde rebind mint dus ook na reconnect geen cap.
  if (acceptedHaVoiceEventIdentities.has(eventIdentity)) return null;
  if (acceptedHaVoiceEventIdentities.size >= 50_000) return null;
  acceptedHaVoiceEventIdentities.set(eventIdentity, frameSha256);
  const sourceRef = `home-assistant-websocket:${createHash('sha256').update(canonicalJson({
    contract: 'HomeAssistantVoiceEventIdentity/v1',
    adapterInstanceSha256: HA_ADAPTER_INSTANCE_SHA256,
    socketGeneration: generation,
    eventType,
    requestId,
    frameSha256,
  })).digest('hex')}`;
  const capability = Object.freeze({});
  issuedHaVoiceEvents.set(capability, Object.freeze({
    eventType, bodyJson, sourceRef,
    sourceSha256: createHash('sha256').update(bodyJson).digest('hex'),
    socketGeneration: generation,
    adapterInstanceSha256: HA_ADAPTER_INSTANCE_SHA256,
    isCurrent: () => currentHaSocket(socket, generation, { requireSubscribed: true }),
  }));
  setTimeout(() => {
    if (!consumedHaVoiceEvents.has(capability)) issuedHaVoiceEvents.delete(capability);
  }, 30_000).unref?.();
  return capability;
}

/**
 * Alleen-lezen transportverificatie van een eenmalige capability. De capability
 * zelf kan uitsluitend in de actuele authenticated+subscribed WS-handler ontstaan.
 */
export function verifiedAuthenticatedHaVoiceEvent(capability, expectedEventType) {
  const issued = capability && typeof capability === 'object'
    ? issuedHaVoiceEvents.get(capability) : null;
  if (!issued || consumedHaVoiceEvents.has(capability)
    || issued.eventType !== expectedEventType || issued.isCurrent() !== true) return null;
  let body;
  try { body = JSON.parse(issued.bodyJson); } catch { return null; }
  if (canonicalJson(body) !== issued.bodyJson
    || body.eventType !== issued.eventType
    || body.adapterInstanceSha256 !== issued.adapterInstanceSha256
    || Number(body.socketGeneration) !== issued.socketGeneration) return null;
  const source = Object.freeze({
    eventType: issued.eventType,
    data: Object.freeze({ ...(body.event?.event?.data || {}) }),
    sourceRef: issued.sourceRef,
    sourceSha256: issued.sourceSha256,
    sourceBodyJson: issued.bodyJson,
    socketGeneration: issued.socketGeneration,
    adapterInstanceSha256: issued.adapterInstanceSha256,
    isCurrent: issued.isCurrent,
  });
  verifiedHaVoiceEventSources.set(source, Object.freeze({
    capability,
    eventType: issued.eventType,
    sourceRef: issued.sourceRef,
    sourceSha256: issued.sourceSha256,
    bodyJson: issued.bodyJson,
  }));
  return source;
}

/** Consumeer pas nadat de duurzame eventprojectie aantoonbaar gecommit is. */
export function consumeAuthenticatedHaVoiceEvent(capability, expectedEventType, source) {
  const issued = capability && typeof capability === 'object'
    ? issuedHaVoiceEvents.get(capability) : null;
  const verified = source && typeof source === 'object'
    ? verifiedHaVoiceEventSources.get(source) : null;
  if (!issued || !verified || consumedHaVoiceEvents.has(capability)
    || verified.capability !== capability
    || verified.eventType !== expectedEventType
    || issued.eventType !== expectedEventType
    || verified.sourceRef !== issued.sourceRef
    || verified.sourceSha256 !== issued.sourceSha256
    || verified.bodyJson !== issued.bodyJson
    || source.sourceRef !== issued.sourceRef
    || source.sourceSha256 !== issued.sourceSha256
    || source.sourceBodyJson !== issued.bodyJson) return false;
  consumedHaVoiceEvents.add(capability);
  issuedHaVoiceEvents.delete(capability);
  verifiedHaVoiceEventSources.delete(source);
  return true;
}

function dispatchHaVoiceEvent(socket, generation, msg, frame) {
  const capability = issueHaVoiceEvent(socket, generation, msg, frame);
  if (!capability) return;
  const eventType = msg.event.event_type;
  import('../mind/voicebridge.js').then(async (voiceBridge) => {
    if (eventType === 'eva_voice_query') {
      await voiceBridge.acceptAuthenticatedHaVoiceQuery(capability);
    } else {
      await voiceBridge.acceptAuthenticatedHaVoiceReplyAck(capability);
    }
  }).catch((error) => {
    logTimeline('system', `Geauthenticeerd HA-voice-event kon niet worden verwerkt: ${errMsg(error)}`, {
      internalOnly: true,
      eventType,
    });
  });
}

function handleMessage(socket, generation, msg, frame) {
  switch (msg.type) {
    case 'auth_required':
      if (ws !== socket || haSocketStates.get(socket)?.generation !== generation) break;
      connected = false;
      haSocketStates.get(socket).authenticated = false;
      haSocketStates.get(socket).subscribed = false;
      haSocketStates.get(socket).subscriptionIds.clear();
      socket.send(JSON.stringify({ type: 'auth', access_token: config.haToken }));
      break;

    case 'auth_ok':
      if (ws !== socket || haSocketStates.get(socket)?.generation !== generation) break;
      haSocketStates.get(socket).authenticated = true;
      onAuthenticated(socket, generation);
      break;

    case 'auth_invalid':
      if (!offlineLogged) logTimeline('system', 'Home Assistant weigert Eva\'s toegang; huiswaarneming en bediening zijn niet beschikbaar.', {
        familyVisible: true, homeAssistantAuthFailure: true, technicalError: msg.message || 'auth_invalid',
      });
      offlineLogged = true; // niet nogmaals loggen bij de close die volgt of bij retries
      try { socket.close(); } catch { /* negeer */ }
      break;

    case 'result': {
      const p = pending.get(msg.id);
      if (!p || p.socket !== socket || p.generation !== generation) break;
      pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.success) p.resolve(p.includeCommandId
        ? Object.freeze({ commandId: Number(msg.id), result: msg.result })
        : msg.result);
      else p.reject(new Error(msg.error?.message || 'WS-commando mislukt'));
      break;
    }

    case 'event':
      if (Number(msg.id) !== Number(
        haSocketStates.get(socket)?.subscriptionIds?.get(msg.event?.event_type),
      )) break;
      if (msg.event?.event_type === 'state_changed') onStateChanged(msg.event.data);
      else if (msg.event?.event_type === 'eva_voice_query') {
        dispatchHaVoiceEvent(socket, generation, msg, frame);
      } else if (msg.event?.event_type === 'eva_voice_reply_ack') {
        dispatchHaVoiceEvent(socket, generation, msg, frame);
      }
      break;
  }
}

/** Stuur een WS-commando en wacht op het result. Ook voor beheer (admin-API). */
function wsCommand(payload, timeoutMs = 10_000, binding = null, includeCommandId = false) {
  return new Promise((resolve, reject) => {
    const socket = binding?.socket || ws;
    const generation = binding?.generation ?? haSocketStates.get(socket)?.generation;
    if (!socket || socket.readyState !== WebSocket.OPEN
      || !Number.isSafeInteger(generation)
      || (binding && !currentHaSocket(socket, generation))) {
      return reject(new Error('WS niet verbonden'));
    }
    const id = msgId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error('WS-commando timeout'));
    }, timeoutMs);
    pending.set(id, {
      resolve, reject, timer, socket, generation, includeCommandId: includeCommandId === true,
    });
    socket.send(JSON.stringify({ id, ...payload }));
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

// Toolresultaten boven 8 kB worden centraal als gedeeltelijk gecompacteerd.
// Houd één inventarispagina daar dus bewust onder, met een expliciete totaalstand
// wanneer een uitzonderlijk grote integratie niet volledig past.
const INTEGRATION_INVENTORY_LIMITS = Object.freeze({ entries: 10, devices: 14, entities: 42 });
export const HA_INTEGRATION_SLUG_PATTERN = /^(?!all$)[a-z0-9_]{1,64}$/u;

function registryList(result, key) {
  if (Array.isArray(result)) return result;
  return Array.isArray(result?.[key]) ? result[key] : null;
}

function integrationSlug(value) {
  const slug = String(value || '').trim(); return HA_INTEGRATION_SLUG_PATTERN.test(slug) ? slug : '';
}

function inventoryReadFailure(failures) {
  const reason = failures.map((failure) => failure.reason).join(' ');
  if (/auth|permission|unauthor|forbidden|toegang/iu.test(reason)) {
    return { failure_class: 'permission', retryable: false, effect_uncertain: false };
  }
  return {
    failure_class: /timeout|tijdslimiet/iu.test(reason) ? 'transient' : 'unavailable',
    retryable: true,
    effect_uncertain: false,
  };
}

function deviceConfigEntryIds(device) {
  return [...new Set([
    ...(Array.isArray(device?.config_entries) ? device.config_entries : []),
    device?.config_entry_id,
  ].filter((value) => value != null && String(value)).map(String))];
}

/**
 * Lees één integratie als samenhangende, verse HA-inventaris. Config entries,
 * apparaten, entity-registry en states komen uit vier officiële read-only
 * WebSocket-opdrachten; zo hoeft een hogere laag geen losse lijsten zelf te
 * koppelen of platformnamen te raden.
 */
export async function listIntegrationInventory(integration, {
  command = wsCommand,
  limits = INTEGRATION_INVENTORY_LIMITS,
} = {}) {
  const domain = integrationSlug(integration);
  if (!domain) {
    return { ok: false, changed: false, verified: false, reason: 'een exacte integratieslug ontbreekt' };
  }
  const sources = [
    { name: 'config_entries', payload: { type: 'config_entries/get', domain }, key: 'entries' },
    { name: 'devices', payload: { type: 'config/device_registry/list' }, key: 'devices' },
    { name: 'entities', payload: { type: 'config/entity_registry/list' }, key: 'entities' },
    { name: 'states', payload: { type: 'get_states' }, key: 'states' },
  ];
  const settled = await Promise.allSettled(sources.map(({ payload }) => command(payload, 20_000)));
  const failures = settled.flatMap((result, index) => (
    result.status === 'rejected'
      ? [{ source: sources[index].name, reason: errMsg(result.reason).slice(0, 180) }]
      : []
  ));
  if (failures.length) {
    return {
      ok: false, changed: false, verified: false, integration: domain,
      reason: 'de Home Assistant-inventaris is niet volledig leesbaar', missing_sources: failures,
      needs_followup: true, ...inventoryReadFailure(failures),
    };
  }
  const lists = Object.fromEntries(settled.map((result, index) => [
    sources[index].name,
    result.status === 'fulfilled' ? registryList(result.value, sources[index].key) : null,
  ]));
  const malformed = sources.map(({ name }) => name).filter((name) => lists[name] == null);
  if (malformed.length) {
    return {
      ok: false, changed: false, verified: false, integration: domain,
      reason: 'Home Assistant gaf geen geldige volledige inventarislijsten terug',
      missing_sources: malformed.map((source) => ({ source, reason: 'ongeldige antwoordvorm' })),
      needs_followup: true, failure_class: 'invalid_input', retryable: false,
      effect_uncertain: false,
    };
  }

  const entries = lists.config_entries.filter((entry) => String(entry?.domain || '').toLowerCase() === domain);
  const entryIds = new Set(entries.map((entry) => String(entry?.entry_id || '')).filter(Boolean));
  const integrationIdentifier = (device) => (Array.isArray(device?.identifiers) ? device.identifiers : [])
    .some((identifier) => Array.isArray(identifier)
      && String(identifier[0] || '').toLowerCase() === domain);
  const directlyMatchedEntities = lists.entities.filter((entity) => (
    entryIds.has(String(entity?.config_entry_id || ''))
    || String(entity?.platform || '').toLowerCase() === domain
  ));
  const directlyMatchedDeviceIds = new Set(directlyMatchedEntities
    .map((entity) => String(entity?.device_id || '')).filter(Boolean));
  const devices = lists.devices.filter((device) => (
    deviceConfigEntryIds(device).some((entryId) => entryIds.has(entryId)) || integrationIdentifier(device)
      || directlyMatchedDeviceIds.has(String(device?.id || ''))
  ));
  const deviceIds = new Set(devices.map((device) => String(device?.id || '')).filter(Boolean));
  const entities = lists.entities.filter((entity) => (
    entryIds.has(String(entity?.config_entry_id || ''))
    || deviceIds.has(String(entity?.device_id || ''))
    || String(entity?.platform || '').toLowerCase() === domain
  ));
  const stateById = new Map(lists.states.map((state) => [String(state?.entity_id || ''), state]));
  const entitiesByDevice = new Map();
  for (const entity of entities) {
    const deviceId = String(entity?.device_id || '');
    const list = entitiesByDevice.get(deviceId) || [];
    list.push(entity);
    entitiesByDevice.set(deviceId, list);
  }
  const entityEvidence = (entity) => {
    const entityId = String(entity?.entity_id || '');
    const state = stateById.get(entityId);
    const name = entity?.name || entity?.original_name || state?.attributes?.friendly_name || null;
    return {
      entity_id: entityId,
      ...(name ? { name } : {}),
      ...(entity?.platform && String(entity.platform).toLowerCase() !== domain
        ? { platform: entity.platform } : {}),
      ...(entity?.disabled_by ? { disabled_by: entity.disabled_by } : {}),
      ...(entity?.hidden_by ? { hidden_by: entity.hidden_by } : {}),
      ...(state ? { state: state.state, state_verified: true } : { state_verified: false }),
    };
  };
  const boundedEntries = entries.slice(0, Math.max(1, Number(limits.entries) || 1)).map((entry) => ({
    entry_id: entry?.entry_id || null,
    title: entry?.title || null,
    state: entry?.state || null,
    ...(entry?.source ? { source: entry.source } : {}),
    ...(entry?.disabled_by ? { disabled_by: entry.disabled_by } : {}),
  }));
  let remainingEntities = Math.max(1, Number(limits.entities) || 1);
  const boundedDevices = devices.slice(0, Math.max(1, Number(limits.devices) || 1)).map((device) => {
    const allLinked = entitiesByDevice.get(String(device?.id || '')) || [];
    const linked = allLinked
      .slice(0, remainingEntities);
    remainingEntities -= linked.length;
    return {
      device_id: device?.id || null,
      name: device?.name_by_user || device?.name || null,
      manufacturer: device?.manufacturer || null,
      model: device?.model || null,
      ...(device?.area_id ? { area_id: device.area_id } : {}),
      config_entry_ids: deviceConfigEntryIds(device).filter((id) => entryIds.has(id)),
      identifiers: (Array.isArray(device?.identifiers) ? device.identifiers : []).slice(0, 3),
      ...(device?.disabled_by ? { disabled_by: device.disabled_by } : {}),
      entity_count: allLinked.length,
      entities: linked.map(entityEvidence),
    };
  });
  const orphanEntities = (entitiesByDevice.get('') || [])
    .slice(0, remainingEntities).map(entityEvidence);
  const returnedEntityCount = boundedDevices.reduce((sum, device) => sum + device.entities.length, 0)
    + orphanEntities.length;
  let truncated = boundedEntries.length < entries.length || boundedDevices.length < devices.length
    || returnedEntityCount < entities.length;
  const inventory = {
    ok: true, changed: false, verified: true, complete: !truncated,
    partial: truncated, needs_followup: truncated, truncated, integration: domain,
    counts: {
      config_entries: entries.length, devices: devices.length, entities: entities.length,
      returned_config_entries: boundedEntries.length, returned_devices: boundedDevices.length,
      returned_entities: returnedEntityCount,
    },
    config_entries: boundedEntries, devices: boundedDevices, entities_without_device: orphanEntities,
    source_commands: sources.map(({ payload }) => payload.type),
    evidence_contract: 'entry_id, device_id en entity_id zijn de exacte sleutels uit de genoemde verse registrybronnen; een state met state_verified=true komt uit get_states.',
    limitations: [
      'Deze inventaris bewijst de huidige HA-koppelingen en states, niet compatibiliteit met een nog niet gekozen doelintegratie.',
      'Lokale sleutels, pairingcodes en cloudreferenties staan niet in deze registry-uitlezing.',
      ...(truncated ? ['De uitvoer is begrensd; niet ieder gevonden item staat in dit resultaat.'] : []),
    ],
  };
  // De centrale ActionResult-grens compacteert boven 8 kB en zou dan juist de
  // itemlijst verliezen. Snoei uitzonderlijk lange registrynamen daarom hier
  // transparant, mét totale en geretourneerde aantallen.
  while (Buffer.byteLength(JSON.stringify(inventory)) > 7_800) {
    const deviceWithEntities = [...inventory.devices].reverse()
      .find((device) => device.entities.length);
    if (inventory.entities_without_device.length) inventory.entities_without_device.pop();
    else if (deviceWithEntities) deviceWithEntities.entities.pop();
    else if (inventory.devices.length) inventory.devices.pop();
    else if (inventory.config_entries.length > 1) inventory.config_entries.pop();
    else break;
    truncated = true;
    inventory.complete = false;
    inventory.partial = true;
    inventory.needs_followup = true;
    inventory.truncated = true;
    inventory.counts.returned_config_entries = inventory.config_entries.length;
    inventory.counts.returned_devices = inventory.devices.length;
    inventory.counts.returned_entities = inventory.devices.reduce(
      (sum, device) => sum + device.entities.length, inventory.entities_without_device.length,
    );
    if (!inventory.limitations.some((line) => /uitvoer is begrensd/iu.test(line))) {
      inventory.limitations.push('De uitvoer is begrensd; niet ieder gevonden item staat in dit resultaat.');
    }
  }
  return inventory;
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
export async function setPersonTrackers(personQuery, deviceTrackers, {
  isCurrent = () => true,
  listPersonConfigurations: readPeople = listPersonConfigurations,
  wsCommand: mutatePerson = wsCommand,
  stateStore = states,
} = {}) {
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
  const missing = requested.filter((entityId) => !stateStore.has(entityId));
  if (missing.length) {
    return { ok: false, changed: false, verified: false, reason: `tracker bestaat niet in Home Assistant: ${missing.join(', ')}` };
  }

  const before = await readPeople();
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

  let current = false;
  try { current = Boolean(isCurrent()); } catch { current = false; }
  if (!current) {
    return {
      ok: false, changed: false, verified: false, stale: true,
      failure_class: 'conflict', retryable: false, effect_uncertain: false,
      needs_followup: false,
      reason: 'voicebeurt is ingehaald; de aanwezigheidstrackers zijn niet gewijzigd',
    };
  }
  await mutatePerson({
    type: 'person/update',
    person_id: person.id,
    device_trackers: newTrackers,
  });
  const after = await readPeople();
  const stored = after.storage.find((candidate) => candidate.id === person.id);
  const verifiedTrackers = [...(stored?.device_trackers || [])].sort();
  const verified = JSON.stringify(verifiedTrackers) === JSON.stringify(newTrackers);
  const personState = stateStore.get(`person.${person.id}`) || null;
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

async function onAuthenticated(socket, generation) {
  if (!currentHaSocket(socket, generation)) return;
  backoff = BACKOFF_START;

  // keepalive: ping elke 30s, geen pong = verbinding kapot
  socket._alive = true;
  clearInterval(pingTimer);
  pingTimer = setInterval(() => {
    if (!currentHaSocket(socket, generation)) return;
    if (!socket._alive) { try { socket.terminate(); } catch { /* negeer */ } return; }
    socket._alive = false;
    try { socket.ping(); } catch { /* negeer */ }
  }, 30_000);
  pingTimer.unref?.();

  try {
    const binding = { socket, generation };
    const eventTypes = ['state_changed', 'eva_voice_query', 'eva_voice_reply_ack'];
    const subscriptionReceipts = [];
    for (const eventType of eventTypes) {
      const receipt = await wsCommand(
        { type: 'subscribe_events', event_type: eventType }, 10_000, binding, true,
      );
      subscriptionReceipts.push([eventType, Number(receipt?.commandId)]);
    }
    const subscriptionIds = new Map(subscriptionReceipts);
    if (subscriptionIds.size !== eventTypes.length
      || new Set(subscriptionIds.values()).size !== eventTypes.length
      || [...subscriptionIds.values()].some((id) => !Number.isSafeInteger(id) || id < 1)) {
      throw new Error("HA gaf geen exacte unieke subscription-id's terug");
    }
    const all = await wsCommand({ type: 'get_states' }, 20_000, binding);
    if (!currentHaSocket(socket, generation)) throw new Error('WS-generatie is ingehaald');
    states.clear();
    for (const st of all) states.set(st.entity_id, st);
    const state = haSocketStates.get(socket);
    state.subscriptionIds = subscriptionIds;
    state.subscribed = true;
    connected = true;
    if (offlineLogged) logTimeline('system', 'Weer verbonden met Home Assistant', { internalOnly: true, homeAssistantConnection: true });
    else logTimeline('system', 'Verbonden met Home Assistant', { internalOnly: true, homeAssistantConnection: true });
    offlineLogged = false;
    // Live events kunnen tijdens downtime zijn gemist. Hogere lagen krijgen na
    // iedere reconnect één complete, actuele bronfoto om hun gewapende wachters
    // te verzoenen zonder zelf te gaan pollen.
    bus.emit('ha_snapshot_ready', { ts: Date.now(), states: all });
  } catch (e) {
    if (ws === socket) {
      connected = false;
      const state = haSocketStates.get(socket);
      if (state) {
        state.subscribed = false;
        state.subscriptionIds.clear();
      }
      try { socket.close(); } catch { /* negeer */ }
    }
    logTimeline('system', `Home Assistant: initialisatie mislukt (${errMsg(e)})`);
  }
}

function onDisconnected(reason, socket = ws, generation = haSocketStates.get(socket)?.generation) {
  if (socket && (ws !== socket || haSocketStates.get(socket)?.generation !== generation)) return;
  const wasConnected = connected;
  connected = false;
  const state = socket && haSocketStates.get(socket);
  if (state) {
    state.authenticated = false;
    state.subscribed = false;
    state.subscriptionIds.clear();
  }
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
  reconnectTimer.unref?.();
  backoff = Math.min(backoff * 2, BACKOFF_MAX);
}

// ---------- state-changes verwerken ----------

function emitWorldStateSignal(entityId, oldState, newState, naam, correlationId, changedAt) {
  bus.emit('world_signal', {
    source: 'home_assistant',
    type: 'entity_state',
    entityId,
    domain: entityId.split('.')[0] || null,
    friendlyName: naam,
    correlationId,
    oldState: oldState.state,
    newState: newState.state,
    changedAt,
    attributes: {
      deviceClass: newState.attributes?.device_class || null,
      sourceType: newState.attributes?.source_type || null,
      unit_of_measurement: newState.attributes?.unit_of_measurement || null,
      current_temperature: newState.attributes?.current_temperature ?? null,
      previous_current_temperature: oldState.attributes?.current_temperature ?? null,
      temperature: newState.attributes?.temperature ?? null,
      previous_temperature: oldState.attributes?.temperature ?? null,
      current_humidity: newState.attributes?.current_humidity ?? null,
      humidity: newState.attributes?.humidity ?? null,
      illuminance: newState.attributes?.illuminance ?? null,
      battery_level: newState.attributes?.battery_level ?? null,
    },
  });
}

function handlePersonStateChange(entityId, oldState, newState, naam, correlationId) {
  const was = oldState.state === 'home';
  const is = newState.state === 'home';
  if (was === is) return;
  const text = is ? `${naam} is thuisgekomen 🏠` : `${naam} is weggegaan`;
  logTimeline('house', text);
  bus.emit('house', {
    type: is ? 'arrival' : 'departure', source: 'home_assistant', entityId,
    correlationId, person: naam, oldState: oldState.state, newState: newState.state,
    changedAt: newState.last_changed || new Date().toISOString(), text,
  });
}

function handleSunStateChange(entityId, newState, correlationId, changedAt) {
  if (!['above_horizon', 'below_horizon'].includes(newState.state)) return;
  const text = newState.state === 'above_horizon' ? 'De zon is opgekomen ☀️' : 'De zon is ondergegaan 🌙';
  logTimeline('house', text);
  bus.emit('house', {
    type: 'sun', source: 'home_assistant', entityId, correlationId, changedAt, text,
  });
}

function emitPrivateDoorbell(entityId, newState, correlationId) {
  const cam = camEntityVoorSensor(entityId);
  const accepted = captureAndEmit({
    type: 'doorbell', camera: cam, sensor: entityId, correlationId,
    text: 'Er wordt aangebeld! 🔔', priority: true,
    triggerTs: newState.last_changed || new Date().toISOString(),
  }, cam);
  if (accepted) logTimeline('vision', 'Een deurbeltrigger is privé voor Eva\'s keuze vastgelegd.', {
    internalOnly: true, camera: cam, privatePerceptionIngress: true,
  });
}

function handleBinarySensorStateChange(entityId, oldState, newState, {
  naam, correlationId, changedAt,
}) {
  const dc = newState.attributes?.device_class;
  if (/_doorbell$/.test(entityId) && oldState.state !== 'on' && newState.state === 'on') {
    emitPrivateDoorbell(entityId, newState, correlationId);
    return;
  }
  if ((dc === 'door' || dc === 'garage_door')
    && oldState.state === 'off' && newState.state === 'on') {
    bus.emit('house', {
      type: 'door', source: 'home_assistant', entityId,
      correlationId, changedAt, text: `De ${naam} is opengegaan`,
    });
    return;
  }
  if (!/_person_detected(_\d+)?$/.test(entityId)
    || oldState.state !== 'off' || newState.state !== 'on') return;
  const cam = camEntityVoorSensor(entityId);
  const waar = (naam || '').replace(/person detected/i, '').trim() || 'een camera';
  logTimeline('house', `Camera zag mogelijk een persoon bij ${waar}`, {
    internalOnly: true, camera: cam, sensor: entityId, personDetected: true,
  });
  captureAndEmit({
    type: 'person_detected', camera: cam, sensor: entityId, correlationId, waar,
    text: `Ik zie iemand bij ${waar}`, priority: true,
    triggerTs: newState.last_changed || new Date().toISOString(),
    detection: {
      source: 'unifi_protect', filter: 'person_detected', object: 'person', sensor: entityId,
      startedAt: newState.last_changed || null,
    },
  }, cam);
}

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

  const privateDoorbellTrigger = (entity_id.startsWith('event.')
      && new_state.attributes?.device_class === 'doorbell')
    || (entity_id.startsWith('binary_sensor.') && /_doorbell$/u.test(entity_id));

  // Eén generieke zintuigstroom voor hogere lagen. Opvolging, projectregie en
  // leren hoeven Home Assistant zo niet opnieuw te pollen of zelf alle
  // entiteitstypen te kennen: ze kunnen wachten tot de adapter een echte
  // verandering meldt en daarna alleen gericht verifiëren. Een deurbeltrigger
  // blijft echter uitsluitend in haar private, eenmalig geattesteerde lane.
  if (!privateDoorbellTrigger) emitWorldStateSignal(
    entity_id, old_state, new_state, naam, correlationId, changedAt,
  );

  // De specifieke aankomst-, deur- en camera-events hieronder horen alleen bij
  // een echte hoofdstatusovergang, niet bij een los bijgewerkt attribuut.
  if (!stateChanged) return;

  // personen: thuiskomen / weggaan
  if (entity_id.startsWith('person.')) {
    handlePersonStateChange(entity_id, old_state, new_state, naam, correlationId);
    return;
  }

  // zon op/onder
  if (entity_id === 'sun.sun') {
    handleSunStateChange(entity_id, new_state, correlationId, changedAt);
    return;
  }

  // deurbel ingedrukt: event.*-entiteit met device_class 'doorbell' (state = tijdstempel)
  if (entity_id.startsWith('event.') && new_state.attributes?.device_class === 'doorbell') {
    emitPrivateDoorbell(entity_id, new_state, correlationId);
    return;
  }

  if (entity_id.startsWith('binary_sensor.')) {
    handleBinarySensorStateChange(entity_id, old_state, new_state, {
      naam, correlationId, changedAt,
    });
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
async function captureTriggerFrame(event, cam, {
  index = 0,
  total = 1,
  snapshot = cameraSnapshot,
  timeoutMs = CAMERA_SNAPSHOT_TIMEOUT_MS,
} = {}) {
  const controller = new AbortController();
  let timeout;
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      controller.abort(new Error('camera snapshot deadline'));
      reject(new Error(`Camerabeeld van ${cam} overschreed de opnamedeadline`));
    }, Math.max(1, Number(timeoutMs) || CAMERA_SNAPSHOT_TIMEOUT_MS));
  });
  let shot;
  try {
    shot = await Promise.race([
      Promise.resolve().then(() => snapshot(cam, {
        signal: controller.signal,
        maxBytes: CAMERA_SNAPSHOT_MAX_BYTES,
      })),
      deadline,
    ]);
  } finally {
    clearTimeout(timeout);
  }
  const contentType = shot.contentType || 'image/jpeg';
  const capturedTs = new Date().toISOString();
  const cameraName = shot.cameraName || friendlyName(states.get(cam)) || cam;
  let file = null;
  try {
    await fs.mkdir(TRIGGER_FRAME_DIR, { recursive: true, mode: 0o700 });
    await fs.chmod(TRIGGER_FRAME_DIR, 0o700);
    const naam = safeFilePart(cameraName);
    const stamp = capturedTs.replace(/[:.]/g, '-');
    const burst = total > 1 ? `_b${index + 1}-van-${total}` : '';
    file = path.join(TRIGGER_FRAME_DIR, `${stamp}_${naam}_${safeFilePart(event.type)}${burst}.${imageExt(contentType)}`);
    await fs.writeFile(file, shot.buffer, { mode: 0o600 });
    await fs.chmod(file, 0o600);
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

async function captureAndIssue(event, cam, captureOptions = undefined) {
  if (cam) {
    const burstDelays = event.type === 'person_detected' ? [0, 700, 1600] : [0];
    const frames = [];
    for (let i = 0; i < burstDelays.length; i += 1) {
      if (burstDelays[i]) await delay(burstDelays[i]);
      try {
        frames.push(await captureTriggerFrame(event, cam, {
          index: i,
          total: burstDelays.length,
          ...captureOptions,
        }));
      } catch (e) {
        logTimeline('system', `Geen triggerbeeld ${i + 1}/${burstDelays.length} voor ${cam}: ${errMsg(e).slice(0, 120)}`);
      }
    }
    if (frames.length) {
      event.frame = frames[0];
      event.frames = frames;
    }
  }
  const frames = Object.freeze((Array.isArray(event.frames) ? event.frames : [])
    .map((frame) => Object.freeze({ ...frame })));
  const issued = Object.freeze({
    ...event,
    ...(frames.length ? { frame: frames[0], frames } : { frame: null, frames }),
  });
  ISSUED_CAMERA_TRIGGER_EVENTS.add(issued);
  bus.emit('house', issued);
  return issued;
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

  void captureAndIssue(event, cam).catch((error) => {
    logTimeline('system', `Camera-incident kon niet worden uitgegeven: ${errMsg(error).slice(0, 160)}`);
  });
  return true;
}

/**
 * Verbruik exact eenmaal een camera-event dat deze adapter zelf na de echte
 * HA-trigger en snapshotpoging heeft uitgegeven. Een structureel gelijk
 * bus-object krijgt hierdoor nooit waarnemings- of vervolgbevoegdheid.
 */
export function consumeIssuedCameraTriggerEvent(event) {
  if (!event || typeof event !== 'object'
    || !ISSUED_CAMERA_TRIGGER_EVENTS.has(event)
    || CONSUMED_CAMERA_TRIGGER_EVENTS.has(event)) return null;
  CONSUMED_CAMERA_TRIGGER_EVENTS.add(event);
  return event;
}

/** Niet-consumerende identitycheck voor de wachtrij vóór enige byte-/file-I/O. */
export function pendingIssuedCameraTriggerEvent(event) {
  return event && typeof event === 'object'
    && ISSUED_CAMERA_TRIGGER_EVENTS.has(event)
    && !CONSUMED_CAMERA_TRIGGER_EVENTS.has(event)
    ? event : null;
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
export async function callService(domain, service, data = {}, {
  timeoutMs = 10_000, target = null, noTransportFallback = false,
} = {}) {
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
      if (noTransportFallback === true
        || (domain === 'assist_satellite' && service === 'announce')) {
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
    throw new Error(`Service ${domain}.${service} mislukt: HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ''}`);
  }
  return res.json();
}

function requestAbortLease(externalSignal, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => {
    if (!controller.signal.aborted) {
      controller.abort(new DOMException('Home Assistant request timeout', 'TimeoutError'));
    }
  }, timeoutMs);
  timeout.unref?.();

  let detachExternal = () => {};
  if (externalSignal != null) {
    if (typeof externalSignal.addEventListener !== 'function') {
      clearTimeout(timeout);
      throw new TypeError('signal moet een AbortSignal zijn');
    }
    const forwardAbort = () => {
      if (!controller.signal.aborted) controller.abort(externalSignal.reason);
    };
    if (externalSignal.aborted) forwardAbort();
    else {
      externalSignal.addEventListener('abort', forwardAbort, { once: true });
      detachExternal = () => externalSignal.removeEventListener('abort', forwardAbort);
    }
  }

  return {
    signal: controller.signal,
    dispose() {
      clearTimeout(timeout);
      detachExternal();
    },
  };
}

/** Generieke REST-call naar Home Assistant (voor beheer: automatiseringen, scripts, config). */
export async function restCall(method, path, body = null, {
  timeoutMs = 10_000,
  signal = null,
} = {}) {
  if (!path.startsWith('/api/')) throw new Error('pad moet met /api/ beginnen');
  const abortLease = requestAbortLease(
    signal,
    Math.max(250, Number(timeoutMs) || 10_000),
  );
  try {
    const res = await fetch(config.haUrl + path, {
      method,
      headers: headers(),
      body: body != null ? JSON.stringify(body) : undefined,
      signal: abortLease.signal,
    });
    const text = await res.text().catch(() => '');
    if (!res.ok) throw new Error(`HA ${method} ${path} mislukt: HTTP ${res.status}${text ? `: ${text.slice(0, 300)}` : ''}`);
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  } finally {
    abortLease.dispose();
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
function assessEntityState(target, memberStates = [], expectedState = null) {
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
async function rawCameras() {
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
  const matches = all.filter(
    (s) => s.entity_id.startsWith('media_player.')
      && /doorbell|deurbel/i.test(s.entity_id),
  );
  return matches.length === 1 ? matches[0].entity_id : null;
}

/**
 * Los uitsluitend het vaste intercomendpoint op dat werkelijk bij de lokale
 * deurbel hoort. Een model of hogere laag kan hier geen willekeurige speaker
 * invullen en er bestaat bewust geen fuzzy/defaultfallback.
 */
export function resolveDoorbellIntercomTarget(cameraEntity, stateRows = []) {
  const camera = String(cameraEntity || '').trim().toLowerCase();
  const all = Array.isArray(stateRows) ? stateRows : [];
  if (!/^camera\.[a-z0-9_]+$/u.test(camera)) return null;
  const cameraState = all.find((state) => String(state?.entity_id || '').toLowerCase() === camera);
  if (!cameraState || !/doorbell|deurbel/iu.test(String(cameraState.entity_id || ''))) {
    return null;
  }
  const speakerEntity = doorbellSpeaker(all);
  const speakerState = all.find((state) => state?.entity_id === speakerEntity);
  if (!speakerEntity || !speakerState
    || ['unavailable', 'unknown'].includes(String(speakerState.state || '').toLowerCase())) return null;
  return Object.freeze({
    contract: 'DoorbellIntercomTarget/v1',
    cameraEntity: cameraState.entity_id,
    speakerEntity,
  });
}

/** Los het private deurbelendpoint op tegen de actuele adaptercache. */
export function currentDoorbellIntercomTarget(cameraEntity) {
  return resolveDoorbellIntercomTarget(cameraEntity, cachedStates());
}

/**
 * Eén smalle hoorbare deurbelroute. `speak` krijgt het exact geverifieerde
 * endpoint, zodat haar algemene speakerselectie nooit in beeld komt.
 */
export async function announceOnDoorbellIntercom(text, cameraEntity, options = {}) {
  const message = String(text || '').trim();
  if (!message || message.length > 500) {
    return { ok: false, accepted: false, uncertain: false,
      error: 'deurbelspraak vereist 1-500 tekens' };
  }
  try {
    const all = await allStates();
    const target = resolveDoorbellIntercomTarget(cameraEntity, all);
    if (!target) return { ok: false, accepted: false, uncertain: false,
      error: 'geen exact gekoppelde beschikbare deurbelintercom' };
    const expectedSpeaker = String(options?.expectedSpeakerEntity || '').trim();
    if (expectedSpeaker && expectedSpeaker !== target.speakerEntity) {
      return {
        ok: false,
        accepted: false,
        uncertain: false,
        stale: true,
        error: 'de exact gebonden deurbelintercom is intussen gewijzigd',
      };
    }
    const result = await speak(message, target.speakerEntity, {
      ...options,
      noTransportFallback: true,
    });
    return {
      ...result,
      accepted: result?.ok === true,
      uncertain: result?.uncertain === true,
      target: target.speakerEntity,
      camera: target.cameraEntity,
      acceptance: result?.ok === true ? 'home_assistant_call_completed' : null,
    };
  } catch (error) {
    return {
      ok: false,
      accepted: false,
      uncertain: serviceTimeoutIsAmbiguous(error),
      error: errMsg(error),
    };
  }
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
  return [...new Set([config.unifiHost, secret('unifi_protect_host')].filter(Boolean))];
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

function unifiProtectRaw(host, key, pathname, accept, {
  signal = undefined,
  maxBytes = CAMERA_SNAPSHOT_MAX_BYTES,
} = {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      fn(value);
    };
    const req = https.request({
      hostname: host.hostname,
      port: host.port,
      method: 'GET',
      path: pathname,
      rejectUnauthorized: false,
      timeout: 10_000,
      headers: { 'X-API-KEY': key, Accept: accept, 'User-Agent': 'Eva/1.0' },
      ...(signal ? { signal } : {}),
    }, (res) => {
      const chunks = [];
      let size = 0;
      const declared = Number(res.headers['content-length']);
      if (Number.isFinite(declared) && declared > maxBytes) {
        finish(reject, new Error('UniFi Protect antwoord is te groot'));
        res.destroy();
        return;
      }
      res.on('data', (d) => {
        size += d.length;
        if (size > maxBytes) {
          finish(reject, new Error('UniFi Protect antwoord overschrijdt de groottelimiet'));
          res.destroy();
          return;
        }
        chunks.push(d);
      });
      res.on('error', (error) => finish(reject, error));
      res.on('end', () => {
        if (settled) return;
        const body = Buffer.concat(chunks);
        if ((res.statusCode || 500) < 200 || (res.statusCode || 500) >= 300) {
          const preview = body.toString('utf8').slice(0, 160);
          const e = new Error(`UniFi Protect HTTP ${res.statusCode}: ${preview}`);
          e.statusCode = res.statusCode;
          finish(reject, e);
          return;
        }
        finish(resolve, { body, headers: res.headers });
      });
    });
    req.on('error', (error) => finish(reject, error));
    req.setTimeout(10_000, () => req.destroy(new Error('timeout naar UniFi Protect')));
    req.end();
  });
}

async function unifiProtectBuffer(pathname, {
  accept = 'application/json',
  signal = undefined,
  maxBytes = CAMERA_SNAPSHOT_MAX_BYTES,
} = {}) {
  const hosts = unifiProtectHosts().map(parseUnifiHost).filter(Boolean);
  const keys = unifiProtectKeys();
  if (!hosts.length || !keys.length) throw new Error('UniFi Protect niet ingesteld');

  let laatsteFout = null;
  for (const host of hosts) {
    for (const key of keys) {
      try {
        return await unifiProtectRaw(host, key, pathname, accept, { signal, maxBytes });
      } catch (e) {
        laatsteFout = e;
      }
    }
  }
  throw laatsteFout || new Error('UniFi Protect niet bereikbaar');
}

async function unifiProtectCameras({ signal = undefined } = {}) {
  if (unifiProtectCache.cameras && Date.now() - unifiProtectCache.ts < 60_000) return unifiProtectCache.cameras;
  const { body } = await unifiProtectBuffer(`${UNIFI_PROTECT_BASE}/cameras`, {
    signal,
    maxBytes: UNIFI_CAMERA_LIST_MAX_BYTES,
  });
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

async function unifiProtectSnapshot(entityId, { signal = undefined } = {}) {
  try {
    const cameras = await unifiProtectCameras({ signal });
    const camera = kiesUnifiCamera(entityId, cameras);
    if (!camera) return null;
    const { body, headers } = await unifiProtectBuffer(`${UNIFI_PROTECT_BASE}/cameras/${camera.id}/snapshot`, {
      accept: 'image/jpeg', signal, maxBytes: CAMERA_SNAPSHOT_MAX_BYTES,
    });
    const type = String(headers['content-type'] || 'image/jpeg');
    if (!/^image\/jpeg(?:;|$)/iu.test(type) || body.length < 1000
      || body[0] !== 0xff || body[1] !== 0xd8) return null;
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

async function readBoundedImageResponse(response, maxBytes = CAMERA_SNAPSHOT_MAX_BYTES) {
  const contentType = String(response?.headers?.get?.('content-type') || '').toLowerCase();
  if (!/^image\/(?:jpeg|png|webp)(?:;|$)/u.test(contentType)) {
    throw new Error('Cameraantwoord heeft geen toegestaan beeldtype');
  }
  return readBoundedResponseBody(response, maxBytes, 'Cameraantwoord');
}

async function readBoundedResponseBody(response, maxBytes, label) {
  const declared = Number(response?.headers?.get?.('content-length'));
  if (Number.isFinite(declared) && declared > maxBytes) {
    throw new Error(`${label} is te groot`);
  }
  if (response.body?.getReader) {
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = Buffer.from(value);
        size += chunk.length;
        if (size > maxBytes) {
          await reader.cancel(`${label} overschrijdt de groottelimiet`);
          throw new Error(`${label} overschrijdt de groottelimiet`);
        }
        chunks.push(chunk);
      }
    } finally {
      reader.releaseLock();
    }
    return Buffer.concat(chunks);
  }
  const body = Buffer.from(await response.arrayBuffer());
  if (body.length > maxBytes) throw new Error(`${label} overschrijdt de groottelimiet`);
  return body;
}

/** Stilstaand beeld van een camera: eerst direct via UniFi Protect, daarna HA /api/camera_proxy. */
export async function cameraSnapshot(entityId, {
  signal = undefined,
  maxBytes = CAMERA_SNAPSHOT_MAX_BYTES,
} = {}) {
  const direct = await unifiProtectSnapshot(entityId, { signal });
  if (direct) return direct;

  let res;
  try {
    res = await fetch(`${config.haUrl}/api/camera_proxy/${entityId}`, {
      headers: headers(),
      ...(signal ? { signal } : {}),
    });
  } catch (e) {
    throw new Error(`Camerabeeld van ${entityId} ophalen mislukt: ${errMsg(e)}`);
  }
  if (!res.ok) throw new Error(`Camerabeeld van ${entityId} ophalen mislukt: HTTP ${res.status}`);
  const buffer = await readBoundedImageResponse(res, Math.min(maxBytes, CAMERA_SNAPSHOT_MAX_BYTES));
  return { buffer, contentType: res.headers.get('content-type'), source: 'home-assistant' };
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
  return error?.uncertainExecution === true
    || /(?:timeout|timed out|aborted due to timeout|operation was aborted)/i.test(errMsg(error));
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
  const message = normalizeUserFacingText(text).replace(/\s+/g, ' ').trim();
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
    let reportedUnavailable = ['unavailable', 'unknown'].includes(String(targetState.state));
    if (reportedUnavailable) {
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
        reportedUnavailable = false;
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
      ...(reportedUnavailable ? {
        stale_target_state: String(targetState.state),
      } : {}),
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
    const failureText = errMsg(error);
    const safelyRetryable = !uncertain
      && /(?:HTTP 5\d\d|unavailable|onbereikbaar|connection refused|econnrefused|netwerk)/iu
        .test(failureText);
    return {
      ok: false,
      verified: false,
      changed: false,
      uncertain,
      effect_uncertain: uncertain,
      failure_class: safelyRetryable ? 'unavailable'
        : uncertain ? 'uncertain_effect' : 'unknown',
      retryable: safelyRetryable,
      needs_followup: true,
      target: plan.target.entityId,
      target_alias: plan.target.alias,
      service: `${plan.domain}.${plan.service}`,
      reason: uncertain
        ? `De omroep naar ${targetLabel} bleef onbevestigd; er is niet naar een andere speaker uitgeweken.`
        : `De omroep naar ${targetLabel} is mislukt: ${failureText}`,
    };
  }
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

// Voorkeursvolgorde van TTS-stemmen: haar eigen ElevenLabs-stem (zodra die er is),
// dan de natuurlijke Nabu Casa-cloudstem, dan OpenAI, dan het lokale Piper als
// robuuste terugval. Stuk/onbeschikbare engines worden vanzelf overgeslagen.
const TTS_VOORKEUR = ['tts.elevenlabs', 'tts.home_assistant_cloud', 'tts.openai_tts', 'tts.piper'];

/**
 * Maak spraak én bewijs dat de audio écht op te halen is (HTTP 200 + bytes) vóór we
 * een speaker ernaartoe sturen. Zo claimt Eva nooit meer dat ze sprak terwijl er een
 * kapot/onbereikbaar audio-pad uitkwam (de stille-Sonos-bug). Geeft {url, engine} of {error}.
 */
function ttsPreflightLease(isCurrent, timeoutMs) {
  const controller = new AbortController();
  const current = () => {
    try { return isCurrent() === true; } catch { return false; }
  };
  const abort = (reason) => {
    if (!controller.signal.aborted) controller.abort(reason);
  };
  const check = () => {
    if (!current()) abort(new DOMException('TTS-preflight is ingehaald', 'AbortError'));
  };
  const timeout = setTimeout(() => {
    abort(new DOMException('TTS-preflight timeout', 'TimeoutError'));
  }, Math.max(250, Number(timeoutMs) || TTS_PREFLIGHT_TIMEOUT_MS));
  const interval = setInterval(check, 50);
  check();
  return {
    signal: controller.signal,
    current,
    dispose() { clearTimeout(timeout); clearInterval(interval); },
  };
}

function withAbort(promise, signal) {
  if (signal.aborted) return Promise.reject(signal.reason);
  return Promise.race([
    promise,
    new Promise((_, reject) => signal.addEventListener(
      'abort', () => reject(signal.reason), { once: true },
    )),
  ]);
}

async function playableTtsUrl(all, text, {
  isCurrent = () => true,
  timeoutMs = TTS_PREFLIGHT_TIMEOUT_MS,
} = {}) {
  const lease = ttsPreflightLease(isCurrent, timeoutMs);
  let laatste = 'geen werkende TTS-engine';
  try {
    const base = await withAbort(reachableBase(), lease.signal);
    const kandidaten = TTS_VOORKEUR.filter((engine) => all.some(
      (state) => state.entity_id === engine && state.state !== 'unavailable',
    ));
    for (const state of all) {
      if (state.entity_id.startsWith('tts.') && state.state !== 'unavailable'
        && !kandidaten.includes(state.entity_id)) kandidaten.push(state.entity_id);
    }
    for (const engine of kandidaten) {
      if (!lease.current() || lease.signal.aborted) break;
      try {
        const generated = await restCall('POST', '/api/tts_get_url', {
          engine_id: engine, message: text,
        }, { timeoutMs, signal: lease.signal });
        if (!lease.current() || lease.signal.aborted) break;
        const audioPath = generated?.path
          || (generated?.url ? new URL(generated.url).pathname : null);
        if (!audioPath) { laatste = `${engine}: geen audiopad`; continue; }
        const url = base + audioPath; // bereikbaar LAN-adres, niet het interne docker-IP
        const response = await fetch(url, { headers: headers(), signal: lease.signal });
        if (!response.ok) { laatste = `${engine}: audio HTTP ${response.status}`; continue; }
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        if (!/^audio\/(?:mpeg|mp3|wav|x-wav|ogg|webm)(?:;|$)/u.test(contentType)) {
          laatste = `${engine}: ongeldig audiotype`; continue;
        }
        const audio = await readBoundedResponseBody(
          response, TTS_AUDIO_MAX_BYTES, 'TTS-audio',
        );
        if (!lease.current() || lease.signal.aborted) break;
        if (audio.length < 500) { laatste = `${engine}: lege audio (${audio.length}b)`; continue; }
        return { url, engine, bytes: audio.length };
      } catch (error) {
        laatste = `${engine}: ${errMsg(error)}`;
        if (lease.signal.aborted) break;
      }
    }
    return {
      error: lease.signal.aborted
        ? `TTS-preflight afgebroken: ${errMsg(lease.signal.reason)}` : laatste,
      stale: !lease.current(),
    };
  } finally {
    lease.dispose();
  }
}

/**
 * Laat Eva praten door het huis.
 * 1) moderne tts.speak met een tts.*-entiteit; 2) anders legacy tts.*_say.
 * `entityIds` mag null (standaardspeakers), 'overal'/'alle', entity_ids of naamfragmenten zijn.
 * Geeft {ok, via?, error?} terug en throwt nooit.
 */
export async function speak(text, entityIds = null, options = {}) {
  const message = normalizeUserFacingText(text).trim();
  if (!message) return { ok: false, uncertain: false, error: 'spraaktekst is leeg' };
  const isCurrent = typeof options?.isCurrent === 'function' ? options.isCurrent : () => true;
  const mutationOptions = options?.noTransportFallback === true
    ? { noTransportFallback: true } : undefined;
  try {
    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
    const all = await allStates();
    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };

    let spelers = resolveSpeakers(all, entityIds);
    if (!spelers.length) return { ok: false, error: 'geen geschikte mediaspeler gevonden' };

    // 1) Beste route (werkt op Sonos én Cast): maak een TTS-bestand, controleer dat de
    //    audio écht op te halen is, geef de speaker die bereikbare URL en speel het als
    //    'announce' af — dan dempt de muziek even en hoor je écht woorden.
    const audio = await playableTtsUrl(all, message, {
      isCurrent,
      timeoutMs: options?.preflightTimeoutMs,
    });
    if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
    if (!audio.url && options?.noTransportFallback === true) {
      return { ok: false, stale: audio.stale === true, uncertain: false,
        error: audio.error || 'geen aantoonbaar afspeelbare TTS-audio' };
    }
    if (audio.url) {
      try {
        if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
        await callService('media_player', 'play_media', {
          entity_id: spelers,
          media_content_id: audio.url,
          media_content_type: 'music',
          announce: true,
        }, mutationOptions);
        return { ok: true, via: `${audio.engine} → ${spelers.join(', ')}`, engine: audio.engine };
      } catch (error) {
        // Een afgebroken HTTP-wachttermijn zegt niet dat HA de opdracht niet
        // uitvoerde. Meteen een tweede route proberen kan dezelfde zin dubbel
        // afspelen; laat de volgende gebeurtenis liever opnieuw proberen.
        if (options?.noTransportFallback === true || serviceTimeoutIsAmbiguous(error)) {
          return { ok: false, uncertain: true, error: `speakeropdracht bleef onbevestigd: ${errMsg(error)}` };
        }
        // sommige spelers lusten geen 'announce' → probeer tts.speak met dezelfde engine
        try {
          if (!isCurrent()) return { ok: false, stale: true, error: 'ingehaald door een direct gesprek' };
          await callService('tts', 'speak', {
            entity_id: audio.engine, media_player_entity_id: spelers, message,
          }, mutationOptions);
          return { ok: true, via: `${audio.engine} (speak) → ${spelers.join(', ')}`, engine: audio.engine };
        } catch (fallbackError) {
          if (options?.noTransportFallback === true || serviceTimeoutIsAmbiguous(fallbackError)) {
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
        message,
      }, mutationOptions);
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
    await callService('tts', say, { entity_id: spelers, message }, mutationOptions);
    return { ok: true, via: `tts.${say} → ${spelers.join(', ')}` };
  } catch (e) {
    return {
      ok: false,
      uncertain: serviceTimeoutIsAmbiguous(e) || options?.noTransportFallback === true,
      error: errMsg(e),
    };
  }
}
