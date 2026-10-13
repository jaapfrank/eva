import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';
import Database from 'better-sqlite3';

import {
  EXECUTION_LEDGER_INDEX_SQL,
  EXECUTION_LEDGER_TABLE_SQL,
  EXECUTION_LEDGER_TRIGGER_SQL,
} from '../src/executionledger.js';
import { canonicalJson } from '../src/util.js';

const dataDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-v2-');
process.env.EVA_DATA_DIR = dataDir;

const {
  claimTurnToolExecution,
  completeTurnToolExecution,
  db,
  executionLedgerStoreStatus,
  transitionTurnToolExecution,
  validateExecutionRecord,
} = await import('../src/db.js');

const CONTRACT_V1 = 'ExecutionRecord/v1';
const CONTRACT_V2 = 'ExecutionRecord/v2';
const VERIFIED = 'verified';
const LEGACY_UNVERIFIABLE = 'legacy_unverifiable';

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function parsedIntent(row) {
  return {
    args: row.args == null ? null : JSON.parse(row.args),
    binding: row.semantic_binding == null ? null : JSON.parse(row.semantic_binding),
  };
}

function intentRecordV1(row) {
  const { args, binding } = parsedIntent(row);
  return sha256(canonicalJson({
    turnId: row.turn_id,
    tool: row.tool,
    args,
    binding,
    semanticHash: row.semantic_hash,
  }));
}

function intentRecordV2(row) {
  const { args, binding } = parsedIntent(row);
  return sha256(canonicalJson({
    executionContract: row.execution_contract,
    executionSeq: row.execution_seq,
    turnId: row.turn_id,
    tool: row.tool,
    args,
    binding,
    semanticHash: row.semantic_hash,
  }));
}

function completionRecordV1(row) {
  return sha256(canonicalJson({
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  }));
}

function completionRecordV2(row) {
  return sha256(canonicalJson({
    executionContract: row.execution_contract,
    executionSeq: row.execution_seq,
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  }));
}

function createDeployedHeadLedger(targetDataDir, rows, { extraSql = '' } = {}) {
  const legacyDb = new Database(path.join(targetDataDir, 'eva.db'));
  legacyDb.exec(`CREATE TABLE turn_tool_executions (
    execution_key TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    started_ts TEXT NOT NULL,
    finished_ts TEXT,
    output TEXT
  );
  CREATE INDEX idx_turn_tool_executions_turn
    ON turn_tool_executions(turn_id, tool);
  ${extraSql}`);
  const insert = legacyDb.prepare(`INSERT INTO turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,finished_ts,output)
    VALUES (@execution_key,@turn_id,@tool,@args,@status,@started_ts,@finished_ts,@output)`);
  for (const row of rows) insert.run(row);
  return legacyDb;
}

function createProductionBackup14Ledger(targetDataDir, rows) {
  const legacyDb = new Database(path.join(targetDataDir, 'eva.db'));
  legacyDb.exec(`CREATE TABLE turn_tool_executions (
  execution_key TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  output TEXT
, semantic_hash TEXT, semantic_binding TEXT, output_sha256 TEXT,
material_sha256 TEXT, intent_record_sha256 TEXT, completion_record_sha256 TEXT);
CREATE INDEX idx_turn_tool_executions_turn ON turn_tool_executions(turn_id, tool);`);
  const insert = legacyDb.prepare(`INSERT INTO turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,
     semantic_hash,semantic_binding,output_sha256,material_sha256,
     intent_record_sha256,completion_record_sha256)
    VALUES (@execution_key,@turn_id,@tool,@args,@status,@started_ts,@finished_ts,@output,
     @semantic_hash,@semantic_binding,@output_sha256,@material_sha256,
     @intent_record_sha256,@completion_record_sha256)`);
  for (const row of rows) insert.run(row);
  return legacyDb;
}

