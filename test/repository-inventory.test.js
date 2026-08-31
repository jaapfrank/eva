import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const generator = path.join(projectRoot, 'scripts', 'generate-repository-inventory.mjs');
const projectGitignore = fs.readFileSync(path.join(projectRoot, '.gitignore'), 'utf8');
const MAX_FILE_BYTES = 128 * 1024 * 1024;
const ACTIVE_LEDGER_EVIDENCE = Object.freeze([
  'scripts/execution-ledger-production14-recovery.mjs',
  'scripts/execution-ledger-production14-recovery.test.mjs',
  'scripts/execution-ledger-quiescent-snapshot.mjs',
  'scripts/execution-ledger-quiescent-snapshot.test.mjs',
]);
const EXPECTED_GITIGNORE_PATTERNS = Object.freeze([
  'node_modules/', 'dist/', 'coverage/', '.nyc_output/', 'data/',
  '*.db', '*.db-*', '*.sqlite', '*.sqlite-*', '*.log', '*.pid',
  '__pycache__/', '*.py[cod]', '.ruff_cache/', '.pytest_cache/', '.venv/', 'venv/',
  '.env', '.env.*', '*.env', '*.env.*',
  '!.env.example', '!**/.env.example',
  '!eva2/deploy/living-core/runtime.env.example',
  '!eva2/deploy/living-core/hindsight.env.example',
  'eva2/deploy/living-core/secrets/', 'eva2/deploy/living-core/secrets/**',
  '*.key', '*.pem', '*.ppk', '*.p12', '*.pfx', '*.jks', '*.keystore',
  'id_rsa', 'id_rsa.*', '*_rsa', '*_rsa.*', '*_ed25519', '*_ed25519.*',
  '*.swp', '*.swo', '*~', '*:Zone.Identifier', '.DS_Store', 'Thumbs.db',
  '/scratch_*', '/_*.cjs',
  '/eva_brain/replay/LOCK_*.md', '/eva_brain/replay/out_*.txt',
  '/eva2/experiments/distroless-core-v1/wheelhouse/*.whl',
  '/eva2/experiments/distroless-hindsight-api-v1/offline-artifacts/*.apk',
  '/eva2/experiments/distroless-hindsight-api-v1/offline-artifacts/*.tar',
  '/eva2/experiments/distroless-hindsight-api-v1/wheelhouse/*.whl',
  '/eva2/build/',
]);
const EXPECTED_EXCLUSIONS = Object.freeze(
  EXPECTED_GITIGNORE_PATTERNS.filter((pattern) => !pattern.startsWith('!')),
);
const UNRESOLVED_DISPOSITION_CODES = Object.freeze([
  'UNRESOLVED_DISPOSITION_ARCHIVE',
  'UNRESOLVED_DISPOSITION_DELETE',
  'UNRESOLVED_DISPOSITION_INTEGRATE',
  'UNRESOLVED_DISPOSITION_ISOLATE',
  'UNRESOLVED_DISPOSITION_QUARANTINE',
]);

function write(root, file, contents = `${file}\n`) {
  const target = path.join(root, file);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, contents);
}

function childEnvironment(overrides = {}) {
  return { ...process.env, ...overrides };
}

function rawGit(root, args, environment = {}) {
  return spawnSync('/usr/bin/git', ['-C', root, ...args], {
    encoding: 'utf8', env: childEnvironment(environment),
  });
}

function git(root, args) {
  const result = rawGit(root, args);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return result.stdout;
}

function run(root, args = [], environment = {}) {
  return spawnSync(process.execPath, [generator, '--root', root, ...args], {
    cwd: root,
    encoding: 'utf8',
    env: childEnvironment(environment),
  });
}

function gitignorePatterns(contents) {
  return contents.split(/\r?\n/u).filter((line) => line && !line.startsWith('#'));
}

function gitignoreDrifts(contents) {
  return [
    ['toevoeging', `${contents}src/contract-hidden.js\n`],
    ['verwijdering', contents.replace('*.ppk\n', '')],
    ['wijziging', contents.replace('*.p12\n', '*.p13\n')],
    ['rootnegatie verwijderd', contents.replace('!.env.example\n', '')],
    ['geneste negatie verwijderd', contents.replace('!**/.env.example\n', '')],
  ];
}

