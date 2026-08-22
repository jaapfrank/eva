import assert from 'node:assert/strict';
import test from 'node:test';

const { readCurrentFact } = await import('../src/integrations/currentfacts.js');

test('current-fact registry kiest de eerste toepasselijke connector', async () => {
  const calls = [];
  const result = await readCurrentFact('iets actueels', {
    adapters: [
      { id: 'first', read: async () => { calls.push('first'); return null; }, text: () => '', data: () => ({}) },
      { id: 'second', read: async () => { calls.push('second'); return { value: 7 }; }, text: () => 'bronfeit', data: (fact) => ({ example_fact: fact }) },
    ],
  });
  assert.deepEqual(calls, ['first', 'second']);
  assert.equal(result.adapterId, 'second');
  assert.equal(result.text, 'bronfeit');
  assert.deepEqual(result.data, { example_fact: { value: 7 } });
});

test('current-fact registry valt door naar de volgende connector bij een bronfout', async () => {
  const result = await readCurrentFact('iets actueels', {
    adapters: [
      { id: 'broken', read: async () => { throw new Error('offline'); }, text: () => '', data: () => ({}) },
      { id: 'usable', read: async () => ({ value: true }), text: () => 'bruikbaar', data: (fact) => ({ example_fact: fact }) },
    ],
  });
  assert.equal(result.adapterId, 'usable');
});
