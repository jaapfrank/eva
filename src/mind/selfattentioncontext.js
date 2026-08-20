import { createHash } from 'node:crypto';
import { redactSensitiveText } from '../security/redaction.js';

export const EXPLORATION_CONTINUATION_CONTRACT = 'ExplorationContinuationContext/v1';

const ACTIVE_EXPLORATION_STATES = new Set([
  'chosen', 'running', 'waiting', 'evaluating',
]);
const CONTEXT_KEYS = Object.freeze([
  'actionCount', 'aspirationId', 'contract', 'decision', 'expectedObservation', 'hypothesis', 'id',
  'lastEvidence', 'maxActions', 'question', 'questionSha256', 'revision', 'status',
  'stopRule',
].sort());
const PROVIDER_KEYS = Object.freeze(CONTEXT_KEYS.filter((key) => (
  !['aspirationId', 'questionSha256'].includes(key)
)));
const EVIDENCE_KEYS = Object.freeze(['summary', 'verified']);
const MAX_CONTEXT_BYTES = 7_500;
const TEXT_LIMITS = Object.freeze({
  question: 1_200,
  hypothesis: 1_200,
  expectedObservation: 1_200,
  stopRule: 1_200,
  decision: 800,
  evidence: 900,
});
const ISSUED_EXPLORATION_CONTEXTS = new WeakMap();
const CONTEXT_PATH = /(?:[A-Za-z]:\\|\/(?:home|opt|srv|tmp|var)\/|data[\\/]workspace[\\/])[^\s"'`<>[\]{}(),;]+/giu;

const sha256 = (value) => createHash('sha256').update(String(value)).digest('hex');
const exactKeys = (value, keys) => value && typeof value === 'object'
  && !Array.isArray(value)
  && JSON.stringify(Object.keys(value).sort()) === JSON.stringify(keys);
const boundedText = (value, limit) => String(value ?? '')
  .replace(/\s+/gu, ' ').trim().slice(0, limit);
const jsonBytes = (value) => {
  try { return Buffer.byteLength(JSON.stringify(value), 'utf8'); } catch { return Infinity; }
};

function lastEvidence(value) {
  const rows = Array.isArray(value) ? value : [];
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const row = rows[index];
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue;
    const raw = typeof row.text === 'string' && row.text.trim()
      ? row.text : typeof row.evidence === 'string' ? row.evidence : '';
    const summary = boundedText(raw, TEXT_LIMITS.evidence);
    if (summary) return Object.freeze({ summary, verified: row.verified === true });
  }
  return null;
}

export function validExplorationContinuationContext(value) {
  if (!exactKeys(value, CONTEXT_KEYS) || !Object.isFrozen(value)
    || value.contract !== EXPLORATION_CONTINUATION_CONTRACT
    || !Number.isSafeInteger(value.id) || value.id < 1
    || !Number.isSafeInteger(value.revision) || value.revision < 1
    || (value.aspirationId !== null
      && (!Number.isSafeInteger(value.aspirationId) || value.aspirationId < 1))
    || !ACTIVE_EXPLORATION_STATES.has(value.status)
    || !Number.isSafeInteger(value.actionCount) || value.actionCount < 0
    || !Number.isSafeInteger(value.maxActions) || value.maxActions < 2
    || value.maxActions > 12 || value.actionCount > value.maxActions
    || typeof value.question !== 'string' || !value.question
    || value.question.length > TEXT_LIMITS.question
    || typeof value.hypothesis !== 'string' || !value.hypothesis
    || value.hypothesis.length > TEXT_LIMITS.hypothesis
    || typeof value.expectedObservation !== 'string' || !value.expectedObservation
    || value.expectedObservation.length > TEXT_LIMITS.expectedObservation
    || typeof value.stopRule !== 'string' || !value.stopRule
    || value.stopRule.length > TEXT_LIMITS.stopRule
    || (value.decision !== null && (typeof value.decision !== 'string'
      || !value.decision || value.decision.length > TEXT_LIMITS.decision))
    || !/^[a-f0-9]{64}$/u.test(value.questionSha256)
    || value.questionSha256 !== sha256(value.question)
    || jsonBytes(value) > MAX_CONTEXT_BYTES) return false;
  if (value.lastEvidence === null) return true;
  return exactKeys(value.lastEvidence, EVIDENCE_KEYS)
    && Object.isFrozen(value.lastEvidence)
    && typeof value.lastEvidence.summary === 'string'
    && Boolean(value.lastEvidence.summary)
    && value.lastEvidence.summary.length <= TEXT_LIMITS.evidence
    && typeof value.lastEvidence.verified === 'boolean';
}

