import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { config } from '../config.js';
import { isNight } from '../util.js';
import {
  addMessage,
  addMemory,
  addReminder,
  cancelReminder,
  getTimeline,
  kvGet,
  kvSet,
  logTimeline,
  openReminders,
  searchMemories,
  searchTelegramMessages,
} from '../db.js';
import {
  allStates,
  cachedStates,
  callService,
  getState,
  listPersonConfigurations,
  listCameras,
  presenceDetails,
  restCall,
  setPersonTrackers,
  announceOnVoiceAssistSatellite,
  verifyEntityState,
  whoIsHome,
} from '../integrations/homeassistant.js';
import { searchWeb } from '../brain/llm.js';
import { listWorkItems } from './work.js';
import {
  activateCapability,
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
import { listExplorations, startExploration, updateExploration } from './curiosity.js';
import {
  episodeNeedsDelivery,
  episodeHasDelivery,
  createDurableWork,
  episodeForWork,
  goalEpisodeById,
  manageGoal,
  recordEpisodeDelivery,
  transitionGoalEpisode,
  updateDurableWork,
} from './portfolio.js';
import { listSecretKeys } from '../self/vault.js';
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
import {
  toolAllowedByPolicy,
  toolAvailableInAutonomy,
  toolChangesByDefault,
  toolEffect,
  toolHasTag,
  toolProofRank,
  toolVerification,
} from './toolpolicy.js';

const MAX_TOOL_OUTPUT = 24_000;
const HOME_LOCALITY_KEY = 'eva_home_locality_v1';
const AUTONOMY_TOPIC_QUIET_MS = 7 * 24 * 3600_000;
const READABLE_ROOTS = [config.root, config.workspaceDir].map((value) => path.resolve(value));
const WRITABLE_ROOT = path.resolve(config.workspaceDir);
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
const HOME_DOMAINS = new Set([
  'light', 'switch', 'input_boolean', 'fan', 'cover', 'media_player',
  'climate', 'scene', 'script', 'vacuum',
]);

let messenger = null;
let photoSender = null;
let voiceSender = null;

const str = (description) => ({ type: 'string', description });
const num = (description) => ({ type: 'number', description });
const obj = (properties, required = []) => ({
  type: 'object',
  properties,
  required,
  additionalProperties: false,
});

export function registerMessenger(fn) {
  messenger = typeof fn === 'function' ? fn : null;
}

export function registerPhotoSender(fn) {
  photoSender = typeof fn === 'function' ? fn : null;
}

export function registerVoiceSender(fn) {
  voiceSender = typeof fn === 'function' ? fn : null;
}

export async function sendRegisteredMessage(text, recipient = null, options = null) {
  if (!messenger) return { ok: false, info: 'Telegram is niet actief' };
  return messenger(String(text || ''), recipient, options);
}

export async function sendRegisteredPhoto(buffer, contentType = 'image/jpeg', caption = '', recipient = null, options = null) {
  if (!photoSender) return { ok: false, info: 'Telegram-fotoverzending is niet actief' };
  return photoSender(buffer, contentType, caption, recipient, options);
}

export async function sendRegisteredVoice(buffer, spokenText, caption = 'Eva', recipient = null, options = null) {
  if (!voiceSender) return { ok: false, info: 'Telegram-spraakverzending is niet actief' };
  return voiceSender(buffer, caption || spokenText || 'Eva', recipient, options);
}

function clean(value, limit = MAX_TOOL_OUTPUT) {
  return String(value ?? '').slice(0, limit);
}

function normalize(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/[_./-]+/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function words(value) {
  const stop = new Set([
    'de', 'het', 'een', 'van', 'in', 'op', 'bij', 'naar', 'voor', 'met',
    'zet', 'doe', 'maak', 'schakel', 'graag', 'even', 'nu', 'aan', 'uit',
    'open', 'dicht', 'alle', 'alles', 'mijn', 'ons', 'onze',
  ]);
  return normalize(value).split(' ').filter((word) => word.length > 1 && !stop.has(word));
}

const TOPIC_GENERIC_WORDS = new Set([
  'alleen', 'blijft', 'blijven', 'daarom', 'geeft', 'kunnen', 'morgen',
  'nacht', 'verwacht', 'waarschijnlijk', 'vannacht', 'worden',
]);

const AUTONOMOUS_TOPIC_PATTERNS = Object.freeze([
  ['deur', /\b(?:achterdeur|voordeur|tuindeur|deur)\b/i],
  ['raam', /\b(?:ramen?|dakraam)\b/i],
  ['warmte', /\b(?:hitte|warmte|warm|benauwd|koelte|koeler|temperatuur)\b/i],
  ['regen', /\b(?:regen|regent|regenbui|neerslag|droog)\b/i],
  ['aanwezigheid', /\b(?:thuis|aanwezig|afwezig|vertrokken|aangekomen)\b/i],
  ['voice', /\b(?:voice\s*pe|assist[_ -]?satellite|spraak|omroep)\b/i],
  ['overkapping', /\b(?:overkapping|terras)\b/i],
]);

function autonomousTopicKeys(value) {
  return new Set(AUTONOMOUS_TOPIC_PATTERNS
    .filter(([, pattern]) => pattern.test(String(value || '')))
    .map(([key]) => key));
}

const AUTONOMOUS_CONDITION_PATTERNS = Object.freeze([
  ['open', /\bopen\b/i], ['dicht', /\bdicht|gesloten\b/i],
  ['regen', /\bregent|regenbui|neerslag\b/i], ['droog', /\bdroog\b/i],
  ['warmer', /\bwarmer|opwarming\b/i], ['koeler', /\bkoeler|afkoeling\b/i],
  ['thuis', /\bthuis|aanwezig|aangekomen\b/i], ['weg', /\bafwezig|weg|vertrokken\b/i],
  ['beschikbaar', /\bbeschikbaar|verbonden|online\b/i],
  ['onbeschikbaar', /\bonbeschikbaar|unavailable|offline\b/i],
]);

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

const SYNONYM_GROUPS = [
  new Set(['licht', 'lichten', 'lamp', 'lampen', 'verlichting']),
  new Set(['buiten', 'tuin', 'terras', 'overkapping']),
  new Set(['heater', 'verwarming', 'verwarmer', 'terrasverwarmer']),
  new Set(['tv', 'televisie', 'media', 'speler']),
  new Set(['rolluik', 'gordijn', 'gordijnen', 'cover']),
];

function relatedWord(a, b) {
  if (a === b) return true;
  return SYNONYM_GROUPS.some((group) => group.has(a) && group.has(b));
}

function friendlyName(state) {
  return String(state?.attributes?.friendly_name || state?.entity_id || '');
}

function homeLocality(states) {
  const home = states.find((state) => state.entity_id === 'zone.home');
  const homeLat = Number(home?.attributes?.latitude);
  const homeLon = Number(home?.attributes?.longitude);
  const candidates = states
    .filter((state) => state.entity_id.includes('geocoded_location'))
    .map((state) => {
      const location = state.attributes?.Location;
      const lat = Number(Array.isArray(location) ? location[0] : NaN);
      const lon = Number(Array.isArray(location) ? location[1] : NaN);
      const locality = String(state.attributes?.Locality || '').trim();
      const region = String(state.attributes?.['Administrative Area'] || '').trim();
      const distance = Number.isFinite(homeLat) && Number.isFinite(homeLon)
        && Number.isFinite(lat) && Number.isFinite(lon)
        ? Math.hypot(homeLat - lat, homeLon - lon)
        : Number.POSITIVE_INFINITY;
      return { locality, region, distance };
    })
    .filter((item) => item.locality)
    .sort((a, b) => a.distance - b.distance);
  const best = candidates.find((item) => item.distance < 0.02) || candidates[0] || null;
  if (best) return [best.locality, best.region].filter(Boolean).join(', ');
  return home?.attributes?.friendly_name || 'Thuis';
}

function compactState(state) {
  return {
    entity_id: state.entity_id,
    name: friendlyName(state),
    state: state.state,
    updated: state.last_updated || state.last_changed || null,
    unit: state.attributes?.unit_of_measurement || null,
    current_temperature: state.attributes?.current_temperature ?? null,
    temperature: state.attributes?.temperature ?? null,
  };
}

function compactHomeOverview(states) {
  const people = states
    .filter((state) => state.entity_id.startsWith('person.'))
    .map((state) => `${friendlyName(state)}: ${state.state}`);
  const lights = states
    .filter((state) => state.entity_id.startsWith('light.') && state.state === 'on')
    .slice(0, 16)
    .map(friendlyName);
  const openings = states
    .filter((state) => state.entity_id.startsWith('binary_sensor.')
      && ['door', 'window', 'garage_door', 'opening'].includes(state.attributes?.device_class)
      && state.state === 'on')
    .slice(0, 16)
    .map(friendlyName);
  const temperatures = states
    .filter((state) => state.entity_id.startsWith('climate.')
      && Number.isFinite(Number(state.attributes?.current_temperature)))
    .slice(0, 16)
    .map((state) => `${friendlyName(state)} ${state.attributes.current_temperature}°C`);
  return [
    people.length ? `Aanwezigheid: ${people.join(', ')}.` : '',
    lights.length ? `Lampen aan: ${lights.join(', ')}.` : 'Er staan geen lampen als aan geregistreerd.',
    openings.length ? `Open: ${openings.join(', ')}.` : 'Er staan geen deuren of ramen als open geregistreerd.',
    temperatures.length ? `Kamertemperaturen: ${temperatures.join(', ')}.` : '',
  ].filter(Boolean).join('\n');
}

export async function homeContextText({
  fresh: _fresh = false,
  query = '',
  includeOverview = true,
  includeWeather = true,
} = {}) {
  let states = [];
  if (!includeOverview && !includeWeather) {
    states = cachedStates();
    if (!states.length) {
      const known = String(kvGet(HOME_LOCALITY_KEY, '') || '').trim();
      return known
        ? `Woonplaats: ${known}. Actuele huiswaarden zijn voor deze vrije beurt niet opgevraagd.`
        : 'De woonplaats en actuele huiswaarden zijn voor deze vrije beurt niet opgevraagd.';
    }
  } else {
    try {
      states = await allStates();
    } catch {
      return 'Home Assistant is nu niet bereikbaar; plaats en actuele huiswaarden zijn niet bevestigd.';
    }
  }
  const locality = homeLocality(states);
  if (locality && locality !== 'Thuis') kvSet(HOME_LOCALITY_KEY, locality);
  const overview = compactHomeOverview(states);
  const weather = states.find((state) => state.entity_id.startsWith('weather.'));
  const locationLine = `Woonplaats: ${locality}. Home Assistant-zone: ${friendlyName(states.find((state) => state.entity_id === 'zone.home')) || 'Thuis'}.`;
  const weatherLine = includeWeather && weather
    ? `Weerbron thuis: ${weather.entity_id}, ${weather.state}, ${weather.attributes?.temperature ?? 'onbekend'} ${weather.attributes?.temperature_unit || '°C'}, bijgewerkt ${weather.last_updated || 'onbekend'}.`
    : includeWeather ? 'Er is geen actuele weather-entiteit gevonden.' : '';

  if (!query || !includeOverview) {
    return [locationLine, weatherLine, includeOverview ? overview : ''].filter(Boolean).join('\n');
  }
  const queryWords = words(query);
  const matches = states
    .map((state) => {
      const candidateWords = words(`${state.entity_id} ${friendlyName(state)}`);
      const score = queryWords.reduce(
        (sum, word) => sum + (candidateWords.some((candidate) => relatedWord(word, candidate)) ? 1 : 0),
        0,
      );
      return { state, score };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 24)
    .map((item) => compactState(item.state));
  return [
    locationLine,
    weatherLine,
    overview,
    matches.length ? `Relevante entiteiten: ${JSON.stringify(matches)}` : '',
  ].filter(Boolean).join('\n');
}

function entityScore(state, target) {
  const targetNormalized = normalize(target);
  const label = normalize(`${state.entity_id} ${friendlyName(state)}`);
  const targetWords = words(target);
  const candidateWords = words(label);
  const objectId = String(state.entity_id.split('.')[1] || '').toLowerCase();
  let score = 0;
  if (state.entity_id.toLowerCase() === String(target || '').toLowerCase()) score += 1000;
  // De slug is Home Assistants stabiele identiteit. Een exact passend object-id
  // wint van vijf integraties die toevallig dezelfde friendly_name publiceren.
  if (targetWords.length && targetWords.join('_') === objectId) score += 300;
  if (targetNormalized && label.includes(targetNormalized)) score += 120;
  for (const targetWord of targetWords) {
    if (candidateWords.includes(targetWord)) score += 24;
    else if (candidateWords.some((candidate) => relatedWord(targetWord, candidate))) score += 9;
  }
  const domain = state.entity_id.split('.')[0];
  if (targetWords.some((word) => ['licht', 'lichten', 'lamp', 'lampen', 'verlichting'].includes(word))) {
    score += domain === 'light' ? 30 : -20;
  }
  if (targetWords.some((word) => ['gordijn', 'gordijnen', 'rolluik'].includes(word))) {
    score += domain === 'cover' ? 30 : -20;
  }
  if (/\b(?:alle|alles)\b/i.test(target) && domain === 'light' && state.entity_id.split('_').length <= 2) score += 8;
  if (['unavailable', 'unknown'].includes(state.state)) score -= 10;
  return score;
}

function resolveHomeEntity(states, target) {
  const direct = states.find((state) => state.entity_id.toLowerCase() === String(target || '').toLowerCase());
  if (direct) return { state: direct, alternatives: [], ambiguous: false };
  const ranked = states
    .filter((state) => HOME_DOMAINS.has(state.entity_id.split('.')[0]))
    .map((state) => ({ state, score: entityScore(state, target) }))
    .filter((item) => item.score > 8)
    .sort((a, b) => b.score - a.score);
  const first = ranked[0];
  const second = ranked[1];
  if (!first) return { state: null, alternatives: [], ambiguous: false };
  // Een hoge score maakt een gelijkspel niet minder dubbelzinnig. Alleen een
  // duidelijke winnaar (bijvoorbeeld een exacte entity-id/slug) mag handelen.
  const ambiguous = !!second && second.score >= first.score - 3;
  return {
    state: ambiguous ? null : first.state,
    ambiguous,
    alternatives: ranked.slice(0, 6).map((item) => ({
      entity_id: item.state.entity_id,
      name: friendlyName(item.state),
      state: item.state.state,
      score: item.score,
    })),
  };
}

function actionFor(domain, action, value) {
  const normalized = normalize(action);
  if (['aan', 'on', 'inschakelen', 'start', 'activeer'].includes(normalized)) {
    return { service: 'turn_on', expected: 'on', data: {} };
  }
  if (['uit', 'off', 'uitschakelen', 'stop'].includes(normalized)) {
    return { service: 'turn_off', expected: 'off', data: {} };
  }
  if (['toggle', 'wissel'].includes(normalized)) return { service: 'toggle', expected: null, data: {} };
  if (['open', 'openen'].includes(normalized) && domain === 'cover') {
    return { service: 'open_cover', expected: 'open', data: {} };
  }
  if (['dicht', 'sluit', 'sluiten'].includes(normalized) && domain === 'cover') {
    return { service: 'close_cover', expected: 'closed', data: {} };
  }
  if (['pauze', 'pauzeren'].includes(normalized) && domain === 'media_player') {
    return { service: 'media_pause', expected: 'paused', data: {} };
  }
  if (['speel', 'hervat'].includes(normalized) && domain === 'media_player') {
    return { service: 'media_play', expected: 'playing', data: {} };
  }
  if (['temperatuur', 'set temperature'].includes(normalized) && domain === 'climate' && Number.isFinite(Number(value))) {
    return { service: 'set_temperature', expected: null, data: { temperature: Number(value) } };
  }
  return null;
}

function homeOperationOutcome(expected, after, verification) {
  if (expected == null) {
    const verified = after != null;
    return { ok: verified, verified, partial: false, needsFollowup: !verified };
  }
  const complete = verification?.ok === true;
  const reachable = verification?.reachableVerified === true;
  const partial = !complete && reachable
    && Array.isArray(verification?.unavailable) && verification.unavailable.length > 0;
  const verified = complete || reachable;
  return { ok: verified, verified, partial, needsFollowup: partial || !verified };
}

function homeOperationMessage(name, domain, expected, after, verification, outcome) {
  const state = expected === 'on' ? 'aan' : expected === 'off' ? 'uit' : after?.state || 'zoals gevraagd';
  if (!outcome.partial) {
    return outcome.verified
      ? `${name} staat ${state}.`
      : `${name} is aangestuurd, maar de gevraagde toestand is nog niet bevestigd.`;
  }
  const unavailable = Array.isArray(verification?.unavailable) ? verification.unavailable : [];
  const reachable = Array.isArray(verification?.reachable) ? verification.reachable : [];
  const noun = domain === 'light' ? (unavailable.length === 1 ? 'lamp' : 'lampen') : (unavailable.length === 1 ? 'onderdeel' : 'onderdelen');
  const names = unavailable.slice(0, 4).map((item) => item.name || item.entity_id).filter(Boolean);
  return `${name} staat ${state} op alle ${reachable.length} bereikbare onderdelen. ${unavailable.length} ${noun} ${unavailable.length === 1 ? 'is' : 'zijn'} offline${names.length ? `: ${names.join(', ')}` : ''}.`;
}

async function operateHome({ target, action, value = null }) {
  const exactEntityId = /^[a-z_]+\.[a-z0-9_]+$/i.test(String(target || '').trim())
    ? String(target).trim()
    : null;
  const exactState = exactEntityId ? await getState(exactEntityId) : null;
  const states = exactEntityId ? (exactState ? [exactState] : []) : await allStates();
  const resolved = resolveHomeEntity(states, target);
  if (!resolved.state) {
    return {
      ok: false,
      changed: false,
      reason: resolved.ambiguous ? 'doel is dubbelzinnig' : 'geen passend Home Assistant-doel gevonden',
      alternatives: resolved.alternatives,
    };
  }
  const entity = resolved.state;
  const domain = entity.entity_id.split('.')[0];
  if (domain === 'vacuum') {
    return {
      ok: false,
      changed: false,
      reason: 'stofzuigers gebruiken eigen start/pauze/stop/dock- en camerabetekenis; gebruik gebruik_robot',
      entity: compactState(entity),
    };
  }
  const operation = actionFor(domain, action, value);
  if (!operation) {
    return { ok: false, changed: false, reason: `actie "${action}" past niet veilig bij ${domain}`, entity: compactState(entity) };
  }
  if (operation.expected && entity.state === operation.expected) {
    const already = await verifyEntityState(entity.entity_id, operation.expected, {
      timeoutMs: 2_000,
      pollMs: 250,
    });
    if (already?.ok || already?.reachableVerified) {
      const outcome = homeOperationOutcome(operation.expected, {
        entity_id: entity.entity_id,
        state: already.state,
      }, already);
      return {
        ok: outcome.ok,
        changed: false,
        verified: outcome.verified,
        partial: outcome.partial,
        needs_followup: outcome.needsFollowup,
        entity_id: entity.entity_id,
        name: friendlyName(entity),
        before: entity.state,
        after: already.state,
        evidence: already,
        message: outcome.partial
          ? homeOperationMessage(friendlyName(entity), domain, operation.expected, { state: already.state }, already, outcome)
          : `${friendlyName(entity)} stond al ${operation.expected === 'on' ? 'aan' : 'uit'}.`,
      };
    }
  }
  const payload = { entity_id: entity.entity_id, ...operation.data };
  const expected = operation.service === 'toggle' && ['on', 'off'].includes(entity.state)
    ? (entity.state === 'on' ? 'off' : 'on')
    : operation.expected;
  const serviceCall = callService(domain, operation.service, payload)
    .then((result) => ({ result, error: null }))
    .catch((error) => ({ result: null, error }));
  if (expected == null) {
    const service = await serviceCall;
    if (service.error) {
      return {
        ok: false,
        changed: false,
        verified: false,
        entity_id: entity.entity_id,
        name: friendlyName(entity),
        before: entity.state,
        expected: null,
        reason: service.error?.message || String(service.error),
      };
    }
  }
  const verification = await verifyEntityState(entity.entity_id, expected, {
    timeoutMs: 3_500,
    pollMs: 300,
  });
  if (expected != null && !verification?.ok && !verification?.reachableVerified) {
    const service = await serviceCall;
    if (service.error) {
      return {
        ok: false,
        changed: false,
        verified: false,
        entity_id: entity.entity_id,
        name: friendlyName(entity),
        before: entity.state,
        expected,
        reason: service.error?.message || String(service.error),
        evidence: { assessment: verification },
      };
    }
  }
  const after = expected != null && (verification?.ok || verification?.reachableVerified)
    ? {
        entity_id: entity.entity_id,
        state: verification.state,
        attributes: entity.attributes,
      }
    : await getState(entity.entity_id, {
        timeoutMs: 1_500,
        preferCache: false,
      }).catch(() => null);
  const outcome = homeOperationOutcome(expected, after, verification);
  return {
    ok: outcome.ok,
    changed: entity.state !== after?.state,
    verified: outcome.verified,
    partial: outcome.partial,
    needs_followup: outcome.needsFollowup,
    entity_id: entity.entity_id,
    name: friendlyName(entity),
    service: `${domain}.${operation.service}`,
    before: entity.state,
    after: after?.state ?? null,
    evidence: {
      readback: after ? compactState(after) : null,
      assessment: verification,
    },
    message: homeOperationMessage(
      friendlyName(entity),
      domain,
      expected,
      after,
      verification,
      outcome,
    ),
  };
}

function pathInside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
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
  const absolute = path.resolve(raw.startsWith('/') ? raw : path.join(config.root, raw));
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
  const raw = String(input || '').trim();
  const absolute = path.resolve(raw.startsWith('/') ? raw : path.join(config.root, raw));
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
    ? path.resolve(value.startsWith('/') ? value : path.join(config.root, value))
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

async function runProgram(program, args = [], cwd = '', expectedExitCodes = [0]) {
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
    '--property=RuntimeMaxSec=120',
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
      reject(new Error('programma duurde langer dan 120 seconden'));
    }, 130_000);
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

export const TOOLS = [
  {
    name: 'huis_status',
    description: 'Lees de actuele, gegronde Home Assistant-context: woonplaats, weer, aanwezigheid, lampen, deuren, media en relevante entiteiten.',
    parameters: obj({ vraag: str('Optioneel onderwerp of apparaat om gericht terug te lezen') }),
  },
  {
    name: 'bedien_huis',
    description: 'Bedien een normaal, omkeerbaar Home Assistant-apparaat en lees de toestand daarna terug. Gebruik gebruik_robot voor de stofzuiger; die is geen aan/uit-schakelaar.',
    parameters: obj({
      doel: str('Apparaat of entity-id, bijvoorbeeld "heater buiten" of "light.keuken_3"'),
      actie: str('aan, uit, toggle, open, dicht, pauze, speel of temperatuur'),
      waarde: num('Optionele numerieke waarde, bijvoorbeeld de doeltemperatuur'),
    }, ['doel', 'actie']),
  },
  {
    name: 'gebruik_robot',
    description: 'Gebruik Erik als echt beweegbaar zintuig of stofzuiger. Status en kaart zijn alleen waarneming. Bij een read-only verzoek of "laat hem stilstaan" gebruik je status/kaart en stuur je geen stop-service alleen ter geruststelling. Kijk haalt de frontcamera op. Verken draait alleen een korte puls als eerst live beeld beschikbaar is, stopt altijd en leest de eindstaat terug. Start, pauze, stop en dock gebruiken de echte vacuum-services.',
    parameters: obj({
      actie: str('status, kaart, kijk, verken, start, pauze, stop of dock'),
      richting: str('Bij verken: links of rechts'),
      duur_ms: num('Bij verken: 150-500 milliseconden'),
    }, ['actie']),
  },
  {
    name: 'ha_lees',
    description: 'Lees een exacte Home Assistant-entiteit of een veilige GET-route; wijzigt niets. Voor "gebeurde dit/wanneer/waardoor": vind eerst de exacte entity-id met huis_status en lees daarna echt `/api/logbook/<ISO-start>?entity=<urlencoded-entity-id>&end_time=<ISO-eind>`. Een algemene states-uitlezing is geen tijdlijnonderzoek.',
    parameters: obj({
      entity_id: str('Exacte entity-id (optioneel)'),
      pad: str('Veilige Home Assistant API-route (optioneel)'),
    }),
  },
  {
    name: 'wie_is_thuis',
    description: 'Lees actuele aanwezigheid; met een naam worden de beschikbare bronnen voor die persoon vergeleken.',
    parameters: obj({ persoon: str('Optionele naam') }),
  },
  {
    name: 'beheer_aanwezigheid',
    description: 'Bekijk of wijzig welke bestaande Home Assistant device_trackers een persoon voor aanwezigheid gebruikt. Gebruik "bekijk" om de echte person-config te lezen en "stel_trackers_in" om na een expliciete opdracht de volledige gewenste trackerlijst te zetten en terug te verifiëren. Zeg nooit dat dit handmatig moet voordat deze tool is geprobeerd.',
    parameters: obj({
      actie: str('bekijk of stel_trackers_in'),
      persoon: str('Exacte persoonsnaam of person-id'),
      device_trackers: { type: 'array', items: { type: 'string' }, description: 'Bij stel_trackers_in: volledige gewenste lijst met bestaande device_tracker entity-ids' },
    }, ['actie', 'persoon']),
  },
  {
    name: 'zoek_internet',
    description: 'Doe actueel internetonderzoek met directe bronlinks. Gebruik dit voor actuele feiten, prijzen, markt, weerbronnen en technische documentatie.',
    parameters: obj({ vraag: str('Concrete onderzoeksvraag, inclusief de juiste plaats of context') }, ['vraag']),
  },
  {
    name: 'beheer_agentmail',
    description: 'Gebruik Eva haar echte AgentMail-inbox rechtstreeks via de officiële API, zonder browser of losse SDK. Kan aanmelden, verifiëren, e-mail versturen, recente berichten lezen en berichten doorzoeken. De API-sleutel blijft uitsluitend versleuteld in Eva haar kluis.',
    parameters: obj({
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
    }, ['actie']),
  },
  {
    name: 'lees_bestand',
    description: 'Lees of doorzoek een bestand uit Eva haar project of data/workspace. Gebruik zoek bij grote bronbestanden om relevante regels met context te vinden, en lees alle expliciet genoemde bestanden. Geheime bestanden zijn afgeschermd.',
    parameters: obj({
      pad: str('Pad naar het bestand'),
      zoek: str('Optionele tekst of eenvoudige reguliere expressie; retourneert treffers met regelnummers en context'),
      vanaf_regel: num('Optionele eerste regel, vanaf 1'),
      aantal_regels: num('Optioneel aantal regels, maximaal 400'),
    }, ['pad']),
  },
  {
    name: 'schrijf_bestand',
    description: 'Schrijf of overschrijf een bestand binnen data/workspace. Het pad begint altijd met data/workspace/. Schrijf ook voorbeeldinvoer met deze tool; maak die niet via inline-code. De werkruimte behandelt .js expliciet als CommonJS (require/module.exports); gebruik .mjs als je ESM wilt.',
    parameters: obj({ pad: str('Pad vanaf de projectroot, beginnend met data/workspace/'), inhoud: str('Volledige bestandsinhoud') }, ['pad', 'inhoud']),
  },
  {
    name: 'lijst_bestanden',
    description: 'Lijst bestanden en mappen binnen Eva haar project of data/workspace. Gebruik zoek om in een grote map alleen passende namen te krijgen en volgorde=nieuwste wanneer de meest recente kandidaat nodig is.',
    parameters: obj({
      pad: str('Map om te bekijken, standaard data/workspace'),
      zoek: str('Optionele woorden of eenvoudige reguliere expressie voor de naam'),
      volgorde: str('naam of nieuwste'),
    }),
  },
  {
    name: 'voer_uit',
    description: 'Voer zonder shell een eerder met schrijf_bestand opgeslagen lokaal programma uit binnen data/workspace. Toegestaan: node, npm, npx, python, python3 en git. Inline-opties zoals node -e, python -c en python -m zijn niet toegestaan. Geef verwachte_exitcodes mee wanneer bijvoorbeeld een audit bewust 1 gebruikt voor gevonden problemen.',
    parameters: obj({
      programma: str('Programmanaam'),
      argumenten: { type: 'array', items: { type: 'string' }, description: 'Afzonderlijke argumenten' },
      werkmap: str('Werkmap binnen data/workspace'),
      verwachte_exitcodes: { type: 'array', items: { type: 'number' }, description: 'Geldige uitkomsten voor deze proef; standaard [0]' },
    }, ['programma']),
  },
  {
    name: 'maak_vaardigheid',
    description: 'Maak, beproef, bekijk of activeer een versieerbare eigen werkwijze. Volg stage → beproef → activeer. De proef krijgt een receipt dat exact aan naam, versie, instructiehash en testbestand is gebonden.',
    parameters: obj({
      bewerking: str('stage, beproef, activeer, deactiveer of lijst'),
      naam: str('Naam of slug van de vaardigheid'),
      beschrijving: str('Welk herhaalbaar probleem deze werkwijze oplost'),
      instructies: str('Concrete algemene werkwijze, geen vaste antwoordzinnen'),
      triggers: { type: 'array', items: { type: 'string' }, description: 'Onderwerpen waarbij de vaardigheid relevant is' },
      testbestand: str('Bestaand testprogramma in data/workspace dat de vaardigheid controleert'),
      testruntime: str('node of python3'),
      testargumenten: { type: 'array', items: { type: 'string' }, description: 'Optionele argumenten voor de gebonden proef' },
      receipt_id: num('Receipt-id uit beproef voor exact deze versie'),
    }, ['bewerking']),
  },
  {
    name: 'beheer_addon',
    description: 'Registreer, beproef, bekijk, activeer of quarantaineer een uitvoerbare add-on in Eva haar geïsoleerde werkruimte. Volg stage → beproef → activeer. De vaste sandbox heeft alleen workspace:read-write en network:none.',
    parameters: obj({
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
    }, ['bewerking']),
  },
  {
    name: 'gebruik_capability',
    description: 'Demonstreer een reeds actieve, receipt-gecontroleerde skill of add-on. Zonder naam wordt de laatst geactiveerde gekozen. Een skill wordt aantoonbaar geladen; een add-on wordt met haar vastgelegde entrypoint in de netwerkloze sandbox uitgevoerd.',
    parameters: obj({
      naam: str('Optionele naam of slug van een actieve capability'),
      argumenten: { type: 'array', items: { type: 'string' }, description: 'Alleen voor een actieve add-on; runtime-argumenten' },
    }),
  },
  {
    name: 'inspecteer_vermogen',
    description: 'Inspecteer provider-onafhankelijk welke kerntools, skills, add-ons, implementatiebronnen en geconfigureerde geheimnamen bij een gewenste handeling passen. Leest alleen metadata en toont nooit geheime waarden. Gebruik dit vóór een conclusie over wat Eva wel of niet kan.',
    parameters: obj({
      vraag: str('De concrete gewenste handeling of het vermogen dat onderzocht moet worden'),
    }, ['vraag']),
  },
  {
    name: 'beheer_werkcel',
    description: 'Verdeel één groter doel achter dezelfde Eva in 1-20 onafhankelijke onderzoekstromen, voer maximaal zes daarvan aantoonbaar parallel uit, bewaar bewijs en bronlinks per stroom en verbreed automatisch van primaire documentatie naar vakbronnen/forums wanneer de eerste route onvoldoende is. Gebruik start voor echt parallel werk, status/lijst om terug te lezen en hervat om onderbroken of mislukte stromen opnieuw op te pakken.',
    parameters: obj({
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
    }, ['bewerking']),
  },
  {
    name: 'beheer_toolpakket',
    description: 'Ontdek, stage, inspecteer en activeer een echt npm-pakket als toekomstige uitbreiding. Discovery leest officiële registryprovenance; installatie vereist een exacte versie, negeert install-scripts en voert pakketcode niet uit. Activatie kan alleen na een geslaagde netwerkloze healthcheck via voer_uit in dezelfde beurt, gebonden aan de ongewijzigde artifacthash. Een gestaged pakket is nog geen werkende Eva-tool.',
    parameters: obj({
      bewerking: str('ontdek, installeer, inspecteer, lijst, activeer, deactiveer of quarantaineer'),
      id: num('Installatierecord-id bij inspectie, activatie of statuswijziging'),
      pakket: str('Exacte npm-registrynaam, bijvoorbeeld lodash of @scope/package'),
      versie: str('Exacte semver bij installatie; bij ontdek mag ook latest'),
      artifact_sha256: str('Exacte artifacthash uit inspectie, vereist bij activeer'),
      bewijs: str('Korte beschrijving van de netwerkloze healthcheck of reden van quarantaine'),
    }, ['bewerking']),
  },
  {
    name: 'onthoud',
    description: 'Bewaar een concreet feit, voorkeur, afspraak of gebeurtenis over het gezin.',
    parameters: obj({
      inhoud: str('De concrete herinnering'),
      persoon: str('Optionele persoon'),
      categorie: str('feit, voorkeur, gebeurtenis, persoon of afspraak'),
      belang: num('Belang tussen 0 en 1'),
    }, ['inhoud']),
  },
  {
    name: 'zoek_geheugen',
    description: 'Zoek in Eva haar blijvende herinneringen.',
    parameters: obj({ vraag: str('Zoekvraag') }, ['vraag']),
  },
  {
    name: 'zoek_gesprek',
    description: 'Zoek echte eerder opgeslagen Telegram-berichten terug.',
    parameters: obj({ vraag: str('Zoekwoorden'), persoon: str('Optionele afzender'), limiet: num('Maximaal aantal resultaten') }),
  },
  {
    name: 'zet_herinnering',
    description: 'Plan een gezinsherinnering op een exact ISO-tijdstip in Europe/Amsterdam.',
    parameters: obj({
      wanneer: str('ISO-tijdstip'),
      tekst: str('Herinnering'),
      voor_wie: str('Optionele ontvanger'),
      herhaling: str('Optioneel: daily, weekly of monthly'),
    }, ['wanneer', 'tekst']),
  },
  {
    name: 'toon_herinneringen',
    description: 'Lees alle open herinneringen.',
    parameters: obj({}),
  },
  {
    name: 'annuleer_herinnering',
    description: 'Annuleer een open herinnering.',
    parameters: obj({ id: num('Herinnerings-id') }, ['id']),
  },
  {
    name: 'bekijk_camera',
    description: 'Bekijk en beschrijf een echte actuele camera. Gebruik dit wanneer beeld nodig is.',
    parameters: obj({ camera: str('Camera-naam, bijvoorbeeld deurbel, tuin of straat') }),
  },
  {
    name: 'leer_gezicht_van_foto',
    description: 'Koppel de naam aan de exact bewaarde Telegram-foto uit de actuele context en leer het gezicht alleen als het beeld bruikbaar is.',
    parameters: obj({ naam: str('Naam van de persoon') }, ['naam']),
  },
  {
    name: 'spreek_in_huis',
    description: 'Spreek een korte boodschap via exact één toegestane Voice PE (Assist satellite). Gebruik het optionele doel voor bijvoorbeeld woonkamer of overkapping; zonder doel geldt het geconfigureerde standaarddoel. Deze tool wijkt nooit uit naar een versterker of Chromecast.',
    parameters: obj({
      tekst: str('Wat Eva zegt'),
      doel: {
        type: 'string',
        enum: config.voiceAssistSatelliteTargets.flatMap((target) => [target.alias, target.entityId]),
        description: 'Optionele bekende Voice PE-alias of exact assist_satellite entity_id',
      },
    }, ['tekst']),
  },
  {
    name: 'stuur_bericht',
    description: 'Stuur een zelfstandig Telegram-bericht. Alleen voor een nieuw, gecontroleerd en nuttig resultaat; nooit voor voortgang of een herhaling van eerder advies. Schrijf als huisgenoot: conclusie eerst, hooguit twee korte zinnen en alleen meetwaarden die nodig zijn.',
    parameters: obj({ tekst: str('Het ene eindbericht'), aan: str('Optionele ontvanger') }, ['tekst']),
  },
  {
    name: 'doel_bijhouden',
    description: 'Maak, wijzig, pauzeer, hervat of rond een zelfgekozen langetermijndoel af. Dit kiest nooit het onderwerp; het bewaart Eva haar eigen reden, horizon en toetsbare succesmaat zodat aandachtcycli dezelfde richting kunnen voortzetten.',
    parameters: obj({
      bewerking: str('create, update, pause, resume, complete, abandon of list'),
      doel_sleutel: str('Bestaande unieke doelsleutel; niet bij create/list'),
      titel: str('Herkenbare naam van het doel'),
      intentie: str('Welke blijvende verandering Eva wil bereiken'),
      waarom: str('Waarom Eva dit zelf belangrijk vindt'),
      begunstigde: str('Voor wie dit waarde heeft'),
      horizon: str('Verwachte tijdshorizon of reviewperiode'),
      succesmaten: { type: 'array', items: { type: 'string' }, description: 'Toetsbare succesmaten' },
      prioriteit: num('Prioriteit 0-1'),
      volgende_review: str('Optioneel ISO-reviewmoment'),
    }, ['bewerking']),
  },
  {
    name: 'werk_bijhouden',
    description: 'Bewaar of werk één duurzame klus bij die echt meerdere sessies nodig heeft. Niet gebruiken voor een simpele vraag, losse gedachte of alleen een plan. Afronden vereist bewijs van een andere tool in dezelfde beurt.',
    parameters: obj({
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
      doel_sleutel: str('Optionele doelsleutel die Eva eerder zelf met doel_bijhouden heeft gekozen'),
    }, ['bewerking']),
  },
  {
    name: 'verkenning_bijhouden',
    description: 'Leg Eva haar eigen nieuwsgierigheid vast als toetsbare cyclus; de tool kiest nooit het onderwerp. Start vóór de tweede inhoudelijke actie. Werk daarna bewijs, wachttijd of eindbesluit bij. Het actiebudget en de stopregel begrenzen doelloze calls, niet de doelkeuze.',
    parameters: obj({
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
    }, ['bewerking']),
  },
  {
    name: 'toon_werk',
    description: 'Lees het ene duurzame werkboek met open, geblokkeerde en recent afgeronde klussen.',
    parameters: obj({}),
  },
  {
    name: 'signalen_afhandelen',
    description: 'Markeer alleen de genoemde autonome HA-signalen als inhoudelijk beoordeeld, nadat je ze hebt onderzocht, gebruikt of bewust niet relevant hebt bevonden.',
    parameters: obj({
      ids: {
        type: 'array',
        items: { type: 'string' },
        description: 'Exacte signaal-ids uit de huidige autonome aanleiding',
      },
      oordeel: str('Korte concrete reden waarom deze signalen zijn gebruikt of niet relevant zijn'),
    }, ['ids', 'oordeel']),
  },
];

function capabilityInventory(question = '', offeredToolNames = []) {
  const stop = new Set(['aan', 'als', 'dat', 'deze', 'dit', 'een', 'eigen', 'heeft', 'het', 'hoe', 'kan', 'kun', 'met', 'niet', 'of', 'ondersteunt', 'voor', 'wat', 'welke', 'zijn']);
  const terms = [...new Set(String(question || '').toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ').split(/\s+/)
    .filter((term) => term.length >= 3 && !stop.has(term)))];
  const score = (value) => terms.reduce((total, term) => (
    total + (String(value || '').toLowerCase().includes(term) ? 1 : 0)
  ), 0);
  const offered = new Set((offeredToolNames || []).map(String));
  const matchingTools = TOOLS
    .map((tool) => ({ tool, score: score(`${tool.name} ${tool.description}`) }))
    .filter(({ tool, score: value }) => tool.name !== 'inspecteer_vermogen' && value > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 8)
    .map(({ tool }) => ({
      name: tool.name,
      description: tool.description,
      offered_this_turn: offered.has(tool.name),
      parameters: Object.entries(tool.parameters?.properties || {}).map(([name, schema]) => ({
        name,
        type: schema?.type || null,
        description: schema?.description || '',
      })),
    }));
  const sourceRoots = ['src/integrations', 'src/mind'];
  const sourceRefs = sourceRoots.flatMap((relative) => {
    const directory = path.join(config.root, relative);
    if (!fs.existsSync(directory)) return [];
    return fs.readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && /\.[cm]?js$/i.test(entry.name))
      .map((entry) => ({
        path: `${relative}/${entry.name}`,
        score: score(entry.name.replace(/\.[^.]+$/, '')),
      }));
  }).filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 8)
    .map(({ path: sourcePath }) => sourcePath);
  const extensions = listCapabilities({ limit: 100 })
    .map((item) => ({ item, score: score(`${item.name} ${item.slug} ${item.description} ${(item.triggers || []).join(' ')}`) }))
    .filter(({ score: value }) => value > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 8)
    .map(({ item }) => ({
      kind: item.kind,
      name: item.name,
      status: item.status,
      version: item.version,
      updated_ts: item.updated_ts,
    }));
  const secretNames = listSecretKeys().filter((name) => score(name) > 0);
  return {
    ok: true,
    changed: false,
    verified: true,
    message: `Passende kerntools: ${matchingTools.map((tool) => `${tool.name}${tool.offered_this_turn ? ' (aangeboden)' : ' (niet aangeboden)'}`).join(', ') || 'geen'}. Relevante bronbestanden: ${sourceRefs.join(', ') || 'geen automatisch gevonden'}. Passende extensies: ${extensions.map((item) => `${item.name} (${item.status})`).join(', ') || 'geen'}. Geconfigureerde geheimnamen: ${secretNames.join(', ') || 'geen passende naam gevonden'}.`,
    question: String(question || '').trim(),
    source_refs: sourceRefs,
    configured_secret_names: secretNames,
    matching_tools: matchingTools,
    matching_extensions: extensions,
    note: 'Geheimwaarden zijn niet gelezen of getoond. Een descriptor bewijst beschikbaarheid; werking vereist zo nodig een read-only statusprobe of echte actie.',
  };
}

