import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import {
  collectTypeScriptDebtEvidence,
  createTypeScriptDebtManifest,
  evaluateTypeScriptDebt,
  validateTypeScriptDebtManifest,
  validateTypeScriptEvidence,
} from '../scripts/quality-gate.mjs';
import complexityConfig from '../eslint.complexity.config.js';

const projectRoot = process.cwd();

test('complexiteitsgate meet geen regel- of functielengteplafond', () => {
  const configuredRules = Object.assign({}, ...complexityConfig
    .map((entry) => entry.rules || {}));
  assert.equal(configuredRules['max-lines'], undefined);
  assert.equal(configuredRules['max-lines-per-function'], undefined);
  assert.notEqual(configuredRules.complexity, undefined);
  assert.notEqual(configuredRules['max-depth'], undefined);
});

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const canonicalJsonValue = (value) => {
  if (Array.isArray(value)) return value.map(canonicalJsonValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort()
      .map((key) => [key, canonicalJsonValue(value[key])]));
  }
  return value;
};
const diagnosticFingerprint = (entry) => sha256(JSON.stringify({
  contract: 'EvaTypeScriptDiagnostic/v3',
  category: 'error',
  code: entry.code,
  file: entry.file,
  messageSha256: entry.messageSha256,
  siteSha256: entry.siteSha256,
}));
const inputReceiptSha256 = (value) => sha256(JSON.stringify(canonicalJsonValue({
  contract: 'EvaTypeScriptInputReceipt/v3',
  compiler: value.compiler,
  config: value.config,
  sourceInventory: value.sourceInventory,
  programInputs: value.programInputs,
  dependencies: value.dependencies,
})));

function fixture(t) {
  const root = fs.mkdtempSync('/tmp/eva-quality-gate-');
  fs.chmodSync(root, 0o700);
  fs.mkdirSync(path.join(root, 'scripts'));
  fs.mkdirSync(path.join(root, 'src'));
  fs.mkdirSync(path.join(root, 'node_modules'));
  fs.copyFileSync(path.join(projectRoot, 'scripts/quality-gate.mjs'),
    path.join(root, 'scripts/quality-gate.mjs'));
  fs.symlinkSync(path.join(projectRoot, 'node_modules/typescript'),
    path.join(root, 'node_modules/typescript'));
  fs.writeFileSync(path.join(root, 'package.json'), '{"type":"module"}\n');
  fs.writeFileSync(path.join(root, 'quality-baseline.json'), `${JSON.stringify({
    typescriptDiagnostics: 1, lintWarnings: 0, complexityWarnings: 0,
  }, null, 2)}\n`);
  const config = `${JSON.stringify({
    compilerOptions: {
      allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
    },
    include: ['src/**/*.js'],
  }, null, 2)}\n`;
  fs.writeFileSync(path.join(root, 'tsconfig.json'), config);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), '/** @type {number} */\nconst value = "replacement";\nvoid value;\n');

  const manifestPath = path.join(root, 'typescript-diagnostic-debt.json');
  fs.writeFileSync(manifestPath, '{}\n');
  const evidence = collectTypeScriptDebtEvidence(root);
  assert.equal(evidence.diagnostics.length, 1, 'fixture moet exact één bekende diagnose leveren');
  const allowedMessage = 'known historical fixture diagnostic';
  const allowedDiagnostic = {
    ...evidence.diagnostics[0],
    message: allowedMessage,
    messageSha256: sha256(allowedMessage),
    siteSha256: '2'.repeat(64),
  };
  allowedDiagnostic.fingerprint = diagnosticFingerprint(allowedDiagnostic);
  const debtManifest = createTypeScriptDebtManifest({
    ...evidence, diagnostics: [allowedDiagnostic],
  }, 1);
  fs.writeFileSync(manifestPath, `${JSON.stringify(debtManifest, null, 2)}\n`);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function manifestEntry(seed, count = 1) {
  const file = `src/${seed}.js`;
  const entry = {
    code: 2304,
    file,
    messageSha256: sha256(`diagnostic ${file}`),
    siteSha256: sha256(`site:${seed}`),
    count,
  };
  return { fingerprint: diagnosticFingerprint(entry), ...entry };
}

function evidenceDiagnostic(entry) {
  return {
    contract: 'EvaTypeScriptDiagnostic/v3',
    category: 'error',
    code: entry.code,
    file: entry.file,
    messageSha256: entry.messageSha256,
    siteSha256: entry.siteSha256,
    fingerprint: entry.fingerprint,
    line: 1,
    message: `diagnostic ${entry.file}`,
  };
}

function contractFixture() {
  const diagnostics = [manifestEntry('known-a'), manifestEntry('known-b')]
    .sort((left, right) => left.fingerprint.localeCompare(right.fingerprint));
  const manifest = {
    schema: 4,
    contract: 'EvaTypeScriptDiagnosticDebt/v4',
    maximum: 2,
    snapshotCount: 2,
    compiler: {
      name: 'typescript', version: '5.9.3',
      packageJsonSha256: 'a'.repeat(64), compilerSha256: 'b'.repeat(64),
      treeCount: 2, treeBytes: 2, treeSha256: 'd'.repeat(64),
    },
    config: {
      path: 'tsconfig.json', sha256: 'c'.repeat(64), filesCount: 1,
      filesBytes: 1,
      filesSha256: 'e'.repeat(64), optionsSha256: 'f'.repeat(64),
      projectReferencesSha256: '0'.repeat(64),
    },
    sourceInventory: {
      rootNames: ['src/known-a.js', 'src/known-b.js'],
      count: 2,
      sha256: sha256('src/known-a.js\0src/known-b.js\0'),
    },
    programInputs: { count: 2, bytes: 2, sha256: '1'.repeat(64) },
    dependencies: { count: 1, bytes: 0, sha256: '2'.repeat(64) },
    provenance: { scope: 'worktree-snapshot', releaseEligible: false },
    diagnostics,
  };
  manifest.inputReceiptSha256 = inputReceiptSha256(manifest);
  const evidence = {
    compiler: { ...manifest.compiler },
    config: { ...manifest.config },
    sourceInventory: { ...manifest.sourceInventory },
    programInputs: { ...manifest.programInputs },
    dependencies: { ...manifest.dependencies },
    inputReceiptSha256: manifest.inputReceiptSha256,
    diagnostics: diagnostics.map(evidenceDiagnostic),
  };
  return { manifest, evidence };
}

function sourceFixture(t) {
  const fixtureRoot = fs.mkdtempSync('/tmp/eva-quality-source-');
  fs.chmodSync(fixtureRoot, 0o700);
  fs.mkdirSync(path.join(fixtureRoot, 'src'));
  fs.writeFileSync(path.join(fixtureRoot, 'tsconfig.json'), `${JSON.stringify({
    compilerOptions: {
      allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
    },
    include: ['src/**/*.js'],
  }, null, 2)}\n`);
  t.after(() => fs.rmSync(fixtureRoot, { recursive: true, force: true }));
  return fixtureRoot;
}

const CREATE_PROGRAM_BLOCK = `  const program = ts.createProgram({
    rootNames: parsed.fileNames,
    options: parsed.options,
    projectReferences: parsed.projectReferences,
    host: compilerHost,
  });`;
const LEGACY_CREATE_PROGRAM_BLOCK = `  const program = ts.createProgram({
    rootNames: parsed.fileNames,
    options: parsed.options,
    projectReferences: parsed.projectReferences,
  });`;

let composedGateSequence = 0;

async function composedGate(t, fixtureRoot, injection, transform = (source) => source) {
  return composedGateAround(t, fixtureRoot, {
    afterCreateProgram: injection,
    transform,
  });
}

async function composedGateAround(t, fixtureRoot, {
  beforeCreateProgram = '', afterCreateProgram = '', transform = (source) => source,
} = {}) {
  const sourcePath = path.join(projectRoot, 'scripts/quality-gate.mjs');
  let source = fs.readFileSync(sourcePath, 'utf8');
  const createProgramBlock = source.includes(CREATE_PROGRAM_BLOCK)
    ? CREATE_PROGRAM_BLOCK : LEGACY_CREATE_PROGRAM_BLOCK;
  assert.equal(source.includes(createProgramBlock), true,
    'testcompositie vindt het createProgram-bindpunt niet meer');
  source = source.replace(createProgramBlock,
    `${beforeCreateProgram}\n${createProgramBlock}\n${afterCreateProgram}`);
  source = source.replace(
    'let compilerEntrypointPreloaded = Boolean(require.cache[typescriptEntrypoint]);',
    'let compilerEntrypointPreloaded = false;',
  );
  source = source.replace(
    'const compilerEntrypointIsCached = () => Boolean(require.cache[typescriptEntrypoint]);',
    'const compilerEntrypointIsCached = () => false;',
  );
  source = transform(source);
  const scripts = path.join(fixtureRoot, 'scripts');
  fs.mkdirSync(scripts, { recursive: true });
  composedGateSequence += 1;
  const modulePath = path.join(scripts,
    `quality-gate-composed-${process.pid}-${composedGateSequence}.mjs`);
  fs.writeFileSync(modulePath, source, { mode: 0o600 });
  t.after(() => fs.rmSync(modulePath, { force: true }));
  return import(`${pathToFileURL(modulePath).href}?test=${composedGateSequence}`);
}

function installTypeScriptLink(fixtureRoot) {
  const modules = path.join(fixtureRoot, 'node_modules');
  fs.mkdirSync(modules, { recursive: true });
  fs.symlinkSync(path.join(projectRoot, 'node_modules/typescript'),
    path.join(modules, 'typescript'));
}

function installTypeScriptCopy(fixtureRoot) {
  const modules = path.join(fixtureRoot, 'node_modules');
  fs.mkdirSync(modules, { recursive: true });
  fs.cpSync(path.join(projectRoot, 'node_modules/typescript'),
    path.join(modules, 'typescript'), { recursive: true });
}

function writeFakeEslint(fixtureRoot, source) {
  const bin = path.join(fixtureRoot, 'node_modules/eslint/bin');
  fs.mkdirSync(bin, { recursive: true });
  fs.writeFileSync(path.join(bin, 'eslint.js'), source, { mode: 0o600 });
}

test('debtgate weigert foutsubstitutie die de oude count-only gate doorlaat', (t) => {
  const root = fixture(t);
  const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
    cwd: root, encoding: 'utf8',
  });
  assert.notEqual(result.status, 0,
    `count-only accepteerde de vervangen diagnose:\n${result.stdout}${result.stderr}`);
  assert.match(`${result.stdout}${result.stderr}`, /onverwachte TypeScript-diagnose/u);
});

