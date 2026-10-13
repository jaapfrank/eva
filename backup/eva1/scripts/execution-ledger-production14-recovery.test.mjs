import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const recoveryProgram = path.join(root, 'scripts', 'execution-ledger-production14-recovery.mjs');
const oldColumnNames = [
  'execution_key', 'turn_id', 'tool', 'args', 'status', 'started_ts',
  'finished_ts', 'output', 'semantic_hash', 'semantic_binding', 'output_sha256',
  'material_sha256', 'intent_record_sha256', 'completion_record_sha256',
];
const production14TableSql = `CREATE TABLE turn_tool_executions (
  execution_key TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  output TEXT
, semantic_hash TEXT, semantic_binding TEXT, output_sha256 TEXT, material_sha256 TEXT, intent_record_sha256 TEXT, completion_record_sha256 TEXT)`;
const fixtureLegacyKeys = Object.freeze([
  'fixture:legacy:noncanonical', 'fixture:legacy:canonical',
  'fixture:legacy:redacted', 'fixture:legacy:started',
]);

function createProduction14Fixture(filename) {
  const database = new Database(filename, { fileMustExist: false });
  try {
    database.pragma('journal_mode = DELETE');
    database.pragma('page_size = 4096');
    database.pragma('auto_vacuum = NONE');
    database.pragma('user_version = 14');
    database.pragma('application_id = 1163280689');
    database.exec(`${production14TableSql};
      CREATE INDEX idx_turn_tool_executions_turn
        ON turn_tool_executions(turn_id, tool);
      CREATE TABLE recovery_fixture_meta (
        fixture_id INTEGER PRIMARY KEY,
        label TEXT NOT NULL,
        payload BLOB,
        nullable_value TEXT
      );
      CREATE UNIQUE INDEX recovery_fixture_meta_label
        ON recovery_fixture_meta(label);
      CREATE TABLE recovery_fixture_keyed (
        namespace TEXT NOT NULL,
        item_key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY(namespace,item_key)
      ) WITHOUT ROWID;
      CREATE TABLE "__proto__" (value TEXT);
      CREATE TABLE recovery_fixture_visible_rowid (
        rowid TEXT,
        payload TEXT
      );
      CREATE TABLE recovery_fixture_misleading (
        value TEXT DEFAULT 'WITHOUT ROWID'
      );
      CREATE VIEW recovery_fixture_view AS
        SELECT fixture_id,label,length(payload) AS payload_size
        FROM recovery_fixture_meta;
      INSERT INTO recovery_fixture_meta(fixture_id,label,payload,nullable_value)
        VALUES (3,'three',x'000102ff',NULL),(9,'nine',x'', 'kept');
      INSERT INTO recovery_fixture_keyed(namespace,item_key,value)
        VALUES ('fixture','alpha','one'),('fixture','beta',NULL);
      INSERT INTO "__proto__"(value) VALUES ('prototype-safe');
      INSERT INTO recovery_fixture_visible_rowid(rowid,payload)
        VALUES ('user-rowid','intrinsic-alias-required');
      INSERT INTO recovery_fixture_misleading(value) VALUES ('ordinary-rowid-table');
      CREATE TRIGGER recovery_fixture_meta_no_delete
        BEFORE DELETE ON recovery_fixture_meta
        BEGIN SELECT RAISE(ABORT,'fixture rows are immutable'); END;`);
    const insert = database.prepare(`INSERT INTO turn_tool_executions (
      execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,
      semantic_hash,semantic_binding,output_sha256,material_sha256,
      intent_record_sha256,completion_record_sha256
    ) VALUES (
      @executionKey,@turnId,@tool,@args,@status,@startedTs,@finishedTs,@output,
      NULL,NULL,NULL,NULL,NULL,NULL
    )`);
    const rows = [
      {
        executionKey: fixtureLegacyKeys[0], turnId: 'fixture:turn:1', tool: 'fixture_tool',
        args: '{"z":1, "a":2}', status: 'done',
        startedTs: '2026-08-01T00:00:00.000Z', finishedTs: '2026-08-01T00:00:01.000Z',
        output: '{"legacy":1}',
      },
      {
        executionKey: fixtureLegacyKeys[1], turnId: 'fixture:turn:2', tool: 'fixture_tool',
        args: '{"legacy":"canonical"}', status: 'done',
        startedTs: '2026-08-02T00:00:00.000Z', finishedTs: '2026-08-02T00:00:01.000Z',
        output: 'legacy plain output',
      },
      {
        executionKey: fixtureLegacyKeys[2], turnId: 'fixture:turn:3', tool: 'fixture_tool',
        args: '{"secret":"[AFGESCHERMD:api_key]"}', status: 'done',
        startedTs: '2026-08-03T00:00:00.000Z', finishedTs: '2026-08-03T00:00:01.000Z',
        output: '{"redacted":true}',
      },
      {
        executionKey: fixtureLegacyKeys[3], turnId: 'fixture:turn:4', tool: 'fixture_tool',
        args: '{"pending":true}', status: 'started',
        startedTs: '2026-08-04T00:00:00.000Z', finishedTs: null, output: null,
      },
    ];
    database.transaction(() => rows.forEach((row) => insert.run(row)))();
    assert.equal(database.pragma('integrity_check', { simple: true }), 'ok');
  } finally {
    database.close();
  }
  fs.chmodSync(filename, 0o600);
  return filename;
}

const sanitizedFixtureDirectory = fs.mkdtempSync('/tmp/bottie-ledger-sanitized-fixture-');
fs.chmodSync(sanitizedFixtureDirectory, 0o700);
const immutableBackup = createProduction14Fixture(
  path.join(sanitizedFixtureDirectory, 'production14.db'),
);
process.once('exit', () => {
  fs.rmSync(sanitizedFixtureDirectory, { recursive: true, force: true });
});
const addedVerifiedKeys = Object.freeze([
  'recovery:test:added:started', 'recovery:test:added:done',
  'recovery:test:added:transport',
]);
const incidentRowCount = fixtureLegacyKeys.length + addedVerifiedKeys.length;

