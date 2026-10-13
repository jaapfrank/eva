import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const fixture = path.join(import.meta.dirname, 'native-monitoring-v2-restart-fixture.mjs');

function runPhase(dataDir, phase, clockIso = null) {
  const child = spawnSync(process.execPath, [
    fixture, phase, dataDir, ...(clockIso ? [clockIso] : []),
  ], {
    cwd: path.resolve(import.meta.dirname, '..'),
    env: { ...process.env, TMPDIR: '/tmp', TMP: '/tmp', TEMP: '/tmp' },
    encoding: 'utf8', timeout: 30_000,
  });
  assert.equal(child.status, 0, `${child.stdout}\n${child.stderr}`);
  const line = child.stdout.split('\n')
    .find((entry) => entry.startsWith('NATIVE_MONITORING_V2_RESTART='));
  assert.ok(line, child.stdout);
  return JSON.parse(line.slice('NATIVE_MONITORING_V2_RESTART='.length));
}

test('MonitoringPendingDelivery/v2 overleeft een echte procesrestart zonder nieuwe read of judge', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-restart-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-pending');
  assert.deepEqual({
    episodeState: prepared.episodeState,
    pendingContract: prepared.pendingContract,
    pendingAttempts: prepared.pendingAttempts,
    receipts: prepared.receipts,
    acceptedSends: prepared.acceptedSends,
  }, {
    episodeState: 'delivery_pending',
    pendingContract: 'MonitoringPendingDelivery/v2',
    pendingAttempts: 1,
    receipts: 0,
    acceptedSends: 0,
  });
  assert.ok(prepared.pendingText);
  assert.equal(createHash('sha256').update(prepared.pendingText).digest('hex'),
    prepared.pendingTextSha256);
  // eslint-disable-next-line no-control-regex
  assert.doesNotMatch(prepared.pendingText, /sk-proj-|[\u0000-\u001f\u007f\u2013\u2014]/u,
    'rendererbytes verwijderen controls, typografische strepen en secretachtige labels');

  const recovered = runPhase(dataDir, 'recover');
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    judgeCalls: recovered.judgeCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    acceptedSends: 1, receipts: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
  assert.equal(recovered.newTransportRows, 1, 'exact één Telegram-child is geaccepteerd');
  assert.deepEqual({
    chatId: recovered.transportIntent.chatId,
    kind: recovered.transportIntent.kind,
    deliveryKey: recovered.transportIntent.deliveryKey,
    directTurnSignalRef: recovered.transportIntent.directTurnSignalRef,
    originPrincipal: recovered.transportIntent.originPrincipal,
    messageThreadId: recovered.transportIntent.sendOptions.message_thread_id,
    replyToMessageId: recovered.transportIntent.sendOptions.reply_parameters.message_id,
  }, {
    chatId: '424242', kind: 'text',
    deliveryKey: 'prepared:episode:1:monitoring-final',
    directTurnSignalRef: 'turn:telegram:424242:7358',
    originPrincipal: 'telegram:424242',
    messageThreadId: 17, replyToMessageId: 7358,
  });
  assert.equal(Object.hasOwn(recovered.transportIntent, 'broadcast'), false);
  assert.equal(Object.hasOwn(recovered.transportIntent, 'recipient'), false,
    'het childtransport gebruikt geen naamlookup');
  assert.equal(recovered.transportIntent.payloadSha256, prepared.pendingTextSha256);
  assert.equal(recovered.transportReceipt.accepted, true);
  assert.equal(recovered.transportReceipt.response.chatId, '424242');
  assert.equal(recovered.transportReceipt.response.messageThreadId, 17);
  assert.equal(recovered.transportReceipt.response.replyToMessageId, 7358);
  assert.equal(recovered.transportReceipt.response.textSha256, prepared.pendingTextSha256);
  assert.deepEqual({
    episodeId: recovered.deliveryReceipt.episodeId,
    originConversationId: recovered.deliveryReceipt.originConversationId,
    originMessageId: recovered.deliveryReceipt.originMessageId,
    deliveredConversationId: recovered.deliveryReceipt.deliveredConversationId,
    deliveredChannel: recovered.deliveryReceipt.deliveredChannel,
    deliveryTarget: recovered.deliveryReceipt.deliveryTarget,
    resultText: recovered.deliveryReceipt.resultText,
    idempotencyKey: recovered.deliveryReceipt.idempotencyKey,
  }, {
    episodeId: 1, originConversationId: 'telegram:424242', originMessageId: '7358',
    deliveredConversationId: 'telegram:424242', deliveredChannel: 'telegram',
    deliveryTarget: 'Jaap', resultText: prepared.pendingText,
    idempotencyKey: 'prepared:episode:1:monitoring-final',
  });
  const receiptBinding = recovered.deliveryReceipt.transport.monitoringBinding;
  assert.equal(receiptBinding.contract, 'MonitoringDeliveryReceiptBinding/v1');
  assert.equal(receiptBinding.pendingSha256, prepared.pendingSha256);
  assert.equal(receiptBinding.textSha256, prepared.pendingTextSha256);
  assert.equal(receiptBinding.lifecycleGeneration, 1);
  assert.match(receiptBinding.lifecycleSha256, /^[a-f0-9]{64}$/u);
  assert.match(receiptBinding.originBindingSha256, /^[a-f0-9]{64}$/u);
  assert.match(receiptBinding.outcomeReceiptSha256, /^[a-f0-9]{64}$/u);
  assert.match(receiptBinding.factSetSha256, /^[a-f0-9]{64}$/u);
  assert.deepEqual({
    chatId: recovered.assistantArchive.chatId,
    content: recovered.assistantArchive.content,
    replyToMessageId: recovered.assistantArchive.replyToMessageId,
    messageThreadId: recovered.assistantArchive.meta.messageThreadId,
    payloadSha256: recovered.assistantArchive.meta.payloadSha256,
  }, {
    chatId: '424242', content: prepared.pendingText, replyToMessageId: '7358',
    messageThreadId: '17', payloadSha256: prepared.pendingTextSha256,
  });
  const replay = runPhase(dataDir, 'recover');
  assert.deepEqual({
    providerCalls: replay.providerCalls,
    homeReads: replay.homeReads,
    acceptedSends: replay.acceptedSends,
    receipts: replay.receipts,
  }, { providerCalls: 0, homeReads: 0, acceptedSends: 0, receipts: 1 });
});

