import { registerHooks } from 'node:module';

import { configureIsolatedWebTestDependencies } from './web-server-test-state.js';

const agentUrl = new URL('../src/mind/agent.js', import.meta.url).href;
const serverUrl = new URL('../src/web/server.js?isolated-web-test-composition', import.meta.url).href;
const testAgentUrl = new URL('./web-server-test-agent.js', import.meta.url).href;

registerHooks({
  resolve(specifier, context, nextResolve) {
    const resolved = nextResolve(specifier, context);
    if (context.parentURL === serverUrl && resolved.url === agentUrl) {
      return { url: testAgentUrl, shortCircuit: true };
    }
    return resolved;
  },
});

export async function startIsolatedWebServer(dependencies) {
  configureIsolatedWebTestDependencies(dependencies);
  const { startWeb } = await import(serverUrl);
  return startWeb();
}
