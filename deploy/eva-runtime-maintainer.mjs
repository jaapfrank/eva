#!/usr/bin/node
// Root-owned privilege boundary for EvaRuntimeRepair/v3. Install this file;
// never execute the mutable repository copy as the maintainer.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { execFileSync, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import {
  HASH_PATTERN, ID_PATTERN, MAX_FILES, MAX_PATCH_BYTES, PHASE_MATRIX, PROTOCOL, RELEASE_PATHS,
  ROOT_GATE_NAMES, activeRuntimeFor, applyHunks, atomicBlob, atomicJson, attestRelease, contained,
  dateMillis, dependencyTreeSha256, fileSetSha256, fsyncDirectory, phaseContract, parseUnifiedDiff,
  readRegular, repairSourcePath, runtimeTreeSha256, securePath, sha256, trackedManifest,
  transactionResult, validateRootReceipt,
} from './eva-runtime-repair-protocol.mjs';

const ACTION = process.argv[2] || '--requests';
const ACTION_ID = process.argv[3] || '';
const APPROVAL_OPERATION = process.argv[4] || '';
const CONFIG = ACTION === '--approve'
  ? process.argv[5] || '/etc/eva-runtime-self-repair.json'
  : process.argv[3] || '/etc/eva-runtime-self-repair.json';
const GATES = [
  { name: 'typecheck', args: ['scripts/quality-gate.mjs', 'typecheck'] },
  { name: 'lint', args: ['scripts/quality-gate.mjs', 'lint'] },
  { name: 'complexity', args: ['scripts/quality-gate.mjs', 'complexity'] },
  { name: 'coverage' }, { name: 'tests' },
];
const ROOT_GATE_TEST_POLICY = Object.freeze({
  contract: 'EvaRootGateTestSelection/v2',
  globs: Object.freeze(['test/*.test.js', 'test/*.test.mjs', 'scripts/*.test.mjs']),
  directExclusions: Object.freeze(['scripts/run-isolated-tests.test.mjs']),
  osRunner: 'scripts/run-isolated-tests.mjs',
  osTests: Object.freeze(['scripts/run-isolated-tests.test.mjs']),
});
const ROOT_GATE_TEST_PATTERN = /^(?:test\/[^/]+\.test\.(?:js|mjs)|scripts\/[^/]+\.test\.mjs)$/u;
const NODE_WASM_FLAG = '--disable-wasm-trap-handler';
const ISOLATED_TEST_RELEASE_SHA256 = '35a09a439574c5e661dca39d83fc9fbf0415e492a1893d4ad487778e6413c35c';
const ISOLATED_TEST_BOOTSTRAP = '/usr/local/libexec/eva/run-isolated-tests-bootstrap';
const ISOLATED_TEST_GATE_USER = 'eva-test-gate';
const SERVICE_USER = 'eva';
const RUNTIME_READ_GROUP = 'eva-runtime-read';
const ISOLATED_TEST_RELEASE_PATHS = Object.freeze([
  ISOLATED_TEST_BOOTSTRAP,
  '/usr/local/libexec/eva/run-isolated-tests-bootstrap.c',
  '/usr/local/libexec/eva/run-isolated-tests.mjs',
  '/usr/local/libexec/eva/run-isolated-tests-release.json',
]);
const EXPECTED_ISOLATED_TEST_RELEASE = Object.freeze({
  schema: 1,
  installPath: ISOLATED_TEST_BOOTSTRAP,
  artifact: Object.freeze({
    format: 'static-linux-x86_64-et_exec', owner: 'root', group: 'root', mode: '0755',
    writableByExecutionIdentity: false,
  }),
  execution: Object.freeze({
    runAsRoot: false, dedicatedUnprivilegedIdentity: true,
    invokeDirectlyOutsideNodeAndNpm: true,
    callerContract: 'A separate root-owned gate must invoke the attested path as a dedicated non-root identity; the ELF rejects uid/euid 0.',
  }),
  integration: Object.freeze({
    npmPackageScripts: 'developer-alias-only-not-a-pre-node-trust-boundary',
    rootGate: 'separate-required',
    releaseStatus: 'no-go-until-root-owned-install-and-rootgate-integration',
  }),
  claims: Object.freeze({
    normalAccidentalComponent: true, hostileSameUid: false,
    normalNestedNodeAvailability: true, hostileTestEnvironmentWipeAvailability: false,
    sameUidCoveragePostprocessing: false, releaseRootGate: false,
  }),
  build: Object.freeze({
    compiler: '/usr/bin/x86_64-linux-gnu-gcc-13',
    arguments: '-static -std=c11 -O2 -Wall -Wextra -Werror -fno-ident -fno-record-gcc-switches -Wl,--build-id=none -s',
  }),
  sha256: Object.freeze({
    bootstrap: '320d84306d8f23d21ea2bad40bbfe8bff1ca7ab3acf60c6baba3f5f59a1a7882',
    bootstrapSource: 'fa7234de4eced386f02c37555de31fbbbcea8b3f5fc01259bc9ffcd467f8628b',
    runner: '012dc998e0804eaa6216c8acbde1d1dd83f591c8cd0d2a239ba288234cc19d27',
  }),
});
const ROOT_GATE_LAUNCHER = String.raw`
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const NODE_WASM_FLAG = '--disable-wasm-trap-handler';
const gateArguments = JSON.parse(process.argv[1] || 'null');
if (!Array.isArray(gateArguments) || gateArguments[0] !== NODE_WASM_FLAG
  || gateArguments.some((argument) => typeof argument !== 'string')) {
  throw new Error('ongeldige root-gateargumenten');
}
const dataDir = fs.mkdtempSync('/tmp/eva-runtime-rootgate-data-');
try {
  fs.chmodSync(dataDir, 0o700);
  const stat = fs.lstatSync(dataDir);
  if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid()
    || (stat.mode & 0o777) !== 0o700 || path.dirname(fs.realpathSync.native(dataDir)) !== '/tmp') {
    throw new Error('root-gate EVA_DATA_DIR is niet eigen private strict-/tmp authority');
  }
  const result = spawnSync('/usr/bin/node', gateArguments, {
    stdio: ['ignore', 'inherit', 'inherit'],
    env: { ...process.env, NODE_OPTIONS: NODE_WASM_FLAG, EVA_DATA_DIR: dataDir },
  });
  if (result.error) throw result.error;
  process.exitCode = Number.isInteger(result.status) ? result.status : 1;
} finally {
  fs.rmSync(dataDir, { recursive: true, force: true });
}
`;

function exactObjectShape(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(Object.keys(expected).sort())) {
    return false;
  }
  return Object.entries(expected).every(([key, expectedValue]) => {
    const actual = value[key];
    if (expectedValue && typeof expectedValue === 'object') {
      return exactObjectShape(actual, expectedValue);
    }
    return actual === expectedValue;
  });
}

function staticLinuxElf(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 64 || bytes[0] !== 0x7f
    || bytes.subarray(1, 4).toString('ascii') !== 'ELF' || bytes[4] !== 2 || bytes[5] !== 1
    || bytes.readUInt16LE(16) !== 2 || bytes.readUInt16LE(18) !== 62) return false;
  const programOffset = Number(bytes.readBigUInt64LE(32));
  const entrySize = bytes.readUInt16LE(54); const entryCount = bytes.readUInt16LE(56);
  if (!Number.isSafeInteger(programOffset) || entrySize < 56 || entryCount < 1 || entryCount > 128
    || programOffset + (entrySize * entryCount) > bytes.length) return false;
  for (let index = 0; index < entryCount; index += 1) {
    const type = bytes.readUInt32LE(programOffset + (index * entrySize));
    if (type === 2 || type === 3) return false;
  }
  return true;
}

/**
 * @param {{release?: Buffer, bootstrap?: Buffer, bootstrapSource?: Buffer, runner?: Buffer}} [artifacts]
 */
export function validateIsolatedTestRelease({ release, bootstrap, bootstrapSource, runner } = {}) {
  /** @type {Array<[string, Buffer | undefined, number]>} */
  const artifacts = [
    ['release', release, 16 * 1024], ['bootstrap', bootstrap, 2 * 1024 * 1024],
    ['bootstrapSource', bootstrapSource, 256 * 1024], ['runner', runner, 2 * 1024 * 1024],
  ];
  for (const [name, bytes, maximum] of artifacts) {
    if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > maximum) {
      throw new Error(`ongeldig statisch runnerrelease-artefact: ${name}`);
    }
  }
  if (sha256(release) !== ISOLATED_TEST_RELEASE_SHA256) {
    throw new Error('statisch runnerreleasecontract heeft niet de exact goedgekeurde hash');
  }
  let contract;
  try { contract = JSON.parse(release.toString('utf8')); } catch {
    throw new Error('statisch runnerreleasecontract is geen JSON');
  }
  if (!exactObjectShape(contract, EXPECTED_ISOLATED_TEST_RELEASE)
    || sha256(bootstrap) !== contract.sha256.bootstrap
    || sha256(bootstrapSource) !== contract.sha256.bootstrapSource
    || sha256(runner) !== contract.sha256.runner || !staticLinuxElf(bootstrap)) {
    throw new Error('statisch runnerreleasecontract, claims, hashes of ELF-formaat wijken af');
  }
  return EXPECTED_ISOLATED_TEST_RELEASE;
}

function assertInstalledIsolatedTestRelease(rootUid = 0) {
  const directory = path.dirname(ISOLATED_TEST_BOOTSTRAP);
  if (!securePath(directory, { kind: 'directory', uid: rootUid, gid: rootUid, mode: 0o755 })) {
    throw new Error('geïnstalleerde runnerreleasedirectory is niet root-owned 0755');
  }
  const bytes = {
    bootstrap: readRegular(ISOLATED_TEST_RELEASE_PATHS[0], {
      uid: rootUid, gid: rootUid, mode: 0o755, nlink: 1, maximum: 2 * 1024 * 1024,
    }),
    bootstrapSource: readRegular(ISOLATED_TEST_RELEASE_PATHS[1], {
      uid: rootUid, gid: rootUid, mode: 0o444, nlink: 1, maximum: 256 * 1024,
    }),
    runner: readRegular(ISOLATED_TEST_RELEASE_PATHS[2], {
      uid: rootUid, gid: rootUid, mode: 0o444, nlink: 1, maximum: 2 * 1024 * 1024,
    }),
    release: readRegular(ISOLATED_TEST_RELEASE_PATHS[3], {
      uid: rootUid, gid: rootUid, mode: 0o444, nlink: 1, maximum: 16 * 1024,
    }),
  };
  return validateIsolatedTestRelease(bytes);
}

function unitDirectiveIndex(lines, name) {
  const canonical = new RegExp(`^${name}=`, 'u');
  const indexes = lines.flatMap((line, index) => canonical.test(line) ? [index] : []);
  if (indexes.length !== 1) throw new Error(`service-unit vereist exact één canonieke ${name}-regel`);
  return indexes[0];
}

