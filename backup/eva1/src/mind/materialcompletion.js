import { createHash } from 'node:crypto';
import { validateExecutionRecord } from '../db.js';
import {
  artifactReceiptChainMatches,
  canonicalJson,
} from '../util.js';
import {
  canonicalArtifactDisplayPath,
  executableWorkspaceArtifact,
} from './projectexecution.js';
import {
  revalidateWorkspaceFileManifest,
  validWorkspaceFileManifest,
} from './workspacefilemanifest.js';

const SHA256_RE = /^[a-f0-9]{64}$/u;
const DEFERRED_BOUNDARY_REASONS = new Set(['conditional', 'inspection_only']);
const MANIFEST_AUTHORITY = new WeakMap();
const REQUEST_SENTINEL_AUTHORITY = new WeakMap();
const MATERIAL_OBJECT = /(?:\b(?:artifacts?|bestanden?|documenten?|rapporten?|resulta(?:at|ten)|teksten?|workspace|werkstukken?|notities?|overzichten?)\b|\b[\p{L}\p{N}_-]*artefact(?:en|s)?\b|\b(?:economisch|inkomsten|verkoop)[\p{L}\p{N}_-]*werk\b|data[\\/]workspace[\\/]|\.md\b)/iu;
const MATERIAL_MUTATION = /\b(?:actualiseer\w*|aanvul\w*|bewaar\w*|bewerk\w*|bouw\w*|corrigeer\w*|creëer\w*|exporteer\w*|genereer\w*|herstel\w*|herschrijf\w*|kopieer\w*|label\w*|lever\w*|maak\w*|markeer\w*|overschrijf\w*|pas\w*|produceer\w*|rond\w*|sla\w*|schrijf\w*|update\w*|vervang\w*|verwijder\w*|voeg\w*|werk\w*|wijzig\w*|zet\w*)\b/iu;
const NEGATED_MUTATION = /\b(?:niet|niets|nooit)\b.{0,40}\b(?:actualiseren|aanvullen|bewaren|bewerken|bouwen|corrigeren|creëren|exporteren|genereren|herstellen|herschrijven|kopiëren|labelen|leveren|maken|markeren|aanpassen|opslaan|produceren|schrijven|updaten|vervangen|verwijderen|toevoegen|uitwerken|wijzigen|zetten)\b/iu;
const EXPLICIT_WORK_SETTLEMENT = /\b(?:rond\w*\s+(?:het\s+)?werk\s*#\s*\d+\s+af|werk\s*#\s*\d+\s+afrond\w*)\b/giu;
const QUALIFIED_WORKSPACE_PATH = /data[\\/]workspace[\\/][\p{L}\p{N}._/-]+/giu;
const EXACT_TARGET_ENUMERATION = /^\s*(?:maak|creëer|genereer|bouw|schrijf|exporteer)\s+(?:(?:het|de)\s+)?(?:bestand(?:en)?\s*:?\s*)?<path>(?:\s*(?:,|en)\s*<path>)*\s*[.!?]?\s*$/iu;

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function uniqueStrings(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map((value) => String(value || '').trim()).filter(Boolean))];
}

function deepFreeze(value, seen = new Set()) {
  if (!value || typeof value !== 'object' || seen.has(value)) return value;
  seen.add(value);
  Object.values(value).forEach((child) => deepFreeze(child, seen));
  return Object.freeze(value);
}

function exactStringArray(value, expected) {
  return Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
}

function isSourceBound(action, sourceText, sourceTextSha256) {
  const text = typeof sourceText === 'string' ? sourceText : '';
  return text.length > 0 && SHA256_RE.test(String(sourceTextSha256 || ''))
    && sha256(text) === sourceTextSha256
    && Array.isArray(action?.sourceSpans) && action.sourceSpans.length > 0
    && action.sourceSpans.every((span) => (
      Number.isInteger(span?.start) && Number.isInteger(span?.end)
      && span.start >= 0 && span.end > span.start && span.end <= text.length
      && text.slice(span.start, span.end).trim().length > 0
    ));
}

function isWorkspaceWrite(action) {
  return action?.effect === 'workspace'
    && action?.capabilityId === 'workspace.file.write'
    && action?.operationId === 'write';
}

