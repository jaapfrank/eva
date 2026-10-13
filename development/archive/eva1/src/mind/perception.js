import fs from 'node:fs/promises';
import path from 'node:path';
import { bus } from '../bus.js';
import { config } from '../config.js';
import { kvGet, kvSet, logTimeline } from '../db.js';
import {
  consumeIssuedCameraTriggerEvent,
  pendingIssuedCameraTriggerEvent,
} from '../integrations/homeassistant.js';
import { recognizeFaceDetailed, warmupFaceRec } from './facerec.js';
import { sendRegisteredPhoto } from './tools.js';
import { renderCameraNotice } from './persona.js';
import {
  cameraAttentionFingerprint,
  recordDoorbellEvidence,
  replacePendingCameraCandidate,
  selectCameraAttentionCandidate,
} from './perceptionattention.js';

const QUIET_MS = Math.max(
  60 * 60_000,
  config.cameraReportQuietMinutes * 60_000,
);
const SAME_PERSON_QUIET_MS = 6 * 3600_000;
const UNKNOWN_QUIET_MS = 2 * 3600_000;
const REPORT_STATE_LIMIT = 64;

let started = false;
let processing = false;
const pending = new Map();
const pendingDuplicateFingerprints = new Map();
const REPORT_KEY = 'camera_report_state_v2';
const REPORT_REGIONS_KEY = 'camera_report_regions_v1';

function boundedRecord(value, timestampFor) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value)
    .sort((left, right) => Number(timestampFor(right[0], right[1]) || 0)
      - Number(timestampFor(left[0], left[1]) || 0)
      || left[0].localeCompare(right[0], 'nl-NL'))
    .slice(0, REPORT_STATE_LIMIT));
}

function compactReportState(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const rawByCamera = source.byCamera && typeof source.byCamera === 'object'
    ? source.byCamera : {};
  const attentionSelections = (Array.isArray(source.attentionSelections)
    ? source.attentionSelections : [])
    .filter((entry) => entry && typeof entry === 'object'
      && typeof entry.cameraKey === 'string' && typeof entry.fingerprint === 'string')
    .slice(0, REPORT_STATE_LIMIT);
  return {
    ...source,
    byCamera: boundedRecord(rawByCamera, (_key, timestamp) => timestamp),
    personByCamera: boundedRecord(source.personByCamera,
      (key) => rawByCamera[key]),
    unknownAt: Number(source.unknownAt || 0),
    unknownByCamera: boundedRecord(source.unknownByCamera,
      (_key, timestamp) => timestamp),
    attentionSelections,
    attentionCursor: typeof source.attentionCursor === 'string'
      ? source.attentionCursor : attentionSelections[0]?.cameraKey || null,
  };
}

function reportState() {
  const value = kvGet(REPORT_KEY, null);
  return compactReportState(value);
}

function frameBuffer(frame) {
  if (!frame?.buffer) return null;
  return Buffer.isBuffer(frame.buffer) ? frame.buffer : Buffer.from(frame.buffer);
}

export async function cleanupTemporaryCameraFrames(event) {
  const root = path.resolve(config.dataDir, 'private', 'camera_trigger_frames');
  const prefix = `${root}${path.sep}`;
  const frames = Array.isArray(event?.frames) && event.frames.length
    ? event.frames
    : event?.frame ? [event.frame] : [];
  const files = [...new Set(frames
    .filter((frame) => frame?.temporary === true && frame?.path)
    .map((frame) => path.resolve(String(frame.path)))
    .filter((file) => file.startsWith(prefix)))];
  await Promise.all(files.map((file) => fs.unlink(file).catch((error) => {
    if (error?.code !== 'ENOENT') throw error;
  })));
  return files.length;
}

function cameraKey(event) {
  return String(event.camera || event.sensor || event.waar || 'camera');
}

export function cameraReportHistory(state, key) {
  const camera = String(key || 'camera');
  return Object.freeze({
    previous: Number(state?.byCamera?.[camera] || 0),
    previousPerson: state?.personByCamera?.[camera] || null,
    unknownAt: Number(state?.unknownByCamera?.[camera] || 0),
  });
}

export function cameraEventIsFresh(event, now = Date.now()) {
  const timestamp = Date.parse(
    event?.triggerTs || event?.frame?.capturedTs || event?.changedAt || '',
  );
  const at = Number(now);
  return Number.isFinite(timestamp) && Number.isFinite(at)
    && timestamp <= at + 30_000 && at - timestamp <= 2 * 60_000;
}

