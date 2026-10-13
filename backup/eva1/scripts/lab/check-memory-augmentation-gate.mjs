import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const DEFAULT_PATH = 'benchmark/memory-augmentation-gate-v1.json';
const CONTRACT = 'EvaMemoryAugmentationGate/v1';
const REQUIRED_AUGMENTATIONS = ['mnemosyne_hot_projection', 'honcho_person_model'];
const REQUIRED_HARD_CASES = [
  'base_core_and_hindsight_survive_optional_layer_outage',
  'hot_projection_never_overrides_newer_correction',
  'person_correction_supersedes_derived_trait',
  'cross_person_and_cross_tenant_canary_isolation',
  'private_group_project_audience_isolation',
  'group_consent_revoke_removes_person_model_context',
  'assistant_inference_never_becomes_direct_user_fact',
  'same_person_cross_channel_continuity',
  'no_duplicate_retain_or_unbounded_feedback_loop',
  'erase_export_rebuild_upgrade_and_rollback',
  'dashboard_edits_follow_eva_correction_and_erasure_authority',
];
const REQUIRED_METRICS = [
  'augmentation_correctness_delta_percent',
  'person_model_correction_latency_seconds',
  'representation_read_latency_p50_ms',
  'representation_read_latency_p95_ms',
  'first_answer_added_latency_p95_ms',
  'hot_path_context_tokens_added_p50',
  'hot_path_context_tokens_added_p95',
  'input_output_tokens_total',
  'background_model_tokens_total',
  'estimated_model_cost_eur',
  'resident_memory_peak_mib',
  'persistent_storage_mib',
  'background_queue_oldest_age_seconds',
  'duplicate_or_conflicting_memory_count',
  'backup_restore_seconds',
  'upgrade_downtime_seconds',
  'operator_steps',
];
const PINS = {
  mnemosyne_hot_projection: {
    pin: 'mnemosyne',
    version: '3.15.1',
    source_commit: '78506708aae344635e01a24f67a7319efc36fce9',
  },
  honcho_person_model: {
    pin: 'honcho',
    version: '3.0.12',
    source_commit: '93dcf59c4a4225bb020b20c628799737fefae318',
  },
};

function assertExactSet(actual, required, label) {
  if (!Array.isArray(actual) || actual.length !== required.length) {
    throw new Error(`${label} heeft niet exact de verplichte omvang`);
  }
  const seen = new Set(actual);
  for (const item of required) if (!seen.has(item)) throw new Error(`${label} mist ${item}`);
  if (seen.size !== actual.length) throw new Error(`${label} bevat dubbelen`);
}

function referenceKey(reference) {
  if (typeof reference?.receipt_id !== 'string' || typeof reference?.claim_id !== 'string') {
    throw new Error('ongeldige evidence-ref');
  }
  return `${reference.receipt_id}#${reference.claim_id}`;
}

function readVerifiedClaims(gate, gatePath) {
  const root = path.resolve(path.dirname(gatePath), '..');
  const seenReceipts = new Set();
  const claims = new Map();
  for (const receipt of gate.evidence_catalog ?? []) {
    if (!receipt?.id || seenReceipts.has(receipt.id)) {
      throw new Error(`ongeldig of dubbel evidence-id: ${receipt?.id ?? ''}`);
    }
    seenReceipts.add(receipt.id);
    if (!/^[a-f0-9]{64}$/u.test(receipt.sha256 ?? '')) {
      throw new Error(`ongeldige evidence-sha256: ${receipt.id}`);
    }
    const receiptPath = path.resolve(root, receipt.path ?? '');
    if (!receiptPath.startsWith(`${root}${path.sep}`)) {
      throw new Error(`evidencepad buiten repository: ${receipt.id}`);
    }
    const stat = fs.lstatSync(receiptPath);
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error(`evidence is geen regulier bestand: ${receipt.id}`);
    }
    const realPath = fs.realpathSync(receiptPath);
    if (!realPath.startsWith(`${root}${path.sep}`)) {
      throw new Error(`evidence resolveert buiten repository: ${receipt.id}`);
    }
    const bytes = fs.readFileSync(receiptPath);
    const actual = crypto.createHash('sha256').update(bytes).digest('hex');
    if (actual !== receipt.sha256) throw new Error(`evidencehash wijkt af: ${receipt.id}`);
    const parsed = JSON.parse(bytes.toString('utf8'));
    if (parsed.contract !== 'EvaMemoryAugmentationReceipt/v1') {
      throw new Error(`verkeerd receiptcontract: ${receipt.id}`);
    }
    for (const claim of [
      ...(parsed.memory_augmentation_claims ?? []),
      ...(parsed.memory_combination_claims ?? []),
    ]) {
      if (typeof claim?.claim_id !== 'string') throw new Error(`claim-id ontbreekt: ${receipt.id}`);
      const key = `${receipt.id}#${claim.claim_id}`;
      if (claims.has(key)) throw new Error(`dubbele receiptclaim: ${key}`);
      claims.set(key, claim);
    }
  }
  return claims;
}

