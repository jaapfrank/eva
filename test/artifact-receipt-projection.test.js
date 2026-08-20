import './isolated-data-dir.js';
import assert from 'node:assert/strict';
import test from 'node:test';

import { createTurnEnvelope } from '../src/mind/understanding.js';

const hash = (character) => character.repeat(64);
const versionRef = (character) => `artifact-version:${hash(character)}`;

function sealedReceipt(overrides = {}) {
  return {
    contract: 'ArtifactVersionReceipt/v1',
    versionRef: versionRef('1'),
    objectRef: `sha256:${hash('a')}`,
    path: 'data/workspace/inkomstenkans-resultaat.md',
    sha256: hash('a'),
    bytes: 431,
    role: 'result',
    understandingSha256: hash('b'),
    requirementId: 'r2',
    previousVersionRef: versionRef('0'),
    provenanceSha256: hash('c'),
    ...overrides,
  };
}

test('TurnEnvelope projecteert een sealed ArtifactVersionReceipt volledig maar zonder vrije authority', () => {
  const receipt = sealedReceipt();
  const envelope = createTurnEnvelope({
    text: 'Ga verder met het resultaat.',
    context: { artifactReceipts: [receipt] },
  });

  assert.deepEqual(envelope.context.artifactReceipts, [{
    contract: 'ArtifactVersionReceipt/v1',
    versionRef: versionRef('1'),
    objectRef: `sha256:${hash('a')}`,
    path: 'data/workspace/inkomstenkans-resultaat.md',
    sha256: hash('a'),
    bytes: 431,
    role: 'result',
    understandingSha256: hash('b'),
    requirementId: 'r2',
    previousVersionRef: versionRef('0'),
    provenanceSha256: hash('c'),
  }]);
  assert.ok(Object.isFrozen(envelope.context.artifactReceipts[0]));

  const expanded = createTurnEnvelope({
    text: 'Ga verder met het resultaat.', context: { artifactReceipts: [{
      ...receipt, content: 'mag nooit in de interpretercontext komen',
      authoritySha256: hash('e'),
    }] },
  });
  assert.deepEqual(expanded.context.artifactReceipts, [],
    'een v1-receipt met vrije content of authority is geen exact storereceipt');
});

test('TurnEnvelope accepteert alle vier artifactrollen en behoudt legacyprojectie begrensd', () => {
  const roles = ['source', 'test', 'fixture', 'result'];
  const receipts = roles.map((role, index) => index < 2
    ? sealedReceipt({ role, path: `data/workspace/${role}.md`,
      previousVersionRef: index === 0 ? null : versionRef('2') })
    : { path: `data/workspace/${role}.json`, sha256: hash('d'), role,
      understandingSha256: hash('e'), requirementId: 'r12', content: 'niet projecteren' });
  const projected = createTurnEnvelope({
    text: 'Corrigeer en controleer de artefacten.', context: { artifactReceipts: receipts },
  }).context.artifactReceipts;

  assert.deepEqual(projected.map(({ role }) => role), roles);
  assert.equal(projected[0].previousVersionRef, null);
  assert.equal(projected[1].previousVersionRef, versionRef('2'));
  assert.deepEqual(projected[2], {
    path: 'data/workspace/fixture.json', sha256: hash('d'), role: 'fixture',
    understandingSha256: hash('e'), requirementId: 'r12',
  });
  assert.equal('content' in projected[2], false);
});

test('TurnEnvelope weigert onvolledige of onderling strijdige sealed receipts fail-closed', () => {
  const invalid = [
    sealedReceipt({ contract: 'ArtifactVersionReceipt/v2' }),
    sealedReceipt({ objectRef: `sha256:${hash('f')}` }),
    sealedReceipt({ bytes: -1 }),
    sealedReceipt({ bytes: 2 * 1024 * 1024 + 1 }),
    sealedReceipt({ previousVersionRef: 'artifact-version:ongeldig' }),
    sealedReceipt({ provenanceSha256: hash('G') }),
    sealedReceipt({ versionRef: `artifact-version:${hash('G')}` }),
    sealedReceipt({ path: 'data/workspace/../buiten.md' }),
  ];
  const projected = createTurnEnvelope({
    text: 'Ga door.', context: { artifactReceipts: invalid },
  }).context.artifactReceipts;

  assert.deepEqual(projected, []);
});

test('TurnEnvelope hanteert dezelfde twaalf-artifactgrens als de store', () => {
  const receipts = Array.from({ length: 13 }, (_, index) => ({
    path: `data/workspace/resultaat-${index}.md`, sha256: hash('a'), role: 'result',
    understandingSha256: hash('b'), requirementId: 'r1',
  }));
  const projected = createTurnEnvelope({
    text: 'Ga door met alle resultaten.', context: { artifactReceipts: receipts },
  }).context.artifactReceipts;

  assert.equal(projected.length, 12);
  assert.equal(projected.at(-1).path, 'data/workspace/resultaat-11.md');
});
