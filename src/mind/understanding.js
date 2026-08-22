import { createHash } from 'node:crypto';
import { db } from '../db.js';
import { canonicalJson, jsonSchemaViolation, verifiedCompleteReceipt } from '../util.js';
import {
  effectBoundary, maskAdditiveNegation, monitoringGuardValid, monitoringRequestSpec,
  priorActionResultFollowup,
} from './turnplan.js';
import { verifiedAutonomySelfAttentionReceipt } from './runtimecontroller.js';
import {
  buildMonitoringMeasurementFact,
  issueMonitoringContract as issueMonitoringContractV2,
  issueMonitoringFactBinding,
  monitoringConditionId,
  monitoringContractReadBindings,
  normalizeMonitoringContract as normalizeMonitoringContractV2,
  normalizeMonitoringOutcomeDecision,
} from './monitoringcontract.js';
import { createSignalBatch, signalMatchesScopeSelector, validSignalBatch } from './signals.js';
import {
  providerExplorationContinuationContext,
  validExplorationContinuationContext,
} from './selfattentioncontext.js';
import {
  behaviorLearningAvailable, normalizeBehaviorLensCandidate, registerBehaviorProposalVerifier,
} from './learning.js';
const CONTRACT = 'TurnUnderstanding/v1';
const RECORD_CONTRACT = 'TurnUnderstandingRecord/v1';
const ENVELOPE_CONTRACT = 'TurnEnvelope/v1';
const MAX_TURN_TEXT = 16_000;
const MAX_PRIOR_BYTES = 32_000;
const ARTIFACT_RECEIPT_LIMIT = 12;
const ARTIFACT_MAX_BYTES = 2 * 1024 * 1024;
const ARTIFACT_SHA256 = /^[a-f0-9]{64}$/u;
const ARTIFACT_VERSION_REF = /^artifact-version:[a-f0-9]{64}$/u;
const ARTIFACT_ROLES = new Set(['source', 'test', 'fixture', 'result']);
const EFFECTS = Object.freeze(['read', 'discovery', 'home', 'voice', 'external', 'workspace', 'persistent', 'runtime']);
const MUTATING_EFFECTS = new Set(EFFECTS.filter((effect) => !['read', 'discovery'].includes(effect)));
const READ_ONLY_SIGNAL_ASSESSMENT = 'De actuele toestand is uitsluitend via de geselecteerde uitlezingen beoordeeld; er is in deze beurt geen fysieke handeling uitgevoerd.';
const NONACTING_SIGNAL_ASSESSMENT = 'De geselecteerde niet-fysieke stappen zijn beoordeeld; er is in deze beurt geen fysieke handeling uitgevoerd.';
const DECISIONS = new Set(['reply', 'act', 'inspect', 'schedule', 'clarify', 'cancel', 'silent', 'fail_closed']);
const DURABILITY = new Set(['turn', 'episode']);
const DELIVERY_MODES = new Set(['none', 'reply', 'later', 'silent']);
const DELIVERY_TARGETS = new Set(['none', 'origin', 'requester', 'family']);
const TEMPORAL_MODES = new Set(['now', 'later', 'condition', 'none']);
const DIALOGUE_ACTS = new Set(['chat', 'question', 'inform', 'request', 'command', 'continue', 'correct', 'confirm', 'reject', 'cancel', 'unknown']);
export const CONTINUATION_DIALOGUE_ACTS = Object.freeze(['continue', 'correct']);
const CONTINUATION_ACTS = new Set(CONTINUATION_DIALOGUE_ACTS);
const BENEFICIARIES = new Set(['none', 'self', 'requester', 'household', 'family', 'shared', 'unknown']);
const CONDITION_STATES = new Set(['required', 'met', 'unmet', 'unknown']);
const CONDITION_ROLES = new Set(['action', 'guard', 'recovery', 'completion']);
const POLARITIES = new Set(['request', 'deny', 'mention']);
const EXECUTING_DECISIONS = new Set(['act', 'inspect', 'schedule']);
const SELF_ATTENTION_PROJECTABLE_EFFECTS = new Set(['read', 'discovery', 'workspace']);
// Dit spiegelt exact de administratieve toolnamen in prepareAutonomyPlanStep:
// verkenning_bijhouden, toon_werk en signalen_afhandelen.
const SELF_ATTENTION_ADMIN_CAPABILITIES = new Set([
  'exploration.manage', 'exploration.read', 'work.episode.read', 'signal.assess',
]);
const SELF_ATTENTION_EXPLORATION_CONTINUATIONS = new Set([
  'update', 'wachten', 'afronden', 'stoppen',
]);
const SELF_ATTENTION_EXPLORATION_TERMINAL = new Set(['afronden', 'stoppen']);
const SELF_ATTENTION_GOAL_MANAGE_OPERATIONS = new Set([
  'update', 'pause', 'complete', 'abandon',
]);
const SELF_ATTENTION_GOAL_UPDATE_FIELDS = new Set([
  'titel', 'intentie', 'waarom', 'begunstigde', 'horizon',
  'succesmaten', 'prioriteit', 'volgende_review',
]);
const SELF_ATTENTION_GOAL_REF = /^goal-ref:[a-f0-9]{32}$/u;
const SELF_ATTENTION_AUDIT_BOUNDARY_REASONS = new Set([
  'missing_source_span', 'quoted', 'negated', 'hypothetical', 'conditional',
  'cancelled', 'deferred', 'semantic_denial', 'inspection_only',
  'effect_conflict', 'guard_only',
]);
const SELF_ATTENTION_VALIDATION_AUDITS = new WeakMap();
const TURN_DECISION_RETRY_GUIDES = new WeakMap();
const PROJECTED_SELF_ATTENTION_GOAL_OPERATIONS = new WeakMap();
const ISSUED_SELF_ATTENTION_GOAL_ACTIONS = new WeakMap();
const SELF_ATTENTION_GOAL_ACTION_TOKENS = new WeakMap();
const PLANNED_SELF_ATTENTION_GOAL_ACTION_TOKENS = new WeakSet();
const CONSUMED_SELF_ATTENTION_GOAL_ACTION_TOKENS = new WeakSet();
const ISSUED_ASPIRATION_EXPLORATION_ANCHORS = new WeakMap();
const CONSUMED_ASPIRATION_EXPLORATION_ANCHORS = new WeakSet();
const ISSUED_EXPLORATION_TERMINAL_CONTRIBUTIONS = new WeakMap();
const PLANNED_EXPLORATION_TERMINAL_CONTRIBUTIONS = new WeakSet();
const CONSUMED_EXPLORATION_TERMINAL_CONTRIBUTIONS = new WeakSet();
const ISSUED_EXPLORATION_TERMINAL_PRECOMMITS = new WeakMap();
const ISSUED_BEHAVIOR_PROPOSALS = new WeakMap();
const EXPLORATION_TERMINAL_MUTATION_STATES = new WeakMap();
registerBehaviorProposalVerifier((context) => ISSUED_BEHAVIOR_PROPOSALS.get(context) || null);
const FREE_AUTONOMY_CONTINUATION_CONTRACT = 'FreeAutonomyContinuation/v1';
const FREE_AUTONOMY_CONTINUATION_CHOICES = new Set([
  'start_exploration', 'keep_observation', 'silent',
]);
const FREE_AUTONOMY_EXPLORATION_FIELDS = Object.freeze([
  'aanleiding', 'hypothese', 'max_acties', 'onderwerp_ankers',
  'onderwerp_sleutel', 'stopregel', 'verwachte_waarneming', 'vraag',
]);
const ISSUED_FREE_AUTONOMY_CONTINUATIONS = new WeakMap();
const CONSUMED_FREE_AUTONOMY_CONTINUATIONS = new WeakSet();
const OPERATION_POSITIVE_SOURCE_HINTS = new Map([
  ['work.episode.manage\u0000afronden', { effect: 'persistent', hints: [
    /^\s*(?:(?:eva)\s*[,!:]\s*)?(?:(?:dan|daarna|vervolgens|nu|graag|alsjeblieft)\s+)*(?:rond|sluit)\s+(?:dit|dat|het|deze)\s+(?:werk|vervolgwerk|taak|episode|project|onderdeel|resultaat)\s+af\s*[.!?]?\s*$/iu,
    /^\s*(?:(?:eva)\s*[,!:]\s*)?(?:(?:dan|daarna|vervolgens|nu|graag|alsjeblieft)\s+)*(?:afrond|voltooi|completeer)\s+(?:dit|dat|het|deze)\s+(?:werk|vervolgwerk|taak|episode|project|onderdeel|resultaat)\s*[.!?]?\s*$/iu,
    /^\s*(?:(?:eva)\s*[,!:]\s*)?(?:(?:dan|daarna|vervolgens|nu|graag|alsjeblieft)\s+)*(?:rond|sluit)\s+af\s+(?:wat|datgene(?:\s+wat)?)\s+(?:(?:al|nu|reeds)\s+)?(?:klaar|afgerond|voltooid)\s+is\s*[.!?]?\s*$/iu,
    /^\s*(?:(?:eva)\s*[,!:]\s*)?(?:(?:dan|daarna|vervolgens|nu|graag|alsjeblieft)\s+)*(?:kun|kan)\s+je\s+(?:dit|dat|het|deze)\s+(?:werk|vervolgwerk|taak|episode|project|onderdeel|resultaat)\s+(?:afronden|voltooien|completeren)\s*[.!?]?\s*$/iu,
  ] }],
]);
const MODEL_FIELDS = ['dialogueAct', 'goal', 'conditions', 'provenance', 'confidence', 'actions', 'requestedEffects', 'deniedEffects', 'temporal', 'durability', 'delivery', 'proofNeeds', 'ambiguities', 'firstDecision'];
const IDENTIFIER = /^[a-z][a-z0-9_.:-]{0,119}$/u;
const FORBIDDEN_KEYS = new Set([
  'tool', 'toolName', 'tool_name', 'requiredTools', 'allowedTools', 'privilege', 'privileges',
  'workId', 'work_id', 'episodeId', 'episode_id', 'recipientId', 'recipient_id', 'authority',
]);
// Polarity draagt de betekenis, maar is afkomstig uit dezelfde modelcall als
// de actie. Daarom blijft een smalle modelonafhankelijke hostgrens nodig.
// Relationeel additief "niet alleen ... maar ook" is vóór clauseslicing al
// lengtebehoudend gemaskeerd. Alleen graadbegrenzing blijft hier uitgezonderd;
// iedere andere ontkenning in de geselecteerde actieclause faalt gesloten.
const MUTATION_NEGATION = /\b(?:niet|geen|nooit|niemand|niets|niks|not|never|nobody|nothing)\b|\bdo\s+not\b|\bdon['’]?t\b/iu;
const NON_ACTION_NEGATION = /\b(?:niet\s+te|not\s+too)\b/giu;
const READ_NON_ACTION_NEGATION = /\bnot\s+too\b/giu;
const PRESENCE_STATE_NEGATION = /\b(?:(?:ben|bent|is|zijn|am|are)\s+(?:er\s+(?:niet|nooit|not|never)\b|(?:niet|nooit|not|never)\s+(?:thuis|aanwezig|hier|daar|weg|home|present|here|there|away)\b)|(?:niet|nooit|not|never)\s+(?:thuis|aanwezig|hier|daar|weg|home|present|here|there|away)\s+(?:ben|bent|is|zijn|am|are)\b|(?:niemand|nobody|no\s+one)\s+(?:(?:is|zijn|are)\s+(?:er\s+)?(?:thuis|aanwezig|hier|daar|home|present|here|there)\b|(?:thuis|aanwezig|hier|daar|home|present|here|there)\s+(?:is|zijn|are)\b))/giu;
// Bij de letterlijke voice-fastlane ligt de uitgesproken payload al buiten
// deze scope. Een losse `niet` in de opdrachtprefix is daar dus wél eenduidig:
// "zeg niet ..." mag nooit als omroepopdracht doorgaan.
// Alleen een aantoonbaar hypothetische vraag begrenst een handeling. Een los
// modaal woord in het gevraagde resultaat ("een aanbod dat we zouden kunnen
// plaatsen") maakt de huidige opdracht zelf niet hypothetisch.
const HYPOTHETICAL = /\b(?:stel\s+dat|hypothetisch|suppose)\b|(?:^|[.!?;]\s*)\s*(?:wat|hoe|what|how)\s+(?:zou(?:den)?|kon(?:den)?|would|could)\b/iu;
const POLITE_REQUEST = /^\s*(?:(?:hey|he|hé)\s+)?(?:eva\s*[,!:]?\s*)?(?:(?:zou(?:den)?|kon(?:den)?)\s+(?:je|jij|u)|(?:could|would)\s+you)\b(?!\s+(?:wat|what|hoe|how)\b)/iu;
const CONDITIONAL = /\b(?:als|wanneer|zodra|indien|if|when|once)\b/giu;
// `als` is in het Nederlands ook een rol-/presentatievoorzetsel. Dat gebruik
// start geen toekomstige voorwaarde: "iets als echt presenteren" vraagt juist
// om een huidige correctie. Houd de uitzondering bewust smal en werkwoordelijk;
// een echte grens als "corrigeer als het bewijs verandert" blijft conditioneel.
const NON_CONDITIONAL_PRESENTATION_AFTER_ALS = /^\s+(?:(?:echt|waar|feit|feitelijk|voorbeeld|placeholder|sjabloon|concept|scenario|onbewezen|bewezen|afgerond|voltooid|klaar)\s+)?(?:presenteert|presenteerde|presenteerden|markeert|markeerde|markeerden|labelt|labelde|labelden|toont|toonde|toonden|beschrijft|beschreef|behandelt|behandelde|behandelden)\b/iu;
const PRESENTATION_BEFORE_ALS = /(?:presenteer\w*|markeer\w*|label\w*|toon\w*|beschrijf\w*|behandel\w*)\b[^.!?;\n]{0,80}$/iu;
const PRESENTATION_ROLE_AFTER_ALS = /^\s+(?:(?:een|het|de)\s+)?(?:echt|waar|feit|feitelijk|voorbeeld|placeholder|sjabloon|concept|scenario|onbewezen|bewezen|afgerond|voltooid|klaar)\b/iu;
// Een iteratieve workspace-operatie heeft zelf een begrensde recoveryroute.
// "herstel als de proef faalt" is daar een stop-/herstelregel binnen de nu
// startende handeling, niet een toekomstige starttrigger. Andere voorwaarden
// blijven fail-closed wanneer het model ze ten onrechte als `now` markeert.
const ITERATIVE_RECOVERY_CONDITION = /\b(?:als|wanneer|if|when)\b[^.!?;\n]{0,120}\b(?:mislukt|faalt|stukloopt|struikelt|fout\s+gaat|fails?|breaks?|errors?)\b/iu;
const DEFERRED = /\b(?:later|straks|morgen|vanavond|vannacht|later\s+on|tomorrow|tonight)\b/iu;
// `notBefore` en een vrije condition komen van hetzelfde model als de rest van
// de interpretatie. Een uitgestelde start heeft daarom ook letterlijk bewijs
// uit de actuele beurt nodig. Dit is bewust geen algemene intentieclassifier:
// alleen enkele ondubbelzinnige startvormen en conditionele voegwoorden tellen.
const LATER_START_MARKER = /\b(?:later|straks|morgen|overmorgen|vanavond|vannacht|tomorrow|tonight|vanaf|starting\s+(?:at|from)|over\s+(?:\d+|een|one|twee|two)\s+(?:minu(?:ut|ten)|uur|uren|dag|dagen|week|weken|minutes?|hours?|days?|weeks?))\b|\b(?:om\s+)?(?:[01]?\d|2[0-3])(?::|\.)(?:[0-5]\d)(?:\s*u(?:ur)?)?\b|\bom\s+(?:[01]?\d|2[0-3])\s*u(?:ur)?\b/giu;
const CONDITION_START_MARKER = /\b(?:als|wanneer|zodra|indien|if|when|once)\b/giu;
const DEADLINE_PREFIX = /\b(?:voor|vóór|uiterlijk(?:\s+op)?|tegen|binnen|before|by|until|no\s+later\s+than)\s*$/iu;
const NEGATED_LATER = /\b(?:niet|no)\s+later\s+(?:dan|than)\b/iu;
const CANCEL = /\b(?:laat\s+(?:(?:het|dat|dit)\s+)?maar|toch\s+niet|annuleer|stop\s+maar|cancel|never\s+mind|niet\s+(?:opnieuw|nogmaals))\b/iu;
const CANCEL_TURN = /^\s*(?:laat\s+(?:(?:het|dat|dit)\s+)?maar|toch\s+niet|annuleer|stop\s+maar|cancel|never\s+mind|niet\s+(?:opnieuw|nogmaals))[.!]?\s*$/iu;
const CANCELLATION_OPERATION_IDS = new Set(['abandon', 'annuleren', 'cancel', 'stop']);
const RETRY = /^\s*(?:(?:doe|probeer)\s+(?:dat|het|hem|haar|[’']?m)\s+(?:nogmaals|opnieuw|nog\s+(?:eens|(?:één|een)\s+keer))(?:\s+via\s+(?:(?:die|de)zelfde\s+)?voice\s*pe)?|doe\s+(?:dat|het|hem|haar|[’']?m)\s+(?:dan|nu)|(?:nogmaals|opnieuw|nog\s+(?:eens|(?:één|een)\s+keer))|(?:(?:wel\s+waar|nee|kom\s+op)[,!.]?\s*)?schiet\s+op(?:\s+dan)?|repeat|again)[.!?]*\s*$/iu;
const PRESENT_STATE_CONSTRUCTIONS = [
  /^(?:staat|staan|is|zijn)\s+\S(?:.{0,180}\S)?\s+(?:aan|uit|open|dicht|gesloten|actief|inactief|online|offline|thuis|weg|aanwezig|beschikbaar|bezet|leeg|vol|klaar|kapot|warm|koud)\s*[?!.]*$/iu,
  /^(?:werkt|werken|brandt|branden|draait|draaien|reageert|reageren)\s+\S(?:.{0,180}\S)?\s*[?!.]*$/iu,
  /^(?:wat|welk|welke|what|which)\s+(?:is|zijn|are)\s+(?:(?:nu|momenteel|now|currently)\s+)?(?:(?:de|het|the)\s+)?(?:(?:actuele|huidige|current)\s+)?(?:status|state|toestand)\s+(?:van|of)\s+\S(?:.{0,180}\S)?\s*[?!.]*$/iu,
  /^(?:hoe|how)\s+(?:warm|koud|heet|koel|vochtig|droog|licht|donker|hoog|laag|snel)\s+(?:is|zijn|are)\s+(?:(?:het|it)\s+)?(?:(?:nu|momenteel|now|currently)\s+)?(?:in|bij|op|inside|at)\s+\S(?:.{0,180}\S)?\s*[?!.]*$/iu,
  /^(?:hoe|how)\s+(?:warm|koud|heet|koel|vochtig|droog|licht|donker|hoog|laag|snel)\s+(?:is|zijn|are)\s+\S(?:.{0,180}\S)?\s+(?:nu|momenteel|now|currently)\s*[?!.]*$/iu,
  /^(?:waar|where)\s+(?:is|zijn|staat|staan|ligt|liggen|hangt|hangen|are)\s+\S(?:.{0,180}\S)?\s*[?!.]*$/iu,
  /^(?:is|are)\s+\S(?:.{0,180}\S)?\s+(?:on|off|open|closed|active|inactive|online|offline|home|away|available|occupied|empty|full|ready|broken|hot|cold)\s*[?!.]*$/iu,
  /^(?:does|do)\s+\S(?:.{0,180}\S)?\s+(?:work|run|respond)\s*[?!.]*$/iu,
];
function presentStateQuestion(value) {
  const text = String(value || '').trim();
  return text.length > 0 && text.length <= 400
    && PRESENT_STATE_CONSTRUCTIONS.some((pattern) => pattern.test(text));
}
const VOICE_MARKER = /\b(?:zeg(?:gen|t)?|spreek|roep|say|announce)\b/iu;
const ISSUED_UNDERSTANDINGS = new WeakSet();
const ISSUED_SIGNAL_BATCHES = new WeakMap();
const ISSUED_CONTINUITY_WATCHES = new WeakMap();
const PROJECTED_SIGNAL_OPERATIONS = new WeakMap();
const PROJECTED_ACTIVE_WORK_OPERATIONS = new WeakMap();
export function iterativeExecutionBudget(mode = 'chat', configuredMinutes = 30) {
  const minutes = Math.max(2, Number(configuredMinutes || 30));
  return mode === 'autonomy'
    ? { baseRounds: 12, maxRounds: 32, progressExtensionRounds: 3,
      totalTimeoutMs: minutes * 60_000 }
    : { baseRounds: 12, maxRounds: 24, progressExtensionRounds: 3,
      totalTimeoutMs: Math.min(10, minutes) * 60_000 };
}
function sha256(value) { return createHash('sha256').update(value).digest('hex'); }
function plainObject(value) { return value != null && typeof value === 'object' && !Array.isArray(value); }
function jsonValue(value, depth = 0) {
  if (depth > 8) throw new TypeError('semantic_input_too_deep');
  if (value == null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError('semantic_input_non_finite');
    return value;
  }
  if (Array.isArray(value)) {
    if (value.length > 100) throw new TypeError('semantic_input_too_large');
    return value.map((item) => jsonValue(item, depth + 1));
  }
  if (!plainObject(value)) throw new TypeError('semantic_input_not_json');
  const result = {};
  for (const key of Object.keys(value).sort()) {
    if (FORBIDDEN_KEYS.has(key)) throw new TypeError(`forbidden_model_key:${key}`);
    result[key] = jsonValue(value[key], depth + 1);
  }
  return result;
}
function deepFreeze(value, seen = new Set()) {
  if (!value || typeof value !== 'object' || seen.has(value)) return value;
  seen.add(value);
  Object.values(value).forEach((child) => deepFreeze(child, seen));
  return Object.freeze(value);
}
function frozenJsonData(value, seen = new Set()) {
  if (value == null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value !== 'object' || seen.has(value)) return false;
  try {
    if (!Object.isFrozen(value)) return false;
    const array = Array.isArray(value);
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== (array ? Array.prototype : Object.prototype)
      && !(prototype === null && !array)) return false;
    const descriptors = Object.getOwnPropertyDescriptors(value);
    const keys = Reflect.ownKeys(descriptors);
    if (keys.some((key) => typeof key !== 'string')) return false;
    const dataKeys = /** @type {string[]} */ (
      array ? keys.filter((key) => key !== 'length') : keys
    );
    if (array && (dataKeys.length !== value.length
      || dataKeys.some((key, index) => key !== String(index)))) return false;
    seen.add(value);
    return dataKeys.every((key) => {
      const descriptor = descriptors[key];
      return descriptor && Object.hasOwn(descriptor, 'value')
        && descriptor.enumerable === true && descriptor.configurable === false
        && descriptor.writable === false && frozenJsonData(descriptor.value, seen);
    });
  } catch { return false; }
}
function identifier(value, label) {
  const result = String(value || '').trim();
  if (!IDENTIFIER.test(result)) throw new TypeError(`invalid_${label}`);
  return result;
}
function normalizeEffects(value) {
  const values = Array.isArray(value) ? value : [];
  const result = [...new Set(values.map((item) => String(item || '').trim().toLowerCase()))];
  if (result.some((effect) => !EFFECTS.includes(effect))) throw new TypeError('invalid_effect');
  return result.sort();
}
function limitedText(value, max = 1_000) { return String(value ?? '').trim().slice(0, max) || null; }
function normalizeGoal(value) {
  const raw = plainObject(value) ? value : {};
  const beneficiary = BENEFICIARIES.has(raw.beneficiary) ? raw.beneficiary : 'unknown';
  return { statement: limitedText(raw.statement), outcome: limitedText(raw.outcome), beneficiary };
}
function boundedUnderstanding(value) {
  try {
    const serialized = canonicalJson(value);
    if (typeof serialized !== 'string' || serialized.length > MAX_PRIOR_BYTES) return null;
    const source = JSON.parse(serialized);
    if (!verifyTurnUnderstanding(source) && !verifyTurnUnderstandingRecord(source)) return null;
    return {
    contract: source.contract, sha256: source.sha256,
    dialogueAct: DIALOGUE_ACTS.has(source.dialogueAct) ? source.dialogueAct : 'unknown', goal: normalizeGoal(source.goal),
    conditions: (Array.isArray(source.conditions) ? source.conditions : []).map((item) => ({
      statement: limitedText(item?.statement, 500), state: CONDITION_STATES.has(item?.state) ? item.state : 'unknown',
      sourceSpans: structuredClone(Array.isArray(item?.sourceSpans) ? item.sourceSpans : []),
    })),
    temporal: { mode: source.temporal?.mode || 'none', notBefore: source.temporal?.notBefore || null,
      dueBy: source.temporal?.dueBy || null, condition: limitedText(source.temporal?.condition, 500),
      startSourceSpans: structuredClone(Array.isArray(source.temporal?.startSourceSpans)
        ? source.temporal.startSourceSpans : []) },
    durability: source.durability,
    actions: (Array.isArray(source.actions) ? source.actions : []).map((action) => ({
      requirementId: action.requirementId, capabilityId: action.capabilityId, operationId: action.operationId,
      polarity: action.polarity, effect: action.effect, authorization: action.authorization,
    })),
    firstDecision: { kind: source.firstDecision?.kind || 'unknown',
      requirementIds: Array.isArray(source.firstDecision?.requirementIds) ? source.firstDecision.requirementIds : [] },
    };
  } catch { return null; }
}
function normalizeResourceReceipts(value) {
  const raw = Array.isArray(value) ? value : [];
  if (raw.length > 8) throw new TypeError('invalid_resource_receipt');
  const sourceTurns = new Set();
  const receipts = raw.map((item) => {
    if (!plainObject(item)) throw new TypeError('invalid_resource_receipt');
    const receipt = {
      capabilityId: identifier(item.capabilityId, 'receipt_capability_id'), operationId: identifier(item.operationId, 'receipt_operation_id'),
      field: String(item.field || '').trim(), value: String(item.value || '').trim(),
    };
    const sourceTurnId = limitedText(item.sourceTurnId, 160);
    const invalidHash = [item.understandingSha256, item.receiptSha256]
      .some((hash) => !/^[a-f0-9]{64}$/u.test(String(hash || '')));
    if (!/^[A-Za-z][A-Za-z0-9_]{0,119}$/u.test(receipt.field) || !receipt.value || receipt.value.length > 1_000
      || !sourceTurnId || invalidHash
      || !/^r(?:[1-9]|1[0-2])$/u.test(String(item.requirementId || ''))
      || !Number.isFinite(Date.parse(limitedText(item.observedAt, 40) || ''))) throw new TypeError('invalid_resource_receipt');
    sourceTurns.add(sourceTurnId);
    return receipt;
  });
  if (sourceTurns.size > 1) throw new TypeError('mixed_resource_receipt_turns');
  return [...new Map(receipts.map((item) => [canonicalJson(
    [item.capabilityId, item.operationId, item.field, item.value]), item])).values()];
}
function normalizeConversationWorkReceipts(value, conversationId, requester) {
  const raw = Array.isArray(value) ? value : [];
  if (raw.length > 1) throw new TypeError('invalid_conversation_work_receipt');
  return raw.map((item) => {
    if (!plainObject(item)) throw new TypeError('invalid_conversation_work_receipt');
    const { sha256: claimed, ...body } = item;
    const valueText = String(body.value || ''), expectedConversation = String(conversationId || '');
    if (Object.keys(item).sort().join(',') !== [
      'capabilityId', 'completionRecordSha256', 'contract', 'conversationId', 'episodeId',
      'field', 'observedAt', 'operationId', 'requester', 'requirementId', 'sha256',
      'sourceTurnId', 'understandingSha256', 'value',
    ].sort().join(',')
      || body.contract !== 'ConversationWorkReceipt/v1'
      || body.capabilityId !== 'work.episode.manage' || body.operationId !== 'annuleren'
      || body.field !== 'id' || !/^[1-9][0-9]*$/u.test(valueText)
      || !Number.isSafeInteger(Number(valueText)) || !expectedConversation
      || body.conversationId !== expectedConversation || body.requester !== requester
      || !Number.isSafeInteger(Number(body.episodeId)) || Number(body.episodeId) < 1
      || !limitedText(body.sourceTurnId, 160)
      || !/^r(?:[1-9]|1[0-2])$/u.test(String(body.requirementId || ''))
      || [body.understandingSha256, body.completionRecordSha256, claimed]
        .some((hash) => !/^[a-f0-9]{64}$/u.test(String(hash || '')))
      || !Number.isFinite(Date.parse(limitedText(body.observedAt, 40) || ''))
      || sha256(canonicalJson(body)) !== claimed) throw new TypeError('invalid_conversation_work_receipt');
    return { capabilityId: body.capabilityId, operationId: body.operationId,
      field: body.field, value: valueText };
  });
}
function semanticContext(envelope) {
  return { ...envelope.context, priorUnderstandings: envelope.priorUnderstandings.slice(-4) };
}
function currentSignalBatchTurn(envelope, source = 'model') {
  return source === 'model' && envelope.origin === 'self'
    && Boolean(envelope.context.signalBatch?.signals?.length);
}
function canonicalSignalBatchTiming(raw, envelope, source) {
  if (!currentSignalBatchTurn(envelope, source)) return raw;
  return { ...raw, temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn' };
}
function canonicalSelfAttentionCurrentSpans(raw, selfAttention, source) {
  if (source !== 'model' || !selfAttention || !plainObject(raw?.temporal)
    || !['now', 'none'].includes(raw.temporal.mode)) return raw;
  return { ...raw, temporal: { ...raw.temporal, startSourceSpans: [] } };
}
function boundedVoiceRetryIntent(value) {
  if (!plainObject(value) || value.contract !== 'VoiceRetryIntent/v1'
    || !['retryable_pre_effect', 'already_completed'].includes(value.disposition)
    || typeof value.conversationId !== 'string' || value.conversationId.length > 240
    || typeof value.sourceTurnId !== 'string' || value.sourceTurnId.length > 512
    || !/^[a-f0-9]{64}$/u.test(String(value.sourceSha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.effectSha256 || ''))
    || !plainObject(value.action)) return null;
  const actionKeys = Object.keys(value.action).sort().join(',');
  if (!['doel,tekst', 'tekst'].includes(actionKeys)
    || typeof value.action.tekst !== 'string' || !value.action.tekst.trim()
    || value.action.tekst.length > 500
    || (Object.hasOwn(value.action, 'doel')
      && (typeof value.action.doel !== 'string' || !value.action.doel.trim()
        || value.action.doel.length > 240))
    || !Number.isFinite(Date.parse(value.expiresAt || ''))) return null;
  try {
    const { sha256: claimed, ...body } = value;
    if (!/^[a-f0-9]{64}$/u.test(String(claimed || ''))
      || sha256(canonicalJson(body)) !== claimed) return null;
    return deepFreeze(JSON.parse(canonicalJson(value)));
  } catch { return null; }
}
function exactArtifactReceiptPath(value) {
  const supplied = String(value || '');
  if (!supplied.startsWith('data/workspace/') || supplied.length > 2_048
    || supplied.includes('\\') || supplied.includes('\0') || /[\r\n]/u.test(supplied)) return false;
  const parts = supplied.split('/');
  return parts.length > 2 && parts[0] === 'data' && parts[1] === 'workspace'
    && parts.slice(2).every((part) => part && part !== '.' && part !== '..');
}
function artifactReceiptBase(value) {
  if (!plainObject(value) || !exactArtifactReceiptPath(value.path)
    || !ARTIFACT_SHA256.test(String(value.sha256 || ''))
    || !ARTIFACT_SHA256.test(String(value.understandingSha256 || ''))
    || !/^r(?:[1-9]|1[0-2])$/u.test(String(value.requirementId || ''))
    || !ARTIFACT_ROLES.has(value.role)) return null;
  return {
    path: value.path, sha256: value.sha256, role: value.role,
    understandingSha256: value.understandingSha256, requirementId: value.requirementId,
  };
}
function projectedArtifactReceipt(value) {
  const base = artifactReceiptBase(value);
  if (!base || value.contract == null) return base;
  const fields = [
    'bytes', 'contract', 'objectRef', 'path', 'previousVersionRef',
    'provenanceSha256', 'requirementId', 'role', 'sha256',
    'understandingSha256', 'versionRef',
  ];
  const exactShape = canonicalJson(Object.keys(value).sort()) === canonicalJson(fields)
    && value.contract === 'ArtifactVersionReceipt/v1'
    && ARTIFACT_VERSION_REF.test(String(value.versionRef || ''))
    && value.objectRef === `sha256:${value.sha256}`
    && Number.isSafeInteger(value.bytes) && value.bytes >= 0 && value.bytes <= ARTIFACT_MAX_BYTES
    && (value.previousVersionRef == null
      || ARTIFACT_VERSION_REF.test(String(value.previousVersionRef)))
    && ARTIFACT_SHA256.test(String(value.provenanceSha256 || ''));
  if (!exactShape) return null;
  return {
    contract: value.contract, versionRef: value.versionRef, objectRef: value.objectRef,
    ...base, bytes: value.bytes, previousVersionRef: value.previousVersionRef,
    provenanceSha256: value.provenanceSha256,
  };
}
export function createTurnEnvelope(value = {}) {
  if (!plainObject(value)) throw new TypeError('invalid_turn_envelope');
  const rawText = value.text ?? value.message?.text;
  if (typeof rawText !== 'string' || !rawText.trim() || rawText.length > MAX_TURN_TEXT) {
    throw new TypeError('invalid_turn_text');
  }
  const text = rawText;
  const messageId = String(value.messageId ?? value.message?.id ?? '').trim().slice(0, 160);
  const turnId = String(value.turnId || messageId || `turn:${sha256(text).slice(0, 24)}`).trim();
  if (!/^[a-z0-9][a-z0-9_.:@/-]{0,159}$/iu.test(turnId)) throw new TypeError('invalid_turn_id');
  const origin = ['user', 'self', 'resume'].includes(value.origin) ? value.origin : 'user';
  const raw = plainObject(value.context) ? value.context : {};
  const suppliedBatch = raw.signalBatch ?? value.signalBatch;
  if (origin !== 'user' && suppliedBatch != null && !validSignalBatch(suppliedBatch)) {
    throw new TypeError('invalid_signal_batch');
  }
  const signalBatch = origin !== 'user' && suppliedBatch?.signals?.length
    ? createSignalBatch(suppliedBatch.signals) : null;
  const dialogue = raw.dialogueExcerpts ?? value.dialogueExcerpts;
  const goal = raw.activeGoal ?? value.activeGoal;
  const receipts = raw.artifactReceipts ?? value.artifactReceipts;
  const context = {
    dialogueExcerpts: (Array.isArray(dialogue) ? dialogue : []).slice(-8).map((item) => ({
      role: ['user', 'assistant', 'eva'].includes(item?.role) ? item.role : 'user', text: limitedText(item?.text, 1_200),
      ts: limitedText(item?.ts, 40),
      evidenceClass: item?.evidenceClass === 'historical_unconfirmed'
        ? 'historical_unconfirmed' : 'conversation',
    })).filter((item) => item.text),
    requester: limitedText(raw.requester ?? value.requester ?? value.sender, 120),
    activeGoal: plainObject(goal) ? normalizeGoal(goal) : null,
    artifactReceipts: (Array.isArray(receipts) ? receipts : [])
      .slice(0, ARTIFACT_RECEIPT_LIMIT).map(projectedArtifactReceipt).filter(Boolean),
    ...(signalBatch ? { signalBatch } : {}),
  };
  return deepFreeze({
    contract: ENVELOPE_CONTRACT, turnId, channel: String(value.channel || 'chat').trim().slice(0, 40), origin,
    message: { id: messageId || null, text },
    priorUnderstandings: (Array.isArray(value.priorUnderstandings) ? value.priorUnderstandings.slice(-8) : [])
      .map(boundedUnderstanding).filter(Boolean),
    voiceRetryIntent: boundedVoiceRetryIntent(value.voiceRetryIntent),
    context,
  });
}
export function fileArgumentsMetadataValid(fileArguments, input) {
  if (fileArguments == null) return true;
  const extensionValid = (value) => /^\.[a-z0-9]{1,16}$/u.test(String(value || ''));
  const properties = input?.properties || {};
  const required = new Set(input?.required || []);
  const programField = String(fileArguments.programField || '');
  const argumentsField = String(fileArguments.argumentsField || '');
  const programs = fileArguments.materializedByProgram;
  const dependencies = fileArguments.readDependencies;
  if (!programField || !argumentsField || programField === argumentsField
    || !Object.hasOwn(properties, programField) || !Object.hasOwn(properties, argumentsField)
    || !required.has(programField) || properties[programField]?.type !== 'string'
    || properties[argumentsField]?.type !== 'array'
    || properties[argumentsField]?.items?.type !== 'string'
    || !plainObject(programs) || !Array.isArray(dependencies) || dependencies.length === 0
    || new Set(dependencies).size !== dependencies.length
    || !dependencies.every(extensionValid)) return false;
  const entries = Object.entries(programs);
  if (!entries.length || entries.some(([program, extensions]) => (
    !/^[a-z0-9][a-z0-9._-]*$/u.test(program)
      || !Array.isArray(extensions) || !extensions.length
      || new Set(extensions).size !== extensions.length
      || !extensions.every(extensionValid)
  ))) return false;
  const materialized = new Set(entries.flatMap(([, extensions]) => extensions));
  return dependencies.every((extension) => !materialized.has(extension));
}
function normalizedCatalogExtensions(value) {
  return [...new Set((Array.isArray(value) ? value : [])
    .map((item) => String(item || '').trim().toLowerCase()).filter(Boolean))];
}
export function normalizeFileArgumentsMetadata(value) {
  if (value == null) return null;
  if (!plainObject(value) || !plainObject(value.materializedByProgram)) {
    throw new TypeError('invalid_catalog_file_arguments');
  }
  return {
    programField: String(value.programField || ''),
    argumentsField: String(value.argumentsField || ''),
    materializedByProgram: Object.fromEntries(Object.entries(value.materializedByProgram)
      .map(([program, extensions]) => [
        String(program || '').trim().toLowerCase(), normalizedCatalogExtensions(extensions),
      ])),
    readDependencies: normalizedCatalogExtensions(value.readDependencies),
  };
}

export function semanticFailureResultMetadata(semanticTurn) {
  const understood = semanticTurn?.interpretationSucceeded === true;
  const failureStage = ['understanding', 'binding', 'authority'].includes(
    semanticTurn?.failureStage,
  ) ? semanticTurn.failureStage : understood ? 'binding' : 'understanding';
  return {
    understood,
    interpretationFailed: !understood,
    failureStage,
    ...(failureStage === 'understanding'
      ? {} : { planningError: String(semanticTurn?.error || 'capability_planning_failed') }),
  };
}

export function retainedTurnResultMetadata(result = {}) {
  const metadata = {};
  for (const key of ['understood', 'interpretationFailed', 'noPlan', 'recovered',
    'durableHandoff', 'executionPending', 'actionFailed', 'durableProofMissing',
    'superseded', 'yielded']) {
    if (typeof result[key] === 'boolean') metadata[key] = result[key];
  }
  if (['understanding', 'binding', 'authority'].includes(result.failureStage)) {
    metadata.failureStage = result.failureStage;
  }
  if (result.planningError) metadata.planningError = String(result.planningError).slice(0, 500);
  if (result.noPlanDecision) metadata.noPlanDecision = String(result.noPlanDecision).slice(0, 80);
  return metadata;
}

export const PREPARED_UNDERSTANDING_TURN_CONTRACT = 'PreparedUnderstandingTurn/v1';
const PREPARED_UNDERSTANDING_RESULT_KEYS = Object.freeze([
  'understood', 'interpretationFailed', 'noPlan', 'recovered',
  'durableHandoff', 'executionPending', 'actionFailed', 'durableProofMissing',
  'superseded', 'yielded', 'failureStage', 'planningError', 'noPlanDecision',
]);

function preparedTurnId(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

export function createPreparedUnderstandingTurn(result) {
  const metadata = retainedTurnResultMetadata(result);
  const projectedMetadata = Object.fromEntries(PREPARED_UNDERSTANDING_RESULT_KEYS
    .filter((key) => Object.hasOwn(metadata, key)).map((key) => [key, metadata[key]]));
  const body = {
    contract: PREPARED_UNDERSTANDING_TURN_CONTRACT,
    channel: String(result?.channel || ''),
    conversationId: String(result?.conversationId || ''),
    originMessageId: String(result?.originMessageId || ''),
    turnId: String(result?.turnId || ''),
    reply: String(result?.reply || ''),
    replySha256: sha256(String(result?.reply || '')),
    workItemId: preparedTurnId(result?.workItemId),
    goalEpisodeId: preparedTurnId(result?.goalEpisodeId),
    finalDeliveryBindingSha256: ARTIFACT_SHA256.test(
      String(result?.finalDeliveryBindingSha256 || ''),
    ) ? String(result.finalDeliveryBindingSha256) : null,
    syntheticTest: result?.syntheticTest === true,
    error: result?.error ? String(result.error).slice(0, 500) : null,
    metadata: projectedMetadata,
  };
  const serialized = canonicalJson(body);
  if (!body.channel || !body.conversationId || !body.originMessageId || !body.turnId
    || Buffer.byteLength(serialized, 'utf8') > 24_000) {
    throw new Error('voorbereid ingressantwoord is niet veilig begrensd');
  }
  return Object.freeze({ ...body, sha256: sha256(serialized) });
}

export function verifyPreparedUnderstandingTurn(value, expected = {}) {
  const expectedKeys = [
    'channel', 'contract', 'conversationId', 'error', 'finalDeliveryBindingSha256',
    'goalEpisodeId', 'metadata', 'originMessageId', 'reply', 'replySha256',
    'sha256', 'syntheticTest', 'turnId', 'workItemId',
  ].sort();
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort()) !== canonicalJson(expectedKeys)
    || value.contract !== PREPARED_UNDERSTANDING_TURN_CONTRACT
    || !['telegram', 'voice', 'web'].includes(value.channel)
    || (expected.channel != null && value.channel !== expected.channel)
    || (expected.conversationId != null
      && value.conversationId !== expected.conversationId)
    || (expected.messageId != null
      && value.originMessageId !== String(expected.messageId))
    || typeof value.conversationId !== 'string' || !value.conversationId
    || typeof value.originMessageId !== 'string' || !value.originMessageId
    || typeof value.turnId !== 'string' || !value.turnId
    || typeof value.syntheticTest !== 'boolean'
    || typeof value.reply !== 'string' || value.replySha256 !== sha256(value.reply)
    || !(value.workItemId === null || preparedTurnId(value.workItemId) === value.workItemId)
    || !(value.goalEpisodeId === null
      || preparedTurnId(value.goalEpisodeId) === value.goalEpisodeId)
    || !(value.error === null || (typeof value.error === 'string' && value.error.length <= 500))
    || !(value.finalDeliveryBindingSha256 === null
      || ARTIFACT_SHA256.test(value.finalDeliveryBindingSha256))) return null;
  const projectedMetadata = retainedTurnResultMetadata(value.metadata || {});
  if (canonicalJson(projectedMetadata) !== canonicalJson(value.metadata || {})) return null;
  const { sha256: claimed, ...body } = value;
  const serialized = canonicalJson(body);
  if (!ARTIFACT_SHA256.test(String(claimed || ''))
    || claimed !== sha256(serialized)
    || Buffer.byteLength(serialized, 'utf8') > 24_000) return null;
  return Object.freeze(value);
}
function catalogExecutionValid(execution, input, ownKey) {
  if (execution.mode === 'direct') return execution.target == null
    && execution.materializable.length === 0 && execution.proofOperations.length === 0
    && execution.recoveryOperations.length === 0
    && fileArgumentsMetadataValid(execution.fileArguments, input);
  const fields = new Set(Object.keys(input?.properties || {}));
  const required = new Set(input?.required || []);
  const target = execution.target;
  return execution.fileArguments == null && execution.materializable.length > 0
    && execution.materializable.every((field) => !fields.has(field))
    && target?.boundary === 'workspace' && target.sameDirectory === true
    && fields.has(target.field) && required.has(target.field)
    && !execution.materializable.includes(target.field)
    && Number.isInteger(target.artifactLimit) && target.artifactLimit >= 1 && target.artifactLimit <= 12
    && execution.proofOperations.length > 0 && execution.recoveryOperations.length > 0
    && execution.proofOperations.some((reference) => (
      `${reference.capabilityId}\u0000${reference.operationId}` === ownKey
    ));
}
function normalizeCatalog(value = []) {
  const operations = new Map();
  for (const capability of Array.isArray(value) ? value : []) {
    const capabilityId = identifier(capability.id ?? capability.capabilityId, 'capability_id');
    for (const raw of Array.isArray(capability.operations) ? capability.operations : []) {
      const operation = typeof raw === 'string' ? { id: raw } : raw;
      const operationId = identifier(operation.id ?? operation.operationId, 'operation_id');
      const effect = String(operation.effect || 'read').toLowerCase();
      if (!EFFECTS.includes(effect)) throw new TypeError('invalid_catalog_effect');
      const freshnessClass = String(operation.freshnessClass || 'static').toLowerCase();
      if (!['historical', 'live', 'static'].includes(freshnessClass)) {
        throw new TypeError('invalid_catalog_freshness_class');
      }
      const proofNeeds = (Array.isArray(operation.proofNeeds) ? operation.proofNeeds : [])
        .map((proof) => identifier(typeof proof === 'string' ? proof : proof.kind, 'proof_kind'));
      const input = jsonValue(plainObject(operation.input) ? operation.input
        : { type: 'object', properties: {}, required: [], additionalProperties: false });
      const operationFields = (name) => [...new Set((Array.isArray(operation[name])
        ? operation[name] : []).map(String))];
      const rebindableFields = operationFields('rebindableFields'), sourceBoundFields = operationFields('sourceBoundFields');
      const signalScope = operationFields('signalScope'), signalKeyField = String(operation.signalKeyField || '');
      if ([...rebindableFields, ...sourceBoundFields].some((field) => !Object.hasOwn(input.properties || {}, field))) throw new TypeError('invalid_catalog_bound_field');
      if (rebindableFields.some((field) => !stepReferenceKind(field))) throw new TypeError('invalid_catalog_rebindable_field');
      if (signalScope.some((selector) => !/^[a-z][a-z0-9_-]{0,59}:(?:[a-z0-9_]+\.[a-z0-9_]+|[a-z0-9_]+\.\*)$/u.test(selector))
        || (signalScope.length ? !sourceBoundFields.includes(signalKeyField) : signalKeyField)) {
        throw new TypeError('invalid_catalog_signal_scope');
      }
      const rawExecution = plainObject(operation.execution) ? operation.execution : {};
      const executionMode = String(rawExecution.mode || 'direct');
      if (!['direct', 'iterative'].includes(executionMode)) throw new TypeError('invalid_catalog_execution_mode');
      const operationRefs = (items) => (Array.isArray(items) ? items : []).map((reference) => ({
        capabilityId: identifier(reference?.capabilityId, 'execution_capability_id'),
        operationId: identifier(reference?.operationId, 'execution_operation_id'),
        type: identifier(reference?.type, 'execution_type'),
      }));
      const execution = {
        mode: executionMode,
        materializable: [...new Set((Array.isArray(rawExecution.materializable)
          ? rawExecution.materializable : []).map((field) => identifier(field, 'materializable_field')))],
        target: plainObject(rawExecution.target) ? {
          field: identifier(rawExecution.target.field, 'execution_target_field'),
          boundary: String(rawExecution.target.boundary || ''),
          sameDirectory: rawExecution.target.sameDirectory === true,
          artifactLimit: Math.trunc(Number(rawExecution.target.artifactLimit)),
        } : null,
        proofOperations: operationRefs(rawExecution.proofOperations),
        recoveryOperations: operationRefs(rawExecution.recoveryOperations),
        fileArguments: normalizeFileArgumentsMetadata(rawExecution.fileArguments),
      };
      const operationKey = `${capabilityId}\u0000${operationId}`;
      if (!catalogExecutionValid(execution, input, operationKey)) {
        throw new TypeError('invalid_catalog_execution');
      }
      operations.set(operationKey, deepFreeze({
        capabilityId, operationId, effect, proofNeeds: [...new Set(proofNeeds)].sort(),
        supportsDeferred: operation.supportsDeferred === true,
        fastlane: String(operation.fastlane || ''), input, execution, rebindableFields, sourceBoundFields,
        signalScope, signalKeyField, freshnessClass,
      }));
    }
  }
  for (const operation of operations.values()) {
    for (const reference of [...operation.execution.proofOperations, ...operation.execution.recoveryOperations]) {
      if (!operations.has(`${reference.capabilityId}\u0000${reference.operationId}`)) {
        throw new TypeError('unknown_catalog_execution_operation');
      }
    }
  }
  return operations;
}
function projectSignalOperations(operations, batch) {
  if (!batch?.signals?.length) return operations;
  const projected = new Map(), covered = new Set();
  for (const [key, operation] of operations) {
    if (key === 'signal.assess\u0000assess') {
      projected.set(key, operation); continue;
    }
    if (!operation.signalScope.length || !operation.signalKeyField) continue;
    const matches = batch.signals.filter((signal) => operation.signalScope.some(
      (selector) => signalMatchesScopeSelector(signal, selector),
    ));
    const values = [...new Set(matches.map((signal) => signal.key))];
    if (!values.length) continue;
    for (const signal of matches) covered.add(signal.id);
    const field = operation.signalKeyField;
    const input = structuredClone(operation.input);
    input.properties[field] = { ...input.properties[field], enum: values };
    const narrowed = deepFreeze({ ...operation, input });
    PROJECTED_SIGNAL_OPERATIONS.set(narrowed, {
      field, idsByValue: new Map(values.map((value) => [value,
        new Set(matches.filter((signal) => signal.key === value).map((signal) => signal.id))])),
    });
    projected.set(key, narrowed);
  }
  if (batch.signals.some((signal) => !covered.has(signal.id))) throw new TypeError('unsupported_signal_scope');
  return projected;
}
function projectedSignalEffectAvailable(operations, accepts) {
  return [...operations.values()].some((operation) => (
    PROJECTED_SIGNAL_OPERATIONS.has(operation) && accepts(operation.effect)
  ));
}
function projectedSignalMutationAvailable(operations) {
  return projectedSignalEffectAvailable(operations, (effect) => MUTATING_EFFECTS.has(effect));
}
function projectedSignalReadAvailable(operations) {
  return projectedSignalEffectAvailable(operations, (effect) => effect === 'read');
}
function projectedSignalAssessmentInput(operations) {
  const operation = operations.get('signal.assess\u0000assess');
  if (!operation) return null;
  const acted = projectedSignalMutationAvailable(operations);
  const observed = projectedSignalReadAvailable(operations);
  const input = structuredClone(operation.input);
  const disposition = input.properties?.disposition;
  if (Array.isArray(disposition?.enum)) {
    input.properties.disposition = {
      ...disposition, enum: disposition.enum.filter((value) => (
        (value !== 'acted' || acted) && (value !== 'observed' || observed)
      )),
    };
  }
  return deepFreeze(input);
}
function projectActiveWorkOperations(operations, workId) {
  if (workId == null) return operations;
  const id = Number(workId);
  if (!Number.isSafeInteger(id) || id < 1) throw new TypeError('invalid_active_work_id');
  const projected = new Map(operations);
  for (const [key, operation] of operations) {
    if (operation.capabilityId !== 'work.episode.manage' || operation.operationId === 'start') continue;
    const input = structuredClone(operation.input);
    input.properties.id = { ...input.properties.id, enum: [id] };
    const narrowed = deepFreeze({ ...operation, input });
    PROJECTED_ACTIVE_WORK_OPERATIONS.set(narrowed, id);
    projected.set(key, narrowed);
  }
  return projected;
}
function projectSelfAttentionGoalOperations(operations, selfAttention) {
  if (selfAttention?.kind !== 'goal') return operations;
  if (typeof selfAttention.goalKey !== 'string' || !selfAttention.goalKey
    || !SELF_ATTENTION_GOAL_REF.test(selfAttention.goalRef || '')
    || !Number.isSafeInteger(selfAttention.goalRevision) || selfAttention.goalRevision < 1
    || !Number.isFinite(Date.parse(selfAttention.claimedUntil || ''))) {
    throw new TypeError('invalid_self_attention_goal_binding');
  }
  const projected = new Map(operations);
  for (const [key, operation] of operations) {
    if (operation.capabilityId !== 'goal.portfolio.manage'
      || !SELF_ATTENTION_GOAL_MANAGE_OPERATIONS.has(operation.operationId)) continue;
    const input = structuredClone(operation.input);
    if (!plainObject(input.properties?.doel_sleutel)
      || !plainObject(input.properties?.verwachte_revisie)
      || (operation.operationId === 'update'
        && !plainObject(input.properties?.volgende_review))) {
      throw new TypeError('invalid_self_attention_goal_catalog');
    }
    const { doel_sleutel: goalKeyProperty } = input.properties;
    const updateProperties = operation.operationId === 'update'
      ? Object.fromEntries(Object.entries(input.properties).filter(([field]) => (
        SELF_ATTENTION_GOAL_UPDATE_FIELDS.has(field)
      ))) : {};
    input.properties = {
      ...updateProperties,
      doel_ref: {
        ...goalKeyProperty,
        description: 'Tijdelijke, opaque doelreferentie voor uitsluitend deze reviewbeurt.',
        enum: [selfAttention.goalRef],
      },
      verwachte_revisie: {
        ...input.properties.verwachte_revisie, enum: [selfAttention.goalRevision],
      },
    };
    input.required = [
      'doel_ref', 'verwachte_revisie',
      ...(operation.operationId === 'update' ? ['volgende_review'] : []),
    ];
    const narrowed = deepFreeze({ ...operation, input });
    PROJECTED_SELF_ATTENTION_GOAL_OPERATIONS.set(narrowed, Object.freeze({
      selfAttention, operationId: operation.operationId,
    }));
    projected.set(key, narrowed);
  }
  return projected;
}
function verifiedExplorationContinuation(selfAttention) {
  const context = selfAttention?.explorationContext;
  return selfAttention?.kind === 'exploration'
    && validExplorationContinuationContext(context)
    && context.id === selfAttention.id
    && context.revision === selfAttention.explorationRevision
    && context.questionSha256 === selfAttention.attentionSha256
    && context.questionSha256 === sha256(context.question)
    ? context : null;
}
function exhaustedExplorationContinuation(selfAttention) {
  const context = verifiedExplorationContinuation(selfAttention);
  return context !== null
    && (context.status === 'evaluating' || context.actionCount >= context.maxActions);
}
function explorationContinuationOperationAllowed(selfAttention, operationId) {
  return verifiedExplorationContinuation(selfAttention) !== null
    && (!exhaustedExplorationContinuation(selfAttention)
      || SELF_ATTENTION_EXPLORATION_TERMINAL.has(operationId));
}
function explorationTurnOperationAllowed(operation, exhausted) {
  if (exhausted) {
    return operation.capabilityId === 'exploration.manage'
      && SELF_ATTENTION_EXPLORATION_TERMINAL.has(operation.operationId);
  }
  if (operation.capabilityId === 'exploration.manage') {
    return SELF_ATTENTION_EXPLORATION_CONTINUATIONS.has(operation.operationId);
  }
  return SELF_ATTENTION_PROJECTABLE_EFFECTS.has(operation.effect)
    && operation.capabilityId !== 'goal.portfolio.read';
}
function projectSelfAttentionExplorationOperations(operations, selfAttention) {
  if (selfAttention?.kind !== 'exploration') return operations;
  const context = verifiedExplorationContinuation(selfAttention);
  if (!context) throw new TypeError('invalid_self_attention_exploration_binding');
  const exhausted = exhaustedExplorationContinuation(selfAttention);
  const projected = new Map([...operations].filter(([, operation]) => (
    explorationTurnOperationAllowed(operation, exhausted)
  )));
  for (const [key, operation] of operations) {
    if (operation.capabilityId !== 'exploration.manage'
      || !SELF_ATTENTION_EXPLORATION_CONTINUATIONS.has(operation.operationId)
      || (exhausted
        && !SELF_ATTENTION_EXPLORATION_TERMINAL.has(operation.operationId))) continue;
    const input = structuredClone(operation.input);
    if (!plainObject(input.properties?.id)
      || !plainObject(input.properties?.verwachte_revisie)
      || (SELF_ATTENTION_EXPLORATION_TERMINAL.has(operation.operationId)
        && !plainObject(input.properties?.resultaat))
      || (operation.operationId === 'wachten'
        && !plainObject(input.properties?.hervatten_na))) {
      throw new TypeError('invalid_self_attention_exploration_catalog');
    }
    input.properties.id = { ...input.properties.id, enum: [selfAttention.id] };
    input.properties.verwachte_revisie = {
      ...input.properties.verwachte_revisie,
      enum: [context.revision],
    };
    input.required = [...new Set([
      ...(Array.isArray(input.required) ? input.required : []),
      'id', 'verwachte_revisie',
      ...(SELF_ATTENTION_EXPLORATION_TERMINAL.has(operation.operationId)
        ? ['resultaat'] : []),
      ...(operation.operationId === 'wachten' ? ['hervatten_na'] : []),
    ])];
    projected.set(key, deepFreeze({ ...operation, input }));
  }
  return projected;
}
function assertExplorationTurnFields(raw, selfAttention, source) {
  if (source !== 'model' || verifiedExplorationContinuation(selfAttention) === null) return;
  if (raw.durability !== 'turn') throw new TypeError('exploration_durability_not_turn');
  const temporal = plainObject(raw.temporal) ? raw.temporal : null;
  if (!temporal || temporal.mode !== 'now'
    || !Array.isArray(temporal.startSourceSpans)
    || temporal.startSourceSpans.length !== 0
    || Object.keys(temporal).some((field) => !['mode', 'startSourceSpans'].includes(field))) {
    throw new TypeError('exploration_temporal_not_current');
  }
  const delivery = plainObject(raw.delivery) ? raw.delivery : null;
  if (!delivery || !['none', 'silent'].includes(delivery.mode)
    || delivery.target !== 'none'
    || Object.keys(delivery).some((field) => !['mode', 'target'].includes(field))) {
    throw new TypeError('exploration_delivery_not_current');
  }
}
function fileExtension(value) {
  const basename = String(value || '').replaceAll('\\', '/').split('/').pop() || '';
  const separator = basename.lastIndexOf('.');
  return separator > 0 ? basename.slice(separator).toLowerCase() : '';
}
function programFileArguments(operation, semanticInput) {
  const policy = operation.execution.fileArguments;
  if (!policy) return null;
  const runtime = String(semanticInput[policy.programField] || '')
    .replaceAll('\\', '/').split('/').pop().toLowerCase();
  const args = Array.isArray(semanticInput[policy.argumentsField])
    ? semanticInput[policy.argumentsField].map((value) => String(value || '').trim()) : [];
  return {
    runtime,
    args,
    materializedExtensions: policy.materializedByProgram[runtime] || null,
  };
}
function programFileArgumentInstruction(operations) {
  const operation = operations.get('workspace.program.execute\u0000execute');
  const policy = operation?.execution?.fileArguments;
  if (!policy) return '';
  const entrypoints = Object.entries(policy.materializedByProgram)
    .map(([program, extensions]) => `${program}: ${extensions.join('/')}`).join('; ');
  const dependencies = policy.readDependencies.join('/');
  return `Programma-entrypoints per runtime: ${entrypoints}; eerste niet-optieargument is entrypoint. ${dependencies} is read-dependency, geen entrypoint.`;
}
function concreteWorkspaceReadPath(value) {
  const text = String(value || '').trim();
  if (!text || text.includes('\0') || /[\r\n]/u.test(text) || hasUnresolvedReference(text)) return false;
  const portable = text.replaceAll('\\', '/');
  if (portable.split('/').includes('..')) return false;
  const basename = portable.split('/').pop() || '';
  return portable.includes('/')
    || /\.[A-Za-z0-9]{1,16}$/u.test(basename)
    || /^(?:agents|license|readme)$/iu.test(basename);
}
function workspaceContextPath(value, cwd = '') {
  const clean = (item) => String(item || '').trim().replaceAll('\\', '/').replace(/^\.\//u, '');
  const candidate = clean(value);
  if (!candidate || candidate.split('/').includes('..')) return '';
  const marker = candidate.indexOf('data/workspace/');
  if (marker >= 0) return candidate.slice(marker).split('/').filter((part) => part !== '.').join('/');
  const base = clean(cwd);
  const baseMarker = base.indexOf('data/workspace');
  const workspace = baseMarker >= 0 ? base.slice(baseMarker).replace(/\/$/u, '') : 'data/workspace';
  return `${workspace}/${candidate}`.split('/').filter((part) => part !== '.').join('/');
}
function workspacePathHasReceipt(value, cwd, receipts, priorActions) {
  const candidate = workspaceContextPath(value, cwd);
  const priorWrites = priorActions.filter((action) => action.authorization === 'allowed'
    && action.polarity === 'request' && action.capabilityId === 'workspace.file.write')
    .map((action) => workspaceContextPath(action.semanticInput.pad || action.semanticInput.path));
  return candidate && [...receipts.map((item) => item.path), ...priorWrites].includes(candidate);
}
function workspaceWriteHasReceipt(value, receipts, operations) {
  const candidate = workspaceContextPath(value);
  const paths = receipts.map((item) => item.path);
  if (paths.includes(candidate)) return true;
  const executable = [...operations.values()].flatMap((operation) => (
    Object.values(operation.execution.fileArguments?.materializedByProgram || {}).flat()
  )).includes(fileExtension(candidate));
  const parent = (item) => item.slice(0, item.lastIndexOf('/'));
  return !executable && paths.some((item) => parent(item) === parent(candidate));
}
function deferredWorkspaceReadPath(value, priorActions = []) {
  return hasUnresolvedReference(value)
    && priorActions.some((action) => (
      action.polarity === 'request'
      && action.capabilityId === 'workspace.file.list'
      && action.operationId === 'list'
      && action.boundaryReasons.length === 0
    ));
}
function validateWorkspaceAction({
  capabilityId, operationId, semanticInput, operation, polarity,
  artifactReceipts, priorActions, operations,
}) {
  if (polarity !== 'request') return;
  if (capabilityId === 'workspace.file.read' && operationId === 'read') {
    const concrete = concreteWorkspaceReadPath(semanticInput.pad);
    if (artifactReceipts.length
      ? !concrete || !workspacePathHasReceipt(semanticInput.pad, '', artifactReceipts, priorActions)
      : !concrete && !deferredWorkspaceReadPath(semanticInput.pad, priorActions)) {
      throw new TypeError(artifactReceipts.length
        ? 'workspace_path_not_bound_to_receipt' : 'workspace_read_requires_concrete_path');
    }
  }
  if (artifactReceipts.length && capabilityId === 'workspace.file.list') {
    const directory = workspaceContextPath(semanticInput.pad || '.');
    const parents = artifactReceipts.map((item) => item.path.slice(0, item.path.lastIndexOf('/')));
    if (!parents.includes(directory)) throw new TypeError('workspace_path_not_bound_to_receipt');
  }
  if (artifactReceipts.length && capabilityId === 'workspace.file.write'
    && !workspaceWriteHasReceipt(semanticInput.pad, artifactReceipts, operations)) {
    throw new TypeError('workspace_path_not_bound_to_receipt');
  }
  if (capabilityId !== 'workspace.program.execute' || operationId !== 'execute') return;
  const fileArguments = programFileArguments(operation, semanticInput);
  const { runtime, args, materializedExtensions } = fileArguments || {
    runtime: '', args: [], materializedExtensions: null,
  };
  const entrypoint = args.find((value) => !value.startsWith('-'));
  if (fileArguments && ((!materializedExtensions && runtime !== 'npm') || (materializedExtensions
    && (!entrypoint || !materializedExtensions.includes(fileExtension(entrypoint)))))) {
    throw new TypeError('workspace_program_missing_concrete_entrypoint');
  }
  if (runtime === 'npm' && !['test', 'run'].includes(String(args[0] || '').toLowerCase())) {
    throw new TypeError('workspace_program_missing_concrete_entrypoint');
  }
  const boundFiles = args.filter((value) =>
    [...(materializedExtensions || []), ...(operation.execution.fileArguments?.readDependencies || [])]
      .includes(fileExtension(value)));
  if (artifactReceipts.length && boundFiles.some((value) => (
    !workspacePathHasReceipt(value, semanticInput.werkmap, artifactReceipts, priorActions)
  ))) {
    throw new TypeError('workspace_path_not_bound_to_receipt');
  }
}
function compactFieldType(value) {
  if (!plainObject(value)) return '?';
  const bounds = (lower, upper) => {
    const hasLower = Object.hasOwn(value, lower) && Number.isFinite(value[lower]);
    const hasUpper = Object.hasOwn(value, upper) && Number.isFinite(value[upper]);
    return hasLower || hasUpper ? [hasLower ? value[lower] : null, hasUpper ? value[upper] : null]
      : null;
  };
  if (value.type === 'array') {
    const compact = { a: compactFieldType(value.items) };
    const limit = bounds('minItems', 'maxItems');
    return limit ? { ...compact, b: limit } : compact;
  }
  if (value.type === 'object' || plainObject(value.properties)) {
    const fields = Object.keys(value.properties || {}).sort();
    const required = [...new Set(
      (Array.isArray(value.required) ? value.required : []).map(String),
    )].sort();
    return {
      r: fields.length > 0 && required.length === fields.length ? 'all' : required,
      f: Object.fromEntries(fields.map((key) => [key, compactFieldType(value.properties[key])])),
    };
  }
  const type = structuredClone(value.type ?? '?');
  /** @type {{ k: unknown, e?: unknown, p?: unknown, b?: (number | null)[] }} */
  const constraints = {
    k: type,
    ...(Array.isArray(value.enum) ? { e: structuredClone(value.enum) } : {}),
    ...(Object.hasOwn(value, 'pattern') ? { p: value.pattern } : {}),
  };
  const limit = ['number', 'integer'].includes(type)
    ? bounds('minimum', 'maximum') : bounds('minLength', 'maxLength');
  if (limit) constraints.b = limit;
  return Object.keys(constraints).length === 1 ? constraints.k : constraints;
}
function catalogPrompt(operations) {
  const capabilities = [];
  const fieldTypes = {};
  const fieldTypeIds = new Map();
  const fieldMaps = {};
  const fieldMapIds = new Map();
  const inputSchemas = {};
  const inputSchemaIds = new Map();
  const proofSets = {};
  const proofSetIds = new Map();
  const freshnessCodes = new Map([
    ['static', 0], ['live', 1], ['historical', 2],
  ]);
  const internFieldType = (value) => {
    const signature = compactFieldType(value);
    const key = canonicalJson(signature);
    let reference = fieldTypeIds.get(key);
    if (!reference) {
      reference = `t${fieldTypeIds.size + 1}`;
      fieldTypeIds.set(key, reference);
      fieldTypes[reference] = signature;
    }
    return reference;
  };
  const internFieldMap = (value) => {
    const fields = Object.keys(value.properties || {}).sort();
    const signature = Object.fromEntries(fields
      .map((key) => [key, internFieldType(value.properties[key])]));
    const key = canonicalJson(signature);
    let reference = fieldMapIds.get(key);
    if (!reference) {
      reference = `f${fieldMapIds.size + 1}`;
      fieldMapIds.set(key, reference);
      fieldMaps[reference] = signature;
    }
    return reference;
  };
  const internInputSchema = (value) => {
    const fields = Object.keys(value.properties || {}).sort();
    const required = [...new Set(
      (Array.isArray(value.required) ? value.required : []).map(String),
    )].sort();
    const schema = {
      r: fields.length > 0 && required.length === fields.length ? 'all' : required,
      f: internFieldMap(value),
    };
    const key = canonicalJson(schema);
    let reference = inputSchemaIds.get(key);
    if (!reference) {
      reference = `s${inputSchemaIds.size + 1}`;
      inputSchemaIds.set(key, reference);
      inputSchemas[reference] = schema;
    }
    return reference;
  };
  const internProofSet = (value) => {
    const proofs = [...new Set((Array.isArray(value) ? value : []).map(String))].sort();
    const key = canonicalJson(proofs);
    let reference = proofSetIds.get(key);
    if (!reference) {
      reference = `p${proofSetIds.size + 1}`;
      proofSetIds.set(key, reference);
      proofSets[reference] = proofs;
    }
    return reference;
  };
  const sorted = [...operations.values()].sort((left, right) => (
    left.capabilityId.localeCompare(right.capabilityId)
      || left.operationId.localeCompare(right.operationId)
  ));
  for (const operation of sorted) {
    let capability = capabilities.at(-1);
    if (!capability || capability[0] !== operation.capabilityId) {
      capability = [operation.capabilityId, [], []];
      capabilities.push(capability);
    }
    const item = [operation.operationId, operation.effect, operation.supportsDeferred ? 1 : 0,
      internInputSchema(operation.input), operation.execution.mode === 'iterative' ? 1 : 0,
      operation.execution.materializable, operation.execution.target?.field ?? 0,
      internProofSet(operation.proofNeeds), operation.rebindableFields];
    if (operation.sourceBoundFields.length) item.push(operation.sourceBoundFields);
    const freshness = freshnessCodes.get(operation.freshnessClass);
    if (!Number.isInteger(freshness)) throw new TypeError('invalid_catalog_freshness_class');
    capability[1].push(item);
    capability[2].push(freshness);
  }
  for (const capability of capabilities) {
    if (capability[2].every((freshness) => freshness === capability[2][0])) {
      capability[2] = capability[2][0];
    }
  }
  return {
    catalogFormat: {
      contract: 'CapabilityCatalogPrompt/v2',
      capability: ['id', 'operations',
        'freshness(0=static|1=live|2=historical|[]=per-operation)'],
      schema: ['r(required|all)', 'f(fieldMapRef)'],
      fieldMap: 'field:typeRef',
      fieldType: 'primitive|k(kind),e(enum),p(pattern),a(items),r/f(object),b([lower|null,upper|null] by kind)',
      operation: ['id', 'effect', 'defer', 'schema', 'iter', 'material',
        'target(0=none|field)', 'proofs', 'rebind', 'source?'],
    },
    capabilities,
    inputSchemas,
    fieldMaps,
    fieldTypes,
    proofSets,
  };
}
function quoteRanges(text) {
  const ranges = [];
  const patterns = [/"[^"\n]+"/gu, /“[^”\n]+”/gu, /‘[^’\n]+’/gu, /(?:^|\s)'[^'\n]+'(?=$|\s|[.,!?;])/gu];
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const leading = match[0].startsWith(' ') ? 1 : 0;
      ranges.push({ start: Number(match.index) + leading, end: Number(match.index) + match[0].length });
    }
  }
  return ranges.sort((left, right) => left.start - right.start);
}
function clauseRanges(text) {
  const ranges = [];
  const separator = /[.!?;\n]+|\b(?:maar|but)\b/giu;
  let start = 0;
  for (const match of text.matchAll(separator)) {
    const end = Number(match.index);
    if (end > start) ranges.push({ start, end });
    start = end + match[0].length;
  }
  if (start < text.length) ranges.push({ start, end: text.length });
  return ranges.length ? ranges : [{ start: 0, end: text.length }];
}
function normalizeSpans(value, text) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 6).map((span) => ({
    start: Math.max(0, Math.min(text.length, Math.trunc(Number(span?.start)))),
    end: Math.max(0, Math.min(text.length, Math.trunc(Number(span?.end)))),
  })).filter((span) => Number.isFinite(span.start) && Number.isFinite(span.end) && span.end > span.start)
    .sort((left, right) => left.start - right.start);
}
function overlaps(left, right) { return left.start < right.end && right.start < left.end; }
function normalizeTemporalSourceSpans(value, text) {
  if (value == null) return [];
  if (!Array.isArray(value) || value.length > 4) throw new TypeError('invalid_temporal_source_spans');
  const spans = value.map((span) => {
    const start = Number(span?.start);
    const end = Number(span?.end);
    if (!Number.isInteger(start) || !Number.isInteger(end)
      || start < 0 || end > text.length || end <= start || end - start > 320) {
      throw new TypeError('invalid_temporal_source_span');
    }
    return { start, end };
  }).sort((left, right) => left.start - right.start || left.end - right.end);
  return spans.filter((span, index) => index === 0
    || span.start !== spans[index - 1].start || span.end !== spans[index - 1].end);
}
function markerIsQuoted(text, start, end) {
  return quoteRanges(text).some((quote) => overlaps({ start, end }, quote));
}
function markerIsNonConditionalPresentation(text, marker, start, end) {
  return String(marker || '').toLocaleLowerCase('nl-NL') === 'als'
    && (NON_CONDITIONAL_PRESENTATION_AFTER_ALS.test(text.slice(end, end + 96))
      || (PRESENTATION_BEFORE_ALS.test(text.slice(Math.max(0, start - 96), start))
        && PRESENTATION_ROLE_AFTER_ALS.test(text.slice(end, end + 96))));
}
function hasConditionalMarker(text) {
  for (const match of String(text || '').matchAll(CONDITIONAL)) {
    const start = Number(match.index), end = start + match[0].length;
    if (!markerIsNonConditionalPresentation(text, match[0], start, end)) return true;
  }
  return false;
}
function hasLaterStartEvidence(text, spans) {
  for (const span of spans) {
    const evidence = text.slice(span.start, span.end);
    for (const match of evidence.matchAll(LATER_START_MARKER)) {
      const start = span.start + Number(match.index);
      const end = start + match[0].length;
      const prefix = text.slice(Math.max(0, start - 48), start);
      const vicinity = text.slice(Math.max(0, start - 16), Math.min(text.length, end + 24));
      if (!DEADLINE_PREFIX.test(prefix) && !NEGATED_LATER.test(vicinity)
        && !markerIsQuoted(text, start, end)) return true;
    }
  }
  return false;
}
function hasConditionStartEvidence(text, spans) {
  for (const span of spans) {
    const evidence = text.slice(span.start, span.end);
    for (const match of evidence.matchAll(CONDITION_START_MARKER)) {
      const start = span.start + Number(match.index);
      const end = start + match[0].length;
      if (!markerIsQuoted(text, start, end)
        && !markerIsNonConditionalPresentation(text, match[0], start, end)) return true;
    }
  }
  return false;
}
function projectedConditionStartSourceSpans(text) {
  const spans = [];
  for (const match of String(text || '').matchAll(CONDITION_START_MARKER)) {
    const start = Number(match.index), end = start + match[0].length;
    if (markerIsQuoted(text, start, end)
      || markerIsNonConditionalPresentation(text, match[0], start, end)) continue;
    spans.push({ start, end });
    if (spans.length >= 4) break;
  }
  return spans;
}
function actionBoundary(text, spans, temporal, operation, classifyBoundary = effectBoundary) {
  const quotes = quoteRanges(text);
  const clauses = clauseRanges(text);
  const selected = clauses.filter((clause) => spans.some((span) => overlaps(span, clause)));
  const boundaryText = operation.fastlane === 'literal_voice' ? text : maskAdditiveNegation(
    text.split('').map((character, index) => (
      quotes.some(({ start, end }) => index >= start && index < end) ? ' ' : character)).join(''),
  );
  const scopes = operation.fastlane === 'literal_voice'
    ? [spans.map((span) => text.slice(span.start, span.end)).join(' ')]
    : selected.map((clause) => boundaryText.slice(clause.start, clause.end));
  const scope = scopes.join(' ');
  const reasons = [];
  if (!spans.length) reasons.push('missing_source_span');
  if (spans.some((span) => quotes.some((quote) => overlaps(span, quote)))) reasons.push('quoted');
  // Classificeer iedere grammaticale clause apart. Door clauses eerst samen te
  // voegen werd "schrijf lokaal, maar publiceer niets" ten onrechte gelezen als
  // "schrijf ... niets". Een ongespecificeerde ontkenning blijft fail-closed;
  // een aantoonbare ontkenning van een ander effect besmet deze actie niet.
  const negatedForOperation = scopes.some((clause) => {
    const scopedEffects = classifyBoundary(clause);
    if (scopedEffects.denied.includes(operation.effect)) return true;
    const exception = ['read', 'discovery'].includes(operation.effect)
      ? READ_NON_ACTION_NEGATION : NON_ACTION_NEGATION;
    const rawNegation = MUTATION_NEGATION.test(clause.replace(exception, '').replace(PRESENCE_STATE_NEGATION, ''));
    if (!rawNegation) return false;
    return scopedEffects.requestedEffects.length === 0;
  });
  if (negatedForOperation) reasons.push('negated');
  if (HYPOTHETICAL.test(scope) && !POLITE_REQUEST.test(scope)) reasons.push('hypothetical');
  const boundedIterativeRecovery = temporal.mode === 'now'
    && operation.execution?.mode === 'iterative'
    && ITERATIVE_RECOVERY_CONDITION.test(scope);
  if (hasConditionalMarker(scope) && !operation.supportsDeferred && !boundedIterativeRecovery) {
    reasons.push('conditional');
  }
  // "Annuleer werk #104" is zelf een positieve opdracht aan een beheer-
  // operatie. Hetzelfde woord begrenst nog steeds iedere andere voorgestelde
  // handeling (zoals een eerdere omroep of write).
  if (CANCEL.test(scope) && !CANCELLATION_OPERATION_IDS.has(operation.operationId)) {
    reasons.push('cancelled');
  }
  // A current read can legitimately concern a future date: "kijk nu naar het
  // weer van morgen". The model's explicit temporal mode governs when the
  // read runs; a date inside the subject is not itself a deferred execution.
  const deferred = temporal.mode === 'later' || temporal.mode === 'condition'
    || (!['read', 'discovery'].includes(operation.effect) && DEFERRED.test(scope));
  if (deferred && !operation.supportsDeferred) reasons.push('deferred');
  return [...new Set(reasons)].sort();
}
function normalizeTemporal(value, text, now) {
  const raw = plainObject(value) ? value : {};
  const mode = TEMPORAL_MODES.has(raw.mode) ? raw.mode : 'now';
  const iso = (item) => {
    if (item == null || item === '') return null;
    const date = new Date(item);
    if (!Number.isFinite(date.getTime())) throw new TypeError('invalid_temporal_value');
    return date.toISOString();
  };
  const notBefore = iso(raw.notBefore);
  const dueBy = iso(raw.dueBy);
  if (notBefore && dueBy && Date.parse(dueBy) < Date.parse(notBefore)) throw new TypeError('invalid_temporal_order');
  const condition = mode === 'condition'
    ? String(raw.condition || '').trim().slice(0, 500) || null : null;
  // Het model bepaalt de temporele betekenis, maar rekent geen UTF-16-offsets.
  // Bij een conditionele start projecteert de host uitsluitend de werkelijk
  // aanwezige, ongequote startmarkers; zonder zo'n marker blijft dit fail-closed.
  const startSourceSpans = mode === 'condition'
    ? projectedConditionStartSourceSpans(text)
    : normalizeTemporalSourceSpans(raw.startSourceSpans, text);
  if (mode === 'later' && !notBefore) throw new TypeError('missing_temporal_start');
  if (mode === 'condition' && !condition) throw new TypeError('missing_temporal_condition');
  if (mode === 'later' && !hasLaterStartEvidence(text, startSourceSpans)) {
    throw new TypeError('missing_temporal_start_evidence');
  }
  if (mode === 'condition' && !hasConditionStartEvidence(text, startSourceSpans)) {
    throw new TypeError('missing_temporal_condition_evidence');
  }
  return { mode, notBefore, dueBy, condition, startSourceSpans,
    interpretedAt: now.toISOString() };
}
function normalizeDelivery(value) {
  const raw = plainObject(value) ? value : {};
  const mode = DELIVERY_MODES.has(raw.mode) ? raw.mode : 'reply';
  const target = DELIVERY_TARGETS.has(raw.target) ? raw.target : mode === 'none' ? 'none' : 'origin';
  if (mode === 'silent') return { mode, target: 'none' };
  return { mode, target };
}
function normalizeConditions(value, text, actions = []) {
  const actionById = new Map(actions.map((action, index) => (
    [action.requirementId, { action, index }]
  )));
  return (Array.isArray(value) ? value : []).slice(0, 12).map((item) => {
    if (!plainObject(item) || !limitedText(item.statement)) throw new TypeError('invalid_condition');
    const role = CONDITION_ROLES.has(item.role) ? item.role : 'guard';
    const ids = (field) => [...new Set((Array.isArray(item[field]) ? item[field] : [])
      .map((id) => String(id || '').trim()).filter((id) => actionById.has(id)))];
    const sourceSpans = normalizeSpans(item.sourceSpans, text);
    // Conditions beschrijven het doel; ze verlenen geen uitvoeringsbevoegdheid.
    // Hun links worden daarom uitsluitend uit de actuele, toegestane actions
    // afgeleid en ongeldige modelboekhouding wordt weggeprojecteerd.
    const requirementIds = role === 'action' ? actions.filter((action) => (
      action.polarity === 'request' && action.authorization === 'allowed'
      && action.sourceSpans.some((actionSpan) => (
        sourceSpans.some((conditionSpan) => overlaps(actionSpan, conditionSpan))
      ))
    )).map((action) => action.requirementId) : [];
    const firstActionIndex = Math.min(...requirementIds.map((id) => actionById.get(id).index));
    const afterRequirementIds = role === 'action' && Number.isFinite(firstActionIndex)
      ? ids('afterRequirementIds').filter((id) => (
        !requirementIds.includes(id) && actionById.get(id).index < firstActionIndex
      )) : [];
    return {
      statement: limitedText(item.statement, 500), role,
      state: CONDITION_STATES.has(item.state) ? item.state : 'unknown',
      sourceSpans,
      requirementIds, afterRequirementIds,
    };
  });
}
const conditionActive = (condition) => ['required', 'unmet'].includes(condition.state);
function guardOnlyRequirementIds(actions, conditions, monitoringRequested) {
  return new Set(actions.filter((action) => action.authorization === 'allowed'
    && action.polarity === 'request' && MUTATING_EFFECTS.has(action.effect)
    && !(monitoringRequested && action.capabilityId === 'work.episode.manage' && action.operationId === 'start')
    && conditions.some((condition) => conditionActive(condition) && condition.role === 'guard'
      && condition.sourceSpans.some((guardSpan) => action.sourceSpans.some((actionSpan) => overlaps(guardSpan, actionSpan))))
    && !conditions.some((condition) => conditionActive(condition) && condition.role === 'action'
      && condition.requirementIds.includes(action.requirementId)))
    .map((action) => action.requirementId));
}
function normalizeConfidence(value, fallback) {
  const number = value == null ? fallback : Number(value);
  if (!Number.isFinite(number) || number < 0 || number > 1) throw new TypeError('invalid_confidence');
  return Math.round(number * 10_000) / 10_000;
}
function normalizeProvenance(value, envelope, fallbackSpans) {
  const raw = plainObject(value) ? value : {};
  const context = semanticContext(envelope);
  const allowed = new Set(context.priorUnderstandings.map((item) => item.sha256));
  const hashes = [...new Set(Array.isArray(raw.contextHashes) ? raw.contextHashes : [])];
  if (hashes.some((hash) => !allowed.has(hash))) throw new TypeError('unknown_context_hash');
  const spans = normalizeSpans(raw.sourceSpans, envelope.message.text);
  return { currentTurnSpans: spans.length ? spans : fallbackSpans, contextHashes: hashes.sort() };
}
function normalizeProofNeeds(value, actions) {
  const result = [];
  for (const raw of Array.isArray(value) ? value.slice(0, 40) : []) {
    const item = typeof raw === 'string' ? { kind: raw } : raw;
    const kind = identifier(item?.kind, 'proof_kind');
    const actionIndex = item?.actionIndex == null ? null : Math.trunc(Number(item.actionIndex));
    if (actionIndex != null && (!Number.isInteger(actionIndex) || !actions[actionIndex])) throw new TypeError('invalid_proof_action_index');
    result.push({ kind, actionIndex, freshness: String(item?.freshness || 'unspecified').slice(0, 80) });
  }
  actions.forEach((action, actionIndex) => action.operation.proofNeeds
    .forEach((kind) => result.push({ kind, actionIndex, freshness: 'catalog_required' })));
  const unique = new Map(result.map((proof) => [`${proof.kind}\u0000${proof.actionIndex}\u0000${proof.freshness}`, proof]));
  return [...unique.values()].sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
}
function normalizeDecision(value, actions, {
  monitoringRequested = false, monitoringReadRequirementIds = new Set(),
  continuityRequested = false, continuityReadRequirementIds = new Set(),
  allowSelfAttentionProjection = false,
} = {}) {
  const proposed = plainObject(value) ? value : {};
  const replyDraft = proposed.replyDraft == null
    ? null : String(proposed.replyDraft).trim() || null;
  if (replyDraft && replyDraft.length > 4_000) {
    throw new TypeError('invalid_reply_draft');
  }
  const proposedKind = DECISIONS.has(proposed.kind) ? proposed.kind : 'unknown';
  const proposedRequirementIds = [...new Set((Array.isArray(proposed.requirementIds)
    ? proposed.requirementIds : []).map((item) => String(item || '').trim()).filter(Boolean))];
  const actionById = new Map(actions.map((action) => [action.requirementId, action]));
  const unknownRequirementIds = proposedRequirementIds.filter((id) => !actionById.has(id));
  const executableActions = actions.filter((action) => (
    action.authorization === 'allowed' && action.polarity === 'request'
  ));
  // De host voert uitsluitend deze canonieke allowed/request-actions uit. In
  // een pure inspectie kan een afgewezen actie in de modelboekhouding dus
  // nooit worden uitgevoerd. Laat zo'n gewone onderzoeksvraag met wél een
  // geldige leesstap niet volledig stranden op die administratieve vergissing;
  // houd de afgewezen actie zichtbaar in de projectie. Elke uitvoerende of
  // gemengde beurt blijft daarentegen gesloten.
  if (decisionSelectsDeniedAction(proposed, actions) && !allowSelfAttentionProjection
    && !(proposedKind === 'inspect' && executableActions.length
      && executableActions.every((action) => ['read', 'discovery'].includes(action.effect)))) {
    throw new TypeError('selected_denied_action');
  }
  const projectableIds = monitoringRequested || continuityRequested ? actions.filter((action) => (
    action.authorization === 'allowed' && action.polarity === 'request'
    && (((monitoringRequested ? monitoringReadRequirementIds : continuityReadRequirementIds)
      .has(action.requirementId))
      || (action.capabilityId === 'work.episode.manage' && action.operationId === 'start'))
  )).map((action) => action.requirementId) : [];
  // Alleen monitoring kent een expliciete huidige fase: bronread + episode-
  // start. In alle andere beurten is de volledige actuele allowed/request-set
  // canoniek; firstDecision is slechts modelboekhouding en kan niets toevoegen,
  // weglaten of autoriseren.
  const canonicalRequirementIds = (monitoringRequested || continuityRequested) && projectableIds.length
    ? projectableIds : executableActions.map((action) => action.requirementId);
  if (!canonicalRequirementIds.length && EXECUTING_DECISIONS.has(proposedKind)) {
    const rejected = proposedRequirementIds.map((id) => actionById.get(id)).filter(Boolean);
    if (rejected.length) throw new TypeError('selected_denied_action');
    if (unknownRequirementIds.length) throw new TypeError('unknown_decision_requirement');
    throw new TypeError('decision_requirement_mismatch');
  }
  if (!canonicalRequirementIds.length && proposedKind === 'unknown') {
    throw new TypeError('invalid_first_decision');
  }
  const canonicalActions = canonicalRequirementIds.map((id) => actionById.get(id));
  const kind = canonicalRequirementIds.length
    ? monitoringRequested || continuityRequested ? 'schedule'
      : canonicalActions.every((action) => ['read', 'discovery'].includes(action.effect))
        ? 'inspect'
        : canonicalActions.some((action) => MUTATING_EFFECTS.has(action.effect)
          && !action.operation.supportsDeferred) ? 'act' : 'schedule'
    : proposedKind;
  const acceptedRequirementIds = canonicalRequirementIds.filter((id) => (
    proposedRequirementIds.includes(id)
  ));
  return {
    decision: { kind, requirementIds: canonicalRequirementIds,
      replyDraft },
    projection: {
      proposedKind,
      proposedRequirementIds,
      acceptedRequirementIds,
      rejectedRequirementIds: proposedRequirementIds.filter((id) => (
        actionById.has(id) && !canonicalRequirementIds.includes(id)
      )),
      unknownRequirementIds,
      hostAddedRequirementIds: canonicalRequirementIds.filter((id) => (
        !acceptedRequirementIds.includes(id)
      )),
    },
  };
}
function positiveOperationSourceRequest(operation, value = '') {
  const configured = OPERATION_POSITIVE_SOURCE_HINTS.get(
    `${operation.capabilityId}\u0000${operation.operationId}`,
  );
  if (!configured || configured.effect !== operation.effect) return false;
  return configured.hints.some((hint) => hint.test(String(value || '')));
}
function trimAuthorityRange(text, range) {
  const value = text.slice(range.start, range.end);
  const leading = value.match(/^[\s\p{P}]*/u)?.[0].length || 0;
  const trailing = value.match(/[\s\p{P}]*$/u)?.[0].length || 0;
  return { start: range.start + leading, end: Math.max(range.start + leading, range.end - trailing) };
}
function operationAuthorityClauses(text, operation) {
  const clauses = clauseRanges(text);
  if (operation.capabilityId !== 'work.episode.manage' || operation.operationId !== 'afronden') {
    return clauses;
  }
  const result = [];
  const coordinator = /\b(?:en|and)\s+(?=(?:rond|sluit|afrond|voltooi|completeer|(?:kun|kan)\s+je)\b)/giu;
  for (const clause of clauses) {
    let start = clause.start;
    const scope = text.slice(clause.start, clause.end);
    for (const match of scope.matchAll(coordinator)) {
      const end = clause.start + Number(match.index);
      if (end > start) result.push({ start, end });
      start = end + match[0].length;
    }
    if (clause.end > start) result.push({ start, end: clause.end });
  }
  return result;
}
function spanCoversAuthorityClause(text, operation, span) {
  const normalized = trimAuthorityRange(text, span);
  if (normalized.end <= normalized.start) return false;
  const selected = operationAuthorityClauses(text, operation)
    .map((clause) => trimAuthorityRange(text, clause))
    .filter((clause) => overlaps(normalized, clause));
  return selected.length === 1 && selected[0].start === normalized.start
    && selected[0].end === normalized.end;
}
const EXPLICIT_HOME_HISTORY_REQUEST = /\b(?:gisteren|eergisteren|vannacht|vanochtend|vanmiddag|vanavond|eerder|geschiedenis|logboek|waarom|waardoor|oorzaak|hoe\s+kan\s+dat|wanneer|om\s+\d{1,2}[.:]\d{2})\b/iu;
function projectExplicitHomeHistory(
  capabilityId, operationId, suppliedInput, text, monitoringRequested = false,
) {
  if (capabilityId !== 'home.context.read' || operationId !== 'read') return suppliedInput;
  if (monitoringRequested) {
    const { history: _history, ...currentInput } = suppliedInput;
    return currentInput;
  }
  if (!EXPLICIT_HOME_HISTORY_REQUEST.test(text)) return suppliedInput;
  return {
    ...suppliedInput,
    query: String(suppliedInput.query || text).trim(),
    history: true,
  };
}