export function renderEvaServiceUnit(template, values) {
  if (typeof template !== 'string' || template.includes('\r') || template.includes('\0')
    || !values || typeof values !== 'object') throw new Error('ongeldige service-unittemplate');
  const serviceUser = Object.hasOwn(values, 'serviceUser') ? String(values.serviceUser) : '';
  const runtimeReadGroup = Object.hasOwn(values, 'runtimeReadGroup')
    ? String(values.runtimeReadGroup) : '';
  const projectRoot = Object.hasOwn(values, 'projectRoot') ? String(values.projectRoot) : '';
  if (!/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/u.test(serviceUser)
    || !/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/u.test(runtimeReadGroup)
    || !/^\/[A-Za-z0-9._/-]+$/u.test(projectRoot) || path.resolve(projectRoot) !== projectRoot) {
    throw new Error('ongeldige service-unitwaarden');
  }
  const lines = template.split('\n');
  const activeDataLines = lines.filter((line) => !/^\s*[#;]/u.test(line)
    && line.includes('EVA_DATA_DIR'));
  if (activeDataLines.length !== 1 || !/^Environment=EVA_DATA_DIR=[^\s]*$/u.test(activeDataLines[0])) {
    throw new Error('service-unit vereist exact één canonieke EVA_DATA_DIR-regel');
  }
  const dataIndex = lines.indexOf(activeDataLines[0]);
  const replacements = new Map([
    ['User', serviceUser], ['Group', serviceUser],
    ['SupplementaryGroups', runtimeReadGroup], ['WorkingDirectory', projectRoot],
    ['ExecStart', `/usr/bin/node ${projectRoot}/src/index.js`],
  ]);
  for (const [name, value] of replacements) lines[unitDirectiveIndex(lines, name)] = `${name}=${value}`;
  lines[dataIndex] = `Environment=EVA_DATA_DIR=${projectRoot}/data`;
  const rendered = lines.join('\n');
  const outputDataLines = rendered.split('\n').filter((line) => !/^\s*[#;]/u.test(line)
    && line.includes('EVA_DATA_DIR'));
  if (outputDataLines.length !== 1
    || outputDataLines[0] !== `Environment=EVA_DATA_DIR=${projectRoot}/data`) {
    throw new Error('gerenderde service-unit heeft geen exacte EVA_DATA_DIR-postcondition');
  }
  return rendered;
}

function atomicReplace(file, content, mode, owner = null) {
  const directory = path.dirname(file);
  const temporary = path.join(directory, `.eva-runtime-${process.pid}-${randomUUID()}`);
  const fd = fs.openSync(temporary, fs.constants.O_CREAT | fs.constants.O_EXCL
    | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, mode);
  try {
    fs.writeFileSync(fd, content);
    if (owner) fs.fchownSync(fd, owner.uid, owner.gid);
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  fs.renameSync(temporary, file); fs.chmodSync(file, mode); fsyncDirectory(directory);
}

async function withRootLock(config, action) {
  const lock = path.join(config.maintainer_state_dir, 'maintainer.lock');
  let descriptor;
  try { descriptor = fs.openSync(lock, 'wx', 0o600); } catch {
    let stale = false;
    try {
      const pid = Number(readRegular(lock, { uid: 0, maximum: 128 }).toString('utf8').trim());
      try { process.kill(pid, 0); } catch { stale = true; }
    } catch { /* een onleesbare lock wordt niet blind verwijderd */ }
    if (!stale) throw new Error('een andere root-maintainerbewerking is actief');
    fs.rmSync(lock); descriptor = fs.openSync(lock, 'wx', 0o600);
  }
  try {
    fs.writeFileSync(descriptor, `${process.pid}\n`); fs.fsyncSync(descriptor);
    return await action();
  } finally {
    fs.closeSync(descriptor); fs.rmSync(lock, { force: true }); fsyncDirectory(config.maintainer_state_dir);
  }
}

const rootFile = (file, uid = 0, mode = null) => securePath(file, { uid, mode });

function canonicalConfig(value) {
  const absolute = ['root', 'state_dir', 'maintainer_state_dir', 'release_manifest', 'tracked_manifest'];
  if (value.protocol !== PROTOCOL || absolute.some((name) => !path.isAbsolute(value[name]))
    || !String(value.service_user || '').trim() || !String(value.runtime_read_group || '').trim()) {
    throw new Error('ongeldige maintainerconfig');
  }
  for (const name of absolute) value[name] = path.resolve(value[name]);
  return value;
}

function assertConfigDomains(value) {
  const health = new URL(value.health_url || 'http://127.0.0.1:3210/api/health');
  const tokenFile = value.health_token_file;
  if (!/^\/[A-Za-z0-9._/-]+$/u.test(value.root)
    || !value.state_dir.startsWith(`${path.join(value.root, 'data')}${path.sep}`)
    || !value.maintainer_state_dir.startsWith('/var/lib/') || !value.release_manifest.startsWith('/etc/')
    || !value.tracked_manifest.startsWith(`${value.maintainer_state_dir}${path.sep}`)
    || fs.realpathSync(value.root) !== value.root || fs.realpathSync(path.join(value.root, 'data')) !== path.join(value.root, 'data')
    || health.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(health.hostname)
    || (tokenFile && (!/^\/etc\/[A-Za-z0-9._/-]+$/u.test(tokenFile) || path.resolve(tokenFile) !== tokenFile))) {
    throw new Error('state roots of healthendpoint vallen buiten hun vaste domein');
  }
}

function assertRelease(config, rootUid = 0) {
  const release = attestRelease(config.release_manifest, [
    ...RELEASE_PATHS, ...ISOLATED_TEST_RELEASE_PATHS, config.tracked_manifest,
  ], {
    uid: rootUid, gid: serviceGid(config), mode: 0o640, sourceRoot: config.root,
  });
  assertInstalledIsolatedTestRelease(rootUid);
  return release;
}

function readConfig() {
  if (process.getuid?.() !== 0 || !rootFile(CONFIG, 0, 0o640)) throw new Error('maintainerconfig is niet root-owned');
  const value = canonicalConfig(JSON.parse(fs.readFileSync(CONFIG, 'utf8')));
  if (!securePath(CONFIG, { uid: 0, gid: serviceGid(value), mode: 0o640 })) throw new Error('maintainerconfiggroep klopt niet');
  value.production_grants ??= [];
  assertConfigDomains(value); assertRelease(value);
  if (!Array.isArray(value.production_grants)) throw new Error('production_grants moet een lijst zijn');
  for (const grant of value.production_grants) dateMillis(grant?.expires_at, 'grant expires_at');
  return value;
}

function serviceUid(config) {
  const uid = Number(execFileSync('/usr/bin/id', ['-u', config.service_user], { encoding: 'utf8' }).trim());
  if (!Number.isInteger(uid) || uid === 0) throw new Error('service_user moet een bestaande unprivileged gebruiker zijn');
  return uid;
}

function serviceGid(config) {
  const gid = Number(execFileSync('/usr/bin/id', ['-g', config.service_user], { encoding: 'utf8' }).trim());
  if (!Number.isInteger(gid) || gid === 0) throw new Error('service_user moet een unprivileged groep hebben');
  return gid;
}

function runtimeReadGid(config) {
  const gid = Number(execFileSync('/usr/bin/getent', ['group', config.runtime_read_group], { encoding: 'utf8' }).split(':')[2]);
  if (!Number.isInteger(gid) || gid === 0) throw new Error('runtime_read_group moet een bestaande unprivileged groep zijn');
  return gid;
}

function exactIdentitySet(actual, expected, numeric = false) {
  if (!Array.isArray(actual) || actual.length !== expected.length
    || new Set(actual).size !== actual.length
    || (numeric && actual.some((value) => !Number.isInteger(value) || value <= 0))) return false;
  const normalize = numeric
    ? (values) => [...values].sort((left, right) => left - right)
    : (values) => values.map(String).sort();
  const normalized = normalize(actual); const wanted = normalize(expected);
  return normalized.every((value, index) => value === wanted[index]);
}

export function validateRuntimeIdentities(config, snapshot) {
  const service = snapshot?.service; const gate = snapshot?.gate; const runtime = snapshot?.runtime;
  const positive = (value) => Number.isInteger(value) && value > 0;
  if (config?.service_user !== SERVICE_USER || config?.runtime_read_group !== RUNTIME_READ_GROUP
    || !service || !gate || !runtime
    || service.user !== SERVICE_USER || gate.user !== ISOLATED_TEST_GATE_USER
    || runtime.group !== RUNTIME_READ_GROUP
    || !positive(service.uid) || !positive(gate.uid) || service.uid === gate.uid
    || !positive(service.gid) || !positive(gate.gid) || !positive(runtime.gid)
    || new Set([service.gid, gate.gid, runtime.gid]).size !== 3
    || service.primaryGroup !== SERVICE_USER || gate.primaryGroup !== ISOLATED_TEST_GATE_USER
    || service.home !== '/nonexistent' || gate.home !== '/nonexistent'
    || service.shell !== '/usr/sbin/nologin' || gate.shell !== '/usr/sbin/nologin'
    || !exactIdentitySet(service.groups, [service.gid, runtime.gid], true)
    || !exactIdentitySet(gate.groups, [gate.gid, runtime.gid], true)
    || !exactIdentitySet(service.groupNames, [SERVICE_USER, RUNTIME_READ_GROUP])
    || !exactIdentitySet(gate.groupNames, [ISOLATED_TEST_GATE_USER, RUNTIME_READ_GROUP])) {
    throw new Error('service- of testidentiteit wijkt af van het exacte non-rootcontract');
  }
  return Object.freeze({
    user: gate.user, uid: gate.uid, gid: gate.gid, primaryGroup: gate.primaryGroup,
    groups: Object.freeze([...gate.groups].sort((left, right) => left - right)),
    groupNames: Object.freeze([...gate.groupNames].sort()),
    home: gate.home, shell: gate.shell,
    serviceUser: service.user, serviceUid: service.uid, serviceGid: service.gid,
    servicePrimaryGroup: service.primaryGroup,
    serviceGroups: Object.freeze([...service.groups].sort((left, right) => left - right)),
    serviceGroupNames: Object.freeze([...service.groupNames].sort()),
    serviceHome: service.home, serviceShell: service.shell,
    runtimeReadGroup: runtime.group, runtimeReadGid: runtime.gid,
  });
}

function operatingIdentity(user) {
  const passwd = execFileSync('/usr/bin/getent', ['passwd', user], { encoding: 'utf8' }).trim().split(':');
  return {
    user: passwd[0], uid: Number(passwd[2]), gid: Number(passwd[3]),
    primaryGroup: execFileSync('/usr/bin/id', ['-gn', user], { encoding: 'utf8' }).trim(),
    groups: execFileSync('/usr/bin/id', ['-G', user], { encoding: 'utf8' }).trim()
      .split(/\s+/u).filter(Boolean).map(Number),
    groupNames: execFileSync('/usr/bin/id', ['-Gn', user], { encoding: 'utf8' }).trim()
      .split(/\s+/u).filter(Boolean),
    home: passwd.length === 7 ? passwd[5] : '', shell: passwd.length === 7 ? passwd[6] : '',
  };
}

function isolatedTestGateIdentity(config, { requireRuntime = true } = {}) {
  const runtimeGroup = execFileSync('/usr/bin/getent', ['group', config.runtime_read_group], {
    encoding: 'utf8',
  }).trim().split(':');
  const identity = validateRuntimeIdentities(config, {
    service: operatingIdentity(config.service_user),
    gate: operatingIdentity(ISOLATED_TEST_GATE_USER),
    runtime: { group: runtimeGroup[0], gid: Number(runtimeGroup[2]) },
  });
  if (requireRuntime) {
    const runtime = `/run/user/${identity.uid}`;
    const bus = path.join(runtime, 'bus');
    const busStat = fs.lstatSync(bus);
    if (!securePath(runtime, { kind: 'directory', uid: identity.uid, gid: identity.gid, mode: 0o700 })
      || !busStat.isSocket() || busStat.uid !== identity.uid || busStat.gid !== identity.gid) {
      throw new Error('dedicated geïsoleerde-testidentiteit mist een private actieve user-manager');
    }
  }
  return identity;
}

function maintainerDirectories(
  config, serviceGroup = serviceGid(config), runtimeGroup = runtimeReadGid(config),
) {
  const child = (name) => path.join(config.maintainer_state_dir, name);
  return [
    [config.maintainer_state_dir, 0o711, 0], [child('transactions'), 0o700, 0],
    [child('candidates'), 0o750, runtimeGroup], [child('responses'), 0o750, serviceGroup],
    [child('status'), 0o750, serviceGroup], [child('inbox'), 0o730, serviceGroup],
    [child('processing'), 0o700, 0], [child('quarantine'), 0o700, 0],
  ];
}

function assertMaintainerPaths(
  config, serviceGroup = serviceGid(config), runtimeGroup = runtimeReadGid(config),
) {
  for (const [directory, mode, ownerGid] of maintainerDirectories(config, serviceGroup, runtimeGroup)) {
    const stat = fs.lstatSync(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== 0 || stat.gid !== ownerGid
      || (stat.mode & 0o777) !== mode) throw new Error(`onveilige maintainerdirectory: ${directory}`);
  }
}

function assertServiceCannotWriteRuntime(config) {
  const result = spawnSync('/usr/sbin/runuser', [
    '-u', config.service_user, '--', '/usr/bin/find', config.root,
    '(', '-path', path.join(config.root, 'data'), '-o', '-path', path.join(config.root, '.git'), ')',
    '-prune', '-o', '-writable', '-print', '-quit',
  ], { encoding: 'utf8', timeout: 30_000 });
  if (result.status !== 0 || String(result.stdout || '').trim()) {
    throw new Error(`service_user heeft write-toegang tot de privilegegrens: ${String(result.stdout || '').trim() || 'controle faalde'}`);
  }
  for (const target of [
    ...RELEASE_PATHS, ...ISOLATED_TEST_RELEASE_PATHS,
    config.release_manifest, config.tracked_manifest, CONFIG,
  ]) {
    if (spawnSync('/usr/sbin/runuser', [
      '-u', config.service_user, '--', '/usr/bin/test', '!', '-w', target,
    ]).status !== 0) throw new Error(`service_user kan privilegebestand schrijven: ${target}`);
  }
}

function assertRuntimeAttestation(config, rootUid = 0) {
  assertRelease(config, rootUid);
  return dependencyTreeSha256(path.join(config.root, 'node_modules'), { uid: rootUid, readonly: true });
}

function preparePaths(config) {
  const ensureDirectory = (directory, mode, gid = 0) => {
    if (fs.existsSync(directory)) {
      const stat = fs.lstatSync(directory);
      if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`onveilig bestaand maintainerpad: ${directory}`);
    } else fs.mkdirSync(directory, { recursive: false, mode });
    fs.chownSync(directory, 0, gid); fs.chmodSync(directory, mode);
  };
  const identity = isolatedTestGateIdentity(config, { requireRuntime: false });
  for (const [directory, mode, ownerGid] of maintainerDirectories(
    config, identity.serviceGid, identity.runtimeReadGid,
  )) {
    ensureDirectory(directory, mode, ownerGid);
  }
  assertMaintainerPaths(config, identity.serviceGid, identity.runtimeReadGid);
}

function trackedFiles(config) {
  return trackedManifest(config.tracked_manifest, config.root, { uid: process.getuid?.() ?? 0, gid: serviceGid(config) });
}

function checkedSource(config, relative, tracked) {
  repairSourcePath(relative);
  if (!tracked.includes(relative)) throw new Error(`bron is niet getrackt: ${relative}`);
  const absolute = contained(config.root, relative);
  const stat = fs.lstatSync(absolute);
  if (!stat.isFile() || stat.isSymbolicLink() || fs.realpathSync(absolute) !== absolute) {
    throw new Error(`bronpad is niet regulier: ${relative}`);
  }
  return { absolute, stat };
}

const treeSha = (config, files, overrides = new Map(), root = config.root) => fileSetSha256(root, files, overrides);
const runtimeSha = (config, overrides = new Map(), root = config.root) => runtimeTreeSha256(root, overrides);

function loadProposal(config, request) {
  if (!ID_PATTERN.test(String(request.transaction_id || ''))
    || request.proposal_path !== `proposals/${request.transaction_id}/proposal.json`) {
    throw new Error('voorstelbinding is niet canoniek');
  }
  const txPath = contained(config.state_dir, request.proposal_path);
  if (!txPath.startsWith(`${path.join(config.state_dir, 'proposals')}${path.sep}`)) throw new Error('voorstelpad niet toegestaan');
  const tx = JSON.parse(readRegular(txPath, { uid: serviceUid(config) }).toString('utf8'));
  if (tx.protocol !== PROTOCOL || tx.id !== request.transaction_id || path.resolve(tx.root) !== config.root) {
    throw new Error('transactiebinding klopt niet');
  }
  const txDir = path.dirname(txPath);
  if (fs.realpathSync(txDir) !== txDir) throw new Error('journalmap bevat een symbolische omleiding');
  return { tx, txPath, txDir };
}

function readPatchArtifacts(tx, txDir, tracked) {
  if (JSON.stringify(tracked) !== JSON.stringify(tx.base?.files)) {
    throw new Error('tracked bestandsset veranderde');
  }
  if (![Array.isArray(tx.files), tx.files?.length > 0, tx.files?.length <= MAX_FILES,
    new Set(tx.files?.map((item) => item?.path)).size === tx.files?.length].every(Boolean)) {
    throw new Error('ongeldige bestandsset');
  }
  const patchFile = contained(txDir, tx.patch_file);
  const patch = readRegular(patchFile, { maximum: MAX_PATCH_BYTES });
  if (sha256(patch) !== tx.patch_sha256) throw new Error('patchhash klopt niet');
  const parsed = parseUnifiedDiff(patch);
  if (JSON.stringify(parsed.files.map((item) => item.path).sort())
    !== JSON.stringify(tx.files.map((item) => item.path).sort())) throw new Error('patch en transactiebestanden verschillen');
  return { patch, changedLines: parsed.changed_lines,
    byPath: new Map(parsed.files.map((item) => [item.path, item])) };
}

function artifactMetadataValid(item, source, before, supplied, after) {
  const owner = item.owner_uid == null
    || [source.stat.uid === item.owner_uid, source.stat.gid === item.owner_gid].every(Boolean);
  return [after.length <= MAX_PATCH_BYTES, supplied.compare(after) === 0,
    sha256(before) === item.preimage_sha256, sha256(after) === item.candidate_sha256,
    Number.isInteger(item.mode), (source.stat.mode & 0o777) === item.mode, owner].every(Boolean);
}

function readArtifact(config, txDir, tracked, item, filePatch, direction) {
  const source = checkedSource(config, item.path, tracked);
  const backup = contained(txDir, item.backup); const candidate = contained(txDir, item.candidate);
  if (![backup.startsWith(`${txDir}${path.sep}`), candidate.startsWith(`${txDir}${path.sep}`)].every(Boolean)) {
    throw new Error('artifactpad ongeldig');
  }
  const before = readRegular(backup); const supplied = readRegular(candidate);
  const after = applyHunks(before, filePatch); const live = readRegular(source.absolute);
  if (!artifactMetadataValid(item, source, before, supplied, after)) {
    throw new Error('root-afgeleide kandidaat, artifacthash, mode of ownership klopt niet');
  }
  const expectedLive = direction === 'apply' ? before : after;
  if (live.compare(expectedLive) !== 0) throw new Error(`preimage-live-CAS wijkt af: ${item.path}`);
  return { before, after, owner: { uid: source.stat.uid, gid: source.stat.gid } };
}

function derivedArtifactState(config, tracked, originals, replacements) {
  const dependency = dependencyTreeSha256(path.join(config.root, 'node_modules'), {
    uid: process.getuid?.() ?? 0, readonly: true,
  });
  return {
    currentTree: treeSha(config, tracked), currentRuntime: runtimeSha(config),
    base: { tracked_tree_sha256: treeSha(config, tracked, originals),
      runtime_sha256: runtimeSha(config, originals), dependency_tree_sha256: dependency },
    candidate: { tracked_tree_sha256: treeSha(config, tracked, replacements),
      runtime_sha256: runtimeSha(config, replacements), dependency_tree_sha256: dependency },
  };
}

function artifactStateValid(tx, state, direction) {
  const current = direction === 'apply' ? state.base : state.candidate;
  return [state.currentTree === current.tracked_tree_sha256,
    state.currentRuntime === current.runtime_sha256,
    tx.base?.tracked_tree_sha256 === state.base.tracked_tree_sha256,
    tx.base?.runtime_sha256 === state.base.runtime_sha256,
    tx.base?.dependency_tree_sha256 === state.base.dependency_tree_sha256,
    tx.candidate?.tracked_tree_sha256 === state.candidate.tracked_tree_sha256,
    tx.candidate?.runtime_sha256 === state.candidate.runtime_sha256,
    tx.candidate?.dependency_tree_sha256 === state.candidate.dependency_tree_sha256].every(Boolean);
}

function verifyArtifacts(config, tx, txDir, direction) {
  const tracked = trackedFiles(config); const parsed = readPatchArtifacts(tx, txDir, tracked);
  const replacements = new Map();
  const originals = new Map();
  const ownership = new Map();
  for (const item of tx.files) {
    const value = readArtifact(config, txDir, tracked, item, parsed.byPath.get(item.path), direction);
    replacements.set(item.path, value.after); originals.set(item.path, value.before);
    ownership.set(item.path, value.owner);
  }
  const state = derivedArtifactState(config, tracked, originals, replacements);
  if (!artifactStateValid(tx, state, direction)) {
    throw new Error('root-afgeleide whole-tree, runtime- of dependency-CAS wijkt af');
  }
  return {
    tracked, replacements, originals, ownership, patch: parsed.patch,
    base: state.base, candidate: state.candidate, changed_lines: parsed.changedLines,
  };
}

function sealCandidateDirectories(root, owner) {
  const pending = [root];
  while (pending.length) {
    const directory = pending.pop(); fs.chownSync(directory, owner.uid, owner.gid); fs.chmodSync(directory, 0o750);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) pending.push(path.join(directory, entry.name));
    }
  }
}

