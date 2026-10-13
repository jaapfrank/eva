import db from '../db.js';
import { bus } from '../bus.js';

db.exec(`
CREATE TABLE IF NOT EXISTS eva_work_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  source TEXT NOT NULL,
  owner TEXT,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  next_step TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  evidence TEXT,
  last_action TEXT,
  result TEXT,
  visible_to_family INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_eva_work_items_status
  ON eva_work_items(status, updated_ts);
`);

const FLOW_COLUMNS = {
  definition_of_done: 'TEXT',
  current_step: 'TEXT',
  state_json: 'TEXT',
  wait_json: 'TEXT',
  priority: 'INTEGER NOT NULL DEFAULT 50',
  next_wake_ts: 'TEXT',
  revision: 'INTEGER NOT NULL DEFAULT 0',
  lease_owner: 'TEXT',
  lease_until: 'TEXT',
  attempt_count: 'INTEGER NOT NULL DEFAULT 0',
  last_error: 'TEXT',
  interrupted_ts: 'TEXT',
};
try {
  const present = new Set(db.prepare('PRAGMA table_info(eva_work_items)').all().map((column) => column.name));
  for (const [name, type] of Object.entries(FLOW_COLUMNS)) {
    if (!present.has(name)) db.exec(`ALTER TABLE eva_work_items ADD COLUMN ${name} ${type}`);
  }
  db.exec('CREATE INDEX IF NOT EXISTS idx_eva_work_wake ON eva_work_items(status, next_wake_ts, priority)');
} catch { /* een migratie mag Eva niet onnodig uit gesprek houden */ }

const STATUSES = new Set(['open', 'blocked', 'done', 'cancelled']);
const TERMINAL_STATES = new Set(['done', 'cancelled', 'closed', 'abandoned']);

function nowIso() {
  return new Date().toISOString();
}

function clean(value, limit = 1_200) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

function row(id) {
  return db.prepare('SELECT * FROM eva_work_items WHERE id = ?').get(Number(id)) || null;
}

function announce(item) {
  if (item) bus.emit('work_changed', {
    id: item.id,
    status: item.status,
    updated_ts: item.updated_ts,
  });
  return item;
}

export function terminalWorkProjection(values = {}, status = values.status) {
  if (!TERMINAL_STATES.has(clean(status, 20))) return {};
  let stateJson = values.state_json;
  try {
    const state = typeof stateJson === 'string' ? JSON.parse(stateJson) : stateJson;
    if (state && typeof state === 'object' && !Array.isArray(state)) {
      const nextState = { ...state };
      delete nextState.pending_delivery;
      delete nextState.final_delivery_binding;
      if (nextState.interpretation_reply_binding
        && typeof nextState.interpretation_reply_binding === 'object') {
        nextState.interpretation_reply_binding = {
          ...nextState.interpretation_reply_binding,
        };
        delete nextState.interpretation_reply_binding.supersessionScope;
      }
      stateJson = typeof stateJson === 'string'
        ? JSON.stringify(nextState).slice(0, 12_000)
        : nextState;
    }
  } catch { /* bewaar onbekende oudere toestand exact; wis alleen wat herkenbaar is */ }
  return {
    next_step: '',
    current_step: '',
    state_json: stateJson,
    wait_json: null,
    next_wake_ts: null,
    lease_owner: null,
    lease_until: null,
  };
}

