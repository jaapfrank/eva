import { createHash } from 'node:crypto';
import { kvGet, kvSet } from '../db.js';
import {
  allStates,
  cachedStates,
  callService,
  getState,
  verifyEntityState,
} from '../integrations/homeassistant.js';
import {
  canonicalJson,
  dutchContentWords as words,
  normalizeDutchText as normalize,
} from '../util.js';

const HOME_LOCALITY_KEY = 'eva_home_locality_v1';
const VOICE_HOME_GROUNDING_CONTRACT = 'VoiceHomeGrounding/v1';
const HOME_HISTORY_ENTITY_LIMIT = 8;
const HOME_CONTEXT_ENTITY_LIMIT = 8;
const HOME_CONTEXT_RECORD_MAX_BYTES = 700;
const HOME_CONTEXT_MESSAGE_MAX_BYTES = 1_400;
const HOME_STRUCTURED_MAX_BYTES = 8_000;
const HOME_OVERVIEW_ITEM_LIMIT = 6;
const HOME_OVERVIEW_GROUP_MAX_BYTES = 720;
const HOME_CONTINUITY_ITEMS_MAX_BYTES = 4_500;
const HOME_CONTINUITY_MESSAGE_MAX_BYTES = 1_200;
const HOME_CONTEXT_QUERY_STOPWORDS = new Set(['en']);
const HOME_DOMAINS = new Set([
  'light', 'switch', 'input_boolean', 'fan', 'cover', 'media_player',
  'climate', 'scene', 'script', 'vacuum',
]);
const SYNONYM_GROUPS = [
  new Set(['licht', 'lichten', 'lamp', 'lampen', 'verlichting']),
  new Set(['buiten', 'tuin', 'terras', 'overkapping']),
  new Set(['heater', 'verwarming', 'verwarmer', 'terrasverwarmer']),
  new Set(['tv', 'televisie', 'media', 'speler']),
  new Set(['rolluik', 'gordijn', 'gordijnen', 'cover']),
  new Set(['bedroom', 'kamer', 'room', 'slaapkamer']),
];
const HOME_CONTEXT_DOMAIN_SEARCH_TAGS = Object.freeze({
  weather: Object.freeze([
    'buiten', 'weer', 'temperatuur', 'buitentemperatuur', 'outside', 'outdoor', 'weather',
  ]),
  climate: Object.freeze([
    'binnen', 'temperatuur', 'binnentemperatuur', 'kamertemperatuur',
    'inside', 'indoor', 'climate', 'klimaat', 'thermostaat',
  ]),
});
const HOME_CONTEXT_ROLE_SOURCE_TAGS = Object.freeze({
  weather: Object.freeze([
    'buiten', 'buitentemperatuur', 'outside', 'outdoor', 'weer', 'weather',
  ]),
  climate: Object.freeze([
    'binnen', 'binnentemperatuur', 'kamertemperatuur', 'inside', 'indoor',
    'climate', 'klimaat', 'thermostaat',
  ]),
});
const HOME_CONTEXT_TEMPERATURE_INTENT = new Set([
  'temperatuur', 'buitentemperatuur', 'binnentemperatuur', 'kamertemperatuur',
  'thermometer', 'graad', 'graden', 'temperature',
]);
const HOME_CONTEXT_COLD_WARM_COMPARATOR = new Set([
  'koud', 'koude', 'kouder', 'koudst', 'warm', 'warme', 'warmer', 'warmst',
]);
const HOME_CONTEXT_HUMIDITY_INTENT = new Set([
  'humidity', 'luchtvochtigheid', 'vochtigheid',
]);
const HOME_CONTEXT_COMPARATOR_INTENT = new Set([
  'kouder', 'warmer', 'hoger', 'lager', 'meer', 'minder',
  'verschil', 'verschillen', 'vergelijk', 'vergelijken', 'tegenover',
]);
const HOME_CONTEXT_REFERENCE_STOPWORDS = new Set([
  'resultaat', 'uitkomst', 'gevonden', 'receipt', 'stap', 'bron', 'waarde',
  'entity', 'entityid', 'exact', 'exacte', 'actueel', 'actuele', 'huidig', 'huidige',
]);
const HOME_REQUESTER_TARGET_BINDING_CONTRACT = 'HomeRequesterTargetBinding/v1';
const FIRST_PERSON_POSSESSIVE = /\b(?:mijn|mijne|m[’']n)\b/iu;
const GENERIC_HOME_ALIAS_WORDS = new Set([
  'apparaat', 'bedroom', 'kamer', 'lamp', 'lampen', 'licht', 'lichten',
  'room', 'slaapkamer', 'switch', 'verlichting',
]);

function relatedWord(left, right) {
  if (left === right) return true;
  return SYNONYM_GROUPS.some((group) => group.has(left) && group.has(right));
}

function friendlyName(state) {
  return String(state?.attributes?.friendly_name || state?.entity_id || '');
}

function jsonBytes(value) {
  return Buffer.byteLength(JSON.stringify(value), 'utf8');
}

function normalizedScalarText(value) {
  if (!['string', 'number', 'boolean'].includes(typeof value)
    || (typeof value === 'number' && !Number.isFinite(value))) return null;
  const source = String(value).replace(/\s+/gu, ' ').trim();
  return source || null;
}

function completeScalarText(value, maxBytes) {
  const source = normalizedScalarText(value);
  return source && Buffer.byteLength(source, 'utf8') <= maxBytes ? source : null;
}

function boundedDisplayText(value, maxBytes) {
  const source = normalizedScalarText(value);
  if (!source) return null;
  if (Buffer.byteLength(source, 'utf8') <= maxBytes) return source;
  const marker = ' [afgekort]';
  const contentBudget = maxBytes - Buffer.byteLength(marker, 'utf8');
  if (contentBudget <= 0) return null;
  let result = '';
  for (const character of source) {
    if (Buffer.byteLength(result + character, 'utf8') > contentBudget) break;
    result += character;
  }
  return result ? `${result.trimEnd()}${marker}` : null;
}

function finiteScalarNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/iu.test(text)
    || text.length > 32) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function exactHomeEntityId(value) {
  if (typeof value !== 'string') return null;
  const entityId = value.trim();
  return /^[a-z0-9_]+\.[a-z0-9_]+$/u.test(entityId)
    && Buffer.byteLength(entityId, 'utf8') <= 255 ? entityId : null;
}

function addBoundedRecordField(record, key, value) {
  if (value === null || value === undefined) return;
  const candidate = { ...record, [key]: value };
  if (jsonBytes(candidate) <= HOME_CONTEXT_RECORD_MAX_BYTES) record[key] = value;
}

function homeLocality(states) {
  const home = states.find((state) => state.entity_id === 'zone.home');
  const homeLat = Number(home?.attributes?.latitude);
  const homeLon = Number(home?.attributes?.longitude);
  const candidates = states
    .filter((state) => state.entity_id.includes('geocoded_location'))
    .map((state) => {
      const location = state.attributes?.Location;
      const lat = Number(Array.isArray(location) ? location[0] : NaN);
      const lon = Number(Array.isArray(location) ? location[1] : NaN);
      const locality = String(state.attributes?.Locality || '').trim();
      const region = String(state.attributes?.['Administrative Area'] || '').trim();
      const distance = Number.isFinite(homeLat) && Number.isFinite(homeLon)
        && Number.isFinite(lat) && Number.isFinite(lon)
        ? Math.hypot(homeLat - lat, homeLon - lon)
        : Number.POSITIVE_INFINITY;
      return { locality, region, distance };
    })
    .filter((item) => item.locality)
    .sort((left, right) => left.distance - right.distance);
  const best = candidates.find((item) => item.distance < 0.02) || candidates[0] || null;
  if (best) return [best.locality, best.region].filter(Boolean).join(', ');
  return home?.attributes?.friendly_name || 'Thuis';
}

export function compactHomeState(state) {
  const entityId = exactHomeEntityId(state?.entity_id);
  if (!entityId) return null;
  const attributes = state?.attributes && typeof state.attributes === 'object'
    && !Array.isArray(state.attributes) ? state.attributes : {};
  const record = { entity_id: entityId };
  addBoundedRecordField(record, 'name',
    boundedDisplayText(attributes.friendly_name, 96) || entityId);
  addBoundedRecordField(record, 'state', completeScalarText(state?.state, 64));
  const updated = completeScalarText(state?.last_updated || state?.last_changed, 48);
  addBoundedRecordField(record, 'updated',
    updated && Number.isFinite(Date.parse(updated)) ? updated : null);
  addBoundedRecordField(record, 'unit',
    completeScalarText(attributes.unit_of_measurement, 24));
  addBoundedRecordField(record, 'temperature_unit',
    completeScalarText(attributes.temperature_unit, 24));
  for (const field of [
    'current_temperature', 'temperature', 'target_temperature',
    'upper_limit', 'lower_limit',
  ]) addBoundedRecordField(record, field, finiteScalarNumber(attributes[field]));
  addBoundedRecordField(record, 'device_class',
    completeScalarText(attributes.device_class, 48));
  return record;
}

function openingState(state) {
  return state.entity_id.startsWith('binary_sensor.')
    && ['door', 'window', 'garage_door', 'opening'].includes(state.attributes?.device_class);
}

function unknownState(state) {
  return ['unavailable', 'unknown'].includes(state.state);
}

function exactEntityOrder(left, right) {
  const leftId = String(left?.entity_id || '');
  const rightId = String(right?.entity_id || '');
  return leftId < rightId ? -1 : leftId > rightId ? 1 : 0;
}

function homeHistorySalience(state) {
  if (openingState(state) && state.state === 'on') return 0;
  if (openingState(state) && unknownState(state)) return 1;
  if (state.entity_id.startsWith('person.') && ['home', 'not_home'].includes(state.state)) return 2;
  if (state.entity_id.startsWith('light.') && state.state === 'on') return 3;
  return null;
}

function requestedHomeContextRoleDomains(queryWords) {
  const domains = Object.entries(HOME_CONTEXT_ROLE_SOURCE_TAGS)
    .filter(([, tags]) => queryWords.some((queryWord) => (
      tags.some((tag) => relatedWord(queryWord, tag))
    )))
    .map(([domain]) => domain);
  const temperatureIntent = queryWords.some((word) => (
    HOME_CONTEXT_TEMPERATURE_INTENT.has(word) || word.endsWith('temperatuur')
  ));
  const coldWarmComparator = domains.length >= 2
    && queryWords.some((word) => HOME_CONTEXT_COLD_WARM_COMPARATOR.has(word));
  return temperatureIntent || coldWarmComparator ? domains : [];
}

function temperatureRoleSemanticWord(word) {
  return HOME_CONTEXT_TEMPERATURE_INTENT.has(word) || word.endsWith('temperatuur')
    || HOME_CONTEXT_COLD_WARM_COMPARATOR.has(word)
    || HOME_CONTEXT_COMPARATOR_INTENT.has(word)
    || Object.values(HOME_CONTEXT_ROLE_SOURCE_TAGS).some((tags) => (
      tags.some((tag) => relatedWord(word, tag))
    ));
}

function homeContextRoleTemperatureProof(domain, entity) {
  const field = domain === 'weather' ? 'temperature'
    : domain === 'climate' ? 'current_temperature' : null;
  return { field, present: Boolean(field
    && typeof entity?.[field] === 'number' && Number.isFinite(entity[field])) };
}

function homeContextRoleReference(queryWords, domain) {
  const tags = HOME_CONTEXT_ROLE_SOURCE_TAGS[domain] || [];
  return queryWords.filter((queryWord) => (
    tags.some((tag) => relatedWord(queryWord, tag))
  )).join(' ');
}

function topHomeContextRoleCandidates(semantic, domain, reference) {
  const ranked = semantic.filter((item) => item.entity.entity_id.startsWith(`${domain}.`))
    .map((item) => ({ ...item,
      roleScore: homeContextReferenceScore({ sourceText: reference }, item.entity) }))
    .filter((item) => item.roleScore > 0)
    .sort((left, right) => right.roleScore - left.roleScore
      || exactEntityOrder(left.entity, right.entity));
  if (!ranked.length) return [];
  return ranked.filter((item) => item.roleScore === ranked[0].roleScore);
}

function topHomeContextReferenceCandidates(semantic, reference) {
  const ranked = semantic.map((item) => ({ ...item,
    roleScore: homeContextReferenceScore({ sourceText: reference }, item.entity) }))
    .filter((item) => item.roleScore > 0)
    .sort((left, right) => right.roleScore - left.roleScore
      || exactEntityOrder(left.entity, right.entity));
  if (!ranked.length) return [];
  return ranked.filter((item) => item.roleScore === ranked[0].roleScore);
}

function boundedSemanticHomeContextSelection(semantic, queryWords, limit) {
  const roleDomains = requestedHomeContextRoleDomains(queryWords);
  const reservedDomains = new Set(roleDomains);
  const selected = [];
  const selectedIds = new Set();
  const roleCandidates = [];
  for (const domain of roleDomains) {
    const reference = homeContextRoleReference(queryWords, domain);
    const top = topHomeContextRoleCandidates(semantic, domain, reference);
    roleCandidates.push({ domain, reference, top,
      globalTop: topHomeContextReferenceCandidates(semantic, reference) });
    if (!top.length || top.length > limit - selected.length) continue;
    for (const item of top) {
      selected.push(item);
      selectedIds.add(item.entity.entity_id);
    }
  }
  for (const item of semantic) {
    if (selected.length >= limit) break;
    const domain = item.entity.entity_id.split('.')[0];
    if (selectedIds.has(item.entity.entity_id) || reservedDomains.has(domain)) continue;
    selected.push(item);
    selectedIds.add(item.entity.entity_id);
  }
  const omittedNonTemperatureCandidates = semantic.filter((item) => (
    !selectedIds.has(item.entity.entity_id)
    && item.matchedQueryWords.some((word) => !temperatureRoleSemanticWord(word))
  )).length;
  const roles = roleCandidates.map(({ domain, reference, top, globalTop }) => {
    const retainedTop = top.filter((item) => selectedIds.has(item.entity.entity_id));
    const winner = top.length === 1 ? top[0] : null;
    const typedValue = homeContextRoleTemperatureProof(domain, winner?.entity);
    const globallyUnique = winner && globalTop.length === 1
      && globalTop[0].entity.entity_id === winner.entity.entity_id
      && globalTop[0].roleScore >= 12;
    const unique = Boolean(globallyUnique && retainedTop.length === 1
      && typedValue.present);
    return {
      domain,
      reference_words: homeContextReferenceWords(reference),
      candidate_count: semantic.filter((item) => (
        item.entity.entity_id.startsWith(`${domain}.`)
      )).length,
      top_tie_count: top.length,
      retained_top_tie_count: retainedTop.length,
      global_top_tie_count: globalTop.length,
      typed_value_field: typedValue.field,
      typed_value_present: typedValue.present,
      unique,
      ...(unique ? { selected_entity_id: winner.entity.entity_id } : {}),
    };
  });
  const winners = roles.flatMap((role) => (
    role.unique ? [role.selected_entity_id] : []
  ));
  const distinct = roles.length > 0 && winners.length === roles.length
    && new Set(winners).size === winners.length;
  return {
    entities: selected.map((item) => item.entity),
    bindingCoverage: roles.length ? {
      complete: roles.every((role) => role.unique) && distinct
        && omittedNonTemperatureCandidates === 0,
      distinct,
      omitted_non_temperature_candidates: omittedNonTemperatureCandidates,
      roles,
    } : null,
  };
}

function homeContextSelectionCoverage(matchedCandidates, returnedCandidates) {
  const omittedCandidates = Math.max(0, matchedCandidates - returnedCandidates);
  return {
    complete: omittedCandidates === 0,
    matched_candidates: matchedCandidates,
    returned_candidates: returnedCandidates,
    omitted_candidates: omittedCandidates,
  };
}

function humidityScalarProof(entity) {
  if (!String(entity?.entity_id || '').startsWith('sensor.')
    || finiteScalarNumber(entity?.state) === null || !completeScalarText(entity?.unit, 24)) {
    return false;
  }
  return entity.device_class === 'humidity'
    || homeContextEntityWords(entity).some((word) => (
      HOME_CONTEXT_HUMIDITY_INTENT.has(word)
    ));
}

function hasDistinctRoleProof(roleCandidates, index = 0, used = new Set()) {
  if (index >= roleCandidates.length) return true;
  return roleCandidates[index].some((entityId) => {
    if (used.has(entityId)) return false;
    const next = new Set(used);
    next.add(entityId);
    return hasDistinctRoleProof(roleCandidates, index + 1, next);
  });
}

function homeContextMeasurementCoverage(queryWords, entities) {
  if (!queryWords.some((word) => HOME_CONTEXT_HUMIDITY_INTENT.has(word))) return null;
  const proofs = entities.filter(humidityScalarProof);
  const roles = Object.entries(HOME_CONTEXT_ROLE_SOURCE_TAGS)
    .flatMap(([source, tags]) => {
      const referenceWords = queryWords.filter((queryWord) => (
        tags.some((tag) => relatedWord(queryWord, tag))
      ));
      if (!referenceWords.length) return [];
      const candidates = proofs.filter((entity) => (
        homeContextReferenceScore({ sourceText: referenceWords.join(' ') }, entity) >= 12
      )).map((entity) => entity.entity_id);
      return [{ source, reference_words: referenceWords,
        proof_records: candidates.length, candidates }];
    });
  const roleCandidates = roles.map((role) => role.candidates);
  const distinct = roles.length ? hasDistinctRoleProof(roleCandidates) : proofs.length > 0;
  return {
    kind: 'humidity_scalar_state_and_unit',
    complete: proofs.length > 0 && roles.every((role) => role.proof_records > 0) && distinct,
    proof_records: proofs.length,
    distinct,
    roles: roles.map(({ candidates: _candidates, ...role }) => role),
  };
}

function selectHomeContextEntities(states, query, historyRequested) {
  const queryWords = words(query).filter((word) => !HOME_CONTEXT_QUERY_STOPWORDS.has(word));
  const causalHistory = historyRequested
    && /\b(?:waarom|waardoor|oorzaak|hoe\s+kan\s+dat)\b/iu.test(String(query || ''));
  const mediaRequested = queryWords.some((word) => ['tv', 'televisie', 'media', 'speler'].includes(word));
  const semantic = states
    .map((state) => {
      const entity = compactHomeState(state);
      if (!entity) return null;
      const domain = entity.entity_id.split('.')[0];
      const searchTags = HOME_CONTEXT_DOMAIN_SEARCH_TAGS[domain] || [];
      const candidateWords = words(`${entity.entity_id} ${entity.name || ''} ${searchTags.join(' ')}`);
      const matchedQueryWords = queryWords.filter((word) => (
        candidateWords.some((candidate) => relatedWord(word, candidate))
      ));
      let score = matchedQueryWords.length;
      if (causalHistory && (domain === 'automation'
        || entity.entity_id.startsWith('switch.automation_'))) score += 3;
      if (causalHistory && mediaRequested && domain === 'media_player') score += 2;
      return { entity, score, matchedQueryWords };
    })
    .filter((item) => item?.score > 0)
    .sort((left, right) => right.score - left.score
      || exactEntityOrder(left.entity, right.entity));
  if (semantic.length || requestedHomeContextRoleDomains(queryWords).length) {
    const limit = historyRequested ? HOME_HISTORY_ENTITY_LIMIT : HOME_CONTEXT_ENTITY_LIMIT;
    const bounded = boundedSemanticHomeContextSelection(semantic, queryWords, limit);
    return { selection: semantic.length ? 'semantic' : 'none',
      entities: bounded.entities,
      selectionCoverage: homeContextSelectionCoverage(
        semantic.length, bounded.entities.length,
      ),
      bindingCoverage: bounded.bindingCoverage,
      measurementCoverage: homeContextMeasurementCoverage(queryWords, bounded.entities) };
  }
  if (!historyRequested) return { selection: 'none', entities: [],
    selectionCoverage: homeContextSelectionCoverage(0, 0), bindingCoverage: null,
    measurementCoverage: homeContextMeasurementCoverage(queryWords, []) };
  const fallbackCandidates = states
    .map((state) => ({ state, salience: homeHistorySalience(state) }))
    .filter((item) => item.salience != null)
    .sort((left, right) => left.salience - right.salience
      || String(right.state.last_updated || right.state.last_changed || '')
        .localeCompare(String(left.state.last_updated || left.state.last_changed || ''))
      || exactEntityOrder(left.state, right.state))
    .map((item) => compactHomeState(item.state))
    .filter(Boolean);
  const fallback = fallbackCandidates.slice(0, HOME_HISTORY_ENTITY_LIMIT);
  return { selection: fallback.length ? 'salient_fallback' : 'none', entities: fallback,
    selectionCoverage: homeContextSelectionCoverage(
      fallbackCandidates.length, fallback.length,
    ),
    bindingCoverage: null,
    measurementCoverage: homeContextMeasurementCoverage(queryWords, fallback) };
}

function homeContextStructuredSelectionState(selected) {
  const selectionCoverage = selected.selectionCoverage
    || homeContextSelectionCoverage(selected.entities.length, selected.entities.length);
  const bindingCoverage = selected.bindingCoverage || null;
  const bindingComplete = bindingCoverage?.complete === true;
  const measurementCoverage = selected.measurementCoverage || null;
  const measurementComplete = measurementCoverage?.complete !== false;
  const candidateComplete = selectionCoverage.complete
    ? (!bindingCoverage || bindingComplete) : bindingComplete;
  const complete = candidateComplete && measurementComplete;
  const truncated = selectionCoverage.omitted_candidates > 0
    && !(bindingComplete && measurementComplete);
  return {
    complete,
    verification_scope: bindingComplete && !selectionCoverage.complete
      ? 'requested_source_binding_candidates'
      : complete ? 'all_semantic_candidates' : 'returned_candidates_only',
    partial: !complete,
    needs_followup: !complete,
    ...(truncated ? { truncated: true } : {}),
    selection_coverage: selectionCoverage,
    ...(bindingCoverage ? { binding_coverage: bindingCoverage } : {}),
    ...(measurementCoverage ? { measurement_coverage: measurementCoverage } : {}),
  };
}

function homeContextReferenceWords(value) {
  return words(value).filter((word) => !HOME_CONTEXT_REFERENCE_STOPWORDS.has(word)
    && !/^r\d+$/u.test(word));
}

function homeContextEntityWords(entity) {
  const entityId = String(entity?.entity_id || '');
  const domain = entityId.split('.')[0];
  return homeContextReferenceWords([
    entityId,
    entity?.name,
    entity?.friendly_name,
    entity?.device_class,
    ...(HOME_CONTEXT_DOMAIN_SEARCH_TAGS[domain] || []),
  ].filter(Boolean).join(' '));
}

function homeContextReferenceWordScore(reference, candidate) {
  if (reference === candidate) return 12;
  if (relatedWord(reference, candidate)) return 8;
  if (reference.length >= 4 && candidate.length >= 4
    && (reference.includes(candidate) || candidate.includes(reference))) return 4;
  return 0;
}

function homeContextReferenceScore(reference, entity) {
  const candidateWords = homeContextEntityWords(entity);
  if (!candidateWords.length) return 0;
  const referenceWords = homeContextReferenceWords(reference?.sourceText);
  const base = referenceWords.reduce((sum, word) => (
    sum + candidateWords.reduce(
      (best, candidate) => Math.max(best, homeContextReferenceWordScore(word, candidate)), 0,
    )
  ), 0);
  const domain = String(entity?.entity_id || '').split('.')[0];
  const objectAndNameWords = words(`${String(entity?.entity_id || '').split('.')[1] || ''} ${entity?.name || entity?.friendly_name || ''}`);
  const wholeHome = objectAndNameWords.some((word) => (
    ['home', 'huis', 'thuis', 'woning', 'average', 'gemiddeld'].includes(word)
  ));
  const asksIndoor = referenceWords.some((word) => (
    ['binnen', 'binnentemperatuur', 'kamertemperatuur', 'inside', 'indoor'].includes(word)
  ));
  const asksOutdoor = referenceWords.some((word) => (
    ['buiten', 'buitentemperatuur', 'outside', 'outdoor', 'weer', 'weather'].includes(word)
  ));
  // Bij een algemene binnenwaarde is een expliciete hele-woningthermostaat
  // een betere bron dan een willekeurige kamer. Zonder zo'n unieke aggregate
  // blijft de bestaande gelijkstand bewust fail-closed.
  return base
    + (asksIndoor && domain === 'climate' && wholeHome ? 6 : 0)
    + (asksOutdoor && domain === 'weather' ? 6 : 0);
}

/**
 * Bindt onopgeloste semantische HA-referenties uitsluitend wanneer een verse
 * contextread voor iedere requirement één duidelijke, onderling verschillende
 * entity-kandidaat bevat. Bij een gelijke score, ontbrekende kandidaat of
 * dubbele entity wordt niets geraden en blijft de gewone vervolgstap beschikbaar.
 */
export function bindHomeContextEntityReferences(references = [], entities = []) {
  if (!Array.isArray(references) || !references.length || references.length > 8
    || !Array.isArray(entities) || !entities.length || entities.length > 24) return null;
  const normalizedEntities = entities.map((entity) => ({
    ...entity, entity_id: String(entity?.entity_id || '').trim(),
  }));
  if (normalizedEntities.some((entity) => !/^[a-z0-9_]+\.[a-z0-9_]+$/u.test(entity.entity_id))
    || new Set(normalizedEntities.map((entity) => entity.entity_id)).size
      !== normalizedEntities.length) return null;
  const normalizedReferences = references.map((reference) => ({
    requirementId: String(reference?.requirementId || '').trim(),
    sourceText: String(reference?.sourceText || '').trim(),
  }));
  if (normalizedReferences.some((reference) => !/^r(?:[1-9]|1[0-2])$/u
    .test(reference.requirementId) || !reference.sourceText)
    || new Set(normalizedReferences.map((reference) => reference.requirementId)).size
      !== normalizedReferences.length) return null;
  const bindings = [];
  for (const reference of normalizedReferences) {
    const ranked = normalizedEntities.map((entity) => ({
      entity, score: homeContextReferenceScore(reference, entity),
    })).filter((candidate) => candidate.score > 0)
      .sort((left, right) => right.score - left.score
        || exactEntityOrder(left.entity, right.entity));
    if (!ranked.length || ranked[0].score < 12
      || (ranked[1] && ranked[1].score === ranked[0].score)) return null;
    bindings.push(Object.freeze({
      requirementId: reference.requirementId,
      entityId: ranked[0].entity.entity_id,
    }));
  }
  if (new Set(bindings.map((binding) => binding.entityId)).size !== bindings.length) return null;
  return Object.freeze(bindings);
}

function causalHistorySelectionComplete(query, entities) {
  const text = String(query || '');
  if (!/\b(?:waarom|waardoor|oorzaak|hoe\s+kan\s+dat)\b/iu.test(text)) return true;
  const domains = new Set(entities.map((item) => String(item?.entity_id || '').split('.')[0]));
  const asksLighting = /\b(?:licht|lichten|lamp|lampen|verlichting)\b/iu.test(text);
  const asksMedia = /\b(?:tv|televisie|chromecast|media|speler)\b/iu.test(text);
  const hasLightingCause = domains.has('light') || domains.has('automation') || domains.has('switch');
  return (!asksLighting || hasLightingCause) && (!asksMedia || domains.has('media_player'));
}

function homeOverviewName(state) {
  return boundedDisplayText(state?.attributes?.friendly_name, 72)
    || boundedDisplayText(state?.entity_id, 96) || 'onbekend';
}

function boundedOverviewLine(label, items, formatItem, emptyMessage = '') {
  if (!items.length) return emptyMessage;
  let shown = [];
  for (const item of items.slice(0, HOME_OVERVIEW_ITEM_LIMIT)) {
    const rendered = formatItem(item);
    if (!rendered) continue;
    const trial = [...shown, rendered];
    const omitted = items.length - trial.length;
    const line = `${label}: ${trial.join(', ')}${omitted ? `; ${omitted} niet getoond` : ''}.`;
    if (jsonBytes(line) > HOME_OVERVIEW_GROUP_MAX_BYTES) break;
    shown = trial;
  }
  const omitted = items.length - shown.length;
  return `${label}: ${shown.join(', ') || 'geen'}${omitted ? `; ${omitted} niet getoond` : ''}.`;
}

function compactHomeOverview(states) {
  const validEntity = (state, prefix) => typeof state?.entity_id === 'string'
    && state.entity_id.startsWith(prefix);
  const people = states.filter((state) => validEntity(state, 'person.'));
  const lights = states.filter((state) => validEntity(state, 'light.') && state.state === 'on');
  const openings = states.filter((state) => openingState(state) && state.state === 'on');
  const unknownOpenings = states.filter((state) => openingState(state) && unknownState(state));
  const temperatures = states.filter((state) => validEntity(state, 'climate.')
    && finiteScalarNumber(state.attributes?.current_temperature) !== null);
  const presence = boundedOverviewLine('Aanwezigheid', people, (state) => {
    const value = completeScalarText(state.state, 32);
    return `${homeOverviewName(state)}: ${value || 'niet bevestigd'}`;
  });
  const temperature = boundedOverviewLine('Kamertemperaturen', temperatures, (state) => {
    const value = finiteScalarNumber(state.attributes?.current_temperature);
    const unit = completeScalarText(state.attributes?.temperature_unit, 24);
    return value === null ? null
      : `${homeOverviewName(state)} ${value}${unit ? ` ${unit}` : ''}`;
  });
  return [
    presence,
    boundedOverviewLine('Lampen aan', lights, homeOverviewName,
      'Er staan geen lampen als aan geregistreerd.'),
    boundedOverviewLine('Open', openings, homeOverviewName,
      'Er staan geen deuren of ramen als open geregistreerd.'),
    boundedOverviewLine(`Openingstatus niet bevestigd (${unknownOpenings.length})`,
      unknownOpenings, homeOverviewName),
    temperature,
  ].filter(Boolean).join('\n');
}

function homeContextCandidateEvidenceRecord(entity) {
  const name = entity.name || entity.entity_id;
  const readings = [];
  const stateUnit = entity.entity_id.startsWith('sensor.')
    && finiteScalarNumber(entity.state) !== null ? entity.unit : null;
  if (entity.state) readings.push(`status ${entity.state}${stateUnit ? ` ${stateUnit}` : ''}`);
  for (const [field, label] of [
    ['current_temperature', 'huidige temperatuur'],
    ['temperature', 'temperatuur'],
    ['target_temperature', 'doeltemperatuur'],
    ['lower_limit', 'ondergrens'],
    ['upper_limit', 'bovengrens'],
  ]) {
    const value = entity[field];
    if (typeof value === 'number' && Number.isFinite(value)) {
      readings.push(`${label} ${value}${entity.temperature_unit ? ` ${entity.temperature_unit}` : ''}`);
    }
  }
  return `${name} (${entity.entity_id}): ${readings.join(', ') || 'waarde niet bevestigd'}`;
}

function homeContextCandidateEvidence(matches, coverage = null) {
  const semanticOmitted = Number(coverage?.omitted_candidates || 0);
  const coverageSuffix = semanticOmitted
    ? ` ${semanticOmitted} overige semantische kandidaat${semanticOmitted === 1 ? '' : 'en'} niet getoond.`
    : '';
  if (!matches.length) return semanticOmitted
    ? `Geen kandidaat getoond.${coverageSuffix}`
    : 'Geen actuele Home Assistant-kandidaat voor deze gerichte vraag.';
  const prefix = matches.length === 1
    ? 'Actuele Home Assistant-kandidaat (1):'
    : `Actuele Home Assistant-kandidaten (${matches.length}):`;
  let shown = [];
  for (const entity of matches) {
    const line = homeContextCandidateEvidenceRecord(entity);
    const trial = [...shown, line];
    const omitted = matches.length - trial.length;
    const message = `${prefix} ${trial.join('; ')}${omitted
      ? `; ${omitted} aanvullende complete kandidaat${omitted === 1 ? '' : 'en'} ${omitted === 1 ? 'staat' : 'staan'} in entities.`
      : ''}${coverageSuffix}`;
    if (jsonBytes(message) > HOME_CONTEXT_MESSAGE_MAX_BYTES) break;
    shown = trial;
  }
  const omitted = matches.length - shown.length;
  return `${prefix} ${shown.join('; ')}${omitted
    ? `; ${omitted} aanvullende complete kandidaat${omitted === 1 ? '' : 'en'} ${omitted === 1 ? 'staat' : 'staan'} in entities.`
    : ''}${coverageSuffix}`;
}

function continuityStateRecord(state) {
  const entityId = exactHomeEntityId(state?.entity_id);
  if (!entityId) return null;
  return {
    entity_id: entityId,
    name: boundedDisplayText(state?.attributes?.friendly_name, 80) || entityId,
    state: completeScalarText(state?.state, 32),
  };
}

function continuityGroupLine(label, group) {
  const item = group.items[0] || null;
  const detail = item ? `${item.name}: ${item.state || 'niet bevestigd'}` : 'geen';
  const hidden = group.count - (item ? 1 : 0);
  return `${label}: ${detail}${hidden ? `; ${hidden} niet in samenvatting` : ''}.`;
}

function compactContinuitySnapshot(states) {
  const byEntity = (left, right) => String(left.entity_id).localeCompare(String(right.entity_id));
  const people = states.filter((state) => state.entity_id.startsWith('person.')).sort(byEntity);
  const lights = states.filter((state) => state.entity_id.startsWith('light.')
    && state.state === 'on').sort(byEntity);
  const openings = states.filter((state) => openingState(state) && state.state === 'on').sort(byEntity);
  const openingUnknown = states.filter((state) => openingState(state) && unknownState(state)).sort(byEntity);
  const presenceUnknown = people.filter(unknownState).length;
  const lightsUnknown = states.filter((state) => state.entity_id.startsWith('light.') && unknownState(state)).length;
  const anomalies = states.filter(unknownState).sort((left, right) => {
    const priority = (state) => openingState(state) ? 0
      : state.entity_id.startsWith('person.') ? 1 : state.entity_id.startsWith('light.') ? 2 : 3;
    return priority(left) - priority(right) || byEntity(left, right);
  });
  const sources = [
    ['openings_unknown', openingUnknown],
    ['openings_open', openings],
    ['presence', people],
    ['lights_on', lights],
    ['anomalies', anomalies],
  ].map(([key, items]) => ({ key, items,
    group: { count: items.length, omitted: items.length, items: [] } }));
  let itemBytes = 0;
  for (let index = 0; index < 6; index += 1) {
    for (const source of sources) {
      const record = continuityStateRecord(source.items[index]);
      if (!record) continue;
      const recordBytes = jsonBytes(record) + 1;
      if (itemBytes + recordBytes > HOME_CONTINUITY_ITEMS_MAX_BYTES) continue;
      source.group.items.push(record);
      source.group.omitted -= 1;
      itemBytes += recordBytes;
    }
  }
  const group = Object.fromEntries(sources.map((source) => [source.key, source.group]));
  const returnedRecords = sources.reduce(
    (count, source) => count + source.group.items.length, 0,
  );
  const omittedRecords = sources.reduce(
    (count, source) => count + source.group.omitted, 0,
  );
  const coverage = { complete: omittedRecords === 0,
    returned_records: returnedRecords, omitted_records: omittedRecords };
  const snapshot = {
    complete: coverage.complete, coverage,
    presence: group.presence, lights_on: group.lights_on,
    openings_open: group.openings_open, openings_unknown: group.openings_unknown,
    presence_unknown_count: presenceUnknown, lights_unknown_count: lightsUnknown,
    anomalies: group.anomalies,
  };
  const messageLines = [
    continuityGroupLine('Aanwezigheid', snapshot.presence),
    continuityGroupLine(`Lampen aan (${lights.length})`, snapshot.lights_on),
    continuityGroupLine(`Openingen open (${openings.length})`, snapshot.openings_open),
    continuityGroupLine(`Openingen niet bevestigd (${openingUnknown.length})`,
      snapshot.openings_unknown),
    `Niet bevestigde aanwezigheid: ${presenceUnknown}; lampstatus: ${lightsUnknown}.`,
    continuityGroupLine(`Onbekend of onbeschikbaar (${anomalies.length})`,
      snapshot.anomalies),
    omittedRecords ? `Snapshot begrensd: ${omittedRecords} records niet getoond.` : '',
  ].filter(Boolean);
  const message = messageLines.reduce((accepted, line) => {
    const candidate = [...accepted, line];
    return jsonBytes(candidate.join('\n')) <= HOME_CONTINUITY_MESSAGE_MAX_BYTES
      ? candidate : accepted;
  }, []).join('\n');
  const result = {
    contract: 'HomeCurrentSnapshot/v1', ok: true, verified: true, changed: false,
    complete: coverage.complete,
    verification_scope: 'returned_records_and_group_counts',
    snapshot, entities: [], message,
  };
  if (jsonBytes(result) < HOME_STRUCTURED_MAX_BYTES) return result;
  const boundedGroups = Object.fromEntries(sources.map((source) => [source.key, {
    count: source.group.count, omitted: source.group.count, items: [],
  }]));
  const reason = 'De actuele huismomentopname paste niet volledig binnen het veilige payloadbudget.';
  return {
    contract: 'HomeCurrentSnapshot/v1', ok: false, verified: false, changed: false,
    complete: false, partial: false, needs_followup: true,
    reason, message: reason, entities: [],
    snapshot: {
      complete: false,
      coverage: { complete: false, returned_records: 0,
        omitted_records: sources.reduce((count, source) => count + source.group.count, 0) },
      presence: boundedGroups.presence, lights_on: boundedGroups.lights_on,
      openings_open: boundedGroups.openings_open,
      openings_unknown: boundedGroups.openings_unknown,
      presence_unknown_count: presenceUnknown, lights_unknown_count: lightsUnknown,
      anomalies: boundedGroups.anomalies,
    },
  };
}

function compactHomeWeatherLine(weather) {
  if (!weather) return 'Er is geen actuele weather-entiteit gevonden.';
  const entityId = exactHomeEntityId(weather.entity_id);
  const state = completeScalarText(weather.state, 64);
  const temperature = finiteScalarNumber(weather.attributes?.temperature);
  const unit = completeScalarText(weather.attributes?.temperature_unit, 24);
  const updated = completeScalarText(weather.last_updated || weather.last_changed, 48);
  const parts = [entityId, state,
    temperature === null ? null : `${temperature}${unit ? ` ${unit}` : ''}`,
    updated && Number.isFinite(Date.parse(updated)) ? `bijgewerkt ${updated}` : null,
  ].filter(Boolean);
  return parts.length ? `Weerbron thuis: ${parts.join(', ')}.`
    : 'De actuele weather-entiteit bevatte geen bevestigde scalars.';
}

function structuredHomeContext(message, entities, extra = {}) {
  const payload = {
    ok: true, verified: true, changed: false,
    message, entities, ...extra,
  };
  if (jsonBytes(payload) < HOME_STRUCTURED_MAX_BYTES) return payload;
  const reason = 'De begrensde huiscontext kon niet volledig binnen het veilige payloadbudget worden geprojecteerd.';
  return { ok: false, verified: false, changed: false, complete: false,
    truncated: true, partial: true,
    needs_followup: true, reason, message: reason, entities: [] };
}

export async function homeContextText({
  fresh: _fresh = false,
  query = '',
  includeOverview = true,
  includeWeather = true,
  structured = false,
  compactCurrent = false,
  historyRequested = false,
} = {}) {
  let states = [];
  if (!includeOverview && !includeWeather) {
    states = cachedStates();
    if (!states.length) {
      const known = boundedDisplayText(kvGet(HOME_LOCALITY_KEY, ''), 120);
      const message = known
        ? `Woonplaats: ${known}. Actuele huiswaarden zijn voor deze vrije beurt niet opgevraagd.`
        : 'De woonplaats en actuele huiswaarden zijn voor deze vrije beurt niet opgevraagd.';
      return structured ? { ok: false, verified: false, changed: false,
        reason: message, message, entities: [] } : message;
    }
  } else {
    try {
      // Historisch oorzaakonderzoek mag niet varen op een halfgevulde
      // WebSocket-cache direct na een HA-reconnect; lees daarvoor één verse
      // volledige REST-snapshot.
      states = await allStates({ forceRefresh: historyRequested });
    } catch {
      const message = 'Home Assistant is nu niet bereikbaar; plaats en actuele huiswaarden zijn niet bevestigd.';
      if (compactCurrent) return { contract: 'HomeCurrentSnapshot/v1', ok: false,
        verified: false, changed: false, reason: message, message, entities: [] };
      return structured ? { ok: false, verified: false, changed: false,
        reason: message, message, entities: [] } : message;
    }
  }
  if (compactCurrent && (!Array.isArray(states) || !states.length)) {
    const message = 'Home Assistant leverde geen actuele states; de huiscontext is niet bevestigd.';
    return { contract: 'HomeCurrentSnapshot/v1', ok: false, verified: false,
      changed: false, reason: message, message, entities: [] };
  }
  const locality = homeLocality(states);
  if (locality && locality !== 'Thuis') kvSet(HOME_LOCALITY_KEY, locality);
  if (compactCurrent) return compactContinuitySnapshot(states);
  const overview = compactHomeOverview(states);
  const weather = states.find((state) => state.entity_id.startsWith('weather.'));
  const homeZone = boundedDisplayText(
    states.find((state) => state.entity_id === 'zone.home')?.attributes?.friendly_name,
    96,
  ) || 'Thuis';
  const safeLocality = boundedDisplayText(locality, 120) || 'Thuis';
  const locationLine = `Woonplaats: ${safeLocality}. Home Assistant-zone: ${homeZone}.`;
  const weatherLine = includeWeather ? compactHomeWeatherLine(weather) : '';
  if (!query && !historyRequested) {
    const broadMessage = [
      locationLine, weatherLine, includeOverview ? overview : '',
    ].filter(Boolean).join('\n');
    return structured ? structuredHomeContext(broadMessage, []) : broadMessage;
  }
  const selected = selectHomeContextEntities(states, query, historyRequested);
  const matches = selected.entities;
  if (historyRequested && !causalHistorySelectionComplete(query, matches)) {
    const reason = 'Home Assistant is net opnieuw verbonden; de relevante huisgegevens zijn nog niet volledig beschikbaar.';
    const incomplete = { ok: false, verified: false, changed: false,
      history_snapshot_incomplete: true, reason, message: reason, entities: [] };
    return structured ? incomplete : reason;
  }
  const message = [
    locationLine,
    weatherLine,
    includeOverview ? overview : '',
    matches.length ? `Relevante entiteiten: ${JSON.stringify(matches)}` : '',
  ].filter(Boolean).join('\n');
  if (!structured) return message;
  const selectionState = homeContextStructuredSelectionState(selected);
  return structuredHomeContext(
    homeContextCandidateEvidence(matches, selected.selectionCoverage), matches,
    { ...selectionState,
      ...(historyRequested ? { history_selection: selected.selection } : {}) },
  );
}

function entityScore(state, target) {
  const targetNormalized = normalize(target);
  const label = normalize(`${state.entity_id} ${friendlyName(state)}`);
  const targetWords = words(target);
  const candidateWords = words(label);
  const objectId = String(state.entity_id.split('.')[1] || '').toLowerCase();
  let score = 0;
  if (state.entity_id.toLowerCase() === String(target || '').toLowerCase()) score += 1000;
  if (targetWords.length && targetWords.join('_') === objectId) score += 300;
  if (targetNormalized && label.includes(targetNormalized)) score += 120;
  for (const targetWord of targetWords) {
    if (candidateWords.includes(targetWord)) score += 24;
    else if (candidateWords.some((candidate) => relatedWord(targetWord, candidate))) score += 9;
  }
  const domain = state.entity_id.split('.')[0];
  if (targetWords.some((word) => ['licht', 'lichten', 'lamp', 'lampen', 'verlichting'].includes(word))) {
    score += domain === 'light' ? 30 : -20;
  }
  if (targetWords.some((word) => ['gordijn', 'gordijnen', 'rolluik'].includes(word))) {
    score += domain === 'cover' ? 30 : -20;
  }
  if (/\b(?:alle|alles)\b/i.test(target) && domain === 'light' && state.entity_id.split('_').length <= 2) score += 8;
  if (['unavailable', 'unknown'].includes(state.state)) score -= 10;
  return score;
}

function semanticWordMatch(left, right) {
  if (relatedWord(left, right)) return true;
  return left.length >= 4 && right.length >= 4
    && (left.includes(right) || right.includes(left));
}

function entityIdentityWords(state) {
  return [...new Set(words(`${state?.entity_id || ''} ${friendlyName(state)}`))];
}

function personName(value) {
  return String(typeof value === 'string' ? value : value?.name || '').trim();
}

function samePerson(left, right) {
  const a = normalize(left), b = normalize(right);
  if (!a || !b) return false;
  if (a === b) return true;
  const leftWords = words(a), rightWords = words(b);
  return leftWords.length > 0 && leftWords.every((word) => rightWords.includes(word))
    || rightWords.length > 0 && rightWords.every((word) => leftWords.includes(word));
}

function requesterIdentityWords(states, requester) {
  const own = new Set(words(requester));
  const presenceMatches = states.filter((state) => String(state?.entity_id || '').startsWith('person.'))
    .filter((state) => samePerson(requester, `${state.entity_id.split('.')[1] || ''} ${friendlyName(state)}`));
  for (const state of presenceMatches) {
    for (const word of entityIdentityWords(state)) own.add(word);
  }
  return [...own].filter((word) => word.length >= 3);
}

function otherHouseholdIdentityWords(knownPeople, requester) {
  return [...new Set((Array.isArray(knownPeople) ? knownPeople : [])
    .map(personName).filter((name) => name && !samePerson(name, requester))
    .flatMap((name) => words(name)).filter((word) => word.length >= 3))];
}

function candidateMentionsAny(state, identityWords) {
  const candidateWords = entityIdentityWords(state);
  return identityWords.some((identity) => candidateWords.includes(identity));
}

function requestedHomeDomains(value) {
  const requestWords = words(value);
  if (requestWords.some((word) => ['lamp', 'lampen', 'licht', 'lichten', 'verlichting']
    .includes(word))) return new Set(['light']);
  if (requestWords.some((word) => ['tv', 'televisie'].includes(word))) {
    return new Set(['media_player']);
  }
  if (requestWords.some((word) => ['gordijn', 'gordijnen', 'rolluik'].includes(word))) {
    return new Set(['cover']);
  }
  return null;
}

function requesterBindingPayload({
  requester, requestedTarget, method, resolvedEntityId,
  sourceMemoryIds = [], aliasEvidence = [],
}) {
  return {
    contract: HOME_REQUESTER_TARGET_BINDING_CONTRACT,
    requester: String(requester || '').trim(),
    requested_target: String(requestedTarget || '').trim(),
    method,
    resolved_entity_id: String(resolvedEntityId || '').trim().toLowerCase(),
    source_memory_ids: [...new Set(sourceMemoryIds.map(Number)
      .filter((id) => Number.isSafeInteger(id) && id > 0))].sort((left, right) => left - right),
    alias_evidence: [...new Set(aliasEvidence.map((alias) => normalize(alias))
      .filter((alias) => /^[\p{L}\p{N}][\p{L}\p{N} -]{1,39}$/u.test(alias)))].sort(),
  };
}

function requesterBindingSha256(payload) {
  return createHash('sha256').update(canonicalJson(payload)).digest('hex');
}

function createRequesterTargetBinding(options) {
  const payload = requesterBindingPayload(options);
  return Object.freeze({ ...payload, binding_sha256: requesterBindingSha256(payload) });
}

/** Verifieert de begrensde, door de host gemaakte koppeling tussen bezitstaal en HA-entiteit. */
export function validHomeRequesterTargetBinding(binding, {
  requestedTarget = null, requester = null, resolvedEntityId = null,
} = {}) {
  if (!binding || binding.contract !== HOME_REQUESTER_TARGET_BINDING_CONTRACT
    || !['person_memory', 'requester_identity'].includes(binding.method)
    || typeof binding.requester !== 'string' || !binding.requester.trim()
    || typeof binding.requested_target !== 'string' || !binding.requested_target.trim()
    || !/^[a-z0-9_]+\.[a-z0-9_]+$/u.test(String(binding.resolved_entity_id || ''))
    || !Array.isArray(binding.source_memory_ids) || binding.source_memory_ids.length > 8
    || !Array.isArray(binding.alias_evidence) || !binding.alias_evidence.length
    || binding.alias_evidence.length > 8
    || !/^[a-f0-9]{64}$/u.test(String(binding.binding_sha256 || ''))) return false;
  const payload = requesterBindingPayload({
    requester: binding.requester,
    requestedTarget: binding.requested_target,
    method: binding.method,
    resolvedEntityId: binding.resolved_entity_id,
    sourceMemoryIds: binding.source_memory_ids,
    aliasEvidence: binding.alias_evidence,
  });
  if (canonicalJson(payload.source_memory_ids) !== canonicalJson(binding.source_memory_ids)
    || canonicalJson(payload.alias_evidence) !== canonicalJson(binding.alias_evidence)
    || requesterBindingSha256(payload) !== binding.binding_sha256
    || (binding.method === 'person_memory' && payload.source_memory_ids.length < 1)
    || (binding.method === 'requester_identity' && payload.source_memory_ids.length !== 0)) return false;
  if (requestedTarget != null && String(requestedTarget).trim() !== payload.requested_target) return false;
  if (requester != null && normalize(requester) !== normalize(payload.requester)) return false;
  if (resolvedEntityId != null
    && String(resolvedEntityId).trim().toLowerCase() !== payload.resolved_entity_id) return false;
  return true;
}

function memoryTargetRelation(memoryWords, requestWords) {
  const matches = requestWords.filter((requestWord) => (
    memoryWords.some((memoryWord) => semanticWordMatch(requestWord, memoryWord))
  ));
  return matches.length >= Math.min(2, requestWords.length);
}

function affirmedMemoryWords(content) {
  const tokens = normalize(content).split(' ').filter(Boolean);
  const negatedPositions = new Set();
  for (let index = 0; index < tokens.length; index += 1) {
    if (!['geen', 'niet'].includes(tokens[index])) continue;
    const boundary = tokens.slice(index + 1, index + 17).indexOf('maar');
    if (boundary < 0) continue;
    const butIndex = index + boundary + 1;
    for (let position = index + 1; position < butIndex; position += 1) {
      negatedPositions.add(position);
    }
    index = butIndex;
  }
  return [...new Set(tokens.filter((_token, index) => !negatedPositions.has(index))
    .filter((token) => token.length > 1))];
}

function candidateMemoryEvidence(state, memories, requestWords, identityWords) {
  const candidateWords = entityIdentityWords(state);
  const matchedAliases = new Set();
  const memoryIds = new Set();
  for (const memory of memories) {
    const memoryWords = affirmedMemoryWords(memory?.content || '');
    if (!memoryTargetRelation(memoryWords, requestWords)) continue;
    const aliases = candidateWords.filter((candidateWord) => (
      candidateWord.length >= 3
      && !/^\d+$/u.test(candidateWord)
      && !GENERIC_HOME_ALIAS_WORDS.has(candidateWord)
      && !identityWords.includes(candidateWord)
      && !requestWords.some((requestWord) => semanticWordMatch(candidateWord, requestWord))
      && memoryWords.includes(candidateWord)
    ));
    if (!aliases.length) continue;
    for (const alias of aliases) matchedAliases.add(alias);
    if (Number.isSafeInteger(Number(memory?.id)) && Number(memory.id) > 0) {
      memoryIds.add(Number(memory.id));
    }
  }
  return matchedAliases.size && memoryIds.size ? {
    aliases: [...matchedAliases].sort().slice(0, 8),
    memoryIds: [...memoryIds].sort((left, right) => left - right).slice(0, 8),
  } : null;
}

function requesterOwnedResolution(states, target, {
  requester = null,
  requesterTrusted = false,
  knownPeople = [],
  requesterMemories = [],
  sourceText = '',
} = {}) {
  const evidenceText = `${target || ''} ${sourceText || ''}`.trim();
  const domains = requestedHomeDomains(evidenceText);
  const ranked = states.filter((state) => HOME_DOMAINS.has(String(state?.entity_id || '').split('.')[0]))
    .filter((state) => !domains || domains.has(state.entity_id.split('.')[0]))
    .map((state) => ({ state, score: entityScore(state, evidenceText) }))
    .sort((left, right) => right.score - left.score || exactEntityOrder(left.state, right.state));
  const alternatives = ranked.slice(0, 6).map((item) => ({
    entity_id: item.state.entity_id,
    name: friendlyName(item.state),
    state: item.state.state,
    score: item.score,
  }));
  const exactRequester = String(requester || '').trim();
  if (requesterTrusted !== true || !exactRequester) {
    return { state: null, alternatives, ambiguous: true };
  }
  const requesterWords = requesterIdentityWords(states, exactRequester);
  const otherWords = otherHouseholdIdentityWords(knownPeople, exactRequester);
  const candidates = ranked.filter(({ state }) => !candidateMentionsAny(state, otherWords));
  const direct = candidates.filter(({ state }) => candidateMentionsAny(state, requesterWords));
  if (direct.length === 1) {
    const selected = direct[0].state;
    return {
      state: selected, alternatives, ambiguous: false,
      targetBinding: createRequesterTargetBinding({
        requester: exactRequester,
        requestedTarget: target,
        method: 'requester_identity',
        resolvedEntityId: selected.entity_id,
        aliasEvidence: entityIdentityWords(selected).filter((word) => requesterWords.includes(word)).slice(0, 8),
      }),
    };
  }
  if (direct.length > 1) return { state: null, alternatives, ambiguous: true };
  const requestWords = [...new Set(words(evidenceText))];
  const memories = (Array.isArray(requesterMemories) ? requesterMemories : [])
    .filter((memory) => samePerson(memory?.person, exactRequester)).slice(0, 20);
  const identityWords = [...new Set([...requesterWords, ...otherWords])];
  const memoryMatches = candidates.map(({ state }) => ({
    state,
    evidence: candidateMemoryEvidence(state, memories, requestWords, identityWords),
  })).filter((candidate) => candidate.evidence);
  if (memoryMatches.length !== 1) return { state: null, alternatives, ambiguous: true };
  const selected = memoryMatches[0];
  return {
    state: selected.state, alternatives, ambiguous: false,
    targetBinding: createRequesterTargetBinding({
      requester: exactRequester,
      requestedTarget: target,
      method: 'person_memory',
      resolvedEntityId: selected.state.entity_id,
      sourceMemoryIds: selected.evidence.memoryIds,
      aliasEvidence: selected.evidence.aliases,
    }),
  };
}

export function resolveHomeEntity(states, target, options = {}) {
  const direct = states.find((state) => state.entity_id.toLowerCase() === String(target || '').toLowerCase());
  if (direct) return { state: direct, alternatives: [], ambiguous: false };
  if (FIRST_PERSON_POSSESSIVE.test(String(target || ''))) {
    return requesterOwnedResolution(states, target, options);
  }
  const ranked = states
    .filter((state) => HOME_DOMAINS.has(state.entity_id.split('.')[0]))
    .map((state) => ({ state, score: entityScore(state, target) }))
    .filter((item) => item.score > 8)
    .sort((left, right) => right.score - left.score);
  const first = ranked[0];
  const second = ranked[1];
  if (!first) return { state: null, alternatives: [], ambiguous: false };
  // Een hoge score maakt een gelijkspel niet minder dubbelzinnig. Alleen een
  // duidelijke winnaar (bijvoorbeeld een exacte entity-id/slug) mag handelen.
  const ambiguous = !!second && second.score >= first.score - 3;
  return {
    state: ambiguous ? null : first.state,
    ambiguous,
    alternatives: ranked.slice(0, 6).map((item) => ({
      entity_id: item.state.entity_id,
      name: friendlyName(item.state),
      state: item.state.state,
      score: item.score,
    })),
  };
}

function actionFor(domain, action, value) {
  const normalized = normalize(action);
  if (['aan', 'on', 'inschakelen', 'start', 'activeer'].includes(normalized)) {
    return { service: 'turn_on', expected: 'on', data: {} };
  }
  if (['uit', 'off', 'uitschakelen', 'stop'].includes(normalized)) {
    return { service: 'turn_off', expected: 'off', data: {} };
  }
  if (['toggle', 'wissel'].includes(normalized)) return { service: 'toggle', expected: null, data: {} };
  if (['open', 'openen'].includes(normalized) && domain === 'cover') {
    return { service: 'open_cover', expected: 'open', data: {} };
  }
  if (['dicht', 'sluit', 'sluiten'].includes(normalized) && domain === 'cover') {
    return { service: 'close_cover', expected: 'closed', data: {} };
  }
  if (['pauze', 'pauzeren'].includes(normalized) && domain === 'media_player') {
    return { service: 'media_pause', expected: 'paused', data: {} };
  }
  if (['speel', 'hervat'].includes(normalized) && domain === 'media_player') {
    return { service: 'media_play', expected: 'playing', data: {} };
  }
  if (['temperatuur', 'set temperature'].includes(normalized)
    && domain === 'climate' && Number.isFinite(Number(value))) {
    return { service: 'set_temperature', expected: null, data: { temperature: Number(value) } };
  }
  return null;
}

function editSimilarity(left, right) {
  const a = normalize(left).replaceAll(' ', '');
  const b = normalize(right).replaceAll(' ', '');
  if (!a || !b) return 0;
  const previous = Array.from({ length: b.length + 1 }, (_item, index) => index);
  for (let row = 1; row <= a.length; row += 1) {
    const current = [row];
    for (let column = 1; column <= b.length; column += 1) {
      current[column] = Math.min(
        current[column - 1] + 1,
        previous[column] + 1,
        previous[column - 1] + (a[row - 1] === b[column - 1] ? 0 : 1),
      );
    }
    previous.splice(0, previous.length, ...current);
  }
  return 1 - previous[b.length] / Math.max(a.length, b.length);
}

function spokenTokenSimilarity(left, right) {
  if (relatedWord(left, right)) return 1;
  if (Math.min(left.length, right.length) < 4) return 0;
  let prefix = 0;
  while (left[prefix] === right[prefix] && prefix < Math.min(left.length, right.length)) prefix += 1;
  const consonants = (value) => value.replace(/[aeiouy]/gu, '');
  return Math.max(
    editSimilarity(left, right),
    consonants(left).length > 2 && consonants(right).length > 2
      ? editSimilarity(consonants(left), consonants(right)) : 0,
    prefix >= 4 ? prefix / Math.min(left.length, right.length) : 0,
  );
}

function fuzzyHomeCandidate(state, queryTokens, action) {
  const domain = String(state?.entity_id || '').split('.')[0];
  if (!HOME_DOMAINS.has(domain) || domain === 'vacuum' || !actionFor(domain, action)) return null;
  const objectId = String(state.entity_id || '').split('.')[1] || '';
  const candidateTokens = [...new Set(words(`${friendlyName(state)} ${objectId}`))];
  const pairs = queryTokens.flatMap((query, queryIndex) => candidateTokens.map((candidate, candidateIndex) => ({
    queryIndex, candidateIndex, similarity: spokenTokenSimilarity(query, candidate),
  }))).sort((left, right) => right.similarity - left.similarity);
  const usedQueries = new Set();
  const usedCandidates = new Set();
  const matches = [];
  for (const pair of pairs) {
    if (pair.similarity < 0.56 || usedQueries.has(pair.queryIndex)
      || usedCandidates.has(pair.candidateIndex)) continue;
    usedQueries.add(pair.queryIndex);
    usedCandidates.add(pair.candidateIndex);
    matches.push(pair);
  }
  const coverage = queryTokens.length ? matches.length / queryTokens.length : 0;
  const average = matches.length
    ? matches.reduce((sum, pair) => sum + pair.similarity, 0) / matches.length : 0;
  if (matches.length < 2 || coverage < 0.65 || average < 0.64) return null;
  return {
    entityId: state.entity_id,
    name: friendlyName(state),
    state: state.state,
    score: average + coverage / 10,
    confidence: Math.min(0.99, average),
  };
}

function homeClarification(candidates = []) {
  const names = [...new Set(candidates.map((item) => item?.name || friendlyName(item)).filter(Boolean))];
  if (!names.length) return 'Welk apparaat bedoel je precies?';
  if (names.length === 1 && candidates.length > 1) {
    return `Welke van de apparaten met de naam ${names[0]} bedoel je?`;
  }
  if (names.length === 1) return `Bedoel je ${names[0]}?`;
  return `Bedoel je ${names.slice(0, 2).join(' of ')}?`;
}

/**
 * Grondt een al herkende enkelvoudige voice-huisopdracht aan de actuele
 * entitycatalogus. Fonetische overeenkomst mag alleen winnen met minstens
 * twee onafhankelijke woorden en een duidelijke winnaar.
 */
export function resolveContextualHomeCommand(states, command) {
  if (!command?.doel || !command?.actie || !Array.isArray(states)) return null;
  const direct = resolveHomeEntity(states, command.doel);
  if (direct.state?.entity_id?.startsWith('vacuum.')) return null;
  if (direct.state && direct.state.entity_id.split('.')[0] !== 'vacuum'
    && actionFor(direct.state.entity_id.split('.')[0], command.actie)) {
    return {
      contract: VOICE_HOME_GROUNDING_CONTRACT,
      status: 'resolved',
      resolution: 'lexical',
      originalTarget: command.doel,
      action: command.actie,
      entityId: direct.state.entity_id,
      name: friendlyName(direct.state),
      confidence: 1,
    };
  }
  const queryTokens = words(command.doel);
  const ranked = states.map((state) => fuzzyHomeCandidate(state, queryTokens, command.actie))
    .filter(Boolean).sort((left, right) => right.score - left.score);
  if (!ranked.length) {
    return {
      contract: VOICE_HOME_GROUNDING_CONTRACT,
      status: 'ambiguous',
      originalTarget: command.doel,
      action: command.actie,
      question: homeClarification(direct.alternatives),
      candidates: direct.alternatives,
    };
  }
  const uniqueWinner = !ranked[1] || ranked[1].score < ranked[0].score - 0.08;
  if (!uniqueWinner) {
    return {
      contract: VOICE_HOME_GROUNDING_CONTRACT,
      status: 'ambiguous',
      originalTarget: command.doel,
      action: command.actie,
      question: homeClarification(ranked.slice(0, 3)),
      candidates: ranked.slice(0, 3),
    };
  }
  const winner = ranked[0];
  return {
    contract: VOICE_HOME_GROUNDING_CONTRACT,
    status: 'resolved',
    resolution: 'contextual_phonetic',
    originalTarget: command.doel,
    action: command.actie,
    entityId: winner.entityId,
    name: winner.name,
    confidence: winner.confidence,
  };
}

export async function groundContextualHomeCommand(command, {
  states = null,
  loadStates = allStates,
  isCurrent = () => true,
} = {}) {
  if (!command) return null;
  if (!isCurrent()) return { contract: VOICE_HOME_GROUNDING_CONTRACT, status: 'stale' };
  let snapshot;
  try {
    snapshot = Array.isArray(states) ? states : await loadStates();
  } catch {
    return {
      contract: VOICE_HOME_GROUNDING_CONTRACT,
      status: 'unavailable',
      question: 'Welk apparaat bedoel je precies?',
    };
  }
  if (!isCurrent()) return { contract: VOICE_HOME_GROUNDING_CONTRACT, status: 'stale' };
  return resolveContextualHomeCommand(snapshot, command);
}

export function homeOperationOutcome(expected, after, verification) {
  if (expected == null) {
    const verified = after != null;
    return { ok: verified, verified, partial: false, needsFollowup: !verified };
  }
  const complete = verification?.ok === true;
  const reachable = verification?.reachableVerified === true;
  const partial = !complete && reachable
    && Array.isArray(verification?.unavailable) && verification.unavailable.length > 0;
  const verified = complete || reachable;
  return { ok: verified, verified, partial, needsFollowup: partial || !verified };
}

function homeOperationMessage(name, domain, expected, after, verification, outcome) {
  const state = expected === 'on' ? 'aan' : expected === 'off' ? 'uit' : after?.state || 'zoals gevraagd';
  if (!outcome.partial) {
    return outcome.verified
      ? `${name} staat ${state}.`
      : `${name} is aangestuurd, maar de gevraagde toestand is nog niet bevestigd.`;
  }
  const unavailable = Array.isArray(verification?.unavailable) ? verification.unavailable : [];
  const reachable = Array.isArray(verification?.reachable) ? verification.reachable : [];
  const noun = domain === 'light'
    ? (unavailable.length === 1 ? 'lamp' : 'lampen')
    : (unavailable.length === 1 ? 'onderdeel' : 'onderdelen');
  const names = unavailable.slice(0, 4).map((item) => item.name || item.entity_id).filter(Boolean);
  return `${name} staat ${state} op alle ${reachable.length} bereikbare onderdelen. ${unavailable.length} ${noun} ${unavailable.length === 1 ? 'is' : 'zijn'} offline${names.length ? `: ${names.join(', ')}` : ''}.`;
}

function homeRuntime(dependencies) {
  return {
    readState: dependencies.getState || getState,
    readStates: dependencies.allStates || allStates,
    invokeService: dependencies.callService || callService,
    verifyState: dependencies.verifyEntityState || verifyEntityState,
  };
}

async function targetStates(target, runtime) {
  const value = String(target || '').trim();
  const exactEntityId = /^[a-z_]+\.[a-z0-9_]+$/i.test(value) ? value : null;
  if (!exactEntityId) return runtime.readStates();
  const exactState = await runtime.readState(exactEntityId);
  return exactState ? [exactState] : [];
}

async function alreadyExpectedResult(entity, domain, operation, verifyState) {
  if (!operation.expected || entity.state !== operation.expected) return null;
  const already = await verifyState(entity.entity_id, operation.expected, {
    timeoutMs: 2_000,
    pollMs: 250,
  });
  if (!already?.ok && !already?.reachableVerified) return null;
  const outcome = homeOperationOutcome(operation.expected, {
    entity_id: entity.entity_id,
    state: already.state,
  }, already);
  return {
    ok: outcome.ok,
    changed: false,
    verified: outcome.verified,
    partial: outcome.partial,
    needs_followup: outcome.needsFollowup,
    entity_id: entity.entity_id,
    name: friendlyName(entity),
    before: entity.state,
    after: already.state,
    evidence: already,
    message: outcome.partial
      ? homeOperationMessage(friendlyName(entity), domain, operation.expected, { state: already.state }, already, outcome)
      : `${friendlyName(entity)} stond al ${operation.expected === 'on' ? 'aan' : 'uit'}.`,
  };
}

function serviceFailure(entity, expected, error, verification = null) {
  return {
    ok: false,
    changed: false,
    verified: false,
    entity_id: entity.entity_id,
    name: friendlyName(entity),
    before: entity.state,
    expected,
    reason: error?.message || String(error),
    ...(verification ? { evidence: { assessment: verification } } : {}),
  };
}

async function readOperationAfter(entity, expected, verification, readState) {
  if (expected != null && (verification?.ok || verification?.reachableVerified)) {
    return {
      entity_id: entity.entity_id,
      state: verification.state,
      attributes: entity.attributes,
    };
  }
  return readState(entity.entity_id, {
    timeoutMs: 1_500,
    preferCache: false,
  }).catch(() => null);
}

function completedHomeResult(entity, domain, operation, expected, after, verification) {
  const outcome = homeOperationOutcome(expected, after, verification);
  return {
    ok: outcome.ok,
    changed: entity.state !== after?.state,
    verified: outcome.verified,
    partial: outcome.partial,
    needs_followup: outcome.needsFollowup,
    entity_id: entity.entity_id,
    name: friendlyName(entity),
    service: `${domain}.${operation.service}`,
    before: entity.state,
    after: after?.state ?? null,
    evidence: {
      readback: after ? compactHomeState(after) : null,
      assessment: verification,
    },
    message: homeOperationMessage(
      friendlyName(entity),
      domain,
      expected,
      after,
      verification,
      outcome,
    ),
  };
}

async function executeHomeOperation(entity, domain, operation, runtime) {
  const payload = { entity_id: entity.entity_id, ...operation.data };
  const expected = operation.service === 'toggle' && ['on', 'off'].includes(entity.state)
    ? (entity.state === 'on' ? 'off' : 'on')
    : operation.expected;
  const serviceCall = runtime.invokeService(domain, operation.service, payload)
    .then((result) => ({ result, error: null }))
    .catch((error) => ({ result: null, error }));
  if (expected == null) {
    const service = await serviceCall;
    if (service.error) return serviceFailure(entity, null, service.error);
  }
  const verification = await runtime.verifyState(entity.entity_id, expected, {
    timeoutMs: 3_500,
    pollMs: 300,
  });
  if (expected != null && !verification?.ok && !verification?.reachableVerified) {
    const service = await serviceCall;
    if (service.error) return serviceFailure(entity, expected, service.error, verification);
  }
  const after = await readOperationAfter(entity, expected, verification, runtime.readState);
  return completedHomeResult(entity, domain, operation, expected, after, verification);
}

/** Resolveert, bedient en leest één huisentiteit geverifieerd terug. */
export async function operateHome({
  target,
  action,
  value = null,
  isCurrent = () => true,
  requester = null,
  requesterTrusted = false,
  knownPeople = [],
  requesterMemories = [],
  sourceText = '',
}, dependencies = {}) {
  const runtime = homeRuntime(dependencies);
  const resolved = resolveHomeEntity(await targetStates(target, runtime), target, {
    requester, requesterTrusted, knownPeople, requesterMemories, sourceText,
  });
  const withTargetBinding = (result) => resolved.targetBinding
    ? { ...result, target_binding: resolved.targetBinding }
    : result;
  if (!resolved.state) {
    return {
      ok: false,
      changed: false,
      reason: resolved.ambiguous ? 'doel is dubbelzinnig' : 'geen passend Home Assistant-doel gevonden',
      alternatives: resolved.alternatives,
    };
  }
  const entity = resolved.state;
  const domain = entity.entity_id.split('.')[0];
  if (domain === 'vacuum') {
    return {
      ok: false,
      changed: false,
      reason: 'stofzuigers gebruiken eigen start/pauze/stop/dock- en camerabetekenis; gebruik gebruik_robot',
      entity: compactHomeState(entity),
    };
  }
  const operation = actionFor(domain, action, value);
  if (!operation) {
    return {
      ok: false,
      changed: false,
      reason: `actie "${action}" past niet veilig bij ${domain}`,
      entity: compactHomeState(entity),
    };
  }
  const already = await alreadyExpectedResult(entity, domain, operation, runtime.verifyState);
  if (already) return withTargetBinding(already);
  // Resolutie en een verse uitlezing kunnen netwerk-I/O doen. Controleer vlak
  // vóór de muterende servicegrens nogmaals of deze voicebeurt actueel is.
  if (!isCurrent()) {
    return { ok: false, changed: false, stale: true, reason: 'voicebeurt is ingehaald' };
  }
  return withTargetBinding(await executeHomeOperation(entity, domain, operation, runtime));
}
