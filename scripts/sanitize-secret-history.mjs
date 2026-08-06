#!/usr/bin/env node
import 'dotenv/config';
import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';
import { openThought } from '../src/self/secretmind.js';

const root = path.resolve(process.cwd());
const apply = process.argv.includes('--apply');
const environmentSecrets = [
  ['HA_TOKEN', process.env.HA_TOKEN],
  ['TELEGRAM_BOT_TOKEN', process.env.TELEGRAM_BOT_TOKEN],
  ['UNIFI_API_KEY', process.env.UNIFI_API_KEY],
  ['OPENAI_API_KEY', process.env.OPENAI_API_KEY],
  ['EVA_WEB_TOKEN', process.env.EVA_WEB_TOKEN],
].filter(([, value]) => typeof value === 'string' && value.length >= 8);

if (!['HA_TOKEN', 'TELEGRAM_BOT_TOKEN', 'UNIFI_API_KEY'].every(
  (name) => environmentSecrets.some(([candidate]) => candidate === name),
)) {
  throw new Error('sanering vereist HA_TOKEN, TELEGRAM_BOT_TOKEN en UNIFI_API_KEY in de runtimeomgeving');
}

function safeSecretName(value = '') {
  return String(value || 'SECRET').toUpperCase().replace(/[^A-Z0-9_-]+/g, '_').slice(0, 80);
}

function currentVaultSecrets(file) {
  try {
    const db = new Database(file, { readonly: true, fileMustExist: true });
    try {
      const row = db.prepare("SELECT value FROM kv WHERE key='vault'").get();
      let vault = {};
      try { vault = JSON.parse(String(row?.value || '{}')); } catch { vault = {}; }
      return Object.entries(vault).flatMap(([name, sealed]) => {
        const raw = String(sealed || '');
        const opened = raw.startsWith('encrypted:')
          ? openThought(raw.slice('encrypted:'.length))
          : raw;
        return typeof opened === 'string' && opened.length >= 8
          ? [[`VAULT_${safeSecretName(name)}`, opened]]
          : [];
      });
    } finally {
      db.close();
    }
  } catch {
    return [];
  }
}

function sqliteFiles(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) return sqliteFiles(file);
    return /\.db$/i.test(entry.name) ? [file] : [];
  });
}

function quoteIdentifier(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function containsSecret(buffer, secret) {
  return buffer.includes(Buffer.from(secret));
}

const activeDb = path.join(root, 'data/eva.db');
const requestedFiles = process.argv.flatMap((argument, index, all) => (
  argument === '--file' && all[index + 1] ? [path.resolve(all[index + 1])] : []
));
const files = (requestedFiles.length
  ? requestedFiles
  : [activeDb, ...sqliteFiles(path.join(root, 'data/backups'))])
  .filter((file, index, all) => all.indexOf(file) === index && fs.existsSync(file));
const secrets = [
  ...environmentSecrets,
  ...currentVaultSecrets(activeDb),
  ...files.flatMap((file) => currentVaultSecrets(file)),
]
  .filter(([, value], index, all) => all.findIndex(([, candidate]) => candidate === value) === index);
const report = [];

for (const file of files) {
  const relative = path.relative(root, file);
  // Draai dit script met de Eva-service uit. Materialiseer eerst eventueel WAL,
  // zodat een geheim niet buiten de hoofdfile aan de bytescan ontsnapt.
  try {
    const checkpoint = new Database(file);
    try { checkpoint.pragma('busy_timeout = 10000'); checkpoint.pragma('wal_checkpoint(TRUNCATE)'); }
    finally { checkpoint.close(); }
  } catch {
    // De invalid_sqlite-route hieronder rapporteert een onleesbaar bestand.
  }
  const before = fs.readFileSync(file);
  const found = secrets.filter(([, value]) => containsSecret(before, value)).map(([name]) => name);
  if (!found.length) {
    report.push({ file: relative, status: 'clean', replacements: 0 });
    continue;
  }
  if (before.subarray(0, 16).toString() !== 'SQLite format 3\u0000') {
    report.push({ file: relative, status: 'invalid_sqlite', secret_names: found });
    continue;
  }
  if (!apply) {
    report.push({ file: relative, status: 'would_sanitize', secret_names: found });
    continue;
  }

  const db = new Database(file);
  let replacements = 0;
  try {
    db.pragma('busy_timeout = 10000');
    db.pragma('wal_checkpoint(TRUNCATE)');
    db.pragma('journal_mode = DELETE');
    const tables = db.pragma('table_list')
      .filter((item) => item.schema === 'main' && item.type === 'table' && !item.name.startsWith('sqlite_'));
    const redact = db.transaction(() => {
      for (const { name: table } of tables) {
        const columns = db.prepare(`PRAGMA table_info(${quoteIdentifier(table)})`).all();
        for (const { name: column } of columns) {
          const tableName = quoteIdentifier(table);
          const columnName = quoteIdentifier(column);
          for (const [secretName, secret] of secrets) {
            const marker = `[REDACTED_SECRET:${secretName}]`;
            const info = db.prepare(`UPDATE ${tableName}
              SET ${columnName}=replace(${columnName}, ?, ?)
              WHERE typeof(${columnName})='text' AND instr(${columnName}, ?)>0`)
              .run(secret, marker, secret);
            replacements += Number(info.changes || 0);
          }
        }
      }
    });
    redact();
    db.exec('VACUUM');
    const integrity = db.pragma('quick_check', { simple: true });
    if (integrity !== 'ok') throw new Error(`quick_check faalde voor ${relative}`);
  } finally {
    db.close();
  }
  const after = fs.readFileSync(file);
  const residual = secrets.filter(([, value]) => containsSecret(after, value)).map(([name]) => name);
  report.push({
    file: relative,
    status: residual.length ? 'residual_secret' : 'sanitized',
    replacements,
    ...(residual.length ? { secret_names: residual } : {}),
  });
}

console.log(JSON.stringify({ apply, files: report }, null, 2));
if (report.some((item) => ['invalid_sqlite', 'residual_secret'].includes(item.status))) process.exitCode = 2;
