import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-artifact-versions-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  claimTurnToolExecution, completeTurnToolExecution, db,
} = await import('../src/db.js');
const {
  createDurableWork,
  recentEpisodeArtifactReceipts,
  recordEpisodeDelivery,
  transitionGoalEpisode,
} = await import('../src/mind/portfolio.js');
const {
  artifactBytes,
  artifactCollectionStoreAuthority,
  artifactVersion,
  artifactVersionContracts,
  artifactVersionReceipt,
  artifactVersionsForWork,
  artifactVersionStoreStatus,
  consumeArtifactCollectionStoreAuthority,
  artifactSealAuthority,
  registerArtifactSealAuthorityVerifier,
  sealVerifiedWorkArtifacts,
  storeWorkspaceArtifactObject,
} = await import('../src/mind/artifacts.js');
const { canonicalJson } = await import('../src/util.js');

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function withoutExecutionTrigger(name, operation) {
  const trigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name);
  assert.ok(trigger?.sql, `missing execution trigger ${name}`);
  db.exec(`DROP TRIGGER main.${name}`);
  try { return operation(); } finally { db.exec(trigger.sql); }
}

let sequence = 0;
const issuedAuthorities = new WeakMap();
registerArtifactSealAuthorityVerifier((context) => issuedAuthorities.get(context) || null);

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function workFixture(label) {
  sequence += 1;
  return createDurableWork({
    source: 'test', owner: 'Jaap', title: `Artifact ${label} ${sequence}`,
    goal: `Bewaar artifact ${label} reproduceerbaar.`,
    nextStep: 'Verzegel de bewezen bytes.',
    definitionOfDone: 'De exacte bytes en provenance zijn duurzaam verzegeld.',
  }, {
    originKind: 'user', originChannel: 'test',
    originConversationId: `test:artifact:${sequence}`,
    originMessageId: String(sequence), requester: 'Jaap', deliveryPolicy: 'none',
  });
}

function actionResult(tool, data, artifactReceipt = null) {
  // Productie bewaart ActionResult in de vaste presentatiesleutelvolgorde van
  // JSON.stringify; dat is bewust niet dezelfde volgorde als canonicalJson.
  return JSON.stringify({
    contract: 'ActionResult/v1', tool, ok: true, changed: tool === 'schrijf_bestand',
    verified: true, evidence: { source: tool, summary: `${tool} verified` },
    needs_followup: false, partial: false, risk: 'normal', human_message: '',
    ...(artifactReceipt ? { artifact_receipt: artifactReceipt } : {}), data,
  });
}

function turnUnderstandingRecord(sourceSha256) {
  const body = {
    contract: 'TurnUnderstandingRecord/v1', sourceSha256,
    sourceKind: 'test', sourceTextSha256: sha256('artifact test source'),
    dialogueAct: 'command', goal: {
      statement: 'Maak een artifact.', outcome: 'Bewaar exacte bytes.',
      beneficiary: 'requester',
    }, conditions: [], temporal: { mode: 'none', notBefore: null, dueBy: null, condition: null },
    durability: 'episode', requestedEffects: ['workspace'], deniedEffects: [],
    actions: [], firstDecision: {
      kind: 'execute', requirementIds: ['r1'], replyDraftSha256: null,
    }, decisionProjection: {
      proposedKind: 'execute', proposedRequirementIds: ['r1'],
      acceptedRequirementIds: ['r1'], rejectedRequirementIds: [],
      unknownRequirementIds: [], hostAddedRequirementIds: [],
    }, monitoring: null,
  };
  return { ...body, sha256: sha256(canonicalJson(body)) };
}

function authorityFor(work, turnId, understandingSha256) {
  const record = turnUnderstandingRecord(understandingSha256);
  assert.notEqual(record.sha256, understandingSha256);
  db.prepare(`INSERT INTO timeline(ts,kind,content,meta) VALUES (?,?,?,?)`).run(
    new Date().toISOString(), 'turn_understanding', 'artifact test authority',
    JSON.stringify({
      internalOnly: true, turnId, conversationId: `test:${turnId}`,
      understanding: record, interpretationSucceeded: true,
    }),
  );
  const context = Object.freeze({});
  const proofExecutionKeys = db.prepare(`SELECT execution_key FROM turn_tool_executions
    WHERE turn_id=? AND status='done' AND json_valid(semantic_binding)
      AND json_extract(semantic_binding,'$.understandingSha256')=?
    ORDER BY execution_key`).all(turnId, understandingSha256)
    .map((row) => row.execution_key);
  issuedAuthorities.set(context, Object.freeze({
    workItemId: Number(work.workItem.id), episodeId: Number(work.episode.id),
    expectedWorkRevision: Number(work.workItem.revision), turnId, understandingSha256,
    proofExecutionKeys,
  }));
  const authority = artifactSealAuthority(context);
  assert.ok(authority);
  return authority;
}

function completeExecution({ key, turnId, tool, args, binding, output }) {
  const claimed = claimTurnToolExecution({
    executionKey: key, turnId, tool, args, semanticBinding: binding,
  });
  assert.equal(claimed.claimed, true);
  return completeTurnToolExecution(key, output).row;
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
  return db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(key);
}

function provenArtifact({
  filePath, content, role = 'result', requirementId = 'r1', executable = false,
  bindingOverride = null, turnIdOverride = null, understandingSha256Override = null,
  executionContract = 'ExecutionRecord/v2',
}) {
  sequence += 1;
  const bytes = Buffer.from(content, 'utf8');
  const digest = sha256(bytes);
  const understandingSha256 = understandingSha256Override || sha256(`understanding:${sequence}`);
  const bindingFor = (capabilityId, operationId) => ({
    understandingSha256, requirementId,
    addressesRequirementIds: [requirementId], basisReceiptIds: [],
    capabilityId, operationId,
    role: 'proof', executionType: 'direct',
  });
  storeWorkspaceArtifactObject(bytes, { expectedSha256: digest });
  const turnId = turnIdOverride || `artifact-turn-${sequence}`;
  const prefix = `artifact-proof-${sequence}`;
  const execute = executionContract === 'ExecutionRecord/v1'
    ? completeLegacyV1Execution : completeExecution;
  const write = execute({
    key: `${prefix}:write`, turnId, tool: 'schrijf_bestand',
    args: { pad: filePath, inhoud: content },
    binding: bindingOverride || bindingFor('workspace.file.write', 'write'),
    output: actionResult('schrijf_bestand', {
      path: filePath, bytes: bytes.length, material_sha256: digest,
    }),
  });
  const runs = [];
  if (executable) {
    const run = execute({
      key: `${prefix}:run`, turnId, tool: 'voer_uit',
      args: {
        programma: 'node', argumenten: [filePath], werkmap: 'data/workspace',
        verwachte_exitcodes: [0],
      }, binding: bindingOverride || bindingFor('workspace.program.execute', 'execute'),
      output: actionResult('voer_uit', {
        exit_code: 0, expected_exit_codes: [0],
        artifact_receipts: {
          before: [{ path: filePath, bytes: bytes.length, sha256: digest }],
          after: [{ path: filePath, bytes: bytes.length, sha256: digest }],
        },
      }),
    });
    runs.push({
      executionKey: run.execution_key,
      completionSha256: run.completion_record_sha256,
    });
  }
  const read = execute({
    key: `${prefix}:read`, turnId, tool: 'lees_bestand', args: { pad: filePath },
    binding: bindingOverride || bindingFor('workspace.file.read', 'read'),
    output: actionResult('lees_bestand', content, {
      path: filePath, bytes: bytes.length, source_bytes: bytes.length,
      sha256: digest, complete_read: true,
    }),
  });
  return {
    role, path: filePath, sha256: digest, understandingSha256, requirementId,
    writeExecutionKey: write.execution_key,
    writeCompletionSha256: write.completion_record_sha256,
    runExecutions: runs,
    readExecutionKey: read.execution_key,
    readCompletionSha256: read.completion_record_sha256,
  };
}