function copySealedCandidate(config, requestId, tracked, replacements) {
  const candidate = path.join(config.maintainer_state_dir, 'candidates', requestId);
  const owner = { uid: process.getuid?.() ?? 0, gid: runtimeReadGid(config) };
  const sources = tracked.map((relative) => {
    const source = contained(config.root, relative);
    try {
      const stat = fs.lstatSync(source);
      if (!stat.isFile() || stat.isSymbolicLink() || fs.realpathSync(source) !== source) {
        throw new Error('niet-regulier');
      }
      return { relative, source, mode: stat.mode };
    } catch {
      throw new Error(`tracked manifest bevat geen regulier bronbestand: ${relative}`);
    }
  });
  fs.rmSync(candidate, { recursive: true, force: true });
  try {
    fs.mkdirSync(candidate, { recursive: true, mode: 0o750 });
    fs.chownSync(candidate, owner.uid, owner.gid);
    for (const { relative, source, mode } of sources) {
      const target = path.join(candidate, relative);
      const sealedMode = (mode & 0o111) === 0 ? 0o440 : 0o550;
      fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o750 });
      fs.chownSync(path.dirname(target), owner.uid, owner.gid);
      const contents = replacements.has(relative) ? replacements.get(relative) : fs.readFileSync(source);
      fs.writeFileSync(target, contents, { mode: sealedMode });
      fs.chownSync(target, owner.uid, owner.gid); fs.chmodSync(target, sealedMode);
    }
    fs.mkdirSync(path.join(candidate, 'node_modules'), { mode: 0o750 });
    fs.chownSync(path.join(candidate, 'node_modules'), owner.uid, owner.gid);
    fs.mkdirSync(path.join(candidate, 'data'), { mode: 0o750 });
    fs.chownSync(path.join(candidate, 'data'), owner.uid, owner.gid);
    sealCandidateDirectories(candidate, owner);
    return candidate;
  } catch (error) {
    fs.rmSync(candidate, { recursive: true, force: true });
    throw error;
  }
}

