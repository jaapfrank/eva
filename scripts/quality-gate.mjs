import { spawn } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import Module, { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path, { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const bootstrap = globalThis.__EVA_QUALITY_GATE_PRIVATE_BOOTSTRAP__;
if (bootstrap && process.argv[1]) {
  throw new Error('Private quality-gate workerbootstrap is niet geldig bij publieke argv-invocation');
}
const importedGateFile = bootstrap?.gateFile ?? fileURLToPath(import.meta.url);
const directInvocationPath = !bootstrap && process.argv[1] ? resolve(process.argv[1]) : null;
let directInvocationDescriptor = null;
let directInvocationMetadata = null;
if (directInvocationPath === importedGateFile) {
  if (!Number.isInteger(fs.constants.O_NOFOLLOW)
    || !Number.isInteger(fs.constants.O_NONBLOCK)) {
    throw new Error('Directe quality-gate binding vereist O_NOFOLLOW en O_NONBLOCK');
  }
  directInvocationDescriptor = fs.openSync(importedGateFile,
    fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK
      | (fs.constants.O_CLOEXEC || 0));
  directInvocationMetadata = fs.fstatSync(directInvocationDescriptor, { bigint: true });
  if (!directInvocationMetadata.isFile()) {
    fs.closeSync(directInvocationDescriptor);
    directInvocationDescriptor = null;
    throw new Error('Directe quality-gate executable is geen regulier bestand');
  }
}
const gateFile = bootstrap?.gateFile ?? fileURLToPath(import.meta.url);
const root = bootstrap?.root ?? resolve(dirname(gateFile), '..');
const require = createRequire(bootstrap?.requireFrom ?? import.meta.url);
const typescriptEntrypoint = require.resolve('typescript');
const TYPECHECK_DEBT_CONTRACT = 'EvaTypeScriptDiagnosticDebt/v4';
const TYPECHECK_DIAGNOSTIC_CONTRACT = 'EvaTypeScriptDiagnostic/v3';
const TYPECHECK_INPUT_RECEIPT_CONTRACT = 'EvaTypeScriptInputReceipt/v3';
const TYPECHECK_DEBT_MANIFEST = 'typescript-diagnostic-debt.json';
const TYPECHECK_PRIVATE_WORKER_PROTOCOL = 'EvaTypeScriptPrivateWorker/v1';
const EXPECTED_TYPESCRIPT_VERSION = '5.9.3';
const HASH_PATTERN = /^[a-f0-9]{64}$/u;
const SUBPROCESS_TIMEOUT_MS = 120_000;
const TYPECHECK_MEASUREMENT_TIMEOUT_MS = 120_000;
const MAX_INPUT_FILES = 100_000;
const MAX_INPUT_DEPTH = 64;
const MAX_INPUT_BYTES = 536_870_912;
const MAX_SINGLE_INPUT_BYTES = 134_217_728;
const MAX_CONTROL_FILE_BYTES = 16_777_216;
const MAX_WORKER_EVIDENCE_BYTES = 67_108_864;
const MAX_NODE_EXECUTABLE_BYTES = 268_435_456;
const MAX_MANIFEST_ROWS = 100_000;
const MAX_DIAGNOSTIC_COUNT = 1_000_000;
const TYPESCRIPT_PACKAGE_ROOT = resolve(dirname(typescriptEntrypoint), '..');
let ts = null;
let loadedCompilerBinding = null;
let compilerEntrypointPreloaded = Boolean(require.cache[typescriptEntrypoint]);
const compilerEntrypointIsCached = () => Boolean(require.cache[typescriptEntrypoint]);

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function canonicalJsonData(value) {
  if (Array.isArray(value)) return value.map(canonicalJsonData);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort()
      .map((key) => [key, canonicalJsonData(value[key])]));
  }
  return value;
}

const canonicalJson = (value) => JSON.stringify(canonicalJsonData(value));

function exactKeys(value, expected, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...expected].sort())) {
    throw new Error(`${label} heeft niet de exacte sleutelset`);
  }
}

function regularCanonicalFile(file, expectedRoot = root) {
  const absolute = path.resolve(expectedRoot, file);
  const prefix = `${path.resolve(expectedRoot)}${path.sep}`;
  const status = fs.lstatSync(absolute);
  if (!absolute.startsWith(prefix) || !status.isFile() || status.isSymbolicLink()
    || fs.realpathSync.native(absolute) !== absolute) {
    throw new Error(`TypeScript-bron valt buiten de reguliere repositorygrens: ${file}`);
  }
  return {
    absolute,
    relative: path.relative(expectedRoot, absolute).split(path.sep).join('/'),
  };
}

function stableMetadata(status) {
  return Object.freeze({
    dev: status.dev.toString(),
    ino: status.ino.toString(),
    mode: status.mode.toString(),
    nlink: status.nlink.toString(),
    uid: status.uid.toString(),
    gid: status.gid.toString(),
    rdev: status.rdev.toString(),
    size: status.size.toString(),
    mtimeNs: status.mtimeNs.toString(),
    ctimeNs: status.ctimeNs.toString(),
  });
}

function assertDirectInvocationConsistency() {
  if (directInvocationDescriptor === null || !directInvocationMetadata) {
    throw new Error('Directe quality-gate invocation mist een geopende executablebinding');
  }
  const descriptorStatus = fs.fstatSync(directInvocationDescriptor, { bigint: true });
  let pathStatus;
  try {
    pathStatus = fs.lstatSync(importedGateFile, { bigint: true });
  } catch {
    throw new Error('Directe quality-gate executable verdween tijdens uitvoering');
  }
  if (!descriptorStatus.isFile() || !pathStatus.isFile() || pathStatus.isSymbolicLink()
    || fs.realpathSync.native(importedGateFile) !== importedGateFile
    || !sameValue(stableMetadata(descriptorStatus), stableMetadata(directInvocationMetadata))
    || !sameValue(stableMetadata(pathStatus), stableMetadata(directInvocationMetadata))) {
    throw new Error('Directe quality-gate executable veranderde of kreeg realpath-drift');
  }
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function changedInput() {
  throw new Error('TypeScript-input veranderde tijdens de diagnostiekmeting');
}

function safeInteger(value, minimum, maximum) {
  return Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}

function createMeasurementBudget() {
  const deadline = Date.now() + TYPECHECK_MEASUREMENT_TIMEOUT_MS;
  const reservations = new Map();
  let aggregateBytes = 0;
  return Object.freeze({
    check() {
      if (Date.now() > deadline) {
        throw new Error('TypeScript-meetdeadline overschreden vóór attestatie');
      }
    },
    tree({ count, bytes, depth }) {
      this.check();
      if (!safeInteger(count, 0, MAX_INPUT_FILES)
        || !safeInteger(bytes, 0, MAX_INPUT_BYTES)
        || !safeInteger(depth, 0, MAX_INPUT_DEPTH)) {
        throw new Error('TypeScript-inputboom overschrijdt een harde resourcebovengrens');
      }
    },
    reserve(key, bytes) {
      this.check();
      const previous = reservations.get(key);
      if (previous !== undefined) {
        if (previous !== bytes) changedInput();
        return;
      }
      if (!safeInteger(bytes, 0, MAX_SINGLE_INPUT_BYTES)
        || aggregateBytes > MAX_INPUT_BYTES - bytes) {
        throw new Error('Globale TypeScript-inputbytes overschrijden de harde aggregate resourcebovengrens');
      }
      aggregateBytes += bytes;
      reservations.set(key, bytes);
    },
    remaining(key) {
      this.check();
      return reservations.has(key)
        ? reservations.get(key) : MAX_INPUT_BYTES - aggregateBytes;
    },
  });
}

function fileSizeNumber(status, maximumBytes = MAX_SINGLE_INPUT_BYTES, label = 'TypeScript-inputbestand') {
  if (!safeInteger(maximumBytes, 0, MAX_INPUT_BYTES)
    || status.size > BigInt(maximumBytes)) {
    throw new Error(`${label} overschrijdt de harde bytebovengrens`);
  }
  const value = Number(status.size);
  if (!safeInteger(value, 0, maximumBytes)) {
    throw new Error(`${label} heeft een onveilige grootte`);
  }
  return value;
}

function canonicalInputFile(file, expectedRoot = root) {
  const absolute = path.resolve(file);
  const compilerPrefix = `${TYPESCRIPT_PACKAGE_ROOT}${path.sep}`;
  const repository = path.resolve(expectedRoot);
  const repositoryPrefix = `${repository}${path.sep}`;
  let key;
  if (absolute === TYPESCRIPT_PACKAGE_ROOT || absolute.startsWith(compilerPrefix)) {
    key = `<typescript>/${path.relative(TYPESCRIPT_PACKAGE_ROOT, absolute).split(path.sep).join('/')}`;
  } else if (absolute.startsWith(repositoryPrefix)) {
    key = path.relative(repository, absolute).split(path.sep).join('/');
  } else {
    throw new Error(`TypeScript-input valt buiten de gebonden grenzen: ${file}`);
  }
  return { absolute, key };
}

function decodeTypeScriptBytes(bytes) {
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    const swapped = Buffer.from(bytes.subarray(2));
    swapped.swap16();
    return swapped.toString('utf16le');
  }
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
    return bytes.subarray(2).toString('utf16le');
  }
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return bytes.subarray(3).toString('utf8');
  }
  return bytes.toString('utf8');
}

function positionalBoundedRead(file, maximumBytes, label, reserve = null) {
  if (!Number.isInteger(fs.constants.O_NOFOLLOW)
    || !Number.isInteger(fs.constants.O_NONBLOCK)) {
    throw new Error(`${label} kan niet fail-closed zonder O_NOFOLLOW/O_NONBLOCK worden gelezen`);
  }
  const flags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK
    | (fs.constants.O_CLOEXEC || 0);
  let descriptor;
  try {
    descriptor = fs.openSync(file, flags);
  } catch (error) {
    throw new Error(`${label} kon niet als regulier canoniek bestand worden geopend (${error.code || 'open-fout'})`);
  }
  try {
    const beforeStatus = fs.fstatSync(descriptor, { bigint: true });
    if (!beforeStatus.isFile()) throw new Error(`${label} is geen regulier bestand`);
    const size = fileSizeNumber(beforeStatus, maximumBytes, label);
    reserve?.(size);
    const before = stableMetadata(beforeStatus);
    const bytes = Buffer.allocUnsafe(size);
    let offset = 0;
    while (offset < size) {
      const count = fs.readSync(descriptor, bytes, offset, size - offset, offset);
      if (count === 0) changedInput();
      offset += count;
    }
    const growthProbe = Buffer.allocUnsafe(1);
    if (fs.readSync(descriptor, growthProbe, 0, 1, size) !== 0) changedInput();
    const after = stableMetadata(fs.fstatSync(descriptor, { bigint: true }));
    if (!sameValue(before, after)) changedInput();
    return Object.freeze({ beforeStatus, metadata: before, bytes });
  } finally {
    fs.closeSync(descriptor);
  }
}

function captureFile(file, expectedRoot = root, budget = null,
  maximumBytes = MAX_SINGLE_INPUT_BYTES, label = 'TypeScript-inputbestand', decodeText = true) {
  budget?.check();
  const canonical = canonicalInputFile(file, expectedRoot);
  const remaining = budget?.remaining(canonical.key) ?? maximumBytes;
  const boundedLabel = remaining < maximumBytes
    ? 'Globale TypeScript-inputbytes' : label;
  const bounded = positionalBoundedRead(
    canonical.absolute, Math.min(maximumBytes, remaining), boundedLabel,
    (size) => budget?.reserve(canonical.key, size),
  );
  budget?.check();
  const afterStatus = fs.lstatSync(canonical.absolute, { bigint: true });
  const pathAfter = stableMetadata(afterStatus);
  if (!afterStatus.isFile() || afterStatus.isSymbolicLink()
    || fs.realpathSync.native(canonical.absolute) !== canonical.absolute
    || !sameValue(pathAfter, bounded.metadata)) changedInput();
  return Object.freeze({
    absolute: canonical.absolute,
    key: canonical.key,
    metadata: bounded.metadata,
    bytes: bounded.bytes.length,
    sha256: sha256(bounded.bytes),
    text: decodeText ? decodeTypeScriptBytes(bounded.bytes) : undefined,
  });
}

