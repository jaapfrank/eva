import { createHash } from 'node:crypto';
import { db, validateExecutionRecord } from '../db.js';
import { executionLedgerStatus } from '../executionledger.js';
import { canonicalJson, verifiedCompleteReceipt } from '../util.js';
import {
  canonicalArtifactDisplayPath,
  executableWorkspaceArtifact,
  executedArtifactPath,
} from './projectexecution.js';

const SHA256 = /^[a-f0-9]{64}$/u;
const REQUIREMENT_ID = /^r(?:[1-9]|1[0-2])$/u;
const OPERATION_KEY = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,239}$/u;
const VERSION_REF = /^artifact-version:[a-f0-9]{64}$/u;
const MAX_OBJECT_BYTES = 2 * 1024 * 1024;
const MAX_ARTIFACTS_PER_SEAL = 12;
const MAX_RUN_EXECUTIONS_PER_ARTIFACT = 8;
const MAX_PROOF_EXECUTION_KEYS = MAX_ARTIFACTS_PER_SEAL
  * (1 + MAX_RUN_EXECUTIONS_PER_ARTIFACT + 1);
const ROLES = new Set(['source', 'test', 'fixture', 'result']);
const COLLECTION_PATH_LIMIT = 128;
const ARTIFACT_COLLECTION_AUTHORITIES = new WeakMap();
const PROVENANCE_KINDS = new Set([
  'native_verified', 'legacy_ledger_recovered', 'legacy_receipt_recovered',
]);
const TABLES = Object.freeze({
  objects: 'eva_artifact_objects',
  seals: 'eva_artifact_seals',
  versions: 'eva_artifact_versions',
});
const REFERENCE_TABLES = Object.freeze([
  'eva_work_items', 'eva_goal_episodes', 'timeline', 'turn_tool_executions',
]);
const REFERENCE_COLUMN_SIGNATURES = Object.freeze({
  eva_work_items: [
    ['id', 'INTEGER', 0, 1], ['created_ts', 'TEXT', 1, 0],
    ['updated_ts', 'TEXT', 1, 0], ['source', 'TEXT', 1, 0],
    ['owner', 'TEXT', 0, 0], ['title', 'TEXT', 1, 0], ['goal', 'TEXT', 1, 0],
    ['next_step', 'TEXT', 0, 0], ['status', 'TEXT', 1, 0],
    ['evidence', 'TEXT', 0, 0], ['last_action', 'TEXT', 0, 0],
    ['result', 'TEXT', 0, 0], ['visible_to_family', 'INTEGER', 1, 0],
    ['definition_of_done', 'TEXT', 0, 0], ['current_step', 'TEXT', 0, 0],
    ['state_json', 'TEXT', 0, 0], ['wait_json', 'TEXT', 0, 0],
    ['priority', 'INTEGER', 1, 0], ['next_wake_ts', 'TEXT', 0, 0],
    ['revision', 'INTEGER', 1, 0], ['lease_owner', 'TEXT', 0, 0],
    ['lease_until', 'TEXT', 0, 0], ['attempt_count', 'INTEGER', 1, 0],
    ['last_error', 'TEXT', 0, 0], ['interrupted_ts', 'TEXT', 0, 0],
  ],
  eva_goal_episodes: [
    ['id', 'INTEGER', 0, 1], ['episode_key', 'TEXT', 1, 0],
    ['goal_id', 'INTEGER', 1, 0], ['project_id', 'INTEGER', 1, 0],
    ['work_item_id', 'INTEGER', 0, 0], ['owner_actor', 'TEXT', 1, 0],
    ['origin_kind', 'TEXT', 1, 0], ['origin_channel', 'TEXT', 0, 0],
    ['origin_conversation_id', 'TEXT', 0, 0], ['origin_message_id', 'TEXT', 0, 0],
    ['requester', 'TEXT', 0, 0], ['requested_outcome', 'TEXT', 1, 0],
    ['delivery_policy', 'TEXT', 1, 0], ['delivery_target', 'TEXT', 0, 0],
    ['state', 'TEXT', 1, 0], ['next_action', 'TEXT', 0, 0],
    ['checkpoint', 'TEXT', 0, 0], ['proof_summary', 'TEXT', 0, 0],
    ['wake_ts', 'TEXT', 0, 0], ['lease_owner', 'TEXT', 0, 0],
    ['lease_until', 'TEXT', 0, 0], ['created_ts', 'TEXT', 1, 0],
    ['updated_ts', 'TEXT', 1, 0], ['closed_ts', 'TEXT', 0, 0],
    ['revision', 'INTEGER', 1, 0],
  ],
  timeline: [
    ['id', 'INTEGER', 0, 1], ['ts', 'TEXT', 1, 0], ['kind', 'TEXT', 1, 0],
    ['content', 'TEXT', 1, 0], ['meta', 'TEXT', 0, 0], ['outcome_key', 'TEXT', 0, 0],
  ],
});
const REQUIRED_COLUMNS = Object.freeze({
  [TABLES.objects]: [
    'sha256', 'byte_length', 'content', 'created_ts',
  ],
  [TABLES.seals]: [
    'id', 'seal_ref', 'operation_key', 'request_sha256', 'created_ts',
    'work_item_id', 'episode_id', 'expected_work_revision',
    'source_turn_id', 'understanding_sha256', 'authority_timeline_id',
    'authority_sha256',
    'response_json', 'response_sha256',
  ],
  [TABLES.versions]: [
    'id', 'version_ref', 'seal_id', 'ordinal', 'role', 'canonical_path',
    'artifact_sha256', 'byte_length', 'understanding_sha256', 'requirement_id',
    'producer_execution_key', 'producer_completion_sha256', 'proof_json',
    'proof_sha256', 'previous_version_ref', 'provenance_kind',
    'provenance_sha256', 'created_ts',
  ],
});
const REQUIRED_TRIGGERS = Object.freeze([
  'eva_artifact_objects_no_update', 'eva_artifact_objects_no_delete',
  'eva_artifact_objects_no_collision',
  'eva_artifact_seals_no_update', 'eva_artifact_seals_no_delete',
  'eva_artifact_seals_no_collision',
  'eva_artifact_seals_valid_reference',
  'eva_artifact_versions_no_update', 'eva_artifact_versions_no_delete',
  'eva_artifact_versions_no_collision',
  'eva_artifact_versions_valid_reference',
]);
const TRIGGERS_BY_TABLE = Object.freeze({
  [TABLES.objects]: REQUIRED_TRIGGERS.filter((name) => (
    name.startsWith('eva_artifact_objects_')
  )),
  [TABLES.seals]: REQUIRED_TRIGGERS.filter((name) => (
    name.startsWith('eva_artifact_seals_')
  )),
  [TABLES.versions]: REQUIRED_TRIGGERS.filter((name) => (
    name.startsWith('eva_artifact_versions_')
  )),
});
const INDEXES_BY_TABLE = Object.freeze({
  [TABLES.objects]: [],
  [TABLES.seals]: ['idx_artifact_seals_work_episode'],
  [TABLES.versions]: [
    'idx_artifact_versions_work_path', 'idx_artifact_versions_seal',
  ],
});
const INDEX_SIGNATURES_BY_TABLE = Object.freeze({
  [TABLES.objects]: [
    { name: null, unique: 1, origin: 'pk', keys: ['sha256'] },
  ],
  [TABLES.seals]: [
    {
      name: 'idx_artifact_seals_work_episode', unique: 0, origin: 'c',
      keys: ['work_item_id', 'episode_id', 'id'],
    },
    { name: null, unique: 1, origin: 'u', keys: ['seal_ref'] },
    { name: null, unique: 1, origin: 'u', keys: ['operation_key'] },
    { name: null, unique: 1, origin: 'u', keys: ['request_sha256'] },
    { name: null, unique: 1, origin: 'u', keys: ['authority_sha256'] },
    {
      name: null, unique: 1, origin: 'u',
      keys: ['source_turn_id', 'understanding_sha256'],
    },
  ],
  [TABLES.versions]: [
    {
      name: 'idx_artifact_versions_work_path', unique: 0, origin: 'c',
      keys: ['canonical_path', 'id'],
    },
    {
      name: 'idx_artifact_versions_seal', unique: 0, origin: 'c',
      keys: ['seal_id', 'ordinal'],
    },
    { name: null, unique: 1, origin: 'u', keys: ['version_ref'] },
    { name: null, unique: 1, origin: 'u', keys: ['provenance_sha256'] },
    { name: null, unique: 1, origin: 'u', keys: ['seal_id', 'ordinal'] },
    {
      name: null, unique: 1, origin: 'u',
      keys: ['seal_id', 'canonical_path'],
    },
  ],
});

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const nowIso = () => new Date().toISOString();
const hashJson = (value) => sha256(canonicalJson(value));
const artifactDb = /** @type {any} */ (db);
let initializationFailure = '';
const AUTHORITY_VERIFIERS = [];
const ARTIFACT_SEAL_AUTHORITIES = new WeakMap();
const SETTLED_ARTIFACT_SEAL_AUTHORITIES = new WeakMap();

