import { createHash } from 'node:crypto';
import { allPeople, consumeContributionMutationAuthority, db } from '../db.js';
import { redactSensitiveText } from '../security/redaction.js';
import { canonicalJson } from '../util.js';
import { householdMembers } from './household.js';

const EXPERIMENT_CONTRACT = 'BehaviorLearningExperiment/v1';
const CANDIDATE_CONTRACT = 'BehaviorLensCandidate/v2';
const PROPOSAL_CONTRACT = 'BehaviorLearningProposal/v2';
const RENDERER_CONTRACT = 'BehaviorLensRenderer/v1';
const EVENT_ACTIONS = new Set([
  'proposed', 'preflight_passed', 'canary_prepared', 'canary_exposed',
  'retained', 'retention_confirmed', 'rolled_back', 'inconclusive', 'rejected',
  'deactivated',
]);
const TERMINAL = new Set(['rolled_back', 'inconclusive', 'rejected', 'deactivated']);
const CANDIDATE_FIELDS = [
  'contextScope', 'dimension', 'direction', 'gapSummary', 'hypothesis',
  'intensity', 'motivation', 'person',
];
const LENS_DIMENSIONS = new Set([
  'warmth', 'playfulness', 'directness', 'independent_voice', 'informality', 'concision',
]);
const LENS_DIRECTIONS = new Set(['increase', 'decrease']);
const LENS_INTENSITIES = new Set(['subtle', 'moderate', 'pronounced']);
const LENS_SCOPES = new Set([
  'any_direct_reply', 'social_chat', 'answering_question', 'collaborative_task',
]);
const EXPECTED_OUTCOME_KIND = 'direct_style_appreciation';
const NEGATIVE_OUTCOME_KIND = 'direct_style_correction';
const STYLE_OUTCOME_CLASSIFIER_CONTRACT = 'RelationshipStyleOutcomeClassifier/v1';
const RETAINED_DAYS = 14;
const RETAINED_DELIVERY_LIMIT = 12;
const BEHAVIOR_TABLES = Object.freeze([
  'eva_behavior_canary_deliveries', 'eva_behavior_experiment_events',
  'eva_behavior_experiments', 'eva_behavior_feedback_settlements',
]);
const BEHAVIOR_TRIGGERS = Object.freeze([
  'eva_behavior_canary_deliveries_no_delete',
  'eva_behavior_canary_deliveries_valid_update',
  'eva_behavior_experiment_events_no_delete',
  'eva_behavior_experiment_events_no_update',
  'eva_behavior_experiments_immutable_candidate',
  'eva_behavior_experiments_no_delete',
  'eva_behavior_experiments_valid_transition',
  'eva_behavior_feedback_settlements_no_delete',
  'eva_behavior_feedback_settlements_no_update',
]);
const PROPOSAL_VERIFIERS = [];
const FEEDBACK_VERIFIERS = [];
const DELIVERY_VERIFIERS = [];
const CANARY_CLAIMS = new WeakMap();
const ACTIVE_CANARY_CLAIMS = new Set();
const CONSUMED_CANARY_CLAIMS = new WeakSet();
const DELIVERY_CAPABILITIES = new WeakMap();
const CONSUMED_DELIVERY_CAPABILITIES = new WeakSet();
// better-sqlite3 kan in checkJs geen domeinrijvorm uit SQL afleiden. Houd de
// dynamische grens hier lokaal; alle projecties en capabilities worden direct
// daarna expliciet gevalideerd voordat ze authority of promptcontext krijgen.
const behaviorDb = /** @type {any} */ (db);
const sha256 = (value) => createHash('sha256').update(String(value ?? '')).digest('hex');
const nowIso = () => new Date().toISOString();

