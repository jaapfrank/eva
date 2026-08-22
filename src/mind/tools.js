import fs from 'node:fs';
import path from 'node:path';
import { isUtf8 } from 'node:buffer';
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { config } from '../config.js';
import { redactSensitiveText } from '../security/redaction.js';
import {
  artifactReceiptChainMatches,
  boundedRuntimeMs,
  classifyActionFailure as classifyToolFailure,
  canonicalJson,
  dutchContentWords as words,
  isNight, jsonSchemaViolation,
  normalizeDutchText as normalize,
  normalizeActionFailure as normalizeToolFailure,
  normalizeActionResultState,
  verifiedCompleteReceipt,
} from '../util.js';
import { behaviorLearningAvailable } from './learning.js';
import {
  addMessage,
  addMemory,
  addReminder,
  allPeople,
  cancelReminder,
  canonicalTelegramText,
  claimTurnToolExecution,
  completeTurnToolExecution,
  getTimeline,
  logTimeline,
  memoriesForPerson,
  openReminders,
  prepareTelegramConversationOpportunity,
  searchMemories,
  searchTelegramConversationOpportunity,
  searchTelegramMessages,
  telegramConversationSearchSpec,
  telegramMessage,
  validateExecutionRecord,
  db,
} from '../db.js';
import {
  callService,
  getState,
  HA_INTEGRATION_SLUG_PATTERN, listIntegrationInventory,
  listPersonConfigurations,
  presenceDetails,
  restCall,
  setPersonTrackers,
  announceOnVoiceAssistSatellite,
  voiceAssistSatelliteAnnouncementPlan,
  whoIsHome,
} from '../integrations/homeassistant.js';
import { searchWeb } from '../brain/llm.js';
import { currentCryptoMarketFact, currentCryptoMarketFactText } from '../integrations/marketdata.js';
import { currentWeatherForecastFact, currentWeatherForecastFactText } from '../integrations/weatherdata.js';
import { householdMembers } from './household.js';
import { createTelegramDeliveryRegistry } from './telegramdelivery.js';
import { listWorkItems } from './work.js';
import {
  activateCapability,
  capabilityInventory,
  capabilityTestSpec,
  issueCapabilityReceipt,
  listCapabilities,
  setCapabilityStatus,
  stageAddon,
  stageSkill,
  useActiveCapability,
} from './capabilities.js';
import { controlRobot, exploreWithRobot, robotMap, robotStatus, captureRobotFrame } from '../integrations/robot.js';
import {
  agentMailStatus, listAgentMailMessages, sendAgentMail, signUpAgentMail, verifyAgentMail,
} from '../integrations/agentmail.js';
import {
  completeIssuedExplorationContribution,
  explorationEvidenceTopicSha256,
  listExplorations,
  registerExplorationEvidenceReceiptVerifier,
  startIssuedFreeAutonomyExplorationAtomic,
  startExploration,
  updateExploration,
} from './curiosity.js';
import { monitoringRequestSpec, priorActionResultFollowup } from './turnplan.js';
import {
  monitoringDeliveryTransportLimits,
  monitoringDeliveryWireCurrent,
  normalizeMonitoringContract,
} from './monitoringcontract.js';
import { boundedVerifiedHomePartialEffect } from './communication.js';
import { explorationEconomicExecutionPolicy } from './economichandoff.js';
import {
  episodeNeedsDelivery,
  episodeHasFinalDelivery,
  createDurableWork,
  episodeDeliveryOrigin,
  episodeForWork,
  goalEpisodeById,
  interpretationReplyDeliveryGate,
  manageGoal,
  recordEpisodeDelivery,
  requiredTelegramOriginBinding,
  transitionGoalEpisode,
  updateDurableWork,
  verifyMonitoringLifecycleAuthority,
} from './portfolio.js';
import {
  bindIssuedSelfAttentionGoalAction,
  consumeIssuedExplorationTerminalContribution,
  consumeIssuedSelfAttentionGoalAction,
  continuityWatchForUnderstanding,
  fileArgumentsMetadataValid,
  hasUnresolvedReference,
  isIssuedTurnUnderstanding,
  normalizeFileArgumentsMetadata,
  signalBatchForUnderstanding,
} from './understanding.js';
import {
  HOME_ENTITY_SIGNAL_SCOPES,
  SIGNAL_ASSESSMENT_CONTRACT,
  createSignalBatch,
  validSignalBatch,
} from './signals.js';
import {
  createWorkCell,
  getWorkCell,
  listWorkCells,
  resumeWorkCell,
  runWorkCell,
  WORKCELL_LIMITS,
} from './workcells.js';
import {
  activateToolPackage,
  discoverToolPackage,
  inspectToolPackage,
  installToolPackage,
  listToolPackages,
  toolPackageStatus,
} from './toolinstaller.js';
import { manageRuntime, runtimePrincipalAuthorized } from './runtimecontroller.js';
import {
  compactHomeState as compactState,
  groundContextualHomeCommand,
  homeContextText,
  homeOperationOutcome,
  operateHome,
  resolveContextualHomeCommand,
  resolveHomeEntity,
  validHomeRequesterTargetBinding,
} from './home.js';
import {
  WORKSPACE_FILE_MANIFEST_LIMITS,
  WORKSPACE_FILE_MANIFEST_IGNORED_DIRECTORIES,
  addWorkspaceManifestFile,
  createWorkspaceManifestState,
  finishWorkspaceFileManifest,
  markWorkspaceManifestAmbiguous,
  markWorkspaceManifestTruncated,
  observeWorkspaceManifestEntry,
  revalidateWorkspaceFileManifest,
  validWorkspaceFileManifest,
  workspaceManifestSearchTerms,
} from './workspacefilemanifest.js';
import {
  createWorkspaceGenerationStore,
} from './workspacegeneration.js';
export { revalidateWorkspaceFileManifest, validWorkspaceFileManifest };
const MAX_TOOL_OUTPUT = 24_000;
const AUTONOMY_TOPIC_QUIET_MS = 7 * 24 * 3600_000;
const READABLE_ROOTS = [config.root, config.workspaceDir].map((value) => path.resolve(value));
const WRITABLE_ROOT = path.resolve(config.workspaceDir);
const SYNTHETIC_ARTIFACT_ROOT = path.resolve(WRITABLE_ROOT, 'test-artifacts');
const SENSITIVE_READ_ROOTS = ['private', 'backups', 'runtime']
  .map((name) => path.resolve(config.dataDir, name));
const SAFE_PROGRAMS = new Set(['node', 'npm', 'npx', 'python', 'python3', 'git']);
const SAFE_EXECUTABLES = new Map([
  ['node', '/usr/bin/node'],
  ['npm', '/usr/bin/npm'],
  ['npx', '/usr/bin/npx'],
  ['python', '/usr/bin/python3'],
  ['python3', '/usr/bin/python3'],
  ['git', '/usr/bin/git'],
]);
const TELEGRAM_DELIVERY = createTelegramDeliveryRegistry();
let PRODUCTION_WORKSPACE_GENERATION = null;
let SYNTHETIC_WORKSPACE_GENERATION = null;
try { PRODUCTION_WORKSPACE_GENERATION = createWorkspaceGenerationStore(db); } catch { /* fail closed in write handler */ }
try {
  SYNTHETIC_WORKSPACE_GENERATION = createWorkspaceGenerationStore(db, { lane: 'synthetic' });
} catch { /* fail closed in write handler */ }
const str = (description, constraints = {}) => ({ type: 'string', description, ...constraints });
const num = (description, constraints = {}) => ({ type: 'number', description, ...constraints });
const BEHAVIOR_TRIAL_PEOPLE = householdMembers(allPeople()).map((person) => person.name).filter(Boolean).sort();
const obj = (properties, required = []) => ({
  type: 'object',
  properties,
  required,
  additionalProperties: false,
});

export const TOOL_EFFECT = Object.freeze({
  read: 'read',
  home: 'home',
  voice: 'voice',
  external: 'external',
  workspace: 'workspace',
  persistent: 'persistent',
  runtime: 'runtime',
});
const EFFECT_VALUES = new Set(Object.values(TOOL_EFFECT));
function policyOperation(args = {}) {
  return String(args?.actie ?? args?.bewerking ?? '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .trim()
    .toLowerCase();
}
const tagList = (value) => Object.freeze([...new Set(String(value || '').split(/\s+/u).filter(Boolean))]);
function fixed(effect, extras = {}) {
  const mutates = effect !== TOOL_EFFECT.read;
  return { effect, synthetic: 'deny', durable: mutates, changesByDefault: mutates, ...extras };
}
function mixed(defaultEffect, readOperations, extras = {}, operationEffects = {}) {
  const reads = new Set(readOperations);
  return {
    effect: 'mixed',
    resolveEffect: (args) => operationEffects[policyOperation(args)]
      || (reads.has(policyOperation(args)) ? TOOL_EFFECT.read : defaultEffect),
    readOperations: Object.freeze([...reads]), synthetic: 'deny', durable: true, changesByDefault: true, ...extras,
  };
}
const runtimeLifecycle = (extras = {}) => mixed(TOOL_EFFECT.runtime, ['status'], extras,
  { stage: TOOL_EFFECT.persistent, beproef: TOOL_EFFECT.persistent });
const profile = (autonomy, tags, extras) => ({ autonomy, tags: tagList(tags), ...extras });
const bounded = (tags, extras) => profile('bounded', tags, extras);
const full = (tags, extras) => profile('full', tags, extras);
const never = (tags, extras) => profile('none', tags, extras);

export const registerMessenger = TELEGRAM_DELIVERY.registerMessenger;
export const registerPhotoSender = TELEGRAM_DELIVERY.registerPhotoSender;
export const registerVoiceSender = TELEGRAM_DELIVERY.registerVoiceSender;
export const sendRegisteredMessage = TELEGRAM_DELIVERY.sendRegisteredMessage;
export const sendRegisteredPhoto = TELEGRAM_DELIVERY.sendRegisteredPhoto;
export const sendRegisteredVoice = TELEGRAM_DELIVERY.sendRegisteredVoice;
function clean(value, limit = MAX_TOOL_OUTPUT) { return String(value ?? '').slice(0, limit); }
const TOPIC_GENERIC_WORDS = new Set([
  'alleen', 'blijft', 'blijven', 'daarom', 'geeft', 'kunnen', 'morgen',
  'nacht', 'verwacht', 'waarschijnlijk', 'vannacht', 'worden',
]);
const AUTONOMOUS_TOPIC_PATTERNS = /** @type {ReadonlyArray<readonly [string, RegExp]>} */ (Object.freeze([
  ['deur', /\b(?:achterdeur|voordeur|tuindeur|deur)\b/i],
  ['raam', /\b(?:ramen?|dakraam)\b/i],
  ['warmte', /\b(?:hitte|warmte|warm|benauwd|koelte|koeler|temperatuur)\b/i],
  ['regen', /\b(?:regen|regent|regenbui|neerslag|droog)\b/i],
  ['aanwezigheid', /\b(?:thuis|aanwezig|afwezig|vertrokken|aangekomen)\b/i],
  ['voice', /\b(?:voice\s*pe|assist[_ -]?satellite|spraak|omroep)\b/i],
  ['overkapping', /\b(?:overkapping|terras)\b/i],
]));
function autonomousTopicKeys(value) {
  return new Set(AUTONOMOUS_TOPIC_PATTERNS
    .filter(([, pattern]) => pattern.test(String(value || '')))
    .map(([key]) => key));
}
const AUTONOMOUS_CONDITION_PATTERNS = /** @type {ReadonlyArray<readonly [string, RegExp]>} */ (Object.freeze([
  ['open', /\bopen\b/i], ['dicht', /\bdicht|gesloten\b/i],
  ['regen', /\bregent|regenbui|neerslag\b/i], ['droog', /\bdroog\b/i],
  ['warmer', /\bwarmer|opwarming\b/i], ['koeler', /\bkoeler|afkoeling\b/i],
  ['thuis', /\bthuis|aanwezig|aangekomen\b/i], ['weg', /\bafwezig|weg|vertrokken\b/i],
  ['beschikbaar', /\bbeschikbaar|verbonden|online\b/i],
  ['onbeschikbaar', /\bonbeschikbaar|unavailable|offline\b/i],
]));
function autonomousConditionKeys(value) {
  return new Set(AUTONOMOUS_CONDITION_PATTERNS
    .filter(([, pattern]) => pattern.test(String(value || '')))
    .map(([key]) => key));
}
function topicStem(word) {
  if (/^ram(?:en)?$/.test(word)) return 'raam';
  if (/^lamp(?:en)?$/.test(word)) return 'lamp';
  if (/^licht(?:en)?$/.test(word)) return 'licht';
  if (/^temperatur/.test(word)) return 'temperatuur';
  if (/^afsluit/.test(word)) return 'afsluiting';
  if (/^camera/.test(word)) return 'camera';
  return word;
}
function topicBigrams(value) {
  const tokens = words(value)
    .map(topicStem)
    .filter((word) => word.length >= 3 && !TOPIC_GENERIC_WORDS.has(word));
  return new Set(tokens.slice(0, 50).flatMap((word, index) => (
    index + 1 < tokens.length ? [`${word} ${tokens[index + 1]}`] : []
  )));
}
function autonomousMessagesOverlap(left, right) {
  if (/\b(?:brand|rook|koolmonoxide|inbraak|lekkage|nood|alarm)\b/i.test(String(left || ''))) {
    return false;
  }
  const a = topicBigrams(left);
  const b = topicBigrams(right);
  if ([...a].some((pair) => b.has(pair))) return true;
  const leftTopics = autonomousTopicKeys(left);
  const rightTopics = autonomousTopicKeys(right);
  return [...leftTopics].some((topic) => rightTopics.has(topic));
}
const RESULT_FRAMING_WORDS = new Set([
  'afgerond', 'alle', 'alleen', 'blok', 'blokkade', 'concrete', 'daarmee',
  'dicht', 'echt', 'geld', 'gecontroleerd', 'geslaagd', 'getest', 'goed',
  'hersteld', 'juiste', 'klaar', 'nog', 'ontbrekende', 'opnieuw', 'plek',
  'resultaat', 'staat', 'stap', 'uitgevoerd', 'verdiend', 'weer', 'werkboek',
]);
const NUMBER_WORDS = new Map([
  ['een', '1'], ['twee', '2'], ['drie', '3'], ['vier', '4'], ['vijf', '5'],
  ['zes', '6'], ['zeven', '7'], ['acht', '8'], ['negen', '9'], ['tien', '10'],
]);
function resultFactWords(value) {
  return words(value)
    .map((word) => NUMBER_WORDS.get(word) || topicStem(word))
    .filter((word) => word.length >= 2 && !RESULT_FRAMING_WORDS.has(word));
}
function autonomousMessageHasNewResult(current, previous) {
  const sharedTopic = [...autonomousTopicKeys(current)]
    .some((topic) => autonomousTopicKeys(previous).has(topic));
  if (sharedTopic) {
    const beforeConditions = autonomousConditionKeys(previous);
    const newCondition = [...autonomousConditionKeys(current)]
      .some((condition) => !beforeConditions.has(condition));
    if (newCondition) return true;
  }
  const before = new Set(resultFactWords(previous));
  const currentFacts = [...new Set(resultFactWords(current))];
  const novel = currentFacts.filter((word) => !before.has(word));
  const currentNumbers = currentFacts.filter((word) => /^\d/.test(word));
  const newNumber = currentNumbers.some((number) => !before.has(number));
  const sharedRatio = currentFacts.length
    ? currentFacts.filter((word) => before.has(word)).length / currentFacts.length
    : 1;
  const claimsProof = /\b(?:getest|gecontroleerd|geslaagd|hersteld|uitgevoerd|teruggelezen|gevonden|exitcode|rapport|bestand)\b/i
    .test(String(current || ''));
  // Binnen hetzelfde concrete onderwerp is ander bewijsjargon geen nieuwe
  // gezinsuitkomst. Een echte toestandsovergang of nieuw getal hierboven wel;
  // de rest kan intern in dezelfde werkboog blijven in plaats van statusspam.
  if (sharedTopic) return newNumber;
  if (!newNumber && sharedRatio >= 0.6) return false;
  return claimsProof && (novel.length >= 2 || newNumber);
}

// Dit is een kanaalgrens, geen inhoudsscript: interne uitvoeringsdetails horen in
// de tijdlijn, niet in een spontaan gezinsbericht. Eva blijft vrij om het concrete
// menselijke gevolg in haar eigen woorden te vertellen.
function autonomousMessageIsInternalProgress(value) {
  const text = String(value || '');
  if (/\bdata[\\/]workspace\b|\/(?:home|tmp)\/|\b(?:stdout|stderr|exitcode)\b|```|\{\s*"(?:ok|contract|tool)"/i.test(text)
    || /\b(?:mijn\s+)?(?:huidige\s+)?werkruimte\b|\bregistratieaanroep\b|\bvanuit\s+deze\s+route\b|\btechnische\s+(?:route|blokkade)\b/i.test(text)
    || /\b(?:aanwezigheidsbewijs|uitstand|bewijsgrens|delivery|receipt|werkpunt|werkboek|entity[_ -]?id|tool(?:call)?|op\s+naam\s+gegokt)\b/i.test(text)) {
    return true;
  }
  const signals = [
    /\b(?:regressie)?tests?\b/i,
    /\b(?:audit|testuitslag|werkboek|checkpoint)\b/i,
    /\b(?:script|bestand|json|ya?ml|\.m?js|\.py)\b/i,
    /\b(?:teruggelezen|exitcode|tool(?:uitvoer|resultaat)?)\b/i,
    /\b(?:configuratie|implementatie|runtime|debug)\b/i,
  ].filter((pattern) => pattern.test(text)).length;
  return signals >= 2;
}
function nextQuietHoursEnd(now = new Date()) {
  const [hours, minutes] = String(config.wakeTime || '07:30').split(':').map(Number);
  const due = new Date(now);
  due.setHours(Number.isFinite(hours) ? hours : 7, Number.isFinite(minutes) ? minutes : 30, 0, 0);
  if (due.getTime() <= now.getTime()) due.setDate(due.getDate() + 1);
  return due.toISOString();
}
function commercialResearchEvidenceBoundary(question, value, today = new Date()) {
  const text = String(value || '').trim();
  if (!/\b(?:geld\s+verdienen|inkomsten?|omzet|koper|klant|betaald|prijs|opdracht|vacature|freelance)\b/i.test(String(question || ''))) {
    return text;
  }
  const todayIso = today.toISOString().slice(0, 10);
  const futureDeadlines = [...text.matchAll(/\b(20\d{2}-\d{2}-\d{2})\b/g)]
    .map((match) => match[1])
    .filter((date) => date > todayIso);
  const claimsReachable = /\b(?:open(?:staand|\s+for\s+bidding)?|bereikbare?\s+(?:koper|klant)|reageren\s+kan|loopt\s+.*\baf)\b/i.test(text);
  if (!claimsReachable || futureDeadlines.length) return text;
  return `${text}\n\nBEWIJSGRENS: de actuele open status en bereikbaarheid zijn niet bewezen met een exacte, toekomstige sluitingsdatum op de directe bron. Behandel dit alleen als kandidaat of marktindicatie, niet als bereikbare koper of plaatsbare opdracht.`;
}
function pathInside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
}
function workspaceAliasedPath(input, { defaultToWorkspace = false } = {}) {
  const raw = String(input || '').trim();
  if (!raw || raw.includes('\0')) throw new Error('werkruimtepad ontbreekt of bevat ongeldige tekens');
  const portable = raw.replaceAll('\\', '/');
  const segments = portable.split('/');
  if (segments.includes('..')) throw new Error('padtraversal is niet toegestaan in de werkruimte');
  if (path.isAbsolute(raw)) return path.resolve(raw);
  const withoutDot = portable.replace(/^\.\//u, '');
  if (withoutDot === 'data/workspace' || withoutDot.startsWith('data/workspace/')) {
    return path.resolve(WRITABLE_ROOT, withoutDot.slice('data/workspace'.length).replace(/^\/+/, ''));
  }
  if (defaultToWorkspace) return path.resolve(WRITABLE_ROOT, withoutDot);
  return path.resolve(config.root, withoutDot);
}
function canonicalWorkspacePath(input, { directory = false } = {}) {
  const absolute = workspaceAliasedPath(input, { defaultToWorkspace: true });
  if (!pathInside(WRITABLE_ROOT, absolute) || (!directory && absolute === WRITABLE_ROOT)) {
    throw new Error('pad valt buiten Eva haar werkruimte');
  }
  return absolute;
}
function workspaceDisplayPath(input) {
  const absolute = canonicalWorkspacePath(input, { directory: true });
  const relative = path.relative(WRITABLE_ROOT, absolute).replaceAll(path.sep, '/');
  return relative ? `data/workspace/${relative}` : 'data/workspace';
}
function sensitiveReadablePath(realPath) {
  const relative = path.relative(config.root, realPath).replaceAll(path.sep, '/');
  const basename = path.basename(realPath);
  return SENSITIVE_READ_ROOTS.some((root) => pathInside(root, realPath))
    || /(?:^|\/)data\/eva\.db(?:-(?:wal|shm))?$/i.test(relative)
    || /(?:^|\/)(?:\.env(?:[._-][^/]*)?|auth\.json|credentials?\.json|secrets?\.ya?ml|\.eva_mind_key)$/i.test(relative)
    || /(?:^|\/)[^/]*(?:id_rsa|\.ppk|\.pem|\.key|\.sqlite3?|\.db)(?:-(?:wal|shm))?$/i.test(relative)
    || /^\.(?:npmrc|netrc|git-credentials)$/i.test(basename);
}
function resolveReadablePath(input) {
  const raw = String(input || '').trim();
  let absolute = workspaceAliasedPath(raw);
  // Een relatieve naam kan zowel projectbron als een zojuist gemaakt
  // werkstuk bedoelen. Kies alleen op bestaande, unieke herkomst; zo leest
  // `rapport.js` het gemaakte workspacebestand terug zonder `src/...` of
  // `docs/...` uit de repository anders te behandelen.
  if (!path.isAbsolute(raw) && raw !== '.' && !raw.replaceAll('\\', '/').replace(/^\.\//u, '')
    .startsWith('data/workspace')) {
    const workspaceCandidate = workspaceAliasedPath(raw, { defaultToWorkspace: true });
    const rootExists = fs.existsSync(absolute);
    const workspaceExists = fs.existsSync(workspaceCandidate);
    if (rootExists && workspaceExists && path.resolve(absolute) !== path.resolve(workspaceCandidate)) {
      throw new Error('relatief pad is ambigu tussen project en werkruimte; gebruik data/workspace/...');
    }
    if (workspaceExists && !rootExists) absolute = workspaceCandidate;
  }
  if (!READABLE_ROOTS.some((root) => pathInside(root, absolute))) {
    throw new Error('pad valt buiten Eva haar project en werkruimte');
  }
  const real = fs.realpathSync(absolute);
  if (!READABLE_ROOTS.some((root) => pathInside(fs.realpathSync(root), real))) {
    throw new Error('pad wijst buiten Eva haar project en werkruimte');
  }
  if (sensitiveReadablePath(real)) {
    throw new Error('dit bestand bevat afgeschermde gegevens');
  }
  return real;
}
function syntheticNamespaceName(value) {
  const namespace = String(value || 'synthetic')
    .replace(/[^a-zA-Z0-9._-]+/gu, '_').slice(0, 120) || 'synthetic';
  return ['.', '..'].includes(namespace) ? 'synthetic' : namespace;
}
function existingSyntheticTurnWorkspace(context = {}) {
  if (context?.messageContext?.syntheticTest !== true) return null;
  const turnId = context?.turnUnderstanding?.source?.turnId || context?.turnId;
  if (!String(turnId || '').trim()) return null;
  const namespace = syntheticNamespaceName(turnId);
  const candidate = path.resolve(SYNTHETIC_ARTIFACT_ROOT, namespace);
  if (!pathInside(SYNTHETIC_ARTIFACT_ROOT, candidate) || !fs.existsSync(candidate)) return null;
  const artifactRootStat = fs.lstatSync(SYNTHETIC_ARTIFACT_ROOT);
  const candidateStat = fs.lstatSync(candidate);
  if (artifactRootStat.isSymbolicLink() || candidateStat.isSymbolicLink()
    || !artifactRootStat.isDirectory() || !candidateStat.isDirectory()) {
    throw new Error('synthetische werkruimte moet een echte geïsoleerde map zijn');
  }
  const writableRoot = fs.realpathSync(WRITABLE_ROOT);
  const artifactRoot = fs.realpathSync(SYNTHETIC_ARTIFACT_ROOT);
  const root = fs.realpathSync(candidate);
  if (!pathInside(writableRoot, artifactRoot) || !pathInside(artifactRoot, root)) {
    throw new Error('synthetische werkruimte wijst buiten test-artifacts');
  }
  return { namespace, root };
}
function syntheticReadableProjection(input, context) {
  const workspace = existingSyntheticTurnWorkspace(context);
  if (!workspace) return null;
  const raw = String(input || '').trim();
  if (raw.includes('\0')) throw new Error('werkruimtepad bevat ongeldige tekens');
  if (path.isAbsolute(raw)) return null;
  const portable = raw.replaceAll('\\', '/').replace(/^\.\//u, '');
  if (portable.split('/').includes('..')) {
    throw new Error('padtraversal is niet toegestaan in de synthetische werkruimte');
  }
  const physicalPrefix = path.relative(WRITABLE_ROOT, workspace.root).replaceAll(path.sep, '/');
  const workspaceRelative = portable === 'data/workspace' ? ''
    : portable.startsWith('data/workspace/') ? portable.slice('data/workspace/'.length)
      : null;
  const relative = workspaceRelative != null
    ? workspaceRelative === physicalPrefix ? ''
      : workspaceRelative.startsWith(`${physicalPrefix}/`)
        ? workspaceRelative.slice(physicalPrefix.length + 1)
        : workspaceRelative
    : ['.', ''].includes(portable) ? '' : portable;
  const candidate = path.resolve(workspace.root, relative);
  if (!pathInside(workspace.root, candidate)) {
    throw new Error('synthetisch werkruimtepad valt buiten de turnnamespace');
  }
  return { root: workspace.root, path: candidate };
}
function resolveContextReadablePath(input, context, { fallback = '' } = {}) {
  const projected = syntheticReadableProjection(input, context);
  const readable = resolveReadablePath(projected?.path || String(input || '').trim() || fallback);
  if (projected && !pathInside(projected.root, readable)) {
    throw new Error('synthetisch leespad wijst buiten de turnnamespace');
  }
  return { readable, syntheticRoot: projected?.root || null };
}
const PROJECT_SEARCH_EXTENSIONS = new Set([
  '.cjs', '.css', '.html', '.js', '.json', '.md', '.mjs', '.py', '.service', '.txt', '.yaml', '.yml',
]);
const PROJECT_SEARCH_BASENAMES = new Set(['agents.md', 'license', 'readme']);
const PROJECT_SEARCH_IGNORED_DIRECTORIES = WORKSPACE_FILE_MANIFEST_IGNORED_DIRECTORIES;
const WORKSPACE_LISTING_ENVELOPE_CONTRACT = 'WorkspaceFileListingEnvelope/internal-v1';
const SEARCH_CONTEXT_STOPWORDS = new Set([
  'alles', 'bestaan', 'deze', 'eigen', 'even', 'haar', 'hier', 'kunnen', 'laat',
  'naar', 'niet', 'onze', 'staan', 'verder', 'welke', 'worden', 'zelf', 'eva',
]);
function normalizedSearchTerms(value, maximum = 16) {
  const terms = [...new Set(normalize(value).split(/[^\p{L}\p{N}_.-]+/u)
    .filter((term) => term.length >= 3))];
  const informative = terms.length > 1 ? terms.filter((term) => term !== 'eva') : terms;
  return informative.slice(0, maximum);
}
function searchTermOccurs(normalizedText, term) {
  if (term.length > 5) return normalizedText.includes(term);
  return normalizedText.split(/[^\p{L}\p{N}_.-]+/u).includes(term);
}
/** @param {string} readable */
function readSearchableProjectFile(readable) {
  let descriptor;
  try {
    descriptor = fs.openSync(readable, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const stat = fs.fstatSync(descriptor);
    const extension = path.extname(readable).toLowerCase();
    const basename = path.basename(readable).toLowerCase();
    if (stat.size > 512_000
      || (!PROJECT_SEARCH_EXTENSIONS.has(extension) && !PROJECT_SEARCH_BASENAMES.has(basename))) return null;
    const content = fs.readFileSync(descriptor);
    if (content.includes(0) || !isUtf8(content)) return null;
    return { stat, content };
  } catch {
    return null;
  } finally {
    if (descriptor != null) fs.closeSync(descriptor);
  }
}
/** @param {Buffer} content @param {string[]} terms @param {string[]} requiredTerms */
function bestProjectSearchLine(content, terms, requiredTerms = []) {
  const lines = content.toString('utf8').split(/\r?\n/);
  /** @type {{ index: number, coverage: number, heading: number, excerpt?: string } | null} */
  let best = null;
  for (const [index, line] of lines.entries()) {
    const normalizedLine = normalize(line);
    const coverage = terms.reduce((total, term) => total + (searchTermOccurs(normalizedLine, term) ? 1 : 0), 0);
    const directCoverage = requiredTerms.reduce(
      (total, term) => total + (searchTermOccurs(normalizedLine, term) ? 1 : 0), 0,
    );
    if (requiredTerms.length && !directCoverage) continue;
    if (!coverage) continue;
    const heading = /^\s{0,3}(?:#{1,6}\s+|\|)/u.test(line) ? 1 : 0;
    if (!best || coverage > best.coverage
      || (coverage === best.coverage && heading > best.heading)) best = { index, coverage, heading };
  }
  if (!best) return null;
  const start = Math.max(0, best.index - 2);
  const end = Math.min(lines.length, best.index + 3);
  best.excerpt = lines.slice(start, end)
    .map((line, offset) => `${start + offset + 1}: ${line}`)
    .join('\n');
  return best;
}
/**
 * @param {string} directory
 * @param {string} query
 * @param {{ messageContext?: { syntheticTest?: boolean } }} [context]
 */
function searchReadableTree(directory, query, context = {}) {
  const dataRoot = path.resolve(config.dataDir);
  const workspaceRoot = fs.realpathSync(config.workspaceDir);
  if (pathInside(dataRoot, directory) && !pathInside(workspaceRoot, directory)) {
    throw new Error('recursief zoeken in runtime-data is afgeschermd; gebruik de projectroot of data/workspace');
  }
  const rawQuery = String(query || '').trim();
  const manifestState = createWorkspaceManifestState(directory, rawQuery);
  const terms = manifestState
    ? workspaceManifestSearchTerms(manifestState.query)
    : normalizedSearchTerms(rawQuery.slice(0, 240));
  if (!terms.length) return { entries: [], fileManifest: finishWorkspaceFileManifest(manifestState) };
  /** @type {Array<{ name: string, type: string, size: number, modified_ts: string, match: string, line?: number, excerpt?: string, _score: number }>} */
  const results = [];
  let visited = 0;
  let scannedBytes = 0;
  /** @param {string} current @param {number} depth */
  const scan = (current, depth = 0) => {
    if (depth > WORKSPACE_FILE_MANIFEST_LIMITS.depthCap) {
      markWorkspaceManifestTruncated(manifestState);
      return;
    }
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
        .sort((a, b) => a.name.localeCompare(b.name, 'nl'));
    } catch {
      markWorkspaceManifestAmbiguous(manifestState);
      return;
    }
    for (const [entryIndex, entry] of entries.entries()) {
      if (visited >= WORKSPACE_FILE_MANIFEST_LIMITS.entryCap) {
        if (entryIndex < entries.length) markWorkspaceManifestTruncated(manifestState);
        break;
      }
      visited += 1;
      const expectedKind = entry.isSymbolicLink() ? 'symlink'
        : entry.isDirectory() ? 'directory' : entry.isFile() ? 'file' : 'other';
      observeWorkspaceManifestEntry(manifestState, path.join(current, entry.name), expectedKind);
      if (entry.isSymbolicLink()) {
        markWorkspaceManifestAmbiguous(manifestState);
        continue;
      }
      const absolute = path.join(current, entry.name);
      if (context?.messageContext?.syntheticTest !== true && isSyntheticArtifactPath(absolute)) {
        markWorkspaceManifestAmbiguous(manifestState);
        continue;
      }
      if (entry.isDirectory()) {
        if (PROJECT_SEARCH_IGNORED_DIRECTORIES.has(entry.name.toLowerCase())) {
          markWorkspaceManifestAmbiguous(manifestState);
          continue;
        }
        if (current === config.root && entry.name === 'data') {
          markWorkspaceManifestAmbiguous(manifestState);
          continue;
        }
        try { resolveReadablePath(absolute); } catch {
          markWorkspaceManifestAmbiguous(manifestState);
          continue;
        }
        if (depth >= WORKSPACE_FILE_MANIFEST_LIMITS.depthCap) {
          markWorkspaceManifestTruncated(manifestState);
          continue;
        }
        scan(absolute, depth + 1);
        continue;
      }
      if (!entry.isFile()) continue;
      let readable;
      try {
        readable = resolveReadablePath(absolute);
      } catch {
        markWorkspaceManifestAmbiguous(manifestState);
        continue;
      }
      let stat;
      try {
        stat = fs.statSync(readable);
        if (!stat.isFile()) throw new Error('geen bestand');
      } catch {
        markWorkspaceManifestAmbiguous(manifestState);
        continue;
      }
      addWorkspaceManifestFile(manifestState, readable);
      const relative = pathInside(workspaceRoot, readable)
        ? workspaceDisplayPath(readable)
        : path.relative(config.root, readable).replaceAll(path.sep, '/');
      const normalizedName = normalize(relative);
      const nameCoverage = terms.reduce(
        (total, term) => total + (searchTermOccurs(normalizedName, term) ? 1 : 0), 0,
      );
      const nameMatches = nameCoverage > 0;
      const file = scannedBytes < 8_000_000 ? readSearchableProjectFile(readable) : null;
      if (file) scannedBytes += file.content.length;
      const best = file ? bestProjectSearchLine(file.content, terms) : null;
      if (!nameMatches && !best) continue;
      stat = file?.stat || stat;
      const safeRelative = redactSensitiveText(relative);
      const safeExcerpt = best ? redactSensitiveText(best.excerpt) : '';
      results.push({
        name: safeRelative,
        type: 'file',
        size: stat.size,
        modified_ts: stat.mtime.toISOString(),
        match: best ? 'content' : 'name',
        ...(best ? { line: best.index + 1, excerpt: clean(safeExcerpt, 360) } : {}),
        _score: Math.max(nameCoverage, Number(best?.coverage || 0)) * 100
          + (relative.startsWith('docs/') ? 25 : /^readme(?:\.|$)/iu.test(relative) ? 20 : 0)
          + (relative.startsWith('test/') ? -10 : 0)
          + (best ? 10 : 0) + Number(best?.heading || 0),
      });
    }
  };
  scan(directory);
  const ranked = results
    .sort((a, b) => b._score - a._score || a.name.localeCompare(b.name, 'nl'))
    .slice(0, 5)
    .map(({ _score, ...entry }) => entry);
  const selected = [];
  for (const entry of ranked) {
    if (selected.length && JSON.stringify([...selected, entry]).length > 1_500) break;
    selected.push(entry);
  }
  return { entries: selected, fileManifest: finishWorkspaceFileManifest(manifestState) };
}
function directWorkspaceFileManifest(directory, entries, context = {}) {
  const state = createWorkspaceManifestState(directory, '');
  if (!state) return null;
  for (const [index, entry] of entries.entries()) {
    if (index >= WORKSPACE_FILE_MANIFEST_LIMITS.entryCap) {
      markWorkspaceManifestTruncated(state);
      break;
    }
    const absolute = path.join(directory, entry.name);
    const expectedKind = entry.isSymbolicLink() ? 'symlink'
      : entry.isDirectory() ? 'directory' : entry.isFile() ? 'file' : 'other';
    observeWorkspaceManifestEntry(state, absolute, expectedKind);
    if (context?.messageContext?.syntheticTest !== true && isSyntheticArtifactPath(absolute)) {
      markWorkspaceManifestAmbiguous(state);
      continue;
    }
    if (entry.isSymbolicLink()) {
      markWorkspaceManifestAmbiguous(state);
      continue;
    }
    if (entry.isDirectory()) continue;
    if (!entry.isFile()) {
      markWorkspaceManifestAmbiguous(state);
      continue;
    }
    try {
      const readable = resolveReadablePath(absolute);
      if (!fs.statSync(readable).isFile()) throw new Error('geen bestand');
      addWorkspaceManifestFile(state, readable, { matchQuery: false });
    } catch { markWorkspaceManifestAmbiguous(state); }
  }
  return finishWorkspaceFileManifest(state);
}
function workspaceListingEnvelope(entries, fileManifest = null) {
  return JSON.stringify({
    contract: WORKSPACE_LISTING_ENVELOPE_CONTRACT,
    entries,
    ...(fileManifest ? { file_manifest: fileManifest } : {}),
  });
}
function parseWorkspaceListingEnvelope(name, raw) {
  if (name !== 'lijst_bestanden') return null;
  try {
    const parsed = JSON.parse(String(raw));
    return parsed?.contract === WORKSPACE_LISTING_ENVELOPE_CONTRACT
      && Array.isArray(parsed.entries) ? parsed : null;
  } catch { return null; }
}
function assertNoWritableSymlink(absolute, { includeTarget = true } = {}) {
  const root = fs.realpathSync(WRITABLE_ROOT);
  if (absolute === WRITABLE_ROOT) return;
  const relative = path.relative(WRITABLE_ROOT, absolute);
  let current = WRITABLE_ROOT;
  const parts = relative.split(path.sep).filter(Boolean);
  const count = includeTarget ? parts.length : Math.max(0, parts.length - 1);
  for (const part of parts.slice(0, count)) {
    current = path.join(current, part);
    if (!fs.existsSync(current)) break;
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) throw new Error('symlinks zijn niet toegestaan in een schrijfpad');
  }
  const existingParent = fs.existsSync(path.dirname(absolute))
    ? fs.realpathSync(path.dirname(absolute))
    : root;
  if (!pathInside(root, existingParent)) throw new Error('schrijfpad wijst buiten data/workspace');
}
function resolveWritablePath(input) {
  const absolute = canonicalWorkspacePath(input);
  if (!pathInside(WRITABLE_ROOT, absolute)) {
    throw new Error('schrijven mag alleen in data/workspace');
  }
  assertNoWritableSymlink(absolute);
  return absolute;
}
function ensureWritableDirectory(directory) {
  const relative = path.relative(WRITABLE_ROOT, directory);
  let current = WRITABLE_ROOT;
  for (const part of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (fs.existsSync(current)) {
      const stat = fs.lstatSync(current);
      if (stat.isSymbolicLink() || !stat.isDirectory()) {
        throw new Error('ieder onderdeel van een schrijfpad moet een echte werkruimtemap zijn');
      }
    } else {
      fs.mkdirSync(current, { mode: 0o700 });
    }
  }
  assertNoWritableSymlink(directory);
}
function readExistingWritableFileNoFollow(file) {
  if (fs.constants.O_NOFOLLOW == null || fs.constants.O_NONBLOCK == null) {
    throw new Error('veilige no-follow schrijfdoelcontrole is niet beschikbaar');
  }
  const flags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK;
  let descriptor;
  try { descriptor = fs.openSync(file, flags); } catch (error) {
    if (error?.code === 'ENOENT') return null;
    throw error;
  }
  try {
    const before = fs.fstatSync(descriptor);
    if (!before.isFile() || !Number.isSafeInteger(before.size) || before.size < 0
      || before.size > 512_000) {
      throw new Error('bestaand schrijfdoel moet een regulier bestand zijn');
    }
    const content = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < content.length) {
      const bytes = fs.readSync(descriptor, content, offset, content.length - offset, offset);
      if (bytes <= 0) throw new Error('schrijfdoel wijzigde tijdens de no-follow controle');
      offset += bytes;
    }
    if (fs.readSync(descriptor, Buffer.alloc(1), 0, 1, content.length) !== 0) {
      throw new Error('schrijfdoel wijzigde tijdens de no-follow controle');
    }
    const after = fs.fstatSync(descriptor);
    if (!after.isFile() || String(after.dev) !== String(before.dev)
      || String(after.ino) !== String(before.ino) || after.size !== before.size
      || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs) {
      throw new Error('schrijfdoel wijzigde tijdens de no-follow controle');
    }
    if (!isUtf8(content)) throw new Error('bestaand schrijfdoel is geen UTF-8 tekstbestand');
    return {
      content: content.toString('utf8'),
      sha256: createHash('sha256').update(content).digest('hex'),
      device: String(after.dev), inode: String(after.ino), size: after.size,
      mtimeMs: after.mtimeMs, ctimeMs: after.ctimeMs,
    };
  } finally { fs.closeSync(descriptor); }
}
function isSyntheticArtifactPath(value) {
  return /(?:^|[\\/])test-artifacts(?:[\\/]|$)/i.test(String(value || ''));
}
function rejectSyntheticArtifactLeak(value, context) {
  if (context?.messageContext?.syntheticTest === true) return;
  if (isSyntheticArtifactPath(value)) {
    throw new Error('geïsoleerde testartefacten zijn geen bron voor echte gesprekken of zelfstandige aandacht');
  }
}
function workspaceCwd(input = '') {
  const value = String(input || '').trim();
  const absolute = value
    ? canonicalWorkspacePath(value, { directory: true })
    : WRITABLE_ROOT;
  if (!pathInside(WRITABLE_ROOT, absolute)) {
    throw new Error('programma\'s mogen alleen vanuit data/workspace draaien');
  }
  assertNoWritableSymlink(absolute);
  const real = fs.realpathSync(absolute);
  if (!pathInside(fs.realpathSync(WRITABLE_ROOT), real)) {
    throw new Error('programmawerkmap wijst buiten data/workspace');
  }
  return real;
}
function fileReceipt(file) {
  const stat = fs.statSync(file);
  return {
    path: workspaceDisplayPath(file),
    bytes: stat.size,
    sha256: createHash('sha256').update(fs.readFileSync(file)).digest('hex'),
  };
}
function addProgramArtifactReceipt(receipts, file) {
  const receipt = fileReceipt(file);
  if (!receipts.some((item) => item.path === receipt.path)) receipts.push(receipt);
}
function verifiedPriorWriteFile(result, directory) {
  if (result?.name !== 'schrijf_bestand' || !verifiedCompleteReceipt(result)) return null;
  try {
    const file = fs.realpathSync(canonicalWorkspacePath(result.args?.pad));
    if (path.dirname(file) !== directory || !fs.statSync(file).isFile()) return null;
    const parsed = JSON.parse(String(result.output || ''));
    const expected = String(parsed?.data?.material_sha256 || '');
    const actual = fileReceipt(file).sha256;
    return expected && expected === actual ? file : null;
  } catch { return null; }
}
function continuationWriteTargetAllowed(file, context, currentSha256 = null) {
  const receipts = Array.isArray(context?.artifactReceipts) ? context.artifactReceipts : [];
  if (!receipts.length || currentSha256 === null) return true;
  const receipt = receipts.find((item) => {
    try { return canonicalWorkspacePath(item.path) === file; } catch { return false; }
  });
  if (receipt && currentSha256 === String(receipt.sha256)) return true;
  return (context.priorToolResults || []).some((result) => {
    if (result?.name !== 'schrijf_bestand' || !verifiedCompleteReceipt(result)) return false;
    let sameTarget = false;
    try { sameTarget = canonicalWorkspacePath(result.args?.pad) === file; } catch { return false; }
    if (!sameTarget) return false;
    try {
      return String(JSON.parse(String(result.output || ''))?.data?.material_sha256 || '')
        === currentSha256;
    } catch { return false; }
  });
}
function sameWritableObservation(left, right) {
  if (left === null || right === null) return left === right;
  return ['sha256', 'device', 'inode', 'size', 'mtimeMs', 'ctimeMs']
    .every((key) => left[key] === right[key]);
}
function programArtifactReceipts(args = [], cwd = '', priorToolResults = []) {
  let directory;
  try { directory = workspaceCwd(cwd); } catch { return []; }
  const receipts = [];
  for (const argument of Array.isArray(args) ? args : []) {
    const value = String(argument || '').trim();
    if (!value || value.startsWith('-')) continue;
    const candidate = path.resolve(value.startsWith('/') ? value : path.join(directory, value));
    try {
      const real = fs.realpathSync(candidate);
      if (!pathInside(fs.realpathSync(WRITABLE_ROOT), real) || !fs.statSync(real).isFile()) continue;
      addProgramArtifactReceipt(receipts, real);
    } catch { /* Een gewoon CLI-argument hoeft geen bestand te zijn. */ }
  }
  for (const result of (Array.isArray(priorToolResults) ? priorToolResults : []).slice(-40)) {
    const file = verifiedPriorWriteFile(result, directory);
    if (file) addProgramArtifactReceipt(receipts, file);
  }
  return receipts.slice(0, 20);
}
function assertContinuationProgramReceipts(args, context) {
  const expected = Array.isArray(context?.artifactReceipts) ? context.artifactReceipts : [];
  if (!expected.length) return;
  const operation = TOOL_REGISTRY.flatMap((tool) => tool.provides)
    .find((provided) => provided.capability === 'workspace.program.execute')?.operations[0];
  const workingDirectory = workspaceCwd(args.werkmap);
  const classified = classifyProgramWorkspaceArguments(operation, args, workingDirectory);
  const expectedFiles = new Map(expected.map((receipt) => [
    canonicalWorkspacePath(receipt.path), String(receipt.sha256),
  ]));
  const receiptDirectories = new Set([...expectedFiles.keys()].map(path.dirname));
  const writtenFixtures = new Set((context.priorToolResults || []).flatMap((result) => (
    [...receiptDirectories].map((directory) => verifiedPriorWriteFile(result, directory)).filter(Boolean)
  )));
  const matchesReceipt = (file) => expectedFiles.has(file)
    && fileReceipt(file).sha256 === expectedFiles.get(file);
  if (classified.materializedArtifacts.some((file) => !matchesReceipt(file))) {
    throw new Error('programma-entrypoint wijkt af van het geverifieerde episodereceipt');
  }
  if (classified.readDependencies.some((file) => (
    !matchesReceipt(file) && !writtenFixtures.has(file)
  ))) throw new Error('programma-invoer mist een geverifieerde actuele fixture-receipt');
}
function inferredBoundTestArguments(testEntrypoint, priorToolResults = []) {
  let target;
  try { target = fs.realpathSync(resolveWritablePath(testEntrypoint)); } catch { return []; }
  for (const result of [...priorToolResults].reverse()) {
    if (result?.name !== 'voer_uit' || !result.ok || !result.verified) continue;
    const args = Array.isArray(result.args?.argumenten) ? result.args.argumenten.map(String) : [];
    const index = args.findIndex((argument) => /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(argument));
    if (index < 0) continue;
    let executed;
    try {
      const cwd = workspaceCwd(result.args?.werkmap || '');
      const raw = args[index];
      executed = fs.realpathSync(path.resolve(raw.startsWith('/') ? raw : path.join(cwd, raw)));
    } catch { continue; }
    if (executed === target) return args.filter((_argument, argIndex) => argIndex !== index);
  }
  return [];
}
function normalizeExpectedExitCodes(values) {
  const codes = (Array.isArray(values) ? values : [0])
    .map(Number)
    .filter((value) => Number.isInteger(value) && value >= 0 && value <= 255);
  return [...new Set(codes.length ? codes : [0])].slice(0, 12);
}
function normalizeHaReadPath(value = '') {
  const apiPath = String(value).trim();
  const queryAt = apiPath.indexOf('?');
  if (queryAt < 0) return apiPath;
  // In een query wordt een kale '+' als spatie gelezen. LLM's schrijven een
  // ISO-offset vaak als +02:00; behoud die letterlijk voor Home Assistant.
  return `${apiPath.slice(0, queryAt + 1)}${apiPath.slice(queryAt + 1).replaceAll('+', '%2B')}`;
}
async function runProgram(program, args = [], cwd = '', expectedExitCodes = [0], timeoutMs = 120_000) {
  const executable = path.basename(String(program || '').trim());
  if (!SAFE_PROGRAMS.has(executable)) throw new Error(`programma "${executable}" is niet toegestaan`);
  const executablePath = SAFE_EXECUTABLES.get(executable);
  const safeArgs = Array.isArray(args) ? args.map((arg) => String(arg)) : [];
  const expected = normalizeExpectedExitCodes(expectedExitCodes);
  if (['python', 'python3'].includes(executable) && safeArgs.some((arg) => ['-c', '-m'].includes(arg))) {
    throw new Error('Python inline- of module-uitvoering is niet toegestaan; voer een bestand uit de werkruimte uit');
  }
  if (executable === 'node' && safeArgs.includes('-e')) {
    throw new Error('Node inline-uitvoering is niet toegestaan; voer een bestand uit de werkruimte uit');
  }
  const workingDirectory = workspaceCwd(cwd);
  const runtimeMs = boundedRuntimeMs(timeoutMs);
  // Voor Node en Python is het eerste niet-optieargument het programma dat
  // Eva zegt uit te voeren. Een verwachte exitcode mag nooit een ontbrekend
  // entrypoint in een geslaagde proef veranderen.
  if (['node', 'python', 'python3'].includes(executable)) {
    const entrypoint = safeArgs.find((argument) => !argument.startsWith('-'));
    if (entrypoint) {
      const absoluteEntrypoint = path.resolve(
        entrypoint.startsWith('/') ? entrypoint : path.join(workingDirectory, entrypoint),
      );
      if (!fs.existsSync(absoluteEntrypoint) || !fs.statSync(absoluteEntrypoint).isFile()) {
        throw new Error(`programma-entrypoint bestaat niet: ${entrypoint}`);
      }
    }
  }
  const childEnv = {
    PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin',
    LANG: process.env.LANG || 'C.UTF-8',
    LC_ALL: process.env.LC_ALL || process.env.LANG || 'C.UTF-8',
    TZ: process.env.TZ || 'Europe/Amsterdam',
    HOME: WRITABLE_ROOT,
    TMPDIR: '/tmp',
    npm_config_cache: path.join(WRITABLE_ROOT, '.npm-cache'),
    XDG_RUNTIME_DIR: `/run/user/${process.getuid()}`,
    DBUS_SESSION_BUS_ADDRESS: `unix:path=/run/user/${process.getuid()}/bus`,
  };
  const unit = `eva-work-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const runnerArgs = [
    '--user', '--quiet', '--wait', '--pipe', '--collect', `--unit=${unit}`,
    '--property=NoNewPrivileges=yes',
    '--property=ProtectSystem=strict',
    '--property=ProtectHome=tmpfs',
    `--property=BindPaths=${WRITABLE_ROOT}`,
    `--property=ReadOnlyPaths=-${path.join(WRITABLE_ROOT, 'extensions')}`,
    `--property=WorkingDirectory=${workingDirectory}`,
    '--property=PrivateTmp=yes',
    '--property=PrivateDevices=yes',
    '--property=PrivateMounts=yes',
    '--property=PrivateNetwork=yes',
    '--property=ProtectProc=invisible',
    '--property=ProcSubset=pid',
    '--property=ProtectControlGroups=yes',
    '--property=ProtectKernelModules=yes',
    '--property=ProtectKernelTunables=yes',
    '--property=RestrictSUIDSGID=yes',
    '--property=LockPersonality=yes',
    '--property=DevicePolicy=closed',
    '--property=MemoryMax=768M',
    '--property=TasksMax=64',
    '--property=CPUQuota=200%',
    `--property=RuntimeMaxSec=${Math.max(1, Math.ceil(runtimeMs / 1_000))}`,
    '--property=LimitNOFILE=256',
    '--property=KillMode=control-group',
    '--property=UMask=0077',
    `--property=InaccessiblePaths=-/run/user/${process.getuid()}/bus -/run/docker.sock -/var/run/docker.sock`,
    `--setenv=HOME=${WRITABLE_ROOT}`,
    '--setenv=PATH=/usr/local/bin:/usr/bin:/bin',
    `--setenv=LANG=${childEnv.LANG}`,
    `--setenv=LC_ALL=${childEnv.LC_ALL}`,
    `--setenv=TZ=${childEnv.TZ}`,
    `--setenv=TMPDIR=/tmp`,
    `--setenv=npm_config_cache=${childEnv.npm_config_cache}`,
    executablePath,
    ...safeArgs,
  ];
  return new Promise((resolve, reject) => {
    const child = spawn('/usr/bin/systemd-run', runnerArgs, {
      cwd: config.root,
      shell: false,
      env: childEnv,
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`programma duurde langer dan ${runtimeMs} milliseconden`));
    }, runtimeMs);
    child.stdout.on('data', (chunk) => { stdout = clean(stdout + chunk, 40_000); });
    child.stderr.on('data', (chunk) => { stderr = clean(stderr + chunk, 20_000); });
    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      const outputText = `${stdout}\n${stderr}`;
      const semanticFailure = /\b(?:ha[_ -]?token|api[_ -]?key|access[_ -]?token|secret|wachtwoord)\b.{0,80}\b(?:ontbreekt|missing|not\s+set|niet\s+ingesteld)\b/i.test(outputText)
        || /\b(?:uncaught|traceback|syntaxerror|referenceerror|typeerror)\b/i.test(outputText);
      resolve(JSON.stringify({
        ok: expected.includes(code) && !semanticFailure,
        exit_code: code,
        expected_exit_codes: expected,
        stdout,
        stderr,
        ...(semanticFailure ? { reason: 'programma meldde een uitvoerfout ondanks de toegestane exitcode' } : {}),
      }));
    });
  });
}
const capability = (id, operations, options = {}) => (
  { id, operations: Array.isArray(operations) ? operations : [operations], ...options });