function rootGateTestSelection(files) {
  const tracked = new Set(files);
  if (new Set(ROOT_GATE_TEST_POLICY.directExclusions).size
      !== ROOT_GATE_TEST_POLICY.directExclusions.length
      || ROOT_GATE_TEST_POLICY.directExclusions.length !== ROOT_GATE_TEST_POLICY.osTests.length
      || ROOT_GATE_TEST_POLICY.directExclusions.some(
        (file) => !ROOT_GATE_TEST_POLICY.osTests.includes(file),
      )) throw new Error('root-gatepolicy OS-partitie wijkt af');
  for (const required of [ROOT_GATE_TEST_POLICY.osRunner, ...ROOT_GATE_TEST_POLICY.osTests]) {
    if (!tracked.has(required)) throw new Error(`root-gatepolicy mist getrackt OS-testbestand: ${required}`);
  }
  const tests = files.filter((file) => ROOT_GATE_TEST_PATTERN.test(file));
  if (new Set(tests).size !== tests.length) {
    throw new Error('root-gatepolicy bevat een dubbel testbestand');
  }
  const direct = tests.filter((file) => !ROOT_GATE_TEST_POLICY.directExclusions.includes(file));
  if (direct.length === 0) throw new Error('root-gatepolicy selecteerde geen directe kandidaattests');
  const os = ROOT_GATE_TEST_POLICY.osTests.filter((file) => tests.includes(file));
  if (direct.length + os.length !== tests.length
      || new Set([...direct, ...os]).size !== direct.length + os.length) {
    throw new Error('root-gatepolicy zou een test dubbel uitvoeren');
  }
  return Object.freeze({ direct: Object.freeze(direct), os: Object.freeze(os) });
}

function attestCandidateIsolatedTestRelease(candidate, evidence) {
  const rootUid = evidence?.rootUid;
  const runtimeReadGid = evidence?.runtimeReadGid;
  if (!Number.isInteger(rootUid) || rootUid < 0 || !Number.isInteger(runtimeReadGid)
    || runtimeReadGid < 0 || evidence.contract !== EXPECTED_ISOLATED_TEST_RELEASE) {
    throw new Error('root-gate mist geïnstalleerd release- en identitybewijs');
  }
  const candidateStat = fs.lstatSync(candidate);
  if (!candidateStat.isDirectory() || candidateStat.isSymbolicLink()
    || candidateStat.uid !== rootUid || candidateStat.gid !== runtimeReadGid
    || (candidateStat.mode & 0o777) !== 0o750 || fs.realpathSync(candidate) !== candidate) {
    throw new Error('OS-rootgate kandidaatroot wijkt af van de sealed snapshotauthority');
  }
  for (const directory of ['data', 'node_modules']) {
    if (!securePath(path.join(candidate, directory), {
      kind: 'directory', uid: rootUid, gid: runtimeReadGid, mode: 0o750,
    })) throw new Error(`OS-rootgate ${directory} is niet root-owned read-only`);
  }
  const scripts = path.join(candidate, 'scripts');
  const bytes = {
    bootstrap: readRegular(path.join(scripts, 'run-isolated-tests-bootstrap'), {
      uid: rootUid, gid: runtimeReadGid, mode: 0o550, nlink: 1, maximum: 2 * 1024 * 1024,
    }),
    bootstrapSource: readRegular(path.join(scripts, 'run-isolated-tests-bootstrap.c'), {
      uid: rootUid, gid: runtimeReadGid, mode: 0o440, nlink: 1, maximum: 256 * 1024,
    }),
    release: readRegular(path.join(scripts, 'run-isolated-tests-release.json'), {
      uid: rootUid, gid: runtimeReadGid, mode: 0o440, nlink: 1, maximum: 16 * 1024,
    }),
    runner: readRegular(path.join(scripts, 'run-isolated-tests.mjs'), {
      uid: rootUid, gid: runtimeReadGid, mode: 0o440, nlink: 1, maximum: 2 * 1024 * 1024,
    }),
  };
  try { validateIsolatedTestRelease(bytes); } catch {
    throw new Error('kandidaat-runnerbytes wijken af van het geïnstalleerde releasecontract');
  }
}

function rootGateInvocation(config, candidate, requestId, gate, selectedTests = null) {
  const unit = `eva-runtime-rootgate-${requestId.slice(0, 8)}-${gate.name}`;
  const files = selectedTests || trackedFiles(config);
  const tests = gate.name === 'tests' ? rootGateTestSelection(files).direct
    : files.filter((file) => /^test\/.+\.test\.js$|^scripts\/.+\.test\.mjs$/u.test(file));
  const args = gate.name === 'tests' ? [NODE_WASM_FLAG, '--test', ...tests]
    : gate.name === 'coverage' ? [
      NODE_WASM_FLAG, 'node_modules/c8/bin/c8.js', '--all=true',
      '--include=deploy/eva-runtime-maintainer.mjs',
      '--include=deploy/eva-runtime-repair-protocol.mjs',
      '--reporter=text-summary', `--reports-dir=/run/${unit}/coverage`,
      `--temp-directory=/run/${unit}/c8`, '--check-coverage=true', '--per-file=true',
      '--lines=70', '--functions=0', '--branches=0', '--statements=0',
      '/usr/bin/node', NODE_WASM_FLAG, '--test', 'test/runtime-self-repair.test.js',
    ] : [NODE_WASM_FLAG, ...gate.args];
  return {
    command: '/usr/bin/systemd-run',
    args: [
      '--quiet', '--wait', '--pipe', '--collect', `--unit=${unit}`,
      '--property=DynamicUser=yes', `--property=RuntimeDirectory=${unit}`,
      '--property=RuntimeDirectoryMode=0700',
      '--property=NoNewPrivileges=yes', '--property=ProtectSystem=strict', '--property=ProtectHome=tmpfs',
      `--property=BindReadOnlyPaths=${candidate}:/run/eva-candidate`,
      `--property=BindReadOnlyPaths=${path.join(config.root, 'node_modules')}:/run/eva-candidate/node_modules`,
      '--property=WorkingDirectory=/run/eva-candidate',
      '--property=PrivateTmp=yes', '--property=PrivateDevices=yes', '--property=PrivateNetwork=yes',
      `--property=SupplementaryGroups=${config.runtime_read_group}`,
      '--property=ProtectProc=invisible', '--property=ProcSubset=pid', '--property=RestrictSUIDSGID=yes',
      '--property=RestrictNamespaces=yes', '--property=LockPersonality=yes',
      '--property=MemoryMax=1536M', '--property=TasksMax=128', '--property=RuntimeMaxSec=240',
      '--setenv=NODE_ENV=test', '--setenv=EVA_ROOT_GATE_DIRECT=1',
      `--setenv=NODE_OPTIONS=${NODE_WASM_FLAG}`,
      '--setenv=PATH=/usr/local/bin:/usr/bin:/bin',
      '--setenv=LANG=C.UTF-8', '--setenv=LC_ALL=C.UTF-8', '--setenv=TZ=Europe/Amsterdam',
      '/usr/bin/node', NODE_WASM_FLAG, '--input-type=module', '--eval',
      ROOT_GATE_LAUNCHER, JSON.stringify(args),
    ],
    options: {
      cwd: config.root, encoding: 'utf8', timeout: 250_000, maxBuffer: 32 * 1024 * 1024,
    },
  };
}

function rootOsGateInvocation(candidate, files, evidence) {
  const selection = rootGateTestSelection(files);
  attestCandidateIsolatedTestRelease(candidate, evidence);
  const identity = evidence.identity;
  if (!identity || identity.user !== ISOLATED_TEST_GATE_USER || !Number.isInteger(identity.uid)
    || identity.uid <= 0 || !Number.isInteger(identity.gid) || identity.gid <= 0
    || identity.primaryGroup !== ISOLATED_TEST_GATE_USER
    || identity.home !== '/nonexistent' || identity.shell !== '/usr/sbin/nologin'
    || identity.serviceUser !== SERVICE_USER || !Number.isInteger(identity.serviceUid)
    || identity.serviceUid <= 0 || identity.serviceUid === identity.uid
    || !Number.isInteger(identity.serviceGid) || identity.serviceGid <= 0
    || identity.servicePrimaryGroup !== SERVICE_USER
    || identity.serviceHome !== '/nonexistent' || identity.serviceShell !== '/usr/sbin/nologin'
    || identity.runtimeReadGroup !== RUNTIME_READ_GROUP
    || identity.runtimeReadGid !== evidence.runtimeReadGid
    || new Set([identity.gid, identity.serviceGid, identity.runtimeReadGid]).size !== 3
    || !exactIdentitySet(identity.groups, [identity.gid, identity.runtimeReadGid], true)
    || !exactIdentitySet(identity.groupNames, [ISOLATED_TEST_GATE_USER, RUNTIME_READ_GROUP])
    || !exactIdentitySet(identity.serviceGroups,
      [identity.serviceGid, identity.runtimeReadGid], true)
    || !exactIdentitySet(identity.serviceGroupNames, [SERVICE_USER, RUNTIME_READ_GROUP])) {
    throw new Error('OS-rootgate mist de dedicated non-root testidentiteit');
  }
  return {
    command: '/usr/bin/setpriv',
    args: [
      `--reuid=${identity.uid}`, `--regid=${identity.gid}`,
      `--groups=${identity.runtimeReadGid}`,
      '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all',
      '--no-new-privs', '--pdeathsig=keep', ISOLATED_TEST_BOOTSTRAP, ...selection.os,
    ],
    options: {
      cwd: candidate, encoding: 'utf8', timeout: 250_000, maxBuffer: 32 * 1024 * 1024,
      env: {
        PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8',
        XDG_RUNTIME_DIR: `/run/user/${identity.uid}`,
      },
    },
  };
}

function rootGatePlan(config, candidate, requestId, gate, selectedTests = null, evidence = null) {
  const files = selectedTests || trackedFiles(config);
  return Object.freeze({
    os: gate.name === 'tests'
      ? Object.freeze(rootOsGateInvocation(candidate, files, evidence)) : null,
    candidate: Object.freeze(rootGateInvocation(config, candidate, requestId, gate, files)),
  });
}