function isWorkspaceDiscovery(action) {
  return action?.capabilityId === 'workspace.file.list'
    && action?.operationId === 'list';
}

function canonicalWorkspaceFile(value) {
  const result = canonicalArtifactDisplayPath(value);
  return result.startsWith('data/workspace/') && result !== 'data/workspace/' ? result : '';
}

function explicitMaterialMutation(sourceText) {
  return String(sourceText || '').replace(EXPLICIT_WORK_SETTLEMENT, ' ')
    .split(/(?:[.!?;\n]+|,|\b(?:maar|daarna|vervolgens)\b)/iu)
    .some((clause) => MATERIAL_OBJECT.test(clause)
      && MATERIAL_MUTATION.test(clause) && !NEGATED_MUTATION.test(clause));
}

function explicitWorkspaceTargets(sourceText) {
  const matches = String(sourceText || '').match(QUALIFIED_WORKSPACE_PATH) || [];
  return uniqueStrings(matches.map((match) => (
    canonicalWorkspaceFile(match.replace(/[.,;:!?]+$/u, ''))
  )).filter(Boolean));
}

function materialMutationFacetCount(sourceText) {
  return (String(sourceText || '').replace(EXPLICIT_WORK_SETTLEMENT, ' ')
    .match(/[\p{L}]+/gu) || [])
    .filter((word) => MATERIAL_MUTATION.test(word)).length;
}

function exactTargetEnumeration(sourceText, expectedPaths) {
  const matches = String(sourceText || '').match(QUALIFIED_WORKSPACE_PATH) || [];
  const canonicalMatches = matches.map((match) => (
    canonicalWorkspaceFile(match.replace(/[.,;:!?]+$/u, ''))
  )).filter(Boolean);
  if (!expectedPaths.length || matches.length !== canonicalMatches.length
    || canonicalMatches.length !== expectedPaths.length
    || !exactStringArray(canonicalMatches, expectedPaths)) return false;
  const skeleton = String(sourceText || '').replace(QUALIFIED_WORKSPACE_PATH, '<path>');
  return EXACT_TARGET_ENUMERATION.test(skeleton);
}

/**
 * Issues a one-turn, non-executable sentinel from the raw request.
 * It can only force a fail-closed material lifecycle; it never authorises a
 * path, tool, argument or write. Principal/ingress authority is deliberately
 * checked separately by the caller: an untrusted mutation request must still
 * be detected so it cannot escape through a free-form success reply.
 */
export function materialRequestSentinel({
  sourceText = '', sourceIdentitySha256 = '', synthetic = false,
} = {}) {
  const text = typeof sourceText === 'string' ? sourceText : '';
  if (synthetic === true || !text.trim()) return null;
  const identitySha256 = String(sourceIdentitySha256 || sha256(text));
  if (!SHA256_RE.test(identitySha256)) return null;
  const required = explicitMaterialMutation(text);
  const explicitTargetPaths = explicitWorkspaceTargets(text);
  const mutationFacetCount = materialMutationFacetCount(text);
  const body = {
    contract: 'MaterialRequestSentinel/v1',
    sourceTextSha256: identitySha256,
    sourceProjectionSha256: sha256(text),
    required,
    explicitTargetPaths,
    mutationFacetCount,
    targetSetComplete: required && !hasUniversalScope(text)
      && mutationFacetCount === 1
      && exactTargetEnumeration(text, explicitTargetPaths),
  };
  const sentinel = deepFreeze({ ...body, sha256: sha256(canonicalJson(body)) });
  REQUEST_SENTINEL_AUTHORITY.set(sentinel, canonicalJson(sentinel));
  return sentinel;
}

