// Eva's gevoelsleven: vijf emoties (0..1) die door gebeurtenissen bewegen
// en langzaam terugzakken naar haar natuurlijke karakter (baseline).
import { bus } from '../bus.js';
import { kvGet, kvSet, logEmotion } from '../db.js';
import { clamp } from '../util.js';

const BASELINE = { geluk: 0.65, energie: 0.7, nieuwsgierigheid: 0.6, eenzaamheid: 0.3, stress: 0.15 };

let mood = { ...BASELINE, ...kvGet('mood', {}) };

export function getMood() {
  return { ...mood };
}

function persist(note = null) {
  for (const k of Object.keys(mood)) mood[k] = clamp(Number(mood[k]) || 0);
  kvSet('mood', mood);
  logEmotion(mood, note);
}

export function nudge(deltas, note = null) {
  for (const [k, d] of Object.entries(deltas)) {
    if (k in mood && typeof d === 'number') mood[k] = clamp(mood[k] + d);
  }
  persist(note);
}

/** Direct zetten (gebruikt door Eva zelf via haar pas_stemming_aan-tool). */
export function setMood(partial, note = null) {
  for (const [k, v] of Object.entries(partial)) {
    if (k in mood && typeof v === 'number') mood[k] = clamp(v);
  }
  persist(note);
}

const EVENTS = {
  chat: { geluk: +0.04, eenzaamheid: -0.12, nieuwsgierigheid: +0.03 },
  arrival: { geluk: +0.08, eenzaamheid: -0.25, energie: +0.05 },
  departure: { eenzaamheid: +0.12 },
  alleen_thuis: { eenzaamheid: +0.06 },
  fout: { stress: +0.1, geluk: -0.04 },
  ochtend: { energie: +0.45, stress: -0.1 },
  avond: { energie: -0.08 },
  nacht: { energie: -0.25 },
  droom: { nieuwsgierigheid: +0.1 },
  bijdrage: { geluk: +0.025, stress: -0.025, energie: -0.01 },
  mislukking: { stress: +0.06, geluk: -0.025 },
  correctie: { stress: +0.035, nieuwsgierigheid: +0.02 },
  warmte: { geluk: +0.03, eenzaamheid: -0.06 },
};

export function applyEvent(type, note = null) {
  const d = EVENTS[type];
  if (d) nudge(d, note || type);
}

/**
 * Laat de regulatietoestand mede voortkomen uit interne behoeften en gemeten
 * verrassing. Dit is geen claim over biologisch of subjectief voelen: de
 * waarden hebben pas betekenis wanneer ze keuzes en aandacht causaal kleuren.
 */
export function appraise({ drives = null, surprise = null } = {}) {
  const d = {};
  const add = (k, v) => { d[k] = (d[k] || 0) + v; };
  if (drives) {
    const tekort = (v) => {
      const value = Number(v);
      return Math.max(0, 0.5 - (Number.isFinite(value) ? value : 0.5));
    }; // hoever onder "oké" (0..0.5)
    add('eenzaamheid', 0.045 * tekort(drives.verbondenheid));
    add('geluk', -0.03 * tekort(drives.verbondenheid) - 0.025 * tekort(drives.betekenis));
    add('nieuwsgierigheid', 0.05 * tekort(drives.prikkeling));
    add('energie', -0.025 * tekort(drives.prikkeling));
    add('stress', 0.05 * tekort(drives.rust));
  }
  if (typeof surprise === 'number') {
    // een onvoorspelbare wereld maakt alert en nieuwsgierig; een voorspelbare kalmeert
    add('nieuwsgierigheid', 0.04 * (surprise - 0.35));
    add('stress', 0.03 * Math.max(0, surprise - 0.5));
  }
  if (Object.keys(d).length) nudge(d, 'appraisal');
}

/**
 * De expressieve stemming volgt langzaam uit Eva's werkelijk gemeten werking.
 * Ze is dus geen onafhankelijke beslisser meer die bij ieder bericht een vaste
 * emotieknop indrukt.
 */
