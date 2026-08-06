const EFFECTS = Object.freeze(['home', 'voice', 'external', 'workspace', 'persistent']);

function plainText(value = '') {
  const text = String(value || '').trim();
  const lines = text.split('\n');
  const spoken = [...lines].reverse().find((line) => {
    const match = line.match(/^([^:\n]{1,50}):\s+\S/);
    if (!match) return false;
    // Een dubbele punt na een opdracht is inhoud ("Zeg via ...: hallo"),
    // geen transcriptlabel dat van de gebruikerszin mag worden afgeknipt.
    return !/\b(?:zeg|spreek|roep|praat|omroep|laat|zet|doe|maak|bouw|zoek|lees)\b/iu.test(match[1]);
  });
  return (spoken ? spoken.replace(/^[^:\n]{1,50}:\s+/, '') : text)
    .replace(/\s+/g, ' ')
    .trim();
}

function withoutDiscourseLead(value = '') {
  return plainText(value)
    .replace(/^\s*(?:(?:ja|ok(?:é)?|okay|inderdaad|precies|prima|goed|nou|dan|maar)\s*[,;:]?\s*)+/iu, '')
    .trim();
}

function contrastiveNegation(value = '') {
  return /\bniet\b.{0,100}\bmaar\b/iu.test(value);
}

export function effectBoundary(text = '') {
  const value = plainText(text).toLowerCase();
  const denied = new Set();
  const contrastive = contrastiveNegation(value);
  const denyEverything = /\b(?:doe|verander|wijzig)\s+(?:nu\s+)?(?:niets|niks)\b|\bzonder\b.{0,60}\b(?:iets\s+te\s+)?(?:veranderen|wijzigen)\b/iu.test(value);
  if (denyEverything) EFFECTS.forEach((effect) => denied.add(effect));

  if (!contrastive && (
    /\b(?:zet|schakel|doe|open|sluit)\b.{0,120}\bniet\b.{0,40}\b(?:aan|uit|open|dicht)\b/iu.test(value)
    || /\b(?:zet|schakel|doe)\b.{0,120}\b(?:niet\s+aan|niet\s+uit)\b/iu.test(value)
    || /\b(?:start|stop|pauzeer|dock|stuur|laat)\b.{0,100}\b(?:erik|robot(?:stofzuiger)?)\b.{0,30}\bniet\b/iu.test(value)
  )) denied.add('home');

  if (!contrastive && (
    /\b(?:zeg|spreek|roep|omroep|praat|laat\b.{0,40}\bhoren)\b.{0,80}\b(?:niets|niks|niet)\b/iu.test(value)
    || /\b(?:niets|niks|niet)\b.{0,50}\b(?:zeggen|spreken|omroepen|uitspreken)\b/iu.test(value)
  )) denied.add('voice');

  if (/\b(?:verstuur|verzend|mail|publiceer|plaats)\b.{0,80}\b(?:niets|niks|niet)\b|\b(?:niets|niks)\s+extern\b/iu.test(value)) {
    denied.add('external');
  }
  if (/\b(?:maak|bouw|programmeer|schrijf|voer)\b.{0,80}\b(?:niets|niks|niet|geen)\b|\b(?:maak|schrijf)\s+(?:in\s+deze\s+(?:proef|test)\s+)?(?:niets|niks)\b/iu.test(value)) {
    denied.add('workspace');
  }
  if (/\b(?:onthoud|bewaar|plan|maak\s+een\s+taak)\b.{0,60}\b(?:niets|niks|niet)\b/iu.test(value)) {
    denied.add('persistent');
  }

  const asksExplanation = /\b(?:leg\s+(?:alleen\s+)?uit|wat\s+begrijp\w*|welke\b|wat\s+zou|hoe\s+zou|alleen\s+(?:uitleg|antwoord))\b/iu.test(value);
  return {
    denied: [...denied],
    contrastive,
    actionIntent: denied.size ? (asksExplanation ? 'explain' : 'forbid') : null,
  };
}

function voiceReference(value = '') {
  return /\b(?:voice\s*pe|assist[_ -]?satellite|omroep(?:en|actie)?|via\s+(?:de\s+)?(?:voice|speaker))\b/iu.test(value);
}

function voiceTarget(value = '') {
  if (/\b(?:overkapping|buiten\s*voice|patio)\b/iu.test(value)) return 'overkapping';
  if (/\b(?:woonkamer|binnen\s*voice|living)\b/iu.test(value)) return 'woonkamer';
  return null;
}