const BEHAVIOR_SCHEMA_SQL = `
  CREATE TABLE IF NOT EXISTS eva_behavior_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_key TEXT NOT NULL UNIQUE,
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    person_id INTEGER NOT NULL,
    source_kind TEXT NOT NULL,
    source_outcome_key TEXT NOT NULL,
    source_evidence_sha256 TEXT NOT NULL,
    gap_summary TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    motivation TEXT NOT NULL,
    lens_dimension TEXT NOT NULL,
    lens_direction TEXT NOT NULL,
    lens_intensity TEXT NOT NULL,
    context_scope TEXT NOT NULL,
    renderer_contract TEXT NOT NULL,
    expected_outcome_kind TEXT NOT NULL,
    negative_outcome_kind TEXT NOT NULL,
    candidate_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    preflight_receipt_sha256 TEXT NOT NULL,
    outcome_class TEXT,
    settled_ts TEXT,
    retained_until_ts TEXT,
    retained_delivery_limit INTEGER,
    deactivated_ts TEXT,
    deactivation_reason TEXT,
    CHECK (source_kind IN ('self_initiated','relationship_prompted')),
    CHECK (lens_dimension IN ('warmth','playfulness','directness','independent_voice','informality','concision')),
    CHECK (lens_direction IN ('increase','decrease')),
    CHECK (lens_intensity IN ('subtle','moderate','pronounced')),
    CHECK (context_scope IN ('any_direct_reply','social_chat','answering_question','collaborative_task')),
    CHECK (renderer_contract='BehaviorLensRenderer/v1'),
    CHECK (expected_outcome_kind='direct_style_appreciation'),
    CHECK (negative_outcome_kind='direct_style_correction'),
    CHECK (status IN ('canary_pending','canary_exposed','retained','rolled_back','inconclusive','rejected','deactivated')),
    CHECK (revision >= 2),
    CHECK (outcome_class IS NULL OR outcome_class IN ('helped','neutral','harmed','insufficient')),
    CHECK (retained_delivery_limit IS NULL OR retained_delivery_limit BETWEEN 1 AND 24),
    CHECK (deactivation_reason IS NULL OR deactivation_reason IN ('lease_expired','delivery_budget_exhausted','direct_style_correction'))
  );
  CREATE INDEX IF NOT EXISTS idx_behavior_experiments_person_status
    ON eva_behavior_experiments(person_id,status,id);
  CREATE TRIGGER IF NOT EXISTS eva_behavior_experiments_immutable_candidate
    BEFORE UPDATE ON eva_behavior_experiments
    WHEN NEW.experiment_key IS NOT OLD.experiment_key
      OR NEW.created_ts IS NOT OLD.created_ts
      OR NEW.person_id IS NOT OLD.person_id
      OR NEW.source_kind IS NOT OLD.source_kind
      OR NEW.source_outcome_key IS NOT OLD.source_outcome_key
      OR NEW.source_evidence_sha256 IS NOT OLD.source_evidence_sha256
      OR NEW.gap_summary IS NOT OLD.gap_summary
      OR NEW.hypothesis IS NOT OLD.hypothesis
      OR NEW.motivation IS NOT OLD.motivation
      OR NEW.lens_dimension IS NOT OLD.lens_dimension
      OR NEW.lens_direction IS NOT OLD.lens_direction
      OR NEW.lens_intensity IS NOT OLD.lens_intensity
      OR NEW.context_scope IS NOT OLD.context_scope
      OR NEW.renderer_contract IS NOT OLD.renderer_contract
      OR NEW.expected_outcome_kind IS NOT OLD.expected_outcome_kind
      OR NEW.negative_outcome_kind IS NOT OLD.negative_outcome_kind
      OR NEW.candidate_sha256 IS NOT OLD.candidate_sha256
      OR NEW.preflight_receipt_sha256 IS NOT OLD.preflight_receipt_sha256
    BEGIN
      SELECT RAISE(ABORT,'behavior learning candidates are immutable');
    END;
  CREATE TRIGGER IF NOT EXISTS eva_behavior_experiments_valid_transition
    BEFORE UPDATE ON eva_behavior_experiments
    WHEN NOT (
      OLD.status='canary_pending' AND NEW.status IN ('canary_exposed','rejected')
        AND NEW.revision=OLD.revision+1
        AND ((NEW.status='canary_exposed' AND NEW.outcome_class IS NULL AND NEW.settled_ts IS NULL)
          OR (NEW.status='rejected' AND NEW.outcome_class='insufficient' AND NEW.settled_ts IS NOT NULL))
      OR OLD.status='canary_exposed'
        AND NEW.status IN ('retained','rolled_back','inconclusive')
        AND NEW.revision=OLD.revision+1 AND NEW.settled_ts IS NOT NULL
        AND ((NEW.status='retained' AND NEW.outcome_class='helped')
          OR (NEW.status='rolled_back' AND NEW.outcome_class IN ('neutral','harmed'))
          OR (NEW.status='inconclusive' AND NEW.outcome_class='insufficient'))
      OR OLD.status='retained' AND NEW.status='deactivated'
        AND NEW.revision=OLD.revision+1 AND NEW.outcome_class=OLD.outcome_class
        AND NEW.settled_ts=OLD.settled_ts AND NEW.deactivated_ts IS NOT NULL
        AND NEW.deactivation_reason IN ('lease_expired','delivery_budget_exhausted','direct_style_correction')
    )
    BEGIN
      SELECT RAISE(ABORT,'invalid behavior learning transition');
    END;
  CREATE TRIGGER IF NOT EXISTS eva_behavior_experiments_no_delete
    BEFORE DELETE ON eva_behavior_experiments BEGIN
      SELECT RAISE(ABORT,'behavior learning experiments cannot be deleted');
    END;

  CREATE TABLE IF NOT EXISTS eva_behavior_experiment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    experiment_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    from_revision INTEGER NOT NULL,
    to_revision INTEGER NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    outcome_class TEXT,
    CHECK (action IN ('proposed','preflight_passed','canary_prepared','canary_exposed','retained','retention_confirmed','rolled_back','inconclusive','rejected','deactivated')),
    CHECK (outcome_class IS NULL OR outcome_class IN ('helped','neutral','harmed','insufficient'))
  );
  CREATE INDEX IF NOT EXISTS idx_behavior_experiment_events_experiment
    ON eva_behavior_experiment_events(experiment_id,id);
  CREATE TRIGGER IF NOT EXISTS eva_behavior_experiment_events_no_update
    BEFORE UPDATE ON eva_behavior_experiment_events BEGIN
      SELECT RAISE(ABORT,'behavior learning events are append-only');
    END;
  CREATE TRIGGER IF NOT EXISTS eva_behavior_experiment_events_no_delete
    BEFORE DELETE ON eva_behavior_experiment_events BEGIN
      SELECT RAISE(ABORT,'behavior learning events are append-only');
    END;

  CREATE TABLE IF NOT EXISTS eva_behavior_canary_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_sha256 TEXT NOT NULL UNIQUE,
    experiment_id INTEGER NOT NULL,
    exposure_kind TEXT NOT NULL,
    exposure_index INTEGER NOT NULL,
    prepared_ts TEXT NOT NULL,
    delivered_ts TEXT,
    turn_id TEXT NOT NULL,
    conversation_sha256 TEXT NOT NULL,
    local_message_id TEXT,
    external_reply_ref_sha256 TEXT,
    reply_sha256 TEXT NOT NULL,
    rendered_lens_sha256 TEXT NOT NULL,
    effective_intensity TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE (experiment_id,exposure_kind,exposure_index),
    CHECK (exposure_kind IN ('canary','retained')),
    CHECK ((exposure_kind='canary' AND exposure_index=0) OR (exposure_kind='retained' AND exposure_index>=1)),
    CHECK (effective_intensity IN ('subtle','moderate','pronounced')),
    CHECK (status IN ('prepared','delivered'))
  );
  CREATE TRIGGER IF NOT EXISTS eva_behavior_canary_deliveries_no_delete
    BEFORE DELETE ON eva_behavior_canary_deliveries BEGIN
      SELECT RAISE(ABORT,'behavior canary deliveries are append-only');
    END;
  CREATE TRIGGER IF NOT EXISTS eva_behavior_canary_deliveries_valid_update
    BEFORE UPDATE ON eva_behavior_canary_deliveries
    WHEN OLD.status != 'prepared' OR NEW.status != 'delivered'
      OR NEW.binding_sha256 IS NOT OLD.binding_sha256
      OR NEW.experiment_id IS NOT OLD.experiment_id
      OR NEW.exposure_kind IS NOT OLD.exposure_kind
      OR NEW.exposure_index IS NOT OLD.exposure_index
      OR NEW.prepared_ts IS NOT OLD.prepared_ts
      OR NEW.turn_id IS NOT OLD.turn_id
      OR NEW.conversation_sha256 IS NOT OLD.conversation_sha256
      OR NEW.reply_sha256 IS NOT OLD.reply_sha256
      OR NEW.rendered_lens_sha256 IS NOT OLD.rendered_lens_sha256
      OR NEW.effective_intensity IS NOT OLD.effective_intensity
      OR NEW.delivered_ts IS NULL
    BEGIN
      SELECT RAISE(ABORT,'behavior canary delivery transition is immutable');
    END;

  CREATE TABLE IF NOT EXISTS eva_behavior_feedback_settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id INTEGER,
    experiment_id INTEGER NOT NULL,
    delivery_id INTEGER NOT NULL UNIQUE,
    binding_sha256 TEXT NOT NULL UNIQUE,
    inbound_message_sha256 TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL,
    conversation_sha256 TEXT NOT NULL,
    inbound_content_sha256 TEXT NOT NULL,
    classifier_contract TEXT NOT NULL,
    outcome_type TEXT NOT NULL,
    created_ts TEXT NOT NULL
    ,CHECK (classifier_contract='RelationshipStyleOutcomeClassifier/v1')
    ,CHECK (outcome_type IN ('direct_style_appreciation','direct_style_correction','no_direct_style_evaluation'))
  );
  CREATE TRIGGER IF NOT EXISTS eva_behavior_feedback_settlements_no_update
    BEFORE UPDATE ON eva_behavior_feedback_settlements BEGIN
      SELECT RAISE(ABORT,'behavior feedback settlements are append-only');
    END;
  CREATE TRIGGER IF NOT EXISTS eva_behavior_feedback_settlements_no_delete
    BEFORE DELETE ON eva_behavior_feedback_settlements BEGIN
      SELECT RAISE(ABORT,'behavior feedback settlements are append-only');
    END;
`;

function exactColumns(table, expected) {
  const actual = behaviorDb.prepare(`PRAGMA table_info('${table}')`).all();
  const byName = new Map(actual.map((column) => [column.name, column]));
  return actual.length === expected.length && expected.every((name) => byName.has(name));
}

function normalizedSchemaSql(value) {
  return String(value || '').replace(/\bIF\s+NOT\s+EXISTS\b/giu, '')
    .replace(/\s+/gu, ' ').replace(/\s*([(),=])\s*/gu, '$1')
    .replace(/;+$/u, '').trim().toLocaleLowerCase('en-US');
}

function expectedTableSql(table) {
  const escaped = table.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
  const match = BEHAVIOR_SCHEMA_SQL.match(new RegExp(
    `CREATE TABLE IF NOT EXISTS ${escaped}\\s*\\(([\\s\\S]*?)\\n\\s*\\);`, 'u',
  ));
  return match ? normalizedSchemaSql(`CREATE TABLE ${table} (${match[1]})`) : '';
}

function exactTableSql(table) {
  const row = behaviorDb.prepare(`SELECT sql FROM sqlite_master
    WHERE type='table' AND name=?`).get(table);
  return normalizedSchemaSql(row?.sql) === expectedTableSql(table);
}

function expectedTriggerSql(trigger) {
  const escaped = trigger.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
  const match = BEHAVIOR_SCHEMA_SQL.match(new RegExp(
    `CREATE TRIGGER IF NOT EXISTS ${escaped}([\\s\\S]*?\\n\\s*END);`, 'u',
  ));
  return match ? normalizedSchemaSql(`CREATE TRIGGER ${trigger}${match[1]}`) : '';
}

function exactTriggerSql(trigger, actual) {
  return normalizedSchemaSql(actual) === expectedTriggerSql(trigger);
}

function uniqueIndexSignatures(table) {
  return behaviorDb.prepare(`PRAGMA index_list('${table}')`).all()
    .filter((index) => Number(index.unique) === 1)
    .map((index) => behaviorDb.prepare(`PRAGMA index_info('${index.name}')`).all()
      .map((field) => field.name).join('\u0000'))
    .sort();
}

function exactUniqueIndexes(table, expected) {
  return canonicalJson(uniqueIndexSignatures(table))
    === canonicalJson(expected.map((columns) => columns.join('\u0000')).sort());
}