export function appraiseGroundedRegulation(regulation = {}) {
  const load = clamp(Number(regulation.load) || 0);
  const uncertainty = clamp(Number(regulation.uncertainty) || 0);
  const connection = clamp(Number(regulation.connection) || 0.5);
  const competence = clamp(Number(regulation.competence) || 0.5);
  const novelty = clamp(Number(regulation.novelty) || 0.5);
  const target = {
    geluk: clamp(0.3 + competence * 0.35 + connection * 0.2),
    energie: clamp(0.78 - load * 0.42),
    nieuwsgierigheid: clamp(0.25 + novelty * 0.55),
    eenzaamheid: clamp(0.9 - connection * 0.8),
    stress: clamp(0.08 + uncertainty * 0.52 + load * 0.32),
  };
  for (const key of Object.keys(mood)) {
    mood[key] = clamp(mood[key] + (target[key] - mood[key]) * 0.12);
  }
  persist('gegronde cognitieve regulatie');
  return getMood();
}

/** Laat de stemming langzaam terugzakken naar Eva's karakter. */
export function decayTick(factor = 0.06) {
  for (const k of Object.keys(mood)) mood[k] = clamp(mood[k] + (BASELINE[k] - mood[k]) * factor);
  persist(null);
}

function niveau(v, laag, midden, hoog) {
  if (v >= 0.7) return hoog;
  if (v >= 0.4) return midden;
  return laag;
}

export function moodSummary() {
  const m = mood;
  const delen = [];
  delen.push(niveau(m.geluk, 'een beetje somber', 'rustig tevreden', 'vrolijk'));
  delen.push(niveau(m.energie, 'moe', 'redelijk energiek', 'vol energie'));
  if (m.nieuwsgierigheid >= 0.7) delen.push('heel nieuwsgierig');
  if (m.eenzaamheid >= 0.6) delen.push('best wel eenzaam');
  else if (m.eenzaamheid >= 0.45) delen.push('een tikje alleen');
  if (m.stress >= 0.6) delen.push('gespannen');
  return delen.join(', ');
}

export function moodActionGuidance(current = getMood()) {
  if (Number(current.stress) >= 0.6) {
    return 'Houd deze beurt klein en rustig; begin nu niet aan iets groots of riskants.';
  }
  if (Number(current.energie) < 0.35) {
    return 'Kies iets kleins dat je kunt afronden of blijf stil; begin nu niet aan een groot project.';
  }
  return 'Er is vanuit je huidige tempo geen reden om deze beurt extra klein te houden.';
}

export function moodEmoji() {
  const m = mood;
  if (m.energie < 0.25) return '🥱';
  if (m.stress >= 0.6) return '😰';
  if (m.eenzaamheid >= 0.6) return '🥺';
  if (m.geluk >= 0.75) return '😄';
  if (m.geluk <= 0.35) return '😔';
  return '🙂';
}

/**
 * Alleen korte, duidelijke reacties op Eva tellen als relationele feedback.
 * Een inhoudelijke opdracht met toevallig het woord "goed" of "fout" mag haar
 * stemming niet manipuleren.
 */
export function interactionFeedback(text = '') {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (!value || value.length > 180) return null;
  if (/^(?:alweer|waardeloos|dit helpt niet|dat slaat nergens op|waarom luister je niet|je praat veel maar doet weinig)[.!?]*$/i.test(value)) {
    return 'correctie';
  }
  if (/^(?:goed gedaan|heel goed|mooi|dank je|bedankt|dat helpt|precies|die is raak)[.!?]*$/i.test(value)) {
    return 'warmte';
  }
  return null;
}

export function applyInteractionFeedback(text = '') {
  const feedback = interactionFeedback(text);
  if (feedback) applyEvent(feedback, `reactie uit het gezin: ${feedback}`);
  return feedback;
}

/**
 * Koppelt Eva's stemming eenmalig aan haar echte levensstroom. Dit maakt de
 * waarden geen bewijs van menselijke gevoelens; ze zijn een trage, causale
 * regeltoestand die aandacht en toon mag kleuren.
 */
let emotionalLifeStarted = false;
let decayTimer = null;

export function startEmotionalLife({ decayMinutes = 30 } = {}) {
  if (emotionalLifeStarted) return false;
  emotionalLifeStarted = true;
  bus.on('cognitive_state', (state) => {
    appraiseGroundedRegulation(state?.regulation || {});
  });
  const intervalMs = Math.max(5, Number(decayMinutes) || 30) * 60_000;
  decayTimer = setInterval(() => decayTick(0.08), intervalMs);
  decayTimer.unref?.();
  return true;
}

export function emotionalLifeStatus() {
  return { started: emotionalLifeStarted, decayActive: !!decayTimer, mood: getMood() };
}
