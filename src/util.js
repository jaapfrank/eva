export const isoNow = () => new Date().toISOString();

export const clamp = (v, min = 0, max = 1) => Math.min(max, Math.max(min, v));

/**
 * Canonieke laatste grens voor tekst die Eva aan bewoners laat zien of horen.
 * Lange gedachtestreepjes maken haar antwoorden onnodig geschreven en
 * sjabloonachtig. Getallenbereiken houden een gewoon koppelteken; een
 * zinsbreuk wordt gewone Nederlandse leestekening.
 */
export function normalizeUserFacingText(value) {
  const enDash = String.fromCodePoint(0x2013);
  const emDash = String.fromCodePoint(0x2014);
  const minusSign = String.fromCodePoint(0x2212);
  const longDash = `[${enDash}${emDash}]`;
  let text = String(value ?? '');
  text = text.replaceAll(minusSign, '-');
  text = text.replace(new RegExp(
    `(\\p{N})[ \\t]*${longDash}[ \\t]*(\\p{N})`, 'gu',
  ), '$1-$2');
  text = text.replace(new RegExp(
    `([\\p{L}\\p{N}])${enDash}(?=[\\p{L}\\p{N}])`, 'gu',
  ), '$1-');
  text = text.replace(new RegExp(`[ \\t]*${longDash}[ \\t]*`, 'gu'), (match, offset, source) => {
    const before = source.slice(0, offset).match(/[^\s]$/u)?.[0] || '';
    const after = source.slice(offset + match.length).match(/^[^\s]/u)?.[0] || '';
    if (!before || !after) return '';
    if (/[.!?;:,]/u.test(before)) return ' ';
    if (/[.!?;:,]/u.test(after)) return '';
    return ', ';
  });
  return text.replace(/[ \t]{2,}/gu, ' ');
}

const DUTCH_CONTENT_WORD_STOP = new Set([
  'de', 'het', 'een', 'van', 'in', 'op', 'bij', 'naar', 'voor', 'met',
  'zet', 'doe', 'maak', 'schakel', 'graag', 'even', 'nu', 'aan', 'uit',
  'open', 'dicht', 'alle', 'alles', 'mijn', 'ons', 'onze',
]);

