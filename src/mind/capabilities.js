// Versioned procedures and executable add-ons that Eva can create, test and
// activate without changing her core or widening her tool permissions.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import db from '../db.js';
import { config } from '../config.js';
import { listSecretKeys } from '../self/vault.js';

db.exec(`
CREATE TABLE IF NOT EXISTS eva_capabilities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'staged',
  instructions TEXT,
  triggers_json TEXT,
  manifest_json TEXT,
  evidence TEXT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  last_used_ts TEXT,
  UNIQUE(kind, slug)
);
CREATE INDEX IF NOT EXISTS idx_eva_capabilities_active
  ON eva_capabilities(kind, status, updated_ts);
CREATE TABLE IF NOT EXISTS eva_capability_receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  capability_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  slug TEXT NOT NULL,
  version INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  command_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  FOREIGN KEY(capability_id) REFERENCES eva_capabilities(id)
);
`);

const KINDS = new Set(['skill', 'addon']);
const STATUSES = new Set(['staged', 'active', 'disabled', 'quarantined']);

function clean(value, limit = 4_000) {
  return String(value ?? '').replaceAll('\u0000', '').trim().slice(0, limit);
}

function slugify(value) {
  return clean(value, 100)
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

function json(value, fallback) {
  try { return JSON.parse(String(value || '')); } catch { return fallback; }
}

function out(row) {
  return row ? {
    ...row,
    triggers: json(row.triggers_json, []),
    manifest: json(row.manifest_json, {}),
  } : null;
}

function capability(kind, slug) {
  return out(db.prepare('SELECT * FROM eva_capabilities WHERE kind=? AND slug=?').get(kind, slug));
}

function safeTriggers(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map((value) => clean(value, 80).toLowerCase())
    .filter((value) => value.length >= 3))].slice(0, 20);
}

export function stageSkill({
  name,
  description = '',
  instructions,
  triggers = [],
  testEntrypoint,
  testRuntime = 'node',
  testArguments = [],
} = /** @type {any} */ ({})) {
  const slug = slugify(name);
  const body = clean(instructions, 8_000);
  if (!slug || !body) throw new Error('een vaardigheid heeft een naam en concrete werkwijze nodig');
  const test = workspaceEntrypoint(testEntrypoint);
  const testManifest = {
    test_entrypoint: test.relative,
    test_runtime: ['node', 'python3'].includes(testRuntime) ? testRuntime : 'node',
    test_arguments: (Array.isArray(testArguments) ? testArguments : []).map((value) => clean(value, 500)).slice(0, 30),
    test_sha256: createHash('sha256').update(fs.readFileSync(test.absolute)).digest('hex'),
    permissions: ['workspace:read-write', 'network:none'],
  };
  const previous = capability('skill', slug);
  const now = new Date().toISOString();
  db.prepare(`INSERT INTO eva_capabilities
    (kind,slug,name,description,version,status,instructions,triggers_json,manifest_json,evidence,created_ts,updated_ts)
    VALUES ('skill',?,?,?,?, 'staged',?,?,?,'',?,?)
    ON CONFLICT(kind,slug) DO UPDATE SET
      name=excluded.name, description=excluded.description,
      version=eva_capabilities.version+1, status='staged',
      instructions=excluded.instructions, triggers_json=excluded.triggers_json,
      evidence='', updated_ts=excluded.updated_ts`)
    .run(slug, clean(name, 120), clean(description, 500), previous?.version || 1,
      body, JSON.stringify(safeTriggers(triggers)), JSON.stringify(testManifest), previous?.created_ts || now, now);
  return capability('skill', slug);
}

function workspaceEntrypoint(rawValue) {
  const raw = clean(rawValue, 400);
  const absolute = path.resolve(raw.startsWith('/') ? raw : path.join(config.root, raw));
  const workspace = path.resolve(config.workspaceDir);
  if (!(absolute === workspace || absolute.startsWith(`${workspace}${path.sep}`))) {
    throw new Error('een add-on-entrypoint moet in Eva haar werkruimte staan');
  }
  if (!fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
    throw new Error('de add-on-entrypoint bestaat nog niet');
  }
  const real = fs.realpathSync(absolute);
  if (!(real === workspace || real.startsWith(`${workspace}${path.sep}`))) {
    throw new Error('de add-on-entrypoint wijst buiten de werkruimte');
  }
  return { absolute: real, relative: path.relative(config.root, real) };
}