test('niet-authoritatieve werkstarthint blijft bruikbaar bij resume en directe levering', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-start-hint-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-hinted-pending');
  assert.deepEqual({
    workStateHint: prepared.workStateHint,
    episodeState: prepared.episodeState,
    pendingContract: prepared.pendingContract,
    judgeCalls: prepared.judgeCalls,
    acceptedSends: prepared.acceptedSends,
  }, {
    workStateHint: 'Controleer uitsluitend de afgesproken grens.',
    episodeState: 'delivery_pending',
    pendingContract: 'MonitoringPendingDelivery/v2',
    judgeCalls: 1, acceptedSends: 0,
  }, JSON.stringify(prepared));
  const recovered = runPhase(dataDir, 'recover');
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    judgeCalls: recovered.judgeCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    acceptedSends: 1, receipts: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
  assert.match(recovered.deliveryReceipt.transport.maxAcceptedAt,
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u);
  assert.ok(Date.parse(recovered.deliveryReceipt.transport.maxAcceptedAt)
    <= Date.parse(prepared.pendingExpiresAt));
});

test('tweede geldige outcome-poging wordt na transient transport duurzaam herlezen', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-outcome-attempt2-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-outcome-retry-pending');
  assert.deepEqual({
    judgeCalls: prepared.judgeCalls,
    episodeState: prepared.episodeState,
    pendingContract: prepared.pendingContract,
    acceptedSends: prepared.acceptedSends,
    receipts: prepared.receipts,
  }, {
    judgeCalls: 2, episodeState: 'delivery_pending',
    pendingContract: 'MonitoringPendingDelivery/v2',
    acceptedSends: 0, receipts: 0,
  }, JSON.stringify(prepared));
  const recovered = runPhase(dataDir, 'recover');
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    judgeCalls: recovered.judgeCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    acceptedSends: 1, receipts: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
});

test('scheduler bewaart retryruimte en bezorgt via een fresh-process tick vóór de horizon', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-scheduler-retry-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(
    dataDir, 'prepare-scheduler-retry', '2026-08-15T10:00:00.000Z',
  );
  assert.deepEqual({
    episodeState: prepared.episodeState,
    pendingContract: prepared.pendingContract,
    pendingAttempts: prepared.pendingAttempts,
    messengerCalls: prepared.messengerCalls,
    acceptedSends: prepared.acceptedSends,
    receipts: prepared.receipts,
    newTransportRows: prepared.newTransportRows,
  }, {
    episodeState: 'delivery_pending', pendingContract: 'MonitoringPendingDelivery/v2',
    pendingAttempts: 1, messengerCalls: 1, acceptedSends: 0, receipts: 0,
    newTransportRows: 0,
  }, JSON.stringify(prepared));
  assert.equal(prepared.workNextWakeTs, prepared.wakeTs);
  assert.equal(prepared.wait?.resumeAfter, prepared.wakeTs);
  assert.equal(prepared.wait?.reason, 'delivery_retry');
  assert.ok(Date.parse(prepared.wakeTs) > Date.parse(prepared.clockNow));
  assert.ok(Date.parse(prepared.wakeTs) <= Date.parse(prepared.pendingExpiresAt) - 120_000,
    JSON.stringify(prepared));
  assert.equal(prepared.scheduler?.next_run_ts, prepared.wakeTs,
    'de echte scheduler bewaart dezelfde vroegste durable wake als hint');

  const recovered = runPhase(dataDir, 'scheduler-recover', new Date(
    Date.parse(prepared.wakeTs) + 1,
  ).toISOString());
  assert.deepEqual(recovered.schedulerUrgencyBefore, {
    workPresent: true, workStatus: 'open', workWakeTs: prepared.wakeTs,
    wakeFinite: true, wakeDue: true, pendingSha256: prepared.pendingSha256,
    choicePresent: true, choiceEpisodeState: 'delivery_pending',
    authorityPresent: true, authorityPendingPresent: true,
  }, JSON.stringify(recovered));
  assert.equal(recovered.schedulerStartupStateSha256, prepared.stateJsonSha256,
    'cold startup bewaart de geldige verzegelde monitoringstate bytegelijk vóór tick');
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    judgeCalls: recovered.judgeCalls,
    messengerCalls: recovered.messengerCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    newHaLees: recovered.newHaLees,
    newTransportRows: recovered.newTransportRows,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    messengerCalls: 1, acceptedSends: 1, receipts: 1,
    newHaLees: 0, newTransportRows: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
});

