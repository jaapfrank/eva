# Shared-route preparation improvement; overall latency remains open

Base: `4701dd8ffd697cbd384c41ed1f2e56775a44fc03`. No deployment, runtime
configuration change or central workboard edit was performed by this task.

## Result

The candidate removes two observed serial preparation costs without dropping
context or changing model selection:

1. Runtime construction verifies and loads the pinned local tokenizer before
   readiness. Every model request still performs input admission; credentials
   and the provider are not contacted by this initialization.
2. After pending action recovery, the Hindsight recall overlaps independent
   tool-availability and active-context reads. Both finish before model
   dispatch and the next authority/cancellation checkpoint. Request ContextVars
   are copied into the recall thread. Only adapters explicitly declaring support
   use this path; other memory adapters retain sequential preparation.

No memory filters, ranking, facts, identity, prompt instructions, tool schemas,
model choice, delegated-work semantics or stop boundaries were removed.

**The fixed two-call comparison does not demonstrate overall speed improvement.**

| Metric | Installed baseline | Candidate interpreter |
|---|---:|---:|
| First ASGI voice delta | 4216 ms | 4551 ms |
| Full ASGI response | 4826 ms | 5300 ms |
| First input admission | 341 ms | 15 ms |
| Memory recall | 708 ms | 945 ms |
| Tool availability + context reads | 139 ms, serial | 218 ms, overlapping recall |
| Model phase first content | 2791 ms | 3277 ms |
| Provider input / output tokens | 5033 / 20 | 5033 / 20 |

Pending recovery precedes those reads in both versions (63 / 103 ms). The model
phase includes SDK/provider/network work; this probe does not isolate server
queueing from all client overhead. The memory hash was identical, and the
instruction hash matched the earlier installed preparation profile. No p95 or
physical microphone/audio claim follows from one pair. Provider/service variance
exceeded the local improvement in this pair; no repeat was used to seek a better
number. The earlier installed HTTP result (5116 ms first delta) remains separate.

The candidate was loaded through an import hook **only in a new interpreter**
inside the installed core. It used the real ASGI voice endpoint, access checks,
journal, memory, catalog and configured provider. There was no source copy into
the server, configuration change or restart. Each input was `Hallo.` in a fresh
session. Action execution was blocked for this greeting probe. These requests
use the ordinary journal/after-response path; the preparation-only probe did not
write conversations/tasks but did call the ordinary Hindsight bank-ensure path.

## Input accounting

Measured provider usage: **2 attempts, 10066 input tokens, 40 output tokens,
0 tool calls**. No further model calls. Subscription currency cost and coding
agent tokens are unknown. Estimated effort was 60–90 minutes; no dedicated
development-time meter was kept. Ordinary tests used model fixtures.

The actual 5033 input tokens per greeting differ from the previous synthetic
Luna greeting's 1414 tokens. The previous 7616 figure was the total of six calls,
not one greeting. The actual route includes identity, memory, voice/task context
and tools. Local BPE estimates, **not a provider-supplied breakdown**, were:

| Input component | Local estimated tokens |
|---|---:|
| General conversational instructions | 657 |
| Fixed principles | 58 |
| IdentityPack presentation | 225 |
| Style mirror | 148 |
| Selected memory | 44 |
| Active state, contact and voice instructions | 620 |
| Recent-history section and answer bound | 23 |
| Entire instruction string | 1775 |
| JSON for all 38 offered tool definitions | 4902 |
| User prompt | 2 |

These estimates must not be summed into a purported provider breakdown. Deferred
tool handling, serialization/framing and model tokenization differ from this
local estimate. No context was removed to reduce the measured 5033 tokens.

Concrete follow-up findings, not changes in this candidate:

- Request admission reconstructs and tokenizes the same envelope in `generate`
  and again in `_run_pydantic_phase`; the second baseline pass was only 11 ms.
  Both checks remain in place.
