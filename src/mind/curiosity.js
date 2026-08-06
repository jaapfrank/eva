import { db, activeAspirations, touchAspiration } from '../db.js';

const ACTIVE = new Set(['chosen', 'running', 'waiting', 'evaluating']);
const TERMINAL = new Set(['completed', 'abandoned', 'inconclusive']);
const nowIso = () => new Date().toISOString();
const clean = (value, limit = 2_000) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
const isProductionExploration = (item) => item?.source === 'self';
const isRelationshipExploration = (item) => ['self', 'conversation'].includes(String(item?.source || ''));
const TOPIC_FEATURE_VERSION = 9;
const ASTRONOMY_EVENTS = [
  ['perseids', /\bperse[iï]d\w*\b/i],
  ['geminids', /\bgeminid\w*\b/i],
  ['quadrantids', /\bquadrantid\w*\b/i],
  ['lyrids', /\blyrid\w*\b/i],
  ['leonids', /\bleonid\w*\b/i],
  ['orionids', /\borionid\w*\b/i],
  ['eta-aquariids', /\beta[ -]?aquariid\w*\b/i],
  ['delta-aquariids', /\bdelta[ -]?aquariid\w*\b/i],
  ['lunar-eclipse', /\b(?:maansverduister\w*|lunar[ -]?eclipse)\b/i],
  ['solar-eclipse', /\b(?:zonsverduister\w*|solar[ -]?eclipse)\b/i],
];

db.exec(`
CREATE TABLE IF NOT EXISTS eva_explorations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'self',
  source_episode_id INTEGER,
  aspiration_id INTEGER,
  topic_key TEXT,
  topic_anchors_json TEXT NOT NULL DEFAULT '[]',
  topic_features_json TEXT NOT NULL DEFAULT '{}',
  question TEXT NOT NULL,
  why_now TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  expected_observation TEXT NOT NULL,
  stop_rule TEXT NOT NULL,
  max_actions INTEGER NOT NULL DEFAULT 6,
  action_count INTEGER NOT NULL DEFAULT 0,
  empty_evidence_streak INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'chosen',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  result TEXT,
  decision TEXT,
  next_wake_ts TEXT,
  last_selected_ts TEXT,
  revision INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_eva_explorations_attention
  ON eva_explorations(status, next_wake_ts, updated_ts);
`);

try {
  const columns = new Set(db.prepare('PRAGMA table_info(eva_explorations)').all().map((column) => column.name));
  if (!columns.has('topic_key')) db.exec('ALTER TABLE eva_explorations ADD COLUMN topic_key TEXT');
  if (!columns.has('topic_anchors_json')) {
    db.exec("ALTER TABLE eva_explorations ADD COLUMN topic_anchors_json TEXT NOT NULL DEFAULT '[]'");
  }
  if (!columns.has('topic_features_json')) {
    db.exec("ALTER TABLE eva_explorations ADD COLUMN topic_features_json TEXT NOT NULL DEFAULT '{}'");
  }
} catch { /* oudere databases blijven startbaar; de normale migratie wordt bij volgende start herhaald */ }

function parseJson(value, fallback) {
  try { return JSON.parse(value); } catch { return fallback; }
}

function out(row) {
  if (!row) return null;
  const storedFeatures = parseJson(row.topic_features_json, null);
  return {
    ...row,
    evidence: parseJson(row.evidence_json, []),
    topic_anchors: parseJson(row.topic_anchors_json, []),
    topic_features: storedFeatures?.version === TOPIC_FEATURE_VERSION ? storedFeatures : deriveTopicFeatures(row),
    max_actions: Number(row.max_actions || 0),
    action_count: Number(row.action_count || 0),
    empty_evidence_streak: Number(row.empty_evidence_streak || 0),
    revision: Number(row.revision || 0),
  };
}

export function explorationById(id) {
  return out(db.prepare('SELECT * FROM eva_explorations WHERE id=?').get(Number(id)));
}

export function listExplorations({ activeOnly = false, limit = 20, sources = null } = {}) {
  const wantedSources = Array.isArray(sources)
    ? sources.map((value) => clean(value, 80)).filter(Boolean)
    : [];
  const where = [
    activeOnly ? "status IN ('chosen','running','waiting','evaluating')" : null,
    wantedSources.length ? `source IN (${wantedSources.map(() => '?').join(',')})` : null,
  ].filter(Boolean);
  const rows = db.prepare(`SELECT * FROM eva_explorations${where.length ? ` WHERE ${where.join(' AND ')}` : ''}
    ORDER BY updated_ts DESC, id DESC LIMIT ?`).all(
    ...wantedSources, Math.max(1, Math.min(100, Number(limit) || 20)),
  );
  return rows.map(out);
}

