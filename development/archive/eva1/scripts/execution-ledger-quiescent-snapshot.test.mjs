import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';

import Database from 'better-sqlite3';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const program = path.join(repositoryRoot, 'scripts', 'execution-ledger-quiescent-snapshot.mjs');
const temporaryRoots = [];
const publisherSourceSha256 = 'd98981ea08a4814bfb1448cc990284f70db442e0d96d1ab1c99385fbe7627a25';
const publisherBinarySha256 = '52dc1dfa134a5f938ebef6b59d70a5837b79c27de8793c9605e19bb86521a0f7';
const publisherReleaseRecordSha256 = 'b91149f0137ca3a2e9b8122d2f68796fe4e62e9d320f0a43ed6a464f8779236b';
const nodeBinarySha256 = 'e6ec2c188d83d813f81f2de8aea084d74dce603ac1abedd0a30ad941b10087b2';
const sqliteEntrySha256 = '82db11c4ee43a41d859988c5db42c3771dff565371f94bacbd1e4d8d6ceb47cd';
const sqlitePackageSha256 = '58e81504576abccfacbfd08dc9432dc3a00d0fc0454685cb7a76aaf976cf25a9';
const sqliteAddonSha256 = 'd7d9272b12d11c1dc2bb787741b1b7c4037d336155f316ace3420410c28fda37';
const sqliteCompileOptionsSha256 = '23d946f02586ea243869b21fc44a8c6058e2b63a11da0def258a015360522e41';

function privateDirectory(label) {
  const directory = fs.mkdtempSync(path.join('/tmp', `eva-quiescent-test-${label}-`));
  fs.chmodSync(directory, 0o700);
  temporaryRoots.push(directory);
  return directory;
}

