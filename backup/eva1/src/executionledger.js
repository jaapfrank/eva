import { createHash } from 'node:crypto';

export const EXECUTION_RECORD_CONTRACT_V1 = 'ExecutionRecord/v1';
export const EXECUTION_RECORD_CONTRACT_V2 = 'ExecutionRecord/v2';
export const EXECUTION_VERIFICATION_VERIFIED = 'verified';
export const EXECUTION_VERIFICATION_LEGACY_UNVERIFIABLE = 'legacy_unverifiable';
export const TURN_EFFECT_MANIFEST_TOOL = '@recovery/turn-effect-manifest';
export const TURN_EFFECT_PLAN_TOOL = '@recovery/turn-effect-plan';

const SHA256 = /^[a-f0-9]{64}$/u;
const TURN_EFFECT_MANIFEST_CONTRACT = 'TurnEffectManifest/v1';
const TURN_EFFECT_MANIFEST_STATE_CONTRACT = 'TurnEffectManifestState/v1';
const TURN_EFFECT_PLAN_CONTRACT = 'TurnEffectPlan/v1';
const TURN_EFFECT_PLAN_STATE_CONTRACT = 'TurnEffectPlanState/v1';

/**
 * @typedef {
 *   | Readonly<{
 *       contract: 'ExecutionLedgerStoreStatus/v2',
 *       available: true,
 *       layout: string,
 *       manifestSha256: string,
 *     }>
 *   | Readonly<{
 *       contract: 'ExecutionLedgerStoreStatus/v2',
 *       available: false,
 *       reason: string,
 *     }>
 * } ExecutionLedgerStoreStatus
 */

export const EXECUTION_LEDGER_TABLE_SQL = `CREATE TABLE turn_tool_executions (
  execution_key TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  output TEXT,
  semantic_hash TEXT,
  semantic_binding TEXT,
  intent_record_sha256 TEXT,
  output_sha256 TEXT,
  material_sha256 TEXT,
  completion_record_sha256 TEXT,
  execution_seq INTEGER NOT NULL DEFAULT 0,
  execution_contract TEXT NOT NULL DEFAULT 'ExecutionRecord/v1',
  execution_verification TEXT NOT NULL DEFAULT 'legacy_unverifiable'
)`;

const EXECUTION_LEDGER_MIGRATED_HEAD8_TABLE_SQL = `CREATE TABLE turn_tool_executions (
    execution_key TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    started_ts TEXT NOT NULL,
    finished_ts TEXT,
    output TEXT
  , semantic_hash TEXT, semantic_binding TEXT, intent_record_sha256 TEXT,
  output_sha256 TEXT, material_sha256 TEXT, completion_record_sha256 TEXT,
  execution_seq INTEGER NOT NULL DEFAULT 0, execution_contract
        TEXT NOT NULL DEFAULT 'ExecutionRecord/v1', execution_verification
        TEXT NOT NULL DEFAULT 'legacy_unverifiable')`;

const EXECUTION_LEDGER_MIGRATED_PRODUCTION14_TABLE_SQL = `CREATE TABLE turn_tool_executions (
  execution_key TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  output TEXT
, semantic_hash TEXT, semantic_binding TEXT, output_sha256 TEXT,
  material_sha256 TEXT, intent_record_sha256 TEXT, completion_record_sha256 TEXT,
  execution_seq INTEGER NOT NULL DEFAULT 0, execution_contract
        TEXT NOT NULL DEFAULT 'ExecutionRecord/v1', execution_verification
        TEXT NOT NULL DEFAULT 'legacy_unverifiable')`;

export const EXECUTION_LEDGER_INDEX_SQL = Object.freeze({
  idx_turn_tool_executions_turn: `CREATE INDEX idx_turn_tool_executions_turn
    ON turn_tool_executions(turn_id, tool)`,
  idx_turn_tool_executions_sequence: `CREATE UNIQUE INDEX idx_turn_tool_executions_sequence
    ON turn_tool_executions(execution_seq)`,
});

