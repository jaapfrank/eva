# Additional system candidates and building blocks

Research date: 27 September 2026. Official documentation and current repository branches; these may be ahead of releases. This is a fit assessment, not an installation, benchmark or assertion that every described API exists in Eva's installed versions. The five primary platforms have separate release pins in platforms.md.

## Letta: another complete candidate

The former Letta V1 API server is retired. Current development is in letta-code: a stateful harness combining persistent agent identity, memory, skills, scheduling and channels, under Apache-2.0. It belongs in the system comparison rather than being treated only as a memory library. [Old repository](https://github.com/letta-ai/letta), [current repository](https://github.com/letta-ai/letta-code), [license](https://github.com/letta-ai/letta-code/blob/main/LICENSE).

The SDK documents local/self-hosted use without requiring an account for the local backend. However, the channel guide still calls that backend experimental. It routes messages by agent/conversation, supports cancellation and separates inbound processing from user-visible delivery. These contracts do not establish household maturity, Voice PE compatibility or family privacy. [SDK](https://docs.letta.com/agent-sdk), [channels](https://github.com/letta-ai/letta-code/blob/main/src/channels/README.md).

**Judgment:** a relevant whole-system challenger, with local maturity as a specific uncertainty. An old comparison of the retired server cannot settle its current suitability.

## Execution

| Candidate | Upstream supplies | Consequence for Eva |
|---|---|---|
| Pydantic AI | Typed agents/tools, deferred external work, durable backends and on-demand toolsets. | Already installed as 2.37.0; using it does not mean upstream owns Eva's loop. Current Eva still deliberately invokes one step and owns continuation. [Deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/), [durability](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/), [on-demand](https://pydantic.dev/docs/ai/capabilities/on-demand/). |
| Deep Agents / LangGraph | Packaged harness with context handling, filesystem, skills/subagents; checkpoints and cross-thread stores. Deep Agents is MIT. | Credible generic-execution reuse; household product remains application work. Checkpoints do not verify external effects or enforce family boundaries. [Harness](https://docs.langchain.com/oss/python/deepagents/overview), [repository](https://github.com/langchain-ai/deepagents), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence). |
| OpenHands SDK | Agent loop, conversation lifecycle, tools/MCP, skills, events, context condensation, local/container/remote workspaces. | Possible bounded digital-work executor; no reviewed proof of complete household channels or personality. [Architecture](https://docs.openhands.dev/sdk/arch/overview). |
| DBOS | Background workflows and recorded steps; configurable retries and recovery. Outside a workflow a step is not durable. | Only use if the selected execution owner needs it. Existing Eva fork/upgrade decisions need explicit compatibility review. [Workflow](https://docs.dbos.dev/python/tutorials/workflow-tutorial), [steps](https://docs.dbos.dev/python/tutorials/step-tutorial). |
| Temporal | Workflow event history, deterministic replay, workers and a service. | Strong workflow model but added operation; not an agent model or proof of exactly-once external effects. [Workflow](https://docs.temporal.io/workflow-execution). |

Pydantic's backend contract itself warns that an effect may happen before recorded completion and thus be repeated after recovery. Define idempotency and ambiguous-result behavior once. This is not an argument for rebuilding every platform service. [Backend contract](https://pydantic.dev/docs/ai/capabilities/durable_execution/backends/).

**Design inference:** one authority per task lifecycle. Avoid Eva's manual runner, a full agent platform and a separate workflow planner all deciding the same retries and completion. A coding worker can own its internal session behind a clear parent-job boundary.

## Voice

| Candidate | Documented | Still unproven for Eva |
|---|---|---|
| LiveKit Agents | Turn/interruption handling and asynchronous tools while conversation continues. AsyncToolset preserves pending tools across handoffs; AgentTask structures conversations. | Crash-durable household work, first audio through installed HA/Voice PE. Session tasks are not automatically persistent background jobs. [Sessions](https://docs.livekit.io/agents/logic/sessions/), [async tools](https://docs.livekit.io/agents/logic/tools/async/), [tasks](https://docs.livekit.io/agents/logic/tasks/). |
| Pipecat | Function calls can survive interruption when configured, return into ongoing context and group parallel results. | Faster/better behavior than working LiveKit or native operation on both speakers. [Function calling](https://docs.pipecat.ai/pipecat/learn/function-calling). |

LiveKit also documents speculative generation and a default tool-step limit. Earlier response can cost discarded inference; upstream execution also has bounds. Preserve working LiveKit unless a challenger demonstrates a concrete advantage. Measure acknowledgment, substantive reply and interruption separately; a browser result does not prove physical speaker timing.

## Memory

| Candidate | Role | Limit |
|---|---|---|
| Hindsight | MIT retain/recall/reflect service; accepted Eva integration. | Recall and model-based reflection are different operations. Memory banks are not verified speaker identity. [Repository](https://github.com/vectorize-io/hindsight), [docs](https://hindsight.vectorize.io/), [recall/reflect](https://hindsight.vectorize.io/blog/2026/07/24/recall-vs-reflect). |
| Honcho | AGPL-3.0 person/conversation representations, background reasoning, workspaces/peers/sessions, self-hosting. | Another reasoning/storage path if stacked with Hindsight; publisher performance claims are not Eva measurements. Distribution-license suitability needs a concrete decision. [Repository/license](https://github.com/plastic-labs/honcho). |
| Platform memory | Hermes, OpenClaw and Letta already provide continuity mechanisms. | Combining stores can duplicate extraction, truth and cost; define correction/deletion/context ownership. See [platforms](platforms.md). |

An official provider article documents Hermes–Hindsight integration. Retaining accepted memory during evaluation is therefore plausible, but migration completeness, privacy and cost remain unproven. [Integration](https://hindsight.vectorize.io/blog/2026/07/17/hermes-hindsight-open-stack).

## Home Assistant and browser

| Component | Scope | Consequence |
|---|---|---|
| Official HA MCP | Selected LLM API/Assist, exposed entities and live context. | Basic Assist does not automatically mean general automation editing. [Official integration](https://www.home-assistant.io/integrations/mcp_server/). |
| Community ha-mcp | Broad device/service discovery, automations, scripts, history and HA management; MIT. | Reuse candidate for general HA. Documentation proves neither natural reasoning nor real effect; Eva's current general API may already suffice. [Repository](https://github.com/homeassistant-ai/ha-mcp). |
| Playwright MCP | Browser tools, extension for existing tabs/logged-in state, persistent/isolated contexts. | Control surface under the chosen agent; not the reasoning loop or a security boundary. Preserve personal-browser handover. [Repository](https://github.com/microsoft/playwright-mcp). |
| Browser Use | MIT browser-agent library, optional hosted services/models and local browser/profile facilities. | Additional agent loop and possible model spend; use only if it adds value over the chosen executor using tools. [Repository](https://github.com/browser-use/browser-use). |

Optional installation requires declared capabilities, dependencies, context access, data ownership and disable behavior. Several overlapping agents are not automatically a modular product. Core conversation should remain useful without optional voice/HA/browser; disabling a module retains results and makes interrupted work visible. These are proposed contracts, not implementation claims.

## Scope and maintenance

This covers requested assistants, relevant current stateful/coding alternatives and Eva's major building blocks. It is not an exhaustive census of every agent repository. Omitted frameworks are not thereby declared inferior.

No installations, model trials, provider connections, dependency upgrades or full advisory scans occurred. Exact release pins exist for five primary platforms; other candidates remain documentation-level screens and require pinned contract checks before trials. Coordinator rechecks versions, licenses, advisories and required APIs before candidate installation, or by 5 October if selection remains open. Activity is not safety, affordability or product-quality proof.
