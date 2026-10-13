import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';

const O_CLOEXEC = Number(Reflect.get(fs.constants, 'O_CLOEXEC') || 0);

export const PROTOCOL = 'EvaRuntimeRepair/v3';
export const MAX_PATCH_BYTES = 256 * 1024;
export const MAX_CHANGED_LINES = 400;
export const MAX_FILES = 4;
export const ID_PATTERN = /^[a-f0-9-]{20,50}$/iu;
export const HASH_PATTERN = /^[a-f0-9]{64}$/u;
export const ROOT_GATE_NAMES = Object.freeze([
  'typecheck', 'lint', 'complexity', 'coverage', 'tests',
]);
export const RELEASE_PATHS = Object.freeze([
  '/usr/local/libexec/eva-runtime-maintainer.mjs',
  '/usr/local/libexec/eva-runtime-repair-protocol.mjs',
  '/etc/systemd/system/eva.service',
  '/etc/systemd/system/eva-runtime-maintainer.path',
  '/etc/systemd/system/eva-runtime-maintainer.service',
  '/etc/systemd/system/eva-runtime-maintainer-recover.service',
  '/etc/systemd/system/eva.service.d/runtime-maintainer.conf',
]);
export const PROTECTED_SOURCE = [
  /^src\/(?:config|db|index|runtimeversion)\.js$/u, /^src\/security\//u,
  /^src\/brain\/oauth\.js$/u, /^src\/web\/(?:access|server)\.js$/u,
  /^src\/integrations\/telegram\.js$/u,
  /^src\/mind\/(?:agent|autonomy|cognitive|communication|runtimecontroller|toolinstaller|tools|turnplan|workcells)\.js$/u,
];

const phase = (ok, verified, needsFollowup, liveVerified, runtime, options = {}) => Object.freeze({
  ok, verified, needs_followup: needsFollowup, live_verified: liveVerified,
  runtime, pointer: false, terminal: false, reason: false, gates: 'full', approval: null,
  ...options,
});

// Eén bron voor statussemantiek, pointerlevensduur en runtimeclaims.
export const PHASE_MATRIX = Object.freeze({
  staged: phase(true, false, true, false, 'base', { gates: 'empty' }),
  test_queued: phase(true, false, true, false, 'base', { gates: 'empty' }),
  apply_queued: phase(true, false, true, false, 'base'),
  rollback_queued: phase(true, false, true, false, 'candidate'),
  root_tested: phase(true, true, true, false, 'base'),
  root_test_failed: phase(false, false, false, false, 'base', { terminal: true, reason: true, gates: 'optional' }),
  approval_required: phase(true, false, true, false, 'base', { approval: 'apply' }),
  approval_required_rollback: phase(true, false, true, false, 'candidate', { approval: 'rollback' }),
  applying: phase(true, false, true, false, 'unknown', { pointer: true }),
  applied_pending_boot: phase(true, false, true, false, 'base', { pointer: true }),
  rollback_pending_boot: phase(true, false, true, false, 'candidate', { pointer: true }),
  booting: phase(true, false, true, false, 'unknown', { pointer: true }),
  committed: phase(true, true, false, true, 'candidate', { terminal: true }),
  rolled_back: phase(true, true, false, true, 'base', { terminal: true }),
  mutation_failed_rolled_back: phase(false, false, false, false, 'previous', { terminal: true, reason: true }),
  mutation_crash_rolled_back: phase(false, false, false, false, 'previous', { terminal: true, reason: true }),
  recovery_booting: phase(true, false, true, false, 'unknown', { pointer: true }),
  recovery_live_unverified: phase(false, false, true, false, 'unknown', { pointer: true, reason: true }),
  recovery_manual_intervention: phase(false, false, true, false, 'unknown', { pointer: true, reason: true }),
  recovery_tree_conflict: phase(false, false, true, false, 'unknown', { pointer: true, reason: true }),
  boot_failed_rolled_back_live: phase(false, true, false, true, 'previous', { terminal: true, reason: true }),
});