function captureExternalFile(file, maximumBytes, label, key, decodeText = false) {
  const absolute = path.resolve(file);
  const bounded = positionalBoundedRead(absolute, maximumBytes, label);
  const afterStatus = fs.lstatSync(absolute, { bigint: true });
  if (!afterStatus.isFile() || afterStatus.isSymbolicLink()
    || fs.realpathSync.native(absolute) !== absolute
    || !sameValue(bounded.metadata, stableMetadata(afterStatus))) changedInput();
  return Object.freeze({
    absolute,
    key,
    metadata: bounded.metadata,
    bytes: bounded.bytes.length,
    sha256: sha256(bounded.bytes),
    text: decodeText ? decodeTypeScriptBytes(bounded.bytes) : undefined,
  });
}

function verifyFile(snapshot, expectedRoot = root, budget = null,
  maximumBytes = MAX_SINGLE_INPUT_BYTES, label = 'TypeScript-inputbestand') {
  let current;
  try {
    current = captureFile(
      snapshot.absolute, expectedRoot, budget, maximumBytes, label,
      snapshot.text !== undefined,
    );
  } catch {
    changedInput();
  }
  if (current.key !== snapshot.key || current.bytes !== snapshot.bytes
    || current.sha256 !== snapshot.sha256
    || !sameValue(current.metadata, snapshot.metadata)) changedInput();
}

function addSnapshot(collection, snapshot) {
  const previous = collection.get(snapshot.key);
  if (previous && (previous.sha256 !== snapshot.sha256
      || !sameValue(previous.metadata, snapshot.metadata))) changedInput();
  if (!previous) {
    collection.set(snapshot.key, snapshot);
    const tracked = snapshotByteTotals.get(collection);
    if (tracked !== undefined) snapshotByteTotals.set(collection, tracked + snapshot.bytes);
  }
  return snapshot;
}

const snapshotByteTotals = new WeakMap();

function snapshotBytes(collection) {
  const tracked = snapshotByteTotals.get(collection);
  if (tracked !== undefined) return tracked;
  let bytes = 0;
  for (const snapshot of collection.values()) {
    if (bytes > MAX_INPUT_BYTES - snapshot.bytes) {
      throw new Error('TypeScript-inputbytes overschrijden de harde resourcebovengrens');
    }
    bytes += snapshot.bytes;
  }
  snapshotByteTotals.set(collection, bytes);
  return bytes;
}

function semanticTextFile(file) {
  const name = path.basename(file).toLowerCase();
  return name === 'package.json' || name.startsWith('tsconfig')
    || /\.(?:[cm]?js|jsx|[cm]?ts|tsx|json)$/u.test(name);
}

function captureBoundedInto(collection, file, expectedRoot = root, budget = null,
  decodeText = semanticTextFile(file)) {
  const remaining = MAX_INPUT_BYTES - snapshotBytes(collection);
  if (remaining < 0) {
    throw new Error('TypeScript-inputbytes overschrijden de harde resourcebovengrens');
  }
  const snapshot = captureFile(
    file, expectedRoot, budget, Math.min(MAX_SINGLE_INPUT_BYTES, remaining),
    'TypeScript-inputbestand', decodeText,
  );
  return addSnapshot(collection, snapshot);
}

function contentEntries(snapshots) {
  return [...snapshots.values()]
    .map(({ key, sha256: contentSha256 }) => Object.freeze({ key, sha256: contentSha256 }))
    .sort((left, right) => left.key.localeCompare(right.key));
}

function contentInventory(snapshots) {
  const entries = contentEntries(snapshots);
  const bytes = snapshotBytes(snapshots);
  if (!safeInteger(bytes, 0, MAX_INPUT_BYTES)) {
    throw new Error('TypeScript-inputbytes overschrijden de harde resourcebovengrens');
  }
  return Object.freeze({
    count: entries.length,
    bytes,
    sha256: sha256(JSON.stringify(entries)),
  });
}

function verifySnapshots(snapshots, expectedRoot = root, budget = null) {
  for (const snapshot of snapshots.values()) verifyFile(snapshot, expectedRoot, budget);
}

function* walkRegularFiles(directory, predicate = () => true, rejectSymlinks = false,
  budget = null) {
  budget?.check();
  if (!fs.existsSync(directory)) return;
  const rootStatus = fs.lstatSync(directory, { bigint: true });
  if (!rootStatus.isDirectory() || rootStatus.isSymbolicLink()
    || fs.realpathSync.native(directory) !== path.resolve(directory)) {
    throw new Error(`TypeScript-inputboom is niet canoniek: ${directory}`);
  }
  let count = 0;
  function* visit(current, depth) {
    budget?.tree({ count, bytes: 0, depth });
    const directoryHandle = fs.opendirSync(current);
    try {
      let entry;
      while ((entry = directoryHandle.readSync()) !== null) {
        const candidate = path.join(current, entry.name);
        if (entry.isSymbolicLink()) {
          if (rejectSymlinks) {
            throw new Error(`TypeScript-inputboom bevat een symlink: ${candidate}`);
          }
        } else if (entry.isDirectory()) {
          yield* visit(candidate, depth + 1);
        } else if (entry.isFile() && predicate(candidate)) {
          count += 1;
          budget?.tree({ count, bytes: 0, depth });
          yield candidate;
        }
        budget?.tree({ count, bytes: 0, depth });
      }
    } finally {
      directoryHandle.closeSync();
    }
  };
  yield* visit(path.resolve(directory), 0);
}

function captureTree(directory, expectedRoot, predicate = () => true, rejectSymlinks = false,
  budget = null) {
  const snapshots = new Map();
  for (const file of walkRegularFiles(directory, predicate, rejectSymlinks, budget)) {
    captureBoundedInto(snapshots, file, expectedRoot, budget);
  }
  budget?.tree({ count: snapshots.size, bytes: snapshotBytes(snapshots), depth: 0 });
  return snapshots;
}

function verifyTree(directory, snapshots, expectedRoot, predicate = () => true,
  rejectSymlinks = false, budget = null) {
  let current;
  try {
    current = captureTree(directory, expectedRoot, predicate, rejectSymlinks, budget);
  } catch {
    changedInput();
  }
  if (!sameValue(contentInventory(current), contentInventory(snapshots))) changedInput();
  verifySnapshots(snapshots, expectedRoot, budget);
}

function canonicalValue(value, expectedRoot = root) {
  if (value === undefined) return Object.freeze({ $undefined: true });
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('TypeScript-config bevat een onbindbaar getal');
    return value;
  }
  if (typeof value === 'string') {
    const repository = path.resolve(expectedRoot);
    if (value === TYPESCRIPT_PACKAGE_ROOT) return '<typescript>';
    if (value.startsWith(`${TYPESCRIPT_PACKAGE_ROOT}${path.sep}`)) {
      return `<typescript>/${path.relative(TYPESCRIPT_PACKAGE_ROOT, value).split(path.sep).join('/')}`;
    }
    if (value === repository) return '<repository>';
    if (value.startsWith(`${repository}${path.sep}`)) {
      return `<repository>/${path.relative(repository, value).split(path.sep).join('/')}`;
    }
    return value;
  }
  if (Array.isArray(value)) return value.map((entry) => canonicalValue(entry, expectedRoot));
  if (typeof value !== 'object') throw new Error('TypeScript-config bevat een onbindbare waarde');
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new Error('TypeScript-config bevat een niet-canoniek object');
  }
  const result = {};
  for (const key of Object.keys(value).sort()) result[key] = canonicalValue(value[key], expectedRoot);
  return result;
}

function valueSha256(value, expectedRoot = root) {
  return sha256(JSON.stringify(canonicalValue(value, expectedRoot)));
}

function inputReceiptSha256(value) {
  return sha256(JSON.stringify(canonicalValue({
    contract: TYPECHECK_INPUT_RECEIPT_CONTRACT,
    compiler: value.compiler,
    config: value.config,
    sourceInventory: value.sourceInventory,
    programInputs: value.programInputs,
    dependencies: value.dependencies,
  })));
}

function sourceInventory(files, expectedRoot = root) {
  if (!Array.isArray(files) || files.length > MAX_INPUT_FILES) {
    throw new Error('TypeScript-broninventaris overschrijdt de harde bestandsbovengrens');
  }
  const normalized = [...new Set(files.map((file) => regularCanonicalFile(file, expectedRoot).relative))]
    .sort();
  if (normalized.length !== files.length) throw new Error('TypeScript-broninventaris bevat duplicaten');
  return Object.freeze({
    rootNames: Object.freeze(normalized),
    count: normalized.length,
    sha256: sha256(normalized.length ? `${normalized.join('\0')}\0` : ''),
  });
}

function captureCompilerTreeBinding(expectedRoot = root, budget = null) {
  const tree = captureTree(
    TYPESCRIPT_PACKAGE_ROOT, expectedRoot, () => true, true, budget,
  );
  const packageJson = tree.get('<typescript>/package.json');
  const entrypoint = tree.get(`<typescript>/${path.relative(
    TYPESCRIPT_PACKAGE_ROOT, typescriptEntrypoint,
  ).split(path.sep).join('/')}`);
  if (!packageJson || !entrypoint) throw new Error('TypeScript-compilerboom is onvolledig');
  const inventory = contentInventory(tree);
  let packageMetadata;
  try {
    packageMetadata = JSON.parse(packageJson.text);
  } catch {
    throw new Error('TypeScript-package.json is geen geldige gepinde JSON');
  }
  const binding = Object.freeze({
    name: 'typescript',
    version: packageMetadata?.version,
    packageJsonSha256: packageJson.sha256,
    compilerSha256: entrypoint.sha256,
    treeCount: inventory.count,
    treeBytes: inventory.bytes,
    treeSha256: inventory.sha256,
  });
  if (binding.version !== EXPECTED_TYPESCRIPT_VERSION) {
    throw new Error(`TypeScript ${EXPECTED_TYPESCRIPT_VERSION} is vereist; gevonden ${binding.version}`);
  }
  return Object.freeze({ tree, binding });
}

function captureCompilerBinding(expectedRoot = root, budget = null, expectedBinding = null) {
  if (!ts && (compilerEntrypointPreloaded || compilerEntrypointIsCached())) {
    throw new Error('TypeScript-compiler was al vóór de compilerbinding geladen');
  }
  const captured = captureCompilerTreeBinding(expectedRoot, budget);
  const { tree, binding: treeBinding } = captured;
  const entrypoint = tree.get(`<typescript>/${path.relative(
    TYPESCRIPT_PACKAGE_ROOT, typescriptEntrypoint,
  ).split(path.sep).join('/')}`);
  if (!entrypoint) throw new Error('TypeScript-compilerentrypoint ontbreekt in de gepinde boom');
  if (expectedBinding && canonicalJson(treeBinding) !== canonicalJson(expectedBinding)) {
    throw new Error('TypeScript-compilerboom wijkt vóór uitvoering af van de parentreceipt');
  }
  if (!ts) {
    if (compilerEntrypointPreloaded || compilerEntrypointIsCached()) {
      throw new Error('TypeScript-compiler was al vóór de compilerbinding geladen');
    }
    budget?.check();
    const sealedCompilerModule = new Module(typescriptEntrypoint);
    sealedCompilerModule.filename = typescriptEntrypoint;
    sealedCompilerModule.paths = [];
    const builtins = new Set([
      ...Module.builtinModules,
      ...Module.builtinModules.map((name) => `node:${name}`),
    ]);
    sealedCompilerModule.require = (specifier) => {
      if (typeof specifier !== 'string' || !builtins.has(specifier)) {
        throw new Error(`TypeScript-compiler vroeg een ongebonden runtimedependency: ${specifier}`);
      }
      return require(specifier);
    };
    sealedCompilerModule._compile(entrypoint.text, typescriptEntrypoint);
    ts = sealedCompilerModule.exports;
    compilerEntrypointPreloaded = false;
    budget?.check();
    verifyTree(
      TYPESCRIPT_PACKAGE_ROOT, tree, expectedRoot, () => true, true, budget,
    );
    if (ts.version !== treeBinding.version) {
      throw new Error('Uitgevoerde TypeScript-versie wijkt af van de gepinde packagebinding');
    }
    loadedCompilerBinding = treeBinding;
  } else if (!sameValue(loadedCompilerBinding, treeBinding) || ts.version !== treeBinding.version) {
    throw new Error('Uitgevoerde TypeScript-compiler wijkt af van de gebonden compilerboom');
  }
  return Object.freeze({
    tree,
    binding: loadedCompilerBinding,
  });
}

function normalizedMessage(diagnostic, expectedRoot = root) {
  return ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')
    .replaceAll(path.resolve(expectedRoot), '<repository>')
    .replaceAll('\r\n', '\n');
}

