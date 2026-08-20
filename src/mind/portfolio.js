import { createHash, randomUUID } from 'node:crypto';

import {
  canonicalTelegramText,
  consumeContributionMutationAuthority,
  db,
  newerTelegramUserMessage,
  recentVerifiedTurnToolReceipts,
  TELEGRAM_DELIVERY_STATUS,
  telegramMessage,
  validateTelegramDeliveryProof,
  validateExecutionRecord,
} from '../db.js';
import {
  turnUnderstandingAuthorityRecord,
  turnUnderstandingAuthorityStoreStatus,
} from '../turnauthority.js';
import { verifiedVoiceFinalDeliveryRecoveryProof } from '../voiceauthority.js';
import { redactSensitiveText } from '../security/redaction.js';
import { canonicalJson, errMsg, verifiedCompleteReceipt } from '../util.js';
import {
  issuedExplorationTerminalMutationMatches,
  verifyPreparedUnderstandingTurn,
  verifyTurnUnderstandingRecord,
} from './understanding.js';
import { bindHomeContextEntityReferences } from './home.js';
import { createWorkItem, terminalWorkProjection, updateWorkItem } from './work.js';
import {
  artifactVersionReceipt,
  artifactVersionsForEpisode,
} from './artifacts.js';
import {
  buildMonitoringMeasurementFact,
  issueMonitoringFactBinding,
  issueMonitoringLifecycleBinding,
  issueMonitoringOutcomeReceipt,
  monitoringConditionId,
  monitoringContractReadBindings,
  monitoringDeliveryMatchesAuthority,
  monitoringOutcomeRequestRecord,
  monitoringOutcomeResponseRecord,
  monitoringPendingFactReferences,
  monitoringStateFitsDurableBudget,
  normalizeMonitoringContract,
  normalizeMonitoringLifecycleBinding,
  normalizeMonitoringOutcomeDecision,
  normalizeMonitoringPendingDelivery,
  normalizeMonitoringPendingDeliveryReference,
} from './monitoringcontract.js';

const ROOT_GOAL_KEY = 'eva_housemate';
const DURABLE_EXECUTION_INTENT = 'DurableExecutionIntent/v1';
const EXECUTION_INTENT_EVENT = 'execution_intent_committed';
const SHA256 = /^[a-f0-9]{64}$/u;
const MONITORING_START_RESERVED_STATE_KEYS = new Set([
  'monitoring_runtime', 'pending_delivery', 'monitoring_lifecycle',
  'monitoring_start_authority', 'monitoring_start_delivery_origin',
]);
const INTERPRETATION_RETRY_CONTRACT = 'ChatInterpretationRetry/v1';
const INTERPRETATION_REPLY_PROVENANCE_CONTRACT = 'InterpretationRetryReplyProvenance/v1';
const FINAL_DELIVERY_BINDING_CONTRACT = 'FinalDeliveryBinding/v1';
const EXPLORATION_ECONOMIC_HANDOFF_CONTRACT = 'ExplorationEconomicHandoff/v1';
const EXPLORATION_ECONOMIC_GOAL_KEY = 'economic_value';
const WORK_STATE_JSON_LIMIT = 12_000;
const INTERPRETATION_RETRY_TTL_MS = 6 * 60 * 60_000;
const INTERPRETATION_RETRY_BACKOFF_MS = Object.freeze([
  60_000,
  5 * 60_000,
  20 * 60_000,
  60 * 60_000,
]);
const INTERPRETATION_HANDOFF_TABLE = 'eva_interpretation_retry_handoffs';
const INTERPRETATION_HANDOFF_ORIGIN_INDEX = 'idx_eva_interpretation_retry_origin';
const INTERPRETATION_HANDOFF_TABLE_SQL = `CREATE TABLE ${INTERPRETATION_HANDOFF_TABLE} (
  handoff_key TEXT NOT NULL PRIMARY KEY,
  contract TEXT NOT NULL,
  origin_channel TEXT NOT NULL,
  origin_conversation_id TEXT NOT NULL,
  origin_message_id TEXT NOT NULL,
  raw_original_text_sha256 TEXT NOT NULL,
  original_text_sha256 TEXT NOT NULL,
  requester_sha256 TEXT NOT NULL,
  supersession_scope_sha256 TEXT NOT NULL,
  supersession_token_sha256 TEXT NOT NULL,
  work_item_id INTEGER NOT NULL UNIQUE,
  episode_id INTEGER NOT NULL UNIQUE,
  binding_sha256 TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(contract, origin_channel, origin_conversation_id, origin_message_id)
)`;
const INTERPRETATION_HANDOFF_ORIGIN_INDEX_SQL = `CREATE INDEX
  ${INTERPRETATION_HANDOFF_ORIGIN_INDEX} ON ${INTERPRETATION_HANDOFF_TABLE}(
    origin_channel, origin_conversation_id, origin_message_id
  )`;
const INTERPRETATION_HANDOFF_INSERT_TRIGGER = `CREATE TRIGGER
trg_interpretation_retry_handoff_insert_guard
BEFORE INSERT ON ${INTERPRETATION_HANDOFF_TABLE}
BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM ${INTERPRETATION_HANDOFF_TABLE} AS prior
    WHERE prior.handoff_key = NEW.handoff_key
      OR prior.work_item_id = NEW.work_item_id
      OR prior.episode_id = NEW.episode_id
      OR (prior.contract = NEW.contract
        AND prior.origin_channel = NEW.origin_channel
        AND prior.origin_conversation_id = NEW.origin_conversation_id
        AND prior.origin_message_id = NEW.origin_message_id)
  ) THEN RAISE(ABORT, 'ChatInterpretationRetry/v1-registryidentiteit is onveranderlijk') END;
  SELECT CASE WHEN NEW.contract IS NULL
    OR NEW.contract <> 'ChatInterpretationRetry/v1'
    OR NEW.handoff_key IS NULL
    OR length(NEW.handoff_key) <> 64
    OR NEW.handoff_key GLOB '*[^0-9a-f]*'
    OR NEW.origin_channel IS NULL
    OR NEW.origin_channel NOT IN ('telegram', 'voice', 'web')
    OR NEW.origin_conversation_id IS NULL
    OR length(NEW.origin_conversation_id) < 1
    OR length(NEW.origin_conversation_id) > 240
    OR NEW.origin_message_id IS NULL
    OR length(NEW.origin_message_id) < 1
    OR length(NEW.origin_message_id) > 160
    OR NEW.raw_original_text_sha256 IS NULL
    OR length(NEW.raw_original_text_sha256) <> 64
    OR NEW.raw_original_text_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.original_text_sha256 IS NULL
    OR length(NEW.original_text_sha256) <> 64
    OR NEW.original_text_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.requester_sha256 IS NULL
    OR length(NEW.requester_sha256) <> 64
    OR NEW.requester_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.supersession_scope_sha256 IS NULL
    OR length(NEW.supersession_scope_sha256) <> 64
    OR NEW.supersession_scope_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.supersession_token_sha256 IS NULL
    OR length(NEW.supersession_token_sha256) <> 64
    OR NEW.supersession_token_sha256 GLOB '*[^0-9a-f]*'
    OR typeof(NEW.work_item_id) <> 'integer'
    OR NEW.work_item_id < 1
    OR typeof(NEW.episode_id) <> 'integer'
    OR NEW.episode_id < 1
    OR NEW.binding_sha256 IS NULL
    OR length(NEW.binding_sha256) <> 64
    OR NEW.binding_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.created_ts IS NULL
    OR length(NEW.created_ts) < 20
    OR length(NEW.created_ts) > 40
  THEN RAISE(ABORT, 'ongeldige ChatInterpretationRetry/v1-registryrij') END;
END`;
const INTERPRETATION_HANDOFF_UPDATE_TRIGGER = `CREATE TRIGGER
trg_interpretation_retry_handoff_immutable_update
BEFORE UPDATE ON ${INTERPRETATION_HANDOFF_TABLE}
BEGIN
  SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
END`;
const INTERPRETATION_HANDOFF_DELETE_TRIGGER = `CREATE TRIGGER
trg_interpretation_retry_handoff_immutable_delete
BEFORE DELETE ON ${INTERPRETATION_HANDOFF_TABLE}
BEGIN
  SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
END`;
const INTERPRETATION_HANDOFF_TRIGGER_SQL = Object.freeze(new Map([
  ['trg_interpretation_retry_handoff_insert_guard', INTERPRETATION_HANDOFF_INSERT_TRIGGER],
  ['trg_interpretation_retry_handoff_immutable_update', INTERPRETATION_HANDOFF_UPDATE_TRIGGER],
  ['trg_interpretation_retry_handoff_immutable_delete', INTERPRETATION_HANDOFF_DELETE_TRIGGER],
]));
function nowIso() {
  return new Date().toISOString();
}

function clean(value, limit = 1_200) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

function exactDeliveryText(value, limit = 12_000) {
  const text = String(value ?? '').trim();
  if (!text || text.length > limit) {
    throw new Error('delivery receipt vereist exact begrensde antwoordtekst');
  }
  return text;
}

function json(value, fallback = null) {
  if (value == null || value === '') return fallback;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return fallback; }
}

function semanticHash(value) {
  return createHash('sha256').update(canonicalJson(value)).digest('hex');
}

function optionalClean(value, limit) {
  return value == null ? null : clean(value, limit);
}

function transitionIntent({
  state, nextAction, checkpoint, proofSummary, wakeTs, deliveryPolicy,
  deliveryTarget, sessionRef, expectedRevision, eventKind,
  work = /** @type {any} */ ({}),
}) {
  return {
    state: optionalClean(state, 40),
    nextAction: optionalClean(nextAction, 1_000),
    checkpoint: optionalClean(checkpoint, 4_000),
    proofSummary: optionalClean(proofSummary, 4_000),
    wakeTs: optionalClean(wakeTs, 100),
    deliveryPolicy: optionalClean(deliveryPolicy, 40),
    deliveryTarget: optionalClean(deliveryTarget, 160),
    sessionRef: optionalClean(sessionRef, 180),
    expectedRevision: expectedRevision == null ? null : Number(expectedRevision),
    eventKind: optionalClean(eventKind, 80),
    work: {
      status: optionalClean(work.status, 20),
      nextStep: optionalClean(work.nextStep, 600),
      evidence: optionalClean(work.evidence, 2_000),
      lastAction: optionalClean(work.lastAction, 120),
      result: optionalClean(work.result, 2_000),
      currentStep: optionalClean(work.currentStep, 600),
      state: work.state == null ? null : work.state,
      clearWait: work.clearWait === true,
      wait: work.wait == null ? null : work.wait,
      nextWakeTs: optionalClean(work.nextWakeTs, 80),
      definitionOfDone: optionalClean(work.definitionOfDone, 1_200),
      priority: work.priority == null
        ? null : Math.max(0, Math.min(100, Number(work.priority) || 50)),
      lastError: optionalClean(work.lastError, 1_500),
      clearLease: work.clearLease === true,
      expectedRevision: work.expectedRevision == null ? null : Number(work.expectedRevision),
    },
  };
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

CREATE TABLE IF NOT EXISTS eva_execution_intent_heads (
  episode_id INTEGER PRIMARY KEY,
  work_item_id INTEGER NOT NULL,
  event_id INTEGER NOT NULL UNIQUE,
  intent_sha256 TEXT NOT NULL,
  generation INTEGER NOT NULL,
  active_effect_key TEXT,
  updated_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eva_execution_effect_receipts (
  effect_key TEXT PRIMARY KEY,
  episode_id INTEGER NOT NULL,
  work_item_id INTEGER NOT NULL,
  intent_event_id INTEGER NOT NULL,
  intent_sha256 TEXT NOT NULL,
  head_generation INTEGER NOT NULL,
  capability_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  lane TEXT NOT NULL,
  effect_type TEXT NOT NULL,
  args_sha256 TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  target_path TEXT,
  material_sha256 TEXT,
  preimage_sha256 TEXT,
  status TEXT NOT NULL,
  owner_pid INTEGER NOT NULL,
  owner_boot_id TEXT,
  owner_start_ticks TEXT,
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  result_json TEXT,
  result_sha256 TEXT
);
CREATE INDEX IF NOT EXISTS idx_eva_execution_effect_intent
  ON eva_execution_effect_receipts(episode_id,intent_event_id,status);

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

-- Een interpretatieherstel hoort bij precies één inbound oorsprong. De
-- lifecycle-status van episode of werk is nadrukkelijk geen onderdeel van deze
-- identiteit: een transportreplay na blokkeren, sluiten of een procesrestart
-- moet hetzelfde duurzame werk blijven terugvinden.
${INTERPRETATION_HANDOFF_TABLE_SQL.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ')};

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
`);

const INTERPRETATION_HANDOFF_COLUMNS = Object.freeze([
  ['handoff_key', 'TEXT', 1, 1],
  ['contract', 'TEXT', 1, 0],
  ['origin_channel', 'TEXT', 1, 0],
  ['origin_conversation_id', 'TEXT', 1, 0],
  ['origin_message_id', 'TEXT', 1, 0],
  ['raw_original_text_sha256', 'TEXT', 1, 0],
  ['original_text_sha256', 'TEXT', 1, 0],
  ['requester_sha256', 'TEXT', 1, 0],
  ['supersession_scope_sha256', 'TEXT', 1, 0],
  ['supersession_token_sha256', 'TEXT', 1, 0],
  ['work_item_id', 'INTEGER', 1, 0],
  ['episode_id', 'INTEGER', 1, 0],
  ['binding_sha256', 'TEXT', 1, 0],
  ['created_ts', 'TEXT', 1, 0],
]);
const INTERPRETATION_HANDOFF_UNIQUE_INDEXES = Object.freeze(new Map([
  ['handoff_key', 'pk'],
  ['work_item_id', 'u'],
  ['episode_id', 'u'],
  ['contract\u0000origin_channel\u0000origin_conversation_id\u0000origin_message_id', 'u'],
]));

function normalizedRegistrySql(value) {
  return String(value || '').replace(/;\s*$/u, '').replace(/\s+/gu, ' ').trim();
}

function registrySchemaFailure(detail) {
  throw new Error(`ChatInterpretationRetry/v1-registryschema is niet exact: ${detail}`);
}

function interpretationHandoffColumnRows() {
  return /** @type {any[]} */ (db.prepare(
    `PRAGMA table_info(${INTERPRETATION_HANDOFF_TABLE})`,
  ).all());
}

function recognizedInterpretationHandoffColumns(rows) {
  const names = rows.map((row) => row.name);
  const expected = INTERPRETATION_HANDOFF_COLUMNS.map(([name]) => name);
  const withoutRaw = expected.filter((name) => name !== 'raw_original_text_sha256');
  const rawAppended = [...withoutRaw, 'raw_original_text_sha256'];
  const shape = canonicalJson(names) === canonicalJson(expected)
    ? 'full'
    : canonicalJson(names) === canonicalJson(withoutRaw)
      ? 'missing_raw'
      : canonicalJson(names) === canonicalJson(rawAppended)
        ? 'raw_appended' : '';
  if (!shape) return null;
  for (const row of rows) {
    const expectedColumn = INTERPRETATION_HANDOFF_COLUMNS.find(([name]) => name === row.name);
    if (!expectedColumn || String(row.type).toUpperCase() !== expectedColumn[1]
      || Number(row.pk) !== expectedColumn[3]) return null;
    const migratableNullability = ['handoff_key', 'raw_original_text_sha256'].includes(row.name);
    if ((!migratableNullability && Number(row.notnull) !== 1)
      || (migratableNullability && ![0, 1].includes(Number(row.notnull)))) return null;
    const validDefault = row.dflt_value == null
      || (row.name === 'raw_original_text_sha256' && row.dflt_value === "''");
    if (!validDefault) return null;
  }
  return shape;
}

function exactInterpretationHandoffColumns(rows) {
  if (rows.length !== INTERPRETATION_HANDOFF_COLUMNS.length) return false;
  return rows.every((row, index) => {
    const expected = INTERPRETATION_HANDOFF_COLUMNS[index];
    return Number(row.cid) === index
      && row.name === expected[0]
      && String(row.type).toUpperCase() === expected[1]
      && Number(row.notnull) === expected[2]
      && Number(row.pk) === expected[3]
      && row.dflt_value == null;
  });
}

function interpretationHandoffIndexColumns(name) {
  const rows = /** @type {any[]} */ (db.prepare(`SELECT seqno,cid,name,desc,coll,key
    FROM pragma_index_xinfo(?) ORDER BY seqno`).all(name));
  const keyRows = rows.filter((row) => Number(row.key) === 1);
  if (!keyRows.length || keyRows.some((row) => Number(row.cid) < 0
    || Number(row.desc) !== 0 || row.coll !== 'BINARY')) return null;
  return keyRows.map((row) => row.name).join('\u0000');
}

function verifyInterpretationHandoffIndexes(allowMissingOrigin) {
  const indexes = /** @type {any[]} */ (db.prepare(
    `PRAGMA index_list(${INTERPRETATION_HANDOFF_TABLE})`,
  ).all());
  const remainingUnique = new Map(INTERPRETATION_HANDOFF_UNIQUE_INDEXES);
  let exactOrigin = false;
  for (const index of indexes) {
    if (Number(index.partial) !== 0) registrySchemaFailure('partiële index');
    const columns = interpretationHandoffIndexColumns(index.name);
    if (index.name === INTERPRETATION_HANDOFF_ORIGIN_INDEX) {
      const definition = db.prepare(`SELECT sql FROM sqlite_master
        WHERE type='index' AND name=? AND tbl_name=?`).get(
        INTERPRETATION_HANDOFF_ORIGIN_INDEX, INTERPRETATION_HANDOFF_TABLE,
      );
      exactOrigin = Number(index.unique) === 0 && index.origin === 'c'
        && columns === 'origin_channel\u0000origin_conversation_id\u0000origin_message_id'
        && normalizedRegistrySql(definition?.sql)
          === normalizedRegistrySql(INTERPRETATION_HANDOFF_ORIGIN_INDEX_SQL);
      if (!exactOrigin) registrySchemaFailure('oorsprongindex');
      continue;
    }
    if (Number(index.unique) !== 1 || !remainingUnique.has(String(columns || ''))
      || remainingUnique.get(columns) !== index.origin) {
      registrySchemaFailure(`onverwachte index ${String(index.name || '')}`);
    }
    remainingUnique.delete(columns);
  }
  if (remainingUnique.size) registrySchemaFailure('unieke identiteitindex ontbreekt');
  const globalOrigin = db.prepare(`SELECT tbl_name FROM sqlite_master
    WHERE type='index' AND name=?`).get(INTERPRETATION_HANDOFF_ORIGIN_INDEX);
  if (globalOrigin && globalOrigin.tbl_name !== INTERPRETATION_HANDOFF_TABLE) {
    registrySchemaFailure('oorsprongindex hoort bij een andere tabel');
  }
  if (!allowMissingOrigin && !exactOrigin) registrySchemaFailure('oorsprongindex ontbreekt');
  if (indexes.length !== INTERPRETATION_HANDOFF_UNIQUE_INDEXES.size + Number(exactOrigin)) {
    registrySchemaFailure('indexset bevat extra definities');
  }
}

function verifyInterpretationHandoffTriggers(allowMissing) {
  const attached = /** @type {any[]} */ (db.prepare(`SELECT name,sql
    FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(
    INTERPRETATION_HANDOFF_TABLE,
  ));
  for (const trigger of attached) {
    const expected = INTERPRETATION_HANDOFF_TRIGGER_SQL.get(trigger.name);
    if (!expected || normalizedRegistrySql(trigger.sql) !== normalizedRegistrySql(expected)) {
      registrySchemaFailure(`trigger ${String(trigger.name || '')}`);
    }
  }
  for (const [name] of INTERPRETATION_HANDOFF_TRIGGER_SQL) {
    const globalTrigger = db.prepare(`SELECT tbl_name FROM sqlite_master
      WHERE type='trigger' AND name=?`).get(name);
    if (globalTrigger && globalTrigger.tbl_name !== INTERPRETATION_HANDOFF_TABLE) {
      registrySchemaFailure(`triggernaam ${name} hoort bij een andere tabel`);
    }
  }
  if (!allowMissing && attached.length !== INTERPRETATION_HANDOFF_TRIGGER_SQL.size) {
    registrySchemaFailure('append-only triggerset is onvolledig');
  }
}

function recreateInterpretationHandoffTable() {
  const legacyTable = 'eva_interpretation_retry_handoffs_schema_migration';
  if (db.prepare(`SELECT 1 FROM sqlite_master WHERE name=?`).get(legacyTable)) {
    registrySchemaFailure('onafgemaakte schemamigratie');
  }
  db.exec(`ALTER TABLE ${INTERPRETATION_HANDOFF_TABLE} RENAME TO ${legacyTable}`);
  for (const [name] of INTERPRETATION_HANDOFF_TRIGGER_SQL) {
    db.exec(`DROP TRIGGER IF EXISTS ${name}`);
  }
  db.exec(`DROP INDEX IF EXISTS ${INTERPRETATION_HANDOFF_ORIGIN_INDEX}`);
  db.exec(`${INTERPRETATION_HANDOFF_TABLE_SQL};`);
  db.exec(`INSERT INTO ${INTERPRETATION_HANDOFF_TABLE} (
      handoff_key,contract,origin_channel,origin_conversation_id,origin_message_id,
      raw_original_text_sha256,original_text_sha256,requester_sha256,
      supersession_scope_sha256,supersession_token_sha256,work_item_id,episode_id,
      binding_sha256,created_ts
    ) SELECT handoff_key,contract,origin_channel,origin_conversation_id,origin_message_id,
      coalesce(raw_original_text_sha256,''),original_text_sha256,requester_sha256,
      supersession_scope_sha256,supersession_token_sha256,work_item_id,episode_id,
      binding_sha256,created_ts FROM ${legacyTable}`);
  db.exec(`DROP TABLE ${legacyTable}`);
}

function installInterpretationHandoffSchemaObjects() {
  db.exec(`${INTERPRETATION_HANDOFF_ORIGIN_INDEX_SQL
    .replace('CREATE INDEX', 'CREATE INDEX IF NOT EXISTS')};`);
  for (const sql of INTERPRETATION_HANDOFF_TRIGGER_SQL.values()) {
    db.exec(`${sql.replace('CREATE TRIGGER', 'CREATE TRIGGER IF NOT EXISTS')};`);
  }
}

function initializeInterpretationHandoffRegistry() {
  try {
    db.transaction(() => {
      let columns = interpretationHandoffColumnRows();
      const shape = recognizedInterpretationHandoffColumns(columns);
      if (!shape) registrySchemaFailure('kolommen of nullability');
      const table = db.prepare(`SELECT sql FROM sqlite_master
        WHERE type='table' AND name=?`).get(INTERPRETATION_HANDOFF_TABLE);
      const tableFlags = db.prepare(`SELECT wr,strict FROM pragma_table_list
        WHERE schema='main' AND name=?`).get(INTERPRETATION_HANDOFF_TABLE);
      const foreignKeys = db.prepare(
        `PRAGMA foreign_key_list(${INTERPRETATION_HANDOFF_TABLE})`,
      ).all();
      if (!table?.sql || Number(tableFlags?.wr) !== 0 || Number(tableFlags?.strict) !== 0
        || foreignKeys.length) registrySchemaFailure('tabelvorm');
      verifyInterpretationHandoffIndexes(true);
      verifyInterpretationHandoffTriggers(true);
      if (shape === 'missing_raw') {
        // De ruwe tekst zelf is niet reconstrueerbaar. De lege sentinel maakt
        // een oude rij onbruikbaar, terwijl nieuwe rijen de exacte hash eisen.
        db.exec(`ALTER TABLE ${INTERPRETATION_HANDOFF_TABLE}
          ADD COLUMN raw_original_text_sha256 TEXT NOT NULL DEFAULT ''`);
        columns = interpretationHandoffColumnRows();
      }
      const currentTableSql = db.prepare(`SELECT sql FROM sqlite_master
        WHERE type='table' AND name=?`).get(INTERPRETATION_HANDOFF_TABLE)?.sql;
      if (!exactInterpretationHandoffColumns(columns)
        || normalizedRegistrySql(currentTableSql)
          !== normalizedRegistrySql(INTERPRETATION_HANDOFF_TABLE_SQL)) {
        recreateInterpretationHandoffTable();
      }
      installInterpretationHandoffSchemaObjects();
      const finalTableSql = db.prepare(`SELECT sql FROM sqlite_master
        WHERE type='table' AND name=?`).get(INTERPRETATION_HANDOFF_TABLE)?.sql;
      if (!exactInterpretationHandoffColumns(interpretationHandoffColumnRows())
        || normalizedRegistrySql(finalTableSql)
          !== normalizedRegistrySql(INTERPRETATION_HANDOFF_TABLE_SQL)) {
        registrySchemaFailure('eindprojectie');
      }
      verifyInterpretationHandoffIndexes(false);
      verifyInterpretationHandoffTriggers(false);
    }).immediate();
    return Object.freeze({ ready: true, error: '' });
  } catch (error) {
    return Object.freeze({ ready: false, error: errMsg(error).slice(0, 500) });
  }
}

const interpretationHandoffRegistry = initializeInterpretationHandoffRegistry();

function assertInterpretationHandoffRegistryReady() {
  if (!interpretationHandoffRegistry.ready) {
    throw new Error('ChatInterpretationRetry/v1-registryschema is niet exact; handoff geweigerd');
  }
}

function verifyCurrentInterpretationHandoffRegistrySchema() {
  assertInterpretationHandoffRegistryReady();
  const table = db.prepare(`SELECT sql FROM sqlite_master
    WHERE type='table' AND name=?`).get(INTERPRETATION_HANDOFF_TABLE);
  const tableFlags = db.prepare(`SELECT wr,strict FROM pragma_table_list
    WHERE schema='main' AND name=?`).get(INTERPRETATION_HANDOFF_TABLE);
  const foreignKeys = db.prepare(
    `PRAGMA foreign_key_list(${INTERPRETATION_HANDOFF_TABLE})`,
  ).all();
  if (!table?.sql || Number(tableFlags?.wr) !== 0 || Number(tableFlags?.strict) !== 0
    || foreignKeys.length
    || !exactInterpretationHandoffColumns(interpretationHandoffColumnRows())
    || normalizedRegistrySql(table.sql)
      !== normalizedRegistrySql(INTERPRETATION_HANDOFF_TABLE_SQL)) {
    registrySchemaFailure('actuele tabelvorm');
  }
  verifyInterpretationHandoffIndexes(false);
  verifyInterpretationHandoffTriggers(false);
}

// Een oudere ontvangstbevestiging kan horen bij een tussentijds antwoord. De
// eindaflevering krijgt haar betekenis pas via de bijbehorende closed-event;
// daarom mogen meerdere pogingen bestaan, ieder met een eigen idempotency key.
// De index uit de eerste versie verbood juist die noodzakelijke verse poging.
db.exec('DROP INDEX IF EXISTS idx_eva_delivery_one_final');

try {
  const headColumns = new Set(
    db.prepare('PRAGMA table_info(eva_execution_intent_heads)').all()
      .map((column) => /** @type {any} */ (column).name),
  );
  if (!headColumns.has('work_item_id')) {
    db.exec('ALTER TABLE eva_execution_intent_heads ADD COLUMN work_item_id INTEGER');
  }
  if (!headColumns.has('active_effect_key')) {
    db.exec('ALTER TABLE eva_execution_intent_heads ADD COLUMN active_effect_key TEXT');
  }
} catch { /* nieuwe durable-tabellen worden bij de volgende start opnieuw gecontroleerd */ }

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
  delivery_pending: Object.freeze([
    'delivery_pending', 'waiting', 'blocked', 'delivery_recorded',
    'closed', 'abandoned', 'recovering',
  ]),
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

function exactExplorationEconomicHandoff(value) {
  const fields = [
    'authority', 'commitment', 'contract', 'evidenceSha256', 'explorationId',
    'fromRevision', 'goalKey', 'sourceOutcomeKey', 'toRevision',
  ];
  const authorityFields = [
    'externalContact', 'familyDelivery', 'internalWork', 'publication', 'spend',
  ];
  const commitmentFields = ['definitionOfDone', 'nextAction', 'outcome', 'title'];
  const commitment = value?.commitment;
  const authority = value?.authority;
  const exactShape = value && typeof value === 'object' && !Array.isArray(value)
    && canonicalJson(Object.keys(value).sort()) === canonicalJson(fields)
    && authority && typeof authority === 'object' && !Array.isArray(authority)
    && canonicalJson(Object.keys(authority).sort()) === canonicalJson(authorityFields)
    && commitment && typeof commitment === 'object' && !Array.isArray(commitment)
    && canonicalJson(Object.keys(commitment).sort()) === canonicalJson(commitmentFields);
  const normalized = exactShape ? {
    title: clean(commitment.title, 240),
    outcome: clean(commitment.outcome, 1_200),
    definitionOfDone: clean(commitment.definitionOfDone, 1_200),
    nextAction: clean(commitment.nextAction, 600),
  } : null;
  const exact = exactShape
    && value.contract === EXPLORATION_ECONOMIC_HANDOFF_CONTRACT
    && value.goalKey === EXPLORATION_ECONOMIC_GOAL_KEY
    && canonicalJson(authority) === canonicalJson({
      internalWork: true, familyDelivery: true,
      externalContact: false, publication: false, spend: false,
    })
    && /^[a-f0-9]{64}$/u.test(String(value.sourceOutcomeKey || ''))
    && /^[a-f0-9]{64}$/u.test(String(value.evidenceSha256 || ''))
    && Number.isSafeInteger(Number(value.explorationId))
    && Number(value.explorationId) >= 1
    && Number.isSafeInteger(Number(value.fromRevision))
    && Number(value.fromRevision) >= 1
    && Number(value.toRevision) === Number(value.fromRevision) + 1
    && Object.values(normalized).every((part) => part.length >= 4)
    && canonicalJson(normalized) === canonicalJson(commitment);
  if (!exact) throw new Error('ongeldige ExplorationEconomicHandoff/v1');
  return Object.freeze({
    ...value,
    explorationId: Number(value.explorationId),
    fromRevision: Number(value.fromRevision),
    toRevision: Number(value.toRevision),
    commitment: Object.freeze(normalized),
  });
}

function explorationEconomicEpisodeKey(sourceOutcomeKey) {
  return `exploration-economic:${sourceOutcomeKey}`;
}

function exactExplorationTerminalOutcomeClaim(precommit, exploration, handoff, value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort())
      !== canonicalJson(['content', 'meta', 'outcomeKey'])) return false;
  const evidence = json(exploration?.evidence_json, []);
  const expectedSummary = clean(redactSensitiveText(precommit?.evidenceSummary), 900);
  const prior = Array.isArray(evidence) ? [...evidence].reverse().find((row) => (
    row?.ok === true && row?.verified === true
    && Number.isSafeInteger(Number(row?.episode_id)) && Number(row.episode_id) >= 1
    && clean(row?.tool, 80) && clean(row?.tool, 80) !== 'verkenning_bijhouden'
    && clean(redactSensitiveText(row?.evidence), 900) === expectedSummary
  )) : null;
  const aspirationId = exploration?.aspiration_id == null
    ? null : Number(exploration.aspiration_id);
  const expectedMeta = {
    internalOnly: true,
    source: 'bijdrage',
    owner: null,
    evidence: String(exploration?.result || ''),
    lastAction: clean(prior?.tool, 80),
    status: 'done',
    syntheticTest: false,
    lifecycleContract: 'ExplorationTerminalContribution/v1',
    understandingSha256: String(precommit?.understanding?.sha256 || ''),
    requirementId: String(precommit?.action?.requirementId || ''),
    explorationId: Number(precommit?.explorationId),
    aspirationId,
    fromRevision: Number(precommit?.fromRevision),
    toRevision: Number(precommit?.toRevision),
    terminalStatus: 'completed',
    economicHandoff: handoff,
    outcomeKey: handoff.sourceOutcomeKey,
  };
  const expectedContent = clean(`Zelfstandig afgerond: ${exploration?.result || ''}`, 500);
  return Boolean(prior && expectedSummary.length >= 8
    && value.outcomeKey === handoff.sourceOutcomeKey
    && value.content === expectedContent
    && canonicalJson(value.meta) === canonicalJson(expectedMeta));
}

