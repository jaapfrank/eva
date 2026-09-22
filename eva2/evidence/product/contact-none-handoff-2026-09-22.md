# Luna reasoning none: bounded compatibility candidate

This separate candidate is based on latency preparation commit
`2e0598a7f64e73add2d9a9f8b8f8a6416989677d`. It changes only effort validation;
there is no deployed configuration change, model switch, API-key route, fallback,
tool change or new product behavior. Root owns independent review and installation.

## Scope and configuration

`EVA_CONVERSATION_CONTACT_REASONING_EFFORT=none` is now admitted only when
`EVA_CONVERSATION_CONTACT_MODEL=gpt-5.6-luna`. The default remains `low`.
PydanticConversationModel and OpenAICodexProviderSettings admit `none` only for
that exact model. RuntimeSettings still rejects `none` for the work model, both
through environment parsing and direct construction. Astra's ordinary medium
effort and all prior low/medium/high options remain unchanged.

The public Luna API page lists `none`:
https://developers.openai.com/api/docs/models/gpt-5.6-luna . That does not itself
prove subscription availability; the requests below check the actual existing
subscription transport without reading secrets into evidence.

## Closed request budget

The coordinator authorized at most two provider requests for this new hypothesis:
one tiny capability probe and, only if accepted, one real route probe. Both were
used. No further calls are authorized by these scripts. The earlier low pair is
separate and remains closed.

1. Tiny identity/greeting, no tools, no private memory: outgoing wire records
   `gpt-5.6-luna`, `reasoning.effort=none`, `summary=auto`; HTTP 200 and Agent.run
   returned. Elapsed through the later measurement failure was 4408 ms. The
   measurement code then incorrectly called the SDK 2.37 `result.usage` property
   as a function. The saved receipt's `accepted:false` reflects that exception,
   not a provider rejection. Output and usage for this call are **unknown**;
   neither is reconstructed or estimated. The corrected script uses the property
   but was not rerun. Local inspection confirmed the installed property accessor
   and a model-free AST check confirmed removal of the mistaken call.
   An earlier preparation attempt used the wrong credential file default and
   made zero network requests; using the configured lease path fixed preparation.
   A short answer below 64 tokens was requested in the instruction. The existing
   subscription transport rejects server output-cap fields, so this was not a
   hard server-enforced token limit.
2. Same real ASGI voice-session greeting and complete context as the low pair,
   in a separate interpreter with candidate source loading and temporary
   process-local contact effort. No restart, container file copy or deployment.
   Exactly one provider attempt, **5033 input / 15 output tokens**, zero tool
   calls. Outgoing wire again confirms Luna `none`; there are 38 deferred action
   tools plus hosted tool search on the wire. Action dispatch was refused by the
   probe. Existing authority, journal, memory and scope checks remained active.

The tiny probe has a one-network-request guard; the route probe has a one-request
projection guard. No fallback to low or another model occurred. Euro cost and
the combined token total are unknown because first-call usage was not retained.

## Results and limits

| Measurement | Original low baseline | Preparation candidate low | Preparation candidate none |
| --- | ---: | ---: | ---: |
| First route delta | 4216 ms | 4551 ms | 4379 ms |
| Route completion | 4826 ms | 5300 ms | 5025 ms |
| Model first content | 2791 ms | 3277 ms | 3543 ms |
| Model completion | 3306 ms | 3952 ms | 4048 ms |
| Memory preparation | 708 ms | 945 ms | 607 ms |

Compared with the same preparation candidate using low, none's route delta was
172 ms earlier and completion 275 ms earlier, while model first content was
266 ms later. The shorter memory phase accounts for the apparent route gain;
these single observations do not establish a reasoning-effort latency benefit.
The original baseline was also faster than none. There is **no Instant or
natural-conversation speed guarantee** and no recommendation to deploy based
on a claimed speedup.

Memory count/hash and instruction hash match the preceding pair exactly. The
none call used 5033 input tokens, matching both low calls. This establishes
subscription compatibility with the current full context and deferred tool
catalog, not tool-use quality, physical audio latency or general model quality.

## Validation and evidence

109 tests passed in 11.77 seconds using a disposable network-disabled container:
the new `test_contact_none_settings.py`, runtime settings tests, shared provider
tests and existing contact/work routing tests. New cases cover defaults,
environment parsing, work-model preservation and rejecting none for Astra,
unknown models and a missing contact model at each relevant boundary.

Evidence files are `contact-none-provider-probe-2026-09-22.json`,
`contact-none-route-after-2026-09-22.json`, and
`contact-none-tests-2026-09-22.xml`. The probe and isolated launcher are retained
for review, not automatic execution. The broader previous candidate's known
catalog-hash baseline failure is still documented in its own handoff; this
focused settings/provider selection does not resolve or hide it.

For a minimal integration, this commit's three production-file hunks are
independent of the preceding preparation behavior despite its parent commit.
No settings file is modified. Activation would require root's explicit choice
to change the contact effort only; rollback is that same setting back to low.