function seal(work, artifacts, operationKey, authorityWork = work) {
  const first = artifacts[0];
  const write = db.prepare('SELECT turn_id FROM turn_tool_executions WHERE execution_key=?')
    .get(first.writeExecutionKey);
  const authority = authorityFor(
    authorityWork, write.turn_id, first.understandingSha256,
  );
  return sealVerifiedWorkArtifacts(authority, { artifacts, operationKey });
}

function issuedAuthority(work, artifact) {
  const turnId = db.prepare(
    'SELECT turn_id FROM turn_tool_executions WHERE execution_key=?',
  ).get(artifact.writeExecutionKey).turn_id;
  return authorityFor(work, turnId, artifact.understandingSha256);
}

function replaceArtifactRow(table, row, overrides = {}) {
  assert.ok([
    'eva_artifact_objects', 'eva_artifact_seals', 'eva_artifact_versions',
  ].includes(table));
  const candidate = { ...row, ...overrides };
  const columns = Object.keys(candidate);
  return db.prepare(`INSERT OR REPLACE INTO ${table}
    (${columns.join(',')}) VALUES (${columns.map((column) => `@${column}`).join(',')})`)
    .run(candidate);
}

test('artifact module imports safely before work and portfolio reference tables', () => {
  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-artifact-import-'));
  const source = `
    process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
    const artifacts=await import('./src/mind/artifacts.js');
    const {db}=await import('./src/db.js');
    const status=artifacts.artifactVersionStoreStatus();
    let sealed=false;
    try { artifacts.sealVerifiedWorkArtifacts({}, {}); sealed=true; } catch { sealed=false; }
    process.stdout.write(JSON.stringify({imported:true,status,sealed}));
    db.close();
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
  });
  try {
    assert.equal(result.status, 0, result.stderr || result.stdout);
    const observed = JSON.parse(result.stdout);
    assert.equal(observed.imported, true);
    assert.equal(typeof observed.status.available, 'boolean');
    assert.equal(observed.sealed, false);
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('artifact store starts available and rejects bytes that do not match their hash', () => {
  assert.deepEqual(artifactVersionStoreStatus(), {
    contract: 'ArtifactVersionStoreStatus/v1', available: true,
  });
  assert.throws(() => storeWorkspaceArtifactObject('not these bytes', {
    expectedSha256: sha256('different bytes'),
  }), /do not match/u);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_artifact_objects').get().n, 0);
});

test('an overwritten workspace path keeps every sealed byte version and exact lineage', () => {
  const filePath = 'data/workspace/reports/result.txt';
  const firstWork = workFixture('lineage-a');
  const firstArtifact = provenArtifact({
    work: firstWork, filePath, content: 'eerste, onveranderlijke versie\n',
  });
  const first = seal(firstWork, [firstArtifact], 'artifact-seal:lineage-a');
  const firstReceipt = first.versions[0];
  assert.equal(firstReceipt.previousVersionRef, null);
  assert.equal(artifactBytes(firstReceipt.versionRef, {
    expectedSha256: firstArtifact.sha256,
  }).toString('utf8'), 'eerste, onveranderlijke versie\n');

  const secondWork = workFixture('lineage-b');
  const secondArtifact = provenArtifact({
    work: secondWork, filePath, content: 'tweede versie op hetzelfde levende pad\n',
  });
  const second = seal(secondWork, [secondArtifact], 'artifact-seal:lineage-b');
  assert.equal(second.versions[0].previousVersionRef, firstReceipt.versionRef);

  const thirdWork = workFixture('lineage-c');
  const thirdArtifact = provenArtifact({
    work: thirdWork, filePath, content: 'derde versie, zonder verlies van A of B\n',
  });
  const third = seal(thirdWork, [thirdArtifact], 'artifact-seal:lineage-c');
  assert.equal(third.versions[0].previousVersionRef, second.versions[0].versionRef);
  assert.equal(artifactBytes(firstReceipt.versionRef, {
    expectedSha256: firstArtifact.sha256,
  }).toString('utf8'), 'eerste, onveranderlijke versie\n');
  assert.equal(artifactBytes(second.versions[0].versionRef, {
    expectedSha256: secondArtifact.sha256,
  }).toString('utf8'), 'tweede versie op hetzelfde levende pad\n');
});

test('objects deduplicate while versions remain separately bound to work', () => {
  const content = 'dezelfde bytes, twee bewezen werkresultaten\n';
  const digest = sha256(content);
  const beforeObjects = db.prepare('SELECT count(*) n FROM eva_artifact_objects').get().n;
  const leftWork = workFixture('dedupe-left');
  const left = provenArtifact({
    work: leftWork, filePath: 'data/workspace/dedupe/left.txt', content,
  });
  seal(leftWork, [left], 'artifact-seal:dedupe-left');
  const rightWork = workFixture('dedupe-right');
  const right = provenArtifact({
    work: rightWork, filePath: 'data/workspace/dedupe/right.txt', content,
  });
  seal(rightWork, [right], 'artifact-seal:dedupe-right');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_artifact_objects').get().n,
    beforeObjects + 1);
  assert.equal(db.prepare(
    'SELECT count(*) n FROM eva_artifact_versions WHERE artifact_sha256=?',
  ).get(digest).n, 2);
});

test('conversation continuation projects only store-attested exact episode versions', () => {
  const conversation = `web:artifact-continuation-${sequence}`;
  const work = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Artifact continuation',
    goal: 'Bewaar en bezorg één exact resultaat.', nextStep: 'Verzegel het resultaat.',
    definitionOfDone: 'Het verzegelde resultaat is exact bezorgd.',
  }, {
    originKind: 'user', originChannel: 'web',
    originConversationId: conversation, originMessageId: `origin-${sequence}`,
    requester: 'Jaap', deliveryPolicy: 'required', deliveryTarget: 'Jaap',
  });
  const artifact = provenArtifact({
    work,
    filePath: 'data/workspace/continuation/result.txt',
    content: 'exact vervolgresultaat\n',
  });
  const sealed = seal(work, [artifact], `artifact-seal:continuation-${sequence}`);
  transitionGoalEpisode(work.episode.id, {
    state: 'delivery_pending', proofSummary: `Artifactseal ${sealed.sealRef}.`,
    work: { evidence: `Artifactseal ${sealed.sealRef}.`, lastAction: 'artifact_verzegeld' },
  });
  recordEpisodeDelivery(work.episode.id, {
    deliveredChannel: 'web', originConversationId: conversation,
    deliveredConversationId: conversation, resultText: 'Het resultaat is verzegeld.',
    idempotencyKey: `artifact-continuation-${sequence}`,
  });

  assert.deepEqual(recentEpisodeArtifactReceipts(conversation), sealed.versions);
  db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?').run(
    JSON.stringify({ artifact_receipts: [{
      ...sealed.versions[0], sha256: 'f'.repeat(64),
    }] }), work.workItem.id,
  );
  assert.deepEqual(recentEpisodeArtifactReceipts(conversation), sealed.versions,
    'mutable state_json kan immutable artifactauthority niet wijzigen');
});

test('collection authority komt alleen uit de actuele artifactstore en is one-shot CAS', () => {
  const work = workFixture('collection-authority');
  const filePath = `data/workspace/collection-${sequence}.txt`;
  const artifact = provenArtifact({ work, filePath, content: 'collection bytes\n' });
  const sealed = seal(work, [artifact], `artifact-seal:collection-${sequence}`);
  const authority = artifactCollectionStoreAuthority({
    workItemId: work.workItem.id, episodeId: work.episode.id, paths: [filePath],
  });
  assert.ok(authority);
  const consumed = consumeArtifactCollectionStoreAuthority(authority);
  assert.equal(consumed.contract, 'ArtifactCollectionStoreState/v1');
  assert.equal(consumed.heads[0].versionRef, sealed.versions[0].versionRef);
  assert.equal(consumed.heads[0].workItemId, work.workItem.id);
  assert.equal(consumed.heads[0].episodeId, work.episode.id);
  assert.equal(consumeArtifactCollectionStoreAuthority(authority), null);

  const stale = artifactCollectionStoreAuthority({
    workItemId: work.workItem.id, episodeId: work.episode.id, paths: [filePath],
  });
  const replacement = provenArtifact({
    work, filePath, content: 'new collection bytes\n',
  });
  seal(work, [replacement], `artifact-seal:collection-replace-${sequence}`);
  assert.equal(consumeArtifactCollectionStoreAuthority(stale), null,
    'een nieuwe artifacthead vervalt eerder uitgegeven collectieauthority');
});

test('collection authority inventariseert alle actuele target-heads naast requested paths', () => {
  const work = workFixture('collection-complete-inventory');
  const requestedPath = `data/workspace/complete-inventory/requested-${sequence}.txt`;
  const outsidePath = `data/workspace/complete-inventory/outside-${sequence}.txt`;
  const requested = provenArtifact({ work, filePath: requestedPath, content: 'requested\n' });
  seal(work, [requested], `artifact-seal:complete-inventory-requested-${sequence}`);
  const outside = provenArtifact({ work, filePath: outsidePath, content: 'outside\n' });
  seal(work, [outside], `artifact-seal:complete-inventory-outside-${sequence}`);

  const authority = artifactCollectionStoreAuthority({
    workItemId: work.workItem.id, episodeId: work.episode.id, paths: [requestedPath],
  });
  const state = consumeArtifactCollectionStoreAuthority(authority);
  assert.deepEqual(state.heads.map((head) => head.path), [requestedPath]);
  assert.deepEqual(state.inventory.map((head) => head.path), [outsidePath, requestedPath].sort());
  assert.ok(state.inventory.every((head) => (
    head.workItemId === work.workItem.id && head.episodeId === work.episode.id
      && /^artifact-version:[a-f0-9]{64}$/u.test(head.versionRef)
      && /^[a-f0-9]{64}$/u.test(head.receiptSha256)
  )));

  const stale = artifactCollectionStoreAuthority({
    workItemId: work.workItem.id, episodeId: work.episode.id, paths: [requestedPath],
  });
  const appearedPath = `data/workspace/complete-inventory/appeared-${sequence}.txt`;
  const appeared = provenArtifact({ work, filePath: appearedPath, content: 'appeared\n' });
  seal(work, [appeared], `artifact-seal:complete-inventory-appeared-${sequence}`);
  assert.equal(consumeArtifactCollectionStoreAuthority(stale), null,
    'een nieuwe off-request target-head vervalt de volledige inventoryfence');
});

test('collection head lookup valideert de actuele head zonder oude historie als liveness-slot', () => {
  const work = workFixture('collection-current-head-only');
  const filePath = `data/workspace/current-head-only/result-${sequence}.txt`;
  const oldArtifact = provenArtifact({ work, filePath, content: 'old bytes\n' });
  const oldReceipt = seal(work, [oldArtifact], `artifact-seal:current-head-old-${sequence}`)
    .versions[0];
  const latestArtifact = provenArtifact({ work, filePath, content: 'latest bytes\n' });
  const latestReceipt = seal(work, [latestArtifact],
    `artifact-seal:current-head-latest-${sequence}`).versions[0];
  const trigger = db.prepare(`SELECT sql FROM sqlite_master
    WHERE type='trigger' AND name='eva_artifact_versions_no_update'`).get().sql;
  const oldRow = db.prepare('SELECT proof_json FROM eva_artifact_versions WHERE version_ref=?')
    .get(oldReceipt.versionRef);
  db.exec('DROP TRIGGER eva_artifact_versions_no_update');
  db.prepare('UPDATE eva_artifact_versions SET proof_json=? WHERE version_ref=?')
    .run('{}', oldReceipt.versionRef);
  db.exec(trigger);
  try {
    const authority = artifactCollectionStoreAuthority({
      workItemId: work.workItem.id, episodeId: work.episode.id, paths: [filePath],
    });
    assert.equal(consumeArtifactCollectionStoreAuthority(authority).heads[0].versionRef,
      latestReceipt.versionRef);
    assert.throws(() => artifactVersionReceipt(oldReceipt.versionRef),
      /artifact version proof failed integrity verification/u,
      'de corrupte historische versie blijft bij directe lezing fail-closed');
    assert.equal(artifactVersionReceipt(latestReceipt.versionRef).versionRef,
      latestReceipt.versionRef);
  } finally {
    db.exec('DROP TRIGGER eva_artifact_versions_no_update');
    db.prepare('UPDATE eva_artifact_versions SET proof_json=? WHERE version_ref=?')
      .run(oldRow.proof_json, oldReceipt.versionRef);
    db.exec(trigger);
  }
});

test('collection head/inventory heeft geen permanente history-cap en begrenst actuele output', () => {
  assert.equal(artifactVersionContracts.maxCollectionPaths, 128);
  assert.equal(Object.hasOwn(artifactVersionContracts, 'maxCollectionHistoryRows'), false);
  const plan = db.prepare(`EXPLAIN QUERY PLAN SELECT version.id
    FROM main.eva_artifact_versions version
    JOIN main.eva_artifact_seals seal ON seal.id=version.seal_id
    WHERE seal.work_item_id=? AND seal.episode_id=?
      AND NOT EXISTS (SELECT 1 FROM main.eva_artifact_versions later
        WHERE later.canonical_path=version.canonical_path AND later.id>version.id)
    ORDER BY version.canonical_path LIMIT ?`).all(1, 1, 129);
  const detail = plan.map((row) => row.detail).join('\n');
  assert.match(detail, /idx_artifact_seals_work_episode/u);
  assert.match(detail, /idx_artifact_versions_work_path/u);
  const work = workFixture('collection-inventory-bound');
  const paths = [];
  for (let batch = 0; batch < 11; batch += 1) {
    const turnId = `artifact-inventory-bound-turn-${sequence}-${batch}`;
    const understandingSha256 = sha256(`artifact-inventory-bound-understanding-${sequence}-${batch}`);
    const count = batch === 10 ? 9 : 12;
    const artifacts = Array.from({ length: count }, (_, index) => {
      const itemPath = `data/workspace/inventory-bound/${batch}-${index}.txt`;
      paths.push(itemPath);
      return provenArtifact({
        work, filePath: itemPath, content: `${batch}:${index}\n`,
        turnIdOverride: turnId, understandingSha256Override: understandingSha256,
      });
    });
    seal(work, artifacts, `artifact-seal:inventory-bound-${sequence}-${batch}`);
  }
  assert.equal(paths.length, 129);
  assert.throws(() => artifactCollectionStoreAuthority({
    workItemId: work.workItem.id, episodeId: work.episode.id, paths: [paths[0]],
  }), /target inventory exceeds 128 heads/u);
});

test('artifact- en collectionpaden delen hetzelfde control/NFC-domein en behouden gewone spaties', () => {
  const work = workFixture('collection-path-domain');
  for (const invalidPath of [
    'data/workspace/control/line\nbreak.txt',
    'data/workspace/control/tab\tname.txt',
    'data/workspace/control/e\u0301.txt',
    'data/workspace/control/trailing-space.txt ',
    'data/workspace/control/nonbreaking\u00A0space.txt',
  ]) {
    assert.throws(() => artifactCollectionStoreAuthority({
      workItemId: work.workItem.id, episodeId: work.episode.id, paths: [invalidPath],
    }), /canonical data\/workspace path|workspace paths/u);
    const artifact = provenArtifact({ work, filePath: invalidPath, content: 'invalid path\n' });
    assert.throws(() => seal(work, [artifact], `artifact-seal:path-domain-${sequence}`),
      /canonical data\/workspace path/u);
  }
  const spacedPath = `data/workspace/control/ordinary space ${sequence}.txt`;
  const spaced = provenArtifact({ work, filePath: spacedPath, content: 'ordinary space\n' });
  const receipt = seal(work, [spaced], `artifact-seal:path-domain-space-${sequence}`);
  const authority = artifactCollectionStoreAuthority({
    workItemId: work.workItem.id, episodeId: work.episode.id, paths: [spacedPath],
  });
  assert.equal(consumeArtifactCollectionStoreAuthority(authority).heads[0].versionRef,
    receipt.versions[0].versionRef);

  const utf8OrderedPaths = [
    `data/workspace/control/\uE000-${sequence}.txt`,
    `data/workspace/control/\u{10000}-${sequence}.txt`,
  ];
  for (const [index, itemPath] of utf8OrderedPaths.entries()) {
    const item = provenArtifact({ work, filePath: itemPath, content: `utf8 ${index}\n` });
    seal(work, [item], `artifact-seal:path-domain-utf8-${sequence}`);
  }
  const utf8Authority = artifactCollectionStoreAuthority({
    workItemId: work.workItem.id, episodeId: work.episode.id, paths: utf8OrderedPaths,
  });
  assert.deepEqual(consumeArtifactCollectionStoreAuthority(utf8Authority)
    .heads.map((head) => head.path), utf8OrderedPaths);
});

test('collection authority faalt vóór reads op TEMP shadows van main dependencies', () => {
  const work = workFixture('collection-temp-main');
  const filePath = `data/workspace/collection-temp-main/result-${sequence}.txt`;
  const artifact = provenArtifact({ work, filePath, content: 'temp shadow proof\n' });
  seal(work, [artifact], `artifact-seal:collection-temp-main-${sequence}`);
  db.exec('CREATE TEMP VIEW eva_artifact_versions AS SELECT * FROM main.eva_artifact_versions');
  try {
    assert.throws(() => artifactCollectionStoreAuthority({
      workItemId: work.workItem.id, episodeId: work.episode.id, paths: [filePath],
    }), /artifact version store unavailable: artifact_schema_temp_shadow/u);
  } finally {
    db.exec('DROP VIEW temp.eva_artifact_versions');
  }
});

test('continuation behoudt de nieuwste versie van een oud pad binnen de twaalfgrens', () => {
  const conversation = `web:artifact-newest-per-path-${sequence}`;
  const work = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Artifact newest per path',
    goal: 'Bewaar de nieuwste versie per pad.', nextStep: 'Verzegel de reeks.',
    definitionOfDone: 'De nieuwste twaalf paden blijven vervolg-authority.',
  }, {
    originKind: 'user', originChannel: 'web',
    originConversationId: conversation, originMessageId: `origin-newest-${sequence}`,
    requester: 'Jaap', deliveryPolicy: 'required', deliveryTarget: 'Jaap',
  });
  const oldA = provenArtifact({
    work, filePath: 'data/workspace/newest/a.txt', content: 'oude A\n',
  });
  seal(work, [oldA], `artifact-seal:newest-old-a-${sequence}`);
  for (let index = 0; index < 12; index += 1) {
    const item = provenArtifact({
      work,
      filePath: `data/workspace/newest/path-${String(index + 1).padStart(2, '0')}.txt`,
      content: `pad ${index + 1}\n`,
    });
    seal(work, [item], `artifact-seal:newest-path-${index}-${sequence}`);
  }
  const newA = provenArtifact({
    work, filePath: 'data/workspace/newest/a.txt', content: 'nieuwe A\n',
  });
  const latest = seal(work, [newA], `artifact-seal:newest-new-a-${sequence}`);
  transitionGoalEpisode(work.episode.id, {
    state: 'delivery_pending', proofSummary: `Artifactseal ${latest.sealRef}.`,
    work: { evidence: `Artifactseal ${latest.sealRef}.`, lastAction: 'artifact_verzegeld' },
  });
  recordEpisodeDelivery(work.episode.id, {
    deliveredChannel: 'web', originConversationId: conversation,
    deliveredConversationId: conversation, resultText: 'De nieuwste reeks is verzegeld.',
    idempotencyKey: `artifact-newest-per-path-${sequence}`,
  });
  const receipts = recentEpisodeArtifactReceipts(conversation);
  assert.equal(receipts.length, 12);
  const projectedA = receipts.find((receipt) => receipt.path === 'data/workspace/newest/a.txt');
  assert.equal(projectedA?.sha256, sha256('nieuwe A\n'));
  assert.notEqual(projectedA?.sha256, sha256('oude A\n'));
});

test('one seal atomically supports all twelve typed requirement artifacts', () => {
  assert.equal(artifactVersionContracts.maxArtifactsPerSeal, 12);
  const work = workFixture('twelve-requirements');
  const turnId = `artifact-twelve-turn-${sequence}`;
  const understandingSha256 = sha256(`artifact-twelve-understanding-${sequence}`);
  const artifacts = Array.from({ length: 12 }, (_, index) => provenArtifact({
    work,
    filePath: `data/workspace/twelve/result-${index + 1}.txt`,
    content: `duurzaam resultaat ${index + 1}\n`,
    requirementId: `r${index + 1}`,
    turnIdOverride: turnId,
    understandingSha256Override: understandingSha256,
  }));
  const authority = issuedAuthority(work, artifacts[0]);
  assert.throws(() => sealVerifiedWorkArtifacts(authority, {
    artifacts: [...artifacts, artifacts[0]],
    operationKey: 'artifact-seal:twelve-requirements-overflow',
  }), /between one and 12 entries/u);
  const receipt = sealVerifiedWorkArtifacts(authority, {
    artifacts,
    operationKey: 'artifact-seal:twelve-requirements',
  });
  assert.equal(receipt.versions.length, 12);
  assert.deepEqual(receipt.versions.map((version) => version.requirementId),
    Array.from({ length: 12 }, (_, index) => `r${index + 1}`));
  assert.deepEqual(db.prepare(`SELECT ordinal FROM eva_artifact_versions
    WHERE seal_id=(SELECT id FROM eva_artifact_seals WHERE operation_key=?)
    ORDER BY ordinal`).all('artifact-seal:twelve-requirements').map((row) => row.ordinal),
  Array.from({ length: 12 }, (_, index) => index));
});

test('batch seal is exactly idempotent and operation/request conflicts fail closed', () => {
  const work = workFixture('idempotency');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/idempotency/result.txt', content: 'exact replay\n',
  });
  const authority = issuedAuthority(work, artifact);
  const first = sealVerifiedWorkArtifacts(authority, {
    artifacts: [artifact], operationKey: 'artifact-seal:idempotency',
  });
  const replay = sealVerifiedWorkArtifacts(authority, {
    artifacts: [artifact], operationKey: 'artifact-seal:idempotency',
  });
  assert.deepEqual(replay, first);
  db.prepare('UPDATE eva_work_items SET revision=revision+1 WHERE id=?')
    .run(work.workItem.id);
  assert.deepEqual(sealVerifiedWorkArtifacts(authority, {
    artifacts: [artifact], operationKey: 'artifact-seal:idempotency',
  }), first, 'een bestaand exact sealreceipt blijft replaybaar na latere werkvoortgang');
  assert.equal(db.prepare('SELECT count(*) n FROM eva_artifact_seals WHERE operation_key=?')
    .get('artifact-seal:idempotency').n, 1);
  assert.throws(() => sealVerifiedWorkArtifacts(authority, {
    artifacts: [{ ...artifact, role: 'fixture' }],
    operationKey: 'artifact-seal:idempotency',
  }), /already consumed|another request/u);
  assert.throws(() => sealVerifiedWorkArtifacts(authority, {
    artifacts: [artifact], operationKey: 'artifact-seal:idempotency-alias',
  }), /already consumed|another operationKey/u);
});

test('one host authority cannot move receipts across work or authorize a second batch', () => {
  const sourceWork = workFixture('causal-source');
  const artifact = provenArtifact({
    work: sourceWork, filePath: 'data/workspace/causal/source.txt',
    content: 'causally bound bytes\n',
  });
  const foreignWork = workFixture('causal-foreign');
  const foreignAuthority = authorityFor(
    foreignWork, `artifact-foreign-turn-${sequence}`, artifact.understandingSha256,
  );
  assert.throws(() => sealVerifiedWorkArtifacts(foreignAuthority, {
    artifacts: [artifact], operationKey: 'artifact-seal:causal-foreign',
  }), /causally bound/u);
  const authority = issuedAuthority(sourceWork, artifact);
  sealVerifiedWorkArtifacts(authority, {
    artifacts: [artifact], operationKey: 'artifact-seal:causal-source',
  });
  assert.throws(() => sealVerifiedWorkArtifacts(authority, {
    artifacts: [{ ...artifact, role: 'fixture' }],
    operationKey: 'artifact-seal:causal-second',
  }), /already consumed/u);
});

test('executable artifacts require a stable before/after run receipt', () => {
  const work = workFixture('executable');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/program/main.js',
    content: 'process.stdout.write("ok\\n");\n', executable: true, role: 'source',
  });
  const receipt = seal(work, [artifact], 'artifact-seal:executable');
  assert.equal(receipt.versions[0].role, 'source');
  const forgedWork = workFixture('executable-forged');
  const forged = provenArtifact({
    work: forgedWork, filePath: 'data/workspace/program/forged.js',
    content: 'process.stdout.write("forged\\n");\n', executable: true, role: 'source',
  });
  const bad = {
    ...forged,
    runExecutions: [{
      ...forged.runExecutions[0], completionSha256: '0'.repeat(64),
    }],
  };
  assert.throws(() => seal(forgedWork, [bad], 'artifact-seal:executable-forged'),
    /conflicts with the durable execution receipt/u);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_artifact_seals WHERE operation_key=?')
    .get('artifact-seal:executable-forged').n, 0);
});

test('one invalid member leaves an artifact seal batch completely absent', () => {
  const work = workFixture('atomic-batch');
  const turnId = `artifact-batch-turn-${sequence}`;
  const understandingSha256 = sha256(`artifact-batch-understanding-${sequence}`);
  const first = provenArtifact({
    work, filePath: 'data/workspace/batch/a.txt', content: 'batch a\n', role: 'source',
    turnIdOverride: turnId, understandingSha256Override: understandingSha256,
  });
  const second = provenArtifact({
    work, filePath: 'data/workspace/batch/b.txt', content: 'batch b\n', role: 'test',
    turnIdOverride: turnId, understandingSha256Override: understandingSha256,
  });
  const invalidSecond = { ...second, readCompletionSha256: 'f'.repeat(64) };
  const versionsBefore = db.prepare('SELECT count(*) n FROM eva_artifact_versions').get().n;
  assert.throws(() => seal(work, [first, invalidSecond], 'artifact-seal:atomic-batch'),
    /conflicts with the durable execution receipt/u);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_artifact_versions').get().n,
    versionsBefore);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_artifact_seals WHERE operation_key=?')
    .get('artifact-seal:atomic-batch').n, 0);
});

test('execution proofs are read again after authority issuance and before sealing', () => {
  const work = workFixture('proof-reread');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/proof-reread/result.txt', content: 'proof before race\n',
  });
  const authority = issuedAuthority(work, artifact);
  withoutExecutionTrigger('turn_tool_executions_terminal_immutable', () => {
    db.prepare('UPDATE turn_tool_executions SET output=? WHERE execution_key=?')
      .run('{}', artifact.writeExecutionKey);
  });
  assert.throws(() => sealVerifiedWorkArtifacts(authority, {
    artifacts: [artifact], operationKey: 'artifact-seal:proof-reread',
  }), /artifact execution receipt is invalid/u);
  assert.equal(db.prepare('SELECT count(*) n FROM eva_artifact_seals WHERE operation_key=?')
    .get('artifact-seal:proof-reread').n, 0);
});

test('stale work revision and mismatched episode reference cannot be sealed', () => {
  const work = workFixture('references');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/references/result.txt', content: 'reference proof\n',
  });
  const staleAuthority = issuedAuthority(work, artifact);
  db.prepare('UPDATE eva_work_items SET revision=revision+1 WHERE id=?')
    .run(work.workItem.id);
  assert.throws(() => sealVerifiedWorkArtifacts(staleAuthority, {
    artifacts: [artifact], operationKey: 'artifact-seal:stale-work',
  }), /current host authority|revision is stale/u);
  const other = workFixture('other-episode');
  const wrongAuthority = authorityFor(
    other, `artifact-other-turn-${sequence}`, artifact.understandingSha256,
  );
  assert.throws(() => sealVerifiedWorkArtifacts(wrongAuthority, {
    artifacts: [artifact], operationKey: 'artifact-seal:wrong-episode',
  }), /causally bound/u);
});

test('version projections preserve legacy receipt fields and immutable provenance', () => {
  const work = workFixture('projection');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/projection/result.txt', content: 'projection\n',
  });
  const sealed = seal(work, [artifact], 'artifact-seal:projection');
  const version = artifactVersion(sealed.versions[0].versionRef);
  assert.ok(Object.isFrozen(version));
  assert.deepEqual(artifactVersionReceipt(version), sealed.versions[0]);
  const listed = artifactVersionsForWork(work.workItem.id);
  assert.equal(listed.length, 1);
  assert.equal(listed[0].provenanceKind, 'native_verified');
  for (const field of ['path', 'sha256', 'role', 'understandingSha256', 'requirementId']) {
    assert.equal(Object.hasOwn(sealed.versions[0], field), true, field);
  }
  assert.throws(() => artifactVersionReceipt({
    ...version, path: 'data/workspace/forged/result.txt',
  }), /conflicts with durable storage/u);
});

test('artifact sealing consumeert exact legacy v1 en sequence-gebonden v2 receipts', () => {
  for (const executionContract of ['ExecutionRecord/v1', 'ExecutionRecord/v2']) {
    const label = executionContract.endsWith('/v1') ? 'ledger-v1' : 'ledger-v2';
    const work = workFixture(label);
    const artifact = provenArtifact({
      filePath: `data/workspace/${label}/result.txt`,
      content: `${label} exact\n`, executionContract,
    });
    const executionRows = db.prepare(`SELECT execution_contract
      FROM main.turn_tool_executions WHERE turn_id=(
        SELECT turn_id FROM main.turn_tool_executions WHERE execution_key=?
      ) ORDER BY execution_seq`).all(artifact.writeExecutionKey);
    assert.deepEqual(new Set(executionRows.map((row) => row.execution_contract)),
      new Set([executionContract]));
    const sealed = seal(work, [artifact], `artifact-seal:${label}`);
    assert.equal(sealed.versions[0].sha256, artifact.sha256);
  }
});

test('execution rowid relocation does not invalidate sealed provenance', () => {
  const work = workFixture('rowid-relocation');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/rowid-relocation/result.txt',
    content: 'stable across sqlite maintenance\n',
  });
  const sealed = seal(work, [artifact], 'artifact-seal:rowid-relocation');
  db.prepare('UPDATE turn_tool_executions SET rowid=rowid+1000000').run();
  const version = artifactVersion(sealed.versions[0].versionRef);
  assert.equal(version.sha256, artifact.sha256);
  assert.equal(artifactBytes(version.versionRef, {
    expectedSha256: artifact.sha256,
  }).toString('utf8'), 'stable across sqlite maintenance\n');
});

test('sealed provenance rejects later execution sequence relocation', () => {
  const work = workFixture('sequence-relocation');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/sequence-relocation/result.js',
    content: 'process.stdout.write("stable sequence\\n");\n',
    executable: true, role: 'source',
  });
  const sealed = seal(work, [artifact], 'artifact-seal:sequence-relocation');
  const proofKeys = [
    artifact.writeExecutionKey,
    ...artifact.runExecutions.map((run) => run.executionKey),
    artifact.readExecutionKey,
  ];
  const trigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='turn_tool_executions_sequence_immutable'`).get().sql;
  const shift = Number(db.prepare(
    'SELECT coalesce(max(execution_seq),0)+1000 AS value FROM main.turn_tool_executions',
  ).get().value);
  const placeholders = proofKeys.map(() => '?').join(',');
  db.exec('DROP TRIGGER main.turn_tool_executions_sequence_immutable');
  db.prepare(`UPDATE main.turn_tool_executions
    SET execution_seq=execution_seq+? WHERE execution_key IN (${placeholders})`)
    .run(shift, ...proofKeys);
  db.exec(trigger);
  try {
    assert.throws(() => artifactVersion(sealed.versions[0].versionRef),
      /artifact execution receipt is invalid: duurzame toolintentie heeft een ongeldige intentierecordhash/u);
    assert.throws(() => artifactVersionReceipt(sealed.versions[0].versionRef),
      /artifact execution receipt is invalid: duurzame toolintentie heeft een ongeldige intentierecordhash/u);
  } finally {
    db.exec('DROP TRIGGER main.turn_tool_executions_sequence_immutable');
    db.prepare(`UPDATE main.turn_tool_executions
      SET execution_seq=execution_seq-? WHERE execution_key IN (${placeholders})`)
      .run(shift, ...proofKeys);
    db.exec(trigger);
  }
});