const semanticOperation = (id, options = {}) => ({ id, ...options });
const opRequires = (id, required, options = {}) => semanticOperation(id, { required, ...options });
const semanticInput = (properties = {}, required = [], fields = {}, values = {}, normalizers = {}) => (
  { properties, required, fields, values, normalizers });
const executionRef = (capabilityId, operationId, type) => ({ capabilityId, operationId, type });
const iterativeExecution = ({
  materializable = [], targetField, artifactLimit = 4, proofOperations = [], recoveryOperations = [],
}) => ({
  mode: 'iterative', materializable,
  target: { field: targetField, boundary: 'workspace', sameDirectory: true, artifactLimit },
  proofOperations, recoveryOperations,
});
const SEMANTIC_VALUES = Object.freeze({
  authority: new Set(['ambient', 'explicit', 'trusted_context', 'trusted_owner', 'root_approved']),
  idempotency: new Set(['repeatable', 'turn_scoped', 'deduplicated', 'cas', 'sealed_transaction']),
  proof: new Set(['source_read', 'accepted_result', 'verified_effect', 'delivery_receipt', 'root_receipt']),
});
const INTERACTIVE_AUTHORITY = Object.freeze([
  'ambient', 'explicit', 'trusted_context', 'trusted_owner', 'root_approved',
]);
const HANDLE_BINDINGS = new WeakMap();
const ISSUED_PLAN_METADATA = new WeakMap();
const PENDING_EXPLORATION_EVIDENCE_RECEIPTS = new Map();
const RESOLVED_SELF_ATTENTION_GOAL_ARGS = new WeakMap();
const CONSUMED_SELF_ATTENTION_GOAL_ARGS = new WeakSet();
const ISSUED_EXPLORATION_TERMINAL_EXECUTIONS = new WeakMap();
const CONSUMED_EXPLORATION_TERMINAL_EXECUTIONS = new WeakSet();
const HOME_CONTEXT_RECOVERY_ARGS = new WeakMap();
const ISSUED_HOME_CONTEXT_RECOVERY_ARGS = new WeakSet();
const PRIOR_HOME_CONTEXT_ARGS = new WeakMap();
const ISSUED_PRIOR_HOME_CONTEXT_ARGS = new WeakSet();
const CONTINUITY_HOME_CONTEXT_ARGS = new WeakSet();
const SIGNAL_ASSESSMENT_ARGS = new WeakMap();
const EXECUTION_HANDLE_METADATA = new WeakMap();
const EXECUTION_OBLIGATION_METADATA = new WeakMap();
const explorationEvidenceSummary = (value) => String(redactSensitiveText(value) || '')
  .replace(/\s+/gu, ' ').trim().slice(0, 900);
function claimPendingExplorationEvidenceReceipt(
  { current, options } = /** @type {any} */ ({}),
) {
  const evidence = explorationEvidenceSummary(options?.evidence);
  const evidenceSha256 = createHash('sha256').update(evidence).digest('hex');
  const entries = [...PENDING_EXPLORATION_EVIDENCE_RECEIPTS.entries()].reverse();
  const matched = entries.find(([, receipt]) => (
    Number(receipt.explorationId) === Number(current?.id)
    && Number(receipt.explorationRevision) === Number(current?.revision)
    && Number(receipt.explorationActionCount) === Number(current?.action_count)
    && Number(receipt.episodeId) === Number(options?.episodeId)
    && receipt.tool === clean(options?.tool, 80)
    && receipt.evidenceSha256 === evidenceSha256
    && receipt.topicSha256 === explorationEvidenceTopicSha256(current)
  ));
  if (!matched) return null;
  const [key, receipt] = matched;
  return Object.freeze({
    receipt,
    consume: () => PENDING_EXPLORATION_EVIDENCE_RECEIPTS.delete(key),
  });
}
registerExplorationEvidenceReceiptVerifier(claimPendingExplorationEvidenceReceipt);
const authorityAllows = (operation, mode) => mode === 'chat' && operation.authority.interactive === 'ambient';
const defineTool = (name, handler, policy, description, parameters, provides) => (
  { name, handler, policy, description, parameters, provides: Array.isArray(provides) ? provides : [provides] });
const semanticArgs = (tool, operation) => {
  const field = tool.parameters?.properties?.actie ? 'actie' : tool.parameters?.properties?.bewerking ? 'bewerking' : null;
  return field ? { [field]: operation } : {};
};
function deepFrozenClone(value) {
  if (Array.isArray(value)) return Object.freeze(value.map(deepFrozenClone));
  if (value && typeof value === 'object') return Object.freeze(Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, deepFrozenClone(item)])));
  return value;
}
function deepFreeze(value, seen = new Set()) {
  if (!value || !['object', 'function'].includes(typeof value) || seen.has(value)) return value;
  seen.add(value);
  for (const child of Object.values(value)) deepFreeze(child, seen); return Object.freeze(value);
}
function materializeInput(tool, provided, operation) {
  const operationField = tool.parameters?.properties?.actie ? 'actie' : tool.parameters?.properties?.bewerking ? 'bewerking' : null;
  const declared = operation.input || provided.input;
  const properties = deepFrozenClone(Object.fromEntries(
    Object.entries(declared?.properties || tool.parameters?.properties || {})
      .filter(([name]) => name !== operationField)));
  const required = deepFrozenClone([...(operation.required || declared?.required || tool.parameters?.required || [])]
    .filter((name) => name !== operationField));
  const fields = deepFrozenClone(Object.fromEntries(Object.keys(properties).map(
    (name) => [name, declared?.fields?.[name] || name])));
  const values = deepFrozenClone(declared?.values || {});
  const normalizers = deepFrozenClone(declared?.normalizers || {});
  return Object.freeze({
    schema: deepFrozenClone({ type: 'object', properties, required, additionalProperties: false }),
    fields, values, normalizers, operationField,
    action: operation.action === false ? null : String(operation.action || operation.id),
  });
}
function materializeOperationRef(value) {
  return deepFrozenClone({
    capabilityId: String(value?.capabilityId || ''),
    operationId: String(value?.operationId || ''),
    type: String(value?.type || ''),
  });
}
function materializeExecution(provided, operation) {
  const raw = operation.execution || provided.execution || {};
  const mode = String(raw.mode || 'direct');
  const target = raw.target ? deepFrozenClone({
    field: String(raw.target.field || ''), boundary: String(raw.target.boundary || ''),
    sameDirectory: raw.target.sameDirectory === true,
    artifactLimit: Number(raw.target.artifactLimit),
  }) : null;
  return deepFrozenClone({
    mode,
    materializable: [...new Set(Array.isArray(raw.materializable) ? raw.materializable.map(String) : [])],
    target,
    proofOperations: (Array.isArray(raw.proofOperations) ? raw.proofOperations : []).map(materializeOperationRef),
    recoveryOperations: (Array.isArray(raw.recoveryOperations) ? raw.recoveryOperations : []).map(materializeOperationRef),
    fileArguments: raw.fileArguments == null ? null : normalizeFileArgumentsMetadata(raw.fileArguments),
  });
}
function buildToolRegistry(entries) {
  const registry = entries.map((tool) => Object.freeze({
    ...tool, provides: Object.freeze(tool.provides.map((provided) => Object.freeze({
      capability: provided.id,
      operations: Object.freeze(provided.operations.map((rawOperation) => {
        const operation = typeof rawOperation === 'string' ? { id: rawOperation } : rawOperation;
        const { id } = operation;
        const effect = tool.policy.effect === 'mixed'
          ? tool.policy.resolveEffect(semanticArgs(tool, operation.action || id)) : tool.policy.effect;
        const proofFloor = new Set(operation.proofFloor || provided.proofFloor || []);
        proofFloor.add(effect === TOOL_EFFECT.read ? 'source_read'
          : tool.policy.verification === 'explicit' ? 'verified_effect' : 'accepted_result');
        if (tool.policy.report === 'delivery') proofFloor.add('delivery_receipt');
        if (effect === TOOL_EFFECT.runtime) proofFloor.add('root_receipt');
        const binding = materializeInput(tool, provided, operation);
        const execution = materializeExecution(provided, operation);
        const operationFields = (name) => Object.freeze([...new Set(operation[name]
          || provided[name] || [])].map(String));
        const signalScope = operationFields('signalScope');
        const signalKeyField = String(operation.signalKeyField || provided.signalKeyField || '');
        return Object.freeze({ id, effect,
          authority: Object.freeze({
            interactive: operation.authority || provided.authority || (effect === TOOL_EFFECT.read
              ? 'ambient' : effect === TOOL_EFFECT.runtime ? 'root_approved' : 'explicit'),
            autonomy: tool.policy.autonomy }),
          idempotency: operation.idempotency || provided.idempotency
            || (effect === TOOL_EFFECT.read ? 'repeatable' : 'turn_scoped'),
          proofFloor: Object.freeze([...proofFloor]),
          supportsDeferred: operation.supportsDeferred === true || provided.supportsDeferred === true,
          fastlane: operation.fastlane || null, input: binding.schema, binding, execution,
          rebindableFields: operationFields('rebindableFields'), sourceBoundFields: operationFields('sourceBoundFields'),
          signalScope, signalKeyField,
        });
      })),
    }))),
  }));
  validateToolRegistry(registry); return deepFreeze(registry);
}
const registryInvariant = (valid, message) => { if (!valid) throw new Error(message); };
function executionRefValid(reference, operationKeys) {
  return reference && /^[a-z][a-z0-9_.-]+$/u.test(reference.capabilityId)
    && /^[a-z0-9][a-z0-9_-]*$/u.test(reference.operationId)
    && /^[a-z][a-z0-9_-]*$/u.test(reference.type)
    && operationKeys.has(`${reference.capabilityId}:${reference.operationId}`);
}
function executionMetadataValid(operation, inputNames, operationKeys, ownKey) {
  const execution = operation.execution;
  const required = new Set(operation.input?.required || []);
  if (!execution || !['direct', 'iterative'].includes(execution.mode)
    || !Array.isArray(execution.materializable)
    || !Array.isArray(execution.proofOperations)
    || !Array.isArray(execution.recoveryOperations)) return false;
  if (execution.mode === 'direct') return execution.materializable.length === 0
    && execution.target == null && execution.proofOperations.length === 0
    && execution.recoveryOperations.length === 0
    && fileArgumentsMetadataValid(execution.fileArguments, operation.input);
  const target = execution.target;
  const references = [...execution.proofOperations, ...execution.recoveryOperations];
  return execution.fileArguments == null && execution.materializable.length > 0
    && new Set(execution.materializable).size === execution.materializable.length
    && execution.materializable.every((field) => inputNames.includes(field))
    && execution.materializable.every((field) => required.has(field))
    && target?.boundary === 'workspace' && target.sameDirectory === true
    && inputNames.includes(target.field) && required.has(target.field)
    && !execution.materializable.includes(target.field)
    && Number.isInteger(target.artifactLimit) && target.artifactLimit >= 1 && target.artifactLimit <= 12
    && execution.proofOperations.length > 0 && execution.recoveryOperations.length > 0
    && references.every((reference) => executionRefValid(reference, operationKeys))
    && execution.proofOperations.some((reference) => (
      `${reference.capabilityId}:${reference.operationId}` === ownKey
    ));
}
function operationMetadataValid(tool, operation, operationKeys, ownKey) {
  const expectedEffect = tool.policy.effect === 'mixed'
    ? tool.policy.resolveEffect(semanticArgs(tool, operation.binding?.action || operation.id)) : tool.policy.effect;
  const inputNames = Object.keys(operation.input?.properties || {}),
    required = Array.isArray(operation.input?.required) ? operation.input.required : [],
    mappedFields = Object.values(operation.binding?.fields || {}), physicalFields = tool.parameters?.properties || {},
    proofFloor = Array.isArray(operation.proofFloor) ? operation.proofFloor : [],
    declaredFieldsValid = (names) => Array.isArray(names) && new Set(names).size === names.length && names.every((name) => inputNames.includes(name));
  return [
    EFFECT_VALUES.has(operation.effect), operation.effect === expectedEffect,
    SEMANTIC_VALUES.authority.has(operation.authority?.interactive), ['bounded', 'full', 'none'].includes(operation.authority?.autonomy),
    operation.authority?.autonomy === tool.policy.autonomy, SEMANTIC_VALUES.idempotency.has(operation.idempotency),
    proofFloor.length > 0, new Set(proofFloor).size === proofFloor.length,
    proofFloor.every((proof) => SEMANTIC_VALUES.proof.has(proof)), typeof operation.supportsDeferred === 'boolean',
    [null, 'literal_voice', 'contextual_home'].includes(operation.fastlane),
    declaredFieldsValid(operation.rebindableFields), operation.rebindableFields.every((name) => (
      /(?:^|_)(?:pad|path|entity|entity_id|entityid|target)$/iu.test(name))),
    declaredFieldsValid(operation.sourceBoundFields),
    Array.isArray(operation.signalScope), operation.signalScope.every((selector) => (
      /^[a-z][a-z0-9_-]{0,59}:(?:[a-z0-9_]+\.[a-z0-9_]+|[a-z0-9_]+\.\*)$/u.test(selector))),
    operation.signalScope.length ? operation.sourceBoundFields.includes(operation.signalKeyField)
      : operation.signalKeyField === '',
    operation.input?.type === 'object',
    operation.input?.additionalProperties === false, Array.isArray(operation.input?.required),
    required.every((name) => inputNames.includes(name)), new Set(required).size === required.length,
    inputNames.every((name) => Object.hasOwn(operation.binding?.fields || {}, name)), mappedFields.every((name) => Object.hasOwn(physicalFields, name)),
    new Set(mappedFields).size === mappedFields.length,
    Object.entries(operation.binding?.values || {}).every(([name, mapping]) => inputNames.includes(name)
      && Object.keys(mapping).every((value) => !jsonSchemaViolation(operation.input.properties[name], value))),
    Object.entries(operation.binding?.normalizers || {}).every(([name, normalizer]) => (
      inputNames.includes(name) && ['workspace_file'].includes(normalizer)
    )),
    !operation.binding?.operationField || Object.hasOwn(physicalFields, operation.binding.operationField),
    operation.binding?.action == null || !mappedFields.includes(operation.binding.operationField),
    executionMetadataValid(operation, inputNames, operationKeys, ownKey),
  ].every(Boolean);
}
export function validateToolRegistry(registry) {
  registryInvariant(Array.isArray(registry) && registry.length, 'TOOL_REGISTRY is leeg');
  const names = new Set(), operationKeys = new Set();
  const declaredOperationKeys = new Set(registry.flatMap((tool) => tool.provides?.flatMap((provided) => (
    provided.operations?.map((operation) => `${provided.capability}:${operation.id}`) || []
  )) || []));
  let literalVoiceOperations = 0;
  let contextualHomeOperations = 0;
  for (const tool of registry) {
    registryInvariant(tool.name && !names.has(tool.name), `dubbele of lege toolnaam: ${tool.name || '<leeg>'}`);
    names.add(tool.name);
    registryInvariant(typeof tool.handler === 'function' && tool.parameters?.type === 'object' && tool.provides?.length,
      `onvolledige registry-entry: ${tool.name}`);
    registryInvariant(Array.isArray(tool.policy?.tags) && new Set(tool.policy.tags).size === tool.policy.tags.length,
      `ongeldige getypeerde tags: ${tool.name}`);
    const operations = [];
    for (const provided of tool.provides) {
      registryInvariant(/^[a-z][a-z0-9_.-]+$/u.test(provided.capability) && provided.operations?.length,
        `ongeldige capability: ${tool.name}`);
      for (const operation of provided.operations) {
        const key = `${provided.capability}:${operation.id}`;
        registryInvariant(/^[a-z0-9][a-z0-9_-]*$/u.test(operation.id) && !operationKeys.has(key),
          `dubbele of ongeldige operation: ${tool.name}:${key}`);
        operationKeys.add(key);
        operations.push(operation);
        registryInvariant(operationMetadataValid(tool, operation, declaredOperationKeys, key),
          `ongeldige operationele metadata: ${tool.name}:${key}`);
        if (operation.fastlane === 'literal_voice') literalVoiceOperations += 1;
        if (operation.fastlane === 'contextual_home') contextualHomeOperations += 1;
        registryInvariant(operation.fastlane !== 'literal_voice' || operation.effect === TOOL_EFFECT.voice,
          `ongeldige literal_voice-fastlane: ${tool.name}:${key}`);
        registryInvariant(operation.fastlane !== 'contextual_home' || operation.effect === TOOL_EFFECT.home,
          `ongeldige contextual_home-fastlane: ${tool.name}:${key}`);
      }
    }
    for (const id of tool.policy.readOperations || []) registryInvariant(operations.some((item) =>
      (item.binding?.action || item.id) === id && item.effect === TOOL_EFFECT.read),
    `ontbrekende leesoperation: ${tool.name}:${id}`);
  }
  registryInvariant(literalVoiceOperations === 1, 'literal_voice-fastlane moet exact eenmaal bestaan');
  registryInvariant(contextualHomeOperations === 1, 'contextual_home-fastlane moet exact eenmaal bestaan');
  return true;
}
function bindInput(binding, semanticInputValue = {}) {
  const input = semanticInputValue == null ? {} : semanticInputValue;
  const violation = jsonSchemaViolation(binding.schema, input, 'semanticInput');
  if (violation) throw new TypeError(violation);
  const args = Object.fromEntries(Object.entries(input).map(([name, value]) => [
    binding.fields[name], binding.normalizers?.[name] === 'workspace_file'
      ? canonicalWorkspacePath(value)
      : Object.hasOwn(binding.values[name] || {}, value) ? binding.values[name][value] : value,
  ]));
  if (binding.operationField && binding.action != null) args[binding.operationField] = binding.action; return Object.freeze(args);
}
function parentSemanticBinding(operation) {
  if (operation.execution.mode !== 'iterative') return operation.binding;
  const materializable = new Set(operation.execution.materializable);
  const keep = (entry) => !materializable.has(entry[0]);
  return deepFreeze({
    ...operation.binding,
    schema: deepFrozenClone({
      ...operation.binding.schema,
      properties: Object.fromEntries(
        Object.entries(operation.binding.schema.properties || {}).filter(keep),
      ),
      required: (operation.binding.schema.required || []).filter((name) => !materializable.has(name)),
    }),
    fields: deepFrozenClone(Object.fromEntries(Object.entries(operation.binding.fields || {}).filter(keep))),
    values: deepFrozenClone(Object.fromEntries(Object.entries(operation.binding.values || {}).filter(keep))),
    normalizers: deepFrozenClone(Object.fromEntries(
      Object.entries(operation.binding.normalizers || {}).filter(keep),
    )),
  });
}
function capabilityOperationMatches(capabilityId, operationId = null) {
  const requestedCapability = String(capabilityId || '').trim();
  const requestedOperation = operationId == null ? null : String(operationId || '').trim();
  return TOOL_REGISTRY.flatMap((tool) => tool.provides.flatMap((provided) => (
    provided.capability !== requestedCapability ? [] : provided.operations
      .filter((operation) => !requestedOperation || operation.id === requestedOperation)
      .map((operation) => ({ tool, provided, operation }))
  )));
}
export function bindCapability(handle, semanticInputValue = {}) {
  const binding = HANDLE_BINDINGS.get(handle);
  if (!binding) throw new TypeError('onbekende capabilityhandle');
  return bindInput(binding, semanticInputValue);
}
export function bindCapabilityInput(capabilityId, operationId, semanticInputValue = {}) {
  const matches = capabilityOperationMatches(capabilityId, operationId);
  if (matches.length !== 1) throw new TypeError('onbekende of ambigue capabilityoperation');
  return bindInput(matches[0].operation.binding, semanticInputValue);
}

