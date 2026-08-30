import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const HASH = /^[a-f0-9]{64}$/u;
const CONTEXT_ID = /^[a-zA-Z0-9_-]{1,160}$/u;
const VISIBLE_E0_CASES = new Set(['C01', 'C04', 'T02']);
const PARTICIPANT = /^v(?<release>[0-9]+\.[0-9]+)@(?<commit>[a-f0-9]{40})$/u;

function canonicalData(value) {
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalData(value[key])]),
  );
  return value;
}

const canonical = (value) => JSON.stringify(canonicalData(value));
const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function exactCase(catalog, caseId) {
  const item = catalog?.cases?.find((candidate) => candidate.case_id === caseId);
  if (catalog?.contract !== 'LivedAgentBenchmark/v1' || catalog.benchmark_id !== 'LAB-36'
    || !VISIBLE_E0_CASES.has(caseId) || item?.effect_class !== 'E0'
    || !item.eligible_tracks?.includes('core_live') || typeof item.stimulus !== 'string') {
    throw new TypeError('Agent Zero-runner kreeg geen ondersteunde zichtbare E0-case');
  }
  return item;
}

function regularFile(filePath, label) {
  const stat = fs.lstatSync(filePath);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new TypeError(`${label} is niet regulier`);
  return fs.readFileSync(filePath);
}

