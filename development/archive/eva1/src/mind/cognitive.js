// Eva's gedeelde cognitieve toestand. Dit is geen tweede agent en schrijft geen
// antwoorden: het verbindt waarneming, aandacht, handeling en gevolg tot één
// duurzame episode die alle gespecialiseerde vermogens kunnen delen.
import { randomUUID } from 'node:crypto';
import { bus } from '../bus.js';
import { db } from '../db.js';
import { clamp } from '../util.js';

const STATE_ID = 1;
const FREE_NO_ACTION_TTL_MS = 24 * 60 * 60_000;
const SEMANTIC_ACTION_ID = /^[a-z][a-z0-9_.:-]{0,119}$/u;
const LEGACY_READ_OPERATIONS = new Set([
  'home.context.read\u0000read', 'home.api.read\u0000read',
  'conversation.history.read\u0000search', 'work.episode.read\u0000list',
  'goal.portfolio.read\u0000list', 'goal.portfolio.read\u0000lijst',
  'exploration.read\u0000list', 'exploration.read\u0000lijst',
]);
let started = false;
let schemaReady = false;

function now() {
  return new Date().toISOString();
}

function json(value, fallback = {}) {
  if (value == null || value === '') return fallback;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return fallback; }
}

function plainRecord(value) {
  return value != null && typeof value === 'object' && !Array.isArray(value);
}

function semanticActionBinding(capabilityId, operationId) {
  const capability = String(capabilityId || '');
  const operation = String(operationId || '');
  return SEMANTIC_ACTION_ID.test(capability) && SEMANTIC_ACTION_ID.test(operation)
    ? { capabilityId: capability, operationId: operation }
    : null;
}

function ensureSchema() {
  if (schemaReady) return;
  db.exec(`
    CREATE TABLE IF NOT EXISTS cognitive_state (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      revision INTEGER NOT NULL DEFAULT 0,
      phase TEXT NOT NULL DEFAULT 'idle',
      active_episode_id INTEGER,
      focus_kind TEXT,
      focus_ref TEXT,
      goal_key TEXT,
      attention_reason TEXT,
      regulation_json TEXT NOT NULL DEFAULT '{}',
      continuity_json TEXT NOT NULL DEFAULT '{}',
      last_event_id INTEGER,
      last_outcome_id INTEGER,
      updated_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS cognitive_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      episode_id INTEGER,
      correlation_id TEXT NOT NULL,
      kind TEXT NOT NULL,
      modality TEXT NOT NULL,
      source TEXT NOT NULL,
      source_ref TEXT,
      subject_key TEXT,
      salience REAL NOT NULL DEFAULT 0.5,
      confidence REAL NOT NULL DEFAULT 1,
      payload_json TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'integrated',
      processed_ts TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_cognitive_events_episode
      ON cognitive_events(episode_id, id);
    CREATE INDEX IF NOT EXISTS idx_cognitive_events_correlation
      ON cognitive_events(correlation_id, id);
    CREATE INDEX IF NOT EXISTS idx_cognitive_events_status
      ON cognitive_events(status, salience, ts);

    CREATE TABLE IF NOT EXISTS cognitive_episodes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      correlation_id TEXT NOT NULL UNIQUE,
      opened_ts TEXT NOT NULL,
      closed_ts TEXT,
      kind TEXT NOT NULL,
      trigger_event_id INTEGER,
      focus_kind TEXT NOT NULL,
      focus_ref TEXT,
      goal_key TEXT,
      why_now TEXT,
      expected_change TEXT,
      status TEXT NOT NULL DEFAULT 'active',
      result_class TEXT,
      evidence_json TEXT,
      effect_json TEXT,
      reflection_json TEXT
    );
  `);
  db.prepare(`
    INSERT OR IGNORE INTO cognitive_state (
      id, revision, phase, regulation_json, continuity_json, updated_ts
    ) VALUES (1, 0, 'idle', ?, ?, ?)
  `).run(JSON.stringify({
    load: 0,
    uncertainty: 0,
    continuity: 0,
    connection: 0.5,
    competence: 0.5,
    novelty: 0.5,
    quiet: 0.5,
  }), JSON.stringify({ interrupted: [], lastCompleted: null }), now());
  db.prepare('UPDATE cognitive_events SET episode_id = NULL WHERE episode_id = 0').run();
  schemaReady = true;
}

