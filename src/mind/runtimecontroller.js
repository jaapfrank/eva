import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { config } from '../config.js';
import { runtimeCodeSha256 } from '../runtimeversion.js';
import {
  issuedExplorationContinuationContext,
  validExplorationContinuationContext,
} from './selfattentioncontext.js';
import {
  HASH_PATTERN, ID_PATTERN, PROTECTED_SOURCE, PROTOCOL, RELEASE_PATHS,
  applyHunks, atomicJson, attestRelease, contained, dateMillis, dependencyTreeSha256,
  fileSetSha256, gitTrackedFiles, parseUnifiedDiff, readRegular, repairSourcePath, securePath, sha256,
  trackedManifest, transactionResult, validateRootResult, writeNewFile,
} from '../../deploy/eva-runtime-repair-protocol.mjs';

const LOCAL_PATCH_BYTES = 32 * 1024;
const TX_TTL_MS = 2 * 60 * 60_000;
const DEFAULT_STATE = path.join(config.dataDir, 'runtime', 'self-repair');
const ROOT_CONFIG = '/etc/eva-runtime-self-repair.json';
const ISOLATED_TEST_BOOTSTRAP = '/usr/local/libexec/eva/run-isolated-tests-bootstrap';
const ISOLATED_TEST_BOOTSTRAP_SHA256 = '320d84306d8f23d21ea2bad40bbfe8bff1ca7ab3acf60c6baba3f5f59a1a7882';
const ISOLATED_TEST_GATE_USER = 'eva-test-gate';
const SERVICE_USER = 'eva';
const RUNTIME_READ_GROUP = 'eva-runtime-read';
const ISOLATED_TEST_RELEASE_PATHS = Object.freeze([
  ISOLATED_TEST_BOOTSTRAP,
  '/usr/local/libexec/eva/run-isolated-tests-bootstrap.c',
  '/usr/local/libexec/eva/run-isolated-tests.mjs',
  '/usr/local/libexec/eva/run-isolated-tests-release.json',
]);
const AUTH_ERROR = 'runtimebeheer niet geautoriseerd';
const PRINCIPAL_SOURCES = new Set(['telegram', 'autonomy']);
const PRINCIPAL_VALIDATORS = new Map();
const VALIDATION_STARTED = new Set();
const PRINCIPAL_ROUTE = new Map([['autonomy', 'autonomy'], ['chat:telegram', 'telegram']]);
const PRINCIPAL_AUTHORIZED = Object.freeze({
  autonomy: (principal) => principal === 'system:eva-autonomy',
  telegram: (principal) => /^telegram:[1-9]\d*$/u.test(principal),
});
const SELF_ATTENTION_KINDS = new Set(['aspiration', 'exploration', 'goal']);
const SELF_ATTENTION_GOAL_KEY = /^[a-z][a-z0-9_-]{0,119}$/u;
const SELF_ATTENTION_RECEIPTS = new WeakMap();
const ISSUED_SELF_ATTENTION_CONTEXTS = new WeakSet();
const CONSUMED_SELF_ATTENTION_RECEIPTS = new WeakSet();
const ISSUED_UNPRIVILEGED_AUTONOMY_CONTEXTS = new WeakSet();
const iso = (value = Date.now()) => new Date(value).toISOString();

function plainFrozenDataObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || !Object.isFrozen(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return false;
  return Object.values(Object.getOwnPropertyDescriptors(value)).every((descriptor) => (
    Object.hasOwn(descriptor, 'value') && descriptor.get == null && descriptor.set == null
  ));
}

export function registerRuntimePrincipalValidator(source, validator) {
  if (!PRINCIPAL_SOURCES.has(source) || typeof validator !== 'function'
    || PRINCIPAL_VALIDATORS.has(source) || VALIDATION_STARTED.has(source)) {
    throw new Error('runtime-principalvalidator kan alleen eenmaal door een vaste ingress worden gezet');
  }
  PRINCIPAL_VALIDATORS.set(source, validator);
}

function registeredPrincipal(source, context) {
  VALIDATION_STARTED.add(source);
  try { return PRINCIPAL_VALIDATORS.get(source)?.(context) || null; } catch { return null; }
}

export function runtimePrincipalAuthorized(source, context) {
  if (!PRINCIPAL_SOURCES.has(source)) return false;
  const principal = String(registeredPrincipal(source, context) || '');
  return PRINCIPAL_AUTHORIZED[source]?.(principal) === true;
}