test('version reads reject metadata corruption after trigger drop and restoration', () => {
  const work = workFixture('metadata-corruption');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/metadata/result.txt', content: 'metadata intact\n',
  });
  const sealed = seal(work, [artifact], 'artifact-seal:metadata-corruption');
  const trigger = db.prepare(
    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='eva_artifact_versions_no_update'",
  ).get().sql;
  db.exec('DROP TRIGGER eva_artifact_versions_no_update');
  db.prepare('UPDATE eva_artifact_versions SET provenance_sha256=? WHERE version_ref=?')
    .run('e'.repeat(64), sealed.versions[0].versionRef);
  db.exec(trigger);
  assert.equal(artifactVersionStoreStatus().available, true);
  assert.throws(() => artifactVersion(sealed.versions[0].versionRef),
    /provenance failed integrity verification/u);
  assert.throws(() => artifactVersionsForWork(work.workItem.id),
    /provenance failed integrity verification/u);
  assert.throws(() => artifactVersionReceipt(sealed.versions[0].versionRef),
    /provenance failed integrity verification/u);
  assert.throws(() => artifactBytes(sealed.versions[0].versionRef, {
    expectedSha256: artifact.sha256,
  }), /provenance failed integrity verification/u);
});

test('object, seal and version rows reject UPDATE and DELETE', () => {
  const work = workFixture('append-only');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/append-only/result.txt', content: 'immutable\n',
  });
  const sealed = seal(work, [artifact], 'artifact-seal:append-only');
  assert.throws(() => db.prepare(
    'UPDATE eva_artifact_objects SET created_ts=? WHERE sha256=?',
  ).run(new Date().toISOString(), artifact.sha256), /append-only/u);
  assert.throws(() => db.prepare(
    'UPDATE eva_artifact_seals SET created_ts=? WHERE seal_ref=?',
  ).run(new Date().toISOString(), sealed.sealRef), /append-only/u);
  assert.throws(() => db.prepare(
    'DELETE FROM eva_artifact_versions WHERE version_ref=?',
  ).run(sealed.versions[0].versionRef), /append-only/u);
});