/** Accent-, separator- en leestekenongevoelige sleutel voor vrije Nederlandse tekst. */
export function normalizeDutchText(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/[_./-]+/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Betekenisdragende woorden met dezelfde canonieke normalisatie. */
export function dutchContentWords(value) {
  return normalizeDutchText(value).split(' ')
    .filter((word) => word.length > 1 && !DUTCH_CONTENT_WORD_STOP.has(word));
}

/** Deterministische JSON-weergave voor duurzame semantische sleutels. */
export function canonicalJson(value) {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item) ?? 'null').join(',')}]`;
  }
  return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

const schemaTypeMatches = (type, value) => type === 'null' ? value === null
  : type === 'array' ? Array.isArray(value)
    : type === 'object' ? value !== null && typeof value === 'object' && !Array.isArray(value)
      : type === 'integer' ? Number.isInteger(value)
        : type === 'number' ? typeof value === 'number' && Number.isFinite(value) : typeof value === type;

/** Geeft de eerste JSON-schemaovertreding, of null. */
export function jsonSchemaViolation(schema, value, pathName = 'value') {
  if (!schema || typeof schema !== 'object') return `${pathName}: schema ontbreekt`;
  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) return `${pathName}: waarde buiten enum`;
  const types = (Array.isArray(schema.type) ? schema.type : [schema.type]).filter(Boolean);
  if (!types.some((type) => schemaTypeMatches(type, value))) return `${pathName}: ongeldig type`;
  if (typeof value === 'string') {
    if (schema.minLength != null && value.length < schema.minLength) return `${pathName}: korter dan minLength`;
    if (schema.maxLength != null && value.length > schema.maxLength) return `${pathName}: langer dan maxLength`;
    if (schema.pattern && !new RegExp(schema.pattern, 'u').test(value)) return `${pathName}: patroon wijkt af`;
  }
  if (typeof value === 'number') {
    if (schema.minimum != null && value < schema.minimum) return `${pathName}: lager dan minimum`;
    if (schema.maximum != null && value > schema.maximum) return `${pathName}: hoger dan maximum`;
    if (schema.exclusiveMinimum != null && value <= schema.exclusiveMinimum) return `${pathName}: niet boven exclusiveMinimum`;
    if (schema.exclusiveMaximum != null && value >= schema.exclusiveMaximum) return `${pathName}: niet onder exclusiveMaximum`;
  }
  if (Array.isArray(value)) {
    if (schema.minItems != null && value.length < schema.minItems) return `${pathName}: minder dan minItems`;
    if (schema.maxItems != null && value.length > schema.maxItems) return `${pathName}: meer dan maxItems`;
    for (let index = 0; schema.items && index < value.length; index += 1) {
      const violation = jsonSchemaViolation(schema.items, value[index], `${pathName}[${index}]`);
      if (violation) return violation;
    }
  } else if (value && typeof value === 'object') {
    const properties = schema.properties || {};
    const missing = (schema.required || []).find((name) => !Object.hasOwn(value, name));
    if (missing) return `${pathName}: ontbrekend veld ${missing}`;
    for (const key of Object.keys(value)) {
      const childSchema = properties[key] || (schema.additionalProperties && schema.additionalProperties !== true
        ? schema.additionalProperties : null);
      if (!childSchema && schema.additionalProperties === false) return `${pathName}: onbekend veld ${key}`;
      const violation = childSchema ? jsonSchemaViolation(childSchema, value[key], `${pathName}.${key}`) : null;
      if (violation) return violation;
    }
  }
  return null;
}

/** Woordoverlap (0..1) tussen twee teksten — getallen/leestekens genegeerd.
 *  Vangt "bijna hetzelfde bericht" (zoals herhaalde meldingen met andere cijfers). */
export function tekstOverlap(a, b) {
  const woorden = (s) =>
    new Set(
      String(s || '')
        .toLowerCase()
        .replace(/[0-9]+/g, ' ')
        .replace(/[^\p{L} ]/gu, ' ')
        .split(/\s+/)
        .filter((w) => w.length > 2)
    );
  const A = woorden(a);
  const B = woorden(b);
  if (!A.size || !B.size) return 0;
  let gedeeld = 0;
  for (const w of A) if (B.has(w)) gedeeld++;
  return gedeeld / Math.min(A.size, B.size);
}

export function parseHHMM(str) {
  const [h, m] = String(str).split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** Is `date` binnen het slaapvenster [sleepStart, wakeTime)? Venster mag over middernacht lopen. */
export function isNight(date, sleepStart, wakeTime) {
  const mins = date.getHours() * 60 + date.getMinutes();
  const start = parseHHMM(sleepStart);
  const end = parseHHMM(wakeTime);
  if (start <= end) return mins >= start && mins < end;
  return mins >= start || mins < end;
}

const DAGEN = ['zondag', 'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag'];
const MAANDEN = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december'];

export function fmtDutch(date = new Date()) {
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${DAGEN[date.getDay()]} ${date.getDate()} ${MAANDEN[date.getMonth()]} ${date.getFullYear()}, ${hh}:${mm}`;
}

/** Probeert een fout leesbaar samen te vatten zonder stacktrace-ruis. */
export const errMsg = (e) => (e && (e.message || String(e))) || 'onbekende fout';

/** Een receipt is pas voltooid wanneer bewijs én resultaat volledig zijn.
 * `ok`/`verified` alleen zijn onvoldoende: begrensde of vervolgbehoevende
 * uitvoer mag nooit een succes- of klaarclaim vrijgeven. */