test('debtcontract staat alleen subset toe en bewaakt multipliciteit', () => {
  const { manifest, evidence } = contractFixture();
  const resolved = evaluateTypeScriptDebt({
    manifest,
    evidence: { ...evidence, diagnostics: evidence.diagnostics.slice(0, 1) },
    baselineMaximum: 2,
  });
  assert.equal(resolved.ok, true);
  assert.equal(resolved.resolved, 1);

  const replacement = evidenceDiagnostic(manifestEntry('replacement'));
  const substituted = evaluateTypeScriptDebt({
    manifest,
    evidence: { ...evidence, diagnostics: [evidence.diagnostics[0], replacement] },
    baselineMaximum: 2,
  });
  assert.equal(substituted.ok, false);
  assert.equal(substituted.unexpected.length, 1);

  const duplicated = evaluateTypeScriptDebt({
    manifest,
    evidence: { ...evidence, diagnostics: [evidence.diagnostics[0], evidence.diagnostics[0]] },
    baselineMaximum: 2,
  });
  assert.equal(duplicated.ok, false);
  assert.equal(duplicated.unexpected.length, 1);
});

test('debtcontract bindt exact schema, compiler, config, bronset en releaseprovenance', () => {
  const { manifest, evidence } = contractFixture();
  assert.equal(evaluateTypeScriptDebt({ manifest, evidence, baselineMaximum: 2 }).ok, true);
  const changedBindings = [
    { ...evidence, compiler: { ...evidence.compiler, packageJsonSha256: '9'.repeat(64) } },
    { ...evidence, config: { ...evidence.config, sha256: '8'.repeat(64) } },
    {
      ...evidence,
      sourceInventory: {
        rootNames: ['src/known-a.js', 'src/known-b.js', 'src/known-c.js'],
        count: 3,
        sha256: sha256('src/known-a.js\0src/known-b.js\0src/known-c.js\0'),
      },
      programInputs: { ...evidence.programInputs, count: 3 },
    },
  ];
  for (const candidate of changedBindings) {
    const changed = { ...candidate, inputReceiptSha256: inputReceiptSha256(candidate) };
    assert.equal(evaluateTypeScriptDebt({
      manifest, evidence: changed, baselineMaximum: 2,
    }).ok, false);
  }
  assert.equal(evaluateTypeScriptDebt({
    manifest, evidence, baselineMaximum: 2, releaseMode: true,
  }).ok, false);
  assert.throws(() => validateTypeScriptDebtManifest({ ...manifest, extra: true }),
    /exacte sleutelset/u);
  assert.throws(() => validateTypeScriptDebtManifest({
    ...manifest, diagnostics: [...manifest.diagnostics].reverse(),
  }), /niet canoniek/u);
  assert.throws(() => validateTypeScriptDebtManifest({ ...manifest, snapshotCount: 3 }),
    /snapshotcount verschilt/u);
  const reorderedBinding = {
    ...manifest,
    compiler: Object.fromEntries(Object.entries(manifest.compiler).reverse()),
  };
  assert.equal(inputReceiptSha256(reorderedBinding), manifest.inputReceiptSha256);
  assert.doesNotThrow(() => validateTypeScriptDebtManifest(reorderedBinding));
  assert.throws(() => validateTypeScriptDebtManifest({
    ...manifest,
    programInputs: { ...manifest.programInputs, sha256: '9'.repeat(64) },
  }), /contract is ongeldig/u);
  assert.throws(() => validateTypeScriptDebtManifest({
    ...manifest, compiler: { ...manifest.compiler, version: '5.9.4' },
  }), /contract is ongeldig/u);
  const reversedInventory = {
    ...manifest,
    sourceInventory: {
      ...manifest.sourceInventory,
      rootNames: [...manifest.sourceInventory.rootNames].reverse(),
    },
  };
  reversedInventory.inputReceiptSha256 = inputReceiptSha256(reversedInventory);
  assert.throws(() => validateTypeScriptDebtManifest(reversedInventory),
    /broninventaris is niet canoniek/u);
  const escapedInventory = {
    ...manifest,
    sourceInventory: {
      rootNames: ['../buiten.js', 'src/known-a.js'],
      count: 2,
      sha256: sha256('../buiten.js\0src/known-a.js\0'),
    },
  };
  escapedInventory.inputReceiptSha256 = inputReceiptSha256(escapedInventory);
  assert.throws(() => validateTypeScriptDebtManifest(escapedInventory),
    /broninventaris is niet canoniek/u);
});

test('snapshotcount en onverruimde maximumcap blijven onafhankelijk', () => {
  const { manifest, evidence } = contractFixture();
  const capped = { ...manifest, maximum: 1 };
  assert.doesNotThrow(() => validateTypeScriptDebtManifest(capped));
  const overCap = evaluateTypeScriptDebt({
    manifest: capped, evidence, baselineMaximum: 1,
  });
  assert.equal(overCap.ok, false);
  assert.match(overCap.failures.join('\n'), /overschrijdt het maximum/u);

  const reduced = evaluateTypeScriptDebt({
    manifest: capped,
    evidence: { ...evidence, diagnostics: evidence.diagnostics.slice(0, 1) },
    baselineMaximum: 1,
  });
  assert.equal(reduced.ok, true);
  assert.equal(reduced.resolved, 1);

  const generated = createTypeScriptDebtManifest(evidence, 1);
  assert.equal(generated.maximum, 1);
  assert.equal(generated.snapshotCount, 2);
});

test('AST-site blijft stabiel bij regelverschuiving en wijzigt bij een nieuwe named locatie', (t) => {
  const root = sourceFixture(t);
  const source = path.join(root, 'src/fixture.js');
  fs.writeFileSync(source, '/** @returns {number} */\nfunction known() { return "wrong"; }\n');
  const original = collectTypeScriptDebtEvidence(root);
  assert.equal(original.diagnostics.length, 1);
  assert.equal(original.programInputs.count > original.sourceInventory.count, true,
    'de binding mist de externe TypeScript-libbronnen van het programma');
  const manifest = createTypeScriptDebtManifest(original, 1);

  fs.writeFileSync(source, '// alleen verschoven regels\n\n/** @returns {number} */\nfunction known() { return "wrong"; }\n');
  const moved = collectTypeScriptDebtEvidence(root);
  assert.equal(moved.diagnostics[0].fingerprint, original.diagnostics[0].fingerprint);
  const movedResult = evaluateTypeScriptDebt({
    manifest, evidence: moved, baselineMaximum: 1,
  });
  assert.equal(movedResult.ok, false,
    'een stabiel diagnosepad mag geen gewijzigde programmabron als dezelfde snapshot attesteren');
  assert.match(movedResult.failures.join('\n'), /programmabronnen/u);

  fs.writeFileSync(source, '/** @returns {number} */\nfunction replacement() { return "wrong"; }\n');
  const relocated = collectTypeScriptDebtEvidence(root);
  const result = evaluateTypeScriptDebt({
    manifest, evidence: relocated, baselineMaximum: 1,
  });
  assert.equal(result.ok, false);
  assert.equal(result.unexpected.length, 1);

  fs.writeFileSync(path.join(root, 'src/untracked.js'), 'export const clean = true;\n');
  const expanded = collectTypeScriptDebtEvidence(root);
  assert.equal(evaluateTypeScriptDebt({
    manifest, evidence: expanded, baselineMaximum: 1,
  }).ok, false);
  assert.notDeepEqual(expanded.sourceInventory, original.sourceInventory);
});

test('meting weigert bron- en extendsmutatie direct na createProgram', async (t) => {
  await t.test('bronbytes', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    const source = path.join(root, 'src/fixture.js');
    fs.writeFileSync(source,
      '/** @type {number} */\nconst value = "voor-mutatie";\nvoid value;\n');
    const replacement = '/** @type {number} */\nconst value = 1;\nvoid value;\n';
    const gate = await composedGate(child, root,
      `  fs.writeFileSync(${JSON.stringify(source)}, ${JSON.stringify(replacement)});`);
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /TypeScript-input veranderde|projectReferences/u);
  });

  await t.test('extends-opties', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    const base = path.join(root, 'base.json');
    fs.writeFileSync(base, `${JSON.stringify({ compilerOptions: {
      allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
    } }, null, 2)}\n`);
    fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
      extends: './base.json', include: ['src/**/*.js'],
    }, null, 2)}\n`);
    fs.writeFileSync(path.join(root, 'src/fixture.js'),
      '/** @type {number} */\nconst value = "voor-mutatie";\nvoid value;\n');
    const replacement = `${JSON.stringify({ compilerOptions: {
      allowJs: true, checkJs: false, noEmit: true, skipLibCheck: true, types: [],
    } }, null, 2)}\n`;
    const gate = await composedGate(child, root,
      `  fs.writeFileSync(${JSON.stringify(base)}, ${JSON.stringify(replacement)});`);
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /TypeScript-input veranderde/u);
  });

  await t.test('in-memory compileropties', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.writeFileSync(path.join(root, 'src/fixture.js'),
      '/** @type {number} */\nconst value = "voor-mutatie";\nvoid value;\n');
    const gate = await composedGate(child, root, '  parsed.options.checkJs = false;');
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /TypeScript-input veranderde/u);
  });

  await t.test('in-memory projectreferences', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.mkdirSync(path.join(root, 'referenced'));
    fs.writeFileSync(path.join(root, 'referenced/tsconfig.json'),
      '{"compilerOptions":{"composite":true},"files":[]}\n');
    fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
      compilerOptions: {
        allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
      },
      include: ['src/**/*.js'], references: [{ path: './referenced' }],
    }, null, 2)}\n`);
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    const gate = await composedGate(child, root,
      `  parsed.projectReferences[0].path = ${JSON.stringify(path.join(root, 'vervalst'))};`);
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /TypeScript-input veranderde|projectReferences/u);
  });
});

