# Recovery integration checks

Reviewer: `document_private_identity`, 23 September 2026. **Source GO for the bounded writer guard; PG18.6 provenance accepted.**

Eight current-work recovery cases passed on the exact production PostgreSQL image with version 180006 explicitly asserted. The disposable target used no network and was removed. This is source-overlay compatibility evidence, not a package or installed-release test. Initial readiness reached the temporary init server; TCP readiness corrected the harness before the final run.

Snapshot and restore now recheck core, delegated worker, Hindsight and the existing exact-DSN-mount shared-consumer inventory before database I/O. Nine host-operator tests cover refusal for active writers and acceptance when relevant writers are stopped. Live read-only inventory confirms the standalone document worker shares the active core's exact DSN mount.

The guard is not atomic pause/fencing and cannot discover non-Docker processes or equivalent credentials mounted from another path. Explicit shutdown and before/after database attestations remain necessary. No live cutover or installation occurred.

[Evidence](../evidence/product/portable-recovery-integration-20260923.json) contains command, output provenance, hashes and failed-harness history.
