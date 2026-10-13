import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-material-lifecycle-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  claimTurnToolExecution, completeTurnToolExecution, db,
} = await import('../src/db.js');
const { config } = await import('../src/config.js');
const {
  abandonInterpretationRetry,
  completeInterpretationRetry,
  createInterpretationRetryHandoff,
} = await import('../src/mind/portfolio.js');
const { materialCompletionObligation } = await import('../src/mind/materialcompletion.js');
const { projectTurnUnderstandingTerminalOutcome } = await import(
  '../src/mind/turnunderstandingoutcome.js'
);
const { createWorkCompletionPlanSeal } = await import('../src/mind/workcompletion.js');
const {
  appendMaterialEffectIntent,
  bindMaterialExecutionContract,
  claimMaterialEffectWrite,
  issueMaterialEffectIntentCapability,
  issueMaterialEffectWriteClaimCapability,
  issueMaterialEffectReconciliationCapability,
  issueMaterialExecutionContractCapability,
  materialEffectIntent,
  materialEffectReconciliation,
  materialEffectWriteClaim,
  materialEffectWriteFenceCurrent,
  materialExecutionContract,
  materialExecutionContractByHandoff,
  materialLifecycleStoreStatus,
  reconcileMaterialEffect,
  registerMaterialEffectRecoveryVerifier,
  registerMaterialLifecycleAuthorityVerifier,
} = await import('../src/mind/materiallifecycle.js');
const { canonicalJson } = await import('../src/util.js');
const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function withoutExecutionTrigger(name, operation) {
  const row = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name);
  assert.ok(row?.sql, `missing execution trigger ${name}`);
  db.exec(`DROP TRIGGER main.${name}`);
  try { return operation(); } finally { db.exec(row.sql); }
}
const execFileAsync = promisify(execFile);
const authorityAttestations = new WeakMap();
const recoveryAttestations = new WeakMap();
let sequence = 0;

registerMaterialLifecycleAuthorityVerifier(
  (context) => authorityAttestations.get(context) || null,
);
registerMaterialEffectRecoveryVerifier(
  (context) => recoveryAttestations.get(context) || null,
);

