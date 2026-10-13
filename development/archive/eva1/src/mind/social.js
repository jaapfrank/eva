import { activeAspirations, allMemories, getTimeline } from '../db.js';
import { toolHasTag } from './tools.js';

const BOTLIKE_CRITICISM = /\b(?:bot|chatbot|robot(?:achtig)?|geen\s+persoon|niet\s+(?:als\s+)?(?:een\s+)?persoon|geen\s+huisgenoot|zakelijk|helpdesk|gescript|standaard(?:antwoord|zin)|persoonlijkheid)\b/i;
const NATURAL_VOICE_CRITICISM = /\b(?:praat\s+(?:eens\s+)?normaal|niet\s+zo\s+glad|tegeltjeswijsheid|gemaakt(?:e)?\s+(?:humor|warmte)|echte\s+humor|menselijker|natuurlijker)\b/i;
const INITIATIVE_CRITICISM = /\b(?:uit\s+zichzelf|eigen\s+initiatief|doorpakken|doortast|wacht(?:en)?\s+(?:niet\s+)?(?:altijd\s+)?op\s+mij|autonom(?:ie|er)|zelf\s+(?:doen|kiezen|oppakken)|jezelf\s+.{0,40}aanpas\w*|dat\s+je\s+het\s+wel\s+kan)\b/i;
const REPETITION_CRITICISM = /\b(?:alweer|steeds\s+hetzelfde|herhaal(?:t|d|den|ing)?|meldingsruis|te\s+veel\s+meldingen)\b/i;
const CHARACTER_TRAIT = /\b(?:eigenwijs(?:heid)?|humor|speels(?:heid)?|plagerig(?:heid)?|gevat(?:heid)?|eigen\s+(?:toon|stem|mening|oordeel)|herkenbare\s+(?:toon|stem|schrijfstijl))\b/i;
const CHARACTER_PREFERENCE = /(?:\b(?:eigenwijs(?:heid)?|humor|speels(?:heid)?|plagerig(?:heid)?|gevat(?:heid)?|eigen\s+(?:toon|stem|mening|oordeel)|herkenbare\s+(?:toon|stem|schrijfstijl))\b.{0,120}\b(?:moet(?:en)?|mag|mogen|hoort|horen)\b.{0,60}\b(?:kenmerk|karaktertrek|eigenschap)\b.{0,50}\b(?:eva|haar|jou|jouw)\b|\b(?:kenmerk|karaktertrek|eigenschap)\b.{0,80}\b(?:van\s+eva|van\s+haar|jouw)\b.{0,60}\b(?:moet(?:en)?|mag|mogen|hoort|horen)\b.{0,50}\b(?:eigenwijs|humor|speels|gevat|eigen\s+(?:toon|stem|oordeel))\w*\b)/i;
const POSITIVE_EVA_EVENT = /(?:\b(?:dat|dit|zoiets)\s+(?:was|is|vond\s+ik)\s+(?:(?:echt|super|heel)\s+){0,2}(?:slim|briljant|geweldig|raak|grappig|gevat|speels)\s+van\s+(?:haar|eva)\b|\b(?:jouw|je\s+eigen|haar|eva'?s?|eva\s+haar)\b.{0,40}\b(?:actie|initiatief|reactie|antwoord|toon|stem|tekst|humor|grap|ingreep)\b.{0,80}\b(?:was|is|waren|zijn|vond\s+ik)\b.{0,50}\b(?:slim|briljant|geweldig|raak|grappig|gevat|speels|typisch\s+eva)\b|\bik\s+vond\s+het\b.{0,45}\b(?:slim|briljant|geweldig|raak|grappig|gevat|speels)\b.{0,25}\bdat\s+(?:je|eva|ze|zij)\b.{0,80}\b(?:zelf|uit\s+(?:jezelf|zichzelf)|reageerde|begon|deed|ingreep)\b)/i;
const NEGATED_OR_UNCERTAIN_PRAISE = /\?|\b(?:niet|geen|nooit|nauwelijks|misschien|wellicht|zou|had\s+kunnen)\b.{0,100}\b(?:slim|briljant|geweldig|raak|grappig|gevat|speels|eigenwijs|typisch\s+eva)\b|\b(?:slim|briljant|geweldig|raak|grappig|gevat|speels|eigenwijs)\b.{0,50}\b(?:niet|geen|nooit)\b/i;
const SITUATIONAL_INITIATIVE_APPRECIATION = /\b(?:actie|reactie|initiatief|ingreep|zelf|uit\s+(?:jezelf|zichzelf)|begon\s+ze|reageerde\s+ze|speelde\s+in\s+op)\b/i;
const PLAYFUL_APPRECIATION = /\b(?:grappig|humor|speels(?:heid)?|plagerig(?:heid)?|gevat(?:heid)?|eigenwijs(?:heid)?|typisch\s+eva)\b/i;
const VOICE_APPRECIATION = /(?:\b(?:toon|stem|schrijfstijl|tekst|antwoord|formuler)\w*\b|\bhoe\s+(?:jij|je|eva|ze|zij)\s+(?:praat|schrijft)\b)/i;
const PERSONAL_TASTE_QUERY = /(?:\b(?:wat|welk\w*|waarom|hoe)\b.{0,100}\b(?:vind\w*|voorkeur|smaak|mooi\w*|favoriet|houd\w*\s+van|interesse\w*)\b|\b(?:jouw|eva'?s?)\s+(?:voorkeur|smaak|favoriet|interesse)|\bover\s+jezelf\b)/i;
const PERSONAL_DESIRE_QUERY = /(?:\b(?:waar|wat)\b.{0,100}\b(?:jij|je|eva)\b.{0,80}\b(?:zin\s+in|wil\w*)\b|\b(?:jij|eva)\b.{0,100}\bzin\s+in\b)/i;
const DIRECT_REPLY_SUBJECT = /(?:\b(?:dit|dat)\s+(?:antwoord|bericht|reactie|formulering|tekst|toon)\b|\b(?:je|jouw)\s+(?:antwoord|bericht|reactie|formulering|tekst|toon)\b|\b(?:dit|dat|zo)\s+(?:helpt|werkt|landt|voelt|klinkt)\b)/iu;
const HISTORICAL_OR_UNCERTAIN_OUTCOME = /\?|\b(?:laatst|gisteren|eergisteren|vorige\s+week|toen|ooit|eerder|destijds|misschien|wellicht|zou|had\s+kunnen)\b/iu;
const DIRECT_STYLE_EVALUATION = /(?:\b(?:dit|dat|je|jouw)\s+(?:antwoord|bericht|reactie|formulering|tekst|toon)\b.{0,55}\b(?:klinkt|voelt|komt\s+over|is|was)\b|\bik\s+vind\s+(?:dit|dat|je|jouw)\s+(?:antwoord|bericht|reactie|formulering|tekst|toon)\b|\b(?:dit|dat|zo)\s+(?:klinkt|voelt)\b)/iu;
const DIRECT_STYLE_NEGATIVE = /\b(?:onnatuurlijk|robotachtig|botachtig|gescript|geforceerd|gemaakt|te\s+(?:warm|kil|koud|afstandelijk|affectief|speels|grappig|flauw|serieus|direct|stellig|bot|voorzichtig|vaag|eigenwijs|dominant|volgzaam|informeel|los|formeel|zakelijk|kort|bondig|compact|lang|uitgebreid|langdradig)|(?:niet|onvoldoende)\s+(?:warm|betrokken|speels|grappig|direct|duidelijk|eigen|eigenwijs|informeel|los|kort|bondig|compact)\s+genoeg|als\s+(?:een\s+)?(?:(?:zakelijke|afstandelijke|gescripte)\s+)?(?:bot|chatbot|robot))\b/iu;
const DIRECT_STYLE_POSITIVE = /\b(?:klinkt|voelt|komt\s+over|is|was)\b.{0,45}\b(?:natuurlijk(?:er)?|menselijk(?:er)?|warm(?:er)?|betrokken(?:er)?|speels(?:er)?|grappig(?:er)?|rustig(?:er)?|directer|duidelijker|voorzichtiger|eigen(?:er)?|eigenwijzer|losser|informeler|formeler|bondiger|compacter|uitgebreider|prettiger|fijner)\b(?=\s*(?:[.!?,;]|$|\b(?:en|maar|dan)\b))/iu;
export const RELATIONSHIP_STYLE_OUTCOME_CONTRACT = 'RelationshipStyleOutcomeClassifier/v1';

function compact(value, limit = 300) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

const SELF_MEMORY_STOP_WORDS = new Set([
  'aan', 'als', 'bij', 'dat', 'de', 'deze', 'die', 'dit', 'een', 'en', 'het',
  'eigenlijk', 'eens', 'ergens', 'favoriet', 'graag', 'haar', 'heb', 'hebt', 'heeft',
  'hoe', 'houden', 'houdt', 'ik', 'in', 'interesse', 'interesses', 'is', 'je',
  'jezelf', 'jij', 'jouw', 'met',
  'mijn', 'mooi', 'mooier', 'moment', 'naar', 'niet', 'of', 'om', 'op', 'over',
  'smaak', 'te', 'van', 'vertel', 'vind', 'vinden', 'vindt', 'voor', 'voorkeur',
  'nog', 'waar', 'wat', 'welke', 'welk', 'wil', 'willen', 'ze', 'zij', 'zin', 'zijn', 'eva',
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
    if (queryWords.size && row.relevance === 0) continue;
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
  if (/\b(?:geweldig|briljant|daar\s+hou\s+ik\s+van|waardeer|landde\s+goed|super\s+slim)\b/i.test(value)
    && !/\b(?:niet|geen|nooit|maar|zogenaamd)\b/i.test(value)) return null;
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

/**
 * Bewaar waardering als ruimte voor oordeel, initiatief en stem — nooit als
 * recept voor het genoemde voorval. Zo kan een geslaagd spontaan moment Eva's
 * karakter versterken zonder dat de host het gedrag gaat naspelen.
 */
export function relationshipAppreciationFromText(text = '') {
  const value = compact(text, 2_000);
  if (!value || !POSITIVE_EVA_EVENT.test(value) || NEGATED_OR_UNCERTAIN_PRAISE.test(value)) return null;
  const topics = [];
  const lessons = [];
  if (SITUATIONAL_INITIATIVE_APPRECIATION.test(value)) {
    topics.push('situationeel initiatief');
    lessons.push('Eigen initiatief landt goed wanneer Eva actuele context zelf verbindt aan een passende, veilige handeling; behoud die beoordelingsruimte zonder de concrete handeling als recept te kopiëren');
  }
  if (PLAYFUL_APPRECIATION.test(value)) {
    topics.push('speelsheid');
    lessons.push('Droge humor, speelsheid en een tikje eigenwijsheid landen goed wanneer ze uit het echte moment voortkomen, niet uit een vaste grap of aangeleerde formulering');
  }
  if (VOICE_APPRECIATION.test(value)) {
    topics.push('eigen stem');
    lessons.push('Een herkenbare, directe huisgenootstem landt goed; laat de formulering variëren en vermijd gladde helpdesktaal');
  }
  if (!lessons.length) return null;
  return {
    kind: 'appreciation',
    topic: [...new Set(topics)].join('+').slice(0, 80),
    summary: `${lessons.join('. ')}.`.slice(0, 320),
  };
}

/** Expliciete karakterwensen zijn een relatievoorkeur, geen lof voor een voorval. */
export function relationshipCharacterPreferenceFromText(text = '') {
  const value = compact(text, 2_000);
  if (!value || value.includes('?') || !CHARACTER_TRAIT.test(value) || !CHARACTER_PREFERENCE.test(value)
    || /\b(?:niet|geen|nooit)\b.{0,120}\b(?:kenmerk|karaktertrek|eigenschap|eigenwijs|humor|speels|plagerig|gevat)\w*\b|\b(?:kenmerk|karaktertrek|eigenschap|eigenwijs|humor|speels|plagerig|gevat)\w*\b.{0,80}\b(?:niet|geen|nooit)\b/i.test(value)) return null;
  const traits = [];
  if (/\beigenwijs(?:heid)?|eigen\s+(?:mening|oordeel)\b/i.test(value)) traits.push('een eigen oordeel');
  if (/\b(?:humor|grappig|gevat(?:heid)?)\b/i.test(value)) traits.push('natuurlijke humor');
  if (/\b(?:speels(?:heid)?|plagerig(?:heid)?)\b/i.test(value)) traits.push('speelsheid');
  if (/\b(?:toon|stem|schrijfstijl)\b/i.test(value)) traits.push('een herkenbare eigen stem');
  if (!traits.length) return null;
  return {
    kind: 'preference',
    topic: 'eigen stem en karakter',
    summary: `Deze bewoner waardeert bij Eva ${traits.join(', ')}; laat dat vrij uit de situatie ontstaan en maak er geen vaste stijltruc of antwoordscript van.`,
  };
}

/** Kies hoogstens één duurzame relationele les; kritiek gaat vóór voorkeur en lof. */
export function relationshipFeedbackFromText(text = '') {
  return relationshipLessonFromText(text)
    || relationshipCharacterPreferenceFromText(text)
    || relationshipAppreciationFromText(text);
}

/**
 * Een causale canary-uitkomst vereist meer dan algemene waardering: de tekst
 * moet het zojuist gegeven antwoord zelf aanwijzen. Historische anekdotes,
 * karakterwensen en vragen blijven wel relationele context, maar nooit bewijs
 * dat één tijdelijke lens hielp of schaadde.
 */
export function relationshipOutcomeFeedbackKind(text = '') {
  const value = compact(text, 2_000);
  if (!value || HISTORICAL_OR_UNCERTAIN_OUTCOME.test(value)
    || !DIRECT_REPLY_SUBJECT.test(value) || !DIRECT_STYLE_EVALUATION.test(value)) return null;
  if (/\b(?:niet\s+meer\s+(?:als\s+)?(?:een\s+)?(?:bot|chatbot|robot(?:achtig)?)|niet\s+meer\s+zo\s+(?:zakelijk|gescript)|geen\s+(?:bot|chatbot|robot)\s+meer)\b/iu.test(value)) {
    return 'direct_style_appreciation';
  }
  if (DIRECT_STYLE_NEGATIVE.test(value)) {
    return 'direct_style_correction';
  }
  if (DIRECT_STYLE_POSITIVE.test(value)
    && !/\b(?:niet|geen|nooit|toch|nog\s+steeds|\bte\s+)\b/iu.test(value)) {
    return 'direct_style_appreciation';
  }
  return null;
}

function contributionLane(row) {
  const action = String(row?.meta?.lastAction || '');
  if (toolHasTag(action, 'contribution:house')) return 'huis';
  if (toolHasTag(action, 'contribution:research')) return 'onderzoek';
  if (toolHasTag(action, 'contribution:making')) return 'maken';
  if (toolHasTag(action, 'contribution:contact')) return 'contact';
  return 'anders';
}

function recentAutonomousContributions(days = 7) {
  const cutoff = Date.now() - Math.max(1, Number(days) || 7) * 24 * 3600_000;
  return getTimeline({ kinds: ['contribution'], limit: 300 })
    .filter((row) => ['autonomy', 'bijdrage'].includes(row.meta?.source))
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
  const tasteChat = mode === 'chat' && PERSONAL_TASTE_QUERY.test(query);
  const desireChat = mode === 'chat' && PERSONAL_DESIRE_QUERY.test(query);
  const selfQuestion = tasteChat || desireChat;
  const relevantTasteFacets = tasteChat ? selfMemoryFacets(query, { limit: 2 }) : [];

  const aspirations = activeAspirations();
  const queryWords = new Set(selfMemoryWords(query));
  const selfFacets = mode === 'autonomy'
    ? selfMemoryFacets(query, { limit: 1 })
    : relevantTasteFacets;
  const interests = aspirations
    .filter((item) => item.kind === 'interest')
    .map((item) => ({
      ...item,
      relevance: selfMemoryWords(`${item.text} ${item.why || ''}`)
        .filter((word) => queryWords.has(word)).length,
    }))
    .filter((item) => mode === 'autonomy'
      || (selfQuestion && (!queryWords.size || item.relevance > 0)))
    .sort((a, b) => b.relevance - a.relevance
      || Date.parse(a.last_touched || '') - Date.parse(b.last_touched || ''))
    .slice(0, mode === 'autonomy' ? 2 : 1);
  const directions = aspirations.filter((item) => item.kind === 'development')
    .sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0))
    .slice(0, 2);
  const recent = recentAutonomousContributions(7);
  const counts = recent.reduce((result, row) => {
    const lane = contributionLane(row);
    result[lane] = Number(result[lane] || 0) + 1;
    return result;
  }, {});
  const lastChoices = recent.slice(-2).map((row) => compact(
    row.meta?.evidence || row.content,
    120,
  ));

  const lines = ['Eigen continuïteit (alleen achtergrond):'];
  if (selfFacets.length) {
    lines.push('Gevestigde smaak:');
    for (const item of selfFacets) lines.push(`- ${compact(item.content, mode === 'autonomy' ? 160 : 220)}`);
  }
  if (interests.length) {
    lines.push('Levende nieuwsgierigheid:');
    for (const item of interests) {
      lines.push(`- ${compact(item.text, 150)}${item.why ? `; ${compact(item.why, 70)}` : ''}`);
    }
  }
  if (mode === 'autonomy' && directions.length) {
    lines.push('Zelfgekozen ontwikkelrichtingen:');
    for (const item of directions) {
      lines.push(`- ${compact(item.text, 150)}${item.next_step ? ` Volgende mogelijkheid: ${compact(item.next_step, 80)}` : ''}`);
    }
  }
  if (mode === 'autonomy') {
    const balance = ['huis', 'onderzoek', 'maken', 'contact', 'anders']
      .map((lane) => `${lane} ${Number(counts[lane] || 0)}`)
      .join(', ');
    lines.push(`Patroon van de laatste zeven dagen: ${balance}. Dit is geschiedenis, geen quotum; kies vrij maar verwar herhaling niet met betrokkenheid.`);
  }
  if (mode === 'autonomy' && lastChoices.length) {
    lines.push('Laatste zelfgekozen uitkomsten:');
    for (const choice of lastChoices) {
      lines.push(`- ${choice}`);
    }
  }
  if (lines.length === 1) return '';
  lines.push('Alleen gebruiken wanneer dit werkelijk aansluit; nooit deze interne context noemen.');
  return lines.join('\n').slice(0, mode === 'autonomy' ? 1_800 : 900);
}
