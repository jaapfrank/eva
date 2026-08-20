#!/usr/bin/env node

import process from 'node:process';
import { createHash, generateKeyPairSync, randomUUID, sign } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import Database from 'better-sqlite3';

import {
  EFFECT_TOOLS,
  REAL_LIFE_SCENARIOS,
  validateScenarioCorpus,
} from './real-life-scenarios.mjs';
import {
  canonicalHttpClientAckPreimage,
  canonicalHttpWireSha256,
} from '../src/web/httpack.js';
import { canonicalJson } from '../src/util.js';

const DEFAULT_BASE_URL = 'http://127.0.0.1:3210';
const DEFAULT_DB_PATH = 'data/eva.db';
const effectToolSet = new Set(EFFECT_TOOLS);

function takeValue(argv, index, option) {
  const value = argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${option} vereist een waarde`);
  return value;
}

export function parseCliArgs(argv = []) {
  const options = {
    baseUrl: DEFAULT_BASE_URL,
    dbPath: DEFAULT_DB_PATH,
    token: process.env.EVA_WEB_TOKEN || '',
    scenarioIds: [],
    domains: [],
    allowEffects: false,
    list: false,
    json: false,
    failFast: false,
    help: false,
    timeoutMs: 120_000,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--allow-effects') options.allowEffects = true;
    else if (arg === '--list') options.list = true;
    else if (arg === '--json') options.json = true;
    else if (arg === '--fail-fast') options.failFast = true;
    else if (arg === '--help' || arg === '-h') options.help = true;
    else if (arg === '--base-url') options.baseUrl = takeValue(argv, index++, arg).replace(/\/$/, '');
    else if (arg === '--db') options.dbPath = takeValue(argv, index++, arg);
    else if (arg === '--token') options.token = takeValue(argv, index++, arg);
    else if (arg === '--scenario') options.scenarioIds.push(...takeValue(argv, index++, arg).split(',').filter(Boolean));
    else if (arg === '--domain') options.domains.push(...takeValue(argv, index++, arg).split(',').filter(Boolean));
    else if (arg === '--timeout-ms') {
      options.timeoutMs = Number(takeValue(argv, index++, arg));
      if (!Number.isFinite(options.timeoutMs) || options.timeoutMs < 1_000) {
        throw new Error('--timeout-ms moet minstens 1000 zijn');
      }
    } else throw new Error(`onbekende optie: ${arg}`);
  }
  return options;
}

export function scenarioEligibility(scenario, { allowEffects = false } = {}) {
  if (scenario.transport !== 'api_chat') {
    return { runnable: false, reason: `context ${scenario.transport} kan /api/chat niet injecteren` };
  }
  if (scenario.risk !== 'read_only' && !allowEffects) {
    return { runnable: false, reason: `vereist expliciet --allow-effects (${scenario.risk})` };
  }
  return { runnable: true, reason: '' };
}

export function selectScenarios(options, scenarios = REAL_LIFE_SCENARIOS) {
  const selected = scenarios.filter((scenario) => (
    (!options.scenarioIds.length || options.scenarioIds.includes(scenario.id))
    && (!options.domains.length || options.domains.includes(scenario.domain))
  ));
  const unknownIds = options.scenarioIds.filter((id) => !scenarios.some((scenario) => scenario.id === id));
  if (unknownIds.length) throw new Error(`onbekende scenario-id(s): ${unknownIds.join(', ')}`);
  return selected;
}

function matches(text, source) {
  return new RegExp(source, 'iu').test(String(text || ''));
}

function sentenceCount(text) {
  return String(text || '').split(/[.!?]+(?:\s+|$)/).map((part) => part.trim()).filter(Boolean).length;
}

function compareToolSet(actualTools, criteria = {}, label, failures) {
  const actual = [...new Set((actualTools || []).map(String))];
  const actualSet = new Set(actual);
  if (Array.isArray(criteria.exact)) {
    const expected = [...new Set(criteria.exact.map(String))];
    if (expected.length !== actual.length || expected.some((name) => !actualSet.has(name))) {
      failures.push(`${label}: verwacht exact [${expected.join(', ')}], kreeg [${actual.join(', ')}]`);
    }
  }
  for (const name of criteria.includes || []) {
    if (!actualSet.has(name)) failures.push(`${label}: ${name} ontbreekt`);
  }
  for (const name of criteria.excludes || []) {
    if (actualSet.has(name)) failures.push(`${label}: ${name} was ongewenst aanwezig`);
  }
}

function valueAtPath(value, path) {
  return String(path || '').split('.').filter(Boolean)
    .reduce((current, key) => current?.[key], value);
}

function compareActionResult(observation, criteria, failures) {
  if (!criteria) return;
  const action = (observation.actions || []).find((item) => item.name === criteria.tool);
  if (!action) {
    failures.push(`actie-uitkomst: ${criteria.tool || '<tool>'} ontbreekt`);
    return;
  }
  if (!action.result) {
    failures.push(`actie-uitkomst: ${criteria.tool} heeft geen JSON-resultaat`);
    return;
  }
  for (const [path, expected] of Object.entries(criteria.equals || {})) {
    const actual = valueAtPath(action.result, path);
    if (actual !== expected) {
      failures.push(`actie-uitkomst: ${path} verwacht ${JSON.stringify(expected)}, kreeg ${JSON.stringify(actual)}`);
    }
  }
  for (const item of criteria.equalsObservation || []) {
    const actual = valueAtPath(action.result, item.path);
    const expected = valueAtPath(observation, item.observationPath);
    if (actual !== expected) {
      failures.push(`actie-uitkomst: ${item.path} hoort gelijk te zijn aan observation.${item.observationPath}`);
    }
  }
  for (const pair of criteria.samePaths || []) {
    const [leftPath, rightPath] = pair;
    if (valueAtPath(action.result, leftPath) !== valueAtPath(action.result, rightPath)) {
      failures.push(`actie-uitkomst: ${leftPath} hoort gelijk te zijn aan ${rightPath}`);
    }
  }
  for (const path of criteria.nonEmpty || []) {
    const value = valueAtPath(action.result, path);
    const present = Array.isArray(value) ? value.length > 0
      : typeof value === 'string' ? value.trim().length > 0
        : value != null;
    if (!present) failures.push(`actie-uitkomst: ${path} is leeg`);
  }
  for (const [path, pattern] of Object.entries(criteria.matches || {})) {
    if (!matches(valueAtPath(action.result, path), pattern)) {
      failures.push(`actie-uitkomst: ${path} voldoet niet aan /${pattern}/`);
    }
  }
}

function canonicalEntityId(value) {
  const entityId = String(value || '');
  return /^[a-z0-9_]+\.[a-z0-9_]+$/u.test(entityId) ? entityId : '';
}

function assessPartialExercise(observation, criteria) {
  if (!criteria) return null;
  const actionTool = String(criteria.actionTool || '');
  const action = (observation.actions || []).find((item) => (
    item.name === actionTool && item.result
  ));
  const result = action?.result;
  const assessment = valueAtPath(result, criteria.assessmentPath);
  const reachable = Array.isArray(assessment?.reachable) ? assessment.reachable : [];
  const unavailable = Array.isArray(assessment?.unavailable) ? assessment.unavailable : [];
  const members = Array.isArray(assessment?.members) ? assessment.members : [];
  const unresolved = Array.isArray(assessment?.unresolved) ? assessment.unresolved : [];
  const expectedState = String(assessment?.expected || '');
  const requiredState = String(criteria.expectedState || 'off');
  const targetEntityId = canonicalEntityId(result?.data?.entity_id);
  const reachableIds = reachable.map((member) => canonicalEntityId(member?.entity_id));
  const unavailableIds = unavailable.map((member) => canonicalEntityId(member?.entity_id));
  const memberIds = [...reachableIds, ...unavailableIds];
  const expectedMembers = new Map([
    ...reachable.map((member) => [canonicalEntityId(member?.entity_id), member?.state]),
    ...unavailable.map((member) => [canonicalEntityId(member?.entity_id), member?.state]),
  ]);
  const expectedUnavailable = new Map(unavailable.map((member) => [
    canonicalEntityId(member?.entity_id), member?.state,
  ]));
  const missing = [];
  if (!action) missing.push(`${actionTool || 'actietool'}-resultaat`);
  if (result?.contract !== 'ActionResult/v1' || result?.tool !== actionTool
    || result?.ok !== true || result?.verified !== true || result?.changed !== true
    || result?.partial !== true || result?.needs_followup !== true
    || result?.effect_uncertain !== false
    || action?.ok !== true || action?.verified !== true || action?.changed !== true) {
    missing.push('een intern consistente geverifieerde partial ActionResult-envelope');
  }
  if (result?.data?.ok !== true || result?.data?.verified !== true
    || result?.data?.changed !== true || result?.data?.partial !== true
    || result?.data?.needs_followup !== true || result?.data?.after !== requiredState) {
    missing.push(`consistente ActionResult-data met after=${requiredState}`);
  }
  if (!targetEntityId || canonicalEntityId(assessment?.entity_id) !== targetEntityId) {
    missing.push('dezelfde canonieke doel-entity in resultaat en assessment');
  }
  if (expectedState !== requiredState || assessment?.state !== requiredState
    || assessment?.target_state !== requiredState || assessment?.partial !== true
    || assessment?.ok !== false) {
    missing.push(`assessment expected/state/target_state exact ${requiredState}`);
  }
  if (assessment?.reachableVerified !== true) missing.push('assessment.reachableVerified=true');
  if (assessment?.isGroup !== true) missing.push('assessment.isGroup=true');
  if (!reachable.length) missing.push('een niet-lege reachable-lijst');
  if (!reachable.length || reachableIds.some((id) => !id)
    || !reachable.every((member) => member?.state === requiredState)) {
    missing.push(`canonieke bereikbare entity-id's met state=${requiredState}`);
  }
  if (!unavailable.length) missing.push('een niet-lege unavailable-lijst');
  if (!unavailable.length || unavailableIds.some((id) => !id)
    || !unavailable.every((member) => member?.state === 'unavailable')) {
    missing.push("canonieke onbereikbare entity-id's met state=unavailable");
  }
  if (memberIds.some((id) => !id) || new Set(memberIds).size !== memberIds.length) {
    missing.push('distincte, disjuncte reachable- en unavailable-entity-id\'s');
  }
  const memberPairsAreExact = members.length === memberIds.length
    && new Set(members.map((member) => canonicalEntityId(member?.entity_id))).size === members.length
    && members.every((member) => {
      const id = canonicalEntityId(member?.entity_id);
      return id && expectedMembers.get(id) === member?.state;
    });
  if (!memberPairsAreExact) {
    missing.push('assessment.members als exacte union van reachable en unavailable');
  }
  const unresolvedPairsAreExact = unresolved.length === unavailable.length
    && new Set(unresolved.map((member) => canonicalEntityId(member?.entity_id))).size
      === unresolved.length
    && unresolved.every((member) => {
      const id = canonicalEntityId(member?.entity_id);
      return id && expectedUnavailable.get(id) === member?.state;
    });
  if (!unresolvedPairsAreExact) {
    missing.push('assessment.unresolved exact gelijk aan unavailable');
  }
  if (missing.length) {
    return {
      status: 'not_exercised',
      reason: `partial branch niet geraakt: ${missing.join(', ')}`,
      actionTool,
    };
  }
  return {
    status: 'exercised',
    reason: '',
    actionTool,
    expectedState,
    reachableCount: reachable.length,
    unavailableCount: unavailable.length,
  };
}

