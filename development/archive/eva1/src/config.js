import 'dotenv/config';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const env = process.env;
const TEST_DATA_ROOT = '/tmp';
// Linux O_PATH is niet door alle ondersteunde Node-versies geëxporteerd. De
// guard gebruikt al Linux' /proc/self/fd; deze vlag opent een bestaand inode
// zonder device/FIFO-I/O en maakt de filecheck dus zelf ook effectvrij.
const LINUX_O_PATH = 0o10000000;

/** @typedef {Record<string, string|undefined>} EvaEnvironment */

function strictDescendant(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative);
}

function testDataDirError(message) {
  return new Error(`Onveilige testdatabase: EVA_DATA_DIR ${message}`);
}

function dataDirError() {
  return new Error('Onveilige databasetoegang: EVA_DATA_DIR moet expliciet en niet leeg zijn');
}

/**
 * Detecteer uitsluitend echte Node-testroutes: de interne runner-markering,
 * Node's eigen --test-vlag, een mainscript in de project-testboom, of exact
 * scripts/*.test.{js,mjs,cjs}. Ook die laatste glob kan rechtstreeks starten.
 * Een willekeurig later argv-item of NODE_ENV=test telt uitdrukkelijk niet.
 */
export function nodeTestContextPresent({
  projectRoot = root, environment = {}, execArgv = [], argv = [],
} = /** @type {{projectRoot?:string, environment?:EvaEnvironment, execArgv?:string[], argv?:string[]}} */ ({})) {
  if (Object.hasOwn(environment, 'NODE_TEST_CONTEXT')) return true;
  if (execArgv.some((argument) => argument === '--test' || argument.startsWith('--test='))) {
    return true;
  }
  const mainScript = typeof argv[1] === 'string' && argv[1] ? path.resolve(argv[1]) : '';
  const testRoot = path.resolve(projectRoot, 'test');
  const scriptsRoot = path.resolve(projectRoot, 'scripts');
  const inProjectTestTree = mainScript && strictDescendant(testRoot, mainScript);
  const directProjectScript = mainScript && path.dirname(mainScript) === scriptsRoot;
  return Boolean((inProjectTestTree || directProjectScript)
    && /\.test\.(?:cjs|js|mjs)$/u.test(path.basename(mainScript)));
}

/**
 * Resolveer het datapad zonder filesystemeffecten. Elk proces moet zijn
 * data-authority expliciet krijgen. Productie behoudt relatieve-padsemantiek;
 * echte Node-tests krijgen aanvullend altijd de strikte /tmp-grens.
 */
export function resolveEvaDataDir({
  projectRoot = root, environment = process.env, cwd = process.cwd(), execArgv = [], argv = [],
} = /** @type {{projectRoot?:string, environment?:EvaEnvironment, cwd?:string, execArgv?:string[], argv?:string[]}} */ ({})) {
  const configured = Object.hasOwn(environment, 'EVA_DATA_DIR')
    ? environment.EVA_DATA_DIR : undefined;
  const explicit = configured ? String(configured) : '';
  const testing = nodeTestContextPresent({ projectRoot, environment, execArgv, argv });
  if (!explicit) {
    if (testing) throw testDataDirError('moet een expliciet absolute /tmp-afstammeling zijn');
    throw dataDirError();
  }
  if (!testing) return path.resolve(cwd, explicit);
  if (!explicit || !path.isAbsolute(explicit)) {
    throw testDataDirError('moet een expliciet absolute /tmp-afstammeling zijn');
  }
  const resolved = path.resolve(explicit);
  if (!strictDescendant(TEST_DATA_ROOT, resolved)) {
    throw testDataDirError('moet een expliciet absolute /tmp-afstammeling zijn');
  }
  return resolved;
}

function privateDirectoryStat(stat) {
  const mode = Number(stat.mode) & 0o777;
  const owned = typeof process.getuid !== 'function' || Number(stat.uid) === process.getuid();
  return stat.isDirectory() && owned && (mode & 0o077) === 0 && (mode & 0o700) === 0o700;
}