function command(commandPath, args, options, label) {
  const result = spawnSync(commandPath, args, {
    encoding: 'utf8', maxBuffer: 16_777_216, ...options,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${label} faalde gesloten: ${(result.stderr || result.error?.message || '').trim()}`);
  }
  return result;
}

function chatReceipt(chat) {
  const providerKeys = [];
  const usage = { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, api_calls: 0 };
  const seenResponses = new Set();
  const visit = (value) => {
    if (typeof value === 'string' && /^[{[]/u.test(value.trim())) {
      try { visit(JSON.parse(value)); } catch { /* ordinary message text */ }
      return;
    }
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== 'object') return;
    if (typeof value.provider_model_key === 'string') {
      providerKeys.push(value.provider_model_key);
      const responseId = typeof value.response_id === 'string'
        ? value.response_id : `${providerKeys.length}`;
      if (!seenResponses.has(responseId)) {
        seenResponses.add(responseId);
        usage.api_calls += 1;
        usage.input_tokens += value.usage?.input_tokens ?? 0;
        usage.output_tokens += value.usage?.output_tokens ?? 0;
        usage.reasoning_tokens += value.usage?.output_tokens_details?.reasoning_tokens ?? 0;
      }
    }
    Object.values(value).forEach(visit);
  };
  visit(chat);
  return { providerKeys, usage };
}

function expectedAgentZero(matrix) {
  const match = PARTICIPANT.exec(matrix?.pinned_participants?.agent_zero ?? '');
  if (matrix?.contract !== 'EvaCompetitorCapabilityMatrix/v1' || !match) {
    throw new TypeError('actuele Agent Zero-matrixbinding ontbreekt');
  }
  return Object.freeze({ release: match.groups.release, commit: match.groups.commit });
}

async function readStableChat(container, contextId) {
  let lastError;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const raw = command('docker', [
      'exec', container, 'cat', `/a0/usr/chats/${contextId}/chat.json`,
    ], {}, 'Agent Zero-chatreceipt').stdout;
    try {
      return JSON.parse(raw);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`Agent Zero-chatreceipt bleef onvolledig: ${lastError?.message || 'parsefout'}`);
}

export function captureAgentZeroResult({
  catalog, matrix, caseId, runId, sourceCommit, configSha256, imageId, wallClockMs,
  apiResult, logItems, providerKeys, usage, reasoningEffort,
}) {
  const item = exactCase(catalog, caseId);
  const expected = expectedAgentZero(matrix);
  const toolNames = (logItems || [])
    .filter((entry) => entry?.type === 'tool')
    .map((entry) => entry?.kvps?._tool_name)
    .filter((name) => typeof name === 'string');
  const reply = apiResult?.response?.trim();
  const finalLog = (logItems || []).filter(
    (entry) => entry?.type === 'response' && entry?.kvps?.finished === true,
  ).at(-1);
  if (!/^[a-zA-Z0-9._:-]{1,160}$/u.test(runId)
    || sourceCommit !== expected.commit || !HASH.test(configSha256)
    || !/^sha256:[a-f0-9]{64}$/u.test(imageId)
    || !Number.isSafeInteger(wallClockMs) || wallClockMs < 0
    || !CONTEXT_ID.test(apiResult?.context_id || '') || typeof reply !== 'string' || !reply
    || finalLog?.content?.trim() !== reply || toolNames.length !== 0
    || providerKeys.length < 1 || providerKeys.some((key) => key !== 'openai/gpt-5.4-mini')
    || reasoningEffort !== 'medium' || !Number.isSafeInteger(usage?.api_calls)
    || usage.api_calls < 1) {
    throw new TypeError('Agent Zero-runresultaat wijkt af van de begrensde actuele binding');
  }
  return Object.freeze({
    contract: 'AgentZeroLab36VisibleCaseCapture/v2',
    benchmark_id: 'LAB-36',
    benchmark_version: catalog.version,
    case_id: caseId,
    lane: 'native',
    track: 'core_live',
    status: 'development_evidence_captured_not_scored',
    run_id: runId,
    competitor_binding: matrix.pinned_participants.agent_zero,
    source_commit: sourceCommit,
    image_id: imageId,
    config_sha256: configSha256,
    participant_version: `v${expected.release}`,
    model: 'gpt-5.4-mini',
    provider: 'codex_oauth',
    reasoning_effort: reasoningEffort,
    stimulus: item.stimulus,
    wall_clock_ms: wallClockMs,
    reply,
    reply_sha256: sha256(reply),
    usage: Object.freeze({ ...usage }),
    tool_calls: toolNames.length,
    tool_names: Object.freeze(toolNames),
    effectful_tool_calls: 0,
    semantic_gates: 'not_judged',
    critical_failures: Object.freeze([]),
  });
}

function cliArguments(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value == null) throw new TypeError('ongeldige CLI-argumenten');
    parsed[key.slice(2)] = value;
  }
  return parsed;
}

async function jsonRequest(url, apiKey, body, timeoutMs) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-KEY': apiKey },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(`Agent Zero API ${response.status}: ${payload.error || 'fout'}`);
  return payload;
}

async function main() {
  const args = cliArguments(process.argv.slice(2));
  const root = path.resolve(import.meta.dirname, '../..');
  const sourceRoot = path.resolve(args['source-root']);
  const configPath = path.resolve(args.config);
  const baseUrl = new URL(args['base-url']);
  const apiKey = process.env.AGENT_ZERO_API_KEY;
  const container = args.container;
  const catalog = JSON.parse(regularFile(
    path.join(root, 'benchmark/eva-agent-benchmark-v1.json'), 'LAB-catalogus',
  ));
  const matrix = JSON.parse(regularFile(
    path.join(root, 'benchmark/competitor-capability-matrix-v1.json'), 'concurrentmatrix',
  ));
  const item = exactCase(catalog, args['case-id']);
  const configBytes = regularFile(configPath, 'Agent Zero-config');
  regularFile(path.join(root, 'benchmark/participants/agent-zero-core-live-presets.yaml'), 'modelpreset');
  if (!apiKey || !container || baseUrl.hostname !== '127.0.0.1') {
    throw new TypeError('Agent Zero-runner vereist een lokale geïsoleerde API-binding');
  }
  const sourceCommit = command('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'], {}, 'bronbinding')
    .stdout.trim();
  const expected = expectedAgentZero(matrix);
  if (sourceCommit !== expected.commit
    || command('git', ['-C', sourceRoot, 'status', '--short'], {}, 'bronschoonheid').stdout.trim()) {
    throw new TypeError('Agent Zero-broncheckout wijkt af');
  }
  const exactTag = command('git', ['-C', sourceRoot, 'describe', '--tags', '--exact-match', 'HEAD'], {}, 'versiebinding')
    .stdout.trim();
  if (exactTag !== `v${expected.release}`) throw new TypeError('Agent Zero-versie wijkt af');
  const imageId = command('docker', ['inspect', container, '--format', '{{.Image}}'], {}, 'containerbeeld')
    .stdout.trim();
  const labels = JSON.parse(command(
    'docker', ['inspect', container, '--format', '{{json .Config.Labels}}'], {}, 'containerherkomst',
  ).stdout);
  if (labels?.['org.opencontainers.image.revision'] !== expected.commit
    || labels?.['org.opencontainers.image.version'] !== `v${expected.release}`) {
    throw new TypeError('Agent Zero-image wijkt af van de actuele bronbinding');
  }
  const containerEnv = JSON.parse(command(
    'docker', ['inspect', container, '--format', '{{json .Config.Env}}'], {}, 'containerconfig',
  ).stdout);
  if (!containerEnv.includes('A0_SET__oauth__codex__reasoning_effort=medium')) {
    throw new TypeError('Agent Zero-reasoningbinding wijkt af');
  }
  const presetSha = command('docker', [
    'exec', container, 'sha256sum', '/a0/usr/plugins/_model_config/presets.yaml',
  ], {}, 'modelpresetbinding').stdout.split(/\s+/u)[0];
  if (presetSha !== sha256(regularFile(
    path.join(root, 'benchmark/participants/agent-zero-core-live-presets.yaml'), 'modelpreset',
  ))) throw new TypeError('Agent Zero-modelpreset wijkt af');
  const health = await fetch(new URL('/api/health', baseUrl), { signal: AbortSignal.timeout(5_000) });
  if (!health.ok) throw new Error('Agent Zero-healthcheck faalde');
  const started = performance.now();
  const apiResult = await jsonRequest(new URL('/api/api_message', baseUrl), apiKey, {
    message: item.stimulus,
    lifetime_hours: 1,
  }, 180_000);
  const wallClockMs = Math.round(performance.now() - started);
  const logResult = await jsonRequest(new URL('/api/api_log_get', baseUrl), apiKey, {
    context_id: apiResult.context_id,
    length: 500,
  }, 10_000);
  if (!CONTEXT_ID.test(apiResult.context_id)) throw new TypeError('ongeldige context-ID');
  const chat = await readStableChat(container, apiResult.context_id);
  const { providerKeys, usage } = chatReceipt(chat);
  const capture = captureAgentZeroResult({
    catalog,
    matrix,
    caseId: args['case-id'],
    runId: args['run-id'],
    sourceCommit,
    configSha256: sha256(configBytes),
    imageId,
    wallClockMs,
    apiResult,
    logItems: logResult?.log?.items,
    providerKeys,
    usage,
    reasoningEffort: 'medium',
  });
  process.stdout.write(`${canonical(capture)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(import.meta.filename)) {
  main().catch((error) => {
    process.stderr.write(`LAB Agent Zero-developmentrunner faalde gesloten: ${error.message}\n`);
    process.exitCode = 1;
  });
}
