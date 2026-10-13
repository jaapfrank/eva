import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');

function startupCalls(t, {
  runtimeMode,
  recoveryMode = runtimeMode !== 'normal',
  externalEffectsEnabled = runtimeMode === 'normal',
} = {}) {
  const root = fs.mkdtempSync(`/tmp/bottie-${runtimeMode}-startup-`);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const workspaceDir = path.join(root, 'workspace');
  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', `
    import { registerHooks } from 'node:module';

    globalThis.__startupCalls = [];
    const called = (name) => globalThis.__startupCalls.push(name);
    globalThis.setTimeout = (callback, delay) => {
      globalThis.__startupCalls.push('timer:timeout:' + delay);
      if (delay === 15 * 60_000) callback();
      return { delay, unref() {} };
    };
    globalThis.setInterval = (_callback, delay) => {
      globalThis.__startupCalls.push('timer:interval:' + delay);
      return { delay, unref() {} };
    };
    globalThis.clearTimeout = () => {};
    globalThis.clearInterval = () => {};

    const sources = new Map([
      ['mock:config', ${JSON.stringify(`export const config = Object.freeze({
        root: ${JSON.stringify(projectRoot)},
        logicalDataDir: ${JSON.stringify(root)},
        workspaceDir: ${JSON.stringify(workspaceDir)},
        webPort: 0,
        openaiApiKey: 'test-only',
        householdMembers: [],
        trustHaPersonMembership: false,
        runtimeMode: ${JSON.stringify(runtimeMode)},
        recoveryMode: ${JSON.stringify(recoveryMode)},
        externalEffectsEnabled: ${JSON.stringify(externalEffectsEnabled)},
        autonomyEnabled: true,
      });`) }],
      ['mock:db', ${JSON.stringify(`
        export const dailyBackupDue = () => true;
        export const logTimeline = () => globalThis.__startupCalls.push('timeline');
        export const maintain = () => globalThis.__startupCalls.push('maintain');
      `)}],
      ['mock:child-process', ${JSON.stringify(`
        export const spawn = () => {
          globalThis.__startupCalls.push('backup-spawn');
          return { once() {} };
        };
      `)}],
      ['mock:oauth', ${JSON.stringify(`export const authStatus = () => ({ status: 'ok' });`)}],
      ['mock:ha', ${JSON.stringify(`
        export const cachedStates = () => { globalThis.__startupCalls.push('ha-cache'); return []; };
        export const connectHA = async () => { globalThis.__startupCalls.push('ha'); };
      `)}],
      ['mock:telegram', ${JSON.stringify(`export const startTelegram = async () => { globalThis.__startupCalls.push('telegram'); };`)}],
      ['mock:web', ${JSON.stringify(`export const startWeb = async () => { globalThis.__startupCalls.push('web'); };`)}],
      ['mock:voice', ${JSON.stringify(`export const startVoiceBridge = () => { globalThis.__startupCalls.push('voice'); };`)}],
      ['mock:reminders', ${JSON.stringify(`export const startReminderLoop = () => { globalThis.__startupCalls.push('reminders'); };`)}],
      ['mock:autonomy', ${JSON.stringify(`export const startAutonomy = () => { globalThis.__startupCalls.push('autonomy'); };`)}],
      ['mock:perception', ${JSON.stringify(`export const startPerception = () => { globalThis.__startupCalls.push('perception'); };`)}],
      ['mock:emotions', ${JSON.stringify(`export const startEmotionalLife = () => { globalThis.__startupCalls.push('emotions'); };`)}],
      ['mock:cognitive', ${JSON.stringify(`export const startCognitiveCore = () => { globalThis.__startupCalls.push('cognitive'); };`)}],
      ['mock:household', ${JSON.stringify(`
        export const setPersonHouseholdRole = () => { globalThis.__startupCalls.push('household-role'); };
        export const syncHouseholdRolesFromPresence = () => { globalThis.__startupCalls.push('household-sync'); };
      `)}],
      ['mock:runtimeversion', ${JSON.stringify(`export const runtimeVersion = () => ({ loaded_code_sha256: 'a'.repeat(64) });`)}],
      ['mock:vault', ${JSON.stringify(`export const registerVaultSecretsForRedaction = () => { globalThis.__startupCalls.push('vault'); };`)}],
    ]);
    const specifiers = new Map([
      ['node:child_process', 'mock:child-process'],
      ['./config.js', 'mock:config'],
      ['./db.js', 'mock:db'],
      ['./brain/oauth.js', 'mock:oauth'],
      ['./integrations/homeassistant.js', 'mock:ha'],
      ['./integrations/telegram.js', 'mock:telegram'],
      ['./web/server.js', 'mock:web'],
      ['./mind/voicebridge.js', 'mock:voice'],
      ['./reminders.js', 'mock:reminders'],
      ['./mind/autonomy.js', 'mock:autonomy'],
      ['./mind/perception.js', 'mock:perception'],
      ['./mind/emotions.js', 'mock:emotions'],
      ['./mind/cognitive.js', 'mock:cognitive'],
      ['./mind/household.js', 'mock:household'],
      ['./runtimeversion.js', 'mock:runtimeversion'],
      ['./self/vault.js', 'mock:vault'],
    ]);
    registerHooks({
      resolve(specifier, context, nextResolve) {
        const fromIndex = context.parentURL
          && new URL(context.parentURL).pathname.endsWith('/src/index.js');
        if (fromIndex && specifiers.has(specifier)) {
          return { url: specifiers.get(specifier), shortCircuit: true };
        }
        return nextResolve(specifier, context);
      },
      load(url, context, nextLoad) {
        if (sources.has(url)) {
          return { format: 'module', source: sources.get(url), shortCircuit: true };
        }
        return nextLoad(url, context);
      },
    });
    await import('./src/index.js');
    process.stdout.write('\\nSTARTUP_CALLS=' + JSON.stringify(globalThis.__startupCalls));
  `], {
    cwd: projectRoot,
    env: {
      ...process.env,
      DOTENV_CONFIG_PATH: '/dev/null',
      EVA_DATA_DIR: root,
      EVA_RUNTIME_MODE: runtimeMode,
    },
    encoding: 'utf8',
    timeout: 10_000,
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const marker = child.stdout.lastIndexOf('\nSTARTUP_CALLS=');
  assert.notEqual(marker, -1, child.stdout);
  return JSON.parse(child.stdout.slice(marker + '\nSTARTUP_CALLS='.length));
}

test('recovery composition keeps web alive and starts no external background lane', (t) => {
  const calls = startupCalls(t, { runtimeMode: 'recovery' });
  assert.equal(calls.filter((name) => name === 'web').length, 1);
  assert.equal(calls.filter((name) => name === 'cognitive').length, 1);
  t.diagnostic(`emotional_life_started=${calls.filter((name) => name === 'emotions').length}`);
  assert.deepEqual(calls.filter((name) => [
    'ha', 'ha-cache', 'telegram', 'voice', 'perception', 'reminders', 'autonomy',
    'household-role', 'household-sync', 'maintain', 'backup-spawn',
    'timer:timeout:900000', 'timer:interval:21600000',
  ].includes(name)), []);
});

test('byte-exact normal preserves the existing startup order and starts each lane once', (t) => {
  const calls = startupCalls(t, { runtimeMode: 'normal' });
  const orderedLanes = [
    'cognitive', 'perception', 'web', 'ha', 'voice', 'telegram', 'reminders',
    'emotions', 'autonomy', 'maintain', 'backup-spawn',
  ];
  assert.deepEqual(calls.filter((name) => orderedLanes.includes(name)), orderedLanes);
  for (const lane of orderedLanes) {
    assert.equal(calls.filter((name) => name === lane).length, 1, lane);
  }
  assert.equal(calls.filter((name) => name === 'timer:timeout:900000').length, 1);
  assert.equal(calls.filter((name) => name === 'timer:interval:21600000').length, 2);
});
