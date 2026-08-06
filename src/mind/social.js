import { activeAspirations, allMemories, getTimeline } from '../db.js';

const BOTLIKE_CRITICISM = /\b(?:bot|chatbot|robot(?:achtig)?|geen\s+persoon|niet\s+(?:als\s+)?(?:een\s+)?persoon|geen\s+huisgenoot|zakelijk|helpdesk|gescript|standaard(?:antwoord|zin)|persoonlijkheid)\b/i;
const NATURAL_VOICE_CRITICISM = /\b(?:praat\s+(?:eens\s+)?normaal|niet\s+zo\s+glad|tegeltjeswijsheid|gemaakt(?:e)?\s+(?:humor|warmte)|echte\s+humor|menselijker|natuurlijker)\b/i;
const INITIATIVE_CRITICISM = /\b(?:uit\s+zichzelf|eigen\s+initiatief|doorpakken|doortast|wacht(?:en)?\s+(?:niet\s+)?(?:altijd\s+)?op\s+mij|autonom(?:ie|er)|zelf\s+(?:doen|kiezen|oppakken))\b/i;
const REPETITION_CRITICISM = /\b(?:alweer|steeds\s+hetzelfde|herhaal(?:t|d|den|ing)?|meldingsruis|te\s+veel\s+meldingen)\b/i;

function compact(value, limit = 300) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

const SELF_MEMORY_STOP_WORDS = new Set([
  'aan', 'als', 'bij', 'dat', 'de', 'deze', 'die', 'dit', 'een', 'en', 'het',
  'haar', 'ik', 'in', 'is', 'met', 'mijn', 'naar', 'niet', 'of', 'om', 'op',
  'over', 'te', 'van', 'voor', 'wat', 'ze', 'zij', 'zijn', 'eva',
]);
const SELF_FACET_VOICE = /(?:\bhoud(?:t)?\b.{0,80}\bvan\b|\b(?:waardeer(?:t)?|vind(?:t)?|kies(?:t)?|voorkeur|eigen\s+smaak|aangesproken|liever|mooi(?:er)?)\b)/i;
const SELF_FACET_PROCESS = /\b(?:api|config(?:uratie)?|inbox|tool|route|bewijs|controle|status|voortaan|moet|procedure|beleid|functie|werkboek|test)\b/i;

function selfMemoryWords(value) {
  return compact(value, 2_000)
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLocaleLowerCase('nl-NL')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .split(/\s+/)
    .filter((word) => word.length > 2 && !SELF_MEMORY_STOP_WORDS.has(word));
}

function selfFacetCandidate(row) {
  const category = String(row?.category || '').toLocaleLowerCase('nl-NL');
  const content = compact(row?.content, 1_000);
  return category === 'voorkeur'
    && SELF_FACET_VOICE.test(content)
    && !SELF_FACET_PROCESS.test(content);
}

function selfFacetOverlap(left, right) {
  const a = new Set(selfMemoryWords(left));
  const b = new Set(selfMemoryWords(right));
  if (!a.size || !b.size) return 0;
  return [...a].filter((word) => b.has(word)).length / Math.min(a.size, b.size);
}

/**
 * Select established first-person tastes from Eva's own memory. This is
 * deliberately read-only and conservative: operational rules and one-off
 * model prose must not silently become identity.
 */
export function selfMemoryFacets(query = '', { limit = 3 } = {}) {
  const queryWords = new Set(selfMemoryWords(query));
  const candidates = allMemories(600)
    .filter((row) => String(row.person || '').trim().toLocaleLowerCase('nl-NL') === 'eva')
    .filter(selfFacetCandidate)
    .map((row) => {
      const words = new Set(selfMemoryWords(row.content));
      const relevance = [...queryWords].filter((word) => words.has(word)).length;
      return { ...row, relevance };
    })
    .sort((a, b) => b.relevance - a.relevance
      || Number(b.importance || 0) - Number(a.importance || 0)
      || Date.parse(b.ts || '') - Date.parse(a.ts || ''));
  const selected = [];
  for (const row of candidates) {
    if (selected.some((item) => selfFacetOverlap(item.content, row.content) >= 0.28)) continue;
    selected.push(row);
    if (selected.length >= Math.max(1, Math.min(5, Number(limit) || 3))) break;
  }
  return selected;
}

/**
 * Turn explicit feedback into a reusable behavioural lesson. We deliberately
 * store the lesson, not the user's raw sentence. This lets the relationship
 * change over time without turning every conversation into permanent prompt
 * baggage.
 */
