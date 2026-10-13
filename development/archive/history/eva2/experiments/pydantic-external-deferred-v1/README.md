# Pydantic external deferred boundary

This providerless experiment proves one narrow production seam using public
Pydantic AI 2.36.0 APIs:

1. `ExternalToolset` exposes runtime tool definitions to the model.
2. One model request returns `DeferredToolRequests` and stops.
3. Code outside Pydantic performs the durable action.
4. A second one-request phase receives `DeferredToolResults` and produces text
   with no tools available.
5. After a process crash, the required Pydantic message history can be rebuilt
   from the user prompt and Eva's durable call receipt.

There is no HTTP client, MCP client, socket or provider implementation in this
experiment. `FunctionModel` is a public providerless Pydantic test model.

## Production API mapping

| Experiment | Eva production contract |
|---|---|
| `ToolDefinition` | projection of `ConversationToolDescriptor` |
| `SelectedAction` | `ConversationActionCall` |
| external executor line | `ConversationActionPort.execute(turn, call)` |
| `DurableActionReceipt` | `ConversationActionReceipt` |
| `DeferredToolResults` | model-only projection of the durable receipt |

The production adapter must not use `MCPToolset` or an ordinary Pydantic
function tool for external actions. Those paths let the framework call a
provider inside its own loop. Only `ExternalToolset` provides the required
stop-before-I/O behavior.

## Run

Create an isolated Python 3.12 environment, install the exact dependency from
an approved wheelhouse, and run:

```bash
python -m unittest -v test_candidate.py
```

The experiment intentionally does not alter Eva's product dependency lock,
runtime bootstrap, image or provider configuration.

## Remaining production work

- Keep `UsageLimits(request_limit=1, tool_calls_limit=1)`, `retries=0` and
  provider `parallel_tool_calls=False` in both phases.
- Reject zero, multiple, approval-type or unavailable calls before the durable
  action port.
- Offer no toolset in the receipt-to-answer phase.
- Treat Pydantic message history as reconstructable transport state, never as
  the action ledger.
- Preserve Eva's credential rotation, total deadline, request/response byte
  limits, usage projection and no-retry-after-visible-output rules outside the
  framework.
