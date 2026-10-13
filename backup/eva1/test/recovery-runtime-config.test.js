import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');

function readRuntimeConfig(t, runtimeMode, autonomyEnabled = 'true') {
  const dataDir = fs.mkdtempSync('/tmp/bottie-recovery-config-');
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const env = {
    ...process.env,
    NODE_TEST_CONTEXT: 'recovery-runtime-config-child',
    DOTENV_CONFIG_PATH: '/dev/null',
    EVA_DATA_DIR: dataDir,
    EVA_AUTONOMY_ENABLED: autonomyEnabled,
    EVA_AUTONOMY_LEVEL: 'full',
  };
  if (runtimeMode === undefined) delete env.EVA_RUNTIME_MODE;
  else env.EVA_RUNTIME_MODE = runtimeMode;
  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', `
    const { config } = await import('./src/config.js');
    process.stdout.write(JSON.stringify({
      runtimeMode: config.runtimeMode,
      recoveryMode: config.recoveryMode,
      externalEffectsEnabled: config.externalEffectsEnabled,
      autonomyEnabled: config.autonomyEnabled,
      autonomyLevel: config.autonomyLevel,
    }));
  `], {
    cwd: projectRoot,
    env,
    encoding: 'utf8',
    timeout: 10_000,
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  return JSON.parse(child.stdout);
}

test('runtime mode is fail-closed and only byte-exact normal opens external effects', (t) => {
  for (const [label, value] of [
    ['missing', undefined],
    ['empty', ''],
    ['explicit recovery', 'recovery'],
    ['unknown', 'maintenance'],
    ['wrong case', 'NORMAL'],
    ['surrounding whitespace', ' normal '],
  ]) {
    assert.deepEqual(readRuntimeConfig(t, value), {
      runtimeMode: 'recovery',
      recoveryMode: true,
      externalEffectsEnabled: false,
      autonomyEnabled: false,
      autonomyLevel: 'disabled',
    }, label);
  }

  assert.deepEqual(readRuntimeConfig(t, 'normal'), {
    runtimeMode: 'normal',
    recoveryMode: false,
    externalEffectsEnabled: true,
    autonomyEnabled: true,
    autonomyLevel: 'full',
  });
});

test('normal runtime does not itself opt in to autonomy', (t) => {
  assert.deepEqual(readRuntimeConfig(t, 'normal', 'false'), {
    runtimeMode: 'normal',
    recoveryMode: false,
    externalEffectsEnabled: true,
    autonomyEnabled: false,
    autonomyLevel: 'disabled',
  });
});
