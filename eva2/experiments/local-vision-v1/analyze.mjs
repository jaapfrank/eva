// Optional recorded-image backend. Model is a candidate, not a quality guarantee.
import { AutoProcessor, AutoModelForVision2Seq, RawImage, env } from '@huggingface/transformers';
import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';

const filename = process.argv[2];
if (!filename || process.argv.length !== 3) throw new Error('Expected one local image file');
const size = fs.statSync(filename).size;
if (size < 1 || size > 2097152) throw new Error('Image size outside transport limit');
const bytes = fs.readFileSync(filename);
const imageHash = crypto.createHash('sha256').update(bytes).digest('hex');
const modelId = 'HuggingFaceTB/SmolVLM2-500M-Video-Instruct';
const revision = '7b375e1b73b11138ff12fe22c8f2822d8fe03467';
const device = process.env.EVA_VISION_DEVICE ?? 'cpu';
if (!['cpu', 'webgpu', 'cuda'].includes(device)) throw new Error('Unsupported explicit device');
env.cacheDir = process.env.EVA_VISION_CACHE ?? './cache';
// Inference must use the previously prepared cache, not silently download.
env.allowRemoteModels = false;
const modelPath = path.resolve(env.cacheDir, modelId, revision);
const started = performance.now();
const processor = await AutoProcessor.from_pretrained(modelPath, { local_files_only: true });
const model = await AutoModelForVision2Seq.from_pretrained(modelPath, {
  revision, device,
  dtype: { embed_tokens: 'fp32', vision_encoder: 'q8', decoder_model_merged: 'q4' },
  session_options: { intraOpNumThreads: 4 },
});
try {
  const image = await RawImage.read(filename);
  const prompt = processor.apply_chat_template([{ role: 'user', content: [
    { type: 'image' },
    { type: 'text', text: 'Describe only what is visible in this image. Mention uncertainty. Do not infer identities, intentions, dates or events outside the image.' },
  ] }], { add_generation_prompt: true });
  const inputs = await processor(prompt, image);
  const output = await model.generate({ ...inputs, max_new_tokens: 120, do_sample: false });
  const description = processor.batch_decode(output.slice(null, [inputs.input_ids.dims.at(-1), null]), { skip_special_tokens: true })[0];
  process.stdout.write(JSON.stringify({ image_sha256: imageHash, model: modelId, revision, description, elapsed_ms: performance.now() - started }));
} finally {
  await model.dispose();
}