function directoryOpenFlags() {
  const { O_DIRECTORY, O_NOFOLLOW, O_RDONLY } = fs.constants;
  if (!Number.isInteger(O_DIRECTORY) || !Number.isInteger(O_NOFOLLOW)) {
    throw testDataDirError('kan op dit platform geen no-follow directoryhandle openen');
  }
  return O_RDONLY | O_DIRECTORY | O_NOFOLLOW;
}

function validatePrivateTestDirectory(candidate) {
  let linkStat;
  try {
    linkStat = fs.lstatSync(candidate);
  } catch (error) {
    if (error?.code === 'ENOENT' || error?.code === 'ENOTDIR') {
      throw testDataDirError('moet vooraf bestaan als private directory');
    }
    throw error;
  }
  if (linkStat.isSymbolicLink() || !privateDirectoryStat(linkStat)) {
    throw testDataDirError('moet een echte, eigen private directory (0700) zijn');
  }
  const realTestRoot = fs.realpathSync.native(TEST_DATA_ROOT);
  const realCandidate = fs.realpathSync.native(candidate);
  if (!strictDescendant(realTestRoot, realCandidate)) {
    throw testDataDirError('ligt werkelijk niet onder /tmp');
  }
  return { linkStat, realCandidate };
}

function openPinnedDirectory(candidate, { requirePrivate = true } = {}) {
  const validated = requirePrivate ? validatePrivateTestDirectory(candidate) : null;
  let descriptor;
  try {
    descriptor = fs.openSync(candidate, directoryOpenFlags());
    const descriptorStat = fs.fstatSync(descriptor);
    if (!descriptorStat.isDirectory()
      || (requirePrivate && !privateDirectoryStat(descriptorStat))) {
      throw testDataDirError('directoryhandle verwijst niet naar de goedgekeurde private map');
    }
    if (validated && (Number(descriptorStat.dev) !== Number(validated.linkStat.dev)
      || Number(descriptorStat.ino) !== Number(validated.linkStat.ino))) {
      throw testDataDirError('directory is tijdens het openen verwisseld');
    }
    const accessRoot = `/proc/self/fd/${descriptor}`;
    const descriptorRealPath = fs.realpathSync.native(accessRoot);
    if (validated && descriptorRealPath !== validated.realCandidate) {
      throw testDataDirError('directoryhandle wijkt af van het goedgekeurde werkelijke pad');
    }
    return Object.freeze({
      accessRoot,
      descriptor,
      dev: Number(descriptorStat.dev),
      ino: Number(descriptorStat.ino),
    });
  } catch (error) {
    if (descriptor != null) fs.closeSync(descriptor);
    throw error;
  }
}

function verifyPinnedDirectory(authority, label) {
  if (!authority) throw testDataDirError(`mist een gepinde ${label}directory`);
  let current;
  try {
    current = fs.fstatSync(authority.descriptor);
  } catch {
    throw testDataDirError(`verloor de gepinde ${label}directory`);
  }
  if (!current.isDirectory() || Number(current.dev) !== authority.dev
    || Number(current.ino) !== authority.ino || !privateDirectoryStat(current)) {
    throw testDataDirError(`gepinde ${label}directory is ongeldig of niet meer private`);
  }
  return authority.accessRoot;
}

function openPinnedWorkspace(dataAuthority) {
  const workspaceEntry = path.join(dataAuthority.accessRoot, 'workspace');
  try {
    fs.mkdirSync(workspaceEntry, { mode: 0o700 });
  } catch (error) {
    if (error?.code !== 'EEXIST') throw error;
  }
  const authority = openPinnedDirectory(workspaceEntry, { requirePrivate: false });
  fs.fchmodSync(authority.descriptor, 0o700);
  return Object.freeze({ ...authority });
}

/**
 * Een losse validator voor regressies en callers die nog vóór config-load een
 * kandidaat willen toetsen. De echte runtime-authority blijft de open fd.
 */