export const EXECUTION_LEDGER_TRIGGER_SQL = Object.freeze({
  turn_tool_executions_valid_sequence: `CREATE TRIGGER turn_tool_executions_valid_sequence
    BEFORE INSERT ON turn_tool_executions
    WHEN typeof(NEW.execution_seq)<>'integer' OR NEW.execution_seq<=0
    BEGIN SELECT RAISE(ABORT,'turn tool execution sequence must be positive'); END`,
  turn_tool_executions_sequence_immutable: `CREATE TRIGGER turn_tool_executions_sequence_immutable
    BEFORE UPDATE OF execution_seq ON turn_tool_executions
    WHEN OLD.execution_seq IS NOT NEW.execution_seq
    BEGIN SELECT RAISE(ABORT,'turn tool execution sequence is immutable'); END`,
  turn_tool_executions_valid_contract: `CREATE TRIGGER turn_tool_executions_valid_contract
    BEFORE INSERT ON turn_tool_executions
    WHEN typeof(NEW.execution_contract)<>'text'
      OR NEW.execution_contract NOT IN ('ExecutionRecord/v1','ExecutionRecord/v2')
    BEGIN SELECT RAISE(ABORT,'turn tool execution contract is unsupported'); END`,
  turn_tool_executions_contract_immutable: `CREATE TRIGGER turn_tool_executions_contract_immutable
    BEFORE UPDATE OF execution_contract ON turn_tool_executions
    WHEN OLD.execution_contract IS NOT NEW.execution_contract
    BEGIN SELECT RAISE(ABORT,'turn tool execution contract is immutable'); END`,
  turn_tool_executions_key_immutable: `CREATE TRIGGER turn_tool_executions_key_immutable
    BEFORE UPDATE OF execution_key ON turn_tool_executions
    WHEN OLD.execution_key IS NOT NEW.execution_key
    BEGIN SELECT RAISE(ABORT,'turn tool execution key is immutable'); END`,
  turn_tool_executions_valid_verification: `CREATE TRIGGER turn_tool_executions_valid_verification
    BEFORE INSERT ON turn_tool_executions
    WHEN typeof(NEW.execution_verification)<>'text'
      OR NEW.execution_verification NOT IN ('verified','legacy_unverifiable')
      OR (NEW.execution_contract='ExecutionRecord/v2'
        AND NEW.execution_verification<>'verified')
    BEGIN SELECT RAISE(ABORT,'turn tool execution verification is invalid'); END`,
  turn_tool_executions_intent_immutable: `CREATE TRIGGER turn_tool_executions_intent_immutable
    BEFORE UPDATE OF turn_id,tool,args,semantic_hash,semantic_binding,
      intent_record_sha256,started_ts,execution_verification ON turn_tool_executions
    WHEN OLD.turn_id IS NOT NEW.turn_id OR OLD.tool IS NOT NEW.tool
      OR OLD.args IS NOT NEW.args OR OLD.semantic_binding IS NOT NEW.semantic_binding
      OR OLD.started_ts IS NOT NEW.started_ts
      OR OLD.semantic_hash IS NOT NEW.semantic_hash
      OR OLD.intent_record_sha256 IS NOT NEW.intent_record_sha256
      OR OLD.execution_verification IS NOT NEW.execution_verification
    BEGIN SELECT RAISE(ABORT,'turn tool execution intent is immutable'); END`,
  turn_tool_executions_terminal_immutable: `CREATE TRIGGER turn_tool_executions_terminal_immutable
    BEFORE UPDATE OF status,finished_ts,output,output_sha256,material_sha256,
      completion_record_sha256,execution_verification ON turn_tool_executions
    WHEN OLD.status IN ('done','transport_accepted','transport_rejected','transport_uncertain')
      AND (OLD.status IS NOT NEW.status OR OLD.finished_ts IS NOT NEW.finished_ts
        OR OLD.output IS NOT NEW.output
        OR OLD.output_sha256 IS NOT NEW.output_sha256
        OR OLD.material_sha256 IS NOT NEW.material_sha256
        OR OLD.completion_record_sha256 IS NOT NEW.completion_record_sha256
        OR OLD.execution_verification IS NOT NEW.execution_verification)
    BEGIN SELECT RAISE(ABORT,'turn tool execution terminal record is immutable'); END`,
  turn_tool_executions_no_delete: `CREATE TRIGGER turn_tool_executions_no_delete
    BEFORE DELETE ON turn_tool_executions
    BEGIN SELECT RAISE(ABORT,'turn tool execution record deletion is forbidden'); END`,
  turn_tool_executions_no_replace: `CREATE TRIGGER turn_tool_executions_no_replace
    BEFORE INSERT ON turn_tool_executions
    WHEN EXISTS (
      SELECT 1 FROM main.turn_tool_executions existing
      WHERE existing.execution_key=NEW.execution_key
         OR existing.execution_seq=NEW.execution_seq
    )
    BEGIN SELECT RAISE(ABORT,'turn tool execution record replacement is forbidden'); END`,
});

