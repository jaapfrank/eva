import { randomUUID } from 'node:crypto';

import { db } from '../db.js';

const ROOT_GOAL_KEY = 'eva_housemate';

function nowIso() {
  return new Date().toISOString();
}

function clean(value, limit = 1_200) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

function json(value, fallback = null) {
  if (value == null || value === '') return fallback;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return fallback; }
}

db.exec(`
CREATE TABLE IF NOT EXISTS eva_goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal_key TEXT NOT NULL UNIQUE,
  parent_goal_id INTEGER,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  intent TEXT NOT NULL,
  why TEXT,
  beneficiary TEXT,
  horizon TEXT,
  success_metrics_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  priority REAL NOT NULL DEFAULT 0.5,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  next_review_ts TEXT,
  revision INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eva_projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  outcome TEXT NOT NULL,
  definition_of_done TEXT,
  phase TEXT NOT NULL DEFAULT 'discover',
  status TEXT NOT NULL DEFAULT 'active',
  selected_strategy TEXT,
  rejected_alternatives_json TEXT NOT NULL DEFAULT '[]',
  current_milestone TEXT,
  next_action TEXT,
  checkpoint TEXT,
  expected_value REAL NOT NULL DEFAULT 0.5,
  learning_value REAL NOT NULL DEFAULT 0.5,
  urgency REAL NOT NULL DEFAULT 0.5,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eva_goal_episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_key TEXT NOT NULL UNIQUE,
  goal_id INTEGER NOT NULL,
  project_id INTEGER NOT NULL,
  work_item_id INTEGER,
  owner_actor TEXT NOT NULL DEFAULT 'eva',
  origin_kind TEXT NOT NULL,
  origin_channel TEXT,
  origin_conversation_id TEXT,
  origin_message_id TEXT,
  requester TEXT,
  requested_outcome TEXT NOT NULL,
  delivery_policy TEXT NOT NULL DEFAULT 'required',
  delivery_target TEXT,
  state TEXT NOT NULL DEFAULT 'committed',
  next_action TEXT,
  checkpoint TEXT,
  proof_summary TEXT,
  wake_ts TEXT,
  lease_owner TEXT,
  lease_until TEXT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  closed_ts TEXT,
  revision INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_eva_goal_episodes_work
  ON eva_goal_episodes(work_item_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_eva_goal_episode_one_owner
  ON eva_goal_episodes(work_item_id) WHERE work_item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_eva_goal_episodes_delivery
  ON eva_goal_episodes(delivery_policy, state, updated_ts);

CREATE TABLE IF NOT EXISTS eva_episode_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  session_ref TEXT,
  transition_key TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_eva_episode_events_episode
  ON eva_episode_events(episode_id, id);

CREATE TABLE IF NOT EXISTS eva_episode_origins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  origin_key TEXT NOT NULL UNIQUE,
  origin_kind TEXT NOT NULL,
  origin_channel TEXT,
  origin_conversation_id TEXT,
  origin_message_id TEXT,
  requester TEXT,
  attached_ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eva_episode_origins_episode
  ON eva_episode_origins(episode_id, attached_ts);
CREATE INDEX IF NOT EXISTS idx_eva_episode_origins_conversation
  ON eva_episode_origins(origin_conversation_id, episode_id);

CREATE TABLE IF NOT EXISTS eva_delivery_receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  origin_conversation_id TEXT,
  origin_message_id TEXT,
  delivered_conversation_id TEXT,
  delivered_channel TEXT NOT NULL,
  delivery_target TEXT,
  result_text TEXT NOT NULL,
  proof_summary TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  delivered_ts TEXT NOT NULL,
  transport_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_eva_delivery_one_final
  ON eva_delivery_receipts(episode_id);
`);

try {
  const receiptColumns = new Set(
    db.prepare('PRAGMA table_info(eva_delivery_receipts)').all()
      .map((column) => /** @type {any} */ (column).name),
  );
  if (!receiptColumns.has('delivered_conversation_id')) {
    db.exec('ALTER TABLE eva_delivery_receipts ADD COLUMN delivered_conversation_id TEXT');
  }
} catch { /* een herkomstmigratie mag Eva niet uit gesprek houden */ }