function sha256File(filename) {
  return createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function publicationHelperDirectories() {
  return fs.readdirSync('/tmp').filter((name) => name.startsWith('eva-ledger-publisher-')).sort();
}

function canonicalJson(value) {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item) ?? 'null').join(',')}]`;
  }
  return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

function typed(value) {
  if (value === null) return ['null'];
  if (Buffer.isBuffer(value)) return ['blob', value.toString('hex')];
  if (typeof value === 'number') return [Number.isInteger(value) ? 'integer' : 'real', String(value)];
  return [typeof value, String(value)];
}

function ledgerRowsManifest(filename) {
  const database = new Database(filename, { readonly: true, fileMustExist: true });
  try {
    const projection = oldColumnNames.flatMap((name, index) => [
      `typeof("${name}") AS "value_type_${index}"`,
      `hex(CAST("${name}" AS BLOB)) AS "value_bytes_${index}"`,
    ]).join(',');
    const rows = database.prepare(`SELECT ${projection}
      FROM main.turn_tool_executions`).all();
    const hashes = rows.map((row) => createHash('sha256').update(
      canonicalJson(oldColumnNames.map((_name, index) => [
        String(row[`value_type_${index}`]), String(row[`value_bytes_${index}`]),
      ])),
    ).digest('hex')).sort();
    return {
      count: rows.length, sha256: sha256(canonicalJson(hashes)),
    };
  } finally {
    database.close();
  }
}

function ledgerRowsByKey(filename) {
  const database = new Database(filename, { readonly: true, fileMustExist: true });
  try {
    return new Map(database.prepare(`SELECT ${oldColumnNames.join(',')}
      FROM main.turn_tool_executions ORDER BY execution_key`).all().map((row) => [
      row.execution_key, oldColumnNames.map((name) => typed(row[name])),
    ]));
  } finally {
    database.close();
  }
}

function runRecovery(source, target, { env = {} } = {}) {
  return spawnSync(process.execPath, [recoveryProgram, '--source', source, '--target', target], {
    cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024,
    env: { ...process.env, ...env },
  });
}

function waitForPath(filename, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (fs.existsSync(filename)) resolve();
      else if (Date.now() >= deadline) reject(new Error(`timeout waiting for ${filename}`));
      else setTimeout(poll, 5);
    };
    poll();
  });
}

function waitForChild(child) {
  return new Promise((resolve, reject) => {
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (status) => resolve({ status, stdout, stderr }));
  });
}

function migrateWithCurrentCode(filename, targetDirectory) {
  const snapshot = path.join(targetDirectory, 'incident-snapshot.db');
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const {
      claimTurnToolExecution, completeTurnToolExecution, db, executionLedgerStoreStatus,
      transitionTurnToolExecution,
    } = await import('./src/db.js');
    const {createHash}=await import('node:crypto');
    const {canonicalJson}=await import('./src/util.js');
    const hash=(value)=>createHash('sha256').update(value).digest('hex');
    claimTurnToolExecution({
      executionKey:'recovery:test:added:started',turnId:'recovery:test',
      tool:'recovery_probe',args:{state:'started'},
    });
    claimTurnToolExecution({
      executionKey:'recovery:test:added:done',turnId:'recovery:test',
      tool:'recovery_probe',args:{state:'done'},
    });
    completeTurnToolExecution('recovery:test:added:done','{"proof":true}');
    const transportKey='recovery:test:added:transport';
    const payloadSha256=hash('fixture telegram payload');
    const transportIntent={
      contract:'TelegramTransportIntent/v2',kind:'text',chatId:'123456',
      deliveryKey:'fixture:delivery',chunkKey:'fixture:delivery:chunk:1',
      directTurnSignalRef:null,originPrincipal:'fixture',payloadSha256,
      wholeReplySha256:payloadSha256,
      segment:{splitVersion:'split-3800/v1',index:0,count:1},sendOptions:{},
    };
    claimTurnToolExecution({
      executionKey:transportKey,turnId:'recovery:test',
      tool:'@transport/telegram.sendMessage',args:transportIntent,
      initialStatus:'transport_sending',
    });
    const receiptCore={
      contract:'TelegramAcceptedReceipt/v3',accepted:true,operationKey:transportKey,
      intentSha256:hash(canonicalJson(transportIntent)),attempt:1,
      response:{chatId:'123456',messageId:'42',telegramDate:1780000000,
        messageThreadId:null,replyToMessageId:null,textSha256:payloadSha256,
        captionSha256:null,mediaFileId:null},
    };
    transitionTurnToolExecution(transportKey,{
      from:'transport_sending',status:'transport_accepted',
      output:{...receiptCore,receiptSha256:hash(canonicalJson(receiptCore))},finished:true,
    });
    const status=executionLedgerStoreStatus();
    await db.backup(${JSON.stringify(snapshot)});
    const {default:Database}=await import('better-sqlite3');
    const snapshotDatabase=new Database(${JSON.stringify(snapshot)},{fileMustExist:true});
    snapshotDatabase.pragma('journal_mode = DELETE');
    snapshotDatabase.close();
    process.stdout.write(JSON.stringify({status,snapshot:${JSON.stringify(snapshot)}}));
    db.close();
  `], {
    cwd: root,
    env: {
      ...process.env, EVA_DATA_DIR: targetDirectory,
      NODE_TEST_CONTEXT: 'child-v8', TMPDIR: '/tmp',
    },
    encoding: 'utf8', maxBuffer: 8 * 1024 * 1024,
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.equal(path.join(targetDirectory, 'eva.db'), filename);
  const result = JSON.parse(child.stdout);
  assert.equal(result.status.layout, 'production14-migrated-v2');
  assert.equal(result.snapshot, snapshot);
  fs.chmodSync(snapshot, 0o600);
  return snapshot;
}

function rewriteVerifiedIntent(database, executionKey, changes) {
  const triggers = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name='turn_tool_executions' ORDER BY name`).all();
  for (const { name } of triggers) database.exec(`DROP TRIGGER main.${name}`);
  const assignments = Object.keys(changes).map((field) => `${field}=@${field}`).join(',');
  database.prepare(`UPDATE main.turn_tool_executions SET ${assignments}
    WHERE execution_key=@executionKey`).run({ executionKey, ...changes });
  const row = database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  const legacyIntent = {
    turnId: row.turn_id, tool: row.tool, args: JSON.parse(row.args),
    binding: row.semantic_binding == null ? null : JSON.parse(row.semantic_binding),
    semanticHash: row.semantic_hash,
  };
  const intent = row.execution_contract === 'ExecutionRecord/v1' ? legacyIntent : {
    executionContract: row.execution_contract,
    executionSeq: Number(row.execution_seq),
    ...legacyIntent,
  };
  database.prepare(`UPDATE main.turn_tool_executions SET intent_record_sha256=?
    WHERE execution_key=?`).run(sha256(canonicalJson(intent)), executionKey);
  for (const { sql } of triggers) database.exec(sql);
}

function mutateExecutionRow(database, executionKey, changes) {
  const triggers = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name='turn_tool_executions' ORDER BY name`).all();
  for (const { name } of triggers) database.exec(`DROP TRIGGER main.${name}`);
  const assignments = Object.keys(changes).map((field) => `${field}=@${field}`).join(',');
  database.prepare(`UPDATE main.turn_tool_executions SET ${assignments}
    WHERE execution_key=@executionKey`).run({ executionKey, ...changes });
  for (const { sql } of triggers) database.exec(sql);
}

