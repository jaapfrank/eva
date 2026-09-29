# P1 native admission — 29 September 2026

This bounded source assessment completes the current admission investigation,
not P1's equal-access implementation or a product comparison. No model calls
were made. Use the five source pins in `p1_participants_20260929.json`; retain
EV-002 unchanged. Do not repeat source inspections without a changed cause.

## Decision

**The five-way equal-budget run is not admitted.** Do not build another evaluator,
silently switch to paid API access, claim missing usage is zero, or count an
unavailable comparison as a competitor loss. The existing Inspect runner and
native adapters remain the intended route. No quality ranking follows from this
assessment. Product functionality can continue independently.

| Participant | Existing useful capability | Exact remaining admission gap |
| --- | --- | --- |
| Eva2 | Digital worker has deadline, dispatch, model-run and cumulative reported token checks; refuses partial output accounting when calculating remaining allowance. | These worker limits do not establish identical foreground-conversation limits or complete memory/helper usage for a comparison sample. Bind actual runtime configuration and sample-wide accounting. |
| Eva1/Bottie | Subscription transport, deadline/abort support and recorded model usage/retry counts exist. | OAuth transport removes `max_output_tokens`; a configured output number is not provider-enforced parity. Complete sample accounting and equal cancellation/readback still require admission. |
| OpenClaw | Current native Codex harness registration passed in a network-isolated process. Official documentation describes per-attempt execution and separate tool/control timeouts. | Registration does not prove authentication, exact model settings, cumulative call/token limits or full usage. A plugin settings-upgrade warning remains. Npm integrity does not independently prove source-to-build provenance. |
| Hermes | Native Codex Responses route, model/reasoning interfaces, parent/child iteration budget, time settings and response usage counters exist. | Iteration grace calls and internal retries prevent equating iteration count with provider-call cap. General cumulative task token enforcement and complete memory/helper accounting remain unverified. |
| Agent Zero | Generic model kwargs, per-call output settings, provider usage and rate limiting exist. A follow-up found the built-in `codex_oauth` subscription bridge. | Inspected token limits are approximate 60-second rate limits, not cumulative task limits. Core loop is unbounded in inspected code. Generic Responses normalization omits `none`, while the OAuth bridge can preserve explicit reasoning or use its own configured default. Effective end-to-end settings and complete helper usage remain unverified. |

“Unverified” means the bounded inspection did not establish the property, not
that the product cannot support it. There was no exhaustive source audit or
live performance experiment. Native controls with different scopes must not be
represented as equal limits just because they have similar names.

## Source references

Local Eva2 source: `src/eva/bootstrap/delegated_digital_runner.py:413–451`
checks work deadline, tool dispatch count, reported usage and remaining output.
`src/eva/bootstrap/delegated_digital_composition.py:146–149` distinguishes task
and response token limits. These do not prove whole-installation accounting.

Local Eva1 source: `src/brain/llm.js:386–388` strips the output limit for OAuth;
`:658–679` records observed token usage and retry metadata. Existing
`cp001_native.py` and `cp001_accounting.py` preserve unknown totals and do not
admit a live run merely from caller-supplied fields.

Pinned OpenClaw source: `docs/plugins/codex-harness-reference/timeouts.md:18–58`
describes dynamic-tool deadlines; `:91–93` describes per-attempt elapsed time.
`extensions/codex/index.ts` registers the native harness. Executed registration
and its limits are recorded in
`../../evidence/product/p1-openclaw-registration-20260929.json`.

Pinned Hermes source (five-file inspection):

- `agent/agent_init.py:385–389,837–845`: Codex Responses route/client selection;
  `:2365–2366`: shared iteration budget.
- `run_agent.py:265–295`: model, reasoning, output and run-time interfaces;
  `:1500–1578`: direct runner/agent entrypoint.
- `agent/conversation_loop.py:1546–1565`: iteration/grace and retry loops;
  `:109–118`: detached-review-only cumulative input gate;
  `:1437`: interrupt handling.