function exactMaterialRequestSentinel(sentinel, sourceText, sourceIdentitySha256) {
  return sentinel?.contract === 'MaterialRequestSentinel/v1'
    && Object.isFrozen(sentinel)
    && REQUEST_SENTINEL_AUTHORITY.get(sentinel) === canonicalJson(sentinel)
    && sentinel.sourceTextSha256 === String(sourceIdentitySha256 || sha256(sourceText))
    && sentinel.sourceProjectionSha256 === sha256(String(sourceText || ''))
    && sentinel.sha256 === sha256(canonicalJson({
      contract: sentinel.contract,
      sourceTextSha256: sentinel.sourceTextSha256,
      sourceProjectionSha256: sentinel.sourceProjectionSha256,
      required: sentinel.required,
      explicitTargetPaths: sentinel.explicitTargetPaths,
      mutationFacetCount: sentinel.mutationFacetCount,
      targetSetComplete: sentinel.targetSetComplete,
    })) ? sentinel : null;
}

function hasUniversalScope(text) {
  return /\b(?:elk(?:e|en)?|alle|alles|ieder(?:e|en)?|each|every|all)\b/iu
    .test(String(text || ''));
}

function activeConditions(understanding) {
  return (Array.isArray(understanding?.conditions) ? understanding.conditions : [])
    .filter((condition) => ['active', 'required', 'unmet'].includes(condition?.state)
      && (condition?.role === 'action' || condition?.role === 'completion'))
    .map((condition, index) => ({
      index,
      role: condition.role,
      requirementIds: uniqueStrings(condition.requirementIds),
      afterRequirementIds: uniqueStrings(condition.afterRequirementIds),
      statementSha256: sha256(String(condition.statement || '')),
    }))
    // Een losse kwalitatieve actiezin (zoals "kies zelf iets bruikbaars") is
    // richting voor Eva, maar geen afzonderlijke uitvoerbare verplichting. De
    // modelgekozen write/read-keten mag daardoor niet na werkelijk schrijven
    // alsnog administratief als mislukt eindigen. Een completionvoorwaarde
    // blijft wel altijd bindend.
    .filter((condition) => condition.role === 'completion'
      || condition.requirementIds.length > 0);
}

function inertObligation() {
  return deepFreeze({
    contract: 'MaterialCompletionObligation/v1',
    sha256: '',
    required: false,
    understandingSha256: '',
    selectedRequirementIds: [],
    deferredRequirementIds: [],
    discoveryRequirementIds: [],
    selectedWrites: [],
    selectedPlans: [],
    activeConditions: [],
    sentinelRequired: false,
    sentinelTargetPaths: [],
    sentinelTargetSetComplete: false,
    sentinelTargetsMatch: false,
    universalScope: false,
    universalScopeAuthority: null,
    scopeUnknown: false,
    invalidTargetSet: false,
  });
}

function materialObligationSourceContext({
  understanding, requestSourceText, requestSourceTextSha256,
  requestSentinel, trusted, synthetic,
}) {
  const sourceKind = understanding?.contract === 'TurnUnderstandingRecord/v1'
    ? understanding?.sourceKind : understanding?.source?.kind;
  const sourceOrigin = understanding?.source?.origin;
  const sourceTextSha256 = String(understanding?.sourceTextSha256
    || understanding?.source?.textSha256 || '');
  const temporalMode = String(understanding?.temporal?.mode || 'none');
  const understandingSha256 = String(
    understanding?.contract === 'TurnUnderstandingRecord/v1'
      ? understanding?.sourceSha256 : understanding?.sha256,
  );
  const requestIdentitySha256 = String(requestSourceTextSha256
    || sha256(String(requestSourceText || '')));
  const sentinel = exactMaterialRequestSentinel(
    requestSentinel, requestSourceText, requestIdentitySha256,
  );
  const sentinelRequired = sentinel?.required === true;
  const invalid = trusted !== true || synthetic === true || sourceKind !== 'model'
    || (sourceOrigin && sourceOrigin !== 'user' && sourceOrigin !== 'resume')
    || !['now', 'none'].includes(temporalMode)
    || !SHA256_RE.test(understandingSha256)
    || (!sentinelRequired
      && !['act', 'inspect'].includes(understanding?.firstDecision?.kind));
  return { invalid, sourceTextSha256, understandingSha256, sentinel, sentinelRequired };
}

/**
 * Selects only host-authorised material obligations. Deferred model actions are
 * retained as blockers, never promoted to executable authority and never copy
 * their arguments into this contract.
 */