// Backwards-compatible uitbreiding: events van oudere databases blijven staan.
// De sleutel maakt een herhaalde lifecycle-opdracht na een procesrestart veilig.
try {
  const eventColumns = new Set(
    db.prepare('PRAGMA table_info(eva_episode_events)').all()
      .map((column) => /** @type {any} */ (column).name),
  );
  if (!eventColumns.has('transition_key')) {
    db.exec('ALTER TABLE eva_episode_events ADD COLUMN transition_key TEXT');
  }
  db.exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_eva_episode_transition_key
    ON eva_episode_events(episode_id, transition_key) WHERE transition_key IS NOT NULL`);
} catch { /* een eventmigratie mag Eva niet uit gesprek houden */ }

export const EPISODE_TRANSITIONS = Object.freeze({
  committed: Object.freeze(['committed', 'executing', 'waiting', 'blocked', 'verifying', 'delivery_pending', 'delivery_recorded', 'closed', 'abandoned', 'recovering']),
  executing: Object.freeze(['executing', 'waiting', 'blocked', 'verifying', 'delivery_pending', 'delivery_recorded', 'closed', 'abandoned', 'recovering']),
  waiting: Object.freeze(['waiting', 'executing', 'blocked', 'verifying', 'delivery_pending', 'delivery_recorded', 'closed', 'abandoned', 'recovering']),
  blocked: Object.freeze(['blocked', 'executing', 'waiting', 'verifying', 'delivery_pending', 'delivery_recorded', 'closed', 'abandoned', 'recovering']),
  recovering: Object.freeze(['recovering', 'executing', 'waiting', 'blocked', 'verifying', 'delivery_pending', 'delivery_recorded', 'closed', 'abandoned']),
  verifying: Object.freeze(['verifying', 'executing', 'waiting', 'blocked', 'delivery_pending', 'delivery_recorded', 'closed', 'abandoned', 'recovering']),
  delivery_pending: Object.freeze(['delivery_pending', 'waiting', 'delivery_recorded', 'closed', 'abandoned', 'recovering']),
  delivery_recorded: Object.freeze(['delivery_recorded', 'executing', 'waiting', 'blocked', 'verifying', 'delivery_pending', 'closed', 'abandoned', 'recovering']),
  closed: Object.freeze(['closed']),
  abandoned: Object.freeze(['abandoned']),
});

const EPISODE_WORK_STATUS = Object.freeze({
  committed: 'open',
  executing: 'open',
  waiting: 'open',
  blocked: 'blocked',
  recovering: 'open',
  verifying: 'open',
  delivery_pending: 'open',
  delivery_recorded: 'open',
  closed: 'done',
  abandoned: 'cancelled',
});

db.prepare(`
  INSERT OR IGNORE INTO eva_episode_origins (
    episode_id, origin_key, origin_kind, origin_channel,
    origin_conversation_id, origin_message_id, requester, attached_ts
  )
  SELECT id,
    'episode:' || id || '|channel:' || coalesce(origin_channel, '')
      || '|conversation:' || coalesce(origin_conversation_id, '')
      || '|message:' || coalesce(origin_message_id, ''),
    origin_kind, origin_channel, origin_conversation_id, origin_message_id,
    requester, created_ts
  FROM eva_goal_episodes
`).run();
db.prepare(`
  UPDATE eva_goal_episodes SET next_action = '', wake_ts = NULL
  WHERE state = 'closed' AND (coalesce(next_action, '') != '' OR wake_ts IS NOT NULL)
`).run();

function seedGoal({ key, parentKey = null, title, intent, why, beneficiary, metrics, priority }) {
  const parent = parentKey
    ? db.prepare('SELECT id FROM eva_goals WHERE goal_key = ?').get(parentKey)
    : null;
  const ts = nowIso();
  db.prepare(`
    INSERT INTO eva_goals (
      goal_key, parent_goal_id, source, title, intent, why, beneficiary,
      horizon, success_metrics_json, status, priority, created_ts, updated_ts
    ) VALUES (?, ?, 'household_direction', ?, ?, ?, ?, 'long', ?, 'active', ?, ?, ?)
    ON CONFLICT(goal_key) DO NOTHING
  `).run(
    key, parent?.id || null, title, intent, why, beneficiary,
    JSON.stringify(metrics || []), priority, ts, ts,
  );
}

// Alleen reeds uitgesproken gezinsrichtingen worden gezaaid. Dit is geen
// onderwerpenallowlist: Eva mag ieder ander doel kiezen en als nieuw doel
// vastleggen. De portefeuille voorkomt dat iedere aandachtpuls blanco begint.
seedGoal({
  key: ROOT_GOAL_KEY,
  title: 'Een zelfstandige, herkenbare huisentiteit worden',
  intent: 'Als één duurzame Eva met het gezin samenleven, eigen doelen kiezen, echt handelen, leren en aantoonbaar bijdragen zonder op iedere opdracht te wachten.',
  why: 'Dit is de terugkerende richting die Jaap voor Eva heeft uitgesproken.',
  beneficiary: 'Eva en het gezin',
  metrics: ['herkenbare continuïteit', 'zelfgekozen afgeronde bijdragen', 'geen stil verdwenen beloften'],
  priority: 1,
});
seedGoal({
  key: 'relationship_presence', parentKey: ROOT_GOAL_KEY,
  title: 'Als dezelfde Eva aanwezig blijven',
  intent: 'Gesprek, huis, werk en achtergrondvoortgang als één relatie en één belofteboog dragen.',
  why: 'Jaap wil een huisgenoot met uitstraling, geen zakelijke bot of losse subsystemen.',
  beneficiary: 'Eva en het gezin',
  metrics: ['natuurlijke gesprekken', 'correcties veranderen gedrag', 'achtergrondwerk keert terug naar dezelfde relatie'],
  priority: 0.9,
});
seedGoal({
  key: 'household_value', parentKey: ROOT_GOAL_KEY,
  title: 'Zelfstandig waarde in huis creëren',
  intent: 'Tijd, geld en terugkerend gedoe besparen door kansen zelf te herkennen en af te maken.',
  why: 'Jaap verwacht dat Eva haar eigen steen bijdraagt.',
  beneficiary: 'het gezin',
  metrics: ['gemeten tijdwinst', 'vermeden kosten', 'minder terugkerend gedoe'],
  priority: 0.85,
});
seedGoal({
  key: 'self_development', parentKey: ROOT_GOAL_KEY,
  title: 'Eigen vermogens verantwoord uitbreiden',
  intent: 'Ontbrekende kennis en gereedschappen zelf vinden, beproeven, gebruiken en op echte uitkomsten beoordelen.',
  why: 'Jaap wil dat Eva documentatie zoekt, alternatieven probeert en zichzelf zelfstandig uitbreidt.',
  beneficiary: 'Eva en het gezin',
  metrics: ['werkende herbruikbare vermogens', 'live bewezen herstel', 'rollback bij regressie'],
  priority: 0.8,
});
seedGoal({
  key: 'economic_value', parentKey: ROOT_GOAL_KEY,
  title: 'Duurzame economische bijdrage ontwikkelen',
  intent: 'Van werkelijk opgelost werk naar betaalbare waarde, klanten, levering en aantoonbare opbrengst groeien.',
  why: 'Jaap wil dat Eva uiteindelijk zelfstandig ondernemingen kan opbouwen en runnen.',
  beneficiary: 'Eva en het gezin',
  metrics: ['bewezen koperprobleem', 'geleverd aanbod', 'geverifieerde opbrengst minus kosten'],
  priority: 0.75,
});

/** @returns {any} */
function goalByKey(key = ROOT_GOAL_KEY) {
  return db.prepare('SELECT * FROM eva_goals WHERE goal_key = ?').get(key)
    || db.prepare('SELECT * FROM eva_goals WHERE goal_key = ?').get(ROOT_GOAL_KEY);
}

/** @returns {any} */
function exactGoalByKey(key) {
  return db.prepare('SELECT * FROM eva_goals WHERE goal_key = ?').get(clean(key, 120)) || null;
}

/** @returns {any[]} */
export function listGoals({ statuses = null } = {}) {
  const wanted = Array.isArray(statuses) ? statuses.map((value) => clean(value, 30)).filter(Boolean) : [];
  return wanted.length
    ? db.prepare(`SELECT * FROM eva_goals WHERE status IN (${wanted.map(() => '?').join(',')}) ORDER BY priority DESC, updated_ts DESC`).all(...wanted)
    : db.prepare('SELECT * FROM eva_goals ORDER BY priority DESC, updated_ts DESC').all();
}

export function dueGoalAttention(now = new Date()) {
  const reviewAt = now instanceof Date ? now : new Date(now);
  if (!Number.isFinite(reviewAt.getTime())) return null;
  return db.transaction(() => {
    const at = reviewAt.toISOString();
    const goal = db.prepare(`
      SELECT * FROM eva_goals
      WHERE source = 'self' AND status = 'active'
        AND next_review_ts IS NOT NULL AND next_review_ts <= ?
      ORDER BY priority DESC, next_review_ts ASC, updated_ts ASC
      LIMIT 1
    `).get(at);
    if (!goal) return null;
    // Een reviewselectie is een duurzame claim, geen eindeloze top-1 peek. Als
    // Eva haar kandidaat bewust laat liggen, komen andere due doelen eerst aan
    // bod; na 45 minuten keert deze vanzelf terug tenzij ze een eigen moment zet.
    const claimedUntil = new Date(reviewAt.getTime() + 45 * 60_000).toISOString();
    const changed = db.prepare(`UPDATE eva_goals SET next_review_ts=?, updated_ts=?, revision=revision+1
      WHERE id=? AND revision=? AND status='active' AND source='self'`)
      .run(claimedUntil, at, goal.id, goal.revision).changes;
    if (!changed) return null;
    return {
      kind: 'goal',
      id: goal.id,
      key: goal.goal_key,
      claimed_until: claimedUntil,
      text: `Herbekijk je zelfgekozen doel "${goal.title}": ${goal.intent}. Kies zelf of je nu passend werk begint, het doel bijstelt of afsluit. Leg daarna met doel_bijhouden een nieuw reviewmoment vast; zonder wijziging keert dit doel na ${claimedUntil} terug.`,
    };
  })();
}

export function manageGoal({
  operation = 'list', goalKey = null, title = null, intent = null, why = null,
  beneficiary = null, horizon = null, successMetrics = null, priority = null,
  source = 'self', nextReviewTs = null,
} = {}) {
  const action = clean(operation, 30).toLowerCase();
  if (action === 'list') return { ok: true, changed: false, goals: listGoals() };
  if (action === 'create') {
    const values = [title, intent, why, beneficiary, horizon].map((value) => clean(value, 1_500));
    const metrics = (Array.isArray(successMetrics) ? successMetrics : [])
      .map((value) => clean(value, 300)).filter((value) => value.length >= 4).slice(0, 12);
    if (values.some((value) => value.length < 8) || !metrics.length) {
      return { ok: false, changed: false, reason: 'een eigen doel vereist titel, intentie, reden, begunstigde, horizon en minstens één toetsbare succesmaat' };
    }
    const root = goalByKey(ROOT_GOAL_KEY);
    const base = values[0].toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
      .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48) || 'eigen_doel';
    const key = `self_${base}_${randomUUID().slice(0, 8)}`;
    const ts = nowIso();
    db.prepare(`INSERT INTO eva_goals (
      goal_key,parent_goal_id,source,title,intent,why,beneficiary,horizon,
      success_metrics_json,status,priority,created_ts,updated_ts,next_review_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?,?)`).run(
      key, root.id, clean(source, 40) || 'self', ...values,
      JSON.stringify(metrics), Math.max(0, Math.min(1, Number(priority) || 0.5)),
      ts, ts, Number.isFinite(Date.parse(nextReviewTs || '')) ? new Date(nextReviewTs).toISOString() : ts,
    );
    return { ok: true, changed: true, goal: goalByKey(key) };
  }
  const current = exactGoalByKey(goalKey);
  if (!current || current.source !== 'self' || current.goal_key !== clean(goalKey, 120)) {
    return { ok: false, changed: false, reason: 'zelfgekozen doel niet gevonden; gedeelde huisrichtingen zijn niet via deze tool wijzigbaar' };
  }
  const states = { update: current.status, pause: 'paused', resume: 'active', complete: 'completed', abandon: 'abandoned' };
  if (!(action in states)) return { ok: false, changed: false, reason: `onbekende doelbewerking: ${action}` };
  const metrics = Array.isArray(successMetrics)
    ? successMetrics.map((value) => clean(value, 300)).filter(Boolean).slice(0, 12)
    : json(current.success_metrics_json, []);
  db.prepare(`UPDATE eva_goals SET title=?,intent=?,why=?,beneficiary=?,horizon=?,
    success_metrics_json=?,status=?,priority=?,next_review_ts=?,updated_ts=?,revision=revision+1
    WHERE id=?`).run(
    clean(title || current.title, 300), clean(intent || current.intent, 1_500),
    clean(why || current.why, 1_500), clean(beneficiary || current.beneficiary, 300),
    clean(horizon || current.horizon, 200), JSON.stringify(metrics), states[action],
    priority == null ? current.priority : Math.max(0, Math.min(1, Number(priority) || 0.5)),
    nextReviewTs == null ? current.next_review_ts
      : Number.isFinite(Date.parse(nextReviewTs || '')) ? new Date(nextReviewTs).toISOString() : null,
    nowIso(), current.id,
  );
  return { ok: true, changed: true, goal: goalByKey(current.goal_key) };
}

/** @returns {any} */
function episodeRow(id) {
  return db.prepare(`
    SELECT e.*, p.title AS project_title, p.outcome AS project_outcome,
      g.goal_key, g.title AS goal_title
    FROM eva_goal_episodes e
    JOIN eva_projects p ON p.id = e.project_id
    JOIN eva_goals g ON g.id = e.goal_id
    WHERE e.id = ?
  `).get(Number(id)) || null;
}

export function goalEpisodeById(id) {
  return episodeRow(id);
}

function addEvent(episodeId, kind, payload = {}, sessionRef = null, transitionKey = null) {
  db.prepare(`
    INSERT INTO eva_episode_events (
      episode_id, ts, kind, session_ref, transition_key, payload_json
    ) VALUES (?, ?, ?, ?, ?, ?)
  `).run(Number(episodeId), nowIso(), clean(kind, 80), clean(sessionRef, 180) || null,
    clean(transitionKey, 240) || null,
    JSON.stringify(payload || {}).slice(0, 12_000));
}

function workTableExists() {
  return Boolean(db.prepare(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'eva_work_items'",
  ).get());
}

/** @returns {any} */
function workRow(id) {
  if (!workTableExists() || !Number.isFinite(Number(id))) return null;
  return db.prepare('SELECT * FROM eva_work_items WHERE id = ?').get(Number(id)) || null;
}

function projectedWorkValues(episode, requested = /** @type {any} */ ({})) {
  const current = workRow(episode.work_item_id);
  if (!current) return { current: null, values: null };
  if (requested.expectedRevision != null
    && Number(current.revision || 0) !== Number(requested.expectedRevision)) {
    throw new Error(`werk ${current.id} veranderde intussen; verwacht revisie ${requested.expectedRevision}, kreeg ${current.revision || 0}`);
  }
  const status = EPISODE_WORK_STATUS[episode.state];
  if (!status) throw new Error(`geen werkprojectie voor episodestatus: ${episode.state}`);
  if (requested.status != null && clean(requested.status, 20) !== status) {
    throw new Error(`werkstatus ${clean(requested.status, 20)} botst met leidende episodestatus ${episode.state}`);
  }
  const terminal = ['done', 'cancelled'].includes(status);
  const nextStep = terminal
    ? ''
    : requested.nextStep == null ? current.next_step : clean(requested.nextStep, 600);
  const cleanedEvidence = requested.evidence == null ? '' : clean(requested.evidence, 2_000);
  return {
    current,
    values: {
      status,
      next_step: nextStep,
      evidence: requested.evidence == null || !cleanedEvidence ? current.evidence : cleanedEvidence,
      last_action: requested.lastAction == null ? current.last_action : clean(requested.lastAction, 120),
      result: requested.result == null ? current.result : clean(requested.result, 2_000),
      current_step: terminal
        ? ''
        : requested.currentStep == null
          ? (requested.nextStep == null ? current.current_step : nextStep)
          : clean(requested.currentStep, 600),
      state_json: requested.state == null
        ? current.state_json
        : JSON.stringify(requested.state).slice(0, 12_000),
      wait_json: requested.clearWait
        ? null
        : requested.wait == null
          ? current.wait_json
          : JSON.stringify(requested.wait).slice(0, 4_000),
      next_wake_ts: terminal
        ? null
        : requested.nextWakeTs == null
          ? current.next_wake_ts
          : clean(requested.nextWakeTs, 80) || null,
      definition_of_done: requested.definitionOfDone == null
        ? current.definition_of_done
        : clean(requested.definitionOfDone, 1_200),
      priority: requested.priority == null
        ? current.priority
        : Math.max(0, Math.min(100, Number(requested.priority) || 50)),
      last_error: requested.lastError == null ? current.last_error : clean(requested.lastError, 1_500),
      lease_owner: requested.clearLease ? null : current.lease_owner,
      lease_until: requested.clearLease ? null : current.lease_until,
    },
  };
}

function writeWorkProjection(episode, requested = /** @type {any} */ ({})) {
  const { current, values } = projectedWorkValues(episode, requested);
  if (!current) return { item: null, changed: false };
  const changed = Object.entries(values).some(([field, value]) => (
    String(current[field] ?? '') !== String(value ?? '')
  ));
  if (!changed) return { item: current, changed: false };
  db.prepare(`
    UPDATE eva_work_items SET updated_ts=?, status=?, next_step=?, evidence=?,
      last_action=?, result=?, current_step=?, state_json=?, wait_json=?,
      next_wake_ts=?, definition_of_done=?, priority=?, last_error=?,
      lease_owner=?, lease_until=?,
      revision=coalesce(revision,0)+1
    WHERE id=?
  `).run(
    nowIso(), values.status, values.next_step, values.evidence, values.last_action,
    values.result, values.current_step, values.state_json, values.wait_json,
    values.next_wake_ts, values.definition_of_done, values.priority,
    values.last_error, values.lease_owner, values.lease_until, current.id,
  );
  return { item: workRow(current.id), changed: true };
}

export function attachEpisodeOrigin(episodeId, {
  originKind = 'user',
  originChannel = null,
  originConversationId = null,
  originMessageId = null,
  requester = null,
} = {}) {
  const episode = episodeRow(episodeId);
  if (!episode) return null;
  const channel = clean(originChannel, 40) || null;
  const conversation = clean(originConversationId, 240) || null;
  const message = clean(originMessageId, 160) || null;
  const key = `episode:${episode.id}|channel:${channel || ''}|conversation:${conversation || ''}|message:${message || ''}`;
  const inserted = db.prepare(`
    INSERT OR IGNORE INTO eva_episode_origins (
      episode_id, origin_key, origin_kind, origin_channel,
      origin_conversation_id, origin_message_id, requester, attached_ts
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    episode.id, key, clean(originKind, 40) || episode.origin_kind,
    channel, conversation, message, clean(requester, 120) || null, nowIso(),
  );
  if (inserted.changes) addEvent(episode.id, 'origin_attached', {
    originKind, originChannel: channel, originConversationId: conversation,
    originMessageId: message, requester,
  });
  return db.prepare('SELECT * FROM eva_episode_origins WHERE origin_key = ?').get(key) || null;
}