test('recovery keert een sanitized production14→v2-incident exact terug', (t) => {
  assert.equal(fs.existsSync(immutableBackup), true, 'sanitized production14 fixture ontbreekt');
  const recoverySource = fs.readFileSync(recoveryProgram, 'utf8');
  assert.doesNotMatch(recoverySource, /(?:from|import\()\s*['"]\.\.\/src\//u);
  assert.equal([...recoverySource.matchAll(/new Database\(/gu)].length, 2);
  assert.match(recoverySource, /new Database\(sourceBytes\)/u);
  assert.match(recoverySource, /new Database\(bytes, \{ readonly: true \}\)/u);
  assert.doesNotMatch(
    recoverySource,
    /new Database\((?:target|candidate|publication|descriptorPath|sourceArgument)/u,
  );
  assert.doesNotMatch(recoverySource, /-fuse-ld/u);
  assert.match(recoverySource,
    /runPinnedCompiler\(toolchain\.linker,[\s\S]*publication helper direct pinned link/u);
  assert.match(recoverySource,
    /'dev', 'ino', 'uid', 'gid', 'mode', 'nlink', 'size', 'mtimeNs', 'ctimeNs'/u);
  assert.match(recoverySource,
    /same_stable_file[\s\S]*st_mtim\.tv_nsec[\s\S]*st_ctim\.tv_nsec/u);
  assert.match(recoverySource, /pread\(fd,buf,want,\(off_t\)position\)/u);
  assert.match(recoverySource, /pread\(fd,&trailing,1,\(off_t\)expected\)/u);
  assert.doesNotMatch(recoverySource, /lseek\(tmpfd/u);
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-recovery-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDatabase = path.join(directory, 'eva.db');
  const targetDirectory = path.join(directory, 'target');
  fs.mkdirSync(targetDirectory, { mode: 0o700 });
  const recovered = path.join(targetDirectory, 'recovered.db');
  const immutableHashBefore = sha256File(immutableBackup);
  const oldRows = ledgerRowsManifest(immutableBackup);
  const originalRows = ledgerRowsByKey(immutableBackup);
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const migrated = migrateWithCurrentCode(runtimeDatabase, directory);
  const migratedHashBefore = sha256File(migrated);
  const migratedRows = ledgerRowsManifest(migrated);
  assert.equal(migratedRows.count, oldRows.count + addedVerifiedKeys.length);

  const helperDirectoriesBefore = publicationHelperDirectories();
  const child = runRecovery(migrated, recovered);
  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.deepEqual(publicationHelperDirectories(), helperDirectoriesBefore);
  const report = JSON.parse(child.stdout);
  assert.equal(report.contract, 'ExecutionLedgerProduction14RecoveryReport/v1');
  assert.equal(report.limits.maximumSourceBytes, String(512 * 1024 * 1024));
  assert.equal(report.limits.maximumLedgerRows, '100000');
  assert.equal(report.limits.maximumNonLedgerRows, '1000000');
  assert.equal(report.limits.maximumTables, '512');
  assert.equal(report.limits.maximumSchemaObjects, '4096');
  assert.equal(report.source.fileSha256Before, migratedHashBefore);
  assert.equal(report.source.fileSha256After, migratedHashBefore);
  assert.deepEqual(report.source.oldRows, migratedRows);
  assert.deepEqual(report.target.oldRows, migratedRows);
  assert.equal(report.source.classification.noncanonicalLegacyArgs > 0, true);
  assert.deepEqual(report.source.nonLedger, report.target.nonLedger);
  assert.equal(report.source.checks.integrityCheck, 'ok');
  assert.equal(report.target.checks.integrityCheck, 'ok');
  assert.equal(report.source.requestedPath, migrated);
  assert.equal(report.source.currentDescriptorPath, migrated);
  assert.equal(Object.hasOwn(report.source, 'sidecars'), false);
  assert.deepEqual(report.source.sidecarAuthority.checkedSuffixes, ['-wal', '-shm', '-journal']);
  assert.equal(report.source.sidecarAuthority.scope,
    'pinned-original-parent-and-basename');
  assert.equal(report.source.sidecarAuthority.absentAtFinalCheck, true);
  assert.equal(report.source.sidecarAuthority.namedSourceState, 'same-pinned-inode');
  assert.equal(Array.isArray(report.source.nonLedger.tables), true);
  const nonLedgerTables = new Map(report.source.nonLedger.tables.map((entry) => [entry.name, entry]));
  assert.equal(nonLedgerTables.get('__proto__').rows.count, 1);
  assert.deepEqual(nonLedgerTables.get('recovery_fixture_keyed').rowIdentity,
    { kind: 'without-rowid', alias: null });
  assert.deepEqual(nonLedgerTables.get('recovery_fixture_visible_rowid').rowIdentity,
    { kind: 'intrinsic-rowid', alias: '_rowid_' });
  assert.deepEqual(nonLedgerTables.get('recovery_fixture_misleading').rowIdentity,
    { kind: 'intrinsic-rowid', alias: 'rowid' });
  assert.equal(report.target.requestedPath, recovered);
  assert.equal(report.target.currentPath, recovered);
  assert.equal(Object.hasOwn(report.source, 'path'), false);
  assert.equal(Object.hasOwn(report.target, 'path'), false);
  assert.match(report.source.pinned.dev, /^\d+$/u);
  assert.match(report.source.pinned.ino, /^\d+$/u);
  assert.match(report.target.pinnedParent.dev, /^\d+$/u);
  assert.match(report.target.pinnedFile.ino, /^\d+$/u);
  assert.equal(report.target.layout, 'production14');
  assert.equal(report.source.pinned.uid, String(process.geteuid()));
  assert.equal(report.source.pinned.mode, '0600');
  assert.deepEqual(report.source.metadataBefore, report.source.metadataAfter);
  assert.deepEqual(
    {
      encoding: report.source.persistentPragmas.encoding,
      pageSize: report.source.persistentPragmas.pageSize,
      autoVacuum: report.source.persistentPragmas.autoVacuum,
      userVersion: report.source.persistentPragmas.userVersion,
      applicationId: report.source.persistentPragmas.applicationId,
    },
    {
      encoding: report.target.persistentPragmas.encoding,
      pageSize: report.target.persistentPragmas.pageSize,
      autoVacuum: report.target.persistentPragmas.autoVacuum,
      userVersion: report.target.persistentPragmas.userVersion,
      applicationId: report.target.persistentPragmas.applicationId,
    },
  );
  assert.equal(report.target.persistentPragmas.journalMode, 'delete');
  assert.equal(report.changes.schemaVersion.intentional, true);
  assert.notEqual(report.changes.schemaVersion.before, report.changes.schemaVersion.after);
  assert.equal(report.changes.journalMode.after, 'delete');
  assert.equal(report.target.immutableVerification.contract,
    'PinnedImmutableDatabaseVerification/v1');
  assert.equal(report.target.immutableVerification.storage, 'anonymous-main-and-temp-memory');
  assert.equal(report.target.immutableVerification.tempStore, 'memory');
  assert.equal(report.target.immutableVerification.fileSha256, report.target.fileSha256);
  assert.equal(report.target.immutableVerification.journalHeader, 'rollback');
  assert.equal(report.target.immutableVerification.byteLength, report.target.pinnedFile.size);
  assert.equal(report.target.immutableVerification.manifest.checks.integrityCheck, 'ok');
  assert.equal(report.target.immutableVerification.manifest.persistentPragmas.journalMode, 'delete');
  assert.equal(sha256File(migrated), migratedHashBefore);
  assert.equal(sha256File(immutableBackup), immutableHashBefore);
  assert.equal(fs.statSync(recovered).mode & 0o777, 0o600);
  assert.equal(String(fs.statSync(recovered).dev), report.target.pinnedFile.dev);
  assert.equal(String(fs.statSync(recovered).ino), report.target.pinnedFile.ino);
  assert.equal(fs.statSync(recovered).nlink, 1);
  assert.equal(report.anonymousCandidate.storage, 'anonymous-in-memory-buffer');
  assert.equal(report.anonymousCandidate.fileSha256, report.target.fileSha256);
  assert.equal(report.target.publication.mechanism, 'O_TMPFILE+linkat(AT_EMPTY_PATH)');
  assert.equal(report.target.publication.finalNlink, '1');
  assert.match(report.target.publication.helperSourceSha256, /^[a-f0-9]{64}$/u);
  assert.match(report.target.publication.helperBinarySha256, /^[a-f0-9]{64}$/u);
  assert.equal(report.target.publication.helperBinaryFormat.linkage, 'static');
  assert.equal(report.target.publication.helperBinaryFormat.machine, 'x86-64');
  assert.equal(report.target.publication.helperPinnedSource.uid, String(process.geteuid()));
  assert.equal(report.target.publication.helperPinnedSource.mode, '0400');
  assert.equal(report.target.publication.helperUnlinkedSource.nlink, '0');
  assert.equal(report.target.publication.helperObjectEvidence.storage, 'O_TMPFILE-anonymous');
  assert.equal(report.target.publication.helperObjectEvidence.pinned.nlink, '0');
  assert.equal(report.target.publication.helperObjectEvidence.afterLink.nlink, '0');
  assert.equal(report.target.publication.helperObjectEvidence.fileSha256.length, 64);
  assert.equal(report.target.publication.helperUnlinkedFile.nlink, '0');
  assert.equal(report.target.publication.helperUnlinkedDirectory.nlink, '0');
  assert.equal(report.target.publication.buildArtifactsRemoved, true);
  for (const authority of Object.values(report.target.publication.toolchain)) {
    assert.equal(path.isAbsolute(authority.canonicalPath), true);
    assert.equal(authority.pinned.uid, '0');
    assert.match(authority.fileSha256, /^[a-f0-9]{64}$/u);
  }
  assert.match(report.target.publication.toolchain.compiler.targetTriple,
    /^[a-z0-9_+.]+(?:-[a-z0-9_+.]+)+$/u);
  assert.match(report.target.publication.toolchain.compiler.fullVersion,
    /^[0-9]+(?:\.[0-9]+){0,3}$/u);
  assert.equal(report.target.publication.toolchain.compiler.abiMajor,
    report.target.publication.toolchain.compiler.fullVersion.split('.')[0]);
  assert.equal(report.target.publication.toolchain.compiler.executedEvidence.method,
    'pinned-descriptor-exec');
  assert.equal(report.target.publication.toolchain.linker.executedEvidence.method,
    'pinned-descriptor-exec');
  assert.equal(report.target.publication.toolchain.linker.executedEvidence.fileSha256,
    report.target.publication.toolchain.linker.fileSha256);
  assert.equal(report.target.capacity.policy, 'three-times-source-plus-64MiB');
  assert.equal(
    BigInt(report.target.capacity.requiredBytes),
    3n * BigInt(report.anonymousCandidate.byteLength) + 64n * 1024n * 1024n,
  );
  assert.equal(
    BigInt(report.target.capacity.availableBytes) >= BigInt(report.target.capacity.requiredBytes),
    true,
  );
  for (const suffix of ['-wal', '-shm', '-journal']) {
    assert.equal(fs.existsSync(`${recovered}${suffix}`), false);
  }

  const recoveredRows = ledgerRowsByKey(recovered);
  const migratedRowsByKey = ledgerRowsByKey(migrated);
  for (const [executionKey, original] of originalRows) {
    assert.deepEqual(recoveredRows.get(executionKey), original, executionKey);
  }
  for (const executionKey of addedVerifiedKeys) {
    assert.deepEqual(recoveredRows.get(executionKey), migratedRowsByKey.get(executionKey));
  }

  const recoveredDatabase = new Database(recovered, { readonly: true, fileMustExist: true });
  try {
    assert.equal(recoveredDatabase.pragma('integrity_check', { simple: true }), 'ok');
    assert.equal(recoveredDatabase.pragma('journal_mode', { simple: true }), 'delete');
    assert.deepEqual(
      recoveredDatabase.prepare("PRAGMA main.table_info('turn_tool_executions')")
        .all().map((column) => column.name),
      oldColumnNames,
    );
    assert.deepEqual(
      recoveredDatabase.prepare(`SELECT type,name FROM main.sqlite_master
        WHERE name='turn_tool_executions' OR tbl_name='turn_tool_executions'
        ORDER BY type,name`).all(),
      [
        { type: 'index', name: 'idx_turn_tool_executions_turn' },
        { type: 'index', name: 'sqlite_autoindex_turn_tool_executions_1' },
        { type: 'table', name: 'turn_tool_executions' },
      ],
    );
  } finally {
    recoveredDatabase.close();
  }
});

test('recovery weigert een onverwachte ledger en overschrijft nooit een doel', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-refusal-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const production14 = path.join(directory, 'production14.db');
  const refusalTargetDirectory = path.join(directory, 'refusal-target');
  fs.mkdirSync(refusalTargetDirectory, { mode: 0o700 });
  const target = path.join(refusalTargetDirectory, 'target.db');
  fs.copyFileSync(immutableBackup, production14, fs.constants.COPYFILE_EXCL);
  let child = runRecovery(production14, target);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /source ledger layout is not exact production14-migrated-v2|column layout is unrecognized/u);
  assert.equal(fs.existsSync(target), false);

  const migratedDirectory = fs.mkdtempSync(path.join(directory, 'migrated-'));
  const runtimeDatabase = path.join(migratedDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const migrated = migrateWithCurrentCode(runtimeDatabase, migratedDirectory);
  const mutated = path.join(migratedDirectory, 'mutated-trigger.db');
  fs.copyFileSync(migrated, mutated, fs.constants.COPYFILE_EXCL);
  const mutatedDatabase = new Database(mutated, { fileMustExist: true });
  mutatedDatabase.exec(`DROP TRIGGER main.turn_tool_executions_valid_sequence;
    CREATE TRIGGER turn_tool_executions_valid_sequence
      BEFORE INSERT ON turn_tool_executions
      WHEN NEW.execution_seq<=0
      BEGIN SELECT RAISE(ABORT,'turn tool execution sequence must be positive'); END`);
  mutatedDatabase.pragma('journal_mode = DELETE');
  mutatedDatabase.close();
  const mutatedTarget = path.join(refusalTargetDirectory, 'mutated-target.db');
  child = runRecovery(mutated, mutatedTarget);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /layout\/object SQL is not exact production14-migrated-v2/u);
  assert.equal(fs.existsSync(mutatedTarget), false);

  const sidecarTarget = path.join(refusalTargetDirectory, 'sidecar-target.db');
  fs.writeFileSync(`${sidecarTarget}-shm`, 'counterfeit target sidecar', {
    flag: 'wx', mode: 0o600,
  });
  child = runRecovery(migrated, sidecarTarget);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /target recovery has a shm sidecar/u);
  assert.equal(fs.existsSync(sidecarTarget), false);

  fs.writeFileSync(target, 'do-not-overwrite');
  const targetHash = sha256File(target);
  child = runRecovery(migrated, target);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /target already exists/u);
  assert.equal(sha256File(target), targetHash);
});

test('recovery weigert live-, symlink-, hardlink-, sidecar- en buiten-/tmp-paden', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-path-refusal-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const migratedDirectory = fs.mkdtempSync(path.join(directory, 'migrated-'));
  const runtimeDatabase = path.join(migratedDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const migrated = migrateWithCurrentCode(runtimeDatabase, migratedDirectory);

  let child = runRecovery(path.join(root, 'data', 'eva.db'), path.join(directory, 'live.db'));
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /refusing canonical live database source/u);

  const sourceSymlink = path.join(directory, 'source-symlink.db');
  fs.symlinkSync(migrated, sourceSymlink);
  child = runRecovery(sourceSymlink, path.join(directory, 'symlink.db'));
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /source parent authority changed before open|regular, non-symlink database snapshot/u);

  const sourceHardlink = path.join(directory, 'source-hardlink.db');
  fs.linkSync(migrated, sourceHardlink);
  child = runRecovery(sourceHardlink, path.join(directory, 'hardlink.db'));
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /must not be a hardlinked database snapshot/u);
  fs.unlinkSync(sourceHardlink);

  fs.writeFileSync(`${migrated}-wal`, 'not-self-contained');
  child = runRecovery(migrated, path.join(directory, 'sidecar.db'));
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /source is not self-contained: wal sidecar exists/u);

  child = runRecovery(recoveryProgram, path.join(directory, 'outside-source.db'));
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /strict real \/tmp descendants/u);
  child = runRecovery(migrated, path.join(root, 'recovery-must-not-exist.db'));
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /strict real \/tmp descendants/u);
  assert.equal(fs.existsSync(path.join(root, 'recovery-must-not-exist.db')), false);
});

