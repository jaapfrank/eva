import fs from 'node:fs';
import os from 'node:os';
import { spawnSync } from 'node:child_process';

try { os.setPriority(10); } catch { /* inferentie blijft ook zonder nice-ondersteuning bruikbaar */ }
try {
  spawnSync('ionice', ['-c', '3', '-p', String(process.pid)], {
    stdio: 'ignore',
    timeout: 2000,
  });
} catch { /* op systemen zonder ionice blijft CPU-nice actief */ }

const root = process.env.EVA_LOCAL_INFERENCE_ROOT || process.cwd();
const predictorDir = `${root}/data/predictor`;
const policyDir = `${root}/data/policybrain`;
const encoderDir = `${root}/data/encoder`;

let tfPromise = null;
let predictor = null;
let policy = null;
let embeddingExtractor = null;
let embeddingPromise = null;
let queue = Promise.resolve();

const EMBEDDING_MODEL = 'Xenova/paraphrase-multilingual-MiniLM-L12-v2';

function tfLib() {
  tfPromise ||= import('@tensorflow/tfjs-node').then((module) => module.default);
  return tfPromise;
}

async function loadEmbeddings() {
  if (embeddingExtractor) return embeddingExtractor;
  embeddingPromise ||= (async () => {
    const { pipeline, env } = await import('@xenova/transformers');
    env.allowLocalModels = false;
    if (process.env.EVA_MODEL_CACHE) env.cacheDir = process.env.EVA_MODEL_CACHE;
    embeddingExtractor = await pipeline('feature-extraction', EMBEDDING_MODEL, { quantized: true });
    return embeddingExtractor;
  })();
  return embeddingPromise;
}

async function loadPredictor(modelSha) {
  if (!modelSha || !fs.existsSync(`${predictorDir}/model.json`)) return null;
  if (predictor?.sha === modelSha) return predictor;
  const tf = await tfLib();
  predictor?.model?.dispose?.();
  predictor = {
    tf,
    model: await tf.loadLayersModel(`file://${predictorDir}/model.json`),
    sha: modelSha,
  };
  return predictor;
}

async function loadPolicy(modelSha, encoderKind) {
  if (!modelSha || !fs.existsSync(`${policyDir}/model.json`)) return null;
  if (policy?.sha === modelSha && policy?.encoderKind === encoderKind) return policy;
  const tf = await tfLib();
  policy?.model?.dispose?.();
  policy?.encoder?.dispose?.();
  policy = {
    tf,
    model: await tf.loadLayersModel(`file://${policyDir}/model.json`),
    encoder: encoderKind === 'eva_encoder_v1' && fs.existsSync(`${encoderDir}/model.json`)
      ? await tf.loadLayersModel(`file://${encoderDir}/model.json`)
      : null,
    encoderKind,
    sha: modelSha,
  };
  return policy;
}

async function run(message) {
  const payload = message.payload || {};
  if (message.type === 'warmup') {
    if (payload.target === 'embeddings') return { loaded: !!(await loadEmbeddings()) };
    if (payload.target === 'predictor') return { loaded: !!(await loadPredictor(payload.modelSha)) };
    if (payload.target === 'policy') return {
      loaded: !!(await loadPolicy(payload.modelSha, payload.encoder)),
    };
    return { loaded: false };
  }
  if (message.type === 'embedding') {
    const extractor = await loadEmbeddings();
    if (!extractor || !payload.text) return null;
    const output = await extractor(payload.text, { pooling: 'mean', normalize: true });
    return Array.from(output.data || []);
  }
  if (message.type === 'predictor') {
    const loaded = await loadPredictor(payload.modelSha);
    if (!loaded || !payload.input?.length) return null;
    const input = loaded.tf.tensor2d([payload.input]);
    let output = null;
    try {
      output = loaded.model.predict(input);
      return Array.from(await output.data());
    } finally {
      input.dispose();
      output?.dispose?.();
    }
  }
  if (message.type === 'policy') {
    const loaded = await loadPolicy(payload.modelSha, payload.encoder);
    if (!loaded || !payload.input?.length) return null;
    const raw = loaded.tf.tensor2d([payload.input]);
    const latent = loaded.encoder ? loaded.encoder.predict(raw) : raw;
    if (loaded.encoder) raw.dispose();
    const normalized = loaded.tf.div(
      latent,
      loaded.tf.add(loaded.tf.norm(latent, 2, 1, true), 1e-6),
    );
    latent.dispose();
    const categorical = loaded.tf.tensor2d([payload.sourceFeatures || []]);
    const input = loaded.tf.concat([normalized, categorical], 1);
    normalized.dispose();
    categorical.dispose();
    const output = loaded.model.predict(input);
    try {
      return Number((await output.data())[0]);
    } finally {
      output.dispose();
      input.dispose();
    }
  }
  throw new Error(`onbekend inferentieverzoek: ${message.type}`);
}

process.on('message', (message) => {
  queue = queue.then(async () => {
    try {
      const result = await run(message);
      if (process.connected) process.send({ id: message.id, ok: true, result });
    } catch (error) {
      if (process.connected) process.send({
        id: message.id,
        ok: false,
        error: error?.message || String(error),
      });
    }
  });
});

process.on('disconnect', () => process.exit(0));
