import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const HASH = /^[a-f0-9]{64}$/u;
const VERSION = /^v(?<release>[0-9]{4}\.[0-9]+\.[0-9]+(?:-[0-9]+)?)@(?<commit>[a-f0-9]{40})$/u;
const VISIBLE_E0_CASES = new Set(['C01', 'C04', 'T02']);

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
    throw new TypeError('OpenClaw-runner kreeg geen ondersteunde zichtbare E0-case');
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
    encoding: 'utf8', maxBuffer: 8_388_608, ...options,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${label} faalde gesloten: ${(result.stderr || result.error?.message || '').trim()}`);
  }
  return result;
}

function expectedOpenClaw(matrix) {
  const match = VERSION.exec(matrix?.pinned_participants?.openclaw ?? '');
  if (matrix?.contract !== 'EvaCompetitorCapabilityMatrix/v1' || !match) {
    throw new TypeError('actuele OpenClaw-matrixbinding ontbreekt');
  }
  return Object.freeze({ release: match.groups.release, commit: match.groups.commit });
}

function contentToolCalls(content) {
  if (!Array.isArray(content)) return 0;
  return content.filter((part) => ['toolCall', 'tool_call', 'toolUse', 'tool_use'].includes(part?.type)).length;
}

export function inspectOpenClawTrace(lines, expectedSessionId) {
  if (!Array.isArray(lines) || lines.length < 2) throw new TypeError('OpenClaw-trace ontbreekt');
  const completed = lines.filter((line) => line?.type === 'model.completed');
  const ended = lines.filter((line) => line?.type === 'session.ended');
  if (completed.length !== 1 || ended.length !== 1
    || completed[0].sessionId !== expectedSessionId || ended[0].sessionId !== expectedSessionId
    || completed[0].data?.timedOut !== false || completed[0].data?.aborted !== false
    || completed[0].data?.promptError != null || ended[0].data?.status !== 'success') {
    throw new TypeError('OpenClaw-trace bewijst geen enkele volledige modelbeurt');
  }
  const messages = completed[0].data?.messagesSnapshot;
  if (!Array.isArray(messages) || messages.length !== 2
    || messages[0]?.role !== 'user' || messages[1]?.role !== 'assistant') {
    throw new TypeError('OpenClaw-trace bevat een onverwachte gespreksvorm');
  }
  const toolCalls = messages.reduce((count, message) => count + contentToolCalls(message.content), 0);
  const assistantTexts = completed[0].data?.assistantTexts;
  if (!Array.isArray(assistantTexts) || assistantTexts.length !== 1
    || typeof assistantTexts[0] !== 'string' || !assistantTexts[0].trim()) {
    throw new TypeError('OpenClaw-trace mist het zichtbare antwoord');
  }
  return Object.freeze({
    reply: assistantTexts[0].trim(),
    toolCalls,
    usage: Object.freeze({ ...(completed[0].data?.usage || {}) }),
  });
}

export function captureOpenClawResult({
  catalog, matrix, caseId, runId, sourceCommit, configSha256, participantVersion,
  codexPluginVersion, wallClockMs, result, trace,
}) {
  const item = exactCase(catalog, caseId);
  const expected = expectedOpenClaw(matrix);
  const attempts = result?.meta?.executionTrace?.attempts;
  const text = result?.payloads?.[0]?.text;
  const agent = result?.meta?.agentMeta;
  if (!/^[a-zA-Z0-9._:-]{1,160}$/u.test(runId) || sourceCommit !== expected.commit
    || !HASH.test(configSha256)
    || participantVersion !== `OpenClaw ${expected.release} (${expected.commit.slice(0, 7)})`
    || !/^2026\.[0-9]+\.[0-9]+(?:-[0-9]+)?$/u.test(codexPluginVersion)
    || !Number.isSafeInteger(wallClockMs) || wallClockMs < 0
    || result?.meta?.aborted !== false || result?.meta?.stopReason !== 'stop'
    || agent?.provider !== 'openai' || agent?.model !== 'gpt-5.4-mini'
    || agent?.agentHarnessId !== 'codex' || result?.meta?.requestShaping?.thinking !== 'medium'
    || !Array.isArray(attempts) || attempts.length !== 1 || attempts[0]?.result !== 'success'
    || result?.meta?.executionTrace?.fallbackUsed !== false
    || typeof text !== 'string' || !text.trim()
    || trace?.reply !== text.trim() || trace?.toolCalls !== 0) {
    throw new TypeError('OpenClaw-runresultaat wijkt af van de begrensde actuele binding');
  }
  return Object.freeze({
    contract: 'OpenClawLab36VisibleCaseCapture/v2',
    benchmark_id: 'LAB-36',
    benchmark_version: catalog.version,
    case_id: caseId,
    lane: 'native',
    track: 'core_live',
    status: 'development_evidence_captured_not_scored',
    run_id: runId,
    competitor_binding: matrix.pinned_participants.openclaw,
    source_commit: sourceCommit,
    config_sha256: configSha256,
    participant_version: participantVersion,
    codex_plugin_version: codexPluginVersion,
    model: agent.model,
    provider: agent.provider,
    harness: agent.agentHarnessId,
    reasoning_effort: result.meta.requestShaping.thinking,
    stimulus: item.stimulus,
    wall_clock_ms: wallClockMs,
    reply: text.trim(),
    reply_sha256: sha256(text.trim()),
    usage: trace.usage,
    cost_usd: null,
    tool_calls: trace.toolCalls,
    fallback_used: result.meta.executionTrace.fallbackUsed,
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

async function main() {
  const args = cliArguments(process.argv.slice(2));
  const root = path.resolve(import.meta.dirname, '../..');
  const sourceRoot = path.resolve(args['source-root']);
  const cliPath = path.resolve(args['cli-path']);
  const configPath = path.resolve(args.config);
  const codexHome = path.resolve(args['codex-home']);
  const stateDir = path.resolve(args['state-dir']);
  const openclawHome = path.resolve(args['openclaw-home']);
  const workdir = path.resolve(args.workdir);
  const catalog = JSON.parse(regularFile(
    path.join(root, 'benchmark/eva-agent-benchmark-v1.json'), 'LAB-catalogus',
  ));
  const matrix = JSON.parse(regularFile(
    path.join(root, 'benchmark/competitor-capability-matrix-v1.json'), 'concurrentmatrix',
  ));
  const item = exactCase(catalog, args['case-id']);
  const expected = expectedOpenClaw(matrix);
  regularFile(cliPath, 'OpenClaw CLI');
  const configBytes = regularFile(configPath, 'OpenClaw-config');
  regularFile(path.join(codexHome, 'auth.json'), 'Codex-accountbinding');
  for (const [directory, label] of [[workdir, 'werkmap'], [stateDir, 'statusmap'], [openclawHome, 'thuismap']]) {
    if (!fs.statSync(directory).isDirectory()) throw new TypeError(`OpenClaw-${label} ontbreekt`);
  }
  const sourceCommit = command('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'], {}, 'bronbinding')
    .stdout.trim();
  if (sourceCommit !== expected.commit) throw new TypeError('OpenClaw-broncommit loopt achter op de matrix');
  const participantVersion = command(cliPath, ['--version'], {}, 'versiebinding').stdout.trim();
  const pluginProjects = path.join(stateDir, 'npm/projects');
  const pluginManifests = fs.readdirSync(pluginProjects)
    .map((name) => path.join(pluginProjects, name, 'package.json'))
    .filter((candidate) => fs.existsSync(candidate));
  const pluginVersions = pluginManifests.flatMap((manifest) => {
    const parsed = JSON.parse(regularFile(manifest, 'Codex-pluginmanifest'));
    return parsed?.dependencies?.['@openclaw/codex'] ? [parsed.dependencies['@openclaw/codex']] : [];
  });
  if (pluginVersions.length !== 1) throw new TypeError('exact één officiële Codex-pluginbinding vereist');
  const env = {
    ...process.env,
    TMPDIR: '/tmp', TEMP: '/tmp', TMP: '/tmp',
    OPENCLAW_HOME: openclawHome,
    OPENCLAW_STATE_DIR: stateDir,
    OPENCLAW_CONFIG_PATH: configPath,
    CODEX_HOME: codexHome,
  };
  const started = performance.now();
  const executed = command(cliPath, [
    'agent', '--local', '--message', item.stimulus,
    '--model', 'openai/gpt-5.4-mini', '--thinking', 'medium', '--timeout', '120', '--json',
    '--session-id', args['run-id'],
  ], { cwd: workdir, env, timeout: 135_000 }, 'OpenClaw live turn');
  const wallClockMs = Math.round(performance.now() - started);
  const result = JSON.parse(executed.stdout);
  const trajectoryPath = path.join(
    stateDir, 'agents/main/sessions', `${args['run-id']}.trajectory.jsonl`,
  );
  const trace = inspectOpenClawTrace(
    regularFile(trajectoryPath, 'OpenClaw-traject').toString('utf8').trim().split('\n').map(JSON.parse),
    args['run-id'],
  );
  const capture = captureOpenClawResult({
    catalog, matrix, caseId: args['case-id'], runId: args['run-id'], sourceCommit,
    configSha256: sha256(configBytes), participantVersion,
    codexPluginVersion: pluginVersions[0], wallClockMs, result, trace,
  });
  process.stdout.write(`${canonical(capture)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(import.meta.filename)) {
  main().catch((error) => {
    process.stderr.write(`LAB OpenClaw-developmentrunner faalde gesloten: ${error.message}\n`);
    process.exitCode = 1;
  });
}
