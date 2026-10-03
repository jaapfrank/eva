# P1 Hindsight HTTP accounting — independent review, 3 October 2026

Decision: **GO for the bounded native HTTP accounting adapter and its offline evidence.** No blocking finding remains in the reviewed change. P1 remains open: this does not admit a full sample, prove the complete memory lifecycle, or authorize live comparison. The reviewer inspected source, hashes and existing receipts without executing tests, models, auth flows or provider calls.

The adapter reuses `ProviderSampleLedger` and `SampleAsyncStream`. Each bound HTTP attempt checks the exact endpoint, native model and effort before dispatch, including native retries. The probe imports the actual Hindsight `CodexLLM`; it injects only synthetic authentication and a parent-owned HTTP client. Three existing pure Eva attestation definitions are extracted from a SHA-256-bound source AST. Their implementation is unchanged; no substitute Hindsight package or module is used. The pinned native image and existing read-only dependency mount supply the actual Python environment.

Observation decodes a separate copy of compressed content, while the original request body, response chunks and headers continue through HTTPX. Settlement requires the unique valid terminal usage, EOF and successful owned response close. Missing or duplicate terminal usage stops the sample and remains unknown. Cancellation after a terminal event but before EOF cannot become completed accounting. Sample-owned cancellation leaves another sample's active call untouched.

Review found and resolved three related close-failure edges before the successful probe: a failed response close now rejects further admission while retaining pending/unknown ownership; transport close attempts every owned body and its inner transport before re-raising; setup failure stops admission before cleanup that may itself fail. Failed closes remain visibly unsettled instead of becoming false quiescence. The focused EOF-close and multiple-owned-resource checks cover the principal corrections.

The retained v3 receipt reports **9 checks passed, zero failures/errors, 0.19060972801526077 seconds**, with HTTPX 0.28.1 and zero upstream model calls. The real native JSON parser retries once after synthetic malformed JSON. Both `gpt-6-astra`/`medium` attempts are recorded: 62 input / 14 output / 76 total fixture tokens. The native char/4 estimate, 6 input / 8 output, stays separate. Missing usage at HTTP 200 and 503 blocks another dispatch; wrong profile rejects before dispatch; fragmented gzip preserves wire bytes; duplicate terminal, cancellation and failed-close states remain incomplete. Current source hashes match the executed receipt.

Preparation history remains visible. V1 failed importing HTTPX because the installed dependency mount was absent; no checks ran. V2 included that mount but could not inspect the root-owned output path as its non-root user; no checks ran. Its blocked optional LiteLLM cost-map lookup is retained in stderr. V3 used fixture-only UID 0, the packaged local cost map, `--network none`, a read-only root filesystem, dropped capabilities and temporary scratch space. Source/dependencies stayed read-only; only the report directory was writable. No real auth cache or database was mounted. The successful nine-check suite was not a repeat of failed test assertions.

This establishes accounting only for the explicitly bound client. It does not prove that every retain, reflect, consolidation, helper or deferred task is bound; full sample ownership, admission and local quiescence still require their actual integration. Comparison-side stopping is assistance, not proof of native/cloud cancellation. Synthetic usage and test duration are not billed consumption, provider latency or answer quality. Existing model roles and limits remain unchanged; no new budget or ledger was introduced.

Bound SHA-256 values:

| Input | SHA-256 |
| --- | --- |
| `cp001_http_accounting.py` | `906ea1bf9ee43ee5892aeb2c1d609447bbaa73698c8dd514cc4269a5bb3c5ff9` |
| `p1_hindsight_accounting_probe.py` | `512766ab2dd0e0804062acdbb3b1c962613503b6266b8d78b7326b63762aed1e` |
| Reused `cp001_accounting.py` | `c57637e389204ba9e816f430da40db073e217bf969b9968cc74f791f4854986f` |
| Eva attestor source | `efdaf9e083ef15ee77b16a1015b7cfb1ac5cd82538e47840404fa5a290ca843f` |
| Native Hindsight provider | `b4cda4b56b3a6cc1f6bca64eaca7cbc7c75b5e8420d6bb77750c0d6304098afe` |
| V3 `result.json` | `99a7f091cf2e1c52ef20c4e82249a29cfcee212d9eed513e67c72680032005cd` |
| V3 `launch.json` | `e6a1277a069767550203b421bf761274a2858ea621beaea644cbd0e62c400b5b` |
| V3 `stderr.log` | `78f639b85ba5d1c7cd0d1d275e711ec5deca7edfc87013b511fceadbe4da244a` |
| V1 `exit.json` | `00f69e5c8d6320400fdca88b305328c37ba5af6e2f383b4f529e307edaa3e6b7` |
| V2 `exit.json` | `b9954e90db1c7f34eb194a3aedd9ce705142497d4328b755aeece0d9a42dc8f9` |

Receipts are retained under `.eva2-artifacts/p1-hindsight-accounting-20261003-v1`, `-v2` and `-v3`. The image is `sha256:20d72ad34164bd1a67906c4d61b57a51f2b255c49ddf8cb7ac5e901ebc3b3710`. No additional executions are needed for this bounded decision.