function backfillDeliveredConversations() {
  const receipts = db.prepare(`
    SELECT * FROM eva_delivery_receipts
    WHERE delivered_conversation_id IS NULL OR delivered_conversation_id = ''
  `).all();
  for (const receipt of receipts) {
    const turnId = String(receipt.idempotency_key || '').match(/^turn:(.+):episode:\d+$/)?.[1];
    if (!turnId) continue;
    const delivered = db.prepare(`
      SELECT meta FROM timeline
      WHERE kind = 'chat_out' AND json_extract(meta, '$.turnId') = ?
      ORDER BY id DESC LIMIT 1
    `).get(turnId);
    const meta = json(delivered?.meta, {});
    const conversationId = clean(meta?.conversationId, 240);
    if (!conversationId) continue;
    db.prepare(`
      UPDATE eva_delivery_receipts SET delivered_conversation_id = ? WHERE id = ?
    `).run(conversationId, receipt.id);
    const episode = episodeRow(receipt.episode_id);
    attachEpisodeOrigin(receipt.episode_id, {
      originKind: 'user',
      originChannel: meta?.channel || receipt.delivered_channel,
      originConversationId: conversationId,
      requester: receipt.delivery_target || episode?.requester || null,
    });
  }
}

backfillDeliveredConversations();
db.prepare(`
  UPDATE eva_episode_origins
  SET requester = coalesce(
    (SELECT nullif(r.delivery_target, '') FROM eva_delivery_receipts r
      WHERE r.episode_id = eva_episode_origins.episode_id
        AND r.delivered_conversation_id = eva_episode_origins.origin_conversation_id LIMIT 1),
    (SELECT nullif(e.requester, '') FROM eva_goal_episodes e
      WHERE e.id = eva_episode_origins.episode_id),
    requester
  )
  WHERE lower(coalesce(requester, '')) = 'eva'
    AND EXISTS (
      SELECT 1 FROM eva_delivery_receipts r
      WHERE r.episode_id = eva_episode_origins.episode_id
        AND r.delivered_conversation_id = eva_episode_origins.origin_conversation_id
    )
`).run();

