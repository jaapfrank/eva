import { config } from '../config.js';
import { bus } from '../bus.js';
import { kvGet, kvSet, logTimeline, recentMessages } from '../db.js';
import { isNight } from '../util.js';
import { agentStatus, runAutonomousTurn } from './agent.js';
import { claimWorkItem, durableWorkStatus, recoverWorkLeases, releaseWorkLease } from './work.js';
import { dueAttention } from './curiosity.js';
import { dueGoalAttention, selectPortfolioCommitment, updateGoalEpisode } from './portfolio.js';

const KEY = 'eva_autonomy_scheduler_v1';
const CHECK_MS = 5 * 60_000;
const BASE_CADENCE_MS = Math.max(
  (config.autonomyFull ? 5 : 30) * 60_000,
  config.autonomyIntervalMinutes * 60_000,
);
const ACTIVE_WORK_CADENCE_MS = Math.max(
  2 * 60_000,
  Number(process.env.EVA_ACTIVE_WORK_INTERVAL_MINUTES || (config.autonomyFull ? 10 : 30)) * 60_000,
);
const PROGRESS_WORK_CADENCE_MS = Math.max(
  15_000,
  Number(process.env.EVA_PROGRESS_WORK_INTERVAL_MINUTES || (config.autonomyFull ? 0.25 : 5)) * 60_000,
);
const RECENT_CONVERSATION_PAUSE_MS = Math.max(
  60_000,
  config.autonomyConversationPauseMinutes * 60_000,
);
const SIGNAL_MAX_AGE_MS = 24 * 3600_000;
const TEMPERATURE_SIGNAL_DELTA = Math.max(
  0.5,
  Number(process.env.EVA_AUTONOMY_TEMPERATURE_DELTA || 1),
);

let timer = null;
let running = false;
let listenersStarted = false;

function localDayKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function nextDayResetTs(date = new Date()) {
  const reset = new Date(date);
  reset.setDate(reset.getDate() + 1);
  reset.setHours(0, 5, 0, 0);
  return reset.toISOString();
}

function dailyBudgetMs() {
  return config.autonomyDailyWorkMinutes * 60_000;
}

function withCurrentDailyBudget(state, now = new Date()) {
  const day = localDayKey(now);
  if (state.daily_budget_date !== day) {
    state.daily_budget_date = day;
    state.daily_used_ms = 0;
    state.daily_productive_ms = 0;
    state.daily_preempted_ms = 0;
  }
  return state;
}

function remainingDailyMs(state, now = new Date()) {
  const current = withCurrentDailyBudget({ ...state }, now);
  return Math.max(0, dailyBudgetMs() - Number(current.daily_used_ms || 0));
}

function initialState() {
  const now = new Date();
  return {
    last_run_ts: null,
    next_run_ts: new Date(Date.now() + 10 * 60_000).toISOString(),
    last_result: null,
    productive_runs: 0,
    progress_runs: 0,
    quiet_runs: 0,
    pending_signals: [],
    daily_budget_date: localDayKey(now),
    daily_used_ms: 0,
    daily_productive_ms: 0,
    daily_preempted_ms: 0,
  };
}

function readState() {
  const stored = kvGet(KEY, null);
  return stored && typeof stored === 'object' ? { ...initialState(), ...stored } : initialState();
}

function writeState(state) {
  kvSet(KEY, state);
}

function recentInteractiveTurn(status) {
  const turn = status?.last_interactive_turn || status?.last_turn;
  if (turn?.finishedAt && turn.mode !== 'autonomy') {
    const time = Date.parse(turn.finishedAt);
    if (Number.isFinite(time) && Date.now() - time < RECENT_CONVERSATION_PAUSE_MS) return true;
  }
  const persisted = recentMessages(null, 40)
    .reverse()
    .find((message) => message.role === 'user' && message.channel !== 'autonomy');
  const persistedTime = Date.parse(persisted?.ts || '');
  return Number.isFinite(persistedTime)
    && Date.now() - persistedTime < RECENT_CONVERSATION_PAUSE_MS;
}

