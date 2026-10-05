# T3 installed browser handoff review - 5 October 2026

**Status: GO for installed T3.3 and the declared T3 browser case.** The source,
focused tests, installed hashes and completed actual-route stages support the
unchanged technical outcome. No blocking review findings remain in this scope.
Personal takeover experience and family acceptance remain separate.

Reviewer: `/root/t3_installed_review`, independent of implementation and probe
execution. Base: `ad191138`. This review changes no runtime code or workboard,
runs no service/browser/model actions and does not repeat the existing tests.

## Original outcome and scope

The unchanged T3.3 condition is: takeover stops ongoing operation; return requires
a fresh observation and does not repeat an old action. The original T3 parent
in `design/blueprint-2026-09-28/delivery-plan.md:306` asks for one declared
browser/file/service task, preservation of its saved artifact, takeover and
fresh-observation continuation. Its following paragraph limits native-application
claims to the declared case.

The retained shared-Chrome and autonomous-own-browser evidence covers completed
ordinary browser tasks. I also read their saved runtime work records, rather than
treating the coordinator's evidence JSON as the user artifact: the own-browser
record retains `result_payload.output` and its output hash; the shared-Chrome
owner projection retains `result_available=true`, the output/hash and the private
retrieval route. This supports a preserved user work result for the declared
browser case. It does not establish exported files, uploads/downloads, arbitrary
file/service operations, all desktop applications or general browser reliability.
No extra task or test case is inferred solely from the slash-separated wording.

## Firewall dependency

I read the complete helper and its focused test file. The change permits only
canonical positive single-host `/32` source jumps that are disjoint from both
protected browser addresses before/interleaved with the exact own hooks. Broad,
overlapping, negated or unrecognized rules fail; both own hooks and exact own
chain contents remain required. The change neither reorders unrelated rules nor
weakens the delivered destination restrictions. The 37 authored tests cover the
reported disjoint layout, unsafe prefixes, duplicates, exact chain mismatch and
recovery of a missing own hook/interrupted own chain. Their recorded result is
37 passed in 0.25 seconds; this review did not rerun them.

Independent readback found the source and installed
`/usr/local/lib/eva-owner-browser/recover_firewall.py` share SHA-256
`84b932f979f6bbe969674a1dec851e359bdcf6f03f043f77ebef9e619a3bbb91`.
The coordinator's installation receipt records unchanged firewall rules,
preserved profiles/data and no core-image replacement for this dependency.

## Installed handoff evidence

I read the predeclared case, probe and continuation scripts, all completed event
stages and `evidence/product/t3-owned-handoff-installed-20261005.json`.

- Recovery began from recorded `recovery_required`, not a human-owned session.
  The first preparation failed while the gateway catalog was not ready.
- The actual synthetic page had `Effects: 0`. Its disabled-button adapter call
  remained pending at 2.720 seconds. Takeover returned revision 78, owner control
  true and MCP readiness false at 3.052 seconds.
- The pending HTTP request ended in `TimeoutError` at 15.813 seconds. The harness
  confused this with its own wait deadline; continuation retained the same state
  and did not repeat takeover or the pending click.
- Return reached revision 80. Immediate preparation again failed catalog
  validation before invocation. After actual catalog recovery, old reference
  `e4` was rejected before the fresh snapshot.
- The fresh snapshot showed `Effects: 0`; enabling the button and waiting two
  seconds still showed zero. One new click then produced `Effects: 1`, confirmed
  by a separate snapshot. The fixture was closed to `about:blank`; final state
  was `eva`, revision 80, with its profile preserved.

The saved stages substantiate the observed lack of delayed/replayed effect.
They do not include an independent server dispatch trace for the pending click
or the raw provider reason for the old-reference rejection; the latter is saved
only as `ToolExecutionRejected: MCP tool rejected the operation`. Immediate HTTP
cancellation, exact provider error wording and universal cancellation of effects
already executed are therefore not claims of this review. No new Eva model call
was used. Runtime credits and development consumption remain unknown.

## Readiness correction

