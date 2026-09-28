# Deferred function descriptions through provider namespaces

Baseline: 8254f4a2. Scope: conversation provider transport, not a new tool router.
Official source checked 28 September 2026:
https://developers.openai.com/api/docs/guides/tools-tool-search

Flat deferred functions expose their descriptions initially. Namespaces can defer those descriptions.
One function per namespace keeps the existing function-name namespace used by receipt replay.
The complete description, strict schema and authorized tool membership stay unchanged.
Only the short search label is derived from the existing function name; semantic discovery outside the measured intent is not proven.

The initial prototype lowers greeting input from 6057 to 2146 but the discovery request fails.
Pydantic 2.37 recognizes only flat FunctionTool entries in native search output.
The revised prototype registers authorized external tools locally and defers their loading at the owned provider boundary.
It returns list_own_initiatives in one request, without executing it.
Source implementation opts in only for the owned conversation provider; other provider clients remain unchanged.

Initial limit was two real requests. One additional changed-cause discovery request tested local registration.
Two preliminary invocation errors occurred before any model request (unsupported import/Agent argument).
The failed initial discovery request did not retain usage; its token/credit cost remains unknown.
Do not rerun the unchanged cases. Raw results live in the linked product evidence.
