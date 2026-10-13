import { db, getTimeline, validateExecutionRecord } from '../db.js';
import { canonicalJson } from '../util.js';
import { validHomeRequesterTargetBinding } from './home.js';
import { monitoringGuardValid } from './turnplan.js';

const ACCOUNTABILITY = /\b(?:per\s+ongeluk|onbedoeld|vergissing|fout(?:je)?|niet\s+zeker|onzeker|mogelijk\s+effect|brand|rook|koolmonoxide|inbraak|lekkage|nood|alarm|privacy|betaling|betaald|kosten|geld|menselijke\s+keuze|jullie\s+moeten|je\s+moet)\b/i;
const OPERATIONAL_UPDATE = /\b(?:staat|stond|gezet|geschakeld|aangezet|uitgezet|hersteld|gecontroleerd|gevonden|afgerond|lamp|licht|deur|raam|robot|stofzuiger|verwarming|thermostaat|apparaat|bestand|rapport|test)\b/i;
const INTERNAL_LANGUAGE = /\b(?:aanwezigheidsbewijs|uitstand|bewijsgrens|delivery|receipt|werkpunt|werkboek|entity[_ -]?id|tool(?:call)?|gecontroleerd\s+(?:uit|aan)|op\s+naam\s+gegokt)\b/i;
const ROUTINE_MESSAGE_QUIET_MS = 90 * 60_000;
const HOME_ACTION_STATE = Object.freeze({
  aan: 'on', uit: 'off', open: 'open', dicht: 'closed',
});
const HA_ENTITY_ID = /^[a-z_]+\.[a-z0-9_]+$/u;

function actionData(tool) {
  try { return JSON.parse(String(tool?.output || '')).data || null; } catch { return null; }
}

function actionEnvelope(tool) {
  try { return JSON.parse(String(tool?.output || '')); } catch { return null; }
}

function canonicalHomeAction(value = '') {
  const action = String(value || '').toLowerCase().trim();
  return ({
    on: 'aan', turn_on: 'aan', aanzetten: 'aan',
    off: 'uit', turn_off: 'uit', uitzetten: 'uit',
    openen: 'open', close: 'dicht', sluiten: 'dicht', closed: 'dicht',
  })[action] || action;
}

function canonicalHomeTarget(value = '') {
  const ordinals = {
    eerste: '1', tweede: '2', derde: '3', vierde: '4', vijfde: '5',
    zesde: '6', zevende: '7', achtste: '8', negende: '9', tiende: '10',
  };
  const ignored = new Set([
    'de', 'het', 'een', 'in', 'op', 'onder', 'bij', 'van',
    'media', 'player', 'light', 'switch', 'cover', 'climate',
  ]);
  return String(value || '').toLowerCase().replace(/^[a-z_]+\./u, '')
    .replace(/_/gu, ' ').replace(/[^\p{L}\p{N} ]/gu, ' ').split(/\s+/u)
    .filter(Boolean).map((token) => ordinals[token] || token)
    .filter((token) => !ignored.has(token)).sort().join(' ');
}

function resolvedTargetMatches(target, data, requester = null, targetBinding = data?.target_binding) {
  const expected = canonicalHomeTarget(target);
  const entity = canonicalHomeTarget(data?.entity_id);
  const name = canonicalHomeTarget(data?.name);
  if (!expected) return false;
  // De opgeloste entity-id is de harde identiteit; een friendly name kan bij
  // duplicate apparaten generiek zijn en mag een strijdige entity nooit witwassen.
  if (entity ? entity === expected : name === expected) return true;
  return validHomeRequesterTargetBinding(targetBinding, {
    requestedTarget: target,
    requester,
    resolvedEntityId: data?.entity_id,
  });
}

function targetMatches(target, tool, data, requester = null) {
  const expected = canonicalHomeTarget(target);
  return Boolean(expected)
    && canonicalHomeTarget(tool?.args?.doel) === expected
    && resolvedTargetMatches(target, data, requester);
}

function exactHomeMemberList(members, validState) {
  if (!Array.isArray(members) || !members.length) return null;
  const normalized = members.map((member) => ({
    entityId: String(member?.entity_id || '').trim(),
    state: String(member?.state || '').trim(),
  }));
  if (normalized.some(({ entityId, state }) => (
    !HA_ENTITY_ID.test(entityId) || !state || !validState(state)
  ))
    || new Set(normalized.map(({ entityId }) => entityId)).size !== normalized.length) return null;
  return normalized;
}

function exactHomeMembers(members, expectedState) {
  return exactHomeMemberList(members, (state) => state === expectedState);
}

function exactUnavailableHomeMembers(members) {
  return exactHomeMemberList(
    members, (state) => ['unavailable', 'unknown'].includes(state),
  );
}

function exactPartialResultFlags(value, { failure = false, settlement = true } = {}) {
  return value?.ok === true && value?.verified === true && value?.changed === true
    && value?.partial === true && value?.needs_followup === true
    && (!settlement || (value?.retryable === false && value?.effect_uncertain === false))
    && (!failure || value?.failure_class === 'partial_result');
}

function boundedPartialToolValid(tool, expectedAction) {
  return tool?.name === 'bedien_huis'
    && canonicalHomeAction(tool?.args?.actie) === expectedAction
    && exactPartialResultFlags(tool, { failure: true });
}

