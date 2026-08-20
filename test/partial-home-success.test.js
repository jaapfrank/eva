import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-partial-home-success-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';
process.env.HA_URL = 'http://partial-home.test';
process.env.HA_TOKEN = 'partial-home-test-token';
process.env.ALLOW_HOME_CONTROL = 'true';
process.env.NODE_ENV = 'test';

await import('./agent-test-composition.js');

const {
  __projectRouteTest,
  handleMessageDetailed,
  recordDeliveredReply,
} = await import('../src/mind/agent.js');
const {
  bindPriorVerifiedHomeContextPlan,
  priorVerifiedHomeContinuationResourceReceipt,
} = await import('../src/mind/tools.js');
const {
  boundedVerifiedHomePartialEffect,
  homeEffectClaimFeedback,
} = await import('../src/mind/communication.js');
const { db, logTimeline } = await import('../src/db.js');
const { goalEpisodeById } = await import('../src/mind/portfolio.js');
const {
  beginHttpIngress,
  issueVerifiedHttpResultAuthority,
  prepareHttpResponse,
  settleHttpIngress,
} = await import('../src/web/httpingress.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const reachable = Array.from({ length: 8 }, (_, index) => ({
  entity_id: `light.keuken_${index + 1}`,
  name: `Keukenlamp ${index + 1}`,
  state: 'off',
}));
const unavailable = [
  { entity_id: 'light.eettafel_links', name: 'Eettafel links', state: 'unavailable' },
  { entity_id: 'light.eettafel_rechts', name: 'Eettafel rechts', state: 'unavailable' },
];

const httpAckPair = generateKeyPairSync('ec', { namedCurve: 'P-256' });
const httpAckJwk = httpAckPair.publicKey.export({ format: 'jwk' });
const httpClientAckPublicKey = Object.freeze({
  contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
  kty: 'EC', crv: 'P-256', x: httpAckJwk.x, y: httpAckJwk.y,
});

function productWebIngress({ conversationId, messageId, text }) {
  const intent = {
    contract: 'PublicHttpIntent/v1',
    route: '/api/chat',
    sender: 'Jaap',
    rawText: text,
    conversationId,
    syntheticTest: false,
    clientAckPublicKey: httpClientAckPublicKey,
  };
  const ingress = beginHttpIngress({
    channel: 'web', conversationId, requestId: messageId, intent,
  });
  assert.equal(ingress.kind, 'started');
  return {
    httpIngressAuthority: ingress.preparationAuthority,
    httpIngressIntent: intent,
  };
}

function settleProductWebIngress(result, ingress) {
  const resultAuthority = issueVerifiedHttpResultAuthority(
    result, ingress.httpIngressAuthority,
  );
  assert.ok(resultAuthority);
  const prepared = prepareHttpResponse(ingress.httpIngressAuthority, resultAuthority);
  assert.ok(prepared.settlementAuthority);
  const settled = settleHttpIngress(prepared.settlementAuthority);
  assert.equal(settled.kind, 'settled');
}

function kitchenReceipt(overrides = {}) {
  const args = { doel: 'keuken', actie: 'uit' };
  const payload = {
    contract: 'ActionResult/v1',
    tool: 'bedien_huis',
    ok: true,
    changed: true,
    verified: true,
    needs_followup: true,
    partial: true,
    failure_class: 'partial_result',
    retryable: false,
    effect_uncertain: false,
    human_message: 'Keuken staat uit op alle 8 bereikbare onderdelen. 2 lampen zijn offline: Eettafel links, Eettafel rechts.',
    data: {
      ok: true,
      changed: true,
      verified: true,
      partial: true,
      needs_followup: true,
      entity_id: 'light.keuken',
      name: 'Keuken',
      service: 'light.turn_off',
      before: 'on',
      after: 'off',
      message: 'Keuken staat uit op alle 8 bereikbare onderdelen. 2 lampen zijn offline: Eettafel links, Eettafel rechts.',
      evidence: {
        readback: { entity_id: 'light.keuken', name: 'Keuken', state: 'off' },
        assessment: {
          ok: false,
          reachableVerified: true,
          partial: true,
          entity_id: 'light.keuken',
          state: 'off',
          target_state: 'off',
          expected: 'off',
          isGroup: true,
          members: [...reachable, ...unavailable],
          reachable,
          unavailable,
          unresolved: unavailable,
        },
      },
    },
  };
  const mergedPayload = structuredClone(payload);
  if (overrides.payload) Object.assign(mergedPayload, overrides.payload);
  if (overrides.data) Object.assign(mergedPayload.data, overrides.data);
  if (overrides.assessment) {
    Object.assign(mergedPayload.data.evidence.assessment, overrides.assessment);
  }
  return {
    name: 'bedien_huis',
    args,
    output: JSON.stringify(mergedPayload),
    ok: mergedPayload.ok,
    changed: mergedPayload.changed,
    verified: mergedPayload.verified,
    needs_followup: mergedPayload.needs_followup,
    partial: mergedPayload.partial,
    failure_class: mergedPayload.failure_class,
    retryable: mergedPayload.retryable,
    effect_uncertain: mergedPayload.effect_uncertain,
    ...overrides.recorded,
  };
}

function forgedKitchenReceipt(mutate) {
  const receipt = kitchenReceipt();
  const payload = JSON.parse(receipt.output);
  mutate(payload, receipt);
  receipt.output = JSON.stringify(payload);
  return receipt;
}