// De vorige productieversie stond uitsluitend tijdens haar eenmalige v1-hashupgrade
// twee nauw begrensde wijzigingen van legacy_unverifiable naar verified toe. Een
// volledig gemigreerde live database kan die tijdelijke triggers nog dragen. Hun
// exacte manifest mag daarom één keer naar de definitieve immutable set hierboven.
export const EXECUTION_LEDGER_UPGRADE_PREDECESSOR_TRIGGER_SQL = Object.freeze({
  ...EXECUTION_LEDGER_TRIGGER_SQL,
  turn_tool_executions_intent_immutable: `CREATE TRIGGER turn_tool_executions_intent_immutable
    BEFORE UPDATE OF turn_id,tool,args,semantic_hash,semantic_binding,
      intent_record_sha256,started_ts,execution_verification ON turn_tool_executions
    WHEN (
      OLD.turn_id IS NOT NEW.turn_id OR OLD.tool IS NOT NEW.tool
      OR OLD.args IS NOT NEW.args OR OLD.semantic_binding IS NOT NEW.semantic_binding
      OR OLD.started_ts IS NOT NEW.started_ts
      OR NOT (
        OLD.execution_contract='ExecutionRecord/v1'
        AND OLD.execution_verification='legacy_unverifiable'
        AND NEW.execution_verification='verified'
        AND OLD.semantic_hash IS NULL AND OLD.intent_record_sha256 IS NULL
        AND NEW.semantic_hash IS NOT NULL AND NEW.intent_record_sha256 IS NOT NULL
        AND OLD.semantic_binding IS NULL AND NEW.semantic_binding IS NULL
        AND instr(OLD.args,'[AFGESCHERMD:')=0
      ) AND (
        OLD.semantic_hash IS NOT NEW.semantic_hash
        OR OLD.intent_record_sha256 IS NOT NEW.intent_record_sha256
        OR OLD.execution_verification IS NOT NEW.execution_verification
      )
    )
    BEGIN SELECT RAISE(ABORT,'turn tool execution intent is immutable'); END`,
  turn_tool_executions_terminal_immutable: `CREATE TRIGGER turn_tool_executions_terminal_immutable
    BEFORE UPDATE OF status,finished_ts,output,output_sha256,material_sha256,
      completion_record_sha256,execution_verification ON turn_tool_executions
    WHEN OLD.status IN ('done','transport_accepted','transport_rejected','transport_uncertain')
      AND (
        OLD.status IS NOT NEW.status OR OLD.finished_ts IS NOT NEW.finished_ts
        OR OLD.output IS NOT NEW.output
        OR NOT (
          OLD.execution_contract='ExecutionRecord/v1'
          AND OLD.execution_verification='legacy_unverifiable'
          AND NEW.execution_verification='verified'
          AND OLD.output_sha256 IS NULL AND OLD.completion_record_sha256 IS NULL
          AND NEW.output_sha256 IS NOT NULL AND NEW.completion_record_sha256 IS NOT NULL
        ) AND (
          OLD.output_sha256 IS NOT NEW.output_sha256
          OR OLD.material_sha256 IS NOT NEW.material_sha256
          OR OLD.completion_record_sha256 IS NOT NEW.completion_record_sha256
          OR OLD.execution_verification IS NOT NEW.execution_verification
        )
      )
    BEGIN SELECT RAISE(ABORT,'turn tool execution terminal record is immutable'); END`,
});

