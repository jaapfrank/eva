import fs from 'node:fs/promises';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import ffmpegPath from 'ffmpeg-static';
import { config } from '../config.js';
import { cameraSnapshot, callService, getState } from './homeassistant.js';

const execFileAsync = promisify(execFile);
const CAPABILITY_PATH = '/api/v2/robot/capabilities/HighResolutionManualControlCapability';
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function turnIsCurrent(isCurrent) {
  try { return typeof isCurrent !== 'function' || isCurrent() !== false; } catch { return false; }
}

function staleRobotResult(extra = {}) {
  return {
    ok: false,
    verified: false,
    changed: false,
    stale: true,
    failure_class: 'conflict',
    retryable: false,
    effect_uncertain: false,
    needs_followup: false,
    reason: 'voicebeurt is ingehaald; er is geen nieuwe robotactie gestart',
    ...extra,
  };
}

function staleRobotError() {
  return Object.assign(new Error('voicebeurt is ingehaald'), {
    code: 'EVA_TURN_SUPERSEDED',
    failure_class: 'conflict',
    retryable: false,
    effect_uncertain: false,
  });
}

function compact(state) {
  if (!state) return null;
  return {
    entity_id: state.entity_id,
    state: state.state,
    battery: Number(state.attributes?.battery_level ?? state.attributes?.battery ?? NaN),
    status: state.attributes?.status || null,
    error: state.attributes?.error || null,
    fan_speed: state.attributes?.fan_speed || null,
    last_updated: state.last_updated || null,
  };
}