function createRepository(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-repository-inventory-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  git(root, ['init', '--quiet']);
  git(root, ['config', 'user.email', 'inventory@example.invalid']);
  git(root, ['config', 'user.name', 'Inventory Test']);

  write(root, '.gitignore', projectGitignore);
  write(root, 'package.json', `${JSON.stringify({
    type: 'module',
    main: 'src/index.js',
    scripts: {
      start: 'node src/index.js',
      test: './scripts/run-isolated-tests-bootstrap test/*.test.js scripts/*.test.mjs && python -m pytest eva_brain/test_*.py',
      'docs:scan': 'node src/index.js test/nested/*.test.js',
    },
  }, null, 2)}\n`);
  write(root, 'benchmark/catalog.json', '{"schema":1}\n');
  write(root, 'deploy/eva-runtime-maintainer.mjs', "import '../src/index.js';\n");
  write(root, 'deploy/eva-runtime-maintainer.service', [
    '[Service]',
    'OnFailure=eva-runtime-maintainer-recover.service',
    'ExecStart=/usr/bin/node /usr/local/libexec/eva-runtime-maintainer.mjs',
    '',
  ].join('\n'));
  write(root, 'deploy/eva-runtime-maintainer-recover.service', [
    '[Service]',
    'ExecStart=/usr/bin/node /usr/local/libexec/eva-runtime-maintainer.mjs --recover',
    '',
  ].join('\n'));
  write(root, 'deploy/eva-runtime-maintainer.path', [
    '[Path]',
    'PathChanged=/var/lib/eva/inbox',
    'Unit=eva-runtime-maintainer.service',
    '',
  ].join('\n'));
  write(root, 'deploy/eva-brain.service', [
    '[Service]',
    'ExecStart=/opt/eva/venv/bin/python -m uvicorn eva_brain.service:app',
    '',
  ].join('\n'));
  write(root, 'deploy/install-eva-runtime-maintainer.sh', [
    '#!/bin/sh',
    'SCRIPT_DIR=$(dirname "$0")',
    'git ls-files scripts/run-isolated-tests.test.mjs',
    'install -m 0550 "$SCRIPT_DIR/eva-runtime-maintainer.mjs" /usr/local/libexec/eva-runtime-maintainer.mjs',
    '',
  ].join('\n'));
  write(root, 'design/contract.json', '{}\n');
  write(root, 'docs/design.md');
  write(root, 'docs/eva-runtime-map.md', 'historische runtimekaart\n');
  write(root, 'docs/live-acceptance-2026-08-04.md', 'historisch live-acceptatiebewijs\n');
  write(root, 'eva_brain/service.py', 'def health(): return True\n');
  write(root, 'eva_brain/test_service.py', 'def test_health(): assert True\n');
  write(root, 'eva_brain/ingest.py', 'print("offline")\n');
  write(root, 'eva2/README.md', 'Eva2 fixture\n');
  write(root, 'eva2/deploy/Dockerfile', 'FROM scratch\n');
  write(root, 'eva2/experiments/README.md', 'experiment\n');
  write(root, 'eva2/release/policy.json', '{}\n');
  write(root, 'eva2/src/eva/__init__.py', '"""fixture"""\n');
  write(root, 'eva2/tests/test_sample.py', 'def test_sample(): assert True\n');
  write(root, 'eva2/tools/check.py', 'print("check")\n');
  write(root, 'experiments/README.md', 'experiment fixture\n');
  write(root, 'native/workspace_writer_broker.c', 'int main(void) { return 0; }\n');
  for (const file of ACTIVE_LEDGER_EVIDENCE) write(root, file, 'export {};\n');
  write(root, 'scripts/generate-runtime-contract-map.mjs', 'export {};\n');
  write(root, 'scripts/run-isolated-tests-bootstrap', 'bootstrap\n');
  write(root, 'scripts/run-isolated-tests.mjs', 'export {};\n');
  write(root, 'scripts/run-isolated-tests.test.mjs', 'export {};\n');
  write(root, 'scripts/eva-backup.mjs', 'export {};\n');
  write(root, 'scripts/sanitize-secret-history.mjs', 'export {};\n');
  write(root, 'src/index.js', [
    "import './worker.js';",
    "import './new.js';",
    "spawn(process.execPath, [`${config.root}/scripts/eva-backup.mjs`]);",
    '',
  ].join('\n'));
  write(root, 'src/worker.js', 'export const worker = true;\n');
  write(root, 'src/mind/artifactcollections.js', 'export {};\n');
  write(root, 'src/mind/materiallifecycle.js', 'export {};\n');
  write(root, 'src/mind/workcompletion.js', 'export {};\n');
  write(root, 'src/mind/voiceingress.js', 'export {};\n');
  write(root, 'src/web/public/index.html', [
    '<link rel="stylesheet" href="/style.css">',
    '<script type="module" src="/app.js"></script>',
    '',
  ].join('\n'));
  write(root, 'src/web/public/app.js', "new Loader().load('/eva.glb');\n");
  write(root, 'src/web/public/style.css', 'body { color: black; }\n');
  write(root, 'src/web/public/eva.glb', Buffer.from([0x67, 0x6c, 0x54, 0x46, 0, 1, 2, 3]));
  write(root, 'src/web/server.js', "app.use(express.static(path.join(__dirname, 'public')));\n");
  write(root, 'test/sample-fixture.mjs', 'export const value = 1;\n');
  write(root, 'test/sample.test.js', [
    "import './sample-fixture.mjs';",
    "const fixture = new URL('./sample-fixture.mjs', import.meta.url);",
    "const inertFixtureText = \"import '../src/index.js';\";",
    "const inertSpawnText = \"spawn('scripts/eva-backup.mjs')\";",
    '',
  ].join('\n'));
  write(root, 'transition/README.md', 'transition fixture\n');
  write(root, 'src/retired.js', 'export const retired = true;\n');

  git(root, ['add', '.']);
  git(root, ['commit', '--quiet', '-m', 'fixture']);
  fs.unlinkSync(path.join(root, 'src/retired.js'));
  write(root, 'src/new.js', 'export const fresh = "vers"; // café\n');
  write(root, 'docs/untracked-note.md', 'untracked documentation\n');
  write(root, '.env.example', 'SAFE_ROOT_TEMPLATE=1\n');
  write(root, 'docs/templates/.env.example', 'SAFE_NESTED_TEMPLATE=1\n');

  write(root, '.env', 'TOP_SECRET=must-not-be-read\n');
  write(root, 'data/private/sentinel.txt', 'must-not-be-read\n');
  write(root, 'node_modules/private/sentinel.txt', 'must-not-be-read\n');
  write(root, 'coverage/private/sentinel.txt', 'must-not-be-read\n');
  write(root, 'eva_brain/__pycache__/service.pyc', Buffer.from([0, 1, 2, 3]));
  return root;
}

function record(manifest, file) {
  const found = manifest.files.find((candidate) => candidate.path === file);
  assert.ok(found, `inventaris mist ${file}`);
  return found;
}

function hasEdge(manifest, kind, from, to) {
  return manifest.edges.some((edge) => edge.kind === kind
    && edge.from === from && edge.to === to);
}

function fullyTrackedDispositionRepository(t) {
  const root = createRepository(t);
  git(root, ['add', '-A']);
  assert.equal(run(root).status, 0);
  git(root, ['add',
    'docs/eva-repository-inventory.generated.json',
    'docs/eva-repository-inventory.generated.md']);
  assert.equal(run(root).status, 0);
  git(root, ['add',
    'docs/eva-repository-inventory.generated.json',
    'docs/eva-repository-inventory.generated.md']);
  assert.equal(run(root).status, 0);
  return root;
}