function stateRow() {
  ensureSchema();
  const row = db.prepare('SELECT * FROM cognitive_state WHERE id = ?').get(STATE_ID);
  return {
    ...row,
    regulation: json(row?.regulation_json),
    continuity: json(row?.continuity_json),
  };
}

function updateState(fields = {}) {
  const current = stateRow();
  const next = {
    phase: fields.phase ?? current.phase,
    active_episode_id: fields.activeEpisodeId === undefined
      ? current.active_episode_id : fields.activeEpisodeId,
    focus_kind: fields.focusKind === undefined ? current.focus_kind : fields.focusKind,
    focus_ref: fields.focusRef === undefined ? current.focus_ref : fields.focusRef,
    goal_key: fields.goalKey === undefined ? current.goal_key : fields.goalKey,
    attention_reason: fields.attentionReason === undefined
      ? current.attention_reason : fields.attentionReason,
    regulation_json: JSON.stringify(fields.regulation ?? current.regulation),
    continuity_json: JSON.stringify(fields.continuity ?? current.continuity),
    last_event_id: fields.lastEventId === undefined ? current.last_event_id : fields.lastEventId,
    last_outcome_id: fields.lastOutcomeId === undefined
      ? current.last_outcome_id : fields.lastOutcomeId,
  };
  db.prepare(`
    UPDATE cognitive_state SET
      revision = revision + 1,
      phase = @phase,
      active_episode_id = @active_episode_id,
      focus_kind = @focus_kind,
      focus_ref = @focus_ref,
      goal_key = @goal_key,
      attention_reason = @attention_reason,
      regulation_json = @regulation_json,
      continuity_json = @continuity_json,
      last_event_id = @last_event_id,
      last_outcome_id = @last_outcome_id,
      updated_ts = @updated_ts
    WHERE id = 1
  `).run({ ...next, updated_ts: now() });
  const updated = cognitiveState();
  bus.emit('cognitive_state', updated);
  return updated;
}

function compactPayload(payload = {}) {
  const clean = {};
  for (const [key, value] of Object.entries(payload || {})) {
    if (value === undefined) continue;
    if (typeof value === 'string') clean[key] = value.slice(0, 600);
    else if (['number', 'boolean'].includes(typeof value) || value === null) clean[key] = value;
    else if (Array.isArray(value)) clean[key] = value.slice(0, 12).map((item) => (
      typeof item === 'string' ? item.slice(0, 180) : item
    ));
    else clean[key] = value;
  }
  return clean;
}

export function cognitiveState() {
  const row = stateRow();
  return {
    revision: row.revision,
    phase: row.phase,
    activeEpisodeId: row.active_episode_id,
    focus: row.focus_kind ? {
      kind: row.focus_kind,
      ref: row.focus_ref,
      goalKey: row.goal_key,
      reason: row.attention_reason,
    } : null,
    regulation: row.regulation,
    continuity: row.continuity,
    lastEventId: row.last_event_id,
    lastOutcomeId: row.last_outcome_id,
    updatedAt: row.updated_ts,
  };
}

