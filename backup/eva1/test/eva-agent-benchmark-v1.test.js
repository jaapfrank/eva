import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const catalogUrl = new URL('../benchmark/eva-agent-benchmark-v1.json', import.meta.url);
const catalog = JSON.parse(fs.readFileSync(catalogUrl, 'utf8'));

const expectedSuiteCounts = {
  autonomy: 3,
  conversation_identity: 4,
  corrections: 3,
  initiative: 3,
  memory_continuity: 4,
  partial_success: 3,
  recovery: 4,
  safe_closure: 4,
  silence_proactivity: 4,
  tool_calling: 4,
};

test('LAB-36 catalog has exactly 36 unique, structurally complete cases', () => {
  assert.equal(catalog.contract, 'LivedAgentBenchmark/v1');
  assert.equal(catalog.benchmark_id, 'LAB-36');
  assert.equal(catalog.case_count, 36);
  assert.equal(catalog.cases.length, 36);

  const ids = catalog.cases.map((benchmarkCase) => benchmarkCase.case_id);
  assert.equal(new Set(ids).size, 36);

  const suiteCounts = {};
  const knownTracks = new Set(Object.keys(catalog.tracks));
  const knownLanes = new Set(Object.keys(catalog.lanes));
  const knownEffectClasses = new Set(Object.keys(catalog.effect_classes));

  for (const benchmarkCase of catalog.cases) {
    assert.match(benchmarkCase.case_id, /^[CTPRMXAISF]\d{2}$/);
    assert.equal(typeof benchmarkCase.title, 'string');
    assert.ok(benchmarkCase.title.length > 0);
    assert.equal(typeof benchmarkCase.stimulus, 'string');
    assert.ok(benchmarkCase.stimulus.length > 0);
    assert.ok(knownEffectClasses.has(benchmarkCase.effect_class));
    assert.ok(knownTracks.has(benchmarkCase.track));
    assert.ok(Array.isArray(benchmarkCase.eligible_tracks));
    assert.ok(benchmarkCase.eligible_tracks.includes(benchmarkCase.track));
    assert.ok(benchmarkCase.eligible_tracks.every((track) => knownTracks.has(track)));
    assert.ok(Array.isArray(benchmarkCase.lanes));
    assert.ok(benchmarkCase.lanes.length > 0);
    assert.ok(benchmarkCase.lanes.every((lane) => knownLanes.has(lane)));
    assert.ok(Array.isArray(benchmarkCase.semantic_gates));
    assert.ok(benchmarkCase.semantic_gates.length > 0);
    assert.ok(benchmarkCase.semantic_gates.every((gate) => typeof gate === 'string'));
    assert.ok(Array.isArray(benchmarkCase.required_evidence));
    assert.ok(benchmarkCase.required_evidence.length > 0);
    assert.ok(benchmarkCase.required_evidence.every((item) => typeof item === 'string'));
    assert.equal(Object.hasOwn(benchmarkCase, 'golden_answer'), false);
    assert.equal(Object.hasOwn(benchmarkCase, 'expected_answer'), false);

    suiteCounts[benchmarkCase.suite] = (suiteCounts[benchmarkCase.suite] ?? 0) + 1;
  }

  assert.deepEqual(suiteCounts, expectedSuiteCounts);
});