after(() => {
  if (db.open) db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function productionUnderstandingBody({
  label, sourceSha256, sourceTextSha256, obligation: frozenObligation,
  conditionStatements = [],
}) {
  const selectedPlans = Array.isArray(frozenObligation?.selectedPlans)
    ? frozenObligation.selectedPlans : [];
  const selectedRequirementIds = Array.isArray(frozenObligation?.selectedRequirementIds)
    ? frozenObligation.selectedRequirementIds : [];
  return {
    contract: 'TurnUnderstandingRecord/v1', sourceSha256,
    sourceKind: 'model', sourceTextSha256, dialogueAct: 'request',
    goal: {
      statement: `Voer de materiaalopdracht ${label} uit.`,
      outcome: `De materiaalopdracht ${label} is exact uitgevoerd.`,
      beneficiary: 'Jaap',
    },
    conditions: (frozenObligation?.activeConditions || []).map((condition, index) => ({
      statement: conditionStatements[index] || `voorwaarde ${label} ${index + 1}`,
      role: condition.role, state: 'required',
      requirementIds: [...condition.requirementIds],
      afterRequirementIds: [...condition.afterRequirementIds],
    })),
    temporal: { mode: 'now', notBefore: null, dueBy: null, condition: null },
    durability: 'turn', requestedEffects: ['workspace'], deniedEffects: [],
    actions: selectedPlans.map((plan) => ({
      requirementId: plan.requirementId, capabilityId: plan.capabilityId,
      operationId: plan.operationId, polarity: 'request', effect: plan.effect,
      authorization: 'allowed', sourceSpans: [{ start: 0, end: 1 }],
      boundaryReasons: [],
    })),
    firstDecision: {
      kind: 'act', requirementIds: [...selectedRequirementIds], replyDraftSha256: null,
    },
    decisionProjection: {
      proposedKind: 'act', proposedRequirementIds: [...selectedRequirementIds],
      acceptedRequirementIds: [...selectedRequirementIds], rejectedRequirementIds: [],
      unknownRequirementIds: [], hostAddedRequirementIds: [],
    },
    monitoring: null,
  };
}

function understandingAuthority(label, {
  sourceSha256 = sha256(`understanding-source:${label}:${sequence + 1}`),
  sourceTextSha256, obligation: frozenObligation,
  conditionStatements = [], recordTransform = (value) => value, origin = null,
  semanticAuthoritySha256 = sha256(`semantic-authority:${label}:${sequence + 1}`),
} = {}) {
  sequence += 1;
  const body = recordTransform(productionUnderstandingBody({
    label, sourceSha256, sourceTextSha256, obligation: frozenObligation,
    conditionStatements,
  }));
  const understanding = { ...body, sha256: sha256(canonicalJson(body)) };
  const turnId = `material-authority-turn:${label}:${sequence}`;
  const inserted = db.prepare(
    'INSERT INTO timeline(ts,kind,content,meta) VALUES (?,?,?,?)',
  ).run(
    new Date().toISOString(), 'turn_understanding', `authority ${label}`,
    JSON.stringify({
      turnId, interpretationSucceeded: true, understanding,
      conversationId: origin?.registry?.origin_conversation_id,
      originMessageId: origin?.registry?.origin_message_id,
      requester: origin?.requester || 'Jaap', semanticAuthoritySha256,
    }),
  );
  return {
    turnId, timelineId: Number(inserted.lastInsertRowid),
    understandingSha256: sourceSha256, recordSha256: understanding.sha256,
    understanding, semanticAuthoritySha256,
  };
}

function handoffFixture(label, {
  originalText: suppliedText = '', ingress: ingressOverrides = {},
} = {}) {
  sequence += 1;
  const originalText = suppliedText
    || `Corrigeer exact de materiaalbestanden voor ${label} ${sequence}.`;
  const conversationId = `web:material:${label}:${sequence}`;
  const message = db.prepare(`INSERT INTO messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`).run(
    new Date().toISOString(), 'web', conversationId, 'Jaap', 'user', originalText,
  );
  const messageId = String(message.lastInsertRowid);
  const supersessionScope = `web:material:${label}:${sequence}`;
  const supersessionToken = `material-token-${label}-${sequence}`;
  const sessionRef = `turn:${label}:${sequence}`;
  const ingressIdentity = {
    channel: 'web', conversationId: ingressOverrides.conversationId || conversationId,
    messageId: ingressOverrides.messageId || messageId,
  };
  const created = createInterpretationRetryHandoff({
    channel: 'web', conversationId,
    messageId, requester: 'Jaap', originalText,
    failureClass: 'interpreter_timeout', sessionRef,
    supersessionScope, supersessionToken,
  });
  seedAuthorizedTurnUnderstanding({
    database: db, ...ingressIdentity,
    requester: ingressOverrides.requester || 'Jaap',
    originalText: ingressOverrides.originalText || originalText,
    sessionRef, supersessionScope, supersessionToken,
    output: {
      durableHandoff: true, workItemId: Number(created.workItem.id),
      goalEpisodeId: Number(created.episode.id),
    },
  });
  const registry = db.prepare(`SELECT * FROM eva_interpretation_retry_handoffs
    WHERE work_item_id=? AND episode_id=?`).get(created.workItem.id, created.episode.id);
  assert.ok(registry);
  return {
    workItem: created.workItem, episode: created.episode, registry, originalText,
    requester: 'Jaap',
  };
}

function obligation(label, understandingSha256, requestSourceTextSha256, paths = Array.from(
  { length: 12 }, (_, index) => `data/workspace/${label}/result-${index + 1}.md`,
)) {
  const requirementIds = paths.map((_, index) => `r${index + 1}`);
  const body = {
    contract: 'MaterialCompletionObligation/v1', required: true,
    understandingSha256, requestSourceTextSha256,
    selectedRequirementIds: requirementIds,
    deferredRequirementIds: [], discoveryRequirementIds: [],
    selectedWrites: paths.map((canonicalPath, index) => ({
      requirementId: `r${index + 1}`,
      capabilityId: 'workspace.file.write', operationId: 'write', canonicalPath,
    })),
    selectedPlans: paths.map((canonicalPath, index) => ({
      requirementId: `r${index + 1}`,
      capabilityId: 'workspace.file.write', operationId: 'write',
      effect: 'workspace', toolName: 'schrijf_bestand',
      args: { pad: canonicalPath, inhoud: `# ${label} ${index + 1}\n` },
    })),
    activeConditions: [], sentinelRequired: false, sentinelTargetPaths: [],
    sentinelTargetSetComplete: false, sentinelTargetsMatch: false,
    universalScope: false, universalScopeAuthority: null,
    scopeUnknown: false, invalidTargetSet: false,
  };
  const authorityBody = structuredClone(body);
  delete authorityBody.understandingSha256;
  return { ...body, sha256: sha256(canonicalJson(authorityBody)) };
}

function rehashObligation(value) {
  const body = structuredClone(value);
  delete body.sha256;
  delete body.understandingSha256;
  return { ...value, sha256: sha256(canonicalJson(body)) };
}

function governedProductionPlans(selectedPlans, understandingSha256) {
  return selectedPlans.map((plan) => ({
    ...structuredClone(plan),
    idempotency: plan.operationId === 'write' ? 'cas' : 'deduplicated',
    proofFloor: plan.operationId === 'write' ? ['material_readback'] : [],
    supportsDeferred: false,
    execution: {
      mode: 'direct', materializable: [], target: null,
      proofOperations: [], recoveryOperations: [], fileArguments: [],
    },
    proofOperations: [], recoveryOperations: [],
    semanticBinding: {
      understandingSha256, requirementId: plan.requirementId,
      addressesRequirementIds: [plan.requirementId], basisReceiptIds: [],
      capabilityId: plan.capabilityId, operationId: plan.operationId,
      role: 'primary', executionType: null,
    },
  }));
}

function productionDecision(understanding) {
  return {
    kind: understanding.firstDecision.kind,
    requirementIds: [...understanding.firstDecision.requirementIds],
    replyDraft: null,
  };
}

function mixedProductionObligation(
  label, understandingSha256, sourceText, requestSourceTextSha256,
) {
  const sourceSpans = [{ start: 0, end: sourceText.length }];
  const action = (requirementId, capabilityId, operationId, effect) => ({
    requirementId, capabilityId, operationId, effect,
    polarity: 'request', authorization: 'allowed', sourceSpans, boundaryReasons: [],
  });
  const actions = [
    action('r1', 'workspace.file.list', 'list', 'read'),
    action('r2', 'workspace.file.read', 'read', 'read'),
    action('r3', 'workspace.file.write', 'write', 'workspace'),
  ];
  const targetPath = `data/workspace/${label}/result.md`;
  const understanding = {
    contract: 'TurnUnderstandingRecord/v1', sourceSha256: understandingSha256,
    sourceKind: 'model', sourceTextSha256: sha256(sourceText),
    conditions: [], temporal: { mode: 'now' }, actions,
    firstDecision: { kind: 'act', requirementIds: ['r1', 'r2', 'r3'] },
  };
  const plans = [
    {
      requirementId: 'r1', capabilityId: 'workspace.file.list',
      operationId: 'list', effect: 'read', toolName: 'lijst_bestanden',
      args: { pad: 'data/workspace' },
    },
    {
      requirementId: 'r2', capabilityId: 'workspace.file.read',
      operationId: 'read', effect: 'read', toolName: 'lees_bestand',
      args: { pad: `data/workspace/${label}/source.md` },
    },
    {
      requirementId: 'r3', capabilityId: 'workspace.file.write',
      operationId: 'write', effect: 'workspace', toolName: 'schrijf_bestand',
      args: { pad: targetPath, inhoud: `# ${label}\n` },
    },
  ];
  const projected = materialCompletionObligation({
    understanding, plans, sourceText, trusted: true, synthetic: false,
  });
  return rehashObligation({ ...projected, requestSourceTextSha256 });
}

function replaceRetryState(workItemId, transform) {
  const row = db.prepare('SELECT state_json FROM eva_work_items WHERE id=?')
    .get(workItemId);
  const state = JSON.parse(row.state_json);
  state.interpretation_retry = transform(state.interpretation_retry);
  db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
    .run(JSON.stringify(state), workItemId);
}

function contractFixture(label, overrides = {}) {
  const handoff = overrides.handoff || handoffFixture(label);
  const understandingSha256 = overrides.understandingSha256
    || sha256(`understanding-source:${label}:${sequence + 1}`);
  const baseObligation = obligation(
    label, understandingSha256, handoff.registry.raw_original_text_sha256,
    overrides.paths,
  );
  const frozenObligation = overrides.obligationFactory
    ? overrides.obligationFactory(understandingSha256, handoff)
    : overrides.obligationTransform
      ? overrides.obligationTransform(baseObligation)
      : overrides.obligation || baseObligation;
  const authority = overrides.authority || understandingAuthority(label, {
    sourceSha256: understandingSha256,
    sourceTextSha256: handoff.registry.original_text_sha256,
    obligation: frozenObligation,
    conditionStatements: overrides.conditionStatements,
    recordTransform: overrides.recordTransform,
    origin: handoff,
    semanticAuthoritySha256: overrides.semanticAuthoritySha256,
  });
  const sealObligation = overrides.obligationTransform
    ? baseObligation : frozenObligation;
  const sealDecision = {
    kind: overrides.planDecisionKind || 'act',
    requirementIds: [...sealObligation.selectedRequirementIds], replyDraft: null,
  };
  const planSeal = overrides.planSeal || createWorkCompletionPlanSeal({
    decision: sealDecision,
    plans: governedProductionPlans(
      sealObligation.selectedPlans, authority.understandingSha256,
    ),
  });
  let activeHandoff = handoff;
  if (overrides.activate !== false) {
    const active = completeInterpretationRetry(
      handoff.workItem, handoff.episode, authority.turnId, planSeal.sha256,
      authority.understandingSha256, frozenObligation.sha256,
      authority.semanticAuthoritySha256,
    );
    activeHandoff = { ...handoff, workItem: active.workItem, episode: active.episode };
  }
  return {
    handoff: activeHandoff,
    attestation: {
      contract: 'MaterialExecutionContractAuthority/v1',
      handoffKey: overrides.handoffKey || handoff.registry.handoff_key,
      rawBodySha256: overrides.rawBodySha256
        || handoff.registry.raw_original_text_sha256,
      projection: overrides.projection || {
        rule: 'trimmed-redacted-user-text/v1',
        sha256: handoff.registry.original_text_sha256,
      },
      workItemId: overrides.workItemId || Number(handoff.workItem.id),
      episodeId: overrides.episodeId || Number(handoff.episode.id),
      sourceTurnId: authority.turnId,
      authorityTimelineId: authority.timelineId,
      understandingSha256: authority.understandingSha256,
      semanticAuthoritySha256: authority.semanticAuthoritySha256,
      planSeal, obligation: frozenObligation,
    },
  };
}

function issueContract(attestation) {
  const context = Object.freeze({});
  authorityAttestations.set(context, attestation);
  return issueMaterialExecutionContractCapability(context);
}

function bindContract(label, overrides = {}) {
  const fixture = contractFixture(label, overrides);
  const result = bindMaterialExecutionContract(issueContract(fixture.attestation));
  assert.equal(result.kind, 'created');
  return { ...fixture, value: result.value };
}

function semanticBinding(contractValue, requirementId, operation) {
  return {
    understandingSha256: contractValue.understandingSha256,
    requirementId, addressesRequirementIds: [requirementId], basisReceiptIds: [],
    capabilityId: `workspace.file.${operation}`, operationId: operation,
    role: 'proof', executionType: 'direct',
  };
}

function readOutput(canonicalPath, current) {
  const absent = current?.kind === 'absent';
  if (absent) return JSON.stringify({
    contract: 'WorkspacePathAbsenceReceipt/v1', tool: '@host/workspace.lstat',
    ok: true, changed: false, verified: true, partial: false,
    needs_followup: false, path: canonicalPath, absent: true, noFollow: true,
  });
  const content = absent ? null : String(current?.text ?? current ?? '');
  const bytes = absent ? Buffer.alloc(0) : Buffer.from(content, 'utf8');
  return JSON.stringify({
    contract: 'ActionResult/v1', tool: 'lees_bestand', ok: true,
    changed: false, verified: true,
    evidence: { source: 'lees_bestand', summary: 'exacte volledige uitlezing' },
    needs_followup: false, partial: false, risk: 'normal', human_message: '',
    artifact_receipt: {
      path: canonicalPath, bytes: bytes.length, source_bytes: bytes.length,
      sha256: sha256(bytes), complete_read: true,
    },
    data: content,
  });
}

function completeLegacyV1Execution({ key, turnId, tool, args, binding, output }) {
  const storedArgs = canonicalJson(args);
  const storedBinding = binding == null ? null : canonicalJson(binding);
  const semanticHash = sha256(canonicalJson({ turnId, tool, args, binding }));
  const intentRecordSha256 = sha256(canonicalJson({
    turnId, tool, args, binding, semanticHash,
  }));
  const executionSeq = Number(db.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
    FROM main.turn_tool_executions`).get().value);
  const startedTs = new Date().toISOString();
  const finishedTs = new Date(Date.now() + 1).toISOString();
  const outputSha256 = sha256(output);
  let materialSha256 = null;
  try {
    const parsed = JSON.parse(output);
    const candidate = parsed?.data?.material_sha256 || parsed?.artifact_receipt?.sha256;
    if (/^[a-f0-9]{64}$/u.test(String(candidate || ''))) materialSha256 = candidate;
  } catch { /* ordinary output */ }
  const completionRecordSha256 = sha256(canonicalJson({
    executionKey: key, turnId, tool, args: storedArgs, semanticHash,
    semanticBinding: storedBinding, intentRecordSha256, status: 'done',
    startedTs, finishedTs, outputSha256, materialSha256,
  }));
  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,semantic_binding,
     intent_record_sha256,status,started_ts,finished_ts,output,output_sha256,
     material_sha256,completion_record_sha256,execution_seq,execution_contract,
     execution_verification)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    key, turnId, tool, storedArgs, semanticHash, storedBinding,
    intentRecordSha256, 'done', startedTs, finishedTs, output, outputSha256,
    materialSha256, completionRecordSha256, executionSeq, 'ExecutionRecord/v1', 'verified',
  );
}

function completeRead(contractValue, requirementId, canonicalPath, current, overrides = {}) {
  sequence += 1;
  const absent = current?.kind === 'absent';
  const key = overrides.executionKey || `material-read:${sequence}:${sha256(
    `${canonicalPath}:${overrides.label || 'read'}`,
  )}`;
  const intent = {
    executionKey: key,
    turnId: overrides.turnId || contractValue.executionTurnId,
    tool: overrides.tool || (absent ? '@host/workspace.lstat' : 'lees_bestand'),
    args: overrides.args || (absent
      ? { pad: canonicalPath, no_follow: true } : { pad: canonicalPath }),
    semanticBinding: overrides.semanticBinding
      || (absent ? {
        ...semanticBinding(contractValue, requirementId, 'read'),
        capabilityId: 'workspace.file.stat', operationId: 'lstat',
      } : semanticBinding(contractValue, requirementId, 'read')),
  };
  const output = overrides.output || readOutput(canonicalPath, current);
  if (overrides.executionContract === 'ExecutionRecord/v1') {
    completeLegacyV1Execution({
      key, turnId: intent.turnId, tool: intent.tool, args: intent.args,
      binding: intent.semanticBinding, output,
    });
  } else {
    const claimed = claimTurnToolExecution(intent);
    assert.equal(claimed.claimed, true);
    completeTurnToolExecution(key, output);
  }
  return db.prepare('SELECT * FROM turn_tool_executions WHERE execution_key=?')
    .get(key);
}

function intentFixture(contractValue, requirementId, canonicalPath, overrides = {}) {
  const oldText = overrides.oldText ?? `old:${canonicalPath}`;
  const plannedText = contractValue.obligation.selectedPlans.find((plan) => (
    plan.requirementId === requirementId
  ))?.args?.inhoud;
  const newText = overrides.newText ?? plannedText;
  assert.equal(typeof newText, 'string');
  const preRead = overrides.preRead || completeRead(
    contractValue, requirementId, canonicalPath,
    overrides.preimageAbsent ? { kind: 'absent' } : oldText,
    { label: 'pre-read', executionContract: overrides.executionContract },
  );
  return {
    oldText, newText, preRead,
    attestation: {
      contract: 'MaterialEffectIntentAuthority/v1',
      contractRef: contractValue.contractRef, requirementId, canonicalPath,
      intendedPostimage: {
        sha256: sha256(Buffer.from(newText, 'utf8')),
        bytes: Buffer.byteLength(newText, 'utf8'),
      },
      preReadExecutionKey: preRead.execution_key,
      preReadCompletionSha256: preRead.completion_record_sha256,
    },
  };
}

function issueIntent(attestation) {
  const context = Object.freeze({});
  authorityAttestations.set(context, attestation);
  return issueMaterialEffectIntentCapability(context);
}

function appendIntent(contractValue, requirementId, canonicalPath, overrides = {}) {
  const fixture = intentFixture(contractValue, requirementId, canonicalPath, overrides);
  const result = appendMaterialEffectIntent(issueIntent(fixture.attestation));
  assert.equal(result.kind, 'created');
  return { ...fixture, value: result.value };
}

function issueWriteClaim(intentFixtureValue, overrides = {}) {
  const intentValue = intentFixtureValue.value || intentFixtureValue;
  const generation = Number(overrides.generation || 0);
  const context = Object.freeze({});
  authorityAttestations.set(context, {
    contract: 'MaterialEffectWriteClaimAuthority/v1',
    intentRef: intentValue.intentRef, generation,
    physicalPath: overrides.physicalPath || intentValue.canonicalPath,
  });
  const capability = issueMaterialEffectWriteClaimCapability(context);
  assert.ok(capability);
  return claimMaterialEffectWrite(capability);
}

function startWrite(contractValue, intentFixtureValue, overrides = {}) {
  const claimed = issueWriteClaim(intentFixtureValue, overrides);
  assert.ok(['created', 'replay'].includes(claimed.kind));
  return db.prepare(`SELECT observed.observation_sequence AS sequence,execution.*
    FROM turn_tool_executions execution
    JOIN eva_material_execution_observations observed
      ON observed.execution_key=execution.execution_key
    WHERE execution.execution_key=?`).get(claimed.value.executionKey);
}

function recoveryFixture(
  contractValue, intentFixtureValue, current, {
    startedWrite = null, claimGeneration = 0, ...overrides
  } = {},
) {
  const intentValue = intentFixtureValue.value || intentFixtureValue;
  const started = startedWrite || startWrite(
    contractValue, intentFixtureValue, { generation: claimGeneration },
  );
  const recoveryRead = overrides.recoveryRead || completeRead(
    contractValue, intentValue.requirementId, intentValue.canonicalPath, current,
    { label: 'recovery-read' },
  );
  return {
    startedWrite: started, recoveryRead,
    attestation: {
      contract: 'MaterialEffectRecoveryObservation/v1',
      intentRef: intentValue.intentRef, claimGeneration,
      recoveryReadExecutionKey: recoveryRead.execution_key,
      recoveryReadCompletionSha256: recoveryRead.completion_record_sha256,
    },
  };
}

function issueRecovery(attestation) {
  const context = Object.freeze({});
  recoveryAttestations.set(context, attestation);
  return issueMaterialEffectReconciliationCapability(context);
}

function reconcileFixture(contractValue, intentFixtureValue, current, overrides = {}) {
  const fixture = recoveryFixture(contractValue, intentFixtureValue, current, overrides);
  const capability = issueRecovery(fixture.attestation);
  assert.ok(capability);
  return { ...fixture, result: reconcileMaterialEffect(capability) };
}

test('material lifecycle stays unavailable when the interpretation registry is absent', () => {
  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-material-no-registry-'));
  try {
    const result = runChild(`
      const {db}=await import('./src/db.js');
      const lifecycle=await import('./src/mind/materiallifecycle.js');
      const status=lifecycle.materialLifecycleStoreStatus();
      db.prepare('INSERT INTO timeline(ts,kind,content,meta) VALUES (?,?,?,?)')
        .run(new Date().toISOString(),'system','core remains writable','{}');
      process.stdout.write(JSON.stringify({status,core:Number(db.prepare(
        "SELECT count(*) n FROM timeline WHERE content='core remains writable'"
      ).get().n)}));
      db.close();
    `, isolated);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    const observed = JSON.parse(result.stdout);
    assert.deepEqual(observed.status, {
      contract: 'MaterialLifecycleStoreStatus/v1', available: false,
      reason: 'material_lifecycle_interpretation_registry_unavailable',
    });
    assert.equal(observed.core, 1);
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('contract binds one exact immutable handoff, raw body, work and episode', () => {
  assert.deepEqual(materialLifecycleStoreStatus(), {
    contract: 'MaterialLifecycleStoreStatus/v1', available: true,
  });
  assert.throws(
    () => registerMaterialLifecycleAuthorityVerifier(() => null),
    /only be registered once/u,
  );
  const fixture = contractFixture('stable');
  const retryState = JSON.parse(fixture.handoff.workItem.state_json)
    .interpretation_retry;
  const storedRecord = JSON.parse(db.prepare('SELECT meta FROM timeline WHERE id=?')
    .get(fixture.attestation.authorityTimelineId).meta).understanding;
  assert.equal(retryState.understandingSha256, fixture.attestation.understandingSha256);
  assert.equal(retryState.understandingSha256, storedRecord.sourceSha256);
  assert.notEqual(retryState.understandingSha256, storedRecord.sha256);
  assert.equal(issueContract({
    ...fixture.attestation, workItemId: fixture.attestation.workItemId + 999,
  }), null);
  assert.equal(issueContract({
    ...fixture.attestation, rawBodySha256: sha256('forged raw body'),
  }), null);
  assert.equal(issueContract({
    ...fixture.attestation, handoffKey: sha256('missing handoff'),
  }), null);

  const capability = issueContract(fixture.attestation);
  assert.ok(capability);
  assert.equal(issueMaterialExecutionContractCapability({ ...fixture.attestation }), null);
  const created = bindMaterialExecutionContract(capability);
  assert.equal(created.kind, 'created');
  assert.equal(created.value.contract, 'MaterialExecutionContract/v1');
  assert.equal(created.value.handoffKey, fixture.handoff.registry.handoff_key);
  assert.equal(created.value.rawBodySha256,
    fixture.handoff.registry.raw_original_text_sha256);
  assert.equal(created.value.workItemId, Number(fixture.handoff.workItem.id));
  assert.equal(created.value.episodeId, Number(fixture.handoff.episode.id));
  assert.match(created.value.handoffRegistrySha256, /^[a-f0-9]{64}$/u);
  assert.match(created.value.contractRef, /^material-contract:[a-f0-9]{64}$/u);
  assert.match(created.value.executionTurnId, /^material-execution:[a-f0-9]{64}$/u);
  assert.equal(created.value.sha256, sha256(canonicalJson({
    ...created.value,
    contractRef: undefined, sha256: undefined, createdAt: undefined,
  })));
  assert.throws(
    () => bindMaterialExecutionContract(capability), /exact one-shot capability/u,
  );

  const replay = bindMaterialExecutionContract(issueContract(
    structuredClone(fixture.attestation),
  ));
  assert.equal(replay.kind, 'replay');
  assert.deepEqual(replay.value, created.value);
  assert.deepEqual(materialExecutionContract(created.value.contractRef), created.value);
  assert.deepEqual(
    materialExecutionContractByHandoff(fixture.attestation.handoffKey), created.value,
  );
  assert.equal(issueContract({
    ...structuredClone(fixture.attestation),
    projection: {
      ...fixture.attestation.projection, sha256: sha256('different projection'),
    },
  }), null);
});

test('contract rejects cross-wired registry, production understanding and obligation sources', () => {
  const sourceB = sha256('timeline source B');
  const sourceC = sha256('obligation source C');
  const fixture = contractFixture('three-source-cross-wire', {
    obligationTransform: (base) => rehashObligation({
      ...base, requestSourceTextSha256: sourceC,
    }),
    recordTransform: (record) => ({ ...record, sourceTextSha256: sourceB }),
    projection: { rule: 'trimmed-redacted-user-text/v1', sha256: sourceB },
  });
  assert.notEqual(fixture.handoff.registry.raw_original_text_sha256, sourceB);
  assert.notEqual(fixture.handoff.registry.raw_original_text_sha256, sourceC);
  assert.notEqual(sourceB, sourceC);
  assert.equal(issueContract(fixture.attestation), null);
});

test('contract requires production understanding decisions, actions and conditions to match', () => {
  const cases = [
    ['decision', {
      recordTransform: (record) => ({
        ...record,
        firstDecision: { ...record.firstDecision, requirementIds: ['r2'] },
      }),
    }],
    ['action', {
      recordTransform: (record) => ({
        ...record,
        actions: record.actions.map((action, index) => (index ? action : {
          ...action, capabilityId: 'workspace.file.read', operationId: 'read',
          effect: 'read',
        })),
      }),
    }],
    ['condition', {
      obligationTransform: (base) => rehashObligation({
        ...base,
        activeConditions: [{
          index: 0, role: 'completion', requirementIds: ['r1'],
          afterRequirementIds: ['r1'], statementSha256: sha256('juiste voorwaarde'),
        }],
      }),
      conditionStatements: ['andere voorwaarde'],
    }],
  ];
  for (const [label, overrides] of cases) {
    const fixture = contractFixture(`understanding-${label}`, overrides);
    assert.equal(issueContract(fixture.attestation), null, label);
  }
});

test('contract accepts only the shared host production plan seal and recomputes its full plans', () => {
  const handoff = handoffFixture('shared-plan-seal');
  const understandingSha256 = sha256('shared-plan-seal-understanding');
  const frozenObligation = obligation(
    'shared-plan-seal', understandingSha256,
    handoff.registry.raw_original_text_sha256,
  );
  const authority = understandingAuthority('shared-plan-seal', {
    sourceSha256: understandingSha256,
    sourceTextSha256: handoff.registry.original_text_sha256,
    obligation: frozenObligation, origin: handoff,
  });
  const planSeal = createWorkCompletionPlanSeal({
    decision: productionDecision(authority.understanding),
    plans: governedProductionPlans(
      frozenObligation.selectedPlans, understandingSha256,
    ),
  });
  const active = completeInterpretationRetry(
    handoff.workItem, handoff.episode, authority.turnId, planSeal.sha256,
    understandingSha256, frozenObligation.sha256, authority.semanticAuthoritySha256,
  );
  const attestation = {
    contract: 'MaterialExecutionContractAuthority/v1',
    handoffKey: handoff.registry.handoff_key,
    rawBodySha256: handoff.registry.raw_original_text_sha256,
    projection: {
      rule: 'trimmed-redacted-user-text/v1',
      sha256: handoff.registry.original_text_sha256,
    },
    workItemId: Number(active.workItem.id), episodeId: Number(active.episode.id),
    sourceTurnId: authority.turnId, authorityTimelineId: authority.timelineId,
    understandingSha256, semanticAuthoritySha256: authority.semanticAuthoritySha256,
    planSeal, obligation: frozenObligation,
  };
  assert.ok(issueContract(attestation));

  const arbitrary = structuredClone(attestation);
  arbitrary.planSeal.sha256 = sha256('arbitrary cross-wired plan hash');
  assert.throws(
    () => issueContract(arbitrary),
    /invalid material execution contract host attestation/u,
  );
});

test('contract authority requires the exact complete write obligation and hash algorithm', () => {
  const cases = [
    ['wrong-hash', (base) => ({ ...base, sha256: sha256('forged obligation') })],
    ['scope-unknown', (base) => rehashObligation({ ...base, scopeUnknown: true })],
    ['deferred', (base) => rehashObligation({
      ...base, deferredRequirementIds: ['r2'],
    })],
    ['wrong-operation', (base) => rehashObligation({
      ...base,
      selectedWrites: base.selectedWrites.map((write, index) => (index ? write : {
        ...write, capabilityId: 'workspace.file.read', operationId: 'read',
      })),
    })],
    ['duplicate-target', (base) => rehashObligation({
      ...base,
      selectedWrites: [base.selectedWrites[0], { ...base.selectedWrites[0] }],
      selectedRequirementIds: ['r1', 'r1'],
    })],
    ['requirements-drift', (base) => rehashObligation({
      ...base, selectedRequirementIds: base.selectedRequirementIds.slice(1),
    })],
    ['understanding-drift', (base) => ({
      ...base, understandingSha256: sha256('different understanding'),
    })],
  ];
  for (const [label, mutate] of cases) {
    const fixture = contractFixture(`obligation-${label}`, {
      obligationTransform: mutate,
    });
    assert.throws(
      () => issueContract(fixture.attestation),
      /invalid material execution contract host attestation/u,
      label,
    );
  }
});

test('production mixed discovery/read/write obligation authorizes only its write subset', () => {
  let mixed;
  const fixture = contractFixture('mixed-production', {
    obligationFactory: (understandingSha256, handoff) => {
      mixed = mixedProductionObligation(
        'mixed-production', understandingSha256, handoff.originalText,
        handoff.registry.raw_original_text_sha256,
      );
      return mixed;
    },
  });
  assert.equal(mixed.scopeUnknown, false);
  assert.deepEqual(mixed.selectedRequirementIds, ['r1', 'r2', 'r3']);
  assert.deepEqual(mixed.discoveryRequirementIds, ['r1']);
  assert.deepEqual(
    mixed.selectedWrites.map((write) => write.requirementId), ['r3'],
  );
  const bound = bindMaterialExecutionContract(issueContract(fixture.attestation));
  assert.equal(bound.kind, 'created');
  const contractValue = bound.value;
  const readPlan = mixed.selectedPlans.find((plan) => plan.requirementId === 'r2');
  const readAttempt = intentFixture(
    contractValue, readPlan.requirementId, readPlan.args.pad,
    { newText: 'read-plannen hebben bewust geen schrijfpostimage' },
  );
  assert.equal(issueIntent(readAttempt.attestation), null);

  const write = mixed.selectedWrites[0];
  const writeAttempt = intentFixture(
    contractValue, write.requirementId, write.canonicalPath,
  );
  assert.equal(appendMaterialEffectIntent(
    issueIntent(writeAttempt.attestation),
  ).kind, 'created');
});

test('msg3804-vorm bindt geselecteerde read/write-plannen en attesteert alle geweigerde acties apart', () => {
  const sourceText = 'Inspecteer de drie inkomstenbestanden, corrigeer ze aantoonbaar en zet één bewijsgebonden economische stap; publiceer, mail, betaal of verwijder niets zonder toestemming.';
  const handoff = handoffFixture('msg3804-production-shape', { originalText: sourceText });
  const denied = [
    ['r4', 'external.publish', 'publish', 'external'],
    ['r5', 'external.email', 'send', 'external'],
    ['r6', 'external.payment', 'pay', 'external'],
    ['r7', 'workspace.file.delete', 'delete', 'workspace'],
  ].map(([requirementId, capabilityId, operationId, effect]) => ({
    requirementId, capabilityId, operationId, effect,
    polarity: 'request', authorization: 'denied',
    sourceSpans: [{ start: 0, end: sourceText.length }],
    boundaryReasons: ['requires_explicit_authority'],
  }));
  const fixture = contractFixture('msg3804-production-shape', {
    handoff,
    obligationFactory: (understandingSha256, currentHandoff) => (
      mixedProductionObligation(
        'msg3804-production-shape', understandingSha256,
        currentHandoff.originalText,
        currentHandoff.registry.raw_original_text_sha256,
      )
    ),
    recordTransform: (record) => ({
      ...record,
      actions: [...record.actions, ...denied],
      decisionProjection: {
        ...record.decisionProjection,
        proposedRequirementIds: [
          ...record.decisionProjection.proposedRequirementIds,
          ...denied.map((action) => action.requirementId),
        ],
        rejectedRequirementIds: denied.map((action) => action.requirementId),
      },
    }),
  });
  const result = bindMaterialExecutionContract(issueContract(fixture.attestation));
  assert.equal(result.kind, 'created');
  assert.deepEqual(result.value.excludedActions, denied);
  assert.equal(
    result.value.excludedActionsSha256, sha256(canonicalJson(denied)),
  );
  assert.deepEqual(
    result.value.planSeal.plans.map((plan) => plan.requirementId),
    ['r1', 'r2', 'r3'],
  );
});

test('contract weigert elke conversation-, message-, principal- of semantic-source cross-wire', () => {
  const cases = [
    ['conversation', (meta) => ({ ...meta, conversationId: 'web:ander-gesprek' })],
    ['message', (meta) => ({ ...meta, originMessageId: '999999' })],
    ['principal', (meta) => ({ ...meta, requester: 'Mallory' })],
    ['semantic', (meta) => ({
      ...meta, semanticAuthoritySha256: sha256('ander semantisch gezag'),
    })],
  ];
  for (const [label, mutate] of cases) {
    const fixture = contractFixture(`origin-cross-wire-${label}`);
    const row = db.prepare('SELECT meta FROM timeline WHERE id=?')
      .get(fixture.attestation.authorityTimelineId);
    db.prepare('UPDATE timeline SET meta=? WHERE id=?').run(
      JSON.stringify(mutate(JSON.parse(row.meta))),
      fixture.attestation.authorityTimelineId,
    );
    assert.equal(issueContract(fixture.attestation), null, label);
  }
});

test('registry, episode en ingress binden exact dezelfde oorsprong en failen zonder bewijs van ruwe bytes', () => {
  const rawText = `Corrigeer het materiaal met api key: sk-${'a'.repeat(24)} en bewaar geen geheim.`;
  const exactHandoff = handoffFixture('redacted-ingress-origin', {
    originalText: rawText,
  });
  assert.notEqual(
    exactHandoff.registry.raw_original_text_sha256,
    exactHandoff.registry.original_text_sha256,
  );
  const exact = contractFixture('redacted-ingress-origin', { handoff: exactHandoff });
  assert.equal(issueContract(exact.attestation), null,
    'een geredigeerde execution-row kan de afwijkende ruwe bronhash niet bewijzen');

  const mismatches = [
    ['conversation', { conversationId: 'web:cross-wired-ingress' }],
    ['message', { messageId: 'cross-wired-message' }],
    ['principal', { requester: 'Mallory' }],
    ['raw-source', { originalText: 'een andere ruwe opdracht' }],
  ];
  for (const [label, ingress] of mismatches) {
    const handoff = handoffFixture(`ingress-${label}`, { ingress });
    const fixture = contractFixture(`ingress-${label}`, { handoff });
    assert.equal(issueContract(fixture.attestation), null, label);
  }
});

test('pending, revoked and superseded handoffs cannot authorize new effects', () => {
  const pending = contractFixture('pending-authority', { activate: false });
  assert.equal(issueContract(pending.attestation), null);

  const beforeBind = contractFixture('revoked-before-bind');
  const issued = issueContract(beforeBind.attestation);
  assert.ok(issued);
  abandonInterpretationRetry(
    beforeBind.handoff.workItem, beforeBind.handoff.episode,
    'test revocation before contract bind', 'material-lifecycle-test',
  );
  assert.throws(
    () => bindMaterialExecutionContract(issued),
    /lost its durable authority/u,
  );

  const active = bindContract('revoked-before-intent');
  const historical = structuredClone(active.value);
  const revoked = abandonInterpretationRetry(
    active.handoff.workItem, active.handoff.episode,
    'test revocation before effect', 'material-lifecycle-test',
  );
  assert.equal(revoked.episode.state, 'abandoned');
  assert.deepEqual(materialExecutionContract(active.value.contractRef), historical);
  const target = active.value.obligation.selectedWrites[0];
  const intent = intentFixture(
    active.value, target.requirementId, target.canonicalPath,
  );
  assert.equal(issueIntent(intent.attestation), null);

  const between = bindContract('revoked-between-intent-issue-and-append');
  const betweenTarget = between.value.obligation.selectedWrites[0];
  const betweenIntent = intentFixture(
    between.value, betweenTarget.requirementId, betweenTarget.canonicalPath,
  );
  const intentCapability = issueIntent(betweenIntent.attestation);
  assert.ok(intentCapability);
  abandonInterpretationRetry(
    between.handoff.workItem, between.handoff.episode,
    'test revocation after intent issuance', 'material-lifecycle-test',
  );
  assert.throws(
    () => appendMaterialEffectIntent(intentCapability),
    /lost its active durable authority/u,
  );

  const superseded = contractFixture('superseded-authority');
  sequence += 1;
  const retry = JSON.parse(
    superseded.handoff.workItem.state_json,
  ).interpretation_retry;
  const conversationId = `web:material-superseding:${sequence}`;
  const messageId = `material-superseding-${sequence}`;
  const originalText = 'Latere authentieke opdracht.';
  const sessionRef = `turn:${sequence}`;
  const supersessionToken = `new-token:${sequence}`;
  const terminal = projectTurnUnderstandingTerminalOutcome({
    args: {
      channel: 'web', conversationId, messageId, requester: 'Jaap', originalText,
      sessionRef, supersessionScope: retry.supersessionScope, supersessionToken,
      syntheticTest: false,
    },
    kind: 'prepared',
    value: {
      channel: 'web', conversationId, originMessageId: messageId,
      turnId: sessionRef, reply: 'De latere opdracht is verwerkt.', syntheticTest: false,
    },
  });
  seedAuthorizedTurnUnderstanding({
    database: db, channel: 'web', conversationId, messageId, requester: 'Jaap',
    originalText, sessionRef,
    supersessionScope: retry.supersessionScope,
    supersessionToken, output: terminal,
  });
  assert.equal(issueContract(superseded.attestation), null);
});

test('expired or max-attempt understood retries revoke contract and intent authority', () => {
  const mutate = {
    expired: (retry) => {
      const createdAt = new Date(Date.now() - (6 * 60 * 60_000) - 1_000);
      return {
        ...retry, createdAt: createdAt.toISOString(),
        deadlineAt: new Date(createdAt.getTime() + (6 * 60 * 60_000)).toISOString(),
      };
    },
    exhausted: (retry) => ({ ...retry, attempts: 4 }),
  };
  for (const [label, transform] of Object.entries(mutate)) {
    const contractCandidate = contractFixture(`inactive-contract-${label}`);
    replaceRetryState(contractCandidate.handoff.workItem.id, transform);
    assert.equal(issueContract(contractCandidate.attestation), null, label);

    const active = bindContract(`inactive-intent-${label}`);
    const target = active.value.obligation.selectedWrites[0];
    const intent = intentFixture(
      active.value, target.requirementId, target.canonicalPath,
    );
    replaceRetryState(active.handoff.workItem.id, transform);
    assert.equal(issueIntent(intent.attestation), null, label);
  }
});

test('intent derives its preimage from an exact durable complete read receipt', () => {
  const { value: contractValue } = bindContract('intent');
  const target = contractValue.obligation.selectedWrites[0];
  const fixture = intentFixture(
    contractValue, target.requirementId, target.canonicalPath,
  );
  const mismatched = intentFixture(
    contractValue, target.requirementId, target.canonicalPath,
    { newText: 'door de caller vervangen bytes' },
  );
  assert.equal(issueIntent(mismatched.attestation), null);
  assert.equal(issueIntent({
    ...fixture.attestation,
    preReadCompletionSha256: sha256('forged completion'),
  }), null);
  assert.equal(issueIntent({
    ...fixture.attestation,
    preReadExecutionKey: 'material-read:missing',
  }), null);
  assert.throws(() => issueIntent({
    ...fixture.attestation,
    preimage: { kind: 'sha256', sha256: sha256('caller bytes'), bytes: 12 },
  }), /invalid material effect intent host attestation/u);
  const wrongPath = completeRead(
    contractValue, target.requirementId, 'data/workspace/other/result.md', 'wrong',
    { label: 'wrong-path' },
  );
  assert.equal(issueIntent({
    ...fixture.attestation,
    preReadExecutionKey: wrongPath.execution_key,
    preReadCompletionSha256: wrongPath.completion_record_sha256,
  }), null);
  const wrongTurn = completeRead(
    contractValue, target.requirementId, target.canonicalPath, fixture.oldText,
    { label: 'wrong-turn', turnId: 'material-execution:'.concat('0'.repeat(64)) },
  );
  assert.equal(issueIntent({
    ...fixture.attestation,
    preReadExecutionKey: wrongTurn.execution_key,
    preReadCompletionSha256: wrongTurn.completion_record_sha256,
  }), null);

  const capability = issueIntent(fixture.attestation);
  assert.ok(capability);
  const created = appendMaterialEffectIntent(capability);
  assert.equal(created.kind, 'created');
  assert.deepEqual(created.value.preimage, {
    kind: 'sha256', sha256: sha256(Buffer.from(fixture.oldText)),
    bytes: Buffer.byteLength(fixture.oldText),
  });
  assert.equal(created.value.preReadExecutionKey, fixture.preRead.execution_key);
  assert.equal(
    created.value.preReadCompletionSha256, fixture.preRead.completion_record_sha256,
  );
  assert.equal(created.value.preReadSequence, Number(db.prepare(`SELECT observation_sequence
    FROM eva_material_execution_observations WHERE execution_key=?`)
    .get(fixture.preRead.execution_key).observation_sequence));
  assert.match(created.value.intentRef, /^material-effect-intent:[a-f0-9]{64}$/u);
  assert.match(created.value.writeExecutionKey, /^material-write:[a-f0-9]{64}$/u);
  assert.throws(
    () => appendMaterialEffectIntent(capability), /exact one-shot capability/u,
  );
  assert.deepEqual(materialEffectIntent(created.value.intentRef), created.value);

  const replay = appendMaterialEffectIntent(issueIntent(structuredClone(fixture.attestation)));
  assert.equal(replay.kind, 'replay');
  assert.deepEqual(replay.value, created.value);
  const conflictAttestation = {
    ...structuredClone(fixture.attestation),
    intendedPostimage: {
      sha256: sha256('different intended bytes'),
      bytes: Buffer.byteLength('different intended bytes'),
    },
  };
  assert.equal(issueIntent(conflictAttestation), null);

  const counterfeitTarget = contractValue.obligation.selectedWrites[1];
  const counterfeit = intentFixture(
    contractValue, counterfeitTarget.requirementId,
    counterfeitTarget.canonicalPath, { newText: 'niet de bevroren planbytes' },
  );
  assert.equal(issueIntent(counterfeit.attestation), null);
});

test('material lifecycle consumeert exact legacy v1 en sequence-gebonden v2 readreceipts', () => {
  for (const executionContract of ['ExecutionRecord/v1', 'ExecutionRecord/v2']) {
    const label = executionContract.endsWith('/v1') ? 'ledger-v1' : 'ledger-v2';
    const { value: contractValue } = bindContract(label);
    const target = contractValue.obligation.selectedWrites[0];
    const fixture = intentFixture(
      contractValue, target.requirementId, target.canonicalPath, { executionContract },
    );
    assert.equal(fixture.preRead.execution_contract, executionContract);
    const capability = issueIntent(fixture.attestation);
    assert.ok(capability);
    const created = appendMaterialEffectIntent(capability);
    assert.equal(created.kind, 'created');
    assert.equal(created.value.preReadExecutionKey, fixture.preRead.execution_key);
  }
});

test('recovery requires a later durable read and derives applied/no-effect/manual itself', () => {
  const { value: contractValue } = bindContract('outcomes');
  const targets = contractValue.obligation.selectedWrites;

  const applied = appendIntent(
    contractValue, targets[0].requirementId, targets[0].canonicalPath,
  );
  assert.equal(issueMaterialEffectReconciliationCapability({
    intentRef: applied.value.intentRef, current: Buffer.from('forged'),
  }), null);
  const forgedContext = Object.freeze({});
  recoveryAttestations.set(forgedContext, {
    contract: 'MaterialEffectRecoveryObservation/v1',
    claimGeneration: 0,
    intentRef: applied.value.intentRef,
    recoveryReadExecutionKey: applied.preRead.execution_key,
    recoveryReadCompletionSha256: applied.preRead.completion_record_sha256,
    conflictFree: true,
  });
  assert.throws(
    () => issueMaterialEffectReconciliationCapability(forgedContext),
    /invalid material effect recovery host observation/u,
  );
  assert.equal(issueRecovery({
    contract: 'MaterialEffectRecoveryObservation/v1',
    claimGeneration: 0,
    intentRef: applied.value.intentRef,
    recoveryReadExecutionKey: applied.preRead.execution_key,
    recoveryReadCompletionSha256: applied.preRead.completion_record_sha256,
  }), null, 'the pre-read is not a post-write recovery read');
  assert.equal(issueRecovery(recoveryFixture(
    contractValue, applied, applied.newText, { startedWrite: null },
  ).attestation) !== null, true);
  // De vorige issuance is bewust niet verbruikt; maak een tweede exact geldige
  // read voor de daadwerkelijke reconciliation.
  const appliedRecovery = reconcileFixture(contractValue, applied, applied.newText, {
    startedWrite: db.prepare(`SELECT observed.observation_sequence AS sequence,
      execution.* FROM turn_tool_executions execution
      JOIN eva_material_execution_observations observed
        ON observed.execution_key=execution.execution_key
      WHERE execution.execution_key=?`).get(applied.value.writeExecutionKey),
  });
  assert.equal(appliedRecovery.result.kind, 'created');
  assert.equal(appliedRecovery.result.value.outcome, 'applied');
  assert.equal(appliedRecovery.result.value.nextAction, 'none');
  assert.equal(
    appliedRecovery.result.value.recoveryRead.executionKey,
    appliedRecovery.recoveryRead.execution_key,
  );
  assert.equal(
    appliedRecovery.result.value.recoveryRead.completionRecordSha256,
    appliedRecovery.recoveryRead.completion_record_sha256,
  );
  assert.ok(
    appliedRecovery.result.value.preRead.sequence
      < appliedRecovery.result.value.startedWrite.sequence,
  );
  assert.ok(
    appliedRecovery.result.value.startedWrite.sequence
      < appliedRecovery.result.value.recoveryRead.sequence,
  );
  assert.equal(Object.hasOwn(appliedRecovery.result.value, 'conflictFree'), false);
  assert.match(appliedRecovery.result.value.casContextSha256, /^[a-f0-9]{64}$/u);
  assert.deepEqual(
    materialEffectReconciliation(applied.value.intentRef),
    appliedRecovery.result.value,
  );

  const noEffect = appendIntent(
    contractValue, targets[1].requirementId, targets[1].canonicalPath,
  );
  const noEffectResult = reconcileFixture(
    contractValue, noEffect, noEffect.oldText,
  ).result.value;
  assert.equal(noEffectResult.outcome, 'no_effect');
  assert.equal(noEffectResult.nextAction, 'retry_cas');
  assert.equal(noEffectResult.recoveryGeneration, 1);
  assert.match(noEffectResult.recoveryWriteExecutionKey,
    /^material-write-recovery:[a-f0-9]{64}$/u);

  const mismatch = appendIntent(
    contractValue, targets[2].requirementId, targets[2].canonicalPath,
  );
  const mismatchResult = reconcileFixture(
    contractValue, mismatch, 'third state',
  ).result.value;
  assert.equal(mismatchResult.outcome, 'uncertain_manual');
  assert.equal(mismatchResult.reason, 'current_mismatch');

  const missing = appendIntent(
    contractValue, targets[3].requirementId, targets[3].canonicalPath,
  );
  const missingResult = reconcileFixture(
    contractValue, missing, { kind: 'absent' },
  ).result.value;
  assert.equal(missingResult.outcome, 'uncertain_manual');
  assert.equal(missingResult.reason, 'current_missing');
});

test('absent preimages require a future host no-follow lstat receipt', () => {
  const { value: contractValue } = bindContract('absent');
  const targets = contractValue.obligation.selectedWrites;
  const unchanged = appendIntent(
    contractValue, targets[0].requirementId, targets[0].canonicalPath,
    { preimageAbsent: true },
  );
  assert.deepEqual(unchanged.value.preimage, { kind: 'absent' });
  assert.equal(reconcileFixture(
    contractValue, unchanged, { kind: 'absent' },
  ).result.value.outcome, 'no_effect');

  const applied = appendIntent(
    contractValue, targets[1].requirementId, targets[1].canonicalPath,
    { preimageAbsent: true },
  );
  assert.equal(reconcileFixture(
    contractValue, applied, applied.newText,
  ).result.value.outcome, 'applied');
});

test('missing started write, stale recovery read and durable heads fail closed', () => {
  const { value: contractValue } = bindContract('head-conflicts');
  const targets = contractValue.obligation.selectedWrites;

  const missingStarted = appendIntent(
    contractValue, targets[0].requirementId, targets[0].canonicalPath,
  );
  const missingStartedRead = completeRead(
    contractValue, missingStarted.value.requirementId,
    missingStarted.value.canonicalPath, missingStarted.newText,
    { label: 'missing-started' },
  );
  assert.equal(issueRecovery({
    contract: 'MaterialEffectRecoveryObservation/v1',
    claimGeneration: 0,
    intentRef: missingStarted.value.intentRef,
    recoveryReadExecutionKey: missingStartedRead.execution_key,
    recoveryReadCompletionSha256: missingStartedRead.completion_record_sha256,
  }), null);

  const stalePreRead = intentFixture(
    contractValue, targets[4].requirementId, targets[4].canonicalPath,
  );
  const stalePreCapability = issueIntent(stalePreRead.attestation);
  assert.ok(stalePreCapability);
  sequence += 1;
  claimTurnToolExecution({
    executionKey: `material-pre-read-conflict:${sequence}`,
    turnId: contractValue.executionTurnId, tool: 'schrijf_bestand',
    args: { pad: targets[4].canonicalPath, inhoud: 'later path state' },
    semanticBinding: semanticBinding(contractValue, targets[4].requirementId, 'write'),
  });
  assert.throws(
    () => appendMaterialEffectIntent(stalePreCapability),
    /lost its exact pre-read head/u,
  );

  const staleBase = intentFixture(
    contractValue, targets[1].requirementId, targets[1].canonicalPath,
  );
  const staleRead = completeRead(
    contractValue, targets[1].requirementId, targets[1].canonicalPath,
    staleBase.newText, { label: 'stale-before-write' },
  );
  const staleIntentResult = appendMaterialEffectIntent(issueIntent(staleBase.attestation));
  assert.equal(staleIntentResult.kind, 'created');
  const staleIntent = { ...staleBase, value: staleIntentResult.value };
  startWrite(contractValue, staleIntent);
  assert.equal(issueRecovery({
    contract: 'MaterialEffectRecoveryObservation/v1',
    claimGeneration: 0,
    intentRef: staleIntent.value.intentRef,
    recoveryReadExecutionKey: staleRead.execution_key,
    recoveryReadCompletionSha256: staleRead.completion_record_sha256,
  }), null);

  const laterWrite = appendIntent(
    contractValue, targets[2].requirementId, targets[2].canonicalPath,
  );
  const started = startWrite(contractValue, laterWrite);
  sequence += 1;
  claimTurnToolExecution({
    executionKey: `material-conflicting-write:${sequence}`,
    turnId: contractValue.executionTurnId, tool: 'schrijf_bestand',
    args: { pad: laterWrite.value.canonicalPath, inhoud: 'conflicting later bytes' },
    semanticBinding: semanticBinding(
      contractValue, laterWrite.value.requirementId, 'write',
    ),
  });
  const laterWriteResult = reconcileFixture(
    contractValue, laterWrite, laterWrite.newText, { startedWrite: started },
  ).result.value;
  assert.equal(laterWriteResult.outcome, 'uncertain_manual');
  assert.equal(laterWriteResult.reason, 'later_conflict');

  const older = appendIntent(
    contractValue, targets[3].requirementId, targets[3].canonicalPath,
  );
  const olderStarted = startWrite(contractValue, older);
  const laterContract = bindContract('second-contract-same-path', {
    paths: [older.value.canonicalPath],
  }).value;
  appendIntent(laterContract, 'r1', older.value.canonicalPath);
  const laterIntentResult = reconcileFixture(
    contractValue, older, older.newText, { startedWrite: olderStarted },
  ).result.value;
  assert.equal(laterIntentResult.outcome, 'uncertain_manual');
  assert.equal(laterIntentResult.reason, 'later_conflict');
});

test('durable receipt rereads detect missing and tampered read authority', () => {
  const { value: contractValue } = bindContract('tampered-reads');
  const targets = contractValue.obligation.selectedWrites;
  const tamperedPreRead = appendIntent(
    contractValue, targets[0].requirementId, targets[0].canonicalPath,
  );
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?')
      .run('{}', tamperedPreRead.preRead.execution_key);
  });
  assert.throws(
    () => materialEffectIntent(tamperedPreRead.value.intentRef),
    /durable read receipt failed integrity verification/u,
  );

  const missingPreRead = appendIntent(
    contractValue, targets[1].requirementId, targets[1].canonicalPath,
  );
  withoutExecutionTrigger('turn_tool_executions_no_delete', () => {
    db.prepare('DELETE FROM turn_tool_executions WHERE execution_key=?')
      .run(missingPreRead.preRead.execution_key);
  });
  assert.throws(
    () => materialEffectIntent(missingPreRead.value.intentRef),
    /durable read receipt failed integrity verification/u,
  );

  const tamperedRecovery = appendIntent(
    contractValue, targets[2].requirementId, targets[2].canonicalPath,
  );
  const completed = reconcileFixture(
    contractValue, tamperedRecovery, tamperedRecovery.newText,
  );
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare(`UPDATE turn_tool_executions SET completion_record_sha256=?
      WHERE execution_key=?`).run(
      sha256('tampered recovery'), completed.recoveryRead.execution_key,
    );
  });
  assert.throws(
    () => materialEffectReconciliation(tamperedRecovery.value.intentRef),
    /durable read receipt failed integrity verification/u,
  );
});

test('recovery rejects a started write whose durable intent record was forged', () => {
  const { value: contractValue } = bindContract('forged-started-write');
  const targets = contractValue.obligation.selectedWrites;
  const invalidHash = appendIntent(
    contractValue, targets[0].requirementId, targets[0].canonicalPath,
  );
  startWrite(contractValue, invalidHash);
  withoutExecutionTrigger('turn_tool_executions_intent_immutable', () => {
    db.prepare(`UPDATE turn_tool_executions SET intent_record_sha256=?
      WHERE execution_key=?`).run(
      sha256('forged started intent'), invalidHash.value.writeExecutionKey,
    );
  });
  const invalidHashRead = completeRead(
    contractValue, invalidHash.value.requirementId,
    invalidHash.value.canonicalPath, invalidHash.newText,
    { label: 'invalid-started-hash' },
  );
  assert.equal(issueRecovery({
    contract: 'MaterialEffectRecoveryObservation/v1',
    claimGeneration: 0,
    intentRef: invalidHash.value.intentRef,
    recoveryReadExecutionKey: invalidHashRead.execution_key,
    recoveryReadCompletionSha256: invalidHashRead.completion_record_sha256,
  }), null);

  const alteredBinding = appendIntent(
    contractValue, targets[1].requirementId, targets[1].canonicalPath,
  );
  const started = startWrite(contractValue, alteredBinding);
  const binding = {
    ...JSON.parse(started.semantic_binding), addressesRequirementIds: ['r12'],
  };
  const intentRecordSha256 = sha256(canonicalJson({
    turnId: started.turn_id, tool: started.tool,
    args: JSON.parse(started.args), binding, semanticHash: started.semantic_hash,
  }));
  withoutExecutionTrigger('turn_tool_executions_intent_immutable', () => {
    db.prepare(`UPDATE turn_tool_executions
      SET semantic_binding=?,intent_record_sha256=? WHERE execution_key=?`).run(
      canonicalJson(binding), intentRecordSha256, alteredBinding.value.writeExecutionKey,
    );
  });
  const alteredRead = completeRead(
    contractValue, alteredBinding.value.requirementId,
    alteredBinding.value.canonicalPath, alteredBinding.newText,
    { label: 'altered-started-binding' },
  );
  assert.equal(issueRecovery({
    contract: 'MaterialEffectRecoveryObservation/v1',
    claimGeneration: 0,
    intentRef: alteredBinding.value.intentRef,
    recoveryReadExecutionKey: alteredRead.execution_key,
    recoveryReadCompletionSha256: alteredRead.completion_record_sha256,
  }), null);
});

test('fysieke workspace-identiteit laat aliases botsen en een latere write verbreekt de fence', () => {
  const targetPath = 'data/workspace/physical-alias/shared.md';
  const first = bindContract('physical-alias-first', { paths: [targetPath] });
  const firstIntentFixture = intentFixture(first.value, 'r1', targetPath);
  const issuedFirst = issueIntent(firstIntentFixture.attestation);
  assert.ok(issuedFirst);
  sequence += 1;
  claimTurnToolExecution({
    executionKey: `alias-before-intent:${sequence}`,
    turnId: first.value.executionTurnId, tool: 'schrijf_bestand',
    args: { pad: 'physical-alias/shared.md', inhoud: 'intervening alias bytes' },
    semanticBinding: semanticBinding(first.value, 'r1', 'write'),
  });
  assert.throws(
    () => appendMaterialEffectIntent(issuedFirst),
    /lost its exact pre-read head/u,
  );

  const shared = 'data/workspace/physical-alias/lost-update.md';
  const older = bindContract('physical-lost-update-older', { paths: [shared] });
  const olderIntent = appendIntent(older.value, 'r1', shared);
  const newer = bindContract('physical-lost-update-newer', { paths: [shared] });
  const newerIntent = appendIntent(newer.value, 'r1', shared);
  assert.throws(
    () => issueWriteClaim(olderIntent),
    /lost its CAS authority/u,
  );
  const rejectPhysicalPath = (physicalPath, pattern) => {
    const context = Object.freeze({});
    authorityAttestations.set(context, {
      contract: 'MaterialEffectWriteClaimAuthority/v1',
      intentRef: newerIntent.value.intentRef, generation: 0, physicalPath,
    });
    assert.throws(() => issueMaterialEffectWriteClaimCapability(context), pattern);
  };
  rejectPhysicalPath(
    path.join(path.dirname(config.workspaceDir),
      `${path.basename(config.workspaceDir)}-sibling`, 'lost-update.md'),
    /escapes the physical root/u,
  );
  rejectPhysicalPath(
    `${config.workspaceDir}-prefix/physical-alias/lost-update.md`,
    /escapes the physical root/u,
  );
  fs.symlinkSync(
    path.join(config.workspaceDir, 'distinct-missing-symlink-target'),
    path.join(config.workspaceDir, 'physical-alias-link'),
    'dir',
  );
  rejectPhysicalPath(
    path.join(config.workspaceDir, 'physical-alias-link/lost-update.md'),
    /contains a symlink/u,
  );
  const claimed = issueWriteClaim(newerIntent, {
    physicalPath: path.join(config.workspaceDir, 'physical-alias/lost-update.md'),
  });
  assert.equal(claimed.kind, 'created');
  assert.equal(materialEffectWriteFenceCurrent(
    claimed.value.claimRef, claimed.value.fenceToken,
  ), true);

  sequence += 1;
  claimTurnToolExecution({
    executionKey: `alias-after-claim:${sequence}`,
    turnId: newer.value.executionTurnId, tool: 'schrijf_bestand',
    args: { pad: 'data/workspace/physical-alias/./lost-update.md', inhoud: 'conflict' },
    semanticBinding: semanticBinding(newer.value, 'r1', 'write'),
  });
  assert.equal(materialEffectWriteFenceCurrent(
    claimed.value.claimRef, claimed.value.fenceToken,
  ), false);
  const started = db.prepare(`SELECT observed.observation_sequence AS sequence,execution.*
    FROM turn_tool_executions execution
    JOIN eva_material_execution_observations observed
      ON observed.execution_key=execution.execution_key
    WHERE execution.execution_key=?`).get(claimed.value.executionKey);
  const reconciled = reconcileFixture(
    newer.value, newerIntent, newerIntent.newText,
    { startedWrite: started },
  ).result.value;
  assert.equal(reconciled.outcome, 'uncertain_manual');
  assert.equal(reconciled.reason, 'later_conflict');
});

test('directe fysieke writes verbreken preimage-CAS en een bestaande claim blijft idempotent leesbaar', () => {
  fs.mkdirSync(path.join(config.workspaceDir, 'physical-cas'), { recursive: true });
  const beforeIntentPath = 'data/workspace/physical-cas/before-intent.md';
  const beforeIntentContract = bindContract('physical-cas-before-intent', {
    paths: [beforeIntentPath],
  });
  const beforeIntent = intentFixture(
    beforeIntentContract.value, 'r1', beforeIntentPath,
  );
  const issued = issueIntent(beforeIntent.attestation);
  assert.ok(issued);
  fs.writeFileSync(
    path.join(config.workspaceDir, 'physical-cas/before-intent.md'),
    'x'.repeat(Buffer.byteLength(beforeIntent.oldText)),
  );
  assert.throws(
    () => appendMaterialEffectIntent(issued),
    /lost its exact pre-read head/u,
  );

  const beforeClaimPath = 'data/workspace/physical-cas/before-claim.md';
  const beforeClaimContract = bindContract('physical-cas-before-claim', {
    paths: [beforeClaimPath],
  });
  const beforeClaim = appendIntent(beforeClaimContract.value, 'r1', beforeClaimPath);
  fs.writeFileSync(
    path.join(config.workspaceDir, 'physical-cas/before-claim.md'),
    'y'.repeat(Buffer.byteLength(beforeClaim.oldText)),
  );
  assert.throws(
    () => issueWriteClaim(beforeClaim),
    /lost its CAS authority/u,
  );

  const afterClaimPath = 'data/workspace/physical-cas/after-claim.md';
  const afterClaimContract = bindContract('physical-cas-after-claim', {
    paths: [afterClaimPath],
  });
  const afterClaim = appendIntent(afterClaimContract.value, 'r1', afterClaimPath);
  const created = issueWriteClaim(afterClaim);
  assert.equal(created.kind, 'created');
  const replay = issueWriteClaim(afterClaim);
  assert.equal(replay.kind, 'replay');
  assert.deepEqual(replay.value, created.value);
  assert.equal(materialEffectWriteFenceCurrent(
    created.value.claimRef, created.value.fenceToken,
  ), true);
  fs.writeFileSync(
    path.join(config.workspaceDir, 'physical-cas/after-claim.md'),
    'z'.repeat(Buffer.byteLength(afterClaim.oldText)),
  );
  assert.equal(materialEffectWriteFenceCurrent(
    created.value.claimRef, created.value.fenceToken,
  ), false);
});

test('recoverygeneratie één is duurzaam begrensd en sluit een tweede no-effect handmatig', () => {
  const { value: contractValue } = bindContract('bounded-recovery-generation');
  const target = contractValue.obligation.selectedWrites[0];
  const intent = appendIntent(
    contractValue, target.requirementId, target.canonicalPath,
  );
  const first = reconcileFixture(
    contractValue, intent, intent.oldText,
  ).result.value;
  assert.equal(first.outcome, 'no_effect');
  assert.equal(first.recoveryGeneration, 1);

  const recoveryClaim = issueWriteClaim(intent, { generation: 1 });
  assert.equal(recoveryClaim.kind, 'created');
  assert.equal(recoveryClaim.value.generation, 1);
  assert.equal(recoveryClaim.value.predecessorReconciliationRef, first.reconciliationRef);
  const recoveryStarted = db.prepare(`SELECT observed.observation_sequence AS sequence,
    execution.* FROM turn_tool_executions execution
    JOIN eva_material_execution_observations observed
      ON observed.execution_key=execution.execution_key
    WHERE execution.execution_key=?`).get(recoveryClaim.value.executionKey);
  const second = reconcileFixture(
    contractValue, intent, intent.oldText, {
      claimGeneration: 1, startedWrite: recoveryStarted,
    },
  ).result.value;
  assert.equal(second.claimGeneration, 1);
  assert.equal(second.outcome, 'uncertain_manual');
  assert.equal(second.reason, 'recovery_no_effect');
  assert.equal(second.nextAction, 'manual');
  assert.equal(materialEffectReconciliation(intent.value.intentRef).sha256, second.sha256);
});

test('expliciete observatievolgorde en claims blijven bytegelijk na VACUUM en procesrestart', () => {
  const { value: contractValue } = bindContract('vacuum-stable-observation');
  const target = contractValue.obligation.selectedWrites[0];
  const intent = appendIntent(
    contractValue, target.requirementId, target.canonicalPath,
  );
  const claim = issueWriteClaim(intent).value;
  const before = {
    intent: materialEffectIntent(intent.value.intentRef),
    claim: materialEffectWriteClaim(intent.value.intentRef, 0),
  };
  const child = runChild(`
    const {db}=await import('./src/db.js');
    await import('./src/mind/portfolio.js');
    const lifecycle=await import('./src/mind/materiallifecycle.js');
    const beforeIntent=lifecycle.materialEffectIntent(${JSON.stringify(intent.value.intentRef)});
    const beforeClaim=lifecycle.materialEffectWriteClaim(${JSON.stringify(intent.value.intentRef)},0);
    db.exec('VACUUM');
    const afterIntent=lifecycle.materialEffectIntent(${JSON.stringify(intent.value.intentRef)});
    const afterClaim=lifecycle.materialEffectWriteClaim(${JSON.stringify(intent.value.intentRef)},0);
    process.stdout.write(JSON.stringify({beforeIntent,beforeClaim,afterIntent,afterClaim,status:lifecycle.materialLifecycleStoreStatus()}));
    db.close();
  `, dataDir);
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const observed = JSON.parse(child.stdout);
  assert.deepEqual(observed.beforeIntent, before.intent);
  assert.deepEqual(observed.afterIntent, before.intent);
  assert.deepEqual(observed.beforeClaim, before.claim);
  assert.deepEqual(observed.afterClaim, before.claim);
  assert.equal(observed.afterClaim.claimRef, claim.claimRef);
  assert.deepEqual(observed.status, {
    contract: 'MaterialLifecycleStoreStatus/v1', available: true,
  });
});

test('INSERT OR REPLACE cannot bypass any lifecycle identity', () => {
  const { value: contractValue } = bindContract('replace');
  const target = contractValue.obligation.selectedWrites[0];
  const intent = appendIntent(
    contractValue, target.requirementId, target.canonicalPath,
  );
  const reconciliation = reconcileFixture(
    contractValue, intent, intent.newText,
  ).result.value;
  for (const [table, key, value, pattern] of [
    ['eva_material_execution_contracts', 'contract_ref', contractValue.contractRef,
      /material execution contract identity collision/u],
    ['eva_material_effect_intents', 'intent_ref', intent.value.intentRef,
      /material effect intent (?:identity collision|has a stale path head)/u],
    ['eva_material_effect_reconciliations', 'reconciliation_ref',
      reconciliation.reconciliationRef,
      /material effect reconciliation identity collision/u],
  ]) {
    const row = db.prepare(`SELECT * FROM ${table} WHERE ${key}=?`).get(value);
    const columns = Object.keys(row);
    assert.throws(() => db.prepare(`INSERT OR REPLACE INTO ${table}
      (${columns.join(',')}) VALUES (${columns.map((column) => `@${column}`).join(',')})`)
      .run({ ...row, created_ts: '2099-01-01T00:00:00.000Z' }), pattern);
    assert.deepEqual(db.prepare(`SELECT * FROM ${table} WHERE ${key}=?`).get(value), row);
  }
});

function runChild(source, isolated) {
  return spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: path.resolve('.'), encoding: 'utf8',
    env: { ...process.env, EVA_DATA_DIR: isolated },
  });
}

const childFixtureSource = (isolatedLabel, action) => `
  import {createHash} from 'node:crypto';
  import {canonicalJson} from './src/util.js';
  const sha=(value)=>createHash('sha256').update(value).digest('hex');
  const {db}=await import('./src/db.js');
  const {seedAuthorizedTurnUnderstanding}=await import(
    './test/turn-understanding-authority-fixture.js');
  const {completeInterpretationRetry,createInterpretationRetryHandoff}=await import('./src/mind/portfolio.js');
  const {createWorkCompletionPlanSeal}=await import('./src/mind/workcompletion.js');
  const originalText='Corrigeer materiaal ${isolatedLabel}.';
  const conversationId='web:${isolatedLabel}'; const messageId='1';
  const supersessionScope='web:${isolatedLabel}';
  const supersessionToken='token:${isolatedLabel}'; const sessionRef='turn:${isolatedLabel}';
  db.prepare('INSERT OR IGNORE INTO messages(id,ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?,?)').run(1,new Date().toISOString(),'web',conversationId,'Jaap','user',originalText);
  const handoff=createInterpretationRetryHandoff({channel:'web',conversationId,messageId,requester:'Jaap',originalText,failureClass:'interpreter_timeout',sessionRef,supersessionScope,supersessionToken});
  seedAuthorizedTurnUnderstanding({database:db,channel:'web',conversationId,messageId,
    requester:'Jaap',originalText,sessionRef,supersessionScope,supersessionToken,
    output:{durableHandoff:true,workItemId:Number(handoff.workItem.id),
      goalEpisodeId:Number(handoff.episode.id)}});
  const registry=db.prepare('SELECT * FROM eva_interpretation_retry_handoffs WHERE work_item_id=?').get(handoff.workItem.id);
  const lifecycle=await import('./src/mind/materiallifecycle.js');
  const sourceSha=sha('source:${isolatedLabel}');
  const semanticAuthoritySha256=sha('semantic:${isolatedLabel}');
  const targetPath='data/workspace/${isolatedLabel}/result.md';
  const obligationBody={contract:'MaterialCompletionObligation/v1',required:true,understandingSha256:sourceSha,requestSourceTextSha256:registry.raw_original_text_sha256,selectedRequirementIds:['r1'],deferredRequirementIds:[],discoveryRequirementIds:[],selectedWrites:[{requirementId:'r1',capabilityId:'workspace.file.write',operationId:'write',canonicalPath:targetPath}],selectedPlans:[{requirementId:'r1',capabilityId:'workspace.file.write',operationId:'write',effect:'workspace',toolName:'schrijf_bestand',args:{pad:targetPath,inhoud:'# result\\n'}}],activeConditions:[],sentinelRequired:false,sentinelTargetPaths:[],sentinelTargetSetComplete:false,sentinelTargetsMatch:false,universalScope:false,universalScopeAuthority:null,scopeUnknown:false,invalidTargetSet:false};
  const obligationAuthority={...obligationBody}; delete obligationAuthority.understandingSha256;
  const obligation={...obligationBody,sha256:sha(canonicalJson(obligationAuthority))};
  const understandingBody={contract:'TurnUnderstandingRecord/v1',sourceSha256:sourceSha,sourceKind:'model',sourceTextSha256:sha(originalText.trim()),dialogueAct:'request',goal:{statement:'Corrigeer materiaal ${isolatedLabel}.',outcome:'Het materiaal is exact gecorrigeerd.',beneficiary:'Jaap'},conditions:[],temporal:{mode:'now',notBefore:null,dueBy:null,condition:null},durability:'turn',requestedEffects:['workspace'],deniedEffects:[],actions:[{requirementId:'r1',capabilityId:'workspace.file.write',operationId:'write',polarity:'request',effect:'workspace',authorization:'allowed',sourceSpans:[{start:0,end:originalText.length}],boundaryReasons:[]}],firstDecision:{kind:'act',requirementIds:['r1'],replyDraftSha256:null},decisionProjection:{proposedKind:'act',proposedRequirementIds:['r1'],acceptedRequirementIds:['r1'],rejectedRequirementIds:[],unknownRequirementIds:[],hostAddedRequirementIds:[]},monitoring:null};
  const understanding={...understandingBody,sha256:sha(canonicalJson(understandingBody))};
      const fullPlans=obligation.selectedPlans.map((plan)=>({...plan,idempotency:'cas',proofFloor:['material_readback'],supportsDeferred:false,execution:{mode:'direct',materializable:[],target:null,proofOperations:[],recoveryOperations:[],fileArguments:[]},proofOperations:[],recoveryOperations:[],semanticBinding:{understandingSha256:sourceSha,requirementId:plan.requirementId,addressesRequirementIds:[plan.requirementId],basisReceiptIds:[],capabilityId:plan.capabilityId,operationId:plan.operationId,role:'primary',executionType:null}}));
  const planSeal=createWorkCompletionPlanSeal({decision:{kind:'act',requirementIds:['r1'],replyDraft:null},plans:fullPlans});
  let timeline=db.prepare("SELECT id FROM timeline WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?").get('turn:${isolatedLabel}');
  if(!timeline) timeline={id:Number(db.prepare('INSERT INTO timeline(ts,kind,content,meta) VALUES (?,?,?,?)').run(new Date().toISOString(),'turn_understanding','authority',JSON.stringify({turnId:'turn:${isolatedLabel}',conversationId:'web:${isolatedLabel}',originMessageId:'1',requester:'Jaap',semanticAuthoritySha256,interpretationSucceeded:true,understanding})).lastInsertRowid)};
  const retry=JSON.parse(handoff.workItem.state_json).interpretation_retry;
  const active=retry.phase==='understood'?handoff:completeInterpretationRetry(handoff.workItem,handoff.episode,'turn:${isolatedLabel}',planSeal.sha256,sourceSha,obligation.sha256,semanticAuthoritySha256);
  const attestation={contract:'MaterialExecutionContractAuthority/v1',handoffKey:registry.handoff_key,rawBodySha256:registry.raw_original_text_sha256,projection:{rule:'trimmed-redacted-user-text/v1',sha256:sha(originalText.trim())},workItemId:Number(active.workItem.id),episodeId:Number(active.episode.id),sourceTurnId:'turn:${isolatedLabel}',authorityTimelineId:Number(timeline.id),understandingSha256:sourceSha,semanticAuthoritySha256,planSeal,obligation};
  const issued=new WeakMap();
  lifecycle.registerMaterialLifecycleAuthorityVerifier((context)=>issued.get(context)||null);
  ${action}
  db.close();
`;

test('two processes racing one real handoff create one contract and exact replay', async () => {
  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-material-race-'));
  try {
    const initialize = runChild(childFixtureSource('race', `
      process.stdout.write(JSON.stringify({ready:lifecycle.materialLifecycleStoreStatus().available}));
    `), isolated);
    assert.equal(initialize.status, 0, initialize.stderr || initialize.stdout);
    assert.deepEqual(JSON.parse(initialize.stdout), { ready: true });
    const source = childFixtureSource('race', `
      const context=Object.freeze({}); issued.set(context,attestation);
      const result=lifecycle.bindMaterialExecutionContract(lifecycle.issueMaterialExecutionContractCapability(context));
      process.stdout.write(JSON.stringify({kind:result.kind,ref:result.value.contractRef,sha256:result.value.sha256}));
    `);
    const options = {
      cwd: path.resolve('.'), encoding: 'utf8',
      env: { ...process.env, EVA_DATA_DIR: isolated }, maxBuffer: 1024 * 1024,
    };
    const outcomes = await Promise.all([
      execFileAsync(process.execPath, ['--input-type=module', '-e', source], options),
      execFileAsync(process.execPath, ['--input-type=module', '-e', source], options),
    ]);
    const parsed = outcomes.map(({ stdout }) => JSON.parse(stdout));
    assert.deepEqual(parsed.map((item) => item.kind).sort(), ['created', 'replay']);
    assert.equal(new Set(parsed.map((item) => item.ref)).size, 1);
    assert.equal(new Set(parsed.map((item) => item.sha256)).size, 1);
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('restart rereads registry authority and rejects removed or tampered handoffs', () => {
  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-material-registry-tamper-'));
  try {
    const writer = runChild(childFixtureSource('restart', `
      const context=Object.freeze({}); issued.set(context,attestation);
      const result=lifecycle.bindMaterialExecutionContract(lifecycle.issueMaterialExecutionContractCapability(context));
      process.stdout.write(JSON.stringify({ref:result.value.contractRef,handoffKey:attestation.handoffKey}));
    `), isolated);
    assert.equal(writer.status, 0, writer.stderr || writer.stdout);
    const identity = JSON.parse(writer.stdout);
    const reader = runChild(`
      const {db}=await import('./src/db.js');
      await import('./src/mind/portfolio.js');
      const lifecycle=await import('./src/mind/materiallifecycle.js');
      const value=lifecycle.materialExecutionContract(${JSON.stringify(identity.ref)});
      process.stdout.write(JSON.stringify({ref:value.contractRef,status:lifecycle.materialLifecycleStoreStatus()}));
      db.close();
    `, isolated);
    assert.equal(reader.status, 0, reader.stderr || reader.stdout);
    assert.equal(JSON.parse(reader.stdout).ref, identity.ref);

    const tamper = runChild(`
      const {db}=await import('./src/db.js');
      await import('./src/mind/portfolio.js');
      const lifecycle=await import('./src/mind/materiallifecycle.js');
      const trigger=db.prepare("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_interpretation_retry_handoff_immutable_update'").get().sql;
      db.exec('DROP TRIGGER trg_interpretation_retry_handoff_immutable_update');
      db.prepare('UPDATE eva_interpretation_retry_handoffs SET raw_original_text_sha256=? WHERE handoff_key=?').run('0'.repeat(64),${JSON.stringify(identity.handoffKey)});
      db.exec(trigger);
      let error=''; try { lifecycle.materialExecutionContract(${JSON.stringify(identity.ref)}); } catch (caught) { error=String(caught.message||caught); }
      process.stdout.write(JSON.stringify({status:lifecycle.materialLifecycleStoreStatus(),error}));
      db.close();
    `, isolated);
    assert.equal(tamper.status, 0, tamper.stderr || tamper.stdout);
    const observed = JSON.parse(tamper.stdout);
    assert.equal(observed.status.available, true);
    assert.match(observed.error, /interpretation handoff failed integrity verification/u);
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('a removed immutable handoff invalidates every later contract read', () => {
  const { handoff, value } = bindContract('removed-handoff');
  const trigger = db.prepare(`SELECT sql FROM sqlite_master
    WHERE type='trigger' AND name='trg_interpretation_retry_handoff_immutable_delete'`)
    .get().sql;
  db.exec('DROP TRIGGER trg_interpretation_retry_handoff_immutable_delete');
  db.prepare('DELETE FROM eva_interpretation_retry_handoffs WHERE handoff_key=?')
    .run(handoff.registry.handoff_key);
  db.exec(trigger);
  assert.throws(
    () => materialExecutionContract(value.contractRef),
    /interpretation handoff failed integrity verification/u,
  );
  assert.equal(materialLifecycleStoreStatus().available, true);
});

test('artifact store trigger or index tampering disables lifecycle availability and heads', () => {
  for (const target of ['trigger', 'index']) {
    const isolated = fs.mkdtempSync(path.join(
      os.tmpdir(), `bottie-material-artifact-${target}-`,
    ));
    try {
      const result = runChild(`
        const {db}=await import('./src/db.js');
        await import('./src/mind/portfolio.js');
        const lifecycle=await import('./src/mind/materiallifecycle.js');
        if (${JSON.stringify(target)}==='trigger') {
          db.exec('DROP TRIGGER eva_artifact_versions_no_delete');
        } else {
          db.exec('DROP INDEX idx_artifact_versions_work_path');
        }
        const status=lifecycle.materialLifecycleStoreStatus();
        let unavailable='';
        try { lifecycle.materialEffectIntent('material-effect-intent:'+('0'.repeat(64))); }
        catch (error) { unavailable=String(error.message||error); }
        process.stdout.write(JSON.stringify({status,unavailable}));
        db.close();
      `, isolated);
      assert.equal(result.status, 0, result.stderr || result.stdout);
      const observed = JSON.parse(result.stdout);
      assert.equal(observed.status.available, false, target);
      assert.match(
        observed.status.reason,
        /^material_lifecycle_artifact_store_unavailable:/u,
      );
      assert.match(observed.unavailable, /store unavailable/u);
    } finally {
      fs.rmSync(isolated, { recursive: true, force: true });
    }
  }
});

test('mixed-case TEMP shadows en elke drift in een core dependency schakelen lifecycle fail-closed uit', () => {
  const cases = [
    ['owned-temp', `db.exec('CREATE TEMP TABLE "EvA_MaTeRiAl_EfFeCt_InTeNtS"(x)')`,
      'material_lifecycle_schema_temp_shadow'],
    ['owned-temp-before-import',
      `db.exec('CREATE TEMP TABLE "EvA_MaTeRiAl_EfFeCt_InTeNtS"(x)')`,
      'material_lifecycle_schema_temp_shadow', true],
    ['core-temp', `db.exec('CREATE TEMP TABLE "TuRn_ToOl_ExEcUtIoNs"(x)')`,
      'material_lifecycle_schema_temp_shadow'],
    ['core-temp-before-import',
      `db.exec('CREATE TEMP TABLE "TuRn_ToOl_ExEcUtIoNs"(x)')`,
      'material_lifecycle_schema_temp_shadow', true],
    ['core-schema', `db.exec('ALTER TABLE main.messages ADD COLUMN counterfeit TEXT')`,
      'material_lifecycle_reference_schema_invalid'],
    ['core-default', `db.exec(\`DROP TABLE main.turn_tool_executions;
      CREATE TABLE main.turn_tool_executions (
        execution_key TEXT PRIMARY KEY,turn_id TEXT NOT NULL,tool TEXT NOT NULL,
        args TEXT,semantic_hash TEXT,semantic_binding TEXT,intent_record_sha256 TEXT,
        status TEXT NOT NULL DEFAULT 'done',started_ts TEXT NOT NULL,finished_ts TEXT,
        output TEXT,output_sha256 TEXT,material_sha256 TEXT,
        completion_record_sha256 TEXT
      );
      CREATE INDEX idx_turn_tool_executions_turn
        ON turn_tool_executions(turn_id,tool);\`)`,
      'material_lifecycle_artifact_store_unavailable:artifact_execution_ledger_invalid:execution ledger column layout is unrecognized'],
    ['core-index', `db.exec('DROP INDEX main.idx_messages_conversation')`,
      'material_lifecycle_reference_schema_invalid'],
  ];
  for (const [label, mutation, reason, beforeImport = false] of cases) {
    const isolated = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-material-${label}-`));
    try {
      const result = runChild(`
        const {db}=await import('./src/db.js');
        await import('./src/mind/portfolio.js');
        ${beforeImport ? mutation : ''};
        const lifecycle=await import('./src/mind/materiallifecycle.js');
        ${beforeImport ? '' : mutation};
        const status=lifecycle.materialLifecycleStoreStatus();
        let error='';
        try { lifecycle.materialExecutionContract('material-contract:'+('0'.repeat(64))); }
        catch (caught) { error=String(caught.message||caught); }
        db.prepare('INSERT INTO main.timeline(ts,kind,content,meta) VALUES (?,?,?,?)')
          .run(new Date().toISOString(),'system','core survives material disable','{}');
        process.stdout.write(JSON.stringify({status,error,core:Number(db.prepare(
          "SELECT count(*) n FROM main.timeline WHERE content='core survives material disable'"
        ).get().n)}));
        db.close();
      `, isolated);
      assert.equal(result.status, 0, result.stderr || result.stdout);
      const observed = JSON.parse(result.stdout);
      assert.deepEqual(observed.status, {
        contract: 'MaterialLifecycleStoreStatus/v1', available: false, reason,
      }, label);
      assert.match(observed.error, /store unavailable/u, label);
      assert.equal(observed.core, 1, label);
    } finally {
      fs.rmSync(isolated, { recursive: true, force: true });
    }
  }
});

test('counterfeit registry or lifecycle schema disables only material lifecycle APIs', () => {
  for (const target of ['registry', 'lifecycle', 'extra-trigger', 'quoted-literal']) {
    const isolated = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-material-${target}-schema-`));
    try {
      const result = runChild(childFixtureSource(`schema-${target}`, `
        let createResult=null; let creationError=''; let preTamperCapability=null;
        if (${JSON.stringify(target)}==='extra-trigger') {
          const context=Object.freeze({}); issued.set(context,attestation);
          preTamperCapability=lifecycle.issueMaterialExecutionContractCapability(context);
        }
        if (${JSON.stringify(target)}==='registry') {
          db.exec('DROP TRIGGER trg_interpretation_retry_handoff_immutable_delete');
          db.exec("CREATE TRIGGER trg_interpretation_retry_handoff_immutable_delete BEFORE DELETE ON eva_interpretation_retry_handoffs WHEN 0 BEGIN SELECT RAISE(ABORT,'ChatInterpretationRetry/v1-registry is onveranderlijk'); END");
        } else if (${JSON.stringify(target)}==='lifecycle') {
          db.exec('DROP TRIGGER eva_material_effect_intents_no_delete');
          db.exec("CREATE TRIGGER eva_material_effect_intents_no_delete BEFORE DELETE ON eva_material_effect_intents WHEN 0 BEGIN SELECT RAISE(ABORT,'material effect intents are append-only'); END");
        } else if (${JSON.stringify(target)}==='extra-trigger') {
          db.exec("CREATE TRIGGER counterfeit_material_ignore BEFORE INSERT ON eva_material_execution_contracts BEGIN SELECT RAISE(IGNORE); END");
        } else {
          const objects=db.prepare("SELECT type,sql FROM sqlite_master WHERE tbl_name='eva_material_execution_contracts' AND sql IS NOT NULL ORDER BY CASE type WHEN 'index' THEN 1 ELSE 2 END,name").all();
          const stored=objects.find((object)=>object.type==='table').sql;
          const weakened=stored.replace("'MaterialExecutionContract/v1'","'materialexecutioncontract/v1'");
          db.exec('DROP TABLE eva_material_execution_contracts');
          db.exec([weakened,...objects.filter((object)=>object.type!=='table').map((object)=>object.sql)].join(';'));
        }
        const status=lifecycle.materialLifecycleStoreStatus();
        if (preTamperCapability) {
          try { createResult=lifecycle.bindMaterialExecutionContract(preTamperCapability); }
          catch (error) { creationError=String(error.message||error); }
        }
        let unavailable=''; try { lifecycle.materialExecutionContract('material-contract:'+('0'.repeat(64))); } catch (error) { unavailable=String(error.message||error); }
        db.prepare('INSERT INTO timeline(ts,kind,content,meta) VALUES (?,?,?,?)').run(new Date().toISOString(),'system','core remains writable','{}');
        process.stdout.write(JSON.stringify({status,unavailable,createResult,creationError,contracts:Number(db.prepare('SELECT count(*) n FROM eva_material_execution_contracts').get().n),core:Number(db.prepare("SELECT count(*) n FROM timeline WHERE content='core remains writable'").get().n)}));
      `), isolated);
      assert.equal(result.status, 0, result.stderr || result.stdout);
      const observed = JSON.parse(result.stdout);
      assert.equal(observed.status.available, false);
      assert.equal(observed.status.reason, target === 'registry'
        ? 'material_lifecycle_interpretation_registry_schema_invalid'
        : target === 'quoted-literal'
          ? 'material_lifecycle_schema_invalid:eva_material_execution_contracts'
          : 'material_lifecycle_schema_triggers_invalid');
      assert.match(observed.unavailable, /store unavailable/u);
      assert.equal(observed.createResult, null);
      assert.equal(observed.contracts, 0);
      if (target === 'extra-trigger') {
        assert.match(observed.creationError, /store unavailable/u);
      }
      assert.equal(observed.core, 1);
    } finally {
      fs.rmSync(isolated, { recursive: true, force: true });
    }
  }
});