export function createWorkItem({
  source = 'user',
  owner = null,
  title,
  goal,
  nextStep = '',
  visibleToFamily = true,
  definitionOfDone = '',
  priority = 50,
} = {}) {
  const item = {
    source: clean(source, 40) || 'user',
    owner: clean(owner, 80) || null,
    title: clean(title, 240),
    goal: clean(goal, 1_200),
    nextStep: clean(nextStep, 600),
    definitionOfDone: clean(definitionOfDone || goal, 1_200),
    priority: Math.max(0, Math.min(100, Number(priority) || 50)),
  };
  if (!item.title || !item.goal) throw new Error('werk heeft een concrete titel en einddoel nodig');

  const existing = db.prepare(`
    SELECT * FROM eva_work_items
    WHERE lower(title) = lower(?) AND coalesce(owner, '') = coalesce(?, '')
      AND status IN ('open', 'blocked')
    ORDER BY id DESC LIMIT 1
  `).get(item.title, item.owner);
  if (existing) {
    const nextStep = item.nextStep || existing.next_step || '';
    const bound = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='eva_goal_episodes'").get()
      && db.prepare('SELECT id FROM eva_goal_episodes WHERE work_item_id=? LIMIT 1').get(existing.id);
    if (bound) {
      if (existing.goal === item.goal
        && String(existing.next_step || '') === String(nextStep || '')) return existing;
      // Een andere bedoeling met dezelfde titel is nieuw werk; de bestaande
      // episode mag niet stil worden heropend of herschreven.
    } else {
      if (existing.goal === item.goal
        && String(existing.next_step || '') === String(nextStep || '')
        && existing.status === 'open') return existing;
      db.prepare(`
        UPDATE eva_work_items
        SET updated_ts = ?, goal = ?, next_step = COALESCE(NULLIF(?, ''), next_step), status = 'open'
        WHERE id = ?
      `).run(nowIso(), item.goal, item.nextStep, existing.id);
      return announce(row(existing.id));
    }
  }

  const ts = nowIso();
  const info = db.prepare(`
    INSERT INTO eva_work_items
      (created_ts, updated_ts, source, owner, title, goal, next_step, status,
       visible_to_family, definition_of_done, current_step, priority)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
  `).run(
    ts,
    ts,
    item.source,
    item.owner,
    item.title,
    item.goal,
    item.nextStep,
    visibleToFamily ? 1 : 0,
    item.definitionOfDone,
    item.nextStep,
    item.priority,
  );
  return announce(row(info.lastInsertRowid));
}

function validatedWorkStatus(id, current, status, proofAccepted, evidence) {
  const nextStatus = status ? clean(status, 20) : current.status;
  if (!STATUSES.has(nextStatus)) throw new Error(`ongeldige werkstatus: ${nextStatus}`);
  if (['done', 'cancelled'].includes(current.status) && nextStatus !== current.status) {
    throw new Error(`terminaal werk ${id} kan niet van ${current.status} naar ${nextStatus} terugkeren`);
  }
  if (nextStatus === 'done' && (!proofAccepted || !clean(evidence || current.evidence))) {
    throw new Error('werk mag pas klaar als een eerdere tool in deze beurt het resultaat heeft geverifieerd');
  }
  return nextStatus;
}

export function updateWorkItem(id, {
  status = null,
  nextStep = null,
  evidence = null,
  lastAction = null,
  result = null,
  proofAccepted = false,
  expectedRevision = null,
  currentStep = null,
  state = null,
  wait = null,
  nextWakeTs = null,
  definitionOfDone = null,
  priority = null,
  lastError = null,
} = {}) {
  const current = row(id);
  if (!current) throw new Error(`werk ${id} bestaat niet`);
  if (expectedRevision != null && Number(current.revision || 0) !== Number(expectedRevision)) {
    throw new Error(`werk ${id} veranderde intussen; verwacht revisie ${expectedRevision}, kreeg ${current.revision || 0}`);
  }
  const nextStatus = validatedWorkStatus(id, current, status, proofAccepted, evidence);
  const cleanedEvidence = evidence == null ? '' : clean(evidence, 2_000);
  const proposedValues = {
    status: nextStatus,
    next_step: nextStep == null ? current.next_step : clean(nextStep, 600),
    evidence: evidence == null || !cleanedEvidence ? current.evidence : cleanedEvidence,
    last_action: lastAction == null ? current.last_action : clean(lastAction, 120),
    result: result == null ? current.result : clean(result, 2_000),
    current_step: currentStep == null
      ? (nextStep == null ? current.current_step : clean(nextStep, 600))
      : clean(currentStep, 600),
    state_json: state == null ? current.state_json : JSON.stringify(state).slice(0, 12_000),
    wait_json: wait == null ? current.wait_json : JSON.stringify(wait).slice(0, 4_000),
    next_wake_ts: nextWakeTs == null ? current.next_wake_ts : clean(nextWakeTs, 80) || null,
    definition_of_done: definitionOfDone == null ? current.definition_of_done : clean(definitionOfDone, 1_200),
    priority: priority == null ? current.priority : Math.max(0, Math.min(100, Number(priority) || 50)),
    last_error: lastError == null ? current.last_error : clean(lastError, 1_500),
    lease_owner: current.lease_owner,
    lease_until: current.lease_until,
  };
  const nextValues = {
    ...proposedValues,
    ...terminalWorkProjection(proposedValues, nextStatus),
  };
  const changed = Object.entries(nextValues).some(([field, value]) => (
    String(current[field] ?? '') !== String(value ?? '')
  ));
  if (!changed) return current;
  db.prepare(`
    UPDATE eva_work_items
    SET updated_ts = ?, status = ?, next_step = ?, evidence = ?, last_action = ?, result = ?,
        current_step = ?, state_json = ?, wait_json = ?, next_wake_ts = ?,
        definition_of_done = ?, priority = ?, last_error = ?, lease_owner = ?, lease_until = ?,
        revision = coalesce(revision,0) + 1
    WHERE id = ?
  `).run(
    nowIso(),
    nextValues.status,
    nextValues.next_step,
    nextValues.evidence,
    nextValues.last_action,
    nextValues.result,
    nextValues.current_step,
    nextValues.state_json,
    nextValues.wait_json,
    nextValues.next_wake_ts,
    nextValues.definition_of_done,
    nextValues.priority,
    nextValues.last_error,
    nextValues.lease_owner,
    nextValues.lease_until,
    Number(id),
  );
  return announce(row(id));
}