function issuedSyntheticArgs(args, operation, turnNamespace) {
  if (operation.execution.mode !== 'iterative' || operation.execution.target?.boundary !== 'workspace') return args;
  const targetField = operation.execution.target.field;
  const physicalField = operation.binding.fields[targetField];
  const namespace = syntheticNamespaceName(turnNamespace);
  return Object.freeze({
    ...args,
    [physicalField]: canonicalWorkspacePath(path.join(
      SYNTHETIC_ARTIFACT_ROOT, namespace, path.basename(String(args[physicalField] || 'artifact.txt')),
    )),
  });
}

function bindSelfAttentionGoalReference(understanding, action, operation) {
  if (action?.capabilityId !== 'goal.portfolio.manage') return null;
  const issued = bindIssuedSelfAttentionGoalAction(understanding, action);
  if (!issued) {
    if (Object.hasOwn(action?.semanticInput || {}, 'doel_ref')) {
      throw new TypeError('unissued_self_attention_goal_ref');
    }
    return null;
  }
  const binding = parentSemanticBinding(operation);
  const args = Object.fromEntries(Object.entries(action.semanticInput || {}).map(([name, value]) => {
    if (name === 'doel_ref') return [name, value];
    const physical = binding.fields[name];
    if (!physical) throw new TypeError(`onbekend_semantisch_veld_${name}`);
    return [physical, binding.normalizers?.[name] === 'workspace_file'
      ? canonicalWorkspacePath(value)
      : Object.hasOwn(binding.values[name] || {}, value) ? binding.values[name][value] : value];
  }));
  if (binding.operationField && binding.action != null) {
    args[binding.operationField] = binding.action;
  }
  return { issued, args: Object.freeze(args) };
}

export function planCapabilityAction(action, {
  mode = 'chat', interactiveAuthority = 'ambient', fullAutonomy = false,
  synthetic = false, syntheticNamespace = null, turnId = null, effectPolicy = null,
  understanding = null, understandingSha256 = null,
} = {}) {
  if (!action || action.authorization !== 'allowed' || !['chat', 'autonomy'].includes(mode)) return null;
  const matches = capabilityOperationMatches(action.capabilityId, action.operationId);
  if (matches.length !== 1) return null;
  const { tool, provided, operation } = matches[0];
  const selfAttentionGoal = bindSelfAttentionGoalReference(understanding, action, operation);
  let args = selfAttentionGoal?.args
    || bindInput(parentSemanticBinding(operation), action.semanticInput || {});
  const semanticArgs = args;
  if (synthetic) args = issuedSyntheticArgs(args, operation, syntheticNamespace || turnId);
  const interactiveRank = INTERACTIVE_AUTHORITY.indexOf(interactiveAuthority);
  const requiredRank = INTERACTIVE_AUTHORITY.indexOf(operation.authority.interactive);
  const allowed = mode === 'autonomy'
    ? operation.authority.autonomy === 'bounded'
      || (fullAutonomy && operation.authority.autonomy === 'full')
    : interactiveRank >= 0 && requiredRank >= 0 && requiredRank <= interactiveRank;
  if (!allowed || (synthetic && !toolSyntheticAllowed(tool.name))
    || !toolAllowedByPolicy(tool.name, args, effectPolicy)) return null;
  const plan = deepFreeze({
    requirementId: String(action.requirementId || ''),
    capabilityId: provided.capability, operationId: operation.id,
    toolName: tool.name, args, effect: operation.effect,
    idempotency: operation.idempotency, proofFloor: operation.proofFloor,
    supportsDeferred: operation.supportsDeferred,
    execution: operation.execution,
    proofOperations: operation.execution.proofOperations,
    recoveryOperations: operation.execution.recoveryOperations,
  });
  const targetField = operation.execution.target?.field;
  const targetArg = targetField ? operation.binding.fields[targetField] : null;
  ISSUED_PLAN_METADATA.set(plan, {
    kind: 'parent', tool, provided, operation,
    ...(selfAttentionGoal ? { selfAttentionGoal: Object.freeze({
      action, binding: selfAttentionGoal.issued, opaqueArgs: plan.args,
    }) } : {}),
    issuer: deepFrozenClone({
      mode, interactiveAuthority, fullAutonomy, synthetic, effectPolicy,
      understandingSha256: String(understandingSha256 || '').trim() || null,
    }),
    semanticArgs,
    session: operation.execution.mode === 'iterative' ? {
      targetPath: canonicalWorkspacePath(args[targetArg]),
      artifactPaths: new Set([canonicalWorkspacePath(args[targetArg])]),
      handles: null,
    } : null,
  });
  return plan;
}

export function bindExplorationTerminalContributionExecution(claim, expectedArgs, isCurrent) {
  if (!claim || typeof claim !== 'object'
    || !expectedArgs || typeof expectedArgs !== 'object'
    || !Object.isFrozen(expectedArgs)
    || typeof isCurrent !== 'function') return null;
  const issued = Object.freeze({});
  ISSUED_EXPLORATION_TERMINAL_EXECUTIONS.set(issued, Object.freeze({
    claim, expectedArgs, isCurrent,
  }));
  return issued;
}

export function resolveIssuedSelfAttentionGoalPlan(plan, now = Date.now()) {
  const metadata = ISSUED_PLAN_METADATA.get(plan);
  const goal = metadata?.selfAttentionGoal;
  if (!goal) return null;
  if (metadata.kind !== 'parent' || metadata.tool.name !== 'doel_bijhouden'
    || metadata.provided.capability !== 'goal.portfolio.manage'
    || metadata.operation.id !== plan.operationId
    || plan.capabilityId !== 'goal.portfolio.manage'
    || goal.action.capabilityId !== plan.capabilityId
    || goal.action.operationId !== plan.operationId
    || goal.action.requirementId !== plan.requirementId
    || goal.opaqueArgs !== plan.args
    || plan.args.doel_ref !== goal.action.semanticInput?.doel_ref
    || plan.args.verwachte_revisie !== goal.action.semanticInput?.verwachte_revisie
    || Object.hasOwn(plan.args, 'doel_sleutel')) {
    throw new TypeError('invalid_self_attention_goal_plan');
  }
  const binding = consumeIssuedSelfAttentionGoalAction(goal.binding, now);
  if (!binding || binding.operationId !== plan.operationId
    || binding.requirementId !== plan.requirementId
    || binding.goalRef !== plan.args.doel_ref
    || binding.goalRevision !== plan.args.verwachte_revisie) {
    throw new TypeError('invalid_or_expired_self_attention_goal_plan');
  }
  const { doel_ref: _goalRef, ...modelArgs } = plan.args;
  const args = Object.freeze({
    ...modelArgs,
    doel_sleutel: binding.goalKey,
    verwacht_doel_id: binding.goalId,
  });
  const provenance = Object.freeze({
    kind: 'self_attention_goal_ref', source: 'host_receipt',
    modelField: 'doel_ref', resolved: true,
  });
  RESOLVED_SELF_ATTENTION_GOAL_ARGS.set(args, Object.freeze({
    ...binding, plan, args, provenance,
  }));
  return Object.freeze({ args, provenance });
}

function explicitHomeHistoryWindow(question, now) {
  const text = String(question || '');
  const time = text.match(/\b(?:om|rond)\s*(\d{1,2})[.:](\d{2})\b/iu);
  const daysBack = /\beergisteren\b/iu.test(text) ? 2
    : /\bgisteren\b/iu.test(text) ? 1
      : /\b(?:vandaag|vannacht|vanochtend|vanmiddag|vanavond)\b/iu.test(text) ? 0 : null;
  const hour = Number(time?.[1]), minute = Number(time?.[2]);
  if (daysBack == null || !Number.isInteger(hour) || hour < 0 || hour > 23
    || !Number.isInteger(minute) || minute < 0 || minute > 59) return null;
  // config.js bindt process.env.TZ aan config.tz; lokale kalenderbewerkingen
  // volgen daardoor ook over de zomer-/wintertijdgrens de huis-tijdzone.
  const target = new Date(now);
  if (!Number.isFinite(target.getTime())) return null;
  target.setDate(target.getDate() - daysBack);
  target.setHours(hour, minute, 0, 0);
  return {
    start: new Date(target.getTime() - 30 * 60_000).toISOString(),
    end: new Date(target.getTime() + 30 * 60_000).toISOString(),
  };
}

export function bindHomeContextRecoveryPlan(parentPlan, recorded, now = new Date()) {
  const metadata = ISSUED_PLAN_METADATA.get(parentPlan), binding = recorded?.semanticBinding;
  const result = typedReceiptActionResult(recorded, 'huis_status');
  const data = result?.data;
  const entityIds = [...new Set((Array.isArray(data?.entities) ? data.entities : [])
    .map((item) => String(item?.entity_id || '').trim()))];
  if (metadata?.kind !== 'parent' || metadata.tool.name !== 'huis_status'
    || metadata.provided.capability !== 'home.context.read' || metadata.operation.id !== 'read'
    || parentPlan.args.historie !== true || canonicalJson(recorded?.args) !== canonicalJson(parentPlan.args)
    || recorded?.name !== 'huis_status' || recorded?.ok !== true || recorded?.verified !== true
    || recorded?.partial !== true || recorded?.changed === true || recorded?.effect_uncertain === true
    || binding?.understandingSha256 !== metadata.issuer.understandingSha256
    || binding?.requirementId !== parentPlan.requirementId
    || binding?.capabilityId !== 'home.context.read' || binding?.operationId !== 'read'
    || data?.history_required !== true
    || !entityIds.length || entityIds.length >= 24
    || entityIds.some((id) => !/^[a-z0-9_]+\.[a-z0-9_]+$/u.test(id))) return null;
  const args = Object.freeze({ ...parentPlan.args }), end = new Date(now);
  const requestedWindow = explicitHomeHistoryWindow(parentPlan.args.vraag, end);
  const plan = deepFreeze({ ...parentPlan, args });
  HOME_CONTEXT_RECOVERY_ARGS.set(args, Object.freeze({
    plan, entityIds: Object.freeze(entityIds),
    start: requestedWindow?.start || new Date(end.getTime() - 60 * 60_000).toISOString(),
    end: requestedWindow?.end || end.toISOString(),
  }));
  ISSUED_HOME_CONTEXT_RECOVERY_ARGS.add(args);
  return plan;
}

function priorHomeContinuationWordsMatch(userText, prior) {
  const text = normalize(userText);
  if (!priorActionResultFollowup(text)) return false;
  const queryWords = words(text);
  const priorWords = words([
    prior.requestedTarget,
    prior.requester,
    prior.entityId,
    prior.name,
  ].filter(Boolean).join(' '));
  const resultWords = new Set([
    'gelukt', 'gedaan', 'uitgevoerd', 'voltooid', 'gewerkt', 'werkt', 'lukte',
    'vorige', 'laatste', 'opdracht', 'actie', 'handeling', 'verzoek', 'staat',
    'stond', 'blijft', 'bleef', 'aan', 'uit', 'open', 'dicht', 'actief', 'inactief',
    'en', 'maar', 'of', 'nou', 'dan', 'ja', 'nee', 'is', 'was', 'heb', 'heeft',
    'had', 'je', 'jij', 'eva', 'dat', 'dit', 'het', 'hem', 'haar', 'hij', 'zij',
    'ze', 'die', 'deze', 'nog', 'nu', 'al', 'wel', 'niet', 'ook', 'toch', 'mijn',
    'de', 'een', 'met',
  ]);
  const newSubjectWords = queryWords.filter((word) => !resultWords.has(word)
    && !priorWords.some((priorWord) => word === priorWord
      || (word.length >= 4 && priorWord.length >= 4
        && (word.includes(priorWord) || priorWord.includes(word)))));
  return newSubjectWords.length === 0;
}

function priorHomeExecutionResultValid(binding, result, data, complete, boundedPartialVerified) {
  return binding?.capabilityId === 'home.device.control' && binding?.operationId === 'set'
    && result?.contract === 'ActionResult/v1' && result?.tool === 'bedien_huis'
    && result?.ok === true && result?.verified === true && result?.effect_uncertain !== true
    && /^[a-z0-9_]+\.[a-z0-9_]+$/u.test(String(data?.entity_id || ''))
    && typeof data?.after === 'string' && Boolean(data.after.trim())
    && (complete || boundedPartialVerified);
}

function boundedPriorHomeSummary(execution, data, boundedPartialVerified) {
  if (!boundedPartialVerified) return null;
  const assessment = data.evidence.assessment;
  const usedNames = new Set();
  const unavailableNames = assessment.unavailable.map((member) => {
    const entityId = String(member.entity_id);
    const friendly = clean(member.name || '', 96);
    let name = friendly || entityId;
    const key = name.toLocaleLowerCase('nl-NL');
    if (usedNames.has(key)) name = entityId;
    usedNames.add(name.toLocaleLowerCase('nl-NL'));
    return name;
  });
  return Object.freeze({
    contract: 'BoundedHomePartialContinuation/v1',
    source_completion_record_sha256: String(execution.completion_record_sha256),
    entity_id: String(data.entity_id),
    expected_state: String(assessment.expected),
    reachable_count: assessment.reachable.length,
    unavailable_count: assessment.unavailable.length,
    unavailable_names: Object.freeze(unavailableNames),
  });
}

function verifiedPriorHomeExecution(row, { conversationId, requester, turnId }) {
  let execution;
  try { execution = validateExecutionRecord(row); } catch { return null; }
  if (execution?.status !== 'done' || execution.tool !== 'bedien_huis'
    || execution.turn_id !== turnId) return null;
  let args, binding, result;
  try {
    args = JSON.parse(execution.args);
    binding = JSON.parse(execution.semantic_binding);
    result = JSON.parse(execution.output);
  } catch { return null; }
  const data = result?.data;
  const boundedPartialVerified = boundedVerifiedHomePartialEffect({
    name: execution.tool,
    args,
    output: execution.output,
    ok: result?.ok,
    verified: result?.verified,
    changed: result?.changed,
    partial: result?.partial,
    needs_followup: result?.needs_followup,
    failure_class: result?.failure_class,
    retryable: result?.retryable,
    effect_uncertain: result?.effect_uncertain,
  }, { ...args, requester });
  const complete = result?.partial !== true && result?.needs_followup !== true
    && validHomeRequesterTargetBinding(data?.target_binding, {
      requestedTarget: args?.doel,
      requester,
      resolvedEntityId: data?.entity_id,
    });
  if (!priorHomeExecutionResultValid(
    binding, result, data, complete, boundedPartialVerified,
  )) return null;
  const understandingRow = /** @type {any} */ (db.prepare(`SELECT meta FROM main.timeline
    WHERE kind='turn_understanding' AND json_valid(meta)
      AND json_extract(meta,'$.conversationId')=?
      AND json_extract(meta,'$.turnId')=?
    ORDER BY id DESC LIMIT 1`).get(conversationId, turnId));
  let priorUnderstanding;
  try { priorUnderstanding = JSON.parse(understandingRow?.meta || '{}')?.understanding; } catch { return null; }
  if (priorUnderstanding?.sourceSha256 !== binding?.understandingSha256) return null;
  const boundedPartial = boundedPriorHomeSummary(execution, data, boundedPartialVerified);
  return Object.freeze({
    executionKey: String(execution.execution_key || ''),
    completionRecordSha256: String(execution.completion_record_sha256 || ''),
    turnId,
    requester: String(requester || ''),
    requestedTarget: String(args.doel || ''),
    action: String(args.actie || ''),
    entityId: String(data.entity_id),
    name: clean(data.name || data.entity_id, 96),
    priorState: String(data.after),
    priorChanged: result.changed === true,
    boundedPartial,
    targetBindingSha256: String(data.target_binding?.binding_sha256 || ''),
    understandingSha256: String(binding.understandingSha256 || ''),
    requirementId: String(binding.requirementId || ''),
    observedAt: String(execution.finished_ts || ''),
  });
}

function immediatePriorVerifiedHome({ conversationId, requester, userText, executions = [] }) {
  const latestOutput = /** @type {any} */ (db.prepare(`SELECT id,meta FROM main.timeline
    WHERE kind='chat_out' AND json_valid(meta)
      AND json_extract(meta,'$.conversationId')=?
    ORDER BY id DESC LIMIT 1`).get(conversationId));
  let outputMeta;
  try { outputMeta = JSON.parse(latestOutput?.meta || '{}'); } catch { return null; }
  const priorTurnId = String(outputMeta?.turnId || '');
  const laterInbound = /** @type {any} */ (db.prepare(`SELECT COUNT(*) AS count FROM main.timeline
    WHERE id>? AND kind='chat_in' AND json_valid(meta)
      AND json_extract(meta,'$.conversationId')=?`).get(
    Number(latestOutput?.id || 0), conversationId,
  ));
  const laterInboundCount = Number(laterInbound?.count || 0);
  if (!priorTurnId || laterInboundCount !== 0) return null;
  const candidates = (Array.isArray(executions) ? executions : []).slice(0, 20)
    .map((row) => verifiedPriorHomeExecution(row, {
      conversationId, requester, turnId: priorTurnId,
    })).filter(Boolean);
  if (candidates.length !== 1) return null;
  const prior = candidates[0];
  return priorHomeContinuationWordsMatch(userText, prior) ? prior : null;
}

export function priorVerifiedHomeContinuationResourceReceipt({
  conversationId, requester, userText, executions = [],
} = /** @type {any} */ ({})) {
  const prior = immediatePriorVerifiedHome({
    conversationId, requester, userText, executions,
  });
  if (!prior || !/^[a-f0-9]{64}$/u.test(prior.understandingSha256)
    || !/^[a-f0-9]{64}$/u.test(prior.completionRecordSha256)
    || !/^r(?:[1-9]|1[0-2])$/u.test(prior.requirementId)
    || !Number.isFinite(Date.parse(prior.observedAt))) return null;
  return Object.freeze({
    capabilityId: 'home.entity.read', operationId: 'read', field: 'entityId',
    value: prior.entityId, sourceTurnId: prior.turnId,
    understandingSha256: prior.understandingSha256,
    requirementId: prior.requirementId,
    receiptSha256: prior.completionRecordSha256,
    observedAt: prior.observedAt,
  });
}

/**
 * Bind een leesplan uit een echte vervolgvraag eenmalig aan het geverifieerde
 * huisdoel van exact de direct voorafgaande, afgeleverde beurt. Het model kan
 * deze private plan/args-identiteit niet zelf maken of hergebruiken.
 */
export function bindPriorVerifiedHomeContextPlan(parentPlan, {
  conversationId, requester, userText, understanding, executions = [],
} = /** @type {any} */ ({})) {
  const metadata = ISSUED_PLAN_METADATA.get(parentPlan);
  if (metadata?.kind !== 'parent' || metadata.issuer?.mode !== 'chat'
    || metadata.issuer.understandingSha256 !== understanding?.sha256
    || !isIssuedTurnUnderstanding(understanding)
    || !['continue', 'correct'].includes(understanding.dialogueAct)
    || parentPlan?.operationId !== 'read'
    || !((parentPlan?.capabilityId === 'home.context.read'
      && parentPlan?.toolName === 'huis_status')
      || (parentPlan?.capabilityId === 'home.entity.read'
        && parentPlan?.toolName === 'ha_lees'))
    || !understanding.firstDecision.requirementIds.includes(parentPlan.requirementId)
    || typeof conversationId !== 'string' || !conversationId.trim()
    || typeof requester !== 'string' || !requester.trim()) return parentPlan;
  const prior = immediatePriorVerifiedHome({
    conversationId, requester, userText, executions,
  });
  if (!prior || (parentPlan.capabilityId === 'home.entity.read'
    && (Object.keys(parentPlan.args || {}).sort().join(',') !== 'entity_id'
      || parentPlan.args.entity_id !== prior.entityId))) return parentPlan;
  const args = Object.freeze({ ...parentPlan.args });
  const plan = deepFreeze({ ...parentPlan, args });
  PRIOR_HOME_CONTEXT_ARGS.set(args, Object.freeze({ plan, prior }));
  ISSUED_PRIOR_HOME_CONTEXT_ARGS.add(args);
  ISSUED_PLAN_METADATA.set(plan, metadata);
  return plan;
}

export function bindContinuityContextPlan(parentPlan, understanding) {
  const metadata = ISSUED_PLAN_METADATA.get(parentPlan);
  const continuity = continuityWatchForUnderstanding(understanding);
  const monitoring = isIssuedTurnUnderstanding(understanding)
    && understanding.monitoring?.requested === true ? understanding.monitoring : null;
  const continuityCurrent = continuity?.contract === 'ContinuityWatch/v1'
    && parentPlan.requirementId === continuity.sourceReadRequirementId;
  const monitoringCurrent = monitoring?.sourceDiscoveryRequirementId
    && parentPlan.requirementId === monitoring.sourceDiscoveryRequirementId;
  if (metadata?.kind !== 'parent' || (!continuityCurrent && !monitoringCurrent)
    || metadata.issuer.understandingSha256 !== understanding?.sha256
    || metadata.provided.capability !== 'home.context.read' || metadata.operation.id !== 'read'
  ) return parentPlan;
  const { historie: _history, ...currentArgs } = parentPlan.args;
  const args = Object.freeze(currentArgs);
  if (continuityCurrent) CONTINUITY_HOME_CONTEXT_ARGS.add(args);
  const plan = deepFreeze({ ...parentPlan, args });
  ISSUED_PLAN_METADATA.set(plan, metadata);
  return plan;
}

export function bindSignalAssessmentPlan(parentPlan, understanding) {
  const metadata = ISSUED_PLAN_METADATA.get(parentPlan);
  const issued = signalBatchForUnderstanding(understanding);
  if (metadata?.kind !== 'parent' || metadata.issuer.mode !== 'autonomy'
    || metadata.issuer.understandingSha256 !== understanding?.sha256
    || metadata.provided.capability !== 'signal.assess' || metadata.operation.id !== 'assess'
    || !validSignalBatch(issued) || !issued.signals.length
    || !understanding.firstDecision.requirementIds.includes(parentPlan.requirementId)) return null;
  const args = Object.freeze({ ...parentPlan.args });
  const selected = new Set(understanding.firstDecision.requirementIds);
  SIGNAL_ASSESSMENT_ARGS.set(args, Object.freeze({
    batch: createSignalBatch(issued.signals), understandingSha256: understanding.sha256,
    requirements: understanding.actions.filter((action) => action.requirementId !== parentPlan.requirementId
      && selected.has(action.requirementId)).map((action) => Object.freeze({
      requirementId: action.requirementId, capabilityId: action.capabilityId,
      operationId: action.operationId, effect: action.effect,
    })),
  }));
  const plan = deepFreeze({ ...parentPlan, args });
  ISSUED_PLAN_METADATA.set(plan, metadata);
  return plan;
}

function executionOperationAllowed(match, issuer, args) {
  const { tool, operation } = match;
  const interactiveRank = INTERACTIVE_AUTHORITY.indexOf(issuer.interactiveAuthority);
  const requiredRank = INTERACTIVE_AUTHORITY.indexOf(operation.authority.interactive);
  const allowed = issuer.mode === 'autonomy'
    ? operation.authority.autonomy === 'bounded'
      || (issuer.fullAutonomy && operation.authority.autonomy === 'full')
    : interactiveRank >= 0 && requiredRank >= 0 && requiredRank <= interactiveRank;
  return allowed && (!issuer.synthetic || toolSyntheticAllowed(tool.name))
    && toolAllowedByPolicy(tool.name, args, issuer.effectPolicy);
}
function issueExecutionHandle(metadata, reference, lane) {
  const matches = capabilityOperationMatches(reference.capabilityId, reference.operationId);
  if (matches.length !== 1) throw new TypeError('execution-ref is onbekend of ambigu');
  const descriptions = {
    write: 'Materialiseer volledige inhoud in het begrensde doel- of proefbestand.',
    run: 'Voer een geschreven programma of proef uit en verzamel het echte resultaat.',
    readback: 'Lees een geschreven artefact exact terug als inhoudsbewijs.',
    inspect: 'Inspecteer een geschreven artefact gericht om een concrete fout te vinden.',
    repair: 'Materialiseer een verbeterde versie of een begrensd proefartefact.',
    retest: 'Voer de gerepareerde versie of proef opnieuw uit.',
  };
  const handle = Object.freeze({
    contract: 'CapabilityExecutionHandle/v1',
    alias: `execution_${lane}_${reference.type}`,
    description: descriptions[reference.type] || `Voer de getypeerde ${reference.type}-stap uit.`,
    parameters: matches[0].operation.input,
  });
  EXECUTION_HANDLE_METADATA.set(handle, { ...metadata, reference, lane, match: matches[0] });
  return handle;
}

export function executionHandlesForPlan(parentPlan) {
  const metadata = ISSUED_PLAN_METADATA.get(parentPlan);
  if (metadata?.kind !== 'parent') throw new TypeError('plan is niet door de capability-issuer uitgegeven');
  if (metadata.operation.execution.mode !== 'iterative' || !metadata.session) {
    throw new TypeError('plan heeft geen iteratieve uitvoeringsruimte');
  }
  if (metadata.session.handles) return metadata.session.handles;
  const shared = { parentPlan, parentMetadata: metadata, session: metadata.session };
  const execution = metadata.operation.execution;
  metadata.session.handles = Object.freeze({
    contract: 'CapabilityExecutionHandles/v1', mode: execution.mode,
    materializable: execution.materializable,
    target: Object.freeze({
      path: workspaceDisplayPath(metadata.session.targetPath),
      artifactLimit: execution.target.artifactLimit,
      sameDirectory: execution.target.sameDirectory,
    }),
    proof: Object.freeze(execution.proofOperations.map((reference) => (
      issueExecutionHandle(shared, reference, 'proof')
    ))),
    recovery: Object.freeze(execution.recoveryOperations.map((reference) => (
      issueExecutionHandle(shared, reference, 'recovery')
    ))),
  });
  return metadata.session.handles;
}

function safeWorkspaceReadDependency(absolute) {
  if (!fs.existsSync(absolute)) throw new TypeError('programma-invoer bestaat niet');
  const realWorkspace = fs.realpathSync(WRITABLE_ROOT);
  const real = fs.realpathSync(absolute);
  if (!pathInside(realWorkspace, real)) {
    throw new TypeError('programma-invoer wijst buiten Eva haar werkruimte');
  }
  if (!fs.statSync(real).isFile()) throw new TypeError('programma-invoer moet een regulier bestand zijn');
  if (sensitiveReadablePath(real)) throw new TypeError('programma-invoer bevat afgeschermde gegevens');
  return real;
}

function classifyProgramWorkspaceArguments(operation, args, workingDirectory) {
  const policy = operation.execution?.fileArguments;
  if (!policy) throw new TypeError('programma-operation mist declaratieve bestandsrollen');
  const programArgument = operation.binding.fields[policy.programField];
  const argumentsArgument = operation.binding.fields[policy.argumentsField];
  const runtime = path.basename(String(args[programArgument] || '').trim()).toLowerCase();
  const materializedExtensions = new Set(policy.materializedByProgram[runtime] || []);
  if (!materializedExtensions.size) {
    throw new TypeError('programma-runtime heeft geen declaratieve artefactroute');
  }
  const dependencies = new Set(policy.readDependencies);
  const rawArguments = Array.isArray(args[argumentsArgument]) ? args[argumentsArgument].map(String) : [];
  const entrypoint = rawArguments.find((argument) => !argument.startsWith('-'));
  if (!entrypoint || !materializedExtensions.has(path.extname(entrypoint).toLowerCase())) {
    throw new TypeError('programma-entrypoint is geen gedeclareerd schrijfartefact');
  }
  const materializedArtifacts = [];
  const readDependencies = [];
  for (const argument of rawArguments) {
    const portable = argument.replaceAll('\\', '/');
    if (portable.split('/').includes('..')) {
      throw new TypeError('padtraversal is niet toegestaan in programma-argumenten');
    }
    const extension = path.extname(argument).toLowerCase();
    if (argument.startsWith('-')
      && (materializedExtensions.has(extension) || dependencies.has(extension))) {
      throw new TypeError('bestandsopties vereisen een afzonderlijk gedeclareerd padargument');
    }
    if (argument.startsWith('-')) continue;
    const role = materializedExtensions.has(extension) ? 'materialized'
      : dependencies.has(extension) ? 'read_dependency' : null;
    if (!role) {
      const unresolved = path.resolve(workingDirectory, portable);
      if (fs.existsSync(unresolved)) {
        canonicalWorkspacePath(unresolved);
        throw new TypeError('bestaand programma-bestandsargument heeft geen declaratieve rol');
      }
      continue;
    }
    const absolute = canonicalWorkspacePath(path.resolve(workingDirectory, portable));
    if (role === 'materialized') materializedArtifacts.push(absolute);
    else readDependencies.push(safeWorkspaceReadDependency(absolute));
  }
  if (!materializedArtifacts.length) {
    throw new TypeError('programma-entrypoint is geen gedeclareerd schrijfartefact');
  }
  return Object.freeze({
    materializedArtifacts: Object.freeze(materializedArtifacts),
    readDependencies: Object.freeze(readDependencies),
  });
}

export function executionObligationForPlan(parentPlan, candidatePlan, { receiptContext = [] } = {}) {
  const parentMetadata = ISSUED_PLAN_METADATA.get(parentPlan);
  const candidateMetadata = ISSUED_PLAN_METADATA.get(candidatePlan);
  if (parentMetadata?.kind !== 'parent' || !parentMetadata.session
    || parentMetadata.operation.execution.mode !== 'iterative') {
    throw new TypeError('execution-verplichting mist een authentiek iteratief parentplan');
  }
  if (candidateMetadata?.kind !== 'parent'
    || candidateMetadata.issuer.understandingSha256
      !== parentMetadata.issuer.understandingSha256) {
    throw new TypeError('execution-verplichting hoort niet bij dezelfde immutable understanding');
  }
  const reference = parentMetadata.operation.execution.proofOperations.find((item) => (
    item.capabilityId === candidatePlan.capabilityId
    && item.operationId === candidatePlan.operationId
  ));
  if (!reference) return null;
  const goalDirectory = path.dirname(parentMetadata.session.targetPath);
  const synthetic = parentMetadata.issuer.synthetic === true;
  const key = `${candidatePlan.capabilityId}:${candidatePlan.operationId}`;
  const verifiedArtifacts = sessionWriteArtifacts(parentMetadata.session, receiptContext);
  let expectedInput = candidateMetadata.semanticArgs || candidatePlan.args;
  let compatible = false;
  if (key === 'workspace.file.write:write') {
    const requested = canonicalWorkspacePath(candidatePlan.args.pad);
    const artifact = synthetic ? path.join(goalDirectory, path.basename(requested)) : requested;
    compatible = path.dirname(artifact) === goalDirectory
      && (parentMetadata.session.artifactPaths.has(artifact)
        || parentMetadata.session.artifactPaths.size
          < parentMetadata.operation.execution.target.artifactLimit);
  } else if (key === 'workspace.file.read:read') {
    if (hasUnresolvedReference(expectedInput.pad)) {
      const readArtifacts = verifiedSessionReadArtifacts(parentPlan, parentMetadata, receiptContext);
      const unread = [...verifiedArtifacts].filter((artifact) => !readArtifacts.has(artifact));
      if (unread.length === 1) {
        expectedInput = Object.freeze({ ...expectedInput, pad: workspaceDisplayPath(unread[0]) });
        compatible = true;
      }
    } else {
      const requested = canonicalWorkspacePath(candidatePlan.args.pad);
      const artifact = synthetic ? path.join(goalDirectory, path.basename(requested)) : requested;
      compatible = verifiedArtifacts.has(artifact);
    }
  } else if (key === 'workspace.program.execute:execute') {
    const requestedDirectory = canonicalWorkspacePath(
      candidatePlan.args.werkmap || goalDirectory, { directory: true },
    );
    const workingDirectory = synthetic ? goalDirectory : requestedDirectory;
    try {
      const classified = classifyProgramWorkspaceArguments(
        candidateMetadata.operation, candidatePlan.args, workingDirectory,
      );
      compatible = workingDirectory === goalDirectory
        && classified.materializedArtifacts.every((artifact) => verifiedArtifacts.has(artifact));
    } catch {
      compatible = false;
    }
  }
  if (!compatible) return null;
  const handle = executionHandlesForPlan(parentPlan).proof.find((item) => (
    item.alias === `execution_proof_${reference.type}`
  ));
  if (!handle) throw new TypeError('execution-verplichting heeft geen uitgegeven proof-handle');
  const materializableFields = candidateMetadata.operation.execution.mode === 'iterative'
    ? candidateMetadata.operation.execution.materializable.map((field) => (
      candidateMetadata.operation.binding.fields[field]
    )) : [];
  const obligation = Object.freeze({
    handle,
    descriptor: Object.freeze({
      alias: handle.alias,
      expectedInput,
      materializableFields: Object.freeze(materializableFields),
    }),
    expectedPlan: candidatePlan,
  });
  EXECUTION_OBLIGATION_METADATA.set(obligation, {
    parentPlan, candidatePlan, handle, expectedInput,
  });
  return obligation;
}

function verifiedExecutionChildReceipt(entry, parentPlan, parentMetadata, understandingSha256) {
  const childMetadata = ISSUED_PLAN_METADATA.get(entry?.plan);
  const result = entry?.result;
  if (childMetadata?.kind !== 'child' || childMetadata.session !== parentMetadata.session
    || result?.name !== childMetadata.plan.toolName
    || canonicalJson(result?.args) !== canonicalJson(childMetadata.plan.args)
    || !verifiedCompleteReceipt(result)) return null;
  const binding = result.semanticBinding;
  if (String(binding?.understandingSha256 || '') !== understandingSha256
    || String(binding?.requirementId || '') !== String(parentPlan.requirementId || '')
    || binding?.capabilityId !== childMetadata.plan.capabilityId
    || binding?.operationId !== childMetadata.plan.operationId) return null;
  const typed = typedReceiptActionResult(result, childMetadata.plan.toolName);
  return verifiedCompleteReceipt(typed) ? childMetadata : null;
}

function verifiedSessionReadArtifacts(parentPlan, parentMetadata, receiptContext) {
  const read = new Set();
  for (const entry of Array.isArray(receiptContext) ? receiptContext : []) {
    const child = verifiedExecutionChildReceipt(
      entry, parentPlan, parentMetadata, parentMetadata.issuer.understandingSha256,
    );
    if (child?.plan.capabilityId === 'workspace.file.read'
      && child.plan.operationId === 'read') read.add(canonicalWorkspacePath(child.plan.args.pad));
  }
  return read;
}

function childSatisfiesIssuedParent(childMetadata, candidate, candidateMetadata) {
  const child = childMetadata.plan;
  if (child.capabilityId !== candidate.capabilityId
    || child.operationId !== candidate.operationId
    || child.toolName !== candidate.toolName) return false;
  if (candidateMetadata.operation.execution.mode !== 'iterative') {
    return canonicalJson(child.args) === canonicalJson(candidate.args);
  }
  const materializedArgs = new Set(candidateMetadata.operation.execution.materializable
    .map((field) => candidateMetadata.operation.binding.fields[field]));
  const parentKeys = new Set(Object.keys(candidate.args));
  if (Object.keys(child.args).some((key) => !parentKeys.has(key) && !materializedArgs.has(key))) {
    return false;
  }
  return Object.entries(candidate.args).every(([key, value]) => (
    canonicalJson(child.args[key]) === canonicalJson(value)
  ));
}