const FLOATING_MEMORY_SUBJECT = new RegExp([
  String.raw`\b(?:de|het|een|mijn|jouw|zijn|haar|hun)\s+(?:laatst(?:e)?(?:\s+relevante)?|relevante|meest\s+recente|bovenstaande|voorgaande)\s+(?:camera)?(?:foto|beeld|bericht|melding|persoon|gebeurtenis|situatie|ding)\b`,
  String.raw`\b(?:dit|dat|deze|die)\s+(?:camera)?(?:foto|beeld|bericht|melding|persoon|gebeurtenis|situatie|ding)\b`,
  String.raw`^(?:dit|dat|deze|die)\b`,
  String.raw`\b(?:dit|dat)\s+(?:is|was|betrof|gaat)\b`,
].join('|'), 'iu');
const CONCRETE_MEMORY_REFERENCE = new RegExp([
  String.raw`\b(?:telegram-bericht|bericht(?:nummer|-id)?|foto-ref|media-ref|event-id)\s*[:#]?\s*[a-z0-9][a-z0-9_.:-]{0,119}\b`,
  String.raw`\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b`,
  String.raw`\b\d{1,2}\s+(?:januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)\s+\d{4}\b`,
].join('|'), 'iu');

function unboundPersistentMemoryReference(capabilityId, operationId, polarity, semanticInput) {
  if (capabilityId !== 'memory.fact.write' || operationId !== 'remember'
    || polarity !== 'request') return false;
  const content = String(semanticInput?.inhoud ?? semanticInput?.content ?? '').trim();
  return Boolean(content) && FLOATING_MEMORY_SUBJECT.test(content)
    && !CONCRETE_MEMORY_REFERENCE.test(content);
}

