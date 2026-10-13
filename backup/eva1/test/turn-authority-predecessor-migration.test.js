import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import test from 'node:test';

function probe(mode) {
  const fixture = path.join(import.meta.dirname, 'turn-authority-predecessor-fixture.mjs');
  const child = spawnSync(process.execPath, [fixture, mode], {
    cwd: path.resolve(import.meta.dirname, '..'),
    env: { ...process.env },
    encoding: 'utf8',
    timeout: 20_000,
  });
  assert.equal(child.status, 0, `${child.stdout}\n${child.stderr}`);
  const line = child.stdout.split(/\r?\n/u)
    .find((entry) => entry.startsWith('PREDECESSOR_PROBE='));
  assert.ok(line, child.stdout);
  return JSON.parse(line.slice('PREDECESSOR_PROBE='.length));
}

test('alleen de lege exacte predecessor migreert naar reconstructeerbaar schema', () => {
  const result = probe('empty');
  assert.equal(result.ok, true, result.error);
  assert.equal(result.columns.includes('source_body_json'), true);
  assert.deepEqual(result.status, {
    contract: 'TurnUnderstandingAuthorityStoreStatus/v1', available: true,
  });
  assert.equal(result.httpTables, 0);
  assert.equal(result.httpBoundary, 0);
});

test('een geldige web-predecessorrij migreert exact met null body', () => {
  const result = probe('web-row');
  assert.equal(result.ok, true, result.error);
  assert.deepEqual(result.row, {
    channel: 'web', message_id: 'predecessor-web-1', source_body_json: null,
  });
  assert.equal(result.status.available, true);
});

test('het exacte vorige schema migreert authorityrijen bytegelijk', () => {
  const result = probe('prior-web-row');
  assert.equal(result.ok, true, result.error);
  assert.equal(result.status.available, true);
  assert.equal(result.beforeRowsBase64, result.afterRowsBase64);
  assert.deepEqual(result.row, {
    channel: 'web', message_id: 'predecessor-web-1', source_body_json: null,
  });
});

test('HTTP-v1 migreert met bytegelijke hash en output naar een exacte claim-ref', () => {
  const result = probe('current-http-v1-row');
  assert.equal(result.ok, true, result.error);
  assert.equal(result.status.available, true);
  assert.equal(result.beforeRowsBase64, result.afterRowsBase64);
  assert.equal(result.beforeExecutionOutputsBase64, result.afterExecutionOutputsBase64);
  assert.equal(result.authority.contract, 'TurnUnderstandingExecutionAuthority/v1');
  assert.match(result.authority.authority_sha256, /^[a-f0-9]{64}$/u);
  assert.match(result.authority.source_claim_event_ref,
    /^http-claim-event:[a-f0-9]{64}$/u);
});

test('gemigreerde HTTP-v1 krijgt een live V2-successor en blijft exact leesbaar', () => {
  const result = probe('current-http-v1-successor');
  assert.equal(result.ok, true, result.error);
  assert.equal(result.status.available, true);
  assert.equal(result.successor.v1RowsBase64, result.beforeRowsBase64);
  assert.equal(result.afterExecutionOutputsBase64, result.beforeExecutionOutputsBase64);
  assert.deepEqual(result.successor.history.map((row) => ({
    contract: row.contract, generation: row.source_generation,
  })), [{
    contract: 'TurnUnderstandingExecutionAuthority/v1', generation: 1,
  }, {
    contract: 'TurnUnderstandingExecutionAuthority/v2', generation: 2,
  }]);
  assert.match(result.successor.history[0].source_claim_event_ref,
    /^http-claim-event:[a-f0-9]{64}$/u);
  assert.equal(result.successor.history[0].authority_sha256,
    result.authority.authority_sha256);
  assert.deepEqual(result.successor.latest, {
    contract: 'TurnUnderstandingExecutionAuthority/v2',
    sourceGeneration: 2,
    sourceClaimEventRef: result.successor.history[1].source_claim_event_ref,
    authoritySha256: result.successor.history[1].authority_sha256,
  });
});

test('HTTP-v1 met acquire en renew op dezelfde milliseconde migreert niet', () => {
  const result = probe('current-http-v1-ambiguous');
  assert.equal(result.ok, false);
  assert.match(`${result.error} ${result.cause}`, /unieke claimeventbinding|claim/iu);
  assert.equal(result.rollback.afterBase64, result.rollback.beforeBase64);
  assert.equal(result.rollback.afterColumns.includes('source_claim_event_ref'), false);
});

test('een zelfgehashte niet-canonieke HTTP issued_ts migreert niet', () => {
  const result = probe('prior-web-noncanonical');
  assert.equal(result.ok, false);
  assert.match(`${result.error} ${result.cause}`, /ongeldig authoritybewijs/iu);
});

test('native Telegram en HA-voice v1 migreren bytegelijk met null claim-ref', () => {
  const result = probe('prior-native-rows');
  assert.equal(result.ok, true, result.error);
  assert.equal(result.status.available, true);
  assert.equal(result.afterRowsBase64, result.beforeRowsBase64);
  assert.equal(result.afterExecutionOutputsBase64, result.beforeExecutionOutputsBase64);
  assert.equal(result.authorityRows.length, 2);
  assert.equal(result.authorityRows.every((row) => (
    row.contract === 'TurnUnderstandingExecutionAuthority/v1'
      && row.source_claim_event_ref === null
  )), true);
  assert.equal(result.authorityRecords.every(Boolean), true);
  const records = result.authorityRecords.sort((left, right) => (
    left.channel.localeCompare(right.channel)
  ));
  assert.deepEqual(records.map((record) => ({
    channel: record.channel, sourceKind: record.sourceKind,
    sourceClaimEventRef: record.sourceClaimEventRef,
  })), [{
    channel: 'telegram', sourceKind: 'telegram_ingress', sourceClaimEventRef: null,
  }, {
    channel: 'voice', sourceKind: 'voice_ingress', sourceClaimEventRef: null,
  }]);
  for (const record of records) {
    assert.equal(createHash('sha256').update(record.sourceBodyJson).digest('hex'),
      record.sourceSha256);
    assert.match(record.authoritySha256, /^[a-f0-9]{64}$/u);
  }
});

test('een predecessor met native rijen zonder bronbytes faalt gesloten', () => {
  const result = probe('native-row');
  assert.equal(result.ok, false);
  assert.match(`${result.error} ${result.cause}`, /native authority zonder reconstructeerbare bronbytes/u);
});