export function startExploration({
  question,
  whyNow,
  hypothesis,
  expectedObservation,
  stopRule,
  maxActions = 6,
  sourceEpisodeId = null,
  aspirationId = null,
  source = 'self',
  supersedesExplorationId = null,
  newEvidence = null,
  newEvidenceVerified = false,
  topicKey = null,
  topicAnchors = null,
} = {}) {
  const required = [question, whyNow, hypothesis, expectedObservation, stopRule].map((value) => clean(value));
  if (required.some((value) => value.length < 8)) {
    return { ok: false, reason: 'vraag, aanleiding, hypothese, verwachte waarneming en stopregel moeten concreet zijn' };
  }
  const identity = explorationIdentity({
    topicKey, topicAnchors, question: required[0], hypothesis: required[2],
    expectedObservation: required[3], stopRule: required[4],
  });
  if (!identity.ok) return { ok: false, changed: false, reason: identity.reason };
  const candidate = {
    question: required[0], hypothesis: required[2], expected_observation: required[3],
    stop_rule: required[4], topic_key: identity.topicKey, topic_features: identity.topicFeatures,
  };
  if (supersedesExplorationId != null && String(supersedesExplorationId).trim() !== '') {
    const requestedPrior = explorationById(Number(supersedesExplorationId));
    const related = requestedPrior && requestedPrior.source === source && TERMINAL.has(requestedPrior.status)
      && (explorationSafetyConflict(requestedPrior, candidate) || sameExplorationTopic(requestedPrior, candidate));
    if (!related) {
      return {
        ok: false,
        changed: false,
        needs_followup: true,
        reason: `besluit #${clean(supersedesExplorationId, 40)} hoort inhoudelijk niet bij deze verkenning; kies geen ander besluit-id om de onderwerpcontrole te passeren`,
        prior_exploration: requestedPrior || null,
      };
    }
  }
  const recentTerminal = listExplorations({ limit: 60 }).find((item) => {
    if (item.source !== source || !TERMINAL.has(item.status)
      || Date.now() - Date.parse(item.updated_ts || '') > 24 * 3600_000) return false;
    return explorationSafetyConflict(item, candidate) || sameExplorationTopic(item, candidate);
  });
  if (recentTerminal) {
    const explicitlyReopened = Number(supersedesExplorationId) === Number(recentTerminal.id)
      && clean(newEvidence).length >= 8 && newEvidenceVerified === true;
    if (!explicitlyReopened) return {
      ok: false,
      changed: false,
      needs_followup: true,
      reason: `dit onderwerp kreeg recent al besluit #${recentTerminal.id}; heropen het alleen met dat id en concreet nieuw, geverifieerd toolbewijs uit deze beurt dat de eerdere drempel verandert`,
      prior_exploration: recentTerminal,
    };
    required[1] = clean(`${required[1]} Heropening van besluit #${recentTerminal.id} door nieuw bewijs: ${newEvidence}`);
  }
  const duplicate = listExplorations({ activeOnly: true, limit: 40 }).find((item) => (
    item.source === source && sameExplorationTopic(item, candidate)
  ));
  if (duplicate) return { ok: true, changed: false, exploration: duplicate, reason: 'dezelfde verkenning loopt al' };
  const ts = nowIso();
  const cap = Math.max(2, Math.min(12, Math.floor(Number(maxActions) || 6)));
  const info = db.prepare(`INSERT INTO eva_explorations
    (created_ts,updated_ts,source,source_episode_id,aspiration_id,topic_key,topic_anchors_json,topic_features_json,question,why_now,hypothesis,
     expected_observation,stop_rule,max_actions,status,last_selected_ts)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'chosen',?)`).run(
    ts, ts, clean(source, 80) || 'self', Number(sourceEpisodeId) || null,
    Number(aspirationId) || null, identity.topicKey, JSON.stringify(identity.topicAnchors),
    JSON.stringify(identity.topicFeatures), ...required, cap, ts,
  );
  if (Number(aspirationId)) touchAspiration(Number(aspirationId), `Eigen verkenning #${info.lastInsertRowid} gestart: ${required[0]}`);
  return { ok: true, changed: true, exploration: explorationById(info.lastInsertRowid) };
}

export function updateExploration(id, {
  status = null,
  evidence = null,
  result = null,
  decision = null,
  nextWakeTs = null,
  expectedRevision = null,
} = {}) {
  const current = explorationById(id);
  if (!current) return { ok: false, reason: `verkenning #${id} bestaat niet` };
  if (expectedRevision != null && Number(expectedRevision) !== current.revision) {
    return { ok: false, reason: `verkenning #${id} is intussen gewijzigd`, exploration: current };
  }
  const nextStatus = clean(status, 30) || current.status;
  if (![...ACTIVE, ...TERMINAL].includes(nextStatus)) return { ok: false, reason: `ongeldige status ${nextStatus}` };
  if (current.status === 'waiting' && current.next_wake_ts
    && Date.parse(current.next_wake_ts) > Date.now()
    && ['chosen', 'running', 'evaluating'].includes(nextStatus)) {
    return {
      ok: false,
      changed: false,
      reason: `verkenning #${current.id} slaapt tot ${current.next_wake_ts}; vóór die tijd kan zij alleen blijven wachten of bewust worden afgesloten`,
      exploration: current,
    };
  }
  if (TERMINAL.has(nextStatus) && clean(result).length < 8) {
    return { ok: false, reason: 'afronden vereist een concrete uitkomst of bewezen stopreden' };
  }
  const evidenceRows = [...current.evidence];
  if (clean(evidence)) evidenceRows.push({ ts: nowIso(), text: clean(evidence, 1_500) });
  const wake = nextStatus === 'waiting' && nextWakeTs && Number.isFinite(Date.parse(nextWakeTs))
    ? new Date(nextWakeTs).toISOString()
    : null;
  db.prepare(`UPDATE eva_explorations SET updated_ts=?,status=?,evidence_json=?,result=?,decision=?,
    next_wake_ts=?,last_selected_ts=?,revision=revision+1 WHERE id=?`).run(
    nowIso(), nextStatus, JSON.stringify(evidenceRows.slice(-30)), clean(result) || current.result,
    clean(decision) || current.decision, wake, nowIso(), Number(id),
  );
  const updated = explorationById(id);
  if (updated.aspiration_id) touchAspiration(updated.aspiration_id, `${updated.status}: ${updated.result || updated.question}`);
  return { ok: true, changed: true, exploration: updated };
}