test('meting bindt lockfile en relevante @types-package tijdens programma-opbouw', async (t) => {
  await t.test('package-lock', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    const lock = path.join(root, 'package-lock.json');
    fs.writeFileSync(path.join(root, 'package.json'), '{"type":"module"}\n');
    fs.writeFileSync(lock, '{"lockfileVersion":3,"packages":{}}\n');
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    const gate = await composedGate(child, root,
      `  fs.writeFileSync(${JSON.stringify(lock)}, '{"lockfileVersion":3,"packages":{"forged":{}}}\\n');`);
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /TypeScript-input veranderde/u);
  });

  await t.test('@types-package', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    const typeRoot = path.join(root, 'node_modules/@types/fixture-types');
    fs.mkdirSync(typeRoot, { recursive: true });
    const packageJson = path.join(typeRoot, 'package.json');
    fs.writeFileSync(packageJson,
      '{"name":"@types/fixture-types","version":"1.0.0","types":"index.d.ts"}\n');
    fs.writeFileSync(path.join(typeRoot, 'index.d.ts'), 'declare const fixtureGlobal: boolean;\n');
    fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
      compilerOptions: {
        allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true,
        types: ['fixture-types'],
      },
      include: ['src/**/*.js'],
    }, null, 2)}\n`);
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'void fixtureGlobal;\n');
    const gate = await composedGate(child, root,
      `  fs.writeFileSync(${JSON.stringify(packageJson)}, '{"name":"@types/fixture-types","version":"9.9.9","types":"index.d.ts"}\\n');`);
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /TypeScript-input veranderde/u);
  });

  await t.test('compilerboom', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptCopy(root);
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    const compilerReadme = path.join(root, 'node_modules/typescript/README.md');
    const replacement = `${fs.readFileSync(compilerReadme, 'utf8')}\ngewijzigd\n`;
    const gate = await composedGate(child, root,
      `  fs.writeFileSync(${JSON.stringify(compilerReadme)}, ${JSON.stringify(replacement)});`);
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /TypeScript-input veranderde/u);
  });
});

test('identieke siblingdiagnoses hebben botsingsvrije AST-paden en laten geen substitutie toe', (t) => {
  const root = sourceFixture(t);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), `
/** @param {number} value */
function accepts(value) { void value; }
function known() {
  accepts('zelfde');
  accepts('zelfde');
  accepts('zelfde');
}
void known;
`);
  const full = collectTypeScriptDebtEvidence(root);
  assert.equal(full.diagnostics.length, 3);
  assert.equal(new Set(full.diagnostics.map(({ fingerprint }) => fingerprint)).size, 3,
    'identieke siblings botsen op hetzelfde sitefingerprint');
  const original = { ...full, diagnostics: Object.freeze(full.diagnostics.slice(0, 2)) };
  const manifest = createTypeScriptDebtManifest(original, 2);
  const substituted = {
    ...full,
    diagnostics: Object.freeze([full.diagnostics[0], full.diagnostics[2]]),
  };
  const result = evaluateTypeScriptDebt({ manifest, evidence: substituted, baselineMaximum: 2 });
  assert.equal(result.ok, false);
  assert.equal(result.unexpected.length, 1);
});

test('caller-minted reviewed-release blijft zonder extern rootreceipt onbruikbaar', () => {
  const { manifest, evidence } = contractFixture();
  const forged = {
    ...manifest,
    provenance: { scope: 'reviewed-release', releaseEligible: true },
  };
  const result = evaluateTypeScriptDebt({
    manifest: forged, evidence, baselineMaximum: 2, releaseMode: true,
  });
  assert.equal(result.ok, false);
  assert.match(result.failures.join('\n'), /rootreceipt|externe attestatie/u);
});

test('workerenvelope accepteert alleen herberekenbare canonieke evidence', () => {
  const { evidence } = contractFixture();
  const message = 'canonieke workerevidence';
  const identity = {
    contract: 'EvaTypeScriptDiagnostic/v3',
    category: 'error',
    code: 2304,
    file: 'src/canonical.js',
    messageSha256: sha256(message),
    siteSha256: sha256('canonical-site'),
  };
  const diagnostic = {
    ...identity,
    fingerprint: sha256(JSON.stringify(identity)),
    line: 1,
    message,
  };
  const canonical = { ...evidence, diagnostics: [diagnostic] };
  assert.equal(validateTypeScriptEvidence(canonical), canonical);
  assert.throws(() => validateTypeScriptEvidence({
    ...canonical,
    diagnostics: [{ ...diagnostic, message: 'forged canonical-looking JSON' }],
  }), /evidence.*niet canoniek|message/iu);
  assert.throws(() => validateTypeScriptEvidence({
    ...canonical,
    diagnostics: [{ ...diagnostic, file: '../outside.js' }],
  }), /evidence.*niet canoniek|pad/iu);
});

test('ESLint-subproces faalt gesloten bij exit 2, signal, timeout en truncatie', async (t) => {
  const cases = [
    {
      name: 'geldige JSON met exit 2',
      source: 'process.stdout.write("[]"); process.exitCode = 2;\n',
    },
    {
      name: 'signal/null-status',
      source: 'process.stdout.write("[]"); process.kill(process.pid, "SIGTERM");\n',
    },
    {
      name: 'timeout',
      source: 'process.stdout.write("[]"); Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5000);\n',
      transform: (source) => source.replace(
        'const SUBPROCESS_TIMEOUT_MS = 120_000;',
        'const SUBPROCESS_TIMEOUT_MS = 50;',
      ),
    },
    {
      name: 'afgekapt resultaat',
      source: 'process.stdout.write(JSON.stringify([{ messages: [], errorCount: 0, warningCount: 0, padding: "x".repeat(4096) }]));\n',
      transform: (source) => source.replace(
        'maxBuffer: 64 * 1024 * 1024,',
        'maxBuffer: 256,',
      ),
    },
  ];
  for (const spec of cases) {
    await t.test(spec.name, (child) => {
      const root = fixture(child);
      writeFakeEslint(root, spec.source);
      if (spec.transform) {
        const gatePath = path.join(root, 'scripts/quality-gate.mjs');
        const transformed = spec.transform(fs.readFileSync(gatePath, 'utf8'));
        fs.writeFileSync(gatePath, transformed);
      }
      const started = Date.now();
      const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'lint'], {
        cwd: root, encoding: 'utf8', timeout: 2_000,
      });
      assert.notEqual(result.status, 0,
        `ESLint-foutstatus werd geaccepteerd:\n${result.stdout}${result.stderr}`);
      assert.equal(Date.now() - started < 1_500, true, 'gate bleef hangen na ESLint-timeout');
    });
  }
});

function configureProjectReferences(root) {
  fs.mkdirSync(path.join(root, 'referenced/src'), { recursive: true });
  fs.mkdirSync(path.join(root, 'referenced/nested'), { recursive: true });
  fs.writeFileSync(path.join(root, 'referenced/src/item.js'),
    '/** @type {number} */\nexport const item = "ref-fout";\n');
  fs.writeFileSync(path.join(root, 'referenced/nested/tsconfig.json'), `${JSON.stringify({
    compilerOptions: { composite: true }, files: [],
  }, null, 2)}\n`);
  fs.writeFileSync(path.join(root, 'referenced/tsconfig.json'), `${JSON.stringify({
    compilerOptions: {
      allowJs: true, checkJs: true, composite: true, declaration: true, types: [],
    },
    include: ['src/**/*.js'],
    references: [{ path: './nested' }],
  }, null, 2)}\n`);
  fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
    compilerOptions: {
      allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
    },
    include: ['src/**/*.js'], references: [{ path: './referenced' }],
  }, null, 2)}\n`);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const rootValue = true;\n');
}

test('standalone debtmeting weigert recursieve projectReferences en buitenrepo-references', async (t) => {
  await t.test('contained projectReference zonder volledige recursieve receipt', (child) => {
    const root = sourceFixture(child);
    configureProjectReferences(root);
    assert.throws(() => collectTypeScriptDebtEvidence(root),
      /projectReferences|recursieve projectbinding/u);
  });

  for (const target of ['config-opties', 'referenced-bestand', 'geneste-reference']) {
    await t.test(target, async (child) => {
      const root = sourceFixture(child);
      installTypeScriptLink(root);
      configureProjectReferences(root);
      let file;
      let replacement;
      if (target === 'config-opties') {
        file = path.join(root, 'referenced/tsconfig.json');
        replacement = `${JSON.stringify({
          compilerOptions: {
            allowJs: true, checkJs: false, composite: true, declaration: true, types: [],
          },
          include: ['src/**/*.js'], references: [{ path: './nested' }],
        }, null, 2)}\n`;
      } else if (target === 'referenced-bestand') {
        file = path.join(root, 'referenced/src/item.js');
        replacement = 'export const item = 1;\n';
      } else {
        file = path.join(root, 'referenced/nested/tsconfig.json');
        replacement = `${JSON.stringify({
          compilerOptions: { composite: true }, files: [],
          references: [{ path: '../../vervalst' }],
        }, null, 2)}\n`;
      }
      const gate = await composedGate(child, root,
        `  fs.writeFileSync(${JSON.stringify(file)}, ${JSON.stringify(replacement)});`);
      assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
        /projectReferences|TypeScript-input veranderde/u);
    });
  }

  await t.test('reference buiten repository', (child) => {
    const root = sourceFixture(child);
    const outside = fs.mkdtempSync('/tmp/eva-quality-ref-outside-');
    fs.chmodSync(outside, 0o700);
    child.after(() => fs.rmSync(outside, { recursive: true, force: true }));
    fs.writeFileSync(path.join(outside, 'tsconfig.json'),
      '{"compilerOptions":{"composite":true},"files":[]}\n');
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
      compilerOptions: {
        allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
      },
      include: ['src/**/*.js'], references: [{ path: outside }],
    }, null, 2)}\n`);
    assert.throws(() => collectTypeScriptDebtEvidence(root),
      /projectReferences|repositorygrens|gebonden grenzen/u);
  });
});

test('reeds uitgevoerde compiler A kan niet als latere compilerboom B worden geattesteerd', (t) => {
  const root = sourceFixture(t);
  installTypeScriptCopy(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
  const fixtureRequire = createRequire(path.join(root, 'scripts/preload.cjs'));
  const entrypoint = fixtureRequire.resolve('typescript');
  const source = fs.readFileSync(path.join(projectRoot, 'scripts/quality-gate.mjs'), 'utf8');
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.writeFileSync(path.join(root, 'scripts/quality-gate.mjs'), source);
  const result = spawnSync(process.execPath, [
    '--input-type=module', '-e',
    `import { createRequire } from 'node:module'; import fs from 'node:fs'; const require = createRequire(import.meta.url); const compiler = require(${JSON.stringify(entrypoint)}); if (compiler.version !== '5.9.3') process.exit(9); fs.writeFileSync(${JSON.stringify(entrypoint)}, fs.readFileSync(${JSON.stringify(entrypoint)}, 'utf8') + '\\n// compiler B\\n'); const { collectTypeScriptDebtEvidence } = await import('./scripts/quality-gate.mjs'); collectTypeScriptDebtEvidence(process.cwd());`,
  ], { cwd: root, encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  assert.match(`${result.stdout}${result.stderr}`,
    /compiler.*(?:voor|eerder).*geladen|compilerbinding|TypeScript-input veranderde/iu);
});

test('compilerentrypoint wordt uit gepinde bytes uitgevoerd bij een A-B-A padswap', async (t) => {
  const root = sourceFixture(t);
  installTypeScriptCopy(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
  const compilerPath = path.join(root, 'node_modules/typescript/lib/typescript.js');
  const marker = path.join(root, 'compiler-b-executed');
  const gate = await composedGateAround(t, root, {
    transform: (source) => {
      let transformed = source.replace(
        'const HASH_PATTERN = /^[a-f0-9]{64}$/u;',
        `const HASH_PATTERN = /^[a-f0-9]{64}$/u;