function compactWorkCell(cell) {
  if (!cell) return null;
  return {
    id: cell.id,
    title: cell.title,
    objective: cell.objective,
    status: cell.status,
    concurrency: cell.concurrency,
    created_ts: cell.created_ts,
    updated_ts: cell.updated_ts,
    started_ts: cell.started_ts,
    finished_ts: cell.finished_ts,
    last_error: cell.last_error,
    streams: (Array.isArray(cell.streams) ? cell.streams : []).map((stream) => ({
      id: stream.id,
      ordinal: stream.ordinal,
      title: stream.title,
      query: stream.query,
      status: stream.status,
      attempt_count: stream.attempt_count,
      research_phase: stream.research_phase,
      result: clean(stream.result_text || '', 2_400),
      sources: Array.isArray(stream.sources) ? stream.sources.slice(0, 10) : [],
      last_error: stream.last_error,
    })),
  };
}

function compactToolPackage(item) {
  if (!item) return null;
  return {
    id: item.id,
    package_name: item.package_name,
    package_version: item.package_version,
    slug: item.slug,
    status: item.status,
    registry_url: item.registry_url,
    target_path: item.target_path,
    artifact_sha256: item.artifact_sha256,
    manifest_sha256: item.manifest_sha256,
    lock_sha256: item.lock_sha256,
    integrity_ok: item.integrity_ok,
    files: item.files,
    bytes: item.bytes,
    metadata: item.metadata ? {
      description: item.metadata.description,
      license: item.metadata.license,
      repository: item.metadata.repository,
      deprecated: item.metadata.deprecated,
      bin: item.metadata.bin,
      install_scripts_ignored: item.metadata.install_scripts_ignored,
    } : null,
    provenance: item.provenance,
    health_receipt: item.health_receipt,
    last_error: item.last_error,
    created_ts: item.created_ts,
    updated_ts: item.updated_ts,
    installed_ts: item.installed_ts,
    activated_ts: item.activated_ts,
    events: Array.isArray(item.events) ? item.events.slice(-12) : [],
  };
}