export function noteExplorationAction(id, { tool, ok, verified, evidence = null, episodeId = null } = {}) {
  const current = explorationById(id);
  if (!current || !ACTIVE.has(current.status) || current.status === 'evaluating'
    || tool === 'verkenning_bijhouden') return current;
  if (current.next_wake_ts && Date.parse(current.next_wake_ts) > Date.now()) return current;
  const useful = verified === true && clean(evidence).length >= 4;
  const row = {
    ts: nowIso(), tool: clean(tool, 80), ok: ok === true, verified: verified === true,
    evidence: clean(evidence, 900) || null, episode_id: Number(episodeId) || null,
  };
  const rows = [...current.evidence, row].slice(-30);
  const count = current.action_count + 1;
  const emptyStreak = useful ? 0 : current.empty_evidence_streak + 1;
  const exhausted = count >= current.max_actions || emptyStreak >= 2;
  db.prepare(`UPDATE eva_explorations SET updated_ts=?,status=?,action_count=?,empty_evidence_streak=?,
    evidence_json=?,decision=?,revision=revision+1 WHERE id=?`).run(
    nowIso(), exhausted ? 'evaluating' : 'running', count, emptyStreak, JSON.stringify(rows),
    exhausted
      ? (emptyStreak >= 2 ? 'Twee stappen zonder nieuwe bevestigde waarneming: nu stoppen of van aanpak veranderen.' : 'Actiebudget bereikt: nu beoordelen.')
      : current.decision,
    Number(id),
  );
  return explorationById(id);
}

function aspirationDue(item, now = Date.now()) {
  const cadenceMs = Number(item.cadence_hours || 0) * 3600_000;
  if (!cadenceMs) return !item.last_touched;
  const touched = Date.parse(item.last_touched || item.created_ts || '') || 0;
  return touched + cadenceMs <= now;
}

export function attentionContextText(now = new Date()) {
  const at = now.getTime();
  const activeExplorations = listExplorations({ activeOnly: true, limit: 12, sources: ['self'] });
  const explorations = activeExplorations
    .filter((item) => !item.next_wake_ts || Date.parse(item.next_wake_ts) <= at);
  const sleepingExplorations = activeExplorations
    .filter((item) => item.next_wake_ts && Date.parse(item.next_wake_ts) > at).slice(0, 4);
  const aspirations = activeAspirations().filter((item) => aspirationDue(item, at)).slice(0, 8);
  const lines = [];
  if (explorations.length) {
    lines.push('Open eigen verkenningen:');
    for (const item of explorations) {
      lines.push(`- #${item.id} [${item.status}] onderwerp=${item.topic_key || 'legacy'} ${item.question} | hypothese: ${item.hypothesis} | ${item.action_count}/${item.max_actions} stappen | stop: ${item.stop_rule}${item.decision ? ` | kernbesluit: ${item.decision}` : ''}`);
    }
  }
  if (sleepingExplorations.length) {
    lines.push('Slapende eigen verkenningen; hun wekdatum is al de herinnering, plan hiervoor geen tweede zelfherinnering:');
    for (const item of sleepingExplorations) {
      lines.push(`- #${item.id} onderwerp=${item.topic_key || 'legacy'} wordt wakker op ${item.next_wake_ts}: ${item.question}`);
    }
  }
  const recentDecisions = listExplorations({ limit: 30, sources: ['self'] })
    .filter((item) => TERMINAL.has(item.status) && item.decision
      && at - Date.parse(item.updated_ts || '') < 24 * 3600_000)
    .slice(0, 8);
  if (recentDecisions.length) {
    lines.push('Recente eigen besluiten die nog gelden:');
    for (const item of recentDecisions) {
      lines.push(`- #${item.id} onderwerp=${item.topic_key || 'legacy'}: ${item.decision} | eerdere stopregel: ${item.stop_rule}`);
    }
    lines.push('Begin over hetzelfde onderwerp geen nieuwe cyclus met een soepelere drempel. Heropen het eerdere besluit alleen met het id en concreet nieuw bewijs dat de wijziging verklaart.');
  }
  if (aspirations.length) {
    lines.push('Richtingen die volgens je eigen cadans weer aandacht mogen krijgen:');
    for (const item of aspirations) {
      lines.push(`- aspiratie #${item.id}: ${item.text}${item.next_step ? ` | eerdere volgende stap: ${item.next_step}` : ''}`);
    }
  }
  lines.push('Kies zelf wat je werkelijk wilt onderzoeken. Voor een nieuwe verkenning leg je vóór de tweede inhoudelijke toolactie met verkenning_bijhouden je eigen vraag, hypothese, verwachte waarneming, actiebudget en stop/pivotregel vast. Geef ook een stabiele onderwerp_sleutel en minstens twee concrete onderwerp_ankers. De kern leidt daarnaast zelf een canonieke identiteit af uit de inhoud: vrije labels kunnen een eerder besluit dus niet met synoniemen omzeilen. Noem bij een onbekend domein een concrete entity:, event: of artifact: ook letterlijk in je vraag of hypothese. De kern schrijft het onderwerp of antwoord niet voor. Rond een bereikt budget af of verander aantoonbaar van aanpak.');
  return lines.join('\n');
}