/** Kies en onthoud precies één kandidaat uit de actuele camerabatch. */
export function selectCameraEventForAttention(events, {
  now = Date.now(),
  duplicateFingerprints = [],
} = {}) {
  const state = reportState();
  const selection = selectCameraAttentionCandidate(events, {
    history: state.attentionSelections,
    lastCameraKey: state.attentionCursor,
    now,
    duplicateFingerprints,
  });
  if (!selection) return null;
  const selectedAt = new Date(Number(now)).toISOString();
  state.attentionSelections = [{
    cameraKey: selection.cameraKey,
    fingerprint: selection.fingerprint,
    selectedAt,
  }, ...(Array.isArray(state.attentionSelections) ? state.attentionSelections : [])]
    .slice(0, REPORT_STATE_LIMIT);
  state.attentionCursor = selection.cameraKey;
  kvSet(REPORT_KEY, compactReportState(state));
  return Object.freeze({
    event: selection.candidate,
    cameraKey: selection.cameraKey,
    fingerprint: selection.fingerprint,
    reason: selection.reason,
    factors: selection.factors,
  });
}

function jpegDimensions(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 12 || buffer[0] !== 0xff || buffer[1] !== 0xd8) return null;
  const sof = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  let offset = 2;
  while (offset + 9 < buffer.length) {
    if (buffer[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = buffer[offset + 1];
    if (sof.has(marker)) {
      return {
        height: buffer.readUInt16BE(offset + 5),
        width: buffer.readUInt16BE(offset + 7),
      };
    }
    if (marker === 0xd8 || marker === 0xd9) {
      offset += 2;
      continue;
    }
    const length = buffer.readUInt16BE(offset + 2);
    if (!Number.isFinite(length) || length < 2) return null;
    offset += 2 + length;
  }
  return null;
}

function reportRegion(camera) {
  const regions = kvGet(REPORT_REGIONS_KEY, null);
  if (!regions || typeof regions !== 'object') return null;
  const region = regions[String(camera || '')];
  return region && typeof region === 'object' ? region : null;
}

function faceInsideReportRegion(detail, frameSize, region) {
  if (!region) return true;
  const bbox = detail?.bbox;
  const width = Number(frameSize?.width || 0);
  const height = Number(frameSize?.height || 0);
  if (!Array.isArray(bbox) || bbox.length < 4 || width <= 0 || height <= 0) return false;
  const x = (Number(bbox[0]) + Number(bbox[2])) / (2 * width);
  const y = (Number(bbox[1]) + Number(bbox[3])) / (2 * height);
  const minX = Number.isFinite(Number(region.minX)) ? Number(region.minX) : 0;
  const maxX = Number.isFinite(Number(region.maxX)) ? Number(region.maxX) : 1;
  const minY = Number.isFinite(Number(region.minY)) ? Number(region.minY) : 0;
  const maxY = Number.isFinite(Number(region.maxY)) ? Number(region.maxY) : 1;
  return x >= minX && x <= maxX && y >= minY && y <= maxY;
}

export function shouldReportCameraEvent({
  type,
  detail,
  previous = 0,
  previousPerson = null,
  unknownAt = 0,
  now = Date.now(),
  quietMs = QUIET_MS,
  frameSize = null,
  region = null,
} = {}) {
  if (type === 'doorbell') return true;
  if (!faceInsideReportRegion(detail, frameSize, region)) return false;
  if (detail?.recognized && detail?.learnable !== true) return false;
  if (detail?.recognized) {
    const samePerson = String(previousPerson || '').toLocaleLowerCase('nl-NL')
      === String(detail.person || '').toLocaleLowerCase('nl-NL');
    return now - Number(previous || 0) >= (samePerson
      ? Math.max(quietMs, SAME_PERSON_QUIET_MS)
      : quietMs);
  }
  if (detail?.learnable !== true) return false;
  return now - Number(previous || 0) >= Math.max(quietMs, UNKNOWN_QUIET_MS)
    && now - Number(unknownAt || 0) >= UNKNOWN_QUIET_MS;
}

export function cameraNoticeRecognition(type, detail = null) {
  if (type !== 'doorbell' || detail?.learnable === true) return detail;
  return {
    ...(detail || {}),
    recognized: false,
    person: null,
    faceCount: 0,
    reason: 'deurbelbeeld niet sterk genoeg voor een naam of identificatievraag',
  };
}

export function cameraAttentionAudit(attention = null) {
  const reason = typeof attention?.reason === 'string' ? attention.reason.trim() : '';
  const factors = attention?.factors && typeof attention.factors === 'object'
    && !Array.isArray(attention.factors) ? attention.factors : null;
  return Object.freeze({
    attentionReason: reason || null,
    attentionFactors: factors,
  });
}

export function cameraFamilyCaption({ place, camera, recognition } = {}) {
  return renderCameraNotice({ place, camera, recognition });
}

function trustedVisibleCameraDelivery(delivery) {
  const acceptedChatIds = Array.isArray(delivery?.acceptedChatIds)
    ? delivery.acceptedChatIds.map((id) => String(id || '').trim()) : [];
  const failedChatIds = Array.isArray(delivery?.failedChatIds)
    ? delivery.failedChatIds.map((id) => String(id || '').trim()) : [];
  if (delivery?.familyVisible !== true || !acceptedChatIds.length
    || acceptedChatIds.some((id) => !id)
    || new Set(acceptedChatIds).size !== acceptedChatIds.length
    || failedChatIds.some((id) => !id || acceptedChatIds.includes(id))
    || new Set(failedChatIds).size !== failedChatIds.length) return false;
  const complete = delivery.ok === true && delivery.accepted === true
    && failedChatIds.length === 0;
  const partial = delivery.ok === false && delivery.accepted === false
    && delivery.partial === true && failedChatIds.length > 0;
  return complete || partial;
}

export function applyVisibleCameraDeliveryState({
  state, key, detail, unknown = false, delivery, now = Date.now(),
} = {}) {
  const timestamp = Number(now);
  const camera = String(key || '').trim();
  if (!state || typeof state !== 'object' || !camera || !Number.isFinite(timestamp)
    || !trustedVisibleCameraDelivery(delivery)) return false;
  state.byCamera = { ...(state.byCamera || {}), [camera]: timestamp };
  if (detail?.recognized) {
    state.personByCamera = { ...(state.personByCamera || {}), [camera]: detail.person };
  }
  if (unknown) {
    state.unknownByCamera = { ...(state.unknownByCamera || {}), [camera]: timestamp };
  }
  return true;
}

async function recognizeFrames(frames) {
  let best = null;
  for (const frame of frames) {
    const buffer = frameBuffer(frame);
    if (!buffer) continue;
    const detail = await recognizeFaceDetailed(buffer);
    const score = detail.recognized && detail.learnable === true
      ? 10 + Number(detail.gelijkenis || 0)
      : Number(detail.largestArea || 0) / 100_000 + Number(detail.detScore || 0);
    if (!best || score > best.score) {
      best = { frame, buffer, detail, score, frameSize: jpegDimensions(buffer) };
    }
  }
  return best;
}

function logSuppressedCameraEvent({ event, best, place, detail, region, attention }) {
  const outsideRegion = !!region && Array.isArray(detail?.bbox)
    && !faceInsideReportRegion(detail, best.frameSize, region);
  logTimeline('vision', outsideRegion
    ? `${place}: gezicht buiten het afgesproken meldgebied genegeerd.`
    : `${place}: Protect-detectie beoordeeld, geen meldenswaardig of leerbaar gezicht.`, {
    internalOnly: true, camera: event.camera || null, face: detail || null,
    frameSize: best.frameSize, reportRegion: region, outsideReportRegion: outsideRegion,
    suppressed: true, ...cameraAttentionAudit(attention),
  });
}

async function deliverPersonDetection({ event, best, place, detail, state, key, unknown, attention }) {
  const noticeRecognition = cameraNoticeRecognition(event.type, detail);
  const caption = cameraFamilyCaption({ place,
    camera: best.frame?.cameraName || event.camera || place, recognition: noticeRecognition,
    reason: attention?.reason });
  const occurrence = event.correlationId
    || event.triggerTs || best.frame?.capturedTs || event.changedAt || null;
  const sent = await sendRegisteredPhoto(best.buffer, best.frame?.contentType || 'image/jpeg', caption,
    null, { broadcast: true, dedupeKey: occurrence ? `camera:${key}:${occurrence}` : null,
      cameraKey: key, description: caption,
      suggestedPerson: noticeRecognition?.recognized ? noticeRecognition.person : null,
      candidateSimilarity: detail?.gelijkenis ?? null, candidateMargin: detail?.marge ?? null });
  const familyVisible = applyVisibleCameraDeliveryState({
    state, key, detail, unknown, delivery: sent,
  });
  if (familyVisible) {
    kvSet(REPORT_KEY, compactReportState(state));
  }
  logTimeline('vision', familyVisible ? caption.split('\n')[0]
    : `Camerabeeld bij ${place} kon niet worden gemeld.`, {
    internalOnly: !familyVisible, familyVisible,
    deliveryAccepted: sent?.ok === true && sent?.accepted === true,
    deliveryPartial: sent?.partial === true,
    acceptedChatIds: Array.isArray(sent?.acceptedChatIds) ? sent.acceptedChatIds : [],
    failedChatIds: Array.isArray(sent?.failedChatIds) ? sent.failedChatIds : [],
    camera: event.camera || null, face: detail || null, photoRef: sent?.photoRef || null,
    ...cameraAttentionAudit(attention),
  });
}

async function handlePersonDetectedEvent(event, attention = null) {
  const frames = Array.isArray(event.frames) && event.frames.length
    ? event.frames : event.frame ? [event.frame] : [];
  const best = await recognizeFrames(frames);
  if (!best?.buffer) return;
  const place = String(event.waar || best.frame?.cameraName || 'de camera').trim();
  const detail = best.detail;
  const state = reportState();
  const key = cameraKey(event);
  const history = cameraReportHistory(state, key);
  const region = reportRegion(key);
  const unknown = !detail?.recognized && detail?.learnable === true;
  const shouldSend = shouldReportCameraEvent({ type: event.type, detail,
    previous: history.previous,
    previousPerson: history.previousPerson, unknownAt: history.unknownAt,
    frameSize: best.frameSize, region });
  if (!shouldSend) {
    logSuppressedCameraEvent({ event, best, place, detail, region, attention });
    return;
  }
  await deliverPersonDetection({ event, best, place, detail, state, key, unknown, attention });
}

async function handleCameraEvent(event, attention = null) {
  try {
    if (event?.type !== 'person_detected' || !cameraEventIsFresh(event)) return;
    await handlePersonDetectedEvent(event, attention);
  } finally { await cleanupTemporaryCameraFrames(event); }
}

async function handleDoorbellEvent(event) {
  try {
    // recordDoorbellEvidence consumeert de adaptercapability en kopieert het
    // beeld synchroon naar de private ledger. Doe dit buiten de seriële
    // herkennings-/fotolane, zodat een vastgelopen gewone cameramelding een
    // echte deurbel nooit vóór durable registratie kan blokkeren.
    const recorded = recordDoorbellEvidence(event);
    if (recorded?.ok) bus.emit('perception_ready', {
      contract: 'PerceptionReadyNotice/v1', incidentId: recorded.incidentId,
    });
  } catch (error) {
    logTimeline('system', `Deurbelevent kon niet worden verwerkt: ${error?.message || error}`, {
      internalOnly: true,
      camera: event?.camera || null,
    });
  } finally { await cleanupTemporaryCameraFrames(event); }
}

async function drain() {
  if (processing) return;
  processing = true;
  try {
    while (pending.size) {
      const current = [...pending.values()].filter((event) => cameraEventIsFresh(event));
      const attention = selectCameraEventForAttention(current, {
        duplicateFingerprints: [...pendingDuplicateFingerprints.values()],
      });
      const event = attention?.event || pending.values().next().value;
      const key = cameraKey(event);
      pending.delete(key);
      pendingDuplicateFingerprints.delete(key);
      try {
        await handleCameraEvent(event, attention);
      } catch (error) {
        logTimeline('system', `Camera-event kon niet worden verwerkt: ${error?.message || error}`, {
          internalOnly: true,
          camera: event?.camera || null,
        });
      }
    }
  } finally {
    processing = false;
  }
}

function enqueueCameraEvent(event) {
  const key = cameraKey(event);
  const displaced = pending.get(key) || null;
  const fingerprint = cameraAttentionFingerprint(event);
  if (displaced && cameraAttentionFingerprint(displaced) === fingerprint) {
    pendingDuplicateFingerprints.set(key, fingerprint);
  } else {
    pendingDuplicateFingerprints.delete(key);
  }
  void replacePendingCameraCandidate(pending, event, {
    cleanup: cleanupTemporaryCameraFrames,
  }).catch((error) => {
    logTimeline('system', `Verdrongen camerabeeld kon niet worden opgeruimd: ${error?.message || error}`, {
      internalOnly: true,
      camera: event?.camera || null,
    });
  }).finally(() => drain());
}

export function startPerception() {
  if (started) return;
  started = true;
  warmupFaceRec();
  bus.on('house', (event) => {
    if (!['person_detected', 'doorbell'].includes(event?.type)) return;
    if (event.type === 'doorbell') {
      // Alleen het exacte, nog niet verbruikte adapterobject krijgt toegang tot
      // core en cleanup. recordDoorbellEvidence consumeert het direct.
      if (!pendingIssuedCameraTriggerEvent(event)) return;
      void handleDoorbellEvent(event);
      return;
    }
    if (!consumeIssuedCameraTriggerEvent(event)) return;
    enqueueCameraEvent(event);
  });
}