const ARTIFACT_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS eva_artifact_objects (
  sha256 TEXT PRIMARY KEY,
  byte_length INTEGER NOT NULL,
  content BLOB NOT NULL,
  created_ts TEXT NOT NULL,
  CHECK(length(sha256)=64 AND lower(sha256)=sha256
    AND sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(byte_length>=0 AND byte_length<=${MAX_OBJECT_BYTES}),
  CHECK(length(content)=byte_length)
);
CREATE TRIGGER IF NOT EXISTS eva_artifact_objects_no_update
BEFORE UPDATE ON eva_artifact_objects
BEGIN SELECT RAISE(ABORT,'artifact objects are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_objects_no_delete
BEFORE DELETE ON eva_artifact_objects
BEGIN SELECT RAISE(ABORT,'artifact objects are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_objects_no_collision
BEFORE INSERT ON eva_artifact_objects
WHEN EXISTS (
  SELECT 1 FROM eva_artifact_objects WHERE sha256=NEW.sha256
)
BEGIN SELECT RAISE(ABORT,'artifact object identity collision'); END;

CREATE TABLE IF NOT EXISTS eva_artifact_seals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seal_ref TEXT NOT NULL UNIQUE,
  operation_key TEXT NOT NULL UNIQUE,
  request_sha256 TEXT NOT NULL UNIQUE,
  created_ts TEXT NOT NULL,
  work_item_id INTEGER NOT NULL,
  episode_id INTEGER NOT NULL,
  expected_work_revision INTEGER NOT NULL,
  source_turn_id TEXT NOT NULL,
  understanding_sha256 TEXT NOT NULL,
  authority_timeline_id INTEGER NOT NULL,
  authority_sha256 TEXT NOT NULL UNIQUE,
  response_json TEXT NOT NULL,
  response_sha256 TEXT NOT NULL,
  UNIQUE(source_turn_id,understanding_sha256),
  CHECK(length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(response_sha256)=64 AND response_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(work_item_id>=1 AND episode_id>=1 AND expected_work_revision>=0)
  ,CHECK(length(source_turn_id)>=1 AND length(source_turn_id)<=180)
  ,CHECK(length(understanding_sha256)=64
    AND understanding_sha256 NOT GLOB '*[^a-f0-9]*')
  ,CHECK(authority_timeline_id>=1)
  ,CHECK(length(authority_sha256)=64 AND authority_sha256 NOT GLOB '*[^a-f0-9]*')
);
CREATE TRIGGER IF NOT EXISTS eva_artifact_seals_no_update
BEFORE UPDATE ON eva_artifact_seals
BEGIN SELECT RAISE(ABORT,'artifact seals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_seals_no_delete
BEFORE DELETE ON eva_artifact_seals
BEGIN SELECT RAISE(ABORT,'artifact seals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_seals_no_collision
BEFORE INSERT ON eva_artifact_seals
WHEN EXISTS (
  SELECT 1 FROM eva_artifact_seals
  WHERE id=NEW.id
    OR seal_ref=NEW.seal_ref
    OR operation_key=NEW.operation_key
    OR request_sha256=NEW.request_sha256
    OR authority_sha256=NEW.authority_sha256
    OR (source_turn_id=NEW.source_turn_id
      AND understanding_sha256=NEW.understanding_sha256)
)
BEGIN SELECT RAISE(ABORT,'artifact seal identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_seals_valid_reference
BEFORE INSERT ON eva_artifact_seals
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_work_items
    WHERE id=NEW.work_item_id AND revision=NEW.expected_work_revision
  ) THEN RAISE(ABORT,'artifact seal has stale work reference') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_goal_episodes
    WHERE id=NEW.episode_id AND work_item_id=NEW.work_item_id
  ) THEN RAISE(ABORT,'artifact seal has invalid episode reference') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM timeline
    WHERE id=NEW.authority_timeline_id AND kind='turn_understanding'
      AND json_valid(meta)
      AND json_extract(meta,'$.turnId')=NEW.source_turn_id
      AND json_extract(meta,'$.understanding.sourceSha256')=NEW.understanding_sha256
      AND json_extract(meta,'$.interpretationSucceeded')=1
  ) THEN RAISE(ABORT,'artifact seal has invalid turn authority') END;
END;

CREATE TABLE IF NOT EXISTS eva_artifact_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version_ref TEXT NOT NULL UNIQUE,
  seal_id INTEGER NOT NULL,
  ordinal INTEGER NOT NULL,
  role TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  byte_length INTEGER NOT NULL,
  understanding_sha256 TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  producer_execution_key TEXT NOT NULL,
  producer_completion_sha256 TEXT NOT NULL,
  proof_json TEXT NOT NULL,
  proof_sha256 TEXT NOT NULL,
  previous_version_ref TEXT,
  provenance_kind TEXT NOT NULL,
  provenance_sha256 TEXT NOT NULL UNIQUE,
  created_ts TEXT NOT NULL,
  UNIQUE(seal_id,ordinal),
  UNIQUE(seal_id,canonical_path),
  CHECK(ordinal>=0 AND ordinal<${MAX_ARTIFACTS_PER_SEAL}),
  CHECK(role IN ('source','test','fixture','result')),
  CHECK(canonical_path GLOB 'data/workspace/?*'),
  CHECK(length(artifact_sha256)=64 AND artifact_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(byte_length>=0 AND byte_length<=${MAX_OBJECT_BYTES}),
  CHECK(length(understanding_sha256)=64
    AND understanding_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(requirement_id GLOB 'r[1-9]' OR requirement_id GLOB 'r1[0-2]'),
  CHECK(length(producer_completion_sha256)=64
    AND producer_completion_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(proof_sha256)=64 AND proof_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(previous_version_ref IS NULL
    OR previous_version_ref GLOB 'artifact-version:*'),
  CHECK(provenance_kind IN
    ('native_verified','legacy_ledger_recovered','legacy_receipt_recovered')),
  CHECK(length(provenance_sha256)=64
    AND provenance_sha256 NOT GLOB '*[^a-f0-9]*')
);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_work_path
  ON eva_artifact_versions(canonical_path,id);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_seal
  ON eva_artifact_versions(seal_id,ordinal);
CREATE INDEX IF NOT EXISTS idx_artifact_seals_work_episode
  ON eva_artifact_seals(work_item_id,episode_id,id);
CREATE TRIGGER IF NOT EXISTS eva_artifact_versions_no_update
BEFORE UPDATE ON eva_artifact_versions
BEGIN SELECT RAISE(ABORT,'artifact versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_versions_no_delete
BEFORE DELETE ON eva_artifact_versions
BEGIN SELECT RAISE(ABORT,'artifact versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_versions_no_collision
BEFORE INSERT ON eva_artifact_versions
WHEN EXISTS (
  SELECT 1 FROM eva_artifact_versions
  WHERE id=NEW.id
    OR version_ref=NEW.version_ref
    OR (seal_id=NEW.seal_id AND ordinal=NEW.ordinal)
    OR (seal_id=NEW.seal_id AND canonical_path=NEW.canonical_path)
    OR provenance_sha256=NEW.provenance_sha256
)
BEGIN SELECT RAISE(ABORT,'artifact version identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_artifact_versions_valid_reference
BEFORE INSERT ON eva_artifact_versions
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_artifact_seals WHERE id=NEW.seal_id
  ) THEN RAISE(ABORT,'artifact version has invalid seal reference') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_artifact_objects
    WHERE sha256=NEW.artifact_sha256 AND byte_length=NEW.byte_length
  ) THEN RAISE(ABORT,'artifact version has invalid object reference') END;
  SELECT CASE WHEN NEW.previous_version_ref IS NULL AND EXISTS (
    SELECT 1 FROM eva_artifact_versions WHERE canonical_path=NEW.canonical_path
  ) THEN RAISE(ABORT,'artifact version omits lineage') END;
  SELECT CASE WHEN NEW.previous_version_ref IS NOT NULL AND
    NEW.previous_version_ref IS NOT (
      SELECT version_ref FROM eva_artifact_versions
      WHERE canonical_path=NEW.canonical_path ORDER BY id DESC LIMIT 1
    ) THEN RAISE(ABORT,'artifact version has stale lineage') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM main.turn_tool_executions execution
    JOIN eva_artifact_seals seal ON seal.id=NEW.seal_id
    WHERE execution.execution_key=NEW.producer_execution_key
      AND execution.status='done'
      AND execution.completion_record_sha256=NEW.producer_completion_sha256
      AND execution.turn_id=seal.source_turn_id
      AND json_valid(execution.semantic_binding)
      AND json_extract(execution.semantic_binding,'$.understandingSha256')
        =NEW.understanding_sha256
      AND json_extract(execution.semantic_binding,'$.requirementId')=NEW.requirement_id
  ) THEN RAISE(ABORT,'artifact version has invalid producer reference') END;
END;
`;

try {
  artifactDb.exec(ARTIFACT_SCHEMA_SQL);
} catch (error) {
  initializationFailure = String(error?.message || error || 'schema initialization failed');
}

function normalizeSqlOutsideLiterals(value, transform) {
  const source = String(value || '');
  const literals = [];
  let masked = '';
  for (let index = 0; index < source.length;) {
    if (source[index] !== "'") {
      masked += source[index];
      index += 1;
      continue;
    }
    const start = index;
    index += 1;
    while (index < source.length) {
      if (source[index] !== "'") {
        index += 1;
        continue;
      }
      if (source[index + 1] === "'") {
        index += 2;
        continue;
      }
      index += 1;
      break;
    }
    const literalIndex = literals.push(source.slice(start, index)) - 1;
    masked += `\u{e000}${literalIndex}\u{e001}`;
  }
  const normalized = transform(masked);
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literalIndex,
  ) => literals[Number(literalIndex)]);
}

function normalizedDdl(value) {
  return normalizeSqlOutsideLiterals(value, (syntax) => syntax
    .replaceAll('"', '').replaceAll('`', '').replaceAll('[', '').replaceAll(']', '')
    .replace(/\bIF\s+NOT\s+EXISTS\b/giu, '')
    .replace(/\s+/gu, ' ')
    .replace(/\s*([(),;=])\s*/gu, '$1')
    .replace(/;+$/u, '')
    .trim()
    .toLowerCase());
}

function expectedDdl(type, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
  const start = type === 'table' ? `CREATE\\s+TABLE\\s+IF\\s+NOT\\s+EXISTS\\s+${escaped}`
    : type === 'index' ? `CREATE\\s+INDEX\\s+IF\\s+NOT\\s+EXISTS\\s+${escaped}`
      : `CREATE\\s+TRIGGER\\s+IF\\s+NOT\\s+EXISTS\\s+${escaped}`;
  const position = ARTIFACT_SCHEMA_SQL.search(new RegExp(start, 'iu'));
  if (position < 0) return '';
  const tail = ARTIFACT_SCHEMA_SQL.slice(position);
  if (type !== 'trigger') return tail.slice(0, tail.indexOf(';') + 1);
  const nextStatement = tail.search(/\nCREATE\s+(?:TABLE|INDEX|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+/iu);
  return (nextStatement < 0 ? tail : tail.slice(0, nextStatement)).trim();
}

function exactSchemaObject(type, name) {
  try {
    const stored = artifactDb.prepare(
      'SELECT sql FROM main.sqlite_master WHERE type=? AND name=?',
    ).get(type, name)?.sql;
    const expected = expectedDdl(type, name);
    return Boolean(stored && expected && normalizedDdl(stored) === normalizedDdl(expected));
  } catch { return false; }
}

function exactOwnedObjectNames(type, expectedByTable) {
  try {
    return Object.entries(expectedByTable).every(([table, expected]) => {
      const actual = artifactDb.prepare(`SELECT name FROM main.sqlite_master
        WHERE type=? AND tbl_name=? AND sql IS NOT NULL ORDER BY name`)
        .all(type, table).map((row) => row.name);
      return canonicalJson(actual) === canonicalJson([...expected].sort());
    });
  } catch { return false; }
}

function expectedIndexSignature(table, index) {
  const columns = REQUIRED_COLUMNS[table] || [];
  const xinfo = index.keys.map((name, seqno) => ({
    seqno, cid: columns.indexOf(name), name, desc: 0, coll: 'BINARY', key: 1,
  }));
  if (xinfo.some((entry) => entry.cid < 0)) return null;
  xinfo.push({
    seqno: xinfo.length, cid: -1, name: null, desc: 0, coll: 'BINARY', key: 0,
  });
  return {
    name: index.origin === 'c' ? index.name : null,
    unique: index.unique, origin: index.origin, partial: 0, xinfo,
  };
}

function actualIndexSignatures(table) {
  const escapedTable = table.replaceAll("'", "''");
  return artifactDb.prepare(`PRAGMA main.index_list('${escapedTable}')`).all()
    .map((index) => {
      const escapedName = String(index.name).replaceAll("'", "''");
      const xinfo = artifactDb.prepare(`PRAGMA main.index_xinfo('${escapedName}')`).all()
        .map((entry) => ({
          seqno: Number(entry.seqno), cid: Number(entry.cid),
          name: entry.name == null ? null : String(entry.name),
          desc: Number(entry.desc), coll: String(entry.coll || ''),
          key: Number(entry.key),
        }));
      return {
        name: index.origin === 'c' ? String(index.name) : null,
        unique: Number(index.unique), origin: String(index.origin),
        partial: Number(index.partial), xinfo,
      };
    })
    .sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
}

function exactIndexSignatures() {
  try {
    return Object.entries(INDEX_SIGNATURES_BY_TABLE).every(([table, indexes]) => {
      const expected = indexes.map((index) => expectedIndexSignature(table, index));
      if (expected.some((index) => index == null)) return false;
      expected.sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
      return canonicalJson(actualIndexSignatures(table)) === canonicalJson(expected);
    });
  } catch { return false; }
}

function hasCriticalTemporarySchemaObject() {
  try {
    const criticalTables = new Set(
      [...Object.values(TABLES), ...REFERENCE_TABLES].map((name) => name.toLowerCase()),
    );
    return artifactDb.prepare(`SELECT type,name,tbl_name FROM sqlite_temp_master
      ORDER BY type,name`).all().some((row) => (
      String(row.name || '').toLowerCase().startsWith('eva_artifact_')
        || criticalTables.has(String(row.name || '').toLowerCase())
        || criticalTables.has(String(row.tbl_name || '').toLowerCase())
    ));
  } catch { return true; }
}

function schemaFailure() {
  if (initializationFailure) return 'artifact_schema_initialization_failed';
  if (hasCriticalTemporarySchemaObject()) return 'artifact_schema_temp_shadow';
  const executionStatus = executionLedgerStatus(artifactDb);
  if (!executionStatus.available) return `artifact_execution_ledger_invalid:${executionStatus.reason}`;
  for (const [table, columns] of Object.entries(REQUIRED_COLUMNS)) {
    const actualColumns = artifactDb.prepare(`PRAGMA main.table_info('${table}')`).all()
      .map((column) => column.name);
    if (canonicalJson(actualColumns) !== canonicalJson(columns)
      || !exactSchemaObject('table', table)) return `artifact_schema_invalid:${table}`;
  }
  if (!exactOwnedObjectNames('trigger', TRIGGERS_BY_TABLE)
    || REQUIRED_TRIGGERS.some((name) => !exactSchemaObject('trigger', name))) {
    return 'artifact_schema_triggers_invalid';
  }
  if (!exactOwnedObjectNames('index', INDEXES_BY_TABLE)
    || !exactIndexSignatures()) {
    return 'artifact_schema_indexes_invalid';
  }
  for (const index of INDEXES_BY_TABLE[TABLES.versions]) {
    if (!exactSchemaObject('index', index)) return 'artifact_schema_indexes_invalid';
  }
  return '';
}

export function artifactVersionStoreStatus() {
  const reason = schemaFailure();
  return Object.freeze({
    contract: 'ArtifactVersionStoreStatus/v1', available: !reason,
    ...(reason ? { reason } : {}),
  });
}

function requireStore() {
  const status = artifactVersionStoreStatus();
  if (!status.available) throw new Error(`artifact version store unavailable: ${status.reason}`);
}

export function registerArtifactSealAuthorityVerifier(verifier) {
  if (typeof verifier !== 'function') throw new TypeError('artifact seal verifier must be a function');
  if (!AUTHORITY_VERIFIERS.includes(verifier)) AUTHORITY_VERIFIERS.push(verifier);
  return () => {
    const index = AUTHORITY_VERIFIERS.indexOf(verifier);
    if (index >= 0) AUTHORITY_VERIFIERS.splice(index, 1);
  };
}

function exactArtifactSealAttestation(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const fields = [
    'episodeId', 'expectedWorkRevision', 'proofExecutionKeys', 'turnId',
    'understandingSha256', 'workItemId',
  ];
  if (canonicalJson(Object.keys(value).sort()) !== canonicalJson(fields)) return null;
  const workItemId = Number(value.workItemId);
  const episodeId = Number(value.episodeId);
  const expectedWorkRevision = Number(value.expectedWorkRevision);
  const turnId = String(value.turnId || '');
  const understandingSha256 = String(value.understandingSha256 || '');
  const proofExecutionKeys = Array.isArray(value.proofExecutionKeys)
    ? value.proofExecutionKeys.map(String) : [];
  if (!Number.isSafeInteger(workItemId) || workItemId < 1
    || !Number.isSafeInteger(episodeId) || episodeId < 1
    || !Number.isSafeInteger(expectedWorkRevision) || expectedWorkRevision < 0
    || !turnId || turnId.length > 180
    || proofExecutionKeys.length > MAX_PROOF_EXECUTION_KEYS
    || proofExecutionKeys.some((key) => !OPERATION_KEY.test(key))
    || new Set(proofExecutionKeys).size !== proofExecutionKeys.length
    || canonicalJson([...proofExecutionKeys].sort()) !== canonicalJson(proofExecutionKeys)
    || !SHA256.test(understandingSha256)) return null;
  return Object.freeze({
    workItemId, episodeId, expectedWorkRevision, turnId, understandingSha256,
    proofExecutionKeys: Object.freeze(proofExecutionKeys),
  });
}

function durableArtifactTurnAuthority(attestation) {
  const work = artifactDb.prepare('SELECT revision FROM main.eva_work_items WHERE id=?')
    .get(attestation.workItemId);
  const episode = artifactDb.prepare('SELECT work_item_id FROM main.eva_goal_episodes WHERE id=?')
    .get(attestation.episodeId);
  if (!work || !episode || Number(episode.work_item_id) !== attestation.workItemId) return null;
  const rows = artifactDb.prepare(`SELECT id,meta FROM main.timeline
    WHERE kind='turn_understanding' AND json_valid(meta)
      AND json_extract(meta,'$.turnId')=?
    ORDER BY id DESC LIMIT 2`).all(attestation.turnId);
  if (rows.length !== 1) return null;
  let meta;
  try { meta = JSON.parse(rows[0].meta); } catch { return null; }
  const record = meta?.understanding;
  if (meta?.turnId !== attestation.turnId || meta?.interpretationSucceeded !== true
    || record?.contract !== 'TurnUnderstandingRecord/v1'
    || !SHA256.test(String(record?.sha256 || ''))
    || record?.sourceSha256 !== attestation.understandingSha256) return null;
  const { sha256: recordSha256, ...recordBody } = record;
  if (hashJson(recordBody) !== recordSha256) return null;
  const core = {
    contract: 'ArtifactSealAuthority/v1', ...attestation,
    timelineId: Number(rows[0].id),
  };
  const authoritySha256 = hashJson(core);
  if (Number(work.revision) !== attestation.expectedWorkRevision) {
    const historical = artifactDb.prepare(`SELECT 1 FROM main.eva_artifact_seals
      WHERE authority_sha256=? AND work_item_id=? AND episode_id=?
        AND expected_work_revision=? AND source_turn_id=? AND understanding_sha256=?`)
      .get(
        authoritySha256, attestation.workItemId, attestation.episodeId,
        attestation.expectedWorkRevision, attestation.turnId,
        attestation.understandingSha256,
      );
    if (!historical) return null;
  }
  return Object.freeze({ ...core, authoritySha256 });
}

function sameDurableArtifactTurnAuthority(attestation) {
  const rows = artifactDb.prepare(`SELECT id,meta FROM main.timeline
    WHERE kind='turn_understanding' AND json_valid(meta)
      AND json_extract(meta,'$.turnId')=?
    ORDER BY id DESC LIMIT 2`).all(attestation.turnId);
  if (rows.length !== 1 || Number(rows[0].id) !== Number(attestation.timelineId)) return false;
  let meta;
  try { meta = JSON.parse(rows[0].meta); } catch { return false; }
  const record = meta?.understanding;
  if (meta?.turnId !== attestation.turnId || meta?.interpretationSucceeded !== true
    || record?.contract !== 'TurnUnderstandingRecord/v1'
    || record?.sourceSha256 !== attestation.understandingSha256) return false;
  const { sha256: recordSha256, ...recordBody } = record;
  const core = {
    contract: 'ArtifactSealAuthority/v1',
    workItemId: attestation.workItemId, episodeId: attestation.episodeId,
    expectedWorkRevision: attestation.expectedWorkRevision,
    turnId: attestation.turnId, understandingSha256: attestation.understandingSha256,
    proofExecutionKeys: attestation.proofExecutionKeys,
    timelineId: Number(rows[0].id),
  };
  return SHA256.test(String(recordSha256 || '')) && hashJson(recordBody) === recordSha256
    && hashJson(core) === attestation.authoritySha256;
}

/**
 * Mint alleen via een door de host geregistreerde, object-identiteitsgebonden
 * verifier. Vrije ids of een nagemaakte plain object geven nooit seal-authority.
 */
export function artifactSealAuthority(hostContext) {
  requireStore();
  ensureReferenceTables();
  const schemaVersion = mainSchemaVersion();
  let attestation = null;
  for (const verifier of AUTHORITY_VERIFIERS) {
    const candidate = exactArtifactSealAttestation(verifier(hostContext));
    if (!candidate) continue;
    if (attestation && canonicalJson(attestation) !== canonicalJson(candidate)) {
      throw new Error('artifact seal verifiers disagree');
    }
    attestation = candidate;
  }
  // Verifiers zijn hostcallbacks buiten deze store. Controleer de schema-
  // en TEMP-grens opnieuw nadat zo'n callback heeft kunnen draaien.
  requireStore();
  ensureReferenceTables();
  if (mainSchemaVersion() !== schemaVersion) {
    throw new Error('artifact reference schema changed during authority verification');
  }
  if (!attestation) return null;
  const durable = durableArtifactTurnAuthority(attestation);
  if (!durable) return null;
  const authority = Object.freeze({
    contract: durable.contract, authoritySha256: durable.authoritySha256,
  });
  ARTIFACT_SEAL_AUTHORITIES.set(authority, durable);
  return authority;
}

function verifiedArtifactSealAuthority(authority) {
  const attestation = ARTIFACT_SEAL_AUTHORITIES.get(authority);
  if (!attestation || authority?.contract !== 'ArtifactSealAuthority/v1'
    || authority?.authoritySha256 !== attestation.authoritySha256
    || !sameDurableArtifactTurnAuthority(attestation)) {
    throw new Error('artifact seal requires exact current host authority');
  }
  return attestation;
}

function artifactAuthorityBoundToProofs(attestation, proofs) {
  if (!proofs.length || proofs.some((proof) => proof.turnId !== attestation.turnId
    || proof.understandingSha256 !== attestation.understandingSha256)) return false;
  const proofExecutionKeys = proofs.flatMap((proof) => [
    proof.write.executionKey, ...proof.runs.map((run) => run.executionKey),
    proof.read.executionKey,
  ]).sort();
  return canonicalJson(proofExecutionKeys) === canonicalJson(attestation.proofExecutionKeys);
}

function exactBuffer(value) {
  if (Buffer.isBuffer(value)) return Buffer.from(value);
  if (value instanceof Uint8Array) return Buffer.from(value);
  if (typeof value === 'string') return Buffer.from(value, 'utf8');
  throw new TypeError('artifact bytes must be a string, Buffer, or Uint8Array');
}

function validSha256(value, field) {
  const text = String(value || '');
  if (!SHA256.test(text)) throw new TypeError(`${field} must be an exact sha256`);
  return text;
}

function validPositiveId(value, field) {
  const id = Number(value);
  if (!Number.isSafeInteger(id) || id < 1) throw new TypeError(`${field} must be a positive integer`);
  return id;
}

function exactWorkspacePath(value) {
  if (typeof value !== 'string') {
    throw new TypeError('artifact path must already be a canonical data/workspace path');
  }
  const supplied = value;
  const canonical = canonicalArtifactDisplayPath(supplied);
  const parts = supplied.split('/');
  const invalidPart = parts.some((part) => !part || part === '.' || part === '..'
    || part.normalize('NFC') !== part || part.trim() !== part
    || Buffer.byteLength(part, 'utf8') > 255
    || [...part].some((character) => {
      const code = character.codePointAt(0);
      return code <= 31 || code === 127 || (character !== ' ' && /\s/u.test(character));
    }));
  if (!supplied || supplied !== canonical || !supplied.startsWith('data/workspace/')
    || supplied.length === 'data/workspace/'.length
    || supplied.includes(String.fromCharCode(0))
    || supplied.includes('\\') || invalidPart
    || Buffer.byteLength(supplied, 'utf8') > 4_096) {
    throw new TypeError('artifact path must already be a canonical data/workspace path');
  }
  return supplied;
}

function storedObject(artifactSha256) {
  const row = artifactDb.prepare(`SELECT sha256,byte_length,content,created_ts
    FROM main.eva_artifact_objects WHERE sha256=?`).get(artifactSha256);
  if (!row) throw new Error(`artifact object ${artifactSha256} is unavailable`);
  const content = Buffer.from(row.content);
  if (Number(row.byte_length) !== content.length || sha256(content) !== row.sha256) {
    throw new Error(`artifact object ${artifactSha256} failed byte verification`);
  }
  return { ...row, byte_length: Number(row.byte_length), content };
}

export function storeWorkspaceArtifactObject(
  bytes, { expectedSha256 } = /** @type {any} */ ({}),
) {
  requireStore();
  const content = exactBuffer(bytes);
  if (content.length > MAX_OBJECT_BYTES) {
    throw new RangeError(`artifact object exceeds ${MAX_OBJECT_BYTES} bytes`);
  }
  const expected = validSha256(expectedSha256, 'expectedSha256');
  const actual = sha256(content);
  if (actual !== expected) throw new Error('artifact bytes do not match expectedSha256');
  const stored = artifactDb.transaction(() => {
    const existing = artifactDb.prepare(
      'SELECT 1 FROM main.eva_artifact_objects WHERE sha256=?',
    ).get(actual);
    if (!existing) {
      artifactDb.prepare(`INSERT INTO main.eva_artifact_objects
        (sha256,byte_length,content,created_ts) VALUES (?,?,?,?)`)
        .run(actual, content.length, content, nowIso());
    }
    return storedObject(actual);
  }).immediate();
  return Object.freeze({
    contract: 'ArtifactObjectReceipt/v1', objectRef: `sha256:${stored.sha256}`,
    sha256: stored.sha256, bytes: stored.byte_length, createdAt: stored.created_ts,
  });
}

function parseCanonicalJson(value, field) {
  let parsed;
  try { parsed = JSON.parse(String(value)); } catch { throw new Error(`${field} contains invalid JSON`); }
  if (canonicalJson(parsed) !== String(value)) throw new Error(`${field} is not canonical JSON`);
  return parsed;
}

function parseExecutionOutput(value) {
  try { return JSON.parse(String(value)); } catch {
    throw new Error('artifact execution output contains invalid JSON');
  }
}

function executionRow(key, expectedTool, artifact) {
  if (!OPERATION_KEY.test(String(key || ''))) throw new TypeError('invalid execution key');
  const raw = artifactDb.prepare('SELECT * FROM main.turn_tool_executions WHERE execution_key=?')
    .get(String(key));
  let row;
  try { row = validateExecutionRecord(raw); } catch (error) {
    throw new Error(`artifact execution receipt is invalid: ${String(error?.message || error)}`);
  }
  if (!row || row.status !== 'done' || row.tool !== expectedTool) {
    throw new Error(`artifact proof requires completed ${expectedTool}`);
  }
  const binding = parseCanonicalJson(row.semantic_binding, 'artifact semantic binding');
  if (binding?.understandingSha256 !== artifact.understandingSha256
    || binding?.requirementId !== artifact.requirementId) {
    throw new Error('artifact proof has a conflicting semantic binding');
  }
  const expectedOperation = {
    schrijf_bestand: ['workspace.file.write', 'write'],
    lees_bestand: ['workspace.file.read', 'read'],
    voer_uit: ['workspace.program.execute', 'execute'],
  }[expectedTool];
  if (!expectedOperation || binding.capabilityId !== expectedOperation[0]
    || binding.operationId !== expectedOperation[1]) {
    throw new Error(`artifact proof has a conflicting ${expectedTool} operation binding`);
  }
  // Tooluitvoer is bytegelijk door completion_record_sha256 gebonden, maar de
  // productieformatter bewaart haar stabiele presentatiesleutelvolgorde en is
  // daarom niet noodzakelijk canonicalJson-gesorteerd. Args, bindings, proofs
  // en sealrecords blijven hierboven/elders wél strikt canoniek.
  const result = parseExecutionOutput(row.output);
  if (!verifiedCompleteReceipt(result) || result.contract !== 'ActionResult/v1'
    || result.tool !== expectedTool) {
    throw new Error(`artifact proof has incomplete ${expectedTool} result`);
  }
  const sequence = Number(row.execution_seq);
  if (!Number.isSafeInteger(sequence) || sequence < 1) {
    throw new Error('artifact execution receipt has no durable sequence');
  }
  return { row, sequence, args: parseCanonicalJson(row.args, 'artifact execution args'), result };
}

function exactCompletion(execution, supplied, field) {
  const expected = validSha256(supplied, field);
  if (execution.row.completion_record_sha256 !== expected) {
    throw new Error(`${field} conflicts with the durable execution receipt`);
  }
  return expected;
}

function receiptAtPath(receipts, artifact) {
  return (Array.isArray(receipts) ? receipts : []).find((receipt) => (
    receipt?.path === artifact.path && receipt?.sha256 === artifact.sha256
      && Number(receipt?.bytes) === artifact.bytes
  ));
}

function normalizeArtifact(value, index) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`artifacts[${index}] must be an object`);
  }
  const exactFields = [
    'role', 'path', 'sha256', 'understandingSha256', 'requirementId',
    'writeExecutionKey', 'writeCompletionSha256', 'runExecutions',
    'readExecutionKey', 'readCompletionSha256',
  ];
  if (canonicalJson(Object.keys(value).sort()) !== canonicalJson(exactFields.sort())) {
    throw new TypeError(`artifacts[${index}] has an inexact shape`);
  }
  const role = String(value.role || '');
  if (!ROLES.has(role)) throw new TypeError(`artifacts[${index}].role is invalid`);
  const path = exactWorkspacePath(value.path);
  const artifactSha256 = validSha256(value.sha256, `artifacts[${index}].sha256`);
  const understandingSha256 = validSha256(
    value.understandingSha256, `artifacts[${index}].understandingSha256`,
  );
  const requirementId = String(value.requirementId || '');
  if (!REQUIREMENT_ID.test(requirementId)) {
    throw new TypeError(`artifacts[${index}].requirementId is invalid`);
  }
  if (!Array.isArray(value.runExecutions)
    || value.runExecutions.length > MAX_RUN_EXECUTIONS_PER_ARTIFACT) {
    throw new TypeError(`artifacts[${index}].runExecutions is invalid`);
  }
  const runExecutions = value.runExecutions.map((run, runIndex) => {
    if (!run || typeof run !== 'object' || Array.isArray(run)
      || canonicalJson(Object.keys(run).sort())
        !== canonicalJson(['completionSha256', 'executionKey'])) {
      throw new TypeError(`artifacts[${index}].runExecutions[${runIndex}] is invalid`);
    }
    return Object.freeze({
      executionKey: String(run.executionKey || ''),
      completionSha256: validSha256(
        run.completionSha256,
        `artifacts[${index}].runExecutions[${runIndex}].completionSha256`,
      ),
    });
  });
  if (new Set(runExecutions.map((run) => run.executionKey)).size !== runExecutions.length) {
    throw new TypeError(`artifacts[${index}].runExecutions contains duplicates`);
  }
  if (executableWorkspaceArtifact(path) && runExecutions.length === 0) {
    throw new Error(`executable artifact ${path} requires an execution receipt`);
  }
  const object = storedObject(artifactSha256);
  return Object.freeze({
    role, path, sha256: artifactSha256, bytes: object.byte_length,
    understandingSha256, requirementId,
    writeExecutionKey: String(value.writeExecutionKey || ''),
    writeCompletionSha256: validSha256(
      value.writeCompletionSha256, `artifacts[${index}].writeCompletionSha256`,
    ),
    runExecutions,
    readExecutionKey: String(value.readExecutionKey || ''),
    readCompletionSha256: validSha256(
      value.readCompletionSha256, `artifacts[${index}].readCompletionSha256`,
    ),
  });
}

function verifyArtifactProof(artifact, { enforceCurrentSequence = true } = {}) {
  const write = executionRow(artifact.writeExecutionKey, 'schrijf_bestand', artifact);
  exactCompletion(write, artifact.writeCompletionSha256, 'writeCompletionSha256');
  if (canonicalArtifactDisplayPath(write.args?.pad) !== artifact.path
    || typeof write.args?.inhoud !== 'string'
    || Buffer.byteLength(write.args.inhoud, 'utf8') !== artifact.bytes
    || sha256(Buffer.from(write.args.inhoud, 'utf8')) !== artifact.sha256
    || write.result?.data?.path !== artifact.path
    || write.result?.data?.material_sha256 !== artifact.sha256
    || Number(write.result?.data?.bytes) !== artifact.bytes) {
    throw new Error('write receipt does not prove the exact artifact bytes and path');
  }
  const read = executionRow(artifact.readExecutionKey, 'lees_bestand', artifact);
  exactCompletion(read, artifact.readCompletionSha256, 'readCompletionSha256');
  if (canonicalArtifactDisplayPath(read.args?.pad) !== artifact.path
    || typeof read.result?.data !== 'string'
    || Buffer.byteLength(read.result.data, 'utf8') !== artifact.bytes
    || sha256(Buffer.from(read.result.data, 'utf8')) !== artifact.sha256
    || read.result?.artifact_receipt?.path !== artifact.path
    || read.result?.artifact_receipt?.sha256 !== artifact.sha256
    || read.result?.artifact_receipt?.complete_read !== true
    || Number(read.result?.artifact_receipt?.bytes) !== artifact.bytes
    || Number(read.result?.artifact_receipt?.source_bytes) !== artifact.bytes) {
    throw new Error('read receipt does not prove a complete read of the exact artifact');
  }
  if (write.row.turn_id !== read.row.turn_id) {
    throw new Error('artifact write and read receipts belong to different turns');
  }
    const runs = artifact.runExecutions.map((reference) => {
    const run = executionRow(reference.executionKey, 'voer_uit', artifact);
    exactCompletion(run, reference.completionSha256, 'run completionSha256');
    if (run.row.turn_id !== write.row.turn_id
      || (enforceCurrentSequence
        && (run.sequence <= write.sequence || run.sequence >= read.sequence))
      || executedArtifactPath({ args: run.args }) !== artifact.path
      || Number(run.result?.data?.exit_code) !== 0
      || !Array.isArray(run.result?.data?.expected_exit_codes)
      || !run.result.data.expected_exit_codes.includes(0)
      || !receiptAtPath(run.result?.data?.artifact_receipts?.before, artifact)
      || !receiptAtPath(run.result?.data?.artifact_receipts?.after, artifact)) {
      throw new Error('run receipt does not prove stable execution of the exact artifact');
    }
    return Object.freeze({
      executionKey: run.row.execution_key,
      completionRecordSha256: run.row.completion_record_sha256,
      sequence: run.sequence,
    });
  });
  if (enforceCurrentSequence && read.sequence <= write.sequence) {
    throw new Error('artifact readback does not follow its materializing write');
  }
  return Object.freeze({
    contract: 'ArtifactVersionProof/v1',
    turnId: write.row.turn_id,
    understandingSha256: artifact.understandingSha256,
    write: Object.freeze({
      executionKey: write.row.execution_key,
      completionRecordSha256: write.row.completion_record_sha256,
      sequence: write.sequence,
    }),
    runs: Object.freeze(runs),
    read: Object.freeze({
      executionKey: read.row.execution_key,
      completionRecordSha256: read.row.completion_record_sha256,
      sequence: read.sequence,
    }),
  });
}

function immutableVersion(row) {
  return Object.freeze({
    contract: 'ArtifactVersion/v1', id: Number(row.id), versionRef: row.version_ref,
    sealId: Number(row.seal_id), ordinal: Number(row.ordinal), role: row.role,
    path: row.canonical_path, sha256: row.artifact_sha256, bytes: Number(row.byte_length),
    understandingSha256: row.understanding_sha256, requirementId: row.requirement_id,
    producerExecutionKey: row.producer_execution_key,
    producerCompletionSha256: row.producer_completion_sha256,
    proofSha256: row.proof_sha256, previousVersionRef: row.previous_version_ref,
    provenanceKind: row.provenance_kind, provenanceSha256: row.provenance_sha256,
    createdAt: row.created_ts,
  });
}

function versionReceiptProjection(version) {
  return Object.freeze({
    contract: 'ArtifactVersionReceipt/v1', versionRef: version.versionRef,
    objectRef: `sha256:${version.sha256}`, path: version.path, sha256: version.sha256,
    bytes: version.bytes, role: version.role,
    understandingSha256: version.understandingSha256,
    requirementId: version.requirementId,
    previousVersionRef: version.previousVersionRef,
    provenanceSha256: version.provenanceSha256,
  });
}

function verifiedStoredProof(row) {
  const proof = parseCanonicalJson(row.proof_json, 'artifact version proof');
  if (sha256(row.proof_json) !== row.proof_sha256
    || proof?.contract !== 'ArtifactVersionProof/v1'
    || proof?.write?.executionKey !== row.producer_execution_key
    || proof?.write?.completionRecordSha256 !== row.producer_completion_sha256
    || !Array.isArray(proof.runs)) {
    throw new Error('artifact version proof failed integrity verification');
  }
  const artifact = {
    role: row.role, path: row.canonical_path, sha256: row.artifact_sha256,
    bytes: Number(row.byte_length), understandingSha256: row.understanding_sha256,
    requirementId: row.requirement_id,
    writeExecutionKey: proof.write.executionKey,
    writeCompletionSha256: proof.write.completionRecordSha256,
    runExecutions: proof.runs.map((run) => ({
      executionKey: run.executionKey,
      completionSha256: run.completionRecordSha256,
    })),
    readExecutionKey: proof.read?.executionKey,
    readCompletionSha256: proof.read?.completionRecordSha256,
  };
  const sequences = [proof.write?.sequence,
    ...proof.runs.map((run) => run?.sequence), proof.read?.sequence];
  if (sequences.some((sequence) => !Number.isSafeInteger(sequence) || sequence < 1)
    || new Set(sequences).size !== sequences.length
    || proof.write.sequence >= proof.read?.sequence
    || proof.runs.some((run) => (
      run.sequence <= proof.write.sequence || run.sequence >= proof.read.sequence
    ))) {
    throw new Error('artifact version proof has an invalid sealed execution order');
  }
  const actual = verifyArtifactProof(artifact, { enforceCurrentSequence: false });
  if (canonicalJson(actual) !== row.proof_json) {
    throw new Error('artifact version proof conflicts with durable executions');
  }
  return { proof, artifact };
}

function validateStoredVersionBatch(seal) {
  if (!seal || !OPERATION_KEY.test(String(seal.operation_key || ''))
    || !SHA256.test(String(seal.request_sha256 || ''))
    || !SHA256.test(String(seal.response_sha256 || ''))
    || !SHA256.test(String(seal.authority_sha256 || ''))) {
    throw new Error('artifact seal metadata failed integrity verification');
  }
  const rows = artifactDb.prepare(`SELECT * FROM main.eva_artifact_versions
    WHERE seal_id=? ORDER BY ordinal LIMIT ?`).all(
    seal.id, MAX_ARTIFACTS_PER_SEAL + 1,
  );
  if (!rows.length || rows.length > MAX_ARTIFACTS_PER_SEAL
    || rows.some((row, index) => Number(row.ordinal) !== index)) {
    throw new Error('artifact seal has an invalid version batch');
  }
  const proven = rows.map((row) => ({ row, ...verifiedStoredProof(row) }));
  const proofExecutionKeys = proven.flatMap(({ proof }) => [
    proof.write.executionKey, ...proof.runs.map((run) => run.executionKey),
    proof.read.executionKey,
  ]).sort();
  if (!sameDurableArtifactTurnAuthority({
    workItemId: Number(seal.work_item_id), episodeId: Number(seal.episode_id),
    expectedWorkRevision: Number(seal.expected_work_revision),
    turnId: seal.source_turn_id, understandingSha256: seal.understanding_sha256,
    proofExecutionKeys, timelineId: Number(seal.authority_timeline_id),
    authoritySha256: seal.authority_sha256,
  })) throw new Error('artifact seal authority failed durable verification');
  const work = artifactDb.prepare('SELECT id FROM main.eva_work_items WHERE id=?')
    .get(seal.work_item_id);
  const episode = artifactDb.prepare(
    'SELECT work_item_id FROM main.eva_goal_episodes WHERE id=?',
  ).get(seal.episode_id);
  if (!work || Number(episode?.work_item_id) !== Number(seal.work_item_id)) {
    throw new Error('artifact seal lost its work and episode reference');
  }
  const request = {
    contract: 'ArtifactSealRequest/v1', workItemId: Number(seal.work_item_id),
    episodeId: Number(seal.episode_id),
    expectedWorkRevision: Number(seal.expected_work_revision),
    sourceTurnId: seal.source_turn_id,
    understandingSha256: seal.understanding_sha256,
    authorityTimelineId: Number(seal.authority_timeline_id),
    authoritySha256: seal.authority_sha256,
    artifacts: proven.map(({ row, proof }) => ({
      role: row.role, path: row.canonical_path, sha256: row.artifact_sha256,
      bytes: Number(row.byte_length), understandingSha256: row.understanding_sha256,
      requirementId: row.requirement_id,
      writeExecutionKey: proof.write.executionKey,
      writeCompletionSha256: proof.write.completionRecordSha256,
      runExecutions: proof.runs.map((run) => ({
        executionKey: run.executionKey,
        completionSha256: run.completionRecordSha256,
      })),
      readExecutionKey: proof.read.executionKey,
      readCompletionSha256: proof.read.completionRecordSha256,
    })),
  };
  if (hashJson(request) !== seal.request_sha256
    || seal.seal_ref !== `artifact-seal:${hashJson({
      requestSha256: seal.request_sha256, operationKey: seal.operation_key,
    })}`) throw new Error('artifact seal request binding failed integrity verification');
  const versions = proven.map(({ row }, index) => {
    storedObject(row.artifact_sha256);
    const previous = artifactDb.prepare(`SELECT version_ref FROM main.eva_artifact_versions
      WHERE canonical_path=? AND id<? ORDER BY id DESC LIMIT 1`)
      .get(row.canonical_path, row.id)?.version_ref || null;
    const versionRef = `artifact-version:${hashJson({
      sealRef: seal.seal_ref, ordinal: index, role: row.role, path: row.canonical_path,
      sha256: row.artifact_sha256, previousVersionRef: previous,
    })}`;
    const provenanceSha256 = hashJson({
      contract: 'ArtifactVersionProvenance/v1', sealRef: seal.seal_ref,
      versionRef, ordinal: index, workItemId: Number(seal.work_item_id),
      episodeId: Number(seal.episode_id), role: row.role, path: row.canonical_path,
      sha256: row.artifact_sha256, bytes: Number(row.byte_length),
      understandingSha256: row.understanding_sha256,
      requirementId: row.requirement_id, proofSha256: row.proof_sha256,
      previousVersionRef: previous, provenanceKind: row.provenance_kind,
    });
    if (row.version_ref !== versionRef || row.previous_version_ref !== previous
      || row.provenance_kind !== 'native_verified'
      || row.provenance_sha256 !== provenanceSha256
      || row.created_ts !== seal.created_ts) {
      throw new Error('artifact version provenance failed integrity verification');
    }
    return immutableVersion(row);
  });
  const response = parseCanonicalJson(seal.response_json, 'artifact seal response');
  const expectedResponse = {
    contract: 'ArtifactSealReceipt/v1', sealRef: seal.seal_ref,
    operationKey: seal.operation_key, requestSha256: seal.request_sha256,
    workItemId: Number(seal.work_item_id), episodeId: Number(seal.episode_id),
    workRevision: Number(seal.expected_work_revision), createdAt: seal.created_ts,
    sourceTurnId: seal.source_turn_id, understandingSha256: seal.understanding_sha256,
    authoritySha256: seal.authority_sha256,
    versions: versions.map(versionReceiptProjection),
  };
  if (sha256(seal.response_json) !== seal.response_sha256
    || canonicalJson(response) !== canonicalJson(expectedResponse)) {
    throw new Error('artifact seal response failed integrity verification');
  }
  return versions;
}

function validatedStoredVersionRow(row) {
  if (!row) return null;
  const seal = artifactDb.prepare('SELECT * FROM main.eva_artifact_seals WHERE id=?')
    .get(row.seal_id);
  const versions = validateStoredVersionBatch(seal);
  const version = versions.find((item) => Number(item.id) === Number(row.id));
  if (!version) throw new Error('artifact version is absent from its sealed batch');
  return version;
}

function sealResponse(row) {
  const response = parseCanonicalJson(row.response_json, 'artifact seal response');
  if (sha256(row.response_json) !== row.response_sha256
    || response?.contract !== 'ArtifactSealReceipt/v1'
    || response?.sealRef !== row.seal_ref
    || response?.operationKey !== row.operation_key
    || response?.requestSha256 !== row.request_sha256) {
    throw new Error('artifact seal response failed integrity verification');
  }
  const versions = validateStoredVersionBatch(row);
  if (!Array.isArray(response.versions) || response.versions.length !== versions.length
    || response.versions.some((item, index) => (
      item.versionRef !== versions[index].versionRef
        || item.provenanceSha256 !== versions[index].provenanceSha256
    ))) throw new Error('artifact seal response conflicts with its version batch');
  return Object.freeze({ ...response,
    versions: Object.freeze(versions.map(versionReceiptProjection)) });
}

function ensureReferenceTables() {
  for (const table of REFERENCE_TABLES) {
    if (!artifactDb.prepare("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?").get(table)) {
      throw new Error(`artifact reference table unavailable: ${table}`);
    }
    if (table === 'turn_tool_executions') {
      const executionStatus = executionLedgerStatus(artifactDb);
      if (!executionStatus.available) {
        throw new Error(`artifact execution ledger is invalid: ${executionStatus.reason}`);
      }
      continue;
    }
    const actual = artifactDb.prepare(`PRAGMA main.table_info('${table}')`).all()
      .map((column) => [
        String(column.name), String(column.type).toUpperCase(),
        Number(column.notnull), Number(column.pk),
      ]);
    if (canonicalJson(actual) !== canonicalJson(REFERENCE_COLUMN_SIGNATURES[table])) {
      throw new Error(`artifact reference table has an invalid schema: ${table}`);
    }
  }
}

function mainSchemaVersion() {
  const version = Number(artifactDb.prepare('PRAGMA main.schema_version').get()?.schema_version);
  if (!Number.isSafeInteger(version) || version < 0) {
    throw new Error('artifact main schema version is unavailable');
  }
  return version;
}

export function sealVerifiedWorkArtifacts(
  authority, { artifacts, operationKey } = /** @type {any} */ ({}),
) {
  requireStore();
  ensureReferenceTables();
  const issuedAuthority = verifiedArtifactSealAuthority(authority);
  const workId = issuedAuthority.workItemId;
  const episode = issuedAuthority.episodeId;
  const revision = issuedAuthority.expectedWorkRevision;
  const key = String(operationKey || '');
  if (!OPERATION_KEY.test(key)) throw new TypeError('operationKey is invalid');
  if (!Array.isArray(artifacts) || artifacts.length < 1
    || artifacts.length > MAX_ARTIFACTS_PER_SEAL) {
    throw new TypeError(`artifacts must contain between one and ${MAX_ARTIFACTS_PER_SEAL} entries`);
  }
  const normalized = artifacts.map(normalizeArtifact);
  if (new Set(normalized.map((item) => item.path)).size !== normalized.length) {
    throw new TypeError('an artifact seal cannot contain the same path twice');
  }
  const request = Object.freeze({
    contract: 'ArtifactSealRequest/v1', workItemId: workId, episodeId: episode,
    expectedWorkRevision: revision,
    sourceTurnId: issuedAuthority.turnId,
    understandingSha256: issuedAuthority.understandingSha256,
    authorityTimelineId: issuedAuthority.timelineId,
    authoritySha256: issuedAuthority.authoritySha256,
    artifacts: normalized.map((item) => ({
      role: item.role, path: item.path, sha256: item.sha256, bytes: item.bytes,
      understandingSha256: item.understandingSha256, requirementId: item.requirementId,
      writeExecutionKey: item.writeExecutionKey,
      writeCompletionSha256: item.writeCompletionSha256,
      runExecutions: item.runExecutions,
      readExecutionKey: item.readExecutionKey,
      readCompletionSha256: item.readCompletionSha256,
    })),
  });
  const requestSha256 = hashJson(request);
  const settledAuthority = SETTLED_ARTIFACT_SEAL_AUTHORITIES.get(authority);
  if (settledAuthority && (settledAuthority.operationKey !== key
    || settledAuthority.requestSha256 !== requestSha256)) {
    throw new Error('artifact seal authority was already consumed by another batch');
  }
  const existingByKey = artifactDb.prepare(
    'SELECT * FROM main.eva_artifact_seals WHERE operation_key=?',
  ).get(key);
  if (existingByKey) {
    if (existingByKey.request_sha256 !== requestSha256) {
      throw new Error('artifact seal operationKey is bound to another request');
    }
    return sealResponse(existingByKey);
  }
  const duplicateRequest = artifactDb.prepare(
    'SELECT operation_key FROM main.eva_artifact_seals WHERE request_sha256=?',
  ).get(requestSha256);
  if (duplicateRequest) {
    throw new Error('artifact seal request is already bound to another operationKey');
  }
  return artifactDb.transaction(() => {
    const replay = artifactDb.prepare(
      'SELECT * FROM main.eva_artifact_seals WHERE operation_key=?',
    ).get(key);
    if (replay) {
      if (replay.request_sha256 !== requestSha256) {
        throw new Error('artifact seal operationKey changed during transaction');
      }
      return sealResponse(replay);
    }
    if (artifactDb.prepare('SELECT 1 FROM main.eva_artifact_seals WHERE request_sha256=?')
      .get(requestSha256)) {
      throw new Error('artifact seal request changed operationKey during transaction');
    }
    const proofs = normalized.map((artifact) => verifyArtifactProof(artifact));
    if (!artifactAuthorityBoundToProofs(issuedAuthority, proofs)) {
      throw new Error('artifact proofs are not causally bound to the authorized work turn');
    }
    const work = artifactDb.prepare('SELECT revision FROM main.eva_work_items WHERE id=?')
      .get(workId);
    const boundEpisode = artifactDb.prepare(
      'SELECT work_item_id FROM main.eva_goal_episodes WHERE id=?',
    ).get(episode);
    if (!work || Number(work.revision) !== revision) {
      throw new Error('artifact seal work revision is stale');
    }
    if (!boundEpisode || Number(boundEpisode.work_item_id) !== workId) {
      throw new Error('artifact seal episode is not bound to the work item');
    }
    normalized.forEach((item) => storedObject(item.sha256));
    const sealRef = `artifact-seal:${hashJson({ requestSha256, operationKey: key })}`;
    const createdAt = nowIso();
    const prepared = normalized.map((item, ordinal) => {
      const previous = artifactDb.prepare(`SELECT version_ref FROM main.eva_artifact_versions
        WHERE canonical_path=? ORDER BY id DESC LIMIT 1`).get(item.path)?.version_ref || null;
      const proofJson = canonicalJson(proofs[ordinal]);
      const proofSha256 = sha256(proofJson);
      const versionRef = `artifact-version:${hashJson({
        sealRef, ordinal, role: item.role, path: item.path, sha256: item.sha256,
        previousVersionRef: previous,
      })}`;
      const provenanceSha256 = hashJson({
        contract: 'ArtifactVersionProvenance/v1', sealRef, versionRef, ordinal,
        workItemId: workId, episodeId: episode, role: item.role, path: item.path,
        sha256: item.sha256, bytes: item.bytes,
        understandingSha256: item.understandingSha256,
        requirementId: item.requirementId, proofSha256,
        previousVersionRef: previous, provenanceKind: 'native_verified',
      });
      return { item, ordinal, previous, proofJson, proofSha256, versionRef, provenanceSha256 };
    });
    const response = {
      contract: 'ArtifactSealReceipt/v1', sealRef, operationKey: key, requestSha256,
      workItemId: workId, episodeId: episode, workRevision: revision, createdAt,
      sourceTurnId: issuedAuthority.turnId,
      understandingSha256: issuedAuthority.understandingSha256,
      authoritySha256: issuedAuthority.authoritySha256,
      versions: prepared.map(({ item, versionRef, provenanceSha256, previous }) => ({
        contract: 'ArtifactVersionReceipt/v1', versionRef,
        objectRef: `sha256:${item.sha256}`, path: item.path, sha256: item.sha256,
        bytes: item.bytes, role: item.role, understandingSha256: item.understandingSha256,
        requirementId: item.requirementId, previousVersionRef: previous,
        provenanceSha256,
      })),
    };
    const responseJson = canonicalJson(response);
    const sealInfo = artifactDb.prepare(`INSERT INTO main.eva_artifact_seals
      (seal_ref,operation_key,request_sha256,created_ts,work_item_id,episode_id,
       expected_work_revision,source_turn_id,understanding_sha256,
       authority_timeline_id,authority_sha256,response_json,response_sha256)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      sealRef, key, requestSha256, createdAt, workId, episode, revision,
      issuedAuthority.turnId, issuedAuthority.understandingSha256,
      issuedAuthority.timelineId, issuedAuthority.authoritySha256,
      responseJson, sha256(responseJson),
    );
    for (const preparedVersion of prepared) {
      const { item, ordinal, previous, proofJson, proofSha256,
        versionRef, provenanceSha256 } = preparedVersion;
      artifactDb.prepare(`INSERT INTO main.eva_artifact_versions
        (version_ref,seal_id,ordinal,role,canonical_path,artifact_sha256,byte_length,
         understanding_sha256,requirement_id,producer_execution_key,
         producer_completion_sha256,proof_json,proof_sha256,previous_version_ref,
         provenance_kind,provenance_sha256,created_ts)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
        versionRef, Number(sealInfo.lastInsertRowid), ordinal, item.role, item.path,
        item.sha256, item.bytes, item.understandingSha256, item.requirementId,
        item.writeExecutionKey, item.writeCompletionSha256, proofJson, proofSha256,
        previous, 'native_verified', provenanceSha256, createdAt,
      );
    }
    const result = sealResponse(artifactDb.prepare(
      'SELECT * FROM main.eva_artifact_seals WHERE id=?',
    ).get(Number(sealInfo.lastInsertRowid)));
    SETTLED_ARTIFACT_SEAL_AUTHORITIES.set(authority, Object.freeze({
      operationKey: key, requestSha256,
    }));
    return result;
  }).immediate();
}