export function executionGroupConsumptions({
  parentPlan = null, candidatePlans = [], receiptContext = [],
  understandingSha256 = '', requirementIds = [],
} = {}) {
  const parentMetadata = ISSUED_PLAN_METADATA.get(parentPlan);
  const understanding = String(understandingSha256 || '').trim();
  if (parentMetadata?.kind !== 'parent' || !parentMetadata.session
    || parentMetadata.operation.execution.mode !== 'iterative') {
    throw new TypeError('execution-group mist een authentiek iteratief parentplan');
  }
  if (!understanding) throw new TypeError('execution-group mist immutable understandingbinding');
  if (parentMetadata.issuer.understandingSha256 !== understanding) {
    throw new TypeError('execution-group parent hoort niet bij deze immutable understanding');
  }
  const selected = new Set((Array.isArray(requirementIds) ? requirementIds : [])
    .map((value) => String(value || '').trim()).filter(Boolean));
  if (!selected.has(String(parentPlan.requirementId || ''))) {
    throw new TypeError('execution-group parent valt buiten de geselecteerde requirements');
  }
  const receipts = (Array.isArray(receiptContext) ? receiptContext : [])
    .map((entry, receiptIndex) => ({
      entry,
      receiptIndex,
      childMetadata: verifiedExecutionChildReceipt(
        entry, parentPlan, parentMetadata, understanding,
      ),
    }))
    .filter(({ childMetadata }) => childMetadata);
  const usedReceipts = new Set();
  const consumptions = [];
  let lastReceiptIndex = -1;
  for (const candidate of Array.isArray(candidatePlans) ? candidatePlans : []) {
    if (candidate === parentPlan) continue;
    const candidateMetadata = ISSUED_PLAN_METADATA.get(candidate);
    const requirementId = String(candidate?.requirementId || '');
    if (candidateMetadata?.kind !== 'parent'
      || candidateMetadata.issuer.understandingSha256 !== understanding
      || !selected.has(requirementId)) continue;
    const matched = receipts.find(({ receiptIndex, childMetadata }) => {
      if (usedReceipts.has(receiptIndex) || receiptIndex <= lastReceiptIndex) return false;
      if (childMetadata.expectedPlan && childMetadata.expectedPlan !== candidate) return false;
      const executionCandidate = childMetadata.expectedPlan === candidate
        && childMetadata.expectedExecutionArgs
        ? { ...candidate, args: childMetadata.expectedExecutionArgs }
        : candidate;
      return childSatisfiesIssuedParent(childMetadata, executionCandidate, candidateMetadata);
    });
    if (!matched) break;
    usedReceipts.add(matched.receiptIndex);
    lastReceiptIndex = matched.receiptIndex;
    consumptions.push(Object.freeze({
      contract: 'ExecutionRequirementConsumption/v1',
      understandingSha256: understanding,
      parentRequirementId: String(parentPlan.requirementId || ''),
      requirementId,
      capabilityId: candidate.capabilityId,
      operationId: candidate.operationId,
      receiptIndex: matched.receiptIndex,
      recorded: matched.entry.result,
    }));
  }
  return Object.freeze(consumptions);
}

function typedReceiptActionResult(result, expectedTool) {
  if (result?.contract === 'ActionResult/v1' && result.tool === expectedTool) return result;
  try {
    const parsed = JSON.parse(String(result?.output || ''));
    return parsed?.contract === 'ActionResult/v1' && parsed.tool === expectedTool ? parsed : null;
  } catch { return null; }
}
function sessionWriteArtifacts(session, receiptContext, { allowUncertain = false } = {}) {
  const paths = new Set();
  for (const entry of Array.isArray(receiptContext) ? receiptContext : []) {
    const childMetadata = ISSUED_PLAN_METADATA.get(entry?.plan);
    const result = entry?.result;
    if (childMetadata?.kind !== 'child' || childMetadata.session !== session
      || childMetadata.plan.capabilityId !== 'workspace.file.write'
      || childMetadata.plan.operationId !== 'write'
      || ![result?.name, result?.tool].includes(childMetadata.plan.toolName)
      || canonicalJson(result?.args) !== canonicalJson(childMetadata.plan.args)) continue;
    const typed = typedReceiptActionResult(result, childMetadata.plan.toolName);
    const verified = verifiedCompleteReceipt(result);
    const uncertain = allowUncertain && typed?.ok === false && typed.verified === false
      && typed.effect_uncertain === true;
    if (!verified && !uncertain) continue;
    paths.add(canonicalWorkspacePath(childMetadata.plan.args.pad));
  }
  return paths;
}
function relatedWorkspaceArgs(handleMetadata, args, receiptContext) {
  const { session, match } = handleMetadata;
  const goalDirectory = path.dirname(session.targetPath);
  const synthetic = handleMetadata.parentMetadata.issuer.synthetic === true;
  const key = `${match.provided.capability}:${match.operation.id}`;
  if (key === 'workspace.file.write:write') {
    const requestedPath = canonicalWorkspacePath(args.pad);
    const artifactPath = synthetic ? path.join(goalDirectory, path.basename(requestedPath)) : requestedPath;
    if (path.dirname(artifactPath) !== goalDirectory) {
      throw new TypeError('extra werkartefact moet in dezelfde doelmap blijven');
    }
    if (!session.artifactPaths.has(artifactPath)
      && session.artifactPaths.size >= handleMetadata.parentMetadata.operation.execution.target.artifactLimit) {
      throw new TypeError('artefactlimiet van het uitgegeven plan is bereikt');
    }
    session.artifactPaths.add(artifactPath);
    return Object.freeze({ ...args, pad: artifactPath });
  }
  const uncertainInspect = key === 'workspace.file.read:read'
    && handleMetadata.lane === 'recovery' && handleMetadata.reference.type === 'inspect';
  const artifacts = sessionWriteArtifacts(session, receiptContext, { allowUncertain: uncertainInspect });
  if (!artifacts.size) throw new TypeError('uitvoering of teruglezing vereist een geverifieerde schrijfreceipt');
  if (key === 'workspace.file.read:read') {
    const requestedPath = canonicalWorkspacePath(args.pad);
    const readPath = synthetic ? path.join(goalDirectory, path.basename(requestedPath)) : requestedPath;
    if (!artifacts.has(readPath)) throw new TypeError('teruglezing hoort niet bij een geschreven planartefact');
    return Object.freeze({ ...args, pad: readPath });
  }
  if (key === 'workspace.program.execute:execute') {
    const requestedDirectory = canonicalWorkspacePath(args.werkmap || goalDirectory, { directory: true });
    const workingDirectory = synthetic ? goalDirectory : requestedDirectory;
    if (workingDirectory !== goalDirectory) throw new TypeError('programmawerkmap hoort niet bij de doelmap');
    const classified = classifyProgramWorkspaceArguments(match.operation, args, workingDirectory);
    if (classified.materializedArtifacts.some((candidate) => !artifacts.has(candidate))) {
      throw new TypeError('programma-entrypoint heeft geen geverifieerde schrijfreceipt');
    }
    return Object.freeze({ ...args, werkmap: workingDirectory });
  }
  throw new TypeError('operationele relatie is niet gedeclareerd');
}

export function bindExecutionHandle(handle, semanticInputValue = {}, {
  receiptContext = [], expectedPlan: requestedExpectedPlan = null, expectedObligation = null,
} = {}) {
  const metadata = EXECUTION_HANDLE_METADATA.get(handle);
  if (!metadata || ISSUED_PLAN_METADATA.get(metadata.parentPlan) !== metadata.parentMetadata) {
    throw new TypeError('onbekende of vervalste execution-handle');
  }
  const bound = bindInput(metadata.match.operation.binding, semanticInputValue);
  const obligation = expectedObligation ? EXECUTION_OBLIGATION_METADATA.get(expectedObligation) : null;
  if (expectedObligation && (!obligation || obligation.parentPlan !== metadata.parentPlan
    || obligation.handle !== handle)) {
    throw new TypeError('onbekende of vervalste execution-verplichting');
  }
  const expectedPlan = obligation?.candidatePlan || requestedExpectedPlan;
  const expectedMetadata = expectedPlan ? ISSUED_PLAN_METADATA.get(expectedPlan) : null;
  const declaredExpectation = expectedPlan
    ? metadata.parentMetadata.operation.execution.proofOperations.some((reference) => (
      reference.capabilityId === expectedPlan.capabilityId
      && reference.operationId === expectedPlan.operationId
      && reference.type === metadata.reference.type
      && metadata.lane === 'proof'
    )) : true;
  const rawExpectedPlan = expectedPlan ? {
    ...expectedPlan,
    args: obligation?.expectedInput || expectedMetadata?.semanticArgs || expectedPlan.args,
  } : null;
  const rawChildPlan = {
    capabilityId: metadata.match.provided.capability,
    operationId: metadata.match.operation.id,
    toolName: metadata.match.tool.name,
    args: bound,
  };
  if (expectedPlan && (expectedMetadata?.kind !== 'parent'
    || expectedMetadata.issuer.understandingSha256
      !== metadata.parentMetadata.issuer.understandingSha256
    || !declaredExpectation
    || !childSatisfiesIssuedParent({ plan: rawChildPlan }, rawExpectedPlan, expectedMetadata))) {
    throw Object.assign(
      new TypeError('child-operation wijkt af van de uitgegeven siblingverplichting'),
      { code: 'EVA_EXECUTION_OBLIGATION_MISMATCH' },
    );
  }
  const args = relatedWorkspaceArgs(metadata, bound, receiptContext);
  if (!executionOperationAllowed(metadata.match, metadata.parentMetadata.issuer, args)) {
    throw new TypeError('child-operation valt buiten de uitgegeven authority');
  }
  const plan = deepFreeze({
    requirementId: metadata.parentPlan.requirementId,
    capabilityId: metadata.match.provided.capability, operationId: metadata.match.operation.id,
    toolName: metadata.match.tool.name, args, effect: metadata.match.operation.effect,
    idempotency: metadata.match.operation.idempotency, proofFloor: metadata.match.operation.proofFloor,
    supportsDeferred: metadata.match.operation.supportsDeferred,
    execution: { mode: 'direct', lane: metadata.lane, type: metadata.reference.type },
    proofOperations: [], recoveryOperations: [],
  });
  if (expectedPlan) {
    const executionExpectedPlan = { ...expectedPlan, args };
    if (!childSatisfiesIssuedParent({ plan }, executionExpectedPlan, expectedMetadata)) {
      throw Object.assign(
        new TypeError('child-operation wijkt af van de uitgegeven siblingverplichting'),
        { code: 'EVA_EXECUTION_OBLIGATION_MISMATCH' },
      );
    }
  }
  const childMetadata = {
    kind: 'child', session: metadata.session, plan,
    parentPlan: metadata.parentPlan, parentMetadata: metadata.parentMetadata,
    expectedPlan: expectedPlan || null,
    expectedExecutionArgs: expectedPlan ? plan.args : null,
    match: metadata.match, lane: metadata.lane, reference: metadata.reference,
  };
  ISSUED_PLAN_METADATA.set(plan, childMetadata);
  return plan;
}

export function isIssuedExecutionProofReadback(plan) {
  const metadata = ISSUED_PLAN_METADATA.get(plan);
  return metadata?.kind === 'child'
    && metadata.plan === plan
    && metadata.lane === 'proof'
    && metadata.reference?.type === 'readback'
    && metadata.match?.provided?.capability === 'workspace.file.read'
    && metadata.match?.operation?.id === 'read'
    && plan?.capabilityId === 'workspace.file.read'
    && plan.operationId === 'read'
    && plan.execution?.lane === 'proof'
    && plan.execution?.type === 'readback';
}

export function resolveCapability(capabilityId, {
  operation = null, mode = 'chat', synthetic = false, effectPolicy = null, args = {},
} = {}) {
  const requestedCapability = String(capabilityId || '').trim(), requestedOperation = operation == null ? null : String(operation).trim();
  if (!['chat', 'autonomy'].includes(mode)) return [];
  return TOOL_REGISTRY.flatMap((tool) => tool.provides.flatMap((provided) => {
    if (provided.capability !== requestedCapability) return [];
    if (synthetic && !toolSyntheticAllowed(tool.name)) return [];
    return provided.operations.filter((item) => (
      (!requestedOperation || item.id === requestedOperation)
      && authorityAllows(item, mode)
      && toolAllowedByPolicy(tool.name, { ...args, ...semanticArgs(tool, item.binding.action || item.id) }, effectPolicy)
    )).map(({ id, binding, ...metadata }) => {
      const handle = /** @type {any} */ ({
        capability: provided.capability, operation: id, ...metadata, tool,
      });
      handle.bind = (input) => bindCapability(handle, input);
      Object.freeze(handle);
      HANDLE_BINDINGS.set(handle, binding);
      return handle;
    });
  })).sort((left, right) => toolProofRank(right.tool.name) - toolProofRank(left.tool.name));
}
export const TOOL_REGISTRY = buildToolRegistry([
  defineTool('huis_status', handleHuisStatus, fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house contribution:house proof:house grounding receipt:conversation', { synthetic: 'allow', report: 'none', proofRank: 2 })),
    'Lees gegronde Home Assistant-context. Zet history alleen bij een expliciete historische of causale informatievraag; dan mag dezelfde issued read na actuele kandidaatselectie exact één uur entity-gebonden logboekbewijs lezen.',
    obj({ vraag: str('Optioneel onderwerp of apparaat om gericht terug te lezen'), historie: { type: 'boolean', description: 'Expliciet historische of causale readbehoefte' } }),
    capability('home.context.read', 'read', { input: semanticInput({ query: str('Onderwerp om gericht te lezen'),
      history: { type: 'boolean', description: 'Alleen bij expliciet historische of causale informatiebehoefte' } }, [], { query: 'vraag', history: 'historie' }) })),
  defineTool('bedien_huis', handleBedienHuis, fixed(TOOL_EFFECT.home, full('house delegated contribution:house grounding', { report: 'conditional', verification: 'explicit', proofRank: 5 })),
    'Bedien een normaal, omkeerbaar Home Assistant-apparaat en lees de toestand daarna terug. Gebruik gebruik_robot voor de stofzuiger; die is geen aan/uit-schakelaar.',
    obj({
      doel: str('Apparaat of entity-id, bijvoorbeeld "heater buiten" of "light.keuken_3"'),
      actie: str('aan, uit, toggle, open, dicht, pauze, speel of temperatuur'),
      waarde: num('Optionele numerieke waarde, bijvoorbeeld de doeltemperatuur'),
    }, ['doel', 'actie']), capability('home.device.control', semanticOperation('set', {
      fastlane: 'contextual_home',
      action: false,
      input: semanticInput({
        target: str('Apparaat of entity-id'),
        desiredState: { type: 'string', enum: ['on', 'off', 'toggle', 'open', 'close', 'pause', 'play', 'temperature'] },
        value: num('Optionele numerieke waarde'),
      }, ['target', 'desiredState'], { target: 'doel', desiredState: 'actie', value: 'waarde' }, {
        desiredState: { on: 'aan', off: 'uit', close: 'dicht', pause: 'pauze', play: 'speel', temperature: 'temperatuur' },
      }),
    }))),
  defineTool('gebruik_robot', handleGebruikRobot, mixed(TOOL_EFFECT.home, ['status', 'kaart'], full('discovery house delegated', { report: 'conditional', verification: 'explicit' })),
    'Gebruik Erik als echt beweegbaar zintuig of stofzuiger. Status en kaart zijn alleen waarneming. Bij een read-only verzoek of "laat hem stilstaan" gebruik je status/kaart en stuur je geen stop-service alleen ter geruststelling. Gebruik bekijk_camera voor iedere stilstaande camerawaarneming. Verken draait alleen een korte puls als eerst live beeld beschikbaar is, stopt altijd en leest de eindstaat terug. Start, pauze, stop en dock gebruiken de echte vacuum-services.',
    obj({
      actie: str('status, kaart, verken, start, pauze, stop of dock'),
      richting: str('Bij verken: links of rechts'),
      duur_ms: num('Bij verken: 150-500 milliseconden', { minimum: 150, maximum: 500 }),
    }, ['actie']), [capability('robot.observe', ['status', 'kaart']),
      capability('robot.control', [opRequires('verken', ['richting', 'duur_ms']), 'start', 'pauze', 'stop', 'dock'])]),
  defineTool('ha_lees', handleHaLees, fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house primary:house contribution:house proof:house grounding receipt:conversation', { synthetic: 'allow', report: 'none', proofRank: 2 })),
    'Lees een exacte Home Assistant-entiteit, veilige GET-route of volledige read-only integratie-inventaris; wijzigt niets. Inventariseer alleen een reeds bekende exacte integratieslug; zoek onbekende entiteiten eerst met huis_status. De inventaris koppelt config entries, devices, entities en actuele states, maar bewijst geen compatibiliteit of geheime pairinggegevens. Voor "gebeurde dit/wanneer/waardoor": vind eerst de exacte entity-id met huis_status en lees daarna echt `/api/logbook/<ISO-start>?entity=<urlencoded-entity-id>&end_time=<ISO-eind>`. Een algemene states-uitlezing is geen tijdlijnonderzoek.',
    obj({
      entity_id: str('Exacte entity-id (optioneel)'),
      pad: str('Veilige Home Assistant API-route (optioneel)'),
      integratie: str('Reeds bekende exacte integratieslug, bijvoorbeeld tuya (optioneel)', { pattern: HA_INTEGRATION_SLUG_PATTERN.source }),
    }), [
      capability('home.entity.read', semanticOperation('read', {
        action: false, supportsDeferred: true, rebindableFields: ['entityId'], sourceBoundFields: ['entityId'],
        signalScope: HOME_ENTITY_SIGNAL_SCOPES, signalKeyField: 'entityId',
        input: semanticInput({
          entityId: str('Exacte Home Assistant entity-id of onopgeloste deferred marker', {
            pattern: '^(?:[a-z0-9_]+\\.[a-z0-9_]+|<[^<>\\r\\n]{1,120}>)$', maxLength: 255 }),
        }, ['entityId'], { entityId: 'entity_id' }),
      })),
      capability('home.api.read', semanticOperation('read', {
        action: false,
        input: semanticInput({
          path: str('Veilige Home Assistant API-route', { pattern: '^/api/[A-Za-z0-9_./?=&%:+-]+$', maxLength: 2_000 }),
        }, ['path'], { path: 'pad' }),
      })),
      capability('home.integration.inventory', semanticOperation('read', {
        action: false, sourceBoundFields: ['integrationSlug'],
        input: semanticInput({
          integrationSlug: str('Reeds bekende exacte Home Assistant-integratieslug', { pattern: HA_INTEGRATION_SLUG_PATTERN.source }),
        }, ['integrationSlug'], { integrationSlug: 'integratie' }),
      })),
    ]),
  defineTool('wie_is_thuis', handleWieIsThuis, fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house lane:family primary:house contribution:house proof:house grounding', { synthetic: 'allow', report: 'none' })),
    'Lees actuele aanwezigheid; met een naam worden de beschikbare bronnen voor die persoon vergeleken.',
    obj({ persoon: str('Optionele naam') }), capability('household.presence.read', 'read')),
  defineTool('beheer_aanwezigheid', handleBeheerAanwezigheid, mixed(TOOL_EFFECT.home, ['bekijk'], full('house delegated lane:house primary:house', { report: 'conditional', verification: 'explicit' })),
    'Bekijk of wijzig welke bestaande Home Assistant device_trackers een persoon voor aanwezigheid gebruikt. Gebruik "bekijk" om de echte person-config te lezen en "stel_trackers_in" om na een expliciete opdracht de volledige gewenste trackerlijst te zetten en terug te verifiëren. Zeg nooit dat dit handmatig moet voordat deze tool is geprobeerd.',
    obj({
      actie: str('bekijk of stel_trackers_in'),
      persoon: str('Exacte persoonsnaam of person-id'),
      device_trackers: { type: 'array', items: { type: 'string' }, description: 'Bij stel_trackers_in: volledige gewenste lijst met bestaande device_tracker entity-ids' },
    }, ['actie', 'persoon']), [capability('household.presence.config.read', 'bekijk'),
      capability('household.presence.config.write', opRequires('stel_trackers_in', ['persoon', 'device_trackers']))]),
  defineTool('zoek_internet', handleZoekInternet, fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace primary:research contribution:research proof:project grounding', { synthetic: 'allow', report: 'none', proofRank: 4 })),
    'Doe actueel internetonderzoek met directe bronlinks. Gebruik dit voor actuele feiten, prijzen, markt, weerbronnen en technische documentatie.',
    obj({ vraag: str('Concrete onderzoeksvraag, inclusief de juiste plaats of context') }, ['vraag']), capability('internet.research', 'search')),
  defineTool('beheer_agentmail', handleBeheerAgentmail, mixed(TOOL_EFFECT.external, [
      'status', 'berichten', 'inbox', 'lezen', 'list', 'zoeken', 'zoek', 'search',
      ], full('delegated lane:research lane:workspace lane:family proof:project grounding', { report: 'effect' })),
    'Gebruik Eva haar echte AgentMail-inbox rechtstreeks via de officiële API, zonder browser of losse SDK. Kan aanmelden, verifiëren, e-mail versturen, recente berichten lezen en berichten doorzoeken. De API-sleutel blijft uitsluitend versleuteld in Eva haar kluis.',
    obj({
      actie: str('status, aanmelden, verifiëren, versturen, berichten of zoeken'),
      mens_email: str('Bij aanmelden: e-mailadres van de menselijke eigenaar'),
      gebruikersnaam: str('Bij aanmelden: gewenste inboxnaam, standaard eva-bottie'),
      otp_code: str('Bij verifiëren: zescijferige code uit de verificatiemail'),
      otp_opnieuw: { type: 'boolean', description: 'Alleen true als de mens expliciet om een nieuwe OTP vroeg; dit kan de API-sleutel vervangen' },
      aan: str('Bij versturen: ontvanger; leeg betekent het opgeslagen adres van de eigenaar'),
      onderwerp: str('Bij versturen: concreet onderwerp'),
      tekst: str('Bij versturen: volledige platte tekst van de e-mail'),
      zoekterm: str('Bij zoeken: woorden uit afzender, ontvanger, onderwerp of berichtinhoud'),
      limiet: num('Bij berichten of zoeken: maximaal aantal resultaten, 1-50'),
    }, ['actie']), [capability('mailbox.read', ['status', 'berichten', 'inbox', 'lezen', 'list', 'zoeken', 'zoek', 'search']),
      capability('mailbox.manage', [opRequires('aanmelden', ['mens_email']), opRequires('verifieren', ['otp_code']),
        opRequires('versturen', ['onderwerp', 'tekst'])])]),
  defineTool('lees_bestand', handleLeesBestand, fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace primary:workspace contribution:making proof:project grounding reasoning:mechanical', { synthetic: 'allow', report: 'none', proofRank: 3 })),
    'Lees of doorzoek een bestand uit Eva haar project of data/workspace. Gebruik zoek bij grote bronbestanden om relevante regels met context te vinden, en lees alle expliciet genoemde bestanden. Geheime bestanden zijn afgeschermd.',
    obj({
      pad: str('Pad naar het bestand'),
      zoek: str('Optionele tekst of eenvoudige reguliere expressie; retourneert treffers met regelnummers en context'),
      vanaf_regel: num('Optionele eerste regel, vanaf 1'),
      aantal_regels: num('Optioneel aantal regels, maximaal 400'),
    }, ['pad']), capability('workspace.file.read', semanticOperation('read', { rebindableFields: ['pad'] }))),
  defineTool('beheer_runtime', handleBeheerRuntime, runtimeLifecycle(full('lane:workspace primary:workspace contribution:making proof:project reasoning:code', { report: 'effect', verification: 'explicit' })),
    'Repareer Eva haar runtime transactioneel. Volg status → stage → beproef → latere status → pas_toe → latere status. De root-owned helper leidt de kandidaat zelf uit patch plus live preimage af en draait vaste netwerkloze gates inclusief coverage. Iedere JavaScript-promotie en rollback vereist daarna een exacte rootgrant of offline rootgoedkeuring. Pas_toe, bootcanary en rollback zijn asynchroon: queued, root_tested of applied_pending_boot is nog niet live.',
    obj({
      bewerking: str('status, stage, beproef, pas_toe of rollback'),
      transactie_id: str('Transactie-id uit stage; niet nodig voor algemene status'),
      basis_sha256: str('Bij stage: exacte tracked_tree_sha256 uit de direct voorafgaande status'),
      patch: str('Bij stage: begrensde unified diff met --- a/pad, +++ b/pad en hunks'),
      receipt_sha256: str('Bij pas_toe: exacte root_receipt_sha256 uit de latere status'),
    }, ['bewerking']), [capability('runtime.status.read', 'status', { authority: 'trusted_owner' }),
      capability('runtime.change.stage', opRequires('stage', ['basis_sha256', 'patch']), {
        authority: 'trusted_owner', idempotency: 'cas', proofFloor: ['accepted_result'] }),
      capability('runtime.change.test', opRequires('beproef', ['transactie_id'], { supportsDeferred: true }), {
        authority: 'trusted_owner', idempotency: 'sealed_transaction', proofFloor: ['root_receipt'] }),
      capability('runtime.change.apply', opRequires('pas_toe', ['transactie_id', 'receipt_sha256'], { supportsDeferred: true }), {
        authority: 'root_approved', idempotency: 'sealed_transaction', proofFloor: ['root_receipt'] }),
      capability('runtime.change.rollback', opRequires('rollback', ['transactie_id'], { supportsDeferred: true }), {
        authority: 'root_approved', idempotency: 'sealed_transaction', proofFloor: ['root_receipt'] })]),
  defineTool('schrijf_bestand', handleSchrijfBestand, fixed(TOOL_EFFECT.workspace, bounded('lane:research lane:workspace primary:workspace contribution:making proof:project reasoning:code', { synthetic: 'isolated', report: 'none', verification: 'ok' })),
    'Schrijf of overschrijf een bestand binnen Eva haar werkruimte. Een werkruimte-relatief pad zoals testmap/program.py is voldoende; data/workspace/... blijft als stabiele alias geldig. Schrijf ook voorbeeldinvoer met deze tool; maak die niet via inline-code. De werkruimte behandelt .js expliciet als CommonJS (require/module.exports); gebruik .mjs als je ESM wilt.',
    obj({
      pad: str('Werkruimte-relatief bestandspad; geen ..-segmenten of pad buiten de werkruimte', {
        minLength: 1, maxLength: 500,
      }),
      inhoud: str('Volledige, direct opslaanbare bestandsinhoud; geen beschrijving van wat er nog geschreven moet worden', {
        minLength: 1, maxLength: 512_000,
      }),
    }, ['pad', 'inhoud']), capability('workspace.file.write', semanticOperation('write', {
      input: semanticInput({
        pad: str('Werkruimte-relatief doelbestand, bijvoorbeeld testmap/program.py; .. is verboden', {
          minLength: 1, maxLength: 500,
          pattern: '^[A-Za-z0-9_./ -]+\\.[A-Za-z0-9]{1,16}$',
        }),
        inhoud: str('Volledige gematerialiseerde bestandsinhoud, dus broncode of fixture zelf', {
          minLength: 1, maxLength: 512_000,
        }),
      }, ['pad', 'inhoud'], {}, {}, { pad: 'workspace_file' }),
      execution: iterativeExecution({
        materializable: ['inhoud'], targetField: 'pad', artifactLimit: 4,
        proofOperations: [
          executionRef('workspace.file.write', 'write', 'write'),
          executionRef('workspace.program.execute', 'execute', 'run'),
          executionRef('workspace.file.read', 'read', 'readback'),
        ],
        recoveryOperations: [
          executionRef('workspace.file.read', 'read', 'inspect'),
          executionRef('workspace.file.write', 'write', 'repair'),
          executionRef('workspace.program.execute', 'execute', 'retest'),
        ],
      }),
    }))),
  defineTool('lijst_bestanden', handleLijstBestanden, fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace primary:workspace proof:project', { synthetic: 'allow', report: 'none' })),
    'Lijst bestanden en mappen binnen Eva haar project of data/workspace. Gebruik pad "." voor vragen over dit project, de repo, architectuur of broncode; data/workspace is alleen de standaard voor gemaakte werkstukken. Met zoek wordt veilig en recursief in bestandsnamen en leesbare tekst gezocht; lees daarna de beste bron gericht of gepagineerd.',
    obj({
      pad: str('Map om te bekijken; gebruik . voor de projectroot, standaard data/workspace'),
      zoek: str('Optionele woorden voor gerangschikte zoeking in naam en inhoud'),
      volgorde: str('naam of nieuwste'),
    }), capability('workspace.file.list', 'list')),
  defineTool('voer_uit', handleVoerUit, fixed(TOOL_EFFECT.workspace, bounded('lane:research lane:workspace primary:workspace contribution:making proof:project grounding reasoning:code', { synthetic: 'isolated', report: 'none', verification: 'ok', proofRank: 5 })),
    'Voer zonder shell een eerder met schrijf_bestand opgeslagen lokaal programma uit binnen data/workspace. Toegestaan: node, npm, npx, python, python3 en git. Inline-opties zoals node -e, python -c en python -m zijn niet toegestaan. Geef verwachte_exitcodes mee wanneer bijvoorbeeld een audit bewust 1 gebruikt voor gevonden problemen.',
    obj({
      programma: str('Programmanaam'),
      argumenten: { type: 'array', items: { type: 'string' }, description: 'Afzonderlijke argumenten' },
      werkmap: str('Projectrelatief werkmappad dat letterlijk met data/workspace begint'),
      verwachte_exitcodes: { type: 'array', items: { type: 'number' }, description: 'Geldige uitkomsten voor deze proef; standaard [0]' },
    }, ['programma']), capability('workspace.program.execute', semanticOperation('execute', {
      execution: {
        mode: 'direct',
        fileArguments: {
          programField: 'programma',
          argumentsField: 'argumenten',
          materializedByProgram: {
            node: ['.cjs', '.js', '.mjs'],
            python: ['.py'],
            python3: ['.py'],
          },
          readDependencies: ['.csv', '.json', '.jsonl', '.ndjson', '.txt', '.yaml', '.yml'],
        },
      },
    }))),
  defineTool('maak_vaardigheid', handleMaakVaardigheid, mixed(TOOL_EFFECT.workspace, ['lijst'], bounded('contribution:making reasoning:code', { report: 'none', verification: 'explicit' })),
    'Maak, beproef, bekijk of activeer een versieerbare eigen werkwijze. Volg stage → beproef → activeer. De proef krijgt een receipt dat exact aan naam, versie, instructiehash en testbestand is gebonden.',
    obj({
      bewerking: str('stage, beproef, activeer, deactiveer of lijst'),
      naam: str('Naam of slug van de vaardigheid'),
      beschrijving: str('Welk herhaalbaar probleem deze werkwijze oplost'),
      instructies: str('Concrete algemene werkwijze, geen vaste antwoordzinnen'),
      triggers: { type: 'array', items: { type: 'string' }, description: 'Onderwerpen waarbij de vaardigheid relevant is' },
      testbestand: str('Bestaand testprogramma in data/workspace dat de vaardigheid controleert'),
      testruntime: str('node of python3'),
      testargumenten: { type: 'array', items: { type: 'string' }, description: 'Optionele argumenten voor de gebonden proef' },
      receipt_id: num('Receipt-id uit beproef voor exact deze versie'),
    }, ['bewerking']), [capability('capability.skill.read', 'lijst'),
      capability('capability.skill.manage', [opRequires('stage', ['naam', 'beschrijving', 'instructies', 'testbestand', 'testruntime']),
        opRequires('beproef', ['naam']), opRequires('activeer', ['naam', 'receipt_id']), opRequires('deactiveer', ['naam'])])]),
  defineTool('beheer_addon', handleBeheerAddon, mixed(TOOL_EFFECT.workspace, ['lijst'], bounded('contribution:making reasoning:code', { report: 'none', verification: 'explicit' })),
    'Registreer, beproef, bekijk, activeer of quarantaineer een uitvoerbare add-on in Eva haar geïsoleerde werkruimte. Volg stage → beproef → activeer. De vaste sandbox heeft alleen workspace:read-write en network:none.',
    obj({
      bewerking: str('stage, beproef, activeer, quarantaineer, deactiveer of lijst'),
      naam: str('Naam of slug van de add-on'),
      beschrijving: str('Concrete capability die deze add-on toevoegt'),
      entrypoint: str('Bestaand programma in data/workspace'),
      runtime: str('node of python3'),
      bron: str('Herkomst van de code of het pakket'),
      bronversie: str('Exacte versie of revisie'),
      permissions: { type: 'array', items: { type: 'string' }, description: 'Benodigde declaratieve rechten' },
      triggers: { type: 'array', items: { type: 'string' }, description: 'Onderwerpen waarbij de add-on relevant is' },
      testargumenten: { type: 'array', items: { type: 'string' }, description: 'Optionele argumenten voor de gebonden proef' },
      receipt_id: num('Receipt-id uit beproef voor exact deze versie'),
    }, ['bewerking']), [capability('capability.addon.read', 'lijst'),
      capability('capability.addon.manage', [opRequires('stage', ['naam', 'beschrijving', 'entrypoint', 'runtime', 'bron', 'bronversie']),
        opRequires('beproef', ['naam']), opRequires('activeer', ['naam', 'receipt_id']),
        opRequires('quarantaineer', ['naam']), opRequires('deactiveer', ['naam'])])]),
  defineTool('gebruik_capability', handleGebruikCapability, fixed(TOOL_EFFECT.workspace, bounded('contribution:making reasoning:mechanical', { synthetic: 'isolated', report: 'none', changesByDefault: false })),
    'Demonstreer een reeds actieve, receipt-gecontroleerde skill of add-on. Zonder naam wordt de laatst geactiveerde gekozen. Een skill wordt aantoonbaar geladen; een add-on wordt met haar vastgelegde entrypoint in de netwerkloze sandbox uitgevoerd.',
    obj({
      naam: str('Optionele naam of slug van een actieve capability'),
      argumenten: { type: 'array', items: { type: 'string' }, description: 'Alleen voor een actieve add-on; runtime-argumenten' },
      verwachte_exitcodes: { type: 'array', items: { type: 'number' }, description: 'Geldige procesuitkomsten voor deze concrete demonstratie; standaard [0]' },
    }), capability('capability.active.use', 'use')),
  defineTool('inspecteer_vermogen', handleInspecteerVermogen, fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace lane:family', { synthetic: 'allow', report: 'none' })),
    'Inspecteer provider-onafhankelijk welke kerntools, skills, add-ons, implementatiebronnen en geconfigureerde geheimnamen bij een gewenste handeling passen. Leest alleen metadata en toont nooit geheime waarden. Gebruik dit vóór een conclusie over wat Eva wel of niet kan.',
    obj({
      vraag: str('De concrete gewenste handeling of het vermogen dat onderzocht moet worden'),
    }, ['vraag']), capability('capability.catalog.inspect', 'inspect')),
  defineTool('beheer_werkcel', handleBeheerWerkcel, mixed(TOOL_EFFECT.persistent, ['status', 'lijst'], bounded('discovery lane:research lane:workspace primary:research', { report: 'none', verification: 'explicit' })),
    'Verdeel één groter doel achter dezelfde Eva in 1-20 onafhankelijke onderzoekstromen, voer maximaal zes daarvan aantoonbaar parallel uit, bewaar bewijs en bronlinks per stroom en verbreed automatisch van primaire documentatie naar vakbronnen/forums wanneer de eerste route onvoldoende is. Gebruik start voor echt parallel werk, status/lijst om terug te lezen en hervat om onderbroken of mislukte stromen opnieuw op te pakken.',
    obj({
      bewerking: str('start, status, lijst of hervat'),
      id: num('Bestaande werkcel-id bij status of hervat'),
      titel: str('Korte naam van het gezamenlijke werk'),
      doel: str('Concreet gezamenlijk einddoel'),
      werkstromen: {
        type: 'array',
        maxItems: WORKCELL_LIMITS.maxStreams,
        items: {
          type: 'object',
          properties: {
            titel: { type: 'string', description: 'Naam van deze onafhankelijke werkstroom' },
            vraag: { type: 'string', description: 'Concrete, zelfstandig onderzoekbare vraag' },
          },
          required: ['vraag'],
          additionalProperties: false,
        },
        description: 'Onafhankelijke onderzoekstromen die veilig tegelijk mogen lopen',
      },
      gelijktijdigheid: num('Aantal gelijktijdige stromen, 1-6; standaard 3'),
    }, ['bewerking']), [capability('research.workcell.read', ['status', 'lijst']),
      capability('research.workcell.manage', [opRequires('start', ['titel', 'doel', 'werkstromen']),
        opRequires('hervat', ['id'])], { idempotency: 'cas' })]),
  defineTool('beheer_toolpakket', handleBeheerToolpakket, mixed(TOOL_EFFECT.workspace, ['inspecteer', 'status', 'lijst'], bounded('discovery lane:research lane:workspace primary:research reasoning:code', { report: 'none', verification: 'explicit' })),
    'Ontdek, stage, inspecteer en activeer een echt npm-pakket als toekomstige uitbreiding. Discovery leest officiële registryprovenance; installatie vereist een exacte versie, negeert install-scripts en voert pakketcode niet uit. Activatie kan alleen na een geslaagde netwerkloze healthcheck via voer_uit in dezelfde beurt, gebonden aan de ongewijzigde artifacthash. Een gestaged pakket is nog geen werkende Eva-tool.',
    obj({
      bewerking: str('ontdek, installeer, inspecteer, lijst, activeer, deactiveer of quarantaineer'),
      id: num('Installatierecord-id bij inspectie, activatie of statuswijziging'),
      pakket: str('Exacte npm-registrynaam, bijvoorbeeld lodash of @scope/package'),
      versie: str('Exacte semver bij installatie; bij ontdek mag ook latest'),
      artifact_sha256: str('Exacte artifacthash uit inspectie, vereist bij activeer'),
      bewijs: str('Korte beschrijving van de netwerkloze healthcheck of reden van quarantaine'),
    }, ['bewerking']), [capability('package.registry.read', ['inspecteer', 'status', 'lijst']),
      capability('package.workspace.manage', [opRequires('ontdek', ['pakket']), opRequires('installeer', ['pakket', 'versie']),
        opRequires('activeer', ['id', 'artifact_sha256']), opRequires('deactiveer', ['id']),
        opRequires('quarantaineer', ['id'])])]),
  defineTool('onthoud', handleOnthoud, fixed(TOOL_EFFECT.persistent, bounded('lane:family primary:family', { report: 'none', verification: 'ok' })),
    'Bewaar een concreet feit, voorkeur, afspraak of gebeurtenis over het gezin.',
    obj({
      inhoud: str('De concrete herinnering'),
      persoon: str('Optionele persoon'),
      categorie: str('feit, voorkeur, gebeurtenis, persoon of afspraak'),
      belang: num('Belang tussen 0 en 1'),
    }, ['inhoud']), capability('memory.fact.write', 'remember')),
  defineTool('zoek_geheugen', handleZoekGeheugen, fixed(TOOL_EFFECT.read, bounded('discovery free lane:family primary:family grounding', { report: 'none' })),
    'Zoek in Eva haar blijvende herinneringen.',
    obj({ vraag: str('Zoekvraag') }, ['vraag']), capability('memory.fact.read', 'search')),
  defineTool('zoek_gesprek', handleZoekGesprek, fixed(TOOL_EFFECT.read, full('lane:house lane:family grounding receipt:conversation', { report: 'none' })),
    'Zoek echte eerder opgeslagen Telegram-berichten terug.',
    obj({ vraag: str('Zoekwoorden'), persoon: str('Optionele afzender'), limiet: num('Maximaal aantal resultaten') }), capability('conversation.history.read', 'search')),
  defineTool('zet_herinnering', handleZetHerinnering, fixed(TOOL_EFFECT.persistent, full('delegated', { report: 'conditional', verification: 'ok' })),
    'Plan een gezinsherinnering op een exact ISO-tijdstip in Europe/Amsterdam.',
    obj({
      wanneer: str('ISO-tijdstip'),
      tekst: str('Herinnering'),
      voor_wie: str('Optionele ontvanger'),
      herhaling: str('Optioneel: daily, weekly of monthly'),
    }, ['wanneer', 'tekst']), capability('reminder.schedule', 'schedule')),
  defineTool('toon_herinneringen', handleToonHerinneringen, fixed(TOOL_EFFECT.read, bounded('discovery free', { report: 'none' })),
    'Lees alle open herinneringen.',
    obj({}), capability('reminder.read', 'list')),
  defineTool('annuleer_herinnering', handleAnnuleerHerinnering, fixed(TOOL_EFFECT.persistent, full('delegated', { report: 'effect', verification: 'ok' })),
    'Annuleer een open herinnering.',
    obj({ id: num('Herinnerings-id') }, ['id']), capability('reminder.cancel', 'cancel')),
  defineTool('bekijk_camera', handleBekijkCamera, fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house primary:house contribution:house proof:house grounding', { synthetic: 'allow', report: 'none', proofRank: 4 })),
    'Bekijk en beschrijf één echt actueel stilstaand camerabeeld zonder een apparaat te bewegen. Kies home_assistant voor een HA-camera of robot voor de frontcamera van de robotstofzuiger.',
    obj({ provider: str('Cameraprovider', { enum: ['home_assistant', 'robot'] }),
      target: str('Camera-entity, herkenbare cameranaam of frontcamera bij provider robot', { minLength: 1, maxLength: 160 }) },
    ['provider', 'target']), capability('camera.frame.read', 'capture')),
  defineTool('leer_gezicht_van_foto', handleLeerGezichtVanFoto, fixed(TOOL_EFFECT.persistent, never('delegated', { report: 'none', verification: 'ok' })),
    'Koppel de naam aan de exact bewaarde Telegram-foto uit de actuele context en leer het gezicht alleen als het beeld bruikbaar is.',
    obj({ naam: str('Naam van de persoon') }, ['naam']), capability('identity.face.learn', 'learn', { authority: 'trusted_context' })),
  defineTool('spreek_in_huis', handleSpreekInHuis, fixed(TOOL_EFFECT.voice, full('house delegated contribution:house', { report: 'none', verification: 'ok' })),
    'Spreek een korte boodschap via exact één toegestane Voice PE (Assist satellite). Gebruik het optionele doel voor bijvoorbeeld woonkamer of overkapping; zonder doel geldt het geconfigureerde standaarddoel. Deze tool wijkt nooit uit naar een versterker of Chromecast.',
    obj({
      tekst: str('Wat Eva zegt'),
      doel: {
        type: 'string',
        enum: config.voiceAssistSatelliteTargets.flatMap((target) => [target.alias, target.entityId]),
        description: 'Optionele bekende Voice PE-alias of exact assist_satellite entity_id',
      },
    }, ['tekst']), capability('home.voice.announce', semanticOperation('announce', {
      fastlane: 'literal_voice',
      input: semanticInput({
        literalText: str('Letterlijke boodschap'),
        targetAlias: { type: 'string', enum: config.voiceAssistSatelliteTargets.flatMap((item) => [item.alias, item.entityId]) },
      }, ['literalText'], { literalText: 'tekst', targetAlias: 'doel' }),
    }), { idempotency: 'deduplicated' })),
  defineTool('stuur_bericht', handleStuurBericht, fixed(TOOL_EFFECT.external, bounded('free lane:house lane:research lane:workspace lane:family contribution:contact', { report: 'delivery', verification: 'ok' })),
    'Stuur een zelfstandig Telegram-bericht. Alleen voor een nieuw, gecontroleerd en nuttig resultaat; nooit voor voortgang of een herhaling van eerder advies. Schrijf als huisgenoot: conclusie eerst, hooguit twee korte zinnen en alleen meetwaarden die nodig zijn.',
    obj({ tekst: str('Het ene eindbericht'), aan: str('Optionele ontvanger') }, ['tekst']), capability('message.deliver', 'send', { idempotency: 'deduplicated' })),
  defineTool('doel_bijhouden', handleDoelBijhouden, mixed(TOOL_EFFECT.persistent, ['list', 'lijst'], full('', { report: 'none', changesByDefault: false })),
    'Maak, wijzig, pauzeer, hervat of rond een zelfgekozen langetermijndoel af. Dit kiest nooit het onderwerp; het bewaart Eva haar eigen reden, horizon en toetsbare succesmaat zodat aandachtcycli dezelfde richting kunnen voortzetten.',
    obj({
      bewerking: str('create, update, pause, resume, complete, abandon of list'),
      doel_sleutel: str('Bestaande unieke doelsleutel; niet bij create/list'),
      verwachte_revisie: num('Geattesteerde bestaande doelrevisie voor botsingsvrije wijziging'),
      titel: str('Herkenbare naam van het doel'),
      intentie: str('Welke blijvende verandering Eva wil bereiken'),
      waarom: str('Waarom Eva dit zelf belangrijk vindt'),
      begunstigde: str('Voor wie dit waarde heeft'),
      horizon: str('Verwachte tijdshorizon of reviewperiode'),
      succesmaten: { type: 'array', items: { type: 'string' }, description: 'Toetsbare succesmaten' },
      prioriteit: num('Prioriteit 0-1'),
      volgende_review: str('Optioneel ISO-reviewmoment'),
    }, ['bewerking']), [capability('goal.portfolio.read', ['list', 'lijst']),
      capability('goal.portfolio.manage', [opRequires('create', ['titel', 'intentie', 'waarom', 'begunstigde', 'horizon', 'succesmaten']),
        ...['update', 'pause', 'resume', 'complete', 'abandon'].map((id) => opRequires(id, ['doel_sleutel']))], { idempotency: 'cas' })]),
  defineTool('werk_bijhouden', handleWerkBijhouden, fixed(TOOL_EFFECT.persistent, bounded('lane:house lane:research lane:workspace lane:family', { report: 'none', changesByDefault: false })),
    'Bewaar of werk één duurzame klus bij die echt meerdere sessies nodig heeft. Niet gebruiken voor een simpele vraag, losse gedachte of alleen een plan. Afronden vereist bewijs van een andere tool in dezelfde beurt.',
    obj({
      bewerking: str('start, update, blokkeer, afronden of annuleren'),
      id: num('Bestaand werk-id; vereist behalve bij start'),
      titel: str('Concrete titel; vereist bij start'),
      doel: str('Toetsbaar eindresultaat; vereist bij start'),
      volgende_stap: str('De eerstvolgende uitvoerbare stap'),
      definitie_klaar: str('Toetsbare voorwaarden waaronder dit doel echt klaar is'),
      fase: str('Huidige concrete uitvoeringsfase'),
      toestand_json: str('Kleine gestructureerde tussenstaat als JSON'),
      hervatten_na: str('Optioneel ISO-tijdstip of lege string'),
      prioriteit: num('Prioriteit 0-100'),
      verwachte_revisie: num('Laatste gelezen revisie voor botsingsvrije update'),
      bewijs: str('Concreet teruggelezen bewijs'),
      resultaat: str('Eindresultaat, alleen bij afronden'),
      afhankelijk_van: str('Alleen bij echt blokkeren: concrete externe persoon, toestemming of gebeurtenis waarop Eva niet zelf kan handelen'),
      afleveren_aan: str('Verplicht expliciet doel wanneer zelfgekozen monitoring later een mens moet berichten'),
      doel_sleutel: str('Optionele doelsleutel die Eva eerder zelf met doel_bijhouden heeft gekozen'),
    }, ['bewerking']), capability('work.episode.manage', [
      opRequires('start', ['titel', 'doel'], { supportsDeferred: true }),
      opRequires('update', ['id'], { supportsDeferred: true }),
      opRequires('blokkeer', ['id']),
      opRequires('annuleren', ['id'], { sourceBoundFields: ['id'] }),
      opRequires('afronden', ['id', 'resultaat'])], { idempotency: 'cas' })),
  defineTool('verkenning_bijhouden', handleVerkenningBijhouden, mixed(TOOL_EFFECT.persistent, ['list', 'lijst'], bounded('', { report: 'none' })),
    `Leg Eva haar eigen nieuwsgierigheid vast als toetsbare cyclus; de tool kiest nooit het onderwerp. Start vóór de tweede inhoudelijke actie. Werk daarna bewijs, wachttijd of eindbesluit bij. Het actiebudget en de stopregel begrenzen doelloze calls, niet de doelkeuze. Bij afronden kan Eva optioneel met gezinsresultaat exact één nieuwe, geverifieerde en concreet bruikbare gezinsvondst zichtbaar maken, met economisch_vervolg duurzaam vervolgwerk kiezen${behaviorLearningAvailable() ? ', of met gedragsproef zelf een tijdelijke relationele stijlrichting beproeven' : ''}; deze drie keuzes zijn onderling exclusief. Een negatieve uitkomst, herhaling of gewone eigen interesse krijgt nooit gezinsresultaat. Alle inhoud komt van Eva; geen vervolg geeft extra handelingsbevoegdheid.`,
    obj({
      bewerking: str('start, update, wachten, afronden, stoppen of lijst'),
      id: num('Bestaande verkenning-id, behalve bij start/lijst'),
      vraag: str('Eigen concrete vraag'),
      aanleiding: str('Waarom dit nu werkelijk aandacht trekt'),
      hypothese: str('Toetsbare verwachting'),
      verwachte_waarneming: str('Wat de kleinste echte proef zou laten zien'),
      stopregel: str('Wanneer stoppen of aantoonbaar pivoteren'),
      max_acties: num('Maximaal 2-12 inhoudelijke toolacties'),
      onderwerp_sleutel: str('Verplichte stabiele semantische sleutel bij start; hergebruik exact dezelfde sleutel voor hetzelfde beslisdomein'),
      onderwerp_ankers: { type: 'array', items: { type: 'string' }, description: 'Bij start minstens twee stabiele ankers. Gebruik een echte entity:/event:/artifact:-waarde als die bestaat; nieuwe onderwerpen zijn ook toegestaan.' },
      aspiratie_id: num('Optionele gekoppelde eigen aspiratie'),
      bewijs: str('Nieuwe concrete waarneming'),
      resultaat: str('Conclusie, weerlegging, bruikbaar resultaat of bewezen stopreden'),
      besluit: str('Wat dit verandert aan een volgende keuze'),
      hervatten_na: str('ISO-tijdstip bij wachten'),
      verwachte_revisie: num('Laatst gelezen revisie'),
      eerder_besluit_id: num('Recent verkennings-id dat met nieuw bewijs bewust wordt heropend'),
      nieuw_bewijs: str('Concreet nieuw bewijs waardoor het eerdere besluit of de eerdere drempel verandert'),
      gezinsresultaat: obj({
        titel: str('Natuurlijke titel van exact één nieuwe, vooraf geverifieerde gezinsvondst; nooit gebruiken voor niets gevonden, een herhaling of een interne interesse', { minLength: 4, maxLength: 240 }),
        gezinsnut: str('Concrete manier waarop het gezin deze vondst kan gebruiken; geen systeemstatus of algemene mogelijkheid', { minLength: 8, maxLength: 600 }),
      }, ['titel', 'gezinsnut']),
      economisch_vervolg: obj({
        titel: str('Eva haar eigen concrete titel voor duurzaam vervolgwerk'),
        uitkomst: str('De zelfgekozen toetsbare uitkomst van het vervolgwerk'),
        definitie_klaar: str('Eva haar eigen voorwaarden waaronder het vervolg echt klaar is'),
        volgende_stap: str('De eerstvolgende uitvoerbare stap die Eva zelf kiest'),
      }, ['titel', 'uitkomst', 'definitie_klaar', 'volgende_stap']),
      ...(behaviorLearningAvailable() ? { gedragsproef: obj({
        doelpersoon: str('Bestaande gezinsbewoner voor wie Eva deze tijdelijke stijlproef zelf kiest', { ...(BEHAVIOR_TRIAL_PEOPLE.length ? { enum: BEHAVIOR_TRIAL_PEOPLE } : {}), minLength: 2, maxLength: 120 }),
        waargenomen_kloof: str('Eva haar vrije, evidence-based beschrijving; alleen audit, nooit prompttekst', { minLength: 12, maxLength: 500 }), hypothese: str('Eva haar vrije hypothese; alleen audit, nooit prompttekst', { minLength: 12, maxLength: 500 }),
        motivatie: str('Waarom Eva deze proef zelf wil doen; alleen audit, nooit prompttekst', { minLength: 12, maxLength: 500 }),
        dimensie: str('De door Eva gekozen relationele stijlas', { enum: ['warmth', 'playfulness', 'directness', 'independent_voice', 'informality', 'concision'] }), richting: str('De door Eva gekozen beweging op de stijlas', { enum: ['increase', 'decrease'] }),
        intensiteit: str('De door Eva gekozen sterkte; host begrenst alleen de proefduur', { enum: ['subtle', 'moderate', 'pronounced'] }), contextscope: str('Het door Eva gekozen soort gesprek waarin de proef relevant is', { enum: ['any_direct_reply', 'social_chat', 'answering_question', 'collaborative_task'] }),
      }, ['doelpersoon', 'waargenomen_kloof', 'hypothese', 'motivatie', 'dimensie', 'richting', 'intensiteit', 'contextscope']) } : {}),
    }, ['bewerking']), [capability('exploration.read', ['list', 'lijst']),
      capability('exploration.manage', [opRequires('start', ['vraag', 'aanleiding', 'hypothese', 'verwachte_waarneming',
        'stopregel', 'max_acties', 'onderwerp_sleutel', 'onderwerp_ankers']),
      ...['update', 'wachten', 'afronden', 'stoppen'].map((id) => opRequires(id, ['id']))], { idempotency: 'cas' })]),
  defineTool('toon_werk', handleToonWerk, fixed(TOOL_EFFECT.read, full('free lane:house lane:workspace lane:family', { report: 'none' })),
    'Lees een compacte pagina uit het duurzame werkboek: open en geblokkeerd eerst, daarna recent afgerond.',
    obj({
      cursor: num('Optionele cursor uit pagination.next_cursor', { minimum: 0, maximum: 100_000 }),
    }), capability('work.episode.read', 'list')),
  defineTool('signalen_afhandelen', handleSignalenAfhandelen, fixed(TOOL_EFFECT.persistent, bounded('free lane:house lane:research lane:workspace lane:family', { report: 'none', changesByDefault: false })),
    'Sluit de volledige hostgebonden SignalBatch af na inhoudelijke beoordeling.',
    obj({
      dispositie: str('Terminale beoordeling van de hele batch', {
        enum: ['acted', 'observed', 'dismissed', 'no_action'],
      }),
      oordeel: str('Concrete beoordeling op basis van de uitgevoerde stappen', { minLength: 8, maxLength: 600 }),
    }, ['dispositie', 'oordeel']), capability('signal.assess', semanticOperation('assess', {
      action: false, input: semanticInput({
        disposition: str('Terminale batchdispositie', { enum: ['acted', 'observed', 'dismissed', 'no_action'] }),
        assessment: str('Concrete beoordeling', { minLength: 8, maxLength: 600 }),
      }, ['disposition', 'assessment'], { disposition: 'dispositie', assessment: 'oordeel' }),
      idempotency: 'cas',
    }))),
]);