test('recovery weigert source-pathswap door verplichte ctimeNs-stabiliteit', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-source-race-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDatabase = path.join(directory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, directory);
  const originalSource = path.join(directory, 'original-source.db');
  const counterfeit = path.join(directory, 'counterfeit.db');
  const targetParent = path.join(directory, 'target-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'source-pinned');
  fs.copyFileSync(immutableBackup, counterfeit, fs.constants.COPYFILE_EXCL);
  const intendedHash = sha256File(source);
  const counterfeitHash = sha256File(counterfeit);
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_SOURCE_PINNED: hook,
    },
  });
  await waitForPath(hook);
  fs.renameSync(source, originalSource);
  fs.renameSync(counterfeit, source);
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /source descriptor authority changed before exact hash/u);
  assert.equal(fs.existsSync(target), false);
  assert.equal(fs.existsSync(source), true);
  assert.equal(fs.existsSync(originalSource), true);
  assert.equal(sha256File(originalSource), intendedHash);
  assert.equal(sha256File(source), counterfeitHash);
});

test('recovery publiceert na een parent-pathswap uitsluitend in de gepinde map', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-parent-race-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDatabase = path.join(directory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, directory);
  const targetParent = path.join(directory, 'target-parent');
  const pinnedParent = path.join(directory, 'pinned-parent');
  const replacementParent = path.join(directory, 'replacement-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  fs.mkdirSync(replacementParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'parent-pinned');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_PARENT_PINNED: hook,
    },
  });
  await waitForPath(hook);
  fs.renameSync(targetParent, pinnedParent);
  fs.renameSync(replacementParent, targetParent);
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(fs.existsSync(path.join(pinnedParent, 'recovered.db')), true);
  assert.equal(fs.existsSync(path.join(targetParent, 'recovered.db')), false);
  const report = JSON.parse(result.stdout);
  assert.equal(report.target.oldRows.count, incidentRowCount);
  assert.equal(report.target.fileSha256, sha256File(path.join(pinnedParent, 'recovered.db')));
  assert.equal(report.target.requestedPath, target);
  assert.equal(report.target.currentParentDescriptorPath, pinnedParent);
  assert.equal(report.target.currentPath, path.join(pinnedParent, 'recovered.db'));
  assert.equal(fs.readdirSync(targetParent).length, 0);
});

test('recovery weigert een source-parentwissel tussen projectie en fd-open', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-source-preopen-race-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const incident = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const genuineRoot = path.join(directory, 'genuine-root');
  const counterfeitRoot = path.join(directory, 'counterfeit-root');
  const genuineParent = path.join(genuineRoot, 'source-parent');
  const counterfeitParent = path.join(counterfeitRoot, 'source-parent');
  const targetParent = path.join(directory, 'target-parent');
  for (const candidate of [genuineRoot, counterfeitRoot, genuineParent,
    counterfeitParent, targetParent]) fs.mkdirSync(candidate, { mode: 0o700 });
  const genuineSource = path.join(genuineParent, 'incident.db');
  const counterfeitSource = path.join(counterfeitParent, 'incident.db');
  fs.copyFileSync(incident, genuineSource, fs.constants.COPYFILE_EXCL);
  fs.copyFileSync(immutableBackup, counterfeitSource, fs.constants.COPYFILE_EXCL);
  const genuineHash = sha256File(genuineSource);
  const counterfeitHash = sha256File(counterfeitSource);
  fs.chmodSync(counterfeitSource, 0o000);
  const route = path.join(directory, 'source-route');
  fs.symlinkSync(genuineRoot, route, 'dir');
  const source = path.join(route, 'source-parent', 'incident.db');
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'source-parent-preopen');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_SOURCE_PARENT_PREOPEN: hook,
    },
  });
  await waitForPath(hook);
  fs.unlinkSync(route);
  fs.symlinkSync(counterfeitRoot, route, 'dir');
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /source parent authority changed before open/u);
  assert.equal(fs.existsSync(target), false);
  assert.equal(sha256File(genuineSource), genuineHash);
  fs.chmodSync(counterfeitSource, 0o600);
  assert.equal(sha256File(counterfeitSource), counterfeitHash);
});