function homeGroupReceipt({
  target = 'keuken', entityId = 'light.keuken', name = 'Keuken', action = 'uit',
  unavailableMembers = unavailable,
} = {}) {
  const receipt = kitchenReceipt();
  const payload = JSON.parse(receipt.output);
  const expectedState = action === 'aan' ? 'on' : 'off';
  const before = action === 'aan' ? 'off' : 'on';
  const service = action === 'aan' ? 'light.turn_on' : 'light.turn_off';
  const reachableMembers = reachable.map((member) => ({ ...member, state: expectedState }));
  const unavailableCopies = unavailableMembers.map((member) => ({ ...member }));
  const message = `${name} staat ${action} op alle ${reachableMembers.length} bereikbare onderdelen. ${unavailableCopies.length} lampen zijn offline.`;
  receipt.args = { doel: target, actie: action };
  payload.human_message = message;
  Object.assign(payload.data, {
    entity_id: entityId,
    name,
    service,
    before,
    after: expectedState,
    message,
  });
  Object.assign(payload.data.evidence.readback, {
    entity_id: entityId,
    name,
    state: expectedState,
  });
  Object.assign(payload.data.evidence.assessment, {
    entity_id: entityId,
    state: expectedState,
    target_state: expectedState,
    expected: expectedState,
    members: [...reachableMembers, ...unavailableCopies],
    reachable: reachableMembers,
    unavailable: unavailableCopies,
    unresolved: unavailableCopies,
  });
  receipt.output = JSON.stringify(payload);
  return receipt;
}

test('de echte keukenreceipt is een begrensd geverifieerd deelsucces, geen generieke failure', () => {
  const receipt = kitchenReceipt();
  const plan = { toolName: 'bedien_huis', args: receipt.args };

  assert.equal(boundedVerifiedHomePartialEffect(receipt, receipt.args), true);
  assert.equal(__projectRouteTest.requiredToolSatisfied('bedien_huis', [receipt]), true);
  assert.equal(
    __projectRouteTest.turnRequirementSatisfied(
      'bedien_huis', [receipt], 'Zet het licht uit in de keuken.',
    ),
    true,
  );
  assert.equal(__projectRouteTest.plannedResultComplete({
    plan,
    recorded: receipt,
    completed: true,
    adaptiveRecorded: null,
    investigationRequired: false,
    investigationComplete: false,
  }), true);

  const replySeed = __projectRouteTest.humanResultText(receipt.output);
  assert.equal(replySeed, JSON.parse(receipt.output).human_message);
  assert.doesNotMatch(replySeed, /Nog open|gegevens niet volledig|weet het antwoord nog niet zeker/iu);
  assert.doesNotMatch(replySeed, /[\u2013\u2014]/u);

  const contribution = __projectRouteTest.contributionFromTools(
    [receipt], 'chat', 'Zet het licht uit in de keuken.', {
      durable: true,
      plannedRequiredTools: ['bedien_huis'],
      plannedTurnKind: 'action',
      plannedActionRequested: true,
      autoBoundOneShot: {
        autoBoundThisTurn: true,
        turnSucceeded: true,
        actionRequested: true,
        requiredTools: ['bedien_huis'],
        plans: [plan],
      },
    },
  );
  assert.equal(contribution, receipt,
    'het geslaagde effect blijft eindbewijs en wordt geen phantom continuation');
});