export function ingestCognitiveEvent({
  episodeId = null,
  correlationId = randomUUID(),
  kind = 'observation',
  modality = 'internal',
  source = 'eva',
  sourceRef = null,
  subjectKey = null,
  salience = 0.5,
  confidence = 1,
  payload = {},
  status = 'integrated',
} = {}) {
  ensureSchema();
  const ts = now();
  const boundedSalience = clamp(Number(salience) || 0);
  const numericEpisodeId = episodeId != null && Number.isFinite(Number(episodeId))
    ? Number(episodeId) : null;
  // Hoogfrequente telemetrie (vermogen, CPU, kleine sensorschommelingen) is een
  // zintuigelijke onderlaag, geen reeks nieuwe aandachtspunten. Bewaar per bron
  // de nieuwste samenvatting binnen een uur; echte episodes en opvallende
  // gebeurtenissen blijven append-only.
  if (numericEpisodeId == null
    && source === 'home_assistant'
    && modality === 'environmental'
    && boundedSalience < 0.5
    && sourceRef) {
    const recent = db.prepare(`
      SELECT id FROM cognitive_events
      WHERE episode_id IS NULL AND kind = ? AND source = ? AND source_ref = ?
        AND salience < 0.5 AND ts >= ?
      ORDER BY id DESC LIMIT 1
    `).get(kind, source, String(sourceRef), new Date(Date.now() - 60 * 60_000).toISOString());
    if (recent) {
      db.prepare(`
        UPDATE cognitive_events SET ts = ?, correlation_id = ?, subject_key = ?,
          salience = ?, confidence = ?, payload_json = ?, status = ?, processed_ts = ?
        WHERE id = ?
      `).run(
        ts, String(correlationId), subjectKey == null ? null : String(subjectKey),
        boundedSalience, clamp(Number(confidence) || 0),
        JSON.stringify(compactPayload(payload)), String(status),
        status === 'pending' ? null : ts, recent.id,
      );
      return { id: Number(recent.id), ts, correlationId: String(correlationId), episodeId: null, coalesced: true };
    }
  }
  const result = db.prepare(`
    INSERT INTO cognitive_events (
      ts, episode_id, correlation_id, kind, modality, source, source_ref,
      subject_key, salience, confidence, payload_json, status, processed_ts
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    ts,
    numericEpisodeId,
    String(correlationId),
    String(kind),
    String(modality),
    String(source),
    sourceRef == null ? null : String(sourceRef),
    subjectKey == null ? null : String(subjectKey),
    boundedSalience,
    clamp(Number(confidence) || 0),
    JSON.stringify(compactPayload(payload)),
    String(status),
    status === 'pending' ? null : ts,
  );
  const id = Number(result.lastInsertRowid);
  if (numericEpisodeId != null || boundedSalience >= 0.7) updateState({ lastEventId: id });
  return { id, ts, correlationId: String(correlationId), episodeId: numericEpisodeId };
}

function episodeByCorrelation(correlationId) {
  return db.prepare('SELECT * FROM cognitive_episodes WHERE correlation_id = ?')
    .get(String(correlationId));
}

export function beginCognitiveEpisode({
  correlationId = randomUUID(),
  kind = 'interaction',
  modality = 'linguistic',
  source = 'unknown',
  sourceRef = null,
  subjectKey = null,
  focusKind = kind,
  focusRef = null,
  goalKey = null,
  whyNow = '',
  expectedChange = '',
  salience = 0.7,
  confidence = 1,
  payload = {},
} = {}) {
  ensureSchema();
  const existing = episodeByCorrelation(correlationId);
  if (existing) {
    if (existing.status === 'interrupted') {
      db.prepare(`
        UPDATE cognitive_episodes
        SET status = 'active', closed_ts = NULL, reflection_json = NULL
        WHERE id = ?
      `).run(existing.id);
    }
    const current = stateRow();
    const regulation = {
      ...current.regulation,
      load: clamp((Number(current.regulation.load) || 0) + 0.12),
    };
    updateState({
      phase: ['active', 'interrupted'].includes(existing.status)
        ? 'deliberating' : existing.status,
      activeEpisodeId: existing.id,
      focusKind: existing.focus_kind,
      focusRef: existing.focus_ref,
      goalKey: existing.goal_key,
      attentionReason: existing.why_now,
      regulation,
    });
    return {
      id: existing.id,
      correlationId: existing.correlation_id,
      resumed: existing.status === 'interrupted',
      terminal: ['completed', 'abandoned'].includes(existing.status),
    };
  }

  const current = stateRow();
  const continuity = { ...current.continuity };
  let preempted = false;
  if (current.active_episode_id) {
    const previous = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?')
      .get(current.active_episode_id);
    if (previous?.status === 'active') {
      const interruptedAt = now();
      db.prepare(`
        UPDATE cognitive_episodes
        SET status = 'interrupted', closed_ts = ?, reflection_json = ?
        WHERE id = ? AND status = 'active'
      `).run(interruptedAt, JSON.stringify({ reason: `onderbroken door ${kind}` }), previous.id);
      continuity.interrupted = [
        ...(Array.isArray(continuity.interrupted) ? continuity.interrupted : []),
        {
          episodeId: previous.id,
          focusKind: previous.focus_kind,
          focusRef: previous.focus_ref,
          goalKey: previous.goal_key,
          interruptedAt,
        },
      ].slice(-12);
      preempted = true;
    }
  }

  const trigger = ingestCognitiveEvent({
    correlationId,
    kind: 'observation',
    modality,
    source,
    sourceRef,
    subjectKey,
    salience,
    confidence,
    payload,
  });
  const openedTs = now();
  const result = db.prepare(`
    INSERT INTO cognitive_episodes (
      correlation_id, opened_ts, kind, trigger_event_id, focus_kind,
      focus_ref, goal_key, why_now, expected_change, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
  `).run(
    String(correlationId), openedTs, String(kind), trigger.id, String(focusKind),
    focusRef == null ? null : String(focusRef),
    goalKey == null ? null : String(goalKey),
    String(whyNow || ''), String(expectedChange || ''),
  );
  const episodeId = Number(result.lastInsertRowid);
  db.prepare('UPDATE cognitive_events SET episode_id = ? WHERE id = ?')
    .run(episodeId, trigger.id);
  const regulation = {
    ...current.regulation,
    load: clamp((Number(current.regulation.load) || 0) + 0.15),
    connection: kind === 'interaction'
      ? clamp((Number(current.regulation.connection) || 0.5) + 0.06)
      : Number(current.regulation.connection) || 0.5,
    // Historische onderbrekingen zijn autobiografisch bewijs, geen lijst met
    // huidig werk. Alleen een zojuist onderbroken focus verhoogt kort de last;
    // work/portfolio bepalen wat er werkelijk nog openstaat.
    continuity: preempted ? 0.2 : 0,
  };
  updateState({
    phase: 'deliberating',
    activeEpisodeId: episodeId,
    focusKind,
    focusRef,
    goalKey,
    attentionReason: whyNow,
    continuity,
    regulation,
  });
  return { id: episodeId, correlationId: String(correlationId), resumed: false };
}

export function recordCognitiveAction(episodeId, {
  correlationId = null,
  tool = 'unknown',
  capabilityId = null,
  operationId = null,
  subject = null,
  expected = null,
  ok = null,
  verified = null,
  changed = null,
  executionStarted = true,
  effect = null,
  partial = false,
  needsFollowup = false,
  effectUncertain = null,
  evidence = null,
} = {}) {
  ensureSchema();
  const episode = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?').get(episodeId);
  if (!episode) return null;
  const semanticBinding = semanticActionBinding(capabilityId, operationId);
  const event = ingestCognitiveEvent({
    episodeId,
    correlationId: correlationId || episode.correlation_id,
    kind: 'action',
    modality: 'motor',
    source: 'tool',
    sourceRef: tool,
    subjectKey: subject,
    salience: changed ? 0.85 : 0.65,
    confidence: verified ? 1 : ok === false ? 0.2 : 0.55,
    payload: {
      tool,
      ...(semanticBinding || {}),
      subject,
      expected,
      ok,
      verified,
      changed,
      executionStarted: executionStarted === true,
      effect,
      partial: partial === true,
      needsFollowup: needsFollowup === true,
      effectUncertain: typeof effectUncertain === 'boolean' ? effectUncertain : null,
      evidence,
    },
  });
  updateState({ phase: 'acting', activeEpisodeId: episodeId, lastEventId: event.id });
  return event;
}

export function recordCognitiveOutcome(episodeId, {
  correlationId = null,
  resultClass = 'unknown',
  ok = false,
  verified = false,
  changed = false,
  executionStarted = true,
  effectType = null,
  partial = false,
  needsFollowup = false,
  effectUncertain = null,
  evidence = null,
  effect = null,
  sourceUpdatedAt = null,
} = {}) {
  ensureSchema();
  const episode = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?').get(episodeId);
  if (!episode) return null;
  const confidence = verified ? 1 : ok ? 0.55 : 0.2;
  const surprise = ok && verified ? 0.1 : 0.75;
  const event = ingestCognitiveEvent({
    episodeId,
    correlationId: correlationId || episode.correlation_id,
    kind: 'outcome',
    modality: 'internal',
    source: 'verification',
    sourceRef: resultClass,
    salience: ok && verified ? 0.7 : 0.9,
    confidence,
    payload: {
      resultClass,
      ok,
      verified,
      changed,
      executionStarted: executionStarted === true,
      effectType,
      partial: partial === true,
      needsFollowup: needsFollowup === true,
      effectUncertain: typeof effectUncertain === 'boolean' ? effectUncertain : null,
      evidence,
      effect,
      surprise,
      observedAt: now(),
      sourceUpdatedAt,
    },
  });
  const current = stateRow();
  const regulation = {
    ...current.regulation,
    uncertainty: clamp((Number(current.regulation.uncertainty) || 0) * 0.75 + (1 - confidence) * 0.25),
    competence: clamp((Number(current.regulation.competence) || 0.5) + (ok && verified ? 0.025 : -0.04)),
  };
  updateState({
    phase: ok && verified ? 'integrating' : 'recovering',
    activeEpisodeId: episodeId,
    regulation,
    lastOutcomeId: event.id,
  });
  return event;
}

export function closeCognitiveEpisode(episodeId, {
  status = 'completed',
  resultClass = null,
  evidence = null,
  effect = null,
  reflection = null,
} = {}) {
  ensureSchema();
  const episode = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?').get(episodeId);
  if (!episode) return false;
  const finalStatus = ['completed', 'interrupted', 'abandoned'].includes(status)
    ? status : 'completed';
  if (effect?.replySummary) {
    ingestCognitiveEvent({
      episodeId,
      correlationId: episode.correlation_id,
      kind: 'claim',
      modality: 'linguistic',
      source: 'eva',
      sourceRef: episode.focus_ref,
      subjectKey: 'eva',
      salience: 0.7,
      confidence: 0.75,
      payload: { text: effect.replySummary },
    });
  }
  db.prepare(`
    UPDATE cognitive_episodes SET
      closed_ts = ?, status = ?, result_class = ?, evidence_json = ?,
      effect_json = ?, reflection_json = ?
    WHERE id = ?
  `).run(
    now(), finalStatus, resultClass,
    evidence == null ? null : JSON.stringify(compactPayload(evidence)),
    effect == null ? null : JSON.stringify(compactPayload(effect)),
    reflection == null ? null : JSON.stringify(compactPayload(reflection)),
    episodeId,
  );
  const current = stateRow();
  const continuity = { ...current.continuity };
  continuity.interrupted = (Array.isArray(continuity.interrupted) ? continuity.interrupted : [])
    .filter((item) => Number(item?.episodeId) !== Number(episodeId));
  if (finalStatus === 'completed') continuity.lastCompleted = {
    episodeId,
    focusKind: episode.focus_kind,
    focusRef: episode.focus_ref,
    goalKey: episode.goal_key,
    completedAt: now(),
    resultClass,
  };
  if (finalStatus === 'interrupted') {
    continuity.interrupted = [...continuity.interrupted, {
      episodeId,
      focusKind: episode.focus_kind,
      focusRef: episode.focus_ref,
      goalKey: episode.goal_key,
      interruptedAt: now(),
    }].slice(-12);
  }
  const regulation = {
    ...current.regulation,
    load: clamp((Number(current.regulation.load) || 0) - 0.15),
    continuity: finalStatus === 'interrupted' ? 0.2 : 0,
  };
  if (Number(current.active_episode_id) === Number(episodeId)) {
    updateState({
      phase: finalStatus === 'interrupted' ? 'interrupted' : 'idle',
      activeEpisodeId: null,
      focusKind: null,
      focusRef: null,
      goalKey: null,
      attentionReason: null,
      continuity,
      regulation,
    });
  } else {
    updateState({ continuity, regulation });
  }
  return true;
}

const EXPERIENCE_LABELS = Object.freeze({
  huis_status: 'je hebt zelf je actuele Home Assistant-wereldbeeld geraadpleegd',
  ha_lees: 'je hebt zelf de gevraagde Home Assistant-entiteiten gelezen',
  bekijk_camera: 'je hebt zelf via de gekozen camera gekeken',
  bedien_huis: 'je hebt zelf een huisbediening uitgevoerd',
});

function latestAutonomousEpisodes() {
  return /** @type {any[]} */ (db.prepare(`
    SELECT episode.*, trigger.kind AS trigger_kind,
      trigger.source AS trigger_source,
      trigger.episode_id AS trigger_episode_id,
      trigger.correlation_id AS trigger_correlation_id,
      trigger.payload_json AS trigger_payload_json
    FROM cognitive_episodes AS episode
    LEFT JOIN cognitive_events AS trigger ON trigger.id = episode.trigger_event_id
    WHERE episode.kind = 'autonomous_attention'
    ORDER BY episode.id DESC
    LIMIT 3
  `).all());
}

function exactRecentFreeBoundary(episode, at) {
  if (!episode || episode.status !== 'completed'
    || episode.goal_key !== 'free_attention'
    || !['no_action', 'partial_execution'].includes(episode.result_class)
    || episode.trigger_kind !== 'observation' || episode.trigger_source !== 'autonomy_clock'
    || Number(episode.trigger_episode_id) !== Number(episode.id)
    || episode.trigger_correlation_id !== episode.correlation_id) return false;
  const closedAt = Date.parse(String(episode.closed_ts || ''));
  if (!Number.isFinite(closedAt) || closedAt > at || at - closedAt > FREE_NO_ACTION_TTL_MS) {
    return false;
  }
  const trigger = json(episode.trigger_payload_json, null);
  const evidence = json(episode.evidence_json, null);
  const effect = json(episode.effect_json, null);
  const common = plainRecord(trigger) && trigger.authenticFree === true
    && trigger.syntheticTest !== true
    && plainRecord(evidence) && evidence.contribution === null
    && plainRecord(effect) && effect.madeProgress === false
    && effect.replied === false && effect.continuationId == null;
  if (!common || episode.result_class === 'no_action') return common;
  return effect.readOnly === true && effect.hadChanged === false
    && effect.hadEffectUncertainty === false && effect.hadDurableEffect === false;
}

function unchangedActionSignatures(episode) {
  const events = /** @type {any[]} */ (db.prepare(`
    SELECT kind, source, payload_json
    FROM cognitive_events
    WHERE episode_id = ? AND kind IN ('action', 'outcome')
    ORDER BY id
  `).all(Number(episode.id)));
  if (events.length > 24 || events.length % 2 !== 0) return null;
  const signatures = [];
  for (let index = 0; index < events.length; index += 2) {
    const actionEvent = events[index];
    const outcomeEvent = events[index + 1];
    const action = json(actionEvent?.payload_json, null);
    const outcome = json(outcomeEvent?.payload_json, null);
    if (actionEvent?.kind !== 'action' || actionEvent.source !== 'tool'
      || outcomeEvent?.kind !== 'outcome' || outcomeEvent.source !== 'verification'
      || !plainRecord(action) || !plainRecord(outcome)) return null;
    const binding = semanticActionBinding(action.capabilityId, action.operationId);
    if (!binding || action.changed !== false || outcome.changed !== false
      || action.effectUncertain === true || outcome.effectUncertain === true) return null;
    const legacyNoAction = episode.result_class === 'no_action'
      && action.executionStarted == null && outcome.executionStarted == null;
    if (!legacyNoAction
      && (action.executionStarted !== true || outcome.executionStarted !== true)) return null;
    const effect = action.effect || outcome.effectType;
    if (legacyNoAction
      ? !LEGACY_READ_OPERATIONS.has(`${binding.capabilityId}\u0000${binding.operationId}`)
      : !['read', 'discovery'].includes(effect)) return null;
    if (action.tool !== outcome.resultClass
      || (!legacyNoAction && action.effect !== outcome.effectType)) return null;
    const verifiedUnchanged = action.ok === true && action.verified === true
      && outcome.ok === true && outcome.verified === true;
    if (!verifiedUnchanged) return null;
    const complete = verifiedUnchanged
      && action.partial !== true && outcome.partial !== true
      && action.needsFollowup !== true && outcome.needsFollowup !== true;
    if (complete) signatures.push(`${binding.capabilityId}/${binding.operationId}`);
  }
  return [...new Set(signatures)];
}

/**
 * Uitsluitend een negatieve, privacy-arme continuiteitsgrens. De projectie
 * bevat geen onderwerp, argument, bewijs, receipt of uitvoerrecht en valt
 * nooit terug op een oudere no-action wanneer een nieuwere autonome episode
 * niet kwalificeert.
 */
export function recentFreeNoActionContextText(at = new Date()) {
  ensureSchema();
  const nowMs = at instanceof Date ? at.getTime() : new Date(at).getTime();
  if (!Number.isFinite(nowMs)) return '';
  const signatures = [];
  for (const episode of latestAutonomousEpisodes()) {
    if (!exactRecentFreeBoundary(episode, nowMs)) break;
    const episodeSignatures = unchangedActionSignatures(episode);
    if (!episodeSignatures) break;
    for (const signature of episodeSignatures) {
      if (!signatures.includes(signature)) signatures.push(signature);
    }
  }
  if (!signatures.length) return '';
  const text = `Recente vrije aandacht (negatieve grens): geverifieerd en onveranderd gelezen: ${signatures.join(', ')}. Dit was geen bijdrage of voortgang. Zonder nieuw signaal, binding of bewijs: herhaal die exacte acties niet enkel voor cadans; kies vrij iets anders of blijf stil. Negatieve geschiedenis, geen opdracht of bevoegdheid.`;
  return text.length <= 400 ? text : '';
}

function cognitiveExperience(row) {
  const payload = json(row.payload_json);
  const label = EXPERIENCE_LABELS[payload.resultClass || row.source_ref];
  if (!label || payload.ok !== true || payload.verified !== true) return [];
  const evidence = json(payload.evidence, payload.evidence);
  const entity = typeof evidence === 'object'
    ? String(evidence?.entity || evidence?.entity_id || '').trim() : '';
  const state = typeof evidence === 'object'
    ? String(evidence?.state || evidence?.after || '').trim() : '';
  const plain = typeof evidence === 'string'
    && /^[\p{L}\p{N}_. +°%=-]{3,180}$/u.test(evidence.trim())
    ? evidence.trim() : '';
  return [`${label}${entity && state ? `; ${entity} is ${state}` : plain ? `; ${plain}` : ''}`];
}

export function cognitiveContextText() {
  const state = cognitiveState();
  const parts = ['Dit hoort allemaal bij dezelfde Eva en loopt gewoon door tussen gesprekken.'];
  const last = state.continuity?.lastCompleted;
  if (last) {
    const ownExperience = db.prepare(`SELECT source_ref, payload_json FROM cognitive_events
      WHERE episode_id = ? AND kind = 'outcome' ORDER BY id DESC LIMIT 3`)
      .all(last.episodeId).reverse().flatMap(cognitiveExperience);
    if (ownExperience.length) {
      parts.push(`Wat je kort geleden zelf deed of zag: ${ownExperience.join('; ')}. Een sensormeting kan ouder zijn dan het moment waarop je haar bekeek. Herhaal een handeling alleen als nieuw bewijs daar reden voor geeft.`);
    }
  }
  const r = state.regulation || {};
  if (Number(r.load) > 0.7 || Number(r.continuity) > 0.6) {
    parts.push('Er lopen veel dingen tegelijk. Maak er eerst één af of bewaar precies waar je verdergaat.');
  }
  if (Number(r.uncertainty) > 0.45) parts.push('Je bent niet zeker van het gevolg. Kijk opnieuw voordat je iets ontkent, herhaalt of geslaagd noemt.');
  return parts.join('\n');
}

function recoverInterruptedEpisode() {
  const current = stateRow();
  if (!current.active_episode_id) return;
  const episode = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?')
    .get(current.active_episode_id);
  if (!episode || episode.status !== 'active') return;
  closeCognitiveEpisode(episode.id, {
    status: 'interrupted',
    resultClass: 'process_restart',
    reflection: { reason: 'proces stopte voordat de episode werd gesloten' },
  });
}

export function startCognitiveCore() {
  if (started) return false;
  ensureSchema();
  recoverInterruptedEpisode();
  started = true;
  bus.on('house', (event) => {
    // Rijke cameratriggers blijven vóór Eva's keuze uitsluitend in de private
    // perception-attentionlane. Alleen haar latere getypeerde besluit/uitkomst
    // mag als eigen ervaring terugkomen; een los bus-object geeft geen bewijs.
    if (['doorbell', 'person_detected'].includes(event?.type)) return;
    ingestCognitiveEvent({
      correlationId: event?.correlationId || randomUUID(),
      kind: 'observation',
      modality: event?.type === 'person_detected' ? 'visual' : 'environmental',
      source: 'home_assistant',
      sourceRef: event?.entityId || event?.camera || event?.type || null,
      subjectKey: event?.type || null,
      salience: ['doorbell', 'door', 'arrival', 'departure'].includes(event?.type) ? 0.85 : 0.55,
      confidence: Number.isFinite(Number(event?.confidence)) ? Number(event.confidence) : 0.8,
      payload: { type: event?.type, text: event?.text || '' },
    });
  });
  bus.on('world_signal', (event) => {
    const deviceClass = String(event?.attributes?.deviceClass || '').toLowerCase();
    const domain = String(event?.domain || '');
    if (deviceClass === 'doorbell'
      || /_doorbell$/u.test(String(event?.entityId || ''))) return;
    const urgentBinary = domain === 'binary_sensor'
      && ['door', 'window', 'garage_door', 'opening', 'occupancy'].includes(deviceClass);
    const salience = urgentBinary || domain === 'person'
      ? 0.85
      : ['climate', 'light', 'cover', 'lock', 'media_player'].includes(domain)
        ? 0.55
        : 0.25;
    ingestCognitiveEvent({
      correlationId: event?.correlationId || randomUUID(),
      kind: 'observation',
      modality: 'environmental',
      source: 'home_assistant',
      sourceRef: event?.entityId || event?.key || null,
      subjectKey: event?.domain || event?.type || null,
      salience,
      confidence: 0.9,
      payload: {
        type: event?.type,
        oldState: event?.oldState,
        newState: event?.newState,
        text: event?.text || '',
      },
    });
  });
  bus.on('ha_snapshot_ready', (event) => {
    const states = Array.isArray(event?.states) ? event.states : [];
    ingestCognitiveEvent({
      correlationId: `ha:snapshot:${event?.ts || Date.now()}`,
      kind: 'observation',
      modality: 'environmental',
      source: 'home_assistant',
      sourceRef: 'full_snapshot',
      subjectKey: 'house',
      salience: 0.7,
      confidence: 0.95,
      payload: {
        entityCount: states.length,
        observedAt: new Date(Number(event?.ts) || Date.now()).toISOString(),
      },
    });
  });
  return true;
}
