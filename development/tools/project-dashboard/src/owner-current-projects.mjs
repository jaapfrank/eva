// Select only recorded open work; this never infers that a worker is running.
export function currentProjects(cards) {
  const known = new Set(['working', 'attention', 'done', 'stopped']);
  const counts = new Map();
  for (const card of cards) {
    if (typeof card?.intent_id === 'string') counts.set(card.intent_id, (counts.get(card.intent_id) || 0) + 1);
  }
  let unknownCount = 0;
  const open = cards.filter(card => {
    if (!card || typeof card.intent_id !== 'string' || !card.intent_id
      || counts.get(card.intent_id) !== 1 || !known.has(card.status)) {
      unknownCount += 1;
      return false;
    }
    return card.status === 'working' || card.status === 'attention';
  });
  const at = card => Number.isFinite(Date.parse(card.updated_at)) ? Date.parse(card.updated_at) : -Infinity;
  open.sort((a, b) => (at(b) - at(a)) || a.intent_id.localeCompare(b.intent_id));
  return { cards: open.slice(0, 2), openCount: open.length, unknownCount };
}

export const initiativeCardId = intentId => 'initiative-' + intentId;