function schemaReady() {
  try {
    const experimentColumns = [
      'id', 'experiment_key', 'created_ts', 'updated_ts', 'person_id', 'source_kind',
      'source_outcome_key', 'source_evidence_sha256', 'gap_summary', 'hypothesis',
      'motivation', 'lens_dimension', 'lens_direction', 'lens_intensity',
      'context_scope', 'renderer_contract', 'expected_outcome_kind',
      'negative_outcome_kind', 'candidate_sha256', 'status', 'revision',
      'preflight_receipt_sha256', 'outcome_class', 'settled_ts', 'retained_until_ts',
      'retained_delivery_limit', 'deactivated_ts', 'deactivation_reason',
    ];
    const eventColumns = [
      'id', 'event_key', 'request_sha256', 'experiment_id', 'ts', 'action',
      'from_status', 'to_status', 'from_revision', 'to_revision', 'evidence_sha256',
      'outcome_class',
    ];
    const deliveryColumns = [
      'id', 'binding_sha256', 'experiment_id', 'exposure_kind', 'exposure_index',
      'prepared_ts', 'delivered_ts', 'turn_id', 'conversation_sha256',
      'local_message_id', 'external_reply_ref_sha256', 'reply_sha256',
      'rendered_lens_sha256', 'effective_intensity', 'status',
    ];
    const settlementColumns = [
      'id', 'feedback_id', 'experiment_id', 'delivery_id', 'binding_sha256',
      'inbound_message_sha256', 'person_id', 'conversation_sha256',
      'inbound_content_sha256', 'classifier_contract', 'outcome_type', 'created_ts',
    ];
    const triggers = new Map(behaviorDb.prepare(`SELECT name,sql FROM sqlite_master
      WHERE type='trigger' AND name LIKE 'eva_behavior_%'`).all()
      .map((row) => [row.name, String(row.sql || '')]));
    const tables = behaviorDb.prepare(`SELECT name FROM sqlite_master
      WHERE type='table' AND name LIKE 'eva_behavior_%' ORDER BY name`).all()
      .map((row) => row.name);
    return canonicalJson(tables) === canonicalJson(BEHAVIOR_TABLES)
      && canonicalJson([...triggers.keys()].sort()) === canonicalJson(BEHAVIOR_TRIGGERS)
      && BEHAVIOR_TABLES.every(exactTableSql)
      && BEHAVIOR_TRIGGERS.every((trigger) => exactTriggerSql(trigger, triggers.get(trigger)))
      && exactColumns('eva_behavior_experiments', experimentColumns)
      && exactColumns('eva_behavior_experiment_events', eventColumns)
      && exactColumns('eva_behavior_canary_deliveries', deliveryColumns)
      && exactColumns('eva_behavior_feedback_settlements', settlementColumns)
      && exactUniqueIndexes('eva_behavior_experiments', [['experiment_key']])
      && exactUniqueIndexes('eva_behavior_experiment_events', [['event_key']])
      && exactUniqueIndexes('eva_behavior_canary_deliveries', [
        ['binding_sha256'], ['experiment_id', 'exposure_kind', 'exposure_index'],
      ])
      && exactUniqueIndexes('eva_behavior_feedback_settlements', [
        ['delivery_id'], ['binding_sha256'], ['inbound_message_sha256'],
      ]);
  } catch { return false; }
}

let behaviorLearningEnabled = false;
let behaviorLearningReason = 'not_initialized';
try {
  const existing = behaviorDb.prepare(`SELECT name FROM sqlite_master
    WHERE type='table' AND name LIKE 'eva_behavior_%'`).all()
    .map((row) => row.name);
  if (!existing.length) behaviorDb.transaction(() => behaviorDb.exec(BEHAVIOR_SCHEMA_SQL)).immediate();
  behaviorLearningEnabled = schemaReady();
  behaviorLearningReason = behaviorLearningEnabled ? 'ready' : 'schema_mismatch';
} catch {
  behaviorLearningEnabled = false;
  behaviorLearningReason = 'schema_initialization_failed';
}

export function behaviorLearningAvailable() { return behaviorLearningEnabled; }

function disableBehaviorLearningOnStorageFailure(error) {
  if (!new Set([
    'SQLITE_ERROR', 'SQLITE_SCHEMA', 'SQLITE_CORRUPT', 'SQLITE_NOTADB',
    'SQLITE_IOERR', 'SQLITE_CANTOPEN', 'SQLITE_READONLY', 'SQLITE_FULL',
  ]).has(String(error?.code || ''))) return false;
  behaviorLearningEnabled = false;
  behaviorLearningReason = 'runtime_storage_error';
  return true;
}

function cleanExact(value, { min = 8, max = 700 } = {}) {
  if (typeof value !== 'string' || value !== value.trim()
    || value.length < min || value.length > max
    || redactSensitiveText(value) !== value) return null;
  const compact = value.replace(/\s+/gu, ' ');
  return compact.length >= min && compact.length <= max ? compact : null;
}

function canonicalLensSpec(candidate) {
  return canonicalJson({
    contract: 'BehaviorLensSpec/v2', dimension: candidate.dimension,
    direction: candidate.direction, intensity: candidate.intensity,
    contextScope: candidate.contextScope,
    expectedOutcomeKind: EXPECTED_OUTCOME_KIND,
    negativeOutcomeKind: NEGATIVE_OUTCOME_KIND,
  });
}

function renderBehaviorLens(spec) {
  const movement = {
    warmth: spec?.direction === 'increase'
      ? 'Laat de formulering warmer en meer betrokken klinken.'
      : 'Laat de formulering neutraler en minder affectief klinken, zonder kil te worden.',
    playfulness: spec?.direction === 'increase'
      ? 'Geef de formulering meer speelsheid; humor mag alleen als die natuurlijk past en is nooit verplicht.'
      : 'Houd de formulering rustiger en minder speels.',
    directness: spec?.direction === 'increase'
      ? 'Formuleer duidelijker en directer, zonder bot te worden.'
      : 'Formuleer voorzichtiger en minder stellig, zonder vaag te worden.',
    independent_voice: spec?.direction === 'increase'
      ? "Laat Eva's eigen afweging duidelijker meeklinken, zonder feiten te verzinnen of de keuze van de bewoner over te nemen."
      : "Zet Eva's eigen oordeel minder op de voorgrond en leg meer nadruk op de vraag van de bewoner.",
    informality: spec?.direction === 'increase'
      ? 'Schrijf losser en alledaagser, zonder vaste stopwoorden of aanspreekvorm.'
      : 'Schrijf iets formeler en zakelijker, maar nog steeds menselijk.',
    concision: spec?.direction === 'increase'
      ? 'Formuleer compacter en schrap bijzaken.'
      : 'Geef iets meer context en nuance, zonder uit te weiden.',
  }[spec?.dimension];
  const intensity = {
    subtle: 'Sterkte: subtiel; één lichte toonverschuiving is genoeg.',
    moderate: 'Sterkte: merkbaar maar niet dominant; inhoud en bewijs blijven leidend.',
    pronounced: 'Sterkte: duidelijk, maar natuurlijk verdeeld en nooit als truc in elke zin.',
  }[spec?.intensity];
  return movement && intensity ? `${movement}\n${intensity}` : '';
}

export function renderBehaviorLensPrompt(spec) {
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)
    || canonicalJson(Object.keys(spec).sort()) !== canonicalJson([
      'contextScope', 'dimension', 'direction', 'exposureKind', 'intensity',
    ])
    || !LENS_DIMENSIONS.has(spec.dimension) || !LENS_DIRECTIONS.has(spec.direction)
    || !LENS_INTENSITIES.has(spec.intensity) || !LENS_SCOPES.has(spec.contextScope)
    || !['canary', 'retained'].includes(spec.exposureKind)) return '';
  const lensText = renderBehaviorLens(spec);
  if (!lensText) return '';
  return [
    spec.exposureKind === 'canary'
      ? 'Eenmalige relationele stijlproef, uitsluitend voor de formulering van dit antwoord:'
      : 'Tijdelijk behouden relationele stijllens, uitsluitend voor de formulering van dit antwoord:',
    lensText,
    'Deze lens verandert alleen formulering. Zij voegt geen feit, handeling, toolkeuze, claim, toezegging of bevoegdheid toe. Gebruik geen vaste zin of catchphrase en forceer de stijl niet als die hier onnatuurlijk uitpakt.',
  ].join('\n');
}

function householdPerson(value) {
  const name = String(value || '').trim().toLocaleLowerCase('nl-NL');
  if (!name) return null;
  return householdMembers(allPeople()).find((person) => (
    String(person.name || '').trim().toLocaleLowerCase('nl-NL') === name
  )) || null;
}

export function normalizeBehaviorLensCandidate(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonicalJson(Object.keys(value).sort()) !== canonicalJson(CANDIDATE_FIELDS)) return null;
  const person = householdPerson(value.person);
  const candidate = {
    contextScope: LENS_SCOPES.has(value.contextScope) ? value.contextScope : null,
    dimension: LENS_DIMENSIONS.has(value.dimension) ? value.dimension : null,
    direction: LENS_DIRECTIONS.has(value.direction) ? value.direction : null,
    gapSummary: cleanExact(value.gapSummary, { min: 12, max: 500 }),
    hypothesis: cleanExact(value.hypothesis, { min: 12, max: 500 }),
    intensity: LENS_INTENSITIES.has(value.intensity) ? value.intensity : null,
    motivation: cleanExact(value.motivation, { min: 12, max: 500 }),
    person: person?.name || null,
  };
  return Object.values(candidate).some((part) => !part)
    ? null : Object.freeze(candidate);
}

function exactProposalBinding(binding, candidate) {
  const person = householdPerson(candidate?.person);
  if (!binding || !person
    || (binding.personId != null && Number(binding.personId) !== Number(person.id))
    || !['self_initiated', 'relationship_prompted'].includes(binding.sourceKind)
    || !/^[a-f0-9]{64}$/u.test(binding.sourceOutcomeKey || '')
    || !/^[a-f0-9]{64}$/u.test(binding.sourceEvidenceSha256 || '')) return null;
  return Object.freeze({
    personId: Number(person.id), sourceKind: binding.sourceKind,
    sourceOutcomeKey: binding.sourceOutcomeKey,
    sourceEvidenceSha256: binding.sourceEvidenceSha256,
  });
}