test('due monitoring houdt cold-start, work_changed en busy tick op maximaal vijftien seconden', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-scheduler-guards-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(
    dataDir, 'prepare-scheduler-retry', '2026-08-15T10:00:00.000Z',
  );
  const now = new Date(Date.parse(prepared.wakeTs) + 1).toISOString();
  const guarded = runPhase(dataDir, 'scheduler-guard-recover', now);
  assert.deepEqual(guarded.schedulerUrgencyBefore, {
    workPresent: true, workStatus: 'open', workWakeTs: prepared.wakeTs,
    wakeFinite: true, wakeDue: true, pendingSha256: prepared.pendingSha256,
    choicePresent: true, choiceEpisodeState: 'delivery_pending',
    authorityPresent: true, authorityPendingPresent: true,
  }, JSON.stringify(guarded));
  assert.equal(guarded.schedulerStartupStateSha256, prepared.stateJsonSha256);
  for (const field of [
    'coldNextRunTs', 'workChangedNextRunTs', 'busyNextRunTs',
  ]) {
    const delay = Date.parse(guarded.schedulerGuard?.[field] || '') - Date.parse(now);
    assert.ok(delay >= -1 && delay <= 15_000,
      `${field} moet de due delivery binnen vijftien seconden opnieuw oppakken: ${JSON.stringify(guarded)}`);
  }
  assert.ok(guarded.schedulerGuard.coldTimerDelay <= 15_000);
  assert.ok(guarded.schedulerGuard.workChangedTimerDelay <= 15_000);
  assert.equal(guarded.schedulerGuard.busyCurrentSelection, null,
    'een busy tick mag geen andere commitment kiezen');
  assert.deepEqual({
    providerCalls: guarded.providerCalls,
    homeReads: guarded.homeReads,
    judgeCalls: guarded.judgeCalls,
    messengerCalls: guarded.messengerCalls,
    acceptedSends: guarded.acceptedSends,
    receipts: guarded.receipts,
    newTransportRows: guarded.newTransportRows,
    episodeState: guarded.episodeState,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    messengerCalls: 0, acceptedSends: 0, receipts: 0,
    newTransportRows: 0, episodeState: 'delivery_pending',
  }, JSON.stringify(guarded));
});

test('due monitoringdelivery wordt niet overruled door een nabije andere bewonerswake', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-overrule-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(
    dataDir, 'prepare-scheduler-retry', '2026-08-15T10:00:00.000Z',
  );
  const recovered = runPhase(dataDir, 'scheduler-overrule-recover', new Date(
    Date.parse(prepared.wakeTs) + 1,
  ).toISOString());
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    judgeCalls: recovered.judgeCalls,
    messengerCalls: recovered.messengerCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    newHaLees: recovered.newHaLees,
    newTransportRows: recovered.newTransportRows,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    messengerCalls: 1, acceptedSends: 1, receipts: 1,
    newHaLees: 0, newTransportRows: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
  assert.deepEqual({
    status: recovered.schedulerCompetitor?.status,
    attemptCount: recovered.schedulerCompetitor?.attemptCount,
    storedWakeTs: recovered.schedulerCompetitor?.storedWakeTs,
  }, {
    status: 'open', attemptCount: 0,
    storedWakeTs: recovered.schedulerCompetitor?.wakeTs,
  }, 'de nabije andere commitment mag niet worden geclaimd of herschikt');
});

test('completion van een andere autonomy-run bewaart een intussen due monitoringwake', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-crossing-wake-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(
    dataDir, 'prepare-scheduler-retry', '2026-08-15T10:00:00.000Z',
  );
  const recovered = runPhase(
    dataDir, 'scheduler-crossing-recover', prepared.clockNow,
  );
  const completionDelay = Date.parse(recovered.schedulerCrossing?.completionNextRunTs || '')
    - Date.parse(recovered.schedulerCrossing?.completionAt || '');
  assert.ok(completionDelay >= 0 && completionDelay <= 15_000,
    `completion moet de intussen due monitoring binnen 15s repollen: ${JSON.stringify(recovered)}`);
  assert.equal(recovered.schedulerCrossing?.completionLastResult?.technical_retry, undefined,
    'de andere beurt moet geldig zonder technische fout eindigen');
  assert.equal(recovered.schedulerCrossing?.completionLastResult?.progressed, false);
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    judgeCalls: recovered.judgeCalls,
    messengerCalls: recovered.messengerCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    newHaLees: recovered.newHaLees,
    newTransportRows: recovered.newTransportRows,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 1, homeReads: 0, judgeCalls: 0,
    messengerCalls: 1, acceptedSends: 1, receipts: 1,
    newHaLees: 0, newTransportRows: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
});

