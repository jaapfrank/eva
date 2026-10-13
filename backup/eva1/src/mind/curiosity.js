import { createHash } from 'node:crypto';
import {
  db, activeAspirations, logContributionOutcome, touchAspiration,
} from '../db.js';
import { bus } from '../bus.js';
import { config } from '../config.js';
import { redactSensitiveText } from '../security/redaction.js';
import {
  canonicalJson, dutchContentWords, verifiedCompleteReceipt,
} from '../util.js';
import {
  materializeExplorationEconomicHandoff,
  replayExplorationEconomicHandoff,
} from './portfolio.js';
import {
  behaviorProposalAttestation,
  materializeBehaviorExperiment,
  replayBehaviorExperiment,
} from './learning.js';
import { createIssuedExplorationAttention } from './selfattentioncontext.js';
import {
  commitIssuedExplorationTerminalMutation,
  releaseIssuedExplorationTerminalMutation,
  reserveIssuedExplorationTerminalMutation,
} from './understanding.js';

const ACTIVE = new Set(['chosen', 'running', 'waiting', 'evaluating']);
const TERMINAL = new Set(['completed', 'abandoned', 'inconclusive']);
const ISSUED_EXPLORATION_ATTENTION = new WeakMap();
const ISSUED_ASPIRATION_ATTENTION = new WeakMap();
const ISSUED_FREE_AUTONOMY_EXPLORATION_STARTS = new WeakMap();
const CONSUMED_FREE_AUTONOMY_EXPLORATION_STARTS = new WeakSet();
const FREE_AUTONOMY_EXPLORATION_START_CONTRACT = 'FreeAutonomyExplorationAtomicStart/v1';
const EXPLORATION_EVIDENCE_RECEIPT_CONTRACT = 'ExplorationEvidenceReceipt/v1';
const EXPLORATION_EVIDENCE_RECEIPT_VERIFIERS = [];
const FREE_AUTONOMY_EXPLORATION_FIELDS = [
  'aanleiding', 'hypothese', 'max_acties', 'onderwerp_ankers',
  'onderwerp_sleutel', 'stopregel', 'verwachte_waarneming', 'vraag',
];
const SCHEDULER_DEFERRED_TS = Symbol('scheduler_deferred_ts');
const EXPLORATION_RETRY_BACKOFF_MS = Math.max(
  (config.autonomyFull ? 5 : 30) * 60_000,
  config.autonomyIntervalMinutes * 60_000,
);
const DEFAULT_ASPIRATION_CADENCE_HOURS = Object.freeze({
  interest: 7 * 24,
  development: 24,
});
const nowIso = () => new Date().toISOString();
const clean = (value, limit = 2_000) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
const SLEEPING_CONSTRAINT_LIMIT = 4;

export function explorationEvidenceTopicSha256(exploration) {
  return createHash('sha256').update(canonicalJson({
    topicKey: clean(exploration?.topic_key, 120) || null,
    question: clean(exploration?.question, 1_200),
    expectedObservation: clean(exploration?.expected_observation, 1_200),
  })).digest('hex');
}

export function registerExplorationEvidenceReceiptVerifier(verifier) {
  if (typeof verifier !== 'function') throw new TypeError('exploration receipt verifier ontbreekt');
  if (!EXPLORATION_EVIDENCE_RECEIPT_VERIFIERS.includes(verifier)) {
    EXPLORATION_EVIDENCE_RECEIPT_VERIFIERS.push(verifier);
  }
}

function exactExplorationEvidenceReceipt(value) {
  const fields = [
    'argsSha256', 'capabilityId', 'contract', 'effect', 'episodeId', 'evidenceSha256',
    'explorationActionCount', 'explorationId', 'explorationRevision', 'observedAt',
    'operationId', 'outputSha256', 'receiptId', 'receiptSha256', 'requirementId',
    'tool', 'topicKey', 'topicSha256', 'understandingSha256',
  ];
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort()) !== canonicalJson(fields)
    || value.contract !== EXPLORATION_EVIDENCE_RECEIPT_CONTRACT
    || !Number.isSafeInteger(Number(value.explorationId)) || Number(value.explorationId) < 1
    || !Number.isSafeInteger(Number(value.explorationRevision)) || Number(value.explorationRevision) < 1
    || !Number.isSafeInteger(Number(value.explorationActionCount))
    || Number(value.explorationActionCount) < 0
    || !Number.isSafeInteger(Number(value.episodeId)) || Number(value.episodeId) < 1
    || !['read', 'discovery'].includes(String(value.effect || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.argsSha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.evidenceSha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.outputSha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.topicSha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.understandingSha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.receiptSha256 || ''))
    || !Number.isFinite(Date.parse(String(value.observedAt || '')))
    || !clean(value.tool, 80) || !clean(value.capabilityId, 120)
    || !clean(value.operationId, 120) || !clean(value.requirementId, 40)
    || (value.topicKey !== null && clean(value.topicKey, 120) !== value.topicKey)) return null;
  const { receiptId, receiptSha256, ...body } = value;
  const expectedSha256 = createHash('sha256').update(canonicalJson(body)).digest('hex');
  return receiptSha256 === expectedSha256
    && receiptId === `exploration-evidence:${expectedSha256}` ? value : null;
}

function claimExplorationEvidenceReceipt(current, options, expected) {
  for (const verifier of EXPLORATION_EVIDENCE_RECEIPT_VERIFIERS) {
    let claim = null;
    try { claim = verifier({ current, expected, options }); } catch { /* fail closed */ }
    const receipt = exactExplorationEvidenceReceipt(claim?.receipt);
    const summary = clean(redactSensitiveText(options?.evidence), 900);
    if (receipt
      && Number(receipt.explorationId) === Number(current.id)
      && Number(receipt.explorationRevision) === Number(current.revision)
      && Number(receipt.explorationActionCount) === Number(current.action_count)
      && Number(receipt.episodeId) === Number(options?.episodeId)
      && receipt.tool === clean(options?.tool, 80)
      && options?.ok === true && options?.verified === true
      && receipt.evidenceSha256
        === createHash('sha256').update(summary).digest('hex')
      && receipt.topicKey === (clean(current.topic_key, 120) || null)
      && receipt.topicSha256 === explorationEvidenceTopicSha256(current)
      && typeof claim.consume === 'function') return { receipt, consume: claim.consume };
  }
  return null;
}