const BASE_COLUMNS = Object.freeze([
  ['execution_key', 'TEXT', 0, 1, null],
  ['turn_id', 'TEXT', 1, 0, null],
  ['tool', 'TEXT', 1, 0, null],
  ['args', 'TEXT', 0, 0, null],
]);
const SEMANTIC_COLUMNS = Object.freeze([
  ['semantic_hash', 'TEXT', 0, 0, null],
  ['semantic_binding', 'TEXT', 0, 0, null],
  ['intent_record_sha256', 'TEXT', 0, 0, null],
]);
const LIFECYCLE_COLUMNS = Object.freeze([
  ['status', 'TEXT', 1, 0, "'started'"],
  ['started_ts', 'TEXT', 1, 0, null],
  ['finished_ts', 'TEXT', 0, 0, null],
  ['output', 'TEXT', 0, 0, null],
]);
const PROOF_COLUMNS = Object.freeze([
  ['output_sha256', 'TEXT', 0, 0, null],
  ['material_sha256', 'TEXT', 0, 0, null],
  ['completion_record_sha256', 'TEXT', 0, 0, null],
  ['execution_seq', 'INTEGER', 1, 0, '0'],
  ['execution_contract', 'TEXT', 1, 0, "'ExecutionRecord/v1'"],
  ['execution_verification', 'TEXT', 1, 0, "'legacy_unverifiable'"],
]);

export const EXECUTION_LEDGER_COLUMN_LAYOUTS = Object.freeze({
  'canonical-v2': Object.freeze([
    ...BASE_COLUMNS, ...LIFECYCLE_COLUMNS, ...SEMANTIC_COLUMNS, ...PROOF_COLUMNS,
  ]),
  'production14-migrated-v2': Object.freeze([
    ...BASE_COLUMNS, ...LIFECYCLE_COLUMNS,
    ...SEMANTIC_COLUMNS.slice(0, 2), ...PROOF_COLUMNS.slice(0, 2),
    SEMANTIC_COLUMNS[2], PROOF_COLUMNS[2], ...PROOF_COLUMNS.slice(3),
  ]),
});

function normalizeSql(value) {
  const source = String(value || '').replace(/\bmain\./gu, '');
  const literals = [];
  let syntax = '';
  for (let index = 0; index < source.length;) {
    if (source[index] !== "'") {
      syntax += source[index];
      index += 1;
      continue;
    }
    const start = index;
    index += 1;
    while (index < source.length) {
      if (source[index] !== "'") { index += 1; continue; }
      if (source[index + 1] === "'") { index += 2; continue; }
      index += 1;
      break;
    }
    const literal = literals.push(source.slice(start, index)) - 1;
    syntax += `\u{e000}${literal}\u{e001}`;
  }
  const normalized = syntax.replace(/;\s*$/u, '').replace(/\s+/gu, ' ')
    .replace(/\s*([(),=])\s*/gu, '$1').trim().toLowerCase();
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literal,
  ) => literals[Number(literal)]);
}

function canonicalJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  return `{${Object.keys(value).sort().map((key) => (
    `${JSON.stringify(key)}:${canonicalJson(value[key])}`
  )).join(',')}}`;
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function boundedEffectManifestText(value, limit = 512) {
  const text = String(value || '').trim();
  return text && text.length <= limit ? text : null;
}

