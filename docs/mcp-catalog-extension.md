# Extending a configured MCP catalog

`EVA_MCP_GATEWAY_ADDITIONAL_CATALOG_FILE` optionally names one additional private runtime file. The original `EVA_MCP_GATEWAY_CATALOG_FILE` remains required. Both catalogs use the existing supported formats and retain their own contract versions and tool configuration hashes. This permits a v4 invocation alongside historical v1–v3 tools without rewriting historical receipts.

The combined catalog is limited to 128 tools. Duplicate Eva action names or provider execute names are rejected. The additional file uses the same runtime UID ownership, file permissions and bounded read validation as the primary catalog. It does not add credentials, principals or network authority. Admission still validates every pinned tool against the existing gateway before exposing it.

The optional catalog adds explicitly pinned tools from an already registered gateway. It does not register or duplicate a service. Invocation acknowledgement is not independent proof of filesystem state; use an existing inspection tool to check the outcome.

Changing the combined configured catalog changes its discovery ID. Rediscover tools before new calls; do not reuse cached tool IDs from the previous catalog. Existing durable requests continue to resolve by unchanged individual configuration hashes. Keep the original specs unchanged while their receipts remain in use.

For deployment, mount the additional file read-only with the runtime UID and private permissions, set the optional variable on both core and worker, and use the existing idle deployment and rollback procedure. Preserve the previous images/configuration and catalog files. Removing the added specification is unsuitable as a routine update if outstanding work needs its binding. Installation and actual tool execution are separate verification steps.

Checks: `tests/connectors/test_mcp_gateway_provider.py`, `test_mcp_invocation.py`, the configured-catalog registration case, and the digital-profile configuration case. These verify configuration and receipt behavior, not a completed project or directory creation.
