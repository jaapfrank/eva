import { performance } from 'node:perf_hooks';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { bus } from '../bus.js';
import {
  addMessage,
  allMemories,
  claimTurnToolExecution,
  completeTurnToolExecution,
  getTimeline,
  hasDeliveredTurn,
  memoriesForPerson,
  bumpRecall,
  logTimeline,
  newerTelegramUserMessage,
  recentConversation,
  recentMessages,
  recentOtherConversationsForPerson,
  recentTelegramMessages,
  searchMemories,
  telegramMessage,
  upsertPerson,
  db,
} from '../db.js';
import { config } from '../config.js';
import { imageMsg, msg, runAgent } from '../brain/llm.js';
import {
  moodActionGuidance,
  applyInteractionFeedback,
} from './emotions.js';
import { buildInstructions, renderHouseActionResult } from './persona.js';
import { executeTool, homeContextText, toolsForTurn } from './tools.js';
import {
  toolAllowedByPolicy,
  toolEffect,
  toolHasTag,
  toolIsDurable,
  toolRequiresEffectReport,
  toolSyntheticAllowed,
} from './toolpolicy.js';
import {
  arbitrateTurnPlan,
  effectBoundary,
  identityNameCandidate,
  resolveVoiceDirective,
} from './turnplan.js';
import { createWorkItem, durableWorkStatus, workContextText } from './work.js';
import { householdContextText, recordRelationshipFeedback } from './household.js';
import { relationshipLessonFromText, selfContinuityContextText } from './social.js';
import {
  autonomousMessageNeedsAccountability,
  composeAutonomousMessage,
  factualDeliveryFallback,
} from './communication.js';
import { capabilityContextText } from './capabilities.js';
import {
  beginCognitiveEpisode,
  closeCognitiveEpisode,
  cognitiveContextText,
  recordCognitiveAction,
  recordCognitiveOutcome,
} from './cognitive.js';
import {
  attentionContextText, explorationById, noteExplorationAction, relevantExplorationContextText,
} from './curiosity.js';
import {
  attachEpisodeOrigin,
  bindEpisodeToWork,
  createGoalEpisode,
  ensureEpisodeForWorkItem,
  adoptUnboundWorkEpisodes,
  episodeForWork,
  episodeHasDelivery,
  goalEpisodeById,
  openEpisodeForConversation,
  portfolioContextText,
  recordEpisodeDelivery,
  recoverDeliveredEpisodeReceipts,
  recoverStrandedDeliveryWork,
  selectPortfolioCommitment,
  transitionGoalEpisode,
  updateDurableWork,
  updateGoalEpisode,
} from './portfolio.js';
import { errMsg, tekstOverlap } from '../util.js';

const queues = new Map();
const deliveredTurns = new Set();
const AUTONOMY_SILENT = '__eva_autonomy_silent__';
const HIGH_IMPACT_HOUSE_EFFECT = /(?:slot|lock|alarm|beveilig|deur|poort|garage|cover|rolluik|gordijn|verwarming|thermostaat|climate|boiler|water|gas)/i;
function autonomyEffectRequiresReport(toolOrName, args = null) {
  const name = typeof toolOrName === 'string' ? toolOrName : toolOrName?.name;
  const toolArgs = args || (typeof toolOrName === 'object' ? toolOrName?.args : null) || {};
  // Een normale, omkeerbare huisroutine is bewezen werk maar geen verplichte
  // Telegram-aflevering. Alleen effecten met duidelijke accountability worden
  // hier hard meldplichtig; Eva blijft vrij om iets werkelijk relevants zelf te
  // delen via haar afzonderlijke communicatie-oordeel.
  if (name === 'bedien_huis') {
    return HIGH_IMPACT_HOUSE_EFFECT.test(`${toolArgs.doel || ''} ${toolArgs.actie || ''}`);
  }
  if (!toolRequiresEffectReport(name)) return false;
  // Een eigen wekker is alleen Eva's interne planning. Die is geen resultaat
  // voor het gezin en mag een episode dus niet voortijdig afleverplichtig maken.
  if (name === 'zet_herinnering') {
    const target = String(toolArgs.voor_wie || 'Eva').trim().toLowerCase();
    if (!target || target === 'eva') return false;
  }
  return true;
}
function syntheticReplyNeedsPersistentState(text = '') {
  const value = String(text || '');
  return /\b(?:morgen(?:ochtend|middag|avond)?|vanavond|later|om\s+\d{1,2}(?::\d{2})?|volgende\s+(?:keer|week|dag))\b/i.test(value)
    && (/\b(?:ik|we)\b.{0,80}\b(?:ga|gaan|pak|pakken|doe|doen|hervat|vervolg|kijk|zoek|controleer|pas)\b/i.test(value)
      || /\b(?:pak|pakken|doe|doen|hervat|vervolg|kijk|zoek|controleer|pas)\b.{0,40}\b(?:ik|we)\b/i.test(value));
}
function stripSyntheticFutureClaims(text = '') {
  const sentences = String(text || '').match(/[^.!?]+[.!?]?/g) || [];
  return sentences
    .map((sentence) => sentence.trim())
    .filter(Boolean)
    .filter((sentence) => !syntheticReplyNeedsPersistentState(sentence))
    .join(' ')
    .trim();
}
function shouldBindDurableUserEpisode({ projectWork = false, explicitTool = false, buildWork = false, requiredToolCount = 0, longWork = false } = {}) {
  return projectWork || (explicitTool && (buildWork || requiredToolCount > 1 || longWork));
}

// Oud duurzaam werk en het nieuwe doelportfolio vormen vanaf nu één boog. Dit
// is een eenmalige, idempotente adoptie; open beloften verdwijnen niet omdat ze
// vóór de portfolio-migratie zijn ontstaan.
adoptUnboundWorkEpisodes();
recoverDeliveredEpisodeReceipts();
recoverStrandedDeliveryWork();
for (const row of db.prepare(`
  SELECT DISTINCT json_extract(meta, '$.conversationId') AS conversation_id
  FROM timeline
  WHERE (kind = 'chat_in' AND json_extract(meta, '$.messageContext.syntheticTest') = 1)
     OR (kind = 'chat_out' AND json_extract(meta, '$.syntheticTest') = 1)
`).all()) {
  if (row.conversation_id) {
    // De append-only timeline bewaart de evaluatie-audit. De gewone
    // gesprekscontext mag haar niet als gezinsherinnering of autonome aanleiding lezen.
    db.prepare('DELETE FROM messages WHERE conversation_id = ?').run(row.conversation_id);
  }
}
const AUTONOMY_DISCOVERY_LIMIT = 4;
const runtime = {
  active: 0,
  activity: 'luistert',
  current: null,
  lastTurn: null,
  lastInteractiveTurn: null,
  lastAutonomyTurn: null,
  interactivePending: 0,
  interactionEpoch: 0,
};

function queueFor(key, work) {
  const previous = queues.get(key) || Promise.resolve();
  const current = previous.catch(() => {}).then(work);
  queues.set(key, current);
  current.finally(() => {
    if (queues.get(key) === current) queues.delete(key);
  }).catch(() => {});
  return current;
}

function cleanUserText(value) {
  const text = String(value || '').trim();
  const lines = text.split('\n');
  if (lines.length < 2) return text;
  const lastSpeakerLine = [...lines].reverse().find((line) => /^[^:\n]{1,50}:\s+\S/.test(line));
  return lastSpeakerLine ? lastSpeakerLine.replace(/^[^:\n]{1,50}:\s+/, '').trim() : text;
}

function commandText(value) {
  return cleanUserText(value)
    .replace(/^\s*(?:(?:kun|kan|wil|zou)\s+(?:je|jij)|wil\s+je)\s+(?:(?:even|eens|alsjeblieft)\s+)*(?:gaan\s+)?/i, '')
    .trim();
}

function hasHomeControlTarget(value = '') {
  return /\b(?:licht|lamp|verlichting|schakelaar|stekker|ventilator|verwarming|heater|rolluik|gordijn|raam|deur|tv|televisie|media|speaker|sonos|stofzuiger|kantoor|woonkamer|keuken|badkamer|slaapkamer|tuin|overkapping|gang|hal|wc)\b/i.test(value)
    || /\b(?:light|switch|fan|climate|cover|media_player|vacuum)\.[a-z0-9_]+\b/i.test(value);
}

function requestsRobotAction(value = '') {
  const raw = cleanUserText(value).toLowerCase();
  const text = commandText(value).toLowerCase();
  const robot = String.raw`(?:eriks?|robotstofzuiger|stofzuigerrobot|robot|vacuum\.[a-z0-9_]+)`;
  if (!new RegExp(`\\b${robot}\\b`, 'i').test(text)) return false;

  // A robot mentioned while somebody describes a scene is still just the
  // subject of that scene. Require directive or information-request grammar;
  // a loose action stem made Dutch statements such as "Erik rijdt/draait ..."
  // look like imperatives and prevented the conversation from reaching Eva.
  const commandVerb = String.raw`(?:stuur|start|stop|pauzeer|dock|rijd|draai|verken|toon|bekijk|controleer)`;
  const actionInfinitive = String.raw`(?:starten|stoppen|pauzeren|docken|terugsturen|teruggaan|rijden|rondrijden|draaien|verkennen)`;
  const negatedCommand = new RegExp(
    String.raw`^\s*${commandVerb}\w*\b.{0,100}\b${robot}\b.{0,24}\bniet\b|^\s*laat\b.{0,100}\b${robot}\b.{0,24}\b(?:niet|met\s+rust)\b`,
    'i',
  ).test(text);
  if (negatedCommand) return false;

  const actionLed = new RegExp(
    String.raw`^\s*${commandVerb}\w*\b.{0,100}\b${robot}\b|^\s*maak\b.{0,100}\b(?:schoon|stofvrij)\b.{0,60}\b(?:met|door)\b.{0,30}\b${robot}\b`,
    'i',
  ).test(text);
  const letCommand = new RegExp(
    String.raw`^\s*laat\b.{0,40}\b${robot}\b.{0,50}\b${actionInfinitive}\b`,
    'i',
  ).test(text);
  const vocativeCommand = new RegExp(
    String.raw`^\s*${robot}\s*,\s*(?:terug|naar|ga|kom|start|stop|pauzeer|dock|rijd|draai|verken)\w*\b`,
    'i',
  ).test(text);
  const shorthandCommand = new RegExp(
    String.raw`^\s*${robot}\b\s+(?:even\s+)?${actionInfinitive}\b`,
    'i',
  ).test(text);
  if (actionLed || letCommand || vocativeCommand || shorthandCommand) return true;

  const explicitWish = new RegExp(
    String.raw`\b(?:ik\s+wil\s+dat|zorg\s+dat)\b.{0,100}\b${robot}\b.{0,80}\b(?:start|stop|pauze|dock|terug|rijd|draai|verken)\w*\b|\b${robot}\b.{0,30}\bmoet\b.{0,40}\b(?:start|stop|pauze|dock|terug|rijd|draai|verken)\w*\b`,
    'i',
  ).test(raw);
  if (explicitWish) return true;

  return new RegExp(
    String.raw`\bwaar\s+is\b.{0,60}\b${robot}\b|\b(?:wat\s+is\s+de\s+)?status\b.{0,60}\b${robot}\b|\b${robot}\b.{0,60}\b(?:status|kaart)\b|\b(?:toon|bekijk|open|controleer)\w*\b.{0,80}\b(?:camera|frontcamera|kaart)\b.{0,80}\b${robot}\b|\bwat\s+(?:ziet|filmt|doet)\b.{0,60}\b${robot}\b|^\s*(?:rijdt|draait|werkt)\b.{0,30}\b${robot}\b.{0,20}\?*$`,
    'i',
  ).test(text);
}

// Route on the requested outcome, not on one memorised sentence. These helpers
// deliberately combine independent concepts so paraphrases still land in the
// same mission without dictating Eva's wording or solution.
function requestsSelfImprovement(value = '') {
  const text = commandText(value).toLowerCase();
  const change = /\b(?:verbeter\w*|verrijk\w*|breid\w*\s+uit|wijzig\w*|pas\w*\s+aan|zelfverbeter\w*)\b/i.test(text);
  const self = /\b(?:jezelf|jouw?\s+eigen|je\s+eigen|eigen\s+(?:systeem|werking|gedrag|communicatie|vaardigheid)|zelfverbetering|recente\s+(?:echte\s+)?chatbeurten|hoe\s+je\s+(?:communiceert|doorpakt))\b/i.test(text);
  return change && self;
}

function requestsCapabilityDemonstration(value = '') {
  const text = commandText(value).toLowerCase();
  const capability = /\b(?:auto\s*bots?|autobots?|zelfverbeter\w*|add-?ons?|skills?|plugins?|uitbreidingsmogelijk\w*|uitbreidingsmechanism\w*|jezelf\s+uitbreid\w*)\b/i.test(text);
  const action = /\b(?:onderzoek\w*|inspecteer\w*|demonstreer\w*|toon\w*|probeer\w*|gebruik\w*|test\w*)\b/i.test(text);
  const asksForNewCapability = /\b(?:nieuw|nieuwe|onbekend|ontdek\w*|zoek\w*\s+(?:een|naar)|installeer\w*|registreer\w*|voeg\w*\s+toe|bouw\w*|maak\w*|ontwikkel\w*|activeer\w*)\b/i.test(text)
    && !/\b(?:wat\s+je\s+(?:al|eerder)\s+(?:hebt|gemaakt)|bestaand|bestaande|huidig|huidige|daadwerkelijk\s+werkt|hoe\s+.*\s+werkt)\b/i.test(text);
  return capability && action && !asksForNewCapability;
}

function requestsCapabilityExpansion(value = '') {
  const text = commandText(value).toLowerCase();
  const capability = /\b(?:tool|tools|pakket|packages?|npm|mcp|connector|integratie|add-?ons?|skills?|plugins?|vaardigheid|vermogen|capabilit\w*|uitbreid\w*)\b/i.test(text);
  const discovery = /\b(?:nieuw|nieuwe|onbekend|ontdek\w*|zoek\w*|vind\w*|installeer\w*|registreer\w*|voeg\w*\s+toe|bouw\w*|maak\w*|ontwikkel\w*|activeer\w*)\b/i.test(text);
  return capability && discovery;
}

function requestsParallelWork(value = '') {
  const text = commandText(value).toLowerCase();
  const parallel = /\b(?:parallel|tegelijk(?:ertijd)?|gelijktijdig|swarm|werkcel|worker(?:s)?|subagents?|sub-agents?|onafhankelijke\s+(?:lijnen|werkstromen|onderzoeken|opdrachten))\b/i.test(text);
  const work = /\b(?:onderzoek\w*|zoek\w*|vergelijk\w*|analyseer\w*|voer\w*|start\w*|run\w*|verdeel\w*|laat\w*)\b/i.test(text);
  return parallel && work;
}

function requestsToolPackageManagement(value = '') {
  const text = commandText(value).toLowerCase();
  const packageTarget = /\b(?:npm|toolpakket|package|pakket)\b/i.test(text);
  const action = /\b(?:ontdek\w*|zoek\w*\s+op|installeer\w*|stage\w*|inspecteer\w*|activeer\w*|quarantaineer\w*)\b/i.test(text);
  const buildsAdapter = /\b(?:maak|bouw|ontwikkel|registreer|stage|beproef|activeer)\w*\b.{0,160}\b(?:add-?on|skill|vaardigheid|adapter|capability)\b/i.test(text)
    || /\b(?:add-?on|skill|vaardigheid|adapter|capability)\b.{0,160}\b(?:maak|bouw|ontwikkel|registreer|stage|beproef|activeer)\w*\b/i.test(text);
  return packageTarget && action && !buildsAdapter;
}

function requestsCapabilityPromotion(value = '') {
  const text = commandText(value).toLowerCase();
  return /\b(?:staged|gestaged|bestaande\s+add-?on|add-?on)\b/i.test(text)
    && /\b(?:beproef\w*|activeer\w*|promoveer\w*)\b/i.test(text)
    && /\b(?:add-?on|capability|vaardigheid)\b/i.test(text);
}

function requestsExecutableBuild(value = '') {
  const text = commandText(value).toLowerCase();
  // "Maakt me niet uit, bijvoorbeeld openmail.sh" is voorkeurstaal. Een
  // domeinnaam met een toevallige code-extensie is geen bouwopdracht.
  if (/^maakt?\s+me\s+niet\s+uit\b/i.test(text)) return false;
  // Een ontkennende grens ("maak geen account", "schrijf niets") mag een
  // genoemd technisch woord zoals CLI niet in een bouwmissie veranderen.
  if (/\b(?:bouw\w*|programmeer\w*|schrijf\w*|maak\w*|implementeer\w*|voer\w*)\s+(?:geen|niets|niet)\b/i.test(text)) return false;
  const create = /\b(?:bouw\w*|programmeer\w*|schrijf\w*|maak\w*|implementeer\w*)\b/i.test(text);
  const artifact = /\b(?:script|programma|code|cli|command[ -]?line|calculator|website|app|tool|prototype|module|library|bibliotheek|automatisering|oplossing)\b/i.test(text)
    || /(?:^|\s)(?!https?:\/\/)(?:\.\/|data\/workspace\/)?[a-z0-9_/-]+\.(?:py|m?js|cjs|ts|sh)(?=\s|[.,!?;:]|$)/i.test(text);
  return create && artifact;
}

function requestsWorkspaceFileEdit(value = '') {
  const text = commandText(value).toLowerCase();
  const editVerb = /^\s*(?:zet|voeg|schrijf|plaats|pas|verander|wijzig|werk)\b/i.test(text);
  const fileReference = /\bREADME(?:[-_.][a-z0-9_-]+)?(?:\.[a-z0-9]+)?\b/i.test(text)
    || /(?:^|\s)(?:data\/workspace\/)?[a-z0-9][a-z0-9_./-]*\.[a-z0-9]{1,12}(?=\s|[.,!?;:]|$)/i.test(text)
    || /\b(?:dit|het|de)\s+(?:bestaande\s+)?bestand\b/i.test(text);
  const editShape = /\b(?:onderaan|bovenaan|erin|in\s+het|in\s+de|aan\s+het|aan\s+de|bij|toe|aanpassen|wijzigen)\b/i.test(text);
  return editVerb && fileReference && editShape;
}

function requestedWorkspaceEditPath(value = '') {
  const text = commandText(value);
  const explicit = text.match(/(?:^|\s)((?:data\/workspace\/)?[a-z0-9][a-z0-9_./-]*\.[a-z0-9]{1,12})(?=\s|[.,!?;:]|$)/i)?.[1];
  if (explicit) return explicit.replace(/[.,!?;:]+$/g, '');
  return text.match(/\b(README(?:[-_.][a-z0-9_-]+)?(?:\.[a-z0-9]+)?)\b/i)?.[1] || '';
}