const LIVE_FRESHNESS_CAPABILITIES = new Set([
  'camera.frame.read',
  'home.api.read',
  'home.context.read',
  'home.entity.read',
  'home.integration.inventory',
  'household.presence.read',
  'internet.research',
  'robot.observe',
  'runtime.status.read',
]);
const HISTORICAL_FRESHNESS_CAPABILITIES = new Set([
  'conversation.history.read',
  'memory.fact.read',
]);
function capabilityFreshnessClass(capabilityId) {
  if (LIVE_FRESHNESS_CAPABILITIES.has(capabilityId)) return 'live';
  if (HISTORICAL_FRESHNESS_CAPABILITIES.has(capabilityId)) return 'historical';
  return 'static';
}

export function capabilityCatalog() {
  const capabilities = new Map();
  for (const tool of TOOL_REGISTRY) for (const provided of tool.provides) {
    const operations = capabilities.get(provided.capability) || new Map();
    for (const operation of provided.operations) if (!operations.has(operation.id)) operations.set(
      operation.id, Object.freeze({
        id: operation.id, effect: operation.effect, proofNeeds: operation.proofFloor,
        supportsDeferred: operation.supportsDeferred, input: parentSemanticBinding(operation).schema,
        execution: operation.execution, rebindableFields: operation.rebindableFields, sourceBoundFields: operation.sourceBoundFields,
        signalScope: operation.signalScope, signalKeyField: operation.signalKeyField,
        freshnessClass: capabilityFreshnessClass(provided.capability),
        ...(operation.fastlane ? { fastlane: operation.fastlane } : {}) }));
    capabilities.set(provided.capability, operations);
  }
  return Object.freeze([...capabilities].sort(([left], [right]) => left.localeCompare(right))
    .map(([id, operations]) => Object.freeze({ id, operations: Object.freeze([...operations.values()]) })));
}

// Model-facing schema view; semantics and policy stay owned by TOOL_REGISTRY.
export const TOOLS = deepFreeze(TOOL_REGISTRY.map(
  ({ name, description, parameters }) => ({ name, description, parameters })));
export function toolPolicy(name) {
  return TOOL_REGISTRY.find((tool) => tool.name === String(name || ''))?.policy || null;
}
export function toolHasTag(name, tag) { return toolPolicy(name)?.tags?.includes(String(tag || '')) === true; }
export function toolAvailableInAutonomy(name, fullAutonomy = false) {
  return toolPolicy(name)?.autonomy === 'bounded' || (fullAutonomy && toolPolicy(name)?.autonomy === 'full');
}
export function toolVerification(name, args = {}) { return toolEffect(name, args) === TOOL_EFFECT.read
  ? 'read' : toolPolicy(name)?.verification || 'read'; }
export function toolProofRank(name) { return Number(toolPolicy(name)?.proofRank) || 1; }
export function toolSyntheticAllowed(name) { return ['allow', 'isolated'].includes(toolPolicy(name)?.synthetic); }
export function toolIsDurable(name) { return toolPolicy(name)?.durable === true; }
export function toolRequiresEffectReport(name) { return ['effect', 'conditional'].includes(toolPolicy(name)?.report); }
export function toolEffect(name, args = {}) {
  const policy = toolPolicy(name);
  if (!policy) return 'unknown';
  const effect = policy.effect === 'mixed' ? policy.resolveEffect(args) : policy.effect;
  return EFFECT_VALUES.has(effect) ? effect : 'unknown';
}
function effectSet(value) {
  const values = value instanceof Set
    ? [...value]
    : Array.isArray(value) ? value : value == null ? [] : [value];
  return new Set(values.map((item) => String(item || '').trim().toLowerCase())
    .filter(EFFECT_VALUES.has.bind(EFFECT_VALUES)));
}

export function toolAllowedByPolicy(name, args = {}, effectPolicy = null) {
  if (effectPolicy == null || effectPolicy === '' || effectPolicy === 'allow'
    || effectPolicy === 'all' || effectPolicy === 'unrestricted') return true;
  const effect = toolEffect(name, args);
  if (effect === 'unknown') return false;
  const raw = typeof effectPolicy === 'string' ? { mode: effectPolicy } : effectPolicy;
  if (!raw || typeof raw !== 'object') return false;
  const mode = String(raw.mode || (raw.readOnly === true ? 'read_only' : 'allow')).trim().toLowerCase();
  if (['deny', 'deny_all', 'none'].includes(mode)) return false;
  if (effect === TOOL_EFFECT.read) return raw.allowRead !== false;
  if (['read', 'read_only', 'readonly'].includes(mode)) return false;
  const denied = effectSet(raw.deniedEffects ?? raw.denyEffects ?? raw.deny);
  if (denied.has(effect)) return false;
  const allowedValue = raw.allowedEffects ?? raw.allowEffects ?? raw.allow;
  if (allowedValue != null) return effectSet(allowedValue).has(effect);
  return ['allow', 'all', 'unrestricted'].includes(mode);
}

export function toolChangesByDefault(name) { return toolPolicy(name)?.changesByDefault === true; }
const selectFields = (source, names) => Object.fromEntries(names.map((name) => [name, source[name]]));
function compactWorkCell(cell) {
  if (!cell) return null;
  return {
    ...selectFields(cell, ['id', 'title', 'objective', 'status', 'concurrency', 'created_ts',
      'updated_ts', 'started_ts', 'finished_ts', 'last_error']),
    streams: (Array.isArray(cell.streams) ? cell.streams : []).map((stream) => ({
      ...selectFields(stream, ['id', 'ordinal', 'title', 'query', 'status', 'attempt_count', 'research_phase']),
      result: clean(stream.result_text || '', 2_400),
      sources: Array.isArray(stream.sources) ? stream.sources.slice(0, 10) : [],
      last_error: stream.last_error,
    })),
  };
}
function compactToolPackage(item) {
  if (!item) return null;
  return {
    ...selectFields(item, ['id', 'package_name', 'package_version', 'slug', 'status', 'registry_url',
      'target_path', 'artifact_sha256', 'manifest_sha256', 'lock_sha256', 'integrity_ok', 'files', 'bytes']),
    metadata: item.metadata ? selectFields(item.metadata,
      ['description', 'license', 'repository', 'deprecated', 'bin', 'install_scripts_ignored']) : null,
    ...selectFields(item, ['provenance', 'health_receipt', 'last_error', 'created_ts', 'updated_ts',
      'installed_ts', 'activated_ts']),
    events: Array.isArray(item.events) ? item.events.slice(-12) : [],
  };
}

