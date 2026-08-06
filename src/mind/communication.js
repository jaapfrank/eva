import { chatComplete, msg } from '../brain/llm.js';
import { getTimeline, recentConversation } from '../db.js';
import { EVA_CHARACTER_CORE } from './persona.js';
import { householdContextText } from './household.js';
import { selfContinuityContextText } from './social.js';

const ACCOUNTABILITY = /\b(?:per\s+ongeluk|onbedoeld|vergissing|fout(?:je)?|niet\s+zeker|onzeker|mogelijk\s+effect|brand|rook|koolmonoxide|inbraak|lekkage|nood|alarm|privacy|betaling|betaald|kosten|geld|menselijke\s+keuze|jullie\s+moeten|je\s+moet)\b/i;
const OPERATIONAL_UPDATE = /\b(?:staat|stond|gezet|geschakeld|aangezet|uitgezet|hersteld|gecontroleerd|gevonden|afgerond|lamp|licht|deur|raam|robot|stofzuiger|verwarming|thermostaat|apparaat|bestand|rapport|test)\b/i;
const INTERNAL_LANGUAGE = /\b(?:aanwezigheidsbewijs|uitstand|bewijsgrens|delivery|receipt|werkpunt|werkboek|entity[_ -]?id|tool(?:call)?|gecontroleerd\s+(?:uit|aan)|op\s+naam\s+gegokt)\b/i;
const ROUTINE_MESSAGE_QUIET_MS = 90 * 60_000;

function compact(value, limit = 1_200) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

export function autonomousMessageNeedsAccountability(text = '') {
  return ACCOUNTABILITY.test(String(text || ''));
}

function recentRealOutbound(limit = 8) {
  return getTimeline({ kinds: ['chat_out'], limit: 300 })
    .filter((row) => row.meta?.syntheticTest !== true)
    .filter((row) => row.meta?.quarantinedSyntheticContamination !== true)
    .slice(0, Math.max(1, limit))
    .reverse();
}

export function routineAutonomousMessageCoolingDown(text = '', {
  rows = recentRealOutbound(12),
  now = Date.now(),
} = {}) {
  if (!OPERATIONAL_UPDATE.test(String(text || '')) || autonomousMessageNeedsAccountability(text)) return false;
  return rows.some((row) => (
    row.meta?.autonomous === true
    && Number.isFinite(Date.parse(row.ts || ''))
    && now - Date.parse(row.ts) >= 0
    && now - Date.parse(row.ts) < ROUTINE_MESSAGE_QUIET_MS
  ));
}

function verifiedFactText(toolResults = []) {
  return toolResults
    .filter((tool) => tool?.ok === true && tool?.verified === true)
    .filter((tool) => tool?.name !== 'stuur_bericht')
    .slice(-6)
    .map((tool) => {
      if (tool.evidence?.summary) return `- ${compact(tool.evidence.summary, 500)}`;
      try {
        const value = JSON.parse(String(tool.output || ''));
        const fact = value?.evidence?.summary || value?.human_message || value?.data?.message;
        return fact ? `- ${compact(fact, 500)}` : '';
      } catch {
        return '';
      }
    })
    .filter(Boolean)
    .join('\n');
}

