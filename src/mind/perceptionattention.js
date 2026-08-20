import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { config } from '../config.js';
import { db } from '../db.js';
import {
  consumeIssuedCameraTriggerEvent,
  currentDoorbellIntercomTarget,
} from '../integrations/homeassistant.js';
import { redactSensitiveText } from '../security/redaction.js';
import { canonicalJson } from '../util.js';

const CONTEXT = 'DoorbellAttentionContext/v1';
const DECISION = 'DoorbellAttentionDecision/v1';
const PLAN = 'DoorbellAttentionPlan/v1';
const INCIDENT_TTL_MS = 5 * 60_000;
const CLAIM_TTL_MS = 45_000;
const CLUSTER_MS = 20_000;
const CAMERA_ATTENTION_HISTORY_LIMIT = 64;
const CAMERA_EVENT_FRESH_MS = 2 * 60_000;
const CAMERA_EVENT_URGENT_WINDOW_MS = 15_000;
const SHA256 = /^[a-f0-9]{64}$/u;
const CLAIMS = new WeakMap();
const DECISIONS = new WeakMap();
const PLANS = new WeakMap();
const CONSUMED = new WeakSet();
const privateRoot = path.resolve(config.dataDir, 'private', 'perception_incidents');
const attentionDb = /** @type {any} */ (db);

const hash = (value) => createHash('sha256').update(value).digest('hex');
const jsonHash = (value) => hash(canonicalJson(value));
const iso = (value = Date.now()) => new Date(value).toISOString();
const ms = (value) => Date.parse(String(value || ''));
const clamp01 = (value) => Math.max(0, Math.min(1, Number(value) || 0));
const clean = (value, max = 240) => {
  if (typeof value !== 'string' || value !== value.trim() || !value || value.length > max) return null;
  return redactSensitiveText(value) === value ? value : null;
};

function cameraAttentionKey(candidate) {
  return String(candidate?.camera || candidate?.sensor || candidate?.waar || 'onbekende camera');
}

function cameraAttentionLabel(candidate) {
  const place = clean(candidate?.waar, 160);
  if (place) return place;
  return cameraAttentionKey(candidate)
    .replace(/^(?:camera|binary_sensor|event)\./u, '')
    .replaceAll('_', ' ');
}

function cameraAttentionFrame(candidate) {
  const frames = Array.isArray(candidate?.frames) ? candidate.frames : [];
  const frame = frames.find((entry) => Buffer.isBuffer(entry?.buffer))
    || (Buffer.isBuffer(candidate?.frame?.buffer) ? candidate.frame : null);
  return frame?.buffer || null;
}

export function cameraAttentionFingerprint(candidate) {
  const frame = cameraAttentionFrame(candidate);
  if (frame) return hash(frame);
  return jsonHash({
    type: String(candidate?.type || ''),
    camera: cameraAttentionKey(candidate),
    sensor: String(candidate?.sensor || ''),
    place: String(candidate?.waar || ''),
    object: String(candidate?.detection?.object || ''),
  });
}

function cameraAttentionValue(candidate) {
  let value = 0.15;
  if (cameraAttentionFrame(candidate)) value += 0.55;
  if (candidate?.detection && typeof candidate.detection === 'object') value += 0.15;
  if (clean(candidate?.waar, 160)) value += 0.15;
  return clamp01(value);
}

function cameraAttentionReason(entry, candidateCount) {
  const label = cameraAttentionLabel(entry.candidate);
  if (entry.urgent) {
    return `Het camera-incident bij ${label} is bijna te oud om betrouwbaar te beoordelen en krijgt daarom nu voorrang.`;
  }
  if (candidateCount === 1) {
    return `Dit is het enige actuele camera-incident dat nu beoordeeld kan worden.`;
  }
  if (entry.fairness >= 1 && !entry.duplicate) {
    return `De camera bij ${label} is recent minder aan bod geweest en deze waarneming is nieuw.`;
  }
  if (entry.duplicate) {
    return `De herhaalde waarneming bij ${label} is nu toch de meest bruikbare actuele optie.`;
  }
  return `Het camera-incident bij ${label} biedt nu de meest bruikbare actuele waarneming.`;
}

/**
 * Rangschik een reeds toegelaten batch camera-events zonder cameranaamvoorkeur.
 * Urgente veroudering is een harde voorrang; daaronder wegen rotatie, nieuwheid,
 * bruikbaarheid en exacte beeldherhaling samen. History staat nieuwste eerst.
 */