function speechLead(value = '') {
  return /^(?:zeg|spreek|roep|praat|omroep|laat\b.{0,40}\bhoren)\b/iu.test(withoutDiscourseLead(value));
}

function retryLead(value = '') {
  return /^(?:probeer\b.{0,80}\b(?:opnieuw|nogmaals|nog\s+een\s+keer)|probeer\s+het\s+nogmaals|doe\s+(?:dat|het)\s+(?:nogmaals|opnieuw|nog\s+een\s+keer)|ja\s+doe\s+dat|doe\s+dat|nogmaals)\b/iu.test(plainText(value));
}

function requestedSpeech(value = '') {
  const raw = withoutDiscourseLead(value);
  const quoted = [...raw.matchAll(/[“"']([^“”"']{2,500})[”"']/gu)]
    .map((match) => match[1].trim())
    .sort((a, b) => b.length - a.length)[0];
  if (quoted) return quoted;
  let content = raw.replace(/^(?:zeg|spreek|roep|praat|omroep)\s+(?:nu\s+)?/iu, '');
  content = content
    .replace(/^(?:via\s+(?:de\s+)?voice\s*pe\s+)?(?:in|onder|op)\s+(?:de\s+)?(?:overkapping|woonkamer)\s*[:,-]?\s*/iu, '')
    .replace(/^(?:via\s+(?:de\s+)?voice\s*pe)\s*[:,-]?\s*/iu, '')
    .split(/(?:[.!?]\s+|,\s+)(?:dan\s+)?(?:zal\s+ik|ga\s+ik|ik\s+(?:tel|time)|zodat\s+ik)\b/iu, 1)[0]
    .trim();
  if (!content || /^(?:wat|iets)(?:\s+gezelligs)?\s+(?:tegen|naar)\s+me\b/iu.test(content)) return null;
  if (/^(?:het|dat)\s+(?:nogmaals|opnieuw|nog\s+een\s+keer)\b/iu.test(content)) return null;
  return content.slice(0, 500);
}

function recentVoiceAction(rows = []) {
  const ordered = [...rows].reverse();
  for (const row of ordered) {
    if (row?.role !== 'user') continue;
    const content = plainText(row.content);
    if (!speechLead(content)) continue;
    const text = requestedSpeech(content);
    const target = voiceTarget(content) || voiceTarget(rows.map((item) => item.content || '').join('\n'));
    if (text || target) return { text, target, source: 'thread' };
  }
  return null;
}

export function resolveVoiceDirective({ text = '', recentMessages = [], previousAssistant = '' } = {}) {
  const value = plainText(text);
  const boundary = effectBoundary(value);
  const threadText = recentMessages.map((row) => row?.content || '').join('\n');
  const prior = recentVoiceAction(recentMessages);
  const cancelledRetry = /^\s*probeer\b.{0,80}\bniet\b.{0,40}\b(?:opnieuw|nogmaals|nog\s+een\s+keer)\b/iu.test(value);
  const correction = /^nee\s*[,;:]?\s+via\s+(?:de\s+)?voice\s*pe\b/iu.test(value);
  const explicit = speechLead(value) && Boolean(voiceReference(value) || voiceTarget(value) || voiceReference(threadText));
  const retry = retryLead(value) && Boolean(prior || voiceReference(threadText));
  const voiceDenied = boundary.denied.includes('voice') || cancelledRetry;
  const requested = !voiceDenied && (explicit || correction || retry);
  const target = voiceTarget(value) || prior?.target || voiceTarget(threadText);
  const speech = correction
    ? plainText(previousAssistant)
    : explicit
      ? requestedSpeech(value)
      : prior?.text || null;
  return {
    requested,
    denied: voiceDenied,
    explicit,
    inherited: requested && !explicit,
    target,
    text: speech,
    source: explicit ? 'current' : requested ? 'thread' : null,
  };
}

export function locationRequest(text = '') {
  const value = withoutDiscourseLead(text);
  return /^(?:waar\s+ben\s+ik|waar\s+(?:is|bevindt)\s+(?:jaap|lola)|ben\s+ik\s+(?:binnen|buiten|thuis))\b/iu.test(value)
    || /\b(?:actuele|huidige)\s+(?:plek|locatie|accesspoint|wifi-accesspoint)\b/iu.test(value);
}

