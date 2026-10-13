import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { loadAndEvaluate } from '../scripts/lab/check-memory-augmentation-gate.mjs';

const source = JSON.parse(fs.readFileSync('benchmark/memory-augmentation-gate-v1.json', 'utf8'));

function withFixture(gate, receipt, assertion, { corruptHash = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-memory-augmentation-'));
  try {
    fs.mkdirSync(path.join(root, 'benchmark'));
    fs.mkdirSync(path.join(root, 'evidence'));
    const bytes = JSON.stringify(receipt);
    fs.writeFileSync(path.join(root, 'evidence', 'receipt.json'), bytes);
    gate.evidence_catalog = [{
      id: 'receipt',
      path: 'evidence/receipt.json',
      sha256: corruptHash ? '0'.repeat(64) : crypto.createHash('sha256').update(bytes).digest('hex'),
    }];
    const gatePath = path.join(root, 'benchmark', 'gate.json');
    fs.writeFileSync(gatePath, JSON.stringify(gate));
    assertion(gatePath);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function claim(gate, id, decision) {
  const pinName = id === 'mnemosyne_hot_projection' ? 'mnemosyne' : 'honcho';
  const pin = gate.pins[pinName];
  return {
    claim_id: `${id}-decision`,
    claim_scope: 'full_augmentation_decision',
    augmentation_id: id,
    decision,
    version: pin.version,
    source_commit: pin.source_commit,
    artifact_digest: pin.artifact_digest,
    operational_authority: 'eva_postgresql',
    hindsight_retained_as_long_term_memory: true,
    optional_layer_became_canonical_authority: false,
    independent_verdict: 'pass',
    hard_cases: gate.required_hard_cases.map((caseId) => ({ id: caseId, status: 'pass' })),
    metrics: gate.required_metrics.map((metricId, index) => ({
      id: metricId,
      status: 'measured',
      value: index + 1,
    })),
    benefit_proven: decision === 'accepted',
    dependency_lock: 'pass',
    sbom_vulnerability_license: 'pass',
    export_rebuild: 'pass',
    upgrade: 'pass',
    rollback: 'pass',
  };
}

function decidedFixture(decisions = ['accepted', 'rejected']) {
  const gate = structuredClone(source);
  gate.status = 'decided';
  const claims = [];
  Object.keys(gate.augmentations).forEach((id, index) => {
    const pinName = id === 'mnemosyne_hot_projection' ? 'mnemosyne' : 'honcho';
    gate.pins[pinName].artifact_digest = `sha256:${String(index + 1).repeat(64)}`;
    gate.augmentations[id] = {
      ...gate.augmentations[id],
      status: 'decided',
      decision: decisions[index],
      evidence_ref: { receipt_id: 'receipt', claim_id: `${id}-decision` },
    };
    claims.push(claim(gate, id, decisions[index]));
  });
  gate.combination = {
    status: decisions.every((decision) => decision === 'accepted') ? 'pending_single_layer_decisions' : 'not_applicable',
    decision: null,
    evidence_ref: null,
    rule: source.combination.rule,
  };
  return {
    gate,
    receipt: {
      contract: 'EvaMemoryAugmentationReceipt/v1',
      memory_augmentation_claims: claims,
    },
  };
}

test('keeps the current optional layers blocked without changing Hindsight', () => {
  assert.deepEqual(loadAndEvaluate(), {
    allowed: false,
    blockers: [
      'mnemosyne_hot_projection:not_materialized',
      'honcho_person_model:not_materialized',
      'decision:open',
    ],
  });
});

test('hard-codes Hindsight as L2 and forbids either augmentation replacing it', () => {
  for (const field of ['mnemosyne_replaces_hindsight', 'honcho_replaces_hindsight']) {
    const gate = structuredClone(source);
    gate.architecture[field] = true;
    withFixture(gate, { contract: 'EvaMemoryAugmentationReceipt/v1' }, (gatePath) => {
      assert.throws(() => loadAndEvaluate(gatePath), /memoryrollen of authoritygrens wijken af/u);
    });
  }
});

test('keeps the memory dashboard independent and behind Eva authority', () => {
  for (const [field, invalid] of [
    ['memory_dashboard_is_provider_independent', false],
    ['memory_dashboard_may_bypass_eva_authority', true],
  ]) {
    const gate = structuredClone(source);
    gate.architecture[field] = invalid;
    withFixture(gate, { contract: 'EvaMemoryAugmentationReceipt/v1' }, (gatePath) => {
      assert.throws(() => loadAndEvaluate(gatePath), /memoryrollen of authoritygrens wijken af/u);
    });
  }
});

test('required privacy cases and resource metrics cannot be edited away', () => {
  for (const [field, expected] of [
    ['required_hard_cases', /required_hard_cases/u],
    ['required_metrics', /required_metrics/u],
  ]) {
    const gate = structuredClone(source);
    gate[field].pop();
    withFixture(gate, { contract: 'EvaMemoryAugmentationReceipt/v1' }, (gatePath) => {
      assert.throws(() => loadAndEvaluate(gatePath), expected);
    });
  }
});

test('allows evidence-bound acceptance or rejection while Hindsight remains L2', () => {
  const fixture = decidedFixture(['accepted', 'rejected']);
  withFixture(fixture.gate, fixture.receipt, (gatePath) => {
    assert.deepEqual(loadAndEvaluate(gatePath), { allowed: true, blockers: [] });
  });
});

test('does not accept a layer with a failed privacy case', () => {
  const fixture = decidedFixture(['accepted', 'rejected']);
  fixture.receipt.memory_augmentation_claims[0].hard_cases[3].status = 'fail';
  withFixture(fixture.gate, fixture.receipt, (gatePath) => {
    assert.throws(() => loadAndEvaluate(gatePath), /geen volledig groen bewijs/u);
  });
});

test('requires a separate combination decision if both layers pass alone', () => {
  const fixture = decidedFixture(['accepted', 'accepted']);
  withFixture(fixture.gate, fixture.receipt, (gatePath) => {
    const result = loadAndEvaluate(gatePath);
    assert.equal(result.allowed, false);
    assert.ok(result.blockers.includes('combination:pending_single_layer_decisions'));
  });
});

test('accepts both layers together only with a separately measured combination claim', () => {
  const fixture = decidedFixture(['accepted', 'accepted']);
  fixture.gate.combination = {
    status: 'decided',
    decision: 'accepted',
    evidence_ref: { receipt_id: 'receipt', claim_id: 'combination-decision' },
    rule: source.combination.rule,
  };
  fixture.receipt.memory_combination_claims = [{
    claim_id: 'combination-decision',
    claim_scope: 'combined_augmentation_decision',
    decision: 'accepted',
    independent_verdict: 'pass',
    hindsight_retained_as_long_term_memory: true,
    candidate_claim_keys: {
      mnemosyne_hot_projection: 'receipt#mnemosyne_hot_projection-decision',
      honcho_person_model: 'receipt#honcho_person_model-decision',
    },
    hard_cases: fixture.gate.required_hard_cases.map((id) => ({ id, status: 'pass' })),
    metrics: fixture.gate.required_metrics.map((id, index) => ({
      id,
      status: 'measured',
      value: index + 1,
    })),
    benefit_proven: true,
  }];
  withFixture(fixture.gate, fixture.receipt, (gatePath) => {
    assert.deepEqual(loadAndEvaluate(gatePath), { allowed: true, blockers: [] });
  });
});

test('rejects changed receipt bytes through the evidence hash', () => {
  const fixture = decidedFixture();
  withFixture(fixture.gate, fixture.receipt, (gatePath) => {
    assert.throws(() => loadAndEvaluate(gatePath), /evidencehash wijkt af/u);
  }, { corruptHash: true });
});