export function issueUnprivilegedAutonomyContext({
  pendingSignals = [], workId = null, goalEpisodeId = null,
} = /** @type {any} */ ({})) {
  const context = Object.freeze({
    pendingSignals: Object.freeze(
      (Array.isArray(pendingSignals) ? pendingSignals : []).slice(0, 30),
    ),
    attention: null,
    workId,
    goalEpisodeId,
  });
  ISSUED_UNPRIVILEGED_AUTONOMY_CONTEXTS.add(context);
  return context;
}

export function verifiedAutonomyInteractionEpoch(messageContext) {
  if (!runtimePrincipalAuthorized('autonomy', { messageContext })) return null;
  try {
    if (!plainFrozenDataObject(messageContext)
      || typeof messageContext.interactionEpoch !== 'number'
      || !Number.isSafeInteger(messageContext.interactionEpoch)
      || messageContext.interactionEpoch < 0) return null;
    return messageContext.interactionEpoch;
  } catch { return null; }
}

function validSelfAttentionIssuanceShape({
  messageContext, attentionText, exactReason, exactTurnText,
  kind, id, goalKey, goalRevision, claimedUntil, explorationContext,
}) {
  return !ISSUED_SELF_ATTENTION_CONTEXTS.has(messageContext)
    && typeof exactReason === 'string' && Boolean(exactReason)
    && typeof exactTurnText === 'string' && Boolean(exactTurnText)
    && typeof kind === 'string' && typeof id === 'number'
    && SELF_ATTENTION_KINDS.has(kind)
    && Number.isSafeInteger(id) && id >= 1
    && typeof attentionText === 'string' && Boolean(attentionText.trim())
    && attentionText.length <= 8_000
    && (kind !== 'goal' || (SELF_ATTENTION_GOAL_KEY.test(goalKey || '')
      && Number.isSafeInteger(goalRevision) && goalRevision >= 1
      && Number.isFinite(Date.parse(claimedUntil || ''))))
    && (kind !== 'exploration' || (validExplorationContinuationContext(explorationContext)
      && explorationContext.id === id && explorationContext.question === attentionText))
    && (!Array.isArray(messageContext.signalBatch?.signals)
      || messageContext.signalBatch.signals.length === 0)
    && (!Array.isArray(messageContext.pendingSignals)
      || messageContext.pendingSignals.length === 0)
    && messageContext.workId == null && messageContext.goalEpisodeId == null;
}

function autonomySelfAttentionIssuance({
  mode, messageContext, turnText,
} = /** @type {any} */ ({})) {
  if (mode !== 'autonomy') return { presented: false, receipt: null };
  const authorized = runtimePrincipalAuthorized('autonomy', { messageContext });
  if (!authorized) return {
    presented: !ISSUED_UNPRIVILEGED_AUTONOMY_CONTEXTS.has(messageContext),
    receipt: null,
  };
  try {
    if (!plainFrozenDataObject(messageContext)) {
      return { presented: true, receipt: null };
    }
    const attention = messageContext.attention;
    const presented = attention !== null;
    if (!presented) return { presented: false, receipt: null };
    if (!plainFrozenDataObject(attention)) return { presented, receipt: null };
    const exactReason = messageContext.reason;
    const exactTurnText = turnText;
    const attentionText = attention.text;
    const id = attention.id;
    const kind = attention.kind;
    const goalKey = kind === 'goal' && typeof attention.key === 'string'
      ? attention.key : null;
    const goalRevision = kind === 'goal' && typeof attention.revision === 'number'
      ? attention.revision : null;
    const claimedUntil = kind === 'goal' && typeof attention.claimed_until === 'string'
      ? attention.claimed_until : null;
    const explorationContext = kind === 'exploration'
      ? issuedExplorationContinuationContext(attention) : null;
    if (!validSelfAttentionIssuanceShape({
      messageContext, attentionText, exactReason, exactTurnText,
      kind, id, goalKey, goalRevision, claimedUntil, explorationContext,
    })) {
      return { presented, receipt: null };
    }
    const goalRef = kind === 'goal'
      ? `goal-ref:${randomBytes(16).toString('hex')}` : null;
    const value = Object.freeze({
      contract: 'AutonomySelfAttentionReceipt/v1',
      kind, id, attentionSha256: sha256(attentionText),
      reasonSha256: sha256(exactReason), turnTextSha256: sha256(exactTurnText),
    });
    const metadata = Object.freeze({ ...value,
      ...(kind === 'goal' ? {
        goalKey, goalRef, goalRevision,
        claimedUntil: new Date(claimedUntil).toISOString(),
      } : {}),
      ...(kind === 'exploration' ? {
        explorationContext,
        explorationRevision: explorationContext.revision,
      } : {}),
    });
    ISSUED_SELF_ATTENTION_CONTEXTS.add(messageContext);
    SELF_ATTENTION_RECEIPTS.set(value, metadata);
    return { presented, receipt: value };
  } catch { return { presented: true, receipt: null }; }
}