function topicWords(value) {
  const stop = new Set([
    'de', 'het', 'een', 'en', 'van', 'voor', 'met', 'naar', 'dat', 'dit', 'die',
    'wat', 'hoe', 'verder', 'gaan', 'doen', 'zijn', 'kan', 'kunnen', 'niet', 'geen',
    'zonder', 'iemand', 'omdat', 'alleen', 'veilig', 'actuele', 'recent', 'recente',
    'bestaande', 'nieuwe', 'kleine', 'concrete', 'aantoonbare', 'bruikbaar', 'maken',
    'gebruiken', 'wanneer', 'waarschijnlijk', 'regel', 'eigen', 'eerdere', 'besluit',
    'volgende', 'precies', 'mogelijk', 'functie', 'functies', 'bewijs', 'verbetering',
    'proef', 'testen', 'test', 'marker',
    'exacte', 'entiteit', 'entiteiten', 'status', 'statusgeschiedenis', 'toestand',
    'toestandbewijs', 'logboek', 'logboekregel', 'logboekregels', 'home', 'assistant',
    'waarneming', 'waarnemingen', 'bevestigt', 'beschikbaar', 'onderzoek', 'onderzoeken',
    'actor', 'automatisering', 'automatiseringen', 'tijdstip', 'moment', 'momentopname',
    'gecontroleerd', 'controleren', 'toont', 'tonen', 'gebruikt', 'gebruiken',
    'actueel', 'onbekend', 'onbekende', 'passend', 'passende',
    'vannacht', 'nachtelijk', 'nachtelijke', 'ochtend', 'vanmorgen', 'morgen',
  ]);
  const genericCompoundHeads = ['regel', 'status', 'bewijs', 'context', 'onderzoek', 'moment', 'entiteit', 'logboek'];
  const words = clean(value, 3_000).toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/[^a-z0-9]+/g, ' ').trim().split(/\s+/)
    .filter((word) => word.length >= 4 && !/^\d+$/.test(word) && !stop.has(word))
    .filter((word) => !genericCompoundHeads.some((head) => (
      word.length > head.length + 2 && word.endsWith(head)
    )));
  return words.flatMap((word) => {
    // Een paar concrete domeinankers vangen natuurlijke synoniemen zonder een
    // algemene fuzzy score. Zo zijn tuinlamp en buitenverlichting hetzelfde
    // veiligheidsdomein, maar camera, koeling en weer niet automatisch.
    const anchors = [];
    if (/(?:lamp|licht|verlicht)/.test(word)) anchors.push('topic:lighting');
    if (/(?:tuin|buiten|overkapping|schuur)/.test(word)) anchors.push('topic:outdoor');
    if (/(?:aanwezig|bewoner|persoon|mensen|routerappar|bewegings|binnen|thuis|wifi|beeld)/.test(word)) {
      anchors.push('topic:presence');
    }
    return [word, ...anchors];
  });
}

function normalizeTopicPart(value, limit = 120) {
  return clean(value, limit).toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/[^a-z0-9:._-]+/g, '_').replace(/^_+|_+$/g, '');
}