export function relationshipLessonFromText(text = '') {
  const value = compact(text, 2_000);
  if (!value) return null;
  const lessons = [];
  const topics = [];
  if (BOTLIKE_CRITICISM.test(value)) {
    topics.push('aanwezigheid');
    lessons.push('Eva komt als bot over wanneer ze vooral een taak afhandelt of een status meldt; laat haar eigen aandacht, oordeel en relatie met de lopende situatie werkelijk meewegen');
  }
  if (NATURAL_VOICE_CRITICISM.test(value)) {
    topics.push('toon');
    lessons.push('spreek gewoon en situationeel; warmte of humor mag alleen ontstaan uit wat hier werkelijk gebeurt en niet uit een aangeleerde formulering');
  }
  if (INITIATIVE_CRITICISM.test(value)) {
    topics.push('initiatief');
    lessons.push('wacht niet standaard op een opdracht: kies zelf een betekenisvolle volgende stap, maak die af en kom pas terug wanneer er iets te delen of oprecht te vragen is');
  }
  if (REPETITION_CRITICISM.test(value)) {
    topics.push('timing');
    lessons.push('een bekend onderwerp opnieuw melden zonder wezenlijk nieuwe ontwikkeling voelt niet als aandacht maar als automatisering');
  }
  if (!lessons.length) return null;
  return {
    kind: 'correction',
    topic: [...new Set(topics)].join('+').slice(0, 80),
    summary: `${lessons.join('. ')}.`.slice(0, 320),
  };
}

function contributionLane(row) {
  const action = String(row?.meta?.lastAction || '');
  if (['bedien_huis', 'huis_status', 'ha_lees', 'wie_is_thuis', 'bekijk_camera', 'spreek_in_huis'].includes(action)) return 'huis';
  if (action === 'zoek_internet') return 'onderzoek';
  if (['schrijf_bestand', 'lees_bestand', 'voer_uit', 'maak_vaardigheid', 'beheer_addon', 'gebruik_capability'].includes(action)) return 'maken';
  if (action === 'stuur_bericht') return 'contact';
  return 'anders';
}

function recentAutonomousContributions(days = 7) {
  const cutoff = Date.now() - Math.max(1, Number(days) || 7) * 24 * 3600_000;
  return getTimeline({ kinds: ['contribution'], limit: 300 })
    .filter((row) => row.meta?.source === 'autonomy')
    .filter((row) => row.meta?.syntheticTest !== true)
    .filter((row) => !row.meta?.quarantinedSyntheticContamination)
    .filter((row) => Date.parse(row.ts || '') >= cutoff)
    .reverse();
}

/**
 * A small longitudinal self-context. It does not tell Eva what to say or choose;
 * it makes her own unfinished interests and recent choice pattern causally
 * available to the next decision instead of recreating her from a static prompt.
 */
export function selfContinuityContextText({ mode = 'chat', query = '' } = {}) {
  const aspirations = activeAspirations();
  const selfFacets = selfMemoryFacets(query, { limit: mode === 'autonomy' ? 3 : 2 });
  const interests = aspirations.filter((item) => item.kind === 'interest').slice(0, 3);
  const directions = aspirations.filter((item) => item.kind === 'development')
    .sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0))
    .slice(0, 3);
  const recent = recentAutonomousContributions(7);
  const counts = recent.reduce((result, row) => {
    const lane = contributionLane(row);
    result[lane] = Number(result[lane] || 0) + 1;
    return result;
  }, {});
  const lastChoices = recent.slice(-4).map((row) => compact(
    row.meta?.evidence || row.content,
    260,
  ));

  const lines = [
    'Langlopende eigen continuïteit (achtergrond, geen tekst om op te voeren):',
  ];
  if (selfFacets.length) {
    lines.push('Eigen gevestigde smaak uit eerdere echte keuzes:');
    for (const item of selfFacets) lines.push(`- ${compact(item.content, 420)}`);
    lines.push('Gebruik dit alleen als het werkelijk raakt aan dit moment. Een voorkeur is een gezichtspunt, geen gimmick of verplichte verwijzing.');
  }
  if (interests.length) {
    lines.push('Eigen nog levende nieuwsgierigheid:');
    for (const item of interests) {
      lines.push(`- ${compact(item.text, 360)}${item.why ? ` — ${compact(item.why, 260)}` : ''}`);
    }
  }
  if (mode === 'autonomy' && directions.length) {
    lines.push('Duurzame zelfgekozen ontwikkelrichtingen:');
    for (const item of directions) {
      lines.push(`- ${compact(item.text, 340)}${item.next_step ? ` Mogelijke voortzetting: ${compact(item.next_step, 300)}` : ''}`);
    }
  }
  if (mode === 'autonomy') {
    const balance = ['huis', 'onderzoek', 'maken', 'contact', 'anders']
      .map((lane) => `${lane} ${Number(counts[lane] || 0)}`)
      .join(', ');
    lines.push(`Patroon van de laatste zeven dagen: ${balance}. Dit is geschiedenis, geen quotum; kies vrij maar verwar herhaling niet met betrokkenheid.`);
  }
  if (lastChoices.length) {
    lines.push('Laatste zelfgekozen uitkomsten; bouw erop voort of kies bewust iets anders:');
    for (const choice of lastChoices) lines.push(`- ${choice}`);
  }
  lines.push('Gebruik dit alleen wanneer het werkelijk aansluit. Noem deze interne context, categorieën of tellingen nooit in een gesprek.');
  return lines.join('\n');
}

export const __socialTest = Object.freeze({
  contributionLane,
});
