# Shared-route latency candidate

Base: `4701dd8ffd697cbd384c41ed1f2e56775a44fc03`.
Scope: model/context preparation and focused regression tests; no deployment,
central status edits, model/provider change, memory loss or capability filtering.

Estimated effort: 60–90 minutes. Exactly one before/after pair, each a fresh
interpreter invoking the real ASGI voice endpoint with installed authority,
memory, tool catalog, request journal and provider. Input: `Hallo.`; fresh
conversation IDs. No physical audio and no general p95 claim. At most two
logical provider requests, with any adapter retries included in measured usage.
No model calls for ordinary regressions. Currency cost and coding tokens unknown.

The before call is recorded in `live-contact-route-before-2026-09-22.json`:
4216 ms first delta; memory 708 ms, context/catalog reads 202 ms, tokenizer/input
admission 341 ms, model first content 2791 ms. The missing stage evidence justified
this call; the previous 5116 ms live measurement did not expose those stages.

End conditions: remove an observed unnecessary serial cost, preserve identical
memory/capability/identity contracts and cancellation/authority checks, pass
focused tests, record the single after call and its usage, commit an installable
candidate. If provider variance prevents a total-speed claim, separate the
controlled local improvement from residual provider wait. Root reviews,
integrates, installs and performs final live verification.
