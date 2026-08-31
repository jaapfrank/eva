import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const inventory = JSON.parse(fs.readFileSync(
  path.join(root, 'docs/eva-repository-inventory.generated.json'),
  'utf8',
));
const disposition = JSON.parse(fs.readFileSync(
  path.join(root, 'docs/repository-inventory-disposition-v1.json'),
  'utf8',
));

function errorKey(error) {
  return `${error.code}\0${error.path}`;
}

function selectedErrors(group, alreadySelected) {
  if (Array.isArray(group.paths)) {
    const paths = new Set(group.paths);
    return inventory.policyErrors.filter((error) => (
      error.code === group.currentPolicyCode && paths.has(error.path)
    ));
  }
  const selector = group.selector;
  if (selector.kind === 'path-prefix') {
    return inventory.policyErrors.filter((error) => (
      error.code === group.currentPolicyCode && error.path.startsWith(selector.value)
    ));
  }
  if (selector.kind === 'directory-basename-list') {
    const paths = new Set(selector.basenames.map((basename) => (
      `${selector.directory}/${basename}`
    )));
    return inventory.policyErrors.filter((error) => (
      error.code === group.currentPolicyCode && paths.has(error.path)
    ));
  }
  assert.equal(selector.kind, 'untracked-direct-json-remainder');
  return inventory.policyErrors.filter((error) => (
    error.code === group.currentPolicyCode
    && path.posix.dirname(error.path) === selector.directory
    && error.path.endsWith('.json')
    && !alreadySelected.has(errorKey(error))
  ));
}

test('inventory disposition covers every current policy error exactly once', () => {
  assert.equal(disposition.schema, 'EvaRepositoryInventoryDisposition/v1');
  assert.equal(disposition.scope.policyErrorCount, inventory.policyErrors.length);
  assert.equal(
    disposition.scope.policyErrorsSha256,
    createHash('sha256').update(JSON.stringify(inventory.policyErrors)).digest('hex'),
  );
  assert.equal(disposition.globalSafety.destructiveActionAuthorized, false);
  assert.equal(disposition.globalSafety.originalFilesChanged, false);

  const selected = new Set();
  const totals = Object.fromEntries(
    Object.keys(disposition.totalsByProposedDisposition).map((name) => [name, 0]),
  );
  for (const group of disposition.groups) {
    const matches = selectedErrors(group, selected);
    assert.equal(matches.length, group.count, group.id);
    for (const error of matches) {
      const key = errorKey(error);
      assert.equal(selected.has(key), false, `${group.id}: ${key}`);
      selected.add(key);
    }
    assert.ok(Object.hasOwn(totals, group.proposedDisposition), group.id);
    totals[group.proposedDisposition] += group.count;
  }

  assert.equal(selected.size, inventory.policyErrors.length);
  assert.deepEqual(totals, disposition.totalsByProposedDisposition);
  assert.equal(
    Object.values(totals).reduce((sum, count) => sum + count, 0),
    inventory.policyErrors.length,
  );
  assert.equal(
    disposition.scope.untrackedCount,
    inventory.policyErrors.filter((error) => (
      error.code === 'UNTRACKED_REPOSITORY_FILE'
    )).length,
  );
  assert.equal(
    disposition.scope.trackedUnresolvedCount,
    inventory.policyErrors.length - disposition.scope.untrackedCount,
  );
});
