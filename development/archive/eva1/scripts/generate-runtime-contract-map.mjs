#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import process from 'node:process';
import ts from 'typescript';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const srcRoot = path.join(root, 'src');
const outputJson = path.join(root, 'docs', 'eva-runtime-contract-map.generated.json');
const outputMarkdown = path.join(root, 'docs', 'eva-runtime-contract-map.generated.md');
const semanticMarkdown = path.join(root, 'docs', 'eva-runtime-contracts.md');
const checkOnly = process.argv.includes('--check');

function unix(value) {
  return value.split(path.sep).join('/');
}

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) return walk(absolute);
      return /\.(?:js|mjs)$/u.test(entry.name) ? [absolute] : [];
    })
    .sort();
}

function lineOf(sourceFile, node) {
  return sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
}

function localTarget(fromFile, specifier) {
  if (!specifier.startsWith('.')) return null;
  const base = path.resolve(path.dirname(fromFile), specifier);
  const candidates = [base, `${base}.js`, `${base}.mjs`, path.join(base, 'index.js')];
  const found = candidates.find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
  return found ? unix(path.relative(root, found)) : `MISSING:${unix(path.relative(root, base))}`;
}

function declarationName(node) {
  if (node.name && ts.isIdentifier(node.name)) return node.name.text;
  if (node.name && ts.isStringLiteral(node.name)) return node.name.text;
  return null;
}

function exportedSymbols(sourceFile) {
  const symbols = [];
  for (const statement of sourceFile.statements) {
    const modifiers = ts.canHaveModifiers(statement) ? ts.getModifiers(statement) ?? [] : [];
    const exported = modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword);
    const isDefault = modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.DefaultKeyword);
    if (ts.isExportAssignment(statement)) {
      symbols.push({ name: 'default', kind: 'default', line: lineOf(sourceFile, statement) });
      continue;
    }
    if (ts.isExportDeclaration(statement)) {
      if (!statement.exportClause) {
        symbols.push({ name: '*', kind: 're-export', line: lineOf(sourceFile, statement) });
      } else if (ts.isNamedExports(statement.exportClause)) {
        for (const element of statement.exportClause.elements) {
          symbols.push({
            name: element.name.text,
            kind: 're-export',
            line: lineOf(sourceFile, element),
          });
        }
      }
      continue;
    }
    if (!exported) continue;
    if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        const name = declarationName(declaration);
        if (name) symbols.push({ name, kind: isDefault ? 'default' : 'value', line: lineOf(sourceFile, declaration) });
      }
      continue;
    }
    const name = declarationName(statement) ?? (isDefault ? 'default' : null);
    if (name) {
      symbols.push({
        name,
        kind: ts.isFunctionDeclaration(statement)
          ? 'function'
          : ts.isClassDeclaration(statement)
            ? 'class'
            : 'value',
        line: lineOf(sourceFile, statement),
      });
    }
  }
  return symbols;
}

function moduleReferences(sourceFile, absoluteFile) {
  const refs = [];
  function add(node, specifier, kind) {
    refs.push({
      specifier,
      kind,
      line: lineOf(sourceFile, node),
      target: localTarget(absoluteFile, specifier),
    });
  }
  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      add(node, node.moduleSpecifier.text, ts.isImportDeclaration(node) ? 'static' : 're-export');
    }
    if (ts.isCallExpression(node)
      && node.expression.kind === ts.SyntaxKind.ImportKeyword
      && node.arguments.length === 1
      && ts.isStringLiteral(node.arguments[0])) {
      add(node, node.arguments[0].text, 'dynamic');
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return refs;
}

function matches(source, expression) {
  return [...source.matchAll(expression)].map((match) => match[1]).filter(Boolean);
}