function hasOpenWork(status) {
  return Array.isArray(status?.open_commitments) && status.open_commitments.length > 0;
}

function hasRunnableWork(status, now = Date.now()) {
  return Array.isArray(status?.open_commitments) && status.open_commitments.some((item) => {
    const wake = Date.parse(item?.next_wake_ts || '');
    return !Number.isFinite(wake) || wake <= now;
  });
}

function mayWorkAtNight(status, pendingSignals = [], full = config.autonomyFull) {
  return full || hasRunnableWork(status) || hasNightUrgentSignal(pendingSignals);
}

function nextDelay(result, status = agentStatus()) {
  if (hasRunnableWork(status) && result?.progress?.made_progress) return PROGRESS_WORK_CADENCE_MS;
  if (hasRunnableWork(status)) return ACTIVE_WORK_CADENCE_MS;
  if (result?.contribution) return BASE_CADENCE_MS;
  if (config.autonomyFull) return BASE_CADENCE_MS;
  return Math.max(BASE_CADENCE_MS, 2 * 60 * 60_000);
}

function schedulerDelay(nextRunTs, now = Date.now()) {
  const due = Date.parse(nextRunTs || '');
  if (!Number.isFinite(due)) return CHECK_MS;
  return Math.max(1_000, Math.min(CHECK_MS, due - now));
}

function armTimer() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    tick()
      .catch((error) => {
        logTimeline('system', `Zelfstandige planner liep vast: ${error?.message || error}`, {
          internalOnly: true,
          autonomy: true,
        });
      })
      .finally(armTimer);
  }, schedulerDelay(readState().next_run_ts));
}

function signalText(signal) {
  if (signal.text) return signal.text;
  const value = signal.attributes?.current_temperature
    ?? signal.attributes?.temperature
    ?? signal.newState;
  const previous = signal.attributes?.previous_current_temperature
    ?? signal.attributes?.previous_temperature
    ?? signal.oldState;
  return `${signal.friendlyName || signal.entityId || signal.type}: ${previous ?? '?'} -> ${value ?? '?'}`;
}

function enqueueSignal(event, { wake = false } = {}) {
  if (!event || typeof event !== 'object') return;
  const state = readState();
  const now = Date.now();
  const signal = {
    id: `${now}-${Math.random().toString(36).slice(2, 7)}`,
    ts: new Date(now).toISOString(),
    key: String(event.entityId || `${event.type}:${event.person || event.text || ''}`).slice(0, 240),
    type: String(event.type || 'signal').slice(0, 60),
    text: signalText(event).slice(0, 500),
  };
  const pending = (Array.isArray(state.pending_signals) ? state.pending_signals : [])
    .filter((item) => now - Date.parse(item.ts || '') < SIGNAL_MAX_AGE_MS)
    .filter((item) => item.key !== signal.key);
  state.pending_signals = [...pending, signal].slice(-30);
  if (wake) {
    const soon = now + 60_000;
    const scheduled = Date.parse(state.next_run_ts || '') || Infinity;
    if (scheduled > soon) state.next_run_ts = new Date(soon).toISOString();
  }
  writeState(state);
  if (wake && timer) armTimer();
}

export function shouldQueueWorldSignal(event) {
  if (/(?:cpu|gpu|processor|cloud[_ -]?key|server|nas|disk|nvme)/i.test(
    `${event?.entityId || ''} ${event?.friendlyName || ''}`,
  )) return false;
  const deviceClass = String(event?.attributes?.deviceClass || '');
  const temperatureSignal = event?.domain === 'climate'
    || (event?.domain === 'sensor' && (
      deviceClass === 'temperature'
      || /(?:°c|celsius)/i.test(String(event?.attributes?.unit_of_measurement || ''))
    ));
  if (temperatureSignal) {
    const current = Number(
      event?.attributes?.current_temperature
        ?? event?.attributes?.temperature
        ?? event?.newState,
    );
    const previous = Number(
      event?.attributes?.previous_current_temperature
        ?? event?.attributes?.previous_temperature
        ?? event?.oldState,
    );
    return !Number.isFinite(current) || !Number.isFinite(previous)
      || Math.abs(current - previous) >= TEMPERATURE_SIGNAL_DELTA;
  }
  return event?.domain === 'binary_sensor'
    && ['door', 'window', 'garage_door', 'opening', 'occupancy'].includes(deviceClass)
    && String(event?.newState || '').toLowerCase() === 'on';
}