- `agent/codex_responses_adapter.py:907–912`: reasoning/output fields.
- `agent/turn_usage.py:48–105,173–180`: response/MoA usage; unavailable usage
  remains unavailable. Full memory/compression/helper coverage is unverified.

Pinned Agent Zero source (five-file inspection):

- `initialize.py:7–17`: configuration delegates to model plugins.
- `agent.py:237–240,267–308`: task kill and message entry;
  `:394–410,536–538`: iteration/final-tool termination;
  `:845–878`: separate utility-model calls.
- `models.py:296–304,339–353,632–636`: rate windows and approximate tokens;
  `:570–577,641–648`: configurable retries.
- `helpers/litellm_transport.py:809–812`: output setting;
  `:1133–1146,1911–1920`: Responses reasoning normalization;
  `:436–446,1901–1908`: individual usage and absent-cost handling.
- `helpers/settings.py:94–95,619–620`: MCP timeout settings; enforcement not
  established by a settings declaration alone.

Coordinator follow-up on two additional Agent Zero files:
`plugins/_oauth/README.md` documents the native Codex device-flow subscription
bridge and Agent-Zero-owned credentials; rotating credentials must not be shared
with another client. `plugins/_oauth/helpers/codex.py:674–714,978–979` preserves
explicit reasoning or supplies the bridge setting (default `high`). Therefore
generic transport omission alone does not prove the final effort is wrong.
The initial import stopped on missing `simpleeval`. A follow-up installed only
the required import dependencies in an isolated Python environment and executed
the actual native configuration normalizer and request preparation without
network access or credentials. Four cases establish:

- Config-only `none` normalizes to `high`; it cannot establish contact parity.
- Explicit `reasoning_effort: none` or `reasoning.effort: none` survives at the
  bridge boundary. Upstream propagation through the agent remains unverified.
- Config-only `medium` survives for work requests.
- The bridge removes `max_output_tokens` in every case. It is not an enforced
  response limit at this boundary.

See `p1_agentzero_request_probe.py` and
`../../evidence/product/p1-agentzero-request-20260929.json`. Independent review
accepted these limited conclusions. This is not authenticated inference, a full
Agent Zero installation, provider enforcement, or sample-wide accounting.

## Remaining work, without a platform detour

1. Bind effective model/provider/tool settings and native runtime provenance for
   each participant using existing adapters. Agent Zero's native subscription
   route exists and its request preparation now executes locally; full upstream
   propagation and runtime access remain open before any model use. Do not
   repeat the completed bridge-shaping probe.
2. For each native control, establish the enforcement boundary and readback.
   Record unsupported equal-budget cases as unavailable; do not silently relax
   one participant's constraints or add an alternative paid route.
3. Freeze P1.2 task-specific and aggregate limits using the existing development
   cases before execution. The full EV-003 matrix is not authorized by this
   source assessment. Final unseen cases stay independently curated.

P1.1 remains open. This document ends this source-assessment pass; the next step
must resolve a concrete native admission gap or continue independent product
work, rather than repeating the same inventory.

## Transport configuration follow-up — 29 September

The actual pinned `ResponsesTransport.from_chat`, LiteLLM 1.88.1 HTTP
serialization and native OAuth preparer now ran together with mocked HTTP in
a disabled network namespace. Ordinary `reasoning_effort: none` arrives without
reasoning and falls back to `high`. The existing configuration
`extra_body: {reasoning: {effort: none}}` preserves `none` through this chain;
ordinary `reasoning_effort: medium` preserves `medium`. The participant manifest
now records these bounded transport presets without modifying Agent Zero.

This resolves the transport shaping question, not persisted settings, the full
agent loop, OAuth access or cumulative accounting. The initial probe used the
wrong mock-client type and made one unauthenticated request rejected with 401;
no model answer was generated. Corrected runs use the actual LiteLLM HTTPHandler,
network namespace isolation and a socket-connection rejection guard. See
`../../evidence/product/p1-agentzero-transport-20260929.json`. Native comparison
admission remains open; do not repeat this shaping probe without changed code.