function boundedPartialPayloadValid(payload) {
  return payload?.contract === 'ActionResult/v1' && payload?.tool === 'bedien_huis'
    && exactPartialResultFlags(payload, { failure: true });
}

function boundedPartialDataValid(data, expectedService, expectedState, entityId) {
  const before = typeof data?.before === 'string' ? data.before.trim() : '';
  const after = String(data?.after ?? '').trim();
  return exactPartialResultFlags(data, { settlement: false })
    && HA_ENTITY_ID.test(entityId) && entityId.startsWith('light.')
    && String(data?.service || '').trim() === expectedService
    && typeof data?.before === 'string' && Boolean(before)
    && before !== after && after === expectedState;
}

function boundedPartialAssessmentValid(data, assessment, expectedState, entityId) {
  const assessmentEntityId = String(assessment?.entity_id || '').trim();
  const readback = data?.evidence?.readback;
  const readbackEntityId = String(readback?.entity_id || '').trim();
  return assessment?.ok === false && assessment?.reachableVerified === true
    && assessment?.partial === true && assessment?.isGroup === true
    && HA_ENTITY_ID.test(assessmentEntityId) && assessmentEntityId === entityId
    && String(assessment?.expected || '').trim() === expectedState
    && String(assessment?.state || '').trim() === expectedState
    && HA_ENTITY_ID.test(readbackEntityId) && readbackEntityId === entityId
    && String(readback?.state || '').trim() === expectedState;
}

function boundedPartialMessageValid(payload, data) {
  const message = String(payload?.human_message || '').trim();
  return Boolean(message) && message === String(data?.message || '').trim();
}

function boundedPartialMembersValid(assessment, expectedState) {
  const reachable = exactHomeMembers(assessment?.reachable, expectedState);
  const unavailable = exactUnavailableHomeMembers(assessment?.unavailable);
  if (!reachable || !unavailable) return false;
  const reachableById = new Map(reachable.map((member) => [member.entityId, member.state]));
  const unavailableById = new Map(unavailable.map((member) => [member.entityId, member.state]));
  if ([...reachableById.keys()].some((id) => unavailableById.has(id))) return false;
  const expectedMembers = new Map([...reachableById, ...unavailableById]);
  const members = exactHomeMemberList(assessment.members, () => true);
  if (!members || members.length !== expectedMembers.size
    || members.some(({ entityId: id, state }) => expectedMembers.get(id) !== state)) return false;
  const unresolved = exactUnavailableHomeMembers(assessment.unresolved);
  return Boolean(unresolved && unresolved.length === unavailableById.size
    && unresolved.every(({ entityId: id, state }) => unavailableById.get(id) === state));
}

/**
 * Een HA-groep kan een echt, begrensd succes bewijzen terwijl enkele leden
 * offline blijven. Dit is nadrukkelijk geen algemene partial-uitzondering:
 * alleen een veranderde bedien_huis-mutatie met een intacte groepsreadback,
 * alle bereikbare leden in de verwachte toestand en uitsluitend expliciet
 * onbereikbare restleden passeert deze grens.
 */
export function boundedVerifiedHomePartialEffect(tool, intent = tool?.args || null) {
  const payload = actionEnvelope(tool);
  const data = payload?.data;
  const assessment = data?.evidence?.assessment;
  const expectedAction = canonicalHomeAction(intent?.actie);
  const expectedState = HOME_ACTION_STATE[expectedAction];
  const expectedService = ({ aan: 'light.turn_on', uit: 'light.turn_off' })[expectedAction];
  const entityId = String(data?.entity_id || '').trim();
  if (!expectedState || !expectedService || !intent?.doel
    || !boundedPartialToolValid(tool, expectedAction)
    || !boundedPartialPayloadValid(payload)
    || !boundedPartialDataValid(data, expectedService, expectedState, entityId)
    || !boundedPartialAssessmentValid(data, assessment, expectedState, entityId)
    || !boundedPartialMessageValid(payload, data)) return false;

  const requester = intent.requester || data.target_binding?.requester || null;
  if (!targetMatches(intent.doel, tool, data, requester)) return false;
  return boundedPartialMembersValid(assessment, expectedState);
}

export function homeReadbackProvesIntent(intent = null, toolResults = []) {
  const action = canonicalHomeAction(intent?.actie);
  const expectedState = HOME_ACTION_STATE[action];
  if (!intent?.doel || !expectedState) return false;

  for (let index = toolResults.length - 1; index >= 0; index -= 1) {
    const attempt = toolResults[index];
    const attemptedData = actionData(attempt) || {};
    if (attempt?.name !== 'bedien_huis'
      || canonicalHomeAction(attempt.args?.actie) !== action
      || !targetMatches(intent.doel, attempt, attemptedData, intent.requester)) continue;
    const attemptedEntityId = String(attemptedData.entity_id || '').toLowerCase();
    if (!attemptedEntityId) continue;

    for (let readIndex = toolResults.length - 1; readIndex > index; readIndex -= 1) {
      const read = toolResults[readIndex];
      const readData = actionData(read) || {};
      if (read?.name === 'ha_lees' && read.ok && read.verified
        && !read.partial && !read.needs_followup
        && String(readData.entity_id || '').toLowerCase() === attemptedEntityId
        && resolvedTargetMatches(
          intent.doel, readData, intent.requester, attemptedData.target_binding,
        )
        && String(readData.state || '').toLowerCase() === expectedState) return true;
    }
    return false;
  }
  return false;
}