export function verifiedCompleteReceipt(value) {
  return value?.ok === true && value?.verified === true
    && value?.partial !== true && value?.needs_followup !== true;
}

const clipped = (value, limit) => String(value ?? '').slice(0, limit);

function compactMonitoringWorkState(stateJson) {
  if (typeof stateJson !== 'string' || stateJson.length > 120_000) return undefined;
  let state;
  try { state = JSON.parse(stateJson); } catch { return undefined; }
  const monitoring = state?.monitoring;
  if (!monitoring || typeof monitoring !== 'object' || Array.isArray(monitoring)
    || monitoring.contract !== 'MonitoringContract/v1') return undefined;
  const projected = { contract: 'MonitoringContract/v1' };
  if (/^[a-f0-9]{64}$/u.test(String(monitoring.sourceUnderstandingSha256 || ''))) {
    projected.sourceUnderstandingSha256 = monitoring.sourceUnderstandingSha256;
  }
  for (const field of ['createdAt', 'endsAt']) {
    if (monitoring[field] == null && field === 'endsAt') projected[field] = null;
    else if (monitoring[field]) projected[field] = clipped(monitoring[field], 80);
  }
  if (Number.isFinite(Number(monitoring.cadenceMs))) {
    projected.cadenceMs = Number(monitoring.cadenceMs);
  }
  if (Array.isArray(monitoring.readBindings)) {
    projected.readBindings = monitoring.readBindings.slice(0, 8).flatMap((binding) => {
      const entityId = clipped(binding?.entityId, 180).trim();
      const requirementId = clipped(binding?.requirementId, 20).trim();
      if (!entityId || !requirementId) return [];
      return [{ entityId, requirementId,
        ...(binding?.name ? { name: clipped(binding.name, 120) } : {}) }];
    });
  }
  return JSON.stringify({ monitoring: projected });
}

function compactWorkReceiptData(value, { tool = '' } = {}) {
  if (tool !== 'werk_bijhouden' || !value || typeof value !== 'object'
    || Array.isArray(value)) return null;
  const item = value.item;
  const id = Number(item?.id);
  const status = clipped(item?.status, 40).trim();
  if (!Number.isSafeInteger(id) || id < 1 || !status) return null;
  const projectedItem = { id, status };
  for (const [field, limit] of [
    ['source', 40], ['owner', 80], ['title', 240], ['goal', 800],
    ['next_step', 600], ['current_step', 600], ['definition_of_done', 1_200],
    ['evidence', 800], ['last_action', 120], ['result', 800],
    ['created_ts', 80], ['updated_ts', 80],
  ]) {
    if (item[field]) projectedItem[field] = clipped(item[field], Number(limit));
  }
  if (item.next_wake_ts == null) projectedItem.next_wake_ts = null;
  else projectedItem.next_wake_ts = clipped(item.next_wake_ts, 80);
  for (const field of ['priority', 'revision', 'visible_to_family']) {
    if (Number.isFinite(Number(item[field]))) projectedItem[field] = Number(item[field]);
  }
  const stateJson = compactMonitoringWorkState(item.state_json);
  if (stateJson) projectedItem.state_json = stateJson;
  const compact = { compacted: true, item: projectedItem };
  for (const field of ['ok', 'changed', 'proofAccepted', 'selfReviewInstead']) {
    if (typeof value[field] === 'boolean') compact[field] = value[field];
  }
  return compact;
}