export function materialCompletionObligation({
  understanding = null,
  plans = [],
  sourceText = '',
  requestSourceText = sourceText,
  requestSourceTextSha256 = '',
  requestSentinel = null,
  trusted = false,
  synthetic = false,
} = {}) {
  const sourceContext = materialObligationSourceContext({
    understanding, requestSourceText, requestSourceTextSha256,
    requestSentinel, trusted, synthetic,
  });
  if (sourceContext.invalid) return inertObligation();
  const {
    sourceTextSha256, understandingSha256, sentinel, sentinelRequired,
  } = sourceContext;

  const actions = Array.isArray(understanding?.actions) ? understanding.actions : [];
  const selectedRequirementIds = uniqueStrings(understanding?.firstDecision?.requirementIds);
  const selectedSet = new Set(selectedRequirementIds);
  const issuedPlans = Array.isArray(plans) ? plans : [];

  const selectedPlans = selectedRequirementIds.flatMap((requirementId) => {
    const action = actions.find((candidate) => candidate?.requirementId === requirementId);
    const plan = issuedPlans.find((candidate) => candidate?.requirementId === requirementId);
    if (!action || !plan || action.polarity !== 'request' || action.authorization !== 'allowed'
      || !isSourceBound(action, sourceText, sourceTextSha256)
      || plan.capabilityId !== action.capabilityId
      || plan.operationId !== action.operationId
      || plan.effect !== action.effect) return [];
    return [{
      requirementId,
      capabilityId: action.capabilityId,
      operationId: action.operationId,
      effect: action.effect,
      toolName: String(plan.toolName || ''),
      args: plan.args && typeof plan.args === 'object' && !Array.isArray(plan.args)
        ? structuredClone(plan.args) : {},
    }];
  });

  const selectedWrites = selectedRequirementIds.flatMap((requirementId) => {
    const action = actions.find((candidate) => candidate?.requirementId === requirementId);
    const plan = selectedPlans.find((candidate) => candidate.requirementId === requirementId);
    if (!action || !plan || !isWorkspaceWrite(action)) return [];
    const canonicalPath = canonicalWorkspaceFile(plan.args?.pad);
    return canonicalPath ? [{
      requirementId,
      capabilityId: action.capabilityId,
      operationId: action.operationId,
      canonicalPath,
    }] : [];
  });

  const deferredRequirementIds = actions.flatMap((action) => {
    const reasons = uniqueStrings(action?.boundaryReasons);
    return !selectedSet.has(String(action?.requirementId || ''))
      && action?.polarity === 'request' && action?.authorization === 'denied'
      && isWorkspaceWrite(action) && isSourceBound(action, sourceText, sourceTextSha256)
      && reasons.length > 0
      && reasons.every((reason) => DEFERRED_BOUNDARY_REASONS.has(reason))
      ? [String(action.requirementId)] : [];
  });

  const discoveryRequirementIds = selectedPlans
    .filter((plan) => isWorkspaceDiscovery(plan))
    .map((plan) => plan.requirementId);
  const conditions = activeConditions(understanding);
  const universalScope = hasUniversalScope(sourceText);
  const targetIds = selectedWrites.map((item) => item.requirementId);
  const targetPaths = selectedWrites.map((item) => item.canonicalPath);
  const sentinelTargetPaths = uniqueStrings(sentinel?.explicitTargetPaths);
  const sentinelTargetSetComplete = sentinel?.targetSetComplete === true;
  const sentinelTargetsMatch = sentinelTargetSetComplete
    && exactStringArray(
      [...targetPaths].sort((left, right) => left.localeCompare(right)),
      [...sentinelTargetPaths].sort((left, right) => left.localeCompare(right)),
    );
  const invalidTargetSet = new Set(targetIds).size !== targetIds.length
    || new Set(targetPaths).size !== targetPaths.length;
  const universalScopeAuthority = null;
  const unlinkedCondition = conditions.some((condition) => !condition.requirementIds.length);
  const required = sentinelRequired || selectedWrites.length > 0 || deferredRequirementIds.length > 0;

  const body = {
    contract: 'MaterialCompletionObligation/v1',
    required,
    understandingSha256,
    requestSourceTextSha256: sentinel?.sourceTextSha256 || '',
    selectedRequirementIds,
    deferredRequirementIds,
    discoveryRequirementIds,
    selectedWrites,
    selectedPlans,
    activeConditions: conditions,
    sentinelRequired,
    sentinelTargetPaths,
    sentinelTargetSetComplete,
    sentinelTargetsMatch,
    universalScope,
    universalScopeAuthority,
    scopeUnknown: required && (
      (sentinelRequired && !sentinelTargetsMatch)
      ||
      (universalScope && universalScopeAuthority == null)
      || deferredRequirementIds.length > 0 || unlinkedCondition || invalidTargetSet
    ),
    invalidTargetSet,
  };
  const authorityBody = { ...body };
  delete authorityBody.understandingSha256;
  return deepFreeze({
    ...body,
    sha256: sha256(canonicalJson(authorityBody)),
  });
}

