import { config } from '../config.js';
import { bus } from '../bus.js';
import { db, kvGet, kvSet, logTimeline, recentMessages } from '../db.js';
import { isNight } from '../util.js';
import { agentStatus, runAutonomousTurn } from './agent.js';
import {
  initializePerceptionIncidentRuntime,
  perceptionIncidentPending,
  runPerceptionIncidentOnce,
} from './perceptionexecutor.js';
import { claimWorkItem, durableWorkStatus, recoverWorkLeases, releaseWorkLease } from './work.js';
import {
  dueAttention,
  nextDeferredExplorationRetryTs,
  settleIssuedAspirationAttention,
  settleIssuedExplorationAttention,
} from './curiosity.js';
import {
  acceptedMonitoringDeliveryReceiptCandidate,
  dueGoalAttention,
  selectPortfolioCommitment,
  updateGoalEpisode,
  verifyMonitoringLifecycleAuthority,
} from './portfolio.js';
import { registerRuntimePrincipalValidator } from './runtimecontroller.js';
import {
  acknowledgedSignalIds,
  createSignalBatch,
  partitionSignalSnapshots,
  quarantineUnsupportedSignalSnapshots,
  removeAcknowledgedSignalSnapshots,
  selectRoutableSignalBatchSnapshots,
} from './signals.js';