function executeRootGatePlan(plan, spawner = spawnSync) {
  const output = { status: 0, stdout: '', stderr: '', error: null };
  for (const invocation of [plan.os, plan.candidate].filter(Boolean)) {
    const result = spawner(invocation.command, invocation.args, invocation.options);
    output.stdout += String(result.stdout || ''); output.stderr += String(result.stderr || '');
    if (result.error || result.status !== 0) {
      output.status = result.status; output.error = result.error || null; return output;
    }
  }
  return output;
}

function rootGate(config, candidate, requestId, gate) {
  if (gate.name === 'tests' && (!securePath('/usr/bin/setpriv', {
    uid: 0, gid: 0, mode: 0o755, nlink: 1,
  }) || fs.realpathSync('/usr/bin/setpriv') !== '/usr/bin/setpriv')) {
    throw new Error('root OS-gate mist de vaste root-owned setpriv identitygrens');
  }
  const evidence = gate.name === 'tests' ? {
    contract: assertInstalledIsolatedTestRelease(),
    identity: isolatedTestGateIdentity(config),
    rootUid: 0,
    runtimeReadGid: runtimeReadGid(config),
  } : null;
  return executeRootGatePlan(rootGatePlan(config, candidate, requestId, gate, null, evidence));
}

function runRootGates(config, request, tx, artifacts, gateRunner = rootGate) {
  const candidate = copySealedCandidate(config, request.request_id, artifacts.tracked, artifacts.replacements);
  const results = [];
  try {
    if (GATES.length !== ROOT_GATE_NAMES.length
      || GATES.some((gate, index) => gate.name !== ROOT_GATE_NAMES[index])) {
      throw new Error('root-gateregister wijkt af van het gedeelde receipt-schema');
    }
    for (const gate of GATES) {
      const started = Date.now(); const result = gateRunner(config, candidate, request.request_id, gate);
      const stdout = String(result.stdout || ''); const stderr = String(result.stderr || result.error?.message || '');
      results.push({ name: gate.name, exit_code: result.status, stdout_sha256: sha256(stdout), stderr_sha256: sha256(stderr), duration_ms: Date.now() - started });
      if (result.status !== 0) throw new Error(`root-gate ${gate.name} faalde`);
    }
    const dependency = dependencyTreeSha256(path.join(config.root, 'node_modules'), {
      uid: process.getuid?.() ?? 0, readonly: true,
    });
    if (treeSha(config, artifacts.tracked, new Map(), candidate) !== tx.candidate.tracked_tree_sha256
      || runtimeSha(config, new Map(), candidate) !== tx.candidate.runtime_sha256
      || dependency !== tx.candidate.dependency_tree_sha256) throw new Error('sealed kandidaat of dependencyboom veranderde tijdens gates');
    return results;
  } finally { fs.rmSync(candidate, { recursive: true, force: true }); }
}

function sealTransaction(config, sourceTx, request, artifacts, runtime = {}) {
  const parent = path.join(config.maintainer_state_dir, 'transactions');
  const finalDir = path.join(parent, sourceTx.id);
  if (fs.existsSync(finalDir)) throw new Error('canonical transactie-id bestaat al');
  const txDir = path.join(parent, `.${sourceTx.id}.${randomUUID()}.tmp`);
  fs.mkdirSync(txDir, { recursive: false, mode: 0o700 });
  const tx = structuredClone(sourceTx);
  tx.base = { files: artifacts.tracked, ...artifacts.base };
  tx.candidate = { ...artifacts.candidate };
  tx.active_runtime = 'base';
  try {
    for (const item of tx.files) {
      const owner = artifacts.ownership.get(item.path);
      if (!owner) throw new Error(`root-ownership ontbreekt voor ${item.path}`);
      item.owner_uid = owner.uid; item.owner_gid = owner.gid;
      atomicBlob(contained(txDir, item.backup), artifacts.originals.get(item.path));
      checkpoint(runtime, `seal:${item.path}:backup`);
      atomicBlob(contained(txDir, item.candidate), artifacts.replacements.get(item.path));
      checkpoint(runtime, `seal:${item.path}:candidate`);
    }
    atomicBlob(contained(txDir, tx.patch_file), artifacts.patch); checkpoint(runtime, 'seal:patch');
    tx.sealed_at = new Date().toISOString();
    tx.untrusted_audit = { actor: tx.actor || null, origin: request.origin || null };
    delete tx.actor;
    tx.root_receipt = {
      protocol: PROTOCOL, transaction_id: tx.id, gates: [],
      candidate_tree_sha256: tx.candidate.tracked_tree_sha256,
      full_runtime_sha256: tx.candidate.runtime_sha256,
      dependency_tree_sha256: tx.candidate.dependency_tree_sha256,
      issued_at: tx.sealed_at,
      expires_at: new Date(Date.now() + 24 * 60 * 60_000).toISOString(),
      risk: 'review', changed_lines: artifacts.changed_lines,
    };
    tx.root_receipt.sha256 = sha256(JSON.stringify(tx.root_receipt));
    atomicJson(path.join(txDir, 'transaction.json'), tx); checkpoint(runtime, 'seal:transaction');
    fs.renameSync(txDir, finalDir); fsyncDirectory(parent); checkpoint(runtime, 'seal:rename');
    return { tx, txPath: path.join(finalDir, 'transaction.json'), txDir: finalDir };
  } catch (error) {
    if (!fs.existsSync(finalDir)) fs.rmSync(txDir, { recursive: true, force: true });
    throw error;
  }
}

function loadSealed(config, transactionId, rootUid = 0) {
  if (!ID_PATTERN.test(String(transactionId || ''))) throw new Error('ongeldig transactie-id');
  const txDir = path.join(config.maintainer_state_dir, 'transactions', transactionId);
  const txPath = path.join(txDir, 'transaction.json');
  const tx = JSON.parse(readRegular(txPath, { uid: rootUid }).toString('utf8'));
  if (tx.protocol !== PROTOCOL || tx.id !== transactionId || !tx.root_receipt?.sha256) {
    throw new Error('canonical transactiejournal ongeldig');
  }
  validateRootReceipt(tx);
  return { tx, txPath, txDir };
}

function updateRootReceiptHash(tx) {
  const receipt = { ...tx.root_receipt }; delete receipt.sha256;
  tx.root_receipt.sha256 = sha256(JSON.stringify(receipt));
}

const PHASE_REASONS = Object.freeze({
  root_test_failed: 'de rootproef faalde; deze kandidaat wordt niet toegepast',
  mutation_failed_rolled_back: 'de productionmutatie faalde en is teruggedraaid',
  mutation_crash_rolled_back: 'een onderbroken productionmutatie is teruggedraaid',
  recovery_live_unverified: 'de herstelde runtime is nog niet live bevestigd',
  recovery_manual_intervention: 'automatisch herstel is uitgeput; rootinterventie is nodig',
  recovery_tree_conflict: 'de herstelboom wijkt af; rootinterventie is nodig',
  boot_failed_rolled_back_live: 'de doelruntime faalde; de vorige runtime is live hersteld',
});

function writeStatus(config, tx, extras = {}) {
  const reason = phaseContract(tx.status).reason ? extras.reason || PHASE_REASONS[tx.status] : null;
  const value = transactionResult(tx, {
    updated_at: new Date().toISOString(), approval_binding: null, ...extras, reason,
  });
  atomicJson(path.join(config.maintainer_state_dir, 'status', `${tx.id}.json`), value, {
    mode: 0o640, owner: { uid: process.getuid?.() ?? 0, gid: serviceGid(config) },
  });
  return value;
}

function matchingProductionGrant(config, tx, operation) {
  if (!Array.isArray(config.production_grants)) return false;
  for (const grant of config.production_grants) {
    const expires = dateMillis(grant?.expires_at, 'grant expires_at');
    if (grant.operation === operation && grant.transaction_id === tx.id
      && grant.candidate_tree_sha256 === tx.candidate.tracked_tree_sha256
      && HASH_PATTERN.test(String(grant.candidate_tree_sha256 || ''))
      && Date.now() < expires) return true;
  }
  return false;
}

function checkpoint(runtime, name) {
  if (typeof runtime.checkpoint === 'function') runtime.checkpoint(name);
}

function journal(config, tx, txPath, marker, runtime = {}, label = 'journal') {
  const markerPath = path.join(config.maintainer_state_dir, 'current-apply.json');
  tx.mutation = marker; const active = mutationInProgress(marker);
  if (active) {
    let pointer = null;
    try { pointer = JSON.parse(readRegular(markerPath, { uid: runtime.rootUid ?? 0 }).toString('utf8')); } catch { /* nog geen actieve pointer */ }
    if (pointer?.transaction_id !== tx.id || pointer.direction !== marker.direction) {
      atomicJson(markerPath, { protocol: PROTOCOL, transaction_id: tx.id, direction: marker.direction });
      checkpoint(runtime, `${label}:marker`);
    }
  }
  atomicJson(txPath, tx); checkpoint(runtime, `${label}:transaction`);
  writeStatus(config, tx);
  checkpoint(runtime, `${label}:status`);
  if (!active && fs.existsSync(markerPath)) {
    checkpoint(runtime, `${label}:marker-before`);
    fs.rmSync(markerPath); fsyncDirectory(path.dirname(markerPath)); checkpoint(runtime, `${label}:marker-after`);
  }
}

function restore(config, tx, txDir, direction, artifactUid = 0) {
  const tracked = trackedFiles(config);
  for (const item of tx.files) {
    const source = checkedSource(config, item.path, tracked);
    const current = sha256(fs.readFileSync(source.absolute));
    const from = direction === 'apply' ? item.candidate_sha256 : item.preimage_sha256;
    const to = direction === 'apply' ? item.preimage_sha256 : item.candidate_sha256;
    if (current === to) continue;
    if (current !== from) throw new Error(`rollback-CAS conflict: ${item.path}`);
    const artifact = direction === 'apply' ? item.backup : item.candidate;
    const content = readRegular(contained(txDir, artifact), { uid: artifactUid });
    if (sha256(content) !== to) throw new Error(`rollbackartifact ongeldig: ${item.path}`);
    atomicReplace(source.absolute, content, item.mode, { uid: item.owner_uid, gid: item.owner_gid });
  }
}

const ACTIVE_PHASES = new Set(Object.entries(PHASE_MATRIX).filter(([, contract]) => contract.pointer).map(([name]) => name));
const mutationInProgress = (marker) => ACTIVE_PHASES.has(marker?.phase);

function activeMutation(config, rootUid = 0) {
  const current = loadCurrent(config, rootUid);
  if (!current) return null;
  const mutation = current.tx.mutation || mutationFor(current.tx, current.pointer.direction);
  return mutationInProgress(mutation) ? { ...current.tx, mutation } : null;
}

function mutationFor(tx, direction) {
  return {
    direction, phase: 'applying',
    expected_tree_sha256: tx[direction === 'apply' ? 'candidate' : 'base'].tracked_tree_sha256,
    previous_tree_sha256: tx[direction === 'apply' ? 'base' : 'candidate'].tracked_tree_sha256,
  };
}

