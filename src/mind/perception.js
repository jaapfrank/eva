import fs from 'node:fs/promises';
import path from 'node:path';
import { bus } from '../bus.js';
import { config } from '../config.js';
import { kvGet, kvSet, logTimeline } from '../db.js';
import { recognizeFaceDetailed, warmupFaceRec } from './facerec.js';
import { sendRegisteredPhoto } from './tools.js';
import { renderCameraNotice } from './persona.js';

const QUIET_MS = Math.max(
  60 * 60_000,
  Number(process.env.EVA_CAMERA_REPORT_QUIET_MINUTES || 90) * 60_000,
);
const SAME_PERSON_QUIET_MS = 6 * 3600_000;
const UNKNOWN_QUIET_MS = 2 * 3600_000;

let started = false;
let processing = false;
const pending = new Map();
const REPORT_KEY = 'camera_report_state_v2';
const REPORT_REGIONS_KEY = 'camera_report_regions_v1';

function reportState() {
  const value = kvGet(REPORT_KEY, null);
  return value && typeof value === 'object'
    ? { byCamera: {}, personByCamera: {}, unknownAt: 0, ...value }
    : { byCamera: {}, personByCamera: {}, unknownAt: 0 };
}

function frameBuffer(frame) {
  if (!frame?.buffer) return null;
  return Buffer.isBuffer(frame.buffer) ? frame.buffer : Buffer.from(frame.buffer);
}

export async function cleanupTemporaryCameraFrames(event) {
  const root = path.resolve(config.workspaceDir, 'camera_trigger_frames');
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

function eventIsFresh(event) {
  const timestamp = Date.parse(
    event?.triggerTs || event?.frame?.capturedTs || event?.changedAt || '',
  );
  return !Number.isFinite(timestamp) || Date.now() - timestamp <= 2 * 60_000;
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

async function handleCameraEvent(event) {
  try {
    if (!['person_detected', 'doorbell'].includes(event?.type) || !eventIsFresh(event)) return;
    const frames = Array.isArray(event.frames) && event.frames.length
      ? event.frames
      : event.frame ? [event.frame] : [];
    const best = await recognizeFrames(frames);
    if (!best?.buffer) return;

    const place = String(event.waar || best.frame?.cameraName || 'de camera').trim();
    const detail = best.detail;
    const state = reportState();
    const key = cameraKey(event);
    const region = reportRegion(key);
    const previous = Number(state.byCamera?.[key] || 0);
    const unknown = !detail?.recognized && detail?.learnable === true;
    const shouldSend = shouldReportCameraEvent({
      type: event.type,
      detail,
      previous,
      previousPerson: state.personByCamera?.[key] || null,
      unknownAt: state.unknownAt,
      frameSize: best.frameSize,
      region,
    });
    if (!shouldSend) {
      const outsideRegion = !!region && Array.isArray(detail?.bbox)
        && !faceInsideReportRegion(detail, best.frameSize, region);
      logTimeline('vision', outsideRegion
        ? `${place}: gezicht buiten het afgesproken meldgebied genegeerd.`
        : `${place}: Protect-detectie beoordeeld, geen meldenswaardig of leerbaar gezicht.`, {
        internalOnly: true,
        camera: event.camera || null,
        face: detail || null,
        frameSize: best.frameSize,
        reportRegion: region,
        outsideReportRegion: outsideRegion,
        suppressed: true,
      });
      return;
    }
    const noticeRecognition = cameraNoticeRecognition(event.type, detail);
    const caption = renderCameraNotice({
      place,
      camera: best.frame?.cameraName || event.camera || place,
      recognition: noticeRecognition,
    });

    const sent = await sendRegisteredPhoto(best.buffer, best.frame?.contentType || 'image/jpeg', caption, null, {
      broadcast: true,
      cameraKey: key,
      description: caption,
      suggestedPerson: noticeRecognition?.recognized ? noticeRecognition.person : null,
      candidateSimilarity: detail?.gelijkenis ?? null,
      candidateMargin: detail?.marge ?? null,
    });
    if (sent?.ok) {
      const now = Date.now();
      state.byCamera = { ...(state.byCamera || {}), [key]: now };
      if (detail?.recognized) {
        state.personByCamera = { ...(state.personByCamera || {}), [key]: detail.person };
      }
      if (unknown) state.unknownAt = now;
      kvSet(REPORT_KEY, state);
    }
    logTimeline('vision', sent?.ok ? caption.split('\n')[0] : `Camerabeeld bij ${place} kon niet worden gemeld.`, {
      internalOnly: !sent?.ok,
      familyVisible: sent?.ok === true,
      camera: event.camera || null,
      face: detail || null,
      photoRef: sent?.photoRef || null,
    });
  } finally {
    await cleanupTemporaryCameraFrames(event);
  }
}

async function drain() {
  if (processing) return;
  processing = true;
  try {
    while (pending.size) {
      const [key, event] = pending.entries().next().value;
      pending.delete(key);
      try {
        await handleCameraEvent(event);
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

export function startPerception() {
  if (started) return;
  started = true;
  warmupFaceRec();
  bus.on('house', (event) => {
    if (!['person_detected', 'doorbell'].includes(event?.type)) return;
    pending.set(cameraKey(event), event);
    drain();
  });
}
