import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const directoryFlags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW;
const identity = stat => ({dev: stat.dev, ino: stat.ino});
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const hex = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function privateDirectory(fd) {
  const stat = fs.fstatSync(fd);
  if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o7777) !== 0o700 || !Number.isSafeInteger(stat.dev) || !Number.isSafeInteger(stat.ino)) throw Error('relocation_directory_refused');
  return identity(stat);
}
export function readPrivateFile(file, maximum = 4194304) {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const before = fs.fstatSync(fd);
    if (!before.isFile() || before.uid !== process.getuid() || (before.mode & 0o7777) !== 0o400 || before.nlink !== 1 || before.size > maximum) throw Error('relocation_authority_refused');
    const bytes = fs.readFileSync(fd);
    const after = fs.fstatSync(fd);
    if (before.size !== bytes.length || ['dev','ino','mode','nlink','uid','gid','size','mtimeMs','ctimeMs'].some(key => before[key] !== after[key])) throw Error('relocation_authority_changed');
    return bytes;
  } finally { fs.closeSync(fd); }
}
export function physicalHashes(executable) {
  return {
    binary_sha256: hash(fs.readFileSync(executable)),
    client_sha256: hash(fs.readFileSync(new URL('./broker-client.mjs', import.meta.url))),
    resolver_sha256: hash(fs.readFileSync(new URL('./project-journal.mjs', import.meta.url))),
    relocation_sha256: hash(fs.readFileSync(new URL('./relocation.mjs', import.meta.url))),
    server_sha256: hash(fs.readFileSync(new URL('./mcp-server.mjs', import.meta.url))),
  };
}
function originalBinding(bytes) {
  const binding = JSON.parse(bytes);
  if (binding.version !== 2 || binding.journal_mode !== 'per_path_prefix_v1'
      || !hex(binding.project_journal_identity) || !hex(binding.binary_sha256)
      || !hex(binding.client_sha256) || !hex(binding.resolver_sha256)) throw Error('relocation_source_binding_refused');
  return binding;
}

// Offline preparation only: stdout snapshots are built by the native journal
// parser without publication, synchronization or recovery. This does not grant
// authority. The host must approve these exact bytes and mount the package read
// only as its binding and relocation files before resuming the restored service.
export function prepareRelocation({executable, rootFd, journalFd, originalBindingBytes, logicalCatalogIdentity, projectKeys}) {
  const old = originalBinding(originalBindingBytes);
  if (!hex(logicalCatalogIdentity) || !Array.isArray(projectKeys) || projectKeys.length === 0
      || new Set(projectKeys).size !== projectKeys.length || projectKeys.some(key => !hex(key))) throw Error('relocation_projects_refused');
  const root = privateDirectory(rootFd), journal = privateDirectory(journalFd);
  if (equal(root, old.root) || equal(journal, old.journal)) throw Error('relocation_requires_new_directories');
  const projects = {}, snapshots = {};
  for (const key of projectKeys) {
    const childFd = fs.openSync(`/proc/self/fd/${journalFd}/${key}`, directoryFlags);
    try {
      const child = privateDirectory(childFd);
      const markerBytes = readPrivateFile(`/proc/self/fd/${childFd}/.project-identity.json`, 2048);
      const marker = JSON.parse(markerBytes);
      if (marker.version !== 1 || marker.project_key !== key || !equal(marker.root, old.journal)
          || equal(marker.child, child)) throw Error('relocation_marker_refused');
      const result = spawnSync(executable, ['--relocation-snapshot'], {
        input: Buffer.alloc(0), stdio: ['pipe', 'pipe', 'pipe', rootFd, childFd],
        env: {PATH: '/usr/bin:/bin', LANG: 'C'}, timeout: 10000, maxBuffer: 4194304,
      });
      if (result.status !== 0 || result.error || result.signal) throw Error('relocation_snapshot_refused: ' + String(result.stderr));
      const snapshot = result.stdout;
      if (snapshot.subarray(0, 8).toString() !== 'EVWWREL1') throw Error('relocation_snapshot_refused');
      const count = snapshot.readUInt32BE(120);
      if (snapshot.length !== 124 + 510 * count + 32) throw Error('relocation_snapshot_refused');
      // This provider accepts flat project artifact names. Mixed old/new source
      // parents imply a prior relocation; do not silently rebase its authority.
      for (let i = 0; i < count; i++) {
        const offset = 124 + 510 * i + 32;
        if (snapshot.readBigUInt64BE(offset) !== BigInt(old.root.dev)
            || snapshot.readBigUInt64BE(offset + 8) !== BigInt(old.root.ino)) throw Error('relocation_chain_unsupported');
      }
      projects[key] = {marker_base64: markerBytes.toString('base64'), marker_sha256: hash(markerBytes), old_root: marker.root, old_child: marker.child,
        root: journal, child, snapshot_sha256: hash(snapshot), prefix_length: Number(snapshot.readBigUInt64BE(8)), prefix_sha256: snapshot.subarray(16, 48).toString('hex')};
      snapshots[key] = snapshot;
    } finally { fs.closeSync(childFd); }
  }
  const binding = {version: 3, kind: 'host_authorized_relocation_v1', original_binding_base64: Buffer.from(originalBindingBytes).toString('base64'),
    logical_provider_identity: hash(originalBindingBytes), logical_catalog_identity: logicalCatalogIdentity,
    physical: {...physicalHashes(executable), root, journal}, projects};
  return {bindingBytes: Buffer.from(JSON.stringify(binding)), snapshots};
}