test('exception van een andere autonomy-run bewaart een intussen due monitoringwake', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-crossing-error-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(
    dataDir, 'prepare-scheduler-retry', '2026-08-15T10:00:00.000Z',
  );
  const recovered = runPhase(
    dataDir, 'scheduler-crossing-error-recover', prepared.clockNow,
  );
  const completionDelay = Date.parse(recovered.schedulerCrossing?.completionNextRunTs || '')
    - Date.parse(recovered.schedulerCrossing?.completionAt || '');
  assert.ok(completionDelay >= 0 && completionDelay <= 15_000,
    `catch moet de intussen due monitoring binnen 15s repollen: ${JSON.stringify(recovered)}`);
  assert.equal(recovered.schedulerCrossing?.completionLastResult?.technical_retry, true,
    'de andere beurt moet als technische fout worden vastgelegd');
  assert.equal(recovered.schedulerCrossing?.completionLastResult?.error,
    'scheduler-crossing-test-exception', 'de proef moet de echte scheduler-catch raken');
  assert.deepEqual({
    leaseOwner: recovered.schedulerCrossing?.competingLeaseOwner,
    leaseUntil: recovered.schedulerCrossing?.competingLeaseUntil,
  }, { leaseOwner: null, leaseUntil: null },
  'de exception-catch moet de concurrerende werklease vrijgeven');
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    judgeCalls: recovered.judgeCalls,
    messengerCalls: recovered.messengerCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    newHaLees: recovered.newHaLees,
    newTransportRows: recovered.newTransportRows,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    messengerCalls: 1, acceptedSends: 1, receipts: 1,
    newHaLees: 0, newTransportRows: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
});

test('monitoring zonder veilige retrywake verwijdert stale pending voor een verse observatie', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-no-retry-window-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const blocked = runPhase(
    dataDir, 'prepare-scheduler-no-window', '2026-08-15T10:00:00.000Z',
  );
  assert.deepEqual({
    episodeState: blocked.episodeState,
    pendingContract: blocked.pendingContract,
    messengerCalls: blocked.messengerCalls,
    acceptedSends: blocked.acceptedSends,
    receipts: blocked.receipts,
    newTransportRows: blocked.newTransportRows,
    lastError: blocked.lastError,
  }, {
    episodeState: 'waiting', pendingContract: null,
    messengerCalls: 1, acceptedSends: 0, receipts: 0, newTransportRows: 0,
    lastError: 'monitoring_delivery_fresh_observation_required',
  }, JSON.stringify(blocked));
  assert.equal(blocked.wakeTs, blocked.workNextWakeTs);
  assert.equal(blocked.wait?.resumeAfter, blocked.wakeTs);
  assert.equal(blocked.wait?.reason, 'monitoring_resume');
  assert.ok(Date.parse(blocked.wakeTs) > Date.parse(blocked.clockNow));
  assert.ok(Date.parse(blocked.wakeTs) - Date.parse(blocked.clockNow) <= 15_000);
});

test('monitoring start na de H-90 wiregrens geen nieuw Telegramtransport', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-wire-expired-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-pending');
  const tooLate = new Date(Date.parse(prepared.pendingExpiresAt) - 90_000 + 1).toISOString();
  const rejected = runPhase(dataDir, 'recover', tooLate);
  assert.deepEqual({
    providerCalls: rejected.providerCalls,
    homeReads: rejected.homeReads,
    judgeCalls: rejected.judgeCalls,
    messengerCalls: rejected.messengerCalls,
    acceptedSends: rejected.acceptedSends,
    receipts: rejected.receipts,
    newTransportRows: rejected.newTransportRows,
    episodeState: rejected.episodeState,
    pendingContract: rejected.pendingContract,
    lastError: rejected.lastError,
  }, {
    providerCalls: 0, homeReads: 0, judgeCalls: 0,
    messengerCalls: 0, acceptedSends: 0, receipts: 0, newTransportRows: 0,
    episodeState: 'waiting', pendingContract: null,
    lastError: 'monitoring_delivery_fresh_observation_required',
  }, JSON.stringify(rejected));
});

test('startup zet een malformed MonitoringPendingDelivery/v2 in quarantaine vóór scheduler-I/O', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-startup-invalid-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(
    dataDir, 'prepare-scheduler-retry', '2026-08-15T10:00:00.000Z',
  );
  runPhase(dataDir, 'tamper:wrong-lifecycle-generation', prepared.clockNow);
  const rejected = runPhase(dataDir, 'scheduler-recover', new Date(
    Date.parse(prepared.wakeTs) + 1,
  ).toISOString());
  assert.deepEqual({
    providerCalls: rejected.providerCalls,
    judgeCalls: rejected.judgeCalls,
    homeReads: rejected.homeReads,
    messengerCalls: rejected.messengerCalls,
    acceptedSends: rejected.acceptedSends,
    receipts: rejected.receipts,
    newHaLees: rejected.newHaLees,
    newTransportRows: rejected.newTransportRows,
    episodeState: rejected.episodeState,
    wakeTs: rejected.wakeTs,
    lastError: rejected.lastError,
  }, {
    providerCalls: 0, judgeCalls: 0, homeReads: 0,
    messengerCalls: 0, acceptedSends: 0, receipts: 0,
    newHaLees: 0, newTransportRows: 0,
    episodeState: 'blocked', wakeTs: null,
    lastError: 'monitoring_startup_pending_invalid',
  }, JSON.stringify(rejected));
  assert.ok(Date.parse(rejected.scheduler.next_run_ts) - Date.parse(rejected.clockNow) > 15_000,
    'een malformed v2 mag niet als urgente geldige delivery blijven repollen');
});