test('ExecutionRecord-contract is exact, verplicht en immutable in de main-ledger', () => {
  const column = db.prepare('PRAGMA main.table_info(turn_tool_executions)').all()
    .find((candidate) => candidate.name === 'execution_contract');
  assert.deepEqual(
    column && { type: column.type, notnull: column.notnull, dflt: column.dflt_value },
    { type: 'TEXT', notnull: 1, dflt: "'ExecutionRecord/v1'" },
  );
  assert.deepEqual(db.prepare(`SELECT name FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name='turn_tool_executions' ORDER BY name`).all()
  .map((row) => row.name), [
    'turn_tool_executions_contract_immutable',
    'turn_tool_executions_intent_immutable',
    'turn_tool_executions_key_immutable',
    'turn_tool_executions_no_delete',
    'turn_tool_executions_no_replace',
    'turn_tool_executions_sequence_immutable',
    'turn_tool_executions_terminal_immutable',
    'turn_tool_executions_valid_contract',
    'turn_tool_executions_valid_sequence',
    'turn_tool_executions_valid_verification',
  ]);

  const claimed = claimTurnToolExecution({
    executionKey: 'execution:v2:schema',
    turnId: 'turn:v2:schema',
    tool: 'bewijs_tool',
    args: { exact: true },
  });
  assert.equal(claimed.row.execution_contract, CONTRACT_V2);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions
    SET execution_contract=? WHERE execution_key=?`).run(
    CONTRACT_V1, claimed.row.execution_key,
  ), /execution contract is immutable/u);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions
    SET execution_key=? WHERE execution_key=?`).run(
    'execution:v2:renamed', claimed.row.execution_key,
  ), /execution key is immutable/u);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions
    SET args=? WHERE execution_key=?`).run(
    canonicalJson({ exact: false }), claimed.row.execution_key,
  ), /execution intent is immutable/u);

  assert.throws(() => db.prepare(`INSERT OR REPLACE INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,intent_record_sha256,status,
     started_ts,execution_seq,execution_contract)
    VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    claimed.row.execution_key, 'turn:forged', 'probe', 'null', 'a'.repeat(64),
    'b'.repeat(64), 'started', new Date().toISOString(), claimed.row.execution_seq,
    CONTRACT_V2,
  ), /execution record replacement is forbidden/u);

  assert.throws(() => db.prepare(`INSERT OR REPLACE INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,intent_record_sha256,status,
     started_ts,execution_seq,execution_contract)
    VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    'execution:v2:replace-by-sequence', 'turn:forged', 'probe', 'null',
    'a'.repeat(64), 'b'.repeat(64), 'started', new Date().toISOString(),
    claimed.row.execution_seq, CONTRACT_V2,
  ), /execution record replacement is forbidden/u);
});

test('delete kan een sequence nooit vrijgeven voor hergebruik', () => {
  const first = claimTurnToolExecution({
    executionKey: 'execution:v2:no-delete:first', turnId: 'turn:v2:no-delete',
    tool: 'bewijs_tool', args: { index: 1 },
  });
  assert.throws(() => db.prepare(`DELETE FROM main.turn_tool_executions
    WHERE execution_key=?`).run(first.row.execution_key), /record deletion is forbidden/u);
  const second = claimTurnToolExecution({
    executionKey: 'execution:v2:no-delete:second', turnId: 'turn:v2:no-delete',
    tool: 'bewijs_tool', args: { index: 2 },
  });
  assert.equal(second.row.execution_seq, first.row.execution_seq + 1);
});

test('finale v2 transportrecords zijn immutable terwijl retryable CAS door kan', () => {
  const retryKey = 'execution:v2:transport:retry';
  claimTurnToolExecution({
    executionKey: retryKey, turnId: 'turn:v2:transport',
    tool: '@transport/telegram.sendMessage', args: { deliveryKey: retryKey },
    initialStatus: 'transport_sending',
  });
  assert.equal(transitionTurnToolExecution(retryKey, {
    from: 'transport_sending', status: 'transport_retryable',
    output: { failure: 'network' }, finished: false,
  }).changed, true);
  assert.equal(transitionTurnToolExecution(retryKey, {
    from: 'transport_retryable', status: 'transport_sending',
    output: null, finished: false,
  }).changed, true);

  const acceptedKey = 'execution:v2:transport:accepted';
  claimTurnToolExecution({
    executionKey: acceptedKey, turnId: 'turn:v2:transport',
    tool: '@transport/telegram.sendMessage', args: { deliveryKey: acceptedKey },
    initialStatus: 'transport_sending',
  });
  assert.equal(transitionTurnToolExecution(acceptedKey, {
    from: 'transport_sending', status: 'transport_accepted',
    output: { accepted: true },
  }).changed, true);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions
    SET status='transport_rejected',output=? WHERE execution_key=?`).run(
    '{"accepted":false}', acceptedKey,
  ), /execution terminal record is immutable/u);
});