function normalizeActions(rawActions, text, operations, denied, temporal, {
  inspectionOnly = false, inspectionReleaseAt = null, explicitDeniedEffects = new Set(),
  artifactReceipts = [], resourceReceipts = [],
  conversationWorkReceipts = [], monitoringRequested = false,
} = {}) {
  const actions = [];
  const clauses = clauseRanges(text);
  const boundaryCache = new Map();
  const classifyBoundary = (scope) => {
    if (!boundaryCache.has(scope)) boundaryCache.set(scope, effectBoundary(scope));
    return boundaryCache.get(scope);
  };
  const inspectionReleased = new WeakSet();
  const releasedFromInspection = (operation, sourceSpans, boundaryReasons) => inspectionOnly
    && Number.isInteger(inspectionReleaseAt) && sourceSpans.length > 0
    && PROJECTED_ACTIVE_WORK_OPERATIONS.has(operation)
    && !explicitDeniedEffects.has(operation.effect)
    && !boundaryReasons.some((reason) => (
      ['negated', 'hypothetical', 'deferred', 'cancelled', 'quoted'].includes(reason)
    ))
    && sourceSpans.every((span) => span.start >= inspectionReleaseAt
      && spanCoversAuthorityClause(text, operation, span)
      && positiveOperationSourceRequest(operation, text.slice(span.start, span.end)));
  for (const [index, raw] of (Array.isArray(rawActions) ? rawActions.slice(0, 12) : []).entries()) {
    if (!plainObject(raw)) throw new TypeError('invalid_action');
    const capabilityId = identifier(raw.capabilityId, 'capability_id');
    const operationId = identifier(raw.operationId, 'operation_id');
    const operation = operations.get(`${capabilityId}\u0000${operationId}`);
    if (!operation) throw new TypeError('unknown_capability_operation');
    const polarity = String(raw.polarity || '');
    if (!POLARITIES.has(polarity)) throw new TypeError('invalid_action_polarity');
    const sourceSpans = normalizeSpans(raw.sourceSpans, text);
    const boundaryReasons = actionBoundary(
      text, sourceSpans, temporal, operation, classifyBoundary,
    );
    const suppliedInput = projectExplicitHomeHistory(
      capabilityId, operationId,
      plainObject(raw.semanticInput) ? raw.semanticInput : {}, text, monitoringRequested,
    );
    const activeWorkId = PROJECTED_ACTIVE_WORK_OPERATIONS.get(operation);
    // Een deny/mention wordt nooit uitgevoerd: capability + operatie + bronspan
    // leggen de ontkenning al exact vast. Een model hoeft daarvoor dus geen
    // verplichte uitvoerargumenten te verzinnen. Bij een iterative request zijn
    // materializable velden juist uitvoeroutput; negeer die hier zodat alleen de
    // driver ze later uit bewijs kan maken en binden.
    const semanticInput = jsonValue(polarity === 'request'
      ? Object.fromEntries(Object.entries(activeWorkId == null ? suppliedInput
        : { ...suppliedInput, id: activeWorkId }).filter(([key]) => (
        !operation.execution.materializable.includes(key)
      )))
      : {});
    const inputViolation = polarity === 'request'
      ? jsonSchemaViolation(operation.input, semanticInput, `actions[${index}].semanticInput`)
      : null;
    if (inputViolation) throw new TypeError(inputViolation);
    if (unboundPersistentMemoryReference(
      capabilityId, operationId, polarity, semanticInput,
    )) throw new TypeError('unbound_persistent_memory_reference');
    const sourceBound = (field) => {
      const value = String(semanticInput[field] || '').trim();
      const signalBinding = PROJECTED_SIGNAL_OPERATIONS.get(operation);
      const priorValues = new Set([...resourceReceipts, ...conversationWorkReceipts]
        .filter((receipt) => receipt.capabilityId === capabilityId
        && receipt.operationId === operationId && receipt.field === field).map((receipt) => receipt.value));
      return Boolean(value) && ((signalBinding?.field === field
        && signalBinding.idsByValue.has(value))
        || (operation.supportsDeferred && operation.rebindableFields.includes(field)
        && hasUnresolvedReference(value)) || sourceSpans.some(({ start, end }) => text.slice(start, end)
        .toLowerCase().split(/[^a-z0-9_.]+/u).map((token) => token.replace(/\.+$/u, ''))
        .includes(value.toLowerCase()))
        || (priorValues.size === 1 && priorValues.has(value)));
    };
    if (polarity === 'request' && operation.sourceBoundFields.some((field) => !sourceBound(field))) throw new TypeError('source_bound_field_not_in_action_span');
    validateWorkspaceAction({ capabilityId, operationId, semanticInput, operation, polarity,
      artifactReceipts, priorActions: actions, operations });
    if (MUTATING_EFFECTS.has(operation.effect) && polarity !== 'request') boundaryReasons.push('semantic_denial');
    const releasedInspection = releasedFromInspection(operation, sourceSpans, boundaryReasons);
    if (inspectionOnly && MUTATING_EFFECTS.has(operation.effect) && !releasedInspection) {
      boundaryReasons.push('inspection_only');
    }
    const action = {
      requirementId: `r${index + 1}`, capabilityId, operationId,
      semanticInput, sourceSpans, polarity, effect: operation.effect,
      clauseIds: clauses.map((clause, clauseIndex) => ({ clause, clauseIndex }))
        .filter(({ clause }) => sourceSpans.some((span) => overlaps(span, clause)))
        .map(({ clauseIndex }) => clauseIndex),
      boundaryReasons: [...new Set(boundaryReasons)].sort(),
      authorization: 'allowed',
      operation,
    };
    actions.push(action);
    if (releasedInspection) inspectionReleased.add(action);
  }
  for (const action of actions) {
    const peers = actions.filter((item) => item.effect === action.effect);
    const positive = peers.filter((item) => item.polarity === 'request' && item.sourceSpans.length);
    const negative = peers.filter((item) => item.polarity === 'deny' && item.sourceSpans.length);
    const scopedConflict = positive.some((yes) => negative.some((no) =>
      yes.clauseIds.length && no.clauseIds.length
      && yes.clauseIds.every((clauseId) => !no.clauseIds.includes(clauseId))));
    // Een expliciet `deniedEffects`-label is globaal en absoluut. Alleen een
    // lokale deny-action zonder zo'n label kan naast een afzonderlijke
    // positieve operatie in hetzelfde effectdomein bestaan.
    const denialTargetsOtherOperations = negative.length > 0 && negative.every((no) => (
      no.capabilityId !== action.capabilityId || no.operationId !== action.operationId
    ));
    const explicitEffectConflict = explicitDeniedEffects.has(action.effect);
    const releasedInspectionEffect = inspectionReleased.has(action);
    if (explicitEffectConflict || (denied.has(action.effect) && !scopedConflict
      && !denialTargetsOtherOperations && !releasedInspectionEffect)) {
      action.boundaryReasons.push('effect_conflict');
    }
    action.boundaryReasons = [...new Set(action.boundaryReasons)].sort();
    const blocksRead = action.boundaryReasons.some((reason) => (
      ['missing_source_span', 'hypothetical', 'deferred', 'negated', 'cancelled'].includes(reason)
    ));
    action.authorization = action.boundaryReasons.length
      && (MUTATING_EFFECTS.has(action.effect) || blocksRead) ? 'denied' : 'allowed';
  }
  return actions;
}
function modelPayload(response) {
  const calls = Array.isArray(response?.toolCalls) ? response.toolCalls
    : Array.isArray(response?.output) ? response.output.filter((item) => item?.type === 'function_call') : [];
  if (calls.length === 1) {
    const name = calls[0].name ?? calls[0].function?.name;
    if (name !== 'commit_turn_understanding') throw new TypeError('unexpected_interpreter_call');
    const raw = calls[0].arguments ?? calls[0].function?.arguments;
    return typeof raw === 'string' ? JSON.parse(raw) : raw;
  }
  throw new TypeError('missing_structured_understanding');
}