export const sha256 = (value) => createHash('sha256').update(value).digest('hex');

export function fsyncDirectory(directory) {
  const fd = fs.openSync(directory, fs.constants.O_RDONLY | O_CLOEXEC);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}

export function atomicBlob(file, value, { mode = 0o600, owner = null } = {}) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
  const flags = fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY
    | fs.constants.O_NOFOLLOW | O_CLOEXEC;
  const fd = fs.openSync(temporary, flags, mode);
  try {
    fs.writeFileSync(fd, value);
    if (owner) fs.fchownSync(fd, owner.uid, owner.gid);
    fs.fchmodSync(fd, mode); fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  fs.renameSync(temporary, file); fsyncDirectory(path.dirname(file));
}

export const atomicJson = (file, value, options = {}) => (
  atomicBlob(file, `${JSON.stringify(value, null, 2)}\n`, options));

export function writeNewFile(file, value, mode = 0o600) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const flags = fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY
    | fs.constants.O_NOFOLLOW | O_CLOEXEC;
  const fd = fs.openSync(file, flags, mode);
  try { fs.writeFileSync(fd, value); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fsyncDirectory(path.dirname(file));
}

function statPermissionsValid(stat, mode, forbidden) {
  if (mode != null) return (stat.mode & 0o777) === mode;
  return forbidden == null || (stat.mode & forbidden) === 0;
}

function securityStatValid(stat, {
  kind = 'file', uid = null, gid = null, mode = null, forbidden = null,
  nlink = null, maximum = null,
} = {}) {
  const type = kind === 'directory' ? stat.isDirectory() : stat.isFile();
  return [type, !stat.isSymbolicLink(), uid == null || stat.uid === uid,
    gid == null || stat.gid === gid, nlink == null || stat.nlink === nlink,
    maximum == null || stat.size <= maximum, statPermissionsValid(stat, mode, forbidden)].every(Boolean);
}

export function securePath(file, options = {}) {
  try {
    return securityStatValid(fs.lstatSync(file), { forbidden: 0o022, ...options });
  } catch { return false; }
}

export function readRegular(file, {
  uid = null, gid = null, mode = null, maximum = 512 * 1024, nlink = null,
} = {}) {
  const flags = fs.constants.O_RDONLY | fs.constants.O_NONBLOCK | fs.constants.O_NOFOLLOW
    | O_CLOEXEC;
  const fd = fs.openSync(file, flags);
  try {
    const stat = fs.fstatSync(fd);
    if (!securityStatValid(stat, { uid, gid, mode, nlink, maximum })) {
      throw new Error('artifact is geen veilig regulier bestand');
    }
    return fs.readFileSync(fd);
  } finally { fs.closeSync(fd); }
}

function pathLikeTraversal(value) {
  return value.startsWith('/') || value.includes('\\')
    || value.split('/').some((part) => !part || part === '.' || part === '..');
}

export function canonicalRelativePath(value) {
  if (typeof value !== 'string' || pathLikeTraversal(value)
    || !/^[A-Za-z0-9_./@+-]+$/u.test(value)) throw new Error('pad is niet canoniek relatief');
  return value;
}

export function repairSourcePath(value) {
  canonicalRelativePath(value);
  if (!/^src\/[a-z0-9_./-]+\.js$/iu.test(value)) throw new Error(`bronpad niet toegestaan: ${String(value || '?')}`);
  if (PROTECTED_SOURCE.some((pattern) => pattern.test(value))) throw new Error(`beschermde runtimebron niet toegestaan: ${value}`);
  return value;
}

export function contained(root, relative) {
  const absolute = path.resolve(root, canonicalRelativePath(relative));
  if (!absolute.startsWith(`${path.resolve(root)}${path.sep}`)) throw new Error('pad valt buiten de vaste root');
  return absolute;
}

const TRACKED_METADATA_ABSENT = 'tracked manifest unavailable: repository metadata absent';

