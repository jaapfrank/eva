import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';

import {
  buildMonitoringMeasurementFact,
  issueMonitoringContract,
  issueMonitoringFactBinding,
  issueMonitoringLifecycleBinding,
  issueMonitoringOutcomeReceipt,
  issueMonitoringPendingDelivery,
  monitoringDeliveryMatchesAuthority,
  monitoringDeliveryRetrySchedule,
  monitoringDeliveryTransportLimits,
  monitoringDeliveryWireCurrent,
  monitoringOutcomeRequestRecord,
  monitoringOutcomeResponseRecord,
  monitoringStateFitsDurableBudget,
  normalizeMonitoringContract,
  normalizeMonitoringMeasurementFact,
  normalizeMonitoringLifecycleBinding,
  normalizeMonitoringPendingDelivery,
  normalizeMonitoringOutcomeDecision,
  renderMonitoringTerminalText,
} from '../src/mind/monitoringcontract.js';

const SHA = 'a'.repeat(64);
const READ_AT = '2026-08-15T10:00:01.000Z';
const UPDATED = '2026-08-15T10:00:00.000Z';
const verifyLedgerReceipt = (item) => item.monitoringLedger;

function receipt(entityId, data, overrides = {}) {
  const output = JSON.stringify({
    contract: 'ActionResult/v1', tool: 'ha_lees', ok: true, changed: false,
    verified: true, needs_followup: false, partial: false,
    effect_uncertain: false, data: { entity_id: entityId, ...data },
  });
  const outputSha256 = createHash('sha256').update(output).digest('hex');
  return {
    name: 'ha_lees', args: { entity_id: entityId }, output,
    ok: true, verified: true, changed: false, partial: false,
    needs_followup: false, effect_uncertain: false, at: READ_AT,
    semanticBinding: {
      understandingSha256: SHA, requirementId: overrides.requirementId || 'r1',
      capabilityId: 'home.entity.read', operationId: 'read',
    },
    monitoringLedger: {
      executionKey: `monitoring-read:${entityId}`,
      intentRecordSha256: createHash('sha256').update(`intent:${entityId}`).digest('hex'),
      outputSha256,
      completionRecordSha256: createHash('sha256')
        .update(`completion:${entityId}`).digest('hex'),
      finishedTs: READ_AT,
    },
    ...overrides,
  };
}

function bind(entityId, data, options = {}) {
  const requirementId = options.requirementId || 'r1';
  return issueMonitoringFactBinding({
    requirementId, entityId, name: options.name || data.name || entityId,
    condition: options.condition || 'Waarschuw wanneer deze meting de grens bereikt.',
    conditionRole: 'guard', operand: options.operand || 'subject',
    sourceText: options.sourceText || 'deze temperatuurmeting',
    sourceUnderstandingSha256: SHA,
    freshness: { maxAgeMs: 60_000 },
    receipt: receipt(entityId, data, { requirementId }),
    verifyInitialReceipt: verifyLedgerReceipt,
  });
}

function measurement(entityId, data, options = {}) {
  const requirementId = options.requirementId || 'r1';
  const binding = bind(entityId, data, options);
  return buildMonitoringMeasurementFact(
    binding,
    receipt(entityId, data, { requirementId }),
    {
      now: Date.parse(READ_AT), verifyLedgerReceipt,
      sourceUnderstandingSha256: SHA,
    },
  );
}

test('MonitoringFactBinding/v1 kiest exact het domeinveld en de eigen temperatuureenheid', () => {
  const climate = bind('climate.woonkamer', {
    name: 'Woonkamer', state: 'heat', current_temperature: 20.1,
    temperature_unit: '°C', unit: '°F', updated: UPDATED,
  });
  assert.equal(climate.sourceField, 'current_temperature');
  assert.equal(climate.unitField, 'temperature_unit');
  assert.equal(climate.expectedUnit, '°C');
  assert.equal(climate.quantityKind, 'temperature');
  assert.equal(climate.contract, 'MonitoringFactBinding/v1');
  assert.match(climate.bindingSha256, /^[a-f0-9]{64}$/u);

  const weather = bind('weather.thuis', {
    name: 'Weer thuis', state: 'sunny', temperature: 16.4,
    temperature_unit: '°C', unit: 'km/h', updated: UPDATED,
  });
  assert.equal(weather.sourceField, 'temperature');
  assert.equal(weather.expectedUnit, '°C');

  const climateState = bind('climate.woonkamer', {
    name: 'Woonkamer', state: 'heat', current_temperature: 20.1,
    temperature_unit: '°C', updated: UPDATED,
  }, {
    sourceText: 'wanneer de verwarming uit staat',
  });
  assert.equal(climateState.sourceField, 'state');
  assert.equal(climateState.valueType, 'string');
  assert.equal(climateState.expectedUnit, null);

  const weatherState = bind('weather.thuis', {
    name: 'Weer thuis', state: 'sunny', temperature: 16.4,
    temperature_unit: '°C', updated: UPDATED,
  }, {
    sourceText: 'wanneer het regent',
  });
  assert.equal(weatherState.sourceField, 'state');
  assert.equal(weatherState.valueType, 'string');
  assert.equal(weatherState.expectedUnit, null);

  const humidity = bind('sensor.luchtvochtigheid', {
    name: 'Luchtvochtigheid', state: '64.2', unit: '%', updated: UPDATED,
  });
  assert.equal(humidity.sourceField, 'state');
  assert.equal(humidity.valueType, 'number');
  assert.equal(humidity.expectedUnit, '%');

  assert.equal(bind('climate.kapot', {
    state: 'heat', temperature_unit: '°C', updated: UPDATED,
  }, { sourceText: 'de temperatuur' }), null,
  'climate-state heat mag nooit als temperatuur worden gebonden');
  assert.equal(issueMonitoringFactBinding({
    requirementId: 'r1', entityId: 'climate.woonkamer', name: 'Woonkamer',
    condition: 'Temperatuur bereikt grens.', conditionRole: 'guard', operand: 'subject',
    sourceText: 'temperatuur', sourceUnderstandingSha256: SHA,
    freshness: { maxAgeMs: 60_000 },
    receipt: receipt('climate.woonkamer', {
      name: 'Woonkamer', state: 'heat', current_temperature: 20.1,
      temperature_unit: '°C', updated: UPDATED,
    }),
  }), null, 'een structureel gelijk initieel receipt mist hostauthority');
});