test('replycontrole eist de exacte offline-beperking en laat geen alles-gelukt-claim door', () => {
  const receipt = kitchenReceipt();
  const intent = { doel: 'keuken', actie: 'uit' };

  for (const answer of [
    'De acht bereikbare keukenlampen zijn uit. Twee lampen zijn offline, dus die kon ik niet controleren.',
    'De acht bereikbare keukenlampen zijn uit. Twee offline lampen kon ik niet controleren.',
    'De acht bereikbare keukenlampen zijn uit. Die twee zijn onbereikbaar.',
    'Alle acht lampen die bereikbaar zijn, staan uit. De twee eettafellampen zijn offline, dus die kan ik niet bevestigen.',
    'Keuken staat uit op alle acht bereikbare lampen. Twee lampen zijn offline, dus die kon ik niet controleren.',
  ]) {
    assert.equal(homeEffectClaimFeedback(answer, intent, [receipt]), '', answer);
  }

  for (const answer of [
    'De keuken is uit.',
    'De lampen zijn uit.',
    'Twee lampen zijn offline.',
    'Keuken is uit. Twee lampen zijn offline.',
    'Acht lampen zijn uit. Twee lampen zijn offline.',
    'De zeven bereikbare keukenlampen zijn uit. Twee lampen zijn offline.',
    'De acht bereikbare keukenlampen zijn aan. Twee lampen zijn offline.',
    'De acht bereikbare keukenlampen zijn niet uit. Twee lampen zijn offline.',
    'De acht bereikbare keukenlampen zijn uit noch aan. Twee lampen zijn offline.',
    'Niet alle acht bereikbare keukenlampen zijn uit. Twee lampen zijn offline.',
    'Keuken staat uit en acht bereikbare lampen zijn aanwezig. Twee lampen zijn offline.',
    'De acht bereikbare keukenlampen zijn uit. Twee lampen zijn bereikbaar, maar één lamp is offline.',
    'De acht bereikbare keukenlampen zijn uit. Eén lamp is offline, maar twee lampen zijn bereikbaar.',
    'De acht bereikbare keukenlampen zijn uit. Twee lampen zijn bereikbaar en één lamp is offline.',
    'De acht bereikbare keukenlampen zijn uit. Drie lampen zijn offline, waaronder twee keukenlampen.',
    'De acht bereikbare keukenlampen zijn uit. Eén lamp is offline en de andere twee lampen zijn bereikbaar.',
    'De acht bereikbare keukenlampen zijn aanwezig en de keuken staat uit. Twee lampen zijn offline.',
    'De acht bereikbare keukenlampen zijn aanwezig, maar de woonkamerlampen zijn uit. Twee lampen zijn offline.',
    'De acht bereikbare keukenlampen zijn aan of de keuken is uit. Twee lampen zijn offline.',
    'De acht bereikbare keukenlampen en twee ganglampen zijn uit. Twee lampen zijn offline.',
    'Oké.',
    'Ik heb alles in de keuken uitgezet.',
    'Alles in de keuken is uit; twee lampen zijn offline.',
    'Klaar, twee lampen zijn offline.',
    'Gelukt. Twee lampen zijn offline.',
    'Het is gelukt. Twee lampen zijn offline.',
    'Gereed. Twee lampen zijn offline.',
    'Voltooid. Twee lampen zijn offline.',
    'Afgerond. Twee lampen zijn offline.',
    'Uitgevoerd. Twee lampen zijn offline.',
    'Resultaat bereikt. Twee lampen zijn offline.',
    'Het resultaat is bereikt. Twee lampen zijn offline.',
    'Dat is gelukt. Twee lampen zijn offline.',
    'De opdracht is uitgevoerd. Twee lampen zijn offline.',
    'Het gewenste resultaat is bereikt. Twee lampen zijn offline.',
    'De opdracht is klaar. Twee lampen zijn offline.',
    'De opdracht is gereed. Twee lampen zijn offline.',
    'De opdracht is voltooid. Twee lampen zijn offline.',
    'De opdracht is afgerond. Twee lampen zijn offline.',
    'Keuken is uit. Twee minuten geleden zag ik dat een lamp offline is.',
    'Keuken is uit. Twee minuten geleden keek ik; een lamp is offline.',
  ]) {
    assert.match(homeEffectClaimFeedback(answer, intent, [receipt]),
      /bereikbare|offline|begrens/iu, answer);
  }
});

function boundedFollowupRead(overrides = {}) {
  const group = {
    target: 'keuken', entityId: 'light.keuken', name: 'Keuken',
    ...(overrides.group || {}),
  };
  const boundary = {
    contract: 'BoundedHomePartialContinuation/v1',
    source_completion_record_sha256: 'a'.repeat(64),
    entity_id: group.entityId,
    expected_state: 'off',
    reachable_count: 8,
    unavailable_count: 2,
    unavailable_names: ['Eettafel links', 'Eettafel rechts'],
  };
  const data = {
    ok: true,
    verified: true,
    changed: false,
    partial: false,
    needs_followup: false,
    entity_id: group.entityId,
    name: group.name,
    state: 'off',
    prior_action: {
      verified: true,
      changed: true,
      action: 'uit',
      requested_target: group.target,
      resulting_state: 'off',
      turn_id: 'turn:bounded-partial',
      completion_record_sha256: 'a'.repeat(64),
      target_binding_sha256: '',
      bounded_partial: boundary,
    },
    current: { entity_id: group.entityId, name: group.name, state: 'off' },
    message: `${group.name} staat als groep nog uit. Bij de vorige actie waren acht lampen bereikbaar en twee niet bereikbaar, dus niet alle tien zijn toen individueel bevestigd.`,
  };
  const payload = {
    contract: 'ActionResult/v1',
    tool: 'ha_lees',
    ok: true,
    changed: false,
    verified: true,
    needs_followup: false,
    partial: false,
    effect_uncertain: false,
    human_message: data.message,
    data,
  };
  if (overrides.boundary) Object.assign(boundary, overrides.boundary);
  if (overrides.priorAction) Object.assign(data.prior_action, overrides.priorAction);
  if (overrides.data) Object.assign(data, overrides.data);
  if (overrides.payload) Object.assign(payload, overrides.payload);
  return {
    name: 'ha_lees',
    args: { entity_id: group.entityId, ...(overrides.args || {}) },
    output: JSON.stringify(payload),
    ok: payload.ok,
    changed: payload.changed,
    verified: payload.verified,
    needs_followup: payload.needs_followup,
    partial: payload.partial,
    effect_uncertain: payload.effect_uncertain,
    ...(overrides.recorded || {}),
  };
}