function exactStoredExplorationEconomicHandoff(episode, handoff) {
  const work = workRow(episode?.work_item_id);
  const state = json(work?.state_json, {});
  const linkage = episode ? /** @type {any} */ (db.prepare(`SELECT e.goal_id AS episode_goal_id,
      p.goal_id AS project_goal_id,g.id AS goal_id,g.goal_key
    FROM eva_goal_episodes e
    JOIN eva_projects p ON p.id=e.project_id
    JOIN eva_goals g ON g.id=e.goal_id
    WHERE e.id=?`).get(episode.id)) : null;
  const origins = episode ? /** @type {any[]} */ (db.prepare(`SELECT
      origin_key,origin_kind,origin_channel,requester
    FROM eva_episode_origins WHERE episode_id=? ORDER BY id ASC`).all(episode.id)) : [];
  const origin = origins[0] || null;
  const events = episode ? /** @type {any[]} */ (db.prepare(`SELECT kind,payload_json FROM eva_episode_events
    WHERE episode_id=? AND kind IN ('committed','work_bound') ORDER BY id ASC`).all(
    episode.id,
  )) : [];
  let committed = null, workBound = null;
  try {
    committed = JSON.parse(String(events.find((event) => event.kind === 'committed')?.payload_json || 'null'));
    workBound = JSON.parse(String(events.find((event) => event.kind === 'work_bound')?.payload_json || 'null'));
  } catch { /* ontbrekende of beschadigde provenance faalt hieronder gesloten */ }
  const exact = [episode, work,
    episode?.episode_key === explorationEconomicEpisodeKey(handoff.sourceOutcomeKey),
    episode?.goal_key === EXPLORATION_ECONOMIC_GOAL_KEY,
    linkage?.episode_goal_id === linkage?.project_goal_id,
    linkage?.episode_goal_id === linkage?.goal_id,
    linkage?.goal_key === EXPLORATION_ECONOMIC_GOAL_KEY,
    episode?.origin_kind === 'self', episode?.origin_channel === 'autonomy',
    episode?.requester == null, episode?.delivery_policy === 'milestone',
    episode?.delivery_target == null, origins.length === 1,
    origin?.origin_key === `episode:${episode?.id}|channel:autonomy|conversation:|message:`,
    origin?.origin_kind === 'self', origin?.origin_channel === 'autonomy',
    origin?.requester == null, events.length === 2,
    committed?.lifecycleContract === EXPLORATION_ECONOMIC_HANDOFF_CONTRACT,
    committed?.originKind === 'self', committed?.originChannel === 'autonomy',
    committed?.deliveryPolicy === 'milestone',
    Number(workBound?.workItemId) === Number(work?.id),
    work?.source === 'autonomy', work?.owner == null,
    canonicalJson(state?.exploration_economic_handoff) === canonicalJson(handoff),
  ].every(Boolean);
  return exact ? { workItem: work, episode } : null;
}

/**
 * Materialiseert uitsluitend Eva's expliciete vierdelige vervolgcommitment.
 * De caller bezit de outer transaction; deze helper emit daarom geen events.
 */
export function materializeExplorationEconomicHandoff(
  mutationAuthority, precommit, value, outcomeMutationAuthority, outcomeClaim,
) {
  const handoff = exactExplorationEconomicHandoff(value);
  const args = precommit?.action?.semanticInput;
  if (![issuedExplorationTerminalMutationMatches(mutationAuthority, precommit),
    db.inTransaction === true, outcomeClaim,
    outcomeClaim?.outcomeKey === handoff.sourceOutcomeKey,
  ].every(Boolean)) {
    throw new Error('ExplorationEconomicHandoff/v1 mist atomische issued authority');
  }
  const currentExploration = /** @type {any} */ (db.prepare(`SELECT
      id,source,status,revision,result,decision,aspiration_id
    FROM eva_explorations WHERE id=?`).get(handoff.explorationId));
  const expectedCommitment = args?.economisch_vervolg ? {
    title: clean(redactSensitiveText(args.economisch_vervolg.titel), 240),
    outcome: clean(redactSensitiveText(args.economisch_vervolg.uitkomst), 1_200),
    definitionOfDone: clean(redactSensitiveText(args.economisch_vervolg.definitie_klaar), 1_200),
    nextAction: clean(redactSensitiveText(args.economisch_vervolg.volgende_stap), 600),
  } : null;
  const exactTransition = [currentExploration,
    currentExploration?.source === 'self', currentExploration?.status === 'completed',
    Number(currentExploration?.revision) === handoff.toRevision,
    Number(precommit?.explorationId) === handoff.explorationId,
    Number(precommit?.fromRevision) === handoff.fromRevision,
    Number(precommit?.toRevision) === handoff.toRevision,
    precommit?.outcomeKey === handoff.sourceOutcomeKey,
    (currentExploration?.aspiration_id == null
      ? null : Number(currentExploration.aspiration_id)) === precommit?.snapshot?.aspirationId,
    canonicalJson(expectedCommitment) === canonicalJson(handoff.commitment),
    Number(args?.id) === handoff.explorationId,
    Number(args?.verwachte_revisie) === handoff.fromRevision,
    clean(redactSensitiveText(args?.resultaat), 2_000) === String(currentExploration?.result || ''),
    (clean(redactSensitiveText(args?.besluit), 2_000) || null)
      === (currentExploration?.decision ?? null),
    createHash('sha256').update(
      clean(redactSensitiveText(precommit?.evidenceSummary), 900),
    ).digest('hex') === handoff.evidenceSha256,
  ].every(Boolean);
  if (!exactTransition) {
    throw new Error('ExplorationEconomicHandoff/v1 mist de exact voltooide transition');
  }
  const currentExplorationWithEvidence = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(handoff.explorationId);
  if (!exactExplorationTerminalOutcomeClaim(
    precommit, currentExplorationWithEvidence, handoff, outcomeClaim,
  )) {
    throw new Error('ExplorationEconomicHandoff/v1 mist haar exacte contribution-outcome');
  }
  // Geen root-fallback: de economische richting moet vóór iedere insert exact bestaan.
  const goal = exactGoalByKey(EXPLORATION_ECONOMIC_GOAL_KEY);
  if (!goal || goal.goal_key !== EXPLORATION_ECONOMIC_GOAL_KEY || goal.status !== 'active') {
    throw new Error('economic_value is niet exact actief; handoff geweigerd');
  }
  const episodeKey = explorationEconomicEpisodeKey(handoff.sourceOutcomeKey);
  const existingRow = /** @type {any} */ (db.prepare(
    'SELECT id FROM eva_goal_episodes WHERE episode_key=?',
  ).get(episodeKey));
  if (existingRow) {
    const exact = exactStoredExplorationEconomicHandoff(
      episodeRow(existingRow.id), handoff,
    );
    if (!exact) throw new Error('conflicterende ExplorationEconomicHandoff/v1 replay');
    throw new Error('orphan ExplorationEconomicHandoff/v1 vóór outcome');
  }

  // Consumeer de callback-lokale outcome-capability vóór de eerste handoffinsert.
  // Iedere latere fout rolt zowel deze inserts als de outcome volledig terug.
  if (!consumeContributionMutationAuthority(outcomeMutationAuthority, outcomeClaim)) {
    throw new Error('ExplorationEconomicHandoff/v1 mist haar exacte outcome-transactie');
  }

  const ts = nowIso();
  const commitment = handoff.commitment;
  const workInsert = db.prepare(`
    INSERT INTO eva_work_items (
      created_ts,updated_ts,source,owner,title,goal,next_step,status,
      visible_to_family,definition_of_done,current_step,state_json
    ) VALUES (?,?,'autonomy',NULL,?,?,?,'open',1,?,?,?)
  `).run(
    ts, ts, commitment.title, commitment.outcome, commitment.nextAction,
    commitment.definitionOfDone, commitment.nextAction,
    JSON.stringify({ exploration_economic_handoff: handoff }),
  );
  const workItemId = Number(workInsert.lastInsertRowid);
  const projectInsert = db.prepare(`
    INSERT INTO eva_projects (
      goal_id,title,outcome,definition_of_done,phase,status,
      current_milestone,next_action,created_ts,updated_ts
    ) VALUES (?,?,?,?,'discover','active',?,?,?,?)
  `).run(
    goal.id, commitment.title, commitment.outcome, commitment.definitionOfDone,
    commitment.outcome, commitment.nextAction, ts, ts,
  );
  const episodeInsert = db.prepare(`
    INSERT INTO eva_goal_episodes (
      episode_key,goal_id,project_id,work_item_id,origin_kind,origin_channel,
      requester,requested_outcome,delivery_policy,delivery_target,state,
      next_action,created_ts,updated_ts
    ) VALUES (?,?,?,?,'self','autonomy',NULL,?,'milestone',NULL,'committed',?,?,?)
  `).run(
    episodeKey, goal.id, Number(projectInsert.lastInsertRowid), workItemId,
    commitment.outcome, commitment.nextAction, ts, ts,
  );
  const episodeId = Number(episodeInsert.lastInsertRowid);
  db.prepare(`
    INSERT INTO eva_episode_origins (
      episode_id,origin_key,origin_kind,origin_channel,requester,attached_ts
    ) VALUES (?,?,'self','autonomy',NULL,?)
  `).run(episodeId, `episode:${episodeId}|channel:autonomy|conversation:|message:`, ts);
  addEvent(episodeId, 'committed', {
    originKind: 'self', originChannel: 'autonomy', deliveryPolicy: 'milestone',
    lifecycleContract: EXPLORATION_ECONOMIC_HANDOFF_CONTRACT,
  });
  addEvent(episodeId, 'work_bound', { workItemId });
  const exact = exactStoredExplorationEconomicHandoff(episodeRow(episodeId), handoff);
  if (!exact) throw new Error('ExplorationEconomicHandoff/v1 kon niet exact worden opgeslagen');
  return { ...exact, handoff, created: true };
}

/** Exacte replay is strikt read-only en kan nooit ontbrekend werk aanmaken. */
export function replayExplorationEconomicHandoff(value) {
  const handoff = exactExplorationEconomicHandoff(value);
  const existing = db.prepare(
    'SELECT id FROM eva_goal_episodes WHERE episode_key=?',
  ).get(explorationEconomicEpisodeKey(handoff.sourceOutcomeKey));
  const exact = existing
    ? exactStoredExplorationEconomicHandoff(episodeRow(existing.id), handoff) : null;
  if (!exact) throw new Error('ExplorationEconomicHandoff/v1 replay mist exact duurzaam werk');
  return { ...exact, handoff, created: false };
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
      revision: Number(goal.revision || 0) + 1,
      claimed_until: claimedUntil,
      text: `Herbekijk je zelfgekozen doel "${goal.title}": ${goal.intent}. Kies hoogstens één doelbewerking: bijstellen met een nieuw reviewmoment op of na ${claimedUntil}, pauzeren, afronden of verlaten.`,
    };
  })();
}