export function issueAutonomySelfAttentionReceipt(options = /** @type {any} */ ({})) {
  return autonomySelfAttentionIssuance(options).receipt;
}

export function issueAutonomySelfAttentionIngress(options = /** @type {any} */ ({})) {
  return Object.freeze(autonomySelfAttentionIssuance(options));
}

export function verifiedAutonomySelfAttentionReceipt(receipt, {
  turnText, origin, channel, now = Date.now(),
} = /** @type {any} */ ({})) {
  const value = receipt && typeof receipt === 'object'
    ? SELF_ATTENTION_RECEIPTS.get(receipt) : null;
  if (!value || CONSUMED_SELF_ATTENTION_RECEIPTS.has(receipt)) return null;
  const at = now instanceof Date ? now.getTime() : new Date(now).getTime();
  const valid = origin === 'self' && channel === 'autonomy'
    && typeof turnText === 'string'
    && value.turnTextSha256 === sha256(turnText)
    && (value.kind !== 'goal' || (Number.isFinite(at)
      && Date.parse(value.claimedUntil) > at));
  if (!valid) return null;
  CONSUMED_SELF_ATTENTION_RECEIPTS.add(receipt);
  return value;
}

function checkedTrackedFile(root, relative, tracked) {
  const normalized = repairSourcePath(relative);
  if (!tracked.includes(normalized)) throw new Error(`bron is niet getrackt: ${normalized}`);
  const absolute = contained(root, normalized); const stat = fs.lstatSync(absolute);
  if (!stat.isFile() || stat.isSymbolicLink() || fs.realpathSync(absolute) !== absolute) {
    throw new Error('alleen reguliere bronbestanden zijn toegestaan');
  }
  return { absolute, stat };
}

function readRootConfig(root, stateDir, serviceGid) {
  if (!securePath(ROOT_CONFIG, { uid: 0, gid: serviceGid, mode: 0o640 })) return null;
  const value = JSON.parse(fs.readFileSync(ROOT_CONFIG, 'utf8'));
  const fixed = value.protocol === PROTOCOL && path.resolve(value.root) === path.resolve(root)
    && path.resolve(value.state_dir) === path.resolve(stateDir)
    && path.isAbsolute(value.maintainer_state_dir) && path.isAbsolute(value.release_manifest)
    && path.isAbsolute(value.tracked_manifest) && String(value.runtime_read_group || '').trim();
  if (!fixed || !securePath(value.maintainer_state_dir, { kind: 'directory', uid: 0, mode: 0o711 })) return null;
  /** @type {Array<[string, number]>} */
  const directories = [['responses', 0o750], ['status', 0o750], ['inbox', 0o730]];
  if (directories.some(([name, mode]) => !securePath(path.join(value.maintainer_state_dir, name), {
    kind: 'directory', uid: 0, gid: serviceGid, mode,
  }))) return null;
  attestRelease(value.release_manifest, [
    ...RELEASE_PATHS, ...ISOLATED_TEST_RELEASE_PATHS, value.tracked_manifest,
  ], {
    gid: serviceGid, mode: 0o640, sourceRoot: root,
  });
  trackedManifest(value.tracked_manifest, root, { gid: serviceGid });
  return value;
}

function systemdEnvironmentWords(value) {
  const words = []; let current = ''; let quote = ''; let escaped = false; let started = false;
  for (const character of String(value || '')) {
    if (escaped) { current += character; escaped = false; started = true; continue; }
    if (character === '\\') { escaped = true; started = true; continue; }
    if (quote) {
      if (character === quote) quote = '';
      else current += character;
      started = true; continue;
    }
    if (character === '"' || character === "'") { quote = character; started = true; continue; }
    if (/\s/u.test(character)) {
      if (started) { words.push(current); current = ''; started = false; }
      continue;
    }
    current += character; started = true;
  }
  if (escaped || quote) return null;
  if (started) words.push(current);
  return words;
}