test('een verse v2 receipt bindt contract en store-sequence in intentie en voltooiing', () => {
  const executionKey = 'execution:v2:sealed';
  const claimed = claimTurnToolExecution({
    executionKey,
    turnId: 'turn:v2:sealed',
    tool: 'bewijs_tool',
    args: { index: 2, action: 'seal' },
  });
  assert.equal(claimed.row.execution_contract, CONTRACT_V2);
  assert.equal(claimed.row.intent_record_sha256, intentRecordV2(claimed.row));
  assert.notEqual(claimed.row.intent_record_sha256, intentRecordV1(claimed.row));

  const completed = completeTurnToolExecution(executionKey, '{"ok":true}').row;
  assert.equal(completed.completion_record_sha256, completionRecordV2(completed));
  assert.notEqual(completed.completion_record_sha256, completionRecordV1(completed));
  assert.equal(validateExecutionRecord(completed).execution_contract, CONTRACT_V2);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET
    output=?,output_sha256=?,completion_record_sha256=? WHERE execution_key=?`).run(
    '{"ok":false}', sha256('{"ok":false}'), 'f'.repeat(64), executionKey,
  ), /execution terminal record is immutable/u);

  const originalSequence = completed.execution_seq;
  const originalIntent = completed.intent_record_sha256;
  const sequenceTrigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='turn_tool_executions_sequence_immutable'`).get().sql;
  const intentTrigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='turn_tool_executions_intent_immutable'`).get().sql;
  db.exec('DROP TRIGGER main.turn_tool_executions_sequence_immutable');
  let intentTriggerDropped = false;
  try {
    db.prepare(`UPDATE main.turn_tool_executions SET execution_seq=?
      WHERE execution_key=?`).run(originalSequence + 10_000, executionKey);
    let tampered = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey);
    assert.throws(() => validateExecutionRecord(tampered), /intentierecordhash/u);
    db.exec('DROP TRIGGER main.turn_tool_executions_intent_immutable');
    intentTriggerDropped = true;
    db.prepare(`UPDATE main.turn_tool_executions SET intent_record_sha256=?
      WHERE execution_key=?`).run(intentRecordV2(tampered), executionKey);
    tampered = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey);
    assert.throws(() => validateExecutionRecord(tampered), /eindrecordhash/u);
  } finally {
    if (intentTriggerDropped) {
      db.prepare(`UPDATE main.turn_tool_executions SET intent_record_sha256=?
        WHERE execution_key=?`).run(originalIntent, executionKey);
    }
    db.prepare(`UPDATE main.turn_tool_executions SET execution_seq=?
      WHERE execution_key=?`).run(originalSequence, executionKey);
    if (intentTriggerDropped) db.exec(intentTrigger);
    db.exec(sequenceTrigger);
  }
  assert.equal(validateExecutionRecord(db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey)).execution_seq, originalSequence);
});

test('een bestaande v1 receipt behoudt exact de oude hashes en kan niet als v2 doorgaan', () => {
  const executionKey = 'execution:legacy:v1';
  const row = {
    execution_key: executionKey,
    turn_id: 'turn:legacy:v1',
    tool: 'legacy_probe',
    args: canonicalJson({ legacy: true }),
    semantic_hash: sha256('legacy-semantic-intent'),
    semantic_binding: null,
    status: 'done',
    started_ts: '2026-01-02T03:04:05.000Z',
    finished_ts: '2026-01-02T03:04:06.000Z',
    output: '{"legacy":true}',
    material_sha256: null,
    execution_contract: CONTRACT_V1,
    execution_verification: VERIFIED,
  };
  row.output_sha256 = sha256(row.output);
  row.intent_record_sha256 = intentRecordV1(row);
  row.execution_seq = db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS seq
    FROM main.turn_tool_executions`).get().seq;
  row.completion_record_sha256 = completionRecordV1(row);

  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,semantic_binding,
     intent_record_sha256,status,started_ts,finished_ts,output,output_sha256,
     material_sha256,completion_record_sha256,execution_seq,execution_contract,
     execution_verification)
    VALUES (@execution_key,@turn_id,@tool,@args,@semantic_hash,@semantic_binding,
     @intent_record_sha256,@status,@started_ts,@finished_ts,@output,@output_sha256,
     @material_sha256,@completion_record_sha256,@execution_seq,@execution_contract,
     @execution_verification)`).run(row);

  const stored = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  assert.equal(validateExecutionRecord(stored).execution_contract, CONTRACT_V1);
  assert.equal(stored.intent_record_sha256, intentRecordV1(stored));
  assert.equal(stored.completion_record_sha256, completionRecordV1(stored));
  assert.throws(
    () => validateExecutionRecord({ ...stored, execution_contract: CONTRACT_V2 }),
    /intentierecordhash/u,
  );
  assert.throws(
    () => validateExecutionRecord({ ...stored, execution_contract: 'ExecutionRecord/v9' }),
    /onbekend uitvoeringscontract/u,
  );
});

test('de echte deployed-HEAD 8-koloms ledger migreert started en done deterministisch', async (t) => {
  const firstDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-head8-first-');
  const vacuumDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-head8-vacuum-');
  t.after(() => {
    fs.rmSync(firstDir, { recursive: true, force: true });
    fs.rmSync(vacuumDir, { recursive: true, force: true });
  });
  const rows = [
    {
      execution_key: 'head8:z', turn_id: 'turn:head8', tool: 'legacy_probe',
      args: canonicalJson({ order: 'z' }), status: 'started',
      started_ts: '2025-01-01T00:00:00.000Z', finished_ts: null, output: null,
    },
    {
      execution_key: 'head8:a', turn_id: 'turn:head8', tool: 'legacy_probe',
      args: canonicalJson({ order: 'a' }), status: 'done',
      started_ts: '2025-01-01T00:00:00.000Z',
      finished_ts: '2025-01-01T00:00:01.000Z', output: '{"ok":true}',
    },
  ];
  const firstDb = createDeployedHeadLedger(firstDir, rows);
  firstDb.close();
  const vacuumDb = createDeployedHeadLedger(vacuumDir, [...rows].reverse());
  vacuumDb.exec(`VACUUM INTO '${path.join(vacuumDir, 'copy.db').replaceAll("'", "''")}'`);
  vacuumDb.close();
  fs.renameSync(path.join(vacuumDir, 'copy.db'), path.join(vacuumDir, 'vacuum.db'));
  fs.renameSync(path.join(vacuumDir, 'eva.db'), path.join(vacuumDir, 'original.db'));
  fs.renameSync(path.join(vacuumDir, 'vacuum.db'), path.join(vacuumDir, 'eva.db'));

  const script = `
    const {db,executionLedgerStoreStatus}=await import('./src/db.js');
    const rows=db.prepare('SELECT * FROM main.turn_tool_executions ORDER BY execution_seq').all();
    process.stdout.write(JSON.stringify({status:executionLedgerStoreStatus(),rows}));
    db.close();
  `;
  const [first, vacuum] = await Promise.all([
    runProcess(script, firstDir), runProcess(script, vacuumDir),
  ]);
  assert.equal(first.status.available, true);
  assert.equal(vacuum.status.available, true);
  assert.deepEqual(first.rows.map((row) => [row.execution_key, row.execution_seq]), [
    ['head8:a', 1], ['head8:z', 2],
  ]);
  assert.deepEqual(
    vacuum.rows.map((row) => [row.execution_key, row.execution_seq]),
    first.rows.map((row) => [row.execution_key, row.execution_seq]),
  );
  const done = first.rows.find((row) => row.execution_key === 'head8:a');
  assert.equal(done.execution_contract, CONTRACT_V1);
  assert.equal(done.execution_verification, LEGACY_UNVERIFIABLE);
  assert.equal(done.semantic_hash, null);
  assert.equal(done.intent_record_sha256, null);
  assert.equal(done.completion_record_sha256, null);
  const started = first.rows.find((row) => row.execution_key === 'head8:z');
  assert.equal(started.execution_contract, CONTRACT_V1);
  assert.equal(started.execution_verification, LEGACY_UNVERIFIABLE);
  assert.equal(started.semantic_hash, null);
  assert.equal(started.intent_record_sha256, null);
  assert.equal(started.completion_record_sha256, null);
  const freshColumns = db.prepare(`PRAGMA main.table_xinfo('turn_tool_executions')`).all()
    .map(({ name, type, notnull, dflt_value, pk, hidden }) => (
      [name, type, notnull, dflt_value, pk, hidden]
    ));
  const migratedDb = new Database(path.join(firstDir, 'eva.db'), { readonly: true });
  const migratedColumns = migratedDb.prepare(`PRAGMA main.table_xinfo('turn_tool_executions')`)
    .all().map(({ name, type, notnull, dflt_value, pk, hidden }) => (
      [name, type, notnull, dflt_value, pk, hidden]
    ));
  migratedDb.close();
  assert.deepEqual(migratedColumns, freshColumns);
});