function sqlLiterals(sourceFile) {
  const constants = new Map();
  const values = new Set();
  function staticText(node, seen = new Set()) {
    if (!node) return null;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
    if (ts.isTemplateExpression(node)) {
      return `${node.head.text}${node.templateSpans.map((span) => ` \${dynamic} ${span.literal.text}`).join('')}`;
    }
    if (ts.isParenthesizedExpression(node)) return staticText(node.expression, seen);
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
      const left = staticText(node.left, seen);
      const right = staticText(node.right, seen);
      return left != null && right != null ? `${left}${right}` : null;
    }
    if (ts.isIdentifier(node)) {
      if (seen.has(node.text)) return null;
      const value = constants.get(node.text);
      return value == null ? null : staticText(value, new Set([...seen, node.text]));
    }
    if (ts.isCallExpression(node)
      && ts.isPropertyAccessExpression(node.expression)
      && node.expression.name.text === 'replace') {
      return staticText(node.expression.expression, seen);
    }
    return null;
  }
  function collectConstants(node) {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      constants.set(node.name.text, node.initializer);
    }
    ts.forEachChild(node, collectConstants);
  }
  collectConstants(sourceFile);

  const sqlStart = /^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|PRAGMA)\b/iu;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)
      && /(?:SQL|DDL|SCHEMA)/iu.test(node.name.text)) {
      const text = staticText(node.initializer);
      if (text && sqlStart.test(text)) values.add(text);
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && ['exec', 'prepare'].includes(node.expression.name.text)) {
      const text = staticText(node.arguments[0]);
      if (text && sqlStart.test(text)) values.add(text);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return [...values].join('\n');
}

function sqlAccess(sourceFile) {
  const source = sqlLiterals(sourceFile);
  const cteAliases = new Set(matches(source, /\b([a-zA-Z_][\w]*)(?:\s*\([^)]*\))?\s+AS(?:\s+(?:NOT\s+)?MATERIALIZED)?\s*\(/giu));
  const nonTables = new Set([
    'main', 'temp', 'json_each',
    'AS', 'CREATE', 'DELETE', 'DROP', 'EXISTS', 'FROM', 'IF', 'INSERT', 'INTO',
    'JOIN', 'NOT', 'SELECT', 'SET', 'TABLE', 'UPDATE', 'WHERE', 'WITH',
  ]);
  const identifiers = (expression) => matches(source, expression)
    .map((value) => value.split('.').at(-1))
    .filter((value) => !cteAliases.has(value)
      && !nonTables.has(value)
      && !value.startsWith('pragma_'));
  const table = '([a-zA-Z_][\\w]*(?:\\.[a-zA-Z_][\\w]*)?)';
  const reads = new Set([
    ...identifiers(new RegExp(`\\bFROM\\s+${table}`, 'giu')),
    ...identifiers(new RegExp(`\\bJOIN\\s+${table}`, 'giu')),
  ]);
  const writes = new Set([
    ...identifiers(new RegExp(`\\bINSERT\\s+(?:OR\\s+\\w+\\s+)?INTO\\s+${table}`, 'giu')),
    ...identifiers(new RegExp(`\\bUPDATE\\s+${table}\\s+SET\\b`, 'giu')),
    ...identifiers(new RegExp(`\\bDELETE\\s+FROM\\s+${table}`, 'giu')),
  ]);
  const schema = new Set(identifiers(new RegExp(`\\bCREATE\\s+TABLE\\s+(?:IF\\s+NOT\\s+EXISTS\\s+)?${table}`, 'giu')));
  return {
    reads: [...reads].sort(),
    writes: [...writes].sort(),
    schema: [...schema].sort(),
    dynamicIdentifiers: source.includes('${dynamic}'),
  };
}

function quotedCalls(source, receiver, method) {
  const escaped = receiver.replaceAll('.', '\\.');
  return [...new Set(matches(source, new RegExp(`\\b${escaped}\\.${method}\\(\\s*['"]([^'"]+)['"]`, 'gu')))].sort();
}

function routeCalls(source) {
  const result = [];
  const expression = /\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]/giu;
  for (const match of source.matchAll(expression)) result.push(`${match[1].toUpperCase()} ${match[2]}`);
  return [...new Set(result)].sort();
}