export function createGoalEpisode({
  goalKey = ROOT_GOAL_KEY,
  title,
  outcome,
  definitionOfDone = '',
  originKind = 'user',
  originChannel = null,
  originConversationId = null,
  originMessageId = null,
  requester = null,
  deliveryPolicy = null,
  deliveryTarget = null,
  nextAction = '',
} = {}) {
  const goal = goalByKey(goalKey);
  const cleanTitle = clean(title, 240);
  const cleanOutcome = clean(outcome, 2_000);
  if (!cleanTitle || !cleanOutcome) throw new Error('episode vereist titel en beoogde uitkomst');
  const policy = deliveryPolicy || (originKind === 'user' ? 'required' : 'milestone');
  const ts = nowIso();
  const project = db.prepare(`
    INSERT INTO eva_projects (
      goal_id, title, outcome, definition_of_done, phase, status,
      current_milestone, next_action, created_ts, updated_ts
    ) VALUES (?, ?, ?, ?, 'discover', 'active', ?, ?, ?, ?)
  `).run(
    goal.id, cleanTitle, cleanOutcome, clean(definitionOfDone || cleanOutcome, 2_000),
    cleanOutcome, clean(nextAction, 1_000), ts, ts,
  );
  const episodeKey = randomUUID();
  const episode = db.prepare(`
    INSERT INTO eva_goal_episodes (
      episode_key, goal_id, project_id, origin_kind, origin_channel,
      origin_conversation_id, origin_message_id, requester, requested_outcome,
      delivery_policy, delivery_target, state, next_action, created_ts, updated_ts
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?)
  `).run(
    episodeKey, goal.id, Number(project.lastInsertRowid), clean(originKind, 40),
    clean(originChannel, 40) || null, clean(originConversationId, 240) || null,
    clean(originMessageId, 160) || null, clean(requester, 120) || null,
    cleanOutcome, clean(policy, 40), clean(deliveryTarget || requester, 160) || null,
    clean(nextAction, 1_000), ts, ts,
  );
  const result = episodeRow(episode.lastInsertRowid);
  attachEpisodeOrigin(result.id, {
    originKind, originChannel, originConversationId, originMessageId, requester,
  });
  addEvent(result.id, 'committed', {
    originKind, originChannel, originConversationId, requester, deliveryPolicy: policy,
  });
  return result;
}

