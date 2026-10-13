# P1 native provider control — independent review, 3 October 2026

**GO for the bounded offline native adapter seam. P1 remains open.** This connects the existing Bottie HTTP/agent/fetch fixture to `ProviderSampleLedger`; it neither replaces Eva1 nor adds a second ledger, evaluator, live mode or comparison allowance. The review read the combined four-file delta and existing test receipts without running tests, models or providers.

The serial worker protocol binds provider events to the expected request, unique event and reserved attempt. Duplicate events, wrong identities and invalid terminal states cannot create successful accounting. Raw completed usage goes through the existing ledger; missing/incoherent usage remains unknown and prevents further admission. Stop rejects later dispatch, interrupts a pending permission wait and preserves the distinction between a reservation and a dispatched request. An aborted terminal with no attempt ID can close an already reserved attempt only for the matching request/event after the parent has requested stop. It records aborted/unknown usage, not successful completion or cloud cancellation. The worker also observes an already-aborted native signal before dispatch.

The two controlled native interleavings are now covered:

- First request dispatches once and reports synthetic SSE usage 17 input/5 output; stop occurs before admitting the next native request. No later dispatch remains.
- The first request is reserved, but its permit is never delivered. Stop produces the matching aborted terminal, zero provider dispatches, unknown usage and `complete=false`. Both worker pending-control and active-attempt counts reach zero.

The latter uses the first native request deliberately. It does not assume the later interpretation/response phases use the same model or weaken role enforcement. The temporary environment override has been reverted. The previously successful uncontrolled default-route replay check is retained; it was not rerun by this reviewer.

## Execution history retained

The initial combined run was **7 passed / 1 failed in 2.41 s**. Its reservation test incorrectly assumed the second native phase matched the first profile; the ledger correctly refused it. An environment-only fixture attempt still produced **1 passed / 1 failed in 2.74 s**. A diagnostic execution also failed (**1 failed in 1.74 s**). Those logs remain evidence, not discarded attempts. The final correction changes only the race test to reserve the first permit; the unchanged six parent cases and the earlier passing pre-admission-stop case are reused. The corrected case alone passed **1/1 in 1.40 s**, exit 0. All four current source/test hashes match that final immutable receipt.

The evidence therefore covers the eight intended cases through seven retained passes and one corrected-case pass. No additional test is required for this bounded outcome.

## Limits

This uses real native ingress/agent dispatch with a synthetic provider response in a private network namespace. The contact fixture binds the observed profile; it does not establish prescribed native model/effort parity, full helper/memory/tool coverage, provider-enforced token limits, complete live admission or a comparative result. No live provider or model call occurred.

`response.clone().text()` consumes the complete fixture SSE before returning the original Response to the native consumer. Byte preservation does not establish latency-neutral streaming, live streaming cancellation or cloud stop. No such timing or cancellation claim is accepted. Comparison budgets, final unseen tasks and participant admission remain separate existing work.

## Exact bindings

| Source/test file | SHA-256 |
| --- | --- |
| `eva2/benchmark/inspect_ai_v1/cp001_native.py` | `8839d4efc75be5b67f6e25bfaf8aa816cb20d3bf7d63a8ddeb43b25db837cb36` |
| `eva2/benchmark/inspect_ai_v1/cp001_bottie_worker.mjs` | `f473c3984bf114e6480143a17352d503ebb96fcfdcd391e3c949154a578ade21` |
| `eva2/tests/benchmark/test_cp001_native.py` | `5034d6023f33a7b8b1e9f5078a351c83574b4d7a8b272ab4fe761d3c76886969` |
| `eva2/tests/benchmark/test_cp001_native_bottie.py` | `18a408acbf275127293473a5ad039c57f2b1740fccc63c0bcd80d40b6b5c6fd6` |

Private proof root: `/home/administrator/project/.eva2-artifacts/p1-native-control-20261003/`.

| Receipt/log | SHA-256 |
| --- | --- |
| `result.json` | `c6faa3069ab11d873a39a7063fcfc2677fe34d785fea693a87a7d292d0899ef7` |
| `pytest.log` | `cc58e6c5d502d11fa0da5f1073e7f28ec809b86781208e518e931ad1a275ec38` |
| `controlled-profile-check.json` | `e6efaf972a7e3e7a9321c5f415cd41219dc07b02062d5a4476693d8e0c10d8ca` |
| `controlled-profile-check.log` | `0b15afda61906dfb21cb97cab1d70b34f20b33deaf9cfcf332e3862d1918debe` |
| `permit-diagnostic.log` | `8166161b420413ead532b21baacf38cbce657ffa0f3b99b0fe1a4512dd7728de` |
| `reserved-permit-check.json` | `c06fa26d9382a16f6618153f316984141c0e41ff24308e431ee75401e0a25cab` |
| `reserved-permit-check.log` | `a855b3c5f70f8cab19bae7fe75831012c6b74396f5ebb0b0985cd31c75146cd9` |

## Public evidence and owner-instruction precedence

The public evidence `eva2/evidence/product/p1-native-control-20261003.json` matches the reviewed source hashes, retained failed checks and final bounded result. It explicitly leaves whole-task completion and live admission false, reports zero model/live provider calls, and preserves unknown credit cost and the streaming/stop limitations. Reviewed evidence SHA-256: `4c999d2079a60204335aaf51bcc838c930519d248ea7cb077ed14f1440654d5c`.

The new “Owner instruction precedence — 2 October 2026” section in `eva2/design/product/comparable-agent-benchmarks-v1.md` correctly allows a partial comparison among available, individually admitted participants. Missing participants/samples remain unavailable or unknown; their budgets are not reassigned and their absence is neither a loss nor a win. Each executed participant still requires equal task conditions, accounting and native admission. Full EV-003 thresholds and the complete superiority requirement remain unchanged. This clarification enables no run and changes no limit. Reviewed protocol SHA-256: `f12758d9156f4b23321ee5f0a7062a5d6fa47d067a5cd6a8d595a652f1bb7479`.

This bounded GO closes only the identified offline adapter seam. P1's remaining admission, calibrated final budgets and independent final dataset are not completed by this review. Earlier F1 reviews remain unchanged.