let compilerSwapCount = 0;
export const compilerSwapProbe = () => compilerSwapCount;`,
      );
      const before = `    compilerSwapCount += 1;
    const compilerAlpha = fs.readFileSync(${JSON.stringify(compilerPath)}, 'utf8');
    fs.writeFileSync(${JSON.stringify(compilerPath)}, compilerAlpha
      + ${JSON.stringify(`\nrequire('fs').writeFileSync(${JSON.stringify(marker)}, 'B');\n`)});`;
      const after = `    fs.writeFileSync(${JSON.stringify(compilerPath)}, compilerAlpha);`;
      if (transformed.includes('const sealedCompilerModule = new Module(typescriptEntrypoint);')) {
        transformed = transformed
          .replace('    const sealedCompilerModule = new Module(typescriptEntrypoint);',
            `${before}\n    const sealedCompilerModule = new Module(typescriptEntrypoint);`)
          .replace('    ts = sealedCompilerModule.exports;',
            `    ts = sealedCompilerModule.exports;\n${after}`);
      } else {
        transformed = transformed.replace('    ts = require(typescriptEntrypoint);',
          `${before}\n    ts = require(typescriptEntrypoint);\n${after}`);
      }
      return transformed;
    },
  });
  assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
    /TypeScript-input veranderde|compilerboom|compilerbinding/u);
  assert.equal(gate.compilerSwapProbe(), 1, 'compiler-swaphook werd niet exact eenmaal geraakt');
  assert.equal(fs.existsSync(marker), false,
    'live compilerpad B werd uitgevoerd in plaats van de gepinde compilerbytes A');
});

test('dependencytopologie, symlinktargets, negative lookups en geneste packagegrenzen zijn inputs', async (t) => {
  await t.test('symlinktarget wisselt en wordt hersteld', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.mkdirSync(path.join(root, 'hidden'));
    const targetA = path.join(root, 'hidden/a');
    const targetB = path.join(root, 'hidden/b');
    fs.writeFileSync(targetA, 'a\n');
    fs.writeFileSync(targetB, 'b\n');
    const linked = path.join(root, 'node_modules/linked');
    fs.symlinkSync(targetA, linked);
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    const gate = await composedGateAround(child, root, {
      beforeCreateProgram: `  fs.unlinkSync(${JSON.stringify(linked)}); fs.symlinkSync(${JSON.stringify(targetB)}, ${JSON.stringify(linked)});`,
      afterCreateProgram: `  fs.unlinkSync(${JSON.stringify(linked)}); fs.symlinkSync(${JSON.stringify(targetA)}, ${JSON.stringify(linked)});`,
    });
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /dependency|topologie|TypeScript-input veranderde/u);
  });

  await t.test('transiënte symlinktopologie', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.mkdirSync(path.join(root, 'hidden'));
    const target = path.join(root, 'hidden/target');
    const linked = path.join(root, 'node_modules/transient');
    fs.writeFileSync(target, 'target\n');
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    const gate = await composedGateAround(child, root, {
      beforeCreateProgram: `  fs.symlinkSync(${JSON.stringify(target)}, ${JSON.stringify(linked)});`,
      afterCreateProgram: `  fs.unlinkSync(${JSON.stringify(linked)});`,
    });
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /dependency|topologie|TypeScript-input veranderde/u);
  });

  await t.test('hogere-prioriteit negative lookup verschijnt tijdelijk', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.mkdirSync(path.join(root, 'hidden'));
    fs.writeFileSync(path.join(root, 'hidden/priority-target.d.ts'),
      'export declare const value: number;\n');
    fs.writeFileSync(path.join(root, 'src/fixture.js'),
      "import { value } from 'priority-pkg';\nvoid value;\n");
    const transient = path.join(root, 'src/node_modules/priority-pkg');
    const packageJson = JSON.stringify({
      name: 'priority-pkg', version: '1.0.0', types: '../../../hidden/priority-target.d.ts',
    });
    const gate = await composedGateAround(child, root, {
      beforeCreateProgram: `  fs.mkdirSync(${JSON.stringify(transient)}, { recursive: true }); fs.writeFileSync(${JSON.stringify(path.join(transient, 'package.json'))}, ${JSON.stringify(packageJson)});`,
      afterCreateProgram: `  fs.rmSync(${JSON.stringify(path.join(root, 'src/node_modules'))}, { recursive: true, force: true });`,
    });
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /dependency|topologie|TypeScript-input veranderde/u);
  });

  await t.test('geneste repository-package.json verandert en wordt hersteld', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    const nestedPackage = path.join(root, 'src/package.json');
    const original = '{"type":"commonjs"}\n';
    fs.writeFileSync(nestedPackage, original);
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    const gate = await composedGateAround(child, root, {
      beforeCreateProgram: `  fs.writeFileSync(${JSON.stringify(nestedPackage)}, '{"type":"module"}\\n');`,
      afterCreateProgram: `  fs.writeFileSync(${JSON.stringify(nestedPackage)}, ${JSON.stringify(original)});`,
    });
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /dependency|package|TypeScript-input veranderde/u);
  });
});

test('dubbele gelijknamige declaraties behouden een collision-safe externe AST-positie', (t) => {
  const root = sourceFixture(t);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), `
/** @returns {number} */
function duplicate() { return 'zelfde'; }
/** @returns {number} */
function duplicate() { return 'zelfde'; }
/** @returns {number} */
function duplicate() { return 'zelfde'; }
void duplicate;
`);
  const evidence = collectTypeScriptDebtEvidence(root);
  const returns = evidence.diagnostics.filter(({ code }) => code === 2322);
  assert.equal(returns.length, 3);
  assert.equal(new Set(returns.map(({ fingerprint }) => fingerprint)).size, 3,
    'gelijknamige declaraties botsen op hetzelfde interne sitepad');
  const original = { ...evidence, diagnostics: Object.freeze(returns.slice(0, 2)) };
  const manifest = createTypeScriptDebtManifest(original, 2);
  const result = evaluateTypeScriptDebt({
    manifest,
    evidence: { ...evidence, diagnostics: Object.freeze([returns[0], returns[2]]) },
    baselineMaximum: 2,
  });
  assert.equal(result.ok, false);
  assert.equal(result.unexpected.length, 1);
});

test('manifesttellingen gebruiken safe integers en harde bovengrenzen', () => {
  const { manifest } = contractFixture();
  const unsafe = Number.MAX_SAFE_INTEGER + 1;
  assert.throws(() => validateTypeScriptDebtManifest({ ...manifest, maximum: unsafe }),
    /safe integer|bovengrens|contract is ongeldig/u);
  const oversizedDiagnostic = { ...manifest.diagnostics[0], count: unsafe };
  assert.throws(() => validateTypeScriptDebtManifest({
    ...manifest,
    maximum: unsafe,
    snapshotCount: unsafe,
    diagnostics: [oversizedDiagnostic],
  }), /safe integer|bovengrens|contract is ongeldig/u);
  for (const mutation of [
    { compiler: { ...manifest.compiler, treeCount: unsafe } },
    { config: { ...manifest.config, filesCount: unsafe } },
    { programInputs: { ...manifest.programInputs, count: unsafe } },
    { dependencies: { ...manifest.dependencies, count: unsafe } },
  ]) {
    const candidate = { ...manifest, ...mutation };
    candidate.inputReceiptSha256 = inputReceiptSha256(candidate);
    assert.throws(() => validateTypeScriptDebtManifest(candidate),
      /safe integer|bovengrens|contract is ongeldig/u);
  }
});

test('bestand-, diepte-, byte-, rij- en tijdcaps falen vóór attestatie', async (t) => {
  const capCases = [
    ['bestandcap', 'const MAX_INPUT_FILES = 100_000;', 'const MAX_INPUT_FILES = 2;'],
    ['dieptecap', 'const MAX_INPUT_DEPTH = 64;', 'const MAX_INPUT_DEPTH = 2;'],
    ['bytecap', 'const MAX_INPUT_BYTES = 536_870_912;', 'const MAX_INPUT_BYTES = 64;'],
  ];
  for (const [name, needle, replacement] of capCases) {
    await t.test(name, async (child) => {
      const root = sourceFixture(child);
      installTypeScriptLink(root);
      if (name === 'dieptecap') {
        fs.mkdirSync(path.join(root, 'src/a/b/c'), { recursive: true });
        fs.writeFileSync(path.join(root, 'src/a/b/c/deep.js'), 'export const deep = true;\n');
      }
      fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
      const gate = await composedGateAround(child, root, {
        transform: (source) => source.replace(needle, replacement),
      });
      assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
        /bovengrens|budget|te veel|te diep/u);
    });
  }

  await t.test('manifestrijcap', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    const gate = await composedGateAround(child, root, {
      transform: (source) => source.replace(
        'const MAX_MANIFEST_ROWS = 100_000;', 'const MAX_MANIFEST_ROWS = 2;',
      ),
    });
    const { manifest } = contractFixture();
    const diagnostics = [...manifest.diagnostics, manifestEntry('known-c')]
      .sort((left, right) => left.fingerprint.localeCompare(right.fingerprint));
    assert.throws(() => gate.validateTypeScriptDebtManifest({
      ...manifest, maximum: 3, snapshotCount: 3, diagnostics,
    }), /bovengrens|manifestrij|te veel|contract is ongeldig/u);
  });

  await t.test('meetdeadline', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    const gate = await composedGateAround(child, root, {
      afterCreateProgram: '  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 30);',
      transform: (source) => source.replace(
        'const TYPECHECK_MEASUREMENT_TIMEOUT_MS = 120_000;',
        'const TYPECHECK_MEASUREMENT_TIMEOUT_MS = 5;',
      ),
    });
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root), /deadline|timeout|tijdsb budget/iu);
  });
});

test('manifestgeneratie sluit alleen eigen content uit en fresh verify bindt pad/topologie', (t) => {
  const root = sourceFixture(t);
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.copyFileSync(path.join(projectRoot, 'scripts/quality-gate.mjs'),
    path.join(root, 'scripts/quality-gate.mjs'));
  fs.mkdirSync(path.join(root, 'node_modules'));
  fs.symlinkSync(path.join(projectRoot, 'node_modules/typescript'),
    path.join(root, 'node_modules/typescript'));
  fs.writeFileSync(path.join(root, 'src/fixture.js'),
    '/** @type {number} */\nconst value = "fout";\nvoid value;\n');
  const manifestPath = path.join(root, 'typescript-diagnostic-debt.json');
  fs.writeFileSync(manifestPath, '{}\n');
  const evidence = collectTypeScriptDebtEvidence(root);
  const manifest = createTypeScriptDebtManifest(evidence, evidence.diagnostics.length);
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  const verifyScript = `
import fs from 'node:fs';
import { collectTypeScriptDebtEvidence, evaluateTypeScriptDebt } from './scripts/quality-gate.mjs';
const manifest = JSON.parse(fs.readFileSync('typescript-diagnostic-debt.json', 'utf8'));
const evidence = collectTypeScriptDebtEvidence(process.cwd());
const result = evaluateTypeScriptDebt({ manifest, evidence, baselineMaximum: manifest.maximum });
if (!result.ok) { console.error(result.failures.join('\\n')); process.exit(1); }
`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', verifyScript], {
    cwd: root, encoding: 'utf8', timeout: 10_000,
  });
  assert.equal(result.status, 0,
    `fresh verify kon een zojuist geschreven manifest niet valideren:\n${result.stderr}`);

  fs.renameSync(manifestPath, path.join(root, 'typescript-diagnostic-debt-moved.json'));
  fs.mkdirSync(manifestPath);
  const moved = spawnSync(process.execPath, ['--input-type=module', '-e', verifyScript], {
    cwd: root, encoding: 'utf8', timeout: 10_000,
  });
  assert.notEqual(moved.status, 0, 'manifestpad/type/topologie was niet gebonden');
});

test('manifest -> repository-inventaris -> typecheck heeft een stabiel bewijs-fixed-point', (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
  fs.mkdirSync(path.join(root, 'docs'));
  const manifestPath = path.join(root, 'typescript-diagnostic-debt.json');
  fs.writeFileSync(manifestPath, '{}\n');
  const otherGenerated = path.join(root, 'docs/eva-runtime-contract-map.generated.json');
  fs.writeFileSync(otherGenerated, '{"generation":1}\n');

  const beforeInventory = collectTypeScriptDebtEvidence(root);
  const manifest = createTypeScriptDebtManifest(
    beforeInventory, beforeInventory.diagnostics.length,
  );
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  const writeInventoryGeneration = (generation) => {
    for (const [name, contents] of [
      ['eva-repository-inventory.generated.json', `{"generation":${generation}}\n`],
      ['eva-repository-inventory.generated.md', `# generation ${generation}\n`],
    ]) {
      const target = path.join(root, 'docs', name);
      const temporary = `${target}.tmp`;
      fs.writeFileSync(temporary, contents, { flag: 'wx' });
      fs.renameSync(temporary, target);
    }
  };

  writeInventoryGeneration(1);
  const afterFirstInventory = collectTypeScriptDebtEvidence(root);
  assert.deepEqual(afterFirstInventory.dependencies, beforeInventory.dependencies,
    'eerste inventory-output verscheen ten onrechte in de TypeScript-dependencyreceipt');
  assert.equal(afterFirstInventory.inputReceiptSha256, beforeInventory.inputReceiptSha256);
  assert.equal(evaluateTypeScriptDebt({
    manifest, evidence: afterFirstInventory, baselineMaximum: manifest.maximum,
  }).ok, true, 'inventorygeneratie maakte het zojuist geschreven debtmanifest stale');

  writeInventoryGeneration(2);
  const afterSecondInventory = collectTypeScriptDebtEvidence(root);
  assert.deepEqual(afterSecondInventory.dependencies, beforeInventory.dependencies,
    'atomische inventory-regeneratie veranderde de TypeScript-dependencyreceipt');
  assert.equal(afterSecondInventory.inputReceiptSha256, beforeInventory.inputReceiptSha256);

  fs.writeFileSync(otherGenerated, '{"generation":2}\n');
  const afterOtherGeneratedChange = collectTypeScriptDebtEvidence(root);
  assert.notDeepEqual(afterOtherGeneratedChange.dependencies, beforeInventory.dependencies,
    'een ander gegenereerd architectuurbestand werd ten onrechte uitgesloten');
});

