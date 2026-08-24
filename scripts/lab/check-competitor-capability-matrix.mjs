import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const DEFAULT_PATH = 'benchmark/competitor-capability-matrix-v1.json';
const REQUIRED_COMPETITORS = ['openclaw', 'hermes', 'agent_zero'];
const ALLOWED_STATUSES = new Set(['won', 'tied', 'lost', 'no_run']);
const REQUIRED_CATEGORIES = [
  'install_update_recovery',
  'conversation_persona',
  'channels_cross_channel_identity',
  'models_media_local_inference',
  'skills_marketplaces',
  'plugins_mcp_a2a',
  'gateway_pairing_trustdomains',
  'companion_nodes_hostbridges',
  'browser_desktop_documents',
  'code_files_projects_worktrees',
  'memory_persona_learning',
  'cron_durable_goals_background_work',
  'multi_agent_delegation',
  'permissions_secrets_sandboxing',
  'idempotency_readback_rollback',
  'observability_costs_receipts',
  'home_assistant_resident_silence',
  'api_ide_programmatic_access',
  'voice_speech_media',
  'web_extraction_ui_canvas_artifacts_notifications',
  'rl_batch_evaluation',
  'commerce_external_accounts',
];

function buildEvidenceCatalog(matrix) {
  const catalog = new Map();
  for (const receipt of matrix?.evidence_catalog ?? []) {
    if (!receipt?.id || catalog.has(receipt.id)) {
      throw new Error(`ongeldig of dubbel evidence-id: ${receipt?.id ?? ''}`);
    }
    catalog.set(receipt.id, receipt);
  }
  return catalog;
}

function assertClaimBinding(matrix, verifiedClaims, category, competitor, result) {
  for (const reference of result.evidence_refs) {
    if (typeof reference?.receipt_id !== 'string' || typeof reference?.claim_id !== 'string') {
      throw new Error(`ongeldige evidence-ref: ${category.id}/${competitor}`);
    }
    const claim = verifiedClaims.get(`${reference.receipt_id}#${reference.claim_id}`);
    if (!claim
      || claim.claim_scope !== 'full_category_acceptance'
      || claim.category_id !== category.id
      || claim.competitor !== competitor
      || claim.result !== result.status
      || claim.independent_verdict !== 'pass'
      || claim.eva_binding !== matrix.pinned_participants.eva2
      || claim.competitor_binding !== matrix.pinned_participants[competitor]) {
      throw new Error(
        `gehashte receiptclaim heeft verkeerde binding: ${category.id}/${competitor}`,
      );
    }
  }
}

function evaluateCompetitorMatrix(matrix, verifiedClaims) {
  if (!Array.isArray(matrix?.categories) || matrix.categories.length === 0) {
    throw new Error('categories ontbreekt');
  }
  for (const participant of ['eva2', ...REQUIRED_COMPETITORS]) {
    if (typeof matrix?.pinned_participants?.[participant] !== 'string'
      || matrix.pinned_participants[participant].length === 0) {
      throw new Error(`pinned_participants mist ${participant}`);
    }
  }

  const blockers = [];
  const seen = new Set();
  for (const category of matrix.categories) {
    if (!category?.id || seen.has(category.id)) {
      throw new Error(`ongeldige of dubbele categorie: ${category?.id ?? ''}`);
    }
    seen.add(category.id);
    for (const competitor of REQUIRED_COMPETITORS) {
      const result = category?.comparisons?.[competitor];
      if (!result || !ALLOWED_STATUSES.has(result.status)) {
        throw new Error(`${category.id}/${competitor} heeft geen geldige status`);
      }
      if (!Array.isArray(result.critical_failures)) {
        throw new Error(`${category.id}/${competitor} mist critical_failures`);
      }
      if (result.status !== 'no_run'
        && (!Array.isArray(result.evidence_refs) || result.evidence_refs.length === 0)) {
        throw new Error(`${category.id}/${competitor} mist evidence_refs`);
      }
      if (result.status !== 'no_run') {
        assertClaimBinding(matrix, verifiedClaims, category, competitor, result);
      }
      if (result.status !== 'won') {
        blockers.push(`${category.id}/${competitor}:${result.status}`);
      }
      for (const failure of result.critical_failures) {
        blockers.push(`${category.id}/${competitor}:critical:${failure}`);
      }
    }
  }
  for (const required of REQUIRED_CATEGORIES) {
    if (!seen.has(required)) {
      throw new Error(`verplichte categorie ontbreekt: ${required}`);
    }
  }
  return { allowed: blockers.length === 0, blockers };
}

function readVerifiedClaims(matrix, matrixPath) {
  const root = path.resolve(path.dirname(matrixPath), '..');
  const catalog = buildEvidenceCatalog(matrix);
  const verifiedClaims = new Map();
  for (const receipt of catalog.values()) {
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
    if (actual !== receipt.sha256) {
      throw new Error(`evidencehash wijkt af: ${receipt.id}`);
    }
    const parsed = JSON.parse(bytes.toString('utf8'));
    for (const claim of parsed.competitor_capability_claims ?? []) {
      if (typeof claim?.claim_id !== 'string') {
        throw new Error(`receiptclaim mist claim_id: ${receipt.id}`);
      }
      const key = `${receipt.id}#${claim.claim_id}`;
      if (verifiedClaims.has(key)) {
        throw new Error(`dubbele receiptclaim: ${key}`);
      }
      verifiedClaims.set(key, claim);
    }
  }
  return verifiedClaims;
}

export function loadAndEvaluate(filePath = DEFAULT_PATH) {
  const matrix = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  const verifiedClaims = readVerifiedClaims(matrix, filePath);
  return evaluateCompetitorMatrix(matrix, verifiedClaims);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = loadAndEvaluate(process.argv[2] ?? DEFAULT_PATH);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.allowed ? 0 : 1;
}
