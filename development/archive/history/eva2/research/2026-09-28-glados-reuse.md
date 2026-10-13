# GLaDOS reuse assessment — 28 September 2026

Bounded source comparison requested by Jaap. No installation, runtime benchmark, camera access or paid Eva model calls. Candidate revision: `fde319cfb8ceb965e512800b2ffc5b9c0d1574b1`; latest commit at inspection: 17 September 2026. Repository code declares MIT; model weights/assets need separate license assessment before adoption. Activity does not establish security or compatibility. No release/security audit performed.

## Model license correction after direct license inspection

FastVLM weights at onnx-community revision ca35eb9373f8a8761df0855fca19dea330f2407a use Apple AMLR, which expressly excludes product development. Do not adopt these weights for Eva. The scene-change architecture remains useful; the bounded local execution candidate uses HuggingFaceTB/SmolVLM2-500M-Video-Instruct revision 7b375e1b73b11138ff12fe22c8f2822d8fe03467 (model card: Apache-2.0). This corrects the earlier provisional FastVLM recommendation; no FastVLM weights were installed.

Source: https://huggingface.co/onnx-community/FastVLM-0.5B-ONNX/blob/ca35eb9373f8a8761df0855fca19dea330f2407a/LICENSE

## Decision

Do not replace Eva's core. Prioritize a bounded comparison of local scene-change vision and optional behavioral feedback inside the existing initiative/personality modules. Voice scheduling is a useful reference, but neither verified Dutch latency nor Voice PE firmware compatibility is established. These are follow-up candidates in existing slices, not a new active build mandate.

| Candidate | Actual source mechanism | Existing Eva basis | Decision and proof needed |
|---|---|---|---|
| Local vision | `vision/vision_processor.py`: frame differences gate FastVLM calls; cached features and vision events. | `application/free_will.py` and `adapters/postgres/sensory_events.py` already process observations, relevance, budgets and initiative. No FastVLM/scene_change implementation found in bounded source search; this does not prove all camera access absent. | Strongest new candidate: optional observation adapter into existing sensory events. First compare same recorded changed/unchanged scenes, bin colour/count accuracy, resource use and false alerts. Calendar correlation remains Eva's job. No new initiative engine. |
| Behavioral observer | `autonomy/agents/observer_agent.py` evaluates recent assistant text and applies bounded prompt modifiers through `constitution.py`. | Shared persona, memory, personal interests and correction already exist; M3 remains open. | Candidate for identifying repetitive disclaimers or excessive verbosity. Current observer samples assistant outputs only, not full user feedback; not evidence of improved task success or code self-improvement. Compare against existing correction route before adopting. |
| Compact context slots | `autonomy/slots.py` stores summaries, importance, confidence, timestamps and separate reports; `core/context.py` sorts registered sources. | Eva already supplies authorized memory, task state and personal interests in `pydantic_conversation.py`/`conversation_actions.py`. | Reuse pattern only if same-task context measurements show avoidable input. Priority ordering alone is not a hard token budget. Do not create a second authoritative task store; GLaDOS slots here are in-memory. |
| Responsive speech | Separate priority/autonomy queues, VAD-triggered playback stop, streamed sentence synthesis. `core/speech_player.py` estimates heard words from percentage of audio played. | Eva has shared contact/background execution, LiveKit, VAD and native-duplex candidate; `application/audio_playback.py` explicitly distinguishes frames from exact spoken words. | Reference for comparison, no proven replacement benefit. Preserve Eva's truthful playback accounting. README recommends echo-cancelling microphone/headphones if self-triggering; this is not a Voice PE firmware fix. |
| Local STT/TTS | `ASR/tdt_asr.py` uses Parakeet TDT v3 ONNX; TTS includes Kokoro/GLaDOS. | Existing HA Cloud audio and isolated Wyoming/Piper/Whisper candidate. | Optional model/provider comparison, not whole-platform adoption. Dutch quality, target hardware latency, echo handling and individual model licenses remain unmeasured. |
| Memory and autonomy core | `core/memory_context.py` loads shared facts.jsonl, filters importance and injects up to 10 facts. Timer/vision autonomy plus cooldown/coalescing. | Hindsight with person binding, accepted correction/forgetting and durable worker/event records. | Keep existing basis. Inspected memory injection does not establish family-person isolation; no equal-task advantage over Eva demonstrated. |

## Claims that must remain qualified

- README says under 600 ms but also describes a 640 ms end-of-speech pause; the timing boundary/hardware/language must be established before comparing with Eva's end-to-end user wait.
- Separate inference queues prevent a software queue dependency; they do not guarantee low latency when CPU/GPU/provider capacity is shared.
- PAD/HEXACO are modeled mood/personality parameters, not evidence of judgment, lived emotions or general self-improvement.
- Vision's advertised speedup is an upstream claim, not a measurement on Jaap's system.
- Source inspection is not a product superiority result. No dependencies or firmware changed.

## Follow-up within existing plan

- I-step: reuse change-triggered observation design, not FastVLM weights (product-development license exclusion). The isolated SmolVLM2 candidate remains unselected pending a recorded-scene comparison; see local-vision evidence.
- M3: consider bounded observer feedback; retain Eva identity, user corrections and existing authority. Measure whether it removes the actual repetitive/impersonal behavior without new invented claims.
- C3: retain current firmware until the existing candidate failure is observed. GLaDOS is not established as a ready-to-install replacement.
- Coordinator owns candidate reassessment at the relevant slice, before any adoption. Pin exact revision/model versions, verify licenses/dependencies, establish upgrade/rollback and schedule maintenance then. No recurring monitor installed.

## Sources inspected

https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/README.md
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/LICENSE.txt
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/pyproject.toml
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/configs/glados_config.yaml
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/docs/autonomy.md
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/docs/vision.md
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/core/speech_listener.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/core/speech_player.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/core/llm_processor.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/core/context.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/core/memory_context.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/autonomy/agents/observer_agent.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/autonomy/constitution.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/autonomy/emotion_state.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/autonomy/slots.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/autonomy/loop.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/vision/vision_processor.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/ASR/tdt_asr.py
https://github.com/dnhkng/GLaDOS/blob/fde319cfb8ceb965e512800b2ffc5b9c0d1574b1/src/glados/TTS/tts_kokoro.py

Read scope: README via GitHub; selected modules/documents fetched and inspected, several through targeted excerpts. No claim of a complete repository audit or running upstream tests.