function declarationDescriptor(node, sourceFile) {
  if (ts.isConstructorDeclaration(node)) return 'Constructor:#constructor';
  if (!ts.isNamedDeclaration(node) || !node.name) return null;
  const name = node.name.getText(sourceFile).replace(/\s+/gu, ' ').trim();
  return `${ts.SyntaxKind[node.kind]}:${name}`;
}

function childIndexPath(token, boundary, sourceFile) {
  const result = [];
  for (let child = token; child && child !== boundary;) {
    const parent = child.parent;
    if (!parent) throw new Error('TypeScript-diagnostieksite mist een AST-parent');
    const semanticChildren = [];
    ts.forEachChild(parent, (candidate) => { semanticChildren.push(candidate); });
    const syntaxChildren = parent.getChildren(sourceFile);
    const locate = (children) => {
      const identityIndex = children.indexOf(child);
      if (identityIndex >= 0) return identityIndex;
      const matches = children.map((candidate, candidateIndex) => ({ candidate, candidateIndex }))
        .filter(({ candidate }) => candidate.kind === child.kind
          && candidate.pos === child.pos && candidate.end === child.end);
      return matches.length === 1 ? matches[0].candidateIndex : -1;
    };
    let children = semanticChildren;
    let index = locate(children);
    let domain = 'semantic';
    if (index < 0) {
      children = syntaxChildren;
      index = locate(children);
      domain = 'syntax';
    }
    if (index < 0) throw new Error('TypeScript-diagnostieksite mist een exact AST-kindpad');
    result.unshift(Object.freeze({
      parentKind: ts.SyntaxKind[parent.kind],
      childKind: ts.SyntaxKind[child.kind],
      domain,
      childIndex: String(index).padStart(8, '0'),
      childCount: String(children.length).padStart(8, '0'),
    }));
    child = parent;
  }
  return result;
}

function diagnosticSite(diagnostic) {
  if (!diagnostic.file || !Number.isInteger(diagnostic.start)) {
    return { scope: 'global' };
  }
  const sourceFile = diagnostic.file;
  const start = diagnostic.start;
  const token = ts.getTokenAtPosition(sourceFile, start);
  const declarations = [];
  let nearestDeclaration = null;
  for (let node = token; node && node !== sourceFile; node = node.parent) {
    const descriptor = declarationDescriptor(node, sourceFile);
    if (descriptor) {
      declarations.unshift(descriptor);
      nearestDeclaration ||= node;
    }
  }
  const structuralKinds = [];
  for (let node = token; node && node !== sourceFile; node = node.parent) {
    structuralKinds.unshift(ts.SyntaxKind[node.kind]);
    if (node === nearestDeclaration) break;
  }
  const tokenStart = token.getStart(sourceFile);
  return {
    declarations,
    structuralKinds,
    childIndexPath: childIndexPath(token, sourceFile, sourceFile),
    token: token.getText(sourceFile),
    spanOffset: start - tokenStart,
    spanLength: diagnostic.length ?? 0,
    spanText: sourceFile.text.slice(start, start + (diagnostic.length ?? 0)),
  };
}

function diagnosticIdentity(diagnostic, expectedRoot = root) {
  const file = diagnostic.file
    ? regularCanonicalFile(diagnostic.file.fileName, expectedRoot).relative : '<global>';
  const message = normalizedMessage(diagnostic, expectedRoot);
  const identity = {
    contract: TYPECHECK_DIAGNOSTIC_CONTRACT,
    category: 'error',
    code: diagnostic.code,
    file,
    messageSha256: sha256(message),
    siteSha256: sha256(JSON.stringify(diagnosticSite(diagnostic))),
  };
  return Object.freeze({
    ...identity,
    fingerprint: sha256(JSON.stringify(identity)),
    line: diagnostic.file && Number.isInteger(diagnostic.start)
      ? diagnostic.file.getLineAndCharacterOfPosition(diagnostic.start).line + 1 : null,
    message,
  });
}

function loadConfiguration(configFile, snapshotFileSystem, expectedRoot = root, budget = null) {
  const snapshots = new Map();
  const readConfig = (fileName) => {
    budget?.check();
    const snapshot = snapshotFileSystem.snapshot(fileName, 'TypeScript-configread');
    if (!snapshot) return undefined;
    addSnapshot(snapshots, snapshot);
    return snapshot.text;
  };
  const host = {
    useCaseSensitiveFileNames: true,
    readDirectory: snapshotFileSystem.readDirectory,
    fileExists: snapshotFileSystem.fileExists,
    readFile: readConfig,
    trace() {},
  };
  const loaded = ts.readConfigFile(configFile.absolute, readConfig);
  if (loaded.error) throw new Error(normalizedMessage(loaded.error, expectedRoot));
  const parsed = ts.parseJsonConfigFileContent(
    loaded.config, host, expectedRoot, undefined, configFile.absolute,
  );
  const rootSnapshot = snapshots.get(configFile.relative);
  if (!rootSnapshot) throw new Error('Hoofd-tsconfig ontbreekt in de configuratiebinding');
  const files = contentInventory(snapshots);
  budget?.tree({ count: files.count, bytes: files.bytes, depth: 0 });
  return Object.freeze({
    raw: loaded.config,
    parsed,
    snapshots,
    binding: Object.freeze({
      path: configFile.relative,
      sha256: rootSnapshot.sha256,
      filesCount: files.count,
      filesBytes: files.bytes,
      filesSha256: files.sha256,
      optionsSha256: valueSha256(parsed.options, expectedRoot),
      projectReferencesSha256: valueSha256(parsed.projectReferences, expectedRoot),
    }),
  });
}

const REPOSITORY_TOPOLOGY_EXCLUDES = new Set(['.git', 'coverage', 'data', 'dist']);

function assertNoExcludedSemanticPath(file, expectedRoot, label) {
  const absolute = path.resolve(file);
  if (absolute === TYPESCRIPT_PACKAGE_ROOT
    || absolute.startsWith(`${TYPESCRIPT_PACKAGE_ROOT}${path.sep}`)) return;
  const repository = path.resolve(expectedRoot);
  const relative = path.relative(repository, absolute);
  if (!relative || (!relative.startsWith(`..${path.sep}`) && relative !== '..'
      && !path.isAbsolute(relative))) {
    const [first] = relative.split(path.sep);
    if (REPOSITORY_TOPOLOGY_EXCLUDES.has(first)) {
      throw new Error(`${label} gebruikt uitgesloten root ${first} zonder inhoudsreceipt`);
    }
    return;
  }
  throw new Error(`${label} valt buiten de gebonden repositorygrens`);
}

function assertNoExcludedConfiguration(raw, configFile, expectedRoot) {
  const inherited = raw?.extends === undefined
    ? [] : (Array.isArray(raw.extends) ? raw.extends : [raw.extends]);
  for (const specifier of inherited) {
    if (typeof specifier !== 'string' || !specifier) {
      throw new Error('TypeScript-extends bevat een ongeldige configbron');
    }
    if (specifier.startsWith('.') || path.isAbsolute(specifier)) {
      assertNoExcludedSemanticPath(
        path.resolve(dirname(configFile.absolute), specifier), expectedRoot,
        'TypeScript-extends',
      );
    } else if (REPOSITORY_TOPOLOGY_EXCLUDES.has(specifier.split('/')[0])) {
      throw new Error(`TypeScript-extends gebruikt uitgesloten root ${specifier}`);
    }
  }
  for (const field of ['files', 'include']) {
    for (const candidate of raw?.[field] || []) {
      if (typeof candidate !== 'string' || !candidate) {
        throw new Error(`TypeScript-${field} bevat een ongeldige configbron`);
      }
      assertNoExcludedSemanticPath(
        path.resolve(dirname(configFile.absolute), candidate), expectedRoot,
        `TypeScript-${field}`,
      );
    }
  }
}

function assertNoExcludedParsedInputs(configuration, expectedRoot) {
  assertNoExcludedConfiguration(configuration.raw, {
    absolute: resolve(expectedRoot, configuration.binding.path),
  }, expectedRoot);
  if (configuration.parsed.options.baseUrl || configuration.parsed.options.paths) {
    throw new Error('Standalone debtmeting weigert baseUrl/paths zonder volledige lookupreceipt');
  }
  for (const option of ['rootDirs', 'typeRoots']) {
    for (const candidate of configuration.parsed.options[option] || []) {
      assertNoExcludedSemanticPath(candidate, expectedRoot, `TypeScript-${option}`);
    }
  }
  for (const fileName of configuration.parsed.fileNames) {
    if (path.resolve(fileName) === resolve(expectedRoot, TYPECHECK_DEBT_MANIFEST)) {
      throw new Error('TypeScript-manifest mag nooit een semantische rootName zijn');
    }
    assertNoExcludedSemanticPath(fileName, expectedRoot, 'TypeScript-rootName');
  }
  for (const snapshot of configuration.snapshots.values()) {
    assertNoExcludedSemanticPath(snapshot.absolute, expectedRoot, 'TypeScript-configbron');
  }
}

function assertNoExcludedProgramSources(program, expectedRoot) {
  for (const sourceFile of program.getSourceFiles()) {
    if (path.resolve(sourceFile.fileName) === resolve(expectedRoot, TYPECHECK_DEBT_MANIFEST)) {
      throw new Error('TypeScript-manifest mag nooit een semantische programmabron zijn');
    }
    assertNoExcludedSemanticPath(sourceFile.fileName, expectedRoot, 'TypeScript-programmabron');
  }
}

function canonicalTopologyTarget(target, expectedRoot) {
  const absolute = fs.realpathSync.native(target);
  const repository = path.resolve(expectedRoot);
  if (absolute === repository) return '<repository>';
  if (absolute.startsWith(`${repository}${path.sep}`)) {
    const relative = path.relative(repository, absolute).split(path.sep).join('/');
    if (REPOSITORY_TOPOLOGY_EXCLUDES.has(relative.split('/')[0])) {
      throw new Error(`Dependency-symlinkdoel gebruikt uitgesloten root: ${target}`);
    }
    return `<repository>/${relative}`;
  }
  if (absolute === TYPESCRIPT_PACKAGE_ROOT) return '<typescript>';
  if (absolute.startsWith(`${TYPESCRIPT_PACKAGE_ROOT}${path.sep}`)) {
    return `<typescript>/${path.relative(TYPESCRIPT_PACKAGE_ROOT, absolute).split(path.sep).join('/')}`;
  }
  throw new Error(`Dependency-symlinkdoel valt buiten de gebonden grenzen: ${target}`);
}

function topologyBinding(nodes, snapshots) {
  const topology = [...nodes.values()]
    .map(({ semantic }) => semantic)
    .sort((left, right) => left.path.localeCompare(right.path));
  const files = contentEntries(snapshots);
  const inventory = contentInventory(snapshots);
  if (!safeInteger(topology.length, 1, MAX_INPUT_FILES)) {
    throw new Error('Dependencytopologie overschrijdt de harde bestandsbovengrens');
  }
  return Object.freeze({
    count: topology.length,
    bytes: inventory.bytes,
    sha256: sha256(JSON.stringify({ topology, files })),
  });
}

