import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const baseline = JSON.parse(readFileSync(resolve(root, 'quality-baseline.json'), 'utf8'));
const mode = process.argv[2];

function runNode(relativeExecutable, args) {
  const result = spawnSync(process.execPath, [resolve(root, relativeExecutable), ...args], {
    cwd: root,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  return result;
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

function typecheck() {
  const result = runNode('node_modules/typescript/bin/tsc', ['-p', 'tsconfig.json', '--pretty', 'false']);
  const output = `${result.stdout || ''}${result.stderr || ''}`;
  const diagnostics = output.split(/\r?\n/).filter((line) => /error TS\d+:/.test(line));
  const files = new Map();
  for (const line of diagnostics) {
    const match = line.match(/^(.+?)\(\d+,\d+\): error TS\d+:/);
    const file = match?.[1] || '<configuratie>';
    files.set(file, (files.get(file) || 0) + 1);
  }
  printLeaders(files, 'Meeste TypeScript-diagnoses');
  const passed = compare('TypeScript JS-diagnoses', diagnostics.length, baseline.typescriptDiagnostics);
  if (result.status === null) {
    console.error('Typecheck kon niet worden uitgevoerd.');
    process.exitCode = 1;
  } else if (result.status !== 0 && diagnostics.length === 0) {
    console.error(result.stderr || result.stdout || 'Typecheck stopte zonder leesbare diagnoses.');
    process.exitCode = 1;
  } else if (!passed) {
    console.error('Typecheck-regressie: de gecontroleerde nulmeting is overschreden.');
    process.exitCode = 1;
  }
}

function eslintCheck(complexityOnly) {
  const args = complexityOnly
    ? ['--config', 'eslint.complexity.config.js', 'src', '--format', 'json']
    : ['src', 'test', 'scripts', '--format', 'json'];
  const result = runNode('node_modules/eslint/bin/eslint.js', args);
  let reports;
  try {
    reports = JSON.parse(result.stdout);
  } catch {
    console.error(result.stderr || result.stdout || 'ESLint gaf geen leesbaar resultaat.');
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
  if (errors > 0 || !passed || result.status === null) process.exitCode = 1;
}

if (mode === 'typecheck') typecheck();
else if (mode === 'lint') eslintCheck(false);
else if (mode === 'complexity') eslintCheck(true);
else {
  console.error('Gebruik: node scripts/quality-gate.mjs <typecheck|lint|complexity>');
  process.exitCode = 2;
}