function hydrateExecutionRow(raw) {
  try {
    const row = validateExecutionRecord(raw);
    if (!row || row.status !== 'done' || !Number.isSafeInteger(Number(row.rowid))
      || Number(row.rowid) <= 0) return null;
    const args = JSON.parse(String(row.args));
    const semanticBinding = JSON.parse(String(row.semantic_binding));
    const outputPayload = JSON.parse(String(row.output));
    return {
      ...row,
      rowid: Number(row.rowid),
      args,
      semanticBinding,
      outputPayload,
    };
  } catch {
    return null;
  }
}

function completeActionResult(payload) {
  return payload?.contract === 'ActionResult/v1'
    && payload.ok === true && payload.verified === true
    && payload.partial === false && payload.needs_followup === false;
}

function exactBinding(row, obligation, requirementId, capabilityId, operationId) {
  const binding = row?.semanticBinding;
  return binding?.understandingSha256 === obligation.understandingSha256
    && binding?.requirementId === requirementId
    && exactStringArray(binding?.addressesRequirementIds, [requirementId])
    && binding?.capabilityId === capabilityId
    && binding?.operationId === operationId;
}

function verifiedDiscovery(row, obligation, turnId) {
  const hydrated = row?.outputPayload && row?.semanticBinding && row?.args
    ? row : hydrateExecutionRow(row);
  if (!hydrated || hydrated.turn_id !== turnId || hydrated.tool !== 'lijst_bestanden'
    || !obligation.discoveryRequirementIds.includes(hydrated.semanticBinding?.requirementId)
    || !exactBinding(
      hydrated, obligation, hydrated.semanticBinding?.requirementId,
      'workspace.file.list', 'list',
    )
    || !completeActionResult(hydrated.outputPayload)) return null;
  const plan = obligation.selectedPlans.find((candidate) => (
    candidate.requirementId === hydrated.semanticBinding.requirementId
  ));
  if (!plan || canonicalJson(hydrated.args) !== canonicalJson(plan.args)) return null;
  const fileManifest = hydrated.outputPayload.file_manifest;
  if (!validWorkspaceFileManifest(fileManifest, {
    root: hydrated.args.pad,
    query: hydrated.args.zoek || '',
  }) || !revalidateWorkspaceFileManifest(fileManifest, {
    root: hydrated.args.pad,
    query: hydrated.args.zoek || '',
  }) || fileManifest.complete !== true || fileManifest.truncated !== false) return null;
  return { row: hydrated, fileManifest };
}

/**
 * Issues an in-process host capability for exactly these targets. The WeakMap
 * deliberately makes a serialized or structured-cloned manifest powerless.
 */