export function artifactVersion(versionRef) {
  requireStore();
  const reference = String(versionRef || '');
  if (!VERSION_REF.test(reference)) throw new TypeError('invalid artifact versionRef');
  const row = artifactDb.prepare('SELECT * FROM main.eva_artifact_versions WHERE version_ref=?')
    .get(reference);
  return validatedStoredVersionRow(row);
}

export function artifactVersionReceipt(value) {
  requireStore();
  const reference = typeof value === 'string' ? value : String(value?.versionRef || '');
  const version = artifactVersion(reference);
  if (!version) throw new TypeError('invalid artifact version');
  if (typeof value !== 'string' && canonicalJson(value) !== canonicalJson(version)) {
    throw new TypeError('artifact version object conflicts with durable storage');
  }
  return versionReceiptProjection(version);
}

export function artifactVersionsForWork(workItemId) {
  requireStore();
  const workId = validPositiveId(workItemId, 'workItemId');
  return Object.freeze(artifactDb.prepare(`SELECT version.* FROM main.eva_artifact_versions version
    JOIN main.eva_artifact_seals seal ON seal.id=version.seal_id
    WHERE seal.work_item_id=? ORDER BY version.id`).all(workId)
    .map(validatedStoredVersionRow));
}

export function artifactVersionsForEpisode(workItemId, episodeId) {
  requireStore();
  const workId = validPositiveId(workItemId, 'workItemId');
  const goalEpisodeId = validPositiveId(episodeId, 'episodeId');
  return Object.freeze(artifactDb.prepare(`SELECT version.* FROM main.eva_artifact_versions version
    JOIN main.eva_artifact_seals seal ON seal.id=version.seal_id
    WHERE seal.work_item_id=? AND seal.episode_id=? ORDER BY version.id`).all(
    workId, goalEpisodeId,
  ).map(validatedStoredVersionRow));
}