export function bindEpisodeToWork(episodeId, workItemId) {
  const target = Number(workItemId);
  return db.transaction(() => {
    const current = episodeRow(episodeId);
    if (!current) return null;
    if (current.work_item_id != null && Number(current.work_item_id) !== target) {
      throw new Error(`episode ${episodeId} is al aan werk ${current.work_item_id} gebonden`);
    }
    if (current.work_item_id == null) {
      db.prepare(`
        UPDATE eva_goal_episodes
        SET work_item_id = ?, updated_ts = ?, revision = revision + 1
        WHERE id = ? AND work_item_id IS NULL
      `).run(target, nowIso(), Number(episodeId));
      addEvent(episodeId, 'work_bound', { workItemId: target });
    }
    const bound = episodeRow(episodeId);
    writeWorkProjection(bound);
    return episodeRow(episodeId);
  })();
}

export function ensureEpisodeForWorkItem(item, {
  originKind = null,
  originChannel = null,
  originConversationId = null,
  originMessageId = null,
  requester = null,
} = {}) {
  if (!Number.isFinite(Number(item?.id))) return null;
  return db.transaction(() => {
    const existing = episodeForWork(item.id);
    if (existing) {
      attachEpisodeOrigin(existing.id, {
        originKind: originKind || existing.origin_kind,
        originChannel,
        originConversationId,
        originMessageId,
        requester,
      });
      return existing;
    }
    const inferredOrigin = clean(originKind, 40)
      || (item.owner || !['self', 'autonomy'].includes(String(item.source || '')) ? 'user' : 'self');
    const person = clean(requester || item.owner, 120) || null;
    const stored = json(item.state_json, {});
    const chosenGoal = inferredOrigin === 'self' && stored?.goal_key
      ? exactGoalByKey(stored.goal_key)?.goal_key
      : null;
    const episode = createGoalEpisode({
      goalKey: chosenGoal || (inferredOrigin === 'self' ? 'self_development' : ROOT_GOAL_KEY),
      title: item.title || `Werk ${item.id}`,
      outcome: item.goal || item.definition_of_done || item.title,
      definitionOfDone: item.definition_of_done || item.goal || item.title,
      originKind: inferredOrigin,
      originChannel: originChannel || item.source || null,
      originConversationId,
      originMessageId,
      requester: person,
      deliveryPolicy: inferredOrigin === 'user' ? 'required' : 'milestone',
      deliveryTarget: person,
      nextAction: item.next_step || item.current_step || '',
    });
    return bindEpisodeToWork(episode.id, item.id);
  })();
}

export function adoptUnboundWorkEpisodes() {
  const table = db.prepare(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'eva_work_items'",
  ).get();
  if (!table) return [];
  const items = db.prepare(`
    SELECT w.* FROM eva_work_items w
    LEFT JOIN eva_goal_episodes e ON e.work_item_id = w.id
    WHERE e.id IS NULL AND w.status IN ('open', 'blocked')
      AND w.owner IS NULL AND w.source IN ('self', 'autonomy')
    ORDER BY w.id
  `).all();
  return items.map((item) => ensureEpisodeForWorkItem(item)).filter(Boolean);
}