test('recovery weigert een target-parentwissel tussen projectie en fd-open', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-target-preopen-race-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const sourceHash = sha256File(source);
  const genuineRoot = path.join(directory, 'genuine-root');
  const replacementRoot = path.join(directory, 'replacement-root');
  const genuineParent = path.join(genuineRoot, 'target-parent');
  const replacementParent = path.join(replacementRoot, 'target-parent');
  for (const candidate of [genuineRoot, replacementRoot, genuineParent,
    replacementParent]) fs.mkdirSync(candidate, { mode: 0o700 });
  const sentinel = path.join(replacementParent, 'sentinel');
  fs.writeFileSync(sentinel, 'outside-target-must-stay-unchanged', { mode: 0o600 });
  const sentinelHash = sha256File(sentinel);
  const route = path.join(directory, 'target-route');
  fs.symlinkSync(genuineRoot, route, 'dir');
  const target = path.join(route, 'target-parent', 'recovered.db');
  const hook = path.join(directory, 'target-parent-preopen');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_TARGET_PARENT_PREOPEN: hook,
    },
  });
  await waitForPath(hook);
  fs.unlinkSync(route);
  fs.symlinkSync(replacementRoot, route, 'dir');
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /target parent authority changed before open/u);
  assert.equal(fs.existsSync(path.join(genuineParent, 'recovered.db')), false);
  assert.equal(fs.existsSync(path.join(replacementParent, 'recovered.db')), false);
  assert.deepEqual(fs.readdirSync(replacementParent), ['sentinel']);
  assert.equal(sha256File(sentinel), sentinelHash);
  assert.equal(sha256File(source), sourceHash);
});

test('recovery weigert dezelfde source/target-parent en gereserveerde basenames', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-authority-alias-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const sameParentTarget = path.join(runtimeDirectory, 'recovered.db');
  let child = runRecovery(source, sameParentTarget);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /source and target parents must differ/u);
  assert.equal(fs.existsSync(sameParentTarget), false);

  const targetParent = path.join(directory, 'target-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  for (const targetName of [
    path.basename(source), `${path.basename(source)}-wal`,
    `${path.basename(source)}-shm`, `${path.basename(source)}-journal`,
  ]) {
    const target = path.join(targetParent, targetName);
    child = runRecovery(source, target);
    assert.notEqual(child.status, 0);
    assert.match(child.stderr, /target basename conflicts with source authority/u);
    assert.equal(fs.existsSync(target), false);
  }
});