test('startup laat ontbrekende, legacy of te laat geplande monitoringpending niet slapen', (t) => {
  const cases = [
    'missing-pending-future',
    'legacy-pending-future',
    'late-pending-wake',
  ];
  for (const tamper of cases) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-monitoring-v2-${tamper}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    const prepared = runPhase(
      dataDir, 'prepare-scheduler-retry', '2026-08-15T10:00:00.000Z',
    );
    runPhase(dataDir, `tamper:${tamper}`, prepared.clockNow);
    const rejected = runPhase(dataDir, 'startup-inspect', prepared.clockNow);
    assert.deepEqual({
      providerCalls: rejected.providerCalls,
      judgeCalls: rejected.judgeCalls,
      homeReads: rejected.homeReads,
      messengerCalls: rejected.messengerCalls,
      acceptedSends: rejected.acceptedSends,
      receipts: rejected.receipts,
      newHaLees: rejected.newHaLees,
      newTransportRows: rejected.newTransportRows,
      episodeState: rejected.episodeState,
      wakeTs: rejected.wakeTs,
      lastError: rejected.lastError,
    }, {
      providerCalls: 0, judgeCalls: 0, homeReads: 0,
      messengerCalls: 0, acceptedSends: 0, receipts: 0,
      newHaLees: 0, newTransportRows: 0,
      episodeState: 'blocked', wakeTs: null,
      lastError: 'monitoring_startup_pending_invalid',
    }, `${tamper}: ${JSON.stringify(rejected)}`);
  }
});

test('Telegram-acceptatie vóór portfolio-receipt wordt na restart gesloten zonder resend', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-accepted-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-pending');
  const accepted = runPhase(dataDir, 'accept-transport-crash');
  assert.deepEqual({
    acceptedSends: accepted.acceptedSends,
    receipts: accepted.receipts,
    episodeState: accepted.episodeState,
    transportStatus: accepted.transportStatus,
  }, {
    acceptedSends: 1, receipts: 0, episodeState: 'delivery_pending',
    transportStatus: 'transport_accepted',
  });
  const recovered = runPhase(dataDir, 'recover-accepted', new Date(
    Date.parse(prepared.pendingExpiresAt) + 1,
  ).toISOString());
  assert.deepEqual({
    providerCalls: recovered.providerCalls,
    homeReads: recovered.homeReads,
    messengerCalls: recovered.messengerCalls,
    acceptedSends: recovered.acceptedSends,
    receipts: recovered.receipts,
    episodeState: recovered.episodeState,
    workStatus: recovered.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, messengerCalls: 0,
    acceptedSends: 0, receipts: 1,
    episodeState: 'closed', workStatus: 'done',
  }, JSON.stringify(recovered));
});

test('Telegramacceptatie na de facthorizon sluit geen monitoringreceipt', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-late-accepted-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-pending');
  const afterHorizon = new Date(Date.parse(prepared.pendingExpiresAt) + 1).toISOString();
  const accepted = runPhase(dataDir, 'accept-transport-crash', afterHorizon);
  assert.deepEqual({
    acceptedSends: accepted.acceptedSends,
    receipts: accepted.receipts,
    transportStatus: accepted.transportStatus,
  }, { acceptedSends: 1, receipts: 0, transportStatus: 'transport_accepted' },
  JSON.stringify(accepted));
  const rejected = runPhase(dataDir, 'recover-accepted', new Date(
    Date.parse(afterHorizon) + 1,
  ).toISOString());
  assert.deepEqual({
    providerCalls: rejected.providerCalls,
    homeReads: rejected.homeReads,
    messengerCalls: rejected.messengerCalls,
    acceptedSends: rejected.acceptedSends,
    receipts: rejected.receipts,
    episodeState: rejected.episodeState,
    workStatus: rejected.workStatus,
  }, {
    providerCalls: 0, homeReads: 0, messengerCalls: 0,
    acceptedSends: 0, receipts: 0,
    episodeState: 'blocked', workStatus: 'blocked',
  }, JSON.stringify(rejected));
});

test('fresh-import monitoring weigert iedere ontbrekende of gemanipuleerde durable authority vóór effecten', (t) => {
  const cases = [
    'missing-start-execution',
    'corrupt-start-execution',
    'missing-start-authority',
    'missing-understanding',
    'missing-fact',
    'missing-outcome',
    'legacy-contract',
    'legacy-pending',
    'expired',
    'attempts-100',
    'duplicate-fact-refs',
    'wrong-lifecycle-generation',
  ];
  for (const tamper of cases) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-monitoring-v2-${tamper}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    runPhase(dataDir, 'prepare-pending');
    runPhase(dataDir, `tamper:${tamper}`);
    const rejected = runPhase(dataDir, 'recover');
    assert.deepEqual({
      providerCalls: rejected.providerCalls,
      judgeCalls: rejected.judgeCalls,
      homeReads: rejected.homeReads,
      acceptedSends: rejected.acceptedSends,
      receipts: rejected.receipts,
      newHuisStatus: rejected.newHuisStatus,
      newHaLees: rejected.newHaLees,
      newWerkBijhouden: rejected.newWerkBijhouden,
      newTransportRows: rejected.newTransportRows,
      newTelegramArchiveRows: rejected.newTelegramArchiveRows,
      episodeState: rejected.episodeState,
      wakeTs: rejected.wakeTs,
    }, {
      providerCalls: 0, judgeCalls: 0, homeReads: 0,
      acceptedSends: 0, receipts: 0,
      newHuisStatus: 0, newHaLees: 0, newWerkBijhouden: 0,
      newTransportRows: 0, newTelegramArchiveRows: 0,
      episodeState: 'blocked', wakeTs: null,
    }, `${tamper}: ${JSON.stringify(rejected)}`);
    assert.match(String(rejected.lastError || ''), /^(?:monitoring_|prepared_delivery_integrity$)/u,
      `${tamper}: een getypeerde monitoringblokkade ontbreekt`);
  }
});