test('de echte production-backup14 layout migreert verliesloos en classificeert exact', async (t) => {
  const targetDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-production14-');
  t.after(() => fs.rmSync(targetDir, { recursive: true, force: true }));
  const base = (key, status, args, output = null) => ({
    execution_key: key, turn_id: `turn:${key}`, tool: 'legacy_probe',
    args, status, started_ts: '2025-10-01T00:00:00.000Z',
    finished_ts: status === 'done' ? '2025-10-01T00:00:01.000Z' : null,
    output, semantic_hash: null, semantic_binding: null, output_sha256: null,
    material_sha256: null, intent_record_sha256: null, completion_record_sha256: null,
  });
  const bindIntent = (row) => {
    const args = row.args == null ? null : JSON.parse(row.args);
    row.semantic_hash = sha256(canonicalJson({
      turnId: row.turn_id, tool: row.tool, args, binding: null,
    }));
    row.intent_record_sha256 = intentRecordV1(row);
    return row;
  };
  const full = bindIntent(base(
    'production14:full', 'done', canonicalJson({ full: true }), '{"full":true}',
  ));
  full.output_sha256 = sha256(full.output);
  full.completion_record_sha256 = completionRecordV1(full);
  const partial = bindIntent(base(
    'production14:partial', 'done', canonicalJson({ partial: true }), '{"partial":true}',
  ));
  const redacted = bindIntent(base(
    'production14:redacted', 'done',
    canonicalJson({ token: '[AFGESCHERMD:TOKEN]' }), '{"redacted":true}',
  ));
  redacted.output_sha256 = sha256(redacted.output);
  redacted.completion_record_sha256 = completionRecordV1(redacted);
  const hashless = base(
    'production14:hashless', 'done', canonicalJson({ hashless: true }), '{"hashless":true}',
  );
  const ambiguous = base(
    'production14:ambiguous', 'done', JSON.stringify({ z: 2, a: 1 }), '{"ambiguous":true}',
  );
  const started = bindIntent(base(
    'production14:started', 'started', canonicalJson({ started: true }), null,
  ));
  const transport = bindIntent(base(
    'production14:transport', 'transport_accepted',
    canonicalJson({ accepted: true }), '{"accepted":true}',
  ));
  const legacyDb = createProductionBackup14Ledger(targetDir, [
    full, partial, redacted, hashless, ambiguous, started, transport,
  ]);
  legacyDb.close();

  const migrated = await runProcess(`
    const {db,executionLedgerStoreStatus}=await import('./src/db.js');
    await import('./src/mind/portfolio.js');
    const artifacts=await import('./src/mind/artifacts.js');
    const lifecycle=await import('./src/mind/materiallifecycle.js');
    const collections=await import('./src/mind/artifactcollections.js');
    const completion=await import('./src/mind/workcompletion.js');
    const collectionStore=collections.createArtifactCollectionStore(db);
    collectionStore.registerAuthorityVerifier(()=>null);
    const completionStore=completion.createWorkCompletionStore(db);
    const rows=db.prepare('SELECT * FROM main.turn_tool_executions ORDER BY execution_key').all();
    process.stdout.write(JSON.stringify({
      ledger:executionLedgerStoreStatus(),
      artifact:artifacts.artifactVersionStoreStatus(),
      material:lifecycle.materialLifecycleStoreStatus(),
      collection:collectionStore.status(),completion:completionStore.status(),rows,
    }));
    db.close();
  `, targetDir);
  assert.equal(migrated.ledger.available, true);
  assert.equal(migrated.ledger.layout, 'production14-migrated-v2');
  assert.equal(migrated.artifact.available, true);
  assert.equal(migrated.material.available, true);
  assert.equal(migrated.collection.ready, true);
  assert.equal(migrated.completion.ready, false);
  assert.equal(migrated.completion.reason, 'production table manifest mismatch for timeline');
  assert.doesNotMatch(migrated.completion.reason, /execution ledger/u);
  const byKey = new Map(migrated.rows.map((row) => [row.execution_key, row]));
  for (const key of [full.execution_key, started.execution_key, transport.execution_key]) {
    assert.equal(byKey.get(key).execution_verification, VERIFIED, key);
  }
  assert.equal(byKey.get(full.execution_key).intent_record_sha256, full.intent_record_sha256);
  assert.equal(byKey.get(full.execution_key).completion_record_sha256,
    full.completion_record_sha256);
  assert.equal(
    byKey.get(hashless.execution_key).execution_verification,
    LEGACY_UNVERIFIABLE,
  );
  assert.equal(byKey.get(hashless.execution_key).semantic_hash, null);
  assert.equal(byKey.get(hashless.execution_key).intent_record_sha256, null);
  assert.equal(byKey.get(hashless.execution_key).completion_record_sha256, null);
  for (const key of [partial.execution_key, redacted.execution_key,
    ambiguous.execution_key]) {
    assert.equal(byKey.get(key).execution_verification, LEGACY_UNVERIFIABLE, key);
  }
  assert.equal(byKey.get(partial.execution_key).intent_record_sha256,
    partial.intent_record_sha256);
  assert.equal(byKey.get(partial.execution_key).completion_record_sha256, null);
  assert.equal(byKey.get(redacted.execution_key).intent_record_sha256,
    redacted.intent_record_sha256);
  assert.equal(byKey.get(redacted.execution_key).completion_record_sha256,
    redacted.completion_record_sha256);
  assert.equal(byKey.get(ambiguous.execution_key).semantic_hash, null);
  assert.equal(byKey.get(ambiguous.execution_key).intent_record_sha256, null);
  assert.deepEqual(
    migrated.rows.map((row) => row.execution_seq).toSorted((left, right) => left - right),
    [1, 2, 3, 4, 5, 6, 7],
  );
});