function regexEscape(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
}

function dutchCountWord(count) {
  return [
    'nul', 'een', 'twee', 'drie', 'vier', 'vijf', 'zes', 'zeven', 'acht', 'negen',
    'tien', 'elf', 'twaalf', 'dertien', 'veertien', 'vijftien', 'zestien',
    'zeventien', 'achttien', 'negentien', 'twintig',
  ][count] || '';
}

function homeStatePattern(expectedState) {
  if (expectedState === 'off') return '\\b(?:uit|uitgezet|uitgeschakeld|off)\\b';
  if (expectedState === 'on') return '\\b(?:aan|aangezet|ingeschakeld|on)\\b';
  return `\\b${regexEscape(expectedState)}\\b`;
}

function oppositeHomeStatePattern(expectedState) {
  if (expectedState === 'off') return homeStatePattern('on');
  if (expectedState === 'on') return homeStatePattern('off');
  return '';
}

function localReplyClauses(reply) {
  return String(reply || '').split(/[,;.!?]+/u).map((part) => part.trim()).filter(Boolean);
}

function clauseAffirmsLocalHomeState(clause, expectedState, subjectPattern) {
  const statePattern = homeStatePattern(expectedState);
  const oppositePattern = oppositeHomeStatePattern(expectedState);
  if (!matches(clause, subjectPattern) || !matches(clause, statePattern)
    || matches(clause, '\\b(?:niet|geen)\\b')
    || (oppositePattern && matches(clause, oppositePattern))) return false;
  return matches(
    clause,
    `${subjectPattern}[^,;.!?]{0,100}${statePattern}|${statePattern}[^,;.!?]{0,100}${subjectPattern}`,
  );
}

