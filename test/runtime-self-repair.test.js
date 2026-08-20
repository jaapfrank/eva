import './isolated-data-dir.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import {
  createRuntimeController,
  registerRuntimePrincipalValidator,
  __runtimeControllerTest,
} from '../src/mind/runtimecontroller.js';
import { __maintainerTest } from '../deploy/eva-runtime-maintainer.mjs';
import {
  PROTOCOL, ROOT_GATE_NAMES, attestRelease, createGitTrackedFiles,
  dependencyTreeSha256, sha256,
} from '../deploy/eva-runtime-repair-protocol.mjs';

const TEST_PRINCIPALS = new WeakMap();
function authenticatedContext(principal, fields = {}) {
  const value = Object.freeze({ ...fields }); TEST_PRINCIPALS.set(value, principal); return value;
}
const testPrincipalValidator = (_source, context) => TEST_PRINCIPALS.get(context?.messageContext) || null;
for (const source of ['telegram', 'autonomy']) {
  registerRuntimePrincipalValidator(source, (context) => testPrincipalValidator(source, context));
}
const testRuntimeController = (options = {}) => createRuntimeController({
  maintainerConfig: () => null,
  ...options,
});

function fixture(t) {
  const directory = fs.mkdtempSync('/tmp/eva-runtime-repair-');
  const root = path.join(directory, 'repo');
  const stateDir = path.join(directory, 'state');
  const maintainer = path.join(directory, 'maintainer');
  fs.mkdirSync(path.join(root, 'src'), { recursive: true });
  fs.mkdirSync(path.join(root, 'node_modules', 'fixture-dependency'), { recursive: true });
  fs.writeFileSync(path.join(root, 'node_modules', 'fixture-dependency', 'index.js'), 'export default true;\n', { mode: 0o444 });
  fs.chmodSync(path.join(root, 'node_modules'), 0o555);
  fs.chmodSync(path.join(root, 'node_modules', 'fixture-dependency'), 0o555);
  for (const name of ['inbox', 'processing', 'quarantine', 'responses', 'status']) {
    fs.mkdirSync(path.join(maintainer, name), { recursive: true });
  }
  fs.writeFileSync(path.join(root, 'src/foo.js'), 'export const answer = 1;\n');
  fs.writeFileSync(path.join(root, 'package.json'), '{"type":"module"}\n');
  fs.writeFileSync(path.join(root, 'package-lock.json'), '{}\n');
  execFileSync('/usr/bin/git', ['init', '-q', root]);
  execFileSync('/usr/bin/git', ['-C', root, 'add', 'src/foo.js', 'package.json', 'package-lock.json']);
  t.after(() => {
    try {
      fs.chmodSync(path.join(root, 'node_modules'), 0o755);
      fs.chmodSync(path.join(root, 'node_modules', 'fixture-dependency'), 0o755);
    } catch { /* fixture kan bewust al verwijderd zijn */ }
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return { root, stateDir, maintainer };
}

function helperFixture(t) {
  const base = fixture(t);
  fs.mkdirSync(path.join(base.maintainer, 'transactions'), { recursive: true });
  fs.mkdirSync(path.join(base.maintainer, 'candidates'), { recursive: true });
  const config = {
    root: base.root, state_dir: base.stateDir,
    maintainer_state_dir: base.maintainer, service_user: 'administrator',
    runtime_read_group: execFileSync('/usr/bin/id', ['-gn'], { encoding: 'utf8' }).trim(),
    tracked_manifest: path.join(base.maintainer, 'tracked-manifest.json'),
    release_manifest: path.join(base.maintainer, 'release-manifest.json'), production_grants: [],
  };
  const tracked = ['package-lock.json', 'package.json', 'src/foo.js'];
  fs.writeFileSync(config.tracked_manifest, `${JSON.stringify({
    protocol: __maintainerTest.PROTOCOL, root: base.root, files: tracked,
    issued_at: new Date().toISOString(),
  }, null, 2)}\n`, { mode: 0o640 });
  fs.chmodSync(config.tracked_manifest, 0o640);
  const before = Buffer.from('export const answer = 1;\n');
  const after = Buffer.from('export const answer = 2;\n');
  const replacements = new Map([['src/foo.js', after]]);
  const originals = new Map([['src/foo.js', before]]);
  const sourceStat = fs.statSync(path.join(base.root, 'src/foo.js'));
  const ownership = new Map([['src/foo.js', { uid: sourceStat.uid, gid: sourceStat.gid }]]);
  const txDir = path.join(base.stateDir, 'proposals', '12345678-1234-1234-1234-123456789abc');
  fs.mkdirSync(path.join(txDir, 'backup'), { recursive: true });
  fs.mkdirSync(path.join(txDir, 'candidate'), { recursive: true });
  fs.writeFileSync(path.join(txDir, 'backup/source'), before);
  fs.writeFileSync(path.join(txDir, 'candidate/source'), after);
  const patchBlob = Buffer.from(patch);
  fs.writeFileSync(path.join(txDir, 'change.diff'), patchBlob);
  const tx = {
    protocol: __maintainerTest.PROTOCOL,
    id: '12345678-1234-1234-1234-123456789abc', status: 'test_queued',
    root: base.root, expires_at: new Date(Date.now() + 60_000).toISOString(),
    base: {
      files: tracked,
      tracked_tree_sha256: __maintainerTest.treeSha(config, tracked),
      runtime_sha256: __maintainerTest.runtimeSha(config),
      dependency_tree_sha256: __maintainerTest.dependencyTreeSha256(
        path.join(base.root, 'node_modules'), { uid: process.getuid(), readonly: true },
      ),
    },
    candidate: {
      tracked_tree_sha256: __maintainerTest.treeSha(config, tracked, replacements),
      runtime_sha256: __maintainerTest.runtimeSha(config, replacements),
      dependency_tree_sha256: __maintainerTest.dependencyTreeSha256(
        path.join(base.root, 'node_modules'), { uid: process.getuid(), readonly: true },
      ),
    },
    patch_file: 'change.diff', patch_sha256: __maintainerTest.sha256(patchBlob),
    files: [{
      path: 'src/foo.js', mode: sourceStat.mode & 0o777,
      preimage_sha256: __maintainerTest.sha256(before),
      candidate_sha256: __maintainerTest.sha256(after),
      backup: 'backup/source', candidate: 'candidate/source',
    }],
  };
  fs.writeFileSync(path.join(txDir, 'proposal.json'), `${JSON.stringify(tx, null, 2)}\n`);
  return {
    ...base, config, tx, txDir, tracked,
    artifacts: {
      tracked, originals, replacements, ownership, patch: patchBlob,
      base: tx.base, candidate: tx.candidate, changed_lines: 2,
    },
  };
}

const rootRuntime = {
  rootUid: process.getuid(), assertBoundary: () => {},
  serviceIdentity: () => 123,
  assertAttestation: (config) => __maintainerTest.dependencyTreeSha256(
    path.join(config.root, 'node_modules'), { uid: process.getuid(), readonly: true },
  ),
  gateRunner: () => ({ status: 0, stdout: '', stderr: '' }),
  restart: (tx) => { tx.restart_requested_at = new Date().toISOString(); },
  liveAttempts: 1, sleep: async () => {},
};

const rootRequest = (value, operation, overrides = {}) => ({
  protocol: __maintainerTest.PROTOCOL,
  request_id: `${operation}-12345678-1234-1234-1234-123456789abc`,
  transaction_id: value.tx.id,
  proposal_path: `proposals/${value.tx.id}/proposal.json`,
  operation,
  origin: { turn_id_sha256: 'a'.repeat(64), input_sha256: 'b'.repeat(64) },
  ...overrides,
});

const passingGates = () => ROOT_GATE_NAMES.map((name) => ({
  name, exit_code: 0, duration_ms: 1,
}));

const canonicalTx = (value) => JSON.parse(fs.readFileSync(
  path.join(value.maintainer, 'transactions', value.tx.id, 'transaction.json'), 'utf8',
));
const canonicalStatus = (value) => JSON.parse(fs.readFileSync(
  path.join(value.maintainer, 'status', `${value.tx.id}.json`), 'utf8',
));

const patch = `--- a/src/foo.js
+++ b/src/foo.js
@@ -1 +1 @@
-export const answer = 1;
+export const answer = 2;
`;

test('runtime-diff accepteert alleen canonieke getrackte bronpaden', () => {
  assert.equal(__runtimeControllerTest.parseUnifiedDiff(patch).files[0].path, 'src/foo.js');
  for (const malicious of [
    patch.replaceAll('src/foo.js', '../src/foo.js'),
    patch.replaceAll('src/foo.js', 'src\\foo.js'),
    patch.replace('+++ b/src/foo.js', '+++ /dev/null'),
    patch.replace('+++ b/src/foo.js', '+++ b/src/bar.js'),
    patch.replace('+export const answer = 2;', '+import fs from "node:fs";'),
  ]) assert.throws(() => __runtimeControllerTest.parseUnifiedDiff(malicious));
});

test('autonomy-runtime faalt exact vóór git wanneer lokale repositorymetadata ontbreekt', async (t) => {
  const { root, stateDir } = fixture(t);
  fs.rmSync(path.join(root, '.git'), { recursive: true, force: true });
  let gitCalls = 0;
  const tracked = createGitTrackedFiles({
    execute: () => {
      gitCalls += 1;
      throw new Error('git mocht zonder lokale metadata nooit starten');
    },
  });
  const controller = testRuntimeController({ root, stateDir, tracked });
  const context = {
    mode: 'autonomy', channel: 'attention', turnId: 'turn-no-metadata',
    userText: 'Controleer je runtime.',
    messageContext: authenticatedContext('system:eva-autonomy'),
  };
  await assert.rejects(
    controller.manage({ bewerking: 'status' }, context),
    { message: 'tracked manifest unavailable: repository metadata absent' },
  );
  assert.equal(gitCalls, 0, 'git werd gestart vóór de lokale .git-precondition');
});

test('stage en beproef blijven async; productionstatus komt alleen uit rootstatus', async (t) => {
  const { root, stateDir, maintainer } = fixture(t);
  let rootStatus = null;
  const controller = testRuntimeController({
    root, stateDir,
    maintainerConfig: () => ({ maintainer_state_dir: maintainer }),
    readRootStatus: () => rootStatus,
  });
  const context = {
    mode: 'autonomy', channel: 'attention', turnId: 'turn-1', userText: 'repareer jezelf',
    messageContext: authenticatedContext('system:eva-autonomy'),
    turnUnderstanding: { kind: 'self_repair', actionIntent: 'execute' },
  };
  const status = await controller.manage({ bewerking: 'status' }, context);
  const staged = await controller.manage({
    bewerking: 'stage', basis_sha256: status.tracked_tree_sha256, patch,
  }, context);
  const queued = await controller.manage({
    bewerking: 'beproef', transactie_id: staged.transaction_id,
  }, context);
  assert.equal(queued.status, 'test_queued');
  assert.equal(queued.verified, false);
  const requests = fs.readdirSync(path.join(maintainer, 'inbox'));
  assert.equal(requests.length, 1);
  assert.equal(rootStatus, null);
  assert.deepEqual(fs.readdirSync(path.join(maintainer, 'responses')), []);
  assert.deepEqual(fs.readdirSync(path.join(maintainer, 'status')), []);
  const request = JSON.parse(fs.readFileSync(path.join(maintainer, 'inbox', requests[0]), 'utf8'));
  assert.equal(request.operation, 'test');
  assert.equal(request.actor, undefined);
  const proposal = JSON.parse(fs.readFileSync(
    path.join(stateDir, 'proposals', staged.transaction_id, 'proposal.json'), 'utf8',
  ));
  assert.equal(proposal.actor.mode, 'autonomy');
  const duplicate = await controller.manage({
    bewerking: 'beproef', transactie_id: staged.transaction_id,
  }, context);
  assert.equal(duplicate.duplicate, true);
  assert.equal(fs.readdirSync(path.join(maintainer, 'inbox')).length, 1);

  rootStatus = {
    protocol: __runtimeControllerTest.PROTOCOL,
    transaction_id: staged.transaction_id,
    status: 'root_tested',
    ok: true, changed: false, verified: true, needs_followup: true,
    live_verified: false, updated_at: new Date().toISOString(), gates: passingGates(),
    base_tree_sha256: staged.base_tree_sha256,
    candidate_tree_sha256: staged.candidate_tree_sha256,
    candidate_runtime_sha256: staged.candidate_runtime_sha256,
    dependency_tree_sha256: staged.dependency_tree_sha256,
    active_runtime: 'base', active_tree_sha256: status.tracked_tree_sha256,
    full_runtime_sha256: status.full_runtime_sha256,
    root_receipt_sha256: 'a'.repeat(64),
    reason: null, approval_binding: null,
  };
  const proven = await controller.manage({
    bewerking: 'status', transactie_id: staged.transaction_id,
  }, context);
  assert.equal(proven.status, 'root_tested');
  assert.equal(proven.verified, true);
  await assert.rejects(controller.manage({
    bewerking: 'pas_toe', transactie_id: staged.transaction_id,
    receipt_sha256: 'b'.repeat(64),
  }, context));
  const apply = await controller.manage({
    bewerking: 'pas_toe', transactie_id: staged.transaction_id,
    receipt_sha256: rootStatus.root_receipt_sha256,
  }, context);
  assert.equal(apply.status, 'apply_queued');
  assert.equal(apply.verified, false);

  rootStatus = {
    ...rootStatus, status: 'approval_required', verified: false,
    approval_binding: {
      operation: 'apply', transaction_id: staged.transaction_id,
      candidate_tree_sha256: staged.candidate_tree_sha256,
      root_receipt_sha256: rootStatus.root_receipt_sha256,
    },
  };
  const requeued = await controller.manage({
    bewerking: 'pas_toe', transactie_id: staged.transaction_id,
    receipt_sha256: rootStatus.root_receipt_sha256,
  }, context);
  assert.equal(requeued.status, 'apply_queued');
  assert.equal(fs.readdirSync(path.join(maintainer, 'inbox')).length, 3);

  rootStatus = {
    ...rootStatus, status: 'committed', verified: true, needs_followup: false,
    live_verified: true, active_runtime: 'candidate',
    active_tree_sha256: staged.candidate_tree_sha256,
    full_runtime_sha256: staged.candidate_runtime_sha256,
  };
  rootStatus.approval_binding = null;
  const committed = await controller.manage({
    bewerking: 'status', transactie_id: staged.transaction_id,
  }, context);
  assert.equal(committed.status, 'committed');
  assert.equal(committed.verified, true);
});

test('alle runtimeacties eisen een vertrouwde self-repairactor en blijven aan de eigenaar gebonden', async (t) => {
  const { root, stateDir, maintainer } = fixture(t);
  const controller = testRuntimeController({
    root, stateDir, maintainerConfig: () => ({ maintainer_state_dir: maintainer }),
  });
  const context = (principal, trusted = true) => ({
    mode: 'chat', channel: 'telegram', sender: 'Jaap', turnId: `turn-${principal}`,
    userText: 'repareer jezelf',
    messageContext: trusted ? authenticatedContext(principal)
      : { identityTrusted: true, authenticatedPrincipal: principal },
    turnUnderstanding: { kind: 'self_repair', actionIntent: 'execute' },
  });
  const owner = context('telegram:1234');
  const status = await controller.manage({ bewerking: 'status' }, owner);
  const staged = await controller.manage({
    bewerking: 'stage', basis_sha256: status.tracked_tree_sha256, patch,
  }, owner);

  for (const intruder of [context('', false), context('telegram:1234', false), context('telegram:5678')]) {
    await assert.rejects(controller.manage({
      bewerking: 'status', transactie_id: staged.transaction_id,
    }, intruder), { message: 'runtimebeheer niet geautoriseerd' });
    await assert.rejects(controller.manage({
      bewerking: 'beproef', transactie_id: staged.transaction_id,
    }, intruder), { message: 'runtimebeheer niet geautoriseerd' });
  }
  await assert.rejects(controller.manage({
    bewerking: 'status', transactie_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  }, context('telegram:5678')), { message: 'runtimebeheer niet geautoriseerd' });
  assert.deepEqual(fs.readdirSync(path.join(maintainer, 'inbox')), []);
  const queued = await controller.manage({
    bewerking: 'beproef', transactie_id: staged.transaction_id,
  }, { ...owner, turnId: 'latere-turn' });
  assert.equal(queued.status, 'test_queued');
});

test('herhaalde runtime-status is een pure read en ruimt ook oude proposalmappen niet op', async (t) => {
  const { root, stateDir } = fixture(t);
  const orphan = path.join(stateDir, 'proposals', 'oude-onvolledige-proposal');
  fs.mkdirSync(orphan, { recursive: true });
  const fingerprint = (base) => {
    const pending = [base]; const entries = [];
    while (pending.length) {
      const current = pending.pop(); const stat = fs.lstatSync(current);
      entries.push([path.relative(base, current), stat.isDirectory() ? 'd' : 'f',
        stat.mode & 0o777, stat.size, stat.mtimeMs,
        stat.isFile() ? sha256(fs.readFileSync(current)) : null]);
      if (stat.isDirectory()) for (const name of fs.readdirSync(current)) pending.push(path.join(current, name));
    }
    return entries.sort((left, right) => left[0].localeCompare(right[0]));
  };
  const controller = testRuntimeController({ root, stateDir, maintainerConfig: () => null });
  const context = { mode: 'chat', channel: 'telegram',
    messageContext: authenticatedContext('telegram:1234'),
    turnUnderstanding: { kind: 'self_repair' } };
  const beforeState = fingerprint(stateDir); const beforeRoot = fingerprint(root);
  assert.equal((await controller.manage({ bewerking: 'status' }, context)).changed, false);
  assert.equal((await controller.manage({ bewerking: 'status' }, context)).changed, false);
  assert.deepEqual(fingerprint(stateDir), beforeState); assert.deepEqual(fingerprint(root), beforeRoot);
  assert.equal(fs.existsSync(orphan), true);
});

test('whole-tree CAS stopt een stale kandidaat vóór een rootrequest', async (t) => {
  const { root, stateDir, maintainer } = fixture(t);
  const controller = testRuntimeController({
    root, stateDir, maintainerConfig: () => ({ maintainer_state_dir: maintainer }),
  });
  const context = { mode: 'autonomy', channel: 'attention',
    messageContext: authenticatedContext('system:eva-autonomy') };
  const status = await controller.manage({ bewerking: 'status' }, context);
  const staged = await controller.manage({
    bewerking: 'stage', basis_sha256: status.tracked_tree_sha256, patch,
  }, context);
  fs.writeFileSync(path.join(root, 'src/foo.js'), 'export const answer = 9;\n');
  await assert.rejects(controller.manage({
    bewerking: 'beproef', transactie_id: staged.transaction_id,
  }, context), /basisboom veranderde/u);
  assert.deepEqual(fs.readdirSync(path.join(maintainer, 'inbox')), []);
});

test('voice en synthetische kanalen kunnen geen runtimekandidaat stagen', async (t) => {
  const { root, stateDir } = fixture(t);
  const controller = testRuntimeController({ root, stateDir });
  const status = await controller.manage({ bewerking: 'status' }, {
    mode: 'autonomy', channel: 'attention', messageContext: authenticatedContext('system:eva-autonomy'),
  });
  for (const context of [
    { mode: 'chat', channel: 'voice', messageContext: { identityTrusted: true } },
    { mode: 'chat', channel: 'web', messageContext: { identityTrusted: true, syntheticTest: true } },
    { mode: 'chat', channel: 'web', messageContext: { identityTrusted: true, authenticatedPrincipal: 'telegram:1234' } },
    { mode: 'autonomy', channel: 'attention', messageContext: { authenticatedPrincipal: 'system:eva-autonomy' } },
  ]) await assert.rejects(controller.manage({
    bewerking: 'stage', basis_sha256: status.tracked_tree_sha256, patch,
  }, context));
});

test('beschermde runtimebron en high-risk toevoeging worden al bij stage geweigerd', async (t) => {
  const { root, stateDir } = fixture(t);
  fs.writeFileSync(path.join(root, 'src/config.js'), 'export const safe = true;\n');
  execFileSync('/usr/bin/git', ['-C', root, 'add', 'src/config.js']);
  const controller = testRuntimeController({ root, stateDir });
  const context = { mode: 'autonomy', channel: 'attention',
    messageContext: authenticatedContext('system:eva-autonomy') };
  const status = await controller.manage({ bewerking: 'status' }, context);
  const protectedPatch = patch
    .replaceAll('src/foo.js', 'src/config.js')
    .replace('export const answer = 1;', 'export const safe = true;')
    .replace('export const answer = 2;', 'export const safe = false;');
  await assert.rejects(controller.manage({
    bewerking: 'stage', basis_sha256: status.tracked_tree_sha256, patch: protectedPatch,
  }, context), /beschermde runtimebron/u);
  fs.mkdirSync(path.join(root, 'src/mind'), { recursive: true });
  fs.writeFileSync(path.join(root, 'src/mind/turnplan.js'), 'export const policy = true;\n', { flag: 'wx' });
  execFileSync('/usr/bin/git', ['-C', root, 'add', 'src/mind/turnplan.js']);
  await assert.rejects(controller.manage({
    bewerking: 'stage', basis_sha256: (await controller.manage({ bewerking: 'status' }, context)).tracked_tree_sha256,
    patch: patch.replaceAll('src/foo.js', 'src/mind/turnplan.js')
      .replace('export const answer = 1;', 'export const policy = true;')
      .replace('export const answer = 2;', 'export const policy = false;'),
  }, context), /beschermde runtimebron/u);
  await assert.rejects(controller.manage({
    bewerking: 'stage', basis_sha256: status.tracked_tree_sha256,
    patch: patch.replace('+export const answer = 2;', '+import fs from "node:fs";'),
  }, context), /verboden/u);
});

test('forgeable requestmetadata verleent nooit productieautoriteit', async () => {
  const tx = {
    id: '12345678-1234-1234-1234-123456789abc',
    candidate: { tracked_tree_sha256: 'a'.repeat(64) },
  };
  const forgedLegacyConfig = {
    autonomy_low_risk_grant: true,
    maintainer_principals: ['telegram:1'],
  };
  assert.equal(__maintainerTest.matchingProductionGrant(forgedLegacyConfig, tx, 'apply'), false);
  assert.equal(__maintainerTest.matchingProductionGrant({
    production_grants: [{
      operation: 'apply', transaction_id: tx.id,
      candidate_tree_sha256: tx.candidate.tracked_tree_sha256,
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    }],
  }, tx, 'apply'), true);
  assert.equal(__maintainerTest.matchingProductionGrant({
    production_grants: [{
      operation: 'apply', transaction_id: tx.id,
      candidate_tree_sha256: 'b'.repeat(64),
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    }],
  }, tx, 'apply'), false);
  assert.equal(__maintainerTest.matchingProductionGrant({
    production_grants: [{
      operation: 'apply', transaction_id: tx.id,
      candidate_tree_sha256: tx.candidate.tracked_tree_sha256,
      expires_at: new Date(Date.now() - 1).toISOString(),
    }],
  }, tx, 'apply'), false);
  await assert.rejects(__maintainerTest.processRequest({}, {
    protocol: __maintainerTest.PROTOCOL, request_id: tx.id, transaction_id: tx.id,
    operation: 'apply', requested_at: new Date(Date.now() - 10_000).toISOString(),
    expires_at: new Date(Date.now() - 1).toISOString(),
    not_before: new Date(Date.now() - 5_000).toISOString(),
    origin: { turn_id_sha256: 'a'.repeat(64), input_sha256: 'b'.repeat(64) },
  }), /expiry/u);
});

test('requestwachttijd houdt de rootlock vrij en controleert expiry opnieuw vóór dispatch', async () => {
  const start = 1_000_000; let current = start; let locked = false;
  const request = {
    protocol: PROTOCOL, request_id: '11111111-1111-1111-1111-111111111111',
    transaction_id: '22222222-2222-2222-2222-222222222222', operation: 'test',
    requested_at: new Date(start).toISOString(), not_before: new Date(start + 50).toISOString(),
    expires_at: new Date(start + 500).toISOString(),
    origin: { turn_id_sha256: 'a'.repeat(64), input_sha256: 'b'.repeat(64) },
  };
  const result = await __maintainerTest.processRequest({}, request, {
    now: () => current,
    sleep: async (milliseconds) => { assert.equal(locked, false); current += milliseconds; },
    serialize: async (action) => { locked = true; try { return await action(); } finally { locked = false; } },
    dispatchRequest: () => ({ ok: true }),
  });
  assert.equal(result.ok, true); assert.equal(locked, false);

  current = start; let serialized = false;
  await assert.rejects(__maintainerTest.processRequest({}, {
    ...request, not_before: new Date(start + 50).toISOString(),
    expires_at: new Date(start + 51).toISOString(),
  }, {
    now: () => current, sleep: async (milliseconds) => { current += milliseconds + 1; },
    serialize: async (action) => { serialized = true; return action(); },
    dispatchRequest: () => ({ ok: true }),
  }), /verliep/u);
  assert.equal(serialized, false);

  current = start; let dispatched = false;
  await assert.rejects(__maintainerTest.processRequest({}, request, {
    now: () => current, sleep: async (milliseconds) => { current += milliseconds; },
    serialize: async (action) => { current = start + 501; return action(); },
    dispatchRequest: () => { dispatched = true; return { ok: true }; },
  }), /geserialiseerde dispatch/u);
  assert.equal(dispatched, false);
});

test('helper weigert traversal en symlink-artifacts', (t) => {
  const directory = fs.mkdtempSync('/tmp/eva-maintainer-path-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  assert.throws(() => __maintainerTest.contained(directory, '../escape'));
  assert.throws(() => __maintainerTest.contained(directory, 'a\\b'));
  assert.throws(() => __maintainerTest.contained(directory, '/dev/null'));
  const target = path.join(directory, 'target');
  const link = path.join(directory, 'link');
  fs.writeFileSync(target, 'veilig');
  fs.symlinkSync(target, link);
  assert.throws(() => __maintainerTest.readRegular(link));
});

test('maintainerstatus is alleen leesbaar voor Eva en de inbox is niet world-writable', (t) => {
  const { maintainer } = fixture(t);
  const plan = new Map(__maintainerTest.maintainerDirectories({
    maintainer_state_dir: maintainer,
  }, 4242, 4343).map(([directory, mode, gid]) => [path.basename(directory), { mode, gid }]));
  assert.deepEqual(plan.get('responses'), { mode: 0o750, gid: 4242 });
  assert.deepEqual(plan.get('status'), { mode: 0o750, gid: 4242 });
  assert.deepEqual(plan.get('inbox'), { mode: 0o730, gid: 4242 });
  assert.deepEqual(plan.get('candidates'), { mode: 0o750, gid: 4343 });
  assert.deepEqual(plan.get('processing'), { mode: 0o700, gid: 0 });
  assert.deepEqual(plan.get('quarantine'), { mode: 0o700, gid: 0 });
  assert.equal(plan.get('inbox').mode & 0o007, 0);

  const installer = fs.readFileSync(path.resolve('deploy/install-eva-runtime-maintainer.sh'), 'utf8');
  assert.match(installer, /for CANDIDATE_READER in "\$SERVICE_USER" "\$TEST_GATE_USER"/u);
  assert.match(installer, /runuser[^\n]+test -x "\$MAINTAINER_STATE\/candidates"/u);
  assert.match(installer, /runuser[^\n]+test ! -w "\$MAINTAINER_STATE\/candidates"/u);
});

test('guard-release maintaineridentiteiten eisen exacte primaire en supplementaire groepen', () => {
  const config = { service_user: 'eva', runtime_read_group: 'eva-runtime-read' };
  const snapshot = {
    service: {
      user: 'eva', uid: 61_000, gid: 61_000, primaryGroup: 'eva',
      groups: [4343, 61_000], groupNames: ['eva', 'eva-runtime-read'],
      home: '/nonexistent', shell: '/usr/sbin/nologin',
    },
    gate: {
      user: 'eva-test-gate', uid: 61_001, gid: 61_001, primaryGroup: 'eva-test-gate',
      groups: [4343, 61_001], groupNames: ['eva-runtime-read', 'eva-test-gate'],
      home: '/nonexistent', shell: '/usr/sbin/nologin',
    },
    runtime: { group: 'eva-runtime-read', gid: 4343 },
  };
  const identity = __maintainerTest.validateRuntimeIdentities(config, snapshot);
  assert.deepEqual(identity.groups, [4343, 61_001]);
  assert.deepEqual(identity.groupNames, ['eva-runtime-read', 'eva-test-gate']);
  assert.equal(identity.serviceUid, 61_000);
  for (const mutation of [
    { ...snapshot, service: { ...snapshot.service, groups: [...snapshot.service.groups, 9999] } },
    { ...snapshot, gate: { ...snapshot.gate, groupNames: [...snapshot.gate.groupNames, 'wheel'] } },
    { ...snapshot, gate: { ...snapshot.gate, gid: 4343, groups: [4343] } },
    { ...snapshot, gate: { ...snapshot.gate, groups: snapshot.gate.groups.map(String) } },
    { ...snapshot, service: { ...snapshot.service, primaryGroup: 'wheel' } },
    { ...snapshot, service: { ...snapshot.service, home: '/var/lib/eva' } },
  ]) assert.throws(() => __maintainerTest.validateRuntimeIdentities(config, mutation), /identiteit/u);
});

test('roothelper claimt inbox atomair en blokkeert niet op fifo, symlink of map', async (t) => {
  const { maintainer } = fixture(t);
  const inbox = path.join(maintainer, 'inbox'); const processing = path.join(maintainer, 'processing');
  const config = { maintainer_state_dir: maintainer, service_user: 'administrator' };
  const names = [
    '11111111-1111-1111-1111-111111111111.json',
    '22222222-2222-2222-2222-222222222222.json',
    '33333333-3333-3333-3333-333333333333.json',
  ];
  execFileSync('/usr/bin/mkfifo', [path.join(inbox, names[0])]);
  fs.symlinkSync('/dev/null', path.join(inbox, names[1]));
  fs.mkdirSync(path.join(inbox, names[2])); fs.writeFileSync(path.join(inbox, names[2], 'payload'), 'x');
  const fifoInProcessing = path.join(processing, names[0]);
  const originalOpenSync = fs.openSync; let fifoFlags = null;
  fs.openSync = (file, flags, ...args) => {
    if (file === fifoInProcessing) {
      fifoFlags = flags;
      throw new Error('fixture onderschept de geclaimde fifo-open');
    }
    return originalOpenSync(file, flags, ...args);
  };
  try {
    await __maintainerTest.processRequests(config, { rootUid: process.getuid(), batchLimit: 16 });
  } finally { fs.openSync = originalOpenSync; }
  assert.notEqual(fifoFlags, null);
  assert.equal(fifoFlags & fs.constants.O_NONBLOCK, fs.constants.O_NONBLOCK);
  assert.deepEqual(fs.readdirSync(inbox), []); assert.deepEqual(fs.readdirSync(processing), []);
  const quarantined = fs.readdirSync(path.join(maintainer, 'quarantine'));
  assert.equal(quarantined.length, 1);
  assert.equal(fs.readFileSync(path.join(maintainer, 'quarantine', quarantined[0], 'payload'), 'utf8'), 'x');

  const atomicName = '44444444-4444-4444-4444-444444444444.json';
  const original = path.join(inbox, atomicName); fs.writeFileSync(original, 'eerste', { mode: 0o600 });
  const inode = fs.lstatSync(original).ino;
  assert.deepEqual(__maintainerTest.claimInbox(config, 1), [atomicName]);
  fs.writeFileSync(original, 'vervanging', { mode: 0o600 });
  assert.equal(fs.lstatSync(path.join(processing, atomicName)).ino, inode);
  assert.equal(fs.readFileSync(path.join(processing, atomicName), 'utf8'), 'eerste');
});

test('inboxspam wordt per run hard op zestien entries begrensd', async (t) => {
  const { maintainer } = fixture(t); const inbox = path.join(maintainer, 'inbox');
  const config = { maintainer_state_dir: maintainer, service_user: 'administrator' };
  for (let index = 0; index < 40; index += 1) fs.writeFileSync(path.join(inbox, `spam-${String(index).padStart(2, '0')}`), 'x');
  await __maintainerTest.processRequests(config, { rootUid: process.getuid(), batchLimit: 999 });
  assert.equal(fs.readdirSync(inbox).length, 24);
  assert.deepEqual(fs.readdirSync(path.join(maintainer, 'processing')), []);
});

test('restartbehoefte stopt de batch vóór een tweede request en geeft de lock eerst vrij', async (t) => {
  const { maintainer } = fixture(t); const inbox = path.join(maintainer, 'inbox');
  const ids = ['11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'];
  for (const id of ids) fs.writeFileSync(path.join(inbox, `${id}.json`), `${JSON.stringify({
    protocol: PROTOCOL, request_id: id, transaction_id: id,
  })}\n`, { mode: 0o600 });
  let calls = 0;
  const restart = await __maintainerTest.processRequests({
    maintainer_state_dir: maintainer, service_user: 'administrator',
  }, {
    requestProcessor: async () => { calls += 1; return { ok: true, restart_required: true }; },
  });
  assert.equal(restart, true); assert.equal(calls, 1);
  assert.equal(fs.readdirSync(inbox).length, 1);
  assert.deepEqual(fs.readdirSync(path.join(maintainer, 'processing')), []);
});

test('release-attestatie bindt de exacte byteset en installer ontsluit geen world- of gittoegang', (t) => {
  const directory = fs.mkdtempSync('/tmp/eva-release-attest-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const artifacts = ['helper.mjs', 'protocol.mjs', 'eva.service'].map((name) => path.join(directory, name));
  for (const [index, file] of artifacts.entries()) fs.writeFileSync(file, `artifact-${index}\n`, { mode: 0o440 });
  const manifest = path.join(directory, 'release.json');
  fs.writeFileSync(manifest, `${JSON.stringify({
    protocol: PROTOCOL, issued_at: new Date().toISOString(),
    files: Object.fromEntries(artifacts.map((file) => [file, sha256(fs.readFileSync(file))])),
  }, null, 2)}\n`, { mode: 0o640 }); fs.chmodSync(manifest, 0o640);
  assert.equal(attestRelease(manifest, artifacts, {
    uid: process.getuid(), gid: process.getgid(), mode: 0o640,
  }).protocol, PROTOCOL);
  const sourceRoot = path.join(directory, 'source');
  const sourceProtocol = path.join(sourceRoot, 'deploy/eva-runtime-repair-protocol.mjs');
  fs.mkdirSync(path.dirname(sourceProtocol), { recursive: true });
  fs.copyFileSync(artifacts[1], sourceProtocol); fs.chmodSync(sourceProtocol, 0o440);
  assert.equal(attestRelease(manifest, artifacts, {
    uid: process.getuid(), gid: process.getgid(), mode: 0o640,
    sourceRoot, protocolArtifact: artifacts[1],
  }).protocol, PROTOCOL);
  fs.chmodSync(sourceProtocol, 0o640); fs.writeFileSync(sourceProtocol, 'ander protocol\n');
  assert.throws(() => attestRelease(manifest, artifacts, {
    uid: process.getuid(), gid: process.getgid(), mode: 0o640,
    sourceRoot, protocolArtifact: artifacts[1],
  }), /serviceprotocol wijkt af/u);
  fs.chmodSync(artifacts[0], 0o640);
  fs.writeFileSync(artifacts[0], 'gewijzigd\n');
  assert.throws(() => attestRelease(manifest, artifacts, {
    uid: process.getuid(), gid: process.getgid(), mode: 0o640,
  }), /release-artifact wijkt af/u);

  const installer = fs.readFileSync(path.resolve('deploy/install-eva-runtime-maintainer.sh'), 'utf8');
  assert.doesNotMatch(installer, /chmod\s+(?:-R\s+)?(?:a\+|o\+)[rwxX]/u);
  assert.match(installer, /O_NOFOLLOW/u); assert.match(installer, /s\.uid!==0/u);
  assert.match(installer, /setfacl -bR/u); assert.match(installer, /chmod -R u=rX,g=rX,o=/u);
  assert.match(installer, /\.git["']?\nchmod -R u=rwX,go=/u);
  assert.match(installer, /tracked-manifest\.json/u); assert.match(installer, /release\.json/u);
});

test('guard-release rootgates maken per directe testgate een private strict-/tmp data-authority en ruimen die op', (t) => {
  const directory = fs.mkdtempSync('/tmp/eva-rootgate-static-spawn-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const config = { root: '/opt/eva', runtime_read_group: 'eva-runtime-read' };
  const requestId = '12345678-1234-1234-1234-123456789abc';
  const testFiles = [
    'test/runtime-self-repair.test.js',
    'scripts/run-isolated-tests.mjs',
    'scripts/run-isolated-tests.test.mjs',
  ];
  const releaseFixture = {
    contract: __maintainerTest.validateIsolatedTestRelease({
      release: fs.readFileSync('scripts/run-isolated-tests-release.json'),
      bootstrap: fs.readFileSync('scripts/run-isolated-tests-bootstrap'),
      bootstrapSource: fs.readFileSync('scripts/run-isolated-tests-bootstrap.c'),
      runner: fs.readFileSync('scripts/run-isolated-tests.mjs'),
    }),
    identity: Object.freeze({
      user: 'eva-test-gate', uid: 61_001, gid: 61_001,
      primaryGroup: 'eva-test-gate',
      groups: Object.freeze([process.getgid(), 61_001]),
      groupNames: Object.freeze(['eva-runtime-read', 'eva-test-gate']),
      home: '/nonexistent', shell: '/usr/sbin/nologin',
      runtimeReadGroup: 'eva-runtime-read', runtimeReadGid: process.getgid(),
      serviceUser: 'eva', serviceUid: 61_000, serviceGid: 61_000,
      servicePrimaryGroup: 'eva',
      serviceGroups: Object.freeze([process.getgid(), 61_000]),
      serviceGroupNames: Object.freeze(['eva', 'eva-runtime-read']),
      serviceHome: '/nonexistent', serviceShell: '/usr/sbin/nologin',
    }),
    rootUid: process.getuid(), runtimeReadGid: process.getgid(),
  };
  const candidate = path.join(directory, 'sealed-candidate');
  for (const relative of ['scripts', 'test', 'node_modules', 'data']) {
    fs.mkdirSync(path.join(candidate, relative), { recursive: true, mode: 0o750 });
  }
  for (const relative of ['', 'node_modules', 'data']) {
    fs.chmodSync(path.join(candidate, relative), 0o750);
  }
  for (const relative of [
    'scripts/run-isolated-tests-bootstrap', 'scripts/run-isolated-tests-bootstrap.c',
    'scripts/run-isolated-tests-release.json', 'scripts/run-isolated-tests.mjs',
  ]) {
    fs.copyFileSync(path.resolve(relative), path.join(candidate, relative));
    fs.chmodSync(path.join(candidate, relative), relative.endsWith('-bootstrap') ? 0o550 : 0o440);
  }
  fs.copyFileSync(path.resolve('scripts/run-isolated-tests.test.mjs'),
    path.join(candidate, 'scripts/run-isolated-tests.test.mjs'));
  fs.chmodSync(path.join(candidate, 'scripts/run-isolated-tests.test.mjs'), 0o440);
  const candidateStat = fs.lstatSync(candidate);
  assert.deepEqual({
    directory: candidateStat.isDirectory(), symlink: candidateStat.isSymbolicLink(),
    uid: candidateStat.uid, gid: candidateStat.gid, mode: candidateStat.mode & 0o777,
    canonical: fs.realpathSync(candidate),
  }, {
    directory: true, symlink: false, uid: releaseFixture.rootUid,
    gid: releaseFixture.runtimeReadGid, mode: 0o750, canonical: candidate,
  }, 'de fixture moet exact dezelfde sealed kandidaatrootauthority modelleren');
  const plan = __maintainerTest.rootGatePlan(
    config, candidate, requestId, { name: 'tests' }, testFiles, releaseFixture,
  );
  const tests = plan.candidate;
  const coverage = __maintainerTest.rootGateInvocation(
    config, '/sealed/candidate', requestId, { name: 'coverage' }, testFiles,
  );
  const nodeWasmFlag = '--disable-wasm-trap-handler';
  assert.throws(() => __maintainerTest.rootGatePlan(
    config, candidate, requestId, { name: 'tests' },
    ['test/runtime-self-repair.test.js'], releaseFixture,
  ), /mist getrackt OS-testbestand/u);
  for (const invocation of [tests, coverage]) {
    assert.equal(invocation.command, '/usr/bin/systemd-run');
    assert.equal(invocation.args.includes('--property=PrivateTmp=yes'), true);
    assert.equal(invocation.args.includes('--setenv=EVA_ROOT_GATE_DIRECT=1'), true);
    assert.equal(invocation.args.includes(`--setenv=NODE_OPTIONS=${nodeWasmFlag}`), true);
    assert.equal(invocation.args.some((argument) => argument.startsWith('--setenv=EVA_DATA_DIR=')), false);
    assert.equal(invocation.args.some((argument) => argument.includes('run-isolated-tests')), false);
    const evaluate = invocation.args.indexOf('--eval');
    assert.notEqual(evaluate, -1);
    const outerNode = invocation.args.indexOf('/usr/bin/node');
    assert.notEqual(outerNode, -1);
    assert.equal(invocation.args[outerNode + 1], nodeWasmFlag,
      'de outer transient rootGate-Node mist zijn expliciete vlag');
    assert.equal(invocation.args[evaluate + 1], __maintainerTest.ROOT_GATE_LAUNCHER);
    const childArguments = JSON.parse(invocation.args[evaluate + 2]);
    assert.equal(childArguments[0], nodeWasmFlag,
      'de door de launcher gestarte rootGate-Node mist zijn expliciete vlag');
    assert.equal(childArguments.includes('--test'), true);
    assert.equal(childArguments.some((argument) => argument.includes('run-isolated-tests')), false);
  }
  const coverageArguments = JSON.parse(coverage.args[coverage.args.indexOf('--eval') + 2]);
  const coverageNode = coverageArguments.indexOf('/usr/bin/node');
  assert.notEqual(coverageNode, -1);
  assert.equal(coverageArguments[coverageNode + 1], nodeWasmFlag,
    'de door c8 gestarte coverage-Node mist zijn expliciete vlag');
  assert.equal(__maintainerTest.ROOT_GATE_TEST_POLICY.contract,
    'EvaRootGateTestSelection/v2');
  assert.deepEqual(__maintainerTest.ROOT_GATE_TEST_POLICY.globs,
    ['test/*.test.js', 'test/*.test.mjs', 'scripts/*.test.mjs']);
  assert.equal(Object.isFrozen(__maintainerTest.ROOT_GATE_TEST_POLICY), true);
  assert.equal(plan.os.command, '/usr/bin/setpriv');
  assert.equal(plan.os.args.includes('/usr/bin/node'), false);
  assert.equal(plan.os.args.includes('npm'), false);
  assert.equal(plan.os.args.includes('/usr/bin/systemd-run'), false,
    'de statische bootstrap bezit zelf de enige resource-envelop');
  assert.equal(plan.os.args.includes('--inside-resource-unit'), false);
  for (const boundary of [
    '--reuid=61001', '--regid=61001', '--inh-caps=-all', '--ambient-caps=-all',
    '--bounding-set=-all', '--no-new-privs', '--pdeathsig=keep',
  ]) assert.equal(plan.os.args.includes(boundary), true, boundary);
  assert.equal(plan.os.args.includes(`--groups=${process.getgid()}`), true,
    'setpriv krijgt exact alleen de runtime-read supplementary group');
  assert.deepEqual(plan.os.args.slice(-2), [
    '/usr/local/libexec/eva/run-isolated-tests-bootstrap',
    'scripts/run-isolated-tests.test.mjs',
  ]);
  assert.equal(plan.os.options.cwd, candidate);
  assert.deepEqual(plan.os.options.env, {
    PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8',
    XDG_RUNTIME_DIR: '/run/user/61001',
  });
  assert.equal(JSON.parse(tests.args[tests.args.indexOf('--eval') + 2])
    .includes('scripts/run-isolated-tests.test.mjs'), false,
  'de direct-node kandidaatgate nest de namespacerunner nooit');
  const calls = [];
  const passed = __maintainerTest.executeRootGatePlan(plan, (command, args) => {
    calls.push([command, args]); return { status: 0, stdout: `${command}\n`, stderr: '' };
  });
  assert.equal(passed.status, 0);
  assert.deepEqual(calls.map(([command]) => command), ['/usr/bin/setpriv', '/usr/bin/systemd-run']);
  calls.length = 0;
  const failed = __maintainerTest.executeRootGatePlan(plan, (command, args) => {
    calls.push([command, args]); return { status: command === '/usr/bin/setpriv' ? 9 : 0,
      stdout: '', stderr: 'os-gate faalde' };
  });
  assert.equal(failed.status, 9);
  assert.deepEqual(calls.map(([command]) => command), ['/usr/bin/setpriv'],
    'een falende OS-gate start de transient kandidaatgate niet');
  for (const identity of [
    Object.freeze({ ...releaseFixture.identity,
      groups: Object.freeze([...releaseFixture.identity.groups, 62_000]) }),
    Object.freeze({ ...releaseFixture.identity,
      groupNames: Object.freeze([...releaseFixture.identity.groupNames, 'wheel']) }),
    Object.freeze({ ...releaseFixture.identity,
      serviceGroups: Object.freeze([...releaseFixture.identity.serviceGroups, 62_000]) }),
    Object.freeze({ ...releaseFixture.identity,
      serviceGroupNames: Object.freeze([...releaseFixture.identity.serviceGroupNames, 'wheel']) }),
  ]) assert.throws(() => __maintainerTest.rootGatePlan(
    config, candidate, requestId, { name: 'tests' }, testFiles,
    { ...releaseFixture, identity },
  ), /testidentiteit/u, 'extra numerieke of benoemde groep faalt gesloten');
  fs.chmodSync(path.join(candidate, 'scripts/run-isolated-tests.mjs'), 0o640);
  fs.appendFileSync(path.join(candidate, 'scripts/run-isolated-tests.mjs'), '\n');
  fs.chmodSync(path.join(candidate, 'scripts/run-isolated-tests.mjs'), 0o440);
  assert.throws(() => __maintainerTest.rootGatePlan(
    config, candidate, requestId, { name: 'tests' }, testFiles, releaseFixture,
  ), /runner.*releasecontract/u);

  const runProbe = (exitCode) => {
    const observation = path.join(directory, `gate-${exitCode}.json`);
    const probe = [
      "import fs from 'node:fs';",
      "import path from 'node:path';",
      'const [observation, expectedExit] = process.argv.slice(1);',
      'const dataDir = process.env.EVA_DATA_DIR;',
      `if (process.env.NODE_OPTIONS !== ${JSON.stringify(nodeWasmFlag)}) process.exit(90);`,
      "if (!dataDir || !dataDir.startsWith('/tmp/')) process.exit(91);",
      'const stat = fs.lstatSync(dataDir);',
      'if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid()',
      '  || (stat.mode & 0o777) !== 0o700 || path.resolve(dataDir) === "/tmp") process.exit(92);',
      'fs.writeFileSync(observation, JSON.stringify({ dataDir, mode: stat.mode & 0o777, uid: stat.uid }));',
      'process.exit(Number(expectedExit));',
    ].join('\n');
    const result = spawnSync(process.execPath, [
      '--input-type=module', '--eval', __maintainerTest.ROOT_GATE_LAUNCHER,
      JSON.stringify([
        nodeWasmFlag, '--input-type=module', '--eval', probe, observation, String(exitCode),
      ]),
    ], { encoding: 'utf8' });
    assert.equal(result.status, exitCode, result.stderr);
    const observed = JSON.parse(fs.readFileSync(observation, 'utf8'));
    assert.equal(observed.mode, 0o700); assert.equal(observed.uid, process.getuid());
    assert.equal(fs.existsSync(observed.dataDir), false, 'ook een falende gate laat geen datadirectory achter');
    return observed.dataDir;
  };
  assert.notEqual(runProbe(0), runProbe(7), 'iedere gate krijgt een eigen authority');
});

test('rootgate selecteert exact de canonieke unieke Node-testset en isoleert de OS-runnertest', () => {
  const packageJson = JSON.parse(fs.readFileSync('package.json', 'utf8'));
  const canonicalArguments = __maintainerTest.ROOT_GATE_TEST_POLICY.globs.join(' ');
  assert.equal(packageJson.scripts['test:all'],
    `./scripts/run-isolated-tests-bootstrap ${canonicalArguments}`);
  assert.equal(packageJson.scripts.coverage,
    `./scripts/run-isolated-tests-bootstrap --coverage ${canonicalArguments}`);
  const files = [
    'scripts/execution-ledger-production14-recovery.test.mjs',
    'scripts/execution-ledger-quiescent-snapshot.test.mjs',
    'scripts/run-isolated-tests.mjs',
    'scripts/run-isolated-tests.test.mjs',
    'test/example.test.js',
    'test/example.test.mjs',
    'test/helper.js',
    'test/nested/ignored.test.js',
  ];
  const selection = __maintainerTest.rootGateTestSelection(files);
  assert.deepEqual(selection.direct, [
    'scripts/execution-ledger-production14-recovery.test.mjs',
    'scripts/execution-ledger-quiescent-snapshot.test.mjs',
    'test/example.test.js',
    'test/example.test.mjs',
  ]);
  assert.deepEqual(selection.os, ['scripts/run-isolated-tests.test.mjs']);
  assert.equal(new Set([...selection.direct, ...selection.os]).size,
    selection.direct.length + selection.os.length, 'een test mag niet dubbel draaien');
  assert.throws(() => __maintainerTest.rootGateTestSelection([
    ...files, 'test/example.test.js',
  ]), /dubbel testbestand/u);
});

test('EvaRuntimeRepair/v3 behoudt exact de oorspronkelijke vijf-gatereceipt', (t) => {
  const value = helperFixture(t);
  const observed = [];
  const result = __maintainerTest.processTest(
    value.config, rootRequest(value, 'test'), {
      ...rootRuntime,
      gateRunner: (_config, _candidate, _requestId, gate) => {
        observed.push(gate.name);
        return { status: 0, stdout: '', stderr: '' };
      },
    },
  );
  const expected = ['typecheck', 'lint', 'complexity', 'coverage', 'tests'];
  assert.equal(PROTOCOL, 'EvaRuntimeRepair/v3');
  assert.deepEqual(ROOT_GATE_NAMES, expected);
  assert.deepEqual(observed, expected);
  assert.deepEqual(result.gates.map(({ name }) => name), expected);
  assert.doesNotThrow(() => __maintainerTest.loadSealed(
    value.config, value.tx.id, process.getuid(),
  ));
});

test('een echte src-replacement passeert rootgates zonder stale gegenereerde kaarten', (t) => {
  const value = helperFixture(t);
  const generated = [
    'docs/eva-runtime-contract-map.generated.json',
    'docs/eva-repository-inventory.generated.json',
  ];
  fs.mkdirSync(path.join(value.root, 'docs'), { recursive: true });
  for (const relative of generated) {
    fs.writeFileSync(path.join(value.root, relative), '{"src/foo.js":"base-byte"}\n');
  }
  const tracked = [...value.tracked, ...generated].sort();
  const base = {
    ...value.tx.base, files: tracked,
    tracked_tree_sha256: __maintainerTest.treeSha(value.config, tracked),
  };
  const candidate = {
    ...value.tx.candidate,
    tracked_tree_sha256: __maintainerTest.treeSha(
      value.config, tracked, value.artifacts.replacements,
    ),
  };
  const observed = [];
  const gates = __maintainerTest.runRootGates(
    value.config,
    { request_id: 'replacement-1234-1234-1234-123456789abc' },
    { ...value.tx, base, candidate },
    { ...value.artifacts, tracked, base, candidate },
    (_config, sealedCandidate, _requestId, gate) => {
      observed.push(gate.name);
      assert.equal(fs.readFileSync(path.join(sealedCandidate, 'src/foo.js'), 'utf8'),
        'export const answer = 2;\n');
      assert.equal(fs.readFileSync(path.join(sealedCandidate, generated[0]), 'utf8'),
        '{"src/foo.js":"base-byte"}\n');
      return { status: gate.name.startsWith('architecture-') ? 23 : 0, stdout: '', stderr: '' };
    },
  );
  const expected = ['typecheck', 'lint', 'complexity', 'coverage', 'tests'];
  assert.deepEqual(observed, expected);
  assert.deepEqual(gates.map(({ name }) => name), expected);
});

test('een tracked tombstone faalt vóór de sealed candidate wordt aangemaakt', (t) => {
  const value = helperFixture(t);
  const requestId = 'tombstone-1234-1234-1234-123456789abc';
  const candidate = path.join(value.maintainer, 'candidates', requestId);
  let gateCalls = 0;
  assert.throws(() => __maintainerTest.runRootGates(
    value.config, { request_id: requestId }, value.tx,
    { ...value.artifacts, tracked: [...value.tracked, 'test/removed.test.js'] },
    () => { gateCalls += 1; return { status: 0, stdout: '', stderr: '' }; },
  ), /tracked manifest bevat geen regulier bronbestand: test\/removed\.test\.js/u);
  assert.equal(gateCalls, 0);
  assert.equal(fs.existsSync(candidate), false,
    'een tombstone mag geen gedeeltelijke candidateboom achterlaten');
});

test('guard-release valideert exact het statische runnerreleasecontract en installer-artifacts', () => {
  const release = fs.readFileSync('scripts/run-isolated-tests-release.json');
  const artifacts = {
    release,
    bootstrap: fs.readFileSync('scripts/run-isolated-tests-bootstrap'),
    bootstrapSource: fs.readFileSync('scripts/run-isolated-tests-bootstrap.c'),
    runner: fs.readFileSync('scripts/run-isolated-tests.mjs'),
  };
  const contract = __maintainerTest.validateIsolatedTestRelease(artifacts);
  assert.equal(Object.isFrozen(contract), true);
  assert.equal(contract.installPath, '/usr/local/libexec/eva/run-isolated-tests-bootstrap');
  assert.equal(contract.sha256.bootstrap,
    '320d84306d8f23d21ea2bad40bbfe8bff1ca7ab3acf60c6baba3f5f59a1a7882');
  assert.equal(contract.sha256.bootstrapSource,
    'fa7234de4eced386f02c37555de31fbbbcea8b3f5fc01259bc9ffcd467f8628b');
  assert.equal(contract.sha256.runner,
    '012dc998e0804eaa6216c8acbde1d1dd83f591c8cd0d2a239ba288234cc19d27');
  assert.equal(contract.claims.normalNestedNodeAvailability, true);
  assert.equal(contract.claims.hostileTestEnvironmentWipeAvailability, false);
  for (const mutation of [
    { ...artifacts, release: Buffer.from(release.toString('utf8').replace('"schema": 1', '"schema": 2')) },
    { ...artifacts, runner: Buffer.concat([artifacts.runner, Buffer.from('\n')]) },
    { ...artifacts, bootstrap: Buffer.from(artifacts.bootstrap).fill(0, 0, 4) },
  ]) assert.throws(() => __maintainerTest.validateIsolatedTestRelease(mutation));

  const installer = fs.readFileSync('deploy/install-eva-runtime-maintainer.sh', 'utf8');
  assert.match(installer, /x86_64-linux-gnu-gcc-13/u);
  assert.match(installer, /run-isolated-tests-release\.json/u);
  assert.match(installer, /install -o root -g root -m 0755[^\n]+run-isolated-tests-bootstrap/u);
  for (const installed of [
    '/usr/local/libexec/eva/run-isolated-tests-bootstrap.c',
    '/usr/local/libexec/eva/run-isolated-tests.mjs',
    '/usr/local/libexec/eva/run-isolated-tests-release.json',
  ]) assert.match(installer, new RegExp(installed.replaceAll('/', '\\/'), 'u'));
  assert.match(installer, /eva-test-gate/u);
  assert.match(installer, /loginctl enable-linger/u);
  assert.match(installer, /git -C "\$PROJECT_ROOT" ls-files --error-unmatch/u);
  assert.doesNotMatch(installer, /usermod[^\n]*\s-a(?:\s|$)/u,
    'installer mag bestaande supplementaire groepen nooit behouden');
  assert.match(installer, /usermod[^\n]+-g "\$SERVICE_USER"[^\n]+-G "\$RUNTIME_GROUP"[^\n]+"\$SERVICE_USER"/u);
  assert.match(installer, /usermod[^\n]+-g "\$TEST_GATE_USER"[^\n]+-G "\$RUNTIME_GROUP"[^\n]+"\$TEST_GATE_USER"/u);
  for (const numericIdentity of ['SERVICE_GID', 'TEST_GATE_GID', 'RUNTIME_GID']) {
    assert.match(installer, new RegExp(`${numericIdentity}=`, 'u'));
  }
  assert.match(installer, /id -Gn/u); assert.match(installer, /id -G/u);
});

test('guard-release installer rendert exact een custom-root EVA_DATA_DIR unitregel', () => {
  const installer = fs.readFileSync(path.resolve('deploy/install-eva-runtime-maintainer.sh'), 'utf8');
  const customRoot = '/srv/eva-custom';
  const values = {
    serviceUser: 'eva', runtimeReadGroup: 'eva-runtime-read', projectRoot: customRoot,
  };
  const template = fs.readFileSync(path.resolve('deploy/eva.service'), 'utf8');
  const rendered = __maintainerTest.renderEvaServiceUnit(template, values);
  assert.deepEqual(rendered.split('\n').filter((line) => line.includes('EVA_DATA_DIR')),
    [`Environment=EVA_DATA_DIR=${customRoot}/data`]);
  assert.equal(__maintainerTest.renderEvaServiceUnit(rendered, values), rendered,
    'de postcondition is exact en idempotent');
  assert.throws(() => __maintainerTest.renderEvaServiceUnit(
    template.replace(/^Environment=EVA_DATA_DIR=.*\n/mu, ''), values,
  ), /exact één canonieke EVA_DATA_DIR/u);
  assert.throws(() => __maintainerTest.renderEvaServiceUnit(
    template.replace(/^Environment=EVA_DATA_DIR=.*$/mu,
      'Environment=EVA_DATA_DIR=/opt/eva/data\nEnvironment=EVA_DATA_DIR=/opt/eva/data'), values,
  ), /exact één canonieke EVA_DATA_DIR/u);
  const installAt = installer.indexOf('install -o root -g "$RUNTIME_GROUP" -m 0640 "$SERVICE_TEMP"');
  const postconditionAt = installer.indexOf('EVA_SERVICE_POSTCONDITION');
  const signingAt = installer.indexOf('crypto.createHash("sha256")');
  assert.equal(installAt > -1 && postconditionAt > installAt && signingAt > postconditionAt, true,
    'de geïnstalleerde unit wordt vóór release-signing opnieuw zuiver geattesteerd');
});

test('guard-release serviceattestatie weigert missing, dubbele en afwijkende EVA_DATA_DIR fixtures', () => {
  const root = '/tmp/eva-service-attestation-fixture/repo';
  const expectedDataDir = path.join(root, 'data');
  const value = { service_user: 'eva', runtime_read_group: 'eva-runtime-read' };
  const showValues = {
    User: value.service_user,
    Group: value.service_user,
    SupplementaryGroups: value.runtime_read_group,
    WorkingDirectory: root,
    ExecStart: `/usr/bin/node ${path.join(root, 'src/index.js')}`,
    Environment: `EVA_DATA_DIR=${expectedDataDir}`,
  };
  const installedWithoutEnvironment = [
    '[Service]', `User=${value.service_user}`, `Group=${value.service_user}`,
    `SupplementaryGroups=${value.runtime_read_group}`, `WorkingDirectory=${root}`,
    `ExecStart=/usr/bin/node ${path.join(root, 'src/index.js')}`,
    'UnsetEnvironment=EVA_WEB_TOKEN WEB_HOST', '',
  ].join('\n');
  const expectedUnit = installedWithoutEnvironment.replace(
    `WorkingDirectory=${root}\n`,
    `WorkingDirectory=${root}\nEnvironment=EVA_DATA_DIR=${expectedDataDir}\n`,
  );
  const dropIn = [
    '[Service]',
    'ExecStartPre=+/usr/bin/node /usr/local/libexec/eva-runtime-maintainer.mjs --boot /etc/eva-runtime-self-repair.json',
    'ExecStartPost=+/usr/bin/node /usr/local/libexec/eva-runtime-maintainer.mjs --verify-live /etc/eva-runtime-self-repair.json',
    '',
  ].join('\n');
  const snapshot = (unit, environment = showValues.Environment) => Object.freeze({
    ...showValues, Environment: environment,
    ServiceUid: '61000', ServiceGid: '61000', ServicePrimaryGroup: 'eva',
    ServiceGroupIds: '4343 61000', ServiceGroups: 'eva eva-runtime-read',
    ServiceHome: '/nonexistent', ServiceShell: '/usr/sbin/nologin',
    RuntimeReadGroup: 'eva-runtime-read', RuntimeReadGid: '4343',
    BootstrapPath: '/usr/local/libexec/eva/run-isolated-tests-bootstrap',
    BootstrapSha256: '320d84306d8f23d21ea2bad40bbfe8bff1ca7ab3acf60c6baba3f5f59a1a7882',
    BootstrapUid: '0', BootstrapGid: '0', BootstrapMode: '0755', BootstrapNlink: '1',
    GateUser: 'eva-test-gate', GateUid: '61001', GateGid: '61001',
    GatePrimaryGroup: 'eva-test-gate', GateGroupIds: '4343 61001',
    GateGroups: `eva-test-gate ${value.runtime_read_group}`,
    GateHome: '/nonexistent', GateShell: '/usr/sbin/nologin', unit, dropIn,
  });
  const attest = (unit, environment = showValues.Environment) => (
    __runtimeControllerTest.serviceAttestationValid(value, root, snapshot(unit, environment))
  );
  assert.equal(attest(expectedUnit), true);
  assert.equal(attest(installedWithoutEnvironment, ''), false,
    'de huidige installed-unitvorm zonder Environment is incompatibel');
  assert.equal(attest(installedWithoutEnvironment), false,
    'alleen effectieve Environment zonder exacte unitregel is onvoldoende');
  assert.equal(attest(expectedUnit, ''), false,
    'alleen een unitregel zonder effectieve Environment is onvoldoende');
  assert.equal(attest(expectedUnit, `${showValues.Environment} ${showValues.Environment}`), false);
  assert.equal(attest(expectedUnit, 'EVA_DATA_DIR=/tmp/ander'), false);
  assert.equal(attest(`${expectedUnit}Environment=EVA_DATA_DIR=${expectedDataDir}\n`), false);
  assert.equal(attest(expectedUnit.replace(expectedDataDir, '/tmp/ander')), false);
  const inherited = Object.create(snapshot(expectedUnit));
  Object.freeze(inherited);
  assert.equal(__runtimeControllerTest.serviceAttestationValid(value, root, inherited), false,
    'geërfde snapshotvelden of prototypepollution verlenen geen attestatie');
  assert.equal(__runtimeControllerTest.serviceAttestationValid(
    value, root, { ...snapshot(expectedUnit) },
  ), false, 'de hermetische testboundary accepteert uitsluitend een bevroren snapshot');
  assert.equal(__runtimeControllerTest.serviceAttestationValid(value, root, Object.freeze({
    ...snapshot(expectedUnit), BootstrapSha256: '0'.repeat(64),
  })), false);
  assert.equal(__runtimeControllerTest.serviceAttestationValid(value, root, Object.freeze({
    ...snapshot(expectedUnit), BootstrapUid: '1000',
  })), false);
  assert.equal(__runtimeControllerTest.serviceAttestationValid(value, root, Object.freeze({
    ...snapshot(expectedUnit), GateUser: value.service_user,
  })), false);
  assert.equal(__runtimeControllerTest.serviceAttestationValid(value, root, Object.freeze({
    ...snapshot(expectedUnit), GateUid: '61000',
  })), false);
  for (const mutation of [
    { ServiceGroupIds: '4343 61000 62000' },
    { ServiceGroups: 'eva eva-runtime-read wheel' },
    { GateGroupIds: '4343 61001 62000' },
    { GateGroups: 'eva-test-gate eva-runtime-read wheel' },
    { SupplementaryGroups: 'eva-runtime-read wheel' },
    { GateGid: '4343', GateGroupIds: '4343' },
    { ServicePrimaryGroup: 'wheel' },
    { GatePrimaryGroup: 'wheel' },
    { ServiceHome: '/var/lib/eva' },
  ]) assert.equal(__runtimeControllerTest.serviceAttestationValid(value, root, Object.freeze({
    ...snapshot(expectedUnit), ...mutation,
  })), false, `identiteitsmutatie moet gesloten falen: ${JSON.stringify(mutation)}`);
  assert.equal(Object.hasOwn(__runtimeControllerTest, 'serviceAttested'), false,
    'de /etc lezende productiegrens is geen injecteerbare testseam');
});

test('dependencyseal accepteert alleen interne symlinks en root-owned readonly bytes', (t) => {
  const directory = fs.mkdtempSync('/tmp/eva-dependency-seal-');
  t.after(() => {
    fs.chmodSync(directory, 0o755); fs.chmodSync(path.join(directory, 'package'), 0o755);
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const packageDir = path.join(directory, 'package'); fs.mkdirSync(packageDir);
  fs.writeFileSync(path.join(packageDir, 'index.js'), 'export default 1;\n', { mode: 0o444 });
  fs.symlinkSync('package/index.js', path.join(directory, 'entry.js'));
  fs.chmodSync(packageDir, 0o555); fs.chmodSync(directory, 0o555);
  const sealed = dependencyTreeSha256(directory, { uid: process.getuid(), readonly: true });
  assert.match(sealed, /^[a-f0-9]{64}$/u);
  fs.chmodSync(directory, 0o755); fs.symlinkSync('/etc/passwd', path.join(directory, 'escape'));
  fs.chmodSync(directory, 0o555);
  assert.throws(() => dependencyTreeSha256(directory, {
    uid: process.getuid(), readonly: true,
  }), /symlink ontsnapt/u);
  fs.chmodSync(directory, 0o755); fs.rmSync(path.join(directory, 'escape')); fs.chmodSync(directory, 0o775);
  assert.throws(() => dependencyTreeSha256(directory, {
    uid: process.getuid(), readonly: true,
  }), /root-owned readonly/u);
  assert.match(dependencyTreeSha256(directory, { uid: process.getuid() }), /^[a-f0-9]{64}$/u);
});

test('restart-handoff gebeurt aantoonbaar pas nadat de rootlock vrij is', async (t) => {
  const { maintainer } = fixture(t);
  const lock = path.join(maintainer, 'maintainer.lock');
  let restarted = false;
  await __maintainerTest.withRestartHandoff({ maintainer_state_dir: maintainer }, async () => {
    assert.equal(fs.existsSync(lock), true);
    return true;
  }, () => {
    assert.equal(fs.existsSync(lock), false);
    restarted = true;
  });
  assert.equal(restarted, true);
});

test('healthauth vereist root-token en bindt livebewijs aan MainPID, cgroup en luistersokkel', async (t) => {
  const directory = fs.mkdtempSync('/tmp/eva-health-auth-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const root = path.join(directory, 'repo'); fs.mkdirSync(root);
  const openConfig = { root, health_token_file: '' };
  assert.throws(() => __maintainerTest.assertHealthAuth(openConfig, process.getuid()), /token/u);

  let expected = 'b'.repeat(40);
  const server = http.createServer((request, response) => {
    if (expected && request.headers.authorization !== `Bearer ${expected}`) {
      response.writeHead(401, { 'content-type': 'application/json' }); response.end('{"error":"unauthorized"}'); return;
    }
    response.writeHead(200, { 'content-type': 'application/json' }); response.end('{"gezond":true}');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const address = server.address();
  openConfig.health_url = `http://127.0.0.1:${address.port}/api/health`;
  const token = 'a'.repeat(40);
  fs.writeFileSync(path.join(root, '.env'), `EVA_WEB_TOKEN=${token}\n`);
  const tokenFile = path.join(directory, 'health.token');
  fs.writeFileSync(tokenFile, token, { mode: 0o600 }); fs.chmodSync(tokenFile, 0o600);
  const tokenConfig = { ...openConfig, health_token_file: tokenFile };
  assert.equal(__maintainerTest.assertHealthAuth(tokenConfig, process.getuid()).mode, 'bearer-token');
  assert.equal(await __maintainerTest.jsonRequest(
    tokenConfig, '/api/health', { rootUid: process.getuid() },
  ), null, 'een verkeerde bearer-token levert 401/null');
  expected = token;
  assert.deepEqual(await __maintainerTest.jsonRequest(
    tokenConfig, '/api/health', { rootUid: process.getuid() },
  ), { gezond: true });
  const cgroup = fs.readFileSync('/proc/self/cgroup', 'utf8').trim().split(/\r?\n/u)[0].split(':').at(-1);
  const systemdShow = (property) => property === 'MainPID' ? String(process.pid) : cgroup;
  assert.equal(__maintainerTest.assertLiveService(tokenConfig, { systemdShow }), process.pid);
  assert.throws(() => __maintainerTest.assertLiveService({
    ...tokenConfig, health_url: `http://127.0.0.2:${address.port}/api/health`,
  }, { systemdShow }), /liveendpoint/u, 'dezelfde PID en poort op een ander adres zijn geen endpointbewijs');
  assert.throws(() => __maintainerTest.assertLiveService(tokenConfig, {
    systemdShow: (property) => property === 'MainPID' ? String(process.pid + 100_000) : cgroup,
  }), /MainPID/u);
  fs.writeFileSync(tokenFile, 'b'.repeat(40));
  assert.throws(() => __maintainerTest.assertHealthAuth(
    tokenConfig, process.getuid(),
  ), /wijkt af/u);
  fs.chmodSync(tokenFile, 0o640);
  assert.throws(() => __maintainerTest.assertHealthAuth(
    tokenConfig, process.getuid(),
  ), /0600/u);
});

test('runtimecanary bindt een echte request-id en Idempotency-Key aan dezelfde transactie', async (t) => {
  const directory = fs.mkdtempSync('/tmp/eva-runtime-canary-');
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const root = path.join(directory, 'repo');
  fs.mkdirSync(root);
  const token = 'c'.repeat(40);
  fs.writeFileSync(path.join(root, '.env'), `EVA_WEB_TOKEN=${token}\n`);
  const tokenFile = path.join(directory, 'health.token');
  fs.writeFileSync(tokenFile, token, { mode: 0o600 });
  fs.chmodSync(tokenFile, 0o600);
  const runtime = 'd'.repeat(64);
  const tx = { id: '12345678-1234-1234-1234-123456789abc' };
  const expectedId = `runtime-canary:${tx.id}`;
  const server = http.createServer((request, response) => {
    let raw = '';
    request.on('data', (chunk) => { raw += chunk; });
    request.on('end', () => {
      const body = JSON.parse(raw);
      assert.equal(request.headers.authorization, `Bearer ${token}`);
      assert.equal(request.headers['idempotency-key'], expectedId);
      assert.equal(body.request_id, expectedId);
      assert.equal(body.synthetic, undefined);
      assert.equal(body.conversation_id, `runtime-canary-${tx.id}`);
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({
        ok: true, reply: 'Ik leef.', turn_id: 'turn-runtime-canary',
        conversation_id: `web:runtime-canary-${tx.id}`,
        runtime_code_sha256: runtime,
      }));
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const address = server.address();
  assert.equal(await __maintainerTest.conversationCanary({
    root, health_token_file: tokenFile,
    health_url: `http://127.0.0.1:${address.port}/api/health`,
  }, tx, runtime, process.getuid()), true);
});

test('rootparser weigert kandidaat-injectie, dubbele paden, binary, rename, mode en grenzen', (t) => {
  const value = helperFixture(t);
  fs.writeFileSync(path.join(value.txDir, 'candidate/source'), 'export const answer = 31337;\n');
  assert.throws(() => __maintainerTest.verifyArtifacts(
    value.config, value.tx, value.txDir, 'apply',
  ), /root-afgeleide kandidaat/u);
  for (const malicious of [
    Buffer.from(`${patch}${patch}`),
    Buffer.from(patch.replaceAll('src/foo.js', '../src/foo.js')),
    Buffer.from(`diff --git a/src/foo.js b/src/foo.js\nold mode 100644\nnew mode 100755\n${patch}`),
    Buffer.from(`diff --git a/src/foo.js b/src/bar.js\n${patch}`),
    Buffer.from('diff --git a/src/foo.js b/src/foo.js\n'),
    Buffer.from(`diff --git a/src/foo.js b/src/foo.js\nindex abcd..ef01\nindex abcd..ef01\n${patch}`),
    Buffer.from(patch.replace('@@ -1 +1 @@', '@@ -0 +1 @@')),
    Buffer.from([0, 1, 2]),
    Buffer.alloc(256 * 1024 + 1, 65),
  ]) assert.throws(() => __maintainerTest.parseUnifiedDiff(malicious));
  const rows = Array.from({ length: 201 }, (_, index) => `-old${index}\n+new${index}`).join('\n');
  assert.throws(() => __maintainerTest.parseUnifiedDiff(Buffer.from(
    `--- a/src/foo.js\n+++ b/src/foo.js\n@@ -1,201 +1,201 @@\n${rows}\n`,
  )), /maximaal 400/u);
});

test('root bepaalt zelf dat gate-, policy- en selfrepaircode nooit low-risk bron is', (t) => {
  const value = helperFixture(t);
  const protectedFiles = [
    ['scripts/quality-gate.mjs', 'export const gate = true;\n'],
    ['src/mind/turnplan.js', 'export const policy = true;\n'],
    ['src/mind/runtimecontroller.js', 'export const repair = true;\n'],
  ];
  for (const [relative, content] of protectedFiles) {
    const absolute = path.join(value.root, relative);
    fs.mkdirSync(path.dirname(absolute), { recursive: true }); fs.writeFileSync(absolute, content);
    execFileSync('/usr/bin/git', ['-C', value.root, 'add', relative]);
  }
  const tracked = execFileSync('/usr/bin/git', ['-C', value.root, 'ls-files'], { encoding: 'utf8' })
    .trim().split('\n');
  for (const [relative] of protectedFiles) {
    assert.throws(() => __maintainerTest.checkedSource(value.config, relative, tracked), /niet toegestaan/u);
  }
});

test('root-seal bindt de gelezen bytes en negeert een latere serviceswap', (t) => {
  const value = helperFixture(t);
  const verified = __maintainerTest.verifyArtifacts(value.config, value.tx, value.txDir, 'apply');
  const sealed = __maintainerTest.sealTransaction(value.config, value.tx, {
    actor: { principal: 'telegram:vervalst' }, origin: { input_sha256: '0'.repeat(64) },
  }, verified);
  fs.writeFileSync(path.join(value.txDir, 'candidate/source'), 'export const answer = 99;\n');
  assert.equal(
    fs.readFileSync(path.join(sealed.txDir, 'candidate/source'), 'utf8'),
    'export const answer = 2;\n',
  );
  assert.equal(
    __maintainerTest.verifyArtifacts(value.config, sealed.tx, sealed.txDir, 'apply')
      .replacements.get('src/foo.js').toString('utf8'),
    'export const answer = 2;\n',
  );
  assert.throws(() => __maintainerTest.sealTransaction(
    value.config, value.tx, {}, verified,
  ), /bestaat al/u);
});

test('loadSealed weigert ook zelf-consistent hertekende maar inhoudelijk vervalste receipts', (t) => {
  const value = helperFixture(t);
  __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
  const original = canonicalTx(value);
  const txPath = path.join(value.maintainer, 'transactions', value.tx.id, 'transaction.json');
  const mutations = [
    (receipt) => { receipt.risk = 'low'; },
    (receipt) => { receipt.changed_lines = 0; },
    (receipt) => { receipt.candidate_tree_sha256 = '0'.repeat(64); },
    (receipt) => { receipt.gates = receipt.gates.filter(({ name }) => name !== 'coverage'); },
    (receipt) => { receipt.gates[3].exit_code = 1; },
    (receipt) => { receipt.verzonnen_bewijs = true; },
    (receipt) => { receipt.expires_at = receipt.issued_at; },
  ];
  for (const mutate of mutations) {
    const forged = structuredClone(original);
    mutate(forged.root_receipt);
    const unsigned = { ...forged.root_receipt }; delete unsigned.sha256;
    forged.root_receipt.sha256 = __maintainerTest.sha256(JSON.stringify(unsigned));
    fs.writeFileSync(txPath, `${JSON.stringify(forged, null, 2)}\n`);
    assert.throws(() => __maintainerTest.loadSealed(
      value.config, value.tx.id, process.getuid(),
    ), /root-receipt/u);
  }
  const failedAfterGates = structuredClone(original);
  failedAfterGates.status = 'root_test_failed';
  fs.writeFileSync(txPath, `${JSON.stringify(failedAfterGates, null, 2)}\n`);
  assert.equal(__maintainerTest.loadSealed(
    value.config, value.tx.id, process.getuid(),
  ).tx.status, 'root_test_failed');
  fs.writeFileSync(txPath, `${JSON.stringify(original, null, 2)}\n`);
});

test('root_tested replay reconcilieert ontbrekende status na crash tussen tx en status', (t) => {
  const value = helperFixture(t); let crashed = false;
  assert.throws(() => __maintainerTest.processTest(value.config, rootRequest(value, 'test'), {
    ...rootRuntime,
    checkpoint: (name) => {
      if (name === 'root-test:transaction') {
        crashed = true; const error = new Error('crash@root-test:transaction');
        error.runtimeCrash = true; throw error;
      }
    },
  }), /crash@root-test:transaction/u);
  assert.equal(crashed, true); assert.equal(canonicalTx(value).status, 'root_tested');
  const statusPath = path.join(value.maintainer, 'status', `${value.tx.id}.json`);
  assert.equal(fs.existsSync(statusPath), false);
  const replay = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
  assert.equal(replay.status, 'root_tested'); assert.equal(canonicalStatus(value).status, 'root_tested');
  assert.match(fs.readFileSync(path.resolve('deploy/eva-runtime-maintainer.path'), 'utf8'), /processing/u);
});

test('rootgate-detectie weigert kandidaatmutatie en stale productionbron', (t) => {
  const value = helperFixture(t);
  const sealed = __maintainerTest.sealTransaction(value.config, value.tx, {}, value.artifacts);
  const artifacts = __maintainerTest.verifyArtifacts(value.config, sealed.tx, sealed.txDir, 'apply');
  const previousUmask = process.umask(0o077);
  try {
    assert.throws(() => __maintainerTest.runRootGates(
      value.config, { request_id: '12345678-1234-1234-1234-123456789abc' },
      sealed.tx, artifacts, (_config, candidate, _requestId, gate) => {
        const source = path.join(candidate, 'src/foo.js');
        if (gate.name === 'typecheck') {
          assert.equal(fs.statSync(candidate).mode & 0o777, 0o750);
          assert.equal(fs.statSync(path.join(candidate, 'src')).mode & 0o777, 0o750);
          assert.equal(fs.statSync(path.join(candidate, 'node_modules')).mode & 0o777, 0o750);
          assert.equal(fs.statSync(source).mode & 0o777, 0o440);
          fs.chmodSync(source, 0o644);
          fs.writeFileSync(source, 'export const answer = 7;\n');
        }
        return { status: 0, stdout: '', stderr: '' };
      },
    ), /(?:sealed kandidaat|dependencyboom) veranderde/u);
  } finally { process.umask(previousUmask); }
  fs.writeFileSync(path.join(value.root, 'src/foo.js'), 'export const answer = 8;\n');
  assert.throws(() => __maintainerTest.verifyArtifacts(
    value.config, sealed.tx, sealed.txDir, 'apply',
  ), /(?:whole-tree|preimage-live)/u);
});

test('rootgates en livebewijs stoppen bij dependencydrift', async (t) => {
  const value = helperFixture(t);
  const sealed = __maintainerTest.sealTransaction(value.config, value.tx, {}, value.artifacts);
  const artifacts = __maintainerTest.verifyArtifacts(value.config, sealed.tx, sealed.txDir, 'apply');
  const dependency = path.join(value.root, 'node_modules/fixture-dependency/index.js');
  assert.throws(() => __maintainerTest.runRootGates(
    value.config, { request_id: '12345678-1234-1234-1234-123456789abc' }, sealed.tx, artifacts,
    () => {
      fs.chmodSync(dependency, 0o644); fs.writeFileSync(dependency, 'export default false;\n');
      return { status: 0, stdout: '', stderr: '' };
    },
  ), /dependencyboom/u);
  fs.writeFileSync(dependency, 'export default true;\n'); fs.chmodSync(dependency, 0o444);

  const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
  __maintainerTest.processPromotion(value.config, rootRequest(value, 'apply', {
    root_receipt_sha256: tested.root_receipt_sha256,
  }), true, rootRuntime);
  __maintainerTest.recover(value.config, true, rootRuntime);
  await assert.rejects(__maintainerTest.verifyLive(value.config, {
    ...rootRuntime, assertAttestation: () => '0'.repeat(64),
  }), /dependencyseal wijkt af/u);
});

test('livebewijs vertrouwt zelfrapportage nooit zonder root-hash vóór en na de canary', async (t) => {
  const prepareBoot = (value) => {
    const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
    __maintainerTest.processPromotion(value.config, rootRequest(value, 'apply', {
      root_receipt_sha256: tested.root_receipt_sha256,
    }), true, rootRuntime);
    __maintainerTest.recover(value.config, true, rootRuntime);
    const tx = canonicalTx(value);
    return {
      runtime: tx.candidate.runtime_sha256,
      dependency: tx.candidate.dependency_tree_sha256,
    };
  };
  const reportedHealth = ({ runtime, dependency }) => async () => ({
    gezond: true, agent: {}, runtimeVersion: {
      pid: 123, loaded_code_sha256: runtime, disk_code_sha256: runtime, code_matches_disk: true,
      loaded_dependency_tree_sha256: dependency, disk_dependency_tree_sha256: dependency,
    },
  });

  const forged = helperFixture(t); const forgedExpected = prepareBoot(forged);
  fs.writeFileSync(path.join(forged.root, 'src/foo.js'), 'export const answer = 9;\n');
  await assert.rejects(__maintainerTest.verifyLive(forged.config, {
    ...rootRuntime, healthRequest: reportedHealth(forgedExpected), canary: async () => true,
  }), /runtimebron wijkt af/u);

  const drifting = helperFixture(t); const driftingExpected = prepareBoot(drifting);
  await assert.rejects(__maintainerTest.verifyLive(drifting.config, {
    ...rootRuntime, healthRequest: reportedHealth(driftingExpected),
    canary: async () => {
      fs.writeFileSync(path.join(drifting.root, 'src/foo.js'), 'export const answer = 9;\n');
      return true;
    },
  }), /runtimebron wijzigde tijdens de livecanary/u);
});

test('expliciet root-goedgekeurde JS-flow bewijst apply, canary, commit en rollback', async (t) => {
  const value = helperFixture(t);
  const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
  assert.equal(tested.status, 'root_tested');
  assert.equal(tested.active_runtime, 'base');
  const receipt = tested.root_receipt_sha256;
  const applyRequest = rootRequest(value, 'apply', { root_receipt_sha256: receipt });
  assert.equal(canonicalTx(value).root_receipt.risk, 'review');
  assert.deepEqual(canonicalTx(value).root_receipt.gates.map(({ name }) => name), ROOT_GATE_NAMES);
  const applied = __maintainerTest.processPromotion(value.config, applyRequest, true, rootRuntime);
  assert.equal(applied.status, 'applied_pending_boot');
  assert.equal(applied.active_runtime, 'base');
  assert.equal(fs.readFileSync(path.join(value.root, 'src/foo.js'), 'utf8'), 'export const answer = 2;\n');
  assert.equal(canonicalTx(value).receipt, undefined);
  assert.ok(canonicalTx(value).promotion.applied_at);
  const replay = __maintainerTest.processPromotion(value.config, applyRequest, true, rootRuntime);
  assert.equal(replay.changed, false);

  __maintainerTest.recover(value.config, true, rootRuntime);
  const candidateRuntime = canonicalTx(value).candidate.runtime_sha256;
  const dependency = canonicalTx(value).candidate.dependency_tree_sha256;
  await __maintainerTest.verifyLive(value.config, {
    ...rootRuntime,
    healthRequest: async () => ({
      gezond: true, agent: {}, runtimeVersion: {
        pid: 123,
        loaded_code_sha256: candidateRuntime, disk_code_sha256: candidateRuntime, code_matches_disk: true,
        loaded_dependency_tree_sha256: dependency, disk_dependency_tree_sha256: dependency,
      },
    }),
    canary: async () => true,
  });
  assert.equal(canonicalTx(value).status, 'committed');
  assert.equal(__maintainerTest.publicStatus(canonicalTx(value)).active_runtime, 'candidate');

  const rolled = __maintainerTest.processPromotion(
    value.config, rootRequest(value, 'rollback'), true, rootRuntime,
  );
  assert.equal(rolled.status, 'rollback_pending_boot');
  assert.equal(rolled.active_runtime, 'candidate');
  assert.equal(fs.readFileSync(path.join(value.root, 'src/foo.js'), 'utf8'), 'export const answer = 1;\n');
  __maintainerTest.recover(value.config, true, rootRuntime);
  const baseRuntime = canonicalTx(value).base.runtime_sha256;
  await __maintainerTest.verifyLive(value.config, {
    ...rootRuntime,
    healthRequest: async () => ({
      gezond: true, agent: {}, runtimeVersion: {
        pid: 123,
        loaded_code_sha256: baseRuntime, disk_code_sha256: baseRuntime, code_matches_disk: true,
        loaded_dependency_tree_sha256: dependency, disk_dependency_tree_sha256: dependency,
      },
    }),
    canary: async () => true,
  });
  const final = canonicalTx(value);
  const status = __maintainerTest.publicStatus(final);
  assert.equal(final.status, 'rolled_back');
  assert.equal(status.active_runtime, 'base');
  assert.equal(status.full_runtime_sha256, final.base.runtime_sha256);
  assert.equal(__maintainerTest.processPromotion(
    value.config, rootRequest(value, 'rollback'), true, rootRuntime,
  ).changed, false);
});

test('approval_required kan met een latere exacte rootgrant verder zonder actorautoriteit', (t) => {
  const value = helperFixture(t);
  const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
  const request = rootRequest(value, 'apply', { root_receipt_sha256: tested.root_receipt_sha256 });
  const waiting = __maintainerTest.processPromotion(value.config, request, false, rootRuntime);
  assert.equal(waiting.status, 'approval_required');
  assert.equal(fs.readFileSync(path.join(value.root, 'src/foo.js'), 'utf8'), 'export const answer = 1;\n');
  value.config.production_grants = [{
    operation: 'apply', transaction_id: value.tx.id,
    candidate_tree_sha256: value.tx.candidate.tracked_tree_sha256,
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  }];
  assert.equal(__maintainerTest.processPromotion(
    value.config, { ...request, actor: { principal: 'forged' } }, false, rootRuntime,
  ).status, 'applied_pending_boot');
});

test('legacy auto-promotievlaggen geven JavaScript nooit productieautoriteit', (t) => {
  const value = helperFixture(t);
  value.config.auto_promote_low_risk = true;
  value.config.auto_promote_max_per_24h = 24;
  value.config.auto_promote_paths = ['src/foo.js'];
  const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
  assert.equal(canonicalTx(value).root_receipt.risk, 'review');
  const waiting = __maintainerTest.processPromotion(value.config, rootRequest(value, 'apply', {
    root_receipt_sha256: tested.root_receipt_sha256,
  }), false, rootRuntime);
  assert.equal(waiting.status, 'approval_required');
  assert.equal(fs.readFileSync(path.join(value.root, 'src/foo.js'), 'utf8'), 'export const answer = 1;\n');
});

test('stale bron, ontbrekende receipt en ongeldige datums falen vóór een write', async (t) => {
  const missing = helperFixture(t);
  const tested = __maintainerTest.processTest(missing.config, rootRequest(missing, 'test'), rootRuntime);
  assert.throws(() => __maintainerTest.processPromotion(
    missing.config, rootRequest(missing, 'apply'), false, rootRuntime,
  ), /rootreceipt/u);
  assert.equal(fs.readFileSync(path.join(missing.root, 'src/foo.js'), 'utf8'), 'export const answer = 1;\n');
  fs.writeFileSync(path.join(missing.root, 'src/foo.js'), 'export const answer = 9;\n');
  assert.throws(() => __maintainerTest.processPromotion(
    missing.config, rootRequest(missing, 'apply', { root_receipt_sha256: tested.root_receipt_sha256 }), true, rootRuntime,
  ), /preimage-live/u);
  for (const invalid of ['', 'geen-datum', '2026-02-30T00:00:00.000Z']) {
    assert.throws(() => __maintainerTest.dateMillis(invalid, 'datum'), /canonieke datum/u);
    await assert.rejects(__maintainerTest.processRequest({}, {
      ...rootRequest(missing, 'apply'), expires_at: invalid, not_before: new Date().toISOString(),
    }), /canonieke datum/u);
  }
  assert.throws(() => __maintainerTest.matchingProductionGrant({
    production_grants: [{
      operation: 'apply', transaction_id: missing.tx.id,
      candidate_tree_sha256: missing.tx.candidate.tracked_tree_sha256, expires_at: 'NaN',
    }],
  }, missing.tx, 'apply'), /canonieke datum/u);
});

test('onbevestigd herstel blijft gesloten en kan later alsnog live worden bewezen', async (t) => {
  const value = helperFixture(t);
  const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
  __maintainerTest.processPromotion(value.config, rootRequest(value, 'apply', {
    root_receipt_sha256: tested.root_receipt_sha256,
  }), true, rootRuntime);
  __maintainerTest.recover(value.config, true, rootRuntime);
  const unavailable = {
    ...rootRuntime, healthRequest: async () => null, canary: async () => false,
  };
  await assert.rejects(__maintainerTest.verifyLive(value.config, unavailable), /live health/u);
  __maintainerTest.recover(value.config, true, rootRuntime);
  assert.equal(fs.readFileSync(path.join(value.root, 'src/foo.js'), 'utf8'), 'export const answer = 1;\n');
  await assert.rejects(__maintainerTest.verifyLive(value.config, unavailable), /live health/u);
  assert.equal(canonicalTx(value).status, 'recovery_live_unverified');
  assert.equal(
    __maintainerTest.activeMutation(value.config, process.getuid()).mutation.phase,
    'recovery_live_unverified',
  );
  assert.throws(() => __maintainerTest.processTest(
    value.config, rootRequest(value, 'test'), rootRuntime,
  ), /eerdere promotie/u);

  const baseRuntime = canonicalTx(value).base.runtime_sha256;
  const dependency = canonicalTx(value).base.dependency_tree_sha256;
  await __maintainerTest.verifyLive(value.config, {
    ...rootRuntime,
    healthRequest: async () => ({
      gezond: true, agent: {}, runtimeVersion: {
        pid: 123,
        loaded_code_sha256: baseRuntime, disk_code_sha256: baseRuntime, code_matches_disk: true,
        loaded_dependency_tree_sha256: dependency, disk_dependency_tree_sha256: dependency,
      },
    }),
    canary: async () => true,
  });
  assert.equal(canonicalTx(value).status, 'boot_failed_rolled_back_live');
  assert.equal(__maintainerTest.activeMutation(value.config, process.getuid()), null);
});

test('crashinjecties rond ieder mutatiejournal en iedere source/restart-write herstellen atomair', (t) => {
  const boundaries = [
    'mutation-start:marker', 'mutation-start:transaction', 'mutation-start:status',
    'source:src/foo.js:before', 'source:src/foo.js:after',
    'mutation-ready:transaction', 'mutation-ready:status',
    'restart:before', 'restart:after',
  ];
  for (const boundary of boundaries) {
    const value = helperFixture(t);
    const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
    let fired = false;
    const runtime = {
      ...rootRuntime,
      checkpoint: (name) => {
        if (!fired && name === boundary) {
          fired = true;
          const error = new Error(`crash@${name}`); error.runtimeCrash = true; throw error;
        }
      },
    };
    assert.throws(() => __maintainerTest.processPromotion(value.config, rootRequest(value, 'apply', {
      root_receipt_sha256: tested.root_receipt_sha256,
    }), true, runtime), new RegExp(`crash@${boundary.replaceAll('.', '\\.')}`, 'u'));
    assert.equal(fired, true, boundary);
    assert.equal(
      __maintainerTest.activeMutation(value.config, process.getuid())?.id,
      value.tx.id,
      `${boundary}: de rootpointer blijft fail-closed`,
    );
    __maintainerTest.recover(value.config, false, rootRuntime);
    const content = fs.readFileSync(path.join(value.root, 'src/foo.js'), 'utf8');
    const tx = canonicalTx(value);
    assert.ok(['export const answer = 1;\n', 'export const answer = 2;\n'].includes(content), boundary);
    assert.equal(tx.active_runtime, 'base', boundary);
    assert.ok(['mutation_crash_rolled_back', 'applied_pending_boot'].includes(tx.status), boundary);
  }
});

test('root-seal is bij iedere artifact- en renamecrash herhaalbaar zonder halve canonieke transactie', (t) => {
  for (const boundary of [
    'seal:src/foo.js:backup', 'seal:src/foo.js:candidate', 'seal:patch',
    'seal:transaction', 'seal:rename',
  ]) {
    const value = helperFixture(t);
    let fired = false;
    assert.throws(() => __maintainerTest.processTest(value.config, rootRequest(value, 'test'), {
      ...rootRuntime,
      checkpoint: (name) => {
        if (name === boundary) {
          fired = true; const error = new Error(`crash@${name}`); error.runtimeCrash = true; throw error;
        }
      },
    }), /crash@/u);
    assert.equal(fired, true, boundary);
    const retried = __maintainerTest.processTest(
      value.config, rootRequest(value, 'test'), rootRuntime,
    );
    assert.equal(retried.status, 'root_tested', boundary);
  }
});

test('terminal journal fsynct tx en status voordat de current-pointer verdwijnt', async (t) => {
  for (const boundary of [
    'live-verified:transaction', 'live-verified:status',
    'live-verified:marker-before', 'live-verified:marker-after',
  ]) {
    const value = helperFixture(t);
    const tested = __maintainerTest.processTest(value.config, rootRequest(value, 'test'), rootRuntime);
    __maintainerTest.processPromotion(value.config, rootRequest(value, 'apply', {
      root_receipt_sha256: tested.root_receipt_sha256,
    }), true, rootRuntime);
    __maintainerTest.recover(value.config, true, rootRuntime);
    const runtimeHash = canonicalTx(value).candidate.runtime_sha256;
    const dependency = canonicalTx(value).candidate.dependency_tree_sha256;
    await assert.rejects(__maintainerTest.verifyLive(value.config, {
      ...rootRuntime,
      healthRequest: async () => ({
        gezond: true, agent: {}, runtimeVersion: {
          pid: 123,
          loaded_code_sha256: runtimeHash, disk_code_sha256: runtimeHash, code_matches_disk: true,
          loaded_dependency_tree_sha256: dependency, disk_dependency_tree_sha256: dependency,
        },
      }),
      canary: async () => true,
      checkpoint: (name) => {
        if (name === boundary) {
          const error = new Error(`crash@${name}`); error.runtimeCrash = true; throw error;
        }
      },
    }), /crash@live-verified/u);
    assert.equal(canonicalTx(value).status, 'committed');
    const pointer = path.join(value.maintainer, 'current-apply.json');
    if (!fs.existsSync(pointer)) assert.equal(canonicalStatus(value).status, 'committed', boundary);
    __maintainerTest.recover(value.config, true, rootRuntime);
    const tx = canonicalTx(value);
    assert.equal(tx.status, 'committed');
    assert.equal(tx.active_runtime, 'candidate');
    assert.equal(canonicalStatus(value).status, 'committed');
    assert.equal(fs.existsSync(pointer), false);
    assert.equal(__maintainerTest.treeSha(value.config, value.tracked), tx.candidate.tracked_tree_sha256);
  }
});

test('rollback gebruikt root-sealed preimage, is idempotent en CAS-begrensd', (t) => {
  const value = helperFixture(t);
  const sealed = __maintainerTest.sealTransaction(value.config, value.tx, {}, value.artifacts);
  const source = path.join(value.root, 'src/foo.js');
  const owner = fs.statSync(source);
  fs.writeFileSync(source, 'export const answer = 2;\n');
  __maintainerTest.restore(value.config, sealed.tx, sealed.txDir, 'apply', process.getuid());
  assert.equal(fs.readFileSync(source, 'utf8'), 'export const answer = 1;\n');
  assert.equal(fs.statSync(source).uid, owner.uid);
  assert.equal(fs.statSync(source).gid, owner.gid);
  __maintainerTest.restore(value.config, sealed.tx, sealed.txDir, 'apply', process.getuid());
  fs.writeFileSync(source, 'export const answer = 31337;\n');
  assert.throws(() => __maintainerTest.restore(
    value.config, sealed.tx, sealed.txDir, 'apply', process.getuid(),
  ), /CAS conflict/u);
});

test('concurrente bootfase blokkeert tweede promotie en live-uitkomsten zijn begrensd', () => {
  for (const phase of ['applying', 'applied_pending_boot', 'rollback_pending_boot', 'booting', 'recovery_booting']) {
    assert.equal(__maintainerTest.mutationInProgress({ phase }), true, phase);
  }
  assert.equal(__maintainerTest.mutationInProgress({ phase: 'committed' }), false);
  assert.equal(__maintainerTest.liveOutcome('booting', true, 'apply'), 'committed');
  assert.equal(__maintainerTest.liveOutcome('booting', true, 'rollback'), 'rolled_back');
  assert.equal(__maintainerTest.liveOutcome('booting', false, 'apply'), null);
  assert.equal(
    __maintainerTest.liveOutcome('recovery_booting', false, 'apply'),
    'recovery_live_unverified',
  );
  assert.equal(
    __maintainerTest.liveOutcome('recovery_booting', true, 'apply'),
    'boot_failed_rolled_back_live',
  );
  const runtime = 'a'.repeat(64);
  const canary = {
    ok: true, reply: 'Ik leef.', turn_id: 'turn-canary',
    conversation_id: 'web:runtime-canary-1', runtime_code_sha256: runtime,
  };
  assert.equal(__maintainerTest.validCanaryReply(canary, 'runtime-canary-1', runtime), true);
  assert.equal(__maintainerTest.validCanaryReply({ ...canary, ok: false }, 'runtime-canary-1', runtime), false);
  assert.equal(__maintainerTest.validCanaryReply({
    ...canary, runtime_code_sha256: 'b'.repeat(64),
  }, 'runtime-canary-1', runtime), false);
  const phaseTx = {
    id: '12345678-1234-1234-1234-123456789abc',
    base: { tracked_tree_sha256: 'c'.repeat(64), runtime_sha256: 'd'.repeat(64) },
    candidate: { tracked_tree_sha256: 'e'.repeat(64), runtime_sha256: 'f'.repeat(64) },
  };
  for (const [status, direction, expected] of [
    ['applied_pending_boot', 'apply', 'base'],
    ['rollback_pending_boot', 'rollback', 'candidate'],
    ['booting', 'apply', null],
    ['recovery_booting', 'apply', null],
    ['recovery_live_unverified', 'rollback', null],
  ]) {
    const result = __maintainerTest.publicStatus({
      ...phaseTx, status, mutation: { phase: status, direction },
    });
    assert.equal(result.active_runtime, expected, status);
    assert.equal(result.active_tree_sha256, expected ? phaseTx[expected].tracked_tree_sha256 : null, status);
  }
});

test('canonieke rootstatus is exact aan kandidaat en werkelijk actieve runtime gebonden', () => {
  const tx = {
    id: '12345678-1234-1234-1234-123456789abc', active_runtime: 'base',
    base: { tracked_tree_sha256: 'e'.repeat(64), runtime_sha256: 'f'.repeat(64), dependency_tree_sha256: 'd'.repeat(64) },
    candidate: { tracked_tree_sha256: 'a'.repeat(64), runtime_sha256: 'b'.repeat(64), dependency_tree_sha256: 'd'.repeat(64) },
  };
  const status = {
    protocol: __runtimeControllerTest.PROTOCOL, transaction_id: tx.id,
    status: 'root_tested', ok: true, changed: false, verified: true,
    needs_followup: true, live_verified: false, updated_at: new Date().toISOString(),
    base_tree_sha256: tx.base.tracked_tree_sha256,
    candidate_tree_sha256: tx.candidate.tracked_tree_sha256,
    candidate_runtime_sha256: tx.candidate.runtime_sha256,
    dependency_tree_sha256: tx.candidate.dependency_tree_sha256,
    active_runtime: 'base', active_tree_sha256: tx.base.tracked_tree_sha256,
    full_runtime_sha256: tx.base.runtime_sha256,
    root_receipt_sha256: '9'.repeat(64), gates: passingGates(), reason: null, approval_binding: null,
  };
  assert.equal(__runtimeControllerTest.validateRootStatus(status, tx), status);
  assert.equal(__runtimeControllerTest.validateRootStatus({
    ...status, status: 'root_test_failed', ok: false, verified: false, needs_followup: false,
    reason: 'rootgate faalde', gates: [],
  }, tx).status, 'root_test_failed');
  assert.throws(() => __runtimeControllerTest.validateRootStatus({
    ...status, candidate_tree_sha256: 'c'.repeat(64),
  }, tx));
  assert.throws(() => __runtimeControllerTest.validateRootStatus({
    ...status, candidate_runtime_sha256: 'd'.repeat(64),
  }, tx));
  for (const forged of [
    { ...status, status: 'zelf_verzonnen' },
    { ...status, ok: false },
    { ...status, changed: true },
    { ...status, active_tree_sha256: '0'.repeat(64) },
    { ...status, dependency_tree_sha256: '0'.repeat(64) },
    { ...status, root_receipt_sha256: 'te-kort' },
    { ...status, gates: passingGates().filter(({ name }) => name !== 'coverage') },
    { ...status, gates: passingGates().map((gate) => (
      gate.name === 'coverage' ? { ...gate, exit_code: 1 } : gate
    )) },
    { ...status, updated_at: 'morgen' },
    { ...status, verzonnen_bewijs: true },
    { ...status, approval_binding: {} },
    { ...status, status: 'approval_required', verified: false },
    { ...status, reason: 'toch een fout' },
    { ...status, status: 'root_test_failed', ok: false, verified: false,
      needs_followup: false, gates: [], reason: null },
    { ...status, status: 'committed', needs_followup: false, live_verified: true,
      active_runtime: 'base' },
  ]) assert.throws(() => __runtimeControllerTest.validateRootStatus(forged, tx));
});