test('v1 en v2 intenties en terminale records zijn na startup immutable', () => {
  const executionKey = 'execution:v1:immutable';
  const row = {
    execution_key: executionKey, turn_id: 'turn:v1:immutable', tool: 'legacy_probe',
    args: canonicalJson({ legacy: true }), semantic_hash: sha256('v1-immutable'),
    semantic_binding: null, status: 'done', started_ts: '2025-04-01T00:00:00.000Z',
    finished_ts: '2025-04-01T00:00:01.000Z', output: '{"legacy":true}',
    material_sha256: null, execution_contract: CONTRACT_V1,
    execution_verification: VERIFIED,
  };
  row.output_sha256 = sha256(row.output);
  row.intent_record_sha256 = intentRecordV1(row);
  row.execution_seq = db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS seq
    FROM main.turn_tool_executions`).get().seq;
  row.completion_record_sha256 = completionRecordV1(row);
  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,semantic_binding,
     intent_record_sha256,status,started_ts,finished_ts,output,output_sha256,
     material_sha256,completion_record_sha256,execution_seq,execution_contract,
     execution_verification)
    VALUES (@execution_key,@turn_id,@tool,@args,@semantic_hash,@semantic_binding,
     @intent_record_sha256,@status,@started_ts,@finished_ts,@output,@output_sha256,
     @material_sha256,@completion_record_sha256,@execution_seq,@execution_contract,
     @execution_verification)`).run(row);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET args='null'
    WHERE execution_key=?`).run(executionKey), /execution intent is immutable/u);
  assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET output='null'
    WHERE execution_key=?`).run(executionKey), /execution terminal record is immutable/u);
});

test('hashloze done replay wordt hoogstens eenmaal atomair v1-verified en nooit heruitgevoerd', () => {
  const executionKey = 'execution:legacy:hashless-done';
  const sequence = db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
    FROM main.turn_tool_executions`).get().value;
  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,execution_seq,
     execution_contract,execution_verification)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    executionKey, 'turn:legacy:hashless-done', 'legacy_probe', canonicalJson({ a: 1 }),
    'done', '2025-05-01T00:00:00.000Z', '2025-05-01T00:00:01.000Z',
    '{"effect":"already-happened"}', sequence, CONTRACT_V1, LEGACY_UNVERIFIABLE,
  );
  db.exec('SAVEPOINT forbidden_legacy_authority');
  try {
    assert.throws(() => db.prepare(`UPDATE main.turn_tool_executions SET
      semantic_hash=?,intent_record_sha256=?,output_sha256=?,completion_record_sha256=?,
      execution_verification='verified' WHERE execution_key=?`).run(
      'a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64), 'd'.repeat(64), executionKey,
    ), /execution (?:intent|terminal record) is immutable/u);
  } finally {
    db.exec('ROLLBACK TO forbidden_legacy_authority; RELEASE forbidden_legacy_authority');
  }
  const first = claimTurnToolExecution({
    executionKey, turnId: 'turn:legacy:hashless-done', tool: 'legacy_probe', args: { a: 1 },
  });
  assert.equal(first.claimed, false);
  assert.equal(first.row.status, 'done');
  assert.equal(first.row.execution_verification, VERIFIED);
  assert.equal(first.row.intent_record_sha256, intentRecordV1(first.row));
  assert.equal(first.row.completion_record_sha256, completionRecordV1(first.row));
  assert.equal(completeTurnToolExecution(executionKey, first.row.output).completed, false);
  assert.equal(claimTurnToolExecution({
    executionKey, turnId: 'turn:legacy:hashless-done', tool: 'legacy_probe', args: { a: 1 },
  }).row.completion_record_sha256, first.row.completion_record_sha256);
});

test('redactie-markers en caller-binding maken hashloze legacy expliciet onverifieerbaar', () => {
  const cases = [
    ['marker', { token: '[AFGESCHERMD:TOKEN]' }, null],
    ['binding', { safe: true }, {
      understandingSha256: 'a'.repeat(64), requirementId: 'r1',
      addressesRequirementIds: ['r1'], basisReceiptIds: [],
      capabilityId: 'legacy.probe', operationId: 'probe', role: 'executor',
    }],
  ];
  for (const [suffix, args, semanticBinding] of cases) {
    const executionKey = `execution:legacy:unverifiable:${suffix}`;
    const sequence = db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
      FROM main.turn_tool_executions`).get().value;
    db.prepare(`INSERT INTO main.turn_tool_executions
      (execution_key,turn_id,tool,args,status,started_ts,execution_seq,
       execution_contract,execution_verification)
      VALUES (?,?,?,?,?,?,?,?,?)`).run(
      executionKey, `turn:legacy:unverifiable:${suffix}`, 'legacy_probe', canonicalJson(args),
      'started', '2025-06-01T00:00:00.000Z', sequence, CONTRACT_V1, LEGACY_UNVERIFIABLE,
    );
    assert.throws(() => claimTurnToolExecution({
      executionKey, turnId: `turn:legacy:unverifiable:${suffix}`,
      tool: 'legacy_probe', args, semanticBinding,
    }), /legacy.*unverifiable|onverifieerbaar/u);
    const stored = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey);
    assert.equal(stored.execution_verification, LEGACY_UNVERIFIABLE);
    assert.equal(stored.semantic_hash, null);
    assert.equal(stored.intent_record_sha256, null);
  }

  const executionKey = 'execution:legacy:unverifiable:noncanonical';
  const sequence = db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
    FROM main.turn_tool_executions`).get().value;
  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,execution_seq,
     execution_contract,execution_verification)
    VALUES (?,?,?,?,?,?,?,?,?)`).run(
    executionKey, 'turn:legacy:unverifiable:noncanonical', 'legacy_probe',
    JSON.stringify({ z: 2, a: 1 }), 'started', '2025-06-01T00:00:00.000Z',
    sequence, CONTRACT_V1, LEGACY_UNVERIFIABLE,
  );
  assert.throws(() => claimTurnToolExecution({
    executionKey, turnId: 'turn:legacy:unverifiable:noncanonical',
    tool: 'legacy_probe', args: { z: 2, a: 1 },
  }), /legacy.*unverifiable|onverifieerbaar|andere handeling/u);
  const noncanonical = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  assert.equal(noncanonical.execution_verification, LEGACY_UNVERIFIABLE);
  assert.equal(noncanonical.semantic_hash, null);
  assert.equal(noncanonical.intent_record_sha256, null);
});