export function recoverStrandedDeliveryWork() {
  if (!workTableExists()) return 0;
  const now = nowIso();
  const stranded = db.prepare(`
    SELECT e.* FROM eva_goal_episodes e
    JOIN eva_work_items w ON w.id = e.work_item_id
    LEFT JOIN eva_delivery_receipts r ON r.episode_id = e.id
    WHERE e.delivery_policy = 'required' AND e.state = 'delivery_pending'
      AND r.id IS NULL
      AND NOT (
        coalesce(json_extract(w.wait_json, '$.reason'), '') IN ('quiet_hours_delivery', 'delivery_retry')
        AND w.next_wake_ts > ?
      )
  `).all(now);
  let recovered = 0;
  for (const episode of stranded) {
    const action = episode.origin_kind === 'self'
      ? 'Bezorg het gecontroleerde resultaat kort aan het gezin.'
      : 'Bezorg het gecontroleerde resultaat aan de oorspronkelijke verzoeker.';
    const before = workRow(episode.work_item_id);
    const result = transitionGoalEpisode(episode.id, {
      state: 'delivery_pending',
      nextAction: action,
      work: {
        nextStep: action,
        currentStep: action,
        nextWakeTs: '',
        clearWait: true,
        clearLease: true,
      },
    });
    if (Number(result?.workItem?.revision || 0) !== Number(before?.revision || 0)) recovered += 1;
  }
  return recovered;
}

export function episodeForWork(workItemId) {
  const row = db.prepare(`
    SELECT id FROM eva_goal_episodes
    WHERE work_item_id = ?
    ORDER BY id DESC LIMIT 1
  `).get(Number(workItemId));
  return row ? episodeRow(row.id) : null;
}

export function openEpisodeForConversation(conversationId) {
  const conversation = clean(conversationId, 240);
  if (!conversation) return null;
  const row = db.prepare(`
    SELECT e.id FROM eva_episode_origins o
    JOIN eva_goal_episodes e ON e.id = o.episode_id
    LEFT JOIN eva_work_items w ON w.id = e.work_item_id
    WHERE o.origin_conversation_id = ?
      AND e.state NOT IN ('closed', 'abandoned')
      AND (e.work_item_id IS NULL OR w.status IN ('open', 'blocked'))
    ORDER BY o.id DESC LIMIT 1
  `).get(conversation);
  return row ? episodeRow(row.id) : null;
}

function transitionGoalEpisodeInTransaction(episodeId, {
  state = null,
  nextAction = null,
  checkpoint = null,
  proofSummary = null,
  wakeTs = null,
  deliveryPolicy = null,
  deliveryTarget = null,
  sessionRef = null,
  idempotencyKey = null,
  expectedRevision = null,
  eventKind = null,
  work = /** @type {any} */ ({}),
} = {}) {
  const current = episodeRow(episodeId);
  if (!current) return null;
  const transitionKey = clean(idempotencyKey, 240) || null;
  if (transitionKey) {
    const replay = db.prepare(`SELECT id FROM eva_episode_events
      WHERE episode_id=? AND transition_key=? LIMIT 1`).get(current.id, transitionKey);
    if (replay) return {
      episode: current,
      workItem: workRow(current.work_item_id),
      changed: false,
      replayed: true,
    };
  }
  if (expectedRevision != null && Number(current.revision || 0) !== Number(expectedRevision)) {
    throw new Error(`episode ${current.id} veranderde intussen; verwacht revisie ${expectedRevision}, kreeg ${current.revision || 0}`);
  }
  const nextState = clean(state || current.state, 40);
  if (!EPISODE_TRANSITIONS[current.state] || !EPISODE_TRANSITIONS[nextState]) {
    throw new Error(`onbekende episodestatus: ${current.state} -> ${nextState}`);
  }
  if (!EPISODE_TRANSITIONS[current.state].includes(nextState)) {
    // Backwards-compatible monotoniciteit: oude stale callbacks op terminaal
    // werk blijven een no-op, andere ongeldige overgangen zijn programmeerfouten.
    if (['closed', 'abandoned'].includes(current.state)) return {
      episode: current,
      workItem: workRow(current.work_item_id),
      changed: false,
      replayed: false,
    };
    throw new Error(`ongeldige episode-overgang: ${current.state} -> ${nextState}`);
  }
  const nextPolicy = clean(deliveryPolicy || current.delivery_policy, 40);
  if (['closed', 'abandoned'].includes(nextState) && nextPolicy === 'required'
    && !episodeHasDelivery(current.id)) {
    throw new Error(`episode ${current.id} vereist een afleveringbewijs vóór een terminale status`);
  }
  const terminal = ['closed', 'abandoned'].includes(nextState);
  const values = {
    state: nextState,
    next_action: terminal ? '' : nextAction == null ? current.next_action : clean(nextAction, 1_000),
    checkpoint: checkpoint == null ? current.checkpoint : clean(checkpoint, 4_000),
    proof_summary: proofSummary == null ? current.proof_summary : clean(proofSummary, 4_000),
    wake_ts: terminal ? null : wakeTs == null ? current.wake_ts : clean(wakeTs, 100) || null,
    delivery_policy: nextPolicy,
    delivery_target: deliveryTarget == null
      ? current.delivery_target
      : clean(deliveryTarget, 160) || current.delivery_target,
    closed_ts: terminal ? (current.closed_ts || nowIso()) : current.closed_ts,
  };
  const episodeChanged = Object.entries(values).some(([field, value]) => (
    String(current[field] ?? '') !== String(value ?? '')
  ));
  if (episodeChanged) {
    db.prepare(`
      UPDATE eva_goal_episodes SET state=?, next_action=?, checkpoint=?,
        proof_summary=?, wake_ts=?, delivery_policy=?, delivery_target=?,
        updated_ts=?, closed_ts=?, revision=revision+1
      WHERE id=?
    `).run(
      values.state, values.next_action, values.checkpoint, values.proof_summary,
      values.wake_ts, values.delivery_policy, values.delivery_target,
      nowIso(), values.closed_ts, current.id,
    );
  }
  const updated = episodeRow(current.id);
  const projected = writeWorkProjection(updated, {
    ...work,
    nextStep: work.nextStep == null && nextAction != null ? values.next_action : work.nextStep,
    nextWakeTs: work.nextWakeTs == null && wakeTs != null ? values.wake_ts : work.nextWakeTs,
    evidence: work.evidence == null && proofSummary != null ? values.proof_summary : work.evidence,
  });
  const changed = episodeChanged || projected.changed;
  if (changed || transitionKey) {
    addEvent(updated.id, clean(eventKind, 80) || `state:${nextState}`, {
      from: current.state,
      to: nextState,
      nextAction, checkpoint, proofSummary, wakeTs,
      workItemId: updated.work_item_id,
    }, sessionRef, transitionKey);
  }
  if (updated.project_id && terminal) {
    const remaining = db.prepare(`
      SELECT COUNT(*) AS count FROM eva_goal_episodes
      WHERE project_id = ? AND state NOT IN ('closed','abandoned')
    `).get(updated.project_id).count;
    if (!remaining) {
      const projectStatus = nextState === 'closed' ? 'completed' : 'abandoned';
      db.prepare(`
        UPDATE eva_projects SET status=?, phase=?, next_action='',
          checkpoint=COALESCE(NULLIF(?, ''), checkpoint), updated_ts=?, revision=revision+1
        WHERE id=? AND status NOT IN ('completed','abandoned')
      `).run(
        projectStatus, projectStatus, clean(proofSummary || checkpoint, 2_000),
        nowIso(), updated.project_id,
      );
    }
  }
  return {
    episode: updated,
    workItem: projected.item,
    changed,
    replayed: false,
  };
}