function holdIgnoredFixturePaths(root) {
  const held = [
    '.env', 'data', 'node_modules', 'coverage', 'eva_brain/__pycache__',
  ].filter((relative) => fs.existsSync(path.join(root, relative))).map((relative, index) => ({
    original: path.join(root, relative), held: `${root}.excluded-${index}`,
  }));
  for (const item of held) fs.renameSync(item.original, item.held);
  return () => {
    for (const item of held) fs.renameSync(item.held, item.original);
  };
}

function resolveDispositionFixture(root) {
  for (const file of [
    'docs/eva-runtime-map.md',
    'docs/live-acceptance-2026-08-04.md',
    'native/workspace_writer_broker.c',
    'scripts/sanitize-secret-history.mjs',
    'src/mind/artifactcollections.js',
    'src/mind/materiallifecycle.js',
    'src/mind/workcompletion.js',
    'src/mind/voiceingress.js',
  ]) fs.rmSync(path.join(root, file), { force: true });
  write(root, 'native/resolved.c', 'int resolved(void) { return 1; }\n');
  git(root, ['add', '-A']);
}

function fullyTrackedResolvedRepository(t) {
  const root = createRepository(t);
  resolveDispositionFixture(root);
  assert.equal(run(root).status, 0);
  for (let pass = 0; pass < 2; pass += 1) {
    git(root, ['add',
      'docs/eva-repository-inventory.generated.json',
      'docs/eva-repository-inventory.generated.md']);
    assert.equal(run(root).status, 0);
  }
  return root;
}

test('project-.gitignore-contract is exact en drift faalt gewoon en sealed gesloten', (t) => {
  assert.deepEqual(gitignorePatterns(projectGitignore), EXPECTED_GITIGNORE_PATTERNS);
  assert.equal(EXPECTED_EXCLUSIONS.length, 51);
  assert.deepEqual(EXPECTED_GITIGNORE_PATTERNS.filter((pattern) => pattern.startsWith('!')), [
    '!.env.example', '!**/.env.example',
    '!eva2/deploy/living-core/runtime.env.example',
    '!eva2/deploy/living-core/hindsight.env.example',
  ]);

  const ordinaryRoot = createRepository(t);
  for (const [label, drift] of gitignoreDrifts(projectGitignore)) {
    fs.writeFileSync(path.join(ordinaryRoot, '.gitignore'), drift);
    if (label === 'toevoeging') write(ordinaryRoot, 'src/contract-hidden.js', 'export {};\n');
    const rejected = run(ordinaryRoot);
    assert.notEqual(rejected.status, 0, label);
    assert.match(rejected.stderr, /GITIGNORE_CONTRACT_DRIFT/u, label);
    fs.rmSync(path.join(ordinaryRoot, 'src/contract-hidden.js'), { force: true });
    fs.writeFileSync(path.join(ordinaryRoot, '.gitignore'), projectGitignore);
  }
  const sealedRoot = fullyTrackedResolvedRepository(t);
  const restoreIgnored = holdIgnoredFixturePaths(sealedRoot);
  const gitDirectory = path.join(sealedRoot, '.git');
  const heldGit = `${sealedRoot}.git-held`;
  fs.renameSync(gitDirectory, heldGit);
  try {
    for (const [label, drift] of gitignoreDrifts(projectGitignore)) {
      fs.writeFileSync(path.join(sealedRoot, '.gitignore'), drift);
      const rejected = run(sealedRoot, ['--check']);
      assert.notEqual(rejected.status, 0, label);
      assert.match(rejected.stderr, /GITIGNORE_CONTRACT_DRIFT/u, label);
      fs.writeFileSync(path.join(sealedRoot, '.gitignore'), projectGitignore);
    }
  } finally {
    fs.renameSync(heldGit, gitDirectory);
    restoreIgnored();
  }
});

test('untracked nested .gitignore faalt ordinary en kan geen sibling verbergen', (t) => {
  const root = createRepository(t);
  write(root, 'src/.gitignore', 'hidden-by-nested.js\n');
  write(root, 'src/hidden-by-nested.js', 'export const stillVisible = true;\n');
  const rootOnlyUntracked = git(root, [
    '-c', 'core.excludesFile=/dev/null',
    'ls-files', '--others', '--exclude-from=.gitignore', '-z',
  ]).split('\0').filter(Boolean);
  assert.ok(rootOnlyUntracked.includes('src/.gitignore'));
  assert.ok(rootOnlyUntracked.includes('src/hidden-by-nested.js'));

  const rejected = run(root);
  assert.notEqual(rejected.status, 0);
  assert.match(rejected.stderr, /GITIGNORE_CONTRACT_DRIFT.*src\/.gitignore/u);
});

test('nested .gitignore faalt ook in sealed herleiding', (t) => {
  const root = fullyTrackedResolvedRepository(t);
  const restoreIgnored = holdIgnoredFixturePaths(root);
  const gitDirectory = path.join(root, '.git');
  const heldGit = `${root}.git-held`;
  fs.renameSync(gitDirectory, heldGit);
  try {
    write(root, 'src/.gitignore', 'hidden-by-nested.js\n');
    write(root, 'src/hidden-by-nested.js', 'export const stillVisible = true;\n');
    const rejected = run(root, ['--check']);
    assert.notEqual(rejected.status, 0);
    assert.match(rejected.stderr, /GITIGNORE_CONTRACT_DRIFT.*src\/.gitignore/u);
  } finally {
    fs.renameSync(heldGit, gitDirectory);
    restoreIgnored();
  }
});