function unscopedEffectConflicts(actions, text, requested, denied) {
  return [...new Set(actions.filter((action) => (
    action.polarity === 'request'
    && MUTATION_NEGATION.test(text.replace(NON_ACTION_NEGATION, ''))
    && MUTATING_EFFECTS.has(action.effect) && requested.has(action.effect)
    && denied.has(action.effect) && action.authorization === 'denied'
    && action.boundaryReasons.length === 1 && action.boundaryReasons[0] === 'effect_conflict'
    && !actions.some((peer) => peer.effect === action.effect
      && peer.polarity === 'deny' && peer.sourceSpans.length)
  )).map((action) => action.effect))].sort();
}
function rejectUnscopedEffectConflicts(actions, text, requested, denied) {
  const conflicts = unscopedEffectConflicts(actions, text, requested, denied);
  if (conflicts.length) throw new TypeError(`unscoped_effect_conflict:${conflicts.join(',')}`);
}

function issueMonitoring({
  requested, monitoring, chosen, readRequirementIds, delivery, temporal, envelope, now,
}) {
  if (!requested) return null;
  if (temporal.mode !== 'now' || temporal.notBefore !== null || temporal.dueBy !== null
    || temporal.condition !== null || temporal.startSourceSpans.length !== 0) {
    throw new TypeError('monitoring_temporal_not_current');
  }
  const monitorStarts = chosen.filter((action) => action.authorization === 'allowed'
    && action.capabilityId === 'work.episode.manage' && action.operationId === 'start');
  if (!monitorStarts.length) throw new TypeError('missing_monitoring_episode_action');
  const sourceReads = chosen.filter((action) => readRequirementIds.has(action.requirementId));
  const discoveryReads = sourceReads.filter((action) => (
    action.capabilityId === 'home.context.read' && action.operationId === 'read'
  ));
  const entityReads = sourceReads.filter((action) => (
    action.capabilityId === 'home.entity.read' && action.operationId === 'read'
  ));
  if (!entityReads.length) throw new TypeError('missing_monitoring_source_read');
  const minimumSourceReads = Math.max(1, Math.min(
    MONITORING_MAX_BINDINGS, Number(monitoring.minimumSourceReads) || 1,
  ));
  const distinctEntityReadReferences = new Set(entityReads.map((action) => (
    String(action.semanticInput?.entityId || '').trim().toLowerCase()
  )).filter(Boolean));
  if (entityReads.length < minimumSourceReads
    || distinctEntityReadReferences.size < minimumSourceReads) {
    throw new TypeError('missing_monitoring_source_coverage');
  }
  const monitorStart = monitorStarts[0];
  const exactShape = monitorStarts.length === 1 && discoveryReads.length <= 1
    && entityReads.length <= MONITORING_MAX_BINDINGS
    && chosen.length === discoveryReads.length + entityReads.length + 1
    && chosen.at(-1) === monitorStart
    && (!discoveryReads.length || chosen[0] === discoveryReads[0])
    && entityReads.every((action, index) => chosen[discoveryReads.length + index] === action)
    && (!entityReads.some((action) => hasUnresolvedReference(action.semanticInput?.entityId))
      || discoveryReads.length === 1);
  if (!exactShape) throw new TypeError('invalid_monitoring_source_shape');
  const sourceReadRequirementIds = sourceReads.map((action) => action.requirementId);
  const sourceEntityReadRequirementIds = entityReads.map((action) => action.requirementId);
  const requester = String(envelope.context.requester || '').trim().toLowerCase();
  const recipient = String(monitoring.recipient || '').trim().toLowerCase();
  const expectedTarget = !recipient || (requester && recipient === requester) ? 'requester' : 'family';
  const deliveryBound = monitoring.deliveryRequired
    ? delivery.mode === 'later' && delivery.target === expectedTarget
    : ['none', 'silent'].includes(delivery.mode) && delivery.target === 'none';
  const issued = {
    ...monitoring, sourceReadRequirementIds, sourceEntityReadRequirementIds,
    sourceDiscoveryRequirementId: discoveryReads[0]?.requirementId || null,
    sourceReadBound: true,
    deliveryMode: delivery.mode, deliveryTarget: delivery.target, deliveryBound,
    hostProjectsWake: true, hostProjectsSchedule: true,
    recipient: monitoring.deliveryRequired
      ? monitoring.recipient || envelope.context.requester || null : null,
  };
  if (!monitoringGuardValid({
    args: { ...monitorStart.semanticInput, bewerking: 'start' }, now: now.getTime(),
    monitoring: issued, allowStart: true,
  })) throw new TypeError('missing_durable_monitoring_schedule');
  return issued;
}

function issueContinuityWatch({
  requested, monitoring, actions, chosen, dialogueAct, durability, temporal, delivery, envelope,
}) {
  if (!requested) return null;
  const allowed = actions.filter((action) => action.authorization === 'allowed'
    && action.polarity === 'request');
  const readIndex = allowed.findIndex((action) => action.capabilityId === 'home.context.read'
    && action.operationId === 'read' && action.effect === 'read');
  const startIndex = allowed.findIndex((action) => action.capabilityId === 'work.episode.manage'
    && action.operationId === 'start' && action.effect === 'persistent');
  const start = allowed[startIndex], wake = Date.parse(start?.semanticInput?.hervatten_na || '');
  const selected = new Set(chosen.map((action) => action.requirementId));
  const valid = envelope.origin === 'user' && ['command', 'request'].includes(dialogueAct)
    && temporal.mode === 'now' && durability === 'episode' && allowed.length === 2
    && readIndex === 0 && startIndex === 1 && allowed.every((action) => selected.has(action.requirementId))
    && String(start?.semanticInput?.volgende_stap || '').trim()
    && String(start?.semanticInput?.definitie_klaar || '').trim()
    && Number.isFinite(wake) && wake >= monitoring.minWakeTs && wake <= monitoring.maxWakeTs
    && delivery.mode === 'later' && ['origin', 'requester'].includes(delivery.target);
  if (!valid) throw new TypeError('missing_bounded_continuity_schedule');
  return deepFreeze({ contract: 'ContinuityWatch/v1', requested: true, checks: 1,
    sourceReadRequirementId: allowed[0].requirementId,
    workRequirementId: start.requirementId,
    minWakeTs: monitoring.minWakeTs, maxWakeTs: monitoring.maxWakeTs });
}

export function projectContinuityWorkStart(args, contract, toolResults, person) {
  const accepted = contract?.contract === 'ContinuityWatch/v1' && toolResults.some((tool) => (
    verifiedCompleteReceipt(tool)
    && tool.semanticBinding?.understandingSha256 === contract.sourceUnderstandingSha256
    && tool.semanticBinding?.requirementId === contract.sourceReadRequirementId
    && tool.semanticBinding?.capabilityId === 'home.context.read'
    && tool.semanticBinding?.operationId === 'read'
  ));
  return { accepted, args: accepted ? {
    ...args,
    volgende_stap: 'Lees op het geplande tijdstip één keer opnieuw de actuele huiscontext en rond dit werk daarna af zonder huisbediening of verdere herplanning.',
    definitie_klaar: 'Eén nieuwe actuele huiscontextread is na het geplande tijdstip vastgelegd en dit werk is daarna gesloten.',
    fase: 'Eerste huiscontext gelezen; wacht op één begrensde hercontrole.',
    afleveren_aan: String(person || '').trim(),
  } : args };
}

function durableActionFailure(required, monitoringRequested, durableActions, chosen, now, aspirationStartAction) {
  if (!required) return '';
  if (!durableActions.length && !aspirationStartAction) return monitoringRequested
    ? 'missing_monitoring_episode_action' : 'missing_durable_action';
  const start = (!monitoringRequested && chosen.find((action) => (
    action.capabilityId === 'work.episode.manage' && action.operationId === 'start'
  )) ) || (!monitoringRequested && aspirationStartAction);
  if (start === aspirationStartAction) return '';
  if (!start) return '';
  const input = start.semanticInput || {}, wake = Date.parse(input.hervatten_na || '');
  return String(input.volgende_stap || '').trim() && String(input.definitie_klaar || '').trim()
    && Number.isFinite(wake) && wake > now.getTime() ? '' : 'missing_durable_schedule';
}

function freshAspirationExplorationAnchor({
  source, envelope, selfAttention, durability, temporal, delivery,
  actions, conditions, requestedEffects, deniedEffects, firstDecision,
  monitoringRequested, continuityRequested,
}) {
  const exactContext = [
    source === 'model', envelope.origin === 'self', selfAttention?.kind === 'aspiration',
    Number.isSafeInteger(selfAttention?.id), selfAttention?.id >= 1,
    durability === 'episode', temporal.mode === 'now', temporal.notBefore === null,
    temporal.dueBy === null, temporal.condition === null,
    temporal.startSourceSpans.length === 0,
    ['none', 'silent'].includes(delivery.mode), delivery.target === 'none',
    !monitoringRequested, !continuityRequested, conditions.length === 0,
    !envelope.context.activeGoal, !envelope.context.signalBatch?.signals?.length,
    actions.length === 2, EXECUTING_DECISIONS.has(firstDecision?.kind),
    canonicalJson(firstDecision?.requirementIds) === canonicalJson(['r1', 'r2']),
  ].every(Boolean);
  if (!exactContext) return null;
  const [start, next] = actions;
  const exactStartFields = [
    'aanleiding', 'aspiratie_id', 'hypothese', 'max_acties', 'onderwerp_ankers',
    'onderwerp_sleutel', 'stopregel', 'verwachte_waarneming', 'vraag',
  ];
  const exactStart = [
    start.requirementId === 'r1', start.capabilityId === 'exploration.manage',
    start.operationId === 'start', start.effect === 'persistent',
    start.polarity === 'request', start.authorization === 'allowed',
    start.boundaryReasons.length === 0, start.operation.supportsDeferred === false,
    start.operation.execution?.mode === 'direct',
    start.semanticInput?.aspiratie_id === selfAttention.id,
    canonicalJson(Object.keys(start.semanticInput || {}).sort())
      === canonicalJson(exactStartFields),
    !hasUnresolvedReference(start.semanticInput),
  ].every(Boolean);
  const exactNext = [
    next.requirementId === 'r2', SELF_ATTENTION_PROJECTABLE_EFFECTS.has(next.effect),
    next.polarity === 'request', next.authorization === 'allowed',
    next.boundaryReasons.length === 0, next.operation.supportsDeferred === false,
    !SELF_ATTENTION_ADMIN_CAPABILITIES.has(next.capabilityId),
    !hasUnresolvedReference(next.semanticInput),
  ].every(Boolean);
  const effects = [...new Set(['persistent', next.effect])].sort();
  if (![exactStart, exactNext,
    canonicalJson([...new Set(requestedEffects)].sort()) === canonicalJson(effects),
    !deniedEffects.some((effect) => effects.includes(effect)),
  ].every(Boolean)) return null;
  return Object.freeze({ aspirationId: selfAttention.id, startIndex: 0, nextIndex: 1 });
}

function exactOptionalTerminalFollowup(input) {
  const familyResultOptInPresent = Boolean(input
    && Object.hasOwn(input, 'gezinsresultaat'));
  const economicOptInPresent = Boolean(input
    && Object.hasOwn(input, 'economisch_vervolg'));
  const behaviorOptInPresent = Boolean(input
    && Object.hasOwn(input, 'gedragsproef'));
  if ([familyResultOptInPresent, economicOptInPresent, behaviorOptInPresent]
    .filter(Boolean).length > 1) {
    throw new TypeError('exploration_terminal_followups_are_exclusive');
  }
  if (familyResultOptInPresent) {
    const result = input.gezinsresultaat;
    const fields = ['gezinsnut', 'titel'];
    if (!(result && typeof result === 'object' && !Array.isArray(result)
      && canonicalJson(Object.keys(result).sort()) === canonicalJson(fields)
      && typeof result.titel === 'string' && result.titel.trim().length >= 4
      && typeof result.gezinsnut === 'string' && result.gezinsnut.trim().length >= 8)) {
      throw new TypeError('invalid_exploration_family_result');
    }
  }
  if (economicOptInPresent) {
    const commitment = input.economisch_vervolg;
    const fields = ['definitie_klaar', 'titel', 'uitkomst', 'volgende_stap'];
    if (!(commitment && typeof commitment === 'object' && !Array.isArray(commitment)
      && canonicalJson(Object.keys(commitment).sort()) === canonicalJson(fields)
      && fields.every((field) => typeof commitment[field] === 'string'
        && commitment[field].trim().length >= 4))) {
      throw new TypeError('invalid_exploration_economic_handoff');
    }
  }
  if (behaviorOptInPresent) {
    const proposal = input.gedragsproef;
    const fields = [
      'contextscope', 'dimensie', 'doelpersoon', 'hypothese', 'intensiteit',
      'motivatie', 'richting', 'waargenomen_kloof',
    ];
    const normalized = proposal && typeof proposal === 'object' && !Array.isArray(proposal)
      && canonicalJson(Object.keys(proposal).sort()) === canonicalJson(fields)
      ? normalizeBehaviorLensCandidate({
        person: proposal.doelpersoon, gapSummary: proposal.waargenomen_kloof,
        hypothesis: proposal.hypothese, motivation: proposal.motivatie,
        dimension: proposal.dimensie, direction: proposal.richting,
        intensity: proposal.intensiteit, contextScope: proposal.contextscope,
      }) : null;
    if (!behaviorLearningAvailable() || !normalized) {
      throw new TypeError('invalid_exploration_behavior_trial');
    }
  }
}

function explorationTerminalEvidenceReceiptSha256(context, evidenceSummary) {
  try {
    const row = /** @type {any} */ (db.prepare(`SELECT revision,evidence_json
      FROM eva_explorations WHERE id=?`).get(Number(context?.id)));
    if (!row || Number(row.revision) !== Number(context?.revision)) return null;
    const evidence = JSON.parse(String(row.evidence_json || '[]'));
    const latest = Array.isArray(evidence) ? evidence.at(-1) : null;
    const receipt = latest?.receipt;
    if (latest?.verified !== true
      || String(latest?.evidence || '').replace(/\s+/gu, ' ').trim() !== evidenceSummary
      || receipt?.contract !== 'ExplorationEvidenceReceipt/v1'
      || Number(receipt?.explorationId) !== Number(context.id)
      || !/^[a-f0-9]{64}$/u.test(String(receipt?.receiptSha256 || ''))
      || receipt.receiptId !== `exploration-evidence:${receipt.receiptSha256}`) return null;
    const { receiptId: _receiptId, receiptSha256, ...body } = receipt;
    return sha256(canonicalJson(body)) === receiptSha256 ? receiptSha256 : null;
  } catch { return null; }
}

function explorationTerminalContributionPrecommit({
  source, envelope, selfAttention, durability, temporal, delivery,
  actions, conditions, requestedEffects, firstDecision,
}) {
  const context = verifiedExplorationContinuation(selfAttention);
  const action = actions[0], input = action?.semanticInput;
  if (context && action?.capabilityId === 'exploration.manage'
    && action?.operationId === 'afronden') exactOptionalTerminalFollowup(input);
  const evidenceSummary = String(context?.lastEvidence?.summary || '').trim();
  const evidenceReceiptSha256 = explorationTerminalEvidenceReceiptSha256(
    context, evidenceSummary,
  );
  const inputFields = Object.keys(input || {}).sort();
  const allowedInputFields = new Set([
    'besluit', 'bewijs', 'economisch_vervolg', 'gedragsproef', 'gezinsresultaat',
    'id', 'resultaat', 'verwachte_revisie',
  ]);
  const exact = [
    source === 'model', envelope.origin === 'self', context,
    selfAttention?.kind === 'exploration', durability === 'turn',
    temporal.mode === 'now', temporal.notBefore === null, temporal.dueBy === null,
    temporal.condition === null, temporal.startSourceSpans.length === 0,
    ['none', 'silent'].includes(delivery.mode), delivery.target === 'none',
    conditions.length === 0, actions.length === 1,
    firstDecision?.kind === 'act',
    canonicalJson(firstDecision?.requirementIds) === canonicalJson(['r1']),
    canonicalJson(requestedEffects) === canonicalJson(['persistent']),
    action?.requirementId === 'r1', action?.capabilityId === 'exploration.manage',
    action?.operationId === 'afronden', action?.effect === 'persistent',
    action?.polarity === 'request', action?.authorization === 'allowed',
    action?.boundaryReasons?.length === 0,
    action?.operation?.supportsDeferred === false,
    action?.operation?.execution?.mode === 'direct',
    input?.id === context?.id, input?.verwachte_revisie === context?.revision,
    String(input?.resultaat || '').trim().length >= 8,
    inputFields.every((field) => allowedInputFields.has(field)),
    context?.lastEvidence?.verified === true, evidenceSummary.length >= 8,
  ].every(Boolean);
  return exact ? Object.freeze({
    actionIndex: 0, context, evidenceSummary, evidenceReceiptSha256,
  }) : null;
}

function allowedRequestActions(actions) {
  return actions.filter((action) => (
    action.authorization === 'allowed' && action.polarity === 'request'
  ));
}

function canonicalUnboundSelfCurrentTurnDurability({
  source, envelope, selfAttention, durability, temporal, delivery,
  actions, conditions,
  requestedEffects, conversationWorkReceipts, decision,
}) {
  const candidate = source === 'model' && envelope.origin === 'self'
    && !selfAttention && durability === 'episode';
  if (!candidate) return durability;
  // SignalBatch-timing wordt vóór deze evaluatie canoniek `turn`; observation
  // scope wordt voor self-origin vóór deze evaluatie altijd uitgezet. Geen van
  // beide kan dus een episode→turn-kandidaat bereiken.
  // ResourceReceipts verlenen uitsluitend typed sourceBound-authority tijdens
  // normalizeActions, vóór deze evaluatie. Deze projectie wijzigt daarna alleen
  // het durability-label en nooit action, argument, pad, default of selectie;
  // loutere receiptaanwezigheid is daarom geen tweede autorisatiepredicate.
  const actionEffects = [...new Set(actions.map((action) => action.effect))].sort();
  const proposed = plainObject(decision) ? decision : {};
  const requirementIds = Array.isArray(proposed.requirementIds)
    ? proposed.requirementIds.map((id) => String(id || '').trim()) : [];
  const allRequirementIds = actions.map((action) => action.requirementId);
  const expectedKind = actions.every((action) => ['read', 'discovery'].includes(action.effect))
    ? 'inspect' : 'act';
  const eligible = !envelope.context.activeGoal
    && envelope.context.artifactReceipts.length === 0
    && temporal.mode === 'now' && temporal.notBefore === null && temporal.dueBy === null
    && temporal.condition === null && temporal.startSourceSpans.length === 0
    && ['none', 'silent'].includes(delivery.mode) && delivery.target === 'none'
    && conditions.length === 0 && conversationWorkReceipts.length === 0
    && actions.length > 0
    && actions.every((action) => action.authorization === 'allowed'
      && action.polarity === 'request' && action.boundaryReasons.length === 0
      && !action.operation.supportsDeferred)
    && canonicalJson(actionEffects)
      === canonicalJson([...new Set(requestedEffects)].sort())
    && proposed.kind === expectedKind
    && canonicalJson(requirementIds) === canonicalJson(allRequirementIds)
    && Object.hasOwn(proposed, 'replyDraft') && proposed.replyDraft === null;
  return eligible ? 'turn' : durability;
}

function selfAttentionActionBindingMatched(selfAttention, action, durability) {
  if (!selfAttention) return false;
  // Een doelreview krijgt haar ene target al via de opaque receiptbinding.
  // De generieke portefeuille-read zou ruwe goal_keys opleveren die een
  // iteratieve vervolgactie opnieuw providerwaarts kan sturen.
  if (action.capabilityId === 'goal.portfolio.read') return false;
  if (SELF_ATTENTION_PROJECTABLE_EFFECTS.has(action.effect)) return true;
  if (action.effect !== 'persistent') return false;
  if (action.capabilityId === 'goal.portfolio.manage') {
    const binding = PROJECTED_SELF_ATTENTION_GOAL_OPERATIONS.get(action.operation);
    return selfAttention.kind === 'goal'
      && SELF_ATTENTION_GOAL_MANAGE_OPERATIONS.has(action.operationId)
      && binding?.selfAttention === selfAttention
      && binding.operationId === action.operationId
      && action.semanticInput?.doel_ref === selfAttention.goalRef
      && !Object.hasOwn(action.semanticInput || {}, 'doel_sleutel')
      && action.semanticInput?.verwachte_revisie === selfAttention.goalRevision;
  }
  if (action.capabilityId === 'exploration.manage') {
    if (action.operationId === 'start') {
      return (selfAttention.kind === 'goal'
          && action.semanticInput?.aspiratie_id == null)
        || (selfAttention.kind === 'aspiration'
          && action.semanticInput?.aspiratie_id === selfAttention.id);
    }
    return selfAttention.kind === 'exploration'
      && explorationContinuationOperationAllowed(selfAttention, action.operationId)
      && SELF_ATTENTION_EXPLORATION_CONTINUATIONS.has(action.operationId)
      && action.semanticInput?.id === selfAttention.id
      && action.semanticInput?.verwachte_revisie === selfAttention.explorationRevision;
  }
  // Een goal-attention mag alleen de hierboven per request exact vernauwde
  // goal-manage-operaties gebruiken; work-start blijft in deze fase dicht.
  // Een bestaande exploration-id is al exact door het private receipt gebonden.
  return action.capabilityId === 'work.episode.manage'
    && action.operationId === 'start'
    && durability === 'episode'
    && selfAttention.kind === 'aspiration';
}

function selfAttentionActionAudit(selfAttention, actions, durability, decision) {
  const selected = new Set((Array.isArray(decision?.requirementIds)
    ? decision.requirementIds : []).map(String));
  return deepFreeze(actions.slice(0, 12).map((action, index) => {
    const selectedAction = selected.has(action.requirementId);
    return {
      ordinal: index + 1,
      capabilityId: action.capabilityId,
      operationId: action.operationId,
      effect: action.effect,
      polarity: action.polarity,
      authorization: action.authorization,
      boundaryReasons: action.boundaryReasons.filter((reason) => (
        SELF_ATTENTION_AUDIT_BOUNDARY_REASONS.has(reason)
      )).slice(0, 11),
      normalizedSpanCount: Math.min(6, action.sourceSpans.length),
      bindingMatched: selfAttentionActionBindingMatched(
        selfAttention, action, durability,
      ),
      executingDecision: EXECUTING_DECISIONS.has(decision?.kind),
      selected: selectedAction,
      selectedExecutable: selectedAction && action.authorization === 'allowed'
        && action.polarity === 'request',
    };
  }));
}

function selfAttentionValidationError(code, selfAttention, actions, durability, decision) {
  const error = new TypeError(code);
  SELF_ATTENTION_VALIDATION_AUDITS.set(error, selfAttentionActionAudit(
    selfAttention, actions, durability, decision,
  ));
  return error;
}

function turnDecisionRetryGuide(actions) {
  return deepFreeze({
    contract: 'TurnDecisionRetryGuide/v1',
    requirements: actions.slice(0, 12).map((action, index) => ({
      ordinal: index + 1,
      requirementId: action.requirementId,
      capabilityId: action.capabilityId,
      operationId: action.operationId,
      polarity: action.polarity,
      authorization: action.authorization,
      selection: action.authorization === 'allowed' && action.polarity === 'request'
        ? 'select' : 'exclude',
    })),
  });
}

function turnDecisionValidationError(code, actions) {
  const error = new TypeError(code);
  if (code === 'selected_denied_action') {
    TURN_DECISION_RETRY_GUIDES.set(error, turnDecisionRetryGuide(actions));
  }
  return error;
}

function turnDecisionRetrySummary(guide) {
  if (guide?.contract !== 'TurnDecisionRetryGuide/v1'
    || !Array.isArray(guide.requirements)) return null;
  const selectCount = guide.requirements.filter((item) => item.selection === 'select').length;
  const excludeCount = guide.requirements.filter((item) => item.selection === 'exclude').length;
  return deepFreeze({
    contract: 'TurnDecisionRetrySummary/v1', selectCount, excludeCount,
  });
}

function durableActionError(code, selfAttention, actions, durability, decision) {
  return selfAttention
    ? selfAttentionValidationError(code, selfAttention, actions, durability, decision)
    : new TypeError(code);
}

function assertSelfAttentionActionScope(selfAttention, actions, durability, decision, now) {
  if (!selfAttention) return;
  const allowed = allowedRequestActions(actions);
  const boundWorkStarts = allowed.filter((action) => (
    action.capabilityId === 'work.episode.manage'
    && action.operationId === 'start'
    && selfAttentionActionBindingMatched(selfAttention, action, durability)
  ));
  if (boundWorkStarts.length > 1) throw selfAttentionValidationError(
    'multiple_self_attention_work_starts', selfAttention, actions, durability, decision,
  );
  const boundGoalManage = allowed.filter((action) => (
    action.capabilityId === 'goal.portfolio.manage'
    && selfAttentionActionBindingMatched(selfAttention, action, durability)
  ));
  if (boundGoalManage.length > 1) throw selfAttentionValidationError(
    'multiple_self_attention_goal_manage', selfAttention, actions, durability, decision,
  );
  const boundExplorationManage = allowed.filter((action) => (
    action.capabilityId === 'exploration.manage'
    && SELF_ATTENTION_EXPLORATION_CONTINUATIONS.has(action.operationId)
    && selfAttentionActionBindingMatched(selfAttention, action, durability)
  ));
  if (boundExplorationManage.length > 1) throw selfAttentionValidationError(
    'multiple_self_attention_exploration_manage', selfAttention, actions, durability, decision,
  );
  if (boundExplorationManage.length === 1 && allowed.length !== 1) {
    throw selfAttentionValidationError(
      'self_attention_exploration_manage_not_exclusive',
      selfAttention, actions, durability, decision,
    );
  }
  const disallowed = allowed.filter((action) => (
    !selfAttentionActionBindingMatched(selfAttention, action, durability)
  ));
  if (disallowed.length) throw selfAttentionValidationError(
    'self_attention_effect_not_allowed', selfAttention, actions, durability, decision,
  );
  const update = boundGoalManage.find((action) => action.operationId === 'update');
  if (update) {
    const review = update.semanticInput?.volgende_review;
    const reviewTs = typeof review === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.test(review)
      ? Date.parse(review) : Number.NaN;
    if (!Number.isFinite(reviewTs) || reviewTs <= now.getTime()
      || reviewTs < Date.parse(selfAttention.claimedUntil)) {
      throw selfAttentionValidationError(
        'self_attention_goal_review_not_future', selfAttention, actions, durability, decision,
      );
    }
  }
  const explorationWait = boundExplorationManage.find((action) => (
    action.operationId === 'wachten'
  ));
  if (explorationWait) {
    const wake = explorationWait.semanticInput?.hervatten_na;
    const wakeTs = typeof wake === 'string'
      && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.test(wake)
      ? Date.parse(wake) : Number.NaN;
    if (!Number.isFinite(wakeTs) || wakeTs <= now.getTime()) {
      throw selfAttentionValidationError(
        'self_attention_exploration_wake_not_future',
        selfAttention, actions, durability, decision,
      );
    }
  }
}

function selfAttentionProjectionAllowed(selfAttention, allowedRequests) {
  return Boolean(selfAttention) && allowedRequests.length > 0
    && allowedRequests.every((action) => SELF_ATTENTION_PROJECTABLE_EFFECTS.has(action.effect));
}

function selfAttentionExplorationFailure(selfAttention, actions, durability) {
  if (!selfAttention) return '';
  const allowed = allowedRequestActions(actions);
  const starts = allowed.map((action, index) => ({ action, index })).filter(({ action }) => (
    action.capabilityId === 'exploration.manage' && action.operationId === 'start'
  ));
  // Een exploration-attention is door dezelfde private scheduler al aan haar
  // bestaande exploration-id gebonden; een start zou die binding vervangen.
  if (selfAttention.kind === 'exploration') return starts.length
    ? 'bound_self_attention_duplicate_exploration_start' : '';
  const substantive = allowed.map((action, index) => ({ action, index }))
    .filter(({ action }) => !SELF_ATTENTION_ADMIN_CAPABILITIES.has(action.capabilityId));
  const workStarts = substantive.filter(({ action }) => (
    action.capabilityId === 'work.episode.manage' && action.operationId === 'start'
  ));
  const episodeActions = durability === 'episode' ? substantive.filter(({ action }) => (
    action.effect === 'persistent' && action.operation.supportsDeferred
  )) : [];
  const boundaries = [
    ...(substantive.length > 1 ? [substantive[1].index] : []),
    ...workStarts.map(({ index }) => index),
    ...episodeActions.map(({ index }) => index),
  ];
  if (!boundaries.length) return starts.length > 1
    ? 'multiple_self_attention_exploration_starts' : '';
  if (!starts.length) return 'missing_self_attention_exploration_start';
  if (starts.length > 1) return 'multiple_self_attention_exploration_starts';
  return starts[0].index < Math.min(...boundaries)
    ? '' : 'late_self_attention_exploration_start';
}