function fileSha256(filename) {
  const descriptor = fs.openSync(
    filename,
    fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NOATIME,
  );
  try {
    return crypto.createHash('sha256').update(fs.readFileSync(descriptor)).digest('hex');
  } finally {
    fs.closeSync(descriptor);
  }
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

function isolatedRuntimeMarkerEnvironment() {
  return process.env.EVA_ISOLATED_RESOURCE_GATE === '1'
    ? { EVA_ISOLATED_RESOURCE_GATE: '1' } : {};
}

function exactEvidence(filename) {
  const stat = fs.statSync(filename, { bigint: true });
  return {
    dev: String(stat.dev),
    ino: String(stat.ino),
    mode: Number(stat.mode & 0o777n),
    nlink: String(stat.nlink),
    uid: String(stat.uid),
    gid: String(stat.gid),
    size: String(stat.size),
    atimeNs: String(stat.atimeNs),
    mtimeNs: String(stat.mtimeNs),
    ctimeNs: String(stat.ctimeNs),
  };
}

function trioEvidence(sourceDirectory) {
  return Object.fromEntries(fs.readdirSync(sourceDirectory).sort().map((name) => {
    const filename = path.join(sourceDirectory, name);
    const metadata = exactEvidence(filename);
    const sha256 = fileSha256(filename);
    const after = exactEvidence(filename);
    assert.deepEqual({ ...after, atimeNs: metadata.atimeNs }, metadata);
    return [name, { ...after, sha256 }];
  }));
}

function copyTrio(databasePath, sourceDirectory) {
  for (const suffix of ['', '-wal', '-shm']) {
    const destination = path.join(sourceDirectory, `eva.db${suffix}`);
    fs.copyFileSync(`${databasePath}${suffix}`, destination, fs.constants.COPYFILE_EXCL);
    fs.chmodSync(destination, 0o600);
  }
}

function createWalFixture(label = 'wal') {
  const originDirectory = privateDirectory(`${label}-origin`);
  const sourceDirectory = privateDirectory(`${label}-source`);
  const targetDirectory = privateDirectory(`${label}-target`);
  const originPath = path.join(originDirectory, 'eva.db');
  const database = new Database(originPath);
  database.pragma('journal_mode = WAL');
  database.pragma('wal_autocheckpoint = 0');
  database.exec(`
    PRAGMA foreign_keys = ON;
    CREATE TABLE household_events (
      event_id INTEGER PRIMARY KEY,
      value TEXT NOT NULL UNIQUE
    );
    CREATE TABLE dependent_events (
      event_id INTEGER PRIMARY KEY REFERENCES household_events(event_id),
      note TEXT NOT NULL
    );
    INSERT INTO household_events(event_id, value) VALUES (1, 'main-page');
    INSERT INTO dependent_events(event_id, note) VALUES (1, 'main-page');
  `);
  database.pragma('wal_checkpoint(TRUNCATE)');
  database.exec(`
    INSERT INTO household_events(event_id, value) VALUES (2, 'committed-only-in-wal');
    INSERT INTO dependent_events(event_id, note) VALUES (2, 'committed-only-in-wal');
  `);
  copyTrio(originPath, sourceDirectory);
  return {
    database,
    originPath,
    sourceDirectory,
    targetDirectory,
    outputName: 'eva-quiescent.db',
  };
}

function createEvaProductionFtsWalFixture(label = 'production-fts') {
  const originDirectory = privateDirectory(`${label}-origin`);
  const sourceDirectory = privateDirectory(`${label}-source`);
  const targetDirectory = privateDirectory(`${label}-target`);
  const originPath = path.join(originDirectory, 'eva.db');
  const database = new Database(originPath);
  database.pragma('journal_mode = WAL');
  database.pragma('wal_autocheckpoint = 0');
  database.exec(`
    PRAGMA foreign_keys = ON;
    CREATE TABLE memories (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      category TEXT DEFAULT 'feit',
      person TEXT,
      content TEXT NOT NULL,
      importance REAL DEFAULT 0.5,
      recall_count INTEGER DEFAULT 0,
      last_recalled TEXT,
      feeling TEXT,
      archived INTEGER DEFAULT 0,
      archived_reason TEXT
    );
    CREATE VIRTUAL TABLE memories_fts
      USING fts5(content, content='memories', content_rowid='id');
    CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END;
    CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content)
      VALUES ('delete', old.id, old.content);
    END;
    CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content)
      VALUES ('delete', old.id, old.content);
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END;
    CREATE TABLE visible_rowid_alias (
      rowid TEXT NOT NULL,
      value TEXT NOT NULL
    );
    CREATE TABLE double_visible_rowid_alias (
      rowid TEXT NOT NULL,
      _rowid_ TEXT NOT NULL,
      value TEXT NOT NULL
    );
    CREATE TABLE household_preferences (
      kind TEXT NOT NULL,
      slot INTEGER NOT NULL,
      value TEXT NOT NULL,
      PRIMARY KEY(kind, slot)
    ) WITHOUT ROWID;
    CREATE VIEW active_memories AS
      SELECT id, content FROM memories WHERE COALESCE(archived, 0)=0;
    INSERT INTO memories(ts, category, content, importance)
    VALUES ('2026-08-13T10:00:00.000Z', 'gebeurtenis', 'basis uit hoofddatabase', 0.8);
  `);
  database.pragma('wal_checkpoint(TRUNCATE)');
  database.exec(`
    INSERT INTO memories(ts, category, person, content, importance, feeling)
    VALUES (
      '2026-08-13T10:01:00.000Z', 'voorkeur', 'gezin',
      'gezinscontinuiteit alleen committed in WAL', 0.95, 'verbonden'
    );
    INSERT INTO visible_rowid_alias(rowid, value) VALUES
      ('zichtbaar-a', 'zelfde-waarde'), ('zichtbaar-b', 'zelfde-waarde');
    INSERT INTO double_visible_rowid_alias(rowid, _rowid_, value) VALUES
      ('zichtbaar-a', 'ook-zichtbaar-a', 'zelfde-waarde'),
      ('zichtbaar-b', 'ook-zichtbaar-b', 'zelfde-waarde');
    INSERT INTO household_preferences(kind, slot, value)
    VALUES ('ontbijt', 1, 'havermout'), ('muziek', 2, 'rustig');
  `);
  copyTrio(originPath, sourceDirectory);
  return {
    database,
    originPath,
    sourceDirectory,
    targetDirectory,
    outputName: 'eva-production-fts.db',
  };
}

function closeFixture(fixture) {
  if (fixture.database?.open) fixture.database.close();
}

function runSnapshot(fixture, extra = {}) {
  return spawnSync(extra.execPath ?? process.execPath, [
    extra.program ?? program,
    '--source-dir', fixture.sourceDirectory,
    '--target-dir', fixture.targetDirectory,
    '--output', fixture.outputName,
  ], {
    cwd: repositoryRoot,
    encoding: 'utf8',
    env: {
      ...process.env,
      TMPDIR: fixture.sourceDirectory,
      TEMP: fixture.sourceDirectory,
      TMP: fixture.sourceDirectory,
      ...extra.env,
    },
    timeout: extra.timeout ?? 30_000,
  });
}

function counterfeitProgram(label, relativeFile, mutate) {
  const root = privateDirectory(`counterfeit-${label}`);
  const scripts = path.join(root, 'scripts');
  const modules = path.join(root, 'node_modules');
  fs.mkdirSync(scripts, { mode: 0o700 });
  fs.mkdirSync(modules, { mode: 0o700 });
  const copiedProgram = path.join(scripts, path.basename(program));
  fs.copyFileSync(program, copiedProgram, fs.constants.COPYFILE_EXCL);
  for (const packageName of ['better-sqlite3', 'bindings', 'file-uri-to-path']) {
    fs.cpSync(
      path.join(repositoryRoot, 'node_modules', packageName),
      path.join(modules, packageName),
      { recursive: true, errorOnExist: true, force: false },
    );
  }
  const counterfeit = path.join(modules, 'better-sqlite3', relativeFile);
  mutate(counterfeit);
  return copiedProgram;
}

function assertFailed(result, pattern) {
  assert.notEqual(result.status, 0, `unexpected success: ${result.stdout}`);
  assert.match(result.stderr, pattern);
}

function waitForFile(filename, child, timeout = 10_000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (fs.existsSync(filename)) return resolve(fs.readFileSync(filename, 'utf8'));
      if (child.exitCode != null) return reject(new Error(`child exited ${child.exitCode}`));
      if (Date.now() - started > timeout) return reject(new Error(`hook timeout: ${filename}`));
      setTimeout(poll, 10);
    };
    poll();
  });
}

function spawnWithHook(fixture, variableName, hookPath) {
  return spawn(process.execPath, [
    program,
    '--source-dir', fixture.sourceDirectory,
    '--target-dir', fixture.targetDirectory,
    '--output', fixture.outputName,
  ], {
    cwd: repositoryRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      NODE_TEST_CONTEXT: 'eva-quiescent-snapshot-test',
      [variableName]: hookPath,
    },
  });
}

function collectChild(child) {
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', (chunk) => { stdout += chunk; });
  child.stderr.on('data', (chunk) => { stderr += chunk; });
  return new Promise((resolve) => child.once('close', (status, signal) => {
    resolve({ status, signal, stdout, stderr });
  }));
}