test('Git-universum blijft project-only onder hostile inherited Git environment', (t) => {
  const root = createRepository(t);
  const foreign = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-repository-inventory-foreign-'));
  t.after(() => fs.rmSync(foreign, { recursive: true, force: true }));
  git(foreign, ['init', '--quiet']);
  write(foreign, '.gitignore', projectGitignore);
  write(foreign, 'src/foreign-tracked.js', 'export const foreignTracked = true;\n');
  git(foreign, ['add', '.gitignore', 'src/foreign-tracked.js']);
  write(foreign, 'src/foreign-untracked.js', 'export const foreignUntracked = true;\n');
  write(foreign, 'hostile.gitconfig', 'dit is geen geldige Git-configuratie\n');

  const trackedArgs = ['ls-files', '-z'];
  const untrackedArgs = [
    '-c', 'core.excludesFile=/dev/null',
    'ls-files', '--others', '--exclude-from=.gitignore', '-z',
  ];
  const cases = [
    ['GIT_DIR', { GIT_DIR: path.join(foreign, '.git') }, trackedArgs, 'redirected'],
    ['GIT_WORK_TREE', { GIT_WORK_TREE: foreign }, untrackedArgs, 'redirected'],
    ['GIT_INDEX_FILE', { GIT_INDEX_FILE: path.join(foreign, '.git', 'index') },
      trackedArgs, 'redirected'],
    ['GIT_CONFIG_COUNT', { GIT_CONFIG_COUNT: 'geen-getal' }, trackedArgs, 'rejected'],
    ['GIT_CONFIG_GLOBAL/SYSTEM', {
      GIT_CONFIG_NOSYSTEM: '0',
      GIT_CONFIG_GLOBAL: path.join(foreign, 'hostile.gitconfig'),
      GIT_CONFIG_SYSTEM: path.join(foreign, 'hostile.gitconfig'),
    }, trackedArgs, 'rejected'],
  ];

  for (const [label, environment, args, hostileEffect] of cases) {
    const redirected = rawGit(root, args, environment);
    if (hostileEffect === 'redirected') {
      assert.equal(redirected.status, 0, redirected.stderr || redirected.stdout);
      assert.match(redirected.stdout, /foreign-(?:tracked|untracked)\.js/u,
        `${label} was niet hostile`);
    } else {
      assert.notEqual(redirected.status, 0, `${label} was niet hostile`);
    }

    const generated = run(root, [], environment);
    assert.equal(generated.status, 0, `${label}: ${generated.stderr || generated.stdout}`);
    const manifest = JSON.parse(fs.readFileSync(
      path.join(root, 'docs/eva-repository-inventory.generated.json'), 'utf8',
    ));
    assert.ok(manifest.files.some((file) => file.path === 'src/index.js'), label);
    assert.equal(manifest.files.some((file) => file.path.includes('foreign-')), false, label);
  }

  write(root, 'src/path-fresh.js', 'export const pathFresh = true;\n');
  const noPathLookup = run(root, [], { PATH: path.join(foreign, 'no-executables') });
  assert.equal(noPathLookup.status, 0, noPathLookup.stderr || noPathLookup.stdout);
  const pathManifest = JSON.parse(fs.readFileSync(
    path.join(root, 'docs/eva-repository-inventory.generated.json'), 'utf8',
  ));
  assert.equal(record(pathManifest, 'src/path-fresh.js').gitState, 'untracked');
  assert.equal(record(pathManifest, 'src/path-fresh.js').contentSha256,
    createHash('sha256').update(fs.readFileSync(path.join(root, 'src/path-fresh.js'))).digest('hex'));
});

test('global en info excludes kunnen nonignored untracked bronbestanden niet verbergen', (t) => {
  const root = createRepository(t);
  const globalExcludes = path.join(root, '.git', 'global-excludes');
  fs.writeFileSync(globalExcludes, 'src/global-hidden.js\n');
  git(root, ['config', 'core.excludesFile', globalExcludes]);
  fs.writeFileSync(path.join(root, '.git', 'info', 'exclude'), 'src/info-hidden.js\n');
  write(root, 'src/global-hidden.js', 'export const globalHidden = false;\n');
  write(root, 'src/info-hidden.js', 'export const infoHidden = false;\n');

  const generated = run(root);
  assert.equal(generated.status, 0, generated.stderr || generated.stdout);
  const manifest = JSON.parse(fs.readFileSync(
    path.join(root, 'docs/eva-repository-inventory.generated.json'), 'utf8',
  ));
  for (const file of ['src/global-hidden.js', 'src/info-hidden.js']) {
    assert.equal(record(manifest, file).gitState, 'untracked', file);
    assert.ok(manifest.policyErrors.some((error) => (
      error.code === 'UNTRACKED_REPOSITORY_FILE' && error.path === file
    )), file);
  }
  assert.equal(JSON.stringify(manifest).includes('must-not-be-read'), false);
  assert.equal(JSON.stringify(manifest).includes('TOP_SECRET'), false);
});

test('volledig tracked unresolved dispositions blokkeren gewone architecture-check', (t) => {
  const root = fullyTrackedDispositionRepository(t);
  const manifest = JSON.parse(fs.readFileSync(
    path.join(root, 'docs/eva-repository-inventory.generated.json'), 'utf8',
  ));
  assert.equal(manifest.summary.untracked, 0);
  assert.equal(manifest.summary.tombstones, 0);
  const check = run(root, ['--check']);
  assert.notEqual(check.status, 0, 'tracked unresolved dispositions mochten ten onrechte door');
  for (const code of UNRESOLVED_DISPOSITION_CODES) {
    assert.match(check.stderr, new RegExp(code, 'u'));
  }
  for (const file of manifest.files.filter((item) => item.decision !== 'keep')) {
    assert.equal(file.release.status, 'blocked-unresolved-disposition', file.path);
  }
});

test('volledig tracked unresolved dispositions blokkeren sealed herleiding', (t) => {
  const root = fullyTrackedDispositionRepository(t);
  const gitDirectory = path.join(root, '.git');
  const heldGit = `${root}.git-held`;
  const restoreIgnored = holdIgnoredFixturePaths(root);
  fs.renameSync(gitDirectory, heldGit);
  try {
    const check = run(root, ['--check']);
    assert.notEqual(check.status, 0, 'sealed unresolved dispositions mochten ten onrechte door');
    for (const code of UNRESOLVED_DISPOSITION_CODES) {
      assert.match(check.stderr, new RegExp(code, 'u'));
    }
  } finally {
    fs.renameSync(heldGit, gitDirectory);
    restoreIgnored();
  }
});

