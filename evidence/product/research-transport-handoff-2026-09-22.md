# Local research deadline and cancellation ownership

This separate candidate follows `c748391eb0f8eadad00fb43e22079c711c0000b5`.
Scope: model/research transport and its synchronous-to-async bridge only. No
voice/contact redesign, latency experiment, provider request, new dependency,
runtime setting or deployment. Root owns integration and activation; the research
agent is independently reviewing the transport.

## Activation blocker and choice

The source review of research candidate `8834f2a6` found a local lifecycle blocker:
NativeResearchTool used a bare `asyncio.to_thread` around synchronous urllib I/O.
An orchestrator timeout could cancel its await while leaving the provider thread
running. `asyncio.run` then waited for that executor during shutdown. urllib's
socket timeout was not a total SSE deadline, so repeated incoming data could
hold the shared delegated worker beyond the intended research timeout.

The pre-fix offline reproduction at `c748391e` returned success after 0.265 seconds
with a configured 0.05-second timeout. It used only a synthetic stream and zero
external requests; see `research-deadline-baseline-2026-09-22.json` and its guarded
reproducer. The guard prevents running that old urllib fixture against the new
transport, where its urllib monkeypatch would no longer intercept network I/O.

The existing tool-free worker uses async Pydantic/HTTP transport, not a reusable
subprocess boundary. This fix uses the already pinned public `httpx2.AsyncClient`
and existing `run_cancellable` / `run_in_cancellation_thread` helpers. No private
socket access, extra scheduler or abandoned synchronous network thread is added.

## Implementation

- `research_transport.py` performs request headers and body reads under one
  absolute async deadline. It bounds response bytes to the existing 8 MiB limit,
  owns the async response/client and closes them before returning or raising.
  HTTP error bodies remain bounded to 1024 bytes. It neither follows redirects
  nor retries transport failures.
- `_request_using` selects this helper only for the existing explicitly admitted
  provider-managed research path. One deadline is shared across the existing
  allowed credential attempts; lease loading, URL, explicit authentication
  headers, canonical request bytes, parser shape, token budgets and usage checks
  remain in their existing adapter. Ordinary chat/legacy action transport is
  unchanged. Research has no incremental user-text delivery contract; bounded
  response bytes are parsed by the same SSE parser after the network is closed.
- `NativeResearchTool.execute` uses the existing owned cancellation-thread bridge,
  wrapped by the existing request cancellation registration. Owner stop and an
  outer orchestration timeout therefore signal the async provider task and wait
  for its local cleanup before returning. This preserves context propagation and
  maps owner cancellation to WorkInterrupted. The worker's durable stop/replay
  policy is unchanged.

Closure is local evidence only. It does not prove that the remote server stopped
computing or reporting billable usage. An interrupted response without terminal
usage remains unknown; this change invents no counters or retrospective proof.

## Verification

**215 passed, one known catalog assertion deselected, in 14.64 seconds** in a
network-disabled disposable container. The selection includes research transport,
hosted usage, both model suites, research-source parsing and cancellation tests.
No test assertion was relaxed; three existing research fixtures now intercept
the new transport boundary instead of urllib. The pre-existing catalog hash
mismatch remains documented in its earlier separate failure receipt.

Twelve new transport cases cover unchanged request content/auth fields, ordinary
terminal usage, absolute time across a dripping stream and blocked headers,
response-size limits, HTTP errors, a shared deadline across authentication
attempts, owner stop before dispatch, and owner stop / outer timeout while headers
or body are blocked. Bridge fixtures assert that the provider worker has finished
and the stream/client are closed **before** asyncio.run's executor shutdown.
Two fixtures also use real loopback TCP sockets with the pinned HTTP client and
observe peer closure at deadline. They contact only the test server inside the
network-disabled container, never an external provider.

Receipt: `research-transport-tests-2026-09-22.xml`. Independent review is recorded
separately by the research agent; this document does not self-approve the change.

## Integration

Production files: `adapters/model/pydantic_conversation.py`,
`adapters/model/research_tools.py`, and new `adapters/model/research_transport.py`.
Root must also add `adapters/model/research_transport.py` to
`research_program_hash()` in the separately owned `bootstrap/delegated_research.py`.
Both core admission and worker must then use matching sealed release bytes.
The source branch does not contain that other agent's new research composition,
so this candidate does not edit or overwrite it.

Research candidate source review found no additional blocker in v1/v2 authority,
private owner scope, exact task binding, stop/replay fencing or measured failure
retention. The canonical opt-in wiring also preserved the owner-only catalog
filter and ended foreground work after research admission. Activation still
requires independent transport review and integrated verification. Rollback must
retain a v2-compatible reader for admitted research rows; a pre-v2 binary rollback
is not established. Existing voice features remain outside this change.
