import fs from 'node:fs';

const directoryFlags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW;
const markerName = '.project-identity.json';
const identity = stat => ({dev: stat.dev, ino: stat.ino});
function privateDirectory(fd) {
  const stat = fs.fstatSync(fd);
  if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o700 || !Number.isSafeInteger(stat.ino) || !Number.isSafeInteger(stat.dev)) throw Error('journal_directory_invalid');
  return stat;
}

// The caller owns the returned descriptor. No request supplies a filesystem root.
export function resolveProjectJournal(rootFd, artifactPath, {observe = false, relocation = null} = {}) {
  const match = /^([a-f0-9]{64})-[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.exec(artifactPath);
  if (!match || artifactPath.length > 255) throw Error('project_path_invalid');
  const key = match[1];
  const root = privateDirectory(rootFd);
  const parentPath = `/proc/self/fd/${rootFd}`;
  const childPath = `${parentPath}/${key}`;
  let created = false;
  if (!observe && !relocation) {
    try { fs.mkdirSync(childPath, {mode: 0o700}); created = true; }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  const fd = fs.openSync(childPath, directoryFlags);
  try {
    const child = privateDirectory(fd);
    if (child.dev !== root.dev) throw Error('journal_device_mismatch');
    const expected = {version: 1, project_key: key, root: identity(root), child: identity(child)};
    const heldPath = `/proc/self/fd/${fd}`;
    if (created) {
      // Publish only fully written metadata. Interrupted initialization stays closed.
      const pending = `${heldPath}/.project-identity.pending`;
      const marker = fs.openSync(pending, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o400);
      try { fs.writeFileSync(marker, JSON.stringify(expected)); fs.fsyncSync(marker); }
      finally { fs.closeSync(marker); }
      fs.linkSync(pending, `${heldPath}/${markerName}`);
      fs.unlinkSync(pending);
      fs.fsyncSync(fd);
      fs.fsyncSync(rootFd);
    }
    const markerFd = fs.openSync(`${heldPath}/${markerName}`, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    try {
      const stat = fs.fstatSync(markerFd);
      if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o400 || stat.nlink !== 1 || stat.size > 2048) throw Error('journal_identity_invalid');
      const bytes = fs.readFileSync(markerFd);
      const actual = JSON.parse(bytes);
      if (relocation) {
        if (created || !bytes.equals(Buffer.from(relocation.marker_base64, 'base64'))
            || JSON.stringify(relocation.root) !== JSON.stringify(identity(root))
            || JSON.stringify(relocation.child) !== JSON.stringify(identity(child))
            || actual.project_key !== key) throw Error('journal_relocation_mismatch');
      } else if (JSON.stringify(actual) !== JSON.stringify(expected)) throw Error('journal_identity_mismatch');
    } finally { fs.closeSync(markerFd); }
    const named = fs.openSync(childPath, directoryFlags);
    try { const stat = privateDirectory(named); if (stat.dev !== child.dev || stat.ino !== child.ino) throw Error('journal_directory_rebound'); }
    finally { fs.closeSync(named); }
    // A concurrent reader of newly published metadata must establish durability
    // before executing. Observe deliberately performs no synchronization or repair.
    if (!observe) { fs.fsyncSync(fd); fs.fsyncSync(rootFd); }
    return fd;
  } catch (error) { fs.closeSync(fd); throw error; }
}
