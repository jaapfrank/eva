import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';

const startedAt = new Date().toISOString();

function sourceFiles(dir) {
  const rows = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) rows.push(...sourceFiles(full));
    else if (entry.isFile() && /\.(?:js|mjs|json)$/i.test(entry.name)) rows.push(full);
  }
  return rows;
}

function codeHash() {
  const hash = crypto.createHash('sha256');
  const roots = [
    path.join(config.root, 'src'),
    path.join(config.root, 'package.json'),
    path.join(config.root, 'package-lock.json'),
  ];
  const files = roots.flatMap((target) => {
    try {
      return fs.statSync(target).isDirectory() ? sourceFiles(target) : [target];
    } catch {
      return [];
    }
  }).sort();
  for (const file of files) {
    hash.update(path.relative(config.root, file));
    hash.update('\0');
    hash.update(fs.readFileSync(file));
    hash.update('\0');
  }
  return hash.digest('hex');
}

const loadedCodeSha256 = codeHash();
let recentDiskHash = loadedCodeSha256;
let recentDiskCheck = 0;

export function runtimeVersion() {
  if (Date.now() - recentDiskCheck > 1000) {
    recentDiskCheck = Date.now();
    try { recentDiskHash = codeHash(); } catch { /* houd de laatste controle */ }
  }
  return {
    pid: process.pid,
    started_at: startedAt,
    loaded_code_sha256: loadedCodeSha256,
    disk_code_sha256: recentDiskHash,
    code_matches_disk: loadedCodeSha256 === recentDiskHash,
  };
}