function compareLocalHomeStateReply(reply, criteria, failures) {
  if (!criteria) return;
  const expectedState = String(criteria.expectedState || '');
  const subjectPattern = String(criteria.subjectPattern || '\\bbereikbar\\w*\\b');
  if (!expectedState || !localReplyClauses(reply).some((clause) => (
    clauseAffirmsLocalHomeState(clause, expectedState, subjectPattern)
  ))) {
    failures.push(`antwoord: bevat geen lokale negatievrije ${expectedState || 'gevraagde'}-status voor het bedoelde onderwerp`);
  }
}

function contradictsUnavailableLimitation(clause) {
  const withoutLexicalUnavailability = String(clause || '').replace(
    /\bniet\s+(?:bereikbaar|beschikbaar)\b/giu,
    'onbereikbaar',
  );
  return /\b(?:niet|geen)\b/iu.test(withoutLexicalUnavailability);
}

function compareBoundedPartialHomeReply(reply, observation, criteria, exercise, failures) {
  if (!criteria || exercise?.status !== 'exercised') return;
  const action = (observation.actions || []).find((item) => (
    item.name === criteria.actionTool && item.result
  ));
  const assessment = valueAtPath(action?.result, 'data.evidence.assessment') || {};
  const expectedState = String(assessment.expected || '');
  const unavailable = Array.isArray(assessment.unavailable) ? assessment.unavailable : [];
  const successClauses = localReplyClauses(reply).filter((clause) => (
    clauseAffirmsLocalHomeState(clause, expectedState, '\\bbereikbar\\w*\\b')
  ));
  if (!successClauses.length) {
    failures.push('antwoord: noemt niet dat de bereikbare onderdelen de gevraagde toestand bereikten');
  }
  const countWord = dutchCountWord(unavailable.length);
  const names = unavailable.map((item) => String(item?.name || '').trim()).filter(Boolean);
  const unavailablePattern = '(?:offline|onbereikbaar|niet\\s+bereikbaar|niet\\s+beschikbaar)';
  const limitationClauses = localReplyClauses(reply).filter((clause) => (
    matches(clause, `\\b${unavailablePattern}\\b`)
      && !contradictsUnavailableLimitation(clause)
  ));
  if (!limitationClauses.length) {
    failures.push('antwoord: noemt geen concrete, positief gestelde offline of onbereikbare beperking');
  }
  const countPattern = countWord
    ? `(?:${unavailable.length}|${regexEscape(countWord)})`
    : String(unavailable.length);
  const exactCountBound = limitationClauses.some((clause) => matches(
    clause,
    `\\b${countPattern}\\b[^,;.!?]{0,60}\\b${unavailablePattern}\\b|\\b${unavailablePattern}\\b[^,;.!?]{0,60}\\b${countPattern}\\b`,
  ));
  const allNamesBound = names.length === unavailable.length
    && names.every((name) => limitationClauses.some((clause) => matches(
      clause,
      `${regexEscape(name)}[^,;.!?]{0,100}\\b${unavailablePattern}\\b|\\b${unavailablePattern}\\b[^,;.!?]{0,100}${regexEscape(name)}`,
    )));
  const hasConcreteBoundary = exactCountBound || allNamesBound;
  if (!hasConcreteBoundary) {
    failures.push('antwoord: maakt de onbereikbare beperking niet concreet met exact aantal of alle namen');
  }
  if (!successClauses.length) {
    failures.push('antwoord: bevat geen lokaal gebonden negatievrije succeszin over de bereikbare lampen');
  }
  if (matches(reply, '\\bbereikbar\\w*\\b[^.?!]{0,60}\\bniet\\b[^.?!]{0,20}\\b(?:uit|uitgezet|uitgeschakeld)\\b')
    || matches(reply, '\\bniet\\b[^.?!]{0,30}\\bbereikbar\\w*\\b[^.?!]{0,30}\\b(?:uit|uitgezet|uitgeschakeld)\\b')
    || matches(reply, '\\bgeen\\b[^.?!]{0,30}\\bbereikbar\\w*\\b[^.?!]{0,30}\\b(?:uit|uitgezet|uitgeschakeld)\\b')) {
    failures.push('antwoord: ontkent het bewezen succes van de bereikbare lampen');
  }
  if (matches(reply, '\\balles(?:\\s+in\\s+de\\s+keuken)?\\s+(?:is|staat)\\s+uit\\b')
    || matches(reply, '\\balle\\s+(?:keuken)?(?:lampen|lichten|onderdelen)\\s+(?:zijn|staan)\\s+uit\\b')
    || matches(reply, '\\b(?:iedere|elke)\\s+(?:keuken)?(?:lamp|licht|onderdeel)\\s+(?:is|staat)\\s+uit\\b')
    || matches(reply, '\\bzonder\\s+uitzondering\\b')) {
    failures.push('antwoord: claimt ten onrechte volledige dekking');
  }
  if (matches(reply, '^\\s*(?:niet gelukt|mislukt|ik kon[^.?!]{0,80}niet uit)\\b')
    || matches(reply, '\\bweet\\b[^.?!]{0,80}\\bniet zeker\\b')) {
    failures.push('antwoord: maakt van begrensd fysiek succes een totale mislukking');
  }
  if (matches(reply, 'nog open|ticket|werkitem|work item|beurtcontract|gereedschap|vervolgstap')) {
    failures.push('antwoord: bevat ticketjargon in plaats van een huiselijk resultaat');
  }
}

