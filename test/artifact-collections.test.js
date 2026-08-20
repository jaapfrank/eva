import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';
import Database from 'better-sqlite3';

const dataDir = fs.mkdtempSync('/tmp/bottie-artifact-collections-v3-');
process.env.EVA_DATA_DIR = dataDir;

const {
  claimTurnToolExecution, completeTurnToolExecution, db,
} = await import('../src/db.js');
const { config } = await import('../src/config.js');
const { attachEpisodeOrigin, createDurableWork } = await import('../src/mind/portfolio.js');
const {
  artifactSealAuthority, artifactVersionReceipt, registerArtifactSealAuthorityVerifier,
  sealVerifiedWorkArtifacts, storeWorkspaceArtifactObject,
} = await import('../src/mind/artifacts.js');
const {
  artifactCollectionContracts, createArtifactCollectionStore,
} = await import('../src/mind/artifactcollections.js');
const { canonicalJson } = await import('../src/util.js');

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const PREDICATE = Object.freeze({
  contract: 'ArtifactCollectionPredicate/v1',
  kind: 'explicit_snapshot_member_classification',
  included: 'current_artifact_version', excluded: 'bounded_reason_code',
});
const sealAuthorities = new WeakMap();
registerArtifactSealAuthorityVerifier((context) => sealAuthorities.get(context) || null);

let sequence = 0;
let savepointSequence = 0;

after(() => {
  if (db.open) db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function originSnapshot(row) {
  return {
    id: Number(row.id), episodeId: Number(row.episode_id), originKey: row.origin_key,
    originKind: row.origin_kind, originChannel: row.origin_channel,
    originConversationId: row.origin_conversation_id,
    originMessageId: row.origin_message_id, requester: row.requester,
    attachedTs: row.attached_ts,
  };
}

function timelineSnapshot(row) {
  return {
    id: Number(row.id), ts: row.ts, kind: row.kind,
    contentSha256: sha256(Buffer.from(row.content, 'utf8')),
    meta: JSON.parse(row.meta), outcomeKey: row.outcome_key,
  };
}

function actionResult(tool, data, artifactReceipt = null) {
  return JSON.stringify({
    contract: 'ActionResult/v1', tool, ok: true, changed: tool === 'schrijf_bestand',
    verified: true, evidence: { source: tool, summary: `${tool} verified` },
    needs_followup: false, partial: false, risk: 'normal', human_message: '',
    ...(artifactReceipt ? { artifact_receipt: artifactReceipt } : {}), data,
  });
}

function completeExecution({ key, turnId, tool, args, binding = null, output }) {
  const claimed = claimTurnToolExecution({
    executionKey: key, turnId, tool, args, semanticBinding: binding,
  });
  assert.equal(claimed.claimed, true);
  return completeTurnToolExecution(key, output).row;
}

function completeLegacyV1Execution({ key, turnId, tool, args, binding = null, output }) {
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
  const completionRecordSha256 = sha256(canonicalJson({
    executionKey: key, turnId, tool, args: storedArgs, semanticHash,
    semanticBinding: storedBinding, intentRecordSha256, status: 'done',
    startedTs, finishedTs, outputSha256, materialSha256: null,
  }));
  db.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,semantic_binding,
     intent_record_sha256,status,started_ts,finished_ts,output,output_sha256,
     material_sha256,completion_record_sha256,execution_seq,execution_contract,
     execution_verification)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    key, turnId, tool, storedArgs, semanticHash, storedBinding,
    intentRecordSha256, 'done', startedTs, finishedTs, output, outputSha256,
    null, completionRecordSha256, executionSeq, 'ExecutionRecord/v1', 'verified',
  );
  return db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(key);
}

function turnUnderstandingRecord(sourceSha256) {
  const body = {
    contract: 'TurnUnderstandingRecord/v1', sourceSha256,
    sourceKind: 'test', sourceTextSha256: sha256('artifact collection source'),
    dialogueAct: 'command', goal: {
      statement: 'Maak een artifact.', outcome: 'Bewaar exacte bytes.', beneficiary: 'requester',
    }, conditions: [],
    temporal: { mode: 'none', notBefore: null, dueBy: null, condition: null },
    durability: 'episode', requestedEffects: ['workspace'], deniedEffects: [], actions: [],
    firstDecision: { kind: 'execute', requirementIds: ['r1'], replyDraftSha256: null },
    decisionProjection: {
      proposedKind: 'execute', proposedRequirementIds: ['r1'],
      acceptedRequirementIds: ['r1'], rejectedRequirementIds: [],
      unknownRequirementIds: [], hostAddedRequirementIds: [],
    }, monitoring: null,
  };
  return { ...body, sha256: sha256(canonicalJson(body)) };
}

function sealAuthorityFor(work, turnId, understandingSha256) {
  const record = turnUnderstandingRecord(understandingSha256);
  db.prepare('INSERT INTO timeline(ts,kind,content,meta) VALUES (?,?,?,?)').run(
    new Date().toISOString(), 'turn_understanding', 'artifact collection seal authority',
    JSON.stringify({
      internalOnly: true, turnId, conversationId: `test:${turnId}`,
      understanding: record, interpretationSucceeded: true,
    }),
  );
  const context = Object.freeze({});
  const proofExecutionKeys = db.prepare(`SELECT execution_key FROM turn_tool_executions
    WHERE turn_id=? AND status='done' AND json_valid(semantic_binding)
      AND json_extract(semantic_binding,'$.understandingSha256')=?
    ORDER BY execution_key`).all(turnId, understandingSha256).map((row) => row.execution_key);
  const workRevision = Number(db.prepare('SELECT revision FROM eva_work_items WHERE id=?')
    .get(work.workItem.id).revision);
  sealAuthorities.set(context, Object.freeze({
    workItemId: Number(work.workItem.id), episodeId: Number(work.episode.id),
    expectedWorkRevision: workRevision, turnId, understandingSha256, proofExecutionKeys,
  }));
  const authority = artifactSealAuthority(context);
  assert.ok(authority);
  return authority;
}

function provenArtifact(filePath, content) {
  sequence += 1;
  const bytes = Buffer.from(content, 'utf8');
  const digest = sha256(bytes);
  const understandingSha256 = sha256(`collection-understanding:${sequence}`);
  const turnId = `collection-artifact-turn-${sequence}`;
  const binding = (capabilityId, operationId) => ({
    understandingSha256, requirementId: 'r1', addressesRequirementIds: ['r1'],
    basisReceiptIds: [], capabilityId, operationId, role: 'proof', executionType: 'direct',
  });
  storeWorkspaceArtifactObject(bytes, { expectedSha256: digest });
  const write = completeExecution({
    key: `collection-artifact:${sequence}:write`, turnId, tool: 'schrijf_bestand',
    args: { pad: filePath, inhoud: content }, binding: binding('workspace.file.write', 'write'),
    output: actionResult('schrijf_bestand', {
      path: filePath, bytes: bytes.length, material_sha256: digest,
    }),
  });
  const read = completeExecution({
    key: `collection-artifact:${sequence}:read`, turnId, tool: 'lees_bestand',
    args: { pad: filePath }, binding: binding('workspace.file.read', 'read'),
    output: actionResult('lees_bestand', content, {
      path: filePath, bytes: bytes.length, source_bytes: bytes.length,
      sha256: digest, complete_read: true,
    }),
  });
  return {
    role: 'result', path: filePath, sha256: digest, understandingSha256,
    requirementId: 'r1', writeExecutionKey: write.execution_key,
    writeCompletionSha256: write.completion_record_sha256, runExecutions: [],
    readExecutionKey: read.execution_key,
    readCompletionSha256: read.completion_record_sha256, turnId,
  };
}

function seal(work, artifact, operationKey) {
  const { turnId, ...artifactInput } = artifact;
  return sealVerifiedWorkArtifacts(
    sealAuthorityFor(work, turnId, artifact.understandingSha256),
    { artifacts: [artifactInput], operationKey },
  ).versions[0];
}