test('recovery weigert iedere structureel ongeldige legacyclassificatie', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-legacy-shape-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const validSnapshot = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const cases = [
    {
      label: 'sequence', trigger: 'turn_tool_executions_sequence_immutable',
      sql: 'UPDATE turn_tool_executions SET execution_seq=0 WHERE execution_key=?',
      pattern: /legacy execution sequence is invalid/u,
    },
    {
      label: 'status', trigger: 'turn_tool_executions_terminal_immutable',
      sql: "UPDATE turn_tool_executions SET status='counterfeit' WHERE execution_key=?",
      pattern: /legacy execution status is invalid/u,
    },
    {
      label: 'args', trigger: 'turn_tool_executions_intent_immutable',
      sql: `UPDATE turn_tool_executions SET args='[]' WHERE execution_key=?`,
      pattern: /legacy execution args is invalid/u,
    },
    {
      label: 'binding', trigger: 'turn_tool_executions_intent_immutable',
      sql: "UPDATE turn_tool_executions SET semantic_binding='not-json' WHERE execution_key=?",
      pattern: /legacy execution binding is invalid/u,
    },
    {
      label: 'turn', trigger: 'turn_tool_executions_intent_immutable',
      sql: "UPDATE turn_tool_executions SET turn_id='' WHERE execution_key=?",
      pattern: /legacy execution field shape is invalid/u,
    },
  ];
  for (const fixture of cases) {
    const source = path.join(directory, `${fixture.label}.db`);
    const targetParent = path.join(directory, `${fixture.label}-target`);
    fs.mkdirSync(targetParent, { mode: 0o700 });
    fs.copyFileSync(validSnapshot, source, fs.constants.COPYFILE_EXCL);
    const database = new Database(source, { fileMustExist: true });
    const executionKey = database.prepare(`SELECT execution_key FROM turn_tool_executions
      WHERE execution_verification='legacy_unverifiable' ORDER BY execution_seq LIMIT 1`)
      .get().execution_key;
    const triggerSql = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='trigger' AND name=?`).get(fixture.trigger)?.sql;
    assert.equal(typeof triggerSql, 'string');
    database.exec(`DROP TRIGGER main.${fixture.trigger}`);
    database.prepare(fixture.sql).run(executionKey);
    database.exec(triggerSql);
    database.pragma('journal_mode = DELETE');
    database.close();
    const child = runRecovery(source, path.join(targetParent, 'recovered.db'));
    assert.notEqual(child.status, 0, fixture.label);
    assert.match(child.stderr, fixture.pattern, fixture.label);
    assert.equal(fs.existsSync(path.join(targetParent, 'recovered.db')), false, fixture.label);
    assert.match(child.stderr,
      /final-link-observed=false; automatic-unlink-attempted=false/u, fixture.label);
  }
});

test('recovery vereist private en stabiele bronmetadata', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-source-metadata-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const validSnapshot = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);

  const insecure = path.join(runtimeDirectory, 'insecure.db');
  fs.copyFileSync(validSnapshot, insecure, fs.constants.COPYFILE_EXCL);
  fs.chmodSync(insecure, 0o640);
  const insecureTargetParent = path.join(directory, 'insecure-target');
  fs.mkdirSync(insecureTargetParent, { mode: 0o700 });
  let result = runRecovery(insecure, path.join(insecureTargetParent, 'recovered.db'));
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /source must be private and owned by the current user/u);
  assert.deepEqual(fs.readdirSync(insecureTargetParent), []);

  const targetParent = path.join(directory, 'race-target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'source-buffered');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', validSnapshot, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_SOURCE_BUFFERED: hook,
    },
  });
  await waitForPath(hook);
  fs.chmodSync(validSnapshot, 0o640);
  fs.unlinkSync(hook);
  result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /source descriptor authority changed before exact hash/u);
  assert.match(result.stderr,
    /final-link-observed=false; automatic-unlink-attempted=false/u);
  assert.equal(fs.existsSync(target), false);
});

test('recovery begrenst descriptorhash op gepinde lengte bij voortdurende groei', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-growing-hash-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const originalSize = fs.statSync(source).size;
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'hash-first-chunk');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_HASH_FIRST_CHUNK: hook,
    },
  });
  await waitForPath(hook);
  const hookEvidence = JSON.parse(fs.readFileSync(hook, 'utf8'));
  assert.equal(hookEvidence.descriptorSize, originalSize);
  assert.equal(hookEvidence.position > 0 && hookEvidence.position <= originalSize, true);
  const writer = fs.openSync(source, fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW);
  let grownSize = originalSize + 1;
  fs.ftruncateSync(writer, grownSize);
  const growthLimit = originalSize + 64 * 1024 * 1024;
  const growth = setInterval(() => {
    grownSize = Math.min(growthLimit, grownSize + 1024 * 1024);
    fs.ftruncateSync(writer, grownSize);
  }, 1);
  fs.unlinkSync(hook);
  let timedOut = false;
  const watchdog = setTimeout(() => {
    timedOut = true;
    child.kill('SIGKILL');
  }, 5_000);
  const result = await waitForChild(child);
  clearTimeout(watchdog);
  clearInterval(growth);
  fs.closeSync(writer);
  assert.equal(timedOut, false, 'bounded descriptor hash must not chase a growing EOF');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /source descriptor grew during exact hash/u);
  assert.equal(fs.statSync(source).size > originalSize, true);
  assert.equal(fs.existsSync(target), false);
});

test('recovery weigert same-length mutatie tijdens exact descriptorhash', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-same-length-hash-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const originalSize = fs.statSync(source).size;
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'hash-first-chunk');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_HASH_FIRST_CHUNK: hook,
    },
  });
  await waitForPath(hook);
  const writer = fs.openSync(source, fs.constants.O_RDWR | fs.constants.O_NOFOLLOW);
  const offset = Math.min(4096, originalSize - 1);
  const original = Buffer.allocUnsafe(1);
  assert.equal(fs.readSync(writer, original, 0, 1, offset), 1);
  const replacement = Buffer.from([original[0] ^ 0xff]);
  assert.equal(fs.writeSync(writer, replacement, 0, 1, offset), 1);
  fs.fsyncSync(writer);
  assert.equal(fs.fstatSync(writer).size, originalSize);
  fs.closeSync(writer);
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /source descriptor authority changed during exact hash/u);
  assert.equal(fs.statSync(source).size, originalSize);
  assert.equal(fs.existsSync(target), false);
});

test('recovery weigert bronbestanden boven de 512MiB hard cap vóór read/publicatie', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-source-cap-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const sourceParent = path.join(directory, 'source');
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(sourceParent, { mode: 0o700 });
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const source = path.join(sourceParent, 'oversized.db');
  const descriptor = fs.openSync(source, 'wx', 0o600);
  try { fs.ftruncateSync(descriptor, 512 * 1024 * 1024 + 1); }
  finally { fs.closeSync(descriptor); }
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /source exceeds 536870912 byte recovery cap/u);
  assert.equal(fs.existsSync(target), false);
});

test('recovery weigert meer dan 100000 ledgerregels vóór materialisatie/publicatie', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-row-cap-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const database = new Database(source, { fileMustExist: true });
  try {
    database.exec(`WITH RECURSIVE fixture_number(value) AS (
        VALUES(1) UNION ALL SELECT value+1 FROM fixture_number WHERE value<100001
      )
      INSERT INTO main.turn_tool_executions (
        execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,
        semantic_hash,semantic_binding,output_sha256,material_sha256,
        intent_record_sha256,completion_record_sha256,execution_seq,
        execution_contract,execution_verification
      ) SELECT
        'row-cap:'||value,'row-cap','row_cap_probe','{}','started',
        '2026-08-01T00:00:00.000Z',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
        1000000+value,'ExecutionRecord/v1','legacy_unverifiable'
      FROM fixture_number`);
    database.pragma('journal_mode = DELETE');
  } finally {
    database.close();
  }
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /execution ledger row cap exceeded: 100008/u);
  assert.equal(fs.existsSync(target), false);
});

test('recovery weigert meer dan 1000000 nonledger-rijen vóór hashmaterialisatie', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-nonledger-cap-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const database = new Database(source, { fileMustExist: true });
  try {
    database.exec(`CREATE TABLE recovery_nonledger_cap(value INTEGER NOT NULL);
      WITH RECURSIVE fixture_number(value) AS (
        VALUES(1) UNION ALL SELECT value+1 FROM fixture_number WHERE value<1000001
      ) INSERT INTO recovery_nonledger_cap SELECT value FROM fixture_number`);
    database.pragma('journal_mode = DELETE');
  } finally { database.close(); }
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /table recovery_nonledger_cap row cap exceeded: 1000001/u);
  assert.equal(fs.existsSync(target), false);
});

test('recovery weigert een rowid-tabel waarvan alle intrinsieke aliassen zijn overschaduwd', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-rowid-alias-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const database = new Database(source, { fileMustExist: true });
  database.exec(`CREATE TABLE recovery_all_rowid_aliases_shadowed (
    rowid TEXT, _rowid_ TEXT, oid TEXT, payload TEXT
  ); INSERT INTO recovery_all_rowid_aliases_shadowed VALUES ('r','u','o','payload')`);
  database.pragma('journal_mode = DELETE');
  database.close();
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /no accessible intrinsic rowid alias/u);
  assert.equal(fs.existsSync(target), false);
});

test('recovery handhaaft table/schema-caps als eerste manifestactie', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-manifest-cap-order-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const validSnapshot = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  for (const fixture of [
    {
      label: 'tables', count: 513, prefix: 'CREATE TABLE cap_table_', suffix: '(value);',
      pattern: /source table cap exceeded/u,
    },
    {
      label: 'schema', count: 4097, prefix: 'CREATE VIEW cap_view_', suffix: ' AS SELECT 1;',
      pattern: /source schema object cap exceeded/u,
    },
  ]) {
    const source = path.join(directory, `${fixture.label}.db`);
    fs.copyFileSync(validSnapshot, source, fs.constants.COPYFILE_EXCL);
    const database = new Database(source, { fileMustExist: true });
    const statements = [];
    for (let index = 0; index < fixture.count; index += 1) {
      statements.push(`${fixture.prefix}${index}${fixture.suffix}`);
    }
    statements.push(`CREATE VIEW cap_external_reference AS
      SELECT execution_key FROM turn_tool_executions`);
    database.exec(statements.join('\n'));
    database.pragma('journal_mode = DELETE');
    database.close();
    const targetParent = path.join(directory, `${fixture.label}-target`);
    fs.mkdirSync(targetParent, { mode: 0o700 });
    const target = path.join(targetParent, 'recovered.db');
    const result = runRecovery(source, target);
    assert.notEqual(result.status, 0, fixture.label);
    assert.match(result.stderr, fixture.pattern, fixture.label);
    assert.doesNotMatch(result.stderr, /external execution-ledger references/u, fixture.label);
    assert.equal(fs.existsSync(target), false, fixture.label);
  }
});

test('recovery negeert PATH en geërfde compiler/toolchain-environment volledig', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-toolchain-env-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target');
  const spoofBin = path.join(directory, 'spoof-bin');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  fs.mkdirSync(spoofBin, { mode: 0o700 });
  const marker = path.join(directory, 'spoof-compiler-ran');
  for (const name of ['cc', 'gcc', 'ld', 'ld.bfd', 'as', 'collect2', 'cc1']) {
    const executable = path.join(spoofBin, name);
    fs.writeFileSync(executable, `#!/bin/sh\nprintf spoof > '${marker}'\nexit 97\n`, {
      flag: 'wx', mode: 0o700,
    });
  }
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target, { env: {
    PATH: spoofBin,
    CC: path.join(spoofBin, 'cc'),
    GCC_EXEC_PREFIX: `${spoofBin}${path.sep}`,
    COMPILER_PATH: spoofBin,
    LIBRARY_PATH: spoofBin,
    CPATH: spoofBin,
    C_INCLUDE_PATH: spoofBin,
    CPLUS_INCLUDE_PATH: spoofBin,
    OBJC_INCLUDE_PATH: spoofBin,
    COLLECT_GCC: path.join(spoofBin, 'gcc'),
    COLLECT_LTO_WRAPPER: path.join(spoofBin, 'collect2'),
    LD_LIBRARY_PATH: spoofBin,
    LD_PRELOAD: path.join(spoofBin, 'fake-preload.so'),
    LD_AUDIT: path.join(spoofBin, 'fake-audit.so'),
    GCONV_PATH: spoofBin,
  } });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(fs.existsSync(marker), false);
  assert.equal(fs.existsSync(target), true);
});

test('recovery weigert targetsidecar die in check-naar-link-venster verschijnt', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-target-sidecar-race-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const sidecar = `${target}-wal`;
  const hook = path.join(directory, 'publication-ready');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_BEFORE_LINK_RELEASE: hook,
    },
  });
  await waitForPath(hook);
  fs.writeFileSync(sidecar, 'counterfeit WAL sidecar', { flag: 'wx', mode: 0o600 });
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /target sidecar exists at link errno=17 File exists/u);
  assert.equal(fs.existsSync(target), false, 'final must remain absent before atomic link');
  assert.equal(fs.readFileSync(sidecar, 'utf8'), 'counterfeit WAL sidecar');
});

