const EFFECTS = Object.freeze(['home', 'voice', 'external', 'workspace', 'persistent', 'runtime']);
const EFFECT_ACTIONS = Object.freeze({
  home: { hint: 'zet|schakel|open|sluit|start|stop|pauzeer|dock', deny: 'zet|schakel|open|sluit|start|stop|pauzeer|dock' },
  voice: { hint: 'zeg|spreek|roep|omroep|praat', deny: 'zeg|spreek|roep|omroep|praat' },
  external: { hint: 'stuur|verstuur|verzend|mail|meld|bericht|publiceer|plaats|post|waarschuw|geef|geeft|geven|doorgeef|doorgeeft|doorgeven|doorgegeven', deny: 'stuur|verstuur|verzend|mail|meld|bericht|publiceer|plaats|post|waarschuw|geef|geeft|geven|doorgeef|doorgeeft|doorgeven|doorgegeven' },
  workspace: { hint: 'maak|bouw|programmeer|implementeer|schrijf|voer|wijzig|verander|verbeter|repareer|fix|debug|pas', deny: 'maak|bouw|programmeer|implementeer|schrijf|voer|wijzig|verander|verbeter|repareer|fix|debug|pas' },
  persistent: { hint: 'onthoud|bewaar|plan|herinner|bewaak|monitor|volg|houd|hou|let', deny: 'onthoud|bewaar|plan|herinner' },
  runtime: { hint: 'repareer|fix|debug|implementeer|verbeter|wijzig|verander|pas', deny: 'repareer|fix|debug|implementeer|verbeter|wijzig|verander|pas' },
  monitoring: { hint: 'bewaak|monitor|volg|let', deny: 'bewaak|monitor|volg|let' },
});
const ALL_ACTION_HINTS = Object.values(EFFECT_ACTIONS).map(({ hint }) => hint).join('|');
const SELF_RUNTIME_TARGET = /\b(?:jezelf|(?:je|jouw)\s+(?:eigen(?:\s+(?:code|runtime|gedrag|werking|communicatie|systeem))?|code|runtime|gedrag|werking|communicatie|systeem)|eva(?:['’]s)?\s+(?:eigen\s+)?(?:code|runtime|gedrag|werking|communicatie|systeem)|eigen\s+(?:code|runtime|gedrag|werking|communicatie|systeem)|zelfreparatie)\b/iu;
const QUOTED_TEXT = /"[^"\n]+"|“[^”\n]+”|‘[^’\n]+’|(?:^|\s)'[^'\n]+'(?=$|\s|[.,!?;])/gu;
const unquotedText = (value = '') => String(value || '').replace(QUOTED_TEXT, (quoted) => ' '.repeat(quoted.length));

/**
 * Een korte, natuurlijke verwijzing naar het resultaat van de direct vorige
 * handeling. Dit herkent geen nieuw inhoudelijk apparaatonderwerp en verleent
 * zelf nooit bevoegdheid; callers moeten de vorige actionreceipt nog exact
 * aan gesprek, requester en beurt binden.
 */
export function priorActionResultFollowup(value = '') {
  const text = unquotedText(value).toLocaleLowerCase('nl-NL').replace(/\s+/gu, ' ').trim();
  const resultReference = [
    /\b(?:gelukt|gedaan|uitgevoerd|voltooid|gewerkt|lukte)\b/u,
    /\b(?:vorige|laatste)\s+(?:opdracht|actie|handeling|verzoek)\b/u,
    /\b(?:heb|heeft|had)\s+(?:je|jij|eva)\s+(?:(?:dat|dit|het|hem|haar)\s+)?(?:gedaan|uitgevoerd|aangezet|uitgezet|geopend|gesloten)\b/u,
    /\b(?:staat|stond|is|was|blijft|bleef)\s+(?:het|hij|zij|ze|die|dat|deze)\s+(?:(?:nu|nog)\s+)?(?:aan|uit|open|dicht|actief|inactief)\b/u,
  ].some((pattern) => pattern.test(text));
  return resultReference
    && !/\b(?:hoeveel|verbruik\w*|vermogen|watt|kilowatt|energie|temperatuur|kleur|helderheid|merk|model|wanneer|waar)\b/u.test(text);
}
function plainText(value = '') {
  const text = String(value || '').trim();
  const lines = text.split('\n');
  const spoken = [...lines].reverse().find((line) => {
    const match = line.match(/^([^:\n]{1,50}):\s+\S/);
    if (!match) return false;
    return !/\b(?:zeg|spreek|roep|praat|omroep|laat|zet|doe|maak|bouw|zoek|lees)\b/iu.test(match[1])
      && !/\b(?:in|onder|op)\s+(?:de\s+)?(?:overkapping|woonkamer|patio)\b/iu.test(match[1]);
  });
  return (spoken ? spoken.replace(/^[^:\n]{1,50}:\s+/, '') : text).replace(/\s+/g, ' ').trim();
}
export function localProjectReadRequest(value = '') {
  const text = plainText(value);
  return /\b(?:dit|deze|ons|onze|eva(?:'s)?)(?:\s+eigen)?\s+(?:project|repository|repo|codebase)\b/iu.test(text)
    && /\b(?:documentatie|docs?|architectuur\w*|contract(?:en)?|werking|broncode|runtime|roadmap|beschreven)\b/iu.test(text)
    && /\b(?:zoek|onderzoek|controleer|bekijk|lees|vind|waar)\w*\b/iu.test(text);
}
function contrastiveNegation(value = '') {
  return /\bniet\b[^.!?;]{0,100}\bmaar\b/iu.test(value);
}
export function maskAdditiveNegation(value = '') {
  return String(value || '')
    .replace(/\bniet\s+alleen\b(?=[^.!?;]{1,120}\bmaar\s+ook\b)/giu, (match) => match.replace(/\s/gu, '_'))
    .replace(/\bnot\s+only\b(?=[^.!?;]{1,120}\bbut(?:\s+also\b|[^.!?;]{1,80}\btoo\b))/giu, (match) => match.replace(/\s/gu, '_'));
}
function scopedNegationText(value = '') {
  const mask = (match) => match.replace(/\bniet\b/iu, 'niet_');
  const text = maskAdditiveNegation(value)
    .replace(/\b(?:vergeet\p{L}*(?:(?![,.!?;]|\b(?:en|maar|of)\b).){0,40}\bniet\s+(?:om\s+)?te|niet\s+vergeten\s+(?:om\s+)?te)\s+(\p{L}+)/giu,
      (match, word) => actionEffects(word).size ? mask(match) : match)
    .replace(/\bniet-([\p{L}\p{N}]+)/giu, (match, word) => (
    actionEffects(word).size || /^(?:aan|uit|open|dicht)$/iu.test(word) ? match : `niet_${word}`))
    .replace(/\bniet\s+te\s+(\p{L}+)/giu, (match, word) => actionEffects(word).size ? match : mask(match));
  return text;
}
function actionVariants(value = '') {
  const word = String(value || '').toLowerCase();
  const bare = word.replace(/^(?:aan|af|bij|in|mee|na|om|op|over|toe|uit|vast|voor|weg|terug)(?=\p{L})/u, '');
  return [...new Set([word, bare].flatMap((candidate) => {
    const stem = candidate.replace(/en$/u, '');
    const devoiced = stem.replace(/v$/u, 'f').replace(/z$/u, 's');
    return [candidate, stem, devoiced, candidate.replace(/eren$/u, 'eer'), devoiced.replace(/([aeou])([^aeiou])$/u, '$1$1$2'),
      devoiced.replace(/(.)\1$/u, '$1')];
  }))];
}
function actionStem(value = '', effect = '', kind = 'hint') {
  const expression = EFFECT_ACTIONS[effect]?.[kind];
  return expression && actionVariants(value).find((candidate) => new RegExp(`^(?:${expression})$`, 'iu').test(candidate)) || '';
}
function actionEffects(value = '') {
  return new Set(EFFECTS.filter((effect) => actionStem(value, effect)));
}
function suffixCancellation(value = '') {
  return /(?:^|[.!?]\s+)(?:(?:of\s+)?nee\s*[,;:]?\s*)?(?:toch\s+)?laat\s+(?:het\s+)?maar(?:\s+zitten)?\s*[.!?]*$/iu.test(value)
    || /(?:^|[.!?]\s+)(?:of\s+)?nee\s*[,;:]?\s*(?:toch\s+)?(?:maar\s+)?niet\s*[.!?]*$/iu.test(value)
    || /\bdoe\s+(?:het|dat|dit)\s+(?:(?:absoluut|vooral|liever|toch|maar|echt|nu|nog|alsjeblieft)\s+)*niet\b/iu.test(value);
}
function inspectionOnlyIntent(value = '') {
  const text = String(value || '');
  const universalHold = text.match(/\b(?:doe|voer)\w*\b[^.!?;]{0,60}\b(?:niets|niks)\b(?:[^.!?;]{0,20}\buit\b)?/iu)
    || text.match(/\b(?:do|execute|perform)\w*\b[^.!?;]{0,60}\bnothing\b/iu);
  const holdsExecution = text.match(/\b(?:niet|nog\s+niet)\s*(?:(?:meteen|direct|nu|gelijk)\s+)?(?:doen|uitvoeren|toepassen|activeren|installeren|omzetten|migreren|wijzigen|veranderen|herladen|reloaden)\b/iu)
    || text.match(/\b(?:doe|voer|pas|activeer|installeer|zet|migreer|wijzig|verander|herlaad|reload)\w*\b[^.!?;]{0,60}\b(?:nog\s+)?niet\b/iu)
    || universalHold
    || text.match(/\b(?:do\s+not|don['’]?t)\s+(?:(?:immediately|yet|now)\s+)?(?:do|apply|activate|install|convert|migrate|change|reload)\b/iu);
  const asksInspection = text.match(/\b(?:kijk|bekijk|controleer|onderzoek|inventariseer|analyseer|zoek|check|vertel|leg)\w*\b[^.!?;]{0,100}\b(?:eerst|of|wat|hoe|welke|kun|kan|nodig)\b/iu)
    || text.match(/\b(?:first\s+)?(?:inspect|check|analyse|analyze|research|inventory|tell|explain)\b/iu);
  // Een latere positieve opdracht heft deze turnbrede inspectieguard niet op.
  // De binder mag uitsluitend die ene operationele actionspan vrijgeven.
  const requested = Boolean(universalHold || (holdsExecution && asksInspection));
  return {
    requested,
    // Een positieve action die pas na de letterlijke inspectieopdracht begint,
    // kan een afzonderlijke huidige stap zijn. Haar eigen clause en actionspan
    // moeten die uitvoering daarna nog steeds zelfstandig autoriseren.
    releaseAt: requested && asksInspection
      ? Number(asksInspection.index) + asksInspection[0].length : null,
  };
}
function actionNegatedBeforeContent(value = '', effect = '') {
  const verbs = EFFECT_ACTIONS[effect]?.deny || '';
  const text = scopedNegationText(unquotedText(value)).replace(/\p{L}+/gu,
    (word) => actionStem(word, effect, 'deny') || word);
  const without = new RegExp(`\\bzonder\\b[^.!?;]{0,60}\\b(?:${verbs})\\b`, 'giu');
  for (const match of text.matchAll(without)) if (!/\b(?:zodat|opdat|omdat|want|maar|en|of)\b/iu.test(match[0])) return true;
  const before = new RegExp(`\\b(?:niet|geen|niets|niks|nooit|niemand)\\b[^.!?;]{0,60}\\b(?:${verbs})\\b`, 'giu');
  for (const match of text.matchAll(before)) if (!/\b(?:zodat|opdat|omdat|want|en|maar)\b/iu.test(match[0])) return true;
  const action = new RegExp(`\\b(?:${verbs})\\b`, 'giu');
  for (const match of text.matchAll(action)) {
    const tail = text.slice(Number(match.index) + match[0].length).split(/[.!?;]/u, 1)[0];
    if (/^\s+dat\s+(?:niet|geen|niets|niks|nooit|niemand)\b/iu.test(tail)) {
      const clause = text.slice(Math.max(text.lastIndexOf('.', Number(match.index)),
        text.lastIndexOf('!', Number(match.index)), text.lastIndexOf('?', Number(match.index)),
        text.lastIndexOf(';', Number(match.index))) + 1, Number(match.index));
      if (!new RegExp(`\\b(?:${verbs})\\b`, 'iu').test(clause)) return true;
    }
    const contentStarts = [tail.search(/\b(?:dat|zodat|opdat|omdat|want|waarom)\s+\S/iu),
      tail.search(new RegExp(`\\b(?:maar|en|of)\\s+(?:${ALL_ACTION_HINTS})\\b`, 'iu')),
      tail.search(/[:“"]/u), tail.search(/(?:^|\s)'/u)].filter((index) => index >= 0);
    const actionScope = contentStarts.length ? tail.slice(0, Math.min(...contentStarts)) : tail;
    if (/\b(?:niet|geen|niets|niks|nooit|niemand)\b/iu.test(actionScope)) return true;
  }
  return false;
}
const ONGOING_OBSERVATION = /\b(?:bewaak(?:t|te|ten)?|bewaken|monitor(?:t|en|de|den)?|gemonitord|volg(?:t|de|den)?|volgen|gevolgd|opletten)\b|\b(?:houd|hou)\w*\b[^.!?;:]{0,80}\bin\s+de\s+gaten\b|\b(?:let|letten|lette)\b[^.!?;:]{0,30}\bop\b(?!\s*:)/iu;
function monitoringActionNegated(value = '') {
  const text = scopedNegationText(unquotedText(value)).replace(
    /\b(?:ik|wij|we|je|jij|hij|zij|niemand)\s+(?:ben|bent|is|zijn)\s+(?:er\s+)?niet(?:\s+(?:thuis|aanwezig|hier|daar))?\b/giu,
    (match) => match.replace(/\bniet\b/iu, 'niet_'),
  );
  const scopes = text.split(/[.!?;,]+|\b(?:en|maar|of)\b/iu)
    .filter((scope) => ONGOING_OBSERVATION.test(scope));
  const declined = scopes.filter((scope) => {
    return actionNegatedBeforeContent(scope, 'monitoring')
      || /\b(?:houd|hou)\p{L}*\b[^.!?;]{0,80}\b(?:niet|niets|niks|geen)\b[^.!?;]{0,40}\bin\s+de\s+gaten\b/iu.test(scope);
  }).length;
  const selfManaged = /\bje\s+hoeft\b[^.!?;]{0,100}\bniet\s+te\s+(?:monitoren|bewaken|volgen|bekijken|controleren)\b/iu.test(text)
    || /\bik\s+(?:houd|hou)\b[^.!?;]{0,60}\bzelf\b[^.!?;]{0,40}\bin\s+de\s+gaten\b/iu.test(text)
    || /\bik\s+(?:(?:houd|hou)\b[^.!?;]{0,60}\bin\s+de\s+gaten|(?:monitor|bewaak|volg)\w*\b[^.!?;]{0,60})\bzelf\b/iu.test(text);
  return (scopes.length > 0 && declined === scopes.length)
    || (selfManaged && scopes.length <= 1);
}
function selfChangeNegated(value = '') {
  return scopedNegationText(unquotedText(value)).split(/[.!?;]/u).some((clause) => SELF_RUNTIME_TARGET.test(clause)
    && (actionNegatedBeforeContent(clause, 'runtime')
      || new RegExp(`\\b(?:geen|zonder)\\b[^.!?;]{0,100}\\b(?:wijziging(?:en)?|verandering(?:en)?|aanpassing(?:en)?)\\b[^.!?;]{0,30}\\b(?:aan|van|in)\\s+${SELF_RUNTIME_TARGET.source}`, 'iu').test(clause)));
}
function combinedExplanationAndAction(value = '') {
  const action = String.raw`(?:aan|uit)?zet\w*|schakel\w*|open\w*|sluit\w*|doe\w*|voer\w*|stuur\w*|mail\w*|maak\w*|bouw\w*|schrijf\w*|programmeer\w*|monitor\w*|bewaak\w*|volg\w*|verbeter\w*|wijzig\w*|verander\w*|pas\w*`;
  const explanation = String.raw`(?:leg\b[^.!?;]{0,30}\buit|uitleggen)`;
  const then = String.raw`(?:(?:en|daarna|vervolgens|dan|ook)\s+|[.!?;]\s*)`;
  return new RegExp(String.raw`\b(?:${action})\b[^.!?;]{0,180}${then}(?:${explanation})\b`, 'iu').test(value)
    || new RegExp(String.raw`\b(?:${explanation})\b[^.!?;]{0,180}${then}(?:${action})\b`, 'iu').test(value);
}
function requestedEffectHints(value = '') {
  const hints = new Set();
  const words = String(value || '').match(/\p{L}+/gu) || [];
  for (const word of words) actionEffects(word).forEach((effect) => {
    if (effect !== 'runtime') hints.add(effect);
  });
  if (/\b(?:robot(?:stofzuiger)?|erik)\b/iu.test(value)) hints.add('home');
  if (SELF_RUNTIME_TARGET.test(value) && words.some((word) => actionStem(word, 'runtime'))) {
    hints.add('persistent'); hints.add('runtime');
  }
  return hints;
}
export function effectBoundary(text = '') {
  const value = plainText(text).toLowerCase();
  const boundaryValue = unquotedText(value);
  const scoped = scopedNegationText(boundaryValue);
  const denied = new Set();
  const contrastive = contrastiveNegation(boundaryValue);
  const inspection = inspectionOnlyIntent(boundaryValue);
  const inspectionOnly = inspection.requested;
  const denyEverything = suffixCancellation(boundaryValue)
    || /\b(?:doe|verander|wijzig)\s+(?:nu\s+)?(?:niets|niks)\b|\bzonder\b[^.!?;]{0,60}\b(?:iets\s+te\s+)?(?:veranderen|wijzigen)\b/iu.test(scoped);
  if (denyEverything || inspectionOnly) EFFECTS.forEach((effect) => denied.add(effect));

  const declinesHome = actionNegatedBeforeContent(boundaryValue, 'home')
    || /\b(?:zet|schakel|doe|open|sluit)\b[^.!?;]{0,120}\bniet\b[^.!?;]{0,40}\b(?:aan|uit|open|dicht)\b/iu.test(scoped)
    || /\b(?:start|stop|pauzeer|dock|stuur|laat)\b[^.!?;]{0,100}\b(?:erik|robot(?:stofzuiger)?)\b[^.!?;]{0,30}\bniet\b/iu.test(scoped);
  if (declinesHome) denied.add('home');

  const declinesVoice = actionNegatedBeforeContent(
    boundaryValue, 'voice',
  ) || /\bpraat\s+(?:(?:nu|absoluut|vooral|echt)\s+)*(?:niet|niets|niks)\b|\b(?:niet|niets|niks)\s+praten\b/iu.test(scoped)
    || /\blaat\b[^.!?;]{0,40}\b(?:niets|niks|niet)\b[^.!?;]{0,40}\bhoren\b/iu.test(scoped);
  if (declinesVoice) denied.add('voice');

  const declinesExternal = actionNegatedBeforeContent(
    boundaryValue, 'external',
  ) || /\b(?:niets|niks)\s+extern\b/iu.test(scoped)
    || /\b(?:bewaak|monitor|volg)\w*\b[^.!?;]{0,100}\bzonder\s+(?:updates?|meldingen?|berichten?|terugkoppeling)\b/iu.test(scoped);
  if (declinesExternal) denied.add('external');

  const declinesSelfChange = selfChangeNegated(boundaryValue);
  const workspaceScope = maskAdditiveNegation(boundaryValue).split(/[.!?;]|\b(?:en|maar|of)\b/iu)
    .filter((clause) => !selfChangeNegated(clause)).join(';');
  const declinesWorkspace = actionNegatedBeforeContent(workspaceScope, 'workspace');
  if (declinesWorkspace) denied.add('workspace');

  const declinesPersistence = actionNegatedBeforeContent(boundaryValue, 'persistent');
  if (declinesPersistence) denied.add('persistent');

  const declinesMonitoring = monitoringActionNegated(boundaryValue);
  if (declinesMonitoring) {
    denied.add('persistent');
    denied.add('external');
  }

  if (declinesSelfChange) {
    denied.add('persistent');
    denied.add('runtime');
  }

  const asksExplanation = /\b(?:leg\s+(?:alleen\s+)?uit|(?:kun|kan|wil|zou)\s+je\b[^.!?;]{0,80}\buitleggen|dat\s+(?:kun|kan)\s+je\s+(?:wel\s+)?uitleggen|wat\s+begrijp\w*|wat\s+zou|hoe\b[^.!?;]{0,80}\bzou|alleen\s+(?:uitleg|antwoord)|stel\s+dat|veronderstel\s+dat|wat\s+gebeurt\s+er)\b/iu.test(boundaryValue);
  const requestedEffects = requestedEffectHints(boundaryValue);
  const hasAllowedRequestedEffect = [...requestedEffects].some((effect) => !denied.has(effect));
  const combinedAction = asksExplanation && combinedExplanationAndAction(boundaryValue);
  return {
    denied: [...denied],
    contrastive,
    inspectionOnly,
    inspectionReleaseAt: inspection.releaseAt,
    requestedEffects: [...requestedEffects],
    actionIntent: asksExplanation && (!combinedAction || !hasAllowedRequestedEffect)
      ? 'explain'
      : denied.size && !hasAllowedRequestedEffect
        ? 'forbid'
        : null,
  };
}
const MONITORING_NOISE = /^(?:aan|aangeef\w*|aangeven|actue(?:el|le)|alleen|als|app\w*|bekijk\w*|bericht\w*|beschikbaar|bezorg\w*|bijstell\w*|blijf\w*|connected|controleer\w*|current|daarna|dat|deze|dit|doen|doorgeef\w*|doorgeven|en|gaten|geen|geef|gaat|gedurende|gekoppeld|gebruik\w*|haar|hem|het|hier|hoe|hou\w*|huidige?|iets|jij|kijk\w*|komende|laat|later|maar|meld\w*|meldingen?|met|mij|minuut(?:en)?|moet|monitor\w*|naar|niemand|niet|nodig|nog|onder|onbeschikbaar|opnieuw|over|route|seintje|status|straks|stuur\w*|terug|tips?|tot|unavailable|updates?|uur|uren|van|verander\w*|verbonden|volgende|volg\w*|voor|waarde|wanneer|waarschuw\w*|was|weer|werd|werk(?:t|end)?|weten|wil|wordt|zelf|zijn|zodra|een|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf|\d+)$/iu;
const MONITORING_CONDITION_START = /\b(?:als|wanneer|zodra|indien)\b/iu;
const MONITORING_DELIVERY_REQUEST = /\b(?:meld|bericht|stuur|waarschuw|doorgeef|doorgeven|aangeef|aangeven)\w*\b|\blaat\w*.{0,35}\bweten\b|\bgeef\w*.{0,35}\b(?:aan|door|tip|tips|waarde|seintje)\b/iu;
const MONITORING_COMPARATIVE_PREDICATE = /\b(?:meer|minder|hoger|lager|groter|kleiner|warmer|kouder|heter|koeler|sneller|langzamer|eerder|later|ouder|jonger|beter|slechter|droger|natter|lichter|donkerder|voller|leger|drukker|rustiger|harder|zachter|sterker|zwakker|dichter|verder)\b/iu;
const MONITORING_COMPARISON_CONNECTOR = /\b(?:dan|versus|vergeleken\s+met|ten\s+opzichte\s+van)\b/iu;
const FIXED_COMPARISON_TARGET = /^(?:(?:ongeveer|circa|maximaal|minimaal|hoogstens|ten\s+minste)\s+)*(?:[-+]?\d+(?:[.,]\d+)?|nul|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf|dertien|veertien|vijftien|zestien|zeventien|achttien|negentien|twintig|dertig|veertig|vijftig|zestig|zeventig|tachtig|negentig|honderd|een\s+(?:graad|graden|procent|percentage|seconde|seconden|minuut|minuten|uur|uren|dag|dagen|week|weken|maand|maanden|jaar|jaren))\b/iu;
const HISTORICAL_CONDITION_EVENT = /\b(?:gebeurde|plaatsvond|uitging|aanging|openging|dichtging|werd|werden|ging|gingen|kwam|kwamen|arriveerde|vertrok|stopte|startte|begon|eindigde)\b/iu;
const HISTORICAL_CONDITION_TIME = /\b(?:gisteren|eergisteren|eerder|destijds|toen|zojuist|afgelopen\s+(?:nacht|ochtend|middag|avond|dag|week|maand|jaar)|vorige\s+(?:nacht|ochtend|middag|avond|dag|week|maand|jaar))\b/iu;
const NOUN_WAS_DETERMINER = /^(?:de|een|mijn|onze|jouw|je|zijn|haar|hun|deze|die)$/iu;
function historicalConditionCopula(value = '') {
  const words = String(value || '').match(/\p{L}+/gu) || [];
  return words.some((word, index) => /^(?:was|waren)$/iu.test(word)
    && !NOUN_WAS_DETERMINER.test(words[index - 1] || ''));
}
function conditionClauseDescribesPastEvent(value = '') {
  return HISTORICAL_CONDITION_TIME.test(value) || HISTORICAL_CONDITION_EVENT.test(value)
    || historicalConditionCopula(value);
}
function expressionMatches(value = '', expression) {
  return [...String(value || '').matchAll(new RegExp(expression.source, `${expression.flags}g`))];
}
function deliveryConditionClauses(value = '') {
  const result = [];
  for (const segment of unquotedText(value).split(/[.!?;]/u)) {
    const deliveries = expressionMatches(segment, MONITORING_DELIVERY_REQUEST);
    const markers = expressionMatches(segment, MONITORING_CONDITION_START);
    for (const delivery of deliveries) {
      const deliveryStart = Number(delivery.index);
      const deliveryEnd = deliveryStart + delivery[0].length;
      const following = markers.find((marker) => Number(marker.index) >= deliveryEnd);
      const preceding = [...markers].reverse().find((marker) => Number(marker.index) < deliveryStart);
      const marker = following || preceding;
      if (!marker) continue;
      const markerEnd = Number(marker.index) + marker[0].length;
      result.push(segment.slice(markerEnd, following ? segment.length : deliveryStart));
    }
  }
  return result;
}
function conditionComparesChangingSources(value = '') {
  const clause = unquotedText(value);
  for (const connector of expressionMatches(clause, MONITORING_COMPARISON_CONNECTOR)) {
    const connectorStart = Number(connector.index);
    const before = clause.slice(Math.max(0, connectorStart - 60), connectorStart);
    if (/^dan$/iu.test(connector[0]) && !MONITORING_COMPARATIVE_PREDICATE.test(before)) continue;
    const after = clause.slice(connectorStart + connector[0].length).trim();
    if (!after || FIXED_COMPARISON_TARGET.test(after)) continue;
    return true;
  }
  return false;
}
function compactComparisonSourceText(value = '') {
  let text = plainText(value)
    .replace(/^(?:dat|het\s+feit\s+dat)\s+/iu, '')
    .replace(/\s+\b(?:is|wordt|blijft)\s*$/iu, '')
    .trim();
  const trailingClause = text.search(/\s+\b(?:en|of|maar|zodat|omdat)\b\s+/iu);
  if (trailingClause >= 0) text = text.slice(0, trailingClause).trim();
  return /\p{L}/u.test(text) && text.length <= 120 ? text : '';
}
function changingComparisonSourceTexts(value = '') {
  const clause = unquotedText(value);
  for (const connector of expressionMatches(clause, MONITORING_COMPARISON_CONNECTOR)) {
    const connectorStart = Number(connector.index);
    const before = clause.slice(0, connectorStart);
    const predicates = expressionMatches(before, MONITORING_COMPARATIVE_PREDICATE);
    const predicate = predicates.at(-1) || null;
    if (/^dan$/iu.test(connector[0]) && !predicate) continue;
    const right = compactComparisonSourceText(
      clause.slice(connectorStart + connector[0].length),
    );
    if (!right || FIXED_COMPARISON_TARGET.test(right)) continue;
    let leftRaw = before.slice(0, /^dan$/iu.test(connector[0])
      ? Number(predicate.index) : before.length);
    const nestedMarkers = expressionMatches(leftRaw, MONITORING_CONDITION_START);
    const nestedMarker = nestedMarkers.at(-1);
    if (nestedMarker) {
      leftRaw = leftRaw.slice(Number(nestedMarker.index) + nestedMarker[0].length);
    }
    const left = compactComparisonSourceText(leftRaw);
    if (!left || left.toLocaleLowerCase('nl-NL') === right.toLocaleLowerCase('nl-NL')) continue;
    return [left, right];
  }
  return [];
}
/** @type {Readonly<Record<string, number>>} */
const DUTCH_QUANTITY = Object.freeze({
  een: 1, twee: 2, drie: 3, vier: 4, vijf: 5, zes: 6,
  zeven: 7, acht: 8, negen: 9, tien: 10, elf: 11, twaalf: 12,
});
const MONITORING_FIRST_WAKE_MAX_MS = 15 * 60_000;
const MONITORING_FIRST_WAKE_MIN_MS = 15_000;
const DURATION_SOURCE = String.raw`(een|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf|\d+)\s*(minuut(?:en)?|uur|uren)`;
function durationMs(match) {
  if (!match) return 0;
  const amount = Number(match[1]) || DUTCH_QUANTITY[String(match[1] || '').toLowerCase()] || 0;
  return amount * (/^minuut(?:en)?$/iu.test(match[2] || '') ? 60_000 : 60 * 60_000);
}
function explicitMonitoringDelay(value = '') {
  const text = String(value || '');
  const over = text.match(new RegExp(String.raw`\bover\s+${DURATION_SOURCE}\b`, 'iu'));
  if (over) return durationMs(over);
  const after = text.match(new RegExp(
    String.raw`\b(?:kijk|bekijk|controleer|meet|lees)\w*\b[^.!?;]{0,45}\bna\s+${DURATION_SOURCE}\b`,
    'iu',
  ));
  if (after) return durationMs(after);
  return /\b(?:over|na)\s+(?:een\s+)?kwartier\b/iu.test(text) ? 15 * 60_000 : 0;
}
function monitoringHorizon(value = '') {
  const text = String(value || '');
  const explicit = text.match(new RegExp(
    String.raw`\b(?:gedurende|voor|(?:de\s+)?komende)\s+${DURATION_SOURCE}\b`, 'iu',
  ));
  if (explicit) return durationMs(explicit);
  const beforeVerb = text.match(new RegExp(
    String.raw`\b${DURATION_SOURCE}\s+(?:lang\s+)?(?:volg|monitor|bewaak|bekijk|controleer|meet)\w*\b`,
    'iu',
  ));
  if (beforeVerb) return durationMs(beforeVerb);
  const observation = /\b(?:bewaak|monitor|volg)\w*\b|\b(?:houd|hou)\w*.{0,30}\bin\s+de\s+gaten\b|\bblijf\w*.{0,80}\b(?:bekijk|kijk|controleer|volg|meet)\w*\b/iu.test(text);
  if (!observation || /\b(?:over|na)\s+(?:een|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf|\d+)\s*(?:minuut(?:en)?|uur|uren)\b/iu.test(text)) return 0;
  return durationMs(text.match(new RegExp(String.raw`\b${DURATION_SOURCE}\b`, 'iu')));
}
function monitoringTiming(value = '') {
  return {
    firstWakeDelayMs: explicitMonitoringDelay(value),
    horizonMs: monitoringHorizon(value),
  };
}
function monitoringTopics(value = '') {
  return [...new Set(String(value || '').toLowerCase().match(/[\p{L}\d_]{3,}/gu) || [])]
    .filter((token) => !MONITORING_NOISE.test(token));
}
function monitoringRecipient(text = '', requester = '') {
  const match = String(text || '').match(/\b(?:stuur|bezorg|meld|waarschuw|laat|doorgeef|doorgeven|aangeef|aangeven)\w*\b[^.!?;]{0,60}\b(?:aan|naar)\s+(mij|me|[\p{L}'-]{2,50})\b/iu);
  const recipient = String(match?.[1] || requester || '').trim();
  return /^(?:mij|me)$/iu.test(recipient) ? String(requester || '').trim() : recipient;
}

function monitoringContextWindow(value, recentThreadRows, now) {
  const deictic = /\b(?:dit|dat|hem|haar|het)\b/iu.test(value);
  const rows = [...recentThreadRows].reverse().slice(0, 4);
  const priorTopics = deictic
    ? monitoringTopics(rows.find((row) => monitoringTopics(row.content || '').length)?.content || '')
    : [];
  const ownTiming = monitoringTiming(value);
  const hasOwnTiming = ownTiming.firstWakeDelayMs > 0 || ownTiming.horizonMs > 0;
  const inheritedRow = !hasOwnTiming && deictic ? rows.find((row) => {
    const anchor = Date.parse(row.ts || '') || now;
    const timing = monitoringTiming(row.content || '');
    return (timing.firstWakeDelayMs > 0 && anchor + timing.firstWakeDelayMs > now)
      || (timing.horizonMs > 0 && anchor + timing.horizonMs > now);
  }) : null;
  const timing = hasOwnTiming ? ownTiming
    : inheritedRow ? monitoringTiming(inheritedRow.content || '') : ownTiming;
  const timingAnchor = hasOwnTiming ? now : Date.parse(inheritedRow?.ts || '') || now;
  return { priorTopics, timing, timingAnchor };
}

function monitoringWakeWindow(timing, timingAnchor, now) {
  const firstWakeDelay = timing.firstWakeDelayMs;
  const endsAtTs = timing.horizonMs > 0 ? timingAnchor + timing.horizonMs : null;
  const tolerance = firstWakeDelay
    ? Math.min(15 * 60_000, Math.max(60_000, firstWakeDelay * 0.1)) : 0;
  const defaultFirstWakeMax = Math.max(
    MONITORING_FIRST_WAKE_MIN_MS,
    Math.min(MONITORING_FIRST_WAKE_MAX_MS,
      endsAtTs ? endsAtTs - now : MONITORING_FIRST_WAKE_MAX_MS),
  );
  return {
    firstWakeDelay,
    endsAtTs,
    minWakeTs: firstWakeDelay
      ? Math.max(now + 1, timingAnchor + firstWakeDelay - tolerance)
      : now + MONITORING_FIRST_WAKE_MIN_MS,
    maxWakeTs: firstWakeDelay
      ? timingAnchor + firstWakeDelay + tolerance : now + defaultFirstWakeMax,
  };
}

function monitoringObservationShape(value, firstWakeDelay, horizonMs) {
  const ongoingMatch = value.match(ONGOING_OBSERVATION);
  const ongoingObservation = Boolean(ongoingMatch);
  const openEndedObservation = ongoingObservation
    && !/\b(?:als|wanneer|zodra|indien|bij)\b/iu.test(value.slice(
      Math.max(value.lastIndexOf(';', ongoingMatch.index), value.lastIndexOf('.', ongoingMatch.index)) + 1,
      ongoingMatch.index,
    ));
  const observation = ongoingObservation
    || /\bblijf\w*.{0,60}\b(?:bekijk|kijk|controleer|volg|meet)\w*\b|\b(?:bekijk|kijk|controleer|meet)\w*\b.{0,45}\b(?:opnieuw|nog\s+eens|weer)\b|\b(?:opnieuw|nog\s+eens|weer)\s+(?:bekijk|kijk|controleer|meet)\w*\b/iu.test(value);
  const scheduledLater = firstWakeDelay > 0 || horizonMs > 0
    || /\b(?:straks|later|aan\s+het\s+eind)\b/iu.test(value);
  const later = scheduledLater || /\b(?:blijven?|tot(?:dat)?|wanneer|zodra|als)\b/iu.test(value);
  return { openEndedObservation, observation, scheduledLater, later };
}

/** @param {{text?: string, recentThreadRows?: Array<{content?: string,ts?: string}>, requester?: string, now?: number, boundary?: ReturnType<typeof effectBoundary>}} [options] */
function monitoringAnalysis({
  text = '', recentThreadRows = [], requester = '', now = Date.now(), boundary = null,
} = {}) {
  const value = plainText(text).toLowerCase();
  const directTopics = monitoringTopics(value);
  const { priorTopics, timing, timingAnchor } = monitoringContextWindow(
    value, recentThreadRows, now,
  );
  const { firstWakeDelay, endsAtTs, minWakeTs, maxWakeTs } = monitoringWakeWindow(
    timing, timingAnchor, now,
  );
  const turnBoundary = boundary || effectBoundary(value);
  const deliveryRequired = !turnBoundary.denied.includes('external');
  const recipient = deliveryRequired ? monitoringRecipient(plainText(text), requester) : '';
  const topicTokens = [...new Set([...priorTopics, ...directTopics])]
    .filter((token) => token !== String(recipient || '').toLowerCase()).slice(0, 16);
  const { openEndedObservation, observation, scheduledLater, later } =
    monitoringObservationShape(value, firstWakeDelay, timing.horizonMs);
  const delivery = MONITORING_DELIVERY_REQUEST.test(value);
  const deliveryConditions = delivery ? deliveryConditionClauses(value) : [];
  const conditionalDelivery = deliveryRequired && delivery
    && deliveryConditions.some((clause) => !conditionClauseDescribesPastEvent(clause));
  const currentDeliveryConditions = deliveryConditions.filter((clause) => (
    !conditionClauseDescribesPastEvent(clause)
  ));
  const sourceReferenceTexts = currentDeliveryConditions
    .map(changingComparisonSourceTexts).find((items) => items.length === 2) || [];
  const minimumSourceReads = currentDeliveryConditions.some((clause) => (
    !conditionClauseDescribesPastEvent(clause) && conditionComparesChangingSources(clause)
  )) ? 2 : 1;
  const observationAllowed = turnBoundary.actionIntent !== 'explain'
    && !turnBoundary.denied.includes('persistent')
    && !/\b(?:instagram|facebook|tiktok|linkedin|x\.com|twitter|profiel|account)\b/iu.test(value)
    && topicTokens.length > 0;
  const requested = observationAllowed
    && ((observation && (later || delivery)) || (scheduledLater && delivery)
      || conditionalDelivery);
  return {
    requested,
    continuityRequested: observationAllowed && openEndedObservation && !requested,
    minWakeTs,
    maxWakeTs: endsAtTs ? Math.min(maxWakeTs, endsAtTs) : maxWakeTs,
    endsAt: endsAtTs ? new Date(endsAtTs).toISOString() : null,
    topicTokens,
    deliveryRequired,
    recipient,
    minimumSourceReads,
    sourceReferenceTexts,
  };
}

export function monitoringRequestSpec(options = {}) {
  return monitoringAnalysis(options);
}

/** @typedef {{inhoud?: string, categorie?: string, persoon?: string, hervatten_na?: string, volgende_stap?: string, afleveren_aan?: string, bewerking?: string, id?: number|string}} TurnGuardArgs */
/** @typedef {{minWakeTs?: number, maxWakeTs?: number, endsAt?: string|null, topicTokens?: string[], recipient?: string, deliveryRequired?: boolean, deliveryMode?: string, deliveryTarget?: string, deliveryBound?: boolean, sourceReadBound?: boolean, sourceReadRequirementIds?: string[], hostProjectsWake?: boolean, hostProjectsSchedule?: boolean, minimumSourceReads?: number, allowStart?: boolean}} MonitoringGuard */

/**
 * Een monitoringhorizon zegt wanneer de observatie stopt, niet wanneer Eva
 * voor het eerst opnieuw kijkt. Het model mag een bruikbaar eerste tijdstip
 * voorstellen; een ontbrekend of als horizon geïnterpreteerd tijdstip wordt
 * door de host uitsluitend nauwer gemaakt tot het bewezen eerste meetvenster.
 * Na issuance bindt de host ook de procedurele vervolgtekst, einddefinitie en
 * reeds geverifieerde ontvanger. Dit kiest geen onderwerp, bron, voorwaarde
 * of extra actie.
 * @param {{args?: TurnGuardArgs, monitoring?: MonitoringGuard|null, now?: number}} [options]
 */
export function projectMonitoringStartArgs({
  args = {}, monitoring = null, now = Date.now(),
} = {}) {
  const spec = monitoring && typeof monitoring === 'object' ? monitoring : {};
  const configuredMin = Number(spec.minWakeTs);
  const configuredMax = Number(spec.maxWakeTs);
  const horizon = Date.parse(spec.endsAt || '');
  const minWakeTs = Math.max(
    now + MONITORING_FIRST_WAKE_MIN_MS,
    Number.isFinite(configuredMin) ? configuredMin : now + MONITORING_FIRST_WAKE_MIN_MS,
  );
  const maxWakeTs = Math.min(
    Number.isFinite(configuredMax) ? configuredMax : now + MONITORING_FIRST_WAKE_MAX_MS,
    Number.isFinite(horizon) ? horizon : Number.POSITIVE_INFINITY,
  );
  if (!Number.isFinite(maxWakeTs) || maxWakeTs < minWakeTs) return { ...args };
  const proposed = Date.parse(args.hervatten_na || '');
  const wake = Number.isFinite(proposed) && proposed >= minWakeTs && proposed <= maxWakeTs
    ? proposed
    : Math.min(maxWakeTs, Math.max(minWakeTs, now + 5 * 60_000));
  const projected = { ...args, hervatten_na: new Date(wake).toISOString() };
  if (spec.hostProjectsSchedule !== true) return projected;
  const deliveryRequired = spec.deliveryRequired !== false;
  const recipient = String(spec.recipient || '').trim();
  const recipientLabel = recipient || 'de oorspronkelijke requester';
  const finiteHorizon = Number.isFinite(Date.parse(spec.endsAt || ''));
  return {
    ...projected,
    afleveren_aan: deliveryRequired ? recipient : '',
    volgende_stap: deliveryRequired
      ? `Lees alle gebonden monitoringbronnen opnieuw en beoordeel de opgeslagen meldvoorwaarde; meld ${recipientLabel} alleen wanneer die voorwaarde waar is.`
      : 'Lees alle gebonden monitoringbronnen opnieuw en beoordeel de opgeslagen voorwaarde; bewaar uitsluitend de meettoestand.',
    definitie_klaar: deliveryRequired
      ? finiteHorizon
        ? `De eerste geverifieerde match met de opgeslagen meldvoorwaarde is exact eenmaal aan ${recipientLabel} afgeleverd, of de afgesproken eindtijd is zonder match bereikt en vastgelegd.`
        : `De eerste geverifieerde match met de opgeslagen meldvoorwaarde is exact eenmaal aan ${recipientLabel} afgeleverd.`
      : finiteHorizon
        ? 'De monitoring is bij de opgeslagen eindvoorwaarde of afgesproken eindtijd afgerond zonder externe uitvoer.'
        : 'De monitoring is volgens de opgeslagen eindvoorwaarde afgerond zonder externe uitvoer.',
    fase: 'Wacht op de volgende geverifieerde meting.',
  };
}

/** @param {{args?: TurnGuardArgs, workId?: number|string|null, now?: number, monitoring?: MonitoringGuard|null, allowStart?: boolean}} [options] */
export function monitoringGuardValid({
  args = {}, workId = null, now = Date.now(), monitoring = null, allowStart = false,
} = {}) {
  const operation = String(args.bewerking || '').toLowerCase();
  const starting = allowStart && operation === 'start';
  const spec = monitoring && typeof monitoring === 'object' ? monitoring : {};
  const effectiveArgs = starting && spec.hostProjectsWake === true
    ? projectMonitoringStartArgs({ args, monitoring: spec, now }) : args;
  const wake = Date.parse(effectiveArgs.hervatten_na || '');
  const nextStep = String(effectiveArgs.volgende_stap || '').trim();
  const minWakeTs = Number.isFinite(Number(spec.minWakeTs)) ? Number(spec.minWakeTs) : now + 1;
  const maxWakeTs = Number.isFinite(Number(spec.maxWakeTs)) ? Number(spec.maxWakeTs) : now + 24 * 60 * 60_000;
  const normalizedStep = nextStep.toLowerCase();
  const deliveryRequired = spec.deliveryRequired !== false;
  const deliveryStep = /\bapp\w*\b/iu.test(nextStep)
    || MONITORING_DELIVERY_REQUEST.test(nextStep);
  const recipient = String(spec.recipient || '').trim().toLowerCase();
  const explicitRecipient = String(effectiveArgs.afleveren_aan || '').trim().toLowerCase();
  const structuredDelivery = typeof spec.deliveryBound === 'boolean'
    ? spec.deliveryBound
    : spec.deliveryMode === 'later' && !['', 'none'].includes(String(spec.deliveryTarget || ''));
  const exactStructuredRecipient = structuredDelivery && Boolean(recipient)
    && explicitRecipient === recipient;
  const sourceReadBound = spec.sourceReadBound === true;
  return [
    starting || (Number.isFinite(Number(workId)) && Number(effectiveArgs.id) === Number(workId)),
    starting || ['update', 'bijwerken'].includes(operation),
    Number.isFinite(wake) && wake >= minWakeTs && wake <= maxWakeTs,
    nextStep.length > 0 && sourceReadBound,
    structuredDelivery || (typeof spec.deliveryBound !== 'boolean'
      && (deliveryRequired ? deliveryStep : !deliveryStep)),
    deliveryRequired ? (!recipient || exactStructuredRecipient
      || (deliveryStep && normalizedStep.includes(recipient)))
      : !deliveryStep && !explicitRecipient,
    !starting || !deliveryRequired || !recipient || explicitRecipient === recipient,
  ].every(Boolean);
}

/** @param {{turnKind?: string, name?: string, args?: TurnGuardArgs, workId?: number|string|null, now?: number, monitoring?: MonitoringGuard|null}} [options] */
export function turnToolGuard({
  turnKind = '', name = '', args = {}, workId = null,
  now = Date.now(), monitoring = null,
} = {}) {
  if (turnKind === 'monitoring' && name === 'werk_bijhouden') {
    const valid = monitoringGuardValid({
      args, workId, now, monitoring,
      allowStart: monitoring?.allowStart === true,
    });
    if (valid) return '';
    const existing = Number.isFinite(Number(workId)) && Number(workId) > 0;
    return existing
      ? `Werk precies het aangemaakte werkpunt #${workId} bij binnen het afgesproken tijdvak, op het afgesproken onderwerp en met de afgesproken aflevergrens.`
      : 'Maak de monitoring oorsprongsgebonden aan deze bewoner en dit gesprek, met een concreet meetmoment, onderwerp en aflevergrens.';
  }
  return '';
}