let boundedFixtureSequence = 0;
function ledgerBackedBoundedFollowupRead(overrides = {}) {
  boundedFixtureSequence += 1;
  const sequence = boundedFixtureSequence;
  const priorTurnId = `turn:bounded-partial:${sequence}`;
  const group = {
    target: 'keuken', entityId: 'light.keuken', name: 'Keuken',
    ...(overrides.group || {}),
  };
  const original = homeGroupReceipt({
    ...group,
    unavailableMembers: overrides.unavailableMembers || unavailable,
  });
  const originalAssessment = JSON.parse(original.output).data.evidence.assessment;
  const originalBinding = {
    understandingSha256: sequence.toString(16).padStart(64, '0'),
    requirementId: 'r1',
    addressesRequirementIds: ['r1'],
    basisReceiptIds: [],
    capabilityId: 'home.device.control',
    operationId: 'set',
    role: 'primary',
    executionType: null,
  };
  const originalExecution = __projectRouteTest.recordTurnToolObservation({
    turnId: priorTurnId,
    tool: original.name,
    args: original.args,
    semanticBinding: originalBinding,
    output: original.output,
  });
  const readBinding = {
    ...originalBinding,
    understandingSha256: (sequence + 4096).toString(16).padStart(64, '0'),
    capabilityId: 'home.entity.read',
    operationId: 'read',
  };
  const receipt = boundedFollowupRead({
    ...overrides,
    group,
    boundary: {
      source_completion_record_sha256: originalExecution.completion_record_sha256,
      entity_id: group.entityId,
      reachable_count: originalAssessment.reachable.length,
      unavailable_count: originalAssessment.unavailable.length,
      unavailable_names: originalAssessment.unavailable.map((member) => (
        String(member.name || member.entity_id).slice(0, 96)
      )),
      ...(overrides.boundary || {}),
    },
    priorAction: {
      turn_id: priorTurnId,
      completion_record_sha256: originalExecution.completion_record_sha256,
      ...(overrides.priorAction || {}),
    },
    recorded: {
      semanticBinding: readBinding,
      ...(overrides.recorded || {}),
    },
  });
  __projectRouteTest.recordTurnToolObservation({
    turnId: `turn:bounded-followup:${sequence}`,
    tool: receipt.name,
    args: receipt.args,
    semanticBinding: readBinding,
    output: receipt.output,
  });
  return receipt;
}

test('bounded follow-up bewaakt actuele groepstaat en de historische 8/2-grens', () => {
  const receipt = ledgerBackedBoundedFollowupRead();
  const qualified = 'Ja, de keukengroep staat nog uit. Bij de vorige actie waren acht lampen bereikbaar en twee lampen niet bereikbaar, dus niet alle tien zijn toen individueel bevestigd.';
  assert.equal(homeEffectClaimFeedback(qualified, null, [receipt]), '');

  for (const answer of [
    'Alles is uit.',
    'Gelukt.',
    'Ja, alles is gelukt.',
  ]) {
    assert.match(
      homeEffectClaimFeedback(answer, null, [receipt]),
      /acht|twee|begrenz|bereikbaar|individueel/iu,
      answer,
    );
  }
});

test('bounded follow-up bindt de actuele groepsclaim uitsluitend aan exacte ledger-aliassen', () => {
  const history = 'Bij de vorige actie waren acht lampen bereikbaar en twee lampen niet bereikbaar, dus niet alle tien zijn toen individueel bevestigd.';
  const kitchen = ledgerBackedBoundedFollowupRead();
  const living = ledgerBackedBoundedFollowupRead({
    group: {
      target: 'woonkamer', entityId: 'light.woonkamer', name: 'Woonkamer',
    },
  });

  assert.equal(
    homeEffectClaimFeedback(`De keukengroep staat nog uit. ${history}`, null, [kitchen]),
    '',
  );
  assert.equal(
    homeEffectClaimFeedback(`De woonkamergroep staat nog uit. ${history}`, null, [living]),
    '',
  );
  for (const [receipt, answer] of [
    [kitchen, `De groep in de woonkamer staat nog uit. ${history}`],
    [living, `De keukengroep staat nog uit. ${history}`],
  ]) {
    assert.match(
      homeEffectClaimFeedback(answer, null, [receipt]),
      /actuele|begrenz|bewijs|niet bevestigd/iu,
      answer,
    );
  }
});

test('bounded namen gebruiken exact dezelfde 96-tekenprojectie en padded namen falen gesloten', () => {
  const longName = `Lange offline lamp ${'x'.repeat(120)}`;
  const history = 'Keuken staat nog uit. Bij de vorige actie waren acht lampen bereikbaar en twee lampen niet bereikbaar, dus niet alle tien zijn toen individueel bevestigd.';
  const longNames = ledgerBackedBoundedFollowupRead({
    unavailableMembers: [
      { entity_id: 'light.lang_offline', name: longName, state: 'unavailable' },
      unavailable[1],
    ],
  });
  assert.equal(homeEffectClaimFeedback(history, null, [longNames]), '');
  assert.equal(
    JSON.parse(longNames.output).data.prior_action.bounded_partial.unavailable_names[0],
    longName.slice(0, 96),
  );

  const padded = ledgerBackedBoundedFollowupRead({
    unavailableMembers: [
      { entity_id: 'light.padded_offline', name: '  Padded offline lamp  ', state: 'unavailable' },
      unavailable[1],
    ],
  });
  assert.match(
    homeEffectClaimFeedback(history, null, [padded]),
    /begrenz|bewijs|ongeldig|niet bevestigd/iu,
  );
});

