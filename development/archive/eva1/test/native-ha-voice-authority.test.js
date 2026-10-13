import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

test('alleen de actuele geauthenticeerde en subscribed HA-socket mint voice query/ACK authority', () => {
  const fixture = path.join(import.meta.dirname, 'native-ha-voice-authority-fixture.mjs');
  const child = spawnSync(process.execPath, [fixture], {
    cwd: path.resolve(import.meta.dirname, '..'),
    env: { ...process.env },
    encoding: 'utf8',
    timeout: 20_000,
  });
  assert.equal(child.status, 0, `${child.stdout}\n${child.stderr}`);
  const line = child.stdout.split('\n').find((entry) => entry.startsWith('NATIVE_HA_VOICE_REPORT='));
  assert.ok(line, child.stdout);
  const report = JSON.parse(line.slice('NATIVE_HA_VOICE_REPORT='.length));
  assert.equal(report.providerCalls.preauth, 0);
  assert.equal(report.providerCalls.publicBus, 0);
  assert.equal(report.providerCalls.wrongSubscription, 0);
  assert.equal(report.providerCalls.missingAckBinding, 2);
  assert.equal(report.providerCalls.tamperedAck, 2);
  assert.equal(report.providerCalls.crossGeneration, 2);
  assert.equal(report.providerCalls.authentic, 2);
  assert.equal(report.providerCalls.rollback, 2);
  assert.equal(report.providerCalls.ackRollback, 2);
  assert.equal(report.providerCalls.drainBlocked, 2);
  assert.equal(report.providerCalls.drainGood, 2);
  assert.equal(report.providerCalls.stale, 1, JSON.stringify(report.providerConnectionStates));
  assert.equal(report.rows.preauth, null);
  assert.equal(report.rows.publicBus, null);
  assert.equal(report.rows.wrongSubscription, null);
  assert.deepEqual(report.rows.missingAckBinding, { status: 'done' });
  assert.deepEqual(report.rows.tamperedAck, { status: 'done' });
  assert.deepEqual(report.rows.crossGeneration, { status: 'done' });
  assert.deepEqual(report.rows.authentic, { status: 'done' });
  assert.deepEqual(report.rows.rollback, { status: 'done' });
  assert.deepEqual(report.rows.ackRollback, { status: 'done' });
  assert.deepEqual(report.rows.drainBlocked, { status: 'done' });
  assert.deepEqual(report.rows.drainGood, { status: 'done' });
  assert.notDeepEqual(report.rows.stale, { status: 'done' });
  assert.equal(report.offers.includes('native-ha-preauth-1'), false);
  assert.equal(report.offers.includes('native-ha-public-bus-1'), false);
  assert.equal(report.offers.includes('native-ha-wrong-subscription-1'), false);
  assert.equal(report.offers.includes('native-ha-authentic-1'), true);
  assert.equal(report.offers.includes('native-ha-rollback-1'), true);
  assert.equal(report.offers.includes('native-ha-stale-socket-1'), false);
  assert.equal(report.publicAckDeliveryCount, 0);
  assert.equal(report.missingAckDeliveryCount, 0);
  assert.equal(report.tamperedAckDeliveryCount, 0);
  assert.equal(report.crossGenerationDeliveryCount, 0);
  assert.equal(report.authenticDeliveryCount, 1);
  assert.equal(report.staleDeliveryCount, 0);
  assert.deepEqual(report.authenticReplay, {
    providerCalls: 2, offerCount: 1, deliveryCount: 1,
  });
  assert.deepEqual(report.rollbackBeforeRecovery, {
    offerCount: 1, deliveryCount: 0, stage: 'acknowledged',
  });
  assert.deepEqual(report.startupRecovery, {
    completedBeforeScenarios: true, recovered: 0,
  });
  assert.deepEqual(report.rollbackAfterRecovery, {
    offerCount: 1, deliveryCount: 1, stage: 'delivered',
    ackEventCount: 1, deliveredEventCount: 1,
    scheduledRecovered: true, explicitRecovery: 0, secondExplicitRecovery: 0,
  });
  assert.deepEqual(report.ackRollbackBeforeRetry, {
    offerCount: 1, ackEventCount: 1, deliveryCount: 0, stage: 'offered',
  });
  assert.deepEqual(report.ackRollbackAfterRetry, {
    offerCount: 1, ackEventCount: 1, deliveryCount: 1, stage: 'delivered',
  });
  assert.deepEqual(report.drainBeforeLift, {
    blockedStage: 'acknowledged', blockedDeliveryCount: 0,
    goodStage: 'delivered', goodDeliveryCount: 1,
    blockedOfferCount: 1, goodOfferCount: 1,
    blockedAckEventCount: 1, goodAckEventCount: 1,
  });
  assert.deepEqual(report.drainAfterLift, {
    blockedStage: 'delivered', blockedDeliveryCount: 1,
    goodStage: 'delivered', goodDeliveryCount: 1,
    blockedOfferCount: 1, goodOfferCount: 1,
    blockedAckEventCount: 1, goodAckEventCount: 1,
  });
  assert.match(report.authenticOffer.deliveryOperationRef,
    /^home-assistant-voice-reply:[a-f0-9]{64}$/u);
  assert.equal(report.authenticOffer.payloadSha256,
    report.authenticOffer.expectedPayloadSha256);
  assert.equal(report.authenticOffer.replySha256,
    report.authenticOffer.expectedReplySha256);
  assert.equal(report.authenticDurableOffer.deliveryOperationRef,
    report.authenticOffer.deliveryOperationRef);
  assert.equal(report.authenticDurableOffer.deliveryOperationSha256,
    report.authenticOffer.deliveryOperationRef.split(':').at(-1));
  assert.equal(report.authenticDurableOffer.payloadSha256,
    report.authenticOffer.payloadSha256);
  assert.equal(report.authenticDurableOffer.replySha256,
    report.authenticOffer.replySha256);
  assert.deepEqual(report.authenticAckEvidence, {
    contract: 'HomeAssistantVoiceDeliveryAuthorityEvidence/v2',
    requestId: 'native-ha-authentic-1',
    satellite: 'assist_satellite.keuken',
    queryEventType: 'eva_voice_query',
    ackEventType: 'eva_voice_reply_ack',
    queryCanonical: true,
    ackCanonical: true,
    queryBodySha256: report.authenticAckEvidence.querySourceSha256,
    querySourceSha256: report.authenticAckEvidence.querySourceSha256,
    ackBodySha256: report.authenticAckEvidence.ackSourceSha256,
    ackSourceSha256: report.authenticAckEvidence.ackSourceSha256,
    queryFrameRoundTrip: true,
    ackFrameRoundTrip: true,
    queryFrameSha256: report.authenticAckEvidence.expectedQueryFrameSha256,
    expectedQueryFrameSha256: report.authenticAckEvidence.expectedQueryFrameSha256,
    ackFrameSha256: report.authenticAckEvidence.expectedAckFrameSha256,
    expectedAckFrameSha256: report.authenticAckEvidence.expectedAckFrameSha256,
    sameAdapter: true,
    sameSocketGeneration: true,
  });
  assert.equal(report.authenticSourceEvidence.canonical, true);
  assert.equal(report.authenticSourceEvidence.frameRoundTrip, true);
  assert.equal(report.authenticSourceEvidence.bodySha256,
    report.authenticSourceEvidence.sourceSha256);
  assert.ok(report.authenticSourceEvidence.socketGeneration >= 1);
});

test('de HA-automation spreekt en echoot exact de gebonden offer-identiteit', () => {
  const automation = fs.readFileSync(path.resolve(
    import.meta.dirname, '../deploy/eva-voice-bridge-automation.yaml',
  ), 'utf8');
  assert.match(automation,
    /delivery_operation_ref: "\{\{ wait\.trigger\.event\.data\.delivery_operation_ref \}\}"/u);
  assert.match(automation,
    /payload_sha256: "\{\{ wait\.trigger\.event\.data\.payload_sha256 \}\}"/u);
  assert.match(automation,
    /reply_sha256: "\{\{ wait\.trigger\.event\.data\.reply_sha256 \}\}"/u);
  assert.match(automation,
    /reply: "\{\{ wait\.trigger\.event\.data\.reply \}\}"/u);
  assert.match(automation, /home-assistant-voice-reply:\[a-f0-9\]\{64\}/u);
});