export function shouldWakeForWorldSignal(event) {
  const deviceClass = String(event?.attributes?.deviceClass || '');
  return event?.domain === 'binary_sensor'
    && ['door', 'window', 'garage_door', 'opening', 'occupancy'].includes(deviceClass)
    && String(event?.newState || '').toLowerCase() === 'on';
}

export function hasNightUrgentSignal(signals = []) {
  return (Array.isArray(signals) ? signals : []).some((signal) => (
    /^(?:arrival|departure|door|doorbell)$/i.test(String(signal?.type || ''))
    || (/^binary_sensor\./i.test(String(signal?.key || ''))
      && /\boff\s*->\s*on\b/i.test(String(signal?.text || '')))
  ));
}

function startSignalInbox() {
  if (listenersStarted) return;
  listenersStarted = true;
  bus.on('house', (event) => {
    // Dezelfde deurtransition komt ook als rijker world_signal met entity-id.
    if (['person_detected', 'doorbell', 'door'].includes(event?.type)) return;
    enqueueSignal(event, {
      wake: ['arrival', 'departure', 'door'].includes(event?.type),
    });
  });
  bus.on('world_signal', (event) => {
    if (shouldQueueWorldSignal(event)) {
      enqueueSignal(event, { wake: shouldWakeForWorldSignal(event) });
    }
  });
  bus.on('work_changed', (event) => {
    if (event?.status !== 'open') return;
    const state = readState();
    const soon = Date.now() + 60_000;
    if ((Date.parse(state.next_run_ts || '') || Infinity) > soon) {
      state.next_run_ts = new Date(soon).toISOString();
      writeState(state);
      if (timer) armTimer();
    }
  });
}

function mayStartFreeAttention(pendingSignals = [], portfolioChoice = null) {
  return (!Array.isArray(pendingSignals) || pendingSignals.length === 0) && !portfolioChoice;
}