function captureDependencies(expectedRoot = root, budget = null) {
  const repository = path.resolve(expectedRoot);
  for (let ancestor = dirname(repository);;) {
    const candidate = path.join(ancestor, 'node_modules');
    try {
      fs.lstatSync(candidate);
      throw new Error(`Resolvergrens weigert ancestor node_modules buiten de gebonden repository: ${candidate}`);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
    const parent = dirname(ancestor);
    if (ancestor === parent) break;
    ancestor = parent;
  }
  const snapshots = new Map();
  const nodes = new Map();
  let count = 0;
  const visit = (absolute, relative, depth) => {
    budget?.check();
    const status = fs.lstatSync(absolute, { bigint: true });
    count += 1;
    budget?.tree({ count, bytes: 0, depth });
    const normalized = relative.split(path.sep).join('/') || '.';
    let type;
    let link = null;
    let target = null;
    if (status.isSymbolicLink()) {
      type = 'symlink';
      link = fs.readlinkSync(absolute);
      target = canonicalTopologyTarget(absolute, expectedRoot);
    } else if (status.isDirectory()) type = 'directory';
    else if (status.isFile()) type = 'file';
    else throw new Error(`Dependencytopologie bevat een niet-regulier object: ${normalized}`);
    nodes.set(normalized, Object.freeze({
      metadata: stableMetadata(status),
      semantic: Object.freeze({ path: normalized, type, link, target }),
    }));
    if (type === 'file' && normalized !== TYPECHECK_DEBT_MANIFEST) {
      captureBoundedInto(snapshots, absolute, expectedRoot, budget);
      budget?.tree({ count, bytes: snapshotBytes(snapshots), depth });
    }
    if (type !== 'directory') return;
    const directoryHandle = fs.opendirSync(absolute);
    try {
      let entry;
      while ((entry = directoryHandle.readSync()) !== null) {
        if (!relative && REPOSITORY_TOPOLOGY_EXCLUDES.has(entry.name)) continue;
        const childRelative = relative ? path.join(relative, entry.name) : entry.name;
        visit(path.join(absolute, entry.name), childRelative, depth + 1);
      }
    } finally {
      directoryHandle.closeSync();
    }
  };
  visit(repository, '', 0);
  const state = Object.freeze({ nodes, snapshots });
  return Object.freeze({ state, binding: topologyBinding(nodes, snapshots) });
}

function verifyDependencies(state, expectedRoot = root, budget = null) {
  let current;
  try {
    current = captureDependencies(expectedRoot, budget);
  } catch {
    changedInput();
  }
  if (!sameValue(current.binding, topologyBinding(state.nodes, state.snapshots))) changedInput();
  for (const [key, node] of state.nodes) {
    const candidate = current.state.nodes.get(key);
    if (!candidate || !sameValue(candidate.metadata, node.metadata)
      || !sameValue(candidate.semantic, node.semantic)) changedInput();
  }
  verifySnapshots(state.snapshots, expectedRoot, budget);
}

function createSnapshotFileSystem(dependencyState, compilerTree, expectedRoot = root) {
  const repository = path.resolve(expectedRoot);
  const files = new Map();
  const directories = new Set([repository, TYPESCRIPT_PACKAGE_ROOT]);
  const symlinks = [];
  const addFile = (snapshot) => {
    const absolute = path.resolve(snapshot.absolute);
    const previous = files.get(absolute);
    if (previous && previous.sha256 !== snapshot.sha256) changedInput();
    files.set(absolute, snapshot);
  };
  for (const snapshot of dependencyState.snapshots.values()) addFile(snapshot);
  for (const snapshot of compilerTree.values()) addFile(snapshot);
  for (const node of dependencyState.nodes.values()) {
    const relative = node.semantic.path;
    const absolute = relative === '.' ? repository : path.join(repository, relative);
    if (node.semantic.type === 'directory') directories.add(path.resolve(absolute));
    else if (node.semantic.type === 'symlink') symlinks.push(path.resolve(absolute));
  }
  for (const absolute of files.keys()) {
    const boundary = absolute === TYPESCRIPT_PACKAGE_ROOT
      || absolute.startsWith(`${TYPESCRIPT_PACKAGE_ROOT}${path.sep}`)
      ? TYPESCRIPT_PACKAGE_ROOT : repository;
    for (let current = dirname(absolute); current.startsWith(boundary); current = dirname(current)) {
      directories.add(current);
      if (current === boundary) break;
    }
  }
  symlinks.sort((left, right) => right.length - left.length);
  const directoryEntries = new Map();
  const addDirectoryEntry = (parent, name, type) => {
    let entry = directoryEntries.get(parent);
    if (!entry) {
      entry = { files: new Set(), directories: new Set() };
      directoryEntries.set(parent, entry);
    }
    entry[type].add(name);
  };
  for (const absolute of files.keys()) {
    addDirectoryEntry(dirname(absolute), path.basename(absolute), 'files');
  }
  for (const absolute of directories) {
    if (absolute === repository || absolute === TYPESCRIPT_PACKAGE_ROOT) continue;
    addDirectoryEntry(dirname(absolute), path.basename(absolute), 'directories');
  }
  const sealedDirectoryEntries = new Map([...directoryEntries].map(([directory, entry]) => [
    directory,
    Object.freeze({
      files: Object.freeze([...entry.files].sort()),
      directories: Object.freeze([...entry.directories].sort()),
    }),
  ]));

  const normalizeLookup = (candidate, label) => {
    const absolute = path.resolve(candidate);
    const compiler = absolute === TYPESCRIPT_PACKAGE_ROOT
      || absolute.startsWith(`${TYPESCRIPT_PACKAGE_ROOT}${path.sep}`);
    const inRepository = absolute === repository
      || absolute.startsWith(`${repository}${path.sep}`);
    if (!compiler && !inRepository) return null;
    if (inRepository && absolute !== repository) {
      if (absolute === resolve(repository, TYPECHECK_DEBT_MANIFEST)) {
        throw new Error(`${label} probeert het TypeScript-manifest als semantische input te lezen`);
      }
      const [first] = path.relative(repository, absolute).split(path.sep);
      if (REPOSITORY_TOPOLOGY_EXCLUDES.has(first)) {
        throw new Error(`${label} probeert een resolverlookup in uitgesloten root ${first}`);
      }
    }
    const throughSymlink = symlinks.find((link) => absolute === link
      || absolute.startsWith(`${link}${path.sep}`));
    if (throughSymlink) {
      throw new Error(`${label} probeert een ongebonden symlinkroute: ${throughSymlink}`);
    }
    return absolute;
  };
  const entries = (directory) => {
    const absolute = normalizeLookup(directory, 'TypeScript-readDirectory');
    if (!absolute) return { files: [], directories: [] };
    if (!directories.has(absolute)) return { files: [], directories: [] };
    return sealedDirectoryEntries.get(absolute) || { files: [], directories: [] };
  };
  const snapshot = (candidate, label = 'TypeScript-readFile') => {
    const absolute = normalizeLookup(candidate, label);
    if (!absolute) return undefined;
    return files.get(absolute);
  };
  return Object.freeze({
    snapshot,
    fileExists(candidate) {
      return Boolean(snapshot(candidate, 'TypeScript-fileExists'));
    },
    readFile(candidate) {
      return snapshot(candidate)?.text;
    },
    directoryExists(candidate) {
      const absolute = normalizeLookup(candidate, 'TypeScript-directoryExists');
      return absolute ? directories.has(absolute) : false;
    },
    getDirectories(candidate) {
      const absolute = normalizeLookup(candidate, 'TypeScript-getDirectories');
      if (!absolute) return [];
      return entries(absolute).directories.map((name) => path.join(absolute, name));
    },
    realpath(candidate) {
      const absolute = normalizeLookup(candidate, 'TypeScript-realpath');
      if (!absolute) return path.resolve(candidate);
      if (!files.has(absolute) && !directories.has(absolute)) {
        throw new Error(`TypeScript-realpath mist in de gepinde snapshot: ${candidate}`);
      }
      return absolute;
    },
    readDirectory(candidate, extensions, excludes, includes, depth) {
      const absolute = normalizeLookup(candidate, 'TypeScript-readDirectory');
      if (!absolute) return [];
      return ts.matchFiles(
        absolute, extensions, excludes, includes, true, repository, depth,
        (directory) => entries(directory), (entry) => normalizeLookup(entry, 'TypeScript-realpath'),
      );
    },
  });
}

function createSnapshotCompilerHost(options, snapshotFileSystem, expectedRoot = root) {
  const host = ts.createCompilerHost(options, true);
  return {
    ...host,
    fileExists: snapshotFileSystem.fileExists,
    readFile: snapshotFileSystem.readFile,
    directoryExists: snapshotFileSystem.directoryExists,
    getDirectories: snapshotFileSystem.getDirectories,
    realpath: snapshotFileSystem.realpath,
    readDirectory: snapshotFileSystem.readDirectory,
    getCurrentDirectory: () => path.resolve(expectedRoot),
    getSourceFile(fileName, languageVersionOrOptions, onError) {
      let text;
      try {
        text = snapshotFileSystem.readFile(fileName);
      } catch (error) {
        onError?.(error.message);
        throw error;
      }
      if (text === undefined) return undefined;
      return ts.createSourceFile(fileName, text, languageVersionOrOptions, true);
    },
  };
}

function snapshotRootSources(fileNames, snapshotFileSystem, budget = null) {
  const snapshots = new Map();
  for (const fileName of fileNames) {
    budget?.check();
    const snapshot = snapshotFileSystem.snapshot(fileName, 'TypeScript-rootName');
    if (!snapshot) throw new Error(`TypeScript-rootName ontbreekt in de gepinde snapshot: ${fileName}`);
    addSnapshot(snapshots, snapshot);
    budget?.tree({ count: snapshots.size, bytes: snapshotBytes(snapshots), depth: 0 });
  }
  return snapshots;
}

function snapshotProgramSources(program, snapshotFileSystem, budget = null) {
  const snapshots = new Map();
  for (const sourceFile of program.getSourceFiles()) {
    budget?.check();
    const snapshot = snapshotFileSystem.snapshot(sourceFile.fileName, 'TypeScript-programmabron');
    if (!snapshot || sourceFile.text !== snapshot.text) changedInput();
    addSnapshot(snapshots, snapshot);
    budget?.tree({ count: snapshots.size, bytes: snapshotBytes(snapshots), depth: 0 });
  }
  return snapshots;
}

function verifyParsedProgramConfiguration(parsed, program, binding, expectedRoot = root) {
  if (valueSha256(parsed.options, expectedRoot) !== binding.optionsSha256
    || valueSha256(parsed.projectReferences, expectedRoot) !== binding.projectReferencesSha256
    || valueSha256(program.getCompilerOptions(), expectedRoot) !== binding.optionsSha256
    || valueSha256(program.getProjectReferences(), expectedRoot)
      !== binding.projectReferencesSha256) changedInput();
}

export function collectTypeScriptDebtEvidence(expectedRoot = root, configName = 'tsconfig.json') {
  const expected = arguments[2] ?? {};
  const budget = createMeasurementBudget();
  budget.check();
  const configFile = regularCanonicalFile(configName, expectedRoot);
  const compiler = captureCompilerBinding(expectedRoot, budget, expected.compiler);
  const dependencies = captureDependencies(expectedRoot, budget);
  if (expected.dependencies
    && canonicalJson(dependencies.binding) !== canonicalJson(expected.dependencies)) {
    throw new Error('TypeScript-candidatesnapshot wijkt af van de parentreceipt');
  }
  const snapshotFileSystem = createSnapshotFileSystem(
    dependencies.state, compiler.tree, expectedRoot,
  );
  const configuration = loadConfiguration(configFile, snapshotFileSystem, expectedRoot, budget);
  assertNoExcludedParsedInputs(configuration, expectedRoot);
  if (configuration.parsed.projectReferences?.length) {
    throw new Error('Standalone debtmeting weigert projectReferences zonder volledige recursieve projectbinding');
  }
  const rootNamesBefore = sourceInventory(configuration.parsed.fileNames, expectedRoot);
  budget.tree({ count: rootNamesBefore.count, bytes: 0, depth: 0 });
  const rootSources = snapshotRootSources(
    configuration.parsed.fileNames, snapshotFileSystem, budget,
  );
  const parsed = configuration.parsed;
  const compilerHost = createSnapshotCompilerHost(
    parsed.options, snapshotFileSystem, expectedRoot,
  );
  const program = ts.createProgram({
    rootNames: parsed.fileNames,
    options: parsed.options,
    projectReferences: parsed.projectReferences,
    host: compilerHost,
  });
  budget.check();
  assertNoExcludedProgramSources(program, expectedRoot);
  verifyParsedProgramConfiguration(parsed, program, configuration.binding, expectedRoot);
  const programSources = snapshotProgramSources(program, snapshotFileSystem, budget);
  const programInputs = contentInventory(programSources);
  budget.check();
  const raw = ts.sortAndDeduplicateDiagnostics([
    ...parsed.errors, ...ts.getPreEmitDiagnostics(program),
  ]).filter((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error);
  budget.check();
  if (raw.length > MAX_DIAGNOSTIC_COUNT) {
    throw new Error('TypeScript-diagnostiektelling overschrijdt de harde bovengrens');
  }
  const diagnostics = raw.map((diagnostic) => diagnosticIdentity(diagnostic, expectedRoot));
  verifyParsedProgramConfiguration(parsed, program, configuration.binding, expectedRoot);

  const configurationAfter = loadConfiguration(
    configFile, snapshotFileSystem, expectedRoot, budget,
  );
  const rootNamesAfter = sourceInventory(configurationAfter.parsed.fileNames, expectedRoot);
  if (!sameValue(configuration.binding, configurationAfter.binding)
    || !sameValue(rootNamesBefore, rootNamesAfter)) changedInput();
  verifySnapshots(configuration.snapshots, expectedRoot, budget);
  verifySnapshots(configurationAfter.snapshots, expectedRoot, budget);
  verifySnapshots(rootSources, expectedRoot, budget);
  verifySnapshots(programSources, expectedRoot, budget);
  verifyDependencies(dependencies.state, expectedRoot, budget);
  verifyTree(
    TYPESCRIPT_PACKAGE_ROOT, compiler.tree, expectedRoot, () => true, true, budget,
  );
  budget.check();

  const evidence = {
    compiler: compiler.binding,
    config: configuration.binding,
    sourceInventory: rootNamesBefore,
    programInputs,
    dependencies: dependencies.binding,
    diagnostics: Object.freeze(diagnostics),
  };
  return Object.freeze({
    ...evidence,
    inputReceiptSha256: inputReceiptSha256(evidence),
  });
}

function snapshotReceipt(snapshot) {
  return Object.freeze({
    key: snapshot.key,
    metadata: snapshot.metadata,
    bytes: snapshot.bytes,
    sha256: snapshot.sha256,
  });
}

export function captureQualityGateCommonSnapshot(expectedRoot = root) {
  const budget = createMeasurementBudget();
  const baseline = captureFile(
    resolve(expectedRoot, 'quality-baseline.json'), expectedRoot, budget,
    MAX_CONTROL_FILE_BYTES, 'quality-baseline-controlbestand',
  );
  const manifest = captureFile(
    resolve(expectedRoot, TYPECHECK_DEBT_MANIFEST), expectedRoot, budget,
    MAX_CONTROL_FILE_BYTES, 'TypeScript-manifest-controlbestand',
  );
  const gate = captureFile(
    gateFile, expectedRoot, budget, MAX_CONTROL_FILE_BYTES, 'quality-gate-controlbestand',
  );
  const compiler = captureCompilerTreeBinding(expectedRoot, budget);
  const dependencies = captureDependencies(expectedRoot, budget);
  const nodeExecutable = captureExternalFile(
    process.execPath, MAX_NODE_EXECUTABLE_BYTES, 'Node-workerexecutable', '<node-executable>',
  );
  const receipt = Object.freeze({
    baseline: snapshotReceipt(baseline),
    manifest: snapshotReceipt(manifest),
    gate: snapshotReceipt(gate),
    worker: Object.freeze({
      bytes: Buffer.byteLength(PRIVATE_WORKER_SOURCE),
      sha256: sha256(PRIVATE_WORKER_SOURCE),
    }),
    compiler: compiler.binding,
    dependencies: dependencies.binding,
    nodeExecutable: snapshotReceipt(nodeExecutable),
  });
  return Object.freeze({
    baseline,
    manifest,
    gate,
    compiler,
    dependencies,
    nodeExecutable,
    receipt,
  });
}

function assertSameCommonSnapshot(actual, expected, label) {
  if (!sameValue(actual.receipt, expected)) {
    throw new Error(`${label} wijkt af van de gemeenschappelijke parentreceipt`);
  }
}

function manifestDiagnosticIdentity(entry) {
  return {
    contract: TYPECHECK_DIAGNOSTIC_CONTRACT,
    category: 'error',
    code: entry.code,
    file: entry.file,
    messageSha256: entry.messageSha256,
    siteSha256: entry.siteSha256,
  };
}

function canonicalDiagnosticPath(file) {
  if (file === '<global>') return true;
  return typeof file === 'string' && file.length > 0 && !file.includes('\\')
    && !file.startsWith('/') && path.posix.normalize(file) === file
    && file.split('/').every((component) => component && component !== '.' && component !== '..')
    && [...file].every((character) => {
      const code = character.codePointAt(0);
      return code > 0x1f && code !== 0x7f;
    });
}

function validateManifestDiagnostic(entry, previousFingerprint) {
  exactKeys(entry, [
    'fingerprint', 'code', 'file', 'messageSha256', 'siteSha256', 'count',
  ], 'TypeScript-diagnostiekmanifestregel');
  if (!HASH_PATTERN.test(entry.fingerprint) || !HASH_PATTERN.test(entry.messageSha256)
    || !HASH_PATTERN.test(entry.siteSha256) || !Number.isInteger(entry.code) || entry.code < 1
    || !canonicalDiagnosticPath(entry.file)
    || !safeInteger(entry.count, 1, MAX_DIAGNOSTIC_COUNT)
    || (previousFingerprint && entry.fingerprint <= previousFingerprint)
    || sha256(JSON.stringify(manifestDiagnosticIdentity(entry))) !== entry.fingerprint) {
    throw new Error('TypeScript-diagnostiekmanifestregel is niet canoniek');
  }
}

export function validateTypeScriptDebtManifest(manifest) {
  exactKeys(manifest, [
    'schema', 'contract', 'maximum', 'snapshotCount', 'compiler', 'config', 'sourceInventory',
    'programInputs', 'dependencies', 'inputReceiptSha256', 'provenance', 'diagnostics',
  ], 'TypeScript-diagnostiekmanifest');
  exactKeys(manifest.compiler, [
    'name', 'version', 'packageJsonSha256', 'compilerSha256', 'treeCount', 'treeBytes',
    'treeSha256',
  ], 'TypeScript-compilerbinding');
  exactKeys(manifest.config, [
    'path', 'sha256', 'filesCount', 'filesSha256', 'optionsSha256',
    'projectReferencesSha256', 'filesBytes',
  ], 'TypeScript-configbinding');
  exactKeys(manifest.sourceInventory, ['rootNames', 'count', 'sha256'], 'TypeScript-broninventaris');
  exactKeys(manifest.programInputs, ['count', 'bytes', 'sha256'], 'TypeScript-programmabinding');
  exactKeys(manifest.dependencies, ['count', 'bytes', 'sha256'], 'TypeScript-dependencybinding');
  exactKeys(manifest.provenance, ['scope', 'releaseEligible'], 'TypeScript-provenance');
  const compiler = manifest.compiler;
  const config = manifest.config;
  const inventory = manifest.sourceInventory;
  const programInputs = manifest.programInputs;
  const dependencies = manifest.dependencies;
  const provenance = manifest.provenance;
  if (manifest.schema !== 4 || manifest.contract !== TYPECHECK_DEBT_CONTRACT
    || !safeInteger(manifest.maximum, 0, MAX_DIAGNOSTIC_COUNT)
    || !safeInteger(manifest.snapshotCount, 0, MAX_DIAGNOSTIC_COUNT)
    || compiler.name !== 'typescript' || compiler.version !== EXPECTED_TYPESCRIPT_VERSION
    || !HASH_PATTERN.test(compiler.packageJsonSha256) || !HASH_PATTERN.test(compiler.compilerSha256)
    || !safeInteger(compiler.treeCount, 1, MAX_INPUT_FILES)
    || !safeInteger(compiler.treeBytes, 1, MAX_INPUT_BYTES)
    || !HASH_PATTERN.test(compiler.treeSha256)
    || config.path !== 'tsconfig.json' || !HASH_PATTERN.test(config.sha256)
    || !safeInteger(config.filesCount, 1, MAX_INPUT_FILES)
    || !safeInteger(config.filesBytes, 1, MAX_INPUT_BYTES)
    || !HASH_PATTERN.test(config.filesSha256) || !HASH_PATTERN.test(config.optionsSha256)
    || !HASH_PATTERN.test(config.projectReferencesSha256)
    || !Array.isArray(inventory.rootNames)
    || !safeInteger(inventory.rootNames.length, 1, MAX_INPUT_FILES)
    || !safeInteger(inventory.count, 1, MAX_INPUT_FILES) || !HASH_PATTERN.test(inventory.sha256)
    || !safeInteger(programInputs.count, inventory.count, MAX_INPUT_FILES)
    || !safeInteger(programInputs.bytes, 1, MAX_INPUT_BYTES)
    || !HASH_PATTERN.test(programInputs.sha256)
    || !safeInteger(dependencies.count, 1, MAX_INPUT_FILES)
    || !safeInteger(dependencies.bytes, 0, MAX_INPUT_BYTES)
    || !HASH_PATTERN.test(dependencies.sha256)
    || !HASH_PATTERN.test(manifest.inputReceiptSha256)
    || manifest.inputReceiptSha256 !== inputReceiptSha256(manifest)
    || !['worktree-snapshot', 'reviewed-release'].includes(provenance.scope)
    || typeof provenance.releaseEligible !== 'boolean'
    || provenance.releaseEligible !== (provenance.scope === 'reviewed-release')
    || !Array.isArray(manifest.diagnostics)
    || manifest.diagnostics.length > MAX_MANIFEST_ROWS) {
    throw new Error('TypeScript-diagnostiekmanifestcontract is ongeldig');
  }
  const canonicalRootNames = [...inventory.rootNames].sort();
  if (inventory.rootNames.some((name) => typeof name !== 'string' || !name
      || name.includes('\\') || name.startsWith('/') || path.posix.normalize(name) !== name
      || name.split('/').some((component) => !component || component === '.' || component === '..')
      || [...name].some((character) => {
        const code = character.codePointAt(0);
        return code <= 0x1f || code === 0x7f;
      }))
    || JSON.stringify(inventory.rootNames) !== JSON.stringify(canonicalRootNames)
    || new Set(inventory.rootNames).size !== inventory.rootNames.length
    || inventory.count !== inventory.rootNames.length
    || inventory.sha256 !== sha256(`${inventory.rootNames.join('\0')}\0`)) {
    throw new Error('TypeScript-broninventaris is niet canoniek');
  }
  let previous = null;
  let total = 0;
  for (const entry of manifest.diagnostics) {
    validateManifestDiagnostic(entry, previous);
    previous = entry.fingerprint;
    if (total > MAX_DIAGNOSTIC_COUNT - entry.count) {
      throw new Error('TypeScript-snapshotcount overschrijdt de harde bovengrens');
    }
    total += entry.count;
  }
  if (total !== manifest.snapshotCount) {
    throw new Error('TypeScript-snapshotcount verschilt van het diagnostiekmanifest');
  }
  return manifest;
}

function validateQualityBaseline(baseline) {
  exactKeys(baseline, [
    'typescriptDiagnostics', 'lintWarnings', 'complexityWarnings',
  ], 'quality-baseline');
  for (const [name, value] of Object.entries(baseline)) {
    if (!safeInteger(value, 0, MAX_DIAGNOSTIC_COUNT)) {
      throw new Error(`quality-baseline ${name} bevat geen begrensde safe integer`);
    }
  }
  return baseline;
}

function parseCanonicalControlJson(snapshot, label) {
  let value;
  try {
    value = JSON.parse(snapshot.text);
  } catch {
    throw new Error(`${label} bevat geen geldige gepinde JSON`);
  }
  if (snapshot.text !== `${JSON.stringify(value, null, 2)}\n`) {
    throw new Error(`${label} is niet canoniek of bevat dubbele/afwijkend geformatteerde JSON`);
  }
  return value;
}

function validateEvidenceDiagnostic(diagnostic) {
  exactKeys(diagnostic, [
    'contract', 'category', 'code', 'file', 'messageSha256', 'siteSha256',
    'fingerprint', 'line', 'message',
  ], 'TypeScript-evidencediagnose');
  const identity = {
    contract: diagnostic.contract,
    category: diagnostic.category,
    code: diagnostic.code,
    file: diagnostic.file,
    messageSha256: diagnostic.messageSha256,
    siteSha256: diagnostic.siteSha256,
  };
  if (diagnostic.contract !== TYPECHECK_DIAGNOSTIC_CONTRACT
    || diagnostic.category !== 'error'
    || !Number.isInteger(diagnostic.code) || diagnostic.code < 1
    || !canonicalDiagnosticPath(diagnostic.file)
    || typeof diagnostic.message !== 'string'
    || !HASH_PATTERN.test(diagnostic.messageSha256)
    || diagnostic.messageSha256 !== sha256(diagnostic.message)
    || !HASH_PATTERN.test(diagnostic.siteSha256)
    || diagnostic.fingerprint !== sha256(JSON.stringify(identity))
    || (diagnostic.line !== null && !safeInteger(diagnostic.line, 1, MAX_INPUT_BYTES))) {
    throw new Error('TypeScript-evidencediagnose is niet canoniek');
  }
}

export function validateTypeScriptEvidence(evidence) {
  exactKeys(evidence, [
    'compiler', 'config', 'sourceInventory', 'programInputs', 'dependencies',
    'diagnostics', 'inputReceiptSha256',
  ], 'TypeScript-evidence');
  if (!Array.isArray(evidence.diagnostics)
    || evidence.diagnostics.length > MAX_DIAGNOSTIC_COUNT) {
    throw new Error('TypeScript-evidence overschrijdt de diagnostiekbovengrens');
  }
  const bindingProbe = {
    schema: 4,
    contract: TYPECHECK_DEBT_CONTRACT,
    maximum: 0,
    snapshotCount: 0,
    compiler: evidence.compiler,
    config: evidence.config,
    sourceInventory: evidence.sourceInventory,
    programInputs: evidence.programInputs,
    dependencies: evidence.dependencies,
    inputReceiptSha256: evidence.inputReceiptSha256,
    provenance: { scope: 'worktree-snapshot', releaseEligible: false },
    diagnostics: [],
  };
  validateTypeScriptDebtManifest(bindingProbe);
  for (const diagnostic of evidence.diagnostics) validateEvidenceDiagnostic(diagnostic);
  if (evidence.inputReceiptSha256 !== inputReceiptSha256(evidence)) {
    throw new Error('TypeScript-evidence heeft geen canonieke inputreceipt');
  }
  return evidence;
}

function bindingMismatch(manifest, evidence) {
  if (canonicalJson(manifest.compiler) !== canonicalJson(evidence.compiler)) return 'compiler';
  if (canonicalJson(manifest.config) !== canonicalJson(evidence.config)) return 'config';
  if (canonicalJson(manifest.sourceInventory) !== canonicalJson(evidence.sourceInventory)) {
    return 'broninventaris';
  }
  if (canonicalJson(manifest.programInputs) !== canonicalJson(evidence.programInputs)) {
    return 'programmabronnen';
  }
  if (canonicalJson(manifest.dependencies) !== canonicalJson(evidence.dependencies)) {
    return 'dependencies';
  }
  if (manifest.inputReceiptSha256 !== evidence.inputReceiptSha256) return 'inputreceipt';
  return null;
}

export function evaluateTypeScriptDebt({
  manifest, evidence, baselineMaximum, releaseMode = false,
}) {
  validateTypeScriptDebtManifest(manifest);
  validateTypeScriptEvidence(evidence);
  const failures = [];
  if (!safeInteger(baselineMaximum, 0, MAX_DIAGNOSTIC_COUNT)) {
    failures.push('quality-baseline bevat geen begrensde safe integer');
  }
  if (manifest.maximum !== baselineMaximum) failures.push('manifestmaximum wijkt af van quality-baseline');
  const mismatch = bindingMismatch(manifest, evidence);
  if (mismatch) failures.push(`${mismatch} wijkt af van het diagnostiekmanifest`);
  if (manifest.provenance.scope === 'reviewed-release'
    || manifest.provenance.releaseEligible) {
    failures.push('reviewed-release vereist een externe root-owned attestatie; '
      + 'de lokale evaluator kan die authority niet minten');
  }
  if (releaseMode) {
    // Deze module bezit geen root-only sleutel of maintainerreceipt-verifier. Tot die externe
    // grens gate+baseline+manifest+inputs bindt, is elke standalone releaseclaim fail-closed.
    failures.push('releaseMode vereist een externe root-owned attestatie; standalone '
      + 'reviewed-release is geblokkeerd tot rootreceipt-integratie');
  }
  if (evidence.diagnostics.length > baselineMaximum) {
    failures.push('de TypeScript-diagnostiektelling overschrijdt het maximum');
  }
  const remaining = new Map(manifest.diagnostics.map((entry) => [entry.fingerprint, entry.count]));
  const unexpected = [];
  for (const diagnostic of evidence.diagnostics) {
    const count = remaining.get(diagnostic.fingerprint) || 0;
    if (count === 0) unexpected.push(diagnostic);
    else remaining.set(diagnostic.fingerprint, count - 1);
  }
  if (unexpected.length) failures.push(`${unexpected.length} onverwachte TypeScript-diagnose(s)`);
  const resolved = [...remaining.values()].reduce((sum, count) => sum + count, 0);
  return Object.freeze({
    ok: failures.length === 0,
    failures: Object.freeze(failures),
    unexpected: Object.freeze(unexpected),
    resolved,
  });
}

export function createTypeScriptDebtManifest(evidence, maximum, provenance = {
  scope: 'worktree-snapshot', releaseEligible: false,
}) {
  if (!safeInteger(maximum, 0, MAX_DIAGNOSTIC_COUNT)) {
    throw new Error('manifestgeneratie vereist een geldige, onverruimde maximumcap');
  }
  if (!Array.isArray(evidence?.diagnostics)
    || evidence.diagnostics.length > MAX_MANIFEST_ROWS
    || evidence.diagnostics.length > MAX_DIAGNOSTIC_COUNT) {
    throw new Error('manifestgeneratie overschrijdt de harde manifestrijbovengrens');
  }
  validateTypeScriptEvidence(evidence);
  if (!sameValue(provenance, { scope: 'worktree-snapshot', releaseEligible: false })) {
    throw new Error('manifestgenerator weigert reviewed-release zonder externe rootreceipt');
  }
  const grouped = new Map();
  for (const diagnostic of evidence.diagnostics) {
    const current = grouped.get(diagnostic.fingerprint);
    if (current) current.count += 1;
    else grouped.set(diagnostic.fingerprint, {
      fingerprint: diagnostic.fingerprint,
      code: diagnostic.code,
      file: diagnostic.file,
      messageSha256: diagnostic.messageSha256,
      siteSha256: diagnostic.siteSha256,
      count: 1,
    });
  }
  const manifest = {
    schema: 4,
    contract: TYPECHECK_DEBT_CONTRACT,
    maximum,
    snapshotCount: evidence.diagnostics.length,
    compiler: evidence.compiler,
    config: evidence.config,
    sourceInventory: evidence.sourceInventory,
    programInputs: evidence.programInputs,
    dependencies: evidence.dependencies,
    inputReceiptSha256: evidence.inputReceiptSha256,
    provenance,
    diagnostics: [...grouped.values()].sort((left, right) =>
      left.fingerprint.localeCompare(right.fingerprint)),
  };
  return validateTypeScriptDebtManifest(manifest);
}

const PRIVATE_WORKER_SOURCE = `
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const canonicalData = (value) => {
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalData(value[key])]),
  );
  return value;
};
const canonicalJson = (value) => JSON.stringify(canonicalData(value));
const exactKeys = (value, expected) => value && typeof value === 'object'
  && !Array.isArray(value)
  && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort());
const boundedCanonicalData = (rootValue) => {
  const stack = [{ value: rootValue, depth: 0 }];
  let count = 0;
  while (stack.length) {
    const { value, depth } = stack.pop();
    count += 1;
    if (count > 1_000_000 || depth > 64) {
      throw new Error('private workerrequest overschrijdt de structuurcap');
    }
    if (typeof value === 'string') {
      if (Buffer.byteLength(value) > 67_108_864) {
        throw new Error('private workerrequest overschrijdt de stringcap');
      }
    } else if (Array.isArray(value)) {
      if (value.length > 100_000) throw new Error('private workerrequest overschrijdt de lijstcap');
      for (let index = 0; index < value.length; index += 1) {
        stack.push({ value: value[index], depth: depth + 1 });
      }
    } else if (value && typeof value === 'object') {
      const keys = Object.keys(value);
      if (keys.length > 100_000) throw new Error('private workerrequest overschrijdt de sleutelcap');
      for (const key of keys) stack.push({ value: value[key], depth: depth + 1 });
    }
  }
};
let inputBytes = 0;
const chunks = [];
for await (const chunk of process.stdin) {
  inputBytes += chunk.length;
  if (inputBytes > 67_108_864) throw new Error('private workerrequest overschrijdt de bytecap');
  chunks.push(chunk);
}
const raw = Buffer.concat(chunks).toString('utf8');
const request = JSON.parse(raw);
if (!exactKeys(request, [
  'protocol', 'nonce', 'root', 'gateFile', 'requireFrom', 'gateSourceBase64',
  'gateSha256', 'manifestText', 'commonReceipt', 'operation',
])) throw new Error('private workerrequest heeft niet de exacte sleutelset');
if (request.protocol !== 'EvaTypeScriptPrivateWorker/v1'
  || !/^[a-f0-9]{64}$/u.test(request.nonce)
  || !/^[a-f0-9]{64}$/u.test(request.gateSha256)
  || typeof request.root !== 'string' || typeof request.gateFile !== 'string'
  || typeof request.requireFrom !== 'string' || typeof request.manifestText !== 'string'
  || typeof request.gateSourceBase64 !== 'string'
  || !['verify', 'generate', 'verify-generated'].includes(request.operation)
  || [request.root, request.gateFile, request.requireFrom].some(
    (value) => !value || value.length > 4096 || value.includes('\\0')
  )
  || Buffer.byteLength(request.manifestText) > 16_777_216
  || request.gateSourceBase64.length > 22_369_624
  || !exactKeys(request.commonReceipt, [
    'baseline', 'manifest', 'gate', 'worker', 'compiler', 'dependencies', 'nodeExecutable',
  ])) {
  throw new Error('private workerrequest heeft ongeldige velden');
}
boundedCanonicalData(request);
if (raw !== canonicalJson(request)) throw new Error('private workerrequest is niet canoniek');
const gateBytes = Buffer.from(request.gateSourceBase64, 'base64');
if (gateBytes.length > 16_777_216
  || gateBytes.toString('base64') !== request.gateSourceBase64
  || sha256(gateBytes) !== request.gateSha256) {
  throw new Error('private workerrequest bevat geen exact gepinde gatebron');
}
Object.defineProperty(globalThis, '__EVA_QUALITY_GATE_PRIVATE_BOOTSTRAP__', {
  configurable: true,
  value: Object.freeze({
    root: request.root,
    gateFile: request.gateFile,
    requireFrom: pathToFileURL(request.requireFrom).href,
  }),
});
const gate = await import('data:text/javascript;base64,' + request.gateSourceBase64);
delete globalThis.__EVA_QUALITY_GATE_PRIVATE_BOOTSTRAP__;
let manifest = null;
if (request.operation !== 'generate') {
  manifest = gate.validateTypeScriptDebtManifest(JSON.parse(request.manifestText));
  if (request.manifestText !== JSON.stringify(manifest, null, 2) + '\\n') {
    throw new Error('private workermanifest is niet canoniek geserialiseerd');
  }
}
const before = gate.captureQualityGateCommonSnapshot(request.root);
if (canonicalJson(before.receipt) !== canonicalJson(request.commonReceipt)) {
  throw new Error('childsnapshot vóór meting wijkt af van de parentreceipt');
}
if (manifest && canonicalJson(before.compiler.binding) !== canonicalJson(manifest.compiler)) {
  throw new Error('compilerbinding wijkt vóór uitvoering af van het manifest');
}
const evidence = gate.validateTypeScriptEvidence(gate.collectTypeScriptDebtEvidence(
  request.root, 'tsconfig.json', {
    compiler: request.commonReceipt.compiler,
    dependencies: request.commonReceipt.dependencies,
  },
));
const after = gate.captureQualityGateCommonSnapshot(request.root);
if (canonicalJson(after.receipt) !== canonicalJson(request.commonReceipt)) {
  throw new Error('childsnapshot na meting wijkt af van de parentreceipt');
}
process.stdout.write(canonicalJson({
  protocol: request.protocol,
  nonce: request.nonce,
  gateSha256: request.gateSha256,
  workerSha256: before.receipt.worker.sha256,
  commonBefore: before.receipt,
  commonAfter: after.receipt,
  evidence,
}));
`;

function safeChildEnvironment() {
  const environment = {};
  for (const name of ['PATH', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'SYSTEMROOT', 'WINDIR']) {
    if (typeof process.env[name] === 'string') environment[name] = process.env[name];
  }
  return environment;
}

function readLinuxProcessStat(pid) {
  const file = `/proc/${pid}/stat`;
  let descriptor;
  try {
    descriptor = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NONBLOCK
      | fs.constants.O_NOFOLLOW | (fs.constants.O_CLOEXEC || 0));
    const bytes = Buffer.allocUnsafe(4097);
    const count = fs.readSync(descriptor, bytes, 0, bytes.length, 0);
    if (count === bytes.length) throw new Error('Linux processtat overschrijdt de harde bytecap');
    const value = bytes.subarray(0, count).toString('utf8');
    const boundary = value.lastIndexOf(') ');
    if (boundary < 0) return null;
    const fields = value.slice(boundary + 2).trim().split(' ');
    const parent = Number(fields[1]);
    return Number.isSafeInteger(parent) && parent >= 0
      ? Object.freeze({ parent, state: fields[0] }) : null;
  } catch (error) {
    if (['ENOENT', 'ESRCH'].includes(error?.code)) return null;
    throw error;
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
}

function linuxProcessTable() {
  if (process.platform !== 'linux') return null;
  const children = new Map();
  const directory = fs.opendirSync('/proc');
  let count = 0;
  try {
    let entry;
    while ((entry = directory.readSync()) !== null) {
      if (!/^\d+$/u.test(entry.name)) continue;
      count += 1;
      if (count > MAX_INPUT_FILES) {
        throw new Error('Linux procestabel overschrijdt de harde containmentcap');
      }
      const pid = Number(entry.name);
      const status = readLinuxProcessStat(pid);
      if (!status || ['Z', 'X'].includes(status.state)) continue;
      const siblings = children.get(status.parent) || [];
      siblings.push(pid);
      children.set(status.parent, siblings);
    }
  } finally {
    directory.closeSync();
  }
  return children;
}

function trackLinuxDescendants(rootPid, tracked) {
  const table = linuxProcessTable();
  if (!table) return;
  const queue = [rootPid, ...tracked];
  const visited = new Set(queue);
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    for (const childPid of table.get(queue[cursor]) || []) {
      if (visited.has(childPid)) continue;
      if (visited.size >= MAX_INPUT_FILES) {
        throw new Error('Subprocesboom overschrijdt de harde containmentcap');
      }
      visited.add(childPid);
      tracked.add(childPid);
      queue.push(childPid);
    }
  }
}

function signalPid(pid, signal) {
  try { process.kill(pid, signal); } catch (error) {
    if (error?.code !== 'ESRCH') throw error;
  }
}

function killProcessTree(child, tracked) {
  if (!child?.pid) return;
  if (process.platform !== 'win32') {
    signalPid(-child.pid, 'SIGSTOP');
    if (process.platform === 'linux') {
      for (let pass = 0; pass < 3; pass += 1) {
        trackLinuxDescendants(child.pid, tracked);
        for (const pid of tracked) signalPid(pid, 'SIGSTOP');
      }
      for (const pid of [...tracked].reverse()) signalPid(pid, 'SIGKILL');
    }
    signalPid(-child.pid, 'SIGKILL');
  }
  try { child.kill('SIGKILL'); } catch { /* proces is al gestopt */ }
}

function runProcess(executable, args, {
  timeout = SUBPROCESS_TIMEOUT_MS, input = '', cwd = root,
  environment = safeChildEnvironment(),
  maxOutputBytes = 64 * 1024 * 1024,
} = {}) {
  const limits = {
    maxBuffer: 64 * 1024 * 1024,
  };
  const outputLimit = Math.min(limits.maxBuffer, maxOutputBytes);
  return new Promise((resolveResult) => {
    if (!safeInteger(outputLimit, 1, MAX_INPUT_BYTES)) {
      resolveResult({
        status: null,
        signal: null,
        stdout: '',
        stderr: '',
        error: new Error('subprocesuitvoer heeft geen geldige begrensde bytecap'),
      });
      return;
    }
    let child;
    try {
      child = spawn(executable, args, {
        cwd,
        env: environment,
        detached: process.platform !== 'win32',
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      resolveResult({ status: null, signal: null, stdout: '', stderr: '', error });
      return;
    }
    const stdout = [];
    const stderr = [];
    let outputBytes = 0;
    let failure = null;
    let settled = false;
    let exitStatus = null;
    let exitSignal = null;
    let timer = null;
    let containmentTracker = null;
    let hardSettleTimer = null;
    const tracked = new Set();
    const finish = (status = exitStatus, signal = exitSignal) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (containmentTracker) clearInterval(containmentTracker);
      if (hardSettleTimer) clearTimeout(hardSettleTimer);
      resolveResult({
        status,
        signal,
        stdout: Buffer.concat(stdout).toString('utf8'),
        stderr: Buffer.concat(stderr).toString('utf8'),
        error: failure,
      });
    };
    const stop = (error) => {
      if (settled || hardSettleTimer) return;
      failure ||= error;
      try { killProcessTree(child, tracked); } catch (containmentError) {
        failure = containmentError;
      }
      child.stdin.destroy();
      child.stdout.destroy();
      child.stderr.destroy();
      hardSettleTimer = setTimeout(() => finish(null, exitSignal || 'SIGKILL'), 75);
    };
    const collect = (target) => (chunk) => {
      if (outputBytes > outputLimit - chunk.length) {
        const error = new Error('subprocesuitvoer overschrijdt de begrensde maxBuffer');
        error.code = 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER';
        stop(error);
        return;
      }
      outputBytes += chunk.length;
      target.push(chunk);
    };
    child.stdout.on('data', collect(stdout));
    child.stderr.on('data', collect(stderr));
    child.on('error', stop);
    child.on('exit', (status, signal) => {
      exitStatus = status;
      exitSignal = signal;
    });
    child.stdin.on('error', () => {});
    if (process.platform === 'linux' && child.pid) {
      containmentTracker = setInterval(() => {
        try { trackLinuxDescendants(child.pid, tracked); } catch (error) { stop(error); }
      }, 25);
      containmentTracker.unref();
    }
    timer = setTimeout(() => {
      const error = new Error(`subproces overschreed timeout/deadline van ${timeout} ms`);
      error.code = 'ETIMEDOUT';
      stop(error);
    }, timeout);
    child.on('close', (status, signal) => {
      finish(status, signal);
    });
    child.stdin.end(input);
  });
}

function runNode(relativeExecutable, args, { timeout = SUBPROCESS_TIMEOUT_MS } = {}) {
  return runProcess(process.execPath, [resolve(root, relativeExecutable), ...args], { timeout });
}

function printLeaders(items, label) {
  const leaders = [...items.entries()]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 8);
  if (leaders.length) console.log(`${label}: ${leaders.map(([name, count]) => `${name}=${count}`).join(', ')}`);
}

function compare(label, actual, allowed) {
  console.log(`${label}: ${actual} (baseline/maximaal ${allowed})`);
  if (actual < allowed) console.log(`Baseline kan worden verlaagd van ${allowed} naar ${actual}.`);
  return actual <= allowed;
}

function assertBoundedCanonicalData(rootValue, label) {
  const stack = [{ value: rootValue, depth: 0 }];
  let count = 0;
  while (stack.length) {
    const { value, depth } = stack.pop();
    count += 1;
    if (count > MAX_DIAGNOSTIC_COUNT || depth > MAX_INPUT_DEPTH) {
      throw new Error(`${label} overschrijdt de precanonieke structuurcap`);
    }
    if (typeof value === 'string') {
      if (Buffer.byteLength(value) > MAX_WORKER_EVIDENCE_BYTES) {
        throw new Error(`${label} overschrijdt de precanonieke stringcap`);
      }
    } else if (Array.isArray(value)) {
      if (value.length > MAX_DIAGNOSTIC_COUNT) {
        throw new Error(`${label} overschrijdt de precanonieke lijstcap`);
      }
      for (let index = 0; index < value.length; index += 1) {
        stack.push({ value: value[index], depth: depth + 1 });
      }
    } else if (value && typeof value === 'object') {
      const keys = Object.keys(value);
      if (keys.length > MAX_INPUT_FILES) {
        throw new Error(`${label} overschrijdt de precanonieke sleutelcap`);
      }
      for (const key of keys) stack.push({ value: value[key], depth: depth + 1 });
    }
  }
}

function parseCanonicalManifestText(text, label) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > MAX_CONTROL_FILE_BYTES) {
    throw new Error(`${label} overschrijdt de harde bytecap`);
  }
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error(`${label} bevat geen geldige JSON`); }
  const manifest = validateTypeScriptDebtManifest(parsed);
  if (text !== `${JSON.stringify(manifest, null, 2)}\n`) {
    throw new Error(`${label} is niet canoniek geserialiseerd`);
  }
  return manifest;
}