for (const kind of ['source', 'binary']) {
  test(`recovery weigert helper-${kind} named-inode-swap vóór exec`, async (t) => {
    const directory = fs.mkdtempSync(`/tmp/bottie-ledger-helper-${kind}-swap-`);
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
    const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
    fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
    const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
    const targetParent = path.join(directory, 'target');
    fs.mkdirSync(targetParent, { mode: 0o700 });
    const target = path.join(targetParent, 'recovered.db');
    const hook = path.join(directory, `helper-${kind}-pinned`);
    const hookName = kind === 'source'
      ? 'EVA_LEDGER_RECOVERY_TEST_HOOK_HELPER_SOURCE_PINNED'
      : 'EVA_LEDGER_RECOVERY_TEST_HOOK_HELPER_BINARY_PINNED';
    const child = spawn(process.execPath, [
      recoveryProgram, '--source', source, '--target', target,
    ], {
      cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, NODE_TEST_CONTEXT: 'child-v8', [hookName]: hook },
    });
    await waitForPath(hook);
    const authority = JSON.parse(fs.readFileSync(hook, 'utf8'));
    assert.equal(path.dirname(authority.directory), fs.realpathSync.native('/tmp'));
    assert.match(path.basename(authority.directory), /^eva-ledger-publisher-[A-Za-z0-9]+$/u);
    t.after(() => fs.rmSync(authority.directory, { recursive: true, force: true }));
    const buildFile = kind === 'source' ? authority.sourcePath : authority.binaryPath;
    const displaced = `${buildFile}.pinned-original`;
    fs.renameSync(buildFile, displaced);
    const spoofMarker = path.join(directory, `${kind}-spoof-executed`);
    const replacement = kind === 'source' ? 'int main(void){return 0;}\n'
      : `#!/bin/sh\nprintf executed > '${spoofMarker}'\nprintf 'CLEANED 1 1 1 1\\nVERIFIED 1 1 0 1 ${'0'.repeat(64)}\\n'\n`;
    fs.writeFileSync(buildFile, replacement, {
      flag: 'wx', mode: kind === 'source' ? 0o600 : 0o700,
    });
    fs.unlinkSync(hook);
    const result = await waitForChild(child);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, new RegExp(
      `publication helper ${kind === 'source' ? 'source' : 'binary'} named authority changed`, 'u',
    ));
    assert.equal(fs.existsSync(target), false);
    assert.equal(fs.existsSync(spoofMarker), false, 'forged protocol helper must never execute');
    assert.equal(fs.existsSync(displaced), true, 'pinned displaced evidence must not be unlinked');
  });
}

test('helpersterfte na link rapporteert tri-state op exact named inode en standalone doel', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-helper-postlink-death-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target, { env: {
    NODE_TEST_CONTEXT: 'child-v8',
    EVA_LEDGER_RECOVERY_TEST_HELPER_DIE_AFTER_LINK: 'true',
  } });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /final-link-observed=true/u);
  assert.equal(fs.existsSync(target), true);
  assert.equal(fs.statSync(target).nlink, 1);
  for (const suffix of ['-wal', '-shm', '-journal']) {
    assert.equal(fs.existsSync(`${target}${suffix}`), false);
  }
  const database = new Database(target, { readonly: true, fileMustExist: true });
  try {
    assert.equal(database.pragma('integrity_check', { simple: true }), 'ok');
    assert.deepEqual(
      database.prepare("PRAGMA main.table_info('turn_tool_executions')")
        .all().map((column) => column.name),
      oldColumnNames,
    );
  } finally { database.close(); }
});

test('helpersterfte in releasevenster rapporteert finalLinkObserved=uncertain', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-helper-prelink-death-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target, { env: {
    NODE_TEST_CONTEXT: 'child-v8',
    EVA_LEDGER_RECOVERY_TEST_HELPER_DIE_BEFORE_LINK: 'true',
  } });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /finalLinkObserved=uncertain/u);
  assert.equal(fs.existsSync(target), false);
});

test('recovery valideert verified v1/v2-structuur vóór bewijs-hashes', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-verified-shape-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const validSnapshot = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const cases = [
    {
      label: 'empty-turn', key: 'recovery:test:added:started',
      changes: { turn_id: '' }, pattern: /verified execution field shape is invalid/u,
    },
    {
      label: 'semantic-hash', key: 'recovery:test:added:started',
      changes: { semantic_hash: 'not-a-sha256' },
      pattern: /verified execution semantic hash is invalid/u,
    },
    {
      label: 'binding-shape', key: 'recovery:test:added:started',
      changes: { semantic_binding: canonicalJson({ unknown: true }) },
      pattern: /verified execution binding is invalid/u,
    },
    {
      label: 'transport-output', key: null,
      changes: { output: null }, pattern: /verified execution field shape is invalid/u,
    },
    {
      label: 'transport-tool', key: 'recovery:test:added:transport',
      changes: { tool: 'not_transport_namespace' },
      pattern: /transport execution tool is outside @transport\/telegram\.\*/u,
    },
    {
      label: 'leaf-tool-ordinary-status', key: 'recovery:test:added:started',
      changes: { tool: '@transport/telegram.sendMessage' },
      pattern: /Telegram leaf tool requires transport lifecycle/u,
    },
    {
      label: 'unknown-telegram-namespace-ordinary-status',
      key: 'recovery:test:added:started',
      changes: { tool: '@transport/telegram.futureMethod' },
      pattern: /Telegram namespace requires an exact transport leaf lifecycle/u,
    },
    {
      label: 'transport-accepted-shape', key: 'recovery:test:added:transport',
      changes: { output: canonicalJson({ accepted: false }) },
      pattern: /transport_accepted output is invalid/u,
    },
    {
      label: 'transport-output-noncanonical', key: 'recovery:test:added:transport',
      noncanonicalTransportOutput: true,
      changes: {}, pattern: /transport_accepted output is not canonical/u,
    },
  ];
  for (const fixture of cases) {
    const source = path.join(directory, `${fixture.label}.db`);
    const targetParent = path.join(directory, `${fixture.label}-target`);
    fs.mkdirSync(targetParent, { mode: 0o700 });
    fs.copyFileSync(validSnapshot, source, fs.constants.COPYFILE_EXCL);
    const database = new Database(source, { fileMustExist: true });
    const executionKey = fixture.key || database.prepare(`SELECT execution_key
      FROM main.turn_tool_executions WHERE execution_verification='verified'
        AND status='transport_accepted' ORDER BY execution_seq LIMIT 1`).get()?.execution_key;
    assert.ok(executionKey, fixture.label);
    const changes = fixture.noncanonicalTransportOutput ? {
      output: JSON.stringify(JSON.parse(database.prepare(`SELECT output
        FROM main.turn_tool_executions WHERE execution_key=?`).get(executionKey).output), null, 2),
    } : fixture.changes;
    rewriteVerifiedIntent(database, executionKey, changes);
    database.pragma('journal_mode = DELETE');
    database.close();
    const result = runRecovery(source, path.join(targetParent, 'recovered.db'));
    assert.notEqual(result.status, 0, fixture.label);
    assert.match(result.stderr, fixture.pattern, fixture.label);
    assert.equal(fs.existsSync(path.join(targetParent, 'recovered.db')), false, fixture.label);
  }
});

test('recovery valideert ook legacy transportstatus en output exact', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-legacy-transport-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const validSnapshot = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const cases = [
    {
      label: 'tool', changes: {
        tool: 'legacy_wrong_namespace', status: 'transport_rejected',
        finished_ts: '2026-08-01T00:00:02.000Z',
        output: canonicalJson({
          classification: 'rejected', attempts: 1, message: 'fixture rejected',
        }),
      }, pattern: /transport execution tool is outside @transport\/telegram\.\*/u,
    },
    {
      label: 'output', changes: {
        tool: '@transport/telegram.sendMessage', status: 'transport_rejected',
        finished_ts: '2026-08-01T00:00:02.000Z',
        output: canonicalJson({
          classification: 'uncertain', attempts: 1, message: 'wrong classification',
        }),
      }, pattern: /transport_rejected output is invalid/u,
    },
    {
      label: 'unknown-telegram-namespace-ordinary-status', changes: {
        tool: '@transport/telegram.futureMethod', status: 'done',
        finished_ts: '2026-08-01T00:00:02.000Z', output: '{"legacy":1}',
      }, pattern: /Telegram namespace requires an exact transport leaf lifecycle/u,
    },
    {
      label: 'telegram-leaf-ordinary-status', changes: {
        tool: '@transport/telegram.sendMessage', status: 'done',
        finished_ts: '2026-08-01T00:00:02.000Z', output: '{"legacy":1}',
      }, pattern: /Telegram leaf tool requires transport lifecycle/u,
    },
  ];
  for (const fixture of cases) {
    const source = path.join(directory, `${fixture.label}.db`);
    const targetParent = path.join(directory, `${fixture.label}-target`);
    fs.mkdirSync(targetParent, { mode: 0o700 });
    fs.copyFileSync(validSnapshot, source, fs.constants.COPYFILE_EXCL);
    const database = new Database(source, { fileMustExist: true });
    const executionKey = database.prepare(`SELECT execution_key
      FROM main.turn_tool_executions WHERE execution_verification='legacy_unverifiable'
      ORDER BY execution_seq LIMIT 1`).get().execution_key;
    const transportArgs = database.prepare(`SELECT args FROM main.turn_tool_executions
      WHERE execution_key='recovery:test:added:transport'`).get().args;
    mutateExecutionRow(database, executionKey, {
      args: transportArgs, ...fixture.changes,
    });
    database.pragma('journal_mode = DELETE');
    database.close();
    const result = runRecovery(source, path.join(targetParent, 'recovered.db'));
    assert.notEqual(result.status, 0, fixture.label);
    assert.match(result.stderr, fixture.pattern, fixture.label);
    assert.equal(fs.existsSync(path.join(targetParent, 'recovered.db')), false, fixture.label);
  }
});