function homeActionReceipt(intent, toolResults = [], legacyReadbackProven) {
  const action = canonicalHomeAction(intent?.actie);
  const expectedState = HOME_ACTION_STATE[action];
  if (!intent?.doel || !expectedState) {
    return { stateProven: false, mutationProven: false };
  }
  for (const tool of [...toolResults].reverse()) {
    const data = actionData(tool) || {};
    if (boundedVerifiedHomePartialEffect(tool, intent)) {
      const unavailable = data.evidence.assessment.unavailable;
      return {
        stateProven: true,
        mutationProven: true,
        boundedPartial: {
          expectedState,
          reachableCount: data.evidence.assessment.reachable.length,
          unavailableCount: unavailable.length,
          unavailableNames: unavailable
            .map((member) => String(member?.name || '').trim()).filter(Boolean),
        },
      };
    }
    const valid = tool.name === 'bedien_huis' && tool.ok && tool.verified
      && !tool.partial && !tool.needs_followup && Boolean(data.entity_id)
      && canonicalHomeAction(tool.args?.actie) === action
      && targetMatches(intent.doel, tool, data, intent.requester)
      && String(data.after || '').toLowerCase() === expectedState;
    if (!valid) continue;
    return {
      stateProven: true,
      mutationProven: tool.changed === true
        && String(data.before ?? '') !== String(data.after ?? ''),
    };
  }
  const readbackProven = legacyReadbackProven !== false
    && homeReadbackProvesIntent(intent, toolResults);
  return { stateProven: readbackProven, mutationProven: false };
}

const DUTCH_COUNTS = Object.freeze([
  'nul', 'een', 'twee', 'drie', 'vier', 'vijf', 'zes', 'zeven', 'acht', 'negen',
  'tien', 'elf', 'twaalf', 'dertien', 'veertien', 'vijftien', 'zestien',
  'zeventien', 'achttien', 'negentien', 'twintig',
]);

function exactCountPattern(count) {
  const word = DUTCH_COUNTS[count];
  return `(?:${count}${word ? `|${word}` : ''})`;
}

const HOME_COMPONENT_PATTERN = String.raw`(?:[\p{L}\p{N}_-]*lamp(?:en)?|lichten?|onderde(?:el|len)|appar(?:aat|aten)|(?:groeps)?leden|lid)`;

function clauseBindsUnavailableCount(clause, count) {
  const exactCount = exactCountPattern(count);
  const predicate = String.raw`(?:offline|onbereikbaar|niet\s+bereikbaar|niet\s+beschikbaar)`;
  const attributive = String.raw`(?:offline|onbereikbare?|niet\s+bereikbare?|niet\s+beschikbare?)`;
  const copula = String.raw`(?:(?:is|zijn|blijft|blijven)\s+)?(?:nog\s+)?`;
  return new RegExp(
    String.raw`\b${exactCount}\s+${HOME_COMPONENT_PATTERN}\s+${copula}${predicate}\b|\b${exactCount}\s+${attributive}\s+${HOME_COMPONENT_PATTERN}\b|\b(?:die|deze)\s+${exactCount}\s+${copula}${predicate}\b`,
    'iu',
  ).test(clause);
}

function clauseBindsReachableState(clause, boundedPartial) {
  if (/\b(?:niet|geen|nooit|noch)\b/iu.test(clause)) return false;
  const exactCount = exactCountPattern(boundedPartial.reachableCount);
  const state = ({
    on: 'aan', off: 'uit', open: 'open', closed: '(?:dicht|gesloten)',
  })[boundedPartial.expectedState];
  if (!state) return false;
  const copulaState = String.raw`(?:staat|staan|is|zijn)\s+${state}`;
  const countReachableSubject = String.raw`(?:alle\s+)?${exactCount}\s+bereikbare\s+${HOME_COMPONENT_PATTERN}`;
  const countRelativeSubject = String.raw`(?:alle\s+)?${exactCount}\s+${HOME_COMPONENT_PATTERN}\s+die\s+bereikbaar\s+(?:is|zijn),?`;
  return new RegExp(
    String.raw`\b${countReachableSubject}\s+${copulaState}\b|\b${countRelativeSubject}\s+${copulaState}\b|\b${copulaState}\s+op\s+${countReachableSubject}\b`,
    'iu',
  ).test(clause);
}

function boundedPartialHomeClaimIsQualified(answer, boundedPartial) {
  const value = String(answer || '');
  const clauses = value.split(/[.!?;]+/u);
  const reachableState = clauses.some((clause) => (
    clauseBindsReachableState(clause, boundedPartial)
  ));
  const boundedLimitation = clauses.some((clause) => (
    clauseBindsUnavailableCount(clause, boundedPartial.unavailableCount)
  ));
  if (!reachableState || !boundedLimitation) return false;
  const withoutReachableBound = value.replace(
    /\balle\b(?=[^.!?;]{0,60}\b(?:bereikbaar|bereikbare)\b)/giu,
    '',
  );
  return !/\b(?:alles|alle|elk(?:e)?|iedere|helemaal|volledig|zonder\s+uitzondering)\b/iu
    .test(withoutReachableBound);
}