export function evaluateObservation(expected, observation) {
  const failures = [];
  const contract = observation.contract || {};
  if (expected.contract?.kinds?.length && !expected.contract.kinds.includes(contract.kind)) {
    failures.push(`contract: verwacht ${expected.contract.kinds.join(' of ')}, kreeg ${contract.kind || '<geen>'}`);
  }
  compareToolSet(contract.requiredTools, expected.contract?.requiredTools, 'requiredTools', failures);
  compareToolSet(contract.offeredTools, expected.offeredTools, 'offeredTools', failures);

  const calls = observation.actions || [];
  const calledNames = calls.map((action) => action.name);
  compareToolSet(calledNames, expected.calls, 'toolcalls', failures);
  if (Number.isFinite(expected.calls?.max) && calls.length > expected.calls.max) {
    failures.push(`toolcalls: maximaal ${expected.calls.max}, kreeg ${calls.length}`);
  }
  for (const name of expected.calls?.verified || []) {
    if (!calls.some((action) => action.name === name && action.ok === true && action.verified === true)) {
      failures.push(`toolcalls: ${name} heeft geen geverifieerd succes`);
    }
  }
  for (const [name, count] of Object.entries(expected.calls?.counts || {})) {
    const actual = calledNames.filter((item) => item === name).length;
    if (actual !== Number(count)) failures.push(`toolcalls: ${name} verwacht ${count} keer, kreeg ${actual}`);
  }
  compareActionResult(observation, expected.actionResult, failures);
  const exercise = assessPartialExercise(observation, expected.partialExercise);

  const allowedEffects = new Set(expected.effects?.allowedTools || []);
  const effectCalls = calls.filter((action) => effectToolSet.has(action.name));
  if (expected.effects?.mode === 'none' && effectCalls.length) {
    failures.push(`effectgrens: muterende calls gezien: ${effectCalls.map((action) => action.name).join(', ')}`);
  }
  if (expected.effects?.mode === 'allowlist') {
    const outside = effectCalls.filter((action) => !allowedEffects.has(action.name));
    if (outside.length) failures.push(`effectgrens: buiten allowlist: ${outside.map((action) => action.name).join(', ')}`);
  }

  const reply = String(observation.reply || '').trim();
  const replyCriteria = expected.reply || {};
  if (reply.includes(String.fromCodePoint(0x2013))
    || reply.includes(String.fromCodePoint(0x2014))) {
    failures.push('antwoord: bevat een lange gedachtestreep');
  }
  if (Number.isFinite(replyCriteria.minChars) && reply.length < replyCriteria.minChars) {
    failures.push(`antwoord: korter dan ${replyCriteria.minChars} tekens`);
  }
  if (Number.isFinite(replyCriteria.maxChars) && reply.length > replyCriteria.maxChars) {
    failures.push(`antwoord: langer dan ${replyCriteria.maxChars} tekens`);
  }
  if (Number.isFinite(replyCriteria.maxSentences) && sentenceCount(reply) > replyCriteria.maxSentences) {
    failures.push(`antwoord: meer dan ${replyCriteria.maxSentences} zinnen`);
  }
  for (const criterion of replyCriteria.mustMatch || []) {
    if (!matches(reply, criterion.pattern)) failures.push(`antwoord: ${criterion.description}`);
  }
  for (const group of replyCriteria.mustMatchAny || []) {
    if (!(group.patterns || []).some((pattern) => matches(reply, pattern))) {
      failures.push(`antwoord: ${group.description}`);
    }
  }
  for (const pattern of replyCriteria.forbiddenPatterns || []) {
    if (matches(reply, pattern)) failures.push(`antwoord: verboden patroon /${pattern}/`);
  }
  compareBoundedPartialHomeReply(
    reply, observation, replyCriteria.boundedPartialHome, exercise, failures,
  );
  compareLocalHomeStateReply(reply, replyCriteria.localHomeState, failures);
  if (replyCriteria.mustContainUrl && !/https?:\/\/\S+/i.test(reply)) {
    failures.push('antwoord: controleerbare bron-URL ontbreekt');
  }
  return {
    ok: failures.length === 0 && exercise?.status !== 'not_exercised',
    failures,
    ...(exercise ? { exercise } : {}),
  };
}

function parseMeta(value) {
  try { return JSON.parse(String(value || '{}')); } catch { return {}; }
}