test('forged bounded follow-upmetadata maakt nooit replyauthority', () => {
  const qualified = 'Ja, de keukengroep staat nog uit. Bij de vorige actie waren acht lampen bereikbaar en twee lampen niet bereikbaar, dus niet alle tien zijn toen individueel bevestigd.';
  const malformed = [
    ledgerBackedBoundedFollowupRead({ boundary: { reachable_count: 0 } }),
    ledgerBackedBoundedFollowupRead({ boundary: { unavailable_count: -1 } }),
    ledgerBackedBoundedFollowupRead({ boundary: { expected_state: 'on' } }),
    ledgerBackedBoundedFollowupRead({ boundary: { entity_id: 'light.woonkamer' } }),
    ledgerBackedBoundedFollowupRead({
      boundary: { source_completion_record_sha256: 'b'.repeat(64) },
    }),
    ledgerBackedBoundedFollowupRead({
      boundary: { unavailable_names: ['Eettafel links'] },
    }),
    ledgerBackedBoundedFollowupRead({
      boundary: { unavailable_names: ['Eettafel links', 'Eettafel links'] },
    }),
    ledgerBackedBoundedFollowupRead({ data: { entity_id: 'light.woonkamer' } }),
    ledgerBackedBoundedFollowupRead({ data: { state: 'on' } }),
    ledgerBackedBoundedFollowupRead({ args: { entity_id: 'light.woonkamer' } }),
    ledgerBackedBoundedFollowupRead({ recorded: { partial: true } }),
  ];
  for (const receipt of malformed) {
    assert.match(
      homeEffectClaimFeedback(qualified, null, [receipt]),
      /begrenz|bewijs|ongeldig|niet bevestigd/iu,
      receipt.output,
    );
  }

  const forgedReadBinding = {
    understandingSha256: 'c'.repeat(64),
    requirementId: 'r1',
    addressesRequirementIds: ['r1'],
    basisReceiptIds: [],
    capabilityId: 'home.entity.read',
    operationId: 'read',
    role: 'primary',
    executionType: null,
  };
  const forgedOrdinaryRead = boundedFollowupRead({
    recorded: { semanticBinding: forgedReadBinding },
  });
  const forgedHash = ledgerBackedBoundedFollowupRead({
    boundary: { source_completion_record_sha256: 'b'.repeat(64) },
    priorAction: { completion_record_sha256: 'b'.repeat(64) },
    recorded: { semanticBinding: forgedReadBinding },
  });
  for (const receipt of [forgedOrdinaryRead, forgedHash]) {
    assert.match(
      homeEffectClaimFeedback(qualified, null, [receipt]),
      /begrenz|bewijs|ongeldig|niet bevestigd/iu,
      'caller-controlled ha_lees JSON en een synthetische hash mogen geen replyauthority maken',
    );
  }

  const legacyComplete = boundedFollowupRead();
  const legacyPayload = JSON.parse(legacyComplete.output);
  delete legacyPayload.data.prior_action.bounded_partial;
  legacyComplete.output = JSON.stringify(legacyPayload);
  assert.equal(homeEffectClaimFeedback('De keuken staat uit.', null, [legacyComplete]), '',
    'een complete legacy-read wordt niet tot bounded-partialreceipt gepromoveerd');
});

let duplicatePriorSequence = 0;
function duplicatePriorHomeResourceReceipt(receipts) {
  duplicatePriorSequence += 1;
  const conversationId = `web:duplicate-prior-home:${duplicatePriorSequence}`;
  const turnId = `turn:duplicate-prior-home:${duplicatePriorSequence}`;
  const understandingSha256 = (duplicatePriorSequence + 8192).toString(16).padStart(64, '0');
  const binding = {
    understandingSha256,
    requirementId: 'r1',
    addressesRequirementIds: ['r1'],
    basisReceiptIds: [],
    capabilityId: 'home.device.control',
    operationId: 'set',
    role: 'primary',
    executionType: null,
  };
  logTimeline('turn_understanding', 'exacte prior-home understanding', {
    internalOnly: true,
    conversationId,
    turnId,
    understanding: { sourceSha256: understandingSha256 },
  });
  const executions = receipts.map((receipt) => (
    __projectRouteTest.recordTurnToolObservation({
      turnId,
      tool: receipt.name,
      args: receipt.args,
      semanticBinding: binding,
      output: receipt.output,
    })
  ));
  logTimeline('chat_out', 'Eva -> Jaap: prior-home resultaat', {
    conversationId,
    turnId,
  });
  return priorVerifiedHomeContinuationResourceReceipt({
    conversationId,
    requester: 'Jaap',
    userText: 'En, gelukt?',
    executions,
  });
}

test('dubbele prior-home executions voor dezelfde entity falen gesloten', () => {
  const repeated = homeGroupReceipt();
  const opposite = homeGroupReceipt({ action: 'aan' });
  assert.equal(boundedVerifiedHomePartialEffect(repeated, repeated.args), true);
  assert.equal(boundedVerifiedHomePartialEffect(opposite, opposite.args), true);

  assert.equal(
    duplicatePriorHomeResourceReceipt([repeated, structuredClone(repeated)]),
    null,
    'herhaalde geldige records mogen geen arbitraire prior kiezen',
  );
  assert.equal(
    duplicatePriorHomeResourceReceipt([repeated, opposite]),
    null,
    'tegengestelde geldige records voor dezelfde entity zijn semantisch ambigu',
  );
});