function workspaceEditPathMatches(actual = '', requested = '') {
  const left = String(actual || '').replace(/\\/g, '/').replace(/^\.\//, '');
  const right = String(requested || '').replace(/\\/g, '/').replace(/^\.\//, '');
  if (!left || !right) return false;
  return left === right || left.endsWith(`/${right}`) || path.basename(left) === path.basename(right);
}

function workspaceFileEditRemaining(toolResults = [], requiredTools = [], userText = '') {
  const target = requestedWorkspaceEditPath(userText);
  const readIndex = toolResults.findIndex((tool) => (
    tool.name === 'lees_bestand' && tool.ok && tool.verified
    && workspaceEditPathMatches(readArtifactPath(tool), target)
  ));
  if (readIndex < 0) return ['lees_bestand'];
  const writeIndex = toolResults.findIndex((tool, index) => (
    index > readIndex && tool.name === 'schrijf_bestand' && tool.ok && tool.verified
    && workspaceEditPathMatches(writtenArtifactPath(tool), target)
  ));
  if (writeIndex < 0) return ['schrijf_bestand'];
  if (requiredTools.includes('voer_uit')) {
    const execution = toolResults.find((tool, index) => (
      index > writeIndex && tool.name === 'voer_uit' && successfulZeroExit(tool)
    ));
    if (!execution) return ['voer_uit'];
  }
  return [];
}

function isExplicitToolRequest(text = '') {
  const raw = cleanUserText(text);
  const value = commandText(text).toLowerCase();
  // Begrijp de grammaticale rol van een anaforische verwijzing in plaats van
  // exacte opdrachten op te sommen. commandText verwijdert eerst een beleefd
  // vraagkader, zodat zowel "maak dat" als "kun je dit repareren" hier landen.
  const reference = String.raw`(?:dit|dat|het|hier|daar|hiermee|daarmee|ermee)`;
  const directive = String.raw`(?:maak|werk|repareer|fix|regel|onderzoek|zoek|controleer|bekijk|lees|schrijf|bouw|voer|meld|registreer|installeer|activeer|probeer|los|pas)`;
  const infinitive = String.raw`(?:maken|uitwerken|repareren|fixen|regelen|onderzoeken|uitzoeken|controleren|bekijken|lezen|schrijven|bouwen|uitvoeren|melden|registreren|installeren|activeren|proberen|oplossen|aanpassen)`;
  const contrastive = /\bniet\b.{0,100}\bmaar\b/i.test(value);
  const negated = /\bniet\b/i.test(value) && !contrastive;
  const directiveLed = new RegExp(
    String.raw`^\s*(?:${directive})\w*\b.{0,32}\b${reference}\b`, 'i',
  ).test(value);
  if (directiveLed && negated) return false;
  if (directiveLed && !negated) return true;
  const politeRequest = /^\s*(?:(?:kun|kan|wil|zou)\s+(?:je|jij)|wil\s+je)\b/i.test(raw);
  if (politeRequest && !negated && new RegExp(
    String.raw`^\s*${reference}\b.{0,48}\b${infinitive}\b`, 'i',
  ).test(value)) return true;
  if (requestsWorkspaceFileEdit(value)) return true;
  if (/^\s*(?:zet|schakel|doe)\b.{0,100}\b(?:aan|uit)\b/.test(value) && hasHomeControlTarget(value)) return true;
  if (/\b(?:aan|uit)\s*(?:zetten|doen|schakelen)\b|\b(?:openen|sluiten)\b/.test(value)) return true;
  if (/^\s*(?:ga\s+)?(?:open|sluit|stuur|onthoud|herinner|zoek|onderzoek|uitzoeken|controleer|bekijk|kijk|lees|repareer|fix|voer|meld|registreer|zoek\s+uit|werk\s+uit)\b/.test(value)) return true;
  if (/\b(?:meld\s+(?:je|jou)|registreer\s+(?:je|jou)|account\s+aanmak\w*|je\s+moet\s+je\s+aanmeld\w*)\b/.test(value)) return true;
  if (/\blees_bestand\b|\bsrc\/[a-z0-9_./-]+\b/.test(value)) return true;
  if (/\b(?:inspecteer|bekijk|lees|controleer)\w*\b.{0,100}\b(?:broncode|configuratie|configbestand|projectbestand)\b/.test(value)) return true;
  if (!asksForBuildOpinion(value)
    && /\b(?:bouw(?:en)?|programmeer|programmeren|schrijf|schrijven|maak|maken)\b/.test(value)
    && /\b(?:bestand|rapport|script|programma|code|calculator|dashboard|analyse|overzicht|automatisering|proef|website|app|tool|prototype|oplossing)\b/.test(value)) return true;
  return false;
}

function asksForBuildOpinion(text = '') {
  const value = String(text || '');
  return /\b(?:goed|slecht|slim|dom|handig|onhandig)\s+idee\b|\bwat\s+vind\s+(?:je|jij)\b|\bhoe\s+kijk\s+(?:je|jij)\s+(?:daar|ernaar)\b|\blijkt\s+(?:je|jou)\b/i.test(value)
    && !/(?:^|[.!?]\s*)(?:bouw|programmeer|schrijf|maak|implementeer)\b/i.test(value);
}

function isContinuationRequest(text = '') {
  const value = commandText(text).toLowerCase()
    .replace(/^\s*(?:(?:ja|ok(?:é)?|okay|prima|goed|nou|dan)\s*[,;:]?\s*)+/i, '')
    .replace(/[.!?]+$/g, '').trim();
  if (!value || value.length > 180) return false;
  return /^(?:doe\s+(?:dit|dat|het)(?:\s+dan)?|regel\s+(?:dit|dat|het)|los\s+(?:dit|dat|het)\s+op|pas\s+(?:dit|dat|het)\s+aan|zorg\s+(?:dat\s+)?(?:dit|dat|het)\s+werkt|probeer\s+(?:dit|dat|het|nog)\s*(?:opnieuw|nog\s+een\s+keer)?|ga\s+(?:hiermee|daarmee|ermee)\s+verder|ga\s+(?:(?:hier|daar|er)\s+)?(?:mee\s+)?verder(?:\s+met\b.*)?|ga\s+verder\s+met\b.+|ga\s+door|maak\s+(?:dit|dat|het|deze\s+klus|dit\s+project)\s+af|pak\s+(?:dit|dat|het|deze\s+klus|dit\s+project)\s+(?:weer\s+)?op|werk\s+(?:dit|dat|het|hier|daar)\s+verder\s+uit|voer\s+de\s+volgende\s+stap\s+uit|meld\s+(?:je|jou)\s+aan|registreer\s+(?:je|jou)|je\s+moet\s+je\s+aanmeld\w*)(?:\b|[.!?])/i.test(value);
}

function followsUnfinishedReply(text = '', previousReply = '') {
  const value = commandText(text).trim();
  const previous = String(previousReply || '').trim();
  if (!value || value.length > 600 || !previous) return false;
  const previousWasUnfinished = /\b(?:kan\s+(?:ik|het)\s+(?:nu\s+)?niet|niet\s+gelukt|niet\s+bereiken|loop\s+ik\s+(?:nu\s+)?op\s+vast|ontbreekt|wacht\s+(?:nog\s+)?op|nog\s+niet|blokkade|stuur\s+me|geef\s+me)\b/i.test(previous);
  if (!previousWasUnfinished) return false;
  // Herken de gespreksdaad (corrigeren, aandringen of het gevraagde feit
  // leveren), los van één specifiek apparaat of één provider.
  return /^\s*(?:\d{6}|dat\s+(?:kun|kan)\s+je|waarom\s+zou|je\s+(?:kunt|kan|doet)|doe\s+niet\s+moeilijk|los\s+het|probeer|zoek\s+het|hier\s+is|dit\s+is|er\s+staat|ik\s+geef|in\s+de\s+mail|the\s+code|your\s+agent)\b/i.test(value)
    || /\b(?:ga\s+(?:je\s+)?maar\s+uitzoeken|je\s+hebt\s+alles|hoef\s+ik\s+(?:je|jou)\s+niet)\b/i.test(value);
}

function turnContractFor(text = '', { continuation = false, mode = 'chat', voiceDirective = null } = {}) {
  if (mode !== 'chat') return {
    kind: 'autonomy', outcome: 'een zelfgekozen, gecontroleerde uitkomst',
    proof: 'toolbewijs of een expliciete stille keuze', requiredTools: [], allowedTools: null,
    effort: config.reasoningAutonomy,
  };
  const value = cleanUserText(text).toLowerCase();
  const resolvedVoice = voiceDirective || resolveVoiceDirective({ text: value });
  if (resolvedVoice.requested) return {
    kind: 'voice_action',
    outcome: 'exact via het bedoelde Voice PE-doel spreken en de gebruikte route noemen',
    proof: 'een geslaagde doelgerichte spreekactie; geen algemene speakerfallback',
    requiredTools: ['spreek_in_huis'],
    allowedTools: new Set(['spreek_in_huis', 'ha_lees', 'zoek_geheugen', 'onthoud']),
    effort: 'medium',
  };
  if (requestsCapabilityPromotion(value)) return {
    kind: 'capability_promotion',
    outcome: 'de genoemde gestagede uitbreiding met haar gebonden proef activeren en exact die actieve versie demonstreren',
    proof: 'geslaagde proefreceipt, actieve registryversie en demo-uitvoer van dezelfde capabilitynaam',
    requiredTools: ['beheer_addon', 'gebruik_capability'],
    allowedTools: new Set(['beheer_addon', 'gebruik_capability', 'voer_uit', 'lees_bestand', 'inspecteer_vermogen']),
    effort: 'high',
  };
  if (requestsToolPackageManagement(value)) return {
    kind: 'tool_package',
    outcome: 'het genoemde pakket via de officiële registry inspecteren of exact en controleerbaar stagen',
    proof: 'registryprovenance, exacte versie, lockfile- en artifacthash; staging is nog geen werkende adapter',
    requiredTools: ['beheer_toolpakket'],
    allowedTools: new Set(['beheer_toolpakket', 'schrijf_bestand', 'voer_uit', 'lees_bestand', 'beheer_addon', 'gebruik_capability']),
    effort: 'high',
  };
  if (requestsCapabilityExpansion(value)) return {
    kind: 'capability_expansion',
    outcome: 'een ontbrekend vermogen onderzoeken, als echte uitvoerbare uitbreiding bouwen, beproeven, activeren en live gebruiken',
    proof: 'brononderzoek plus een receipt-gebonden add-onuitvoering; een instructietekst of plan telt niet als geïnstalleerd vermogen',
    requiredTools: [],
    allowedTools: null,
    effort: 'high',
  };
  if (requestsParallelWork(value)) return {
    kind: 'parallel_work',
    outcome: 'één gezamenlijk doel opdelen in onafhankelijke werkstromen, die aantoonbaar gelijktijdig uitvoeren en het bewijs samenvoegen',
    proof: 'persistente stream- en runrecords met afzonderlijke bronnen, tijden en uitkomsten; alleen dan paralleliteit claimen',
    requiredTools: ['beheer_werkcel'],
    allowedTools: new Set(['beheer_werkcel']),
    effort: 'high',
  };
  if (requestsCapabilityDemonstration(value)) return {
    kind: 'capability_demo',
    outcome: 'één bestaande actieve uitbreiding met haar actuele receipt aantoonbaar gebruiken en de grenzen exact noemen',
    proof: 'de registry accepteert uitsluitend een actieve capability waarvan versie, inhoudshash en proefreceipt nog overeenkomen',
    requiredTools: ['gebruik_capability'],
    allowedTools: new Set(['gebruik_capability']),
    effort: 'medium',
  };
  if (continuation) return {
    kind: 'conversation_continuation',
    outcome: 'de direct voorafgaande gekozen taak werkelijk voortzetten tot een bruikbaar resultaat',
    proof: 'bewijs dat bij precies die gesprekstaak hoort; geen oudste of ongerelateerde klus',
    requiredTools: [],
    allowedTools: null,
    effort: 'high',
  };
  const asksEarningResearch = /\b(?:geld\s+verdienen|verdien(?:en|model)|inkomsten?|omzet|koper|betaal(?:d|bereidheid)|prijs)\b/i.test(value);
  if (asksEarningResearch) {
    const readOnly = /\b(?:verander|wijzig|schrijf|maak|verstuur|stuur)\b.{0,60}\b(?:niets|niet)\b/i.test(value)
      || /\bzonder\b.{0,50}\b(?:veranderen|wijzigen|schrijven|maken|versturen)\b/i.test(value);
    return {
      kind: 'earning_route',
      outcome: readOnly
        ? 'één realistische route kiezen en alleen de eerste concrete intern uitvoerbare stap kort noemen, zonder iets te maken of te wijzigen'
        : 'één realistische route kiezen, actueel bewijs controleren en de eerste intern uitvoerbare stap werkelijk klaarzetten',
      proof: readOnly
        ? 'actuele broncontrole; geen lokaal werkstuk, wijziging of verzending in deze beurt'
        : 'actuele broncontrole plus een teruggelezen lokaal werkstuk; zonder bronbewijs geen aanbod of prijsclaim',
      requiredTools: ['zoek_internet'],
      // Externe verzending blijft buiten bereik; binnen de werkruimte mag Eva wel
      // doorpakken naar het aanbod, concept of klantselectie dat de gebruiker vroeg.
      allowedTools: new Set(readOnly
        ? ['zoek_internet', 'lees_bestand', 'lijst_bestanden']
        : ['zoek_internet', 'schrijf_bestand', 'lees_bestand', 'lijst_bestanden']),
      effort: 'high',
    };
  }
  const asksHouseholdContribution = /\b(?:bijdrag\w*|help\w*|beteken\w*|ontlast\w*|nuttig\w*|meerwaarde)\b/i.test(value)
    && /\b(?:gezin|huishouden|thuis|ons|onze|jullie)\b/i.test(value);
  if (asksHouseholdContribution) {
    return {
      kind: 'household_contribution',
      outcome: 'één actuele, specifieke gezinsbijdrage kiezen en waar mogelijk meteen uitvoeren',
      proof: 'actuele huis- of werkcontext en een merkbaar effect voor een bewoner',
      requiredTools: ['huis_status'],
      // De statuslezing grondt de keuze, maar schrijft niet voor welk domein de
      // bijdrage moet hebben. Eva mag daarna zelf huis, onderzoek of werk kiezen.
      allowedTools: null,
      effort: 'medium',
    };
  }
  if (/\b(?:los\w*\s+op|repareer|fix|pas\w*\s+aan|zorg\s+dat).{0,100}\b(?:werkt|opgelost|goed)\b/i.test(value)) {
    return {
      kind: 'repair', outcome: 'de genoemde fout herstellen en dezelfde gebruikersuitkomst opnieuw controleren',
      proof: 'een andere strategie dan de mislukte poging plus een gerichte hertest',
      requiredTools: [], allowedTools: null, effort: 'high',
    };
  }
  return {
    kind: 'conversation', outcome: 'de actuele vraag direct en specifiek beantwoorden',
    proof: 'alleen gereedschap gebruiken wanneer de vraag uitvoering of actuele feiten vereist',
    requiredTools: [], allowedTools: null, effort: null,
  };
}

function isLongWork(text = '') {
  const value = cleanUserText(text);
  return value.length > 420
    || /\b(?:van a tot z|uitgebreid|grondig|onderzoek|bouw|programmeer|repareer|vergelijk.+bronnen|maak.+bestand|voer.+uit)\b/i.test(value)
    || /\b(?:regel|bereid|zet)\b.{0,140}\b(?:echt\s+)?klaar\b/i.test(value)
    || requestsSelfImprovement(value)
    || requestsCapabilityDemonstration(value)
    || /^\s*zoek\b.{0,160}\b(?:uit|actueel|offici[eë]le|bronnen?|regeling|subsidie|prijs|markt)\b/i.test(value);
}

function isReflectiveQuestion(text = '') {
  const value = commandText(text).trim();
  if (!/^(?:werkt|werken|is|zijn|kan|kunnen|klopt|waarom|hoe|wat|welke|wanneer|waar)\b/i.test(value)) return false;
  const imperative = /(?:^|[.!?]\s*)(?:zet|schakel|open|sluit|zeg|spreek|praat|laat|stuur|maak|doe|voer|repareer|fix|test|onderzoek|zoek\s+uit)\b/i.test(value);
  const politeAction = /^\s*(?:kan|kun|wil)\s+je\b.{0,100}\b(?:aanzetten|uitzetten|openen|sluiten|zeggen|spreken|sturen|maken|doen|uitvoeren|repareren|testen)\b/i.test(value);
  return !imperative && !politeAction;
}

function isDurableProjectRequest(text = '') {
  const value = commandText(text);
  const negatedAction = /\b(?:bouw\w*|programmeer\w*|schrijf\w*|maak\w*|repareer\w*|voer\w*)\s+(?:(?:nu|nog|voorlopig)\s+)?(?:geen|niets|niet)\b/i.test(value);
  const researchOnlyBoundary = negatedAction
    && /\b(?:nu\s+)?alleen\b.{0,180}\b(?:keuze|antwoord|onderzoek|bewijs|analyse|advies|volgende\s+stap)\b/i.test(value);
  if (researchOnlyBoundary) return false;
  const action = /\b(?:kijk(?:en)?|zoek(?:en)?|uitzoeken|onderzoek(?:en)?|vind(?:en)?|bedenk(?:en)?|vergelijk(?:en)?|selecteer|selecteren|kies|kiezen|bouw(?:en)?|maak|maken|programmeer|programmeren|repareer|repareren|los)\b/i.test(value);
  const externalOutcome = /\b(?:geld.{0,40}\b(?:verdienen|vragen|krijgen)|inkomsten?|betaald|betalende|verkopen?|verkoopbaar|klanten?|opbrengst|bount(?:y|ies)|verdienmodel)\b/i.test(value);
  const multipleSteps = (value.match(/\b(?:zoek(?:en)?|uitzoeken|onderzoek(?:en)?|verzamel(?:en)?|groepeer|groeperen|vergelijk(?:en)?|selecteer|selecteren|kies|kiezen|bouw(?:en)?|maak|maken|test(?:en)?|controleer|controleren|lever(?:en)?|verkoop|verkopen)\b/gi) || []).length >= 2;
  const broadScope = /\b(?:tientallen|honderd|meerdere\s+(?:bronnen|problemen|kandidaten|bestanden|stappen)|van\s+a\s+tot\s+z|daarna\s+(?:bouw|maak|test)|groepeer|werkend\s+eindresultaat)\b/i.test(value);
  return value.length > 420
    || /\b(?:van a tot z|meerdere stappen|werkend eindresultaat|maak.+(?:bestand|rapport|script|programma|code|website|app))\b/i.test(value)
    || (!negatedAction && /\b(?:bouw\w*|programmeer\w*|repareer\w*)\b/i.test(value))
    || /\b(?:regel|bereid|zet)\b.{0,140}\b(?:echt\s+)?klaar\b/i.test(value)
    || requestsSelfImprovement(value)
    || requestsCapabilityDemonstration(value)
    || (action && externalOutcome)
    || (multipleSteps && broadScope);
}

function isWorkRecapQuery(text = '') {
  const value = cleanUserText(text).toLowerCase().replace(/\s+/g, ' ').trim();
  return /\b(?:wat heb je(?: vandaag)?(?: echt)? (?:gedaan|afgemaakt|bijgedragen)|waar ben je(?: nu)?(?: echt)? mee bezig|wat (?:ben je|doe je) nu|wat staat(?: er)?(?: nog)? open|hoe staat (?:het|je werk) ervoor|wat heb je ons(?: vandaag)? gebracht)\b/i.test(value)
    || /\bwat heb je (?:net|zojuist).{0,120}\b(?:gedaan|uitgevoerd|geschakeld|aangepast)\b/i.test(value);
}

function identityNameFromText(text = '', sender = '') {
  return identityNameCandidate(cleanUserText(text), sender);
}

function isRecognitionConfirmation(text = '') {
  return /^(?:ja\s*[,;:]?\s*)?(?:dat\s+)?klopt(?:\s+(?:inderdaad|precies))?[.!?]*$/iu
    .test(cleanUserText(text));
}

function confirmedRecognitionName(text = '', replyTo = null) {
  if (!isRecognitionConfirmation(text)) return null;
  const caption = String(replyTo?.text || '').split(/\r?\n/, 1)[0].trim();
  const match = caption.match(/^([\p{L}][\p{L}' -]{0,78}?)\s+is\s+bij\b/iu);
  return match?.[1]?.trim() || null;
}

function requiredToolsFor({ text = '', replyEvidence = null } = {}) {
  const rawValue = cleanUserText(text).toLowerCase();
  const value = commandText(text).toLowerCase();
  if (requestsParallelWork(value)) return ['beheer_werkcel'];
  if (requestsCapabilityPromotion(value)) return ['beheer_addon', 'gebruik_capability'];
  if (requestsToolPackageManagement(value)) return ['beheer_toolpakket'];
  if (replyEvidence?.imageDataUrl && identityNameFromText(text, 'ik')) {
    return ['leer_gezicht_van_foto'];
  }
  const externalCapabilityQuestion = /^\s*(?:kan|kun|ondersteunt|werkt|is\s+het\s+mogelijk|hoe\s+kan)\b/i.test(rawValue)
    && /\b(?:api|endpoint|sdk|cli|website|dienst|provider|integratie|koppeling|plugin|add-?on|bibliotheek|library|account|inbox|e-?mail|software)\b/i.test(rawValue);
  if (externalCapabilityQuestion) {
    const asksAboutSelf = /\b(?:jij|je|jouw|jezelf|eigen|koppeling|integratie|wat\s+je\s+al\s+kunt)\b/i.test(rawValue);
    return [
      'inspecteer_vermogen',
      'zoek_internet',
      ...(asksAboutSelf ? ['lees_bestand'] : []),
    ];
  }
  const asksRobot = requestsRobotAction(rawValue);
  const asksFile = /\blees_bestand\b|\b(?:src|data\/workspace)\/[a-z0-9_./-]+\.[a-z0-9]+\b/i.test(value)
    || /\b(?:inspecteer|bekijk|lees|controleer)\w*\b.{0,120}\b(?:bestand|broncode|configuratie|configbestand|projectbestand)\b/i.test(value);
  if (asksRobot && asksFile) {
    const fileAt = value.search(/\blees_bestand\b|\b(?:src|data\/workspace)\//i);
    const robotAt = value.search(/\b(?:eriks?|robotstofzuiger|stofzuigerrobot|stofzuiger)\b/i);
    return fileAt >= 0 && (robotAt < 0 || fileAt < robotAt)
      ? ['lees_bestand', 'gebruik_robot']
      : ['gebruik_robot', 'lees_bestand'];
  }
  if (asksRobot) return ['gebruik_robot'];
  const attendancePersonConfig = /\b(?:aanwezigheid|thuisstatus|person(?:\.|\s)|tracker|telefoon)\b/i.test(value)
    && /\b(?:koppel|voeg|verwijder|haal|gebruik|vervang|wijzig|pas\s+aan|instel|instellen|moet\s+jij\s+doen|doe\s+(?:jij\s+)?dat)\w*\b/i.test(value);
  if (attendancePersonConfig) return ['beheer_aanwezigheid'];
  if ((/^\s*(?:zet|schakel|doe)\b.{0,120}\b(?:aan|uit)\b/i.test(value) && hasHomeControlTarget(value))
    || /^\s*(?:open|sluit)\b.{0,120}\b(?:raam|deur|gordijn|rolluik)\b/i.test(value)) {
    return ['bedien_huis'];
  }
  if (/\b(?:aan|uit)\s*(?:zetten|doen|schakelen)\b|\b(?:openen|sluiten)\b/i.test(value)
    && (/\b(?:licht|lamp|verlichting|schakelaar|stekker|ventilator|verwarming|rolluik|gordijn|raam|deur)\b/i.test(value)
      || /\b(?:light|switch|fan|climate|cover|media_player|vacuum)\.[a-z0-9_]+\b/i.test(value))) {
    return ['bedien_huis'];
  }
  if (/^\s*(?:herinner|zet\s+een\s+herinnering|plan\s+een\s+herinnering)\b/i.test(value)) {
    return ['zet_herinnering'];
  }
  if (/^\s*(?:annuleer|verwijder)\b.{0,80}\bherinnering\b/i.test(value)) {
    return ['annuleer_herinnering'];
  }
  if (/^\s*(?:onthoud|noteer)\b/i.test(value)) return ['onthoud'];
  if (/\b(?:e-?mail|mailtje|inbox)\b/i.test(value)
    && /\b(?:stuur|verstuur|mail|schrijf|lees|bekijk|controleer|zoek|ontvang|antwoord|reply|berichten?)\w*\b/i.test(value)) {
    return ['beheer_agentmail'];
  }
  if (/^\s*(?:stuur|bericht)\b.{0,80}\b(?:naar|aan)\b/i.test(value)) return ['stuur_bericht'];
  if (/\bagentmail\b/i.test(value)
    && /\b(?:aanmeld\w*|meld\s+(?:je|jou)\s+aan|registreer\w*|sign[ -]?up|account\s+aanmak\w*|verifi(?:eer|ëren)|otp|status)\b/i.test(value)) {
    return ['beheer_agentmail'];
  }
  const documentationRequested = /\b(?:documentatie|docs?|api\s*reference|handleiding|website|url|link|endpoint|methode)\b/i.test(value)
    && /\b(?:zoek|onderzoek|controleer|bekijk|lees|vind|klopt|actueel|offici[eë]le?)\w*\b/i.test(value);
  if (documentationRequested && !requestsCapabilityExpansion(value)) return ['zoek_internet'];
  if (requestsWorkspaceFileEdit(value)) {
    const asksToRun = /\b(?:draai|run|voer\s+uit|start)\b.{0,80}\b(?:test|tests|programma|script|cli)\b|\b(?:test|tests)\b.{0,50}\b(?:draai|run|uitvoer)\w*\b/i.test(value);
    return ['lees_bestand', 'schrijf_bestand', ...(asksToRun ? ['voer_uit'] : [])];
  }
  if (/(?:^|[.!?]\s*)(?:ga\s+)?(?:zoek(?:\s+uit)?|uitzoeken|onderzoek|controleer|diagnosticeer)\b.{0,200}\b(?:home\s+assistant|ha(?:-status|-verbinding|\s+verbinding))\b/i.test(value)) {
    return ['huis_status', 'ha_lees'];
  }
  const researchRequested = /^\s*(?:zoek|onderzoek|uitzoek(?:en)?|zoek\s+uit|vergelijk)\b/i.test(value)
    || (/(?:^|[.!?]\s*)(?:zoek|onderzoek|uitzoek(?:en)?|zoek\s+uit|vergelijk|controleer)\b/i.test(value)
      && /\b(?:online|internet|website|site|url|link|bron(?:nen)?|documentatie|docs?|actueel|publiek|markt|prijs|retentie|regeling|subsidie)\b/i.test(value));
  const selfImprovementRequested = requestsSelfImprovement(value);
  const capabilityExpansionRequested = requestsCapabilityExpansion(value);
  const capabilityDemoRequested = requestsCapabilityDemonstration(value);
  if (capabilityDemoRequested) return ['gebruik_capability'];
  if (capabilityExpansionRequested) {
    const forbidsInternet = /\b(?:geen|zonder)\s+internet\b|\boffline\b/i.test(value);
    return [
      'inspecteer_vermogen',
      ...(!forbidsInternet ? ['zoek_internet'] : []),
      'schrijf_bestand',
      'voer_uit',
      'lees_bestand',
      'beheer_addon',
      'gebruik_capability',
    ];
  }
  if (selfImprovementRequested) {
    return ['schrijf_bestand', 'voer_uit', 'lees_bestand', 'maak_vaardigheid'];
  }
  const forbidsBuild = /\b(?:bouw\w*|programmeer\w*|schrijf\w*|maak\w*|implementeer\w*|sla\w*\s+op)\s+(?:(?:nu|nog|voorlopig)\s+)?(?:geen|niets|niet)\b/i.test(value);
  const buildRequested = !forbidsBuild && !asksForBuildOpinion(value) && (requestsExecutableBuild(value) || (
    /\b(?:bouw(?:en)?|programmeer|programmeren|schrijf|schrijven|maak|maken)\b/i.test(value)
    && /\b(?:bestand|rapport|script|programma|code|calculator|dashboard|analyse|overzicht|automatisering|proef|website|app|tool|prototype|oplossing)\b/i.test(value)
  ));
  if (buildRequested) {
    const executable = requestsExecutableBuild(value)
      || /\b(?:script|programma|code|cli|calculator|website|app|tool|prototype|oplossing)\b/i.test(value);
    return [
      ...(researchRequested ? ['zoek_internet'] : []),
      'schrijf_bestand',
      ...(executable ? ['voer_uit'] : []),
      'lees_bestand',
    ];
  }
  const preparedDeliverable = /\b(?:regel|bereid|zet)\b.{0,140}\b(?:echt\s+)?klaar\b/i.test(value)
    && !hasHomeControlTarget(value);
  if (preparedDeliverable) return ['schrijf_bestand', 'lees_bestand'];
  if (researchRequested) return ['zoek_internet'];
  if (/^\s*(?:bekijk|controleer)\b.{0,80}\b(?:camera|deurbel|tuin|oprit)\b/i.test(value)) {
    return ['bekijk_camera'];
  }
  if (/^\s*(?:controleer|kijk)\b.{0,80}\b(?:thuis|aanwezig|telefoonstatus|ha-status)\b/i.test(value)) {
    return ['wie_is_thuis'];
  }
  if (/\b(?:bekijk|zoek|analyseer|controleer)\w*\b.{0,120}\b(?:gesprek(?:ken)?|chat(?:s|berichten)?|berichtenhistorie|tijdlijn)\b/i.test(value)) {
    return ['zoek_gesprek'];
  }
  if (/^\s*(?:lees|bekijk)\b.{0,80}\bbestand\b/i.test(value)) return ['lees_bestand'];
  if (/\blees_bestand\b|\bsrc\/[a-z0-9_./-]+\b/i.test(value)
    || /\b(?:inspecteer|bekijk|lees|controleer)\w*\b.{0,120}\b(?:broncode|configuratie|configbestand|projectbestand)\b/i.test(value)) {
    return ['lees_bestand'];
  }
  return [];
}

function chatToolNamesFor(requiredTools = []) {
  if (!requiredTools.length) return null;
  const names = new Set(requiredTools);
  // Deze read-only vermogens zijn na een mislukte voorkeursroute beschikbaar
  // voor diagnose en een materieel andere aanpak. runAgent biedt vóór een fout
  // nog steeds uitsluitend de eerstvolgende vereiste tool aan.
  for (const name of [
    'huis_status', 'ha_lees', 'wie_is_thuis', 'zoek_internet', 'lees_bestand',
    'lijst_bestanden', 'zoek_geheugen', 'zoek_gesprek', 'toon_herinneringen', 'toon_werk',
  ]) names.add(name);
  if (requiredTools.includes('schrijf_bestand')) {
    names.add('lijst_bestanden');
    // Eva may freely choose a document or executable. If she chooses code, the
    // same mission must be able to execute it instead of claiming an unrun demo.
    names.add('voer_uit');
  }
  if (requiredTools.includes('lees_bestand')) names.add('lijst_bestanden');
  // Bij een providerhandeling blijft actuele documentatie een veilige
  // alternatieve route wanneer de directe API een fout geeft.
  if (requiredTools.includes('beheer_agentmail')) names.add('zoek_internet');
  return names;
}

function directHomeCommand(text = '') {
  const value = commandText(text).trim();
  let match = value.match(/^\s*(?:zet|schakel|doe)\s+(.{1,160}?)\s+(?:weer\s+)?(aan|uit)\s*[.!?]*$/i);
  let target;
  let action;
  if (match) {
    [, target, action] = match;
  } else {
    match = value.match(/^\s*(open|sluit)\s+(.{1,160}?)\s*[.!?]*$/i);
    if (match) {
      action = /^open$/i.test(match[1]) ? 'open' : 'dicht';
      target = match[2];
    } else {
      match = value.match(/^\s*(.{1,160}?)\s+(aan|uit)\s*(?:zetten|doen|schakelen)\s*[.!?]*$/i);
      if (!match) return null;
      [, target, action] = match;
    }
  }
  target = String(target || '').replace(/^\s*(?:de|het|die|dat)\s+/i, '').trim();
  if (!target || /(?:,|\ben\b)/i.test(target) || /^(?:alle|alles)\b/i.test(target)) return null;
  return { doel: target, actie: String(action).toLowerCase() };
}

const MEMORY_STOP_WORDS = new Set([
  'aan', 'als', 'ben', 'bij', 'dat', 'de', 'deze', 'die', 'dit', 'een', 'en',
  'het', 'hier', 'hoe', 'ik', 'in', 'is', 'je', 'jij', 'maar', 'met', 'mij',
  'mijn', 'naar', 'niet', 'nog', 'nu', 'of', 'om', 'ons', 'ook', 'op', 'over',
  'te', 'van', 'voor', 'waar', 'wat', 'we', 'wel', 'wij', 'wil', 'zijn', 'zou',
]);

function recallWords(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .split(/\s+/)
    .filter((word) => word.length > 2 && !MEMORY_STOP_WORDS.has(word));
}

function recallOverlap(queryWords, content) {
  const words = recallWords(content);
  let score = 0;
  for (const queryWord of queryWords) {
    if (words.some((word) => word === queryWord)) score += 3;
    else if (queryWord.length >= 5 && words.some((word) => word.length >= 5 && (
      word.startsWith(queryWord.slice(0, -2)) || queryWord.startsWith(word.slice(0, -2))
    ))) score += 1;
  }
  return score;
}

function memoryContextText(query, sender) {
  let rows = [];
  try {
    rows = searchMemories(query, 40, false);
  } catch {
    rows = [];
  }
  const queryWords = recallWords(query);
  const person = String(sender || '').trim().toLowerCase();
  const selected = rows
    .filter((row) => Number(row.archived || 0) !== 1)
    .filter((row) => String(row.person || '').trim().toLowerCase() !== 'eva')
    .filter((row) => {
      const owner = String(row.person || '').trim().toLowerCase();
      return !owner || owner === person;
    })
    .map((row) => ({
      ...row,
      recallScore: recallOverlap(queryWords, row.content)
        + (String(row.person || '').trim().toLowerCase() === person ? 2 : 0),
    }))
    .filter((row) => row.recallScore > 0)
    .sort((a, b) => b.recallScore - a.recallScore
      || Number(b.importance || 0) - Number(a.importance || 0))
    .slice(0, 8);
  if (selected.length) bumpRecall(selected.map((row) => row.id));
  // Operationele voorkeuren (zoals een vast Voice PE-doel) zijn juist tijdens
  // een gereedschapsopdracht nodig. Laat expliciete opdrachten deze context niet
  // meer wegdrukken.
  const stablePersonMemories = memoriesForPerson(sender, 5);
  const seen = new Set();
  return [...selected, ...stablePersonMemories]
    .filter((row) => {
      if (seen.has(row.id)) return false;
      seen.add(row.id);
      return true;
    })
    .slice(0, 10)
    .map((row) => `- ${row.person ? `${row.person}: ` : ''}${row.content}`)
    .join('\n');
}

function autonomousMemoryContextText() {
  const candidates = allMemories(500).filter((row) => {
    if (String(row.person || '').trim().toLowerCase() === 'eva') return false;
    const content = String(row.content || '');
    if (/^\s*Eva wil\b/i.test(content)
      || /\bwil dat (?:Eva|ik)\b.{0,120}\b(?:opdracht|routing|doorpak|achtergrondwerk)\w*/i.test(content)
      || /\bbedoelt met doorpakken\b/i.test(content)) return false;
    return ['afspraak', 'voorkeur'].includes(String(row.category || '').toLowerCase());
  });
  const projects = candidates.filter((row) => (
    String(row.category || '').toLowerCase() === 'afspraak'
    && /\b(?:staat nog open|open klus|proef|rapport|onderzoek\w*|uitzoek\w*|verifi(?:eer|eren))\b/i.test(row.content)
  ));
  const preferences = candidates.filter((row) => String(row.category || '').toLowerCase() === 'voorkeur');
  const distinct = (rows, limit) => {
    const selected = [];
    for (const row of rows) {
      const content = String(row.content || '');
      const candidate = new Set(recallWords(content));
      if (!candidate.size) continue;
      const duplicate = selected.some((existing) => {
        const words = new Set(recallWords(existing.content));
        const overlap = [...candidate].filter((word) => words.has(word)).length;
        return overlap / Math.min(candidate.size, words.size) >= 0.3;
      });
      if (!duplicate) selected.push(row);
      if (selected.length >= limit) break;
    }
    return selected;
  };
  const selectedProjects = distinct(projects, 4);
  const selectedPreferences = distinct(preferences, 8);
  const selected = [...selectedProjects, ...selectedPreferences];
  if (selected.length) bumpRecall(selected.map((row) => row.id));
  const render = (rows) => rows
    .map((row) => `- ${row.person ? `${row.person}: ` : ''}${row.content}`)
    .join('\n');
  return [
    selectedProjects.length
      ? `Mogelijke eerdere projecten; behandel ze niet als open opdracht voordat hun actuele status is bewezen:\n${render(selectedProjects)}`
      : '',
    selectedPreferences.length
      ? `Gezinsvoorkeuren; dit zijn randvoorwaarden, geen opdrachten:\n${render(selectedPreferences)}`
      : '',
  ].filter(Boolean).join('\n\n');
}

function otherConversationContext(person, conversationId) {
  return recentOtherConversationsForPerson(person, conversationId, 8)
    .filter((row) => !/^(?:voice:)?(?:blackbox|live-|workcell-|toolpackage-|capability-|persona-eval|test)/i.test(String(row.conversation_id || '')))
    .map((row) => `${row.sender}: ${String(row.content || '').replace(/\s+/g, ' ').slice(0, 500)}`)
    .join('\n');
}

function conversationIdFor({ channel, sender, messageContext = null }) {
  const explicit = String(messageContext?.conversationId || '').trim();
  if (explicit) return explicit;
  const chatId = String(messageContext?.chatId || '').trim();
  if (chatId) return `${channel}:${chatId}`;
  const person = String(sender || 'huisgenoot')
    .normalize('NFKC')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase();
  return `${channel}:${person}`;
}

function telegramConversationRows(messageContext, limit) {
  const chatId = String(messageContext?.chatId || '').trim();
  const messageId = String(messageContext?.messageId || '').trim();
  if (!chatId) return [];
  if (!messageId) return recentTelegramMessages({ chatId, limit });
  const prior = recentTelegramMessages({
    chatId,
    beforeMessageId: messageId,
    limit: Math.max(1, limit - 1),
  });
  const current = telegramMessage(chatId, messageId);
  return current ? [...prior, current] : prior;
}

function telegramConversationText(row, sender) {
  const content = String(row.content || '').trim();
  if (row.kind === 'photo') {
    const who = row.role === 'assistant' ? 'Eva' : (row.sender || sender);
    return `[${who} stuurde een foto; Telegram-bericht ${row.message_id}${row.media_ref ? `; foto-ref ${row.media_ref}` : ''}. Bijschrift: ${content || 'geen bijschrift'}]`;
  }
  if (row.kind !== 'text') {
    return `[${row.sender || sender} stuurde een Telegram-${row.kind}${content ? `: ${content}` : ''}]`;
  }
  return content;
}

function conversationInput({
  channel,
  conversationId,
  sender,
  messageContext = null,
  replyEvidence = null,
  limit = 40,
}) {
  const rows = channel === 'telegram'
    ? telegramConversationRows(messageContext, limit)
    : recentConversation(conversationId, limit);
  const input = rows.map((row) => {
    const content = channel === 'telegram' ? telegramConversationText(row, sender) : row.content;
    if (row.role === 'assistant') return msg('assistant', content);
    return msg('user', `[${channel === 'telegram' ? 'telegram' : row.channel}] ${row.sender || sender}: ${content}`);
  });
  const derivedText = String(messageContext?.derivedText || '').trim();
  if (channel === 'telegram' && derivedText) {
    input.push(msg('user', `[telegram] ${sender}: ${derivedText}`));
  }
  if (replyEvidence?.imageDataUrl && input.length) {
    let currentIndex = input.length - 1;
    while (currentIndex > 0 && input[currentIndex]?.role !== 'user') currentIndex -= 1;
    const current = input[currentIndex];
    const text = current?.content?.map((part) => part.text || '').join(' ').trim() || `${sender} stuurde een beeld.`;
    input[currentIndex] = imageMsg(
      `${text}\nDit is het exacte lokaal teruggelezen beeld uit de actuele Telegram-reply. Baseer beeldclaims op deze pixels.`,
      replyEvidence.imageDataUrl,
    );
  }
  return input;
}

function replyNeedsNarration(value = '') {
  return /\b(?:dat is niet gelukt|controleerbare uitkomst|bewijs dat|bewijsgrens|exitcode|aangeroepen|gereedschap|tools?|tussenwerk|werkpunt|beurtcontract|uitvoering stopte|nog niet af:|handeling niet uitgevoerd|allowlist|deze (?:chat)?beurt|in deze (?:chat)?sessie|ander systeem|twee systemen|niet aangeboden|vanuit deze route|api-endpoint|post-aanroep|sdk|module\s+not\s+found|zoekroute|operation was aborted|timeout)\b/i
    .test(String(value || ''));
}

function claimsCompletedAction(value = '') {
  return /\b(?:gelukt|klaar|afgerond|gemaakt|geschreven|uitgevoerd|verstuurd|verzonden|geactiveerd|geïnstalleerd|staat\s+nu\s+(?:aan|uit|open|dicht))\b/i
    .test(String(value || ''));
}

function conclusionInvestigationFeedback(reply = '', userText = '', toolResults = [], actionRequested = null) {
  const answer = String(reply || '').trim();
  const inspectConclusion = actionRequested == null
    ? /(?:^|[.!?]\s*)(?:zoek(?:\s+uit)?|onderzoek|controleer|diagnosticeer|probeer|maak|doe|regel|los)\b/i.test(userText)
    : actionRequested;
  if (effectBoundary(userText).actionIntent || !inspectConclusion) return '';
  const scope = `${userText}\n${answer}`;
  const claimsIncapacity = /\b(?:(?:kan|kon)\s+ik\b.{0,80}\bniet|kan\s+(?:eva|dit|dat|het)\b.{0,80}\bniet|kunnen\s+(?:we|dit|dat)\b.{0,80}\bniet|ik\s+kom\s+(?:nu\s+)?niet\s+bij|heb\s+ik\s+geen\s+toegang|geen\s+(?:schrijf|uitvoer|api|netwerk|account|browser|systeem)?toegang|niet\s+(?:ondersteund|beschikbaar|bereikbaar|mogelijk)|ontbreekt\s+(?:mij|me|hier)|(?:loop|liep)\s+ik\s+(?:nu\s+)?op\s+vast|daardoor\s+kan\s+ik\b.{0,100}\bniet|zoekroute\s+(?:hapert|werkt\s+niet)|ik\s+kan\b.{0,100}\b(?:niet\s+openen|niet\s+uitvoeren|niet\s+vaststellen|niet\s+bereiken))\b/i.test(answer);
  if (!claimsIncapacity) return '';

  const parsedResults = toolResults.map((tool) => {
    let parsed = null;
    try { parsed = JSON.parse(String(tool.output || '')); } catch { /* oudere uitvoer */ }
    return { tool, parsed };
  });
  const humanDependency = parsedResults.some(({ tool, parsed }) => (
    /^human_/i.test(String(parsed?.waiting_for || parsed?.data?.waiting_for || ''))
    && tekstOverlap(userText, `${JSON.stringify(tool.args || {})} ${tool.evidence?.summary || tool.output || ''}`) >= 0.04
  ));
  if (humanDependency) return '';

  const technicalOrExternal = /\b(?:api|endpoint|sdk|cli|website|site|online|internet|provider|dienst|account|inbox|e-?mail|integratie|koppeling|plugin|add-?on|bibliotheek|library|software|documentatie|docs?|token|sleutel|auth)\b/i.test(scope);
  const researched = toolResults.some((tool) => {
    if (tool.name !== 'zoek_internet' || !tool.ok || !tool.verified) return false;
    const query = String(tool.args?.vraag || '');
    const evidence = String(tool.evidence?.summary || tool.output || '');
    const officialIntent = /\b(?:offici[eë]le?|documentatie|docs?|api\s*reference|handleiding|developer)\b/i.test(query);
    return officialIntent && tekstOverlap(userText, `${query} ${evidence}`) >= 0.04;
  });
  const inspectedSelf = toolResults.some((tool) => (
    toolHasTag(tool.name, 'reasoning:mechanical')
    && tool.ok && tool.verified
  ));
  const attemptedAction = toolResults.some((tool) => (
    toolEffect(tool.name, tool.args) !== 'read'
    && (tool.changed === true || tool.ok !== true || tool.verified !== true)
    && tekstOverlap(userText, `${JSON.stringify(tool.args || {})} ${tool.evidence?.summary || tool.output || ''}`) >= 0.04
  ));
  const failed = toolResults.filter((tool) => tool.ok !== true || tool.verified !== true);
  const successfulAlternative = toolResults.some((tool) => (
    tool.ok && tool.verified && !failed.some((failure) => failure.name === tool.name)
    && tekstOverlap(userText, `${JSON.stringify(tool.args || {})} ${tool.evidence?.summary || tool.output || ''}`) >= 0.04
  ));

  if (!toolResults.length) {
    return 'Je conclusie is nog een aanname: je hebt niets geprobeerd of onderzocht. Onderzoek nu zelf de actuele mogelijkheid met aangeboden gereedschap. Gebruik bij een externe dienst eerst de officiële documentatie en controleer bij een eigen vermogen ook je relevante bron of actuele status. Geef nog geen blokkadeantwoord.';
  }
  if (technicalOrExternal && !researched) {
    return 'Voordat je zegt dat dit niet kan, zoek je nu zelf de actuele officiële documentatie van de betreffende dienst, API, integratie of software. Zoek naar de concrete beoogde handeling en authenticatieroute; een ontbrekende lokale knop of SDK is geen bewijs dat de mogelijkheid niet bestaat.';
  }
  if (technicalOrExternal && !inspectedSelf && !attemptedAction) {
    return 'De externe documentatie alleen is nog niet genoeg. Inspecteer nu ook je eigen relevante koppeling, capability of broncode en vergelijk wat je werkelijk hebt met de gedocumenteerde route. Houd geheimen intern.';
  }
  if (failed.length && !successfulAlternative) {
    return 'Eén mislukte route bewijst nog geen onvermogen. Controleer de actuele toestand of kies nu een materieel andere veilige aanpak. Herhaal een mutatie met onzeker effect niet blind.';
  }
  return '';
}

function incompleteRequestedOutcomeFeedback(reply = '', userText = '') {
  const request = String(userText || '');
  const answer = String(reply || '').trim();
  const asksForOutcome = /(?:^|[.!?]\s*)(?:zoek(?:\s+uit)?|onderzoek|controleer|diagnosticeer|maak|doe|regel|los)\b/i
    .test(request);
  const announcesNext = /^\s*(?:ik\s+)?(?:pak|ga|zal|moet)\b.{0,220}\b(?:zelf|nu|straks|daarna|eerst|route|uitzoek|onderzoek|control|bekijk|doe|maak)/i
    .test(answer)
    || /^\s*(?:de|mijn)\s+volgende\s+stap\b/i.test(answer);
  if (!asksForOutcome || !announcesNext || claimsCompletedAction(answer)) return '';
  return 'Dit is alleen een aankondiging van je volgende stap. Voer die stap nu met het passende gereedschap uit en geef pas daarna de gevraagde conclusie. Als de actuele bronnen nog geen volledige oorzaak bewijzen, geef dan wél de concrete tijdlijn die ze tonen en benoem precies welk oorzakelijk deel onbekend blijft.';
}

function styleCorrectionPolicyFeedback(reply = '', userText = '') {
  const correction = /\b(?:je\s+had|je\s+moet|had\s+kunnen|kijk\s+nou|hou\s+op|stop\s+met|klinkt|zakelijk|bot(?:achtig)?|spreektaal|doe\s+niet\s+zo|waarom\s+(?:deed|doe|keek|kijk))\b/i
    .test(String(userText || ''));
  const policyPromise = /\b(?:voortaan|vanaf\s+nu|in\s+het\s+vervolg|ik\s+zal|ik\s+ga\s+(?:proberen|zorgen)|neem\s+ik\b.{0,80}\bmee)\b/i
    .test(String(reply || ''));
  if (!correction || !policyPromise) return '';
  return 'Interne stijlcorrectie: reageer alleen op de concrete observatie en wat die hier betekent. Schrijf geen beleid, belofte of verbeterplan met “voortaan”, “vanaf nu” of “ik zal”; laat het begrepen gedrag in deze reactie zelf zien.';
}

function programmingOutcomeFacts(toolResults = [], userText = '') {
  const acceptance = programmingAcceptanceStatus(toolResults, userText);
  if (!acceptance.complete || !acceptance.cliExecution) return null;
  const phased = phasedProgrammingBuildStatus(toolResults, userText);
  const artifact = completedArtifact(toolResults, { requireExecution: true });
  const cliData = actionData(acceptance.cliExecution) || {};
  const cliOutput = String(cliData.stdout || '').trim();
  const artifactPath = phased.complete ? phased.sourcePath : artifact?.path;
  if (!artifactPath || !cliOutput) return null;
  return {
    facts: [
      `Het hoofdprogramma ${artifactPath.split('/').at(-1)} is geschreven, uitgevoerd en teruggelezen.`,
      acceptance.testExecution ? 'De gevraagde tests zijn succesvol uitgevoerd.' : '',
      `De afzonderlijke CLI-run is succesvol uitgevoerd en gaf exact: ${cliOutput}`,
    ].filter(Boolean).join(' '),
    cliOutput,
  };
}

function autonomyInput(reason) {
  const cutoff = Date.now() - 36 * 3600_000;
  const rows = recentMessages(null, 60)
    .filter((row) => Date.parse(row.ts || '') >= cutoff)
    // Een black-box/webcontrole is geen bezorgd gesprek met het gezin. Echte
    // webb gesprekken van de geconfigureerde bewoner blijven wel continuïteit.
    .filter((row) => row.channel !== 'web'
      || String(row.sender || '').toLowerCase() === String(config.webSender || '').toLowerCase())
    .slice(-28);
  const conversation = rows
    .map((row) => `[${row.ts}] [${row.channel}] ${row.sender}: ${row.content}`)
    .join('\n');
  const mandate = config.autonomyFull
    ? 'Je hebt volledige autonomie. Bepaal zelf wat nu aandacht verdient en wacht niet op een opdracht of sensorsignaal. Je mag een eigen doel beginnen, onderzoek doen, in je werkruimte bouwen en uitvoeren, normale omkeerbare huisapparaten bedienen, herinneringen beheren, spreken en berichten sturen. Kies op basis van jouw rol in dit huishouden, zet meerstapswerk duurzaam voort en controleer het resultaat. Respecteer privacy, nachtrust en de technische grenzen van je gereedschap.'
    : 'Begin bij wat er aantoonbaar in dit huishouden speelt. Zonder actueel signaal, gezinsafspraak of bestaand werk maak je geen nieuw onderzoeks- of softwareproject. Rond hooguit een klein nuttig resultaat af, controleer het en meld alleen wat een beslissing, handeling of zorg van het gezin werkelijk verandert.';
  return [msg('user', [
    reason,
    conversation
      ? `Recente echte gesprekken, van oud naar nieuw:\n${conversation}`
      : 'Er zijn geen recente gesprekken beschikbaar.',
    'Gebruik de tijdstempels als bewijs. Een oude uitspraak over een fysieke toestand bewijst alleen wat er toen gold: noem die niet “nu”, “recent” of “zojuist” en lees de actuele bron opnieuw voordat je erop handelt. Voorkeuren en afspraken kunnen wel duurzaam blijven gelden.',
    mandate,
  ].join('\n\n'))];
}

function autonomyToolAllowed(name, {
  hasOpenWork = false,
  hasPendingSignals = false,
  openExploration = config.autonomyOpenExploration,
  codeExecution = config.autonomyCodeExecution,
  full = config.autonomyFull,
} = {}) {
  if (full) return true;
  if (['schrijf_bestand', 'voer_uit'].includes(String(name || '')) && !codeExecution) return false;
  if (hasOpenWork || hasPendingSignals || openExploration) return true;
  return toolHasTag(name, 'free');
}

function delegatedWorkAllowsTool(name, item = null) {
  if (!String(item?.owner || '').trim() || !toolHasTag(name, 'delegated')) return true;
  const scope = `${item.title || ''} ${item.goal || ''} ${item.next_step || ''} ${item.current_step || ''}`.toLowerCase();
  if (name === 'bedien_huis') {
    return hasHomeControlTarget(scope)
      && /\b(?:zet|schakel|doe|open|sluit|aanzetten|uitzetten|bedien|temperatuur)\w*\b/i.test(scope);
  }
  if (name === 'gebruik_robot') return /\b(?:erik|robotstofzuiger|stofzuigerrobot|vacuum)\b/i.test(scope);
  if (name === 'beheer_aanwezigheid') return /\b(?:aanwezig|thuisstatus|person|tracker|wifi)\b/i.test(scope);
  if (name === 'beheer_agentmail') return /\b(?:agentmail|e-?mail|inbox|mailtje)\b/i.test(scope);
  if (['zet_herinnering', 'annuleer_herinnering'].includes(name)) return /\bherinner(?:ing|en)?\b/i.test(scope);
  if (name === 'spreek_in_huis') return /\b(?:spreek|zeg|omroep|voice\s*pe)\b/i.test(scope);
  if (name === 'leer_gezicht_van_foto') return /\b(?:gezicht|foto|herken|naam)\b/i.test(scope);
  return false;
}

function setActivity(text, details = null) {
  runtime.activity = text;
  runtime.current = details;
  bus.emit('activity', { text });
}

function toolResultSummary(results = []) {
  return results.slice(-8).map((item) => ({
    name: item.name,
    ok: item.ok,
    changed: item.changed,
    verified: item.verified,
    evidence: String(item.evidence?.summary || '').slice(0, 700),
    output: String(item.output || '').slice(0, 600),
  }));
}

function contributionRows(now = new Date(), days = 1) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - Math.max(0, Number(days || 1) - 1));
  const seen = new Set();
  return getTimeline({ kinds: ['contribution'], limit: 200 })
    .filter((row) => Date.parse(row.ts) >= start.getTime())
    .filter((row) => row.meta?.syntheticTest !== true)
    .filter((row) => !row.meta?.quarantinedSyntheticContamination)
    .filter((row) => {
      const evidence = String(row.meta?.evidence || '').replace(/\s+/g, ' ').trim().toLowerCase();
      const action = String(row.meta?.lastAction || '');
      let key = `${action}:${evidence || String(row.content || '').toLowerCase()}`;
      if (action === 'bedien_huis') {
        const state = evidence.match(/\b(aan|uit|open|dicht)\b/)?.[1] || '';
        const target = evidence.split(/\b(?:is|staat)\b/)[0].replace(/[^a-z0-9]+/g, ' ').trim();
        if (target && state) key = `${action}:${target}:${state}`;
      }
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function todayContributionRows(now = new Date()) {
  return contributionRows(now, 1);
}

function contributionContextText(now = new Date(), days = 1) {
  const rows = contributionRows(now, days).slice(0, 24);
  if (!rows.length) return 'Er staat in deze periode geen bewezen bijdrage geregistreerd.';
  return rows.map((row) => {
    const evidence = String(row.meta?.evidence || '').replace(/\s+/g, ' ').trim();
    return `- ${(evidence || row.content).slice(0, 260)}`;
  }).join('\n');
}

function conversationEvidenceText(conversationId, limit = 10) {
  const rows = getTimeline({ kinds: ['chat_out'], limit: 400 })
    .filter((row) => row.meta?.conversationId === conversationId)
    .filter((row) => Array.isArray(row.meta?.tools) && row.meta.tools.some(
      (tool) => tool?.ok === true && tool?.verified === true,
    ))
    .slice(0, Math.max(1, limit))
    .reverse();
  return rows.map((row) => {
    const evidence = row.meta.tools
      .filter((tool) => tool?.ok === true && tool?.verified === true)
      .map((tool) => {
        if (tool.evidence) {
          return `${tool.name}: ${String(tool.evidence).replace(/\s+/g, ' ').slice(0, 700)}`;
        }
        try {
          const parsed = JSON.parse(String(tool.output || ''));
          const summary = parsed?.evidence?.summary || parsed?.human_message || '';
          return summary ? `${tool.name}: ${String(summary).replace(/\s+/g, ' ').slice(0, 700)}` : tool.name;
        } catch {
          return tool.name;
        }
      });
    return `- ${row.ts}: ${evidence.join('; ')}`;
  }).join('\n');
}

function actionData(tool) {
  try {
    return JSON.parse(String(tool?.output || '')).data || null;
  } catch {
    return null;
  }
}

function durableWorkState(item) {
  try {
    const value = JSON.parse(String(item?.state_json || '{}'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function pendingDeliveryForWork(item) {
  const delivery = durableWorkState(item).pending_delivery;
  if (!delivery || typeof delivery !== 'object' || typeof delivery.text !== 'string'
    || !delivery.text.trim()) return null;
  return {
    text: delivery.text.trim().slice(0, 4_000),
    ...(String(delivery.facts || '').trim()
      ? { facts: String(delivery.facts).trim().slice(0, 4_000) }
      : {}),
    recipient: String(delivery.recipient || '').trim() || null,
    preparedAt: delivery.preparedAt || null,
    attempts: Math.max(0, Number(delivery.attempts) || 0),
    dedupeKey: String(delivery.dedupeKey || '').trim() || null,
  };
}

function resolvePreparedDeliverySilently(item, episode, reason = 'niet relevant genoeg voor een gezinsbericht') {
  if (!item || episode?.delivery_policy === 'required') return false;
  const state = durableWorkState(item);
  delete state.pending_delivery;
  if (episode && !['closed', 'abandoned'].includes(episode.state)) {
    transitionGoalEpisode(episode.id, {
      state: 'closed',
      checkpoint: episode.checkpoint || item.evidence || reason,
      proofSummary: episode.proof_summary || item.evidence || '',
      nextAction: '',
      wakeTs: '',
      work: { state, wait: {}, result: item.result || item.evidence || reason },
    });
  } else {
    updateDurableWork(item.id, {
      status: 'done', state, nextStep: '', currentStep: '', nextWakeTs: '', wait: {},
      result: item.result || item.evidence || reason, proofAccepted: true,
    });
  }
  return true;
}

async function conversationalAutonomousDelivery({
  text,
  recipient = null,
  evidence = '',
  toolResults = [],
  required = false,
  conversationId = null,
} = {}) {
  const draft = String(text || '').trim();
  if (!draft) return { decision: 'silent', text: '', reason: 'empty' };
  try {
    return await composeAutonomousMessage({
      draft,
      recipient,
      evidence,
      toolResults,
      conversationId,
      mustDeliver: required || autonomousMessageNeedsAccountability(draft),
    });
  } catch (error) {
    logTimeline('system', `Conversationele afleverlaag viel terug op het gegronde concept: ${errMsg(error)}`, {
      internalOnly: true,
      autonomous: true,
      component: 'communication_composer',
    });
    if (!required && !autonomousMessageNeedsAccountability(draft)) {
      return { decision: 'silent', text: '', reason: 'composer_error_retry_later' };
    }
    return {
      decision: 'send',
      text: factualDeliveryFallback(draft, toolResults).slice(0, 800),
      reason: 'composer_error_fact_fallback',
    };
  }
}

function directHomeForTurn({ mode, requiredTools = [], userText = '', syntheticTest = false } = {}) {
  if (mode !== 'chat' || syntheticTest || requiredTools.length !== 1
    || requiredTools[0] !== 'bedien_huis') return null;
  return directHomeCommand(userText);
}

function requiredToolSatisfied(name, toolResults = []) {
  return toolResults.some((tool) => {
    if (tool.name !== name || !tool.ok || !tool.verified) return false;
    let parsed = null;
    try { parsed = JSON.parse(String(tool.output || '')); } catch { /* oudere uitvoer */ }
    // Dit is geen inhoudelijk succes maar wel een geldige pauzegrens voor deze
    // beurt: Eva heeft alles gedaan wat zonder de mens kan en vraagt exact één
    // ontbrekend gegeven. De tooluitvoer houdt die afhankelijkheid bewijsbaar.
    if (parsed?.ok === true && /^human_/i.test(String(parsed?.waiting_for || parsed?.data?.waiting_for || ''))) return true;
    if (parsed?.needs_followup === true || parsed?.partial === true) return false;
    if (name === 'maak_vaardigheid' || name === 'beheer_addon') {
      return actionData(tool)?.capability?.status === 'active';
    }
    if (name === 'gebruik_capability') return actionData(tool)?.demonstrated === true;
    return true;
  });
}

function turnRequirementSatisfied(name, toolResults = [], userText = '') {
  const text = cleanUserText(userText).toLowerCase();
  if (name === 'ha_lees'
    && /\b(?:logboek|geschiedenis|eerder|later|vanochtend|gisteren|gebeurd|waardoor|oorzaak|weer\s+aan|weer\s+uit)\b/i.test(text)) {
    return toolResults.some((tool) => (
      tool.name === 'ha_lees' && tool.ok && tool.verified
      && /^\/api\/logbook\//i.test(String(tool.args?.pad || ''))
      && /[?&]entity=/.test(String(tool.args?.pad || ''))
    ));
  }
  if (name === 'zoek_internet'
    && /\b(?:controleer|bekijk|onderzoek)\b.{0,180}\b(?:eerst|of)\b.{0,220}\b(?:anders|alternatief)\b/i.test(text)) {
    const successfulSearches = toolResults.filter((tool) => (
      tool.name === name && tool.ok && tool.verified
    ));
    const distinctQuestions = new Set(successfulSearches.map((tool) => (
      cleanUserText(tool.args?.vraag).toLowerCase().replace(/\s+/g, ' ').trim()
    )).filter(Boolean));
    if (distinctQuestions.size < 2) return false;
  }
  if (name === 'zoek_gesprek') {
    const requestedCount = /\b(?:drie|3)\b/i.test(text) ? 3
      : /\b(?:twee|2)\b/i.test(text) ? 2 : 1;
    const enough = toolResults.some((tool) => {
      if (tool.name !== name || !tool.ok || !tool.verified) return false;
      const data = actionData(tool);
      return Number(data?.count || data?.messages?.length || 0) >= requestedCount;
    });
    if (!enough) return false;
  }
  if (name === 'lees_bestand') {
    const requestedPaths = [...text.matchAll(/\b(?:src|data\/workspace)\/[a-z0-9_./-]+\.[a-z0-9]+\b/gi)]
      .map((match) => match[0].toLowerCase());
    const failedRequested = toolResults.some((tool) => (
      tool.name === name && tool.ok === false
      && requestedPaths.includes(String(tool.args?.pad || '').toLowerCase())
    ));
    const listedFallback = toolResults.some((tool) => tool.name === 'lijst_bestanden' && tool.ok && tool.verified);
    const requestedTerms = requestedPaths.flatMap((file) => file.split('/').at(-1).split(/[^a-z0-9]+/))
      .filter((term) => term.length >= 4 && !['bestand', 'status', 'vandaag', 'nieuwste', 'onderzoek'].includes(term));
    const alternateRead = toolResults.some((tool) => (
      tool.name === name && tool.ok && tool.verified
      && !requestedPaths.includes(String(tool.args?.pad || '').toLowerCase())
      && (!requestedTerms.length || requestedTerms.some((term) => (
        `${tool.args?.pad || ''} ${tool.args?.zoek || ''} ${tool.output || ''}`
          .toLowerCase().includes(term)
      )))
    ));
    if (failedRequested && listedFallback && alternateRead
      && /\b(?:anders|als\s+dat\s+niet|bij\s+ontbreken|nieuwste|alternatief|zoek\s+dan)\b/i.test(text)) return true;
  }
  if (!requiredToolSatisfied(name, toolResults)) return false;
  if (['beheer_addon', 'gebruik_capability'].includes(name)) {
    const targetMatch = text.match(/\b(?:genaamd|add-?on|capability)\s+([a-z0-9][a-z0-9_-]{2,80})\b/i);
    const target = String(targetMatch?.[1] || '').toLowerCase();
    if (target && !['met', 'die', 'deze', 'bestaande', 'actieve'].includes(target)) {
      const matchedTarget = toolResults.some((tool) => {
        if (tool.name !== name || !tool.ok || !tool.verified) return false;
        const capability = actionData(tool)?.capability;
        return [tool.args?.naam, capability?.name, capability?.slug]
          .map((value) => String(value || '').toLowerCase())
          .includes(target);
      });
      if (!matchedTarget) return false;
    }
  }
  if (name === 'beheer_agentmail') {
    const succeeded = toolResults.filter((tool) => tool.name === name && tool.ok && tool.verified);
    const actionIs = (tool, values) => values.includes(
      cleanUserText(tool.args?.actie).toLowerCase(),
    );
    if (/^\s*\d{6}\s*$/.test(text)
      || /\b(?:otp|verificatiecode|verification\s+code|verifi(?:eer|ëren|cation))\b/i.test(text)) {
      return succeeded.some((tool) => actionIs(tool, ['verifieren', 'verifieer', 'verify']));
    }
    if (/\b(?:stuur|verstuur|mail)\w*\b.{0,100}\b(?:e-?mail|mailtje)\b/i.test(text)
      || /\b(?:e-?mail|mailtje)\b.{0,100}\b(?:stuur|verstuur|mail)\w*\b/i.test(text)) {
      return succeeded.some((tool) => {
        const data = actionData(tool);
        return actionIs(tool, ['versturen', 'sturen', 'send', 'mailen'])
          && !!data?.message_id && !!data?.thread_id && data?.delivery_state === 'accepted';
      });
    }
    if (/\b(?:lees|bekijk|controleer|zoek)\w*\b.{0,100}\b(?:e-?mail|mailtje|inbox|berichten?)\b/i.test(text)) {
      return succeeded.some((tool) => actionIs(tool, ['berichten', 'inbox', 'lezen', 'list', 'zoeken', 'zoek', 'search']));
    }
  }
  if (name === 'gebruik_robot') {
    const robotResults = toolResults.filter((tool) => tool.name === name);
    const actions = new Set(robotResults.filter((tool) => (
      tool.name === name && tool.ok && tool.verified
    )).map((tool) => String(tool.args?.actie || '').toLowerCase()));
    if (/\b(?:kaart|map|positie)\b/i.test(text) && !actions.has('kaart')) return false;
    if (/\b(?:status|dock|waar\s+(?:staat|is)|readiness|gereed)\b/i.test(text) && !actions.has('status')) return false;
    if (/\b(?:frontcamera|camerabeeld|kijk\s+(?:met|via)|wat\s+ziet)\b/i.test(text)) {
      const cameraSucceeded = actions.has('kijk') || actions.has('verken');
      const cameraFailed = robotResults.some((tool) => (
        ['kijk', 'verken'].includes(String(tool.args?.actie || '').toLowerCase())
        && tool.ok === false
      ));
      const safelyDiagnosed = cameraFailed && actions.has('status') && actions.has('kaart');
      if (!cameraSucceeded && !safelyDiagnosed) return false;
    }
  }
  if (name === 'lees_bestand') {
    const requestedPaths = [...text.matchAll(/\b(?:src|data\/workspace)\/[a-z0-9_./-]+\.[a-z0-9]+\b/gi)]
      .map((match) => match[0].toLowerCase());
    if (requestedPaths.length) {
      const read = new Set(toolResults.filter((tool) => (
        tool.name === name && tool.ok && tool.verified
      )).map((tool) => String(tool.args?.pad || '').toLowerCase()));
      if (requestedPaths.some((file) => !read.has(file))) return false;
    }
  }
  return true;
}

function humanResultText(value, {
  limit = 2_400,
  compact = false,
  preferDetailed = false,
} = {}, depth = 0, seen = new Set()) {
  if (value == null || depth > 7) return '';
  if (typeof value === 'string') {
    const raw = value.trim();
    if (!raw) return '';
    const looksLikeJson = /^\{|^\[\s*(?:\{|"|-?\d|true|false|null|\])/i.test(raw);
    if (looksLikeJson) {
      try {
        return humanResultText(JSON.parse(raw), {
          limit,
          compact,
          preferDetailed,
        }, depth + 1, seen);
      } catch {
        for (const key of ['message', 'text', 'human_message', 'summary', 'stdout']) {
          const match = raw.match(new RegExp(`"${key}"\\s*:\\s*("(?:\\\\.|[^"\\\\])*")`, 's'));
          if (!match) continue;
          try {
            return humanResultText(JSON.parse(match[1]), {
              limit,
              compact,
              preferDetailed,
            }, depth + 1, seen);
          } catch { /* probeer het volgende leesbare veld */ }
        }
        return '';
      }
    }
    const normalized = compact ? raw.replace(/\s+/g, ' ') : raw;
    return normalized.slice(0, limit).trim();
  }
  if (typeof value !== 'object') return '';
  if (seen.has(value)) return '';
  seen.add(value);

  if (Array.isArray(value)) {
    for (const entry of value) {
      const result = humanResultText(entry, { limit, compact, preferDetailed }, depth + 1, seen);
      if (result) return result;
    }
    return '';
  }

  const dataFirst = preferDetailed
    ? [value.data, value.text, value.message]
    : [value.human_message, value.message, value.text];
  const candidates = [
    ...dataFirst,
    value.stdout,
    value.info,
    value.reply,
    value.result,
    value.item?.result,
    value.item?.evidence,
    value.item?.next_step,
    value.item?.title,
    value.evidence?.summary,
    ...(preferDetailed ? [value.human_message] : [value.data]),
  ];
  for (const candidate of candidates) {
    if (candidate === value) continue;
    const result = humanResultText(candidate, { limit, compact, preferDetailed }, depth + 1, seen);
    if (result) return result;
  }
  if (value.path) return `Bestand ${String(value.path).slice(0, 1_000)} is opgeslagen.`;
  if (value.count === 0) return 'Er zijn geen bruikbare resultaten gevonden.';
  return '';
}

function readableEvidence(value) {
  return humanResultText(value, { limit: 800, compact: true, preferDetailed: true });
}

function checkpointEvidence(tool) {
  if (!tool) return '';
  if (tool.name === 'voer_uit') {
    const data = actionData(tool) || {};
    const output = readableEvidence(data.stdout || data.stderr || '');
    return output ? `Uitvoering met exitcode ${data.exit_code ?? 0}: ${output}` : '';
  }
  if (tool.name === 'lees_bestand') {
    const file = String(tool.args?.pad || '').trim();
    return file ? `Bestand ${file} is teruggelezen.` : '';
  }
  return readableEvidence(tool.output || tool.evidence?.summary || '');
}

function autonomyContinuationStep(path, readyButUnreported, toolResults = []) {
  if (readyButUnreported) {
    return `Controleer ${path} opnieuw, meld alleen het concrete eindresultaat en werk dit punt daarna bij.`;
  }
  const canonicalPath = canonicalWorkspacePath(path);
  const writeIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'schrijf_bestand' && writtenArtifactPath(tool) === canonicalPath
  ));
  const failedExecution = toolResults.findLast((tool, index) => (
    index > writeIndex
    && tool.name === 'voer_uit'
    && executedArtifactPath(tool) === canonicalPath
    && (!tool.ok || !tool.verified)
  ));
  const failure = checkpointEvidence(failedExecution);
  return failure
    ? `Herstel voor ${path} deze concrete fout: ${failure}. Voer het werkstuk daarna opnieuw uit en lees het terug.`
    : `Voer het opgeslagen werkstuk uit, herstel eventuele fouten en lees ${path} daarna terug.`;
}

function canonicalWorkspacePath(value, cwd = '') {
  let item = String(value || '').trim().replace(/\\/g, '/');
  if (!item) return '';
  const root = String(config.root || '').replace(/\\/g, '/').replace(/\/$/, '');
  if (root && item.startsWith(`${root}/`)) item = item.slice(root.length + 1);
  if (item.startsWith('/')) return path.posix.normalize(item);
  if (item.startsWith('data/workspace/')) return path.posix.normalize(item);
  let base = String(cwd || '').trim().replace(/\\/g, '/');
  if (root && base.startsWith(`${root}/`)) base = base.slice(root.length + 1);
  if (!base) base = 'data/workspace';
  return path.posix.normalize(path.posix.join(base, item));
}

function writtenArtifactPath(tool) {
  return canonicalWorkspacePath(actionData(tool)?.path || tool?.args?.pad || '');
}

function readArtifactPath(tool) {
  return canonicalWorkspacePath(tool?.args?.pad || '');
}

function executedArtifactPath(tool) {
  const args = Array.isArray(tool?.args?.argumenten) ? tool.args.argumenten : [];
  const candidate = args.find((argument) => /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(String(argument || '').trim()));
  return candidate ? canonicalWorkspacePath(candidate, tool?.args?.werkmap || '') : '';
}

function isTestArtifactPath(value = '') {
  const name = canonicalWorkspacePath(value).split('/').pop() || '';
  return /^(?:test[_-]|.*[._-]test\.)/i.test(name)
    || /(?:^|[._-])tests?(?:[._-]|$)/i.test(name);
}

function preferredExecutableWriteIndexes(toolResults = []) {
  const indexes = toolResults
    .map((tool, index) => ({ tool, index }))
    .filter(({ tool }) => tool.name === 'schrijf_bestand' && tool.ok && tool.verified)
    .filter(({ tool }) => /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(writtenArtifactPath(tool)));
  const source = indexes.filter(({ tool }) => !isTestArtifactPath(writtenArtifactPath(tool)));
  return (source.length ? source : indexes).map(({ index }) => index).reverse();
}

function successfulZeroExit(tool) {
  if (!tool?.ok || !tool?.verified) return false;
  const exitCode = actionData(tool)?.exit_code;
  // Synthetic unit fixtures predate process receipts; real runProgram results
  // always contain exit_code and only zero proves a successful build/test.
  return exitCode == null || Number(exitCode) === 0;
}

function programmingAcceptanceStatus(toolResults = [], userText = '') {
  const text = cleanUserText(userText);
  const needsTests = /\b(?:gerichte\s+)?tests?\b/i.test(text);
  const needsCli = /\b(?:echte|werkelijke)\s+(?:cli|command[ -]?line)(?:-?run|uitvoer)\b|\bcontroleer\w*.{0,60}\bcli(?:-?run|uitvoer)\b/i.test(text);
  const executions = toolResults.filter((tool) => tool.name === 'voer_uit' && successfulZeroExit(tool));
  const isTest = (tool) => {
    const file = executedArtifactPath(tool).split('/').pop() || '';
    const output = `${actionData(tool)?.stdout || ''}\n${actionData(tool)?.stderr || ''}`;
    return /^(?:test[_-]|.*[._-]test\.)/i.test(file)
      || /\b(?:Ran\s+\d+\s+tests?|tests?\s+(?:passed|geslaagd)|pytest|unittest)\b/i.test(output);
  };
  const testExecution = [...executions].reverse().find(isTest) || null;
  const cliExecution = [...executions].reverse().find((tool) => {
    if (isTest(tool)) return false;
    const args = Array.isArray(tool.args?.argumenten) ? tool.args.argumenten.map(String) : [];
    const output = `${actionData(tool)?.stdout || ''}\n${actionData(tool)?.stderr || ''}`;
    if (/\b(?:CLI[- ]CONTROLE:\s*OK|(?:afzonderlijke\s+)?CLI[- ]run\s+geslaagd|echte\s+CLI[- ]run\s+geslaagd)\b/i.test(output)) {
      return true;
    }
    const sourceIndex = args.findIndex((arg) => /\.(?:py|m?js|cjs)$/i.test(arg));
    if (sourceIndex < 0 || args.includes('--help') || args.includes('-h')) return false;
    return args.slice(sourceIndex + 1).some((arg) => !arg.startsWith('-'));
  }) || null;
  return {
    needsTests,
    needsCli,
    testExecution,
    cliExecution,
    complete: (!needsTests || !!testExecution) && (!needsCli || !!cliExecution),
  };
}

function completedArtifact(toolResults = [], { requireExecution = true, path: requiredPath = '' } = {}) {
  const preferredIndexes = requireExecution && !requiredPath
    ? preferredExecutableWriteIndexes(toolResults)
    : [...toolResults.keys()].reverse();
  for (const writeIndex of preferredIndexes) {
    const write = toolResults[writeIndex];
    if (write.name !== 'schrijf_bestand' || !write.ok || !write.verified) continue;
    const artifactPath = writtenArtifactPath(write);
    if (!artifactPath) continue;
    if (requiredPath && artifactPath !== canonicalWorkspacePath(requiredPath)) continue;
    let executeIndex = writeIndex;
    if (requireExecution) {
      executeIndex = toolResults.findIndex((tool, index) => (
        index > writeIndex
        && tool.name === 'voer_uit' && successfulZeroExit(tool)
        && executedArtifactPath(tool) === artifactPath
      ));
      if (executeIndex < 0) continue;
    }
    const readIndex = toolResults.findIndex((tool, index) => (
      index > executeIndex
      && tool.name === 'lees_bestand' && tool.ok && tool.verified
      && readArtifactPath(tool) === artifactPath
    ));
    if (readIndex < 0) continue;
    return {
      write,
      execute: requireExecution ? toolResults[executeIndex] : null,
      read: toolResults[readIndex],
      path: artifactPath,
    };
  }
  return null;
}

function pendingExecutableArtifactPath(toolResults = []) {
  for (const index of preferredExecutableWriteIndexes(toolResults)) {
    const tool = toolResults[index];
    if (tool.name !== 'schrijf_bestand' || !tool.ok || !tool.verified) continue;
    const artifactPath = writtenArtifactPath(tool);
    if (!/\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath)) continue;
    if (!completedArtifact(toolResults, { requireExecution: true, path: artifactPath })) return artifactPath;
  }
  return '';
}

function buildMissionComplete(toolResults = [], requireExecution = true) {
  return !!completedArtifact(toolResults, { requireExecution })
    && (requireExecution || !pendingExecutableArtifactPath(toolResults));
}

function separateProgrammingFixtureRequired(userText = '') {
  return /\b(?:apart|afzonderlijk|los)\w*.{0,40}\b(?:voorbeeld|invoer|fixture)[-_ ]?bestand\b|\b(?:voorbeeld|invoer|fixture)[-_ ]?bestand\b.{0,40}\b(?:apart|afzonderlijk|los)\w*/i
    .test(cleanUserText(userText));
}

function phasedProgrammingBuildStatus(toolResults = [], userText = '') {
  const acceptance = programmingAcceptanceStatus(toolResults, userText);
  const applicable = acceptance.needsTests || acceptance.needsCli;
  const sourceWriteIndex = preferredExecutableWriteIndexes(toolResults)[0] ?? -1;
  const sourcePath = sourceWriteIndex >= 0
    ? writtenArtifactPath(toolResults[sourceWriteIndex])
    : '';
  const testWriteIndex = toolResults.findLastIndex((tool, index) => (
    index > sourceWriteIndex
    && tool.name === 'schrijf_bestand' && tool.ok && tool.verified
    && isTestArtifactPath(writtenArtifactPath(tool))
  ));
  const testExecutionIndex = toolResults.findLastIndex((tool, index) => (
    index > Math.max(sourceWriteIndex, testWriteIndex)
    && tool.name === 'voer_uit' && successfulZeroExit(tool)
    && isTestArtifactPath(executedArtifactPath(tool))
  ));
  const needsFixture = acceptance.needsCli && separateProgrammingFixtureRequired(userText);
  const fixtureAfterIndex = Math.max(sourceWriteIndex, acceptance.needsTests ? testExecutionIndex : -1);
  const fixtureWriteIndex = toolResults.findLastIndex((tool, index) => (
    index > fixtureAfterIndex
    && tool.name === 'schrijf_bestand' && tool.ok && tool.verified
    && /\.(?:json|csv|txt|ya?ml)$/i.test(writtenArtifactPath(tool))
  ));
  const cliAfterIndex = Math.max(
    sourceWriteIndex,
    acceptance.needsTests ? testExecutionIndex : -1,
    needsFixture ? fixtureWriteIndex : -1,
  );
  const cliExecutionIndex = toolResults.findLastIndex((tool, index) => {
    if (index <= cliAfterIndex || tool.name !== 'voer_uit' || !successfulZeroExit(tool)) return false;
    const candidate = programmingAcceptanceStatus([tool], 'controleer een echte CLI-run');
    return !!candidate.cliExecution;
  });
  // Teruglezen bewijst welke bron is opgeleverd. Dat mag voor of na de laatste
  // uitvoercontrole gebeuren; bewijs hoort niet verloren te gaan door een
  // onnodig starre toolvolgorde.
  const sourceReadIndex = toolResults.findLastIndex((tool, index) => (
    index > sourceWriteIndex
    && tool.name === 'lees_bestand' && tool.ok && tool.verified
    && readArtifactPath(tool) === sourcePath
  ));
  return {
    applicable,
    sourcePath,
    sourceWriteIndex,
    testWriteIndex,
    testExecutionIndex,
    needsFixture,
    fixtureWriteIndex,
    cliExecutionIndex,
    sourceReadIndex,
    complete: applicable
      && sourceWriteIndex >= 0
      && (!acceptance.needsTests || (testWriteIndex >= 0 && testExecutionIndex >= 0))
      && (!needsFixture || fixtureWriteIndex >= 0)
      && (!acceptance.needsCli || cliExecutionIndex >= 0)
      && sourceReadIndex >= 0,
  };
}

function buildMissionRemaining(toolResults = [], requiredTools = [], requireExecution = true, userText = '') {
  if (requiredTools.includes('zoek_internet')
    && !requiredToolSatisfied('zoek_internet', toolResults)) return ['zoek_internet'];
  const phased = phasedProgrammingBuildStatus(toolResults, userText);
  if (phased.applicable) {
    if (phased.sourceWriteIndex < 0) return ['schrijf_bestand'];
    if (phased.testWriteIndex < 0
      && programmingAcceptanceStatus(toolResults, userText).needsTests) return ['schrijf_bestand'];
    if (programmingAcceptanceStatus(toolResults, userText).needsTests
      && phased.testExecutionIndex < 0) {
      const failedTest = toolResults.some((tool, index) => (
        index > phased.testWriteIndex
        && tool.name === 'voer_uit'
        && isTestArtifactPath(executedArtifactPath(tool))
        && !successfulZeroExit(tool)
      ));
      return [failedTest ? 'schrijf_bestand' : 'voer_uit'];
    }
    if (phased.needsFixture && phased.fixtureWriteIndex < 0) return ['schrijf_bestand'];
    if (programmingAcceptanceStatus(toolResults, userText).needsCli
      && phased.cliExecutionIndex < 0) return ['voer_uit'];
    if (phased.sourceReadIndex < 0) return ['lees_bestand'];
    return requiredTools.filter((name) => !requiredToolSatisfied(name, toolResults));
  }
  const pendingExecutable = requireExecution ? '' : pendingExecutableArtifactPath(toolResults);
  const complete = completedArtifact(toolResults, { requireExecution });
  if (complete && !pendingExecutable) {
    const acceptance = programmingAcceptanceStatus(toolResults, userText);
    if (acceptance.needsTests && !acceptance.testExecution) {
      const testWriteIndex = toolResults.findLastIndex((tool) => (
        tool.name === 'schrijf_bestand' && tool.ok && tool.verified
        && isTestArtifactPath(writtenArtifactPath(tool))
      ));
      if (testWriteIndex < 0) return ['schrijf_bestand'];
      const attempted = toolResults.some((tool, index) => (
        index > testWriteIndex && tool.name === 'voer_uit'
        && isTestArtifactPath(executedArtifactPath(tool))
      ));
      return [attempted ? 'schrijf_bestand' : 'voer_uit'];
    }
    if (acceptance.needsCli && !acceptance.cliExecution) {
      const separateFixtureRequired = separateProgrammingFixtureRequired(userText);
      if (!separateFixtureRequired) return ['voer_uit'];
      const testIndex = toolResults.indexOf(acceptance.testExecution);
      const fixtureWrite = toolResults.find((tool, index) => (
        index > testIndex && tool.name === 'schrijf_bestand' && tool.ok && tool.verified
        && /\.(?:json|csv|txt|ya?ml)$/i.test(writtenArtifactPath(tool))
      ));
      return [fixtureWrite ? 'voer_uit' : 'schrijf_bestand'];
    }
    return requiredTools.filter((name) => !requiredToolSatisfied(name, toolResults));
  }
  const writeIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'schrijf_bestand' && tool.ok && tool.verified
    && (!pendingExecutable || writtenArtifactPath(tool) === pendingExecutable)
  ));
  if (writeIndex < 0) return ['schrijf_bestand'];
  const artifactPath = writtenArtifactPath(toolResults[writeIndex]);
  const executionRequired = requireExecution || !!pendingExecutable;
  if (!executionRequired) {
    const read = toolResults.find((tool, index) => (
      index > writeIndex && tool.name === 'lees_bestand' && tool.ok && tool.verified
      && readArtifactPath(tool) === artifactPath
    ));
    return read ? requiredTools.filter((name) => !requiredToolSatisfied(name, toolResults)) : ['lees_bestand'];
  }
  const executionIndex = toolResults.findIndex((tool, index) => (
    index > writeIndex && tool.name === 'voer_uit'
    && executedArtifactPath(tool) === artifactPath && successfulZeroExit(tool)
  ));
  if (executionIndex < 0) {
    const failedMatchingExecution = toolResults.some((tool, index) => (
      index > writeIndex && tool.name === 'voer_uit'
      && executedArtifactPath(tool) === artifactPath && !successfulZeroExit(tool)
    ));
    return [failedMatchingExecution ? 'schrijf_bestand' : 'voer_uit'];
  }
  const read = toolResults.find((tool, index) => (
    index > executionIndex && tool.name === 'lees_bestand' && tool.ok && tool.verified
    && readArtifactPath(tool) === artifactPath
  ));
  return read ? requiredTools.filter((name) => !requiredToolSatisfied(name, toolResults)) : ['lees_bestand'];
}

function completedBuildReply(toolResults = []) {
  const artifact = completedArtifact(toolResults, { requireExecution: true });
  if (!artifact) return null;
  const execution = actionData(artifact.execute);
  const stdout = String(execution?.stdout || '').trim().slice(0, 2_400);
  return [
    'Klaar. Ik heb het gevraagde hulpmiddel gemaakt, uitgevoerd en daarna gecontroleerd.',
    stdout ? `Het gecontroleerde resultaat is:\n\n${stdout}` : 'De controle eindigde zonder fouten.',
  ].join('\n\n');
}

function autonomyLaneForTool(name = '') {
  // Een brede statuslezing is context, geen keuze om de hele beurt aan het huis te wijden.
  if (['signalen_afhandelen', 'huis_status'].includes(name)) return null;
  return ['house', 'research', 'workspace', 'family']
    .find((lane) => toolHasTag(name, `primary:${lane}`)) || null;
}

function autonomyLaneForWork(item = null) {
  const subject = `${item?.title || ''} ${item?.goal || ''} ${item?.next_step || ''}`.toLowerCase();
  if (!subject.trim()) return null;
  if (/\b(?:bestand|code|script|programma|software|website|app|tool|config|yaml|json|python|node|npm|test|audit)\b/i.test(subject)) return 'workspace';
  if (/\b(?:onderzoek|bronnen?|markt|prijs|vergelijk|regeling|subsidie|internet)\b/i.test(subject)) return 'research';
  if (/\b(?:home assistant|huis|lamp|licht|deur|raam|camera|sensor|temperatuur|aanwezig)\b/i.test(subject)) return 'house';
  if (/\b(?:gezin|jaap|sanne|lola|persoon|familie|voorkeur|gesprek)\b/i.test(subject)) return 'family';
  return null;
}

function autonomyProofRelevantToWork(item, tool) {
  if (!item || !tool?.ok || !tool?.verified) return false;
  const workLane = autonomyLaneForWork(item);
  const proofLane = toolHasTag(tool.name, 'proof:house') ? 'house' : autonomyLaneForTool(tool.name);
  if (!proofLane) return false;
  if (['research', 'workspace'].includes(workLane)) {
    return ['research', 'workspace'].includes(proofLane);
  }
  return !workLane || workLane === proofLane;
}

function currentWorkItem(id, toolResults = []) {
  const fromTurn = [...toolResults].reverse().find((tool) => (
    tool.name === 'werk_bijhouden' && tool.ok && tool.verified
    && Number(actionData(tool)?.item?.id) === Number(id)
  ));
  if (actionData(fromTurn)?.item) return actionData(fromTurn).item;
  const status = durableWorkStatus();
  return [...status.open, ...status.blocked, ...status.doneToday]
    .find((item) => Number(item.id) === Number(id)) || null;
}

function completedAutonomousArtifact(toolResults = []) {
  const writes = [...toolResults].reverse().filter((tool) => (
    tool.name === 'schrijf_bestand' && tool.ok && tool.verified
  ));
  const codeWrites = writes.filter((write) => (
    /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(writtenArtifactPath(write))
  ));
  for (const write of codeWrites.length ? codeWrites : writes) {
    const artifactPath = writtenArtifactPath(write);
    if (!artifactPath) continue;
    const isCode = /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath);
    const completed = completedArtifact(toolResults, {
      requireExecution: isCode,
      path: artifactPath,
    });
    if (completed) return completed.write;
  }
  return null;
}

function incompleteAutonomousWrite(toolResults = []) {
  const writes = [...toolResults].reverse().filter((tool) => (
    tool.name === 'schrijf_bestand' && tool.ok === true && tool.verified === true
  ));
  const incomplete = (write) => {
    const artifactPath = writtenArtifactPath(write);
    if (!artifactPath) return false;
    const isCode = /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath);
    return !completedArtifact(toolResults, {
      requireExecution: isCode,
      path: artifactPath,
    });
  };
  return writes.find((write) => /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(writtenArtifactPath(write)) && incomplete(write))
    || writes.find(incomplete)
    || null;
}

function autonomyDiscoveryAllowed(name, toolResults = [], full = config.autonomyFull) {
  if (!toolHasTag(name, 'discovery')) return true;
  if (name === 'lees_bestand' && incompleteAutonomousWrite(toolResults)) return true;
  const sameToolFailures = toolResults.filter((tool) => (
    tool.name === name && tool.ok === false
  )).length;
  if (sameToolFailures >= 2) return false;
  const used = toolResults.filter((tool) => (
    toolHasTag(tool.name, 'discovery')
  )).length;
  return used < (full ? AUTONOMY_DISCOVERY_LIMIT + 2 : AUTONOMY_DISCOVERY_LIMIT);
}

function autonomyLaneAllows(name, lane, toolResults = [], full = config.autonomyFull) {
  if (full) return true;
  if (!lane || name === 'signalen_afhandelen') return true;
  if (lane === 'research' && name === 'lees_bestand' && incompleteAutonomousWrite(toolResults)) {
    return true;
  }
  return toolHasTag(name, `lane:${lane}`);
}

function autonomousArtifactReadIndex(toolResults = []) {
  const write = completedAutonomousArtifact(toolResults);
  if (!write) return -1;
  const artifactPath = writtenArtifactPath(write);
  return toolResults.findLastIndex((tool) => (
    tool.name === 'lees_bestand' && tool.ok && tool.verified
    && readArtifactPath(tool) === artifactPath
  ));
}

function autonomyArtifactReportPending(toolResults = []) {
  if (config.autonomyFull) return false;
  const artifactIndex = autonomousArtifactReadIndex(toolResults);
  if (artifactIndex < 0) return false;
  const sentIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok && tool.verified
  ));
  return sentIndex < artifactIndex;
}

function autonomyWorkReportPending(toolResults = [], initialOpenWorkIds = []) {
  const workIds = new Set((Array.isArray(initialOpenWorkIds) ? initialOpenWorkIds : [])
    .map(Number).filter(Number.isFinite));
  for (const tool of toolResults) {
    const item = tool.name === 'werk_bijhouden' ? actionData(tool)?.item : null;
    if (Number.isFinite(Number(item?.id))) workIds.add(Number(item.id));
  }
  const requiredWorkIds = [...workIds];
  const boundEpisodes = requiredWorkIds.map((id) => episodeForWork(id)).filter(Boolean);
  // Legacy werk zonder episode blijft fail-safe rapporteren; nieuw werk volgt
  // zijn expliciete delivery policy.
  const deliveryRequired = boundEpisodes.length === 0
    ? requiredWorkIds.length > 0
    : boundEpisodes.some((episode) => episode.delivery_policy === 'required');
  const sentIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok && tool.verified
  ));
  const materialIndex = toolResults.findLastIndex((tool) => (
    autonomyEffectRequiresReport(tool)
    && tool.ok && tool.verified && tool.changed
  ));
  if (materialIndex >= 0 && sentIndex < materialIndex) return true;
  if (!deliveryRequired) return false;
  if (boundEpisodes.some((episode) => (
    episode.delivery_policy === 'required'
    && episode.state === 'delivery_pending'
    && !episodeHasDelivery(episode.id)
  ))) return true;
  const completedIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'werk_bijhouden' && tool.ok && tool.verified
    && actionData(tool)?.item?.status === 'done'
  ));
  return completedIndex >= 0 && sentIndex < 0;
}

function autonomyRequiredWorkIds(toolResults = [], initialOpenWorkIds = []) {
  const requiredIds = new Set((Array.isArray(initialOpenWorkIds) ? initialOpenWorkIds : [])
    .map(Number).filter(Number.isFinite));
  for (const tool of toolResults) {
    const item = tool.name === 'werk_bijhouden' ? actionData(tool)?.item : null;
    if (['open', 'blocked'].includes(item?.status) && Number.isFinite(Number(item?.id))) {
      requiredIds.add(Number(item.id));
    }
  }
  return [...requiredIds];
}

function autonomyPendingCheckpointIds(toolResults = [], initialOpenWorkIds = []) {
  const requiredIds = autonomyRequiredWorkIds(toolResults, initialOpenWorkIds);
  if (!requiredIds.length) return [];
  const outcomeIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok && tool.verified
  ));
  if (outcomeIndex < 0) return [];
  return requiredIds.filter((requiredId) => {
    const item = currentWorkItem(requiredId, toolResults);
    const hasRelevantProof = toolResults.some((tool, index) => (
      index < outcomeIndex && autonomyProofRelevantToWork(item, tool)
    ));
    if (!hasRelevantProof) return false;
    const updates = toolResults.map((tool, index) => ({ tool, index }))
      .filter(({ tool }) => (
        tool.name === 'werk_bijhouden' && tool.ok && tool.verified
        && Number(actionData(tool)?.item?.id) === requiredId
      ));
    return !updates.some(({ index }) => index > outcomeIndex);
  });
}