async function measureTypeScriptEvidence(operation = 'verify', generatedManifestText = null) {
  if (!['verify', 'generate', 'verify-generated'].includes(operation)) {
    throw new Error('TypeScript-meetoperatie is niet toegestaan');
  }
  const before = captureQualityGateCommonSnapshot(root);
  const baseline = validateQualityBaseline(parseCanonicalControlJson(
    before.baseline, 'quality-baseline-controlbestand',
  ));
  let manifest = null;
  let manifestText = '';
  if (operation === 'verify') {
    manifestText = before.manifest.text;
    manifest = parseCanonicalManifestText(manifestText, 'TypeScript-manifest-controlbestand');
  } else if (operation === 'verify-generated') {
    manifestText = generatedManifestText;
    manifest = parseCanonicalManifestText(manifestText, 'gegenereerd TypeScript-manifest');
  }
  if (manifest && canonicalJson(before.compiler.binding) !== canonicalJson(manifest.compiler)) {
    const field = Object.keys(before.compiler.binding)
      .find((name) => before.compiler.binding[name] !== manifest.compiler[name]) || '<structuur>';
    throw new Error(`compilerboom wijkt vóór workeruitvoering af van het diagnostiekmanifest (${field})`);
  }
  const gateBytes = Buffer.from(before.gate.text, 'utf8');
  if (gateBytes.length !== before.gate.bytes || sha256(gateBytes) !== before.gate.sha256) {
    throw new Error('quality-gate gebruikt geen canonieke UTF-8 bronbytes');
  }
  const nonce = randomBytes(32).toString('hex');
  const request = {
    protocol: TYPECHECK_PRIVATE_WORKER_PROTOCOL,
    nonce,
    root,
    gateFile,
    requireFrom: gateFile,
    gateSourceBase64: gateBytes.toString('base64'),
    gateSha256: before.gate.sha256,
    manifestText,
    commonReceipt: before.receipt,
    operation,
  };
  const measured = await runProcess(before.nodeExecutable.absolute, [
    '--input-type=module', '--eval', PRIVATE_WORKER_SOURCE,
  ], {
    timeout: TYPECHECK_MEASUREMENT_TIMEOUT_MS,
    input: canonicalJson(request),
    maxOutputBytes: MAX_WORKER_EVIDENCE_BYTES,
  });
  const after = captureQualityGateCommonSnapshot(root);
  assertSameCommonSnapshot(after, before.receipt, 'parentsnapshot na worker');
  if (measured.error || measured.signal || measured.status !== 0) {
    const reason = measured.error?.message
      || (measured.signal ? `SIGKILL/signaal ${measured.signal}` : null)
      || measured.stderr || `exitstatus ${measured.status}`;
    throw new Error(`TypeScript-meetchild faalde of overschreed de deadline: ${reason}`);
  }
  if (measured.stderr) {
    throw new Error(`TypeScript-meetchild schreef onverwacht stderr: ${measured.stderr}`);
  }
  if (Buffer.byteLength(measured.stdout) > MAX_WORKER_EVIDENCE_BYTES) {
    throw new Error('TypeScript-workerevidence overschrijdt de bytecap');
  }
  const envelope = JSON.parse(measured.stdout);
  exactKeys(envelope, [
    'protocol', 'nonce', 'gateSha256', 'workerSha256', 'commonBefore',
    'commonAfter', 'evidence',
  ], 'TypeScript-workerenvelope');
  assertBoundedCanonicalData(envelope, 'TypeScript-workerenvelope');
  const evidence = validateTypeScriptEvidence(envelope.evidence);
  if (measured.stdout !== canonicalJson(envelope)) {
    throw new Error('TypeScript-workerenvelope is niet canoniek geserialiseerd');
  }
  if (envelope.protocol !== TYPECHECK_PRIVATE_WORKER_PROTOCOL || envelope.nonce !== nonce
    || envelope.gateSha256 !== before.gate.sha256
    || envelope.workerSha256 !== before.receipt.worker.sha256
    || canonicalJson(envelope.commonBefore) !== canonicalJson(before.receipt)
    || canonicalJson(envelope.commonAfter) !== canonicalJson(before.receipt)) {
    throw new Error('TypeScript-workerenvelope wijkt af van nonce of common receipt');
  }
  if (canonicalJson(evidence.compiler) !== canonicalJson(before.receipt.compiler)
    || canonicalJson(evidence.dependencies) !== canonicalJson(before.receipt.dependencies)) {
    throw new Error('TypeScript-workerevidence wijkt af van de parentcandidate');
  }
  return Object.freeze({ before, after, baseline, manifest, evidence });
}

