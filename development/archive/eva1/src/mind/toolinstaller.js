// Veilige npm-discovery en staging voor toekomstige Eva-tools.
//
// Deze module installeert uitsluitend data. Geïnstalleerde packages worden nooit
// geïmporteerd of uitgevoerd. Activatie vereist een door de aanroeper geleverde,
// succesvolle health-receipt die aan exact dezelfde artifact-checksum is gebonden.
import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import db from '../db.js';
import { config } from '../config.js';

const REGISTRY_ORIGIN = 'https://registry.npmjs.org';
const EXTENSIONS_ROOT = path.join(config.workspaceDir, 'extensions');
const INSTALL_TIMEOUT_MS = 6 * 60_000;
const MAX_REGISTRY_BYTES = 8 * 1024 * 1024;
const MAX_HASH_FILES = 50_000;
const MAX_HASH_BYTES = 1024 * 1024 * 1024;
const STATUSES = new Set([
  'discovered', 'staged', 'health_checked', 'active', 'disabled', 'quarantined',
]);
const MUTABLE_STATUSES = new Set(['disabled', 'quarantined']);
const EXACT_VERSION = /^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
const PACKAGE_NAME = /^(?:@[a-z0-9][a-z0-9._-]*\/[a-z0-9][a-z0-9._-]*|[a-z0-9][a-z0-9._-]*)$/;

db.exec(`
CREATE TABLE IF NOT EXISTS eva_tool_installations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  package_name TEXT NOT NULL,
  package_version TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'discovered',
  registry_url TEXT NOT NULL,
  target_path TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  provenance_json TEXT NOT NULL DEFAULT '{}',
  metadata_sha256 TEXT,
  manifest_sha256 TEXT,
  lock_sha256 TEXT,
  tree_sha256 TEXT,
  artifact_sha256 TEXT,
  health_receipt_json TEXT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  installed_ts TEXT,
  activated_ts TEXT,
  last_error TEXT,
  revision INTEGER NOT NULL DEFAULT 1,
  UNIQUE(package_name, package_version)
);
CREATE INDEX IF NOT EXISTS idx_eva_tool_install_status
  ON eva_tool_installations(status, updated_ts);
CREATE TABLE IF NOT EXISTS eva_tool_install_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  installation_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  detail TEXT,
  FOREIGN KEY(installation_id) REFERENCES eva_tool_installations(id)
);
CREATE INDEX IF NOT EXISTS idx_eva_tool_install_events
  ON eva_tool_install_events(installation_id, ts);
`);

function nowIso() {
  return new Date().toISOString();
}

function clean(value, limit = 2_000) {
  return String(value ?? '').replaceAll('\u0000', '').trim().slice(0, limit);
}

function json(value, fallback = {}) {
  try { return JSON.parse(String(value || '')); } catch { return fallback; }
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
}

function digest(value) {
  const input = Buffer.isBuffer(value) ? value : Buffer.from(
    typeof value === 'string' ? value : JSON.stringify(stable(value)),
  );
  return createHash('sha256').update(input).digest('hex');
}

function normalizePackageName(value) {
  const name = clean(value, 214).toLowerCase();
  if (!PACKAGE_NAME.test(name) || name.includes('..')) {
    throw new Error('npm-package heeft geen geldige exacte registrynaam');
  }
  return name;
}

function normalizeExactVersion(value) {
  const version = clean(value, 120);
  if (!EXACT_VERSION.test(version)) {
    throw new Error('install vereist een exacte npm-versie, bijvoorbeeld 1.2.3; tags en ranges zijn niet toegestaan');
  }
  return version;
}

function packageSlug(packageName, version) {
  const base = `${packageName.replace(/^@/, '').replace(/[^a-z0-9]+/g, '-')}-${version.replace(/[^a-z0-9]+/gi, '-')}`
    .replace(/^-+|-+$/g, '').slice(0, 90);
  return `${base}-${digest(`${packageName}@${version}`).slice(0, 10)}`;
}

function pathInside(root, target) {
  return target === root || target.startsWith(`${root}${path.sep}`);
}

