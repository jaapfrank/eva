import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');

function isolatedModuleProbe(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-learning-availability-'));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: projectRoot,
      env: { ...process.env, EVA_DATA_DIR: dataDir },
      encoding: 'utf8',
      timeout: 30_000,
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return JSON.parse(result.stdout.trim());
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

function isolatedSequentialProbe(sources) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-learning-sequence-'));
  try {
    return sources.map((source) => {
      const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
        cwd: projectRoot,
        env: { ...process.env, EVA_DATA_DIR: dataDir },
        encoding: 'utf8',
        timeout: 30_000,
      });
      assert.equal(result.status, 0, result.stderr || result.stdout);
      return JSON.parse(result.stdout.trim());
    });
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

test('vers behavior-schema is beschikbaar en biedt Eva de optionele typed proef', () => {
  const result = isolatedModuleProbe(`
    const learning = await import('./src/mind/learning.js');
    const { TOOLS } = await import('./src/mind/tools.js');
    const exploration = TOOLS.find((tool) => tool.name === 'verkenning_bijhouden');
    console.log(JSON.stringify({
      status: learning.behaviorLearningStatus(),
      offered: Object.hasOwn(exploration.parameters.properties, 'gedragsproef'),
    }));
  `);
  assert.equal(result.status.enabled, true);
  assert.equal(result.offered, true);
});

test('onbekend niet-leeg schema blijft bytegelijk en schakelt alleen gedragsleren uit', () => {
  const result = isolatedModuleProbe(`
    const { db } = await import('./src/db.js');
    db.exec("CREATE TABLE eva_behavior_experiments (id INTEGER PRIMARY KEY, legacy_text TEXT); INSERT INTO eva_behavior_experiments (id,legacy_text) VALUES (7,'behouden');");
    const before = db.prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name='eva_behavior_experiments'").get().sql;
    const learning = await import('./src/mind/learning.js');
    await import('./src/mind/agent.js');
    const { TOOLS } = await import('./src/mind/tools.js');
    const exploration = TOOLS.find((tool) => tool.name === 'verkenning_bijhouden');
    const after = db.prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name='eva_behavior_experiments'").get().sql;
    const legacy = db.prepare('SELECT * FROM eva_behavior_experiments WHERE id=7').get();
    console.log(JSON.stringify({
      status: learning.behaviorLearningStatus(), before, after, legacy,
      offered: Object.hasOwn(exploration.parameters.properties, 'gedragsproef'),
      claim: learning.claimBehaviorCanaryForPerson('Jaap', {
        turnId: 'disabled-turn', conversationId: 'web:disabled', dialogueAct: 'chat',
      }),
    }));
  `);
  assert.deepEqual(result.status, { enabled: false, reason: 'schema_mismatch' });
  assert.equal(result.before, result.after);
  assert.deepEqual(result.legacy, { id: 7, legacy_text: 'behouden' });
  assert.equal(result.offered, false);
  assert.equal(result.claim, null);
});

test('een gelijknamige maar verzwakte trigger schakelt leren uit zonder schemaherstel', () => {
  const [created, restarted] = isolatedSequentialProbe([`
    const learning = await import('./src/mind/learning.js');
    const { db } = await import('./src/db.js');
    db.exec(\`DROP TRIGGER eva_behavior_experiments_immutable_candidate;
      CREATE TRIGGER eva_behavior_experiments_immutable_candidate
      BEFORE UPDATE ON eva_behavior_experiments
      WHEN NEW.experiment_key IS NOT OLD.experiment_key
      BEGIN SELECT RAISE(ABORT,'behavior learning candidates are immutable'); END;\`);
    console.log(JSON.stringify({ status: learning.behaviorLearningStatus() }));
  `, `
    const learning = await import('./src/mind/learning.js');
    const { db } = await import('./src/db.js');
    const sql = db.prepare("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='eva_behavior_experiments_immutable_candidate'").get().sql;
    console.log(JSON.stringify({ status: learning.behaviorLearningStatus(), sql }));
  `]);
  assert.equal(created.status.enabled, true);
  assert.deepEqual(restarted.status, { enabled: false, reason: 'schema_mismatch' });
  assert.doesNotMatch(restarted.sql, /NEW\.candidate_sha256/u,
    'de optionele feature mag een onbekende trigger niet stil repareren');
});
