// Eva's gedeelde cognitieve toestand. Dit is geen tweede agent en schrijft geen
// antwoorden: het verbindt waarneming, aandacht, handeling en gevolg tot één
// duurzame episode die alle gespecialiseerde vermogens kunnen delen.
import { randomUUID } from 'node:crypto';
import { bus } from '../bus.js';
import { db } from '../db.js';
import { clamp } from '../util.js';

const STATE_ID = 1;
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
    if (['interrupted', 'waiting'].includes(existing.status)) {
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
      phase: ['active', 'interrupted', 'waiting'].includes(existing.status)
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
      resumed: ['interrupted', 'waiting'].includes(existing.status),
      terminal: ['completed', 'abandoned'].includes(existing.status),
    };
  }

  const current = stateRow();
  const continuity = { ...current.continuity };
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
    continuity: clamp((continuity.interrupted?.length || 0) / 5),
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
  subject = null,
  expected = null,
  ok = null,
  verified = null,
  changed = null,
  evidence = null,
} = {}) {
  ensureSchema();
  const episode = db.prepare('SELECT * FROM cognitive_episodes WHERE id = ?').get(episodeId);
  if (!episode) return null;
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
    payload: { tool, subject, expected, ok, verified, changed, evidence },
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
  const finalStatus = ['completed', 'waiting', 'interrupted', 'abandoned'].includes(status)
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
  if (['waiting', 'interrupted'].includes(finalStatus)) {
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
    continuity: clamp((continuity.interrupted?.length || 0) / 5),
  };
  if (Number(current.active_episode_id) === Number(episodeId)) {
    updateState({
      phase: finalStatus === 'completed' ? 'idle' : finalStatus,
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

export function cognitiveContextText() {
  const state = cognitiveState();
  const parts = [
    'Je bent één doorlopende Eva. Dit is je actuele gedeelde toestand, niet een rol of antwoordsjabloon.',
  ];
  if (state.focus) {
    parts.push(`Actuele focus: ${state.focus.kind}${state.focus.ref ? ` (${state.focus.ref})` : ''}${state.focus.reason ? `, omdat ${state.focus.reason}` : ''}.`);
  }
  const interrupted = Array.isArray(state.continuity?.interrupted)
    ? state.continuity.interrupted.slice(-3) : [];
  if (interrupted.length) {
    parts.push(`Te hervatten bogen: ${interrupted.map((item) => (
      `${item.focusKind}${item.focusRef ? ` ${item.focusRef}` : ''}${item.goalKey ? ` [${item.goalKey}]` : ''}`
    )).join('; ')}.`);
  }
  const last = state.continuity?.lastCompleted;
  if (last) {
    parts.push(`Laatst afgeronde boog: ${last.focusKind}${last.focusRef ? ` ${last.focusRef}` : ''}; resultaat ${last.resultClass || 'afgerond'}.`);
    const outcomes = db.prepare(`
      SELECT source_ref, payload_json FROM cognitive_events
      WHERE episode_id = ? AND kind = 'outcome'
      ORDER BY id DESC LIMIT 3
    `).all(last.episodeId).reverse();
    const ownReceipts = outcomes.map((row) => {
      const payload = json(row.payload_json);
      const proof = String(payload.evidence || payload.effect || '').replace(/\s+/g, ' ').trim();
      const action = payload.resultClass || row.source_ref || 'handeling';
      const embodiedMeaning = action === 'huis_status'
        ? 'je hebt zelf je actuele Home Assistant-wereldbeeld geraadpleegd'
        : action === 'ha_lees'
          ? 'je hebt zelf de gevraagde Home Assistant-entiteiten gelezen'
          : action === 'bekijk_camera'
            ? 'je hebt zelf via de gekozen camera gekeken'
            : action === 'bedien_huis'
              ? 'je hebt zelf een huisbediening uitgevoerd'
              : `je hebt zelf ${action} gebruikt`;
      return `${embodiedMeaning}; ${payload.verified ? 'de gereedschapsuitkomst is geverifieerd' : 'de gereedschapsuitkomst is onbevestigd'}${proof ? ` — ${proof.slice(0, 420)}` : ''}`;
    });
    if (ownReceipts.length) {
      parts.push(`Eigen ervaring uit die boog: ${ownReceipts.join('; ')}. Een sensors bron-tijd kan ouder zijn dan het moment waarop jij de actuele snapshot raadpleegde; benoem die versheid apart. Ontken niet dat jij keek of handelde wanneer dit receipt dat bewijst, en herhaal de externe handeling alleen bij nieuwer tegenbewijs.`);
    }
  }
  const r = state.regulation || {};
  if (Number(r.load) > 0.7 || Number(r.continuity) > 0.6) {
    parts.push('Er lopen veel bogen tegelijk: rond de gekozen focus af of leg één exact hervatpunt vast voordat je iets nieuws begint.');
  }
  if (Number(r.uncertainty) > 0.45) parts.push('Je actuele gevolgzekerheid is laag: controleer de wereld voordat je een eerdere handeling ontkent, herhaalt of als gelukt claimt.');
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

export const __cognitiveTest = {
  ensureSchema,
  episodeByCorrelation,
};