test('repository-inventory self-output kan geen semantische TypeScript-input worden', (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  fs.mkdirSync(path.join(root, 'docs'));
  fs.writeFileSync(path.join(root, 'docs/eva-repository-inventory.generated.json'), '{}\n');
  fs.writeFileSync(path.join(root, 'src/fixture.js'), [
    "import inventory from '../docs/eva-repository-inventory.generated.json';",
    'void inventory;',
    '',
  ].join('\n'));
  fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
    compilerOptions: {
      allowJs: true, checkJs: true, noEmit: true, resolveJsonModule: true,
      module: 'esnext', moduleResolution: 'node', types: [],
    },
    include: ['src/**/*.js'],
  }, null, 2)}\n`);

  assert.throws(() => collectTypeScriptDebtEvidence(root),
    /repository-inventaris|niet-compilerinput|semantische input/iu);
});

test('compiler cache ontstaan na gate-import maar vóór collect faalt onmiddellijk gesloten', (t) => {
  const root = sourceFixture(t);
  installTypeScriptCopy(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.copyFileSync(path.join(projectRoot, 'scripts/quality-gate.mjs'),
    path.join(root, 'scripts/quality-gate.mjs'));
  const script = `
import { createRequire } from 'node:module';
import fs from 'node:fs';
import { collectTypeScriptDebtEvidence } from './scripts/quality-gate.mjs';
const require = createRequire(import.meta.url);
const entrypoint = require.resolve('typescript');
const compilerA = require(entrypoint);
if (compilerA.version !== '5.9.3') process.exit(9);
fs.writeFileSync(entrypoint, fs.readFileSync(entrypoint, 'utf8') + '\\n// compiler B\\n');
collectTypeScriptDebtEvidence(process.cwd());
`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    cwd: root, encoding: 'utf8', timeout: 10_000,
  });
  assert.notEqual(result.status, 0, 'na import geladen compiler A werd als boom B geattesteerd');
  assert.match(`${result.stdout}${result.stderr}`, /compiler.*geladen|compilerbinding|input veranderde/iu);
});

test('uitgesloten roots kunnen geen semantische TypeScript-input verbergen', async (t) => {
  await t.test('data package scope A-B-A', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.mkdirSync(path.join(root, 'data/src'), { recursive: true });
    const packageJson = path.join(root, 'data/package.json');
    fs.writeFileSync(packageJson, '{"type":"commonjs"}\n');
    fs.writeFileSync(path.join(root, 'data/src/value.js'), 'export const value = true;\n');
    fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
      compilerOptions: {
        allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
      },
      include: ['data/src/**/*.js'],
    }, null, 2)}\n`);
    const gate = await composedGateAround(child, root, {
      beforeCreateProgram: `  fs.writeFileSync(${JSON.stringify(packageJson)}, '{"type":"module"}\\n');`,
      afterCreateProgram: `  fs.writeFileSync(${JSON.stringify(packageJson)}, '{"type":"commonjs"}\\n');`,
    });
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /uitgesloten root|TypeScript-input veranderde|repositorytopologie/u);
  });

  await t.test('dist higher-priority kandidaat verschijnt tijdelijk', async (child) => {
    const root = sourceFixture(child);
    installTypeScriptLink(root);
    fs.mkdirSync(path.join(root, 'dist'));
    fs.writeFileSync(path.join(root, 'src/fixture.js'),
      "import { value } from '../dist/candidate.js';\nvoid value;\n");
    const candidate = path.join(root, 'dist/candidate.js');
    const gate = await composedGateAround(child, root, {
      beforeCreateProgram: `  fs.writeFileSync(${JSON.stringify(candidate)}, 'export const value = true;\\n');`,
      afterCreateProgram: `  fs.rmSync(${JSON.stringify(candidate)}, { force: true });`,
    });
    assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
      /uitgesloten root|TypeScript-input veranderde|repositorytopologie/u);
  });

  await t.test('excluded symlink/config source', (child) => {
    const root = sourceFixture(child);
    const outside = fs.mkdtempSync('/tmp/eva-quality-excluded-config-');
    fs.chmodSync(outside, 0o700);
    child.after(() => fs.rmSync(outside, { recursive: true, force: true }));
    fs.mkdirSync(path.join(root, 'coverage'));
    fs.writeFileSync(path.join(outside, 'base.json'),
      '{"compilerOptions":{"allowJs":true,"checkJs":true,"noEmit":true,"types":[]}}\n');
    fs.symlinkSync(path.join(outside, 'base.json'), path.join(root, 'coverage/base.json'));
    fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
    fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
      extends: './coverage/base.json', include: ['src/**/*.js'],
    }, null, 2)}\n`);
    assert.throws(() => collectTypeScriptDebtEvidence(root),
      /uitgesloten root|symlink|gebonden grenzen|repositorygrens/u);
  });
});

test('typecheck parent dwingt SIGKILL-deadline af rond een niet-terugkerende meetchild', (t) => {
  const root = fixture(t);
  const gatePath = path.join(root, 'scripts/quality-gate.mjs');
  let source = fs.readFileSync(gatePath, 'utf8');
  source = source.replace('const TYPECHECK_MEASUREMENT_TIMEOUT_MS = 120_000;',
    'const TYPECHECK_MEASUREMENT_TIMEOUT_MS = 50;');
  source = source.replace('export function collectTypeScriptDebtEvidence(expectedRoot = root, configName = \'tsconfig.json\') {',
    `export function collectTypeScriptDebtEvidence(expectedRoot = root, configName = 'tsconfig.json') {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0);`);
  fs.writeFileSync(gatePath, source);
  const started = Date.now();
  const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
    cwd: root, encoding: 'utf8', timeout: 2_000,
  });
  assert.notEqual(result.status, 0);
  assert.equal(Date.now() - started < 1_500, true, 'ouderproces doodde de meetchild niet tijdig');
  assert.match(`${result.stdout}${result.stderr}`, /SIGKILL|deadline|timeout|meetchild/iu);
});

test('diagnosecap stopt vóór identitymapping van een explosieve lijst', async (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'),
    '/** @type {number} */\nconst value = "fout";\nvoid value;\n');
  const gate = await composedGateAround(t, root, {
    beforeCreateProgram: '  let identityCalls = 0;',
    afterCreateProgram: '',
    transform: (source) => source
      .replace('const MAX_DIAGNOSTIC_COUNT = 1_000_000;',
        'const MAX_DIAGNOSTIC_COUNT = 2;')
      .replace('budget.check();\n  if (raw.length > MAX_DIAGNOSTIC_COUNT) {',
        `budget.check();
  raw.push(raw[0], raw[0]);
  if (raw.length > MAX_DIAGNOSTIC_COUNT) {`)
      .replace('const diagnostics = raw.map((diagnostic) => diagnosticIdentity(diagnostic, expectedRoot));',
        `const diagnostics = raw.map((diagnostic) => { identityCalls += 1; if (identityCalls > 2) throw new Error('identitymapping-exploded'); return diagnosticIdentity(diagnostic, expectedRoot); });`),
  });
  assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
    /diagnostiek.*bovengrens/u);
});

test('parent weigert baseline, manifest, gate en private worker die na childsnapshot uiteenlopen', (t) => {
  const root = fixture(t);
  const gatePath = path.join(root, 'scripts/quality-gate.mjs');
  let source = fs.readFileSync(gatePath, 'utf8');
  const evidenceReturn = `  return Object.freeze({
    ...evidence,
    inputReceiptSha256: inputReceiptSha256(evidence),
  });`;
  assert.equal(source.includes(evidenceReturn), true);
  source = source.replace(evidenceReturn, `  const measuredEvidence = Object.freeze({
    ...evidence,
    inputReceiptSha256: inputReceiptSha256(evidence),
  });
  const replacementManifest = createTypeScriptDebtManifest(
    measuredEvidence, measuredEvidence.diagnostics.length,
  );
  fs.writeFileSync(resolve(expectedRoot, TYPECHECK_DEBT_MANIFEST),
    JSON.stringify(replacementManifest));
  fs.writeFileSync(resolve(expectedRoot, 'quality-baseline.json'), JSON.stringify({
    typescriptDiagnostics: measuredEvidence.diagnostics.length,
    lintWarnings: 0,
    complexityWarnings: 0,
  }));
  fs.appendFileSync(gateFile, '\\n// gate/worker B na snapshot\\n');
  return measuredEvidence;`);
  fs.writeFileSync(gatePath, source);

  const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
    cwd: root, encoding: 'utf8', timeout: 10_000,
  });
  assert.notEqual(result.status, 0,
    `A/B/C-controlsnapshot werd geaccepteerd:\n${result.stdout}${result.stderr}`);
});

test('publieke worker-argv en geërfde Node-loaderomgeving kunnen geen evidence minten', async (t) => {
  await t.test('publieke argv-worker', (child) => {
    const root = fixture(child);
    const result = spawnSync(process.execPath, [
      'scripts/quality-gate.mjs', '__typecheck-evidence-worker',
    ], { cwd: root, encoding: 'utf8', timeout: 10_000 });
    assert.notEqual(result.status, 0,
      `publieke worker gaf evidence vrij: ${result.stdout.slice(0, 200)}`);
  });

  await t.test('publieke Node execArgv-loader', (child) => {
    const root = fixture(child);
    const preload = path.join(root, 'argv-preload.cjs');
    const marker = path.join(root, 'argv-preload-ran');
    fs.writeFileSync(preload,
      `require('node:fs').writeFileSync(${JSON.stringify(marker)}, 'loaded');\n`);
    const result = spawnSync(process.execPath, [
      '--require', preload, 'scripts/quality-gate.mjs', 'typecheck',
    ], { cwd: root, encoding: 'utf8', timeout: 10_000 });
    assert.equal(fs.existsSync(marker), true, 'execArgv-canary werd niet geladen');
    assert.notEqual(result.status, 0, 'publieke Node loaderflag passeerde de gate');
    assert.match(`${result.stdout}${result.stderr}`, /execArgv|loaderflags/iu);
  });

  await t.test('publiek vervalste private bootstrap', (child) => {
    const root = fixture(child);
    const preload = path.join(root, 'bootstrap-preload.cjs');
    fs.writeFileSync(preload, `
globalThis.__EVA_QUALITY_GATE_PRIVATE_BOOTSTRAP__ = {
  root: ${JSON.stringify(root)},
  gateFile: ${JSON.stringify(path.join(root, 'scripts/quality-gate.mjs'))},
  requireFrom: ${JSON.stringify(path.join(root, 'scripts/quality-gate.mjs'))},
};
`);
    const result = spawnSync(process.execPath, [
      '--require', preload, 'scripts/quality-gate.mjs', 'typecheck',
    ], { cwd: root, encoding: 'utf8', timeout: 10_000 });
    assert.notEqual(result.status, 0, 'publieke preload vervalste private worker-mode');
    assert.match(`${result.stdout}${result.stderr}`, /private.*bootstrap|workerbootstrap|publieke argv/iu);
  });

  for (const variable of ['NODE_OPTIONS', 'NODE_PATH']) {
    await t.test(variable, (child) => {
      const root = fixture(child);
      const preload = path.join(root, 'forged-worker.cjs');
      fs.writeFileSync(preload, `
if (process.argv[2] === '__typecheck-evidence-worker') {
  const fs = require('node:fs');
  const manifest = JSON.parse(fs.readFileSync('typescript-diagnostic-debt.json', 'utf8'));
  process.stdout.write(JSON.stringify({
    compiler: manifest.compiler,
    config: manifest.config,
    sourceInventory: manifest.sourceInventory,
    programInputs: manifest.programInputs,
    dependencies: manifest.dependencies,
    diagnostics: manifest.diagnostics.map((entry) => ({ ...entry, line: 1, message: 'forged' })),
    inputReceiptSha256: manifest.inputReceiptSha256,
  }));
  process.exit(0);
}
`);
      const env = { ...process.env };
      if (variable === 'NODE_OPTIONS') env.NODE_OPTIONS = `--require=${preload}`;
      else env.NODE_PATH = root;
      const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
        cwd: root, encoding: 'utf8', timeout: 10_000, env,
      });
      assert.notEqual(result.status, 0,
        `${variable} mintte canonical-looking evidence:\n${result.stdout}${result.stderr}`);
      assert.match(`${result.stdout}${result.stderr}`, new RegExp(variable, 'u'));
    });
  }
});

test('gate-entrypoint en controlbestanden weigeren symlink- en aliaspaden', (t) => {
  const root = fixture(t);
  const gateLink = path.join(root, 'scripts/quality-gate-link.mjs');
  fs.symlinkSync(path.join(root, 'scripts/quality-gate.mjs'), gateLink);
  const aliased = spawnSync(process.execPath, ['scripts/quality-gate-link.mjs', 'typecheck'], {
    cwd: root, encoding: 'utf8', timeout: 10_000,
  });
  assert.notEqual(aliased.status, 0, 'gate werd via een aliaspad uitgevoerd');
  assert.match(`${aliased.stdout}${aliased.stderr}`, /symlink|aliaspad/iu);

  const baselinePath = path.join(root, 'quality-baseline.json');
  const baselineTarget = path.join(root, 'baseline-target.json');
  fs.renameSync(baselinePath, baselineTarget);
  fs.symlinkSync(baselineTarget, baselinePath);
  const linkedControl = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
    cwd: root, encoding: 'utf8', timeout: 10_000,
  });
  assert.notEqual(linkedControl.status, 0, 'symlink-controlbestand werd geaccepteerd');
  assert.match(`${linkedControl.stdout}${linkedControl.stderr}`,
    /symlink|canoniek|regulier|controlbestand/iu);
});

test('resolver gebruikt geen ongebonden ancestorpackage of excluded symlinkdoel', async (t) => {
  const scenarios = ['ancestor-node-modules', 'excluded-data-symlink'];
  for (const scenario of scenarios) {
    await t.test(scenario, async (child) => {
      const container = fs.mkdtempSync('/tmp/eva-quality-resolver-');
      fs.chmodSync(container, 0o700);
      child.after(() => fs.rmSync(container, { recursive: true, force: true }));
      const root = path.join(container, 'project');
      fs.mkdirSync(path.join(root, 'src'), { recursive: true });
      fs.writeFileSync(path.join(root, 'package.json'), '{"type":"module"}\n');
      fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
        compilerOptions: {
          allowJs: true, checkJs: true, noEmit: true, skipLibCheck: true, types: [],
        },
        include: ['src/**/*'],
      }, null, 2)}\n`);
      fs.writeFileSync(path.join(root, 'src/a.d.ts'),
        'export declare const value: number;\n');
      fs.writeFileSync(path.join(root, 'src/b.d.ts'),
        'export declare const value: string;\n');
      fs.writeFileSync(path.join(root, 'src/fixture.js'),
        "import { value } from 'priority-pkg';\n/** @type {number} */ const result = value;\nvoid result;\n");
      installTypeScriptLink(root);

      let packageRoot;
      if (scenario === 'ancestor-node-modules') {
        packageRoot = path.join(container, 'node_modules/priority-pkg');
        fs.mkdirSync(packageRoot, { recursive: true });
      } else {
        packageRoot = path.join(root, 'data/priority-pkg');
        fs.mkdirSync(packageRoot, { recursive: true });
        fs.symlinkSync(packageRoot, path.join(root, 'node_modules/priority-pkg'));
      }
      const packageJson = path.join(packageRoot, 'package.json');
      const relativeA = path.relative(packageRoot, path.join(root, 'src/a.d.ts'))
        .split(path.sep).join('/');
      const relativeB = path.relative(packageRoot, path.join(root, 'src/b.d.ts'))
        .split(path.sep).join('/');
      const value = (types) => `${JSON.stringify({
        name: 'priority-pkg', version: '1.0.0', types,
      })}\n`;
      fs.writeFileSync(packageJson, value(relativeA));

      const gate = await composedGateAround(child, root, {
        beforeCreateProgram: `  fs.writeFileSync(${JSON.stringify(packageJson)}, ${JSON.stringify(value(relativeB))});`,
        afterCreateProgram: `  fs.writeFileSync(${JSON.stringify(packageJson)}, ${JSON.stringify(value(relativeA))});`,
      });
      assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
        /resolver|lookup|symlinkdoel|buiten de gebonden|uitgesloten root|TypeScript-input veranderde/u);
    });
  }
});