function assertSelfAttentionExploration(selfAttention, actions, durability, decision) {
  const failure = selfAttentionExplorationFailure(selfAttention, actions, durability);
  if (failure) throw selfAttentionValidationError(
    failure, selfAttention, actions, durability, decision,
  );
}

function decisionSelectsDeniedAction(value, actions) {
  const proposed = plainObject(value) ? value : {};
  if (!EXECUTING_DECISIONS.has(proposed.kind)) return false;
  const actionById = new Map(actions.map((action) => [action.requirementId, action]));
  return (Array.isArray(proposed.requirementIds) ? proposed.requirementIds : []).some((id) => {
    const action = actionById.get(String(id || '').trim());
    return action && (action.authorization !== 'allowed' || action.polarity !== 'request');
  });
}

function hasRedundantWorkStart(actions, durability) {
  if (durability === 'episode') return false;
  const allowed = allowedRequestActions(actions);
  const starts = allowed.filter((action) => (
    action.capabilityId === 'work.episode.manage' && action.operationId === 'start'
  ));
  return starts.length > 0 && allowed.some((action) => !starts.includes(action));
}

function turnActionInvariantFailure(actions, durability, decision, {
  allowSelfAttentionProjection = false, trustedSelfAttention = false,
} = {}) {
  const allowed = allowedRequestActions(actions);
  if (trustedSelfAttention && EXECUTING_DECISIONS.has(decision?.kind) && !allowed.length) {
    return 'self_attention_no_allowed_action';
  }
  const selectedDenied = !allowSelfAttentionProjection && decisionSelectsDeniedAction(decision, actions)
    && !(decision?.kind === 'inspect' && allowed.length
      && allowed.every((action) => ['read', 'discovery'].includes(action.effect)));
  const redundantStart = hasRedundantWorkStart(actions, durability);
  if (selectedDenied && redundantStart) {
    return 'selected_denied_action_and_redundant_work_start';
  }
  if (selectedDenied) return 'selected_denied_action';
  if (redundantStart) return 'redundant_work_start';
  return '';
}

function assertTurnActionInvariant(actions, durability, decision, options) {
  const failure = turnActionInvariantFailure(actions, durability, decision, options);
  if (!failure) return;
  if (options?.selfAttention) throw selfAttentionValidationError(
    failure, options.selfAttention, actions, durability, decision,
  );
  throw failure === 'selected_denied_action'
    || failure === 'selected_denied_action_and_redundant_work_start'
    ? turnDecisionValidationError(failure, actions)
    : new TypeError(failure);
}

function signalAssessmentRouteFailure(actions, assessment, firstDecision) {
  const disposition = assessment.semanticInput?.disposition;
  if (!['acted', 'observed'].includes(disposition)) return '';
  const selected = new Set(firstDecision?.requirementIds || []);
  const terminalIndex = actions.indexOf(assessment);
  const predecessors = actions.slice(0, terminalIndex).filter((action) => (
    selected.has(action.requirementId) && action.authorization === 'allowed'
    && action.polarity === 'request' && PROJECTED_SIGNAL_OPERATIONS.has(action.operation)
  ));
  if (disposition === 'acted' && !predecessors.some((action) => MUTATING_EFFECTS.has(action.effect))) {
    return 'signal_acted_without_effectful_predecessor';
  }
  if (disposition === 'observed' && !predecessors.some((action) => action.effect === 'read')) {
    return 'signal_observed_without_read_predecessor';
  }
  return '';
}

function assertSignalAssessment(actions, envelope, source, firstDecision) {
  if (source !== 'model') return;
  const issued = envelope.context.signalBatch;
  const signalActions = actions.filter((action) => (
    action.capabilityId === 'signal.assess' && action.operationId === 'assess'
  ));
  if (!issued && signalActions.length) throw new TypeError('orphan_signal_assessment');
  if (!issued) return;
  const allowed = signalActions.filter((action) => (
    action.authorization === 'allowed' && action.polarity === 'request'
  ));
  if (!allowed.length) throw new TypeError('missing_signal_assessment');
  if (signalActions.length !== 1) throw new TypeError('multiple_signal_assessments');
  if (actions.at(-1) !== allowed[0]) throw new TypeError('nonterminal_signal_assessment');
  const covered = new Set();
  for (const action of actions) {
    const binding = PROJECTED_SIGNAL_OPERATIONS.get(action.operation);
    if (action.authorization !== 'allowed' || action.polarity !== 'request' || !binding) continue;
    const value = String(action.semanticInput?.[binding.field] || '');
    for (const id of binding.idsByValue.get(value) || []) covered.add(id);
  }
  if (issued.signals.some((signal) => !covered.has(signal.id))) {
    throw new TypeError('incomplete_signal_observation');
  }
  const routeFailure = signalAssessmentRouteFailure(actions, allowed[0], firstDecision);
  if (routeFailure) throw new TypeError(routeFailure);
}

function canonicalSignalAssessment(raw, envelope, operations, source) {
  if (source !== 'model' || !envelope.context.signalBatch) {
    return { raw, action: null, requestedEffects: normalizeEffects(raw.requestedEffects) };
  }
  const operation = operations.get('signal.assess\u0000assess');
  const draft = raw.signalAssessment;
  if (!operation || !plainObject(draft)) throw new TypeError('missing_signal_assessment');
  const readOnlyActed = draft.disposition === 'acted'
    && !projectedSignalMutationAvailable(operations);
  const observedFallback = projectedSignalReadAvailable(operations);
  const canonicalDraft = readOnlyActed ? {
    ...draft, disposition: observedFallback ? 'observed' : 'no_action',
    assessment: observedFallback ? READ_ONLY_SIGNAL_ASSESSMENT : NONACTING_SIGNAL_ASSESSMENT,
  } : draft;
  const violation = jsonSchemaViolation(
    projectedSignalAssessmentInput(operations), canonicalDraft, 'signalAssessment',
  );
  if (violation) throw new TypeError(violation);
  const { signalAssessment: _draft, ...body } = raw;
  const actions = Array.isArray(body.actions) ? body.actions : [];
  if (actions.some((action) => action?.capabilityId === 'signal.assess'
    && action?.operationId === 'assess')) throw new TypeError('model_signal_assessment_action');
  if (actions.length > 11) throw new TypeError('too_many_signal_predecessors');
  const action = {
    capabilityId: 'signal.assess', operationId: 'assess', semanticInput: jsonValue(canonicalDraft),
    sourceSpans: [], polarity: 'request', effect: operation.effect, clauseIds: [],
    boundaryReasons: [], authorization: 'allowed', operation,
  };
  return { raw: { ...body, actions }, action,
    requestedEffects: normalizeEffects(body.requestedEffects) };
}
function appendSignalAssessment(actions, action, requested, denied) {
  if (!action) return;
  if (!requested.has(action.effect) || denied.has(action.effect)) {
    throw new TypeError('signal_assessment_policy_denied');
  }
  actions.push({ ...action, requirementId: `r${actions.length + 1}` });
}

function observationRequestScope(source, envelope, monitoring) {
  return {
    monitoringRequested: source === 'model' && envelope.origin === 'user'
      && monitoring.requested,
    continuityRequested: source === 'model' && envelope.origin === 'user'
      && monitoring.continuityRequested === true,
  };
}

function requesterObservationSpec(envelope, analysis) {
  if (envelope.origin === 'user') return analysis;
  // Self- en resume-tekst is hostcontext, geen bewonersopdracht. Zij mag dus
  // wel onderwerp/timing helpen analyseren, maar nooit door losse woorden een
  // monitoring- of continuiteitsbelofte autoriseren. Expliciet gekozen
  // duurzaam self-work blijft via de gewone episodegrens geldig.
  return {
    ...analysis,
    requested: false,
    continuityRequested: false,
  };
}

function observationReadRequirementIds(actions) {
  const allowedRead = (action) => action.authorization === 'allowed'
    && action.polarity === 'request' && action.effect === 'read';
  return {
    monitoringReadRequirementIds: new Set(actions.filter((action) => allowedRead(action)
      && ((action.capabilityId === 'home.context.read' && action.operationId === 'read')
        || (action.capabilityId === 'home.entity.read' && action.operationId === 'read'
          && (MONITORING_ENTITY.test(String(action.semanticInput?.entityId || '').trim())
            || hasUnresolvedReference(action.semanticInput?.entityId)))))
      .map((action) => action.requirementId)),
    continuityReadRequirementIds: new Set(actions.filter((action) => allowedRead(action)
      && action.capabilityId === 'home.context.read' && action.operationId === 'read')
      .map((action) => action.requirementId)),
  };
}

function assertSemanticReplyDraft(firstDecision) {
  if (!['reply', 'clarify'].includes(firstDecision.kind)) return;
  if (!firstDecision.replyDraft) throw new TypeError('missing_reply_draft');
}

function assertHistoricalCurrentInspection(envelope, actions, firstDecision) {
  const historical = envelope.context.dialogueExcerpts.some((row) => (
    row.evidenceClass === 'historical_unconfirmed'
  ));
  if (!historical || !presentStateQuestion(envelope.message.text)) return;
  const selected = actions.filter((action) => (
    firstDecision.requirementIds.includes(action.requirementId)
  ));
  const liveObservation = selected.some((action) => (
    ['read', 'discovery'].includes(action.effect)
    && action.operation.freshnessClass === 'live'
  ));
  if (firstDecision.kind !== 'inspect' || !selected.length
    || selected.some((action) => !['read', 'discovery'].includes(action.effect))
    || !liveObservation) {
    throw new TypeError('historical_current_requires_live_inspection');
  }
}

function assertPriorHomeResultInspection({
  envelope, dialogueAct, actions, firstDecision, resourceReceipts,
}) {
  if (!priorActionResultFollowup(envelope.message.text)) return;
  const receipts = resourceReceipts.filter((receipt) => (
    receipt.capabilityId === 'home.entity.read' && receipt.operationId === 'read'
      && receipt.field === 'entityId'
  ));
  if (!receipts.length) return;
  if (!CONTINUATION_ACTS.has(dialogueAct)) {
    throw new TypeError('prior_home_result_requires_continuation');
  }
  const values = new Set(receipts.map((receipt) => receipt.value));
  const selected = actions.filter((action) => (
    firstDecision.requirementIds.includes(action.requirementId)
  ));
  const exactReads = selected.filter((action) => (
    action.authorization === 'allowed' && action.polarity === 'request'
      && action.capabilityId === 'home.entity.read' && action.operationId === 'read'
      && values.has(String(action.semanticInput?.entityId || ''))
  ));
  if (firstDecision.kind !== 'inspect' || selected.length !== 1 || exactReads.length !== 1) {
    throw new TypeError('prior_home_result_requires_live_read');
  }
}

function orphanEffectfulContinuation({
  source, dialogueAct, envelope, artifactReceipts, conversationWorkReceipts,
  selfAttention, allowedRequests,
}) {
  return source === 'model' && CONTINUATION_ACTS.has(dialogueAct)
    && !envelope.context.activeGoal && !artifactReceipts.length
    && !conversationWorkReceipts.length
    && verifiedExplorationContinuation(selfAttention) === null
    && allowedRequests.some((action) => MUTATING_EFFECTS.has(action.effect));
}

function finalizedUnderstanding(raw, envelope, operations, now, source = 'model', {
  monitoring = /** @type {ReturnType<typeof monitoringRequestSpec>} */ ({ requested: false }), boundary = null,
  resourceReceipts = [], conversationWorkReceipts = [], selfAttention = null,
} = {}) {
  if (!plainObject(raw)) throw new TypeError('understanding_not_object');
  assertExplorationTurnFields(raw, selfAttention, source);
  const signal = canonicalSignalAssessment(raw, envelope, operations, source);
  raw = canonicalSignalBatchTiming(signal.raw, envelope, source);
  raw = canonicalSelfAttentionCurrentSpans(raw, selfAttention, source);
  if (source === 'model' && MODEL_FIELDS.some((key) => !Object.hasOwn(raw, key))) {
    throw new TypeError('understanding_missing_field');
  }
  if (source === 'model' && Object.keys(raw).some((key) => !MODEL_FIELDS.includes(key))) {
    throw new TypeError('understanding_unknown_field');
  }
  jsonValue(raw);
  const requestedEffects = signal.requestedEffects;
  const turnBoundary = boundary || effectBoundary(envelope.message.text);
  const explicitDeniedEffects = new Set(normalizeEffects(raw.deniedEffects));
  const deniedEffects = normalizeEffects([
    ...explicitDeniedEffects,
    ...(turnBoundary.inspectionOnly ? MUTATING_EFFECTS : []),
  ]);
  const requested = new Set(requestedEffects);
  const denied = new Set(deniedEffects);
  const temporal = normalizeTemporal(raw.temporal, envelope.message.text, now);
  const requestedDialogueAct = String(raw.dialogueAct || (source === 'voice_retry' ? 'continue'
    : source.startsWith('voice_') ? 'command' : 'unknown')).trim().toLowerCase();
  if (!DIALOGUE_ACTS.has(requestedDialogueAct)) throw new TypeError('invalid_dialogue_act');
  const { monitoringRequested, continuityRequested } = observationRequestScope(
    source, envelope, monitoring,
  );
  const artifactReceipts = CONTINUATION_ACTS.has(requestedDialogueAct)
    ? envelope.context.artifactReceipts : [];
  const actions = normalizeActions(raw.actions, envelope.message.text, operations, denied, temporal, {
    inspectionOnly: turnBoundary.inspectionOnly,
    inspectionReleaseAt: turnBoundary.inspectionReleaseAt,
    explicitDeniedEffects,
    artifactReceipts, monitoringRequested,
    resourceReceipts, conversationWorkReceipts,
  });
  const delivery = normalizeDelivery(raw.delivery);
  const conditions = normalizeConditions(raw.conditions, envelope.message.text, actions);
  const guardOnly = guardOnlyRequirementIds(actions, conditions,
    monitoringRequested || continuityRequested);
  for (const action of actions) {
    if (guardOnly.has(action.requirementId)) {
      action.boundaryReasons = [...new Set([...action.boundaryReasons, 'guard_only'])].sort();
      action.authorization = 'denied';
    }
  }
  rejectUnscopedEffectConflicts(actions, envelope.message.text, requested, denied);
  const locallyDenied = new Set(actions.filter((action) => action.authorization === 'denied'
    && MUTATING_EFFECTS.has(action.effect)).map((action) => action.effect));
  actions.filter((action) => action.authorization === 'allowed')
    .forEach((action) => denied.delete(action.effect));
  locallyDenied.forEach((effect) => {
    if (!actions.some((action) => action.effect === effect && action.authorization === 'allowed')) denied.add(effect);
  });
  appendSignalAssessment(actions, signal.action, requested, denied);
  const durability = DURABILITY.has(raw.durability) ? raw.durability : 'turn';
  const temporalDurability = temporal.mode === 'later' || temporal.mode === 'condition'
    ? 'episode' : durability;
  const clampedDurability = canonicalUnboundSelfCurrentTurnDurability({
    source, envelope, selfAttention, durability: temporalDurability, temporal, delivery,
    actions, conditions, requestedEffects,
    conversationWorkReceipts, decision: raw.firstDecision,
  });
  const allowedRequests = allowedRequestActions(actions);
  assertSelfAttentionActionScope(
    selfAttention, actions, clampedDurability, raw.firstDecision, now,
  );
  const allowSelfAttentionProjection = selfAttentionProjectionAllowed(
    selfAttention, allowedRequests,
  );
  assertTurnActionInvariant(
    actions, clampedDurability, raw.firstDecision,
    { allowSelfAttentionProjection, trustedSelfAttention: Boolean(selfAttention), selfAttention },
  );
  assertSelfAttentionExploration(
    selfAttention, actions, clampedDurability, raw.firstDecision,
  );
  const { monitoringReadRequirementIds, continuityReadRequirementIds } = observationReadRequirementIds(actions);
  const normalizedDecision = normalizeDecision(raw.firstDecision, actions, {
    monitoringRequested, monitoringReadRequirementIds, continuityRequested,
    continuityReadRequirementIds, allowSelfAttentionProjection,
  });
  const firstDecision = normalizedDecision.decision;
  const decisionProjection = normalizedDecision.projection;
  const aspirationExplorationAnchor = freshAspirationExplorationAnchor({
    source, envelope, selfAttention, durability: clampedDurability, temporal, delivery,
    actions, conditions, requestedEffects, deniedEffects: [...denied], firstDecision,
    monitoringRequested, continuityRequested,
  });
  const explorationTerminalContribution = explorationTerminalContributionPrecommit({
    source, envelope, selfAttention, durability: clampedDurability, temporal, delivery,
    actions, conditions, requestedEffects, firstDecision,
  });
  // Een reply/clarify-besluit is alleen een bruikbaar semantisch resultaat
  // wanneer het model ook zelf de tekst leverde. De uitvoerder mag een lege
  // draft niet later met hosttekst invullen: dan zou die hosttekst ten onrechte
  // als modelbegrepen antwoord en duurzaam resultaat kunnen worden bezorgd.
  assertSemanticReplyDraft(firstDecision);
  assertHistoricalCurrentInspection(envelope, actions, firstDecision);
  assertPriorHomeResultInspection({
    envelope, dialogueAct: requestedDialogueAct, actions, firstDecision, resourceReceipts,
  });
  assertSignalAssessment(actions, envelope, source, firstDecision);
  const terminalRequirementId = allowedRequests.at(-1)?.requirementId;
  const unfinishedTerminal = conditions.some((condition) => conditionActive(condition)
    && (condition.role === 'completion' || (condition.role === 'action'
      && condition.requirementIds.includes(terminalRequirementId)
      && condition.afterRequirementIds.length === 0)));
  if (!monitoringRequested && requestedDialogueAct === 'command'
    && unfinishedTerminal && requestedEffects.some((effect) => MUTATING_EFFECTS.has(effect)
      && !denied.has(effect) && !allowedRequests.some((action) => action.effect === effect))) {
    throw new TypeError('missing_concrete_completion_action');
  }
  if (orphanEffectfulContinuation({
    source, dialogueAct: requestedDialogueAct, envelope, artifactReceipts,
    conversationWorkReceipts, selfAttention, allowedRequests,
  })) {
    throw new TypeError('orphan_effectful_continuation');
  }
  if (envelope.origin === 'self' && firstDecision.kind === 'reply') {
    throw new TypeError('self_origin_reply_without_recipient');
  }
  const chosen = actions.filter((action) => firstDecision.requirementIds.includes(action.requirementId));
  if (chosen.some((action) => action.authorization !== 'allowed' || action.polarity !== 'request')) {
    throw new TypeError('selected_denied_action');
  }
  const needsDurableAction = clampedDurability === 'episode' && envelope.origin !== 'resume';
  const durableActions = actions.filter((action) => action.authorization === 'allowed'
    && action.effect === 'persistent' && action.operation.supportsDeferred);
  const monitoringStarts = durableActions.filter((action) => (
    action.capabilityId === 'work.episode.manage' && action.operationId === 'start'
  ));
  const durableActionsToBind = monitoringRequested ? monitoringStarts
    : aspirationExplorationAnchor
      ? [...durableActions, actions[aspirationExplorationAnchor.startIndex]]
      : durableActions;
  const durableFailure = durableActionFailure(
    needsDurableAction, monitoringRequested, durableActionsToBind, chosen, now,
    aspirationExplorationAnchor && chosen.includes(actions[aspirationExplorationAnchor.startIndex])
      ? actions[aspirationExplorationAnchor.startIndex] : null,
  );
  if (durableFailure) throw durableActionError(
    durableFailure, selfAttention, actions, clampedDurability, raw.firstDecision,
  );
  const issuedMonitoring = issueMonitoring({
    requested: monitoringRequested, monitoring, chosen,
    readRequirementIds: monitoringReadRequirementIds, delivery, temporal, envelope, now,
  });
  const issuedContinuity = issueContinuityWatch({
    requested: continuityRequested, monitoring, actions, chosen, dialogueAct: requestedDialogueAct,
    durability: clampedDurability, temporal, delivery, envelope,
  });
  const publicActions = actions.map(({ operation: _operation, clauseIds: _clauseIds, ...action }) => action);
  const dialogueAct = requestedDialogueAct;
  const defaultSpans = normalizeSpans(actions.flatMap((action) => action.sourceSpans), envelope.message.text);
  const body = {
    contract: CONTRACT,
    source: { kind: source, turnId: envelope.turnId, messageId: envelope.message.id,
      origin: envelope.origin, textSha256: sha256(envelope.message.text) },
    dialogueAct, goal: normalizeGoal(raw.goal), conditions,
    provenance: normalizeProvenance(raw.provenance, envelope, defaultSpans),
    confidence: normalizeConfidence(raw.confidence, source === 'fail_closed' ? 0 : 1),
    actions: publicActions, requestedEffects,
    deniedEffects: [...denied].sort(),
    temporal, durability: clampedDurability, delivery, monitoring: issuedMonitoring,
    proofNeeds: normalizeProofNeeds(raw.proofNeeds, actions),
    ambiguities: [...new Set((Array.isArray(raw.ambiguities) ? raw.ambiguities : [])
      .map((item) => String(item || '').trim().slice(0, 300)).filter(Boolean))].sort(),
    firstDecision, decisionProjection,
  };
  const understanding = deepFreeze({ ...body, sha256: sha256(canonicalJson(body)) });
  ISSUED_UNDERSTANDINGS.add(understanding);
  ISSUED_SIGNAL_BATCHES.set(understanding, envelope.context.signalBatch);
  ISSUED_CONTINUITY_WATCHES.set(understanding, issuedContinuity);
  if (aspirationExplorationAnchor) {
    const token = Object.freeze({});
    ISSUED_ASPIRATION_EXPLORATION_ANCHORS.set(understanding, Object.freeze({
      token, understanding,
      aspirationId: aspirationExplorationAnchor.aspirationId,
      startAction: understanding.actions[aspirationExplorationAnchor.startIndex],
      nextAction: understanding.actions[aspirationExplorationAnchor.nextIndex],
      startIndex: aspirationExplorationAnchor.startIndex,
      nextIndex: aspirationExplorationAnchor.nextIndex,
    }));
  }
  if (explorationTerminalContribution) {
    const token = Object.freeze({});
    ISSUED_EXPLORATION_TERMINAL_CONTRIBUTIONS.set(understanding, Object.freeze({
      token, understanding,
      action: understanding.actions[explorationTerminalContribution.actionIndex],
      actionIndex: explorationTerminalContribution.actionIndex,
      context: explorationTerminalContribution.context,
      evidenceSummary: explorationTerminalContribution.evidenceSummary,
      evidenceReceiptSha256: explorationTerminalContribution.evidenceReceiptSha256,
    }));
  }
  actions.forEach((action, index) => {
    if (action.capabilityId !== 'goal.portfolio.manage'
      || !selfAttentionActionBindingMatched(selfAttention, action, clampedDurability)) return;
    const publicAction = understanding.actions[index];
    const token = Object.freeze({});
    ISSUED_SELF_ATTENTION_GOAL_ACTIONS.set(publicAction, token);
    SELF_ATTENTION_GOAL_ACTION_TOKENS.set(token, Object.freeze({
      understanding,
      action: publicAction,
      actionIndex: index,
      goalId: selfAttention.id,
      goalKey: selfAttention.goalKey,
      goalRef: selfAttention.goalRef,
      goalRevision: selfAttention.goalRevision,
      claimedUntil: selfAttention.claimedUntil,
      operationId: action.operationId,
      requirementId: action.requirementId,
    }));
  });
  return understanding;
}
export function verifyTurnUnderstanding(value) {
  if (!plainObject(value) || value.contract !== CONTRACT || typeof value.sha256 !== 'string') return false;
  try {
    const { sha256: claimed, ...body } = value;
    return claimed === sha256(canonicalJson(body));
  } catch { return false; }
}

/**
 * Stable semantic authority for a re-issued interpretation. Runtime turn and
 * message identifiers prove one concrete issuance, but must not make an
 * otherwise byte-identical retry impossible after a safe yield. Every
 * completion-relevant semantic field remains inside this hash.
 */