function createSelfGoal({
  title, intent, why, beneficiary, horizon, successMetrics, priority, source, nextReviewTs,
}) {
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

function updateSelfGoal({
  action, goalKey, title, intent, why, beneficiary, horizon, successMetrics,
  priority, nextReviewTs, expectedRevision, expectedGoalId,
}) {
  const current = exactGoalByKey(goalKey);
  if (!current || current.source !== 'self' || current.goal_key !== clean(goalKey, 120)) {
    return { ok: false, changed: false, reason: 'zelfgekozen doel niet gevonden; gedeelde huisrichtingen zijn niet via deze tool wijzigbaar' };
  }
  const expected = expectedRevision == null ? null : Number(expectedRevision);
  const expectedId = expectedGoalId == null ? null : Number(expectedGoalId);
  const revisionBound = expectedRevision != null;
  const privatelyAttested = expectedGoalId != null;
  if ((privatelyAttested && !revisionBound)
    || (revisionBound && (!Number.isSafeInteger(expected) || expected < 0
      || Number(current.revision || 0) !== expected))
    || (privatelyAttested && (!Number.isSafeInteger(expectedId) || expectedId < 1
      || Number(current.id) !== expectedId))) {
    return { ok: false, changed: false,
      reason: 'het zelfgekozen doel veranderde sinds de geattesteerde reviewselectie' };
  }
  const states = { update: current.status, pause: 'paused', resume: 'active', complete: 'completed', abandon: 'abandoned' };
  if (!(action in states)) return { ok: false, changed: false, reason: `onbekende doelbewerking: ${action}` };
  const metrics = Array.isArray(successMetrics)
    ? successMetrics.map((value) => clean(value, 300)).filter(Boolean).slice(0, 12)
    : json(current.success_metrics_json, []);
  const changed = db.prepare(`UPDATE eva_goals SET title=?,intent=?,why=?,beneficiary=?,horizon=?,
    success_metrics_json=?,status=?,priority=?,next_review_ts=?,updated_ts=?,revision=revision+1
    WHERE id=?${revisionBound
    ? " AND revision=? AND goal_key=? AND source='self' AND status='active'" : ''}`).run(
    clean(title || current.title, 300), clean(intent || current.intent, 1_500),
    clean(why || current.why, 1_500), clean(beneficiary || current.beneficiary, 300),
    clean(horizon || current.horizon, 200), JSON.stringify(metrics), states[action],
    priority == null ? current.priority : Math.max(0, Math.min(1, Number(priority) || 0.5)),
    nextReviewTs == null ? current.next_review_ts
      : Number.isFinite(Date.parse(nextReviewTs || '')) ? new Date(nextReviewTs).toISOString() : null,
    nowIso(), privatelyAttested ? expectedId : current.id,
    ...(revisionBound ? [expected, current.goal_key] : []),
  ).changes;
  if (changed !== 1) return { ok: false, changed: false,
    reason: 'het zelfgekozen doel veranderde sinds de geattesteerde reviewselectie' };
  return { ok: true, changed: true, goal: goalByKey(current.goal_key) };
}

export function manageGoal({
  operation = 'list', goalKey = null, title = null, intent = null, why = null,
  beneficiary = null, horizon = null, successMetrics = null, priority = null,
  source = 'self', nextReviewTs = null, expectedRevision = null, expectedGoalId = null,
} = {}) {
  const action = clean(operation, 30).toLowerCase();
  if (action === 'list') return { ok: true, changed: false, goals: listGoals() };
  const values = {
    title, intent, why, beneficiary, horizon, successMetrics, priority, nextReviewTs,
  };
  return action === 'create'
    ? createSelfGoal({ ...values, source })
    : updateSelfGoal({ ...values, action, goalKey, expectedRevision, expectedGoalId });
}

/** @returns {any} */
function episodeRow(id) {
  return db.prepare(`
    SELECT e.*, p.title AS project_title, p.outcome AS project_outcome,
      p.definition_of_done AS project_definition_of_done,
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
  const nextStep = requested.nextStep == null ? current.next_step : clean(requested.nextStep, 600);
  const cleanedEvidence = requested.evidence == null ? '' : clean(requested.evidence, 2_000);
  let requestedStateJson = null;
  if (requested.state != null) {
    requestedStateJson = JSON.stringify(requested.state);
    if (requestedStateJson.length > WORK_STATE_JSON_LIMIT) {
      throw new Error(`werkstaat overschrijdt ${WORK_STATE_JSON_LIMIT} tekens`);
    }
  }
  const proposedValues = {
    status,
    next_step: nextStep,
    evidence: requested.evidence == null || !cleanedEvidence ? current.evidence : cleanedEvidence,
    last_action: requested.lastAction == null ? current.last_action : clean(requested.lastAction, 120),
    result: requested.result == null ? current.result : clean(requested.result, 2_000),
    current_step: requested.currentStep == null
      ? (requested.nextStep == null ? current.current_step : nextStep)
      : clean(requested.currentStep, 600),
    state_json: requested.state == null
      ? current.state_json
      : requestedStateJson,
    wait_json: requested.clearWait
      ? null
      : requested.wait == null
        ? current.wait_json
        : JSON.stringify(requested.wait).slice(0, 4_000),
    next_wake_ts: requested.nextWakeTs == null
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
  };
  return {
    current,
    values: {
      ...proposedValues,
      ...terminalWorkProjection(proposedValues, status),
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
      SELECT meta FROM main.timeline
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
  const storedOriginKind = clean(originKind, 40);
  const policy = deliveryPolicy || (storedOriginKind === 'user' ? 'required' : 'milestone');
  // Een gebruiker kan monitoring uitdrukkelijk zonder terugmelding vragen.
  // Zo'n niet-required gebruikersepisode mag niet via de requester-fallback
  // alsnog ongemerkt een afleverdoel krijgen.
  const storedDeliveryTarget = storedOriginKind === 'user' && policy !== 'required'
    ? null
    : clean(deliveryTarget || (policy === 'required' ? requester : null), 160) || null;
  if (storedOriginKind === 'self' && policy === 'required' && !storedDeliveryTarget) {
    throw new Error('zelfgekozen verplichte aflevering vereist een expliciet afleverdoel');
  }
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
    episodeKey, goal.id, Number(project.lastInsertRowid), storedOriginKind,
    clean(originChannel, 40) || null, clean(originConversationId, 240) || null,
    clean(originMessageId, 160) || null, clean(requester, 120) || null,
    cleanOutcome, clean(policy, 40), storedDeliveryTarget,
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
  deliveryPolicy = null,
  deliveryTarget = undefined,
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
      if (deliveryPolicy === 'required') {
        const target = clean(deliveryTarget, 160) || existing.delivery_target;
        if (existing.delivery_policy === 'required' && existing.delivery_target
          && target && existing.delivery_target !== target) {
          throw new Error(`episode ${existing.id} heeft al afleverdoel ${existing.delivery_target}`);
        }
        if (existing.delivery_policy !== 'required' || existing.delivery_target !== target) {
          return transitionGoalEpisode(existing.id, {
            state: existing.state,
            deliveryPolicy: 'required',
            deliveryTarget: target,
          }).episode;
        }
      }
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
      deliveryPolicy: deliveryPolicy || (inferredOrigin === 'user' ? 'required' : 'milestone'),
      deliveryTarget: deliveryTarget === undefined ? person : deliveryTarget,
      nextAction: item.next_step || item.current_step || '',
    });
    return bindEpisodeToWork(episode.id, item.id);
  })();
}

export function createDurableWork(item, origin = {}, options = {}) {
  return db.transaction(() => {
    const workItem = createWorkItem(item);
    const episode = ensureEpisodeForWorkItem(workItem, origin);
    const monitoringStart = options?.monitoringStart;
    if (!monitoringStart) return { workItem, episode };
    const monitoring = normalizeMonitoringContract(monitoringStart.monitoring);
    if (!monitoring || !episode || Number(episode.work_item_id) !== Number(workItem.id)) {
      throw new Error('monitoringstart mist haar exacte contract- of werkbinding');
    }
    const required = monitoring.delivery.deliveryRequired === true;
    const startAuthority = monitoringStart.startAuthority;
    const deliveryOrigin = monitoringStart.deliveryOrigin;
    let startRow;
    let durableStart;
    let startArgs;
    try {
      startRow = validateExecutionRecord(db.prepare(`SELECT * FROM main.turn_tool_executions
        WHERE execution_key=?`).get(String(startAuthority?.executionKey || '')));
      durableStart = turnUnderstandingAuthorityRecord(db, startRow);
      startArgs = JSON.parse(String(startRow?.args || 'null'));
    } catch { /* fail closed below */ }
    if (!durableStart || startRow.status !== 'started' || startRow.tool !== '@turn/understanding'
      || durableStart.executionKey !== startAuthority?.executionKey
      || Number(durableStart.executionSeq) !== Number(startAuthority?.executionSeq)
      || durableStart.intentRecordSha256 !== startAuthority?.intentRecordSha256
      || durableStart.authoritySha256 !== startAuthority?.authoritySha256
      || durableStart.channel !== startAuthority?.channel
      || durableStart.conversationId !== startAuthority?.conversationId
      || String(durableStart.messageId || '') !== String(startAuthority?.messageId || '')
      || durableStart.sourceKind !== startAuthority?.sourceKind
      || durableStart.sourceRef !== startAuthority?.sourceRef
      || durableStart.sourceSha256 !== startAuthority?.sourceSha256
      || Number(durableStart.sourceGeneration) !== Number(startAuthority?.sourceGeneration)
      || startArgs?.channel !== startAuthority?.channel
      || startArgs?.conversationId !== startAuthority?.conversationId
      || String(startArgs?.messageId || '') !== String(startAuthority?.messageId || '')
      || createHash('sha256').update(String(startArgs?.originalText || '')).digest('hex')
        !== monitoring.presentation.sourceTextSha256
      || startArgs?.syntheticTest === true) {
      throw new Error('monitoringstart mist haar store-owned understanding-authority');
    }
    const origins = /** @type {any[]} */ (db.prepare(`SELECT * FROM eva_episode_origins
      WHERE episode_id=? ORDER BY id ASC`).all(Number(episode.id)));
    const lifecycleOrigin = origins[0];
    if (origins.length !== 1
      || episode.origin_channel !== startAuthority.channel
      || episode.origin_conversation_id !== startAuthority.conversationId
      || String(episode.origin_message_id || '') !== String(startAuthority.messageId)
      || lifecycleOrigin.origin_channel !== startAuthority.channel
      || lifecycleOrigin.origin_conversation_id !== startAuthority.conversationId
      || String(lifecycleOrigin.origin_message_id || '') !== String(startAuthority.messageId)) {
      throw new Error('monitoringepisode mist precies één authentieke oorsprong');
    }
    if (required) {
      const expected = deliveryOrigin;
      if (!expected) {
        throw new Error('monitoringepisode mist haar verzegelde Telegramroute');
      }
      if (episode.origin_kind !== 'user'
        || episode.origin_channel !== 'telegram'
        || episode.origin_conversation_id !== expected.conversationId
        || String(episode.origin_message_id || '') !== String(expected.messageId)
        || episode.delivery_policy !== 'required'
        || origins[0].origin_kind !== 'user'
        || origins[0].origin_channel !== 'telegram'
        || origins[0].origin_conversation_id !== expected.conversationId
        || String(origins[0].origin_message_id || '') !== String(expected.messageId)) {
        throw new Error('monitoringepisode mist precies één oorspronkelijke Telegramroute');
      }
      const archived = requiredTelegramOriginBinding(
        expected.conversationId, expected.messageId,
      );
      if (archived.archiveRowId !== expected.archiveRowId
        || archived.originPrincipal !== expected.principal
        || archived.messageThreadId !== expected.messageThreadId) {
        throw new Error('monitoringepisode verloor haar oorspronkelijke Telegramprincipal');
      }
    }
    const lifecycle = issueMonitoringLifecycleBinding({
      monitoring, episodeId: Number(episode.id), episodeKey: String(episode.episode_key),
      issuedEpisodeRevision: Number(episode.revision || 0), workId: Number(workItem.id),
      originRowId: Number(lifecycleOrigin.id),
      originKey: String(lifecycleOrigin.origin_key),
      startAuthority,
      deliveryOrigin,
      generation: 1,
    });
    const {
      monitoring_start_authority: _consumedStartAuthority,
      monitoring_start_delivery_origin: _consumedDeliveryOrigin,
      ...initialState
    } = monitoringStart.state && typeof monitoringStart.state === 'object'
      ? monitoringStart.state : {};
    const state = {
      ...initialState,
      monitoring,
      monitoring_lifecycle: lifecycle,
    };
    if (!lifecycle || !monitoringStateFitsDurableBudget(state)) {
      throw new Error('monitoringstart past niet volledig in de duurzame stategrens');
    }
    const wakeTs = clean(monitoringStart.wakeTs, 100) || null;
    const transitioned = transitionGoalEpisodeInTransaction(episode.id, {
      state: wakeTs ? 'waiting' : episode.state,
      wakeTs,
      nextAction: monitoringStart.nextAction || workItem.next_step || '',
      sessionRef: monitoringStart.sessionRef || null,
      eventKind: 'monitoring_lifecycle_bound',
      idempotencyKey: `monitoring-lifecycle:${monitoring.monitoringId}`,
      work: {
        state,
        currentStep: monitoringStart.currentStep,
        nextWakeTs: wakeTs,
        wait: wakeTs ? { reason: 'monitoring_resume', resumeAfter: wakeTs } : null,
      },
    });
    return { workItem: transitioned.workItem, episode: transitioned.episode, lifecycle };
  }).immediate();
}

function interpretationWorkState(item) {
  return json(item?.state_json, {}) || {};
}

function interpretationRetryHash(value) {
  return createHash('sha256').update(String(value ?? ''), 'utf8').digest('hex');
}

function interpretationRetryOrigin(episode) {
  return {
    channel: String(episode?.origin_channel || ''),
    conversationId: String(episode?.origin_conversation_id || ''),
    messageId: String(episode?.origin_message_id || ''),
    requester: String(episode?.requester || ''),
    deliveryPolicy: String(episode?.delivery_policy || ''),
    deliveryTarget: String(episode?.delivery_target || ''),
  };
}

function interpretationRetryOriginHash(episode) {
  return interpretationRetryHash(JSON.stringify(interpretationRetryOrigin(episode)));
}

function interpretationRetryRecordValid({
  raw, episode, originalText, createdMs, deadlineMs, attempts, phase,
  planSha256, understandingSha256, semanticAuthoritySha256,
  materialAuthoritySha256, supersessionScope, supersessionToken,
  supersessionSha256, supersessionBound,
}) {
  return raw.contract === INTERPRETATION_RETRY_CONTRACT
    && originalText.trim().length > 0
    && interpretationRetryHash(originalText) === raw.originalTextSha256
    && interpretationRetryOriginHash(episode) === raw.originSha256
    && Number.isFinite(createdMs) && Number.isFinite(deadlineMs)
    && deadlineMs === createdMs + INTERPRETATION_RETRY_TTL_MS
    && Number.isInteger(attempts) && attempts >= 0
    && ['pending', 'understood'].includes(phase)
    && (phase === 'pending'
      ? !planSha256 && !understandingSha256 && !semanticAuthoritySha256
        && !materialAuthoritySha256
      : SHA256.test(planSha256) && (!understandingSha256 || SHA256.test(understandingSha256))
        && (!semanticAuthoritySha256 || SHA256.test(semanticAuthoritySha256))
        && (!materialAuthoritySha256 || SHA256.test(materialAuthoritySha256)))
    && (!supersessionBound || (supersessionScope.length > 0 && supersessionToken.length > 0
      && interpretationRetryHash(`${supersessionScope}\n${supersessionToken}`)
        === supersessionSha256));
}

function interpretationRetryRecord(item, episode) {
  const raw = interpretationWorkState(item).interpretation_retry;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const originalText = typeof raw.originalText === 'string' ? raw.originalText : '';
  const createdMs = Date.parse(String(raw.createdAt || ''));
  const deadlineMs = Date.parse(String(raw.deadlineAt || ''));
  const attempts = Number(raw.attempts);
  const phase = raw.phase == null ? 'pending' : String(raw.phase);
  const planSha256 = String(raw.planSha256 || '');
  const understandingSha256 = String(raw.understandingSha256 || '');
  const semanticAuthoritySha256 = String(raw.semanticAuthoritySha256 || '');
  const materialAuthoritySha256 = String(raw.materialAuthoritySha256 || '');
  const supersessionScope = String(raw.supersessionScope || '');
  const supersessionToken = String(raw.supersessionToken || '');
  const supersessionSha256 = String(raw.supersessionSha256 || '');
  const supersessionBound = Boolean(supersessionScope || supersessionToken || supersessionSha256);
  const valid = interpretationRetryRecordValid({
    raw, episode, originalText, createdMs, deadlineMs, attempts, phase,
    planSha256, understandingSha256, semanticAuthoritySha256,
    materialAuthoritySha256, supersessionScope, supersessionToken,
    supersessionSha256, supersessionBound,
  });
  return Object.freeze({
    valid,
    originalText: valid ? originalText : '',
    originalTextSha256: valid ? String(raw.originalTextSha256) : '',
    rawOriginalTextSha256: valid && SHA256.test(String(raw.rawOriginalTextSha256 || ''))
      ? String(raw.rawOriginalTextSha256) : '',
    handoffKey: valid && SHA256.test(String(raw.handoffKey || ''))
      ? String(raw.handoffKey) : '',
    createdAt: valid ? new Date(createdMs).toISOString() : null,
    deadlineAt: valid ? new Date(deadlineMs).toISOString() : null,
    attempts: valid ? attempts : INTERPRETATION_RETRY_BACKOFF_MS.length,
    origin: interpretationRetryOrigin(episode),
    failureClass: valid ? String(raw.lastFailureClass || '') : '',
    phase: valid ? phase : 'invalid',
    planSha256: valid ? planSha256 : '',
    understandingSha256: valid ? understandingSha256 : '',
    semanticAuthoritySha256: valid ? semanticAuthoritySha256 : '',
    materialAuthoritySha256: valid ? materialAuthoritySha256 : '',
    supersessionScope: valid ? supersessionScope : '',
    supersessionToken: valid ? supersessionToken : '',
  });
}

function telegramRetrySupersessionArchiveBinding(episode) {
  const origin = interpretationRetryOrigin(episode);
  const chatId = origin.conversationId.match(/^telegram:(.+)$/u)?.[1] || '';
  const inbound = chatId && origin.messageId
    ? telegramMessage(chatId, origin.messageId) : null;
  const meta = inbound?.meta && typeof inbound.meta === 'object' ? inbound.meta : {};
  const fromTelegramId = String(meta.fromTelegramId || '');
  const rawThread = meta.messageThreadId;
  const messageThreadId = rawThread == null || rawThread === '' ? null : Number(rawThread);
  if (!inbound || inbound.role !== 'user' || inbound.deleted || meta.inbound !== true
    || !/^[1-9][0-9]*$/u.test(fromTelegramId)
    || (messageThreadId != null
      && (!Number.isSafeInteger(messageThreadId) || messageThreadId <= 0))) return null;
  const sourceMeta = { ...meta };
  delete sourceMeta.preparedReply;
  delete sourceMeta.deliveryRetryAt;
  if (sourceMeta.messageThreadId == null) delete sourceMeta.messageThreadId;
  const sourceBodyJson = canonicalJson({
    contract: 'TelegramArchivedUserEnvelope/v1',
    chatId: String(inbound.chat_id), messageId: String(inbound.message_id),
    sender: String(inbound.sender || ''), role: inbound.role,
    kind: String(inbound.kind || ''), content: String(inbound.content || ''),
    replyToMessageId: inbound.reply_to_message_id == null
      ? null : String(inbound.reply_to_message_id),
    mediaRef: inbound.media_ref == null ? null : String(inbound.media_ref),
    fileId: inbound.file_id == null ? null : String(inbound.file_id),
    deleted: false, meta: sourceMeta,
  });
  const sourceSha256 = interpretationRetryHash(sourceBodyJson);
  const expectedScope = `telegram:${interpretationRetryHash(canonicalJson({
    contract: 'TelegramIngressSupersessionLane/v1',
    chatId: String(inbound.chat_id),
    messageThreadId: messageThreadId == null ? '' : String(messageThreadId),
    fromTelegramId,
  }))}`;
  const expectedToken = `telegram-message:${interpretationRetryHash(canonicalJson({
    contract: 'TelegramIngressSupersessionToken/v1',
    messageId: String(inbound.message_id), sourceSha256,
  }))}`;
  return Object.freeze({
    chatId: String(inbound.chat_id), fromTelegramId, messageThreadId,
    expectedScope, expectedToken,
  });
}

function telegramRetrySupersessionBinding(
  episode, supersessionScope = '', supersessionToken = '',
) {
  const binding = telegramRetrySupersessionArchiveBinding(episode);
  return binding && supersessionScope === binding.expectedScope
    && supersessionToken === binding.expectedToken ? binding : null;
}

function telegramPendingInterpretationReplySuperseded(
  episode, supersessionScope = '', supersessionTokenSha256 = '',
) {
  const origin = interpretationRetryOrigin(episode);
  const binding = telegramRetrySupersessionArchiveBinding(episode);
  if (!binding || supersessionScope !== binding.expectedScope
    || interpretationRetryHash(binding.expectedToken) !== supersessionTokenSha256) return true;
  return Boolean(newerTelegramUserMessage(binding.chatId, origin.messageId, {
    fromTelegramId: binding.fromTelegramId,
    messageThreadId: binding.messageThreadId,
  }));
}

function interpretationRetrySuperseded(episode, supersessionScope = '', supersessionToken = '') {
  const origin = interpretationRetryOrigin(episode);
  if (origin.channel === 'telegram') {
    const binding = telegramRetrySupersessionBinding(
      episode, supersessionScope, supersessionToken,
    );
    if (!binding) return true;
    return Boolean(newerTelegramUserMessage(binding.chatId, origin.messageId, {
      fromTelegramId: binding.fromTelegramId,
      messageThreadId: binding.messageThreadId,
    }));
  }
  if (supersessionScope && supersessionToken) {
    const latest = /** @type {any} */ (latestAuthorizedUnderstandingForScope(
      supersessionScope,
    )
      || db.prepare(`SELECT json_extract(meta, '$.messageContext.ingressToken') AS token
        FROM main.timeline WHERE kind='chat_in'
          AND json_extract(meta, '$.messageContext.ingressScope')=?
        ORDER BY id DESC LIMIT 1`).get(supersessionScope));
    if (latest?.token && latest.token !== supersessionToken) return true;
  }
  if (!/^\d+$/u.test(origin.messageId) || !origin.conversationId) return false;
  return Boolean(db.prepare(`SELECT id FROM main.messages
    WHERE conversation_id=? AND role='user' AND id>? ORDER BY id LIMIT 1`)
    .get(origin.conversationId, Number(origin.messageId)));
}

function latestAuthorizedUnderstandingForScope(scope, {
  afterSequence = 0, excludeExecutionKey = '',
} = {}) {
  if (!turnUnderstandingAuthorityStoreStatus(db).available) return null;
  const rows = /** @type {any[]} */ (db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND execution_seq>?
      AND execution_key<>? AND json_valid(args)
      AND json_extract(args,'$.supersessionScope')=?
    ORDER BY execution_seq DESC`).all(
    Number(afterSequence || 0), String(excludeExecutionKey || ''), String(scope || ''),
  ));
  for (const row of rows) {
    try {
      if (row.status !== 'done') continue;
      validateExecutionRecord(row);
      if (!turnUnderstandingAuthorityRecord(db, row)) continue;
      const args = JSON.parse(row.args);
      if (args.supersessionScope === scope && typeof args.supersessionToken === 'string') {
        return { row, args, token: args.supersessionToken };
      }
    } catch { /* Een ongeldige/historische rij krijgt nooit supersession-authority. */ }
  }
  return null;
}

export function recoverInterruptedUnderstandingIngresses() {
  // Gereserveerde mutatie woont uitsluitend in agent.js, waar de actuele
  // authentieke ingress een opaque capability mint. Deze legacy-startuphook
  // blijft bewust fail-closed en kan geen caller-JSON meer afmaken.
  return 0;
}

export function interpretationRetryResume(item, episode, now = Date.now()) {
  const rawRetry = interpretationRetryRecord(item, episode);
  if (!rawRetry) return null;
  const retry = rawRetry.valid
    ? registeredInterpretationRetryResume(item, episode, rawRetry)
    : null;
  if (!retry) return Object.freeze({ ...rawRetry, valid: false, stopReason: 'invalid' });
  const stopReason = !retry.valid
    ? 'invalid'
    : interpretationRetrySuperseded(episode, retry.supersessionScope, retry.supersessionToken)
      ? 'superseded'
      : retry.attempts >= INTERPRETATION_RETRY_BACKOFF_MS.length
        ? 'attempt_limit'
        : now >= Date.parse(retry.deadlineAt || '') ? 'deadline' : null;
  return Object.freeze({ ...retry, stopReason });
}

function withoutInterpretationRetry(item) {
  const state = interpretationWorkState(item);
  delete state.interpretation_retry;
  return state;
}

export function abandonInterpretationRetry(item, episode, reason, sessionRef = null) {
  return transitionGoalEpisode(episode.id, {
    state: 'abandoned', deliveryPolicy: 'none', deliveryTarget: '',
    checkpoint: `Niet uitgevoerd: ${reason}.`, proofSummary: `Niet uitgevoerd: ${reason}.`,
    nextAction: '', wakeTs: '', sessionRef,
    work: {
      state: withoutInterpretationRetry(item), clearWait: true, nextWakeTs: '',
      evidence: `Niet uitgevoerd: ${reason}.`, lastAction: 'interpretatie_gestopt',
      lastError: reason, clearLease: true,
    },
  });
}

export function interpretationReplyPreparationBinding(episodeId) {
  const row = db.prepare(`SELECT transition_key,session_ref FROM eva_episode_events
    WHERE episode_id=? AND kind='interpretation_reply_prepared'
    ORDER BY id DESC LIMIT 1`).get(Number(episodeId));
  const match = String((/** @type {any} */ (row))?.transition_key || '')
    .match(/^interpretation-reply:([a-f0-9]{64})$/u);
  return match ? Object.freeze({
    provenanceSha256: match[1],
    sessionRef: String((/** @type {any} */ (row))?.session_ref || ''),
  }) : null;
}

function issuedUnderstandingRecord(provenance) {
  const rows = db.prepare(`SELECT meta FROM main.timeline
    WHERE kind='turn_understanding'
      AND json_extract(meta, '$.turnId')=?
      AND json_extract(meta, '$.understanding.sourceSha256')=?
    ORDER BY id DESC LIMIT 4`).all(
    provenance.issuedTurnId, provenance.understandingSha256,
  );
  return rows.map((row) => {
    const record = json((/** @type {any} */ (row)).meta, {})?.understanding;
    return verifyTurnUnderstandingRecord(record)
      && record.sourceSha256 === provenance.understandingSha256
      && record.sourceTextSha256 === provenance.originalTextSha256
      && record.firstDecision?.kind === provenance.decisionKind
      ? record : null;
  }).find(Boolean) || null;
}

function modelReplyDraftAnchored(provenance) {
  const record = issuedUnderstandingRecord(provenance);
  return record?.sourceKind === 'model'
    && record.firstDecision?.replyDraftSha256 === provenance.textSha256;
}