test('de prior-homebinder verbreedt geen niet-uitgegeven leesplan', () => {
  const context = {
    conversationId: 'web:forged-prior-home-plan',
    requester: 'Jaap',
    userText: 'En, gelukt?',
    understanding: { sha256: 'd'.repeat(64), dialogueAct: 'continue' },
    executions: [],
  };
  const plans = [
    {
      capabilityId: 'home.entity.read', operationId: 'read', toolName: 'ha_lees',
      requirementId: 'r1', args: { entity_id: 'light.woonkamer' },
    },
    {
      capabilityId: 'home.entity.read', operationId: 'set', toolName: 'ha_lees',
      requirementId: 'r1', args: { entity_id: 'light.keuken' },
    },
    {
      capabilityId: 'home.entity.read', operationId: 'read', toolName: 'huis_status',
      requirementId: 'r1', args: { entity_id: 'light.keuken' },
    },
    {
      capabilityId: 'home.entity.read', operationId: 'read', toolName: 'ha_lees',
      requirementId: 'r1', args: { entity_id: 'light.keuken' },
    },
  ];
  for (const plan of plans) {
    assert.equal(bindPriorVerifiedHomeContextPlan(plan, context), plan,
      'een andere entity/operation/tool of een niet-uitgegeven plan blijft ongebonden');
  }
});

test('alleen de exacte home-groepsvorm krijgt de uitzondering', () => {
  const expected = { doel: 'keuken', actie: 'uit' };
  const malformed = [
    kitchenReceipt({ recorded: { effect_uncertain: true } }),
    kitchenReceipt({ recorded: { retryable: true } }),
    kitchenReceipt({ recorded: { args: { doel: 'woonkamer', actie: 'uit' } } }),
    kitchenReceipt({ payload: { changed: false }, recorded: { changed: false } }),
    kitchenReceipt({ data: { service: 'switch.turn_off' } }),
    kitchenReceipt({ payload: { human_message: 'Een afwijkende claim.' } }),
    kitchenReceipt({ assessment: { unavailable: [] } }),
    kitchenReceipt({ assessment: {
      reachable: [{ ...reachable[0], state: 'on' }],
    } }),
    kitchenReceipt({ assessment: { reachableVerified: false } }),
    kitchenReceipt({ data: { after: 'on' } }),
    kitchenReceipt({ data: { before: undefined } }),
    kitchenReceipt({ data: { before: 1 } }),
    forgedKitchenReceipt((payload) => {
      payload.data.entity_id = 'light..keuken';
      payload.data.evidence.assessment.entity_id = 'light..keuken';
      payload.data.evidence.readback.entity_id = 'light..keuken';
    }),
    forgedKitchenReceipt((payload) => {
      const invalid = { ...payload.data.evidence.assessment.reachable[0],
        entity_id: 'light.keuken-lamp' };
      payload.data.evidence.assessment.reachable[0] = invalid;
      payload.data.evidence.assessment.members[0] = invalid;
    }),
    forgedKitchenReceipt((payload) => {
      payload.data.evidence.assessment.members[0].state = 'on';
    }),
    forgedKitchenReceipt((payload) => {
      const overlap = {
        ...payload.data.evidence.assessment.reachable[0], state: 'unknown',
      };
      payload.data.evidence.assessment.unavailable.push(overlap);
      payload.data.evidence.assessment.unresolved.push(overlap);
    }),
    forgedKitchenReceipt((payload) => {
      payload.data.evidence.assessment.unresolved[0].state = 'unknown';
    }),
  ];
  for (const receipt of malformed) {
    assert.equal(boundedVerifiedHomePartialEffect(receipt, expected), false);
    assert.equal(__projectRouteTest.requiredToolSatisfied('bedien_huis', [receipt]), false);
    assert.equal(__projectRouteTest.plannedResultComplete({
      plan: { toolName: 'bedien_huis', args: expected },
      recorded: receipt,
      completed: true,
      investigationRequired: false,
    }), false);
  }

  const genericPartial = {
    name: 'zoek_internet', ok: true, verified: true, changed: false,
    partial: true, needs_followup: true, effect_uncertain: false,
    output: JSON.stringify({
      contract: 'ActionResult/v1', tool: 'zoek_internet', ok: true, verified: true,
      changed: false, partial: true, needs_followup: true, effect_uncertain: false,
      data: { results: [] },
    }),
  };
  assert.equal(__projectRouteTest.requiredToolSatisfied('zoek_internet', [genericPartial]), false);

  const unprovenPluralState = kitchenReceipt({
    assessment: { reachableVerified: false },
  });
  for (const pluralState of [
    'De lampen zijn uit.',
    'De lampen staan uit.',
    'De lampen waren uit.',
    'De lampen stonden uit.',
  ]) {
    assert.match(
      homeEffectClaimFeedback(pluralState, expected, [unprovenPluralState]),
      /niet voor precies|niet bevestigd/iu,
      pluralState,
    );
  }
});

function completedModelResponse(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function homeControlUnderstanding(text) {
  return completedModelResponse([{
    type: 'function_call', call_id: 'partial-home-understanding',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'command',
      goal: {
        statement: 'Zet het keukenlicht uit.',
        outcome: 'Het keukenlicht staat aantoonbaar uit waar het bereikbaar is.',
        beneficiary: 'requester',
      },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{
        capabilityId: 'home.device.control', operationId: 'set', polarity: 'request',
        semanticInput: { target: 'keuken', desiredState: 'off' },
        sourceSpans: [{ start: 0, end: text.length }],
      }],
      requestedEffects: ['home'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' },
      proofNeeds: ['accepted_result'], ambiguities: [],
      firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
    }),
  }]);
}