// Call only for a package approved by the host. No tool/conversation input may
// supply a package, path, executable, fd or logical identity to this function.
export function validateRelocationBinding(bindingBytes, {executable, rootFd, journalFd}) {
  const binding = JSON.parse(bindingBytes);
  if (binding.version !== 3 || binding.kind !== 'host_authorized_relocation_v1') throw Error('relocation_binding_refused');
  const originalBytes = Buffer.from(binding.original_binding_base64, 'base64');
  const old = originalBinding(originalBytes);
  if (hash(originalBytes) !== binding.logical_provider_identity || !hex(binding.logical_catalog_identity)
      || !equal(binding.physical, {...physicalHashes(executable), root: privateDirectory(rootFd), journal: privateDirectory(journalFd)})
      || !binding.projects || Array.isArray(binding.projects)) throw Error('relocation_binding_refused');
  for (const [key, project] of Object.entries(binding.projects)) {
    const marker = Buffer.from(project.marker_base64, 'base64');
    const parsed = JSON.parse(marker);
    if (!hex(key) || !hex(project.snapshot_sha256) || !hex(project.prefix_sha256)
        || !Number.isSafeInteger(project.prefix_length) || project.prefix_length < 0
        || hash(marker) !== project.marker_sha256 || parsed.version !== 1 || parsed.project_key !== key
        || !equal(parsed.root, old.journal) || !equal(parsed.root, project.old_root)
        || !equal(parsed.child, project.old_child) || !equal(project.root, binding.physical.journal)) throw Error('relocation_project_refused');
  }
  return {binding, originalBytes, original: old};
}

export function openRelocationSnapshot(project, file) {
  const bytes = readPrivateFile(file);
  if (hash(bytes) !== project.snapshot_sha256 || bytes.subarray(0, 8).toString() !== 'EVWWREL1'
      || Number(bytes.readBigUInt64BE(8)) !== project.prefix_length || bytes.subarray(16, 48).toString('hex') !== project.prefix_sha256) throw Error('relocation_snapshot_binding_mismatch');
  // Re-open then hash the held descriptor, so path substitution between opens
  // cannot substitute authority in the child process.
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const st = fs.fstatSync(fd);
    if (!st.isFile() || st.uid !== process.getuid() || st.nlink !== 1 || (st.mode & 0o7777) !== 0o400
        || st.size !== bytes.length || !fs.readFileSync(fd).equals(bytes)) throw Error('relocation_snapshot_binding_mismatch');
    return fd;
  } catch (error) { fs.closeSync(fd); throw error; }
}