after(() => {
  for (const directory of temporaryRoots) {
    const resolved = path.resolve(directory);
    assert.match(resolved, /^\/tmp\/eva-quiescent-test-[^/]+$/u);
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

test('creates a byte-preserving, self-contained DELETE snapshot containing WAL-only commits', () => {
  assert.notEqual(process.geteuid(), 0, 'publication regression must run as a normal user');
  const status = fs.readFileSync('/proc/self/status', 'utf8');
  const capabilitiesMatch = /^CapEff:\s*([a-f0-9]+)$/imu.exec(status);
  assert.ok(capabilitiesMatch);
  const effectiveCapabilities = BigInt(`0x${capabilitiesMatch[1]}`);
  assert.equal(effectiveCapabilities & (1n << 2n), 0n, 'CAP_DAC_READ_SEARCH must be absent');
  const fixture = createWalFixture('success');
  const sourceBefore = trioEvidence(fixture.sourceDirectory);
  const result = runSnapshot(fixture);
  closeFixture(fixture);

  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  const output = path.join(fixture.targetDirectory, fixture.outputName);
  assert.deepEqual(fs.readdirSync(fixture.targetDirectory), [fixture.outputName]);
  assert.deepEqual(trioEvidence(fixture.sourceDirectory), sourceBefore);
  assert.equal(report.contract, 'eva.execution-ledger-quiescent-snapshot.v1');
  assert.equal(report.source.unchanged, true);
  assert.deepEqual(Object.keys(report.source.files).sort(), ['eva.db', 'eva.db-shm', 'eva.db-wal']);
  for (const evidence of Object.values(report.source.files)) {
    assert.equal(evidence.unchanged, true);
    assert.deepEqual(evidence.after, evidence.before);
    assert.match(evidence.before.sha256, /^[a-f0-9]{64}$/u);
  }
  assert.equal(report.output.path, output);
  assert.equal(report.output.sha256, fileSha256(output));
  assert.equal(report.output.journalMode, 'delete');
  assert.equal(report.output.journalHeader, 'rollback');
  assert.equal(report.source.sqliteOpenedDirectly, false);
  assert.equal(report.source.wal.consumedByScratchCheckpoint, true);
  assert.equal(report.source.wal.present, true);
  assert.equal(report.sqlite.acquisition.evidenceShmOpenedBySQLite, false);
  assert.equal(report.sqlite.acquisition.scratchShmGeneratedBySQLite, true);
  assert.equal(report.sqlite.acquisition.checkpoint.executed, true);
  assert.equal(report.sqlite.acquisition.checkpoint.result.busy, '0');
  assert.equal(
    report.sqlite.acquisition.checkpoint.result.log,
    report.sqlite.acquisition.checkpoint.result.checkpointed,
  );
  assert.equal(report.sqlite.acquisition.checkpoint.scratchShm.absentBeforeSqlite, true);
  assert.equal(report.sqlite.acquisition.checkpoint.scratchShm.observedAfterSqliteOpen, true);
  assert.match(report.sqlite.acquisition.checkpoint.executionSha256, /^[a-f0-9]{64}$/u);
  assert.equal(
    report.source.wal.checkpointExecutionSha256,
    report.sqlite.acquisition.checkpoint.executionSha256,
  );
  assert.equal(
    report.sqlite.acquisition.checkpoint.sourceWalSha256,
    report.source.files['eva.db-wal'].before.sha256,
  );
  assert.deepEqual(report.source.vfs.exactOpenDescriptors, {
    'eva.db': true,
    'eva.db-wal': true,
    'eva.db-shm': true,
  });
  assert.equal(
    report.publication.method,
    'parent-created pinned O_TMPFILE + linkat(/proc/self/fd/3, AT_SYMLINK_FOLLOW)',
  );
  assert.equal(report.publication.helper.inheritedAnonymousDescriptor, 3);
  assert.equal(report.publication.helper.linkMechanism, 'AT_SYMLINK_FOLLOW');
  assert.equal(report.publication.helper.handshake, 'DURABLE_LINKED-after-parent-fsync');
  assert.equal(report.execution.mode, 'single-process-direct');
  assert.equal(report.execution.internalBrokerAvailable, false);
  assert.equal(report.execution.hardDeadlineClaim, false);
  assert.equal(report.conversionRuntime.contract, 'eva.quiescent-conversion-runtime.v1');
  assert.equal(report.conversionRuntime.node.version, 'v22.22.3');
  assert.equal(report.conversionRuntime.node.path, '/usr/bin/node');
  assert.equal(report.conversionRuntime.node.mode, '0755');
  assert.equal(report.conversionRuntime.node.nlink, '1');
  assert.equal(report.conversionRuntime.node.sha256, nodeBinarySha256);
  if (process.env.EVA_ISOLATED_RESOURCE_GATE === '1') {
    assert.equal(report.conversionRuntime.node.uid, String(process.geteuid()));
    assert.equal(
      report.conversionRuntime.node.ownershipAuthority.kind,
      'isolated-singleton-idmap-readonly-usr',
    );
    assert.equal(
      report.conversionRuntime.node.ownershipAuthority.runnerMarker,
      'EVA_ISOLATED_RESOURCE_GATE=1',
    );
    assert.deepEqual(report.conversionRuntime.node.ownershipAuthority.uidMap, [
      process.geteuid(), process.geteuid(), 1,
    ]);
    assert.equal(report.conversionRuntime.node.ownershipAuthority.mount.mountPoint, '/usr');
    assert.deepEqual(
      report.conversionRuntime.node.ownershipAuthority.mount.requiredOptions,
      ['nodev', 'nosuid', 'ro'],
    );
  } else {
    assert.equal(report.conversionRuntime.node.uid, '0');
    assert.equal(report.conversionRuntime.node.ownershipAuthority.kind, 'root-owned-host-runtime');
  }
  assert.equal(report.conversionRuntime.betterSqlite3.version, '11.10.0');
  assert.equal(report.conversionRuntime.betterSqlite3.entry.sha256, sqliteEntrySha256);
  assert.equal(report.conversionRuntime.betterSqlite3.packageJson.sha256, sqlitePackageSha256);
  assert.equal(report.conversionRuntime.betterSqlite3.nativeAddon.sha256, sqliteAddonSha256);
  assert.equal(report.conversionRuntime.sqlite.version, '3.49.2');
  assert.equal(
    report.conversionRuntime.sqlite.sourceId,
    '2025-05-07 10:39:52 17144570b0d96ae63cd6f3edca39e27ebd74925252bbaf6723bcb2f6b4861fb1',
  );
  assert.equal(report.conversionRuntime.sqlite.compileOptionsSha256, sqliteCompileOptionsSha256);
  assert.equal(
    crypto.createHash('sha256')
      .update(canonicalJson(report.conversionRuntime.sqlite.compileOptions)).digest('hex'),
    sqliteCompileOptionsSha256,
  );
  assert.equal(
    crypto.createHash('sha256').update(canonicalJson({
      node: report.conversionRuntime.node,
      betterSqlite3: report.conversionRuntime.betterSqlite3,
      sqlite: report.conversionRuntime.sqlite,
    })).digest('hex'),
    report.conversionRuntime.authoritySha256,
  );
  assert.equal(report.scope.concurrentSameUidWriters, 'excluded-by-quiescent-operator-contract');
  for (const suffix of ['-wal', '-shm', '-journal']) {
    assert.equal(fs.existsSync(`${output}${suffix}`), false);
  }

  const database = new Database(output, { readonly: true, fileMustExist: true });
  assert.deepEqual(database.prepare('SELECT event_id, value FROM household_events ORDER BY event_id').all(), [
    { event_id: 1, value: 'main-page' },
    { event_id: 2, value: 'committed-only-in-wal' },
  ]);
  assert.equal(database.pragma('journal_mode', { simple: true }), 'delete');
  assert.deepEqual(database.pragma('integrity_check'), [{ integrity_check: 'ok' }]);
  assert.deepEqual(database.pragma('foreign_key_check'), []);
  database.close();
});

test('rejects counterfeit Node runtime before interpreting evidence', () => {
  const fixture = createWalFixture('counterfeit-node');
  closeFixture(fixture);
  const runtimeDirectory = privateDirectory('counterfeit-node-runtime');
  const counterfeitNode = path.join(runtimeDirectory, 'node');
  fs.copyFileSync(process.execPath, counterfeitNode, fs.constants.COPYFILE_FICLONE_EXCL);
  fs.chmodSync(counterfeitNode, 0o500);
  fs.writeFileSync(path.join(fixture.sourceDirectory, 'unexpected-before-runtime'), 'invalid', {
    flag: 'wx', mode: 0o600,
  });
  const result = runSnapshot(fixture, {
    execPath: counterfeitNode,
    env: { EVA_ISOLATED_RESOURCE_GATE: '1' },
  });
  assertFailed(result, /conversion runtime.*Node|Node.*conversion runtime/u);
  assert.deepEqual(fs.readdirSync(fixture.targetDirectory), []);
});

test('rejects counterfeit better-sqlite3 JS, package metadata, and native addon before evidence', () => {
  const cases = [
    ['entry', 'lib/index.js', (filename) => fs.appendFileSync(filename, '\n// counterfeit\n')],
    ['package', 'package.json', (filename) => fs.appendFileSync(filename, '\n ')],
    ['addon', 'build/Release/better_sqlite3.node', (filename) => {
      fs.appendFileSync(filename, Buffer.from([0]));
    }],
  ];
  for (const [label, relativeFile, mutate] of cases) {
    const fixture = createWalFixture(`counterfeit-sqlite-${label}`);
    closeFixture(fixture);
    const copiedProgram = counterfeitProgram(label, relativeFile, mutate);
    fs.writeFileSync(path.join(fixture.sourceDirectory, 'unexpected-before-runtime'), 'invalid', {
      flag: 'wx', mode: 0o600,
    });
    const result = runSnapshot(fixture, { program: copiedProgram });
    assertFailed(result, /conversion runtime.*better-sqlite3|better-sqlite3.*conversion runtime/u);
    assert.deepEqual(fs.readdirSync(fixture.targetDirectory), []);
  }
});

test('non-skipped deterministic release gate separates runtime ELF authority from build provenance', () => {
  const result = spawnSync(process.execPath, [program, '--verify-publisher-release'], {
    cwd: repositoryRoot,
    encoding: 'utf8',
    env: { PATH: '/usr/bin:/bin', NODE_OPTIONS: '', ...isolatedRuntimeMarkerEnvironment() },
    timeout: 30_000,
  });
  assert.equal(result.status, 0, result.stderr);
  const release = JSON.parse(result.stdout);
  assert.equal(release.contract, 'eva.quiescent-publisher-release.v1');
  assert.equal(release.runtimeByteAuthority.sourceSha256, publisherSourceSha256);
  assert.equal(release.runtimeByteAuthority.binarySha256, publisherBinarySha256);
  assert.equal(release.runtimeByteAuthority.elf.type, 'ET_EXEC');
  assert.equal(release.runtimeByteAuthority.elf.machine, 'x86-64');
  assert.equal(release.runtimeByteAuthority.elf.hasInterpreter, false);
  assert.equal(release.runtimeByteAuthority.elf.hasDynamicSegment, false);
  assert.equal(
    release.buildProvenance.authority,
    'content-addressed-record-plus-independent-pinned-toolchain-rebuild',
  );
  assert.equal(release.buildProvenance.buildA.sha256, publisherBinarySha256);
  assert.equal(release.buildProvenance.buildB.sha256, publisherBinarySha256);
  assert.equal(release.releaseRecordSha256, publisherReleaseRecordSha256);
  const independentlyAddressed = crypto.createHash('sha256')
    .update(canonicalJson(release.releaseRecord)).digest('hex');
  assert.equal(release.releaseRecordSha256, independentlyAddressed);
  assert.equal(release.releaseRecordContentAddress, `sha256:${independentlyAddressed}`);
});

test('opt-in release build performs two independent builds with the exact pinned toolchain', () => {
  const compiler = '/usr/bin/x86_64-linux-gnu-gcc-13';
  const result = spawnSync(process.execPath, [program, '--reproduce-publisher-release'], {
    cwd: repositoryRoot,
    encoding: 'utf8',
    env: { PATH: '/usr/bin:/bin', NODE_OPTIONS: '', ...isolatedRuntimeMarkerEnvironment() },
    timeout: 30_000,
  });
  if (!fs.existsSync(compiler)) {
    assertFailed(result, /exact pinned publisher toolchain is unavailable/u);
    return;
  }
  assert.equal(result.status, 0, result.stderr);
  const reproduced = JSON.parse(result.stdout);
  assert.equal(reproduced.contract, 'eva.quiescent-publisher-reproducible-build.v1');
  assert.equal(reproduced.toolchain.compiler.path, compiler);
  assert.match(reproduced.toolchain.compiler.sha256, /^[a-f0-9]{64}$/u);
  assert.equal(reproduced.buildA.sha256, publisherBinarySha256);
  assert.equal(reproduced.buildB.sha256, publisherBinarySha256);
  assert.equal(reproduced.embeddedBinarySha256, publisherBinarySha256);
  assert.equal(reproduced.byteIdentical, true);
  assert.equal(reproduced.releaseRecordSha256, publisherReleaseRecordSha256);
});

test('acquires Eva production FTS5, rowid identities, WITHOUT ROWID, view, and WAL-only memory', () => {
  const fixture = createEvaProductionFtsWalFixture();
  const sourceBefore = trioEvidence(fixture.sourceDirectory);
  const result = runSnapshot(fixture);
  closeFixture(fixture);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(trioEvidence(fixture.sourceDirectory), sourceBefore);
  const report = JSON.parse(result.stdout);
  assert.equal(report.source.schema.fts5.contract, 'eva.memories-fts5-external-content.v1');
  assert.deepEqual(report.source.schema.fts5.shadowTables, [
    'memories_fts_config',
    'memories_fts_data',
    'memories_fts_docsize',
    'memories_fts_idx',
  ]);
  assert.equal(report.source.schema.tables.memories_fts.tableListType, 'virtual');
  assert.equal(
    report.source.schema.tables.memories_fts.rowIdentity.kind,
    'fts5-external-content-rowid',
  );
  assert.equal(report.source.schema.tables.memories_fts.rowIdentity.alias, 'rowid');
  assert.equal(report.source.schema.tables.memories_fts_data.tableListType, 'shadow');
  assert.equal(report.source.schema.tables.visible_rowid_alias.rowIdentity.alias, '_rowid_');
  assert.equal(report.source.schema.tables.double_visible_rowid_alias.rowIdentity.alias, 'oid');
  assert.equal(
    report.source.schema.tables.household_preferences.rowIdentity.kind,
    'without-rowid-primary-key',
  );
  assert.equal(report.source.schema.views.active_memories.tableListType, 'view');
  assert.deepEqual(report.output.schema.tables, report.source.schema.tables);
  assert.deepEqual(report.output.schema.tableList, report.source.schema.tableList);

  const output = path.join(fixture.targetDirectory, fixture.outputName);
  const database = new Database(output, { readonly: true, fileMustExist: true });
  assert.deepEqual(database.prepare(`
    SELECT rowid, content FROM memories_fts
    WHERE memories_fts MATCH 'gezinscontinuiteit'
  `).all(), [{ rowid: 2, content: 'gezinscontinuiteit alleen committed in WAL' }]);
  assert.deepEqual(database.prepare(`
    SELECT _rowid_ AS intrinsic_rowid, rowid, value
    FROM visible_rowid_alias ORDER BY intrinsic_rowid
  `).all(), [
    { intrinsic_rowid: 1, rowid: 'zichtbaar-a', value: 'zelfde-waarde' },
    { intrinsic_rowid: 2, rowid: 'zichtbaar-b', value: 'zelfde-waarde' },
  ]);
  assert.deepEqual(database.prepare(`
    SELECT kind, slot, value FROM household_preferences ORDER BY kind, slot
  `).all(), [
    { kind: 'muziek', slot: 2, value: 'rustig' },
    { kind: 'ontbijt', slot: 1, value: 'havermout' },
  ]);
  assert.equal(database.prepare(`
    SELECT count(*) FROM active_memories
    WHERE content='gezinscontinuiteit alleen committed in WAL'
  `).pluck().get(), 1);
  database.close();
});

test('fails closed when all intrinsic rowid aliases are shadowed', () => {
  const sourceDirectory = privateDirectory('all-rowid-shadow-source');
  const targetDirectory = privateDirectory('all-rowid-shadow-target');
  const source = path.join(sourceDirectory, 'eva.db');
  const database = new Database(source);
  database.exec(`
    CREATE TABLE all_rowid_aliases_shadowed (
      rowid TEXT,
      _rowid_ TEXT,
      oid TEXT,
      value TEXT
    );
    INSERT INTO all_rowid_aliases_shadowed VALUES ('a', 'b', 'c', 'd');
  `);
  database.close();
  fs.chmodSync(source, 0o600);
  const result = runSnapshot({ sourceDirectory, targetDirectory, outputName: 'snapshot.db' });
  assertFailed(result, /no accessible intrinsic rowid alias/u);
  assert.deepEqual(fs.readdirSync(targetDirectory), []);
});

test('rejects every extra trigger on the memories FTS content table', () => {
  const fixture = createEvaProductionFtsWalFixture('extra-fts-trigger');
  fixture.database.exec(`
    CREATE TRIGGER memories_unreviewed_extra AFTER INSERT ON memories BEGIN
      SELECT new.content;
    END;
  `);
  for (const suffix of ['', '-wal', '-shm']) {
    const destination = path.join(fixture.sourceDirectory, `eva.db${suffix}`);
    fs.copyFileSync(`${fixture.originPath}${suffix}`, destination);
    fs.chmodSync(destination, 0o600);
  }
  closeFixture(fixture);
  const result = runSnapshot(fixture);
  assertFailed(result, /exact Eva FTS5 trigger set|extra trigger/u);
  assert.deepEqual(fs.readdirSync(fixture.targetDirectory), []);
});

test('accepts a self-contained main database without optional WAL sidecars', () => {
  const sourceDirectory = privateDirectory('main-source');
  const targetDirectory = privateDirectory('main-target');
  const source = path.join(sourceDirectory, 'eva.db');
  const database = new Database(source);
  database.exec('CREATE TABLE x(value TEXT NOT NULL); INSERT INTO x VALUES (\'self-contained\');');
  database.close();
  fs.chmodSync(source, 0o600);
  const fixture = { sourceDirectory, targetDirectory, outputName: 'snapshot.db' };
  const before = trioEvidence(sourceDirectory);
  const result = runSnapshot(fixture);
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.sqlite.acquisition.checkpoint.executed, false);
  assert.equal(report.sqlite.acquisition.checkpoint.scratchShm.observedAfterSqliteOpen, false);
  assert.deepEqual(trioEvidence(sourceDirectory), before);
  const output = new Database(path.join(targetDirectory, fixture.outputName), { readonly: true });
  assert.equal(output.prepare('SELECT value FROM x').pluck().get(), 'self-contained');
  output.close();
});

test('refuses incomplete, stale, swapped, and unexpected source sidecar sets', () => {
  {
    const fixture = createWalFixture('missing');
    fixture.database.close();
    fs.unlinkSync(path.join(fixture.sourceDirectory, 'eva.db-shm'));
    assertFailed(runSnapshot(fixture), /WAL and SHM must be supplied together/u);
  }
  {
    const fixture = createWalFixture('stale');
    const staleShm = fs.readFileSync(path.join(fixture.sourceDirectory, 'eva.db-shm'));
    fixture.database.exec("INSERT INTO household_events VALUES (3, 'later');");
    fs.copyFileSync(`${fixture.originPath}-wal`, path.join(fixture.sourceDirectory, 'eva.db-wal'));
    fs.writeFileSync(path.join(fixture.sourceDirectory, 'eva.db-shm'), staleShm);
    for (const name of fs.readdirSync(fixture.sourceDirectory)) {
      fs.chmodSync(path.join(fixture.sourceDirectory, name), 0o600);
    }
    fixture.database.close();
    assertFailed(runSnapshot(fixture), /stale SHM|frame authority/u);
  }
  {
    const fixture = createWalFixture('corrupt-frame');
    closeFixture(fixture);
    const wal = path.join(fixture.sourceDirectory, 'eva.db-wal');
    const descriptor = fs.openSync(wal, fs.constants.O_RDWR | fs.constants.O_NOFOLLOW);
    const byte = Buffer.alloc(1);
    fs.readSync(descriptor, byte, 0, 1, 64);
    byte[0] ^= 0x80;
    fs.writeSync(descriptor, byte, 0, 1, 64);
    fs.closeSync(descriptor);
    assertFailed(runSnapshot(fixture), /WAL frame \d+ checksum is invalid/u);
  }
  {
    const fixture = createWalFixture('corrupt-shm');
    closeFixture(fixture);
    const shm = path.join(fixture.sourceDirectory, 'eva.db-shm');
    const descriptor = fs.openSync(shm, fs.constants.O_RDWR | fs.constants.O_NOFOLLOW);
    const byte = Buffer.alloc(1);
    fs.readSync(descriptor, byte, 0, 1, 40);
    byte[0] ^= 0x80;
    fs.writeSync(descriptor, byte, 0, 1, 40);
    fs.closeSync(descriptor);
    assertFailed(runSnapshot(fixture), /SHM header copies disagree|SHM header checksum/u);
  }
  {
    const first = createWalFixture('swap-a');
    const second = createWalFixture('swap-b');
    second.database.exec("INSERT INTO household_events VALUES (9, 'other-wal');");
    fs.copyFileSync(`${second.originPath}-wal`, path.join(first.sourceDirectory, 'eva.db-wal'));
    fs.chmodSync(path.join(first.sourceDirectory, 'eva.db-wal'), 0o600);
    closeFixture(first);
    closeFixture(second);
    assertFailed(runSnapshot(first), /WAL\/SHM salt mismatch|swapped sidecar/u);
  }
  {
    const fixture = createWalFixture('unexpected');
    closeFixture(fixture);
    fs.writeFileSync(path.join(fixture.sourceDirectory, 'eva.db-journal'), 'not allowed', {
      mode: 0o600,
    });
    assertFailed(runSnapshot(fixture), /unexpected source entr/u);
  }
});

test('refuses symlinks, hardlinks, unsafe permissions, reused directories, and existing targets', () => {
  {
    const fixture = createWalFixture('symlink');
    closeFixture(fixture);
    const main = path.join(fixture.sourceDirectory, 'eva.db');
    const held = path.join(fixture.sourceDirectory, 'held.db');
    fs.renameSync(main, held);
    fs.symlinkSync(held, main);
    assertFailed(runSnapshot(fixture), /unexpected source entr|non-symlink|regular/u);
  }
  {
    const fixture = createWalFixture('hardlink');
    closeFixture(fixture);
    fs.linkSync(path.join(fixture.sourceDirectory, 'eva.db'), path.join(privateDirectory('alias'), 'alias.db'));
    assertFailed(runSnapshot(fixture), /hardlink/u);
  }
  {
    const fixture = createWalFixture('mode');
    closeFixture(fixture);
    fs.chmodSync(path.join(fixture.sourceDirectory, 'eva.db'), 0o640);
    assertFailed(runSnapshot(fixture), /private|group\/world/u);
  }
  {
    const directory = privateDirectory('same-directory');
    const database = new Database(path.join(directory, 'eva.db'));
    database.exec('CREATE TABLE x(a);');
    database.close();
    fs.chmodSync(path.join(directory, 'eva.db'), 0o600);
    assertFailed(runSnapshot({
      sourceDirectory: directory,
      targetDirectory: directory,
      outputName: 'out.db',
    }), /source and target directories must differ/u);
  }
  {
    const fixture = createWalFixture('exists');
    closeFixture(fixture);
    fs.writeFileSync(path.join(fixture.targetDirectory, fixture.outputName), 'counterfeit', {
      mode: 0o600,
    });
    assertFailed(runSnapshot(fixture), /target directory must start empty|already exists/u);
    assert.equal(fs.readFileSync(path.join(fixture.targetDirectory, fixture.outputName), 'utf8'), 'counterfeit');
  }
});

test('detects a pinned source pathname swap and publishes nothing', async () => {
  const fixture = createWalFixture('source-race');
  closeFixture(fixture);
  const hook = path.join(privateDirectory('source-race-hook'), 'ready');
  const child = spawnWithHook(fixture, 'EVA_QUIESCENT_TEST_HOOK_SOURCE_PINNED', hook);
  const completion = collectChild(child);
  await waitForFile(hook, child);
  const main = path.join(fixture.sourceDirectory, 'eva.db');
  fs.renameSync(main, path.join(fixture.sourceDirectory, 'original.db'));
  fs.writeFileSync(main, 'counterfeit', { mode: 0o600 });
  fs.unlinkSync(hook);
  const result = await completion;
  assertFailed(result, /source path identity changed|unexpected source entr/u);
  assert.equal(fs.existsSync(path.join(fixture.targetDirectory, fixture.outputName)), false);
});

test('detects a pinned source sidecar swap and publishes nothing', async () => {
  const fixture = createWalFixture('sidecar-race');
  closeFixture(fixture);
  const hook = path.join(privateDirectory('sidecar-race-hook'), 'ready');
  const child = spawnWithHook(fixture, 'EVA_QUIESCENT_TEST_HOOK_SOURCE_PINNED', hook);
  const completion = collectChild(child);
  await waitForFile(hook, child);
  const wal = path.join(fixture.sourceDirectory, 'eva.db-wal');
  fs.renameSync(wal, path.join(fixture.sourceDirectory, 'original.wal'));
  fs.writeFileSync(wal, 'counterfeit', { mode: 0o600 });
  fs.unlinkSync(hook);
  const result = await completion;
  assertFailed(result, /eva\.db-wal source path identity changed|unexpected source entr/u);
  assert.equal(fs.existsSync(path.join(fixture.targetDirectory, fixture.outputName)), false);
});

test('atomic no-clobber publication never overwrites a racing target', async () => {
  const fixture = createWalFixture('target-race');
  closeFixture(fixture);
  const hook = path.join(privateDirectory('target-race-hook'), 'ready');
  const child = spawnWithHook(fixture, 'EVA_QUIESCENT_TEST_HOOK_BEFORE_LINK', hook);
  const completion = collectChild(child);
  await waitForFile(hook, child);
  const target = path.join(fixture.targetDirectory, fixture.outputName);
  fs.writeFileSync(target, 'counterfeit', { flag: 'wx', mode: 0o600 });
  fs.unlinkSync(hook);
  const result = await completion;
  assertFailed(result, /atomic publication refused existing target/u);
  assert.equal(fs.readFileSync(target, 'utf8'), 'counterfeit');
});

test('detects a target-directory pathname swap before publication', async () => {
  const fixture = createWalFixture('target-directory-race');
  closeFixture(fixture);
  const hook = path.join(privateDirectory('target-directory-race-hook'), 'ready');
  const child = spawnWithHook(fixture, 'EVA_QUIESCENT_TEST_HOOK_BEFORE_LINK', hook);
  const completion = collectChild(child);
  await waitForFile(hook, child);
  const original = `${fixture.targetDirectory}-original`;
  fs.renameSync(fixture.targetDirectory, original);
  fs.mkdirSync(fixture.targetDirectory, { mode: 0o700 });
  fs.unlinkSync(hook);
  const result = await completion;
  assertFailed(result, /target directory path authority changed/u);
  assert.equal(fs.existsSync(path.join(fixture.targetDirectory, fixture.outputName)), false);
  assert.equal(fs.existsSync(path.join(original, fixture.outputName)), false);
  temporaryRoots.push(original);
});

test('crash immediately before publication leaves final absent', async () => {
  const fixture = createWalFixture('crash-before');
  closeFixture(fixture);
  const hook = path.join(privateDirectory('crash-before-hook'), 'ready');
  const child = spawnWithHook(fixture, 'EVA_QUIESCENT_TEST_HOOK_BEFORE_LINK', hook);
  const completion = collectChild(child);
  const brokerPid = Number(await waitForFile(hook, child));
  assert.ok(Number.isSafeInteger(brokerPid) && brokerPid > 1);
  process.kill(brokerPid, 'SIGKILL');
  const result = await completion;
  assert.notEqual(result.status, 0);
  assert.equal(fs.existsSync(path.join(fixture.targetDirectory, fixture.outputName)), false);
});

test('crash after linkat but before parent fsync fails operation and leaves final unclassified', async () => {
  const fixture = createWalFixture('crash-linked-unclassified');
  closeFixture(fixture);
  const hook = path.join(privateDirectory('crash-linked-unclassified-hook'), 'ready');
  const child = spawnWithHook(
    fixture, 'EVA_QUIESCENT_TEST_HOOK_AFTER_LINK_BEFORE_PARENT_FSYNC', hook,
  );
  const completion = collectChild(child);
  const helperPid = Number(await waitForFile(hook, child));
  assert.ok(Number.isSafeInteger(helperPid) && helperPid > 1);
  process.kill(helperPid, 'SIGKILL');
  fs.unlinkSync(hook);
  const result = await completion;
  assert.notEqual(result.status, 0);
  assert.equal(result.stdout, '');
  const target = path.join(fixture.targetDirectory, fixture.outputName);
  assert.equal(fs.existsSync(target), true);
  const database = new Database(target, { readonly: true, fileMustExist: true });
  assert.equal(database.prepare("SELECT count(*) FROM household_events WHERE value='committed-only-in-wal'").pluck().get(), 1);
  database.close();
});

test('crash after durable-link handshake leaves a complete SQLite final classified durable', async () => {
  const fixture = createWalFixture('crash-after');
  closeFixture(fixture);
  const hook = path.join(privateDirectory('crash-after-hook'), 'ready');
  const child = spawnWithHook(fixture, 'EVA_QUIESCENT_TEST_HOOK_AFTER_DURABLE_LINK', hook);
  const completion = collectChild(child);
  const brokerPid = Number(await waitForFile(hook, child));
  assert.ok(Number.isSafeInteger(brokerPid) && brokerPid > 1);
  process.kill(brokerPid, 'SIGKILL');
  const result = await completion;
  assert.notEqual(result.status, 0);
  const target = path.join(fixture.targetDirectory, fixture.outputName);
  assert.equal(fs.existsSync(target), true);
  const database = new Database(target, { readonly: true, fileMustExist: true });
  assert.equal(database.prepare("SELECT count(*) FROM household_events WHERE value='committed-only-in-wal'").pluck().get(), 1);
  assert.deepEqual(database.pragma('integrity_check'), [{ integrity_check: 'ok' }]);
  assert.equal(fs.statSync(target, { bigint: true }).nlink, 1n);
  database.close();
});

test('rejects a directly invoked internal broker despite caller-forged environment credentials', () => {
  const fixture = createWalFixture('broker-forgery');
  closeFixture(fixture);
  const token = 'a'.repeat(64);
  const options = {
    sourceDirectory: fixture.sourceDirectory,
    targetDirectory: fixture.targetDirectory,
    outputName: fixture.outputName,
  };
  const forgedCapability = canonicalJson({
    contract: 'eva.quiescent-broker-pipe-capability.v1',
    parentPid: process.pid,
    nonce: token,
    argumentsSha256: crypto.createHash('sha256').update(canonicalJson(options)).digest('hex'),
  });
  const result = spawnSync(process.execPath, [
    program,
    '--internal-broker',
    '--source-dir', fixture.sourceDirectory,
    '--target-dir', fixture.targetDirectory,
    '--output', fixture.outputName,
  ], {
    cwd: repositoryRoot,
    encoding: 'utf8',
    env: {
      EVA_QUIESCENT_BROKER_TOKEN: token,
      EVA_QUIESCENT_BROKER_PARENT_PID: String(process.pid),
      ...isolatedRuntimeMarkerEnvironment(),
    },
    input: forgedCapability,
    timeout: 30_000,
  });
  assertFailed(result, /internal broker mode is unavailable/u);
  assert.deepEqual(fs.readdirSync(fixture.targetDirectory), []);
});

test('rejects an arbitrary dummy-script wrapper with a self-made broker pipe capability', () => {
  const fixture = createWalFixture('broker-dummy-wrapper-forgery');
  closeFixture(fixture);
  const wrapper = String.raw`
    const crypto = require('node:crypto');
    const { spawnSync } = require('node:child_process');
    const [program, sourceDirectory, targetDirectory, outputName] = process.argv.slice(1);
    const options = { outputName, sourceDirectory, targetDirectory };
    const argumentsSha256 = crypto.createHash('sha256')
      .update(JSON.stringify(options)).digest('hex');
    const capability = JSON.stringify({
      contract: 'eva.quiescent-broker-pipe-capability.v1',
      parentPid: process.pid,
      nonce: 'b'.repeat(64),
      argumentsSha256,
    });
    const runtimeEnvironment = process.env.EVA_ISOLATED_RESOURCE_GATE === '1'
      ? { EVA_ISOLATED_RESOURCE_GATE: '1' } : {};
    const child = spawnSync(process.execPath, [
      program, '--internal-broker',
      '--source-dir', sourceDirectory,
      '--target-dir', targetDirectory,
      '--output', outputName,
    ], { encoding: 'utf8', input: capability, env: runtimeEnvironment, timeout: 30000 });
    process.stdout.write(child.stdout || '');
    process.stderr.write(child.stderr || '');
    process.exit(child.status == null ? 1 : child.status);
  `;
  const result = spawnSync(process.execPath, [
    '-e', wrapper, program, fixture.sourceDirectory, fixture.targetDirectory, fixture.outputName,
  ], {
    cwd: repositoryRoot,
    encoding: 'utf8',
    env: isolatedRuntimeMarkerEnvironment(),
    timeout: 30_000,
  });
  assertFailed(result, /invalid internal broker invocation|broker mode is unavailable/u);
  assert.deepEqual(fs.readdirSync(fixture.targetDirectory), []);
});

test('documents inherited direct environment without claiming pre-Node hardening', () => {
  const fixture = createWalFixture('environment');
  closeFixture(fixture);
  const secret = `secret-${crypto.randomUUID()}`;
  const result = runSnapshot(fixture, {
    env: {
      TMPDIR: `/tmp/${secret}`,
      TEMP: `/tmp/${secret}`,
      TMP: `/tmp/${secret}`,
      EVA_UNRELATED_SECRET: secret,
      NODE_OPTIONS: '',
    },
  });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.scope.preNodeEnvironment, 'trusted-operator-boundary');
  assert.equal(report.scope.preNodeHardeningClaim, false);
  assert.match(report.scope.preNodeRationale, /NODE_OPTIONS/u);
  assert.equal(report.scope.brokerMode, 'disabled-no-internal-acquisition-entrypoint');
  assert.equal(report.scope.processEnvironment, 'inherited-trusted-operator-boundary');
  assert.doesNotMatch(result.stdout, new RegExp(secret, 'u'));
  assert.doesNotMatch(result.stderr, new RegExp(secret, 'u'));
  assert.equal(fs.existsSync(`/tmp/${secret}`), false);
});
