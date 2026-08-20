import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-tool-policy-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  __toolsTest,
  TOOL_EFFECT,
  toolAllowedByPolicy,
  toolChangesByDefault,
  toolEffect,
  bindCapability,
  bindCapabilityInput,
  bindExecutionHandle,
  bindHomeContextRecoveryPlan,
  bindSignalAssessmentPlan,
  capabilityCatalog,
  executionHandlesForPlan,
  executionGroupConsumptions,
  executionObligationForPlan,
  executeTool,
  isIssuedExecutionProofReadback,
  planCapabilityAction,
  registerMessenger,
  registerPhotoSender,
  resolveCapability,
  sendRegisteredPhoto,
  sendRegisteredMessage,
  TOOL_REGISTRY,
  TOOLS,
  validateToolRegistry,
} = await import('../src/mind/tools.js');
const { createSignalBatch } = await import('../src/mind/signals.js');
const { understandTurn } = await import('../src/mind/understanding.js');
const {
  bindHomeContextEntityReferences,
  homeContextText,
} = await import('../src/mind/home.js');
const { db, upsertPerson } = await import('../src/db.js');
const { createWorkItem, updateWorkItem } = await import('../src/mind/work.js');
const { createDurableWork } = await import('../src/mind/portfolio.js');
const { startExploration, updateExploration } = await import('../src/mind/curiosity.js');
const { householdMembers, setPersonHouseholdRole } = await import('../src/mind/household.js');
const { listIntegrationInventory } = await import('../src/integrations/homeassistant.js');
const { registerSensitiveValues } = await import('../src/security/redaction.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('centrale metadata dekt iedere kerntool exact eenmaal', () => {
  const defined = TOOLS.map((tool) => tool.name).sort();
  const registered = TOOL_REGISTRY.map((tool) => tool.name).sort();
  assert.equal(TOOL_REGISTRY.length, 34);
  assert.equal(new Set(registered).size, TOOL_REGISTRY.length);
  assert.deepEqual(registered, defined);
  for (const descriptor of TOOL_REGISTRY) {
    assert.equal(typeof descriptor.description, 'string', descriptor.name);
    assert.equal(descriptor.parameters?.type, 'object', descriptor.name);
    assert.equal(typeof descriptor.policy, 'object', descriptor.name);
    assert.equal(typeof descriptor.handler, 'function', descriptor.name);
    assert.ok(descriptor.provides.length > 0, descriptor.name);
  }
  for (const { name, policy } of TOOL_REGISTRY) {
    assert.ok(['read', 'home', 'voice', 'external', 'workspace', 'persistent', 'runtime', 'mixed'].includes(policy.effect), name);
    assert.ok(['allow', 'isolated', 'deny'].includes(policy.synthetic), name);
    assert.equal(typeof policy.report, 'string', name);
    assert.equal(typeof policy.durable, 'boolean', name);
    assert.equal(typeof policy.changesByDefault, 'boolean', name);
    assert.ok(['bounded', 'full', 'none'].includes(policy.autonomy), name);
    assert.ok(Array.isArray(policy.tags), name);
    if (policy.verification) assert.ok(['explicit', 'ok'].includes(policy.verification), name);
    if (policy.proofRank != null) assert.ok(Number(policy.proofRank) >= 1, name);
    for (const tag of policy.tags) {
      assert.match(tag, /^(?:discovery|house|free|delegated|grounding|receipt:conversation|lane:(?:house|research|workspace|family)|primary:(?:house|research|workspace|family)|contribution:(?:house|research|making|contact)|proof:(?:house|project)|reasoning:(?:code|mechanical))$/, `${name}:${tag}`);
      if (tag.startsWith('primary:')) assert.ok(policy.tags.includes(tag.replace('primary:', 'lane:')), `${name}:${tag}`);
    }
  }
  assert.equal(validateToolRegistry(TOOL_REGISTRY), true);
});

test('partial en needs-followup receipts zijn geen grounding of heropeningsbewijs', async () => {
  const newEvidence = 'Nieuwe lokale schoonmaakhulp prijs is aantoonbaar lager.';
  const completeProof = {
    name: 'zoek_internet', args: { vraag: newEvidence },
    output: `Bron bevestigt: ${newEvidence}`,
    ok: true, verified: true, partial: false, needs_followup: false,
  };
  assert.equal(__toolsTest.evidenceMatches(newEvidence, completeProof), true);
  assert.equal(__toolsTest.evidenceMatches(newEvidence, {
    ...completeProof, partial: true,
  }), false);
  assert.equal(__toolsTest.evidenceMatches(newEvidence, {
    ...completeProof, needs_followup: true,
  }), false);

  const marker = `tool-proof-quarantine-${Date.now()}`;
  const identity = {
    topicKey: marker,
    topicAnchors: [`artifact:${marker}`, 'constraint:lokale-prijs'],
  };
  const prior = startExploration({
    ...identity,
    question: 'Is lokale schoonmaakhulp onder de eerdere prijsdrempel beschikbaar?',
    whyNow: 'De eerdere marktcontrole legde een concrete prijsdrempel vast.',
    hypothesis: 'Er is geen aanbieder onder de eerdere prijsdrempel.',
    expectedObservation: 'Actuele aanbieders tonen prijzen boven de drempel.',
    stopRule: 'Stop na één actuele prijsvergelijking met bron.',
    source: 'self',
  }).exploration;
  updateExploration(prior.id, {
    status: 'completed', result: 'Geen lagere prijs gevonden.',
    decision: 'Niet heropenen zonder nieuw geverifieerd prijsbewijs.',
  });
  const reopenArgs = {
    bewerking: 'start',
    vraag: 'Is lokale schoonmaakhulp nu aantoonbaar onder de prijsdrempel beschikbaar?',
    aanleiding: 'Een nieuwe marktwaarneming kan de eerdere drempel veranderen.',
    hypothese: 'Een actuele aanbieder ligt nu onder de eerdere prijsdrempel.',
    verwachte_waarneming: 'Een actuele bron noemt een aantoonbaar lagere prijs.',
    stopregel: 'Stop na één actuele prijsvergelijking met bron.',
    max_acties: 2,
    onderwerp_sleutel: marker,
    onderwerp_ankers: identity.topicAnchors,
    eerder_besluit_id: prior.id,
    nieuw_bewijs: newEvidence,
  };
  try {
    for (const poisonedProof of [
      { ...completeProof, partial: true },
      { ...completeProof, needs_followup: true },
    ]) {
      const rejected = JSON.parse(await executeTool('verkenning_bijhouden', reopenArgs, {
        mode: 'autonomy', priorToolResults: [poisonedProof],
      }));
      assert.equal(rejected.ok, false);
      assert.equal(rejected.changed, false);
      assert.equal(db.prepare('SELECT count(*) AS count FROM eva_explorations WHERE topic_key=?')
        .get(marker).count, 1);
    }
    const reopened = JSON.parse(await executeTool('verkenning_bijhouden', reopenArgs, {
      mode: 'autonomy', priorToolResults: [completeProof],
    }));
    assert.equal(reopened.ok, true);
    assert.equal(reopened.changed, true);
    assert.equal(db.prepare('SELECT count(*) AS count FROM eva_explorations WHERE topic_key=?')
      .get(marker).count, 2);
  } finally {
    db.prepare("DELETE FROM eva_explorations WHERE topic_key=? AND source='self'").run(marker);
  }
});

test('capabilityresolver bindt semantische input en handhaaft verzegelde authority', () => {
  assert.deepEqual(resolveCapability('home.device.control', { operation: 'set' }), []);
  for (const forged of [
    { mode: 'chat', level: 'root_approved' },
    { sender: 'jaap', identityTrusted: true, isCurrent: () => true },
    { messageContext: { identityTrusted: true, isCurrent: () => true } },
  ]) assert.deepEqual(resolveCapability('home.device.control', { operation: 'set', authority: forged }), []);
  const control = TOOL_REGISTRY.flatMap((tool) => tool.provides)
    .find((item) => item.capability === 'home.device.control').operations[0];
  assert.equal(control.effect, TOOL_EFFECT.home);
  assert.equal(control.authority.interactive, 'explicit');
  assert.equal(control.idempotency, 'turn_scoped');
  assert.deepEqual(control.proofFloor, ['verified_effect']);
  assert.deepEqual(bindCapabilityInput('home.device.control', 'set', {
    target: 'climate.woonkamer', desiredState: 'temperature', value: 20,
  }), { doel: 'climate.woonkamer', actie: 'temperatuur', waarde: 20 });
  assert.throws(() => bindCapabilityInput('home.device.control', 'set', { target: 'lamp' }), /ontbrekend veld/u);
  assert.throws(() => bindCapabilityInput('home.device.control', 'set', {
    target: 'lamp', desiredState: 'off', extra: true,
  }), /onbekend veld/u);

  const read = resolveCapability('home.context.read', { operation: 'read' });
  assert.equal(read.length, 1);
  assert.equal(read[0].effect, TOOL_EFFECT.read);
  assert.equal(read[0].idempotency, 'repeatable');
  assert.deepEqual(read[0].proofFloor, ['source_read']);
  assert.deepEqual(read[0].bind({ query: 'lampen' }), { vraag: 'lampen' });
  assert.deepEqual(bindCapability(read[0], { query: 'deuren' }), { vraag: 'deuren' });
  assert.deepEqual(read[0].rebindableFields, []);
  assert.throws(() => read[0].bind({ query: 'lampen', path: '/api/logbook/2026-08-09' }),
    /onbekend veld/u);

  const robotRead = resolveCapability('robot.observe', { operation: 'status' });
  assert.equal(robotRead[0].effect, TOOL_EFFECT.read);
  assert.deepEqual(robotRead[0].bind({}), { actie: 'status' });
  assert.deepEqual(resolveCapability('robot.observe', { operation: 'kijk' }), []);
  const camera = resolveCapability('camera.frame.read', { operation: 'capture' });
  assert.equal(camera.length, 1);
  assert.equal(camera[0].tool.name, 'bekijk_camera');
  assert.deepEqual(camera[0].rebindableFields, []);
  assert.deepEqual(camera[0].bind({ provider: 'robot', target: 'Eriks frontcamera' }), {
    provider: 'robot', target: 'Eriks frontcamera',
  });
  assert.throws(() => camera[0].bind({ provider: 'anders', target: 'frontcamera' }), /enum/u);
  assert.deepEqual(resolveCapability('workspace.file.read', { operation: 'read' })[0]
    .rebindableFields, ['pad']);
  assert.deepEqual(resolveCapability('robot.control', { operation: 'start' }), []);
  assert.deepEqual(bindCapabilityInput('robot.control', 'start', {}), { actie: 'start' });
  assert.deepEqual(resolveCapability('identity.face.learn', { operation: 'learn' }), []);
  assert.deepEqual(resolveCapability('runtime.change.apply', { operation: 'pas_toe' }), []);
  const applyMetadata = TOOL_REGISTRY.flatMap((tool) => tool.provides)
    .find((item) => item.capability === 'runtime.change.apply').operations[0];
  assert.equal(applyMetadata.authority.interactive, 'root_approved');
  assert.equal(applyMetadata.idempotency, 'sealed_transaction');
  assert.ok(applyMetadata.proofFloor.includes('root_receipt'));

  assert.deepEqual(resolveCapability('home.context.read', { operation: 'read', mode: 'autonomy' }), []);
  assert.deepEqual(resolveCapability('bestaat.niet'), []);
});

test('SignalAssessmentReceipt gebruikt alleen een issued understanding en exact haar hele batch', async () => {
  const text = 'Beoordeel deze huissignalen nu volledig.';
  const batch = createSignalBatch([
    { id: 'sig-1', ts: '2026-08-09T10:00:00.000Z', key: 'binary_sensor.deur', type: 'entity_state', text: 'Deur open' },
    { id: 'sig-2', ts: '2026-08-09T10:00:01.000Z', key: 'sun.sun', type: 'sun', text: 'Zon op' },
  ]);
  const readActions = batch.signals.map((signal) => ({
    capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
    semanticInput: { entityId: signal.key }, sourceSpans: [{ start: 0, end: text.length }],
  }));
  const raw = {
    dialogueAct: 'command', goal: { statement: text, outcome: 'De batch is beoordeeld.', beneficiary: 'household' },
    conditions: [], provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
    confidence: 0.99, actions: readActions,
    signalAssessment: { disposition: 'no_action', assessment: 'Geen verdere actie nodig na de beoordeling.' },
    requestedEffects: ['read', 'persistent'], deniedEffects: [], temporal: { mode: 'now', startSourceSpans: [] },
    durability: 'turn', delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
    firstDecision: { kind: 'act', requirementIds: ['r1', 'r2', 'r3'], replyDraft: null },
  };
  const interpreted = await understandTurn({ text, origin: 'self', context: { signalBatch: batch } }, {
    capabilityCatalog: capabilityCatalog(),
    complete: async () => ({ toolCalls: [{ name: 'commit_turn_understanding', arguments: JSON.stringify(raw) }] }),
  });
  assert.equal(interpreted.ok, true);
  const parent = planCapabilityAction(interpreted.understanding.actions.at(-1), {
    mode: 'autonomy', understandingSha256: interpreted.understanding.sha256,
  });
  const bound = bindSignalAssessmentPlan(parent, interpreted.understanding);
  assert.ok(bound);
  assert.deepEqual(bound.args, { dispositie: 'no_action', oordeel: 'Geen verdere actie nodig na de beoordeling.' });
  const readReceipts = (understanding) => understanding.actions.filter((action) => (
    action.capabilityId === 'home.entity.read'
  )).map((action) => ({
    contract: 'ActionResult/v1', tool: 'ha_lees', name: 'ha_lees',
    args: { entity_id: action.semanticInput.entityId }, ok: true, verified: true, changed: false,
    partial: false, needs_followup: false, effect_uncertain: false,
    semanticBinding: { understandingSha256: understanding.sha256,
      requirementId: action.requirementId, capabilityId: action.capabilityId, operationId: action.operationId },
  }));
  const context = { mode: 'autonomy', messageContext: { signalBatch: batch },
    priorToolResults: readReceipts(interpreted.understanding) };
  const receipt = JSON.parse(await executeTool(bound.toolName, bound.args, context));
  assert.equal(receipt.ok, true);
  assert.equal(receipt.verified, true);
  assert.equal(receipt.data.contract, 'SignalAssessmentReceipt/v1');
  assert.deepEqual(receipt.data.acknowledgedIds, ['sig-1', 'sig-2']);
  assert.equal(receipt.data.batchSha256, batch.sha256);

  const boundAssessment = async (disposition) => {
    const result = await understandTurn({ text, origin: 'self', context: { signalBatch: batch } }, {
      capabilityCatalog: capabilityCatalog(), complete: async () => ({ toolCalls: [{
        name: 'commit_turn_understanding', arguments: JSON.stringify({ ...raw,
          signalAssessment: { disposition, assessment: 'Deze beoordeling steunt alleen op fysiek beurtbewijs.' },
        }),
      }] }),
    });
    const issuedPlan = planCapabilityAction(result.understanding.actions.at(-1), {
      mode: 'autonomy', understandingSha256: result.understanding.sha256,
    });
    return { plan: bindSignalAssessmentPlan(issuedPlan, result.understanding),
      understanding: result.understanding };
  };
  const observed = await boundAssessment('observed');
  assert.equal(JSON.parse(await executeTool(observed.plan.toolName, observed.plan.args, {
    mode: 'autonomy', messageContext: { signalBatch: batch }, priorToolResults: [],
  })).ok, false, 'vrije beoordelingstekst is geen observatiebewijs');
  assert.equal(JSON.parse(await executeTool(observed.plan.toolName, observed.plan.args, {
    mode: 'autonomy', messageContext: { signalBatch: batch },
    priorToolResults: readReceipts(observed.understanding),
  })).ok, true);
  const acted = await boundAssessment('acted');
  assert.equal(acted.understanding.actions.at(-1).semanticInput.disposition, 'observed');
  assert.deepEqual(acted.plan.args, { dispositie: 'observed',
    oordeel: 'De actuele toestand is uitsluitend via de geselecteerde uitlezingen beoordeeld; er is in deze beurt geen fysieke handeling uitgevoerd.' });
  assert.equal(JSON.parse(await executeTool(acted.plan.toolName, acted.plan.args, {
    mode: 'autonomy', messageContext: { signalBatch: batch },
    priorToolResults: readReceipts(acted.understanding),
  })).ok, true, 'read-only acted wordt vóór uitvoering een feitelijke observed-beoordeling');

  const effectfulCatalog = Object.freeze([...capabilityCatalog(), {
    id: 'home.signal.effect', operations: [{ id: 'set', effect: 'home',
      signalScope: ['entity_state:binary_sensor.*'], signalKeyField: 'entityId',
      sourceBoundFields: ['entityId'], input: {
        type: 'object', additionalProperties: false,
        properties: { entityId: { type: 'string' }, state: { type: 'string', enum: ['off'] } },
        required: ['entityId', 'state'],
      } }],
  }]);
  const effectfulRaw = { ...raw,
    actions: [...readActions, {
      capabilityId: 'home.signal.effect', operationId: 'set', polarity: 'request',
      semanticInput: { entityId: 'binary_sensor.deur', state: 'off' },
      sourceSpans: [{ start: 0, end: text.length }],
    }],
    signalAssessment: { disposition: 'acted', assessment: 'De geselecteerde handeling is uitgevoerd.' },
    requestedEffects: ['read', 'home', 'persistent'],
    firstDecision: { kind: 'act', requirementIds: ['r1', 'r2', 'r3', 'r4'], replyDraft: null },
  };
  const effectful = await understandTurn({ text, origin: 'self', context: { signalBatch: batch } }, {
    capabilityCatalog: effectfulCatalog,
    complete: async () => ({ toolCalls: [{
      name: 'commit_turn_understanding', arguments: JSON.stringify(effectfulRaw),
    }] }),
  });
  assert.equal(effectful.ok, true, JSON.stringify(effectful));
  const effectfulParent = planCapabilityAction(effectful.understanding.actions.at(-1), {
    mode: 'autonomy', understandingSha256: effectful.understanding.sha256,
  });
  const effectfulPlan = bindSignalAssessmentPlan(effectfulParent, effectful.understanding);
  const mutation = effectful.understanding.actions.find((action) => (
    action.capabilityId === 'home.signal.effect'
  ));
  const mutationReceipt = (changed) => ({
    contract: 'ActionResult/v1', tool: 'future_effect', name: 'future_effect', args: {},
    ok: true, verified: true, changed, partial: false, needs_followup: false,
    semanticBinding: { understandingSha256: effectful.understanding.sha256,
      requirementId: mutation.requirementId, capabilityId: mutation.capabilityId,
      operationId: mutation.operationId },
  });
  const unrelatedChanged = { ...mutationReceipt(true), semanticBinding: {
    understandingSha256: effectful.understanding.sha256,
    requirementId: 'unrelated', capabilityId: 'home.signal.effect', operationId: 'set',
  } };
  const effectContext = (changed) => ({ mode: 'autonomy', messageContext: { signalBatch: batch },
    priorToolResults: [...readReceipts(effectful.understanding), mutationReceipt(changed), unrelatedChanged] });
  assert.equal(JSON.parse(await executeTool(effectfulPlan.toolName, effectfulPlan.args,
    effectContext(false))).ok, false,
  'een unrelated changed=true receipt bewijst de geselecteerde effectvolle voorganger niet');
  const actedReceipt = JSON.parse(await executeTool(effectfulPlan.toolName, effectfulPlan.args,
    effectContext(true)));
  assert.equal(actedReceipt.ok, true,
    'de exact gebonden effectvolle voorganger met changed=true houdt future acted intact');
  assert.equal(actedReceipt.data.disposition, 'acted');
  assert.equal(actedReceipt.data.assessment,
    'Een exact geselecteerde effectvolle handeling is in deze beurt uitgevoerd en met een changed=true receipt bevestigd.');

  for (const disposition of ['observed', 'no_action']) {
    const underreported = await understandTurn({ text, origin: 'self', context: { signalBatch: batch } }, {
      capabilityCatalog: effectfulCatalog,
      complete: async () => ({ toolCalls: [{
        name: 'commit_turn_understanding', arguments: JSON.stringify({ ...effectfulRaw,
          signalAssessment: { disposition, assessment: 'Er gebeurde volgens het model niets.' },
        }),
      }] }),
    });
    assert.equal(underreported.ok, true, JSON.stringify(underreported));
    const underreportedPlan = bindSignalAssessmentPlan(planCapabilityAction(
      underreported.understanding.actions.at(-1), {
        mode: 'autonomy', understandingSha256: underreported.understanding.sha256,
      }), underreported.understanding);
    const underreportedMutation = underreported.understanding.actions.find((action) => (
      action.capabilityId === 'home.signal.effect'
    ));
    const exactChanged = { ...mutationReceipt(true), semanticBinding: {
      understandingSha256: underreported.understanding.sha256,
      requirementId: underreportedMutation.requirementId,
      capabilityId: underreportedMutation.capabilityId,
      operationId: underreportedMutation.operationId,
    } };
    const physical = JSON.parse(await executeTool(underreportedPlan.toolName,
      underreportedPlan.args, { mode: 'autonomy', messageContext: { signalBatch: batch },
        priorToolResults: [...readReceipts(underreported.understanding), exactChanged] }));
    assert.equal(physical.ok, true, disposition);
    assert.equal(physical.data.disposition, 'acted', disposition);
    assert.equal(physical.data.assessment,
      'Een exact geselecteerde effectvolle handeling is in deze beurt uitgevoerd en met een changed=true receipt bevestigd.', disposition);
  }

  assert.equal(bindSignalAssessmentPlan(parent, structuredClone(interpreted.understanding)), null,
    'een hashgelijk maar niet-issued understanding krijgt geen batchauthority');
  for (const [name, args, messageContext] of [
    ['direct forge', { ...bound.args }, { signalBatch: batch }],
    ['extra veld', { ...bound.args, ids: ['sig-1'] }, { signalBatch: batch }],
    ['stale batch', bound.args, { signalBatch: createSignalBatch([{ ...batch.signals[0], id: 'nieuw' }]) }],
  ]) {
    const rejected = JSON.parse(await executeTool(bound.toolName, args, { mode: 'autonomy', messageContext }));
    assert.equal(rejected.ok, false, name);
    assert.equal(rejected.verified, false, name);
  }
});