export function turnUnderstandingSemanticAuthoritySha256(value) {
  if (!verifyTurnUnderstanding(value)) throw new TypeError('invalid_turn_understanding');
  const { sha256: _issuedSha256, source, temporal, ...body } = value;
  const {
    turnId: _runtimeTurnId,
    messageId: _runtimeMessageId,
    ...stableSource
  } = source || {};
  const { interpretedAt: _interpretedAt, ...stableTemporal } = temporal || {};
  return sha256(canonicalJson({
    ...body,
    source: stableSource,
    temporal: stableTemporal,
  }));
}
/** Alleen de interpreter kan objectidentiteit aan deze private uitgifteset toevoegen. */
export function isIssuedTurnUnderstanding(value) {
  return ISSUED_UNDERSTANDINGS.has(value) && verifyTurnUnderstanding(value);
}
export function consumeIssuedAspirationExplorationAnchor(understanding) {
  const issued = understanding && typeof understanding === 'object'
    ? ISSUED_ASPIRATION_EXPLORATION_ANCHORS.get(understanding) : null;
  if (!issued) return null;
  if (issued.understanding !== understanding
    || !isIssuedTurnUnderstanding(understanding)
    || CONSUMED_ASPIRATION_EXPLORATION_ANCHORS.has(issued.token)
    || understanding.durability !== 'episode'
    || understanding.actions.length !== 2
    || understanding.actions[issued.startIndex] !== issued.startAction
    || understanding.actions[issued.nextIndex] !== issued.nextAction
    || canonicalJson(understanding.firstDecision.requirementIds)
      !== canonicalJson(['r1', 'r2'])
    || issued.startAction.requirementId !== 'r1'
    || issued.nextAction.requirementId !== 'r2') {
    throw new TypeError('invalid_or_consumed_aspiration_exploration_anchor');
  }
  CONSUMED_ASPIRATION_EXPLORATION_ANCHORS.add(issued.token);
  return Object.freeze({
    understanding, aspirationId: issued.aspirationId,
    startAction: issued.startAction, nextAction: issued.nextAction,
  });
}
export function bindIssuedExplorationTerminalContribution(understanding, action) {
  const issued = understanding && typeof understanding === 'object'
    ? ISSUED_EXPLORATION_TERMINAL_CONTRIBUTIONS.get(understanding) : null;
  if (!issued) return null;
  if (issued.understanding !== understanding || issued.action !== action
    || !isIssuedTurnUnderstanding(understanding)
    || understanding.actions.length !== 1
    || understanding.actions[issued.actionIndex] !== action
    || understanding.firstDecision.kind !== 'act'
    || canonicalJson(understanding.firstDecision.requirementIds) !== canonicalJson(['r1'])
    || PLANNED_EXPLORATION_TERMINAL_CONTRIBUTIONS.has(issued.token)
    || CONSUMED_EXPLORATION_TERMINAL_CONTRIBUTIONS.has(issued.token)) return null;
  PLANNED_EXPLORATION_TERMINAL_CONTRIBUTIONS.add(issued.token);
  return Object.freeze({ token: issued.token, understanding, action });
}
export function consumeIssuedExplorationTerminalContribution(claim, expectedArgs) {
  const token = claim?.token;
  const binding = claim?.understanding && typeof claim.understanding === 'object'
    ? ISSUED_EXPLORATION_TERMINAL_CONTRIBUTIONS.get(claim.understanding) : null;
  if (!binding || binding.token !== token
    || claim.understanding !== binding.understanding || claim.action !== binding.action
    || !isIssuedTurnUnderstanding(binding.understanding)
    || binding.understanding.actions[binding.actionIndex] !== binding.action
    || !frozenJsonData(expectedArgs)
    || canonicalJson((({ bewerking: _operation, ...physical }) => physical)(expectedArgs))
      !== canonicalJson(binding.action.semanticInput)
    || !PLANNED_EXPLORATION_TERMINAL_CONTRIBUTIONS.has(token)
    || CONSUMED_EXPLORATION_TERMINAL_CONTRIBUTIONS.has(token)) return null;
  CONSUMED_EXPLORATION_TERMINAL_CONTRIBUTIONS.add(token);
  const fromRevision = binding.context.revision, toRevision = fromRevision + 1;
  const outcomeBasis = Object.freeze({
    contract: 'ExplorationTerminalContribution/v1',
    explorationId: binding.context.id,
    fromRevision, toRevision, terminalStatus: 'completed',
  });
  const behavior = binding.action.semanticInput.gedragsproef;
  let behaviorProposalContext = null;
  if (behavior) {
    behaviorProposalContext = Object.freeze({});
    ISSUED_BEHAVIOR_PROPOSALS.set(behaviorProposalContext, Object.freeze({
      candidate: Object.freeze({
        person: behavior.doelpersoon,
        gapSummary: behavior.waargenomen_kloof,
        hypothesis: behavior.hypothese,
        motivation: behavior.motivatie,
        dimension: behavior.dimensie,
        direction: behavior.richting,
        intensity: behavior.intensiteit,
        contextScope: behavior.contextscope,
      }),
      sourceKind: 'self_initiated',
      sourceOutcomeKey: sha256(canonicalJson(outcomeBasis)),
      sourceEvidenceSha256: sha256(binding.evidenceSummary),
    }));
  }
  const precommit = Object.freeze({
    contract: outcomeBasis.contract,
    understanding: binding.understanding,
    action: binding.action,
    expectedArgs,
    snapshot: binding.context,
    evidenceSummary: binding.evidenceSummary,
    evidenceReceiptSha256: binding.evidenceReceiptSha256,
    explorationId: outcomeBasis.explorationId,
    fromRevision, toRevision,
    terminalStatus: outcomeBasis.terminalStatus,
    outcomeKey: sha256(canonicalJson(outcomeBasis)),
    behaviorProposalContext,
  });
  const mutationAuthority = Object.freeze({});
  ISSUED_EXPLORATION_TERMINAL_PRECOMMITS.set(precommit, mutationAuthority);
  EXPLORATION_TERMINAL_MUTATION_STATES.set(mutationAuthority, 'issued');
  return precommit;
}
export function reserveIssuedExplorationTerminalMutation(precommit) {
  const authority = precommit && typeof precommit === 'object'
    ? ISSUED_EXPLORATION_TERMINAL_PRECOMMITS.get(precommit) : null;
  if (!authority || EXPLORATION_TERMINAL_MUTATION_STATES.get(authority) !== 'issued') {
    return null;
  }
  EXPLORATION_TERMINAL_MUTATION_STATES.set(authority, 'reserved');
  return authority;
}
export function issuedExplorationTerminalMutationMatches(authority, precommit) {
  return Boolean(authority && precommit
    && ISSUED_EXPLORATION_TERMINAL_PRECOMMITS.get(precommit) === authority
    && EXPLORATION_TERMINAL_MUTATION_STATES.get(authority) === 'reserved');
}
export function commitIssuedExplorationTerminalMutation(authority, precommit) {
  if (!issuedExplorationTerminalMutationMatches(authority, precommit)) return false;
  EXPLORATION_TERMINAL_MUTATION_STATES.set(authority, 'committed');
  return true;
}
export function releaseIssuedExplorationTerminalMutation(authority, precommit) {
  if (!issuedExplorationTerminalMutationMatches(authority, precommit)) return false;
  EXPLORATION_TERMINAL_MUTATION_STATES.set(authority, 'issued');
  return true;
}
export function bindIssuedSelfAttentionGoalAction(understanding, action) {
  const token = action && typeof action === 'object'
    ? ISSUED_SELF_ATTENTION_GOAL_ACTIONS.get(action) : null;
  const binding = token ? SELF_ATTENTION_GOAL_ACTION_TOKENS.get(token) : null;
  if (!binding || binding.action !== action
    || binding.understanding !== understanding
    || !isIssuedTurnUnderstanding(understanding)
    || understanding.actions[binding.actionIndex] !== action
    || !EXECUTING_DECISIONS.has(understanding.firstDecision.kind)
    || !understanding.firstDecision.requirementIds.includes(binding.requirementId)
    || PLANNED_SELF_ATTENTION_GOAL_ACTION_TOKENS.has(token)
    || CONSUMED_SELF_ATTENTION_GOAL_ACTION_TOKENS.has(token)
    || action.capabilityId !== 'goal.portfolio.manage'
    || action.operationId !== binding.operationId
    || action.requirementId !== binding.requirementId
    || action.semanticInput?.doel_ref !== binding.goalRef
    || action.semanticInput?.verwachte_revisie !== binding.goalRevision
    || Object.hasOwn(action.semanticInput || {}, 'doel_sleutel')) return null;
  PLANNED_SELF_ATTENTION_GOAL_ACTION_TOKENS.add(token);
  return Object.freeze({ token, understanding, action });
}
export function consumeIssuedSelfAttentionGoalAction(
  claim,
  now = /** @type {number | Date} */ (Date.now()),
) {
  const token = claim?.token;
  const issued = token && typeof token === 'object'
    ? SELF_ATTENTION_GOAL_ACTION_TOKENS.get(token) : null;
  const at = now instanceof Date ? now.getTime() : new Date(now).getTime();
  if (!issued || claim.understanding !== issued.understanding
    || claim.action !== issued.action
    || !isIssuedTurnUnderstanding(issued.understanding)
    || issued.understanding.actions[issued.actionIndex] !== issued.action
    || !issued.understanding.firstDecision.requirementIds.includes(issued.requirementId)
    || !PLANNED_SELF_ATTENTION_GOAL_ACTION_TOKENS.has(token)
    || CONSUMED_SELF_ATTENTION_GOAL_ACTION_TOKENS.has(token)
    || !Number.isFinite(at) || Date.parse(issued.claimedUntil) <= at) return false;
  CONSUMED_SELF_ATTENTION_GOAL_ACTION_TOKENS.add(token);
  return Object.freeze({
    goalId: issued.goalId, goalKey: issued.goalKey, goalRef: issued.goalRef,
    goalRevision: issued.goalRevision, claimedUntil: issued.claimedUntil,
    operationId: issued.operationId, requirementId: issued.requirementId,
  });
}
export function signalBatchForUnderstanding(value) {
  return isIssuedTurnUnderstanding(value) ? ISSUED_SIGNAL_BATCHES.get(value) || null : null;
}
export function continuityWatchForUnderstanding(value) {
  return isIssuedTurnUnderstanding(value) ? ISSUED_CONTINUITY_WATCHES.get(value) || null : null;
}
export function createTurnUnderstandingRecord(value, { redactText = (text) => text } = {}) {
  if (!verifyTurnUnderstanding(value)) throw new TypeError('invalid_turn_understanding');
  const safeText = (text, max) => {
    const limited = limitedText(text, max);
    return limited == null ? null : limitedText(redactText(limited), max);
  };
  const goal = normalizeGoal(value.goal);
  const body = {
    contract: RECORD_CONTRACT,
    sourceSha256: value.sha256,
    sourceKind: String(value.source?.kind || ''),
    sourceTextSha256: value.source?.textSha256 || null,
    dialogueAct: DIALOGUE_ACTS.has(value.dialogueAct) ? value.dialogueAct : 'unknown',
    goal: {
      statement: safeText(goal.statement, 1_000),
      outcome: safeText(goal.outcome, 1_000),
      beneficiary: goal.beneficiary,
    },
    conditions: (Array.isArray(value.conditions) ? value.conditions : []).map((item) => ({
      statement: safeText(item?.statement, 500),
      role: CONDITION_ROLES.has(item?.role) ? item.role : 'guard',
      state: CONDITION_STATES.has(item?.state) ? item.state : 'unknown',
      sourceSpans: Array.isArray(item?.sourceSpans)
        ? structuredClone(item.sourceSpans) : [],
      requirementIds: Array.isArray(item?.requirementIds) ? [...item.requirementIds] : [],
      afterRequirementIds: Array.isArray(item?.afterRequirementIds)
        ? [...item.afterRequirementIds] : [],
    })),
    temporal: {
      mode: value.temporal?.mode || 'none',
      notBefore: value.temporal?.notBefore || null,
      dueBy: value.temporal?.dueBy || null,
      condition: safeText(value.temporal?.condition, 500),
    },
    durability: DURABILITY.has(value.durability) ? value.durability : 'turn',
    requestedEffects: normalizeEffects(value.requestedEffects),
    deniedEffects: normalizeEffects(value.deniedEffects),
    actions: (Array.isArray(value.actions) ? value.actions : []).map((action) => ({
      requirementId: action.requirementId,
      capabilityId: action.capabilityId,
      operationId: action.operationId,
      polarity: action.polarity,
      effect: action.effect,
      authorization: action.authorization,
      sourceSpans: Array.isArray(action.sourceSpans)
        ? structuredClone(action.sourceSpans) : [],
      boundaryReasons: Array.isArray(action.boundaryReasons)
        ? [...action.boundaryReasons] : [],
    })),
    firstDecision: {
      kind: value.firstDecision?.kind || 'fail_closed',
      requirementIds: Array.isArray(value.firstDecision?.requirementIds)
        ? [...value.firstDecision.requirementIds] : [],
      replyDraftSha256: value.firstDecision?.replyDraft == null
        ? null : sha256(String(value.firstDecision.replyDraft)),
    },
    decisionProjection: {
      proposedKind: value.decisionProjection?.proposedKind || value.firstDecision?.kind || 'fail_closed',
      proposedRequirementIds: Array.isArray(value.decisionProjection?.proposedRequirementIds)
        ? [...value.decisionProjection.proposedRequirementIds] : [],
      acceptedRequirementIds: Array.isArray(value.decisionProjection?.acceptedRequirementIds)
        ? [...value.decisionProjection.acceptedRequirementIds] : [],
      rejectedRequirementIds: Array.isArray(value.decisionProjection?.rejectedRequirementIds)
        ? [...value.decisionProjection.rejectedRequirementIds] : [],
      unknownRequirementIds: Array.isArray(value.decisionProjection?.unknownRequirementIds)
        ? [...value.decisionProjection.unknownRequirementIds] : [],
      hostAddedRequirementIds: Array.isArray(value.decisionProjection?.hostAddedRequirementIds)
        ? [...value.decisionProjection.hostAddedRequirementIds] : [],
    },
    monitoring: value.monitoring ? structuredClone(value.monitoring) : null,
  };
  return deepFreeze({ ...body, sha256: sha256(canonicalJson(body)) });
}
export function verifyTurnUnderstandingRecord(value) {
  if (!plainObject(value) || value.contract !== RECORD_CONTRACT
    || typeof value.sha256 !== 'string' || typeof value.sourceSha256 !== 'string') return false;
  try {
    const { sha256: claimed, ...body } = value;
    return claimed === sha256(canonicalJson(body));
  } catch { return false; }
}
function knownTargetIn(value, targets) {
  const normalized = value.normalize('NFKC').toLocaleLowerCase('nl-NL');
  return targets.find((target) => {
    const alias = target.normalize('NFKC').toLocaleLowerCase('nl-NL');
    const start = normalized.indexOf(alias);
    if (start < 0) return false;
    const before = normalized[start - 1] || ' ';
    const after = normalized[start + alias.length] || ' ';
    return !/[\p{L}\p{N}_]/u.test(before) && !/[\p{L}\p{N}_]/u.test(after);
  }) || null;
}
function literalVoicePayload(text, targets) {
  // Een expliciet citaat is de sterkste payloadgrens. Tekst na het sluitende
  // aanhalingsteken is weer een instructie en mag nooit mee de speaker in.
  for (const quote of quoteRanges(text)) {
    const before = text.slice(0, quote.start);
    const after = text.slice(quote.end);
    const support = [before, after].find((part) => knownTargetIn(part, targets) && VOICE_MARKER.test(part));
    const target = support ? knownTargetIn(support, targets) : null;
    if (target) return {
      target, text: text.slice(quote.start + 1, quote.end - 1).trim(),
      sourceSpans: support === before ? [{ start: 0, end: quote.start }]
      : [{ start: quote.end, end: text.length }],
    };
  }
  const colon = text.match(/^([\s\S]{1,240}?):\s*(\S[\s\S]{0,500})$/u);
  if (colon) {
    const target = knownTargetIn(colon[1], targets);
    if (target && VOICE_MARKER.test(colon[1])) return {
      target, text: colon[2].replace(/^["“‘']|["”’']$/gu, '').trim(),
      sourceSpans: [{ start: 0, end: colon[1].length + 1 }],
    };
  }
  return null;
}
function voiceOperation(operations) { return [...operations.values()].find((item) => item.fastlane === 'literal_voice') || null; }
function contextualHomeOperation(operations) {
  return [...operations.values()].find((item) => item.fastlane === 'contextual_home') || null;
}
function contextualHomeFastlane(envelope, operations, grounding, now) {
  const operation = contextualHomeOperation(operations);
  if (envelope.channel !== 'voice' || !operation
    || grounding?.contract !== 'VoiceHomeGrounding/v1') return null;
  if (grounding.status === 'stale') {
    return finalizedUnderstanding({
      dialogueAct: 'command', actions: [], requestedEffects: [], deniedEffects: [operation.effect],
      temporal: { mode: 'now' }, durability: 'turn',
      delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: ['voice_turn_stale'],
      firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
    }, envelope, operations, now, 'voice_home_stale');
  }
  if (['ambiguous', 'unavailable'].includes(grounding.status)) {
    const draft = String(grounding.question || '').trim().slice(0, 220);
    const question = `${draft.replace(/[?.!]+$/u, '') || 'Welk apparaat bedoel je precies'}?`;
    return finalizedUnderstanding({
      dialogueAct: 'command', actions: [], requestedEffects: [], deniedEffects: [operation.effect],
      temporal: { mode: 'now' }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [question],
      firstDecision: { kind: 'clarify', requirementIds: [], replyDraft: question },
    }, envelope, operations, now, 'voice_home_ambiguity');
  }
  const desiredState = ({ aan: 'on', uit: 'off', open: 'open', dicht: 'close' })[
    String(grounding.action || '').toLowerCase()
  ];
  const target = String(grounding.entityId || '').trim().toLowerCase();
  if (grounding.status !== 'resolved' || !desiredState
    || !/^[a-z_]+\.[a-z0-9_]+$/u.test(target)) return null;
  return finalizedUnderstanding({
    dialogueAct: 'command',
    goal: {
      statement: envelope.message.text,
      outcome: `${String(grounding.name || target).slice(0, 200)} ${desiredState === 'on' ? 'aan' : desiredState === 'off' ? 'uit' : desiredState}.`,
      beneficiary: 'household',
    },
    confidence: Number.isFinite(Number(grounding.confidence)) ? Number(grounding.confidence) : 0.8,
    actions: [{
      capabilityId: operation.capabilityId, operationId: operation.operationId,
      polarity: 'request', semanticInput: { target, desiredState },
      sourceSpans: [{ start: 0, end: envelope.message.text.length }],
    }],
    requestedEffects: [operation.effect], deniedEffects: [],
    temporal: { mode: 'now' }, durability: 'turn',
    delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
    firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
  }, envelope, operations, now, 'voice_home_context');
}
function hasPriorVoiceOperation(envelope, operation) {
  if (!operation) return null;
  return [...envelope.priorUnderstandings].reverse().some((candidate) => (
    candidate.actions?.some((action) => action.capabilityId === operation.capabilityId
      && action.operationId === operation.operationId && action.authorization === 'allowed')
  ));
}
function voiceRaw(operation, envelope, semanticInput, sourceSpans, kind = 'act') {
  return {
    actions: [{
      capabilityId: operation.capabilityId, operationId: operation.operationId, polarity: 'request', semanticInput,
      sourceSpans: sourceSpans || [{ start: 0, end: envelope.message.text.length }],
    }],
    requestedEffects: [operation.effect], deniedEffects: [], temporal: { mode: 'now' }, durability: 'turn',
    delivery: { mode: 'reply', target: 'origin' },
    proofNeeds: operation.proofNeeds, ambiguities: [],
    firstDecision: { kind, requirementIds: ['r1'], replyDraft: null },
  };
}
function voiceFastlane(envelope, operations, targets, now) {
  const operation = voiceOperation(operations);
  if (!operation || !targets.length) return null;
  if (hasPriorVoiceOperation(envelope, operation) && CANCEL_TURN.test(envelope.message.text)) {
    return finalizedUnderstanding({
      actions: [], requestedEffects: [], deniedEffects: [operation.effect],
      temporal: { mode: 'now' }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'cancel', requirementIds: [], replyDraft: null },
    }, envelope, operations, now, 'voice_fastlane');
  }
  const retry = envelope.voiceRetryIntent;
  if (retry && Date.parse(retry.expiresAt) >= now.getTime()
    && RETRY.test(envelope.message.text) && !MUTATION_NEGATION.test(envelope.message.text)) {
    if (retry.disposition === 'already_completed') {
      return finalizedUnderstanding({
        dialogueAct: 'continue', actions: [], requestedEffects: [],
        deniedEffects: [operation.effect], temporal: { mode: 'now' }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: {
          kind: 'reply', requirementIds: [],
          replyDraft: 'Die omroep is al naar de gekozen Voice PE gestuurd. Ik stuur ’m niet dubbel.',
        },
      }, envelope, operations, now, 'voice_retry_completed');
    }
    return finalizedUnderstanding(voiceRaw(operation, envelope, {
      literalText: retry.action.tekst,
      ...(retry.action.doel ? { targetAlias: retry.action.doel } : {}),
    }), envelope, operations, now, 'voice_retry');
  }
  const payload = literalVoicePayload(envelope.message.text, targets);
  if (!payload || !payload.text) return null;
  const prefix = envelope.message.text.slice(0, Math.max(0, envelope.message.text.indexOf(payload.text)));
  if (MUTATION_NEGATION.test(prefix) || HYPOTHETICAL.test(prefix) || hasConditionalMarker(prefix)
    || DEFERRED.test(prefix) || CANCEL.test(prefix)) return null;
  return finalizedUnderstanding(voiceRaw(operation, envelope, {
    targetAlias: payload.target, literalText: payload.text,
  }, payload.sourceSpans), envelope, operations, now, 'voice_fastlane');
}
function responseTool(operations, envelope, selfAttention = null, monitoring = null) {
  const context = semanticContext(envelope);
  const currentSignalBatch = currentSignalBatchTurn(envelope);
  const explorationTurn = verifiedExplorationContinuation(selfAttention) !== null;
  const operationList = [...operations.values()].filter((item) => !context.signalBatch
    || item.capabilityId !== 'signal.assess' || item.operationId !== 'assess');
  const capabilities = [...new Set(operationList.map((item) => item.capabilityId))];
  const operationIds = [...new Set(operationList.map((item) => item.operationId))];
  const signalInput = context.signalBatch ? projectedSignalAssessmentInput(operations) : null;
  const contextHashes = [...new Set(context.priorUnderstandings.map((item) => item.sha256))];
  const object = (properties, required = Object.keys(properties)) => ({
    type: 'object', additionalProperties: false, properties, required,
  });
  const array = (items, maxItems) => ({ type: 'array', items,
    ...(maxItems === undefined ? {} : { maxItems }) });
  const span = object({ start: { type: 'integer' }, end: { type: 'integer' } });
  const spans = array(span, 6);
  const actionSchema = operationList.length ? object({
    capabilityId: { type: 'string', enum: capabilities },
    operationId: { type: 'string', enum: operationIds },
    polarity: { type: 'string', enum: [...POLARITIES] },
    sourceSpans: spans,
    semanticInput: { type: 'object', additionalProperties: true },
  }) : object({});
  const contextHashItems = contextHashes.length
    ? { type: 'string', enum: contextHashes } : { type: 'string' };
  return {
    name: 'commit_turn_understanding',
    description: 'Leg betekenis en eerste beslissing van deze ene beurt samen vast.',
    parameters: object({
      dialogueAct: { type: 'string', enum: [...DIALOGUE_ACTS] },
      goal: object({ statement: { type: ['string', 'null'] }, outcome: { type: ['string', 'null'] },
        beneficiary: { type: 'string', enum: [...BENEFICIARIES] } }),
      conditions: array(object({
        statement: { type: 'string' },
        role: { type: 'string', enum: [...CONDITION_ROLES] },
        state: { type: 'string', enum: [...CONDITION_STATES] },
        sourceSpans: spans,
        requirementIds: array({ type: 'string', pattern: '^r(?:[1-9]|1[0-2])$' }, 12),
        afterRequirementIds: array({ type: 'string', pattern: '^r(?:[1-9]|1[0-2])$' }, 12),
      }), 12),
      provenance: object({ sourceSpans: spans,
        contextHashes: array(contextHashItems, contextHashes.length ? 8 : 0) }),
      confidence: { type: 'number', minimum: 0, maximum: 1 },
      actions: array(actionSchema, operationList.length ? (signalInput ? 11 : 12) : 0),
      ...(signalInput ? { signalAssessment: signalInput } : {}),
      requestedEffects: array({ type: 'string', enum: EFFECTS }),
      deniedEffects: array({ type: 'string', enum: EFFECTS }),
      temporal: currentSignalBatch || explorationTurn || monitoring?.requested === true
        ? object({ mode: { type: 'string', enum: ['now'] },
          startSourceSpans: array(span, 0) })
        : object({
          mode: { type: 'string', enum: selfAttention ? ['now', 'none'] : [...TEMPORAL_MODES] },
          notBefore: { type: ['string', 'null'] },
          dueBy: { type: ['string', 'null'] }, condition: { type: ['string', 'null'] },
          startSourceSpans: array(span, selfAttention ? 0 : 4),
        }, ['mode', 'startSourceSpans']),
      durability: { type: 'string', enum: currentSignalBatch || explorationTurn
        ? ['turn'] : [...DURABILITY] },
      delivery: object({
        mode: { type: 'string', enum: explorationTurn ? ['none', 'silent'] : [...DELIVERY_MODES] },
        target: { type: 'string', enum: explorationTurn ? ['none'] : [...DELIVERY_TARGETS] },
      }),
      proofNeeds: array(object({
          kind: { type: 'string', pattern: '^[a-z][a-z0-9_.:-]{0,119}$' },
          actionIndex: { type: ['integer', 'null'] }, freshness: { type: 'string' },
        }, ['kind'])),
      ambiguities: array({ type: 'string' }),
      firstDecision: object({ kind: { type: 'string', enum: [...DECISIONS] },
        requirementIds: array({ type: 'string', pattern: '^r(?:[1-9]|1[0-2])$' }, 12),
        replyDraft: { type: ['string', 'null'] } }),
    }),
  };
}
const RETRY_HINTS = Object.freeze({
  exploration_durability_not_turn: 'Een hostgebonden exploration voert uitsluitend huidige acties uit: gebruik exact durability turn; de host herschrijft episode niet.',
  exploration_temporal_not_current: 'Een hostgebonden exploration vereist exact temporal {mode:"now",startSourceSpans:[]} zonder latere velden.',
  exploration_delivery_not_current: 'Een hostgebonden exploration heeft geen ontvanger: gebruik delivery none of silent met target none.',
  selected_denied_action_and_redundant_work_start: 'Corrigeer beide grenzen in dezelfde volledige retry: selecteer geen denied/mention-action en verwijder de work.episode.manage:start naast huidig turnwerk. Een positieve mutatie vereist een eigen letterlijke actionspan; gebruik durability episode uitsluitend voor echte latere hervatting.',
  redundant_work_start: 'Een work.episode.manage:start naast een concrete huidige action vereist durability episode voor echte latere hervatting. Gebruik bij volledig werk in deze beurt durability turn en verwijder de start-action.',
  missing_durable_action: 'Gebruik durability turn als alle gekozen stappen nu uitvoerbaar zijn; gebruik episode alleen voor echte hervatting en selecteer dan een aangeboden persistent action met supportsDeferred=1.',
  missing_durable_schedule: 'Een nieuwe geplande werkepisode vereist een concrete volgende_stap, toetsbare definitie_klaar en een toekomstig hervatten_na; maak de beurt anders niet duurzaam.',
  self_attention_effect_not_allowed: 'SelfAttention mag inhoudelijk uitsluitend een huidige read-, discovery- of workspace-action kiezen. Gebruik alleen exact gebonden exploration-/workadministratie volgens de aangeboden aandachtsoort; kies nooit een huis-, bericht-, voice-, runtime- of vrije persistent-action.',
  multiple_self_attention_work_starts: 'Gebruik bij selfAttention hoogstens één exact gebonden work.episode.manage:start. Combineer of verwijder concurrerende starts; de host kiest niet welke episodevelden leidend zijn.',
  multiple_self_attention_goal_manage: 'Kies bij een geattesteerde doelreview hoogstens één goal.portfolio.manage-bewerking. De host kiest niet tussen concurrerende doelmutaties.',
  multiple_self_attention_exploration_manage: 'Kies bij een geattesteerde exploration hoogstens één update-, wacht- of terminale beheeractie. De host kiest niet tussen concurrerende cyclusmutaties.',
  self_attention_exploration_manage_not_exclusive: 'Een exploration-managecontinuatie is een directe CAS en moet de enige allowed/request-action van deze beurt zijn; voer inhoudelijke read/discovery/workspace in een afzonderlijke attention uit.',
  self_attention_goal_review_not_future: 'Een goal.portfolio.manage:update vanuit geattesteerde doelaandacht vereist een volledig modelgeleverd geldig ISO-volgende_review op of na het geattesteerde claimmoment en in de toekomst.',
  self_attention_exploration_wake_not_future: 'Een exploration.manage:wachten vanuit geattesteerde exploratie vereist een volledig modelgeleverd geldig toekomstig ISO-hervatten_na.',
  missing_self_attention_exploration_start: 'Verse aspiration/goal-aandacht mag één inhoudelijke huidige stap doen. Voeg vóór inhoudelijke stap twee en vóór een duurzame episode-action exact één volledig modelgeleverde exploration.manage:start toe; de host verzint geen onderwerp of velden.',
  late_self_attention_exploration_start: 'Zet de volledig modelgeleverde exploration.manage:start vóór inhoudelijke stap twee en vóór work.episode.manage:start of een andere duurzame episode-action.',
  multiple_self_attention_exploration_starts: 'Gebruik bij verse aspiration/goal-aandacht hoogstens één exploration.manage:start; kies en bind één eigen vraagcyclus.',
  bound_self_attention_duplicate_exploration_start: 'Deze geattesteerde exploration bestaat al en is hostgebonden. Hervat of sluit haar met haar bestaande id; maak geen tweede exploration.manage:start.',
  missing_bounded_continuity_schedule: 'Deze positieve opletopdracht vereist exact twee chronologische actions: eerst home.context.read:read voor een verse momentopname, daarna work.episode.manage:start met durability episode, concrete volgende_stap en definitie_klaar, en hervatten_na binnen het aangeboden venster. Dit plant precies één hercontrole; voeg geen huisactie of berichtactie toe.',
  monitoring_temporal_not_current: 'Een monitoringstart voert haar grounding nu uit: gebruik exact temporal {mode:"now",startSourceSpans:[]} zonder notBefore, dueBy of condition; de meldvoorwaarde blijft in conditions en delivery later.',
  invalid_monitoring_source_shape: 'Gebruik chronologisch uitsluitend optioneel één home.context.read:read, daarna één of meer home.entity.read:read en als laatste exact één work.episode.manage:start. Een onopgeloste deferred entityId vereist die ene contextread.',
  missing_monitoring_episode_action: 'Monitoring vereist één work.episode.manage:start-action; een berichtactie of algemene planning vervangt die episode niet.', missing_monitoring_source_read: 'Monitoring vereist één of meer home.entity.read:read-actions naast dezelfde work.episode.manage:start. Gebruik exacte geverifieerde entityIds, of eerst maximaal één home.context.read:read en daarna deferred rebindable entity-markers.',
  missing_monitoring_source_coverage: 'Deze voorwaarde vergelijkt twee veranderlijke bronnen. Gebruik minimaal twee verschillende home.entity.read:read-actions, één per vergeleken actuele bron, naast dezelfde work.episode.manage:start.',
  missing_durable_monitoring_schedule: 'Selecteer exact één work.episode.manage:start met concrete titel en doel. De host bindt daarna de procedurele volgende stap, definitie, hervattijd en ontvanger uit het geverifieerde monitoringcontract.', selected_denied_action: 'Een uitvoerende firstDecision mag uitsluitend requirementIds met polarity request en authorization allowed selecteren. Een bevel om bestaand werk te annuleren, stoppen, pauzeren, verwijderen of sluiten is een positieve request voor die beheeroperatie, geen polarity deny. Gebruik deny alleen wanneer de gebruiker zegt dat een operatie niet mag gebeuren. Geef iedere positieve mutatie haar eigen letterlijke actionspan en selecteer een echte deny nooit.',
  workspace_read_requires_concrete_path: 'Een workspace-read vereist een concreet bestandspad. Is het onbekend, selecteer dan eerst workspace.file.list:list met bekende map en gerichte zoekterm.', workspace_path_not_bound_to_receipt: 'Gebruik bij continue/correct exact het bron-/testpad uit artifactReceipts; alleen een niet-uitvoerbare fixture in dezelfde map mag nieuw zijn. Gebruik command alleen voor een nieuw doel.',
  invalid_temporal_value: 'Laat notBefore en dueBy null tenzij je een volledige geldige ISO-8601-timestamp hebt.', invalid_temporal_source_span: 'Gebruik bij temporal now/none exact startSourceSpans []; alleen later/condition krijgt een geldige letterlijke starttrigger binnen turn.text.', unknown_decision_requirement: 'firstDecision.requirementIds mag uitsluitend bestaande ids r1 tot en met rN uit actions bevatten.',
  missing_concrete_completion_action: 'Breng requestedEffects in lijn met doel, guards en actions. Voeg nooit een mutatie toe om een effectlabel te dekken: zij vereist een eigen letterlijke action-bronspan; verwijder anders het overtollige effect.',
  orphan_effectful_continuation: 'Er is geen actief vervolgwerk of artifactReceipt. Gebruik command alleen voor een nieuw doel; verzin bij continue/correct geen mutatie of werkruimtepad.',
  missing_reply_draft: 'firstDecision kind reply of clarify vereist een niet-lege, natuurlijke replyDraft die jij zelf schrijft. Gebruik geen hostplaceholder en laat replyDraft alleen null bij een besluit dat geen direct antwoord vormt.',
  invalid_reply_draft: 'Schrijf firstDecision.replyDraft als één niet-lege natuurlijke reactie van maximaal 4000 tekens. Lever de definitieve tekst zelf; de host kort of herschrijft haar niet.',
  unbound_persistent_memory_reference: 'Een blijvende herinnering moet zelfstandig begrijpelijk blijven. Vervang laatste/relevante/dit/dat/deze/die door het concrete onderwerp of een stabiele bericht-, foto- of datumref uit de gegeven context. Als die binding ontbreekt, verwijder memory.fact.write en antwoord of vraag één concrete verduidelijking.',
  historical_current_requires_live_inspection: 'Historische gespreksherinnering is onbevestigde context en nooit bewijs voor een actuele toestand van een concreet onderwerp. Selecteer minstens één read- of discovery-action met freshness live; firstDecision wordt dan inspect.',
  prior_home_result_requires_continuation: 'Dit is een korte vervolgvraag naar de uitkomst van de direct vorige huisactie. Gebruik dialogueAct continue of correct en controleer de exact aangeboden home.entity.read-resourceReceipt vers.',
  prior_home_result_requires_live_read: 'Een gesprekszin over de vorige huisactie is geen actuele statusbron. Selecteer exact één home.entity.read:read met de entityId uit de passende typed resourceReceipt en zet firstDecision op inspect; antwoord niet rechtstreeks uit gesprekstekst.',
  missing_signal_assessment: 'Een niet-lege SignalBatch vereist het verplichte getypeerde signalAssessment-veld met disposition en assessment.',
  multiple_signal_assessments: 'Gebruik voor de volledige SignalBatch precies één signal.assess:assess-action.',
  nonterminal_signal_assessment: 'Zet signal.assess:assess als laatste action, na alle inhoudelijke reads of handelingen.',
  orphan_signal_assessment: 'Gebruik signal.assess uitsluitend wanneer de host een niet-lege SignalBatch in deze self-turn meegaf.',
  signal_assessment_policy_denied: 'Houd requestedEffects en deniedEffects in lijn met de verplichte persistente SignalBatch-beoordeling; een ontkende policy wordt niet door de host overschreven.',
});
function retryHintFor(validation = '', operations = new Map()) {
  if (RETRY_HINTS[validation]) return RETRY_HINTS[validation];
  if (validation === 'workspace_program_missing_concrete_entrypoint') return `Volg runtime en argumentrollen uit het execution-schema. ${programFileArgumentInstruction(operations)}`;
  if (validation.includes('patroon_wijkt_af') || validation.includes('source_bound_field')) return 'Volg het k/p-veldpatroon uit de catalogus. Gebruik exact één typed resourceReceipt of een punctuation-exacte huidige bronwaarde; kies anders vrije discovery en alleen bij deferred rebindable een marker die de host uit verified bewijs bindt.';
  if (validation.includes('_ontbrekend_veld_')) return 'Vul per action alle required velden uit het aangeboden input-schema in.';
  if (validation.startsWith('unscoped_effect_conflict:')) return 'Een globaal deniedEffect is absoluut. Verwijder het wanneer een afzonderlijke positieve operatie in dat effectdomein wel gevraagd is, en bind de lokale ontkenning aan haar eigen bekende capability+operation met polarity deny en exacte bronspan.';
  return '';
}
function modelRequest(envelope, operations, monitoring, attempt, timeoutMs,
  identityInstructions = '', retryContext = {}, resourceReceipts = [],
  conversationWorkReceipts = [], selfAttention = null) {
  const modelOperations = envelope.context.signalBatch
    ? new Map([...operations].filter(([key]) => key !== 'signal.assess\u0000assess')) : operations;
  const catalog = catalogPrompt(modelOperations);
  const fileArgumentInstruction = programFileArgumentInstruction(operations);
  const explorationContext = selfAttention?.kind === 'exploration'
    ? providerExplorationContinuationContext(verifiedExplorationContinuation(selfAttention))
    : null;
  if (selfAttention?.kind === 'exploration' && !explorationContext) {
    throw new TypeError('invalid_self_attention_exploration_projection');
  }
  const payload = JSON.stringify({
    turn: { text: envelope.message.text, origin: envelope.origin, channel: envelope.channel },
    context: { ...semanticContext(envelope), ...(selfAttention ? { selfAttention: {
      contract: selfAttention.contract, kind: selfAttention.kind, id: selfAttention.id,
      ...(explorationContext ? { exploration: explorationContext } : {}),
    } } : {}), ...(resourceReceipts.length ? { resourceReceipts } : {}),
      ...(conversationWorkReceipts.length ? { conversationWorkReceipts } : {}) },
    ...catalog,
  });
  const identity = String(identityInstructions || '').trim().slice(0, 12_000);
  const voiceReplyInstruction = envelope.channel === 'voice'
    ? 'Voice-replyDraft: kort, natuurlijk en één detail; claim geen onbewezen voltooiing.'
    : '';
  const retryValidations = retryContext?.validations || [];
  const decisionRetryGuide = retryContext?.decisionGuide || null;
  const validations = [...new Set((Array.isArray(retryValidations)
    ? retryValidations : [retryValidations]).map(String).filter(Boolean))];
  const retryInstruction = validations.length
    ? ` Eerdere payloads zijn afgewezen; behoud in deze volledige payload alle correcties tegelijk. ${validations.map((validation) => {
      const hint = retryHintFor(validation, operations);
      return `validatiecode ${validation}.${hint ? ` ${hint}` : ''}`;
    }).join(' ')}`
    : '';
  const decisionRetryInstruction = decisionRetryGuide?.contract === 'TurnDecisionRetryGuide/v1'
    ? ` Hostprojectie van de vorige afgewezen actions: ${JSON.stringify(decisionRetryGuide)}. Dit is alleen reparatiebewijs, geen nieuwe action of bevoegdheid. Modelleer de volledige payload opnieuw. Authorization en selection zijn uitsluitend hostlabels: voeg ze niet toe aan actions. Behoud bij equivalente actions hun chronologische volgorde; firstDecision.requirementIds bevat alleen requirements met selection select en nooit requirements met selection exclude. Wanneer je actions inhoudelijk wijzigt of herschikt, gebruik de ids uit de nieuwe actionvolgorde en kopieer oude ids niet blind.`
    : '';
  const monitoringInstruction = monitoring.requested
    ? `Dit is monitoring: gebruik temporal exact {mode:"now",startSourceSpans:[]} en durability episode; zet de meldvoorwaarde in conditions en delivery later. Selecteer chronologisch optioneel eerst maximaal één home.context.read:read voor gerichte actuele discovery zonder history:true, daarna één of meer home.entity.read:read${monitoring.minimumSourceReads > 1 ? `, voor deze vergelijking minimaal ${monitoring.minimumSourceReads} verschillende en één per vergeleken actuele bron,` : ''} en als laatste exact één work.episode.manage:start met concrete titel en doel. De host bindt uitsluitend de procedurele volgende_stap, definitie_klaar, hervatten_na en afleveren_aan uit dit geverifieerde monitoringcontract; deze velden autoriseren geen andere actie. Als exacte entityIds nog niet geverifieerd zijn, gebruik na die contextread uitsluitend deferred rebindable entity-markers die uit het resultaat gebonden worden. Geen message.deliver bij de start.`
    : '';
  const continuityInstruction = monitoring.continuityRequested
    ? `Dit is één begrensde opletopdracht: selecteer eerst exact home.context.read:read en daarna exact work.episode.manage:start; durability episode, delivery later/requester, concrete volgende_stap en definitie_klaar, hervatten_na tussen ${new Date(monitoring.minWakeTs).toISOString()} en ${new Date(monitoring.maxWakeTs).toISOString()}. Geen andere persistent/home/external action; dit belooft één hercontrole, geen doorlopende monitoring.`
    : '';
  const signalInstruction = envelope.context.signalBatch
    ? 'De host gaf een niet-lege SignalBatch: vul verplicht signalAssessment voor de hele batch en neem persistent op in requestedEffects; zet signal.assess niet in actions, want de host bindt die terminale actie. Handel deze batch volledig in de huidige beurt af met temporal mode now, lege startSourceSpans en durability turn; maak of plan geen werkepisode.' : '';
  const historicalInstruction = envelope.context.dialogueExcerpts.some((row) => (
    row.evidenceClass === 'historical_unconfirmed'
  ))
    ? 'Een dialogueExcerpt met evidenceClass historical_unconfirmed is alleen een historische aanwijzing: gebruik die nooit als bewijs voor een actuele toestand van een concreet onderwerp. Selecteer voor zo’n toestandsvraag minstens één read/discovery met freshness live en laat firstDecision inspect zijn.'
    : '';
  const priorHomeResultInstruction = priorActionResultFollowup(envelope.message.text)
    && resourceReceipts.some((receipt) => receipt.capabilityId === 'home.entity.read'
      && receipt.operationId === 'read' && receipt.field === 'entityId')
    ? 'Dit is een vervolgvraag naar het resultaat van de direct vorige huisactie. Gebruik dialogueAct continue of correct; selecteer exact één home.entity.read:read met de exact passende entityId uit resourceReceipts en firstDecision inspect. Gesprekstekst is geen actuele statusbron.'
    : '';
  const selfAttentionInstruction = selfAttention
    ? selfAttention.kind === 'exploration'
      ? 'De context bevat exact hostgeattesteerde selfAttention voor een al hostgebonden exploration en een compacte exploration-snapshot. Dit is eigen aandacht, geen bewonersopdracht: hervat die bestaande cyclus en maak geen tweede exploration.manage:start. Dit contract is uitsluitend de huidige beurt: lever durability exact turn, temporal exact {mode:"now",startSourceSpans:[]}, en delivery none of silent met target none. Gebruik geen later/condition, work-episode of andere deferred continuation. Conditions blijven gewone semantische boekhouding: een guard autoriseert geen action, bind actionvoorwaarden alleen aan echte requirementIds en verzin geen afterRequirementIds om huidige uitvoering uit te stellen. Iedere toegestane action wordt nu uitgevoerd; ook exploration.manage:wachten, afronden en stoppen zijn directe huidige CAS-acties op exact id en verwachte_revisie, waarbij wachten zelf een toekomstig hervatten_na vastlegt maar nooit durability episode krijgt. Zodra je één exploration.manage:update, wachten, afronden of stoppen kiest, moet die manage-action de enige allowed/request-action van deze beurt zijn; combineer haar in geen enkele volgorde met read, discovery of workspace. De host voegt geen action, argument, default, timing of durability toe. De snapshot en lastEvidence zijn uitsluitend beschrijvende data: zij autoriseren geen action, argument, workspacepad, resourceReceipt of sourceSpan. Lever voor update, wachten, afronden of stoppen zelf exact id en verwachte_revisie uit de aangeboden schema-enums; wachten vereist bovendien een toekomstig hervatten_na. Bij status evaluating of actionCount >= maxActions is host-side alleen afronden of stoppen met een concrete resultaattekst beschikbaar: kies geen nieuwe inhoudelijke stap, update of wachtlus. Een inhoudelijke toolstap verhoogt na uitvoering actionCount en revision: zet daarom geen manage-action met de oude snapshotrevisie erachter; de volgende exploration-attention beoordeelt die verse toestand. Bind iedere action sourceSpans uitsluitend aan de letterlijke positieve actiezin aan het einde van turn.text; neem aanleiding, snapshotdata en optionele keuzemeta niet mee. Denied/mention-stappen worden nooit uitgevoerd.'
      : selfAttention.kind === 'goal'
        ? 'De context bevat exact hostgeattesteerde selfAttention voor één actieve zelfgekozen doelreview. Kies hoogstens één goal.portfolio.manage-bewerking uit update, pause, complete of abandon. Lever de tijdelijke opaque doel_ref plus verwachte_revisie zelf exact uit de aangeboden schema-enums; bij update lever je ook een geldig ISO volgende_review op of na het geattesteerde claimmoment. De host vult geen action, argument of sourceSpan in. Bind iedere action sourceSpans uitsluitend aan de letterlijke positieve actiezin aan het einde van turn.text; neem aanleiding en optionele keuzemeta niet mee. Denied/mention-stappen worden nooit uitgevoerd.'
        : `De context bevat exact hostgeattesteerde verse aspiration-selfAttention. Dit is eigen aandacht, geen bewonersopdracht: zonder exploration-start blijft het plan durability turn met exact één inhoudelijke read-, discovery- of workspace-action. Een nieuwe duurzame explorationcyclus heeft uitsluitend deze exacte vorm: durability episode, temporal now, delivery none of silent/none, geen conditions, monitoring, work-actie of andere action; r1 is één volledig modelgeleverde exploration.manage:start en r2 is exact één huidige, volledig opgeloste read-, discovery- of workspace-action zonder deferred support. R1 staat vóór r2 en bevat vraag, aanleiding, hypothese, verwachte_waarneming, stopregel, max_acties, onderwerp_sleutel en onderwerp_ankers.${selfAttention.kind === 'aspiration' ? ` Vul daarbij aspiratie_id exact met ${selfAttention.id}.` : ''} De host verzint die action, het onderwerp en de velden nooit. Iedere andere combinatie blijft durability turn of vereist haar eigen gewone supportsDeferred-anker. Bind iedere action sourceSpans uitsluitend aan de letterlijke positieve actiezin aan het einde van turn.text; neem aanleiding en optionele keuzemeta niet mee. Denied/mention-stappen worden nooit uitgevoerd.`
    : '';
  const modelFields = envelope.context.signalBatch ? [...MODEL_FIELDS, 'signalAssessment'] : MODEL_FIELDS;
  const interpreter = [
    'Eén beurt; roep commit_turn_understanding eenmaal aan.',
    `Exacte velden: ${modelFields.join(', ')}; geen andere velden.`,
    'activeGoal-vervolg/reparatie is continue/correct; nieuw doel is command. Gebruik alleen artifactReceipts-paden.',
    'Conditions autoriseren niet. Mutatie vereist role=action met dezelfde letterlijke action-bronspan; een guard volstaat niet.',
    'Uitvoeringen zijn chronologische actions; requestedEffects klopt ermee. Spans zijn UTF-16 met eigen polarity.',
    'Annuleer/stop/pauzeer/verwijder/sluit als bevel is polarity request voor die beheeroperatie; polarity deny betekent uitsluitend dat de gebruiker vraagt de operatie niet uit te voeren.',
    'Lees natuurlijke vraagvormen semantisch: "wel of niet", "moet ik X doen?", "is X verstandig?" en "zou ik X kopen?" vragen om een beoordeling, niet om X uit te voeren of te ontkennen. Een bewijsgrens zoals "ga niet op één alias af", "noem alleen wat de bron draagt" of "maak niets" is een condition voor je redenering of antwoord, geen aparte denied action. Kies voor een actuele vraag alleen de concrete aangeboden read/discovery-action en selecteer die in firstDecision.',
    monitoringInstruction,
    continuityInstruction,
    signalInstruction,
    historicalInstruction,
    priorHomeResultInstruction,
    selfAttentionInstruction,
    'sourceBound=1: gebruik een punctuation-exacte waarde uit de huidige actionspan of exact één passende typed receipt; laat de actionspan zelf dus ook het genoemde id/pad/doel omvatten. Deferred+rebindable mag na discovery <resultaat> zijn.',
    'iterative=1: proof/recovery-siblings zijn aparte artefacten; de driver maakt, test, draait, leest en herstelt.',
    'proofNeeds alleen bij extra bewijs; firstDecision is hint, de host kiest alle geldige actions.',
    'firstDecision.requirementIds bevat uitsluitend actions met polarity request en authorization allowed; neem deny- of mention-actions nooit op.',
    'Bij firstDecision reply of clarify schrijf je verplicht zelf een niet-lege natuurlijke replyDraft; de host vult ontbrekende antwoordtekst nooit aan.',
    'memory.fact.write mag alleen zelfstandig leesbare inhoud bewaren: geen zwevende laatste/relevante/dit/dat/deze/die-verwijzing zonder concrete onderwerp-, bericht-, foto- of datumbinding. Antwoord of vraag één concrete verduidelijking als die binding ontbreekt.',
    'turn is nu; episode is latere persistent hervatting. Deadline is geen uitstel; startSourceSpans is [] bij now/none en alleen de letterlijke starttrigger bij later/condition.',
    'Nieuw artefact: kort pad met extensie; zoek/lijst een onbekend bestaand pad eerst.',
    fileArgumentInstruction,
    envelope.origin === 'resume'
      ? 'Bij resume zijn work en episode hostgebonden: kies de volgende materiële actie; geen work-update alleen om durability. De host checkpoint na het receipt.' : '',
    voiceReplyInstruction,
    `Poging ${attempt}.${retryInstruction}${decisionRetryInstruction}`,
  ].filter(Boolean).join(' ');
  return {
    instructions: [identity, interpreter].filter(Boolean).join('\n\n'),
    input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: payload }] }],
    tools: [responseTool(operations, envelope, selfAttention, monitoring)],
    toolChoice: 'required',
    timeoutMs,
  };
}
function failClosed(envelope, operations, now, failure) {
  const understanding = finalizedUnderstanding({
    actions: [], requestedEffects: [], deniedEffects: [...MUTATING_EFFECTS],
    temporal: { mode: 'now' }, durability: 'turn', delivery: { mode: 'none', target: 'none' },
    proofNeeds: [], ambiguities: [failure.code],
    firstDecision: { kind: 'fail_closed', requirementIds: [], replyDraft: null },
  }, envelope, operations, now, 'fail_closed');
  return deepFreeze({ ok: false, source: 'fail_closed', attempts: failure.attempts, understanding,
    firstDecision: understanding.firstDecision,
    failure: deepFreeze({ contract: 'TurnUnderstandingFailure/v1', ...failure }),
  });
}