export function sealMaterialTargetManifest({
  obligation = null,
  turnId = '',
  scopeComplete = false,
  discoveryRow = null,
} = {}) {
  const targetTurnId = String(turnId || '').trim();
  if (obligation?.contract !== 'MaterialCompletionObligation/v1'
    || obligation.required !== true || scopeComplete !== true || !targetTurnId
    || obligation.invalidTargetSet === true || obligation.scopeUnknown === true
    || !obligation.selectedWrites?.length
    || obligation.deferredRequirementIds?.length) return null;

  const targets = obligation.selectedWrites.map((item) => ({
    requirementId: item.requirementId,
    canonicalPath: item.canonicalPath,
  }));
  let discovery = null;
  if (obligation.universalScope === true) {
    if (obligation.universalScopeAuthority == null) return null;
  } else if (discoveryRow) {
    discovery = verifiedDiscovery(discoveryRow, obligation, targetTurnId);
    if (!discovery) return null;
  }

  const body = {
    contract: 'MaterialTargetManifest/v1',
    turnId: targetTurnId,
    understandingSha256: obligation.understandingSha256,
    scopeComplete: true,
    targets,
    discovery: discovery ? {
      executionKey: discovery.row.execution_key,
      rowid: discovery.row.rowid,
      completionRecordSha256: discovery.row.completion_record_sha256,
      fileManifestSha256: discovery.fileManifest.sha256,
    } : null,
  };
  const manifest = deepFreeze({ ...body, sha256: sha256(canonicalJson(body)) });
  MANIFEST_AUTHORITY.set(manifest, deepFreeze({
    obligation,
    turnId: targetTurnId,
    canonical: canonicalJson(manifest),
  }));
  return manifest;
}

function incompleteProof(obligation, {
  missingTargets = null,
  deferredRequirementIds = null,
  unresolvedConditions = [],
  manifestReason = null,
  ledgerReason = null,
  scopeUnknown = null,
  hadVerifiedRead = false,
} = {}) {
  const missing = missingTargets || (obligation?.selectedWrites || [])
    .map((item) => item.requirementId);
  const deferred = deferredRequirementIds || obligation?.deferredRequirementIds || [];
  return {
    complete: false,
    missingTargets: [...missing],
    hadVerifiedRead: hadVerifiedRead === true,
    unmetState: {
      scopeUnknown: scopeUnknown ?? Boolean(obligation?.scopeUnknown),
      deferredRequirementIds: [...deferred],
      missingTargetRequirementIds: [...missing],
      unresolvedConditions,
      manifestReason,
      ledgerReason,
    },
  };
}

function manifestAuthority(manifest, obligation) {
  if (!manifest || typeof manifest !== 'object') return null;
  const authority = MANIFEST_AUTHORITY.get(manifest);
  if (!authority || authority.obligation !== obligation
    || authority.canonical !== canonicalJson(manifest)
    || manifest.sha256 !== sha256(canonicalJson((({ sha256: _, ...body }) => body)(manifest)))) {
    return null;
  }
  return authority;
}

function targetWriteCandidate(row, obligation, target) {
  if (row.tool !== 'schrijf_bestand'
    || !exactBinding(row, obligation, target.requirementId, 'workspace.file.write', 'write')) {
    return null;
  }
  const canonicalPath = canonicalWorkspaceFile(row.args?.pad);
  if (canonicalPath !== target.canonicalPath) return { invalid: true };
  const payload = row.outputPayload;
  const content = typeof row.args?.inhoud === 'string' ? row.args.inhoud : null;
  if (!completeActionResult(payload) || payload.changed !== true || content == null
    || canonicalWorkspaceFile(payload?.data?.path) !== target.canonicalPath) return { invalid: true };
  const contentSha256 = sha256(content);
  const bytes = Buffer.byteLength(content, 'utf8');
  if (payload?.data?.material_sha256 !== contentSha256
    || Number(payload?.data?.bytes) !== bytes) return { invalid: true };
  return { row, content, sha256: contentSha256, bytes };
}

function targetReadCandidate(row, obligation, target, write) {
  if (row.tool !== 'lees_bestand'
    || !exactBinding(row, obligation, target.requirementId, 'workspace.file.read', 'read')) {
    return false;
  }
  const payload = row.outputPayload;
  const receipt = payload?.artifact_receipt;
  return row.rowid > write.row.rowid
    && canonicalWorkspaceFile(row.args?.pad) === target.canonicalPath
    && completeActionResult(payload)
    && payload.data === write.content
    && receipt?.complete_read === true
    && canonicalWorkspaceFile(receipt?.path) === target.canonicalPath
    && receipt.sha256 === write.sha256
    && Number(receipt.bytes) === write.bytes
    && Number(receipt.source_bytes) === write.bytes
    && artifactReceiptChainMatches({
      write: write.row,
      read: row,
      path: target.canonicalPath,
      normalizePath: canonicalArtifactDisplayPath,
    });
}

