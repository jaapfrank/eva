# Optional local vision candidate

Isolated execution probe, not an installed Eva feature. Run `npm ci` and `node smoke.mjs` in an isolated directory. Model download/cache is about 521 MB with the pinned selection. No camera access or cloud inference. The one synthetic image probes counting/colours only; it cannot establish real scene quality. See criteria.json before running; do not repeat unchanged cases.

FastVLM model weights were not selected: its Apple AMLR license excludes product development. GLaDOS code MIT does not override model licensing. SmolVLM2 is a provisional Apache-2.0 candidate, not a proven best model. No new model tokenizer or inference engine is implemented; the probe uses Transformers.js.

Next meaningful evaluation requires fixed recorded scenes, expected observations, unchanged/changed/obscured cases, latency and CPU/GPU memory measurements. Household vision must feed the existing sensory/initiative route, preserve capture time and source, and distinguish image inference from verified fact. No separate memory, scheduler or bin-specific core.