const KEY = 'eva_autonomy_scheduler_v1';
const CHECK_MS = 5 * 60_000;
const BASE_CADENCE_MS = Math.max(
  (config.autonomyFull ? 5 : 30) * 60_000,
  config.autonomyIntervalMinutes * 60_000,
);
const ACTIVE_WORK_CADENCE_MS = Math.max(
  2 * 60_000,
  config.autonomyActiveWorkIntervalMinutes * 60_000,
);
const PROGRESS_WORK_CADENCE_MS = Math.max(
  15_000,
  config.autonomyProgressWorkIntervalMinutes * 60_000,
);
const RECENT_CONVERSATION_PAUSE_MS = Math.max(
  60_000,
  config.autonomyConversationPauseMinutes * 60_000,
);
const SIGNAL_MAX_AGE_MS = 24 * 3600_000;
const USER_WORK_SOURCES = new Set(['user', 'telegram', 'voice', 'web', 'chat']);
const OBSERVABLE_ATTENTION_KINDS = new Set(['aspiration', 'exploration', 'goal']);
const TEMPERATURE_SIGNAL_DELTA = Math.max(
  0.5,
  config.autonomyTemperatureDelta,
);
const PERCEPTION_RETRY_MS = 15_000;
const TECHNICAL_AUTONOMY_RETRY_MS = Math.max(
  15_000, Math.min(60_000, BASE_CADENCE_MS),
);
const AUTONOMY_SELECTION_DISPLAY_MAX_AGE_MS = Math.max(
  60_000, config.autonomyTurnMinutes * 60_000,
);
const AUTONOMY_RESULT_DISPLAY_MAX_AGE_MS = 24 * 60 * 60_000;
const AUTONOMY_DISPLAY_LABEL = /^[\p{L}\p{M}\p{N}][\p{L}\p{M}\p{N}\s.,;:!?'"()°%/+\-\u2013\u2014]+$/u;
const AUTONOMY_INTERNAL_REFERENCE = /(?:^|\s)(?:aspiration|exploration|goal|work|worker|episode|turn|signal|request|receipt|tool)(?::|#)[a-z0-9._:-]+(?:$|\s|[.,;!?])/iu;
const AUTONOMY_UUID = /(?:^|[^a-f0-9])[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}(?:$|[^a-f0-9])/iu;
const AUTONOMY_HASH = /(?:^|[^a-f0-9])(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64})(?:$|[^a-f0-9])/iu;
let timer = null;
let schedulerStarted = false;
let running = false;
let currentSelection = null;
let listenersStarted = false;
let perceptionWakePending = false;
const runtimePrincipals = new WeakMap();
const selectionDisplayBindings = new WeakMap();
registerRuntimePrincipalValidator(
  'autonomy',
  (context) => runtimePrincipals.get(context?.messageContext) || null,
);

function schedulerTurnContext({
  reason, pendingSignals, signalBatch: batch, attention, workId, goalEpisodeId,
  interactionEpoch,
}) {
  if (typeof interactionEpoch !== 'number' || !Number.isSafeInteger(interactionEpoch)
    || interactionEpoch < 0) throw new TypeError('invalid_autonomy_interaction_epoch');
  const context = Object.freeze({
    reason,
    interactionEpoch,
    pendingSignals: Object.freeze((Array.isArray(pendingSignals) ? pendingSignals : []).slice(0, 30)),
    ...(batch ? { signalBatch: batch } : {}),
    // De exploration-snapshot hangt uitsluitend via een private WeakMap aan
    // het exact uitgegeven, al bevroren attention-object. Behoud die identiteit
    // tot de receipt-ingress; publieke status projecteert hieronder alleen id/kind.
    attention: attention?.kind === 'exploration'
      ? attention
      : attention && typeof attention === 'object'
        ? Object.freeze({ ...attention }) : attention,
    workId: workId || null,
    goalEpisodeId: goalEpisodeId || null,
  });
  runtimePrincipals.set(context, 'system:eva-autonomy');
  return context;
}

function observableSchedulerSelection(context) {
  if (!context || runtimePrincipals.get(context) !== 'system:eva-autonomy') return null;
  const attentionId = Number(context?.attention?.id);
  const workId = Number(context?.workId);
  const goalEpisodeId = Number(context?.goalEpisodeId);
  const attentionKind = String(context?.attention?.kind || '');
  const signalCount = Array.isArray(context?.signalBatch?.signals)
    ? context.signalBatch.signals.length : 0;
  const attention = OBSERVABLE_ATTENTION_KINDS.has(attentionKind)
    && Number.isSafeInteger(attentionId)
    && attentionId > 0
    ? Object.freeze({
      kind: attentionKind,
      id: attentionId,
    })
    : null;
  return Object.freeze({
    attention,
    work_id: Number.isSafeInteger(workId) && workId > 0 ? workId : null,
    goal_episode_id: Number.isSafeInteger(goalEpisodeId) && goalEpisodeId > 0
      ? goalEpisodeId : null,
    signal_batch_id: signalCount > 0
      ? String(context.signalBatch.batchId || '').slice(0, 80) || null
      : null,
    signal_count: signalCount,
  });
}

function boundedAutonomyDisplayLabel(value, limit = 360) {
  if (typeof value !== 'string') return null;
  const text = value.normalize('NFKC').replace(/\s+/gu, ' ').trim();
  if (text.length < 12 || text.length > limit || !AUTONOMY_DISPLAY_LABEL.test(text)
    || /^\d+$/u.test(text) || /^worker$/iu.test(text)
    || AUTONOMY_INTERNAL_REFERENCE.test(text) || AUTONOMY_UUID.test(text)
    || AUTONOMY_HASH.test(text)) return null;
  const words = text.match(/\p{L}{2,}/gu) || [];
  return words.length >= 3 ? text : null;
}

function exactDisplayLabels(choice, reason) {
  const choiceLabel = boundedAutonomyDisplayLabel(choice);
  const reasonLabel = boundedAutonomyDisplayLabel(reason);
  return choiceLabel && reasonLabel ? Object.freeze({
    choice_label: choiceLabel,
    reason_label: reasonLabel,
  }) : null;
}

function attentionSelectionLabels(attention) {
  if (!attention || !Object.isFrozen(attention)
    || !OBSERVABLE_ATTENTION_KINDS.has(attention.kind)
    || !Number.isSafeInteger(Number(attention.id)) || Number(attention.id) <= 0) return null;
  if (attention.kind === 'aspiration') {
    const row = /** @type {any} */ (db.prepare(
      'SELECT text,why,status FROM aspirations WHERE id=?',
    ).get(Number(attention.id)));
    return row?.status === 'actief' && row.text === attention.text
      ? exactDisplayLabels(row.text, row.why) : null;
  }
  if (attention.kind === 'exploration') {
    const row = /** @type {any} */ (db.prepare(
      `SELECT question,why_now,status,source FROM eva_explorations WHERE id=?`,
    ).get(Number(attention.id)));
    return row?.source === 'self'
      && ['chosen', 'running', 'waiting', 'evaluating'].includes(row.status)
      && row.question === attention.text
      ? exactDisplayLabels(row.question, row.why_now) : null;
  }
  const row = /** @type {any} */ (db.prepare(
    `SELECT goal_key,title,intent,why,status,source FROM eva_goals WHERE id=?`,
  ).get(Number(attention.id)));
  return row?.source === 'self' && row.status === 'active'
    && row.goal_key === attention.key
    ? exactDisplayLabels(row.title, row.why || row.intent) : null;
}

function workSelectionLabels(work, rationale) {
  if (!work || typeof work !== 'object' || !rationale || typeof rationale !== 'object') return null;
  const choice = [work.current_step, work.next_step, work.title]
    .map((value) => boundedAutonomyDisplayLabel(value)).find(Boolean);
  let reason = null;
  if (rationale.userCommitment === true) {
    reason = 'Deze openstaande afspraak voor het gezin was nu uitvoerbaar.';
  } else if (rationale.recovery === true) {
    reason = 'Eerder vastgelopen werk was nu weer veilig uitvoerbaar.';
  } else if (rationale.deliveryRequired === true) {
    reason = 'De uitkomst moet na afronding met het gezin gedeeld worden.';
  } else {
    reason = boundedAutonomyDisplayLabel(work.goal)
      || boundedAutonomyDisplayLabel(work.definition_of_done);
  }
  return choice && reason ? exactDisplayLabels(choice, reason) : null;
}

function signalSelectionLabels(signals) {
  if (!Array.isArray(signals) || signals.length === 0) return null;
  const choice = signals.map((signal) => boundedAutonomyDisplayLabel(signal?.text)).find(Boolean);
  return choice ? exactDisplayLabels(
    choice, 'Een nieuwe gebeurtenis in huis vroeg nu om aandacht.',
  ) : null;
}

function schedulerSelectionLabels({ attention = null, work = null, rationale = null,
  signals = [] } = {}) {
  if (work) return workSelectionLabels(work, rationale);
  if (Array.isArray(signals) && signals.length) return signalSelectionLabels(signals);
  return attentionSelectionLabels(attention);
}

function bindSelectionDisplay(selection, labels, selectedAt = Date.now()) {
  if (!selection || !Object.isFrozen(selection) || !labels) return null;
  const at = Number(selectedAt);
  if (!Number.isFinite(at)) return null;
  const binding = Object.freeze({
    choice_label: labels.choice_label,
    reason_label: labels.reason_label,
    selection_ts: new Date(at).toISOString(),
  });
  selectionDisplayBindings.set(selection, binding);
  return binding;
}

function currentSelectionDisplay(selection, isRunning, now = Date.now()) {
  if (!isRunning || !selection || !Object.isFrozen(selection)) return null;
  const binding = selectionDisplayBindings.get(selection);
  const selectedAt = Date.parse(binding?.selection_ts || '');
  const age = Number(now) - selectedAt;
  if (!binding || !Number.isFinite(age) || age < 0
    || age > AUTONOMY_SELECTION_DISPLAY_MAX_AGE_MS) return null;
  return exactDisplayLabels(binding.choice_label, binding.reason_label)
    ? binding : null;
}

function timelineMeta(value) {
  try {
    const parsed = JSON.parse(String(value || ''));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch { return null; }
}

function recentVisibleResultDisplay(state, now = Date.now()) {
  const lastResult = state?.last_result;
  const lastRunAt = Date.parse(state?.last_run_ts || '');
  const age = Number(now) - lastRunAt;
  if (!lastResult || lastResult.productive !== true || !Number.isFinite(age)
    || age < -5_000 || age > AUTONOMY_RESULT_DISPLAY_MAX_AGE_MS) return null;
  const resultLabel = boundedAutonomyDisplayLabel(lastResult.summary, 500);
  if (!resultLabel) return null;
  const runWindowMs = AUTONOMY_SELECTION_DISPLAY_MAX_AGE_MS + 60_000;
  const row = /** @type {any} */ (db.prepare(`SELECT ts,content,meta,outcome_key FROM timeline
    WHERE kind='contribution' AND ts>=? AND ts<=? AND json_valid(meta)
      AND json_type(meta,'$.evidence')='text' AND json_extract(meta,'$.evidence')=?
    ORDER BY id DESC LIMIT 1`).get(
    new Date(lastRunAt - runWindowMs).toISOString(),
    new Date(lastRunAt + 5_000).toISOString(),
    resultLabel,
  ));
  const meta = timelineMeta(row?.meta);
  if (!row || meta?.internalOnly !== false || meta?.familyVisible !== true
    || meta.evidence !== resultLabel || row.content !== `Eva ontdekte: ${resultLabel}`
    || meta.familyResult?.contract !== 'ExplorationFamilyResult/v1'
    || meta.familyResult?.sourceOutcomeKey !== row.outcome_key) return null;
  const resultAt = Date.parse(row.ts || '');
  if (!Number.isFinite(resultAt) || Number(now) - resultAt < -5_000
    || Number(now) - resultAt > AUTONOMY_RESULT_DISPLAY_MAX_AGE_MS) return null;
  return Object.freeze({ result_label: resultLabel, result_ts: row.ts });
}

function autonomyDisplayProjection(state, now = Date.now()) {
  const selection = currentSelectionDisplay(currentSelection, running, now);
  const result = recentVisibleResultDisplay(state, now);
  return Object.freeze({
    choice_label: selection?.choice_label || null,
    reason_label: selection?.reason_label || null,
    selection_ts: selection?.selection_ts || null,
    selection_expires_ts: selection
      ? new Date(Date.parse(selection.selection_ts)
        + AUTONOMY_SELECTION_DISPLAY_MAX_AGE_MS).toISOString() : null,
    result_label: result?.result_label || null,
    result_ts: result?.result_ts || null,
    result_expires_ts: result
      ? new Date(Date.parse(result.result_ts)
        + AUTONOMY_RESULT_DISPLAY_MAX_AGE_MS).toISOString() : null,
  });
}

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
    quarantined_signals: [],
    last_signal_quarantine_receipt: null,
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

function quarantinePendingSignalSnapshots(candidates, quarantinedAt = new Date().toISOString()) {
  if (!Array.isArray(candidates) || candidates.length === 0) {
    return { state: readState(), receipt: null, newFingerprints: 0 };
  }
  const result = db.transaction(() => {
    const state = readState();
    const moved = quarantineUnsupportedSignalSnapshots({
      pendingSignals: state.pending_signals,
      candidates,
      quarantinedSignals: state.quarantined_signals,
      quarantinedAt,
    });
    if (!moved.receipt) return { state, receipt: null, newFingerprints: 0 };
    state.pending_signals = moved.pendingSignals;
    state.quarantined_signals = moved.quarantinedSignals;
    state.last_signal_quarantine_receipt = moved.receipt;
    writeState(state);
    return { state, receipt: moved.receipt, newFingerprints: moved.newFingerprints };
  }).immediate();
  if (result.receipt && result.newFingerprints > 0) {
    logTimeline('system', `${result.receipt.moved.length} onondersteunde signaalsnapshot(s) auditbaar in quarantaine gezet.`, {
      internalOnly: true,
      autonomy: true,
      signalQuarantine: result.receipt,
    });
  }
  return result;
}

function activeSignalSnapshots(state, now = Date.now()) {
  return (Array.isArray(state?.pending_signals) ? state.pending_signals : [])
    .filter((signal) => now - Date.parse(signal.ts || '') < SIGNAL_MAX_AGE_MS);
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

function frozenDelivery(item) {
  try {
    return ['delivery_uncertain', 'delivery_rejected']
      .includes(JSON.parse(item?.wait_json || '{}')?.reason);
  } catch {
    return false;
  }
}

function hasRunnableWork(status, now = Date.now()) {
  return Array.isArray(status?.open_commitments) && status.open_commitments.some((item) => {
    if (frozenDelivery(item)) return false;
    const wake = Date.parse(item?.next_wake_ts || '');
    return !Number.isFinite(wake) || wake <= now;
  });
}

function earliestFutureWorkWake(status, now = Date.now()) {
  const wakes = (Array.isArray(status?.open_commitments) ? status.open_commitments : [])
    .map((item) => Date.parse(item?.next_wake_ts || ''))
    .filter((wake) => Number.isFinite(wake) && wake > now);
  return wakes.length ? Math.min(...wakes) : null;
}

function boundedSchedulerTs(proposedTs, status, now = Date.now()) {
  const proposed = Date.parse(proposedTs || '');
  const wake = earliestFutureWorkWake(status, now);
  const deadline = Number.isFinite(proposed) ? proposed : now + CHECK_MS;
  return new Date(wake == null ? deadline : Math.min(deadline, wake)).toISOString();
}

function urgentDueMonitoringDelivery(now = Date.now()) {
  const open = /** @type {any[]} */ (durableWorkStatus(new Date(now)).open);
  const choices = open.flatMap((item) => {
    const wakeMs = Date.parse(item?.next_wake_ts || '');
    if (!Number.isFinite(wakeMs) || wakeMs > now) return [];
    let pendingSha256 = '';
    try {
      pendingSha256 = String(JSON.parse(String(item.state_json || 'null'))
        ?.pending_delivery?.pendingSha256 || '');
    } catch { return []; }
    if (!pendingSha256) return [];
    const choice = selectPortfolioCommitment([item], new Date(now));
    if (!choice?.episode || choice.episode.state !== 'delivery_pending') return [];
    const accepted = acceptedMonitoringDeliveryReceiptCandidate({
      workId: Number(item.id), episodeId: Number(choice.episode.id),
    });
    if (accepted) return [{
      ...choice, monitoringHorizonMs: Number.NEGATIVE_INFINITY, wakeMs,
    }];
    const authority = verifyMonitoringLifecycleAuthority({
      workId: Number(item.id), episodeId: Number(choice.episode.id),
      expectedPendingSha256: pendingSha256,
    });
    if (!authority?.pending) return [];
    const endsMs = authority.monitoring.endsAt == null
      ? Number.POSITIVE_INFINITY : Date.parse(authority.monitoring.endsAt);
    const horizonMs = Math.min(Date.parse(authority.pending.expiresAt), endsMs);
    return Number.isFinite(horizonMs) ? [{ ...choice, monitoringHorizonMs: horizonMs, wakeMs }] : [];
  });
  choices.sort((left, right) => left.monitoringHorizonMs - right.monitoringHorizonMs
    || left.wakeMs - right.wakeMs || Number(left.item.id) - Number(right.item.id));
  return choices[0] || null;
}

function upcomingUserCommitment(status, now = Date.now(), windowMs = config.autonomyTurnMinutes * 60_000) {
  const candidates = (Array.isArray(status?.open_commitments) ? status.open_commitments : [])
    .filter((item) => item?.owner || USER_WORK_SOURCES.has(String(item?.source || '').toLowerCase()))
    .map((item) => ({ item, wake: Date.parse(item?.next_wake_ts || '') }))
    .filter(({ wake }) => Number.isFinite(wake) && wake > now && wake - now <= windowMs)
    .sort((left, right) => left.wake - right.wake);
  return candidates[0] || null;
}

function shouldReserveAttentionForUserWork(status, pendingSignals = [], portfolioChoice = null, now = Date.now()) {
  if (Array.isArray(pendingSignals) && pendingSignals.length) return null;
  const upcoming = upcomingUserCommitment(status, now);
  if (!upcoming) return null;
  // Een al gekozen, andere (vaak zelfgekozen) episode mag de resterende
  // aandachtstijd vóór een harde bewonerswake niet opsouperen. Alleen wanneer
  // de portefeuille exact dat bewonerswerk koos, hoeft de scheduler niet te
  // wachten: dan is de juiste commitment al aan de beurt.
  return Number(portfolioChoice?.item?.id) === Number(upcoming.item?.id)
    ? null
    : upcoming;
}

function mayWorkAtNight(status, pendingSignals = [], full = config.autonomyFull) {
  return full || hasRunnableWork(status) || hasNightUrgentSignal(pendingSignals);
}

function nextDelay(result, status = agentStatus()) {
  if (hasRunnableWork(status) && result?.executionPending === true) {
    return PROGRESS_WORK_CADENCE_MS;
  }
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
  if (!config.autonomyEnabled || !schedulerStarted) return false;
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
  return true;
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
  bus.on('perception_ready', () => {
    // Het busbericht is uitsluitend een wake-hint. Alleen een reeds duurzaam
    // vastgelegd private incident mag de scheduler versnellen.
    if (!perceptionIncidentPending()) return;
    perceptionWakePending = true;
    const state = readState();
    const soon = Date.now() + 1_000;
    if ((Date.parse(state.next_run_ts || '') || Infinity) > soon) {
      state.next_run_ts = new Date(soon).toISOString();
      writeState(state);
      if (timer) armTimer();
    }
  });
  bus.on('work_changed', (event) => {
    if (event?.status !== 'open') return;
    const state = readState();
    const soon = Date.now() + (urgentDueMonitoringDelivery() ? 15_000 : 60_000);
    const openStatus = { open_commitments: durableWorkStatus().open };
    const nextRunTs = boundedSchedulerTs(new Date(soon).toISOString(), openStatus);
    if ((Date.parse(state.next_run_ts || '') || Infinity) > Date.parse(nextRunTs)) {
      state.next_run_ts = nextRunTs;
      writeState(state);
      if (timer) armTimer();
    }
  });
}

function mayStartFreeAttention(pendingSignals = [], portfolioChoice = null) {
  return (!Array.isArray(pendingSignals) || pendingSignals.length === 0) && !portfolioChoice;
}

function technicalAutonomyFailure(result) {
  if (!result || typeof result !== 'object') return true;
  return Boolean(
    String(result.error || '').trim()
    || result.interpretationFailed === true
    || String(result.failureStage || '').trim()
  );
}

function shouldSettleAttention(result) {
  return Boolean(result && typeof result === 'object'
    && !technicalAutonomyFailure(result)
    && result.yielded !== true
    && result.superseded !== true);
}

function shouldBackoffExploration(result) {
  return shouldSettleAttention(result)
    && !result.contribution
    && result.progress?.made_progress !== true;
}

async function runOnce(reason = 'periodieke vrije aandacht') {
  if (!config.autonomyEnabled || !schedulerStarted) return false;
  if (running) return null;
  const status = agentStatus();
  const urgentMonitoringDelivery = urgentDueMonitoringDelivery();
  if (status.active_turns || status.interactive_pending) return null;
  // Een verse deurbel is een eigen, smalle waarnemingsbeurt en hoeft niet op
  // de gewone gesprekspauze te wachten. Zij mengt nooit met werk/signalen.
  const privateWakeAttempt = perceptionWakePending || perceptionIncidentPending();
  const perceptionEpoch = status.interaction_epoch;
  const perception = privateWakeAttempt
    ? await runPerceptionIncidentOnce({
      isCurrent: () => {
        const current = agentStatus();
        return current.interaction_epoch === perceptionEpoch
          && !current.active_turns && !current.interactive_pending;
      },
    }) : null;
  if (perception) {
    perceptionWakePending = perceptionIncidentPending();
    const latest = withCurrentDailyBudget(readState());
    latest.last_run_ts = new Date().toISOString();
    latest.next_run_ts = new Date(Date.now()
      + (perceptionWakePending ? 1_000 : PERCEPTION_RETRY_MS)).toISOString();
    latest.last_result = { productive: perception.status === 'delivered',
      perception: true, summary: `Deurbelincident: ${perception.status}` };
    writeState(latest);
    return { perception: true, ...perception };
  }
  perceptionWakePending = perceptionIncidentPending();
  if (privateWakeAttempt && !perceptionWakePending) {
    const latest = withCurrentDailyBudget(readState());
    latest.last_run_ts = new Date().toISOString();
    latest.next_run_ts = boundedSchedulerTs(
      new Date(Date.now() + nextDelay(null, status)).toISOString(),
      status,
    );
    latest.last_result = {
      productive: false,
      perception: true,
      summary: 'De private deurbel-wake had geen actueel duurzaam incident.',
    };
    writeState(latest);
    return { perception: true, ok: false, status: 'stale_wake' };
  }
  // De smalle helper kan een claim fail-closed afsluiten en dan null geven.
  // Als de private ledger nog aandacht bevat, voorkom dat deze tick daarna ook
  // een gewone vrije beurt start; één schedulerbeurt heeft één eigenaar.
  if (perceptionIncidentPending()) return null;
  if (recentInteractiveTurn(status) && !urgentMonitoringDelivery) return null;
  let state = withCurrentDailyBudget(readState());
  const observed = partitionSignalSnapshots(activeSignalSnapshots(state));
  if (observed.unsupported.length) {
    const quarantined = quarantinePendingSignalSnapshots(observed.unsupported);
    state = withCurrentDailyBudget(quarantined.state);
  }
  const routableSignals = partitionSignalSnapshots(activeSignalSnapshots(state)).routable;
  const pendingSignals = selectRoutableSignalBatchSnapshots(routableSignals);
  const batch = createSignalBatch(pendingSignals);
  // Twee uur is de dagelijkse vrije ruimte. Lopend werk en echte signalen mogen
  // daar overheen gaan, zodat het budget nooit een half afgemaakt doel veroorzaakt.
  if (remainingDailyMs(state) <= 0 && !hasRunnableWork(status) && routableSignals.length === 0) {
    // Ook een nog slapende bewonersopdracht valt buiten het vrije budget zodra
    // haar afgesproken meetmoment aanbreekt; verlies die deadline niet aan de dagreset.
    state.next_run_ts = boundedSchedulerTs(nextDayResetTs(), status);
    state.last_result = {
      productive: false,
      daily_allocation_complete: true,
      summary: 'Dagelijkse vrije werktijd gebruikt; hervat bij nieuw werk, een signaal of de volgende dag.',
    };
    writeState(state);
    return { budgetExhausted: true };
  }
  if (isNight(new Date(), config.sleepStart, config.wakeTime)
    && !mayWorkAtNight(status, routableSignals)) return null;

  const leaseOwner = `eva:${process.pid}:${Date.now()}`;
  // De portefeuille kiest betekenisvol tussen uitvoerbare commitments. Een
  // nieuwe aandachtsrichting mag een al gemaakte eigen keuze niet telkens
  // verdringen; vrije nieuwsgierigheid komt terug zodra runnable werk klaar is
  // of bewust op een wake/externe afhankelijkheid wacht.
  const portfolioChoice = urgentMonitoringDelivery
    || selectPortfolioCommitment(durableWorkStatus().open);
  const reserved = urgentMonitoringDelivery ? null : shouldReserveAttentionForUserWork(
    status, routableSignals, portfolioChoice,
  );
  if (reserved) {
    state.next_run_ts = new Date(reserved.wake).toISOString();
    state.last_result = {
      productive: false,
      deferred_for_user_work: true,
      work_id: reserved.item.id,
      summary: 'Vrije aandacht uitgesteld tot het nabijgelegen hervatmoment van een bewonersopdracht.',
    };
    writeState(state);
    return { deferred: true, workId: reserved.item.id, nextRunTs: state.next_run_ts };
  }
  const attention = mayStartFreeAttention(routableSignals, portfolioChoice)
    ? dueGoalAttention() || dueAttention()
    : null;
  const selected = attention ? null : portfolioChoice;
  const deferredExplorationRetryTs = !attention && !selected && routableSignals.length === 0
    ? nextDeferredExplorationRetryTs()
    : null;
  if (deferredExplorationRetryTs) {
    state.next_run_ts = boundedSchedulerTs(deferredExplorationRetryTs, status);
    state.last_result = {
      productive: false,
      deferred_for_exploration_retry: true,
      summary: 'Lege exploration uitgesteld; hervat op de begrensde host-retrytijd.',
    };
    writeState(state);
    return { deferred: true, nextRunTs: state.next_run_ts };
  }
  const openWorkId = Number(selected?.item?.id);
  const leasedWork = /** @type {any} */ (Number.isFinite(openWorkId)
    ? claimWorkItem(openWorkId, { owner: leaseOwner, leaseMs: (config.autonomyTurnMinutes + 5) * 60_000 })
    : null);
  if (Number.isFinite(openWorkId) && !leasedWork) return null;
  const turnSignals = leasedWork ? [] : pendingSignals;
  const turnBatch = leasedWork ? null : batch;

  const workReason = leasedWork
    ? `${reason}. De portefeuille koos werk #${leasedWork.id} en episode ${selected?.episode?.id || '-'} op revisie ${leasedWork.revision}. Selectiereden: ${JSON.stringify(selected?.rationale || {})}`
    : reason;
  const groundedReason = turnSignals.length
    ? `${workReason}. Sinds je vorige aandacht kwamen deze echte signalen binnen:\n${turnSignals.map((signal) => `- [${signal.id}] bron ${signal.key}: ${signal.text}`).join('\n')}`
    : attention
      ? `${workReason}. Er is een eigen aandachtsrichting beschikbaar (${attention.kind} #${attention.id}): ${attention.text}. Dit is jouw optionele eigen aandachtsrichting: bepaal zelf of je haar nu volgt of bewust iets anders belangrijker vindt.`
      : workReason;
  const issuedSchedulerContext = schedulerTurnContext({
    reason: groundedReason,
    interactionEpoch: status.interaction_epoch,
    pendingSignals: turnSignals,
    signalBatch: turnBatch,
    attention,
    workId: leasedWork?.id || null,
    goalEpisodeId: selected?.episode?.id || null,
  });
  currentSelection = observableSchedulerSelection(issuedSchedulerContext);
  const runStartedAt = Date.now();
  bindSelectionDisplay(currentSelection, schedulerSelectionLabels({
    attention: issuedSchedulerContext.attention,
    work: leasedWork,
    rationale: selected?.rationale || null,
    signals: turnSignals,
  }), runStartedAt);
  running = true;
  let runError = null;
  try {
    if (selected?.episode?.id) updateGoalEpisode(selected.episode.id, {
      // Een al voorbereide, idempotent gebonden aflevering blijft in haar
      // eigen toestand; alleen inhoudelijk werk gaat naar executing.
      state: selected.episode.state === 'delivery_pending'
        ? 'delivery_pending' : 'executing',
      nextAction: leasedWork?.current_step || leasedWork?.next_step || leasedWork?.goal || '',
      sessionRef: leaseOwner,
    });
    const rawResult = await runAutonomousTurn({
      reason: groundedReason,
      pendingSignals: turnSignals,
      attention,
      workId: leasedWork?.id || null,
      goalEpisodeId: selected?.episode?.id || null,
      schedulerContext: issuedSchedulerContext,
    });
    const result = rawResult && typeof rawResult === 'object'
      ? rawResult
      : { error: 'invalid_autonomy_completion' };
    const technicalFailure = technicalAutonomyFailure(result);
    const productive = !!result?.contribution;
    const progressed = result?.progress?.made_progress === true;
    const yielded = result?.yielded === true || result?.superseded === true;
    const acknowledgedIds = new Set(acknowledgedSignalIds(result?.tools, turnBatch));
    const signalsHandled = routableSignals.length === 0
      || (turnSignals.length > 0 && routableSignals.every((signal) => acknowledgedIds.has(signal.id)));
    const latest = withCurrentDailyBudget(readState());
    const elapsedMs = Math.max(0, Date.now() - runStartedAt);
    latest.daily_used_ms += elapsedMs;
    if (productive || progressed) latest.daily_productive_ms += elapsedMs;
    if (yielded) latest.daily_preempted_ms += elapsedMs;
    if (acknowledgedIds.size) {
      latest.pending_signals = removeAcknowledgedSignalSnapshots(
        latest.pending_signals, turnBatch, [...acknowledgedIds],
      );
    }
    const completedAt = Date.now();
    latest.last_run_ts = new Date(completedAt).toISOString();
    const stillRoutable = partitionSignalSnapshots(activeSignalSnapshots(latest)).routable;
    const privatePerceptionPending = perceptionWakePending || perceptionIncidentPending();
    const urgentMonitoringAfterCompletion = urgentDueMonitoringDelivery(completedAt);
    const proposedNextRunTs = privatePerceptionPending
      ? new Date(completedAt + 1_000).toISOString()
      : urgentMonitoringAfterCompletion
        ? new Date(completedAt + 15_000).toISOString()
      : technicalFailure
        ? new Date(completedAt + TECHNICAL_AUTONOMY_RETRY_MS).toISOString()
        : remainingDailyMs(latest) <= 0
          && !hasRunnableWork(agentStatus())
          && stillRoutable.length === 0
          ? nextDayResetTs()
          : new Date(completedAt
            + (yielded ? RECENT_CONVERSATION_PAUSE_MS : nextDelay(result))).toISOString();
    latest.next_run_ts = boundedSchedulerTs(proposedNextRunTs, agentStatus());
    latest.last_result = {
      turn_id: result?.turnId || null,
      productive,
      progressed,
      yielded,
      tool: result?.contribution?.tool || null,
      signals_handled: signalsHandled,
      signals_acknowledged: acknowledgedIds.size,
      signals_deferred: Math.max(0, routableSignals.length - turnSignals.length),
      ...(technicalFailure ? { technical_retry: true } : {}),
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
    if (!productive && !progressed && !yielded && !technicalFailure) latest.quiet_runs += 1;
    db.transaction(() => {
      if (shouldSettleAttention(result)) {
        settleIssuedAspirationAttention(attention);
        if (shouldBackoffExploration(result)) {
          settleIssuedExplorationAttention(attention);
        }
      }
      writeState(latest);
    }).immediate();
    return result;
  } catch (error) {
    runError = error;
    const latest = withCurrentDailyBudget(readState());
    latest.daily_used_ms += Math.max(0, Date.now() - runStartedAt);
    const completedAt = Date.now();
    const errorText = String(error?.message || error).slice(0, 300);
    latest.last_run_ts = new Date(completedAt).toISOString();
    const privatePerceptionPending = perceptionWakePending || perceptionIncidentPending();
    const urgentMonitoringAfterFailure = urgentDueMonitoringDelivery(completedAt);
    latest.next_run_ts = boundedSchedulerTs(new Date(completedAt
      + (privatePerceptionPending ? 1_000
        : urgentMonitoringAfterFailure ? 15_000 : TECHNICAL_AUTONOMY_RETRY_MS)).toISOString(),
    agentStatus());
    latest.last_result = {
      productive: false,
      technical_retry: true,
      error: errorText,
    };
    writeState(latest);
    logTimeline('system', `Zelfstandige beurt liep vast: ${error?.message || error}`, {
      internalOnly: true,
      autonomy: true,
    });
    return { error: errorText };
  } finally {
    try {
      if (leasedWork) releaseWorkLease(leasedWork.id, leaseOwner, {
        error: runError ? String(runError?.message || runError) : null,
      });
    } finally {
      running = false;
      if (currentSelection) selectionDisplayBindings.delete(currentSelection);
      currentSelection = null;
    }
  }
}

async function tick() {
  if (!config.autonomyEnabled || !schedulerStarted) return false;
  const state = withCurrentDailyBudget(readState());
  const due = Date.parse(state.next_run_ts || '') || 0;
  if (Date.now() < due) return;
  const result = await runOnce();
  if (!result) {
    // Een lopend gesprek krijgt de ene agentlus, maar full hervat kort daarna
    // zelfstandig in plaats van tien minuten passief te blijven.
    // Lees opnieuw: een tegelijk binnengekomen signaal mag hier niet verloren gaan.
    const latest = withCurrentDailyBudget(readState());
    const idleTs = new Date(Date.now() + (perceptionIncidentPending()
      ? PERCEPTION_RETRY_MS
      : urgentDueMonitoringDelivery() ? 15_000
        : (config.autonomyFull ? 2 : 10) * 60_000)).toISOString();
    latest.next_run_ts = boundedSchedulerTs(idleTs, agentStatus());
    writeState(latest);
  }
}

export function startAutonomy() {
  if (!config.autonomyEnabled || schedulerStarted) return false;
  recoverWorkLeases();
  try { initializePerceptionIncidentRuntime(); } catch (error) {
    logTimeline('system', `Private deurbelrecovery bleef uit: ${error?.message || error}`, {
      internalOnly: true, perception: true,
    });
  }
  if (!kvGet(KEY, null)) writeState(initialState());
  perceptionWakePending = perceptionIncidentPending();
  if (perceptionWakePending || config.autonomyFull || hasOpenWork(agentStatus())) {
    const state = readState();
    const soon = Date.now() + (perceptionWakePending
      ? 1_000 : urgentDueMonitoringDelivery() ? 15_000 : 60_000);
    if ((Date.parse(state.next_run_ts || '') || Infinity) > soon) {
      state.next_run_ts = new Date(soon).toISOString();
      writeState(state);
    }
  }
  startSignalInbox();
  schedulerStarted = true;
  armTimer();
  return true;
}

export function autonomyStatus() {
  if (!config.autonomyEnabled) {
    return Object.freeze({
      enabled: false,
      scheduler_started: false,
      running: false,
      level: 'disabled',
    });
  }
  const state = withCurrentDailyBudget(readState());
  const display = autonomyDisplayProjection(state);
  return {
    enabled: true,
    scheduler_started: schedulerStarted,
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
    current_selection: currentSelection,
    display,
  };
}

export const __autonomyScheduleTest = {
  tick,
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
  technicalAutonomyFailure,
  shouldSettleAttention,
  shouldBackoffExploration,
  earliestFutureWorkWake,
  boundedSchedulerTs,
  upcomingUserCommitment,
  shouldReserveAttentionForUserWork,
  signalBatch: createSignalBatch,
  acknowledgedSignalIds,
  removeAcknowledgedSignalSnapshots,
  partitionSignalSnapshots,
  quarantineUnsupportedSignalSnapshots,
  quarantinePendingSignalSnapshots,
  selectRoutableSignalBatchSnapshots,
  boundedAutonomyDisplayLabel,
  schedulerSelectionLabels,
  bindSelectionDisplay,
  currentSelectionDisplay,
};