function candidateBody(candidate, binding) {
  return Object.freeze({
    contract: CANDIDATE_CONTRACT,
    personId: Number(binding.personId),
    sourceKind: binding.sourceKind,
    sourceOutcomeKey: binding.sourceOutcomeKey,
    sourceEvidenceSha256: binding.sourceEvidenceSha256,
    ...candidate,
  });
}

/** Registreer exact één lifecycle-eigen verifier. Zij moet een onforgeerbare,
 * modelgebonden broncontext controleren en geeft alleen hostvelden terug. */
export function registerBehaviorProposalVerifier(verifier) {
  if (typeof verifier !== 'function' || PROPOSAL_VERIFIERS.length) {
    throw new Error('behavior proposal verifier kan alleen eenmaal worden geregistreerd');
  }
  PROPOSAL_VERIFIERS.push(verifier);
}

export function registerBehaviorFeedbackVerifier(verifier) {
  if (typeof verifier !== 'function' || FEEDBACK_VERIFIERS.length) {
    throw new Error('behavior feedback verifier kan alleen eenmaal worden geregistreerd');
  }
  FEEDBACK_VERIFIERS.push(verifier);
}

export function registerBehaviorDeliveryVerifier(verifier) {
  if (typeof verifier !== 'function' || DELIVERY_VERIFIERS.length) {
    throw new Error('behavior delivery verifier kan alleen eenmaal worden geregistreerd');
  }
  DELIVERY_VERIFIERS.push(verifier);
}

function verifiedProposal(proposalContext) {
  try {
    return PROPOSAL_VERIFIERS[0]?.(proposalContext) || null;
  } catch { return null; }
}

function experimentRow(id) {
  return behaviorDb.prepare(`SELECT e.*,p.name AS person_name FROM eva_behavior_experiments e
    LEFT JOIN people p ON p.id=e.person_id WHERE e.id=?`).get(Number(id)) || null;
}

function projectedExperiment(row) {
  if (!row) return null;
  return Object.freeze({
    contract: EXPERIMENT_CONTRACT,
    id: Number(row.id), experimentKey: row.experiment_key,
    personId: Number(row.person_id), person: row.person_name || null,
    sourceKind: row.source_kind, sourceOutcomeKey: row.source_outcome_key,
    sourceEvidenceSha256: row.source_evidence_sha256,
    gapSummary: row.gap_summary, hypothesis: row.hypothesis,
    motivation: row.motivation, dimension: row.lens_dimension,
    direction: row.lens_direction, intensity: row.lens_intensity,
    contextScope: row.context_scope, rendererContract: row.renderer_contract,
    expectedOutcomeKind: row.expected_outcome_kind,
    negativeOutcomeKind: row.negative_outcome_kind,
    lensSpecSha256: sha256(canonicalLensSpec({
      dimension: row.lens_dimension, direction: row.lens_direction,
      intensity: row.lens_intensity, contextScope: row.context_scope,
    })),
    candidateSha256: row.candidate_sha256,
    status: row.status, revision: Number(row.revision),
    preflightReceiptSha256: row.preflight_receipt_sha256,
    outcomeClass: row.outcome_class || null, settledAt: row.settled_ts || null,
    retainedUntil: row.retained_until_ts || null,
    retainedDeliveryLimit: Number(row.retained_delivery_limit) || null,
    deactivatedAt: row.deactivated_ts || null,
    deactivationReason: row.deactivation_reason || null,
  });
}

function eventRequest(action, experimentId, fromStatus, toStatus,
  fromRevision, toRevision, evidenceSha256, outcomeClass) {
  return Object.freeze({
    contract: 'BehaviorLearningEventRequest/v1', action, experimentId,
    fromStatus: fromStatus || null, toStatus, fromRevision, toRevision,
    evidenceSha256, outcomeClass: outcomeClass || null,
  });
}