function loadTestArtifacts(config, request, rootUid, runtime) {
  const sealedPath = path.join(config.maintainer_state_dir, 'transactions', request.transaction_id, 'transaction.json');
  let loaded = fs.existsSync(sealedPath) ? loadSealed(config, request.transaction_id, rootUid) : null;
  if (loaded?.tx.status === 'root_tested') return { loaded, replay: true };
  let artifacts;
  if (!loaded) {
    const source = loadProposal(config, request);
    if (!['staged', 'test_queued'].includes(source.tx.status)
      || Date.now() >= dateMillis(source.tx.expires_at, 'staging expires_at')) throw new Error('staging is verlopen of heeft een ongeldige fase');
    artifacts = verifyArtifacts(config, source.tx, source.txDir, 'apply');
    loaded = sealTransaction(config, source.tx, request, artifacts, runtime);
  } else artifacts = verifyArtifacts(config, loaded.tx, loaded.txDir, 'apply');
  return { loaded, artifacts };
}

function recordRootTestSuccess(config, loaded, gates, requestId, runtime) {
  loaded.tx.root_receipt.gates = gates;
  loaded.tx.root_receipt.issued_at = new Date().toISOString();
  loaded.tx.root_receipt.expires_at = new Date(Date.now() + 24 * 60 * 60_000).toISOString();
  loaded.tx.status = 'root_tested'; updateRootReceiptHash(loaded.tx);
  atomicJson(loaded.txPath, loaded.tx); checkpoint(runtime, 'root-test:transaction');
  const value = writeStatus(config, loaded.tx); checkpoint(runtime, 'root-test:status');
  return { ...value, request_id: requestId };
}

function recordRootTestFailure(config, loaded, error) {
  loaded.tx.status = 'root_test_failed';
  loaded.tx.failure_sha256 = sha256(String(error?.message || error));
  updateRootReceiptHash(loaded.tx); atomicJson(loaded.txPath, loaded.tx);
  writeStatus(config, loaded.tx, { reason: String(error?.message || error).slice(0, 300) });
}

function processTest(config, request, runtime = {}) {
  (runtime.assertBoundary || assertServiceCannotWriteRuntime)(config);
  const rootUid = runtime.rootUid ?? 0;
  (runtime.assertAttestation || assertRuntimeAttestation)(config, rootUid);
  if (activeMutation(config, rootUid)) throw new Error('een eerdere promotie wacht nog op een definitieve bootstatus');
  const prepared = loadTestArtifacts(config, request, rootUid, runtime);
  if (prepared.replay) return { ...writeStatus(config, prepared.loaded.tx), request_id: request.request_id };
  const { loaded, artifacts } = prepared;
  try {
    const rootGates = runRootGates(config, request, loaded.tx, artifacts, runtime.gateRunner || rootGate);
    if (treeSha(config, artifacts.tracked) !== loaded.tx.base.tracked_tree_sha256) {
      throw new Error('whole-tree CAS werd tijdens de rootproef stale');
    }
    return recordRootTestSuccess(config, loaded, rootGates, request.request_id, runtime);
  } catch (error) {
    if (error?.runtimeCrash !== true) recordRootTestFailure(config, loaded, error); throw error;
  }
}

function requestRestart() {
  execFileSync('/usr/bin/systemctl', ['try-restart', '--no-block', 'eva.service']);
}

async function withRestartHandoff(config, action, restart = requestRestart) {
  const required = await withRootLock(config, action);
  if (required === true) restart();
  return required;
}

function restartHandoff(tx, runtime) {
  checkpoint(runtime, 'restart:before');
  const deferred = typeof runtime.restart !== 'function';
  if (!deferred) runtime.restart(tx);
  checkpoint(runtime, 'restart:after');
  return deferred;
}

function existingMutation(config, tx, requestId, rootUid) {
  const active = activeMutation(config, rootUid);
  if (!active) return null;
  if (active.id === tx.id) return transactionResult(tx, { request_id: requestId });
  throw new Error('een andere promotie wacht nog op een definitieve bootstatus');
}

function mutateFiles(config, tx, txDir, tracked, direction, rootUid, runtime) {
  for (const item of tx.files) {
    const source = checkedSource(config, item.path, tracked);
    const expected = direction === 'apply' ? item.preimage_sha256 : item.candidate_sha256;
    const artifact = direction === 'apply' ? item.candidate : item.backup;
    const content = readRegular(contained(txDir, artifact), { uid: rootUid });
    if (sha256(fs.readFileSync(source.absolute)) !== expected) throw new Error(`file-CAS stale: ${item.path}`);
    checkpoint(runtime, `source:${item.path}:before`);
    atomicReplace(source.absolute, content, item.mode, { uid: item.owner_uid, gid: item.owner_gid });
    checkpoint(runtime, `source:${item.path}:after`);
  }
}

function completeMutation(config, loaded, marker, direction, requestId, runtime) {
  const { tx, txPath } = loaded;
  tx.status = direction === 'apply' ? 'applied_pending_boot' : 'rollback_pending_boot';
  marker.phase = tx.status; marker.updated_at = new Date().toISOString();
  tx.active_runtime = direction === 'apply' ? 'base' : 'candidate';
  journal(config, tx, txPath, marker, runtime, 'mutation-ready');
  return transactionResult(tx, {
    request_id: requestId, changed: true, restart_required: restartHandoff(tx, runtime),
  });
}

function failMutation(config, loaded, marker, direction, error, rootUid, runtime) {
  const { tx, txPath, txDir } = loaded;
  restore(config, tx, txDir, direction, rootUid);
  marker.phase = 'mutation_failed_rolled_back'; marker.error_sha256 = sha256(String(error?.message || error));
  tx.status = marker.phase; tx.active_runtime = direction === 'apply' ? 'base' : 'candidate';
  journal(config, tx, txPath, marker, runtime, 'mutation-failed');
}

function mutateSealed(config, loaded, direction, requestId, runtime = {}) {
  const { tx, txPath } = loaded; const rootUid = runtime.rootUid ?? 0;
  const active = existingMutation(config, tx, requestId, rootUid);
  if (active) return active;
  const tracked = trackedFiles(config);
  if (treeSha(config, tracked) !== (direction === 'apply'
    ? tx.base.tracked_tree_sha256 : tx.candidate.tracked_tree_sha256)) throw new Error('whole-tree CAS werd stale');
  const marker = mutationFor(tx, direction);
  tx.status = 'applying';
  if (direction === 'apply') {
    tx.promotion = { applied_at: new Date().toISOString() };
  }
  journal(config, tx, txPath, marker, runtime, 'mutation-start');
  try {
    mutateFiles(config, tx, loaded.txDir, tracked, direction, rootUid, runtime);
    if (treeSha(config, tracked) !== marker.expected_tree_sha256) throw new Error('eindboom wijkt af');
    return completeMutation(config, loaded, marker, direction, requestId, runtime);
  } catch (error) {
    if (error?.runtimeCrash === true) throw error;
    failMutation(config, loaded, marker, direction, error, rootUid, runtime); throw error;
  }
}

function completedPromotion(tx, direction, requestId) {
  if (direction === 'apply') {
    if (['applied_pending_boot', 'booting', 'committed'].includes(tx.status)) {
      return transactionResult(tx, { request_id: requestId });
    }
  }
  if (direction === 'rollback' && ['rolled_back', 'rollback_pending_boot'].includes(tx.status)) {
    return transactionResult(tx, { request_id: requestId });
  }
  return null;
}

function assertPromotionPhase(tx, request, direction) {
  if (direction === 'apply') {
    const valid = [['root_tested', 'approval_required'].includes(tx.status), !tx.promotion?.applied_at,
      Date.now() < dateMillis(tx.root_receipt.expires_at, 'rootreceipt expires_at'),
      request.root_receipt_sha256 === tx.root_receipt.sha256].every(Boolean);
    if (!valid) throw new Error('rootreceipt verlopen, gebruikt of niet gebonden');
    return;
  }
  if (!['committed', 'approval_required_rollback'].includes(tx.status)) {
    throw new Error('rollbackfase is niet toegestaan');
  }
}

function approvalRequired(config, loaded, direction, requestId) {
  const { tx } = loaded; tx.status = direction === 'apply' ? 'approval_required' : 'approval_required_rollback';
  atomicJson(loaded.txPath, tx);
  const value = writeStatus(config, tx, {
    approval_binding: {
      operation: direction, transaction_id: tx.id,
      candidate_tree_sha256: tx.candidate.tracked_tree_sha256,
      root_receipt_sha256: tx.root_receipt.sha256,
    },
  });
  return { ...value, request_id: requestId,
    reason: 'productionmutatie wacht op een exacte root-owned grant of offline rootgoedkeuring' };
}

function processPromotion(config, request, rootApproved = false, runtime = {}) {
  (runtime.assertBoundary || assertServiceCannotWriteRuntime)(config);
  const rootUid = runtime.rootUid ?? 0;
  (runtime.assertAttestation || assertRuntimeAttestation)(config, rootUid);
  const direction = request.operation;
  if (!['apply', 'rollback'].includes(direction)) throw new Error('ongeldige promotiebewerking');
  const loaded = loadSealed(config, request.transaction_id, rootUid); const { tx } = loaded;
  const completed = completedPromotion(tx, direction, request.request_id);
  if (completed) return completed;
  assertPromotionPhase(tx, request, direction);

  if (!rootApproved && !matchingProductionGrant(config, tx, direction)) {
    return approvalRequired(config, loaded, direction, request.request_id);
  }
  verifyArtifacts(config, tx, loaded.txDir, direction);
  return mutateSealed(config, loaded, direction, request.request_id, runtime);
}

function requestWindow(request, runtime) {
  const now = runtime.now || Date.now;
  const requested = dateMillis(request.requested_at, 'request requested_at');
  const notBefore = dateMillis(request.not_before, 'request not_before');
  const expiry = dateMillis(request.expires_at, 'request expires_at');
  const current = now();
  if (![expiry > current, requested <= current + 5_000, notBefore >= requested,
    expiry > notBefore, expiry - requested <= 10 * 60_000,
    notBefore - current <= 60_000].every(Boolean)) throw new Error('requestvenster of expiry ongeldig');
  return { now, notBefore, expiry, waitMs: Math.max(0, notBefore - current) };
}

async function awaitRequestWindow(request, runtime) {
  const timing = requestWindow(request, runtime);
  if (timing.waitMs) await (runtime.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms))))(timing.waitMs);
  const current = timing.now();
  if (current < timing.notBefore || current >= timing.expiry) {
    throw new Error('request verliep of werd niet geldig tijdens de wachttijd');
  }
  return timing;
}

function dispatchRequest(config, request, runtime) {
  return request.operation === 'test'
    ? processTest(config, request, runtime) : processPromotion(config, request, false, runtime);
}

async function processRequest(config, request, runtime = {}) {
  if (request.protocol !== PROTOCOL
    || !['test', 'apply', 'rollback'].includes(request.operation)
    || !HASH_PATTERN.test(request.origin?.turn_id_sha256 || '')
    || !HASH_PATTERN.test(request.origin?.input_sha256 || '')) {
    throw new Error('requestformaat of expiry ongeldig');
  }
  const timing = await awaitRequestWindow(request, runtime);
  const run = runtime.dispatchRequest || dispatchRequest;
  const execute = () => {
    const current = timing.now();
    if (current < timing.notBefore || current >= timing.expiry) {
      throw new Error('request verliep vóór de geserialiseerde dispatch');
    }
    return run(config, request, runtime);
  };
  return runtime.serialize
    ? runtime.serialize(execute) : execute();
}

function response(config, request, value) {
  atomicJson(path.join(config.maintainer_state_dir, 'responses', `${request.request_id}.json`), value, {
    mode: 0o640, owner: { uid: process.getuid?.() ?? 0, gid: serviceGid(config) },
  });
}