/** @param {{dataDir:string, projectRoot?:string, environment?:EvaEnvironment, execArgv?:string[], argv?:string[]}} options */
export function assertTestDataDirRealPath({
  dataDir, projectRoot = root, environment = process.env, execArgv = [], argv = [],
} = /** @type {any} */ ({})) {
  if (!nodeTestContextPresent({ projectRoot, environment, execArgv, argv })) {
    return path.resolve(String(dataDir));
  }
  const expected = resolveEvaDataDir({ projectRoot, environment, execArgv, argv });
  const resolved = path.resolve(String(dataDir || ''));
  if (resolved !== expected) throw testDataDirError('wijkt af van het expliciete testpad');
  const authority = openPinnedDirectory(resolved);
  fs.closeSync(authority.descriptor);
  return resolved;
}

const runtimeTestContext = Object.freeze({
  projectRoot: root,
  environment: env,
  execArgv: process.execArgv,
  argv: process.argv,
});
const isNodeTestProcess = nodeTestContextPresent(runtimeTestContext);
const logicalDataDir = resolveEvaDataDir({
  ...runtimeTestContext,
  cwd: process.cwd(),
});
const testDataAuthority = isNodeTestProcess ? openPinnedDirectory(logicalDataDir) : null;
const testWorkspaceAuthority = testDataAuthority ? openPinnedWorkspace(testDataAuthority) : null;
const dataDir = testDataAuthority?.accessRoot || logicalDataDir;
const workspaceDir = testWorkspaceAuthority?.accessRoot || path.join(dataDir, 'workspace');

/** Herbevestig de retained-fd authority vóór db.js enig filesystemeffect doet. */
export function requireConfiguredDataRoot() {
  const currentlyTesting = nodeTestContextPresent({
    projectRoot: root, environment: process.env,
    execArgv: process.execArgv, argv: process.argv,
  });
  // Een proces dat als test begon verliest zijn authority niet doordat testcode
  // later een env-markering verwijdert. Omgekeerd kan productie zich na config-
  // load niet alsnog als test voordoen zonder de retained handles te bezitten.
  if (!isNodeTestProcess && !currentlyTesting) return dataDir;
  if (!isNodeTestProcess || dataDir !== testDataAuthority?.accessRoot
    || workspaceDir !== testWorkspaceAuthority?.accessRoot) {
    throw testDataDirError('mist retained-fd authority voor dit testproces');
  }
  verifyPinnedDirectory(testDataAuthority, 'data');
  verifyPinnedDirectory(testWorkspaceAuthority, 'workspace');
  return dataDir;
}

function testDatabaseFileError(message, cause = null) {
  return new Error(`Onveilige testdatabase: EVA_DATA_DIR databasebestand ${message}`,
    cause ? { cause } : undefined);
}

function existingPinnedFile(target) {
  try {
    fs.lstatSync(target);
  } catch (error) {
    if (error?.code === 'ENOENT') return null;
    throw error;
  }
  try {
    return fs.openSync(target, LINUX_O_PATH | fs.constants.O_NOFOLLOW);
  } catch (error) {
    throw testDatabaseFileError('kan niet no-follow worden gepind', error);
  }
}