function naturalPartialHomeReply() {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{
      type: 'output_text',
      text: 'De acht bereikbare keukenlampen zijn uit. — De twee eettafellampen zijn offline, dus die kan ik niet controleren.',
    }],
  }]);
}

function homeFollowupUnderstanding(text) {
  return completedModelResponse([{
    type: 'function_call', call_id: 'partial-home-followup-understanding',
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'continue',
      goal: {
        statement: 'Controleer of de vorige keukenactie gelukt is.',
        outcome: 'De actuele toestand van exact hetzelfde keukenlicht is gecontroleerd.',
        beneficiary: 'requester',
      },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99,
      actions: [{
        capabilityId: 'home.entity.read', operationId: 'read', polarity: 'request',
        semanticInput: { entityId: 'light.keuken' },
        sourceSpans: [{ start: 0, end: text.length }],
      }],
      requestedEffects: ['read'], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' },
      proofNeeds: ['accepted_result'], ambiguities: [],
      firstDecision: { kind: 'inspect', requirementIds: ['r1'], replyDraft: null },
    }),
  }]);
}

function naturalPartialHomeFollowupReply() {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{
      type: 'output_text',
      text: 'Ja, de keukengroep staat nog uit. Bij de vorige actie waren acht lampen bereikbaar en twee lampen niet bereikbaar, dus niet alle tien zijn toen individueel bevestigd.',
    }],
  }]);
}

function unboundedPartialHomeFollowupReply() {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text: 'Alles is uit.' }],
  }]);
}

function completedHomeFollowupStep() {
  return completedModelResponse([{
    type: 'function_call', call_id: 'partial-home-followup-complete',
    name: 'commit_turn_step', arguments: JSON.stringify({
      kind: 'complete',
      addressesRequirementIds: ['r1'],
      basisReceiptIds: ['s1'],
      action: null,
      reasonCode: 'verify',
    }),
  }]);
}