test('INSERT OR REPLACE cannot bypass object, seal, or version append-only identity', () => {
  const work = workFixture('replace-bypass');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/replace-bypass/original.txt',
    content: 'replace-resistant bytes\n',
  });
  const sealed = seal(work, [artifact], 'artifact-seal:replace-bypass');
  const objectBefore = db.prepare(
    'SELECT * FROM eva_artifact_objects WHERE sha256=?',
  ).get(artifact.sha256);
  const sealBefore = db.prepare(
    'SELECT * FROM eva_artifact_seals WHERE seal_ref=?',
  ).get(sealed.sealRef);
  const versionBefore = db.prepare(
    'SELECT * FROM eva_artifact_versions WHERE version_ref=?',
  ).get(sealed.versions[0].versionRef);

  assert.throws(() => replaceArtifactRow('eva_artifact_objects', objectBefore, {
    created_ts: '2099-01-01T00:00:00.000Z',
  }), /artifact object identity collision/u);
  assert.throws(() => replaceArtifactRow('eva_artifact_seals', sealBefore, {
    created_ts: '2099-01-01T00:00:00.000Z',
  }), /artifact seal identity collision/u);
  assert.throws(() => replaceArtifactRow('eva_artifact_versions', versionBefore, {
    canonical_path: 'data/workspace/replace-bypass/forged.txt',
    created_ts: '2099-01-01T00:00:00.000Z',
  }), /artifact version identity collision/u);

  assert.deepEqual(db.prepare(
    'SELECT * FROM eva_artifact_objects WHERE sha256=?',
  ).get(artifact.sha256), objectBefore);
  assert.deepEqual(db.prepare(
    'SELECT * FROM eva_artifact_seals WHERE seal_ref=?',
  ).get(sealed.sealRef), sealBefore);
  assert.deepEqual(db.prepare(
    'SELECT * FROM eva_artifact_versions WHERE version_ref=?',
  ).get(sealed.versions[0].versionRef), versionBefore);
});

