import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-monitoring-host-builder-');
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';

const { db } = await import('../src/db.js');
const { createMonitoringContract } = await import('../src/mind/understanding.js');

const SHA = 'a'.repeat(64);
const READ_AT = '2026-08-15T10:00:01.000Z';
const UPDATED = '2026-08-15T10:00:00.000Z';

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function receipt(entityId, data) {
  const output = JSON.stringify({
    contract: 'ActionResult/v1', tool: 'ha_lees', ok: true, changed: false,
    verified: true, needs_followup: false, partial: false,
    effect_uncertain: false, data: { entity_id: entityId, ...data },
  });
  return {
    name: 'ha_lees', args: { entity_id: entityId }, output,
    ok: true, verified: true, changed: false, partial: false,
    needs_followup: false, effect_uncertain: false, at: READ_AT,
    semanticBinding: {
      understandingSha256: SHA, requirementId: 'r1',
      capabilityId: 'home.entity.read', operationId: 'read',
    },
    monitoringLedger: {
      executionKey: 'monitoring-host-builder-read',
      intentRecordSha256: 'b'.repeat(64),
      outputSha256: createHash('sha256').update(output).digest('hex'),
      completionRecordSha256: 'd'.repeat(64), finishedTs: READ_AT,
    },
  };
}

function discoveryReceipt(query) {
  return {
    name: 'huis_status', args: { vraag: query },
    ok: true, verified: true, changed: false, partial: false,
    needs_followup: false, effect_uncertain: false,
    semanticBinding: {
      understandingSha256: SHA, requirementId: 'r2',
      capabilityId: 'home.context.read', operationId: 'read',
    },
    output: JSON.stringify({
      contract: 'ActionResult/v1', tool: 'huis_status', ok: true,
      changed: false, verified: true, needs_followup: false, partial: false,
      effect_uncertain: false,
      data: { entities: [{ entity_id: 'sensor.bewaakte_temperatuur' }] },
    }),
  };
}

test('hostcontractbouw knipt geen monitoringsemantiek stil af', () => {
  const condition = 'Waarschuw wanneer de temperatuur de grens bereikt.';
  const toolReceipt = receipt('sensor.bewaakte_temperatuur', {
    name: 'Bewaakte temperatuur', state: '20.1', unit: '°C', updated: UPDATED,
  });
  const wake = new Date(Date.now() + 5 * 60_000).toISOString();
  const baseMonitoring = {
    sourceUnderstandingSha256: SHA,
    sourceReadRequirementIds: ['r1'],
    sourceEntityReadRequirementIds: ['r1'],
    conditions: [condition], temporalCondition: null,
    definitionOfDone: 'Waarschuw eenmaal met de geverifieerde temperatuur.',
    deliveryRequired: false,
  };
  const issuanceContext = {
    factBindingSpecs: [{
      requirementId: 'r1', condition, conditionRole: 'guard', operand: 'subject',
      sourceText: 'de bewaakte temperatuur',
    }],
    verifyInitialReceipt: (item) => item.monitoringLedger,
    requesterLabel: 'Jaap', sourceTextSha256: 'e'.repeat(64),
  };
  const build = (monitoring, context = issuanceContext, toolResults = [toolReceipt]) => (
    createMonitoringContract({
      monitoring, args: { hervatten_na: wake }, toolResults,
    }, context)
  );
  assert.ok(build(baseMonitoring), 'de hostfixture zelf moet geldig zijn');
  assert.ok(build({
    ...baseMonitoring, definitionOfDone: 'x'.repeat(1_200),
  }), 'definitionOfDone exact op de grens blijft volledig geldig');
  assert.equal(build({
    ...baseMonitoring, definitionOfDone: 'x'.repeat(1_201),
  }), null, 'definitionOfDone boven de grens wordt geweigerd, niet afgeknipt');
  assert.equal(build({
    ...baseMonitoring, temporalCondition: 't'.repeat(1_001),
  }), null, 'temporalCondition boven de grens wordt geweigerd, niet afgeknipt');
  assert.ok(build({
    ...baseMonitoring, temporalCondition: 't'.repeat(1_000),
  }), 'temporalCondition exact op de grens blijft volledig geldig');
  const boundedCondition = 'c'.repeat(500);
  assert.ok(build({
    ...baseMonitoring, conditions: [boundedCondition],
  }, {
    ...issuanceContext,
    factBindingSpecs: [{
      ...issuanceContext.factBindingSpecs[0], condition: boundedCondition,
    }],
  }), 'een condition exact op 500 tekens blijft volledig geldig');
  assert.equal(build({
    ...baseMonitoring, conditions: ['c'.repeat(501)],
  }, {
    ...issuanceContext,
    factBindingSpecs: [{
      ...issuanceContext.factBindingSpecs[0], condition: 'c'.repeat(501),
    }],
  }), null, 'een condition boven 500 tekens wordt geweigerd');
  const discoveryMonitoring = {
    ...baseMonitoring,
    sourceReadRequirementIds: ['r2', 'r1'],
    sourceDiscoveryRequirementId: 'r2',
  };
  assert.ok(build(discoveryMonitoring, issuanceContext, [
    discoveryReceipt('q'.repeat(500)), toolReceipt,
  ]), 'discoveryQuery exact op de grens blijft volledig geldig');
  assert.equal(build(discoveryMonitoring, issuanceContext, [
    discoveryReceipt('q'.repeat(501)), toolReceipt,
  ]), null, 'discoveryQuery boven de grens wordt geweigerd, niet afgeknipt');
  assert.equal(build({
    ...baseMonitoring, conditions: Array.from({ length: 9 }, (_, index) => (
      index === 0 ? condition : `Extra voorwaarde ${index}`
    )),
  }), null, 'een negende monitoringvoorwaarde wordt geweigerd, niet genegeerd');
});