const FEATURE_PATTERNS = {
  subjects: {
    lighting: /(?:lamp(?:en)?|\blicht(?:en)?\b|verlicht\w*|\blight\b|lighting|illumination)/i,
    camera: /(?:camera|cameras|camerabeeld|frame|beeldherkenning|vision)/i,
    access_sensor: /(?:\b(?:voor|achter|schuur|tuin|keuken)?deur\w*|\braam(?:sensor|contact|entiteit)?\w*|\bcontactentiteit\w*|\bdevice_class\b.{0,35}\b(?:door|window|opening)\b|\b(?:door|window|opening)\b.{0,35}\bdevice_class\b)/i,
    energy: /(?:\benergie\w*|\bverbruik\w*|\bvermogen\w*|\bsluipverbruik\w*|\benergy\b|\bpower\b)/i,
    astronomy: /\b(?:perse[iï]d\w*|geminid\w*|quadrantid\w*|lyrid\w*|leonid\w*|orionid\w*|aquariid\w*|meteoor\w*|sterren\w*|astronom\w*|maanstand|meteor\w*|maansverduister\w*|zonsverduister\w*|lunar[ -]?eclipse|solar[ -]?eclipse)\b/i,
    cooling: /\b(?:koel\w*|ventil\w*|airco|hitte|binnentemperatuur)\b/i,
    weather: /\b(?:weer|bewolking|neerslag|regen|wind|forecast|weather)\b/i,
    vacuum: /\b(?:robotstofzuiger|stofzuig\w*|vacuum|roborock)\b/i,
    software: /\b(?:software|code|programmeer\w*|addon|plugin|api|repository)\b/i,
    business: /\b(?:bedrijf|bedrijven|omzet|verdien\w*|klant|business|revenue)\b/i,
    communication: /\b(?:gesprek|communicatie|spraak|stem|voice|wakeword)\b/i,
    household: /\b(?:huishouden|huisgenoot|woning|home assistant)\b/i,
    testing: /(?:\btest\w*|\bproef\w*|bewijsroute)/i,
  },
  dimensions: {
    presence: /(?:\b(?:aanwezigheid\w*|afwezigheid\w*|bewoner\w*|occupancy|resident\w*|persoon|personen|thuis|leeg|buitenactiviteit\w*)\b|\b(?:iemand|niemand)\s+(?:buiten|binnen|thuis|aanwezig)\b)/i,
    schedule: /\b(?:schema|planning|tijdvenster|tijdstip|timer|schedule|cadans)\b/i,
    cause: /\b(?:oorzaak|waardoor|actor|trigger|inschakel\w*|root cause)\b/i,
    brightness: /\b(?:helderheid|dim\w*|brightness|lichtsterkte)\b/i,
    colour: /\b(?:kleur|colour|color|rgb)\b/i,
    consumption: /\b(?:verbruik|vermogen|bespar\w*|consumption|watt)\b/i,
    connectivity: /\b(?:bereikbaar|verbonden|connect\w*|unavailable|offline|online)\b/i,
    weather_window: /\b(?:bewolking|helder weer|kijkvenster|zichtbaar|forecast|waarneem\w*)\b/i,
    improvement: /\b(?:verbeter\w*|uitbreid\w*|upgrade|zelfverbeter\w*)\b/i,
  },
  components: {
    wakeword: /\bwake[ -]?word\w*\b/i,
    dashboard: /\bdashboard\w*\b/i,
    exploration_gate: /\b(?:verkenningsgate|exploration[ _-]?gate)\b/i,
    energy_monitor: /\b(?:sluipverbruik|energy[ _-]?monitor)\b/i,
    json_compare: /\b(?:json[ _-]?(?:vergelijk|compare)|deep[ _-]?equal)\b/i,
    tool_package: /\b(?:toolpakket|package[ _-]?manager)\b/i,
  },
  effects: {
    switch_off: /\b(?:uitzetten|uitschakelen|doven|switch off|turn off|(?:zet|schakel)\b.{0,30}\buit|(?:kan|kunnen|mag|mogen|moet|zou)\b.{0,60}\b(?:uit|uitgezet|uitgeschak\w*|gedoofd))\b/i,
    switch_on: /\b(?:aanzetten|inschakelen|switch on|turn on|(?:zet|schakel)\b.{0,30}\baan|(?:kan|kunnen|mag|mogen|moet|zou)\b.{0,60}\b(?:aan|aangezet|ingeschak\w*))\b/i,
    automate: /\b(?:automatiseren|automatisering maken|automation|timer instellen)\b/i,
  },
  contexts: {
    outdoor: /(?:\btuin(?:lamp|licht|verlicht)?|\bbuiten(?:lamp|licht|verlicht)|\boverkapping\b|\bschuur\b|\bgarden\b|\boutdoor\b|\bexterior\b)/i,
    bedroom: /(?:\bslaapkamer|\bbedroom)/i,
    living_room: /(?:\bwoonkamer|\bliving room)/i,
    kitchen: /(?:\bkeuken|\bkitchen)/i,
    back_door: /(?:\bachterdeur\w*|\bback[ _-]?door\w*)/i,
    front_door: /(?:\bvoordeur\w*|\bfront[ _-]?door\w*)/i,
    window: /(?:\braam\w*|\bwindow\w*)/i,
    door: /(?:\b(?:voor|achter)?deur\w*|\bdoorbell\w*)/i,
  },
};

function semanticText(item = {}) {
  return clean([
    item.question, item.hypothesis, item.expected_observation || item.expectedObservation,
    item.stop_rule || item.stopRule,
  ].filter(Boolean).join(' '), 8_000).toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/\b(?:geen|niet|zonder)\s+(?:een\s+|de\s+|het\s+)?(?:camera|lamp|licht(?:en)?|verlichting|energie|weer|stofzuiger)\w*/g, ' ');
}