function insertEvent({ action, experimentId, fromStatus, toStatus,
  fromRevision, toRevision, evidenceSha256, outcomeClass = null, eventKey = null }) {
  if (!EVENT_ACTIONS.has(action) || !/^[a-f0-9]{64}$/u.test(evidenceSha256 || '')) {
    throw new Error('ongeldig behavior-learning event');
  }
  const request = eventRequest(
    action, experimentId, fromStatus, toStatus, fromRevision, toRevision,
    evidenceSha256, outcomeClass,
  );
  const requestSha256 = sha256(canonicalJson(request));
  const key = eventKey || sha256(canonicalJson({ requestSha256, experimentId, action }));
  const prior = behaviorDb.prepare('SELECT * FROM eva_behavior_experiment_events WHERE event_key=?')
    .get(key);
  if (prior) {
    if (prior.request_sha256 !== requestSha256) throw new Error('conflicterende behavior-event replay');
    return { created: false, row: prior };
  }
  const info = behaviorDb.prepare(`INSERT INTO eva_behavior_experiment_events
    (event_key,request_sha256,experiment_id,ts,action,from_status,to_status,
      from_revision,to_revision,evidence_sha256,outcome_class)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    key, requestSha256, experimentId, nowIso(), action, fromStatus, toStatus,
    fromRevision, toRevision, evidenceSha256, outcomeClass,
  );
  return { created: Number(info.changes || 0) === 1,
    row: behaviorDb.prepare('SELECT * FROM eva_behavior_experiment_events WHERE id=?')
      .get(Number(info.lastInsertRowid)) };
}

function exactProposalMeta(meta, attestation) {
  return meta?.behaviorExperiment
    && canonicalJson(meta.behaviorExperiment) === canonicalJson(attestation);
}

/** Materialiseer uitsluitend binnen de callback van een exact exploration-
 * contribution-outcome. De callbackcapability wordt vóór de eerste insert
 * geconsumeerd; fouten rollen experiment én outcome samen terug. */
export function materializeBehaviorExperiment(
  proposalContext, outcomeMutationAuthority, outcomeClaim,
) {
  if (!behaviorLearningEnabled) return null;
  if (behaviorDb.inTransaction !== true) throw new Error('behavior-proposal vereist outcome-transactie');
  const verified = verifiedProposal(proposalContext);
  const candidate = normalizeBehaviorLensCandidate(verified?.candidate);
  const binding = candidate ? exactProposalBinding(verified, candidate) : null;
  if (!candidate || !binding) {
    throw new Error('behavior-proposal mist haar exact uitgegeven modelkeuze');
  }
  const body = candidateBody(candidate, binding);
  const candidateSha256 = sha256(canonicalJson(body));
  const experimentKey = sha256(canonicalJson({
    contract: PROPOSAL_CONTRACT, sourceOutcomeKey: body.sourceOutcomeKey, candidateSha256,
  }));
  const preflight = Object.freeze({
    contract: 'BehaviorLensStructuralPreflight/v2', candidateSha256,
    authority: 'none', typedRendererOnly: true, exactPersonScope: true,
  });
  const preflightReceiptSha256 = sha256(canonicalJson(preflight));
  const attestation = Object.freeze({
    contract: PROPOSAL_CONTRACT, experimentKey, candidateSha256,
    preflightReceiptSha256, personId: body.personId,
    sourceKind: body.sourceKind, sourceOutcomeKey: body.sourceOutcomeKey,
    sourceEvidenceSha256: body.sourceEvidenceSha256,
  });
  if (!exactProposalMeta(outcomeClaim?.meta, attestation)) {
    throw new Error('behavior-proposal mist haar exacte contribution-claim');
  }
  const existing = behaviorDb.prepare('SELECT id FROM eva_behavior_experiments WHERE experiment_key=?')
    .get(experimentKey);
  if (existing) {
    const replay = projectedExperiment(experimentRow(existing.id));
    throw new Error(replay?.candidateSha256 === candidateSha256
      && replay?.sourceOutcomeKey === body.sourceOutcomeKey
      ? 'behavior-proposal bestaat zonder haar atomische contribution-outcome'
      : 'conflicterende behavior-proposal replay');
  }
  if (!consumeContributionMutationAuthority(outcomeMutationAuthority, outcomeClaim)) {
    throw new Error('behavior-proposal mist haar callback-lokale outcome-authority');
  }
  const ts = nowIso();
  const info = behaviorDb.prepare(`INSERT INTO eva_behavior_experiments
    (experiment_key,created_ts,updated_ts,person_id,source_kind,source_outcome_key,
      source_evidence_sha256,gap_summary,hypothesis,motivation,lens_dimension,
      lens_direction,lens_intensity,context_scope,renderer_contract,
      expected_outcome_kind,negative_outcome_kind,candidate_sha256,status,revision,
      preflight_receipt_sha256)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'canary_pending',2,?)`).run(
    experimentKey, ts, ts, body.personId, body.sourceKind, body.sourceOutcomeKey,
    body.sourceEvidenceSha256, body.gapSummary, body.hypothesis, body.motivation,
    body.dimension, body.direction, body.intensity, body.contextScope,
    RENDERER_CONTRACT, EXPECTED_OUTCOME_KIND, NEGATIVE_OUTCOME_KIND,
    candidateSha256, preflightReceiptSha256,
  );
  const id = Number(info.lastInsertRowid);
  insertEvent({ action: 'proposed', experimentId: id, fromStatus: null,
    toStatus: 'canary_pending', fromRevision: 0, toRevision: 1,
    evidenceSha256: body.sourceEvidenceSha256 });
  insertEvent({ action: 'preflight_passed', experimentId: id,
    fromStatus: 'canary_pending', toStatus: 'canary_pending', fromRevision: 1,
    toRevision: 2, evidenceSha256: preflightReceiptSha256 });
  return Object.freeze({ created: true, experiment: projectedExperiment(experimentRow(id)), attestation });
}

export function behaviorProposalAttestation(proposalContext) {
  if (!behaviorLearningEnabled) return null;
  const verified = verifiedProposal(proposalContext);
  const candidate = normalizeBehaviorLensCandidate(verified?.candidate);
  const binding = candidate ? exactProposalBinding(verified, candidate) : null;
  if (!candidate || !binding) return null;
  const body = candidateBody(candidate, binding);
  const candidateSha256 = sha256(canonicalJson(body));
  const experimentKey = sha256(canonicalJson({
    contract: PROPOSAL_CONTRACT, sourceOutcomeKey: body.sourceOutcomeKey, candidateSha256,
  }));
  const preflightReceiptSha256 = sha256(canonicalJson({
    contract: 'BehaviorLensStructuralPreflight/v2', candidateSha256,
    authority: 'none', typedRendererOnly: true, exactPersonScope: true,
  }));
  return Object.freeze({
    contract: PROPOSAL_CONTRACT, experimentKey, candidateSha256,
    preflightReceiptSha256, personId: body.personId,
    sourceKind: body.sourceKind, sourceOutcomeKey: body.sourceOutcomeKey,
    sourceEvidenceSha256: body.sourceEvidenceSha256,
  });
}

export function replayBehaviorExperiment(attestation) {
  if (!behaviorLearningEnabled) return null;
  if (!attestation || attestation.contract !== PROPOSAL_CONTRACT
    || !/^[a-f0-9]{64}$/u.test(attestation.experimentKey || '')) return null;
  const row = behaviorDb.prepare('SELECT id FROM eva_behavior_experiments WHERE experiment_key=?')
    .get(attestation.experimentKey);
  const result = projectedExperiment(experimentRow(row?.id));
  return result && result.candidateSha256 === attestation.candidateSha256
    && result.sourceOutcomeKey === attestation.sourceOutcomeKey
    && result.sourceKind === attestation.sourceKind
    && result.sourceEvidenceSha256 === attestation.sourceEvidenceSha256
    && result.preflightReceiptSha256 === attestation.preflightReceiptSha256
    && result.personId === Number(attestation.personId) ? result : null;
}

function conversationSha256(conversationId) {
  const value = String(conversationId || '').trim();
  return value ? sha256(value) : null;
}

function scopeMatches(scope, dialogueAct) {
  const act = String(dialogueAct || 'unknown');
  if (act === 'unknown') return false;
  if (scope === 'any_direct_reply') return true;
  if (scope === 'social_chat') return ['chat', 'inform'].includes(act);
  if (scope === 'answering_question') return act === 'question';
  return scope === 'collaborative_task'
    && ['request', 'command', 'continue', 'correct', 'confirm', 'reject', 'cancel'].includes(act);
}

function steppedIntensity(intensity, exposureIndex) {
  if (exposureIndex >= 9) return 'subtle';
  if (exposureIndex >= 5) return intensity === 'pronounced' ? 'moderate' : 'subtle';
  return intensity;
}

function deactivateRetained(current, reason, evidence, eventKey = null) {
  const changed = behaviorDb.prepare(`UPDATE eva_behavior_experiments SET
    status='deactivated',deactivated_ts=?,deactivation_reason=?,updated_ts=?,revision=revision+1
    WHERE id=? AND status='retained' AND revision=?`).run(
    nowIso(), reason, nowIso(), current.id, current.revision,
  );
  if (Number(changed.changes) !== 1) throw new Error('retained-lens deactivation CAS conflict');
  insertEvent({ action: 'deactivated', experimentId: current.id,
    fromStatus: 'retained', toStatus: 'deactivated',
    fromRevision: current.revision, toRevision: current.revision + 1,
    evidenceSha256: evidence, outcomeClass: current.outcomeClass,
    eventKey: eventKey || sha256(`deactivated:${current.id}:${reason}`) });
}

function expirePreparedBehaviorCanariesInternal({
  now = Date.now(), maxAgeMs = 60 * 60_000,
} = {}) {
  if (!behaviorLearningEnabled) return 0;
  const at = Number(now);
  const age = Math.max(60_000, Number(maxAgeMs) || 60 * 60_000);
  if (!Number.isFinite(at)) return 0;
  const cutoff = new Date(at - age).toISOString();
  return behaviorDb.transaction(() => {
    const rows = behaviorDb.prepare(`SELECT e.id FROM eva_behavior_experiments e
      WHERE e.status='canary_pending' AND e.created_ts<=?`)
      .all(cutoff);
    let changed = 0;
    for (const row of rows) {
      const current = projectedExperiment(experimentRow(row.id));
      const updated = behaviorDb.prepare(`UPDATE eva_behavior_experiments SET
        status='rejected',outcome_class='insufficient',settled_ts=?,updated_ts=?,revision=revision+1
        WHERE id=? AND status='canary_pending' AND revision=?`).run(
        nowIso(), nowIso(), current.id, current.revision,
      );
      if (Number(updated.changes) !== 1) throw new Error('prepared-canary expiry CAS conflict');
      insertEvent({ action: 'rejected', experimentId: current.id,
        fromStatus: current.status, toStatus: 'rejected',
        fromRevision: current.revision, toRevision: current.revision + 1,
        evidenceSha256: sha256(`prepared-canary-expired:${current.id}`),
        outcomeClass: 'insufficient',
        eventKey: sha256(`prepared-canary-expired:${current.id}`) });
      changed += 1;
    }
    const exposed = behaviorDb.prepare(`SELECT e.id,d.binding_sha256 FROM eva_behavior_experiments e
      JOIN eva_behavior_canary_deliveries d ON d.experiment_id=e.id
      WHERE e.status='canary_exposed' AND d.status='delivered' AND d.delivered_ts<=?`)
      .all(cutoff);
    for (const row of exposed) {
      const current = projectedExperiment(experimentRow(row.id));
      const updated = behaviorDb.prepare(`UPDATE eva_behavior_experiments SET
        status='inconclusive',outcome_class='insufficient',settled_ts=?,updated_ts=?,revision=revision+1
        WHERE id=? AND status='canary_exposed' AND revision=?`).run(
        nowIso(), nowIso(), current.id, current.revision,
      );
      if (Number(updated.changes) !== 1) throw new Error('exposed-canary expiry CAS conflict');
      insertEvent({ action: 'inconclusive', experimentId: current.id,
        fromStatus: current.status, toStatus: 'inconclusive',
        fromRevision: current.revision, toRevision: current.revision + 1,
        evidenceSha256: sha256(`exposed-canary-expired:${row.binding_sha256}`),
        outcomeClass: 'insufficient',
        eventKey: sha256(`exposed-canary-expired:${row.binding_sha256}`) });
      changed += 1;
    }
    const retained = behaviorDb.prepare(`SELECT e.id,e.retained_until_ts,
      e.retained_delivery_limit,COUNT(d.id) AS deliveries
      FROM eva_behavior_experiments e
      LEFT JOIN eva_behavior_canary_deliveries d ON d.experiment_id=e.id
        AND d.exposure_kind='retained' AND d.status='delivered'
      WHERE e.status='retained'
      GROUP BY e.id`).all();
    for (const row of retained) {
      const expired = Date.parse(row.retained_until_ts || '') <= at;
      const exhausted = Number(row.deliveries) >= Number(row.retained_delivery_limit);
      if (!expired && !exhausted) continue;
      const current = projectedExperiment(experimentRow(row.id));
      const reason = expired ? 'lease_expired' : 'delivery_budget_exhausted';
      deactivateRetained(current, reason, sha256(`${reason}:${current.id}`));
      changed += 1;
    }
    return changed;
  }).immediate();
}

export function expirePreparedBehaviorCanaries(options = {}) {
  if (!behaviorLearningEnabled) return 0;
  try { return expirePreparedBehaviorCanariesInternal(options); } catch (error) {
    if (disableBehaviorLearningOnStorageFailure(error)) return 0;
    throw error;
  }
}

/** @param {{turnId?: string,conversationId?: string,dialogueAct?: string}} [options] */
function claimBehaviorCanaryForPersonInternal(person, options = {}) {
  if (!behaviorLearningEnabled) return null;
  const { turnId, conversationId, dialogueAct } = options;
  expirePreparedBehaviorCanaries();
  if (!behaviorLearningEnabled) return null;
  const member = householdPerson(person);
  const turn = String(turnId || '').trim();
  const conversation = conversationSha256(conversationId);
  if (!member || !turn || !conversation) return null;
  const pendingRows = behaviorDb.prepare(`SELECT id FROM eva_behavior_experiments
    WHERE person_id=? AND status='canary_pending'
      AND NOT EXISTS (SELECT 1 FROM eva_behavior_canary_deliveries d
        WHERE d.experiment_id=eva_behavior_experiments.id)
    ORDER BY id`).all(member.id);
  let experiment = pendingRows.map((row) => projectedExperiment(experimentRow(row.id)))
    .find((item) => scopeMatches(item.contextScope, dialogueAct)) || null;
  let exposureKind = 'canary', exposureIndex = 0, intensityIndex = 0;
  if (!experiment) {
    const retainedRows = behaviorDb.prepare(`SELECT id FROM eva_behavior_experiments
      WHERE person_id=? AND status='retained' ORDER BY settled_ts DESC,id DESC`).all(member.id);
    experiment = retainedRows.map((row) => projectedExperiment(experimentRow(row.id)))
      .find((item) => scopeMatches(item.contextScope, dialogueAct)) || null;
    if (experiment) {
      exposureKind = 'retained';
      const deliveryStats = behaviorDb.prepare(`SELECT
        COALESCE(MAX(exposure_index),0) AS attempts,
        SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS deliveries
        FROM eva_behavior_canary_deliveries
        WHERE experiment_id=? AND exposure_kind='retained'`).get(experiment.id);
      exposureIndex = Number(deliveryStats.attempts) + 1;
      intensityIndex = Number(deliveryStats.deliveries) + 1;
      if (intensityIndex > Number(experiment.retainedDeliveryLimit)) return null;
    }
  }
  const activeKey = experiment ? `${experiment.id}:${exposureKind}:${exposureIndex}` : '';
  if (!experiment || ACTIVE_CANARY_CLAIMS.has(activeKey)) return null;
  const effectiveIntensity = steppedIntensity(
    experiment.intensity, exposureKind === 'retained' ? intensityIndex : exposureIndex,
  );
  const promptText = renderBehaviorLensPrompt({
    dimension: experiment.dimension, direction: experiment.direction,
    intensity: effectiveIntensity, contextScope: experiment.contextScope, exposureKind,
  });
  if (!promptText) return null;
  const claim = Object.freeze({});
  CANARY_CLAIMS.set(claim, {
    experimentId: experiment.id, revision: experiment.revision,
    candidateSha256: experiment.candidateSha256, personId: experiment.personId,
    turnId: turn, conversationSha256: conversation, promptText,
    exposureKind, exposureIndex, effectiveIntensity,
    renderedLensSha256: sha256(promptText), activeKey, presented: false,
  });
  ACTIVE_CANARY_CLAIMS.add(activeKey);
  return claim;
}

/** @param {{turnId?: string,conversationId?: string,dialogueAct?: string}} [options] */
export function claimBehaviorCanaryForPerson(person, options = {}) {
  if (!behaviorLearningEnabled) return null;
  try { return claimBehaviorCanaryForPersonInternal(person, options); } catch (error) {
    if (disableBehaviorLearningOnStorageFailure(error)) return null;
    throw error;
  }
}

export function behaviorCanaryLens(claim) {
  if (!behaviorLearningEnabled) return null;
  const value = CANARY_CLAIMS.get(claim);
  if (!value || CONSUMED_CANARY_CLAIMS.has(claim)) return null;
  value.presented = true;
  return Object.freeze({
    experimentId: value.experimentId, revision: value.revision,
    candidateSha256: value.candidateSha256, promptText: value.promptText,
    exposureKind: value.exposureKind, effectiveIntensity: value.effectiveIntensity,
    renderedLensSha256: value.renderedLensSha256,
  });
}

export function releaseBehaviorCanaryClaim(claim) {
  const value = CANARY_CLAIMS.get(claim);
  if (!value || CONSUMED_CANARY_CLAIMS.has(claim)) return false;
  ACTIVE_CANARY_CLAIMS.delete(value.activeKey);
  CANARY_CLAIMS.delete(claim);
  return true;
}

/** @param {{turnId?: string,conversationId?: string,reply?: string,operationKey?: string}} [options] */
export function prepareBehaviorCanaryDelivery(claim, options = {}) {
  if (!behaviorLearningEnabled) return null;
  const { turnId, conversationId, reply, operationKey } = options;
  const issued = CANARY_CLAIMS.get(claim);
  const replyText = String(reply || '').trim();
  const key = String(operationKey || '').trim();
  if (!issued || CONSUMED_CANARY_CLAIMS.has(claim) || issued.turnId !== String(turnId || '')
    || issued.presented !== true
    || issued.conversationSha256 !== conversationSha256(conversationId)
    || !replyText || !/^[A-Za-z0-9:._-]{8,240}$/u.test(key)) return null;
  const current = projectedExperiment(experimentRow(issued.experimentId));
  const expectedStatus = issued.exposureKind === 'canary' ? 'canary_pending' : 'retained';
  if (!current || current.status !== expectedStatus || current.revision !== issued.revision
    || current.candidateSha256 !== issued.candidateSha256) return null;
  const replySha256 = sha256(replyText);
  const core = Object.freeze({
    contract: 'BehaviorCanaryDeliveryBinding/v1', experimentId: current.id,
    experimentRevision: current.revision, candidateSha256: current.candidateSha256,
    turnId: issued.turnId, conversationSha256: issued.conversationSha256,
    replySha256, operationKey: key, exposureKind: issued.exposureKind,
    exposureIndex: issued.exposureIndex, effectiveIntensity: issued.effectiveIntensity,
    renderedLensSha256: issued.renderedLensSha256,
  });
  const bindingSha256 = sha256(canonicalJson(core));
  const stored = behaviorDb.transaction(() => {
    const prior = behaviorDb.prepare(`SELECT * FROM eva_behavior_canary_deliveries
      WHERE binding_sha256=? OR (experiment_id=? AND exposure_kind=? AND exposure_index=?)`)
      .get(bindingSha256, current.id, core.exposureKind, core.exposureIndex);
    if (prior) {
      if (prior.binding_sha256 !== bindingSha256 || prior.turn_id !== core.turnId
        || prior.reply_sha256 !== replySha256) throw new Error('conflicterende canary-delivery');
      return prior;
    }
    behaviorDb.prepare(`INSERT INTO eva_behavior_canary_deliveries
      (binding_sha256,experiment_id,exposure_kind,exposure_index,prepared_ts,turn_id,
        conversation_sha256,reply_sha256,rendered_lens_sha256,effective_intensity,status)
      VALUES (?,?,?,?,?,?,?,?,?,?,'prepared')`).run(
      bindingSha256, current.id, core.exposureKind, core.exposureIndex, nowIso(), core.turnId,
      core.conversationSha256, replySha256, core.renderedLensSha256, core.effectiveIntensity,
    );
    if (core.exposureKind === 'canary') insertEvent({
      action: 'canary_prepared', experimentId: current.id,
      fromStatus: current.status, toStatus: current.status,
      fromRevision: current.revision, toRevision: current.revision,
      evidenceSha256: bindingSha256, eventKey: sha256(`prepared:${bindingSha256}`),
    });
    return behaviorDb.prepare('SELECT * FROM eva_behavior_canary_deliveries WHERE binding_sha256=?')
      .get(bindingSha256);
  }).immediate();
  CONSUMED_CANARY_CLAIMS.add(claim);
  ACTIVE_CANARY_CLAIMS.delete(issued.activeKey);
  const capability = Object.freeze({});
  DELIVERY_CAPABILITIES.set(capability, Object.freeze({
    ...core, bindingSha256: stored.binding_sha256,
  }));
  return capability;
}

export function behaviorCanaryDeliveryMarker(capability) {
  const binding = DELIVERY_CAPABILITIES.get(capability);
  return !binding || CONSUMED_DELIVERY_CAPABILITIES.has(capability)
    ? null : Object.freeze({
      experimentId: binding.experimentId,
      candidateSha256: binding.candidateSha256,
      bindingSha256: binding.bindingSha256,
      exposureKind: binding.exposureKind,
      exposureIndex: binding.exposureIndex,
      renderedLensSha256: binding.renderedLensSha256,
    });
}

export function recordBehaviorCanaryExposure(deliveryContext) {
  if (!behaviorLearningEnabled) return null;
  let accepted;
  try { accepted = DELIVERY_VERIFIERS[0]?.(deliveryContext) || null; } catch { return null; }
  if (!accepted || accepted.accepted !== true) return null;
  const { capability, turnId, conversationId, reply, localMessageId, externalReplyRef } = accepted;
  const local = localMessageId == null ? null : String(localMessageId).trim();
  const external = externalReplyRef == null ? null : String(externalReplyRef).trim();
  if (!local && !external) return null;
  const issued = DELIVERY_CAPABILITIES.get(capability);
  const binding = String(issued?.bindingSha256 || '');
  const replySha256 = sha256(reply);
  const conversation = conversationSha256(conversationId);
  if (!issued || CONSUMED_DELIVERY_CAPABILITIES.has(capability)
    || !/^[a-f0-9]{64}$/u.test(binding)
    || issued.turnId !== turnId || issued.conversationSha256 !== conversation
    || issued.replySha256 !== replySha256) return null;
  const settled = behaviorDb.transaction(() => {
    const delivery = behaviorDb.prepare('SELECT * FROM eva_behavior_canary_deliveries WHERE binding_sha256=?')
      .get(binding);
    if (!delivery || delivery.turn_id !== String(turnId || '')
      || delivery.conversation_sha256 !== conversation || delivery.reply_sha256 !== replySha256
      || delivery.rendered_lens_sha256 !== issued.renderedLensSha256
      || delivery.exposure_kind !== issued.exposureKind
      || Number(delivery.exposure_index) !== Number(issued.exposureIndex)) {
      throw new Error('canary-exposure hoort niet bij deze aflevering');
    }
    const current = projectedExperiment(experimentRow(delivery.experiment_id));
    if (delivery.status === 'delivered') {
      return current?.status === 'canary_exposed' || current?.status === 'retained'
        || TERMINAL.has(current?.status) ? current : null;
    }
    const expectedStatus = delivery.exposure_kind === 'canary' ? 'canary_pending' : 'retained';
    if (!current || current.status !== expectedStatus) {
      throw new Error('behavior-lens is niet meer exact uitvoerbaar');
    }
    const updated = behaviorDb.prepare(`UPDATE eva_behavior_canary_deliveries
      SET delivered_ts=?,local_message_id=?,external_reply_ref_sha256=?,status='delivered'
      WHERE id=? AND status='prepared'`).run(
      nowIso(), local,
      external == null ? null : sha256(external), delivery.id,
    );
    let changed = { changes: 1 };
    if (delivery.exposure_kind === 'retained') {
      const delivered = Number(behaviorDb.prepare(`SELECT COUNT(*) AS count
        FROM eva_behavior_canary_deliveries
        WHERE experiment_id=? AND exposure_kind='retained' AND status='delivered'`)
        .get(current.id).count);
      if (delivered > Number(current.retainedDeliveryLimit)
        || delivery.effective_intensity !== steppedIntensity(current.intensity, delivered)) {
        throw new Error('retained delivery-budget of intensiteit is niet meer actueel');
      }
    }
    if (delivery.exposure_kind === 'canary') changed = behaviorDb.prepare(`UPDATE eva_behavior_experiments
      SET status='canary_exposed',updated_ts=?,revision=revision+1
      WHERE id=? AND status='canary_pending' AND revision=?`).run(
      nowIso(), current.id, current.revision,
    );
    if (Number(updated.changes) !== 1 || Number(changed.changes) !== 1) {
      throw new Error('canary-exposure CAS conflict');
    }
    if (delivery.exposure_kind === 'canary') insertEvent({ action: 'canary_exposed', experimentId: current.id,
      fromStatus: 'canary_pending', toStatus: 'canary_exposed',
      fromRevision: current.revision, toRevision: current.revision + 1,
      evidenceSha256: binding, eventKey: sha256(`exposed:${binding}`) });
    return projectedExperiment(experimentRow(current.id));
  }).immediate();
  if (settled) {
    CONSUMED_DELIVERY_CAPABILITIES.add(capability);
    DELIVERY_CAPABILITIES.delete(capability);
  }
  return settled;
}

function exactFeedbackSource(delivery, {
  conversationId, priorLocalMessageId = null, replyToExternalRef = null,
}) {
  if (!delivery || delivery.status !== 'delivered'
    || delivery.conversation_sha256 !== conversationSha256(conversationId)) return false;
  const local = priorLocalMessageId != null
    && String(delivery.local_message_id || '') === String(priorLocalMessageId);
  const external = replyToExternalRef != null
    && delivery.external_reply_ref_sha256 === sha256(String(replyToExternalRef));
  return local || external;
}

function exactDeliveredCanaryForFeedback({
  person, conversationId, priorLocalMessageId = null, replyToExternalRef = null,
}) {
  const member = householdPerson(person);
  if (!member) return null;
  if (!behaviorLearningEnabled) return null;
  const matches = behaviorDb.prepare(`SELECT d.* FROM eva_behavior_canary_deliveries d
    JOIN eva_behavior_experiments e ON e.id=d.experiment_id
    WHERE e.person_id=? AND e.status IN ('canary_exposed','inconclusive','retained','deactivated','rolled_back')
      AND d.status='delivered'
    ORDER BY d.id DESC`).all(member.id).filter((row) => exactFeedbackSource(row, {
    conversationId, priorLocalMessageId, replyToExternalRef,
  }));
  return matches.length === 1 ? matches[0] : null;
}

function settlementBindingSha256({ operationKey, delivery, inboundMessageSha256,
  personId, conversationSha256: conversation, inboundContentSha256,
  classifierContract, outcomeType, feedbackId = null }) {
  return sha256(canonicalJson({
    contract: 'BehaviorLensOutcomeBinding/v2', operationKey,
    deliveryId: Number(delivery.id), deliveryBindingSha256: delivery.binding_sha256,
    inboundMessageSha256, personId, conversationSha256: conversation,
    inboundContentSha256, classifierContract, outcomeType, feedbackId,
  }));
}

function exactSettlementReplay(inboundMessageSha256, expected) {
  const row = behaviorDb.prepare(`SELECT * FROM eva_behavior_feedback_settlements
    WHERE inbound_message_sha256=?`).get(inboundMessageSha256);
  if (!row) return null;
  if ((row.feedback_id == null ? null : Number(row.feedback_id)) !== expected.feedbackId
    || Number(row.experiment_id) !== Number(expected.experimentId)
    || Number(row.delivery_id) !== Number(expected.deliveryId)
    || row.binding_sha256 !== expected.bindingSha256
    || Number(row.person_id) !== Number(expected.personId)
    || row.conversation_sha256 !== expected.conversationSha256
    || row.inbound_content_sha256 !== expected.inboundContentSha256
    || row.classifier_contract !== expected.classifierContract
    || row.outcome_type !== expected.outcomeType) {
    throw new Error('conflicterende behavior-feedback replay');
  }
  return projectedExperiment(experimentRow(row.experiment_id));
}

/** @param {{feedbackContext?: object,operationKey?: string}} [options] */
export function settleBehaviorCanaryInconclusive(options = {}) {
  if (!behaviorLearningEnabled) return null;
  expirePreparedBehaviorCanaries();
  const { feedbackContext, operationKey } = options;
  let verified;
  try { verified = FEEDBACK_VERIFIERS[0]?.(feedbackContext) || null; } catch { return null; }
  const {
    person, conversationId, priorLocalMessageId = null,
    replyToExternalRef = null, inboundMessageId = null,
    inboundContentSha256 = null, classifierContract = null,
  } = verified || {};
  const member = householdPerson(person);
  const delivery = exactDeliveredCanaryForFeedback({
    person, conversationId, priorLocalMessageId, replyToExternalRef,
  });
  const inbound = String(inboundMessageId || '').trim();
  const key = String(operationKey || '').trim();
  const conversation = conversationSha256(conversationId);
  if (!member || !delivery || !inbound || !/^[A-Za-z0-9:._-]{8,240}$/u.test(key)
    || !/^[a-f0-9]{64}$/u.test(inboundContentSha256 || '')
    || classifierContract !== STYLE_OUTCOME_CLASSIFIER_CONTRACT) return null;
  const inboundMessageSha256 = sha256(inbound);
  const outcomeType = 'no_direct_style_evaluation';
  const bindingSha256 = settlementBindingSha256({
    operationKey: key, delivery, inboundMessageSha256, personId: member.id,
    conversationSha256: conversation, inboundContentSha256,
    classifierContract, outcomeType,
  });
  return behaviorDb.transaction(() => {
    const replay = exactSettlementReplay(inboundMessageSha256, {
      feedbackId: null, experimentId: delivery.experiment_id,
      deliveryId: delivery.id, bindingSha256, personId: member.id,
      conversationSha256: conversation, inboundContentSha256,
      classifierContract, outcomeType,
    });
    if (replay) return replay;
    const current = projectedExperiment(experimentRow(delivery.experiment_id));
    if (!current || (delivery.exposure_kind === 'canary' && current.status !== 'canary_exposed')
      || (delivery.exposure_kind === 'retained' && current.status !== 'retained')) return null;
    behaviorDb.prepare(`INSERT INTO eva_behavior_feedback_settlements
      (feedback_id,experiment_id,delivery_id,binding_sha256,inbound_message_sha256,
        person_id,conversation_sha256,inbound_content_sha256,classifier_contract,outcome_type,created_ts)
      VALUES (NULL,?,?,?,?,?,?,?,?,?,?)`).run(
      current.id, delivery.id, bindingSha256, inboundMessageSha256, member.id,
      conversation, inboundContentSha256, classifierContract, outcomeType, nowIso(),
    );
    if (delivery.exposure_kind === 'retained') return current;
    const ts = nowIso();
    const changed = behaviorDb.prepare(`UPDATE eva_behavior_experiments SET
      status='inconclusive',outcome_class='insufficient',settled_ts=?,updated_ts=?,revision=revision+1
      WHERE id=? AND status='canary_exposed' AND revision=?`).run(
      ts, ts, current.id, current.revision,
    );
    if (Number(changed.changes) !== 1) throw new Error('inconclusive-settlement CAS conflict');
    insertEvent({ action: 'inconclusive', experimentId: current.id,
      fromStatus: current.status, toStatus: 'inconclusive',
      fromRevision: current.revision, toRevision: current.revision + 1,
      evidenceSha256: bindingSha256, outcomeClass: 'insufficient',
      eventKey: sha256(`settlement:${key}`) });
    return projectedExperiment(experimentRow(current.id));
  }).immediate();
}

/** @param {{feedbackContext?: object,operationKey?: string}} [options] */
export function settleBehaviorCanaryFromFeedback(options = {}) {
  if (!behaviorLearningEnabled) return null;
  expirePreparedBehaviorCanaries();
  const { feedbackContext, operationKey } = options;
  let verified;
  try { verified = FEEDBACK_VERIFIERS[0]?.(feedbackContext) || null; } catch { return null; }
  const {
    person, feedbackId, feedbackKind, outcomeType, conversationId,
    priorLocalMessageId = null, replyToExternalRef = null, inboundMessageId = null,
    inboundContentSha256 = null, classifierContract = null,
  } = verified || {};
  const member = householdPerson(person);
  const delivery = exactDeliveredCanaryForFeedback({
    person, conversationId, priorLocalMessageId, replyToExternalRef,
  });
  const current = projectedExperiment(experimentRow(delivery?.experiment_id));
  const verdict = outcomeType === EXPECTED_OUTCOME_KIND
    && current?.expectedOutcomeKind === EXPECTED_OUTCOME_KIND ? 'helped'
    : outcomeType === NEGATIVE_OUTCOME_KIND
      && current?.negativeOutcomeKind === NEGATIVE_OUTCOME_KIND ? 'harmed' : null;
  const feedback = behaviorDb.prepare(`SELECT f.*,p.name AS person_name FROM relationship_feedback f
    JOIN people p ON p.id=f.person_id WHERE f.id=?`).get(Number(feedbackId));
  const exactKind = (feedbackKind === 'appreciation' && outcomeType === EXPECTED_OUTCOME_KIND)
    || (feedbackKind === 'correction' && outcomeType === NEGATIVE_OUTCOME_KIND);
  const inbound = String(inboundMessageId || '').trim();
  const conversation = conversationSha256(conversationId);
  if (!member || !delivery || !current || !verdict || !feedback || !exactKind || !inbound
    || Number(feedback.person_id) !== Number(member.id)
    || feedback.kind !== feedbackKind
    || !/^[a-f0-9]{64}$/u.test(inboundContentSha256 || '')
    || classifierContract !== STYLE_OUTCOME_CLASSIFIER_CONTRACT) return null;
  const key = String(operationKey || `behavior-feedback:${feedback.id}:${current.id}`);
  if (!/^[A-Za-z0-9:._-]{8,240}$/u.test(key)) return null;
  const inboundMessageSha256 = sha256(inbound);
  const bindingSha256 = settlementBindingSha256({
    operationKey: key, delivery, inboundMessageSha256, personId: member.id,
    conversationSha256: conversation, inboundContentSha256, classifierContract,
    outcomeType, feedbackId: Number(feedback.id),
  });
  return behaviorDb.transaction(() => {
    const replay = exactSettlementReplay(inboundMessageSha256, {
      feedbackId: Number(feedback.id), experimentId: delivery.experiment_id,
      deliveryId: delivery.id, bindingSha256, personId: member.id,
      conversationSha256: conversation, inboundContentSha256,
      classifierContract, outcomeType,
    });
    if (replay) return replay;
    const fresh = projectedExperiment(experimentRow(current.id));
    const expectedStatus = delivery.exposure_kind === 'canary' ? 'canary_exposed' : 'retained';
    if (!fresh || fresh.status !== expectedStatus || fresh.revision !== current.revision) {
      throw new Error('behavior-feedback is stale');
    }
    behaviorDb.prepare(`INSERT INTO eva_behavior_feedback_settlements
      (feedback_id,experiment_id,delivery_id,binding_sha256,inbound_message_sha256,
        person_id,conversation_sha256,inbound_content_sha256,classifier_contract,outcome_type,created_ts)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
      Number(feedback.id), fresh.id, delivery.id, bindingSha256,
      inboundMessageSha256, member.id, conversation, inboundContentSha256,
      classifierContract, outcomeType, nowIso(),
    );
    const eventKey = sha256(`settlement:${key}`);
    if (delivery.exposure_kind === 'retained' && verdict === 'helped') {
      insertEvent({ action: 'retention_confirmed', experimentId: fresh.id,
        fromStatus: 'retained', toStatus: 'retained',
        fromRevision: fresh.revision, toRevision: fresh.revision,
        evidenceSha256: bindingSha256, outcomeClass: fresh.outcomeClass, eventKey });
      return fresh;
    }
    if (delivery.exposure_kind === 'retained') {
      deactivateRetained(fresh, 'direct_style_correction', bindingSha256, eventKey);
      return projectedExperiment(experimentRow(fresh.id));
    }
    const nextStatus = verdict === 'helped' ? 'retained' : 'rolled_back';
    const ts = nowIso();
    const retainedUntil = verdict === 'helped'
      ? new Date(Date.now() + RETAINED_DAYS * 24 * 60 * 60_000).toISOString() : null;
    const changed = behaviorDb.prepare(`UPDATE eva_behavior_experiments SET
      status=?,outcome_class=?,settled_ts=?,updated_ts=?,revision=revision+1,
      retained_until_ts=?,retained_delivery_limit=?
      WHERE id=? AND status='canary_exposed' AND revision=?`).run(
      nextStatus, verdict, ts, ts, retainedUntil,
      verdict === 'helped' ? RETAINED_DELIVERY_LIMIT : null, fresh.id, fresh.revision,
    );
    if (Number(changed.changes) !== 1) throw new Error('canary-settlement CAS conflict');
    insertEvent({ action: nextStatus, experimentId: fresh.id,
      fromStatus: fresh.status, toStatus: nextStatus,
      fromRevision: fresh.revision, toRevision: fresh.revision + 1,
      evidenceSha256: bindingSha256, outcomeClass: verdict, eventKey });
    return projectedExperiment(experimentRow(fresh.id));
  }).immediate();
}