export function artifactBytes(
  versionRef, { expectedSha256 } = /** @type {any} */ ({}),
) {
  const reference = String(versionRef || '');
  if (!VERSION_REF.test(reference)) throw new TypeError('invalid artifact versionRef');
  const expected = validSha256(expectedSha256, 'expectedSha256');
  const version = artifactVersion(reference);
  if (!version) throw new Error(`artifact version ${reference} is unavailable`);
  if (version.sha256 !== expected) {
    throw new Error('artifact version bytes failed integrity verification');
  }
  return Buffer.from(storedObject(version.sha256).content);
}

function exactCollectionPaths(value) {
  const paths = Array.isArray(value) ? value.map(exactWorkspacePath) : [];
  if (!paths.length || paths.length > COLLECTION_PATH_LIMIT
    || new Set(paths).size !== paths.length
    || canonicalJson([...paths].sort((left, right) => (
      Buffer.compare(Buffer.from(left, 'utf8'), Buffer.from(right, 'utf8'))
    ))) !== canonicalJson(paths)) {
    throw new TypeError('artifact collection paths must be unique, sorted workspace paths');
  }
  return paths;
}

function artifactCollectionState({ workItemId, episodeId, paths }) {
  const work = artifactDb.prepare('SELECT revision FROM main.eva_work_items WHERE id=?')
    .get(workItemId);
  const episode = artifactDb.prepare(`SELECT work_item_id,revision,state
    FROM main.eva_goal_episodes WHERE id=?`).get(episodeId);
  if (!work || !episode || Number(episode.work_item_id) !== workItemId) return null;
  const currentHead = (canonicalPath) => {
    const row = artifactDb.prepare(`SELECT version.*,
        seal.work_item_id AS seal_work_item_id,seal.episode_id AS seal_episode_id
      FROM main.eva_artifact_versions version
      JOIN main.eva_artifact_seals seal ON seal.id=version.seal_id
      WHERE version.canonical_path=? ORDER BY version.id DESC LIMIT 1`).get(canonicalPath);
    if (!row) return null;
    return {
      version: validatedStoredVersionRow(row),
      workItemId: Number(row.seal_work_item_id), episodeId: Number(row.seal_episode_id),
    };
  };
  const heads = paths.map((path) => {
    const entry = currentHead(path);
    const version = entry?.version || null;
    const receipt = version ? versionReceiptProjection(version) : null;
    return Object.freeze({
      path,
      versionRef: receipt?.versionRef || null,
      receiptSha256: receipt ? hashJson(receipt) : null,
      workItemId: entry?.workItemId || null,
      episodeId: entry?.episodeId || null,
    });
  });
  // Alleen globale actuele heads uit dit work/episode zijn collectie-inventory.
  // De anti-join gebruikt (canonical_path,id), retourneert maximaal 129 rijen
  // en kent bewust geen levenslange history-cap: oud werk mag een episode niet
  // permanent onbruikbaar maken. Iedere geretourneerde head wordt hieronder
  // nog volledig tegen haar seal/object/proof gevalideerd.
  const headRows = artifactDb.prepare(`SELECT version.*,
      seal.work_item_id AS seal_work_item_id,seal.episode_id AS seal_episode_id
    FROM main.eva_artifact_versions version
    JOIN main.eva_artifact_seals seal ON seal.id=version.seal_id
    WHERE seal.work_item_id=? AND seal.episode_id=?
      AND NOT EXISTS (
        SELECT 1 FROM main.eva_artifact_versions later
        WHERE later.canonical_path=version.canonical_path AND later.id>version.id
      )
    ORDER BY version.canonical_path LIMIT ?`).all(
    workItemId, episodeId, COLLECTION_PATH_LIMIT + 1,
  );
  if (headRows.length > COLLECTION_PATH_LIMIT) {
    throw new Error(`artifact collection target inventory exceeds ${COLLECTION_PATH_LIMIT} heads`);
  }
  const inventory = headRows.map(validatedStoredVersionRow)
    .sort((left, right) => (
      Buffer.compare(Buffer.from(left.path, 'utf8'), Buffer.from(right.path, 'utf8'))
    ))
    .map((version) => {
      const receipt = versionReceiptProjection(version);
      return Object.freeze({
        path: version.path, versionRef: version.versionRef,
        receiptSha256: hashJson(receipt), workItemId, episodeId,
      });
    });
  const state = {
    contract: 'ArtifactCollectionStoreState/v1', workItemId, episodeId,
    workRevision: Number(work.revision), episodeRevision: Number(episode.revision),
    episodeState: String(episode.state || ''),
    heads: Object.freeze(heads), inventory: Object.freeze(inventory),
  };
  return Object.freeze({ ...state, sha256: hashJson(state) });
}

