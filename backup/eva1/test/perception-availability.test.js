import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');

function sequentialProbe(sources) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-perception-schema-'));
  try {
    return sources.map((source) => {
      const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
        cwd: projectRoot,
        env: { ...process.env, EVA_DATA_DIR: dataDir },
        encoding: 'utf8', timeout: 30_000,
      });
      assert.equal(result.status, 0, result.stderr || result.stdout);
      return JSON.parse(result.stdout.trim());
    });
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

test('een gelijknamige verzwakte deurbeltrigger schakelt alleen de private feature uit', () => {
  const [created, restarted] = sequentialProbe([`
    const attention = await import('./src/mind/perceptionattention.js');
    const { db } = await import('./src/db.js');
    db.exec(\`DROP TRIGGER eva_doorbell_incidents_immutable;
      CREATE TRIGGER eva_doorbell_incidents_immutable
      BEFORE UPDATE ON eva_doorbell_incidents
      WHEN NEW.incident_key IS NOT OLD.incident_key
      BEGIN SELECT RAISE(ABORT,'doorbell incident evidence is immutable'); END;\`);
    console.log(JSON.stringify(attention.perceptionAttentionStatus()));
  `, `
    const attention = await import('./src/mind/perceptionattention.js');
    await import('./src/mind/agent.js');
    console.log(JSON.stringify(attention.perceptionAttentionStatus()));
  `]);
  assert.equal(created.enabled, true);
  assert.deepEqual(restarted, { enabled: false, reason: 'schema_mismatch' });
});

test('een vreemd genaamde trigger op de incidenttabellen faalt eveneens exact dicht', () => {
  const [created, restarted] = sequentialProbe([`
    const attention = await import('./src/mind/perceptionattention.js');
    const { db } = await import('./src/db.js');
    db.exec(\`CREATE TRIGGER foreign_incident_side_effect
      AFTER UPDATE ON eva_doorbell_incidents
      BEGIN SELECT 1; END;\`);
    console.log(JSON.stringify(attention.perceptionAttentionStatus()));
  `, `
    const attention = await import('./src/mind/perceptionattention.js');
    await import('./src/mind/agent.js');
    console.log(JSON.stringify(attention.perceptionAttentionStatus()));
  `]);
  assert.equal(created.enabled, true);
  assert.deepEqual(restarted, { enabled: false, reason: 'schema_mismatch' });
});

test('alleen de letterkast van een gequote status wijzigen blijft schema-corruptie', () => {
  const [created, restarted] = sequentialProbe([`
    const attention = await import('./src/mind/perceptionattention.js');
    const { db } = await import('./src/db.js');
    db.exec(\`DROP TRIGGER eva_doorbell_incidents_frame_release;
      CREATE TRIGGER eva_doorbell_incidents_frame_release
      BEFORE UPDATE ON eva_doorbell_incidents
      WHEN NEW.frame_released_ts IS NOT OLD.frame_released_ts AND NOT (
        OLD.frame_released_ts IS NULL AND NEW.frame_released_ts IS NOT NULL
        AND OLD.status IN ('SETTLED','EXPIRED') AND NEW.status=OLD.status
      ) BEGIN SELECT RAISE(ABORT,'invalid doorbell frame release'); END;\`);
    console.log(JSON.stringify(attention.perceptionAttentionStatus()));
  `, `
    const attention = await import('./src/mind/perceptionattention.js');
    console.log(JSON.stringify(attention.perceptionAttentionStatus()));
  `]);
  assert.equal(created.enabled, true);
  assert.deepEqual(restarted, { enabled: false, reason: 'schema_mismatch' });
});