function makeFixture(t, options = {}) {
  savepointSequence += 1;
  const savepoint = `artifact_collections_test_${savepointSequence}`;
  db.exec(`SAVEPOINT ${savepoint}`);
  const temporary = fs.mkdtempSync(path.join(config.dataDir, 'case-'));
  // De productiemodule leidt deze root uitsluitend af van de geopende DB.
  // Iedere case gebruikt dezelfde retained-fd root. Maak alleen haar kinderen
  // leeg: de actieve workspace-inode zelf verwijderen zou precies de
  // testveiligheidsauthority vernietigen die deze suite moet respecteren.
  const workspaceRoot = `${config.workspaceDir}/.`;
  const logicalWorkspaceRoot = config.logicalWorkspaceDir;
  for (const name of fs.readdirSync(workspaceRoot)) {
    fs.rmSync(path.join(workspaceRoot, name), { recursive: true, force: true });
  }
  t.after(() => {
    if (db.inTransaction) {
      db.exec(`ROLLBACK TO ${savepoint}`);
      db.exec(`RELEASE ${savepoint}`);
    }
    fs.rmSync(temporary, { recursive: true, force: true });
    for (const name of fs.readdirSync(workspaceRoot)) {
      fs.rmSync(path.join(workspaceRoot, name), { recursive: true, force: true });
    }
  });
  const fixture = {
    temporary, workspaceRoot, logicalWorkspaceRoot,
    authorities: new WeakMap(), hostHook: null,
    sequence: 0, work: null, originTimelineId: null,
  };
  fixture.store = createArtifactCollectionStore(db, {
    maxFiles: options.maxFiles || 128,
    maxBytes: options.maxBytes || 16 * 1024 * 1024,
    maxDepth: options.maxDepth || 32,
    maxDirectories: options.maxDirectories || 256,
    maxManifestBytes: options.maxManifestBytes || 400_000,
  });
  if (options.registerHost !== false) {
    fixture.store.registerAuthorityVerifier((context) => {
      fixture.hostHook?.(context);
      return fixture.authorities.get(context) || null;
    });
  }
  sequence += 1;
  const conversationId = `collection-conversation-${sequence}`;
  const messageId = `collection-message-${sequence}`;
  fixture.work = createDurableWork({
    source: 'web', owner: 'Jaap', title: `Artifact collection ${sequence}`,
    goal: 'Bewaar de exacte werkruimtecollectie.', nextStep: 'Verzamel de bewezen artifacts.',
    definitionOfDone: 'De volledige collectie is duurzaam en actueel bewezen.',
  }, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: messageId, requester: 'Jaap', deliveryPolicy: 'required',
    deliveryTarget: 'Jaap',
  });
  const scope = `web:${conversationId}`;
  const token = `token:${sequence}`;
  const inbound = db.prepare(`INSERT INTO timeline(ts,kind,content,meta,outcome_key)
    VALUES (?,'chat_in',?,?,NULL)`).run(
    new Date().toISOString(), `Jaap: ${messageId}`, JSON.stringify({
      channel: 'web', conversationId, sender: 'Jaap',
      messageContext: { messageId, ingressScope: scope, ingressToken: token },
    }),
  );
  fixture.originTimelineId = Number(inbound.lastInsertRowid);
  return fixture;
}

function logical(relativePath) {
  return `data/workspace/${relativePath.split(path.sep).join('/')}`;
}

function writeWorkspace(fixture, relativePath, content) {
  const absolute = path.join(fixture.workspaceRoot, relativePath);
  fs.mkdirSync(path.dirname(absolute), { recursive: true });
  fs.writeFileSync(absolute, content);
  return logical(relativePath);
}

function makeWorkspaceFifo(relativePath) {
  const workspaceDescriptor = Number(config.workspaceDir.split('/').at(-1));
  assert.equal(Number.isSafeInteger(workspaceDescriptor), true);
  return spawnSync('/usr/bin/mkfifo', [`/proc/self/fd/3/${relativePath}`], {
    encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe', workspaceDescriptor],
  });
}

function issue(fixture, method, authority) {
  const context = Object.freeze({});
  fixture.authorities.set(context, authority);
  return fixture.store[method](context);
}

function discoveryExecution(fixture, executionContract = 'ExecutionRecord/v2') {
  fixture.sequence += 1;
  const observation = fixture.store.inspectWorkspace();
  const execute = executionContract === 'ExecutionRecord/v1'
    ? completeLegacyV1Execution : completeExecution;
  const row = execute({
    key: `collection-discovery:${sequence}:${fixture.sequence}`,
    turnId: `collection-discovery-turn:${sequence}:${fixture.sequence}`,
    tool: observation.tool, args: observation.args,
    output: canonicalJson(observation.receipt),
  });
  return { observation, row };
}

function captureSnapshot(
  fixture,
  operationKey = `collection-snapshot:${sequence}`,
  executionContract = 'ExecutionRecord/v2',
) {
  const discovery = discoveryExecution(fixture, executionContract);
  const capability = issue(fixture, 'issueSnapshotCapability', {
    contract: 'RecursiveWorkspaceSnapshotAuthority/v1',
    discoveryExecutionKey: discovery.row.execution_key,
    discoveryCompletionSha256: discovery.row.completion_record_sha256,
  });
  assert.ok(capability);
  return fixture.store.captureSnapshot(capability, { operationKey });
}

function classification(itemPath, versionRef = null, reason = 'not_artifact') {
  return {
    path: itemPath, classification: versionRef ? 'included' : 'excluded',
    reason: {
      contract: 'ArtifactCollectionMemberReason/v1',
      code: versionRef ? 'current_artifact_version' : reason,
    }, versionRef,
  };
}

function collectionAuthority(fixture, snapshot, classifications, overrides = {}) {
  const workRow = db.prepare('SELECT * FROM eva_work_items WHERE id=?')
    .get(fixture.work.workItem.id);
  const episodeRow = db.prepare('SELECT * FROM eva_goal_episodes WHERE id=?')
    .get(fixture.work.episode.id);
  const origins = db.prepare(`SELECT * FROM eva_episode_origins WHERE episode_id=?
    ORDER BY origin_key,id`).all(episodeRow.id).map(originSnapshot);
  const selectedOriginId = overrides.originId ?? origins.find(
    (candidate) => candidate.originMessageId === episodeRow.origin_message_id,
  )?.id;
  const origin = origins.find((candidate) => candidate.id === selectedOriginId);
  assert.ok(origin);
  const timeline = timelineSnapshot(db.prepare('SELECT * FROM timeline WHERE id=?')
    .get(overrides.originTimelineId ?? fixture.originTimelineId));
  const supersessionScope = timeline.meta.messageContext.ingressScope;
  const supersessionToken = timeline.meta.messageContext.ingressToken;
  return {
    contract: 'ArtifactCollectionAuthority/v1', workItemId: Number(workRow.id),
    episodeId: Number(episodeRow.id), originId: origin.id,
    expectedWorkRevision: Number(workRow.revision),
    expectedEpisodeRevision: Number(episodeRow.revision),
    expectedOriginSha256: sha256(canonicalJson(origin)),
    originSetSha256: sha256(canonicalJson(origins)),
    originTimelineId: timeline.id, originTimelineSha256: sha256(canonicalJson(timeline)),
    supersessionScope, supersessionToken,
    supersessionSha256: sha256(canonicalJson({ supersessionScope, supersessionToken })),
    snapshotRef: snapshot.snapshotRef, predicate: PREDICATE, classifications,
    ...overrides,
  };
}

function createCollection(fixture, snapshot, classifications, operationKey) {
  const capability = issue(
    fixture, 'issueCollectionCapability',
    collectionAuthority(fixture, snapshot, classifications),
  );
  assert.ok(capability);
  return fixture.store.createCollection(capability, { operationKey });
}