/**
 * Mint een eenmalige, store-owned collectieauthority. De collectiecode krijgt
 * nooit vrije DB- of verifiercallbacks: deze module bepaalt de actuele heads.
 */
export function artifactCollectionStoreAuthority({
  workItemId, episodeId, paths,
} = /** @type {any} */ ({})) {
  requireStore();
  ensureReferenceTables();
  const work = validPositiveId(workItemId, 'workItemId');
  const episode = validPositiveId(episodeId, 'episodeId');
  const canonicalPaths = exactCollectionPaths(paths);
  const state = artifactCollectionState({ workItemId: work, episodeId: episode, paths: canonicalPaths });
  if (!state) return null;
  const capability = Object.freeze({ contract: 'ArtifactCollectionStoreAuthority/v1' });
  ARTIFACT_COLLECTION_AUTHORITIES.set(capability, Object.freeze({
    workItemId: work, episodeId: episode, paths: Object.freeze(canonicalPaths),
    stateSha256: state.sha256,
  }));
  return capability;
}

/**
 * Consumeer authority en vergelijk de volledige actuele storehead opnieuw in
 * één IMMEDIATE transactie. Dit is de CAS-grens voor collection inserts.
 */
export function consumeArtifactCollectionStoreAuthority(authority) {
  requireStore();
  ensureReferenceTables();
  const issued = ARTIFACT_COLLECTION_AUTHORITIES.get(authority);
  ARTIFACT_COLLECTION_AUTHORITIES.delete(authority);
  if (!issued || authority?.contract !== 'ArtifactCollectionStoreAuthority/v1') return null;
  return artifactDb.transaction(() => {
    requireStore();
    ensureReferenceTables();
    const state = artifactCollectionState(issued);
    if (!state || state.sha256 !== issued.stateSha256) return null;
    return state;
  }).immediate();
}

export const artifactVersionContracts = Object.freeze({
  object: 'ArtifactObjectReceipt/v1', seal: 'ArtifactSealReceipt/v1',
  version: 'ArtifactVersion/v1', versionReceipt: 'ArtifactVersionReceipt/v1',
  provenanceKinds: Object.freeze([...PROVENANCE_KINDS]),
  maxObjectBytes: MAX_OBJECT_BYTES,
  maxArtifactsPerSeal: MAX_ARTIFACTS_PER_SEAL,
  collectionStoreState: 'ArtifactCollectionStoreState/v1',
  maxCollectionPaths: COLLECTION_PATH_LIMIT,
});