export function listWorkItems({
  statuses = ['open', 'blocked'], limit = 40, offset = 0, order = 'queue',
  visibleToFamily = null,
} = {}) {
  const allowed = statuses.filter((status) => STATUSES.has(status));
  if (!allowed.length) return [];
  const visibilityClause = visibleToFamily == null ? '' : 'AND visible_to_family = ?';
  const visibilityArgs = visibleToFamily == null ? [] : [visibleToFamily ? 1 : 0];
  const ordering = order === 'workbook'
    ? `CASE status WHEN 'open' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END,
      CASE WHEN status = 'done' THEN updated_ts END DESC,
      CASE WHEN status != 'done' THEN coalesce(priority,50) END DESC,
      updated_ts DESC`
    : `CASE status WHEN 'open' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END,
      coalesce(priority,50) DESC, updated_ts DESC`;
  return db.prepare(`
    SELECT * FROM eva_work_items
    WHERE status IN (${allowed.map(() => '?').join(',')})
      ${visibilityClause}
    ORDER BY ${ordering}
    LIMIT ? OFFSET ?
  `).all(
    ...allowed,
    ...visibilityArgs,
    Math.max(1, Math.min(200, Number(limit) || 40)),
    Math.max(0, Math.trunc(Number(offset) || 0)),
  );
}

export function claimWorkItem(id, {
  owner = `eva:${process.pid}`,
  leaseMs = 35 * 60_000,
} = {}) {
  const current = row(id);
  if (!current || current.status !== 'open') return null;
  const now = new Date();
  const leaseUntil = Date.parse(current.lease_until || '');
  if (current.lease_owner && Number.isFinite(leaseUntil) && leaseUntil > now.getTime()
    && current.lease_owner !== owner) return null;
  const changed = db.prepare(`UPDATE eva_work_items SET
    lease_owner=?, lease_until=?, attempt_count=coalesce(attempt_count,0)+1,
    revision=coalesce(revision,0)+1, updated_ts=?
    WHERE id=? AND status='open'
      AND (lease_owner IS NULL OR lease_owner=? OR lease_until IS NULL OR lease_until<=?)`)
    .run(owner, new Date(now.getTime() + leaseMs).toISOString(), now.toISOString(), Number(id), owner, now.toISOString());
  return changed.changes ? row(id) : null;
}

export function releaseWorkLease(id, owner, { error = null } = {}) {
  const now = nowIso();
  db.prepare(`UPDATE eva_work_items SET lease_owner=NULL, lease_until=NULL,
    last_error=coalesce(?,last_error), revision=coalesce(revision,0)+1, updated_ts=?
    WHERE id=? AND lease_owner=?`)
    .run(error == null ? null : clean(error, 1_500), now, Number(id), owner);
  return row(id);
}

export function recoverWorkLeases() {
  const now = nowIso();
  const info = db.prepare(`UPDATE eva_work_items SET
    interrupted_ts=?, last_error=coalesce(last_error,'Onderbroken door procesherstart; hervat vanaf het laatste checkpoint.'),
    lease_owner=NULL, lease_until=NULL, revision=coalesce(revision,0)+1, updated_ts=?
    WHERE status='open' AND lease_owner IS NOT NULL`)
    .run(now, now);
  return info.changes;
}

export function durableWorkStatus(now = new Date(), { familyVisibleOnly = false } = {}) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const visibleToFamily = familyVisibleOnly ? true : null;
  const open = listWorkItems({ statuses: ['open'], limit: 40, visibleToFamily });
  const blocked = listWorkItems({ statuses: ['blocked'], limit: 40, visibleToFamily });
  const doneToday = db.prepare(`
    SELECT * FROM eva_work_items
    WHERE status = 'done' AND updated_ts >= ?
      ${familyVisibleOnly ? 'AND visible_to_family = 1' : ''}
    ORDER BY updated_ts DESC LIMIT 40
  `).all(start.toISOString());
  return { open, blocked, doneToday };
}
