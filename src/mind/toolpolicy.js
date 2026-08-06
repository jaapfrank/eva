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

const bounded = (tags, extras) => ({ autonomy: 'bounded', tags, ...extras });
const full = (tags, extras) => ({ autonomy: 'full', tags, ...extras });
const never = (tags, extras) => ({ autonomy: 'none', tags, ...extras });

function freezeMetadata(metadata) {
  return Object.freeze(Object.fromEntries(Object.entries(metadata).map(([name, policy]) => [
    name,
    Object.freeze({
      ...policy,
      ...(Array.isArray(policy.recoveryTools)
        ? { recoveryTools: Object.freeze([...policy.recoveryTools]) }
        : {}),
    }),
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
  huis_status: fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house contribution:house proof:house grounding', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false, proofRank: 2, diagnosticFallback: true })),
  bedien_huis: fixed(TOOL_EFFECT.home, full('house delegated contribution:house grounding', { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true, verification: 'explicit', proofRank: 5, recoveryTools: ['ha_lees'], exclusiveRecovery: true })),
  gebruik_robot: mixed(TOOL_EFFECT.home, ['status', 'kaart', 'kijk'], full('discovery house delegated', { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true, verification: 'explicit' })),
  ha_lees: fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house primary:house contribution:house proof:house grounding', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false, proofRank: 2, diagnosticFallback: true })),
  wie_is_thuis: fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house lane:family primary:house contribution:house proof:house grounding', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false, diagnosticFallback: true })),
  beheer_aanwezigheid: mixed(TOOL_EFFECT.home, ['bekijk'], full('house delegated lane:house primary:house', { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true, verification: 'explicit' })),
  zoek_internet: fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace primary:research contribution:research proof:project grounding', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false, proofRank: 4, diagnosticFallback: true })),
  beheer_agentmail: mixed(TOOL_EFFECT.external, [
    'status', 'berichten', 'inbox', 'lezen', 'list', 'zoeken', 'zoek', 'search',
  ], full('delegated lane:research lane:workspace lane:family proof:project grounding', { synthetic: 'deny', report: 'effect', durable: true, changesByDefault: true, recoveryTools: ['zoek_internet'] })),
  lees_bestand: fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace primary:workspace contribution:making proof:project grounding reasoning:mechanical', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false, proofRank: 3, diagnosticFallback: true, recoveryTools: ['lijst_bestanden'] })),
  schrijf_bestand: fixed(TOOL_EFFECT.workspace, bounded('lane:research lane:workspace primary:workspace contribution:making proof:project reasoning:code', { synthetic: 'isolated', report: 'none', durable: true, changesByDefault: true, verification: 'ok', recoveryTools: ['lijst_bestanden', 'voer_uit'] })),
  lijst_bestanden: fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace primary:workspace proof:project', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false, diagnosticFallback: true })),
  voer_uit: fixed(TOOL_EFFECT.workspace, bounded('lane:research lane:workspace primary:workspace contribution:making proof:project grounding reasoning:code', { synthetic: 'isolated', report: 'none', durable: true, changesByDefault: true, verification: 'ok', proofRank: 5 })),
  maak_vaardigheid: mixed(TOOL_EFFECT.workspace, ['lijst'], bounded('contribution:making reasoning:code', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true, verification: 'explicit' })),
  beheer_addon: mixed(TOOL_EFFECT.workspace, ['lijst'], bounded('contribution:making reasoning:code', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true, verification: 'explicit' })),
  gebruik_capability: fixed(TOOL_EFFECT.workspace, bounded('contribution:making reasoning:mechanical', { synthetic: 'isolated', report: 'none', durable: true, changesByDefault: false })),
  inspecteer_vermogen: fixed(TOOL_EFFECT.read, bounded('discovery lane:research lane:workspace lane:family', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false })),
  beheer_werkcel: mixed(TOOL_EFFECT.persistent, ['status', 'lijst'], bounded('discovery lane:research lane:workspace primary:research', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true, verification: 'explicit' })),
  beheer_toolpakket: mixed(TOOL_EFFECT.workspace, ['inspecteer', 'status', 'lijst'], bounded('discovery lane:research lane:workspace primary:research reasoning:code', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true, verification: 'explicit' })),
  onthoud: fixed(TOOL_EFFECT.persistent, bounded('lane:family primary:family', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true, verification: 'ok' })),
  zoek_geheugen: fixed(TOOL_EFFECT.read, bounded('discovery free lane:family primary:family grounding', { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false, diagnosticFallback: true })),
  zoek_gesprek: fixed(TOOL_EFFECT.read, full('lane:house lane:family grounding', { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false, diagnosticFallback: true })),
  zet_herinnering: fixed(TOOL_EFFECT.persistent, full('delegated', { synthetic: 'deny', report: 'conditional', durable: true, changesByDefault: true, verification: 'ok' })),
  toon_herinneringen: fixed(TOOL_EFFECT.read, bounded('discovery free', { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false, diagnosticFallback: true })),
  annuleer_herinnering: fixed(TOOL_EFFECT.persistent, full('delegated', { synthetic: 'deny', report: 'effect', durable: true, changesByDefault: true, verification: 'ok' })),
  bekijk_camera: fixed(TOOL_EFFECT.read, bounded('discovery house free lane:house primary:house contribution:house proof:house grounding', { synthetic: 'allow', report: 'none', durable: false, changesByDefault: false, proofRank: 4 })),
  leer_gezicht_van_foto: fixed(TOOL_EFFECT.persistent, never('delegated', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true, verification: 'ok' })),
  spreek_in_huis: fixed(TOOL_EFFECT.voice, full('house delegated contribution:house', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true, verification: 'ok' })),
  stuur_bericht: fixed(TOOL_EFFECT.external, bounded('free lane:house lane:research lane:workspace lane:family contribution:contact', { synthetic: 'deny', report: 'delivery', durable: true, changesByDefault: true, verification: 'ok' })),
  doel_bijhouden: mixed(TOOL_EFFECT.persistent, ['list', 'lijst'], full('', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: false })),
  werk_bijhouden: fixed(TOOL_EFFECT.persistent, bounded('lane:house lane:research lane:workspace lane:family', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: false })),
  verkenning_bijhouden: mixed(TOOL_EFFECT.persistent, ['list', 'lijst'], bounded('', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: true })),
  toon_werk: fixed(TOOL_EFFECT.read, full('free lane:house lane:workspace lane:family', { synthetic: 'deny', report: 'none', durable: false, changesByDefault: false, diagnosticFallback: true })),
  signalen_afhandelen: fixed(TOOL_EFFECT.persistent, bounded('free lane:house lane:research lane:workspace lane:family', { synthetic: 'deny', report: 'none', durable: true, changesByDefault: false })),
});