- `generate` passes an empty `available_actions` to `_instructions` when tools
  are offered. That can insert the statement that future work cannot be scheduled
  despite scheduling/delegation tools being offered. This existing prompt/catalog
  inconsistency should be checked against task behavior separately; it is not
  established as a measured latency cause.
- General behavioral guidance, IdentityPack and voice/contact instructions
  repeat brevity, natural wording and evidence boundaries. Their token costs are
  now visible, but semantic equivalence of any compression is unproven. No policy
  or identity text was shortened here.

## Validation and known baseline failure

Focused tests cover deterministic read overlap, pending-recovery ordering,
ContextVar propagation, concurrent person isolation, cancellation and preparation
errors preventing model dispatch. Tokenizer tests prove local initialization,
failure on missing pinned vocabulary and continued per-request budget refusal.
Existing conversation/contact/voice, Hindsight audience/privacy, model and
runtime-settings regressions were included.

The broad focused selection has **344 passed and one existing failure**:
`test_action_tool_catalog_remains_canonically_byte_equal` expects catalog digest
`e96adcb879caaf9a7ef864369c15d30db1b106603e8c37056b1e55c8511b6ca0` but the installed
baseline and candidate both yield
`4b2ed4d6d8b444a917aee8c34ed92c13b08f72f94effed8f3ce1423ebdd9d905`.
The exact failing test was separately run against `/opt/eva/src` in the unchanged
installed image and reproduced the failure. This candidate does not change tool
definitions or weaken/update that assertion. Root must retain this known failure
in the review/release decision; the whole selection is not described as green.

The Windows checkout initially converted sealed frontend bytes to CRLF; restoring
only those unchanged assets from HEAD through WSL fixed test collection. No asset
change belongs to this feature. Old local board fragments are preserved in a
named Git stash and were not reapplied onto the current integration basis.

Evidence: `live-contact-route-before-2026-09-22.json`,
`live-contact-route-after-2026-09-22.json`, preparation profile JSON, and
`live-contact-latency-tests-2026-09-22.xml`. Probe scripts are retained for review,
not for an automatic new model-call round.

## Integration

Root owns independent review, integration, deployment and central status.
Cherry-pick only the delivered feature commit onto current main. Rebuild the
ordinary core image with these four production files:

- `src/eva/application/conversation.py`
- `src/eva/adapters/projection/hindsight.py`
- `src/eva/adapters/model/pydantic_conversation.py`
- `src/eva/bootstrap/runtime.py`

No settings or migrations are required. Startup may take roughly the previously
observed tokenizer initialization time longer; readiness must remain closed if
the pinned vocabulary cannot be verified. Normal rollback to the prior core image
restores sequential preparation and lazy tokenizer initialization without touching
stored data. Keep existing workers, voice adapters and permissions unchanged.

Run the focused pytest selection recorded in the XML, with the existing pinned
runtime dependencies. Its one reproduced baseline catalog assertion remains open.
Any post-install verification is coordinated by root; do not automatically rerun
the two model-backed scripts. Overall natural conversation speed remains open.

## Model-free Instant and reasoning check

Official API documentation lists `none` among Luna's supported reasoning efforts:
https://developers.openai.com/api/docs/models/gpt-5.6-luna . Eva currently rejects
`none` in runtime settings, PydanticConversationModel and OpenAICodexProviderSettings;
changing an environment value alone would therefore fail validation.

The official `chat-latest` page describes the latest ChatGPT Instant model but
lists Tool search as unsupported:
https://developers.openai.com/api/docs/models/chat-latest . Eva's current route
uses server tool search and deferred tools, so this is not an established drop-in
replacement. Public API support does not establish subscription-route support.

Sanitized local cache evidence is in `contact-instant-metadata-2026-09-22.json`.
The WSL cache dated September 8 lists Luna without `none`; the Windows cache dated
September 22 lists GPT-5.5 and does not list `chat-latest`. Cache absence does not
prove backend rejection. No additional model requests or configuration changes
were made for this check; the two-request budget remains closed.
