import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HASH = /^[a-f0-9]{64}$/u;
const ID = /^[a-z0-9][a-z0-9._:-]{0,119}$/u;
const LANES = new Set(['controlled', 'native']);
const TRACKS = new Set(['sim', 'core_live', 'native_live', 'resident_long']);

function canonicalData(value) {
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalData(value[key])]),
  );
  return value;
}

const canonical = (value) => JSON.stringify(canonicalData(value));
const sha256 = (value) => createHash('sha256')
  .update(typeof value === 'string' ? value : canonical(value)).digest('hex');

function exactKeys(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) {
    throw new TypeError(`${label} heeft niet de exacte sleutelset`);
  }
}

function boundedInteger(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${label} is geen begrensde safe integer`);
  }
  return value;
}

export function validateLabCatalog(catalog) {
  exactKeys(catalog, [
    'contract', 'benchmark_id', 'version', 'case_count', 'purpose', 'lanes',
    'tracks', 'effect_classes', 'scoring', 'cases',
  ], 'LAB-catalogus');
  if (catalog.contract !== 'LivedAgentBenchmark/v1' || catalog.benchmark_id !== 'LAB-36'
    || catalog.case_count !== 36 || !Array.isArray(catalog.cases)
    || catalog.cases.length !== 36) throw new TypeError('LAB-catalogusidentiteit is ongeldig');
  const ids = new Set();
  for (const item of catalog.cases) {
    if (!item || typeof item !== 'object' || !/^[CTPRMXAISF]\d{2}$/u.test(item.case_id)
      || ids.has(item.case_id) || !LANES.size || !Array.isArray(item.lanes)
      || !item.lanes.length || !item.lanes.every((lane) => LANES.has(lane))
      || !Array.isArray(item.eligible_tracks) || !item.eligible_tracks.length
      || !item.eligible_tracks.every((track) => TRACKS.has(track))
      || !Array.isArray(item.semantic_gates) || !item.semantic_gates.length
      || !item.semantic_gates.every((gate) => typeof gate === 'string' && gate.trim())
      || !Array.isArray(item.required_evidence) || !item.required_evidence.length
      || !item.required_evidence.every((kind) => ID.test(String(kind || '')))) {
      throw new TypeError('LAB-catalogus bevat een ongeldige of dubbele case');
    }
    ids.add(item.case_id);
  }
  return catalog;
}

function validatedParticipant(participant) {
  exactKeys(participant, ['id', 'sourceDigest', 'adapterDigest', 'configDigest'], 'participant');
  if (!ID.test(String(participant.id || ''))
    || ![participant.sourceDigest, participant.adapterDigest, participant.configDigest]
      .every((value) => HASH.test(String(value || '')))) {
    throw new TypeError('participant mist stabiele identiteit of digests');
  }
  return Object.freeze({ ...participant });
}

function validatedBudgets(budgets) {
  exactKeys(budgets, [
    'maxTokensPerCase', 'maxWallClockMs', 'maxExternalCostMinor', 'maxInterventions',
  ], 'LAB-budget');
  return Object.freeze({
    maxTokensPerCase: boundedInteger(budgets.maxTokensPerCase, 1, 10_000_000, 'tokenbudget'),
    maxWallClockMs: boundedInteger(budgets.maxWallClockMs, 1_000, 86_400_000, 'tijdsbudget'),
    maxExternalCostMinor: boundedInteger(
      budgets.maxExternalCostMinor, 0, 100_000_000, 'kostenbudget',
    ),
    maxInterventions: boundedInteger(budgets.maxInterventions, 0, 100, 'interventiebudget'),
  });
}

export function planLabRun({
  catalog, runId, participant, lane, track, sealedSetCommitment,
  repetitions = 1,
  budgets = {
    maxTokensPerCase: 100_000,
    maxWallClockMs: 30 * 60_000,
    maxExternalCostMinor: 10_000,
    maxInterventions: 0,
  },
}) {
  const exactCatalog = validateLabCatalog(catalog);
  const exactParticipant = validatedParticipant(participant);
  if (!ID.test(String(runId || '')) || !LANES.has(lane) || !TRACKS.has(track)
    || !HASH.test(String(sealedSetCommitment || ''))) {
    throw new TypeError('LAB-runidentiteit, lane, track of sealed commitment is ongeldig');
  }
  const repeatCount = boundedInteger(repetitions, 1, 20, 'herhalingsaantal');
  const exactBudgets = validatedBudgets(budgets);
  const catalogSha256 = sha256(exactCatalog);
  const instances = exactCatalog.cases.flatMap((benchmarkCase) => (
    Array.from({ length: repeatCount }, (_, offset) => {
      const repetition = offset + 1;
      const eligible = benchmarkCase.lanes.includes(lane)
        && benchmarkCase.eligible_tracks.includes(track);
      const identity = {
        runId, participantId: exactParticipant.id, lane, track,
        caseId: benchmarkCase.case_id, repetition,
      };
      return Object.freeze({
        caseInstanceId: `lab:${sha256(identity)}`,
        caseId: benchmarkCase.case_id,
        suite: benchmarkCase.suite,
        effectClass: benchmarkCase.effect_class,
        repetition,
        opaqueVariantRef: `sealed:${sha256({
          sealedSetCommitment, caseId: benchmarkCase.case_id, repetition,
        })}`,
        eligible,
        eligibilityReason: eligible ? null : 'lane_or_track_not_eligible',
        semanticGateIds: Object.freeze(benchmarkCase.semantic_gates.map(
          (_gate, index) => `${benchmarkCase.case_id}:g${index + 1}`,
        )),
        requiredEvidenceKinds: Object.freeze([...benchmarkCase.required_evidence]),
        status: 'no_run',
      });
    })
  ));
  const body = {
    schema: 1,
    contract: 'LabRunPlan/v1',
    benchmarkId: exactCatalog.benchmark_id,
    benchmarkVersion: exactCatalog.version,
    catalogSha256,
    runId,
    participant: exactParticipant,
    lane,
    track,
    sealedSetCommitment,
    repetitionPolicy: Object.freeze({ count: repeatCount, selection: 'predeclared' }),
    budgets: exactBudgets,
    instances,
  };
  return Object.freeze({ ...body, planSha256: sha256(body) });
}

function cliArguments(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value == null) throw new TypeError('ongeldige LAB-CLI-argumenten');
    result[key.slice(2)] = value;
  }
  return result;
}

async function main() {
  const args = cliArguments(process.argv.slice(2));
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const catalog = JSON.parse(fs.readFileSync(
    path.join(root, 'benchmark/eva-agent-benchmark-v1.json'), 'utf8',
  ));
  const plan = planLabRun({
    catalog,
    runId: args['run-id'],
    participant: {
      id: args.participant,
      sourceDigest: args['source-digest'],
      adapterDigest: args['adapter-digest'],
      configDigest: args['config-digest'],
    },
    lane: args.lane,
    track: args.track,
    sealedSetCommitment: args['sealed-set-commitment'],
    repetitions: args.repetitions == null ? 1 : Number(args.repetitions),
  });
  process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`LAB-planner faalde gesloten: ${error.message}\n`);
    process.exitCode = 1;
  });
}
