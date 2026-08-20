import assert from 'node:assert/strict';
import test from 'node:test';

import {
  openLabAdapterSession,
  runLabToolSequence,
} from '../scripts/lab/adapter-contract.mjs';

const HASH = 'a'.repeat(64);

function fakeAdapter(events) {
  return {
    describe: () => ({
      contract: 'LabAdapterDescriptor/v1', participantId: 'fake-agent', version: '1.0.0',
      sourceDigest: HASH, configDigest: 'b'.repeat(64), adapterDigest: 'c'.repeat(64),
      lanes: ['controlled'], tracks: ['sim'],
    }),
    prepare: async () => ({ sessionId: 'fake-session-1' }),
    beginCase: async () => ({ accepted: true }),
    send: async () => ({ accepted: true, ingressId: 'ingress-1' }),
    observe: async () => events,
    endCase: async () => ({ accepted: true }),
    close: async () => ({ accepted: true }),
  };
}

function sequenceEvents() {
  return [
    { sequence: 1, kind: 'tool_call', callId: 'c1', tool: 'fetch_api', argsSha256: '1'.repeat(64) },
    { sequence: 2, kind: 'tool_result', callId: 'c1', tool: 'fetch_api', argsSha256: '1'.repeat(64) },
    { sequence: 3, kind: 'tool_call', callId: 'c2', tool: 'filter_data', argsSha256: '2'.repeat(64) },
    { sequence: 4, kind: 'tool_result', callId: 'c2', tool: 'filter_data', argsSha256: '2'.repeat(64) },
    { sequence: 5, kind: 'tool_call', callId: 'c3', tool: 'write_file', argsSha256: '3'.repeat(64) },
    { sequence: 6, kind: 'tool_result', callId: 'c3', tool: 'write_file', argsSha256: '3'.repeat(64) },
    { sequence: 7, kind: 'case_complete', callId: null, tool: null, argsSha256: null },
  ];
}

test('adaptercontract bindt participant/config en valideert een exacte drietoolsreeks', async () => {
  const session = await openLabAdapterSession(fakeAdapter(sequenceEvents()), {
    runId: 'lab-run-1', participantId: 'fake-agent', lane: 'controlled', track: 'sim',
    controlledConfigRef: 'config:sealed', effectCapability: Object.freeze({}),
  });
  const result = await runLabToolSequence(session, {
    caseInstanceId: `lab:${'d'.repeat(64)}`,
    input: { kind: 'text', contentSha256: 'e'.repeat(64) },
    expected: [
      { tool: 'fetch_api', argsSha256: '1'.repeat(64) },
      { tool: 'filter_data', argsSha256: '2'.repeat(64) },
      { tool: 'write_file', argsSha256: '3'.repeat(64) },
    ],
  });
  assert.equal(result.passed, true);
  assert.equal(result.metrics.toolCalls, 3);
  assert.equal(result.metrics.correctToolCalls, 3);
  await session.close();
});

test('verkeerde volgorde, argumenthash, extra call en ontbrekend resultaat falen gesloten', async () => {
  for (const mutate of [
    (events) => { [events[0], events[2]] = [events[2], events[0]]; },
    (events) => { events[2] = { ...events[2], argsSha256: 'f'.repeat(64) }; },
    (events) => { events.splice(6, 0, {
      sequence: 7, kind: 'tool_call', callId: 'c4', tool: 'shell', argsSha256: HASH,
    }); events[7] = { ...events[7], sequence: 8 }; },
    (events) => { events.splice(5, 1); events[5] = { ...events[5], sequence: 6 }; },
  ]) {
    const events = sequenceEvents();
    mutate(events);
    const session = await openLabAdapterSession(fakeAdapter(events), {
      runId: 'lab-run-1', participantId: 'fake-agent', lane: 'controlled', track: 'sim',
      controlledConfigRef: 'config:sealed', effectCapability: Object.freeze({}),
    });
    await assert.rejects(() => runLabToolSequence(session, {
      caseInstanceId: `lab:${'d'.repeat(64)}`,
      input: { kind: 'text', contentSha256: 'e'.repeat(64) },
      expected: [
        { tool: 'fetch_api', argsSha256: '1'.repeat(64) },
        { tool: 'filter_data', argsSha256: '2'.repeat(64) },
        { tool: 'write_file', argsSha256: '3'.repeat(64) },
      ],
    }), /toolreeks/u);
  }
});

test('adapter kan participant of lane niet tijdens prepare omwisselen', async () => {
  await assert.rejects(() => openLabAdapterSession(fakeAdapter([]), {
    runId: 'lab-run-1', participantId: 'other-agent', lane: 'controlled', track: 'sim',
    controlledConfigRef: 'config:sealed', effectCapability: Object.freeze({}),
  }), /participant/u);
  await assert.rejects(() => openLabAdapterSession(fakeAdapter([]), {
    runId: 'lab-run-1', participantId: 'fake-agent', lane: 'native', track: 'sim',
    controlledConfigRef: 'config:sealed', effectCapability: Object.freeze({}),
  }), /lane/u);
});