function checkedExtensionsRoot({ mustExist = false } = {}) {
  const workspace = fs.realpathSync(config.workspaceDir);
  const root = path.resolve(EXTENSIONS_ROOT);
  if (!pathInside(workspace, root)) throw new Error('extensiemap valt buiten de werkruimte');
  if (!fs.existsSync(root)) {
    if (mustExist) throw new Error('extensiemap bestaat niet');
    return root;
  }
  const stat = fs.lstatSync(root);
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new Error('extensiemap moet een echte werkruimtemap zijn');
  }
  const real = fs.realpathSync(root);
  if (!pathInside(workspace, real)) throw new Error('extensiemap wijst buiten de werkruimte');
  return real;
}

function safeTarget(slug) {
  const root = checkedExtensionsRoot();
  const target = path.resolve(root, slug);
  if (!slug || target === root || !pathInside(root, target)) {
    throw new Error('ongeldig extensiedoel');
  }
  if (fs.existsSync(target)) {
    const stat = fs.lstatSync(target);
    if (stat.isSymbolicLink()) throw new Error('extensiedoel mag geen symlink zijn');
    const real = fs.realpathSync(target);
    if (!pathInside(root, real)) throw new Error('extensiedoel wijst buiten de extensiemap');
  }
  return target;
}

function out(row) {
  if (!row) return null;
  return {
    ...row,
    metadata: json(row.metadata_json),
    provenance: json(row.provenance_json),
    health_receipt: json(row.health_receipt_json, null),
    revision: Number(row.revision || 0),
  };
}

function byIdentifier(identifier, version = null) {
  if (identifier && typeof identifier === 'object') {
    if (identifier.id != null) return out(db.prepare('SELECT * FROM eva_tool_installations WHERE id=?').get(Number(identifier.id)));
    if (identifier.slug) return out(db.prepare('SELECT * FROM eva_tool_installations WHERE slug=?').get(clean(identifier.slug, 140)));
    const name = normalizePackageName(identifier.packageName || identifier.name || identifier.package);
    const wantedVersion = identifier.version == null ? version : identifier.version;
    if (wantedVersion != null) {
      return out(db.prepare('SELECT * FROM eva_tool_installations WHERE package_name=? AND package_version=?')
        .get(name, normalizeExactVersion(wantedVersion)));
    }
    return out(db.prepare('SELECT * FROM eva_tool_installations WHERE package_name=? ORDER BY updated_ts DESC LIMIT 1').get(name));
  }
  const raw = clean(identifier, 214);
  if (!raw) return null;
  const bySlug = out(db.prepare('SELECT * FROM eva_tool_installations WHERE slug=?').get(raw));
  if (bySlug) return bySlug;
  const name = normalizePackageName(raw);
  if (version != null) {
    return out(db.prepare('SELECT * FROM eva_tool_installations WHERE package_name=? AND package_version=?')
      .get(name, normalizeExactVersion(version)));
  }
  return out(db.prepare('SELECT * FROM eva_tool_installations WHERE package_name=? ORDER BY updated_ts DESC LIMIT 1').get(name));
}

function recordTransition(id, fromStatus, toStatus, detail = '') {
  if (!STATUSES.has(toStatus)) throw new Error(`ongeldige installatiestatus: ${toStatus}`);
  db.prepare(`INSERT INTO eva_tool_install_events
    (installation_id,ts,from_status,to_status,detail) VALUES (?,?,?,?,?)`)
    .run(Number(id), nowIso(), fromStatus || null, toStatus, clean(detail, 1_500) || null);
}

function compactMetadata(packument, selected, requestedVersion) {
  const repository = typeof selected.repository === 'string'
    ? selected.repository
    : selected.repository?.url || null;
  return {
    name: selected.name,
    version: selected.version,
    description: clean(selected.description, 1_000) || null,
    license: selected.license || null,
    repository,
    homepage: selected.homepage || null,
    deprecated: selected.deprecated || null,
    engines: selected.engines || {},
    bin: selected.bin || {},
    dependencies: selected.dependencies || {},
    optional_dependencies: selected.optionalDependencies || {},
    peer_dependencies: selected.peerDependencies || {},
    install_scripts_ignored: Object.fromEntries(
      Object.entries(selected.scripts || {}).filter(([name]) => (
        ['preinstall', 'install', 'postinstall', 'prepare'].includes(name)
      )),
    ),
    requested_version: requestedVersion,
    latest_dist_tag: packument['dist-tags']?.latest || null,
  };
}

