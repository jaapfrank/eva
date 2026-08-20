import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { config } from '../config.js';
import { redactSensitiveText } from '../security/redaction.js';
import { canonicalJson, normalizeDutchText as normalize } from '../util.js';

export const WORKSPACE_FILE_MANIFEST_LIMITS = Object.freeze({
  depthCap: 6,
  entryCap: 1_500,
  matchCap: 128,
  pathBytesCap: 12_000,
  queryBytesCap: 240,
  queryTermCap: 16,
});
export const WORKSPACE_FILE_MANIFEST_IGNORED_DIRECTORIES = new Set([
  '.cache', '.git', '.npm-cache', '.venv', 'build', 'coverage', 'dist',
  'node_modules', 'venv', '__pycache__',
]);
const CONTRACT = 'WorkspaceFileManifest/v1';
const WORKSPACE_ROOT = path.resolve(config.workspaceDir);
const pathInside = (root, candidate) => candidate === root || candidate.startsWith(`${root}${path.sep}`);
const canonicalPathSort = (left, right) => left < right ? -1 : left > right ? 1 : 0;
const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function normalizedSearchTerms(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return [];
  if (Buffer.byteLength(raw, 'utf8') > WORKSPACE_FILE_MANIFEST_LIMITS.queryBytesCap) {
    throw new Error('manifestzoekvraag is langer dan 240 bytes');
  }
  const terms = normalize(raw).split(/[^\p{L}\p{N}_.-]+/u).filter(Boolean);
  if (!terms.length || terms.some((term) => term.length < 3)) {
    throw new Error('manifestzoekvraag bevat een term korter dan drie tekens');
  }
  const unique = [...new Set(terms)];
  if (unique.length > WORKSPACE_FILE_MANIFEST_LIMITS.queryTermCap) {
    throw new Error('manifestzoekvraag bevat meer dan zestien unieke termen');
  }
  return unique;
}
function searchTermOccurs(normalizedText, term) {
  if (term.length > 5) return normalizedText.includes(term);
  return normalizedText.split(/[^\p{L}\p{N}_.-]+/u).includes(term);
}
function canonicalQuery(value) {
  const terms = normalizedSearchTerms(value);
  return terms.length ? terms.join('|') : null;
}
export function workspaceManifestSearchTerms(value) {
  const query = canonicalQuery(value);
  return query == null ? [] : query.split('|');
}
function canonicalWorkspacePath(input, { directory = false } = {}) {
  const raw = String(input || '').trim();
  if (!raw || raw.includes('\0')) throw new Error('ongeldig werkruimtepad');
  const portable = raw.replaceAll('\\', '/');
  if (portable.split('/').includes('..')) throw new Error('padtraversal');
  const withoutDot = portable.replace(/^\.\//u, '');
  const relative = withoutDot === 'data/workspace' ? ''
    : withoutDot.startsWith('data/workspace/') ? withoutDot.slice('data/workspace/'.length)
      : withoutDot;
  const absolute = path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(WORKSPACE_ROOT, relative);
  if (!pathInside(WORKSPACE_ROOT, absolute) || (!directory && absolute === WORKSPACE_ROOT)) {
    throw new Error('pad valt buiten data/workspace');
  }
  return absolute;
}
function displayPath(input) {
  const absolute = canonicalWorkspacePath(input, { directory: true });
  const relative = path.relative(WORKSPACE_ROOT, absolute).replaceAll(path.sep, '/');
  return relative ? `data/workspace/${relative}` : 'data/workspace';
}
function manifestBody(value) {
  return {
    contract: value.contract, root: value.root, query: value.query,
    matchKind: value.matchKind, paths: value.paths, complete: value.complete,
    truncated: value.truncated, generated: value.generated,
    snapshotSha256: value.snapshotSha256,
  };
}
function sealedManifest(value) {
  const body = manifestBody(value);
  return Object.freeze({
    ...body,
    sha256: sha256(canonicalJson(body)),
  });
}

/** Verifieert uitsluitend een begrensde filename-scope, geen semantische universaliteit. */
export function validWorkspaceFileManifest(value, expectations = {}) {
  try {
    const expectedKeys = [
      'complete', 'contract', 'generated', 'matchKind', 'paths', 'query', 'root', 'sha256',
      'snapshotSha256', 'truncated',
    ];
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join('\n') !== expectedKeys.sort().join('\n')
      || value.contract !== CONTRACT || value.matchKind !== 'filename'
      || value.generated !== 'pre_effect' || typeof value.complete !== 'boolean'
      || typeof value.truncated !== 'boolean' || (value.complete && value.truncated)
      || !Array.isArray(value.paths) || value.paths.length > WORKSPACE_FILE_MANIFEST_LIMITS.matchCap
      || !/^[a-f0-9]{64}$/u.test(String(value.snapshotSha256 || ''))
      || !/^[a-f0-9]{64}$/u.test(String(value.sha256 || ''))) return false;
    const root = displayPath(value.root);
    const query = value.query == null ? null : canonicalQuery(value.query);
    if (root !== value.root || query !== value.query
      || (Object.hasOwn(expectations, 'root') && displayPath(expectations.root) !== root)
      || (Object.hasOwn(expectations, 'query') && canonicalQuery(expectations.query) !== query)) return false;
    const rootPath = canonicalWorkspacePath(root, { directory: true });
    const terms = query == null ? [] : normalizedSearchTerms(query);
    const paths = value.paths.map((candidate) => {
      const absolute = canonicalWorkspacePath(candidate);
      const display = displayPath(absolute);
      if (display !== candidate || !pathInside(rootPath, absolute)
        || (query == null && path.dirname(absolute) !== rootPath)
        || (query != null && (!terms.length || !terms.some((term) => (
          searchTermOccurs(normalize(path.basename(absolute)), term)
        ))))) throw new Error('ongeldig manifestpad');
      return display;
    });
    const sortedPaths = [...paths].sort(canonicalPathSort);
    if (new Set(paths).size !== paths.length
      || paths.some((candidate, index) => candidate !== sortedPaths[index])
      || Buffer.byteLength(canonicalJson(paths), 'utf8') > WORKSPACE_FILE_MANIFEST_LIMITS.pathBytesCap) return false;
    return sha256(canonicalJson(manifestBody(value))) === value.sha256;
  } catch { return false; }
}