export function toolsForTurn({ mode = 'chat' } = {}) {
  if (mode !== 'autonomy') return [...TOOLS];
  return TOOLS.filter((tool) => toolAvailableInAutonomy(tool.name, config.autonomyFull));
}

export const chatTools = () => toolsForTurn({ mode: 'chat' });
export const chatToolsForText = () => toolsForTurn({ mode: 'chat' });
export const voiceTools = () => toolsForTurn({ mode: 'chat' });
export const heartbeatTools = () => toolsForTurn({ mode: 'autonomy' });

const PROOF_STOP_WORDS = new Set([
  'actie', 'alleen', 'bestand', 'bron', 'controle', 'echt', 'gezin', 'goed',
  'informatie', 'klaar', 'maken', 'resultaat', 'stap', 'vandaag', 'werk',
]);

function proofWords(value) {
  return words(value)
    .filter((word) => word.length >= 3 && !PROOF_STOP_WORDS.has(word))
    .slice(0, 120);
}

const SIGNAL_ASSESSMENT_GENERIC = new Set([
  'actie', 'beide', 'bron', 'entity', 'geen', 'nodig', 'normaal', 'signaal',
  'signalen', 'situatie', 'state', 'waarde', 'waarden',
]);

function signalAssessmentMatches(assessment, signals) {
  const judged = words(assessment)
    .filter((word) => word.length >= 4 && !SIGNAL_ASSESSMENT_GENERIC.has(word));
  const observed = words((signals || []).map((signal) => (
    `${signal?.key || ''} ${signal?.text || ''}`
  )).join(' ')).filter((word) => word.length >= 4);
  return judged.some((left) => observed.some((right) => (
    left === right
    || left.includes(right)
    || right.includes(left)
    || (left.length >= 5 && right.length >= 5 && left.slice(0, 5) === right.slice(0, 5))
  )));
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
  if (!result?.ok || !result?.verified) return false;
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

async function executeRawTool(name, args = {}, context = {}) {
  const { mode = 'chat' } = context;
  switch (name) {
    case 'huis_status':
      return homeContextText({ fresh: true, query: args.vraag || '' });
    case 'bedien_huis':
      if (mode === 'autonomy' && !config.autonomyFull) {
        return JSON.stringify({ ok: false, reason: 'autonome huisbediening vereist de volledige-autonomiestand' });
      }
      if (!config.allowHomeControl) return JSON.stringify({ ok: false, reason: 'huisbediening is in de configuratie uitgeschakeld' });
      return JSON.stringify(await operateHome({
        target: args.doel,
        action: args.actie,
        value: args.waarde,
      }));
    case 'gebruik_robot': {
      if (mode === 'autonomy' && !config.autonomyFull) {
        return JSON.stringify({ ok: false, reason: 'autonoom robotgebruik vereist de volledige-autonomiestand' });
      }
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
      if (action === 'kijk') {
        const frame = await captureRobotFrame();
        const { describeImageBuffer } = await import('./vision.js');
        const vision = await describeImageBuffer(
          frame.buffer, frame.contentType,
          'Dit is de actuele frontcamera van robotstofzuiger Erik. Beschrijf wat direct voor hem zichtbaar is en wat onzeker blijft; geef geen routeadvies buiten het beeld.',
          { budgetClass: mode === 'autonomy' ? 'perception' : 'interactive', cacheKey: 'eva-erik-frontcamera' },
        );
        return JSON.stringify({
          ok: vision.ok,
          verified: vision.verified,
          changed: false,
          frame: { path: path.relative(config.root, frame.path), bytes: frame.bytes },
          description: vision.description,
          message: vision.description,
        });
      }
      if (action === 'verken') {
        const result = await exploreWithRobot({ direction: args.richting, durationMs: args.duur_ms });
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
        return JSON.stringify(await controlRobot(action));
      }
      return JSON.stringify({ ok: false, changed: false, reason: `onbekende robotactie: ${args.actie}` });
    }
    case 'ha_lees': {
      if (args.entity_id) {
        const entityId = String(args.entity_id).trim();
        const state = await getState(entityId);
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
    case 'wie_is_thuis':
      return args.persoon
        ? clean(JSON.stringify(await presenceDetails(args.persoon)))
        : clean(JSON.stringify(await whoIsHome()));
    case 'beheer_aanwezigheid': {
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
          ? listWorkItems({ statuses: ['open', 'blocked'], limit: 200 })
            .find((item) => Number(item.id) === workId && String(item.owner || '').trim())
          : null;
        if (mode === 'autonomy' && !delegatedWork) {
          return JSON.stringify({ ok: false, changed: false, verified: false, reason: 'de trackerbronnen van een gezinslid wijzigen vereist een expliciete opdracht in het gesprek' });
        }
        return JSON.stringify(await setPersonTrackers(args.persoon, args.device_trackers));
      }
      return JSON.stringify({ ok: false, changed: false, verified: false, reason: `onbekende aanwezigheidsactie: ${args.actie}` });
    }
    case 'zoek_internet': {
      const requestedTimeout = Number(context.timeoutMs);
      const timeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
        ? requestedTimeout
        : config.autonomousLlmTimeoutMs;
      const result = await searchWeb(args.vraag, {
        cacheKey: mode === 'autonomy' ? 'eva-autonomous-research' : 'eva-interactive-research',
        budgetClass: mode === 'autonomy' ? 'autonomous' : 'interactive',
        timeoutMs,
      });
      const message = commercialResearchEvidenceBoundary(args.vraag, result.text);
      return JSON.stringify({
        ok: true,
        verified: true,
        message: clean(message, 20_000),
        searched: result.searched,
        searchCalls: result.searchCalls,
        sources: result.sources,
      });
    }
    case 'beheer_agentmail': {
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
    case 'lees_bestand': {
      const file = resolveReadablePath(args.pad);
      rejectSyntheticArtifactLeak(file, context);
      const content = fs.readFileSync(file, 'utf8');
      const lines = content.split(/\r?\n/);
      if (String(args.zoek || '').trim()) {
        let pattern;
        try { pattern = new RegExp(String(args.zoek), 'i'); } catch { pattern = null; }
        const needle = normalize(args.zoek);
        const selected = new Set();
        lines.forEach((line, index) => {
          if ((pattern && pattern.test(line)) || (!pattern && normalize(line).includes(needle))) {
            for (let cursor = Math.max(0, index - 4); cursor <= Math.min(lines.length - 1, index + 4); cursor += 1) selected.add(cursor);
          }
        });
        if (!selected.size) return JSON.stringify({ ok: false, reason: `geen treffers voor ${args.zoek} in ${args.pad}` });
        return clean([...selected].sort((a, b) => a - b).map((index) => `${index + 1}: ${lines[index]}`).join('\n'));
      }
      if (args.vanaf_regel != null || args.aantal_regels != null) {
        const start = Math.max(0, Math.floor(Number(args.vanaf_regel) || 1) - 1);
        const count = Math.max(1, Math.min(400, Math.floor(Number(args.aantal_regels) || 200)));
        return clean(lines.slice(start, start + count).map((line, index) => `${start + index + 1}: ${line}`).join('\n'));
      }
      return clean(content);
    }
    case 'schrijf_bestand': {
      if (mode === 'autonomy' && !config.autonomyCodeExecution) {
        return JSON.stringify({ ok: false, reason: 'autonoom bestanden schrijven is standaard uitgeschakeld' });
      }
      const file = resolveWritablePath(args.pad);
      rejectSyntheticArtifactLeak(file, context);
      const content = String(args.inhoud ?? '');
      const previous = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : null;
      if (previous === content) {
        return JSON.stringify({
          ok: true,
          changed: false,
          path: path.relative(config.root, file),
          bytes: Buffer.byteLength(content),
          message: 'Bestand was al exact gelijk; er is niets herschreven.',
        });
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
      fs.renameSync(temp, checkedFile);
      fs.chmodSync(checkedFile, 0o600);
      const stat = fs.statSync(checkedFile);
      return JSON.stringify({ ok: true, changed: true, path: path.relative(config.root, checkedFile), bytes: stat.size });
    }
    case 'lijst_bestanden': {
      const directory = resolveReadablePath(args.pad || 'data/workspace');
      rejectSyntheticArtifactLeak(directory, context);
      const query = String(args.zoek || '').trim();
      let matcher = null;
      try { matcher = query ? new RegExp(query, 'i') : null; } catch { matcher = null; }
      const terms = query.toLowerCase().split(/[^\p{L}\p{N}_.-]+/u).filter(Boolean);
      let entries = fs.readdirSync(directory, { withFileTypes: true })
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
      if (query) entries = entries.filter((entry) => (
        (matcher && matcher.test(entry.name))
        || (!matcher && terms.every((term) => entry.name.toLowerCase().includes(term)))
      ));
      entries.sort(String(args.volgorde || '').toLowerCase() === 'nieuwste'
        ? (a, b) => Date.parse(b.modified_ts) - Date.parse(a.modified_ts)
        : (a, b) => a.name.localeCompare(b.name, 'nl'));
      entries = entries.slice(0, 200);
      return JSON.stringify(entries);
    }
    case 'voer_uit':
      if (mode === 'autonomy' && !config.autonomyCodeExecution) {
        return JSON.stringify({ ok: false, reason: 'autonome programma-uitvoering is standaard uitgeschakeld' });
      }
      rejectSyntheticArtifactLeak(args.werkmap || '', context);
      for (const argument of Array.isArray(args.argumenten) ? args.argumenten : []) {
        rejectSyntheticArtifactLeak(argument, context);
      }
      return runProgram(
        args.programma,
        args.argumenten || [],
        args.werkmap || '',
        args.verwachte_exitcodes,
      );
    case 'maak_vaardigheid': {
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
        changed: true,
        capability: operation === 'activeer'
          ? activateCapability('skill', args.naam, args.receipt_id)
          : setCapabilityStatus('skill', args.naam, 'disabled'),
      });
    }
    case 'beheer_addon': {
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
        changed: true,
        capability: operation === 'activeer'
          ? activateCapability('addon', args.naam, args.receipt_id)
          : setCapabilityStatus('addon', args.naam, statuses[operation]),
      });
    }
    case 'gebruik_capability': {
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
        [0],
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
    case 'inspecteer_vermogen':
      return JSON.stringify(capabilityInventory(args.vraag, context.offeredToolNames || []));
    case 'beheer_werkcel': {
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
    case 'beheer_toolpakket': {
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
    case 'onthoud': {
      const id = addMemory({
        content: args.inhoud,
        person: args.persoon || null,
        category: args.categorie || 'feit',
        importance: Number.isFinite(Number(args.belang)) ? Number(args.belang) : 0.6,
      });
      return JSON.stringify({
        ok: Number.isFinite(Number(id)),
        id: Number.isFinite(Number(id)) ? Number(id) : null,
        message: id ? 'concrete herinnering opgeslagen' : 'de herinnering was leeg, gevoelig of niet bruikbaar en is niet opgeslagen',
      });
    }
    case 'zoek_geheugen': {
      const rows = searchMemories(args.vraag, 30).filter((row) => {
        if (mode === 'autonomy') return true;
        const owner = String(row.person || '').trim().toLowerCase();
        return !owner || owner === String(context.sender || '').trim().toLowerCase();
      }).slice(0, 12);
      return clean(JSON.stringify(rows));
    }
    case 'zoek_gesprek':
      if (mode !== 'autonomy' && !context.messageContext?.chatId
        && context.messageContext?.identityTrusted !== true) {
        return JSON.stringify({ ok: false, reason: 'gesprekken zoeken is alleen binnen het huidige Telegram-gesprek beschikbaar' });
      }
      {
        const question = String(args.vraag || '').trim();
        // Bij een chronologische vraag zijn de recente echte berichten zelf de
        // bron; trefwoordranking op woorden als "te vroeg" zou alleen berichten
        // vinden waarin de huisgenoot die diagnose letterlijk al uitsprak.
        const chronological = /\b(?:nieuwste|laatste|recent(?:e|ste)?|afgelopen)\b/i.test(question);
        const messages = searchTelegramMessages(chronological ? '' : question, {
        chatId: context.messageContext?.chatId || null,
        sender: args.persoon || null,
        limit: args.limiet || 12,
        });
        return clean(JSON.stringify({
          ok: true,
          changed: false,
          verified: true,
          count: messages.length,
          messages,
        }));
      }
    case 'zet_herinnering': {
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
    case 'toon_herinneringen':
      return clean(JSON.stringify(openReminders().filter((reminder) => (
        mode === 'autonomy'
          || !reminder.target
          || String(reminder.target).toLowerCase() === String(context.sender || '').toLowerCase()
      ))));
    case 'annuleer_herinnering': {
      const id = Number(args.id);
      const existed = openReminders().some((reminder) => Number(reminder.id) === id);
      if (existed) cancelReminder(id);
      return JSON.stringify({ ok: existed, id });
    }
    case 'bekijk_camera': {
      const { lookAndDescribe } = await import('./vision.js');
      return clean(JSON.stringify(await lookAndDescribe(args.camera || null, {
        budgetClass: mode === 'autonomy' ? 'perception' : 'interactive',
      })));
    }
    case 'leer_gezicht_van_foto': {
      const { leerGezichtVanFoto, nameLastSeen } = await import('./vision.js');
      const photoRef = String(context.replyEvidence?.photoRef || '').trim();
      const result = photoRef
        ? await nameLastSeen(args.naam, {
            ref: photoRef,
            contextText: context.userText || '',
          })
        : await leerGezichtVanFoto(args.naam);
      return clean(JSON.stringify(result));
    }
    case 'spreek_in_huis':
      if (mode === 'autonomy' && !config.autonomyFull) {
        return JSON.stringify({ ok: false, reason: 'autonoom spreken in huis vereist de volledige-autonomiestand' });
      }
      return clean(JSON.stringify(await announceOnVoiceAssistSatellite(args.tekst, args.doel || null)));
    case 'stuur_bericht': {
      if (mode !== 'autonomy') {
        return clean(JSON.stringify(await sendRegisteredMessage(args.tekst, args.aan || null, {
          dedupeKey: context.turnId ? `tool-message:${context.turnId}` : null,
        })));
      }
      if (/^\s*eva\s*$/i.test(String(args.aan || ''))) {
        return JSON.stringify({
          ok: false,
          needs_followup: false,
          reason: 'een bericht aan Eva zelf is geen externe aflevering en is daarom niet toegestaan',
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
        const openWork = listWorkItems({ statuses: ['open', 'blocked'], limit: 40 });
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
      const key = normalize(args.tekst).slice(0, 180);
      const preparedKey = clean(context.preparedDeliveryDedupeKey || '', 240).trim();
      const transportDedupeKey = preparedKey
        ? `prepared:${preparedKey}`
        : `autonomy:${key}`;
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
        && autonomousMessagesOverlap(args.tekst, row.content)
        && !autonomousMessageHasNewResult(args.tekst, row.content)
      ));
      if (repeated || repeatedTopic) {
        return JSON.stringify({ ok: false, reason: 'dit onderwerp is de afgelopen zeven dagen al zelfstandig gemeld' });
      }
      const sent = await sendRegisteredMessage(args.tekst, args.aan || null, {
        dedupeKey: transportDedupeKey,
        broadcast: !args.aan,
      });
      if (sent?.ok) {
        const chats = new Set((sent.sent || []).map((item) => String(item.chatId || '')).filter(Boolean));
        for (const chatId of chats) {
          addMessage('telegram', 'Eva', 'assistant', args.tekst, `telegram:${chatId}`);
        }
        logTimeline('chat_out', `Eva: ${args.tekst}`, {
          sender: 'Eva',
          autonomous: true,
          familyVisible: true,
          deliveredChatIds: [...chats],
        });
        const episode = goalEpisodeById(context.activeGoalEpisodeId);
        if (episode?.delivery_policy === 'required') {
          recordEpisodeDelivery(episode.id, {
            deliveredChannel: 'telegram',
            deliveryTarget: args.aan || episode.delivery_target,
            resultText: args.tekst,
            proofSummary: episode.proof_summary,
            idempotencyKey: transportDedupeKey,
            transport: { sent: sent.sent || [] },
          });
        }
      }
      return clean(JSON.stringify({
        ...sent,
        message: sent?.ok ? clean(args.tekst, 1_200) : null,
      }));
    }
    case 'werk_bijhouden': {
      const operation = normalize(args.bewerking);
      if (!['start', 'update', 'blokkeer', 'afronden', 'annuleren'].includes(operation)) {
        return JSON.stringify({ ok: false, changed: false, reason: `onbekende werkbewerking: ${operation || '(leeg)'}` });
      }
      if (operation === 'start') {
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
        let { workItem: item } = createDurableWork({
          source: mode === 'autonomy' ? 'autonomy' : 'user',
          owner: mode === 'autonomy' ? null : context.sender || null,
          title: args.titel,
          goal: args.doel,
          nextStep: args.volgende_stap || '',
          definitionOfDone: args.definitie_klaar || args.doel,
          priority: args.prioriteit,
        }, {
          originKind: mode === 'autonomy' ? 'self' : 'user',
          originChannel: mode === 'autonomy' ? 'autonomy' : 'chat',
          requester: mode === 'autonomy' ? null : context.sender || null,
        });
        if (args.fase != null || args.toestand_json != null || args.hervatten_na != null || args.doel_sleutel != null) {
          const state = args.toestand_json ? JSON.parse(args.toestand_json) : {};
          if (args.doel_sleutel) state.goal_key = String(args.doel_sleutel);
          item = updateDurableWork(item.id, {
            currentStep: args.fase,
            state,
            nextWakeTs: args.hervatten_na,
            wait: args.hervatten_na ? { reason: 'scheduled_resume' } : null,
          });
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
      if (mode === 'autonomy' && Number.isFinite(Number(context.activeWorkId))
        && Number(context.activeWorkId) !== id) {
        return JSON.stringify({
          ok: false,
          changed: false,
          reason: `deze beurt is gebonden aan werk #${context.activeWorkId}, niet aan werk #${id}`,
        });
      }
      const current = listWorkItems({
        statuses: ['open', 'blocked', 'done'],
        limit: 200,
      }).find((item) => Number(item.id) === id);
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
      const reviewWake = selfReviewInstead
        ? (Number.isFinite(Date.parse(args.hervatten_na || ''))
          ? new Date(args.hervatten_na).toISOString()
          : new Date(Date.now() + 6 * 60 * 60_000).toISOString())
        : null;
      if (selfReviewInstead) status = 'open';
      const waitsForRequiredDelivery = status === 'done'
        && boundEpisode?.delivery_policy === 'required'
        && !episodeHasDelivery(boundEpisode.id);
      if (waitsForRequiredDelivery) status = 'open';
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
        .filter((result) => !['werk_bijhouden', 'toon_werk'].includes(result?.name)
          && evidenceMatches(workSubject, result))
        .sort((left, right) => (
          toolProofRank(right.name) - toolProofRank(left.name)
        ))[0];
      let storedState = null;
      try { storedState = JSON.parse(String(current?.state_json || '{}')); } catch { storedState = {}; }
      const workChange = {
          nextStep: waitsForRequiredDelivery
            ? 'Bezorg het gecontroleerde resultaat aan de oorspronkelijke verzoeker.'
            : args.volgende_stap,
          currentStep: selfReviewInstead
            ? 'Wacht op het geplande herbeoordelingsmoment en probeer dan zelf een andere bewijsroute.'
            : args.fase,
          state: args.toestand_json
            ? JSON.parse(args.toestand_json)
            : externalDependency
              ? { ...(storedState || {}), blocked_dependency: externalDependency }
              : null,
          nextWakeTs: waitsForRequiredDelivery ? '' : reviewWake || args.hervatten_na,
          wait: waitsForRequiredDelivery
            ? {}
            : selfReviewInstead
              ? { reason: 'self_review', resumeAfter: reviewWake }
              : null,
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
        const delivered = episodeHasDelivery(boundEpisode.id);
        const needsDelivery = waitsForRequiredDelivery || episodeNeedsDelivery(id);
        const nextEpisodeState = operation === 'annuleren'
          ? 'abandoned'
          : selfReviewInstead
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
          checkpoint: proof ? resultEvidence(proof) : current?.evidence || '',
          proofSummary: proof ? resultEvidence(proof) : current?.evidence || '',
          nextAction: needsDelivery
            ? deliveryAction
            : selfReviewInstead
              ? current?.next_step || 'Herbeoordeel met een andere bewijsroute.'
              : args.volgende_stap,
          wakeTs: selfReviewInstead ? reviewWake : args.hervatten_na,
          sessionRef: context.turnId || null,
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
    case 'doel_bijhouden':
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
        source: 'self',
      }));
    case 'verkenning_bijhouden': {
      const operation = normalize(args.bewerking);
      if (operation === 'lijst') return JSON.stringify({ ok: true, changed: false, explorations: listExplorations({ limit: 30 }) });
      if (operation === 'start') return JSON.stringify(startExploration({
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
          result?.ok && result?.verified && evidenceMatches(args.nieuw_bewijs || '', result)
        )),
        topicKey: args.onderwerp_sleutel,
        topicAnchors: args.onderwerp_ankers,
      }));
      const id = Number(args.id);
      if (!Number.isFinite(id)) return JSON.stringify({ ok: false, changed: false, reason: 'verkenning-id ontbreekt' });
      const statuses = {
        update: 'running', wachten: 'waiting', afronden: 'completed',
        stoppen: 'abandoned', stop: 'abandoned',
      };
      const status = statuses[operation];
      if (!status) return JSON.stringify({ ok: false, changed: false, reason: `onbekende verkenningsbewerking: ${operation}` });
      return JSON.stringify(updateExploration(id, {
        status,
        evidence: args.bewijs,
        result: args.resultaat,
        decision: args.besluit,
        nextWakeTs: args.hervatten_na,
        expectedRevision: args.verwachte_revisie,
      }));
    }
    case 'toon_werk':
      return JSON.stringify({
        ok: true,
        items: listWorkItems({ statuses: ['open', 'blocked', 'done'], limit: 40 }),
      });
    case 'signalen_afhandelen': {
      if (mode !== 'autonomy') {
        return JSON.stringify({ ok: false, reason: 'dit gereedschap hoort alleen bij autonome aandacht' });
      }
      const ids = [...new Set((Array.isArray(args.ids) ? args.ids : [])
        .map((id) => String(id || '').trim())
        .filter(Boolean))].slice(0, 30);
      const assessment = clean(args.oordeel || '', 600).trim();
      const assessed = assessment.length >= 8 && assessment.split(/\s+/).filter(Boolean).length >= 2;
      const available = new Map((Array.isArray(context.messageContext?.pendingSignals)
        ? context.messageContext.pendingSignals
        : []).map((signal) => [String(signal?.id || ''), signal]));
      const referenced = ids.map((id) => available.get(id)).filter(Boolean);
      const known = ids.length > 0 && referenced.length === ids.length;
      const grounded = known && signalAssessmentMatches(assessment, referenced);
      return JSON.stringify({
        ok: ids.length > 0 && assessed && known && grounded,
        changed: false,
        ids,
        assessment,
        reason: ids.length === 0
          ? 'geen geldige signaal-ids genoemd'
          : !assessed
            ? 'een concreet inhoudelijk oordeel ontbreekt'
            : !known
              ? 'minstens een signaal-id hoort niet bij deze autonome aanleiding'
              : grounded ? null : 'het oordeel gaat niet aantoonbaar over de genoemde signalen',
      });
    }
    default:
      return JSON.stringify({ ok: false, reason: `onbekend gereedschap: ${name}` });
  }
}

function parsedToolOutput(raw) {
  try {
    return JSON.parse(String(raw));
  } catch {
    return null;
  }
}

function compactToolData(parsed, raw, serializedData) {
  if (serializedData.length <= 14_000) return parsed ?? String(raw);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { truncated: true, text: clean(raw, 12_000) };
  }

  const text = parsed.text || parsed.message || parsed.info || parsed.stdout || '';
  return {
    truncated: true,
    ...(text ? { text: clean(text, 12_000) } : {}),
    ...(parsed.stdout ? { stdout: clean(parsed.stdout, 4_000) } : {}),
    ...(typeof parsed.verified === 'boolean' ? { verified: parsed.verified } : {}),
    ...(typeof parsed.demonstrated === 'boolean' ? { demonstrated: parsed.demonstrated } : {}),
    ...(parsed.receipt_id ? { receipt_id: parsed.receipt_id } : {}),
    ...(parsed.content_hash ? { content_hash: clean(parsed.content_hash, 128) } : {}),
    ...(Array.isArray(parsed.sandbox) ? { sandbox: parsed.sandbox.slice(0, 12) } : {}),
    ...(parsed.capability ? {
      capability: {
        id: parsed.capability.id,
        kind: parsed.capability.kind,
        slug: parsed.capability.slug,
        name: parsed.capability.name,
        version: parsed.capability.version,
        status: parsed.capability.status,
      },
    } : {}),
    ...(parsed.path ? { path: clean(parsed.path, 1_000) } : {}),
    ...(typeof parsed.count === 'number' ? { count: parsed.count } : {}),
    ...(typeof parsed.searched === 'boolean' ? { searched: parsed.searched } : {}),
    ...(typeof parsed.searchCalls === 'number' ? { searchCalls: parsed.searchCalls } : {}),
    ...(Array.isArray(parsed.sources) ? { sources: parsed.sources.slice(0, 20) } : {}),
  };
}

function verifiedToolResult(name, parsed, ok) {
  if (!ok) return false;
  if (typeof parsed?.verified === 'boolean') return parsed.verified;
  const verification = toolVerification(name);
  if (verification === 'explicit') return false;
  if (verification === 'ok') return parsed?.ok === true;
  // Leestools bewijzen dat de bron is geraadpleegd, niet dat de wereld veranderde.
  return true;
}

function classifyToolFailure(value) {
  const text = String(value || '').toLowerCase();
  if (/time.?out|tijdslimiet|temporar|tijdelijk|429|rate.?limit/.test(text)) {
    return { failure_class: 'transient', retryable: true, effect_uncertain: false };
  }
  if (/permission|bevoegd|unauthori|forbidden|authentic|api.?key|geen toegang/.test(text)) {
    return { failure_class: 'permission', retryable: false, effect_uncertain: false };
  }
  if (/not found|niet gevonden|bestaat niet|404/.test(text)) {
    return { failure_class: 'not_found', retryable: false, effect_uncertain: false };
  }
  if (/invalid|ongeldig|validatie|400/.test(text)) {
    return { failure_class: 'invalid_input', retryable: false, effect_uncertain: false };
  }
  if (/conflict|bestaat al|already exists|409/.test(text)) {
    return { failure_class: 'conflict', retryable: false, effect_uncertain: false };
  }
  if (/unavailable|onbereikbaar|connection refused|econnrefused|niet beschikbaar/.test(text)) {
    return { failure_class: 'unavailable', retryable: true, effect_uncertain: false };
  }
  if (/policy|beleid|niet toegestaan|geblokkeerd/.test(text)) {
    return { failure_class: 'policy', retryable: false, effect_uncertain: false };
  }
  return { failure_class: 'unknown', retryable: false, effect_uncertain: false };
}

export async function executeTool(name, args = {}, context = {}) {
  if (!toolAllowedByPolicy(name, args, context.effectPolicy)) {
    const effect = toolEffect(name, args);
    const reason = `gereedschap ${name} met effect ${effect} is niet toegestaan binnen de uitvoeringsgrens van deze beurt`;
    return JSON.stringify({
      contract: 'ActionResult/v1',
      tool: name,
      ok: false,
      changed: false,
      verified: false,
      evidence: null,
      needs_followup: false,
      partial: false,
      failure_class: 'policy',
      retryable: false,
      effect_uncertain: false,
      risk: 'normal',
      human_message: reason,
      error: reason,
    });
  }
  try {
    const raw = await executeRawTool(name, args, context);
    const parsed = parsedToolOutput(raw);
    const ok = parsed?.ok === false
      ? false
      : !/^Fout bij /i.test(String(raw));
    const changed = typeof parsed?.changed === 'boolean'
      ? parsed.changed
      : toolChangesByDefault(name) && ok;
    const verified = verifiedToolResult(name, parsed, ok);
    const reason = clean(parsed?.reason || parsed?.error || '', 1200);
    const summary = clean(parsed?.message || parsed?.info || reason || raw, 1600);
    const serializedData = parsed == null ? String(raw) : JSON.stringify(parsed);
    const data = compactToolData(parsed, raw, serializedData);
    const failure = ok ? null : classifyToolFailure(reason || raw);
    return JSON.stringify({
      contract: 'ActionResult/v1',
      tool: name,
      ok,
      changed,
      verified,
      evidence: {
        source: name,
        summary,
      },
      needs_followup: typeof parsed?.needs_followup === 'boolean'
        ? parsed.needs_followup
        : !verified,
      partial: parsed?.partial === true,
      ...(parsed?.waiting_for ? { waiting_for: clean(parsed.waiting_for, 80) } : {}),
      ...(failure || {}),
      risk: context.mode === 'autonomy' && toolChangesByDefault(name) ? 'autonomous' : 'normal',
      human_message: clean(parsed?.message || parsed?.info || reason, 500),
      ...(!ok && reason ? { error: reason } : {}),
      data,
    });
  } catch (error) {
    const failure = classifyToolFailure(error?.message || error);
    return JSON.stringify({
      contract: 'ActionResult/v1',
      tool: name,
      ok: false,
      changed: false,
      verified: false,
      evidence: null,
      needs_followup: true,
      risk: 'normal',
      human_message: '',
      error: clean(error?.message || error, 1200),
      ...failure,
    });
  }
}

export function makeExecutor({ mode = 'chat', asker = null, ...context } = {}) {
  return (name, args) => executeTool(name, args, {
    ...context,
    mode,
    sender: context.sender || asker || null,
  });
}

export const safeExec = (fn) => async (...args) => {
  try {
    return await fn(...args);
  } catch (error) {
    return JSON.stringify({ ok: false, error: clean(error?.message || error, 800) });
  }
};

export const __toolsTest = {
  autonomousConditionKeys,
  autonomousMessageHasNewResult,
  autonomousMessageIsInternalProgress,
  autonomousMessagesOverlap,
  autonomousTopicKeys,
  commercialResearchEvidenceBoundary,
  compactToolData,
  classifyToolFailure,
  emptyAutonomousWebFinding,
  homeOperationOutcome,
  entityScore,
  resolveHomeEntity,
  normalizeExpectedExitCodes,
  normalizeHaReadPath,
  proofToolFitsSubject,
};

export { listCameras };
