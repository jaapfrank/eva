# Native memory payload lifecycle

Provider addendum for the installed Hindsight **0.9.1** PostgreSQL implementation.
It compacts duplicate execution input after a retain parent and every sibling are
completed, with explicit zero extraction errors on every child. It does not delete operations, results, errors, documents, memory units,
original Eva sources, or pending/retry input. No new process or model call.

Run `python deploy/living-core/hindsight-memory/install_completed_payload_compaction.py
--container <existing-native-container>` when provisioning this provider database.

`completed_payload_compaction.sql` is idempotent. The installer validates the native
version and schema, then applies it in one database transaction. The trigger lives
in that provider database and survives container/image restarts. Apply the same
addendum when provisioning a fresh Hindsight database; revalidate retry/replay and
payload consumers before changing the pinned native version. Do not enable native
operation-row GC as a replacement: that removes idempotency and result evidence.

Only explicitly marked Eva direct-user conversation items are eligible. Tiny
contents remain inline because a SHA256 replacement would use more storage.
Unknown formats, other providers, unsuccessful families and all other metadata stay
unchanged. `content_sha256` explicitly distinguishes compacted input in optional
native operation diagnostics. Normal replay and status read their original result
metadata. The authoritative source is compacted separately, only after Eva's own
source-bound dream acknowledgement. Native duplicate cleanup never supplies that
acknowledgement and cannot make original source deletion eligible by itself.

Disable automatic compaction with `DROP TRIGGER
 eva_compact_closed_memory_family ON <native_schema>.async_operations;` while
retaining the pure helper/function and existing receipts. No erased text is
reconstructed. Historical cleanup requires its separately reviewed source proof;
this addendum does not sweep old rows when installed.