const BOUNDED_HOME_CONTINUATION_KEYS = Object.freeze([
  'contract',
  'entity_id',
  'expected_state',
  'reachable_count',
  'source_completion_record_sha256',
  'unavailable_count',
  'unavailable_names',
]);
const HOME_EXECUTION_BINDING_KEYS = Object.freeze([
  'addressesRequirementIds',
  'basisReceiptIds',
  'capabilityId',
  'executionType',
  'operationId',
  'requirementId',
  'role',
  'understandingSha256',
]);

function exactRecordKeys(value, expected) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join('\u0000') === [...expected].sort().join('\u0000');
}

function regexEscape(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
}

// Exact dezelfde byteprojectie als tools.js `clean(value, 96)`. De daaropvolgende
// woordnormalisatie is alleen voor natuurlijke antwoordmatching; ledger- en
// boundaryvergelijkingen blijven de geprojecteerde bytes exact vergelijken.
function boundedHomeName(value) {
  return String(value ?? '').slice(0, 96);
}

function canonicalHomeSubject(value) {
  return String(value || '').normalize('NFKC').toLocaleLowerCase('nl-NL')
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/gu, ' ');
}

function exactLedgerHomeSubjectPattern(receipt) {
  const entityAlias = canonicalHomeSubject(
    String(receipt?.entityId || '').split('.').at(-1)?.replace(/_/gu, ' ') || '',
  );
  const aliases = [...new Set([
    canonicalHomeSubject(boundedHomeName(receipt?.name || '')),
    entityAlias,
  ].filter(Boolean))];
  if (!aliases.length) return null;
  return aliases.map((alias) => {
    const exact = alias.split(' ').map(regexEscape).join(String.raw`\s+`);
    return /(?:groep|licht)$/u.test(alias)
      ? exact : `${exact}(?:\\s*(?:groep|licht))?`;
  }).join('|');
}

function exactHomeExecutionBinding(binding, capabilityId, operationId) {
  return exactRecordKeys(binding, HOME_EXECUTION_BINDING_KEYS)
    && /^[a-f0-9]{64}$/u.test(String(binding.understandingSha256 || ''))
    && /^r(?:[1-9]|1[0-2])$/u.test(String(binding.requirementId || ''))
    && Array.isArray(binding.addressesRequirementIds)
    && binding.addressesRequirementIds.length === 1
    && binding.addressesRequirementIds[0] === binding.requirementId
    && Array.isArray(binding.basisReceiptIds)
    && binding.basisReceiptIds.length === 0
    && binding.capabilityId === capabilityId
    && binding.operationId === operationId
    && binding.role === 'primary'
    && binding.executionType == null;
}

function exactVerifiedExecutionRows(sql, ...params) {
  try {
    return db.prepare(sql).all(...params).map((row) => validateExecutionRecord(row));
  } catch { return []; }
}

function currentHomeContinuationExecution(tool) {
  const binding = tool?.semanticBinding;
  if (!exactHomeExecutionBinding(binding, 'home.entity.read', 'read')) return null;
  const rows = exactVerifiedExecutionRows(`SELECT * FROM main.turn_tool_executions
    WHERE status='done' AND tool='ha_lees' AND args=? AND semantic_binding=? AND output=?`,
  canonicalJson(tool.args), canonicalJson(binding), String(tool.output || ''));
  if (rows.length !== 1
    || !String(rows[0].execution_key || '').startsWith('observation:')) return null;
  return rows[0];
}

function boundedUnavailableNames(assessment) {
  const usedNames = new Set();
  return assessment.unavailable.map((member) => {
    const entityId = String(member.entity_id);
    const friendly = boundedHomeName(member.name || '');
    let name = friendly || entityId;
    if (usedNames.has(name.toLocaleLowerCase('nl-NL'))) name = entityId;
    usedNames.add(name.toLocaleLowerCase('nl-NL'));
    return name;
  });
}

function priorBoundedHomeExecution(prior, boundary) {
  const rows = exactVerifiedExecutionRows(`SELECT * FROM main.turn_tool_executions
    WHERE status='done' AND completion_record_sha256=?`,
  String(prior.completion_record_sha256 || ''));
  if (rows.length !== 1) return null;
  const execution = rows[0];
  let args, binding, result;
  try {
    args = JSON.parse(execution.args);
    binding = JSON.parse(execution.semantic_binding);
    result = JSON.parse(execution.output);
  } catch { return null; }
  const data = result?.data;
  const assessment = data?.evidence?.assessment;
  const recorded = {
    name: execution.tool,
    args,
    output: execution.output,
    ok: result?.ok,
    verified: result?.verified,
    changed: result?.changed,
    partial: result?.partial,
    needs_followup: result?.needs_followup,
    failure_class: result?.failure_class,
    retryable: result?.retryable,
    effect_uncertain: result?.effect_uncertain,
  };
  if (execution.tool !== 'bedien_huis'
    || !exactHomeExecutionBinding(binding, 'home.device.control', 'set')
    || execution.turn_id !== prior.turn_id
    || !boundedVerifiedHomePartialEffect(recorded, args)
    || prior.requested_target !== args.doel
    || canonicalHomeAction(prior.action) !== canonicalHomeAction(args.actie)
    || prior.resulting_state !== data.after
    || prior.target_binding_sha256
      !== String(data.target_binding?.binding_sha256 || '')) return null;
  const expectedBoundary = {
    contract: 'BoundedHomePartialContinuation/v1',
    source_completion_record_sha256: execution.completion_record_sha256,
    entity_id: data.entity_id,
    expected_state: assessment.expected,
    reachable_count: assessment.reachable.length,
    unavailable_count: assessment.unavailable.length,
    unavailable_names: boundedUnavailableNames(assessment),
  };
  return canonicalJson(boundary) === canonicalJson(expectedBoundary) ? execution : null;
}

