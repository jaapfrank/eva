import { registerHooks } from 'node:module';

const llmTestModuleUrl = new URL(
  '../src/brain/llm.js?llm-test-composition', import.meta.url,
).href;
const testComposition = String.raw`
export const __llmTest = Object.freeze({
  requestOnce,
  requestOnceWith: _requestOnceWith,
});
`;

// Alleen een test die deze loader expliciet importeert en daarna exact de
// query-gebonden module laadt krijgt toegang tot de lexicale transportseam.
// De gewone production module-URL behoudt haar oorspronkelijke namespace.
registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== llmTestModuleUrl || loaded.format !== 'module') return loaded;
    const source = Buffer.isBuffer(loaded.source)
      ? loaded.source.toString('utf8') : String(loaded.source);
    return { ...loaded, source: `${source}\n${testComposition}` };
  },
});