async function runOnce(reason = 'periodieke vrije aandacht') {
  if (running) return null;
  const status = agentStatus();
  if (status.active_turns || recentInteractiveTurn(status)) return null;
  const state = withCurrentDailyBudget(readState());
  const pendingSignals = (Array.isArray(state.pending_signals) ? state.pending_signals : [])
    .filter((signal) => Date.now() - Date.parse(signal.ts || '') < SIGNAL_MAX_AGE_MS);
  // Twee uur is de dagelijkse vrije ruimte. Lopend werk en echte signalen mogen
  // daar overheen gaan, zodat het budget nooit een half afgemaakt doel veroorzaakt.
  if (remainingDailyMs(state) <= 0 && !hasRunnableWork(status) && pendingSignals.length === 0) {
    state.next_run_ts = nextDayResetTs();
    state.last_result = {
      productive: false,
      daily_allocation_complete: true,
      summary: 'Dagelijkse vrije werktijd gebruikt; hervat bij nieuw werk, een signaal of de volgende dag.',
    };
    writeState(state);
    return { budgetExhausted: true };
  }
  if (isNight(new Date(), config.sleepStart, config.wakeTime)
    && !mayWorkAtNight(status, pendingSignals)) return null;

  const leaseOwner = `eva:${process.pid}:${Date.now()}`;
  // De portefeuille kiest betekenisvol tussen uitvoerbare commitments. Een
  // nieuwe aandachtsrichting mag een al gemaakte eigen keuze niet telkens
  // verdringen; vrije nieuwsgierigheid komt terug zodra runnable werk klaar is
  // of bewust op een wake/externe afhankelijkheid wacht.
  const portfolioChoice = selectPortfolioCommitment(durableWorkStatus().open);
  const attention = mayStartFreeAttention(pendingSignals, portfolioChoice)
    ? dueGoalAttention() || dueAttention()
    : null;
  const selected = attention ? null : portfolioChoice;
  const openWorkId = Number(selected?.item?.id);
  const leasedWork = Number.isFinite(openWorkId)
    ? claimWorkItem(openWorkId, { owner: leaseOwner, leaseMs: (config.autonomyTurnMinutes + 5) * 60_000 })
    : null;
  if (Number.isFinite(openWorkId) && !leasedWork) return null;

  running = true;
  const runStartedAt = Date.now();
  const workReason = leasedWork
    ? `${reason}. Hervat duurzame episode ${selected?.episode?.id || '-'} en werk #${leasedWork.id} op revisie ${leasedWork.revision}: ${leasedWork.current_step || leasedWork.next_step || leasedWork.goal}. Selectiereden: ${JSON.stringify(selected?.rationale || {})}`
    : reason;
  const groundedReason = pendingSignals.length
    ? `${workReason}. Sinds je vorige aandacht kwamen deze echte signalen binnen:\n${pendingSignals.map((signal) => `- [${signal.id}] bron ${signal.key}: ${signal.text}`).join('\n')}`
    : attention
      ? `${workReason}. Er is een eigen aandachtsrichting beschikbaar (${attention.kind} #${attention.id}): ${attention.text}. Dit is een kandidaat, geen opdracht: kies zelf of je haar nu volgt of bewust iets anders belangrijker vindt.`
      : workReason;
  let runError = null;
  try {
    if (selected?.episode?.id) updateGoalEpisode(selected.episode.id, {
      state: 'executing',
      nextAction: leasedWork?.current_step || leasedWork?.next_step || leasedWork?.goal || '',
      sessionRef: leaseOwner,
    });
    const result = await runAutonomousTurn({
      reason: groundedReason,
      pendingSignals,
      attention,
      workId: leasedWork?.id || null,
      goalEpisodeId: selected?.episode?.id || null,
    });
    const productive = !!result?.contribution;
    const progressed = result?.progress?.made_progress === true;
    const yielded = result?.yielded === true || result?.superseded === true;
    const acknowledgedIds = new Set((result?.tools || [])
      .filter((tool) => tool.name === 'signalen_afhandelen' && tool.ok && tool.verified)
      .flatMap((tool) => Array.isArray(tool.args?.ids) ? tool.args.ids : [])
      .map((id) => String(id)));
    const signalsHandled = pendingSignals.length === 0
      || pendingSignals.every((signal) => acknowledgedIds.has(signal.id));
    const latest = withCurrentDailyBudget(readState());
    const elapsedMs = Math.max(0, Date.now() - runStartedAt);
    latest.daily_used_ms += elapsedMs;
    if (productive || progressed) latest.daily_productive_ms += elapsedMs;
    if (yielded) latest.daily_preempted_ms += elapsedMs;
    if (acknowledgedIds.size) {
      latest.pending_signals = (Array.isArray(latest.pending_signals) ? latest.pending_signals : [])
        .filter((signal) => !acknowledgedIds.has(signal.id));
    }
    latest.last_run_ts = new Date().toISOString();
    latest.next_run_ts = remainingDailyMs(latest) <= 0
      && !hasRunnableWork(agentStatus())
      && latest.pending_signals.length === 0
      ? nextDayResetTs()
      : new Date(Date.now() + (yielded ? RECENT_CONVERSATION_PAUSE_MS : nextDelay(result))).toISOString();
    latest.last_result = {
      turn_id: result?.turnId || null,
      productive,
      progressed,
      yielded,
      tool: result?.contribution?.tool || null,
      signals_handled: signalsHandled,
      signals_acknowledged: acknowledgedIds.size,
      summary: String(
        result?.contribution?.evidence?.summary
          || result?.progress?.summary
          || result?.noActionReason
          || result?.reply
          || result?.error
          || '',
      ).slice(0, 600),
    };
    if (productive) latest.productive_runs += 1;
    if (progressed) {
      latest.progress_runs += 1;
      latest.last_progress_ts = latest.last_run_ts;
    }
    if (!productive && !progressed && !yielded) latest.quiet_runs += 1;
    writeState(latest);
    return result;
  } catch (error) {
    runError = error;
    const latest = withCurrentDailyBudget(readState());
    latest.daily_used_ms += Math.max(0, Date.now() - runStartedAt);
    latest.last_run_ts = new Date().toISOString();
    latest.next_run_ts = new Date(Date.now() + BASE_CADENCE_MS).toISOString();
    latest.last_result = {
      productive: false,
      error: String(error?.message || error).slice(0, 300),
    };
    writeState(latest);
    logTimeline('system', `Zelfstandige beurt liep vast: ${error?.message || error}`, {
      internalOnly: true,
      autonomy: true,
    });
    return null;
  } finally {
    if (leasedWork) releaseWorkLease(leasedWork.id, leaseOwner, {
      error: runError ? String(runError?.message || runError) : null,
    });
    running = false;
  }
}