test('racegroei wordt begrensd vóór een padread de nieuwe bytes alloceert', async (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  const raced = path.join(root, 'src/fixture.js');
  fs.writeFileSync(raced, 'void 0;\n');
  const gate = await composedGateAround(t, root, {
    transform: (source) => {
      let instrumented = source.replace('const HASH_PATTERN = /^[a-f0-9]{64}$/u;',
        `const HASH_PATTERN = /^[a-f0-9]{64}$/u;
let racePreReadCount = 0;
let raceOversizedAllocationCount = 0;
export const raceAllocationProbe = () => ({
  beforeRead: racePreReadCount,
  oversizedAllocation: raceOversizedAllocationCount,
});`)
      if (instrumented.includes('const before = stableMetadata(beforeStatus);')) {
        instrumented = instrumented
          .replace('const before = stableMetadata(beforeStatus);',
            `const before = stableMetadata(beforeStatus);
  if (file === ${JSON.stringify(raced)}) {
    racePreReadCount += 1;
    fs.writeFileSync(file, 'x'.repeat(4096));
  }`)
          .replace('const bytes = Buffer.allocUnsafe(size);',
            `const bytes = Buffer.allocUnsafe(size);
  if (file === ${JSON.stringify(raced)} && bytes.length > 64) {
    raceOversizedAllocationCount += 1;
    throw new Error('oversized-read-was-allocated');
  }`);
      } else {
        instrumented = instrumented
          .replace('const before = stableMetadata(canonical.status);',
            `const before = stableMetadata(canonical.status);
  if (canonical.absolute === ${JSON.stringify(raced)}) {
    racePreReadCount += 1;
    fs.writeFileSync(canonical.absolute, 'x'.repeat(4096));
  }`)
          .replace('const bytes = readFileSync(canonical.absolute);',
            `const bytes = readFileSync(canonical.absolute);
  if (canonical.absolute === ${JSON.stringify(raced)} && bytes.length > 64) {
    raceOversizedAllocationCount += 1;
    throw new Error('oversized-read-was-allocated');
  }`);
      }
      return instrumented;
    },
  });
  let failure;
  try {
    gate.collectTypeScriptDebtEvidence(root);
  } catch (error) {
    failure = error;
  }
  assert.equal(gate.raceAllocationProbe().beforeRead, 1,
    'racehook werd niet exact eenmaal na de onder-cap fstat geraakt');
  assert.equal(gate.raceAllocationProbe().oversizedAllocation, 0,
    `padread alloceerde bytes boven de cap: ${failure?.message ?? 'geen failure'}`);
  assert.match(String(failure?.message), /bovengrens|TypeScript-input veranderde|bounded read/u);
});