function localGitRepository(root) {
  try {
    const repository = path.resolve(String(root || ''));
    const repositoryStat = fs.lstatSync(repository);
    const metadata = path.join(repository, '.git');
    const metadataStat = fs.lstatSync(metadata);
    if (!repositoryStat.isDirectory() || repositoryStat.isSymbolicLink()
      || fs.realpathSync(repository) !== repository
      || !metadataStat.isDirectory() || metadataStat.isSymbolicLink()
      || fs.realpathSync(metadata) !== metadata) throw new Error(TRACKED_METADATA_ABSENT);
    return repository;
  } catch {
    throw new Error(TRACKED_METADATA_ABSENT);
  }
}

export function createGitTrackedFiles({ execute = execFileSync } = {}) {
  if (typeof execute !== 'function') throw new TypeError('git executor ontbreekt');
  return (root) => {
    const repository = localGitRepository(root);
    const output = execute('/usr/bin/git', [
      '-c', `safe.directory=${repository}`, '-C', repository, 'ls-files', '-z',
    ], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
    return output.split('\0').filter(Boolean).map(canonicalRelativePath).sort();
  };
}

export const gitTrackedFiles = createGitTrackedFiles();

function regularFile(root, relative) {
  const absolute = contained(root, relative);
  const stat = fs.lstatSync(absolute);
  if (!stat.isFile() || stat.isSymbolicLink() || fs.realpathSync(absolute) !== absolute) {
    throw new Error(`boom bevat geen regulier bestand: ${relative}`);
  }
  return absolute;
}

export function fileSetSha256(root, files, overrides = new Map()) {
  const hash = createHash('sha256');
  for (const relative of files) {
    const normalized = canonicalRelativePath(relative);
    hash.update(normalized); hash.update('\0');
    hash.update(overrides.has(normalized) ? overrides.get(normalized) : fs.readFileSync(regularFile(root, normalized)));
    hash.update('\0');
  }
  return hash.digest('hex');
}

function runtimeFiles(root, directory = path.join(root, 'src'), rows = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) runtimeFiles(root, full, rows);
    else if (entry.isFile() && /\.(?:js|mjs|json)$/iu.test(entry.name)) rows.push(path.relative(root, full).replaceAll(path.sep, '/'));
    else if (entry.isSymbolicLink()) throw new Error('runtimebron bevat een symbolische omleiding');
  }
  return rows;
}

export function runtimeTreeSha256(root, overrides = new Map()) {
  const files = runtimeFiles(root);
  for (const name of ['package.json', 'package-lock.json', 'deploy/eva-runtime-repair-protocol.mjs']) {
    if (fs.existsSync(path.join(root, name))) files.push(name);
  }
  return fileSetSha256(root, files.sort(), overrides);
}

function dependencyEntries(root, directory = root, rows = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const absolute = path.join(directory, entry.name);
    rows.push(absolute);
    if (entry.isDirectory()) dependencyEntries(root, absolute, rows);
  }
  return rows;
}

function attestDependencyEntry(root, absolute, uid, readonly) {
  const stat = fs.lstatSync(absolute);
  if (uid != null && stat.uid !== uid) throw new Error('dependencyboom is niet volledig root-owned');
  if (readonly && !stat.isSymbolicLink() && (stat.mode & 0o022) !== 0) throw new Error('dependencyboom is schrijfbaar buiten root');
  if (stat.isSymbolicLink()) {
    const real = fs.realpathSync(absolute);
    if (real !== root && !real.startsWith(`${root}${path.sep}`)) throw new Error('dependency-symlink ontsnapt uit de seal');
    return ['link', Buffer.from(fs.readlinkSync(absolute))];
  }
  if (stat.isDirectory()) return ['directory', Buffer.alloc(0)];
  if (stat.isFile()) return ['file', readRegular(absolute, { uid, maximum: 512 * 1024 * 1024 })];
  throw new Error('dependencyboom bevat een speciaal bestand');
}