async function typecheck() {
  let measurement;
  try {
    measurement = await measureTypeScriptEvidence('verify');
  } catch (error) {
    console.error(`Typecheck kon niet betrouwbaar worden uitgevoerd: ${error.message}`);
    process.exitCode = 1;
    return;
  }
  const { evidence, manifest, baseline } = measurement;
  const files = new Map();
  for (const diagnostic of evidence.diagnostics) {
    files.set(diagnostic.file, (files.get(diagnostic.file) || 0) + 1);
  }
  printLeaders(files, 'Meeste TypeScript-diagnoses');
  compare('TypeScript JS-diagnoses', evidence.diagnostics.length, baseline.typescriptDiagnostics);
  const result = evaluateTypeScriptDebt({
    manifest,
    evidence,
    baselineMaximum: baseline.typescriptDiagnostics,
    releaseMode: process.env.EVA_ROOT_GATE_DIRECT === '1',
  });
  if (result.resolved > 0) console.log(`${result.resolved} toegestane diagnose(s) zijn opgelost; verlaag manifest en baseline samen.`);
  console.log(`TypeScript-debtprovenance: ${manifest.provenance.scope}`);
  for (const diagnostic of result.unexpected.slice(0, 12)) {
    console.error(`Onverwachte TypeScript-diagnose: ${diagnostic.file}:${diagnostic.line ?? '-'} TS${diagnostic.code} ${diagnostic.message} [${diagnostic.fingerprint}]`);
  }
  for (const failure of result.failures) console.error(`Typecheck-regressie: ${failure}.`);
  if (!result.ok) {
    process.exitCode = 1;
  }
}