export function toolPolicy(name) {
  return TOOL_POLICY[String(name || '')] || null;
}

/**
 * Bepaalt na een mislukte voorkeursroute welke tools Eva voor herstel mag zien.
 * De verplichte tool blijft beschikbaar; diagnostiek en route-specifieke
 * alternatieven komen uitsluitend uit de centrale policy.
 */
export function toolNamesForRecovery(requiredNames = []) {
  const required = [...new Set(
    (Array.isArray(requiredNames) ? requiredNames : [])
      .map((name) => String(name || '').trim())
      .filter(Boolean),
  )];
  if (!required.length) return null;

  const onlyPolicy = required.length === 1 ? toolPolicy(required[0]) : null;
  if (onlyPolicy?.exclusiveRecovery === true) {
    return new Set([required[0], ...(onlyPolicy.recoveryTools || [])]);
  }

  const names = new Set(required);
  for (const [name, policy] of Object.entries(TOOL_POLICY)) {
    if (policy.diagnosticFallback === true) names.add(name);
  }
  for (const name of required) {
    for (const recoveryTool of toolPolicy(name)?.recoveryTools || []) names.add(recoveryTool);
  }
  return names;
}

export function toolHasTag(name, tag) {
  return String(toolPolicy(name)?.tags || '').split(/\s+/u).includes(String(tag || ''));
}

export function toolAvailableInAutonomy(name, fullAutonomy = false) {
  const profile = toolPolicy(name)?.autonomy;
  return profile === 'bounded' || (fullAutonomy && profile === 'full');
}

export function toolVerification(name) {
  return toolPolicy(name)?.verification || 'read';
}

export function toolProofRank(name) {
  return Number(toolPolicy(name)?.proofRank) || 1;
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