test('MonitoringMeasurementFact/v1 accepteert alleen de volledige exacte verse binding', () => {
  const binding = bind('climate.woonkamer', {
    name: 'Woonkamer', state: 'heat', current_temperature: 20.1,
    temperature_unit: '°C', unit: '°F', updated: UPDATED,
  });
  const exact = receipt('climate.woonkamer', {
    name: 'Woonkamer', state: 'heat', current_temperature: 20.1,
    temperature_unit: '°C', unit: '°F', updated: UPDATED,
  });
  const fact = buildMonitoringMeasurementFact(binding, exact, {
    now: Date.parse(READ_AT), verifyLedgerReceipt,
    sourceUnderstandingSha256: SHA,
  });
  assert.equal(fact.contract, 'MonitoringMeasurementFact/v1');
  assert.equal(fact.value, 20.1);
  assert.equal(fact.unit, '°C');
  assert.equal(fact.observedAt, READ_AT);
  assert.equal(fact.sourceUpdatedAt, UPDATED);
  assert.equal(fact.readAt, READ_AT);
  assert.equal(fact.outputSha256, exact.monitoringLedger.outputSha256);
  assert.match(fact.factSha256, /^[a-f0-9]{64}$/u);
  assert.equal(fact.factId, `monitoring-fact:${fact.factSha256}`);

  for (const [label, mutate] of [
    ['unit drift', (item) => { JSON.parse(item.output); return { ...item,
      output: item.output.replace('"temperature_unit":"°C"', '"temperature_unit":"°F"') }; }],
    ['missing unit', (item) => ({ ...item,
      output: item.output.replace(',"temperature_unit":"°C"', '') })],
    ['object value', (item) => ({ ...item,
      output: item.output.replace('"current_temperature":20.1', '"current_temperature":{"n":20.1}') })],
    ['NaN text', (item) => ({ ...item,
      output: item.output.replace('"current_temperature":20.1', '"current_temperature":"NaN"') })],
    ['partial', (item) => ({ ...item, partial: true })],
    ['wrong entity', (item) => ({ ...item, args: { entity_id: 'climate.anders' } })],
    ['wrong requirement', (item) => ({ ...item, semanticBinding: {
      ...item.semanticBinding, requirementId: 'r2',
    } })],
    ['missing ledger', (item) => ({ ...item, monitoringLedger: null })],
  ]) assert.equal(buildMonitoringMeasurementFact(binding, mutate(exact), {
    now: Date.parse(READ_AT), verifyLedgerReceipt,
    sourceUnderstandingSha256: SHA,
  }), null, label);

  const stale = receipt('climate.woonkamer', {
    name: 'Woonkamer', state: 'heat', current_temperature: 20.1,
    temperature_unit: '°C', updated: '2026-08-15T09:58:00.000Z',
  });
  const unchangedButFreshlyRead = buildMonitoringMeasurementFact(binding, stale, {
    now: Date.parse(READ_AT), verifyLedgerReceipt,
    sourceUnderstandingSha256: SHA,
  });
  assert.equal(unchangedButFreshlyRead.observedAt, READ_AT,
    'observation time komt uit de gevalideerde duurzame readledger');
  assert.equal(unchangedButFreshlyRead.sourceUpdatedAt, '2026-08-15T09:58:00.000Z',
    'HA last_updated blijft uitsluitend bronprovenance');
  assert.equal(buildMonitoringMeasurementFact(binding, exact, {
    now: Date.parse(READ_AT),
    sourceUnderstandingSha256: SHA,
  }), null, 'een zelfconsistent caller-ledger zonder hostverifier heeft geen authority');
  assert.deepEqual(
    normalizeMonitoringMeasurementFact(normalizeMonitoringMeasurementFact(fact)),
    fact,
  );
  assert.deepEqual(normalizeMonitoringMeasurementFact({
    ...fact, observedAt: '2026-08-15T12:00:01+02:00',
  }), fact, 'een equivalente timestamp normaliseert vóór de hashtoets');
});