test('productieroute bezorgt het begrensde resultaat en laat na delivery geen wachtend werk achter', async () => {
  const text = 'Zet het licht uit in de keuken.';
  const conversationId = 'web:partial-home-success';
  const firstMessageId = 'partial-home-command:0001';
  const followupMessageId = 'partial-home-followup:0002';
  const followupText = 'En, gelukt?';
  const memberIds = [...reachable, ...unavailable].map(({ entity_id: entityId }) => entityId);
  let currentStates = [
    {
      entity_id: 'light.keuken', state: 'on',
      attributes: { friendly_name: 'Keuken', entity_id: memberIds },
    },
    ...reachable.map((member) => ({
      entity_id: member.entity_id, state: 'on',
      attributes: { friendly_name: member.name },
    })),
    ...unavailable.map((member) => ({
      entity_id: member.entity_id, state: member.state,
      attributes: { friendly_name: member.name },
    })),
  ];
  const serviceCalls = [];
  let modelCalls = 0;
  let understandingCalls = 0;
  let replyCalls = 0;
  const originalFetch = globalThis.fetch;
  const mockFetch = async (input, options = {}) => {
    const url = new URL(String(input));
    if (url.hostname === 'partial-home.test') {
      if (url.pathname === '/api/states') {
        return new Response(JSON.stringify(currentStates), { status: 200 });
      }
      if (url.pathname.startsWith('/api/states/')) {
        const entityId = decodeURIComponent(url.pathname.slice('/api/states/'.length));
        const state = currentStates.find((candidate) => candidate.entity_id === entityId);
        return state
          ? new Response(JSON.stringify(state), { status: 200 })
          : new Response('', { status: 404 });
      }
      if (url.pathname === '/api/services/light/turn_off' && options.method === 'POST') {
        const body = JSON.parse(String(options.body || '{}'));
        serviceCalls.push(body);
        currentStates = currentStates.map((state) => (
          state.entity_id === 'light.keuken'
            || (memberIds.includes(state.entity_id)
              && !['unavailable', 'unknown'].includes(state.state))
            ? { ...state, state: 'off' }
            : state
        ));
        return new Response('[]', { status: 200 });
      }
      throw new Error(`onverwachte HA-route: ${options.method || 'GET'} ${url.pathname}`);
    }
    modelCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const modelTool = body.tools?.[0]?.name || null;
    if (modelTool === 'commit_turn_understanding') {
      understandingCalls += 1;
      return understandingCalls === 1
        ? homeControlUnderstanding(text)
        : homeFollowupUnderstanding('En, gelukt?');
    }
    if (modelTool === 'commit_turn_step') return completedHomeFollowupStep();
    replyCalls += 1;
    return replyCalls === 1
      ? naturalPartialHomeReply()
      : replyCalls === 2
        ? unboundedPartialHomeFollowupReply()
        : naturalPartialHomeFollowupReply();
  };
  globalThis.fetch = mockFetch;

  const firstIngress = productWebIngress({
    conversationId, messageId: firstMessageId, text,
  });
  let turn;
  try {
    turn = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: {
        conversationId,
        messageId: firstMessageId,
        identityTrusted: true,
        ...firstIngress,
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(modelCalls, 2);
  assert.deepEqual(serviceCalls, [{ entity_id: 'light.keuken' }]);
  assert.deepEqual(turn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed, tool.partial, tool.needs_followup,
  ]), [['bedien_huis', true, true, true, true, true]]);
  assert.equal(turn.tools[0].failure_class, 'partial_result');
  assert.equal(turn.tools[0].retryable, false);
  assert.doesNotMatch(turn.reply, /Nog open|gegevens niet volledig|weet het antwoord nog niet zeker/iu);
  assert.doesNotMatch(turn.reply, /[\u2013\u2014]/u);
  assert.match(turn.reply, /acht/iu);
  assert.match(turn.reply, /twee.+offline/iu);
  assert.equal(turn.contribution?.tool, 'bedien_huis');
  assert.equal(turn.continuation, null);
  assert.ok(Number.isSafeInteger(Number(turn.workItemId)));
  assert.ok(Number.isSafeInteger(Number(turn.goalEpisodeId)));
  const pendingEpisode = goalEpisodeById(turn.goalEpisodeId);
  const pendingWork = db.prepare('SELECT status,evidence FROM eva_work_items WHERE id=?')
    .get(turn.workItemId);
  assert.equal(pendingEpisode.state, 'delivery_pending');
  assert.equal(pendingWork.status, 'open');
  assert.match(`${pendingEpisode.proof_summary} ${pendingWork.evidence}`, /8 bereikbare/iu);
  assert.match(`${pendingEpisode.proof_summary} ${pendingWork.evidence}`, /2 lampen.+offline/iu);
  assert.doesNotMatch(`${pendingEpisode.proof_summary} ${pendingWork.evidence}`,
    /kantoor|woonkamer/iu);
  const priorExecutions = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE turn_id=? AND tool='bedien_huis' ORDER BY execution_seq`).all(turn.turnId);
  assert.equal(priorExecutions.length, 1,
    'de eerste durable webbeurt vormt zelf exact één prior-actionreceipt');
  assert.equal(priorExecutions[0].status, 'done');
  assert.equal(priorExecutions[0].output, turn.tools[0].output);
  assert.deepEqual(JSON.parse(priorExecutions[0].semantic_binding),
    turn.tools[0].semanticBinding);

  settleProductWebIngress(turn, firstIngress);
  assert.equal(recordDeliveredReply(turn, { accepted: true, transport: 'test' }), true);
  assert.equal(goalEpisodeById(turn.goalEpisodeId).state, 'closed');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?')
    .get(turn.workItemId).status, 'done');
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM eva_work_items
    WHERE id=? AND status IN ('open','blocked')`).get(turn.workItemId).count, 0);

  globalThis.fetch = mockFetch;
  const followupIngress = productWebIngress({
    conversationId, messageId: followupMessageId, text: followupText,
  });
  let followup;
  try {
    followup = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: followupText,
      messageContext: {
        conversationId,
        messageId: followupMessageId,
        identityTrusted: true,
        ...followupIngress,
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(followup.tools.map((tool) => tool.name), ['ha_lees']);
  assert.deepEqual(followup.tools[0].args, { entity_id: 'light.keuken' });
  assert.equal(followup.tools[0].ok, true);
  assert.equal(followup.tools[0].verified, true);
  assert.equal(followup.tools[0].changed, false);
  assert.equal(followup.tools[0].semanticBinding?.capabilityId, 'home.entity.read');
  assert.equal(followup.tools[0].semanticBinding?.operationId, 'read');
  assert.deepEqual(followup.tools[0].semanticBinding?.addressesRequirementIds, ['r1']);
  assert.deepEqual(followup.tools[0].semanticBinding?.basisReceiptIds, []);
  const followupEnvelope = JSON.parse(followup.tools[0].output);
  assert.deepEqual(followupEnvelope.data.prior_action.bounded_partial, {
    contract: 'BoundedHomePartialContinuation/v1',
    source_completion_record_sha256:
      followupEnvelope.data.prior_action.completion_record_sha256,
    entity_id: 'light.keuken',
    expected_state: 'off',
    reachable_count: 8,
    unavailable_count: 2,
    unavailable_names: ['Eettafel links', 'Eettafel rechts'],
  });
  assert.doesNotMatch(followupEnvelope.human_message, /opdracht is gelukt/iu,
    'ook de deterministische tool-fallback blijft begrensd');
  assert.equal(followup.continuation, null);
  assert.equal(followup.workItemId, null);
  assert.equal(followup.goalEpisodeId, null);
  assert.notEqual(followup.reply, 'Alles is uit.');
  assert.match(followup.reply, /groep.+uit/iu);
  assert.match(followup.reply, /acht.+bereikbaar/iu);
  assert.match(followup.reply, /twee.+niet bereikbaar/iu);
  assert.match(followup.reply, /vorige actie|toen/iu);
  assert.equal(replyCalls, 3, 'de brede eerste modelreply is deterministisch gerepareerd');
  assert.doesNotMatch(followup.reply, /Nog open|ticket|werkitem|weet.+niet zeker/iu);
  assert.doesNotMatch(followup.reply, /[\u2013\u2014]/u);
  assert.deepEqual(serviceCalls, [{ entity_id: 'light.keuken' }],
    'de vervolgvraag leest alleen terug en herhaalt de huisactie niet');
  settleProductWebIngress(followup, followupIngress);
  assert.equal(recordDeliveredReply(followup, { accepted: true, transport: 'test' }), true);
});