export function dependencyTreeSha256(directory, { uid = null, readonly = false, maximumEntries = 100_000 } = {}) {
  const root = path.resolve(directory);
  if (fs.realpathSync(root) !== root) throw new Error('dependencyroot bevat een symbolische omleiding');
  const rootStat = fs.lstatSync(root);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink() || (uid != null && rootStat.uid !== uid)
    || (readonly && (rootStat.mode & 0o022) !== 0)) throw new Error('dependencyroot is niet root-owned readonly');
  const entries = dependencyEntries(root);
  if (entries.length > maximumEntries) throw new Error('dependencyboom overschrijdt de vaste bestandsgrens');
  const hash = createHash('sha256');
  for (const absolute of entries) {
    const relative = path.relative(root, absolute).replaceAll(path.sep, '/');
    const [kind, bytes] = attestDependencyEntry(root, absolute, uid, readonly);
    hash.update(kind); hash.update('\0'); hash.update(relative); hash.update('\0'); hash.update(bytes); hash.update('\0');
  }
  return hash.digest('hex');
}

export function trackedManifest(file, root, { uid = 0, gid = null } = {}) {
  const value = JSON.parse(readRegular(file, { uid, gid, mode: 0o640, maximum: 2 * 1024 * 1024 }).toString('utf8'));
  if (value.protocol !== PROTOCOL || path.resolve(value.root) !== path.resolve(root)
    || !Array.isArray(value.files) || !value.files.length
    || JSON.stringify(value.files) !== JSON.stringify([...new Set(value.files.map(canonicalRelativePath))].sort())) {
    throw new Error('tracked manifest is niet canoniek');
  }
  return value.files;
}

function releaseArtifactValid(artifact, expectedHash, uid) {
  return path.isAbsolute(artifact) && securePath(artifact, { uid })
    && expectedHash === sha256(readRegular(artifact, { uid, maximum: 2 * 1024 * 1024 }));
}

function serviceProtocolValid(value, sourceRoot, protocolArtifact, uid) {
  const source = path.join(path.resolve(sourceRoot), 'deploy/eva-runtime-repair-protocol.mjs');
  return securePath(source, { uid })
    && value.files[protocolArtifact] === sha256(readRegular(source, { uid, maximum: 2 * 1024 * 1024 }));
}

export function attestRelease(file, expectedPaths = RELEASE_PATHS, {
  uid = 0, gid = null, mode = 0o600, sourceRoot = null, protocolArtifact = RELEASE_PATHS[1],
} = {}) {
  const value = JSON.parse(readRegular(file, { uid, gid, mode, maximum: 128 * 1024 }).toString('utf8'));
  const paths = Object.keys(value.files || {}).sort();
  if (value.protocol !== PROTOCOL || JSON.stringify(paths) !== JSON.stringify([...expectedPaths].sort())) {
    throw new Error('release-attestatie heeft niet de exacte artifactset');
  }
  dateMillis(value.issued_at, 'release issued_at');
  for (const artifact of paths) {
    if (!releaseArtifactValid(artifact, value.files[artifact], uid)) {
      throw new Error(`release-artifact wijkt af: ${artifact}`);
    }
  }
  if (sourceRoot && !serviceProtocolValid(value, sourceRoot, protocolArtifact, uid)) {
    throw new Error('geladen serviceprotocol wijkt af van het geattesteerde rootprotocol');
  }
  return value;
}

export function dateMillis(value, label) {
  if (typeof value !== 'string') throw new Error(`${label} is geen canonieke datum`);
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString() !== value) throw new Error(`${label} is geen canonieke datum`);
  return parsed;
}