test('byte reads rehash stored content and fail closed after same-length corruption', () => {
  const work = workFixture('corruption');
  const artifact = provenArtifact({
    work, filePath: 'data/workspace/corruption/result.txt', content: 'original bytes\n',
  });
  const sealed = seal(work, [artifact], 'artifact-seal:corruption');
  const trigger = db.prepare(
    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='eva_artifact_objects_no_update'",
  ).get().sql;
  db.exec('DROP TRIGGER eva_artifact_objects_no_update');
  db.prepare('UPDATE eva_artifact_objects SET content=? WHERE sha256=?')
    .run(Buffer.from('corrupted byte\n'), artifact.sha256);
  db.exec(trigger);
  assert.equal(artifactVersionStoreStatus().available, true);
  assert.throws(() => artifactBytes(sealed.versions[0].versionRef, {
    expectedSha256: artifact.sha256,
  }), /(?:integrity|byte) verification/u);
});

test('missing append-only schema protection makes APIs unavailable without import failure', () => {
  db.exec('DROP TRIGGER eva_artifact_versions_no_delete');
  assert.deepEqual(artifactVersionStoreStatus(), {
    contract: 'ArtifactVersionStoreStatus/v1', available: false,
    reason: 'artifact_schema_triggers_invalid',
  });
  assert.throws(() => artifactVersion('artifact-version:'.concat('0'.repeat(64))),
    /store unavailable/u);
});