function readyCollection(t) {
  const fixture = makeFixture(t);
  const artifactPath = writeWorkspace(fixture, 'result.txt', 'eerste resultaat\n');
  const notePath = writeWorkspace(fixture, 'notes/context.txt', 'geen artifact\n');
  const snapshot = captureSnapshot(fixture);
  const receipt = seal(
    fixture.work, provenArtifact(artifactPath, 'eerste resultaat\n'),
    `collection-seal:${sequence}:first`,
  );
  const classifications = snapshot.members.map((member) => (
    member.path === artifactPath
      ? classification(member.path, receipt.versionRef)
      : classification(member.path)
  ));
  const collection = createCollection(
    fixture, snapshot, classifications, `collection-create:${sequence}`,
  );
  return { fixture, artifactPath, notePath, snapshot, receipt, collection };
}

test('collecties gebruiken uitsluitend de echte store-owned artifactauthority', (t) => {
  const { fixture, collection, receipt, snapshot } = readyCollection(t);
  assert.equal(fixture.store.registerArtifactStoreVerifier, undefined);
  assert.deepEqual(fixture.store.status(), { ready: true, reason: null });
  assert.equal(collection.contract, 'ArtifactCollection/v1');
  assert.equal(collection.members.length, snapshot.members.length);
  const included = collection.members.find((member) => member.classification === 'included');
  assert.equal(included.artifact.contract, 'ArtifactVersionReceipt/v1');
  assert.deepEqual(included.artifact, artifactVersionReceipt(receipt.versionRef));
  assert.equal(collection.artifactInventory.length, 1);
  assert.equal(collection.artifactInventory[0].receiptSha256,
    sha256(canonicalJson(receipt)));
  assert.equal(artifactCollectionContracts.collection, 'ArtifactCollection/v1');
});

test('collecties consumeren exact legacy v1 en sequence-gebonden v2 discoveryreceipts', (t) => {
  const fixture = makeFixture(t);
  writeWorkspace(fixture, 'ledger.txt', 'ledger compatibility\n');
  for (const executionContract of ['ExecutionRecord/v1', 'ExecutionRecord/v2']) {
    const label = executionContract.endsWith('/v1') ? 'v1' : 'v2';
    const snapshot = captureSnapshot(
      fixture, `collection-snapshot:ledger:${label}`, executionContract,
    );
    const row = db.prepare(`SELECT execution_contract FROM main.turn_tool_executions
      WHERE execution_key=?`).get(snapshot.discoveryExecutionKey);
    assert.equal(row.execution_contract, executionContract);
    assert.equal(snapshot.members.length, 1);
  }
});

test('een willekeurige absolute map kan nooit als production data/workspace worden gelabeld', (t) => {
  const fixture = makeFixture(t);
  const counterfeitRoot = path.join(fixture.temporary, 'counterfeit-workspace');
  fs.mkdirSync(counterfeitRoot);
  assert.throws(() => createArtifactCollectionStore(db, {
    workspaceRoot: counterfeitRoot,
  }), /configured production workspace|opaque test workspace authority/u);
});

test('alleen de DB-owned configured root kan het fysieke root-object binden', (t) => {
  makeFixture(t);
  assert.throws(() => createArtifactCollectionStore(db, {
    testWorkspaceRootAuthority: Object.freeze({
      contract: 'ArtifactCollectionTestWorkspaceRootAuthority/v1',
    }),
  }), /database-owned configured production workspace/u);

  const configuredRoot = config.workspaceDir;
  const configuredPath = path.join(configuredRoot, `configured-${sequence}.txt`);
  fs.writeFileSync(configuredPath, 'configured root\n');
  t.after(() => fs.rmSync(configuredPath, { force: true }));
  const production = createArtifactCollectionStore(db);
  production.registerAuthorityVerifier(() => null);
  const productionPaths = production.inspectWorkspace().receipt.members
    .map((member) => member.path);
  assert.ok(productionPaths.includes(`data/workspace/configured-${sequence}.txt`));
  assert.ok(productionPaths.every((item) => item.startsWith('data/workspace/')));
});

