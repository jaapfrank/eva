import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-recovery-tool-policy-');
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_RUNTIME_MODE = 'recovery';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.NODE_ENV = 'test';

const originalFetch = globalThis.fetch;
let fetchCalls = 0;
globalThis.fetch = async () => {
  fetchCalls += 1;
  throw new Error('recovery toolpolicy mag geen netwerkgrens bereiken');
};

const { config } = await import('../src/config.js');
const { db } = await import('../src/db.js');
const {
  TOOL_EFFECT,
  executeTool,
  toolAllowedByPolicy,
  toolEffect,
} = await import('../src/mind/tools.js');

after(() => {
  globalThis.fetch = originalFetch;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const effectCases = Object.freeze([
  Object.freeze({ name: 'huis_status', args: {}, effect: TOOL_EFFECT.read, allowed: true }),
  Object.freeze({ name: 'bedien_huis', args: {}, effect: TOOL_EFFECT.home, allowed: false }),
  Object.freeze({ name: 'spreek_in_huis', args: {}, effect: TOOL_EFFECT.voice, allowed: false }),
  Object.freeze({ name: 'stuur_bericht', args: {}, effect: TOOL_EFFECT.external, allowed: false }),
  Object.freeze({ name: 'schrijf_bestand', args: {}, effect: TOOL_EFFECT.workspace, allowed: false }),
  Object.freeze({ name: 'onthoud', args: {}, effect: TOOL_EFFECT.persistent, allowed: false }),
  Object.freeze({
    name: 'beheer_runtime', args: { actie: 'herstart' },
    effect: TOOL_EFFECT.runtime, allowed: false,
  }),
]);

test('recovery is a global read-only ceiling before any caller effect policy', () => {
  assert.equal(config.runtimeMode, 'recovery');
  assert.equal(config.externalEffectsEnabled, false);

  for (const { name, args, effect, allowed } of effectCases) {
    assert.equal(toolEffect(name, args), effect, `${name}:effect`);
    for (const policy of [
      null,
      'allow',
      'all',
      'unrestricted',
      { mode: 'allow' },
      { allowedEffects: [effect] },
    ]) {
      assert.equal(toolAllowedByPolicy(name, args, policy), allowed,
        `${name}:${JSON.stringify(policy)}`);
    }
  }

  assert.equal(toolEffect('bestaat_niet', {}), 'unknown');
  assert.equal(toolAllowedByPolicy('bestaat_niet', {}, null), false);
  assert.equal(toolAllowedByPolicy('huis_status', {}, 'deny'), false,
    'de recovery ceiling mag een strengere callerpolicy niet verruimen');
});

test('executeTool blocks a recovery write before its handler or network boundary', async () => {
  const target = path.join(dataDir, 'workspace', 'recovery-write-must-not-exist.txt');
  const raw = await executeTool('schrijf_bestand', {
    pad: 'data/workspace/recovery-write-must-not-exist.txt',
    inhoud: 'dit mag niet worden geschreven',
  }, { mode: 'chat', effectPolicy: null });
  const result = JSON.parse(raw);

  assert.equal(result.contract, 'ActionResult/v1');
  assert.equal(result.ok, false);
  assert.equal(result.failure_class, 'policy');
  assert.equal(result.effect_uncertain, false);
  assert.equal(fs.existsSync(target), false);
  assert.equal(fetchCalls, 0);
});