export function createWorkspaceManifestState(directory, query) {
  const root = fs.realpathSync(config.workspaceDir);
  if (!pathInside(root, directory)) return null;
  const state = {
    root: displayPath(directory), query: canonicalQuery(query), paths: [],
    complete: true, truncated: false, snapshotEntries: [], snapshotPaths: new Set(),
  };
  observeWorkspaceManifestEntry(state, directory, 'directory');
  return state;
}
export function markWorkspaceManifestAmbiguous(state) {
  if (state) state.complete = false;
}
export function markWorkspaceManifestTruncated(state) {
  if (!state) return;
  state.complete = false;
  state.truncated = true;
}
function filesystemEntryKind(stat) {
  if (stat.isSymbolicLink()) return 'symlink';
  if (stat.isDirectory()) return 'directory';
  if (stat.isFile()) return 'file';
  return 'other';
}
export function observeWorkspaceManifestEntry(state, absolute, expectedKind = '') {
  if (!state) return '';
  let display = '';
  try {
    display = displayPath(absolute);
    const stat = fs.lstatSync(absolute);
    const kind = filesystemEntryKind(stat);
    if (expectedKind && kind !== expectedKind) markWorkspaceManifestAmbiguous(state);
    if (['symlink', 'other'].includes(kind)) markWorkspaceManifestAmbiguous(state);
    if (state.snapshotPaths.has(display)) markWorkspaceManifestAmbiguous(state);
    state.snapshotPaths.add(display);
    state.snapshotEntries.push({
      path: display,
      kind,
      ...(kind === 'directory' ? { device: String(stat.dev), inode: String(stat.ino) } : {}),
    });
    return kind;
  } catch {
    markWorkspaceManifestAmbiguous(state);
    if (display && !state.snapshotPaths.has(display)) {
      state.snapshotPaths.add(display);
      state.snapshotEntries.push({ path: display, kind: 'unreadable' });
    }
    return '';
  }
}
export function addWorkspaceManifestFile(state, readable, { matchQuery = true } = {}) {
  if (!state) return;
  const display = displayPath(readable);
  if (redactSensitiveText(display) !== display) return markWorkspaceManifestAmbiguous(state);
  const terms = state.query == null ? [] : normalizedSearchTerms(state.query);
  if (matchQuery && (!terms.length || !terms.some((term) => (
    searchTermOccurs(normalize(path.basename(readable)), term)
  )))) return;
  if (state.paths.length >= WORKSPACE_FILE_MANIFEST_LIMITS.matchCap
    || Buffer.byteLength(canonicalJson([...state.paths, display]), 'utf8')
      > WORKSPACE_FILE_MANIFEST_LIMITS.pathBytesCap) return markWorkspaceManifestTruncated(state);
  state.paths.push(display);
}
export function finishWorkspaceFileManifest(state) {
  if (!state) return null;
  state.paths.sort(canonicalPathSort);
  state.snapshotEntries.sort((left, right) => (
    canonicalPathSort(left.path, right.path) || canonicalPathSort(left.kind, right.kind)
  ));
  const snapshotSha256 = sha256(canonicalJson({
    root: state.root, query: state.query, entries: state.snapshotEntries,
  }));
  return sealedManifest({
    contract: CONTRACT, root: state.root, query: state.query, matchKind: 'filename',
    paths: state.paths, complete: state.complete, truncated: state.truncated,
    generated: 'pre_effect', snapshotSha256,
  });
}