function explorationContinuationContext(exploration) {
  if (!exploration || typeof exploration !== 'object' || Array.isArray(exploration)) return null;
  const question = boundedText(exploration.question, TEXT_LIMITS.question);
  const context = Object.freeze({
    contract: EXPLORATION_CONTINUATION_CONTRACT,
    id: Number(exploration.id),
    aspirationId: exploration.aspiration_id == null ? null : Number(exploration.aspiration_id),
    revision: Number(exploration.revision),
    question,
    questionSha256: sha256(question),
    status: String(exploration.status || ''),
    actionCount: Number(exploration.action_count),
    maxActions: Number(exploration.max_actions),
    hypothesis: boundedText(exploration.hypothesis, TEXT_LIMITS.hypothesis),
    expectedObservation: boundedText(
      exploration.expected_observation, TEXT_LIMITS.expectedObservation,
    ),
    stopRule: boundedText(exploration.stop_rule, TEXT_LIMITS.stopRule),
    lastEvidence: lastEvidence(exploration.evidence),
    decision: boundedText(exploration.decision, TEXT_LIMITS.decision) || null,
  });
  return validExplorationContinuationContext(context) ? context : null;
}

export function createIssuedExplorationAttention(exploration) {
  const context = explorationContinuationContext(exploration);
  if (!context) return null;
  const attention = Object.freeze({
    kind: 'exploration', id: context.id, text: context.question,
  });
  ISSUED_EXPLORATION_CONTEXTS.set(attention, context);
  return attention;
}

export function issuedExplorationContinuationContext(attention) {
  const context = attention && typeof attention === 'object'
    ? ISSUED_EXPLORATION_CONTEXTS.get(attention) : null;
  return validExplorationContinuationContext(context) ? context : null;
}

export function providerExplorationContinuationContext(value) {
  if (!validExplorationContinuationContext(value)) return null;
  // Dit is beschrijvende continuïteit, geen typed artifactReceipt. Laat een
  // eerder genoemd lokaal pad daarom niet als kopieerbare pseudo-binding mee.
  const redact = (text, limit) => boundedText(
    redactSensitiveText(text).replace(CONTEXT_PATH, '[AFGESCHERMD:PAD]'), limit,
  );
  const projected = Object.freeze({
    contract: value.contract,
    id: value.id,
    revision: value.revision,
    question: redact(value.question, TEXT_LIMITS.question),
    status: value.status,
    actionCount: value.actionCount,
    maxActions: value.maxActions,
    hypothesis: redact(value.hypothesis, TEXT_LIMITS.hypothesis),
    expectedObservation: redact(
      value.expectedObservation, TEXT_LIMITS.expectedObservation,
    ),
    stopRule: redact(value.stopRule, TEXT_LIMITS.stopRule),
    lastEvidence: value.lastEvidence === null ? null : Object.freeze({
      summary: redact(value.lastEvidence.summary, TEXT_LIMITS.evidence),
      verified: value.lastEvidence.verified,
    }),
    decision: value.decision === null
      ? null : redact(value.decision, TEXT_LIMITS.decision),
  });
  return exactKeys(projected, PROVIDER_KEYS) && jsonBytes(projected) <= MAX_CONTEXT_BYTES
    ? projected : null;
}