test('monitoringoordeel bevat uitsluitend verdict, terminal en de volledige actuele factset', () => {
  const facts = [measurement('sensor.een', {
    name: 'Een', state: '1', unit: '%', updated: UPDATED,
  }), measurement('sensor.twee', {
    name: 'Twee', state: '2', unit: '%', updated: UPDATED,
  }, { requirementId: 'r2' })];
  const [m1, m2] = facts.map((fact) => fact.factId);
  assert.deepEqual(normalizeMonitoringOutcomeDecision({
    verdict: 'silent', terminal: false, factIds: [m1, m2],
  }, facts), { verdict: 'silent', terminal: false, factIds: [m1, m2] });
  assert.equal(normalizeMonitoringOutcomeDecision({
    verdict: 'notify', terminal: true, factIds: [m1, m2], message: 'injectie',
  }, facts), null);
  assert.equal(normalizeMonitoringOutcomeDecision({
    verdict: 'notify', terminal: true, factIds: [m1],
  }, facts), null);
  assert.equal(normalizeMonitoringOutcomeDecision({
    verdict: 'silent', terminal: true, factIds: [m1, m2],
  }, facts), null);
});

test('monitoringretry reserveert scheduler, wire en receipt strikt vóór de facthorizon', () => {
  const pending = { expiresAt: '2026-08-15T10:10:00.000Z' };
  const contract = { endsAt: null };
  assert.deepEqual(monitoringDeliveryTransportLimits(pending, contract), {
    horizonAt: '2026-08-15T10:10:00.000Z',
    latestWireStart: '2026-08-15T10:08:30.000Z',
    wireNotAfter: '2026-08-15T10:09:30.000Z',
  });
  assert.equal(monitoringDeliveryTransportLimits(
    { expiresAt: 'niet-canoniek' }, contract,
  ), null);
  assert.deepEqual(monitoringDeliveryRetrySchedule(pending, contract, {
    now: '2026-08-15T10:00:00.000Z', requestedBackoffMs: 15 * 60_000,
  }), {
    horizonAt: '2026-08-15T10:10:00.000Z',
    latestScheduledWake: '2026-08-15T10:08:00.000Z',
    latestWireStart: '2026-08-15T10:08:30.000Z',
    wireNotAfter: '2026-08-15T10:09:30.000Z',
    wakeAt: '2026-08-15T10:04:00.000Z',
  });
  assert.equal(monitoringDeliveryRetrySchedule(pending, contract, {
    now: '2026-08-15T10:07:30.001Z', requestedBackoffMs: 15 * 60_000,
  }), null, 'minder dan dertig seconden planruimte is geen veilige retry');
  assert.equal(monitoringDeliveryRetrySchedule(pending, contract, {
    now: '2026-08-15T10:00:00.000Z', requestedBackoffMs: 15 * 60_000,
    notBefore: '2026-08-15T10:08:00.001Z',
  }), null, 'een externe not-before mag de laatste veilige schedulerwake niet passeren');
  assert.equal(monitoringDeliveryRetrySchedule(pending, contract, {
    now: '2026-08-15T10:00:00.000Z', requestedBackoffMs: 15 * 60_000,
    notBefore: '2026-08-15T10:08:00.000Z',
  })?.wakeAt, '2026-08-15T10:08:00.000Z');
  assert.equal(monitoringDeliveryRetrySchedule(pending, contract, {
    now: '2026-08-15T10:07:30.000Z', requestedBackoffMs: 15 * 60_000,
  })?.wakeAt, '2026-08-15T10:07:45.000Z',
  'de exacte dertigseconden-grens houdt een minimale retry van vijftien seconden over');

  const earlierContract = { endsAt: '2026-08-15T10:06:00.000Z' };
  const bounded = monitoringDeliveryRetrySchedule(pending, earlierContract, {
    now: '2026-08-15T10:00:00.000Z', requestedBackoffMs: 15 * 60_000,
  });
  assert.equal(bounded.horizonAt, earlierContract.endsAt);
  assert.equal(bounded.latestScheduledWake, '2026-08-15T10:04:00.000Z');
  assert.equal(bounded.wakeAt, '2026-08-15T10:02:00.000Z');

  assert.equal(monitoringDeliveryWireCurrent(pending, contract, {
    now: '2026-08-15T10:08:30.000Z', boundary: 'start',
  }), true);
  assert.equal(monitoringDeliveryWireCurrent(pending, contract, {
    now: '2026-08-15T10:08:30.001Z', boundary: 'start',
  }), false);
  assert.equal(monitoringDeliveryWireCurrent(pending, contract, {
    now: '2026-08-15T10:09:30.000Z', boundary: 'child',
  }), true);
  assert.equal(monitoringDeliveryWireCurrent(pending, contract, {
    now: '2026-08-15T10:09:30.001Z', boundary: 'child',
  }), false);
  assert.equal(monitoringDeliveryWireCurrent(pending, contract, {
    now: 'geen-tijd', boundary: 'child',
  }), false);
});