test('Home Assistant-readroutes vereisen vóór planning exact één getypeerde binding', () => {
  assert.deepEqual(resolveCapability('home.integration.inventory', { operation: 'read' })[0]
    .sourceBoundFields, ['integrationSlug']);
  assert.throws(
    () => bindCapabilityInput('home.entity.read', 'read', {}),
    /ontbrekend veld.*entityId/iu,
  );
  assert.throws(
    () => bindCapabilityInput('home.entity.read', 'read', { path: '/api/states' }),
    /onbekend veld|ontbrekend veld/iu,
  );
  assert.deepEqual(
    bindCapabilityInput('home.entity.read', 'read', { entityId: 'sensor.bbq_core' }),
    { entity_id: 'sensor.bbq_core' },
  );
  assert.throws(
    () => bindCapabilityInput('home.api.read', 'read', {}),
    /ontbrekend veld.*path/iu,
  );
  assert.deepEqual(
    bindCapabilityInput('home.api.read', 'read', { path: '/api/states/sensor.bbq_core' }),
    { pad: '/api/states/sensor.bbq_core' },
  );
  assert.throws(() => bindCapabilityInput('home.api.read', 'read', {
    path: 'Controleer de eerder gekozen fallback-route nog eens vers.',
  }), /patroon/iu);
  assert.deepEqual(
    bindCapabilityInput('home.integration.inventory', 'read', { integrationSlug: 'tuya' }),
    { integratie: 'tuya' },
  );
  for (const integrationSlug of ['Home Assistant', 'all']) assert.throws(
    () => bindCapabilityInput('home.integration.inventory', 'read', { integrationSlug }),
    /patroon/iu,
  );
  assert.throws(
    () => bindCapabilityInput('home.integration.inventory', 'read', {}),
    /ontbrekend veld.*integrationSlug/iu,
  );
});

test('gedeeltelijke huiscontext bindt alleen intern exacte begrensde entity-logboekreads', async () => {
  const understandingSha256 = 'a'.repeat(64), query = 'buitenlicht recente oorzaak';
  const parent = planCapabilityAction({ requirementId: 'r1', capabilityId: 'home.context.read',
    operationId: 'read', semanticInput: { query, history: true }, effect: 'read', polarity: 'request',
    authorization: 'allowed' }, { mode: 'chat', interactiveAuthority: 'explicit', understandingSha256 });
  const entity = { entity_id: 'automation.tuin_beweging_buitenlicht_aan' };
  const recorded = { name: 'huis_status', args: parent.args,
    ok: true, verified: true, partial: true, changed: false,
    effect_uncertain: false, semanticBinding: { understandingSha256, requirementId: 'r1',
      capabilityId: 'home.context.read', operationId: 'read' },
    output: JSON.stringify({ contract: 'ActionResult/v1', tool: 'huis_status', data: {
      history_required: true, entities: [entity],
    } }) };
  const recovery = bindHomeContextRecoveryPlan(parent, recorded);
  assert.equal(recovery.capabilityId, 'home.context.read');
  assert.equal(recovery.operationId, 'read');
  assert.deepEqual(recovery.args, { vraag: query, historie: true });
  const currentOnly = planCapabilityAction({ requirementId: 'r1', capabilityId: 'home.context.read',
    operationId: 'read', semanticInput: { query }, effect: 'read', polarity: 'request',
    authorization: 'allowed' }, { mode: 'chat', interactiveAuthority: 'explicit', understandingSha256 });
  assert.equal(bindHomeContextRecoveryPlan(currentOnly, { ...recorded, args: currentOnly.args }), null);
  assert.equal(bindHomeContextRecoveryPlan(parent, { ...recorded,
    semanticBinding: { ...recorded.semanticBinding, understandingSha256: 'b'.repeat(64) } }), null);
  assert.equal(bindHomeContextRecoveryPlan(parent, { ...recorded,
    output: JSON.stringify({ data: { history_required: true,
      entities: Array.from({ length: 24 }, (_item, index) => ({ entity_id: `light.test_${index}` })) } }) }), null);

  const originalFetch = globalThis.fetch;
  let reads = 0, boundedHistoryReads = 0;
  try {
    const smallStates = [
      { ...entity, state: 'on', last_updated: '2026-08-09T10:00:00.000Z',
        attributes: { friendly_name: 'Buitenlicht automatisering' } },
      { entity_id: 'light.buitenlicht', state: 'on', last_updated: '2026-08-09T10:00:01.000Z',
        attributes: { friendly_name: 'Buitenlicht' } },
    ];
    globalThis.fetch = async (url) => {
      if (String(url).endsWith('/api/states')) {
        return new Response(JSON.stringify(smallStates), { status: 200 });
      }
      boundedHistoryReads += 1;
      const selected = decodeURIComponent(String(url).match(/[?&]entity=([^&]+)/u)?.[1] || '');
      const parsedUrl = new URL(String(url));
      const start = decodeURIComponent(parsedUrl.pathname.split('/api/logbook/')[1] || '');
      const end = parsedUrl.searchParams.get('end_time');
      const when = new Date((Date.parse(start) + Date.parse(end)) / 2).toISOString();
      return new Response(JSON.stringify([{ entity_id: selected, state: 'on',
        when }]), { status: 200 });
    };
    const initialHistory = JSON.parse(await executeTool('huis_status', parent.args, { mode: 'chat' }));
    assert.equal(initialHistory.partial, true);
    assert.equal(initialHistory.needs_followup, true);
    assert.equal(initialHistory.data.history_required, true);
    assert.equal(Object.hasOwn(initialHistory.data, 'truncated'), false);
    const liveRecorded = { name: 'huis_status', args: parent.args,
      ok: initialHistory.ok, verified: initialHistory.verified, partial: initialHistory.partial,
      changed: initialHistory.changed, effect_uncertain: initialHistory.effect_uncertain,
      semanticBinding: recorded.semanticBinding, output: JSON.stringify(initialHistory) };
    const liveRecovery = bindHomeContextRecoveryPlan(parent, liveRecorded);
    assert.ok(liveRecovery);
    const liveHistory = JSON.parse(await executeTool('huis_status', liveRecovery.args, {
      mode: 'chat', issuedPlan: liveRecovery,
    }));
    assert.equal(liveHistory.ok, true);
    assert.equal(liveHistory.data.responses_complete, true);
    assert.equal(liveHistory.data.request_count, 2);
    assert.equal(liveHistory.data.completion_count, 2);
    assert.equal(liveHistory.data.matched_events, 2);
    assert.equal(boundedHistoryReads, 2);
    const initialCurrent = JSON.parse(await executeTool('huis_status', currentOnly.args, { mode: 'chat' }));
    assert.equal(initialCurrent.partial, false);
    assert.equal(initialCurrent.needs_followup, false);
    assert.equal(Object.hasOwn(initialCurrent.data, 'history_required'), false);
    assert.equal(boundedHistoryReads, 2, 'een kleine current-only read opent geen logboekroute');

    globalThis.fetch = async () => { reads += 1; return new Response('[]', { status: 200 }); };
    for (const wrongIssuedPlan of [parent, { ...parent }]) {
      const burnedRecovery = bindHomeContextRecoveryPlan(parent, recorded);
      const rejected = JSON.parse(await executeTool('huis_status', burnedRecovery.args, {
        mode: 'chat', issuedPlan: wrongIssuedPlan,
      }));
      assert.equal(rejected.ok, false);
      assert.match(rejected.error, /ongeldige interne logboekbinding/u);
      const replayAfterWrongPlan = JSON.parse(await executeTool(
        'huis_status', burnedRecovery.args, { mode: 'chat', issuedPlan: burnedRecovery },
      ));
      assert.equal(replayAfterWrongPlan.ok, false);
      assert.match(replayAfterWrongPlan.error, /al verbruikt/u);
      assert.equal(reads, 0, 'een verkeerd issued plan verbruikt vóór iedere logboek-I/O');
    }

    globalThis.fetch = async () => { reads += 1; return new Response('[]', { status: 200 }); };
    const empty = JSON.parse(await executeTool('huis_status', recovery.args, {
      mode: 'chat', issuedPlan: recovery,
    }));
    assert.equal(empty.ok, true);
    assert.equal(empty.verified, true);
    assert.equal(empty.partial, false);
    assert.equal(empty.needs_followup, false);
    assert.equal(empty.data.responses_complete, true);
    assert.equal(empty.data.empty, true);
    assert.equal(empty.data.request_count, 1);
    assert.equal(empty.data.completion_count, 1);
    assert.equal(empty.data.matched_events, 0);
    assert.equal(empty.data.returned_events, 0);
    assert.equal(empty.data.events_truncated, false);
    assert.deepEqual(empty.data.entity_ids, [entity.entity_id]);
    assert.deepEqual(Object.keys(empty.data.window).sort(), ['end', 'start']);
    assert.match(empty.data.message, /geen gekoppelde regels in ontvangen responses/iu);
    assert.doesNotMatch(empty.data.message, /er gebeurde niets|geen oorzaak/iu);
    const replay = JSON.parse(await executeTool('huis_status', recovery.args, {
      mode: 'chat', issuedPlan: recovery,
    }));
    assert.equal(replay.ok, false);
    assert.match(replay.error, /al verbruikt/u);
    assert.equal(reads, 1, 'dezelfde opaque recovery bereikt maar één keer het logboek');
    globalThis.fetch = async () => { reads += 1; return new Response(JSON.stringify([
      { entity_id: 'person.onverwant', message: 'privégebeurtenis' },
    ]), { status: 200 }); };
    const unrelatedRecovery = bindHomeContextRecoveryPlan(parent, recorded);
    const unrelated = JSON.parse(await executeTool('huis_status', unrelatedRecovery.args, {
      mode: 'chat', issuedPlan: unrelatedRecovery,
    }));
    assert.equal(unrelated.ok, true);
    assert.equal(unrelated.data.empty, true);
    assert.equal(unrelated.data.matched_events, 0);
    assert.doesNotMatch(JSON.stringify(unrelated), /privégebeurtenis/u);
    globalThis.fetch = async (url) => {
      reads += 1;
      const parsedUrl = new URL(String(url));
      const start = decodeURIComponent(parsedUrl.pathname.split('/api/logbook/')[1] || '');
      return new Response(JSON.stringify(Array.from({ length: 13 }, (_item, index) => ({
        entity_id: entity.entity_id,
        when: new Date(Date.parse(start) + (index + 1) * 1_000).toISOString(),
        state: index % 2 ? 'off' : 'on',
      }))), { status: 200 });
    };
    const truncatedRecovery = bindHomeContextRecoveryPlan(parent, recorded);
    const truncated = JSON.parse(await executeTool('huis_status', truncatedRecovery.args, {
      mode: 'chat', issuedPlan: truncatedRecovery,
    }));
    assert.equal(truncated.ok, true);
    assert.equal(truncated.data.responses_complete, true);
    assert.equal(truncated.data.matched_events, 13);
    assert.equal(truncated.data.returned_events, 12);
    assert.equal(truncated.data.events_truncated, true);
    globalThis.fetch = async () => { reads += 1; return new Response(
      JSON.stringify([{ entity_id: entity.entity_id, message: 'x'.repeat(25_000) }]), { status: 200 }); };
    const oversizedRecovery = bindHomeContextRecoveryPlan(parent, recorded);
    const oversized = JSON.parse(await executeTool('huis_status', oversizedRecovery.args, {
      mode: 'chat', issuedPlan: oversizedRecovery,
    }));
    assert.equal(oversized.ok, false);
    assert.equal(oversized.failure_class, 'output_truncated');
    globalThis.fetch = async () => { reads += 1; return new Response('{}', { status: 200 }); };
    const malformedRecovery = bindHomeContextRecoveryPlan(parent, recorded);
    const malformed = JSON.parse(await executeTool('huis_status', malformedRecovery.args, {
      mode: 'chat', issuedPlan: malformedRecovery,
    }));
    assert.equal(malformed.ok, false);
    assert.equal(malformed.failure_class, 'invalid_result');
    assert.equal(malformed.data.request_count, 1);
    assert.equal(malformed.data.completion_count, 1);
    globalThis.fetch = async () => { reads += 1; return new Response(JSON.stringify([{
      entity_id: entity.entity_id, when: '2000-01-01T00:00:00.000Z',
      message: 'regel buiten het gebonden venster',
    }]), { status: 200 }); };
    const outsideRecovery = bindHomeContextRecoveryPlan(parent, recorded);
    const outside = JSON.parse(await executeTool('huis_status', outsideRecovery.args, {
      mode: 'chat', issuedPlan: outsideRecovery,
    }));
    assert.equal(outside.ok, false);
    assert.equal(outside.failure_class, 'invalid_result');
    assert.doesNotMatch(JSON.stringify(outside), /regel buiten het gebonden venster/u);
    globalThis.fetch = async () => { reads += 1; throw new Error('logboek niet bereikbaar'); };
    const incompleteRecovery = bindHomeContextRecoveryPlan(parent, recorded);
    const incomplete = JSON.parse(await executeTool('huis_status', incompleteRecovery.args, {
      mode: 'chat', issuedPlan: incompleteRecovery,
    }));
    assert.equal(incomplete.ok, false);
    assert.equal(incomplete.failure_class, 'unavailable');
    assert.equal(incomplete.data.request_count, 1);
    assert.equal(incomplete.data.completion_count, 0);
  } finally { globalThis.fetch = originalFetch; }
  assert.equal(reads, 7);
});

