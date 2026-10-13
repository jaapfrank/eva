# Hindsight database recovery proof

Reviewer: `document_private_identity`, 23 September 2026. **GO for the bounded data proof.**

The same live read-only exported PostgreSQL snapshot feeds source fingerprints and pg_dump. Authenticated encrypted transport yields the exact original dump bytes. After restore to a network-isolated target, all 23 source tables / 22,316 counted rows have matching row counts and sorted JSONB content digests. This includes stored memory data; it does not repeat memory acceptance.

The source database was not changed. No Hindsight provider or model was started on the target. After this successful run the private staging copies, key and target container were removed. Only script and result remain in the private proof stage.

Not proven: full schema/sequence/large-object/privileges equality, extra target tables, provider API operation, coordinated core snapshot or activation. Repository transport code ran under the current core runtime image. The one-off script's cleanup is not exception-resilient: a failure during cleanup could skip later cleanup steps, so this is not a generally releasable backup operator.

[Evidence](../evidence/product/hindsight-database-recovery-20260923.json) binds executed script and result hashes. Integrate a coordinated, failure-safe backup/restore route before product installation claims.