export function selectCameraAttentionCandidate(candidates, {
  history = [],
  lastCameraKey = null,
  now = Date.now(),
  duplicateFingerprints = [],
} = {}) {
  const at = Number(now);
  if (!Array.isArray(candidates) || !Number.isFinite(at)) return null;
  const recent = (Array.isArray(history) ? history : [])
    .filter((entry) => entry && typeof entry === 'object'
      && typeof entry.cameraKey === 'string' && typeof entry.fingerprint === 'string')
    .slice(0, CAMERA_ATTENTION_HISTORY_LIMIT);
  const frequencyWindow = recent.slice(0, 12);
  const knownDuplicates = new Set((Array.isArray(duplicateFingerprints)
    ? duplicateFingerprints : []).filter((value) => typeof value === 'string'));
  const rotationKeys = [...new Set(candidates
    .filter((candidate) => candidate && typeof candidate === 'object')
    .map(cameraAttentionKey))].sort((left, right) => left.localeCompare(right, 'nl-NL'));
  const cursorIndex = rotationKeys.indexOf(String(lastCameraKey || ''));
  const entries = candidates.flatMap((candidate, index) => {
    if (!candidate || typeof candidate !== 'object') return [];
    const cameraKey = cameraAttentionKey(candidate);
    const fingerprint = cameraAttentionFingerprint(candidate);
    const observedAt = ms(candidate.triggerTs || candidate.changedAt || candidate.frame?.capturedTs);
    const ageMs = Number.isFinite(observedAt) ? Math.max(0, at - observedAt) : 0;
    const remainingFreshMs = CAMERA_EVENT_FRESH_MS - ageMs;
    const urgent = candidate.type === 'doorbell'
      || (Number.isFinite(observedAt) && remainingFreshMs <= CAMERA_EVENT_URGENT_WINDOW_MS);
    const previousIndex = recent.findIndex((entry) => entry.cameraKey === cameraKey);
    const cameraIndex = rotationKeys.indexOf(cameraKey);
    const rotationSteps = cursorIndex >= 0 && cameraIndex >= 0
      ? ((cameraIndex - cursorIndex + rotationKeys.length) % rotationKeys.length)
        || rotationKeys.length
      : null;
    const fairness = rotationSteps === null
      ? (previousIndex < 0 ? 1 : Math.min(0.999, (previousIndex + 1) / (recent.length + 1)))
      : 1 - ((rotationSteps - 1) / rotationKeys.length);
    const cameraShare = frequencyWindow.length
      ? frequencyWindow.filter((entry) => entry.cameraKey === cameraKey).length
        / frequencyWindow.length
      : 0;
    const duplicate = knownDuplicates.has(fingerprint)
      || recent.some((entry) => entry.fingerprint === fingerprint);
    const novelty = duplicate ? 0 : 1;
    const value = cameraAttentionValue(candidate);
    const age = clamp01(ageMs / CAMERA_EVENT_FRESH_MS);
    const score = fairness * 4 + novelty * 2 + value + age * 1.5
      - cameraShare * 2.5 - (duplicate ? 3.5 : 0);
    return [{ candidate, index, cameraKey, fingerprint, observedAt, remainingFreshMs,
      urgent, fairness, novelty, value, duplicate, score }];
  });
  if (!entries.length) return null;
  entries.sort((left, right) => {
    if (left.urgent !== right.urgent) return left.urgent ? -1 : 1;
    if (left.urgent && left.remainingFreshMs !== right.remainingFreshMs) {
      return left.remainingFreshMs - right.remainingFreshMs;
    }
    return right.score - left.score
      || (left.observedAt || at) - (right.observedAt || at)
      || left.index - right.index;
  });
  const selected = entries[0];
  return Object.freeze({
    candidate: selected.candidate,
    cameraKey: selected.cameraKey,
    fingerprint: selected.fingerprint,
    reason: cameraAttentionReason(selected, entries.length),
    factors: Object.freeze({ urgent: selected.urgent, fairness: selected.fairness,
      novelty: selected.novelty, value: selected.value, duplicate: selected.duplicate }),
  });
}

/**
 * Vervang de nog niet verwerkte waarneming van dezelfde camera. Het verdrongen
 * tijdelijke bewijs wordt precies hier vrijgegeven en bereikt de drain niet meer.
 */