function canonicalText(value, label) {
  const blob = Buffer.isBuffer(value) ? value : Buffer.from(String(value));
  if (!blob.length || blob.includes(0) || blob.includes(13)
    || blob.some((byte) => (byte < 32 && byte !== 9 && byte !== 10) || byte === 127)) throw new Error(`${label} is geen canonieke tekst`);
  let decoded;
  try { decoded = new TextDecoder('utf-8', { fatal: true }).decode(blob); }
  catch { throw new Error(`${label} is geen geldige UTF-8 tekst`); }
  if (Buffer.from(decoded).compare(blob) !== 0) throw new Error(`${label} heeft geen canonieke UTF-8 bytes`);
  return decoded;
}

function diffPath(raw, prefix) {
  if (!raw.startsWith(prefix) || !/^[ab]\/[A-Za-z0-9_./-]+$/u.test(raw)) throw new Error('diffpad is niet canoniek');
  return canonicalRelativePath(raw.slice(2));
}

function diffGitHeader(line, state) {
  const match = line.match(/^diff --git (a\/[A-Za-z0-9_./-]+) (b\/[A-Za-z0-9_./-]+)$/u);
  if (!match) return false;
  const left = diffPath(match[1], 'a/'); const right = diffPath(match[2], 'b/');
  if (left !== right) throw new Error('rename-patches zijn niet toegestaan');
  Object.assign(state, { pendingPath: left, pendingIndexed: false, current: null, hunk: null });
  return true;
}

function diffFileHeader(lines, index, state) {
  const line = lines[index];
  if (!line.startsWith('--- ')) return 0;
  const oldPath = diffPath(line.slice(4), 'a/'); const next = lines[index + 1] || '';
  if (!next.startsWith('+++ ')) throw new Error('iedere --- regel vereist direct een +++ regel');
  const newPath = diffPath(next.slice(4), 'b/');
  if (oldPath !== newPath || (state.pendingPath && state.pendingPath !== oldPath)) throw new Error('rename-patches zijn niet toegestaan');
  state.current = { path: oldPath, hunks: [] }; state.files.push(state.current);
  Object.assign(state, { pendingPath: null, pendingIndexed: false, hunk: null });
  return 1;
}

function diffHunkHeader(line, state) {
  const match = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: [^\r\n]*)?$/u);
  if (!match) return false;
  if (!state.current) throw new Error('hunk zonder bestandheader');
  const hunk = {
    oldStart: Number(match[1]), oldCount: Number(match[2] ?? 1),
    newStart: Number(match[3]), newCount: Number(match[4] ?? 1), lines: [],
  };
  if (Object.values(hunk).slice(0, 4).some((number) => !Number.isSafeInteger(number))
    || (hunk.oldStart === 0) !== (hunk.oldCount === 0)
    || (hunk.newStart === 0) !== (hunk.newCount === 0)) throw new Error('hunkheader is niet canoniek');
  state.current.hunks.push(hunk); state.hunk = hunk; return true;
}

function consumeDiffLine(lines, index, state) {
  const line = lines[index];
  if (diffGitHeader(line, state)) return 0;
  if (/^index [a-f0-9]{4,64}\.\.[a-f0-9]{4,64}$/u.test(line)
    && state.pendingPath && !state.pendingIndexed && !state.current) { state.pendingIndexed = true; return 0; }
  const skipped = diffFileHeader(lines, index, state); if (skipped) return skipped;
  if (diffHunkHeader(line, state)) return 0;
  if (line === '\\ No newline at end of file') throw new Error('bron zonder afsluitende newline is niet toegestaan');
  if (state.hunk && /^[- +]/u.test(line)) {
    state.hunk.lines.push(line); if (line[0] !== ' ') state.changed += 1; return 0;
  }
  throw new Error(`ongeldige unified-diffregel: ${line.slice(0, 80)}`);
}

