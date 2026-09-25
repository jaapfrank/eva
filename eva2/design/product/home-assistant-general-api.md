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
credentials and effect-principal bindings authorize owner turns and both registered
household speakers. On 25 September the owner explicitly corrected the earlier
lamp/switch-only interpretation: general device and automation management is
authorized through both speakers. Their household identity stays separate from
private conversation and memory.
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
identical provider configuration. This historical installation only enabled private routes. The 25 September
owner correction additionally authorizes registered household speakers. See ha-owner-channel-parity-installed-20260924.json.


## Complete configuration batches — 24 September 2026

The existing `read_home_assistant_inventory` action offers the authorized
`automation_configurations` section for up to eight selected inventory IDs.
It reuses the existing concurrent reader and returns complete configuration JSON
as text, retaining template conditions, service data and decimal values. The
older projected `automation_details` view remains available. Full content is
limited to 40,000 characters per batch; omitted content is explicitly marked
incomplete. Both registered household and owner audiences can select this full view.

A real owner task used the automation index and one complete batch, compared four
configurations and saved a supported recommendation within the existing budget.
This verifies one read-only configuration-analysis workflow, not physical effects,
automation editing or broad home-control acceptance. See
`eva2/evidence/product/full-ha-configurations-installed-20260924.json`.


## Background configuration readback — 24 September 2026

A successful REST POST/PUT/PATCH to an automation configuration resource now
leaves a readback requirement in the digital worker. A later complete GET of
the same resource, or the existing complete configuration batch for that exact
automation ID, satisfies it. Another write requires another read. Partial
responses and a saved report do not replace that readback.

This verifies readback occurred; it does not prove semantic equality, physical
execution, or other Home Assistant endpoint protocols. The model must still
compare requested fields with the returned configuration and report limitations.
The existing generic API and all device-independent planning remain unchanged.

The installed 24 September trial created an inert, initially disabled automation,
read it back, changed only its description, and read it back again. The first
attempt reached its budget boundary before delivering a result. One explicit
resume reused all write receipts, performed one inventory read, and returned a
verified-against-evidence report. The coordinator removed the temporary object
afterward. This is a successful resumed workflow, not single-attempt reliability
or owner acceptance of broad household automation behavior. See
`eva2/evidence/product/ha-configuration-readback-installed-20260924.json`.


## Household background work — 25 September 2026

The registered speakers can now admit longer HA work using the existing
`start_digital_work` action. A distinct household envelope preserves the device
actor, original ingress and household audience. It reuses the digital worker,
model accounting, HA provider, progress, cancellation and resume receipts.
The household grant selects HA API/inventory and progress tools; it grants no
private connector, browser, document or personal-memory access.

The foreground remains available while the durable job runs. Existing household
status, stop/resume and result delivery handle both public research and HA jobs.
Resuming preserves the original execution identity and verified tool receipts.
Stored household results may retain their historical program hash for reading;
new execution still requires the installed program and current authority.

Installed source: `19958b28`. The packaged PostgreSQL lifecycle test covers a
controlled HA write, another foreground turn, stop, resume without repeating the
write, readback and one return to the originating session. The installed core and
worker load both registered speaker bindings and equal execution profiles.
Physical speech/model quality and owner acceptance remain separate. See
`eva2/evidence/product/household-digital-installed-20260925.json`.