function autonomyWorkCheckpointPending(toolResults = [], initialOpenWorkIds = []) {
  return autonomyPendingCheckpointIds(toolResults, initialOpenWorkIds).length > 0;
}

function autonomyHasRunnableWork(toolResults = [], initialOpenWorkIds = []) {
  const requiredIds = autonomyRequiredWorkIds(toolResults, initialOpenWorkIds);
  return requiredIds.some((requiredId) => {
    const latest = [...toolResults].reverse().find((tool) => (
      tool.name === 'werk_bijhouden' && tool.ok && tool.verified
      && Number(actionData(tool)?.item?.id) === Number(requiredId)
    ));
    const item = actionData(latest)?.item || currentWorkItem(requiredId, toolResults);
    if ((item?.status || 'open') !== 'open') return false;
    const wake = Date.parse(item?.next_wake_ts || '');
    return !Number.isFinite(wake) || wake <= Date.now();
  });
}

function autonomyHasScheduledWork(toolResults = [], initialOpenWorkIds = []) {
  return autonomyRequiredWorkIds(toolResults, initialOpenWorkIds).some((requiredId) => {
    const latest = [...toolResults].reverse().find((tool) => (
      tool.name === 'werk_bijhouden' && tool.ok && tool.verified
      && Number(actionData(tool)?.item?.id) === Number(requiredId)
    ));
    const item = actionData(latest)?.item || currentWorkItem(requiredId, toolResults);
    const wake = Date.parse(item?.next_wake_ts || '');
    return item?.status === 'open' && Number.isFinite(wake) && wake > Date.now();
  });
}