test('controlfilecap en processgroepdeadline falen vóór onbegrensd werk', async (t) => {
  await t.test('manifestcap vóór JSON-parse', (child) => {
    const root = fixture(child);
    const manifestPath = path.join(root, 'typescript-diagnostic-debt.json');
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    manifest.padding = 'x'.repeat(4096);
    fs.writeFileSync(manifestPath, JSON.stringify(manifest));
    const gatePath = path.join(root, 'scripts/quality-gate.mjs');
    const source = fs.readFileSync(gatePath, 'utf8')
      .replace('const MAX_CONTROL_FILE_BYTES = 16_777_216;',
        'const MAX_CONTROL_FILE_BYTES = 1024;');
    fs.writeFileSync(gatePath, source);
    const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
      cwd: root, encoding: 'utf8', timeout: 10_000,
    });
    assert.notEqual(result.status, 0);
    assert.match(`${result.stdout}${result.stderr}`,
      /controlbestand|manifest.*(?:byte|grootte|bovengrens)|bounded read/iu);
  });

  await t.test('timeout doodt descendants met geërfde pipes', (child) => {
    const root = fixture(child);
    const pidFile = path.join(root, 'descendant.pid');
    const readyFile = path.join(root, 'descendant.ready');
    writeFakeEslint(root, `
import { spawn } from 'node:child_process';
import fs from 'node:fs';
const held = spawn(process.execPath, ['-e', ${JSON.stringify(`
  const fs = require('node:fs');
  fs.writeFileSync(${JSON.stringify(readyFile)}, 'ready');
  setInterval(() => {}, 1000);
`)}], {
  stdio: ['ignore', 'ignore', 'ignore'],
});
const waitUntil = Date.now() + 400;
while (!fs.existsSync(${JSON.stringify(readyFile)}) && Date.now() < waitUntil) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
}
if (!fs.existsSync(${JSON.stringify(readyFile)})) process.exit(73);
fs.writeFileSync(${JSON.stringify(pidFile)}, String(held.pid));
Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5000);
`);
    const gatePath = path.join(root, 'scripts/quality-gate.mjs');
    const source = fs.readFileSync(gatePath, 'utf8').replace(
      'const SUBPROCESS_TIMEOUT_MS = 120_000;', 'const SUBPROCESS_TIMEOUT_MS = 500;',
    );
    fs.writeFileSync(gatePath, source);
    const started = Date.now();
    const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'lint'], {
      cwd: root, encoding: 'utf8', timeout: 3_000,
    });
    assert.notEqual(result.status, 0);
    assert.equal(Date.now() - started < 1_500, true,
      'gateparent keerde niet begrensd terug');
    assert.equal(fs.existsSync(readyFile), true, 'descendant-handshake werd niet bereikt');
    assert.equal(fs.existsSync(pidFile), true, 'descendant-PID werd niet vastgelegd');
    const descendantPid = Number(fs.readFileSync(pidFile, 'utf8'));
    let descendantAlive = false;
    let descendantState = null;
    try {
      const stat = fs.readFileSync(`/proc/${descendantPid}/stat`, 'utf8');
      descendantState = stat.slice(stat.lastIndexOf(') ') + 2).split(' ')[0];
      descendantAlive = !['Z', 'X'].includes(descendantState);
    } catch {
      descendantAlive = false;
    } finally {
      if (descendantAlive) {
        try { process.kill(descendantPid, 'SIGKILL'); } catch { /* already exited */ }
      }
    }
    assert.equal(descendantAlive, false,
      `timeout liet descendant PID ${descendantPid} in status ${descendantState} buiten de killgrens leven`);
  });
});

test('directe gate-invocation blijft aan dezelfde geopende executable gebonden', async (t) => {
  for (const scenario of ['self-delete', 'alpha-beta-alpha', 'realpath-drift']) {
    await t.test(scenario, (child) => {
      const root = fixture(child);
      writeFakeEslint(root,
        'process.stdout.write(JSON.stringify([{ messages: [], errorCount: 0, warningCount: 0 }]));\n');
      const gatePath = path.join(root, 'scripts/quality-gate.mjs');
      const moved = path.join(root, 'scripts/quality-gate-a.mjs');
      const marker = path.join(root, `${scenario}.hook`);
      let source = fs.readFileSync(gatePath, 'utf8');
      const hook = `const gateFile = bootstrap?.gateFile ?? fileURLToPath(import.meta.url);`;
      assert.equal(source.includes(hook), true, 'direct-invocation hook ontbreekt');
      let injection;
      if (scenario === 'self-delete') {
        injection = `\nfs.writeFileSync(${JSON.stringify(marker)}, 'hit');\nfs.unlinkSync(gateFile);`;
      } else if (scenario === 'alpha-beta-alpha') {
        injection = `\nfs.writeFileSync(${JSON.stringify(marker)}, 'hit');\nconst alpha = fs.readFileSync(gateFile);\nfs.writeFileSync(gateFile, 'process.exit(0);\\n');\nfs.writeFileSync(gateFile, alpha);`;
      } else {
        injection = `\nfs.writeFileSync(${JSON.stringify(marker)}, 'hit');\nfs.renameSync(gateFile, ${JSON.stringify(moved)});\nfs.symlinkSync(${JSON.stringify(moved)}, gateFile);`;
      }
      source = source.replace(hook, `${hook}${injection}`);
      fs.writeFileSync(gatePath, source);
      const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'lint'], {
        cwd: root, encoding: 'utf8', timeout: 10_000,
      });
      assert.equal(fs.existsSync(marker), true, 'direct-invocation canaryhook werd niet geraakt');
      assert.notEqual(result.status, 0,
        `${scenario} maakte geen harde executable-bindingfout:\n${result.stdout}${result.stderr}`);
    });
  }
});

test('descriptor-first bounded read opent een FIFO nooit blokkerend', (t) => {
  const root = fixture(t);
  const manifestPath = path.join(root, 'typescript-diagnostic-debt.json');
  const marker = path.join(root, 'fifo-race.hook');
  const gatePath = path.join(root, 'scripts/quality-gate.mjs');
  let source = fs.readFileSync(gatePath, 'utf8');
  const hook = '  const canonical = canonicalInputFile(file, expectedRoot);';
  assert.equal(source.includes(hook), true, 'FIFO lstat/open hook ontbreekt');
  source = source.replace(hook, `${hook}
  if (canonical.absolute === ${JSON.stringify(manifestPath)}) {
    fs.writeFileSync(${JSON.stringify(marker)}, 'hit');
    fs.unlinkSync(canonical.absolute);
    const made = spawn('mkfifo', [canonical.absolute], { stdio: 'ignore' });
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50);
    made.unref();
  }`);
  fs.writeFileSync(gatePath, source);
  const started = Date.now();
  const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
    cwd: root, encoding: 'utf8', timeout: 1_000,
  });
  assert.equal(fs.existsSync(marker), true, 'FIFO-racehook werd niet geraakt');
  assert.equal(fs.lstatSync(manifestPath).isFIFO(), true,
    'FIFO-race verving het reguliere bestand niet vóór descriptor-open');
  assert.notEqual(result.status, 0, 'FIFO-controlpad werd geaccepteerd');
  assert.equal(result.error?.code === 'ETIMEDOUT', false,
    'gate blokkeerde vóór fstat op een FIFO open');
  assert.equal(Date.now() - started < 750, true, 'FIFO-weigering was niet begrensd');
});

test('precanonieke caps stoppen rootNames en evidence vóór sorteren of serialiseren', async (t) => {
  const { evidence } = contractFixture();
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  const gate = await composedGateAround(t, root, {
    transform: (source) => source
      .replace('const MAX_INPUT_FILES = 100_000;', 'const MAX_INPUT_FILES = 2;')
      .replace('const MAX_DIAGNOSTIC_COUNT = 1_000_000;',
        'const MAX_DIAGNOSTIC_COUNT = 2;'),
  });
  const oversizedNames = new Proxy([], {
    get(target, property, receiver) {
      if (property === 'length') return 3;
      if (property === Symbol.iterator) throw new Error('rootNames-iterator-was-consumed');
      return Reflect.get(target, property, receiver);
    },
  });
  assert.throws(() => gate.validateTypeScriptDebtManifest({
    ...contractFixture().manifest,
    sourceInventory: { rootNames: oversizedNames, count: 1, sha256: 'a'.repeat(64) },
  }), (error) => !/rootNames-iterator-was-consumed/u.test(String(error?.message)));
  const oversizedDiagnostics = new Proxy([], {
    get(target, property, receiver) {
      if (property === 'length') return 3;
      if (property === Symbol.iterator) throw new Error('evidence-iterator-was-consumed');
      return Reflect.get(target, property, receiver);
    },
  });
  assert.throws(() => gate.validateTypeScriptEvidence({
    ...evidence, diagnostics: oversizedDiagnostics,
  }), (error) => !/evidence-iterator-was-consumed/u.test(String(error?.message)));
});