function exactVerifiedReadFlags(value, { effect = false } = {}) {
  return value?.ok === true && value?.verified === true && value?.changed === false
    && value?.partial !== true && value?.needs_followup !== true
    && (!effect || value?.effect_uncertain !== true);
}

function continuationToolAndPayloadValid(tool, payload, args, entityId) {
  return tool?.name === 'ha_lees' && exactVerifiedReadFlags(tool, { effect: true })
    && exactRecordKeys(args, ['entity_id']) && String(args.entity_id || '') === entityId
    && payload?.contract === 'ActionResult/v1' && payload?.tool === 'ha_lees'
    && exactVerifiedReadFlags(payload, { effect: true });
}

function continuationCurrentDataValid(data, current, entityId, currentState) {
  return exactVerifiedReadFlags(data)
    && HA_ENTITY_ID.test(entityId) && entityId.startsWith('light.') && Boolean(currentState)
    && current?.entity_id === entityId && String(current?.state || '') === currentState;
}

function continuationPriorValid(prior, expectedState, completionSha256) {
  return prior?.verified === true && prior?.changed === true
    && typeof prior?.requested_target === 'string' && Boolean(prior.requested_target.trim())
    && typeof prior?.turn_id === 'string' && Boolean(prior.turn_id.trim())
    && /^[a-f0-9]{64}$/u.test(completionSha256)
    && /^(?:|[a-f0-9]{64})$/u.test(String(prior?.target_binding_sha256 || ''))
    && Boolean(expectedState) && String(prior?.resulting_state || '') === expectedState;
}

function continuationNamesValid(unavailableNames, boundary) {
  return Array.isArray(unavailableNames)
    && unavailableNames.length === boundary?.unavailable_count
    && unavailableNames.every((name) => (
      typeof name === 'string' && name.trim() === name && name.length > 0 && name.length <= 120
    ))
    && new Set(unavailableNames.map((name) => name.toLocaleLowerCase('nl-NL'))).size
      === unavailableNames.length;
}

function continuationBoundaryValid(boundary, unavailableNames, {
  completionSha256, entityId, expectedState,
}) {
  return exactRecordKeys(boundary, BOUNDED_HOME_CONTINUATION_KEYS)
    && boundary?.contract === 'BoundedHomePartialContinuation/v1'
    && boundary?.source_completion_record_sha256 === completionSha256
    && boundary?.entity_id === entityId && boundary?.expected_state === expectedState
    && Number.isSafeInteger(boundary?.reachable_count) && boundary.reachable_count >= 1
    && Number.isSafeInteger(boundary?.unavailable_count) && boundary.unavailable_count >= 1
    && boundary.reachable_count + boundary.unavailable_count <= 500
    && continuationNamesValid(unavailableNames, boundary);
}

function continuationMessageValid(payload, data) {
  return typeof payload?.human_message === 'string' && Boolean(payload.human_message.trim())
    && payload.human_message.trim() === String(data?.message || '').trim();
}

function boundedHomeContinuationCandidate(tool) {
  const payload = actionEnvelope(tool);
  const data = payload?.data;
  const prior = data?.prior_action;
  if (!prior || !Object.hasOwn(prior, 'bounded_partial')) return null;
  const invalid = Object.freeze({ invalid: true });
  const boundary = prior.bounded_partial;
  const current = data?.current;
  const args = tool?.args;
  const unavailableNames = boundary?.unavailable_names;
  const completionSha256 = String(prior?.completion_record_sha256 || '');
  const entityId = String(data?.entity_id || '');
  const currentState = String(data?.state || '');
  const action = canonicalHomeAction(prior?.action);
  const expectedState = HOME_ACTION_STATE[action];
  if (!continuationToolAndPayloadValid(tool, payload, args, entityId)
    || !continuationCurrentDataValid(data, current, entityId, currentState)
    || !continuationPriorValid(prior, expectedState, completionSha256)
    || !continuationBoundaryValid(boundary, unavailableNames, {
      completionSha256, entityId, expectedState,
    })
    || !continuationMessageValid(payload, data)
    || !currentHomeContinuationExecution(tool)
    || !priorBoundedHomeExecution(prior, boundary)) return invalid;
  return Object.freeze({
    invalid: false,
    entityId,
    name: boundedHomeName(data.name || current?.name || entityId),
    currentState,
    expectedState,
    reachableCount: boundary.reachable_count,
    unavailableCount: boundary.unavailable_count,
    unavailableNames: Object.freeze([...unavailableNames]),
    sourceCompletionRecordSha256: completionSha256,
    fallbackMessage: payload.human_message.trim(),
  });
}

function boundedHomeContinuationReceipt(toolResults = []) {
  const candidates = toolResults.map(boundedHomeContinuationCandidate).filter(Boolean);
  if (!candidates.length) return null;
  if (candidates.length !== 1 || candidates[0].invalid) {
    return Object.freeze({ invalid: true });
  }
  return candidates[0];
}