function validateContract(gate) {
  if (gate?.contract !== CONTRACT) throw new Error('verkeerd memory-augmentationcontract');
  const architecture = gate.architecture ?? {};
  if (architecture.operational_authority !== 'eva_postgresql'
    || architecture.long_term_memory !== 'hindsight_oss_0.9.1'
    || architecture.recent_context !== 'eva_postgresql_last_12_messages'
    || architecture.mnemosyne_replaces_hindsight !== false
    || architecture.honcho_replaces_hindsight !== false
    || architecture.optional_layers_may_become_canonical_authority !== false
    || architecture.honcho_may_own_identity_consent_effects_or_receipts !== false
    || architecture.honcho_output_is_derived_and_rebuildable !== true
    || architecture.honcho_may_enter_every_reply_hot_path_without_latency_proof !== false
    || architecture.mem0_is_in_v1_runtime_plan !== false
    || architecture.memory_dashboard_is_provider_independent !== true
    || architecture.memory_dashboard_may_bypass_eva_authority !== false) {
    throw new Error('memoryrollen of authoritygrens wijken af');
  }
  assertExactSet(Object.keys(gate.augmentations ?? {}), REQUIRED_AUGMENTATIONS, 'augmentations');
  assertExactSet(gate.required_hard_cases, REQUIRED_HARD_CASES, 'required_hard_cases');
  assertExactSet(gate.required_metrics, REQUIRED_METRICS, 'required_metrics');
  for (const [id, required] of Object.entries(PINS)) {
    const pin = gate?.pins?.[required.pin];
    if (!pin || pin.version !== required.version || pin.source_commit !== required.source_commit
      || pin.edition !== 'self_hosted_oss') {
      throw new Error(`kandidaatpin wijkt af: ${id}`);
    }
  }
  const hindsight = gate?.pins?.hindsight;
  if (hindsight?.version !== '0.9.1'
    || hindsight.source_commit !== 'e5b49eb6729512bd9b103058daa93f701da25644'
    || !/^sha256:[a-f0-9]{64}$/u.test(hindsight.artifact_digest ?? '')) {
    throw new Error('Hindsight-L2-pin wijkt af');
  }
}

function validateAugmentationClaim(gate, id, augmentation, claim) {
  const pin = gate.pins[PINS[id].pin];
  if (!claim || claim.claim_scope !== 'full_augmentation_decision'
    || claim.augmentation_id !== id
    || claim.decision !== augmentation.decision
    || claim.version !== pin.version
    || claim.source_commit !== pin.source_commit
    || claim.artifact_digest !== pin.artifact_digest
    || claim.operational_authority !== 'eva_postgresql'
    || claim.hindsight_retained_as_long_term_memory !== true
    || claim.optional_layer_became_canonical_authority !== false
    || claim.independent_verdict !== 'pass') {
    throw new Error(`augmentatieclaim heeft verkeerde binding: ${id}`);
  }
  assertExactSet((claim.hard_cases ?? []).map((item) => item.id), REQUIRED_HARD_CASES, `${id}/hard_cases`);
  if (claim.hard_cases.some((item) => !['pass', 'fail'].includes(item.status))) {
    throw new Error(`augmentatieclaim heeft ongemeten hard case: ${id}`);
  }
  assertExactSet((claim.metrics ?? []).map((item) => item.id), REQUIRED_METRICS, `${id}/metrics`);
  if (claim.metrics.some((item) => item.status !== 'measured'
    || typeof item.value !== 'number' || !Number.isFinite(item.value))) {
    throw new Error(`augmentatieclaim heeft ongemeten metric: ${id}`);
  }
  for (const field of ['dependency_lock', 'sbom_vulnerability_license', 'export_rebuild', 'upgrade', 'rollback']) {
    if (!['pass', 'fail'].includes(claim[field])) throw new Error(`augmentatieclaim mist ${field}: ${id}`);
  }
  if (augmentation.decision === 'accepted'
    && (claim.benefit_proven !== true
      || claim.hard_cases.some((item) => item.status !== 'pass')
      || ['dependency_lock', 'sbom_vulnerability_license', 'export_rebuild', 'upgrade', 'rollback']
        .some((field) => claim[field] !== 'pass'))) {
    throw new Error(`geaccepteerde augmentatie heeft geen volledig groen bewijs: ${id}`);
  }
}