test('sealed outputs worden beide volledig gevalideerd voordat inhoud wordt gelezen', (t) => {
  const root = fullyTrackedResolvedRepository(t);
  const outputs = [
    'docs/eva-repository-inventory.generated.json',
    'docs/eva-repository-inventory.generated.md',
  ];
  const originals = new Map(outputs.map((relative) => [
    relative, fs.readFileSync(path.join(root, relative)),
  ]));
  const externalSentinel = path.join(`${root}.external`, 'do-not-read.json');
  write(`${root}.external`, 'do-not-read.json', '{"external":"SEALED_EXTERNAL_SENTINEL"}\n');
  fs.chmodSync(externalSentinel, 0o000);
  t.after(() => {
    fs.chmodSync(externalSentinel, 0o600);
    fs.rmSync(`${root}.external`, { recursive: true, force: true });
  });
  const restoreOutput = (relative) => {
    const target = path.join(root, relative);
    fs.rmSync(target, { force: true });
    fs.writeFileSync(target, originals.get(relative));
  };
  const restoreIgnored = holdIgnoredFixturePaths(root);
  const gitDirectory = path.join(root, '.git');
  const heldGit = `${root}.git-held`;
  fs.renameSync(gitDirectory, heldGit);
  try {
    const docsDirectory = path.join(root, 'docs');
    const externalDocsDirectory = `${root}.external-docs`;
    const externalJson = path.join(externalDocsDirectory,
      'eva-repository-inventory.generated.json');
    const jsonMode = fs.statSync(path.join(root, outputs[0])).mode & 0o777;
    fs.renameSync(docsDirectory, externalDocsDirectory);
    fs.chmodSync(externalJson, 0o000);
    fs.symlinkSync(externalDocsDirectory, docsDirectory);
    try {
      const outsideRoot = run(root, ['--check']);
      assert.notEqual(outsideRoot.status, 0);
      assert.match(outsideRoot.stderr,
        /SEALED_OUTPUT_OUTSIDE_ROOT:docs\/eva-repository-inventory\.generated\.json/u);
    } finally {
      fs.unlinkSync(docsDirectory);
      fs.chmodSync(externalJson, jsonMode);
      fs.renameSync(externalDocsDirectory, docsDirectory);
    }

    for (const relative of outputs) {
      const target = path.join(root, relative);
      if (relative.endsWith('.md')) {
        fs.writeFileSync(path.join(root, outputs[0]), 'INTERNAL_JSON_MUST_NOT_BE_READ\n');
      }
      fs.rmSync(target);
      fs.symlinkSync(externalSentinel, target);
      const rejected = run(root, ['--check']);
      assert.notEqual(rejected.status, 0, relative);
      assert.match(rejected.stderr,
        new RegExp(`SEALED_OUTPUT_NOT_REGULAR:${relative.replaceAll('.', '\\.')}`, 'u'), relative);
      assert.doesNotMatch(rejected.stderr, /SEALED_EXTERNAL_SENTINEL|INTERNAL_JSON_MUST_NOT_BE_READ/u);
      restoreOutput(relative);
      if (relative.endsWith('.md')) restoreOutput(outputs[0]);
    }

    for (const relative of outputs) {
      if (relative.endsWith('.md')) {
        fs.writeFileSync(path.join(root, outputs[0]), 'INTERNAL_JSON_MUST_NOT_BE_READ\n');
      }
      fs.truncateSync(path.join(root, relative), MAX_FILE_BYTES + 1);
      const rejected = run(root, ['--check']);
      assert.notEqual(rejected.status, 0, relative);
      assert.match(rejected.stderr,
        new RegExp(`SEALED_OUTPUT_TOO_LARGE:${relative.replaceAll('.', '\\.')}`, 'u'), relative);
      assert.doesNotMatch(rejected.stderr, /INTERNAL_JSON_MUST_NOT_BE_READ/u);
      restoreOutput(relative);
      if (relative.endsWith('.md')) restoreOutput(outputs[0]);
    }
  } finally {
    for (const relative of outputs) restoreOutput(relative);
    fs.renameSync(heldGit, gitDirectory);
    restoreIgnored();
  }
});

test('ordinary check en generation volgen output-parent nooit buiten de repository', (t) => {
  const root = fullyTrackedResolvedRepository(t);
  const outputs = [
    'docs/eva-repository-inventory.generated.json',
    'docs/eva-repository-inventory.generated.md',
  ];
  const docsDirectory = path.join(root, 'docs');
  const externalDocsDirectory = `${root}.ordinary-external-docs`;
  fs.renameSync(docsDirectory, externalDocsDirectory);
  for (const relative of outputs) {
    const target = path.join(externalDocsDirectory, path.basename(relative));
    fs.appendFileSync(target, `ORDINARY_EXTERNAL_${relative.endsWith('.json') ? 'JSON' : 'MARKDOWN'}_SENTINEL\n`);
  }
  const externalBytes = new Map(outputs.map((relative) => [
    relative, fs.readFileSync(path.join(externalDocsDirectory, path.basename(relative))),
  ]));
  fs.symlinkSync(externalDocsDirectory, docsDirectory);
  try {
    for (const [label, args] of [
      ['ordinary check', ['--check']],
      ['ordinary generation', []],
    ]) {
      const rejected = run(root, args);
      assert.notEqual(rejected.status, 0, label);
      assert.match(rejected.stderr,
        /ORDINARY_OUTPUT_OUTSIDE_ROOT:docs\/eva-repository-inventory\.generated\.json/u,
        label);
      for (const relative of outputs) {
        assert.deepEqual(
          fs.readFileSync(path.join(externalDocsDirectory, path.basename(relative))),
          externalBytes.get(relative), `${label}:${relative}`,
        );
      }
    }
  } finally {
    fs.unlinkSync(docsDirectory);
    fs.renameSync(externalDocsDirectory, docsDirectory);
  }
});