function createPrivateManifestDirectory() {
  const temporaryRoot = process.platform === 'win32' ? path.resolve(os.tmpdir()) : '/tmp';
  const rootStatus = fs.lstatSync(temporaryRoot, { bigint: true });
  const rootMode = Number(rootStatus.mode & 0o7777n);
  if (!rootStatus.isDirectory() || rootStatus.isSymbolicLink()
    || fs.realpathSync.native(temporaryRoot) !== temporaryRoot
    || (process.platform !== 'win32' && (rootMode & 0o022) !== 0 && (rootMode & 0o1000) === 0)) {
    throw new Error('Private manifesttemp-root is niet canoniek of mist sticky bescherming');
  }
  const directory = fs.mkdtempSync(path.join(temporaryRoot, 'eva-typecheck-debt-'));
  fs.chmodSync(directory, 0o700);
  const status = fs.lstatSync(directory, { bigint: true });
  const expectedUid = typeof process.getuid === 'function' ? BigInt(process.getuid()) : status.uid;
  if (!status.isDirectory() || status.isSymbolicLink()
    || fs.realpathSync.native(directory) !== directory
    || status.uid !== expectedUid || Number(status.mode & 0o077n) !== 0) {
    throw new Error('Private manifesttempdirectory is niet exclusief aan de huidige gebruiker gebonden');
  }
  return directory;
}