test('MonitoringContract/v2 draagt maximaal acht volledig gebonden conditions', (t) => {
  const readBindings = Array.from({ length: 8 }, (_, index) => {
    const requirementId = `r${index + 1}`;
    const factBinding = bind(`sensor.monitor_${index + 1}`, {
      name: `Monitor ${index + 1}`, state: String(index + 1), unit: '%', updated: UPDATED,
    }, {
      requirementId, sourceText: `meting ${index + 1}`,
      condition: `Waarschuw wanneer meting ${index + 1} de grens bereikt.`,
    });
    return {
      entityId: factBinding.entityId, requirementId,
      name: factBinding.name, factBinding,
    };
  });
  const origin = {
    channel: 'telegram', conversationId: 'telegram:424242', messageId: 'eight-bindings',
    principal: 'telegram:424242', messageThreadId: 7,
    executionKey: 'turn-understanding:eight-bindings', executionSeq: 22,
    intentRecordSha256: '1'.repeat(64), authoritySha256: '2'.repeat(64),
    sourceRef: 'telegram-ingress:eight-bindings', sourceSha256: '9'.repeat(64),
    sourceGeneration: 29, archiveRowId: 33, originProofSha256: 'f'.repeat(64),
  };
  const issue = (bindings) => issueMonitoringContract({
    sourceUnderstandingSha256: SHA,
    conditions: bindings.map((_, index) => (
      `Waarschuw wanneer meting ${index + 1} de grens bereikt.`
    )),
    definitionOfDone: 'Alle gebonden voorwaarden zijn exact beoordeeld.',
    cadenceMs: 60_000, readBindings: bindings,
    createdAt: '2026-08-15T09:59:00.000Z', requesterLabel: 'Jaap',
    sourceTextSha256: 'e'.repeat(64), deliveryRequired: true, origin,
  });
  const eightConditionContract = issue(readBindings);
  assert.equal(eightConditionContract?.readBindings.entries.length, 8,
    `acht unieke conditionbindings vormen de volledige ondersteunde bovengrens; bindingsBytes=${Buffer.byteLength(JSON.stringify(readBindings), 'utf8')}`);
  assert.match(eightConditionContract.conditions[0], /^monitoring-condition:[a-f0-9]{64}$/u,
    'conditiontekst en rol worden uit de gepinde TurnUnderstanding gerehydrateerd');
  assert.equal(eightConditionContract.readBindings.contract,
    'MonitoringFactBindingDictionary/v1');
  assert.deepEqual(eightConditionContract.readBindings.fields, [
    'contract', 'requirementId', 'entityId', 'conditionId', 'operand',
    'sourceField', 'valueType', 'quantityKind', 'unitField', 'expectedUnit',
    'maxAgeMs', 'issuedExecutionKey', 'issuedCompletionRecordSha256', 'bindingSha256',
  ]);
  assert.equal(eightConditionContract.readBindings.entries[0].length, 14);
  assert.deepEqual([...eightConditionContract.readBindings.fields].sort(), [
    'bindingSha256', 'conditionId', 'contract', 'entityId', 'expectedUnit',
    'issuedCompletionRecordSha256', 'issuedExecutionKey', 'maxAgeMs', 'operand',
    'quantityKind', 'requirementId', 'sourceField', 'unitField', 'valueType',
  ], 'het contract bewaart compacte strikt geversioneerde bindingtuples');
  const ninthFactBinding = bind('sensor.monitor_9', {
    name: 'Monitor 9', state: '9', unit: '%', updated: UPDATED,
  }, {
    requirementId: 'r9', sourceText: 'meting 9',
    condition: 'Waarschuw wanneer meting 9 de grens bereikt.',
  });
  assert.equal(issue([...readBindings, {
    entityId: ninthFactBinding.entityId, requirementId: 'r9',
    name: ninthFactBinding.name, factBinding: ninthFactBinding,
  }]), null, 'een negende conditionbinding wordt geweigerd, nooit afgesneden');

  const facts = Array.from({ length: 8 }, (_, index) => measurement(
    `sensor.monitor_${index + 1}`,
    { name: `Monitor ${index + 1}`, state: String(index + 1), unit: '%', updated: UPDATED },
    {
      requirementId: `r${index + 1}`, sourceText: `meting ${index + 1}`,
      condition: `Waarschuw wanneer meting ${index + 1} de grens bereikt.`,
    },
  ));
  const outcome = {
    verdict: 'notify', terminal: true, factIds: facts.map((fact) => fact.factId),
  };
  const outcomeReceipt = issueMonitoringOutcomeReceipt({
    outcome, executionKey: 'monitoring-outcome-eight-bindings',
    intentRecordSha256: '5'.repeat(64), outputSha256: '6'.repeat(64),
    completionRecordSha256: '7'.repeat(64), finishedAt: READ_AT,
  });
  const lifecycle = issueMonitoringLifecycleBinding({
    monitoring: eightConditionContract,
    episodeId: 81, episodeKey: 'episode-key-81', issuedEpisodeRevision: 3,
    workId: 71, originRowId: 91,
    originKey: 'episode:81|channel:telegram|conversation:telegram:424242|message:eight-bindings',
    startAuthority: {
      executionKey: origin.executionKey, executionSeq: origin.executionSeq,
      intentRecordSha256: origin.intentRecordSha256,
      authoritySha256: origin.authoritySha256,
      channel: 'telegram', conversationId: origin.conversationId,
      messageId: origin.messageId, sourceKind: 'telegram_ingress',
      sourceRef: origin.sourceRef, sourceSha256: origin.sourceSha256,
      sourceGeneration: origin.sourceGeneration,
    },
    deliveryOrigin: origin, generation: 1,
  });
  assert.ok(lifecycle, 'acht bindings moeten een compacte lifecyclebinding kunnen dragen');
  const pending = issueMonitoringPendingDelivery({
    monitoring: eightConditionContract, lifecycle, outcome, outcomeReceipt,
    facts, text: renderMonitoringTerminalText(facts),
    episodeId: 81, workId: 71, dedupeKey: 'episode:81:monitoring-final',
    lifecycleSha256: lifecycle?.lifecycleSha256, preparedAt: READ_AT,
  });
  assert.ok(pending);
  assert.equal(Object.hasOwn(pending, 'facts'), false,
    'pending dupliceert geen herleidbare measurement facts');
  assert.equal(Object.hasOwn(pending, 'outcome'), false,
    'pending dupliceert geen herleidbaar modeloutcome');
  assert.equal(pending.factRefs.contract, 'MonitoringFactReferenceDictionary/v1');
  assert.equal(pending.factRefs.entries.length, 8);
  assert.deepEqual(pending.factRefs.fields, [
    'bindingSha256', 'requirementId', 'executionKey', 'completionRecordSha256', 'factId',
  ]);
  assert.equal(pending.factRefs.entries[0].length, 5);
  const state = {
    monitoring: eightConditionContract,
    monitoring_lifecycle: lifecycle,
    monitoring_runtime: {
      contract: 'MonitoringRuntimeState/v2', generation: 1,
      lastCycleAt: READ_AT, nextWakeAt: '2026-08-15T10:01:01.000Z',
    },
    pending_delivery: pending,
  };
  t.diagnostic(`max8-state-bytes=${Buffer.byteLength(JSON.stringify(state), 'utf8')}; contract=${Buffer.byteLength(JSON.stringify(eightConditionContract), 'utf8')}; lifecycle=${Buffer.byteLength(JSON.stringify(lifecycle), 'utf8')}; pending=${Buffer.byteLength(JSON.stringify(pending), 'utf8')}`);
  assert.ok(Buffer.byteLength(JSON.stringify(state), 'utf8') <= 10_500,
    `de echte max-8 state houdt minimaal 1.500 bytes werkstate-headroom; bytes=${Buffer.byteLength(JSON.stringify(state), 'utf8')}; contract=${Buffer.byteLength(JSON.stringify(eightConditionContract), 'utf8')}; lifecycle=${Buffer.byteLength(JSON.stringify(lifecycle), 'utf8')}; pending=${Buffer.byteLength(JSON.stringify(pending), 'utf8')}`);
  assert.equal(monitoringStateFitsDurableBudget(state), true,
    `acht bindings plus lifecycle en pending moeten samen passen; bytes=${Buffer.byteLength(JSON.stringify(state), 'utf8')}; contract=${Buffer.byteLength(JSON.stringify(eightConditionContract), 'utf8')}; lifecycle=${Buffer.byteLength(JSON.stringify(lifecycle), 'utf8')}; pending=${Buffer.byteLength(JSON.stringify(pending), 'utf8')}`);
  const padded = (targetBytes) => {
    const base = { ...state, padding: '' };
    const baseBytes = Buffer.byteLength(JSON.stringify(base), 'utf8');
    return { ...base, padding: 'x'.repeat(targetBytes - baseBytes) };
  };
  const exactLimit = padded(12_000);
  const overLimit = padded(12_001);
  assert.equal(Buffer.byteLength(JSON.stringify(exactLimit), 'utf8'), 12_000);
  assert.equal(monitoringStateFitsDurableBudget(exactLimit), true);
  assert.equal(Buffer.byteLength(JSON.stringify(overLimit), 'utf8'), 12_001);
  assert.equal(monitoringStateFitsDurableBudget(overLimit), false);
});

