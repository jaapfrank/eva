import { test } from 'node:test';
import assert from 'node:assert/strict';
import { currentProjects } from './src/owner-current-projects.mjs';

test('only two newest recorded open projects; finished work cannot look active', () => {
  const cards = [
    { intent_id: 'older', status: 'working', updated_at: '2026-10-20T10:00:00Z' },
    { intent_id: 'finished', status: 'done', updated_at: '2026-10-23T10:00:00Z' },
    { intent_id: 'blocked', status: 'attention', updated_at: '2026-10-22T10:00:00Z' },
    { intent_id: 'newest', status: 'working', updated_at: '2026-10-23T09:00:00Z' },
    { intent_id: 'stopped', status: 'stopped', updated_at: '2026-10-23T11:00:00Z' },
  ];
  const before = structuredClone(cards);
  const result = currentProjects(cards);
  assert.deepEqual(result.cards.map(c => c.intent_id), ['newest', 'blocked']);
  assert.equal(result.openCount, 3);
  assert.equal(result.unknownCount, 0);
  assert.deepEqual(cards, before);
});

test('unknown and contradictory records never silently establish activity or inactivity', () => {
  const result = currentProjects([
    { intent_id: 'new-state', status: 'future-state' }, null,
    { intent_id: 'duplicate', status: 'working' },
    { intent_id: 'duplicate', status: 'done' },
    { status: 'working' },
  ]);
  assert.equal(result.openCount, 0);
  assert.equal(result.unknownCount, 5);
  assert.deepEqual(result.cards, []);
});

test('missing dates stay undated; ties are stable; an empty retained page stays scoped', () => {
  const result = currentProjects([
    { intent_id: 'z', status: 'attention' },
    { intent_id: 'b', status: 'working', updated_at: 'invalid' },
    { intent_id: 'a', status: 'working', updated_at: '2026-10-23T09:00:00Z' },
  ]);
  assert.deepEqual(result.cards.map(c => c.intent_id), ['a', 'b']);
  assert.equal(result.cards[1].updated_at, 'invalid');
  assert.deepEqual(currentProjects([]), { cards: [], openCount: 0, unknownCount: 0 });
});
