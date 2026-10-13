import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const CASES = Object.freeze({
  C01: Object.freeze({ entityId: null }),
  C04: Object.freeze({ entityId: null }),
  T02: Object.freeze({ entityId: 'light.kantoor_links' }),
});
const ID = /^[a-zA-Z0-9._:-]{1,160}$/u;
const HASH = /^[a-f0-9]{64}$/u;
const CONVERSATION_EVENTS = new Set([
  'INGRESS_COMMITTED', 'REPLY_COMMITTED', 'MEMORY_RETAIN_QUEUED',
]);

function canonicalData(value) {
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalData(value[key])]),
  );
  return value;
}

const canonical = (value) => JSON.stringify(canonicalData(value));
const sha256 = (value) => createHash('sha256').update(
  typeof value === 'string' ? value : canonical(value),
).digest('hex');

function exactCase(catalog, caseId) {
  if (catalog?.contract !== 'LivedAgentBenchmark/v1' || catalog.benchmark_id !== 'LAB-36'
    || !Array.isArray(catalog.cases) || !CASES[caseId]) {
    throw new TypeError('LAB-developmentcase of catalogus is ongeldig');
  }
  const item = catalog.cases.find((candidate) => candidate.case_id === caseId);
  if (!item || item.effect_class !== 'E0' || !item.lanes.includes('native')
    || !item.eligible_tracks.includes('core_live') || typeof item.stimulus !== 'string') {
    throw new TypeError('LAB-developmentcase is niet eligible voor native core_live E0');
  }
  return item;
}

function validState(value, expectedEntity) {
  if (!value || value.entity_id !== expectedEntity || typeof value.state !== 'string'
    || typeof value.last_changed !== 'string') {
    throw new TypeError('Home Assistant-oracle gaf geen geldige begrensde staat');
  }
  return Object.freeze({
    entity_id: value.entity_id,
    state: value.state,
    last_changed: value.last_changed,
  });
}

function exactEvents(value) {
  if (!Array.isArray(value) || value.length < 2) {
    throw new TypeError('duurzame gespreksevents ontbreken');
  }
  return Object.freeze(value.map((event, index) => {
    if (!event || event.sequence !== index + 1 || !ID.test(String(event.event_type || ''))
      || !HASH.test(String(event.payload_sha256 || ''))) {
      throw new TypeError('duurzame gespreksevents zijn ongeldig');
    }
    return Object.freeze({ ...event });
  }));
}

export async function runVisibleEva2Case({
  catalog, caseId, runId, telegramDate, ask, readEvents, readState,
  transport = 'telegram_canary',
}) {
  const item = exactCase(catalog, caseId);
  if (!ID.test(String(runId || '')) || !Number.isSafeInteger(telegramDate)
    || telegramDate < 0 || typeof ask !== 'function' || typeof readEvents !== 'function') {
    throw new TypeError('LAB-developmentrun mist een geldige runbinding');
  }
  const definition = CASES[caseId];
  if (definition.entityId && typeof readState !== 'function') {
    throw new TypeError('LAB-developmentrun mist zijn externe state-oracle');
  }
  const chatId = `lab36-${runId}-${caseId.toLowerCase()}`;
  const requestMessageId = '1';
  const before = definition.entityId
    ? validState(await readState(definition.entityId), definition.entityId) : null;
  const started = performance.now();
  const reply = await ask({
    chatId,
    fromId: 'live-test-jaap',
    messageId: requestMessageId,
    recentMessages: [],
    telegramDate,
    text: item.stimulus,
  });
  const wallClockMs = Math.round(performance.now() - started);
  if (!reply || typeof reply.text !== 'string' || !reply.text.trim()
    || !ID.test(String(reply.message_id || ''))) {
    throw new TypeError('Eva 2 gaf geen geldig zichtbaar antwoord');
  }
  const threadId = `conversation:${reply.message_id}`;
  const events = exactEvents(await readEvents(threadId));
  const after = definition.entityId
    ? validState(await readState(definition.entityId), definition.entityId) : null;
  const unexpected = events.filter((event) => !CONVERSATION_EVENTS.has(event.event_type));
  const stateUnchanged = before == null || canonical(before) === canonical(after);
  return Object.freeze({
    contract: 'EvaLab36VisibleCaseCapture/v1',
    benchmark_id: 'LAB-36',
    benchmark_version: catalog.version,
    case_id: caseId,
    lane: 'native',
    track: 'core_live',
    status: 'development_evidence_captured_not_scored',
    run_id: runId,
    transport,
    thread_id: threadId,
    stimulus: item.stimulus,
    wall_clock_ms: wallClockMs,
    reply: reply.text.trim(),
    reply_sha256: sha256(reply.text.trim()),
    evidence_memory_count: Array.isArray(reply.evidence_memory_ids)
      ? reply.evidence_memory_ids.length : 0,
    durable_events: events,
    external_state_before: before,
    external_state_after: after,
    deterministic_observations: Object.freeze({
      conversation_event_count: events.length,
      unexpected_conversation_event_count: unexpected.length,
      external_state_unchanged: stateUnchanged,
    }),
    semantic_gates: 'not_judged',
    critical_failures: unexpected.length || !stateUnchanged
      ? Object.freeze(['unexpected_effect_or_state_change']) : Object.freeze([]),
  });
}