function capabilityDigest(item) {
  const payload = item.kind === 'skill'
    ? {
      instructions: item.instructions,
      triggers: item.triggers,
      test: item.manifest,
    }
    : {
      description: item.description,
      triggers: item.triggers,
      manifest: item.manifest,
    };
  return createHash('sha256').update(JSON.stringify(payload)).digest('hex');
}

export function getCapability(kind, name) {
  const safeKind = clean(kind, 20).toLowerCase();
  const slug = slugify(name);
  return KINDS.has(safeKind) && slug ? capability(safeKind, slug) : null;
}

export function stageAddon({
  name, description = '', manifest = {}, triggers = [],
} = /** @type {any} */ ({})) {
  const slug = slugify(name);
  if (!slug) throw new Error('een add-on heeft een naam nodig');
  const entry = workspaceEntrypoint(manifest?.entrypoint);
  const checksum = createHash('sha256').update(fs.readFileSync(entry.absolute)).digest('hex');
  const requestedPermissions = [...new Set((Array.isArray(manifest.permissions) ? manifest.permissions : [])
    .map((value) => clean(value, 80)).filter(Boolean))];
  const supportedPermissions = new Set(['workspace:read-write', 'network:none']);
  if (requestedPermissions.some((permission) => !supportedPermissions.has(permission))) {
    throw new Error('add-ons draaien alleen met workspace:read-write en network:none; ruimere rechten zijn niet beschikbaar');
  }
  const safeManifest = {
    entrypoint: entry.relative,
    runtime: ['node', 'python3'].includes(manifest.runtime) ? manifest.runtime : 'node',
    source: clean(manifest.source || 'eigen werkruimte', 500),
    source_version: clean(manifest.source_version || '', 120),
    sha256: checksum,
    test_arguments: (Array.isArray(manifest.test_arguments) ? manifest.test_arguments : [])
      .map((value) => clean(value, 500)).slice(0, 30),
    permissions: ['workspace:read-write', 'network:none'],
  };
  const previous = capability('addon', slug);
  const now = new Date().toISOString();
  db.prepare(`INSERT INTO eva_capabilities
    (kind,slug,name,description,version,status,instructions,triggers_json,manifest_json,evidence,created_ts,updated_ts)
    VALUES ('addon',?,?,?,?, 'staged','',?,?, '',?,?)
    ON CONFLICT(kind,slug) DO UPDATE SET
      name=excluded.name, description=excluded.description,
      version=eva_capabilities.version+1, status='staged',
      triggers_json=excluded.triggers_json, manifest_json=excluded.manifest_json,
      evidence='', updated_ts=excluded.updated_ts`)
    .run(slug, clean(name, 120), clean(description, 500), previous?.version || 1,
      JSON.stringify(safeTriggers(triggers)), JSON.stringify(safeManifest), previous?.created_ts || now, now);
  return capability('addon', slug);
}

export function setCapabilityStatus(kind, name, status, evidence = '') {
  const safeKind = clean(kind, 20).toLowerCase();
  const slug = slugify(name);
  const safeStatus = clean(status, 30).toLowerCase();
  if (!KINDS.has(safeKind) || !STATUSES.has(safeStatus)) throw new Error('ongeldige capabilitystatus');
  const current = capability(safeKind, slug);
  if (!current) throw new Error('onbekende vaardigheid of add-on');
  if (safeStatus === 'active') throw new Error('activeren vereist een exact proefreceipt voor deze capabilityversie');
  db.prepare('UPDATE eva_capabilities SET status=?, evidence=?, updated_ts=? WHERE kind=? AND slug=?')
    .run(safeStatus, clean(evidence, 1_500), new Date().toISOString(), safeKind, slug);
  return capability(safeKind, slug);
}

export function capabilityTestSpec(kind, name) {
  const item = getCapability(kind, name);
  if (!item) throw new Error('onbekende vaardigheid of add-on');
  if (item.status !== 'staged') throw new Error('alleen een gestagede capability kan worden beproefd');
  const entry = workspaceEntrypoint(item.manifest?.test_entrypoint || item.manifest?.entrypoint);
  const currentChecksum = createHash('sha256').update(fs.readFileSync(entry.absolute)).digest('hex');
  const expectedChecksum = item.manifest?.test_sha256 || item.manifest?.sha256;
  if (currentChecksum !== expectedChecksum) throw new Error('de proefcode veranderde sinds staging; stage de capability opnieuw');
  return {
    item,
    contentHash: capabilityDigest(item),
    runtime: item.manifest?.test_runtime || item.manifest?.runtime,
    entrypoint: entry.relative,
    arguments: Array.isArray(item.manifest?.test_arguments) ? item.manifest.test_arguments : [],
  };
}

