import path from 'node:path';
import { config } from './config.js';
import { dependencyTreeSha256, runtimeTreeSha256 } from '../deploy/eva-runtime-repair-protocol.mjs';

const startedAt = new Date().toISOString();

export function runtimeCodeSha256({ root = config.root, overrides = new Map() } = {}) {
  return runtimeTreeSha256(root, overrides);
}

const loadedCodeSha256 = runtimeCodeSha256();
const loadedDependencySha256 = dependencyTreeSha256(path.join(config.root, 'node_modules'));
let recentDiskHash = loadedCodeSha256;
let recentDependencyHash = loadedDependencySha256;
let recentDiskCheck = 0;
let recentDependencyCheck = Date.now();

export function runtimeVersion() {
  if (Date.now() - recentDiskCheck > 1000) {
    recentDiskCheck = Date.now();
    try { recentDiskHash = runtimeCodeSha256(); } catch { recentDiskHash = null; }
  }
  if (Date.now() - recentDependencyCheck > 30_000) {
    recentDependencyCheck = Date.now();
    try { recentDependencyHash = dependencyTreeSha256(path.join(config.root, 'node_modules')); }
    catch { recentDependencyHash = null; }
  }
  return {
    pid: process.pid,
    started_at: startedAt,
    loaded_code_sha256: loadedCodeSha256,
    disk_code_sha256: recentDiskHash,
    code_matches_disk: loadedCodeSha256 === recentDiskHash,
    loaded_dependency_tree_sha256: loadedDependencySha256,
    disk_dependency_tree_sha256: recentDependencyHash,
    dependency_matches_disk: loadedDependencySha256 === recentDependencyHash,
  };
}