function effectMarkers(source, imports) {
  const effects = new Set();
  const external = new Set(imports.filter((item) => !item.target).map((item) => item.specifier));
  if (/\bfetch\s*\(|\bWebSocket\b|\baxios\b/u.test(source)) effects.add('network');
  if ([...external].some((item) => item === 'node:fs' || item === 'fs' || item === 'node:fs/promises')) effects.add('filesystem');
  if ([...external].some((item) => item === 'node:child_process')) effects.add('process-spawn');
  if (/\bsetInterval\s*\(|\bsetTimeout\s*\(/u.test(source)) effects.add('timer');
  if (/\bprocess\.env\b/u.test(source)) effects.add('environment');
  if (/\bdb\.(?:prepare|exec|transaction)\s*\(/u.test(source)) effects.add('sqlite');
  if (/\b(?:callService|sendMessage|sendPhoto|sendVoice|speak)\s*\(/u.test(source)) effects.add('external-effect-call');
  if (/\b(?:createHash|createHmac|randomBytes|timingSafeEqual)\s*\(/u.test(source)) effects.add('cryptography');
  if (/\b(?:authority|receipt|dedupe|idempotenc|issuedPlan|executionKey|sha256)\b/iu.test(source)) effects.add('authority-or-proof-boundary');
  return [...effects].sort();
}

function domainOf(file) {
  if (file.startsWith('src/mind/')) return 'mind';
  if (file.startsWith('src/integrations/')) return 'integrations';
  if (file.startsWith('src/web/public/')) return 'browser';
  if (file.startsWith('src/web/')) return 'web';
  if (file.startsWith('src/brain/')) return 'brain';
  if (file.startsWith('src/self/')) return 'secrets';
  if (file.startsWith('src/security/')) return 'security';
  if (file.startsWith('deploy/')) return 'deployment';
  return 'runtime';
}

function fileRecord(absoluteFile) {
  const source = fs.readFileSync(absoluteFile, 'utf8');
  const relative = unix(path.relative(root, absoluteFile));
  const sourceFile = ts.createSourceFile(relative, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const imports = moduleReferences(sourceFile, absoluteFile);
  return {
    file: relative,
    domain: domainOf(relative),
    bytes: Buffer.byteLength(source),
    lines: source.split(/\r?\n/u).length,
    sha256: crypto.createHash('sha256').update(source).digest('hex'),
    imports,
    importedBy: [],
    exports: exportedSymbols(sourceFile),
    sql: sqlAccess(sourceFile),
    events: {
      emits: quotedCalls(source, 'bus', 'emit'),
      consumes: [...quotedCalls(source, 'bus', 'on'), ...quotedCalls(source, 'bus', 'once')].sort(),
    },
    routes: routeCalls(source),
    environment: [...new Set(matches(source, /\bprocess\.env\.([A-Z][A-Z0-9_]*)\b/gu))].sort(),
    effects: effectMarkers(source, imports),
  };
}

function stronglyConnected(records, allowedKinds) {
  const byFile = new Map(records.map((record) => [record.file, record]));
  const indexes = new Map();
  const lowLinks = new Map();
  const stack = [];
  const onStack = new Set();
  const components = [];
  let index = 0;

  function visit(file) {
    indexes.set(file, index);
    lowLinks.set(file, index);
    index += 1;
    stack.push(file);
    onStack.add(file);
    const record = byFile.get(file);
    for (const ref of record.imports.filter((item) => item.target
      && byFile.has(item.target)
      && allowedKinds.has(item.kind))) {
      if (!indexes.has(ref.target)) {
        visit(ref.target);
        lowLinks.set(file, Math.min(lowLinks.get(file), lowLinks.get(ref.target)));
      } else if (onStack.has(ref.target)) {
        lowLinks.set(file, Math.min(lowLinks.get(file), indexes.get(ref.target)));
      }
    }
    if (lowLinks.get(file) !== indexes.get(file)) return;
    const component = [];
    while (stack.length) {
      const item = stack.pop();
      onStack.delete(item);
      component.push(item);
      if (item === file) break;
    }
    if (component.length > 1) components.push(component.sort());
  }

  for (const record of records) if (!indexes.has(record.file)) visit(record.file);
  return components;
}

function csv(values, empty = 'geen') {
  return values.length ? values.map((value) => `\`${value}\``).join(', ') : empty;
}

function markdownFor(manifest) {
  const lines = [
    '# Eva runtime-contractkaart, automatisch gegenereerd',
    '',
    '> Niet handmatig wijzigen. Genereer met `node scripts/generate-runtime-contract-map.mjs` en controleer met `node scripts/generate-runtime-contract-map.mjs --check`.',
    '',
    `Bronmoment: \`${manifest.sourceDigest}\`. Bestanden: ${manifest.summary.files}. Statische lokale randen: ${manifest.summary.localEdges}. Dynamische lokale randen: ${manifest.summary.dynamicEdges}. Statische cycli: ${manifest.summary.cycles}. Runtimecycli inclusief dynamische test- en lazy-loadranden: ${manifest.summary.runtimeCycles}.`,
    '',
    'Deze laag beschrijft mechanisch alle JavaScriptmodules onder `src` plus lokale modules die zij importeren. Imports, exports en directe moduleconsumers (`importedBy`) komen uit de AST. Dit zijn geen functiecallsites. SQL-, bus-, route-, environment- en effectregels zijn statische detecties; een lege detectie betekent niet dat runtimegedrag onmogelijk is. Semantische runtimecontracten en foutpaden staan in `docs/eva-runtime-contracts.md`.',
    '',
    '## Domeinoverzicht',
    '',
    '| Domein | Bestanden | Regels | Lokale imports | Exports |',
    '| --- | ---: | ---: | ---: | ---: |',
  ];
  for (const domain of manifest.domains) {
    lines.push(`| ${domain.domain} | ${domain.files} | ${domain.lines} | ${domain.localEdges} | ${domain.exports} |`);
  }
  lines.push('', '## Entry- en bladmodules', '', `Entry-achtig, zonder directe moduleconsumer: ${csv(manifest.entrypoints)}.`, '', `Bladmodules, zonder lokale import: ${csv(manifest.leaves)}.`, '');
  if (manifest.cycles.length) {
    lines.push('## Statische importcycli', '');
    for (const cycle of manifest.cycles) lines.push(`- ${cycle.map((item) => `\`${item}\``).join(' -> ')}`);
    lines.push('');
  }
  if (manifest.runtimeCycles.length) {
    lines.push('## Runtimecycli inclusief dynamische imports', '', 'Deze SCCs zijn geen bewijs dat ESM-initialisatie cyclisch is. Ze tonen callback-, lazy-load- en testcompositieranden die samen terug kunnen lopen.', '');
    for (const cycle of manifest.runtimeCycles) lines.push(`- ${cycle.map((item) => `\`${item}\``).join(' -> ')}`);
    lines.push('');
  }
  lines.push('## Bestandcontracten', '');
  for (const record of manifest.files) {
    const localInputs = record.imports.filter((item) => item.target).map((item) => item.target);
    const externalInputs = record.imports.filter((item) => !item.target).map((item) => item.specifier);
    lines.push(`### \`${record.file}\``, '');
    lines.push(`- Omvang: ${record.lines} regels, ${record.bytes} bytes. SHA-256 \`${record.sha256}\`.`);
    lines.push(`- Invoer uit lokale modules: ${csv(localInputs)}.`);
    lines.push(`- Externe invoer/dependencies: ${csv(externalInputs, 'geen statisch zichtbare externe import')}.`);
    lines.push(`- Uitvoer/API: ${record.exports.length ? record.exports.map((item) => `\`${item.name}\` (${item.kind}, regel ${item.line})`).join(', ') : 'geen exports; side-effect- of entrymodule'}.`);
    lines.push(`- Directe consumers: ${csv(record.importedBy)}.`);
    lines.push(`- Statisch zichtbare SQLite-identifiers: schema ${csv(record.sql.schema, 'geen')}; leest ${csv(record.sql.reads, 'geen')}; schrijft ${csv(record.sql.writes, 'geen')}${record.sql.dynamicIdentifiers ? '; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers' : ''}.`);
    lines.push(`- Statisch letterlijke bus-events: ontvangt ${csv(record.events.consumes, 'geen detectie')}; emitteert ${csv(record.events.emits, 'geen detectie')}.`);
    lines.push(`- Statisch letterlijke HTTP-routes: ${csv(record.routes, 'geen detectie')}.`);
    lines.push(`- Directe process.env-properties: ${csv(record.environment, 'geen detectie')}.`);
    lines.push(`- Heuristisch gedetecteerde bijwerkingen/grenzen: ${csv(record.effects, 'geen marker')}.`);
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

const pendingFiles = walk(srcRoot);
const discoveredFiles = new Map();
while (pendingFiles.length) {
  const absoluteFile = pendingFiles.shift();
  const relative = unix(path.relative(root, absoluteFile));
  if (discoveredFiles.has(relative)) continue;
  const record = fileRecord(absoluteFile);
  discoveredFiles.set(relative, record);
  for (const ref of record.imports) {
    if (!ref.target || ref.target.startsWith('MISSING:') || discoveredFiles.has(ref.target)) continue;
    const targetFile = path.join(root, ref.target);
    if (/\.(?:js|mjs)$/u.test(targetFile) && fs.existsSync(targetFile)) pendingFiles.push(targetFile);
  }
}
const records = [...discoveredFiles.values()].sort((a, b) => a.file.localeCompare(b.file));
const recordByFile = new Map(records.map((record) => [record.file, record]));
for (const record of records) {
  for (const ref of record.imports) {
    if (ref.target && recordByFile.has(ref.target)) recordByFile.get(ref.target).importedBy.push(record.file);
  }
}
for (const record of records) record.importedBy = [...new Set(record.importedBy)].sort();

const staticCycles = stronglyConnected(records, new Set(['static', 're-export']));
const runtimeCycles = stronglyConnected(records, new Set(['static', 're-export', 'dynamic']));
const domainMap = new Map();
for (const record of records) {
  const current = domainMap.get(record.domain) ?? { domain: record.domain, files: 0, lines: 0, localEdges: 0, exports: 0 };
  current.files += 1;
  current.lines += record.lines;
    current.localEdges += record.imports.filter((item) => item.target
      && recordByFile.has(item.target)
      && item.kind !== 'dynamic').length;
  current.exports += record.exports.length;
  domainMap.set(record.domain, current);
}

const digestInput = records.map((record) => `${record.file}:${record.sha256}`).join('\n');
const manifest = {
  schema: 'EvaRuntimeContractMap/v1',
  sourceDigest: crypto.createHash('sha256').update(digestInput).digest('hex'),
  summary: {
    files: records.length,
    localEdges: records.reduce((sum, record) => sum + record.imports.filter((item) => item.target
      && recordByFile.has(item.target)
      && item.kind !== 'dynamic').length, 0),
    dynamicEdges: records.reduce((sum, record) => sum + record.imports.filter((item) => item.target
      && recordByFile.has(item.target)
      && item.kind === 'dynamic').length, 0),
    cycles: staticCycles.length,
    runtimeCycles: runtimeCycles.length,
  },
  domains: [...domainMap.values()].sort((a, b) => a.domain.localeCompare(b.domain)),
  entrypoints: records.filter((record) => !record.importedBy.length).map((record) => record.file),
  leaves: records.filter((record) => !record.imports.some((item) => item.target && recordByFile.has(item.target))).map((record) => record.file),
  cycles: staticCycles,
  runtimeCycles,
  files: records,
};

const json = `${JSON.stringify(manifest, null, 2)}\n`;
const markdown = markdownFor(manifest);

if (checkOnly) {
  const expectedJson = fs.existsSync(outputJson) ? fs.readFileSync(outputJson, 'utf8') : '';
  const expectedMarkdown = fs.existsSync(outputMarkdown) ? fs.readFileSync(outputMarkdown, 'utf8') : '';
  const semantic = fs.existsSync(semanticMarkdown) ? fs.readFileSync(semanticMarkdown, 'utf8') : '';
  const semanticDigest = semantic.match(/<!--\s*eva-runtime-source-digest:\s*([a-f0-9]{64})\s*-->/u)?.[1] ?? '';
  const generatedStale = expectedJson !== json || expectedMarkdown !== markdown;
  const semanticStale = semanticDigest !== manifest.sourceDigest;
  if (generatedStale || semanticStale) {
    if (generatedStale) {
      process.stderr.write('De gegenereerde Eva runtime-contractkaart is verouderd. Draai node scripts/generate-runtime-contract-map.mjs.\n');
    }
    if (semanticStale) {
      process.stderr.write(`De semantische runtimekaart is niet gereviewd voor brondigest ${manifest.sourceDigest}.\n`);
    }
    process.exitCode = 1;
  } else {
    process.stdout.write(`Eva runtime-contractkaart klopt voor ${records.length} bestanden.\n`);
  }
} else {
  fs.writeFileSync(outputJson, json);
  fs.writeFileSync(outputMarkdown, markdown);
  process.stdout.write(`Eva runtime-contractkaart geschreven voor ${records.length} bestanden.\n`);
}
