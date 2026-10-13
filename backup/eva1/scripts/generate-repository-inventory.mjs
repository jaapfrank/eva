#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const SCHEMA = 'EvaRepositoryInventory/v1';
const JSON_OUTPUT = 'docs/eva-repository-inventory.generated.json';
const MARKDOWN_OUTPUT = 'docs/eva-repository-inventory.generated.md';
const SELF_OUTPUTS = new Set([JSON_OUTPUT, MARKDOWN_OUTPUT]);
const SELF_HASH_MODE = 'canonical-self-reference-redacted';
const MAX_FILE_BYTES = 128 * 1024 * 1024;
const GIT_BINARY_PATH = '/usr/bin/git';
const DORMANT_SOURCE = new Set([
  'src/mind/artifactcollections.js',
  'src/mind/materiallifecycle.js',
  'src/mind/workcompletion.js',
  'src/mind/voiceingress.js',
]);
const CONFIRMED_TOMBSTONES = new Set([
  'src/mind/errorbook.js',
  'src/mind/toolpolicy.js',
  'test/vision-pending-identity.test.js',
]);
const HISTORICAL_DOCUMENTS = new Set([
  'docs/eva-runtime-map.md',
  'docs/live-acceptance-2026-08-04.md',
]);
const ACTIVE_LEDGER_INCIDENT_EVIDENCE = new Set([
  'scripts/execution-ledger-production14-recovery.mjs',
  'scripts/execution-ledger-production14-recovery.test.mjs',
  'scripts/execution-ledger-quiescent-snapshot.mjs',
  'scripts/execution-ledger-quiescent-snapshot.test.mjs',
]);
const ROOT_FILES = new Set([
  '.c8rc.json', '.env.example', '.gitignore', 'OPEN_SOURCE_COMPONENTEN.md', 'README.md',
  'component-watch.json', 'renovate.json',
  'eslint.complexity.config.js', 'eslint.config.js', 'package-lock.json', 'package.json',
  'quality-baseline.json', 'tsconfig.json', 'typescript-diagnostic-debt.json',
]);
const EXCLUSIONS = Object.freeze([
  { path: 'node_modules/', readPolicy: 'never-scan-or-hash', reason: 'verification dependencies only' },
  { path: 'dist/', readPolicy: 'never-read', reason: 'generated distribution output' },
  { path: 'coverage/', readPolicy: 'never-read', reason: 'generated coverage output' },
  { path: '.nyc_output/', readPolicy: 'never-read', reason: 'generated coverage state' },
  { path: 'data/', readPolicy: 'never-read', reason: 'private runtime and evidence state' },
  { path: '*.db', readPolicy: 'never-read', reason: 'private SQLite state' },
  { path: '*.db-*', readPolicy: 'never-read', reason: 'private SQLite sidecar state' },
  { path: '*.sqlite', readPolicy: 'never-read', reason: 'private SQLite state' },
  { path: '*.sqlite-*', readPolicy: 'never-read', reason: 'private SQLite sidecar state' },
  { path: '*.log', readPolicy: 'never-read', reason: 'runtime log output' },
  { path: '*.pid', readPolicy: 'never-read', reason: 'runtime process state' },
  { path: '__pycache__/', readPolicy: 'never-read', reason: 'generated Python bytecode' },
  { path: '*.py[cod]', readPolicy: 'never-read', reason: 'generated Python bytecode' },
  { path: '.ruff_cache/', readPolicy: 'never-read', reason: 'generated Python lint cache' },
  { path: '.pytest_cache/', readPolicy: 'never-read', reason: 'generated Python test cache' },
  { path: '.venv/', readPolicy: 'never-scan-or-hash', reason: 'local Python dependencies' },
  { path: 'venv/', readPolicy: 'never-scan-or-hash', reason: 'local Python dependencies' },
  { path: '.env', readPolicy: 'never-read', reason: 'private environment configuration' },
  { path: '.env.*', readPolicy: 'never-read', reason: 'private environment configuration' },
  { path: '*.env', readPolicy: 'never-read', reason: 'private environment configuration' },
  { path: '*.env.*', readPolicy: 'never-read', reason: 'private environment configuration' },
  { path: 'eva2/deploy/living-core/secrets/', readPolicy: 'never-read', reason: 'private managed runtime secrets' },
  { path: 'eva2/deploy/living-core/secrets/**', readPolicy: 'never-read', reason: 'private managed runtime secrets' },
  { path: '*.key', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*.pem', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*.ppk', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*.p12', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*.pfx', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*.jks', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*.keystore', readPolicy: 'never-read', reason: 'private credential material' },
  { path: 'id_rsa', readPolicy: 'never-read', reason: 'private credential material' },
  { path: 'id_rsa.*', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*_rsa', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*_rsa.*', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*_ed25519', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*_ed25519.*', readPolicy: 'never-read', reason: 'private credential material' },
  { path: '*.swp', readPolicy: 'never-read', reason: 'editor scratch output' },
  { path: '*.swo', readPolicy: 'never-read', reason: 'editor scratch output' },
  { path: '*~', readPolicy: 'never-read', reason: 'editor backup output' },
  { path: '*:Zone.Identifier', readPolicy: 'never-read', reason: 'operating-system metadata' },
  { path: '.DS_Store', readPolicy: 'never-read', reason: 'operating-system metadata' },
  { path: 'Thumbs.db', readPolicy: 'never-read', reason: 'operating-system metadata' },
  { path: '/scratch_*', readPolicy: 'never-read', reason: 'repository-root scratch output' },
  { path: '/_*.cjs', readPolicy: 'never-read', reason: 'repository-root scratch output' },
  { path: '/eva_brain/replay/LOCK_*.md', readPolicy: 'never-read', reason: 'brain replay coordination state' },
  { path: '/eva_brain/replay/out_*.txt', readPolicy: 'never-read', reason: 'brain replay output' },
  { path: '/eva2/experiments/distroless-core-v1/wheelhouse/*.whl', readPolicy: 'never-read', reason: 'locally materialized sealed wheelhouse' },
  { path: '/eva2/experiments/distroless-hindsight-api-v1/offline-artifacts/*.apk', readPolicy: 'never-read', reason: 'locally materialized sealed package artifact' },
  { path: '/eva2/experiments/distroless-hindsight-api-v1/offline-artifacts/*.tar', readPolicy: 'never-read', reason: 'locally materialized sealed source artifact' },
  { path: '/eva2/experiments/distroless-hindsight-api-v1/wheelhouse/*.whl', readPolicy: 'never-read', reason: 'locally materialized sealed wheelhouse' },
  { path: '/eva2/build/', readPolicy: 'never-read', reason: 'generated Python build output' },
  { path: '/eva2/deploy/voice-session/vendor/', readPolicy: 'never-read', reason: 'locally materialized pinned voice upstream artifacts' },
]);
const GITIGNORE_NEGATIONS = Object.freeze([
  '!.env.example',
  '!**/.env.example',
  '!eva2/deploy/living-core/runtime.env.example',
  '!eva2/deploy/living-core/hindsight.env.example',
]);
const GITIGNORE_PATTERNS = Object.freeze(EXCLUSIONS.flatMap(({ path: pattern }) => (
  pattern === '*.env.*' ? [pattern, ...GITIGNORE_NEGATIONS] : [pattern]
)));
const SAFE_ENV_TEMPLATE_PATHS = new Set([
  'eva2/deploy/living-core/runtime.env.example',
  'eva2/deploy/living-core/hindsight.env.example',
]);

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function unix(value) {
  return value.split(path.sep).join('/');
}