export function toolsForTurn({ mode = 'chat' } = {}) {
  return mode !== 'autonomy' ? [...TOOLS]
    : TOOLS.filter((tool) => toolAvailableInAutonomy(tool.name, config.autonomyFull));
}
const PROOF_STOP_WORDS = new Set([
  'actie', 'alleen', 'bestand', 'bron', 'controle', 'echt', 'gezin', 'goed',
  'informatie', 'klaar', 'maken', 'resultaat', 'stap', 'vandaag', 'werk',
]);
function proofWords(value) {
  return words(value).filter((word) => word.length >= 3 && !PROOF_STOP_WORDS.has(word)).slice(0, 120);
}
function proofToolFitsSubject(subject, toolName) {
  const value = normalize(subject);
  const projectWork = /\b(?:audit|bestand|code|config|klant|koper|markt|prijs|product|programma|reddit|software|thread|verkoop|website)\b/i.test(value);
  const houseWork = /\b(?:aanwezig|camera|deur|gezin|home assistant|huis|lamp|licht|raam|sensor|temperatuur)\b/i.test(value);
  const houseProof = toolHasTag(toolName, 'proof:house');
  const projectProof = toolHasTag(toolName, 'proof:project');
  if (projectWork && houseProof) return false;
  if (houseWork && !projectWork && projectProof) return false;
  return true;
}
function evidenceMatches(subject, result) {
  if (!verifiedCompleteReceipt(result)) return false;
  if (!proofToolFitsSubject(subject, result.name)) return false;
  const expected = new Set(proofWords(subject));
  if (!expected.size) return false;
  const actual = new Set(proofWords(`${JSON.stringify(result.args || {})}\n${result.output || ''}`));
  let overlap = 0;
  for (const word of expected) {
    if (actual.has(word)) overlap += 1;
  }
  return overlap >= Math.min(2, expected.size);
}
const MATERIAL_WORK_MUTATION = /\b(?:aanpas(?:sen|t)?|bewerk(?:en|t)?|corrigeer\w*|cre[eë]er\w*|genereer\w*|maak\w*|opsla\w*|plaats\w*|produceer\w*|schrijf\w*|verander\w*|vervang\w*|voeg\w*|wijzig\w*)\b/iu;
function workRequiresMaterialMutation(item) {
  const subject = [item?.title, item?.goal, item?.next_step, item?.definition_of_done]
    .filter(Boolean).join(' ');
  return MATERIAL_WORK_MUTATION.test(subject);
}
function explicitWorkspaceTargets(item) {
  const subject = [item?.title, item?.goal, item?.next_step, item?.definition_of_done]
    .filter(Boolean).join(' ');
  const candidates = subject.match(/(?:data\/workspace\/|\/)[^\s<>"'`]+/gu) || [];
  const targets = new Set();
  for (const candidate of candidates) {
    const supplied = candidate.replace(/[),.:;!?]+$/u, '');
    try { targets.add(workspaceDisplayPath(supplied)); } catch { /* geen workspacepad */ }
  }
  return targets;
}
function verifiedChangedEffect(result) {
  if (!verifiedCompleteReceipt(result) || result?.changed !== true
    || ['werk_bijhouden', 'toon_werk', 'stuur_bericht', 'spreek_in_huis']
      .includes(result?.name)) return false;
  const effect = toolEffect(result?.name, result?.args || {});
  return effect !== TOOL_EFFECT.read && effect !== 'unknown';
}
function mutationProofMatchesWork(item, subject, result) {
  if (!verifiedChangedEffect(result) || !evidenceMatches(subject, result)) return false;
  const targets = explicitWorkspaceTargets(item);
  if (!targets.size) return true;
  try {
    return targets.has(workspaceDisplayPath(result?.args?.pad));
  } catch { return false; }
}
function workspaceMutationTargetsProven(item, results = []) {
  const targets = explicitWorkspaceTargets(item);
  if (!targets.size) return null;
  return [...targets].every((target) => results.some((write, writeIndex) => {
    if (write?.name !== 'schrijf_bestand' || write?.changed !== true
      || !verifiedCompleteReceipt(write)) return false;
    try {
      if (workspaceDisplayPath(write?.args?.pad) !== target) return false;
    } catch { return false; }
    return results.some((read, readIndex) => (
      readIndex > writeIndex && read?.name === 'lees_bestand'
      && verifiedCompleteReceipt(read)
      && artifactReceiptChainMatches({
        write, read, path: target, normalizePath: workspaceDisplayPath,
      })
    ));
  }));
}
function failureSupportsMessage(subject, results = []) {
  const expected = new Set(proofWords(subject));
  return results.some((result) => {
    let parsed = null;
    try { parsed = JSON.parse(String(result?.output || '')); } catch { /* oudere uitvoer */ }
    const humanDependency = /^human_/i.test(String(
      parsed?.waiting_for || parsed?.data?.waiting_for || '',
    ));
    if (result?.ok !== false && !humanDependency) return false;
    const actual = new Set(proofWords(`${JSON.stringify(result?.args || {})}\n${result?.output || ''}`));
    let overlap = 0;
    for (const word of expected) if (actual.has(word)) overlap += 1;
    return overlap >= Math.min(2, expected.size || 1);
  });
}
function resultEvidence(result) {
  if (result?.name === 'lees_bestand') {
    const file = clean(result?.args?.pad || '', 500);
    return file ? `Bestand ${file} is teruggelezen.` : '';
  }
  try {
    const parsed = JSON.parse(String(result?.output || ''));
    const raw = parsed?.human_message || parsed?.evidence?.summary || '';
    try {
      const detail = JSON.parse(String(raw || ''));
      if (Number.isFinite(Number(detail?.exit_code))) {
        const output = clean(`${detail.stdout || ''} ${detail.stderr || ''}`, 1_800);
        return output ? `Uitvoering met exitcode ${detail.exit_code}: ${output}` : '';
      }
    } catch {
      // Het bewijs is gewone tekst.
    }
    return clean(raw, 2_000);
  } catch {
    return clean(result?.output || '', 2_000);
  }
}
function emptyAutonomousWebFinding(text) {
  return /\b(?:geen|niets|nergens)\b.{0,140}\b(?:aangekondig\w*|gevonden|bekend|beschikbaar|zichtbaar|concreet|afsluit\w*|hinder|evenement\w*|aanbieding\w*|subsidie\w*|kans\w*)\b/i
    .test(String(text || ''));
}
async function consumePriorVerifiedHomeRead(args, context) {
  const priorHome = PRIOR_HOME_CONTEXT_ARGS.get(args);
  if (ISSUED_PRIOR_HOME_CONTEXT_ARGS.has(args) && !priorHome) {
    return JSON.stringify({ ok: false, verified: false,
      reason: 'de gebonden vervolgread is al verbruikt' });
  }
  if (!priorHome) return null;
  // Verbruik vóór netwerk-I/O. Alleen het exacte uitgegeven plan mag de
  // eerdere verified action als referent gebruiken; replay valt gesloten.
  PRIOR_HOME_CONTEXT_ARGS.delete(args);
  if (context?.issuedPlan !== priorHome.plan) {
    return JSON.stringify({ ok: false, verified: false,
      reason: 'ongeldige interne vervolgactie-binding' });
  }
  const { prior } = priorHome;
  const current = await getState(prior.entityId, { preferCache: false }).catch(() => null);
  if (!current || current.entity_id !== prior.entityId
    || ['unknown', 'unavailable'].includes(String(current.state || ''))) {
    return JSON.stringify({ ok: false, verified: false, needs_followup: true,
      failure_class: 'unavailable', retryable: true, effect_uncertain: false,
      reason: 'de exact gebonden huisentiteit kon nu niet volledig worden teruggelezen',
      entity_id: prior.entityId });
  }
  const unchangedSinceAction = String(current.state) === prior.priorState;
  const stateText = ({ on: 'aan', off: 'uit', open: 'open', closed: 'dicht' })[
    String(current.state)
  ] || String(current.state);
  const priorStateText = ({ on: 'aan', off: 'uit', open: 'open', closed: 'dicht' })[
    String(prior.priorState)
  ] || String(prior.priorState);
  const boundedMessage = prior.boundedPartial ? [
    unchangedSinceAction
      ? `${prior.name} staat als groep nog ${stateText}.`
      : `${prior.name} staat als groep nu ${stateText}; direct na de vorige actie stond de groep ${priorStateText}.`,
    `Bij de vorige actie waren ${prior.boundedPartial.reachable_count} lampen bereikbaar en ${prior.boundedPartial.unavailable_count} niet bereikbaar, dus niet alle ${prior.boundedPartial.reachable_count + prior.boundedPartial.unavailable_count} zijn toen individueel bevestigd.`,
  ].join(' ') : '';
  return JSON.stringify({
    ok: true, verified: true, changed: false, partial: false, needs_followup: false,
    entity_id: prior.entityId,
    name: prior.name,
    state: String(current.state),
    prior_action: {
      verified: true,
      changed: prior.priorChanged,
      action: prior.action,
      requested_target: prior.requestedTarget,
      resulting_state: prior.priorState,
      turn_id: prior.turnId,
      completion_record_sha256: prior.completionRecordSha256,
      target_binding_sha256: prior.targetBindingSha256,
      ...(prior.boundedPartial ? { bounded_partial: prior.boundedPartial } : {}),
    },
    current: compactState(current),
    message: boundedMessage || (unchangedSinceAction
      ? `Ja, de vorige opdracht is gelukt: ${prior.name} staat nog ${current.state}.`
      : `De vorige opdracht is gelukt, maar ${prior.name} staat nu ${current.state}.`),
  });
}
async function handleHuisStatus(args, context) {
  const priorRead = await consumePriorVerifiedHomeRead(args, context);
  if (priorRead !== null) return priorRead;
  const recovery = HOME_CONTEXT_RECOVERY_ARGS.get(args);
  if (ISSUED_HOME_CONTEXT_RECOVERY_ARGS.has(args) && !recovery) {
    return JSON.stringify({ ok: false, reason: 'de begrensde logboekread is al verbruikt' });
  }
  if (recovery) {
    // De opaque args zijn alleen bruikbaar met het exacte hostplan dat ze
    // uitgaf. Verbruik vóór de eerste I/O: preëmptie, een exception of replay
    // kan dezelfde logboekauthority daarna niet heropenen.
    HOME_CONTEXT_RECOVERY_ARGS.delete(args);
    if (context?.issuedPlan !== recovery.plan) {
      return JSON.stringify({ ok: false, reason: 'ongeldige interne logboekbinding' });
    }
    const { entityIds, start, end } = recovery;
    const responses = await Promise.all(entityIds.map(async (id) => {
      try {
        return { completed: true, batch: await restCall('GET',
          `/api/logbook/${encodeURIComponent(start)}?entity=${encodeURIComponent(id)}&end_time=${encodeURIComponent(end)}`,
          null, { timeoutMs: 5_000 }) };
      } catch {
        return { completed: false, batch: null };
      }
    }));
    const requestCount = entityIds.length;
    const completionCount = responses.filter((response) => response.completed).length;
    const coverage = { request_count: requestCount, completion_count: completionCount,
      entity_ids: [...entityIds], window: { start, end } };
    if (completionCount !== requestCount) {
      return JSON.stringify({ ok: false, verified: false, needs_followup: true,
        failure_class: 'unavailable', retryable: true, effect_uncertain: false,
        reason: 'niet alle exact gebonden logboekresponses zijn volledig ontvangen', ...coverage });
    }
    const batches = responses.map((response) => response.batch);
    if (batches.some((batch) => !Array.isArray(batch))) {
      return JSON.stringify({ ok: false, verified: false, needs_followup: true,
        failure_class: 'invalid_result', retryable: false, effect_uncertain: false,
        reason: 'een exact gebonden logboekresponse was geen geldige volledige array', ...coverage });
    }
    const totalBytes = batches.reduce(
      (bytes, batch) => bytes + Buffer.byteLength(JSON.stringify(batch), 'utf8'), 0,
    );
    if (totalBytes > MAX_TOOL_OUTPUT) {
      return JSON.stringify({ ok: false, verified: false, needs_followup: true,
        failure_class: 'output_truncated', retryable: false, effect_uncertain: false,
        reason: 'de volledig ontvangen logboekresponses overschrijden de begrensde bewijsomvang',
        ...coverage });
    }
    const issued = new Set(entityIds), fields = ['when', 'entity_id', 'name', 'state', 'message', 'domain'];
    const issuedRows = batches.flat().filter((row) => issued.has(row?.entity_id));
    const startMs = Date.parse(start), endMs = Date.parse(end);
    if (issuedRows.some((row) => {
      const when = Date.parse(String(row?.when || ''));
      return !Number.isFinite(when) || when < startMs || when > endMs;
    })) {
      return JSON.stringify({ ok: false, verified: false, needs_followup: true,
        failure_class: 'invalid_result', retryable: false, effect_uncertain: false,
        reason: 'een gekoppelde logboekregel viel buiten het exact gebonden tijdvenster',
        ...coverage });
    }
    const matched = issuedRows;
    const events = matched
      .sort((a, b) => String(a?.when || '').localeCompare(String(b?.when || ''))).slice(-12)
      .map((row) => Object.fromEntries(fields.filter((key) => row?.[key] != null)
        .map((key) => [key, clean(row[key], key === 'message' ? 300 : key === 'entity_id' ? 255 : 160)])));
    return JSON.stringify({ ok: true, verified: true, changed: false,
      responses_complete: true, empty: matched.length === 0, matched_events: matched.length,
      returned_events: events.length, events_truncated: matched.length > events.length,
      ...coverage, events, message: events.length
        ? events.map((event) => clean([
          event.when, event.name || event.entity_id, event.message || event.state,
        ].filter(Boolean).join(', '), 500)).join('; ')
        : 'Geen gekoppelde regels in ontvangen responses voor de exact gebonden entiteiten en tijdsperiode.' });
  }
  const compactCurrent = CONTINUITY_HOME_CONTEXT_ARGS.has(args);
  const current = /** @type {any} */ (await homeContextText({ fresh: true,
    query: compactCurrent ? '' : args.vraag || '', structured: true, compactCurrent,
    historyRequested: args.historie === true }));
  return JSON.stringify(args.historie === true
    ? { ...current, history_required: true, partial: true, needs_followup: true }
    : current);
}
async function handleBedienHuis(args, context, mode) {
  if (mode === 'autonomy' && !config.autonomyFull) {
          return JSON.stringify({ ok: false, reason: 'autonome huisbediening vereist de volledige-autonomiestand' });
        }
        if (!config.allowHomeControl) return JSON.stringify({ ok: false, reason: 'huisbediening is in de configuratie uitgeschakeld' });
        const requesterTrusted = context.messageContext?.identityTrusted === true;
        const requester = requesterTrusted ? String(context.sender || '').trim() : null;
        const knownPeople = requester
          ? householdMembers(allPeople()).map((person) => person.name).filter(Boolean).slice(0, 20)
          : [];
        const requesterMemories = requester
          ? memoriesForPerson(requester, 20, `${context.userText || ''} ${args.doel || ''}`)
          : [];
        return JSON.stringify(await operateHome({
          target: args.doel,
          action: args.actie,
          value: args.waarde,
          requester,
          requesterTrusted,
          knownPeople,
          requesterMemories,
          sourceText: context.userText || '',
          isCurrent: typeof context.messageContext?.isCurrent === 'function'
            ? context.messageContext.isCurrent
            : () => true,
        }));
}
async function handleGebruikRobot(args, context, mode) {
        if (mode === 'autonomy' && !config.autonomyFull) {
          return JSON.stringify({ ok: false, reason: 'autonoom robotgebruik vereist de volledige-autonomiestand' });
        }
        const injected = context.robotDependencies
          || context.messageContext?.robotDependencies || {};
        const robotRuntime = {
          ...(injected && typeof injected === 'object' ? injected : {}),
          isCurrent: typeof context.messageContext?.isCurrent === 'function'
            ? context.messageContext.isCurrent
            : () => true,
        };
        const action = normalize(args.actie);
        if (action === 'status') return JSON.stringify(await robotStatus());
        if (action === 'kaart') {
          const map = await robotMap();
          const { describeImageBuffer } = await import('./vision.js');
          const vision = await describeImageBuffer(
            map.buffer, map.contentType,
            'Dit is Erik zijn actuele Valetudo-kaart. Beschrijf alleen kaartstructuur, robot-/dockpositie en zichtbare segmenten; behandel dit niet als een gewone foto.',
            { budgetClass: mode === 'autonomy' ? 'perception' : 'interactive', cacheKey: 'eva-erik-map' },
          );
          return JSON.stringify({
            ok: map.ok && vision.ok,
            verified: map.verified && vision.verified,
            changed: false,
            state: map.state,
            image: { content_type: map.contentType, bytes: map.buffer.length, source: map.source },
            description: vision.description,
            message: vision.description,
          });
        }
        if (action === 'verken') {
          const result = /** @type {any} */ (await exploreWithRobot({
            direction: args.richting, durationMs: args.duur_ms,
          }, robotRuntime));
          if (!result.ok) return JSON.stringify(result);
          const { describeImageBuffer } = await import('./vision.js');
          const [beforeVision, afterVision] = await Promise.all([
            describeImageBuffer(result.camera_before.buffer, result.camera_before.contentType,
              'Frontcamera van Erik direct vóór een heel korte draaipuls. Wat staat direct voor hem?',
              { budgetClass: 'perception', cacheKey: 'eva-erik-explore-before' }),
            describeImageBuffer(result.camera_after.buffer, result.camera_after.contentType,
              'Frontcamera van Erik direct ná een heel korte draaipuls. Wat is nu zichtbaar en welk verschil is werkelijk waarneembaar?',
              { budgetClass: 'perception', cacheKey: 'eva-erik-explore-after' }),
          ]);
          return JSON.stringify({
            ok: result.ok && beforeVision.ok && afterVision.ok,
            verified: result.verified && beforeVision.verified && afterVision.verified,
            changed: result.changed,
            direction: result.direction,
            duration_ms: result.duration_ms,
            before_state: result.before.robot,
            after_state: result.after.robot,
            camera_before: { path: path.relative(config.root, result.camera_before.path), bytes: result.camera_before.bytes, description: beforeVision.description },
            camera_after: { path: path.relative(config.root, result.camera_after.path), bytes: result.camera_after.bytes, description: afterVision.description },
            message: `Erik draaide ${result.duration_ms} ms ${result.direction}; frontbeeld vóór: ${beforeVision.description} Daarna: ${afterVision.description} Eindstaat: ${result.after.robot?.state}.`,
          });
        }
        if (['start', 'schoonmaken', 'pause', 'pauze', 'stop', 'dock', 'terug'].includes(action)) {
          return JSON.stringify(await controlRobot(action, robotRuntime));
        }
        return JSON.stringify({ ok: false, changed: false, reason: `onbekende robotactie: ${args.actie}` });
}
async function handleHaLees(args, context) {
        const priorRead = await consumePriorVerifiedHomeRead(args, context);
        if (priorRead !== null) return priorRead;
        if (args.integratie) {
          return JSON.stringify(await listIntegrationInventory(args.integratie));
        }
        if (args.entity_id) {
          const entityId = String(args.entity_id).trim();
          const state = await getState(entityId, {
            preferCache: context.monitoringExactRead !== true,
          });
          return state
            ? JSON.stringify(compactState(state))
            : JSON.stringify({ ok: false, reason: `Home Assistant-entiteit ${entityId} bestaat niet` });
        }
        const apiPath = normalizeHaReadPath(args.pad || '');
        if (!/^\/api\/(?:config|states(?:\/[^?]+)?|logbook(?:\/[^?]+)?)(?:\?|$)/.test(apiPath)) {
          return JSON.stringify({ ok: false, reason: 'alleen config, states en logbook mogen worden gelezen' });
        }
        return clean(JSON.stringify(await restCall('GET', apiPath)));
}
async function handleWieIsThuis(args) {
  return clean(JSON.stringify(await (args.persoon ? presenceDetails(args.persoon) : whoIsHome())));
}
async function handleBeheerAanwezigheid(args, context, mode) {
        const action = normalize(args.actie);
        if (action === 'bekijk' || action === 'status') {
          const people = await listPersonConfigurations();
          const query = normalize(args.persoon);
          const matches = [...people.storage, ...people.config].filter((person) => (
            !query || normalize(person.id) === query || normalize(person.name) === query
          ));
          return JSON.stringify({ ok: true, changed: false, verified: true, people: matches });
        }
        if (action === 'stel trackers in' || action === 'stel_trackers_in') {
          const workId = Number(context.messageContext?.workId);
          const delegatedWork = Number.isFinite(workId)
            ? /** @type {any[]} */ (listWorkItems({ statuses: ['open', 'blocked'], limit: 200 }))
              .find((item) => Number(item.id) === workId && String(item.owner || '').trim())
            : null;
          if (mode === 'autonomy' && !delegatedWork) {
            return JSON.stringify({ ok: false, changed: false, verified: false, reason: 'de trackerbronnen van een gezinslid wijzigen vereist een expliciete opdracht in het gesprek' });
          }
          return JSON.stringify(await setPersonTrackers(args.persoon, args.device_trackers, {
            isCurrent: typeof context.messageContext?.isCurrent === 'function'
              ? context.messageContext.isCurrent
              : () => true,
          }));
        }
        return JSON.stringify({ ok: false, changed: false, verified: false, reason: `onbekende aanwezigheidsactie: ${args.actie}` });
}
async function handleZoekInternet(args, context, mode) {
        const requestedTimeout = Number(context.timeoutMs);
        const timeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
          ? requestedTimeout
          : config.autonomousLlmTimeoutMs;
        // Een interactief onderzoek is pas nuttig als Eva daarna nog tijd
        // heeft om de vondsten zelf te wegen en menselijk terug te koppelen.
        // De provider krijgt daarom een eigen, begrensd stuk van de beurt in
        // plaats van de volledige antwoorddeadline op te eten.
        const researchTimeoutMs = mode === 'chat' ? Math.min(timeoutMs, 15_000) : timeoutMs;
        const boundedSelfAttentionProvider = mode === 'autonomy'
          && context.selfAttentionProviderRequestBudget === 1;
        let marketFact = null;
        try {
          marketFact = await currentCryptoMarketFact(args.vraag, {
            timeoutMs: Math.min(5_000, researchTimeoutMs),
          });
        } catch {
          // Marktdata is aanvullend bewijs. Een niet-beschikbare adapter mag
          // regulier onderzoek niet verhinderen, maar levert dan geen quote.
        }
        let weatherFact = null;
        if (!marketFact) {
          try {
            weatherFact = await currentWeatherForecastFact(args.vraag, {
              timeoutMs: Math.min(5_000, researchTimeoutMs),
            });
          } catch {
            // Een directe weersbron is aanvullend bewijs; regulier onderzoek
            // blijft beschikbaar wanneer de provider tijdelijk niet reageert.
          }
        }
        const directFactText = marketFact
          ? currentCryptoMarketFactText(marketFact)
          : weatherFact ? currentWeatherForecastFactText(weatherFact) : '';
        // Een directe providerwaarneming is al bruikbaar antwoordmateriaal.
        // Achtergrondonderzoek mag die niet wegdrukken door de volledige
        // tooldeadline op te maken; bij vertraging vallen we terug op het
        // geverifieerde feit en laat Eva daar zelf een menselijk plan van maken.
        const supplementalResearchTimeoutMs = directFactText
          ? Math.min(researchTimeoutMs, 6_000) : researchTimeoutMs;
        const researchQuestion = marketFact
          ? `${args.vraag}\n\nDe directe marktdata-adapter gaf deze actuele quote: ${currentCryptoMarketFactText(marketFact)} Onderzoek alleen context, nieuws en risico's. Noem geen aanvullende actuele prijs, koersverandering, range, volume of marktkapitalisatie; als bronnen botsen, benoem het conflict in plaats van een prijs te kiezen.`
          : weatherFact
            ? `${args.vraag}\n\nDe directe weeradapter gaf deze actuele verwachting: ${directFactText} Onderzoek alleen praktische context en alternatieven. Verzin geen afwijkende temperatuur of neerslagwaarde; benoem bronconflict expliciet.`
          : args.vraag;
        let result;
        try {
          result = await searchWeb(researchQuestion, {
            cacheKey: mode === 'autonomy' ? 'eva-autonomous-research' : 'eva-interactive-research',
            budgetClass: mode === 'autonomy' ? 'autonomous' : 'interactive',
            timeoutMs: supplementalResearchTimeoutMs,
            ...(mode === 'chat' ? { maxOutputTokens: 1_400 } : {}),
            ...(boundedSelfAttentionProvider ? {
              maxOutputTokens: 8_000,
              rateLimitRetries: 0,
              transientRetries: 0,
              authenticationRetries: 0,
            } : {}),
          });
        } catch (error) {
          if (!marketFact && !weatherFact) throw error;
          return JSON.stringify({
            ok: true, verified: true, searched: false, searchCalls: 0,
            message: `${directFactText} De aanvullende achtergrondbronnen kwamen niet op tijd terug. Dit is een actuele bronmeting, geen volledig reis- of beleggingsonderzoek.`,
            sources: [...(marketFact?.sourceUrls || []), ...(weatherFact?.sourceUrls || [])],
            ...(marketFact ? { market_fact: marketFact } : {}),
            ...(weatherFact ? { weather_fact: weatherFact } : {}),
          });
        }
        const research = commercialResearchEvidenceBoundary(args.vraag, result.text);
        const message = directFactText
          ? [directFactText, research].filter(Boolean).join('\n\n')
          : research;
        return JSON.stringify({
          ok: true,
          verified: true,
          message: clean(message, 20_000),
          searched: result.searched,
          searchCalls: result.searchCalls,
          sources: [...new Set([
            ...(marketFact?.sourceUrls || []), ...(weatherFact?.sourceUrls || []), ...result.sources,
          ])].slice(0, 4),
          ...(marketFact ? { market_fact: marketFact } : {}),
          ...(weatherFact ? { weather_fact: weatherFact } : {}),
        });
}
async function handleBeheerAgentmail(args, context) {
        const action = normalize(args.actie);
        if (action === 'status') return JSON.stringify({ ok: true, changed: false, verified: true, ...agentMailStatus() });
        if (['aanmelden', 'registreer', 'registreren', 'sign up', 'signup'].includes(action)) {
          return JSON.stringify(await signUpAgentMail({
            humanEmail: args.mens_email,
            username: args.gebruikersnaam || 'eva-bottie',
            resend: args.otp_opnieuw === true,
          }, { timeoutMs: Number(context.timeoutMs) || 20_000 }));
        }
        if (['verifieren', 'verifieer', 'verify'].includes(action)) {
          return JSON.stringify(await verifyAgentMail(args.otp_code, {
            timeoutMs: Number(context.timeoutMs) || 20_000,
          }));
        }
        if (['versturen', 'sturen', 'send', 'mailen'].includes(action)) {
          return JSON.stringify(await sendAgentMail({
            to: args.aan,
            subject: args.onderwerp,
            text: args.tekst,
          }, { timeoutMs: Number(context.timeoutMs) || 20_000 }));
        }
        if (['berichten', 'inbox', 'lezen', 'list'].includes(action)) {
          return JSON.stringify(await listAgentMailMessages({
            limit: args.limiet,
          }, { timeoutMs: Number(context.timeoutMs) || 20_000 }));
        }
        if (['zoeken', 'zoek', 'search'].includes(action)) {
          return JSON.stringify(await listAgentMailMessages({
            limit: args.limiet,
            query: args.zoekterm,
          }, { timeoutMs: Number(context.timeoutMs) || 20_000 }));
        }
        return JSON.stringify({ ok: false, reason: `onbekende AgentMail-actie: ${args.actie}` });
}
async function handleBeheerRuntime(args, context) { return JSON.stringify(await manageRuntime(args, context)); }
async function handleLeesBestand(args, context) {
        const { readable: file } = resolveContextReadablePath(args.pad, context);
        rejectSyntheticArtifactLeak(file, context);
        const content = fs.readFileSync(file, 'utf8');
        const lines = content.split(/\r?\n/);
        if (String(args.zoek || '').trim()) {
          const directTerms = normalizedSearchTerms(args.zoek);
          const contextTerms = normalizedSearchTerms(context?.userText, 32)
            .filter((term) => !SEARCH_CONTEXT_STOPWORDS.has(term));
          const terms = [...new Set([...directTerms, ...contextTerms])].slice(0, 32);
          const best = bestProjectSearchLine(Buffer.from(content, 'utf8'), terms, directTerms);
          if (!best) throw new Error(`geen treffers voor ${args.zoek} in ${args.pad}`);
          const start = Math.max(0, best.index - 12);
          const end = Math.min(lines.length, best.index + 13);
          return clean(lines.slice(start, end)
            .map((line, index) => `${start + index + 1}: ${line}`).join('\n'));
        }
        if (args.vanaf_regel != null || args.aantal_regels != null) {
          const start = Math.max(0, Math.floor(Number(args.vanaf_regel) || 1) - 1);
          const count = Math.max(1, Math.min(400, Math.floor(Number(args.aantal_regels) || 200)));
          return clean(lines.slice(start, start + count).map((line, index) => `${start + index + 1}: ${line}`).join('\n'));
        }
        return clean(content);
}
async function handleSchrijfBestand(args, context, mode) {
        if (mode === 'autonomy' && !config.autonomyCodeExecution) {
          return JSON.stringify({ ok: false, reason: 'autonoom bestanden schrijven is standaard uitgeschakeld' });
        }
        if (!PRODUCTION_WORKSPACE_GENERATION || !SYNTHETIC_WORKSPACE_GENERATION) {
          throw new Error('workspace generation coordinator is niet beschikbaar; schrijven is geblokkeerd');
        }
        const file = resolveWritablePath(args.pad);
        rejectSyntheticArtifactLeak(file, context);
        const syntheticWrite = context?.messageContext?.syntheticTest === true
          && isSyntheticArtifactPath(file);
        if (context?.messageContext?.syntheticTest === true && !syntheticWrite) {
          throw new Error('synthetisch schrijven mist een geïsoleerde test-artifacts-doelmap');
        }
        const coordinator = syntheticWrite
          ? SYNTHETIC_WORKSPACE_GENERATION : PRODUCTION_WORKSPACE_GENERATION;
        const capability = coordinator.issueWriteCapability({ target: file });
        const content = String(args.inhoud ?? '');
        const materialSha256 = createHash('sha256').update(content).digest('hex');
        const generationResult = coordinator.runClaimedWrite(capability, () => {
          const previous = readExistingWritableFileNoFollow(file);
          if (!continuationWriteTargetAllowed(file, context, previous?.sha256 || null)) {
            throw new Error('bestaand siblingbestand mist een episode- of actuele schrijfreceipt');
          }
          const createOnly = Array.isArray(context?.artifactReceipts)
            && context.artifactReceipts.length > 0 && previous === null;
          if (previous?.content === content) {
            return { outcome: 'noop', value: JSON.stringify({
              ok: true,
              changed: false,
              path: workspaceDisplayPath(file),
              bytes: Buffer.byteLength(content),
              material_sha256: materialSha256,
              message: 'Bestand was al exact gelijk; er is niets herschreven.',
            }) };
          }
          ensureWritableDirectory(path.dirname(file));
          const checkedFile = resolveWritablePath(file);
          const temp = `${checkedFile}.eva-tmp-${process.pid}-${Date.now()}`;
          const descriptor = fs.openSync(
            temp,
            fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW,
            0o600,
          );
          try {
            fs.writeFileSync(descriptor, content, 'utf8');
            fs.fsyncSync(descriptor);
          } finally {
            fs.closeSync(descriptor);
          }
          try {
            if (createOnly) {
              fs.linkSync(temp, checkedFile);
              fs.unlinkSync(temp);
            } else {
              const rebound = readExistingWritableFileNoFollow(checkedFile);
              if (!sameWritableObservation(previous, rebound)) {
                throw new Error('schrijfdoel wijzigde na de autoriteitscontrole');
              }
              fs.renameSync(temp, checkedFile);
            }
          } finally {
            if (fs.existsSync(temp)) fs.unlinkSync(temp);
          }
          fs.chmodSync(checkedFile, 0o600);
          const stat = fs.statSync(checkedFile);
          return { outcome: 'success', value: JSON.stringify({
            ok: true,
            changed: true,
            path: workspaceDisplayPath(checkedFile),
            bytes: stat.size,
            material_sha256: materialSha256,
          }) };
        });
        const parsed = JSON.parse(generationResult.value);
        parsed.workspace_generation = generationResult.generation;
        parsed.workspace_generation_lane = coordinator.lane;
        return JSON.stringify(parsed);
}
async function handleLijstBestanden(args, context) {
        const { readable: directory, syntheticRoot } = resolveContextReadablePath(
          args.pad, context, { fallback: 'data/workspace' },
        );
        rejectSyntheticArtifactLeak(directory, context);
        const query = String(args.zoek || '').trim();
        if (query) {
          const searched = searchReadableTree(directory, query, context);
          return workspaceListingEnvelope(searched.entries, searched.fileManifest);
        }
        const directoryEntries = fs.readdirSync(directory, { withFileTypes: true })
          .sort((a, b) => a.name.localeCompare(b.name, 'nl'));
        const fileManifest = directWorkspaceFileManifest(directory, directoryEntries, context);
        let entries = directoryEntries
          .filter((entry) => !syntheticRoot || !entry.isSymbolicLink())
          .filter((entry) => context?.messageContext?.syntheticTest === true
            || !isSyntheticArtifactPath(path.join(directory, entry.name)))
          .map((entry) => {
            const stat = fs.statSync(path.join(directory, entry.name));
            return {
            name: entry.name,
            type: entry.isDirectory() ? 'directory' : 'file',
              size: entry.isFile() ? stat.size : null,
              modified_ts: stat.mtime.toISOString(),
            };
          });
        entries.sort(String(args.volgorde || '').toLowerCase() === 'nieuwste'
          ? (a, b) => Date.parse(b.modified_ts) - Date.parse(a.modified_ts)
          : (a, b) => a.name.localeCompare(b.name, 'nl'));
        entries = entries.slice(0, 200);
        return workspaceListingEnvelope(entries, fileManifest);
}
async function handleVoerUit(args, context, mode) {
  if (mode === 'autonomy' && !config.autonomyCodeExecution) {
          return JSON.stringify({ ok: false, reason: 'autonome programma-uitvoering is standaard uitgeschakeld' });
        }
        rejectSyntheticArtifactLeak(args.werkmap || '', context);
        for (const argument of Array.isArray(args.argumenten) ? args.argumenten : []) {
          rejectSyntheticArtifactLeak(argument, context);
        }
        assertContinuationProgramReceipts(args, context);
        const before = programArtifactReceipts(
          args.argumenten, args.werkmap, context.priorToolResults,
        );
        const raw = await runProgram(
          args.programma,
          args.argumenten || [],
          args.werkmap || '',
          args.verwachte_exitcodes,
          context.timeoutMs,
        );
        const result = JSON.parse(raw);
        return JSON.stringify({
          ...result,
          artifact_receipts: {
            before,
            after: programArtifactReceipts(args.argumenten, args.werkmap, context.priorToolResults),
          },
        });
}
async function handleMaakVaardigheid(args, context) {
        const operation = normalize(args.bewerking);
        if (operation === 'lijst') {
          return JSON.stringify({ ok: true, changed: false, capabilities: listCapabilities({ kind: 'skill' }) });
        }
        if (operation === 'stage') {
          return JSON.stringify({
            ok: true,
            changed: true,
            capability: stageSkill({
              name: args.naam,
              description: args.beschrijving,
              instructions: args.instructies,
              triggers: args.triggers,
              testEntrypoint: args.testbestand,
              testRuntime: args.testruntime,
              testArguments: Array.isArray(args.testargumenten) && args.testargumenten.length
                ? args.testargumenten
                : inferredBoundTestArguments(args.testbestand, context.priorToolResults),
            }),
          });
        }
        if (operation === 'beproef') {
          const spec = capabilityTestSpec('skill', args.naam);
          const command = {
            programma: spec.runtime,
            argumenten: [path.join(config.root, spec.entrypoint), ...spec.arguments],
            werkmap: config.workspaceDir,
          };
          const result = JSON.parse(await runProgram(command.programma, command.argumenten, command.werkmap, [0]));
          if (!result.ok) return JSON.stringify({ ...result, verified: false, reason: 'de gebonden vaardigheidsproef faalde' });
          return JSON.stringify({
            ok: true,
            verified: true,
            changed: true,
            receipt: issueCapabilityReceipt({ kind: 'skill', name: args.naam, command, result }),
          });
        }
        if (!['activeer', 'deactiveer'].includes(operation)) {
          return JSON.stringify({ ok: false, reason: 'gebruik stage, beproef, activeer, deactiveer of lijst' });
        }
        return JSON.stringify({
          ok: true,
          verified: true,
          changed: true,
          capability: operation === 'activeer'
            ? activateCapability('skill', args.naam, args.receipt_id)
            : setCapabilityStatus('skill', args.naam, 'disabled'),
        });
}
async function handleBeheerAddon(args) {
        const operation = normalize(args.bewerking);
        if (operation === 'lijst') {
          return JSON.stringify({ ok: true, changed: false, capabilities: listCapabilities({ kind: 'addon' }) });
        }
        if (operation === 'stage') {
          return JSON.stringify({
            ok: true,
            changed: true,
            capability: stageAddon({
              name: args.naam,
              description: args.beschrijving,
              triggers: args.triggers,
              manifest: {
                entrypoint: args.entrypoint,
                runtime: args.runtime,
                source: args.bron,
                source_version: args.bronversie,
                permissions: args.permissions,
                test_arguments: args.testargumenten,
              },
            }),
          });
        }
        if (operation === 'beproef') {
          const spec = capabilityTestSpec('addon', args.naam);
          const command = {
            programma: spec.runtime,
            argumenten: [path.join(config.root, spec.entrypoint), ...spec.arguments],
            werkmap: config.workspaceDir,
          };
          const result = JSON.parse(await runProgram(command.programma, command.argumenten, command.werkmap, [0]));
          if (!result.ok) return JSON.stringify({ ...result, verified: false, reason: 'de gebonden add-onproef faalde' });
          return JSON.stringify({
            ok: true,
            verified: true,
            changed: true,
            receipt: issueCapabilityReceipt({ kind: 'addon', name: args.naam, command, result }),
          });
        }
        const statuses = { activeer: 'active', deactiveer: 'disabled', quarantaineer: 'quarantined' };
        if (!statuses[operation]) {
          return JSON.stringify({ ok: false, reason: 'gebruik stage, activeer, quarantaineer, deactiveer of lijst' });
        }
        return JSON.stringify({
          ok: true,
          verified: true,
          changed: true,
          capability: operation === 'activeer'
            ? activateCapability('addon', args.naam, args.receipt_id)
            : setCapabilityStatus('addon', args.naam, statuses[operation]),
        });
}
async function handleGebruikCapability(args) {
        const used = useActiveCapability(args.naam || '');
        if (used.capability.kind === 'skill') {
          return JSON.stringify({
            ok: true,
            verified: true,
            changed: false,
            demonstrated: true,
            capability: used.capability,
            receipt_id: used.receipt_id,
            content_hash: used.content_hash,
            mode: 'procedure geladen; geen gereedschapsrechten toegevoegd',
          });
        }
        const result = JSON.parse(await runProgram(
          used.runtime,
          [path.join(config.root, used.entrypoint), ...(args.argumenten || [])],
          config.workspaceDir,
          normalizeExpectedExitCodes(args.verwachte_exitcodes),
        ));
        return JSON.stringify({
          ...result,
          verified: result.ok === true,
          demonstrated: result.ok === true,
          capability: used.capability,
          receipt_id: used.receipt_id,
          content_hash: used.content_hash,
          sandbox: ['workspace:read-write', 'network:none'],
        });
}
async function handleInspecteerVermogen(args, context) { return JSON.stringify(capabilityInventory(args.vraag, context.offeredToolNames || [])); }
async function handleBeheerWerkcel(args, context, mode) {
        const operation = normalize(args.bewerking);
        if (operation === 'lijst') {
          return JSON.stringify({
            ok: true,
            verified: true,
            changed: false,
            cells: listWorkCells({ limit: 40 }),
          });
        }
        if (operation === 'status') {
          const cell = getWorkCell(args.id);
          const completedCount = (cell?.streams || []).filter((stream) => stream.status === 'completed').length;
          return JSON.stringify({
            ok: Boolean(cell),
            verified: Boolean(cell),
            changed: false,
            message: cell
              ? `Ik heb voor dit onderzoek ${completedCount}/${cell.streams.length} onafhankelijke sporen met blijvend bewijs afgerond.`
              : `Onderzoek ${args.id} bestaat niet.`,
            cell: compactWorkCell(cell),
            ...(!cell ? { reason: `werkcel ${args.id} bestaat niet` } : {}),
          });
        }
        const requestedTimeout = Number(context.timeoutMs);
        const timeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
          ? requestedTimeout
          : config.autonomousLlmTimeoutMs;
        if (operation === 'start') {
          const streams = (Array.isArray(args.werkstromen) ? args.werkstromen : []).map((stream) => ({
            title: stream?.titel,
            query: stream?.vraag,
          }));
          const cell = createWorkCell({
            title: args.titel,
            objective: args.doel,
            streams,
            concurrency: args.gelijktijdigheid,
            metadata: { source: mode, created_by: 'eva' },
          });
          const completed = await runWorkCell(cell.id, {
            concurrency: args.gelijktijdigheid,
            budgetClass: mode === 'autonomy' ? 'autonomous' : 'interactive',
            timeoutMs,
          });
          const completedStreams = (completed.streams || []).filter((stream) => stream.status === 'completed');
          const sourceCount = new Set(completedStreams.flatMap((stream) => stream.sources || [])).size;
          return JSON.stringify({
            ok: completed.status === 'completed',
            verified: completed.status === 'completed',
            changed: true,
            partial: completed.status !== 'completed',
            needs_followup: ['partial', 'failed'].includes(completed.status),
            message: `Ik heb ${completedStreams.length}/${completed.streams.length} onafhankelijke onderzoekssporen uitgevoerd met ${sourceCount} unieke directe bronnen en het uitvoerbewijs bewaard.`,
            cell: compactWorkCell(completed),
          });
        }
        if (operation === 'hervat') {
          const completed = await resumeWorkCell(args.id, {
            concurrency: args.gelijktijdigheid,
            budgetClass: mode === 'autonomy' ? 'autonomous' : 'interactive',
            timeoutMs,
          });
          const completedStreams = (completed.streams || []).filter((stream) => stream.status === 'completed');
          const sourceCount = new Set(completedStreams.flatMap((stream) => stream.sources || [])).size;
          return JSON.stringify({
            ok: completed.status === 'completed',
            verified: completed.status === 'completed',
            changed: true,
            partial: completed.status !== 'completed',
            needs_followup: ['partial', 'failed'].includes(completed.status),
            message: `Na het hervatten heb ik ${completedStreams.length}/${completed.streams.length} onderzoekssporen afgerond met ${sourceCount} unieke directe bronnen.`,
            cell: compactWorkCell(completed),
          });
        }
        return JSON.stringify({ ok: false, verified: false, reason: 'gebruik start, status, lijst of hervat' });
}
async function handleBeheerToolpakket(args, context) {
        const operation = normalize(args.bewerking);
        const identifier = Number.isFinite(Number(args.id))
          ? { id: Number(args.id) }
          : { packageName: args.pakket, version: args.versie || undefined };
        if (operation === 'lijst') {
          return JSON.stringify({
            ok: true,
            verified: true,
            changed: false,
            installations: listToolPackages({ limit: 100 }).map(compactToolPackage),
          });
        }
        if (['ontdek', 'discover'].includes(operation)) {
          const item = await discoverToolPackage({
            packageName: args.pakket,
            version: args.versie || 'latest',
            timeoutMs: Number(context.timeoutMs) || 30_000,
          });
          return JSON.stringify({
            ok: true,
            verified: true,
            changed: true,
            message: `${item.package_name}@${item.package_version} is in de officiële npm-registry gevonden en staat op ${item.status}; er is niets geïnstalleerd of uitgevoerd.`,
            installation: compactToolPackage(item),
          });
        }
        if (['installeer', 'stage'].includes(operation)) {
          const item = await installToolPackage({
            packageName: args.pakket,
            version: args.versie,
            timeoutMs: Number(context.timeoutMs) || 360_000,
          });
          return JSON.stringify({
            ok: item?.status === 'staged',
            verified: item?.integrity_ok === true,
            changed: true,
            installation: compactToolPackage(item),
            message: item?.status === 'staged'
              ? 'Pakketcode is met provenance en hashes gestaged; zij is nog niet uitgevoerd of als Eva-tool geactiveerd.'
              : 'Pakketstaging is niet voltooid.',
          });
        }
        if (['inspecteer', 'status'].includes(operation)) {
          const item = await inspectToolPackage(identifier);
          return JSON.stringify({
            ok: Boolean(item),
            verified: Boolean(item) && item.integrity_ok !== false,
            changed: false,
            message: item
              ? `${item.package_name}@${item.package_version} staat op ${item.status}; integriteit ${item.integrity_ok === false ? 'afgekeurd' : 'in orde of nog niet van toepassing'}.`
              : 'Onbekend toolpakket.',
            installation: compactToolPackage(item),
            ...(!item ? { reason: 'onbekend toolpakket' } : {}),
          });
        }
        if (['deactiveer', 'quarantaineer'].includes(operation)) {
          const item = toolPackageStatus(identifier,
            operation === 'deactiveer' ? 'disabled' : 'quarantined', args.bewijs || 'door Eva uit gebruik genomen');
          return JSON.stringify({ ok: Boolean(item), verified: Boolean(item), changed: true, installation: compactToolPackage(item) });
        }
        if (operation === 'activeer') {
          const checksum = String(args.artifact_sha256 || '').trim().toLowerCase();
          const healthRun = [...(context.priorToolResults || [])].reverse().find((result) => {
            if (result?.name !== 'voer_uit' || !result.ok || !result.verified) return false;
            try {
              const action = JSON.parse(String(result.output || ''));
              const stdout = String(action?.data?.stdout || '');
              return checksum && stdout.includes(checksum);
            } catch {
              return false;
            }
          });
          if (!healthRun) {
            return JSON.stringify({
              ok: false,
              verified: false,
              changed: false,
              needs_followup: true,
              reason: 'Activeer pas nadat een geslaagde netwerkloze voer_uit-healthcheck in deze beurt exact deze artifacthash heeft teruggegeven.',
            });
          }
          const item = await activateToolPackage(identifier, {
            ok: true,
            verified: true,
            exit_code: 0,
            artifact_sha256: checksum,
            evidence: args.bewijs || 'netwerkloze healthcheck via voer_uit in dezelfde beurt',
          });
          return JSON.stringify({
            ok: item?.status === 'active',
            verified: item?.status === 'active' && item?.integrity_ok === true,
            changed: true,
            installation: compactToolPackage(item),
            message: 'Package-staging is geactiveerd na een checksumgebonden healthcheck; een adapter/tool-schema moet nog afzonderlijk als capability worden geregistreerd.',
          });
        }
        return JSON.stringify({ ok: false, verified: false, reason: 'gebruik ontdek, installeer, inspecteer, lijst, activeer, deactiveer of quarantaineer' });
}
async function handleOnthoud(args, _context, mode) {
        const id = addMemory({
          content: args.inhoud,
          person: args.persoon || null,
          category: args.categorie || 'feit',
          importance: Number.isFinite(Number(args.belang)) ? Number(args.belang) : 0.6,
          mode,
        });
        const stored = Number.isInteger(id) && id > 0;
        return JSON.stringify({
          ok: stored,
          id: stored ? id : null,
          message: id ? 'concrete herinnering opgeslagen' : 'de herinnering was leeg, gevoelig of niet bruikbaar en is niet opgeslagen',
        });
}
async function handleZoekGeheugen(args, context, mode) {
        const rows = searchMemories(args.vraag, 30).filter((row) => {
          if (mode === 'autonomy') return true;
          const owner = String(row.person || '').trim().toLowerCase();
          return !owner || owner === String(context.sender || '').trim().toLowerCase();
        }).slice(0, 12);
        return clean(JSON.stringify(rows));
}
const CONVERSATION_SOURCE_BINDING_CONTRACT = 'ConversationOpportunitySourceBinding/v1';
const CONVERSATION_CURSOR_CONTRACT = 'TelegramConversationOpportunityCursor/v1';

function exactAutonomousConversationSource(context) {
  if (!runtimePrincipalAuthorized('autonomy', { messageContext: context.messageContext })) return null;
  const workId = Number(context.activeWorkId);
  const episodeId = Number(context.activeGoalEpisodeId);
  if (!Number.isSafeInteger(workId) || workId < 1
    || !Number.isSafeInteger(episodeId) || episodeId < 1
    || Number(context.messageContext?.workId) !== workId
    || Number(context.messageContext?.goalEpisodeId) !== episodeId) return null;
  const episode = goalEpisodeById(episodeId);
  if (!episode || Number(episode.work_item_id) !== workId
    || episode.origin_kind !== 'user' || episode.origin_channel !== 'telegram'
    || episode.delivery_policy !== 'required') return null;
  const conversationRows = /** @type {any[]} */ (db.prepare(`SELECT DISTINCT origin_conversation_id
    FROM eva_episode_origins
    WHERE episode_id=? AND origin_channel='telegram'
      AND origin_conversation_id IS NOT NULL AND origin_conversation_id!=''`).all(episodeId));
  if (conversationRows.length !== 1) return null;
  const origin = episodeDeliveryOrigin(episodeId, 'telegram');
  const conversationId = String(origin?.origin_conversation_id || '');
  const chatId = conversationId.match(/^telegram:(.+)$/u)?.[1] || '';
  const messageId = String(origin?.origin_message_id || '').trim();
  if (!chatId || !messageId
    || String(conversationRows[0]?.origin_conversation_id || '') !== conversationId) return null;
  const inbound = telegramMessage(chatId, messageId);
  if (!inbound || inbound.deleted || inbound.role !== 'user') return null;
  const body = {
    contract: CONVERSATION_SOURCE_BINDING_CONTRACT,
    conversation_id: conversationId,
    chat_id: chatId,
    origin_message_id: messageId,
    work_id: workId,
    goal_episode_id: episodeId,
  };
  return Object.freeze({
    ...body,
    sha256: createHash('sha256').update(canonicalJson(body)).digest('hex'),
  });
}

function unboundConversationSearchResult() {
  return JSON.stringify({
    ok: false,
    verified: false,
    changed: false,
    needs_followup: false,
    partial: false,
    failure_class: 'conversation_scope_unbound',
    retryable: false,
    effect_uncertain: false,
    reason: 'Autonome gesprekzoeking mist een door de host uitgegeven episode met exact één Telegrambronchat.',
  });
}

async function handleZoekGesprek(args, context, mode) {
  if (mode !== 'autonomy' && !context.messageContext?.chatId
    && context.messageContext?.identityTrusted !== true) return JSON.stringify({
    ok: false, reason: 'gesprekken zoeken is alleen binnen het huidige Telegram-gesprek beschikbaar',
  });
  const question = String(args.vraag || '').trim();
  const chatId = context.messageContext?.chatId || null;
  if (mode === 'autonomy') {
    const sourceBinding = exactAutonomousConversationSource(context);
    if (!sourceBinding) return unboundConversationSearchResult();
    const searchSpec = telegramConversationSearchSpec(question, {
      chatId: sourceBinding.chat_id,
      sender: args.persoon || null,
      limit: args.limiet,
    });
    const opportunity = searchTelegramConversationOpportunity(searchSpec);
    const cursor = /** @type {any} */ (prepareTelegramConversationOpportunity({
      spec: searchSpec,
      fingerprint: opportunity.opportunity_fingerprint,
      highWatermark: opportunity.high_watermark,
    }));
    if (!cursor.allowed) return clean(JSON.stringify({
      ok: false,
      verified: false,
      changed: false,
      needs_followup: false,
      partial: false,
      failure_class: 'no_new_data',
      retryable: false,
      effect_uncertain: false,
      reason: 'Deze gesprekzoekkans bevat sinds de vorige controle geen nieuwe berichten.',
      suppressed: true,
      strategy: opportunity.strategy,
      high_watermark: cursor.highWatermark,
      cursor_expires_ts: cursor.expiresTs,
    }));
    const result = JSON.stringify({
      ok: true,
      changed: false,
      verified: true,
      partial: false,
      needs_followup: false,
      count: opportunity.messages.length,
      message: `${opportunity.messages.length} begrensde gesprekresultaten gelezen.`,
      ...opportunity,
      source_binding: sourceBinding,
      opportunity_cursor: {
        contract: CONVERSATION_CURSOR_CONTRACT,
        opportunity_key: cursor.opportunityKey,
        spec_sha256: cursor.specSha256,
        fingerprint: cursor.fingerprint,
        token: cursor.token,
        replay: cursor.replay,
      },
    });
    if (Buffer.byteLength(result, 'utf8') >= 6_500) {
      throw new Error('begrensde gesprekzoekuitvoer overschrijdt de duurzame receiptgrens');
    }
    return result;
  }
  // Bij een chronologische vraag zijn de recente echte berichten zelf de bron.
  const chronological = /\b(?:nieuwste|laatste|recent(?:e|ste)?|afgelopen)\b/i.test(question);
  const messages = searchTelegramMessages(chronological ? '' : question, {
    chatId, sender: args.persoon || null, limit: args.limiet || 12,
  });
  return clean(JSON.stringify({ ok: true, changed: false, verified: true, count: messages.length, messages }));
}
async function handleZetHerinnering(args, context) {
        const due = new Date(args.wanneer);
        if (!Number.isFinite(due.getTime())) return JSON.stringify({ ok: false, reason: 'ongeldig tijdstip' });
        const id = addReminder({
          due_ts: due.toISOString(),
          text: args.tekst,
          target: args.voor_wie || context.sender || null,
          recurrence: ['daily', 'weekly', 'monthly'].includes(args.herhaling) ? args.herhaling : null,
        });
        return JSON.stringify({
          ok: Number.isFinite(Number(id)),
          id: Number.isFinite(Number(id)) ? Number(id) : null,
          due: due.toISOString(),
          target: args.voor_wie || context.sender || null,
        });
}
async function handleToonHerinneringen(_args, context, mode) {
  return clean(JSON.stringify(/** @type {any[]} */ (openReminders()).filter((reminder) => (
          mode === 'autonomy'
            || !reminder.target
            || String(reminder.target).toLowerCase() === String(context.sender || '').toLowerCase()
        ))));
}
async function handleAnnuleerHerinnering(args) {
  const id = Number(args.id), existed = /** @type {any[]} */ (openReminders())
    .some((reminder) => Number(reminder.id) === id); if (existed) cancelReminder(id);
  return JSON.stringify({ ok: existed, id });
}
async function handleBekijkCamera(args, context, mode) {
  const provider = String(args.provider || '').trim().toLowerCase();
  const injected = context.robotDependencies || context.messageContext?.robotDependencies || {};
  if (provider === 'robot') {
    const frame = await (injected.captureRobotFrame || captureRobotFrame)();
    const vision = await (injected.describeImageBuffer || (await import('./vision.js')).describeImageBuffer)(
      frame.buffer, frame.contentType,
      'Dit is de actuele frontcamera van de robotstofzuiger. Beschrijf alleen wat direct zichtbaar is en wat onzeker blijft; geef geen routeadvies buiten het beeld.',
      { budgetClass: mode === 'autonomy' ? 'perception' : 'interactive', cacheKey: 'eva-robot-frontcamera' },
    );
    return JSON.stringify({
      ok: frame.ok !== false && vision.ok, verified: frame.verified !== false && vision.verified,
      changed: false, provider, target: 'frontcamera',
      frame: { path: path.relative(config.root, frame.path), bytes: frame.bytes },
      description: vision.description, message: vision.description,
    });
  }
  if (provider !== 'home_assistant') return JSON.stringify({ ok: false, verified: false, changed: false, reason: 'onbekende cameraprovider' });
  const look = injected.lookAndDescribe || (await import('./vision.js')).lookAndDescribe;
  return clean(JSON.stringify(await look(clean(args.target || '', 160),
    { budgetClass: mode === 'autonomy' ? 'perception' : 'interactive' })));
}
async function handleLeerGezichtVanFoto(args, context) {
        const { nameLastSeen } = await import('./vision.js');
        const photoRef = String(context.replyEvidence?.photoRef || '').trim();
        const identityClaim = context.faceIdentityClaim || null;
        if (!identityClaim) return clean(JSON.stringify({
          ok: false,
          verified: false,
          changed: false,
          reason: 'de actuele foto en menselijke naamcorrectie zijn niet exact aan elkaar gebonden',
        }));
        const result = await nameLastSeen(args.naam, {
          ref: photoRef,
          identityClaim,
        });
        return clean(JSON.stringify(result));
}
const voiceAnnouncementsInFlight = new Map();
function reusedVoiceAnnouncement(result, plan) {
  if (result?.accepted !== true) return result;
  return {
    ...result,
    changed: false,
    duplicate_prevented: true,
    receipt_reused: true,
    message: `Dezelfde omroep voor ${plan.target.alias || plan.target.entityId} was in deze beurt al door Home Assistant geaccepteerd en is niet herhaald.`,
  };
}
async function announceOncePerTurn({ turnId, text, requestedTarget, announce }) {
  const plan = voiceAssistSatelliteAnnouncementPlan(text, requestedTarget);
  if (!plan.ok) return { ...plan, verified: false, changed: false };
  const execute = () => announce(plan.data.message, plan.target.entityId);
  const normalizedTurnId = String(turnId || '').trim();
  if (!normalizedTurnId) return execute();
  const signature = `${normalizedTurnId}\n${plan.target.entityId}\n${normalize(plan.data.message)}`;
  const executionKey = `voice-announcement:${createHash('sha256').update(signature).digest('hex')}`;
  const inFlight = voiceAnnouncementsInFlight.get(executionKey);
  if (inFlight) return reusedVoiceAnnouncement(await inFlight, plan);
  const claim = claimTurnToolExecution({
    executionKey,
    turnId: normalizedTurnId,
    tool: 'spreek_in_huis',
    args: { target: plan.target.entityId, text: normalize(plan.data.message) },
  });
  if (!claim.claimed) {
    if (claim.row?.status === 'done' && claim.row.output) {
      try {
        return reusedVoiceAnnouncement(JSON.parse(claim.row.output), plan);
      } catch {
        // Een beschadigd receipt is evenmin toestemming om opnieuw hoorbaar te handelen.
      }
    }
    return {
      ok: false,
      verified: false,
      changed: false,
      uncertain: true,
      effect_uncertain: true,
      retryable: false,
      needs_followup: false,
      target: plan.target.entityId,
      reason: 'Een eerdere poging in deze beurt kan al hoorbaar zijn gestart. Ik herhaal die niet zonder een nieuwe opdracht.',
    };
  }
  const operation = Promise.resolve().then(async () => {
    const result = await execute();
    completeTurnToolExecution(executionKey, JSON.stringify(result));
    return result;
  });
  voiceAnnouncementsInFlight.set(executionKey, operation);
  try {
    return await operation;
  } finally {
    if (voiceAnnouncementsInFlight.get(executionKey) === operation) {
      voiceAnnouncementsInFlight.delete(executionKey);
    }
  }
}
async function handleSpreekInHuis(args, context, mode) {
  if (mode === 'autonomy' && !config.autonomyFull) {
          return JSON.stringify({ ok: false, reason: 'autonoom spreken in huis vereist de volledige-autonomiestand' });
        }
        return clean(JSON.stringify(await announceOncePerTurn({
          turnId: context.turnId,
          text: args.tekst,
          requestedTarget: args.doel || null,
          announce: (message, target) => announceOnVoiceAssistSatellite(message, target, {
            callService: (...serviceArgs) => {
              if (typeof context.messageContext?.isCurrent === 'function'
                && !context.messageContext.isCurrent()) {
                throw new Error('voicebeurt is ingehaald');
              }
              return callService(...serviceArgs);
            },
          }),
        })));
}
function autonomousMessagePreflight(args, context, episode) {
        if (episode?.origin_kind === 'user' && episode.delivery_policy !== 'required') {
          return JSON.stringify({
            ok: false,
            needs_followup: false,
            reason: 'de oorspronkelijke monitoringopdracht sluit externe updates uit',
          });
        }
        const urgent = /\b(?:brand|rook|koolmonoxide|inbraak|lekkage|nood|alarm|direct gevaar)\b/i
          .test(String(args.tekst || ''));
        if (isNight(new Date(), config.sleepStart, config.wakeTime) && !urgent) {
          return JSON.stringify({
            ok: false,
            needs_followup: true,
            waiting_for: 'quiet_hours',
            resume_after: nextQuietHoursEnd(),
            reason: 'dit is niet urgent genoeg om de nachtrust voor te onderbreken; werk stil door en bewaar het tot overdag',
          });
        }
        if (autonomousMessageIsInternalProgress(args.tekst)) {
          return JSON.stringify({
            ok: false,
            reason: 'interne test-, bestands- of uitvoeringsdetails blijven intern; meld alleen het concrete gevolg voor het gezin',
          });
        }
        const claimsBlocker = /\b(?:kan\s+ik\s+(?:nu\s+)?niet|niet\s+bereiken|onbereikbaar|niet\s+beschikbaar|blokkade|tegenhoudt|ontbreekt\s+nog|geen\s+toegang)\b/i
          .test(String(args.tekst || ''));
        if (claimsBlocker && !failureSupportsMessage(args.tekst, context.priorToolResults || [])) {
          return JSON.stringify({
            ok: false,
            reason: 'een zelfstandig blokkerbericht vereist een actuele mislukte handeling of bewezen menselijke afhankelijkheid; onderzoek of probeer eerst verder',
          });
        }
        const externalTechnicalBlocker = claimsBlocker
          && /\b(?:api|endpoint|sdk|website|site|online|provider|dienst|account|inbox|e-?mail|integratie|koppeling|plugin|add-?on|software|documentatie|token|sleutel|auth)\b/i.test(String(args.tekst || ''));
        const researchedBlocker = (context.priorToolResults || []).some((result) => (
          result?.name === 'zoek_internet' && result?.ok === true && result?.verified === true
          && /\b(?:offici[eë]le?|documentatie|docs?|api\s*reference|handleiding|developer)\b/i.test(String(result?.args?.vraag || ''))
          && evidenceMatches(args.tekst, result)
        ));
        if (externalTechnicalBlocker && !researchedBlocker) {
          return JSON.stringify({
            ok: false,
            reason: 'onderzoek eerst zelf de actuele officiële documentatie en vergelijk die met je eigen koppeling; één technische fout is nog geen gezinsbericht',
          });
        }
        const grounding = [...(context.priorToolResults || [])].reverse().find((result) => (
          result?.ok === true
          && result?.verified === true
          && toolHasTag(result?.name, 'grounding')
          && evidenceMatches(args.tekst, result)
        ));
        if (!grounding && !config.autonomyFull) {
          return JSON.stringify({
            ok: false,
            reason: 'een zelfstandig bericht vereist eerst inhoudelijk passend, teruggelezen of onderzocht bewijs',
          });
        }
        if (grounding?.name === 'zoek_internet' && emptyAutonomousWebFinding(args.tekst)) {
          const openWork = /** @type {any[]} */ (listWorkItems({
            statuses: ['open', 'blocked'], limit: 40,
          }));
          const tiedToOpenWork = openWork.some((item) => evidenceMatches(
            `${item.title} ${item.goal} ${item.next_step || ''}`,
            grounding,
          ));
          const tiedToSignal = Array.isArray(context.messageContext?.pendingSignals)
            && context.messageContext.pendingSignals.length > 0;
          if (!tiedToOpenWork && !tiedToSignal) {
            return JSON.stringify({
              ok: false,
              reason: 'een algemene internetzoektocht zonder concrete vondst verandert geen bekende gezinsbeslissing en wordt daarom niet verstuurd',
            });
          }
        }
        return null;
}
function autonomousDeliveryTarget(args, episode) {
        const origin = episode?.delivery_policy === 'required'
          ? episodeDeliveryOrigin(episode.id, 'telegram')
          : null;
        const directChatId = String(origin?.origin_conversation_id || '').match(/^telegram:(.+)$/)?.[1] || null;
        const requestedRecipient = clean(args.aan || '', 160).trim();
        const pinnedRecipient = clean(
          origin?.requester || episode?.requester || episode?.delivery_target || '',
          160,
        ).trim() || null;
        if (directChatId && requestedRecipient) {
          const acceptedRecipients = [
            pinnedRecipient,
            origin?.requester,
            episode?.requester,
            episode?.delivery_target,
            directChatId,
            origin?.origin_conversation_id,
          ].map(normalize).filter(Boolean);
          if (!acceptedRecipients.includes(normalize(requestedRecipient))) {
            return {
              rejection: JSON.stringify({
                ok: false,
                needs_followup: false,
                reason: 'de opgegeven ontvanger strijdt met de oorspronkelijke Telegramverzoeker',
              }),
            };
          }
        }
        const transportBinding = directChatId
          ? requiredTelegramOriginBinding(
              origin.origin_conversation_id,
              origin.origin_message_id,
            )
          : {};
        return {
          directChatId,
          origin,
          recipient: directChatId ? pinnedRecipient : requestedRecipient || pinnedRecipient,
          ...transportBinding,
        };
}
function recordAutonomousDelivery(
  sent, args, episode, delivery, transportDedupeKey, monitoringBinding = null,
) {
        if (sent?.ok !== true || sent?.accepted !== true) return null;
        const deliveredText = canonicalTelegramText(args.tekst);
        const chats = new Set((sent.sent || []).map((item) => String(item.chatId || '')).filter(Boolean));
        if (delivery.directChatId
          && (chats.size !== 1 || !chats.has(String(delivery.directChatId)))) {
          return JSON.stringify({
            ...sent,
            ok: false,
            needs_followup: true,
            reason: 'het transport bevestigde de oorspronkelijke Telegramchat niet; er is geen afleveringbewijs vastgelegd',
          });
        }
        for (const chatId of chats) {
          addMessage('telegram', 'Eva', 'assistant', deliveredText, `telegram:${chatId}`);
        }
        logTimeline('chat_out', `Eva: ${deliveredText}`, {
          sender: 'Eva',
          autonomous: true,
          familyVisible: true,
          deliveredChatIds: [...chats],
        });
        if (episode?.delivery_policy === 'required') {
          recordEpisodeDelivery(episode.id, {
            deliveredChannel: 'telegram',
            deliveryTarget: delivery.recipient,
            originConversationId: delivery.origin?.origin_conversation_id || null,
            originMessageId: delivery.origin?.origin_message_id || null,
            deliveredConversationId: chats.size === 1 ? `telegram:${[...chats][0]}` : null,
            resultText: deliveredText,
            proofSummary: episode.proof_summary,
            idempotencyKey: transportDedupeKey,
            transport: {
              accepted: true,
              transport: 'telegram',
              kind: 'text',
              fanoutOperationKey: sent.fanoutOperationKey,
              sent: sent.sent || [],
            },
            ...(monitoringBinding ? { monitoringBinding } : {}),
          });
        }
        return null;
}

function autonomousMessageRepetition(text) {
  const key = normalize(text).slice(0, 180);
  const recentTimeline = getTimeline({ limit: 500 });
  const repeated = recentTimeline.some((row) => (
    row.kind === 'chat_out'
    && row.meta?.autonomous === true
    && normalize(row.content).includes(key)
    && Date.now() - Date.parse(row.ts) < 24 * 3600_000
  ));
  const repeatedTopic = recentTimeline.some((row) => (
    row.kind === 'chat_out'
    && row.meta?.autonomous === true
    && Date.now() - Date.parse(row.ts) < AUTONOMY_TOPIC_QUIET_MS
    && autonomousMessagesOverlap(text, row.content)
    && !autonomousMessageHasNewResult(text, row.content)
  ));
  return { key, blocked: repeated || repeatedTopic };
}

function pinnedRequiredUserPendingDelivery(args, context, episode, preparedKey) {
  const workId = Number(context.activeWorkId);
  if (!preparedKey
    || episode?.origin_kind !== 'user'
    || episode.delivery_policy !== 'required'
    || episode.state !== 'delivery_pending'
    || !Number.isSafeInteger(workId) || workId < 1
    || Number(episode.work_item_id) !== workId
    || !preparedKey.startsWith(`episode:${episode.id}:`)) return false;
  try {
    const work = /** @type {any} */ (db.prepare(
      `SELECT id,status,state_json FROM main.eva_work_items WHERE id=?`,
    ).get(workId));
    if (!work || Number(work.id) !== workId
      || !['open', 'blocked'].includes(String(work.status || ''))) return false;
    const state = JSON.parse(String(work.state_json || 'null'));
    const pending = state?.pending_delivery;
    if (!pending || typeof pending !== 'object' || Array.isArray(pending)) return false;
    const pendingKey = String(pending.dedupeKey || '').trim();
    const pendingText = canonicalTelegramText(pending.text);
    return pendingKey.length > 0 && pendingKey.length <= 240
      && pendingKey === preparedKey
      && pendingText.length > 0
      && pendingText === canonicalTelegramText(args.tekst);
  } catch {
    return false;
  }
}

function preparedInterpretationReplyBoundary(args, context, episode) {
  const replyGate = interpretationReplyDeliveryGate(episode?.id, {
    kind: context.preparedDeliveryKind,
    dedupeKey: context.preparedDeliveryDedupeKey,
    text: args.tekst,
    provenanceSha256: context.preparedDeliveryProvenanceSha256,
  });
  const reconcileSuperseded = context.preparedDeliveryReconcileSuperseded === true
    && replyGate.required && replyGate.bound === true && replyGate.current === false
    && typeof context.preparedDeliveryIsCurrent === 'function'
    && !context.preparedDeliveryIsCurrent();
  let rejection = null;
  if (replyGate.required && (!replyGate.valid
    || typeof context.preparedDeliveryIsCurrent !== 'function'
    || !context.preparedDeliveryIsCurrent()) && !reconcileSuperseded) {
    rejection = JSON.stringify({
      contract: 'ActionResult/v1',
      tool: 'stuur_bericht',
      ok: false,
      changed: false,
      verified: false,
      needs_followup: false,
      failure_class: replyGate.current ? 'integrity' : 'superseded',
      retryable: false,
      effect_uncertain: false,
      superseded: !replyGate.current,
      error: replyGate.current
        ? 'de verzegelde antwoordbinding is niet intact'
        : 'het voorbereide antwoord is door een nieuwer bericht ingehaald',
      data: { superseded: !replyGate.current },
    });
  }
  return {
    replyGate,
    reconcileSuperseded,
    preparedInterpretationReply: replyGate.required
      && (replyGate.valid || reconcileSuperseded),
    rejection,
  };
}

function monitoringTransportAuthority(args, context, episode) {
  const authority = /** @type {any} */ (verifyMonitoringLifecycleAuthority({
    workId: Number(context.activeWorkId), episodeId: Number(episode?.id),
    expectedPendingSha256: String(context.preparedDeliveryMonitoringPendingSha256 || ''),
  }));
  const limits = authority ? monitoringDeliveryTransportLimits(
    authority.pending, authority.monitoring,
  ) : null;
  const now = new Date().toISOString();
  if (!authority || episode?.state !== 'delivery_pending'
    || typeof context.preparedDeliveryIsCurrent !== 'function'
    || !context.preparedDeliveryIsCurrent() || !limits
    || !monitoringDeliveryWireCurrent(
      authority.pending, authority.monitoring, { now, boundary: 'start' },
    )
    || Object.hasOwn(args, 'aan') || canonicalTelegramText(args.tekst) !== args.tekst
    || authority.pending?.text !== args.tekst) return null;
  return { authority, limits };
}

function monitoringDeliveryReceiptBinding(authority) {
  return {
    contract: 'MonitoringDeliveryReceiptBinding/v1',
    monitoringContractSha256: authority.monitoring.contractSha256,
    pendingSha256: authority.pending.pendingSha256,
    lifecycleSha256: authority.lifecycle.lifecycleSha256,
    lifecycleGeneration: authority.lifecycle.generation,
    originBindingSha256: authority.lifecycle.originBindingSha256,
    outcomeReceiptSha256: authority.pending.outcomeRef.outcomeReceiptSha256,
    outcomeSha256: authority.pending.outcomeRef.outcomeSha256,
    factSetSha256: authority.pending.factSetSha256,
    textSha256: authority.pending.textSha256,
    rendererVersion: authority.pending.rendererVersion,
    dedupeKey: authority.pending.dedupeKey,
  };
}

// De prepared-reply lane valideert gate, supersession en transportattest aan
// dezelfde laatste effectgrens; dit mag niet over een clonebare tussenlaag.
async function handleStuurBericht(args, context, mode) {
        if (mode !== 'autonomy') {
          return clean(JSON.stringify(await sendRegisteredMessage(args.tekst, args.aan || null, {
            dedupeKey: context.turnId ? `tool-message:${context.turnId}` : null,
            isCurrent: typeof context.messageContext?.isCurrent === 'function'
              ? context.messageContext.isCurrent
              : undefined,
          })));
        }
        if (/^\s*eva\s*$/i.test(String(args.aan || ''))) {
          return JSON.stringify({
            ok: false,
            needs_followup: false,
            reason: 'een bericht aan Eva zelf is geen externe aflevering en is daarom niet toegestaan',
          });
        }
        const episode = goalEpisodeById(context.activeGoalEpisodeId);
        const boundary = preparedInterpretationReplyBoundary(args, context, episode);
        const { preparedInterpretationReply, reconcileSuperseded } = boundary;
        if (boundary.rejection) return boundary.rejection;
        const monitoringV2 = context.preparedDeliveryKind === 'monitoring_v2';
        const monitoringTransport = monitoringV2
          ? monitoringTransportAuthority(args, context, episode) : null;
        const monitoringAuthority = monitoringTransport?.authority || null;
        const monitoringTransportLimits = monitoringTransport?.limits || null;
        if (monitoringV2 && !monitoringTransport) {
          return JSON.stringify({
            contract: 'ActionResult/v1', tool: 'stuur_bericht', ok: false,
            changed: false, verified: false, needs_followup: false,
            failure_class: 'integrity', retryable: false, effect_uncertain: false,
            error: 'de verzegelde monitoringaflevering is niet meer exact actueel',
          });
        }
        const rejection = preparedInterpretationReply || monitoringV2
          ? null : autonomousMessagePreflight(args, context, episode);
        if (rejection) return rejection;
        const repetition = autonomousMessageRepetition(args.tekst);
        const preparedKey = clean(context.preparedDeliveryDedupeKey || '', 240).trim();
        const transportDedupeKey = preparedKey
          ? `prepared:${preparedKey}`
          : `autonomy:${repetition.key}`;
        const pinnedRequiredUserDelivery = monitoringV2 || pinnedRequiredUserPendingDelivery(
          args, context, episode, preparedKey,
        );
        if (!preparedInterpretationReply && !pinnedRequiredUserDelivery && repetition.blocked) {
          return JSON.stringify({ ok: false, reason: 'dit onderwerp is de afgelopen zeven dagen al zelfstandig gemeld' });
        }
        const delivery = /** @type {any} */ (monitoringV2 ? {
          directChatId: monitoringAuthority.delivery.chatId,
          origin: {
            origin_conversation_id: monitoringAuthority.delivery.conversationId,
            origin_message_id: monitoringAuthority.delivery.messageId,
          },
          recipient: null,
          messageThreadId: monitoringAuthority.delivery.messageThreadId,
          originPrincipal: monitoringAuthority.delivery.principal,
        } : autonomousDeliveryTarget(args, episode));
        if (delivery.rejection) return delivery.rejection;
        const economicFamilyDelivery = context.economicFamilyDelivery || null;
        if (economicFamilyDelivery && !monitoringV2) {
          delivery.directChatId = null;
          delivery.recipient = economicFamilyDelivery.recipient;
        }
        // Laatste effectgrens voor een na crash gepind antwoord. Een nieuw
        // inboundbericht kan na selectie maar vóór transport arriveren; dan
        // mag de oude reply niet alsnog worden verstuurd.
        if (['interpretation_reply', 'monitoring_v2'].includes(context.preparedDeliveryKind)
          && typeof context.preparedDeliveryIsCurrent === 'function'
          && !context.preparedDeliveryIsCurrent() && !reconcileSuperseded) {
          return JSON.stringify({
            ok: false,
            changed: false,
            verified: false,
            needs_followup: false,
            failure_class: 'superseded',
            retryable: false,
            effect_uncertain: false,
            error: 'het voorbereide antwoord is door een nieuwer bericht ingehaald',
            data: { superseded: true },
          });
        }
        const transportOptions = /** @type {any} */ ({
          dedupeKey: transportDedupeKey,
          isCurrent: ['interpretation_reply', 'monitoring_v2']
            .includes(context.preparedDeliveryKind)
            && typeof context.preparedDeliveryIsCurrent === 'function'
            ? context.preparedDeliveryIsCurrent : undefined,
          ...(monitoringV2 ? { checkCurrentBeforeEachTransport: true } : {}),
          ...(monitoringV2 ? { monitoringTransportLimits } : {}),
          broadcast: monitoringV2 ? false : !delivery.recipient && !delivery.directChatId,
          ...(economicFamilyDelivery ? {
            broadcast: economicFamilyDelivery.broadcast,
            householdOnly: true,
            expectedTargetIds: economicFamilyDelivery.chatIds,
          } : {}),
          directReply: Boolean(delivery.directChatId),
          directReplyChatId: delivery.directChatId,
          replyToMessageId: delivery.directChatId
            ? delivery.origin?.origin_message_id || undefined : undefined,
          messageThreadId: delivery.messageThreadId || undefined,
          directTurnSignalRef: delivery.directChatId && delivery.origin?.origin_message_id
            ? `turn:telegram:${delivery.directChatId}:${delivery.origin.origin_message_id}` : undefined,
          originPrincipal: delivery.originPrincipal || undefined,
        });
        if (reconcileSuperseded) {
          transportOptions.reconcileSuperseded = true;
          TELEGRAM_DELIVERY.issueReconciliationOptions(transportOptions);
        }
        const sent = await sendRegisteredMessage(args.tekst, delivery.recipient, transportOptions);
        if (context.preparedDeliveryKind === 'interpretation_reply'
          && sent?.stale === true) {
          return JSON.stringify({
            contract: 'ActionResult/v1',
            tool: 'stuur_bericht',
            ok: false,
            changed: false,
            verified: false,
            needs_followup: false,
            failure_class: 'superseded',
            retryable: false,
            effect_uncertain: sent?.visible === true,
            superseded: true,
            error: 'het voorbereide antwoord is tijdens transport ingehaald en ingetrokken',
            data: { superseded: true, retraction: sent.retraction || null },
          });
        }
        if (monitoringV2 && (!context.preparedDeliveryIsCurrent()
          || sent?.stale === true)) {
          return JSON.stringify({
            contract: 'ActionResult/v1', tool: 'stuur_bericht', ok: false,
            changed: false, verified: false, needs_followup: false,
            failure_class: 'integrity', retryable: false,
            effect_uncertain: sent?.accepted === true,
            error: 'monitoringauthority wijzigde tijdens de directe aflevering',
          });
        }
        const monitoringReceiptBinding = monitoringV2
          ? monitoringDeliveryReceiptBinding(monitoringAuthority) : null;
        const deliveryError = recordAutonomousDelivery(
          sent, args, episode, delivery, transportDedupeKey, monitoringReceiptBinding,
        );
        if (deliveryError) return deliveryError;
        return clean(JSON.stringify({
          ...sent,
          message: sent?.ok ? clean(canonicalTelegramText(args.tekst), 1_200) : null,
        }));
}
const HOST_WORK_STATE_FIELDS = [
  'monitoring', 'monitoring_start_authority', 'monitoring_start_delivery_origin',
  'monitoring_lifecycle',
  'monitoring_runtime', 'pending_delivery',
  'interpretation_reply_binding', 'final_delivery_binding', 'artifact_receipts',
  'exploration_economic_handoff',
];
const WORK_STATE_JSON_LIMIT = 12_000;
const FINAL_DELIVERY_SHA256_RESERVATION = '0'.repeat(64);

function finalDeliveryAwaitingBinding({ episode, work, context, resultText, proof }) {
  const core = {
    contract: 'FinalDeliveryBinding/v1',
    stage: 'awaiting_reply',
    bindingId: randomUUID(),
    episodeId: Number(episode.id),
    workItemId: Number(work.id),
    turnId: String(context.turnId || ''),
    channel: String(context.channel || ''),
    conversationId: String(context.conversationId || ''),
    turnOriginMessageId: String(context.originMessageId || context.messageContext?.messageId || ''),
    originChannel: String(episode.origin_channel || ''),
    originConversationId: String(episode.origin_conversation_id || ''),
    originMessageId: String(episode.origin_message_id || ''),
    resultSha256: createHash('sha256').update(clean(resultText, 2_000)).digest('hex'),
    proofSha256: createHash('sha256').update(canonicalJson({
      tool: String(proof?.name || ''),
      evidence: resultEvidence(proof),
    })).digest('hex'),
  };
  return {
    ...core,
    bindingSha256: createHash('sha256').update(canonicalJson(core)).digest('hex'),
  };
}

function finalDeliveryStateCapacity(state) {
  const awaitingLength = JSON.stringify(state).length;
  const awaiting = state.final_delivery_binding;
  const { bindingSha256: armedSha256, ...armed } = awaiting;
  const sealedState = {
    ...state,
    final_delivery_binding: {
      ...armed,
      stage: 'sealed',
      armedSha256,
      replySha256: FINAL_DELIVERY_SHA256_RESERVATION,
      capabilitySha256: FINAL_DELIVERY_SHA256_RESERVATION,
      bindingSha256: FINAL_DELIVERY_SHA256_RESERVATION,
    },
  };
  return {
    awaitingLength,
    sealedLength: JSON.stringify(sealedState).length,
  };
}

function revokedFinalDeliveryBinding({ episode, work, previous, operation }) {
  const core = {
    contract: 'FinalDeliveryBinding/v1',
    stage: 'revoked',
    bindingId: randomUUID(),
    episodeId: Number(episode.id),
    workItemId: Number(work.id),
    priorBindingSha256: String(previous?.bindingSha256 || ''),
    revokedAt: new Date().toISOString(),
    reason: `work_mutated:${String(operation || 'update')}`,
  };
  return {
    ...core,
    bindingSha256: createHash('sha256').update(canonicalJson(core)).digest('hex'),
  };
}
function parseModelOwnedWorkState(value, stored = {}) {
  let parsed;
  try { parsed = typeof value === 'string' ? JSON.parse(value) : value; } catch {
    return { ok: false, reason: 'toestand_json is geen geldige JSON-toestand' };
  }
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)
    && ['done', 'cancelled', 'canceled', 'closed', 'completed', 'abandoned']
      .includes(normalize(parsed.status || ''))) {
    return {
      ok: false,
      reason: 'toestand_json mag een werkstatus niet afsluiten; gebruik afronden of annuleren als aparte bewerking',
    };
  }
  return { ok: true, state: modelOwnedWorkState(parsed, stored) };
}
function modelOwnedWorkState(value, stored = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const result = { ...value };
  for (const key of HOST_WORK_STATE_FIELDS) {
    delete result[key];
    if (Object.hasOwn(stored, key)) result[key] = stored[key];
  }
  return result;
}
function assertNoActiveEpisodeAtWorkStart(context) {
  const activeEpisodeId = Number(context.activeGoalEpisodeId);
  const activeEpisode = Number.isFinite(activeEpisodeId)
    ? goalEpisodeById(activeEpisodeId) : null;
  if (!activeEpisode) return;
  throw Object.assign(new Error(
    `deze beurt heeft al episode #${activeEpisode.id} voor werk #${activeEpisode.work_item_id}; start maakt geen tweede werkregel`,
  ), { failure_class: 'conflict', retryable: false, effect_uncertain: false });
}
async function handleWerkBijhouden(args, context, mode) {
        const operation = normalize(args.bewerking);
        if (!['start', 'update', 'blokkeer', 'afronden', 'annuleren'].includes(operation)) {
          return JSON.stringify({ ok: false, changed: false, reason: `onbekende werkbewerking: ${operation || '(leeg)'}` });
        }
        if (operation === 'start') {
          assertNoActiveEpisodeAtWorkStart(context);
          const initialStateInput = args.toestand_json == null
            ? null : parseModelOwnedWorkState(args.toestand_json, context.trustedWorkState);
          if (initialStateInput && !initialStateInput.ok) {
            return JSON.stringify({ ok: false, changed: false, reason: initialStateInput.reason });
          }
          const requestedDeliveryTarget = clean(args.afleveren_aan || '', 160).trim();
          const deliveryTarget = mode !== 'autonomy'
            && /^(?:requester|origin)$/iu.test(requestedDeliveryTarget)
            ? clean(context.sender || '', 160).trim() || null
            : requestedDeliveryTarget || null;
          const selfMonitoring = mode === 'autonomy' ? monitoringRequestSpec({
            text: [args.titel, args.doel, args.volgende_stap, args.definitie_klaar]
              .filter(Boolean).join(' '),
          }) : null;
          if (selfMonitoring?.requested && selfMonitoring.deliveryRequired && !deliveryTarget) {
            return JSON.stringify({
              ok: false,
              changed: false,
              reason: 'zelfgekozen monitoring met een menselijke terugmelding vereist afleveren_aan; zonder doel wordt geen afleverbelofte opgeslagen',
            });
          }
          if (mode === 'autonomy' && !config.autonomyFull) {
            const subject = `${args.titel || ''} ${args.doel || ''} ${args.volgende_stap || ''}`;
            const grounding = [...(context.priorToolResults || [])].reverse().find(
              (result) => evidenceMatches(subject, result),
            );
            if (!grounding) {
              return JSON.stringify({
                ok: false,
                reason: 'zelfstandig duurzaam werk vereist eerst inhoudelijk passend bron- of huisbewijs',
              });
            }
          }
          const initialState = initialStateInput?.state
            || modelOwnedWorkState({}, context.trustedWorkState);
          const monitoringStart = normalizeMonitoringContract(initialState.monitoring);
          let { workItem: item, episode } = createDurableWork({
            source: mode === 'autonomy' ? 'autonomy' : 'user',
            owner: mode === 'autonomy' ? null : context.sender || null,
            title: args.titel,
            goal: args.doel,
            nextStep: args.volgende_stap || '',
            definitionOfDone: args.definitie_klaar || args.doel,
            priority: args.prioriteit,
          }, {
            originKind: mode === 'autonomy' ? 'self' : 'user',
            originChannel: mode === 'autonomy' ? 'autonomy' : context.channel || 'chat',
            originConversationId: mode === 'autonomy' ? null : context.conversationId || null,
            originMessageId: mode === 'autonomy' ? null
              : context.originMessageId || context.messageContext?.messageId || null,
            requester: mode === 'autonomy' ? null : context.sender || null,
            deliveryPolicy: deliveryTarget ? 'required' : null,
            deliveryTarget,
          }, monitoringStart ? {
            monitoringStart: {
              monitoring: monitoringStart,
              state: initialState,
              startAuthority: initialState.monitoring_start_authority,
              deliveryOrigin: initialState.monitoring_start_delivery_origin,
              currentStep: args.fase,
              wakeTs: args.hervatten_na,
              nextAction: args.volgende_stap || '',
              sessionRef: context.turnId || null,
            },
          } : {});
          if (!monitoringStart
            && (args.fase != null || args.toestand_json != null
              || args.hervatten_na != null || args.doel_sleutel != null)) {
            const state = initialState;
            if (args.doel_sleutel) state.goal_key = String(args.doel_sleutel);
            const work = {
              currentStep: args.fase,
              state,
              nextWakeTs: args.hervatten_na,
              wait: args.hervatten_na ? { reason: 'scheduled_resume' } : null,
            };
            if (episode) {
              const transitioned = transitionGoalEpisode(episode.id, {
                state: args.hervatten_na ? 'waiting' : episode.state,
                wakeTs: args.hervatten_na,
                nextAction: args.volgende_stap || item.next_step || '',
                sessionRef: context.turnId || null,
                work,
              });
              item = transitioned.workItem;
              episode = transitioned.episode;
            } else item = updateDurableWork(item.id, work);
          }
          return JSON.stringify({
            ok: true,
            changed: true,
            proofAccepted: false,
            item,
          });
        }
        const id = Number(args.id);
        if (!Number.isFinite(id)) return JSON.stringify({ ok: false, reason: 'werk-id ontbreekt' });
        if (context.activeWorkId != null && Number.isFinite(Number(context.activeWorkId))
          && Number(context.activeWorkId) !== id) {
          return JSON.stringify({
            ok: false,
            changed: false,
            reason: `deze beurt is gebonden aan werk #${context.activeWorkId}, niet aan werk #${id}`,
          });
        }
        const current = /** @type {any[]} */ (listWorkItems({
          statuses: ['open', 'blocked', 'done'],
          limit: 200,
        })).find((item) => Number(item.id) === id);
        if (current?.status === 'done') {
          return JSON.stringify({
            ok: false,
            changed: false,
            reason: `werk #${id} is al terminaal afgerond en kan niet worden heropend`,
            item: current,
          });
        }
        const contextEpisode = goalEpisodeById(context.activeGoalEpisodeId);
        const boundEpisode = contextEpisode && Number(contextEpisode.work_item_id) === id
          ? contextEpisode
          : episodeForWork(id);
        let status = operation === 'afronden'
          ? 'done'
          : operation === 'blokkeer'
            ? 'blocked'
            : operation === 'annuleren'
              ? 'cancelled'
            : current?.status || 'open';
        const externalDependency = clean(args.afhankelijk_van || '', 600).trim();
        const selfReviewInstead = mode === 'autonomy' && operation === 'blokkeer'
          && !externalDependency;
        const scheduledWake = Number.isFinite(Date.parse(args.hervatten_na || ''))
          ? new Date(args.hervatten_na).toISOString()
          : null;
        const reviewWake = selfReviewInstead
          ? (scheduledWake
            ? scheduledWake
            : new Date(Date.now() + 6 * 60 * 60_000).toISOString())
          : null;
        if (selfReviewInstead) status = 'open';
        if (mode === 'autonomy' && status === 'done' && !boundEpisode) {
          const reported = (context.priorToolResults || []).some((result) => (
            result?.name === 'stuur_bericht' && result?.ok === true && result?.verified === true
          ));
          if (!reported) {
            return JSON.stringify({
              ok: false,
              changed: false,
              reason: 'zelfstandig werk wordt pas afgerond nadat het bewezen eindresultaat is bezorgd',
            });
          }
        }
        const workSubject = current
          ? `${current.title} ${current.goal} ${current.next_step || ''}`
          : '';
        const proof = (context.priorToolResults || [])
          .filter((result) => !['werk_bijhouden', 'toon_werk', 'stuur_bericht', 'spreek_in_huis']
            .includes(result?.name)
            && evidenceMatches(workSubject, result))
          .sort((left, right) => (
            toolProofRank(right.name) - toolProofRank(left.name)
          ))[0];
        const priorToolResults = context.priorToolResults || [];
        const exactWorkspaceProof = workspaceMutationTargetsProven(current, priorToolResults);
        const changedEffectProof = exactWorkspaceProof === null
          ? priorToolResults.find((result) => (
            mutationProofMatchesWork(current, workSubject, result)
          ))
          : exactWorkspaceProof;
        if (operation === 'afronden' && workRequiresMaterialMutation(current)
          && !changedEffectProof) {
          return JSON.stringify({
            ok: false,
            changed: false,
            proofAccepted: false,
            reason: 'afronden van muterend werk vereist een geverifieerde changed=true effectreceipt uit dezelfde beurt; alleen lezen of zoeken bewijst de wijziging niet',
          });
        }
        if (operation === 'afronden' && !proof) {
          return JSON.stringify({
            ok: false,
            changed: false,
            proofAccepted: false,
            reason: 'afronden vereist geverifieerd bewijs van een andere passende tool in dezelfde beurt',
          });
        }
        if (operation !== 'afronden' && clean(args.resultaat || '', 2_000)) {
          return JSON.stringify({
            ok: false,
            changed: false,
            reason: 'resultaat hoort alleen bij de afzonderlijke bewerking afronden',
          });
        }
        const waitsForRequiredDelivery = status === 'done'
          && boundEpisode?.delivery_policy === 'required'
          && !episodeHasFinalDelivery(boundEpisode.id);
        if (waitsForRequiredDelivery) status = 'open';
        let storedState = null;
        try { storedState = JSON.parse(String(current?.state_json || '{}')); } catch { storedState = {}; }
        const requestedStateInput = args.toestand_json
          ? parseModelOwnedWorkState(args.toestand_json, storedState || {}) : null;
        if (requestedStateInput && !requestedStateInput.ok) {
          return JSON.stringify({ ok: false, changed: false, reason: requestedStateInput.reason });
        }
        const requestedState = requestedStateInput?.state || null;
        const nextStoredState = { ...(requestedState || storedState || {}) };
        if (operation === 'annuleren') delete nextStoredState.final_delivery_binding;
        else if (operation !== 'afronden' && storedState?.final_delivery_binding) {
          nextStoredState.final_delivery_binding = revokedFinalDeliveryBinding({
            episode: boundEpisode, work: current,
            previous: storedState.final_delivery_binding, operation,
          });
        }
        if (waitsForRequiredDelivery && mode === 'chat') {
          if (!String(context.turnId || '') || !String(context.channel || '')
            || !String(context.conversationId || '')) {
            return JSON.stringify({
              ok: false, changed: false, proofAccepted: false,
              reason: 'afronden met verplichte aflevering mist de actuele beurt- en routebinding',
            });
          }
          nextStoredState.final_delivery_binding = finalDeliveryAwaitingBinding({
            episode: boundEpisode, work: current, context,
            resultText: args.resultaat, proof,
          });
          const stateCapacity = finalDeliveryStateCapacity(nextStoredState);
          if (stateCapacity.sealedLength > WORK_STATE_JSON_LIMIT) {
            const capacityReason = stateCapacity.awaitingLength <= WORK_STATE_JSON_LIMIT
              ? 'de latere verzegeling past niet veilig in de duurzame hosttoestand'
              : 'afronden met verplichte aflevering past niet veilig in de duurzame hosttoestand';
            return JSON.stringify({
              ok: false, changed: false, proofAccepted: false,
              reason: capacityReason,
            });
          }
        }
        const stateChangedByHost = operation !== 'afronden'
          && Boolean(storedState?.final_delivery_binding);
        const workChange = {
            nextStep: waitsForRequiredDelivery
              ? 'Bezorg het gecontroleerde resultaat aan de oorspronkelijke verzoeker.'
              : args.volgende_stap,
            currentStep: selfReviewInstead
              ? 'Wacht op het geplande herbeoordelingsmoment en probeer dan zelf een andere bewijsroute.'
              : args.fase,
            state: waitsForRequiredDelivery && mode === 'chat'
              ? nextStoredState
              : requestedState
                ? nextStoredState
                : externalDependency
                  ? { ...nextStoredState, blocked_dependency: externalDependency }
                  : stateChangedByHost ? nextStoredState : null,
            nextWakeTs: waitsForRequiredDelivery ? '' : reviewWake || args.hervatten_na,
            wait: waitsForRequiredDelivery
              ? {}
              : selfReviewInstead
                ? { reason: 'self_review', resumeAfter: reviewWake }
                : scheduledWake
                  ? { reason: 'scheduled_resume', resumeAfter: scheduledWake }
                  : null,
            clearWait: args.hervatten_na === '',
            definitionOfDone: args.definitie_klaar,
            priority: args.prioriteit,
            expectedRevision: args.verwachte_revisie,
            evidence: (proof
              ? resultEvidence(proof)
              : mode === 'autonomy' ? '' : clean(args.bewijs || '', 2_000)) || null,
            lastAction: proof?.name || current?.last_action || 'werk_bijhouden',
            result: args.resultaat,
            proofAccepted: !!proof,
          };
        let item;
        if (boundEpisode) {
          const delivered = episodeHasFinalDelivery(boundEpisode.id);
          const needsDelivery = waitsForRequiredDelivery || episodeNeedsDelivery(id);
          const nextEpisodeState = operation === 'annuleren'
            ? 'abandoned'
            : selfReviewInstead
              ? 'waiting'
              : scheduledWake
                ? 'waiting'
              : operation === 'blokkeer'
                ? 'blocked'
                : status === 'done' || waitsForRequiredDelivery
                  ? delivered || boundEpisode.delivery_policy !== 'required'
                    ? 'closed'
                    : needsDelivery ? 'delivery_pending' : 'verifying'
                  : ['committed', 'waiting', 'blocked', 'recovering'].includes(boundEpisode.state)
                    ? 'executing'
                    : boundEpisode.state;
          const deliveryAction = 'Bezorg het gecontroleerde resultaat aan de oorspronkelijke verzoeker.';
          const transitioned = transitionGoalEpisode(boundEpisode.id, {
            state: nextEpisodeState,
            deliveryPolicy: operation === 'annuleren' ? 'none' : null,
            deliveryTarget: operation === 'annuleren' ? '' : null,
            checkpoint: proof ? resultEvidence(proof) : current?.evidence || '',
            proofSummary: proof ? resultEvidence(proof) : current?.evidence || '',
            nextAction: needsDelivery
              ? deliveryAction
              : selfReviewInstead
                ? current?.next_step || 'Herbeoordeel met een andere bewijsroute.'
                : args.volgende_stap,
            wakeTs: selfReviewInstead ? reviewWake : scheduledWake || args.hervatten_na,
            sessionRef: context.turnId || null,
            eventKind: waitsForRequiredDelivery && mode === 'chat'
              ? 'final_delivery_armed'
              : stateChangedByHost
                ? 'final_delivery_revoked'
                : null,
            work: workChange,
          });
          item = transitioned?.workItem;
        } else {
          item = updateDurableWork(id, { ...workChange, status });
        }
        const changed = ['status', 'next_step', 'evidence', 'last_action', 'result',
          'current_step', 'state_json', 'next_wake_ts', 'definition_of_done', 'priority', 'revision']
          .some((field) => String(current?.[field] ?? '') !== String(item?.[field] ?? ''));
        return JSON.stringify({
          ok: true,
          changed,
          proofAccepted: !!proof,
          selfReviewInstead,
          item,
        });
}
function consumeResolvedSelfAttentionGoalArgs(args) {
  const binding = args && typeof args === 'object'
    ? RESOLVED_SELF_ATTENTION_GOAL_ARGS.get(args) : null;
  const internalShape = Object.hasOwn(args || {}, 'doel_ref')
    || Object.hasOwn(args || {}, 'verwacht_doel_id');
  if (!binding) {
    if (internalShape) throw Object.assign(new Error('ongeldige interne doelbinding'), {
      code: 'EVA_SELF_ATTENTION_GOAL_BOUNDARY',
    });
    return null;
  }
  const metadata = ISSUED_PLAN_METADATA.get(binding.plan);
  if (CONSUMED_SELF_ATTENTION_GOAL_ARGS.has(args)
    || binding.args !== args || metadata?.selfAttentionGoal?.opaqueArgs !== binding.plan.args
    || args.doel_sleutel !== binding.goalKey
    || args.verwacht_doel_id !== binding.goalId
    || args.verwachte_revisie !== binding.goalRevision
    || args.bewerking !== binding.operationId
    || Date.parse(binding.claimedUntil) <= Date.now()) {
    throw Object.assign(new Error('ongeldige of verlopen interne doelbinding'), {
      code: 'EVA_SELF_ATTENTION_GOAL_BOUNDARY',
    });
  }
  CONSUMED_SELF_ATTENTION_GOAL_ARGS.add(args);
  return binding;
}
async function handleDoelBijhouden(args) {
  const resolved = consumeResolvedSelfAttentionGoalArgs(args);
  return JSON.stringify(manageGoal({
          operation: args.bewerking,
          goalKey: args.doel_sleutel,
          title: args.titel,
          intent: args.intentie,
          why: args.waarom,
          beneficiary: args.begunstigde,
          horizon: args.horizon,
          successMetrics: args.succesmaten,
          priority: args.prioriteit,
          nextReviewTs: args.volgende_review,
          expectedRevision: args.verwachte_revisie,
          expectedGoalId: resolved?.goalId ?? null,
          source: 'self',
        }));
}
async function handleVerkenningBijhouden(args, context, mode) {
        const operation = normalize(args.bewerking);
        if (operation === 'lijst') return JSON.stringify({ ok: true, changed: false, explorations: listExplorations({ limit: 30 }) });
        if (operation === 'start'
          && Object.hasOwn(context, 'freeAutonomyExplorationAtomicStart')) {
          return JSON.stringify(startIssuedFreeAutonomyExplorationAtomic(
            context.freeAutonomyExplorationAtomicStart, args,
          ));
        }
        if (operation === 'start') return JSON.stringify(startExploration(/** @type {any} */ ({
          question: args.vraag,
          whyNow: args.aanleiding,
          hypothesis: args.hypothese,
          expectedObservation: args.verwachte_waarneming,
          stopRule: args.stopregel,
          maxActions: args.max_acties,
          sourceEpisodeId: context.cognitiveEpisodeId,
          aspirationId: args.aspiratie_id,
          source: mode === 'autonomy' ? 'self' : 'conversation',
          supersedesExplorationId: args.eerder_besluit_id,
          newEvidence: args.nieuw_bewijs,
          newEvidenceVerified: (context.priorToolResults || []).some((result) => (
            verifiedCompleteReceipt(result)
            && evidenceMatches(args.nieuw_bewijs || '', result)
          )),
          topicKey: args.onderwerp_sleutel,
          topicAnchors: args.onderwerp_ankers,
        })));
        const id = Number(args.id);
        if (!Number.isFinite(id)) return JSON.stringify({ ok: false, changed: false, reason: 'verkenning-id ontbreekt' });
        const statuses = {
          update: 'running', wachten: 'waiting', afronden: 'completed',
          stoppen: 'abandoned', stop: 'abandoned',
        };
        const status = statuses[operation];
        if (!status) return JSON.stringify({ ok: false, changed: false, reason: `onbekende verkenningsbewerking: ${operation}` });
        if (operation === 'afronden'
          && Object.hasOwn(context, 'explorationTerminalContribution')) {
          const issuedExecution = context.explorationTerminalContribution;
          const execution = issuedExecution && typeof issuedExecution === 'object'
            ? ISSUED_EXPLORATION_TERMINAL_EXECUTIONS.get(issuedExecution) : null;
          if (!execution || args !== execution.expectedArgs
            || CONSUMED_EXPLORATION_TERMINAL_EXECUTIONS.has(issuedExecution)) {
            throw Object.assign(new Error(
              'de exploration-afronding mist haar exact uitgegeven execution-binding',
            ), { code: 'EVA_SELF_ATTENTION_EXPLORATION_BOUNDARY' });
          }
          CONSUMED_EXPLORATION_TERMINAL_EXECUTIONS.add(issuedExecution);
          const precommit = consumeIssuedExplorationTerminalContribution(
            execution.claim, execution.expectedArgs,
          );
          if (!precommit || execution.isCurrent() !== true) throw Object.assign(new Error(
            'de exploration-afronding mist haar exact uitgegeven contribution-binding',
          ), { code: 'EVA_SELF_ATTENTION_EXPLORATION_BOUNDARY' });
          return JSON.stringify(completeIssuedExplorationContribution(precommit, args));
        }
        return JSON.stringify(updateExploration(id, {
          status,
          evidence: args.bewijs,
          result: args.resultaat,
          decision: args.besluit,
          nextWakeTs: args.hervatten_na,
          expectedRevision: args.verwachte_revisie,
        }));
}
const WORKBOOK_PAGE_BYTES = 7_000;
function workbookItem(item) {
  const projected = {
    id: Number(item.id), status: item.status,
    title: clean(item.title, 240), goal: clean(item.goal, 1_200),
    definition_of_done: clean(item.definition_of_done, 1_200),
    next_step: clean(item.next_step, 600), current_step: clean(item.current_step, 600),
    evidence_summary: clean(item.evidence, 500) || null,
    result_summary: clean(item.result, 500) || null,
    updated_ts: item.updated_ts, priority: Number(item.priority), revision: Number(item.revision),
  };
  const truncatedFields = [];
  const textFields = [
    'goal', 'definition_of_done', 'next_step', 'current_step',
    'evidence_summary', 'result_summary', 'title',
  ];
  while (Buffer.byteLength(JSON.stringify(projected), 'utf8') > WORKBOOK_PAGE_BYTES - 600) {
    const field = textFields.sort((left, right) => (
      Buffer.byteLength(String(projected[right] || ''), 'utf8')
      - Buffer.byteLength(String(projected[left] || ''), 'utf8')
    )).find((name) => String(projected[name] || '').length > 40);
    if (!field) break;
    projected[field] = String(projected[field]).slice(0, Math.max(40,
      Math.floor(String(projected[field]).length / 2)));
    if (!truncatedFields.includes(field)) truncatedFields.push(field);
  }
  return truncatedFields.length ? { ...projected, truncated_fields: truncatedFields } : projected;
}
function workbookPage(items, cursor, hasMore) {
  return {
    contract: 'WorkBookPage/v1', ok: true, verified: true, changed: false,
    partial: false, needs_followup: false, items,
    pagination: {
      cursor, returned: items.length, has_more: hasMore,
      next_cursor: hasMore ? cursor + items.length : null,
    },
  };
}
async function handleToonWerk(args) {
  const cursor = Math.max(0, Math.trunc(Number(args.cursor) || 0));
  const candidates = listWorkItems({
    statuses: ['open', 'blocked', 'done'], limit: 41, offset: cursor, order: 'workbook',
  }).map(workbookItem);
  const items = [];
  for (const candidate of candidates) {
    const proposed = workbookPage([...items, candidate], cursor, true);
    if (Buffer.byteLength(JSON.stringify(proposed), 'utf8') > WORKBOOK_PAGE_BYTES) break;
    items.push(candidate);
  }
  const page = workbookPage(items, cursor, items.length < candidates.length);
  return JSON.stringify(page);
}
function signalRequirementReceipt(result, binding, required) {
  return verifiedCompleteReceipt(result)
    && result.semanticBinding?.understandingSha256 === binding.understandingSha256
    && result.semanticBinding?.requirementId === required.requirementId
    && result.semanticBinding?.capabilityId === required.capabilityId
    && result.semanticBinding?.operationId === required.operationId;
}
function signalAssessmentOutcome(requested, binding, prior) {
  const receiptFor = (required) => prior.find((result) => (
    signalRequirementReceipt(result, binding, required)
  ));
  const mutating = binding.requirements.filter((required) => (
    required.effect !== TOOL_EFFECT.read
  ));
  const acted = mutating.some((required) => receiptFor(required)?.changed === true);
  if (acted) return { disposition: 'acted',
    assessment: 'Een exact geselecteerde effectvolle handeling is in deze beurt uitgevoerd en met een changed=true receipt bevestigd.' };
  if (requested === 'acted' && mutating.length) return null;
  const observed = binding.requirements.some((required) => (
    required.effect === TOOL_EFFECT.read && receiptFor(required)
  ));
  if (observed) return { disposition: 'observed',
    assessment: 'De actuele toestand is uitsluitend via de geselecteerde uitlezingen beoordeeld; er is in deze beurt geen fysieke handeling uitgevoerd.' };
  if (['dismissed', 'no_action'].includes(requested)) return { disposition: requested,
    assessment: 'De geselecteerde stappen zijn zonder verified changed=true handeling of actuele uitlezing afgesloten.' };
  return null;
}
async function handleSignalenAfhandelen(args, context, mode) {
        const binding = SIGNAL_ASSESSMENT_ARGS.get(args), batch = binding?.batch;
        const current = context.messageContext?.signalBatch;
        const valid = mode === 'autonomy' && validSignalBatch(batch) && validSignalBatch(current)
          && batch.signals.length > 0 && batch.batchId === current.batchId && batch.sha256 === current.sha256;
        if (!valid) return JSON.stringify({ ok: false, changed: false,
          reason: 'signaalbeoordeling mist de exact uitgegeven batch' });
        const prior = Array.isArray(context.priorToolResults) ? context.priorToolResults : [];
        const completed = binding.requirements.every((required) => prior.some((result) => (
          signalRequirementReceipt(result, binding, required))));
        if (!completed) return JSON.stringify({ ok: false, changed: false,
          reason: 'de signaalbeoordeling mist compleet bewijs voor een uitgegeven voorganger' });
        const outcome = signalAssessmentOutcome(args.dispositie, binding, prior);
        if (!outcome) return JSON.stringify({ ok: false, changed: false,
          reason: 'de gekozen signaaldispositie mist fysiek bewijs uit deze beurt' });
        return JSON.stringify({
          contract: SIGNAL_ASSESSMENT_CONTRACT, ok: true, changed: false, verified: true,
          batchId: batch.batchId, batchSha256: batch.sha256,
          acknowledgedIds: batch.signals.map((signal) => signal.id),
          ...outcome,
        });
}
async function executeRawTool(name, args = {}, context = {}) {
  const definition = TOOL_REGISTRY.find((tool) => tool.name === name);
  const { mode = 'chat' } = context;
  return definition ? definition.handler(args, context, mode)
    : JSON.stringify({ ok: false, reason: `onbekend gereedschap: ${name}` });
}
function readArtifactReceipt(name, args, context, ok, raw) {
  if (!ok || name !== 'lees_bestand') return null;
  try {
    const { readable } = resolveContextReadablePath(args.pad, context);
    const returned = Buffer.from(String(raw), 'utf8');
    const stat = fs.statSync(readable);
    return {
      path: workspaceDisplayPath(readable), bytes: returned.length, source_bytes: stat.size,
      sha256: createHash('sha256').update(returned).digest('hex'),
      complete_read: args.zoek == null && args.vanaf_regel == null
        && args.aantal_regels == null && stat.size === returned.length,
    };
  } catch { return null; }
}
function toolFailureFromError(name, args, error) {
  const safeError = redactSensitiveText(error?.message || error);
  const effect = toolEffect(name, args);
  const mutating = effect !== TOOL_EFFECT.read && effect !== 'unknown';
  return {
    error: safeError,
    ...normalizeToolFailure(error, classifyToolFailure(safeError), { mutating }),
  };
}
function verifiedWorkspaceListingManifest(envelope, args, context) {
  if (!envelope?.file_manifest) return null;
  try {
    const { readable } = resolveContextReadablePath(
      args.pad, context, { fallback: 'data/workspace' },
    );
    return validWorkspaceFileManifest(envelope.file_manifest, {
      root: readable,
      query: args.zoek || '',
    }) ? envelope.file_manifest : null;
  } catch { return null; }
}