function verifiedGeneralRequirement(rows, obligation, requirementId) {
  const plan = obligation.selectedPlans.find((candidate) => (
    candidate.requirementId === requirementId
  ));
  if (!plan) return false;
  return rows.some((row) => {
    if (!exactBinding(row, obligation, requirementId, plan.capabilityId, plan.operationId)
      || !completeActionResult(row.outputPayload)) return false;
    if (plan.capabilityId === 'workspace.file.read') {
      const receipt = row.outputPayload?.artifact_receipt;
      return receipt?.complete_read === true
        && canonicalWorkspaceFile(row.args?.pad) === canonicalWorkspaceFile(plan.args?.pad)
        && canonicalWorkspaceFile(receipt?.path) === canonicalWorkspaceFile(plan.args?.pad);
    }
    return true;
  });
}

/**
 * Proves completion exclusively from canonical, completion-hash-validated rows
 * from the manifest's exact turn.
 */
export function durableMaterialProof({
  obligation = null,
  executionRows = [],
  manifest = null,
} = {}) {
  if (obligation?.contract !== 'MaterialCompletionObligation/v1'
    || obligation.required !== true) {
    return incompleteProof(obligation, { manifestReason: 'no_material_obligation' });
  }
  if (obligation.deferredRequirementIds?.length) {
    return incompleteProof(obligation, {
      manifestReason: 'deferred_actions_have_no_authority',
      scopeUnknown: true,
    });
  }
  const authority = manifestAuthority(manifest, obligation);
  if (!authority || manifest?.turnId !== authority.turnId
    || manifest?.understandingSha256 !== obligation.understandingSha256) {
    return incompleteProof(obligation, { manifestReason: 'missing_or_invalid_host_manifest' });
  }

  const rawRows = Array.isArray(executionRows) ? executionRows : [];
  const rows = rawRows.map(hydrateExecutionRow);
  if (rows.some((row) => !row)
    || rows.some((row) => row.turn_id !== manifest.turnId)
    || new Set(rows.map((row) => row.rowid)).size !== rows.length) {
    return incompleteProof(obligation, { ledgerReason: 'invalid_durable_execution_row' });
  }
  rows.sort((left, right) => left.rowid - right.rowid);
  const hadVerifiedRead = rows.some((row) => row.tool === 'lees_bestand'
    && completeActionResult(row.outputPayload)
    && row.outputPayload?.artifact_receipt?.complete_read === true);

  if (manifest.discovery) {
    const discovery = rows.find((row) => row.execution_key === manifest.discovery.executionKey);
    const verified = discovery && verifiedDiscovery(discovery, obligation, manifest.turnId);
    if (!verified
      || discovery.rowid !== manifest.discovery.rowid
      || discovery.completion_record_sha256 !== manifest.discovery.completionRecordSha256
      || verified.fileManifest.sha256 !== manifest.discovery.fileManifestSha256) {
      return incompleteProof(obligation, {
        manifestReason: 'discovery_receipt_missing_or_changed', hadVerifiedRead,
      });
    }
  }

  const artifacts = [];
  const proofExecutionKeys = new Set();
  const provenRequirementIds = new Set();
  let executableRunProofMissing = false;
  for (const target of obligation.selectedWrites) {
    if (executableWorkspaceArtifact(target.canonicalPath)) {
      // De immutable artifactstore eist voor uitvoerbare bytes tevens een
      // stabiele, exact gebonden run tussen write en read. Deze documentlane
      // verleent zonder zo'n bewijs bewust geen voltooiingsauthority.
      executableRunProofMissing = true;
      continue;
    }
    const writeRows = rows
      .map((row) => targetWriteCandidate(row, obligation, target))
      .filter(Boolean);
    const latestWrite = writeRows.at(-1);
    if (!latestWrite || latestWrite.invalid) continue;
    if (manifest.discovery && latestWrite.row.rowid <= manifest.discovery.rowid) continue;
    const read = rows.findLast((row) => targetReadCandidate(row, obligation, target, latestWrite));
    if (!read) continue;
    artifacts.push({
      requirementId: target.requirementId,
      role: 'result',
      path: target.canonicalPath,
      content: latestWrite.content,
      sha256: latestWrite.sha256,
      bytes: latestWrite.bytes,
      understandingSha256: obligation.understandingSha256,
      writeExecutionKey: latestWrite.row.execution_key,
      writeCompletionSha256: latestWrite.row.completion_record_sha256,
      readExecutionKey: read.execution_key,
      readCompletionSha256: read.completion_record_sha256,
      runExecutions: [],
    });
    proofExecutionKeys.add(latestWrite.row.execution_key);
    proofExecutionKeys.add(read.execution_key);
    provenRequirementIds.add(target.requirementId);
  }

  const materialRequirementIds = new Set(
    obligation.selectedWrites.map((target) => target.requirementId),
  );
  for (const requirementId of obligation.selectedRequirementIds) {
    if (materialRequirementIds.has(requirementId)) continue;
    if (provenRequirementIds.has(requirementId)) continue;
    if (verifiedGeneralRequirement(rows, obligation, requirementId)) {
      provenRequirementIds.add(requirementId);
    }
  }

  const missingTargets = obligation.selectedWrites
    .map((target) => target.requirementId)
    .filter((requirementId) => !provenRequirementIds.has(requirementId));
  const unresolvedConditions = obligation.activeConditions.flatMap((condition) => {
    if (!condition.requirementIds.length) return [{
      index: condition.index,
      role: condition.role,
      reason: 'unlinked_active_condition',
      requirementIds: [],
      afterRequirementIds: condition.afterRequirementIds,
      missingRequirementIds: [],
      missingAfterRequirementIds: condition.afterRequirementIds
        .filter((requirementId) => !provenRequirementIds.has(requirementId)),
    }];
    const missingRequirementIds = condition.requirementIds
      .filter((requirementId) => !provenRequirementIds.has(requirementId));
    const missingAfterRequirementIds = condition.afterRequirementIds
      .filter((requirementId) => !provenRequirementIds.has(requirementId));
    return missingRequirementIds.length || missingAfterRequirementIds.length ? [{
      index: condition.index,
      role: condition.role,
      reason: 'condition_requirements_unproven',
      requirementIds: condition.requirementIds,
      afterRequirementIds: condition.afterRequirementIds,
      missingRequirementIds,
      missingAfterRequirementIds,
    }] : [];
  });

  if (missingTargets.length || unresolvedConditions.length
    || artifacts.length !== obligation.selectedWrites.length) {
    return incompleteProof(obligation, {
      missingTargets,
      unresolvedConditions,
      scopeUnknown: unresolvedConditions.some((condition) => (
        condition.reason === 'unlinked_active_condition'
      )),
      ledgerReason: executableRunProofMissing ? 'executable_run_proof_missing' : null,
      hadVerifiedRead,
    });
  }

  return {
    complete: true,
    missingTargets: [],
    hadVerifiedRead,
    unmetState: {
      scopeUnknown: false,
      deferredRequirementIds: [],
      missingTargetRequirementIds: [],
      unresolvedConditions: [],
      manifestReason: null,
      ledgerReason: null,
    },
    artifacts,
    proofExecutionKeys: [...proofExecutionKeys].sort((left, right) => left.localeCompare(right)),
  };
}

export function fixedMaterialIncompleteReply({
  hadVerifiedRead = false,
  handoffVerified = false,
} = {}) {
  const status = hadVerifiedRead
    ? 'Ik heb de bronnen bekeken, maar de gevraagde correcties en het controleerbare interne resultaat zijn nog niet aantoonbaar opgeslagen.'
    : 'De gevraagde correcties en het controleerbare interne resultaat zijn nog niet aantoonbaar opgeslagen.';
  const handoff = handoffVerified
    ? ' De oorspronkelijke opdracht staat één keer als open werk vast.' : '';
  return `${status}${handoff} Er is niets gepubliceerd, verstuurd, gekocht of aangemaakt.`;
}

export function fixedMaterialAuthorizationReply() {
  return 'Ik kan deze wijziging alleen uitvoeren via een geverifieerde bewonersingang. Er is niets gewijzigd, gepubliceerd, verstuurd, gekocht of aangemaakt.';
}