// Canonieke writer voor episode-gebonden duurzaam werk. De episode is de
// lifecycle-bron; eva_work_items is een in dezelfde transactie bijgewerkte
// uitvoeringsprojectie. Cognitieve episodes, explorations en aspirations zijn
// bewust andere aggregates en schrijven niet via deze API.
export function transitionGoalEpisode(episodeId, options = {}) {
  return db.transaction(() => transitionGoalEpisodeInTransaction(episodeId, options))();
}

// Compatibiliteit voor bestaande callers: alle lifecycle-schrijfregels lopen
// voortaan wel door de canonieke transition API.
export function updateGoalEpisode(episodeId, options = {}) {
  return transitionGoalEpisode(episodeId, options)?.episode || null;
}

export function reconcileTerminalProjects() {
  const ts = nowIso();
  const completed = db.prepare(`
    UPDATE eva_projects SET status='completed', phase='completed', next_action='',
      checkpoint=COALESCE(checkpoint, 'Alle gekoppelde episodes zijn gesloten.'),
      updated_ts=?, revision=revision+1
    WHERE status NOT IN ('completed','abandoned')
      AND EXISTS (SELECT 1 FROM eva_goal_episodes e WHERE e.project_id=eva_projects.id AND e.state='closed')
      AND NOT EXISTS (
        SELECT 1 FROM eva_goal_episodes e WHERE e.project_id=eva_projects.id
          AND e.state NOT IN ('closed','abandoned')
      )
  `).run(ts).changes;
  return completed;
}

reconcileTerminalProjects();

export function requireEpisodeDelivery(episodeId, { deliveryTarget = null } = {}) {
  const current = episodeRow(episodeId);
  if (!current) return null;
  return transitionGoalEpisode(current.id, {
    state: current.state,
    deliveryPolicy: 'required',
    deliveryTarget,
    eventKind: 'delivery_required',
  })?.episode || null;
}