test('counterfeit no-op append-only trigger is detected exactly', () => {
  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-artifact-counterfeit-'));
  const source = `
    process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
    const artifacts=await import('./src/mind/artifacts.js');
    const {db}=await import('./src/db.js');
    db.exec('DROP TRIGGER eva_artifact_versions_no_delete');
    db.exec("CREATE TRIGGER eva_artifact_versions_no_delete BEFORE DELETE ON eva_artifact_versions WHEN 0 BEGIN SELECT RAISE(ABORT,'artifact versions are append-only'); END");
    process.stdout.write(JSON.stringify(artifacts.artifactVersionStoreStatus()));
    db.close();
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
  });
  try {
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.deepEqual(JSON.parse(result.stdout), {
      contract: 'ArtifactVersionStoreStatus/v1', available: false,
      reason: 'artifact_schema_triggers_invalid',
    });
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('quoted trigger literals remain byte-exact during schema attestation', () => {
  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-artifact-literal-'));
  const source = `
    process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
    const artifacts=await import('./src/mind/artifacts.js');
    const {db}=await import('./src/db.js');
    db.exec('DROP TRIGGER eva_artifact_versions_no_delete');
    db.exec("CREATE TRIGGER eva_artifact_versions_no_delete BEFORE DELETE ON eva_artifact_versions BEGIN SELECT RAISE(ABORT,'ARTIFACT VERSIONS ARE APPEND-ONLY'); END");
    process.stdout.write(JSON.stringify(artifacts.artifactVersionStoreStatus()));
    db.close();
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
  });
  try {
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.deepEqual(JSON.parse(result.stdout), {
      contract: 'ArtifactVersionStoreStatus/v1', available: false,
      reason: 'artifact_schema_triggers_invalid',
    });
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('unexpected owned triggers and indexes fail-disable the artifact store', () => {
  for (const [label, mutation, expectedReason] of [
    [
      'extra-trigger',
      "CREATE TRIGGER eva_artifact_objects_unexpected BEFORE INSERT ON eva_artifact_objects BEGIN SELECT RAISE(IGNORE); END",
      'artifact_schema_triggers_invalid',
    ],
    [
      'extra-index',
      'CREATE INDEX eva_artifact_versions_unexpected ON eva_artifact_versions(role)',
      'artifact_schema_indexes_invalid',
    ],
  ]) {
    const isolated = fs.mkdtempSync(path.join(
      os.tmpdir(), `bottie-artifact-${label}-`,
    ));
    const source = `
      process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
      const artifacts=await import('./src/mind/artifacts.js');
      const {db}=await import('./src/db.js');
      db.exec(${JSON.stringify(mutation)});
      process.stdout.write(JSON.stringify(artifacts.artifactVersionStoreStatus()));
      db.close();
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
    });
    try {
      assert.equal(result.status, 0, result.stderr || result.stdout);
      assert.deepEqual(JSON.parse(result.stdout), {
        contract: 'ArtifactVersionStoreStatus/v1', available: false,
        reason: expectedReason,
      });
    } finally {
      fs.rmSync(isolated, { recursive: true, force: true });
    }
  }
});

test('TEMP shadows van owned of dependency tables fail-disablen vóór iedere artifactbewerking', () => {
  for (const [label, mutation] of [
    [
      'owned-temp-shadow',
      `CREATE TEMP TABLE eva_artifact_objects (
        sha256 TEXT PRIMARY KEY,
        byte_length INTEGER NOT NULL,
        content BLOB NOT NULL,
        created_ts TEXT NOT NULL
      )`,
    ],
    [
      'dependency-temp-shadow',
      `CREATE TEMP TABLE turn_tool_executions (
        execution_key TEXT PRIMARY KEY,
        turn_id TEXT,
        tool TEXT,
        args TEXT,
        semantic_hash TEXT,
        semantic_binding TEXT,
        intent_record_sha256 TEXT,
        status TEXT,
        started_ts TEXT,
        finished_ts TEXT,
        output TEXT,
        output_sha256 TEXT,
        material_sha256 TEXT,
        completion_record_sha256 TEXT
      )`,
    ],
    [
      'mixed-case-owned-temp-view',
      `CREATE TEMP VIEW Eva_Artifact_Objects AS
        SELECT sha256,byte_length,content,created_ts
        FROM main.eva_artifact_objects`,
    ],
    [
      'mixed-case-dependency-temp-shadow',
      `CREATE TEMP TABLE TuRn_ToOl_ExEcUtIoNs (
        execution_key TEXT PRIMARY KEY,
        turn_id TEXT,
        tool TEXT,
        args TEXT,
        semantic_hash TEXT,
        semantic_binding TEXT,
        intent_record_sha256 TEXT,
        status TEXT,
        started_ts TEXT,
        finished_ts TEXT,
        output TEXT,
        output_sha256 TEXT,
        material_sha256 TEXT,
        completion_record_sha256 TEXT
      )`,
    ],
    [
      'mixed-case-temp-trigger',
      `CREATE TEMP TABLE artifact_shadow_helper (value TEXT);
       CREATE TEMP TRIGGER Eva_Artifact_Shadow_Trigger
       BEFORE INSERT ON artifact_shadow_helper BEGIN SELECT RAISE(IGNORE); END`,
    ],
  ]) {
    const isolated = fs.mkdtempSync(path.join(
      os.tmpdir(), `bottie-artifact-${label}-`,
    ));
    const source = `
      process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
      const artifacts=await import('./src/mind/artifacts.js');
      const {db}=await import('./src/db.js');
      db.exec(${JSON.stringify(mutation)});
      const status=artifacts.artifactVersionStoreStatus();
      let operation='accepted';
      try {
        artifacts.storeWorkspaceArtifactObject(Buffer.from('shadow'), {
          expectedSha256:${JSON.stringify(sha256('shadow'))},
        });
      } catch (error) { operation=String(error.message||error); }
      process.stdout.write(JSON.stringify({status,operation}));
      db.close();
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
    });
    try {
      assert.equal(result.status, 0, result.stderr || result.stdout);
      const observed = JSON.parse(result.stdout);
      assert.equal(observed.status.available, false, label);
      assert.equal(observed.status.reason, 'artifact_schema_temp_shadow', label);
      assert.match(observed.operation, /store unavailable/u, label);
    } finally {
      fs.rmSync(isolated, { recursive: true, force: true });
    }
  }
});