test('evaluator en generator valideren evidence en weigeren reviewed-release altijd', () => {
  const { manifest, evidence } = contractFixture();
  const forgedEvidence = {
    ...evidence,
    diagnostics: [{ ...evidence.diagnostics[0], message: 'forged without message hash update' }],
  };
  assert.throws(() => evaluateTypeScriptDebt({
    manifest, evidence: forgedEvidence, baselineMaximum: 2,
  }), /evidence|canoniek|message/iu);
  assert.throws(() => createTypeScriptDebtManifest(forgedEvidence, 2),
    /evidence|canoniek|message/iu);
  assert.throws(() => createTypeScriptDebtManifest(
    evidence, 2, { scope: 'reviewed-release', releaseEligible: true },
  ), /reviewed-release|rootreceipt|externe attestatie/iu);
});

test('manifest kan zichzelf nooit als semantische TypeScript-input importeren', (t) => {
  const root = sourceFixture(t);
  fs.writeFileSync(path.join(root, 'typescript-diagnostic-debt.json'), '{}\n');
  fs.writeFileSync(path.join(root, 'src/fixture.js'),
    "import debt from '../typescript-diagnostic-debt.json' with { type: 'json' };\nvoid debt;\n");
  fs.writeFileSync(path.join(root, 'tsconfig.json'), `${JSON.stringify({
    compilerOptions: {
      allowJs: true, checkJs: true, noEmit: true, resolveJsonModule: true, types: [],
    },
    include: ['src/**/*.js', 'typescript-diagnostic-debt.json'],
  }, null, 2)}\n`);
  assert.throws(() => collectTypeScriptDebtEvidence(root),
    /manifest.*semantische|rootName|programmabron|ontbreekt.*snapshot/iu);
});

test('filesystem-root node_modules is een expliciet sealed resolverinput', async (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
  const gate = await composedGateAround(t, root, {
    transform: (source) => source
      .replace('const HASH_PATTERN = /^[a-f0-9]{64}$/u;',
        `const HASH_PATTERN = /^[a-f0-9]{64}$/u;
let filesystemRootProbe = 0;
export const filesystemRootLookupProbe = () => filesystemRootProbe;`)
      .replace("    const candidate = path.join(ancestor, 'node_modules');",
        `    const candidate = path.join(ancestor, 'node_modules');
    if (candidate === path.parse(repository).root + 'node_modules') filesystemRootProbe += 1;`),
  });
  gate.collectTypeScriptDebtEvidence(root);
  assert.equal(gate.filesystemRootLookupProbe() >= 1, true,
    'ancestorinventaris sloeg filesystem-root /node_modules over');
});

test('setsid-descendant met geërfde pipe kan timeout niet buiten containment houden', async (t) => {
  const root = fixture(t);
  const readyFile = path.join(root, 'setsid.ready');
  const pidFile = path.join(root, 'setsid.pid');
  writeFakeEslint(root, `
import { spawn } from 'node:child_process';
import fs from 'node:fs';
const held = spawn('/usr/bin/setsid', [process.execPath, '-e', ${JSON.stringify(`
  const fs = require('node:fs');
  fs.writeFileSync(${JSON.stringify(readyFile)}, 'ready');
  setInterval(() => {}, 1000);
`)}], { stdio: ['ignore', 'inherit', 'inherit'] });
const until = Date.now() + 900;
while (!fs.existsSync(${JSON.stringify(readyFile)}) && Date.now() < until) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
}
if (!fs.existsSync(${JSON.stringify(readyFile)})) process.exit(73);
fs.writeFileSync(${JSON.stringify(pidFile)}, String(held.pid));
Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5000);
`);
  const gatePath = path.join(root, 'scripts/quality-gate.mjs');
  fs.writeFileSync(gatePath, fs.readFileSync(gatePath, 'utf8').replace(
    'const SUBPROCESS_TIMEOUT_MS = 120_000;', 'const SUBPROCESS_TIMEOUT_MS = 1200;',
  ));
  const started = Date.now();
  const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'lint'], {
    cwd: root, encoding: 'utf8', timeout: 4_000,
  });
  assert.notEqual(result.status, 0);
  assert.equal(fs.existsSync(readyFile), true, 'setsid-handshake werd niet geraakt');
  assert.equal(Date.now() - started < 2_500, true,
    'setsid-descendant hield geërfde pipes open na de hard deadline');
  const pid = Number(fs.readFileSync(pidFile, 'utf8'));
  let alive = false;
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    alive = !['Z', 'X'].includes(stat.slice(stat.lastIndexOf(') ') + 2).split(' ')[0]);
  } catch { alive = false; }
  if (alive) { try { process.kill(pid, 'SIGKILL'); } catch { /* already gone */ } }
  assert.equal(alive, false, `setsid-descendant ${pid} ontsnapte aan containment`);
});

test('official sealed generate gebruikt private temp, atomisch schrijven en fresh verify', (t) => {
  const root = fixture(t);
  const manifestPath = path.join(root, 'typescript-diagnostic-debt.json');
  fs.copyFileSync(path.join(projectRoot, 'typescript-diagnostic-debt.json'), manifestPath);
  const before = fs.readFileSync(manifestPath);
  const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'generate-typecheck-debt'], {
    cwd: root, encoding: 'utf8', timeout: 20_000,
  });
  assert.equal(result.status, 0,
    `official sealed generate ontbreekt of faalde:\n${result.stdout}${result.stderr}`);
  assert.deepEqual(fs.readFileSync(manifestPath), before,
    'generate wijzigde het repositorymanifest in plaats van private tempoutput');
  const output = JSON.parse(result.stdout);
  assert.equal(output.verified, true);
  assert.equal(output.schema, 4);
  assert.equal(output.releaseEligible, false);
  assert.match(output.outputPath, /^\/tmp\//u);
  assert.equal(fs.lstatSync(output.outputPath).isFile(), true);
  assert.equal(fs.lstatSync(output.outputPath).isSymbolicLink(), false);
  assert.equal(fs.readFileSync(output.outputPath, 'utf8').endsWith('\n'), true);
  fs.rmSync(path.dirname(output.outputPath), { recursive: true, force: true });
});

test('directoryinventaris is incrementeel en contentbinding niet kwadratisch', async (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  const bulk = path.join(root, 'src/bulk');
  fs.mkdirSync(bulk, { recursive: true });
  for (let index = 0; index < 12; index += 1) {
    fs.writeFileSync(path.join(bulk, `${String(index).padStart(2, '0')}.js`),
      `export const value${index} = ${index};\n`);
  }
  const gate = await composedGateAround(t, root, {
    transform: (source) => source
      .replace('const HASH_PATTERN = /^[a-f0-9]{64}$/u;',
        `const HASH_PATTERN = /^[a-f0-9]{64}$/u;
let bulkReaddirAllocation = 0;
let inventoryPasses = 0;
export const enumerationProbe = () => ({ bulkReaddirAllocation, inventoryPasses });`)
      .replace('function contentInventory(snapshots) {',
        `function contentInventory(snapshots) {
  inventoryPasses += 1;`)
      .replace('    const entries = fs.readdirSync(current, { withFileTypes: true })',
        `    if (current === ${JSON.stringify(bulk)}) bulkReaddirAllocation += 1;
    const entries = fs.readdirSync(current, { withFileTypes: true })`)
      .replace('    const entries = fs.readdirSync(absolute, { withFileTypes: true })',
        `    if (absolute === ${JSON.stringify(bulk)}) bulkReaddirAllocation += 1;
    const entries = fs.readdirSync(absolute, { withFileTypes: true })`),
  });
  gate.collectTypeScriptDebtEvidence(root);
  const probe = gate.enumerationProbe();
  assert.equal(probe.bulkReaddirAllocation, 0,
    'bulkdirectory werd in één readdir-array gealloceerd');
  assert.equal(probe.inventoryPasses <= 12, true,
    `contentinventaris werd kwadratisch herbouwd (${probe.inventoryPasses} volledige passes)`);
});

test('globale rolling bytecap omvat compiler en repository vóór allocatie', async (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
  fs.writeFileSync(path.join(root, 'large-bound-input.txt'), Buffer.alloc(8_000_000, 0x61));
  const gate = await composedGateAround(t, root, {
    transform: (source) => source.replace(
      'const MAX_INPUT_BYTES = 536_870_912;', 'const MAX_INPUT_BYTES = 30_000_000;',
    ).replace(
      'const MAX_SINGLE_INPUT_BYTES = 134_217_728;',
      'const MAX_SINGLE_INPUT_BYTES = 30_000_000;',
    ),
  });
  assert.throws(() => gate.collectTypeScriptDebtEvidence(root),
    /globale|aggregate|resourcebovengrens|inputbytes/iu);
});

test('niet-semantische binaire dependencybytes worden nooit als tekst gedecodeerd', async (t) => {
  const root = sourceFixture(t);
  installTypeScriptLink(root);
  fs.writeFileSync(path.join(root, 'src/fixture.js'), 'export const value = true;\n');
  const binary = path.join(root, 'asset.bin');
  fs.writeFileSync(binary, Buffer.from([0, 255, 0, 254, 1, 2, 3, 4]));
  const gate = await composedGateAround(t, root, {
    transform: (source) => source
      .replace('const HASH_PATTERN = /^[a-f0-9]{64}$/u;',
        `const HASH_PATTERN = /^[a-f0-9]{64}$/u;
let binaryDecodeCount = 0;
export const binaryDecodeProbe = () => binaryDecodeCount;`)
      .replace('function decodeTypeScriptBytes(bytes) {',
        `function decodeTypeScriptBytes(bytes) {
  if (bytes.includes(0)) binaryDecodeCount += 1;`),
  });
  gate.collectTypeScriptDebtEvidence(root);
  assert.equal(gate.binaryDecodeProbe(), 0,
    'niet-semantische binaire dependency werd naar een JS-string gedecodeerd');
});

test('workeroutputcap stopt tijdens streaming vóór oversized evidencebuffer', (t) => {
  const root = fixture(t);
  const gatePath = path.join(root, 'scripts/quality-gate.mjs');
  let source = fs.readFileSync(gatePath, 'utf8');
  source = source
    .replace('const MAX_WORKER_EVIDENCE_BYTES = 67_108_864;',
      'const MAX_WORKER_EVIDENCE_BYTES = 1024;')
    .replace('  evidence,\n}));', `  evidence,
  padding: 'x'.repeat(4096),
}));`)
    .replace('      target.push(chunk);', `      if (outputBytes > MAX_WORKER_EVIDENCE_BYTES) {
        throw new Error('oversized-worker-output-was-buffered');
      }
      target.push(chunk);`);
  fs.writeFileSync(gatePath, source);
  const result = spawnSync(process.execPath, ['scripts/quality-gate.mjs', 'typecheck'], {
    cwd: root, encoding: 'utf8', timeout: 10_000,
  });
  assert.notEqual(result.status, 0);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`,
    /oversized-worker-output-was-buffered/u,
    'workeroutput werd boven de evidencecap gebufferd vóór beëindiging');
});