function ignoredManifestDirectory(absolute) {
  return WORKSPACE_FILE_MANIFEST_IGNORED_DIRECTORIES.has(path.basename(absolute).toLowerCase())
    || /(?:^|[\\/])test-artifacts(?:[\\/]|$)/iu.test(absolute);
}
function collectDirectManifest(state, directory) {
  let entries;
  try {
    entries = fs.readdirSync(directory, { withFileTypes: true })
      .sort((left, right) => canonicalPathSort(left.name, right.name));
  } catch {
    markWorkspaceManifestAmbiguous(state);
    return;
  }
  for (const [index, entry] of entries.entries()) {
    if (index >= WORKSPACE_FILE_MANIFEST_LIMITS.entryCap) {
      markWorkspaceManifestTruncated(state);
      break;
    }
    const absolute = path.join(directory, entry.name);
    const expected = entry.isSymbolicLink() ? 'symlink'
      : entry.isDirectory() ? 'directory' : entry.isFile() ? 'file' : 'other';
    const kind = observeWorkspaceManifestEntry(state, absolute, expected);
    if (kind === 'directory') continue;
    if (kind !== 'file') {
      markWorkspaceManifestAmbiguous(state);
      continue;
    }
    addWorkspaceManifestFile(state, absolute, { matchQuery: false });
  }
}
function collectRecursiveManifest(state, directory, depth, visited) {
  if (depth > WORKSPACE_FILE_MANIFEST_LIMITS.depthCap) {
    markWorkspaceManifestTruncated(state);
    return;
  }
  let entries;
  try {
    entries = fs.readdirSync(directory, { withFileTypes: true })
      .sort((left, right) => canonicalPathSort(left.name, right.name));
  } catch {
    markWorkspaceManifestAmbiguous(state);
    return;
  }
  for (const entry of entries) {
    if (visited.count >= WORKSPACE_FILE_MANIFEST_LIMITS.entryCap) {
      markWorkspaceManifestTruncated(state);
      break;
    }
    visited.count += 1;
    const absolute = path.join(directory, entry.name);
    const expected = entry.isSymbolicLink() ? 'symlink'
      : entry.isDirectory() ? 'directory' : entry.isFile() ? 'file' : 'other';
    const kind = observeWorkspaceManifestEntry(state, absolute, expected);
    if (kind === 'directory') {
      if (ignoredManifestDirectory(absolute)) {
        markWorkspaceManifestAmbiguous(state);
      } else if (depth >= WORKSPACE_FILE_MANIFEST_LIMITS.depthCap) {
        markWorkspaceManifestTruncated(state);
      } else {
        collectRecursiveManifest(state, absolute, depth + 1, visited);
      }
      continue;
    }
    if (kind === 'file') addWorkspaceManifestFile(state, absolute);
    else markWorkspaceManifestAmbiguous(state);
  }
}
function collectCurrentWorkspaceFileManifest(root, query) {
  const directory = canonicalWorkspacePath(root, { directory: true });
  const real = fs.realpathSync(directory);
  if (real !== directory || !pathInside(fs.realpathSync(WORKSPACE_ROOT), real)
    || !fs.lstatSync(directory).isDirectory()) throw new Error('manifestroot is geen echte werkruimtemap');
  const state = createWorkspaceManifestState(directory, query);
  if (!state) throw new Error('manifestroot valt buiten de werkruimte');
  if (state.query == null) collectDirectManifest(state, directory);
  else collectRecursiveManifest(state, directory, 0, { count: 0 });
  return finishWorkspaceFileManifest(state);
}

/**
 * Sterke hostcontrole voor een eerder uitgegeven filename-snapshot. De gewone
 * validator blijft syntactisch; deze helper raakt bewust het actuele bestandssysteem.
 */
export function revalidateWorkspaceFileManifest(value, expectations = {}) {
  try {
    if (!validWorkspaceFileManifest(value, expectations)
      || value.complete !== true || value.truncated !== false) return false;
    const first = collectCurrentWorkspaceFileManifest(value.root, value.query);
    const second = collectCurrentWorkspaceFileManifest(value.root, value.query);
    return first.complete === true && second.complete === true
      && first.truncated === false && second.truncated === false
      && canonicalJson(first) === canonicalJson(value)
      && canonicalJson(second) === canonicalJson(value);
  } catch { return false; }
}