I independently reviewed `browser_autostart.py`, `mcp_gateway.py`,
`mcp_discovery.py`, their immediate call context and the focused tests.
The actual gateway history records its browser endpoint failing three health
checks at 01:31:33 UTC, explaining why catalog preparation still failed just
after controller resume. The correction waits for the existing gateway health
process only when the raw catalog field is exactly `reachable=false`, all other
admission fields remain valid and a new managed-browser operation is being
prepared. Missing/unknown reachability, disabled or changed catalogs, unmanaged
connectors and durable result replay do not gain this path.

Each wait cycle checks the current controller generation and registry authority
before/after waiting and again after real health returns. It preserves the
existing catalog hash/schema checks and cancellation mechanism. Waiting is
bounded by the pinned 60-second gateway health interval plus existing request
I/O allowance; exhaustion remains a reported conflict. The loop performs only
status, registry and catalog reads. It never normalizes health fields, starts or
resumes the browser, retries a tool action, or repeats a captured result.

The saved test command and log show 129 passed in 14.73 seconds across the
existing gateway, discovery and browser-autostart files. Relevant added cases
cover healthy admission/cache reuse, unsafe or unknown catalog fields, sealed
schema changes, owner takeover, generation changes, revoked authority, changed
configuration, cancellation and health timeout. I did not rerun this suite.

The first installed readiness check exposed another concrete failure: one-second
catalog polling reached the gateway's HTTP 429 limit before recovery completed.
That failed sequence is retained in `installed-readiness-events.jsonl`. I reviewed
the resulting cadence-only correction: five seconds between reads, with the
same deadline and authority/cancellation checks. Its two-page test requires 26
GETs over the 60-second health cycle against the observed 100-per-minute tier.
HTTP 429 is not retried, and cancellation interrupts the actual sleep. The
affected discovery suite then passed 46 tests in 0.68 seconds. This addresses
the current catalog; arbitrary catalog size or concurrent load is not proven.

## Final installed verification

I independently hashed the three changed Python files in both final running
containers and compared them with the reviewed repository and installation
receipt. All match: gateway `d49e58d4fb11eefc0139c249918e5fcdaac870cf9d831459da9ba7ae1574b122`,
autostart `c1f41f77110f1f728dea0c9ec31205a5fd49434aa66ddd25c4611f33bd685bd2`,
discovery `0a9026763ffc4e7ce7b960730e7289287fe19eb50efa3f77a9cbec6ab0d2e1f8`.
Final core image is
`sha256:b42a5b8a74eaf7307339b8513fbb1e3343d925039a4e0e7866ddfc4defc05dca`;
worker is
`sha256:42e43365526c6e0f6d9ecb5e0ccd2f5c0829fc7c2c44ab72a5b92ff5a1733468`.
The package receipt confirms the same three files plus the firewall helper and
a successful 1,225-file/eight-image package check. Its retained runtime image
`35eba561...` is explicitly unchanged; this is not a fresh-installation claim.

I read `installed-cadence-probe.py` and its completed event log. The final
controlled case starts at owner revision 90 with real catalog reachability
false. Return reaches Eva revision 92 while the catalog is still false.
The installed managed admission waits 36.584 seconds, then an independent
catalog read shows true and an actual fresh snapshot succeeds on `about:blank`.
Final state remains Eva revision 92 with MCP ready and the profile preserved.
There are zero model calls and zero action retries. This specifically resolves
the identified readiness dependency without repeating the counter task.

## Technical decision and aggregation

The original counter stages, retained ordinary browser tasks/saved user outputs
and final readiness proof jointly support technical completion of T3.3 and T3
for the declared browser case. Earlier failures, pending-HTTP timeout, absent
raw rejection reason and absent independent pending-dispatch trace remain
visible limitations, not upgraded proof.

With the previously completed T1, T2 and T4 outcomes/evidence retained, step 3
may be marked technically complete by aggregation of its existing four slices.
This review does not reopen or replace those earlier reviews, add task IDs,
prove arbitrary browser/file/service work or infer personal/family acceptance.
The saved artifact for this browser case is the retained user work result; an
exported file or separate external-service workflow is not claimed.