test('authority verifier callbacks kunnen TEMP- of main-schemadrift niet voorbij de postcheck brengen', () => {
  for (const [label, mutation, expected] of [
    [
      'callback-temp-shadow',
      `CREATE TEMP VIEW Eva_Artifact_Objects AS
        SELECT sha256,byte_length,content,created_ts
        FROM main.eva_artifact_objects`,
      /artifact_schema_temp_shadow/u,
    ],
    [
      'callback-main-drift',
      'ALTER TABLE timeline ADD COLUMN callback_drift TEXT',
      /reference table has an invalid schema|reference schema changed/u,
    ],
  ]) {
    const isolated = fs.mkdtempSync(path.join(
      os.tmpdir(), `bottie-artifact-${label}-`,
    ));
    const source = `
      process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
      const artifacts=await import('./src/mind/artifacts.js');
      await import('./src/mind/portfolio.js');
      const {db}=await import('./src/db.js');
      artifacts.registerArtifactSealAuthorityVerifier(() => {
        db.exec(${JSON.stringify(mutation)});
        return null;
      });
      let outcome='accepted';
      try { artifacts.artifactSealAuthority(Object.freeze({})); }
      catch (error) { outcome=String(error.message||error); }
      process.stdout.write(JSON.stringify({outcome}));
      db.close();
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
    });
    try {
      assert.equal(result.status, 0, result.stderr || result.stdout);
      assert.match(JSON.parse(result.stdout).outcome, expected, label);
    } finally {
      fs.rmSync(isolated, { recursive: true, force: true });
    }
  }
});

test('counterfeit permissive collision guard is detected exactly', () => {
  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-artifact-collision-'));
  const source = `
    process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
    const artifacts=await import('./src/mind/artifacts.js');
    const {db}=await import('./src/db.js');
    db.exec('DROP TRIGGER eva_artifact_seals_no_collision');
    db.exec("CREATE TRIGGER eva_artifact_seals_no_collision BEFORE INSERT ON eva_artifact_seals WHEN 0 BEGIN SELECT RAISE(ABORT,'artifact seal identity collision'); END");
    process.stdout.write(JSON.stringify(artifacts.artifactVersionStoreStatus()));
    db.close();
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
  });
  try {
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.deepEqual(JSON.parse(result.stdout), {
      contract: 'ArtifactVersionStoreStatus/v1', available: false,
      reason: 'artifact_schema_triggers_invalid',
    });
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});