function interpreterFailureEvidence(error) {
  const numericStatus = Number(error?.status);
  const providerStatus = Number.isInteger(numericStatus)
    && numericStatus >= 100 && numericStatus <= 599 ? numericStatus : null;
  const message = String(error?.message || '');
  const code = String(error?.code || '');
  const failureClass = providerStatus === 429
    ? 'rate_limited'
    : [401, 403].includes(providerStatus)
      ? 'authentication'
      : [400, 422].includes(providerStatus)
        ? 'request_contract'
        : /timeout|abort|tijdslimiet|timed out/iu.test(`${code} ${message}`)
          ? 'timeout'
          : providerStatus != null && providerStatus >= 500
            ? 'provider_error'
            : 'connection_error';
  const numericProviderAttempts = Number(error?.providerAttempts);
  const providerAttempts = Number.isSafeInteger(numericProviderAttempts)
    && numericProviderAttempts >= 1 && numericProviderAttempts <= 8
    ? numericProviderAttempts : null;
  return {
    failureClass,
    ...(providerStatus == null ? {} : { providerStatus }),
    ...(providerAttempts == null ? {} : { providerAttempts }),
  };
}

function assertSelfAttentionInterpreterDeadline(selfAttention, deadline) {
  if (!selfAttention || Date.now() < deadline) return;
  throw Object.assign(new Error('Self-attention interpreter timeout.'), {
    code: 'EVA_SELF_ATTENTION_INTERPRETER_TIMEOUT',
  });
}

/**
 * @param {object} envelopeValue
 * @param {{complete:Function, capabilityCatalog?:ReadonlyArray<object>, trustedResourceReceipts?:ReadonlyArray<object>, trustedConversationWorkReceipts?:ReadonlyArray<object>, trustedConversationId?:string, trustedActiveWorkId?:number, trustedSelfAttentionReceipt?:object, clock?:Function, voiceTargets?:ReadonlyArray<string|{alias:string}>, voiceHomeGrounding?:object, timeoutMs?:number, maxAttempts?:number, identityInstructions?:string}} dependencies
 */
export async function understandTurn(envelopeValue, dependencies = /** @type {any} */ ({})) {
  const fallback = () => createTurnEnvelope({
    turnId: 'invalid:turn', text: '[ongeldige beurtinvoer]', channel: 'internal', origin: 'user',
  });
  let envelope;
  try { envelope = createTurnEnvelope(envelopeValue); } catch {
    return failClosed(fallback(), new Map(), new Date(0),
      { code: 'invalid_turn_envelope', attempts: 0 });
  }
  const deps = plainObject(dependencies) ? dependencies : {};
  let operations, now, resourceReceipts, conversationWorkReceipts, selfAttention;
  try {
    operations = projectActiveWorkOperations(projectSignalOperations(
      normalizeCatalog(deps.capabilityCatalog || []), envelope.context.signalBatch,
    ), deps.trustedActiveWorkId);
    const clockValue = typeof deps.clock === 'function' ? deps.clock() : Date.now();
    now = clockValue instanceof Date ? clockValue : new Date(clockValue);
    if (!Number.isFinite(now.getTime())) throw new TypeError('invalid_clock');
    resourceReceipts = deepFreeze(normalizeResourceReceipts(deps.trustedResourceReceipts));
    conversationWorkReceipts = deepFreeze(normalizeConversationWorkReceipts(
      deps.trustedConversationWorkReceipts, deps.trustedConversationId,
      envelope.context.requester,
    ));
    selfAttention = deps.trustedSelfAttentionReceipt == null ? null
      : verifiedAutonomySelfAttentionReceipt(deps.trustedSelfAttentionReceipt, {
        turnText: envelope.message.text, origin: envelope.origin, channel: envelope.channel,
        now,
      });
    if (deps.trustedSelfAttentionReceipt != null && !selfAttention) {
      throw new TypeError('invalid_self_attention_receipt');
    }
    operations = projectSelfAttentionExplorationOperations(
      projectSelfAttentionGoalOperations(operations, selfAttention), selfAttention,
    );
  } catch (error) {
    return failClosed(envelope, new Map(), new Date(0),
      { code: error?.message === 'unsupported_signal_scope'
        ? 'unsupported_signal_scope' : 'invalid_interpreter_dependencies', attempts: 0 });
  }
  const boundary = effectBoundary(envelope.message.text);
  const monitoring = deepFreeze(requesterObservationSpec(envelope, monitoringRequestSpec({
    text: envelope.message.text,
    recentThreadRows: envelope.context.dialogueExcerpts.map((row) => ({
      content: row.text, ts: row.ts,
    })),
    requester: envelope.context.requester || '', now: now.getTime(), boundary,
  })));
  const targets = (Array.isArray(deps.voiceTargets) ? deps.voiceTargets : [])
    .map((item) => String(typeof item === 'string' ? item : item?.alias || '').trim()).filter(Boolean);
  let homeFastlane;
  try {
    homeFastlane = contextualHomeFastlane(
      envelope, operations, deps.voiceHomeGrounding, now,
    );
  } catch {
    return failClosed(envelope, operations, now, { code: 'fastlane_invalid', attempts: 0 });
  }
  if (homeFastlane) return deepFreeze({ ok: true, source: homeFastlane.source.kind, attempts: 0,
    understanding: homeFastlane, firstDecision: homeFastlane.firstDecision, failure: null });
  let fast;
  try { fast = voiceFastlane(envelope, operations, targets, now); } catch {
    return failClosed(envelope, operations, now, { code: 'fastlane_invalid', attempts: 0 });
  }
  if (fast) return deepFreeze({ ok: true, source: fast.source.kind, attempts: 0,
    understanding: fast, firstDecision: fast.firstDecision, failure: null });
  if (typeof deps.complete !== 'function') return failClosed(envelope, operations, now,
    { code: 'interpreter_unavailable', attempts: 0 });
  // Een selfAttention-object bestaat hier uitsluitend na verificatie van het
  // one-shot schedulerreceipt hierboven. Daardoor deelt ook geattesteerde
  // scheduler-aandacht veilig het bestaande self-plafond van maximaal 90s;
  // overige origins houden hun interactieve plafond.
  const timeoutCeilingMs = envelope.origin === 'self' ? 90_000 : 60_000;
  const timeoutMs = Math.max(250,
    Math.min(timeoutCeilingMs, Number(deps.timeoutMs) || 12_000));
  const requestedAttempts = Math.trunc(Number(deps.maxAttempts));
  const configuredMaxAttempts = Number.isFinite(requestedAttempts)
    ? Math.max(1, Math.min(3, requestedAttempts)) : 2;
  const maxAttempts = selfAttention ? 1 : configuredMaxAttempts;
  const identityInstructions = String(deps.identityInstructions || '').trim().slice(0, 12_000);
  let lastCode = 'interpreter_malformed';
  let lastValidation = null;
  let attempts = 0;
  const attemptFailures = [];
  const validationHistory = [];
  let decisionRetryGuide = null;
  let lastProviderEvidence = null;
  const deadline = Date.now() + timeoutMs;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) {
      lastCode = 'interpreter_timeout';
      break;
    }
    attempts = attempt;
    let response;
    try {
      response = await deps.complete(modelRequest(
        envelope, operations, monitoring, attempt, remainingMs, identityInstructions,
        { validations: validationHistory, decisionGuide: decisionRetryGuide },
        resourceReceipts, conversationWorkReceipts, selfAttention,
      ));
      assertSelfAttentionInterpreterDeadline(selfAttention, deadline);
    } catch (error) {
      lastCode = /timeout|abort|tijdslimiet|timed out/i.test(String(error?.message || error))
        ? 'interpreter_timeout' : 'interpreter_unavailable';
      lastValidation = null;
      lastProviderEvidence = interpreterFailureEvidence(error);
      attemptFailures.push({ attempt, code: lastCode, ...lastProviderEvidence });
      break;
    }
    try {
      const understanding = finalizedUnderstanding(modelPayload(response), envelope, operations, now,
        'model', { monitoring, boundary, resourceReceipts, conversationWorkReceipts, selfAttention,
        });
      return deepFreeze({ ok: true, source: 'model', attempts: attempt,
        understanding, firstDecision: understanding.firstDecision, failure: null });
    } catch (error) {
      lastCode = 'interpreter_malformed';
      lastValidation = String(error?.message || error || 'invalid_model_payload')
        .replace(/[^a-z0-9_.:[\]-]/giu, '_').slice(0, 180);
      if (!validationHistory.includes(lastValidation)) validationHistory.push(lastValidation);
      const actionAudit = SELF_ATTENTION_VALIDATION_AUDITS.get(error);
      const nextDecisionRetryGuide = TURN_DECISION_RETRY_GUIDES.get(error);
      decisionRetryGuide = nextDecisionRetryGuide || null;
      attemptFailures.push({ attempt, code: lastCode, validation: lastValidation,
        ...(actionAudit ? { actionAudit } : {}),
        ...(nextDecisionRetryGuide ? { decisionRequirements: nextDecisionRetryGuide.requirements } : {}) });
      if (lastValidation === 'self_attention_no_allowed_action') break;
    }
  }
  const decisionRetrySummary = turnDecisionRetrySummary(decisionRetryGuide);
  return failClosed(envelope, operations, now, {
    code: lastCode, attempts,
    attemptFailures,
    ...(lastProviderEvidence || {}),
    ...(validationHistory.length ? { validationHistory } : {}),
    ...(decisionRetrySummary ? { decisionRetrySummary } : {}),
    ...(lastCode === 'interpreter_malformed' && lastValidation
      ? { validation: lastValidation } : {}),
  });
}

function exactObjectIdentityList(left, right) {
  return Array.isArray(left) && Array.isArray(right) && left.length === right.length
    && left.every((item, index) => item === right[index]);
}

function freeAutonomyPlanMatchesUnderstanding(plan, understanding) {
  const selected = new Set(understanding?.firstDecision?.requirementIds || []);
  const action = understanding?.actions?.find((item) => (
    item.requirementId === plan?.requirementId
  ));
  return Boolean(plan && Object.isFrozen(plan) && Object.isFrozen(plan.args)
    && selected.has(plan.requirementId)
    && ['read', 'discovery'].includes(plan.effect)
    && plan.supportsDeferred === false && plan.execution?.mode === 'direct'
    && !hasUnresolvedReference(plan.args)
    && action?.capabilityId === plan.capabilityId
    && action?.operationId === plan.operationId
    && action?.authorization === 'allowed' && action?.polarity === 'request');
}

function exactFreeAutonomyBasisReceipt(firstReceipt, firstPlan, understanding) {
  const binding = firstReceipt?.semanticBinding;
  return firstReceipt?.name === firstPlan?.toolName
    && firstReceipt?.args === firstPlan?.args
    && verifiedCompleteReceipt(firstReceipt) && firstReceipt.changed !== true
    && firstReceipt.effect_uncertain !== true
    && binding?.understandingSha256 === understanding?.sha256
    && binding?.requirementId === firstPlan?.requirementId
    && canonicalJson(binding?.addressesRequirementIds || [])
      === canonicalJson([firstPlan?.requirementId])
    && canonicalJson(binding?.basisReceiptIds || []) === canonicalJson([])
    && binding?.capabilityId === firstPlan?.capabilityId
    && binding?.operationId === firstPlan?.operationId
    && binding?.role === 'primary';
}

function freeAutonomyContinuationContext({
  understanding, firstPlan, firstReceipt, remainingPlans,
  turnId, schedulerEpoch, messageContext, basisReceiptId,
} = /** @type {any} */ ({})) {
  if (!isIssuedTurnUnderstanding(understanding) || !Object.isFrozen(understanding)
    || understanding.source?.origin !== 'self' || understanding.source?.turnId !== turnId
    || !Number.isSafeInteger(schedulerEpoch) || schedulerEpoch < 0
    || !messageContext || typeof messageContext !== 'object'
    || !/^s[1-9][0-9]*$/u.test(String(basisReceiptId || ''))
    || !Object.isFrozen(remainingPlans) || remainingPlans.length < 1
    || remainingPlans.length > 11 || !freeAutonomyPlanMatchesUnderstanding(firstPlan, understanding)
    || !exactFreeAutonomyBasisReceipt(firstReceipt, firstPlan, understanding)
    || !remainingPlans.every((plan) => freeAutonomyPlanMatchesUnderstanding(plan, understanding))
    || new Set([firstPlan, ...remainingPlans]).size !== remainingPlans.length + 1
    || new Set(remainingPlans.map((plan) => plan.requirementId)).size !== remainingPlans.length) {
    return null;
  }
  const operationIds = new Set(remainingPlans.map((plan) => (
    `${plan.capabilityId}\u0000${plan.operationId}`
  )));
  return operationIds.size ? { understanding, firstPlan, firstReceipt, remainingPlans,
    turnId, schedulerEpoch, messageContext, basisReceiptId } : null;
}

function freeAutonomyStartSchema(operation) {
  if (operation?.capabilityId !== 'exploration.manage' || operation.operationId !== 'start'
    || operation.effect !== 'persistent' || operation.supportsDeferred !== false
    || operation.execution?.mode !== 'direct') return null;
  const properties = operation.input?.properties || {};
  if (FREE_AUTONOMY_EXPLORATION_FIELDS.some((field) => !plainObject(properties[field]))) {
    return null;
  }
  return {
    type: 'object', additionalProperties: false,
    properties: Object.fromEntries(FREE_AUTONOMY_EXPLORATION_FIELDS.map((field) => (
      [field, structuredClone(properties[field])]
    ))),
    required: [...FREE_AUTONOMY_EXPLORATION_FIELDS],
  };
}

function freeAutonomyContinuationTool(startSchema, remainingRequirementIds) {
  const object = (properties) => ({
    type: 'object', additionalProperties: false, properties, required: Object.keys(properties),
  });
  return {
    type: 'function', name: 'commit_free_autonomy_continuation', strict: true,
    description: 'Kies zelf of deze vrije waarneming een begrensde verkenning wordt, op zichzelf blijft staan of stil eindigt.',
    parameters: object({
      choice: { type: 'string', enum: [...FREE_AUTONOMY_CONTINUATION_CHOICES] },
      remainingRequirementIds: {
        type: 'array', maxItems: 1,
        items: { type: 'string', enum: remainingRequirementIds },
      },
      action: {
        type: ['object', 'null'], additionalProperties: false,
        properties: {
          requirementId: { type: 'string', enum: ['continuation_exploration_start'] },
          capabilityId: { type: 'string', enum: ['exploration.manage'] },
          operationId: { type: 'string', enum: ['start'] },
          semanticInput: startSchema,
        },
        required: ['requirementId', 'capabilityId', 'operationId', 'semanticInput'],
      },
    }),
  };
}

function freeAutonomyContinuationPayload(response) {
  const calls = Array.isArray(response?.toolCalls) ? response.toolCalls
    : Array.isArray(response?.output)
      ? response.output.filter((item) => item?.type === 'function_call') : [];
  if (calls.length !== 1
    || (calls[0].name ?? calls[0].function?.name) !== 'commit_free_autonomy_continuation') {
    throw new TypeError('missing_free_autonomy_continuation');
  }
  const raw = calls[0].arguments ?? calls[0].function?.arguments;
  return typeof raw === 'string' ? JSON.parse(raw) : raw;
}

function concreteExplorationStartInput(value, startSchema, selectedCount) {
  const input = jsonValue(value);
  if (!plainObject(input)
    || canonicalJson(Object.keys(input).sort())
      !== canonicalJson([...FREE_AUTONOMY_EXPLORATION_FIELDS])
    || jsonSchemaViolation(startSchema, input, 'freeAutonomyContinuation.semanticInput')
    || !Number.isInteger(input.max_acties) || input.max_acties < 2 || input.max_acties > 12
    || selectedCount < 1 || selectedCount + 1 > input.max_acties
    || String(input.onderwerp_sleutel || '').trim().length < 4
    || new Set((Array.isArray(input.onderwerp_ankers) ? input.onderwerp_ankers : [])
      .map((item) => String(item || '').trim()).filter((item) => item.length >= 4)).size < 2
    || ['vraag', 'aanleiding', 'hypothese', 'verwachte_waarneming', 'stopregel']
      .some((field) => String(input[field] || '').trim().length < 8)) return null;
  return deepFreeze(input);
}

function normalizedFreeAutonomyContinuation(raw, context, startSchema) {
  if (!plainObject(raw) || canonicalJson(Object.keys(raw).sort())
    !== canonicalJson(['action', 'choice', 'remainingRequirementIds'])) {
    throw new TypeError('invalid_free_autonomy_continuation');
  }
  const choice = String(raw.choice || '');
  if (!FREE_AUTONOMY_CONTINUATION_CHOICES.has(choice)) {
    throw new TypeError('invalid_free_autonomy_continuation_choice');
  }
  const allowed = new Map(context.remainingPlans.map((plan) => [plan.requirementId, plan]));
  const ids = [...new Set((Array.isArray(raw.remainingRequirementIds)
    ? raw.remainingRequirementIds : []).map((id) => String(id || '').trim()))];
  if (ids.some((id) => !allowed.has(id))) {
    throw new TypeError('free_autonomy_continuation_unknown_requirement');
  }
  if (choice !== 'start_exploration') {
    if (raw.action !== null || ids.length) {
      throw new TypeError('nonstarting_free_autonomy_continuation_has_effect');
    }
    return { choice, action: null, remainingRequirementIds: [] };
  }
  if (ids.length !== 1) {
    throw new TypeError('free_autonomy_continuation_requires_one_safe_read');
  }
  if (!plainObject(raw.action)
    || canonicalJson(Object.keys(raw.action).sort()) !== canonicalJson([
      'capabilityId', 'operationId', 'requirementId', 'semanticInput',
    ])
    || raw.action.requirementId !== 'continuation_exploration_start'
    || raw.action.capabilityId !== 'exploration.manage'
    || raw.action.operationId !== 'start') {
    throw new TypeError('invalid_free_autonomy_exploration_action');
  }
  const semanticInput = concreteExplorationStartInput(raw.action.semanticInput, startSchema, ids.length);
  if (!semanticInput) throw new TypeError('invalid_free_autonomy_exploration_input');
  return {
    choice,
    action: deepFreeze({
      requirementId: raw.action.requirementId,
      capabilityId: raw.action.capabilityId,
      operationId: raw.action.operationId,
      semanticInput, sourceSpans: [], polarity: 'request', authorization: 'allowed',
    }),
    remainingRequirementIds: ids,
  };
}

function freeAutonomyReceiptSha256(receipt) {
  return sha256(canonicalJson({
    name: receipt?.name || '', args: receipt?.args || null,
    output: String(receipt?.output || ''), semanticBinding: receipt?.semanticBinding || null,
  }));
}

/**
 * Geeft Eva na haar eerste echte vrije waarneming exact één nieuwe semantische
 * keuze. De modelresponse zelf is de bron van een eventuele exploration-start;
 * de oorspronkelijke algemene actiezin wordt daar bewust niet voor hergebruikt.
 */
export async function decideFreeAutonomyContinuation(
  options = /** @type {any} */ ({}),
  dependencies = /** @type {any} */ ({}),
) {
  const context = freeAutonomyContinuationContext(options);
  const deps = plainObject(dependencies) ? dependencies : {};
  if (!context || typeof deps.complete !== 'function') {
    return deepFreeze({ ok: false, failure: 'invalid_free_autonomy_continuation_context', decision: null });
  }
  let operations, startOperation, startSchema;
  try {
    operations = normalizeCatalog(deps.capabilityCatalog || []);
    startOperation = operations.get('exploration.manage\u0000start');
    startSchema = freeAutonomyStartSchema(startOperation);
    if (!startSchema) throw new TypeError('missing_free_autonomy_exploration_start');
  } catch (error) {
    return deepFreeze({ ok: false, failure: String(error?.message || error), decision: null });
  }
  const actions = new Map(context.understanding.actions.map((action) => (
    [action.requirementId, action]
  )));
  const remaining = context.remainingPlans.map((plan) => {
    const action = actions.get(plan.requirementId);
    return {
      requirementId: plan.requirementId,
      capabilityId: plan.capabilityId,
      operationId: plan.operationId,
      effect: plan.effect,
      semanticInput: action?.semanticInput || {},
    };
  });
  const evidence = String(options.evidenceText || '').replace(/\s+/gu, ' ').trim().slice(0, 4_000);
  const payload = JSON.stringify({
    contract: FREE_AUTONOMY_CONTINUATION_CONTRACT,
    original: {
      turnId: context.turnId,
      goal: context.understanding.goal,
      firstRequirementId: context.firstPlan.requirementId,
    },
    evidence: {
      receiptId: context.basisReceiptId, requirementId: context.firstPlan.requirementId,
      capabilityId: context.firstPlan.capabilityId,
      operationId: context.firstPlan.operationId,
      verified: true, summary: evidence,
    },
    remaining,
  });
  try {
    const response = await deps.complete({
      instructions: [String(deps.identityInstructions || '').trim().slice(0, 12_000),
        'Je bent dezelfde Eva, midden in exact dezelfde vrije aandachtbeurt. Je eerste echte waarneming staat in het receipt. Kies nu zelf exact één vervolg: start_exploration als je van de reeds door jou gekozen resterende read/discovery-stappen een toetsbare cyclus wilt maken; keep_observation als deze ene waarneming op zichzelf genoeg is; silent als je er nu niets van wilt maken.',
        'Alleen bij start_exploration lever jij zelf één volledige exploration.manage:start en een niet-lege subset van de aangeboden remainingRequirementIds. Kies geen nieuwe inhoudelijke actie: na de start kunnen uitsluitend die al eerder door jou gekozen reads lopen, begrensd door jouw max_acties en stopregel. Bij keep_observation of silent zijn action null en remainingRequirementIds leeg. De host verzint of herschrijft geen onderwerp, veld, actie of keuze.',
      ].filter(Boolean).join('\n\n'),
      input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: payload }] }],
      tools: [freeAutonomyContinuationTool(
        startSchema, remaining.map((item) => item.requirementId),
      )],
      toolChoice: 'required',
      timeoutMs: Math.max(250, Math.min(30_000, Number(deps.timeoutMs) || 20_000)),
    });
    const normalized = normalizedFreeAutonomyContinuation(
      freeAutonomyContinuationPayload(response), context, startSchema,
    );
    const source = {
      contract: FREE_AUTONOMY_CONTINUATION_CONTRACT,
      kind: 'model_continuation', turnId: context.turnId,
      understandingSha256: context.understanding.sha256,
      basisReceiptSha256: freeAutonomyReceiptSha256(context.firstReceipt),
      basisReceiptId: context.basisReceiptId,
      choice: normalized.choice,
      remainingRequirementIds: normalized.remainingRequirementIds,
      action: normalized.action,
    };
    const decision = deepFreeze({ ...source, sha256: sha256(canonicalJson(source)) });
    ISSUED_FREE_AUTONOMY_CONTINUATIONS.set(decision, Object.freeze({
      ...context, decision,
      selectedPlans: Object.freeze(normalized.remainingRequirementIds.map((id) => (
        context.remainingPlans.find((plan) => plan.requirementId === id)
      ))),
    }));
    return deepFreeze({ ok: true, failure: null, decision });
  } catch (error) {
    return deepFreeze({
      ok: false, failure: String(error?.message || error || 'invalid_free_autonomy_continuation').slice(0, 240),
      decision: null,
    });
  }
}

