#!/usr/bin/env node

import process from 'node:process';
import { pathToFileURL } from 'node:url';
import Database from 'better-sqlite3';

import {
  EFFECT_TOOLS,
  REAL_LIFE_SCENARIOS,
  validateScenarioCorpus,
} from './real-life-scenarios.mjs';

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
  if (replyCriteria.mustContainUrl && !/https?:\/\/\S+/i.test(reply)) {
    failures.push('antwoord: controleerbare bron-URL ontbreekt');
  }
  return { ok: failures.length === 0, failures };
}

function parseMeta(value) {
  try { return JSON.parse(String(value || '{}')); } catch { return {}; }
}

export function extractTurnObservation(rows, prompt, apiReply = '') {
  const normalizedPrompt = String(prompt || '').trim();
  const inboundIndex = rows.findIndex((row) => (
    row.kind === 'chat_in' && String(row.content || '').endsWith(normalizedPrompt)
  ));
  const scoped = inboundIndex >= 0 ? rows.slice(inboundIndex) : rows;
  const contractRow = scoped.find((row) => row.kind === 'turn_contract');
  const contractMeta = parseMeta(contractRow?.meta);
  const turnId = String(contractMeta.turnId || '');
  const turnRows = turnId
    ? scoped.filter((row) => String(parseMeta(row.meta).turnId || '') === turnId)
    : scoped;
  const actions = turnRows.filter((row) => row.kind === 'action').map((row) => {
    const meta = parseMeta(row.meta);
    return {
      name: String(meta.tool || String(row.content || '').split(':', 1)[0]),
      ok: meta.ok === true,
      verified: meta.verified === true,
      changed: meta.changed === true,
    };
  });
  const output = turnRows.findLast((row) => row.kind === 'chat_out');
  return {
    turnId: turnId || null,
    contract: {
      kind: String(contractMeta.kind || String(contractRow?.content || '').split(':', 1)[0] || ''),
      requiredTools: Array.isArray(contractMeta.requiredTools) ? contractMeta.requiredTools : [],
      offeredTools: Array.isArray(contractMeta.offeredTools) ? contractMeta.offeredTools : [],
    },
    actions,
    reply: apiReply || String(output?.content || '').replace(/^Eva\s*->\s*[^:]+:\s*/i, ''),
    timelineIds: turnRows.map((row) => row.id),
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
      WHERE id > ? AND kind IN ('chat_in','turn_contract','action','chat_out') ORDER BY id`).all(id);
  } finally { db.close(); }
}

async function waitForTimeline(dbPath, afterId, prompt, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  let rows = [];
  while (Date.now() < deadline) {
    rows = timelineRowsSince(dbPath, afterId);
    const hasInbound = rows.some((row) => row.kind === 'chat_in' && String(row.content).endsWith(prompt));
    const hasOutbound = rows.some((row) => row.kind === 'chat_out');
    if (hasInbound && hasOutbound) return rows;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return rows;
}

function requestHeaders(token) {
  return {
    'content-type': 'application/json',
    ...(token ? { authorization: `Bearer ${token}` } : {}),
  };
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

async function runStep(scenario, step, options, complete = fetch) {
  const afterId = timelineMaxId(options.dbPath);
  const synthetic = scenario.risk === 'read_only';
  const started = performance.now();
  const response = await complete(`${options.baseUrl}/api/chat`, {
    method: 'POST',
    headers: requestHeaders(options.token),
    body: JSON.stringify({ text: step.text, synthetic }),
    signal: AbortSignal.timeout(scenario.timeoutMs || options.timeoutMs),
  });
  const latencyMs = Math.round(performance.now() - started);
  const body = await response.json();
  if (!response.ok) throw new Error(`/api/chat gaf ${response.status}: ${body?.error || 'onbekende fout'}`);
  const rows = await waitForTimeline(options.dbPath, afterId, step.text);
  const observation = extractTurnObservation(rows, step.text, body?.reply || '');
  const evaluation = evaluateObservation(step.expected, observation);
  return { text: step.text, synthetic, latencyMs, observation, ...evaluation };
}

export async function runCanaries(options, scenarios = REAL_LIFE_SCENARIOS) {
  const corpusErrors = validateScenarioCorpus(scenarios);
  if (corpusErrors.length) throw new Error(`ongeldig corpus:\n- ${corpusErrors.join('\n- ')}`);
  const health = await fetchHealth(options);
  const selected = selectScenarios(options, scenarios);
  const results = [];
  for (const scenario of selected) {
    const eligibility = scenarioEligibility(scenario, options);
    if (!eligibility.runnable) {
      results.push({ id: scenario.id, domain: scenario.domain, status: 'skipped', reason: eligibility.reason, steps: [] });
      continue;
    }
    const result = { id: scenario.id, domain: scenario.domain, status: 'passed', reason: '', steps: [] };
    try {
      for (const step of scenario.steps) {
        const stepResult = await runStep(scenario, step, options);
        result.steps.push(stepResult);
        if (!stepResult.ok) {
          result.status = 'failed';
          if (options.failFast) break;
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
    console.log(`Samenvatting: ${report.summary.passed} geslaagd, ${report.summary.failed} mislukt, ${report.summary.errors} fouten, ${report.summary.skipped} overgeslagen.`);
  }
  return report.summary.failed || report.summary.errors ? 1 : 0;
}

const invokedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  main().then((code) => { process.exitCode = code; }).catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}
