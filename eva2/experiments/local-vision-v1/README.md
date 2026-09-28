# Optional local vision candidate

Isolated execution probe, not an installed Eva feature. Run `npm ci` and `node smoke.mjs` in an isolated directory. Model download/cache is about 521 MB with the pinned selection. No camera access or cloud inference. The one synthetic image probes counting/colours only; it cannot establish real scene quality. See criteria.json before running; do not repeat unchanged cases.

FastVLM model weights were not selected: its Apple AMLR license excludes product development. GLaDOS code MIT does not override model licensing. SmolVLM2 is a provisional Apache-2.0 candidate, not a proven best model. No new model tokenizer or inference engine is implemented; the probe uses Transformers.js.

Next meaningful evaluation requires fixed recorded scenes, expected observations, unchanged/changed/obscured cases, latency and CPU/GPU memory measurements. Household vision must feed the existing sensory/initiative route, preserve capture time and source, and distinguish image inference from verified fact. No separate memory, scheduler or bin-specific core.

## Recorded-image bridge

`analyze.mjs /absolute/path/to/image.png` reads a bounded local image and emits a
JSON description bound to its SHA256. It loads only the pinned revision already
present under `EVA_VISION_CACHE`; remote model loading is disabled. This is still
an optional candidate, not an installed camera watcher. RawImage performs decoding;
the camera transport's signature check alone is not image validation.

`LocalVisionAnalyzer` can invoke that direct Node command and return the existing
`SensoryEvent` type with model/revision, image hash, retrieval time and an explicit
model-interpretation label. It does not persist or publish the event. Capture time
remains unknown. The configured process timeout covers communication/inference,
not OS process creation or arbitrary subprocess descendants. The trusted backend
output is captured before size validation; this is not an arbitrary-code sandbox.

## Backend follow-up

`EVA_VISION_DEVICE` explicitly selects cpu/webgpu/cuda; `EVA_VISION_CACHE` may reuse an existing model cache. Backend name alone does not establish physical GPU execution. WSL enumerated llvmpipe only, and that trial was stopped. One native Windows WebGPU run returned the same correct27-token answer in13.017s generation plus6.908s loading. WSL CPU baseline25.082s generation is a single-run cross-platform comparison, not a causal or distributional benchmark. Native Windows adapter identity and actual camera quality remain unproved. See evidence/product/local-vision-backends-20260928.json.