function clauseBindsCurrentGroupState(clause, receipt) {
  if (/\b(?:niet|geen|nooit|noch)\b/iu.test(clause)) return false;
  const state = ({
    on: 'aan', off: 'uit', open: 'open', closed: '(?:dicht|gesloten)',
  })[receipt.currentState];
  if (!state) return false;
  const subject = exactLedgerHomeSubjectPattern(receipt);
  if (!subject) return false;
  const normalizedClause = canonicalHomeSubject(clause);
  const subjectRef = String.raw`(?:^|\s)(?:${subject})(?=\s|$)`;
  const verbRef = String.raw`(?:^|\s)(?:staat|staan|is|zijn)(?=\s|$)`;
  const stateRef = String.raw`(?:^|\s)(?:${state})(?=\s|$)`;
  return new RegExp(
    `${subjectRef}.{0,50}${verbRef}.{0,20}${stateRef}`
      + `|${verbRef}.{0,20}${stateRef}.{0,50}${subjectRef}`,
    'u',
  ).test(normalizedClause);
}

function boundedHomeContinuationClaimIsQualified(answer, receipt) {
  const value = String(answer || '');
  if (claimsUnboundedHomeCompletion(value)) return false;
  const clauses = value.split(/[.!?;]+/u);
  const currentGroupState = clauses.some((clause) => (
    clauseBindsCurrentGroupState(clause, receipt)
  ));
  const reachable = exactCountPattern(receipt.reachableCount);
  const unavailable = exactCountPattern(receipt.unavailableCount);
  const historicalBoundary = clauses.some((clause) => (
    /\b(?:vorige\s+actie|bij\s+de\s+(?:vorige\s+)?actie|toen|destijds)\b/iu.test(clause)
      && /\b(?:was|waren|bleef|bleven)\b/iu.test(clause)
      && new RegExp(String.raw`\b${reachable}\s+(?:[\p{L}\p{N}_-]+\s+){0,3}bereikbaar\b`, 'iu')
        .test(clause)
      && new RegExp(String.raw`\b${unavailable}\s+(?:(?:[\p{L}\p{N}_-]+\s+){0,3})?(?:offline|onbereikbaar|niet\s+bereikbaar|niet\s+beschikbaar)\b`, 'iu')
        .test(clause)
  ));
  const claimsUnavailableStillCurrent = /\b(?:nu|nog|momenteel|op\s+dit\s+moment)\b[^.!?;]{0,60}\b(?:offline|onbereikbaar|niet\s+bereikbaar|niet\s+beschikbaar)\b|\b(?:offline|onbereikbaar)\b[^.!?;]{0,60}\b(?:nu|nog|momenteel)\b/iu
    .test(value);
  const total = exactCountPattern(receipt.reachableCount + receipt.unavailableCount);
  const withoutBoundedUniversal = value.replace(
    new RegExp(String.raw`\bniet\s+alle\s+${total}\b[^.!?;]{0,80}\b(?:individueel\s+)?bevestigd\b`, 'giu'),
    '',
  );
  const unboundedUniversal = /\b(?:alles|alle|elk(?:e)?|iedere|helemaal|volledig|zonder\s+uitzondering)\b/iu
    .test(withoutBoundedUniversal);
  return currentGroupState && historicalBoundary
    && !claimsUnavailableStillCurrent && !unboundedUniversal;
}

function claimsUnboundedHomeCompletion(answer = '') {
  const value = String(answer || '');
  return /\b(?:klaar|gedaan|geregeld|gefikst|gelukt|gereed|voltooid|afgerond|uitgevoerd)\b|\bvoor\s+elkaar\b|\bresultaat\b[^.!?;]{0,40}\bbereikt\b/iu
    .test(value);
}

function explicitlyDeniesHomeMutation(answer = '') {
  const value = String(answer || '');
  const withinClause = String.raw`[^.!?;]{0,40}`;
  return new RegExp(
    String.raw`\bniet\b${withinClause}\b(?:(?:aan|uit)\s*(?:gezet|gedaan|geschakeld)|(?:in|uit)geschakeld|geopend|gesloten|opengezet|dichtgedaan|veranderd)\b|\b(?:zette|deed|schakelde|opende|sloot)\b${withinClause}\bniet\b${withinClause}\b(?:aan|uit|open|dicht)\b`,
    'i',
  ).test(value);
}

function claimsHomeMutation(answer = '', action = '') {
  const value = String(answer || '');
  if (explicitlyDeniesHomeMutation(value)) return false;
  if (/\b(?:(?:aan|uit)gezet|(?:in|uit)geschakeld|geopend|gesloten|opengezet|dichtgedaan)\b/i.test(value)) return true;
  if (/\b(?:zette|deed|schakelde)\b[^.!?;]{0,35}\b(?:aan|uit)\b|\b(?:opende|sloot)\b/i.test(value)) return true;
  if (/\b(?:aan|uit)\s+(?:gedaan|gezet|geschakeld)\b|\b(?:open|dicht)\s+(?:gedaan|gezet|gemaakt)\b/i.test(value)) return true;
  if (/^\s*(?:zo[,.]?\s*)?(?:klaar|gedaan|geregeld|voor\s+elkaar|gefikst)[.!]?\s*$/i.test(value)) return true;
  const state = ({ aan: 'aan', uit: 'uit', open: 'open', dicht: 'dicht|gesloten' })[action] || '';
  return Boolean(state) && /\b(?:gedaan|geregeld|klaar)\b/i.test(value)
    && new RegExp(`\\b(?:staat|is)\\b.{0,20}\\b(?:${state})\\b`, 'i').test(value);
}