test('terminale tekst komt uitsluitend deterministisch uit de facts', () => {
  const facts = [measurement('weather.thuis', {
    name: 'Buiten', state: 'sunny', temperature: 18,
    temperature_unit: '°C', updated: UPDATED,
  }), measurement('climate.woonkamer', {
    name: 'Woonkamer', state: 'heat', current_temperature: 19,
    temperature_unit: '°C', updated: UPDATED,
  }, { requirementId: 'r2' })];
  assert.equal(renderMonitoringTerminalText(facts),
    'De afgesproken voorwaarde is bereikt. Buiten is 18 °C en Woonkamer is 19 °C.');
  assert.equal(renderMonitoringTerminalText(facts), renderMonitoringTerminalText(facts));
  assert.equal(renderMonitoringTerminalText([{ ...facts[0], value: 999 }]), '',
    'los gewijzigde callerdata heeft geen renderauthority');
  const hostile = measurement('sensor.hostile', {
    name: 'API key: sk-proj-abcdefghijklmnopqrstuvwxyz - Zone\u0007',
    state: '64.2', unit: '%', updated: UPDATED,
  }, { name: 'API key: sk-proj-abcdefghijklmnopqrstuvwxyz - Zone\u0007' });
  const hostileText = renderMonitoringTerminalText([hostile]);
  assert.doesNotMatch(hostileText, /sk-proj-/u);
  // eslint-disable-next-line no-control-regex
  assert.doesNotMatch(hostileText, /[\u0000-\u001f\u007f]/u);
  assert.doesNotMatch(hostileText, /[\u2013\u2014]/u);
});

