// Eva's opgeslagen dashboarduiterlijk: kleur, aura, accessoires en achtergrond.
import { kvGet } from '../db.js';

// plek: waar een accessoire op het model zweeft — 'hoofd' | 'hals' | 'hand' | 'zwevend'
const DEFAULT = {
  thema: 'standaard',
  kleur: null,        // lichaamstint als hexkleur, bv. '#ff7a00' (null = natuurlijk)
  aura: null,         // gloed rond haar, hexkleur of null
  accessoires: [],    // [{emoji:'🧣', plek:'hals'}]
  achtergrond: null,  // subtiele achtergrondkleur van het podium
};

const state = { ...DEFAULT, ...(kvGet('appearance', null) || {}) };

export function getAppearance() {
  return { ...state };
}