function formatToolResult(name, args, raw, context) {
  const listingEnvelope = parseWorkspaceListingEnvelope(name, raw);
  const normalizedRaw = listingEnvelope ? JSON.stringify(listingEnvelope.entries) : raw;
  const state = normalizeActionResultState({
    raw: normalizedRaw, changesByDefault: toolChangesByDefault(name),
    verification: toolVerification(name, args), effect: toolEffect(name, args), tool: name,
  });
  const { parsed, ok, changed, verified, reason, summary, data,
    needsFollowup, partial, failure } = state;
  const artifactReceipt = readArtifactReceipt(name, args, context, ok, raw);
  const fileManifest = ok
    ? verifiedWorkspaceListingManifest(listingEnvelope, args, context) : null;
  const manifestIncomplete = name === 'lijst_bestanden' && Boolean(listingEnvelope?.file_manifest)
    && (!fileManifest || fileManifest.complete !== true || fileManifest.truncated !== false);
  const resultVerified = verified && !manifestIncomplete;
  const resultNeedsFollowup = needsFollowup || manifestIncomplete;
  const resultPartial = partial || manifestIncomplete;
  const resultFailure = failure || (manifestIncomplete ? {
    failure_class: 'partial_result', retryable: false, effect_uncertain: false,
  } : null);
  return JSON.stringify({
    contract: 'ActionResult/v1',
    tool: name,
    ok,
    changed,
    verified: resultVerified,
    evidence: { source: name, summary },
    needs_followup: resultNeedsFollowup,
    partial: resultPartial,
    ...(parsed?.waiting_for ? { waiting_for: clean(parsed.waiting_for, 80) } : {}),
    ...(resultFailure || {}),
    risk: context.mode === 'autonomy' && toolChangesByDefault(name) ? 'autonomous' : 'normal',
    human_message: clean(parsed?.message || parsed?.info || reason, 500),
    ...(artifactReceipt ? { artifact_receipt: artifactReceipt } : {}),
    ...(fileManifest ? { file_manifest: fileManifest } : {}),
    ...(!ok && reason ? { error: reason } : {}),
    data,
  });
}
function failedActionResult(name, error, {
  needsFollowup = false, humanMessage = '', ...failure
} = /** @type {any} */ ({})) {
  return JSON.stringify({
    contract: 'ActionResult/v1', tool: name, ok: false, changed: false, verified: false,
    evidence: null, needs_followup: needsFollowup, partial: false, risk: 'normal',
    human_message: humanMessage, error: clean(error, 1200), ...failure,
  });
}