function workItemMateriallyChanged(before, after) {
  if (!before || !after) return !!after;
  return ['status', 'next_step', 'evidence', 'last_action', 'result']
    .some((field) => String(before[field] ?? '') !== String(after[field] ?? ''));
}

function autonomyProgressFromTools(toolResults = [], continuation = null) {
  const progressTool = [...toolResults].reverse().find((tool) => (
    tool.ok && tool.verified && tool.changed
    && (tool.name === 'schrijf_bestand'
      || (tool.name === 'werk_bijhouden' && actionData(tool)?.proofAccepted === true))
  ));
  if (!progressTool && !continuation) return null;
  const item = actionData(progressTool)?.item || continuation;
  const progressEvidence = checkpointEvidence(progressTool);
  const summary = progressTool?.name === 'schrijf_bestand'
    ? [
        progressEvidence,
        item?.next_step ? `Volgende stap: ${item.next_step}` : '',
      ].filter(Boolean).join(' | ')
    : item
    ? [
        item.title ? `Werk #${item.id}: ${item.title}` : '',
        item.evidence || progressEvidence,
        item.next_step ? `Volgende stap: ${item.next_step}` : '',
      ].filter(Boolean).join(' | ')
    : progressEvidence;
  return {
    made_progress: true,
    tool: progressTool?.name || item?.last_action || 'werk_bijhouden',
    summary: String(summary || 'Een duurzame tussenstap is gecontroleerd en opgeslagen.').slice(0, 800),
  };
}