export async function replacePendingCameraCandidate(
  queue, candidate, { cleanup } = /** @type {any} */ ({}),
) {
  if (!(queue instanceof Map) || !candidate || typeof candidate !== 'object') {
    throw new TypeError('camera candidate queue en event zijn verplicht');
  }
  const key = cameraAttentionKey(candidate);
  const displaced = queue.get(key) || null;
  const fingerprint = cameraAttentionFingerprint(candidate);
  const duplicate = !!displaced
    && cameraAttentionFingerprint(displaced) === fingerprint;
  queue.set(key, candidate);
  if (displaced && displaced !== candidate && typeof cleanup === 'function') {
    await cleanup(displaced);
  }
  return Object.freeze({ key, displaced, fingerprint, duplicate });
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS eva_doorbell_incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_key TEXT NOT NULL UNIQUE,
  physical_sha256 TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  observed_ts TEXT NOT NULL,
  expires_ts TEXT NOT NULL,
  camera TEXT,
  sensor TEXT,
  place TEXT NOT NULL,
  visual_status TEXT NOT NULL,
  recognition_status TEXT,
  recognition_person TEXT,
  recognition_confidence REAL,
  frame_name TEXT,
  frame_sha256 TEXT,
  frame_mime TEXT,
  frame_released_ts TEXT,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL,
  claim_deadline_ts TEXT,
  decision_attempts INTEGER NOT NULL DEFAULT 0,
  settled_ts TEXT,
  terminal_reason TEXT,
  CHECK (visual_status IN ('available','unavailable')),
  CHECK (status IN ('ready','claimed','planned','executing','settled','expired')),
  CHECK (revision>=1),
  CHECK (decision_attempts BETWEEN 0 AND 1),
  CHECK ((visual_status='available' AND frame_name IS NOT NULL AND frame_sha256 IS NOT NULL AND frame_mime IS NOT NULL)
    OR (visual_status='unavailable' AND frame_name IS NULL AND frame_sha256 IS NULL AND frame_mime IS NULL)),
  CHECK (frame_released_ts IS NULL OR frame_name IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_doorbell_incidents_ready ON eva_doorbell_incidents(status,expires_ts,id);
CREATE INDEX IF NOT EXISTS idx_doorbell_incidents_physical ON eva_doorbell_incidents(physical_sha256,observed_ts,id);
CREATE TABLE IF NOT EXISTS eva_doorbell_sources (
  source_sha256 TEXT PRIMARY KEY,
  incident_id INTEGER NOT NULL,
  recorded_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eva_doorbell_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER NOT NULL UNIQUE,
  plan_key TEXT NOT NULL UNIQUE,
  created_ts TEXT NOT NULL,
  action TEXT NOT NULL,
  literal_text TEXT,
  recipient TEXT,
  target_ids_json TEXT,
  camera_entity TEXT,
  speaker_entity TEXT,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL,
  settled_ts TEXT,
  result_sha256 TEXT,
  CHECK (action IN ('silent','household_text','household_photo','intercom')),
  CHECK (status IN ('planned','executing','delivered','silent','failed','effect_uncertain')),
  CHECK (revision>=1)
);
CREATE TABLE IF NOT EXISTS eva_doorbell_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  incident_id INTEGER NOT NULL,
  plan_id INTEGER,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  binding_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS eva_doorbell_sources_no_update BEFORE UPDATE ON eva_doorbell_sources
BEGIN SELECT RAISE(ABORT,'doorbell sources are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_sources_no_delete BEFORE DELETE ON eva_doorbell_sources
BEGIN SELECT RAISE(ABORT,'doorbell sources are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_events_no_update BEFORE UPDATE ON eva_doorbell_events
BEGIN SELECT RAISE(ABORT,'doorbell events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_events_no_delete BEFORE DELETE ON eva_doorbell_events
BEGIN SELECT RAISE(ABORT,'doorbell events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_incidents_no_delete BEFORE DELETE ON eva_doorbell_incidents
BEGIN SELECT RAISE(ABORT,'doorbell incidents cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_incidents_immutable BEFORE UPDATE ON eva_doorbell_incidents
WHEN NEW.incident_key IS NOT OLD.incident_key OR NEW.physical_sha256 IS NOT OLD.physical_sha256
 OR NEW.created_ts IS NOT OLD.created_ts OR NEW.observed_ts IS NOT OLD.observed_ts
 OR NEW.expires_ts IS NOT OLD.expires_ts OR NEW.camera IS NOT OLD.camera
 OR NEW.sensor IS NOT OLD.sensor OR NEW.place IS NOT OLD.place
 OR NEW.visual_status IS NOT OLD.visual_status OR NEW.recognition_status IS NOT OLD.recognition_status
 OR NEW.recognition_person IS NOT OLD.recognition_person
 OR NEW.recognition_confidence IS NOT OLD.recognition_confidence
 OR NEW.frame_name IS NOT OLD.frame_name OR NEW.frame_sha256 IS NOT OLD.frame_sha256
 OR NEW.frame_mime IS NOT OLD.frame_mime
BEGIN SELECT RAISE(ABORT,'doorbell incident evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_incidents_frame_release BEFORE UPDATE ON eva_doorbell_incidents
WHEN NEW.frame_released_ts IS NOT OLD.frame_released_ts AND NOT (
  OLD.frame_released_ts IS NULL AND NEW.frame_released_ts IS NOT NULL
  AND OLD.status IN ('settled','expired') AND NEW.status=OLD.status
)
BEGIN SELECT RAISE(ABORT,'invalid doorbell frame release'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_plans_no_delete BEFORE DELETE ON eva_doorbell_plans
BEGIN SELECT RAISE(ABORT,'doorbell plans cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS eva_doorbell_plans_immutable BEFORE UPDATE ON eva_doorbell_plans
WHEN NEW.incident_id IS NOT OLD.incident_id OR NEW.plan_key IS NOT OLD.plan_key
 OR NEW.created_ts IS NOT OLD.created_ts OR NEW.action IS NOT OLD.action
 OR NEW.literal_text IS NOT OLD.literal_text OR NEW.recipient IS NOT OLD.recipient
 OR NEW.target_ids_json IS NOT OLD.target_ids_json OR NEW.camera_entity IS NOT OLD.camera_entity
 OR NEW.speaker_entity IS NOT OLD.speaker_entity
BEGIN SELECT RAISE(ABORT,'doorbell plan is immutable'); END;
`;

let enabled = false;
let disabledReason = 'not_initialized';

function normalizedSchemaSql(value) {
  const source = String(value || '');
  const literals = [];
  const protectedSql = source.replace(/'(?:''|[^'])*'/gu, (literal) => {
    literals.push(literal);
    return `__doorbell_literal_${literals.length - 1}__`;
  });
  return protectedSql
    .replace(/\bIF\s+NOT\s+EXISTS\b/giu, '')
    .replace(/\s+/gu, ' ')
    .replace(/\s*([(),;=<>+])\s*/gu, '$1')
    .replace(/;+$/u, '')
    .trim()
    .toLowerCase()
    .replace(/__doorbell_literal_(\d+)__/gu, (_match, index) => literals[Number(index)]);
}

function expectedSchemaObjects() {
  const objects = new Map();
  const patterns = [
    /CREATE TABLE IF NOT EXISTS (eva_doorbell_[a-z_]+) \([\s\S]*?\n\);/giu,
    /CREATE INDEX IF NOT EXISTS (idx_doorbell_[a-z_]+)[^;]+;/giu,
    /CREATE TRIGGER IF NOT EXISTS (eva_doorbell_[a-z_]+)[\s\S]*?END;/giu,
  ];
  for (const pattern of patterns) {
    for (const match of SCHEMA.matchAll(pattern)) {
      objects.set(match[1], normalizedSchemaSql(match[0]));
    }
  }
  return objects;
}

try {
  const existing = attentionDb.prepare(`SELECT name FROM sqlite_master
    WHERE name LIKE 'eva_doorbell_%'`).all();
  if (!existing.length) attentionDb.transaction(() => attentionDb.exec(SCHEMA)).immediate();
  const expected = expectedSchemaObjects();
  const actual = new Map(attentionDb.prepare(`SELECT name,sql FROM sqlite_master
    WHERE sql IS NOT NULL AND (
      name LIKE 'eva_doorbell_%' OR name LIKE 'idx_doorbell_%'
      OR tbl_name IN ('eva_doorbell_incidents','eva_doorbell_sources',
        'eva_doorbell_plans','eva_doorbell_events')
    )`)
    .all().map((row) => [row.name, normalizedSchemaSql(row.sql)]));
  enabled = expected.size === 15 && actual.size === expected.size
    && [...expected].every(([name, sql]) => actual.get(name) === sql);
  disabledReason = enabled ? 'ready' : 'schema_mismatch';
} catch {
  enabled = false;
  disabledReason = 'schema_initialization_failed';
}

function event(incidentId, action, binding, planId = null) {
  const ts = iso();
  const eventKey = jsonHash({ incidentId, planId, action, binding, ts, nonce: randomUUID() });
  attentionDb.prepare(`INSERT INTO eva_doorbell_events
    (event_key,incident_id,plan_id,ts,action,binding_sha256) VALUES (?,?,?,?,?,?)`)
    .run(eventKey, incidentId, planId, ts, action, binding);
}

function frameBytesFromEvent(source) {
  const frames = Array.isArray(source.frames) ? source.frames : [];
  const frame = frames.find((candidate) => Buffer.isBuffer(candidate?.buffer))
    || (Buffer.isBuffer(source.frame?.buffer) ? source.frame : null);
  if (!frame) return null;
  const mime = /^(?:image\/jpeg|image\/png|image\/webp)$/u.test(String(frame.contentType || ''))
    ? String(frame.contentType) : 'image/jpeg';
  return { bytes: Buffer.from(frame.buffer), mime };
}

function safeRecognition(detail, visualAvailable) {
  if (!visualAvailable || !detail || typeof detail !== 'object') return null;
  const confidence = Number(detail.gelijkenis);
  if (detail.recognized === true && detail.learnable === true && clean(detail.person, 120)
    && Number.isFinite(confidence) && confidence >= 0 && confidence <= 1) {
    return { status: 'recognized', person: detail.person, confidence };
  }
  return { status: 'unknown', person: null, confidence: null };
}

function incidentRow(id) {
  return attentionDb.prepare('SELECT * FROM eva_doorbell_incidents WHERE id=?').get(Number(id));
}

function planRow(id) {
  return attentionDb.prepare('SELECT * FROM eva_doorbell_plans WHERE id=?').get(Number(id));
}

function perceptionTelegramDeliveryBinding(meta) {
  if (!meta || typeof meta !== 'object') return null;
  const deliveryKey = String(meta.deliveryKey || '').trim();
  const dedupeKey = String(meta.dedupeKey || '').trim();
  const deliveryMatch = deliveryKey
    ? /^perception:(\d+):(\d+)$/u.exec(deliveryKey) : null;
  const dedupeMatch = dedupeKey
    ? /^perception:(\d+):(\d+)(?::chunk:1)?$/u.exec(dedupeKey) : null;
  if ((deliveryKey && !deliveryMatch) || (dedupeKey && !dedupeMatch)
    || (!deliveryMatch && !dedupeMatch)) return null;
  const matches = [deliveryMatch, dedupeMatch].filter(Boolean);
  if (matches.some((match) => match[1] !== matches[0][1] || match[2] !== matches[0][2])) {
    return null;
  }
  return Object.freeze({
    incidentId: Number(matches[0][1]),
    planId: Number(matches[0][2]),
  });
}

function doorbellArchiveRowValid(row) {
  return Boolean(enabled && row && row.role === 'assistant' && row.deleted !== true
    && Number(row.deleted || 0) === 0 && row.processing_status === 'completed'
    && row.meta?.outbound === true && row.meta?.transportAccepted === true
    && row.meta?.retractionPending !== true && row.meta?.retractionUncertain !== true
    && row.meta?.retracted !== true);
}

function doorbellPlanAndIncidentValid(row, binding, plan, incident) {
  return Boolean(plan && incident && Number(plan.incident_id) === binding.incidentId
    && plan.status === 'delivered' && incident.status === 'settled'
    && incident.terminal_reason === 'delivered'
    && SHA256.test(String(plan.result_sha256 || ''))
    && ['household_text', 'household_photo'].includes(plan.action)
    && (plan.action !== 'household_text' || row.kind === 'text')
    && (plan.action !== 'household_photo' || row.kind === 'photo')
    && String(row.content || '') === String(plan.literal_text || ''));
}

function singleDoorbellTextChunkValid(row, plan) {
  return plan.action !== 'household_text'
    || ((row.meta.chunkIndex == null || Number(row.meta.chunkIndex) === 0)
      && (row.meta.chunkCount == null || Number(row.meta.chunkCount) === 1));
}

function doorbellTargetIncludesChat(plan, chatId) {
  let targetIds;
  try { targetIds = JSON.parse(String(plan.target_ids_json || '')); } catch { return false; }
  return Boolean(chatId && Array.isArray(targetIds)
    && targetIds.some((targetId) => String(targetId) === chatId));
}

function doorbellObservationValid(incident, observedAt, place, sensor) {
  return Number.isFinite(ms(observedAt)) && Boolean(place) && Boolean(sensor)
    && ['available', 'unavailable'].includes(incident.visual_status);
}

/**
 * Projecteer uitsluitend bewijs achter een werkelijk geaccepteerde, afgeleverde
 * Telegram-melding. De transportregel, het plan, het incident en de ontvanger
 * moeten exact op elkaar aansluiten; losse tekst of alleen een dedupekey is dus
 * nooit genoeg om een sensorclaim in gesprekscontext te krijgen.
 */
export function verifiedDoorbellTelegramDeliveryEvidence(row) {
  if (!doorbellArchiveRowValid(row)) return null;
  const binding = perceptionTelegramDeliveryBinding(row.meta);
  if (!binding || !Number.isSafeInteger(binding.incidentId) || binding.incidentId <= 0
    || !Number.isSafeInteger(binding.planId) || binding.planId <= 0) return null;
  const plan = planRow(binding.planId);
  const incident = incidentRow(binding.incidentId);
  if (!doorbellPlanAndIncidentValid(row, binding, plan, incident)
    || !singleDoorbellTextChunkValid(row, plan)) return null;
  const chatId = String(row.chat_id || '').trim();
  if (!doorbellTargetIncludesChat(plan, chatId)) return null;
  const observedAt = String(incident.observed_ts || '');
  const place = clean(incident.place, 160);
  const sensor = clean(incident.sensor, 200);
  if (!doorbellObservationValid(incident, observedAt, place, sensor)) return null;
  const recognized = incident.visual_status === 'available'
    && incident.recognition_status === 'recognized'
    && clean(incident.recognition_person, 120);
  return Object.freeze({
    contract: 'VerifiedDoorbellTelegramDeliveryEvidence/v1',
    incidentId: binding.incidentId,
    planId: binding.planId,
    observedAt,
    place,
    sensor,
    trigger: 'doorbell_confirmed',
    visualStatus: incident.visual_status,
    identityStatus: recognized ? 'recognized' : 'not_established',
    identityPerson: recognized ? incident.recognition_person : null,
  });
}

function privateFile(name) {
  if (!/^[a-f0-9-]{36}\.(?:jpg|png|webp)$/u.test(String(name || ''))) return null;
  const file = path.resolve(privateRoot, name);
  return file.startsWith(`${privateRoot}${path.sep}`) ? file : null;
}

function readVerifiedFrame(row) {
  if (!row?.frame_name || !SHA256.test(String(row.frame_sha256 || ''))) return null;
  const file = privateFile(row.frame_name);
  if (!file) return null;
  try {
    const bytes = fs.readFileSync(file);
    if (hash(bytes) !== row.frame_sha256) return null;
    return Object.freeze({ buffer: Buffer.from(bytes), mime: row.frame_mime, sha256: row.frame_sha256 });
  } catch { return null; }
}

function physicalIdentity(source) {
  const camera = clean(source.camera, 200);
  const sensor = clean(source.sensor, 200);
  const rawSensor = sensor?.toLowerCase() || null;
  const sensorStem = rawSensor
    ? rawSensor.replace(/^[a-z_]+\./u, '').replace(/(?:(?:_doorbell|_deurbel))+$/u, '')
      || rawSensor.replace(/^[a-z_]+\./u, '')
    : null;
  return jsonHash({
    sensorStem,
    camera: sensorStem ? null : camera?.toLowerCase() || null,
  });
}

export function perceptionAttentionAvailable() { return enabled; }

/**
 * Consumeer exact één door de HA-adapter uitgegeven deurbelevent. De caller
 * levert alleen een lokale herkenningshint; bron, bytes en tijd komen uit het
 * onforgeable eventobject zelf.
 */
export function recordDoorbellEvidence(sourceEvent, { recognition = null } = {}) {
  if (!enabled) return Object.freeze({ ok: false, reason: disabledReason });
  const source = consumeIssuedCameraTriggerEvent(sourceEvent);
  if (!source || source.type !== 'doorbell') throw new TypeError('geen exact uitgegeven deurbelevent');
  const observedTs = iso(ms(source.triggerTs || source.changedAt));
  const observedMs = ms(observedTs);
  if (!Number.isFinite(observedMs) || observedMs > Date.now() + 30_000
    || Date.now() - observedMs > 2 * 60_000) throw new TypeError('deurbelevent is niet vers');
  const camera = clean(source.camera, 200);
  const sensor = clean(source.sensor, 200);
  if (!camera && !sensor) throw new TypeError('deurbelbron ontbreekt');
  const correlation = clean(source.correlationId, 300);
  if (!correlation) throw new TypeError('deurbelcorrelatie ontbreekt');
  const sourceSha = jsonHash({ source: 'home_assistant', correlation });
  const physicalSha = physicalIdentity(source);
  const place = clean(source.waar || 'de voordeur', 160);
  const frame = frameBytesFromEvent(source);
  const recognized = safeRecognition(recognition, !!frame);
  const frameSha = frame ? hash(frame.bytes) : null;
  const nowTs = iso();
  let frameName = null;
  try {
    return attentionDb.transaction(() => {
      const replay = attentionDb.prepare(`SELECT i.* FROM eva_doorbell_sources s
        JOIN eva_doorbell_incidents i ON i.id=s.incident_id WHERE s.source_sha256=?`).get(sourceSha);
      if (replay) return Object.freeze({ ok: true, replayed: true,
        incidentId: Number(replay.id), status: replay.status });
      const recent = attentionDb.prepare(`SELECT * FROM eva_doorbell_incidents
        WHERE physical_sha256=? AND observed_ts>=? AND observed_ts<=?
        ORDER BY id DESC LIMIT 1`).get(
        physicalSha, iso(observedMs - CLUSTER_MS), iso(observedMs + CLUSTER_MS),
      );
      if (recent) {
        attentionDb.prepare('INSERT INTO eva_doorbell_sources VALUES (?,?,?)')
          .run(sourceSha, recent.id, nowTs);
        event(recent.id, 'source_coalesced', sourceSha);
        return Object.freeze({ ok: true, replayed: false, coalesced: true,
          incidentId: Number(recent.id), status: recent.status });
      }
      if (frame) {
        fs.mkdirSync(privateRoot, { recursive: true, mode: 0o700 });
        fs.chmodSync(privateRoot, 0o700);
        const extension = frame.mime === 'image/png' ? 'png'
          : frame.mime === 'image/webp' ? 'webp' : 'jpg';
        frameName = `${randomUUID()}.${extension}`;
        const framePath = path.join(privateRoot, frameName);
        fs.writeFileSync(framePath, frame.bytes, { mode: 0o600, flag: 'wx' });
        fs.chmodSync(framePath, 0o600);
      }
      const incidentKey = jsonHash({ physicalSha, sourceSha, observedTs });
      const inserted = attentionDb.prepare(`INSERT INTO eva_doorbell_incidents (
        incident_key,physical_sha256,created_ts,observed_ts,expires_ts,camera,sensor,place,
        visual_status,recognition_status,recognition_person,recognition_confidence,
        frame_name,frame_sha256,frame_mime,status,revision,claim_deadline_ts,
        decision_attempts,settled_ts,terminal_reason
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ready',1,NULL,0,NULL,NULL)`).run(
        incidentKey, physicalSha, nowTs, observedTs, iso(observedMs + INCIDENT_TTL_MS),
        camera, sensor, place, frame ? 'available' : 'unavailable',
        recognized?.status || null, recognized?.person || null, recognized?.confidence ?? null,
        frameName, frameSha, frame?.mime || null,
      );
      const id = Number(inserted.lastInsertRowid);
      attentionDb.prepare('INSERT INTO eva_doorbell_sources VALUES (?,?,?)').run(sourceSha, id, nowTs);
      event(id, 'recorded', sourceSha);
      return Object.freeze({ ok: true, replayed: false, coalesced: false,
        incidentId: id, status: 'ready' });
    }).immediate();
  } catch (error) {
    if (frameName) fs.rmSync(path.join(privateRoot, frameName), { force: true });
    throw error;
  }
}

function recipientBindings(bindings) {
  if (!Array.isArray(bindings) || bindings.length > 30) return null;
  const result = [];
  for (const binding of bindings) {
    const label = clean(binding?.label, 120);
    const ids = Array.isArray(binding?.chatIds)
      ? [...new Set(binding.chatIds.map(String).map((id) => id.trim()).filter(Boolean))].sort() : [];
    if (!label || !ids.length) return null;
    result.push(Object.freeze({ label, chatIds: Object.freeze(ids) }));
  }
  return Object.freeze(result);
}

function expireClaims(nowTs) {
  const rows = attentionDb.prepare(`SELECT * FROM eva_doorbell_incidents
    WHERE status='claimed' AND claim_deadline_ts<=?`).all(nowTs);
  for (const row of rows) {
    const changed = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='expired',revision=revision+1,
      claim_deadline_ts=NULL,settled_ts=?,terminal_reason='decision_deadline'
      WHERE id=? AND status='claimed' AND revision=?`).run(nowTs, row.id, row.revision);
    if (Number(changed.changes) === 1) event(row.id, 'decision_expired', hash(row.incident_key));
  }
  const ready = attentionDb.prepare(`SELECT id,revision,incident_key FROM eva_doorbell_incidents
    WHERE status='ready' AND expires_ts<=?`).all(nowTs);
  for (const row of ready) {
    const changed = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='expired',
      revision=revision+1,settled_ts=?,terminal_reason='incident_deadline'
      WHERE id=? AND status='ready' AND revision=?`).run(nowTs, row.id, row.revision);
    if (Number(changed.changes) === 1) event(row.id, 'incident_expired', hash(row.incident_key));
  }
  const planned = attentionDb.prepare(`SELECT p.id AS plan_id,p.incident_id,p.plan_key
    FROM eva_doorbell_plans p JOIN eva_doorbell_incidents i ON i.id=p.incident_id
    WHERE p.status='planned' AND i.status='planned' AND i.expires_ts<=?`).all(nowTs);
  for (const row of planned) {
    const resultSha = hash(`planned-deadline:${row.plan_key}`);
    const changedPlan = attentionDb.prepare(`UPDATE eva_doorbell_plans SET status='failed',
      revision=revision+1,settled_ts=?,result_sha256=?
      WHERE id=? AND status='planned'`).run(nowTs, resultSha, row.plan_id);
    const changedIncident = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='expired',
      revision=revision+1,settled_ts=?,terminal_reason='incident_deadline'
      WHERE id=? AND status='planned'`).run(nowTs, row.incident_id);
    if (Number(changedPlan.changes) !== 1 || Number(changedIncident.changes) !== 1) {
      throw new Error('planned deadline CAS conflict');
    }
    event(row.incident_id, 'planned_expired_pre_effect', resultSha, row.plan_id);
  }
}

export function claimReadyPerceptionIncident({ householdRecipients = [], now = Date.now() } = {}) {
  if (!enabled) return null;
  const recipients = recipientBindings(householdRecipients);
  if (!recipients || !Number.isFinite(Number(now))) throw new TypeError('ongeldige incidentbindings');
  const issued = attentionDb.transaction(() => {
    const nowTs = iso(now);
    expireClaims(nowTs);
    const row = attentionDb.prepare(`SELECT * FROM eva_doorbell_incidents
      WHERE status='ready' AND expires_ts>? ORDER BY observed_ts,id LIMIT 1`).get(nowTs);
    if (!row) return null;
    const intercom = row.camera ? currentDoorbellIntercomTarget(row.camera) : null;
    const frame = readVerifiedFrame(row);
    const actions = ['silent'];
    if (recipients.length) actions.push('household_text');
    if (recipients.length && frame) actions.push('household_photo');
    if (intercom) actions.push('intercom');
    const deadline = iso(Math.min(ms(row.expires_ts), Number(now) + CLAIM_TTL_MS));
    const changed = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='claimed',
      revision=revision+1,claim_deadline_ts=?,decision_attempts=1
      WHERE id=? AND status='ready' AND revision=?`).run(deadline, row.id, row.revision);
    if (Number(changed.changes) !== 1) return null;
    const context = Object.freeze({
      contract: CONTEXT,
      incident: Object.freeze({ id: Number(row.id), deadlineAt: row.expires_ts }),
      observation: Object.freeze({ type: 'doorbell', place: row.place,
        visual: frame ? 'available' : 'unavailable', localRecognition: row.recognition_status
          ? Object.freeze({ status: row.recognition_status, person: row.recognition_person || null,
            confidence: row.recognition_confidence == null ? null : Number(row.recognition_confidence) }) : null }),
      choices: Object.freeze({ actions: Object.freeze(actions),
        householdRecipients: Object.freeze(recipients.map((binding) => binding.label)) }),
    });
    event(row.id, 'claimed', jsonHash({ actions, recipients, intercom }));
    return Object.freeze({ incidentId: Number(row.id), revision: Number(row.revision) + 1,
      deadline, context, recipients, intercom, actions: Object.freeze(actions), frame });
  }).immediate();
  if (!issued) return null;
  const claim = Object.freeze({});
  CLAIMS.set(claim, issued);
  return claim;
}

export function perceptionIncidentContext(claim) {
  const issued = CLAIMS.get(claim);
  return issued && !CONSUMED.has(claim) ? issued.context : null;
}

export function perceptionIncidentDecisionFrame(claim) {
  const issued = CLAIMS.get(claim);
  return issued && !CONSUMED.has(claim) && issued.frame
    ? Object.freeze({ buffer: Buffer.from(issued.frame.buffer), mime: issued.frame.mime,
      sha256: issued.frame.sha256 }) : null;
}

export function normalizePerceptionDecision(claim, candidate) {
  const issued = CLAIMS.get(claim);
  if (!issued || CONSUMED.has(claim) || !candidate || typeof candidate !== 'object'
    || candidate.contract !== DECISION || !issued.actions.includes(candidate.action)) return null;
  let expected = ['action', 'contract'];
  if (['household_text', 'household_photo'].includes(candidate.action)) expected.push('recipient', 'text');
  if (candidate.action === 'intercom') expected.push('text');
  if (canonicalJson(Object.keys(candidate).sort()) !== canonicalJson(expected.sort())) return null;
  const limit = candidate.action === 'intercom' ? 500
    : candidate.action === 'household_photo' ? 1_024 : 4_000;
  const text = candidate.action === 'silent' ? null : clean(candidate.text, limit);
  const recipient = ['household_text', 'household_photo'].includes(candidate.action)
    ? clean(candidate.recipient, 120) : null;
  const binding = recipient ? issued.recipients.find((item) => item.label === recipient) : null;
  if ((candidate.action !== 'silent' && !text) || (recipient && !binding)) return null;
  const decision = Object.freeze({ contract: DECISION, action: candidate.action,
    ...(text ? { text } : {}), ...(recipient ? { recipient } : {}) });
  DECISIONS.set(decision, Object.freeze({ claim, binding, sha256: jsonHash(decision) }));
  return decision;
}

export function issuePerceptionIncidentPlan(claim, decision) {
  const issued = CLAIMS.get(claim);
  const normalized = DECISIONS.get(decision);
  if (!issued || !normalized || normalized.claim !== claim || CONSUMED.has(claim)
    || CONSUMED.has(decision)) throw new TypeError('besluit is niet exact aan de claim gebonden');
  const planId = attentionDb.transaction(() => {
    const incident = incidentRow(issued.incidentId);
    if (!incident || incident.status !== 'claimed' || Number(incident.revision) !== issued.revision
      || incident.claim_deadline_ts !== issued.deadline
      || Date.now() >= ms(issued.deadline) || Date.now() >= ms(incident.expires_ts)) {
      throw new Error('incident claim CAS conflict');
    }
    const planKey = jsonHash({ incidentKey: incident.incident_key, decision: normalized.sha256 });
    const targetIds = normalized.binding?.chatIds || null;
    const inserted = attentionDb.prepare(`INSERT INTO eva_doorbell_plans (
      incident_id,plan_key,created_ts,action,literal_text,recipient,target_ids_json,
      camera_entity,speaker_entity,status,revision,settled_ts,result_sha256
    ) VALUES (?,?,?,?,?,?,?,?,?,'planned',1,NULL,NULL)`).run(
      incident.id, planKey, iso(), decision.action, decision.text || null,
      decision.recipient || null, targetIds ? canonicalJson(targetIds) : null,
      decision.action === 'intercom' ? issued.intercom?.cameraEntity || null : null,
      decision.action === 'intercom' ? issued.intercom?.speakerEntity || null : null,
    );
    const changed = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='planned',
      revision=revision+1,claim_deadline_ts=NULL WHERE id=? AND status='claimed' AND revision=?`)
      .run(incident.id, incident.revision);
    if (Number(changed.changes) !== 1) throw new Error('incident plan CAS conflict');
    event(incident.id, 'plan_issued', normalized.sha256, Number(inserted.lastInsertRowid));
    return Number(inserted.lastInsertRowid);
  }).immediate();
  const capability = Object.freeze({});
  PLANS.set(capability, Object.freeze({ planId }));
  CONSUMED.add(claim);
  CONSUMED.add(decision);
  return capability;
}

function executeProjection(row, incident) {
  let targetIds = null;
  try { targetIds = row.target_ids_json ? JSON.parse(row.target_ids_json) : null; } catch { return null; }
  const frame = row.action === 'household_photo' ? readVerifiedFrame(incident) : null;
  if (row.action === 'household_photo' && !frame) return null;
  return Object.freeze({ contract: PLAN, planId: Number(row.id), incidentId: Number(row.incident_id),
    expiresAt: incident.expires_ts,
    action: row.action, text: row.literal_text || null, recipient: row.recipient || null,
    targetIds: targetIds ? Object.freeze(targetIds) : null,
    cameraEntity: row.camera_entity || null, speakerEntity: row.speaker_entity || null,
    frame });
}

/**
 * Claim het fysieke effect vóór I/O. Iedere executing-row na een crash wordt
 * effect_uncertain en is nooit opnieuw uitvoerbaar.
 */
export function beginPerceptionIncidentEffect(capability) {
  const issued = PLANS.get(capability);
  if (!issued || CONSUMED.has(capability)) return null;
  return attentionDb.transaction(() => {
    const row = planRow(issued.planId);
    if (!row || row.status !== 'planned') return null;
    const incident = incidentRow(row.incident_id);
    if (!incident || Date.now() >= ms(incident.expires_ts)) return null;
    const projection = executeProjection(row, incident);
    if (!projection) return null;
    if (row.action === 'silent') return projection;
    const changedPlan = attentionDb.prepare(`UPDATE eva_doorbell_plans SET status='executing',
      revision=revision+1 WHERE id=? AND status='planned' AND revision=?`).run(row.id, row.revision);
    const changedIncident = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='executing',
      revision=revision+1 WHERE id=? AND status='planned' AND revision=?`)
      .run(incident.id, incident.revision);
    if (Number(changedPlan.changes) !== 1 || Number(changedIncident.changes) !== 1) {
      throw new Error('deurbeleffect CAS conflict');
    }
    event(incident.id, 'effect_claimed', hash(row.plan_key), row.id);
    return projection;
  }).immediate();
}

export function settlePerceptionIncidentPlan(capability, outcome) {
  const issued = PLANS.get(capability);
  if (!issued || CONSUMED.has(capability)) throw new TypeError('plan is niet actief');
  const settled = attentionDb.transaction(() => {
    const row = planRow(issued.planId);
    const incident = row ? incidentRow(row.incident_id) : null;
    if (!row || !incident) throw new Error('plan ontbreekt');
    let status;
    let reason;
    if (row.action === 'silent' && row.status === 'planned' && outcome?.result === 'silent') {
      status = 'silent'; reason = 'silent';
    } else if (row.status === 'executing' && outcome?.result === 'delivered') {
      status = 'delivered'; reason = 'delivered';
    } else if (row.status === 'executing' && outcome?.result === 'failed_pre_effect') {
      status = 'failed'; reason = 'failed_pre_effect';
    } else if (row.status === 'executing' && outcome?.result === 'effect_uncertain') {
      status = 'effect_uncertain'; reason = 'effect_uncertain';
    } else throw new TypeError('ongeldige planuitkomst');
    const resultSha = jsonHash({ result: outcome.result,
      receipt: String(outcome.receipt || ''), error: String(outcome.error || '') });
    const settledTs = iso();
    const changedPlan = attentionDb.prepare(`UPDATE eva_doorbell_plans SET status=?,revision=revision+1,
      settled_ts=?,result_sha256=? WHERE id=? AND status=? AND revision=?`).run(status, settledTs,
      resultSha, row.id, row.status, row.revision);
    const changedIncident = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='settled',
      revision=revision+1,settled_ts=?,terminal_reason=?
      WHERE id=? AND status=? AND revision=?`).run(settledTs, reason,
      incident.id, incident.status, incident.revision);
    if (Number(changedPlan.changes) !== 1 || Number(changedIncident.changes) !== 1) {
      throw new Error('deurbelsettlement CAS conflict');
    }
    event(incident.id, 'settled', resultSha, row.id);
    return Object.freeze({ ok: true, status });
  }).immediate();
  CONSUMED.add(capability);
  return settled;
}

export function failPerceptionIncidentClaim(claim, error = '', terminalReason = 'decision_failed') {
  const issued = CLAIMS.get(claim);
  if (!issued || CONSUMED.has(claim)) return false;
  if (!['decision_failed', 'superseded_pre_effect'].includes(terminalReason)) {
    throw new TypeError('ongeldige pre-effect uitkomst');
  }
  const changed = attentionDb.transaction(() => {
    const result = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET status='expired',
      revision=revision+1,claim_deadline_ts=NULL,settled_ts=?,terminal_reason=?
      WHERE id=? AND status='claimed' AND revision=?`).run(
      iso(), terminalReason, issued.incidentId, issued.revision,
    );
    if (Number(result.changes) !== 1) return false;
    event(issued.incidentId, terminalReason, hash(String(error || terminalReason)));
    return true;
  }).immediate();
  if (!changed) return false;
  CONSUMED.add(claim);
  return true;
}

/** Crash-recovery: executing betekende dat I/O kon zijn begonnen; nooit replay. */
export function recoverPerceptionIncidents() {
  if (!enabled) return Object.freeze({ uncertain: 0, expired: 0 });
  return attentionDb.transaction(() => {
    const planned = attentionDb.prepare(`SELECT p.* FROM eva_doorbell_plans p
      JOIN eva_doorbell_incidents i ON i.id=p.incident_id
      WHERE p.status='planned' AND i.status='planned' AND i.expires_ts>? ORDER BY p.id`)
      .all(iso());
    const executing = attentionDb.prepare(`SELECT p.id AS plan_id,p.incident_id,p.plan_key
      ,p.revision AS plan_revision,i.revision AS incident_revision
      FROM eva_doorbell_plans p JOIN eva_doorbell_incidents i ON i.id=p.incident_id
      WHERE p.status='executing' AND i.status='executing'`).all();
    for (const row of executing) {
      const resultSha = hash(`crash-uncertain:${row.plan_key}`);
      const settledTs = iso();
      const changedPlan = attentionDb.prepare(`UPDATE eva_doorbell_plans SET
        status='effect_uncertain',revision=revision+1,settled_ts=?,result_sha256=?
        WHERE id=? AND status='executing' AND revision=?`)
        .run(settledTs, resultSha, row.plan_id, row.plan_revision);
      const changedIncident = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET
        status='settled',revision=revision+1,settled_ts=?,terminal_reason='effect_uncertain'
        WHERE id=? AND status='executing' AND revision=?`)
        .run(settledTs, row.incident_id, row.incident_revision);
      if (Number(changedPlan.changes) !== 1 || Number(changedIncident.changes) !== 1) {
        throw new Error('deurbelrecovery CAS conflict');
      }
      event(row.incident_id, 'crash_effect_uncertain', resultSha, row.plan_id);
    }
    expireClaims(iso());
    return Object.freeze({ uncertain: executing.length, recoverablePlans: planned.length });
  }).immediate();
}

export function claimPlannedPerceptionIncident() {
  if (!enabled) return null;
  const row = attentionDb.prepare(`SELECT p.* FROM eva_doorbell_plans p
    JOIN eva_doorbell_incidents i ON i.id=p.incident_id
    WHERE p.status='planned' AND i.status='planned' AND i.expires_ts>?
    ORDER BY p.id LIMIT 1`).get(iso());
  if (!row) return null;
  const capability = Object.freeze({});
  PLANS.set(capability, Object.freeze({ planId: Number(row.id) }));
  return capability;
}

export function cleanupPerceptionFrames() {
  if (!enabled) return 0;
  const rows = attentionDb.prepare(`SELECT * FROM eva_doorbell_incidents i
    WHERE i.frame_name IS NOT NULL AND i.frame_released_ts IS NULL
      AND i.status IN ('settled','expired')
      AND NOT EXISTS (SELECT 1 FROM eva_doorbell_incidents active
        WHERE active.frame_name=i.frame_name AND active.status NOT IN ('settled','expired'))`).all();
  let removed = 0;
  for (const row of rows) {
    const file = privateFile(row.frame_name);
    if (!file) continue;
    try {
      fs.rmSync(file, { force: true });
      const releasedTs = iso();
      const changed = attentionDb.transaction(() => {
        const result = attentionDb.prepare(`UPDATE eva_doorbell_incidents SET frame_released_ts=?
          WHERE id=? AND frame_released_ts IS NULL AND status IN ('settled','expired')`)
          .run(releasedTs, row.id);
        if (Number(result.changes) === 1) {
          event(row.id, 'frame_released', hash(row.frame_sha256));
        }
        return Number(result.changes);
      }).immediate();
      removed += changed;
    } catch { /* later opnieuw */ }
  }

  // Een proces kan tussen de private file-write en de SQLite-commit stoppen.
  // Verwijder uitsluitend oude UUID-bestanden die door geen enkele rij worden
  // genoemd; verse bestanden blijven buiten deze crashrecovery.
  try {
    const referenced = new Set(attentionDb.prepare(`SELECT frame_name FROM eva_doorbell_incidents
      WHERE frame_name IS NOT NULL`).all().map((row) => row.frame_name));
    const orphanBefore = Date.now() - 10 * 60_000;
    for (const name of fs.readdirSync(privateRoot)) {
      if (referenced.has(name) || !privateFile(name)) continue;
      const file = privateFile(name);
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.mtimeMs > orphanBefore) continue;
      fs.rmSync(file, { force: true });
      removed += 1;
    }
  } catch (error) {
    if (String(error?.code || '') !== 'ENOENT') throw error;
  }
  return removed;
}

/** Eenmalige, exact begrensde privacymigratie van het vroegere workspacepad. */
export function migrateLegacyDoorbellFrameDirectory() {
  const legacy = path.resolve(config.workspaceDir, 'camera_trigger_frames');
  if (legacy !== path.join(path.resolve(config.workspaceDir), 'camera_trigger_frames')) return 0;
  const quarantine = path.resolve(config.dataDir, 'private', 'legacy_camera_trigger_frames');
  let migrated = 0;
  try {
    const directory = fs.lstatSync(legacy);
    if (!directory.isDirectory() || directory.isSymbolicLink()) return 0;
    fs.mkdirSync(quarantine, { recursive: true, mode: 0o700 });
    for (const name of fs.readdirSync(legacy)) {
      if (!/\.(?:jpe?g|png|webp)$/iu.test(name)) continue;
      const file = path.resolve(legacy, name);
      if (!file.startsWith(`${legacy}${path.sep}`)) continue;
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.isSymbolicLink()) continue;
      const target = path.join(quarantine, `${randomUUID()}${path.extname(name).toLowerCase()}`);
      fs.renameSync(file, target);
      fs.chmodSync(target, 0o600);
      migrated += 1;
    }
    fs.rmdirSync(legacy);
  } catch (error) {
    if (!['ENOENT', 'ENOTEMPTY'].includes(String(error?.code || ''))) throw error;
  }
  return migrated;
}

export function perceptionAttentionStatus() {
  if (!enabled) return Object.freeze({ enabled: false, reason: disabledReason });
  const counts = attentionDb.prepare(`SELECT status,COUNT(*) AS count FROM
    eva_doorbell_incidents GROUP BY status`).all();
  return Object.freeze({ enabled: true, reason: disabledReason,
    counts: Object.freeze(Object.fromEntries(counts.map((row) => [row.status, Number(row.count)]))) });
}

export const PERCEPTION_ATTENTION_CONTRACTS = Object.freeze({ context: CONTEXT,
  decision: DECISION, plan: PLAN });
