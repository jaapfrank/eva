# Prompt-cache boundary probe: incompatible with the current Codex route

**Decision: do not install the experimental projection.** The installed SDK can
serialize explicit cache breakpoints, but Eva's current projector rejects them
and the actual permitted Codex endpoint rejects `prompt_cache_breakpoint` with
HTTP 400, `invalid_request_error`, code `invalid_parameter`. The experiment
stopped after three HTTP requests; no fourth request or retry was made.

This was a bounded evidence task, not a production patch. Base:
`e97d75384f74bd82eca35300175fa1994cbf1a2e`. Current model remained
`gpt-5.6-luna`, reasoning `low`, on the existing subscription lease. No new
provider, subscription, dependency, credential, budget, configuration, tool
execution or production service change. The earlier preparation/reasoning-none
hypotheses were not repeated.

## Offline findings

The [official prompt-caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
describes explicit `prompt_cache_breakpoint` markers on input content blocks for
GPT-5.6 and later. A top-level `instructions` string cannot contain that marker.
The public Responses API contract is not evidence that the separate
`backend-api/codex/responses` route supports the same field.

Installed versions: OpenAI SDK 3.6.0, Pydantic AI 2.37.0, httpx2 2.12.0. The SDK
supports both `prompt_cache_options` and the input-text marker; Pydantic exposes
cache options. Eva's `_codex_compatible_request` accepts a narrower shape,
converts system content to one top-level instruction string, and rejects
structured developer input. `_instructions` places changing style, memory,
self-context and recent history after identity/guidance. `_cache_key` is already
stable within person/project scope; this experiment retained that grouping.

The probe constructed the installed identity and the complete configured
external/delegated descriptor catalog, with synthetic empty memory and a single
controlled history item changing from A to B. It captured the actual SDK wire
packet behind a non-network transport in a network-disabled disposable
container. It did not capture or log an owner's private conversation and is not
the full website greeting request. No application tool implementation was called.

| Exact constructed request component | Local serialized token estimate |
| --- | ---: |
| Complete baseline packet | 5,439 offline; 5,440 in the live run's fresh cache-key instance |
| Tool declarations (33 entries including hosted search) | 4,316 |
| Instructions | 997 |
| Stable instruction prefix for this same phase/catalog | 926 |
| Changing instruction suffix | 71 |
| Input message representation | 15 |

These local component estimates include serialization and are not additive
provider token accounting. The live provider reported 3,753 input tokens per
baseline, including 2,744 for tools and 1,001 for instructions. This explicitly
shows why local JSON/tokenizer counts cannot be substituted for provider usage.
The selected prefix is only stable for an unchanged identity, phase and tool
availability; it is not asserted stable across all conversation states.

The candidate retained the first instruction paragraph as the required top-level
instruction string, moved the rest into one developer input with a stable
content block and changing content block, and marked the stable block. Concatenated
instruction characters, all tools, ordering of original user/history input,
model, reasoning and cache-key value were asserted unchanged. No content was
removed, summarized or replaced with a greeting/acknowledgement script. The
experimental projector accepted only the exact four prevalidated packet hashes
inside the disposable probe process; production source was never widened.

## Actual one-shot measurements

| Request | HTTP | First text | Total probe duration | Input / output | Raw cached tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline first | 200 | 1,328 ms | 1,730 ms | 3,753 / 12 | 0 |
| Baseline second, changed history suffix | 200 | 1,168 ms | 1,794 ms | 3,753 / 20 | 2,816 |
| Candidate first, explicit marker | 400 | none | 60,077 ms | unknown | unknown |
| Candidate second | not run | — | — | — | — |

Baseline `cache_write_tokens` was 0 on both responses. The second baseline's raw
attribution assigns 2,744 cached tokens to tools and 72 to instructions. Thus the
current route already reused a prefix without this change. The first/second
labels do not guarantee an independently controlled cold cache or a general
warm-cache latency distribution. The rejected candidate's duration includes
failed-request/transport handling and is not a model latency comparison.

Total measured usage: **7,506 input + 32 output tokens**, plus **one rejected
request without reported usage**. Three HTTP model requests, zero function calls
returned, zero application tool actions executed. Actual subscription/credit
cost is unknown; public API pricing is not substituted for it. Raw terminal
usage, including `cached_tokens`, `cache_write_tokens` and attribution, is kept
in the JSONL evidence without prompt, credential or generated-response text.

## Recommendation and limits

Keep the current production projector and provider configuration. A future
breakpoint implementation requires explicit support on the admitted Codex route,
then scoped regression and paired real-route latency measurements. Do not add an
API-key route, dependency upgrade, separate subscription or another model to make
this field work. The known incompatibility makes more calls in this experiment
unnecessary.

This sample cannot explain or close the owner's roughly five-second website
response: it has a controlled catalog/context and omits the website, memory and
other host-path work. No general speedup, quality win, physical audio improvement
or owner acceptance is claimed. The three recorded responses are the entire live
experiment; there is no hidden candidate warm pair.

## Evidence and reproduction

- `prompt-cache-probe-20260922.py`: exact executed probe source. Default is
  offline capture; `--live-four-calls` is an explicitly separate mode, capped at
  four one-shot network requests and stopping on the first failed request.
- `prompt-cache-offline-20260922.json`: offline wire/component observations.
- `prompt-cache-live-20260922.jsonl`: live run's offline preflight, three request
  records and usage summary. The preflight record's zero calls applies only to
  that capture stage; the final summary counts the three real requests.
- `prompt-cache-provenance-20260922.json`: image/source/probe hashes and scope.

The exact host runner is archived as `prompt-cache-runner-20260922.py`.
Executed through `/tmp/eva2-run-cache-probe.py`: a disposable read-only container
using the exact installed core image and its read-only mounts. Environment
configuration was transferred through a temporary mode-0600 file, removed when
the runner exited. Offline used network `none`; the explicitly authorized live
mode used Docker bridge for the existing model endpoint. Capture substituted
fixture credentials and never contacted the provider. Live credentials were
loaded only by the existing provider lifecycle; no credential bytes were logged.
Do not rerun live mode merely to review this evidence.