function sleepingConstraintTextPart(value, limit) {
  return clean(redactSensitiveText(value), limit * 2)
    .replace(/(^|[\s("'`])\/(?!\/)[^\s"'`|,;]+/gu,
      '$1[AFGESCHERMD:PAD]')
    .replace(/(^|[\s("'`])(?:\.\.?\/)+[^\s"'`|,;]+/gu,
      '$1[AFGESCHERMD:PAD]')
    .replace(/\b(?:data\/workspace|workspace|runtime)\/[A-Za-z0-9._~!$&'()+,;=@%/-]+/giu,
      '[AFGESCHERMD:PAD]')
    .replace(/\b[A-Za-z]:\\[^\s"'`|,;]+/gu,
      '[AFGESCHERMD:PAD]')
    .replace(/(^|[\s("'`])\.(?:env|npmrc|netrc|gitconfig|dockerconfigjson)(?:\.[A-Za-z0-9_-]+)?\b/giu,
      '$1[AFGESCHERMD:PAD]')
    .replace(/\b[a-z][a-z0-9_.-]*-ref:[a-z0-9_-]{8,}\b/giu,
      '[AFGESCHERMD:INTERNE_VERWIJZING]')
    .replace(/#\d+\b/gu, '[AFGESCHERMD:INTERNE_VERWIJZING]')
    .replace(/\b(?:exploration|verkenning|database)[-_ ]?id\s*[:=#-]?\s*[a-z0-9_.:-]+/giu,
      '[AFGESCHERMD:INTERNE_VERWIJZING]')
    .slice(0, limit)
    .trim();
}
const isRelationshipExploration = (item) => ['self', 'conversation'].includes(String(item?.source || ''));
const TOPIC_FEATURE_VERSION = 9;
const ASTRONOMY_EVENTS = [
  ['perseids', /\bperse[iï]d\w*\b/i],
  ['geminids', /\bgeminid\w*\b/i],
  ['quadrantids', /\bquadrantid\w*\b/i],
  ['lyrids', /\blyrid\w*\b/i],
  ['leonids', /\bleonid\w*\b/i],
  ['orionids', /\borionid\w*\b/i],
  ['eta-aquariids', /\beta[ -]?aquariid\w*\b/i],
  ['delta-aquariids', /\bdelta[ -]?aquariid\w*\b/i],
  ['lunar-eclipse', /\b(?:maansverduister\w*|lunar[ -]?eclipse)\b/i],
  ['solar-eclipse', /\b(?:zonsverduister\w*|solar[ -]?eclipse)\b/i],
];

db.exec(`
CREATE TABLE IF NOT EXISTS eva_explorations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'self',
  source_episode_id INTEGER,
  aspiration_id INTEGER,
  topic_key TEXT,
  topic_anchors_json TEXT NOT NULL DEFAULT '[]',
  topic_features_json TEXT NOT NULL DEFAULT '{}',
  question TEXT NOT NULL,
  why_now TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  expected_observation TEXT NOT NULL,
  stop_rule TEXT NOT NULL,
  max_actions INTEGER NOT NULL DEFAULT 6,
  action_count INTEGER NOT NULL DEFAULT 0,
  empty_evidence_streak INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'chosen',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  result TEXT,
  decision TEXT,
  next_wake_ts TEXT,
  last_selected_ts TEXT,
  scheduler_deferred_ts TEXT,
  revision INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_eva_explorations_attention
  ON eva_explorations(status, next_wake_ts, updated_ts);
`);

try {
  const columns = new Set(db.prepare('PRAGMA table_info(eva_explorations)').all().map((column) => column.name));
  if (!columns.has('topic_key')) db.exec('ALTER TABLE eva_explorations ADD COLUMN topic_key TEXT');
  if (!columns.has('topic_anchors_json')) {
    db.exec("ALTER TABLE eva_explorations ADD COLUMN topic_anchors_json TEXT NOT NULL DEFAULT '[]'");
  }
  if (!columns.has('topic_features_json')) {
    db.exec("ALTER TABLE eva_explorations ADD COLUMN topic_features_json TEXT NOT NULL DEFAULT '{}'");
  }
  if (!columns.has('scheduler_deferred_ts')) {
    db.exec('ALTER TABLE eva_explorations ADD COLUMN scheduler_deferred_ts TEXT');
  }
} catch { /* oudere databases blijven startbaar; de normale migratie wordt bij volgende start herhaald */ }

function parseJson(value, fallback) {
  try { return JSON.parse(value); } catch { return fallback; }
}

function out(row) {
  if (!row) return null;
  const { scheduler_deferred_ts: schedulerDeferredTs, ...visibleRow } = row;
  const storedFeatures = parseJson(row.topic_features_json, null);
  const result = {
    ...visibleRow,
    evidence: parseJson(row.evidence_json, []),
    topic_anchors: parseJson(row.topic_anchors_json, []),
    topic_features: storedFeatures?.version === TOPIC_FEATURE_VERSION ? storedFeatures : deriveTopicFeatures(row),
    max_actions: Number(row.max_actions || 0),
    action_count: Number(row.action_count || 0),
    empty_evidence_streak: Number(row.empty_evidence_streak || 0),
    revision: Number(row.revision || 0),
  };
  Object.defineProperty(result, SCHEDULER_DEFERRED_TS, {
    value: schedulerDeferredTs ?? null,
  });
  return result;
}

export function explorationById(id) {
  return out(db.prepare('SELECT * FROM eva_explorations WHERE id=?').get(Number(id)));
}

export function listExplorations({ activeOnly = false, limit = 20, sources = null } = {}) {
  const wantedSources = Array.isArray(sources)
    ? sources.map((value) => clean(value, 80)).filter(Boolean)
    : [];
  const where = [
    activeOnly ? "status IN ('chosen','running','waiting','evaluating')" : null,
    wantedSources.length ? `source IN (${wantedSources.map(() => '?').join(',')})` : null,
  ].filter(Boolean);
  const rows = db.prepare(`SELECT * FROM eva_explorations${where.length ? ` WHERE ${where.join(' AND ')}` : ''}
    ORDER BY updated_ts DESC, id DESC LIMIT ?`).all(
    ...wantedSources, Math.max(1, Math.min(100, Number(limit) || 20)),
  );
  return rows.map(out);
}

export function startExploration({
  question,
  whyNow,
  hypothesis,
  expectedObservation,
  stopRule,
  maxActions = 6,
  sourceEpisodeId = null,
  aspirationId = null,
  source = 'self',
  supersedesExplorationId = null,
  newEvidence = null,
  newEvidenceVerified = false,
  topicKey = null,
  topicAnchors = null,
} = /** @type {any} */ ({})) {
  const required = [question, whyNow, hypothesis, expectedObservation, stopRule].map((value) => clean(value));
  if (required.some((value) => value.length < 8)) {
    return { ok: false, reason: 'vraag, aanleiding, hypothese, verwachte waarneming en stopregel moeten concreet zijn' };
  }
  const identity = explorationIdentity({
    topicKey, topicAnchors, question: required[0], hypothesis: required[2],
    expectedObservation: required[3], stopRule: required[4],
  });
  if (!identity.ok) return { ok: false, changed: false, reason: identity.reason };
  const candidate = {
    question: required[0], hypothesis: required[2], expected_observation: required[3],
    stop_rule: required[4], topic_key: identity.topicKey, topic_features: identity.topicFeatures,
  };
  if (supersedesExplorationId != null && String(supersedesExplorationId).trim() !== '') {
    const requestedPrior = explorationById(Number(supersedesExplorationId));
    const related = requestedPrior && requestedPrior.source === source && TERMINAL.has(requestedPrior.status)
      && (explorationSafetyConflict(requestedPrior, candidate) || sameExplorationTopic(requestedPrior, candidate));
    if (!related) {
      return {
        ok: false,
        changed: false,
        needs_followup: true,
        reason: `besluit #${clean(supersedesExplorationId, 40)} hoort inhoudelijk niet bij deze verkenning; kies geen ander besluit-id om de onderwerpcontrole te passeren`,
        prior_exploration: requestedPrior || null,
      };
    }
  }
  const recentTerminal = listExplorations({ limit: 60 }).find((item) => {
    if (item.source !== source || !TERMINAL.has(item.status)
      || Date.now() - Date.parse(item.updated_ts || '') > 24 * 3600_000) return false;
    return explorationSafetyConflict(item, candidate) || sameExplorationTopic(item, candidate);
  });
  if (recentTerminal) {
    const explicitlyReopened = Number(supersedesExplorationId) === Number(recentTerminal.id)
      && clean(newEvidence).length >= 8 && newEvidenceVerified === true;
    if (!explicitlyReopened) return {
      ok: false,
      changed: false,
      needs_followup: true,
      reason: `dit onderwerp kreeg recent al besluit #${recentTerminal.id}; heropen het alleen met dat id en concreet nieuw, geverifieerd toolbewijs uit deze beurt dat de eerdere drempel verandert`,
      prior_exploration: recentTerminal,
    };
    required[1] = clean(`${required[1]} Heropening van besluit #${recentTerminal.id} door nieuw bewijs: ${newEvidence}`);
  }
  const duplicate = listExplorations({ activeOnly: true, limit: 40 }).find((item) => (
    item.source === source && sameExplorationTopic(item, candidate)
  ));
  if (duplicate) return { ok: true, changed: false, exploration: duplicate, reason: 'dezelfde verkenning loopt al' };
  const ts = nowIso();
  const cap = Math.max(2, Math.min(12, Math.floor(Number(maxActions) || 6)));
  const info = db.prepare(`INSERT INTO eva_explorations
    (created_ts,updated_ts,source,source_episode_id,aspiration_id,topic_key,topic_anchors_json,topic_features_json,question,why_now,hypothesis,
     expected_observation,stop_rule,max_actions,status,last_selected_ts)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'chosen',?)`).run(
    ts, ts, clean(source, 80) || 'self', Number(sourceEpisodeId) || null,
    Number(aspirationId) || null, identity.topicKey, JSON.stringify(identity.topicAnchors),
    JSON.stringify(identity.topicFeatures), ...required, cap, ts,
  );
  if (Number(aspirationId)) touchAspiration(Number(aspirationId), `Eigen verkenning #${info.lastInsertRowid} gestart: ${required[0]}`);
  return { ok: true, changed: true, exploration: explorationById(info.lastInsertRowid) };
}

function freeAutonomyBasisReceiptSha256(receipt) {
  return createHash('sha256').update(canonicalJson({
    name: receipt?.name || '', args: receipt?.args || null,
    output: String(receipt?.output || ''), semanticBinding: receipt?.semanticBinding || null,
  })).digest('hex');
}

function exactFreeAutonomyBasisReceipt(plan, receipt, decision) {
  const semantic = receipt?.semanticBinding;
  return plan && Object.isFrozen(plan) && Object.isFrozen(plan.args)
    && ['read', 'discovery'].includes(plan.effect)
    && plan.execution?.mode === 'direct' && plan.supportsDeferred === false
    && !['verkenning_bijhouden', 'toon_werk', 'signalen_afhandelen', 'doel_bijhouden']
      .includes(plan.toolName)
    && !['exploration.read', 'exploration.manage', 'work.episode.read',
      'goal.portfolio.read', 'goal.portfolio.manage', 'signal.assess']
      .includes(plan.capabilityId)
    && receipt?.name === plan.toolName && receipt?.args === plan.args
    && verifiedCompleteReceipt(receipt) && receipt.changed !== true
    && receipt.partial !== true && receipt.needs_followup !== true
    && receipt.effect_uncertain !== true
    && semantic?.understandingSha256 === decision?.understandingSha256
    && semantic?.requirementId === plan.requirementId
    && canonicalJson(semantic?.addressesRequirementIds || [])
      === canonicalJson([plan.requirementId])
    && canonicalJson(semantic?.basisReceiptIds || []) === canonicalJson([])
    && semantic?.capabilityId === plan.capabilityId
    && semantic?.operationId === plan.operationId
    && semantic?.role === 'primary';
}

function exactFreeAutonomyContinuationDecision(decision, startArgs, basisReceiptId, basisReceipt) {
  if (!decision || !Object.isFrozen(decision)
    || decision.contract !== 'FreeAutonomyContinuation/v1'
    || decision.kind !== 'model_continuation'
    || decision.choice !== 'start_exploration'
    || !/^s[1-9][0-9]*$/u.test(String(basisReceiptId || ''))
    || decision.basisReceiptId !== basisReceiptId
    || decision.basisReceiptSha256 !== freeAutonomyBasisReceiptSha256(basisReceipt)
    || !/^[a-f0-9]{64}$/u.test(String(decision.understandingSha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(decision.sha256 || ''))) return false;
  const { sha256, ...source } = decision;
  if (sha256 !== createHash('sha256').update(canonicalJson(source)).digest('hex')) return false;
  const action = decision.action;
  const { bewerking, ...physicalInput } = startArgs || {};
  return action?.requirementId === 'continuation_exploration_start'
    && action.capabilityId === 'exploration.manage' && action.operationId === 'start'
    && action.authorization === 'allowed' && action.polarity === 'request'
    && Array.isArray(action.sourceSpans) && action.sourceSpans.length === 0
    && bewerking === 'start'
    && canonicalJson(Object.keys(physicalInput).sort())
      === canonicalJson(FREE_AUTONOMY_EXPLORATION_FIELDS)
    && canonicalJson(physicalInput) === canonicalJson(action.semanticInput)
    && Array.isArray(decision.remainingRequirementIds)
    && decision.remainingRequirementIds.length >= 1
    && new Set(decision.remainingRequirementIds).size === decision.remainingRequirementIds.length
    && Number.isInteger(physicalInput.max_acties)
    && decision.remainingRequirementIds.length + 1 <= physicalInput.max_acties;
}

/**
 * Geeft één opaque capability uit voor de modelgekozen exploration-start en
 * haar reeds uitgevoerde eerste waarneming. Alleen de atomische consumer mag
 * hiermee schrijven; objectidentiteiten blijven onderdeel van de binding.
 */
export function issueFreeAutonomyExplorationAtomicStart({
  decision, startArgs, basisPlan, basisReceipt, basisReceiptId, basisEvidence,
  sourceEpisodeId, turnId, afterInsert = null,
} = /** @type {any} */ ({})) {
  const evidence = clean(basisEvidence, 900);
  const episodeId = Number(sourceEpisodeId);
  const exact = Object.isFrozen(startArgs)
    && exactFreeAutonomyContinuationDecision(
      decision, startArgs, basisReceiptId, basisReceipt,
    )
    && decision.turnId === turnId
    && exactFreeAutonomyBasisReceipt(basisPlan, basisReceipt, decision)
    && evidence.length >= 8
    && (!clean(basisReceipt?.evidence?.summary, 900)
      || clean(basisReceipt.evidence.summary, 900) === evidence)
    && Number.isSafeInteger(episodeId) && episodeId >= 1
    && (afterInsert == null || typeof afterInsert === 'function');
  if (!exact) return null;
  const capability = Object.freeze({
    contract: FREE_AUTONOMY_EXPLORATION_START_CONTRACT,
    decisionSha256: decision.sha256,
    basisReceiptSha256: decision.basisReceiptSha256,
    basisReceiptId,
    sourceEpisodeId: episodeId,
    turnId,
  });
  ISSUED_FREE_AUTONOMY_EXPLORATION_STARTS.set(capability, Object.freeze({
    capability, decision, startArgs, basisPlan, basisReceipt, basisReceiptId,
    basisEvidence: evidence, sourceEpisodeId: episodeId, turnId, afterInsert,
  }));
  return capability;
}

function freeAutonomyAtomicStartFailure(reason) {
  return { ok: false, changed: false, verified: false, effect_uncertain: false,
    reason: clean(reason, 240) || 'de atomische exploration-start is geweigerd' };
}

/**
 * Schrijft de nieuwe exploration en boekt haar reeds bewezen basiswaarneming
 * in één SQLite-transactie. Een fout tussen beide mutaties rolt ook de insert
 * terug; er kan dus nooit een runnable action_count=0-restant ontstaan.
 */
export function startIssuedFreeAutonomyExplorationAtomic(capability, args) {
  const issued = capability && typeof capability === 'object'
    ? ISSUED_FREE_AUTONOMY_EXPLORATION_STARTS.get(capability) : null;
  if (!issued || issued.capability !== capability
    || CONSUMED_FREE_AUTONOMY_EXPLORATION_STARTS.has(capability)
    || issued.startArgs !== args
    || !exactFreeAutonomyContinuationDecision(
      issued.decision, args, issued.basisReceiptId, issued.basisReceipt,
    )
    || !exactFreeAutonomyBasisReceipt(
      issued.basisPlan, issued.basisReceipt, issued.decision,
    )) return freeAutonomyAtomicStartFailure('ongeldige of verbruikte startbinding');
  CONSUMED_FREE_AUTONOMY_EXPLORATION_STARTS.add(capability);
  const settle = db.transaction(() => {
    const episode = /** @type {any} */ (db.prepare(`SELECT id,correlation_id,status
      FROM cognitive_episodes WHERE id=?`).get(issued.sourceEpisodeId));
    if (!episode || Number(episode.id) !== issued.sourceEpisodeId
      || String(episode.correlation_id) !== issued.turnId
      || episode.status !== 'active') {
      throw new Error('de gebonden cognitieve episode is niet meer actief');
    }
    const started = startExploration(/** @type {any} */ ({
      question: args.vraag,
      whyNow: args.aanleiding,
      hypothesis: args.hypothese,
      expectedObservation: args.verwachte_waarneming,
      stopRule: args.stopregel,
      maxActions: args.max_acties,
      sourceEpisodeId: issued.sourceEpisodeId,
      aspirationId: null,
      source: 'self',
      topicKey: args.onderwerp_sleutel,
      topicAnchors: args.onderwerp_ankers,
    }));
    if (started.ok !== true || started.changed !== true
      || started.exploration?.source !== 'self'
      || Number(started.exploration?.source_episode_id) !== issued.sourceEpisodeId
      || started.exploration?.status !== 'chosen'
      || Number(started.exploration?.action_count) !== 0) {
      throw new Error('de exploration-insert is niet exact nieuw en verifieerbaar');
    }
    if (issued.afterInsert) issued.afterInsert(started.exploration);
    const counted = noteExplorationActionCas(started.exploration.id, /** @type {any} */ ({
      tool: issued.basisReceipt.name,
      ok: issued.basisReceipt.ok,
      verified: issued.basisReceipt.verified,
      evidence: issued.basisEvidence,
      episodeId: issued.sourceEpisodeId,
    }), {
      id: Number(started.exploration.id),
      revision: Number(started.exploration.revision),
      status: 'chosen', actionCount: 0,
      maxActions: Number(started.exploration.max_actions),
      question: String(started.exploration.question),
    });
    const settled = counted.exploration;
    const evidence = Array.isArray(settled?.evidence) ? settled.evidence : [];
    const basis = evidence.at(-1);
    if (counted.accepted !== true || settled?.status !== 'running'
      || Number(settled.action_count) !== 1
      || Number(settled.revision) !== Number(started.exploration.revision) + 1
      || evidence.length !== 1 || basis?.tool !== issued.basisReceipt.name
      || basis?.ok !== true || basis?.verified !== true
      || basis?.evidence !== issued.basisEvidence
      || Number(basis?.episode_id) !== issued.sourceEpisodeId) {
      throw new Error('de basiswaarneming kon niet atomisch worden geboekt');
    }
    return settled;
  });
  try {
    const exploration = settle();
    try {
      bus.emit('exploration_changed', {
        id: exploration.id, status: exploration.status,
        action_count: exploration.action_count, revision: exploration.revision,
      });
    } catch { /* domeinmelding volgt bewust pas na commit en mag die niet terugdraaien */ }
    return {
      ok: true, changed: true, verified: true, effect_uncertain: false,
      exploration,
      atomic_basis: {
        contract: FREE_AUTONOMY_EXPLORATION_START_CONTRACT,
        decision_sha256: issued.decision.sha256,
        receipt_sha256: issued.decision.basisReceiptSha256,
        receipt_id: issued.basisReceiptId,
        tool: issued.basisReceipt.name,
        episode_id: issued.sourceEpisodeId,
      },
    };
  } catch (error) {
    return freeAutonomyAtomicStartFailure(
      `exploration-start en basiswaarneming volledig teruggedraaid: ${error?.message || error}`,
    );
  }
}

export function updateExploration(id, {
  status = null,
  evidence = null,
  result = null,
  decision = null,
  nextWakeTs = null,
  expectedRevision = null,
} = {}) {
  const current = explorationById(id);
  if (!current) return { ok: false, reason: `verkenning #${id} bestaat niet` };
  if (expectedRevision != null && Number(expectedRevision) !== current.revision) {
    return { ok: false, changed: false,
      reason: `verkenning #${id} is intussen gewijzigd`, exploration: current };
  }
  const nextStatus = clean(status, 30) || current.status;
  if (![...ACTIVE, ...TERMINAL].includes(nextStatus)) return { ok: false, reason: `ongeldige status ${nextStatus}` };
  if (current.status === 'waiting' && current.next_wake_ts
    && Date.parse(current.next_wake_ts) > Date.now()
    && ['chosen', 'running', 'evaluating'].includes(nextStatus)) {
    return {
      ok: false,
      changed: false,
      reason: `verkenning #${current.id} slaapt tot ${current.next_wake_ts}; vóór die tijd kan zij alleen blijven wachten of bewust worden afgesloten`,
      exploration: current,
    };
  }
  if (TERMINAL.has(nextStatus) && clean(result).length < 8) {
    return { ok: false, reason: 'afronden vereist een concrete uitkomst of bewezen stopreden' };
  }
  const evidenceText = clean(evidence);
  const resultText = clean(result) || current.result;
  const decisionText = clean(decision) || current.decision;
  const evidenceRows = [...current.evidence];
  if (evidenceText) evidenceRows.push({ ts: nowIso(), text: clean(evidence, 1_500) });
  const wakeTs = nextStatus === 'waiting' ? Date.parse(nextWakeTs || '') : Number.NaN;
  if (nextStatus === 'waiting' && (!Number.isFinite(wakeTs) || wakeTs <= Date.now())) {
    return { ok: false, changed: false,
      reason: 'wachten vereist een geldig toekomstig hervatmoment', exploration: current };
  }
  const wake = nextStatus === 'waiting' ? new Date(wakeTs).toISOString() : null;
  if (nextStatus === current.status && !evidenceText
    && resultText === current.result && decisionText === current.decision
    && wake === (current.next_wake_ts ?? null)) {
    return { ok: true, changed: false, exploration: current,
      reason: 'de verkenning bevat geen inhoudelijke wijziging' };
  }
  const ts = nowIso();
  const updateSql = `UPDATE eva_explorations
    SET updated_ts=?,status=?,evidence_json=?,result=?,decision=?,next_wake_ts=?,
      last_selected_ts=?,scheduler_deferred_ts=NULL,revision=revision+1
    WHERE id=?${expectedRevision == null ? '' : `
      AND revision=? AND created_ts=? AND updated_ts=? AND source=? AND status=?
      AND source_episode_id IS ? AND aspiration_id IS ? AND question=?
      AND action_count=? AND max_actions=? AND empty_evidence_streak=?
      AND evidence_json=? AND result IS ? AND decision IS ? AND next_wake_ts IS ?`}`;
  const cursor = expectedRevision == null ? [] : [
    Number(expectedRevision), current.created_ts, current.updated_ts, current.source,
    current.status, current.source_episode_id, current.aspiration_id, current.question,
    current.action_count, current.max_actions, current.empty_evidence_streak,
    current.evidence_json, current.result, current.decision, current.next_wake_ts,
  ];
  const update = db.prepare(updateSql).run(
    ts, nextStatus, JSON.stringify(evidenceRows.slice(-30)), resultText,
    decisionText, wake, ts, Number(id), ...cursor,
  );
  if (update.changes !== 1) {
    const conflicted = explorationById(id);
    return { ok: false, changed: false,
      reason: `verkenning #${id} is intussen gewijzigd`, exploration: conflicted };
  }
  const updated = explorationById(id);
  if (updated.aspiration_id) touchAspiration(updated.aspiration_id, `${updated.status}: ${updated.result || updated.question}`);
  return { ok: true, changed: true, exploration: updated };
}

const TERMINAL_CONTRIBUTION_CONTRACT = 'ExplorationTerminalContribution/v1';
const EXPLORATION_ECONOMIC_HANDOFF_CONTRACT = 'ExplorationEconomicHandoff/v1';
const EXPLORATION_FAMILY_RESULT_CONTRACT = 'ExplorationFamilyResult/v1';
const DUPLICATE_FAMILY_RESULT_CODE = 'EVA_DUPLICATE_FAMILY_RESULT';

function terminalContributionOutcomeKey(explorationId, fromRevision, toRevision) {
  return createHash('sha256').update(canonicalJson({
    contract: TERMINAL_CONTRIBUTION_CONTRACT,
    explorationId, fromRevision, toRevision, terminalStatus: 'completed',
  })).digest('hex');
}

function exactTerminalSnapshot(current, snapshot) {
  const currentAspirationId = current?.aspiration_id == null
    ? null : Number(current.aspiration_id);
  return Boolean(current && snapshot
    && current.source === 'self'
    && (currentAspirationId === null
      || (Number.isSafeInteger(currentAspirationId) && currentAspirationId >= 1))
    && currentAspirationId === snapshot.aspirationId
    && Number(current.id) === Number(snapshot.id)
    && Number(current.revision) === Number(snapshot.revision)
    && String(current.status) === String(snapshot.status)
    && Number(current.action_count) === Number(snapshot.actionCount)
    && Number(current.max_actions) === Number(snapshot.maxActions)
    && String(current.question) === String(snapshot.question)
    && String(current.hypothesis) === String(snapshot.hypothesis)
    && String(current.expected_observation) === String(snapshot.expectedObservation)
    && String(current.stop_rule) === String(snapshot.stopRule)
    && (current.decision ?? null) === (snapshot.decision ?? null));
}

function terminalEvidenceReceipt(current, row, summary, tool) {
  const receipt = exactExplorationEvidenceReceipt(row.receipt);
  return receipt
    && Number(receipt.explorationId) === Number(current.id)
    && Number(receipt.explorationRevision) < Number(current.revision)
    && Number(receipt.explorationActionCount) + 1 <= Number(current.action_count)
    && Number(receipt.episodeId) === Number(row.episode_id)
    && receipt.tool === tool
    && receipt.evidenceSha256
      === createHash('sha256').update(summary).digest('hex')
    && receipt.topicKey === (clean(current.topic_key, 120) || null)
    && receipt.topicSha256 === explorationEvidenceTopicSha256(current)
    ? receipt : null;
}

function terminalPriorEvidence(current, expectedSummary) {
  if (!current || Number(current.action_count) < 1) return null;
  const evidence = Array.isArray(current.evidence) ? current.evidence : [];
  const row = evidence.at(-1);
  const summary = clean(redactSensitiveText(row?.evidence), 900);
  const tool = clean(row?.tool, 80);
  const expected = clean(redactSensitiveText(expectedSummary), 900);
  if (!(row?.ok === true && row?.verified === true
    && Number.isSafeInteger(Number(row?.episode_id)) && Number(row.episode_id) >= 1
    && tool && tool !== 'verkenning_bijhouden'
    && summary.length >= 8 && summary === expected)) return null;
  const exactReceipt = terminalEvidenceReceipt(current, row, summary, tool);
  return { summary, tool, receipt: exactReceipt };
}

function terminalContributionFailure(message, cause = null) {
  return Object.assign(new Error(message, cause ? { cause } : undefined), {
    failure_class: 'conflict', retryable: false, effect_uncertain: false,
  });
}

function duplicateFamilyResultFailure() {
  return Object.assign(terminalContributionFailure(
    'dit gezinsresultaat is inhoudelijk al zichtbaar vastgelegd',
  ), { code: DUPLICATE_FAMILY_RESULT_CODE });
}

function explorationEconomicCommitment(args) {
  if (!args || !Object.hasOwn(args, 'economisch_vervolg')) return null;
  const value = args.economisch_vervolg;
  const fields = ['definitie_klaar', 'titel', 'uitkomst', 'volgende_stap'];
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort()) !== canonicalJson(fields)) {
    throw terminalContributionFailure(
      'economisch_vervolg vereist exact Eva haar vier eigen commitmentvelden',
    );
  }
  const commitment = Object.freeze({
    title: clean(redactSensitiveText(value.titel), 240),
    outcome: clean(redactSensitiveText(value.uitkomst), 1_200),
    definitionOfDone: clean(redactSensitiveText(value.definitie_klaar), 1_200),
    nextAction: clean(redactSensitiveText(value.volgende_stap), 600),
  });
  if (Object.values(commitment).some((part) => part.length < 4)) {
    throw terminalContributionFailure(
      'economisch_vervolg vereist vier concrete, zelfgekozen commitmentvelden',
    );
  }
  return commitment;
}

function explorationFamilyResultRequest(args) {
  if (!args || !Object.hasOwn(args, 'gezinsresultaat')) return null;
  const value = args.gezinsresultaat;
  const fields = ['gezinsnut', 'titel'];
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort()) !== canonicalJson(fields)) {
    throw terminalContributionFailure(
      'gezinsresultaat vereist exact een natuurlijke titel en concreet gezinsnut',
    );
  }
  const request = Object.freeze({
    title: clean(redactSensitiveText(value.titel), 240),
    familyBenefit: clean(redactSensitiveText(value.gezinsnut), 600),
  });
  if (request.title.length < 4 || request.familyBenefit.length < 8) {
    throw terminalContributionFailure(
      'gezinsresultaat vereist een concrete titel en bruikbaar gezinsnut',
    );
  }
  return request;
}

function activeFamilyDevelopmentAspiration(aspirationId) {
  if (!Number.isSafeInteger(aspirationId) || aspirationId < 1) return null;
  const row = /** @type {any} */ (db.prepare(`SELECT id,status,kind,goal_key,beneficiary
    FROM aspirations WHERE id=?`).get(aspirationId));
  return row?.status === 'actief' && row?.kind === 'development'
    && clean(row?.beneficiary, 120).toLowerCase() === 'het gezin'
    ? row : null;
}

const FAMILY_RESULT_NON_RESULT = /\b(?:geen|niets|zonder)\s+(?:\p{L}+\s+){0,5}(?:nieuw\w*|bruikbaar\w*|concreet\w*|relevant\w*|vondst\w*|resultaat\w*|bewijs|bevestiging)\b|\b(?:niet|onvoldoende|onduidelijk|inconclusief)\s+(?:\p{L}+\s+){0,4}(?:bevestigd|aangetoond|gevonden|geverifieerd|bruikbaar)\b/iu;
const FAMILY_RESULT_GENERIC_TERMS = new Set([
  'actuele', 'deze', 'direct', 'eva', 'feit', 'gezin', 'gezinsnut', 'resultaat',
  'vondst', 'wordt', 'worden', 'kunnen', 'blijkt', 'blijven', 'gebruik', 'gebruiken',
]);
function familyResultTerms(value) {
  return [...new Set(dutchContentWords(value)
    .filter((word) => word.length >= 4 && !FAMILY_RESULT_GENERIC_TERMS.has(word)))];
}
function sharedFamilyResultTerms(left, right) {
  const available = new Set(right);
  return left.filter((word) => available.has(word)).length;
}
function familyResultNoveltyClaimSha256({ request, current, resultText }) {
  const resultTerms = familyResultTerms([
    resultText, request?.title, request?.familyBenefit,
  ].filter(Boolean).join(' ')).sort();
  const topicTerms = familyResultTerms([
    current?.question, current?.hypothesis, current?.expected_observation,
  ].filter(Boolean).join(' ')).sort();
  return createHash('sha256').update(canonicalJson({ resultTerms, topicTerms })).digest('hex');
}
function verifiedMaterialFamilyResult({
  request, current, priorEvidence, resultText, evidenceReceiptSha256,
}) {
  const receipt = priorEvidence?.receipt;
  if (!request || !receipt
    || receipt.receiptSha256 !== evidenceReceiptSha256
    || FAMILY_RESULT_NON_RESULT.test(resultText)
    || FAMILY_RESULT_NON_RESULT.test(priorEvidence.summary)
    || FAMILY_RESULT_NON_RESULT.test(`${request.title} ${request.familyBenefit}`)) return false;
  const evidenceTerms = familyResultTerms(priorEvidence.summary);
  const topicTerms = familyResultTerms([
    current?.topic_key, current?.question, current?.hypothesis,
    current?.expected_observation,
  ].filter(Boolean).join(' '));
  const resultTerms = familyResultTerms(resultText);
  const familyTerms = familyResultTerms(`${request.title} ${request.familyBenefit}`);
  return evidenceTerms.length >= 3 && topicTerms.length >= 3
    && resultTerms.length >= 3 && familyTerms.length >= 3
    && sharedFamilyResultTerms(resultTerms, evidenceTerms) >= 3
    && sharedFamilyResultTerms(resultTerms, topicTerms) >= 3
    && sharedFamilyResultTerms(familyTerms,
      [...new Set([...evidenceTerms, ...topicTerms])]) >= 2;
}

function explorationFamilyResultAttestation({
  request, expectedKey, aspirationId, goalKey, topicKey, priorEvidence, resultText,
  noveltyClaimSha256,
}) {
  return request ? Object.freeze({
    contract: EXPLORATION_FAMILY_RESULT_CONTRACT,
    sourceOutcomeKey: expectedKey,
    aspirationId,
    goalKey: clean(goalKey, 120) || null,
    topicKey: clean(topicKey, 120) || null,
    evidenceSha256: priorEvidence.receipt.evidenceSha256,
    evidenceReceiptId: priorEvidence.receipt.receiptId,
    evidenceReceiptSha256: priorEvidence.receipt.receiptSha256,
    evidenceOutputSha256: priorEvidence.receipt.outputSha256,
    evidenceEpisodeId: Number(priorEvidence.receipt.episodeId),
    resultSha256: createHash('sha256').update(resultText).digest('hex'),
    noveltyClaimSha256,
    title: request.title,
    familyBenefit: request.familyBenefit,
  }) : null;
}

function exactReplayFamilyResult(value, context) {
  const fields = [
    'aspirationId', 'contract', 'evidenceEpisodeId', 'evidenceOutputSha256',
    'evidenceReceiptId', 'evidenceReceiptSha256', 'evidenceSha256', 'familyBenefit',
    'goalKey', 'noveltyClaimSha256', 'resultSha256', 'sourceOutcomeKey', 'title', 'topicKey',
  ];
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort()) !== canonicalJson(fields)
    || (value.goalKey !== null && typeof value.goalKey !== 'string')
    || (value.topicKey !== null && typeof value.topicKey !== 'string')) return null;
  const expected = explorationFamilyResultAttestation({
    ...context, goalKey: value.goalKey, topicKey: value.topicKey,
  });
  return expected && canonicalJson(value) === canonicalJson(expected) ? expected : null;
}

function exactCurrentFamilyDevelopmentAspiration(attestation) {
  const row = activeFamilyDevelopmentAspiration(Number(attestation?.aspirationId));
  return Boolean(row
    && (clean(row.goal_key, 120) || null) === attestation.goalKey);
}

function priorVisibleFamilyResultClaim(outcomeKey, familyResult) {
  if (!/^[a-f0-9]{64}$/u.test(String(familyResult?.noveltyClaimSha256 || ''))) return false;
  return Boolean(db.prepare(`SELECT outcome_key FROM main.timeline
    WHERE kind='contribution' AND outcome_key<>?
      AND json_extract(meta,'$.familyVisible')=1
      AND json_extract(meta,'$.familyResult.contract')=?
      AND json_extract(meta,'$.familyResult.noveltyClaimSha256')=?
    LIMIT 1`).get(
    outcomeKey, EXPLORATION_FAMILY_RESULT_CONTRACT, familyResult.noveltyClaimSha256,
  ));
}

function exactTerminalContributionClaim({
  precommit, args, explorationId, fromRevision, toRevision,
  resultText, understandingSha256, requirementId, expectedKey,
}) {
  return [precommit?.contract === TERMINAL_CONTRIBUTION_CONTRACT,
    Number.isSafeInteger(explorationId), explorationId >= 1,
    Number.isSafeInteger(fromRevision), fromRevision >= 1,
    toRevision === fromRevision + 1, precommit?.terminalStatus === 'completed',
    precommit?.outcomeKey === expectedKey,
    /^[a-f0-9]{64}$/u.test(understandingSha256), requirementId === 'r1',
    Number(args?.id) === explorationId,
    Number(args?.verwachte_revisie) === fromRevision,
    clean(args?.bewerking, 30).toLowerCase() === 'afronden', resultText.length >= 8,
  ].every(Boolean);
}

function terminalReplayCandidate(before, { toRevision, resultText, decisionText }) {
  return [before, before?.source === 'self', before?.status === 'completed',
    Number(before?.revision) === toRevision,
    String(before?.result || '') === resultText,
    (before?.decision ?? null) === decisionText,
  ].every(Boolean);
}

function terminalReplayEvidence(precommit, replayMeta, current) {
  const summary = clean(redactSensitiveText(precommit?.evidenceSummary), 900);
  const tool = clean(replayMeta?.lastAction, 80);
  const rows = Array.isArray(current?.evidence) ? [...current.evidence].reverse() : [];
  const row = rows.find((candidate) => (
    candidate?.ok === true && candidate?.verified === true
    && clean(redactSensitiveText(candidate?.evidence), 900) === summary
    && clean(candidate?.tool, 80) === tool
  ));
  if (summary.length < 8 || !tool || tool === 'verkenning_bijhouden' || !row
    || !Number.isSafeInteger(Number(row.episode_id)) || Number(row.episode_id) < 1) return null;
  const receipt = terminalEvidenceReceipt(current, row, summary, tool);
  const family = replayMeta?.familyResult;
  if (family && (!receipt
    || receipt.receiptSha256 !== precommit?.evidenceReceiptSha256
    || family.evidenceReceiptId !== receipt.receiptId
    || family.evidenceReceiptSha256 !== receipt.receiptSha256
    || family.evidenceOutputSha256 !== receipt.outputSha256
    || Number(family.evidenceEpisodeId) !== Number(receipt.episodeId)
    || family.evidenceSha256 !== receipt.evidenceSha256)) return null;
  return { summary, tool, receipt };
}

function explorationEconomicHandoff({
  commitment, expectedKey, explorationId, fromRevision, toRevision, priorEvidence,
}) {
  return commitment ? Object.freeze({
    contract: EXPLORATION_ECONOMIC_HANDOFF_CONTRACT,
    sourceOutcomeKey: expectedKey, explorationId, fromRevision, toRevision,
    evidenceSha256: createHash('sha256').update(priorEvidence.summary).digest('hex'),
    goalKey: 'economic_value',
    authority: Object.freeze({
      internalWork: true, familyDelivery: true,
      externalContact: false, publication: false, spend: false,
    }),
    commitment,
  }) : null;
}

function settleTerminalReplay(rawCurrent, {
  aspirationId, toRevision, resultText, decisionText,
}) {
  const current = out(rawCurrent);
  const exact = [current, current?.source === 'self',
    (current?.aspiration_id == null ? null : Number(current.aspiration_id)) === aspirationId,
    current?.status === 'completed', Number(current?.revision) === toRevision,
    String(current?.result || '') === resultText, (current?.decision ?? null) === decisionText,
  ].every(Boolean);
  if (!exact) throw terminalContributionFailure('conflicterende exploration-outcome replay');
  return current;
}

function touchTerminalAspiration(aspirationId, ts, resultText) {
  if (aspirationId === null) return;
  const aspiration = db.prepare("SELECT id FROM aspirations WHERE id=? AND status='actief'")
    .get(aspirationId);
  if (!aspiration) throw terminalContributionFailure('de gekoppelde aspiratie is niet actief');
  const touched = db.prepare(`UPDATE aspirations SET last_touched=?,progress=?
    WHERE id=? AND status='actief'`).run(ts, `completed: ${resultText}`, aspirationId);
  if (Number(touched.changes || 0) !== 1) {
    throw terminalContributionFailure('de gekoppelde aspiratie kon niet worden bijgewerkt');
  }
}

function settleNewExplorationTerminal({
  precommit, mutationAuthority, rawCurrent, rawBefore, terminalEvidenceText,
  resultText, decisionText, explorationId, fromRevision, toRevision, aspirationId,
}) {
  if (!mutationAuthority) throw terminalContributionFailure(
    'de exploration-afronding mist een vrije issued mutation-authority',
  );
  if (canonicalJson(rawCurrent) !== canonicalJson(rawBefore)) {
    throw terminalContributionFailure('de exploration-snapshot is intussen gewijzigd');
  }
  const current = out(rawCurrent);
  if (!exactTerminalSnapshot(current, precommit.snapshot)
    || !terminalPriorEvidence(current, precommit.evidenceSummary)) {
    throw terminalContributionFailure('de exploration mist het uitgegeven bewijsanker');
  }
  const evidenceRows = [...current.evidence];
  const ts = nowIso();
  if (terminalEvidenceText) evidenceRows.push({ ts, text: terminalEvidenceText });
  const updated = db.prepare(`UPDATE eva_explorations
    SET updated_ts=?,status='completed',evidence_json=?,result=?,decision=?,
      next_wake_ts=NULL,last_selected_ts=?,scheduler_deferred_ts=NULL,revision=revision+1
    WHERE id=? AND revision=? AND status=? AND updated_ts=? AND evidence_json=?
      AND result IS ? AND decision IS ? AND next_wake_ts IS ? AND aspiration_id IS ?`).run(
    ts, JSON.stringify(evidenceRows.slice(-30)), resultText, decisionText, ts,
    explorationId, fromRevision, rawBefore.status, rawBefore.updated_ts,
    rawBefore.evidence_json, rawBefore.result, rawBefore.decision, rawBefore.next_wake_ts,
    aspirationId,
  );
  if (Number(updated.changes || 0) !== 1) {
    throw terminalContributionFailure('de exploration-CAS is intussen gewijzigd');
  }
  touchTerminalAspiration(aspirationId, ts, resultText);
  const completed = explorationById(explorationId);
  if (!completed || completed.status !== 'completed'
    || Number(completed.revision) !== toRevision) {
    throw terminalContributionFailure('de exploration-afronding is niet verifieerbaar');
  }
  return completed;
}

function terminalContributionBasis({
  precommit, args, explorationId, fromRevision, toRevision, resultText, decisionText,
  understandingSha256, requirementId, expectedKey,
}) {
  if (!exactTerminalContributionClaim({
    precommit, args, explorationId, fromRevision, toRevision,
    resultText, understandingSha256, requirementId, expectedKey,
  })) throw terminalContributionFailure(
    'de uitgegeven exploration-afronding is niet exact gebonden',
  );
  const before = explorationById(explorationId);
  const initial = exactTerminalSnapshot(before, precommit.snapshot);
  const replayCandidate = terminalReplayCandidate(before, {
    toRevision, resultText, decisionText,
  });
  if (!initial && !replayCandidate) throw terminalContributionFailure(
    'de exploration-snapshot is intussen gewijzigd',
  );
  if (initial && args !== precommit?.expectedArgs) throw terminalContributionFailure(
    'de nieuwe exploration-outcome vereist exact de uitgegeven argumentidentiteit',
  );
  const replayRow = replayCandidate ? /** @type {any} */ (db.prepare(`SELECT content,meta,outcome_key
    FROM main.timeline WHERE kind='contribution' AND outcome_key=?`).get(expectedKey)) : null;
  let replayMeta = null;
  try { replayMeta = replayRow ? JSON.parse(String(replayRow.meta || '')) : null; } catch { /* conflict */ }
  const priorEvidence = initial
    ? terminalPriorEvidence(before, precommit.evidenceSummary)
    : terminalReplayEvidence(precommit, replayMeta, before);
  if (!priorEvidence) throw terminalContributionFailure(
    'afronden als bijdrage vereist vooraf opgeslagen, geverifieerd toolbewijs',
  );
  return { before, initial, replayCandidate, replayRow, replayMeta, priorEvidence };
}

function prepareTerminalContribution({
  precommit, args, explorationId, fromRevision, toRevision, resultText, decisionText,
  understandingSha256, requirementId, expectedKey,
}) {
  const {
    before, initial, replayCandidate, replayRow, replayMeta, priorEvidence,
  } = terminalContributionBasis({
    precommit, args, explorationId, fromRevision, toRevision, resultText, decisionText,
    understandingSha256, requirementId, expectedKey,
  });
  const aspirationId = before?.aspiration_id == null ? null : Number(before.aspiration_id);
  if (aspirationId !== null
    && (!Number.isSafeInteger(aspirationId) || aspirationId < 1)) throw terminalContributionFailure(
    'de exploration mist haar gekoppelde aspiratie',
  );
  const economicHandoff = explorationEconomicHandoff({
    commitment: explorationEconomicCommitment(args), expectedKey, explorationId,
    fromRevision, toRevision, priorEvidence,
  });
  const familyResultRequest = explorationFamilyResultRequest(args);
  const familyResultVerified = verifiedMaterialFamilyResult({
    request: familyResultRequest,
    current: before,
    priorEvidence,
    resultText,
    evidenceReceiptSha256: precommit?.evidenceReceiptSha256 || null,
  });
  const noveltyClaimSha256 = familyResultRequest && familyResultVerified
    ? familyResultNoveltyClaimSha256({ request: familyResultRequest, current: before, resultText })
    : null;
  const behaviorProposalContext = precommit?.behaviorProposalContext || null;
  const behaviorExperiment = behaviorProposalContext
    ? behaviorProposalAttestation(behaviorProposalContext) : null;
  if (behaviorProposalContext && !behaviorExperiment) throw terminalContributionFailure(
    'de gedragsproef mist Eva haar exact uitgegeven, begrensde modelkeuze',
  );
  if ([Boolean(familyResultRequest), Boolean(economicHandoff), Boolean(behaviorExperiment)]
    .filter(Boolean).length > 1) throw terminalContributionFailure(
    'een exploration-afronding kan niet twee resultaatsoorten tegelijk materialiseren',
  );
  let familyResult = null;
  if (familyResultRequest && familyResultVerified
    && replayCandidate && replayMeta?.familyResult) {
    familyResult = exactReplayFamilyResult(replayMeta.familyResult, {
      request: familyResultRequest, expectedKey, aspirationId, priorEvidence, resultText,
      noveltyClaimSha256,
    });
    if (!familyResult) throw terminalContributionFailure(
      'conflicterend duurzaam gezinsresultaat bij exploration-replay',
    );
  } else if (familyResultRequest && familyResultVerified && !replayCandidate) {
    const aspiration = activeFamilyDevelopmentAspiration(aspirationId);
    familyResult = aspiration ? explorationFamilyResultAttestation({
      request: familyResultRequest, expectedKey, aspirationId,
      goalKey: aspiration.goal_key, topicKey: before.topic_key, priorEvidence, resultText,
      noveltyClaimSha256,
    }) : null;
  }
  const content = clean(familyResult
    ? `Eva ontdekte: ${resultText}` : `Zelfstandig afgerond: ${resultText}`, 500);
  const meta = Object.freeze({
    internalOnly: !familyResult,
    ...(familyResult ? { familyVisible: true, familyResult } : {}),
    source: 'bijdrage', owner: null, evidence: resultText,
    lastAction: priorEvidence.tool, status: 'done', syntheticTest: false,
    lifecycleContract: TERMINAL_CONTRIBUTION_CONTRACT,
    understandingSha256, requirementId, explorationId, aspirationId,
    fromRevision, toRevision, terminalStatus: 'completed',
    ...(economicHandoff ? { economicHandoff } : {}),
    ...(behaviorExperiment ? { behaviorExperiment } : {}),
  });
  const rawBefore = /** @type {any} */ (db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(explorationId));
  if (!rawBefore) throw terminalContributionFailure('de exploration bestaat niet meer');
  if (replayCandidate) {
    const expectedMeta = { ...meta, outcomeKey: expectedKey };
    if (!replayRow || replayRow.outcome_key !== expectedKey || replayRow.content !== content
      || canonicalJson(replayMeta) !== canonicalJson(expectedMeta)) {
      throw terminalContributionFailure('conflicterende exploration-outcome replay');
    }
  }
  return {
    initial, replayCandidate, priorEvidence, aspirationId,
    economicHandoff, familyResult, behaviorProposalContext, behaviorExperiment,
    content, meta, rawBefore,
  };
}

/**
 * Materialiseer een reeds uitgegeven exploration-afronding en haar publieke
 * contribution als één SQLite-outcome. De terminale `bewijs`-tekst kwalificeert
 * nooit als bewijs; alleen de laatste, al opgeslagen verified toolreceipt telt.
 */
export function completeIssuedExplorationContribution(precommit, args = {}) {
  const explorationId = Number(precommit?.explorationId);
  const fromRevision = Number(precommit?.fromRevision);
  const toRevision = Number(precommit?.toRevision);
  const resultText = clean(redactSensitiveText(args.resultaat));
  const decisionText = clean(redactSensitiveText(args.besluit)) || null;
  const terminalEvidenceText = clean(redactSensitiveText(args.bewijs), 1_500);
  const understandingSha256 = String(precommit?.understanding?.sha256 || '');
  const requirementId = String(precommit?.action?.requirementId || '');
  const expectedKey = terminalContributionOutcomeKey(
    explorationId, fromRevision, toRevision,
  );
  const prepared = prepareTerminalContribution({
    precommit, args, explorationId, fromRevision, toRevision, resultText, decisionText,
    understandingSha256, requirementId, expectedKey,
  });
  const {
    initial, aspirationId, economicHandoff, behaviorProposalContext,
    behaviorExperiment, rawBefore,
  } = prepared;
  let { familyResult, content, meta } = prepared;
  let mutationAuthority = null;

  try {
    const persist = () => logContributionOutcome(expectedKey, content, meta, ({
      replay, mutationAuthority: outcomeMutationAuthority,
    }) => {
      if (!replay && familyResult
        && !exactCurrentFamilyDevelopmentAspiration(familyResult)) {
        throw terminalContributionFailure(
          'gezinsresultaat mist een actueel gekoppelde gezinsontwikkelrichting',
        );
      }
      if (!replay && familyResult
        && priorVisibleFamilyResultClaim(expectedKey, familyResult)) {
        throw duplicateFamilyResultFailure();
      }
      const rawCurrent = /** @type {any} */ (db.prepare(
        'SELECT * FROM eva_explorations WHERE id=?',
      ).get(explorationId));
      let completed;
      if (replay) {
        completed = settleTerminalReplay(rawCurrent, {
          aspirationId, toRevision, resultText, decisionText,
        });
      } else {
        if (!initial) throw terminalContributionFailure(
          'nieuwe contribution-outcome mist haar oorspronkelijke exploration-snapshot',
        );
        mutationAuthority = reserveIssuedExplorationTerminalMutation(precommit);
        if (!mutationAuthority) throw terminalContributionFailure(
          'de exploration-afronding mist een vrije issued mutation-authority',
        );
        completed = settleNewExplorationTerminal({
          precommit, mutationAuthority, rawCurrent, rawBefore, terminalEvidenceText,
          resultText, decisionText, explorationId, fromRevision, toRevision, aspirationId,
        });
      }
      const handoff = economicHandoff
        ? replay
          ? replayExplorationEconomicHandoff(economicHandoff)
          : materializeExplorationEconomicHandoff(
            mutationAuthority, precommit, economicHandoff,
            outcomeMutationAuthority, {
              outcomeKey: expectedKey, content, meta: { ...meta, outcomeKey: expectedKey },
            },
          )
        : null;
      const behavior = behaviorExperiment
        ? replay
          ? Object.freeze({
            created: false,
            experiment: replayBehaviorExperiment(behaviorExperiment),
            attestation: behaviorExperiment,
          })
          : materializeBehaviorExperiment(
            behaviorProposalContext, outcomeMutationAuthority,
            { outcomeKey: expectedKey, content, meta: { ...meta, outcomeKey: expectedKey } },
          )
        : null;
      if (behaviorExperiment && !behavior?.experiment) throw terminalContributionFailure(
        'de behavior-proposal replay mist haar exacte duurzame experiment',
      );
      return { exploration: completed, economicHandoff: handoff, behaviorExperiment: behavior };
    });
    let persisted;
    try {
      persisted = persist();
    } catch (error) {
      if (error?.code !== DUPLICATE_FAMILY_RESULT_CODE) throw error;
      familyResult = null;
      content = clean(`Zelfstandig afgerond: ${resultText}`, 500);
      const internalMeta = { ...meta, internalOnly: true };
      delete internalMeta.familyVisible;
      delete internalMeta.familyResult;
      meta = Object.freeze(internalMeta);
      persisted = persist();
    }
    if (persisted.created
      && !commitIssuedExplorationTerminalMutation(mutationAuthority, precommit)) {
      throw terminalContributionFailure(
        'de gecommitteerde exploration-outcome mist haar mutation-settlement',
      );
    }
    const handoff = persisted.effect.economicHandoff;
    const behavior = persisted.effect.behaviorExperiment;
    if (handoff?.created) {
      try {
        bus.emit('work_changed', {
          id: handoff.workItem.id,
          status: handoff.workItem.status,
          updated_ts: handoff.workItem.updated_ts,
        });
      } catch { /* de domeinmelding volgt bewust pas na commit */ }
    }
    return {
      ok: true,
      changed: persisted.created,
      verified: true,
      message: clean(resultText, 500),
      exploration: {
        id: Number(persisted.effect.exploration.id),
        source: persisted.effect.exploration.source,
        status: persisted.effect.exploration.status,
        revision: Number(persisted.effect.exploration.revision),
      },
      contribution: {
        contract: TERMINAL_CONTRIBUTION_CONTRACT,
        outcome_key: expectedKey,
        created: persisted.created,
      },
      ...(handoff ? { economic_handoff: {
        contract: EXPLORATION_ECONOMIC_HANDOFF_CONTRACT,
        source_outcome_key: expectedKey,
        work_id: Number(handoff.workItem.id),
        episode_id: Number(handoff.episode.id),
        goal_key: handoff.episode.goal_key,
        delivery_policy: handoff.episode.delivery_policy,
        delivery_target: handoff.episode.delivery_target,
        created: handoff.created,
      } } : {}),
      ...(behavior ? { behavior_experiment: {
        contract: 'BehaviorLearningExperiment/v1',
        source_outcome_key: expectedKey,
        id: Number(behavior.experiment.id),
        person_id: Number(behavior.experiment.personId),
        candidate_sha256: behavior.experiment.candidateSha256,
        // Dit toolreceipt attesteert de immutable creatiesnapshot. De actuele
        // lifecycle kan daarna door echte delivery/feedback terecht bewegen.
        status: 'canary_pending',
        revision: 2,
        created: behavior.created,
      } } : {}),
    };
  } catch (error) {
    if (mutationAuthority) {
      releaseIssuedExplorationTerminalMutation(mutationAuthority, precommit);
    }
    if (error?.failure_class === 'conflict'
      && error?.retryable === false && error?.effect_uncertain === false) throw error;
    throw terminalContributionFailure(
      'de exploration-afronding en bijdrage zijn volledig teruggedraaid', error,
    );
  }
}

function noteExplorationActionCas(id, {
  tool, ok, verified, evidence = null, episodeId = null,
} = {}, expected = null) {
  const current = explorationById(id);
  if (!current || !ACTIVE.has(current.status) || current.status === 'evaluating'
    || tool === 'verkenning_bijhouden') return { accepted: false, exploration: current };
  if (current.next_wake_ts && Date.parse(current.next_wake_ts) > Date.now()) {
    return { accepted: false, exploration: current };
  }
  if (expected && (Number(current.id) !== expected.id
    || Number(current.revision) !== expected.revision
    || String(current.status || '') !== expected.status
    || Number(current.action_count) !== expected.actionCount
    || Number(current.max_actions) !== expected.maxActions
    || String(current.question || '') !== expected.question)) {
    return { accepted: false, exploration: current };
  }
  const receiptClaim = claimExplorationEvidenceReceipt(current, {
    tool, ok, verified, evidence, episodeId,
  }, expected);
  const useful = verified === true && clean(evidence).length >= 4;
  const row = {
    ts: nowIso(), tool: clean(tool, 80), ok: ok === true, verified: verified === true,
    evidence: clean(evidence, 900) || null, episode_id: Number(episodeId) || null,
    ...(receiptClaim ? { receipt: receiptClaim.receipt } : {}),
  };
  const rows = [...current.evidence, row].slice(-30);
  const count = current.action_count + 1;
  const emptyStreak = useful ? 0 : current.empty_evidence_streak + 1;
  const exhausted = count >= current.max_actions || emptyStreak >= 2;
  const update = db.prepare(`UPDATE eva_explorations SET updated_ts=?,status=?,action_count=?,empty_evidence_streak=?,
    evidence_json=?,decision=?,scheduler_deferred_ts=NULL,revision=revision+1 WHERE id=?
      AND revision=? AND status=? AND action_count=? AND max_actions=? AND question=? AND updated_ts=?`).run(
    nowIso(), exhausted ? 'evaluating' : 'running', count, emptyStreak, JSON.stringify(rows),
    exhausted
      ? (emptyStreak >= 2 ? 'Twee stappen zonder nieuwe bevestigde waarneming: nu stoppen of van aanpak veranderen.' : 'Actiebudget bereikt: nu beoordelen.')
      : current.decision,
    Number(id), current.revision, current.status, current.action_count,
    current.max_actions, current.question, current.updated_ts,
  );
  const accepted = Number(update.changes || 0) === 1;
  if (accepted && receiptClaim) receiptClaim.consume();
  return { accepted, exploration: explorationById(id) };
}

export function noteExplorationAction(id, options = {}) {
  return noteExplorationActionCas(id, options).exploration;
}

export function noteIssuedExplorationAction(id, expected, options = {}) {
  if (!expected || typeof expected !== 'object') {
    return { accepted: false, exploration: explorationById(id) };
  }
  return noteExplorationActionCas(id, options, expected);
}

function aspirationCadenceHours(item) {
  const raw = item?.cadence_hours;
  if (raw === null || raw === undefined || String(raw).trim() === '') {
    return DEFAULT_ASPIRATION_CADENCE_HOURS[item?.kind]
      || DEFAULT_ASPIRATION_CADENCE_HOURS.interest;
  }
  const hours = Number(raw);
  return Number.isFinite(hours) && hours >= 0
    ? hours
    : DEFAULT_ASPIRATION_CADENCE_HOURS[item?.kind]
      || DEFAULT_ASPIRATION_CADENCE_HOURS.interest;
}

function aspirationDueTs(item) {
  const cadenceHours = aspirationCadenceHours(item);
  if (cadenceHours === 0) return item?.last_touched ? Number.POSITIVE_INFINITY : 0;
  const touched = Date.parse(item?.last_touched || item?.created_ts || '');
  return (Number.isFinite(touched) ? touched : 0) + cadenceHours * 3600_000;
}

function aspirationDue(item, now = Date.now()) {
  return aspirationDueTs(item) <= now;
}

function compareDueAspirations(left, right) {
  return aspirationDueTs(left) - aspirationDueTs(right)
    || Number(right.priority || 0.5) - Number(left.priority || 0.5)
    || Date.parse(left.created_ts || '') - Date.parse(right.created_ts || '')
    || Number(left.id) - Number(right.id);
}

export function attentionContextText(now = new Date(), { focusId = null, focusKind = null } = {}) {
  const at = now.getTime();
  const activeExplorations = listExplorations({ activeOnly: true, limit: 12, sources: ['self'] });
  const explorations = activeExplorations
    .filter((item) => !item.next_wake_ts || Date.parse(item.next_wake_ts) <= at)
    .sort((a, b) => Number(
      focusKind === 'exploration' && Number(b.id) === Number(focusId),
    ) - Number(focusKind === 'exploration' && Number(a.id) === Number(focusId)))
    .slice(0, 2);
  const sleepingExplorations = activeExplorations
    .filter((item) => item.next_wake_ts && Date.parse(item.next_wake_ts) > at).slice(0, 1);
  const aspirations = activeAspirations()
    .filter((item) => aspirationDue(item, at))
    .sort((a, b) => Number(
      focusKind === 'aspiration'
        && Number(/** @type {any} */ (b).id) === Number(focusId),
    ) - Number(focusKind === 'aspiration'
      && Number(/** @type {any} */ (a).id) === Number(focusId))
      || compareDueAspirations(a, b))
    .slice(0, explorations.length ? 1 : 2);
  const lines = [];
  if (explorations.length) {
    lines.push('Open eigen verkenningen:');
    for (const item of explorations) {
      lines.push(clean(`- #${item.id} [${item.status}] onderwerp=${item.topic_key || 'legacy'} ${item.question} | hypothese: ${item.hypothesis} | ${item.action_count}/${item.max_actions} stappen | stop: ${item.stop_rule}${item.decision ? ` | kernbesluit: ${item.decision}` : ''}`, 420));
    }
  }
  if (sleepingExplorations.length) {
    lines.push('Slapende eigen verkenningen; hun wekdatum is al de herinnering, plan hiervoor geen tweede zelfherinnering:');
    for (const item of sleepingExplorations) {
      lines.push(clean(`- #${item.id} onderwerp=${item.topic_key || 'legacy'} wordt wakker op ${item.next_wake_ts}: ${item.question}`, 300));
    }
  }
  const recentDecisions = listExplorations({ limit: 30, sources: ['self'] })
    .filter((item) => TERMINAL.has(item.status) && item.decision
      && at - Date.parse(item.updated_ts || '') < 24 * 3600_000)
    .slice(0, 2);
  if (recentDecisions.length) {
    lines.push('Recente eigen besluiten die nog gelden:');
    for (const item of recentDecisions) {
      lines.push(clean(`- #${item.id} onderwerp=${item.topic_key || 'legacy'}: ${item.decision} | eerdere stopregel: ${item.stop_rule}`, 360));
    }
    lines.push('Begin over hetzelfde onderwerp geen nieuwe cyclus met een soepelere drempel. Heropen het eerdere besluit alleen met het id en concreet nieuw bewijs dat de wijziging verklaart.');
  }
  if (aspirations.length) {
    lines.push('Richtingen die volgens je eigen cadans weer aandacht mogen krijgen:');
    for (const item of aspirations) {
      lines.push(clean(`- aspiratie #${item.id}: ${item.text}${item.next_step ? ` | eerdere volgende stap: ${item.next_step}` : ''}`, 320));
    }
  }
  lines.push('Kies zelf wat je werkelijk wilt onderzoeken. Voor een nieuwe verkenning leg je vóór de tweede inhoudelijke toolactie met verkenning_bijhouden je eigen vraag, hypothese, verwachte waarneming, actiebudget en stop/pivotregel vast. Geef ook een stabiele onderwerp_sleutel en minstens twee concrete onderwerp_ankers. De kern gebruikt herkenbare entiteiten en gebeurtenissen waar die bestaan, maar een nieuw onderwerp hoeft niet in een vooraf geschreven woordenlijst te passen. Rond een bereikt budget af of verander aantoonbaar van aanpak.');
  return lines.join('\n').slice(0, 2_600);
}

/**
 * Alleen een negatieve continuïteitsgrens voor modelcontext. Deze projectie
 * geeft geen attention, id, revisie, receipt of uitvoerrecht uit; de scheduler
 * blijft de enige route die een slapende exploration later werkelijk selecteert.
 */
export function sleepingExplorationConstraintText(now = new Date()) {
  const at = now instanceof Date ? now.getTime() : new Date(now).getTime();
  if (!Number.isFinite(at)) return '';
  const sleeping = listExplorations({ activeOnly: true, limit: 40, sources: ['self'] })
    .filter((item) => item.status === 'waiting'
      && Number.isFinite(Date.parse(item.next_wake_ts || ''))
      && Date.parse(item.next_wake_ts) > at)
    .sort((a, b) => Date.parse(a.next_wake_ts) - Date.parse(b.next_wake_ts))
    .slice(0, SLEEPING_CONSTRAINT_LIMIT);
  if (!sleeping.length) return '';
  const lines = [
    'Continuïteitsgrens voor slapende eigen vragen:',
    'Deze vragen wachten bewust tot hun genoemde wekdatum. Onderzoek, rapporteer, herplan of start vóór die datum geen parallelle cyclus over semantisch dezelfde vraag. Kies iets werkelijk anders of blijf stil. Dit is geen opdracht, bewijs, receipt, binding of bevoegdheid.',
  ];
  for (const item of sleeping) {
    const question = sleepingConstraintTextPart(item.question, 280);
    const boundary = sleepingConstraintTextPart(item.decision || item.stop_rule, 220);
    if (!question) continue;
    lines.push(`- Vraag: ${question} | wekdatum: ${new Date(item.next_wake_ts).toISOString()}${boundary ? ` | grens: ${boundary}` : ''}`);
  }
  return lines.length > 2 ? lines.join('\n').slice(0, 1_800) : '';
}

function normalizeTopicPart(value, limit = 120) {
  return clean(value, limit).toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/[^a-z0-9:._-]+/g, '_').replace(/^_+|_+$/g, '');
}

const FEATURE_PATTERNS = {
  subjects: {
    lighting: /(?:lamp(?:en)?|\blicht(?:en)?\b|verlicht\w*|\blight\b|lighting|illumination)/i,
    camera: /(?:camera|cameras|camerabeeld|frame|beeldherkenning|vision)/i,
    access_sensor: /(?:\b(?:voor|achter|schuur|tuin|keuken)?deur\w*|\braam(?:sensor|contact|entiteit)?\w*|\bcontactentiteit\w*|\bdevice_class\b.{0,35}\b(?:door|window|opening)\b|\b(?:door|window|opening)\b.{0,35}\bdevice_class\b)/i,
    energy: /(?:\benergie\w*|\bverbruik\w*|\bvermogen\w*|\bsluipverbruik\w*|\benergy\b|\bpower\b)/i,
    astronomy: /\b(?:perse[iï]d\w*|geminid\w*|quadrantid\w*|lyrid\w*|leonid\w*|orionid\w*|aquariid\w*|meteoor\w*|sterren\w*|astronom\w*|maanstand|meteor\w*|maansverduister\w*|zonsverduister\w*|lunar[ -]?eclipse|solar[ -]?eclipse)\b/i,
    cooling: /\b(?:koel\w*|ventil\w*|airco|hitte|binnentemperatuur)\b/i,
    weather: /\b(?:weer|bewolking|neerslag|regen|wind|forecast|weather)\b/i,
    vacuum: /\b(?:robotstofzuiger|stofzuig\w*|vacuum|roborock)\b/i,
    software: /\b(?:software|code|programmeer\w*|addon|plugin|api|repository)\b/i,
    business: /\b(?:bedrijf|bedrijven|omzet|verdien\w*|klant|business|revenue)\b/i,
    communication: /\b(?:gesprek|communicatie|spraak|stem|voice|wakeword)\b/i,
    household: /\b(?:huishouden|huisgenoot|woning|home assistant)\b/i,
    testing: /(?:\btest\w*|\bproef\w*|bewijsroute)/i,
  },
  dimensions: {
    presence: /(?:\b(?:aanwezigheid\w*|afwezigheid\w*|bewoner\w*|occupancy|resident\w*|persoon|personen|thuis|leeg|buitenactiviteit\w*)\b|\b(?:iemand|niemand)\s+(?:buiten|binnen|thuis|aanwezig)\b)/i,
    schedule: /\b(?:schema|planning|tijdvenster|tijdstip|timer|schedule|cadans)\b/i,
    cause: /\b(?:oorzaak|waardoor|actor|trigger|inschakel\w*|root cause)\b/i,
    brightness: /\b(?:helderheid|dim\w*|brightness|lichtsterkte)\b/i,
    colour: /\b(?:kleur|colour|color|rgb)\b/i,
    consumption: /\b(?:verbruik|vermogen|bespar\w*|consumption|watt)\b/i,
    connectivity: /\b(?:bereikbaar|verbonden|connect\w*|unavailable|offline|online)\b/i,
    weather_window: /\b(?:bewolking|helder weer|kijkvenster|zichtbaar|forecast|waarneem\w*)\b/i,
    improvement: /\b(?:verbeter\w*|uitbreid\w*|upgrade|zelfverbeter\w*)\b/i,
  },
  components: {
    wakeword: /\bwake[ -]?word\w*\b/i,
    dashboard: /\bdashboard\w*\b/i,
    exploration_gate: /\b(?:verkenningsgate|exploration[ _-]?gate)\b/i,
    energy_monitor: /\b(?:sluipverbruik|energy[ _-]?monitor)\b/i,
    json_compare: /\b(?:json[ _-]?(?:vergelijk|compare)|deep[ _-]?equal)\b/i,
    tool_package: /\b(?:toolpakket|package[ _-]?manager)\b/i,
  },
  effects: {
    switch_off: /\b(?:uitzetten|uitschakelen|doven|switch off|turn off|(?:zet|schakel)\b.{0,30}\buit|(?:kan|kunnen|mag|mogen|moet|zou)\b.{0,60}\b(?:uit|uitgezet|uitgeschak\w*|gedoofd))\b/i,
    switch_on: /\b(?:aanzetten|inschakelen|switch on|turn on|(?:zet|schakel)\b.{0,30}\baan|(?:kan|kunnen|mag|mogen|moet|zou)\b.{0,60}\b(?:aan|aangezet|ingeschak\w*))\b/i,
    automate: /\b(?:automatiseren|automatisering maken|automation|timer instellen)\b/i,
  },
  contexts: {
    outdoor: /(?:\btuin(?:lamp|licht|verlicht)?|\bbuiten(?:lamp|licht|verlicht)|\boverkapping\b|\bschuur\b|\bgarden\b|\boutdoor\b|\bexterior\b)/i,
    bedroom: /(?:\bslaapkamer|\bbedroom)/i,
    living_room: /(?:\bwoonkamer|\bliving room)/i,
    kitchen: /(?:\bkeuken|\bkitchen)/i,
    back_door: /(?:\bachterdeur\w*|\bback[ _-]?door\w*)/i,
    front_door: /(?:\bvoordeur\w*|\bfront[ _-]?door\w*)/i,
    window: /(?:\braam\w*|\bwindow\w*)/i,
    door: /(?:\b(?:voor|achter)?deur\w*|\bdoorbell\w*)/i,
  },
};

function semanticText(item = {}) {
  return clean([
    item.question, item.hypothesis, item.expected_observation || item.expectedObservation,
    item.stop_rule || item.stopRule,
  ].filter(Boolean).join(' '), 8_000).toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/\b(?:geen|niet|zonder)\s+(?:een\s+|de\s+|het\s+)?(?:camera|lamp|licht(?:en)?|verlichting|energie|weer|stofzuiger)\w*/g, ' ');
}

function focusSemanticText(item = {}) {
  return clean([item.question, item.hypothesis].filter(Boolean).join(' '), 4_000)
    .toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/\b(?:geen|niet|zonder)\s+(?:een\s+|de\s+|het\s+)?(?:camera|lamp|licht(?:en)?|verlichting|energie|weer|stofzuiger)\w*/g, ' ');
}

function primaryFocusSubject(item = {}, subjects = []) {
  const concrete = subjects.filter((subject) => !['household', 'testing'].includes(subject));
  if (!concrete.length) return null;
  const question = focusSemanticText({ question: item.question });
  const interrogative = /\b(?:welke|welk|wat|waardoor|hoe|waarom|kan|moet|is)\b/i.exec(question);
  const directQuestionClause = interrogative
    ? question.slice(interrogative.index).split(/\b(?:terwijl|zonder|zodat|mits|tenzij)\b|[,;?]/i)[0]
    : '';
  const fields = [
    [directQuestionClause, 8],
    [question, 4],
    [focusSemanticText({ hypothesis: item.hypothesis }), 2],
    [semanticText(item), 1],
  ];
  const ranked = concrete.map((subject) => {
    const pattern = FEATURE_PATTERNS.subjects[subject];
    let score = 0;
    let first = Number.MAX_SAFE_INTEGER;
    for (const [field, weight] of fields) {
      const match = pattern?.exec(field);
      if (!match) continue;
      score += weight;
      first = Math.min(first, Number(match.index || 0));
    }
    return { subject, score, first };
  }).filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.first - b.first || a.subject.localeCompare(b.subject));
  return ranked[0]?.subject || null;
}

function intendedEffectText(item = {}) {
  return clean([item.question, item.hypothesis].filter(Boolean).join(' '), 4_000)
    .toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/\b(?:zonder|niet|niets)\b.{0,30}\b(?:schakelen|bedienen|aanzetten|uitzetten|uitschakelen|inschakelen)\b/g, ' ');
}

function hasExplicitReadOnlyIntent(item = {}) {
  const text = clean([
    item.question, item.hypothesis, item.expected_observation || item.expectedObservation,
    item.stop_rule || item.stopRule,
  ].filter(Boolean).join(' '), 6_000).toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '');
  return /\bread[ -]?only\b/.test(text)
    || /\b(?:niets|geen enkel apparaat)\b.{0,30}\b(?:schakelen|bedienen|wijzigen)\b/.test(text)
    || /\bzonder\b.{0,35}\b(?:iets te )?(?:schakelen|bedienen|wijzigen)\b/.test(text)
    || /\balleen\b.{0,40}\b(?:lezen|uitlezen|teruglezen|onderzoeken|bekijken|controleren)\b/.test(text);
}

function canonicalStrongId(prefix, rawValue) {
  let value = normalizeTopicPart(rawValue, 100);
  if (prefix === 'event') value = value.replace(/perseiden?/g, 'perseids');
  return `${prefix}:${value}`;
}

function exactIdentityMention(text, rawValue) {
  const comparable = (value) => clean(value, 8_000).toLowerCase().normalize('NFD')
    .replace(/\p{Diacritic}/gu, '').replace(/[^a-z0-9]+/g, ' ').trim();
  const phrase = comparable(clean(rawValue, 120));
  if (phrase.length < 2) return false;
  return comparable(text).includes(phrase);
}

function durableReferenceExists(prefix, rawValue) {
  const raw = clean(rawValue, 120);
  const numeric = /^\d+$/.test(raw) ? Number(raw) : null;
  try {
    if (prefix === 'goal') return !!(numeric
      ? db.prepare('SELECT 1 FROM eva_goals WHERE id=?').get(numeric)
      : db.prepare('SELECT 1 FROM eva_goals WHERE goal_key=?').get(raw));
    if (prefix === 'project') return !!(numeric && db.prepare('SELECT 1 FROM eva_projects WHERE id=?').get(numeric));
    if (prefix === 'work') return !!(numeric && db.prepare('SELECT 1 FROM eva_work_items WHERE id=?').get(numeric));
    if (prefix === 'aspiration') return !!(numeric && db.prepare('SELECT 1 FROM aspirations WHERE id=?').get(numeric));
    if (prefix === 'artifact') {
      return !!(db.prepare('SELECT 1 FROM eva_capabilities WHERE slug=? OR name=?').get(raw, raw)
        || db.prepare('SELECT 1 FROM eva_tool_installations WHERE slug=? OR package_name=?').get(raw, raw));
    }
  } catch { return false; }
  return false;
}

function deriveTopicFeatures(item = {}) {
  const text = semanticText(item);
  const focusText = focusSemanticText(item);
  const collect = (patterns, source = text) => Object.entries(patterns)
    .filter(([, pattern]) => pattern.test(source)).map(([name]) => name).sort();
  let subjects = collect(FEATURE_PATTERNS.subjects);
  let focusSubjects = collect(FEATURE_PATTERNS.subjects, focusText);
  const strongIds = new Set();
  for (const match of text.matchAll(/\b((?:light|camera|sensor|binary_sensor|switch|vacuum|climate)\.[a-z0-9_]+)\b/g)) {
    strongIds.add(`entity:${match[1]}`);
  }
  for (const [eventKey, pattern] of ASTRONOMY_EVENTS) {
    if (!pattern.test(text)) continue;
    const year = text.match(/\b20\d{2}\b/)?.[0];
    strongIds.add(`event:${eventKey}${year ? `-${year}` : ''}`);
  }
  for (const anchor of Array.isArray(item.topic_anchors) ? item.topic_anchors : []) {
    const match = normalizeTopicPart(anchor, 120).match(/^(project|work|goal|aspiration|artifact):(.+)$/);
    if (!match) continue;
    const [, prefix, rawValue] = match;
    if (exactIdentityMention(text, rawValue) && durableReferenceExists(prefix, rawValue)) {
      strongIds.add(canonicalStrongId(prefix, rawValue));
    }
  }
  const components = collect(FEATURE_PATTERNS.components);
  for (const match of text.matchAll(/\bcomponent:([a-z0-9._-]{3,80})\b/g)) components.push(match[1]);
  if (subjects.includes('testing') && /\b(?:gate|ten onrechte|audit|correctie)\b/i.test(text)) {
    subjects = subjects.filter((subject) => ['testing', 'software'].includes(subject));
    focusSubjects = focusSubjects.filter((subject) => ['testing', 'software'].includes(subject));
  }
  const concreteSubjects = (values) => values.filter((subject) => !['household', 'testing'].includes(subject));
  focusSubjects = concreteSubjects(focusSubjects);
  if (!focusSubjects.length) focusSubjects = concreteSubjects(subjects);
  const effects = collect(FEATURE_PATTERNS.effects, intendedEffectText(item));
  let contexts = collect(FEATURE_PATTERNS.contexts);
  if (contexts.includes('back_door') || contexts.includes('front_door')) {
    contexts = contexts.filter((context) => context !== 'door');
  }
  return {
    version: TOPIC_FEATURE_VERSION,
    subjects,
    focus_subjects: [...new Set(focusSubjects)].sort(),
    focus_subject: primaryFocusSubject(item, subjects),
    dimensions: collect(FEATURE_PATTERNS.dimensions),
    focus_dimensions: collect(FEATURE_PATTERNS.dimensions, focusText),
    contexts,
    components: [...new Set(components)].sort(),
    effects,
    read_only: hasExplicitReadOnlyIntent(item) && effects.length === 0,
    strong_ids: [...strongIds].sort(),
  };
}

function explorationIdentity({ topicKey, topicAnchors, ...content } = {}) {
  const key = normalizeTopicPart(topicKey);
  const anchors = [...new Set((Array.isArray(topicAnchors) ? topicAnchors : [])
    .map((value) => normalizeTopicPart(value, 100)).filter((value) => value.length >= 4))].slice(0, 8);
  if (key.length < 4 || anchors.length < 2) {
    return {
      ok: false,
      reason: 'een nieuwe verkenning vereist een stabiele onderwerp_sleutel en minstens twee concrete onderwerp_ankers (bijvoorbeeld entity-, project-, gebeurtenis- en beslisdomein); methode- of tijdwoorden zijn geen onderwerp',
    };
  }
  const topicFeatures = deriveTopicFeatures({ ...content, topic_anchors: anchors });
  return { ok: true, topicKey: key, topicAnchors: anchors, topicFeatures };
}

function intersects(left = [], right = []) {
  const wanted = new Set(left);
  return right.some((value) => wanted.has(value));
}

function sameExplorationTopic(item, candidate) {
  if (clean(item?.question).toLowerCase() === clean(candidate?.question).toLowerCase()) return true;
  const prior = item?.topic_features?.version === TOPIC_FEATURE_VERSION ? item.topic_features : deriveTopicFeatures(item);
  const current = candidate?.topic_features?.version === TOPIC_FEATURE_VERSION ? candidate.topic_features : deriveTopicFeatures(candidate);
  const sharedStrongId = intersects(prior.strong_ids, current.strong_ids);
  const priorKey = normalizeTopicPart(item?.topic_key);
  const currentKey = normalizeTopicPart(candidate?.topic_key);
  for (const family of [/^(?:entity|device):/, /^event:/, /^project:/, /^work:/, /^goal:/, /^aspiration:/, /^artifact:/]) {
    const priorFamily = prior.strong_ids.filter((id) => family.test(id));
    const currentFamily = current.strong_ids.filter((id) => family.test(id));
    if (priorFamily.length && currentFamily.length && !intersects(priorFamily, currentFamily)) return false;
    if (priorFamily.length !== currentFamily.length && priorKey !== currentKey) return false;
  }
  const priorFocus = prior.focus_subject || prior.focus_subjects?.[0] || null;
  const currentFocus = current.focus_subject || current.focus_subjects?.[0] || null;
  if (!priorFocus || !currentFocus) return !priorFocus && !currentFocus
    && priorKey.length >= 4 && priorKey === currentKey;
  if (priorFocus !== currentFocus) return false;
  if (sharedStrongId && priorKey.length >= 4 && priorKey === currentKey) return true;
  if (!sharedStrongId && prior.contexts.length && current.contexts.length
    && !intersects(prior.contexts, current.contexts)) return false;
  const softwareLike = prior.subjects.includes('software') && current.subjects.includes('software');
  if (softwareLike) {
    if (prior.components?.length && current.components?.length) {
      return intersects(prior.components, current.components);
    }
    return false;
  }
  const priorDimensions = prior.focus_dimensions || prior.dimensions;
  const currentDimensions = current.focus_dimensions || current.dimensions;
  return intersects(priorDimensions, currentDimensions);
}

function explorationSafetyConflict(item, candidate) {
  const prior = item?.topic_features?.version === TOPIC_FEATURE_VERSION ? item.topic_features : deriveTopicFeatures(item);
  const current = candidate?.topic_features?.version === TOPIC_FEATURE_VERSION ? candidate.topic_features : deriveTopicFeatures(candidate);
  const mutable = ['lighting', 'vacuum', 'cooling'];
  const priorMutableSubjects = (prior.focus_subjects || [prior.focus_subject]).filter((subject) => mutable.includes(subject));
  const currentMutableSubjects = (current.focus_subjects || [current.focus_subject]).filter((subject) => mutable.includes(subject));
  const mutableHouseSubject = intersects(priorMutableSubjects, currentMutableSubjects);
  const priorSafetyConstraint = intersects(prior.dimensions, ['presence', 'connectivity']);
  const safetyContextMatches = !prior.contexts.length
    || !current.contexts.length
    || intersects(prior.contexts, current.contexts);
  return mutableHouseSubject && priorSafetyConstraint && safetyContextMatches && !current.read_only;
}

function backfillTopicFeatures() {
  const rows = db.prepare('SELECT * FROM eva_explorations').all();
  const update = db.prepare('UPDATE eva_explorations SET topic_features_json=? WHERE id=?');
  db.transaction(() => {
    for (const row of rows) {
      const features = JSON.stringify(deriveTopicFeatures({
        ...row, topic_anchors: parseJson(row.topic_anchors_json, []),
      }));
      if (features !== (row.topic_features_json || '{}')) update.run(features, row.id);
    }
  })();
}

backfillTopicFeatures();

function issueExplorationAttention(exploration, { retryAttempt = false } = {}) {
  const attention = createIssuedExplorationAttention(exploration);
  if (!attention) return null;
  ISSUED_EXPLORATION_ATTENTION.set(attention, Object.freeze({
    id: Number(exploration.id),
    createdTs: String(exploration.created_ts),
    source: String(exploration.source),
    status: String(exploration.status),
    revision: Number(exploration.revision),
    updatedTs: String(exploration.updated_ts),
    nextWakeTs: exploration.next_wake_ts ?? null,
    lastSelectedTs: exploration.last_selected_ts ?? null,
    schedulerDeferredTs: exploration[SCHEDULER_DEFERRED_TS] ?? null,
    question: String(exploration.question),
    retryAttempt: retryAttempt === true,
  }));
  return attention;
}

function schedulableExplorations(now = Date.now()) {
  const dueAt = new Date(now).toISOString();
  return db.prepare(`SELECT * FROM eva_explorations
    WHERE source='self' AND status IN ('chosen','running','waiting','evaluating')
      AND (next_wake_ts IS NULL OR trim(next_wake_ts)=''
        OR julianday(next_wake_ts) <= julianday(?))
    ORDER BY coalesce(nullif(last_selected_ts,''),updated_ts) ASC,id ASC`).all(dueAt).map(out);
}

function deferredExplorationTs(item) {
  const value = item?.[SCHEDULER_DEFERRED_TS];
  const parsed = Date.parse(value || '');
  return typeof value === 'string' && Number.isFinite(parsed)
    && new Date(parsed).toISOString() === value
    ? parsed
    : null;
}

export function dueAttention() {
  const now = Date.now();
  const explorations = schedulableExplorations(now);
  const dueAspirations = activeAspirations().filter((item) => aspirationDue(item, now));
  // Een ontbrekende én een corrupte hostmarker failen open naar één begrensde
  // explorationbeurt; een onleesbare timestamp mag een actieve rij nooit verbergen.
  for (const freshExploration of explorations.filter((item) => (
    deferredExplorationTs(item) == null
  ))) {
    const issued = issueExplorationAttention(freshExploration, { retryAttempt: false });
    if (issued) return issued;
  }
  const aspiration = dueAspirations
    .sort(compareDueAspirations)[0];
  if (aspiration) {
    const attention = Object.freeze({
      kind: 'aspiration', id: aspiration.id, text: aspiration.text,
    });
    ISSUED_ASPIRATION_ATTENTION.set(attention, Object.freeze({
      id: Number(aspiration.id),
      createdTs: String(aspiration.created_ts),
      text: String(aspiration.text),
      lastTouched: aspiration.last_touched ?? null,
    }));
    return attention;
  }
  const retryableExplorations = explorations.filter((item) => {
    const deferredAt = deferredExplorationTs(item);
    return deferredAt != null && deferredAt + EXPLORATION_RETRY_BACKOFF_MS <= now;
  });
  for (const retryableExploration of retryableExplorations) {
    const issued = issueExplorationAttention(retryableExploration, { retryAttempt: true });
    if (issued) return issued;
  }
  return null;
}

export function nextDeferredExplorationRetryTs() {
  const now = Date.now();
  const retries = schedulableExplorations(now).map((item) => {
    const deferredAt = deferredExplorationTs(item);
    return deferredAt == null ? null : deferredAt + EXPLORATION_RETRY_BACKOFF_MS;
  }).filter((retryAt) => Number.isFinite(retryAt) && retryAt > now);
  return retries.length ? new Date(Math.min(...retries)).toISOString() : null;
}

export function settleIssuedExplorationAttention(attention) {
  const cursor = attention && typeof attention === 'object'
    ? ISSUED_EXPLORATION_ATTENTION.get(attention) : null;
  if (!cursor) return false;
  ISSUED_EXPLORATION_ATTENTION.delete(attention);
  const selectedAt = nowIso();
  const retryResult = 'Na twee afgeronde aandachtbeurten is geen nieuwe geverifieerde waarneming gevonden; deze richting is inconclusief afgesloten.';
  const retryDecision = 'Deze richting is gestopt en bewust laten vallen; alleen nieuw extern bewijs kan een nieuwe, inhoudelijk andere cyclus rechtvaardigen.';
  const result = cursor.retryAttempt
    ? db.prepare(`UPDATE eva_explorations
      SET updated_ts=?,status='inconclusive',result=?,decision=?,next_wake_ts=NULL,
        last_selected_ts=?,scheduler_deferred_ts=NULL,revision=revision+1
      WHERE id=? AND created_ts=? AND source='self' AND source=? AND status=?
        AND revision=? AND updated_ts=? AND next_wake_ts IS ? AND last_selected_ts IS ?
        AND scheduler_deferred_ts IS ? AND question=?`).run(
      selectedAt, retryResult, retryDecision, selectedAt,
      cursor.id, cursor.createdTs, cursor.source, cursor.status,
      cursor.revision, cursor.updatedTs, cursor.nextWakeTs, cursor.lastSelectedTs,
      cursor.schedulerDeferredTs, cursor.question,
    )
    : db.prepare(`UPDATE eva_explorations
      SET last_selected_ts=?,scheduler_deferred_ts=?,revision=revision+1
      WHERE id=? AND created_ts=? AND source='self' AND source=? AND status=?
        AND revision=? AND updated_ts=? AND next_wake_ts IS ? AND last_selected_ts IS ?
        AND scheduler_deferred_ts IS ? AND question=?`).run(
      selectedAt, selectedAt,
      cursor.id, cursor.createdTs, cursor.source, cursor.status,
      cursor.revision, cursor.updatedTs, cursor.nextWakeTs, cursor.lastSelectedTs,
      cursor.schedulerDeferredTs, cursor.question,
    );
  return result.changes === 1;
}

export function settleIssuedAspirationAttention(attention) {
  const cursor = attention && typeof attention === 'object'
    ? ISSUED_ASPIRATION_ATTENTION.get(attention) : null;
  if (!cursor) return false;
  ISSUED_ASPIRATION_ATTENTION.delete(attention);
  const result = db.prepare(`UPDATE aspirations SET last_touched=?
    WHERE id=? AND status='actief' AND created_ts=? AND text=? AND last_touched IS ?`).run(
    new Date().toISOString(), cursor.id, cursor.createdTs, cursor.text, cursor.lastTouched,
  );
  return result.changes === 1;
}

export const __curiosityTest = {
  aspirationDue, aspirationCadenceHours, aspirationDueTs, compareDueAspirations,
  isRelationshipExploration,
  explorationIdentity, sameExplorationTopic, explorationSafetyConflict, deriveTopicFeatures,
};
