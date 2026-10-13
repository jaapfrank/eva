# P1.1 native loop and SDK composition review — 1 October 2026

Independent bounded review against base `00266408` of the stream callback change, the explicit `native-loop` probe, five focused HTTPX tests and sanitized composition evidence. Retained v1–v4 execution-source records, safe failure/report records and their published artifact hashes were checked. Relevant pinned native model, tool-dispatch and stream-finalizer interfaces were read. No authentication, inference, tests or fixture replay were performed. Credentials, private OAuth configuration and challenges were not inspected. Only this review file was written.

## Disposition

**GO for publishing this bounded composition checkpoint.** No actionable blocker was found in the reviewed route. The evidence establishes that the native loop can consume the two admitted synthetic SDK responses through the real asynchronous SDK and native OAuth routes. It does not establish complete native/sample execution, live admission, participant parity, competitor quality or P1.1 completion.

## Binding and accounting

The native foreground and utility methods retain their original implementations, with the existing role/context wrapper applied around them. Before awaiting the real SDK entrypoint, the probe checks the actual AgentContext, model and effort, admits the attempt into the locked ledger and binds its current task. Rejected helper attempts raise `SampleStopped` before SDK execution. At the native upstream boundary, the serialized model/effort must match the single admitted owner; repeated upstream use of that attempt is rejected. Copying the caller context into each WSGI request preserves ownership, and the response extension carries that request's attempt rather than taking the last global upstream.

For the new native-loop case, mock upstream generation no longer finishes usage. The stream callback records the actual SDK-consumed `response.completed` event, while nonstream usage is read from the SDK-returned response. The callback captures its own invocation's attempt. Events pass through unchanged; missing or incoherent usage raises and closes the same owned response. The callback remains synchronous in this route. Existing cancellation, completed-byte preservation and failed-close checks remain relevant, and the two added tests cover consumption timing and invalid consumed usage. The retained five-test passing result is implementer evidence, not a reviewer rerun.

The ledger's `complete` flag means every admitted attempt has coherent consumed usage. In v4 it coexists with `declared sample attempts reached` and refused helpers. The explicit `sample_completion` field and public limitations correctly prevent that flag or the zero bound active count from being used as whole-sample completion or task-quiescence evidence.

## Tool boundary

The modern `_execute_tool_request` wrapper and legacy `process_tools` wrapper both check the native context and permit only the synthetic `response` protocol before reaching native tool construction. Both native routes subsequently use `tool_execute_before`; the shared hook counts that dispatch once and checks the declared dispatch envelope before execution. Positional and keyword extension calls are forwarded intact. The actual retained run exercises the legacy response protocol; coverage of the modern boundary here comes from source inspection, not a separate executed tool scenario. No general tool-coverage claim is justified.

## Retained outcome and limits

V4 returns the asserted native response, counts one response protocol dispatch and consumes one Sol/none foreground request plus one Astra/medium helper request: 42 input and 18 output synthetic tokens across two admitted attempts. Extra helpers are refused before entering the SDK. Foreground SSE closure is observed before cleanup; the mocked nonstream helper upstream remains open until explicit cleanup. The evidence states both facts and does not convert usage completion into upstream or cloud cancellation.

V1's legacy counting failure, v2's keyword-forwarding failure and v3's third helper SDK entry are retained rather than treated as successful admission. V2 retains zero SDK attempts. V1 lacks a full usage snapshot, so its two SDK route observations cannot supply a full cost total. V3's report contains the additional SDK entry even though its admitted ledger usage is complete. V4 moves admission ahead of that entry and retains refusal records. Unknown development credits remain unknown; synthetic usage is not Eva runtime consumption.

The result remains an in-process fixture with provider HTTP mocked and native source unchanged. No live provider response, synchronous SDK coverage, provider retry coverage, all-tool coverage, deployed proxy behavior or full native helper completion is proved. Private storage, local embeddings and existing settings are reused; the imported role wrapper and existing preset/selection/capability assets are unchanged from the stated base.

## Provenance

All published v1–v4 artifact hashes match their retained files. The public `observed` object exactly matches the retained v4 report. Current probe, ledger and tests match publication hashes, and v4's execution-source record matches the current probe and ledger. The native module hashes match the pinned sources reviewed.

- Executed/current probe: `4ae7c048a6186d25d1a618871e056f47e5e9d363f294d5fff9c7af11e8c61be1`.
- Executed/current ledger: `c57637e389204ba9e816f430da40db073e217bf969b9968cc74f791f4854986f`.
- Focused tests: `4184681559bd9660dec0895dc49a5a64cd7058b0e5b35b8a7daa09656b8ceca1`.
- Retained v4 report: `051544b17b4c2b196e01c87315b1e399b6a749e08652fefa2c6d29bce788ed87`.
- Retained v4 execution-source record: `710e948752e54b4ba0f6ad50e9f0ba4757a151b0c89643ce1cf86ead269f65c3`.