test('historisch huisherstel leest het uur rond gisteren 00:15 en niet het laatste uur', async () => {
  const query = 'Waarom ging het licht in de overkapping gisteren om 00:15 uit?';
  const understandingSha256 = 'd'.repeat(64);
  const parent = planCapabilityAction({
    requirementId: 'r1', capabilityId: 'home.context.read', operationId: 'read',
    semanticInput: { query, history: true }, effect: 'read', polarity: 'request',
    authorization: 'allowed',
  }, { mode: 'chat', interactiveAuthority: 'explicit', understandingSha256 });
  const entityId = 'light.buiten';
  const recorded = {
    name: 'huis_status', args: parent.args, ok: true, verified: true,
    partial: true, changed: false, effect_uncertain: false,
    semanticBinding: { understandingSha256, requirementId: 'r1',
      capabilityId: 'home.context.read', operationId: 'read' },
    output: JSON.stringify({ contract: 'ActionResult/v1', tool: 'huis_status', data: {
      history_required: true, entities: [{ entity_id: entityId }],
    } }),
  };
  const recovery = bindHomeContextRecoveryPlan(
    parent, recorded, new Date('2026-08-14T08:39:00.000Z'),
  );
  assert.ok(recovery);

  const originalFetch = globalThis.fetch;
  let requestedUrl = null;
  try {
    globalThis.fetch = async (url) => {
      requestedUrl = new URL(String(url));
      return new Response('[]', { status: 200 });
    };
    const result = JSON.parse(await executeTool('huis_status', recovery.args, {
      mode: 'chat', issuedPlan: recovery,
    }));
    assert.equal(result.ok, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.ok(requestedUrl);
  assert.equal(
    decodeURIComponent(requestedUrl.pathname.split('/api/logbook/')[1] || ''),
    '2026-08-12T21:45:00.000Z',
  );
  assert.equal(requestedUrl.searchParams.get('end_time'), '2026-08-12T22:45:00.000Z');
  assert.equal(requestedUrl.searchParams.get('entity'), entityId);
});

test('causaal huisonderzoek reserveert logboekruimte voor de schakelaar, herstelautomation en tv', async () => {
  const query = 'Waarom ging het licht in de overkapping gisteren om 00:15 uit terwijl ik tv keek?';
  const states = [
    ...Array.from({ length: 12 }, (_item, index) => ({
      entity_id: `light.overkapping_${String(index).padStart(2, '0')}`,
      state: 'off', last_updated: '2026-08-13T00:15:02+02:00',
      attributes: { friendly_name: `Licht overkapping ${index}` },
    })),
    { entity_id: 'switch.automation_buiten_avond', state: 'on',
      attributes: { friendly_name: 'Hue Bridge Automation: Buiten Avond' } },
    { entity_id: 'automation.eva_buiten_blijft_aan_bij_schuurmedia', state: 'on',
      attributes: { friendly_name: 'Eva: Buiten blijft aan bij schuurmedia' } },
    { entity_id: 'media_player.tv_overkapping', state: 'paused',
      attributes: { friendly_name: 'Tv overkapping' } },
  ];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const context = await homeContextText({
      fresh: true, query, historyRequested: true, structured: true,
    });
    const selected = context.entities.map((item) => item.entity_id);
    assert.ok(selected.includes('switch.automation_buiten_avond'));
    assert.ok(selected.includes('automation.eva_buiten_blijft_aan_bij_schuurmedia'));
    assert.ok(selected.includes('media_player.tv_overkapping'));
    assert.equal(selected.length, 8);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('huiscontext vindt buiten- en binnentemperatuur via domeingebonden zoektags', async () => {
  const states = [{
    entity_id: 'weather.forecast_thuis', state: 'partlycloudy',
    last_updated: '2026-08-14T18:25:00.000Z',
    attributes: { friendly_name: 'Forecast Thuis', temperature: 16.4,
      temperature_unit: '°C' },
  }, {
    entity_id: 'climate.woonkamer', state: 'heat',
    last_updated: '2026-08-14T18:25:00.000Z',
    attributes: { friendly_name: 'Woonkamer', current_temperature: 20.1 },
  }, {
    entity_id: 'sensor.temperatuur_serverkast', state: '31.0',
    attributes: { friendly_name: 'Temperatuur serverkast', unit_of_measurement: '°C' },
  }];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const context = await homeContextText({
      fresh: true, query: 'buiten en binnen temperatuur', structured: true,
    });
    const selected = new Map(context.entities.map((item) => [item.entity_id, item]));
    assert.deepEqual([...selected.keys()].sort(), [
      'climate.woonkamer', 'sensor.temperatuur_serverkast', 'weather.forecast_thuis',
    ]);
    assert.equal(selected.get('weather.forecast_thuis')?.temperature, 16.4);
    assert.equal(selected.get('climate.woonkamer')?.current_temperature, 20.1);
    for (const query of [
      'actuele buitentemperatuur en binnentemperatuur',
      'buitentemperatuur tegenover kamertemperatuur',
    ]) {
      const compound = await homeContextText({ fresh: true, query, structured: true });
      assert.deepEqual(compound.entities.map((item) => item.entity_id).sort(), [
        'climate.woonkamer', 'weather.forecast_thuis',
      ], query);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('gestructureerde huiscontext blijft onder acht kilobyte met complete begrensde records', async () => {
  const hugeName = `Bewoner ${'zeer-lang-'.repeat(1_100)}`;
  const states = [{
    entity_id: 'sensor.woonkamer_temperatuur', state: '18.0',
    last_updated: '2026-08-15T10:00:00.000Z',
    attributes: { friendly_name: `Woonkamer temperatuur ${'lang-'.repeat(1_800)}`,
      unit_of_measurement: '°C' },
  }, ...Array.from({ length: 180 }, (_item, index) => ({
    entity_id: `person.bewoner_${index}`, state: index % 2 ? 'home' : 'not_home',
    attributes: { friendly_name: `${hugeName} ${index}` },
  }))];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const targeted = await homeContextText({
      fresh: true, query: 'woonkamer temperatuur', structured: true,
    });
    assert.deepEqual([targeted.ok, targeted.verified, targeted.changed,
      targeted.partial ?? false], [true, true, false, false]);
    assert.ok(Buffer.byteLength(JSON.stringify(targeted), 'utf8') < 8_000);
    assert.deepEqual(targeted.entities.map((entity) => entity.entity_id),
      ['sensor.woonkamer_temperatuur']);
    assert.match(targeted.message, /Woonkamer temperatuur.*18\.0\s*°C/iu);
    assert.doesNotMatch(targeted.message, /Aanwezigheid|Woonplaats|Weerbron/iu,
      'een gerichte structured read draagt alleen haar geselecteerde kandidaten');
    assert.ok(targeted.entities.every((entity) => Object.values(entity)
      .every((value) => value === null || ['string', 'number', 'boolean'].includes(typeof value))));

    const broad = await homeContextText({ fresh: true, query: '', structured: true });
    assert.deepEqual([broad.ok, broad.verified, broad.changed, broad.partial ?? false],
      [true, true, false, false]);
    assert.ok(Buffer.byteLength(JSON.stringify(broad), 'utf8') < 8_000);
    assert.deepEqual(broad.entities, []);
    assert.match(broad.message, /Aanwezigheid:/u);
    assert.match(broad.message, /\d+ niet getoond/u,
      'een brede status noemt hoeveel niet in het begrensde overzicht pasten');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('huiscontext projecteert status en temperatuur met hun eigen typed units', async () => {
  const states = [{
    entity_id: 'weather.forecast_thuis', state: 'partlycloudy',
    last_updated: '2026-08-15T10:00:00.000Z',
    attributes: { friendly_name: 'Weer thuis', temperature: 16.4,
      temperature_unit: '°C', unit_of_measurement: 'km/h' },
  }, {
    entity_id: 'climate.home', state: 'heat',
    last_updated: '2026-08-15T10:00:00.000Z',
    attributes: { friendly_name: 'Woning', current_temperature: 20.1,
      temperature_unit: '°C', unit_of_measurement: '°F',
      target_temperature: { forged: 21 } },
  }, {
    entity_id: 'sensor.temperatuur_defect', state: { forged: '19.0' },
    attributes: { friendly_name: { forged: 'Temperatuur defect' },
      unit_of_measurement: { forged: '°C' }, upper_limit: { forged: 30 } },
  }];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const context = await homeContextText({
      fresh: true, query: 'buiten en binnen temperatuur', structured: true,
    });
    assert.ok(Buffer.byteLength(JSON.stringify(context), 'utf8') < 8_000);
    assert.deepEqual(bindHomeContextEntityReferences([{
      requirementId: 'r2', sourceText: 'het buiten',
    }, {
      requirementId: 'r3', sourceText: 'binnen',
    }], context.entities), [
      { requirementId: 'r2', entityId: 'weather.forecast_thuis' },
      { requirementId: 'r3', entityId: 'climate.home' },
    ]);
    assert.match(context.message, /Woning.*status heat, huidige temperatuur 20\.1 °C/iu);
    assert.doesNotMatch(context.message, /heat\s+°[CF]/iu,
      'een categorische climate-status krijgt nooit een meeteenheid');
    assert.match(context.message, /Weer thuis.*temperatuur 16\.4 °C/iu);
    assert.doesNotMatch(context.message, /temperatuur 16\.4 km\/h/iu);
    assert.doesNotMatch(JSON.stringify(context), /\[object Object\]/u);
    const climate = context.entities.find((entity) => entity.entity_id === 'climate.home');
    assert.equal(climate.current_temperature, 20.1);
    assert.equal(climate.temperature_unit, '°C');
    assert.equal(climate.target_temperature ?? null, null,
      'een niet-scalair meetveld wordt niet als feit geprojecteerd');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('huiscontext reserveert gevraagde bronrollen voor de begrensde binderselectie', async () => {
  const climate = (id, name) => ({
    entity_id: id, state: 'heat',
    attributes: { friendly_name: name, current_temperature: 20,
      temperature_unit: '°C' },
  });
  const weather = (id, name) => ({
    entity_id: id, state: 'sunny',
    attributes: { friendly_name: name, temperature: 16, temperature_unit: '°C' },
  });
  const references = [{ requirementId: 'r2', sourceText: 'het buiten' },
    { requirementId: 'r3', sourceText: 'binnen' }];
  const originalFetch = globalThis.fetch;
  try {
    const crowded = [
      climate('climate.home', 'Woning'),
      ...Array.from({ length: 9 }, (_item, index) => climate(
        `climate.kamer_${String(index).padStart(2, '0')}`, `Kamer ${index}`,
      )),
      weather('weather.forecast_thuis', 'Weer thuis'),
    ];
    globalThis.fetch = async () => new Response(JSON.stringify(crowded), { status: 200 });
    const selected = await homeContextText({
      fresh: true, query: 'buiten en binnen temperatuur', structured: true,
    });
    assert.ok(selected.entities.length <= 8);
    assert.deepEqual([selected.ok, selected.verified, selected.complete,
      selected.partial, selected.needs_followup, selected.truncated ?? false],
    [true, true, true, false, false, false]);
    assert.equal(selected.verification_scope, 'requested_source_binding_candidates');
    assert.deepEqual(selected.selection_coverage, {
      complete: false, matched_candidates: 11,
      returned_candidates: 2, omitted_candidates: 9,
    });
    assert.equal(selected.binding_coverage.complete, true);
    assert.equal(selected.binding_coverage.roles.length, 2);
    assert.ok(selected.binding_coverage.roles.every((role) => (
      role.top_tie_count === 1 && role.retained_top_tie_count === 1
      && role.unique === true
    )), JSON.stringify(selected.binding_coverage));
    assert.equal(selected.binding_coverage.distinct, true);
    assert.deepEqual(bindHomeContextEntityReferences(references, selected.entities), [
      { requirementId: 'r2', entityId: 'weather.forecast_thuis' },
      { requirementId: 'r3', entityId: 'climate.home' },
    ], JSON.stringify(selected.entities));

    globalThis.fetch = async () => new Response(JSON.stringify([
      climate('climate.home', 'Woning'),
      weather('weather.noord', 'Buitenmeting'),
      weather('weather.zuid', 'Buitenmeting'),
    ]), { status: 200 });
    const weatherTie = await homeContextText({
      fresh: true, query: 'buiten en binnen temperatuur', structured: true,
    });
    assert.deepEqual(weatherTie.entities.filter((entity) => entity.entity_id
      .startsWith('weather.')).map((entity) => entity.entity_id), [
      'weather.noord', 'weather.zuid',
    ]);
    assert.deepEqual([weatherTie.complete, weatherTie.partial,
      weatherTie.needs_followup, weatherTie.truncated ?? false], [false, true, true, false]);
    assert.equal(weatherTie.binding_coverage.complete, false);
    assert.equal(weatherTie.binding_coverage.roles.find(
      (role) => role.domain === 'weather',
    ).top_tie_count, 2);
    assert.equal(bindHomeContextEntityReferences(references, weatherTie.entities), null,
      'twee gelijkwaardige buitenbronnen blijven zichtbaar en binden fail-closed');

    globalThis.fetch = async () => new Response(JSON.stringify([
      climate('climate.aggregate_a', 'Woning'),
      climate('climate.aggregate_b', 'Home'),
      weather('weather.forecast_thuis', 'Weer thuis'),
    ]), { status: 200 });
    const aggregateTie = await homeContextText({
      fresh: true, query: 'buiten en binnen temperatuur', structured: true,
    });
    assert.deepEqual(aggregateTie.entities.filter((entity) => entity.entity_id
      .startsWith('climate.')).map((entity) => entity.entity_id), [
      'climate.aggregate_a', 'climate.aggregate_b',
    ]);
    assert.deepEqual([aggregateTie.complete, aggregateTie.partial,
      aggregateTie.needs_followup, aggregateTie.truncated ?? false], [false, true, true, false]);
    assert.equal(aggregateTie.binding_coverage.complete, false);
    assert.equal(bindHomeContextEntityReferences(references, aggregateTie.entities), null,
      'twee gelijkwaardige hele-woningbronnen blijven zichtbaar en binden fail-closed');

    globalThis.fetch = async () => new Response(JSON.stringify([
      climate('climate.home', 'Woning'),
      ...Array.from({ length: 9 }, (_item, index) => weather(
        `weather.buiten_${String(index).padStart(2, '0')}`, 'Buitenmeting',
      )),
    ]), { status: 200 });
    const overflowTie = await homeContextText({
      fresh: true, query: 'buiten en binnen temperatuur', structured: true,
    });
    assert.deepEqual([overflowTie.complete, overflowTie.partial,
      overflowTie.needs_followup, overflowTie.truncated], [false, true, true, true]);
    assert.deepEqual(overflowTie.selection_coverage, {
      complete: false, matched_candidates: 10,
      returned_candidates: 1, omitted_candidates: 9,
    });
    const weatherCoverage = overflowTie.binding_coverage.roles.find(
      (role) => role.domain === 'weather',
    );
    assert.deepEqual([weatherCoverage.top_tie_count,
      weatherCoverage.retained_top_tie_count, weatherCoverage.unique], [9, 0, false]);
    assert.equal(bindHomeContextEntityReferences(references, overflowTie.entities), null);

    globalThis.fetch = async () => new Response(JSON.stringify([
      weather('weather.forecast_thuis', 'Weer thuis'),
    ]), { status: 200 });
    const missingRole = await homeContextText({
      fresh: true, query: 'buiten en binnen temperatuur', structured: true,
    });
    assert.deepEqual([missingRole.complete, missingRole.partial,
      missingRole.needs_followup], [false, true, true]);
    assert.equal(missingRole.binding_coverage.complete, false);
    assert.equal(missingRole.binding_coverage.roles.find(
      (role) => role.domain === 'climate',
    ).top_tie_count, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('huiscontext reserveert bronrollen alleen bij meet- of vergelijkintentie', async () => {
  const relevant = [{
    entity_id: 'automation.overkapping_lampen_uit', state: 'on',
    attributes: { friendly_name: 'Overkapping lampen uit automation' },
  }, {
    entity_id: 'switch.automation_overkapping_lampen', state: 'on',
    attributes: { friendly_name: 'Overkapping lampen automation schakelaar' },
  }, ...Array.from({ length: 10 }, (_item, index) => ({
    entity_id: `light.overkapping_${String(index).padStart(2, '0')}`, state: 'off',
    attributes: { friendly_name: `Lampen overkapping ${index}` },
  }))];
  const sources = [{
    entity_id: 'weather.forecast_thuis', state: 'sunny',
    attributes: { friendly_name: 'Weer thuis', temperature: 16,
      temperature_unit: '°C' },
  }, {
    entity_id: 'climate.home', state: 'heat',
    attributes: { friendly_name: 'Woning', current_temperature: 20,
      temperature_unit: '°C' },
  }];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(
      JSON.stringify([...relevant, ...sources]), { status: 200 },
    );
    const causal = await homeContextText({ fresh: true,
      query: 'Waarom gingen de lampen in de overkapping uit?',
      historyRequested: true, structured: true });
    assert.equal(causal.ok, true, JSON.stringify(causal));
    assert.equal(causal.entities.length, 8);
    assert.equal(causal.entities.some((entity) => /^(?:weather|climate)\./u
      .test(entity.entity_id)), false, JSON.stringify(causal.entities));
    assert.ok(causal.entities.some((entity) => entity.entity_id.startsWith('automation.')));
    assert.ok(causal.entities.some((entity) => entity.entity_id
      .startsWith('switch.automation_')));

    const lamps = await homeContextText({ fresh: true,
      query: 'lampen buiten en binnen', structured: true });
    assert.equal(lamps.entities.length, 8);
    assert.equal(lamps.entities.some((entity) => /^(?:weather|climate)\./u
      .test(entity.entity_id)), false, JSON.stringify(lamps.entities));
    assert.ok(lamps.entities.every((entity) => /^(?:light|automation|switch)\./u
      .test(entity.entity_id)), JSON.stringify(lamps.entities));

    const temperatures = await homeContextText({ fresh: true,
      query: 'buiten en binnen temperatuur', structured: true });
    assert.ok(temperatures.entities.some((entity) => entity.entity_id.startsWith('weather.')));
    assert.ok(temperatures.entities.some((entity) => entity.entity_id.startsWith('climate.')));

    const roomSources = [
      ...Array.from({ length: 9 }, (_item, index) => ({
        entity_id: `climate.kamer_${String(index).padStart(2, '0')}`, state: 'heat',
        attributes: { friendly_name: index === 0 ? 'Woonkamer' : `Kamer ${index}`,
          current_temperature: 20 + index / 10, temperature_unit: '°C' },
      })),
      sources[0],
    ];
    globalThis.fetch = async () => new Response(JSON.stringify(roomSources), { status: 200 });
    const livingRoom = await homeContextText({ fresh: true,
      query: 'woonkamer temperatuur', structured: true });
    assert.equal(livingRoom.entities.some((entity) => entity.entity_id
      .startsWith('weather.')), false, JSON.stringify(livingRoom));
    assert.equal(livingRoom.binding_coverage ?? null, null,
      'een kamervraag reserveert zonder buiten- of weerrol geen weather-bron');
    assert.deepEqual([livingRoom.partial, livingRoom.truncated], [true, true]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('luchtvochtigheid gebruikt alleen complete sensorfeiten en nooit de temperatuurroluitzondering', async () => {
  const query = 'vergelijk de luchtvochtigheid buiten en binnen';
  const weather = {
    entity_id: 'weather.forecast_thuis', state: 'sunny',
    attributes: { friendly_name: 'Weer thuis', temperature: 16,
      temperature_unit: '°C' },
  };
  const climate = {
    entity_id: 'climate.home', state: 'heat',
    attributes: { friendly_name: 'Woning', current_temperature: 20,
      temperature_unit: '°C' },
  };
  const humidity = (place, value) => ({
    entity_id: `sensor.${place}_luchtvochtigheid`, state: String(value),
    attributes: { friendly_name: `${place} luchtvochtigheid`,
      device_class: 'humidity', unit_of_measurement: '%' },
  });
  const outside = humidity('buiten', 64.2);
  const inside = humidity('binnen', 51.7);
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(
      JSON.stringify([weather, climate, outside, inside]), { status: 200 },
    );
    const complete = await homeContextText({ fresh: true, query, structured: true });
    assert.deepEqual([complete.ok, complete.verified, complete.complete,
      complete.partial, complete.needs_followup], [true, true, true, false, false]);
    assert.equal(complete.binding_coverage ?? null, null,
      'luchtvochtigheid mag geen weather/climate-temperatuurrol claimen');
    assert.equal(complete.verification_scope, 'all_semantic_candidates');
    assert.equal(complete.measurement_coverage.complete, true);
    assert.deepEqual(complete.entities.filter((entity) => entity.entity_id
      .includes('luchtvochtigheid')).map(({ entity_id, state, unit }) => (
      { entity_id, state, unit }
    )), [{ entity_id: 'sensor.binnen_luchtvochtigheid', state: '51.7', unit: '%' },
      { entity_id: 'sensor.buiten_luchtvochtigheid', state: '64.2', unit: '%' }]);
    assert.match(complete.message, /binnen luchtvochtigheid.*status 51\.7 %/iu);
    assert.match(complete.message, /buiten luchtvochtigheid.*status 64\.2 %/iu);

    globalThis.fetch = async () => new Response(
      JSON.stringify([weather, climate]), { status: 200 },
    );
    const noScalarProof = await homeContextText({ fresh: true, query, structured: true });
    assert.deepEqual([noScalarProof.complete, noScalarProof.partial,
      noScalarProof.needs_followup], [false, true, true]);
    assert.equal(noScalarProof.binding_coverage ?? null, null);
    assert.equal(noScalarProof.measurement_coverage.complete, false);

    const crowded = [weather, climate, outside, inside,
      ...Array.from({ length: 8 }, (_item, index) => ({
        entity_id: `sensor.luchtvochtigheid_extra_${String(index).padStart(2, '0')}`,
        state: String(45 + index),
        attributes: { friendly_name: `Luchtvochtigheid meetpunt ${index}`,
          device_class: 'humidity', unit_of_measurement: '%' },
      }))];
    globalThis.fetch = async () => new Response(JSON.stringify(crowded), { status: 200 });
    const humidityPlan = planCapabilityAction({ requirementId: 'r1',
      capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
      authorization: 'allowed', semanticInput: { query } }, {
      mode: 'chat', interactiveAuthority: 'explicit', understandingSha256: 'e'.repeat(64),
    });
    const bounded = JSON.parse(await executeTool(
      'huis_status', humidityPlan.args, { mode: 'chat' },
    ));
    assert.deepEqual([bounded.partial, bounded.needs_followup,
      bounded.failure_class], [true, true, 'output_truncated']);
    assert.equal(bounded.data.truncated, true);
    assert.equal(bounded.data.binding_coverage ?? null, null);
    assert.deepEqual(bounded.data.selection_coverage, {
      complete: false, matched_candidates: 12,
      returned_candidates: 8, omitted_candidates: 4,
    });

    const mixedQuery = 'vergelijk buiten en binnen temperatuur en luchtvochtigheid';
    const mixedPlan = planCapabilityAction({ requirementId: 'r1',
      capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
      authorization: 'allowed', semanticInput: { query: mixedQuery } }, {
      mode: 'chat', interactiveAuthority: 'explicit', understandingSha256: 'd'.repeat(64),
    });
    const mixed = JSON.parse(await executeTool(
      'huis_status', mixedPlan.args, { mode: 'chat' },
    ));
    assert.deepEqual([mixed.partial, mixed.needs_followup,
      mixed.failure_class], [true, true, 'output_truncated']);
    assert.equal(mixed.data.binding_coverage.complete, false,
      'temperatuurrolbewijs mag weggelaten luchtvochtigheidsbewijs niet maskeren');
    assert.ok(mixed.data.binding_coverage.omitted_non_temperature_candidates > 0);

    globalThis.fetch = async () => new Response(JSON.stringify([
      weather, climate,
    ]), { status: 200 });
    const coldWarm = await homeContextText({ fresh: true,
      query: 'buiten kouder dan binnen', structured: true });
    assert.deepEqual([coldWarm.complete, coldWarm.partial,
      coldWarm.binding_coverage.complete], [true, false, true]);

    globalThis.fetch = async () => new Response(JSON.stringify([{
      entity_id: 'weather.forecast_thuis', state: 'sunny',
      attributes: { friendly_name: 'Weer thuis' },
    }, climate]), { status: 200 });
    const missingTypedTemperature = await homeContextText({ fresh: true,
      query: 'buiten en binnen temperatuur', structured: true });
    assert.deepEqual([missingTypedTemperature.complete, missingTypedTemperature.partial,
      missingTypedTemperature.binding_coverage.complete], [false, true, false]);
    assert.equal(missingTypedTemperature.binding_coverage.roles.find(
      (role) => role.domain === 'weather',
    ).typed_value_present, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('huiscontext publiceert nooit een afgeknipt feitveld als geverifieerde waarde', async () => {
  const longState = `CATEGORISCHE_TOESTAND_PREFIX_${'x'.repeat(120)}`;
  const longUnit = `SENSOR_UNIT_PREFIX_${'y'.repeat(120)}`;
  const longTemperatureUnit = `TEMPERATUUR_UNIT_PREFIX_${'z'.repeat(120)}`;
  const states = [{
    entity_id: 'climate.home', state: longState,
    attributes: { friendly_name: 'Woning', current_temperature: 20.1,
      temperature_unit: longTemperatureUnit },
  }, {
    entity_id: 'sensor.binnenmeter_temperatuur', state: '18.0',
    attributes: {
      friendly_name: `Binnenmeter ${'zeer-lange-naam-'.repeat(40)}`,
      unit_of_measurement: longUnit,
    },
  }];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const context = await homeContextText({
      fresh: true, query: 'binnen temperatuur', structured: true,
    });
    assert.deepEqual([context.ok, context.verified, context.partial ?? false],
      [true, true, false]);
    assert.ok(Buffer.byteLength(JSON.stringify(context), 'utf8') < 8_000);
    assert.doesNotMatch(context.message,
      /CATEGORISCHE_TOESTAND_PREFIX|SENSOR_UNIT_PREFIX|TEMPERATUUR_UNIT_PREFIX/u);
    assert.match(context.message, /Woning.*huidige temperatuur 20\.1/iu);
    assert.match(context.message, /Binnenmeter.*\[afgekort\].*status 18\.0/iu,
      'een begrensde displaynaam is gemarkeerd, een complete numerieke state blijft intact');
    const projectedClimate = context.entities.find(
      (entity) => entity.entity_id === 'climate.home',
    );
    const projectedSensor = context.entities.find(
      (entity) => entity.entity_id === 'sensor.binnenmeter_temperatuur',
    );
    assert.equal(Object.hasOwn(projectedClimate, 'state'), false);
    assert.equal(Object.hasOwn(projectedClimate, 'temperature_unit'), false);
    assert.equal(Object.hasOwn(projectedSensor, 'unit'), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('causaal huisonderzoek weigert een onvolledige HA-reconnectsnapshot', async () => {
  const states = [
    { entity_id: 'input_boolean.buiten_licht_door_beweging', state: 'off',
      attributes: { friendly_name: 'Buiten licht door beweging' } },
    { entity_id: 'input_boolean.keuken_licht_door_beweging', state: 'off',
      attributes: { friendly_name: 'Keuken licht door beweging' } },
    { entity_id: 'person.jaap_frank', state: 'home',
      attributes: { friendly_name: 'Jaap Frank' } },
  ];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const context = await homeContextText({
      fresh: true,
      query: 'Waarom ging het licht in de overkapping gisteren om 00:15 uit terwijl ik tv keek?',
      historyRequested: true,
      structured: true,
    });
    assert.equal(context.ok, false);
    assert.equal(context.verified, false);
    assert.equal(context.history_snapshot_incomplete, true);
    assert.deepEqual(context.entities, []);
    assert.match(context.reason, /nog niet volledig beschikbaar/iu);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('brede huisgeschiedenis negeert lokaal en en kiest begrensde actuele salience', async () => {
  const originalFetch = globalThis.fetch;
  try {
    const state = (entityId, value, updated, friendlyName, deviceClass = null) => ({
      entity_id: entityId, state: value, last_updated: updated,
      attributes: { friendly_name: friendlyName, ...(deviceClass ? { device_class: deviceClass } : {}) },
    });
    const states = [
      state('media_player.sync_binnen_en_sonos', 'playing', '2026-08-11T10:00:00.000Z',
        'Sync binnen en Sonos'),
      state('light.overloop', 'on', '2026-08-11T10:05:00.000Z', 'Overloop'),
      state('person.zoe', 'home', '2026-08-11T10:08:00.000Z', 'Zoe'),
      state('binary_sensor.zijdeur', 'on', '2026-08-11T10:10:00.000Z', 'Zijdeur', 'door'),
      state('button.sync_binnen_en_sonos_favorite_current_song', 'unknown',
        '2026-08-11T10:11:00.000Z', 'Sync binnen en Sonos favorite'),
      state('light.keuken', 'on', '2026-08-11T10:09:00.000Z', 'Keuken'),
      state('binary_sensor.achterdeur', 'on', '2026-08-11T10:10:00.000Z', 'Achterdeur', 'door'),
      state('person.anna', 'not_home', '2026-08-11T10:08:00.000Z', 'Anna'),
      state('automation.eva_schuur_2_bedient_buiten_en_schuur', 'on',
        '2026-08-11T10:12:00.000Z', 'Eva schuur bedient buiten en schuur'),
      state('light.hal', 'on', '2026-08-11T10:07:00.000Z', 'Hal'),
      state('binary_sensor.schuurdeur', 'unknown', '2026-08-11T10:06:00.000Z',
        'Schuurdeur', 'door'),
      state('light.trap', 'on', '2026-08-11T10:04:00.000Z', 'Trap'),
    ];
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const broad = await homeContextText({ fresh: true,
      query: 'actuele gezins-, gespreks-, internet-, werk- en huiscontext',
      historyRequested: true, structured: true });
    assert.equal(broad.history_selection, 'salient_fallback');
    assert.deepEqual(broad.entities.map((item) => item.entity_id), [
      'binary_sensor.achterdeur',
      'binary_sensor.zijdeur',
      'binary_sensor.schuurdeur',
      'person.anna',
      'person.zoe',
      'light.keuken',
      'light.hal',
      'light.overloop',
    ]);
    assert.equal(broad.entities.length, 8);
    assert.ok(broad.entities.length < 24);
    assert.equal(broad.entities.some((item) => item.entity_id.startsWith('automation.')), false,
      'automation.on is geen actuele gebeurtenisclaim of salient fallback');
    assert.equal(broad.entities.some((item) => item.entity_id.includes('_en_')), false,
      'het lokale voegwoord en geeft geen semantische match');

    const semanticStates = Array.from({ length: 24 }, (_item, index) => state(
      `light.semantic_${String(index).padStart(2, '0')}`, 'off',
      '2026-08-11T10:00:00.000Z', `Tuinlicht ${String(index).padStart(2, '0')}`,
    ));
    globalThis.fetch = async () => new Response(JSON.stringify(semanticStates), { status: 200 });
    const semanticPlan = planCapabilityAction({ requirementId: 'r1',
      capabilityId: 'home.context.read', operationId: 'read', polarity: 'request',
      authorization: 'allowed', semanticInput: { query: 'tuinlicht', history: true },
      effect: 'read' }, { mode: 'chat', interactiveAuthority: 'explicit',
      understandingSha256: 'c'.repeat(64) });
    const semantic = JSON.parse(await executeTool('huis_status', semanticPlan.args, { mode: 'chat' }));
    assert.equal(semantic.partial, true);
    assert.equal(semantic.failure_class, 'output_truncated');
    assert.equal(semantic.data.truncated, true);
    assert.deepEqual(semantic.data.selection_coverage, {
      complete: false, matched_candidates: 24,
      returned_candidates: 8, omitted_candidates: 16,
    });
    assert.equal(semantic.data.history_selection, 'semantic');
    assert.deepEqual(semantic.data.entities.map((item) => item.entity_id),
      Array.from({ length: 8 }, (_item, index) => `light.semantic_${String(index).padStart(2, '0')}`));
    assert.equal(semantic.data.entities.length, 8);
    assert.ok(semantic.data.entities.length < 24);
  } finally { globalThis.fetch = originalFetch; }
});

test('compacte actuele huiscontext is begrensd en faalt dicht zonder verse HA-bron', async () => {
  const originalFetch = globalThis.fetch;
  try {
    const manyStates = Array.from({ length: 1_300 }, (_item, index) => ({
      entity_id: `sensor.context_${index}`, state: index % 91 ? 'ok' : 'unavailable',
      attributes: { friendly_name: `Context ${index}` },
    }));
    manyStates.push({ entity_id: 'binary_sensor.achterdeur', state: 'unavailable',
      attributes: { friendly_name: 'Achterdeur', device_class: 'door' } });
    globalThis.fetch = async () => new Response(JSON.stringify(manyStates), { status: 200 });
    const ordinary = await homeContextText({ fresh: true, query: '', structured: true });
    assert.match(ordinary.message, /Openingstatus niet bevestigd \(1\): Achterdeur/u);
    const snapshot = await homeContextText({ fresh: true, query: '', structured: true,
      compactCurrent: true });
    assert.equal(snapshot.ok, true);
    assert.equal(snapshot.verified, true);
    assert.equal(snapshot.snapshot.anomalies.count, 16);
    assert.equal(snapshot.snapshot.anomalies.items.length, 6);
    assert.equal(snapshot.snapshot.openings_unknown.count, 1);
    assert.equal(snapshot.snapshot.openings_unknown.items[0].entity_id, 'binary_sensor.achterdeur');
    assert.match(snapshot.message, /Openingen niet bevestigd \(1\): Achterdeur: unavailable/u);
    assert.ok(JSON.stringify(snapshot).length < 8_000);

    globalThis.fetch = async () => new Response('[]', { status: 200 });
    const empty = await homeContextText({ fresh: true, query: '', structured: true,
      compactCurrent: true });
    assert.equal(empty.ok, false);
    assert.equal(empty.verified, false);

    globalThis.fetch = async () => { throw new Error('HA offline'); };
    const unavailable = await homeContextText({ fresh: true, query: '', structured: true,
      compactCurrent: true });
    assert.equal(unavailable.ok, false);
    assert.equal(unavailable.verified, false);
  } finally { globalThis.fetch = originalFetch; }
});

test('compacte continuity-snapshot bewaakt één totaalbudget met expliciete dekking', async () => {
  const entityId = (domain, index) => {
    const suffix = `_${String(index).padStart(2, '0')}`;
    return `${domain}.${'x'.repeat(255 - domain.length - 1 - suffix.length)}${suffix}`;
  };
  const state = (domain, index, value, deviceClass = null) => ({
    entity_id: entityId(domain, index), state: value,
    attributes: { friendly_name: `Naam ${index} ${'lang-'.repeat(80)}`,
      ...(deviceClass ? { device_class: deviceClass } : {}) },
  });
  const states = [
    ...Array.from({ length: 6 }, (_item, index) => state(
      'person', index, index % 2 ? 'home' : 'not_home',
    )),
    ...Array.from({ length: 6 }, (_item, index) => state('light', index, 'on')),
    ...Array.from({ length: 6 }, (_item, index) => state(
      'binary_sensor', index, 'on', 'door',
    )),
    ...Array.from({ length: 6 }, (_item, index) => state(
      'binary_sensor', index + 10, 'unavailable', 'door',
    )),
    ...Array.from({ length: 6 }, (_item, index) => state(
      'sensor', index, 'unavailable',
    )),
  ];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(states), { status: 200 });
    const result = await homeContextText({ fresh: true, query: '', structured: true,
      compactCurrent: true });
    assert.ok(Buffer.byteLength(JSON.stringify(result), 'utf8') < 8_000);
    assert.deepEqual([result.ok, result.verified, result.changed], [true, true, false]);
    assert.equal(result.verification_scope, 'returned_records_and_group_counts');
    const groups = ['presence', 'lights_on', 'openings_open', 'openings_unknown', 'anomalies']
      .map((key) => result.snapshot[key]);
    for (const group of groups) {
      assert.equal(group.count, group.items.length + group.omitted);
      assert.ok(group.items.every((item) => item.entity_id.length === 255));
      assert.ok(group.items.every((item) => !item.name.includes('lang-lang-lang-lang-lang'))
        || group.items.every((item) => item.name.includes('[afgekort]')));
    }
    const omitted = groups.reduce((sum, group) => sum + group.omitted, 0);
    assert.ok(omitted > 0, JSON.stringify(result.snapshot));
    assert.equal(result.snapshot.complete, false);
    assert.deepEqual(result.snapshot.coverage, {
      complete: false, returned_records: groups.reduce(
        (sum, group) => sum + group.items.length, 0,
      ), omitted_records: omitted,
    });
    assert.match(result.message, /niet getoond|begrensd/iu);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('integratie-inventaris koppelt vier read-only HA-bronnen per apparaat en faalt bij een gat', async () => {
  const calls = [];
  const replies = {
    'config_entries/get': [
      { entry_id: 'entry-tuya', domain: 'tuya', title: 'Tuya', state: 'loaded', source: 'user' },
      { entry_id: 'entry-hue', domain: 'hue', title: 'Hue', state: 'loaded' },
    ],
    'config/device_registry/list': [
      { id: 'device-ketel', name: 'Waterkoker', manufacturer: 'Foo', model: 'K1',
        config_entry_id: 'entry-tuya', identifiers: [['tuya', 'abc']] },
      { id: 'device-hue', name: 'Hue', config_entries: ['entry-hue'] },
    ],
    'config/entity_registry/list': [
      { entity_id: 'switch.waterkoker', platform: 'tuya', config_entry_id: 'entry-tuya', device_id: 'device-ketel' },
      { entity_id: 'sensor.tuya_cloudstatus', platform: 'tuya', config_entry_id: 'entry-tuya', device_id: null },
      { entity_id: 'light.hue', platform: 'hue', config_entry_id: 'entry-hue', device_id: 'device-hue' },
    ],
    get_states: [
      { entity_id: 'switch.waterkoker', state: 'off', attributes: { friendly_name: 'Waterkoker' } },
      { entity_id: 'sensor.tuya_cloudstatus', state: 'online', attributes: {} },
    ],
  };
  const command = async (payload) => {
    calls.push(payload);
    return replies[payload.type];
  };
  for (const invalid of ['Home Assistant', 'all']) {
    assert.equal((await listIntegrationInventory(invalid, { command })).ok, false);
  }
  assert.equal(calls.length, 0, 'ongeldige titels en aggregaten bereiken Home Assistant niet');
  const inventory = await listIntegrationInventory('tuya', { command });

  assert.deepEqual(calls.map((call) => call.type), [
    'config_entries/get', 'config/device_registry/list', 'config/entity_registry/list', 'get_states',
  ]);
  assert.equal(calls[0].domain, 'tuya');
  assert.ok(calls.every((call) => !/update|create|delete|reload|install/iu.test(call.type)));
  assert.equal(inventory.ok, true);
  assert.equal(inventory.verified, true);
  assert.deepEqual(inventory.counts, {
    config_entries: 1, devices: 1, entities: 2,
    returned_config_entries: 1, returned_devices: 1, returned_entities: 2,
  });
  assert.equal(inventory.devices[0].name, 'Waterkoker');
  assert.equal(inventory.devices[0].entities[0].state, 'off');
  assert.equal(inventory.devices[0].entities[0].state_verified, true);
  assert.match(inventory.evidence_contract, /exacte sleutels.*registrybronnen/iu);
  assert.equal(inventory.entities_without_device[0].entity_id, 'sensor.tuya_cloudstatus');
  assert.match(inventory.limitations[0], /bewijst.*niet compatibiliteit/iu);

  const incomplete = await listIntegrationInventory('tuya', {
    command: async (payload) => {
      if (payload.type === 'config/device_registry/list') throw new Error('geen registrytoegang');
      return replies[payload.type];
    },
  });
  assert.equal(incomplete.ok, false);
  assert.equal(incomplete.verified, false);
  assert.equal(incomplete.failure_class, 'permission');
  assert.equal(incomplete.effect_uncertain, false);
  assert.deepEqual(incomplete.missing_sources.map((item) => item.source), ['devices']);

  const manyDevices = Array.from({ length: 40 }, (_item, index) => ({
    id: `device-${index}`, name: `Lang apparaat ${index} ${'x'.repeat(80)}`,
    manufacturer: 'Fabrikant', model: 'Model', config_entries: ['entry-tuya'],
    identifiers: [['tuya', `identifier-${index}`]],
  }));
  const manyEntities = manyDevices.flatMap((device, index) => Array.from({ length: 3 }, (_item, sub) => ({
    entity_id: `switch.lang_apparaat_${index}_${sub}`, platform: 'tuya',
    config_entry_id: 'entry-tuya', device_id: device.id,
  })));
  const bounded = await listIntegrationInventory('tuya', {
    command: async ({ type }) => ({
      'config_entries/get': replies['config_entries/get'],
      'config/device_registry/list': manyDevices,
      'config/entity_registry/list': manyEntities,
      get_states: [],
    })[type],
  });
  assert.ok(Buffer.byteLength(JSON.stringify(bounded)) <= 7_800);
  assert.equal(bounded.complete, false);
  assert.equal(bounded.partial, true);
  assert.ok(bounded.counts.returned_devices < bounded.counts.devices
    || bounded.counts.returned_entities < bounded.counts.entities);
});

test('één geverifieerde action bindt aan exact één tool', () => {
  const action = {
    requirementId: 'r1', capabilityId: 'home.device.control', operationId: 'set',
    authorization: 'allowed', semanticInput: { target: 'tv overkapping', desiredState: 'off' },
  };
  assert.equal(planCapabilityAction(action), null, 'ambient chat mag geen mutatie plannen');
  const plan = planCapabilityAction(action, { interactiveAuthority: 'explicit' });
  assert.equal(plan.toolName, 'bedien_huis');
  assert.deepEqual(plan.args, { doel: 'tv overkapping', actie: 'uit' });
  assert.deepEqual(Object.keys(plan).includes('handler'), false);
  assert.equal(Object.isFrozen(plan), true);
  assert.equal(Object.isFrozen(plan.args), true);

  const denied = planCapabilityAction({ ...action, authorization: 'denied' }, {
    interactiveAuthority: 'root_approved',
  });
  assert.equal(denied, null);
  const runtimeApply = planCapabilityAction({
    requirementId: 'r2', capabilityId: 'runtime.change.apply', operationId: 'pas_toe',
    authorization: 'allowed', semanticInput: { transactie_id: 'tx-1', receipt_sha256: 'a'.repeat(64) },
  }, { interactiveAuthority: 'trusted_owner' });
  assert.equal(runtimeApply, null, 'root-approved blijft buiten de eigenaarbeurt');
});

function issuedWorkspacePlan(target = 'testmap/boodschappen.py') {
  return planCapabilityAction({
    requirementId: 'r1', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed', semanticInput: { pad: target },
  }, { interactiveAuthority: 'explicit' });
}
const executionHandle = (handles, lane, type) => handles[lane]
  .find((handle) => handle.alias === `execution_${lane}_${type}`);

test('iteratieve registrymetadata noemt materiaal en uitsluitend exacte operationele bewijsroutes', () => {
  const write = TOOL_REGISTRY.flatMap((tool) => tool.provides)
    .find((provided) => provided.capability === 'workspace.file.write').operations[0];
  assert.equal(write.execution.mode, 'iterative');
  assert.deepEqual(write.execution.materializable, ['inhoud']);
  assert.deepEqual(write.execution.target, {
    field: 'pad', boundary: 'workspace', sameDirectory: true, artifactLimit: 4,
  });
  assert.deepEqual(write.execution.proofOperations, [
    { capabilityId: 'workspace.file.write', operationId: 'write', type: 'write' },
    { capabilityId: 'workspace.program.execute', operationId: 'execute', type: 'run' },
    { capabilityId: 'workspace.file.read', operationId: 'read', type: 'readback' },
  ]);
  assert.deepEqual(write.execution.recoveryOperations, [
    { capabilityId: 'workspace.file.read', operationId: 'read', type: 'inspect' },
    { capabilityId: 'workspace.file.write', operationId: 'write', type: 'repair' },
    { capabilityId: 'workspace.program.execute', operationId: 'execute', type: 'retest' },
  ]);
  assert.equal(Object.isFrozen(write.execution), true);
  const execute = TOOL_REGISTRY.flatMap((tool) => tool.provides)
    .find((provided) => provided.capability === 'workspace.program.execute').operations[0];
  assert.deepEqual(execute.execution.fileArguments, {
    programField: 'programma',
    argumentsField: 'argumenten',
    materializedByProgram: {
      node: ['.cjs', '.js', '.mjs'],
      python: ['.py'],
      python3: ['.py'],
    },
    readDependencies: ['.csv', '.json', '.jsonl', '.ndjson', '.txt', '.yaml', '.yml'],
  });
  assert.equal(Object.isFrozen(execute.execution.fileArguments), true);
  assert.equal(Object.isFrozen(execute.execution.fileArguments.materializedByProgram.node), true);
  assert.equal(TOOL_REGISTRY.flatMap((tool) => tool.provides)
    .flatMap((provided) => provided.operations)
    .filter((operation) => operation.execution.fileArguments != null).length, 1);
  assert.equal(TOOL_REGISTRY.flatMap((tool) => tool.provides)
    .find((provided) => provided.capability === 'home.device.control')
    .operations[0].execution.mode, 'direct');
  assert.equal(JSON.stringify(write.execution).includes('schrijf_bestand'), false);

  const catalogWrite = capabilityCatalog().find((item) => item.id === 'workspace.file.write').operations[0];
  const catalogExecute = capabilityCatalog()
    .find((item) => item.id === 'workspace.program.execute').operations[0];
  assert.equal(catalogWrite.execution.mode, 'iterative');
  assert.deepEqual(catalogExecute.execution.fileArguments, execute.execution.fileArguments);
  assert.deepEqual(catalogWrite.input.required, ['pad']);
  assert.match(catalogWrite.input.properties.pad.pattern, /A-Za-z0-9/);
  assert.throws(() => planCapabilityAction({
    requirementId: 'r-invalid', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed',
    semanticInput: { pad: 'Werkruimte: maak een programma en tests voor keukenkosten' },
  }, { interactiveAuthority: 'explicit' }), /patroon wijkt af/u);
  assert.equal(Object.hasOwn(catalogWrite.input.properties, 'inhoud'), false,
    'de interpreter kiest doel en bedoeling; de uitvoeringsdriver materialiseert pas de inhoud');
  assert.deepEqual(write.input.required, ['pad', 'inhoud'],
    'de fysieke child-operation blijft volledige inhoud eisen');
});

test('alleen een echt issued parentplan kan opaque childhandles uitgeven', () => {
  const parent = issuedWorkspacePlan();
  assert.ok(parent);
  assert.equal(parent.execution.mode, 'iterative');
  assert.equal(parent.args.pad, path.join(dataDir, 'workspace', 'testmap', 'boodschappen.py'));
  const handles = executionHandlesForPlan(parent);
  assert.strictEqual(executionHandlesForPlan(parent), handles, 'opnieuw opvragen omzeilt geen sessielimiet');
  assert.equal(handles.contract, 'CapabilityExecutionHandles/v1');
  assert.equal(handles.proof.length, 3);
  assert.equal(handles.recovery.length, 3);
  assert.equal(Object.isFrozen(handles.proof[0]), true);
  assert.deepEqual(Object.keys(handles.proof[0]).sort(), [
    'alias', 'contract', 'description', 'parameters',
  ]);
  assert.equal(JSON.stringify(handles).includes('schrijf_bestand'), false);
  assert.equal(JSON.stringify(handles).includes('workspace.file.write'), false);
  assert.throws(() => executionHandlesForPlan({ ...parent }), /niet door de capability-issuer/u);
  assert.throws(() => executionHandlesForPlan(planCapabilityAction({
    requirementId: 'r2', capabilityId: 'home.context.read', operationId: 'read',
    authorization: 'allowed', semanticInput: {},
  })), /geen iteratieve uitvoeringsruimte/u);

  const writeHandle = executionHandle(handles, 'proof', 'write');
  assert.deepEqual(writeHandle.parameters.required, ['pad', 'inhoud']);
  assert.throws(() => bindExecutionHandle(writeHandle, {
    pad: 'data/workspace/testmap/boodschappen.py',
  }), /ontbrekend veld inhoud/u);
  const child = bindExecutionHandle(writeHandle, {
    pad: 'data/workspace/testmap/boodschappen.py', inhoud: 'print("klaar")\n',
  });
  assert.equal(child.toolName, 'schrijf_bestand');
  assert.equal(child.execution.mode, 'direct');
  assert.equal(child.execution.type, 'write');
  assert.equal(child.args.pad, path.join(dataDir, 'workspace', 'testmap', 'boodschappen.py'));
  assert.throws(() => bindExecutionHandle({ ...writeHandle }, {
    pad: 'testmap/boodschappen.py', inhoud: 'vervalst',
  }), /vervalste execution-handle/u);
  assert.throws(() => bindExecutionHandle(Object.freeze({
    ...writeHandle, operationId: 'execute', toolName: 'voer_uit',
  }), { programma: 'python3' }), /vervalste execution-handle/u);
});

test('iteratieve writes blijven in één doelmap en onder één niet-heruitgeefbare artefactlimiet', () => {
  const parent = issuedWorkspacePlan('bouw/doel.py');
  const handles = executionHandlesForPlan(parent);
  const write = executionHandle(handles, 'proof', 'write');
  for (const filename of ['doel.py', 'test_doel.py', 'lastig.json', 'verwacht.json']) {
    const child = bindExecutionHandle(write, { pad: `bouw/${filename}`, inhoud: `${filename}\n` });
    assert.equal(path.dirname(child.args.pad), path.join(dataDir, 'workspace', 'bouw'));
  }
  assert.throws(() => bindExecutionHandle(write, {
    pad: 'bouw/vijfde.txt', inhoud: 'te veel',
  }), /artefactlimiet/u);
  assert.throws(() => bindExecutionHandle(write, {
    pad: 'andere-map/test_doel.py', inhoud: 'sibling',
  }), /dezelfde doelmap/u);
  for (const traversal of ['bouw/../buiten.py', '../buiten.py', 'data/workspace/bouw/../../buiten.py']) {
    assert.throws(() => bindExecutionHandle(write, { pad: traversal, inhoud: 'nee' }), /padtraversal/u);
  }
});

test('een sibling buiten de parentsessie wordt niet als driververplichting aangeboden', () => {
  const parent = issuedWorkspacePlan('groep/doel.py');
  const inside = planCapabilityAction({
    requirementId: 'r2', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed', semanticInput: { pad: 'groep/test_doel.py' },
  }, { interactiveAuthority: 'explicit' });
  const outside = planCapabilityAction({
    requirementId: 'r3', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed', semanticInput: { pad: 'andere-groep/test_doel.py' },
  }, { interactiveAuthority: 'explicit' });
  const obligation = executionObligationForPlan(parent, inside);
  assert.equal(obligation.descriptor.alias, 'execution_proof_write');
  assert.deepEqual(obligation.descriptor.expectedInput, inside.args);
  assert.deepEqual(obligation.descriptor.materializableFields, ['inhoud']);
  assert.equal(executionObligationForPlan(parent, outside), null);

  const handles = executionHandlesForPlan(parent);
  const before = handles.target.artifactLimit;
  assert.throws(() => bindExecutionHandle(obligation.handle, {
    pad: 'groep/verkeerde-fixture.py', inhoud: 'nee\n',
  }, { expectedPlan: inside }), (error) => (
    error?.code === 'EVA_EXECUTION_OBLIGATION_MISMATCH'
  ));
  // De afgewezen protocolpoging mag de sessie-artefactlimiet niet verbruiken.
  for (const filename of ['doel.py', 'test_doel.py', 'invoer.json', 'verwacht.json']) {
    bindExecutionHandle(executionHandle(handles, 'proof', 'write'), {
      pad: `groep/${filename}`, inhoud: `${filename}\n`,
    });
  }
  assert.equal(before, 4);
});

test('een onopgeloste eind-read bindt alleen aan exact één nog ongelezen schrijfartefact', () => {
  const understandingSha256 = '9'.repeat(64);
  const issuer = { interactiveAuthority: 'explicit', understandingSha256 };
  const parent = planCapabilityAction({
    requirementId: 'r1', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed', semanticInput: { pad: 'singleton/doel.js' },
  }, issuer);
  const handles = executionHandlesForPlan(parent);
  const writes = ['doel.js', 'doel.test.js'].map((name) => bindExecutionHandle(
    executionHandle(handles, 'proof', 'write'),
    { pad: `singleton/${name}`, inhoud: `${name}\n` },
  ));
  const writeReceipts = writes.map((plan) => ({
    plan, result: { name: plan.toolName, args: plan.args, ok: true, verified: true },
  }));
  const unresolved = planCapabilityAction({
    requirementId: 'r3', capabilityId: 'workspace.file.read', operationId: 'read',
    authorization: 'allowed', semanticInput: { pad: '[gevonden resultaat]' },
  }, issuer);
  assert.equal(executionObligationForPlan(parent, unresolved, {
    receiptContext: writeReceipts,
  }), null, 'twee mogelijke artefacten blijven fail-closed');

  const sourceRead = planCapabilityAction({
    requirementId: 'r2', capabilityId: 'workspace.file.read', operationId: 'read',
    authorization: 'allowed', semanticInput: { pad: 'singleton/doel.js' },
  }, issuer);
  const sourceObligation = executionObligationForPlan(parent, sourceRead, {
    receiptContext: writeReceipts,
  });
  const sourceChild = bindExecutionHandle(sourceObligation.handle, sourceObligation.descriptor.expectedInput, {
    receiptContext: writeReceipts, expectedObligation: sourceObligation,
  });
  const readReceipt = (plan) => ({
    plan,
    result: {
      name: plan.toolName, args: plan.args, ok: true, verified: true,
      changed: false, partial: false, needs_followup: false, effect_uncertain: false,
      output: JSON.stringify({
        contract: 'ActionResult/v1', tool: plan.toolName,
        ok: true, verified: true, changed: false, partial: false, needs_followup: false,
      }),
      semanticBinding: {
        understandingSha256, requirementId: 'r1',
        capabilityId: plan.capabilityId, operationId: plan.operationId,
      },
    },
  });
  const oneUnread = [...writeReceipts, readReceipt(sourceChild)];
  const singleton = executionObligationForPlan(parent, unresolved, { receiptContext: oneUnread });
  assert.deepEqual(singleton.descriptor.expectedInput, {
    pad: 'data/workspace/singleton/doel.test.js',
  });
  const testChild = bindExecutionHandle(singleton.handle, singleton.descriptor.expectedInput, {
    receiptContext: oneUnread, expectedObligation: singleton,
  });
  assert.equal(testChild.args.pad, path.join(dataDir, 'workspace', 'singleton', 'doel.test.js'));
  assert.equal(executionObligationForPlan(parent, unresolved, {
    receiptContext: [...oneUnread, readReceipt(testChild)],
  }), null, 'nul resterende artefacten verzint geen pad');
});

test('een echte genormaliseerde siblingreceipt consumeert exact haar issued requirement', () => {
  const understandingSha256 = 'b'.repeat(64);
  const issuer = { interactiveAuthority: 'explicit', understandingSha256 };
  const parent = planCapabilityAction({
    requirementId: 'r1', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed', semanticInput: { pad: 'consumptie/doel.md' },
  }, issuer);
  const handles = executionHandlesForPlan(parent);
  const written = bindExecutionHandle(executionHandle(handles, 'proof', 'write'), {
    pad: 'consumptie/doel.md', inhoud: '# bewijs\n',
  });
  const writeReceipts = [{
    plan: written,
    result: { name: written.toolName, args: written.args, ok: true, verified: true },
  }];
  const sibling = planCapabilityAction({
    requirementId: 'r2', capabilityId: 'workspace.file.read', operationId: 'read',
    authorization: 'allowed', semanticInput: { pad: 'consumptie/doel.md' },
  }, issuer);
  const obligation = executionObligationForPlan(parent, sibling, {
    receiptContext: writeReceipts,
  });
  const child = bindExecutionHandle(obligation.handle, { pad: 'consumptie/doel.md' }, {
    receiptContext: writeReceipts, expectedPlan: sibling,
  });
  assert.notEqual(child.args.pad, sibling.args.pad,
    'de host heeft het relatieve siblingpad fysiek genormaliseerd');
  const typed = {
    contract: 'ActionResult/v1', tool: child.toolName,
    ok: true, verified: true, changed: false,
    needs_followup: false, partial: false, effect_uncertain: false,
  };
  const readReceipt = {
    plan: child,
    result: {
      name: child.toolName, args: child.args, output: JSON.stringify(typed),
      ok: true, verified: true, changed: false,
      needs_followup: false, partial: false, effect_uncertain: false,
      semanticBinding: {
        understandingSha256, requirementId: 'r1',
        capabilityId: child.capabilityId, operationId: child.operationId,
      },
    },
  };
  const consumed = executionGroupConsumptions({
    parentPlan: parent, candidatePlans: [sibling], receiptContext: [readReceipt],
    understandingSha256, requirementIds: ['r1', 'r2'],
  });
  assert.deepEqual(consumed.map((item) => item.requirementId), ['r2']);
  assert.strictEqual(consumed[0].recorded, readReceipt.result);
});

test('een echte relatieve programmareceipt consumeert haar sibling na werkmapnormalisatie', () => {
  const understandingSha256 = 'c'.repeat(64);
  const issuer = { interactiveAuthority: 'explicit', understandingSha256 };
  const parent = planCapabilityAction({
    requirementId: 'r1', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed', semanticInput: { pad: 'programmaconsumptie/demo.js' },
  }, issuer);
  const handles = executionHandlesForPlan(parent);
  const source = bindExecutionHandle(executionHandle(handles, 'proof', 'write'), {
    pad: 'programmaconsumptie/demo.js', inhoud: 'console.log("ok");\n',
  });
  const testFile = bindExecutionHandle(executionHandle(handles, 'proof', 'write'), {
    pad: 'programmaconsumptie/demo.test.js', inhoud: 'console.log("test ok");\n',
  });
  const writeReceipts = [source, testFile].map((plan) => ({
    plan, result: { name: plan.toolName, args: plan.args, ok: true, verified: true },
  }));
  const sibling = planCapabilityAction({
    requirementId: 'r2', capabilityId: 'workspace.program.execute', operationId: 'execute',
    authorization: 'allowed', semanticInput: {
      programma: 'node', argumenten: ['demo.test.js'], werkmap: 'programmaconsumptie',
    },
  }, issuer);
  const obligation = executionObligationForPlan(parent, sibling, {
    receiptContext: writeReceipts,
  });
  const child = bindExecutionHandle(obligation.handle, sibling.args, {
    receiptContext: writeReceipts, expectedPlan: sibling,
  });
  assert.notEqual(child.args.werkmap, sibling.args.werkmap);
  const typed = {
    contract: 'ActionResult/v1', tool: child.toolName,
    ok: true, verified: true, changed: false,
    needs_followup: false, partial: false, effect_uncertain: false,
  };
  const runReceipt = {
    plan: child,
    result: {
      name: child.toolName, args: child.args, output: JSON.stringify(typed),
      ok: true, verified: true, changed: false,
      needs_followup: false, partial: false, effect_uncertain: false,
      semanticBinding: {
        understandingSha256, requirementId: 'r1',
        capabilityId: child.capabilityId, operationId: child.operationId,
      },
    },
  };
  const consumed = executionGroupConsumptions({
    parentPlan: parent, candidatePlans: [sibling], receiptContext: [runReceipt],
    understandingSha256, requirementIds: ['r1', 'r2'],
  });
  assert.deepEqual(consumed.map((item) => item.requirementId), ['r2']);
});

test('synthetische relatieve workspace gebruikt één turnnamespace voor fixture, build en bewijs', async () => {
  const turnId = 'telegram:990001:2026080803';
  const namespace = 'telegram_990001_2026080803';
  const directory = path.join(dataDir, 'workspace', 'test-artifacts', namespace);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'invoer.csv'), 'waarde\n2\n3\n');
  const context = {
    mode: 'chat', turnId,
    messageContext: { syntheticTest: true },
    turnUnderstanding: { source: { turnId } },
  };

  const listed = JSON.parse(await executeTool('lijst_bestanden', {
    pad: '', zoek: 'invoer.csv',
  }, context));
  assert.equal(listed.ok, true);
  assert.equal(listed.data.length, 1);
  assert.equal(listed.data[0].name.endsWith(`${namespace}/invoer.csv`), true);
  assert.equal(listed.data[0].match, 'name');

  const fixture = JSON.parse(await executeTool('lees_bestand', { pad: 'invoer.csv' }, context));
  assert.equal(fixture.ok, true);
  assert.match(fixture.data, /waarde\n2\n3/u);
  const escaped = JSON.parse(await executeTool('lees_bestand', { pad: '../package.json' }, context));
  assert.equal(escaped.ok, false);
  assert.match(escaped.error, /padtraversal|turnnamespace/u);
  fs.writeFileSync(path.join(dataDir, 'workspace', 'alleen-gewoon.txt'), 'gewone werkruimtebron\n');
  const virtualMiss = JSON.parse(await executeTool('lees_bestand', { pad: 'alleen-gewoon.txt' }, context));
  assert.equal(virtualMiss.ok, false, 'een relatieve synthetic read valt niet terug naar de projectroot');
  const ordinary = JSON.parse(await executeTool(
    'lees_bestand', { pad: 'alleen-gewoon.txt' }, { mode: 'chat' },
  ));
  assert.equal(ordinary.ok, true, 'dezelfde non-synthetic read behoudt de gewone werkruimtesemantiek');
  assert.match(ordinary.data, /gewone werkruimtebron/u);

  const parent = planCapabilityAction({
    requirementId: 'r1', capabilityId: 'workspace.file.write', operationId: 'write',
    authorization: 'allowed', semanticInput: { pad: 'bron.cjs' },
  }, { interactiveAuthority: 'explicit', synthetic: true, turnId });
  const handles = executionHandlesForPlan(parent);
  const writeHandle = executionHandle(handles, 'proof', 'write');
  const runHandle = executionHandle(handles, 'proof', 'run');
  const readHandle = executionHandle(handles, 'proof', 'readback');
  const source = bindExecutionHandle(writeHandle, {
    pad: 'bron.cjs',
    inhoud: `'use strict';\nconst fs = require('node:fs');\nfunction total(file) {\n  return fs.readFileSync(file, 'utf8').trim().split(/\\r?\\n/).slice(1).reduce((sum, value) => sum + Number(value), 0);\n}\nif (require.main === module) console.log(total(process.argv[2]));\nmodule.exports = { total };\n`,
  });
  const testProgram = bindExecutionHandle(writeHandle, {
    pad: 'bron.test.cjs',
    inhoud: `'use strict';\nconst assert = require('node:assert/strict');\nconst { total } = require('./bron.cjs');\nassert.equal(total('invoer.csv'), 5);\nconsole.log('fixture test ok');\n`,
  });
  const executeChild = async (plan) => {
    const result = JSON.parse(await executeTool(plan.toolName, plan.args, context));
    return {
      plan,
      result: {
        name: plan.toolName, args: plan.args, output: JSON.stringify(result),
        ...result,
      },
    };
  };
  const sourceReceipt = await executeChild(source);
  const testReceipt = await executeChild(testProgram);
  const writeReceipts = [sourceReceipt, testReceipt];
  assert.equal(sourceReceipt.result.ok && testReceipt.result.ok, true);

  const testRun = bindExecutionHandle(runHandle, {
    programma: 'node', argumenten: ['bron.test.cjs'], werkmap: '.',
  }, { receiptContext: writeReceipts });
  const sourceRun = bindExecutionHandle(runHandle, {
    programma: 'node', argumenten: ['bron.cjs', 'invoer.csv'], werkmap: '.',
  }, { receiptContext: writeReceipts });
  const testRunReceipt = await executeChild(testRun);
  const sourceRunReceipt = await executeChild(sourceRun);
  assert.equal(testRunReceipt.result.ok, true, JSON.stringify(testRunReceipt.result));
  assert.equal(sourceRunReceipt.result.ok, true, JSON.stringify(sourceRunReceipt.result));
  assert.match(String(testRunReceipt.result.data?.stdout), /fixture test ok/u);
  assert.equal(String(sourceRunReceipt.result.data?.stdout).trim(), '5');

  const readback = bindExecutionHandle(readHandle, { pad: 'bron.cjs' }, {
    receiptContext: writeReceipts,
  });
  const readbackReceipt = await executeChild(readback);
  assert.equal(readbackReceipt.result.ok, true);
  assert.match(String(readbackReceipt.result.data), /function total/u);
  for (const target of [source.args.pad, testProgram.args.pad, testRun.args.werkmap,
    sourceRun.args.werkmap, readback.args.pad]) {
    const relative = path.relative(directory, target);
    assert.equal(relative.startsWith('..') || path.isAbsolute(relative), false, target);
  }
  assert.equal(fs.existsSync(path.join(dataDir, 'workspace', 'bron.cjs')), false);
});

test('run en readback binden aan geverifieerde childreceipts uit exact dezelfde plansessie', () => {
  const parent = issuedWorkspacePlan('proef/reken.py');
  const handles = executionHandlesForPlan(parent);
  const writeHandle = executionHandle(handles, 'proof', 'write');
  const runHandle = executionHandle(handles, 'proof', 'run');
  const readHandle = executionHandle(handles, 'proof', 'readback');
  const source = bindExecutionHandle(writeHandle, {
    pad: 'proef/reken.py', inhoud: 'print(2 + 2)\n',
  });
  const testProgram = bindExecutionHandle(writeHandle, {
    pad: 'proef/test_reken.py', inhoud: 'import reken\n',
  });
  const receipt = (plan) => ({
    plan,
    result: { name: plan.toolName, args: plan.args, ok: true, verified: true },
  });
  assert.throws(() => bindExecutionHandle(runHandle, {
    programma: 'python3', argumenten: ['test_reken.py'], werkmap: 'proef',
  }), /schrijfreceipt/u);
  assert.throws(() => bindExecutionHandle(readHandle, { pad: 'proef/reken.py' }, {
    receiptContext: [{
      plan: source,
      result: {
        ...receipt(source).result,
        partial: true,
        needs_followup: true,
      },
    }],
  }), /schrijfreceipt/u);
  const receiptContext = [receipt(source), receipt(testProgram)];
  const run = bindExecutionHandle(runHandle, {
    programma: 'python3', argumenten: ['test_reken.py'], werkmap: 'data/workspace/proef',
  }, { receiptContext });
  assert.equal(run.toolName, 'voer_uit');
  assert.equal(run.args.werkmap, path.join(dataDir, 'workspace', 'proef'));
  const read = bindExecutionHandle(readHandle, { pad: 'proef/reken.py' }, { receiptContext });
  assert.equal(read.toolName, 'lees_bestand');
  assert.equal(read.args.pad, source.args.pad);
  assert.equal(isIssuedExecutionProofReadback(read), true);
  const forgedReadback = Object.freeze({
    ...read, execution: Object.freeze({ ...read.execution }),
  });
  assert.equal(isIssuedExecutionProofReadback(forgedReadback), false,
    'clonebare proof/readback-velden verlenen geen proof-only explorationbypass');
  assert.equal(isIssuedExecutionProofReadback(parent), false);

  const otherParent = issuedWorkspacePlan('proef/ander.py');
  const otherWrite = executionHandle(executionHandlesForPlan(otherParent), 'proof', 'write');
  const otherChild = bindExecutionHandle(otherWrite, { pad: 'proef/ander.py', inhoud: 'print(1)\n' });
  assert.throws(() => bindExecutionHandle(readHandle, { pad: 'proef/ander.py' }, {
    receiptContext: [receipt(otherChild)],
  }), /schrijfreceipt/u);
  assert.throws(() => bindExecutionHandle(readHandle, { pad: 'proef/reken.py' }, {
    receiptContext: [{ plan: { ...source }, result: receipt(source).result }],
  }), /schrijfreceipt/u);
  assert.throws(() => bindExecutionHandle(runHandle, {
    programma: 'python3', argumenten: ['../proef/test_reken.py'], werkmap: 'proef',
  }, { receiptContext }), /padtraversal|entrypoint/u);
});

test('bestaande CSV- en JSON-invoer zijn veilige read-dependencies zonder schrijfreceipt', () => {
  const directory = path.join(dataDir, 'workspace', 'invoerrollen');
  fs.mkdirSync(path.join(directory, 'input'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'input', 'keukenkosten.csv'), 'datum;bedrag\n2026-08-01;12,40\n');
  fs.writeFileSync(path.join(directory, 'input', 'instellingen.json'), '{"valuta":"EUR"}\n');

  const parent = issuedWorkspacePlan('invoerrollen/keukenkosten.js');
  const handles = executionHandlesForPlan(parent);
  const source = bindExecutionHandle(executionHandle(handles, 'proof', 'write'), {
    pad: 'invoerrollen/keukenkosten.js', inhoud: 'console.log("klaar");\n',
  });
  const receiptContext = [{
    plan: source,
    result: { name: source.toolName, args: source.args, ok: true, verified: true },
  }];
  const sibling = planCapabilityAction({
    requirementId: 'r2', capabilityId: 'workspace.program.execute', operationId: 'execute',
    authorization: 'allowed', semanticInput: {
      programma: 'node',
      argumenten: ['keukenkosten.js', 'input/keukenkosten.csv', 'input/instellingen.json'],
      werkmap: 'invoerrollen',
    },
  }, { interactiveAuthority: 'explicit' });

  const obligation = executionObligationForPlan(parent, sibling, { receiptContext });
  assert.equal(obligation.descriptor.alias, 'execution_proof_run');
  const child = bindExecutionHandle(obligation.handle, sibling.args, {
    receiptContext, expectedPlan: sibling,
  });
  assert.deepEqual(child.args.argumenten, [
    'keukenkosten.js', 'input/keukenkosten.csv', 'input/instellingen.json',
  ]);
  assert.equal(child.args.werkmap, directory);
  assert.equal(receiptContext.length, 1,
    'alleen de programmabron heeft een schrijfreceipt; invoerbestanden nooit');
});

test('onveilige of ontbrekende read-dependencies en ongeschreven entrypoints falen vóór effect', () => {
  const directory = path.join(dataDir, 'workspace', 'invoergrens');
  fs.mkdirSync(path.join(directory, 'input'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'input', 'auth.json'), '{"token":"afgeschermd"}\n');
  fs.mkdirSync(path.join(directory, 'input', 'map.csv'));
  const outside = path.join(dataDir, 'buiten.csv');
  fs.writeFileSync(outside, 'buiten\n');
  fs.symlinkSync(outside, path.join(directory, 'input', 'koppeling.csv'));

  const parent = issuedWorkspacePlan('invoergrens/programma.js');
  const handles = executionHandlesForPlan(parent);
  const source = bindExecutionHandle(executionHandle(handles, 'proof', 'write'), {
    pad: 'invoergrens/programma.js', inhoud: 'console.log("klaar");\n',
  });
  const receiptContext = [{
    plan: source,
    result: { name: source.toolName, args: source.args, ok: true, verified: true },
  }];
  const run = executionHandle(handles, 'proof', 'run');
  const argsFor = (dependency) => ({
    programma: 'node', argumenten: ['programma.js', dependency], werkmap: 'invoergrens',
  });
  for (const [dependency, pattern] of [
    ['input/ontbreekt.csv', /bestaat niet/u],
    ['input/map.csv', /regulier bestand/u],
    ['../buiten.csv', /padtraversal/u],
    [outside, /buiten Eva haar werkruimte/u],
    ['input/koppeling.csv', /buiten Eva haar werkruimte/u],
    ['input/auth.json', /afgeschermde gegevens/u],
  ]) {
    assert.throws(() => bindExecutionHandle(run, argsFor(dependency), { receiptContext }), pattern,
      dependency);
  }
  assert.throws(() => bindExecutionHandle(run, {
    programma: 'node', argumenten: ['input/auth.json'], werkmap: 'invoergrens',
  }, { receiptContext }), /geen gedeclareerd schrijfartefact/u);
  assert.throws(() => bindExecutionHandle(run, {
    programma: 'node', argumenten: ['programma.js'], werkmap: 'invoergrens',
  }), /geverifieerde schrijfreceipt/u);

  const missingInputPlan = planCapabilityAction({
    requirementId: 'r2', capabilityId: 'workspace.program.execute', operationId: 'execute',
    authorization: 'allowed', semanticInput: argsFor('input/ontbreekt.csv'),
  }, { interactiveAuthority: 'explicit' });
  assert.equal(executionObligationForPlan(parent, missingInputPlan, { receiptContext }), null);
});

test('alleen recovery-inspect mag exact een onzeker geschreven sessieartefact onderzoeken', () => {
  const parent = issuedWorkspacePlan('onzeker/doel.py');
  const handles = executionHandlesForPlan(parent);
  const write = bindExecutionHandle(executionHandle(handles, 'proof', 'write'), {
    pad: 'onzeker/doel.py', inhoud: 'print("mogelijk geschreven")\n',
  });
  const uncertain = {
    name: write.toolName, args: write.args, ok: false, verified: false,
    output: JSON.stringify({
      contract: 'ActionResult/v1', tool: write.toolName,
      ok: false, changed: false, verified: false, needs_followup: true,
      failure_class: 'transient', retryable: false, effect_uncertain: true,
    }),
  };
  const receiptContext = [{ plan: write, result: uncertain }];
  const inspect = bindExecutionHandle(executionHandle(handles, 'recovery', 'inspect'), {
    pad: 'onzeker/doel.py',
  }, { receiptContext });
  assert.equal(inspect.toolName, 'lees_bestand');
  assert.equal(inspect.args.pad, write.args.pad);

  assert.throws(() => bindExecutionHandle(executionHandle(handles, 'proof', 'readback'), {
    pad: 'onzeker/doel.py',
  }, { receiptContext }), /geverifieerde schrijfreceipt/u);
  assert.throws(() => bindExecutionHandle(executionHandle(handles, 'proof', 'run'), {
    programma: 'python3', argumenten: ['doel.py'], werkmap: 'onzeker',
  }, { receiptContext }), /geverifieerde schrijfreceipt/u);
  assert.throws(() => bindExecutionHandle(executionHandle(handles, 'recovery', 'inspect'), {
    pad: 'onzeker/sibling.py',
  }, { receiptContext }), /geschreven planartefact/u);

  const forgedPlan = { ...write };
  assert.throws(() => bindExecutionHandle(executionHandle(handles, 'recovery', 'inspect'), {
    pad: 'onzeker/doel.py',
  }, { receiptContext: [{ plan: forgedPlan, result: uncertain }] }), /schrijfreceipt/u);
  const otherParent = issuedWorkspacePlan('onzeker/doel.py');
  const otherHandles = executionHandlesForPlan(otherParent);
  const otherWrite = bindExecutionHandle(executionHandle(otherHandles, 'proof', 'write'), {
    pad: 'onzeker/doel.py', inhoud: 'print("andere sessie")\n',
  });
  assert.throws(() => bindExecutionHandle(executionHandle(handles, 'recovery', 'inspect'), {
    pad: 'onzeker/doel.py',
  }, { receiptContext: [{
    plan: otherWrite,
    result: { ...uncertain, name: otherWrite.toolName, args: otherWrite.args },
  }] }), /schrijfreceipt/u);
  assert.throws(() => bindExecutionHandle(executionHandle(handles, 'recovery', 'inspect'), {
    pad: 'onzeker/doel.py',
  }, { receiptContext: [{
    plan: write,
    result: { ...uncertain, effect_uncertain: true, output: '{}' },
  }] }), /schrijfreceipt/u);
});

test('capabilitybinder valideert geneste schema- en operationele grenzen vóór de handler', () => {
  const base = { titel: 'Marktproef', doel: 'Vind bewijs', werkstromen: [{ vraag: 'Welke bron?' }] };
  const bind = (capability, operation, input) => bindCapabilityInput(capability, operation, input);
  assert.deepEqual(bind('research.workcell.manage', 'start', base), { ...base, bewerking: 'start' });
  assert.throws(() => bind('research.workcell.manage', 'start', { ...base, werkstromen: [{}] }), /ontbrekend veld vraag/u);
  assert.throws(() => bind('research.workcell.manage', 'start', { ...base, werkstromen: [{ vraag: 'x', extra: true }] }), /onbekend veld extra/u);
  assert.throws(() => bind('research.workcell.manage', 'start', {
    ...base, werkstromen: Array.from({ length: 21 }, () => ({ vraag: 'x' })),
  }), /maxItems/u);

  assert.throws(() => bind('robot.control', 'verken', { richting: 'links', duur_ms: 149 }), /minimum/u);
  assert.throws(() => bind('robot.control', 'verken', { richting: 'links', duur_ms: 501 }), /maximum/u);
  assert.deepEqual(bind('robot.control', 'verken', { richting: 'links', duur_ms: 250 }), {
    richting: 'links', duur_ms: 250, actie: 'verken',
  });
  assert.throws(() => bind('goal.portfolio.manage', 'create', {}), /ontbrekend veld titel/u);
});

test('understandingcatalogus is afgeleid, toolnaamvrij en heeft exact één voice-fastlane', () => {
  const catalog = capabilityCatalog();
  const operations = catalog.flatMap((item) => item.operations);
  assert.equal(operations.filter((item) => item.fastlane === 'literal_voice').length, 1);
  const voice = catalog.find((item) => item.id === 'home.voice.announce').operations[0];
  assert.equal(voice.effect, TOOL_EFFECT.voice);
  assert.deepEqual(voice.input.required, ['literalText']);
  assert.equal(voice.input.additionalProperties, false);
  assert.ok(operations.every((item) => typeof item.supportsDeferred === 'boolean'));
  assert.equal(/"(?:tool|name)"\s*:/u.test(JSON.stringify(catalog)), false);

  assert.deepEqual(bindCapabilityInput('home.voice.announce', 'announce', {
    literalText: 'Eten!', targetAlias: 'woonkamer',
  }), { tekst: 'Eten!', doel: 'woonkamer' });
});

test('semantische input, bindings en catalogus zijn recursief immutable', () => {
  const assertDeepFrozen = (value, seen = new Set()) => {
    if (!value || typeof value !== 'object' || seen.has(value)) return;
    seen.add(value);
    assert.equal(Object.isFrozen(value), true);
    for (const child of Object.values(value)) assertDeepFrozen(child, seen);
  };
  assertDeepFrozen(TOOL_REGISTRY);
  assertDeepFrozen(TOOLS);
  for (const tool of TOOL_REGISTRY) for (const provided of tool.provides) {
    for (const operation of provided.operations) {
      assertDeepFrozen(operation.input);
      assertDeepFrozen(operation.binding);
    }
  }
  assertDeepFrozen(capabilityCatalog());
  const nested = capabilityCatalog().find((item) => item.id === 'research.workcell.manage')
    .operations.find((item) => item.id === 'start').input.properties.werkstromen.items;
  assert.throws(() => { nested.properties.vraag.type = 'number'; }, TypeError);
  assert.throws(() => { TOOLS[0].parameters.properties.vraag.type = 'number'; }, TypeError);
});

test('registryvalidator weigert dubbele entries en ongeldige executionbindingen', () => {
  assert.throws(() => validateToolRegistry([...TOOL_REGISTRY, TOOL_REGISTRY[0]]), /dubbele/u);
  const ambiguous = TOOL_REGISTRY.map((tool, index) => index !== 1 ? tool : {
    ...tool, provides: [...tool.provides, TOOL_REGISTRY[0].provides[0]],
  });
  assert.throws(() => validateToolRegistry(ambiguous), /dubbele of ongeldige operation/u);
  const forgedExecution = TOOL_REGISTRY.map((tool) => tool.name !== 'schrijf_bestand' ? tool : ({
    ...tool,
    provides: tool.provides.map((provided) => ({
      ...provided,
      operations: provided.operations.map((operation) => ({
        ...operation,
        execution: {
          ...operation.execution,
          proofOperations: [{
            capabilityId: 'workspace.file.read', operationId: 'mixed_sibling_bestaat_niet', type: 'readback',
          }],
        },
      })),
    })),
  }));
  assert.throws(() => validateToolRegistry(forgedExecution), /operationele metadata/u);
});

test('puntalias leest deterministisch de projectroot en nooit de gelijknamige werkruimteroot', async () => {
  fs.writeFileSync(path.join(dataDir, 'workspace', 'package.json'), '{"shadow":true}\n');
  const result = JSON.parse(await executeTool('lijst_bestanden', { pad: '.' }, { mode: 'chat' }));
  assert.equal(result.ok, true);
  assert.equal(result.data.some((entry) => entry.name === 'src' && entry.type === 'directory'), true);
  assert.equal(result.data.some((entry) => entry.name === 'package.json' && entry.type === 'file'), true);
});

test('projectzoeking vindt veilig gerangschikte inhoud zonder afgeschermde bestanden', async () => {
  const root = path.join(dataDir, 'workspace');
  const nested = path.join(root, 'docs', 'diep');
  fs.mkdirSync(nested, { recursive: true });
  fs.writeFileSync(path.join(nested, 'kaart.md'), [
    '# Kaart',
    'Een losse contractverwijzing.',
    'Hier staan acht doelcontracten met hun geplande status.',
  ].join('\n'));
  fs.writeFileSync(path.join(root, '.env.backup'), 'contracten=mag-nooit-lekken');
  fs.writeFileSync(path.join(root, 'contract-alleen-naam.md'), 'Geen relevante inhoud.');
  fs.mkdirSync(path.join(root, 'docs'), { recursive: true });
  fs.mkdirSync(path.join(root, 'src'), { recursive: true });
  fs.writeFileSync(path.join(root, 'docs', 'een-term.md'), 'Alleen alpha.');
  fs.writeFileSync(path.join(root, 'src', 'volledige-treffer.js'), 'alpha beta gamma');
  fs.writeFileSync(path.join(root, 'groot.md'), `acht contracten gepland ${'x'.repeat(520_000)}`);
  fs.writeFileSync(path.join(root, 'binair.md'), Buffer.from('acht contracten gepland\0verborgen'));
  fs.writeFileSync(path.join(root, 'ongeldig.md'), Buffer.from([0xff, 0xfe, ...Buffer.from('acht contracten gepland')]));
  fs.mkdirSync(path.join(root, '.GIT'), { recursive: true });
  fs.writeFileSync(path.join(root, '.GIT', 'verstopt.md'), 'acht contracten gepland');
  fs.symlinkSync(path.join(nested, 'kaart.md'), path.join(root, 'contract-link.md'));
  const secret = 'search-canary-value-92841';
  const expansionSecret = 'cny92841';
  registerSensitiveValues([
    ['SEARCH_CANARY', secret],
    ['SEARCH_EXPANSION_CANARY_WITH_A_VERY_LONG_LABEL_FOR_BUDGET_TESTING', expansionSecret],
  ]);
  fs.writeFileSync(path.join(root, 'publiek.md'), `acht contracten gepland ${secret} ${`${expansionSecret} `.repeat(42)}`);

  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: root,
    zoek: 'acht|contracten|gepland',
  }, { mode: 'chat' }));
  assert.equal(result.ok, true);
  assert.equal(result.verified, false,
    'een overgeslagen beleidsmap of symlink maakt alleen de manifest-scope onvolledig');
  assert.equal(result.partial, true);
  assert.equal(result.needs_followup, true);
  assert.equal(result.data[0].name.endsWith('docs/diep/kaart.md'), true);
  assert.equal(result.data[0].line, 3);
  assert.match(result.data[0].excerpt, /acht doelcontracten/i);
  assert.equal(result.data.some((entry) => entry.name.includes('.env')), false);
  assert.equal(result.data.some((entry) => /groot|binair|ongeldig|contract-link|\.GIT/.test(entry.name)), false);
  assert.equal(JSON.stringify(result).includes(secret), false);
  assert.match(JSON.stringify(result), /AFGESCHERMD:SEARCH_CANARY/);
  assert.ok(JSON.stringify(result).length <= 3_000);

  const repeated = JSON.parse(await executeTool('lijst_bestanden', {
    pad: root,
    zoek: 'acht|contracten|gepland',
  }, { mode: 'chat' }));
  assert.deepEqual(repeated.data.map((entry) => entry.name), result.data.map((entry) => entry.name));
  const coverageRank = JSON.parse(await executeTool('lijst_bestanden', {
    pad: root,
    zoek: 'alpha|beta|gamma',
  }, { mode: 'chat' }));
  assert.equal(coverageRank.data[0].name.endsWith('src/volledige-treffer.js'), true);
});

test('projectzoeking weigert runtime-data maar houdt de werkruimte beschikbaar', async () => {
  fs.mkdirSync(path.join(dataDir, 'runtime'), { recursive: true });
  fs.writeFileSync(path.join(dataDir, 'runtime', 'status.txt'), 'zoekterm runtime');
  const blocked = JSON.parse(await executeTool('lijst_bestanden', {
    pad: dataDir,
    zoek: 'zoekterm',
  }, { mode: 'chat' }));
  assert.equal(blocked.ok, false);
  assert.match(blocked.error, /(?:runtime-data is afgeschermd|buiten Eva haar project)/i);

  const allowed = JSON.parse(await executeTool('lijst_bestanden', {
    pad: path.join(dataDir, 'workspace'),
    zoek: 'doelcontracten',
  }, { mode: 'chat' }));
  assert.equal(allowed.ok, true);
});

test('bestandsnaamzoeking vindt onbekende binaire extensies zonder inhoud te lezen', async () => {
  const directory = path.join(dataDir, 'workspace', 'naamzoeking');
  const secret = 'BINAIRE-INHOUD-MAG-NIET-UITGELEZEN-WORDEN';
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'camera-invoer.fixtureblob'),
    Buffer.concat([Buffer.from([0xff, 0x00, 0xfe]), Buffer.from(secret)]));
  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: directory, zoek: 'camera-invoer.fixtureblob',
  }, { mode: 'chat' }));
  assert.equal(result.ok, true);
  assert.equal(result.data.length, 1);
  assert.equal(result.data[0].name.endsWith('camera-invoer.fixtureblob'), true);
  assert.equal(result.data[0].match, 'name');
  assert.equal(Object.hasOwn(result.data[0], 'excerpt'), false);
  assert.doesNotMatch(JSON.stringify(result), new RegExp(secret, 'u'));
});

test('afgekorte tooldata blijft geldige expliciet gedeeltelijke ActionResult', async () => {
  const file = path.join(dataDir, 'workspace', 'lang.txt');
  fs.writeFileSync(file, `${'lange regel '.repeat(24)}\n`.repeat(2_000));
  const output = await executeTool('lees_bestand', { pad: file, aantal_regels: 2_000 }, { mode: 'chat' });
  const result = JSON.parse(output);
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.partial, true);
  assert.equal(result.needs_followup, true);
  assert.equal(result.data.truncated, true);
});

test('gerichte bestandzoeking geeft de beste samenhangende passage en geen trefferstort', async () => {
  const file = path.join(dataDir, 'workspace', 'gericht.md');
  const lines = Array.from({ length: 180 }, (_, index) => `regel ${index + 1}: alleen alpha`);
  lines[139] = 'alpha beta gamma: hier begint het relevante overzicht';
  lines[145] = 'Worker Runtime | gepland';
  lines[146] = 'Attention Runtime | grotendeels actief';
  fs.writeFileSync(file, lines.join('\n'));
  const output = await executeTool('lees_bestand', {
    pad: file,
    zoek: 'alpha|beta|gamma',
  }, { mode: 'chat' });
  const result = JSON.parse(output);
  assert.equal(result.partial, false);
  assert.match(result.data, /alpha beta gamma/);
  assert.match(result.data, /Worker Runtime \| gepland/);
  assert.match(result.data, /Attention Runtime \| grotendeels actief/);
  assert.doesNotMatch(result.data, /^1: /m);

  const contextOnly = path.join(dataDir, 'workspace', 'alleen-context.md');
  fs.writeFileSync(contextOnly, 'architectuur gepland maar zonder de expliciete zoekterm');
  const noDirectHit = JSON.parse(await executeTool('lees_bestand', {
    pad: contextOnly,
    zoek: 'unieke-naald',
  }, { mode: 'chat', userText: 'Waar staat de architectuur gepland?' }));
  assert.equal(noDirectHit.ok, false);
  assert.match(noDirectHit.error, /geen treffers/i);
});

test('centrale changed-default bewaart exact de bestaande result-normalisatie', () => {
  const expected = [
    'annuleer_herinnering', 'bedien_huis', 'beheer_addon', 'beheer_agentmail',
    'beheer_aanwezigheid', 'beheer_runtime', 'beheer_toolpakket', 'beheer_werkcel', 'gebruik_robot',
    'leer_gezicht_van_foto', 'maak_vaardigheid', 'onthoud', 'schrijf_bestand',
    'spreek_in_huis', 'stuur_bericht', 'verkenning_bijhouden', 'voer_uit',
    'zet_herinnering',
  ].sort();
  assert.deepEqual(
    TOOL_REGISTRY.map(({ name }) => name).filter(toolChangesByDefault).sort(),
    expected,
  );
});

test('vaste effecten scheiden lezen van fysieke, externe en persistente handelingen', () => {
  assert.equal(toolEffect('huis_status'), TOOL_EFFECT.read);
  assert.equal(toolEffect('bedien_huis'), TOOL_EFFECT.home);
  assert.equal(toolEffect('spreek_in_huis'), TOOL_EFFECT.voice);
  assert.equal(toolEffect('stuur_bericht'), TOOL_EFFECT.external);
  assert.equal(toolEffect('schrijf_bestand'), TOOL_EFFECT.workspace);
  assert.equal(toolEffect('onthoud'), TOOL_EFFECT.persistent);
  assert.equal(toolEffect('bestaat_niet'), 'unknown');
});

test('mixed tools classificeren hun read-operatie en effectoperatie afzonderlijk', () => {
  const cases = [
    ['gebruik_robot', { actie: 'status' }, 'read'],
    ['gebruik_robot', { actie: 'kaart' }, 'read'],
    ['gebruik_robot', { actie: 'start' }, 'home'],
    ['beheer_aanwezigheid', { actie: 'bekijk' }, 'read'],
    ['beheer_aanwezigheid', { actie: 'stel_trackers_in' }, 'home'],
    ['beheer_agentmail', { actie: 'berichten' }, 'read'],
    ['beheer_agentmail', { actie: 'inbox' }, 'read'],
    ['beheer_agentmail', { actie: 'search' }, 'read'],
    ['beheer_agentmail', { actie: 'versturen' }, 'external'],
    ['maak_vaardigheid', { bewerking: 'lijst' }, 'read'],
    ['maak_vaardigheid', { bewerking: 'activeer' }, 'workspace'],
    ['beheer_addon', { bewerking: 'lijst' }, 'read'],
    ['beheer_addon', { bewerking: 'stage' }, 'workspace'],
    ['beheer_werkcel', { bewerking: 'status' }, 'read'],
    ['beheer_werkcel', { bewerking: 'start' }, 'persistent'],
    ['beheer_toolpakket', { bewerking: 'inspecteer' }, 'read'],
    ['beheer_toolpakket', { bewerking: 'status' }, 'read'],
    ['beheer_toolpakket', { bewerking: 'ontdek' }, 'workspace'],
    ['beheer_toolpakket', { bewerking: 'installeer' }, 'workspace'],
    ['beheer_runtime', { bewerking: 'status' }, 'read'],
    ['beheer_runtime', { bewerking: 'stage' }, 'persistent'],
    ['beheer_runtime', { bewerking: 'beproef' }, 'persistent'],
    ['beheer_runtime', { bewerking: 'pas_toe' }, 'runtime'],
    ['beheer_runtime', { bewerking: 'rollback' }, 'runtime'],
    ['doel_bijhouden', { bewerking: 'list' }, 'read'],
    ['doel_bijhouden', { bewerking: 'create' }, 'persistent'],
    ['verkenning_bijhouden', { bewerking: 'lijst' }, 'read'],
    ['verkenning_bijhouden', { bewerking: 'start' }, 'persistent'],
  ];
  for (const [name, args, expected] of cases) {
    assert.equal(toolEffect(name, args), expected, `${name}:${JSON.stringify(args)}`);
  }
});

test('succesvolle fysieke read-operaties van mixed tools zijn centraal geverifieerd', async () => {
  for (const [name, args] of [
    ['maak_vaardigheid', { bewerking: 'lijst' }],
    ['beheer_addon', { bewerking: 'lijst' }],
  ]) {
    const result = JSON.parse(await executeTool(name, args, { mode: 'chat' }));
    assert.equal(result.ok, true, name);
    assert.equal(result.changed, false, name);
    assert.equal(result.verified, true, name);
  }
});

test('onbekende of ontbrekende mixed operaties vallen onder het effect en nooit onder read', () => {
  assert.equal(toolEffect('gebruik_robot', {}), TOOL_EFFECT.home);
  assert.equal(toolEffect('beheer_agentmail', { actie: 'onbekend' }), TOOL_EFFECT.external);
  assert.equal(toolEffect('beheer_werkcel', { bewerking: 'onbekend' }), TOOL_EFFECT.persistent);
});

test('geen effectpolicy bewaart backwards-compatible toegang', () => {
  assert.equal(toolAllowedByPolicy('bedien_huis', {}, null), true);
  assert.equal(toolAllowedByPolicy('bestaat_niet', {}, null), true);
});

test('read-only policy laat reads door en weigert ieder effectdomein', () => {
  for (const name of ['huis_status', 'ha_lees', 'lees_bestand', 'inspecteer_vermogen']) {
    assert.equal(toolAllowedByPolicy(name, {}, 'read_only'), true, name);
  }
  for (const name of ['bedien_huis', 'spreek_in_huis', 'stuur_bericht', 'schrijf_bestand', 'onthoud']) {
    assert.equal(toolAllowedByPolicy(name, {}, 'read_only'), false, name);
  }
  assert.equal(toolAllowedByPolicy('gebruik_robot', { actie: 'status' }, 'read_only'), true);
  assert.equal(toolAllowedByPolicy('gebruik_robot', { actie: 'start' }, 'read_only'), false);
  assert.equal(toolAllowedByPolicy('bestaat_niet', {}, 'read_only'), false);
});

test('read-only authorization volgt voor iedere tool uitsluitend haar centrale effectclassificatie', () => {
  for (const tool of TOOLS) {
    const effect = toolEffect(tool.name, {});
    assert.equal(
      toolAllowedByPolicy(tool.name, {}, 'read_only'),
      effect === TOOL_EFFECT.read,
      `${tool.name}:${effect}`,
    );
  }
});

test('scoped allow en deny behandelen reads standaard als toegestaan en deny als leidend', () => {
  const workspaceOnly = { allowedEffects: ['workspace'] };
  assert.equal(toolAllowedByPolicy('lees_bestand', {}, workspaceOnly), true);
  assert.equal(toolAllowedByPolicy('schrijf_bestand', {}, workspaceOnly), true);
  assert.equal(toolAllowedByPolicy('bedien_huis', {}, workspaceOnly), false);

  const voiceExcepted = { allowedEffects: ['voice', 'home'], deniedEffects: ['voice'] };
  assert.equal(toolAllowedByPolicy('spreek_in_huis', {}, voiceExcepted), false);
  assert.equal(toolAllowedByPolicy('bedien_huis', {}, voiceExcepted), true);
  assert.equal(toolAllowedByPolicy('huis_status', {}, voiceExcepted), true);
  assert.equal(toolAllowedByPolicy('huis_status', {}, { mode: 'allow', allowRead: false }), false);
});

test('executeTool blokkeert een verboden effect vóór de handler en geeft ActionResult policybewijs', async () => {
  const raw = await executeTool('bedien_huis', { doel: 'lamp', actie: 'uit' }, {
    effectPolicy: 'read_only',
  });
  const result = JSON.parse(raw);
  assert.equal(result.contract, 'ActionResult/v1');
  assert.equal(result.tool, 'bedien_huis');
  assert.equal(result.ok, false);
  assert.equal(result.changed, false);
  assert.equal(result.verified, false);
  assert.equal(result.failure_class, 'policy');
  assert.equal(result.retryable, false);
  assert.match(result.error, /effect home.*niet toegestaan/);
});

test('getypeerde post-acceptance onzekerheid blijft door de ActionResult-formatter intact', () => {
  const formatted = JSON.parse(__toolsTest.formatToolResult(
    'beheer_agentmail',
    { actie: 'versturen' },
    JSON.stringify({
      ok: false,
      changed: false,
      verified: false,
      needs_followup: true,
      failure_class: 'delivery_unconfirmed',
      retryable: false,
      effect_uncertain: true,
      reason: 'AgentMail accepteerde de verzending mogelijk, maar gaf geen bericht-id terug.',
    }),
    { mode: 'chat' },
  ));
  assert.deepEqual({
    failure_class: formatted.failure_class,
    retryable: formatted.retryable,
    effect_uncertain: formatted.effect_uncertain,
  }, {
    failure_class: 'delivery_unconfirmed',
    retryable: false,
    effect_uncertain: true,
  });

  const postAcceptance = Object.assign(
    new Error('AgentMail gaf na acceptatie geen bericht-id terug.'),
    { effect_uncertain: true },
  );
  assert.deepEqual(
    __toolsTest.toolFailureFromError(
      'beheer_agentmail', { actie: 'versturen' }, postAcceptance,
    ),
    {
      error: 'AgentMail gaf na acceptatie geen bericht-id terug.',
      failure_class: 'unknown',
      retryable: false,
      effect_uncertain: true,
    },
  );
});

test('een ongetypeerde mutatiefout is onzeker en niet retryable; een leesfout blijft geclassificeerd', () => {
  const mutation = __toolsTest.toolFailureFromError(
    'beheer_agentmail', { actie: 'versturen' }, new Error('vreemde transportfout'),
  );
  assert.deepEqual(mutation, {
    error: 'vreemde transportfout',
    failure_class: 'unknown',
    retryable: false,
    effect_uncertain: true,
  });

  const untypedHandlerResult = JSON.parse(__toolsTest.formatToolResult(
    'bedien_huis',
    { doel: 'light.testlamp', actie: 'uit' },
    JSON.stringify({ ok: false, changed: false, reason: 'onbekende handlerfout' }),
    { mode: 'chat' },
  ));
  assert.equal(untypedHandlerResult.retryable, false);
  assert.equal(untypedHandlerResult.effect_uncertain, true);

  const read = __toolsTest.toolFailureFromError(
    'zoek_internet', { vraag: 'officiële documentatie' }, new Error('timeout'),
  );
  assert.deepEqual(read, {
    error: 'timeout',
    failure_class: 'transient',
    retryable: true,
    effect_uncertain: false,
  });

  const missingFile = __toolsTest.toolFailureFromError(
    'lees_bestand', { pad: 'docs/oud.md' },
    new Error("ENOENT: no such file or directory, lstat 'docs/oud.md'"),
  );
  assert.deepEqual(missingFile, {
    error: "ENOENT: no such file or directory, lstat 'docs/oud.md'",
    failure_class: 'not_found',
    retryable: false,
    effect_uncertain: false,
  });
});

test('executeTool laat onder dezelfde read-only policy een echte readhandler werken', async () => {
  const raw = await executeTool('toon_herinneringen', {}, { effectPolicy: 'read_only' });
  const result = JSON.parse(raw);
  assert.equal(result.contract, 'ActionResult/v1');
  assert.equal(result.tool, 'toon_herinneringen');
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.changed, false);
});

test('werkruimte-alias en korte schrijfroute volgen EVA_DATA_DIR in plaats van de reporoot', async () => {
  const written = JSON.parse(await executeTool('schrijf_bestand', {
    pad: 'live-proef/notitie.txt', inhoud: 'werkt vanuit de ingestelde werkruimte\n',
  }, { mode: 'chat' }));
  assert.equal(written.ok, true);
  assert.equal(written.verified, true);
  assert.equal(written.data.path, 'data/workspace/live-proef/notitie.txt');
  assert.equal(written.data.material_sha256,
    createHash('sha256').update('werkt vanuit de ingestelde werkruimte\n').digest('hex'));
  assert.equal(fs.readFileSync(path.join(dataDir, 'workspace', 'live-proef', 'notitie.txt'), 'utf8'),
    'werkt vanuit de ingestelde werkruimte\n');
  const read = JSON.parse(await executeTool('lees_bestand', {
    pad: 'live-proef/notitie.txt',
  }, { mode: 'chat' }));
  assert.equal(read.ok, true);
  assert.match(read.data, /ingestelde werkruimte/u);
  assert.equal(read.artifact_receipt.sha256, written.data.material_sha256);
  assert.equal(read.artifact_receipt.complete_read, true);
  assert.throws(() => bindCapabilityInput('workspace.file.write', 'write', {
    pad: '../buiten.txt', inhoud: 'nee',
  }), /padtraversal/u);
});

test('episode-uitvoering controleert actuele bronhash en bindt iedere bestandsdependency', async () => {
  const directory = path.join(dataDir, 'workspace', 'continuation-preflight');
  const source = path.join(directory, 'worker.py');
  const marker = path.join(directory, 'executed.txt');
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(source, 'print("versie A")\n');
  const receipt = {
    path: 'data/workspace/continuation-preflight/worker.py',
    sha256: createHash('sha256').update(fs.readFileSync(source)).digest('hex'),
    role: 'source', understandingSha256: 'a'.repeat(64), requirementId: 'r1',
  };
  fs.writeFileSync(source, [
    'import pathlib, sys',
    'pathlib.Path("executed.txt").write_text(pathlib.Path(sys.argv[1]).read_text())',
  ].join('\n'));
  fs.writeFileSync(path.join(directory, 'input.csv'), 'waarde\n7\n');
  const args = {
    programma: 'python3', argumenten: ['worker.py', 'input.csv'],
    werkmap: 'data/workspace/continuation-preflight',
  };
  let result = JSON.parse(await executeTool('voer_uit', args, {
    mode: 'chat', artifactReceipts: [receipt], priorToolResults: [],
  }));
  assert.equal(result.ok, false);
  assert.match(result.error, /episodereceipt/u);
  assert.equal(fs.existsSync(marker), false, 'stale bytes zijn niet uitgevoerd');

  const staleWrite = JSON.parse(await executeTool('schrijf_bestand', {
    pad: 'continuation-preflight/worker.py', inhoud: 'print("versie C")\n',
  }, { mode: 'chat', artifactReceipts: [receipt], priorToolResults: [] }));
  assert.equal(staleWrite.ok, false);
  assert.match(fs.readFileSync(source, 'utf8'), /executed\.txt/u,
    'een stale A-receipt kan externe versie B niet overschrijven');

  receipt.sha256 = createHash('sha256').update(fs.readFileSync(source)).digest('hex');
  result = JSON.parse(await executeTool('voer_uit', args, {
    mode: 'chat', artifactReceipts: [receipt], priorToolResults: [],
  }));
  assert.equal(result.ok, false);
  assert.match(result.error, /fixture-receipt/u);
  assert.equal(fs.existsSync(marker), false, 'een toevallig bestaand siblingbestand is geen authority');

  const existing = path.join(directory, 'bestaand.csv');
  fs.writeFileSync(existing, 'ongewijzigd\n');
  const clobber = JSON.parse(await executeTool('schrijf_bestand', {
    pad: 'continuation-preflight/bestaand.csv', inhoud: 'overschreven\n',
  }, { mode: 'chat', artifactReceipts: [receipt], priorToolResults: [] }));
  assert.equal(clobber.ok, false);
  assert.equal(fs.readFileSync(existing, 'utf8'), 'ongewijzigd\n');

  const fixtureArgs = {
    pad: 'continuation-preflight/input.csv', inhoud: 'waarde\n8\n',
  };
  fs.rmSync(path.join(directory, 'input.csv'));
  const fixtureOutput = await executeTool('schrijf_bestand', fixtureArgs, {
    mode: 'chat', artifactReceipts: [receipt], priorToolResults: [],
  });
  const fixtureResult = JSON.parse(fixtureOutput);
  const firstWrite = {
    name: 'schrijf_bestand', args: fixtureArgs, output: fixtureOutput,
    ok: fixtureResult.ok, verified: fixtureResult.verified,
    partial: fixtureResult.partial, needs_followup: fixtureResult.needs_followup,
  };
  const repairedArgs = { ...fixtureArgs, inhoud: 'waarde\n9\n' };
  const forgedRepair = JSON.parse(await executeTool('schrijf_bestand', repairedArgs, {
    mode: 'chat', artifactReceipts: [receipt], priorToolResults: [{
      ...firstWrite, output: JSON.stringify({ ok: true, verified: true }),
    }],
  }));
  assert.equal(forgedRepair.ok, false);
  assert.equal(fs.readFileSync(path.join(directory, 'input.csv'), 'utf8'), 'waarde\n8\n');
  const repairedOutput = await executeTool('schrijf_bestand', repairedArgs, {
    mode: 'chat', artifactReceipts: [receipt], priorToolResults: [firstWrite],
  });
  const repairedResult = JSON.parse(repairedOutput);
  const repairedWrite = {
    name: 'schrijf_bestand', args: repairedArgs, output: repairedOutput,
    ok: repairedResult.ok, verified: repairedResult.verified,
    partial: repairedResult.partial, needs_followup: repairedResult.needs_followup,
  };
  const fixtureRead = JSON.parse(await executeTool('lees_bestand', {
    pad: 'continuation-preflight/input.csv',
  }, { mode: 'chat', artifactReceipts: [receipt], priorToolResults: [repairedWrite] }));
  assert.equal(fixtureRead.data, 'waarde\n9\n');
  result = JSON.parse(await executeTool('voer_uit', args, {
    mode: 'chat', artifactReceipts: [receipt], priorToolResults: [repairedWrite],
  }));
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(fs.readFileSync(marker, 'utf8'), 'waarde\n9\n');
  for (const option of ['--input=other.csv', '--output=other.py']) {
    fs.rmSync(marker, { force: true });
    result = JSON.parse(await executeTool('voer_uit', {
      ...args, argumenten: ['worker.py', option],
    }, { mode: 'chat', artifactReceipts: [receipt], priorToolResults: [] }));
    assert.equal(result.ok, false, option);
    assert.match(result.error, /bestandsopties/u);
    assert.equal(fs.existsSync(marker), false, `${option} bereikte het programma`);
  }
});

test('leesreceipt hasht exact de geretourneerde bytes bij een A-naar-B race', async () => {
  const file = path.join(dataDir, 'workspace', 'live-proef', 'race.txt');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'versie A\n', 'utf8');
  queueMicrotask(() => fs.writeFileSync(file, 'versie B is langer\n', 'utf8'));
  const read = JSON.parse(await executeTool('lees_bestand', {
    pad: 'live-proef/race.txt',
  }, { mode: 'chat' }));
  assert.equal(read.data, 'versie A\n');
  assert.equal(read.artifact_receipt.sha256,
    createHash('sha256').update('versie A\n').digest('hex'));
  assert.equal(fs.readFileSync(file, 'utf8'), 'versie B is langer\n');
  assert.equal(read.artifact_receipt.complete_read, false);
});

test('toon_werk houdt veel afgerond werk compact en toont een open inhoudsmismatch volledig', async () => {
  const marker = `werkboek-projectie-${Date.now()}`;
  for (let index = 0; index < 24; index += 1) {
    const done = createWorkItem({
      source: marker, owner: marker, title: `${marker}-afgerond-${index}`,
      goal: `Afgerond historisch doel ${index} ${'achtergrond '.repeat(30)}`,
      definitionOfDone: `Historische klaargrens ${index} ${'bewijs '.repeat(25)}`,
      priority: index % 10,
    });
    updateWorkItem(done.id, {
      status: 'done', proofAccepted: true, evidence: `bewijs ${index}`,
      result: `resultaat ${index}`,
    });
  }
  const expected = {
    title: 'Eerste resultaat inkomstenkans afronden',
    goal: 'Maak en controleer een kort, tastbaar klantvoorbeeld voor de gekozen privacyvriendelijke huishoud-automatisering-audit.',
    definitionOfDone: 'Een leesbare eerste versie staat als bestand klaar en is gecontroleerd.',
  };
  const open = createWorkItem({
    source: marker, owner: marker, title: expected.title, goal: expected.goal,
    definitionOfDone: expected.definitionOfDone, priority: 100,
  });

  const receipt = JSON.parse(await executeTool('toon_werk', { cursor: 0 }, { mode: 'chat' }));
  assert.equal(receipt.ok, true);
  assert.equal(receipt.verified, true);
  assert.equal(receipt.partial, false);
  assert.equal(receipt.needs_followup, false);
  assert.equal(receipt.data.contract, 'WorkBookPage/v1');
  assert.ok(Buffer.byteLength(JSON.stringify(receipt.data), 'utf8') < 8_000);
  assert.equal(receipt.data.items[0].id, open.id);
  assert.equal(receipt.data.items[0].status, 'open');
  assert.equal(receipt.data.items[0].title, expected.title);
  assert.equal(receipt.data.items[0].goal, expected.goal);
  assert.equal(receipt.data.items[0].definition_of_done, expected.definitionOfDone);
  assert.equal(receipt.data.pagination.has_more, true);
  assert.equal(receipt.data.pagination.next_cursor, receipt.data.items.length);
  assert.equal(Object.hasOwn(receipt.data.items[0], 'state_json'), false);
  assert.equal(Object.hasOwn(receipt.data.items[0], 'lease_owner'), false);
});

test('werkstart met een hostgebonden actieve episode maakt geen tweede DB-werkregel', async () => {
  const { episode } = createDurableWork({
    source: 'web', owner: 'Jaap', title: 'Bestaand eerste resultaat',
    goal: 'Maak precies één bestaand resultaat af.',
    definitionOfDone: 'Het bestaande resultaat is gecontroleerd.', priority: 80,
  }, {
    originKind: 'user', originChannel: 'web',
    originConversationId: 'web:production-shaped-active-episode',
    originMessageId: 'msg-active-episode', requester: 'Jaap',
    deliveryPolicy: 'required', deliveryTarget: 'Jaap',
  });
  const before = {
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  };
  const receipt = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'start', titel: 'Hallucinerend tweede vervolg',
    doel: 'Maak een inhoudelijk ander tweede plan.',
    definitie_klaar: 'Een tweede bestand bestaat.',
  }, {
    mode: 'chat', channel: 'web', sender: 'Jaap',
    conversationId: 'web:production-shaped-active-episode',
    originMessageId: 'msg-active-episode', activeGoalEpisodeId: episode.id,
  }));
  const after = {
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  };

  assert.equal(receipt.ok, false);
  assert.equal(receipt.changed, false);
  assert.match(receipt.error, /al episode/u);
  assert.equal(receipt.failure_class, 'conflict');
  assert.equal(receipt.retryable, false);
  assert.equal(receipt.effect_uncertain, false);
  assert.deepEqual(after, before);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_work_items WHERE title=?')
    .get('Hallucinerend tweede vervolg').count, 0);
});

function economicFamilyWorkFixture(suffix) {
  const commitment = {
    title: `Economisch gezinswerk ${suffix}`,
    outcome: 'Een intern resultaat is aantoonbaar bruikbaar voor het gezin.',
    definitionOfDone: 'Het interne resultaat is gecontroleerd en teruggelezen.',
    nextAction: 'Werk de eerstvolgende interne stap uit.',
  };
  const { workItem, episode } = createDurableWork({
    source: 'autonomy', owner: null, title: commitment.title,
    goal: commitment.outcome, nextStep: commitment.nextAction,
    definitionOfDone: commitment.definitionOfDone, priority: 80,
  }, {
    originKind: 'self', originChannel: 'autonomy', requester: null,
    deliveryPolicy: 'milestone', deliveryTarget: null,
  });
  const economicGoal = db.prepare(
    "SELECT id FROM eva_goals WHERE goal_key='economic_value'",
  ).get();
  assert.ok(economicGoal, 'de economic_value-richting moet bestaan');
  const handoff = {
    contract: 'ExplorationEconomicHandoff/v1',
    sourceOutcomeKey: createHash('sha256').update(`outcome:${suffix}`).digest('hex'),
    explorationId: 90_000 + Number(workItem.id),
    fromRevision: 3,
    toRevision: 4,
    evidenceSha256: createHash('sha256').update(`evidence:${suffix}`).digest('hex'),
    goalKey: 'economic_value',
    authority: {
      internalWork: true, familyDelivery: true,
      externalContact: false, publication: false, spend: false,
    },
    commitment,
  };
  db.transaction(() => {
    db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
      .run(JSON.stringify({ exploration_economic_handoff: handoff }), workItem.id);
    db.prepare('UPDATE eva_projects SET goal_id=? WHERE id=?')
      .run(economicGoal.id, episode.project_id);
    db.prepare('UPDATE eva_goal_episodes SET goal_id=? WHERE id=?')
      .run(economicGoal.id, episode.id);
  })();
  return { workId: workItem.id, episodeId: episode.id };
}

function economicFamilyMessagePlan(tekst, aan = undefined) {
  const semanticInput = aan === undefined ? { tekst } : { tekst, aan };
  const plan = planCapabilityAction({
    authorization: 'allowed', requirementId: 'r1',
    capabilityId: 'message.deliver', operationId: 'send', semanticInput,
  }, {
    mode: 'autonomy', fullAutonomy: true, understandingSha256: 'f'.repeat(64),
  });
  assert.ok(plan, 'de echte message.deliver-planner moet een plan uitgeven');
  return plan;
}

function economicFamilyExecutionContext(plan, work) {
  return {
    mode: 'autonomy', issuedPlan: plan,
    activeWorkId: work.workId, activeGoalEpisodeId: work.episodeId,
    turnUnderstanding: { delivery: { target: 'family' } },
    turnId: `economic-family-${work.workId}`,
  };
}

test('economic-handoff weigert een bezoeker vóór de Telegram-messenger', async () => {
  const suffix = `visitor-${Date.now()}`;
  const visitorName = `Bezoeker ${suffix}`;
  const visitorId = upsertPerson(visitorName, { telegram_id: 910_001, notes: 'bezoeker' });
  setPersonHouseholdRole(visitorId, 'visitor', { source: 'test' });
  const work = economicFamilyWorkFixture(suffix);
  const plan = economicFamilyMessagePlan('Het gecontroleerde resultaat is klaar.', visitorName);
  let messengerCalls = 0;
  registerMessenger(async () => {
    messengerCalls += 1;
    return { ok: false, accepted: false };
  });
  try {
    const receipt = JSON.parse(await executeTool(
      plan.toolName, plan.args, economicFamilyExecutionContext(plan, work),
    ));
    assert.equal(receipt.ok, false);
    assert.equal(receipt.changed, false);
    assert.equal(receipt.failure_class, 'policy');
    assert.match(receipt.error, /exact gebonden familie-update/u);
    assert.equal(messengerCalls, 0);
  } finally {
    registerMessenger(null);
  }
});

test('economic-handoff gezinsbroadcast bindt alleen household members', async () => {
  const suffix = `broadcast-${Date.now()}`;
  const memberName = `Gezinslid ${suffix}`;
  const visitorName = `Gast ${suffix}`;
  const memberChatId = '920001';
  const visitorChatId = '920002';
  const memberId = upsertPerson(memberName, { telegram_id: memberChatId, notes: 'bewoner' });
  const visitorId = upsertPerson(visitorName, { telegram_id: visitorChatId, notes: 'bezoeker' });
  setPersonHouseholdRole(memberId, 'household_member', { source: 'test' });
  setPersonHouseholdRole(visitorId, 'visitor', { source: 'test' });
  const work = economicFamilyWorkFixture(suffix);
  const plan = economicFamilyMessagePlan('Het gecontroleerde gezinsresultaat is klaar.');
  const calls = [];
  registerMessenger(async (text, recipient, options) => {
    calls.push({ text, recipient, options: {
      ...options, expectedTargetIds: [...(options.expectedTargetIds || [])],
    } });
    return { ok: false, accepted: false, info: 'testtransport stopt na de effectgrens' };
  });
  try {
    await executeTool(plan.toolName, plan.args, economicFamilyExecutionContext(plan, work));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].recipient, 'gezin');
    assert.equal(calls[0].options.broadcast, true);
    assert.equal(calls[0].options.householdOnly, true);
    assert.ok(calls[0].options.expectedTargetIds.includes(memberChatId));
    assert.equal(calls[0].options.expectedTargetIds.includes(visitorChatId), false);
  } finally {
    registerMessenger(null);
  }
});

test('gezinsaflevering weigert mapping-drift vóór de Telegram-messenger', async () => {
  const suffix = `drift-${Date.now()}`;
  const memberId = upsertPerson(`Driftgezinslid ${suffix}`, {
    telegram_id: 930_001, notes: 'bewoner',
  });
  setPersonHouseholdRole(memberId, 'household_member', { source: 'test' });
  const expectedTargetIds = householdMembers()
    .filter((person) => String(person.telegram_id || '').trim())
    .map((person) => String(person.telegram_id)).sort();
  db.prepare('UPDATE people SET telegram_id=? WHERE id=?').run(930_002, memberId);
  let messengerCalls = 0;
  registerMessenger(async () => {
    messengerCalls += 1;
    return { ok: false, accepted: false };
  });
  try {
    const result = await sendRegisteredMessage('Dit bericht mag niet vertrekken.', null, {
      broadcast: true, householdOnly: true, expectedTargetIds,
    });
    assert.equal(result.ok, false);
    assert.equal(result.accepted, false);
    assert.equal(result.code, 'TELEGRAM_HOUSEHOLD_TARGET_DRIFT');
    assert.equal(messengerCalls, 0);
  } finally {
    registerMessenger(null);
  }
});

test('gezinsfoto weigert mapping-drift vóór de Telegram-fotozender', async () => {
  const suffix = `photo-drift-${Date.now()}`;
  const memberId = upsertPerson(`Fotogezinslid ${suffix}`, {
    telegram_id: 940_001, notes: 'bewoner',
  });
  setPersonHouseholdRole(memberId, 'household_member', { source: 'test' });
  const expectedTargetIds = householdMembers()
    .filter((person) => String(person.telegram_id || '').trim())
    .map((person) => String(person.telegram_id)).sort();
  db.prepare('UPDATE people SET telegram_id=? WHERE id=?').run(940_002, memberId);
  let senderCalls = 0;
  registerPhotoSender(async () => {
    senderCalls += 1;
    return { ok: false, accepted: false };
  });
  try {
    const result = await sendRegisteredPhoto(Buffer.from('jpeg'), 'image/jpeg',
      'Deze foto mag niet vertrekken.', null, {
        broadcast: true, householdOnly: true, expectedTargetIds,
      });
    assert.equal(result.ok, false);
    assert.equal(result.accepted, false);
    assert.equal(result.code, 'TELEGRAM_HOUSEHOLD_TARGET_DRIFT');
    assert.equal(senderCalls, 0);
  } finally {
    registerPhotoSender(null);
  }
});