test('ordinary secret- en sizebeleid stoppen exact vóór onleesbare inhoud', (t) => {
  const secretRoot = createRepository(t);
  const secretPath = path.join(secretRoot, '.env');
  git(secretRoot, ['add', '--force', '.env']);
  fs.chmodSync(secretPath, 0o000);
  try {
    const rejected = run(secretRoot);
    assert.notEqual(rejected.status, 0);
    assert.match(rejected.stderr,
      /TRACKED_SENSITIVE_PATH: verboden repositorypad \.env/u);
    assert.doesNotMatch(rejected.stderr, /TOP_SECRET/u);
  } finally {
    fs.chmodSync(secretPath, 0o600);
  }

  const oversizedRoot = createRepository(t);
  const oversizedPath = path.join(oversizedRoot, 'src/oversized.js');
  fs.writeFileSync(oversizedPath, 'UNREADABLE_OVERSIZED_SENTINEL\n');
  fs.truncateSync(oversizedPath, MAX_FILE_BYTES + 1);
  fs.chmodSync(oversizedPath, 0o000);
  try {
    const rejected = run(oversizedRoot);
    assert.notEqual(rejected.status, 0);
    assert.match(rejected.stderr,
      /repositorybestand overschrijdt bytegrens: src\/oversized\.js/u);
    assert.doesNotMatch(rejected.stderr, /UNREADABLE_OVERSIZED_SENTINEL/u);
  } finally {
    fs.chmodSync(oversizedPath, 0o600);
  }
});