function claimsHomeState(answer = '', action = '') {
  const state = ({ aan: 'aan', uit: 'uit', open: 'open', dicht: 'dicht|gesloten' })[action] || '';
  if (!state) return false;
  const value = String(answer || '');
  const stated = new RegExp(
    `\\b(?:staat|staan|stond|stonden|is|zijn|was|waren)\\b.{0,24}\\b(?:${state})\\b`, 'i',
  ).test(value);
  const terse = new RegExp(
    `^\\s*(?:(?:de|het|die|dat|hij|zij|ze|tv|televisie|lampen?|lichten?|heater|deuren?|ramen?|apparaten?)\\b.{0,32}\\s+)?(?:(?:staat|staan)\\s+)?(?:${state})[.!]?\\s*$`,
    'i',
  ).test(value);
  return stated || terse;
}

export function homeEffectClaimFeedback(reply = '', intent = null, toolResults = [], {
  readbackProven = false,
} = {}) {
  const continuation = boundedHomeContinuationReceipt(toolResults);
  if (continuation?.invalid) {
    return 'De begrenzing van de vorige huisactie is in deze teruglezing niet volledig en exact bevestigd. Maak daarom geen status- of voltooiingsclaim uit deze vervolgread.';
  }
  if (continuation?.invalid === false) {
    if (!boundedHomeContinuationClaimIsQualified(
      continuation.fallbackMessage, continuation,
    )) {
      return 'De teruglezing zelf bewaart de bewezen begrenzing van de vorige huisactie niet veilig. Maak daarom geen status- of voltooiingsclaim uit deze vervolgread.';
    }
    if (!boundedHomeContinuationClaimIsQualified(reply, continuation)) {
      return `De groep is vers teruggelezen, maar de vorige actie bevestigde alleen ${continuation.reachableCount} bereikbare onderdelen en liet ${continuation.unavailableCount} onderdelen onbevestigd. Scheid de actuele groepstaat van die historische begrenzing en claim niet dat alles gelukt is.`;
    }
    return '';
  }
  if (!intent || !toolResults.some((tool) => tool.name === 'bedien_huis')) return '';
  const action = canonicalHomeAction(intent.actie);
  const receipt = homeActionReceipt(intent, toolResults, readbackProven);
  const unboundedCompletion = claimsUnboundedHomeCompletion(reply);
  if (receipt.boundedPartial && (unboundedCompletion
    || !boundedPartialHomeClaimIsQualified(reply, receipt.boundedPartial))) {
    return `De bereikbare onderdelen zijn wel bevestigd, maar ${receipt.boundedPartial.unavailableCount} onderdelen zijn offline. Noem die exacte begrenzing en claim niet dat alles gelukt is.`;
  }
  if (claimsHomeMutation(reply, action) && !receipt.mutationProven) {
    return 'De gewenste huisstatus is mogelijk wel bevestigd, maar je hebt hem niet aantoonbaar zelf veranderd. Zeg wat de gecontroleerde toestand is en schrijf een bestaande toestand niet aan je eigen handeling toe.';
  }
  if (claimsHomeState(reply, action) && !receipt.stateProven) {
    return 'Deze toestand is niet voor precies het gevraagde apparaat bevestigd. Gebruik alleen de exacte teruglezing van dat apparaat.';
  }
  return '';
}

export function isScheduledMonitoringItem(item, now = Date.now(), {
  workId = null, ...monitoring
} = {}) {
  const boundWork = workId != null && Number.isFinite(Number(workId));
  return item?.status === 'open'
    && (!boundWork || Number(item?.id) === Number(workId))
    && monitoringGuardValid({
      args: {
        id: item?.id, bewerking: 'update', hervatten_na: item?.next_wake_ts,
        volgende_stap: item?.next_step,
      },
      workId: boundWork ? workId : item?.id,
      now,
      monitoring,
    });
}

export function scheduledMonitoringWork(toolResults = [], options = {}) {
  const boundWork = options.workId != null && Number.isFinite(Number(options.workId));
  if (options.requireBound === true && !boundWork) return null;
  const now = Number.isFinite(Number(options.now)) ? Number(options.now) : Date.now();
  if (options.contract === 'ContinuityWatch/v1') {
    const sha = String(options.sourceUnderstandingSha256 || '');
    const readIndex = toolResults.findIndex((tool) => tool.ok && tool.verified
      && !tool.partial && !tool.needs_followup && !tool.effect_uncertain
      && tool.semanticBinding?.understandingSha256 === sha
      && tool.semanticBinding?.requirementId === options.sourceReadRequirementId
      && tool.semanticBinding?.capabilityId === 'home.context.read'
      && tool.semanticBinding?.operationId === 'read');
    const workIndex = toolResults.findIndex((tool, index) => index > readIndex
      && tool.name === 'werk_bijhouden' && tool.ok && tool.verified
      && tool.semanticBinding?.understandingSha256 === sha
      && tool.semanticBinding?.requirementId === options.workRequirementId);
    const item = actionData(toolResults[workIndex])?.item;
    const wake = Date.parse(item?.next_wake_ts || '');
    return readIndex >= 0 && workIndex > readIndex && item?.status === 'open'
      && (!boundWork || Number(item.id) === Number(options.workId))
      && Number.isFinite(wake) && wake > now && wake >= Number(options.minWakeTs)
      && wake <= Number(options.maxWakeTs) && String(item.next_step || '').trim()
      && String(item.definition_of_done || '').trim() ? item : null;
  }
  for (const tool of [...toolResults].reverse()) {
    if (tool.name !== 'werk_bijhouden' || !tool.ok || !tool.verified) continue;
    const item = actionData(tool)?.item;
    const operation = String(tool.args?.bewerking || '').toLowerCase();
    if (boundWork) {
      const boundUpdate = ['update', 'bijwerken'].includes(operation)
        && Number(tool.args?.id) === Number(options.workId);
      const boundStart = operation === 'start'
        && Number(item?.id) === Number(options.workId);
      if (!boundUpdate && !boundStart) continue;
    }
    if (isScheduledMonitoringItem(item, now, options)) return item;
  }
  return null;
}