async function tick() {
  const state = withCurrentDailyBudget(readState());
  const due = Date.parse(state.next_run_ts || '') || 0;
  if (Date.now() < due) return;
  const result = await runOnce();
  if (!result) {
    // Een lopend gesprek krijgt de ene agentlus, maar full hervat kort daarna
    // zelfstandig in plaats van tien minuten passief te blijven.
    // Lees opnieuw: een tegelijk binnengekomen signaal mag hier niet verloren gaan.
    const latest = withCurrentDailyBudget(readState());
    latest.next_run_ts = new Date(Date.now() + (config.autonomyFull ? 2 : 10) * 60_000).toISOString();
    writeState(latest);
  }
}

export function startAutonomy() {
  if (timer) return;
  recoverWorkLeases();
  if (!kvGet(KEY, null)) writeState(initialState());
  if (config.autonomyFull || hasOpenWork(agentStatus())) {
    const state = readState();
    const soon = Date.now() + 60_000;
    if ((Date.parse(state.next_run_ts || '') || Infinity) > soon) {
      state.next_run_ts = new Date(soon).toISOString();
      writeState(state);
    }
  }
  startSignalInbox();
  armTimer();
}

export function autonomyStatus() {
  const state = withCurrentDailyBudget(readState());
  return {
    running,
    level: config.autonomyLevel,
    cadence_minutes: Math.round(BASE_CADENCE_MS / 60_000),
    active_work_cadence_minutes: Math.round(ACTIVE_WORK_CADENCE_MS / 60_000),
    progress_work_cadence_minutes: Number((PROGRESS_WORK_CADENCE_MS / 60_000).toFixed(2)),
    daily_allocation_minutes: config.autonomyDailyWorkMinutes,
    max_turn_minutes: config.autonomyTurnMinutes,
    daily_used_minutes: Math.round(Number(state.daily_used_ms || 0) / 60_000),
    daily_productive_minutes: Math.round(Number(state.daily_productive_ms || 0) / 60_000),
    daily_remaining_minutes: Math.ceil(remainingDailyMs(state) / 60_000),
    ...state,
  };
}

export const runAutonomyNow = runOnce;
export const __autonomyScheduleTest = {
  hasOpenWork,
  hasRunnableWork,
  mayWorkAtNight,
  nextDelay,
  schedulerDelay,
  localDayKey,
  nextDayResetTs,
  remainingDailyMs,
  withCurrentDailyBudget,
  mayStartFreeAttention,
};