test('MonitoringContract/v2 verzegelt presentatie, directe Telegramroute en pending bytes', () => {
  const data = {
    name: 'Woonkamer', state: 'heat', current_temperature: 20.1,
    temperature_unit: '°C', updated: UPDATED,
  };
  const factBinding = bind('climate.woonkamer', data);
  const boundCondition = 'Waarschuw wanneer deze meting de grens bereikt.';
  const origin = {
    channel: 'telegram', conversationId: 'telegram:424242', messageId: '4358',
    principal: 'telegram:424242', messageThreadId: 7,
    executionKey: 'turn-understanding:telegram-test', executionSeq: 12,
    intentRecordSha256: '1'.repeat(64), authoritySha256: '2'.repeat(64),
    sourceRef: 'telegram-ingress:test', sourceSha256: '9'.repeat(64),
    sourceGeneration: 19, archiveRowId: 23, originProofSha256: 'f'.repeat(64),
  };
  const contract = issueMonitoringContract({
    sourceUnderstandingSha256: SHA,
    conditions: [boundCondition],
    definitionOfDone: 'Waarschuw wanneer de woonkamer warmer is dan 20 graden.',
    cadenceMs: 60_000,
    endsAt: '2026-08-15T11:00:00.000Z',
    readBindings: [{
      entityId: factBinding.entityId, requirementId: factBinding.requirementId,
      name: factBinding.name, factBinding,
    }],
    createdAt: '2026-08-15T09:59:00.000Z',
    requesterLabel: 'Jaap', sourceTextSha256: 'e'.repeat(64),
    deliveryRequired: true,
    origin,
  });
  assert.equal(contract.contract, 'MonitoringContract/v2');
  assert.equal(contract.delivery.route, 'direct_telegram');
  assert.equal(contract.delivery.recipientResolution, 'none');
  assert.equal(contract.delivery.broadcast, false);
  assert.equal(contract.presentation.audience, 'requester');
  assert.equal(issueMonitoringContract({
    sourceUnderstandingSha256: SHA,
    conditions: ['Een andere voorwaarde zonder gebonden meetrequirement.'],
    definitionOfDone: 'Waarschuw wanneer de woonkamer warmer is dan 20 graden.',
    cadenceMs: 60_000, endsAt: '2026-08-15T11:00:00.000Z',
    readBindings: contract.readBindings, createdAt: '2026-08-15T09:59:00.000Z',
    requesterLabel: 'Jaap', sourceTextSha256: 'e'.repeat(64),
    deliveryRequired: true, origin,
  }), null, 'iedere bindingcondition moet exact door het contract worden gedragen');
  assert.equal(monitoringDeliveryMatchesAuthority(contract, {
    channel: 'telegram', conversationId: 'telegram:424242', messageId: '4358',
    principal: 'telegram:424242', messageThreadId: 7,
    executionKey: 'turn-understanding:telegram-test', executionSeq: 12,
    intentRecordSha256: '1'.repeat(64), authoritySha256: '2'.repeat(64),
    sourceRef: 'telegram-ingress:test', sourceSha256: '9'.repeat(64),
    sourceGeneration: 19, archiveRowId: 23, originProofSha256: 'f'.repeat(64),
  }), true);
  assert.equal(monitoringDeliveryMatchesAuthority(contract, {
    ...origin, sourceGeneration: 20,
  }), false, 'een andere duurzame origin-generatie heeft geen routeauthority');
  assert.equal(monitoringDeliveryMatchesAuthority(contract, {
    ...origin, authoritySha256: '3'.repeat(64),
  }), false, 'een zelfgehashte route zonder de gepinde durable authority heeft geen bevoegdheid');
  assert.deepEqual(normalizeMonitoringContract(JSON.parse(JSON.stringify(contract))), contract);
  assert.equal(normalizeMonitoringContract({
    ...contract, delivery: { ...contract.delivery, broadcast: true },
  }), null);

  const fact = measurement('climate.woonkamer', data);
  const outcome = { verdict: 'notify', terminal: true, factIds: [fact.factId] };
  const requestRecord = monitoringOutcomeRequestRecord({
    monitoring: contract,
    conditionContext: [{ statement: boundCondition, role: 'guard' }],
    facts: [fact], attempt: 1,
  });
  assert.match(requestRecord.requestSha256, /^[a-f0-9]{64}$/u);
  assert.equal(monitoringOutcomeRequestRecord({
    monitoring: contract,
    conditionContext: [{ statement: boundCondition, role: 'guard' }],
    facts: [fact], attempt: 1,
  }).requestSha256, requestRecord.requestSha256,
  'dezelfde durable contract-, condition- en factprojectie geeft exact dezelfde requesthash');
  assert.notEqual(monitoringOutcomeRequestRecord({
    monitoring: contract,
    conditionContext: [{ statement: boundCondition, role: 'guard' }],
    facts: [fact], attempt: 2,
  }).requestSha256, requestRecord.requestSha256);
  assert.equal(monitoringOutcomeRequestRecord({
    monitoring: contract,
    conditionContext: [{ statement: boundCondition, role: 'guard' }],
    facts: [fact], attempt: 3,
  }), null);
  const responseRecord = monitoringOutcomeResponseRecord({
    requestSha256: requestRecord.requestSha256,
    accepted: true, outcome, facts: [fact], failure: null,
  });
  assert.match(responseRecord.responseSha256, /^[a-f0-9]{64}$/u);
  assert.equal(monitoringOutcomeResponseRecord({
    requestSha256: requestRecord.requestSha256,
    accepted: true, outcome, facts: [fact], failure: 'forged',
  }), null, 'een geaccepteerde uitkomst kan geen failuretekst dragen');
  const outcomeReceipt = issueMonitoringOutcomeReceipt({
    outcome, executionKey: 'monitoring-outcome-test',
    intentRecordSha256: '5'.repeat(64), outputSha256: '6'.repeat(64),
    completionRecordSha256: '7'.repeat(64), finishedAt: READ_AT,
  });
  const rendered = renderMonitoringTerminalText([fact]);
  const lifecycle = issueMonitoringLifecycleBinding({
    monitoring: contract, episodeId: 41, episodeKey: 'episode-key-41',
    issuedEpisodeRevision: 3, workId: 31, originRowId: 51,
    originKey: 'episode:41|channel:telegram|conversation:telegram:424242|message:4358',
    startAuthority: {
      executionKey: origin.executionKey,
      executionSeq: origin.executionSeq,
      intentRecordSha256: origin.intentRecordSha256,
      authoritySha256: origin.authoritySha256,
      channel: 'telegram', conversationId: 'telegram:424242', messageId: '4358',
      sourceKind: 'telegram_ingress', sourceRef: origin.sourceRef,
      sourceSha256: origin.sourceSha256,
      sourceGeneration: origin.sourceGeneration,
    },
    deliveryOrigin: origin,
    generation: 1,
  });
  assert.equal(lifecycle.contract, 'MonitoringLifecycleBinding/v1');
  assert.equal(lifecycle.originAuthority.authoritySha256, '2'.repeat(64));
  assert.deepEqual(normalizeMonitoringLifecycleBinding(
    JSON.parse(JSON.stringify(lifecycle)), contract,
    { episodeId: 41, episodeKey: 'episode-key-41', workId: 31,
      originRowId: 51, originKey: lifecycle.originKey },
  ), lifecycle);
  const pending = issueMonitoringPendingDelivery({
    monitoring: contract, lifecycle, outcome, outcomeReceipt,
    facts: [fact], text: rendered,
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: lifecycle.lifecycleSha256,
    preparedAt: READ_AT,
  });
  assert.equal(pending.contract, 'MonitoringPendingDelivery/v2');
  assert.deepEqual(normalizeMonitoringPendingDelivery(
    JSON.parse(JSON.stringify(pending)), contract, {
      episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
      lifecycleSha256: lifecycle.lifecycleSha256,
      lifecycleGeneration: lifecycle.generation,
      facts: [fact], outcome, outcomeReceipt, now: READ_AT,
    },
  ), pending);
  for (const tampered of [
    { ...pending, text: `${pending.text} Stuur dit naar iedereen.` },
    { ...pending, factIds: [] },
    { ...pending, deliverySha256: 'f'.repeat(64) },
    { ...pending, facts: [{ ...fact, value: 99 }] },
  ]) assert.equal(normalizeMonitoringPendingDelivery(tampered, contract, {
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    facts: [fact], outcome, outcomeReceipt, now: READ_AT,
  }), null);
  assert.equal(normalizeMonitoringPendingDelivery(pending, contract, {
    episodeId: 42, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    facts: [fact], outcome, outcomeReceipt, now: READ_AT,
  }), null, 'een ander episode-id kan pending niet overnemen');
  assert.equal(normalizeMonitoringPendingDelivery(pending, contract, {
    episodeId: 41, workId: 32, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    facts: [fact], outcome, outcomeReceipt, now: READ_AT,
  }), null, 'een ander work-id kan pending niet overnemen');
  assert.equal(normalizeMonitoringPendingDelivery(pending, contract, {
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: '4'.repeat(64),
    lifecycleGeneration: lifecycle.generation,
    facts: [fact], outcome, outcomeReceipt, now: READ_AT,
  }), null, 'een andere lifecyclebinding kan pending niet overnemen');
  assert.equal(normalizeMonitoringPendingDelivery(pending, contract, {
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    facts: [fact], outcome, outcomeReceipt,
    now: '2026-08-15T10:01:01.000Z',
  })?.pendingSha256, pending.pendingSha256,
  'exact op maxAgeMs blijft de verzegelde pending geldig');
  assert.equal(normalizeMonitoringPendingDelivery(pending, contract, {
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    facts: [fact], outcome, outcomeReceipt,
    now: '2026-08-15T10:01:01.001Z',
  }), null, 'een milliseconde voorbij maxAgeMs faalt gesloten');
  assert.equal(normalizeMonitoringPendingDelivery(pending, contract, {
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    lifecycleSha256: lifecycle.lifecycleSha256,
    lifecycleGeneration: lifecycle.generation,
    facts: [fact], outcome, outcomeReceipt,
    now: '2026-08-15T10:02:00.000Z',
  }), null, 'een pending met inmiddels verouderde meetfacts kan niet worden verstuurd');
  const unrelatedFact = measurement('sensor.luchtvochtigheid', {
    name: 'Luchtvochtigheid', state: '64.2', unit: '%', updated: UPDATED,
  });
  assert.equal(issueMonitoringPendingDelivery({
    monitoring: contract, lifecycle, outcomeReceipt,
    outcome: { verdict: 'notify', terminal: true, factIds: [unrelatedFact.factId] },
    facts: [unrelatedFact], text: renderMonitoringTerminalText([unrelatedFact]),
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    preparedAt: READ_AT,
  }), null, 'een zelfgeldige maar ongerelateerde factset draagt dit contract niet');
  const { factSha256: _factSha256, factId: _factId, ...forgedFactBody } = fact;
  forgedFactBody.sourceField = 'state';
  forgedFactBody.valueType = 'string';
  forgedFactBody.quantityKind = 'state';
  forgedFactBody.value = 'heat';
  forgedFactBody.unit = null;
  const forgedFactSha256 = createHash('sha256')
    .update(JSON.stringify(forgedFactBody, Object.keys(forgedFactBody).sort()))
    .digest('hex');
  const forgedFact = {
    ...forgedFactBody, factSha256: forgedFactSha256,
    factId: `monitoring-fact:${forgedFactSha256}`,
  };
  assert.ok(normalizeMonitoringMeasurementFact(forgedFact),
    'de negatieve proef gebruikt een intern volledig self-consistent fact');
  assert.equal(issueMonitoringPendingDelivery({
    monitoring: contract, lifecycle, outcomeReceipt,
    outcome: { verdict: 'notify', terminal: true, factIds: [forgedFact.factId] },
    facts: [forgedFact], text: renderMonitoringTerminalText([forgedFact]),
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
    preparedAt: READ_AT,
  }), null, 'self-rehashen mag de semantiek van de gepinde binding niet verwisselen');

  const silent = issueMonitoringContract({
    sourceUnderstandingSha256: SHA,
    conditions: [boundCondition],
    definitionOfDone: 'De stille observatie bereikt haar eindhorizon.',
    cadenceMs: 60_000, readBindings: contract.readBindings,
    createdAt: '2026-08-15T09:59:00.000Z', requesterLabel: 'Jaap',
    sourceTextSha256: 'e'.repeat(64), deliveryRequired: false, origin: null,
  });
  assert.equal(silent.delivery.route, 'none');
  assert.equal(issueMonitoringPendingDelivery({
    monitoring: silent, lifecycle, outcome, outcomeReceipt, facts: [fact], text: rendered,
    episodeId: 41, workId: 31, dedupeKey: 'episode:41:monitoring-final',
  }), null, 'een stille route kan nooit pending transport autoriseren');

  assert.equal(monitoringStateFitsDurableBudget({ monitoring: contract }), true);
  assert.equal(monitoringStateFitsDurableBudget({
    monitoring: contract, padding: 'x'.repeat(12_000),
  }), false, 'het gezamenlijke durable stateobject heeft exact één 12k-grens');
});
