/** Read the pinned native Codex request builder without auth or inference. */
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import childProcess from 'node:child_process';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {syncBuiltinESMExports} from 'node:module';

const [pluginRoot, profile] = process.argv.slice(2);
if (!pluginRoot || !profile || process.env.HOME !== profile ||
    process.env.CODEX_HOME !== path.join(profile, '.codex') ||
    process.env.OPENCLAW_STATE_DIR !== path.join(profile, '.openclaw')) {
  throw new Error('An explicit isolated HOME/CODEX_HOME/state directory is required');
}
for (const file of ['.codex/auth.json', '.openclaw/agents/main/agent/auth-profiles.json']) {
  if (fs.existsSync(path.join(profile, file))) throw new Error('Profile contains authentication');
}
const attempts = {network: 0, childProcess: 0};
net.Socket.prototype.connect = function () {
  attempts.network++;
  throw new Error('Network is disabled for native configuration readback');
};
for (const method of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) {
  childProcess[method] = () => {
    attempts.childProcess++;
    throw new Error('Child processes are disabled for native configuration readback');
  };
}
globalThis.fetch = async () => {
  attempts.network++;
  throw new Error('Fetch is disabled for native configuration readback');
};
syncBuiltinESMExports();
const sha256 = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const packageJson = JSON.parse(fs.readFileSync(path.join(pluginRoot, 'package.json')));
if (packageJson.name !== '@openclaw/codex' || packageJson.version !== '2026.9.6') {
  throw new Error('Unexpected native plugin pin');
}
const builderFile = path.join(pluginRoot, 'dist/.setup/thread-lifecycle-B3mfz1TG.mjs');
const {a: buildTurnStartParams} = await import(pathToFileURL(builderFile).href);
const {createCodexAppServerAgentHarness} = await import(pathToFileURL(path.join(pluginRoot, 'dist/harness.js')).href);
const harness = createCodexAppServerAgentHarness({bindingStore: {read: () => undefined}});
const cases = [];
for (const selection of [
  {role: 'contact', model: 'gpt-6-sol', nativeThinkLevel: 'off', requiredEffort: 'none'},
  {role: 'work', model: 'gpt-6-astra', nativeThinkLevel: 'medium', requiredEffort: 'medium'},
]) {
  const config = {agents: {defaults: {
    model: {primary: 'codex/' + selection.model}, thinkingDefault: selection.nativeThinkLevel,
  }}, plugins: {allow: ['codex'], entries: {codex: {enabled: true, config: {
    sessionCatalog: {enabled: false}, appServer: {homeScope: 'agent'},
  }}}}};
  const agentDir = path.join(profile, '.openclaw/agents/main/agent');
  const params = {provider: 'codex', modelId: selection.model,
    model: {id: selection.model}, thinkLevel: selection.nativeThinkLevel,
    config, agentDir, authProfileStore: {version: 1, profiles: {}},
    prompt: 'Public configuration fixture.', hostCapabilities: {}, trigger: 'user'};
  const options = {threadId: 'fixture', cwd: profile, appServer: {
    sandbox: 'read-only', approvalPolicy: 'never'}, model: selection.model};
  const request = buildTurnStartParams(params, options);
  const capability = harness.supports({provider: 'codex', modelId: selection.model});
  const matches = request.model === selection.model &&
    request.effort === selection.requiredEffort &&
    request.collaborationMode?.settings?.model === selection.model &&
    request.collaborationMode?.settings?.reasoning_effort === selection.requiredEffort;
  cases.push({selection, config, nativeCapability: capability, request, matches,
    authAdmission: 'not observed', providerAcceptance: 'not observed'});
}
await harness.dispose?.();
console.log(JSON.stringify({contract: 'EvaNativeConfigurationReadback/v1',
  package: {name: packageJson.name, version: packageJson.version,
    packageJsonSha256: sha256(path.join(pluginRoot, 'package.json')),
    builderSha256: sha256(builderFile)}, cases, attempts,
  scope: 'Actual installed native turn/start builder only. No app-server, credentials, SDK/provider request, model catalog acceptance, complete harness loop or comparison admission.'}));