function compactProvenance(registryUrl, selected) {
  return {
    registry: REGISTRY_ORIGIN,
    registry_url: registryUrl,
    fetched_at: nowIso(),
    package: selected.name,
    version: selected.version,
    tarball: selected.dist?.tarball || null,
    integrity: selected.dist?.integrity || null,
    shasum: selected.dist?.shasum || null,
    npm_signatures: Array.isArray(selected.dist?.signatures) ? selected.dist.signatures : [],
    attestations: selected.dist?.attestations || null,
  };
}

function assertRegistryDependencySpecs(selected) {
  const groups = [
    selected.dependencies,
    selected.optionalDependencies,
    selected.peerDependencies,
  ];
  for (const dependencies of groups) {
    for (const [name, rawSpec] of Object.entries(dependencies || {})) {
      normalizePackageName(name);
      const spec = clean(rawSpec, 500);
      if (/^(?:file|link|workspace|git|git\+|https?|github|gitlab|bitbucket):/i.test(spec)
        || /^[^@\s]+\/[^\s]+(?:#.*)?$/.test(spec)) {
        throw new Error(`dependency ${name} gebruikt geen zuivere npm-registryspec`);
      }
    }
  }
}

async function responseTextLimited(response) {
  const length = Number(response.headers.get('content-length'));
  if (Number.isFinite(length) && length > MAX_REGISTRY_BYTES) {
    throw new Error('npm-registryantwoord is onredelijk groot');
  }
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length > MAX_REGISTRY_BYTES) throw new Error('npm-registryantwoord overschrijdt de limiet');
  return bytes.toString('utf8');
}