/** Compacte, getypeerde weergave van grote tooldata; gedeeld door runtime en audit. */
/** @param {any} parsed @param {any} raw @param {string} serializedData @param {any} [options] */
export function compactActionResultData(parsed, raw, serializedData, options = {}) {
  if (serializedData.length <= 8_000) return parsed ?? String(raw);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { truncated: true, text: clipped(raw, 7_000) };
  }
  const workReceipt = compactWorkReceiptData(parsed, options);
  if (workReceipt) return workReceipt;
  const compact = { truncated: true };
  const text = parsed.text || parsed.message || parsed.info || parsed.stdout;
  if (text) compact.text = clipped(text, 7_000);
  for (const [key, limit] of [['stdout', 4_000], ['stderr', 2_000], ['content_hash', 128],
    ['material_sha256', 128], ['path', 1_000]]) {
    if (parsed[key]) compact[key] = clipped(parsed[key], Number(limit));
  }
  for (const key of ['verified', 'demonstrated', 'searched', 'history_required']) {
    if (typeof parsed[key] === 'boolean') compact[key] = parsed[key];
  }
  for (const key of ['count', 'searchCalls']) {
    if (typeof parsed[key] === 'number') compact[key] = parsed[key];
  }
  if (Number.isInteger(parsed.exit_code)) compact.exit_code = parsed.exit_code;
  if (parsed.receipt_id) compact.receipt_id = parsed.receipt_id;
  if (parsed.artifact_receipts) compact.artifact_receipts = parsed.artifact_receipts;
  if (Array.isArray(parsed.entities)) compact.entities = parsed.entities.slice(0, 24)
    .map(({ entity_id, name, state, updated }) => ({ entity_id, name, state, updated }));
  for (const [key, limit] of [['expected_exit_codes', 20], ['sandbox', 12], ['sources', 20]]) {
    if (Array.isArray(parsed[key])) compact[key] = parsed[key].slice(0, Number(limit));
  }
  if (parsed.capability) compact.capability = Object.fromEntries(
    ['id', 'kind', 'slug', 'name', 'version', 'status']
      .filter((key) => parsed.capability[key] !== undefined)
      .map((key) => [key, parsed.capability[key]]),
  );
  return compact;
}

export function classifyActionFailure(value) {
  const text = String(value || '').toLowerCase();
  if (/time.?out|tijdslimiet|temporar|tijdelijk|429|rate.?limit/.test(text)) return { failure_class: 'transient', retryable: true, effect_uncertain: false };
  if (/permission|bevoegd|autoris|unauthori|forbidden|authentic|api.?key|geen toegang/.test(text)) return { failure_class: 'permission', retryable: false, effect_uncertain: false };
  if (/not found|niet gevonden|bestaat niet|404|\benoent\b|no such file or directory/.test(text)) return { failure_class: 'not_found', retryable: false, effect_uncertain: false };
  if (/invalid|ongeldig|validatie|400/.test(text)) return { failure_class: 'invalid_input', retryable: false, effect_uncertain: false };
  if (/conflict|bestaat al|already exists|409/.test(text)) return { failure_class: 'conflict', retryable: false, effect_uncertain: false };
  if (/unavailable|onbereikbaar|connection refused|econnrefused|niet beschikbaar/.test(text)) return { failure_class: 'unavailable', retryable: true, effect_uncertain: false };
  if (/policy|beleid|niet toegestaan|geblokkeerd/.test(text)) return { failure_class: 'policy', retryable: false, effect_uncertain: false };
  return { failure_class: 'unknown', retryable: false, effect_uncertain: false };
}

export function normalizeActionFailure(value, fallback, { mutating = false } = {}) {
  const source = value && typeof value === 'object' ? value : {};
  const failureClass = typeof source.failure_class === 'string' && source.failure_class.trim()
    ? clipped(source.failure_class, 120) : fallback.failure_class;
  const explicitUncertainty = typeof source.effect_uncertain === 'boolean'
    ? source.effect_uncertain : typeof source.uncertain === 'boolean' ? source.uncertain : null;
  const effectUncertain = explicitUncertainty ?? (mutating ? true : fallback.effect_uncertain);
  const retryable = typeof source.retryable === 'boolean'
    ? source.retryable : effectUncertain || mutating ? false : fallback.retryable;
  return { failure_class: failureClass, retryable, effect_uncertain: effectUncertain };
}