function atomicWritePrivateManifest(directory, text) {
  const staging = path.join(directory, '.typescript-diagnostic-debt.tmp');
  const outputPath = path.join(directory, TYPECHECK_DEBT_MANIFEST);
  const bytes = Buffer.from(text, 'utf8');
  if (bytes.length > MAX_CONTROL_FILE_BYTES) {
    throw new Error('Gegenereerd TypeScript-manifest overschrijdt de controlfilecap');
  }
  const flags = fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL
    | fs.constants.O_NOFOLLOW | (fs.constants.O_CLOEXEC || 0);
  let descriptor;
  try {
    descriptor = fs.openSync(staging, flags, 0o600);
    let offset = 0;
    while (offset < bytes.length) {
      const written = fs.writeSync(descriptor, bytes, offset, bytes.length - offset, offset);
      if (written <= 0) throw new Error('Atomische manifestwrite maakte geen voortgang');
      offset += written;
    }
    fs.fsyncSync(descriptor);
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
  fs.renameSync(staging, outputPath);
  if (Number.isInteger(fs.constants.O_DIRECTORY)) {
    const directoryDescriptor = fs.openSync(directory,
      fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW
        | (fs.constants.O_CLOEXEC || 0));
    try { fs.fsyncSync(directoryDescriptor); } finally { fs.closeSync(directoryDescriptor); }
  }
  const status = fs.lstatSync(outputPath, { bigint: true });
  if (!status.isFile() || status.isSymbolicLink()
    || fs.realpathSync.native(outputPath) !== outputPath
    || Number(status.mode & 0o077n) !== 0) {
    throw new Error('Atomisch manifestoutput is niet privé en regulier');
  }
  return outputPath;
}

async function generateTypecheckDebt() {
  let outputDirectory = null;
  try {
    const initial = await measureTypeScriptEvidence('generate');
    const candidate = createTypeScriptDebtManifest(
      initial.evidence, initial.baseline.typescriptDiagnostics,
    );
    const candidateText = `${JSON.stringify(candidate, null, 2)}\n`;
    outputDirectory = createPrivateManifestDirectory();
    const outputPath = atomicWritePrivateManifest(outputDirectory, candidateText);

    const fresh = await measureTypeScriptEvidence('verify-generated', candidateText);
    const freshCandidate = createTypeScriptDebtManifest(
      fresh.evidence, fresh.baseline.typescriptDiagnostics,
    );
    const freshText = `${JSON.stringify(freshCandidate, null, 2)}\n`;
    if (candidateText !== freshText) {
      throw new Error('Fresh official verify week af van de sealed generatecandidate');
    }
    const evaluation = evaluateTypeScriptDebt({
      manifest: fresh.manifest,
      evidence: fresh.evidence,
      baselineMaximum: fresh.baseline.typescriptDiagnostics,
      releaseMode: false,
    });
    if (!evaluation.ok || evaluation.resolved !== 0) {
      throw new Error(`Fresh official verify faalde: ${evaluation.failures.join('; ')}`);
    }
    const outputSnapshot = captureExternalFile(
      outputPath, MAX_CONTROL_FILE_BYTES, 'gegenereerd TypeScript-manifest',
      '<generated-typecheck-manifest>', true,
    );
    const outputManifest = parseCanonicalManifestText(
      outputSnapshot.text, 'gegenereerd TypeScript-manifest',
    );
    if (outputSnapshot.text !== candidateText || !sameValue(outputManifest, candidate)) {
      throw new Error('Gegenereerd TypeScript-manifest veranderde vóór de finalereceipt');
    }
    process.stdout.write(`${JSON.stringify({
      outputPath,
      schema: outputManifest.schema,
      releaseEligible: outputManifest.provenance.releaseEligible,
      verified: true,
    })}\n`);
  } catch (error) {
    if (outputDirectory) fs.rmSync(outputDirectory, { recursive: true, force: true });
    throw error;
  }
}

async function eslintCheck(complexityOnly, baseline) {
  const args = complexityOnly
    ? ['--config', 'eslint.complexity.config.js', 'src', 'deploy', '--format', 'json']
    : ['src', 'test', 'scripts', 'deploy', '--format', 'json'];
  const result = await runNode('node_modules/eslint/bin/eslint.js', args);
  if (result.error || result.signal || result.status === null
    || (result.status !== 0 && result.status !== 1)) {
    const reason = result.error?.message
      || (result.signal ? `ESLint stopte door signaal ${result.signal}` : null)
      || `ESLint stopte met ongeldige exitstatus ${result.status}`;
    console.error(reason);
    process.exitCode = 1;
    return;
  }
  let reports;
  try {
    reports = JSON.parse(result.stdout);
  } catch {
    console.error(result.stderr || result.stdout || 'ESLint gaf geen leesbaar resultaat.');
    process.exitCode = 1;
    return;
  }
  if (!Array.isArray(reports) || reports.some((report) => !report
      || typeof report !== 'object' || !Array.isArray(report.messages)
      || !Number.isInteger(report.errorCount) || report.errorCount < 0
      || !Number.isInteger(report.warningCount) || report.warningCount < 0)) {
    console.error('ESLint gaf geen canonieke JSON-rapportage.');
    process.exitCode = 1;
    return;
  }

  const rules = new Map();
  let errors = 0;
  let warnings = 0;
  for (const report of reports) {
    errors += report.errorCount;
    warnings += report.warningCount;
    for (const message of report.messages) {
      const rule = message.ruleId || '<parser>';
      rules.set(rule, (rules.get(rule) || 0) + 1);
    }
  }

  printLeaders(rules, 'Meeste ESLint-meldingen');
  const allowed = complexityOnly ? baseline.complexityWarnings : baseline.lintWarnings;
  const passed = compare(complexityOnly ? 'Complexiteitswaarschuwingen' : 'Lintwaarschuwingen', warnings, allowed);
  console.log(`ESLint-fouten: ${errors}`);
  if (errors > 0 || !passed || (result.status === 1 && errors === 0)) process.exitCode = 1;
}

function maintainerCoverage() {
  const summary = JSON.parse(captureExternalFile(
    resolve(root, 'coverage/coverage-summary.json'), MAX_CONTROL_FILE_BYTES,
    'coverage-controlbestand', '<coverage-summary>', true,
  ).text);
  for (const file of ['eva-runtime-maintainer.mjs', 'eva-runtime-repair-protocol.mjs']) {
    const key = Object.keys(summary).find((name) => name.endsWith(`/deploy/${file}`));
    const percentage = key ? Number(summary[key]?.lines?.pct) : Number.NaN;
    console.log(`${file} regelcoverage: ${percentage}% (minimum 70%)`);
    if (!Number.isFinite(percentage) || percentage < 70) process.exitCode = 1;
  }
}

function assertSafeParentEnvironment() {
  if (process.execArgv.length !== 0) {
    throw new Error('Node execArgv/loaderflags zijn niet toegestaan aan de quality-gate trustgrens');
  }
  const nodeOptions = process.env.NODE_OPTIONS;
  if (typeof nodeOptions === 'string' && nodeOptions !== ''
    && nodeOptions !== '--disable-wasm-trap-handler') {
    throw new Error('NODE_OPTIONS is niet toegestaan aan de quality-gate trustgrens');
  }
  if (typeof process.env.NODE_PATH === 'string' && process.env.NODE_PATH !== '') {
    throw new Error('NODE_PATH is niet toegestaan aan de quality-gate trustgrens');
  }
}

async function main() {
  const mode = process.argv[2];
  try {
    assertSafeParentEnvironment();
    if (mode === 'typecheck') await typecheck();
    else if (mode === 'generate-typecheck-debt') await generateTypecheckDebt();
    else if (mode === 'lint' || mode === 'complexity') {
      const baselineSnapshot = captureFile(
        resolve(root, 'quality-baseline.json'), root, null,
        MAX_CONTROL_FILE_BYTES, 'quality-baseline-controlbestand',
      );
      const baseline = validateQualityBaseline(parseCanonicalControlJson(
        baselineSnapshot, 'quality-baseline-controlbestand',
      ));
      await eslintCheck(mode === 'complexity', baseline);
      verifyFile(
        baselineSnapshot, root, null, MAX_CONTROL_FILE_BYTES,
        'quality-baseline-controlbestand',
      );
    } else if (mode === 'maintainer-coverage') maintainerCoverage();
    else {
      console.error('Gebruik: node scripts/quality-gate.mjs '
        + '<typecheck|generate-typecheck-debt|lint|complexity|maintainer-coverage>');
      process.exitCode = 2;
    }
  } catch (error) {
    console.error(`Quality-gate faalde gesloten: ${error.message}`);
    process.exitCode = 2;
  }
}

if (!bootstrap && process.argv[1]) {
  const invocation = resolve(process.argv[1]);
  if (directInvocationPath === importedGateFile) {
    try {
      assertDirectInvocationConsistency();
      await main();
      assertDirectInvocationConsistency();
    } catch (error) {
      console.error(`Quality-gate executablebinding faalde gesloten: ${error.message}`);
      process.exitCode = 2;
    } finally {
      if (directInvocationDescriptor !== null) {
        fs.closeSync(directInvocationDescriptor);
        directInvocationDescriptor = null;
      }
    }
  } else {
    let invocationTarget = null;
    try { invocationTarget = fs.realpathSync.native(invocation); } catch { /* geen gate-invocation */ }
    if (invocationTarget === importedGateFile || invocationTarget === gateFile) {
      console.error('Quality-gate weigert uitvoering via een symlink- of aliaspad.');
      process.exitCode = 2;
    }
  }
}