const MAX_REQUEST_BATCH = 16;

function claimInbox(config, limit) {
  const inbox = path.join(config.maintainer_state_dir, 'inbox');
  const processing = path.join(config.maintainer_state_dir, 'processing');
  const claimed = fs.readdirSync(processing).sort().slice(0, limit);
  for (const name of fs.readdirSync(inbox).sort()) {
    if (claimed.length >= limit) break;
    const target = path.join(processing, name);
    if (fs.existsSync(target)) continue;
    try {
      fs.renameSync(path.join(inbox, name), target); fsyncDirectory(inbox); fsyncDirectory(processing);
      claimed.push(name);
    } catch (error) { if (error?.code !== 'ENOENT') throw error; }
  }
  return claimed;
}

function claimedRequest(config, name) {
  if (!/^[a-f0-9-]+\.json$/iu.test(name)) throw new Error('ongeldige requestnaam');
  const file = path.join(config.maintainer_state_dir, 'processing', name);
  const request = JSON.parse(readRegular(file, {
    uid: serviceUid(config), gid: serviceGid(config), mode: 0o600, nlink: 1, maximum: 64 * 1024,
  }).toString('utf8'));
  if (request.protocol !== PROTOCOL || `${request.request_id}.json` !== name
    || !ID_PATTERN.test(String(request.request_id || ''))
    || !ID_PATTERN.test(String(request.transaction_id || ''))) throw new Error('ongeldig request');
  return request;
}

function rejectedResponse(config, request, error) {
  if (!request?.request_id) return;
  response(config, request, {
    protocol: PROTOCOL, request_id: request.request_id, ok: false, changed: false,
    verified: false, needs_followup: true, status: 'rejected',
    reason: String(error?.message || error).slice(0, 300),
  });
}

function releaseClaim(config, file) {
  const processing = path.dirname(file); let quarantined = false;
  try {
    const stat = fs.lstatSync(file);
    if (stat.isDirectory() && !stat.isSymbolicLink()) {
      const quarantine = path.join(config.maintainer_state_dir, 'quarantine');
      fs.renameSync(file, path.join(quarantine, sha256(`${path.basename(file)}:${randomUUID()}`)));
      fsyncDirectory(quarantine); quarantined = true;
    } else fs.rmSync(file, { force: true });
  } catch (error) { if (error?.code !== 'ENOENT') throw error; }
  fsyncDirectory(processing); return quarantined;
}

async function processClaimed(config, name, runtime) {
  const file = path.join(config.maintainer_state_dir, 'processing', name); let request; let restart = false;
  try {
    request = claimedRequest(config, name);
    const existing = path.join(config.maintainer_state_dir, 'responses', name);
    if (!fs.existsSync(existing)) {
      const processor = runtime.requestProcessor || processRequest;
      const value = await processor(config, request, runtime); restart = value?.restart_required === true;
      const publicValue = { ...value }; delete publicValue.restart_required; response(config, request, publicValue);
    }
  } catch (error) { rejectedResponse(config, request, error); }
  finally { releaseClaim(config, file); }
  return restart;
}

async function processRequests(config, runtime = {}) {
  let restartRequired = false;
  const limit = Math.max(1, Math.min(MAX_REQUEST_BATCH, Number(runtime.batchLimit) || MAX_REQUEST_BATCH));
  for (let handled = 0; handled < limit && !restartRequired; handled += 1) {
    const [name] = claimInbox(config, 1); if (!name) break;
    restartRequired = await processClaimed(config, name, runtime);
  }
  const phaseName = activeMutation(config, runtime.rootUid ?? 0)?.mutation?.phase;
  return restartRequired || ['applied_pending_boot', 'rollback_pending_boot'].includes(phaseName);
}

function loadCurrent(config, rootUid = 0) {
  const file = path.join(config.maintainer_state_dir, 'current-apply.json');
  if (!fs.existsSync(file)) return null;
  const pointer = JSON.parse(readRegular(file, { uid: rootUid }).toString('utf8'));
  if (pointer.protocol !== PROTOCOL || !ID_PATTERN.test(String(pointer.transaction_id || ''))
    || !['apply', 'rollback'].includes(pointer.direction)) throw new Error('actieve transactiepointer is ongeldig');
  return { pointer, ...loadSealed(config, pointer.transaction_id, rootUid) };
}

const RECOVERY_TRANSITIONS = Object.freeze({
  'idle:applying': 'rollback-terminal', 'idle:booting': 'rollback-terminal',
  'boot:applying': 'rollback-boot', 'boot:booting': 'rollback-boot',
  'idle:applied_pending_boot': 'restart-target', 'idle:rollback_pending_boot': 'restart-target',
  'boot:applied_pending_boot': 'boot-target', 'boot:rollback_pending_boot': 'boot-target',
  'idle:recovery_booting': 'restart-recovery', 'idle:recovery_live_unverified': 'restart-recovery',
  'idle:recovery_manual_intervention': 'restart-recovery',
  'boot:recovery_booting': 'boot-recovery', 'boot:recovery_live_unverified': 'boot-recovery',
  'boot:recovery_manual_intervention': 'hold-manual',
  'idle:recovery_tree_conflict': 'hold-manual', 'boot:recovery_tree_conflict': 'reject-conflict',
});

function expectedTree(config, marker, message) {
  if (treeSha(config, trackedFiles(config)) !== marker.expected_tree_sha256) throw new Error(message);
}

function setRecoveryPhase(tx, marker, phaseName) {
  marker.phase = phaseName; tx.status = phaseName;
  tx.active_runtime = activeRuntimeFor(phaseName, { ...tx, mutation: marker });
}

function rollbackInterrupted(config, current, marker, boot, runtime) {
  restore(config, current.tx, current.txDir, marker.direction, runtime.rootUid ?? 0);
  setRecoveryPhase(current.tx, marker, boot ? 'recovery_booting' : 'mutation_crash_rolled_back');
  if (boot) {
    marker.recovery_attempts = Number(marker.recovery_attempts || 0) + 1;
    marker.expected_tree_sha256 = marker.previous_tree_sha256; marker.recovery_started_at = new Date().toISOString();
  }
  journal(config, current.tx, current.txPath, marker, runtime, 'recovery');
}

function recoveryTransition(config, current, marker, action, runtime) {
  const { tx, txPath } = current;
  if (action === 'rollback-terminal' || action === 'rollback-boot') {
    rollbackInterrupted(config, current, marker, action === 'rollback-boot', runtime); return undefined;
  }
  if (action === 'reject-conflict') throw new Error('recovery_tree_conflict vereist rootinterventie');
  if (action === 'hold-manual') {
    expectedTree(config, marker, 'manual-recoveryboom wijkt af; Eva mag niet starten'); return undefined;
  }
  if (action === 'restart-target') return restartHandoff(tx, runtime);
  if (action === 'boot-target') {
    expectedTree(config, marker, 'boot-CAS wijkt af van promotiedoel');
    setRecoveryPhase(tx, marker, 'booting'); marker.boot_started_at = new Date().toISOString();
    journal(config, tx, txPath, marker, runtime, 'boot'); return undefined;
  }
  if (action === 'restart-recovery') {
    setRecoveryPhase(tx, marker, 'recovery_booting'); marker.recovery_attempts = Number(marker.recovery_attempts || 0) + 1;
    journal(config, tx, txPath, marker, runtime, 'recovery-restart'); return restartHandoff(tx, runtime);
  }
  try { expectedTree(config, marker, 'herstelde bootboom wijkt opnieuw af'); }
  catch (error) {
    setRecoveryPhase(tx, marker, 'recovery_tree_conflict'); journal(config, tx, txPath, marker, runtime, 'recovery-conflict'); throw error;
  }
  setRecoveryPhase(tx, marker, 'recovery_booting'); journal(config, tx, txPath, marker, runtime, 'recovery-retry'); return undefined;
}

function recover(config, boot = false, runtime = {}) {
  const current = loadCurrent(config, runtime.rootUid ?? 0); if (!current) return undefined;
  const marker = current.tx.mutation || mutationFor(current.tx, current.pointer.direction);
  if (marker.direction !== current.pointer.direction) throw new Error('transactiepointer en mutatierichting verschillen');
  if (phaseContract(marker.phase).terminal) {
    setRecoveryPhase(current.tx, marker, marker.phase);
    journal(config, current.tx, current.txPath, marker, runtime, 'reconcile'); return undefined;
  }
  const action = RECOVERY_TRANSITIONS[`${boot ? 'boot' : 'idle'}:${marker.phase}`];
  if (!action) throw new Error(`onherstelbare runtimefase: ${marker.phase}`);
  return recoveryTransition(config, current, marker, action, runtime);
}

function runtimeWebToken(config, rootUid = 0) {
  const environment = path.join(config.root, '.env');
  if (!fs.existsSync(environment)) return '';
  const lines = readRegular(environment, { uid: rootUid }).toString('utf8').split(/\r?\n/u);
  const declarations = (name) => lines.filter((line) => new RegExp(`^\\s*(?:export\\s+)?${name}\\s*=`, 'u').test(line));
  const tokens = declarations('EVA_WEB_TOKEN');
  if (tokens.length > 1 || (tokens[0] && !/^EVA_WEB_TOKEN=(?:[A-Za-z0-9._~-]{32,256})?$/u.test(tokens[0]))) {
    throw new Error('EVA_WEB_TOKEN moet afwezig of exact eenmaal canoniek en ongequote in .env staan');
  }
  return tokens[0]?.slice('EVA_WEB_TOKEN='.length) || '';
}

function assertHealthAuth(config, rootUid = 0) {
  const runtimeToken = runtimeWebToken(config, rootUid);
  const file = String(config.health_token_file || '');
  if (!runtimeToken) throw new Error('livebewijs vereist altijd een canonieke runtime-token');
  if (!file || !rootFile(file, rootUid, 0o600)) throw new Error('health_token_file moet root-owned 0600 zijn');
  const token = readRegular(file, { uid: rootUid, maximum: 512 }).toString('utf8');
  if (token !== runtimeToken) throw new Error('health_token_file wijkt af van EVA_WEB_TOKEN');
  return { mode: 'bearer-token', token };
}