function canonicalJson(value) {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalJson(value[key])]));
}

function nulList(value) {
  return value.split('\0').filter(Boolean).map(unix);
}

function attestedGitBinary() {
  let stat;
  try {
    stat = fs.lstatSync(GIT_BINARY_PATH);
  } catch (error) {
    throw new Error(`GIT_BINARY_CONTRACT: ${error.message}`);
  }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.uid !== 0 || stat.gid !== 0
    || (stat.mode & 0o111) === 0
    || (stat.mode & 0o022) !== 0 || fs.realpathSync(GIT_BINARY_PATH) !== GIT_BINARY_PATH) {
    throw new Error(`GIT_BINARY_CONTRACT: ${GIT_BINARY_PATH} is niet attesteerbaar`);
  }
  return GIT_BINARY_PATH;
}

function isolatedGitEnvironment() {
  const environment = Object.fromEntries(Object.entries(process.env)
    .filter(([name]) => !name.startsWith('GIT_')));
  return {
    ...environment,
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_CONFIG_SYSTEM: '/dev/null',
  };
}

function gitList(root, args) {
  return nulList(execFileSync(attestedGitBinary(), args, {
    cwd: root, env: isolatedGitEnvironment(),
    encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
  }));
}

function parsedGitignorePatterns(contents) {
  return contents.split(/\r?\n/u).filter((line) => line && !line.startsWith('#'));
}

function validateGitignoreContract(root, repositoryPaths = null) {
  if (repositoryPaths) {
    const ignoreFiles = [...repositoryPaths].filter((file) => (
      (file === '.gitignore' || file.endsWith('/.gitignore'))
      && fs.existsSync(path.join(root, file))
    ));
    if (ignoreFiles.length !== 1 || ignoreFiles[0] !== '.gitignore') {
      const found = ignoreFiles.length ? ignoreFiles.sort().join(',') : 'geen';
      throw new Error(`GITIGNORE_CONTRACT_DRIFT: alleen root-.gitignore is toegestaan; aangetroffen ${found}`);
    }
  }
  const target = path.join(root, '.gitignore');
  let stat;
  try {
    stat = fs.lstatSync(target);
  } catch (error) {
    if (error?.code === 'ENOENT') {
      throw new Error('GITIGNORE_CONTRACT_DRIFT: versioned root-.gitignore ontbreekt');
    }
    throw error;
  }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_FILE_BYTES) {
    throw new Error('GITIGNORE_CONTRACT_DRIFT: root-.gitignore is niet regulier of te groot');
  }
  const actual = parsedGitignorePatterns(fs.readFileSync(target, 'utf8'));
  if (JSON.stringify(actual) !== JSON.stringify(GITIGNORE_PATTERNS)) {
    throw new Error(`GITIGNORE_CONTRACT_DRIFT: verwacht exact ${EXCLUSIONS.length} uitsluitingen en ${GITIGNORE_NEGATIONS.length} negaties`);
  }
}