function evaluate(gate, claims) {
  validateContract(gate);
  const blockers = [];
  const acceptedClaimKeys = new Map();
  for (const [id, augmentation] of Object.entries(gate.augmentations)) {
    if (augmentation.status !== 'decided') {
      if (!['not_materialized', 'not_run'].includes(augmentation.status)
        || augmentation.decision !== null || augmentation.evidence_ref !== null) {
        throw new Error(`ongeldige open augmentatiestatus: ${id}`);
      }
      blockers.push(`${id}:${augmentation.status}`);
      continue;
    }
    if (!['accepted', 'rejected'].includes(augmentation.decision)) {
      throw new Error(`ongeldige augmentatiebeslissing: ${id}`);
    }
    const pin = gate.pins[PINS[id].pin];
    if (!/^sha256:[a-f0-9]{64}$/u.test(pin.artifact_digest ?? '')) {
      throw new Error(`besliste augmentatie mist artifactdigest: ${id}`);
    }
    const key = referenceKey(augmentation.evidence_ref);
    validateAugmentationClaim(gate, id, augmentation, claims.get(key));
    acceptedClaimKeys.set(id, key);
  }

  const decisions = Object.fromEntries(
    Object.entries(gate.augmentations).map(([id, value]) => [id, value.decision]),
  );
  const bothAccepted = REQUIRED_AUGMENTATIONS.every((id) => decisions[id] === 'accepted');
  const combination = gate.combination ?? {};
  if (bothAccepted) {
    if (combination.status !== 'decided' || !['accepted', 'rejected'].includes(combination.decision)) {
      blockers.push(`combination:${combination.status ?? 'missing'}`);
    } else {
      const claim = claims.get(referenceKey(combination.evidence_ref));
      if (!claim || claim.claim_scope !== 'combined_augmentation_decision'
        || claim.decision !== combination.decision
        || claim.independent_verdict !== 'pass'
        || claim.hindsight_retained_as_long_term_memory !== true
        || claim.candidate_claim_keys?.mnemosyne_hot_projection
          !== acceptedClaimKeys.get('mnemosyne_hot_projection')
        || claim.candidate_claim_keys?.honcho_person_model
          !== acceptedClaimKeys.get('honcho_person_model')) {
        throw new Error('combinatieclaim heeft verkeerde binding');
      }
      assertExactSet(
        (claim.hard_cases ?? []).map((item) => item.id),
        REQUIRED_HARD_CASES,
        'combination/hard_cases',
      );
      assertExactSet(
        (claim.metrics ?? []).map((item) => item.id),
        REQUIRED_METRICS,
        'combination/metrics',
      );
      if (claim.hard_cases.some((item) => !['pass', 'fail'].includes(item.status))
        || claim.metrics.some((item) => item.status !== 'measured'
          || typeof item.value !== 'number' || !Number.isFinite(item.value))) {
        throw new Error('combinatieclaim heeft ongemeten bewijs');
      }
      if (combination.decision === 'accepted'
        && (claim.benefit_proven !== true
          || claim.hard_cases.some((item) => item.status !== 'pass'))) {
        throw new Error('geaccepteerde combinatie heeft geen volledig groen bewijs');
      }
    }
  } else if (gate.status === 'decided') {
    if (combination.status !== 'not_applicable'
      || combination.decision !== null || combination.evidence_ref !== null) {
      throw new Error('combinatie moet not_applicable zijn');
    }
  } else if (combination.status !== 'pending_single_layer_decisions') {
    throw new Error('ongeldige open combinatiestatus');
  }

  if (gate.status !== 'decided') {
    if (gate.status !== 'open') throw new Error('ongeldige gatestatus');
    blockers.push('decision:open');
  } else if (acceptedClaimKeys.size !== REQUIRED_AUGMENTATIONS.length) {
    throw new Error('besliste gate mist augmentatiebesluiten');
  }
  return { allowed: blockers.length === 0, blockers };
}

export function loadAndEvaluate(filePath = DEFAULT_PATH) {
  const gate = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  return evaluate(gate, readVerifiedClaims(gate, filePath));
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = loadAndEvaluate(process.argv[2] ?? DEFAULT_PATH);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.allowed ? 0 : 1;
}