test('pending-rehydration weigert self-consistente observatie- en outcome-lineage drift', (t) => {
  const cases = [
    'observation-extra-args',
    'observation-wrong-addresses',
    'observation-nonempty-basis',
    'observation-execution-type',
    'outcome-wrong-turn',
    'outcome-wrong-key',
    'outcome-extra-args',
    'outcome-wrong-attempt',
    'outcome-attempt-overflow',
    'outcome-wrong-lineage',
  ];
  for (const tamper of cases) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-monitoring-v2-${tamper}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    runPhase(dataDir, 'prepare-pending');
    runPhase(dataDir, `tamper:${tamper}`);
    const rejected = runPhase(dataDir, 'recover');
    assert.deepEqual({
      providerCalls: rejected.providerCalls,
      judgeCalls: rejected.judgeCalls,
      homeReads: rejected.homeReads,
      messengerCalls: rejected.messengerCalls,
      acceptedSends: rejected.acceptedSends,
      receipts: rejected.receipts,
      newHuisStatus: rejected.newHuisStatus,
      newHaLees: rejected.newHaLees,
      newWerkBijhouden: rejected.newWerkBijhouden,
      newStuurBericht: rejected.newStuurBericht,
      newTransportRows: rejected.newTransportRows,
      newTelegramArchiveRows: rejected.newTelegramArchiveRows,
      episodeState: rejected.episodeState,
      wakeTs: rejected.wakeTs,
      lastError: rejected.lastError,
    }, {
      providerCalls: 0, judgeCalls: 0, homeReads: 0,
      messengerCalls: 0, acceptedSends: 0, receipts: 0,
      newHuisStatus: 0, newHaLees: 0, newWerkBijhouden: 0,
      newStuurBericht: 0, newTransportRows: 0, newTelegramArchiveRows: 0,
      episodeState: 'blocked', wakeTs: null,
      lastError: 'prepared_delivery_integrity',
    }, `${tamper}: ${JSON.stringify(rejected)}`);
  }
});

test('pending-rehydration weigert self-consistente outcome-request en outputdrift', (t) => {
  const cases = [
    'outcome-fake-request-sha',
    'outcome-missing-response-sha',
    'outcome-fake-response-sha',
    'outcome-accepted-failure',
    'outcome-missing-failure',
    'outcome-extra-output',
  ];
  for (const tamper of cases) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-monitoring-v2-${tamper}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    runPhase(dataDir, 'prepare-pending');
    runPhase(dataDir, `tamper:${tamper}`);
    const rejected = runPhase(dataDir, 'recover');
    assert.deepEqual({
      providerCalls: rejected.providerCalls,
      judgeCalls: rejected.judgeCalls,
      homeReads: rejected.homeReads,
      messengerCalls: rejected.messengerCalls,
      acceptedSends: rejected.acceptedSends,
      receipts: rejected.receipts,
      newHaLees: rejected.newHaLees,
      newStuurBericht: rejected.newStuurBericht,
      newTransportRows: rejected.newTransportRows,
      episodeState: rejected.episodeState,
      wakeTs: rejected.wakeTs,
      lastError: rejected.lastError,
    }, {
      providerCalls: 0, judgeCalls: 0, homeReads: 0,
      messengerCalls: 0, acceptedSends: 0, receipts: 0,
      newHaLees: 0, newStuurBericht: 0, newTransportRows: 0,
      episodeState: 'blocked', wakeTs: null,
      lastError: 'prepared_delivery_integrity',
    }, `${tamper}: ${JSON.stringify(rejected)}`);
  }
});

test('werkstartauthority weigert host-owned toestandvelden maar negeert gewone hints', (t) => {
  const cases = [
    'start-extra-monitoring-runtime',
    'start-extra-pending-delivery',
    'start-extra-monitoring-lifecycle',
    'start-extra-start-authority',
    'start-extra-delivery-origin',
  ];
  for (const tamper of cases) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-monitoring-v2-${tamper}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    runPhase(dataDir, 'prepare-pending');
    runPhase(dataDir, `tamper:${tamper}`);
    const rejected = runPhase(dataDir, 'recover');
    assert.deepEqual({
      providerCalls: rejected.providerCalls,
      judgeCalls: rejected.judgeCalls,
      homeReads: rejected.homeReads,
      messengerCalls: rejected.messengerCalls,
      acceptedSends: rejected.acceptedSends,
      receipts: rejected.receipts,
      newHaLees: rejected.newHaLees,
      newTransportRows: rejected.newTransportRows,
      episodeState: rejected.episodeState,
      wakeTs: rejected.wakeTs,
    }, {
      providerCalls: 0, judgeCalls: 0, homeReads: 0,
      messengerCalls: 0, acceptedSends: 0, receipts: 0,
      newHaLees: 0, newTransportRows: 0,
      episodeState: 'blocked', wakeTs: null,
    }, `${tamper}: ${JSON.stringify(rejected)}`);
  }
});