test('NODE_TEST_CONTEXT kan geen afwijkende production-workspaceroot minten', () => {
  const isolated = fs.mkdtempSync('/tmp/bottie-artifact-collection-root-authority-');
  const workspaceRoot = path.join(isolated, 'workspace');
  fs.mkdirSync(workspaceRoot);
  const source = `
    process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
    const module=await import('./src/mind/artifactcollections.js');
    let outcome='accepted';
    try { module.issueArtifactCollectionTestWorkspaceRootAuthority(
      ${JSON.stringify(workspaceRoot)}
    ); }
    catch (error) { outcome=String(error.message||error); }
    process.stdout.write(outcome);
  `;
  const childEnv = {
    ...process.env, EVA_DATA_DIR: isolated,
    // Dit is gewone, door ieder proces instelbare input en dus geen authority.
    NODE_TEST_CONTEXT: 'child',
  };
  try {
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: path.resolve('.'), encoding: 'utf8', env: childEnv,
    });
    assert.equal(child.status, 0, child.stderr || child.stdout);
    assert.match(child.stdout, /test-runner only|not a function|is not exported/u);
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('een same-length mutatie op de laatste realpath-grens kan nooit current autoriseren', (t) => {
  const { fixture, collection } = readyCollection(t);
  const target = path.join(fixture.workspaceRoot, 'result.txt');
  const original = fs.readFileSync(target);
  const replacement = Buffer.from('tweede resultaat\n', 'utf8');
  assert.equal(replacement.length, original.length, 'de race behoudt bestandsgrootte');
  const originalRealpath = fs.realpathSync.native;
  let rootCalls = 0;
  let raced = false;
  fs.realpathSync.native = function mutateAfterLastFileFence(itemPath, ...rest) {
    // De fail-closed check observeert tweemaal; iedere scan eindigt met de
    // fysieke root-realpath. Deze write valt na alle file-reads van de laatste.
    if (itemPath === fixture.workspaceRoot && ++rootCalls === 4) {
      fs.writeFileSync(target, replacement);
      raced = true;
    }
    return originalRealpath.call(fs.realpathSync, itemPath, ...rest);
  };
  let current;
  try {
    current = fixture.store.collectionCurrentReceipt(collection.collectionRef, {
      operationKey: `collection-current:${sequence}:last-realpath-mutation`,
    });
  } finally {
    fs.realpathSync.native = originalRealpath;
  }
  assert.equal(raced, true, 'de test moet exact de post-last-file race raken');
  assert.equal(current.current, false);
  assert.equal(current.cas, null);
  assert.ok(current.reasons.some((reason) => (
    reason.code === 'workspace_linearizability_unavailable'
  )));
});

test('een verwijderde target-head buiten de snapshot blokkeert volledige collectieclosure', (t) => {
  const fixture = makeFixture(t);
  const deletedPath = writeWorkspace(fixture, 'deleted-target.txt', 'durable target\n');
  seal(
    fixture.work, provenArtifact(deletedPath, 'durable target\n'),
    `collection-seal:${sequence}:deleted-target`,
  );
  fs.unlinkSync(path.join(fixture.workspaceRoot, 'deleted-target.txt'));
  const visiblePath = writeWorkspace(fixture, 'visible-note.txt', 'geen artifact\n');
  const snapshot = captureSnapshot(fixture);
  assert.deepEqual(snapshot.members.map((member) => member.path), [visiblePath]);
  const capability = issue(fixture, 'issueCollectionCapability', collectionAuthority(
    fixture, snapshot, [classification(visiblePath)],
  ));
  assert.throws(() => fixture.store.createCollection(capability, {
    operationKey: `collection-create:${sequence}:deleted-target`,
  }), /artifact inventory path is absent from the complete workspace snapshot/u);
  assert.equal(db.prepare('SELECT count(*) AS n FROM eva_artifact_collections').get().n, 0);
});

test('een collection receipt is historisch en expliciet non-current tot een aparte check', (t) => {
  const { collection } = readyCollection(t);
  assert.equal(collection.current, false);
  assert.equal(collection.universal, false);
  assert.equal(collection.currentCheckRequired, true);
});

test('een lege workspacecollection faalt contractueel gesloten', (t) => {
  const fixture = makeFixture(t);
  const snapshot = captureSnapshot(fixture);
  assert.deepEqual(snapshot.members, []);
  assert.throws(() => issue(fixture, 'issueCollectionCapability', collectionAuthority(
    fixture, snapshot, [],
  )), /invalid ArtifactCollectionAuthority/u);
  assert.equal(db.prepare('SELECT count(*) AS n FROM eva_artifact_collections').get().n, 0);
});

test('ready vereist zowel de echte artifactstore als een host-authority verifier', (t) => {
  const fixture = makeFixture(t, { registerHost: false });
  assert.deepEqual(fixture.store.status(), {
    ready: false, reason: 'host authority verifier is unavailable',
  });
  assert.throws(() => fixture.store.inspectWorkspace(), /host authority verifier is unavailable/u);
});

test('scanner inventariseert recursief en faalt gesloten op symlink en FIFO', (t) => {
  const fixture = makeFixture(t);
  writeWorkspace(fixture, '.hidden/deep/value.txt', 'zichtbaar voor snapshot\n');
  const observation = fixture.store.inspectWorkspace();
  assert.deepEqual(observation.receipt.members.map((member) => member.path),
    ['data/workspace/.hidden/deep/value.txt']);
  fs.symlinkSync('value.txt', path.join(fixture.workspaceRoot, '.hidden/deep/link.txt'));
  assert.throws(() => fixture.store.inspectWorkspace(), /symbolic_link/u);
  fs.unlinkSync(path.join(fixture.workspaceRoot, '.hidden/deep/link.txt'));
  const made = makeWorkspaceFifo('pipe');
  assert.equal(made.status, 0, made.stderr);
  assert.throws(() => fixture.store.inspectWorkspace(), /unsupported_file_type/u);
});

test('scanner en artifactpadcanon delen whitespace- en NFC-segmentregels', (t) => {
  const fixture = makeFixture(t);
  for (const [label, fileName] of [
    ['trailing-space', 'trailing.txt '],
    ['nonbreaking-space', 'nonbreaking\u00A0space.txt'],
    ['decomposed-nfc', 'e\u0301.txt'],
  ]) {
    const absolute = path.join(fixture.workspaceRoot, fileName);
    fs.writeFileSync(absolute, label);
    assert.throws(() => fixture.store.inspectWorkspace(), /unsafe_path/u, label);
    fs.unlinkSync(absolute);
  }
  writeWorkspace(fixture, 'ordinary internal space.txt', 'accepted\n');
  assert.deepEqual(fixture.store.inspectWorkspace().receipt.members.map((member) => member.path),
    ['data/workspace/ordinary internal space.txt']);
});

test('regular-naar-FIFO swap blokkeert niet en wordt via descriptor-fstat geweigerd', (t) => {
  const fixture = makeFixture(t);
  const target = path.join(fixture.workspaceRoot, 'swap.txt');
  fs.writeFileSync(target, 'regular\n');
  const originalOpen = fs.openSync;
  let swapped = false;
  fs.openSync = function guardedOpen(itemPath, flags, ...rest) {
    if (!swapped && String(itemPath).startsWith('/proc/self/fd/')
      && String(itemPath).endsWith('/swap.txt')) {
      swapped = true;
      fs.unlinkSync(target);
      const made = makeWorkspaceFifo('swap.txt');
      assert.equal(made.status, 0, made.stderr);
    }
    return originalOpen.call(fs, itemPath, flags, ...rest);
  };
  const started = Date.now();
  try {
    assert.throws(() => fixture.store.inspectWorkspace(), /workspace_changed/u);
  } finally {
    fs.openSync = originalOpen;
  }
  assert.equal(swapped, true);
  assert.ok(Date.now() - started < 1_000, 'O_NONBLOCK voorkomt een hang op de FIFO');
});

test('root rename of vervanging tijdens de eindfence faalt gesloten', (t) => {
  const fixture = makeFixture(t);
  writeWorkspace(fixture, 'root.txt', 'root object\n');
  const originalRealpath = fs.realpathSync.native;
  let calls = 0;
  fs.realpathSync.native = function guardedRealpath(itemPath, ...rest) {
    if (itemPath === fixture.workspaceRoot && ++calls === 2) {
      fs.renameSync(fixture.logicalWorkspaceRoot, `${fixture.logicalWorkspaceRoot}.moved`);
      fs.mkdirSync(fixture.logicalWorkspaceRoot);
    }
    return originalRealpath.call(fs.realpathSync, itemPath, ...rest);
  };
  try {
    assert.throws(() => fixture.store.inspectWorkspace(), /workspace_root_changed/u);
  } finally {
    fs.realpathSync.native = originalRealpath;
    fs.rmSync(fixture.logicalWorkspaceRoot, { recursive: true, force: true });
    fs.renameSync(`${fixture.logicalWorkspaceRoot}.moved`, fixture.logicalWorkspaceRoot);
  }
});

test('hostcallback mutate+throw wordt na de callback teruggecontroleerd en teruggedraaid', (t) => {
  const fixture = makeFixture(t);
  const before = db.prepare('SELECT count(*) AS n FROM timeline').get().n;
  fixture.hostHook = () => {
    db.prepare("INSERT INTO timeline(ts,kind,content) VALUES (?,'system','forged')")
      .run(new Date().toISOString());
    throw new Error('na mutatie');
  };
  const context = Object.freeze({});
  fixture.authorities.set(context, {
    contract: 'RecursiveWorkspaceSnapshotAuthority/v1',
    discoveryExecutionKey: 'never-used', discoveryCompletionSha256: '0'.repeat(64),
  });
  assert.throws(() => fixture.store.issueSnapshotCapability(context),
    /changed protected database state/u);
  assert.equal(db.prepare('SELECT count(*) AS n FROM timeline').get().n, before);
});

test('Proxy/getter-normalisatie kan geen post-return mutatie verbergen', (t) => {
  const fixture = makeFixture(t);
  const before = db.prepare('SELECT count(*) AS n FROM timeline').get().n;
  const raw = {
    contract: 'RecursiveWorkspaceSnapshotAuthority/v1',
    discoveryExecutionKey: 'getter-execution', discoveryCompletionSha256: '0'.repeat(64),
  };
  let mutated = false;
  const candidate = new Proxy(raw, {
    get(target, property, receiver) {
      if (!mutated && property === 'contract') {
        mutated = true;
        db.prepare("INSERT INTO timeline(ts,kind,content) VALUES (?,'system','getter')")
          .run(new Date().toISOString());
      }
      return Reflect.get(target, property, receiver);
    },
  });
  const context = Object.freeze({});
  fixture.authorities.set(context, candidate);
  assert.throws(() => fixture.store.issueSnapshotCapability(context),
    /changed protected database state/u);
  assert.equal(db.prepare('SELECT count(*) AS n FROM timeline').get().n, before);

  let coerced = false;
  const strictContext = Object.freeze({});
  fixture.authorities.set(strictContext, {
    ...raw, discoveryExecutionKey: { toString() { coerced = true; return 'forged'; } },
  });
  assert.throws(() => fixture.store.issueSnapshotCapability(strictContext), /invalid/u);
  assert.equal(coerced, false, 'stricte types roepen caller-toString niet aan');
});

test('classificatie is exact gelijk aan target-head en current artifacts zijn nooit excluded', (t) => {
  const fixture = makeFixture(t);
  const artifactPath = writeWorkspace(fixture, 'relation.txt', 'relation\n');
  const snapshot = captureSnapshot(fixture);
  const receipt = seal(
    fixture.work, provenArtifact(artifactPath, 'relation\n'),
    `collection-seal:${sequence}:relation`,
  );
  const excluded = issue(fixture, 'issueCollectionCapability', collectionAuthority(
    fixture, snapshot, [classification(artifactPath)],
  ));
  assert.throws(() => fixture.store.createCollection(excluded, {
    operationKey: `collection-create:${sequence}:excluded-current`,
  }), /classification does not exactly match/u);

  const stale = issue(fixture, 'issueCollectionCapability', collectionAuthority(
    fixture, snapshot, [classification(
      artifactPath,
      receipt.previousVersionRef || `${receipt.versionRef.slice(0, 'artifact-version:'.length)}${
        receipt.versionRef['artifact-version:'.length] === '0' ? '1' : '0'
      }${receipt.versionRef.slice('artifact-version:'.length + 1)}`,
    )],
  ));
  assert.throws(() => fixture.store.createCollection(stale, {
    operationKey: `collection-create:${sequence}:stale-version`,
  }), /not the current version|lacks its exact durable receipt|invalid artifact version/u);
});

test('een actuele head van een ander work/episode kan niet als exclusion worden verstopt', (t) => {
  const fixture = makeFixture(t);
  const artifactPath = writeWorkspace(fixture, 'foreign.txt', 'zelfde bytes\n');
  const snapshot = captureSnapshot(fixture);
  sequence += 1;
  const other = createDurableWork({
    source: 'test', owner: 'Jaap', title: `Other ${sequence}`, goal: 'Ander werk.',
    nextStep: 'Seal.', definitionOfDone: 'Sealed.',
  }, {
    originKind: 'user', originChannel: 'test', originConversationId: `other:${sequence}`,
    originMessageId: String(sequence), requester: 'Jaap', deliveryPolicy: 'none',
  });
  seal(other, provenArtifact(artifactPath, 'zelfde bytes\n'), `collection-seal:${sequence}:foreign`);
  const capability = issue(fixture, 'issueCollectionCapability', collectionAuthority(
    fixture, snapshot, [classification(artifactPath)],
  ));
  assert.throws(() => fixture.store.createCollection(capability, {
    operationKey: `collection-create:${sequence}:foreign`,
  }), /another work episode/u);
});

test('een legitieme later aangehechte origin met eigen supersessionscope blijft bereikbaar', (t) => {
  const fixture = makeFixture(t);
  const artifactPath = writeWorkspace(fixture, 'later-origin.txt', 'later origin\n');
  const snapshot = captureSnapshot(fixture);
  const receipt = seal(
    fixture.work, provenArtifact(artifactPath, 'later origin\n'),
    `collection-seal:${sequence}:later-origin`,
  );
  const conversationId = `later-origin-conversation:${sequence}`;
  const messageId = `later-origin-message:${sequence}`;
  const laterOrigin = attachEpisodeOrigin(fixture.work.episode.id, {
    originKind: 'user', originChannel: 'web', originConversationId: conversationId,
    originMessageId: messageId, requester: 'Jaap',
  });
  const scope = `web:${conversationId}`;
  const token = `later-origin-token:${sequence}`;
  const timeline = db.prepare(`INSERT INTO timeline(ts,kind,content,meta,outcome_key)
    VALUES (?,'chat_in',?,?,NULL)`).run(
    new Date().toISOString(), 'later legitimate inbound', JSON.stringify({
      channel: 'web', conversationId, sender: 'Jaap',
      messageContext: { messageId, ingressScope: scope, ingressToken: token },
    }),
  );
  const authority = collectionAuthority(
    fixture, snapshot, [classification(artifactPath, receipt.versionRef)], {
      originId: Number(laterOrigin.id), originTimelineId: Number(timeline.lastInsertRowid),
    },
  );
  const capability = issue(fixture, 'issueCollectionCapability', authority);
  const collection = fixture.store.createCollection(capability, {
    operationKey: `collection-create:${sequence}:later-origin`,
  });
  assert.equal(collection.origin.id, Number(laterOrigin.id));
  assert.equal(collection.originAuthority.supersessionScope, scope);
});

test('de laatste storefence ziet een headmutatie tussen voorbereiding en insert', (t) => {
  const fixture = makeFixture(t);
  const artifactPath = writeWorkspace(fixture, 'last-fence.txt', 'eerste\n');
  const snapshot = captureSnapshot(fixture);
  const first = seal(
    fixture.work, provenArtifact(artifactPath, 'eerste\n'),
    `collection-seal:${sequence}:before-last-fence`,
  );
  const replacement = provenArtifact(artifactPath, 'eerste\n');
  const authority = collectionAuthority(fixture, snapshot, [classification(
    artifactPath, first.versionRef,
  )]);
  const capability = issue(fixture, 'issueCollectionCapability', authority);
  const originalRealpath = fs.realpathSync.native;
  let rootCalls = 0;
  let raced = false;
  fs.realpathSync.native = function lastFenceRace(itemPath, ...rest) {
    if (itemPath === fixture.workspaceRoot && ++rootCalls === 3) {
      raced = true;
      seal(fixture.work, replacement, `collection-seal:${sequence}:inside-last-fence`);
    }
    return originalRealpath.call(fs.realpathSync, itemPath, ...rest);
  };
  try {
    assert.throws(() => fixture.store.createCollection(capability, {
      operationKey: `collection-create:${sequence}:last-fence`,
    }), /artifact store.*changed|changed$/u);
  } finally {
    fs.realpathSync.native = originalRealpath;
  }
  assert.equal(raced, true);
  assert.equal(db.prepare('SELECT count(*) AS n FROM eva_artifact_versions WHERE version_ref=?')
    .get(artifactVersionReceipt(first.versionRef).versionRef).n, 1);
});

test('historische receipt blijft leesbaar terwijl current een latere head als stale rapporteert', (t) => {
  const { fixture, artifactPath, collection, receipt } = readyCollection(t);
  fs.writeFileSync(path.join(fixture.workspaceRoot, 'result.txt'), 'tweede resultaat\n');
  seal(
    fixture.work, provenArtifact(artifactPath, 'tweede resultaat\n'),
    `collection-seal:${sequence}:replacement`,
  );
  const historical = fixture.store.collection(collection.collectionRef);
  const included = historical.members.find((member) => member.classification === 'included');
  assert.equal(included.artifact.versionRef, receipt.versionRef);
  const current = fixture.store.collectionCurrentReceipt(collection.collectionRef, {
    operationKey: `collection-current:${sequence}:stale`,
  });
  assert.equal(current.current, false);
  assert.ok(current.reasons.some((reason) => reason.code === 'artifact_version_stale'));
});

test('historische collection weigert verplaatste execution sequences', (t) => {
  const { fixture, collection, receipt } = readyCollection(t);
  const proof = JSON.parse(db.prepare(`SELECT proof_json FROM main.eva_artifact_versions
    WHERE version_ref=?`).get(receipt.versionRef).proof_json);
  const proofKeys = [
    proof.write.executionKey,
    ...proof.runs.map((run) => run.executionKey),
    proof.read.executionKey,
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
    assert.throws(() => fixture.store.collection(collection.collectionRef),
      /artifact execution receipt is invalid: duurzame toolintentie heeft een ongeldige intentierecordhash/u);
  } finally {
    db.exec('DROP TRIGGER main.turn_tool_executions_sequence_immutable');
    db.prepare(`UPDATE main.turn_tool_executions
      SET execution_seq=execution_seq-? WHERE execution_key IN (${placeholders})`)
      .run(shift, ...proofKeys);
    db.exec(trigger);
  }
});

test('scan-only collections leveren geen current of universele CAS-authority', (t) => {
  const { fixture, collection } = readyCollection(t);
  const current = fixture.store.collectionCurrentReceipt(collection.collectionRef, {
    operationKey: `collection-current:${sequence}:ready`,
  });
  assert.equal(current.current, false);
  assert.equal(current.cas, null);
  assert.ok(current.reasons.some((reason) => (
    reason.code === 'workspace_linearizability_unavailable'
  )));
  const authority = {
    contract: 'ArtifactCollectionCasAuthority/v1', checkRef: current.checkRef,
    receiptSha256: current.receiptSha256, casSha256: '0'.repeat(64),
    collectionRef: current.collectionRef, collectionSha256: current.collectionSha256,
    stateSha256: current.stateSha256,
  };
  const capability = issue(fixture, 'issueCollectionCasCapability', authority);
  assert.equal(capability, null);
  assert.throws(() => fixture.store.consumeCollectionCas(capability, {
    operationKey: `collection-consume:${sequence}:blocked`,
  }), /one-shot/u);
  assert.equal(db.prepare('SELECT count(*) AS n FROM eva_artifact_collection_consumptions')
    .get().n, 0);
});

test('ook zonder zichtbare drift kan een scan nooit als filesystem-CAS dienen', (t) => {
  const { fixture, collection } = readyCollection(t);
  const current = fixture.store.collectionCurrentReceipt(collection.collectionRef, {
    operationKey: `collection-current:${sequence}:before-new-member`,
  });
  assert.equal(current.current, false);
  assert.equal(current.cas, null);
  const capability = issue(fixture, 'issueCollectionCasCapability', {
    contract: 'ArtifactCollectionCasAuthority/v1', checkRef: current.checkRef,
    receiptSha256: current.receiptSha256, casSha256: '0'.repeat(64),
    collectionRef: current.collectionRef, collectionSha256: current.collectionSha256,
    stateSha256: current.stateSha256,
  });
  assert.equal(capability, null);
  writeWorkspace(fixture, 'appeared-after-check.txt', 'new member\n');
  assert.throws(() => fixture.store.consumeCollectionCas(capability, {
    operationKey: `collection-consume:${sequence}:after-new-member`,
  }), /one-shot/u);
});

test('work/episode/origin/supersessionwijzigingen maken current stale, niet history ongeldig', (t) => {
  const { fixture, collection } = readyCollection(t);
  db.prepare('UPDATE eva_work_items SET revision=revision+1 WHERE id=?')
    .run(fixture.work.workItem.id);
  db.prepare('UPDATE eva_goal_episodes SET revision=revision+1 WHERE id=?')
    .run(fixture.work.episode.id);
  attachEpisodeOrigin(fixture.work.episode.id, {
    originKind: 'user', originChannel: 'web',
    originConversationId: `later-origin:${sequence}`, originMessageId: `later:${sequence}`,
    requester: 'Jaap',
  });
  const originalTimeline = timelineSnapshot(db.prepare('SELECT * FROM timeline WHERE id=?')
    .get(fixture.originTimelineId));
  db.prepare(`INSERT INTO timeline(ts,kind,content,meta,outcome_key)
    VALUES (?,'chat_in',?,?,NULL)`).run(
    new Date().toISOString(), 'superseding inbound', JSON.stringify({
      channel: 'web', conversationId: originalTimeline.meta.conversationId, sender: 'Jaap',
      messageContext: {
        messageId: `superseding:${sequence}`,
        ingressScope: originalTimeline.meta.messageContext.ingressScope,
        ingressToken: `superseding-token:${sequence}`,
      },
    }),
  );
  assert.equal(fixture.store.collection(collection.collectionRef).collectionRef,
    collection.collectionRef);
  const current = fixture.store.collectionCurrentReceipt(collection.collectionRef, {
    operationKey: `collection-current:${sequence}:authority-stale`,
  });
  assert.equal(current.current, false);
  assert.ok(current.reasons.some((reason) => reason.code === 'work_revision_changed'));
  assert.ok(current.reasons.some((reason) => reason.code === 'episode_revision_changed'));
  assert.ok(current.reasons.some((reason) => reason.code === 'origin_set_changed'));
  assert.ok(current.reasons.some((reason) => reason.code === 'origin_superseded'));
});

test('TEMP shadows, dependency-objectdrift en OR REPLACE worden fail-closed geweigerd', (t) => {
  const { fixture, collection } = readyCollection(t);
  db.exec('CREATE TEMP TABLE eva_artifact_collections(fake TEXT)');
  assert.equal(fixture.store.status().ready, false);
  db.exec('DROP TABLE temp.eva_artifact_collections');
  db.exec('CREATE INDEX unexpected_collection_dependency_index ON timeline(content)');
  assert.equal(fixture.store.status().ready, false);
  db.exec('DROP INDEX unexpected_collection_dependency_index');
  const row = db.prepare('SELECT * FROM eva_artifact_collections WHERE collection_ref=?')
    .get(collection.collectionRef);
  const columns = Object.keys(row);
  assert.throws(() => db.prepare(`INSERT OR REPLACE INTO eva_artifact_collections
    (${columns.join(',')}) VALUES (${columns.map((column) => `@${column}`).join(',')})`)
    .run({ ...row, created_ts: '2099-01-01T00:00:00.000Z' }), /identity collision/u);
});

test('collection authority herverifieert echte proof/provenance en weigert corrupte versies', (t) => {
  const fixture = makeFixture(t);
  const artifactPath = writeWorkspace(fixture, 'corrupt-proof.txt', 'proof bytes\n');
  const snapshot = captureSnapshot(fixture);
  const receipt = seal(
    fixture.work, provenArtifact(artifactPath, 'proof bytes\n'),
    `collection-seal:${sequence}:proof-corruption`,
  );
  const trigger = db.prepare(`SELECT sql FROM sqlite_master
    WHERE type='trigger' AND name='eva_artifact_versions_no_update'`).get().sql;
  db.exec('DROP TRIGGER eva_artifact_versions_no_update');
  db.prepare('UPDATE eva_artifact_versions SET proof_json=? WHERE version_ref=?')
    .run('{}', receipt.versionRef);
  db.exec(trigger);
  const capability = issue(fixture, 'issueCollectionCapability', collectionAuthority(
    fixture, snapshot, [classification(artifactPath, receipt.versionRef)],
  ));
  assert.throws(() => fixture.store.createCollection(capability, {
    operationKey: `collection-create:${sequence}:proof-corruption`,
  }), /proof.*integrity|artifact.*invalid|hash/u);
  assert.equal(db.prepare('SELECT count(*) AS n FROM eva_artifact_collections').get().n, 0);
});

test('owned records hebben geen rowid en afhankelijke schemahash bevat hidden/default/objectsets', (t) => {
  const { fixture, collection } = readyCollection(t);
  for (const table of [
    'eva_recursive_workspace_snapshots', 'eva_recursive_workspace_snapshot_members',
    'eva_artifact_collections', 'eva_artifact_collection_members',
    'eva_artifact_collection_checks', 'eva_artifact_collection_consumptions',
  ]) {
    const sql = db.prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name=?")
      .get(table).sql;
    assert.match(sql, /WITHOUT ROWID$/u);
    assert.throws(() => db.prepare(`SELECT rowid FROM ${table}`).all(), /no such column/u);
  }
  assert.match(collection.dependencySchemaSha256, /^[a-f0-9]{64}$/u);
  assert.equal(fixture.store.status().ready, true);
});

test('een caller-database of cross-connection verifiermutatie kan geen authority leveren', (t) => {
  const fixture = makeFixture(t);
  const counterfeitPath = path.join(fixture.temporary, 'counterfeit.sqlite');
  const counterfeit = new Database(counterfeitPath);
  try {
    assert.throws(() => createArtifactCollectionStore(counterfeit),
      /exact production database capability/u);
  } finally { counterfeit.close(); }

  const second = new Database(path.join(dataDir, 'eva.db'));
  second.pragma('busy_timeout = 25');
  fixture.hostHook = () => {
    second.prepare("INSERT INTO timeline(ts,kind,content) VALUES (?,'system','cross')")
      .run(new Date().toISOString());
  };
  const context = Object.freeze({});
  fixture.authorities.set(context, {
    contract: 'RecursiveWorkspaceSnapshotAuthority/v1',
    discoveryExecutionKey: 'cross-connection', discoveryCompletionSha256: '0'.repeat(64),
  });
  try {
    assert.throws(() => fixture.store.issueSnapshotCapability(context), /failed|locked/u);
  } finally { second.close(); }

  let childResult = null;
  fixture.hostHook = () => {
    const source = `
      import Database from 'better-sqlite3';
      const database = new Database(${JSON.stringify(path.join(dataDir, 'eva.db'))}, { timeout: 25 });
      try {
        database.prepare("INSERT INTO timeline(ts,kind,content) VALUES (?,'system','process')")
          .run(new Date().toISOString());
        process.stdout.write('wrote');
      } catch (error) { process.stdout.write(String(error.code || error.message)); }
      database.close();
    `;
    childResult = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env }, timeout: 2_000,
    });
    throw new Error(`cross-process:${childResult.stdout}`);
  };
  const processContext = Object.freeze({});
  fixture.authorities.set(processContext, {
    contract: 'RecursiveWorkspaceSnapshotAuthority/v1',
    discoveryExecutionKey: 'cross-process', discoveryCompletionSha256: '0'.repeat(64),
  });
  assert.throws(() => fixture.store.issueSnapshotCapability(processContext),
    /cross-process:SQLITE_BUSY/u);
  assert.equal(childResult.status, 0, childResult.stderr);
});

