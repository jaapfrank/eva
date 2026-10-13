# One isolated public research proof after transport repair

Source: `3b5370d0` (canonical main at snapshot creation), including the shared
composition, hosted-call counter, owned async transport, and program-hash entry.
The coordinator explicitly authorized exactly one new provider request after
independent transport review. No installation was performed by this task.

## Result

The same public HEAD/RFC question completed in **19.041 seconds**. The isolated
owner-scoped delegated row stored `succeeded`, a result hash, source-bearing text,
and measured usage: **8,886 input + 142 output = 9,028 tokens; one hosted tool call**.
The sanitized receipt retains the completed web-search item ID and terminal usage;
the count is not inferred from URLs. One HTTP request was dispatched; none retried.
The answer identifies RFC 9110 section 9.3.2 and the prohibition on HEAD response
content. Its source link targets the primary RFC document, without a section anchor.

`cost_usd_micros=1949` is the existing API-equivalent estimate, not a measurement of
subscription credits. Actual credits remain unknown. The maximum hosted-tool count
is a result admission limit, not an enforced provider-side search cap. Local deadline
and socket closure do not prove remote computation or billing cancellation.

## Isolation and evidence

The existing read-only lease and runtime budget fed the shared
`build_public_research_composition` factory. PostgreSQL 16.13 ran in a separate
container with synthetic authority records, separate database, and loopback port.
The source mount was read-only. No live database, private memory, external message,
new subscription, or extra provider request was used.

Before dispatch, the exact `SingleRequestGuard` class was exercised with a fake
transport inside a network-disabled container. It admitted the first simulated
request and rejected the second before transport. The real run patched
`httpx2.AsyncHTTPTransport.handle_async_request` with that same guard and allowed
only the existing Codex responses URL. `guard-offline.json` records the check.
`real-probe.json` records durable readback and sanitized SSE event receipts; no
credentials, request headers, or private conversation were captured.

The worker ran as an async task and the same event loop remained available while
research ran. This is not a real channel/Voice PE conversation acceptance test.
The database was dumped to `/tmp/eva2-research-proof2-20260922/disposable-db.sql`
before the test container was removed. The original ambiguous probe is unchanged.

## Installation and rollback advice

The coordinator should deploy the reviewed shared source to core and the existing
worker, with matching lease/budget inputs and research program hashes. Enable the
opt-in in both compositions and mount the existing runtime budget read-only for
the worker. Keep the existing worker rather than starting an additional scheduler.
Readiness/config validation is not another authorized provider probe.

For a compatible rollback, turn research admission off while retaining the v2-aware
reader and source. Quiesce or explicitly resolve admitted v2 work before replacing
its sealed program; never replay an ambiguous attempt or rewrite its grant. A
pre-v2 binary cannot safely be assumed to read v2 rows. Preserve data, prior images,
and configuration. Technical installation, live channel verification, and owner
acceptance remain separate coordinator responsibilities.