function verifiedExecutionReplyBasis(episode, provenance) {
  const preparation = interpretationReplyPreparationBinding(episode?.id);
  const turnId = String(preparation?.sessionRef || '');
  if (!turnId || provenance.executionTurnId !== turnId
    || !issuedUnderstandingRecord(provenance)) return false;
  const receiptSha256s = provenance.basisReceiptSha256s;
  if (!Array.isArray(receiptSha256s) || !receiptSha256s.length
    || receiptSha256s.length > 40
    || receiptSha256s.some((sha) => !SHA256.test(String(sha || '')))
    || new Set(receiptSha256s).size !== receiptSha256s.length) return false;
  const receipts = receiptSha256s.flatMap((receiptSha256) => {
    const raw = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE completion_record_sha256=? AND status='done' LIMIT 2`).all(receiptSha256);
    if (raw.length !== 1) return [];
    try {
      const row = validateExecutionRecord(raw[0]);
      const semanticBinding = json(row.semantic_binding, null);
      const observation = String(row.execution_key || '').startsWith('observation:');
      if (semanticBinding?.understandingSha256 !== provenance.originalTextSha256
        || (observation && row.turn_id !== provenance.issuedTurnId)
        || (!observation && row.turn_id !== turnId)) return [];
      const output = json(row.output, null);
      if (!verifiedCompleteReceipt(output)) return [];
      return [{
        name: row.tool,
        args: json(row.args, {}),
        output: row.output,
        semanticBinding,
      }];
    } catch { return []; }
  });
  return receipts.length === receiptSha256s.length
    && semanticHash(receipts) === provenance.basisSha256;
}

function interpretationReplyProvenanceAnchored(episode, provenance) {
  return provenance?.source === 'model_reply_draft'
    ? modelReplyDraftAnchored(provenance)
    : provenance?.source === 'verified_execution_reply'
      ? verifiedExecutionReplyBasis(episode, provenance)
      : false;
}

const INTERPRETATION_REPLY_DELIVERY_KEYS = [
  'attempts', 'dedupeKey', 'kind', 'preparedAt', 'provenance',
  'provenanceSha256', 'recipient', 'text',
].sort();
const INTERPRETATION_REPLY_PROVENANCE_KEYS = [
  'basisReceiptSha256s', 'basisSha256', 'contract', 'decisionKind', 'episodeId', 'executionTurnId',
  'issuedTurnId', 'originalTextSha256', 'originSha256',
  'planSha256', 'source', 'supersessionScopeSha256', 'supersessionTokenSha256',
  'textSha256', 'understandingSha256', 'workItemId',
].sort();

function interpretationReplyProvenanceShapeValid(episode, provenance, provenanceKeys) {
  return canonicalJson(provenanceKeys) === canonicalJson(INTERPRETATION_REPLY_PROVENANCE_KEYS)
    && provenance.contract === INTERPRETATION_REPLY_PROVENANCE_CONTRACT
    && ['model_reply_draft', 'verified_execution_reply'].includes(provenance.source)
    && (provenance.source !== 'model_reply_draft' || provenance.decisionKind === 'reply')
    && (provenance.source !== 'model_reply_draft'
      || provenance.basisSha256 === provenance.understandingSha256)
    && (provenance.source !== 'model_reply_draft'
      || (Array.isArray(provenance.basisReceiptSha256s)
        && provenance.basisReceiptSha256s.length === 0))
    && interpretationReplyProvenanceAnchored(episode, provenance)
    && ['reply', 'act', 'inspect', 'schedule'].includes(provenance.decisionKind);
}

function interpretationReplyProvenanceIdentityValid(item, episode, provenance, preparation) {
  return Number(provenance.episodeId) === Number(episode?.id)
    && Number(provenance.workItemId) === Number(item?.id)
    && typeof provenance.issuedTurnId === 'string' && provenance.issuedTurnId.length > 0
    && preparation?.sessionRef === provenance.executionTurnId
    && provenance.originSha256 === interpretationRetryOriginHash(episode)
    && SHA256.test(provenance.basisSha256)
    && SHA256.test(provenance.originalTextSha256)
    && SHA256.test(provenance.planSha256)
    && SHA256.test(provenance.understandingSha256)
    && ((provenance.supersessionScopeSha256 == null
      && provenance.supersessionTokenSha256 == null)
      || (SHA256.test(String(provenance.supersessionScopeSha256 || ''))
        && SHA256.test(String(provenance.supersessionTokenSha256 || ''))));
}

function interpretationReplyPayloadValid({
  delivery, episode, provenance, provenanceSha256, preparation, stateBinding,
}) {
  const text = String(delivery.text || '');
  return text.length > 0 && text.length <= 4_000
    && canonicalTelegramText(text) === text
    && provenance.textSha256 === interpretationRetryHash(text)
    && SHA256.test(String(delivery.provenanceSha256 || ''))
    && delivery.provenanceSha256 === provenanceSha256
    && preparation?.provenanceSha256 === provenanceSha256
    && canonicalJson(stateBinding || null) === canonicalJson({
      contract: INTERPRETATION_REPLY_PROVENANCE_CONTRACT,
      provenanceSha256,
      dedupeKey: delivery.dedupeKey,
      supersessionScope: stateBinding?.supersessionScope || null,
      supersessionScopeSha256: provenance.supersessionScopeSha256,
      supersessionTokenSha256: provenance.supersessionTokenSha256,
    })
    && (stateBinding?.supersessionScope
      ? interpretationRetryHash(stateBinding.supersessionScope)
        === provenance.supersessionScopeSha256
      : provenance.supersessionScopeSha256 == null)
    && episode?.checkpoint === `Gebonden modelantwoord voorbereid: ${provenanceSha256}.`
    && delivery.dedupeKey === `episode:${episode?.id}:interpretation-retry-result`
    && delivery.recipient == null
    && Number.isInteger(Number(delivery.attempts)) && Number(delivery.attempts) >= 0
    && Number.isFinite(Date.parse(String(delivery.preparedAt || '')));
}

// Dit is een gesloten allowlist- en hashvalidator. Iedere conjunctie is een
// afzonderlijke fail-closed binding; de helpers groeperen alleen één lokale
// verifier en worden niet als permissieve authoritygrens geëxporteerd.
export function verifyInterpretationRetryPendingDelivery(item, episode, delivery) {
  if (!delivery || typeof delivery !== 'object' || Array.isArray(delivery)
    || delivery.kind !== 'interpretation_reply') return false;
  const provenance = delivery.provenance;
  const deliveryKeys = Object.keys(delivery).sort();
  const provenanceKeys = provenance && typeof provenance === 'object'
    && !Array.isArray(provenance) ? Object.keys(provenance).sort() : [];
  const provenanceSha256 = semanticHash(provenance || null);
  const preparation = interpretationReplyPreparationBinding(episode?.id);
  const stateBinding = interpretationWorkState(item).interpretation_reply_binding;
  return canonicalJson(deliveryKeys) === canonicalJson(INTERPRETATION_REPLY_DELIVERY_KEYS)
    && interpretationReplyProvenanceShapeValid(episode, provenance, provenanceKeys)
    && interpretationReplyProvenanceIdentityValid(item, episode, provenance, preparation)
    && interpretationReplyPayloadValid({
      delivery, episode, provenance, provenanceSha256, preparation, stateBinding,
    });
}

export function interpretationReplyDeliveryGate(episodeId, candidate = {}) {
  const episode = episodeRow(episodeId);
  const item = episode ? workRow(episode.work_item_id) : null;
  const state = interpretationWorkState(item);
  const delivery = state.pending_delivery;
  const preparation = interpretationReplyPreparationBinding(episode?.id);
  const required = Boolean(preparation || state.interpretation_reply_binding
    || delivery?.kind === 'interpretation_reply'
    || candidate?.kind === 'interpretation_reply');
  if (!required) return Object.freeze({ required: false, valid: true, current: true });
  // De oorsprong blijft ook na het voorbereiden van een antwoord leidend.
  // Telegram heeft een eigen transportarchief; web/voice met een numeriek
  // message-id gebruiken dezelfde conversation-bound messagecheck als de
  // retryfase. Zo kan een nieuw bewonersbericht nooit een al verzegeld maar
  // nog niet afgeleverd herstelantwoord achteraf alsnog laten ontsnappen.
  const scope = String(state.interpretation_reply_binding?.supersessionScope || '');
  const tokenSha256 = String(
    state.interpretation_reply_binding?.supersessionTokenSha256 || '',
  );
  let current = Boolean(episode) && (episode.origin_channel === 'telegram'
    ? !telegramPendingInterpretationReplySuperseded(episode, scope, tokenSha256)
    : !interpretationRetrySuperseded(episode));
  if (current && scope) {
    const latest = /** @type {any} */ (latestAuthorizedUnderstandingForScope(scope)
      || db.prepare(`SELECT json_extract(meta, '$.messageContext.ingressToken') AS token
        FROM main.timeline WHERE kind='chat_in'
          AND json_extract(meta, '$.messageContext.ingressScope')=?
        ORDER BY id DESC LIMIT 1`).get(scope));
    if (latest?.token
      && interpretationRetryHash(latest.token) !== tokenSha256) current = false;
  }
  const bound = verifyInterpretationRetryPendingDelivery(item, episode, delivery)
    && candidate?.kind === 'interpretation_reply'
    && candidate?.dedupeKey === delivery.dedupeKey
    && candidate?.text === delivery.text
    && candidate?.provenanceSha256 === delivery.provenanceSha256;
  return Object.freeze({ required: true, bound, valid: current && bound, current });
}

function blockInterpretationRetry(item, episode, reason, sessionRef = null) {
  const detail = clean(reason, 500) || 'de herinterpretatie bleef zonder geldig antwoord';
  return transitionGoalEpisode(episode.id, {
    state: 'blocked', wakeTs: '',
    checkpoint: `Niet uitgevoerd: ${detail}.`,
    nextAction: 'Wacht op een nieuw bericht van de oorspronkelijke verzoeker.', sessionRef,
    work: {
      state: withoutInterpretationRetry(item), clearWait: true, nextWakeTs: '',
      nextStep: 'Wacht op een nieuw bericht van de oorspronkelijke verzoeker.',
      lastAction: 'interpretatie_geblokkeerd', lastError: detail, clearLease: true,
    },
  });
}

export function resolveInterpretationRetryFailure({
  item, episode, retry, error = '', failureClass = '', validation = '', sessionRef = null,
} = /** @type {any} */ ({})) {
  const stopReason = retry?.stopReason || String(error).replace(/^interpreter_retry_/u, '');
  if (stopReason === 'superseded') return {
    kind: 'superseded',
    ...abandonInterpretationRetry(
      item, episode, 'de opdracht is door een nieuwer bericht ingehaald', sessionRef,
    ),
  };
  if (stopReason === 'invalid') return {
    kind: 'stopped',
    ...abandonInterpretationRetry(
      item, episode, 'de bewaarde opdrachtbinding klopt niet', sessionRef,
    ),
  };
  if (['deadline', 'attempt_limit'].includes(stopReason)) return {
    kind: 'blocked',
    ...blockInterpretationRetry(item, episode,
      stopReason === 'deadline'
        ? 'de harde herinterpretatiedeadline is verstreken zonder geldig antwoord'
        : 'de begrensde herinterpretatiepogingen zijn verbruikt zonder geldig antwoord',
      sessionRef),
  };
  if (stopReason === 'plan_drift') return {
    kind: 'blocked',
    ...blockInterpretationRetry(item, episode,
      'het hervatte plan wijkt af van het eerder gebonden plan', sessionRef),
  };
  const retryableDraftFailure = error === 'interpreter_malformed'
    && ['missing_reply_draft', 'invalid_reply_draft', 'clarification_not_terminal']
      .includes(String(validation || ''));
  const retryableDecisionFailure = error === 'interpreter_malformed'
    && ['selected_denied_action', 'selected_denied_action_and_redundant_work_start']
      .includes(String(validation || ''));
  const retryableMaterialCompletionFailure = error === 'material_completion_unproven'
    && validation === 'material_completion_unproven';
  if (!['interpreter_timeout', 'interpreter_unavailable'].includes(error)
    && !retryableDraftFailure && !retryableDecisionFailure
    && !retryableMaterialCompletionFailure) return {
    kind: 'stopped',
    ...abandonInterpretationRetry(
      item, episode, 'de herinterpretatie faalde gesloten', sessionRef,
    ),
  };
  const attempts = retry.attempts + 1;
  const deadlineMs = Date.parse(retry.deadlineAt || '');
  if (attempts >= INTERPRETATION_RETRY_BACKOFF_MS.length || Date.now() >= deadlineMs) return {
    kind: 'blocked',
    ...blockInterpretationRetry(item, episode,
      'de begrensde herinterpretatiepogingen zijn verbruikt zonder geldig antwoord',
      sessionRef),
  };
  const wakeTs = new Date(Math.min(
    Date.now() + INTERPRETATION_RETRY_BACKOFF_MS[attempts], deadlineMs,
  )).toISOString();
  const state = interpretationWorkState(item);
  state.interpretation_retry = {
    ...state.interpretation_retry,
    attempts,
    lastFailureClass: String(failureClass || 'temporary').slice(0, 80),
  };
  return {
    kind: 'retry',
    ...transitionGoalEpisode(episode.id, {
      state: 'waiting', wakeTs,
      checkpoint: 'De opdracht is nog niet geïnterpreteerd; er is niets uitgevoerd.',
      nextAction: 'Interpreteer de exact bewaarde opdracht opnieuw; voer haar pas daarna uit en controleer het resultaat.',
      sessionRef,
      work: {
        state, nextWakeTs: wakeTs,
        wait: { reason: 'interpretation_retry', resumeAfter: wakeTs, attempts },
        nextStep: 'Interpreteer de exact bewaarde opdracht opnieuw; voer haar pas daarna uit en controleer het resultaat.',
        lastAction: 'interpretatie_uitgesteld', lastError: String(failureClass || 'temporary'),
      },
    }),
  };
}

export function completeInterpretationRetry(
  item, episode, sessionRef = null, planSha256 = '', understandingSha256 = '',
  materialAuthoritySha256 = '', semanticAuthoritySha256 = '',
) {
  return db.transaction(() => {
    const retry = interpretationRetryResume(item, episode);
    const planHash = String(planSha256 || '');
    const understandingHash = String(understandingSha256 || '');
    const materialAuthorityHash = String(materialAuthoritySha256 || '');
    const semanticAuthorityHash = String(semanticAuthoritySha256 || '');
    const sameSemanticAuthority = Boolean(semanticAuthorityHash
      && retry?.semanticAuthoritySha256 === semanticAuthorityHash);
    if (!retry?.valid || retry.stopReason || !SHA256.test(planHash)
      || (retry.planSha256 && retry.planSha256 !== planHash)
      || (understandingHash && !SHA256.test(understandingHash))
      || (retry.understandingSha256
        && retry.understandingSha256 !== understandingHash
        && !sameSemanticAuthority)
      || (semanticAuthorityHash && !SHA256.test(semanticAuthorityHash))
      || (retry.semanticAuthoritySha256
        && retry.semanticAuthoritySha256 !== semanticAuthorityHash)
      || (materialAuthorityHash && !SHA256.test(materialAuthorityHash))
      || (retry.materialAuthoritySha256
        && retry.materialAuthoritySha256 !== materialAuthorityHash)) {
      throw new Error('herinterpretatieplan wijkt af van de duurzaam gebonden uitvoering');
    }
    const state = interpretationWorkState(item);
    state.interpretation_retry = {
      ...state.interpretation_retry,
      phase: 'understood',
      planSha256: planHash,
      ...(understandingHash ? { understandingSha256: understandingHash } : {}),
      ...(semanticAuthorityHash ? { semanticAuthoritySha256: semanticAuthorityHash } : {}),
      ...(materialAuthorityHash ? { materialAuthoritySha256: materialAuthorityHash } : {}),
    };
    return transitionGoalEpisode(episode.id, {
      state: 'executing', wakeTs: '',
      checkpoint: 'De oorspronkelijk bewaarde opdracht is nu geldig geïnterpreteerd.',
      nextAction: item.next_step || 'Voer de geïnterpreteerde opdracht uit en controleer het resultaat.',
      sessionRef,
      work: {
        state, clearWait: true, nextWakeTs: '',
        lastAction: 'interpretatie_afgerond', lastError: '',
      },
    });
  }).immediate();
}

function interpretationRetryHandoffInput({
  channel, conversationId, messageId, requester, originalText,
  supersessionScope, supersessionToken,
}) {
  const exactIdentityString = (value, label, limit, optional = false) => {
    if (typeof value !== 'string') {
      throw new Error(`duurzaam interpretatieherstel vereist ${label} als tekst`);
    }
    if ((!optional && !value) || value.length > limit) {
      throw new Error(`duurzaam interpretatieherstel heeft een ongeldige ${label}`);
    }
    // De downstream lifecycle bewaart deze velden al in deze canonieke vorm.
    // We normaliseren hier niet: een afwijkende bytevorm wordt geweigerd en kan
    // dus nooit stil naar dezelfde oorsprong worden verkort of samengevouwen.
    if (value.replace(/\s+/gu, ' ').trim() !== value) {
      throw new Error(`duurzaam interpretatieherstel vereist canonieke ${label}`);
    }
    return value;
  };
  const originChannel = exactIdentityString(channel, 'channel', 40);
  if (!['telegram', 'voice', 'web'].includes(originChannel)) {
    throw new Error('duurzaam interpretatieherstel ondersteunt alleen telegram, voice en web');
  }
  if (typeof originalText !== 'string' || !originalText.trim()) {
    throw new Error('duurzaam interpretatieherstel vereist de oorspronkelijke opdracht als tekst');
  }
  const conversation = exactIdentityString(conversationId, 'conversation-id', 240);
  const message = exactIdentityString(messageId, 'message-id', 160);
  // createWorkItem bewaart owner exact tot 80 tekens; langer weigeren voorkomt
  // dat episode en werk twee aliassen van dezelfde requester zouden bevatten.
  const exactRequester = exactIdentityString(requester, 'requester', 80);
  const scope = exactIdentityString(supersessionScope, 'supersession-scope', 240, true);
  const token = exactIdentityString(supersessionToken, 'supersession-token', 160, true);
  if (Boolean(scope) !== Boolean(token)) {
    throw new Error('duurzaam interpretatieherstel vereist zowel supersession-scope als -token');
  }
  const preserved = redactSensitiveText(originalText);
  const identity = {
    contract: INTERPRETATION_RETRY_CONTRACT,
    channel: originChannel,
    conversationId: conversation,
    messageId: message,
    originalText: preserved,
    rawOriginalTextSha256: interpretationRetryHash(originalText),
    originalTextSha256: interpretationRetryHash(preserved),
    requester: exactRequester,
    requesterSha256: interpretationRetryHash(exactRequester),
    supersessionScope: scope,
    supersessionScopeSha256: interpretationRetryHash(scope),
    supersessionToken: token,
    supersessionTokenSha256: interpretationRetryHash(token),
  };
  return Object.freeze({
    ...identity,
    handoffKey: semanticHash({
      contract: identity.contract,
      channel: identity.channel,
      conversationId: identity.conversationId,
      messageId: identity.messageId,
      rawOriginalTextSha256: identity.rawOriginalTextSha256,
      originalTextSha256: identity.originalTextSha256,
    }),
  });
}

function interpretationRetryHandoffBindingSha256(identity, workItemId, episodeId) {
  return semanticHash({
    handoffKey: identity.handoffKey,
    rawOriginalTextSha256: identity.rawOriginalTextSha256,
    originalTextSha256: identity.originalTextSha256,
    requesterSha256: identity.requesterSha256,
    supersessionScopeSha256: identity.supersessionScopeSha256,
    supersessionTokenSha256: identity.supersessionTokenSha256,
    workItemId: Number(workItemId),
    episodeId: Number(episodeId),
  });
}

function interpretationRetryRegistryIdentity(retry) {
  if (!retry?.valid
    || !SHA256.test(String(retry.rawOriginalTextSha256 || ''))
    || !SHA256.test(String(retry.originalTextSha256 || ''))
    || !SHA256.test(String(retry.handoffKey || ''))) return null;
  const identity = {
    contract: INTERPRETATION_RETRY_CONTRACT,
    channel: String(retry.origin?.channel || ''),
    conversationId: String(retry.origin?.conversationId || ''),
    messageId: String(retry.origin?.messageId || ''),
    originalText: retry.originalText,
    rawOriginalTextSha256: retry.rawOriginalTextSha256,
    originalTextSha256: retry.originalTextSha256,
    requester: String(retry.origin?.requester || ''),
    requesterSha256: interpretationRetryHash(retry.origin?.requester || ''),
    supersessionScope: retry.supersessionScope,
    supersessionScopeSha256: interpretationRetryHash(retry.supersessionScope),
    supersessionToken: retry.supersessionToken,
    supersessionTokenSha256: interpretationRetryHash(retry.supersessionToken),
    handoffKey: retry.handoffKey,
  };
  const expectedHandoffKey = semanticHash({
    contract: identity.contract,
    channel: identity.channel,
    conversationId: identity.conversationId,
    messageId: identity.messageId,
    rawOriginalTextSha256: identity.rawOriginalTextSha256,
    originalTextSha256: identity.originalTextSha256,
  });
  return expectedHandoffKey === identity.handoffKey ? Object.freeze(identity) : null;
}

function interpretationRetryHandoffRow(identity) {
  return /** @type {any} */ (db.prepare(`SELECT *
    FROM eva_interpretation_retry_handoffs
    WHERE contract=? AND origin_channel=?
      AND origin_conversation_id=? AND origin_message_id=? LIMIT 1`).get(
    identity.contract, identity.channel, identity.conversationId, identity.messageId,
  ));
}

function conflictingInterpretationRetryHandoff() {
  throw new Error('conflicterende ChatInterpretationRetry/v1-handoff voor dezelfde oorsprong');
}

function coherentTerminalInterpretationHandoff(workItem, episode) {
  const matchingStatus = {
    blocked: 'blocked',
    closed: 'done',
    abandoned: 'cancelled',
  }[episode?.state];
  if (!matchingStatus || workItem?.status !== matchingStatus
    || workItem.wait_json != null || workItem.next_wake_ts != null) return false;
  const checkpoint = String(episode.checkpoint || '');
  if (!checkpoint) return false;
  const event = /** @type {any} */ (db.prepare(`SELECT kind,payload_json
    FROM eva_episode_events
    WHERE episode_id=?
      AND json_extract(payload_json,'$.to')=?
      AND json_extract(payload_json,'$.workItemId')=?
    ORDER BY id DESC LIMIT 1`).get(episode.id, episode.state, workItem.id));
  const payload = json(event?.payload_json, null);
  if (!payload || event.kind !== `state:${episode.state}` || payload.to !== episode.state
    || Number(payload.workItemId) !== Number(workItem.id)
    || String(payload.checkpoint || '') !== checkpoint) return false;
  if (episode.state === 'blocked') {
    return episode.closed_ts == null
      && episode.wake_ts == null
      && !episode.proof_summary
      && !payload.proofSummary
      && String(workItem.last_error || '').length > 0;
  }
  const proof = String(episode.proof_summary || '');
  return Boolean(episode.closed_ts)
    && episode.wake_ts == null
    && !episode.next_action
    && proof.length > 0
    && String(payload.proofSummary || '') === proof
    && String(workItem.evidence || '') === proof;
}

function exactInterpretationHandoffRegistryRow(identity, row, bindingSha256) {
  return [
    row,
    row?.handoff_key === identity.handoffKey,
    row?.contract === identity.contract,
    row?.origin_channel === identity.channel,
    row?.origin_conversation_id === identity.conversationId,
    row?.origin_message_id === identity.messageId,
    row?.raw_original_text_sha256 === identity.rawOriginalTextSha256,
    row?.original_text_sha256 === identity.originalTextSha256,
    row?.requester_sha256 === identity.requesterSha256,
    row?.supersession_scope_sha256 === identity.supersessionScopeSha256,
    row?.supersession_token_sha256 === identity.supersessionTokenSha256,
    row?.binding_sha256 === bindingSha256,
  ].every(Boolean);
}

function exactInterpretationHandoffLifecycle(identity, workItem, episode, origin) {
  return [
    workItem, episode, origin,
    Number(episode?.work_item_id) === Number(workItem?.id),
    episode?.origin_kind === 'user',
    String(episode?.origin_channel || '') === identity.channel,
    String(episode?.origin_conversation_id || '') === identity.conversationId,
    String(episode?.origin_message_id || '') === identity.messageId,
    interpretationRetryHash(String(episode?.requester || '')) === identity.requesterSha256,
    interpretationRetryHash(String(origin?.requester || '')) === identity.requesterSha256,
    workItem?.source === identity.channel,
    interpretationRetryHash(String(workItem?.owner || '')) === identity.requesterSha256,
  ].every(Boolean);
}

function exactPendingInterpretationHandoff(identity, storedState, storedRetry, workItem, episode) {
  return [
    storedState?.interpretation_retry != null,
    ['waiting', 'executing'].includes(String(episode?.state || '')),
    workItem?.status === 'open', storedState, storedRetry?.valid,
    storedRetry?.rawOriginalTextSha256 === identity.rawOriginalTextSha256,
    storedRetry?.originalTextSha256 === identity.originalTextSha256,
    storedRetry?.supersessionScope === identity.supersessionScope,
    storedRetry?.supersessionToken === identity.supersessionToken,
    storedRetry?.handoffKey === identity.handoffKey,
  ].every(Boolean);
}

function exactDeliveryPendingInterpretationHandoff(storedState, workItem, episode) {
  const delivery = storedState?.pending_delivery;
  const wait = json(workItem?.wait_json, null);
  const attempts = Number(delivery?.attempts);
  const waitAttempts = Number(wait?.attempts);
  return [
    storedState && storedState.interpretation_retry == null,
    episode?.state === 'delivery_pending', workItem?.status === 'open',
    episode?.delivery_policy === 'required',
    delivery?.kind === 'interpretation_reply',
    Number.isSafeInteger(attempts) && attempts >= 0,
    Number.isSafeInteger(waitAttempts) && waitAttempts === attempts,
    ['delivery_retry', 'quiet_hours_delivery'].includes(String(wait?.reason || '')),
    String(wait?.resumeAfter || '') === String(workItem?.next_wake_ts || ''),
    String(workItem?.next_wake_ts || '') === String(episode?.wake_ts || ''),
    verifyInterpretationRetryPendingDelivery(workItem, episode, delivery),
  ].every(Boolean);
}

function exactRegisteredInterpretationRetryHandoff(identity, row) {
  const workItem = workRow(row?.work_item_id);
  const episode = episodeRow(row?.episode_id);
  const bindingSha256 = interpretationRetryHandoffBindingSha256(
    identity, row?.work_item_id, row?.episode_id,
  );
  const origin = episode ? /** @type {any} */ (db.prepare(`SELECT *
    FROM eva_episode_origins
    WHERE episode_id=? AND origin_kind='user'
      AND coalesce(origin_channel,'')=?
      AND coalesce(origin_conversation_id,'')=?
      AND coalesce(origin_message_id,'')=?
    ORDER BY id ASC LIMIT 1`).get(
    episode.id, identity.channel, identity.conversationId, identity.messageId,
  )) : null;
  let storedState = null;
  try {
    const parsed = JSON.parse(String(workItem?.state_json || ''));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      && String(workItem.state_json).length <= WORK_STATE_JSON_LIMIT) storedState = parsed;
  } catch { /* corrupte durable state faalt hieronder gesloten */ }
  const storedRetryValue = storedState?.interpretation_retry;
  const storedRetry = storedRetryValue == null
    ? null : interpretationRetryRecord(workItem, episode);
  const terminalExact = storedState != null && storedRetryValue == null
    && coherentTerminalInterpretationHandoff(workItem, episode);
  const deliveryPendingExact = storedState != null && storedRetryValue == null
    && exactDeliveryPendingInterpretationHandoff(storedState, workItem, episode);
  return exactInterpretationHandoffRegistryRow(identity, row, bindingSha256)
    && exactInterpretationHandoffLifecycle(identity, workItem, episode, origin)
    && (exactPendingInterpretationHandoff(
      identity, storedState, storedRetry, workItem, episode,
    ) || deliveryPendingExact || terminalExact)
    ? { workItem, episode } : null;
}

function exactInterpretationRetryHandoffOwnership(channel, conversationId, messageId) {
  verifyCurrentInterpretationHandoffRegistrySchema();
  const row = /** @type {any} */ (db.prepare(`SELECT *
    FROM eva_interpretation_retry_handoffs
    WHERE contract=? AND origin_channel=? AND origin_conversation_id=?
      AND origin_message_id=? LIMIT 1`).get(
    INTERPRETATION_RETRY_CONTRACT, channel, conversationId, messageId,
  ));
  if (!row) return null;
  const identity = {
    contract: row.contract, channel: row.origin_channel,
    conversationId: row.origin_conversation_id, messageId: row.origin_message_id,
    handoffKey: row.handoff_key,
    rawOriginalTextSha256: row.raw_original_text_sha256,
    originalTextSha256: row.original_text_sha256,
    requesterSha256: row.requester_sha256,
    supersessionScopeSha256: row.supersession_scope_sha256,
    supersessionTokenSha256: row.supersession_token_sha256,
  };
  const workItem = workRow(row.work_item_id);
  const episode = episodeRow(row.episode_id);
  const origin = episode ? /** @type {any} */ (db.prepare(`SELECT *
    FROM eva_episode_origins WHERE episode_id=? AND origin_kind='user'
      AND coalesce(origin_channel,'')=?
      AND coalesce(origin_conversation_id,'')=?
      AND coalesce(origin_message_id,'')=?
    ORDER BY id ASC LIMIT 1`).get(
    episode.id, identity.channel, identity.conversationId, identity.messageId,
  )) : null;
  const bindingSha256 = interpretationRetryHandoffBindingSha256(
    identity, row.work_item_id, row.episode_id,
  );
  const rowExact = exactInterpretationHandoffRegistryRow(identity, row, bindingSha256);
  const lifecycleExact = exactInterpretationHandoffLifecycle(
    identity, workItem, episode, origin,
  );
  return rowExact && lifecycleExact
    ? { workItem, episode } : null;
}

/** Alleen-lezen exact-originprojectie voor een reeds geregistreerde handoff. */
export function registeredInterpretationRetryHandoffForOrigin(
  channel, conversationId, messageId,
) {
  try {
    verifyCurrentInterpretationHandoffRegistrySchema();
    const exactChannel = String(channel || '');
    const exactConversation = String(conversationId || '');
    const exactMessage = String(messageId || '');
    if (!['telegram', 'voice', 'web'].includes(exactChannel)
      || !exactConversation || !exactMessage) return Object.freeze({ status: 'absent' });
    const rows = /** @type {any[]} */ (db.prepare(`SELECT *
      FROM eva_interpretation_retry_handoffs
      WHERE origin_channel=? AND origin_conversation_id=?
        AND origin_message_id=? LIMIT 2`).all(
      exactChannel, exactConversation, exactMessage,
    ));
    if (!rows.length) return Object.freeze({ status: 'absent' });
    if (rows.length !== 1) return Object.freeze({ status: 'invalid' });
    const row = rows[0];
    const workItem = workRow(row.work_item_id);
    const episode = episodeRow(row.episode_id);
    const retry = interpretationRetryRecord(workItem, episode);
    const activeIdentity = retry?.valid ? interpretationRetryRegistryIdentity(retry) : null;
    const identity = activeIdentity || {
      contract: row.contract, channel: row.origin_channel,
      conversationId: row.origin_conversation_id, messageId: row.origin_message_id,
      handoffKey: row.handoff_key,
      rawOriginalTextSha256: row.raw_original_text_sha256,
      originalTextSha256: row.original_text_sha256,
      requesterSha256: row.requester_sha256,
      supersessionScopeSha256: row.supersession_scope_sha256,
      supersessionTokenSha256: row.supersession_token_sha256,
      supersessionScope: '', supersessionToken: '',
    };
    const registered = exactRegisteredInterpretationRetryHandoff(identity, row);
    return registered ? Object.freeze({
      status: 'valid',
      workItemId: Number(registered.workItem.id),
      episodeId: Number(registered.episode.id),
      workStatus: String(registered.workItem.status || ''),
      episodeState: String(registered.episode.state || ''),
    }) : Object.freeze({ status: 'invalid' });
  } catch { return Object.freeze({ status: 'invalid' }); }
}

function exactDurableRowProjection(candidate, registered, table) {
  if (!candidate || !registered) return false;
  const columns = db.prepare(`PRAGMA table_info(${table})`).all()
    .map((column) => /** @type {any} */ (column).name);
  if (!columns.length || columns.some((name) => !Object.hasOwn(candidate, name))) return false;
  const projection = (row) => Object.fromEntries(columns.map((name) => [name, row[name]]));
  return canonicalJson(projection(candidate)) === canonicalJson(projection(registered));
}

function exactInterpretationRetryResumeInput(item, episode, registered) {
  return exactDurableRowProjection(item, registered.workItem, 'eva_work_items')
    && exactDurableRowProjection(episode, registered.episode, 'eva_goal_episodes');
}

function registeredInterpretationRetryResume(item, episode, retry) {
  try {
    return db.transaction(() => {
      verifyCurrentInterpretationHandoffRegistrySchema();
      const identity = interpretationRetryRegistryIdentity(retry);
      if (!identity) return null;
      const row = interpretationRetryHandoffRow(identity);
      const registered = row && exactRegisteredInterpretationRetryHandoff(identity, row);
      if (!registered || !exactInterpretationRetryResumeInput(item, episode, registered)) {
        return null;
      }
      const registeredRetry = interpretationRetryRecord(
        registered.workItem, registered.episode,
      );
      return registeredRetry?.valid
        && canonicalJson(registeredRetry) === canonicalJson(retry)
        ? registeredRetry : null;
    })();
  } catch {
    return null;
  }
}

function registerInterpretationRetryHandoff(identity, workItem, episode, createdAt) {
  const bindingSha256 = interpretationRetryHandoffBindingSha256(
    identity, workItem.id, episode.id,
  );
  db.prepare(`INSERT INTO eva_interpretation_retry_handoffs (
    handoff_key,contract,origin_channel,origin_conversation_id,origin_message_id,
    raw_original_text_sha256,original_text_sha256,requester_sha256,supersession_scope_sha256,
    supersession_token_sha256,work_item_id,episode_id,binding_sha256,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    identity.handoffKey, identity.contract, identity.channel,
    identity.conversationId, identity.messageId, identity.rawOriginalTextSha256,
    identity.originalTextSha256,
    identity.requesterSha256, identity.supersessionScopeSha256,
    identity.supersessionTokenSha256, workItem.id, episode.id,
    bindingSha256, createdAt,
  );
  const row = interpretationRetryHandoffRow(identity);
  const exact = row && exactRegisteredInterpretationRetryHandoff(identity, row);
  if (!exact) throw new Error('ChatInterpretationRetry/v1-handoff kon niet exact worden opgeslagen');
  return exact;
}

function migrateLegacyInterpretationRetryHandoff(identity) {
  const candidates = /** @type {any[]} */ (db.prepare(`SELECT DISTINCT
      e.id AS episode_id,w.id AS work_id
    FROM eva_episode_origins o
    JOIN eva_goal_episodes e ON e.id=o.episode_id
    JOIN eva_work_items w ON w.id=e.work_item_id
    WHERE o.origin_kind='user' AND coalesce(o.origin_channel,'')=?
      AND coalesce(o.origin_conversation_id,'')=?
      AND coalesce(o.origin_message_id,'')=?
    ORDER BY e.id`).all(identity.channel, identity.conversationId, identity.messageId));
  if (!candidates.length) return null;
  if (candidates.length !== 1) return conflictingInterpretationRetryHandoff();
  const workItem = workRow(candidates[0].work_id);
  const episode = episodeRow(candidates[0].episode_id);
  const raw = interpretationWorkState(workItem).interpretation_retry;
  const candidate = {
    workItem, episode, raw,
    retry: raw == null ? null : interpretationRetryRecord(workItem, episode),
  };
  if (!candidate.retry?.valid
    || !['waiting', 'executing'].includes(String(candidate.episode?.state || ''))
    || candidate.workItem?.status !== 'open'
    || typeof candidate.workItem?.state_json !== 'string'
    || candidate.workItem.state_json.length > WORK_STATE_JSON_LIMIT
    || candidate.retry.rawOriginalTextSha256 !== identity.rawOriginalTextSha256
    || candidate.retry.handoffKey !== identity.handoffKey
    || candidate.retry.originalTextSha256 !== identity.originalTextSha256
    || candidate.retry.origin.channel !== identity.channel
    || candidate.retry.origin.conversationId !== identity.conversationId
    || candidate.retry.origin.messageId !== identity.messageId
    || interpretationRetryHash(candidate.retry.origin.requester) !== identity.requesterSha256
    || candidate.retry.supersessionScope !== identity.supersessionScope
    || candidate.retry.supersessionToken !== identity.supersessionToken) {
    return conflictingInterpretationRetryHandoff();
  }
  return registerInterpretationRetryHandoff(
    identity, candidate.workItem, candidate.episode, candidate.retry.createdAt,
  );
}

export function createInterpretationRetryHandoff({
  channel, conversationId, messageId, requester, originalText,
  failureClass = '', sessionRef = null,
  supersessionScope = '', supersessionToken = '',
  visibleToFamily = true,
} = /** @type {any} */ ({})) {
  if (typeof visibleToFamily !== 'boolean') {
    throw new Error('duurzaam interpretatieherstel vereist een geldige gezinszichtbaarheid');
  }
  const identity = interpretationRetryHandoffInput({
    channel, conversationId, messageId, requester, originalText,
    supersessionScope, supersessionToken,
  });
  // Geen werk-, episode- of registrymutatie voordat de hele lokale
  // append-only opslaggrens exact is geverifieerd.
  assertInterpretationHandoffRegistryReady();
  return db.transaction(() => {
    verifyCurrentInterpretationHandoffRegistrySchema();
    const registered = interpretationRetryHandoffRow(identity);
    if (registered) {
      const exact = exactRegisteredInterpretationRetryHandoff(identity, registered);
      return exact || conflictingInterpretationRetryHandoff();
    }
    const legacy = migrateLegacyInterpretationRetryHandoff(identity);
    if (legacy) return legacy;
    const createdAt = new Date();
    const wakeTs = new Date(
      createdAt.getTime() + INTERPRETATION_RETRY_BACKOFF_MS[0],
    ).toISOString();
    const originSha256 = interpretationRetryOriginHash({
      origin_channel: identity.channel,
      origin_conversation_id: identity.conversationId,
      origin_message_id: identity.messageId,
      requester: identity.requester,
      delivery_policy: 'required',
      delivery_target: identity.requester,
    });
    const state = {
      interpretation_retry: {
        contract: INTERPRETATION_RETRY_CONTRACT,
        handoffKey: identity.handoffKey,
        rawOriginalTextSha256: identity.rawOriginalTextSha256,
        originalText: identity.originalText,
        originalTextSha256: identity.originalTextSha256,
        originSha256,
        createdAt: createdAt.toISOString(),
        deadlineAt: new Date(createdAt.getTime() + INTERPRETATION_RETRY_TTL_MS).toISOString(),
        attempts: 0,
        phase: 'pending',
        lastFailureClass: String(failureClass || 'temporary').slice(0, 80),
        ...(identity.supersessionScope ? {
          supersessionScope: identity.supersessionScope,
          supersessionToken: identity.supersessionToken,
          supersessionSha256: interpretationRetryHash(
            `${identity.supersessionScope}\n${identity.supersessionToken}`,
          ),
        } : {}),
      },
    };
    const serializedState = JSON.stringify(state);
    if (serializedState.length > WORK_STATE_JSON_LIMIT) {
      throw new Error('ChatInterpretationRetry/v1-toestand overschrijdt de duurzame limiet');
    }
    const created = createDurableWork({
      source: identity.channel, owner: identity.requester,
      title: `Interpretatieherstel ${identity.handoffKey}`,
      goal: 'Interpreteer de exact bewaarde, geredigeerde oorspronkelijke opdracht opnieuw.',
      nextStep: 'Interpreteer de oorspronkelijke opdracht opnieuw, voer haar daarna volgens de geldige capabilitygrenzen uit en controleer het resultaat.',
      visibleToFamily,
      definitionOfDone: 'De exact bewaarde opdracht is geldig geïnterpreteerd, uitgevoerd en gecontroleerd.',
      priority: 75,
    }, {
      originKind: 'user', originChannel: identity.channel,
      originConversationId: identity.conversationId, originMessageId: identity.messageId,
      requester: identity.requester, deliveryPolicy: 'required',
      deliveryTarget: identity.requester,
    });
    if (interpretationRetryOriginHash(created.episode) !== originSha256) {
      throw new Error('ChatInterpretationRetry/v1-oorsprong werd niet bytegelijk bewaard');
    }
    const transitioned = transitionGoalEpisode(created.episode.id, {
      state: 'waiting', wakeTs,
      checkpoint: 'De opdracht is exact bewaard; er is nog niets uitgevoerd.',
      nextAction: 'Interpreteer de exact bewaarde opdracht opnieuw; voer haar pas daarna uit en controleer het resultaat.',
      sessionRef,
      work: {
        state, nextWakeTs: wakeTs,
        wait: { reason: 'interpretation_retry', resumeAfter: wakeTs, attempts: 0 },
        nextStep: 'Interpreteer de exact bewaarde opdracht opnieuw; voer haar pas daarna uit en controleer het resultaat.',
        lastAction: 'interpretatie_uitgesteld', lastError: String(failureClass || 'temporary'),
      },
    });
    const freshWork = workRow(transitioned.workItem.id);
    const freshEpisode = episodeRow(transitioned.episode.id);
    const freshRetry = interpretationRetryRecord(freshWork, freshEpisode);
    if (freshEpisode?.state !== 'waiting' || freshWork?.status !== 'open'
      || freshWork.state_json !== serializedState
      || !freshRetry?.valid
      || freshRetry.rawOriginalTextSha256 !== identity.rawOriginalTextSha256
      || freshRetry.originalTextSha256 !== identity.originalTextSha256
      || freshRetry.handoffKey !== identity.handoffKey) {
      throw new Error('ChatInterpretationRetry/v1-toestand werd niet exact bewaard');
    }
    return registerInterpretationRetryHandoff(
      identity, freshWork, freshEpisode, createdAt.toISOString(),
    );
  }).immediate();
}

export function cancelInterpretationRetryForConversation(
  conversationId, reason, sessionRef = null,
) {
  const episode = openEpisodeForConversation(conversationId);
  const item = episode?.work_item_id == null ? null : workRow(episode.work_item_id);
  return item && interpretationRetryRecord(item, episode)
    ? abandonInterpretationRetry(item, episode, reason, sessionRef) : null;
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

function recoverStrandedMonitoringEpisode(episode, before, state, pending) {
  if (acceptedMonitoringDeliveryReceiptCandidate({
    workId: Number(before.id), episodeId: Number(episode.id),
  })) return false;
  const wait = json(before?.wait_json, null);
  const wakeMs = Date.parse(before?.next_wake_ts || '');
  const exactWake = typeof before?.next_wake_ts === 'string'
    && before.next_wake_ts.length > 0
    && before.next_wake_ts === episode.wake_ts
    && before.next_wake_ts === wait?.resumeAfter;
  const authority = pending?.contract === 'MonitoringPendingDelivery/v2' && exactWake
    && ['delivery_retry', 'quiet_hours_delivery'].includes(String(wait?.reason || ''))
    && Number(wait?.attempts) === Number(pending.attempts)
    ? verifyMonitoringLifecycleAuthority({
      workId: Number(before.id), episodeId: Number(episode.id),
      expectedPendingSha256: String(pending.pendingSha256 || ''),
    }) : null;
  const endsMs = authority?.monitoring?.endsAt == null
    ? Number.POSITIVE_INFINITY : Date.parse(authority.monitoring.endsAt);
  const horizonMs = authority?.pending
    ? Math.min(Date.parse(authority.pending.expiresAt), endsMs) : Number.NaN;
  const latestScheduledWakeMs = horizonMs - 120_000;
  const preparedMs = Date.parse(authority?.pending?.preparedAt || '');
  if (authority?.pending && Number.isFinite(wakeMs)
    && wakeMs >= preparedMs + 15_000 && wakeMs <= latestScheduledWakeMs) return false;
  const blocked = transitionGoalEpisode(episode.id, {
    state: 'blocked', wakeTs: '',
    checkpoint: 'Monitoringaflevering geblokkeerd: de startupbinding is ongeldig.',
    nextAction: 'Beoordeel de verzegelde aflevering handmatig; verstuur haar niet.',
    eventKind: 'monitoring_startup_pending_invalid',
    idempotencyKey: `monitoring-startup-invalid:${episode.id}`,
    work: {
      state, clearWait: true, nextWakeTs: '', clearLease: true,
      lastError: 'monitoring_startup_pending_invalid',
    },
  });
  return blocked?.changed === true;
}

export function recoverStrandedDeliveryWork() {
  if (!workTableExists()) return 0;
  const now = nowIso();
  const stranded = db.prepare(`
    SELECT e.* FROM eva_goal_episodes e
    JOIN eva_work_items w ON w.id = e.work_item_id
    WHERE e.delivery_policy = 'required' AND e.state = 'delivery_pending'
      AND NOT EXISTS (
        SELECT 1 FROM eva_delivery_receipts r
        JOIN eva_episode_events x ON x.episode_id=r.episode_id
          AND x.transition_key=('receipt:' || r.idempotency_key)
        WHERE r.episode_id=e.id AND json_extract(x.payload_json, '$.to')='closed'
      )
      AND coalesce(json_extract(w.wait_json, '$.reason'), '')
        NOT IN ('${TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN}',
          '${TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED}')
      AND NOT (
        coalesce(json_extract(w.wait_json, '$.reason'), '') IN ('quiet_hours_delivery', 'delivery_retry')
        AND w.next_wake_ts > ?
        AND json_type(w.state_json, '$.monitoring') IS NULL
      )
  `).all(now);
  let recovered = 0;
  for (const episode of stranded) {
    const before = workRow(episode.work_item_id);
    const state = json(before?.state_json, null);
    const pending = state?.pending_delivery;
    if (state && Object.hasOwn(state, 'monitoring')) {
      if (recoverStrandedMonitoringEpisode(episode, before, state, pending)) recovered += 1;
      continue;
    }
    const action = episode.origin_kind === 'self'
      ? 'Bezorg het gecontroleerde resultaat kort aan het gezin.'
      : 'Bezorg het gecontroleerde resultaat aan de oorspronkelijke verzoeker.';
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

/**
 * Bevries een mogelijk al zichtbare aflevering zonder er bewijs van te maken.
 * Alleen een nieuwe, expliciete herstelbeslissing mag deze wait later wissen.
 */
export function freezeEpisodeDelivery(episodeId, {
  reason = TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
  operationKey = null,
  inbound = null,
} = {}) {
  return db.transaction(() => {
    const episode = episodeRow(episodeId);
    if (!episode || ['closed', 'abandoned'].includes(episode.state)) return null;
    const waitReason = [TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
      TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED].includes(reason)
      ? reason : TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN;
    const uncertain = waitReason === TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN;
    const action = uncertain
      ? 'Wacht op expliciete bevestiging voordat dit mogelijk al bezorgde resultaat opnieuw wordt verstuurd.'
      : 'Kies pas na een nieuwe opdracht een andere geldige afleverroute.';
    const result = transitionGoalEpisodeInTransaction(episode.id, {
      state: 'delivery_pending', nextAction: action,
      checkpoint: uncertain
        ? 'Telegram-aflevering heeft een onzekere transportuitkomst; er is geen afleverbewijs.'
        : 'Telegram heeft de aflevering aantoonbaar geweigerd; er is geen afleverbewijs.',
      eventKind: waitReason,
      idempotencyKey: `delivery-freeze:${waitReason}:${clean(operationKey || episode.id, 240)}`,
      work: {
        nextStep: action, currentStep: action,
        wait: { reason: waitReason, operationKey: clean(operationKey, 240) || null, automaticRetry: false },
        nextWakeTs: '', clearLease: true,
        lastError: uncertain
          ? 'Telegram kan het antwoord al hebben geaccepteerd; automatisch herhalen is geblokkeerd.'
          : 'Telegram heeft het antwoord niet geaccepteerd; een nieuwe afleverbeslissing is nodig.',
      },
    });
    if (inbound?.chatId != null && inbound?.messageId != null) {
      const row = db.prepare(`SELECT processing_status FROM telegram_messages
        WHERE chat_id=? AND message_id=? AND role='user'`).get(
        String(inbound.chatId), String(inbound.messageId),
      );
      if (!row) throw new Error('afleverfreeze mist het gebonden inbound Telegram-bericht');
      if (row.processing_status !== waitReason) {
        if ([TELEGRAM_DELIVERY_STATUS.INBOUND_COMPLETED,
          TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
          TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED].includes(row.processing_status)) {
          throw new Error(`terminale inbound-status botst met ${row.processing_status}`);
        }
        db.prepare(`UPDATE telegram_messages SET processing_status=?, processing_error=?, archived_ts=?
          WHERE chat_id=? AND message_id=?`).run(
          waitReason, clean(inbound.error, 500), nowIso(),
          String(inbound.chatId), String(inbound.messageId),
        );
      }
    }
    return result;
  })();
}

/** Herstelt oude/crash-halfafgeronde terminale inboundstatussen naar dezelfde episode-wacht. */
export function reconcileFrozenEpisodeDeliveries() {
  const rows = db.prepare(`SELECT chat_id,message_id,processing_status,processing_error,meta
    FROM telegram_messages WHERE role='user'
      AND processing_status IN ('${TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN}',
        '${TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED}')
      AND json_type(meta, '$.preparedReply.goalEpisodeId') IN ('integer','real')`).all();
  let reconciled = 0;
  for (const row of rows) {
    const prepared = json(row.meta, {})?.preparedReply;
    const episodeId = Number(prepared?.goalEpisodeId);
    if (!episodeId) continue;
    const origin = db.prepare(`SELECT 1 FROM eva_episode_origins
      WHERE episode_id=? AND origin_channel='telegram'
        AND origin_conversation_id=? AND origin_message_id=? LIMIT 1`).get(
      episodeId, `telegram:${row.chat_id}`, String(row.message_id),
    );
    if (!origin) continue;
    const before = episodeRow(episodeId);
    const beforeReason = before
      ? json(workRow(before.work_item_id)?.wait_json, {})?.reason : null;
    const result = freezeEpisodeDelivery(episodeId, {
      reason: row.processing_status,
      operationKey: prepared?.deliveryOperationKey || prepared?.turnId || null,
      inbound: { chatId: row.chat_id, messageId: row.message_id, error: row.processing_error },
    });
    if (result && before && beforeReason !== row.processing_status) {
      reconciled += 1;
    }
  }
  return reconciled;
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

export function conversationWorkReceipt(conversationId, requester) {
  const conversation = clean(conversationId, 240), principal = clean(requester, 120);
  if (!conversation || !principal) return null;
  for (const row of recentVerifiedTurnToolReceipts(conversation, 80)) { try {
    const binding = json(row.semantic_binding), args = json(row.args), result = json(row.output);
    const workId = Number(result?.data?.item?.id);
    if (row.tool !== 'werk_bijhouden' || binding?.capabilityId !== 'work.episode.manage'
      || binding?.operationId !== 'start' || args?.bewerking !== 'start'
      || result?.contract !== 'ActionResult/v1' || result?.tool !== 'werk_bijhouden'
      || !verifiedCompleteReceipt(result) || result.changed !== true
      || !Number.isSafeInteger(workId) || workId < 1) continue;
    const episode = db.prepare(`SELECT e.id FROM eva_goal_episodes e
      JOIN eva_episode_origins o ON o.episode_id=e.id
      JOIN eva_work_items w ON w.id=e.work_item_id
      WHERE e.work_item_id=? AND o.origin_conversation_id=?
        AND e.requester=? AND o.requester=?
        AND e.state NOT IN ('closed','abandoned') AND w.status IN ('open','blocked')
      ORDER BY o.id DESC LIMIT 1`).get(workId, conversation, principal, principal);
    if (!episode) continue;
    const body = {
      contract: 'ConversationWorkReceipt/v1', capabilityId: 'work.episode.manage',
      operationId: 'annuleren', field: 'id', value: String(workId),
      conversationId: conversation, requester: principal, episodeId: Number(episode.id),
      sourceTurnId: String(row.turn_id), understandingSha256: String(binding.understandingSha256),
      requirementId: String(binding.requirementId),
      completionRecordSha256: String(row.completion_record_sha256), observedAt: String(row.finished_ts),
    };
    return Object.freeze({ ...body, sha256: semanticHash(body) });
  } catch { /* alleen volledig gevalideerde startreceipts geven bevoegdheid */ } }
  return null;
}
export function recentEpisodeArtifactReceipts(conversationId) {
  const conversation = clean(conversationId, 240); if (!conversation) return [];
  const episode = openEpisodeForConversation(conversation);
  const closed = episode ? null : db.prepare(`
    SELECT e.id,e.work_item_id FROM eva_delivery_receipts r JOIN eva_goal_episodes e ON e.id=r.episode_id
    JOIN eva_work_items w ON w.id=e.work_item_id
    WHERE r.delivered_conversation_id = ? AND e.state = 'closed' AND w.status = 'done'
      AND EXISTS (SELECT 1 FROM eva_episode_origins o WHERE o.episode_id=e.id
        AND o.origin_conversation_id=?)
    ORDER BY r.id DESC LIMIT 1
  `).get(conversation, conversation);
  const exactEpisode = episode || closed;
  const workItemId = Number(exactEpisode?.work_item_id);
  const episodeId = Number(exactEpisode?.id);
  if (!Number.isSafeInteger(workItemId) || workItemId < 1
    || !Number.isSafeInteger(episodeId) || episodeId < 1) return [];
  try {
    const versions = artifactVersionsForEpisode(workItemId, episodeId);
    const receipts = versions.map((version) => artifactVersionReceipt(version.versionRef));
    const newestByPath = new Map();
    for (const receipt of [...receipts].reverse()) {
      if (!newestByPath.has(receipt.path)) newestByPath.set(receipt.path, receipt);
      if (newestByPath.size >= 12) break;
    }
    return [...newestByPath.values()].reverse();
  } catch {
    // Legacy state_json blijft hoogstens menselijke context elders; alleen de
    // immutable store mag pad-authority aan een vervolgbeurt geven.
    return [];
  }
}
/** @returns {any} */
export function episodeDeliveryOrigin(episodeId, channel = null) {
  const requestedChannel = clean(channel, 40);
  return db.prepare(`
    SELECT o.* FROM eva_episode_origins o
    JOIN eva_goal_episodes e ON e.id = o.episode_id
    WHERE o.episode_id = ?
      AND o.origin_conversation_id IS NOT NULL
      AND o.origin_conversation_id != ''
      AND (? = '' OR o.origin_channel = ?)
    ORDER BY CASE WHEN o.origin_conversation_id = e.origin_conversation_id THEN 0 ELSE 1 END,
      o.id ASC
    LIMIT 1
  `).get(Number(episodeId), requestedChannel, requestedChannel) || null;
}

function executionIntentEnvelopeValid(value) {
  return value && typeof value === 'object' && value.contract === DURABLE_EXECUTION_INTENT
    && SHA256.test(String(value.sha256 || ''))
    && SHA256.test(String(value.commitKey || ''))
    && SHA256.test(String(value.operationFingerprint || ''))
    && SHA256.test(String(value.episode?.definitionOfDoneSha256 || ''))
    && SHA256.test(String(value.episode?.requesterSha256 || ''))
    && SHA256.test(String(value.origin?.principalSha256 || ''))
    && SHA256.test(String(value.action?.boundArgsSha256 || ''))
    && verifyTurnUnderstandingRecord(value.goalSource?.understanding);
}

function executionIntentOperationValid(value) {
  return value.action?.effect === 'workspace' && value.action?.polarity === 'request'
    && value.execution?.mode === 'iterative'
    && value.execution?.target?.boundary === 'workspace'
    && value.execution?.target?.sameDirectory === true
    && value.rebind?.registry === 'current'
    && value.rebind?.policy === 'current'
    && value.rebind?.authority === 'current';
}

function executionIntentRecordValid(value) {
  const record = value.goalSource.understanding;
  const recordedActions = record.actions.filter((action) => (
    action.requirementId === value.action.requirementId
  ));
  const recordedAction = recordedActions[0];
  return SHA256.test(String(record.sourceSha256 || ''))
    && recordedActions.length === 1 && record.firstDecision?.kind === 'act'
    && record.firstDecision.requirementIds.includes(value.action.requirementId)
    && recordedAction.authorization === 'allowed'
    && recordedAction.capabilityId === value.action.capabilityId
    && recordedAction.operationId === value.action.operationId
    && recordedAction.polarity === value.action.polarity
    && recordedAction.effect === value.action.effect;
}

function executionIntentReferencesValid(value) {
  const materializable = value.execution.materializableFields;
  const proofOperations = value.execution.proofOperations;
  const recoveryOperations = value.execution.recoveryOperations;
  const references = Array.isArray(proofOperations) && Array.isArray(recoveryOperations)
    ? [...proofOperations, ...recoveryOperations] : [];
  return Array.isArray(materializable) && materializable.length > 0
    && !materializable.some((field) => Object.hasOwn(value.action.semanticInput || {}, field))
    && references.length > 1 && references.every((reference) => (
      /^[a-z][a-z0-9_.-]+$/u.test(String(reference?.capabilityId || ''))
      && /^[a-z0-9][a-z0-9_-]*$/u.test(String(reference?.operationId || ''))
      && /^[a-z][a-z0-9_-]*$/u.test(String(reference?.type || ''))
    ));
}

function executionIntentSupersessionValid(value) {
  const supersession = value.supersession || {};
  return (supersession.previousEventId == null && supersession.previousIntentSha256 == null)
    || (Number.isInteger(supersession.previousEventId) && supersession.previousEventId > 0
      && SHA256.test(String(supersession.previousIntentSha256 || '')));
}

function executionIntentHashValid(value) {
  if (!executionIntentEnvelopeValid(value) || !executionIntentOperationValid(value)) return false;
  const { sha256, ...body } = value;
  if (semanticHash(body) !== sha256) return false;
  return executionIntentRecordValid(value) && executionIntentReferencesValid(value)
    && executionIntentSupersessionValid(value);
}

function decodeExecutionIntentEvent(row) {
  const payload = json(row?.payload_json, null);
  const intent = payload?.intent;
  if (!executionIntentHashValid(intent)
    || row.transition_key !== `execution-intent:${intent.sha256}`) {
    throw new Error('duurzaam uitvoeringsvoornemen is beschadigd of niet hashgebonden');
  }
  return { eventId: Number(row.id), transitionKey: row.transition_key, intent };
}

/** @returns {any} */
function executionIntentHeadRow(episodeId) {
  return db.prepare(`SELECT * FROM eva_execution_intent_heads
    WHERE episode_id=? LIMIT 1`).get(Number(episodeId)) || null;
}

const EXECUTION_HEAD_BACKFILL_KEY = 'portfolio.execution_intent_heads.backfill.v1';

function backfillExecutionIntentHeads() {
  const episodeIds = db.prepare(`SELECT DISTINCT episode_id FROM eva_episode_events
    WHERE kind=? ORDER BY episode_id ASC`).all(EXECUTION_INTENT_EVENT);
  for (const item of episodeIds) {
    const episodeId = Number((/** @type {any} */ (item)).episode_id);
    if (executionIntentHeadRow(episodeId)) continue;
    try {
      const rows = db.prepare(`SELECT * FROM eva_episode_events
        WHERE episode_id=? AND kind=? ORDER BY id ASC`).all(episodeId, EXECUTION_INTENT_EVENT);
      let previous = null;
      for (const row of rows) {
        const decoded = decodeExecutionIntentEvent(row);
        const link = decoded.intent.supersession;
        if ((!previous && (link.previousEventId != null || link.previousIntentSha256 != null))
          || (previous && (link.previousEventId !== previous.eventId
            || link.previousIntentSha256 !== previous.intent.sha256))) {
          throw new Error('legacy supersession-keten is beschadigd');
        }
        previous = decoded;
      }
      if (previous) db.prepare(`INSERT INTO eva_execution_intent_heads
        (episode_id,work_item_id,event_id,intent_sha256,generation,active_effect_key,updated_ts)
        VALUES (?,?,?,?,?,NULL,?)`).run(
        episodeId, previous.intent.episode.workItemId, previous.eventId,
        previous.intent.sha256, rows.length, nowIso(),
      );
    } catch { /* corrupte legacy-events krijgen bewust geen hervatbare head */ }
  }
  db.prepare(`UPDATE eva_execution_intent_heads SET work_item_id=(
    SELECT work_item_id FROM eva_goal_episodes WHERE id=eva_execution_intent_heads.episode_id
  ) WHERE work_item_id IS NULL`).run();
}

// Dit is een eenmalige legacy-migratie, geen herstelmechanisme. Als een head
// later ontbreekt of wordt verwijderd, mag een restart nooit stil terugvallen
// op de laatste nog aanwezige event-tail.
db.transaction(() => {
  const migrated = db.prepare('SELECT value FROM kv WHERE key=? LIMIT 1')
    .get(EXECUTION_HEAD_BACKFILL_KEY);
  if (migrated) return;
  backfillExecutionIntentHeads();
  db.prepare('INSERT INTO kv (key,value) VALUES (?,?)').run(
    EXECUTION_HEAD_BACKFILL_KEY,
    JSON.stringify({ completedTs: nowIso() }),
  );
}).immediate();

function goalEpisodeTransitionValues(current, {
  nextState, deliveryPolicy, deliveryTarget, deliveryReceiptKey,
  nextAction, checkpoint, proofSummary, wakeTs,
}) {
  const nextPolicy = clean(deliveryPolicy || current.delivery_policy, 40);
  const currentReceiptKey = clean(deliveryReceiptKey, 240);
  const receiptAuthorizesThisTransition = currentReceiptKey && Boolean(db.prepare(`
    SELECT id FROM eva_delivery_receipts
    WHERE episode_id=? AND idempotency_key=? LIMIT 1
  `).get(current.id, currentReceiptKey));
  if (['closed', 'abandoned'].includes(nextState) && nextPolicy === 'required'
    && !episodeHasFinalDelivery(current.id) && !receiptAuthorizesThisTransition) {
    throw new Error(`episode ${current.id} vereist een afleveringbewijs vóór een terminale status`);
  }
  const terminal = ['closed', 'abandoned'].includes(nextState);
  const silentUserEpisode = current.origin_kind === 'user' && nextPolicy !== 'required';
  const nextDeliveryTarget = silentUserEpisode
    ? null
    : deliveryTarget == null
      ? current.delivery_target || (nextPolicy === 'required' ? current.requester : null)
      : clean(deliveryTarget, 160) || current.delivery_target;
  if (current.origin_kind === 'self' && nextPolicy === 'required' && !nextDeliveryTarget) {
    throw new Error('zelfgekozen verplichte aflevering vereist een expliciet afleverdoel');
  }
  return {
    terminal,
    values: {
      state: nextState,
      next_action: terminal ? '' : nextAction == null ? current.next_action : clean(nextAction, 1_000),
      checkpoint: checkpoint == null ? current.checkpoint : clean(checkpoint, 4_000),
      proof_summary: proofSummary == null ? current.proof_summary : clean(proofSummary, 4_000),
      wake_ts: terminal ? null : wakeTs == null ? current.wake_ts : clean(wakeTs, 100) || null,
      delivery_policy: nextPolicy,
      delivery_target: nextDeliveryTarget,
      closed_ts: terminal ? (current.closed_ts || nowIso()) : current.closed_ts,
    },
  };
}

function replayedGoalEpisodeTransition(current, transitionKey, intentSha256) {
  if (!transitionKey) return null;
  const replay = db.prepare(`SELECT id,payload_json FROM eva_episode_events
    WHERE episode_id=? AND transition_key=? LIMIT 1`).get(current.id, transitionKey);
  if (!replay) return null;
  const replayRow = /** @type {{payload_json?: unknown}} */ (replay);
  const replayPayload = /** @type {{intentSha256?: unknown}} */ (
    json(replayRow.payload_json, {})
  );
  const priorIntentSha256 = clean(
    replayPayload?.intentSha256, 64,
  );
  if (!priorIntentSha256 || priorIntentSha256 !== intentSha256) {
    throw new Error(`transition-key ${transitionKey} is al aan een andere overgang gebonden`);
  }
  return {
    episode: current,
    workItem: workRow(current.work_item_id),
    changed: false,
    replayed: true,
  };
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
  deliveryReceiptKey = null,
  work = /** @type {any} */ ({}),
} = {}) {
  const current = episodeRow(episodeId);
  if (!current) return null;
  const transitionKey = clean(idempotencyKey, 240) || null;
  const intentSha256 = semanticHash(transitionIntent({
    state, nextAction, checkpoint, proofSummary, wakeTs, deliveryPolicy,
    deliveryTarget, sessionRef, expectedRevision, eventKind, work,
  }));
  const replay = replayedGoalEpisodeTransition(current, transitionKey, intentSha256);
  if (replay) return replay;
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
  const { terminal, values } = goalEpisodeTransitionValues(current, {
    nextState, deliveryPolicy, deliveryTarget, deliveryReceiptKey,
    nextAction, checkpoint, proofSummary, wakeTs,
  });
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
      nextAction, checkpoint, proofSummary, wakeTs: values.wake_ts,
      workItemId: updated.work_item_id,
      intentSha256,
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
  const { deliveryReceiptKey: _privateDeliveryReceiptKey, ...publicOptions } = options;
  return db.transaction(() => transitionGoalEpisodeInTransaction(episodeId, publicOptions)).immediate();
}

// Enige runtime-ingang voor een bestaand werkpunt: gebonden werk volgt de
// episode atomair; alleen oud of nog ongebonden werk gebruikt de legacywriter.
export function updateDurableWork(workItemId, work = {}) {
  const episode = episodeForWork(workItemId);
  if (!episode) return updateWorkItem(workItemId, work);
  const currentWork = workRow(workItemId);
  const projectedStatus = EPISODE_WORK_STATUS[episode.state];
  const requestedStatus = clean(work.status, 20);
  const state = !requestedStatus || requestedStatus === projectedStatus
    ? episode.state
    : ({ open: 'executing', blocked: 'blocked', done: 'closed', cancelled: 'abandoned' })[requestedStatus];
  if (!state) throw new Error(`ongeldige werkstatus: ${requestedStatus}`);
  if (state === 'closed'
    && (work.proofAccepted !== true || !clean(work.evidence || currentWork?.evidence))) {
    throw new Error('werk mag pas klaar met geaccepteerd en geverifieerd resultaatbewijs');
  }
  return transitionGoalEpisode(episode.id, {
    state,
    nextAction: work.nextStep,
    checkpoint: work.evidence,
    proofSummary: work.evidence,
    wakeTs: work.nextWakeTs,
    work,
  })?.workItem || null;
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

function requiredTelegramTransportProof({
  transport, pinnedConversation, originMessageId, resultText, idempotencyKey,
  messageThreadId, originPrincipal,
}) {
  const expectedChatId = String(pinnedConversation || '').replace(/^telegram:/iu, '');
  const validated = validateTelegramDeliveryProof({
    delivery: transport,
    kind: 'text',
    value: resultText,
    deliveryKey: idempotencyKey,
    expectedChatId,
    broadcast: false,
    directReply: true,
    messageThreadId,
    replyToMessageId: originMessageId,
    originPrincipal,
  });
  const expectedTurnRef = originMessageId
    ? `turn:telegram:${expectedChatId}:${String(originMessageId)}` : '';
  if (validated.deliveryKey !== idempotencyKey
    || (expectedTurnRef && validated.directTurnSignalRef !== expectedTurnRef)) {
    throw new Error('Telegramaflevering hoort niet bij deze episode of afleveringssleutel');
  }
  return { ...validated.proof, maxAcceptedAt: validated.maxAcceptedAt };
}

export function requiredTelegramOriginBinding(pinnedConversation, originMessageId) {
  const chatId = String(pinnedConversation || '').replace(/^telegram:/iu, '');
  const messageId = clean(originMessageId, 160);
  if (!chatId || !messageId) {
    throw new Error('Required Telegramaflevering mist een volledige gepinde inbound-oorsprong');
  }
  const inbound = telegramMessage(chatId, messageId);
  if (!inbound) {
    throw new Error('Required Telegramaflevering mist het gepinde inboundbewijs');
  }
  const meta = inbound.meta && typeof inbound.meta === 'object' ? inbound.meta : {};
  if (inbound.role !== 'user' || inbound.deleted || meta.inbound !== true) {
    throw new Error('Gepind Telegram-inboundbewijs is niet intact');
  }
  const fromTelegramId = Number(meta.fromTelegramId);
  if (!Number.isSafeInteger(fromTelegramId) || fromTelegramId <= 0) {
    throw new Error('Oorspronkelijke Telegramprincipal is ongeldig');
  }
  const originPrincipal = `telegram:${fromTelegramId}`;
  const rawThread = meta.messageThreadId;
  const messageThreadId = rawThread == null || rawThread === '' ? null : Number(rawThread);
  if (messageThreadId != null
    && (!Number.isSafeInteger(messageThreadId) || messageThreadId <= 0)) {
    throw new Error('Oorspronkelijke Telegramthread is ongeldig');
  }
  const preparedThread = meta.preparedReply?.payload?.messageThreadId;
  if (preparedThread != null && Number(preparedThread) !== messageThreadId) {
    throw new Error('Voorbereid antwoord strijdt met de oorspronkelijke Telegramthread');
  }
  if (meta.preparedReply
    && (String(meta.preparedReply.fromTelegramId || '') !== String(fromTelegramId)
      || meta.preparedReply.originPrincipal !== originPrincipal)) {
    throw new Error('Voorbereid antwoord strijdt met de oorspronkelijke Telegramprincipal');
  }
  const archiveRowId = Number(inbound.id);
  if (!Number.isSafeInteger(archiveRowId) || archiveRowId < 1) {
    throw new Error('Oorspronkelijke Telegramarchiefrij is ongeldig');
  }
  const originProofSha256 = createHash('sha256').update(canonicalJson({
    contract: 'MonitoringTelegramOriginProof/v1',
    archiveRowId,
    conversationId: `telegram:${chatId}`,
    messageId,
    originPrincipal,
    messageThreadId,
    inbound: true,
  })).digest('hex');
  return { messageThreadId, originPrincipal, archiveRowId, originProofSha256 };
}

function exactMonitoringSemanticBinding({
  monitoring, requirementId, addressesRequirementIds = [requirementId],
  basisReceiptIds = [], capabilityId, operationId, role, executionType = null,
}) {
  return Object.freeze({
    understandingSha256: monitoring.sourceUnderstandingSha256,
    requirementId,
    addressesRequirementIds: Object.freeze([...addressesRequirementIds]),
    basisReceiptIds: Object.freeze([...basisReceiptIds]),
    capabilityId,
    operationId,
    role,
    executionType,
  });
}

function monitoringIssuanceBindingSpecs(understanding, sourceText) {
  const overlaps = (left, right) => Number.isInteger(left?.start)
    && Number.isInteger(left?.end) && Number.isInteger(right?.start)
    && Number.isInteger(right?.end) && left.start < right.end && right.start < left.end;
  const requirementIds = Array.isArray(understanding?.monitoring?.sourceEntityReadRequirementIds)
    ? understanding.monitoring.sourceEntityReadRequirementIds.map(String) : [];
  if (!requirementIds.length || requirementIds.length > 8
    || new Set(requirementIds).size !== requirementIds.length) return null;
  const requested = new Set(requirementIds);
  const actions = new Map(requirementIds.map((requirementId) => [
    requirementId,
    understanding.actions.find((candidate) => (
      candidate.requirementId === requirementId
      && candidate.capabilityId === 'home.entity.read'
      && candidate.operationId === 'read'
      && candidate.polarity === 'request'
      && candidate.authorization === 'allowed'
    )),
  ]));
  if ([...actions.values()].some((action) => !action)) return null;
  const conditions = (Array.isArray(understanding?.conditions)
    ? understanding.conditions : []).filter((condition) => condition?.role === 'guard');
  if (!conditions.length || conditions.length > 8) return null;
  const conditionRequirements = new Map();
  for (const condition of conditions) {
    const declared = Array.isArray(condition?.requirementIds)
      ? condition.requirementIds.map(String) : [];
    const ids = declared.length ? declared : requirementIds.filter((requirementId) => {
      const action = actions.get(requirementId);
      return Array.isArray(condition?.sourceSpans) && Array.isArray(action?.sourceSpans)
        && condition.sourceSpans.some((conditionSpan) => (
          action.sourceSpans.some((actionSpan) => overlaps(conditionSpan, actionSpan))
        ));
    });
    if (!ids.length || new Set(ids).size !== ids.length
      || ids.some((requirementId) => !requested.has(requirementId))) return null;
    conditionRequirements.set(condition, ids);
  }
  const conditionByRequirement = new Map();
  for (const requirementId of requirementIds) {
    const matches = conditions.filter((condition) => (
      conditionRequirements.get(condition).includes(requirementId)
    ));
    if (matches.length !== 1) return null;
    conditionByRequirement.set(requirementId, matches[0]);
  }
  if (new Set(conditionByRequirement.values()).size !== conditions.length) return null;
  const references = Array.isArray(understanding.monitoring.sourceReferenceTexts)
    && understanding.monitoring.sourceReferenceTexts.length === requirementIds.length
    ? understanding.monitoring.sourceReferenceTexts.map((value) => String(value || '').trim())
    : null;
  const specs = requirementIds.map((requirementId, index) => {
    const action = actions.get(requirementId);
    const spanText = (Array.isArray(action?.sourceSpans) ? action.sourceSpans : [])
      .flatMap((span) => Number.isInteger(span?.start) && Number.isInteger(span?.end)
        && span.start >= 0 && span.end > span.start && span.end <= sourceText.length
        ? [sourceText.slice(span.start, span.end)] : [])
      .join(' ').trim();
    const exactSource = references?.[index] || spanText;
    const condition = conditionByRequirement.get(requirementId);
    const peers = conditionRequirements.get(condition);
    const peerIndex = peers.indexOf(requirementId);
    const operand = peers.length === 1 ? 'subject'
      : peers.length === 2 ? peerIndex === 0 ? 'left' : 'right' : null;
    const statement = String(condition?.statement || '').trim();
    if (!exactSource || !statement || !operand) return null;
    return Object.freeze({
      requirementId, condition: statement, conditionRole: 'guard', operand,
      sourceText: exactSource,
    });
  });
  return specs.some((spec) => !spec) ? null : Object.freeze(specs);
}

function monitoringIssuanceSemanticAuthority({
  monitoring, understanding, specs, turnId,
}) {
  const discoveryRequirementId = String(
    understanding?.monitoring?.sourceDiscoveryRequirementId || '',
  ).trim();
  if (!discoveryRequirementId) {
    return monitoring.discoveryQuery == null ? Object.freeze({
      role: 'primary', basisReceiptIds: Object.freeze([]), entityByRequirement: null,
      discoveryExecutionSeq: null,
    }) : null;
  }
  const discoveryAction = understanding.actions.find((action) => (
    action.requirementId === discoveryRequirementId
    && action.capabilityId === 'home.context.read'
    && action.operationId === 'read'
    && action.polarity === 'request'
    && action.authorization === 'allowed'
  ));
  if (!discoveryAction || typeof monitoring.discoveryQuery !== 'string'
    || !monitoring.discoveryQuery) return null;
  let receiptRows;
  try {
    receiptRows = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE turn_id=? AND semantic_binding IS NOT NULL
      ORDER BY execution_seq ASC`).all(turnId).map((candidate) => ({
      row: validateExecutionRecord(candidate),
      args: JSON.parse(String(candidate.args || 'null')),
      semanticBinding: JSON.parse(String(candidate.semantic_binding || 'null')),
      output: JSON.parse(String(candidate.output || 'null')),
    }));
  } catch { return null; }
  const expectedDiscoveryBinding = exactMonitoringSemanticBinding({
    monitoring,
    requirementId: discoveryRequirementId,
    capabilityId: 'home.context.read',
    operationId: 'read',
    role: 'primary',
  });
  const discoveries = receiptRows.filter(({ row, args, semanticBinding, output }) => (
    row.status === 'done' && row.tool === 'huis_status'
    && canonicalJson(Object.keys(args || {}).sort()) === canonicalJson(['vraag'])
    && args.vraag === monitoring.discoveryQuery
    && canonicalJson(semanticBinding) === canonicalJson(expectedDiscoveryBinding)
    && output?.contract === 'ActionResult/v1' && output.tool === 'huis_status'
    && output.ok === true && output.verified === true && output.changed === false
    && output.partial !== true && output.needs_followup !== true
    && output.effect_uncertain !== true && Array.isArray(output.data?.entities)
  ));
  if (discoveries.length !== 1) return null;
  const discovery = discoveries[0];
  const discoveryIndex = receiptRows.findIndex((candidate) => candidate.row === discovery.row);
  if (discoveryIndex < 0) return null;
  const resolved = bindHomeContextEntityReferences(
    specs.map((spec) => ({
      requirementId: spec.requirementId, sourceText: spec.sourceText,
    })),
    discovery.output.data.entities,
  );
  if (!resolved || resolved.length !== specs.length) return null;
  return Object.freeze({
    role: 'monitoring_binding',
    basisReceiptIds: Object.freeze([`s${discoveryIndex + 1}`]),
    entityByRequirement: new Map(resolved.map((binding) => (
      [binding.requirementId, binding.entityId]
    ))),
    discoveryExecutionSeq: Number(discovery.row.execution_seq),
  });
}

function exactMonitoringContractStartAuthority({ monitoring, understanding, turnId, workId }) {
  const startActions = understanding.actions.filter((action) => (
    action.capabilityId === 'work.episode.manage'
    && action.operationId === 'start'
    && action.polarity === 'request'
    && action.authorization === 'allowed'
  ));
  if (startActions.length !== 1) return false;
  const [startAction] = startActions;
  const expectedSemanticBinding = exactMonitoringSemanticBinding({
    monitoring,
    requirementId: startAction.requirementId,
    capabilityId: 'work.episode.manage',
    operationId: 'start',
    role: 'primary',
  });
  let candidates;
  try {
    candidates = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE turn_id=? AND tool='werk_bijhouden' AND status='done'
      ORDER BY execution_seq ASC`).all(turnId).flatMap((candidate) => {
      const row = validateExecutionRecord(candidate);
      const args = JSON.parse(String(row.args || 'null'));
      const semanticBinding = JSON.parse(String(row.semantic_binding || 'null'));
      const output = JSON.parse(String(row.output || 'null'));
      if (canonicalJson(semanticBinding) !== canonicalJson(expectedSemanticBinding)
        || args?.bewerking !== 'start'
        || output?.contract !== 'ActionResult/v1'
        || output.tool !== 'werk_bijhouden'
        || output.ok !== true || output.verified !== true || output.changed !== true
        || output.partial === true || output.needs_followup === true
        || output.effect_uncertain === true
        || Number(output.data?.item?.id) !== Number(workId)) return [];
      let issuedState;
      try { issuedState = JSON.parse(String(args.toestand_json || 'null')); }
      catch { return []; }
      const issuedMonitoring = normalizeMonitoringContract(issuedState?.monitoring);
      return issuedState && typeof issuedState === 'object' && !Array.isArray(issuedState)
        && !Object.keys(issuedState).some((key) => (
          MONITORING_START_RESERVED_STATE_KEYS.has(key)
        ))
        && issuedMonitoring
        && issuedMonitoring.contractSha256 === monitoring.contractSha256
        && canonicalJson(issuedMonitoring) === canonicalJson(monitoring)
        ? [{ row }] : [];
    });
  } catch { return false; }
  return candidates.length === 1;
}

function exactMonitoringIssuanceBindings({ monitoring, understanding, sourceText, turnId }) {
  const bindings = monitoringContractReadBindings(monitoring);
  const specs = monitoringIssuanceBindingSpecs(understanding, sourceText);
  if (!bindings || !specs || bindings.length !== specs.length) return false;
  const semanticAuthority = monitoringIssuanceSemanticAuthority({
    monitoring, understanding, specs, turnId,
  });
  if (!semanticAuthority) return false;
  const specByRequirement = new Map(specs.map((spec) => [spec.requirementId, spec]));
  const issuedKeys = new Set();
  const issuedCompletionHashes = new Set();
  const expectedMaxAgeMs = Math.max(
    15 * 60_000, Math.min(60 * 60_000, monitoring.cadenceMs * 3),
  );
  for (const binding of bindings) {
    const spec = specByRequirement.get(binding.requirementId);
    if (!spec || issuedKeys.has(binding.issuedExecutionKey)
      || issuedCompletionHashes.has(binding.issuedCompletionRecordSha256)) return false;
    let row;
    let args;
    let semanticBinding;
    let output;
    try {
      row = validateExecutionRecord(db.prepare(`SELECT *
        FROM main.turn_tool_executions WHERE execution_key=?`).get(
        binding.issuedExecutionKey,
      ));
      args = JSON.parse(String(row.args || 'null'));
      semanticBinding = JSON.parse(String(row.semantic_binding || 'null'));
      output = JSON.parse(String(row.output || 'null'));
    } catch { return false; }
    const expectedSemanticBinding = exactMonitoringSemanticBinding({
      monitoring,
      requirementId: binding.requirementId,
      basisReceiptIds: semanticAuthority.basisReceiptIds,
      capabilityId: 'home.entity.read',
      operationId: 'read',
      role: semanticAuthority.role,
    });
    if (row.status !== 'done' || row.tool !== 'ha_lees' || row.turn_id !== turnId
      || row.execution_key !== binding.issuedExecutionKey
      || row.completion_record_sha256 !== binding.issuedCompletionRecordSha256
      || canonicalJson(Object.keys(args || {}).sort()) !== canonicalJson(['entity_id'])
      || typeof args.entity_id !== 'string'
      || (semanticAuthority.entityByRequirement != null
        && semanticAuthority.entityByRequirement.get(binding.requirementId) !== args.entity_id)
      || (semanticAuthority.discoveryExecutionSeq != null
        && Number(row.execution_seq) <= semanticAuthority.discoveryExecutionSeq)
      || canonicalJson(semanticBinding) !== canonicalJson(expectedSemanticBinding)
      || output?.contract !== 'ActionResult/v1' || output.tool !== 'ha_lees'
      || output.ok !== true || output.verified !== true || output.changed !== false
      || output.partial === true || output.needs_followup === true
      || output.effect_uncertain === true || output.data?.entity_id !== args.entity_id) return false;
    const receipt = {
      name: 'ha_lees', args, output: String(row.output),
      ok: output.ok, verified: output.verified, changed: output.changed,
      partial: output.partial === true, needs_followup: output.needs_followup === true,
      effect_uncertain: output.effect_uncertain === true,
      semanticBinding, at: row.finished_ts,
    };
    const expectedBinding = issueMonitoringFactBinding({
      requirementId: spec.requirementId,
      entityId: args.entity_id,
      name: output.data?.name || args.entity_id,
      condition: spec.condition,
      conditionRole: spec.conditionRole,
      operand: spec.operand,
      sourceText: spec.sourceText,
      sourceUnderstandingSha256: monitoring.sourceUnderstandingSha256,
      freshness: { maxAgeMs: expectedMaxAgeMs },
      receipt,
      verifyInitialReceipt: (candidate) => candidate === receipt ? {
        executionKey: row.execution_key,
        intentRecordSha256: row.intent_record_sha256,
        outputSha256: row.output_sha256,
        completionRecordSha256: row.completion_record_sha256,
        finishedTs: row.finished_ts,
      } : null,
    });
    if (!expectedBinding || canonicalJson(expectedBinding) !== canonicalJson(binding)) return false;
    issuedKeys.add(binding.issuedExecutionKey);
    issuedCompletionHashes.add(binding.issuedCompletionRecordSha256);
  }
  return issuedKeys.size === bindings.length && specByRequirement.size === bindings.length;
}

function reprojectMonitoringFact({ factReference, binding, monitoring, now }) {
  if (factReference.bindingSha256 !== binding.bindingSha256
    || factReference.requirementId !== binding.requirementId) return null;
  let row;
  let args;
  let semanticBinding;
  let output;
  try {
    row = validateExecutionRecord(db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(factReference.executionKey));
    args = JSON.parse(String(row.args || 'null'));
    semanticBinding = JSON.parse(String(row.semantic_binding || 'null'));
    output = JSON.parse(String(row.output || 'null'));
  } catch { return null; }
  const expectedSemanticBinding = exactMonitoringSemanticBinding({
    monitoring,
    requirementId: binding.requirementId,
    capabilityId: 'home.entity.read',
    operationId: 'read',
    role: 'monitoring_observation',
  });
  if (row.status !== 'done' || row.tool !== 'ha_lees'
    || row.completion_record_sha256 !== factReference.completionRecordSha256
    || canonicalJson(Object.keys(args || {}).sort()) !== canonicalJson(['entity_id'])
    || args?.entity_id !== binding.entityId
    || canonicalJson(semanticBinding) !== canonicalJson(expectedSemanticBinding)
    || output?.contract !== 'ActionResult/v1' || output.tool !== 'ha_lees'
    || output.ok !== true || output.verified !== true || output.changed !== false
    || output.partial === true || output.needs_followup === true
    || output.effect_uncertain === true || output.data?.entity_id !== binding.entityId) return null;
  const receipt = {
    name: 'ha_lees', args, output: String(row.output),
    ok: output.ok, verified: output.verified, changed: output.changed,
    partial: output.partial === true, needs_followup: output.needs_followup === true,
    effect_uncertain: output.effect_uncertain === true,
    semanticBinding, at: row.finished_ts,
  };
  const fact = buildMonitoringMeasurementFact(binding, receipt, {
    now: Date.parse(now), sourceUnderstandingSha256: monitoring.sourceUnderstandingSha256,
    verifyLedgerReceipt: () => ({
      executionKey: row.execution_key,
      intentRecordSha256: row.intent_record_sha256,
      outputSha256: row.output_sha256,
      completionRecordSha256: row.completion_record_sha256,
      finishedTs: row.finished_ts,
    }),
  });
  return fact && fact.factId === factReference.factId ? { fact, row } : null;
}

function exactMonitoringObservationRows(selectedRows) {
  const factTurnIds = new Set(selectedRows.map((row) => String(row.turn_id || '')));
  if (factTurnIds.size !== 1) return null;
  const factTurnId = [...factTurnIds][0];
  let rows;
  try {
    rows = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE turn_id=? AND tool='ha_lees' AND status='done'
      ORDER BY execution_seq ASC`).all(factTurnId).flatMap((candidate) => {
      const row = validateExecutionRecord(candidate);
      const semanticBinding = JSON.parse(String(row.semantic_binding || 'null'));
      return semanticBinding?.role === 'monitoring_observation' ? [row] : [];
    });
  } catch { return null; }
  return rows.length === selectedRows.length && rows.every((row, index) => (
    row.execution_key === selectedRows[index].execution_key
  )) ? factTurnId : null;
}

function reprojectMonitoringOutcome({
  reference, monitoring, conditionContext, facts, bindings, factTurnId,
}) {
  let row;
  let args;
  let semanticBinding;
  let output;
  try {
    row = validateExecutionRecord(db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(reference.outcomeRef.executionKey));
    args = JSON.parse(String(row.args || 'null'));
    semanticBinding = JSON.parse(String(row.semantic_binding || 'null'));
    output = JSON.parse(String(row.output || 'null'));
  } catch { return null; }
  const outcome = normalizeMonitoringOutcomeDecision(output?.outcome, facts);
  const expectedSemanticBinding = exactMonitoringSemanticBinding({
    monitoring,
    requirementId: bindings[0].requirementId,
    addressesRequirementIds: bindings.map((binding) => binding.requirementId),
    capabilityId: 'monitoring.outcome',
    operationId: 'decide',
    role: 'monitoring_outcome',
    executionType: 'host_bound',
  });
  const expectedExecutionKey = `monitoring-outcome:${createHash('sha256')
    .update(canonicalJson({
      turnId: factTurnId,
      monitoringId: monitoring.monitoringId,
      factIds: outcome?.factIds || [],
      attempt: args?.attempt,
    })).digest('hex')}`;
  const expectedRequest = monitoringOutcomeRequestRecord({
    monitoring, conditionContext, facts, attempt: args?.attempt,
  });
  const expectedResponse = monitoringOutcomeResponseRecord({
    requestSha256: expectedRequest?.requestSha256,
    accepted: true, outcome, facts, failure: null,
  });
  if (!outcome || row.status !== 'done' || row.tool !== '@monitoring/outcome'
    || row.turn_id !== factTurnId || row.execution_key !== expectedExecutionKey
    || row.completion_record_sha256 !== reference.outcomeRef.completionRecordSha256
    || canonicalJson(Object.keys(args || {}).sort()) !== canonicalJson([
      'attempt', 'factIds', 'monitoringContractSha256', 'monitoringId', 'requestSha256',
    ])
    || !Number.isSafeInteger(args.attempt) || args.attempt < 1 || args.attempt > 2
    || !expectedRequest || args.requestSha256 !== expectedRequest.requestSha256
    || args?.monitoringId !== monitoring.monitoringId
    || args?.monitoringContractSha256 !== monitoring.contractSha256
    || canonicalJson(args.factIds) !== canonicalJson(outcome.factIds)
    || canonicalJson(semanticBinding) !== canonicalJson(expectedSemanticBinding)
    || canonicalJson(Object.keys(output || {}).sort()) !== canonicalJson([
      'accepted', 'contract', 'failure', 'outcome', 'responseSha256',
    ])
    || output?.contract !== 'MonitoringOutcomeModelRecord/v1'
    || output.accepted !== true || output.failure !== null
    || !expectedResponse || output.responseSha256 !== expectedResponse.responseSha256) return null;
  const outcomeReceipt = issueMonitoringOutcomeReceipt({
    outcome, executionKey: row.execution_key,
    intentRecordSha256: row.intent_record_sha256,
    outputSha256: row.output_sha256,
    completionRecordSha256: row.completion_record_sha256,
    finishedAt: row.finished_ts,
  });
  if (!outcomeReceipt
    || outcomeReceipt.outcomeReceiptSha256 !== reference.outcomeRef.outcomeReceiptSha256
    || outcomeReceipt.outcomeSha256 !== reference.outcomeRef.outcomeSha256) return null;
  return { outcome, outcomeReceipt };
}

function reprojectMonitoringPending({
  monitoring, lifecycle, episode, work, pending, now, conditionContext,
}) {
  const expected = {
    episodeId: Number(episode.id), workId: Number(work.id),
    dedupeKey: `episode:${episode.id}:monitoring-final`,
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
  };
  const reference = normalizeMonitoringPendingDeliveryReference(
    pending, monitoring, expected,
  );
  const bindings = monitoringContractReadBindings(monitoring);
  const references = monitoringPendingFactReferences(reference);
  if (!reference || !bindings || !references || bindings.length !== references.length) return null;
  const facts = [];
  const selectedRows = [];
  for (let index = 0; index < references.length; index += 1) {
    const factReference = references[index];
    const binding = bindings[index];
    const reprojected = reprojectMonitoringFact({ factReference, binding, monitoring, now });
    if (!reprojected) return null;
    facts.push(reprojected.fact);
    selectedRows.push(reprojected.row);
  }
  const factTurnId = exactMonitoringObservationRows(selectedRows);
  if (!factTurnId) return null;
  const reprojectedOutcome = reprojectMonitoringOutcome({
    reference, monitoring, conditionContext, facts, bindings, factTurnId,
  });
  if (!reprojectedOutcome) return null;
  const { outcome, outcomeReceipt } = reprojectedOutcome;
  const normalized = normalizeMonitoringPendingDelivery(reference, monitoring, {
    ...expected, now, facts, outcome, outcomeReceipt,
  });
  return normalized ? Object.freeze({
    pending: normalized, facts: Object.freeze(facts), outcome, outcomeReceipt,
  }) : null;
}

function monitoringLifecycleOriginMatches(origin, episode, startAuthority) {
  return Boolean(startAuthority
    && origin.origin_channel === startAuthority.channel
    && origin.origin_conversation_id === startAuthority.conversationId
    && String(origin.origin_message_id || '') === startAuthority.messageId
    && episode.origin_channel === startAuthority.channel
    && episode.origin_conversation_id === startAuthority.conversationId
    && String(episode.origin_message_id || '') === startAuthority.messageId);
}

function monitoringStartAuthorityMatches(authority, startAuthority, row, args) {
  return Boolean(authority && row.status === 'done' && row.tool === '@turn/understanding'
    && authority.executionKey === startAuthority.executionKey
    && Number(authority.executionSeq) === startAuthority.executionSeq
    && authority.intentRecordSha256 === startAuthority.intentRecordSha256
    && authority.authoritySha256 === startAuthority.authoritySha256
    && authority.channel === startAuthority.channel
    && authority.conversationId === startAuthority.conversationId
    && String(authority.messageId || '') === startAuthority.messageId
    && authority.sourceKind === startAuthority.sourceKind
    && authority.sourceRef === startAuthority.sourceRef
    && authority.sourceSha256 === startAuthority.sourceSha256
    && Number(authority.sourceGeneration) === startAuthority.sourceGeneration
    && args?.channel === startAuthority.channel
    && args?.conversationId === startAuthority.conversationId
    && String(args?.messageId || '') === startAuthority.messageId);
}

function monitoringStartAuthorityContext(startAuthority, monitoring, work, episode) {
  let row;
  let authority;
  let args;
  let prepared;
  try {
    row = validateExecutionRecord(db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(startAuthority.executionKey));
    authority = turnUnderstandingAuthorityRecord(db, row);
    args = JSON.parse(String(row.args || 'null'));
    prepared = verifyPreparedUnderstandingTurn(JSON.parse(String(row.output || 'null')), {
      channel: startAuthority.channel,
      conversationId: startAuthority.conversationId,
      messageId: startAuthority.messageId,
    });
  } catch { return null; }
  if (!monitoringStartAuthorityMatches(authority, startAuthority, row, args)
    || createHash('sha256').update(String(args?.originalText || '')).digest('hex')
      !== monitoring.presentation.sourceTextSha256
    || !prepared || prepared.workItemId !== Number(work.id)
    || prepared.goalEpisodeId !== Number(episode.id)) return null;
  return { args, prepared };
}

function monitoringDeliveryRoute(monitoring, startAuthority, episode) {
  if (monitoring.delivery.deliveryRequired !== true) return Object.freeze({ route: 'none' });
  const expected = startAuthority;
  const archived = requiredTelegramOriginBinding(expected.conversationId, expected.messageId);
  if (startAuthority.channel !== 'telegram'
    || archived.archiveRowId !== expected.archiveRowId
    || archived.originPrincipal !== expected.principal
    || archived.messageThreadId !== expected.messageThreadId
    || episode.origin_kind !== 'user' || episode.delivery_policy !== 'required'
    || !monitoringDeliveryMatchesAuthority(monitoring, {
      channel: expected.channel,
      conversationId: expected.conversationId,
      messageId: expected.messageId,
      principal: expected.principal,
      messageThreadId: expected.messageThreadId,
      sourceRef: expected.sourceRef,
      sourceSha256: expected.sourceSha256,
      sourceGeneration: expected.sourceGeneration,
      archiveRowId: expected.archiveRowId,
      executionKey: expected.executionKey,
      executionSeq: expected.executionSeq,
      intentRecordSha256: expected.intentRecordSha256,
      authoritySha256: expected.authoritySha256,
      originProofSha256: expected.originProofSha256,
    })) return null;
  return Object.freeze({
    route: 'direct_telegram', chatId: expected.conversationId.replace(/^telegram:/u, ''),
    conversationId: expected.conversationId, messageId: expected.messageId,
    principal: expected.principal, messageThreadId: expected.messageThreadId,
  });
}

function monitoringUnderstandingContext({ prepared, startAuthority, monitoring, args, work }) {
  const rows = /** @type {any[]} */ (db.prepare(`SELECT * FROM main.timeline
    WHERE kind='turn_understanding' AND meta IS NOT NULL AND json_valid(meta)
      AND json_extract(meta, '$.turnId')=?
      AND json_extract(meta, '$.conversationId')=?
    ORDER BY id ASC`).all(prepared.turnId, startAuthority.conversationId));
  if (rows.length !== 1) return null;
  let meta;
  try { meta = JSON.parse(String(rows[0].meta || 'null')); } catch { return null; }
  const understanding = meta?.understanding;
  if (meta?.interpretationSucceeded !== true || meta.turnId !== prepared.turnId
    || meta.conversationId !== startAuthority.conversationId
    || !verifyTurnUnderstandingRecord(understanding)
    || understanding.sourceSha256 !== monitoring.sourceUnderstandingSha256) return null;
  const conditionContext = understanding.conditions.map((condition) => ({
    statement: String(condition.statement || ''), role: String(condition.role || ''),
  }));
  const conditionIds = conditionContext.map((condition) => (
    monitoringConditionId(condition.statement, condition.role)
  ));
  if (conditionIds.some((conditionId) => !conditionId)
    || canonicalJson(conditionIds) !== canonicalJson(monitoring.conditions)
    || !exactMonitoringContractStartAuthority({
      monitoring, understanding, turnId: prepared.turnId, workId: Number(work.id),
    })
    || !exactMonitoringIssuanceBindings({
      monitoring, understanding, sourceText: String(args.originalText || ''),
      turnId: prepared.turnId,
    })) return null;
  return { understandingRecord: understanding, conditionContext };
}

function verifyMonitoringLifecycleAuthorityAt({
  workId, episodeId, pending = null, expectedPendingSha256 = null,
} = {}, verificationAt) {
  try {
    const verificationMs = Date.parse(String(verificationAt || ''));
    if (!Number.isFinite(verificationMs)
      || new Date(verificationMs).toISOString() !== verificationAt) return null;
    const work = workRow(Number(workId));
    const episode = episodeRow(Number(episodeId));
    if (!work || !episode || Number(episode.work_item_id) !== Number(work.id)) return null;
    const state = json(work.state_json, null);
    const monitoring = normalizeMonitoringContract(state?.monitoring);
    if (!monitoring) return null;
    const origins = /** @type {any[]} */ (db.prepare(`SELECT * FROM eva_episode_origins
      WHERE episode_id=? ORDER BY id ASC`).all(Number(episode.id)));
    if (origins.length !== 1) return null;
    const origin = origins[0];
    const lifecycle = normalizeMonitoringLifecycleBinding(
      state?.monitoring_lifecycle, monitoring, {
        episodeId: Number(episode.id), episodeKey: String(episode.episode_key || ''),
        workId: Number(work.id), originRowId: Number(origin.id),
        originKey: String(origin.origin_key || ''),
        minimumEpisodeRevision: Number(episode.revision || 0),
      },
    );
    const startAuthority = lifecycle?.originAuthority;
    if (!lifecycle || !monitoringLifecycleOriginMatches(origin, episode, startAuthority)) return null;
    const startContext = monitoringStartAuthorityContext(
      startAuthority, monitoring, work, episode,
    );
    if (!startContext) return null;
    const { args, prepared } = startContext;
    const understandingContext = monitoringUnderstandingContext({
      prepared, startAuthority, monitoring, args, work,
    });
    if (!understandingContext) return null;
    const { understandingRecord, conditionContext } = understandingContext;
    const delivery = monitoringDeliveryRoute(monitoring, startAuthority, episode);
    if (!delivery) return null;
    const pendingCandidate = expectedPendingSha256 == null ? pending : state?.pending_delivery;
    const reprojected = pendingCandidate == null ? null : reprojectMonitoringPending({
      monitoring, lifecycle, episode, work, pending: pendingCandidate,
      now: verificationAt, conditionContext,
    });
    const expectedPending = reprojected?.pending || null;
    if (pendingCandidate != null && !reprojected) return null;
    if (expectedPendingSha256 != null
      && expectedPending?.pendingSha256 !== expectedPendingSha256) return null;
    return Object.freeze({
      work, episode, monitoring, lifecycle, pending: expectedPending, delivery,
      facts: reprojected?.facts || null,
      outcome: reprojected?.outcome || null,
      outcomeReceipt: reprojected?.outcomeReceipt || null,
      conditionContext: Object.freeze(conditionContext.map(Object.freeze)),
      understandingRecord,
    });
  } catch { return null; }
}

export function verifyMonitoringLifecycleAuthority(options = {}) {
  return verifyMonitoringLifecycleAuthorityAt(options, new Date().toISOString());
}

function monitoringDeliveryReceiptBinding(authority) {
  const pending = authority?.pending;
  return pending ? Object.freeze({
    contract: 'MonitoringDeliveryReceiptBinding/v1',
    monitoringContractSha256: authority.monitoring.contractSha256,
    pendingSha256: pending.pendingSha256,
    lifecycleSha256: authority.lifecycle.lifecycleSha256,
    lifecycleGeneration: authority.lifecycle.generation,
    originBindingSha256: authority.lifecycle.originBindingSha256,
    outcomeReceiptSha256: pending.outcomeRef.outcomeReceiptSha256,
    outcomeSha256: pending.outcomeRef.outcomeSha256,
    factSetSha256: pending.factSetSha256,
    textSha256: pending.textSha256,
    rendererVersion: pending.rendererVersion,
    dedupeKey: pending.dedupeKey,
  }) : null;
}

export function acceptedMonitoringDeliveryReceiptCandidate({ workId, episodeId } = {}) {
  try {
    const work = workRow(Number(workId));
    const episode = episodeRow(Number(episodeId));
    const state = json(work?.state_json, null);
    const pending = state?.pending_delivery;
    if (!work || !episode || Number(episode.work_item_id) !== Number(work.id)
      || episode.state !== 'delivery_pending'
      || pending?.contract !== 'MonitoringPendingDelivery/v2'
      || !clean(pending.dedupeKey, 240) || typeof pending.text !== 'string') return null;
    const deliveryKey = `prepared:${pending.dedupeKey}`;
    const fanoutExecutionKey = `telegram-fanout:${createHash('sha256').update(canonicalJson({
      contract: 'TelegramFanoutOperation/v1', deliveryKey,
    })).digest('hex')}`;
    const fanoutRow = validateExecutionRecord(db.prepare(`SELECT *
      FROM main.turn_tool_executions WHERE execution_key=?`).get(fanoutExecutionKey));
    let fanoutOutput;
    try { fanoutOutput = JSON.parse(String(fanoutRow?.output || 'null')); }
    catch { return null; }
    if (fanoutRow?.status !== 'done' || fanoutRow.tool !== '@transport/telegram.fanout'
      || canonicalJson(Object.keys(fanoutOutput || {}).sort()) !== canonicalJson([
        'accepted', 'contract', 'messageKeys', 'result', 'targetIds',
      ])
      || fanoutOutput.contract !== 'TelegramFanoutResult/v1'
      || fanoutOutput.accepted !== true || fanoutOutput.result?.accepted !== true) return null;
    const pinnedConversation = clean(episode.origin_conversation_id, 240);
    const originMessageId = clean(episode.origin_message_id, 160);
    const origin = requiredTelegramOriginBinding(pinnedConversation, originMessageId);
    const transport = requiredTelegramTransportProof({
      transport: fanoutOutput.result,
      pinnedConversation,
      originMessageId,
      resultText: pending.text,
      idempotencyKey: deliveryKey,
      ...origin,
    });
    const authority = verifyMonitoringLifecycleAuthorityAt({
      workId: Number(work.id), episodeId: Number(episode.id),
      expectedPendingSha256: String(pending.pendingSha256 || ''),
    }, transport.maxAcceptedAt);
    const monitoringBinding = monitoringDeliveryReceiptBinding(authority);
    if (!authority || !monitoringBinding) return null;
    return Object.freeze({
      kind: 'monitoring_receipt_only',
      workId: Number(work.id), episodeId: Number(episode.id),
      resultText: authority.pending.text,
      idempotencyKey: deliveryKey,
      deliveredChannel: 'telegram',
      deliveryTarget: clean(episode.requester || episode.delivery_target, 160) || null,
      originConversationId: pinnedConversation,
      originMessageId,
      deliveredConversationId: pinnedConversation,
      proofSummary: clean(episode.proof_summary, 4_000) || null,
      transport,
      monitoringBinding,
    });
  } catch { return null; }
}

export function reconcileAcceptedMonitoringDelivery({ workId, episodeId } = {}) {
  const candidate = acceptedMonitoringDeliveryReceiptCandidate({ workId, episodeId });
  return candidate ? recordEpisodeDelivery(candidate.episodeId, candidate) : null;
}

function existingEpisodeDeliveryReceipt(episode, key) {
  const keyedReceipt = db.prepare(
    'SELECT * FROM eva_delivery_receipts WHERE idempotency_key = ?',
  ).get(key);
  const receipt = keyedReceipt || db.prepare(`
    SELECT r.* FROM eva_delivery_receipts r
    JOIN eva_episode_events e
      ON e.episode_id=r.episode_id
      AND e.transition_key=('receipt:' || r.idempotency_key)
    WHERE r.episode_id=? AND json_extract(e.payload_json, '$.to')='closed'
    LIMIT 1
  `).get(episode.id);
  if (receipt && Number(receipt.episode_id) !== Number(episode.id)) {
    throw new Error(`delivery-key ${key} hoort al bij episode ${receipt.episode_id}`);
  }
  return receipt || null;
}

function pinnedTelegramDeliveryRoute(episode, receipt, input) {
  let {
    deliveredChannel, deliveryTarget, originConversationId, originMessageId,
    deliveredConversationId,
  } = input;
  let telegramOrigin = episode.delivery_policy === 'required'
    ? episodeDeliveryOrigin(episode.id, 'telegram') : null;
  const storedFinalBinding = finalDeliveryBindingForEpisode(episode).binding;
  const hasSuppliedSealedTelegramBinding = storedFinalBinding
    && storedFinalBinding.contract === FINAL_DELIVERY_BINDING_CONTRACT
    && storedFinalBinding.stage === 'sealed'
    && storedFinalBinding.channel === 'telegram'
    && input.finalDeliveryBinding != null;
  if (hasSuppliedSealedTelegramBinding) {
    telegramOrigin = exactSealedTelegramFinalDeliveryOrigin(episode, {
      bindingSha256: input.finalDeliveryBinding?.bindingSha256,
      deliveryTurnId: input.deliveryTurnId,
    });
    if (!telegramOrigin) {
      throw new Error('finale Telegramaflevering mist precies één gebonden inbound-oorsprong');
    }
  }
  const pinnedConversation = /^telegram:.+$/i.test(String(telegramOrigin?.origin_conversation_id || ''))
    ? clean(telegramOrigin.origin_conversation_id, 240) : null;
  if (!pinnedConversation) return {
    telegramOrigin, pinnedConversation, deliveredChannel, deliveryTarget,
    originConversationId, originMessageId, deliveredConversationId,
  };
  const suppliedOrigin = clean(originConversationId, 240)
    || clean(receipt?.origin_conversation_id, 240);
  const suppliedDelivery = clean(deliveredConversationId, 240)
    || clean(receipt?.delivered_conversation_id, 240);
  const pinnedTarget = clean(
    telegramOrigin.requester || episode.requester || episode.delivery_target, 160,
  ) || null;
  const suppliedTarget = clean(deliveryTarget, 160)
    || clean(receipt?.delivery_target, 160);
  if (clean(deliveredChannel || receipt?.delivered_channel, 40).toLowerCase() !== 'telegram') {
    throw new Error(`episode ${episode.id} moet in de oorspronkelijke Telegramchat worden bezorgd`);
  }
  if (suppliedOrigin && suppliedOrigin !== pinnedConversation) {
    throw new Error(`episode ${episode.id} hoort bij ${pinnedConversation}, niet bij ${suppliedOrigin}`);
  }
  if (suppliedDelivery !== pinnedConversation) {
    throw new Error(`aflevering van episode ${episode.id} is niet bevestigd in ${pinnedConversation}`);
  }
  if (pinnedTarget && suppliedTarget
    && pinnedTarget.toLowerCase() !== suppliedTarget.toLowerCase()) {
    throw new Error(`afleverdoel ${suppliedTarget} strijdt met oorspronkelijke verzoeker ${pinnedTarget}`);
  }
  originConversationId = pinnedConversation;
  originMessageId = telegramOrigin.origin_message_id || null;
  deliveredConversationId = pinnedConversation;
  deliveryTarget = pinnedTarget;
  return {
    telegramOrigin, pinnedConversation, deliveredChannel, deliveryTarget,
    originConversationId, originMessageId, deliveredConversationId,
  };
}

function resolveEpisodeDeliveryRoute(episode, receipt, input) {
  const { resultText, proofSummary, transport, key, rawResultText } = input;
  const pinned = pinnedTelegramDeliveryRoute(episode, receipt, input);
  const {
    telegramOrigin, pinnedConversation, deliveredChannel, deliveryTarget,
    originConversationId, originMessageId, deliveredConversationId,
  } = pinned;
  const requestedConversation = clean(originConversationId, 240);
  const requestedMessage = clean(originMessageId, 160);
  let origin = null;
  if (requestedConversation) {
    origin = requestedMessage
      ? db.prepare(`SELECT * FROM eva_episode_origins
          WHERE episode_id = ? AND origin_conversation_id = ? AND origin_message_id = ?
          ORDER BY id DESC LIMIT 1`).get(episode.id, requestedConversation, requestedMessage)
      : db.prepare(`SELECT * FROM eva_episode_origins
          WHERE episode_id = ? AND origin_conversation_id = ?
          ORDER BY id DESC LIMIT 1`).get(episode.id, requestedConversation);
    if (!origin) {
      throw new Error(
        `aflevering van episode ${episode.id} verwijst niet naar een vooraf gebonden oorsprong`,
      );
    }
  }
  const candidate = {
    origin_conversation_id: origin?.origin_conversation_id
      || clean(episode.origin_conversation_id, 240) || null,
    origin_message_id: origin?.origin_message_id
      || (requestedConversation ? null : clean(episode.origin_message_id, 160)) || null,
    delivered_conversation_id: clean(deliveredConversationId || originConversationId, 240)
      || clean(episode.origin_conversation_id, 240) || null,
    delivered_channel: clean(deliveredChannel, 40),
    delivery_target: clean(deliveryTarget || episode.delivery_target, 160) || null,
    result_text: pinnedConversation
      ? canonicalTelegramText(rawResultText) : exactDeliveryText(resultText),
    proof_summary: clean(proofSummary || episode.proof_summary, 4_000) || null,
  };
  if (!candidate.delivered_channel || !candidate.result_text.trim()) {
    throw new Error('delivery receipt vereist een kanaal en een niet-leeg resultaat');
  }
  let verifiedTransport = transport;
  if (pinnedConversation) {
    const suppliedTransport = transport && typeof transport === 'object'
      && Object.keys(transport).length ? transport : json(receipt?.transport_json, {});
    verifiedTransport = requiredTelegramTransportProof({
      transport: suppliedTransport,
      pinnedConversation,
      originMessageId: telegramOrigin?.origin_message_id || originMessageId,
      resultText: rawResultText,
      idempotencyKey: key,
      ...requiredTelegramOriginBinding(
        pinnedConversation, telegramOrigin?.origin_message_id || originMessageId,
      ),
    });
  }
  return { candidate, verifiedTransport, deliveredChannel, deliveryTarget };
}

function bindInterpretationReplyTransport({
  episode, receipt, key, candidate, verifiedTransport,
}) {
  if (receipt) return verifiedTransport;
  const boundWork = workRow(episode.work_item_id);
  const boundState = interpretationWorkState(boundWork);
  const pendingDelivery = boundState.pending_delivery;
  const interpretationPreparation = interpretationReplyPreparationBinding(episode.id);
  if (!interpretationPreparation && !boundState.interpretation_reply_binding
    && pendingDelivery?.kind !== 'interpretation_reply') return verifiedTransport;
  if (!verifyInterpretationRetryPendingDelivery(boundWork, episode, pendingDelivery)) {
    throw new Error('finale aflevering mist een intact verzegeld modelantwoord');
  }
  if (key !== `prepared:${pendingDelivery.dedupeKey}`
    || pendingDelivery.provenance.textSha256
      !== interpretationRetryHash(candidate.result_text)) {
    throw new Error('finale aflevering wijkt af van het verzegelde modelantwoord');
  }
  return {
    ...(verifiedTransport || {}),
    replyBinding: {
      ...pendingDelivery.provenance,
      provenanceSha256: pendingDelivery.provenanceSha256,
      dedupeKey: pendingDelivery.dedupeKey,
    },
  };
}

function finalDeliveryTextSha256(value, channel = '') {
  const text = String(channel || '').toLowerCase() === 'telegram'
    ? canonicalTelegramText(value) : exactDeliveryText(value);
  return createHash('sha256').update(text).digest('hex');
}

function finalDeliveryResultObjectSha256(value) {
  return createHash('sha256').update(String(value ?? '').trim()).digest('hex');
}

function finalDeliveryBindingForEpisode(episode) {
  const work = workRow(episode?.work_item_id);
  const state = json(work?.state_json, {}) || {};
  return { work, state, binding: state.final_delivery_binding || null };
}

function exactSealedTelegramFinalDeliveryOrigin(episode, expected = {}) {
  const { work, binding } = finalDeliveryBindingForEpisode(episode);
  if (!episode || episode.state !== 'delivery_pending' || !work || !binding
    || binding.contract !== FINAL_DELIVERY_BINDING_CONTRACT
    || binding.stage !== 'sealed' || binding.channel !== 'telegram') return null;
  const { bindingSha256, ...sealedCore } = binding;
  if (semanticHash(sealedCore) !== bindingSha256
    || String(expected.bindingSha256 || '') !== bindingSha256
    || Number(binding.episodeId) !== Number(episode.id)
    || Number(binding.workItemId) !== Number(episode.work_item_id)
    || Number(work.id) !== Number(episode.work_item_id)
    || String(expected.deliveryTurnId || '') !== String(binding.turnId || '')
    || !/^telegram:.+$/iu.test(String(binding.conversationId || ''))
    || !String(binding.turnOriginMessageId || '')) return null;
  const origins = db.prepare(`SELECT * FROM eva_episode_origins
    WHERE episode_id=? AND origin_channel='telegram'
      AND origin_conversation_id=? AND origin_message_id=?
    ORDER BY id`).all(
    episode.id, binding.conversationId, binding.turnOriginMessageId,
  );
  return origins.length === 1 ? origins[0] : null;
}

export function verifiedTelegramFinalDeliveryOrigin(value = {}) {
  const episode = episodeRow(value.episodeId);
  const origin = exactSealedTelegramFinalDeliveryOrigin(episode, {
    bindingSha256: value.bindingSha256,
    deliveryTurnId: value.turnId,
  });
  return Boolean(origin
    && Number(episode.work_item_id) === Number(value.workItemId)
    && String(origin.origin_conversation_id || '') === String(value.conversationId || '')
    && String(origin.origin_message_id || '') === String(value.originMessageId || ''));
}

function episodeEverArmedFinalDelivery(episodeId) {
  return Boolean(db.prepare(`SELECT id FROM eva_episode_events
    WHERE episode_id=? AND kind='final_delivery_armed' LIMIT 1`).get(Number(episodeId)));
}

function validPreparedTelegramFinalDelivery(prepared, binding, candidate) {
  if (!prepared || typeof prepared !== 'object' || !prepared.payload) return false;
  const hasError = Object.hasOwn(prepared, 'error');
  if (hasError && (typeof prepared.error !== 'string'
    || prepared.error.length === 0 || prepared.error.length > 500)) return false;
  const payloadSha256 = semanticHash(prepared.payload);
  const core = {
    contract: 'PreparedTelegramReply/v2',
    turnId: String(prepared.turnId || ''),
    conversationId: String(prepared.conversationId || ''),
    originMessageId: String(prepared.originMessageId || ''),
    fromTelegramId: String(prepared.fromTelegramId || ''),
    originPrincipal: String(prepared.originPrincipal || ''),
    payloadSha256,
    syntheticTest: prepared.syntheticTest === true,
    goalEpisodeId: Number(prepared.goalEpisodeId) || null,
    workItemId: Number(prepared.workItemId) || null,
    finalDeliveryBindingSha256: String(prepared.finalDeliveryBindingSha256 || '') || null,
    ...(hasError ? { error: prepared.error } : {}),
  };
  return prepared.contract === core.contract
    && prepared.payloadSha256 === payloadSha256
    && prepared.bindingSha256 === semanticHash(core)
    && core.finalDeliveryBindingSha256 === binding.bindingSha256
    && core.turnId === binding.turnId
    && core.conversationId === binding.conversationId
    && core.originMessageId === binding.turnOriginMessageId
    && core.goalEpisodeId === Number(binding.episodeId)
    && core.workItemId === Number(binding.workItemId)
    && String(prepared.payload.reply || '') === candidate.result_text;
}

function unsafeMissingFinalDeliveryBinding(episode, receipt, suppliedBinding) {
  if (receipt && episodeHasFinalDelivery(episode.id)) return false;
  return suppliedBinding != null || episodeEverArmedFinalDelivery(episode.id);
}

function sealedFinalDeliveryBinding(binding) {
  return [
    binding.contract === FINAL_DELIVERY_BINDING_CONTRACT,
    binding.stage === 'sealed',
  ].every(Boolean);
}

function boundFinalDeliveryReceiptTransport(receipt, bindingSha256) {
  const storedTransport = json(receipt.transport_json, {}) || {};
  if (storedTransport?.finalDeliveryBinding?.bindingSha256 !== bindingSha256) {
    throw new Error('bestaand afleveringsbewijs hoort niet bij de actuele antwoordbinding');
  }
  return storedTransport;
}

function exactFinalDeliveryCandidate({
  episode, candidate, deliveryTurnId, suppliedBinding, binding, bindingSha256, sealedCore,
}) {
  return [
    semanticHash(sealedCore) === bindingSha256,
    String(suppliedBinding?.bindingSha256 || '') === bindingSha256,
    Number(binding.episodeId) === Number(episode.id),
    Number(binding.workItemId) === Number(episode.work_item_id),
    String(deliveryTurnId || '') === String(binding.turnId || ''),
    String(candidate.delivered_channel || '') === String(binding.channel || ''),
    String(candidate.delivered_conversation_id || '') === String(binding.conversationId || ''),
    String(candidate.origin_conversation_id || '') === String(binding.conversationId || ''),
    String(candidate.origin_message_id || '') === String(binding.turnOriginMessageId || ''),
    finalDeliveryTextSha256(candidate.result_text, candidate.delivered_channel)
      === binding.replySha256,
  ].every(Boolean);
}

function verifyVoiceFinalDeliveryRecovery({
  binding, candidate, suppliedBinding, verifiedTransport,
}) {
  const suppliedKeys = Object.keys(suppliedBinding).sort();
  if (canonicalJson(suppliedKeys) !== canonicalJson([
    'bindingSha256', 'deliverySha256', 'recovery', 'resultSha256',
    'voiceRecoveryProof',
  ])
    || suppliedBinding.bindingSha256 !== binding.bindingSha256
    || !verifiedVoiceFinalDeliveryRecoveryProof(db, suppliedBinding.voiceRecoveryProof, {
      requestId: binding.turnOriginMessageId,
      conversationId: binding.conversationId,
      requester: candidate.delivery_target,
      turnId: binding.turnId,
      replySha256: finalDeliveryTextSha256(
        candidate.result_text, candidate.delivered_channel,
      ),
      episodeId: binding.episodeId,
      workItemId: binding.workItemId,
      finalDeliveryBindingSha256: binding.bindingSha256,
      resultSha256: suppliedBinding.resultSha256,
      deliverySha256: suppliedBinding.deliverySha256,
      delivery: verifiedTransport,
    })) {
    throw new Error('finale Voice-aflevering mist exact duurzaam ACK-herstelbewijs');
  }
}

function verifyFinalDeliveryProof({ binding, candidate, suppliedBinding, verifiedTransport }) {
  if (String(candidate.delivered_channel || '').toLowerCase() !== 'telegram') {
    if (candidate.delivered_channel === 'voice' && suppliedBinding?.recovery === true) {
      verifyVoiceFinalDeliveryRecovery({
        binding, candidate, suppliedBinding, verifiedTransport,
      });
      return;
    }
    if (candidate.delivered_channel === 'web' && suppliedBinding?.recovery === true) {
      const suppliedKeys = Object.keys(suppliedBinding).sort();
      const identity = {
        channel: 'web', conversationId: binding.conversationId,
        messageId: binding.turnOriginMessageId,
      };
      const executionKey = `turn-understanding:${createHash('sha256')
        .update(canonicalJson(identity)).digest('hex')}`;
      const raw = db.prepare(`SELECT * FROM main.turn_tool_executions
        WHERE execution_key=? AND turn_id=? AND tool='@turn/understanding'`)
        .get(executionKey, `ingress:web:${binding.conversationId}:${binding.turnOriginMessageId}`);
      let row;
      let args;
      let output;
      try {
        row = validateExecutionRecord(raw);
        args = JSON.parse(String(row.args || 'null'));
        output = verifyPreparedUnderstandingTurn(
          JSON.parse(String(row.output || 'null')), identity,
        );
      } catch { /* fail closed below */ }
      if (canonicalJson(suppliedKeys) !== canonicalJson(['bindingSha256', 'recovery'])
        || suppliedBinding.bindingSha256 !== binding.bindingSha256
        || row?.status !== 'done' || !output
        || args?.channel !== 'web'
        || args?.conversationId !== binding.conversationId
        || String(args?.messageId || '') !== String(binding.turnOriginMessageId || '')
        || output.turnId !== binding.turnId
        || output.reply !== candidate.result_text
        || Number(output.workItemId) !== Number(binding.workItemId)
        || Number(output.goalEpisodeId) !== Number(binding.episodeId)
        || output.finalDeliveryBindingSha256 !== binding.bindingSha256
        || output.error !== null) {
        throw new Error('finale webaflevering mist exact duurzaam voorbereid antwoordbewijs');
      }
      return;
    }
    const suppliedCapabilitySha256 = createHash('sha256')
      .update(String(suppliedBinding?.capabilityToken || '')).digest('hex');
    if (suppliedCapabilitySha256 !== binding.capabilitySha256) {
      throw new Error('finale aflevering mist de eenmalige actuele afleverbekwaamheid');
    }
    return;
  }
  if (suppliedBinding?.recovery === true) {
    const prepared = telegramMessage(
      String(binding.conversationId || '').replace(/^telegram:/iu, ''),
      String(binding.turnOriginMessageId || ''),
    )?.meta?.preparedReply;
    if (!validPreparedTelegramFinalDelivery(prepared, binding, candidate)) {
      throw new Error('finale Telegramaflevering mist de duurzame prepared binding');
    }
  }
  if (!verifiedTransport || verifiedTransport.accepted !== true) {
    throw new Error('finale Telegramaflevering mist exact transportbewijs');
  }
}

/** @param {any} [options] */
export function sealFinalDeliveryReply(episodeId, options = {}) {
  const { workItemId, turnId, channel, conversationId, originMessageId, resultText } = options;
  const rawToken = randomUUID();
  return db.transaction(() => {
    const episode = episodeRow(episodeId);
    if (!episode || episode.state !== 'delivery_pending') return null;
    const { work, state, binding } = finalDeliveryBindingForEpisode(episode);
    if (!binding || binding.contract !== FINAL_DELIVERY_BINDING_CONTRACT
      || binding.stage !== 'awaiting_reply'
      || Number(binding.episodeId) !== Number(episode.id)
      || Number(binding.workItemId) !== Number(workItemId)
      || Number(work?.id) !== Number(workItemId)
      || String(binding.turnId || '') !== String(turnId || '')
      || String(binding.channel || '') !== String(channel || '')
      || String(binding.conversationId || '') !== String(conversationId || '')
      || String(binding.turnOriginMessageId || '') !== String(originMessageId || '')
      || !String(resultText || '').trim()) return null;
    const { bindingSha256: armedSha256, ...armed } = binding;
    if (semanticHash(armed) !== armedSha256) return null;
    const sealedCore = {
      ...armed,
      stage: 'sealed',
      armedSha256,
      replySha256: finalDeliveryTextSha256(resultText, channel),
      capabilitySha256: createHash('sha256').update(rawToken).digest('hex'),
    };
    const sealed = { ...sealedCore, bindingSha256: semanticHash(sealedCore) };
    const sealedState = { ...state, final_delivery_binding: sealed };
    if (JSON.stringify(sealedState).length > WORK_STATE_JSON_LIMIT) return null;
    const transition = transitionGoalEpisodeInTransaction(episode.id, {
      state: 'delivery_pending', sessionRef: turnId,
      eventKind: 'final_delivery_reply_sealed',
      idempotencyKey: `final-delivery-seal:${sealed.bindingSha256}`,
      work: { state: sealedState },
    });
    return transition ? Object.freeze({
      bindingSha256: sealed.bindingSha256,
      capabilityToken: rawToken,
      episodeId: sealed.episodeId,
      workItemId: sealed.workItemId,
      turnId: sealed.turnId,
      channel: sealed.channel,
      conversationId: sealed.conversationId,
      originMessageId: sealed.turnOriginMessageId,
      replySha256: sealed.replySha256,
      resultObjectReplySha256: finalDeliveryResultObjectSha256(resultText),
    }) : null;
  }).immediate();
}

function bindFinalDeliveryTransport({
  episode, receipt, candidate, verifiedTransport, deliveryTurnId, finalDeliveryBinding,
}) {
  const { binding } = finalDeliveryBindingForEpisode(episode);
  if (!binding) {
    if (unsafeMissingFinalDeliveryBinding(episode, receipt, finalDeliveryBinding)) {
      throw new Error('afleverbekwaamheid hoort niet bij deze episode');
    }
    return verifiedTransport;
  }
  if (!sealedFinalDeliveryBinding(binding)) {
    throw new Error('finale aflevering wacht nog op de exact gebonden antwoordtekst');
  }
  const { bindingSha256, ...sealedCore } = binding;
  if (receipt) return boundFinalDeliveryReceiptTransport(receipt, bindingSha256);
  if (!exactFinalDeliveryCandidate({
    episode,
    candidate,
    deliveryTurnId,
    suppliedBinding: finalDeliveryBinding,
    binding,
    bindingSha256,
    sealedCore,
  })) {
    throw new Error('finale aflevering wijkt af van de actuele antwoordbinding');
  }
  verifyFinalDeliveryProof({
    binding,
    candidate,
    suppliedBinding: finalDeliveryBinding,
    verifiedTransport,
  });
  return {
    ...(verifiedTransport || {}),
    finalDeliveryBinding: { bindingSha256 },
  };
}

function persistEpisodeDeliveryReceipt({ episode, receipt, key, candidate, verifiedTransport }) {
  if (receipt) {
    const mismatch = Object.entries(candidate).find(([field, value]) => (
      String(receipt[field] ?? '') !== String(value ?? '')
    ));
    if (mismatch) {
      throw new Error(`delivery-key ${key} botst met bestaand afleveringsbewijs (${mismatch[0]})`);
    }
    return receipt;
  }
  const serializedTransport = JSON.stringify(verifiedTransport || {});
  if (serializedTransport.length > 40_000) {
    throw new Error('Telegramafleveringsbewijs is onverwacht groot');
  }
  db.prepare(`INSERT INTO eva_delivery_receipts (
      episode_id, origin_conversation_id, origin_message_id, delivered_conversation_id, delivered_channel,
      delivery_target, result_text, proof_summary, idempotency_key, delivered_ts, transport_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(
      episode.id, candidate.origin_conversation_id, candidate.origin_message_id,
      candidate.delivered_conversation_id, candidate.delivered_channel,
      candidate.delivery_target, candidate.result_text, candidate.proof_summary,
      key, nowIso(), serializedTransport,
    );
  return db.prepare('SELECT * FROM eva_delivery_receipts WHERE idempotency_key = ?').get(key);
}

function finalizeEpisodeDeliveryReceipt(episodeId, receipt, deliveredChannel, deliveryTarget) {
  const episode = episodeRow(episodeId);
  const receiptTransitionKey = `receipt:${receipt.idempotency_key}`;
  const priorReceiptTransition = db.prepare(`SELECT payload_json FROM eva_episode_events
    WHERE episode_id=? AND transition_key=? LIMIT 1`).get(episode.id, receiptTransitionKey);
  const priorTargetState = clean(json(priorReceiptTransition?.payload_json, {})?.to, 40);
  const closesWork = ['delivery_pending', 'verifying'].includes(episode.state);
  const targetState = priorTargetState || (closesWork ? 'closed' : 'delivery_recorded');
  const finalizesWork = targetState === 'closed';
  const storedFinalState = workRow(episode.work_item_id)?.state_json;
  const projectedFinalState = finalizesWork
    ? terminalWorkProjection({ state_json: storedFinalState }, 'done').state_json
    : storedFinalState;
  const finalState = json(projectedFinalState, {}) || {};
  delete finalState.final_delivery_binding;
  transitionGoalEpisodeInTransaction(episode.id, {
    state: targetState,
    nextAction: finalizesWork ? '' : episode.next_action,
    checkpoint: 'resultaat bezorgd',
    wakeTs: finalizesWork ? '' : episode.wake_ts,
    idempotencyKey: receiptTransitionKey,
    deliveryReceiptKey: receipt.idempotency_key,
    eventKind: `state:${targetState}`,
    work: {
      state: finalState,
      result: finalizesWork ? receipt.result_text : undefined,
      clearWait: finalizesWork,
      clearLease: finalizesWork,
    },
  });
  const deliveredEvent = db.prepare(`SELECT id FROM eva_episode_events
    WHERE episode_id = ? AND kind = 'delivered'
      AND json_extract(payload_json, '$.idempotencyKey') = ? LIMIT 1`)
    .get(episode.id, receipt.idempotency_key);
  if (!deliveredEvent) {
    addEvent(episode.id, 'delivered', {
      idempotencyKey: receipt.idempotency_key, deliveredChannel, deliveryTarget,
    });
  }
  if (String(deliveredChannel || '').toLowerCase() === 'telegram') {
    const chatId = String(receipt.origin_conversation_id || '').match(/^telegram:(.+)$/iu)?.[1];
    const messageId = clean(receipt.origin_message_id, 160);
    const registered = chatId && messageId
      ? exactInterpretationRetryHandoffOwnership(
        'telegram', `telegram:${chatId}`, messageId,
      ) : null;
    if (registered && Number(registered.episode.id) === Number(episode.id)) {
      const inbound = db.prepare(`SELECT processing_status FROM telegram_messages
        WHERE chat_id=? AND message_id=? AND role='user' AND deleted=0
          AND json_extract(meta, '$.inbound')=1`).get(chatId, messageId);
      if (!inbound) throw new Error('Telegramaflevering mist haar intacte inbound-rij');
      if (inbound.processing_status !== TELEGRAM_DELIVERY_STATUS.INBOUND_COMPLETED) {
        if (!['received', 'processing', 'failed'].includes(inbound.processing_status)) {
          throw new Error(`Telegram-inboundstatus botst met ${inbound.processing_status}`);
        }
        const ts = nowIso();
        const completed = db.prepare(`UPDATE telegram_messages
          SET processing_status='completed',processed_ts=?,processing_error=NULL,archived_ts=?
          WHERE chat_id=? AND message_id=? AND role='user' AND deleted=0
            AND json_extract(meta, '$.inbound')=1 AND processing_status=?`).run(
          ts, ts, chatId, messageId, inbound.processing_status,
        );
        if (Number(completed.changes) !== 1) {
          throw new Error('Telegram-inbound kon niet atomair na afleverbewijs worden afgerond');
        }
      }
    }
  }
}

const MONITORING_RECEIPT_BINDING_FIELDS = Object.freeze([
  'contract', 'monitoringContractSha256', 'pendingSha256', 'lifecycleSha256',
  'lifecycleGeneration', 'originBindingSha256', 'outcomeReceiptSha256',
  'outcomeSha256', 'factSetSha256', 'textSha256', 'rendererVersion', 'dedupeKey',
]);

function normalizedMonitoringReceiptBinding(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort())
      !== canonicalJson([...MONITORING_RECEIPT_BINDING_FIELDS].sort())
    || value.contract !== 'MonitoringDeliveryReceiptBinding/v1'
    || !SHA256.test(String(value.monitoringContractSha256 || ''))
    || !SHA256.test(String(value.pendingSha256 || ''))
    || !SHA256.test(String(value.lifecycleSha256 || ''))
    || !Number.isSafeInteger(value.lifecycleGeneration)
    || value.lifecycleGeneration < 1
    || !SHA256.test(String(value.originBindingSha256 || ''))
    || !SHA256.test(String(value.outcomeReceiptSha256 || ''))
    || !SHA256.test(String(value.outcomeSha256 || ''))
    || !SHA256.test(String(value.factSetSha256 || ''))
    || !SHA256.test(String(value.textSha256 || ''))
    || String(value.rendererVersion || '') !== 'monitoring-nl-v1'
    || !clean(value.dedupeKey, 240)) return null;
  return Object.freeze(Object.fromEntries(MONITORING_RECEIPT_BINDING_FIELDS
    .map((field) => [field, value[field]])));
}

function bindMonitoringDeliveryReceipt({
  episode, existingReceipt, supplied, key, rawResultText, verificationAt,
}) {
  const work = workRow(episode.work_item_id);
  const state = json(work?.state_json, {}) || {};
  const monitoringScoped = Object.hasOwn(state, 'monitoring');
  if (!monitoringScoped && supplied == null) return null;
  const normalized = normalizedMonitoringReceiptBinding(supplied);
  if (!monitoringScoped || !normalized) {
    throw new Error('monitoring receipt mist haar exact getypeerde binding');
  }
  if (existingReceipt) {
    const stored = normalizedMonitoringReceiptBinding(
      json(existingReceipt.transport_json, {})?.monitoringBinding,
    );
    if (!stored || canonicalJson(stored) !== canonicalJson(normalized)) {
      throw new Error('monitoring receipt replay wijkt af van haar opgeslagen binding');
    }
    return stored;
  }
  const authority = verifyMonitoringLifecycleAuthorityAt({
    workId: Number(work.id), episodeId: Number(episode.id),
    expectedPendingSha256: normalized.pendingSha256,
  }, verificationAt);
  const pending = authority?.pending;
  const expected = pending && {
    contract: 'MonitoringDeliveryReceiptBinding/v1',
    monitoringContractSha256: authority.monitoring.contractSha256,
    pendingSha256: pending.pendingSha256,
    lifecycleSha256: authority.lifecycle.lifecycleSha256,
    lifecycleGeneration: authority.lifecycle.generation,
    originBindingSha256: authority.lifecycle.originBindingSha256,
    outcomeReceiptSha256: pending.outcomeRef.outcomeReceiptSha256,
    outcomeSha256: pending.outcomeRef.outcomeSha256,
    factSetSha256: pending.factSetSha256,
    textSha256: pending.textSha256,
    rendererVersion: pending.rendererVersion,
    dedupeKey: pending.dedupeKey,
  };
  if (!authority || !pending || episode.state !== 'delivery_pending'
    || canonicalJson(expected) !== canonicalJson(normalized)
    || key !== `prepared:${pending.dedupeKey}`
    || canonicalTelegramText(rawResultText) !== pending.text
    || createHash('sha256').update(pending.text).digest('hex') !== pending.textSha256) {
    throw new Error('monitoring receipt verloor haar actuele lifecycle- of pendingauthority');
  }
  return normalized;
}

/** @param {any} [options] */
export function recordEpisodeDelivery(episodeId, options = {}) {
  const {
    deliveredChannel,
    deliveryTarget = null,
    originConversationId = null,
    originMessageId = null,
    deliveredConversationId = null,
    resultText,
    proofSummary = null,
    idempotencyKey,
    transport = {},
    deliveryTurnId = null,
    finalDeliveryBinding = null,
    monitoringBinding = null,
  } = options;
  const key = clean(idempotencyKey, 240);
  if (!key) throw new Error('delivery receipt vereist idempotency-key');
  const rawResultText = String(resultText ?? '');
  // Receipt, replybinding en terminale episodeprojectie delen bewust één
  // transactie; private helpers houden dezelfde atomische afleverbewijsgrens.
  return db.transaction(() => {
    const episode = episodeRow(episodeId);
    if (!episode) return null;
    const existingReceipt = existingEpisodeDeliveryReceipt(episode, key);
    const route = resolveEpisodeDeliveryRoute(episode, existingReceipt, {
      deliveredChannel, deliveryTarget, originConversationId, originMessageId,
      deliveredConversationId, resultText, proofSummary, transport, key, rawResultText,
      deliveryTurnId, finalDeliveryBinding,
    });
    const boundMonitoring = bindMonitoringDeliveryReceipt({
      episode, existingReceipt, supplied: monitoringBinding, key, rawResultText,
      verificationAt: route.verifiedTransport?.maxAcceptedAt || new Date().toISOString(),
    });
    let verifiedTransport = bindInterpretationReplyTransport({
      episode, receipt: existingReceipt, key,
      candidate: route.candidate, verifiedTransport: route.verifiedTransport,
    });
    verifiedTransport = bindFinalDeliveryTransport({
      episode, receipt: existingReceipt, candidate: route.candidate,
      verifiedTransport, deliveryTurnId, finalDeliveryBinding,
    });
    if (boundMonitoring) {
      verifiedTransport = { ...verifiedTransport, monitoringBinding: boundMonitoring };
    }
    const receipt = persistEpisodeDeliveryReceipt({
      episode, receipt: existingReceipt, key,
      candidate: route.candidate, verifiedTransport,
    });
    finalizeEpisodeDeliveryReceipt(
      episode.id, receipt, route.deliveredChannel, route.deliveryTarget,
    );
    return receipt;
  }).immediate();
}

export function recoverDeliveredEpisodeReceipts() {
  const rows = db.prepare(`
    SELECT id, ts, content, meta FROM main.timeline
    WHERE kind = 'chat_out'
      AND json_extract(meta, '$.goalEpisodeId') IS NOT NULL
      AND json_extract(meta, '$.delivery.accepted') = 1
    ORDER BY id
  `).all();
  let recovered = 0;
  for (const row of rows) {
    const meta = json(row.meta, {});
    const episode = episodeRow(meta?.goalEpisodeId);
    if (!episode || episodeHasFinalDelivery(episode.id)
      || !['delivery_pending', 'verifying'].includes(episode.state)
      || Date.parse(row.ts || '') < Date.parse(episode.updated_ts || '')) continue;
    const resultText = String(row.content || '').replace(/^Eva\s*(?:->|:)\s*[^:]*:\s*/i, '').trim();
    if (!resultText) continue;
    try {
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
        deliveryTurnId: meta.turnId || null,
        finalDeliveryBinding: meta.finalDeliveryBindingSha256
          ? { bindingSha256: meta.finalDeliveryBindingSha256, recovery: true }
          : null,
      });
      recovered += 1;
    } catch {
      // Een losse tijdlijnclaim is geen transportbewijs; ga door met andere receipts.
    }
  }
  return recovered;
}

export function episodeNeedsDelivery(workItemId) {
  const episode = episodeForWork(workItemId);
  if (!episode || episode.delivery_policy !== 'required') return false;
  return !episodeHasFinalDelivery(episode.id);
}

export function episodeHasDelivery(episodeId) {
  return Boolean(db.prepare('SELECT id FROM eva_delivery_receipts WHERE episode_id = ? LIMIT 1')
    .get(Number(episodeId)));
}

export function episodeHasFinalDelivery(episodeId) {
  return Boolean(db.prepare(`
    SELECT r.id FROM eva_delivery_receipts r
    JOIN eva_episode_events e
      ON e.episode_id=r.episode_id
      AND e.transition_key=('receipt:' || r.idempotency_key)
    WHERE r.episode_id=?
      AND json_extract(e.payload_json, '$.to')='closed'
    LIMIT 1
  `).get(Number(episodeId)));
}

/** @param {any} [options] */
export function createAutoBoundTurnWork({
  channel, person, userText, outcome, conversationId, originMessageId,
} = {}) {
  return db.transaction(() => {
    const command = createWorkItem({
      source: channel, owner: person, title: `Opdracht: ${String(userText).slice(0, 210)}`,
      goal: userText, nextStep: 'Controleer het resultaat en bezorg het in dit gesprek.',
      definitionOfDone: outcome || userText, priority: 75, visibleToFamily: true,
    });
    const existing = episodeForWork(command.id);
    if (existing) {
      attachEpisodeOrigin(existing.id, {
        originKind: 'user', originChannel: channel, originConversationId: conversationId,
        originMessageId, requester: person,
      });
      return { command, episode: existing };
    }
    const episode = createGoalEpisode({
      title: `Opdracht van ${person}`, outcome: userText,
      definitionOfDone: outcome || userText, originKind: 'user', originChannel: channel,
      originConversationId: conversationId, originMessageId, requester: person,
      deliveryPolicy: 'required', deliveryTarget: person,
      nextAction: 'Controleer het resultaat en bezorg het in dit gesprek.',
    });
    return { command, episode: bindEpisodeToWork(episode.id, command.id) };
  })();
}

export function oneShotAutoBoundMemoryContribution(toolResults = [], {
  autoBoundThisTurn = false, turnSucceeded = false, actionRequested = false,
  requiredTools = [], turnKind = '', understanding = null, continuation = false,
  resumed = false, plans = [],
} = {}) {
  const required = [...new Set(Array.isArray(requiredTools) ? requiredTools : [])];
  const issuedPlans = Array.isArray(plans) ? plans : [];
  const plan = issuedPlans[0];
  const blocked = [
    !autoBoundThisTurn, !turnSucceeded, !actionRequested,
    understanding?.durability !== 'turn', understanding?.temporal?.mode !== 'now',
    understanding?.source?.origin !== 'user', understanding?.monitoring?.requested === true,
    continuation, resumed, ['monitoring', 'continuity'].includes(turnKind),
    issuedPlans.length !== 1, plan?.supportsDeferred === true,
    plan?.execution?.mode === 'iterative', plan?.capabilityId === 'work.episode.manage',
    plan?.toolName !== 'onthoud', plan?.capabilityId !== 'memory.fact.write',
    plan?.operationId !== 'remember', required.join('\u0000') !== 'onthoud',
  ];
  if (blocked.some(Boolean)) return null;
  return [...toolResults].reverse().find((tool) => (
    [tool?.name === 'onthoud', tool?.semanticBinding?.requirementId === plan.requirementId,
      tool?.semanticBinding?.capabilityId === 'memory.fact.write',
      tool?.semanticBinding?.operationId === 'remember', tool?.semanticBinding?.role === 'primary',
      tool?.changed === true, tool?.effect_uncertain !== true,
      verifiedCompleteReceipt(tool)].every(Boolean)
  )) || null;
}

export function selectPortfolioCommitment(items = [], now = new Date()) {
  const at = now.getTime();
  const scored = (Array.isArray(items) ? items : [])
    .filter((item) => item?.status === 'open')
    .filter((item) => ![TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
      TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED]
      .includes(json(item.wait_json, {})?.reason))
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
