import { registerHooks } from 'node:module';

const voiceAuthorityModuleUrl = new URL('../src/voiceauthority.js', import.meta.url).href;
const testComposition = String.raw`
export const __voiceAuthorityStoreTest = Object.freeze({
  issueAuthenticatedSource(input, eventType) {
    const evidence = rawEvidence(input, eventType);
    const source = Object.freeze({
      eventType,
      data: Object.freeze({ ...(evidence.data || {}) }),
      sourceRef: String(input.sourceRef || ''),
      sourceSha256: String(input.sourceSha256 || ''),
      sourceBodyJson: String(input.sourceBodyJson || ''),
      adapterInstanceSha256: String(evidence.body.adapterInstanceSha256 || ''),
      socketGeneration: Number(evidence.body.socketGeneration),
      isCurrent: typeof input.isCurrent === 'function' ? input.isCurrent : () => true,
    });
    if (!markAuthenticatedVoiceStoreSource(source, eventType)) {
      throw new Error('test voice source kon niet authentiek worden gemarkeerd');
    }
    return source;
  },
});
`;

registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== voiceAuthorityModuleUrl || loaded.format !== 'module') return loaded;
    const source = Buffer.isBuffer(loaded.source)
      ? loaded.source.toString('utf8') : String(loaded.source);
    return { ...loaded, source: `${source}\n${testComposition}` };
  },
});
