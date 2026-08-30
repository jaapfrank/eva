import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';

import { loadAndEvaluate } from '../scripts/lab/check-competitor-capability-matrix.mjs';

const source = JSON.parse(fs.readFileSync('benchmark/competitor-capability-matrix-v1.json', 'utf8'));
const releaseWatch = fs.readFileSync('component-watch.json');

function capabilityClaim(matrix, categoryId, competitor, result, claimId) {
  return {
    claim_id: claimId,
    claim_scope: 'full_category_acceptance',
    category_id: categoryId,
    competitor,
    result,
    independent_verdict: 'pass',
    eva_binding: matrix.pinned_participants.eva2,
    competitor_binding: matrix.pinned_participants[competitor],
  };
}

function withFixture(matrix, claims, assertion, { corruptHash = false } = {}) {
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-competitor-gate-'));
  try {
    const benchmarkDir = path.join(fixtureRoot, 'benchmark');
    const evidenceDir = path.join(fixtureRoot, 'evidence');
    fs.mkdirSync(benchmarkDir);
    fs.mkdirSync(evidenceDir);
    fs.writeFileSync(path.join(fixtureRoot, 'component-watch.json'), releaseWatch);
    const receipt = JSON.stringify({
      contract: 'EvaCompetitorCapabilityReceipt/v1',
      competitor_capability_claims: claims,
    });
    fs.writeFileSync(path.join(evidenceDir, 'claims.json'), receipt);
    const digest = crypto.createHash('sha256').update(receipt).digest('hex');
    matrix.evidence_catalog = [{
      id: 'test-receipt',
      path: 'evidence/claims.json',
      sha256: corruptHash ? '0'.repeat(64) : digest,
    }];
    const matrixPath = path.join(benchmarkDir, 'matrix.json');
    fs.writeFileSync(matrixPath, JSON.stringify(matrix));
    assertion(matrixPath);
  } finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
}

test('blocks the current general superiority claim while comparisons are no_run', () => {
  const result = loadAndEvaluate();
  assert.equal(result.allowed, false);
  assert.ok(result.blockers.some((blocker) => blocker.endsWith(':no_run')));
});

test('allows only exact won claims read from hashed full-category receipts', () => {
  const complete = structuredClone(source);
  const claims = [];
  for (const category of complete.categories) {
    for (const [competitor, comparison] of Object.entries(category.comparisons)) {
      const claimId = `${category.id}-${competitor}`;
      comparison.status = 'won';
      comparison.evidence_refs = [{ receipt_id: 'test-receipt', claim_id: claimId }];
      claims.push(capabilityClaim(complete, category.id, competitor, 'won', claimId));
    }
  }
  withFixture(complete, claims, (matrixPath) => {
    assert.deepEqual(loadAndEvaluate(matrixPath), { allowed: true, blockers: [] });
  });
});

test('rejects a receipt claim for the wrong category', () => {
  const misbound = structuredClone(source);
  const comparison = misbound.categories[0].comparisons.openclaw;
  comparison.status = 'won';
  comparison.evidence_refs = [{ receipt_id: 'test-receipt', claim_id: 'misbound' }];
  const claim = capabilityClaim(
    misbound,
    'conversation_persona',
    'openclaw',
    'won',
    'misbound',
  );
  withFixture(misbound, [claim], (matrixPath) => {
    assert.throws(() => loadAndEvaluate(matrixPath), /verkeerde binding/u);
  });
});

test('blocks ties, losses and critical failures even with matching receipts', () => {
  const failed = structuredClone(source);
  const lost = failed.categories[0].comparisons.openclaw;
  lost.status = 'lost';
  lost.evidence_refs = [{ receipt_id: 'test-receipt', claim_id: 'lost' }];
  const tied = failed.categories[0].comparisons.agent_zero;
  tied.status = 'tied';
  tied.evidence_refs = [{ receipt_id: 'test-receipt', claim_id: 'tied' }];
  failed.categories[1].comparisons.hermes.critical_failures.push('privacy_leak');
  const claims = [
    capabilityClaim(failed, 'install_update_recovery', 'openclaw', 'lost', 'lost'),
    capabilityClaim(failed, 'install_update_recovery', 'agent_zero', 'tied', 'tied'),
  ];
  withFixture(failed, claims, (matrixPath) => {
    const result = loadAndEvaluate(matrixPath);
    assert.equal(result.allowed, false);
    assert.ok(result.blockers.includes('install_update_recovery/openclaw:lost'));
    assert.ok(result.blockers.includes('install_update_recovery/agent_zero:tied'));
    assert.ok(result.blockers.includes('conversation_persona/hermes:critical:privacy_leak'));
  });
});

test('hard-coded competitors and categories cannot be removed by editing JSON', () => {
  const incomplete = structuredClone(source);
  delete incomplete.categories[0].comparisons.agent_zero;
  withFixture(incomplete, [], (matrixPath) => {
    assert.throws(() => loadAndEvaluate(matrixPath), /agent_zero/u);
  });

  const missingCategory = structuredClone(source);
  missingCategory.categories = missingCategory.categories.slice(1);
  withFixture(missingCategory, [], (matrixPath) => {
    assert.throws(() => loadAndEvaluate(matrixPath), /install_update_recovery/u);
  });
});

test('rejects receipt bytes that do not match the catalog hash', () => {
  withFixture(structuredClone(source), [], (matrixPath) => {
    assert.throws(() => loadAndEvaluate(matrixPath), /evidencehash wijkt af/u);
  }, { corruptHash: true });
});

test('rejects a superiority matrix after any watched competitor release changes', () => {
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-competitor-watch-'));
  try {
    const benchmarkDir = path.join(fixtureRoot, 'benchmark');
    fs.mkdirSync(benchmarkDir);
    fs.writeFileSync(
      path.join(benchmarkDir, 'matrix.json'),
      JSON.stringify({ ...structuredClone(source), evidence_catalog: [] }),
    );
    const watch = JSON.parse(releaseWatch.toString('utf8'));
    watch.competitors.find((row) => row.repository === 'openclaw/openclaw').currentRelease =
      'v9999.1.0';
    fs.writeFileSync(path.join(fixtureRoot, 'component-watch.json'), JSON.stringify(watch));
    assert.throws(
      () => loadAndEvaluate(path.join(benchmarkDir, 'matrix.json')),
      /loopt achter op releasewatch/u,
    );
  } finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
});