export function activeBehaviorLensContext(person, { limit = 1 } = {}) {
  void person;
  void limit;
  // Een behouden lens wordt alleen via claim -> accepted reply -> delivery
  // geprojecteerd. Losse promptcontext zou gebruik en correctie niet binden.
  return '';
}

export function behaviorLearningStatus() {
  if (!behaviorLearningEnabled) return Object.freeze({
    enabled: false, reason: behaviorLearningReason,
  });
  let counts;
  try {
    counts = behaviorDb.prepare(`SELECT status,COUNT(*) AS count FROM eva_behavior_experiments
      GROUP BY status`).all();
  } catch (error) {
    if (!disableBehaviorLearningOnStorageFailure(error)) throw error;
    return Object.freeze({ enabled: false, reason: behaviorLearningReason });
  }
  return Object.freeze({ enabled: true, reason: behaviorLearningReason,
    counts: Object.freeze(Object.fromEntries(
      counts.map((row) => [row.status, Number(row.count)]),
    )) });
}

export const BEHAVIOR_LEARNING_CONTRACTS = Object.freeze({
  experiment: EXPERIMENT_CONTRACT, candidate: CANDIDATE_CONTRACT,
  proposal: PROPOSAL_CONTRACT, renderer: RENDERER_CONTRACT,
});