test('recovery weigert standalone WAL-header fail-closed en bewaart persistente pragmas', (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-journal-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const validSnapshot = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const source = path.join(runtimeDirectory, 'wal-source.db');
  fs.copyFileSync(validSnapshot, source, fs.constants.COPYFILE_EXCL);
  const sourceDatabase = new Database(source, { fileMustExist: true });
  assert.equal(sourceDatabase.pragma('journal_mode = WAL', { simple: true }), 'wal');
  const stablePragmas = {
    encoding: String(sourceDatabase.pragma('encoding', { simple: true })),
    pageSize: String(sourceDatabase.pragma('page_size', { simple: true })),
    autoVacuum: String(sourceDatabase.pragma('auto_vacuum', { simple: true })),
    userVersion: String(sourceDatabase.pragma('user_version', { simple: true })),
    applicationId: String(sourceDatabase.pragma('application_id', { simple: true })),
  };
  sourceDatabase.close();
  fs.chmodSync(source, 0o600);
  for (const suffix of ['-wal', '-shm', '-journal']) {
    assert.equal(fs.existsSync(`${source}${suffix}`), false);
  }
  const targetParent = path.join(directory, 'target');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const result = runRecovery(source, target);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /uses WAL header and is not a standalone recovery image/u);
  assert.equal(fs.existsSync(target), false);
  assert.deepEqual(stablePragmas, {
    encoding: 'UTF-8', pageSize: '4096', autoVacuum: '0',
    userVersion: '14', applicationId: '1163280689',
  });
});

test('recovery behoudt haar gepubliceerde bewijs na een late fout', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-cleanup-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'after-publish');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_AFTER_PUBLISH: hook,
      EVA_LEDGER_RECOVERY_TEST_FAIL_AFTER_PUBLISH: 'true',
    },
  });
  await waitForPath(hook);
  assert.equal(fs.existsSync(target), true);
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /forced recovery failure after publication/u);
  assert.match(result.stderr,
    /final-link-observed=true; automatic-unlink-attempted=false/u);
  assert.match(result.stderr, /final target name=recovered\.db state=present relation=same-inode/u);
  assert.equal(fs.existsSync(target), true);
  assert.equal(ledgerRowsManifest(target).count, incidentRowCount);
});

test('recovery raakt na target-pathswap geen vervangend doel en behoudt beide bewijzen', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-cleanup-race-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const displaced = path.join(targetParent, 'published-candidate.db');
  const hook = path.join(directory, 'after-publish');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_AFTER_PUBLISH: hook,
    },
  });
  await waitForPath(hook);
  fs.renameSync(target, displaced);
  fs.writeFileSync(target, 'replacement-evidence', { mode: 0o600, flag: 'wx' });
  const replacementHash = sha256File(target);
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /target authority changed after publication/u);
  assert.match(result.stderr,
    /final-link-observed=true; automatic-unlink-attempted=false/u);
  assert.match(result.stderr,
    /final target name=recovered\.db state=present relation=different-inode/u);
  assert.equal(sha256File(target), replacementHash);
  assert.equal(fs.existsSync(displaced), true);
  assert.equal(ledgerRowsManifest(displaced).count, incidentRowCount);
});

test('crash vóór atomische final-link laat de finale naam afwezig', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-prelink-crash-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'publication-ready');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_PUBLICATION_READY: hook,
    },
  });
  await waitForPath(hook);
  child.kill('SIGKILL');
  const result = await waitForChild(child);
  assert.equal(result.status, null);
  assert.equal(fs.existsSync(target), false);
  assert.deepEqual(fs.readdirSync(targetParent), []);
});

test('crash ná atomische final-link laat alleen een volledig geverifieerd doel', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-postlink-crash-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const expectedRows = ledgerRowsManifest(source);
  const targetParent = path.join(directory, 'target-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'after-final-link');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_AFTER_PUBLISH: hook,
    },
  });
  await waitForPath(hook);
  child.kill('SIGKILL');
  const result = await waitForChild(child);
  assert.equal(result.status, null);
  assert.equal(fs.existsSync(target), true);
  assert.deepEqual(ledgerRowsManifest(target), expectedRows);
  assert.deepEqual(fs.readdirSync(targetParent), ['recovered.db']);
  assert.equal(fs.statSync(target).nlink, 1);
  const database = new Database(target, { readonly: true, fileMustExist: true });
  try {
    assert.equal(database.pragma('integrity_check', { simple: true }), 'ok');
    assert.deepEqual(
      database.prepare("PRAGMA main.table_info('turn_tool_executions')")
        .all().map((column) => column.name),
      oldColumnNames,
    );
  } finally {
    database.close();
  }
});

test('atomische final-link is no-clobber bij een late doelcreatie', async (t) => {
  const directory = fs.mkdtempSync('/tmp/bottie-ledger-final-noclobber-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const runtimeDirectory = fs.mkdtempSync(path.join(directory, 'runtime-'));
  const runtimeDatabase = path.join(runtimeDirectory, 'eva.db');
  fs.copyFileSync(immutableBackup, runtimeDatabase, fs.constants.COPYFILE_EXCL);
  const source = migrateWithCurrentCode(runtimeDatabase, runtimeDirectory);
  const targetParent = path.join(directory, 'target-parent');
  fs.mkdirSync(targetParent, { mode: 0o700 });
  const target = path.join(targetParent, 'recovered.db');
  const hook = path.join(directory, 'publication-ready');
  const child = spawn(process.execPath, [
    recoveryProgram, '--source', source, '--target', target,
  ], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, NODE_TEST_CONTEXT: 'child-v8',
      EVA_LEDGER_RECOVERY_TEST_HOOK_PUBLICATION_READY: hook,
    },
  });
  await waitForPath(hook);
  fs.writeFileSync(target, 'late-target-must-not-be-overwritten', {
    flag: 'wx', mode: 0o600,
  });
  const sentinelHash = sha256File(target);
  fs.unlinkSync(hook);
  const result = await waitForChild(child);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /target exists at link errno=17 File exists/u);
  assert.match(result.stderr, /final-link-observed=false/u);
  assert.equal(sha256File(target), sentinelHash);
  assert.deepEqual(fs.readdirSync(targetParent), ['recovered.db']);
});

const operatorProbeSource = process.env.EVA_LEDGER_RECOVERY_OPERATOR_PROBE_SOURCE;
if (operatorProbeSource != null) {
  test('expliciete operatorprobe valideert een vooraf gemaakte private /tmp-backupkopie', (t) => {
    assert.equal(path.isAbsolute(operatorProbeSource), true);
    const source = fs.realpathSync.native(operatorProbeSource);
    const relative = path.relative(fs.realpathSync.native('/tmp'), source);
    assert.equal(relative !== '' && relative !== '..'
      && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative), true);
    const sourceStat = fs.lstatSync(source);
    assert.equal(sourceStat.isFile() && !sourceStat.isSymbolicLink(), true);
    assert.equal(sourceStat.nlink, 1);
    assert.equal(sourceStat.uid, process.geteuid());
    assert.equal(sourceStat.mode & 0o077, 0);
    const sourceHash = sha256File(source);
    const originalRows = ledgerRowsByKey(source);
    const directory = fs.mkdtempSync('/tmp/bottie-ledger-operator-probe-');
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const runtimeDatabase = path.join(directory, 'eva.db');
    fs.copyFileSync(source, runtimeDatabase, fs.constants.COPYFILE_EXCL);
    fs.chmodSync(runtimeDatabase, 0o600);
    const incident = migrateWithCurrentCode(runtimeDatabase, directory);
    const targetParent = path.join(directory, 'target');
    fs.mkdirSync(targetParent, { mode: 0o700 });
    const recovered = path.join(targetParent, 'recovered.db');
    const result = runRecovery(incident, recovered);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    const recoveredRows = ledgerRowsByKey(recovered);
    for (const [executionKey, fields] of originalRows) {
      assert.deepEqual(recoveredRows.get(executionKey), fields, executionKey);
    }
    assert.equal(sha256File(source), sourceHash);
  });
}