async function valetudo(method, route, body = null, { fetchImpl = fetch, timeoutMs = 5_000 } = {}) {
  const response = await fetchImpl(`${config.robotUrl}${route}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body == null ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`Erik ${method} ${route} gaf HTTP ${response.status}${text ? `: ${text.slice(0, 180)}` : ''}`);
  try { return JSON.parse(text); } catch { return text || null; }
}

export async function robotStatus(options = {}) {
  const base = config.robotEntityId.split('.')[1];
  const [state, batteryState, eventsState, avoidanceState, currentError, wastewater, capabilities, manual] = await Promise.all([
    getState(config.robotEntityId, { preferCache: false, timeoutMs: 5_000 }),
    getState(`sensor.${base}_battery_level`, { preferCache: false, timeoutMs: 5_000 }).catch(() => null),
    getState(`sensor.${base}_events`, { preferCache: false, timeoutMs: 5_000 }).catch(() => null),
    getState(`switch.${base}_obstacle_avoidance`, { preferCache: false, timeoutMs: 5_000 }).catch(() => null),
    getState(`sensor.${base}_error`, { preferCache: false, timeoutMs: 5_000 }).catch(() => null),
    getState(`sensor.${base}_wastewater_dock_component`, { preferCache: false, timeoutMs: 5_000 }).catch(() => null),
    valetudo('GET', '/api/v2/robot/capabilities', null, options).catch((error) => ({ error: error.message })),
    valetudo('GET', CAPABILITY_PATH, null, options).catch((error) => ({ error: error.message })),
  ]);
  const robot = compact(state);
  if (robot && Number.isFinite(Number(batteryState?.state))) robot.battery = Number(batteryState.state);
  const rawEvents = eventsState
    ? Object.entries(eventsState.attributes || {})
      .filter(([key]) => !['icon', 'friendly_name'].includes(key))
      .map(([id, value]) => ({ id, ...(value && typeof value === 'object' ? value : {}) }))
    : [];
  const currentErrorClear = /^(?:no error|none|ok|clear|unknown)?$/i.test(String(currentError?.state || '').trim());
  const wastewaterOk = /^(?:ok|normal|clear)$/i.test(String(wastewater?.state || '').trim());
  const activeEvents = rawEvents.filter((event) => {
    if (event.__class !== 'ErrorStateValetudoEvent') return true;
    if (/wastewater/i.test(String(event.message || '')) && wastewaterOk) return false;
    return !currentErrorClear;
  });
  return {
    ok: !!state,
    verified: !!state,
    changed: false,
    robot,
    obstacle_avoidance: avoidanceState?.state || null,
    active_events: activeEvents,
    resolved_events: rawEvents.filter((event) => !activeEvents.includes(event)),
    current_error: currentError?.state || null,
    wastewater_dock_component: wastewater?.state || null,
    high_resolution_manual_control: manual,
    capabilities: Array.isArray(capabilities) ? capabilities : capabilities?.error ? null : capabilities,
    capability_error: capabilities?.error || null,
  };
}

export async function robotMap() {
  const state = await getState(config.robotMapCameraId, { preferCache: false, timeoutMs: 5_000 });
  const shot = await cameraSnapshot(config.robotMapCameraId);
  // Valetudo's kaartproxy meldt op deze installatie soms image/jpeg terwijl de
  // bytes een PNG zijn. Vertrouw voor verdere verwerking op de magic bytes.
  const png = shot.buffer?.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  return {
    ok: !!shot.buffer?.length,
    verified: !!shot.buffer?.length,
    changed: false,
    state: compact(state),
    buffer: shot.buffer,
    contentType: png ? 'image/png' : shot.contentType,
    source: shot.source,
  };
}

export async function captureRobotFrame({ timeoutMs = 8_000 } = {}) {
  if (!ffmpegPath) throw new Error('ffmpeg ontbreekt voor Erik zijn H264-camera');
  const dir = path.join(config.workspaceDir, 'robot_frames');
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  const target = path.join(dir, `erik-${Date.now()}.jpg`);
  try {
    await execFileAsync(/** @type {string} */ (/** @type {unknown} */ (ffmpegPath)), [
      '-hide_banner', '-loglevel', 'error', '-y',
      '-f', 'h264', '-i', config.robotCameraStream,
      '-frames:v', '1', '-q:v', '3', target,
    ], { timeout: Math.max(2_000, Math.min(15_000, Number(timeoutMs) || 8_000)) });
    const buffer = await fs.readFile(target);
    if (buffer.length < 2_000) throw new Error('cameraframe is leeg of onvolledig');
    return { ok: true, verified: true, buffer, contentType: 'image/jpeg', path: target, bytes: buffer.length };
  } catch (error) {
    await fs.rm(target, { force: true }).catch(() => {});
    throw new Error(`Erik zijn frontcamera gaf geen bruikbaar frame: ${error.message}`);
  }
}

export async function controlRobot(action, {
  isCurrent = () => true,
  getState: readState = getState,
  callService: mutateService = callService,
  delay: wait = delay,
} = {}) {
  const normalized = String(action || '').trim().toLowerCase();
  const services = {
    start: 'start', schoonmaken: 'start', pause: 'pause', pauze: 'pause',
    stop: 'stop', dock: 'return_to_base', terug: 'return_to_base',
  };
  const service = services[normalized];
  if (!service) return { ok: false, changed: false, reason: `onbekende robotactie ${action}` };
  const before = await readState(config.robotEntityId, { preferCache: false });
  // De verse statusuitlezing kan de eventloop vrijgeven. Controleer daarom aan
  // de fysieke grens opnieuw, niet alleen bij binnenkomst van de tool.
  if (!turnIsCurrent(isCurrent)) return staleRobotResult({ before: compact(before) });
  if (!before) return { ok: false, changed: false, reason: `${config.robotEntityId} bestaat niet` };
  if (!turnIsCurrent(isCurrent)) return staleRobotResult({ before: compact(before) });
  await mutateService('vacuum', service, { entity_id: config.robotEntityId });
  const expected = service === 'start'
    ? new Set(['cleaning'])
    : service === 'pause'
      ? new Set(['paused', 'idle'])
      : service === 'return_to_base'
        ? new Set(['returning', 'docked'])
        : new Set(['idle', 'docked', 'paused']);
  let after = null;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    await wait(350);
    after = await readState(config.robotEntityId, { preferCache: false });
    if (expected.has(String(after?.state))) break;
  }
  const verified = expected.has(String(after?.state));
  return {
    ok: verified,
    verified,
    changed: before.state !== after?.state,
    service: `vacuum.${service}`,
    before: compact(before),
    after: compact(after),
    message: verified
      ? `Erik: ${before.state} → ${after.state} via vacuum.${service}.`
      : `vacuum.${service} is verstuurd, maar de eindstaat is niet bevestigd.`,
  };
}

export async function exploreWithRobot({ direction = 'rechts', durationMs = 250 } = {}, {
  isCurrent = () => true,
  robotStatus: readStatus = robotStatus,
  captureRobotFrame: captureFrame = captureRobotFrame,
  valetudo: mutateManualControl = valetudo,
  delay: wait = delay,
  controlRobot: safetyControl = controlRobot,
} = {}) {
  const before = await readStatus();
  if (!turnIsCurrent(isCurrent)) return staleRobotResult({ before });
  const robot = before.robot;
  if (!before.ok) return { ok: false, changed: false, reason: 'Erik is niet bereikbaar' };
  if (!['docked', 'idle', 'paused'].includes(robot.state)) {
    return { ok: false, changed: false, reason: `Erik is al ${robot.state}; geen handmatige verkenning gestart`, before };
  }
  if (Number.isFinite(robot.battery) && robot.battery < 50) {
    return { ok: false, changed: false, reason: `accu is ${robot.battery}%; minimaal 50% vereist`, before };
  }
  if (before.obstacle_avoidance !== 'on') {
    return { ok: false, changed: false, reason: 'obstakeldetectie is niet aantoonbaar ingeschakeld', before };
  }
  const blockingEvent = before.active_events.find((event) => (
    event.__class === 'ErrorStateValetudoEvent' || /error|fout/i.test(String(event.type || ''))
  ));
  if (blockingEvent) {
    return {
      ok: false,
      changed: false,
      reason: `Erik meldt een actieve fout: ${blockingEvent.message || blockingEvent.id}. Eerst oplossen; handmatige modus blijft uit.`,
      before,
    };
  }
  const angle = /links|left|tegen/i.test(direction) ? -120 : 120;
  const pulseMs = Math.max(150, Math.min(500, Math.floor(Number(durationMs) || 250)));
  let firstFrame = null;
  let secondFrame = null;
  let moved = false;
  let failure = null;
  // Bewijs zicht vóór iedere wijziging van de handmatige modus. Ook "enable"
  // verandert de robottoestand en hoort dus niet vóór de veiligheidsvoorwaarde.
  try {
    firstFrame = await captureFrame();
  } catch (error) {
    return {
      ok: false,
      changed: false,
      verified: true,
      reason: error.message,
      before,
      camera_before: null,
      after: before,
      recovery: `geen beweging en handmatige modus bleef ${before.high_resolution_manual_control?.enabled === true ? 'onverwacht aan' : 'uit'}`,
    };
  }
  if (!turnIsCurrent(isCurrent)) return staleRobotResult({ before });
  try {
    if (!turnIsCurrent(isCurrent)) throw staleRobotError();
    await mutateManualControl('PUT', CAPABILITY_PATH, { action: 'enable' });
    await wait(1_000);
    if (!turnIsCurrent(isCurrent)) throw staleRobotError();
    await mutateManualControl('PUT', CAPABILITY_PATH, { action: 'move', vector: { velocity: 0, angle } });
    moved = true;
    await wait(pulseMs);
    await mutateManualControl('PUT', CAPABILITY_PATH, { action: 'move', vector: { velocity: 0, angle: 0 } });
    await wait(500);
    secondFrame = await captureFrame();
  } catch (error) {
    failure = error;
  } finally {
    // Na enable/beweging zijn nulvector en disable veiligheidsopruiming. Die
    // mogen juist ook na supersession doorgaan; er start dan niets nieuws.
    await mutateManualControl('PUT', CAPABILITY_PATH, { action: 'move', vector: { velocity: 0, angle: 0 } }).catch(() => {});
    await mutateManualControl('PUT', CAPABILITY_PATH, { action: 'disable' }).catch(() => {});
  }
  let after = await readStatus();
  if (!['docked', 'idle'].includes(after.robot?.state)) {
    await safetyControl('stop').catch(() => {});
    await safetyControl('dock').catch(() => {});
  }
  let end = await readStatus();
  for (let attempt = 0; attempt < 40 && !['docked', 'idle'].includes(end.robot?.state); attempt += 1) {
    await wait(500);
    end = await readStatus();
  }
  if (failure) {
    return {
      ok: false,
      changed: moved,
      verified: ['docked', 'idle'].includes(end.robot?.state),
      reason: failure.message,
      ...(failure.code === 'EVA_TURN_SUPERSEDED' ? {
        stale: true,
        failure_class: 'conflict',
        retryable: false,
        effect_uncertain: false,
        needs_followup: false,
      } : {}),
      before,
      camera_before: firstFrame ? { path: firstFrame.path, bytes: firstFrame.bytes } : null,
      after: end,
      recovery: `manual control uit; eindstaat ${end.robot?.state || 'onbekend'}`,
    };
  }
  return {
    ok: !!secondFrame && ['docked', 'idle', 'returning'].includes(end.robot?.state),
    verified: !!secondFrame,
    changed: moved,
    direction: angle < 0 ? 'links' : 'rechts',
    duration_ms: pulseMs,
    before,
    camera_before: { buffer: firstFrame.buffer, contentType: firstFrame.contentType, path: firstFrame.path, bytes: firstFrame.bytes },
    camera_after: { buffer: secondFrame.buffer, contentType: secondFrame.contentType, path: secondFrame.path, bytes: secondFrame.bytes },
    after: end,
  };
}