function effectParentIdentity(parent) {
  const executionKey = boundedEffectManifestText(parent?.execution_key);
  const parentTurnId = boundedEffectManifestText(parent?.turn_id);
  const intentRecordSha256 = String(parent?.intent_record_sha256 || '');
  let args;
  try { args = JSON.parse(String(parent?.args || 'null')); } catch { return null; }
  const executionTurnId = boundedEffectManifestText(args?.sessionRef);
  if (!executionKey || !parentTurnId || !executionTurnId
    || parent?.tool !== '@turn/understanding'
    || parent?.status !== 'started' || !SHA256.test(intentRecordSha256)) return null;
  return Object.freeze({
    parentExecutionKey: executionKey,
    parentIntentRecordSha256: intentRecordSha256,
    parentTurnId,
    executionTurnId,
  });
}

export function turnEffectManifestSpec(parent) {
  const identity = effectParentIdentity(parent);
  if (!identity) throw new Error('effectmanifest mist een exacte started parentbinding');
  const args = Object.freeze({
    contract: TURN_EFFECT_MANIFEST_CONTRACT,
    ...identity,
  });
  const manifestSha256 = sha256(canonicalJson(args));
  return Object.freeze({
    executionKey: `turn-effect-manifest:${manifestSha256}`,
    turnId: identity.parentTurnId,
    tool: TURN_EFFECT_MANIFEST_TOOL,
    args,
    output: Object.freeze({
      contract: TURN_EFFECT_MANIFEST_STATE_CONTRACT,
      state: 'verified_empty',
      manifestSha256,
    }),
  });
}

export function turnEffectPlanSpec(parent, planSha256) {
  const identity = effectParentIdentity(parent);
  const boundedPlanSha256 = String(planSha256 || '');
  if (!identity || !SHA256.test(boundedPlanSha256)) {
    throw new Error('effectplan mist een exacte parent- of planbinding');
  }
  const args = Object.freeze({
    contract: TURN_EFFECT_PLAN_CONTRACT,
    ...identity,
    planSha256: boundedPlanSha256,
  });
  const markerSha256 = sha256(canonicalJson(args));
  return Object.freeze({
    executionKey: `turn-effect-plan:${markerSha256}`,
    turnId: identity.parentTurnId,
    tool: TURN_EFFECT_PLAN_TOOL,
    args,
    output: Object.freeze({
      contract: TURN_EFFECT_PLAN_STATE_CONTRACT,
      state: 'effect_planned',
      markerSha256,
    }),
  });
}

function effectEvidenceRow(row, valid) {
  return Object.freeze({
    valid,
    executionKey: String(row?.execution_key || '').slice(0, 512),
    executionSeq: Number.isSafeInteger(Number(row?.execution_seq))
      ? Number(row.execution_seq) : null,
    turnId: String(row?.turn_id || '').slice(0, 512),
    tool: String(row?.tool || '').slice(0, 240),
    status: String(row?.status || '').slice(0, 80),
    intentRecordSha256: SHA256.test(String(row?.intent_record_sha256 || ''))
      ? String(row.intent_record_sha256) : null,
    completionRecordSha256: SHA256.test(String(row?.completion_record_sha256 || ''))
      ? String(row.completion_record_sha256) : null,
  });
}

/**
 * Classificeert uitsluitend reeds gevonden ledgerrecords. De caller valideert
 * cryptografisch welke records in `records` thuishoren; iedere mislukte
 * validatie gaat afzonderlijk in `invalidRecords` en sluit herstel.
 */