function effectiveDataDirAttested(environment, expected) {
  const words = systemdEnvironmentWords(environment);
  if (!words) return false;
  const assignments = words.filter((word) => word.startsWith('EVA_DATA_DIR='));
  return assignments.length === 1 && assignments[0] === `EVA_DATA_DIR=${expected}`;
}

function unitDataDirAttested(unit, expected) {
  const relevant = String(unit || '').split('\n').filter((line) => (
    !/^\s*[#;]/u.test(line) && line.includes('EVA_DATA_DIR')
  ));
  return relevant.length === 1 && relevant[0] === `Environment=EVA_DATA_DIR=${expected}`;
}

const SERVICE_ATTESTATION_KEYS = Object.freeze([
  'BootstrapGid', 'BootstrapMode', 'BootstrapNlink', 'BootstrapPath',
  'BootstrapSha256', 'BootstrapUid', 'Environment', 'ExecStart', 'GateGid',
  'GateGroupIds', 'GateGroups', 'GateHome', 'GatePrimaryGroup', 'GateShell',
  'GateUid', 'GateUser', 'Group', 'RuntimeReadGid', 'RuntimeReadGroup',
  'ServiceGid', 'ServiceGroupIds', 'ServiceGroups', 'ServiceHome',
  'ServicePrimaryGroup', 'ServiceShell', 'ServiceUid', 'SupplementaryGroups',
  'User', 'WorkingDirectory', 'dropIn', 'unit',
]);

function exactAttestedWords(value, expected, numeric = false) {
  const words = String(value || '').trim();
  const actual = words ? words.split(/\s+/u) : [];
  if (actual.length !== expected.length || new Set(actual).size !== actual.length) return false;
  if (numeric) {
    const numbers = actual.map(Number);
    if (numbers.some((entry) => !Number.isInteger(entry) || entry <= 0)) return false;
    numbers.sort((left, right) => left - right);
    return numbers.every((entry, index) => entry === [...expected].sort((left, right) => left - right)[index]);
  }
  return actual.sort().every((entry, index) => entry === [...expected].sort()[index]);
}

function serviceIdentitiesAttested(value, snapshot) {
  const serviceUid = Number(snapshot.ServiceUid); const serviceGid = Number(snapshot.ServiceGid);
  const gateUid = Number(snapshot.GateUid); const gateGid = Number(snapshot.GateGid);
  const runtimeReadGid = Number(snapshot.RuntimeReadGid);
  return value.service_user === SERVICE_USER && value.runtime_read_group === RUNTIME_READ_GROUP
    && snapshot.User.trim() === SERVICE_USER && snapshot.Group.trim() === SERVICE_USER
    && exactAttestedWords(snapshot.SupplementaryGroups, [RUNTIME_READ_GROUP])
    && Number.isInteger(serviceUid) && serviceUid > 0
    && Number.isInteger(serviceGid) && serviceGid > 0
    && snapshot.ServicePrimaryGroup === SERVICE_USER
    && snapshot.ServiceHome === '/nonexistent' && snapshot.ServiceShell === '/usr/sbin/nologin'
    && snapshot.RuntimeReadGroup === RUNTIME_READ_GROUP
    && Number.isInteger(runtimeReadGid) && runtimeReadGid > 0
    && exactAttestedWords(snapshot.ServiceGroupIds, [serviceGid, runtimeReadGid], true)
    && exactAttestedWords(snapshot.ServiceGroups, [SERVICE_USER, RUNTIME_READ_GROUP])
    && snapshot.GateUser === ISOLATED_TEST_GATE_USER && snapshot.GateUser !== SERVICE_USER
    && Number.isInteger(gateUid) && gateUid > 0 && gateUid !== serviceUid
    && Number.isInteger(gateGid) && gateGid > 0
    && new Set([serviceGid, gateGid, runtimeReadGid]).size === 3
    && snapshot.GatePrimaryGroup === ISOLATED_TEST_GATE_USER
    && snapshot.GateHome === '/nonexistent' && snapshot.GateShell === '/usr/sbin/nologin'
    && exactAttestedWords(snapshot.GateGroupIds, [gateGid, runtimeReadGid], true)
    && exactAttestedWords(snapshot.GateGroups, [ISOLATED_TEST_GATE_USER, RUNTIME_READ_GROUP]);
}

function serviceAttestationValid(value, root, snapshot) {
  if (!plainFrozenDataObject(snapshot)
    || JSON.stringify(Object.keys(snapshot).sort()) !== JSON.stringify(SERVICE_ATTESTATION_KEYS)
    || Object.values(snapshot).some((entry) => typeof entry !== 'string')) return false;
  const resolvedRoot = path.resolve(root); const expectedDataDir = path.join(resolvedRoot, 'data');
  return serviceIdentitiesAttested(value, snapshot)
    && path.resolve(snapshot.WorkingDirectory.trim()) === resolvedRoot
    && snapshot.ExecStart.trim().includes(`/usr/bin/node ${path.join(resolvedRoot, 'src/index.js')}`)
    && effectiveDataDirAttested(snapshot.Environment.trim(), expectedDataDir)
    && snapshot.BootstrapPath === ISOLATED_TEST_BOOTSTRAP
    && snapshot.BootstrapSha256 === ISOLATED_TEST_BOOTSTRAP_SHA256
    && snapshot.BootstrapUid === '0' && snapshot.BootstrapGid === '0'
    && snapshot.BootstrapMode === '0755' && snapshot.BootstrapNlink === '1'
    && unitDataDirAttested(snapshot.unit, expectedDataDir)
    && !snapshot.dropIn.split('\n').some((line) => !/^\s*[#;]/u.test(line)
      && line.includes('EVA_DATA_DIR'))
    && snapshot.unit.includes(`WorkingDirectory=${resolvedRoot}\n`)
    && snapshot.unit.includes(`ExecStart=/usr/bin/node ${path.join(resolvedRoot, 'src/index.js')}\n`)
    && snapshot.unit.includes('UnsetEnvironment=EVA_WEB_TOKEN WEB_HOST\n')
    && snapshot.dropIn.includes('eva-runtime-maintainer.mjs --boot')
    && snapshot.dropIn.includes('eva-runtime-maintainer.mjs --verify-live');
}

function serviceAttested(value, root) {
  const show = (property) => execFileSync('/usr/bin/systemctl', [
    'show', 'eva.service', '-p', property, '--value',
  ], { encoding: 'utf8' }).trim();
  const bootstrapStat = fs.lstatSync(ISOLATED_TEST_BOOTSTRAP);
  if (!bootstrapStat.isFile() || bootstrapStat.isSymbolicLink()
    || fs.realpathSync(ISOLATED_TEST_BOOTSTRAP) !== ISOLATED_TEST_BOOTSTRAP) return false;
  const servicePasswd = execFileSync('/usr/bin/getent', ['passwd', value.service_user], {
    encoding: 'utf8',
  }).trim().split(':');
  const passwd = execFileSync('/usr/bin/getent', ['passwd', ISOLATED_TEST_GATE_USER], {
    encoding: 'utf8',
  }).trim().split(':');
  const runtimeGroup = execFileSync('/usr/bin/getent', ['group', value.runtime_read_group], {
    encoding: 'utf8',
  }).trim().split(':');
  if (servicePasswd.length !== 7 || passwd.length !== 7
    || passwd[0] !== ISOLATED_TEST_GATE_USER || runtimeGroup.length !== 4) return false;
  const snapshot = Object.freeze({
    User: show('User'), Group: show('Group'), SupplementaryGroups: show('SupplementaryGroups'),
    ServiceUid: servicePasswd[2], ServiceGid: servicePasswd[3],
    ServicePrimaryGroup: execFileSync('/usr/bin/id', ['-gn', value.service_user], {
      encoding: 'utf8',
    }).trim(),
    ServiceGroupIds: execFileSync('/usr/bin/id', ['-G', value.service_user], {
      encoding: 'utf8',
    }).trim(),
    ServiceGroups: execFileSync('/usr/bin/id', ['-Gn', value.service_user], {
      encoding: 'utf8',
    }).trim(),
    ServiceHome: servicePasswd[5], ServiceShell: servicePasswd[6],
    RuntimeReadGroup: runtimeGroup[0], RuntimeReadGid: runtimeGroup[2],
    WorkingDirectory: show('WorkingDirectory'), ExecStart: show('ExecStart'),
    Environment: show('Environment'),
    BootstrapPath: ISOLATED_TEST_BOOTSTRAP,
    BootstrapSha256: sha256(readRegular(ISOLATED_TEST_BOOTSTRAP, {
      uid: 0, gid: 0, mode: 0o755, nlink: 1, maximum: 2 * 1024 * 1024,
    })),
    BootstrapUid: String(bootstrapStat.uid), BootstrapGid: String(bootstrapStat.gid),
    BootstrapMode: (bootstrapStat.mode & 0o777).toString(8).padStart(4, '0'),
    BootstrapNlink: String(bootstrapStat.nlink),
    GateUser: passwd[0], GateUid: passwd[2], GateGid: passwd[3],
    GatePrimaryGroup: execFileSync('/usr/bin/id', ['-gn', ISOLATED_TEST_GATE_USER], {
      encoding: 'utf8',
    }).trim(),
    GateHome: passwd[5], GateShell: passwd[6],
    GateGroupIds: execFileSync('/usr/bin/id', ['-G', ISOLATED_TEST_GATE_USER], {
      encoding: 'utf8',
    }).trim(),
    GateGroups: execFileSync('/usr/bin/id', ['-Gn', ISOLATED_TEST_GATE_USER], {
      encoding: 'utf8',
    }).trim(),
    unit: fs.readFileSync('/etc/systemd/system/eva.service', 'utf8'),
    dropIn: fs.readFileSync('/etc/systemd/system/eva.service.d/runtime-maintainer.conf', 'utf8'),
  });
  return serviceAttestationValid(value, root, snapshot);
}

function rootMaintainerConfig(root, stateDir) {
  try {
    const value = readRootConfig(root, stateDir, process.getgid?.() ?? -1);
    if (!value || !serviceAttested(value, root)) return null;
    execFileSync('/usr/bin/systemctl', ['is-active', '--quiet', 'eva-runtime-maintainer.path']);
    try { fs.accessSync(path.join(root, 'src'), fs.constants.W_OK); return null; } catch { return value; }
  } catch { return null; }
}

function actorFor(context = {}, validatePrincipal = registeredPrincipal) {
  const mode = String(context.mode || 'chat'); const channel = String(context.channel || '');
  const source = PRINCIPAL_ROUTE.get(mode === 'autonomy' ? mode : `${mode}:${channel}`);
  const principal = source ? String(validatePrincipal(source, context) || '') : '';
  const authorized = source ? PRINCIPAL_AUTHORIZED[source](principal) : false;
  return {
    mode, channel, authorized,
    synthetic: context.messageContext?.syntheticTest === true,
    turn_kind: String(context.turnUnderstanding?.kind || ''),
    action_intent: String(context.turnUnderstanding?.actionIntent || ''),
    owner_sha256: principal ? sha256(principal) : '',
  };
}

function assertRepairActor(actor, tx = null, execute = true) {
  const contract = actor.mode === 'autonomy' || (actor.turn_kind === 'self_repair'
    && (!execute || actor.action_intent === 'execute'));
  const owner = !tx || (HASH_PATTERN.test(String(tx.actor?.owner_sha256 || ''))
    && tx.actor.owner_sha256 === actor.owner_sha256);
  if (!actor.authorized || actor.synthetic || !contract || !owner) throw new Error(AUTH_ERROR);
}

export function createRuntimeController({
  root = config.root, stateDir = DEFAULT_STATE, now = () => Date.now(),
  maintainerConfig = rootMaintainerConfig, tracked = gitTrackedFiles, readRootStatus = null,
} = {}) {
  const proposalPath = (id) => path.join(stateDir, 'proposals', String(id), 'proposal.json');
  const load = (id) => {
    if (!ID_PATTERN.test(String(id || ''))) throw new Error(AUTH_ERROR);
    const value = JSON.parse(fs.readFileSync(proposalPath(id), 'utf8'));
    if (value.protocol !== PROTOCOL || value.id !== id) throw new Error(AUTH_ERROR);
    return value;
  };
  const loadOwned = (id, actor) => {
    try { const tx = load(id); assertRepairActor(actor, tx, false); return tx; }
    catch { throw new Error(AUTH_ERROR); }
  };
  const maintainer = () => maintainerConfig(root, stateDir);
  const tracking = () => {
    const ready = maintainer();
    return ready?.tracked_manifest
      ? trackedManifest(ready.tracked_manifest, root, { gid: process.getgid?.() ?? -1 }) : tracked(root);
  };
  const save = (tx) => atomicJson(proposalPath(tx.id), tx);
  const snapshot = (files = tracking(), overrides = new Map()) => ({
    files, tracked_tree_sha256: fileSetSha256(root, files, overrides),
    runtime_sha256: runtimeCodeSha256({ root, overrides }),
    dependency_tree_sha256: dependencyTreeSha256(path.join(root, 'node_modules')),
  });
  const lock = async (fn) => {
    fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
    const lockPath = path.join(stateDir, 'controller.lock'); let fd;
    try { fd = fs.openSync(lockPath, 'wx', 0o600); } catch {
      try {
        const pid = Number(fs.readFileSync(lockPath, 'utf8').split(/\s+/u)[0]); let alive = Number.isInteger(pid);
        try { if (alive) process.kill(pid, 0); } catch { alive = false; }
        if (!alive) fs.rmSync(lockPath); fd = fs.openSync(lockPath, 'wx', 0o600);
      } catch { throw new Error('een andere runtimepromotie is nog bezig'); }
    }
    try { fs.writeFileSync(fd, `${process.pid}\n${iso(now())}\n`); fs.fsyncSync(fd); return await fn(); }
    finally { fs.closeSync(fd); fs.rmSync(lockPath, { force: true }); }
  };

  async function stage(args, context) {
    const actor = actorFor(context); assertRepairActor(actor);
    const patch = String(args.patch || ''); const parsed = parseUnifiedDiff(patch, LOCAL_PATCH_BYTES).files;
    return lock(() => {
      const files = tracking(); const base = snapshot(files);
      if (String(args.basis_sha256 || '') !== base.tracked_tree_sha256) throw new Error('basisboom veranderde; vraag eerst opnieuw status op');
      const replacements = new Map(); const id = randomUUID(); const txDir = path.dirname(proposalPath(id));
      fs.mkdirSync(txDir, { recursive: true, mode: 0o700 }); let records;
      try {
        records = parsed.map((item) => {
          const checked = checkedTrackedFile(root, item.path, files); const before = fs.readFileSync(checked.absolute);
          const candidate = applyHunks(before, item); if (candidate.length > 256 * 1024) throw new Error(`${item.path} wordt te groot`);
          replacements.set(item.path, candidate); const stem = sha256(item.path).slice(0, 20);
          const backup = `backup/${stem}`; const staged = `candidate/${stem}`;
          writeNewFile(path.join(txDir, backup), before); writeNewFile(path.join(txDir, staged), candidate);
          return { path: item.path, mode: checked.stat.mode & 0o777,
            preimage_sha256: sha256(before), candidate_sha256: sha256(candidate), backup, candidate: staged };
        });
      } catch (error) { fs.rmSync(txDir, { recursive: true, force: true }); throw error; }
      const candidate = snapshot(files, replacements); writeNewFile(path.join(txDir, 'change.diff'), patch);
      const created = now(); const tx = {
        protocol: PROTOCOL, id, status: 'staged', root: path.resolve(root), created_at: iso(created),
        expires_at: iso(created + TX_TTL_MS), actor, patch_sha256: sha256(patch), patch_file: 'change.diff',
        base, candidate, files: records, active_runtime: 'base', scope: { kind: 'tracked-source-only' },
      };
      save(tx); return transactionResult(tx, {
        changed: true, patch_sha256: tx.patch_sha256,
        files: records.map(({ path: file, preimage_sha256, candidate_sha256 }) => ({ file, preimage_sha256, candidate_sha256 })),
        expires_at: tx.expires_at,
      });
    });
  }

  function canonicalStatus(tx, ready) {
    if (!ready) return null;
    if (typeof readRootStatus === 'function') {
      const value = readRootStatus(ready, tx); return value ? validateRootResult(value, tx) : null;
    }
    const file = path.join(ready.maintainer_state_dir, 'status', `${tx.id}.json`);
    if (!securePath(file, { uid: 0, gid: process.getgid?.() ?? -1, mode: 0o640 })) return null;
    return validateRootResult(JSON.parse(fs.readFileSync(file, 'utf8')), tx);
  }

  function assertQueuePhase(operation, args, tx, rootStatus) {
    if (operation === 'test') {
      if (tx.status !== 'staged' || now() >= dateMillis(tx.expires_at, 'staging expires_at')) throw new Error('staging is niet actueel');
      if (snapshot().tracked_tree_sha256 !== tx.base.tracked_tree_sha256) throw new Error('basisboom veranderde sinds staging');
      return;
    }
    if (operation === 'apply') {
      if (!['root_tested', 'approval_required'].includes(rootStatus?.status)
        || args.receipt_sha256 !== rootStatus.root_receipt_sha256) throw new Error('pas_toe vereist de actuele canonieke root-proefreceipt');
      return;
    }
    if (!['committed', 'approval_required_rollback'].includes(rootStatus?.status)) throw new Error('rollback vereist een canoniek live-bevestigde toepassing');
  }

  function queueUnlocked(operation, args, context) {
    const actor = actorFor(context); assertRepairActor(actor); const tx = loadOwned(args.transactie_id, actor);
    const ready = maintainer(); if (!ready) throw new Error('root-owned maintainerhelper is niet geïnstalleerd en geattesteerd');
    const rootStatus = canonicalStatus(tx, ready); assertQueuePhase(operation, args, tx, rootStatus);
    const queued = { test: 'test_queued', apply: 'apply_queued', rollback: 'rollback_queued' }[operation];
    const requestId = sha256(`${tx.id}:${operation}:${rootStatus?.status || 'staged'}`).slice(0, 40); const requested = now();
    const request = {
      protocol: PROTOCOL, request_id: requestId, operation, transaction_id: tx.id,
      proposal_path: path.relative(stateDir, proposalPath(tx.id)), root_receipt_sha256: rootStatus?.root_receipt_sha256 || null,
      requested_at: iso(requested), expires_at: iso(requested + 10 * 60_000),
      not_before: operation === 'test' ? iso(requested) : iso(requested + 30_000),
      origin: { turn_id_sha256: sha256(String(context.turnId || '')), input_sha256: sha256(String(context.userText || '')) },
    };
    const inbox = path.join(ready.maintainer_state_dir, 'inbox', `${requestId}.json`);
    const answered = path.join(ready.maintainer_state_dir, 'responses', `${requestId}.json`);
    const duplicate = fs.existsSync(inbox) || fs.existsSync(answered);
    if (!duplicate) writeNewFile(inbox, `${JSON.stringify(request, null, 2)}\n`);
    return transactionResult(tx, { status: queued, request_id: requestId, duplicate,
      message: duplicate ? 'Deze exacte transactiefase is al duurzaam aangeboden; vraag status op.'
        : operation === 'test' ? 'De root-helper heeft een duurzame proefaanvraag; vraag status later opnieuw op.'
          : 'De root-helper heeft het productionverzoek duurzaam ontvangen; alleen canonieke status kan toepassing of livegang bewijzen.' });
  }

  const queue = (operation, args, context) => lock(() => queueUnlocked(operation, args, context));

  async function status(args = {}, context = {}) {
    const actor = actorFor(context); assertRepairActor(actor, null, false);
    const id = String(args.transactie_id || '');
    if (id) {
      const tx = loadOwned(id, actor); const rootStatus = canonicalStatus(tx, maintainer());
      return rootStatus || transactionResult(tx, { created_at: tx.created_at, expires_at: tx.expires_at, files: tx.files.map((item) => item.path) });
    }
    const current = snapshot(); const proposalsDir = path.join(stateDir, 'proposals'); const ready = maintainer();
    const recent = fs.existsSync(proposalsDir) ? fs.readdirSync(proposalsDir)
      .map((entry) => { try { return load(entry); } catch { return null; } })
      .filter((tx) => tx?.actor?.owner_sha256 === actor.owner_sha256)
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 10)
      .map((tx) => ({ id: tx.id, status: canonicalStatus(tx, ready)?.status || tx.status, created_at: tx.created_at })) : [];
    return { ok: true, changed: false, verified: true, status: 'ready',
      tracked_tree_sha256: current.tracked_tree_sha256, full_runtime_sha256: current.runtime_sha256,
      dependency_tree_sha256: current.dependency_tree_sha256, maintainer_ready: Boolean(ready), recent };
  }

  return { manage: async (args = {}, context = {}) => {
    const operation = String(args.bewerking || '').trim().toLowerCase();
    if (operation === 'status') return status(args, context);
    if (operation === 'stage') return stage(args, context);
    if (operation === 'beproef') return queue('test', args, context);
    if (operation === 'pas_toe') return queue('apply', args, context);
    if (operation === 'rollback') return queue('rollback', args, context);
    throw new Error('bewerking moet status, stage, beproef, pas_toe of rollback zijn');
  } };
}

const runtimeController = createRuntimeController();
export const manageRuntime = (args, context) => runtimeController.manage(args, context);
export const __runtimeControllerTest = {
  PROTECTED: PROTECTED_SOURCE, PROTOCOL, applyHunks, parseUnifiedDiff, sha256,
  serviceAttestationValid, treeSha256: fileSetSha256, validateRootStatus: validateRootResult,
};
