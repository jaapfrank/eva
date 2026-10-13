# L3 context application source review — 3 October 2026

**Source GO; installation verification pending.** No actionable issue was found in the reviewed importer extension or its application to the exact frozen candidate. This approves the bounded source import and the prepared image overlay route.

## Exact reviewed inputs

- Baseline source SHA-256: `99954a459bb14c3411613d165d981887746890749df1e51c2dd50f0a21acf34e`.
- Candidate source SHA-256: `2554605179fcdff8b2b8793b8dbbdecaffdeddaafc31c65063f19e6e9269beb7`.
- Candidate package canonical binding: `503e263aa1c01ca0bc5aa443990c98a61c94fa29ae4c9f6d637135ccf3b9d5ab`.
- Candidate package file SHA-256: `e9ac24e36882fa67a446c984ba51d91ad1830b49e3be0790cf72828bad7ae752`.
- [Importer](../tools/import_component_release.py) SHA-256: `d0587d00997a1216bae4f777226858a54d92155ea3b3258016c4f7ae5e2388a5`.
- [Focused importer tests](../tests/operations/test_import_component_release.py) SHA-256: `ab264695d7ca236b3305ce3b2296615d6ca3152b459a87023c11eebffb53e34e`.

## Source and package findings

The extension admits `adapters/postgres/delegated_work_actions.py` up to 65,536 bytes because the existing baseline already exceeds the legacy 32,768-byte component limit. The three previously admitted paths retain their existing limit.

The new scope check uses AST UTF-8 byte offsets and requires exactly one target class and one synchronous `active_context` method. Comparing the exact bytes before and after the body preserves the method signature, decorators, imports outside the body, other methods and surrounding module text. I ran this pure scope check against the frozen baseline and candidate; it accepts the intended change.

The existing checks for the canonical package binding, exact candidate content, evaluation identity and outcome, operator acceptance, hash-bound evidence, clean `codex/` release checkout, baseline identity and exact patch remain in place. I independently recomputed these package and evaluation bindings, verified the candidate content is byte-identical to the frozen file, and reconstructed the same patch. The source importer does not execute candidate code or modify running services.

The actual `EvaIsolatedComponentEvaluation/v1` report matches the package and binds both source hashes. Its pinned evaluation image is `sha256:628780e3737f976c29eb988ebbbaf8edd9a9231d4999d266fa0b5d50b8e626d5`; both legs exited successfully with 17 existing active-context/initiative checks passed, no timeout and no truncated output. This report records `semantic_improvement_verified: false`; measured benefit is established separately by the [independent assessment](l3-context-independent-20261003.md), not inferred from equal test counts.

The implementing agent reported 33 focused importer checks passed. I reviewed their cases for size boundaries, body-only scope, duplicate/asynchronous target rejection, UTF-8/CRLF handling and existing binding failures. I reused that result and the actual 17/17 component report; no additional tests, model calls or holdout comparisons were run during this source review.

## Prepared application route

The release checkout was clean on `codex/l3-reviewed-context-20261003` and contained the exact frozen baseline at review time. Read-only checks of the running core and delegated worker also returned the same baseline component hash.

The reviewed `application/build.py` requires a source-import receipt, checks the imported candidate hash, confirms the current pinned baseline images, and overlays only this component into the core and delegated-worker images. Its baseline core is `sha256:901222dc4f1276775084e6725dea269075fa6e5d0d40f3ea601fd1dc25c6288c`; baseline worker is `sha256:35f44f6eb1a8bc972a34b8b4af263284987cc20706625280996c7d86f4951d32`. The previous compose reference is retained as the existing return path. This avoids including unrelated working-tree changes in the runtime image.

The existing image-only managed updater and resulting installed source hashes remain to be verified after application. This review is **pending installed verification**, not evidence that installation has happened.

## Scope and limits

The candidate passes the fixed independent promotion rule documented in [aggregate evidence](../evidence/product/l3-context-independent-20261003.json). Its measured benefit is limited to authorized task-context selection and the sampled status answers. No protected case text or answers are published here.

Operator source import, packaging and installation are assisted steps. Their success would not demonstrate autonomous application, recursive self-improvement, household acceptance or automatic completion of L3.3. Recheck this approval if the reviewed importer, tests, package, candidate or pinned application inputs change.
