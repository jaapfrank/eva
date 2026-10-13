import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { types as utilTypes } from 'node:util';
import { config } from '../config.js';

export const WORKSPACE_GENERATION_STATE_CONTRACT = 'WorkspaceGenerationState/v1';
export const WORKSPACE_GENERATION_CLAIM_CONTRACT = 'WorkspaceGenerationClaim/v1';
export const WORKSPACE_GENERATION_OUTCOME_CONTRACT = 'WorkspaceGenerationOutcome/v1';
export const WORKSPACE_GENERATION_OBSERVATION_CONTRACT = 'WorkspaceRecoveryObservation/v1';
export const OS_EXCLUSIVE_WRITER_UNAVAILABLE = 'os_exclusive_writer_unavailable';

const STATE_TABLE = 'eva_workspace_generation_state_v1';
const CLAIM_TABLE = 'eva_workspace_generation_claims_v1';
const OUTCOME_TABLE = 'eva_workspace_generation_outcomes_v1';
const LANES = new Set(['production', 'synthetic']);
const OUTCOMES = new Set(['success', 'noop', 'failure', 'uncertain']);
const SYNTHETIC_SCOPE = 'test-artifacts';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const POSITIVE_DECIMAL = /^[1-9][0-9]*$/u;
const SCAN_LIMITS = Object.freeze({ entries: 10_000, bytes: 64 * 1024 * 1024, depth: 64 });

const TABLE_DDL = Object.freeze([
  `CREATE TABLE IF NOT EXISTS ${STATE_TABLE} (
    lane TEXT PRIMARY KEY CHECK(lane IN ('production','synthetic')),
    contract TEXT NOT NULL CHECK(contract='${WORKSPACE_GENERATION_STATE_CONTRACT}'),
    root_path TEXT NOT NULL,
    root_identity_sha256 TEXT NOT NULL CHECK(length(root_identity_sha256)=64),
    generation INTEGER NOT NULL CHECK(generation>=0),
    dirty INTEGER NOT NULL CHECK(dirty IN (0,1)),
    active_claim_id TEXT,
    active_owner_json TEXT,
    active_target TEXT,
    active_started_ts TEXT,
    last_outcome TEXT CHECK(last_outcome IS NULL OR last_outcome IN ('success','noop','failure','uncertain')),
    last_outcome_sha256 TEXT CHECK(last_outcome_sha256 IS NULL OR length(last_outcome_sha256)=64),
    CHECK((dirty=0 AND active_claim_id IS NULL AND active_owner_json IS NULL
      AND active_target IS NULL AND active_started_ts IS NULL)
      OR (dirty=1 AND active_claim_id IS NOT NULL AND active_owner_json IS NOT NULL
        AND active_target IS NOT NULL AND active_started_ts IS NOT NULL)),
    CHECK((generation=0 AND last_outcome IS NULL AND last_outcome_sha256 IS NULL)
      OR (generation>0 AND last_outcome IS NOT NULL AND last_outcome_sha256 IS NOT NULL))
  )`,
  `CREATE TABLE IF NOT EXISTS ${CLAIM_TABLE} (
    claim_id TEXT PRIMARY KEY,
    contract TEXT NOT NULL CHECK(contract='${WORKSPACE_GENERATION_CLAIM_CONTRACT}'),
    lane TEXT NOT NULL CHECK(lane IN ('production','synthetic')),
    generation_before INTEGER NOT NULL CHECK(generation_before>=0),
    root_identity_sha256 TEXT NOT NULL CHECK(length(root_identity_sha256)=64),
    target_relative TEXT NOT NULL CHECK(length(target_relative)>0),
    owner_json TEXT NOT NULL CHECK(json_valid(owner_json)),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    claimed_ts TEXT NOT NULL,
    UNIQUE(lane,generation_before)
  )`,
  `CREATE TABLE IF NOT EXISTS ${OUTCOME_TABLE} (
    outcome_id TEXT PRIMARY KEY,
    contract TEXT NOT NULL CHECK(contract='${WORKSPACE_GENERATION_OUTCOME_CONTRACT}'),
    claim_id TEXT NOT NULL UNIQUE,
    lane TEXT NOT NULL CHECK(lane IN ('production','synthetic')),
    generation_after INTEGER NOT NULL CHECK(generation_after>0),
    outcome TEXT NOT NULL CHECK(outcome IN ('success','noop','failure','uncertain')),
    observation_sha256 TEXT CHECK(observation_sha256 IS NULL OR length(observation_sha256)=64),
    observation_json TEXT CHECK(observation_json IS NULL OR json_valid(observation_json)),
    detail_sha256 TEXT NOT NULL CHECK(length(detail_sha256)=64),
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256)=64),
    finished_ts TEXT NOT NULL,
    CHECK((observation_sha256 IS NULL AND observation_json IS NULL)
      OR (observation_sha256 IS NOT NULL AND observation_json IS NOT NULL)),
    UNIQUE(lane,generation_after)
  )`,
]);

const TRIGGER_DDL = Object.freeze([
  `CREATE TRIGGER IF NOT EXISTS ${STATE_TABLE}_no_replace
  BEFORE INSERT ON ${STATE_TABLE}
  WHEN EXISTS(SELECT 1 FROM main.${STATE_TABLE} WHERE lane=NEW.lane)
  BEGIN SELECT RAISE(ABORT,'workspace generation state cannot be replaced'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${STATE_TABLE}_valid_update
  BEFORE UPDATE ON ${STATE_TABLE}
  WHEN NOT (
    NEW.lane=OLD.lane AND NEW.contract=OLD.contract
    AND NEW.root_path=OLD.root_path
    AND NEW.root_identity_sha256=OLD.root_identity_sha256
    AND (
      (OLD.dirty=0 AND NEW.dirty=1 AND NEW.generation=OLD.generation
        AND NEW.active_claim_id IS NOT NULL AND NEW.active_owner_json IS NOT NULL
        AND NEW.active_target IS NOT NULL AND NEW.active_started_ts IS NOT NULL
        AND NEW.last_outcome IS OLD.last_outcome
        AND NEW.last_outcome_sha256 IS OLD.last_outcome_sha256
        AND EXISTS(SELECT 1 FROM main.${CLAIM_TABLE} c
          WHERE c.claim_id=NEW.active_claim_id AND c.lane=NEW.lane
            AND c.generation_before=NEW.generation
            AND c.root_identity_sha256=NEW.root_identity_sha256
            AND c.target_relative=NEW.active_target
            AND c.owner_json=NEW.active_owner_json
            AND c.claimed_ts=NEW.active_started_ts))
      OR
      (OLD.dirty=1 AND NEW.dirty=0 AND NEW.generation=OLD.generation+1
        AND NEW.active_claim_id IS NULL AND NEW.active_owner_json IS NULL
        AND NEW.active_target IS NULL AND NEW.active_started_ts IS NULL
        AND NEW.last_outcome IS NOT NULL AND NEW.last_outcome_sha256 IS NOT NULL
        AND EXISTS(SELECT 1 FROM main.${OUTCOME_TABLE} o
          WHERE o.claim_id=OLD.active_claim_id AND o.lane=NEW.lane
            AND o.generation_after=NEW.generation
            AND o.outcome=NEW.last_outcome
            AND o.receipt_sha256=NEW.last_outcome_sha256))
    )
  )
  BEGIN SELECT RAISE(ABORT,'invalid workspace generation state transition'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${STATE_TABLE}_no_delete
  BEFORE DELETE ON ${STATE_TABLE}
  BEGIN SELECT RAISE(ABORT,'workspace generation state cannot be deleted'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${CLAIM_TABLE}_valid_insert
  BEFORE INSERT ON ${CLAIM_TABLE}
  WHEN EXISTS(SELECT 1 FROM main.${CLAIM_TABLE}
      WHERE claim_id=NEW.claim_id OR (lane=NEW.lane AND generation_before=NEW.generation_before))
    OR NOT EXISTS(SELECT 1 FROM main.${STATE_TABLE} s
      WHERE s.lane=NEW.lane AND s.dirty=0 AND s.generation=NEW.generation_before
        AND s.root_identity_sha256=NEW.root_identity_sha256)
  BEGIN SELECT RAISE(ABORT,'invalid or replacement workspace generation claim'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${CLAIM_TABLE}_no_update
  BEFORE UPDATE ON ${CLAIM_TABLE}
  BEGIN SELECT RAISE(ABORT,'workspace generation claims are immutable'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${CLAIM_TABLE}_no_delete
  BEFORE DELETE ON ${CLAIM_TABLE}
  BEGIN SELECT RAISE(ABORT,'workspace generation claims are immutable'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${OUTCOME_TABLE}_valid_insert
  BEFORE INSERT ON ${OUTCOME_TABLE}
  WHEN EXISTS(SELECT 1 FROM main.${OUTCOME_TABLE}
      WHERE outcome_id=NEW.outcome_id OR claim_id=NEW.claim_id
        OR (lane=NEW.lane AND generation_after=NEW.generation_after))
    OR NOT EXISTS(SELECT 1 FROM main.${STATE_TABLE} s JOIN main.${CLAIM_TABLE} c
      ON c.claim_id=s.active_claim_id
      WHERE s.lane=NEW.lane AND s.dirty=1
        AND c.claim_id=NEW.claim_id AND c.lane=NEW.lane
        AND NEW.generation_after=s.generation+1)
  BEGIN SELECT RAISE(ABORT,'invalid or replacement workspace generation outcome'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${OUTCOME_TABLE}_no_update
  BEFORE UPDATE ON ${OUTCOME_TABLE}
  BEGIN SELECT RAISE(ABORT,'workspace generation outcomes are immutable'); END`,
  `CREATE TRIGGER IF NOT EXISTS ${OUTCOME_TABLE}_no_delete
  BEFORE DELETE ON ${OUTCOME_TABLE}
  BEGIN SELECT RAISE(ABORT,'workspace generation outcomes are immutable'); END`,
]);