test('retry 99 mag exact eenmaal proberen en sluit na de mislukking als uitgeput', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-attempts-99-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  runPhase(dataDir, 'prepare-pending');
  runPhase(dataDir, 'tamper:attempts-99');
  const exhausted = runPhase(dataDir, 'recover-fail');
  assert.deepEqual({
    providerCalls: exhausted.providerCalls,
    judgeCalls: exhausted.judgeCalls,
    homeReads: exhausted.homeReads,
    messengerCalls: exhausted.messengerCalls,
    acceptedSends: exhausted.acceptedSends,
    receipts: exhausted.receipts,
    newStuurBericht: exhausted.newStuurBericht,
    newTransportRows: exhausted.newTransportRows,
    newTelegramArchiveRows: exhausted.newTelegramArchiveRows,
    episodeState: exhausted.episodeState,
    wakeTs: exhausted.wakeTs,
  }, {
    providerCalls: 0, judgeCalls: 0, homeReads: 0,
    messengerCalls: 1, acceptedSends: 0, receipts: 0,
    newStuurBericht: 0, newTransportRows: 0, newTelegramArchiveRows: 0,
    episodeState: 'blocked', wakeTs: null,
  }, JSON.stringify(exhausted));
  assert.match(String(exhausted.lastError || ''), /^monitoring_/u);

  const replay = runPhase(dataDir, 'recover');
  assert.deepEqual({
    providerCalls: replay.providerCalls, homeReads: replay.homeReads,
    acceptedSends: replay.acceptedSends, receipts: replay.receipts,
    newStuurBericht: replay.newStuurBericht,
    newTransportRows: replay.newTransportRows,
    newTelegramArchiveRows: replay.newTelegramArchiveRows,
    episodeState: replay.episodeState, wakeTs: replay.wakeTs,
  }, {
    providerCalls: 0, homeReads: 0, acceptedSends: 0, receipts: 0,
    newStuurBericht: 0, newTransportRows: 0, newTelegramArchiveRows: 0,
    episodeState: 'blocked', wakeTs: null,
  });
});

test('receipt-closure herverifieert lifecycle nadat Telegram transport al accepteerde', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-receipt-race-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  runPhase(dataDir, 'prepare-pending');
  const raced = runPhase(dataDir, 'recover-race-state');
  assert.deepEqual({
    providerCalls: raced.providerCalls,
    judgeCalls: raced.judgeCalls,
    homeReads: raced.homeReads,
    messengerCalls: raced.messengerCalls,
    acceptedSends: raced.acceptedSends,
    receipts: raced.receipts,
    newTransportRows: raced.newTransportRows,
    newTelegramArchiveRows: raced.newTelegramArchiveRows,
    episodeState: raced.episodeState,
    wakeTs: raced.wakeTs,
  }, {
    providerCalls: 0, judgeCalls: 0, homeReads: 0,
    messengerCalls: 1, acceptedSends: 1, receipts: 0,
    newTransportRows: 1, newTelegramArchiveRows: 1,
    episodeState: 'blocked', wakeTs: null,
  }, JSON.stringify(raced));
  assert.match(String(raced.lastError || ''), /^monitoring_/u);
});

test('portfolio receipt-transactie weigert een lifecycle-race na transportacceptatie', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-closure-race-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  runPhase(dataDir, 'prepare-pending');
  const raced = runPhase(dataDir, 'receipt-closure-race');
  assert.deepEqual({
    acceptedSends: raced.acceptedSends,
    receipts: raced.receipts,
    newTransportRows: raced.newTransportRows,
    newTelegramArchiveRows: raced.newTelegramArchiveRows,
    episodeState: raced.episodeState,
  }, {
    acceptedSends: 1, receipts: 0,
    newTransportRows: 1, newTelegramArchiveRows: 1,
    episodeState: 'delivery_pending',
  }, JSON.stringify(raced));
  assert.match(String(raced.closureError || ''), /monitoring/iu,
    'de atomische receiptgrens moet de monitoringrace getypeerd weigeren');
});

test('monitoringreceipt weigert echt Telegrambewijs van een andere niet-directe delivery', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-route-proof-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  runPhase(dataDir, 'prepare-pending');
  const rejected = runPhase(dataDir, 'receipt-wrong-route-proof');
  assert.deepEqual({
    acceptedSends: rejected.acceptedSends,
    receipts: rejected.receipts,
    newTransportRows: rejected.newTransportRows,
    newTelegramArchiveRows: rejected.newTelegramArchiveRows,
    episodeState: rejected.episodeState,
  }, {
    acceptedSends: 1, receipts: 0,
    newTransportRows: 1, newTelegramArchiveRows: 1,
    episodeState: 'delivery_pending',
  }, JSON.stringify(rejected));
  assert.match(String(rejected.closureError || ''), /Telegram/iu);
  assert.equal(rejected.transportIntent.deliveryKey,
    'prepared:wrong-monitoring-delivery');
});