function autonomyNoActionReason(rawAnswer, toolResults = []) {
  const answer = String(rawAnswer || '').replace(/\s+/g, ' ').trim();
  if (answer && answer.length <= 600
    && !/(?:to=functions\.|__eva_|function_call|tool_call)/i.test(answer)) {
    return answer;
  }
  if (toolResults.length) {
    return 'De controles in deze beurt leverden geen nieuw resultaat op dat een gezinsbeslissing of handeling veranderde.';
  }
  return 'Er was nu geen concrete, nieuwe bijdrage die betrouwbaar uitgevoerd kon worden.';
}

function autonomyStopReady(toolResults = [], {
  allowSignalOnly = true,
  requiredWorkIds = [],
} = {}) {
  // Een eerder verstuurd bericht maakt later geschreven code niet af. Zolang
  // een werkstuk nog niet is uitgevoerd en teruggelezen, blijft Eva zelf aan
  // zet en mag de autonome beurt niet als afgerond stoppen.
  if (incompleteAutonomousWrite(toolResults)) return false;
  if (autonomyHasRunnableWork(toolResults, requiredWorkIds)) return false;
  if (autonomyWorkReportPending(toolResults, requiredWorkIds)) return false;
  if (autonomyHasScheduledWork(toolResults, requiredWorkIds)) return true;
  const artifact = completedAutonomousArtifact(toolResults);
  const sentIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok && tool.verified
  ));
  const artifactIndex = artifact ? autonomousArtifactReadIndex(toolResults) : -1;
  if (!config.autonomyFull && artifactIndex >= 0 && sentIndex < artifactIndex) return false;
  const outcomeIndex = sentIndex;
  if (outcomeIndex >= 0) {
    if (!autonomyWorkCheckpointPending(toolResults, requiredWorkIds)) return true;
  }
  if (!config.autonomyFull && allowSignalOnly && toolResults.some((tool) => (
    tool.name === 'signalen_afhandelen' && tool.ok && tool.verified
  ))) return true;
  return toolResults.some((tool) => (
    tool.name === 'werk_bijhouden' && tool.ok && tool.verified
    && actionData(tool)?.item?.status === 'blocked'
  ));
}

function contributionFromTools(toolResults, mode, userText = '', {
  durable = false,
  plannedRequiredTools = null,
} = {}) {
  const verified = toolResults.filter((tool) => tool?.ok === true && tool?.verified === true);
  if (mode !== 'autonomy') {
    const required = Array.isArray(plannedRequiredTools)
      ? plannedRequiredTools
      : requiredToolsFor({ text: userText });
    if (!required.length && !isExplicitToolRequest(userText)) return null;
    if (durable) {
      const latestWrite = [...toolResults].reverse().find((tool) => (
        tool.name === 'schrijf_bestand' && tool.ok && tool.verified
      ));
      const artifactPath = writtenArtifactPath(latestWrite);
      const artifact = completedArtifact(toolResults, {
        requireExecution: /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath),
      });
      if (artifact) return artifact.execute || artifact.write;
      return [...verified].reverse().find((tool) => (
        tool.changed === true
        && !['zoek_internet', 'schrijf_bestand', 'onthoud', 'stuur_bericht', 'werk_bijhouden'].includes(tool.name)
      )) || null;
    }
    if (required.includes('schrijf_bestand')) {
      const artifact = completedArtifact(toolResults, {
        requireExecution: required.includes('voer_uit'),
      });
      return artifact ? (artifact.execute || artifact.write) : null;
    }
    if (required.length && required.some((name) => !turnRequirementSatisfied(name, toolResults, userText))) {
      return null;
    }
    return [...verified].reverse().find((tool) => (
      (tool.changed === true
        && tool.name !== 'leer_gezicht_van_foto'
        && (tool.name !== 'onthoud' || /^\s*(?:onthoud|noteer)\b/i.test(cleanUserText(userText))))
      || (tool.name === 'zoek_internet' && /^\s*(?:zoek|onderzoek|vergelijk)\b/i.test(cleanUserText(userText)))
      || (tool.name === 'voer_uit' && isExplicitToolRequest(userText) && successfulZeroExit(tool))
    )) || null;
  }

  const sentIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok === true && tool.verified === true
      && tool.changed === true
  ));
  // Een bronrapport of tussenbericht mag een daarna geschreven demo niet tot
  // een bijdrage promoveren. Het werk blijft open zodat Eva het in haar eigen
  // volgende werksessie uitvoert en terugleest.
  if (incompleteAutonomousWrite(toolResults)) return null;
  const completedAutonomyArtifact = completedAutonomousArtifact(toolResults);
  const completedArtifactIndex = completedAutonomyArtifact
    ? autonomousArtifactReadIndex(toolResults)
    : -1;
  if (config.autonomyFull
    && completedAutonomyArtifact && completedArtifactIndex > sentIndex) return completedAutonomyArtifact;
  if (config.autonomyFull && sentIndex >= 0) return toolResults[sentIndex];
  if (sentIndex >= 0 && toolResults.slice(0, sentIndex).some((tool) => (
    tool.ok === true && tool.verified === true
      && !['onthoud', 'toon_werk', 'werk_bijhouden', 'stuur_bericht'].includes(tool.name)
  ))) {
    return toolResults[sentIndex];
  }
  // Een aantoonbare stille verandering is ook een bijdrage. Communicatie is een
  // keuze, geen vereiste om uitgevoerd werk als productief te boeken.
  const silentChange = [...verified].reverse().find((tool) => (
    tool.changed === true
    && ['bedien_huis', 'zet_herinnering', 'annuleer_herinnering', 'spreek_in_huis'].includes(tool.name)
    && (tool.name !== 'zet_herinnering' || autonomyEffectRequiresReport(tool))
  ));
  if (silentChange) return silentChange;
  if (config.autonomyFull && completedAutonomyArtifact) return completedAutonomyArtifact;
  return null;
}

function evidenceFallback(toolResults, { requiredTools = [], userText = '' } = {}) {
  const required = new Set((Array.isArray(requiredTools) ? requiredTools : []).map(String));
  if (required.has('beheer_werkcel')) {
    const proof = [...toolResults].reverse().find((tool) => (
      tool.name === 'beheer_werkcel' && tool.ok && tool.verified
      && actionData(tool)?.cell?.status === 'completed'
    ));
    const cell = actionData(proof)?.cell;
    if (cell) {
      const streams = Array.isArray(cell.streams) ? cell.streams : [];
      return `Ik heb ${streams.filter((stream) => stream.status === 'completed').length}/${streams.length} onafhankelijke onderzoekssporen afgerond en het bewijs met de bronlinks bewaard.`;
    }
  }
  if (required.has('beheer_toolpakket')) {
    const proof = [...toolResults].reverse().find((tool) => (
      tool.name === 'beheer_toolpakket' && tool.ok && tool.verified
    ));
    const data = actionData(proof);
    const item = data?.installation || (Array.isArray(data?.installations) ? data.installations[0] : null);
    if (item) {
      return [
        `${item.package_name}@${item.package_version} staat op ${item.status}.`,
        item.target_path ? `Doelpad: ${item.target_path}.` : '',
        item.integrity_ok === true ? 'De stagingintegriteit is opnieuw gecontroleerd.' : '',
        item.lock_sha256 ? `Lockhash: ${item.lock_sha256}.` : '',
        item.artifact_sha256 ? `Artifacthash: ${item.artifact_sha256}.` : '',
        item.status === 'staged' ? 'De pakketcode is nog niet uitgevoerd of geactiveerd.' : '',
      ].filter(Boolean).join(' ');
    }
  }
  if (required.has('gebruik_capability')) {
    const used = [...toolResults].reverse().find((tool) => (
      tool.name === 'gebruik_capability' && tool.ok && tool.verified
      && actionData(tool)?.demonstrated === true
    ));
    const data = actionData(used);
    const capability = data?.capability;
    if (capability) {
      const boundary = capability.kind === 'skill'
        ? 'De procedure is uit de actieve registry geladen; ze voegt geen nieuwe gereedschapsrechten toe.'
        : `De add-on is in de vaste sandbox uitgevoerd met ${data.sandbox?.join(' en ') || 'beperkte rechten'}.`;
      const demoOutput = String(data?.stdout || data?.text || '')
        .trim()
        .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '')
        .slice(0, 800);
      return [
        `${capability.name} v${capability.version} is aantoonbaar gebruikt nadat receipt #${data.receipt_id} en de actuele inhoudshash overeenkwamen.`,
        data.content_hash ? `Inhoudshash: ${data.content_hash}.` : '',
        demoOutput ? `Letterlijke demo-uitvoer: ${demoOutput}` : '',
        boundary,
        'Eva kan versieerbare skills en checksumgebonden workspace-add-ons stagen, beproeven en activeren; dit wijzigt geen productiecode en geeft geen extra kern- of Home Assistant-rechten.',
      ].filter(Boolean).join(' ');
    }
  }
  if (required.has('schrijf_bestand')) {
    if (!required.has('voer_uit') && pendingExecutableArtifactPath(toolResults)) return '';
    const artifact = completedArtifact(toolResults, { requireExecution: required.has('voer_uit') });
    if (!artifact) return '';
    if (required.has('maak_vaardigheid') || required.has('beheer_addon')) {
      const capabilityTool = required.has('maak_vaardigheid') ? 'maak_vaardigheid' : 'beheer_addon';
      const active = [...toolResults].reverse().find((tool) => (
        tool.name === capabilityTool && tool.ok && tool.verified
        && actionData(tool)?.capability?.status === 'active'
      ));
      const capability = actionData(active)?.capability;
      if (!capability) return '';
      return `De ${capability.kind === 'addon' ? 'add-on' : 'vaardigheid'} ${capability.name} v${capability.version} is na de gebonden proef actief geworden.`;
    }
    if (required.has('voer_uit')) {
      const acceptance = programmingAcceptanceStatus(toolResults, userText);
      if ((acceptance.needsTests || acceptance.needsCli) && acceptance.complete) {
        const requestedFile = cleanUserText(userText).match(/\b([a-z0-9_-]+\.(?:cjs|js|mjs|py|sh|ts))\b/i)?.[1] || '';
        const writtenSources = [...toolResults].reverse().filter((tool) => {
          const file = writtenArtifactPath(tool);
          return tool.name === 'schrijf_bestand' && /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(file)
            && !/(?:^|\/)test[_-]|[._-]test\./i.test(file);
        });
        const source = writtenSources.find((tool) => writtenArtifactPath(tool).endsWith(`/${requestedFile}`))
          || writtenSources[0];
        const testData = actionData(acceptance.testExecution) || {};
        const cliData = actionData(acceptance.cliExecution) || {};
        const testText = readableEvidence(`${testData.stdout || ''} ${testData.stderr || ''}`);
        const cliText = readableEvidence(cliData.stdout || cliData.stderr || '');
        return [
          `${writtenArtifactPath(source) || artifact.path} is geschreven en gecontroleerd.`,
          acceptance.testExecution
            ? `De gerichte tests slaagden met exitcode 0${testText ? `: ${testText}` : '.'}` : '',
          acceptance.cliExecution
            ? `De echte CLI-run slaagde met exitcode 0${cliText ? ` en gaf: ${cliText}` : '.'}` : '',
        ].filter(Boolean).join(' ');
      }
      return checkpointEvidence(artifact.execute)
        || 'Het programma is met exitcode 0 uitgevoerd en daarna teruggelezen.';
    }
  }
  const completedDocument = completedArtifact(toolResults, { requireExecution: false });
  if (completedDocument && !/\.(?:cjs|js|mjs|py|sh|ts)$/i.test(completedDocument.path)) {
    const content = humanResultText(
      completedDocument.read.output || completedDocument.read.evidence?.summary,
      { limit: 12_000, preferDetailed: true },
    );
    if (content) return content;
  }
  const explicitResearch = /^\s*(?:zoek|onderzoek|vergelijk)\b/i.test(cleanUserText(userText));
  const useful = new Set(['bedien_huis', 'bekijk_camera', 'spreek_in_huis']);
  if (explicitResearch) useful.add('zoek_internet');
  for (const name of required) {
    if (!['huis_status', 'ha_lees', 'wie_is_thuis', 'zoek_geheugen', 'toon_werk'].includes(name)) useful.add(name);
  }
  const proven = [...toolResults].reverse().find((tool) => (
    tool?.ok && tool?.verified && useful.has(tool.name)
      && (required.size === 0 || required.has(tool.name))
  ));
  if (!proven) return '';
  return humanResultText(proven.output || proven.evidence?.summary, {
    limit: 2_400,
    preferDetailed: true,
  });
}