/** Eén normalisatiepad voor ruwe tooluitvoer vóór bewijs, audit en antwoord. */
/** @param {any} [options] */
export function normalizeActionResultState({
  raw, changesByDefault = false, verification = 'read', effect = 'read', tool = '',
} = {}) {
  let parsed = null;
  try { parsed = JSON.parse(String(raw)); } catch { /* gewone tekst is geldige read-uitvoer */ }
  const ok = parsed?.ok === false ? false : !/^Fout bij /i.test(String(raw));
  const changed = typeof parsed?.changed === 'boolean' ? parsed.changed : changesByDefault && ok;
  const verified = !ok ? false : typeof parsed?.verified === 'boolean' ? parsed.verified
    : verification === 'explicit' ? false : verification === 'ok' ? parsed?.ok === true : true;
  const reason = clipped(parsed?.reason || parsed?.error, 1_200);
  const summary = Array.isArray(parsed)
    ? `${parsed.length} resultaat${parsed.length === 1 ? '' : 'en'} gelezen.`
    : clipped(parsed?.message || parsed?.info || reason || raw, 1_600);
  const serialized = parsed == null ? String(raw) : JSON.stringify(parsed);
  const data = compactActionResultData(parsed, raw, serialized, { tool });
  const outputTruncated = Boolean(data && typeof data === 'object'
    && !Array.isArray(data) && data.truncated === true);
  const needsFollowup = outputTruncated || (typeof parsed?.needs_followup === 'boolean'
    ? parsed.needs_followup : !verified);
  const partial = outputTruncated || parsed?.partial === true;
  const mutating = !['read', 'unknown'].includes(effect);
  const failure = !ok
    ? normalizeActionFailure(parsed, classifyActionFailure(reason || raw), { mutating })
    : (!verified || needsFollowup || partial) ? {
      failure_class: outputTruncated ? 'output_truncated'
        : partial ? 'partial_result' : 'unverified',
      retryable: false, effect_uncertain: false,
    } : null;
  return { parsed, ok, changed, verified, reason, summary, data, needsFollowup, partial, failure };
}

export function boundedRuntimeMs(value, { maximum = 120_000, reserve = 500 } = {}) {
  const requested = Number(value);
  return Number.isFinite(requested) && requested > 0
    ? Math.max(1, Math.min(maximum, Math.floor(requested) - reserve)) : maximum;
}

/** Auditweergave die bewijsvelden behoudt zonder grote tooluitvoer te dupliceren. */
export function auditActionResultOutput(output, parsed = null) {
  const raw = String(output || '');
  if (raw.length <= 1_800) return raw;
  const data = parsed?.data && typeof parsed.data === 'object' ? parsed.data : null;
  const workReceipt = compactWorkReceiptData(data, { tool: parsed?.tool });
  return JSON.stringify({
    contract: parsed?.contract || 'ActionResult/v1', tool: parsed?.tool || null,
    ok: parsed?.ok === true, changed: parsed?.changed === true, verified: parsed?.verified === true,
    partial: parsed?.partial === true, needs_followup: parsed?.needs_followup === true,
    audit_truncated: true, audit_compacted: workReceipt ? true : undefined,
    error: clipped(parsed?.error, 500) || undefined,
    failure_class: parsed?.failure_class || undefined,
    data: workReceipt || (data ? {
      ok: typeof data.ok === 'boolean' ? data.ok : undefined,
      changed: typeof data.changed === 'boolean' ? data.changed : undefined,
      exit_code: data.exit_code, expected_exit_codes: data.expected_exit_codes, path: data.path,
      stdout: clipped(data.stdout, 300) || undefined, stderr: clipped(data.stderr, 300) || undefined,
      material_sha256: data.material_sha256, artifact_receipts: data.artifact_receipts,
    } : undefined),
    artifact_receipt: parsed?.artifact_receipt,
    evidence: clipped(parsed?.evidence?.summary, 500) || undefined,
  });
}

