import assert from 'node:assert/strict';
import test from 'node:test';

import {
  assertSafeWebBinding,
  familyTimelineEntry,
  isLoopbackHost,
  requestAuthorized,
} from '../src/web/access.js';

test('synthetische en gequarantaineerde tijdlijnregels blijven uit gezinsweergaven', () => {
  assert.equal(familyTimelineEntry({ kind: 'chat_in', meta: {} }), true);
  assert.equal(familyTimelineEntry({ kind: 'chat_out', meta: { familyVisible: true } }), true);
  assert.equal(familyTimelineEntry({ kind: 'chat_out', meta: { syntheticTest: true } }), false);
  assert.equal(familyTimelineEntry({
    kind: 'chat_in', meta: { messageContext: { syntheticTest: true } },
  }), false);
  assert.equal(familyTimelineEntry({
    kind: 'chat_out', meta: { familyVisible: true, quarantinedSyntheticContamination: 1 },
  }), false);
});

test('dashboard bindt zonder token alleen aan loopback', () => {
  assert.equal(isLoopbackHost('127.0.0.1'), true);
  assert.equal(isLoopbackHost('::1'), true);
  assert.equal(isLoopbackHost('0.0.0.0'), false);
  assert.equal(assertSafeWebBinding({ host: '127.0.0.1', token: '' }), true);
  assert.throws(
    () => assertSafeWebBinding({ host: '0.0.0.0', token: '' }),
    /EVA_WEB_TOKEN/,
  );
  assert.equal(assertSafeWebBinding({ host: '0.0.0.0', token: 'lang-genoeg-geheim' }), true);
});

test('API-token wordt constant vergeleken en een zelfgekozen sender verleent geen toegang', () => {
  const expected = 'een-lang-dashboard-geheim';
  assert.equal(requestAuthorized({ headers: {} }, expected), false);
  assert.equal(requestAuthorized({
    headers: { authorization: `Bearer ${expected}`, 'x-eva-sender': 'Iemand anders' },
  }, expected), true);
  assert.equal(requestAuthorized({
    headers: { authorization: 'Bearer fout', 'x-eva-sender': 'Jaap' },
  }, expected), false);
});
