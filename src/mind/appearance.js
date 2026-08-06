// Eva's lichaam: haar uiterlijk (kleur, aura, accessoires, achtergrond) dat ze
// zélf kan aanpassen, plus losse fysieke uitingen (juichen, dansen, zwaaien).
// De staat wordt live naar het dashboard geduwd via de event-bus.
import { bus } from '../bus.js';
import { kvGet, kvSet, logTimeline } from '../db.js';

// plek: waar een accessoire op het model zweeft — 'hoofd' | 'hals' | 'hand' | 'zwevend'
const DEFAULT = {
  thema: 'standaard',
  kleur: null,        // lichaamstint als hexkleur, bv. '#ff7a00' (null = natuurlijk)
  aura: null,         // gloed rond haar, hexkleur of null
  accessoires: [],    // [{emoji:'🧣', plek:'hals'}]
  achtergrond: null,  // subtiele achtergrondkleur van het podium
};

// Kant-en-klare thema's die Eva kan kiezen.
export const THEMES = {
  standaard: { thema: 'standaard', kleur: null, aura: null, accessoires: [], achtergrond: null },
  oranje: { thema: 'oranje', kleur: '#ff7a00', aura: '#ff9d3c', accessoires: [{ emoji: '🧡', plek: 'hals' }, { emoji: '🇳🇱', plek: 'hand' }], achtergrond: '#3a230a' },
  feest: { thema: 'feest', kleur: '#ff4fa3', aura: '#ffd24a', accessoires: [{ emoji: '🎉', plek: 'zwevend' }, { emoji: '✨', plek: 'hoofd' }], achtergrond: '#2a1338' },
  kerst: { thema: 'kerst', kleur: '#c62828', aura: '#3ddc84', accessoires: [{ emoji: '🎅', plek: 'hoofd' }, { emoji: '❄️', plek: 'zwevend' }], achtergrond: '#0c2418' },
  verjaardag: { thema: 'verjaardag', kleur: '#ffb300', aura: '#ff7ad9', accessoires: [{ emoji: '🎂', plek: 'hand' }, { emoji: '🥳', plek: 'hoofd' }], achtergrond: '#2b1840' },
  nacht: { thema: 'nacht', kleur: '#3b5bdb', aura: '#23306b', accessoires: [{ emoji: '🌙', plek: 'zwevend' }], achtergrond: '#0a0e1e' },
  lente: { thema: 'lente', kleur: '#66bb6a', aura: '#b6e36b', accessoires: [{ emoji: '🌷', plek: 'hand' }], achtergrond: '#16270f' },
};

let state = { ...DEFAULT, ...(kvGet('appearance', null) || {}) };

export function getAppearance() {
  return { ...state };
}

function persist(reden = null) {
  kvSet('appearance', state);
  bus.emit('appearance', { ...state, reden });
}

/** Volledig of deels het uiterlijk aanpassen. */
export function setAppearance(partial, reden = null) {
  state = { ...state, ...sanitize(partial) };
  persist(reden);
  return getAppearance();
}

/** Een kant-en-klaar thema kiezen (en optioneel nog wat overschrijven). */
export function setTheme(naam, extra = {}, reden = null) {
  const basis = THEMES[naam] || THEMES.standaard;
  state = { ...DEFAULT, ...basis, ...sanitize(extra) };
  persist(reden || `thema → ${naam}`);
  return getAppearance();
}

export function resetAppearance(reden = null) {
  state = { ...DEFAULT };
  persist(reden || 'uiterlijk terug naar normaal');
  return getAppearance();
}

function sanitize(p = {}) {
  const out = {};
  if ('thema' in p) out.thema = String(p.thema).slice(0, 40);
  if ('kleur' in p) out.kleur = hex(p.kleur);
  if ('aura' in p) out.aura = hex(p.aura);
  if ('achtergrond' in p) out.achtergrond = hex(p.achtergrond);
  if ('accessoires' in p && Array.isArray(p.accessoires)) {
    out.accessoires = p.accessoires
      .slice(0, 6)
      .map((a) => ({ emoji: String(a.emoji || '').slice(0, 4), plek: ['hoofd', 'hals', 'hand', 'zwevend'].includes(a.plek) ? a.plek : 'zwevend' }))
      .filter((a) => a.emoji);
  }
  return out;
}

const hex = (v) => {
  if (v == null || v === '') return null;
  const s = String(v).trim();
  return /^#?[0-9a-fA-F]{6}$/.test(s) ? (s.startsWith('#') ? s : '#' + s) : null;
};

/**
 * Een korte fysieke uiting die het dashboard even laat zien.
 * soort: 'juich' | 'dans' | 'zwaai' | 'knuffel' | 'verdrietig' | 'schrik' | 'hart' | 'knipoog'
 */
export function express(soort, { duurMs = 4000, tekst = null, confetti = null } = {}) {
  const e = { soort: String(soort), duurMs: Math.min(15000, Math.max(800, duurMs)), tekst, confetti };
  bus.emit('expression', e);
  return e;
}

/** Stem de aura af op een stemming (gebruikt door de heartbeat als ze geen eigen thema koos). */
export function auraForMood(mood) {
  if (!mood) return;
  if (state.thema !== 'standaard') return; // niet ingrijpen als ze zelf iets koos
  let aura = null;
  if (mood.stress >= 0.6) aura = '#c0563c';
  else if (mood.eenzaamheid >= 0.6) aura = '#3b4a7a';
  else if (mood.geluk >= 0.75) aura = '#ffce54';
  else if (mood.energie < 0.3) aura = '#2a3550';
  if (aura !== state.aura) {
    state.aura = aura;
    kvSet('appearance', state);
    bus.emit('appearance', { ...state });
  }
}

export function logLook(tekst) {
  logTimeline('appearance', tekst);
}