export function classifyTurnEffectManifest({
  parent = null, records = [], invalidRecords = [],
} = {}) {
  let spec = null;
  try { spec = turnEffectManifestSpec(parent); } catch { /* fail closed below */ }
  const verified = Array.isArray(records) ? records : [];
  const invalid = Array.isArray(invalidRecords) ? invalidRecords : [invalidRecords];
  const manifestRows = verified.filter((row) => row?.tool === TURN_EFFECT_MANIFEST_TOOL);
  const exactManifest = spec && manifestRows.length === 1
    && manifestRows[0].execution_key === spec.executionKey
    && manifestRows[0].turn_id === spec.turnId
    && manifestRows[0].status === 'done'
    && manifestRows[0].execution_verification === EXECUTION_VERIFICATION_VERIFIED
    && manifestRows[0].args === canonicalJson(spec.args)
    && manifestRows[0].output === canonicalJson(spec.output)
    && SHA256.test(String(manifestRows[0].completion_record_sha256 || ''));
  const otherRecords = exactManifest
    ? verified.filter((row) => row !== manifestRows[0]) : verified;
  const disposition = exactManifest && invalid.length === 0 && otherRecords.length === 0
    ? 'verified_empty' : 'effect_reconciliation_required';
  const reason = disposition === 'verified_empty' ? 'verified_empty'
    : !spec ? 'parent_binding_invalid'
      : invalid.length ? 'malformed_child_or_manifest'
        : !exactManifest ? 'empty_manifest_missing_or_invalid'
          : 'child_or_effect_present';
  const evidence = Object.freeze({
    contract: 'TurnEffectRecoveryEvidence/v1',
    parentExecutionKey: String(parent?.execution_key || '').slice(0, 512),
    parentIntentRecordSha256: SHA256.test(String(parent?.intent_record_sha256 || ''))
      ? String(parent.intent_record_sha256) : null,
    disposition,
    reason,
    records: Object.freeze(verified.map((row) => effectEvidenceRow(row, true))),
    invalidRecords: Object.freeze(invalid.map((row) => effectEvidenceRow(row, false))),
  });
  return Object.freeze({
    disposition,
    reason,
    evidenceSha256: sha256(canonicalJson(evidence)),
  });
}

function ledgerTemporaryShadow(database) {
  const protectedNames = new Set([
    'turn_tool_executions', ...Object.keys(EXECUTION_LEDGER_INDEX_SQL),
    ...Object.keys(EXECUTION_LEDGER_TRIGGER_SQL),
  ]);
  return database.prepare(`SELECT type,name,tbl_name FROM sqlite_temp_master
    ORDER BY type,name`).all().find((row) => (
    protectedNames.has(String(row.name)) || String(row.tbl_name) === 'turn_tool_executions'
  ));
}

function columnLayout(database) {
  const columns = database.prepare("PRAGMA main.table_info('turn_tool_executions')").all()
    .map((column) => [
      String(column.name), String(column.type).toUpperCase(), Number(column.notnull),
      Number(column.pk), column.dflt_value == null ? null : String(column.dflt_value),
    ]);
  const actual = canonicalJson(columns);
  const layout = Object.entries(EXECUTION_LEDGER_COLUMN_LAYOUTS).find(([, expected]) => (
    canonicalJson(expected) === actual
  ))?.[0] || null;
  if (!layout) return null;
  const tableSql = database.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='table' AND name='turn_tool_executions'`).get()?.sql;
  const recognizedSql = [
    EXECUTION_LEDGER_TABLE_SQL, EXECUTION_LEDGER_MIGRATED_HEAD8_TABLE_SQL,
    EXECUTION_LEDGER_MIGRATED_PRODUCTION14_TABLE_SQL,
  ].some((expected) => normalizeSql(tableSql) === normalizeSql(expected));
  return recognizedSql ? layout : null;
}

function exactIndexes(database) {
  const indexes = database.prepare("PRAGMA main.index_list('turn_tool_executions')").all();
  const expectedNames = [
    'idx_turn_tool_executions_sequence', 'idx_turn_tool_executions_turn',
    'sqlite_autoindex_turn_tool_executions_1',
  ].sort();
  if (canonicalJson(indexes.map((row) => String(row.name)).sort())
    !== canonicalJson(expectedNames)) return false;
  const expected = new Map([
    ['idx_turn_tool_executions_sequence', [0, 1, 'c', [
      [0, 14, 'execution_seq', 0, 'BINARY', 1], [1, -1, null, 0, 'BINARY', 0],
    ]]],
    ['idx_turn_tool_executions_turn', [1, 0, 'c', [
      [0, 1, 'turn_id', 0, 'BINARY', 1], [1, 2, 'tool', 0, 'BINARY', 1],
      [2, -1, null, 0, 'BINARY', 0],
    ]]],
    ['sqlite_autoindex_turn_tool_executions_1', [2, 1, 'pk', [
      [0, 0, 'execution_key', 0, 'BINARY', 1], [1, -1, null, 0, 'BINARY', 0],
    ]]],
  ]);
  return indexes.every((index) => {
    const signature = expected.get(String(index.name));
    if (!signature || Number(index.seq) !== signature[0]
      || Number(index.unique) !== signature[1]
      || String(index.origin) !== signature[2] || Number(index.partial) !== 0) return false;
    const xinfo = database.prepare(`PRAGMA main.index_xinfo('${String(index.name)
      .replaceAll("'", "''")}')`).all().map((entry) => [
      Number(entry.seqno), Number(entry.cid), entry.name == null ? null : String(entry.name),
      Number(entry.desc), String(entry.coll || ''), Number(entry.key),
    ]);
    if (canonicalJson(xinfo) !== canonicalJson(signature[3])) return false;
    if (index.origin !== 'c') return true;
    const sql = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='index' AND name=?`).get(index.name)?.sql;
    return normalizeSql(sql) === normalizeSql(EXECUTION_LEDGER_INDEX_SQL[index.name]);
  });
}

function exactTriggerManifest(database, expectedSql) {
  const rows = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name='turn_tool_executions' ORDER BY name`).all();
  if (canonicalJson(rows.map((row) => String(row.name)))
    !== canonicalJson(Object.keys(expectedSql).sort())) return false;
  return rows.every((row) => normalizeSql(row.sql)
    === normalizeSql(expectedSql[String(row.name)]));
}