function looksLikeInternalOutput(value = '') {
  const text = String(value || '');
  return /\bdata[\\/]workspace\b|\b(?:stdout|stderr|exitcode)\b|_ngcontent-|<\/?(?:html|div|p|script)\b|\{\s*"(?:contract|tool|ok)"/i.test(text)
    || /`[^`\n]{1,160}\.(?:md|txt|json|ya?ml|csv|m?js|py)`/i.test(text)
    || /\[[\s\d,]{120,}\]/.test(text);
}

function understandTurn({
  channel,
  sender,
  text,
  replyEvidence,
  messageContext,
  mode,
  providedConversationId,
}) {
  const person = String(sender || 'huisgenoot').trim() || 'huisgenoot';
  const userText = cleanUserText(text);
  const directContinuationRequest = mode === 'chat' && isContinuationRequest(userText);
  const conversationId = providedConversationId || conversationIdFor({
    channel,
    sender: person,
    messageContext,
  });
  const syntheticTest = messageContext?.syntheticTest === true;
  const recentThreadRows = mode === 'chat' ? recentConversation(conversationId, 10) : [];
  const latestAssistantReply = [...recentThreadRows].reverse()
    .find((row) => row.role === 'assistant')?.content || '';
  const continuationRequest = directContinuationRequest
    || (mode === 'chat' && followsUnfinishedReply(userText, latestAssistantReply));
  const continuationRows = continuationRequest ? recentThreadRows : [];
  const continuationAnchor = continuationRequest ? latestAssistantReply : '';
  const continuationThread = continuationRows.map((row) => row.content || '').join('\n');
  const boundary = effectBoundary(userText);
  const readOnlyRequest = mode === 'chat'
    && (boundary.denied.includes('workspace') || boundary.denied.length >= 3);
  const recentAgentMailThread = /\bagentmail\b/i.test(
    `${recentThreadRows.map((row) => row.content || '').join('\n')}\n${latestAssistantReply}`,
  );
  const agentMailContinuation = recentAgentMailThread && (
    continuationRequest
    || /^\s*\d{6}\s*$/.test(userText)
    || /\b(?:verification\s+code|verificatiecode|otp|all\s+set|e-?mail|mailtje|inbox)\b/i.test(userText)
  );
  const voiceDirective = mode === 'chat'
    ? resolveVoiceDirective({
      text: userText,
      recentMessages: recentThreadRows,
      previousAssistant: latestAssistantReply,
    })
    : null;
  const baseContract = turnContractFor(userText, {
    continuation: continuationRequest,
    mode,
    voiceDirective,
  });
  const baseRequiredTools = mode === 'chat'
    ? [...new Set([
      ...requiredToolsFor({ text: userText, replyEvidence }),
      ...(continuationRequest
        && /\b(?:aanwezigheid|thuisstatus|person(?:\.|\s)|device[_ -]?tracker|telefoontracker)\b/i.test(continuationThread)
        && /\b(?:doe|moet|kan|kunt|regel|pas|verwijder|haal)\w*\b/i.test(userText)
        ? ['beheer_aanwezigheid']
        : []),
      ...(agentMailContinuation ? ['beheer_agentmail'] : []),
      ...(baseContract.requiredTools || []),
    ])]
    : [];
  const actionRequested = mode === 'chat'
    && (isExplicitToolRequest(userText) || continuationRequest);
  const turnPlan = arbitrateTurnPlan({
    mode,
    text: userText,
    baseContract,
    baseRequiredTools,
    baseDirectAction: baseRequiredTools.includes('bedien_huis') ? directHomeCommand(userText) : null,
    actionRequested,
    voiceDirective,
    effectForTool: toolEffect,
  });
  const turnContract = turnPlan.contract;
  const requiredTools = turnPlan.requiredTools;
  const projectWork = mode === 'chat'
    && !readOnlyRequest
    && turnContract.kind !== 'voice_action'
    && (isDurableProjectRequest(userText) || continuationRequest);
  const longWork = mode === 'autonomy' || projectWork || isLongWork(userText);
  const explicitTool = mode === 'chat'
    && turnPlan.actionIntent === 'execute'
    && actionRequested;
  const recapOnly = mode === 'chat' && isWorkRecapQuery(userText);
  const reflectiveQuestion = mode === 'chat' && isReflectiveQuestion(userText);
  const buildWork = requiredTools.includes('schrijf_bestand');
  const workspaceEditWork = mode === 'chat' && requestsWorkspaceFileEdit(userText);
  const newBuildWork = buildWork && !workspaceEditWork;
  const buildNeedsExecution = requiredTools.includes('voer_uit');
  const durableInteraction = shouldBindDurableUserEpisode({
    projectWork,
    explicitTool,
    buildWork: newBuildWork,
    requiredToolCount: requiredTools.length,
    longWork: isLongWork(userText),
  });
  return {
    person, userText, conversationId, syntheticTest,
    continuationRequest, continuationAnchor,
    readOnlyRequest, voiceDirective, turnPlan,
    turnContract, requiredTools, projectWork, longWork, explicitTool, recapOnly,
    reflectiveQuestion, buildWork, workspaceEditWork, newBuildWork,
    buildNeedsExecution, durableInteraction,
  };
}

async function actForTurn(name, args, context) {
  return executeTool(name, args, context);
}

function inspectToolOutcome(name, args, output) {
  let parsed = null;
  try { parsed = JSON.parse(String(output)); } catch { /* gewone tekst is ook geldig */ }
  return {
    parsed,
    recorded: {
      name,
      args,
      output,
      ok: parsed?.ok !== false && !/^Fout bij /i.test(String(output)),
      verified: parsed?.verified === true,
      changed: parsed?.changed === true,
      needs_followup: parsed?.needs_followup === true,
      partial: parsed?.partial === true,
      failure_class: parsed?.failure_class || null,
      retryable: parsed?.retryable === true,
      evidence: parsed?.evidence || null,
      at: new Date().toISOString(),
    },
  };
}

async function executeTurn({
  channel,
  sender,
  text,
  replyTo = null,
  replyEvidence = null,
  messageContext = null,
  mode = 'chat',
  persistInbound = true,
  conversationId: providedConversationId = null,
}) {
  const started = performance.now();
  const {
    person, userText, conversationId, syntheticTest,
    continuationRequest, continuationAnchor,
    readOnlyRequest, voiceDirective, turnPlan,
    turnContract, requiredTools, projectWork, longWork, explicitTool, recapOnly,
    reflectiveQuestion, buildWork, workspaceEditWork, newBuildWork,
    buildNeedsExecution, durableInteraction,
  } = understandTurn({
    channel, sender, text, replyEvidence, messageContext, mode,
    providedConversationId,
  });
  const freeAttention = mode === 'autonomy' && !!messageContext?.attention;
  const allOpenWorkItems = mode === 'autonomy' ? durableWorkStatus().open : [];
  const selectedAutonomyWorkId = Number(messageContext?.workId);
  const initialOpenWorkItems = mode === 'autonomy' && !freeAttention
    ? Number.isFinite(selectedAutonomyWorkId)
      ? allOpenWorkItems.filter((item) => Number(item.id) === selectedAutonomyWorkId).slice(0, 1)
      : allOpenWorkItems.slice(0, 1)
    : [];
  const initialOpenWorkIds = initialOpenWorkItems
    .map((item) => Number(item.id)).filter(Number.isFinite);
  const hasOpenDurableWork = allOpenWorkItems.length > 0;
  // Vervolgtaal bindt aan de episode van dit gesprek, nooit aan het oudste
  // globale werkpunt. Een expliciet work-id blijft de sterkste aanwijzing.
  const requestedWorkId = Number(messageContext?.workId);
  const contextualContinuationEpisode = continuationRequest
    ? openEpisodeForConversation(conversationId)
    : null;
  const continuationWorkId = Number.isFinite(requestedWorkId)
    ? requestedWorkId
    : Number(contextualContinuationEpisode?.work_item_id);
  const continuationWork = continuationRequest && Number.isFinite(continuationWorkId)
    ? db.prepare("SELECT * FROM eva_work_items WHERE id = ? AND status IN ('open','blocked')")
      .get(continuationWorkId) || null
    : null;
  const urgentPendingSignal = mode === 'autonomy'
    && Array.isArray(messageContext?.pendingSignals)
    && messageContext.pendingSignals.some((signal) => (
      /^(?:arrival|departure|door|doorbell)$/i.test(String(signal?.type || ''))
    ));
  let autonomyLane = urgentPendingSignal
    ? 'house'
    : autonomyLaneForWork(initialOpenWorkItems[0]);
  const supersedingTelegramMessage = () => {
    if (channel !== 'telegram') return null;
    return newerTelegramUserMessage(
      messageContext?.chatId,
      messageContext?.messageId,
    );
  };
  const autonomyEpoch = runtime.interactionEpoch;
  const durableTelegramTurn = channel === 'telegram'
    && messageContext?.chatId != null && messageContext?.messageId != null
    ? `telegram:${messageContext.chatId}:${messageContext.messageId}`
    : null;
  const turnId = durableTelegramTurn || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  let inboundMessageId = messageContext?.messageId == null
    ? null
    : String(messageContext.messageId);
  const cognitiveEpisode = beginCognitiveEpisode({
    correlationId: turnId,
    kind: mode === 'autonomy' ? 'autonomous_attention' : 'interaction',
    modality: mode === 'autonomy' ? 'internal' : channel === 'voice' ? 'auditory_language' : 'linguistic',
    source: mode === 'autonomy' ? 'autonomy_clock' : channel,
    sourceRef: conversationId,
    subjectKey: mode === 'autonomy' ? 'eva' : person,
    focusKind: mode === 'autonomy' ? 'self_chosen_or_continuing_work' : 'conversation',
    focusRef: mode === 'autonomy'
      ? (initialOpenWorkIds[0] ? `work:${initialOpenWorkIds[0]}` : null)
      : conversationId,
    goalKey: Number(messageContext?.goalEpisodeId)
      ? `episode:${Number(messageContext.goalEpisodeId)}`
      : initialOpenWorkIds[0]
        ? `work:${initialOpenWorkIds[0]}`
        : mode === 'autonomy'
          ? Array.isArray(messageContext?.pendingSignals) && messageContext.pendingSignals.length
            ? 'signal_assessment'
            : messageContext?.attention?.kind
              ? `attention:${messageContext.attention.kind}:${messageContext.attention.id || '-'}`
              : 'free_attention'
          : `${mode}:${turnContract.kind}`,
    whyNow: mode === 'autonomy'
      ? userText.slice(0, 300)
      : `${person} richt zich nu tot Eva`,
    expectedChange: turnContract.outcome,
    salience: mode === 'autonomy' ? 0.55 : 0.9,
    payload: {
      channel,
      mode,
      sender: person,
      contractKind: turnContract.kind,
    },
  });
  const toolResults = [];
  let durableCommand = mode === 'autonomy' ? (initialOpenWorkItems[0] || null) : null;
  let durableGoalEpisode = Number(messageContext?.goalEpisodeId)
    ? episodeForWork(initialOpenWorkIds[0])
    : null;
  let durableExecution = durableInteraction;
  let boundAutonomyWorkId = initialOpenWorkIds[0] || null;
  let boundAutonomyExplorationId = Number(messageContext?.attention?.kind === 'exploration'
    ? messageContext.attention.id : null) || null;
  const activeAutonomyWorkIds = () => [...new Set([
    ...initialOpenWorkIds,
    Number(durableCommand?.id),
  ].filter(Number.isFinite))];
  const toolAttempts = new Map();
  const toolNameAttempts = new Map();
  const durableExecutionCounts = new Map();
  const attemptLimitFor = (name) => {
    // Een brede zoekopdracht die time-out of weinig oplevert mag niet de hele
    // onderzoeksmissie beëindigen. Een andere vraagstelling is een echte pivot.
    if (name === 'zoek_internet') return 3;
    if (mode !== 'autonomy' && name === 'gebruik_robot') return 5;
    if (mode !== 'autonomy' && name === 'lees_bestand' && requiredTools.includes(name)) return 8;
    if (mode !== 'autonomy') {
      if (!requiredTools.includes(name)) {
        return ({ lijst_bestanden: 1, lees_bestand: 3, schrijf_bestand: 2 })[name] || 2;
      }
      if (requiredTools.includes('schrijf_bestand')) {
        return ({
          schrijf_bestand: 8,
          voer_uit: 4,
          lees_bestand: 7,
          lijst_bestanden: 2,
          maak_vaardigheid: 8,
          beheer_addon: 8,
        })[name] || 3;
      }
      return 2;
    }
    if (config.autonomyFull) return name === 'stuur_bericht' ? 1 : 8;
    return ({
      schrijf_bestand: 3,
      voer_uit: 3,
      werk_bijhouden: 2,
      stuur_bericht: 1,
    })[name] || 2;
  };
  const executeForTurn = async (name, args, context) => {
    const governedContext = {
      ...context,
      effectPolicy: context?.effectPolicy || turnPlan.effectPolicy,
    };
    if (!durableTelegramTurn || !toolIsDurable(name)) {
      return actForTurn(name, args, governedContext);
    }
    const signature = `${name}:${JSON.stringify(args || {})}`;
    const occurrence = Number(durableExecutionCounts.get(signature) || 0) + 1;
    durableExecutionCounts.set(signature, occurrence);
    const executionKey = createHash('sha256')
      .update(`${turnId}\n${signature}\n${occurrence}`)
      .digest('hex');
    const claimed = claimTurnToolExecution({
      executionKey,
      turnId,
      tool: name,
      args,
    });
    if (!claimed.claimed) {
      if (claimed.row?.status === 'done' && claimed.row.output) return claimed.row.output;
      return JSON.stringify({
        contract: 'ActionResult/v1',
        tool: name,
        ok: false,
        changed: false,
        verified: false,
        needs_followup: true,
        error: 'De vorige procesbeurt stopte tijdens deze handeling. Ik voer haar niet blind dubbel uit; de actuele toestand moet eerst worden gecontroleerd.',
      });
    }
    const output = await actForTurn(name, args, governedContext);
    completeTurnToolExecution(executionKey, output);
    return output;
  };
  const recordToolOutput = (name, args, output) => {
    const { recorded, parsed } = inspectToolOutcome(name, args, output);
    const { ok, verified, changed } = recorded;
    toolResults.push(recorded);
    if (mode === 'autonomy' && !syntheticTest && name === 'stuur_bericht'
      && recorded.ok === false && durableCommand) {
      const data = actionData(recorded) || {};
      const previous = pendingDeliveryForWork(durableCommand);
      const attempts = Number(previous?.attempts || 0) + 1;
      const quietHours = data.waiting_for === 'quiet_hours';
      const retryMs = Math.min(6 * 60 * 60_000, 15 * 60_000 * (2 ** Math.min(5, attempts - 1)));
      const resumeAfter = quietHours && Number.isFinite(Date.parse(data.resume_after || ''))
        ? data.resume_after
        : new Date(Date.now() + retryMs).toISOString();
      const workState = durableWorkState(durableCommand);
      const payload = {
        text: String(args?.tekst || previous?.text || '').trim().slice(0, 4_000),
        facts: previous?.facts
          || String(durableGoalEpisode?.proof_summary || durableCommand?.evidence || '').trim().slice(0, 4_000)
          || String(args?.tekst || previous?.text || '').trim().slice(0, 4_000),
        recipient: String(args?.aan || previous?.recipient || '').trim() || null,
        preparedAt: previous?.preparedAt || new Date().toISOString(),
        attempts,
        dedupeKey: previous?.dedupeKey || `episode:${durableGoalEpisode?.id || '-'}:prepared-delivery`,
      };
      const deliveryWork = {
        state: { ...workState, pending_delivery: payload },
        nextWakeTs: resumeAfter,
        wait: {
          reason: quietHours ? 'quiet_hours_delivery' : 'delivery_retry',
          resumeAfter,
          attempts,
        },
        nextStep: 'Beoordeel het geverifieerde resultaat opnieuw en bezorg het alleen passend.',
      };
      if (durableGoalEpisode) {
        const transition = transitionGoalEpisode(durableGoalEpisode.id, {
          state: 'delivery_pending', wakeTs: resumeAfter,
          nextAction: deliveryWork.nextStep, sessionRef: turnId, work: deliveryWork,
        });
        durableGoalEpisode = transition.episode;
        durableCommand = transition.workItem;
      } else durableCommand = updateDurableWork(durableCommand.id, deliveryWork);
    }
    const workItem = name === 'werk_bijhouden' ? actionData(recorded)?.item : null;
    if (!syntheticTest && Number.isFinite(Number(workItem?.id))) {
      if (mode === 'autonomy' && !durableCommand) durableCommand = workItem;
      const episode = ensureEpisodeForWorkItem(workItem, {
        originKind: mode === 'autonomy' ? 'self' : 'user',
        originChannel: channel,
        originConversationId: mode === 'chat' ? conversationId : null,
        originMessageId: inboundMessageId,
        requester: mode === 'chat' ? person : null,
      });
      if (!durableGoalEpisode) durableGoalEpisode = episode;
    }
    if (mode === 'chat' && !syntheticTest && !durableGoalEpisode
      && name !== 'werk_bijhouden' && toolIsDurable(name)
      && recorded.ok && recorded.verified && recorded.changed) {
      ({ command: durableCommand, episode: durableGoalEpisode } = db.transaction(() => {
        const command = createWorkItem({
          source: channel,
          owner: person,
          title: `Opdracht: ${userText.slice(0, 210)}`,
          goal: userText,
          nextStep: 'Controleer het resultaat en bezorg het in dit gesprek.',
          definitionOfDone: turnContract.outcome || userText,
          priority: 75,
          visibleToFamily: true,
        });
        const existing = episodeForWork(command.id);
        if (existing) {
          attachEpisodeOrigin(existing.id, {
            originKind: 'user', originChannel: channel,
            originConversationId: conversationId,
            originMessageId: inboundMessageId,
            requester: person,
          });
          return { command, episode: existing };
        }
        const episode = createGoalEpisode({
          title: `Opdracht van ${person}`,
          outcome: userText,
          definitionOfDone: turnContract.outcome || userText,
          originKind: 'user',
          originChannel: channel,
          originConversationId: conversationId,
          originMessageId: inboundMessageId,
          requester: person,
          deliveryPolicy: 'required',
          deliveryTarget: person,
          nextAction: 'Controleer het resultaat en bezorg het in dit gesprek.',
        });
        return { command, episode: bindEpisodeToWork(episode.id, command.id) };
      })());
      durableExecution = true;
    }
    if (mode === 'autonomy' && !syntheticTest && name === 'zet_herinnering'
      && !autonomyEffectRequiresReport(name, args)
      && recorded.ok && recorded.verified && recorded.changed && durableCommand) {
      const due = actionData(recorded)?.due;
      if (Number.isFinite(Date.parse(due || ''))) {
        const reminderWork = {
          nextWakeTs: due,
          wait: { reason: 'self_reminder', reminderId: actionData(recorded)?.id || null },
          currentStep: durableCommand.current_step || durableCommand.next_step || '',
        };
        if (durableGoalEpisode) {
          const transition = transitionGoalEpisode(durableGoalEpisode.id, {
            state: 'waiting', wakeTs: due, nextAction: reminderWork.currentStep,
            sessionRef: turnId, work: reminderWork,
          });
          durableGoalEpisode = transition.episode;
          durableCommand = transition.workItem;
        } else durableCommand = updateDurableWork(durableCommand.id, reminderWork);
      }
    }
    if (mode === 'autonomy' && !syntheticTest && durableGoalEpisode
      && autonomyEffectRequiresReport(name, args)
      && recorded.ok && recorded.verified && recorded.changed) {
      const proof = readableEvidence(
        recorded.evidence?.summary || recorded.output || `${name} is uitgevoerd`,
      ).slice(0, 1_200);
      const transition = transitionGoalEpisode(durableGoalEpisode.id, {
        state: 'delivery_pending', checkpoint: proof, proofSummary: proof,
        nextAction: 'Bezorg het concrete effect kort aan het gezin.',
        deliveryPolicy: 'required',
        deliveryTarget: durableGoalEpisode.delivery_target || 'gezin',
        sessionRef: turnId,
        work: { evidence: proof, lastAction: name },
      });
      durableGoalEpisode = transition.episode;
      if (transition.workItem) durableCommand = transition.workItem;
    }
    if (mode === 'autonomy' && !syntheticTest && !durableGoalEpisode
      && autonomyEffectRequiresReport(name, args)
      && recorded.ok && recorded.verified && recorded.changed) {
      const proof = readableEvidence(
        recorded.evidence?.summary || recorded.output || `${name} is uitgevoerd`,
      ).slice(0, 1_200);
      ({ command: durableCommand, episode: durableGoalEpisode } = db.transaction(() => {
        const command = createWorkItem({
          source: 'autonomy',
          owner: null,
          title: `Zelfgekozen verandering: ${name}`,
          goal: `Maak de uitgevoerde verandering begrijpelijk zichtbaar voor het gezin: ${proof}`,
          nextStep: 'Bezorg het concrete effect kort aan het gezin.',
          definitionOfDone: 'Het effect is geverifieerd en precies eenmaal passend gemeld.',
          priority: 80,
          visibleToFamily: true,
        });
        const existing = episodeForWork(command.id);
        const episode = existing || bindEpisodeToWork(createGoalEpisode({
          goalKey: 'household_value', title: command.title, outcome: command.goal,
          definitionOfDone: command.definition_of_done, originKind: 'self',
          originChannel: 'autonomy', deliveryPolicy: 'required',
          nextAction: command.next_step,
        }).id, command.id);
        const transition = transitionGoalEpisode(episode.id, {
          state: 'delivery_pending', checkpoint: proof, proofSummary: proof,
          nextAction: command.next_step, sessionRef: turnId,
          work: { evidence: proof, lastAction: name },
        });
        return {
          command: transition.workItem,
          episode: transition.episode,
        };
      })());
      boundAutonomyWorkId = durableCommand.id;
    }
    if (mode === 'autonomy' && !boundAutonomyWorkId && Number.isFinite(Number(workItem?.id))) {
      boundAutonomyWorkId = Number(workItem.id);
    }
    const exploration = name === 'verkenning_bijhouden'
      ? actionData(recorded)?.exploration
      : null;
    if (mode === 'autonomy' && Number.isFinite(Number(exploration?.id))
      && (recorded.changed || Number(boundAutonomyExplorationId) === Number(exploration.id))) {
      boundAutonomyExplorationId = Number(exploration.id);
    } else if (mode === 'autonomy' && boundAutonomyExplorationId) {
      noteExplorationAction(boundAutonomyExplorationId, {
        tool: name,
        ok,
        verified,
        evidence: parsed?.evidence?.summary || parsed?.human_message || null,
        episodeId: cognitiveEpisode.id,
      });
    }
    logTimeline('action', `${name}: ${ok ? 'gelukt' : 'niet gelukt'}`, {
      internalOnly: true,
      turnId,
      mode,
      tool: name,
      args,
      ok,
      verified,
      changed,
      output: String(output).slice(0, 1800),
    });
    try {
      const subject = args?.doel || args?.entiteit || args?.entity_id
        || args?.pad || args?.id || args?.naam || args?.vraag || null;
      const sourceUpdatedAt = parsed?.data?.entity?.updated
        || parsed?.data?.entity?.last_updated
        || parsed?.data?.updated
        || null;
      recordCognitiveAction(cognitiveEpisode.id, {
        correlationId: turnId,
        tool: name,
        subject,
        expected: turnContract.outcome,
        ok,
        verified,
        changed,
        evidence: parsed?.evidence?.summary || null,
      });
      recordCognitiveOutcome(cognitiveEpisode.id, {
        correlationId: turnId,
        resultClass: name,
        ok,
        verified,
        changed,
        evidence: parsed?.evidence?.summary || null,
        effect: parsed?.human_message || parsed?.error || null,
        sourceUpdatedAt,
      });
    } catch (error) {
      logTimeline('system', `Cognitieve actiekoppeling mislukte: ${errMsg(error)}`, {
        internalOnly: true,
        turnId,
        tool: name,
      });
    }
    return recorded;
  };
  const ensureAutonomyCheckpointAfterReport = () => {
    if (mode !== 'autonomy') return true;
    const pendingIds = autonomyPendingCheckpointIds(toolResults, activeAutonomyWorkIds());
    if (!pendingIds.length) return true;
    const sent = [...toolResults].reverse().find((tool) => (
      tool.name === 'stuur_bericht' && tool.ok && tool.verified
    ));
    if (!sent) return false;
    for (const workId of pendingIds) {
      const status = durableWorkStatus();
      const candidates = [...status.open, ...status.blocked, ...status.doneToday];
      const current = candidates.find((item) => Number(item.id) === Number(workId));
      if (!current) return false;
      const proof = [...toolResults].reverse().find((tool) => (
        autonomyProofRelevantToWork(current, tool)
      ));
      if (!proof) continue;
      try {
        const item = updateDurableWork(current.id, {
          status: current.status,
          evidence: checkpointEvidence(proof) || undefined,
          lastAction: proof.name,
          proofAccepted: current.status === 'done',
        });
        recordToolOutput('werk_bijhouden', {
          bewerking: 'herstelcheckpoint',
          id: current.id,
        }, JSON.stringify({
          contract: 'ActionResult/v1',
          tool: 'werk_bijhouden',
          ok: true,
          changed: true,
          verified: true,
          evidence: {
            source: 'werk_bijhouden',
            summary: `Werkpunt #${current.id} is na het bezorgde resultaat als ${current.status} vastgelegd.`,
          },
          needs_followup: current.status !== 'done',
          risk: 'autonomous',
          human_message: '',
          data: { ok: true, changed: true, item },
        }));
      } catch (error) {
        logTimeline('system', `Herstelcheckpoint voor zelfstandig werk mislukte: ${errMsg(error)}`, {
          internalOnly: true,
          autonomous: true,
          turnId,
          workId: current.id,
        });
        return false;
      }
    }
    return !autonomyWorkCheckpointPending(toolResults, activeAutonomyWorkIds());
  };
  const checkpointAutonomyProgress = () => {
    if (mode !== 'autonomy' || !initialOpenWorkIds.length) return null;
    const status = durableWorkStatus();
    const current = [...status.open, ...status.blocked]
      .find((item) => Number(item.id) === initialOpenWorkIds[0]);
    if (!current) return null;
    const proof = [...toolResults].reverse().find((tool) => (
      autonomyProofRelevantToWork(current, tool)
      && !['toon_werk', 'lijst_bestanden', 'werk_bijhouden', 'signalen_afhandelen', 'stuur_bericht'].includes(tool.name)
    ));
    if (!proof) return null;
    const updated = updateDurableWork(current.id, {
      status: current.status,
      evidence: checkpointEvidence(proof) || undefined,
      lastAction: proof.name,
    });
    return workItemMateriallyChanged(current, updated) ? updated : null;
  };
  const answerTurn = (reply, result = {}) => {
    let answer = String(reply || '').trim();
    let contribution = contributionFromTools(toolResults, mode, userText, {
      durable: durableExecution,
      plannedRequiredTools: requiredTools,
    });
    if (contribution && mode === 'autonomy'
      && !ensureAutonomyCheckpointAfterReport()) contribution = null;
    let continuation = null;
    if (mode === 'autonomy' && !contribution) {
      const incomplete = incompleteAutonomousWrite(toolResults)
        || (autonomyArtifactReportPending(toolResults)
          ? completedAutonomousArtifact(toolResults)
          : null);
      const path = String(actionData(incomplete)?.path || incomplete?.args?.pad || '').trim();
      if (path) {
        const readyButUnreported = completedAutonomousArtifact(toolResults) === incomplete;
        const nextStep = autonomyContinuationStep(path, readyButUnreported, toolResults);
        const latestProof = [...toolResults].reverse().find((tool) => (
          tool.ok && tool.verified && tool.name === 'voer_uit'
        )) || [...toolResults].reverse().find((tool) => (
          tool.ok && tool.verified && !['werk_bijhouden', 'stuur_bericht'].includes(tool.name)
        ));
        if (initialOpenWorkIds.length) {
          const before = durableWorkStatus().open.find((item) => (
            Number(item.id) === Number(initialOpenWorkIds[0])
          ));
          const updated = updateDurableWork(initialOpenWorkIds[0], {
            status: 'open',
            nextStep,
            evidence: checkpointEvidence(latestProof) || undefined,
            lastAction: latestProof?.name || 'werk_bijhouden',
          });
          continuation = workItemMateriallyChanged(before, updated) ? updated : null;
        } else {
          continuation = createWorkItem({
            source: 'autonomy',
            title: readyButUnreported
              ? `Meld gecontroleerd werkstuk: ${path.split('/').at(-1)}`
              : `Controleer autonoom werkstuk: ${path.split('/').at(-1)}`,
            goal: readyButUnreported
              ? `Maak het bewezen resultaat van ${path} kort en begrijpelijk zichtbaar voor het gezin.`
              : `Maak ${path} aantoonbaar werkend en laat geen onbewezen tussenversie als afgerond resultaat staan.`,
            nextStep,
            visibleToFamily: true,
          });
          const continuationEpisode = ensureEpisodeForWorkItem(continuation, {
            originKind: 'self',
            originChannel: 'autonomy',
          });
          if (!durableGoalEpisode) durableGoalEpisode = continuationEpisode;
        }
      }
      if (!continuation) continuation = checkpointAutonomyProgress();
    }
    if (contribution) {
      const evidence = readableEvidence(
        contribution.output || contribution.evidence?.summary || '',
      ).slice(0, 1_200);
      const topic = mode === 'autonomy'
        ? contribution.name === 'stuur_bericht'
          ? `Uit zichzelf gemeld: ${evidence}`
          : `Zelfstandig afgerond: ${evidence}`
        : `Opdracht van ${person}: ${userText.slice(0, 240)}`;
      logTimeline('contribution', topic.slice(0, 500), {
        internalOnly: true,
        turnId,
        source: mode === 'autonomy' ? 'bijdrage' : 'opdracht',
        owner: mode === 'autonomy' ? null : person,
        evidence,
        lastAction: contribution.name,
        status: durableGoalEpisode?.delivery_policy === 'required'
          && !episodeHasDelivery(durableGoalEpisode.id)
          ? 'delivery_pending'
          : 'done',
        syntheticTest,
      });
      if (durableCommand) {
        const deliveryRequired = durableGoalEpisode?.delivery_policy === 'required';
        const deliverySent = durableGoalEpisode
          ? episodeHasDelivery(durableGoalEpisode.id)
          : false;
        const waitingForDelivery = deliveryRequired && !deliverySent;
        const nextAction = waitingForDelivery
          ? mode === 'autonomy'
            ? 'Bezorg het gecontroleerde resultaat kort aan het gezin.'
            : 'Bezorg het gecontroleerde resultaat kort aan de oorspronkelijke verzoeker.'
          : '';
        const work = {
          nextStep: nextAction,
          evidence,
          lastAction: contribution.name,
          result: answer || evidence,
        };
        if (durableGoalEpisode) {
          const transition = transitionGoalEpisode(durableGoalEpisode.id, {
            state: waitingForDelivery
            ? 'delivery_pending'
            : deliverySent || (mode === 'autonomy'
              && durableGoalEpisode.delivery_policy !== 'required')
              ? 'closed'
              : 'verifying',
            checkpoint: evidence, proofSummary: evidence, nextAction,
            sessionRef: turnId, work,
          });
          durableGoalEpisode = transition.episode;
          durableCommand = transition.workItem;
        } else {
          durableCommand = updateDurableWork(durableCommand.id, {
            ...work, status: waitingForDelivery ? 'open' : 'done',
            proofAccepted: !waitingForDelivery,
          });
        }
      }
    } else if (durableCommand) {
      const currentCommand = db.prepare('SELECT * FROM eva_work_items WHERE id=?')
        .get(durableCommand.id) || durableCommand;
      if (['done', 'cancelled'].includes(currentCommand.status)) {
        durableCommand = currentCommand;
        if (durableGoalEpisode && durableGoalEpisode.delivery_policy !== 'required'
          && !['closed', 'abandoned'].includes(durableGoalEpisode.state)) {
          durableGoalEpisode = updateGoalEpisode(durableGoalEpisode.id, {
            state: currentCommand.status === 'done' ? 'closed' : 'abandoned',
            checkpoint: currentCommand.evidence || currentCommand.result || answer || '',
            proofSummary: currentCommand.evidence || '',
            nextAction: '', wakeTs: '', sessionRef: turnId,
          });
        }
      } else {
        const checkpoint = [...toolResults].reverse().find((tool) => (
          tool.ok && tool.verified && tool.name !== 'werk_bijhouden'
        ));
        if (checkpoint) {
          const evidence = checkpointEvidence(checkpoint);
          const work = {
            evidence: evidence || undefined,
            lastAction: checkpoint.name,
            result: answer || undefined,
          };
          if (durableGoalEpisode) {
            const transition = transitionGoalEpisode(durableGoalEpisode.id, {
              state: 'waiting', checkpoint: evidence || answer || '',
              nextAction: durableCommand.next_step || '', sessionRef: turnId, work,
            });
            durableGoalEpisode = transition.episode;
            durableCommand = transition.workItem;
            continuation = transition.workItem;
          } else continuation = updateDurableWork(durableCommand.id, { ...work, status: 'open' });
        }
      }
    }
    const progress = mode === 'autonomy'
      ? autonomyProgressFromTools(toolResults, continuation)
      : null;
    const noActionReason = mode === 'autonomy' && !contribution && !progress
      ? autonomyNoActionReason(answer, toolResults)
      : null;
    if (progress) {
      answer = '';
      logTimeline('decision', `Zelfstandig werk voortgezet: ${progress.summary}`, {
        internalOnly: true,
        autonomous: true,
        turnId,
        outcome: 'progress',
        tool: progress.tool,
        tools: toolResultSummary(toolResults),
      });
    } else if (noActionReason) {
      answer = '';
      logTimeline('decision', `Geen zelfstandige actie: ${noActionReason}`, {
        internalOnly: true,
        autonomous: true,
        turnId,
        outcome: 'no_action',
        tools: toolResultSummary(toolResults),
      });
    }
    runtime.lastTurn = {
      turnId,
      channel,
      sender: person,
      mode,
      text: userText.slice(0, 300),
      reply: answer.slice(0, 600),
      noActionReason,
      progress,
      tools: toolResultSummary(toolResults),
      durationMs: Math.round(performance.now() - started),
      finishedAt: new Date().toISOString(),
    };
    if (mode === 'autonomy') runtime.lastAutonomyTurn = runtime.lastTurn;
    else runtime.lastInteractiveTurn = runtime.lastTurn;
    runtime.lastTurn.reply = answer.slice(0, 600);
    try {
      closeCognitiveEpisode(cognitiveEpisode.id, {
        status: result.superseded || result.yielded
          ? 'interrupted'
          : continuation ? 'waiting' : 'completed',
        resultClass: contribution?.name
          || progress?.tool
          || (noActionReason ? 'no_action' : answer ? 'reply' : 'silent'),
        evidence: {
          tools: toolResultSummary(toolResults),
          contribution: contribution?.evidence?.summary || null,
        },
        effect: {
          replied: Boolean(answer),
          replySummary: answer.slice(0, 600),
          madeProgress: progress?.made_progress === true,
          continuationId: continuation?.id || null,
        },
      });
    } catch (error) {
      logTimeline('system', `Cognitieve episode kon niet worden gesloten: ${errMsg(error)}`, {
        internalOnly: true,
        turnId,
      });
    }
    return {
      reply: answer,
      turnId,
      channel,
      sender: person,
      conversationId,
      tools: toolResults,
      contribution: contribution ? {
        tool: contribution.name,
        evidence: contribution.evidence || null,
      } : null,
      continuation: continuation ? { id: continuation.id, title: continuation.title } : null,
      goalEpisodeId: durableGoalEpisode?.id || Number(messageContext?.goalEpisodeId) || null,
      workItemId: durableCommand?.id || null,
      originMessageId: inboundMessageId,
      progress,
      noActionReason,
      yielded: result.yielded === true,
      exhausted: result.exhausted === true,
      superseded: result.superseded === true,
      durationMs: runtime.lastTurn.durationMs,
      syntheticTest,
    };
  };

  runtime.active += 1;
  if (mode === 'chat') runtime.activeInteractive = Number(runtime.activeInteractive || 0) + 1;
  setActivity(mode === 'autonomy' ? 'zoekt zelfstandig iets nuttigs uit' : `praat met ${person}`, {
    turnId,
    channel,
    sender: person,
    text: userText.slice(0, 240),
    startedAt: new Date().toISOString(),
  });
  let conversationOnly = false;

  try {
    if (persistInbound) {
      if (messageContext?.identityTrusted === true && !syntheticTest) upsertPerson(person);
      if (!syntheticTest) {
        const inboundMessage = addMessage(channel, person, 'user', text, conversationId);
        inboundMessageId ||= String(inboundMessage.id);
      } else {
        inboundMessageId ||= `synthetic:${turnId}`;
      }
      logTimeline('chat_in', `${person}: ${userText}`, {
        channel,
        conversationId,
        sender: person,
        messageContext,
        replyTo,
      });
      const interactionLesson = syntheticTest ? null : applyInteractionFeedback(userText);
      if (interactionLesson && messageContext?.identityTrusted === true) {
        try {
          recordRelationshipFeedback({
            person,
            kind: interactionLesson === 'warmte' ? 'appreciation' : 'correction',
            topic: 'samenwerking',
            summary: interactionLesson === 'warmte'
              ? 'Een korte, concrete reactie landde goed; behoud die beknoptheid en relevantie.'
              : 'De vorige reactie hielp niet; herhaal geen leeg of mislukt antwoord en geef alleen een echte uitkomst of één concreet ontbrekend feit.',
          });
        } catch {
          // Een onbekende of nog niet gekoppelde identiteit wordt niet stil aangemaakt.
        }
      }
      const socialLesson = syntheticTest ? null : relationshipLessonFromText(userText);
      if (socialLesson && messageContext?.identityTrusted === true) {
        try {
          recordRelationshipFeedback({ person, ...socialLesson });
        } catch {
          // De relatie wordt alleen bijgewerkt voor een reeds bekende identiteit.
        }
      }
    }

    if (supersedingTelegramMessage()) {
      return answerTurn('', { superseded: true, yielded: true });
    }

    if (durableInteraction && !recapOnly && !syntheticTest) {
      durableCommand = continuationWork;
      if (!continuationRequest) {
        ({ command: durableCommand, episode: durableGoalEpisode } = db.transaction(() => {
          const command = createWorkItem({
            source: channel,
            owner: person,
            title: `Opdracht: ${userText.slice(0, 210)}`,
            goal: userText,
            nextStep: 'Voer de opdracht uit, controleer het resultaat en geef het bewezen eindresultaat terug.',
            visibleToFamily: true,
            definitionOfDone: turnContract.outcome || userText,
            priority: 75,
          });
          const existing = episodeForWork(command.id);
          if (existing) {
            attachEpisodeOrigin(existing.id, {
              originKind: 'user', originChannel: channel,
              originConversationId: conversationId,
              originMessageId: inboundMessageId,
              requester: person,
            });
            return { command, episode: existing };
          }
          const episode = createGoalEpisode({
            title: `Opdracht van ${person}`,
            outcome: userText,
            definitionOfDone: turnContract.outcome || userText,
            originKind: 'user',
            originChannel: channel,
            originConversationId: conversationId,
            originMessageId: inboundMessageId,
            requester: person,
            deliveryPolicy: 'required',
            deliveryTarget: person,
            nextAction: 'Voer de eerstvolgende concrete stap uit en controleer het gevolg.',
          });
          return { command, episode: bindEpisodeToWork(episode.id, command.id) };
        })());
      } else if (durableCommand) {
        durableGoalEpisode = episodeForWork(durableCommand.id);
        if (durableGoalEpisode) attachEpisodeOrigin(durableGoalEpisode.id, {
          originKind: 'user', originChannel: channel,
          originConversationId: conversationId,
          originMessageId: inboundMessageId,
          requester: person,
        });
      }
    }

    const preparedDelivery = mode === 'autonomy'
      ? pendingDeliveryForWork(durableCommand)
      : null;
    if (preparedDelivery) {
      const preparedComposition = await conversationalAutonomousDelivery({
        text: preparedDelivery.text,
        recipient: preparedDelivery.recipient,
        evidence: preparedDelivery.facts
          || durableGoalEpisode?.proof_summary || durableCommand?.evidence || '',
        toolResults,
        required: durableGoalEpisode?.delivery_policy === 'required',
        conversationId: durableGoalEpisode?.origin_conversation_id || null,
      });
      // Bestaande verplichte afleveringen worden nooit stil weggegooid. Een
      // discretionair resultaat mag bij herbeoordeling wel intern blijven.
      if (preparedComposition.decision === 'silent') {
        resolvePreparedDeliverySilently(
          durableCommand,
          durableGoalEpisode,
          preparedComposition.reason,
        );
        return answerTurn('', { toolFinal: true, deliverySuppressed: true });
      }
      const args = {
        tekst: preparedComposition.text,
        ...(preparedDelivery.recipient ? { aan: preparedDelivery.recipient } : {}),
      };
      setActivity('bezorgt een eerder voorbereid resultaat', {
        ...runtime.current,
        tool: 'stuur_bericht',
      });
      toolNameAttempts.set('stuur_bericht', 1);
      toolAttempts.set(`stuur_bericht:${JSON.stringify(args)}`, 1);
      const output = await executeForTurn('stuur_bericht', args, {
        mode,
        sender: person,
        turnId,
        messageContext,
        userText,
        priorToolResults: toolResults,
        activeWorkId: boundAutonomyWorkId,
        activeGoalEpisodeId: durableGoalEpisode?.id || Number(messageContext?.goalEpisodeId) || null,
        activeExplorationId: boundAutonomyExplorationId,
        cognitiveEpisodeId: cognitiveEpisode.id,
        preparedDeliveryDedupeKey: preparedDelivery.dedupeKey,
      });
      const action = recordToolOutput('stuur_bericht', args, output);
      return answerTurn(action.ok && action.verified ? preparedComposition.text : '', {
        toolFinal: true,
      });
    }

    // Een expliciete, volledig bepaalde Voice PE-opdracht hoeft niet eerst door
    // een modelronde. De parser bepaalt alleen kanaal/doel/letterlijke tekst;
    // vrije of vage gesprekstekst blijft bij Eva en gebruikt de gewone toolloop.
    if (mode === 'chat' && !syntheticTest && voiceDirective?.requested
      && voiceDirective.text && voiceDirective.target) {
      logTimeline('turn_contract', `${turnContract.kind}: ${turnContract.outcome}`, {
        internalOnly: true,
        turnId,
        mode,
        kind: turnContract.kind,
        requiredTools,
        offeredTools: ['spreek_in_huis'],
        actionIntent: turnPlan.actionIntent,
        effectPolicy: turnPlan.effectPolicy,
        actionContext: {
          domain: 'voice',
          target: voiceDirective.target,
          text: voiceDirective.text,
          inherited: voiceDirective.inherited,
        },
        context: { direct: true },
        effort: turnContract.effort || null,
      });
      const args = { tekst: voiceDirective.text, doel: voiceDirective.target };
      setActivity('spreekt direct via de Voice PE', {
        ...runtime.current,
        tool: 'spreek_in_huis',
      });
      toolNameAttempts.set('spreek_in_huis', 1);
      toolAttempts.set(`spreek_in_huis:${JSON.stringify(args)}`, 1);
      const output = await executeForTurn('spreek_in_huis', args, {
        mode,
        sender: person,
        turnId,
        messageContext,
        userText,
      });
      const action = recordToolOutput('spreek_in_huis', args, output);
      if (action.ok && action.verified) {
        return answerTurn(`Ja, via de Voice PE in de ${voiceDirective.target}.`, {
          toolFinal: true,
        });
      }
      const failure = humanResultText(output) || 'De omroepactie is niet bevestigd.';
      return answerTurn(failure, { toolFinal: true });
    }

    const confirmedName = mode === 'chat'
      ? confirmedRecognitionName(userText, replyTo)
      : null;
    if (confirmedName) {
      return answerTurn(`Mooi, dan zat ik goed: dat was ${confirmedName}.`, { toolFinal: true });
    }

    const identityName = mode === 'chat' && !syntheticTest && replyEvidence?.imageDataUrl
      ? identityNameFromText(userText, person)
      : null;
    if (identityName) {
      const args = { naam: identityName };
      setActivity('onthoudt wie er op de foto staat', {
        ...runtime.current,
        tool: 'leer_gezicht_van_foto',
      });
      toolNameAttempts.set('leer_gezicht_van_foto', 1);
      toolAttempts.set(`leer_gezicht_van_foto:${JSON.stringify(args)}`, 1);
      const output = await executeForTurn('leer_gezicht_van_foto', args, {
        mode,
        sender: person,
        turnId,
        replyEvidence,
        replyTo,
        messageContext,
        userText,
      });
      const action = recordToolOutput('leer_gezicht_van_foto', args, output);
      const parsed = JSON.parse(String(output));
      const reply = parsed.human_message
        || parsed.evidence?.summary
        || (action.ok && action.verified
          ? `Dank je, dat is ${identityName}. Ik heb de koppeling gecontroleerd en bewaard.`
          : `Duidelijk, dat is ${identityName}. Het gezicht zelf kon ik uit dit beeld niet betrouwbaar leren.`);
      return answerTurn(reply, { toolFinal: true });
    }

    const directHome = mode === 'chat' && !syntheticTest
      && requiredTools.length === 1 && requiredTools[0] === 'bedien_huis'
      ? turnPlan.directAction
      : null;
    if (directHome) {
      setActivity('bedient het huis', { ...runtime.current, tool: 'bedien_huis' });
      toolNameAttempts.set('bedien_huis', 1);
      toolAttempts.set(`bedien_huis:${JSON.stringify(directHome)}`, 1);
      const output = await executeForTurn('bedien_huis', directHome, {
        mode,
        sender: person,
        turnId,
        messageContext,
        userText,
      });
      const action = recordToolOutput('bedien_huis', directHome, output);
      if (action.ok && action.verified) {
        const parsed = JSON.parse(String(output));
        const detailed = humanResultText(parsed);
        const reply = parsed?.needs_followup
          ? detailed
          : renderHouseActionResult({
            target: directHome.doel,
            action: directHome.actie,
            ok: action.ok,
            verified: action.verified,
            changed: action.changed,
          });
        return answerTurn(reply, { toolFinal: true });
      }
    }

    const hasPendingSignals = Array.isArray(messageContext?.pendingSignals)
      && messageContext.pendingSignals.length > 0;
    const subjectiveHouseExperience = mode === 'chat'
      && /\b(?:voelt|aanvoelt|sfeer|gezellig|ongezellig|prettig|onprettig|fijn|onrustig|benauwd|kil|leeg)\b/i.test(userText)
      && !/\b(?:temperatuur|hoe\s+warm|hoe\s+koud|welke\s+lamp|wat\s+staat|staat\s+.*\s+aan|staat\s+.*\s+uit)\b/i.test(userText);
    const includeHomeSnapshot = mode === 'autonomy'
      || ['household_contribution', 'voice_action', 'repair'].includes(turnContract.kind)
      || (!subjectiveHouseExperience
        && /\b(?:huis|thuis|huishoud|kamer|lamp|licht|deur|raam|camera|frontcamera|temperatuur|verwarming|woonkamer|overkapping|speaker|voice|home\s+assistant|ha-verbinding|eriks?|robot(?:stofzuiger)?|stofzuiger(?:robot)?)\b/i.test(`${userText}\n${continuationAnchor}`));
    const allowHouseTools = includeHomeSnapshot || hasOpenDurableWork
      || requiredTools.some((name) => toolHasTag(name, 'house'));
    const mayNeedLiveEvidence = /(?:^|[.!?]\s*)(?:zoek|onderzoek|uitzoek(?:en)?|vergelijk|controleer|bekijk|lees|vind|diagnosticeer)\b/i.test(userText)
      || /\b(?:actueel|vandaag|nu|website|url|link|bron(?:nen)?|documentatie|bestand|project|gesprek(?:ken)?|tijdlijn|status|inbox)\b/i.test(userText);
    conversationOnly = mode === 'chat'
      && turnContract.kind === 'conversation'
      && !explicitTool && !requiredTools.length && !projectWork && !recapOnly
      && !continuationRequest
      && !mayNeedLiveEvidence;
    const [homeContext, memories] = await Promise.all([
      homeContextText({
        query: userText,
        includeOverview: includeHomeSnapshot,
        includeWeather: includeHomeSnapshot,
      }).catch(() => 'Home Assistant-context kon niet worden gelezen.'),
      Promise.resolve(mode === 'autonomy'
        ? autonomousMemoryContextText()
        : memoryContextText(userText, person)),
    ]);
    const familyContext = householdContextText({
      subject: mode === 'chat' ? person : null,
      includeAllMemberDetails: mode === 'autonomy',
    });
    const selfContext = selfContinuityContextText({
      mode,
      person,
      query: mode === 'autonomy' ? `${text}\n${durableCommand?.goal || ''}` : userText,
    });
    const includeCrossChannel = mode === 'chat' && (
      !!replyTo || /\b(?:alweer|opnieuw|hiervoor|daarnet|net zei|vorige)\b/i.test(userText)
    );
    const otherConversation = includeCrossChannel
      ? otherConversationContext(person, conversationId)
      : '';
    const continuationScope = `${continuationAnchor}\n${userText}`;
    const continuationNeedsCode = /\b(?:code|script|programma|software|website|app|tool|prototype|uitvoer\w*|test\w*)\b/i.test(continuationScope);
    const continuationNeedsResearch = /\b(?:zoek|onderzoek|bron(?:nen)?|verifi(?:eer|ëren)|controleer\s+(?:online|actueel)|marktprijs|agentmail|openmail|website|site|online|api|cli|documentatie|account|aanmeld\w*|registreer\w*|e-?mail|inbox)\b/i.test(continuationScope);
    const includeWorkContext = mode === 'autonomy' || (durableInteraction && !continuationRequest)
      || !!continuationWork || recapOnly
      || ['earning_route', 'household_contribution'].includes(turnContract.kind)
      || /\b(?:werk|klus|project|afmaken|verder|verdien|inkomsten|bijdrag)\b/i.test(userText);
    const durableWork = includeWorkContext
      ? workContextText(mode === 'autonomy' ? initialOpenWorkItems : null)
      : '';
    const portfolioContext = mode === 'autonomy' || durableInteraction
      ? portfolioContextText({
        episodeId: durableGoalEpisode?.id || messageContext?.goalEpisodeId || null,
        workItemId: durableCommand?.id || initialOpenWorkIds[0] || null,
      })
      : '';
    const capabilityContext = capabilityContextText(mode === 'autonomy' ? '' : userText);
    const curiosityContext = mode === 'autonomy' ? attentionContextText() : '';
    const autonomousEvidenceContext = mode === 'chat'
      ? relevantExplorationContextText(`${continuationAnchor}\n${userText}`)
      : '';
    const priorEvidence = mode === 'chat'
      ? conversationEvidenceText(conversationId)
      : '';
    const allowedChatTools = mode === 'chat'
      ? turnPlan.allowedToolNames || chatToolNamesFor(requiredTools)
      : null;
    const tools = recapOnly || conversationOnly
      ? []
      : toolsForTurn({ mode }).filter((tool) => (
        (!conversationOnly || toolEffect(tool.name, {}) === 'read')
        &&
        (turnPlan.actionIntent !== 'forbid' || requiredTools.includes(tool.name))
        &&
        (!allowedChatTools || allowedChatTools.has(tool.name))
        && toolAllowedByPolicy(tool.name, {}, turnPlan.effectPolicy)
        && (!syntheticTest || toolSyntheticAllowed(tool.name))
        && (channel === 'telegram' || messageContext?.identityTrusted === true || tool.name !== 'zoek_gesprek')
        && (allowHouseTools || !toolHasTag(tool.name, 'house'))
        && (!continuationRequest || continuationNeedsCode || tool.name !== 'voer_uit')
        && (!continuationRequest || continuationNeedsResearch || tool.name !== 'zoek_internet')
        && (!continuationRequest || continuationWork || !['toon_werk', 'werk_bijhouden'].includes(tool.name))
        && (!reflectiveQuestion || !toolIsDurable(tool.name))
        && (!readOnlyRequest || !toolIsDurable(tool.name))
        && (mode !== 'autonomy' || autonomyToolAllowed(tool.name, {
          hasOpenWork: hasOpenDurableWork,
          hasPendingSignals,
        }))
        && (mode !== 'autonomy' || delegatedWorkAllowsTool(tool.name, durableCommand))
        && autonomyLaneAllows(tool.name, autonomyLane, toolResults)
      ));
    const missingPlannedTools = syntheticTest ? [] : requiredTools.filter(
      (required) => !tools.some((tool) => tool.name === required),
    );
    const forbiddenRequiredTools = requiredTools.filter(
      (required) => !toolAllowedByPolicy(required, {}, turnPlan.effectPolicy),
    );
    if (missingPlannedTools.length || forbiddenRequiredTools.length) {
      logTimeline('turn_planning_error', 'Het beurtplan bevatte een onmogelijke toolverplichting.', {
        internalOnly: true,
        turnId,
        mode,
        missingPlannedTools,
        forbiddenRequiredTools,
        actionIntent: turnPlan.actionIntent,
      });
      throw new Error(`ongeldig beurtplan: ${[...missingPlannedTools, ...forbiddenRequiredTools].join(', ')}`);
    }
    logTimeline('turn_contract', `${turnContract.kind}: ${turnContract.outcome}`, {
      internalOnly: true,
      turnId,
      mode,
      kind: turnContract.kind,
      requiredTools,
      offeredTools: tools.map((tool) => tool.name),
      actionIntent: turnPlan.actionIntent,
      effectPolicy: turnPlan.effectPolicy,
      actionContext: voiceDirective?.requested ? {
        domain: 'voice',
        target: voiceDirective.target,
        text: voiceDirective.text,
        inherited: voiceDirective.inherited,
      } : null,
      context: {
        home: includeHomeSnapshot,
        crossChannel: includeCrossChannel,
        durableWork: includeWorkContext,
      },
      effort: turnContract.effort || null,
    });
    const instructions = buildInstructions({
      sender: person,
      channel,
      homeContext,
      familyContext,
      memoryContext: memories,
      mode,
      extra: [
        (mode === 'autonomy' || explicitTool || requiredTools.length || durableInteraction || recapOnly)
          ? `Interne uitvoergrens (${turnContract.kind}): gewenste uitkomst: ${turnContract.outcome}. Bewijsgrens: ${turnContract.proof}. Dit stuurt alleen je handelen; neem deze termen of structuur niet over in je antwoord.`
          : 'Dit is in de eerste plaats een gesprek, niet een uit te voeren contract. Reageer vanuit de lopende relatie en maak er alleen een taak van wanneer de ander werkelijk om een handeling vraagt.',
        selfContext,
        cognitiveContextText(),
        mode === 'autonomy' || /\b(?:wat|welke|hoe).{0,50}\bkun(?:nen|t)?\s+(?:je|jij)|\bmogelijkheden\b|\bzelfstandig\b/i.test(userText)
          ? `Machineleesbare runtimecapaciteit: autonomie=${config.autonomyLevel}; zelfstandige aandacht elke ${config.autonomyIntervalMinutes} minuten; huisbediening=${config.allowHomeControl ? 'aan' : 'uit'}; open onderzoek=${config.autonomyOpenExploration ? 'aan' : 'uit'}; code in geïsoleerde werkruimte=${config.autonomyCodeExecution ? 'aan' : 'uit'}. In full mag je zonder nieuwe aanleiding eigen doelen beginnen. Dit zijn jouw eigen stabiele vermogens. Een tijdelijke technische fout is een storing in wat jij probeert te doen, nooit bewijs voor een andere Eva, een kanaal-Eva of een los systeem.`
          : '',
        replyTo ? `Dit bericht is een reply op: ${JSON.stringify(replyTo).slice(0, 1200)}.` : '',
        replyEvidence?.description ? `Eerdere beeldbeschrijving is alleen context, geen vervanging voor pixels: ${replyEvidence.description}` : '',
        mode === 'chat'
          ? `Laat interne regulatie alleen tempo, risico en toon kleuren; benoem geen interne stemming. ${moodActionGuidance()} Voor een expliciete opdracht is een tekstantwoord zonder passende tool geen uitvoering. Werk vanuit het bedoelde resultaat, niet vanuit één technische route. Een mislukte route, timeout, ontbrekende SDK of interne beperking blijft interne diagnose zolang je veilig kunt retryen, de actuele toestand kunt controleren, documentatie kunt lezen, een andere route kunt gebruiken of zelfstandig voorbereidend werk kunt doen. Vraag pas iets aan de mens als actuele uitvoer bewijst dat precies die menselijke handeling onvermijdelijk is; vraag dan alleen dat ene concrete ding. Wanneer officiële documentatie beslissend was voor een technisch antwoord, noem de directe bronlink kort zodat de huisgenoot de conclusie kan controleren.`
          : `Een autonome beurt is geslaagd als er aantoonbaar nuttig werk is gedaan; stilte is beter dan een zwak voorstel. Laat interne regulatie alleen keuzes en tempo kleuren, niet je zelfbeschrijving. ${moodActionGuidance()}`,
        mode === 'autonomy'
          ? `Dit is de afgelopen zeven dagen al werkelijk gedaan of gemeld:\n${contributionContextText(new Date(), 7)}\nHerhaal hetzelfde resultaat niet. ${config.autonomyFull ? 'Je kiest zelfstandig doelen en handelingen. Je hoeft niet op een verzoek, open werkpunt of sensorsignaal te wachten; je mag ook zelf een routine, onderzoek, huisactie of werkstuk beginnen en afronden.' : 'Kies bij vrije aandacht ook buiten het huis: een open gezinsvraag, gericht internetonderzoek of een bruikbaar en controleerbaar werkstuk.'} Heeft bestaand werk een bewezen eindresultaat, meld dat dan eerst kort aan het gezin en sluit of actualiseer pas daarna het werkpunt.`
          : '',
        durableWork
          ? `Alleen het voor deze beurt geselecteerde duurzame werkboek:\n${durableWork}\nKoppel vervolgtaal eerst aan de directe gespreksbelofte; pak nooit alleen omdat het ouder of geblokkeerd is een ander werkpunt.`
          : '',
        portfolioContext,
        capabilityContext
          ? `Actieve, eerder geteste vaardigheden en add-ons die inhoudelijk bij deze beurt passen:\n${capabilityContext}\nDit is procedurekennis, geen uitbreiding van gereedschapsrechten of bewijs.`
          : '',
        priorEvidence
          ? `Bewezen handelingen uit eerdere beurten van precies dit gesprek:\n${priorEvidence}\nDit is Eva's eigen uitvoerbewijs. Trek een bewezen handeling niet in zonder nieuw, tegenstrijdig bewijs.`
          : '',
        recapOnly
          ? `Dit is een terugblik- of werkstatusvraag. Voer niets opnieuw uit en maak geen nieuw werk aan. Vat alleen bestaand bewijs samen:\n${contributionContextText()}`
          : '',
        durableInteraction
          ? 'Dit is een meerstapsopdracht. Een zoekresultaat of plan is alleen grondstof: ga in dezelfde beurt door naar een concreet bruikbaar resultaat, controleer het, en houd de opdracht open als het eindresultaat nog niet aantoonbaar bestaat.'
          : '',
        workspaceEditWork
          ? `Dit is een wijziging aan een bestaand bestand. Lees eerst ${requestedWorkspaceEditPath(userText)}, wijzig daarna exact datzelfde bestand (geen patch-, voorstel- of kopiebestand) en voer vervolgens de gevraagde controle uit.`
          : '',
        continuationRequest
          ? `Dit is een vervolgopdracht, geen nieuw onderwerp. ${durableCommand ? `Werk verder aan werkpunt #${durableCommand.id} en maak de opgeslagen volgende stap werkelijk af.` : `De directe referentie is Eva's vorige antwoord: ${String(continuationAnchor).slice(0, 1_600)}`} Gebruik de betekenis en het beoogde eindresultaat daarvan; herhaal geen onderzoek of uitvoering die daarvoor niet nodig is. Stop niet bij een plan of uitleg.`
          : '',
        otherConversation
          ? `Recente draad van ${person} via een ander kanaal, alleen gebruiken als die aantoonbaar bij dit gesprek hoort:\n${otherConversation}`
          : '',
        curiosityContext,
        autonomousEvidenceContext,
      ].filter(Boolean).join('\n'),
    });
    const input = mode === 'chat'
      ? conversationInput({
        channel,
        conversationId,
        sender: person,
        messageContext,
        replyEvidence,
        // Houd de levende draad dichtbij. Ouder operationeel werk komt via
        // bewijs/episodecontext terug, niet als een lange reeks zakelijke
        // assistant-antwoorden die Eva onbedoeld gaat nadoen.
        limit: recapOnly || conversationOnly ? 10 : 8,
      })
      : autonomyInput(text);
    if (mode === 'chat' && syntheticTest) {
      input.push(msg('user', `[${channel}] ${person}: ${userText}`));
    }

    const result = await runAgent({
      instructions,
      input,
      tools,
      model: channel === 'voice' ? config.modelVoice : config.model,
      effort: channel === 'voice'
        ? config.reasoningVoice
        : mode === 'autonomy'
          ? config.reasoningAutonomy
          : turnContract.effort || (longWork ? config.reasoningCode : config.reasoningChat),
      escalatedEffort: config.reasoningCode,
      finalEffort: mode === 'chat' ? 'medium' : 'low',
      finalContextReminder: mode === 'chat'
        ? `De uitvoering is nu alleen achtergrond. Keer terug naar wat ${person} werkelijk vroeg: ${userText.slice(0, 900)}\nGeef alleen de gevraagde uitkomst, in diens gevraagde toon en lengte. Spreek als dezelfde Eva die de handeling deed; geen technisch werkverslag, projectplan of opsomming van wat je later nog zou kunnen doen.`
        : '',
      executeTool: async (name, args, execution = {}) => {
        const newerMessage = supersedingTelegramMessage();
        if (newerMessage) {
          const error = new Error(`deze beurt is ingehaald door Telegram-bericht ${newerMessage.message_id}`);
          error.code = 'EVA_TURN_SUPERSEDED';
          throw error;
        }
        if (syntheticTest && name === 'schrijf_bestand') {
          const requested = String(args?.pad || 'test-artifact.txt');
          args = {
            ...args,
            pad: path.join(
              config.workspaceDir,
              'test-artifacts',
              turnId.replace(/[^a-zA-Z0-9._-]+/g, '_'),
              path.basename(requested),
            ),
          };
        }
        if (mode === 'autonomy') {
          const activeExploration = Number.isFinite(Number(boundAutonomyExplorationId))
            ? explorationById(boundAutonomyExplorationId)
            : null;
          if (activeExploration?.status === 'evaluating'
            && !['verkenning_bijhouden', 'signalen_afhandelen'].includes(name)) {
            const output = JSON.stringify({
              contract: 'ActionResult/v1', tool: name, ok: false, changed: false, verified: false,
              needs_followup: true,
              error: `Het zelfgekozen actiebudget van verkenning #${activeExploration.id} is bereikt. Beoordeel of sluit die verkenning nu; voer geen extra inhoudelijke stap uit.`,
            });
            recordToolOutput(name, args, output);
            return output;
          }
          const traceExempt = initialOpenWorkIds.length > 0
            || (Array.isArray(messageContext?.pendingSignals) && messageContext.pendingSignals.length > 0);
          const substantive = !['verkenning_bijhouden', 'toon_werk', 'signalen_afhandelen'].includes(name);
          const substantiveCount = toolResults.filter((tool) => (
            !['verkenning_bijhouden', 'toon_werk', 'signalen_afhandelen'].includes(tool.name)
          )).length;
          if (!traceExempt && !boundAutonomyExplorationId && substantive && substantiveCount >= 1) {
            const output = JSON.stringify({
              contract: 'ActionResult/v1', tool: name, ok: false, changed: false, verified: false,
              needs_followup: true,
              error: 'Leg nu eerst je eigen vraag, hypothese, verwachte waarneming, actiebudget en stopregel vast met verkenning_bijhouden. Het onderwerp blijft jouw keuze.',
            });
            recordToolOutput(name, args, output);
            return output;
          }
          autonomyLane ||= autonomyLaneForTool(name);
          if (!autonomyLaneAllows(name, autonomyLane, toolResults)) {
            const output = JSON.stringify({
              contract: 'ActionResult/v1',
              tool: name,
              ok: false,
              changed: false,
              verified: false,
              needs_followup: false,
              error: `Deze zelfstandige beurt werkt al binnen ${autonomyLane}; rond die richting eerst af.`,
            });
            recordToolOutput(name, args, output);
            return output;
          }
          if (!autonomyDiscoveryAllowed(name, toolResults)) {
            const output = JSON.stringify({
              contract: 'ActionResult/v1',
              tool: name,
              ok: false,
              changed: false,
              verified: false,
              needs_followup: false,
              error: 'De informatiegrens van deze zelfstandige beurt is bereikt. Rond het gekozen onderwerp nu af met bestaand bewijs of blijf stil.',
            });
            recordToolOutput(name, args, output);
            return output;
          }
          if (!syntheticTest && !durableGoalEpisode && autonomyEffectRequiresReport(name, args)) {
            const exploration = Number.isFinite(Number(boundAutonomyExplorationId))
              ? explorationById(boundAutonomyExplorationId)
              : null;
            const intendedOutcome = exploration?.question
              || `Voer de zelfgekozen ${name}-handeling alleen uit als het actuele bewijs en de eigen stopregel dat dragen.`;
            ({ command: durableCommand, episode: durableGoalEpisode } = db.transaction(() => {
              const command = createWorkItem({
                source: 'autonomy', owner: null,
                title: `Zelfgekozen bedoeling: ${intendedOutcome.slice(0, 180)}`,
                goal: intendedOutcome,
                nextStep: `Voer ${name} gecontroleerd uit, lees het effect terug en verander bij twijfel van aanpak.`,
                definitionOfDone: exploration?.expected_observation
                  || 'De bedoeling is uitgevoerd of bewust verworpen, met gecontroleerd bewijs en een passend eindbesluit.',
                priority: 80, visibleToFamily: true,
              });
              const episode = createGoalEpisode({
                goalKey: 'household_value', title: command.title,
                outcome: command.goal, definitionOfDone: command.definition_of_done,
                originKind: 'self', originChannel: 'autonomy',
                deliveryPolicy: 'milestone',
                nextAction: command.next_step,
              });
              const bound = bindEpisodeToWork(episode.id, command.id);
              const transition = transitionGoalEpisode(bound.id, {
                state: bound.state,
                work: exploration ? { state: {
                  exploration_id: exploration.id,
                  hypothesis: exploration.hypothesis,
                  stop_rule: exploration.stop_rule,
                } } : {},
              });
              return { command: transition.workItem, episode: transition.episode };
            })());
            boundAutonomyWorkId = durableCommand.id;
          }
        }
        const nameAttempts = Number(toolNameAttempts.get(name) || 0) + 1;
        toolNameAttempts.set(name, nameAttempts);
        const nameLimit = attemptLimitFor(name);
        if (nameAttempts > nameLimit) {
          const output = JSON.stringify({
            contract: 'ActionResult/v1',
            tool: name,
            ok: false,
            changed: false,
            verified: false,
            needs_followup: true,
            error: `${name} is in deze beurt al vaak genoeg geprobeerd. Rond af met het beschikbare bewijs of bewaar een concrete volgende stap.`,
          });
          recordToolOutput(name, args, output);
          return output;
        }
        const signature = `${name}:${JSON.stringify(args || {})}`;
        const attempts = Number(toolAttempts.get(signature) || 0) + 1;
        toolAttempts.set(signature, attempts);
        if (attempts > 2) {
          const output = JSON.stringify({
            contract: 'ActionResult/v1',
            tool: name,
            ok: false,
            changed: false,
            verified: false,
            needs_followup: true,
            error: 'Dezelfde gereedschapsaanroep is in deze beurt al twee keer geprobeerd. Gebruik het bestaande resultaat of kies een andere concrete stap.',
          });
          recordToolOutput(name, args, output);
          return output;
        }
        if (mode === 'autonomy' && !syntheticTest && name === 'stuur_bericht') {
          const composition = await conversationalAutonomousDelivery({
            text: args.tekst,
            recipient: args.aan || durableGoalEpisode?.delivery_target || null,
            evidence: durableGoalEpisode?.proof_summary || durableCommand?.evidence || '',
            toolResults,
            required: durableGoalEpisode?.delivery_policy === 'required',
            conversationId: durableGoalEpisode?.origin_conversation_id || null,
          });
          if (composition.decision === 'silent') {
            const output = JSON.stringify({
              contract: 'ActionResult/v1',
              tool: 'stuur_bericht',
              ok: true,
              changed: false,
              verified: true,
              needs_followup: false,
              evidence: { source: 'communication_judgment', summary: composition.reason },
              human_message: 'Dit resultaat blijft bewust intern; er is geen gezinsbericht verstuurd.',
              data: { suppressed: true, reason: composition.reason },
            });
            recordToolOutput(name, args, output);
            return output;
          }
          args = { ...args, tekst: composition.text };
        }
        setActivity(`gebruikt ${name}`, { ...runtime.current, tool: name });
        const output = await executeForTurn(name, args, {
          mode,
          sender: person,
          turnId,
          replyEvidence,
          replyTo,
          messageContext,
          userText,
          deadlineTs: execution.deadlineTs || null,
          timeoutMs: execution.timeoutMs || null,
          priorToolResults: toolResults,
          offeredToolNames: tools.map((tool) => tool.name),
          activeWorkId: boundAutonomyWorkId,
          activeGoalEpisodeId: durableGoalEpisode?.id || Number(messageContext?.goalEpisodeId) || null,
          activeExplorationId: boundAutonomyExplorationId,
          cognitiveEpisodeId: cognitiveEpisode.id,
        });
        recordToolOutput(name, args, output);
        return output;
      },
      onToolCall: (name) => setActivity(`gebruikt ${name}`, { ...runtime.current, tool: name }),
      maxRounds: mode === 'autonomy'
        ? (config.autonomyFull ? 36 : 10)
        : longWork ? 30 : (requiredTools.length || explicitTool) ? 18 : 10,
      allowToolOnFinalRound: mode === 'autonomy',
      requireToolCall: mode === 'autonomy'
        ? () => autonomyArtifactReportPending(toolResults)
          || autonomyWorkReportPending(toolResults, activeAutonomyWorkIds())
          || autonomyWorkCheckpointPending(toolResults, activeAutonomyWorkIds())
          || autonomyHasRunnableWork(toolResults, initialOpenWorkIds)
        : requiredTools.length
        ? workspaceEditWork
          ? () => workspaceFileEditRemaining(toolResults, requiredTools, userText).length > 0
          : newBuildWork
          ? () => (!buildMissionComplete(toolResults, buildNeedsExecution)
              && !phasedProgrammingBuildStatus(toolResults, userText).complete)
            || requiredTools.some((required) => !turnRequirementSatisfied(required, toolResults, userText))
            || !programmingAcceptanceStatus(toolResults, userText).complete
          : () => requiredTools.some((required) => !turnRequirementSatisfied(required, toolResults, userText))
        : explicitTool
          ? ({ round }) => round === 0
          : false,
      requiredToolsRemaining: requiredTools.length
        ? workspaceEditWork
          ? () => workspaceFileEditRemaining(toolResults, requiredTools, userText)
          : newBuildWork
          ? () => buildMissionRemaining(toolResults, requiredTools, buildNeedsExecution, userText)
          : () => requiredTools.filter(
            (required) => !turnRequirementSatisfied(required, toolResults, userText),
          )
        : null,
      validateFinalText: ({ text: proposedReply }) => (
        syntheticTest && syntheticReplyNeedsPersistentState(proposedReply)
          ? 'Interne conclusiecontrole, geen uitspraak van de bewoner: deze geïsoleerde proef heeft geen duurzaam hervat- of meldingsrecht. Claim geen toekomstige handeling of vastgelegd hervatmoment; geef direct alleen wat in deze beurt werkelijk is aangetoond, zonder erkenning, excuus of verbeterplan.'
          : styleCorrectionPolicyFeedback(proposedReply, userText)
            || conclusionInvestigationFeedback(
              proposedReply, userText, toolResults, turnPlan.actionIntent === 'execute',
            )
            || incompleteRequestedOutcomeFeedback(proposedReply, userText)
      ),
      selectTools: ({ tools: offered }) => {
        const researchReady = toolResults.some((tool) => (
          tool.name === 'zoek_internet' && tool.ok && tool.verified
          && !tool.needs_followup && !tool.partial
        ));
        const researchOutcomeReady = turnRequirementSatisfied(
          'zoek_internet', toolResults, userText,
        );
        let selected = researchReady && researchOutcomeReady
          ? offered.filter((tool) => tool.name !== 'zoek_internet')
          : offered;
        if (mode === 'autonomy' && (
          autonomyArtifactReportPending(toolResults)
          || autonomyWorkReportPending(toolResults, activeAutonomyWorkIds())
        )) {
          selected = selected.filter((tool) => (
            String(tool?.name || tool?.function?.name || '') === 'stuur_bericht'
          ));
        } else if (mode === 'autonomy'
          && autonomyWorkCheckpointPending(toolResults, activeAutonomyWorkIds())) {
          selected = selected.filter((tool) => (
            String(tool?.name || tool?.function?.name || '') === 'werk_bijhouden'
          ));
        }
        return selected.filter((tool) => {
          const name = String(tool?.name || tool?.function?.name || '').trim();
          if (mode === 'autonomy' && !autonomyLaneAllows(name, autonomyLane, toolResults)) return false;
          if (mode === 'autonomy' && !autonomyDiscoveryAllowed(name, toolResults)) return false;
          return Number(toolNameAttempts.get(name) || 0) < attemptLimitFor(name);
        });
      },
      roundTimeoutMs: mode === 'autonomy' ? 90_000 : longWork ? 120_000 : 45_000,
      totalTimeoutMs: mode === 'autonomy'
        ? (config.autonomyFull ? config.autonomyTurnMinutes : 6) * 60_000
        : longWork ? 300_000
          : (requiredTools.length || explicitTool) ? 240_000
            : 90_000,
      rateLimitRetries: 2,
      cacheKey: mode === 'autonomy'
        ? 'eva-autonomous-core'
        : channel === 'voice' ? 'eva-voice-core' : 'eva-single-core',
      budgetClass: mode === 'autonomy' ? 'autonomous' : 'interactive',
      shouldYield: mode === 'autonomy'
        ? () => runtime.interactivePending > 0
          || Number(runtime.activeInteractive || 0) > 0
          || runtime.interactionEpoch !== autonomyEpoch
        : null,
      stopAfterToolRound: () => {
        if (mode === 'autonomy' && autonomyStopReady(toolResults, {
          allowSignalOnly: !hasOpenDurableWork,
          requiredWorkIds: activeAutonomyWorkIds(),
        })) return AUTONOMY_SILENT;
        // Laat Eva zelf het bewezen resultaat uitleggen. De artifactcontrole bepaalt
        // alleen of het werk echt af is; zij is geen vast antwoordsjabloon.
        if (mode !== 'chat' || requiredTools.length !== 1 || requiredTools[0] !== 'bedien_huis') return false;
        const action = [...toolResults].reverse().find((tool) => tool.name === 'bedien_huis');
        if (!action?.ok || !action?.verified) return false;
        try {
          const parsed = JSON.parse(String(action.output || ''));
          return parsed.human_message || parsed.evidence?.summary || false;
        } catch {
          return false;
        }
      },
    });

    const provenFallback = evidenceFallback(toolResults, { requiredTools, userText });
    let reply = String(result.text || '').trim();
    if (syntheticTest && syntheticReplyNeedsPersistentState(reply)) {
      reply = stripSyntheticFutureClaims(reply) || provenFallback;
    }
    if (/^\s*\{|^\s*\[\s*(?:\{|"|-?\d|true|false|null|\])/i.test(reply)) {
      reply = humanResultText(reply, { limit: 12_000, preferDetailed: true });
    }
    if (looksLikeInternalOutput(reply)) reply = provenFallback;
    if (mode === 'autonomy' && reply === AUTONOMY_SILENT) reply = '';
    if (mode === 'chat' && provenFallback && (
      !reply
      || result.exhausted === true
      || /\b(?:nog geen betrouwbaar eindresultaat|handeling is nog niet uitgevoerd)\b/i.test(reply)
    )) {
      reply = provenFallback;
    }
    // Bij een expliciete read-only capabilitydemo is de letterlijke uitvoer het
    // antwoord. Laat een vrije samenvatting die receipt/hash/stdout weglaat het
    // gecontroleerde toolbewijs niet afzwakken.
    if (mode === 'chat'
      && requiredTools.length === 1
      && requiredTools[0] === 'gebruik_capability'
      && provenFallback) {
      reply = provenFallback;
    }
    if (!reply) {
      reply = mode === 'autonomy'
        ? ''
        : 'Hier kwam ik nog niet uit.';
    }
    if (mode === 'chat' && buildWork
      && !buildMissionComplete(toolResults, buildNeedsExecution)
      && !phasedProgrammingBuildStatus(toolResults, userText).complete
      && claimsCompletedAction(reply)) {
      reply = 'Nee, dit is nog niet af.';
    } else if (mode === 'chat' && buildWork
      && !programmingAcceptanceStatus(toolResults, userText).complete
      && claimsCompletedAction(reply)) {
      reply = 'Nee, dit is nog niet goed genoeg getest.';
    } else if (mode === 'chat' && requiredTools.length) {
      const missing = requiredTools.filter(
        (required) => !toolResults.some((tool) => tool.name === required),
      );
      const unverified = requiredTools.filter(
        (required) => !turnRequirementSatisfied(required, toolResults, userText),
      );
      if (missing.length && claimsCompletedAction(reply)) {
        reply = 'Nee, dat heb ik nog niet gedaan.';
      } else if (unverified.length && claimsCompletedAction(reply)) {
        let reason = '';
        try {
          const latestFailure = [...toolResults].reverse().find(
            (tool) => unverified.includes(tool.name)
              && (tool.ok !== true || tool.verified !== true),
          );
          const parsed = JSON.parse(String(latestFailure?.output || ''));
          reason = humanResultText(
            parsed?.human_message || parsed?.error || parsed?.evidence?.summary || '',
          );
        } catch {
          reason = '';
        }
        reply = reason ? `Dat ging mis: ${reason}` : 'Nee, dat is niet gelukt.';
      }
    }

    if (mode !== 'chat') {
      logTimeline('innerlijk', `Autonome beurt afgerond${toolResults.length ? ` met ${toolResults.length} handeling(en)` : ' zonder handeling'}.`, {
        internalOnly: true,
        autonomous: true,
        turnId,
        tools: toolResultSummary(toolResults),
      });
    }


    if (mode === 'chat' && (
      conclusionInvestigationFeedback(reply, userText, toolResults, turnPlan.actionIntent === 'execute')
      || incompleteRequestedOutcomeFeedback(reply, userText)
    )) {
      const grounded = evidenceFallback(toolResults, { requiredTools, userText });
      reply = grounded && !conclusionInvestigationFeedback(
        grounded, userText, toolResults, turnPlan.actionIntent === 'execute',
      )
        ? grounded
        : 'Ik heb nog geen onderbouwde uitkomst. Daarom trek ik nu niet de conclusie dat dit niet kan.';
    }

    return answerTurn(reply, result);
  } catch (error) {
    if (error?.code === 'EVA_TURN_SUPERSEDED') {
      return answerTurn('', { superseded: true, yielded: true });
    }
    if (mode === 'autonomy' && contributionFromTools(toolResults, mode, userText)
      && ensureAutonomyCheckpointAfterReport()) {
      logTimeline('system', `Zelfstandige beurt is na bewezen uitvoering hersteld van: ${errMsg(error)}`, {
        internalOnly: true,
        autonomous: true,
        turnId,
        recovered: true,
      });
      return answerTurn('', { recovered: true });
    }
    const detail = errMsg(error);
    const provenFallback = evidenceFallback(toolResults, { requiredTools, userText });
    let failedReason = '';
    try {
      const failed = [...toolResults].reverse().find((tool) => tool.ok !== true || tool.verified !== true);
      const parsed = JSON.parse(String(failed?.output || ''));
      failedReason = humanResultText(
        parsed?.human_message || parsed?.error || parsed?.evidence?.summary || '',
      );
    } catch {
      failedReason = '';
    }
    let reply = mode === 'autonomy'
      ? ''
      : provenFallback
        || (failedReason
          ? failedReason
          : durableCommand
            ? `Mijn antwoord viel weg terwijl ik bezig was met ${String(durableCommand.next_step || 'de volgende stap').replace(/[.!]+$/g, '')}.`
            : conversationOnly
              ? 'Mijn antwoord viel weg. Stuur je laatste bericht nog één keer?'
              : 'Mijn antwoord viel weg voordat ik dit kon afmaken.');
    if (mode === 'autonomy') checkpointAutonomyProgress();
    runtime.lastTurn = {
      turnId,
      channel,
      sender: person,
      mode,
      text: userText.slice(0, 300),
      reply,
      error: detail.slice(0, 500),
      tools: toolResultSummary(toolResults),
      durationMs: Math.round(performance.now() - started),
      finishedAt: new Date().toISOString(),
    };
    if (mode === 'autonomy') runtime.lastAutonomyTurn = runtime.lastTurn;
    else runtime.lastInteractiveTurn = runtime.lastTurn;
    try {
      closeCognitiveEpisode(cognitiveEpisode.id, {
        status: 'interrupted',
        resultClass: 'turn_error',
        evidence: { tools: toolResultSummary(toolResults) },
        reflection: { error: detail.slice(0, 500) },
      });
    } catch {
      // De oorspronkelijke fout blijft leidend; recovery gebeurt bij herstart.
    }
    return {
      reply,
      turnId,
      channel,
      sender: person,
      conversationId,
      tools: toolResults,
      error: detail,
      syntheticTest,
    };
  } finally {
    runtime.active = Math.max(0, runtime.active - 1);
    if (mode === 'chat') {
      runtime.activeInteractive = Math.max(0, Number(runtime.activeInteractive || 0) - 1);
    }
    if (!runtime.active) setActivity('luistert', null);
  }
}

export function handleMessageDetailed(input) {
  const channel = input?.channel || 'telegram';
  const sender = input?.sender || 'huisgenoot';
  const conversationId = conversationIdFor({
    channel,
    sender,
    messageContext: input?.messageContext,
  });
  runtime.interactivePending += 1;
  runtime.interactionEpoch += 1;
  return queueFor(conversationId, () => executeTurn({
    ...input,
    channel,
    sender,
    conversationId,
    mode: 'chat',
  })).finally(() => {
    runtime.interactivePending = Math.max(0, runtime.interactivePending - 1);
  });
}

export function handleMessage(input) {
  return handleMessageDetailed(input).then((result) => {
    recordDeliveredReply(result, { accepted: true, transport: input?.channel || 'direct' });
    return result.reply;
  });
}

export function recordDeliveredReply(result, delivery = null) {
  const turnId = String(result?.turnId || '').trim();
  const reply = String(result?.reply || '').trim();
  if (!turnId || !reply) return false;
  const alreadyRecorded = deliveredTurns.has(turnId) || hasDeliveredTurn(turnId);
  if (!alreadyRecorded) {
    if (result?.syntheticTest !== true) {
      addMessage(
        result.channel || 'unknown',
        'Eva',
        'assistant',
        reply,
        result.conversationId || null,
      );
    }
    logTimeline('chat_out', `Eva -> ${result.sender || 'huisgenoot'}: ${reply}`, {
      channel: result.channel || 'unknown',
      conversationId: result.conversationId || null,
      sender: 'Eva',
      turnId,
      goalEpisodeId: result?.goalEpisodeId || null,
      workItemId: result?.workItemId || null,
      originMessageId: result?.originMessageId || null,
      syntheticTest: result?.syntheticTest === true,
      delivery: delivery || null,
      tools: toolResultSummary(result.tools || []),
    });
  }
  const goalEpisode = goalEpisodeById(result?.goalEpisodeId);
  if (goalEpisode?.state === 'delivery_pending' && !episodeHasDelivery(goalEpisode.id)) {
    try {
      recordEpisodeDelivery(goalEpisode.id, {
        deliveredChannel: result.channel || 'unknown',
        deliveryTarget: result.sender || goalEpisode.delivery_target,
        originConversationId: result.conversationId || null,
        originMessageId: result.originMessageId || null,
        deliveredConversationId: result.conversationId || null,
        resultText: reply,
        proofSummary: goalEpisode.proof_summary,
        idempotencyKey: `turn:${turnId}:episode:${goalEpisode.id}`,
        transport: delivery || {},
      });
    } catch (error) {
      logTimeline('system', `Delivery-receipt voor episode ${goalEpisode.id} mislukte: ${errMsg(error)}`, {
        internalOnly: true,
        turnId,
        goalEpisodeId: goalEpisode.id,
      });
    }
  }
  deliveredTurns.add(turnId);
  if (deliveredTurns.size > 2_000) {
    const oldest = deliveredTurns.values().next().value;
    if (oldest) deliveredTurns.delete(oldest);
  }
  return !alreadyRecorded;
}

export function runAutonomousTurn({
  reason = 'vrije aandacht',
  pendingSignals = [],
  attention = null,
  workId = null,
  goalEpisodeId = null,
} = {}) {
  return queueFor('autonomy', () => executeTurn({
    channel: 'autonomy',
    sender: 'Eva',
    text: `Aanleiding voor deze zelfstandige beurt: ${reason}. Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.`,
    mode: 'autonomy',
    persistInbound: false,
    messageContext: {
      pendingSignals: (Array.isArray(pendingSignals) ? pendingSignals : []).slice(0, 30),
      attention,
      workId,
      goalEpisodeId,
    },
  }));
}

export function agentStatus() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const work = durableWorkStatus(today);
  const project = (item) => ({
    id: item.id,
    source: 'werk',
    topic: item.title,
    status: item.status,
    next_step: item.next_step || '',
    evidence: readableEvidence(item.result || item.evidence || ''),
    last_action: item.last_action || '',
    owner: item.owner || null,
    updated_ts: item.updated_ts,
    next_wake_ts: item.next_wake_ts || null,
  });
  const focusItem = selectPortfolioCommitment(work.open)?.item || null;
  const durableFocus = focusItem
    ? `${focusItem.title}${focusItem.next_step ? `: ${focusItem.next_step}` : ''}`
    : null;
  const statusKey = (value) => String(value || '')
    .toLowerCase()
    .replace(/^opdracht(?: van [^:]+)?:\s*/i, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const durableSteps = todayContributionRows(today)
    .filter((row) => {
      const instruction = String(row.content || '').replace(/^opdracht(?: van [^:]+)?:\s*/i, '');
      const rawEvidence = String(row.meta?.evidence || '').trim();
      if (row.meta?.source === 'opdracht'
        && !isExplicitToolRequest(instruction)
        && !requiredToolsFor({ text: instruction }).length) return false;
      if (/^\{/.test(rawEvidence) && row.meta?.lastAction === 'zoek_internet') return false;
      if (/"count"\s*:\s*0|geen bruikbare resultaten/i.test(rawEvidence)) return false;
      if (row.meta?.lastAction === 'werk_bijhouden' && work.doneToday.some((item) => (
        Math.abs(Date.parse(item.updated_ts || '') - Date.parse(row.ts || '')) < 5 * 60_000
      ))) return false;
      const rowEvidence = statusKey(readableEvidence(row.meta?.evidence || ''));
      const rowTopic = statusKey(row.content);
      return !work.doneToday.some((item) => {
        const itemEvidence = statusKey(readableEvidence(item.result || item.evidence || ''));
        const itemTopic = statusKey(item.title);
        const sameEvidence = rowEvidence.length >= 40 && itemEvidence.length >= 40
          && (rowEvidence.includes(itemEvidence.slice(0, 120))
            || itemEvidence.includes(rowEvidence.slice(0, 120)));
        const sameTopic = rowTopic.length >= 40 && itemTopic.length >= 40
          && (rowTopic.includes(itemTopic.slice(0, 120))
            || itemTopic.includes(rowTopic.slice(0, 120)));
        const closeInTime = Math.abs(
          Date.parse(item.updated_ts || '') - Date.parse(row.ts || ''),
        ) < 5 * 60_000;
        const sameResult = closeInTime && tekstOverlap(
          `${row.content} ${row.meta?.evidence || ''}`,
          `${item.title} ${item.result || ''} ${item.evidence || ''}`,
        ) >= 0.4;
        return sameEvidence || sameTopic || sameResult;
      });
    })
    .map((row) => ({
      id: `contribution:${row.id}`,
      source: row.meta?.source || 'bijdrage',
      topic: row.content,
      status: row.meta?.status || 'done',
      next_step: '',
      evidence: readableEvidence(row.meta?.evidence || ''),
      last_action: row.meta?.lastAction || '',
      owner: row.meta?.owner || null,
      updated_ts: row.ts,
    }));
  return {
    ts: new Date().toISOString(),
    active_turns: runtime.active,
    current_focus: runtime.current?.text || (runtime.active ? runtime.activity : durableFocus),
    activity: runtime.activity,
    last_turn: runtime.lastTurn,
    last_interactive_turn: runtime.lastInteractiveTurn,
    last_autonomy_turn: runtime.lastAutonomyTurn,
    open_commitments: work.open.map(project),
    blocked_items: work.blocked.map(project),
    waiting_for_evidence: [],
    today_real_steps: [
      ...work.doneToday.map(project),
      ...durableSteps,
    ],
  };
}

export const __voiceRouteTest = { isExplicitToolRequest, requiredToolsFor };
export const __projectRouteTest = {
  autonomyInput,
  autonomousMemoryContextText,
  autonomyArtifactReportPending,
  autonomyEffectRequiresReport,
  autonomyContinuationStep,
  autonomyDiscoveryAllowed,
  autonomyHasRunnableWork,
  autonomyHasScheduledWork,
  autonomyLaneAllows,
  autonomyLaneForTool,
  autonomyLaneForWork,
  autonomyNoActionReason,
  autonomyProofRelevantToWork,
  autonomyProgressFromTools,
  autonomyStopReady,
  autonomyWorkCheckpointPending,
  autonomyWorkReportPending,
  autonomyToolAllowed,
  delegatedWorkAllowsTool,
  chatToolNamesFor,
  buildMissionRemaining,
  buildMissionComplete,
  completedAutonomousArtifact,
  completedBuildReply,
  confirmedRecognitionName,
  directHomeCommand,
  directHomeForTurn,
  evidenceFallback,
  humanResultText,
  looksLikeInternalOutput,
  syntheticToolAllowed: toolSyntheticAllowed,
  syntheticReplyNeedsPersistentState,
  stripSyntheticFutureClaims,
  styleCorrectionPolicyFeedback,
  shouldBindDurableUserEpisode,
  identityNameFromText,
  incompleteAutonomousWrite,
  isExplicitToolRequest,
  isContinuationRequest,
  requestsExecutableBuild,
  requiredToolSatisfied,
  turnRequirementSatisfied,
  turnContractFor,
  isDurableProjectRequest,
  isLongWork,
  isReflectiveQuestion,
  isRecognitionConfirmation,
  isWorkRecapQuery,
  programmingAcceptanceStatus,
  phasedProgrammingBuildStatus,
  pendingDeliveryForWork,
  replyNeedsNarration,
  incompleteRequestedOutcomeFeedback,
  programmingOutcomeFacts,
  requiredToolsFor,
  contributionFromTools,
};

export default handleMessage;