const EXPECTED_AUTOINDEXES = Object.freeze({
  [STATE_TABLE]: Object.freeze({
    [`sqlite_autoindex_${STATE_TABLE}_1`]: Object.freeze({
      origin: 'pk', xinfo: Object.freeze([
        Object.freeze([0, 0, 'lane', 0, 'BINARY', 1]),
        Object.freeze([1, -1, null, 0, 'BINARY', 0]),
      ]),
    }),
  }),
  [CLAIM_TABLE]: Object.freeze({
    [`sqlite_autoindex_${CLAIM_TABLE}_1`]: Object.freeze({
      origin: 'pk', xinfo: Object.freeze([
        Object.freeze([0, 0, 'claim_id', 0, 'BINARY', 1]),
        Object.freeze([1, -1, null, 0, 'BINARY', 0]),
      ]),
    }),
    [`sqlite_autoindex_${CLAIM_TABLE}_2`]: Object.freeze({
      origin: 'u', xinfo: Object.freeze([
        Object.freeze([0, 2, 'lane', 0, 'BINARY', 1]),
        Object.freeze([1, 3, 'generation_before', 0, 'BINARY', 1]),
        Object.freeze([2, -1, null, 0, 'BINARY', 0]),
      ]),
    }),
  }),
  [OUTCOME_TABLE]: Object.freeze({
    [`sqlite_autoindex_${OUTCOME_TABLE}_1`]: Object.freeze({
      origin: 'pk', xinfo: Object.freeze([
        Object.freeze([0, 0, 'outcome_id', 0, 'BINARY', 1]),
        Object.freeze([1, -1, null, 0, 'BINARY', 0]),
      ]),
    }),
    [`sqlite_autoindex_${OUTCOME_TABLE}_2`]: Object.freeze({
      origin: 'u', xinfo: Object.freeze([
        Object.freeze([0, 2, 'claim_id', 0, 'BINARY', 1]),
        Object.freeze([1, -1, null, 0, 'BINARY', 0]),
      ]),
    }),
    [`sqlite_autoindex_${OUTCOME_TABLE}_3`]: Object.freeze({
      origin: 'u', xinfo: Object.freeze([
        Object.freeze([0, 3, 'lane', 0, 'BINARY', 1]),
        Object.freeze([1, 4, 'generation_after', 0, 'BINARY', 1]),
        Object.freeze([2, -1, null, 0, 'BINARY', 0]),
      ]),
    }),
  }),
});

function canonicalJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  return `{${Object.keys(value).sort().map((key) => (
    `${JSON.stringify(key)}:${canonicalJson(value[key])}`
  )).join(',')}}`;
}
function sha256(value) {
  return createHash('sha256').update(typeof value === 'string' ? value : canonicalJson(value)).digest('hex');
}
function normalizedSql(value) {
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
      if (source[index] !== "'") { index += 1; continue; }
      if (source[index + 1] === "'") { index += 2; continue; }
      index += 1;
      break;
    }
    const literalIndex = literals.push(source.slice(start, index)) - 1;
    masked += `\u{e000}${literalIndex}\u{e001}`;
  }
  const normalized = masked.replace(/--[^\r\n]*/gu, ' ')
    .replaceAll('"', '').replaceAll('`', '')
    .replaceAll('[', '').replaceAll(']', '')
    .replace(/\bIF\s+NOT\s+EXISTS\b/giu, '')
    .replace(/\s+/gu, ' ').replace(/\s*([(),;=+])\s*/gu, '$1')
    .replace(/;+$/u, '').trim().toLowerCase();
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literalIndex,
  ) => literals[Number(literalIndex)]);
}
function pathInside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
}
function databaseMainPath(database) {
  const rows = database.prepare('PRAGMA main.database_list').all();
  const main = rows.find((row) => String(row.name) === 'main');
  const file = String(main?.file || '');
  if (!file) throw new Error('workspace generation requires a file-backed main database');
  return path.resolve(file);
}
function configuredIdentity(database, lane) {
  const expectedDatabase = path.resolve(config.dataDir, 'eva.db');
  const actualDatabase = databaseMainPath(database);
  if (actualDatabase !== expectedDatabase) {
    throw new Error('workspace generation database is not the configured production database');
  }
  const configuredRoot = path.resolve(config.workspaceDir);
  const rootStat = fs.lstatSync(configuredRoot);
  if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) {
    throw new Error('configured workspace root must be a real directory');
  }
  const root = fs.realpathSync(configuredRoot);
  if (root !== configuredRoot) throw new Error('configured workspace root identity changed');
  const scope = lane === 'synthetic' ? SYNTHETIC_SCOPE : '.';
  const identity = {
    contract: 'WorkspaceRootIdentity/v1', lane, root,
    device: String(rootStat.dev), inode: String(rootStat.ino),
    mode: Number(rootStat.mode), user: String(rootStat.uid), group: String(rootStat.gid),
    database: actualDatabase, scope,
    productionAuthority: lane === 'production',
  };
  return {
    root, scope, device: String(rootStat.dev), inode: String(rootStat.ino),
    sha256: sha256(identity),
  };
}
function assertDatabase(database) {
  if (!database || typeof database.prepare !== 'function' || typeof database.exec !== 'function') {
    throw new TypeError('workspace generation requires an explicit synchronous SQLite database');
  }
}
function protectedNames() {
  return new Set([
    STATE_TABLE, CLAIM_TABLE, OUTCOME_TABLE,
    ...TRIGGER_DDL.map((sql) => sql.match(/CREATE TRIGGER IF NOT EXISTS\s+(\S+)/u)?.[1]),
    ...Object.values(EXPECTED_AUTOINDEXES).flatMap((indexes) => Object.keys(indexes)),
  ].filter(Boolean).map((name) => String(name).toLowerCase()));
}
function assertNoTempShadows(database) {
  const names = protectedNames();
  const rows = database.prepare(
    'SELECT name,tbl_name FROM temp.sqlite_master ORDER BY type,name',
  ).all();
  if (rows.some((row) => names.has(String(row.name || '').toLowerCase())
    || names.has(String(row.tbl_name || '').toLowerCase()))) {
    throw new Error('workspace generation TEMP schema shadow detected');
  }
}
function exactSqlObjects(database) {
  const expected = new Map();
  for (const sql of [...TABLE_DDL, ...TRIGGER_DDL]) {
    const match = sql.match(/CREATE (TABLE|TRIGGER) IF NOT EXISTS\s+(\S+)/u);
    expected.set(`${match[1].toLowerCase()}:${match[2]}`, normalizedSql(sql));
  }
  const tables = [STATE_TABLE, CLAIM_TABLE, OUTCOME_TABLE];
  const placeholders = tables.map(() => '?').join(',');
  const rows = database.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
    WHERE tbl_name IN (${placeholders}) OR name IN (${placeholders})
    ORDER BY type,name`).all(...tables, ...tables);
  const expectedAutoindexes = new Set(Object.values(EXPECTED_AUTOINDEXES)
    .flatMap((indexes) => Object.keys(indexes)));
  const expectedObjectKeys = new Set([
    ...expected.keys(), ...[...expectedAutoindexes].map((name) => `index:${name}`),
  ]);
  const actualKeys = new Set(rows.map((row) => `${row.type}:${row.name}`));
  if (actualKeys.size !== expectedObjectKeys.size
    || [...actualKeys].some((key) => !expectedObjectKeys.has(key))) {
    throw new Error('workspace generation main schema object set changed');
  }
  for (const row of rows) {
    const key = `${row.type}:${row.name}`;
    if (row.type === 'index') {
      if (!expectedAutoindexes.has(row.name) || row.sql !== null) {
        throw new Error('workspace generation autoindex signature changed');
      }
      continue;
    }
    if (normalizedSql(row.sql) !== expected.get(key)) {
      throw new Error(`workspace generation ${key} DDL changed`);
    }
  }
}
function exactAutoindexes(database) {
  for (const [table, expected] of Object.entries(EXPECTED_AUTOINDEXES)) {
    const indexes = database.prepare(`PRAGMA main.index_list('${table}')`).all();
    if (indexes.length !== Object.keys(expected).length) {
      throw new Error(`workspace generation ${table} index set changed`);
    }
    for (const index of indexes) {
      const contract = expected[index.name];
      if (!contract || Number(index.unique) !== 1 || Number(index.partial) !== 0
        || String(index.origin) !== contract.origin) {
        throw new Error(`workspace generation ${table} autoindex metadata changed`);
      }
      const xinfo = database.prepare(`PRAGMA main.index_xinfo('${index.name}')`).all()
        .sort((left, right) => Number(left.seqno) - Number(right.seqno))
        .map((column) => [
          Number(column.seqno), Number(column.cid),
          column.name == null ? null : String(column.name), Number(column.desc),
          String(column.coll || ''), Number(column.key),
        ]);
      if (canonicalJson(xinfo) !== canonicalJson(contract.xinfo)) {
        throw new Error(`workspace generation ${table} autoindex xinfo changed`);
      }
    }
  }
}
function assertMainOrigins(database) {
  const names = protectedNames();
  const rows = database.prepare('SELECT type,name,tbl_name FROM main.sqlite_master ORDER BY type,name').all();
  for (const row of rows) {
    const name = String(row.name || '').toLowerCase();
    const table = String(row.tbl_name || '').toLowerCase();
    if (!names.has(name)) continue;
    if (row.type === 'table' && name === table
      && [STATE_TABLE, CLAIM_TABLE, OUTCOME_TABLE].includes(row.name)) continue;
    if (row.type === 'trigger' && [STATE_TABLE, CLAIM_TABLE, OUTCOME_TABLE].includes(row.tbl_name)) continue;
    if (row.type === 'index' && [STATE_TABLE, CLAIM_TABLE, OUTCOME_TABLE].includes(row.tbl_name)) continue;
    throw new Error('workspace generation protected schema name has the wrong tbl_name');
  }
}
function assertSchema(database) {
  assertNoTempShadows(database);
  assertMainOrigins(database);
  exactSqlObjects(database);
  exactAutoindexes(database);
}
function bootstrapSchema(database) {
  if (database.inTransaction) throw new Error('workspace generation cannot bootstrap in a caller transaction');
  database.exec('BEGIN IMMEDIATE');
  try {
    assertNoTempShadows(database);
    const names = [...protectedNames()];
    const placeholders = names.map(() => '?').join(',');
    const existing = database.prepare(`SELECT COUNT(*) AS count FROM main.sqlite_master
      WHERE lower(name) IN (${placeholders}) OR lower(tbl_name) IN (${placeholders})`)
      .get(...names, ...names).count;
    if (existing === 0) {
      for (const sql of TABLE_DDL) {
        database.exec(sql.replace(
          /^CREATE TABLE IF NOT EXISTS\s+(\S+)/u,
          'CREATE TABLE IF NOT EXISTS main.$1',
        ));
      }
      for (const sql of TRIGGER_DDL) {
        database.exec(sql.replace(
          /^CREATE TRIGGER IF NOT EXISTS\s+(\S+)/u,
          'CREATE TRIGGER IF NOT EXISTS main.$1',
        ));
      }
    }
    assertSchema(database);
    database.exec('COMMIT');
  } catch (error) {
    if (database.inTransaction) database.exec('ROLLBACK');
    throw error;
  }
}
function immediate(database, operation) {
  if (database.inTransaction) throw new Error('workspace generation refuses a caller-owned transaction');
  database.exec('BEGIN IMMEDIATE');
  try {
    assertSchema(database);
    const value = operation();
    database.exec('COMMIT');
    return value;
  } catch (error) {
    if (database.inTransaction) database.exec('ROLLBACK');
    throw error;
  }
}
function readState(database, lane) {
  return database.prepare(`SELECT lane,contract,root_path,root_identity_sha256,generation,dirty,
    active_claim_id,active_owner_json,active_target,active_started_ts,last_outcome,last_outcome_sha256
    FROM main.${STATE_TABLE} WHERE lane=?`).get(lane);
}
function assertStateIdentity(row, identity, lane) {
  if (!row || row.lane !== lane || row.contract !== WORKSPACE_GENERATION_STATE_CONTRACT
    || row.root_path !== identity.root || row.root_identity_sha256 !== identity.sha256
    || !Number.isSafeInteger(row.generation) || row.generation < 0
    || ![0, 1].includes(Number(row.dirty))) {
    throw new Error('workspace generation durable root identity or state changed');
  }
}
function initializeLane(database, identity, lane) {
  immediate(database, () => {
    const prior = readState(database, lane);
    if (!prior) {
      const inserted = database.prepare(`INSERT INTO main.${STATE_TABLE}
        (lane,contract,root_path,root_identity_sha256,generation,dirty)
        VALUES (?,?,?,?,0,0)`).run(
        lane, WORKSPACE_GENERATION_STATE_CONTRACT, identity.root, identity.sha256,
      );
      if (inserted.changes !== 1) throw new Error('workspace generation singleton insert was not exact');
    }
    const row = readState(database, lane);
    assertStateIdentity(row, identity, lane);
    assertDurableBodies(database, identity, lane, row);
  });
}
function parseProcStatStartTicks(value, expectedPid) {
  const source = String(value || '').trim();
  const prefix = `${expectedPid} (`;
  if (!Number.isSafeInteger(expectedPid) || expectedPid <= 0 || !source.startsWith(prefix)) return null;
  const close = source.lastIndexOf(') ');
  if (close <= prefix.length) return null;
  const fields = source.slice(close + 2).trim().split(/\s+/u);
  if (fields.length < 20 || !/^[A-Za-z]$/u.test(fields[0] || '')) return null;
  const startTicks = fields[19];
  return POSITIVE_DECIMAL.test(String(startTicks || '')) ? startTicks : null;
}
function currentOwnerIdentity() {
  let bootId = null;
  let startTicks = null;
  try { bootId = fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim(); } catch { /* failclosed recovery */ }
  try {
    const stat = fs.readFileSync(`/proc/${process.pid}/stat`, 'utf8');
    startTicks = parseProcStatStartTicks(stat, process.pid);
  } catch { /* failclosed recovery */ }
  if (process.platform !== 'linux' || !UUID.test(String(bootId || ''))
    || !POSITIVE_DECIMAL.test(String(startTicks || ''))) {
    throw new Error('workspace writer owner identity is unavailable; claim refused before effect');
  }
  return {
    contract: 'WorkspaceWriterOwner/v1', host: process.platform,
    pid: process.pid, bootId, startTicks,
  };
}
function ownerProvenDead(owner) {
  if (!owner || owner.contract !== 'WorkspaceWriterOwner/v1' || owner.host !== 'linux'
    || !Number.isSafeInteger(owner.pid) || owner.pid <= 0 || !UUID.test(String(owner.bootId || ''))
    || !POSITIVE_DECIMAL.test(String(owner.startTicks || ''))) return false;
  let currentBoot;
  try { currentBoot = fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim(); } catch { return false; }
  if (!UUID.test(currentBoot)) return false;
  if (currentBoot !== owner.bootId) return true;
  let stat;
  try { stat = fs.readFileSync(`/proc/${owner.pid}/stat`, 'utf8'); } catch (error) {
    return error?.code === 'ENOENT';
  }
  const startTicks = parseProcStatStartTicks(stat, owner.pid);
  return startTicks == null ? false : startTicks !== owner.startTicks;
}
function targetBinding(identity, lane, target) {
  const absolute = path.resolve(String(target || ''));
  if (!pathInside(identity.root, absolute) || absolute === identity.root) {
    throw new Error('workspace generation target falls outside the configured root');
  }
  const relative = path.relative(identity.root, absolute).replaceAll(path.sep, '/');
  const synthetic = relative === SYNTHETIC_SCOPE || relative.startsWith(`${SYNTHETIC_SCOPE}/`);
  if (lane === 'production' && synthetic) {
    throw new Error('synthetic artifacts have no production generation authority');
  }
  if (lane === 'synthetic' && !synthetic) {
    throw new Error('synthetic generation target falls outside its separate scope');
  }
  return { absolute, relative };
}
function exactRow(row, expected, label) {
  if (!row || Object.entries(expected).some(([key, value]) => row[key] !== value)) {
    throw new Error(`${label} insert reread did not match`);
  }
}
function sqliteTimestamp(value) {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u.test(value);
}
function parsedCanonical(value, label) {
  if (typeof value !== 'string') throw new Error(`${label} is not stored as TEXT`);
  let parsed;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${label} is not valid JSON`); }
  if (canonicalJson(parsed) !== value) throw new Error(`${label} is not canonical JSON`);
  return parsed;
}
function verifiedClaimRow(row, identity, lane) {
  if (!row || row.contract !== WORKSPACE_GENERATION_CLAIM_CONTRACT || row.lane !== lane
    || !UUID.test(String(row.claim_id || ''))
    || !Number.isSafeInteger(row.generation_before) || row.generation_before < 0
    || row.root_identity_sha256 !== identity.sha256 || !sqliteTimestamp(row.claimed_ts)
    || !/^[0-9a-f]{64}$/u.test(String(row.request_sha256 || ''))) {
    throw new Error('workspace generation claim body is invalid');
  }
  const target = targetBinding(identity, lane, path.join(identity.root, row.target_relative));
  if (target.relative !== row.target_relative) {
    throw new Error('workspace generation claim target is noncanonical');
  }
  const owner = parsedCanonical(row.owner_json, 'workspace generation owner');
  if (canonicalJson(Object.keys(owner || {}).sort()) !== canonicalJson([
    'bootId', 'contract', 'host', 'pid', 'startTicks',
  ])
    || owner?.contract !== 'WorkspaceWriterOwner/v1' || owner?.host !== process.platform
    || !Number.isSafeInteger(owner?.pid) || owner.pid <= 0
    || !UUID.test(String(owner?.bootId || ''))
    || !POSITIVE_DECIMAL.test(String(owner?.startTicks || ''))) {
    throw new Error('workspace generation owner body is invalid');
  }
  const expected = sha256({
    contract: WORKSPACE_GENERATION_CLAIM_CONTRACT,
    claimId: row.claim_id, lane, generationBefore: row.generation_before,
    rootIdentitySha256: identity.sha256, targetRelative: row.target_relative,
    owner, claimedTs: row.claimed_ts,
  });
  if (row.request_sha256 !== expected) {
    throw new Error('workspace generation claim body hash mismatch');
  }
  return row;
}
function verifiedOutcomeRow(row, claim, identity, lane) {
  if (!row || row.contract !== WORKSPACE_GENERATION_OUTCOME_CONTRACT
    || !UUID.test(String(row.outcome_id || ''))
    || row.lane !== lane || row.claim_id !== claim.claim_id
    || row.generation_after !== claim.generation_before + 1
    || !OUTCOMES.has(row.outcome) || !sqliteTimestamp(row.finished_ts)
    || !/^[0-9a-f]{64}$/u.test(String(row.detail_sha256 || ''))
    || !/^[0-9a-f]{64}$/u.test(String(row.receipt_sha256 || ''))
    || (row.observation_sha256 !== null
      && !/^[0-9a-f]{64}$/u.test(String(row.observation_sha256 || '')))
    || (row.observation_sha256 === null) !== (row.observation_json === null)) {
    throw new Error('workspace generation outcome body is invalid');
  }
  if (row.observation_json !== null) {
    const observation = parsedCanonical(
      row.observation_json, 'workspace generation observation body',
    );
    if (observation?.contract !== WORKSPACE_GENERATION_OBSERVATION_CONTRACT
      || observation?.lane !== lane || observation?.claimId !== claim.claim_id
      || observation?.generationBefore !== claim.generation_before
      || observation?.rootIdentitySha256 !== identity.sha256
      || sha256(row.observation_json) !== row.observation_sha256) {
      throw new Error('workspace generation observation body hash mismatch');
    }
  }
  const expected = sha256({
    contract: WORKSPACE_GENERATION_OUTCOME_CONTRACT,
    outcomeId: row.outcome_id, claimId: row.claim_id, lane,
    generationAfter: row.generation_after, outcome: row.outcome,
    observationSha256: row.observation_sha256,
    observationJsonSha256: row.observation_json == null ? null : sha256(row.observation_json),
    detailSha256: row.detail_sha256,
    rootIdentitySha256: identity.sha256, finishedTs: row.finished_ts,
  });
  if (row.receipt_sha256 !== expected) {
    throw new Error('workspace generation outcome body hash mismatch');
  }
  return row;
}
function assertDurableBodies(database, identity, lane, state) {
  const counts = database.prepare(`SELECT
      (SELECT COUNT(*) FROM main.${CLAIM_TABLE} WHERE lane=?) AS claims,
      (SELECT COUNT(*) FROM main.${OUTCOME_TABLE} WHERE lane=?) AS outcomes,
      (SELECT MIN(generation_before) FROM main.${CLAIM_TABLE} WHERE lane=?) AS min_claim,
      (SELECT MAX(generation_before) FROM main.${CLAIM_TABLE} WHERE lane=?) AS max_claim,
      (SELECT MIN(generation_after) FROM main.${OUTCOME_TABLE} WHERE lane=?) AS min_outcome,
      (SELECT MAX(generation_after) FROM main.${OUTCOME_TABLE} WHERE lane=?) AS max_outcome`)
    .get(lane, lane, lane, lane, lane, lane);
  const expectedClaims = state.generation + Number(state.dirty);
  if (counts.claims !== expectedClaims || counts.outcomes !== state.generation
    || (expectedClaims === 0 ? counts.min_claim !== null || counts.max_claim !== null
      : counts.min_claim !== 0 || counts.max_claim !== expectedClaims - 1)
    || (state.generation === 0 ? counts.min_outcome !== null || counts.max_outcome !== null
      : counts.min_outcome !== 1 || counts.max_outcome !== state.generation)) {
    throw new Error('workspace generation durable sequence has a gap or extra row');
  }
  const claims = database.prepare(`SELECT * FROM main.${CLAIM_TABLE}
    WHERE lane=? ORDER BY generation_before`).all(lane);
  const outcomes = database.prepare(`SELECT * FROM main.${OUTCOME_TABLE}
    WHERE lane=? ORDER BY generation_after`).all(lane);
  for (let index = 0; index < claims.length; index += 1) {
    const claim = verifiedClaimRow(claims[index], identity, lane);
    if (claim.generation_before !== index) {
      throw new Error('workspace generation claim sequence is noncanonical');
    }
    if (index >= outcomes.length) continue;
    const outcome = verifiedOutcomeRow(outcomes[index], claim, identity, lane);
    if (outcome.generation_after !== index + 1) {
      throw new Error('workspace generation outcome sequence is noncanonical');
    }
  }
  if (state.dirty === 1) {
    const claim = verifiedClaimRow(database.prepare(
      `SELECT * FROM main.${CLAIM_TABLE} WHERE claim_id=?`,
    ).get(state.active_claim_id), identity, lane);
    if (claim.generation_before !== state.generation
      || claim.owner_json !== state.active_owner_json
      || claim.target_relative !== state.active_target
      || claim.claimed_ts !== state.active_started_ts) {
      throw new Error('workspace generation active state is not bound to its claim body');
    }
  }
  if (state.generation > 0) {
    const outcome = database.prepare(`SELECT * FROM main.${OUTCOME_TABLE}
      WHERE lane=? AND generation_after=?`).get(lane, state.generation);
    const claim = verifiedClaimRow(database.prepare(
      `SELECT * FROM main.${CLAIM_TABLE} WHERE claim_id=?`,
    ).get(outcome?.claim_id), identity, lane);
    verifiedOutcomeRow(outcome, claim, identity, lane);
    if (outcome.outcome !== state.last_outcome
      || outcome.receipt_sha256 !== state.last_outcome_sha256) {
      throw new Error('workspace generation state is not bound to its last outcome body');
    }
  }
}
function claimWrite(database, identity, lane, binding) {
  return immediate(database, () => {
    const state = readState(database, lane);
    assertStateIdentity(state, identity, lane);
    assertDurableBodies(database, identity, lane, state);
    if (state.dirty !== 0 || state.active_claim_id !== null) {
      throw new Error('workspace generation has an active dirty writer claim');
    }
    if (state.generation !== binding.generation) {
      throw new Error('workspace generation expected-generation mismatch');
    }
    const claimId = randomUUID();
    const ownerJson = canonicalJson(currentOwnerIdentity());
    const claimedTs = database.prepare(
      "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now') AS ts",
    ).get().ts;
    const requestSha256 = sha256({
      contract: WORKSPACE_GENERATION_CLAIM_CONTRACT, claimId, lane,
      generationBefore: state.generation, rootIdentitySha256: identity.sha256,
      targetRelative: binding.relative, owner: JSON.parse(ownerJson), claimedTs,
    });
    const inserted = database.prepare(`INSERT INTO main.${CLAIM_TABLE}
      (claim_id,contract,lane,generation_before,root_identity_sha256,target_relative,
        owner_json,request_sha256,claimed_ts)
      VALUES (?,?,?,?,?,?,?,?,?)`).run(
      claimId, WORKSPACE_GENERATION_CLAIM_CONTRACT, lane, state.generation,
      identity.sha256, binding.relative, ownerJson, requestSha256, claimedTs,
    );
    if (inserted.changes !== 1) throw new Error('workspace generation claim insert was not exact');
    const claim = database.prepare(`SELECT * FROM main.${CLAIM_TABLE} WHERE claim_id=?`).get(claimId);
    exactRow(claim, {
      claim_id: claimId, contract: WORKSPACE_GENERATION_CLAIM_CONTRACT, lane,
      generation_before: state.generation, root_identity_sha256: identity.sha256,
      target_relative: binding.relative, owner_json: ownerJson, request_sha256: requestSha256,
      claimed_ts: claimedTs,
    }, 'workspace generation claim');
    verifiedClaimRow(claim, identity, lane);
    const updated = database.prepare(`UPDATE main.${STATE_TABLE}
      SET dirty=1,active_claim_id=?,active_owner_json=?,active_target=?,active_started_ts=?
      WHERE lane=? AND generation=? AND dirty=0 AND active_claim_id IS NULL`).run(
      claimId, ownerJson, binding.relative, claim.claimed_ts, lane, state.generation,
    );
    if (updated.changes !== 1) throw new Error('workspace generation dirty transition was not exact');
    const dirty = readState(database, lane);
    assertStateIdentity(dirty, identity, lane);
    if (dirty.dirty !== 1 || dirty.active_claim_id !== claimId
      || dirty.active_target !== binding.relative || dirty.active_owner_json !== ownerJson) {
      throw new Error('workspace generation dirty transition reread did not match');
    }
    return { claimId, generationBefore: state.generation, ownerJson, target: binding.relative };
  });
}
function finalizeClaim(database, identity, lane, claim, outcome, {
  observation = null, detail = null,
} = {}) {
  if (!OUTCOMES.has(outcome)) throw new Error('invalid workspace generation outcome');
  return immediate(database, () => {
    const state = readState(database, lane);
    assertStateIdentity(state, identity, lane);
    assertDurableBodies(database, identity, lane, state);
    if (state.dirty !== 1 || state.active_claim_id !== claim.claimId
      || state.generation !== claim.generationBefore || state.active_target !== claim.target) {
      throw new Error('workspace generation active claim changed before finalization');
    }
    const generationAfter = state.generation + 1;
    const observationJson = observation == null ? null : canonicalJson(observation);
    const observationSha256 = observationJson == null ? null : sha256(observationJson);
    const detailSha256 = sha256({ contract: 'WorkspaceGenerationOutcomeDetail/v1', detail });
    const outcomeId = randomUUID();
    const finishedTs = database.prepare(
      "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now') AS ts",
    ).get().ts;
    const receiptSha256 = sha256({
      contract: WORKSPACE_GENERATION_OUTCOME_CONTRACT, outcomeId, claimId: claim.claimId,
      lane, generationAfter, outcome, observationSha256, detailSha256,
      observationJsonSha256: observationJson == null ? null : sha256(observationJson),
      rootIdentitySha256: identity.sha256, finishedTs,
    });
    const inserted = database.prepare(`INSERT INTO main.${OUTCOME_TABLE}
      (outcome_id,contract,claim_id,lane,generation_after,outcome,observation_sha256,
        observation_json,detail_sha256,receipt_sha256,finished_ts)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
      outcomeId, WORKSPACE_GENERATION_OUTCOME_CONTRACT, claim.claimId, lane,
      generationAfter, outcome, observationSha256, observationJson,
      detailSha256, receiptSha256, finishedTs,
    );
    if (inserted.changes !== 1) throw new Error('workspace generation outcome insert was not exact');
    const persisted = database.prepare(`SELECT * FROM main.${OUTCOME_TABLE} WHERE outcome_id=?`).get(outcomeId);
    exactRow(persisted, {
      outcome_id: outcomeId, contract: WORKSPACE_GENERATION_OUTCOME_CONTRACT,
      claim_id: claim.claimId, lane, generation_after: generationAfter, outcome,
      observation_sha256: observationSha256, observation_json: observationJson,
      detail_sha256: detailSha256,
      receipt_sha256: receiptSha256,
      finished_ts: finishedTs,
    }, 'workspace generation outcome');
    const persistedClaim = verifiedClaimRow(database.prepare(
      `SELECT * FROM main.${CLAIM_TABLE} WHERE claim_id=?`,
    ).get(claim.claimId), identity, lane);
    verifiedOutcomeRow(persisted, persistedClaim, identity, lane);
    const updated = database.prepare(`UPDATE main.${STATE_TABLE}
      SET generation=?,dirty=0,active_claim_id=NULL,active_owner_json=NULL,
        active_target=NULL,active_started_ts=NULL,last_outcome=?,last_outcome_sha256=?
      WHERE lane=? AND generation=? AND dirty=1 AND active_claim_id=?`).run(
      generationAfter, outcome, receiptSha256, lane, state.generation, claim.claimId,
    );
    if (updated.changes !== 1) throw new Error('workspace generation final transition was not exact');
    const clean = readState(database, lane);
    assertStateIdentity(clean, identity, lane);
    if (clean.generation !== generationAfter || clean.dirty !== 0
      || clean.active_claim_id !== null || clean.last_outcome !== outcome
      || clean.last_outcome_sha256 !== receiptSha256) {
      throw new Error('workspace generation final transition reread did not match');
    }
    return { generation: generationAfter, receiptSha256 };
  });
}

function exactPlainWriteResult(value) {
  if (value === null || typeof value !== 'object') {
    throw new Error('workspace write effect requires an exact synchronous plain result');
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype) {
    throw new Error('workspace write effect requires an exact synchronous plain result');
  }
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Reflect.ownKeys(descriptors);
  if (keys.some((key) => typeof key !== 'string')
    || !keys.includes('outcome')
    || keys.some((key) => !['outcome', 'value'].includes(String(key)))) {
    throw new Error('workspace write effect requires an exact synchronous plain result');
  }
  for (const descriptor of Object.values(descriptors)) {
    if (!Object.hasOwn(descriptor, 'value') || typeof descriptor.get === 'function'
      || typeof descriptor.set === 'function') {
      throw new Error('workspace write effect requires an exact synchronous plain result');
    }
  }
  const outcome = descriptors.outcome.value;
  if (!OUTCOMES.has(outcome)) {
    throw new Error('workspace write effect requires an exact synchronous plain result');
  }
  if (outcome === 'uncertain') {
    throw new Error('workspace write effect cannot self-assert uncertain');
  }
  const result = { outcome };
  if (descriptors.value) {
    const normalizedValue = descriptors.value.value;
    if (normalizedValue !== null && !['string', 'boolean', 'number'].includes(typeof normalizedValue)) {
      throw new Error('workspace write effect requires an exact synchronous plain result');
    }
    if (typeof normalizedValue === 'number' && !Number.isFinite(normalizedValue)) {
      throw new Error('workspace write effect requires an exact synchronous plain result');
    }
    result.value = normalizedValue;
  }
  return result;
}
/** @returns {Error & {code?: string|boolean|number}} */
function normalizedCaughtWriteError(value) {
  let message = typeof value === 'string' ? value : 'workspace write effect failed';
  const fields = {};
  try {
    const descriptors = value != null && ['object', 'function'].includes(typeof value)
      ? Object.getOwnPropertyDescriptors(value) : {};
    if (typeof descriptors.message?.value === 'string' && descriptors.message.value) {
      message = descriptors.message.value;
    }
    for (const key of ['code', 'failure_class', 'effect_uncertain', 'retryable']) {
      const candidate = descriptors[key]?.value;
      if (['string', 'boolean', 'number'].includes(typeof candidate)) fields[key] = candidate;
    }
  } catch { /* hostile thrown proxies remain an opaque, safe error */ }
  return Object.assign(new Error(message), fields);
}
function withGenerationFailure(errorValue, binding) {
  let error = errorValue instanceof Error ? errorValue : new Error(String(errorValue));
  try {
    Object.defineProperty(error, 'workspaceGeneration', {
      value: binding, enumerable: false, configurable: true,
    });
    return error;
  } catch {
    error = new Error(String(errorValue?.message || errorValue || 'workspace write failed'), {
      cause: errorValue,
    });
    Object.defineProperty(error, 'workspaceGeneration', {
      value: binding, enumerable: false, configurable: false,
    });
    return error;
  }
}

function descriptorPath(descriptor, name = '') {
  const base = `/proc/self/fd/${descriptor}`;
  return name ? `${base}/${name}` : base;
}
function strictScanName(value) {
  const raw = Buffer.from(value);
  const name = raw.toString('utf8');
  const safe = name && Buffer.from(name, 'utf8').equals(raw)
    && !['.', '..'].includes(name) && !name.includes('/') && !name.includes('\0')
    && name.normalize('NFC') === name && name.trim() === name
    && Buffer.byteLength(name, 'utf8') <= 255
    && ![...name].some((character) => {
      const code = character.codePointAt(0);
      return code <= 31 || code === 127 || (character !== ' ' && /\s/u.test(character));
    });
  if (!safe) throw new Error('workspace recovery encountered an unsafe path');
  return { raw, name };
}
function scanStatIdentity(stat) {
  return {
    device: String(stat.dev), inode: String(stat.ino), mode: Number(stat.mode),
    links: String(stat.nlink), user: String(stat.uid), group: String(stat.gid),
    size: String(stat.size), modifiedNs: String(stat.mtimeNs), changedNs: String(stat.ctimeNs),
  };
}
function sameScanStat(left, right) {
  return canonicalJson(scanStatIdentity(left)) === canonicalJson(scanStatIdentity(right));
}
function descriptorDirectoryEntries(descriptor, budget, count = true) {
  let directory;
  const entries = [];
  try {
    directory = fs.opendirSync(
      descriptorPath(descriptor), /** @type {any} */ ({ encoding: 'buffer' }),
    );
    for (;;) {
      const entry = directory.readSync();
      if (!entry) break;
      if (count) {
        budget.entries += 1;
        if (budget.entries > SCAN_LIMITS.entries) {
          throw new Error('workspace recovery entry bound exceeded');
        }
      }
      entries.push(strictScanName(entry.name));
    }
  } finally {
    try { directory?.closeSync(); } catch { /* preserve original scan failure */ }
  }
  entries.sort((left, right) => Buffer.compare(left.raw, right.raw));
  if (new Set(entries.map((entry) => entry.name)).size !== entries.length) {
    throw new Error('workspace recovery encountered ambiguous path encoding');
  }
  return entries.map((entry) => entry.name);
}
function openDescriptorChild(parentDescriptor, name, flags) {
  return fs.openSync(descriptorPath(parentDescriptor, name), flags);
}
function hashDescriptorFile(descriptor, stat, budget) {
  const size = Number(stat.size);
  if (!Number.isSafeInteger(size) || size < 0 || size > SCAN_LIMITS.bytes - budget.bytes) {
    throw new Error('workspace recovery byte bound exceeded');
  }
  const hash = createHash('sha256');
  const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, Math.max(1, size)));
  let position = 0;
  while (position < size) {
    const bytes = fs.readSync(
      descriptor, buffer, 0, Math.min(buffer.length, size - position), position,
    );
    if (bytes <= 0) throw new Error('workspace recovery file changed during scan');
    hash.update(buffer.subarray(0, bytes));
    position += bytes;
  }
  const extra = Buffer.allocUnsafe(1);
  if (fs.readSync(descriptor, extra, 0, 1, size) !== 0) {
    throw new Error('workspace recovery file changed during scan');
  }
  budget.bytes += size;
  return hash.digest('hex');
}
function scanWorkspace(identity, lane) {
  if (fs.constants.O_DIRECTORY == null || fs.constants.O_NOFOLLOW == null
    || fs.constants.O_NONBLOCK == null || !fs.existsSync('/proc/self/fd')) {
    throw new Error('secure descriptor workspace recovery is unavailable');
  }
  const directoryFlags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY
    | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK;
  const fileFlags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW
    | fs.constants.O_NONBLOCK;
  const budget = { entries: 1, bytes: 0 };
  const entries = [];
  const directories = [];
  const files = [];
  const descriptors = [];
  const visitDirectory = (
    descriptor, relative, depth, parentDescriptor = null, childName = null,
    kind = 'directory',
  ) => {
    if (depth > SCAN_LIMITS.depth) throw new Error('workspace recovery depth bound exceeded');
    const before = fs.fstatSync(descriptor, { bigint: true });
    if (!before.isDirectory()) throw new Error('workspace recovery directory changed during scan');
    const names = descriptorDirectoryEntries(descriptor, budget);
    entries.push({ path: relative || '.', kind, stat: scanStatIdentity(before) });
    directories.push({
      descriptor, parentDescriptor, childName, relative, stat: before, names,
    });
    if (kind === 'separate_nonproduction_scope') return;
    for (const name of names) {
      const childRelative = relative ? `${relative}/${name}` : name;
      if (Buffer.byteLength(childRelative, 'utf8') > 4_096) {
        throw new Error('workspace recovery path bound exceeded');
      }
      if (lane === 'synthetic' && relative === '' && name !== SYNTHETIC_SCOPE) continue;
      const observed = fs.lstatSync(descriptorPath(descriptor, name), { bigint: true });
      if (observed.isSymbolicLink()) {
        throw new Error('workspace recovery refuses symbolic links');
      }
      if (observed.isDirectory()) {
        const childDescriptor = openDescriptorChild(descriptor, name, directoryFlags);
        descriptors.push(childDescriptor);
        const exact = fs.fstatSync(childDescriptor, { bigint: true });
        if (!exact.isDirectory() || !sameScanStat(observed, exact)) {
          throw new Error('workspace recovery directory changed during scan');
        }
        const childKind = lane === 'production' && childRelative === SYNTHETIC_SCOPE
          ? 'separate_nonproduction_scope' : 'directory';
        visitDirectory(
          childDescriptor, childRelative, depth + 1, descriptor, name, childKind,
        );
        continue;
      }
      if (!observed.isFile()) throw new Error('workspace recovery refuses non-regular files');
      const fileDescriptor = openDescriptorChild(descriptor, name, fileFlags);
      descriptors.push(fileDescriptor);
      const descriptorBefore = fs.fstatSync(fileDescriptor, { bigint: true });
      if (!descriptorBefore.isFile() || !sameScanStat(observed, descriptorBefore)) {
        throw new Error('workspace recovery file changed during scan');
      }
      const contentSha256 = hashDescriptorFile(fileDescriptor, descriptorBefore, budget);
      const descriptorAfter = fs.fstatSync(fileDescriptor, { bigint: true });
      if (!sameScanStat(descriptorBefore, descriptorAfter)) {
        throw new Error('workspace recovery file changed during scan');
      }
      entries.push({
        path: childRelative, kind: 'file', stat: scanStatIdentity(descriptorAfter),
        sha256: contentSha256,
      });
      files.push({
        descriptor: fileDescriptor, parentDescriptor: descriptor,
        childName: name, relative: childRelative, stat: descriptorAfter,
        sha256: contentSha256,
      });
    }
    const afterNames = descriptorDirectoryEntries(descriptor, budget, false);
    const after = fs.fstatSync(descriptor, { bigint: true });
    if (canonicalJson(names) !== canonicalJson(afterNames) || !sameScanStat(before, after)) {
      throw new Error('workspace recovery directory changed during scan');
    }
  };

  try {
    const rootLstat = fs.lstatSync(identity.root, { bigint: true });
    const rootRealpath = fs.realpathSync.native(identity.root);
    if (rootLstat.isSymbolicLink() || !rootLstat.isDirectory()
      || rootRealpath !== identity.root || String(rootLstat.dev) !== identity.device
      || String(rootLstat.ino) !== identity.inode) {
      throw new Error('workspace recovery root identity changed');
    }
    const rootDescriptor = fs.openSync(identity.root, directoryFlags);
    descriptors.push(rootDescriptor);
    const rootStat = fs.fstatSync(rootDescriptor, { bigint: true });
    if (!sameScanStat(rootLstat, rootStat)) {
      throw new Error('workspace recovery root identity changed');
    }
    visitDirectory(rootDescriptor, '', 0);

    for (const file of files) {
      const held = fs.fstatSync(file.descriptor, { bigint: true });
      if (!held.isFile() || !sameScanStat(held, file.stat)
        || hashDescriptorFile(file.descriptor, held, { entries: 0, bytes: 0 }) !== file.sha256) {
        throw new Error('workspace recovery file changed during end fence');
      }
      const rebound = openDescriptorChild(file.parentDescriptor, file.childName, fileFlags);
      try {
        if (!sameScanStat(fs.fstatSync(rebound, { bigint: true }), file.stat)) {
          throw new Error('workspace recovery file changed during end fence');
        }
      } finally { fs.closeSync(rebound); }
    }
    for (const directory of directories) {
      const names = descriptorDirectoryEntries(directory.descriptor, budget, false);
      const held = fs.fstatSync(directory.descriptor, { bigint: true });
      if (canonicalJson(names) !== canonicalJson(directory.names)
        || !held.isDirectory() || !sameScanStat(held, directory.stat)) {
        throw new Error('workspace recovery directory changed during end fence');
      }
      if (directory.parentDescriptor != null) {
        const rebound = openDescriptorChild(
          directory.parentDescriptor, directory.childName, directoryFlags,
        );
        try {
          if (!sameScanStat(fs.fstatSync(rebound, { bigint: true }), directory.stat)) {
            throw new Error('workspace recovery directory changed during end fence');
          }
        } finally { fs.closeSync(rebound); }
      }
    }

    const reboundRootLstat = fs.lstatSync(identity.root, { bigint: true });
    const reboundRootRealpath = fs.realpathSync.native(identity.root);
    if (reboundRootLstat.isSymbolicLink() || !reboundRootLstat.isDirectory()
      || reboundRootRealpath !== identity.root || !sameScanStat(reboundRootLstat, rootStat)) {
      throw new Error('workspace recovery root identity changed during end fence');
    }
    const reboundRootDescriptor = fs.openSync(identity.root, directoryFlags);
    try {
      if (!sameScanStat(fs.fstatSync(reboundRootDescriptor, { bigint: true }), rootStat)) {
        throw new Error('workspace recovery root identity changed during end fence');
      }
    } finally { fs.closeSync(reboundRootDescriptor); }

    entries.sort((left, right) => Buffer.compare(
      Buffer.from(left.path, 'utf8'), Buffer.from(right.path, 'utf8'),
    ));
    const observation = {
      contract: WORKSPACE_GENERATION_OBSERVATION_CONTRACT, lane,
      rootIdentitySha256: identity.sha256, limits: SCAN_LIMITS,
      entries, entryCount: budget.entries, byteCount: budget.bytes,
    };
    return { sha256: sha256(observation), observation };
  } finally {
    for (const descriptor of [...descriptors].reverse()) {
      try { fs.closeSync(descriptor); } catch { /* descriptor already closed */ }
    }
  }
}

export function createWorkspaceGenerationStore(database, options = {}) {
  assertDatabase(database);
  const lane = String(options.lane || 'production');
  if (!LANES.has(lane)) throw new Error('unknown workspace generation lane');
  if (options.workspaceRoot != null
    && path.resolve(String(options.workspaceRoot)) !== path.resolve(config.workspaceDir)) {
    throw new Error('workspace generation refuses a non-configured root');
  }
  const identity = configuredIdentity(database, lane);
  bootstrapSchema(database);
  initializeLane(database, identity, lane);

  const writeCapabilities = new WeakMap();
  const spentWriteCapabilities = new WeakSet();
  const recoveryCapabilities = new WeakMap();
  const spentRecoveryCapabilities = new WeakSet();

  const inspect = () => {
    assertSchema(database);
    const currentIdentity = configuredIdentity(database, lane);
    if (currentIdentity.sha256 !== identity.sha256) {
      throw new Error('configured workspace root identity changed');
    }
    const row = readState(database, lane);
    assertStateIdentity(row, identity, lane);
    assertDurableBodies(database, identity, lane, row);
    return {
      contract: WORKSPACE_GENERATION_STATE_CONTRACT, lane, root: identity.root,
      generation: row.generation, dirty: row.dirty === 1,
      activeClaimId: row.active_claim_id, lastOutcome: row.last_outcome,
    };
  };
  const issueWriteCapability = (request = /** @type {any} */ ({})) => {
    const { target, expectedGeneration = null } = request;
    const targetInfo = targetBinding(identity, lane, target);
    const state = inspect();
    if (state.dirty) throw new Error('workspace generation has an active dirty writer claim');
    const expected = expectedGeneration == null ? state.generation : Number(expectedGeneration);
    if (!Number.isSafeInteger(expected) || expected !== state.generation) {
      throw new Error('workspace generation expected-generation mismatch');
    }
    const capability = Object.freeze(Object.create(null));
    writeCapabilities.set(capability, {
      generation: expected, absolute: targetInfo.absolute, relative: targetInfo.relative,
      rootIdentitySha256: identity.sha256,
    });
    return capability;
  };
  const runClaimedWrite = (capability, effect, optionsOverride = {}) => {
    const binding = capability && writeCapabilities.get(capability);
    if (!binding || spentWriteCapabilities.has(capability)) {
      throw new Error('workspace write requires a live store-owned opaque one-shot capability');
    }
    spentWriteCapabilities.add(capability);
    if (typeof effect !== 'function') throw new TypeError('workspace write effect must be a function');
    if (utilTypes.isAsyncFunction(effect) || utilTypes.isGeneratorFunction(effect)) {
      throw new Error('workspace write effect requires an exact synchronous plain result');
    }
    if (optionsOverride.target != null) {
      const override = targetBinding(identity, lane, optionsOverride.target);
      if (override.absolute !== binding.absolute) throw new Error('workspace write target differs from opaque target');
    }
    const claim = claimWrite(database, identity, lane, binding);
    let result;
    let caught = null;
    try {
      result = exactPlainWriteResult(effect({
        target: binding.absolute, generation: binding.generation, claimId: claim.claimId,
      }));
    } catch (error) {
      caught = normalizedCaughtWriteError(error);
      result = { outcome: 'uncertain' };
    }
    let final;
    try {
      final = finalizeClaim(database, identity, lane, claim, result.outcome, {
        detail: caught ? { name: String(caught?.name || 'Error'), code: String(caught?.code || '') }
          : { returned: true },
      });
    } catch (finalizationError) {
      if (caught && finalizationError && !finalizationError.cause) finalizationError.cause = caught;
      throw finalizationError;
    }
    if (caught) {
      const failureBinding = Object.freeze({
        contract: 'WorkspaceGenerationFailure/v1', lane,
        generation: final.generation, outcome: 'uncertain',
        receiptSha256: final.receiptSha256,
      });
      throw withGenerationFailure(caught, failureBinding);
    }
    return {
      ...result, generation: final.generation, receiptSha256: final.receiptSha256,
    };
  };
  const issueRecoveryCapability = () => {
    const state = inspect();
    if (!state.dirty || !state.activeClaimId) return null;
    const row = readState(database, lane);
    let owner;
    try { owner = JSON.parse(row.active_owner_json); } catch { return null; }
    if (!ownerProvenDead(owner)) return null;
    let scan;
    try { scan = scanWorkspace(identity, lane); } catch { return null; }
    const capability = Object.freeze(Object.create(null));
    recoveryCapabilities.set(capability, {
      claimId: row.active_claim_id, generation: row.generation,
      target: row.active_target, ownerJson: row.active_owner_json,
      observationSha256: scan.sha256, observation: scan.observation,
    });
    return capability;
  };
  const recover = (capability) => {
    const proof = capability && recoveryCapabilities.get(capability);
    if (!proof || spentRecoveryCapabilities.has(capability)) {
      throw new Error('workspace recovery requires a live store-owned opaque host verifier');
    }
    spentRecoveryCapabilities.add(capability);
    const state = readState(database, lane);
    assertStateIdentity(state, identity, lane);
    if (state.dirty !== 1 || state.active_claim_id !== proof.claimId
      || state.generation !== proof.generation || state.active_owner_json !== proof.ownerJson
      || state.active_target !== proof.target) {
      throw new Error('workspace recovery proof no longer binds the active writer');
    }
    let owner;
    try { owner = JSON.parse(state.active_owner_json); } catch { return null; }
    if (!ownerProvenDead(owner)) throw new Error('workspace recovery no longer proves the old owner dead');
    let reboundObservation;
    try { reboundObservation = scanWorkspace(identity, lane); } catch { return null; }
    if (reboundObservation.sha256 !== proof.observationSha256) {
      throw new Error('workspace recovery observation changed after host verification');
    }
    const boundObservation = {
      ...proof.observation,
      claimId: proof.claimId,
      generationBefore: proof.generation,
    };
    const finalized = finalizeClaim(database, identity, lane, {
      claimId: proof.claimId, generationBefore: proof.generation, target: proof.target,
    }, 'uncertain', {
      observation: boundObservation,
      detail: { recovery: true, ownerProvenDead: true, fullBoundedNoFollowObservation: true },
    });
    return { outcome: 'uncertain', generation: finalized.generation,
      observationSha256: sha256(canonicalJson(boundObservation)),
      receiptSha256: finalized.receiptSha256 };
  };

  return Object.freeze({
    lane, inspect, issueWriteCapability, runClaimedWrite,
    issueRecoveryCapability, recover,
  });
}

export const workspaceGenerationContracts = Object.freeze({
  state: WORKSPACE_GENERATION_STATE_CONTRACT,
  claim: WORKSPACE_GENERATION_CLAIM_CONTRACT,
  outcome: WORKSPACE_GENERATION_OUTCOME_CONTRACT,
  observation: WORKSPACE_GENERATION_OBSERVATION_CONTRACT,
  cooperativeBoundary: 'coordinator_only_not_os_sibling_proof',
  productionWriterUnavailable: OS_EXCLUSIVE_WRITER_UNAVAILABLE,
});

export const __workspaceGenerationTest = Object.freeze({
  ownerProvenDead, parseProcStatStartTicks,
});