test('repository-inventaris is Git-breed, canoniek, uitlegbaar en fail-closed', (t) => {
  const root = createRepository(t);
  const first = run(root);
  assert.equal(first.status, 0, first.stderr || first.stdout);

  const jsonPath = path.join(root, 'docs/eva-repository-inventory.generated.json');
  const markdownPath = path.join(root, 'docs/eva-repository-inventory.generated.md');
  const initialJson = fs.readFileSync(jsonPath);
  const manifest = JSON.parse(initialJson);
  const initialMarkdown = fs.readFileSync(markdownPath, 'utf8');

  assert.equal(manifest.schema, 'EvaRepositoryInventory/v1');
  assert.deepEqual(gitignorePatterns(fs.readFileSync(path.join(root, '.gitignore'), 'utf8')),
    EXPECTED_GITIGNORE_PATTERNS);
  assert.deepEqual(manifest.scope.sources, [
    'git ls-files -z',
    'git -c core.excludesFile=/dev/null ls-files --others --exclude-from=.gitignore -z',
  ]);
  assert.deepEqual(manifest.exclusions.map(({ path: excluded }) => excluded),
    EXPECTED_EXCLUSIONS);
  const neverScan = new Set(['node_modules/', '.venv/', 'venv/']);
  assert.ok(manifest.exclusions.filter((item) => !neverScan.has(item.path))
    .every((item) => item.readPolicy === 'never-read'));
  assert.ok(manifest.exclusions.filter((item) => neverScan.has(item.path))
    .every((item) => item.readPolicy === 'never-scan-or-hash'));
  for (const secret of [
    '.env', 'data/private/sentinel.txt', 'node_modules/private/sentinel.txt',
    'coverage/private/sentinel.txt', 'eva_brain/__pycache__/service.pyc',
  ]) {
    assert.equal(manifest.files.some(({ path: file }) => file === secret), false);
    assert.equal(initialJson.includes(secret === '.env' ? 'TOP_SECRET' : 'must-not-be-read'), false);
  }

  assert.equal(record(manifest, 'src/index.js').gitState, 'tracked');
  assert.equal(record(manifest, 'src/new.js').gitState, 'untracked');
  assert.equal(record(manifest, 'src/retired.js').gitState, 'tombstone');
  assert.equal(record(manifest, 'src/retired.js').present, false);
  assert.equal(record(manifest, 'src/retired.js').decision, 'decision');
  assert.equal(record(manifest, 'src/retired.js').release.status,
    'blocked-unresolved-disposition');
  assert.ok(manifest.policyErrors.some((item) => (
    item.code === 'UNRESOLVED_DISPOSITION_DECISION' && item.path === 'src/retired.js'
  )));
  assert.equal(record(manifest, 'src/new.js').release.status, 'blocked-untracked');
  assert.equal(record(manifest, 'docs/untracked-note.md').release.status, 'blocked-untracked');
  for (const template of ['.env.example', 'docs/templates/.env.example']) {
    assert.equal(record(manifest, template).gitState, 'untracked', template);
    assert.equal(record(manifest, template).present, true, template);
    assert.equal(record(manifest, template).contentSha256,
      createHash('sha256').update(fs.readFileSync(path.join(root, template))).digest('hex'), template);
  }
  assert.equal(record(manifest, 'native/workspace_writer_broker.c').decision, 'integrate');
  assert.equal(record(manifest, 'src/mind/voiceingress.js').decision, 'delete');
  assert.equal(record(manifest, 'scripts/sanitize-secret-history.mjs').decision, 'quarantine');
  assert.deepEqual(manifest.files.filter((file) => (
    file.role === 'documentation' && file.decision === 'archive'
  )).map((file) => file.path), [
    'docs/eva-runtime-map.md',
    'docs/live-acceptance-2026-08-04.md',
  ]);
  for (const file of ACTIVE_LEDGER_EVIDENCE) {
    assert.equal(record(manifest, file).decision, 'keep', file);
  }
  assert.match(initialMarkdown, /## Actielijst/u);
  assert.match(initialMarkdown, /workspace_writer_broker\.c.*integrate/u);
  assert.match(initialMarkdown, /voiceingress\.js.*delete/u);
  for (const dormant of [
    'src/mind/artifactcollections.js',
    'src/mind/materiallifecycle.js',
    'src/mind/workcompletion.js',
  ]) assert.equal(record(manifest, dormant).decision, 'isolate');

  const expectedPaths = new Set([
    ...git(root, ['ls-files', '-z']).split('\0').filter(Boolean),
    ...git(root, ['-c', 'core.excludesFile=/dev/null', 'ls-files', '--others',
      '--exclude-from=.gitignore', '-z']).split('\0').filter(Boolean),
    'docs/eva-repository-inventory.generated.json',
    'docs/eva-repository-inventory.generated.md',
  ]);
  assert.deepEqual(manifest.files.map(({ path: file }) => file), [...expectedPaths].sort());
  assert.equal(new Set(manifest.files.map(({ path: file }) => file)).size, manifest.files.length);

  for (const file of manifest.files.filter(({ present }) => present)) {
    assert.ok(Number.isSafeInteger(file.bytes) && file.bytes >= 0, file.path);
    assert.match(file.contentSha256, /^[a-f0-9]{64}$/u, file.path);
  }
  for (const output of [
    'docs/eva-repository-inventory.generated.json',
    'docs/eva-repository-inventory.generated.md',
  ]) {
    assert.equal(record(manifest, output).hashMode, 'canonical-self-reference-redacted');
    assert.equal(record(manifest, output).contentSha256,
      createHash('sha256').update(
        `EvaRepositoryInventory/v1:${output}:canonical-self-reference-redacted`,
      ).digest('hex'), output);
  }
  assert.ok(manifest.classificationRules.every(({ matchCount }) => matchCount > 0));
  assert.ok(manifest.files.every((file) => file.matchingRuleIds.length === 1
    && file.roleRuleId === file.matchingRuleIds[0]));
  assert.equal(manifest.classificationRules.reduce((sum, rule) => sum + rule.matchCount, 0),
    manifest.files.length);

  const unicodeBytes = fs.readFileSync(path.join(root, 'src/new.js'));
  assert.equal(record(manifest, 'src/new.js').bytes, unicodeBytes.length);
  assert.equal(record(manifest, 'src/new.js').contentSha256,
    createHash('sha256').update(unicodeBytes).digest('hex'));
  const binaryBytes = fs.readFileSync(path.join(root, 'src/web/public/eva.glb'));
  assert.equal(record(manifest, 'src/web/public/eva.glb').bytes, binaryBytes.length);
  assert.equal(record(manifest, 'src/web/public/eva.glb').contentSha256,
    createHash('sha256').update(binaryBytes).digest('hex'));

  assert.ok(hasEdge(manifest, 'import', 'src/index.js', 'src/worker.js'));
  assert.ok(hasEdge(manifest, 'subprocess', 'src/index.js', 'scripts/eva-backup.mjs'));
  assert.ok(hasEdge(manifest, 'systemd-exec', 'deploy/eva-runtime-maintainer.service', 'deploy/eva-runtime-maintainer.mjs'));
  assert.ok(hasEdge(manifest, 'systemd-trigger', 'deploy/eva-runtime-maintainer.path', 'deploy/eva-runtime-maintainer.service'));
  assert.ok(hasEdge(manifest, 'systemd-trigger', 'deploy/eva-runtime-maintainer.service', 'deploy/eva-runtime-maintainer-recover.service'));
  assert.ok(hasEdge(manifest, 'systemd-exec', 'deploy/eva-brain.service', 'eva_brain/service.py'));
  assert.ok(hasEdge(manifest, 'static-load', 'src/web/public/index.html', 'src/web/public/style.css'));
  assert.ok(hasEdge(manifest, 'static-load', 'src/web/public/index.html', 'src/web/public/app.js'));
  assert.ok(hasEdge(manifest, 'static-load', 'src/web/public/app.js', 'src/web/public/eva.glb'));
  assert.ok(hasEdge(manifest, 'static-serve', 'src/web/server.js', 'src/web/public/index.html'));
  assert.ok(hasEdge(manifest, 'package-script', 'package.json', 'src/index.js'));
  assert.ok(hasEdge(manifest, 'package-script', 'package.json', 'scripts/run-isolated-tests-bootstrap'));
  assert.ok(hasEdge(manifest, 'package-test-glob', 'package.json', 'test/sample.test.js'));
  assert.ok(hasEdge(manifest, 'installer-copy', 'deploy/install-eva-runtime-maintainer.sh', 'deploy/eva-runtime-maintainer.mjs'));
  assert.ok(hasEdge(manifest, 'test-fixture', 'test/sample.test.js', 'test/sample-fixture.mjs'));
  assert.equal(hasEdge(manifest, 'import', 'test/sample.test.js', 'src/index.js'), false);
  assert.equal(hasEdge(manifest, 'subprocess', 'test/sample.test.js', 'scripts/eva-backup.mjs'), false);
  assert.equal(hasEdge(manifest, 'installer-copy', 'deploy/install-eva-runtime-maintainer.sh',
    'scripts/run-isolated-tests.test.mjs'), false);

  const policyRed = run(root, ['--check']);
  assert.notEqual(policyRed.status, 0);
  assert.match(policyRed.stderr, /UNTRACKED_REPOSITORY_FILE/u);
  assert.match(policyRed.stderr, /TRACKED_TOMBSTONE/u);

  git(root, ['add', 'src/new.js',
    'docs/untracked-note.md',
    '.env.example',
    'docs/templates/.env.example',
    'docs/eva-repository-inventory.generated.json',
    'docs/eva-repository-inventory.generated.md']);
  const statusDrift = run(root, ['--check']);
  assert.notEqual(statusDrift.status, 0);
  assert.match(statusDrift.stderr, /verouderd|drift/iu);
  git(root, ['add', '-u']);
  assert.equal(run(root).status, 0);
  const unresolved = run(root, ['--check']);
  assert.notEqual(unresolved.status, 0);
  for (const code of UNRESOLVED_DISPOSITION_CODES) {
    assert.match(unresolved.stderr, new RegExp(code, 'u'));
  }
  resolveDispositionFixture(root);
  assert.equal(run(root).status, 0);
  const resolvedManifest = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  assert.equal(record(resolvedManifest, 'native/resolved.c').decision, 'keep');
  const firstJson = fs.readFileSync(jsonPath);
  const firstMarkdown = fs.readFileSync(markdownPath);

  const generatedAgain = run(root);
  assert.equal(generatedAgain.status, 0, generatedAgain.stderr || generatedAgain.stdout);
  assert.deepEqual(fs.readFileSync(jsonPath), firstJson);
  assert.deepEqual(fs.readFileSync(markdownPath), firstMarkdown);
  const check = run(root, ['--check']);
  assert.equal(check.status, 0, check.stderr || check.stdout);

  const workerPath = path.join(root, 'src/worker.js');
  fs.writeFileSync(workerPath, 'export const worker = null;\n');
  const beforeSourceDriftJson = fs.readFileSync(jsonPath);
  const beforeSourceDriftMarkdown = fs.readFileSync(markdownPath);
  const sourceDrift = run(root, ['--check']);
  assert.notEqual(sourceDrift.status, 0);
  assert.deepEqual(fs.readFileSync(jsonPath), beforeSourceDriftJson);
  assert.deepEqual(fs.readFileSync(markdownPath), beforeSourceDriftMarkdown);
  fs.writeFileSync(workerPath, 'export const worker = true;\n');

  fs.appendFileSync(markdownPath, 'drift\n');
  const stale = run(root, ['--check']);
  assert.notEqual(stale.status, 0);
  assert.match(stale.stderr, /verouderd|drift/iu);
  assert.equal(fs.readFileSync(markdownPath).subarray(-6).toString(), 'drift\n');
  assert.equal(run(root).status, 0);

  const gitDirectory = path.join(root, '.git');
  const heldGit = `${root}.git-held`;
  const restoreIgnored = holdIgnoredFixturePaths(root);
  fs.renameSync(gitDirectory, heldGit);
  try {
    const sealedCheck = run(root, ['--check']);
    assert.equal(sealedCheck.status, 0, sealedCheck.stderr || sealedCheck.stdout);
    assert.match(sealedCheck.stdout, /sealed|release/iu);

    const sealedJson = fs.readFileSync(jsonPath);
    for (const mutate of [
      (value) => { value.summary.files += 1; },
      (value) => { record(value, 'src/index.js').role = 'documentation'; },
      (value) => { value.policyErrors = []; value.sourceDigest = '0'.repeat(64); },
      (value) => { record(value, 'docs/eva-repository-inventory.generated.json').bytes += 1; },
    ]) {
      const corrupt = JSON.parse(sealedJson);
      mutate(corrupt);
      fs.writeFileSync(jsonPath, `${JSON.stringify(corrupt, null, 2)}\n`);
      const rejected = run(root, ['--check']);
      assert.notEqual(rejected.status, 0);
      fs.writeFileSync(jsonPath, sealedJson);
    }

    for (const excluded of [
      '.env', '.env.local', 'private.sqlite', 'service.log', 'service.pid',
      'credential.pem', 'cache.pyc', '.venv/private', 'draft.swp',
      'scratch_probe', 'dist/output.js', '.nyc_output/state.json',
      'eva_brain/replay/LOCK_test.md',
    ]) {
      write(root, excluded, 'SHOULD_NOT_ENTER_SEALED_RELEASE=1\n');
      const secretRejected = run(root, ['--check']);
      assert.notEqual(secretRejected.status, 0, excluded);
      assert.match(secretRejected.stderr, /uitgesloten pad/iu, excluded);
      fs.rmSync(path.join(root, excluded), { force: true });
      for (const directory of ['.venv', 'dist', '.nyc_output', 'eva_brain/replay']) {
        if (excluded.startsWith(`${directory}/`)) {
          fs.rmSync(path.join(root, directory), { recursive: true, force: true });
        }
      }
    }
  } finally {
    fs.renameSync(heldGit, gitDirectory);
    restoreIgnored();
  }

  write(root, 'test/nested/hidden.test.js', 'export {};\n');
  write(root, 'eva_brain/nested/test_hidden.py', 'def test_hidden(): assert True\n');
  assert.equal(run(root).status, 0);
  const nestedManifest = JSON.parse(fs.readFileSync(jsonPath));
  assert.equal(record(nestedManifest, 'test/nested/hidden.test.js').role, 'verification');
  assert.equal(record(nestedManifest, 'eva_brain/nested/test_hidden.py').role, 'verification');
  assert.ok(hasEdge(nestedManifest, 'package-glob', 'package.json', 'test/nested/hidden.test.js'));
  assert.equal(hasEdge(nestedManifest, 'package-test-glob', 'package.json',
    'test/nested/hidden.test.js'), false);
  assert.ok(nestedManifest.policyErrors.some((item) => item.code === 'UNGATED_TEST'
    && item.path === 'test/nested/hidden.test.js'));
  assert.ok(nestedManifest.policyErrors.some((item) => item.code === 'UNGATED_TEST'
    && item.path === 'eva_brain/nested/test_hidden.py'));
  fs.rmSync(path.join(root, 'test/nested'), { recursive: true });
  fs.rmSync(path.join(root, 'eva_brain/nested'), { recursive: true });
  assert.equal(run(root).status, 0);

  fs.symlinkSync('missing-target.js', path.join(root, 'src/dangling.js'));
  const dangling = run(root);
  assert.notEqual(dangling.status, 0);
  assert.match(dangling.stderr, /niet-regulier/iu);
  fs.unlinkSync(path.join(root, 'src/dangling.js'));

  write(root, 'mystery/unknown.xyz', 'unclassified\n');
  const unclassified = run(root);
  assert.notEqual(unclassified.status, 0);
  assert.match(unclassified.stderr, /ongeclassificeerd/iu);
  fs.rmSync(path.join(root, 'mystery'), { recursive: true });

  git(root, ['rm', '--quiet', 'benchmark/catalog.json']);
  const deadRule = run(root);
  assert.notEqual(deadRule.status, 0);
  assert.match(deadRule.stderr, /dode classificatieregel/iu);
});
