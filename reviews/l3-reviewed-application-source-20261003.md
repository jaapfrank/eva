# Reviewed component application source review — 3 October 2026

**Source GO for the recorded source hashes below; installed verification pending.** The bounded application bridge may be installed and exercised through the existing Eva task route. No actionable source finding remains after the status-read correction.

## Reviewed behavior

The model can select only a configured, independently accepted review ID. The adapter derives the request and originating task identifiers, enforces the configured tenant/installation/person scope, and uses the existing authenticated host connection. The model cannot supply source content, commands, file paths, task identities, images, new privileges or its own approval.

The host checks the exact package, accepted review and its evidence, source-import receipt, build evidence, baseline compose and candidate compose. Both core and delegated-worker images are immutable and tied to the reviewed source. The existing image-only updater continues to reject configuration, mount, inventory and data-service changes.

Application requests are durably recorded before dispatch. One host dispatcher owns the receipt directory, and a canonical accepted-review identity prevents replay through a different request ID, task or reformatted alias. A restart before dispatch requires a matching explicit request; uncertain dispatch is preserved and never automatically rerun. Only the existing updater's exact project/image success receipt confirms completion.

Readiness uses the real originating task record: successful completion, matching actor scope, no stop request, and no currently active lease or advisory lock. The check is a database snapshot, **not an atomic admission barrier**. Work may start between that check and Compose; the existing graceful shutdown and retained storage handle that interval. The implementation does not claim global atomic quiescence.

## Finding resolved during review

The initial bridge exposed only application requests. A later Eva task could not read completion or reconcile an uncertain prior request because it received a new request ID and the host correctly rejected redispatch.

The added `get_reviewed_component_application(review_id)` action now uses the same actor-scope guard and authenticated transport to call the host's read-only registered-review status route. It exposes bounded receipt state and can reconcile an already-written exact updater receipt without requesting, resuming or repeating an update. The new tests cover later-task reads, preserved uncertainty, exact-receipt reconciliation and actor separation.

## Evidence reused

- The original joint run recorded 195 passing checks and one failing test expectation: the production schema rejected extra model-supplied fields with `ConversationConflict`, while the test incorrectly expected `ValueError`. That expectation was corrected.
- The final focused run recorded **72 passed in 4.58 seconds**, including the revised host, transport and existing application-related checks. The host suite now has 32 cases and the transport suite 12.
- The existing 33 importer checks and 17/17 baseline/candidate component checks remain separate valid evidence.
- Existing host preflight evidence reports successful real imports, configuration loading and artifact binding without an application request.
- The real current-core readiness preflight returned `ready` for an already completed task without invoking an update.
- I inspected the changed source, final status tests, preserved updater and the actual recorded logs. I did not rerun tests, models or protected-case comparisons.

## Exact scope

The frozen context candidate remains SHA-256 `2554605179fcdff8b2b8793b8dbbdecaffdeddaafc31c65063f19e6e9269beb7`, with baseline `99954a459bb14c3411613d165d981887746890749df1e51c2dd50f0a21acf34e` and canonical accepted package `503e263aa1c01ca0bc5aa443990c98a61c94fa29ae4c9f6d637135ccf3b9d5ab`.

This bridge applies already accepted and prebuilt artifacts; it does not approve or build them. Human-written integration remains distinct from the Eva-authored context improvement and any later demonstrated Eva request/status workflow. No claim of recursive self-improvement or complete autonomous release preparation follows from this source review.

The previously signed [component application review](l3-context-application-20261003.md) is unchanged. Its file SHA-256 remains `2e18519f3fdbf23fa9665749b0e2bacdd86bf9feab8ea9b311dbb6eab80acab6`.

After installation, verify the actual authenticated host configuration, matching core/worker capabilities, exact installed source/images, retained mounts, the originating Eva request and a later read of the updater's real completion state. This document is not installed-result evidence.

## Source snapshot

| File | SHA-256 |
| --- | --- |
| eva2/src/eva/adapters/connectors/component_evaluation.py | d0e477a842db6a225a7826584709bbc19fe55707639f930e0959a1a0f7a63090 |
| eva2/src/eva/adapters/postgres/component_improvement_actions.py | 36d979fe0daa1ce1ef400bbeae8e19f591f934d760f3ce242ac1ffbd4e879b8f |
| eva2/src/eva/bootstrap/delegated_digital_runner.py | 007847fc48847bbe4f988de0f2e02648c1222b7501e648c1a4d621156af81899 |
| eva2/src/eva/operations/component_evaluation_service.py | 025436c8d94ef2b0d32899c82637fbb1f2931f935367c62c9f151f284259e855 |
| eva2/src/eva/operations/reviewed_component_application.py | 8de1b257595248a2d2fa703f4667d0b9febec5a5bf52325170541faa68efafae |
| eva2/src/eva/operations/component_application_runtime.py | fa33406d5fe4c702d1aa94531569361b99a40b79586435ca837ca9cb5c876871 |
| eva2/tools/installation_runtime_update.py | 86a6476ad3e6eb53f91ec5c0bcf40f542897c0510c4c03a1bed018b6650667f7 |
| eva2/tests/operations/test_reviewed_component_application.py | eed1a13c18e1614e5deaa957bdcb4f49c6a98c6095d1e0f8d0108c0bfb630178 |
| eva2/tests/operations/test_reviewed_component_transport.py | 5b3b620b08398cbb18cce9a03e362efeec8afdb879a2a0da0c0ccf7fbc175eae |

## Inspected evidence hashes

| File | SHA-256 |
| --- | --- |
| application/host-preflight.log | 8010a0240e6d8871ff68257b0126b3079305cb91a89ea77ca93001538ac2607b |
| application/readiness-live-preflight.log | ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b |
| application/application-focused-tests.log | 50ba224d7434a32ab40ca9ac8677860120735c11202c81d852b62ace27d10429 |
| application/application-after-review-tests.log | f8d34d5e5401f9c5b06e859cede8644880b85c53ced3b3c671dbd978104b189c |