test('historische collection en fail-closed current blijven exact over restart en VACUUM', () => {
  const isolated = fs.mkdtempSync('/tmp/bottie-artifact-collection-restart-');
  const workspaceRoot = path.join(isolated, 'workspace');
  fs.mkdirSync(workspaceRoot);
  const firstSource = `
    process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
    const fs=(await import('node:fs')).default;
    const {createHash}=await import('node:crypto');
    const {canonicalJson}=await import('./src/util.js');
    const {db,claimTurnToolExecution,completeTurnToolExecution}=await import('./src/db.js');
    const {createDurableWork}=await import('./src/mind/portfolio.js');
    const artifacts=await import('./src/mind/artifacts.js');
    const {createArtifactCollectionStore}=await import('./src/mind/artifactcollections.js');
    const hash=(value)=>createHash('sha256').update(value).digest('hex');
    const hashJson=(value)=>hash(canonicalJson(value));
    const now=()=>new Date().toISOString();
    const work=createDurableWork({
      source:'web',owner:'Jaap',title:'Persistent collection',goal:'Bewaar exact.',
      nextStep:'Verzamel.',definitionOfDone:'Collectie is exact en duurzaam.'
    },{
      originKind:'user',originChannel:'web',originConversationId:'persistent-conversation',
      originMessageId:'persistent-message',requester:'Jaap',deliveryPolicy:'required',
      deliveryTarget:'Jaap'
    });
    const ingressScope='web:persistent-conversation';
    const ingressToken='persistent-token';
    const inbound=db.prepare(
      "INSERT INTO timeline(ts,kind,content,meta,outcome_key) VALUES (?,'chat_in',?,?,NULL)"
    ).run(now(),'persistent inbound',JSON.stringify({
      channel:'web',conversationId:'persistent-conversation',sender:'Jaap',
      messageContext:{messageId:'persistent-message',ingressScope,ingressToken}
    }));
    const logicalPath='data/workspace/persistent-result.txt';
    const content='persistent exact bytes\\n';
    fs.writeFileSync(${JSON.stringify(path.join(workspaceRoot, 'persistent-result.txt'))},content);
    const digest=hash(content);
    artifacts.storeWorkspaceArtifactObject(content,{expectedSha256:digest});
    const turnId='persistent-artifact-turn';
    const understandingSha256=hash('persistent-understanding');
    const binding=(capabilityId,operationId)=>({
      understandingSha256,requirementId:'r1',addressesRequirementIds:['r1'],
      basisReceiptIds:[],capabilityId,operationId,role:'proof',executionType:'direct'
    });
    const action=(tool,data,artifactReceipt=null)=>JSON.stringify({
      contract:'ActionResult/v1',tool,ok:true,changed:tool==='schrijf_bestand',verified:true,
      evidence:{source:tool,summary:tool+' verified'},needs_followup:false,partial:false,
      risk:'normal',human_message:'',...(artifactReceipt?{artifact_receipt:artifactReceipt}:{}),data
    });
    const finish=(executionKey,tool,args,semanticBinding,output)=>{
      const claimed=claimTurnToolExecution({executionKey,turnId,tool,args,semanticBinding});
      if(!claimed.claimed) throw new Error('proof execution was not claimed');
      return completeTurnToolExecution(executionKey,output).row;
    };
    const write=finish('persistent-proof-write','schrijf_bestand',
      {pad:logicalPath,inhoud:content},binding('workspace.file.write','write'),
      action('schrijf_bestand',{path:logicalPath,bytes:Buffer.byteLength(content),material_sha256:digest}));
    const read=finish('persistent-proof-read','lees_bestand',{pad:logicalPath},
      binding('workspace.file.read','read'),action('lees_bestand',content,{
        path:logicalPath,bytes:Buffer.byteLength(content),source_bytes:Buffer.byteLength(content),
        sha256:digest,complete_read:true
      }));
    const understandingBody={
      contract:'TurnUnderstandingRecord/v1',sourceSha256:understandingSha256,
      sourceKind:'test',sourceTextSha256:hash('persistent source'),dialogueAct:'command',
      goal:{statement:'Maak een artifact.',outcome:'Bewaar exacte bytes.',beneficiary:'requester'},
      conditions:[],temporal:{mode:'none',notBefore:null,dueBy:null,condition:null},
      durability:'episode',requestedEffects:['workspace'],deniedEffects:[],actions:[],
      firstDecision:{kind:'execute',requirementIds:['r1'],replyDraftSha256:null},
      decisionProjection:{proposedKind:'execute',proposedRequirementIds:['r1'],
        acceptedRequirementIds:['r1'],rejectedRequirementIds:[],unknownRequirementIds:[],
        hostAddedRequirementIds:[]},monitoring:null
    };
    const understanding={...understandingBody,sha256:hashJson(understandingBody)};
    db.prepare('INSERT INTO timeline(ts,kind,content,meta) VALUES (?,?,?,?)').run(
      now(),'turn_understanding','persistent artifact authority',JSON.stringify({
        internalOnly:true,turnId,conversationId:'test:'+turnId,understanding,
        interpretationSucceeded:true
      }));
    const sealAuthorities=new WeakMap();
    artifacts.registerArtifactSealAuthorityVerifier((context)=>sealAuthorities.get(context)||null);
    const sealContext=Object.freeze({});
    sealAuthorities.set(sealContext,Object.freeze({
      workItemId:Number(work.workItem.id),episodeId:Number(work.episode.id),
      expectedWorkRevision:Number(db.prepare('SELECT revision FROM eva_work_items WHERE id=?')
        .get(work.workItem.id).revision),turnId,understandingSha256,
      proofExecutionKeys:['persistent-proof-read','persistent-proof-write']
    }));
    const sealed=artifacts.sealVerifiedWorkArtifacts(
      artifacts.artifactSealAuthority(sealContext),{
        operationKey:'persistent-artifact-seal',artifacts:[{
          role:'result',path:logicalPath,sha256:digest,understandingSha256,requirementId:'r1',
          writeExecutionKey:write.execution_key,
          writeCompletionSha256:write.completion_record_sha256,runExecutions:[],
          readExecutionKey:read.execution_key,readCompletionSha256:read.completion_record_sha256
        }]
      });
    const store=createArtifactCollectionStore(db);
    const hostAuthorities=new WeakMap();
    store.registerAuthorityVerifier((context)=>hostAuthorities.get(context)||null);
    const observation=store.inspectWorkspace();
    const discoveryClaim=claimTurnToolExecution({
      executionKey:'persistent-discovery',turnId:'persistent-discovery-turn',
      tool:observation.tool,args:observation.args
    });
    if(!discoveryClaim.claimed) throw new Error('discovery execution was not claimed');
    const discovery=completeTurnToolExecution(
      'persistent-discovery',canonicalJson(observation.receipt)
    ).row;
    const snapshotContext=Object.freeze({});
    hostAuthorities.set(snapshotContext,{
      contract:'RecursiveWorkspaceSnapshotAuthority/v1',
      discoveryExecutionKey:discovery.execution_key,
      discoveryCompletionSha256:discovery.completion_record_sha256
    });
    const snapshot=store.captureSnapshot(store.issueSnapshotCapability(snapshotContext),{
      operationKey:'persistent-snapshot'
    });
    const originRow=db.prepare(
      'SELECT * FROM eva_episode_origins WHERE episode_id=? ORDER BY origin_key,id LIMIT 1'
    ).get(work.episode.id);
    const origin={id:Number(originRow.id),episodeId:Number(originRow.episode_id),
      originKey:originRow.origin_key,originKind:originRow.origin_kind,
      originChannel:originRow.origin_channel,
      originConversationId:originRow.origin_conversation_id,
      originMessageId:originRow.origin_message_id,requester:originRow.requester,
      attachedTs:originRow.attached_ts};
    const origins=[origin];
    const timelineRow=db.prepare('SELECT * FROM timeline WHERE id=?').get(
      Number(inbound.lastInsertRowid));
    const timeline={id:Number(timelineRow.id),ts:timelineRow.ts,kind:timelineRow.kind,
      contentSha256:hash(Buffer.from(timelineRow.content,'utf8')),
      meta:JSON.parse(timelineRow.meta),outcomeKey:timelineRow.outcome_key};
    const workRevision=Number(db.prepare('SELECT revision FROM eva_work_items WHERE id=?')
      .get(work.workItem.id).revision);
    const episodeRevision=Number(db.prepare('SELECT revision FROM eva_goal_episodes WHERE id=?')
      .get(work.episode.id).revision);
    const predicate={contract:'ArtifactCollectionPredicate/v1',
      kind:'explicit_snapshot_member_classification',included:'current_artifact_version',
      excluded:'bounded_reason_code'};
    const versionRef=sealed.versions[0].versionRef;
    const classifications=snapshot.members.map((member)=>({
      path:member.path,classification:member.path===logicalPath?'included':'excluded',
      reason:{contract:'ArtifactCollectionMemberReason/v1',
        code:member.path===logicalPath?'current_artifact_version':'not_artifact'},
      versionRef:member.path===logicalPath?versionRef:null
    }));
    const collectionContext=Object.freeze({});
    hostAuthorities.set(collectionContext,{
      contract:'ArtifactCollectionAuthority/v1',workItemId:Number(work.workItem.id),
      episodeId:Number(work.episode.id),originId:origin.id,
      expectedWorkRevision:workRevision,expectedEpisodeRevision:episodeRevision,
      expectedOriginSha256:hashJson(origin),originSetSha256:hashJson(origins),
      originTimelineId:timeline.id,originTimelineSha256:hashJson(timeline),
      supersessionScope:ingressScope,supersessionToken:ingressToken,
      supersessionSha256:hashJson({supersessionScope:ingressScope,
        supersessionToken:ingressToken}),snapshotRef:snapshot.snapshotRef,
      predicate,classifications
    });
    const collection=store.createCollection(store.issueCollectionCapability(collectionContext),{
      operationKey:'persistent-collection'
    });
    const current=store.collectionCurrentReceipt(collection.collectionRef,{
      operationKey:'persistent-current-check'
    });
    process.stdout.write(JSON.stringify({collectionRef:collection.collectionRef,current}));
    db.close();
  `;
  try {
    const first = spawnSync(process.execPath, ['--input-type=module', '-e', firstSource], {
      cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
    });
    assert.equal(first.status, 0, first.stderr || first.stdout);
    const created = JSON.parse(first.stdout);
    assert.equal(created.current.current, false);
    assert.equal(created.current.cas, null);
    const secondSource = `
      process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
      await import('./src/mind/portfolio.js');
      const {db}=await import('./src/db.js');
      const {createArtifactCollectionStore}=await import('./src/mind/artifactcollections.js');
      db.exec('VACUUM');
      const store=createArtifactCollectionStore(db);
      store.registerAuthorityVerifier(()=>null);
      const historical=store.collection(${JSON.stringify(created.collectionRef)});
      const current=store.collectionCurrentReceipt(historical.collectionRef,{
        operationKey:'persistent-current-check'
      });
      const capability=store.issueCollectionCasCapability(Object.freeze({}));
      const consumptions=db.prepare(
        'SELECT count(*) AS count FROM main.eva_artifact_collection_consumptions'
      ).get().count;
      process.stdout.write(JSON.stringify({historical,current,capability,consumptions}));
      db.close();
    `;
    const second = spawnSync(process.execPath, ['--input-type=module', '-e', secondSource], {
      cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
    });
    assert.equal(second.status, 0, second.stderr || second.stdout);
    const reopened = JSON.parse(second.stdout);
    assert.equal(reopened.historical.collectionRef, created.collectionRef);
    assert.deepEqual(reopened.current, created.current);
    assert.equal(reopened.capability, null);
    assert.equal(reopened.consumptions, 0);
    const thirdSource = `
      process.env.EVA_DATA_DIR=${JSON.stringify(isolated)};
      await import('./src/mind/portfolio.js');
      const {db}=await import('./src/db.js');
      const {createArtifactCollectionStore}=await import('./src/mind/artifactcollections.js');
      db.exec('VACUUM');
      const store=createArtifactCollectionStore(db);
      store.registerAuthorityVerifier(()=>null);
      const historical=store.collection(${JSON.stringify(created.collectionRef)});
      const current=store.collectionCurrentReceipt(historical.collectionRef,{
        operationKey:'persistent-current-check'
      });
      const rows=db.prepare(
        'SELECT receipt_json FROM main.eva_artifact_collection_consumptions ORDER BY consumed_ts'
      ).all().map((row)=>JSON.parse(row.receipt_json));
      process.stdout.write(JSON.stringify({historical,current,rows,status:store.status()}));
      db.close();
    `;
    const third = spawnSync(process.execPath, ['--input-type=module', '-e', thirdSource], {
      cwd: path.resolve('.'), encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: isolated },
    });
    assert.equal(third.status, 0, third.stderr || third.stdout);
    const replayed = JSON.parse(third.stdout);
    assert.deepEqual(replayed.status, { ready: true, reason: null });
    assert.equal(replayed.historical.collectionRef, created.collectionRef);
    assert.deepEqual(replayed.current, created.current);
    assert.deepEqual(replayed.rows, []);
  } finally {
    fs.rmSync(isolated, { recursive: true, force: true });
  }
});

test('alle scannerlimieten zijn hard begrensd en UTF-8 wordt in bytes geteld', (t) => {
  const fixture = makeFixture(t, { maxDirectories: 2, maxBytes: 4 });
  assert.throws(() => createArtifactCollectionStore(db, {
    maxFiles: 129,
  }), /maxFiles must be between 1 and 128/u);
  assert.throws(() => createArtifactCollectionStore(db, {
    maxFiles: '1',
  }), /maxFiles must be between 1 and 128/u);
  writeWorkspace(fixture, 'utf8.txt', 'ééé');
  assert.throws(() => fixture.store.inspectWorkspace(), /byte_limit/u);
  fs.unlinkSync(path.join(fixture.workspaceRoot, 'utf8.txt'));
  fs.mkdirSync(path.join(fixture.workspaceRoot, 'a'));
  fs.mkdirSync(path.join(fixture.workspaceRoot, 'a/b'));
  assert.throws(() => fixture.store.inspectWorkspace(), /directory_limit/u);
});