function focusSemanticText(item = {}) {
  return clean([item.question, item.hypothesis].filter(Boolean).join(' '), 4_000)
    .toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/\b(?:geen|niet|zonder)\s+(?:een\s+|de\s+|het\s+)?(?:camera|lamp|licht(?:en)?|verlichting|energie|weer|stofzuiger)\w*/g, ' ');
}

function primaryFocusSubject(item = {}, subjects = []) {
  const concrete = subjects.filter((subject) => !['household', 'testing'].includes(subject));
  if (!concrete.length) return null;
  const question = focusSemanticText({ question: item.question });
  const interrogative = /\b(?:welke|welk|wat|waardoor|hoe|waarom|kan|moet|is)\b/i.exec(question);
  const directQuestionClause = interrogative
    ? question.slice(interrogative.index).split(/\b(?:terwijl|zonder|zodat|mits|tenzij)\b|[,;?]/i)[0]
    : '';
  const fields = [
    [directQuestionClause, 8],
    [question, 4],
    [focusSemanticText({ hypothesis: item.hypothesis }), 2],
    [semanticText(item), 1],
  ];
  const ranked = concrete.map((subject) => {
    const pattern = FEATURE_PATTERNS.subjects[subject];
    let score = 0;
    let first = Number.MAX_SAFE_INTEGER;
    for (const [field, weight] of fields) {
      const match = pattern?.exec(field);
      if (!match) continue;
      score += weight;
      first = Math.min(first, Number(match.index || 0));
    }
    return { subject, score, first };
  }).filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.first - b.first || a.subject.localeCompare(b.subject));
  return ranked[0]?.subject || null;
}

function intendedEffectText(item = {}) {
  return clean([item.question, item.hypothesis].filter(Boolean).join(' '), 4_000)
    .toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .replace(/\b(?:zonder|niet|niets)\b.{0,30}\b(?:schakelen|bedienen|aanzetten|uitzetten|uitschakelen|inschakelen)\b/g, ' ');
}

function hasExplicitReadOnlyIntent(item = {}) {
  const text = clean([
    item.question, item.hypothesis, item.expected_observation || item.expectedObservation,
    item.stop_rule || item.stopRule,
  ].filter(Boolean).join(' '), 6_000).toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '');
  return /\bread[ -]?only\b/.test(text)
    || /\b(?:niets|geen enkel apparaat)\b.{0,30}\b(?:schakelen|bedienen|wijzigen)\b/.test(text)
    || /\bzonder\b.{0,35}\b(?:iets te )?(?:schakelen|bedienen|wijzigen)\b/.test(text)
    || /\balleen\b.{0,40}\b(?:lezen|uitlezen|teruglezen|onderzoeken|bekijken|controleren)\b/.test(text);
}

function canonicalStrongId(prefix, rawValue) {
  let value = normalizeTopicPart(rawValue, 100);
  if (prefix === 'event') value = value.replace(/perseiden?/g, 'perseids');
  return `${prefix}:${value}`;
}

function exactIdentityMention(text, rawValue) {
  const comparable = (value) => clean(value, 8_000).toLowerCase().normalize('NFD')
    .replace(/\p{Diacritic}/gu, '').replace(/[^a-z0-9]+/g, ' ').trim();
  const phrase = comparable(clean(rawValue, 120));
  if (phrase.length < 2) return false;
  return comparable(text).includes(phrase);
}

function durableReferenceExists(prefix, rawValue) {
  const raw = clean(rawValue, 120);
  const numeric = /^\d+$/.test(raw) ? Number(raw) : null;
  try {
    if (prefix === 'goal') return !!(numeric
      ? db.prepare('SELECT 1 FROM eva_goals WHERE id=?').get(numeric)
      : db.prepare('SELECT 1 FROM eva_goals WHERE goal_key=?').get(raw));
    if (prefix === 'project') return !!(numeric && db.prepare('SELECT 1 FROM eva_projects WHERE id=?').get(numeric));
    if (prefix === 'work') return !!(numeric && db.prepare('SELECT 1 FROM eva_work_items WHERE id=?').get(numeric));
    if (prefix === 'aspiration') return !!(numeric && db.prepare('SELECT 1 FROM aspirations WHERE id=?').get(numeric));
    if (prefix === 'artifact') {
      return !!(db.prepare('SELECT 1 FROM eva_capabilities WHERE slug=? OR name=?').get(raw, raw)
        || db.prepare('SELECT 1 FROM eva_tool_installations WHERE slug=? OR package_name=?').get(raw, raw));
    }
  } catch { return false; }
  return false;
}

