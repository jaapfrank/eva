# Current dashboard summary — independent review, 3 October 2026

**Decision: SOURCE GO for the corrected presentation change and seven explicit current summaries.** This is a narrow review of summary precedence, historical retention and source-backed wording, not a new dashboard audit. Live reload/readback remains the controller's delivery step.

The initial review found one P2: `applyMemoryPresentation`, called by `applyProductCompletion`, replaced the newly supplied memory text while retaining a current date. This is resolved. `applyCurrentCapabilitySummaries` now runs after the complete existing completion/memory projection, then updates only `product.delivery.result` and `followup` when the corresponding current `works` or `gap` is explicitly supplied. Earlier memory narrative is retained in `historicalSummary`.

The final helper changes narrative fields only. Original text, date and evidence remain in `historicalSummary`; current source paths supplement existing evidence. Malformed/unmarked summaries do not opt in. Existing calculated completion, steps, technical states, acceptance and stage notes are not changed by the helper. All seven current source references exist, and the wording distinguishes limited technical outcomes from remaining owner, cloud and family acceptance. The board diff changes no task status, finish condition, acceptance or criterion. Its small-task count remains 23/44; this presentation correction grants no new completion.

The new memory regression uses the real `applyProductCompletion`/`applyMemoryPresentation` path. It checks that current text survives, historic text is retained, and the whole product object is identical except the two delivery strings, with acceptance and stage notes unchanged. The executor reports three focused tests passing in 80 ms after the correction; this reviewer inspected the test and did not rerun it. No service reload, model/provider call or product edit was performed by this review. No remaining issue was found within this scope.

Exact final bytes, relative to `eva2/`:

| File | SHA256 |
| --- | --- |
| `experiments/react-admin-dashboard-v1/project-server.mjs` | `03d7c489a65a0b4519318a5b36daf16505799d513cdc9bed67f895934646a203` |
| `experiments/react-admin-dashboard-v1/current-summary.test.mjs` | `cc00b66bdcea42a8e439d743b2534f27bcae9b2b9f3dc0bddda3729323360c7b` |

Initial HOLD bytes were `abb743d5ed8740f4daf374f8898f52746fab9ebe73a3c3dc7da3e98fd6fa9d6c` and `293939494fc30cdfc178053c70e3ef31faf172279a6fde6358deebcd7541fd4b`, respectively; that precedence finding is retained above as resolved.
