# Refreshing an admitted connector catalog

The existing `connect_service` tool accepts an optional boolean `refresh`.
For a connected service whose gateway catalog changed, use `refresh: true`
with the same endpoint and the documented provenance fields. Then call
`discover_connector_tools` to obtain the current tool identifiers and continue
through `use_connector_tool`.

This reads the current ContextForge catalog; it does not force the upstream
server to rescan. An absent or revoked connector is not created or reactivated.
The existing owner mandate still decides which discovered tools are admitted.

Registration remains immutable by default. Refresh appends a chained generation.
New execution uses the current generation; observation can resolve retained
historical configurations. Existing tasks referring to replaced tools may need
fresh planning rather than replaying the old action.

The installed rollback image retains the generation-aware registry reader while
restoring the previous registration adapter. Do not roll back to a reader that
cannot interpret refreshed events. No data deletion is part of rollback.

Installation evidence: [24 September installation](evidence/product/connector-refresh-installed-20260924.json).
A real changed-service task trial remains deferred; startup is not that proof.
