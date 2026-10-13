import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const HASH = /^[a-f0-9]{64}$/u;
const PARTICIPANT = /^v(?<release>[0-9]{4}\.[0-9]+\.[0-9]+)@(?<commit>[a-f0-9]{40})$/u;
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
    throw new TypeError('Hermes-runner kreeg geen ondersteunde zichtbare E0-case');
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

function parsedToolNames(messages) {
  const names = [];
  for (const message of messages) {
    if (!message.tool_calls) continue;
    const calls = JSON.parse(message.tool_calls);
    for (const call of calls) names.push(call?.function?.name);
  }
  return names;
}

function expectedHermes(matrix) {
  const match = PARTICIPANT.exec(matrix?.pinned_participants?.hermes ?? '');
  if (matrix?.contract !== 'EvaCompetitorCapabilityMatrix/v1' || !match) {
    throw new TypeError('actuele Hermes-matrixbinding ontbreekt');
  }
  return Object.freeze({ release: match.groups.release, commit: match.groups.commit });
}

export function captureHermesResult({
  catalog, matrix, caseId, runId, sourceCommit, sourceVersion, configSha256,
  version, wallClockMs, session,
}) {
  const item = exactCase(catalog, caseId);
  const expected = expectedHermes(matrix);
  const toolNames = parsedToolNames(session?.messages || []);
  const finalMessages = (session?.messages || []).filter(
    (message) => message.role === 'assistant' && typeof message.content === 'string'
      && message.content.trim(),
  );
  const final = finalMessages.at(-1)?.content?.trim();
  if (!/^[a-zA-Z0-9._:-]{1,160}$/u.test(runId) || sourceCommit !== expected.commit
    || !/^0\.[0-9]+\.[0-9]+$/u.test(sourceVersion) || !HASH.test(configSha256)
    || version.split('\n')[0] !== `Hermes Agent v${sourceVersion} (${expected.release})`
    || !Number.isSafeInteger(wallClockMs) || wallClockMs < 0
    || session?.model !== 'gpt-5.4-mini' || session?.billing_provider !== 'openai-codex'
    || session?.api_call_count !== 1 || session?.tool_call_count !== toolNames.length
    || typeof final !== 'string' || !final || toolNames.length !== 0
    || session.messages?.[0]?.role !== 'user' || session.messages[0].content !== item.stimulus) {
    throw new TypeError('Hermes-runresultaat wijkt af van de begrensde actuele binding');
  }
  return Object.freeze({
    contract: 'HermesLab36VisibleCaseCapture/v2',
    benchmark_id: 'LAB-36',
    benchmark_version: catalog.version,
    case_id: caseId,
    lane: 'native',
    track: 'core_live',
    status: 'development_evidence_captured_not_scored',
    run_id: runId,
    competitor_binding: matrix.pinned_participants.hermes,
    source_commit: sourceCommit,
    config_sha256: configSha256,
    participant_version: version.split('\n')[0],
    model: session.model,
    provider: session.billing_provider,
    reasoning_effort: session.reasoning_effort,
    stimulus: item.stimulus,
    wall_clock_ms: wallClockMs,
    reply: final,
    reply_sha256: sha256(final),
    usage: Object.freeze({
      input_tokens: session.input_tokens,
      output_tokens: session.output_tokens,
      reasoning_tokens: session.reasoning_tokens,
      api_calls: session.api_call_count,
    }),
    tool_calls: session.tool_call_count,
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

async function main() {
  const args = cliArguments(process.argv.slice(2));
  const root = path.resolve(import.meta.dirname, '../..');
  const sourceRoot = path.resolve(args['source-root']);
  const configPath = path.resolve(args.config);
  const codexHome = path.resolve(args['codex-home']);
  const hermesHome = path.resolve(args['hermes-home']);
  const workdir = path.resolve(args.workdir);
  const cliPath = path.join(sourceRoot, '.venv/bin/hermes');
  const pythonPath = fs.realpathSync(path.join(sourceRoot, '.venv/bin/python'));
  const catalog = JSON.parse(regularFile(
    path.join(root, 'benchmark/eva-agent-benchmark-v1.json'), 'LAB-catalogus',
  ));
  const matrix = JSON.parse(regularFile(
    path.join(root, 'benchmark/competitor-capability-matrix-v1.json'), 'concurrentmatrix',
  ));
  const item = exactCase(catalog, args['case-id']);
  regularFile(cliPath, 'Hermes CLI');
  regularFile(pythonPath, 'Hermes Python');
  const configBytes = regularFile(configPath, 'Hermes-config');
  regularFile(path.join(codexHome, 'auth.json'), 'Codex-accountbinding');
  regularFile(path.join(hermesHome, 'auth.json'), 'Hermes-accountbinding');
  if (!fs.statSync(workdir).isDirectory()) throw new TypeError('Hermes-workdir ontbreekt');
  if (fs.readdirSync(workdir).length !== 0) throw new TypeError('Hermes-workdir is niet leeg');
  const sourceCommit = command('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'], {}, 'bronbinding')
    .stdout.trim();
  const expected = expectedHermes(matrix);
  if (sourceCommit !== expected.commit) throw new TypeError('Hermes-broncommit loopt achter op de matrix');
  if (command('git', ['-C', sourceRoot, 'status', '--short'], {}, 'bronschoonheid').stdout.trim()) {
    throw new TypeError('Hermes-broncheckout is niet schoon');
  }
  const version = command(cliPath, ['--version'], {}, 'versiebinding').stdout.trim();
  const pyproject = regularFile(path.join(sourceRoot, 'pyproject.toml'), 'Hermes-pakketmanifest')
    .toString('utf8');
  const sourceVersion = /^version\s*=\s*"([^"]+)"$/mu.exec(pyproject)?.[1];
  if (!sourceVersion) throw new TypeError('Hermes-bronversie ontbreekt');
  const env = {
    ...process.env,
    CODEX_HOME: codexHome,
    HERMES_HOME: hermesHome,
    HERMES_CONFIG: configPath,
    PYTHONDONTWRITEBYTECODE: '1',
  };
  const started = performance.now();
  const executed = command(cliPath, [
    'chat', '-Q', '--in', workdir,
    '--provider', 'openai-codex', '--model', 'gpt-5.4-mini',
    '--reasoning', 'medium', '--max-turns', '1', '--ignore-rules',
    '--query', item.stimulus,
  ], { cwd: sourceRoot, env, timeout: 135_000 }, 'Hermes live turn');
  const wallClockMs = Math.round(performance.now() - started);
  const sessionId = /session_id:\s*([a-zA-Z0-9_-]+)/u.exec(
    `${executed.stdout}\n${executed.stderr}`,
  )?.[1];
  if (!sessionId) {
    throw new TypeError('Hermes-uitvoer bevat geen sessiebinding');
  }
  const query = [
    'import json,sqlite3,sys',
    'db,session_id=sys.argv[1:3]',
    'c=sqlite3.connect(f"file:{db}?mode=ro",uri=True)',
    'c.row_factory=sqlite3.Row',
    's=c.execute("select * from sessions where id=?",(session_id,)).fetchone()',
    'm=c.execute("select role,content,tool_calls,tool_name,effect_disposition,finish_reason from messages where session_id=? order by id",(session_id,)).fetchall()',
    'cfg=json.loads(s["model_config"] or "{}")',
    'out={k:s[k] for k in ("id","model","billing_provider","message_count","tool_call_count","input_tokens","output_tokens","reasoning_tokens","api_call_count")}',
    'out["reasoning_effort"]=(cfg.get("reasoning_config") or {}).get("effort")',
    'out["messages"]=[dict(row) for row in m]',
    'print(json.dumps(out,ensure_ascii=False))',
  ].join(';');
  const session = JSON.parse(command(pythonPath, [
    '-c', query, path.join(hermesHome, 'state.db'), sessionId,
  ], { env }, 'Hermes sessiereceipt').stdout);
  const capture = captureHermesResult({
    catalog,
    matrix,
    caseId: args['case-id'],
    runId: args['run-id'],
    sourceCommit,
    sourceVersion,
    configSha256: sha256(configBytes),
    version,
    wallClockMs,
    session,
  });
  process.stdout.write(`${canonical(capture)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(import.meta.filename)) {
  main().catch((error) => {
    process.stderr.write(`LAB Hermes-developmentrunner faalde gesloten: ${error.message}\n`);
    process.exitCode = 1;
  });
}