function explorationEvidenceContextValid(name, args, plan, metadata, explorationId, episodeId) {
  return metadata?.kind === 'parent' && metadata?.issuer?.mode === 'autonomy'
    && plan?.toolName === name && args === plan.args
    && ['read', 'discovery'].includes(String(metadata.operation?.effect || ''))
    && /^[a-f0-9]{64}$/u.test(String(metadata.issuer?.understandingSha256 || ''))
    && Number.isSafeInteger(explorationId) && explorationId >= 1
    && Number.isSafeInteger(episodeId) && episodeId >= 1;
}

function explorationEvidenceEpisodeValid(exploration, episode, explorationId, episodeId) {
  const explorationAttentionKey = `attention:exploration:${explorationId}`;
  const aspirationId = Number(exploration?.aspiration_id);
  const aspirationStartLineage = Number.isSafeInteger(aspirationId) && aspirationId > 0
    && Number(exploration?.source_episode_id) === episodeId
    && episode?.goal_key === `attention:aspiration:${aspirationId}`;
  return Boolean(episode && episode.kind === 'autonomous_attention' && episode.status === 'active'
    && (episode.focus_ref === explorationAttentionKey
      || episode.goal_key === explorationAttentionKey
      || aspirationStartLineage));
}

function stageExplorationEvidenceReceipt(name, args, output, context) {
  const plan = context?.issuedPlan;
  const metadata = plan && typeof plan === 'object' ? ISSUED_PLAN_METADATA.get(plan) : null;
  const explorationId = Number(context?.activeExplorationId);
  const episodeId = Number(context?.cognitiveEpisodeId);
  if (!explorationEvidenceContextValid(
    name, args, plan, metadata, explorationId, episodeId,
  )) return false;
  let result;
  try { result = JSON.parse(String(output || '')); } catch { return false; }
  const evidence = explorationEvidenceSummary(result?.evidence?.summary);
  if (!verifiedCompleteReceipt(result) || !evidence) return false;
  const exploration = /** @type {any} */ (db.prepare(`SELECT id,source,source_episode_id,
    aspiration_id,topic_key,question,expected_observation,revision,action_count,status,next_wake_ts
    FROM eva_explorations WHERE id=?`).get(explorationId));
  if (!exploration || exploration.source !== 'self'
    || !['chosen', 'running', 'waiting'].includes(String(exploration.status || ''))
    || (exploration.next_wake_ts && Date.parse(exploration.next_wake_ts) > Date.now())) return false;
  let episode;
  try {
    episode = /** @type {any} */ (db.prepare(`SELECT id,kind,status,focus_ref,goal_key
      FROM cognitive_episodes WHERE id=?`).get(episodeId));
  } catch { return false; }
  if (!explorationEvidenceEpisodeValid(exploration, episode, explorationId, episodeId)) {
    return false;
  }
  const body = Object.freeze({
    contract: 'ExplorationEvidenceReceipt/v1',
    explorationId,
    explorationRevision: Number(exploration.revision),
    explorationActionCount: Number(exploration.action_count),
    episodeId,
    tool: name,
    capabilityId: String(plan.capabilityId || ''),
    operationId: String(plan.operationId || ''),
    requirementId: String(plan.requirementId || ''),
    effect: String(metadata.operation.effect),
    understandingSha256: String(metadata.issuer.understandingSha256),
    argsSha256: createHash('sha256').update(canonicalJson(args)).digest('hex'),
    outputSha256: createHash('sha256').update(String(output)).digest('hex'),
    evidenceSha256: createHash('sha256').update(evidence).digest('hex'),
    topicKey: clean(exploration.topic_key, 120) || null,
    topicSha256: explorationEvidenceTopicSha256(exploration),
    observedAt: new Date().toISOString(),
  });
  const receiptSha256 = createHash('sha256').update(canonicalJson(body)).digest('hex');
  const receipt = Object.freeze({
    ...body,
    receiptId: `exploration-evidence:${receiptSha256}`,
    receiptSha256,
  });
  PENDING_EXPLORATION_EVIDENCE_RECEIPTS.set(receiptSha256, receipt);
  while (PENDING_EXPLORATION_EVIDENCE_RECEIPTS.size > 64) {
    PENDING_EXPLORATION_EVIDENCE_RECEIPTS.delete(
      PENDING_EXPLORATION_EVIDENCE_RECEIPTS.keys().next().value,
    );
  }
  return true;
}

export async function executeTool(name, args = {}, context = {}) {
  if (typeof context.messageContext?.isCurrent === 'function'
    && !context.messageContext.isCurrent()) {
    return failedActionResult(name, 'voicebeurt is ingehaald', {
      failure_class: 'conflict',
      retryable: false, effect_uncertain: false,
      human_message: 'Deze voicebeurt is ingehaald door een nieuwere opdracht.',
    });
  }
  if (!toolAllowedByPolicy(name, args, context.effectPolicy)) {
    const effect = toolEffect(name, args);
    const reason = `gereedschap ${name} met effect ${effect} is niet toegestaan binnen de uitvoeringsgrens van deze beurt`;
    return failedActionResult(name, reason, {
      failure_class: 'policy', retryable: false, effect_uncertain: false, humanMessage: reason,
    });
  }
  const economicPolicy = explorationEconomicExecutionPolicy(name, args, context,
    context?.issuedPlan ? ISSUED_PLAN_METADATA.get(context.issuedPlan) : null,
    toolEffect(name, args));
  if (!economicPolicy.allowed) {
    return failedActionResult(name, economicPolicy.reason, {
      failure_class: 'policy', retryable: false, effect_uncertain: false,
      humanMessage: economicPolicy.reason,
    });
  }
  try {
    const raw = redactSensitiveText(await executeRawTool(name, args, {
      ...context,
      ...(economicPolicy.target ? { economicFamilyDelivery: economicPolicy.target } : {}),
    }));
    const output = formatToolResult(name, args, raw, context);
    stageExplorationEvidenceReceipt(name, args, output, context);
    return output;
  } catch (error) {
    if (['EVA_SELF_ATTENTION_GOAL_BOUNDARY', 'EVA_SELF_ATTENTION_EXPLORATION_BOUNDARY']
      .includes(error?.code)) throw error;
    const { error: safeError, ...failure } = toolFailureFromError(name, args, error);
    const generationFailure = error?.workspaceGeneration;
    return failedActionResult(name, safeError, {
      needsFollowup: true, ...failure,
      ...(generationFailure?.contract === 'WorkspaceGenerationFailure/v1'
        ? { workspace_generation: generationFailure } : {}),
    });
  }
}

export function makeExecutor(
  { mode = 'chat', asker = null, ...context } = /** @type {any} */ ({}),
) {
  return (name, args) => executeTool(name, args,
    { ...context, mode, sender: context.sender || asker || null });
}
export const __toolsTest = {
  autonomousMessageHasNewResult, autonomousMessageIsInternalProgress,
  autonomousMessagesOverlap, commercialResearchEvidenceBoundary, formatToolResult,
  normalizeToolFailure, toolFailureFromError, emptyAutonomousWebFinding,
  groundContextualHomeCommand, homeOperationOutcome, operateHome,
  resolveContextualHomeCommand, resolveHomeEntity, normalizeHaReadPath,
  proofToolFitsSubject, evidenceMatches,
  announceOncePerTurn,
  WORKSPACE_FILE_MANIFEST_LIMITS,
};
