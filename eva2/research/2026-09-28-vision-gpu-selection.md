# Optional vision GPU candidate — 28 September 2026

## Existing result and concrete gap

The camera byte adapter and local interpretation-to-SensoryEvent bridge exist.
Do not replace conversation, memory, calendar or initiative. SmolVLM2's one blank
CPU case took57.704s including loading and repeated its answer. This is not evidence
that local vision, CUDA or other models are unsuitable. The earlier Windows
WebGPU observation remains separate; its physical adapter was not established.

Both Windows and WSL nvidia-smi now report NVIDIA GeForce RTX5080 Laptop GPU,
16303MiB, driver610.88. Device enumeration is not proof of inference acceleration.

## Bounded choice

| Candidate | Evidence and limits | Decision |
|---|---|---|
| Qwen3.5-4B | Official multimodal checkpoint, Apache2.0. Original checkpoint last modified2March2026: older than six months, not presented as a newly released model. Maintained llama.cpp supports Qwen3.5; Unsloth provides an independent GGUF export. Actual household scene quality/latency unknown. | First isolated CUDA candidate; not product selection. Use existing source/event bridge if successful. |
| Liquid LFM2.5-VL-3B | Official model updated24September2026; image grounding/OCR and local runtime paths documented. LFM license includes commercial-use threshold conditions; not equivalent to Apache2.0. Author speed figures use different hardware and are not Eva measurements. | Retain alternative. No weights downloaded or superiority claim. |

Qwen is chosen for the first bounded trial because it provides a permissive-license
multimodal candidate with a supported CUDA runtime within the available hardware
class. Expected fit is an engineering hypothesis; actual GPU allocation, latency
and quality must be measured. Age alone does not establish quality. This is not
an exhaustive state-of-the-art ranking or proof of meeting all owner requirements.

## Exact trial inputs

- Original Qwen revision:851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a.
- Unsloth GGUF revision:e87f176479d0855a907a41277aca2f8ee7a09523,
  Qwen3.5-4B-Q4_K_M.gguf plus mmproj-F16.gguf. Export is third-party, not Qwen-published.
- llama.cpp pinned pre-release b11146 (source version0.5.0), source
  7fe450e19305b828c199d602c23a8337aaa1f03b; official Ubuntu CUDA12.8 x64 artifacts.
- Downloads stay in the existing external artifact directory, with published
  SHA256/LFS hashes verified. No runtime service or production dependency changes.
- One uniform-white image, same negative semantic criterion as previous case,
  at most120 generated tokens and90s process deadline. Also require non-repetitive
  output; capture cold whole-process time and runtime timing separately. This
  controlled synthetic case does not prove real camera quality.
- Next gate: fixed changed/unchanged/occluded real camera scenes, before any
  autonomous observations are admitted. Camera choice remains open.
- Coordinator owns next check before product adoption: runtime/model pins,
  licenses, compatibility and actual GPU/scene evidence. No recurring monitor.

## Primary sources

- https://huggingface.co/Qwen/Qwen3.5-4B
- https://huggingface.co/unsloth/Qwen3.5-4B-GGUF
- https://huggingface.co/LiquidAI/LFM2.5-VL-3B
- https://huggingface.co/LiquidAI/LFM2.5-VL-3B/blob/main/LICENSE
- https://github.com/ggml-org/llama.cpp/blob/master/src/models/qwen35.cpp
- https://github.com/ggml-org/llama.cpp/releases/tag/b11146

No model benchmark results have been added by this selection document.