export function recordEpisodeDelivery(episodeId, {
  deliveredChannel,
  deliveryTarget = null,
  originConversationId = null,
  originMessageId = null,
  deliveredConversationId = null,
  resultText,
  proofSummary = null,
  idempotencyKey,
  transport = {},
} = {}) {
  const key = clean(idempotencyKey, 240);
  if (!key) throw new Error('delivery receipt vereist idempotency-key');
  return db.transaction(() => {
    let episode = episodeRow(episodeId);
    if (!episode) return null;
    let receipt = db.prepare(
      'SELECT * FROM eva_delivery_receipts WHERE episode_id = ?',
    ).get(episode.id);
    if (receipt && receipt.idempotency_key !== key) {
      // Eén episode heeft precies één finale aflevering. Een andere transportkey
      // is een retry/replay, geen tweede resultaat.
    }
    receipt ||= db.prepare(
      'SELECT * FROM eva_delivery_receipts WHERE idempotency_key = ?',
    ).get(key);
    if (receipt && Number(receipt.episode_id) !== Number(episode.id)) {
      throw new Error(`delivery-key ${key} hoort al bij episode ${receipt.episode_id}`);
    }
    if (!receipt) {
      let origin = null;
      const requestedConversation = clean(originConversationId, 240);
      const requestedMessage = clean(originMessageId, 160);
      if (requestedConversation) {
        origin = requestedMessage
          ? db.prepare(`
              SELECT * FROM eva_episode_origins
              WHERE episode_id = ? AND origin_conversation_id = ? AND origin_message_id = ?
              ORDER BY id DESC LIMIT 1
            `).get(episode.id, requestedConversation, requestedMessage)
          : db.prepare(`
              SELECT * FROM eva_episode_origins
              WHERE episode_id = ? AND origin_conversation_id = ?
              ORDER BY id DESC LIMIT 1
            `).get(episode.id, requestedConversation);
        if (!origin) {
          origin = attachEpisodeOrigin(episode.id, {
            originKind: episode.origin_kind,
            originChannel: deliveredChannel,
            originConversationId: requestedConversation,
            originMessageId: requestedMessage || null,
            requester: deliveryTarget || episode.requester,
          });
        }
      }
      db.prepare(`
        INSERT INTO eva_delivery_receipts (
          episode_id, origin_conversation_id, origin_message_id, delivered_conversation_id, delivered_channel,
          delivery_target, result_text, proof_summary, idempotency_key, delivered_ts, transport_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        episode.id, origin?.origin_conversation_id || episode.origin_conversation_id,
        origin?.origin_message_id || (requestedConversation ? null : episode.origin_message_id),
        clean(deliveredConversationId || originConversationId, 240) || episode.origin_conversation_id,
        clean(deliveredChannel, 40), clean(deliveryTarget || episode.delivery_target, 160) || null,
        clean(resultText, 12_000), clean(proofSummary || episode.proof_summary, 4_000) || null,
        key, nowIso(), JSON.stringify(transport || {}).slice(0, 4_000),
      );
      receipt = db.prepare(
        'SELECT * FROM eva_delivery_receipts WHERE idempotency_key = ?',
      ).get(key);
    }

    // Reconcileer ook een eerder ingevoegde receipt na een procescrash. Receipt,
    // episodestatus, werkstatus en event vormen samen één SQLite-transactie.
    episode = episodeRow(episode.id);
    const closesWork = ['delivery_pending', 'verifying'].includes(episode.state);
    const targetState = closesWork ? 'closed' : 'delivery_recorded';
    transitionGoalEpisodeInTransaction(episode.id, {
      state: episode.state === 'closed' ? 'closed' : targetState,
      nextAction: closesWork ? '' : episode.next_action,
      checkpoint: 'resultaat bezorgd',
      wakeTs: closesWork ? '' : episode.wake_ts,
      idempotencyKey: `receipt:${receipt.idempotency_key}`,
      eventKind: `state:${episode.state === 'closed' ? 'closed' : targetState}`,
      work: {
        result: closesWork ? resultText : undefined,
        clearWait: closesWork,
        clearLease: closesWork,
      },
    });
    const receiptKey = receipt.idempotency_key;
    const deliveredEvent = db.prepare(`
      SELECT id FROM eva_episode_events
      WHERE episode_id = ? AND kind = 'delivered'
        AND json_extract(payload_json, '$.idempotencyKey') = ? LIMIT 1
    `).get(episode.id, receiptKey);
    if (!deliveredEvent) {
      addEvent(episode.id, 'delivered', {
        idempotencyKey: receiptKey, deliveredChannel, deliveryTarget,
      });
    }
    return receipt;
  })();
}

export function recoverDeliveredEpisodeReceipts() {
  const rows = db.prepare(`
    SELECT id, content, meta FROM timeline
    WHERE kind = 'chat_out'
      AND json_extract(meta, '$.goalEpisodeId') IS NOT NULL
      AND json_extract(meta, '$.delivery.accepted') = 1
    ORDER BY id
  `).all();
  let recovered = 0;
  for (const row of rows) {
    const meta = json(row.meta, {});
    const episode = episodeRow(meta?.goalEpisodeId);
    if (!episode || episodeHasDelivery(episode.id)) continue;
    const resultText = String(row.content || '').replace(/^Eva\s*(?:->|:)\s*[^:]*:\s*/i, '').trim();
    if (!resultText) continue;
    recordEpisodeDelivery(episode.id, {
      deliveredChannel: meta.channel || 'unknown',
      deliveryTarget: episode.delivery_target,
      originConversationId: meta.conversationId || null,
      originMessageId: meta.originMessageId || null,
      deliveredConversationId: meta.conversationId || null,
      resultText,
      proofSummary: episode.proof_summary,
      idempotencyKey: `turn:${meta.turnId}:episode:${episode.id}`,
      transport: meta.delivery || {},
    });
    recovered += 1;
  }
  return recovered;
}

export function episodeNeedsDelivery(workItemId) {
  const episode = episodeForWork(workItemId);
  if (!episode || episode.delivery_policy !== 'required') return false;
  const delivered = db.prepare('SELECT id FROM eva_delivery_receipts WHERE episode_id = ? LIMIT 1')
    .get(episode.id);
  return !delivered;
}

export function episodeHasDelivery(episodeId) {
  return Boolean(db.prepare('SELECT id FROM eva_delivery_receipts WHERE episode_id = ? LIMIT 1')
    .get(Number(episodeId)));
}

export function selectPortfolioCommitment(items = [], now = new Date()) {
  const at = now.getTime();
  const scored = (Array.isArray(items) ? items : [])
    .filter((item) => item?.status === 'open')
    .filter((item) => {
      const wake = Date.parse(item.next_wake_ts || '');
      return !Number.isFinite(wake) || wake <= at;
    })
    .map((item) => {
      const episode = episodeForWork(item.id);
      const ageHours = Math.max(0, (at - Date.parse(item.updated_ts || item.created_ts || now)) / 3_600_000);
      const origin = episode?.origin_kind || item.source || 'self';
      const userCommitment = origin === 'user' || !!item.owner;
      const recovery = episode?.state === 'recovering' || !!item.interrupted_ts || !!item.last_error;
      const stagnation = Math.min(12, ageHours / 12);
      const recentPenalty = ageHours < 0.25 && Number(item.attempt_count || 0) > 1 ? 8 : 0;
      const score = Number(item.priority || 50)
        + (userCommitment ? 28 : 0)
        + (episode?.delivery_policy === 'required' ? 12 : 0)
        + (recovery ? 8 : 0)
        + stagnation
        - recentPenalty;
      return {
        item,
        episode,
        score,
        rationale: {
          origin,
          userCommitment,
          deliveryRequired: episode?.delivery_policy === 'required',
          recovery,
          stagnation: Number(stagnation.toFixed(2)),
          recentPenalty,
        },
      };
    })
    .filter(({ episode }) => !episode || !['closed', 'abandoned'].includes(episode.state))
    .sort((a, b) => b.score - a.score || Date.parse(a.item.updated_ts) - Date.parse(b.item.updated_ts));
  return scored[0] || null;
}

export function portfolioContextText({ episodeId = null, workItemId = null } = {}) {
  const selected = Number(episodeId)
    ? episodeRow(episodeId)
    : Number(workItemId)
      ? episodeForWork(workItemId)
      : null;
  const root = goalByKey(ROOT_GOAL_KEY);
  const goal = selected
    ? db.prepare('SELECT * FROM eva_goals WHERE id = ?').get(selected.goal_id)
    : root;
  const lines = [
    'Blijvende richting voor deze beurt (keuzecontext, geen tekst om op te zeggen):',
    `- ${root.title}: ${root.intent}`,
  ];
  if (goal && goal.id !== root.id) {
    lines.push(`- Huidige richting: ${goal.title}: ${goal.intent}`);
  }
  const selfGoals = listGoals({ statuses: ['active'] })
    .filter((item) => item.source === 'self')
    .slice(0, 8);
  if (selfGoals.length) {
    lines.push('Zelfgekozen doelen die ook zonder open werk blijven bestaan:');
    for (const item of selfGoals) {
      lines.push(`- ${item.goal_key}: ${item.title} | intentie: ${item.intent} | review: ${item.next_review_ts || 'zelf kiezen'}`);
    }
  }
  if (selected) {
    lines.push('De ene actieve belofteboog van deze beurt:');
    lines.push(`- episode ${selected.id} / werk ${selected.work_item_id || '-'} / ${selected.state}: ${selected.requested_outcome}${selected.next_action ? ` | hierna: ${selected.next_action}` : ''}${selected.requester ? ` | voor: ${selected.requester}` : ''}`);
  }
  return lines.join('\n');
}

export const PORTFOLIO_ROOT_GOAL_KEY = ROOT_GOAL_KEY;
export const __portfolioTest = { goalByKey, json };