function createdPinnedFile(target) {
  try {
    return fs.openSync(target, fs.constants.O_RDWR | fs.constants.O_CREAT
      | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  } catch (error) {
    throw testDatabaseFileError('kan niet exclusief en no-follow worden aangemaakt', error);
  }
}

/** Zuivere metadata-afwijzing, ook testbaar zonder verhoogde chown-rechten. */
export function databaseFileMetadataProblem(metadata, ownerUid = process.getuid?.()) {
  const owned = ownerUid == null || Number(metadata?.uid) === Number(ownerUid);
  if (metadata?.regular !== true || !owned || Number(metadata?.nlink) !== 1) {
    return 'moet een eigen regulier single-linkbestand zijn; symlink/no-follow geweigerd';
  }
  return null;
}

function sealedPinnedDatabaseLeaf(descriptor, label) {
  try {
    const stat = fs.fstatSync(descriptor);
    const problem = databaseFileMetadataProblem({
      regular: stat.isFile(), uid: Number(stat.uid), nlink: Number(stat.nlink),
    });
    if (problem) throw testDatabaseFileError(`${label} ${problem}`);
    const accessPath = `/proc/self/fd/${descriptor}`;
    fs.chmodSync(accessPath, 0o600);
    const sealed = fs.fstatSync(descriptor);
    const sealedProblem = databaseFileMetadataProblem({
      regular: sealed.isFile(), uid: Number(sealed.uid), nlink: Number(sealed.nlink),
    });
    if (sealedProblem || Number(sealed.dev) !== Number(stat.dev)
      || Number(sealed.ino) !== Number(stat.ino) || (Number(sealed.mode) & 0o777) !== 0o600) {
      throw testDatabaseFileError(`${label} veranderde tijdens de file-attestatie`);
    }
    return Object.freeze({
      accessPath, descriptor, label,
      dev: Number(sealed.dev), ino: Number(sealed.ino),
    });
  } catch (error) {
    fs.closeSync(descriptor);
    throw error;
  }
}

function validatedPinnedExistingDatabaseLeaf(target, label) {
  const descriptor = existingPinnedFile(target);
  return descriptor == null ? null : sealedPinnedDatabaseLeaf(descriptor, label);
}

function validatedPinnedDatabaseFile(target) {
  const preexistingSidecars = [];
  let main = null;
  try {
    for (const suffix of ['-wal', '-shm']) {
      const leaf = validatedPinnedExistingDatabaseLeaf(`${target}${suffix}`, `eva.db${suffix}`);
      if (leaf) preexistingSidecars.push(leaf);
    }
    const descriptor = existingPinnedFile(target) ?? createdPinnedFile(target);
    main = sealedPinnedDatabaseLeaf(descriptor, 'eva.db');
    return Object.freeze({
      ...main,
      preexistingSidecars: Object.freeze(preexistingSidecars),
    });
  } catch (error) {
    for (const leaf of preexistingSidecars) fs.closeSync(leaf.descriptor);
    if (main) fs.closeSync(main.descriptor);
    throw error;
  }
}

function descriptorOpenedBySqlite(authority, identity) {
  const guardDescriptors = new Set([
    authority.descriptor,
    ...(authority.preexistingSidecars || []).map((leaf) => leaf.descriptor),
  ]);
  for (const entry of fs.readdirSync('/proc/self/fd')) {
    if (!/^[0-9]+$/u.test(entry)) continue;
    const descriptor = Number(entry);
    if (guardDescriptors.has(descriptor)) continue;
    try {
      const stat = fs.fstatSync(descriptor);
      if (!stat.isFile() || Number(stat.dev) !== identity.dev || Number(stat.ino) !== identity.ino) {
        continue;
      }
      const info = fs.readFileSync(`/proc/self/fdinfo/${descriptor}`, 'utf8');
      const flags = Number.parseInt(info.match(/^flags:\s*([0-7]+)/mu)?.[1] || '', 8);
      if (!Number.isFinite(flags) || (flags & LINUX_O_PATH) !== 0) continue;
      return descriptor;
    } catch { /* descriptor verdween tijdens de uitsluitend lokale inventarisatie */ }
  }
  throw testDatabaseFileError('mist een SQLite-descriptor voor de geattesteerde inode');
}

function attestCurrentDatabaseLeaf(authority, leafName) {
  const target = path.join(dataDir, leafName);
  let linkStat;
  try { linkStat = fs.lstatSync(target); } catch (error) {
    throw testDatabaseFileError(`${leafName} ontbreekt na SQLite-open`, error);
  }
  if (linkStat.isSymbolicLink()) {
    throw testDatabaseFileError(`${leafName} is na SQLite-open een symlink`);
  }
  const current = fs.statSync(target);
  const problem = databaseFileMetadataProblem({
    regular: current.isFile(), uid: Number(current.uid), nlink: Number(current.nlink),
  });
  if (problem || (Number(current.mode) & 0o777) !== 0o600) {
    throw testDatabaseFileError(`${leafName} ${problem || 'is niet mode 0600'}`);
  }
  const pinnedRoot = fs.realpathSync.native(dataDir);
  const real = fs.realpathSync.native(target);
  if (path.dirname(real) !== pinnedRoot) {
    throw testDatabaseFileError(`${leafName} ligt buiten de gepinde datadirectory`);
  }
  const identity = { dev: Number(current.dev), ino: Number(current.ino) };
  const sqliteDescriptor = descriptorOpenedBySqlite(authority, identity);
  return Object.freeze({ leafName, real, ...identity, sqliteDescriptor });
}

/** Vergelijk de echte SQLite-mainfd vóór pragmas of schema-DDL met de retained anchor. */
export function attestConfiguredDatabaseOpen(database, authority) {
  if (!isNodeTestProcess) return null;
  requireConfiguredDataRoot();
  if (!authority || authority.descriptor == null) {
    throw testDatabaseFileError('mist de retained main-file-anchor');
  }
  const rows = database.prepare('PRAGMA main.database_list').all();
  const main = rows.find((row) => String(row?.name || '') === 'main');
  const reported = String(main?.file || '');
  if (!reported) throw testDatabaseFileError('SQLite rapporteert geen mainbestand');
  const current = attestCurrentDatabaseLeaf(authority, 'eva.db');
  if (current.dev !== authority.dev || current.ino !== authority.ino
    || fs.realpathSync.native(reported) !== current.real) {
    throw testDatabaseFileError('SQLite-main wijkt af van de retained file-anchor');
  }
  return current;
}

/** Attesteer de door een lege WAL-transactie geopende sidecars vóór applicatie-DDL. */
export function attestConfiguredDatabaseSidecars(authority) {
  if (!isNodeTestProcess) return Object.freeze([]);
  requireConfiguredDataRoot();
  return Object.freeze(['eva.db-wal', 'eva.db-shm'].map((leafName) => {
    const current = attestCurrentDatabaseLeaf(authority, leafName);
    const preexisting = (authority.preexistingSidecars || [])
      .find((leaf) => leaf.label === leafName);
    if (preexisting && (current.dev !== preexisting.dev || current.ino !== preexisting.ino)) {
      throw testDatabaseFileError(`${leafName} wijkt af van de retained pre-open anchor`);
    }
    return current;
  }));
}

/**
 * Geef db.js in tests een retained vergelijkingsanker; SQLite wordt direct na
 * xOpen tegen die inode geattesteerd. Productie krijgt het gewone expliciete
 * pad. Alleen het vaste databasekind is toegestaan.
 */
export function configuredDatabaseFile(name = 'eva.db') {
  if (name !== 'eva.db') throw testDatabaseFileError('heeft een onbekende naam');
  requireConfiguredDataRoot();
  const target = path.join(dataDir, name);
  return isNodeTestProcess
    ? validatedPinnedDatabaseFile(target)
    : Object.freeze({ accessPath: target, descriptor: null });
}

// Recovery is de fail-closed procesmodus. Alleen de byte-exacte waarde
// `normal` opent connectoren en andere externe effecten. Daardoor kan een
// ontbrekende, verkeerd gespelde of uit een oude omgeving overgebleven waarde
// nooit stilzwijgend een volledige runtime starten.
const runtimeMode = env.EVA_RUNTIME_MODE === 'normal' ? 'normal' : 'recovery';
const recoveryMode = runtimeMode === 'recovery';
const externalEffectsEnabled = runtimeMode === 'normal';
const autonomyLevel = String(env.EVA_AUTONOMY_LEVEL || '').trim().toLowerCase();
const autonomyEnabled = externalEffectsEnabled && env.EVA_AUTONOMY_ENABLED === 'true';
const autonomyFull = autonomyEnabled && autonomyLevel === 'full';

function numberSetting(name, fallback) {
  const raw = String(env[name] ?? '').trim();
  const value = raw ? Number(raw) : fallback;
  return Number.isFinite(value) ? value : fallback;
}

const ASSIST_SATELLITE_ENTITY = /^assist_satellite\.[a-z0-9_]+$/;

/**
 * Parse `alias=assist_satellite.entity_id` entries from a comma-separated env
 * value. Plain entity ids are accepted too. Invalid/non-satellite targets are
 * ignored so a typo can never widen the speaker allowlist.
 */
export function parseVoiceAssistSatelliteTargets(value) {
  const targets = [];
  const seen = new Set();
  for (const item of String(value || '').split(',')) {
    const entry = item.trim();
    if (!entry) continue;
    const separator = entry.indexOf('=');
    const alias = separator >= 0 ? entry.slice(0, separator).trim() : '';
    const entityId = (separator >= 0 ? entry.slice(separator + 1) : entry).trim().toLowerCase();
    if (!ASSIST_SATELLITE_ENTITY.test(entityId) || seen.has(entityId)) continue;
    seen.add(entityId);
    targets.push({
      alias: alias || entityId.slice('assist_satellite.'.length),
      entityId,
    });
  }
  return targets;
}

const voiceAssistSatelliteTargets = parseVoiceAssistSatelliteTargets(
  env.EVA_VOICE_ASSIST_SATELLITES,
);

/** @template T @param {T} value @returns {T} */
function deepFreeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

export const config = deepFreeze({
  root,
  dataDir,
  logicalDataDir,

  haUrl: (env.HA_URL || 'http://homeassistant.local:8123').replace(/\/$/, ''),
  haToken: env.HA_TOKEN || '',
  // Lokale Valetudo-interface van Erik. Dit is bewust een aparte integratie:
  // een stofzuiger is geen generieke aan/uit-schakelaar.
  robotUrl: (env.EVA_ROBOT_URL || '').replace(/\/$/, ''),
  robotEntityId: env.EVA_ROBOT_ENTITY_ID || '',
  robotMapCameraId: env.EVA_ROBOT_MAP_CAMERA_ID || '',
  robotCameraStream: env.EVA_ROBOT_CAMERA_STREAM || '',

  telegramToken: env.TELEGRAM_BOT_TOKEN || '',
  telegramAllowedIds: (env.TELEGRAM_ALLOWED_IDS || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean)
    .map(Number),

  webPort: numberSetting('WEB_PORT', 3210),
  webHost: env.WEB_HOST || '127.0.0.1',
  webToken: env.EVA_WEB_TOKEN || '',
  webSender: env.EVA_WEB_SENDER || 'Jaap',
  householdMembers: (env.EVA_HOUSEHOLD_MEMBERS || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean),
  trustHaPersonMembership: env.EVA_TRUST_HA_PERSON_MEMBERSHIP === 'true',
  oauthPort: 1455,

  model: env.EVA_MODEL || 'gpt-5.6-sol',
  modelVoice: env.EVA_MODEL_VOICE || env.EVA_MODEL || 'gpt-5.6-terra',
  modelResearch: env.EVA_MODEL_RESEARCH || env.EVA_MODEL_VOICE || env.EVA_MODEL || 'gpt-5.6-terra',
  reasoningEffort: env.EVA_REASONING || 'low',
  reasoningChat: env.EVA_REASONING_CHAT || 'none',
  reasoningVoice: env.EVA_REASONING_VOICE || 'low',
  reasoningAutonomy: env.EVA_REASONING_AUTONOMY || 'low',
  reasoningCode: env.EVA_REASONING_CODE || 'high',
  openaiApiKey: env.OPENAI_API_KEY || '',

  // Aparte Python-service voor ArcFace en de eigen encoder.
  brainUrl: (env.EVA_BRAIN_URL || 'http://127.0.0.1:3220').replace(/\/$/, ''),

  tz: env.TZ || 'Europe/Amsterdam',
  runtimeMode,
  recoveryMode,
  externalEffectsEnabled,
  autonomousLlmTimeoutMs: numberSetting('EVA_AUTONOMOUS_LLM_TIMEOUT_MS', 90_000),
  autonomyEnabled,
  autonomyLevel: autonomyEnabled ? (autonomyFull ? 'full' : 'bounded') : 'disabled',
  autonomyFull,
  autonomyOpenExploration: autonomyEnabled
    && (autonomyFull || env.EVA_AUTONOMY_OPEN_EXPLORATION === 'true'),
  autonomyCodeExecution: autonomyEnabled
    && (autonomyFull || env.EVA_AUTONOMY_CODE_EXECUTION === 'true'),
  autonomyIntervalMinutes: numberSetting('EVA_AUTONOMY_INTERVAL_MINUTES', autonomyFull ? 15 : 75),
  autonomyConversationPauseMinutes: numberSetting(
    'EVA_AUTONOMY_CONVERSATION_PAUSE_MINUTES', autonomyFull ? 2 : 20,
  ),
  // Full autonomy gets a real daily work allocation, not just short attention pings.
  autonomyDailyWorkMinutes: autonomyEnabled ? Math.max(
    120,
    Math.min(480, numberSetting('EVA_AUTONOMY_DAILY_WORK_MINUTES', 120)),
  ) : 0,
  autonomyTurnMinutes: autonomyEnabled ? Math.max(
    15,
    Math.min(60, numberSetting('EVA_AUTONOMY_TURN_MINUTES', autonomyFull ? 30 : 15)),
  ) : 0,
  autonomyActiveWorkIntervalMinutes: numberSetting(
    'EVA_ACTIVE_WORK_INTERVAL_MINUTES', autonomyFull ? 10 : 30,
  ),
  autonomyProgressWorkIntervalMinutes: numberSetting(
    'EVA_PROGRESS_WORK_INTERVAL_MINUTES', autonomyFull ? 0.25 : 5,
  ),
  autonomyTemperatureDelta: numberSetting('EVA_AUTONOMY_TEMPERATURE_DELTA', 1),
  sleepStart: env.SLEEP_START || '23:30',
  wakeTime: env.WAKE_TIME || '07:30',
  allowHomeControl: env.ALLOW_HOME_CONTROL !== 'false',
  // Alleen deze Assist-satellieten mogen door `spreek_in_huis` worden gebruikt.
  // Het default is een alias uit de lijst of exact een toegestaan entity_id.
  voiceAssistSatelliteTargets,
  voiceAssistSatelliteDefault: String(env.EVA_VOICE_ASSIST_SATELLITE_DEFAULT || '').trim(),

  workspaceDir,
  logicalWorkspaceDir: path.join(logicalDataDir, 'workspace'),

  cameraReportQuietMinutes: numberSetting('EVA_CAMERA_REPORT_QUIET_MINUTES', 90),
  cameraIncidentQuietMs: numberSetting('EVA_CAMERA_INCIDENT_QUIET_MS', 180_000),
  doorbellIncidentQuietMs: numberSetting('EVA_DOORBELL_INCIDENT_QUIET_MS', 10_000),
  triggerFrameMaxFiles: numberSetting('EVA_TRIGGER_FRAME_MAX_FILES', 600),
  triggerFrameRetentionHours: numberSetting('EVA_TRIGGER_FRAME_RETENTION_HOURS', 48),
  photoArchiveDays: numberSetting('EVA_PHOTO_ARCHIVE_DAYS', 180),
  photoArchiveMax: numberSetting('EVA_PHOTO_ARCHIVE_MAX', 2_000),

  unifiKey: env.UNIFI_API_KEY || '',
  unifiHost: env.UNIFI_HOST || '',
});

process.env.TZ = config.tz;

export default config;
