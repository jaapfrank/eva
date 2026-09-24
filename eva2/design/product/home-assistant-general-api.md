# General Home Assistant capability

Owner clarification: 24 September 2026. The existing narrow light and automation
planners do not meet the requested general HA capability. This delivery adds one
configured API transport, not another device-specific planner.

`home_assistant_request` supports REST requests to the host-configured `/api/`
origin and single authenticated WebSocket commands through the existing HA admin
session. The model discovers live services, states and registries and supplies
request data. Full automation JSON is supported through HA's existing configuration
endpoint; no fixed trigger/action vocabulary is imposed. The existing API owns
schema validation and operation behavior. Discovery and readback are part of the
model's task, not a claim that the transport understands every integration.

The provider is opt-in with `EVA_HOME_ASSISTANT_API_ENABLED=true`. Existing host
credentials and effect-principal bindings authorize private owner turns. Shared
speaker access remains unchanged until the owner decides its broader scope.
The digital composition includes the same provider and exact general descriptor;
its worker must receive the same HA settings and credential mount. No second
executor or provider credential is introduced.

Requests use existing durable external-tool admission and invocation receipts.
Missing dispatch receipts are never replayed automatically. HTTP/API rejection
is failed work, not verified success. Successful API response verification does
not prove physical state: the model must read changes back separately. Responses
have bounded transport size and generic JSON Pointer/pagination; incomplete
previews are labeled. Do not replay a write merely to change response selection.

For ordinary device control, discover the actual service and target, invoke it,
and read the resulting state. For automation changes, read the existing full
configuration, preserve unrelated behavior, save the requested change, and read
it back. Long work uses the existing background task route so conversation can
continue. Live model quality and physical actuation require separate acceptance.

Protocol references checked 24 September 2026:
- [Official REST API](https://developers.home-assistant.io/docs/api/rest/)
- [Official WebSocket API](https://developers.home-assistant.io/docs/api/websocket/)
- [Official MCP server](https://www.home-assistant.io/integrations/mcp_server/)

The built-in Assist MCP tools expose their configured capabilities; their presence
alone does not establish arbitrary automation administration. This transport uses
the already configured HA API directly for that gap.

Installed owner-route parity on 24 September: both the server-bound local owner
identity and its verified Telegram bridge are admitted. Core and worker retain
identical provider configuration. This does not grant shared speakers general
API administration. See ha-owner-channel-parity-installed-20260924.json.