function verifiedFactData(toolResults = []) {
  return toolResults
    .filter((tool) => tool?.ok === true && tool?.verified === true && tool?.name !== 'stuur_bericht')
    .slice(-6)
    .map((tool) => {
      try {
        const value = JSON.parse(String(tool.output || ''));
        return { tool: tool.name, changed: tool.changed === true, data: value?.data ?? null };
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function conversationThread(conversationId, limit = 6) {
  return recentConversation(conversationId, limit)
    .slice(-Math.max(1, limit))
    .map((row) => `${row.sender || (row.role === 'assistant' ? 'Eva' : 'huisgenoot')}: ${compact(row.content, 500)}`)
    .join('\n');
}

function parseComposition(raw, fallback, mustDeliver) {
  const source = String(raw || '').trim();
  let value = null;
  try {
    value = JSON.parse(source);
  } catch {
    const object = source.match(/\{[\s\S]*\}/)?.[0];
    try { value = object ? JSON.parse(object) : null; } catch { value = null; }
  }
  const decision = String(value?.decision || '').toLocaleLowerCase('nl-NL');
  const text = compact(value?.text, 800);
  if (!mustDeliver && ['silent', 'later', 'digest'].includes(decision)) {
    return { decision: 'silent', text: '', reason: compact(value?.reason, 240) || decision };
  }
  if (decision !== 'send' || !text) {
    return { decision: 'send', text: compact(fallback, 800), reason: 'composer_fallback' };
  }
  return { decision: 'send', text, reason: compact(value?.reason, 240) || null };
}

const FACT_SUBJECT = /\b(spots?|tv['’]?s?|lamp(?:en)?|licht(?:en)?|deur(?:en)?|raam|ramen|slot(?:en)?|verwarming|thermostaat|robot(?:stofzuiger)?|stofzuiger)\b/giu;
const FACT_STATE = /\b(aangezet|uitgezet|ingeschakeld|uitgeschakeld|aan|uit|stil|open|dicht|gesloten|actief|inactief|beschikbaar|onbeschikbaar|unavailable|offline|online)\b/giu;

function canonicalFactWord(value) {
  const word = String(value || '').toLocaleLowerCase('nl-NL');
  if (['aangezet', 'ingeschakeld', 'aan'].includes(word)) return 'aan';
  if (['uitgezet', 'uitgeschakeld', 'uit', 'inactief'].includes(word)) return 'uit';
  if (['dicht', 'gesloten'].includes(word)) return 'dicht';
  if (['onbeschikbaar', 'unavailable', 'offline'].includes(word)) return 'onbeschikbaar';
  if (['beschikbaar', 'online'].includes(word)) return 'beschikbaar';
  return word;
}

function canonicalFactSubject(value) {
  const word = String(value || '').toLocaleLowerCase('nl-NL').replace(/[’']/g, '');
  if (/^spots?$/.test(word)) return 'spot';
  if (/^tvs?$/.test(word)) return 'tv';
  if (/^lamp/.test(word) || /^licht/.test(word)) return 'lamp';
  if (/^deur/.test(word)) return 'deur';
  if (/^ram/.test(word)) return 'raam';
  if (/^slot/.test(word)) return 'slot';
  if (/stofzuiger|robot/.test(word)) return 'robot';
  return word;
}

function subjectStatePairs(value) {
  const pairs = [];
  for (const clause of String(value || '').split(/[;,.!?\n]+/)) {
    const subjects = [...clause.matchAll(FACT_SUBJECT)];
    const states = [...clause.matchAll(FACT_STATE)];
    for (const subject of subjects) {
      const center = Number(subject.index || 0) + subject[0].length / 2;
      const nearest = states
        .map((state) => ({ state, distance: Math.abs(
          Number(state.index || 0) + state[0].length / 2 - center,
        ) }))
        .filter((item) => item.distance <= 60)
        .sort((a, b) => a.distance - b.distance)[0]?.state;
      if (nearest) pairs.push([
        canonicalFactSubject(subject[0]),
        canonicalFactWord(nearest[0]),
      ]);
    }
  }
  return pairs;
}

function compositionContradictsDraft(draft, text) {
  const before = String(draft || '').toLocaleLowerCase('nl-NL');
  const after = String(text || '').toLocaleLowerCase('nl-NL');
  const finalOff = /\b(?:staat\s+(?:nu\s+)?uit|uitgezet|uitgeschakeld|uitstand)\b/.test(before);
  const finalOn = /\b(?:staat\s+(?:nu\s+)?aan|aangezet|ingeschakeld)\b/.test(before);
  if (finalOff && /\b(?:staat\s+(?:nu\s+)?aan|aangezet|ingeschakeld)\b/.test(after)) return true;
  if (finalOn && /\b(?:staat\s+(?:nu\s+)?uit|uitgezet|uitgeschakeld)\b/.test(after)) return true;
  const sourceNumbers = new Set(before.match(/\b\d+(?:[.,]\d+)?\b/g) || []);
  const inventedNumber = (after.match(/\b\d+(?:[.,]\d+)?\b/g) || [])
    .some((number) => !sourceNumbers.has(number));
  if (inventedNumber) return true;
  const sourceEntities = new Set(before.match(/\b[a-z][a-z0-9_]*\.[a-z0-9_]+\b/g) || []);
  const inventedEntity = (after.match(/\b[a-z][a-z0-9_]*\.[a-z0-9_]+\b/g) || [])
    .some((entityId) => !sourceEntities.has(entityId));
  if (inventedEntity) return true;
  const allowedPairs = new Set(subjectStatePairs(before).map((pair) => pair.join(':')));
  return subjectStatePairs(after).some((pair) => !allowedPairs.has(pair.join(':')));
}

function externalizeInternalLanguage(value) {
  return compact(value, 800)
    .replace(/\bgecontroleerd\s+(uit|aan)gezet\b/gi, '$1gezet')
    .replace(/(?:,?\s*en)?\s+(?:daarna\s+)?de\s+uitstand\s+gecontroleerd\b/gi, '')
    .replace(/\bbuitenlamp-id\b/gi, 'welke buitenlamp welke is')
    .replace(/(?:mijn\s+)?aanwezigheidsbewijs\s+(?:daarvoor\s+)?niet\s+sterk\s+genoeg\b/gi, 'ik niet zeker genoeg wist of er nog iemand was')
    .replace(/\s+([,.;!?])/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/**
 * A final social pass for autonomous contact. Work evidence stays immutable;
 * the model only decides whether discretionary contact deserves attention and
 * how Eva would naturally say those facts to the people she lives with.
 */
export async function composeAutonomousMessage({
  draft,
  recipient = null,
  evidence = '',
  toolResults = [],
  mustDeliver = false,
  conversationId = null,
  complete = chatComplete,
} = {}) {
  const candidate = compact(draft, 4_000);
  if (!candidate) return { decision: 'silent', text: '', reason: 'empty' };
  const accountable = mustDeliver || autonomousMessageNeedsAccountability(candidate);
  if (!accountable && routineAutonomousMessageCoolingDown(candidate)) {
    return { decision: 'silent', text: '', reason: 'routine_contact_cooldown' };
  }
  const addressee = compact(recipient, 120) || 'het gezin';
  const recent = recentRealOutbound(6)
    .map((row) => `- ${row.ts}: ${compact(row.content, 420)}`)
    .join('\n');
  const thread = conversationThread(conversationId, 6);
  const relation = householdContextText({
    subject: addressee === 'het gezin' || addressee === 'gezin' ? null : addressee,
    includeAllMemberDetails: addressee === 'het gezin' || addressee === 'gezin',
    feedbackLimit: 4,
  });
  const self = selfContinuityContextText({ mode: 'chat', query: candidate });
  const facts = [compact(evidence, 2_000), verifiedFactText(toolResults)]
    .filter(Boolean).join('\n');
  const typedFacts = verifiedFactData(toolResults);
  const { text: raw } = await complete({
    instructions: `${EVA_CHARACTER_CORE}

Je maakt nu niet het werk af; je beslist als Eva of een autonoom resultaat werkelijk een bericht aan ${addressee} verdient en formuleert het zo nodig. Dit is een relationele keuze, geen rapportagestap.

${accountable
    ? 'Dit bericht draagt een belofte, fout, risico of ander accountability-gevolg en moet worden verstuurd. Verzwijg het niet.'
    : 'Kies stilte wanneer dit normale routine, een herhaling, een interne controle of informatie zonder beslis- of gevoelswaarde voor het gezin is. Eva hoeft haar bestaan niet met statusberichten te bewijzen.'}

Als je verstuurt: behoud de gegeven feiten exact, verzin niets en maak onzekerheid niet zekerder. Vertel het menselijke gevolg in gewone thuistaal. Bewijs-, tool-, entity-, werkboek- en helpdesktaal blijven intern. Kopieer woorden als “gecontroleerd uit”, “uitstand”, “aanwezigheidsbewijs”, “entity-id” of “buitenlamp-id” dus niet: zeg gewoon wat Eva deed, wat nu zo is en welke gewone onzekerheid haar keuze bepaalde. Humor is nooit verplicht en past niet als Eva een fout meldt. Meestal volstaat één losse zin; hooguit twee.

Antwoord uitsluitend als JSON: {"decision":"send"|"silent","text":"...","reason":"..."}.`,
    input: [msg('user', `Concept (grondstof, niet de gewenste toon):\n${candidate}\n\nGeverifieerde feiten:\n${facts || candidate}\n\nGestructureerde geverifieerde uitkomsten:\n${typedFacts.length ? JSON.stringify(typedFacts).slice(0, 6_000) : '(geen)'}\n\nDirecte gespreksthread waarop dit voortbouwt:\n${thread || '(geen beschikbare thread)'}\n\nRecente echte uitgaande berichten, alleen voor herhalingscontrole:\n${recent || '(geen)'}\n\nRelatiecontext:\n${relation || '(geen)'}\n\n${self}`)],
    tools: [],
    effort: 'low',
    cacheKey: 'eva-autonomous-communication',
    timeoutMs: 45_000,
    budgetClass: 'autonomous',
    rateLimitRetries: 1,
  });
  const result = parseComposition(raw, candidate, accountable);
  if (result.decision === 'send') result.text = externalizeInternalLanguage(result.text);
  if (result.decision === 'send' && (
    compositionContradictsDraft(candidate, result.text)
    || (INTERNAL_LANGUAGE.test(result.text) && !INTERNAL_LANGUAGE.test(candidate))
  )) {
    return {
      decision: 'send',
      text: externalizeInternalLanguage(candidate),
      reason: 'fact_lock_fallback',
    };
  }
  return result;
}

export function factualDeliveryFallback(draft, toolResults = []) {
  const facts = verifiedFactText(toolResults).split('\n')
    .map((line) => line.replace(/^[- ]+/, '').trim())
    .filter(Boolean);
  return externalizeInternalLanguage(facts.at(-1) || draft);
}

export const __communicationTest = Object.freeze({
  compositionContradictsDraft,
  conversationThread,
  externalizeInternalLanguage,
  factualDeliveryFallback,
  parseComposition,
  subjectStatePairs,
});