async function registryMetadata(packageName, requestedVersion = 'latest', timeoutMs = 25_000) {
  const name = normalizePackageName(packageName);
  const wanted = clean(requestedVersion || 'latest', 120);
  const registryUrl = `${REGISTRY_ORIGIN}/${encodeURIComponent(name)}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(2_000, Number(timeoutMs) || 25_000));
  let response;
  let body;
  try {
    response = await fetch(registryUrl, {
      headers: { accept: 'application/vnd.npm.install-v1+json' },
      redirect: 'error',
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`npm-registry gaf HTTP ${response.status} voor ${name}`);
    body = await responseTextLimited(response);
  } finally {
    clearTimeout(timer);
  }
  const packument = json(body, null);
  if (!packument || typeof packument !== 'object') throw new Error('npm-registry gaf geen geldige metadata');
  const resolved = packument.versions?.[wanted]
    ? wanted
    : packument['dist-tags']?.[wanted];
  const selected = resolved ? packument.versions?.[resolved] : null;
  if (!selected || selected.name !== name || selected.version !== resolved) {
    throw new Error(`npm-versie ${wanted} bestaat niet voor ${name}`);
  }
  if (!selected.dist?.integrity && !selected.dist?.shasum) {
    throw new Error('npm-versie heeft geen registry-integriteit of shasum en wordt niet gestaged');
  }
  assertRegistryDependencySpecs(selected);
  const metadata = compactMetadata(packument, selected, wanted);
  const provenance = compactProvenance(registryUrl, selected);
  return { name, version: resolved, registryUrl, metadata, provenance };
}

function upsertDiscovery(record) {
  const slug = packageSlug(record.name, record.version);
  const targetPath = safeTarget(slug);
  const metadataJson = JSON.stringify(record.metadata);
  const provenanceJson = JSON.stringify(record.provenance);
  const ts = nowIso();
  const existing = byIdentifier({ packageName: record.name, version: record.version });
  if (existing) {
    db.prepare(`UPDATE eva_tool_installations SET
      registry_url=?, metadata_json=?, provenance_json=?, metadata_sha256=?,
      updated_ts=?, revision=revision+1 WHERE id=?`)
      .run(record.registryUrl, metadataJson, provenanceJson, digest(record.metadata), ts, existing.id);
    return byIdentifier({ id: existing.id });
  }
  const info = db.prepare(`INSERT INTO eva_tool_installations
    (package_name,package_version,slug,status,registry_url,target_path,metadata_json,
     provenance_json,metadata_sha256,created_ts,updated_ts)
    VALUES (?,?,?,'discovered',?,?,?,?,?,?,?)`)
    .run(record.name, record.version, slug, record.registryUrl, targetPath,
      metadataJson, provenanceJson, digest(record.metadata), ts, ts);
  recordTransition(info.lastInsertRowid, null, 'discovered', 'metadata rechtstreeks uit npm-registry opgehaald');
  return byIdentifier({ id: info.lastInsertRowid });
}

async function removeTemporary(target) {
  const resolved = path.resolve(target);
  const root = checkedExtensionsRoot();
  if (!resolved.startsWith(`${root}${path.sep}.install-`)) return;
  const stat = await fs.promises.lstat(resolved).catch(() => null);
  if (stat?.isSymbolicLink()) {
    await fs.promises.unlink(resolved);
    return;
  }
  await fs.promises.rm(resolved, { recursive: true, force: true });
}

function runSandboxedNpm(target, packageName, version) {
  const npmPath = '/usr/bin/npm';
  const envPath = '/usr/bin/env';
  const unit = `eva-npm-stage-${process.pid}-${Date.now()}-${randomBytes(3).toString('hex')}`;
  const home = path.join(target, '.home');
  const cache = path.join(target, '.npm-cache');
  const temp = path.join(target, '.tmp');
  const spec = `${packageName}@${version}`;
  // systemd-run zelf moet de user manager kunnen bereiken. Deze twee waarden
  // zijn alleen transportmetadata voor de launcher en worden door `env -i`
  // bewust niet aan npm of packagecode doorgegeven.
  const userRuntimeDir = `/run/user/${process.getuid()}`;
  const userBusAddress = `unix:path=${userRuntimeDir}/bus`;
  const runnerArgs = [
    '--user', '--quiet', '--wait', '--pipe', '--collect', `--unit=${unit}`,
    '--property=NoNewPrivileges=yes',
    '--property=ProtectSystem=strict',
    '--property=ProtectHome=tmpfs',
    // npm krijgt TMPDIR binnen het doel. /tmp zelf is een read-only leeg tmpfs,
    // zodat de enige duurzame schrijfbare mount de stagingmap blijft.
    '--property=TemporaryFileSystem=/tmp:ro',
    '--property=PrivateDevices=yes',
    '--property=PrivateMounts=yes',
    '--property=PrivateNetwork=no',
    '--property=ProtectProc=invisible',
    '--property=ProcSubset=pid',
    '--property=ProtectControlGroups=yes',
    '--property=ProtectKernelModules=yes',
    '--property=ProtectKernelTunables=yes',
    '--property=ProtectKernelLogs=yes',
    '--property=ProtectClock=yes',
    '--property=ProtectHostname=yes',
    '--property=RestrictSUIDSGID=yes',
    '--property=RestrictRealtime=yes',
    '--property=RestrictNamespaces=yes',
    '--property=LockPersonality=yes',
    '--property=DevicePolicy=closed',
    '--property=CapabilityBoundingSet=',
    '--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6',
    '--property=MemoryMax=1G',
    '--property=TasksMax=96',
    '--property=CPUQuota=200%',
    '--property=RuntimeMaxSec=300',
    '--property=LimitNOFILE=256',
    '--property=KillMode=control-group',
    '--property=UMask=0077',
    `--property=BindPaths=${target}`,
    `--property=WorkingDirectory=${target}`,
    envPath, '-i',
    `HOME=${home}`,
    `TMPDIR=${temp}`,
    'PATH=/usr/local/bin:/usr/bin:/bin',
    'LANG=C.UTF-8',
    'LC_ALL=C.UTF-8',
    `npm_config_cache=${cache}`,
    'npm_config_registry=https://registry.npmjs.org/',
    'npm_config_ignore_scripts=true',
    'npm_config_audit=false',
    'npm_config_fund=false',
    npmPath, 'install', '--save-exact', '--package-lock=true', '--ignore-scripts',
    '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org/', spec,
  ];
  return new Promise((resolve, reject) => {
    const child = spawn('/usr/bin/systemd-run', runnerArgs, {
      cwd: config.root,
      shell: false,
      env: {
        PATH: '/usr/local/bin:/usr/bin:/bin',
        LANG: 'C.UTF-8',
        LC_ALL: 'C.UTF-8',
        XDG_RUNTIME_DIR: userRuntimeDir,
        DBUS_SESSION_BUS_ADDRESS: userBusAddress,
      },
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error('npm-staging overschreed de maximale uitvoertijd'));
    }, INSTALL_TIMEOUT_MS);
    child.stdout.on('data', (chunk) => { stdout = `${stdout}${chunk}`.slice(-20_000); });
    child.stderr.on('data', (chunk) => { stderr = `${stderr}${chunk}`.slice(-20_000); });
    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`afgeschermde npm-installatie stopte met code ${code}: ${clean(stderr || stdout, 1_500)}`));
        return;
      }
      resolve({ exit_code: code, stdout: clean(stdout, 4_000), stderr: clean(stderr, 4_000) });
    });
  });
}

async function hashTree(root) {
  const hasher = createHash('sha256');
  let files = 0;
  let bytes = 0;
  const ignored = new Set(['.npm-cache', '.tmp', '.home']);
  async function visit(directory, relative = '') {
    const entries = (await fs.promises.readdir(directory, { withFileTypes: true }))
      .filter((entry) => !(!relative && ignored.has(entry.name)))
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      const rel = relative ? `${relative}/${entry.name}` : entry.name;
      const absolute = path.join(directory, entry.name);
      const stat = await fs.promises.lstat(absolute);
      if (entry.isDirectory()) {
        hasher.update(`D\0${rel}\0${stat.mode & 0o777}\0`);
        await visit(absolute, rel);
      } else if (entry.isSymbolicLink()) {
        hasher.update(`L\0${rel}\0${await fs.promises.readlink(absolute)}\0`);
      } else if (entry.isFile()) {
        files += 1;
        bytes += stat.size;
        if (files > MAX_HASH_FILES || bytes > MAX_HASH_BYTES) {
          throw new Error('geïnstalleerde packageboom overschrijdt de veilige inventarislimiet');
        }
        hasher.update(`F\0${rel}\0${stat.mode & 0o777}\0${stat.size}\0`);
        await new Promise((resolve, reject) => {
          const stream = fs.createReadStream(absolute);
          stream.on('data', (chunk) => hasher.update(chunk));
          stream.on('error', reject);
          stream.on('end', () => resolve());
        });
        hasher.update('\0');
      } else {
        throw new Error(`onverwacht bestandstype in packageboom: ${rel}`);
      }
    }
  }
  await visit(root);
  return { sha256: hasher.digest('hex'), files, bytes };
}

async function verifyStagedTree(target, packageName, version, provenance) {
  const manifestPath = path.join(target, 'package.json');
  const lockPath = path.join(target, 'package-lock.json');
  const installedManifestPath = path.join(target, 'node_modules', ...packageName.split('/'), 'package.json');
  for (const file of [manifestPath, lockPath, installedManifestPath]) {
    const real = await fs.promises.realpath(file).catch(() => null);
    if (!real || !(real === target || real.startsWith(`${target}${path.sep}`))) {
      throw new Error(`vereist stagingbestand ontbreekt of wijst buiten het doel: ${path.basename(file)}`);
    }
  }
  const manifestBytes = await fs.promises.readFile(manifestPath);
  const lockBytes = await fs.promises.readFile(lockPath);
  const installed = json(await fs.promises.readFile(installedManifestPath, 'utf8'), null);
  const lock = json(lockBytes.toString('utf8'), null);
  if (!installed || installed.name !== packageName || installed.version !== version) {
    throw new Error('geïnstalleerd npm-manifest komt niet overeen met package en exacte versie');
  }
  const lockEntry = lock?.packages?.[`node_modules/${packageName}`];
  if (!lock || lock.lockfileVersion < 2 || lockEntry?.version !== version) {
    throw new Error('package-lock bindt de gevraagde exacte packageversie niet');
  }
  if (lock.packages?.['']?.dependencies?.[packageName] !== version) {
    throw new Error('package-lock-root bevat niet exact de gevraagde packageversie');
  }
  if (provenance.integrity && lockEntry.integrity !== provenance.integrity) {
    throw new Error('package-lock-integriteit wijkt af van de vooraf opgehaalde registryprovenance');
  }
  for (const [key, entry] of Object.entries(lock.packages || {})) {
    if (!key || !entry || entry.link === true) continue;
    if (!entry.integrity) throw new Error(`lockentry zonder integriteit: ${key}`);
    if (entry.resolved) {
      let resolved;
      try { resolved = new URL(entry.resolved); } catch { throw new Error(`ongeldige resolved URL in lock: ${key}`); }
      if (resolved.protocol !== 'https:' || resolved.hostname !== 'registry.npmjs.org') {
        throw new Error(`niet-registry dependency in package-lock: ${key}`);
      }
    }
  }
  const tree = await hashTree(target);
  const manifestSha256 = digest(manifestBytes);
  const lockSha256 = digest(lockBytes);
  const artifactSha256 = digest({
    package_name: packageName,
    package_version: version,
    manifest_sha256: manifestSha256,
    lock_sha256: lockSha256,
    tree_sha256: tree.sha256,
  });
  return {
    manifestSha256, lockSha256, treeSha256: tree.sha256, artifactSha256,
    files: tree.files, bytes: tree.bytes,
  };
}

function installArgs(first, second) {
  if (first && typeof first === 'object') {
    return {
      packageName: first.packageName || first.name || first.package,
      version: first.version,
      timeoutMs: first.timeoutMs,
    };
  }
  return { packageName: first, version: second, timeoutMs: null };
}

/** Haal officiële npm-registrymetadata op en bewaar de exacte gevonden versie. */
async function discover(first, second = 'latest') {
  const request = first && typeof first === 'object'
    ? {
      packageName: first.packageName || first.name || first.package,
      version: first.version || first.tag || 'latest',
      timeoutMs: first.timeoutMs,
    }
    : { packageName: first, version: second || 'latest', timeoutMs: null };
  const record = await registryMetadata(request.packageName, request.version, request.timeoutMs);
  return upsertDiscovery(record);
}

/**
 * Installeer exact package@version in een netwerktoegestane maar verder strikte
 * systemd-sandbox. De package wordt daarna alleen geïnventariseerd en gestaged.
 */
async function install(first, second = null) {
  const request = installArgs(first, second);
  const packageName = normalizePackageName(request.packageName);
  const version = normalizeExactVersion(request.version);
  const record = await registryMetadata(packageName, version, request.timeoutMs);
  let item = upsertDiscovery(record);
  if (['staged', 'health_checked', 'active', 'disabled'].includes(item.status)) {
    const checked = await inspect({ id: item.id });
    if (checked.integrity_ok) return checked;
    throw new Error('bestaande staging veranderde; quarantaineer haar voordat je verdergaat');
  }
  if (item.status === 'quarantined') {
    // Een mislukte launcher vóór er ook maar één gecontroleerd artifact bestaat
    // mag na een concrete runtime-reparatie opnieuw worden gestaged. Bewaar de
    // quarantainetransitie in het eventlog; overschrijf nooit bestaand materiaal.
    if (item.artifact_sha256 || fs.existsSync(item.target_path)) {
      throw new Error('deze exacte packageversie staat met bestaand materiaal in quarantaine; automatische overschrijving is bewust niet toegestaan');
    }
    db.prepare(`UPDATE eva_tool_installations SET status='discovered',last_error=NULL,
      updated_ts=?,revision=revision+1 WHERE id=?`).run(nowIso(), item.id);
    recordTransition(item.id, 'quarantined', 'discovered', 'nieuwe stagingpoging na launcher- of netwerkherstel; geen bestaand artifact aanwezig');
    item = byIdentifier({ id: item.id });
  }

  await fs.promises.mkdir(EXTENSIONS_ROOT, { recursive: true, mode: 0o700 });
  checkedExtensionsRoot({ mustExist: true });
  const target = safeTarget(item.slug);
  if (fs.existsSync(target)) {
    throw new Error('extensiedoel bestaat al zonder geldige stagingreceipt');
  }
  const temporary = path.join(EXTENSIONS_ROOT, `.install-${item.slug}-${randomBytes(4).toString('hex')}`);
  await fs.promises.mkdir(temporary, { recursive: false, mode: 0o700 });
  await Promise.all([
    fs.promises.mkdir(path.join(temporary, '.home'), { mode: 0o700 }),
    fs.promises.mkdir(path.join(temporary, '.npm-cache'), { mode: 0o700 }),
    fs.promises.mkdir(path.join(temporary, '.tmp'), { mode: 0o700 }),
  ]);
  await fs.promises.writeFile(path.join(temporary, 'package.json'), `${JSON.stringify({
    name: `eva-extension-${item.slug}`,
    version: '0.0.0',
    private: true,
    description: `Immutable staging voor ${packageName}@${version}`,
  }, null, 2)}\n`, { encoding: 'utf8', mode: 0o600, flag: 'wx' });

  try {
    const execution = await runSandboxedNpm(temporary, packageName, version);
    await Promise.all(['.home', '.npm-cache', '.tmp'].map((name) => (
      fs.promises.rm(path.join(temporary, name), { recursive: true, force: true })
    )));
    const hashes = await verifyStagedTree(temporary, packageName, version, record.provenance);
    await fs.promises.rename(temporary, target);
    const before = item.status;
    const ts = nowIso();
    db.prepare(`UPDATE eva_tool_installations SET
      status='staged', target_path=?, manifest_sha256=?, lock_sha256=?, tree_sha256=?,
      artifact_sha256=?, installed_ts=?, updated_ts=?, last_error=NULL, revision=revision+1
      WHERE id=?`).run(
      target, hashes.manifestSha256, hashes.lockSha256, hashes.treeSha256,
      hashes.artifactSha256, ts, ts, item.id,
    );
    recordTransition(item.id, before, 'staged', JSON.stringify({
      package: `${packageName}@${version}`,
      artifact_sha256: hashes.artifactSha256,
      files: hashes.files,
      bytes: hashes.bytes,
      npm_exit_code: execution.exit_code,
      scripts: 'ignored',
    }));
    return inspect({ id: item.id });
  } catch (error) {
    await removeTemporary(temporary);
    item = byIdentifier({ id: item.id });
    const message = clean(error?.message || error, 1_500);
    db.prepare(`UPDATE eva_tool_installations SET
      status='quarantined', last_error=?, updated_ts=?, revision=revision+1 WHERE id=?`)
      .run(message, nowIso(), item.id);
    recordTransition(item.id, item.status, 'quarantined', message);
    throw error;
  }
}

/** Diepe, niet-uitvoerende integriteitsinspectie van registry en stagingboom. */
async function inspect(identifier, version = null) {
  const item = byIdentifier(identifier, version);
  if (!item) return null;
  const events = db.prepare(`SELECT ts,from_status,to_status,detail
    FROM eva_tool_install_events WHERE installation_id=? ORDER BY id ASC`).all(item.id);
  if (!item.artifact_sha256 || !fs.existsSync(item.target_path)) {
    return { ...item, integrity_ok: item.status === 'discovered', events };
  }
  try {
    const hashes = await verifyStagedTree(
      item.target_path, item.package_name, item.package_version, item.provenance,
    );
    return {
      ...item,
      integrity_ok: hashes.artifactSha256 === item.artifact_sha256,
      current_artifact_sha256: hashes.artifactSha256,
      files: hashes.files,
      bytes: hashes.bytes,
      events,
    };
  } catch (error) {
    return { ...item, integrity_ok: false, integrity_error: clean(error?.message || error), events };
  }
}

/** Lijst installatierecords zonder packagecode te laden of uit te voeren. */
function list({ status: wantedStatus = null, packageName = null, limit = 100 } = {}) {
  const clauses = [];
  const args = [];
  if (wantedStatus != null) {
    const wanted = clean(wantedStatus, 40);
    if (!STATUSES.has(wanted)) throw new Error(`ongeldige installatiestatus: ${wanted}`);
    clauses.push('status=?');
    args.push(wanted);
  }
  if (packageName != null) {
    clauses.push('package_name=?');
    args.push(normalizePackageName(packageName));
  }
  return db.prepare(`SELECT * FROM eva_tool_installations
    ${clauses.length ? `WHERE ${clauses.join(' AND ')}` : ''}
    ORDER BY updated_ts DESC LIMIT ?`)
    .all(...args, Math.max(1, Math.min(500, Number(limit) || 100))).map(out);
}

/**
 * Lees status, of zet een bestaande staging expliciet disabled/quarantined.
 * Alleen activate() mag health_checked/active schrijven.
 */
function status(identifier, nextStatus = null, reason = '') {
  let requested = nextStatus;
  let detail = reason;
  if (identifier && typeof identifier === 'object' && nextStatus == null) {
    requested = identifier.nextStatus || identifier.status || null;
    detail = identifier.reason || '';
  }
  const item = byIdentifier(identifier);
  if (!item) return null;
  if (!requested) return item;
  const wanted = clean(requested, 40);
  if (!MUTABLE_STATUSES.has(wanted)) {
    throw new Error('status mag handmatig alleen disabled of quarantined zetten; activatie vereist activate()');
  }
  if (item.status === wanted) return item;
  db.prepare(`UPDATE eva_tool_installations SET status=?,last_error=?,updated_ts=?,
    revision=revision+1 WHERE id=?`)
    .run(wanted, clean(detail, 1_500) || item.last_error, nowIso(), item.id);
  recordTransition(item.id, item.status, wanted, detail);
  return byIdentifier({ id: item.id });
}

/**
 * Activeer zonder packagecode uit te voeren. De caller moet de healthcheck in
 * een aparte, passende sandbox hebben uitgevoerd en de exacte artifact-checksum
 * in de receipt teruggeven.
 */
async function activate(identifier, suppliedReceipt = null) {
  const receipt = suppliedReceipt || (identifier && typeof identifier === 'object'
    ? identifier.healthReceipt || identifier.receipt
    : null);
  const item = byIdentifier(identifier);
  if (!item) throw new Error('onbekende npm-toolstaging');
  if (!['staged', 'health_checked', 'disabled'].includes(item.status)) {
    throw new Error(`installatie in status ${item.status} kan niet worden geactiveerd`);
  }
  if (!receipt || receipt.ok !== true || receipt.verified !== true) {
    throw new Error('activatie vereist een succesvolle, geverifieerde health-receipt van de caller');
  }
  const receiptExit = Number(receipt.exit_code ?? receipt.result?.exit_code);
  if (!Number.isInteger(receiptExit) || receiptExit !== 0) {
    throw new Error('health-receipt moet een geslaagde uitvoering met exitcode 0 bevatten');
  }
  const checked = await inspect({ id: item.id });
  if (!checked?.integrity_ok) throw new Error('stagingintegriteit is veranderd; activatie geweigerd');
  const receiptChecksum = clean(
    receipt.artifact_sha256 || receipt.artifactChecksum || receipt.checksum,
    100,
  ).toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(receiptChecksum) || receiptChecksum !== item.artifact_sha256) {
    throw new Error('health-receipt hoort niet bij exact deze artifact-checksum');
  }
  const receiptJson = JSON.stringify({
    ok: true,
    verified: true,
    exit_code: 0,
    artifact_sha256: receiptChecksum,
    checked_at: clean(receipt.checked_at || receipt.checkedAt || nowIso(), 80),
    check: clean(receipt.check || receipt.summary || receipt.evidence, 1_000) || null,
  });
  const transaction = db.transaction(() => {
    const fresh = byIdentifier({ id: item.id });
    db.prepare(`UPDATE eva_tool_installations SET status='health_checked',
      health_receipt_json=?,updated_ts=?,revision=revision+1 WHERE id=?`)
      .run(receiptJson, nowIso(), item.id);
    recordTransition(item.id, fresh.status, 'health_checked', 'caller-healthreceipt gecontroleerd tegen artifact-checksum');
    db.prepare(`UPDATE eva_tool_installations SET status='active',activated_ts=?,
      updated_ts=?,last_error=NULL,revision=revision+1 WHERE id=?`)
      .run(nowIso(), nowIso(), item.id);
    recordTransition(item.id, 'health_checked', 'active', 'geen automatische package-uitvoering; alleen registry-activatie');
  });
  transaction();
  return inspect({ id: item.id });
}

// Beschrijvende aliassen voor integratiecode die liever expliciete namen gebruikt.
export const inspectToolPackage = inspect;
export const discoverToolPackage = discover;
export const installToolPackage = install;
export const listToolPackages = list;
export const toolPackageStatus = status;
export const activateToolPackage = activate;