function sensitivePath(relative) {
  const normalized = unix(String(relative || '')).replace(/^\.\//u, '');
  const segments = normalized.split('/').filter(Boolean);
  const basename = segments.at(-1) || '';
  const excludedDirectory = segments.some((segment) => [
    'node_modules', 'dist', 'coverage', '.nyc_output', 'data',
    '__pycache__', '.venv', 'venv',
  ].includes(segment));
  const sqliteOrRuntime = /\.(?:db|sqlite)(?:-.*)?$/u.test(basename)
    || /\.(?:log|pid)$/u.test(basename);
  const environmentTemplate = basename === '.env.example'
    || SAFE_ENV_TEMPLATE_PATHS.has(normalized);
  const environment = !environmentTemplate && (
    basename === '.env' || basename.startsWith('.env.')
    || basename.endsWith('.env') || basename.includes('.env.')
  );
  const credential = /\.(?:key|pem|ppk|p12|pfx|jks|keystore)$/u.test(basename)
    || basename === 'id_rsa' || basename.startsWith('id_rsa.')
    || basename.endsWith('_rsa') || basename.includes('_rsa.')
    || basename.endsWith('_ed25519') || basename.includes('_ed25519.');
  const generatedPython = /\.py[cod]$/u.test(basename);
  const editorOrOs = /\.(?:swp|swo)$/u.test(basename) || basename.endsWith('~')
    || basename.endsWith(':Zone.Identifier')
    || basename === '.DS_Store' || basename === 'Thumbs.db';
  const rootScratch = segments.length === 1 && (
    basename.startsWith('scratch_') || (basename.startsWith('_') && basename.endsWith('.cjs'))
  );
  const replay = normalized.startsWith('eva_brain/replay/')
    && (/^LOCK_.*\.md$/u.test(basename) || /^out_.*\.txt$/u.test(basename));
  return excludedDirectory || sqliteOrRuntime || environment || credential
    || generatedPython || editorOrOs || rootScratch || replay;
}

const ROLE_RULES = Object.freeze([
  { id: 'root-config', role: 'repository-config', test: (file) => ROOT_FILES.has(file) },
  { id: 'benchmark', role: 'benchmark', test: (file) => file.startsWith('benchmark/') },
  { id: 'deployment', role: 'deployment', test: (file) => file.startsWith('deploy/') },
  { id: 'design', role: 'documentation', test: (file) => file.startsWith('design/') },
  { id: 'experiments', role: 'experimental', test: (file) => file.startsWith('experiments/') },
  { id: 'transition', role: 'documentation', test: (file) => file.startsWith('transition/') },
  {
    id: 'eva2-verification', role: 'verification',
    test: (file) => file.startsWith('eva2/tests/') || file.startsWith('eva2/evidence/'),
  },
  {
    id: 'eva2-documentation', role: 'documentation',
    test: (file) => /^eva2\/[^/]+\.md$/u.test(file)
      || ['eva2/design/', 'eva2/identity-proposals/', 'eva2/reviews/']
        .some((prefix) => file.startsWith(prefix)),
  },
  {
    id: 'eva2-deployment', role: 'deployment',
    test: (file) => file.startsWith('eva2/deploy/') || file.startsWith('eva2/migrations/'),
  },
  {
    id: 'eva2-experimental', role: 'experimental',
    test: (file) => file.startsWith('eva2/experiments/') || file.startsWith('eva2/benchmark/'),
  },
  {
    id: 'eva2-verification-runtime', role: 'verification-runtime',
    test: (file) => file.startsWith('eva2/tools/'),
  },
  {
    id: 'eva2-runtime', role: 'product-runtime',
    test: (file) => ['eva2/src/', 'eva2/connector_sdk/', 'eva2/connectors/']
      .some((prefix) => file.startsWith(prefix)),
  },
  {
    id: 'eva2-build-contract', role: 'repository-config',
    test: (file) => [
      'eva2/forge/', 'eva2/release/', 'eva2/schemas/', 'eva2/vendor/',
    ].some((prefix) => file.startsWith(prefix)) || [
      'eva2/alembic.ini', 'eva2/pyproject.toml', 'eva2/requirements-4d.lock',
      'eva2/requirements-delegated-work.lock', 'eva2/requirements.lock',
    ].includes(file),
  },
  {
    id: 'generated-doc', role: 'generated',
    test: (file) => SELF_OUTPUTS.has(file) || /^docs\/.+\.generated\.(?:json|md)$/u.test(file),
  },
  {
    id: 'documentation', role: 'documentation',
    test: (file) => file === 'AGENTS.md' || (file.startsWith('docs/')
      && !SELF_OUTPUTS.has(file) && !/^docs\/.+\.generated\.(?:json|md)$/u.test(file)),
  },
  {
    id: 'python-test', role: 'verification',
    test: (file) => /^eva_brain\/(?:.+\/)?test_[^/]+\.py$/u.test(file),
  },
  {
    id: 'python-runtime', role: 'separate-service',
    test: (file) => file.startsWith('eva_brain/')
      && !/^eva_brain\/(?:.+\/)?test_[^/]+\.py$/u.test(file),
  },
  { id: 'native-prototype', role: 'experimental', test: (file) => file.startsWith('native/') },
  {
    id: 'script-test', role: 'verification',
    test: (file) => /^scripts\/.+\.test\.mjs$/u.test(file)
      && file !== 'scripts/run-isolated-tests.test.mjs',
  },
  {
    id: 'isolated-runner', role: 'verification-runtime',
    test: (file) => file.startsWith('scripts/run-isolated-tests'),
  },
  {
    id: 'architecture-tool', role: 'verification-runtime',
    test: (file) => /^scripts\/generate-.+\.(?:js|mjs)$/u.test(file),
  },
  {
    id: 'operator-script', role: 'operation',
    test: (file) => file.startsWith('scripts/')
      && !/^scripts\/.+\.test\.mjs$/u.test(file)
      && !file.startsWith('scripts/run-isolated-tests')
      && !/^scripts\/generate-.+\.(?:js|mjs)$/u.test(file),
  },
  {
    id: 'browser-asset', role: 'browser',
    test: (file) => file.startsWith('src/web/public/') && !/\.(?:js|mjs)$/u.test(file),
  },
  {
    id: 'source-runtime', role: 'product-runtime',
    test: (file) => file.startsWith('src/')
      && !(file.startsWith('src/web/public/') && !/\.(?:js|mjs)$/u.test(file)),
  },
  {
    id: 'test-entry', role: 'verification',
    test: (file) => /^test\/.+\.test\.(?:js|mjs)$/u.test(file),
  },
  {
    id: 'test-helper', role: 'verification-fixture',
    test: (file) => file.startsWith('test/') && !/^test\/.+\.test\.(?:js|mjs)$/u.test(file),
  },
]);

function roleContract(role, file) {
  const contracts = {
    'repository-config': {
      owner: 'repository', inputs: ['repository and tool configuration'],
      outputs: ['runtime, build or verification configuration'], state: [], effects: ['configures downstream tools'],
    },
    benchmark: {
      owner: 'benchmark', inputs: ['benchmark case contract'], outputs: ['judge and runner input'],
      state: [], effects: [],
    },
    deployment: {
      owner: 'deployment', inputs: ['operator, systemd or repair request'],
      outputs: ['installed service, receipt or lifecycle transition'], state: ['systemd and release state'],
      effects: ['may install, promote, restart or recover runtime files'],
    },
    generated: {
      owner: 'architecture', inputs: ['classified repository state'], outputs: ['canonical generated evidence'],
      state: [], effects: ['writes generated documentation'],
    },
    documentation: {
      owner: 'documentation', inputs: ['design and evidence'], outputs: ['human-readable contract'],
      state: [], effects: [],
    },
    verification: {
      owner: 'verification', inputs: ['production or fixture contract'], outputs: ['test decision'],
      state: ['temporary isolated test state'], effects: ['may execute only inside its declared gate'],
    },
    'verification-fixture': {
      owner: 'verification', inputs: ['test setup'], outputs: ['fixture capability or child process'],
      state: ['temporary test state'], effects: ['only through a consuming test'],
    },
    'verification-runtime': {
      owner: 'verification', inputs: ['repository and test selection'], outputs: ['gate or inventory result'],
      state: ['temporary gate state'], effects: ['starts bounded verification processes or writes generated evidence'],
    },
    'separate-service': {
      owner: 'brain-service', inputs: ['model, media or offline dataset'], outputs: ['inference or model artifact'],
      state: ['Python environment, model or dataset'], effects: ['may use GPU, HTTP or filesystem'],
    },
    experimental: {
      owner: 'experimental', inputs: ['prototype request'], outputs: ['prototype receipt'],
      state: ['prototype-local state'], effects: ['not authorized for production'],
    },
    operation: {
      owner: 'operations', inputs: ['explicit operator invocation'], outputs: ['report, backup or recovery artifact'],
      state: ['operator-selected filesystem or database state'], effects: ['may mutate only under its declared operator contract'],
    },
    browser: {
      owner: 'web-ui', inputs: ['browser load'], outputs: ['DOM, style or render asset'],
      state: ['browser state'], effects: ['browser rendering'],
    },
    'product-runtime': {
      owner: 'eva-runtime', inputs: ['issued runtime input and dependencies'], outputs: ['runtime result or capability'],
      state: ['runtime-owned durable or process state'], effects: ['module-specific runtime effects'],
    },
  };
  const contract = contracts[role];
  if (!contract) throw new Error(`onbekende rol voor ${file}: ${role}`);
  return contract;
}

function disposition(file, role, present) {
  if (!present && CONFIRMED_TOMBSTONES.has(file)) {
    return { decision: 'delete', reason: 'confirmed replaced tombstone' };
  }
  if (!present) return { decision: 'decision', reason: 'unresolved tracked tombstone' };
  if (file === 'src/mind/voiceingress.js') {
    return { decision: 'delete', reason: 'redundant re-export with no production consumer' };
  }
  if (DORMANT_SOURCE.has(file)) {
    return { decision: 'isolate', reason: 'dormant unfinished subsystem; move outside production source' };
  }
  if (file === 'native/workspace_writer_broker.c') {
    return { decision: 'integrate', reason: 'missing OS-exclusive writer required by the production contract' };
  }
  if (file === 'scripts/sanitize-secret-history.mjs') {
    return { decision: 'quarantine', reason: 'effectful offline database rewrite lacks an attested operator gate' };
  }
  if (ACTIVE_LEDGER_INCIDENT_EVIDENCE.has(file)) {
    return { decision: 'keep', reason: 'active incident evidence and root-gate coverage' };
  }
  if (role === 'experimental') {
    return { decision: 'keep', reason: 'explicitly classified non-production prototype' };
  }
  if (HISTORICAL_DOCUMENTS.has(file)) {
    return { decision: 'archive', reason: 'dated evidence superseded by current contracts' };
  }
  if (/^scripts\/execution-ledger-.+\.(?:mjs|test\.mjs)$/u.test(file)) {
    return { decision: 'archive', reason: 'retain until the ledger incident is formally closed' };
  }
  return { decision: 'keep', reason: 'active repository input or evidence' };
}

function releaseCritical(file, role, decision) {
  if (decision === 'delete' || decision === 'archive' || decision === 'isolate'
    || decision === 'quarantine' || role === 'experimental') return false;
  if (role === 'documentation') return file === 'docs/eva-runtime-contracts.md';
  return role !== 'product-runtime' || !DORMANT_SOURCE.has(file);
}

function entrypointEvidenceFor(file, role, edges = []) {
  const evidence = [];
  if (file === 'src/index.js') evidence.push('package-main');
  if (file === 'src/web/public/app.js') evidence.push('browser-entry');
  if (file === 'eva_brain/service.py') evidence.push('separate-service-entry');
  if (role === 'operation') evidence.push('manual-operator-command');
  if (role === 'deployment' && /\.(?:path|service)$/u.test(file)) evidence.push('systemd-unit');
  if (role === 'deployment' && /\/install-[^/]+\.sh$/u.test(file)) evidence.push('manual-installer');
  for (const edge of edges.filter((candidate) => candidate.to === file
    && ['package-script', 'subprocess', 'systemd-exec', 'systemd-trigger'].includes(candidate.kind))) {
    evidence.push(`${edge.kind}:${edge.from}`);
  }
  return evidence.sort();
}

function testEntryPath(file) {
  return /\.test\.(?:js|mjs)$/u.test(file)
    || /^eva_brain\/(?:.+\/)?test_[^/]+\.py$/u.test(file);
}

const UNRESOLVED_DISPOSITION_CODES = Object.freeze({
  archive: 'UNRESOLVED_DISPOSITION_ARCHIVE',
  decision: 'UNRESOLVED_DISPOSITION_DECISION',
  delete: 'UNRESOLVED_DISPOSITION_DELETE',
  integrate: 'UNRESOLVED_DISPOSITION_INTEGRATE',
  isolate: 'UNRESOLVED_DISPOSITION_ISOLATE',
  quarantine: 'UNRESOLVED_DISPOSITION_QUARANTINE',
});

function policyErrorsFor(files, edges) {
  const gatedTests = new Set(edges.filter((edge) => edge.kind === 'package-test-glob')
    .map((edge) => edge.to));
  const errors = [];
  for (const file of files) {
    if (file.decision !== 'keep') {
      const code = UNRESOLVED_DISPOSITION_CODES[file.decision];
      if (!code) throw new Error(`onbekend onopgelost bestandsbesluit: ${file.path}:${file.decision}`);
      errors.push({ code, path: file.path });
    }
    if (file.gitState === 'untracked') errors.push({ code: 'UNTRACKED_REPOSITORY_FILE', path: file.path });
    if (file.gitState === 'tombstone') errors.push({ code: 'TRACKED_TOMBSTONE', path: file.path });
    if (file.role === 'verification' && testEntryPath(file.path) && file.present
      && !gatedTests.has(file.path)) errors.push({ code: 'UNGATED_TEST', path: file.path });
  }
  return errors.sort((left, right) => left.code.localeCompare(right.code)
    || left.path.localeCompare(right.path));
}

function summaryFor(files, policyErrors) {
  return {
    files: files.length, present: files.filter((file) => file.present).length,
    tracked: files.filter((file) => file.gitState === 'tracked').length,
    untracked: files.filter((file) => file.gitState === 'untracked').length,
    tombstones: files.filter((file) => file.gitState === 'tombstone').length,
    policyErrors: policyErrors.length,
  };
}

function sourceDigestFor(files, edges, classificationRules, policyErrors) {
  return sha256(JSON.stringify(canonicalJson({
    files: files.filter((file) => !SELF_OUTPUTS.has(file.path)), edges,
    classificationRules, policyErrors, exclusions: EXCLUSIONS,
  })));
}

function resolveLocal(from, specifier, known) {
  if (!specifier.startsWith('.')) return null;
  const base = unix(path.normalize(path.join(path.dirname(from), specifier)));
  const candidates = [base, `${base}.js`, `${base}.mjs`, `${base}.json`, `${base}.py`, `${base}/index.js`];
  return candidates.find((candidate) => known.has(candidate)) || null;
}

function addEdge(edges, known, kind, from, to, evidence = null) {
  if (!known.has(from) || !known.has(to) || from === to) return;
  const key = `${kind}\0${from}\0${to}`;
  if (edges.has(key)) return;
  edges.set(key, { kind, from, to, ...(evidence ? { evidence } : {}) });
}

function textFor(buffers, file) {
  const buffer = buffers.get(file);
  if (!buffer || buffer.includes(0) || buffer.length > 4 * 1024 * 1024) return null;
  return buffer.toString('utf8');
}

function analyzeJavaScript(file, source) {
  const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  if (parsed.parseDiagnostics.length) {
    const diagnostic = parsed.parseDiagnostics[0];
    throw new Error(`JavaScript-parserfout in ${file} op offset ${diagnostic.start ?? 0}`);
  }
  const imports = [];
  const subprocessPaths = [];
  const fixtureSpecifiers = [];
  const processCalls = new Set(['execFile', 'execFileSync', 'fork', 'spawn', 'spawnSync']);
  const collectProcessPath = (node) => {
    if (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node)) {
      const literal = ts.isStringLiteralLike(node) ? node.text : node.getText(parsed);
      for (const match of literal.matchAll(/(?:^|[^A-Za-z0-9_.-])((?:src|scripts|test|eva_brain)\/[A-Za-z0-9_.${}/-]+)/gu)) {
        subprocessPaths.push(match[1].replace(/^\$\{[^}]+\}\//u, ''));
      }
      return;
    }
    ts.forEachChild(node, collectProcessPath);
  };
  const visit = (node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      && node.moduleSpecifier && ts.isStringLiteralLike(node.moduleSpecifier)) {
      imports.push(node.moduleSpecifier.text);
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword
      && node.arguments.length === 1 && ts.isStringLiteralLike(node.arguments[0])) {
      imports.push(node.arguments[0].text);
    } else if (ts.isCallExpression(node)) {
      const callName = ts.isIdentifier(node.expression) ? node.expression.text
        : ts.isPropertyAccessExpression(node.expression) ? node.expression.name.text : null;
      if (callName && processCalls.has(callName)) {
        for (const argument of node.arguments) collectProcessPath(argument);
      }
    } else if (ts.isNewExpression(node) && ts.isIdentifier(node.expression)
      && node.expression.text === 'URL' && node.arguments?.length
      && ts.isStringLiteralLike(node.arguments[0])) {
      fixtureSpecifiers.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(parsed);
  return { imports, subprocessPaths, fixtureSpecifiers };
}

function simpleGlobMatch(pattern, file) {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/gu, '\\$&')
    .replaceAll('**', '\u0000').replaceAll('*', '[^/]*').replaceAll('\u0000', '.*');
  return new RegExp(`^${escaped}$`, 'u').test(file);
}

function detectEdges(files, buffers) {
  const known = new Set(files.filter((file) => file.present).map((file) => file.path));
  const edges = new Map();
  const sourceLike = [...known].filter((file) => /\.(?:js|mjs)$/u.test(file));
  for (const file of sourceLike) {
    const text = textFor(buffers, file);
    if (text == null) continue;
    const analysis = analyzeJavaScript(file, text);
    for (const specifier of analysis.imports) {
      const target = resolveLocal(file, specifier, known);
      if (target) {
        addEdge(edges, known, 'import', file, target, specifier);
        if (file.startsWith('test/') && /(?:fixture|composition|test-server)/u.test(target)) {
          addEdge(edges, known, 'test-fixture', file, target, specifier);
        }
      }
    }
    for (const processPath of analysis.subprocessPaths) {
      if (known.has(processPath)) addEdge(edges, known, 'subprocess', file, processPath, processPath);
    }
    if (file.startsWith('test/')) {
      for (const specifier of analysis.fixtureSpecifiers) {
        const target = resolveLocal(file, specifier, known);
        if (target && /(?:fixture|composition|test-server)/u.test(target)) {
          addEdge(edges, known, 'test-fixture', file, target, specifier);
        }
      }
    }
  }
  if (known.has('package.json')) {
    try {
      const pkg = JSON.parse(textFor(buffers, 'package.json'));
      const scripts = pkg.scripts || {};
      const gateScripts = new Set(['test', 'coverage'].filter((name) => typeof scripts[name] === 'string'));
      for (let changed = true; changed;) {
        changed = false;
        for (const name of [...gateScripts]) {
          for (const match of scripts[name].matchAll(/\bnpm\s+run\s+([A-Za-z0-9:_-]+)/gu)) {
            if (typeof scripts[match[1]] === 'string' && !gateScripts.has(match[1])) {
              gateScripts.add(match[1]);
              changed = true;
            }
          }
        }
      }
      for (const [name, command] of Object.entries(scripts)) {
        if (typeof command !== 'string') continue;
        for (const match of command.matchAll(/(?:^|[\s"'=])(?:\.\/)?((?:src|scripts|test|eva_brain)\/[A-Za-z0-9_.*/-]+)/gu)) {
          const token = match[1];
          if (token.includes('*')) {
            for (const target of known) {
              if (simpleGlobMatch(token, target)) {
                addEdge(edges, known, gateScripts.has(name) ? 'package-test-glob' : 'package-glob',
                  'package.json', target, name);
              }
            }
          } else if (known.has(token)) {
            addEdge(edges, known, 'package-script', 'package.json', token, name);
          }
        }
      }
    } catch (error) {
      throw new Error(`package.json is niet canoniek leesbaar: ${error.message}`);
    }
  }
  for (const file of [...known].filter((candidate) => candidate.startsWith('deploy/'))) {
    const text = textFor(buffers, file);
    if (text == null) continue;
    for (const match of text.matchAll(/^ExecStart(?:Pre|Post)?=(?:\+)?([^\n]+)$/gmu)) {
      for (const candidate of [...known].filter((item) => /^(?:deploy|eva_brain|scripts|src)\//u.test(item))) {
        const basename = path.basename(candidate);
        const pythonModule = candidate.endsWith('.py')
          ? candidate.slice(0, -3).replaceAll('/', '.') : null;
        if (match[1].includes(candidate) || match[1].includes(`/${candidate}`)
          || (pythonModule && match[1].includes(pythonModule))
          || (match[1].includes(basename)
            && [...known].filter((item) => path.basename(item) === basename).length === 1)) {
          addEdge(edges, known, 'systemd-exec', file, candidate, match[0]);
        }
      }
    }
    for (const match of text.matchAll(/^(?:OnFailure|Unit)=([^\s]+)$/gmu)) {
      addEdge(edges, known, 'systemd-trigger', file, `deploy/${path.basename(match[1])}`, match[0]);
    }
    if (/install-eva-runtime-maintainer\.sh$/u.test(file)) {
      const copyCommands = text.split('\n').filter((line) => /^\s*(?:cp|install)\b/u.test(line));
      for (const candidate of [...known].filter((item) => item.startsWith('deploy/') || item.startsWith('scripts/'))) {
        if (candidate !== file && copyCommands.some((line) => line.includes(path.basename(candidate)))) {
          addEdge(edges, known, 'installer-copy', file, candidate, path.basename(candidate));
        }
      }
    }
  }
  for (const file of [...known].filter((candidate) => candidate.endsWith('.html'))) {
    const text = textFor(buffers, file) || '';
    for (const match of text.matchAll(/(?:src|href)=['"]\/([^'"?#]+)['"]/gu)) {
      addEdge(edges, known, 'static-load', file, unix(path.join(path.dirname(file), match[1])), match[0]);
    }
  }
  for (const file of sourceLike.filter((candidate) => candidate.startsWith('src/web/public/'))) {
    const text = textFor(buffers, file) || '';
    for (const match of text.matchAll(/\.load\(\s*['"]\/([^'"?#]+)['"]/gu)) {
      addEdge(edges, known, 'static-load', file, unix(path.join(path.dirname(file), match[1])), match[0]);
    }
  }
  if (known.has('src/web/server.js') && /express\.static/u.test(textFor(buffers, 'src/web/server.js') || '')) {
    for (const target of [...known].filter((file) => file.startsWith('src/web/public/'))) {
      addEdge(edges, known, 'static-serve', 'src/web/server.js', target, 'express.static(public)');
    }
  }
  return [...edges.values()].sort((left, right) => (
    left.kind.localeCompare(right.kind) || left.from.localeCompare(right.from) || left.to.localeCompare(right.to)
  ));
}

function gitUniverse(root) {
  const tracked = new Set(gitList(root, ['ls-files', '-z']));
  validateGitignoreContract(root, tracked);
  const untracked = new Set(gitList(root, [
    '-c', 'core.excludesFile=/dev/null',
    'ls-files', '--others', '--exclude-from=.gitignore', '-z',
  ]));
  for (const output of SELF_OUTPUTS) if (!tracked.has(output)) untracked.add(output);
  const paths = [...new Set([...tracked, ...untracked])].sort();
  validateGitignoreContract(root, paths);
  return { tracked, untracked, paths };
}

function ordinaryRepositoryFile(root, canonicalRoot, relative) {
  const absoluteRoot = path.resolve(root);
  const absolute = path.resolve(absoluteRoot, relative);
  if (!containedPath(absoluteRoot, absolute)) {
    throw new Error(`REPOSITORY_PATH_OUTSIDE_ROOT:${relative}`);
  }
  let stat = null;
  try {
    stat = fs.lstatSync(absolute);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  if (!stat) return { absolute, stat: null };
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error(`niet-regulier repositorypad: ${relative}`);
  }
  if (stat.size > MAX_FILE_BYTES) {
    throw new Error(`repositorybestand overschrijdt bytegrens: ${relative}`);
  }
  const canonical = fs.realpathSync(absolute);
  if (!containedPath(canonicalRoot, canonical)) {
    throw new Error(`REPOSITORY_PATH_OUTSIDE_ROOT:${relative}`);
  }
  return { absolute, stat };
}

function ordinaryOutputDestination(root, canonicalRoot, relative) {
  const absoluteRoot = path.resolve(root);
  const absolute = path.resolve(absoluteRoot, relative);
  if (!containedPath(absoluteRoot, absolute)) {
    throw new Error(`ORDINARY_OUTPUT_OUTSIDE_ROOT:${relative}`);
  }
  const parent = path.dirname(absolute);
  let existingParent = parent;
  for (;;) {
    try {
      fs.lstatSync(existingParent);
      break;
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
      const next = path.dirname(existingParent);
      if (next === existingParent) throw error;
      existingParent = next;
    }
  }
  const canonicalExistingParent = fs.realpathSync(existingParent);
  const canonicalParent = path.resolve(canonicalExistingParent,
    path.relative(existingParent, parent));
  if (!containedPath(canonicalRoot, canonicalExistingParent)
    || !containedPath(canonicalRoot, canonicalParent)) {
    throw new Error(`ORDINARY_OUTPUT_OUTSIDE_ROOT:${relative}`);
  }
  if (!fs.statSync(existingParent).isDirectory()) {
    throw new Error(`ORDINARY_OUTPUT_PARENT_NOT_DIRECTORY:${relative}`);
  }

  let stat = null;
  try {
    stat = fs.lstatSync(absolute);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  if (!stat) return { absolute, present: false };
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error(`ORDINARY_OUTPUT_NOT_REGULAR:${relative}`);
  }
  if (stat.size > MAX_FILE_BYTES) {
    throw new Error(`ORDINARY_OUTPUT_TOO_LARGE:${relative}`);
  }
  const canonical = fs.realpathSync(absolute);
  if (!containedPath(canonicalRoot, canonical)) {
    throw new Error(`ORDINARY_OUTPUT_OUTSIDE_ROOT:${relative}`);
  }
  return { absolute, present: true };
}

function ordinaryOutputDestinations(root, canonicalRoot) {
  return new Map([...SELF_OUTPUTS].map((relative) => [
    relative, ordinaryOutputDestination(root, canonicalRoot, relative),
  ]));
}

function buildManifest(root) {
  const canonicalRoot = fs.realpathSync(root);
  ordinaryOutputDestinations(root, canonicalRoot);
  const universe = gitUniverse(root);
  const buffers = new Map();
  const ruleCounts = new Map(ROLE_RULES.map((rule) => [rule.id, 0]));
  const files = [];
  for (const relative of universe.paths) {
    if (sensitivePath(relative)) throw new Error(`TRACKED_SENSITIVE_PATH: verboden repositorypad ${relative}`);
    const validated = ordinaryRepositoryFile(root, canonicalRoot, relative);
    const { absolute, stat } = validated;
    const matching = ROLE_RULES.filter((rule) => rule.test(relative));
    if (matching.length === 0) throw new Error(`ongeclassificeerd repositorypad: ${relative}`);
    if (matching.length !== 1) throw new Error(`AMBIGUOUS_ROLE: ${relative} matcht ${matching.map((rule) => rule.id).join(', ')}`);
    const rule = matching[0];
    ruleCounts.set(rule.id, ruleCounts.get(rule.id) + 1);
    const gitState = universe.tracked.has(relative) ? (stat ? 'tracked' : 'tombstone') : 'untracked';
    const self = SELF_OUTPUTS.has(relative);
    const dispositionValue = disposition(relative, rule.role, Boolean(stat) || self);
    let buffer = null;
    if (stat) {
      buffer = fs.readFileSync(absolute);
      buffers.set(relative, buffer);
    }
    const critical = releaseCritical(relative, rule.role, dispositionValue.decision);
    const releaseStatus = dispositionValue.decision !== 'keep'
      ? 'blocked-unresolved-disposition'
      : gitState === 'tombstone' ? 'blocked-tombstone'
        : gitState === 'untracked' ? 'blocked-untracked'
        : critical ? 'included-tracked-critical' : 'included-tracked-noncritical';
    files.push({
      path: relative, gitState, present: Boolean(stat) || self,
      bytes: self ? 0 : stat ? buffer.length : null,
      contentSha256: self ? sha256(`${SCHEMA}:${relative}:${SELF_HASH_MODE}`) : stat ? sha256(buffer) : null,
      hashMode: self ? SELF_HASH_MODE : stat ? 'exact-bytes' : 'absent',
      role: rule.role, roleRuleId: rule.id, matchingRuleIds: [rule.id],
      ...roleContract(rule.role, relative),
      decision: dispositionValue.decision, decisionReason: dispositionValue.reason,
      consumers: [], entrypointEvidence: [],
      release: {
        includedInTrackedRelease: gitState === 'tracked', runtimeCritical: critical,
        status: releaseStatus,
      },
    });
  }
  for (const rule of ROLE_RULES) if (ruleCounts.get(rule.id) === 0) throw new Error(`dode classificatieregel: ${rule.id}`);
  const edges = detectEdges(files, buffers);
  const byPath = new Map(files.map((file) => [file.path, file]));
  for (const edge of edges) byPath.get(edge.to)?.consumers.push({ kind: edge.kind, from: edge.from });
  for (const file of files) {
    file.consumers.sort((left, right) => left.kind.localeCompare(right.kind) || left.from.localeCompare(right.from));
    file.entrypointEvidence = entrypointEvidenceFor(file.path, file.role, edges);
  }
  const policyErrors = policyErrorsFor(files, edges);
  const classificationRules = ROLE_RULES.map((rule) => ({ id: rule.id, role: rule.role, matchCount: ruleCounts.get(rule.id) }));
  const manifest = {
    schema: SCHEMA,
    scope: {
      sources: [
        'git ls-files -z',
        'git -c core.excludesFile=/dev/null ls-files --others --exclude-from=.gitignore -z',
      ],
      generatedOutputs: [...SELF_OUTPUTS],
      claim: 'all tracked, tombstone and nonignored untracked repository paths',
    },
    exclusions: EXCLUSIONS, summary: {}, classificationRules, policyErrors, edges, files, sourceDigest: '',
  };
  manifest.summary = summaryFor(files, policyErrors);
  manifest.sourceDigest = sourceDigestFor(files, edges, classificationRules, policyErrors);
  return manifest;
}

function markdown(manifest) {
  const lines = [
    '# Eva repository-inventaris', '',
    'Deze kaart is mechanisch gegenereerd. Zij staat naast de semantische runtimegraaf en',
    'classificeert ieder gevolgd, verwijderd of niet-genegeerd nieuw repositorypad.', '',
    `Schema: \`${manifest.schema}\`. Brondigest: \`${manifest.sourceDigest}\`.`, '',
    '## Samenvatting', '',
    '| Bestanden | Aanwezig | Tracked | Untracked | Tombstones | Policyfouten |',
    '| ---: | ---: | ---: | ---: | ---: | ---: |',
    `| ${manifest.summary.files} | ${manifest.summary.present} | ${manifest.summary.tracked} | ${manifest.summary.untracked} | ${manifest.summary.tombstones} | ${manifest.summary.policyErrors} |`, '',
    '## Policyfouten', '',
  ];
  if (manifest.policyErrors.length === 0) lines.push('Geen.');
  else for (const error of manifest.policyErrors) lines.push(`- \`${error.code}\`: \`${error.path}\``);
  lines.push('', '## Actielijst', '', '| Pad | Besluit | Reden |', '| --- | --- | --- |');
  for (const file of manifest.files.filter((item) => item.decision !== 'keep')) {
    lines.push(`| \`${file.path}\` | \`${file.decision}\` | ${file.decisionReason} |`);
  }
  lines.push('', '## Rollen', '', '| Regel | Rol | Matches |', '| --- | --- | ---: |');
  for (const rule of manifest.classificationRules) lines.push(`| \`${rule.id}\` | \`${rule.role}\` | ${rule.matchCount} |`);
  lines.push('', '## Bestanden', '', '| Pad | Git | Rol | Release | Besluit | Bytes |', '| --- | --- | --- | --- | --- | ---: |');
  for (const file of manifest.files) lines.push(`| \`${file.path}\` | ${file.gitState} | ${file.role} | ${file.release.status} | ${file.decision} | ${file.bytes ?? ''} |`);
  lines.push('', '## Gedetecteerde randen', '', '| Type | Van | Naar |', '| --- | --- | --- |');
  for (const edge of manifest.edges) lines.push(`| ${edge.kind} | \`${edge.from}\` | \`${edge.to}\` |`);
  lines.push('', 'De randdetectie is begrensd tot letterlijke lokale imports, package-scripts en testglobs,',
    'spawnpaden, systemd Exec/Unit/OnFailure, installerbestandsnamen en browserloads.',
    'Afwezigheid van een heuristische rand is geen bewijs dat een extern systeem geen caller heeft.', '');
  return `${lines.join('\n')}\n`;
}

function rendered(manifest) {
  let json = '';
  let md = '';
  for (let attempt = 0; attempt < 20; attempt += 1) {
    json = `${JSON.stringify(manifest, null, 2)}\n`;
    md = markdown(manifest);
    const sizes = new Map([[JSON_OUTPUT, Buffer.byteLength(json)], [MARKDOWN_OUTPUT, Buffer.byteLength(md)]]);
    let changed = false;
    for (const file of manifest.files) {
      if (!SELF_OUTPUTS.has(file.path)) continue;
      const next = sizes.get(file.path);
      if (file.bytes !== next) { file.bytes = next; changed = true; }
    }
    if (!changed) return { json, markdown: md };
  }
  throw new Error('self-reference bytefixedpoint convergeerde niet');
}

function writeAtomic(root, canonicalRoot, relative, contents) {
  let destination = ordinaryOutputDestination(root, canonicalRoot, relative);
  fs.mkdirSync(path.dirname(destination.absolute), { recursive: true });
  destination = ordinaryOutputDestination(root, canonicalRoot, relative);
  const target = destination.absolute;
  const temporary = `${target}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, contents, { flag: 'wx' });
  fs.renameSync(temporary, target);
}

function walkSealed(root, directory = root, result = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    const relative = unix(path.relative(root, absolute));
    if (relative === '.git' || relative.startsWith('.git/')) continue;
    if (sensitivePath(relative) || relative === 'dist' || relative.startsWith('dist/')
      || relative === '.nyc_output' || relative.startsWith('.nyc_output/')) {
      throw new Error(`sealed release bevat uitgesloten pad: ${relative}`);
    }
    if (entry.isDirectory()) walkSealed(root, absolute, result);
    else if (entry.isFile()) result.push(relative);
    else throw new Error(`sealed release bevat niet-regulier pad: ${relative}`);
  }
  return result;
}

function containedPath(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!path.isAbsolute(relative)
    && relative !== '..' && !relative.startsWith(`..${path.sep}`));
}

function validateSealedOutput(root, canonicalRoot, relative) {
  const absolute = path.resolve(root, relative);
  if (!containedPath(path.resolve(root), absolute)) {
    throw new Error(`SEALED_OUTPUT_OUTSIDE_ROOT:${relative}`);
  }
  let stat;
  try {
    stat = fs.lstatSync(absolute);
  } catch (error) {
    if (error?.code === 'ENOENT') throw new Error(`SEALED_OUTPUT_MISSING:${relative}`);
    throw error;
  }
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error(`SEALED_OUTPUT_NOT_REGULAR:${relative}`);
  }
  if (stat.size > MAX_FILE_BYTES) throw new Error(`SEALED_OUTPUT_TOO_LARGE:${relative}`);
  const canonical = fs.realpathSync(absolute);
  if (!containedPath(canonicalRoot, canonical)) {
    throw new Error(`SEALED_OUTPUT_OUTSIDE_ROOT:${relative}`);
  }
  return absolute;
}

function sealedCheck(root) {
  validateGitignoreContract(root);
  const canonicalRoot = fs.realpathSync(root);
  const validatedOutputs = new Map([...SELF_OUTPUTS].map((relative) => [
    relative, validateSealedOutput(root, canonicalRoot, relative),
  ]));
  const jsonPath = validatedOutputs.get(JSON_OUTPUT);
  const markdownPath = validatedOutputs.get(MARKDOWN_OUTPUT);
  const actual = walkSealed(root).sort();
  validateGitignoreContract(root, actual);
  const jsonText = fs.readFileSync(jsonPath, 'utf8');
  const manifest = JSON.parse(jsonText);
  if (manifest.schema !== SCHEMA || jsonText !== `${JSON.stringify(manifest, null, 2)}\n`) throw new Error('sealed release-inventaris is niet canoniek');
  const declaredPaths = manifest.files?.map((file) => file.path);
  if (!Array.isArray(declaredPaths) || new Set(declaredPaths).size !== declaredPaths.length
    || JSON.stringify(declaredPaths) !== JSON.stringify([...declaredPaths].sort())
    || JSON.stringify(actual) !== JSON.stringify(declaredPaths)) {
    throw new Error('sealed release-bestandsuniversum wijkt af van de inventaris');
  }
  const buffers = new Map();
  const baseRecords = new Map();
  const ruleCounts = new Map(ROLE_RULES.map((rule) => [rule.id, 0]));
  for (const file of manifest.files) {
    if (sensitivePath(file.path)) throw new Error(`sealed release bevat verboden manifestpad: ${file.path}`);
    const absolute = path.join(root, file.path);
    const stat = fs.lstatSync(absolute);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`sealed release bevat niet-regulier pad: ${file.path}`);
    if (stat.size > MAX_FILE_BYTES) throw new Error(`sealed releasebestand overschrijdt bytegrens: ${file.path}`);
    const buffer = fs.readFileSync(absolute);
    buffers.set(file.path, buffer);
    const matching = ROLE_RULES.filter((rule) => rule.test(file.path));
    if (matching.length !== 1) throw new Error(`sealed releasepad heeft geen unieke rol: ${file.path}`);
    const rule = matching[0];
    ruleCounts.set(rule.id, ruleCounts.get(rule.id) + 1);
    const dispositionValue = disposition(file.path, rule.role, true);
    const critical = releaseCritical(file.path, rule.role, dispositionValue.decision);
    baseRecords.set(file.path, {
      path: file.path, gitState: 'tracked', present: true, bytes: buffer.length,
      contentSha256: SELF_OUTPUTS.has(file.path)
        ? sha256(`${SCHEMA}:${file.path}:${SELF_HASH_MODE}`) : sha256(buffer),
      hashMode: SELF_OUTPUTS.has(file.path) ? SELF_HASH_MODE : 'exact-bytes',
      role: rule.role, roleRuleId: rule.id, matchingRuleIds: [rule.id],
      ...roleContract(rule.role, file.path),
      decision: dispositionValue.decision, decisionReason: dispositionValue.reason,
      consumers: [], entrypointEvidence: [],
      release: {
        includedInTrackedRelease: true, runtimeCritical: critical,
        status: dispositionValue.decision !== 'keep'
          ? 'blocked-unresolved-disposition'
          : critical ? 'included-tracked-critical' : 'included-tracked-noncritical',
      },
    });
  }
  const edges = detectEdges([...baseRecords.values()], buffers);
  const expectedFiles = manifest.files.map((file) => {
    const expected = baseRecords.get(file.path);
    expected.consumers = edges.filter((edge) => edge.to === file.path)
      .map((edge) => ({ kind: edge.kind, from: edge.from }))
      .sort((left, right) => left.kind.localeCompare(right.kind) || left.from.localeCompare(right.from));
    expected.entrypointEvidence = entrypointEvidenceFor(file.path, expected.role, edges);
    return expected;
  });
  const classificationRules = ROLE_RULES.map((rule) => ({
    id: rule.id, role: rule.role, matchCount: ruleCounts.get(rule.id),
  }));
  if (classificationRules.some((rule) => rule.matchCount === 0)) throw new Error('sealed release heeft een dode classificatieregel');
  const policyErrors = policyErrorsFor(expectedFiles, edges);
  const expectedManifest = {
    schema: SCHEMA,
    scope: {
      sources: [
        'git ls-files -z',
        'git -c core.excludesFile=/dev/null ls-files --others --exclude-from=.gitignore -z',
      ],
      generatedOutputs: [...SELF_OUTPUTS],
      claim: 'all tracked, tombstone and nonignored untracked repository paths',
    },
    exclusions: EXCLUSIONS,
    summary: summaryFor(expectedFiles, policyErrors),
    classificationRules, policyErrors, edges, files: expectedFiles, sourceDigest: '',
  };
  expectedManifest.sourceDigest = sourceDigestFor(expectedFiles, edges, classificationRules, policyErrors);
  if (JSON.stringify(canonicalJson(manifest)) !== JSON.stringify(canonicalJson(expectedManifest))) {
    throw new Error('sealed release-inventarissemantiek wijkt af van de actuele bestanden');
  }
  const output = rendered(structuredClone(expectedManifest));
  if (jsonText !== output.json || fs.readFileSync(markdownPath, 'utf8') !== output.markdown) {
    throw new Error('sealed release-output is niet canoniek herleidbaar');
  }
  if (policyErrors.length) throw new Error(`sealed release bevat policyfouten: ${policyErrors.map((item) => item.code).join(',')}`);
  process.stdout.write(`Repository-inventaris sealed release groen: ${manifest.files.length} bestanden.\n`);
}

function parseArgs() {
  const args = process.argv.slice(2);
  const rootIndex = args.indexOf('--root');
  const root = rootIndex >= 0 && args[rootIndex + 1]
    ? path.resolve(args[rootIndex + 1]) : path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  return { root, check: args.includes('--check') };
}

function main() {
  const { root, check } = parseArgs();
  if (check && !fs.existsSync(path.join(root, '.git'))) { sealedCheck(root); return; }
  const manifest = buildManifest(root);
  const output = rendered(manifest);
  const canonicalRoot = fs.realpathSync(root);
  const destinations = ordinaryOutputDestinations(root, canonicalRoot);
  const jsonDestination = destinations.get(JSON_OUTPUT);
  const markdownDestination = destinations.get(MARKDOWN_OUTPUT);
  if (check) {
    const currentJson = jsonDestination.present
      ? fs.readFileSync(jsonDestination.absolute, 'utf8') : '';
    const currentMarkdown = markdownDestination.present
      ? fs.readFileSync(markdownDestination.absolute, 'utf8') : '';
    if (currentJson !== output.json || currentMarkdown !== output.markdown) throw new Error('repository-inventaris is verouderd of bevat drift; voer npm run architecture uit');
    if (manifest.policyErrors.length) throw new Error(manifest.policyErrors.map((item) => `${item.code}:${item.path}`).join('\n'));
    process.stdout.write(`Repository-inventaris groen: ${manifest.files.length} bestanden.\n`);
    return;
  }
  writeAtomic(root, canonicalRoot, JSON_OUTPUT, output.json);
  writeAtomic(root, canonicalRoot, MARKDOWN_OUTPUT, output.markdown);
  process.stdout.write(`Repository-inventaris gegenereerd: ${manifest.files.length} bestanden, ${manifest.policyErrors.length} policyfouten.\n`);
}

try { main(); } catch (error) {
  process.stderr.write(`Repository-inventaris faalde gesloten: ${error.message}\n`);
  process.exitCode = 1;
}