export function turnMonitoringProofOptions(turnContract, understanding, userText, workId) {
  const base = { turnKind: turnContract.kind };
  const currentWorkId = () => Number(typeof workId === 'function' ? workId() : workId);
  if (turnContract.kind === 'continuity') return {
    ...base, ...turnContract.continuity,
    sourceUnderstandingSha256: understanding?.sha256 || null,
    requireBound: true, get workId() { return currentWorkId(); },
  };
  if (turnContract.kind !== 'monitoring') return base;
  return {
    ...base, ...understanding?.monitoring,
    sourceUnderstandingSha256: understanding?.sha256 || null,
    conditions: [...new Set((understanding?.conditions || []).map(({ statement }) => statement))],
    temporalCondition: understanding?.temporal?.mode === 'condition'
      ? understanding.temporal.startSourceSpans
        .map((span) => userText.slice(span.start, span.end)).join(' ') || null : null,
    definitionOfDone: userText, requireBound: true, get workId() { return currentWorkId(); },
  };
}

export function monitoringPromiseFeedback(reply = '', requested = false, toolResults = [], options = {}) {
  if (!requested || scheduledMonitoringWork(toolResults, options)) return '';
  const promises = /\b(?:ik\s+)?(?:houd|hou|blijf|bewaak|monitor|volg|oplett)\w*\b|\bik\s+let\b[^.!?;:]{0,20}\bop\b|\bmeld\s+(?:ik|me)\b|\bik\s+(?:geef|stuur|waarschuw|kijk|controleer|meet|kom)\b.{0,80}\b(?:later|straks|dan|opnieuw|terug|bericht|seintje|tip)|\b(?:staat|is)\b.{0,30}\b(?:gepland|ingepland)\b|\b(?:komt\s+goed|regel\s+ik|doe\s+ik|zorg\s+ik\s+voor|afgesproken)\b/i.test(String(reply || ''));
  return promises
    ? 'Je hebt de vervolgcontrole nog niet duurzaam gepland. Leg eerst een concreet terugkijkmoment, verse meting en eventuele aflevering vast.'
    : '';
}

export function selfRepairClaimFeedback(reply = '', turnKind = '', toolResults = []) {
  const runtimePhases = (Array.isArray(toolResults) ? toolResults : [])
    .filter((tool) => tool?.name === 'beheer_runtime' && tool?.ok === true)
    .map(actionData).map((data) => String(data?.status || '')).filter(Boolean);
  const applied = runtimePhases.includes('committed');
  const live = runtimePhases.some((phase) => ['committed', 'rolled_back'].includes(phase));
  const claimsCoreChange = turnKind === 'self_repair'
    && /\b(?:ik\s+heb\b.{0,80})?(?:gerepareerd|gefixt|opgelost|aangepast|gewijzigd|geïmplementeerd|verbeterd|getest\s+en\s+werkend)\b/i.test(String(reply || ''));
  const claimsLive = turnKind === 'self_repair'
    && /\b(?:draait|werkt|actief|live|opgestart|herstart|in\s+productie)\b/i.test(String(reply || ''));
  return claimsCoreChange && !applied
    ? 'De kandidaat kan onderzocht en beproefd zijn, maar is niet door de root-maintainer toegepast. Benoem in je eigen gewone stem exact de bereikte fase en claim geen productionreparatie.'
    : claimsLive && !live
      ? 'De code staat hoogstens toegepast klaar voor een gecontroleerde herstart; de nieuwe geladen runtime en chatcanary zijn nog niet live bevestigd. Claim dat nog niet.'
    : '';
}

export function autonomousMessageNeedsAccountability(text = '') {
  return ACCOUNTABILITY.test(String(text || ''));
}

export function autonomousMessageNeedsRewrite(text = '') {
  return INTERNAL_LANGUAGE.test(String(text || ''));
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

// Alleen een pure afleverbeslissing. De hoofd-Eva heeft de tekst al geschreven;
// deze grens herschrijft haar stem niet met een tweede model.
export function reviewAutonomousMessage(text = '', { mustDeliver = false } = {}) {
  const draft = String(text || '').trim().slice(0, 4_000);
  if (!draft) return { decision: 'silent', text: '', reason: 'empty' };
  if (autonomousMessageNeedsRewrite(draft)) {
    return { decision: 'rewrite', text: draft, reason: 'internal_language' };
  }
  if (!mustDeliver && !autonomousMessageNeedsAccountability(draft)
    && routineAutonomousMessageCoolingDown(draft)) {
    return { decision: 'silent', text: '', reason: 'routine_contact_cooldown' };
  }
  return { decision: 'send', text: draft, reason: null };
}