test('een narrow hashed legacy-binding wordt nooit met caller-velden verrijkt', () => {
  const executionKey = 'execution:legacy:narrow-binding';
  const binding = {
    understandingSha256: 'b'.repeat(64), requirementId: 'r1',
    capabilityId: 'legacy.probe', operationId: 'probe',
  };
  const args = { exact: true };
  const sequence = db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
    FROM main.turn_tool_executions`).get().value;
  const semanticHash = sha256(canonicalJson({
    turnId: 'turn:legacy:narrow-binding', tool: 'legacy_probe', args, binding,
  }));
  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,semantic_binding,status,started_ts,
     execution_seq,execution_contract,execution_verification)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    executionKey, 'turn:legacy:narrow-binding', 'legacy_probe', canonicalJson(args),
    semanticHash, canonicalJson(binding), 'started', '2025-07-01T00:00:00.000Z',
    sequence, CONTRACT_V1, LEGACY_UNVERIFIABLE,
  );
  const enriched = {
    ...binding, addressesRequirementIds: ['r1'], basisReceiptIds: [], role: 'executor',
  };
  assert.throws(() => claimTurnToolExecution({
    executionKey, turnId: 'turn:legacy:narrow-binding', tool: 'legacy_probe',
    args, semanticBinding: enriched,
  }), /legacy.*unverifiable|onverifieerbaar|binding/u);
  const stored = db.prepare(`SELECT semantic_binding,intent_record_sha256
    FROM main.turn_tool_executions WHERE execution_key=?`).get(executionKey);
  assert.equal(stored.semantic_binding, canonicalJson(binding));
  assert.equal(stored.intent_record_sha256, null);
});

test('ledgerstatus faalt gesloten bij TEMP shadow en ontbrekende trigger', () => {
  assert.equal(executionLedgerStoreStatus().available, true);
  db.exec('CREATE TEMP TABLE turn_tool_executions(execution_key TEXT)');
  assert.match(executionLedgerStoreStatus().reason, /TEMP|temporary/i);
  db.exec('DROP TABLE temp.turn_tool_executions');
  const trigger = db.prepare(`SELECT sql FROM main.sqlite_master WHERE type='trigger'
    AND name='turn_tool_executions_no_delete'`).get().sql;
  db.exec('DROP TRIGGER main.turn_tool_executions_no_delete');
  try {
    assert.equal(executionLedgerStoreStatus().available, false);
    assert.throws(() => claimTurnToolExecution({
      executionKey: 'execution:unavailable', turnId: 'turn:unavailable',
      tool: 'legacy_probe', args: null,
    }), /ledger.*unavailable|ledger.*onbeschikbaar/i);
  } finally { db.exec(trigger); }
  assert.equal(executionLedgerStoreStatus().available, true);

  const sequenceIndex = db.prepare(`SELECT sql FROM main.sqlite_master WHERE type='index'
    AND name='idx_turn_tool_executions_sequence'`).get().sql;
  db.exec(`DROP INDEX main.idx_turn_tool_executions_sequence;
    CREATE UNIQUE INDEX idx_turn_tool_executions_sequence
      ON turn_tool_executions(execution_seq DESC)`);
  try {
    assert.equal(executionLedgerStoreStatus().available, false);
    assert.match(executionLedgerStoreStatus().reason, /index manifest is inexact/u);
  } finally {
    db.exec(`DROP INDEX main.idx_turn_tool_executions_sequence; ${sequenceIndex}`);
  }
  assert.equal(executionLedgerStoreStatus().available, true);
});

test('counterfeit extra tableconstraint met gelijke xinfo faalt gesloten', async (t) => {
  const targetDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-counterfeit-check-');
  t.after(() => fs.rmSync(targetDir, { recursive: true, force: true }));
  const script = `
    const {db,executionLedgerStoreStatus}=await import('./src/db.js');
    const triggers=db.prepare("SELECT sql FROM main.sqlite_master WHERE type='trigger' AND tbl_name='turn_tool_executions'").all();
    db.exec('DROP TABLE main.turn_tool_executions');
    db.exec(\`CREATE TABLE main.turn_tool_executions (
      execution_key TEXT PRIMARY KEY,turn_id TEXT NOT NULL,tool TEXT NOT NULL,args TEXT,
      status TEXT NOT NULL DEFAULT 'started',started_ts TEXT NOT NULL,finished_ts TEXT,
      output TEXT,semantic_hash TEXT,semantic_binding TEXT,intent_record_sha256 TEXT,
      output_sha256 TEXT,material_sha256 TEXT,completion_record_sha256 TEXT,
      execution_seq INTEGER NOT NULL DEFAULT 0,
      execution_contract TEXT NOT NULL DEFAULT 'ExecutionRecord/v1',
      execution_verification TEXT NOT NULL DEFAULT 'legacy_unverifiable',
      CHECK(status='started'));
      CREATE INDEX idx_turn_tool_executions_turn ON turn_tool_executions(turn_id,tool);
      CREATE UNIQUE INDEX idx_turn_tool_executions_sequence ON turn_tool_executions(execution_seq);\`);
    for (const trigger of triggers) db.exec(trigger.sql);
    process.stdout.write(JSON.stringify(executionLedgerStoreStatus()));
    db.close();
  `;
  const status = await runProcess(script, targetDir);
  assert.equal(status.available, false);
  assert.match(status.reason, /column layout is unrecognized/u);
});

test('een migratiefout stopt startup en wordt niet ingeslikt', async (t) => {
  const targetDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-fault-');
  t.after(() => fs.rmSync(targetDir, { recursive: true, force: true }));
  const legacyDb = createDeployedHeadLedger(targetDir, [], {
    extraSql: 'ALTER TABLE turn_tool_executions ADD COLUMN Semantic_Hash TEXT;',
  });
  legacyDb.prepare(`INSERT INTO turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts)
    VALUES (?,?,?,?,?,?)`).run(
    'fault:row', 'turn:fault', 'legacy_probe', 'null', 'started',
    '2025-08-01T00:00:00.000Z',
  );
  legacyDb.close();
  await assert.rejects(
    runRawProcess(`await import('./src/db.js')`, targetDir),
    /duplicate column|execution ledger migration/u,
  );
});

test('een onbekende live-trigger wordt nooit stil verwijderd door de migratie', async (t) => {
  const targetDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-external-trigger-');
  t.after(() => fs.rmSync(targetDir, { recursive: true, force: true }));
  const legacyDb = createDeployedHeadLedger(targetDir, [], {
    extraSql: `CREATE TRIGGER preserve_external_execution_audit
      AFTER INSERT ON turn_tool_executions BEGIN SELECT 1; END;`,
  });
  legacyDb.close();
  await assert.rejects(
    runRawProcess(`await import('./src/db.js')`, targetDir),
    /execution ledger migration|trigger/u,
  );
  const preserved = new Database(path.join(targetDir, 'eva.db'), { readonly: true });
  assert.deepEqual(
    preserved.prepare(`PRAGMA main.table_info('turn_tool_executions')`).all()
      .map((column) => column.name),
    ['execution_key', 'turn_id', 'tool', 'args', 'status', 'started_ts',
      'finished_ts', 'output'],
  );
  assert.equal(preserved.prepare(`SELECT count(*) AS count FROM main.sqlite_master
    WHERE type='trigger' AND name='preserve_external_execution_audit'`).get().count, 1);
  preserved.close();
});

test('exact genoemde maar counterfeit live-trigger stopt startup vóór migratie', async (t) => {
  const targetDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-counterfeit-trigger-');
  t.after(() => fs.rmSync(targetDir, { recursive: true, force: true }));
  const legacyDb = new Database(path.join(targetDir, 'eva.db'));
  const triggers = Object.entries(EXECUTION_LEDGER_TRIGGER_SQL).map(([name, sql]) => (
    name === 'turn_tool_executions_valid_sequence'
      ? sql.replace(
        "WHEN typeof(NEW.execution_seq)<>'integer' OR NEW.execution_seq<=0",
        'WHEN 0',
      )
      : sql
  ));
  legacyDb.exec(`${EXECUTION_LEDGER_TABLE_SQL};
    ${Object.values(EXECUTION_LEDGER_INDEX_SQL).join(';\n')};
    ${triggers.join(';\n')};`);
  legacyDb.close();
  await assert.rejects(
    runRawProcess(`await import('./src/db.js')`, targetDir),
    /execution ledger migration|trigger/u,
  );
  const preserved = new Database(path.join(targetDir, 'eva.db'), { readonly: true });
  assert.match(preserved.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='turn_tool_executions_valid_sequence'`).get().sql,
  /WHEN 0/u);
  preserved.close();
});

test('een pre-v2 database houdt bij herstart contract, hashes en refs byte-exact op v1', async (t) => {
  const legacyDataDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-legacy-');
  t.after(() => fs.rmSync(legacyDataDir, { recursive: true, force: true }));
  const before = {
    execution_key: 'execution:pre-v2:stable',
    turn_id: 'turn:pre-v2:stable',
    tool: 'legacy_probe',
    args: canonicalJson({ stable: 'bytes' }),
    semantic_hash: sha256('pre-v2-semantic'),
    semantic_binding: null,
    status: 'done',
    started_ts: '2025-02-03T04:05:06.000Z',
    finished_ts: '2025-02-03T04:05:07.000Z',
    output: '{"stable":true}',
    material_sha256: null,
  };
  before.output_sha256 = sha256(before.output);
  before.intent_record_sha256 = intentRecordV1(before);
  before.completion_record_sha256 = completionRecordV1(before);
  const legacyDb = createProductionBackup14Ledger(legacyDataDir, [before]);
  legacyDb.close();

  const script = `
    const {db,validateExecutionRecord}=await import('./src/db.js');
    const row=db.prepare("SELECT * FROM main.turn_tool_executions WHERE execution_key='execution:pre-v2:stable'").get();
    const verified=validateExecutionRecord(row);
    process.stdout.write(JSON.stringify({
      contract:verified.execution_contract,executionKey:verified.execution_key,
      turnId:verified.turn_id,sequence:verified.execution_seq,
      intent:verified.intent_record_sha256,completion:verified.completion_record_sha256
    }));
    db.close();
  `;
  const migrated = await runProcess(script, legacyDataDir);
  assert.deepEqual(migrated, {
    contract: CONTRACT_V1,
    executionKey: before.execution_key,
    turnId: before.turn_id,
    sequence: 1,
    intent: before.intent_record_sha256,
    completion: before.completion_record_sha256,
  });
});

test('een fully-hashed bestaande v2 receipt wordt exact geclassificeerd zonder rehash', async (t) => {
  const targetDir = fs.mkdtempSync('/tmp/bottie-execution-ledger-existing-v2-');
  t.after(() => fs.rmSync(targetDir, { recursive: true, force: true }));
  const legacyDb = new Database(path.join(targetDir, 'eva.db'));
  legacyDb.exec(`CREATE TABLE turn_tool_executions (
    execution_key TEXT PRIMARY KEY,turn_id TEXT NOT NULL,tool TEXT NOT NULL,args TEXT,
    status TEXT NOT NULL DEFAULT 'started',started_ts TEXT NOT NULL,finished_ts TEXT,
    output TEXT,semantic_hash TEXT,semantic_binding TEXT,intent_record_sha256 TEXT,
    output_sha256 TEXT,material_sha256 TEXT,completion_record_sha256 TEXT,
    execution_seq INTEGER NOT NULL DEFAULT 0,
    execution_contract TEXT NOT NULL DEFAULT 'ExecutionRecord/v1'
  ); CREATE INDEX idx_turn_tool_executions_turn ON turn_tool_executions(turn_id,tool);
  CREATE UNIQUE INDEX idx_turn_tool_executions_sequence
    ON turn_tool_executions(execution_seq);`);
  const before = {
    execution_key: 'execution:existing:v2', turn_id: 'turn:existing:v2',
    tool: 'legacy_probe', args: canonicalJson({ stable: true }),
    semantic_hash: sha256(canonicalJson({
      turnId: 'turn:existing:v2', tool: 'legacy_probe', args: { stable: true }, binding: null,
    })),
    semantic_binding: null, status: 'done', started_ts: '2025-09-01T00:00:00.000Z',
    finished_ts: '2025-09-01T00:00:01.000Z', output: '{"stable":true}',
    material_sha256: null, execution_seq: 19, execution_contract: CONTRACT_V2,
  };
  before.output_sha256 = sha256(before.output);
  before.intent_record_sha256 = intentRecordV2(before);
  before.completion_record_sha256 = completionRecordV2(before);
  legacyDb.prepare(`INSERT INTO turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,
     semantic_hash,semantic_binding,intent_record_sha256,output_sha256,
     material_sha256,completion_record_sha256,execution_seq,execution_contract)
    VALUES (@execution_key,@turn_id,@tool,@args,@status,@started_ts,@finished_ts,@output,
     @semantic_hash,@semantic_binding,@intent_record_sha256,@output_sha256,
     @material_sha256,@completion_record_sha256,@execution_seq,@execution_contract)`).run(before);
  legacyDb.close();
  const migrated = await runProcess(`
    const {db,validateExecutionRecord}=await import('./src/db.js');
    const row=db.prepare("SELECT * FROM main.turn_tool_executions WHERE execution_key='execution:existing:v2'").get();
    validateExecutionRecord(row);
    process.stdout.write(JSON.stringify(row));
    db.close();
  `, targetDir);
  assert.equal(migrated.execution_verification, VERIFIED);
  assert.equal(migrated.intent_record_sha256, before.intent_record_sha256);
  assert.equal(migrated.completion_record_sha256, before.completion_record_sha256);
  assert.equal(migrated.execution_seq, before.execution_seq);
});

test('een hashloze legacyregel krijgt uitsluitend een v1-upgrade', () => {
  const executionKey = 'execution:legacy:upgrade-v1';
  const sequence = db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
    FROM main.turn_tool_executions`).get().value;
  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,status,started_ts,execution_seq,execution_contract)
    VALUES (?,?,?,?,?,?,?,?)`).run(
    executionKey,
    'turn:legacy:upgrade-v1',
    'legacy_probe',
    canonicalJson({ z: 2, a: 1 }),
    'started',
    '2025-03-04T05:06:07.000Z',
    sequence,
    CONTRACT_V1,
  );
  const replay = claimTurnToolExecution({
    executionKey,
    turnId: 'turn:legacy:upgrade-v1',
    tool: 'legacy_probe',
    args: { a: 1, z: 2 },
  });
  assert.equal(replay.claimed, false);
  assert.equal(replay.row.execution_contract, CONTRACT_V1);
  assert.equal(replay.row.intent_record_sha256, intentRecordV1(replay.row));
  assert.notEqual(replay.row.intent_record_sha256, intentRecordV2({
    ...replay.row, execution_contract: CONTRACT_V2,
  }));
});

function runProcess(script, targetDataDir = dataDir) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', script], {
      cwd: path.resolve('.'),
      env: { ...process.env, EVA_DATA_DIR: targetDataDir },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) reject(new Error(stderr || stdout || `ledger child exit ${code}`));
      else resolve(JSON.parse(stdout));
    });
  });
}

function runRawProcess(script, targetDataDir = dataDir) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', script], {
      cwd: path.resolve('.'),
      env: { ...process.env, EVA_DATA_DIR: targetDataDir },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(stderr || stdout || `ledger child exit ${code}`));
    });
  });
}

function runClaimProcess(index) {
  const script = `
    const {claimTurnToolExecution,db}=await import('./src/db.js');
    const claimed=claimTurnToolExecution({
      executionKey:'execution:v2:concurrent:${index}',
      turnId:'turn:v2:concurrent',tool:'bewijs_tool',args:{index:${index}}
    });
    process.stdout.write(JSON.stringify({
      sequence:claimed.row.execution_seq,contract:claimed.row.execution_contract
    }));
    db.close();
  `;
  return runProcess(script);
}

test('concurrerende claims krijgen atomair unieke positieve v2-sequences', async () => {
  const claims = await Promise.all(Array.from({ length: 8 }, (_, index) => runClaimProcess(index)));
  const sequences = claims.map((claim) => claim.sequence);
  assert.equal(new Set(sequences).size, claims.length);
  assert.equal(sequences.every((sequence) => Number.isSafeInteger(sequence) && sequence > 0), true);
  const ordered = sequences.toSorted((left, right) => left - right);
  assert.deepEqual(
    ordered,
    Array.from({ length: claims.length }, (_, index) => ordered[0] + index),
  );
  assert.deepEqual(new Set(claims.map((claim) => claim.contract)), new Set([CONTRACT_V2]));

  const rows = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE turn_id='turn:v2:concurrent' ORDER BY execution_seq`).all();
  assert.equal(rows.length, claims.length);
  for (const row of rows) {
    assert.equal(validateExecutionRecord(row).intent_record_sha256, intentRecordV2(row));
  }
});