const DANGEROUS_ADDITION = /(?:^\s*import\b|\bfrom\s+['"]|child_process|node:(?:fs|net|http|https|tls|dgram|worker_threads)|(?:^|\/)db\.js\b|(?:^|\/)data\/|\/home\/|\brequire\s*\(|\bimport\s*\(|\beval\s*\(|\bFunction\s*\(|\bprocess(?:\.|\[)|\.env\b|api[_ -]?key|access[_ -]?token|secret|geheim|wachtwoord)/iu;

function validateParsedDiff(state) {
  const { files, changed, pendingPath } = state;
  if (pendingPath || !files.length || files.length > MAX_FILES || changed < 1 || changed > MAX_CHANGED_LINES
    || new Set(files.map(({ path: file }) => file)).size !== files.length
    || files.some(({ hunks }) => hunks.length === 0)) throw new Error(`patch moet unieke 1-${MAX_FILES} bestanden en maximaal ${MAX_CHANGED_LINES} wijzigingen bevatten`);
  const additions = files.flatMap(({ hunks }) => hunks.flatMap(({ lines }) => lines.filter((row) => row[0] === '+').map((row) => row.slice(1))));
  if (additions.some((line) => DANGEROUS_ADDITION.test(line))) throw new Error('verboden gevaarlijke toevoeging in kandidaat');
}

export function parseUnifiedDiff(value, maximum = MAX_PATCH_BYTES) {
  const blob = Buffer.isBuffer(value) ? value : Buffer.from(String(value));
  if (!blob.length || blob.length > maximum) throw new Error(`patchpayload moet 1-${maximum} bytes zijn`);
  const lines = canonicalText(blob, 'patch').split('\n');
  const state = { files: [], changed: 0, pendingPath: null, pendingIndexed: false, current: null, hunk: null };
  for (let index = 0; index < lines.length; index += 1) {
    if (!lines[index] && index === lines.length - 1) continue;
    index += consumeDiffLine(lines, index, state);
  }
  validateParsedDiff(state); return { files: state.files, changed_lines: state.changed };
}

export function applyHunks(value, filePatch) {
  const source = canonicalText(value, filePatch.path);
  if (!source.endsWith('\n')) throw new Error(`${filePatch.path} heeft geen afsluitende newline`);
  const original = source.slice(0, -1).split('\n'); const output = []; let cursor = 0;
  for (const hunk of filePatch.hunks) {
    const start = hunk.oldStart === 0 ? 0 : hunk.oldStart - 1;
    const newStart = hunk.newStart === 0 ? 0 : hunk.newStart - 1;
    if (start < cursor || start > original.length || newStart !== output.length + start - cursor) throw new Error(`ongeldige hunkpositie in ${filePatch.path}`);
    output.push(...original.slice(cursor, start)); cursor = start;
    let oldSeen = 0; let newSeen = 0;
    for (const row of hunk.lines) {
      const content = row.slice(1);
      if (row[0] !== '+') {
        if (original[cursor] !== content) throw new Error(`patchcontext wijkt af in ${filePatch.path}`);
        cursor += 1; oldSeen += 1;
      }
      if (row[0] !== '-') { output.push(content); newSeen += 1; }
    }
    if (oldSeen !== hunk.oldCount || newSeen !== hunk.newCount) throw new Error(`hunktelling klopt niet in ${filePatch.path}`);
  }
  output.push(...original.slice(cursor)); return Buffer.from(`${output.join('\n')}\n`);
}

export function phaseContract(status) {
  const contract = PHASE_MATRIX[status];
  if (!contract) throw new Error(`onbekende runtimefase: ${String(status || '')}`);
  return contract;
}

export function activeRuntimeFor(status, tx) {
  const type = phaseContract(status).runtime; const direction = tx.mutation?.direction;
  if (['base', 'candidate'].includes(type)) return type;
  if (type === 'unknown') return null;
  if (!['apply', 'rollback'].includes(direction)) throw new Error('runtimefase mist een canonieke mutatierichting');
  if (type === 'target') return direction === 'apply' ? 'candidate' : 'base';
  if (type === 'previous') return direction === 'apply' ? 'base' : 'candidate';
  throw new Error('runtimefase heeft geen uitvoerbare runtimebinding');
}

const ROOT_RECEIPT_KEYS = new Set(`protocol transaction_id gates candidate_tree_sha256
full_runtime_sha256 dependency_tree_sha256 issued_at expires_at risk changed_lines sha256`.split(/\s+/u));
const ROOT_RESULT_KEYS = new Set(`protocol transaction_id status ok changed verified needs_followup
live_verified base_tree_sha256 candidate_tree_sha256 candidate_runtime_sha256 dependency_tree_sha256 active_runtime
active_tree_sha256 full_runtime_sha256 root_receipt_sha256 gates updated_at reason approval_binding`.split(/\s+/u));

function exactKeys(value, keys) {
  return value && Object.keys(value).length === keys.size && Object.keys(value).every((key) => keys.has(key));
}

function receiptGatesValid(gates, mode) {
  if (!Array.isArray(gates)) return false;
  if (mode === 'empty') return gates.length === 0;
  if (mode === 'optional' && gates.length === 0) return true;
  return gates.length === ROOT_GATE_NAMES.length && gates.every((gate, index) => (
    Object.keys(gate || {}).sort().join(',') === 'duration_ms,exit_code,name,stderr_sha256,stdout_sha256'
    && gate.name === ROOT_GATE_NAMES[index] && gate.exit_code === 0
    && Number.isInteger(gate.duration_ms) && gate.duration_ms >= 0
    && HASH_PATTERN.test(String(gate.stdout_sha256 || '')) && HASH_PATTERN.test(String(gate.stderr_sha256 || ''))));
}

export function validateRootReceipt(tx) {
  const receipt = tx.root_receipt; const unsigned = { ...receipt }; delete unsigned.sha256;
  const phaseValue = phaseContract(tx.status);
  const issued = dateMillis(receipt?.issued_at, 'rootreceipt issued_at');
  const expires = dateMillis(receipt?.expires_at, 'rootreceipt expires_at');
  if (![receiptIdentityValid(receipt, tx), receiptBindingValid(receipt, tx),
    receiptHashValid(receipt, unsigned), receiptWindowValid(issued, expires), receiptLinesValid(receipt),
    receiptGatesValid(receipt?.gates, phaseValue.gates)].every(Boolean)) {
    throw new Error('canonical root-receipt ongeldig');
  }
  return receipt;
}

function receiptIdentityValid(receipt, tx) {
  return [exactKeys(receipt, ROOT_RECEIPT_KEYS), receipt?.protocol === PROTOCOL,
    receipt?.transaction_id === tx.id, receipt?.risk === 'review'].every(Boolean);
}

function receiptBindingValid(receipt, tx) {
  return [receipt?.candidate_tree_sha256 === tx.candidate?.tracked_tree_sha256,
    receipt?.full_runtime_sha256 === tx.candidate?.runtime_sha256,
    receipt?.dependency_tree_sha256 === tx.candidate?.dependency_tree_sha256].every(Boolean);
}

const receiptHashValid = (receipt, unsigned) => HASH_PATTERN.test(String(receipt?.sha256 || ''))
  && receipt?.sha256 === sha256(JSON.stringify(unsigned));
const receiptWindowValid = (issued, expires) => expires > issued
  && expires - issued <= 25 * 60 * 60_000;
const receiptLinesValid = (receipt) => Number.isInteger(receipt?.changed_lines)
  && receipt.changed_lines >= 1 && receipt.changed_lines <= MAX_CHANGED_LINES;

function projectedRuntime(tx, status) {
  const active = activeRuntimeFor(status, tx);
  return {
    active_runtime: active,
    active_tree_sha256: active ? tx[active]?.tracked_tree_sha256 : null,
    full_runtime_sha256: active ? tx[active]?.runtime_sha256 : null,
  };
}

export function transactionResult(tx, extras = {}) {
  const status = extras.status || tx.status; const contract = phaseContract(status);
  const runtime = projectedRuntime(tx, status); const receipt = tx.root_receipt;
  const value = {
    protocol: PROTOCOL, transaction_id: tx.id, status,
    ok: contract.ok, changed: false, verified: contract.verified,
    needs_followup: contract.needs_followup, live_verified: contract.live_verified,
    base_tree_sha256: tx.base.tracked_tree_sha256,
    candidate_tree_sha256: tx.candidate.tracked_tree_sha256,
    candidate_runtime_sha256: tx.candidate.runtime_sha256,
    dependency_tree_sha256: tx.candidate.dependency_tree_sha256,
    ...runtime, root_receipt_sha256: receipt?.sha256 || null,
    gates: receipt?.gates?.map(({ name, exit_code, duration_ms }) => ({ name, exit_code, duration_ms })) || [],
    reason: null, approval_binding: null,
  };
  return Object.assign(value, extras, {
    status, ok: contract.ok, verified: contract.verified,
    needs_followup: contract.needs_followup, live_verified: contract.live_verified, ...runtime,
  });
}

function publicGatesValid(gates, mode) {
  if (!Array.isArray(gates)) return false;
  if (mode === 'empty') return gates.length === 0;
  if (mode === 'optional' && gates.length === 0) return true;
  return gates.length === ROOT_GATE_NAMES.length && gates.every((gate, index) => (
    Object.keys(gate || {}).sort().join(',') === 'duration_ms,exit_code,name'
    && gate.name === ROOT_GATE_NAMES[index] && gate.exit_code === 0
    && Number.isInteger(gate.duration_ms) && gate.duration_ms >= 0));
}

function approvalValid(binding, contract, tx, receiptHash) {
  if (!contract.approval) return binding == null;
  return Object.keys(binding || {}).sort().join(',')
    === 'candidate_tree_sha256,operation,root_receipt_sha256,transaction_id'
    && binding.operation === contract.approval && binding.transaction_id === tx.id
    && binding.candidate_tree_sha256 === tx.candidate.tracked_tree_sha256
    && binding.root_receipt_sha256 === receiptHash;
}

function publicReasonValid(value, contract) {
  const present = typeof value.reason === 'string' && value.reason.length > 0 && value.reason.length <= 300;
  return contract.reason ? present : value.reason == null;
}

export function validateRootResult(value, tx) {
  const contract = phaseContract(value?.status); const runtime = projectedRuntime(tx, value.status);
  if (![rootResultIdentityValid(value, tx), rootResultBooleansValid(value, contract),
    rootResultHashesValid(value, tx, runtime), publicGatesValid(value?.gates, contract.gates),
    approvalValid(value?.approval_binding, contract, tx, value?.root_receipt_sha256),
    publicReasonValid(value, contract)].every(Boolean)) {
    throw new Error('root-status hoort niet bij de lokale kandidaat');
  }
  dateMillis(value.updated_at, 'rootstatus updated_at'); return value;
}

function rootResultIdentityValid(value, tx) {
  return [exactKeys(value, ROOT_RESULT_KEYS), value?.protocol === PROTOCOL,
    value?.transaction_id === tx.id,
    HASH_PATTERN.test(String(value?.root_receipt_sha256 || ''))].every(Boolean);
}

function rootResultBooleansValid(value, contract) {
  return [value?.ok === contract.ok, value?.changed === false,
    value?.verified === contract.verified, value?.needs_followup === contract.needs_followup,
    value?.live_verified === contract.live_verified].every(Boolean);
}

function rootResultHashesValid(value, tx, runtime) {
  return [value?.base_tree_sha256 === tx.base.tracked_tree_sha256,
    value?.candidate_tree_sha256 === tx.candidate.tracked_tree_sha256,
    value?.candidate_runtime_sha256 === tx.candidate.runtime_sha256,
    value?.dependency_tree_sha256 === tx.candidate.dependency_tree_sha256,
    value?.active_runtime === runtime.active_runtime,
    value?.active_tree_sha256 === runtime.active_tree_sha256,
    value?.full_runtime_sha256 === runtime.full_runtime_sha256].every(Boolean);
}
