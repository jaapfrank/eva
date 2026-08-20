import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
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
});

test('een geldige web-predecessorrij migreert exact met null body', () => {
  const result = probe('web-row');
  assert.equal(result.ok, true, result.error);
  assert.deepEqual(result.row, {
    channel: 'web', message_id: 'predecessor-web-1', source_body_json: null,
  });
  assert.equal(result.status.available, true);
});

test('een predecessor met native rijen zonder bronbytes faalt gesloten', () => {
  const result = probe('native-row');
  assert.equal(result.ok, false);
  assert.match(`${result.error} ${result.cause}`, /native authority zonder reconstructeerbare bronbytes/u);
});