function deriveTopicFeatures(item = {}) {
  const text = semanticText(item);
  const focusText = focusSemanticText(item);
  const collect = (patterns, source = text) => Object.entries(patterns)
    .filter(([, pattern]) => pattern.test(source)).map(([name]) => name).sort();
  let subjects = collect(FEATURE_PATTERNS.subjects);
  let focusSubjects = collect(FEATURE_PATTERNS.subjects, focusText);
  const strongIds = new Set();
  for (const match of text.matchAll(/\b((?:light|camera|sensor|binary_sensor|switch|vacuum|climate)\.[a-z0-9_]+)\b/g)) {
    strongIds.add(`entity:${match[1]}`);
  }
  for (const [eventKey, pattern] of ASTRONOMY_EVENTS) {
    if (!pattern.test(text)) continue;
    const year = text.match(/\b20\d{2}\b/)?.[0];
    strongIds.add(`event:${eventKey}${year ? `-${year}` : ''}`);
  }
  for (const anchor of Array.isArray(item.topic_anchors) ? item.topic_anchors : []) {
    const match = normalizeTopicPart(anchor, 120).match(/^(project|work|goal|aspiration|artifact):(.+)$/);
    if (!match) continue;
    const [, prefix, rawValue] = match;
    if (exactIdentityMention(text, rawValue) && durableReferenceExists(prefix, rawValue)) {
      strongIds.add(canonicalStrongId(prefix, rawValue));
    }
  }
  const components = collect(FEATURE_PATTERNS.components);
  for (const match of text.matchAll(/\bcomponent:([a-z0-9._-]{3,80})\b/g)) components.push(match[1]);
  if (subjects.includes('testing') && /\b(?:gate|ten onrechte|audit|correctie)\b/i.test(text)) {
    subjects = subjects.filter((subject) => ['testing', 'software'].includes(subject));
    focusSubjects = focusSubjects.filter((subject) => ['testing', 'software'].includes(subject));
  }
  const concreteSubjects = (values) => values.filter((subject) => !['household', 'testing'].includes(subject));
  focusSubjects = concreteSubjects(focusSubjects);
  if (!focusSubjects.length) focusSubjects = concreteSubjects(subjects);
  const effects = collect(FEATURE_PATTERNS.effects, intendedEffectText(item));
  let contexts = collect(FEATURE_PATTERNS.contexts);
  if (contexts.includes('back_door') || contexts.includes('front_door')) {
    contexts = contexts.filter((context) => context !== 'door');
  }
  return {
    version: TOPIC_FEATURE_VERSION,
    subjects,
    focus_subjects: [...new Set(focusSubjects)].sort(),
    focus_subject: primaryFocusSubject(item, subjects),
    dimensions: collect(FEATURE_PATTERNS.dimensions),
    focus_dimensions: collect(FEATURE_PATTERNS.dimensions, focusText),
    contexts,
    components: [...new Set(components)].sort(),
    effects,
    read_only: hasExplicitReadOnlyIntent(item) && effects.length === 0,
    strong_ids: [...strongIds].sort(),
  };
}

function explorationIdentity({ topicKey, topicAnchors, ...content } = {}) {
  const key = normalizeTopicPart(topicKey);
  const anchors = [...new Set((Array.isArray(topicAnchors) ? topicAnchors : [])
    .map((value) => normalizeTopicPart(value, 100)).filter((value) => value.length >= 4))].slice(0, 8);
  if (key.length < 4 || anchors.length < 2) {
    return {
      ok: false,
      reason: 'een nieuwe verkenning vereist een stabiele onderwerp_sleutel en minstens twee concrete onderwerp_ankers (bijvoorbeeld entity-, project-, gebeurtenis- en beslisdomein); methode- of tijdwoorden zijn geen onderwerp',
    };
  }
  const topicFeatures = deriveTopicFeatures({ ...content, topic_anchors: anchors });
  if (!topicFeatures.subjects.length && !topicFeatures.strong_ids.length) {
    return {
      ok: false,
      reason: 'de inhoud levert nog geen controleerbare canonieke onderwerpidentiteit op; noem het concrete onderwerp of een entity:, event: of artifact: letterlijk in vraag of hypothese',
    };
  }
  return { ok: true, topicKey: key, topicAnchors: anchors, topicFeatures };
}

function intersects(left = [], right = []) {
  const wanted = new Set(left);
  return right.some((value) => wanted.has(value));
}