export function consumeIssuedFreeAutonomyContinuation(
  decision,
  context = /** @type {any} */ ({}),
) {
  const issued = decision && typeof decision === 'object'
    ? ISSUED_FREE_AUTONOMY_CONTINUATIONS.get(decision) : null;
  if (!issued || CONSUMED_FREE_AUTONOMY_CONTINUATIONS.has(decision)
    || issued.decision !== decision || issued.understanding !== context.understanding
    || issued.firstPlan !== context.firstPlan || issued.firstReceipt !== context.firstReceipt
    || issued.remainingPlans !== context.remainingPlans
    || issued.messageContext !== context.messageContext
    || issued.turnId !== context.turnId || issued.schedulerEpoch !== context.schedulerEpoch
    || issued.basisReceiptId !== context.basisReceiptId
    || context.turnCurrent !== true
    || !exactObjectIdentityList(issued.remainingPlans, context.remainingPlans)) return null;
  const { sha256: issuedSha256, ...source } = decision;
  if (issuedSha256 !== sha256(canonicalJson(source))) return null;
  CONSUMED_FREE_AUTONOMY_CONTINUATIONS.add(decision);
  return Object.freeze({
    decision,
    choice: decision.choice,
    action: decision.action,
    selectedPlans: issued.selectedPlans,
  });
}

const TURN_STEP_REASONS = new Set([
  'investigate', 'verify', 'retry', 'alternate', 'user_fact_required', 'external_block',
]);

function turnStepTool(openRequirementIds, evidenceIds) {
  return {
    type: 'function', name: 'commit_turn_step', strict: true,
    description: 'Kies exact één volgende waarnemingsstap binnen dezelfde onveranderde beurt.',
    parameters: {
      type: 'object', additionalProperties: false,
      properties: {
        kind: { type: 'string', enum: ['execute', 'complete', 'blocked'] },
        addressesRequirementIds: {
          type: 'array', maxItems: openRequirementIds.length,
          items: { type: 'string', enum: openRequirementIds },
        },
        basisReceiptIds: {
          type: 'array', maxItems: evidenceIds.length,
          items: evidenceIds.length ? { type: 'string', enum: evidenceIds } : { type: 'string' },
        },
        action: {
          type: ['object', 'null'], additionalProperties: false,
          properties: {
            capabilityId: { type: 'string', pattern: '^[a-z][a-z0-9_.:-]{0,119}$' },
            operationId: { type: 'string', pattern: '^[a-z][a-z0-9_.:-]{0,119}$' },
            semanticInput: { type: 'object', additionalProperties: true },
          },
          required: ['capabilityId', 'operationId', 'semanticInput'],
        },
        reasonCode: { type: 'string', enum: [...TURN_STEP_REASONS] },
      },
      required: ['kind', 'addressesRequirementIds', 'basisReceiptIds', 'action', 'reasonCode'],
    },
  };
}

function turnStepPayload(response) {
  const calls = Array.isArray(response?.toolCalls) ? response.toolCalls
    : Array.isArray(response?.output)
      ? response.output.filter((item) => item?.type === 'function_call') : [];
  if (calls.length !== 1
    || (calls[0].name ?? calls[0].function?.name) !== 'commit_turn_step') {
    throw new TypeError('missing_structured_turn_step');
  }
  const raw = calls[0].arguments ?? calls[0].function?.arguments;
  return typeof raw === 'string' ? JSON.parse(raw) : raw;
}

export function hasUnresolvedReference(value) {
  if (typeof value === 'string') {
    const text = value.trim();
    return /^<[^>]+>$/u.test(text)
      || /^\[[^\]]*(?:result|uitkomst|gevonden|receipt|stap)[^\]]*\]$/iu.test(text)
      || /^\{\{[^}]+\}\}$/u.test(text);
  }
  if (Array.isArray(value)) return value.some(hasUnresolvedReference);
  return plainObject(value) && Object.values(value).some(hasUnresolvedReference);
}

function stepReferenceKind(key) {
  if (/(?:^|_)(?:pad|path)$/iu.test(key)) return 'path';
  if (/(?:^|_)(?:entity|entity_id|entityid)$/iu.test(key)) return 'entity';
  if (/(?:^|_)target$/iu.test(key)) return 'target';
  return null;
}

function normalizeStepReferenceValue(kind, value) {
  const text = String(value || '').trim();
  return kind === 'path' ? text.replaceAll('\\', '/').replace(/^\.\//u, '') : text;
}

function normalizeStepCandidates(raw) {
  return (Array.isArray(raw) ? raw : []).slice(0, 40).flatMap((item) => {
    if (!plainObject(item) || !['path', 'entity', 'target'].includes(String(item.kind || ''))) return [];
    const kind = String(item.kind);
    const value = normalizeStepReferenceValue(kind, item.value);
    return value ? [{ kind, value: value.slice(0, 1_000) }] : [];
  });
}

function completedTurnStep(kind, reasonCode, addressesRequirementIds, basisReceiptIds, evidenceById) {
  if (!addressesRequirementIds.length || !basisReceiptIds.length) {
    throw new TypeError('incomplete_turn_step_completion');
  }
  for (const requirementId of addressesRequirementIds) {
    const supported = basisReceiptIds.some((id) => {
      const item = evidenceById.get(id);
      return item?.requirementId === requirementId && item.ok === true
        && item.verified === true && item.partial !== true
        && item.needsFollowup !== true && item.effectUncertain !== true;
    });
    if (!supported) throw new TypeError('turn_step_completion_lacks_verified_basis');
  }
  return deepFreeze({ kind, reasonCode, addressesRequirementIds, basisReceiptIds, action: null });
}

function executableTurnStep({
  raw, kind, reasonCode, addressesRequirementIds, basisReceiptIds,
  evidenceById, understanding, operations,
}) {
  if (addressesRequirementIds.length !== 1 || !basisReceiptIds.length || !plainObject(raw.action)) {
    throw new TypeError('incomplete_executable_turn_step');
  }
  const selectedRequirements = new Set(understanding.firstDecision.requirementIds);
  const usableBasis = basisReceiptIds
    .map((id) => evidenceById.get(id))
    .filter((item) => item && item.effectUncertain !== true
      && selectedRequirements.has(item.requirementId)
      && understanding.actions.some((action) => (
        action.requirementId === item.requirementId
        && action.capabilityId === item.capabilityId
        && action.operationId === item.operationId
      )));
  if (!usableBasis.length) throw new TypeError('turn_step_lacks_basis');
  const capabilityId = identifier(raw.action.capabilityId, 'step_capability_id');
  const operationId = identifier(raw.action.operationId, 'step_operation_id');
  const operation = operations.get(`${capabilityId}\u0000${operationId}`);
  if (!operation || !['read', 'discovery'].includes(operation.effect)) {
    throw new TypeError('turn_step_not_read_only');
  }
  const semanticInput = jsonValue(plainObject(raw.action.semanticInput)
    ? raw.action.semanticInput : {});
  if (hasUnresolvedReference(semanticInput)) throw new TypeError('unresolved_turn_step_reference');
  const violation = jsonSchemaViolation(operation.input, semanticInput, 'turnStep.semanticInput');
  if (violation) throw new TypeError(violation);
  const requirementId = addressesRequirementIds[0];
  const originalAction = understanding.actions.find((action) => action.requirementId === requirementId);
  if (!originalAction || !['read', 'discovery'].includes(originalAction.effect)) {
    throw new TypeError('turn_step_original_requirement_not_observational');
  }
  const originalOperation = operations.get(
    `${originalAction.capabilityId}\u0000${originalAction.operationId}`,
  );
  if (originalOperation?.freshnessClass === 'live' && operation.freshnessClass !== 'live') {
    throw new TypeError('turn_step_freshness_downgrade');
  }
  const sameOperation = originalAction.capabilityId === capabilityId
    && originalAction.operationId === operationId;
  if (sameOperation) {
    const rebindable = new Set(operation.rebindableFields);
    for (const field of new Set([...Object.keys(originalAction.semanticInput || {}),
      ...Object.keys(semanticInput)])) if (!rebindable.has(field)
        && canonicalJson(originalAction.semanticInput?.[field]) !== canonicalJson(semanticInput[field])) {
      throw new TypeError(`turn_step_changes_semantic_field:${field}`);
    }
  }
  let referenceFields = 0;
  for (const [key, value] of Object.entries(semanticInput)) {
    const referenceKind = stepReferenceKind(key);
    if (!referenceKind || typeof value !== 'string') continue;
    referenceFields += 1;
    const normalized = normalizeStepReferenceValue(referenceKind, value);
    const bound = usableBasis.some((item) => item.ok === true && item.verified === true
      && item.candidates.some((candidate) => (
        candidate.value === normalized
        && (candidate.kind === referenceKind
          || (referenceKind === 'target' && ['entity', 'target'].includes(candidate.kind)))
      )));
    if (sameOperation && !bound) throw new TypeError(`unbound_turn_step_field:${key}`);
  }
  if (sameOperation && referenceFields === 0
    && Object.keys(semanticInput).some((key) => stepReferenceKind(key))) {
    throw new TypeError('unbound_turn_step_reference');
  }
  return deepFreeze({
    kind, reasonCode, addressesRequirementIds, basisReceiptIds,
    action: {
      requirementId, capabilityId, operationId, semanticInput,
      effect: operation.effect, polarity: 'request', authorization: 'allowed', sourceSpans: [],
    },
  });
}

function normalizeTurnStep(raw, { understanding, openRequirementIds, evidence, operations }) {
  if (!plainObject(raw) || Object.keys(raw).some((key) => ![
    'kind', 'addressesRequirementIds', 'basisReceiptIds', 'action', 'reasonCode',
  ].includes(key))) throw new TypeError('invalid_turn_step');
  const kind = String(raw.kind || '');
  const reasonCode = String(raw.reasonCode || '');
  if (!['execute', 'complete', 'blocked'].includes(kind) || !TURN_STEP_REASONS.has(reasonCode)) {
    throw new TypeError('invalid_turn_step_decision');
  }
  const open = new Set(openRequirementIds);
  const addressesRequirementIds = [...new Set((Array.isArray(raw.addressesRequirementIds)
    ? raw.addressesRequirementIds : []).map((value) => String(value || '').trim()))];
  if (addressesRequirementIds.some((id) => !open.has(id))) {
    throw new TypeError('turn_step_unknown_requirement');
  }
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));
  const basisReceiptIds = [...new Set((Array.isArray(raw.basisReceiptIds)
    ? raw.basisReceiptIds : []).map((value) => String(value || '').trim()))];
  if (basisReceiptIds.some((id) => !evidenceById.has(id))) {
    throw new TypeError('turn_step_unknown_receipt');
  }
  if (kind === 'blocked') {
    if (raw.action != null || addressesRequirementIds.length) {
      throw new TypeError('blocked_turn_step_has_action');
    }
    return deepFreeze({ kind, reasonCode, addressesRequirementIds: [], basisReceiptIds, action: null });
  }
  if (kind === 'complete') {
    if (raw.action != null) throw new TypeError('incomplete_turn_step_completion');
    return completedTurnStep(
      kind, reasonCode, addressesRequirementIds, basisReceiptIds, evidenceById,
    );
  }
  return executableTurnStep({
    raw, kind, reasonCode, addressesRequirementIds, basisReceiptIds,
    evidenceById, understanding, operations,
  });
}

/**
 * Kiest na een echte route-uitkomst één volgende read/discovery-stap, bevestigt
 * dat het beschikbare bewijs voldoende is, of meldt een concrete blokkade.
 * De agentlus mag dit begrensd herhalen. Doel, mutatiebevoegdheid en antwoord
 * blijven onveranderlijk afkomstig uit dezelfde uitgegeven TurnUnderstanding.
 */
export async function decideTurnStep({
  understanding, sourceText = '', openRequirementIds = [], evidence = [],
} = /** @type {{
  understanding?: {
    firstDecision: { requirementIds: readonly unknown[] },
    goal?: unknown,
    conditions?: unknown,
    deniedEffects?: unknown,
  },
  sourceText?: string,
  openRequirementIds?: string[],
  evidence?: Array<{
    id?: unknown,
    requirementId?: unknown,
    capabilityId?: unknown,
    operationId?: unknown,
    ok?: unknown,
    verified?: unknown,
    failureClass?: unknown,
    partial?: unknown,
    needsFollowup?: unknown,
    effectUncertain?: unknown,
    candidates?: unknown,
    result?: unknown,
  }>,
}} */ ({}), dependencies = /** @type {any} */ ({})) {
  const deps = plainObject(dependencies) ? dependencies : {};
  if (!isIssuedTurnUnderstanding(understanding) || typeof deps.complete !== 'function') {
    return deepFreeze({ ok: false, failure: 'invalid_turn_step_context', decision: null });
  }
  let allOperations;
  try { allOperations = normalizeCatalog(deps.capabilityCatalog || []); } catch {
    return deepFreeze({ ok: false, failure: 'invalid_turn_step_catalog', decision: null });
  }
  const operations = new Map([...allOperations].filter(([, operation]) => (
    ['read', 'discovery'].includes(operation.effect)
  )));
  const selected = new Set(understanding.firstDecision.requirementIds);
  const open = [...new Set(openRequirementIds.map((value) => String(value || '').trim()))]
    .filter((id) => selected.has(id));
  const receipts = (Array.isArray(evidence) ? evidence : []).slice(-10).map((item, index) => ({
    id: /^s[1-9][0-9]*$/u.test(String(item?.id || '')) ? String(item.id) : `s${index + 1}`,
    requirementId: String(item?.requirementId || ''),
    capabilityId: String(item?.capabilityId || ''),
    operationId: String(item?.operationId || ''),
    ok: item?.ok === true,
    verified: item?.verified === true,
    failureClass: String(item?.failureClass || ''),
    partial: item?.partial === true,
    needsFollowup: item?.needsFollowup === true,
    effectUncertain: item?.effectUncertain === true,
    candidates: normalizeStepCandidates(item?.candidates),
    result: String(item?.result || '').slice(0, 4_000),
  }));
  if (!open.length || !receipts.length || !operations.size) {
    return deepFreeze({ ok: false, failure: 'empty_turn_step_context', decision: null });
  }
  const catalog = catalogPrompt(operations);
  const payload = JSON.stringify({
    original: {
      text: String(sourceText || '').slice(0, 4_000),
      goal: understanding.goal,
      conditions: understanding.conditions,
      openRequirementIds: open,
      deniedEffects: understanding.deniedEffects,
    },
    evidence: receipts,
    ...catalog,
  });
  const timeoutMs = Math.max(500, Math.min(30_000, Number(deps.timeoutMs) || 20_000));
  const identity = String(deps.identityInstructions || '').trim().slice(0, 12_000);
  let validation = '';
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const response = await deps.complete({
        instructions: [identity,
          'Je bent dezelfde Eva, midden in dezelfde beurt. Bekijk het oorspronkelijke doel en alle receipts. Kies complete wanneer volledige geverifieerde receipts de vraag werkelijk beantwoorden; kies execute voor exact één volgende concrete read/discovery-stap; kies blocked alleen als geen bruikbare waarnemingsroute meer bestaat. Je mag doel, open requirements, effectgrenzen of aflevering niet veranderen en nooit muteren.',
          'Je mag voor hetzelfde informatie-doel wisselen naar een andere read/discovery-capability. Noem de receipt(s) die jouw keuze of voltooiing dragen. Partial, needsFollowup, truncatie en een geslaagde API-call die de gevraagde inhoud niet bevat zijn nooit complete. Gebruik geen placeholder en herhaal niet exact dezelfde mislukte call.',
          validation ? `De vorige stap was ongeldig: ${validation}. Corrigeer alleen dat contractpunt.` : '',
        ].filter(Boolean).join('\n\n'),
        input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: payload }] }],
        tools: [turnStepTool(open, receipts.map((item) => item.id))],
        toolChoice: 'required', timeoutMs,
      });
      const decision = normalizeTurnStep(turnStepPayload(response), {
        understanding, openRequirementIds: open, evidence: receipts, operations,
      });
      return deepFreeze({ ok: true, attempts: attempt, decision, failure: null });
    } catch (error) {
      validation = String(error?.message || error || 'invalid_turn_step').slice(0, 180);
    }
  }
  return deepFreeze({ ok: false, attempts: 2, failure: validation || 'invalid_turn_step', decision: null });
}

const MONITORING_ENTITY = /^[a-z0-9_]+\.[a-z0-9_]+$/iu;
const MONITORING_MAX_BINDINGS = 8;

function monitoringActionData(tool) {
  try { return JSON.parse(String(tool?.output || '')).data || null; } catch { return null; }
}

function monitoringEntityRequirementIds(monitoring = {}) {
  const explicit = Array.isArray(monitoring.sourceEntityReadRequirementIds)
    ? monitoring.sourceEntityReadRequirementIds : null;
  const raw = explicit || (Array.isArray(monitoring.sourceReadRequirementIds)
    ? monitoring.sourceReadRequirementIds : []);
  if (!raw.length || raw.length > MONITORING_MAX_BINDINGS) return [];
  const normalized = raw.map((id) => String(id));
  if (normalized.some((id) => !/^r(?:[1-9]|1[0-2])$/u.test(id))
    || new Set(normalized).size !== normalized.length) return [];
  return normalized;
}

function monitoringReadBindings(
  toolResults = [], monitoring = {}, issuanceContext = {}, maxAgeMs = 15 * 60_000,
) {
  const sourceUnderstandingSha256 = String(monitoring.sourceUnderstandingSha256 || '').trim();
  const requirementIds = new Set(monitoringEntityRequirementIds(monitoring));
  if (!requirementIds.size) return [];
  const bindings = new Map();
  const specs = new Map((Array.isArray(issuanceContext.factBindingSpecs)
    ? issuanceContext.factBindingSpecs : []).map((item) => [String(item?.requirementId || ''), item]));
  const add = (candidate, receipt) => {
    const entityId = String(candidate?.entity_id || candidate?.entityId || '').trim();
    const requirementId = String(candidate?.requirementId || '').trim();
    if (!MONITORING_ENTITY.test(entityId) || !/^r(?:[1-9]|1[0-2])$/u.test(requirementId)
      || bindings.has(entityId) || !specs.has(requirementId)) return;
    const spec = specs.get(requirementId);
    const name = String(candidate?.name || entityId).trim().slice(0, 200);
    const factBinding = issueMonitoringFactBinding({
      requirementId, entityId, name,
      condition: spec.condition, conditionRole: spec.conditionRole,
      operand: spec.operand, sourceText: spec.sourceText,
      sourceUnderstandingSha256,
      freshness: { maxAgeMs }, receipt,
      verifyInitialReceipt: issuanceContext.verifyInitialReceipt,
    });
    if (!factBinding) return;
    bindings.set(entityId, { entityId, requirementId, name, factBinding });
  };
  for (const tool of toolResults) {
    if (!verifiedCompleteReceipt(tool)
      || tool.semanticBinding?.understandingSha256 !== sourceUnderstandingSha256
      || !requirementIds.has(String(tool.semanticBinding?.requirementId || ''))) continue;
    const data = monitoringActionData(tool);
    if (tool.name === 'ha_lees' && tool.args?.entity_id) {
      add({ ...(plainObject(data) ? data : {}), entity_id: tool.args.entity_id,
        requirementId: tool.semanticBinding.requirementId }, tool);
    }
  }
  return [...bindings.values()];
}

export function createMonitoringContract(
  { monitoring = {}, args = {}, toolResults = [] } = /** @type {any} */ ({}),
  issuanceContext = /** @type {any} */ ({}),
) {
  const sourceUnderstandingSha256 = String(monitoring.sourceUnderstandingSha256 || '').trim();
  const wake = Date.parse(args.hervatten_na || '');
  if (!/^[a-f0-9]{64}$/u.test(sourceUnderstandingSha256) || !Number.isFinite(wake)) return null;
  const createdAtMs = Date.now();
  const hasEndsAt = monitoring.endsAt != null && String(monitoring.endsAt).trim() !== '';
  const requestedEndsAt = hasEndsAt ? Date.parse(monitoring.endsAt) : Number.NaN;
  if (hasEndsAt && (!Number.isFinite(requestedEndsAt) || requestedEndsAt <= createdAtMs)) return null;
  const endsAt = hasEndsAt ? new Date(requestedEndsAt).toISOString() : null;
  if (!Array.isArray(monitoring.conditions)
    || !monitoring.conditions.length || monitoring.conditions.length > 12) return null;
  const conditions = monitoring.conditions
    .map((item) => String(item?.statement || item || '').trim());
  if (conditions.some((condition) => !condition || condition.length > 500)) return null;
  const definitionOfDone = String(monitoring.definitionOfDone || '').trim();
  if (!definitionOfDone || definitionOfDone.length > 1_200) return null;
  const temporalCondition = monitoring.temporalCondition == null
    ? null : String(monitoring.temporalCondition).trim();
  if (monitoring.temporalCondition != null
    && (!temporalCondition || temporalCondition.length > 1_000)) return null;
  const sourceDiscoveryRequirementId = String(
    monitoring.sourceDiscoveryRequirementId || '',
  ).trim();
  const discovery = [...toolResults].reverse().find((tool) => tool.name === 'huis_status'
    && verifiedCompleteReceipt(tool)
    && tool.semanticBinding?.understandingSha256 === sourceUnderstandingSha256
    && (sourceDiscoveryRequirementId
      ? tool.semanticBinding?.requirementId === sourceDiscoveryRequirementId
      : Array.isArray(monitoring.sourceReadRequirementIds)
        && monitoring.sourceReadRequirementIds.includes(tool.semanticBinding?.requirementId)));
  if (sourceDiscoveryRequirementId && !discovery) return null;
  const discoveryQuery = discovery == null ? null : String(discovery.args?.vraag || '').trim();
  if (discovery && (!discoveryQuery || discoveryQuery.length > 500)) return null;
  const cadenceMs = Math.max(15_000, Math.min(
    24 * 60 * 60_000,
    wake - createdAtMs,
    endsAt ? Date.parse(endsAt) - createdAtMs : Number.POSITIVE_INFINITY,
  ));
  const entityRequirementIds = monitoringEntityRequirementIds(monitoring);
  const readBindings = monitoringReadBindings(
    toolResults, monitoring, issuanceContext,
    Math.max(15 * 60_000, Math.min(60 * 60_000, cadenceMs * 3)),
  );
  const boundRequirementIds = new Set(readBindings.map((binding) => binding.requirementId));
  if (!entityRequirementIds.length || readBindings.length !== entityRequirementIds.length
    || boundRequirementIds.size !== entityRequirementIds.length
    || entityRequirementIds.some((requirementId) => !boundRequirementIds.has(requirementId))) return null;
  return issueMonitoringContractV2({
    sourceUnderstandingSha256, conditions,
    temporalCondition,
    definitionOfDone,
    cadenceMs, endsAt,
    discoveryQuery,
    readBindings,
    createdAt: new Date(createdAtMs).toISOString(),
    requesterLabel: issuanceContext.requesterLabel,
    sourceTextSha256: issuanceContext.sourceTextSha256,
    deliveryRequired: monitoring.deliveryRequired === true,
    origin: monitoring.deliveryRequired === true ? issuanceContext.deliveryOrigin : null,
  });
}

export function normalizeMonitoringContract(value) {
  return normalizeMonitoringContractV2(value);
}

function monitoringOutcomeTool(factIds) {
  return {
    type: 'function', name: 'commit_monitoring_outcome', strict: true,
    description: 'Beoordeel verse, reeds uitgevoerde monitoringmetingen zonder zelf een handeling of ontvanger te kiezen.',
    parameters: {
      type: 'object', additionalProperties: false,
      properties: {
        verdict: { type: 'string', enum: ['notify', 'silent'] },
        terminal: { type: 'boolean' },
        factIds: {
          type: 'array', minItems: 1, maxItems: Math.max(1, factIds.length),
          items: factIds.length ? { type: 'string', enum: factIds } : { type: 'string' },
        },
      },
      required: ['verdict', 'terminal', 'factIds'],
    },
  };
}

function monitoringOutcomePayload(response) {
  const calls = Array.isArray(response?.toolCalls) ? response.toolCalls
    : Array.isArray(response?.output)
      ? response.output.filter((item) => item?.type === 'function_call') : [];
  if (calls.length !== 1
    || (calls[0].name ?? calls[0].function?.name) !== 'commit_monitoring_outcome') {
    throw new TypeError('missing_structured_monitoring_outcome');
  }
  const raw = calls[0].arguments ?? calls[0].function?.arguments;
  return typeof raw === 'string' ? JSON.parse(raw) : raw;
}

function normalizeMonitoringOutcome(raw, { facts }) {
  const decision = normalizeMonitoringOutcomeDecision(raw, facts);
  if (!decision) throw new TypeError('invalid_monitoring_outcome');
  return deepFreeze(decision);
}

/**
 * Laat dezelfde Eva na verse, hostgebonden reads uitsluitend betekenis geven aan
 * de uitkomst. Deze call kan geen capability, ontvanger, wake of work-id kiezen;
 * die blijven eigendom van het bestaande episodecontract en de Turn Kernel.
 */
export async function decideMonitoringOutcome({
  contract = null, facts = [],
} = {}, dependencies = /** @type {any} */ ({})) {
  const deps = plainObject(dependencies) ? dependencies : {};
  const normalizedContract = normalizeMonitoringContract(contract);
  const readBindings = monitoringContractReadBindings(normalizedContract) || [];
  const conditionContext = Array.isArray(deps.conditionContext)
    ? deps.conditionContext.map((condition) => ({
      statement: String(condition?.statement || ''), role: String(condition?.role || ''),
    })) : [];
  const conditionIds = conditionContext.map((condition) => (
    monitoringConditionId(condition.statement, condition.role)
  ));
  if (!normalizedContract
    || !readBindings.length || !Array.isArray(facts) || facts.length !== readBindings.length
    || !conditionContext.length || conditionIds.some((conditionId) => !conditionId)
    || canonicalJson(conditionIds) !== canonicalJson(contract.conditions)
    || typeof deps.complete !== 'function') {
    return deepFreeze({ ok: false, failure: 'invalid_monitoring_context', decision: null });
  }
  const factIds = facts.map((item) => item?.factId);
  const payload = JSON.stringify({
    monitoring: {
      conditions: conditionContext.map((condition) => condition.statement),
      temporalCondition: String(contract.temporalCondition || '').slice(0, 1_000) || null,
      definitionOfDone: String(contract.definitionOfDone || '').slice(0, 1_200) || null,
      observedEntities: facts.slice(0, 8).map((item) => ({
        entityId: String(item?.entityId || ''),
        name: String(item?.name || '').slice(0, 200) || null,
      })),
    },
    facts,
  });
  const timeoutMs = Math.max(500, Math.min(30_000, Number(deps.timeoutMs) || 20_000));
  const identity = String(deps.identityInstructions || '').trim().slice(0, 12_000);
  let validation = '';
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const response = await deps.complete({
        instructions: [
          identity,
          'Beoordeel uitsluitend de volledig geverifieerde facts tegenover de opgeslagen bedoeling. Kies notify alleen als de opgeslagen voorwaarde nu is bereikt; notify is terminaal. Kies anders silent en houd de observatie actief.',
          'Je kiest geen tekst, reden, waarde, eenheid, handeling, ontvanger, planning of meetbron. Neem alle factIds exact in de aangeboden volgorde over.',
          validation ? `De vorige uitkomst was ongeldig: ${validation}. Corrigeer alleen dat contractpunt.` : '',
        ].filter(Boolean).join('\n\n'),
        input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: payload }] }],
        tools: [monitoringOutcomeTool(factIds)],
        toolChoice: 'required', timeoutMs,
      });
      const decision = normalizeMonitoringOutcome(monitoringOutcomePayload(response), {
        facts,
      });
      return deepFreeze({ ok: true, attempts: attempt, decision, failure: null });
    } catch (error) {
      validation = String(error?.message || error || 'invalid_monitoring_outcome').slice(0, 180);
    }
  }
  return deepFreeze({
    ok: false, attempts: 2,
    failure: validation || 'invalid_monitoring_outcome', decision: null,
  });
}

/** Verzamelt precies de opgeslagen meetset en geeft alleen een bewijsbesluit terug. */
export async function runMonitoringCycle({ contract = null } = {}, dependencies = {}) {
  const deps = plainObject(dependencies) ? dependencies : {};
  const normalizedContract = normalizeMonitoringContract(contract);
  const readBindings = monitoringContractReadBindings(normalizedContract);
  if (!normalizedContract || !readBindings || typeof deps.observe !== 'function') {
    return deepFreeze({ kind: 'retry', observations: [], proof: '',
      failure: 'invalid_monitoring_cycle', decision: null });
  }
  const observations = [];
  for (const [index, binding] of readBindings.entries()) {
    let recorded = null;
    try { recorded = await deps.observe(binding, index); } catch { recorded = null; }
    if (recorded) observations.push(recorded);
    if (!verifiedCompleteReceipt(recorded)) break;
  }
  const complete = readBindings.length > 0
    && observations.length === readBindings.length
    && observations.every(verifiedCompleteReceipt);
  if (!complete) return deepFreeze({ kind: 'retry', observations, facts: [], proof: '',
    failure: 'incomplete_monitoring_observation', decision: null });
  const facts = observations.map((item, index) => buildMonitoringMeasurementFact(
    readBindings[index], item, {
      now: Date.now(),
      verifyLedgerReceipt: (receipt) => deps.verifyLedgerReceipt?.(
        receipt, readBindings[index], index,
      ),
      sourceUnderstandingSha256: contract.sourceUnderstandingSha256,
    },
  ));
  if (facts.some((fact) => !fact)
    || new Set(facts.map((fact) => fact.factId)).size !== facts.length) {
    return deepFreeze({ kind: 'retry', observations, facts: [], proof: '',
      failure: 'invalid_monitoring_measurement_fact', decision: null });
  }
  const proof = facts.map((fact) => (
    `${fact.name}=${fact.value}${fact.unit ? ` ${fact.unit}` : ''}`
  )).join('; ').slice(0, 4_000);
  const judged = await decideMonitoringOutcome({ contract, facts }, deps);
  if (!judged.ok || !judged.decision) return deepFreeze({ kind: 'retry', observations, facts, proof,
    failure: judged.failure || 'invalid_monitoring_outcome', decision: null });
  return deepFreeze({ kind: judged.decision.verdict, observations, facts, proof,
    failure: null, decision: judged.decision });
}