function jsonRequest(config, pathname, {
  method = 'GET', body = null, timeout = 2_000, rootUid = 0, headers = {},
} = {}) {
  const target = new URL(pathname, config.health_url || 'http://127.0.0.1:3210/api/health');
  return new Promise((resolve, reject) => {
    const payload = body == null ? '' : JSON.stringify(body);
    const { token } = assertHealthAuth(config, rootUid);
    const req = http.request(target, {
      method, timeout,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
        ...headers,
      },
    }, (res) => {
      let body = ''; res.setEncoding('utf8');
      res.on('data', (chunk) => { body = `${body}${chunk}`.slice(0, 256 * 1024); });
      res.on('end', () => {
        try { resolve(res.statusCode === 200 ? JSON.parse(body) : null); } catch { resolve(null); }
      });
    });
    req.on('timeout', () => req.destroy(new Error('health timeout'))); req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

const healthRequest = (config) => jsonRequest(config, '/api/health');

function serviceValue(property) {
  return execFileSync('/usr/bin/systemctl', [
    'show', 'eva.service', '-p', property, '--value',
  ], { encoding: 'utf8' }).trim();
}

function processCgroups(procRoot, pid) {
  return fs.readFileSync(path.join(procRoot, String(pid), 'cgroup'), 'utf8')
    .trim().split(/\r?\n/u).map((line) => line.slice(line.lastIndexOf(':') + 1));
}

function listenerEndpoint(target) {
  const port = Number(target.port || 80); const octets = target.hostname.split('.').map(Number);
  if (octets.length === 4 && octets.every((part) => Number.isInteger(part) && part >= 0 && part <= 255)
    && octets.join('.') === target.hostname) {
    const address = [...octets].reverse().map((part) => part.toString(16).padStart(2, '0')).join('').toUpperCase();
    return { table: 'tcp', address, port };
  }
  if (target.hostname === '[::1]') {
    return { table: 'tcp6', address: '00000000000000000000000001000000', port };
  }
  throw new Error('liveendpoint heeft geen bewijsbaar numeriek loopbackadres');
}

function listeningSocketInodes(procRoot, target) {
  const endpoint = listenerEndpoint(target);
  const inodes = new Set();
  const file = path.join(procRoot, 'net', endpoint.table);
  for (const line of fs.readFileSync(file, 'utf8').trim().split(/\r?\n/u).slice(1)) {
    const fields = line.trim().split(/\s+/u); const [address, port] = (fields[1] || '').split(':');
    if (fields[3] === '0A' && address === endpoint.address
      && Number.parseInt(port, 16) === endpoint.port && /^\d+$/u.test(fields[9] || '')) {
      inodes.add(fields[9]);
    }
  }
  return inodes;
}

function processOwnsSocket(procRoot, pid, inodes) {
  const directory = path.join(procRoot, String(pid), 'fd');
  return fs.readdirSync(directory).some((name) => {
    try {
      const match = fs.readlinkSync(path.join(directory, name)).match(/^socket:\[(\d+)\]$/u);
      return Boolean(match && inodes.has(match[1]));
    } catch { return false; }
  });
}

function assertLiveService(config, runtime = {}) {
  const show = runtime.systemdShow || serviceValue; const procRoot = runtime.procRoot || '/proc';
  try {
    const pid = Number(show('MainPID')); const cgroup = show('ControlGroup');
    const target = new URL(config.health_url || 'http://127.0.0.1:3210/api/health');
    if (Number.isInteger(pid) && pid > 1 && cgroup
      && processCgroups(procRoot, pid).includes(cgroup)
      && processOwnsSocket(procRoot, pid, listeningSocketInodes(procRoot, target))) return pid;
  } catch { /* ieder ontbrekend proc/systemd-bewijs is dezelfde gesloten uitkomst */ }
  throw new Error('liveendpoint hoort niet aantoonbaar bij eva.service MainPID/cgroup/socket');
}

function validCanaryReply(reply, conversationId, expectedRuntime) {
  return reply?.ok === true && Boolean(String(reply.reply || '').trim())
    && Boolean(String(reply.turn_id || '').trim())
    && reply.conversation_id === `web:${conversationId}`
    && reply.runtime_code_sha256 === expectedRuntime;
}

async function conversationCanary(config, tx, expectedRuntime, rootUid = 0) {
  const conversationId = `runtime-canary-${tx.id}`;
  const requestId = `runtime-canary:${tx.id}`;
  const reply = await jsonRequest(config, '/api/chat', {
    method: 'POST', timeout: 75_000, rootUid,
    headers: { 'Idempotency-Key': requestId },
    body: {
      text: 'Geef één korte, gewone zin terug over deze veilige runtimecanary. Gebruik geen gereedschap.',
      conversation_id: conversationId, request_id: requestId,
    },
  });
  return validCanaryReply(reply, conversationId, expectedRuntime);
}

function liveOutcome(phase, success, direction = 'apply') {
  if (success) {
    if (phase !== 'booting') return 'boot_failed_rolled_back_live';
    return direction === 'apply' ? 'committed' : 'rolled_back';
  }
  return phase === 'booting' ? null : 'recovery_live_unverified';
}

function healthMatches(health, expectedRuntime, expectedDependency, expectedPid) {
  const value = health || {}; const version = value.runtimeVersion || {};
  return [
    value.gezond === true, value.agent && typeof value.agent === 'object',
    version.pid === expectedPid,
    version.loaded_code_sha256 === expectedRuntime, version.disk_code_sha256 === expectedRuntime,
    version.code_matches_disk === true,
    version.loaded_dependency_tree_sha256 === expectedDependency,
    version.disk_dependency_tree_sha256 === expectedDependency,
  ].every(Boolean);
}

async function waitForRuntimeHealth(config, runtime, expectedRuntime, expectedDependency, expectedPid) {
  const request = runtime.healthRequest || healthRequest;
  const sleep = runtime.sleep || ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  for (let attempt = 0; attempt < (runtime.liveAttempts ?? 30); attempt += 1) {
    try {
      if (healthMatches(await request(config), expectedRuntime, expectedDependency, expectedPid)) return true;
    } catch { /* de volgende korte liveproef kan slagen */ }
    await sleep(1000);
  }
  return false;
}

async function proveLiveRuntime(
  config, tx, runtime, expectedRuntime, expectedDependency, attest, rootUid, expectedPid,
  serviceIdentity, diskRuntime,
) {
  if (!await waitForRuntimeHealth(config, runtime, expectedRuntime, expectedDependency, expectedPid)) return false;
  const canaryReady = await (runtime.canary || conversationCanary)(config, tx, expectedRuntime);
  if (serviceIdentity(config, runtime) !== expectedPid) throw new Error('eva.service wisselde tijdens de livecanary');
  if (diskRuntime(config) !== expectedRuntime) throw new Error('runtimebron wijzigde tijdens de livecanary');
  return canaryReady && attest(config, rootUid) === expectedDependency;
}

function liveTarget(marker) {
  const target = marker.direction === 'apply' ? 'candidate' : 'base';
  const previous = marker.direction === 'apply' ? 'base' : 'candidate';
  return marker.phase === 'booting' ? target : previous;
}

function recordLiveFailure(config, tx, txPath, marker, recovering, outcome, runtime) {
  if (!recovering) return;
  marker.recovery_attempts = Number(marker.recovery_attempts || 0) + 1;
  setRecoveryPhase(tx, marker, marker.recovery_attempts >= 3 ? 'recovery_manual_intervention' : outcome);
  marker.live_check_failed_at = new Date().toISOString();
  journal(config, tx, txPath, marker, runtime, 'live-unverified');
}

async function verifyLive(config, runtime = {}) {
  const rootUid = runtime.rootUid ?? 0;
  const current = loadCurrent(config, rootUid);
  if (!current || !['booting', 'recovery_booting', 'recovery_live_unverified']
    .includes(current.tx.mutation?.phase)) return;
  const { tx, txPath } = current;
  const marker = tx.mutation;
  const recovering = marker.phase !== 'booting';
  const target = liveTarget(marker);
  const expectedRuntime = tx[target].runtime_sha256;
  const expectedDependency = tx[target].dependency_tree_sha256;
  const attest = runtime.assertAttestation || assertRuntimeAttestation;
  const diskRuntime = runtime.diskRuntime || runtimeSha;
  const serviceIdentity = runtime.serviceIdentity || assertLiveService;
  const expectedPid = serviceIdentity(config, runtime);
  if (attest(config, rootUid) !== expectedDependency) throw new Error('live dependencyseal wijkt af');
  if (diskRuntime(config) !== expectedRuntime) throw new Error('live runtimebron wijkt af van het promotiedoel');
  const liveReady = await proveLiveRuntime(
    config, tx, runtime, expectedRuntime, expectedDependency, attest, rootUid, expectedPid,
    serviceIdentity, diskRuntime,
  );
  const outcome = liveOutcome(marker.phase, liveReady, marker.direction);
  if (liveReady) {
    setRecoveryPhase(tx, marker, outcome); marker.live_verified_at = new Date().toISOString();
    journal(config, tx, txPath, marker, runtime, 'live-verified'); return;
  }
  recordLiveFailure(config, tx, txPath, marker, recovering, outcome, runtime);
  throw new Error('live health/canary bevestigde de geladen runtime niet');
}

function checkInstall(config) {
  const identity = isolatedTestGateIdentity(config, { requireRuntime: false });
  assertServiceCannotWriteRuntime(config);
  assertMaintainerPaths(config, identity.serviceGid, identity.runtimeReadGid);
  assertRuntimeAttestation(config);
  assertHealthAuth(config);
  execFileSync('/usr/bin/systemctl', ['is-active', '--quiet', 'eva-runtime-maintainer.path']);
  process.stdout.write(`${PROTOCOL}\n`);
}

function approve(config, transactionId, operation, runtime = {}) {
  if (!['apply', 'rollback'].includes(operation)) throw new Error('rootgoedkeuring vereist apply of rollback');
  const loaded = loadSealed(config, transactionId);
  const request = {
    protocol: PROTOCOL, request_id: `root-${randomUUID()}`,
    operation, transaction_id: transactionId,
    root_receipt_sha256: loaded.tx.root_receipt.sha256,
  };
  const result = processPromotion(config, request, true, runtime);
  const restartRequired = Reflect.get(result, 'restart_required') === true;
  const publicResult = { ...result }; Reflect.deleteProperty(publicResult, 'restart_required');
  process.stdout.write(`${JSON.stringify(publicResult)}\n`);
  return restartRequired;
}

async function main() {
  const runtimeConfig = readConfig();
  if (ACTION === '--prepare') preparePaths(runtimeConfig);
  else if (ACTION === '--check') checkInstall(runtimeConfig);
  else if (ACTION === '--requests') {
    const restart = await processRequests(runtimeConfig, {
      serialize: (action) => withRootLock(runtimeConfig, action),
    });
    if (restart) requestRestart();
  }
  else if (ACTION === '--recover') await withRestartHandoff(runtimeConfig, () => recover(runtimeConfig, false));
  else if (ACTION === '--boot') await withRootLock(runtimeConfig, () => recover(runtimeConfig, true));
  else if (ACTION === '--verify-live') await withRootLock(runtimeConfig, () => verifyLive(runtimeConfig));
  else if (ACTION === '--approve') await withRestartHandoff(runtimeConfig,
    () => approve(runtimeConfig, ACTION_ID, APPROVAL_OPERATION));
  else throw new Error('gebruik --requests, --recover, --boot, --verify-live, --prepare, --check of --approve');
}

const direct = process.argv[1]
  && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (direct) await main();

export const __maintainerTest = {
  PROTOCOL, ROOT_GATE_LAUNCHER, ROOT_GATE_TEST_POLICY, ACTIVE_PHASES, activeMutation, applyHunks, assertHealthAuth,
  assertInstalledIsolatedTestRelease, assertLiveService, assertRuntimeAttestation,
  checkedSource, claimInbox, contained, dateMillis, dependencyTreeSha256,
  matchingProductionGrant, liveOutcome, loadSealed,
  maintainerDirectories, mutationInProgress, parseUnifiedDiff,
  conversationCanary, jsonRequest, processRequest, processRequests, processTest, processPromotion, publicStatus: transactionResult,
  readRegular, recover, restore,
  executeRootGatePlan, renderEvaServiceUnit, rootGateInvocation, rootGatePlan, runRootGates,
  rootGateTestSelection, runtimeSha, sealTransaction, sha256, treeSha, validCanaryReply,
  validateIsolatedTestRelease, validateRuntimeIdentities,
  withRestartHandoff,
  verifyArtifacts, verifyLive,
};
