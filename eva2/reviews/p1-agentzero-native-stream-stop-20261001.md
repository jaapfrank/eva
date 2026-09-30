# P1.1 comparison stream ownership review — 1 October 2026

Independent review of the added `SampleAsyncStream`, the explicit `--bind-stream` hook, three focused HTTPX tests, sanitized stream-stop evidence and the participant `comparisonStreamOwnership` record. Retained v7 report and execution-source record were read, alongside the relevant pinned native asynchronous stream/close route. The stock v5 report hash was checked. No tests, authentication, inference or replay were performed; credential files, tokens and login challenges were not inspected. Only this review file was written.

## Disposition

**GO for this bounded comparison-side ownership fix and its sanitized checkpoint.** No actionable blocker was found in the reviewed route. This is explicit assistance supplied by the comparison harness, not stopping behavior attributable to stock Agent Zero. Native admission, full P1.1 completion and product-function completion remain unproved.

## Ownership and error handling

The wrapper returns the original SDK events without changing their contents or inventing usage. While an attempt remains pending, it binds the actual stream-consuming task, rather than relying on the task that initially obtained the iterator. Sample stop therefore targets the owning sample's pending consumer. The existing queued cancellation callback still rechecks the exact attempt/task binding before cancelling.

On cancellation, stream failure or exhaustion, the wrapper closes the SDK iterator's own close operation or its exposed HTTPX response. It settles incomplete usage only after that close returns successfully. Already completed usage survives exhaustion. A failed close remains an error and an unsettled attempt; a later HTTPX no-op close cannot turn that failure into successful closure. The focused tests exercise independent concurrent samples, unchanged completed bytes/usage and this failed-close case. Their retained passing result is implementer evidence, not a reviewer rerun.

The native transport's finalizer calls the same wrapper close operation. In this reviewed route, one task consumes each stream and its cancellation handler completes before that finalizer. The check establishes this ownership path, not general cancellation of unrelated helpers, synchronous calls, tools or remote provider work.

## Retained execution and boundaries

V7 uses the actual pinned native model construction, asynchronous SDK, registered OAuth routes and owned-auth forwarding, with upstream provider HTTP mocked. The completed Sol/none SSE case returns the asserted fixture output and retains synthetic usage of 21 input and 9 output tokens. Its stream and upstream close before explicit fixture cleanup.

The partial case records a genuinely cancelled model task, closed guarded stream and native upstream, zero bound active attempts and null usage **before** fixture cleanup. The attempt remains `native_upstream_closed_usage_unknown`; cancellation does not become a completed response or zero-cost observation. Explicit cleanup is separately retained. The unguarded v5 limitation remains unchanged.

The earlier v6 preparation failure admitted no mocked upstream and masked native auth-refresh failure with an indexing error. The current freshness preflight and non-200 status boundary avoid that masking without publishing raw proxy diagnostics. The recorded native refresh is a separate authentication request, not inference. This review does not independently attest its private credential contents.

The participant record and evidence correctly exclude deployed proxy behavior, cloud cancellation, real provider usage, complete helper/retry/sync/tool accounting and participant parity. Synthetic tokens are not Eva runtime consumption; credits remain unknown.

## Provenance

The retained report and execution-source hashes match the published evidence. The ledger hash matches the executed version. Reversing only the documented reporting metadata rename in the current probe exactly reconstructs the executed probe hash; no replay or executable-path change is implied.

- Executed v7 probe: `340e5271e5ceaecc3baae8be18af2c7b2f8e6ec76aeb74ff029e391fd5bbde4d`.
- Current probe: `376524eb04112f816ec69ded430254e2e2ba63c5cb641b72692d074df779e898`.
- Executed/current ledger: `fcb5b496b4510ceb779315b2827b2b1a3686829ec6c4b9816794db20634b389d`.
- Focused tests: `c6189e3f8bb5ee4f46605372d75da7b0e2d9625a408cc4ee0ab596b4e6001bd9`.
- Retained v7 report: `67b9c53b2056fab47a58df5469d817f802321d048a91193ec7e77f728d07cd84`.
- Retained execution-source record: `2ea600d0e25cbd65cc717bced8be647613225bb2579f43524473b6efa63288c8`.
- Unchanged stock v5 report: `9a65d5ac4bca83729b42cd4f94278d1bfcd50ead2b791f0ecb2d0706315b7a64`.