function exactTriggers(database) {
  return exactTriggerManifest(database, EXECUTION_LEDGER_TRIGGER_SQL);
}

export function executionLedgerTriggersExact(database) {
  try { return exactTriggers(database); } catch { return false; }
}

export function executionLedgerUpgradePredecessorTriggersExact(database) {
  try {
    return exactTriggerManifest(database, EXECUTION_LEDGER_UPGRADE_PREDECESSOR_TRIGGER_SQL);
  } catch {
    return false;
  }
}

export function executionLedgerSchemaManifest(database) {
  if (!database?.open || typeof database.prepare !== 'function') {
    throw new Error('execution ledger database is unavailable');
  }
  const shadow = ledgerTemporaryShadow(database);
  if (shadow) throw new Error(`execution ledger TEMP shadow: ${shadow.type}:${shadow.name}`);
  const layout = columnLayout(database);
  if (!layout) throw new Error('execution ledger column layout is unrecognized');
  if (!exactIndexes(database)) throw new Error('execution ledger index manifest is inexact');
  if (!exactTriggers(database)) throw new Error('execution ledger trigger manifest is inexact');
  const objects = database.prepare(`SELECT type,name,sql FROM main.sqlite_master
    WHERE name='turn_tool_executions' OR tbl_name='turn_tool_executions'
    ORDER BY type,name`).all().map((row) => ({
    type: String(row.type), name: String(row.name), sql: normalizeSql(row.sql),
  }));
  const body = Object.freeze({
    contract: 'ExecutionLedgerSchemaManifest/v2', layout,
    objects: Object.freeze(objects.map(Object.freeze)),
  });
  return Object.freeze({ ...body, sha256: sha256(canonicalJson(body)) });
}

/** @returns {ExecutionLedgerStoreStatus} */
export function executionLedgerStatus(database) {
  try {
    const manifest = executionLedgerSchemaManifest(database);
    return Object.freeze({
      contract: 'ExecutionLedgerStoreStatus/v2', available: true,
      layout: manifest.layout, manifestSha256: manifest.sha256,
    });
  } catch (error) {
    return Object.freeze({
      contract: 'ExecutionLedgerStoreStatus/v2', available: false,
      reason: String(error?.message || error || 'execution ledger unavailable'),
    });
  }
}

export function requireExecutionLedger(database) {
  const status = executionLedgerStatus(database);
  if (!status.available) throw new Error(`execution ledger unavailable: ${status.reason}`);
  return status;
}