export function identityNameCandidate(text = '', sender = '') {
  const value = plainText(text).replace(/[.!?]+$/gu, '').trim();
  let match = value.match(/^(?:nee\s*[,;:]?\s*)?(?:dit|dat)\s+(?:is|ben)\s+niet\s+.+?\s+maar\s+(.+)$/iu);
  if (!match) match = value.match(/^(?:nee\s*[,;:]?\s*)?(?:dit|dat)\s+(?:is|ben)\s+(.+)$/iu);
  if (!match) match = value.match(/^nee\s*[,;:]\s*(?:dat\s+is\s+)?(.+)$/iu);
  if (!match) match = value.match(/^nee\s+(\p{L}[\p{L}'-]*(?:\s+\p{L}[\p{L}'-]*){0,2})$/iu);
  if (!match) return null;
  let name = String(match[1] || '').replace(/^["']|["']$/gu, '').trim();
  if (/^(?:ik|mij|mezelf)$/iu.test(name)) name = String(sender || '').trim();
  if (!name || name.length > 80 || name.split(/\s+/u).length > 5) return null;
  if (/\b(?:via|voice|speaker|zeg|spreek|doe|probeer|hoor|klopt|precies|dank|goed|fout)\b/iu.test(name)) return null;
  return name;
}

export function arbitrateTurnPlan({
  mode = 'chat',
  text = '',
  baseContract,
  baseRequiredTools = [],
  baseDirectAction = null,
  voiceDirective = null,
  effectForTool = () => 'read',
} = {}) {
  const boundary = effectBoundary(text);
  const contract = { ...(baseContract || {}) };
  let requiredTools = [...new Set(baseRequiredTools)];
  let directAction = baseDirectAction;
  if (boundary.contrastive) directAction = null;
  let actionIntent = requiredTools.length || directAction ? 'execute' : 'respond';

  if (voiceDirective?.requested) {
    contract.kind = 'voice_action';
    contract.outcome = 'de bedoelde tekst via exact het gekozen Voice PE-doel uitspreken';
    contract.proof = 'één geslaagde en geverifieerde spreekactie';
    contract.effort = 'low';
    requiredTools = ['spreek_in_huis'];
    actionIntent = 'execute';
  } else if (locationRequest(text)) {
    contract.kind = 'location_check';
    contract.outcome = 'de actuele wifi/AP-zone lezen zonder een preciezere plek te verzinnen';
    contract.proof = 'een verse Home Assistant/AP-uitlezing uit deze beurt';
    contract.effort = 'low';
    requiredTools = ['ha_lees'];
    actionIntent = 'inspect';
  }

  if (boundary.denied.length) {
    requiredTools = requiredTools.filter((name) => !boundary.denied.includes(effectForTool(name, {})));
    const explicitWorkspaceRead = /\b(?:lees|bekijk|controleer|inspecteer)\b.{0,100}\b(?:bestand|broncode|configuratie|projectmap|repository)\b/iu.test(text);
    if (boundary.denied.includes('workspace') && !explicitWorkspaceRead) {
      // Een hypothetische programmeervraag met "maak niets" is uitleg, geen
      // halve bouwcyclus waarin alleen de toevallig read-only stap overblijft.
      requiredTools = requiredTools.filter((name) => !['lees_bestand', 'lijst_bestanden'].includes(name));
    }
    if (directAction && boundary.denied.includes(effectForTool('bedien_huis', directAction))) directAction = null;
    actionIntent = boundary.actionIntent;
  }

  const allowedEffects = new Set(['read']);
  if (mode === 'autonomy') EFFECTS.forEach((effect) => allowedEffects.add(effect));
  if (actionIntent === 'execute') {
    requiredTools.forEach((name) => allowedEffects.add(effectForTool(name, {})));
  }
  boundary.denied.forEach((effect) => allowedEffects.delete(effect));
  const effectPolicy = {
    mode: 'allow',
    allowedEffects: [...allowedEffects].filter((effect) => effect !== 'read'),
    deniedEffects: boundary.denied,
    actionIntent,
  };
  const allowedToolNames = contract.allowedTools ? new Set(contract.allowedTools) : null;
  if (voiceDirective?.requested) {
    contract.allowedTools = new Set(['spreek_in_huis', 'ha_lees', 'zoek_geheugen']);
  }
  return {
    contract,
    actionIntent,
    requiredTools,
    allowedToolNames: contract.allowedTools || allowedToolNames,
    directAction,
    effectPolicy,
    voiceDirective,
    inheritedFrom: voiceDirective?.inherited ? 'conversation' : null,
  };
}

export const __turnPlanTest = {
  plainText,
  requestedSpeech,
  retryLead,
  speechLead,
  voiceTarget,
  withoutDiscourseLead,
};
