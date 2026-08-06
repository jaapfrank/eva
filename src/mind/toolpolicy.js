// Centrale effectmetadata voor Eva's kerngereedschap. Routers mogen hiermee het
// aanbod beperken, maar dezelfde policy wordt ook vlak voor uitvoering opnieuw
// gecontroleerd: een model- of fallbackfout kan de grens daardoor niet omzeilen.

export const TOOL_EFFECT = Object.freeze({
  read: 'read',
  home: 'home',
  voice: 'voice',
  external: 'external',
  workspace: 'workspace',
  persistent: 'persistent',
});

const EFFECT_VALUES = new Set(Object.values(TOOL_EFFECT));

function operation(args = {}) {
  return String(args?.actie ?? args?.bewerking ?? '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .trim()
    .toLowerCase();
}

function fixed(effect, extras = {}) {
  return { effect, ...extras };
}

function mixed(defaultEffect, readOperations, extras = {}) {
  const reads = new Set(readOperations);
  return {
    effect: 'mixed',
    resolveEffect: (args) => reads.has(operation(args)) ? TOOL_EFFECT.read : defaultEffect,
    ...extras,
  };
}

function freezeMetadata(metadata) {
  return Object.freeze(Object.fromEntries(Object.entries(metadata).map(([name, policy]) => [
    name,
    Object.freeze(policy),
  ])));
}

/**
 * `synthetic` beschrijft of een geïsoleerde evaluatie de tool zinvol mag zien:
 * - allow: volledig read-only;
 * - isolated: effect blijft binnen de tijdelijke werkruimte;
 * - deny: persistent, fysiek of extern effect.
 *
 * `report` is metadata voor een latere communicatiepolicy en autoriseert niets.
 * `durable` geeft aan dat een muterende call bij herstart niet blind herhaald mag
 * worden. `changesByDefault` bewaart exact de bestaande ActionResult-normalisatie.
 */
export const TOOL_POLICY = freezeMetadata({
  huis_status: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  bedien_huis: fixed(TOOL_EFFECT.home, { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true }),
  gebruik_robot: mixed(TOOL_EFFECT.home, ['status', 'kaart', 'kijk'], { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true }),
  ha_lees: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  wie_is_thuis: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  beheer_aanwezigheid: mixed(TOOL_EFFECT.home, ['bekijk'], { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true }),
  zoek_internet: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  beheer_agentmail: mixed(TOOL_EFFECT.external, [
    'status', 'berichten', 'inbox', 'lezen', 'list', 'zoeken', 'zoek', 'search',
  ], { synthetic: 'deny', report: 'effect', durable: true, changesByDefault: true }),
  lees_bestand: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  schrijf_bestand: fixed(TOOL_EFFECT.workspace, { synthetic: 'isolated', report: 'none', durable: true, changesByDefault: true }),
  lijst_bestanden: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  voer_uit: fixed(TOOL_EFFECT.workspace, { synthetic: 'isolated', report: 'none', durable: true, changesByDefault: true }),
  maak_vaardigheid: mixed(TOOL_EFFECT.workspace, ['lijst'], { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  beheer_addon: mixed(TOOL_EFFECT.workspace, ['lijst'], { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  gebruik_capability: fixed(TOOL_EFFECT.workspace, { synthetic: 'isolated', report: 'none', durable: true, changesByDefault: false }),
  inspecteer_vermogen: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  beheer_werkcel: mixed(TOOL_EFFECT.persistent, ['status', 'lijst'], { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  beheer_toolpakket: mixed(TOOL_EFFECT.workspace, ['inspecteer', 'status', 'lijst'], { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  onthoud: fixed(TOOL_EFFECT.persistent, { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  zoek_geheugen: fixed(TOOL_EFFECT.read, { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false }),
  zoek_gesprek: fixed(TOOL_EFFECT.read, { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false }),
  zet_herinnering: fixed(TOOL_EFFECT.persistent, { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true }),
  toon_herinneringen: fixed(TOOL_EFFECT.read, { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false }),
  annuleer_herinnering: fixed(TOOL_EFFECT.persistent, { synthetic: 'deny', report: 'effect', durable: true, changesByDefault: true }),
  bekijk_camera: fixed(TOOL_EFFECT.read, { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false }),
  leer_gezicht_van_foto: fixed(TOOL_EFFECT.persistent, { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  spreek_in_huis: fixed(TOOL_EFFECT.voice, { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  stuur_bericht: fixed(TOOL_EFFECT.external, { synthetic: 'deny', report: 'delivery', durable: true, changesByDefault: true }),
  doel_bijhouden: mixed(TOOL_EFFECT.persistent, ['list', 'lijst'], { synthetic: 'deny', report: 'none', durable: true, changesByDefault: false }),
  werk_bijhouden: fixed(TOOL_EFFECT.persistent, { synthetic: 'deny', report: 'none', durable: true, changesByDefault: false }),
  verkenning_bijhouden: mixed(TOOL_EFFECT.persistent, ['list', 'lijst'], { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true }),
  toon_werk: fixed(TOOL_EFFECT.read, { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false }),
  signalen_afhandelen: fixed(TOOL_EFFECT.persistent, { synthetic: 'deny', report: 'none', durable: true, changesByDefault: false }),
});

export function toolPolicy(name) {
  return TOOL_POLICY[String(name || '')] || null;
}

export function toolSyntheticAllowed(name) {
  return ['allow', 'isolated'].includes(toolPolicy(name)?.synthetic);
}

export function toolIsDurable(name) {
  return toolPolicy(name)?.durable === true;
}

export function toolRequiresEffectReport(name) {
  return ['effect', 'conditional'].includes(toolPolicy(name)?.report);
}

/** Geeft `read` of exact één effectdomein terug; onbekende tools zijn `unknown`. */
export function toolEffect(name, args = {}) {
  const policy = toolPolicy(name);
  if (!policy) return 'unknown';
  const effect = policy.effect === 'mixed'
    ? policy.resolveEffect(args)
    : policy.effect;
  return EFFECT_VALUES.has(effect) ? effect : 'unknown';
}

function effectSet(value) {
  const values = value instanceof Set ? [...value] : Array.isArray(value) ? value : value == null ? [] : [value];
  return new Set(values.map((item) => String(item || '').trim().toLowerCase()).filter(EFFECT_VALUES.has.bind(EFFECT_VALUES)));
}

/**
 * Pure authorizationcheck. Zonder policy is alles backwards-compatible
 * toegestaan. Ondersteunde vormen:
 * - `read_only` / `{ mode: 'read_only' }`;
 * - `{ allowedEffects: ['workspace'], deniedEffects: ['external'] }`;
 * - aliassen `allowEffects`/`denyEffects` en `readOnly: true`.
 * Read-only tools blijven standaard toegestaan, tenzij `allowRead: false`.
 */
export function toolAllowedByPolicy(name, args = {}, effectPolicy = null) {
  if (effectPolicy == null || effectPolicy === '' || effectPolicy === 'allow' || effectPolicy === 'all'
    || effectPolicy === 'unrestricted') return true;

  const effect = toolEffect(name, args);
  if (effect === 'unknown') return false;
  const raw = typeof effectPolicy === 'string' ? { mode: effectPolicy } : effectPolicy;
  if (!raw || typeof raw !== 'object') return false;
  const mode = String(raw.mode || (raw.readOnly === true ? 'read_only' : 'allow')).trim().toLowerCase();
  if (['deny', 'deny_all', 'none'].includes(mode)) return false;
  if (effect === TOOL_EFFECT.read) return raw.allowRead !== false;
  if (['read', 'read_only', 'readonly'].includes(mode)) return false;

  const denied = effectSet(raw.deniedEffects ?? raw.denyEffects ?? raw.deny);
  if (denied.has(effect)) return false;
  const allowedValue = raw.allowedEffects ?? raw.allowEffects ?? raw.allow;
  if (allowedValue != null) return effectSet(allowedValue).has(effect);
  return ['allow', 'all', 'unrestricted'].includes(mode);
}

export function toolChangesByDefault(name) {
  return toolPolicy(name)?.changesByDefault === true;
}
