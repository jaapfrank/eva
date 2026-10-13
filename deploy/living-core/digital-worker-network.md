# Digital worker connector transport

Digital background work reuses the core's configured MCP gateway. Its worker must also join the existing `mcp-gateway` network. The core-only `mcp-gateway.compose.yml` does not attach the worker.

Apply `digital-worker-network.compose.yml` after the base and gateway overlays when enabling digital work. Preserve this binding in the resolved active core/worker compose manifest used for subsequent image updates. This adds no ports, credentials, provider grants or external network; existing scoped gateway authorization still applies.

Before claiming a functional delivery, inspect the actual worker networks and verify gateway reachability from the worker. A configured catalog or healthy worker process does not prove connector reachability. On 23 September 2026 the catalog-only trial passed while real file execution failed because the worker lacked this network.