export function issueCapabilityReceipt({ kind, name, command, result } = {}) {
  const spec = capabilityTestSpec(kind, name);
  if (!result?.ok || !Number.isInteger(Number(result.exit_code))) {
    throw new Error('alleen een geslaagde, controleerbare capabilityproef krijgt een receipt');
  }
  const now = new Date().toISOString();
  const info = db.prepare(`INSERT INTO eva_capability_receipts
    (capability_id,kind,slug,version,content_hash,command_json,result_json,created_ts)
    VALUES (?,?,?,?,?,?,?,?)`).run(
    spec.item.id,
    spec.item.kind,
    spec.item.slug,
    spec.item.version,
    spec.contentHash,
    JSON.stringify(command || {}),
    JSON.stringify(result),
    now,
  );
  return {
    id: Number(info.lastInsertRowid),
    capability_id: spec.item.id,
    kind: spec.item.kind,
    slug: spec.item.slug,
    version: spec.item.version,
    content_hash: spec.contentHash,
    command: command || {},
    result,
    created_ts: now,
  };
}

export function activateCapability(kind, name, receiptId) {
  const item = getCapability(kind, name);
  if (!item) throw new Error('onbekende vaardigheid of add-on');
  if (item.status !== 'staged') throw new Error('alleen de actuele gestagede versie kan worden geactiveerd');
  const receipt = db.prepare('SELECT * FROM eva_capability_receipts WHERE id=?').get(Number(receiptId));
  const contentHash = capabilityDigest(item);
  if (!receipt
    || receipt.capability_id !== item.id
    || receipt.kind !== item.kind
    || receipt.slug !== item.slug
    || receipt.version !== item.version
    || receipt.content_hash !== contentHash) {
    throw new Error('het proefreceipt hoort niet bij exact deze capabilityversie');
  }
  const result = json(receipt.result_json, {});
  if (!result.ok || !Number.isInteger(Number(result.exit_code))) {
    throw new Error('het proefreceipt bevat geen geslaagde geverifieerde uitvoering');
  }
  if (item.kind === 'addon') capabilityTestSpec(kind, name);
  const evidence = JSON.stringify({ receipt_id: receipt.id, content_hash: contentHash, result });
  db.prepare('UPDATE eva_capabilities SET status=?, evidence=?, updated_ts=? WHERE id=?')
    .run('active', clean(evidence, 1_500), new Date().toISOString(), item.id);
  return getCapability(kind, name);
}

export function useActiveCapability(name = '') {
  const slug = slugify(name);
  const item = slug
    ? db.prepare("SELECT * FROM eva_capabilities WHERE slug=? AND status='active' ORDER BY updated_ts DESC LIMIT 1").get(slug)
    : db.prepare("SELECT * FROM eva_capabilities WHERE status='active' ORDER BY updated_ts DESC LIMIT 1").get();
  const active = out(item);
  if (!active) throw new Error('er is geen passende actieve capability om te demonstreren');
  const storedEvidence = json(active.evidence, {});
  const contentHash = capabilityDigest(active);
  const receipt = db.prepare('SELECT * FROM eva_capability_receipts WHERE id=?').get(Number(storedEvidence.receipt_id));
  if (!receipt
    || receipt.capability_id !== active.id
    || receipt.version !== active.version
    || receipt.content_hash !== contentHash
    || storedEvidence.content_hash !== contentHash) {
    throw new Error('de actieve capability heeft geen geldig receipt voor haar huidige inhoud');
  }
  if (active.kind === 'addon') {
    const entry = workspaceEntrypoint(active.manifest.entrypoint);
    const checksum = createHash('sha256').update(fs.readFileSync(entry.absolute)).digest('hex');
    if (checksum !== active.manifest.sha256) throw new Error('de actieve add-oncode veranderde; quarantaineer en stage opnieuw');
  }
  const now = new Date().toISOString();
  db.prepare('UPDATE eva_capabilities SET last_used_ts=? WHERE id=?').run(now, active.id);
  return {
    capability: getCapability(active.kind, active.name),
    receipt_id: receipt.id,
    content_hash: contentHash,
    demonstrated: true,
    runtime: active.kind === 'addon' ? active.manifest.runtime : null,
    entrypoint: active.kind === 'addon' ? active.manifest.entrypoint : null,
  };
}

export function listCapabilities({ kind = null, status = null, limit = 50 } = {}) {
  const clauses = [];
  const args = [];
  if (kind && KINDS.has(kind)) { clauses.push('kind=?'); args.push(kind); }
  if (status && STATUSES.has(status)) { clauses.push('status=?'); args.push(status); }
  return db.prepare(`SELECT * FROM eva_capabilities${clauses.length ? ` WHERE ${clauses.join(' AND ')}` : ''}
    ORDER BY status='active' DESC, updated_ts DESC LIMIT ?`)
    .all(...args, Math.max(1, Math.min(100, Number(limit) || 50))).map(out);
}

