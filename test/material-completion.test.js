import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-material-completion-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  claimTurnToolExecution, completeTurnToolExecution, db,
} = await import('../src/db.js');
const {
  durableMaterialProof,
  fixedMaterialIncompleteReply,
  materialCompletionObligation,
  materialRequestSentinel,
  sealMaterialTargetManifest,
} = await import('../src/mind/materialcompletion.js');
const { executeTool } = await import('../src/mind/tools.js');
const { canonicalJson } = await import('../src/util.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
let sequence = 0;

function span(text = 'opdracht') {
  return [{ start: 0, end: text.length, text, sha256: sha256(text) }];
}

function action(requirementId, capabilityId, operationId, effect, overrides = {}) {
  return {
    requirementId, capabilityId, operationId, effect,
    polarity: 'request', authorization: 'allowed', sourceSpans: span(requirementId),
    boundaryReasons: [], ...overrides,
  };
}

function planFor(item, args = {}) {
  return {
    requirementId: item.requirementId,
    capabilityId: item.capabilityId,
    operationId: item.operationId,
    effect: item.effect,
    toolName: item.capabilityId === 'workspace.file.write' ? 'schrijf_bestand'
      : item.capabilityId === 'workspace.file.read' ? 'lees_bestand'
        : item.capabilityId === 'workspace.file.list' ? 'lijst_bestanden' : 'probe',
    args,
  };
}

function understandingFixture({
  sourceText = 'Werk deze drie concrete resultaten af.', actions = [], selected = [],
  conditions = [], requestedEffects = ['read', 'workspace'], record = false,
} = {}) {
  const sourceSha256 = sha256(`understanding:${sourceText}:${JSON.stringify(actions)}`);
  const common = {
    conditions,
    temporal: { mode: 'now', notBefore: null, dueBy: null, condition: null },
    requestedEffects,
    deniedEffects: [],
    actions,
    firstDecision: { kind: 'act', requirementIds: selected },
  };
  if (record) return {
    contract: 'TurnUnderstandingRecord/v1',
    sha256: sha256(`record:${sourceSha256}`),
    sourceSha256,
    sourceKind: 'model',
    sourceTextSha256: sha256(sourceText),
    ...common,
  };
  return {
    sha256: sourceSha256,
    source: { kind: 'model', origin: 'user', textSha256: sha256(sourceText) },
    ...common,
  };
}

function obligationFixture({ count = 3, sourceText, conditions = [] } = {}) {
  const text = sourceText || 'Werk deze drie concrete resultaten af.';
  const actions = Array.from({ length: count }, (_, index) => action(
    `r${index + 1}`, 'workspace.file.write', 'write', 'workspace',
  ));
  const understanding = understandingFixture({
    sourceText: text, actions, selected: actions.map((item) => item.requirementId), conditions,
  });
  const plans = actions.map((item, index) => planFor(item, {
    pad: `data/workspace/resultaat-${index + 1}.md`,
    inhoud: `# Resultaat ${index + 1}\n\nControleerbaar bewijs.\n`,
  }));
  return {
    sourceText: text, understanding, plans,
    obligation: materialCompletionObligation({
      understanding, plans, sourceText: text, trusted: true, synthetic: false,
    }),
  };
}

function binding(obligation, requirementId, capabilityId, operationId) {
  return {
    understandingSha256: obligation.understandingSha256,
    requirementId,
    addressesRequirementIds: [requirementId],
    basisReceiptIds: [],
    capabilityId,
    operationId,
    role: 'proof',
    executionType: 'direct',
  };
}

function actionResult(tool, {
  changed = false, data = null, artifactReceipt = null,
  partial = false, needsFollowup = false, ok = true, verified = true,
  fileManifest = null,
} = {}) {
  return JSON.stringify({
    contract: 'ActionResult/v1', tool, ok, changed, verified,
    evidence: { source: tool, summary: `${tool} geverifieerd` },
    needs_followup: needsFollowup,
    partial,
    risk: 'normal',
    human_message: '',
    ...(artifactReceipt ? { artifact_receipt: artifactReceipt } : {}),
    ...(fileManifest ? { file_manifest: fileManifest } : {}),
    data,
  });
}

function completeExecution({ turnId, tool, args, semanticBinding, output }) {
  sequence += 1;
  const executionKey = `material-proof-${sequence}`;
  assert.equal(claimTurnToolExecution({
    executionKey, turnId, tool, args, semanticBinding,
  }).claimed, true);
  assert.equal(completeTurnToolExecution(executionKey, output).completed, true);
  return db.prepare(`SELECT execution_seq AS rowid,* FROM main.turn_tool_executions
    WHERE execution_key=?`)
    .get(executionKey);
}

function writeReadRows(obligation, target, turnId, overrides = {}) {
  const plan = obligation.selectedWrites.find((item) => item.requirementId === target);
  const content = overrides.content ?? `# ${target}\n\nEcht bewijs voor ${target}.\n`;
  const bytes = Buffer.byteLength(content, 'utf8');
  const materialSha256 = overrides.materialSha256 || sha256(content);
  const writeRequirementId = overrides.writeRequirementId || target;
  const readRequirementId = overrides.readRequirementId || target;
  const writePath = overrides.writePath || plan.canonicalPath;
  const readPath = overrides.readPath || plan.canonicalPath;
  const writeAddresses = overrides.writeAddresses || [writeRequirementId];
  const readAddresses = overrides.readAddresses || [readRequirementId];
  const writeBinding = {
    ...binding(obligation, writeRequirementId, 'workspace.file.write', 'write'),
    addressesRequirementIds: writeAddresses,
  };
  const readBinding = {
    ...binding(obligation, readRequirementId, 'workspace.file.read', 'read'),
    addressesRequirementIds: readAddresses,
  };
  const write = completeExecution({
    turnId, tool: 'schrijf_bestand', args: { pad: writePath, inhoud: content },
    semanticBinding: writeBinding,
    output: actionResult('schrijf_bestand', {
      changed: overrides.changed ?? true,
      data: { path: writePath, bytes, material_sha256: materialSha256 },
    }),
  });
  const read = completeExecution({
    turnId, tool: 'lees_bestand', args: { pad: readPath }, semanticBinding: readBinding,
    output: actionResult('lees_bestand', {
      data: content,
      artifactReceipt: {
        path: readPath, bytes, source_bytes: bytes,
        sha256: overrides.readSha256 || materialSha256,
        complete_read: overrides.completeRead ?? true,
      },
      partial: overrides.partial ?? false,
    }),
  });
  if (overrides.reverseOrder) return [{ ...write, rowid: read.rowid + 1 }, read];
  return [write, read];
}

function workspaceManifest(paths, overrides = {}) {
  const sortedPaths = [...paths].sort();
  const body = {
    contract: 'WorkspaceFileManifest/v1',
    root: 'data/workspace',
    query: 'resultaat',
    matchKind: 'filename',
    paths: sortedPaths,
    complete: overrides.complete ?? true,
    truncated: overrides.truncated ?? false,
    generated: 'pre_effect',
    snapshotSha256: overrides.snapshotSha256 || sha256(canonicalJson({
      root: 'data/workspace', query: 'resultaat', paths: sortedPaths,
    })),
  };
  return { ...body, sha256: overrides.sha256 || sha256(canonicalJson(body)) };
}

function discoveryRow(obligation, turnId, paths, overrides = {}) {
  const requirementId = overrides.requirementId || obligation.discoveryRequirementIds[0];
  const fileManifest = overrides.manifest || workspaceManifest(paths, overrides.fileManifest);
  return completeExecution({
    turnId,
    tool: 'lijst_bestanden',
    args: { pad: 'data/workspace', zoek: 'resultaat' },
    semanticBinding: binding(obligation, requirementId, 'workspace.file.list', 'list'),
    output: actionResult('lijst_bestanden', {
      data: overrides.data || paths,
      fileManifest,
      partial: overrides.partial ?? false,
      needsFollowup: overrides.needsFollowup ?? false,
    }),
  });
}

test('een kwalitatieve eigen-keuzezin blokkeert een exact geschreven werkstuk niet', () => {
  const { obligation } = obligationFixture({
    count: 1,
    sourceText: 'Kies zelf iets bruikbaars en maak de eerste complete versie.',
    conditions: [{
      statement: 'Kies zelf iets bruikbaars.', role: 'action', state: 'unmet',
      requirementIds: [], afterRequirementIds: [],
    }],
  });
  assert.equal(obligation.scopeUnknown, false);
  assert.deepEqual(obligation.activeConditions, []);
  const turnId = 'material:qualitative-choice';
  const manifest = sealMaterialTargetManifest({ obligation, turnId, scopeComplete: true });
  assert.ok(manifest);
  assert.equal(durableMaterialProof({
    obligation,
    executionRows: writeReadRows(obligation, 'r1', turnId),
    manifest,
  }).complete, true);
});

test('historische productievorm selecteert reads maar geeft deferred writes nooit authority', () => {
  const sourceText = 'Controleer elk zichtbaar inkomstenkans-resultaat, corrigeer claims en maak één interne bewijsstap af.';
  const read = action('r1', 'workspace.file.read', 'read', 'read');
  const history = action('r2', 'conversation.history.read', 'read', 'read');
  const deferred = ['r3', 'r4'].map((requirementId) => action(
    requirementId, 'workspace.file.write', 'write', 'workspace', {
      authorization: 'denied',
      boundaryReasons: ['conditional', 'inspection_only'],
    },
  ));
  const understanding = {
    ...understandingFixture({
      sourceText, actions: [read, history, ...deferred], selected: ['r1', 'r2'],
      conditions: [{
        statement: 'Corrigeer elk resultaat.', role: 'action', state: 'unmet',
        requirementIds: [], afterRequirementIds: ['r1'],
      }],
      record: true,
    }),
    firstDecision: { kind: 'inspect', requirementIds: ['r1', 'r2'] },
    sha256: 'e7c6e59b153cc284b9540bdf0ad11dec29f04b56c7c750347',
    sourceSha256: '838537582f4212a1d1be750db00755b90db71b8783cc802daca8352a89c85125',
  };
  const plans = [planFor(read, { pad: 'data/workspace/inkomstenkans-start.md' }),
    planFor(history, { query: 'inkomstenkans' })];
  const obligation = materialCompletionObligation({
    understanding, plans, sourceText, trusted: true, synthetic: false,
  });

  assert.equal(obligation.required, true);
  assert.equal(obligation.scopeUnknown, true);
  assert.deepEqual(obligation.selectedRequirementIds, ['r1', 'r2']);
  assert.deepEqual(obligation.deferredRequirementIds, ['r3', 'r4']);
  assert.equal(durableMaterialProof({ obligation, executionRows: [] }).complete, false);

  for (const hadVerifiedRead of [false, true]) {
    for (const handoffVerified of [false, true]) {
      const reply = fixedMaterialIncompleteReply({ hadVerifiedRead, handoffVerified });
      assert.doesNotMatch(reply, /\b(?:gemarkeerd|gecorrigeerd|klaar)\b/iu);
      if (hadVerifiedRead) assert.match(reply, /bronnen bekeken/iu);
      else assert.doesNotMatch(reply, /bronnen bekeken/iu);
      if (handoffVerified) assert.match(reply, /open werk/iu);
      else assert.doesNotMatch(reply, /open werk/iu);
    }
  }
});

test('host-sentinel blokkeert een modelreply die alle materiaalacties weglaat', () => {
  const sourceText = 'Corrigeer data/workspace/resultaat.md en sla het controleerbaar op.';
  const understanding = understandingFixture({ sourceText, actions: [], selected: [] });
  understanding.firstDecision = {
    kind: 'reply', requirementIds: [], replyDraft: 'Ik heb het bestand gecorrigeerd.',
  };
  const sentinel = materialRequestSentinel({
    sourceText, trusted: true, synthetic: false,
  });
  const obligation = materialCompletionObligation({
    understanding, plans: [], sourceText, requestSentinel: sentinel,
    trusted: true, synthetic: false,
  });
  assert.equal(obligation.required, true);
  assert.equal(obligation.sentinelRequired, true);
  assert.deepEqual(obligation.selectedWrites, []);
  assert.equal(sealMaterialTargetManifest({
    obligation, turnId: 'sentinel:no-model-authority', scopeComplete: true,
  }), null);

  assert.equal(materialCompletionObligation({
    understanding, plans: [], sourceText, requestSentinel: structuredClone(sentinel),
    trusted: true, synthetic: false,
  }).required, false, 'een geserialiseerde sentinel verleent zelfs geen blokkeringsauthority');
  assert.equal(materialRequestSentinel({
    sourceText: 'Welke bestanden zijn er?', trusted: true, synthetic: false,
  }).required, false);
  assert.equal(materialRequestSentinel({
    sourceText: 'Als jij zelf iets kiest, wat vind ik dan belangrijk aan een afgemaakt resultaat? Voer niets uit en sla niets nieuws op.',
    trusted: true, synthetic: false,
  }).required, false, 'praten over een afgemaakt resultaat is geen claim dat Eva iets heeft uitgevoerd');
  for (const request of [
    'Corrigeer alle inkomstenartefacten.',
    'Rond het inkomstenwerk af en sla het resultaat op.',
    'Bewerk het verkoopwerk en vul het interne overzicht aan.',
    'Creëer data/workspace/offerte.txt en lever het op.',
    'Genereer data/workspace/rapport.txt.',
    'Bouw en exporteer het document naar data/workspace/resultaat.txt.',
    'Kopieer data/workspace/bron.txt naar data/workspace/doel.txt.',
  ]) {
    assert.equal(materialRequestSentinel({
      sourceText: request, trusted: true, synthetic: false,
    }).required, true, request);
  }
});

test('een expliciete work-afronding is geen artifactmutatie maar verbergt er ook geen', () => {
  assert.equal(materialRequestSentinel({
    sourceText: 'Lees data/workspace/verkoopteksten-bewijs.txt terug en rond werk #17 af met het gecontroleerde resultaat.',
    synthetic: false,
  }).required, false);
  assert.equal(materialRequestSentinel({
    sourceText: 'Corrigeer data/workspace/verkoopteksten-bewijs.txt en rond het werk #17 af.',
    synthetic: false,
  }).required, true);
});

test('host-sentinel bindt alle expliciete workspacepaden en weigert een deelplan', () => {
  const sourceText = 'Maak data/workspace/a.md en data/workspace/b.md.';
  const actions = [
    action('r1', 'workspace.file.write', 'write', 'workspace'),
    action('r2', 'workspace.file.write', 'write', 'workspace'),
  ];
  const sentinel = materialRequestSentinel({ sourceText, synthetic: false });
  assert.deepEqual(sentinel.explicitTargetPaths, [
    'data/workspace/a.md', 'data/workspace/b.md',
  ]);
  assert.equal(sentinel.targetSetComplete, true);

  const partialUnderstanding = understandingFixture({
    sourceText, actions: actions.slice(0, 1), selected: ['r1'],
  });
  const partial = materialCompletionObligation({
    understanding: partialUnderstanding,
    plans: [planFor(actions[0], {
      pad: 'data/workspace/a.md', inhoud: '# A\n',
    })],
    sourceText,
    requestSourceText: sourceText,
    requestSentinel: sentinel,
    trusted: true,
    synthetic: false,
  });
  assert.deepEqual(partial.sentinelTargetPaths, sentinel.explicitTargetPaths);
  assert.equal(partial.sentinelTargetsMatch, false);
  assert.equal(partial.scopeUnknown, true);
  assert.equal(sealMaterialTargetManifest({
    obligation: partial, turnId: 'sentinel:partial-targets', scopeComplete: true,
  }), null);

  for (const ambiguousText of [
    'Maak data/workspace/a.md en b.md.',
    'Maak data/workspace/a.md en een notitie.',
    'Maak data/workspace/a.md en nog één.',
    'Maak data/workspace/a.md en daarnaast eentje.',
    'Maak data/workspace/a.md en plus één erbij.',
    'Maak drie bestanden: data/workspace/a.md en data/workspace/b.md.',
    'Maak data/workspace/a.md en data/workspace/../b.md.',
  ]) {
    const ambiguous = materialRequestSentinel({
      sourceText: ambiguousText, synthetic: false,
    });
    assert.equal(ambiguous.required, true, ambiguousText);
    assert.equal(ambiguous.targetSetComplete, false, ambiguousText);
  }

  const completeUnderstanding = understandingFixture({
    sourceText, actions, selected: ['r1', 'r2'],
  });
  const complete = materialCompletionObligation({
    understanding: completeUnderstanding,
    plans: actions.map((item, index) => planFor(item, {
      pad: `data/workspace/${index ? 'b' : 'a'}.md`, inhoud: '# Resultaat\n',
    })),
    sourceText,
    requestSourceText: sourceText,
    requestSentinel: sentinel,
    trusted: true,
    synthetic: false,
  });
  assert.equal(complete.sentinelTargetsMatch, true);
  assert.equal(complete.scopeUnknown, false);
  assert.ok(sealMaterialTargetManifest({
    obligation: complete, turnId: 'sentinel:all-targets', scopeComplete: true,
  }));
});

test('drie doelen zijn pas compleet na drie exacte write-read-ketens', () => {
  const { obligation } = obligationFixture();
  assert.equal(obligation.required, true);
  assert.equal(obligation.scopeUnknown, false);
  assert.equal(obligation.selectedWrites.length, 3);
  const turnId = 'material:three-targets';
  const manifest = sealMaterialTargetManifest({ obligation, turnId, scopeComplete: true });
  assert.ok(manifest);

  const twoRows = ['r1', 'r2'].flatMap((requirementId) => (
    writeReadRows(obligation, requirementId, turnId)
  ));
  const incomplete = durableMaterialProof({
    obligation, executionRows: twoRows, manifest,
  });
  assert.equal(incomplete.complete, false);
  assert.deepEqual(incomplete.missingTargets, ['r3']);
  assert.equal(Object.hasOwn(incomplete, 'artifacts'), false);
  assert.equal(Object.hasOwn(incomplete, 'proofExecutionKeys'), false);

  const allRows = [...twoRows, ...writeReadRows(obligation, 'r3', turnId)];
  const complete = durableMaterialProof({ obligation, executionRows: allRows, manifest });
  assert.equal(complete.complete, true, JSON.stringify(complete));
  assert.equal(complete.artifacts.length, 3);
  assert.deepEqual(complete.artifacts.map((item) => item.requirementId), ['r1', 'r2', 'r3']);
  const rowByExecutionKey = new Map(allRows.map((row) => [row.execution_key, row]));
  assert.ok(complete.artifacts.every((item) => (
    item.role === 'result'
      && item.sha256 === sha256(item.content)
      && item.bytes === Buffer.byteLength(item.content, 'utf8')
      && item.understandingSha256 === obligation.understandingSha256
      && item.writeCompletionSha256
        === rowByExecutionKey.get(item.writeExecutionKey)?.completion_record_sha256
      && item.readCompletionSha256
        === rowByExecutionKey.get(item.readExecutionKey)?.completion_record_sha256
      && item.runExecutions.length === 0
  )));
  assert.deepEqual(complete.proofExecutionKeys,
    [...complete.proofExecutionKeys].sort((left, right) => left.localeCompare(right)));
});

test('uitvoerbare doelen claimen zonder gebonden runbewijs geen late voltooiing', () => {
  const fixture = obligationFixture({ count: 1 });
  const executablePlan = fixture.plans[0];
  executablePlan.args.pad = 'data/workspace/resultaat-1.js';
  const obligation = materialCompletionObligation({
    understanding: fixture.understanding,
    plans: fixture.plans,
    sourceText: fixture.sourceText,
    trusted: true,
    synthetic: false,
  });
  const turnId = 'negative:executable-without-run';
  const manifest = sealMaterialTargetManifest({ obligation, turnId, scopeComplete: true });
  const proof = durableMaterialProof({
    obligation,
    executionRows: writeReadRows(obligation, 'r1', turnId),
    manifest,
  });
  assert.equal(proof.complete, false);
  assert.deepEqual(proof.missingTargets, ['r1']);
  assert.equal(proof.unmetState.ledgerReason, 'executable_run_proof_missing');
  assert.equal(Object.hasOwn(proof, 'artifacts'), false);
});

test('verkeerde sha, requirement, addresses, pad, partial, volgorde en no-op falen dicht', async (t) => {
  const cases = [
    ['verkeerde materiaalhash', { materialSha256: 'a'.repeat(64) }],
    ['verkeerd requirement', { readRequirementId: 'r2' }],
    ['verkeerde addresses', { readAddresses: ['r1', 'r2'] }],
    ['verkeerd pad', { readPath: 'data/workspace/ander.md' }],
    ['partiele read', { partial: true }],
    ['read staat niet later', { reverseOrder: true }],
    ['no-op write', { changed: false }],
  ];
  for (const [label, overrides] of cases) {
    await t.test(label, () => {
      const { obligation } = obligationFixture({ count: 1 });
      const turnId = `negative:${label}`;
      const manifest = sealMaterialTargetManifest({ obligation, turnId, scopeComplete: true });
      const rows = writeReadRows(obligation, 'r1', turnId, overrides);
      const proof = durableMaterialProof({ obligation, executionRows: rows, manifest });
      assert.equal(proof.complete, false, JSON.stringify(proof));
      assert.equal(Object.hasOwn(proof, 'artifacts'), false);
    });
  }
});

test('niet-canonieke of beschadigde duurzame rijen worden niet gehydrateerd', () => {
  const { obligation } = obligationFixture({ count: 1 });
  const turnId = 'negative:ledger-integrity';
  const manifest = sealMaterialTargetManifest({ obligation, turnId, scopeComplete: true });
  const rows = writeReadRows(obligation, 'r1', turnId);
  const parsedArgs = JSON.parse(rows[0].args);
  const malformedIntent = [{
    ...rows[0],
    args: JSON.stringify(Object.fromEntries(Object.entries(parsedArgs).reverse())),
  }, rows[1]];
  assert.equal(durableMaterialProof({
    obligation, executionRows: malformedIntent, manifest,
  }).complete, false);
  const badCompletion = [{ ...rows[0], completion_record_sha256: 'b'.repeat(64) }, rows[1]];
  assert.equal(durableMaterialProof({
    obligation, executionRows: badCompletion, manifest,
  }).complete, false);
});

test('gebonden completionvoorwaarden blijven blockers; losse keuzezinnen niet', () => {
  const linked = {
    statement: 'Rond r1 af na r2.', role: 'completion', state: 'active',
    requirementIds: ['r1'], afterRequirementIds: ['r2'],
  };
  const empty = {
    statement: 'Rond alles af.', role: 'action', state: 'active',
    requirementIds: [], afterRequirementIds: [],
  };
  const fixture = obligationFixture({ count: 2, conditions: [linked] });
  const turnId = 'conditions:linked';
  const manifest = sealMaterialTargetManifest({
    obligation: fixture.obligation, turnId, scopeComplete: true,
  });
  const onlyR1 = writeReadRows(fixture.obligation, 'r1', turnId);
  assert.equal(durableMaterialProof({
    obligation: fixture.obligation, executionRows: onlyR1, manifest,
  }).complete, false);
  const both = [...onlyR1, ...writeReadRows(fixture.obligation, 'r2', turnId)];
  assert.equal(durableMaterialProof({
    obligation: fixture.obligation, executionRows: both, manifest,
  }).complete, true);

  const withEmpty = obligationFixture({ count: 1, conditions: [empty] });
  const emptyTurn = 'conditions:empty';
  const emptyManifest = sealMaterialTargetManifest({
    obligation: withEmpty.obligation, turnId: emptyTurn, scopeComplete: true,
  });
  assert.ok(emptyManifest);
  assert.equal(durableMaterialProof({
    obligation: withEmpty.obligation,
    executionRows: writeReadRows(withEmpty.obligation, 'r1', emptyTurn),
    manifest: emptyManifest,
  }).complete, true);
});

test('modelgekozen zoekscope en losse bronpaden bewijzen nooit alle', async () => {
  const text = 'Corrigeer elk zichtbaar resultaat.';
  const write = action('r1', 'workspace.file.write', 'write', 'workspace');
  const list = action('r2', 'workspace.file.list', 'list', 'read');
  const understanding = understandingFixture({
    sourceText: text, actions: [list, write], selected: ['r2', 'r1'],
  });
  const plans = [
    planFor(list, { pad: 'data/workspace', zoek: 'resultaat' }),
    planFor(write, { pad: 'data/workspace/resultaat-1.md', inhoud: '# Resultaat\n' }),
  ];
  const obligation = materialCompletionObligation({
    understanding, plans, sourceText: text, trusted: true, synthetic: false,
  });
  assert.equal(obligation.scopeUnknown, true);
  assert.equal(sealMaterialTargetManifest({
    obligation, turnId: 'universal:no-discovery', scopeComplete: true,
  }), null);

  const turnId = 'universal:complete-discovery';
  const paths = obligation.selectedWrites.map((item) => item.canonicalPath);
  fs.writeFileSync(path.join(dataDir, 'workspace', 'resultaat-1.md'), '# Vooraf\n');
  const listing = JSON.parse(await executeTool('lijst_bestanden', {
    pad: 'data/workspace', zoek: 'resultaat',
  }, { mode: 'chat', messageContext: { syntheticTest: true } }));
  const discovery = discoveryRow(obligation, turnId, paths, {
    manifest: listing.file_manifest, data: listing.data,
  });
  const manifest = sealMaterialTargetManifest({
    obligation, turnId, scopeComplete: true, discoveryRow: discovery,
  });
  assert.equal(manifest, null,
    'een complete lexicale zoekmanifest is geen semantische collectie-authority');

  const explicitText = 'Corrigeer alle data/workspace/resultaat-1.md.';
  const explicitUnderstanding = understandingFixture({
    sourceText: explicitText, actions: [write], selected: ['r1'],
  });
  const explicitPlans = [planFor(write, {
    pad: 'data/workspace/resultaat-1.md', inhoud: '# Resultaat\n',
  })];
  const explicitObligation = materialCompletionObligation({
    understanding: explicitUnderstanding,
    plans: explicitPlans,
    sourceText: explicitText,
    trusted: true,
    synthetic: false,
  });
  assert.equal(explicitObligation.universalScopeAuthority, null);
  assert.equal(explicitObligation.scopeUnknown, true);
  assert.equal(sealMaterialTargetManifest({
    obligation: explicitObligation,
    turnId: 'universal:explicit-source-paths', scopeComplete: true,
  }), null);

  const misleadingText = 'Corrigeer alle drie inkomstenartefacten: data/workspace/a.md en data/workspace/b.md.';
  const misleadingActions = ['a', 'b'].map((name, index) => action(
    `r${index + 1}`, 'workspace.file.write', 'write', 'workspace',
  ));
  const misleadingUnderstanding = understandingFixture({
    sourceText: misleadingText,
    actions: misleadingActions,
    selected: ['r1', 'r2'],
  });
  const misleadingPlans = misleadingActions.map((item, index) => planFor(item, {
    pad: `data/workspace/${index ? 'b' : 'a'}.md`, inhoud: '# Resultaat\n',
  }));
  const misleading = materialCompletionObligation({
    understanding: misleadingUnderstanding,
    plans: misleadingPlans,
    sourceText: misleadingText,
    trusted: true,
    synthetic: false,
  });
  assert.equal(misleading.scopeUnknown, true);
  assert.equal(sealMaterialTargetManifest({
    obligation: misleading, turnId: 'universal:missing-third', scopeComplete: true,
  }), null);
});

test('manifest is host-uitgegeven, exact verzegeld en niet overdraagbaar via clone', () => {
  const { obligation } = obligationFixture({ count: 1 });
  const turnId = 'manifest:host-owned';
  const manifest = sealMaterialTargetManifest({ obligation, turnId, scopeComplete: true });
  const rows = writeReadRows(obligation, 'r1', turnId);
  assert.equal(durableMaterialProof({ obligation, executionRows: rows, manifest }).complete, true);
  assert.equal(durableMaterialProof({
    obligation, executionRows: rows, manifest: structuredClone(manifest),
  }).complete, false);
  assert.equal(durableMaterialProof({
    obligation, executionRows: rows,
    manifest: { ...manifest, sha256: '0'.repeat(64) },
  }).complete, false);
});