/** @param {any} record */
function actionResultPayload(record) {
  try { return JSON.parse(String(record?.output || '')); } catch { return null; }
}

export function executionActionResult(recorded) {
  const payload = actionResultPayload(recorded);
  if (!payload) return {
    contract: 'ActionResult/v1', ok: false, changed: false, verified: false,
    needs_followup: true, partial: false, failure_class: 'invalid_result',
    retryable: false, effect_uncertain: false,
  };
  if (payload.contract !== 'ActionResult/v1') return payload;
  const needsRecovery = payload.ok !== true || payload.verified !== true
    || payload.partial === true || payload.needs_followup === true;
  if (!needsRecovery) return payload;
  const typedHostFailure = {
    failure_class: typeof recorded?.failure_class === 'string'
      && recorded.failure_class.trim() ? recorded.failure_class.trim() : undefined,
    retryable: typeof recorded?.retryable === 'boolean' ? recorded.retryable : undefined,
    effect_uncertain: typeof recorded?.effect_uncertain === 'boolean'
      ? recorded.effect_uncertain : undefined,
  };
  for (const [field, hostValue] of Object.entries(typedHostFailure)) {
    if (hostValue !== undefined && Object.hasOwn(payload, field)
      && payload[field] !== hostValue) {
      // Een strijdige toolclaim en hostwaarneming mag niet stil één geldig
      // ActionResult worden. Het afwijkende contract laat de driver gesloten
      // stoppen als protocolfout.
      return {
        contract: 'InvalidActionResult/v1', ok: false, verified: false,
        error: `conflicting_action_result_metadata:${field}`,
      };
    }
  }
  const result = { ...payload };
  for (const [field, hostValue] of Object.entries(typedHostFailure)) {
    if (hostValue !== undefined && !Object.hasOwn(payload, field)) result[field] = hostValue;
  }
  return result;
}

/** Moderne write/read- en write/run/read-receipts vormen alleen bewijs als
 * exact dezelfde volledige bytes zijn gevolgd. `run` is optioneel voor gewone
 * documenten; uitvoerbare artefacten blijven daarnaast hun before/after-run
 * receipts vereisen. */
/** @param {any} [options] */
export function artifactReceiptChainMatches({
  write, run, read = null, path, normalizePath = String,
} = {}) {
  const writeResult = actionResultPayload(write);
  const runResult = actionResultPayload(run);
  const readResult = read ? actionResultPayload(read) : null;
  const records = [write, ...(run ? [run] : []), ...(read ? [read] : [])];
  const results = [writeResult, ...(run ? [runResult] : []), ...(read ? [readResult] : [])];
  if (results.some((result) => result?.contract !== 'ActionResult/v1')) return false;
  try {
    const wanted = normalizePath(path);
    const bindings = records.map((record) => record?.semanticBinding || {});
    const understandingSha256 = String(bindings[0]?.understandingSha256 || '');
    const requirementId = String(bindings[0]?.requirementId || '');
    if (!/^[a-f0-9]{64}$/u.test(understandingSha256) || !requirementId
      || bindings.some((binding) => (
        binding.understandingSha256 !== understandingSha256
        || binding.requirementId !== requirementId
      ))) return false;
    const receiptFor = (phase) => (runResult?.data?.artifact_receipts?.[phase] || [])
      .find((receipt) => normalizePath(receipt.path) === wanted);
    const hashes = [writeResult?.data?.material_sha256];
    if (run) hashes.push(receiptFor('before')?.sha256, receiptFor('after')?.sha256);
    if (read) {
      const receipt = readResult?.artifact_receipt;
      if (receipt?.complete_read !== true
        || normalizePath(receipt?.path || '') !== wanted) return false;
      hashes.push(receipt.sha256);
    }
    return hashes.every((hash) => /^[a-f0-9]{64}$/iu.test(String(hash || '')))
      && hashes.every((hash) => hash === hashes[0]);
  } catch { return false; }
}