function sameExplorationTopic(item, candidate) {
  if (clean(item?.question).toLowerCase() === clean(candidate?.question).toLowerCase()) return true;
  const prior = item?.topic_features?.version === TOPIC_FEATURE_VERSION ? item.topic_features : deriveTopicFeatures(item);
  const current = candidate?.topic_features?.version === TOPIC_FEATURE_VERSION ? candidate.topic_features : deriveTopicFeatures(candidate);
  const sharedStrongId = intersects(prior.strong_ids, current.strong_ids);
  for (const family of [/^(?:entity|device):/, /^event:/, /^project:/, /^work:/, /^goal:/, /^aspiration:/, /^artifact:/]) {
    const priorFamily = prior.strong_ids.filter((id) => family.test(id));
    const currentFamily = current.strong_ids.filter((id) => family.test(id));
    if (priorFamily.length && currentFamily.length && !intersects(priorFamily, currentFamily)) return false;
  }
  const priorFocus = prior.focus_subject || prior.focus_subjects?.[0] || null;
  const currentFocus = current.focus_subject || current.focus_subjects?.[0] || null;
  if (!priorFocus || priorFocus !== currentFocus) return false;
  const priorKey = normalizeTopicPart(item?.topic_key);
  const currentKey = normalizeTopicPart(candidate?.topic_key);
  if (sharedStrongId && priorKey.length >= 4 && priorKey === currentKey) return true;
  if (!sharedStrongId && prior.contexts.length && current.contexts.length
    && !intersects(prior.contexts, current.contexts)) return false;
  const softwareLike = prior.subjects.includes('software') && current.subjects.includes('software');
  if (softwareLike) {
    if (prior.components?.length && current.components?.length) {
      return intersects(prior.components, current.components);
    }
    return false;
  }
  const priorDimensions = prior.focus_dimensions || prior.dimensions;
  const currentDimensions = current.focus_dimensions || current.dimensions;
  return intersects(priorDimensions, currentDimensions);
}

function explorationSafetyConflict(item, candidate) {
  const prior = item?.topic_features?.version === TOPIC_FEATURE_VERSION ? item.topic_features : deriveTopicFeatures(item);
  const current = candidate?.topic_features?.version === TOPIC_FEATURE_VERSION ? candidate.topic_features : deriveTopicFeatures(candidate);
  const mutable = ['lighting', 'vacuum', 'cooling'];
  const priorMutableSubjects = (prior.focus_subjects || [prior.focus_subject]).filter((subject) => mutable.includes(subject));
  const currentMutableSubjects = (current.focus_subjects || [current.focus_subject]).filter((subject) => mutable.includes(subject));
  const mutableHouseSubject = intersects(priorMutableSubjects, currentMutableSubjects);
  const priorSafetyConstraint = intersects(prior.dimensions, ['presence', 'connectivity']);
  const safetyContextMatches = !prior.contexts.length
    || !current.contexts.length
    || intersects(prior.contexts, current.contexts);
  return mutableHouseSubject && priorSafetyConstraint && safetyContextMatches && !current.read_only;
}

function backfillTopicFeatures() {
  const rows = db.prepare('SELECT * FROM eva_explorations').all();
  const update = db.prepare('UPDATE eva_explorations SET topic_features_json=? WHERE id=?');
  db.transaction(() => {
    for (const row of rows) {
      const features = JSON.stringify(deriveTopicFeatures({
        ...row, topic_anchors: parseJson(row.topic_anchors_json, []),
      }));
      if (features !== (row.topic_features_json || '{}')) update.run(features, row.id);
    }
  })();
}

backfillTopicFeatures();

// Een autonome ontdekking hoort bij hetzelfde geheugen als het gesprek. Geef
// alleen lexicaal relevante recente resultaten door; zo kan een chat vervolg
// geven aan bewijs uit een aandachtcyclus zonder een apart "onderdeel" te zijn.
export function relevantExplorationContextText(query, limit = 4) {
  const expected = new Set(topicWords(query));
  if (!expected.size) return '';
  const matches = listExplorations({ limit: 30, sources: ['self', 'conversation'] }).map((item) => {
    const haystack = `${item.question} ${item.hypothesis} ${item.result || ''} ${item.decision || ''} ${item.evidence.map((row) => row?.text || row?.evidence || '').join(' ')}`;
    const actual = new Set(topicWords(haystack));
    let score = 0;
    for (const word of expected) if (actual.has(word)) score += 1;
    return { item, score };
  }).filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score || Date.parse(b.item.updated_ts) - Date.parse(a.item.updated_ts))
    .slice(0, Math.max(1, Number(limit) || 4));
  if (!matches.length) return '';
  return ['Relevant bewijs dat je zelf in eerdere aandachtcycli vond:', ...matches.map(({ item }) => (
    `- verkenning #${item.id} [${item.status}] ${item.question}${item.result ? ` | uitkomst: ${item.result}` : ''}${item.decision ? ` | besluit: ${item.decision}` : ''}`
  ))].join('\n');
}

export function dueAttention() {
  const now = Date.now();
  const exploration = listExplorations({ activeOnly: true, limit: 20, sources: ['self'] })
    .filter((item) => !item.next_wake_ts || Date.parse(item.next_wake_ts) <= now)
    .sort((a, b) => Date.parse(a.last_selected_ts || a.updated_ts) - Date.parse(b.last_selected_ts || b.updated_ts))[0];
  if (exploration) return { kind: 'exploration', id: exploration.id, text: exploration.question };
  const aspiration = activeAspirations().filter((item) => aspirationDue(item, now))
    .sort((a, b) => Number(b.priority || 0.5) - Number(a.priority || 0.5))[0];
  return aspiration ? { kind: 'aspiration', id: aspiration.id, text: aspiration.text } : null;
}

export const __curiosityTest = {
  aspirationDue, isProductionExploration, isRelationshipExploration,
  explorationIdentity, sameExplorationTopic, explorationSafetyConflict, deriveTopicFeatures,
};