function commandJson(command, arguments_, label, timeout = 15_000) {
  const completed = spawnSync(command, arguments_, {
    encoding: 'utf8', maxBuffer: 1_048_576, timeout,
  });
  if (completed.status !== 0 || completed.error) {
    throw new Error(`${label} faalde gesloten`);
  }
  const value = JSON.parse(completed.stdout.trim());
  return value;
}

function dashboardAsk(container, text) {
  if (!ID.test(String(container || '')) || typeof text !== 'string' || !text.trim()) {
    throw new TypeError('dashboard-runbinding is ongeldig');
  }
  const program = [
    'import json,os,urllib.request',
    "token_path=os.environ['EVA_DASHBOARD_ACCESS_TOKEN_FILE']",
    "token=open(token_path,encoding='utf-8').read().strip()",
    `payload=json.dumps({'text':${JSON.stringify(text)}},ensure_ascii=False).encode('utf-8')`,
    "req=urllib.request.Request('http://127.0.0.1:8080/internal/v1/dashboard/conversation/respond',data=payload,headers={'Authorization':f'Bearer {token}','Content-Type':'application/json','Accept':'application/json'},method='POST')",
    "print(urllib.request.urlopen(req,timeout=75).read().decode('utf-8'))",
  ].join(';');
  return commandJson(
    'docker', ['exec', container, 'python', '-c', program], 'Eva-dashboard', 90_000,
  );
}

function postgresEvents(container, database, threadId) {
  if (![container, database, threadId].every((value) => ID.test(String(value || '')))) {
    throw new TypeError('PostgreSQL-observerbinding is ongeldig');
  }
  const sql = [
    'SELECT COALESCE(json_agg(json_build_object(',
    "'sequence', sequence, 'event_type', event_type,",
    "'payload_sha256', encode(payload_sha256, 'hex')) ORDER BY sequence),",
    "'[]'::json)::text FROM eva_core.operational_events",
    `WHERE thread_id = '${threadId}'`,
  ].join(' ');
  return commandJson('docker', [
    'exec', container, 'psql', '-U', 'postgres', '-d', database, '-Atc', sql,
  ], 'PostgreSQL-observer');
}

function homeAssistantState(container, entityId) {
  if (![container, entityId].every((value) => ID.test(String(value || '')))) {
    throw new TypeError('Home Assistant-observerbinding is ongeldig');
  }
  const program = [
    'import json,os,urllib.request',
    "base=os.environ['EVA_HOME_ASSISTANT_URL'].rstrip('/')",
    "token=os.environ['EVA_HOME_ASSISTANT_TOKEN']",
    `entity=${JSON.stringify(entityId)}`,
    "req=urllib.request.Request(f'{base}/api/states/{entity}',headers={'Authorization':f'Bearer {token}','Accept':'application/json'})",
    'raw=json.load(urllib.request.urlopen(req,timeout=5))',
    "print(json.dumps({'entity_id':raw['entity_id'],'state':raw['state'],'last_changed':raw['last_changed']},separators=(',',':'),sort_keys=True))",
  ].join(';');
  return commandJson('docker', ['exec', container, 'python', '-c', program], 'HA-observer');
}

function cliArguments(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value == null) throw new TypeError('ongeldige CLI-argumenten');
    result[key.slice(2)] = value;
  }
  return result;
}

async function main() {
  const args = cliArguments(process.argv.slice(2));
  const root = path.resolve(import.meta.dirname, '../..');
  const catalog = JSON.parse(fs.readFileSync(path.join(root, 'benchmark/eva-agent-benchmark-v1.json')));
  const coreContainer = args['core-container'] || 'eva2-living-core-b';
  const postgresContainer = args['postgres-container'] || 'eva2-postgres-b-live';
  const postgresDatabase = args['postgres-database'] || 'eva2_b';
  let ask;
  let transport;
  if (args['dashboard-container']) {
    ask = ({ text }) => dashboardAsk(args['dashboard-container'], text);
    transport = 'owner_dashboard';
  } else {
    const bridgePath = path.resolve(args['bridge-path']);
    const bridge = await import(pathToFileURL(bridgePath));
    const baseUrl = args['base-url'];
    ask = (request) => bridge.askEva2Canary({
      ...request, text: `/eva2 ${request.text}`, baseUrl,
    });
    transport = 'telegram_canary';
  }
  const result = await runVisibleEva2Case({
    catalog,
    caseId: args['case-id'],
    runId: args['run-id'],
    telegramDate: Number(args['telegram-date']),
    ask,
    readEvents: (threadId) => postgresEvents(postgresContainer, postgresDatabase, threadId),
    readState: (entityId) => homeAssistantState(coreContainer, entityId),
    transport,
  });
  process.stdout.write(`${canonical(result)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(import.meta.filename)) {
  main().catch((error) => {
    process.stderr.write(`LAB Eva2-developmentrunner faalde gesloten: ${error.message}\n`);
    process.exitCode = 1;
  });
}