test('self-rehashed meetbinding kan niet van de onveranderde issuance-ledger afwijken', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-binding-redirect-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const waiting = runPhase(dataDir, 'prepare-waiting');
  assert.equal(waiting.episodeState, 'waiting');
  runPhase(dataDir, 'tamper:self-rehashed-binding-redirect');
  const rejected = runPhase(dataDir, 'recover');
  assert.deepEqual({
    providerCalls: rejected.providerCalls,
    judgeCalls: rejected.judgeCalls,
    homeReads: rejected.homeReads,
    messengerCalls: rejected.messengerCalls,
    acceptedSends: rejected.acceptedSends,
    receipts: rejected.receipts,
    newHuisStatus: rejected.newHuisStatus,
    newHaLees: rejected.newHaLees,
    newTransportRows: rejected.newTransportRows,
    episodeState: rejected.episodeState,
    wakeTs: rejected.wakeTs,
  }, {
    providerCalls: 0, judgeCalls: 0, homeReads: 0,
    messengerCalls: 0, acceptedSends: 0, receipts: 0,
    newHuisStatus: 0, newHaLees: 0, newTransportRows: 0,
    episodeState: 'blocked', wakeTs: null,
  }, JSON.stringify(rejected));
  assert.equal(rejected.lastError, 'prepared_delivery_integrity');
});

test('self-rehashed contractsemantiek kan niet van de werk-startledger afwijken', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-contract-redirect-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const waiting = runPhase(dataDir, 'prepare-waiting');
  assert.equal(waiting.episodeState, 'waiting');
  runPhase(dataDir, 'tamper:self-rehashed-contract-semantics');
  const rejected = runPhase(dataDir, 'recover');
  assert.deepEqual({
    providerCalls: rejected.providerCalls,
    judgeCalls: rejected.judgeCalls,
    homeReads: rejected.homeReads,
    messengerCalls: rejected.messengerCalls,
    acceptedSends: rejected.acceptedSends,
    receipts: rejected.receipts,
    newHuisStatus: rejected.newHuisStatus,
    newHaLees: rejected.newHaLees,
    newTransportRows: rejected.newTransportRows,
    episodeState: rejected.episodeState,
    wakeTs: rejected.wakeTs,
    lastError: rejected.lastError,
  }, {
    providerCalls: 0, judgeCalls: 0, homeReads: 0,
    messengerCalls: 0, acceptedSends: 0, receipts: 0,
    newHuisStatus: 0, newHaLees: 0, newTransportRows: 0,
    episodeState: 'blocked', wakeTs: null,
    lastError: 'prepared_delivery_integrity',
  }, JSON.stringify(rejected));
});

test('discovery-issued binding weigert afwijkende rol, basisreceipt en requirement', (t) => {
  const cases = [
    'issuance-wrong-role',
    'issuance-basis-missing',
    'issuance-basis-extra',
    'issuance-basis-wrong',
    'discovery-wrong-requirement',
  ];
  for (const tamper of cases) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `eva-monitoring-v2-${tamper}-`));
    t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
    const waiting = runPhase(dataDir, 'prepare-discovery-waiting');
    assert.equal(waiting.episodeState, 'waiting', `${tamper}: ${JSON.stringify(waiting)}`);
    runPhase(dataDir, `tamper:${tamper}`);
    const rejected = runPhase(dataDir, 'recover');
    assert.deepEqual({
      providerCalls: rejected.providerCalls,
      judgeCalls: rejected.judgeCalls,
      homeReads: rejected.homeReads,
      messengerCalls: rejected.messengerCalls,
      acceptedSends: rejected.acceptedSends,
      receipts: rejected.receipts,
      newHuisStatus: rejected.newHuisStatus,
      newHaLees: rejected.newHaLees,
      newTransportRows: rejected.newTransportRows,
      episodeState: rejected.episodeState,
      wakeTs: rejected.wakeTs,
      lastError: rejected.lastError,
    }, {
      providerCalls: 0, judgeCalls: 0, homeReads: 0,
      messengerCalls: 0, acceptedSends: 0, receipts: 0,
      newHuisStatus: 0, newHaLees: 0, newTransportRows: 0,
      episodeState: 'blocked', wakeTs: null,
      lastError: 'prepared_delivery_integrity',
    }, `${tamper}: ${JSON.stringify(rejected)}`);
  }
});

test('monitoring herverifieert current vóór ieder Telegram-child', (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-monitoring-v2-child-race-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const prepared = runPhase(dataDir, 'prepare-pending');
  assert.ok(prepared.pendingText.length > 40,
    'de test moet productionbytes via de test-only Telegramgrens in meerdere chunks splitsen');
  const raced = runPhase(dataDir, 'recover-multichunk-race');
  assert.deepEqual({
    providerCalls: raced.providerCalls,
    homeReads: raced.homeReads,
    acceptedSends: raced.acceptedSends,
    receipts: raced.receipts,
    episodeState: raced.episodeState,
    wakeTs: raced.wakeTs,
  }, {
    providerCalls: 0, homeReads: 0,
    acceptedSends: 1, receipts: 0,
    episodeState: 'blocked', wakeTs: null,
  }, JSON.stringify(raced));
  assert.equal(raced.deletedMessages, 1,
    'de reeds geaccepteerde prefix wordt na authoritydrift ingetrokken');
});
