# P1 shared native sample — independent review, 3 October 2026

Decision: **GO for the bounded evidence of shared observed usage and stop enforcement. The native sample remains incomplete.** This is an isolated synthetic integration fixture, not a scored C01/C04 result, full live admission, semantic-quality result or P1 completion. No additional run is needed to establish this outcome. The reviewer inspected source, receipts, logs and dump hashes without executing tests, models, auth flows or the fixture.

The actual pinned core `create_runtime_app` and Hindsight `create_app` ran with native background work enabled, fresh separate databases, existing public model resources and synthetic provider/auth responses. One parent `ProviderSampleLedger` owned the unchanged two-attempt allowance and exact contact/helper profiles. Private Unix IPC bound one-use worker credentials, request identity and allowed roles; child ledgers remained mirrors. Late-permit stop handling now aborts the reserved event before returning a dispatchable attempt. EOF, failed close and missing acknowledgements cannot manufacture terminal settlement. The seven unique focused subprocess cases eventually passed; the initial BrokenPipe failure and later buffered-stdout test-readback failure remain retained.

Compatibility corrections were necessary: the provider uses `settings.model_name`; the pinned core has HTTPX2 and no ordinary HTTPX; its decoder yields byte chunks. Native SDK completion can close before HTTP EOF. The final observer settles an already validated terminal only after successful close and without observed invalid input, stop or cancellation. It neither drains nor validates an unread suffix. The seven pinned-core checks cover plain/fragmented gzip forwarding, natural early close, missing or observed duplicate terminal, failed close and cancellation. This supersedes the earlier EOF-only adapter behavior within this fixture; it does not prove remote EOF or detection of unseen duplicate tail events.

Attempt6 has exact first stop reason `declared sample attempts reached`. The parent records two completed dispatched attempts:

| Role | Model / effort | Synthetic input / output |
| --- | --- | --- |
| Contact | gpt-6-sol / none | 17 / 7 |
| Retain helper | gpt-6-astra / medium | 31 / 7 |
| Total known dispatched usage | Two attempts | 48 / 14 |

No third provider HTTP dispatch occurred. The native reply matches its independent journal identity and text hash. Native outbox retention completed and a world fact was persisted. Consolidation was `processing` before stop and `pending` afterwards; no observation was created. Thus `ledger.complete=true` means complete usage for dispatched attempts, not completion of the memory lifecycle or task.

Both native workers stopped their observed threads/tasks and responses; both control closes were acknowledged. The authoritative parent has zero unsettled events, no errors and valid terminals before and after close. Each child's post-close mirror nevertheless contains the generic diagnostic `parent disconnected; accounting unknown`. That diagnostic is retained; settlement is established by the acknowledged parent receipts and known rows, not by treating the child mirror as accounting authority. This is local controlled stopping, not cloud cancellation proof.

Final dumps were successful and their bytes/hashes independently match the receipt. Cleanup blockers and execution errors are empty; recorded removals of both fixture apps, database and dedicated network all exited zero after preservation. Launcher elapsed time was 47.437443603994325 seconds; foreground observation was 3.1856607740046456 seconds and sample observation 4.488307499006623 seconds. These offline synthetic timings are not provider latency or a speed advantage. Zero real upstream model calls were made.

The five preceding attempts remain distinct:

1. Shallow `/probe` mount caused import-time `parents[3]` failure; zero provider attempts. The mount was corrected without rewriting that module.
2. Core refused a mode-0644 fixture DSN; zero provider attempts. Modes were corrected to 0600 and the validated empty schema retained.
3. HTTPX2 decoder output was incompatible with byte-only observation; one synthetic contact dispatch aborted with unknown usage.
4. The reuse guard correctly refused the old memory bank created during pre-answer recall; zero provider attempts. Existing records were preserved.
5. Native reply succeeded, but EOF-only accounting marked its one contact dispatch unknown. Its historical `sample_envelope_refused` label is incorrect: this was an accounting failure, not a cap result. Its dumps and receipt remain unchanged. Fresh databases were necessary for attempt6 after the earlier cleanup.

Across those six fixture attempts, four synthetic HTTP dispatches occurred: two earlier usages remain unknown and only attempt6's two rows supply the known 48/14 total. Earlier unknown usage is not retrospectively zero or repaired by later success. No quota was raised, memory suppressed or failed attempt relabeled successful. This fixture does not prove that all Eva2 cases exceed the budget; complete native comparison admission and calibrated task execution remain open.

Bound SHA-256 values:

| Input / evidence | SHA-256 |
| --- | --- |
| `cp001_process_accounting.py` | `cf204745b51bb0eac27f25bed31b9a9054ef90d722ff30a6a222d5d5b2d19f72` |
| `test_cp001_process_accounting.py` | `6377a5e519b1fd3c24626fd9ef786495a8a0db34298115cbd2536b0b4cc21536` |
| `cp001_http_accounting.py` | `4d9b60a996b4fb221b5ad2cd77072ac36432b5349c3f0fbf96c81ba640ff8b85` |
| `p1_httpx2_accounting_probe.py` | `0bbb45198bdd3469fbfcd70689664e9dccf48bf1fa49c6296ccbb2ccfd566699` |
| `p1_eva2_native_worker.py` | `cfb518679e3efc3bf7eff4621ee3021db4b50dd94ff2c393b8bf460bee840451` |
| Private `run-v6.py` | `823156fca233ba9bb232fceef1cc78c3441db6c2f76048a5023b4fdfe45e3973` |
| Attempt6 result | `2583192e92c8118e8ba4ea1bdec9572b05cbeec79dfdea1242a7724f41877c12` |
| Attempt6 core result | `82a649960c682b230c72d474243194569f18472a5a93d21eb78ce0e2aabc4f4e` |
| Attempt6 memory result | `37173db55a458ad630c7ba1bd83c2d9d73bb14ed8b5c5f480a054ea763984ab4` |
| Attempt6 core dump, 279,664 bytes | `f2d41d3d92f9c3f188e62e8ac21e3e0a20a53471592b88ead2345272f92264f2` |
| Attempt6 memory dump, 79,027 bytes | `491e5d6907387878fc31273e370c1872f7f3134965aa495957fba1087bd298a0` |
| Launcher exit receipt | `f384ad2baeb239d7efb35bac3cf5f82cf067f5a43fd34cd378818a8af0b1e508` |
| Seven HTTPX2 checks result | `044d83e161ef55cc86a0b4ad70e992b763cbd93e2bb06a7e1c53d2ac6804f184` |

All seven source hashes in the attempt6 receipt match current files, including the reused ledger, native control and Hindsight helpers. Main receipts are under `.eva2-artifacts/p1-eva2-shared-sample-20261003-v1`; focused IPC and HTTPX2 receipts remain in their named sibling directories. Core image: `sha256:fd1c7cd3413405fd6b1b5ce408f2028cffed8de735a301550663e4c891ee5902`; Hindsight image: `sha256:20d72ad34164bd1a67906c4d61b57a51f2b255c49ddf8cb7ac5e901ebc3b3710`; database image: `sha256:7e641ed0cf38285bebfc6151ba48243439dfe9cf3306feab4023a812466cd6c1`. No production installation or private memory was changed.