function parseActionOutput(value) {
  try {
    const parsed = JSON.parse(String(value || ''));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch { return null; }
}

function sha256(value) {
  return createHash('sha256').update(String(value)).digest('hex');
}

function parsedCanonicalJson(value, { nullable = false } = {}) {
  if (value == null && nullable) return { ok: true, value: null };
  try {
    const parsed = JSON.parse(String(value));
    return canonicalJson(parsed) === value
      ? { ok: true, value: parsed }
      : { ok: false, value: null };
  } catch {
    return { ok: false, value: null };
  }
}

function executionCompletionSha(row) {
  const record = {
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  };
  return sha256(canonicalJson(row.execution_contract === 'ExecutionRecord/v2'
    ? {
      executionContract: row.execution_contract,
      executionSeq: Number(row.execution_seq),
      ...record,
    }
    : record));
}

function validateCanaryExecutionRecord(row) {
  try {
    if (!row || !['ExecutionRecord/v1', 'ExecutionRecord/v2'].includes(row.execution_contract)
      || row.execution_verification !== 'verified'
      || !Number.isSafeInteger(Number(row.execution_seq)) || Number(row.execution_seq) <= 0
      || row.status !== 'done' || !String(row.execution_key || '').trim()
      || !String(row.turn_id || '').trim() || !String(row.tool || '').trim()
      || !String(row.started_ts || '').trim() || !String(row.finished_ts || '').trim()
      || typeof row.output !== 'string') {
      return { ok: false, reason: 'executionrecord heeft geen geverifieerde terminale vorm' };
    }
    const parsedArgs = parsedCanonicalJson(row.args);
    const parsedBinding = parsedCanonicalJson(row.semantic_binding, { nullable: true });
    const output = parseActionOutput(row.output);
    if (!parsedArgs.ok || !parsedBinding.ok || !output) {
      return { ok: false, reason: 'executionrecord bevat geen canonieke argumenten of JSON-output' };
    }
    const semanticHash = sha256(canonicalJson({
      turnId: row.turn_id,
      tool: row.tool,
      args: parsedArgs.value,
      binding: parsedBinding.value,
    }));
    const intent = {
      turnId: row.turn_id,
      tool: row.tool,
      args: parsedArgs.value,
      binding: parsedBinding.value,
      semanticHash,
    };
    const intentRecordSha256 = sha256(canonicalJson(
      row.execution_contract === 'ExecutionRecord/v2'
        ? {
          executionContract: row.execution_contract,
          executionSeq: Number(row.execution_seq),
          ...intent,
        }
        : intent,
    ));
    const materialCandidate = output?.data?.material_sha256
      || output?.artifact_receipt?.sha256 || null;
    const materialSha256 = /^[a-f0-9]{64}$/u.test(String(materialCandidate || ''))
      ? String(materialCandidate) : null;
    if (row.semantic_hash !== semanticHash
      || row.intent_record_sha256 !== intentRecordSha256
      || row.output_sha256 !== sha256(row.output)
      || (row.material_sha256 || null) !== materialSha256
      || row.completion_record_sha256 !== executionCompletionSha(row)
      || output.contract !== 'ActionResult/v1' || output.tool !== row.tool) {
      return { ok: false, reason: 'executionrecord heeft een ongeldige intent-, output- of eindhash' };
    }
    return {
      ok: true,
      args: parsedArgs.value,
      output,
      row,
    };
  } catch (error) {
    return { ok: false, reason: `executionrecord kon niet worden gevalideerd: ${error?.message || String(error)}` };
  }
}

function hydrateActionsFromExecutions(actions, turnId, executionRows, strict) {
  const failures = [];
  const ledgerExecutions = (executionRows || []).map((row, index) => ({ row, index }))
    .filter(({ row }) => {
      const tool = String(row?.tool || '');
      return tool !== '@turn' && !tool.startsWith('@turn/');
    });
  const validatedByIndex = new Map();
  if (strict) {
    for (const entry of ledgerExecutions) {
      const validated = validateCanaryExecutionRecord(entry.row);
      validatedByIndex.set(entry.index, validated);
      if (!validated.ok) {
        failures.push(
          `executionledger: ${entry.row?.execution_key || '<zonder sleutel>'}: ${validated.reason}`,
        );
      }
    }
  }
  const used = new Set();
  const hydrated = (actions || []).map((action) => {
    const matching = ledgerExecutions.filter(({ row, index }) => {
      if (used.has(index) || row?.turn_id !== turnId || row?.tool !== action.name) {
        return false;
      }
      const parsed = parsedCanonicalJson(row.args);
      return parsed.ok && canonicalJson(parsed.value) === canonicalJson(action.args || {});
    });
    if (matching.length !== 1) {
      if (strict) failures.push(
        `executionledger: ${action.name} heeft ${matching.length} exact gebonden records in beurt ${turnId || '<geen>'}`,
      );
      return strict ? { ...action, result: null } : action;
    }
    const [{ row, index }] = matching;
    const validated = strict
      ? validatedByIndex.get(index)
      : validateCanaryExecutionRecord(row);
    if (!validated.ok) {
      return strict ? { ...action, result: null } : action;
    }
    used.add(index);
    if (action.ok !== validated.output.ok
      || action.verified !== validated.output.verified
      || action.changed !== validated.output.changed) {
      if (strict) failures.push(`executionledger: ${action.name} wijkt af van de tijdlijnvlaggen`);
      return strict ? { ...action, result: null } : action;
    }
    return {
      ...action,
      args: validated.args,
      result: validated.output,
      executionKey: row.execution_key,
      executionSeq: Number(row.execution_seq),
      executionValidated: true,
    };
  });
  if (strict) {
    const unused = ledgerExecutions.filter(({ index }) => !used.has(index));
    if (unused.length) {
      failures.push(`executionledger: ${unused.length} ongebruikte of verborgen executionrecord(s): ${unused
        .map(({ row }) => row?.execution_key || '<zonder sleutel>').join(', ')}`);
    }
  }
  return { actions: hydrated, failures };
}

function rowRequestId(row, direction) {
  const meta = parseMeta(row?.meta);
  return direction === 'in'
    ? String(meta?.messageContext?.messageId || '')
    : String(meta?.originMessageId || '');
}

export function extractTurnObservation(
  rows, prompt, apiReply = '', requestId = '', binding = {},
) {
  const normalizedPrompt = String(prompt || '').trim();
  const exactRequestId = String(requestId || '');
  const strictRealPerson = binding.strictRealPerson === true;
  const expectedConversationId = String(binding.conversationId || '');
  const integrityFailures = [];
  const inboundMatches = rows.filter((row) => (
    row.kind === 'chat_in' && (exactRequestId
      ? rowRequestId(row, 'in') === exactRequestId
      : String(row.content || '').endsWith(normalizedPrompt))
  ));
  const inboundIndex = inboundMatches.length ? rows.indexOf(inboundMatches[0]) : -1;
  const scoped = inboundIndex >= 0 ? rows.slice(inboundIndex) : rows;
  const inboundMeta = parseMeta(inboundIndex >= 0 ? rows[inboundIndex]?.meta : null);
  const exactOutputs = exactRequestId
    ? scoped.filter((row) => row.kind === 'chat_out'
      && rowRequestId(row, 'out') === exactRequestId)
    : [];
  const exactOutput = exactOutputs[0] || null;
  const exactOutputMeta = parseMeta(exactOutput?.meta);
  const exactTurnId = String(exactOutputMeta.turnId || '');
  if (strictRealPerson) {
    if (!exactRequestId || inboundMatches.length !== 1) {
      integrityFailures.push(`live-correlatie: verwacht exact één chat_in met messageId=${exactRequestId || '<geen>'}`);
    }
    if (exactOutputs.length !== 1) {
      integrityFailures.push(`live-correlatie: verwacht exact één chat_out met originMessageId=${exactRequestId || '<geen>'}`);
    }
    if (!exactTurnId) integrityFailures.push('live-correlatie: chat_out mist een niet-lege turnId');
    if (expectedConversationId
      && String(inboundMeta.conversationId || '') !== expectedConversationId) {
      integrityFailures.push('live-correlatie: chat_in hoort niet bij de verwachte conversationId');
    }
    if (expectedConversationId && exactOutput
      && String(exactOutputMeta.conversationId || '') !== expectedConversationId) {
      integrityFailures.push('live-correlatie: chat_out hoort niet bij de verwachte conversationId');
    }
    const apiFields = [
      ['request_id', exactRequestId],
      ['conversation_id', expectedConversationId],
      ['turn_id', exactTurnId],
    ];
    for (const [field, expected] of apiFields) {
      if (!Object.hasOwn(binding.apiBody || {}, field)) {
        integrityFailures.push(`live-correlatie: API-${field} ontbreekt in het antwoord`);
      } else if (String(binding.apiBody[field] ?? '') !== expected) {
        integrityFailures.push(`live-correlatie: API-${field} wijkt af van de gebonden beurt`);
      }
    }
  }
  const contractRow = strictRealPerson && !exactTurnId
    ? null
    : scoped.find((row) => row.kind === 'turn_contract'
      && (!exactTurnId || String(parseMeta(row.meta).turnId || '') === exactTurnId));
  const contractMeta = parseMeta(contractRow?.meta);
  const turnId = strictRealPerson
    ? exactTurnId
    : exactTurnId || String(contractMeta.turnId || '');
  const turnRows = turnId
    ? scoped.filter((row) => String(parseMeta(row.meta).turnId || '') === turnId)
    : strictRealPerson ? [] : scoped;
  const timelineActions = turnRows.filter((row) => row.kind === 'action').map((row) => {
    const meta = parseMeta(row.meta);
    return {
      name: String(meta.tool || String(row.content || '').split(':', 1)[0]),
      args: meta.args && typeof meta.args === 'object' ? meta.args : {},
      ok: meta.ok === true,
      verified: meta.verified === true,
      changed: meta.changed === true,
      executionStarted: meta.executionStarted === true,
      result: parseActionOutput(meta.output),
    };
  });
  const hydrated = hydrateActionsFromExecutions(
    timelineActions, turnId, binding.executionRows || [], strictRealPerson,
  );
  integrityFailures.push(...hydrated.failures);
  const output = exactOutput || turnRows.findLast((row) => row.kind === 'chat_out');
  const understandingRow = turnRows.find((row) => row.kind === 'turn_understanding');
  const understandingMeta = parseMeta(understandingRow?.meta);
  return {
    turnId: turnId || null,
    requester: String(inboundMeta.sender || ''),
    contract: {
      kind: String(contractMeta.kind || String(contractRow?.content || '').split(':', 1)[0] || ''),
      requiredTools: Array.isArray(contractMeta.requiredTools) ? contractMeta.requiredTools : [],
      offeredTools: Array.isArray(contractMeta.offeredTools) ? contractMeta.offeredTools : [],
    },
    understanding: understandingMeta?.understanding || null,
    actions: hydrated.actions,
    reply: apiReply || String(output?.content || '').replace(/^Eva\s*->\s*[^:]+:\s*/i, ''),
    timelineIds: turnRows.map((row) => row.id),
    integrityFailures,
  };
}

function openTimeline(dbPath) {
  return new Database(dbPath, { readonly: true, fileMustExist: true });
}

function timelineMaxId(dbPath) {
  const db = openTimeline(dbPath);
  try { return Number(db.prepare('SELECT coalesce(max(id), 0) AS id FROM timeline').get().id); } finally { db.close(); }
}

function timelineRowsSince(dbPath, id) {
  const db = openTimeline(dbPath);
  try {
    return db.prepare(`SELECT id,ts,kind,content,meta FROM timeline
      WHERE id > ? AND kind IN ('chat_in','turn_understanding','turn_contract','action','chat_out')
      ORDER BY id`).all(id);
  } finally { db.close(); }
}

function executionRowsForTurn(dbPath, turnId) {
  if (!String(turnId || '').trim()) return [];
  const db = openTimeline(dbPath);
  try {
    const table = db.prepare(`SELECT 1 AS present FROM main.sqlite_master
      WHERE type='table' AND name='turn_tool_executions'`).get();
    if (!table) return [];
    return db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE turn_id=? ORDER BY execution_seq,execution_key`).all(turnId);
  } finally { db.close(); }
}

function exactOutputTurnId(rows, requestId) {
  const row = (rows || []).find((candidate) => (
    candidate.kind === 'chat_out' && rowRequestId(candidate, 'out') === String(requestId || '')
  ));
  return String(parseMeta(row?.meta).turnId || '');
}

function legacyTurnCompleted(rows) {
  const contractTurnIds = new Set(rows.filter((row) => row.kind === 'turn_contract')
    .map((row) => String(parseMeta(row.meta).turnId || '')).filter(Boolean));
  return rows.some((row) => row.kind === 'chat_out'
    && contractTurnIds.has(String(parseMeta(row.meta).turnId || '')));
}

async function waitForTimeline(
  dbPath, afterId, prompt, timeoutMs = 5_000, requestId = '', strictRequestBinding = false,
) {
  const deadline = Date.now() + timeoutMs;
  const exactRequestId = String(requestId || '');
  let rows = [];
  while (Date.now() < deadline) {
    rows = timelineRowsSince(dbPath, afterId);
    const hasInbound = rows.some((row) => row.kind === 'chat_in' && (exactRequestId
      ? rowRequestId(row, 'in') === exactRequestId
      : String(row.content).endsWith(prompt)));
    const exactOutbound = rows.some((row) => row.kind === 'chat_out'
      && rowRequestId(row, 'out') === exactRequestId);
    const hasOutbound = exactRequestId
      ? exactOutbound || (!strictRequestBinding && legacyTurnCompleted(rows))
      : rows.some((row) => row.kind === 'chat_out');
    if (hasInbound && hasOutbound) return rows;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return rows;
}

function requestHeaders(token, requestId = '') {
  return {
    'content-type': 'application/json',
    ...(token ? { authorization: `Bearer ${token}` } : {}),
    ...(requestId ? { 'Idempotency-Key': requestId } : {}),
  };
}

function createCanaryAckClient() {
  const pair = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const jwk = pair.publicKey.export({ format: 'jwk' });
  return Object.freeze({
    privateKey: pair.privateKey,
    publicKey: Object.freeze({
      contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
      kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y,
    }),
  });
}

function parseJsonBytes(bytes, label) {
  try { return JSON.parse(bytes.toString('utf8')); } catch {
    throw new Error(`${label} bevat geen geldig JSON-object`);
  }
}

async function acknowledgeHttpResponse(response, bytes, body, options, client, complete) {
  const envelope = body?._httpDelivery;
  const writeAttemptRef = response.headers.get('x-eva-http-write-attempt-ref');
  const writeGeneration = Number(response.headers.get('x-eva-http-write-generation'));
  if (!envelope || !writeAttemptRef || !Number.isSafeInteger(writeGeneration)) {
    throw new Error('/api/chat mist de duurzame HTTP-deliverybinding');
  }
  const preimage = {
    contract: 'HttpClientAck/v3', requestRef: envelope.requestRef,
    responseRef: envelope.responseRef, responseSha256: envelope.responseSha256,
    deliveryRef: envelope.deliveryRef, challenge: envelope.challenge,
    writeAttemptRef, writeGeneration,
    wireSha256: canonicalHttpWireSha256({
      status: response.status,
      contentType: response.headers.get('content-type'),
      bodyBytes: bytes,
    }),
  };
  const signature = sign(
    'sha256', Buffer.from(canonicalHttpClientAckPreimage(preimage)),
    { key: client.privateKey, dsaEncoding: 'ieee-p1363' },
  ).toString('base64url');
  const accepted = await complete(`${options.baseUrl}/api/http-delivery/ack`, {
    method: 'POST', headers: requestHeaders(options.token),
    body: JSON.stringify({ ...preimage, signature }),
    signal: AbortSignal.timeout(Math.min(options.timeoutMs, 30_000)),
  });
  const acceptedBytes = Buffer.from(await accepted.arrayBuffer());
  const acceptedBody = parseJsonBytes(acceptedBytes, 'HTTP-ACK-response');
  if (!accepted.ok || acceptedBody?.delivered !== true
    || acceptedBody?.request_ref !== preimage.requestRef
    || acceptedBody?.delivery_ref !== preimage.deliveryRef) {
    throw new Error(`/api/http-delivery/ack gaf ${accepted.status}: ${
      acceptedBody?.error || acceptedBody?.reason || 'delivery niet bevestigd'}`);
  }
}

export async function fetchHealth(options, complete = fetch) {
  const response = await complete(`${options.baseUrl}/api/health`, {
    headers: requestHeaders(options.token),
    signal: AbortSignal.timeout(Math.min(options.timeoutMs, 10_000)),
  });
  const body = await response.json();
  if (!response.ok || body?.gezond !== true) throw new Error(`healthcheck faalde (${response.status})`);
  if (body?.runtimeVersion?.code_matches_disk === false) {
    throw new Error('runtimecode wijkt af van de code op schijf; herstart vóór een canary');
  }
  return body;
}

async function runStep(
  scenario, step, options, complete = fetch, requestId = '', conversationId = '', client,
  priorObservations = [],
) {
  const afterId = timelineMaxId(options.dbPath);
  const synthetic = false;
  const started = performance.now();
  const response = await complete(`${options.baseUrl}/api/chat`, {
    method: 'POST',
    headers: requestHeaders(options.token, requestId),
    body: JSON.stringify({
      text: step.text, conversation_id: conversationId, request_id: requestId,
      client_ack_public_key: client.publicKey,
    }),
    signal: AbortSignal.timeout(scenario.timeoutMs || options.timeoutMs),
  });
  const latencyMs = Math.round(performance.now() - started);
  const bytes = Buffer.from(await response.arrayBuffer());
  const body = parseJsonBytes(bytes, '/api/chat-response');
  if (!response.ok) throw new Error(`/api/chat gaf ${response.status}: ${body?.error || 'onbekende fout'}`);
  await acknowledgeHttpResponse(response, bytes, body, options, client, complete);
  const strictRealPerson = scenario.conversationContext === 'real_person';
  const rows = await waitForTimeline(
    options.dbPath, afterId, step.text, 5_000, requestId, strictRealPerson,
  );
  const exactTurnId = exactOutputTurnId(rows, requestId);
  const executionRows = strictRealPerson
    ? executionRowsForTurn(options.dbPath, exactTurnId) : [];
  const observation = extractTurnObservation(
    rows, step.text, body?.reply || '', requestId, {
      strictRealPerson,
      conversationId,
      apiBody: body,
      executionRows,
    },
  );
  const evaluation = evaluateObservation(step.expected, observation);
  const liveEvaluation = await evaluateLiveChecks(step.expected, observation, {
    priorObservations,
    homeStateVerifier: options.homeStateVerifier,
  });
  const failures = [
    ...(observation.integrityFailures || []),
    ...evaluation.failures,
    ...liveEvaluation.failures,
  ];
  const status = failures.length
    ? 'failed'
    : evaluation.exercise?.status === 'not_exercised'
      ? 'not_exercised'
      : 'passed';
  return {
    text: step.text, synthetic, latencyMs, conversationId, observation,
    liveChecks: { homeReadback: liveEvaluation.homeReadback },
    ...(evaluation.exercise ? { exercise: evaluation.exercise } : {}),
    status,
    ok: status === 'passed',
    failures,
  };
}

async function defaultHomeStateVerifier(entityId, expectedState) {
  const { verifyEntityState } = await import('../src/integrations/homeassistant.js');
  return verifyEntityState(entityId, expectedState, { timeoutMs: 3_500, pollMs: 300 });
}

function observationsForReadback(source, observation, priorObservations) {
  return source === 'current'
    ? [observation]
    : [...(priorObservations || []), observation].reverse();
}

export async function evaluateLiveChecks(expected, observation, {
  priorObservations = [],
  homeStateVerifier = null,
} = {}) {
  const criteria = expected?.homeReadback;
  if (!criteria) return { ok: true, failures: [], homeReadback: null };
  const failures = [];
  const action = observationsForReadback(
    criteria.source, observation, priorObservations,
  ).flatMap((item) => item?.actions || [])
    .find((item) => item.name === criteria.actionTool && item.result);
  if (!action) {
    return {
      ok: false,
      failures: [`huisreadback: geen ${criteria.actionTool}-resultaat met dynamisch doel gevonden`],
      homeReadback: null,
    };
  }
  const entityId = String(valueAtPath(action.result, criteria.entityPath) || '');
  if (!/^[a-z0-9_]+\.[a-z0-9_]+$/u.test(entityId)) {
    return {
      ok: false,
      failures: ['huisreadback: het actieresultaat bevat geen geldige exacte entity-id'],
      homeReadback: null,
    };
  }
  if (criteria.currentActionTool) {
    const currentAction = (observation?.actions || []).find((item) => (
      item.name === criteria.currentActionTool && item.result
    ));
    const currentEntityId = String(valueAtPath(
      currentAction?.result, criteria.currentEntityPath || criteria.entityPath,
    ) || '');
    if (!currentAction || currentEntityId !== entityId) {
      failures.push(`huisreadback: ${criteria.currentActionTool} las niet exact de eerder gekozen entity-id`);
    }
    if (criteria.currentActionExactEntityArg === true) {
      const currentArgs = currentAction?.args;
      const exactKeys = currentArgs && typeof currentArgs === 'object'
        && !Array.isArray(currentArgs) ? Object.keys(currentArgs).sort() : [];
      if (exactKeys.length !== 1 || exactKeys[0] !== 'entity_id'
        || String(currentArgs?.entity_id || '') !== entityId) {
        failures.push(`huisreadback: ${criteria.currentActionTool} vereist exact argument entity_id zonder pad of integratie`);
      }
    }
  }
  let receipt;
  try {
    receipt = await (homeStateVerifier || defaultHomeStateVerifier)(
      entityId, criteria.expectedState,
    );
  } catch (error) {
    return {
      ok: false,
      failures: [`huisreadback: ${error?.message || String(error)}`],
      homeReadback: null,
    };
  }
  if (String(receipt?.entity_id || '') !== entityId) {
    failures.push('huisreadback: teruglezing hoort niet bij de gekozen entity-id');
  }
  if (String(receipt?.state || '') !== String(criteria.expectedState || '')) {
    failures.push(`huisreadback: verwacht ${criteria.expectedState}, kreeg ${receipt?.state || '<geen>'}`);
  }
  const complete = receipt?.ok === true;
  const boundedSuccess = complete || (criteria.requireComplete !== true
    && receipt?.reachableVerified === true);
  if (!boundedSuccess) failures.push('huisreadback: gevraagde toestand is niet volledig teruggelezen');
  return { ok: failures.length === 0, failures, homeReadback: receipt || null };
}

export function stepShouldRun(step, previousStepResult = null) {
  if (step?.when === 'previous_failed') return previousStepResult?.ok === false;
  return true;
}

export function scenarioConversationId(scenario, runId, scenarioIndex) {
  const prefix = scenario?.conversationContext === 'real_person'
    ? 'web:household-live'
    : 'web:canary';
  return `${prefix}:${runId}:${scenarioIndex}`;
}

export async function runCanaries(options, scenarios = REAL_LIFE_SCENARIOS) {
  const corpusErrors = validateScenarioCorpus(scenarios);
  if (corpusErrors.length) throw new Error(`ongeldig corpus:\n- ${corpusErrors.join('\n- ')}`);
  const health = await fetchHealth(options);
  const selected = selectScenarios(options, scenarios);
  const runId = randomUUID();
  const client = createCanaryAckClient();
  const results = [];
  for (const scenario of selected) {
    const eligibility = scenarioEligibility(scenario, options);
    if (!eligibility.runnable) {
      results.push({ id: scenario.id, domain: scenario.domain, status: 'skipped', reason: eligibility.reason, steps: [] });
      continue;
    }
    const result = { id: scenario.id, domain: scenario.domain, status: 'passed', reason: '', steps: [] };
    try {
      const priorObservations = [];
      let previousStepResult = null;
      for (const [stepIndex, step] of scenario.steps.entries()) {
        if (!stepShouldRun(step, previousStepResult)) continue;
        const requestId = `canary:${runId}:${results.length}:${stepIndex}`;
        const conversationId = scenarioConversationId(scenario, runId, results.length);
        const stepResult = await runStep(
          scenario, step, options, fetch, requestId, conversationId, client,
          priorObservations,
        );
        result.steps.push(stepResult);
        priorObservations.push(stepResult.observation);
        previousStepResult = stepResult;
        if (stepResult.status === 'failed') {
          result.status = 'failed';
          if (options.failFast) break;
        } else if (stepResult.status === 'not_exercised' && result.status === 'passed') {
          result.status = 'not_exercised';
          result.reason = stepResult.exercise?.reason || 'vereiste live branch niet geraakt';
        }
      }
    } catch (error) {
      result.status = 'error';
      result.reason = error?.message || String(error);
    }
    results.push(result);
    if (options.failFast && result.status !== 'passed') break;
  }
  return {
    health: {
      gezond: health.gezond,
      runtimeVersion: health.runtimeVersion || null,
    },
    results,
    summary: {
      passed: results.filter((result) => result.status === 'passed').length,
      failed: results.filter((result) => result.status === 'failed').length,
      errors: results.filter((result) => result.status === 'error').length,
      skipped: results.filter((result) => result.status === 'skipped').length,
      not_exercised: results.filter((result) => result.status === 'not_exercised').length,
    },
  };
}

function usage() {
  return `Gebruik: node scripts/live-canary.mjs [opties]

Standaard worden alleen read-only scenario's synthetisch tegen /api/chat gedraaid.

  --scenario id[,id]     Selecteer scenario's
  --domain naam[,naam]   Selecteer domeinen
  --base-url url          API-basis (standaard ${DEFAULT_BASE_URL})
  --db pad                SQLite-timeline (standaard ${DEFAULT_DB_PATH})
  --timeout-ms n          Algemene requesttimeout
  --allow-effects         Voer ook fysieke/persistente scenario's niet-synthetisch uit
  --fail-fast             Stop na de eerste mislukking
  --json                  Print machineleesbaar resultaat
  --list                  Toon corpus en voer niets uit
  --token token           Dashboardtoken (of EVA_WEB_TOKEN)
  -h, --help              Toon hulp

LET OP: --allow-effects kan echt spreken of persistente toestand wijzigen.`;
}

function printableScenario(scenario, options) {
  const eligibility = scenarioEligibility(scenario, options);
  return {
    id: scenario.id,
    domain: scenario.domain,
    risk: scenario.risk,
    transport: scenario.transport,
    runnable: eligibility.runnable,
    reason: eligibility.reason,
  };
}

async function main() {
  const options = parseCliArgs(process.argv.slice(2));
  if (options.help) {
    console.log(usage());
    return 0;
  }
  const selected = selectScenarios(options);
  if (options.list) {
    console.log(JSON.stringify(selected.map((scenario) => printableScenario(scenario, options)), null, 2));
    return 0;
  }
  if (options.allowEffects) {
    console.error('WAARSCHUWING: --allow-effects is actief; geselecteerde effectscenario\'s kunnen echt handelen.');
  }
  const report = await runCanaries(options);
  if (options.json) console.log(JSON.stringify(report, null, 2));
  else {
    for (const result of report.results) {
      console.log(`${result.status.toUpperCase()} ${result.id}${result.reason ? ` — ${result.reason}` : ''}`);
      for (const step of result.steps) {
        console.log(`  ${step.latencyMs} ms | ${step.observation.contract.kind} | tools: ${step.observation.actions.map((action) => action.name).join(', ') || 'geen'}`);
        console.log(`  Eva: ${step.observation.reply.replace(/\s+/g, ' ').slice(0, 500)}`);
        for (const failure of step.failures) console.log(`  - ${failure}`);
      }
    }
    console.log(`Samenvatting: ${report.summary.passed} geslaagd, ${report.summary.failed} mislukt, ${report.summary.errors} fouten, ${report.summary.skipped} overgeslagen, ${report.summary.not_exercised} niet geraakt.`);
  }
  return report.summary.failed || report.summary.errors || report.summary.not_exercised ? 1 : 0;
}

const invokedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  main().then((code) => { process.exitCode = code; }).catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}
