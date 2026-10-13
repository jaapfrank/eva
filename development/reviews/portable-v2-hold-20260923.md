# Preserve open work behind a database restore hold

Reviewer: `document_private_identity`, 23 September 2026. **Source GO for preserve-and-hold only. No release or installation approval.**

Every v2 restore creates target-local activation state in the same transaction as imported records and rebuilt checkpoints. The archive cannot import release permission. Core, research worker and durable-goal worker check the hold before work starts. Normal runtime roles have read-only access. Early target validation refuses a pre-existing hold before CAS materialization.

Removing terminal-only requirements is appropriate only in combination with this mandatory hold. Task state, lease and delivery history are preserved. Existing source, identity and integrity checks remain. The reviewed test invokes actual process composition entrypoints against restored PostgreSQL without running a model. Later assertions were strengthened by the coordinator to compare the complete original and restored work values and passed.

The guard is database-wide and applies to a stopped, isolated target followed by controlled process start. It does not pause existing processes. All core and worker packages must include the guard. Safe release must subsequently bind archive, canonical/runtime identity, database lineage and reconciled external dependencies through the existing operator authority; it has not been implemented. Old private voice sessions and uncertain calls need explicit handling before release.

[Evidence](../evidence/product/portable-v2-hold-20260923.json) records all attempts and current hashes. Native documents/CAS, Hindsight, PostgreSQL18/package proofs, full installation and owner acceptance remain open.