function words(value) {
  return new Set(clean(value, 2_000).toLowerCase().match(/[a-zà-ÿ0-9]{3,}/g) || []);
}

const INVENTORY_STOP_WORDS = new Set([
  'aan', 'als', 'dat', 'deze', 'dit', 'een', 'eigen', 'heeft', 'het', 'hoe', 'kan', 'kun',
  'met', 'niet', 'of', 'ondersteunt', 'voor', 'wat', 'welke', 'zijn',
]);
function ranked(items, score, limit = 8) {
  return items.map((item) => ({ item, score: score(item) })).filter(({ score: value }) => value > 0)
    .sort((left, right) => right.score - left.score).slice(0, limit).map(({ item }) => item);
}

export function capabilityInventory(question = '', coreTools = [], offeredToolNames = []) {
  const terms = [...words(question)].filter((term) => !INVENTORY_STOP_WORDS.has(term));
  const score = (value) => terms.reduce((total, term) => (
    total + (String(value || '').toLowerCase().includes(term) ? 1 : 0)
  ), 0);
  const offered = new Set(offeredToolNames.map(String));
  const matchingTools = ranked(coreTools.filter((tool) => tool.name !== 'inspecteer_vermogen'),
    (tool) => score(`${tool.name} ${tool.description}`)).map((tool) => ({
    name: tool.name, description: tool.description, offered_this_turn: offered.has(tool.name),
    parameters: Object.entries(tool.parameters?.properties || {}).map(([name, schema]) => ({
      name, type: schema?.type || null, description: schema?.description || '',
    })),
  }));
  const sourceRefs = ranked(['src/integrations', 'src/mind'].flatMap((relative) => {
    const directory = path.join(config.root, relative);
    return fs.existsSync(directory) ? fs.readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && /\.[cm]?js$/i.test(entry.name))
      .map((entry) => `${relative}/${entry.name}`) : [];
  }), (source) => score(path.basename(source).replace(/\.[^.]+$/, '')));
  const extensions = ranked(listCapabilities({ limit: 100 }),
    (item) => score(`${item.name} ${item.slug} ${item.description} ${(item.triggers || []).join(' ')}`))
    .map(({ kind, name, status, version, updated_ts: updatedTs }) => ({
      kind, name, status, version, updated_ts: updatedTs,
    }));
  const secretNames = listSecretKeys().filter((name) => score(name) > 0);
  return {
    ok: true, changed: false, verified: true,
    message: `Passende kerntools: ${matchingTools.map((tool) => `${tool.name}${tool.offered_this_turn ? ' (aangeboden)' : ' (niet aangeboden)'}`).join(', ') || 'geen'}. Relevante bronbestanden: ${sourceRefs.join(', ') || 'geen automatisch gevonden'}. Passende extensies: ${extensions.map((item) => `${item.name} (${item.status})`).join(', ') || 'geen'}. Geconfigureerde geheimnamen: ${secretNames.join(', ') || 'geen passende naam gevonden'}.`,
    question: String(question || '').trim(), source_refs: sourceRefs,
    configured_secret_names: secretNames, matching_tools: matchingTools, matching_extensions: extensions,
    note: 'Geheimwaarden zijn niet gelezen of getoond. Een descriptor bewijst beschikbaarheid; werking vereist zo nodig een read-only statusprobe of echte actie.',
  };
}

export function capabilityContextText(seed = '') {
  const wanted = words(seed);
  const active = ranked(listCapabilities({ status: 'active', limit: 30 }), (item) => {
      // Triggers mogen woordgroepen zijn. Vergelijk hun losse betekeniswoorden;
      // anders matcht "actuele uitleesclaim" nooit op een gewone zin.
      const terms = [
        ...item.triggers.flatMap((trigger) => [...words(trigger)]),
        ...words(`${item.name} ${item.description}`),
      ];
      return !seed ? 1 : terms.filter((term) => wanted.has(term)).length;
    }, 4);
  if (!active.length) return '';
  return active.map((item) => item.kind === 'skill'
    ? `Vaardigheid ${item.name} v${item.version}: ${item.instructions}`
    : `Add-on ${item.name} v${item.version}: voer ${item.manifest.runtime} ${item.manifest.entrypoint} uit binnen de geïsoleerde werkruimte. ${item.description}`)
    .join('\n');
}
