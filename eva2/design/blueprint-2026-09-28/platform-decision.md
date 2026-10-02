# Platform decision for the Eva2 family blueprint

## Current decision — 2 October 2026: add capabilities from ordinary language

This section supersedes conflicting candidate/dispatch instructions below; the older
analysis is preserved. Owner request: compare Eva1, Eva2 and competitors, then plan
an assistant that can discover, create, install and operate a needed capability or
communication channel without a compulsory preselected service bundle. No runtime,
account, provider, hardware or model trial was changed during this research.

### Recommendation and evidence boundary

**Retain Eva2 as the integration/runtime basis for the first coherent family
contribution. Use Hermes as the strongest replacement candidate only when a
concrete extension-lifecycle gap makes that comparison decision-relevant.** This is an architectural recommendation,
not a measured speed, cost or general intelligence win. Existing code alone is not
a reason to keep an inferior design; feature availability alone is not a reason
to migrate a functioning private-memory/task system.

Hermes provides the strongest documented combination relevant to this particular
seam: custom inbound channel adapters, plugin lifecycle, ordinary agent execution,
skills and an external memory-provider interface. Agent Zero provides an especially
useful existing create/manage workflow, but introducing it as a coding worker does
not transfer its plugin host into Eva. Do not combine Hermes, Agent Zero and another
planner by default. A standalone MCP programming tool remains an option for the
retained-Eva route; it is not a complete channel/extension architecture.

The upstream-only review initially preferred Hermes for the full plugin/channel
lifecycle. The subsequent local review established that Eva already has provider
entrypoints, dynamic MCP registration and native initiative through the shared
runtime. Together with the owner's family-first clarification, this removes the
assumption that a new general host must be built. Retention is the lowest demonstrated
immediate change; total future integration/migration effort is unmeasured. Neither
review establishes a complete-product winner. No migration or mandatory preliminary
Hermes trial is dispatched from a documentation-level feature advantage.

### Comparative findings

| System | Verified source/document capability | Remaining boundary for the owner outcome |
|---|---|---|
| Eva1/Bottie | Shared persona/context in conversation and autonomous turns; generic Node/Python/Git work; staged skill/add-on testing and activation. | Add-ons allow workspace access with network:none. npm activation records status; it does not execute/import the package. Fixed top-level channel adapters remain. No proof of an independently built and activated arbitrary new channel. |
| Current Eva2 | MCP endpoint discovery/registration and dynamically selected schemas; Python provider entrypoints; accepted memory and durable task/result bindings. | MCP registration connects an already running endpoint. Provider packages must already be installed/configured. The provider contribution supplies tools/event sources, not the whole install/start/stop/channel lifecycle. The actual admitted catalog lacks a general programming executor. Component improvement stops at a candidate awaiting operator application. |
| Hermes | Custom BasePlatformAdapter and register_platform; plugin installation/configuration; skill creation; external MemoryProvider. | Adapter authorship and real channel verification still have to happen. Effective disable/reload and Eva's person/audience policy must work on the pinned release. A memory plugin is not proof of household privacy. |
| Agent Zero | Agent-facing create/manage-plugin skills plus documented framework lifecycle APIs, runtime checks, installation hooks and removal cleanup. | These are existing mechanisms/instructions, not a measured autonomous success rate. Running a coding job does not automatically expose the host's plugin lifecycle to Eva. Whole-Eva migration still needs identity/memory/task integration. |
| OpenClaw | External channel SDK, plugin install/update/reload/disable/uninstall, session identity links. | Plugin APIs are documented as experimental. Gateway/session defaults are not proof of person/audience isolation; no smaller Eva migration has been established. |
| OpenCode | Headless coding sessions, async work, status, diffs and abort. | A useful possible coding worker, not a complete channel host, autobiographical memory or family initiative runtime. It is not a mandatory extra dependency. |

The four product qualities remain personality, memory, initiative and autonomous
work. Eva1's owner-observed presence is the behavior reference. Eva2's accepted
memory/continuity and result retention are retained evidence. Competitor persona,
memory and scheduling features do not establish human family experience or current
head-to-head superiority. See the existing [whole-product baseline](../../research/2026-09-27-system-choice/baseline.md).

### Local evidence

- Eva1: ../Bottie/src/mind/persona.js, autonomy.js:679/836 and tools.js:984/2508.
  capabilities.js:155/206/254/279 supplies staged local skills/add-ons;
  toolinstaller.js:712-754 explicitly separates npm registry activation from execution.
  src/index.js:13-18/56-77 composes existing channels.
- Eva2: providers/runtime.py:104-151 discovers configured Python entrypoints;
  providers/contracts.py:30-42 describes tools/event-source contributions.
  adapters/connectors/mcp_registration.py:134-169 registers STREAMABLEHTTP endpoints;
  providers/mcp_registration.py:206 onward discovers and admits their tools.
  application/component_improvements.py:83 retains installed:false candidates;
  bootstrap/delegated_digital_runner.py:101-116 forbids bypassing controlled application.
- Actual configured/dynamic catalog observation is retained in L3.executorAdmissionCheck
  on the canonical board. General programming execution was absent; browser JavaScript
  is not equivalent. This observation was reused, not rerun as a new model benchmark.
- The generic HTTP ingress was not exhaustively inspected in this pass. Do not claim
  every new channel requires a core edit, or that a completely new registry is needed.

### Upstream pins and primary sources

Research checked 2 October 2026. Moving documentation may be ahead of a release.
No security scan, install or compatibility success is implied by these reads.

- **Hermes v0.21.5 / v2026.9.24**, commit
  f97608f178d1ffeca59860195ab7da295f7c8e5f, MIT:
  [release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24),
  [custom adapters](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters),
  [plugin management](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins),
  [memory interface](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin).
  Direct pinned-source reads confirm register_platform in hermes_cli/plugins.py
  (SHA256 51c7fdd506b187c8713e706a7b264614902b28039e79875770e4b120180b423c),
  BasePlatformAdapter in gateway/platforms/base.py
  (502a8371735acfb016968d6df5bac3394b21d292f1b64b5a988df3dafeb7549f),
  MemoryProvider initialize/prefetch/sync_turn in agent/memory_provider.py
  (a4e44a293013fff831ac136ab77c556541cd18817f53bd662e97090cd80bd5a6)
  and MIT LICENSE. The pinned gateway/platform_registry.py fetch returned HTTP429;
  its release contents and complete identity metadata propagation remain unverified.
  [Mid-run loading limits](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins#mid-run-plugin-loading-what-activates-now-vs-next-session)
  make effective activation/disable a real integration requirement.
- **Agent Zero v2.13**, commit e3051fb584b1a36be2b0a0c90606f1c2c2d356ec,
  MIT: [release](https://github.com/agent0ai/agent-zero/releases/tag/v2.13).
  Both version-2.0.0 skills were directly read at that commit:
  [create](https://raw.githubusercontent.com/agent0ai/agent-zero/e3051fb584b1a36be2b0a0c90606f1c2c2d356ec/skills/a0-create-plugin/SKILL.md)
  SHA256 40a949911b44c37926a0eefe9d504c69a2782af3584507238ea5b5da7f9869c1;
  [manage](https://raw.githubusercontent.com/agent0ai/agent-zero/e3051fb584b1a36be2b0a0c90606f1c2c2d356ec/skills/a0-manage-plugin/SKILL.md)
  SHA256 aa1462f3483b81b68d10a1e2797442331c55bbe789463fd887a7ffe16dacf224.
  Their prescribed hooks and real runtime verification are confirmed instructions;
  the [lifecycle API reference](https://raw.githubusercontent.com/agent0ai/agent-zero/main/skills/a0-manage-plugin/references/lifecycle.md)
  was read on main, not executed or separately pinned in this pass.
- **OpenClaw**, official current documentation only, no runtime pin selected:
  [channel SDK](https://docs.openclaw.ai/plugins/sdk-channel-plugins),
  [plugin CLI](https://docs.openclaw.ai/cli/plugins),
  [experimental API contract](https://docs.openclaw.ai/plugins/building-plugins),
  [session identity](https://docs.openclaw.ai/concepts/session).
- **OpenCode**, optional worker reference, not selected for installation:
  [server interface](https://opencode.ai/docs/server/),
  [permissions](https://opencode.ai/docs/permissions/).

The coordinator owns the next source/license/advisory/dependency check before any
candidate installation and on subsequent dependency updates. This is not a claim
that an automatic maintenance system has been installed.

### Conditional fit check only when a concrete replacement is justified

This is not the first execution assignment. First close the observed gaps in
family-driven initiative and actual execution through the current owners. If a
necessary new channel/lifecycle responsibility would require substantial custom
framework work, use existing L3/O1 and P1.2 criteria for the following comparison.
Do not reopen completed B0 or start new general benchmark infrastructure. The
changed requirement is the whole optional extension/channel lifecycle, not C1.

1. Pin source and required package versions; use the current permitted provider/model
   route. Confirm task cancellation, restart/status and a real plugin create/start/
   inbound/outbound/disable path on that source. Read-only seam checks come before
   expensive model work. No implicit new subscription/API route.
2. Bind the same Eva identity and authorized existing memory. Demonstrate private
   and household contexts and corrections through the actual adapter boundary.
   Native profile memory must not become a competing autobiographical store.
3. Establish a single execution owner and a task/result binding. Existing accepted
   tasks and data remain readable; do not replay outstanding effects. Native events
   feed the current dashboard rather than a second writable task database.
4. Compare the complete retained-Eva alternative: reuse existing MCP, provider
   entrypoints, ingress and installation; name the missing lifecycle contributions.
   Count actual custom responsibilities/changes and observed human help, not imagined
   code savings or plugin popularity. Existing working contracts do not need reproof
   unless changed. A dependency/tool-only pass is insufficient.
5. **Proceed** only when the relevant pinned interfaces work and Hermes removes
   concrete custom orchestration/channel/lifecycle responsibilities with a credible,
   explicit migration path. **Retain Eva2** when there is no demonstrated advantage,
   the allowed provider route fails, privacy requires a parallel memory truth, or
   integration becomes another planner/general framework around Hermes. Diagnose a
   concrete failure; no unchanged repetitions or automatic platform hopping.

A passed fit check closes that decision, not an autonomous feature. Actual feature
completion requires Eva itself to take a ordinary-language request through discovery,
authorship where needed, installation, activation, verified use and result delivery.
Known prebuilt installation and genuinely missing-adapter creation are separate cases.
Use the existing independent unseen-case protocol; no fixed example adapter written by
Codex, evaluator modification by Eva, or extra test/rollout round per small task.

### Reuse, replacement and finite scope

Keep existing identity, accepted memory/data, audience authority, result evidence and
working requested adapters. If Hermes is adopted, it owns the migrated conversation/
job loop and extension lifecycle; retire those old execution paths only after that
responsibility moves. Keep historical records and necessary readers. Existing adapters
are optional modules, not a required service bundle for every installation.

A baseline text/setup path and general workspace are necessary to bootstrap expansion;
no compulsory Telegram, WhatsApp, HA or browser setup. Human action remains only where
personal login/consent/physical access is genuinely unavailable to the agent. Prepare
the rest first and provide one concrete step; do not ask the user to program adapters.
A new channel inherits identity/memory policy; its asserted user name cannot grant
access to another person's context.

The 16 completed small tasks are retained. Six remaining examples belong to Eva's own
execution, not separate Codex feature builds. Of 28 open cards, 5 concern voice, 1
optional installation and 8 comparison/final delivery/acceptance. These counts justify
reprioritizing work, not deleting required outcomes or declaring any finished. No
percentage of redundant code or historical credit waste was measured.

Development credits remain unknown. This research requested no Eva provider trial,
model/STT/TTS call, service installation or external account connection. AIDE/AIDE²
and the existing R1 hypothesis remain bounded follow-up; they do not justify another
optimization framework before ordinary expansion works.

---

## Preserved 28 September rationale and conditional integration detail

The current decision above controls dispatch and candidate status. Earlier source
observations below keep their original dates and limitations; they are not new proof.


Status: conditional candidate, 28 September 2026; replacement advantage unestablished.
Integration base supplied and checked by the coordinator: `e94f384551d78b8b7ae158d8688cd3a75fa81cda`.
Scope: the complete family housemate. No business edition, feature code, installation,
model trial or runtime migration is authorized by this document alone.
This decision uses the [platform research](../../research/2026-09-27-system-choice/platforms.md),
[building blocks](../../research/2026-09-27-system-choice/building-blocks.md) and
[actual baseline](../../research/2026-09-27-system-choice/baseline.md).
Code reads in this pass were targeted signatures/composition/lock excerpts, not a new audit.

## 1. Lead decision and bounded fallback

**Hermes Agent was proposed as the lead general execution platform (direction B).**
This is not a replacement decision. The owner challenged duplication of capabilities
already present in Eva; that correction applies to every component. The existing
[baseline](../../research/2026-09-27-system-choice/baseline.md) already documents
background work, task controls, results and memory. Its known full-task envelope
failure does not establish that a platform change is necessary or sufficient.

Before any replacement, record the concrete unmet user outcome, existing code and
evidence, smallest sufficient retained change, candidate benefit and migration cost.
Compatibility alone cannot satisfy this requirement. The limited pinned-source pass
is [UNKNOWN](../../experiments/b0-platform-20260928/README.md), with no probes, model
calls or installation. Its supported seams and caveats are research, not a GO/NO-GO.
No candidate repair campaign is running. The per-slice decisions in the
[delivery specification](delivery-plan.md#existing-capabilities-and-change-decisions)
govern what can be built next.

**B0 decision after this correction:** retain the existing execution for C1. Actual
overlap/task-result evidence exists; no demonstrated C1 gap requires Hermes or a
different loop. Address the remaining channel/latency outcome through those routes.
The shared full-task-envelope failure in T1/T2/L1 is a separate concrete reason to
assess a narrow execution change when that work is dispatched. This is an operational
selection under current evidence, not proof that current Eva is superior to Hermes.
B0's capability/change decision can close while the unused Hermes compatibility gate
remains UNKNOWN. Reopening replacement requires new decisive evidence.

If replacement is subsequently justified, the proposed Hermes shape uses its
persistent gateway, native agent loop, sessions, delegation, skills and scheduling.
Eva contributes identity/audience policy, authorized context, effect/result evidence and
family channel adapters. These are services and transport boundaries, not another planner.

The reason is responsibility transfer: replace Eva's current single-step model invocations
and custom continuation runner with an upstream product that already coordinates conversation
and background work. Python integration and documented memory/tool/platform extension points
make Hermes the most direct candidate. This is an engineering judgment, not measured superiority,
a promise of fewer tokens, or proof that Hermes already meets the complete family requirement.

**The narrower implementation candidate is direction C:** Pydantic AI's native multi-step run/streaming loop on the existing
2.37.0 dependency basis, behind the same Eva contracts; the existing application owns durable
job admission and scheduling. Replace the single-step outer loop instead of merely enlarging it.
Do not run Hermes and the fallback as alternative planners for the same task.
Direction A remains the current running basis; lack of replacement evidence does not
make it obsolete. C also requires a concrete outcome that the retained arrangement
cannot sufficiently deliver; do not rewrite it merely because Hermes is unproven.

| Alternative | Decision for this blueprint |
|---|---|
| OpenClaw | Do not start a parallel candidate build. It offers substantial lifecycle ownership, but its default Gateway trust boundary does not remove the family isolation work, and no measured advantage offsets a second integration path. |
| Letta | No first implementation: current local channel maturity and migration contracts are less established in the bounded research. |
| Agent Zero / OpenCode / Pi / OpenHands | Optional bounded executors only if a later concrete task needs them; none is an initial extra general planner. |
| Deep Agents / LangGraph / DBOS / Temporal | Not added to the Hermes path. Adding another general scheduler/checkpointer is not required merely because it exists. |

If justified replacement reaches section 7 and fails, consider C against that same
concrete gap and record the failed contract. UNKNOWN is not a failed platform test.
Do not cycle Hermes versions, try OpenClaw next, or extend a platform repair campaign.
A future change of this decision requires new decisive evidence, not a preference for another stack.

## 2. Exactly one owner for each responsibility

The coordinator's common contracts are the public boundary: `InputEnvelope`,
`ContextSnapshot`, `TaskHandle/TaskEvent`, `EffectRecord`, `ResultEnvelope`,
`MemoryRevision`, `SkillArtifact` and `ModuleManifest`. This note does not create rival schemas.

| Responsibility | Authoritative owner | Eva integration rule |
|---|---|---|
| Persona and mandate | Eva's versioned identity/policy configuration | The same character and boundaries feed every authorized Hermes session. A worker is not another personality. |
| Authenticated person, audience and ingress identity | Eva admission policy | Resolve before model context, session attachment or tool access; channel/profile names are not identity proof. |
| Dialogue/session history and active turn | Hermes session storage/gateway | Eva's UI journal is an audit/read projection, not a second resumable conversation loop. |
| Plan, model/tool loop, task attempt, cancellation and execution status | Hermes | All start/steer/stop/resume commands go to this owner. Native events update the Eva read model. |
| Task identity, original intent and destination | Eva task binding, attached to the Hermes handle | Immutable association, not an independently scheduled task queue. A binding alone is not acceptance. |
| External effect identity, authority, outcome and uncertainty | Eva effect service plus observed target state | Hermes asks the tool service; a model's success statement cannot replace readback evidence. |
| Result artifact, provenance and delivery receipt | Eva result/outbox service | Hermes owns producing/synthesizing the answer; the service owns retention and the authorized original destination. |
| Accepted personal facts, corrections and deletion provenance | Eva PostgreSQL authority | Hindsight supplies the existing semantic recall/retention service; derived recall cannot override current authority. |
| Skill generation and task-oriented reuse | Hermes skill subsystem | Eva stores artifact identity, access scope, source evidence and validation status; research extensions are optional. |
| Initiative timing and job execution | Hermes scheduler/gateway | Eva supplies events, relevance/quiet-hours/audience constraints and open-intention context; no parallel Eva autonomy scheduler. |

Task queries read a projection with last observed event/revision and freshness.
A disconnected owner produces unavailable/unknown, not an invented running/success state.
Commands never mutate that projection into authority: persist the request identity, submit once,
record the returned owner handle, then project owner events. Ambiguous admission is reconciled
by the same request binding; it is not silently resubmitted as a second task.
Effects and delivery have their own idempotency keys; they do not choose the next task step.

## 3. Hermes adapter shape and family trust boundary

Use one persistent, stateful Hermes host per security scope, with one writer per session.
Start with household scope and the actually registered private-person scopes; do not invent users.
Profiles/processes are isolation instances of one platform, not competing orchestrators.
Share the identity package and explicit household knowledge, not private transcripts or credentials.
Private scopes must not be accessible through the household process's filesystem or session search.

The [programmatic interface](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration)
documents TUI gateway JSON-RPC session creation, background prompts, steering, interruption and events.
Use that stateful interface through a small `HermesRuntimeAdapter`; exact release support is gated.
Do not implement the integration as repeated `AIAgent.run_conversation()` calls controlled by
`LivingConversation`, or as a stateless chat endpoint with an Eva-owned continuation loop.
Avoid a second native Telegram/HA listener alongside Eva's existing ingress adapter.

The adapter routes commands and events; it does not plan, summarize every tool result, retry
uncertain side effects, or run an extra model to decide ordinary simple replies.
A fast contact session may delegate substantial work to an appropriate worker model in the same
provider policy. Use an independent owned work session for work that must outlive a conversational
session reset; retain its original result binding. The gate must establish this ownership seam.

Private conversation/memory remains on verified private Telegram. Shared speakers, unknown
speakers and the currently trusted local browser do not receive private memory merely because
they can reach Eva. Registered household devices keep broad household authority.
Every context load, retained memory, task attachment and outbound result rechecks the same
person/audience boundary. A private result cannot fall back to a speaker on delivery failure.

Use a single Hermes memory provider adapter to Eva's existing authorized memory service.
The [provider contract](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
offers session initialization metadata, per-call prefetch and completed-turn synchronization.
Do not trust model-supplied identity fields or profile memory snapshots for authorization.
Disable competing autobiographical writes to native MEMORY.md/USER.md and unscoped recall.
Keep only non-private identity/configuration there if required by the host.
Memory correction/deletion must invalidate affected cached context before the next relevant call;
a fresh bounded context/session boundary is acceptable, silently using a stale snapshot is not.

## 4. Exact reuse, replacement and migration seams

Paths below name existing implementation surfaces, not a mandate to preserve every file.
Extract required contracts when a component is tightly coupled to the old loop.

| Existing surface | Proposed disposition and replacement boundary |
|---|---|
| `eva2/src/eva/application/conversation.py::LivingConversation.respond` | Retire as planner/continuation loop on migrated routes. Retain reusable turn/identity/usage contracts only where needed; adapt to the common envelopes. Hermes produces turns. |
| `eva2/src/eva/adapters/model/pydantic_conversation.py` | Remove its one-request/one-tool-step mediation from Hermes traffic. Keep provider compatibility knowledge as a reference; C would use a native multi-step run instead. |
| `eva2/src/eva/bootstrap/delegated_digital_runner.py::DelegatedDigitalRunner` and `delegated_digital_composition.py` | Retire custom worker continuation/attempt planning after task ownership moves. Preserve tool authorization, observed readback and budget-accounting semantics as non-planning services. |
| `eva2/src/eva/bootstrap/runtime.py`, `private_conversation_resources.py` | Replace composition selectively; retain the authorized memory/identity service boundary. Do not instantiate both conversation engines for one ingress. |
| `application/memory.py`, `application/memory_audience.py`, PostgreSQL memory/outbox adapters and Hindsight | Reuse accepted memory/correction/deletion behavior. Add one Hermes provider adapter; no bulk flattening into persona Markdown or dual extraction pipelines. |
| `adapters/postgres/conversation_journal.py`, `background_work_progress.py`, `action_chain_ledger.py` | Preserve historical records/IDs. New Hermes state is authoritative for execution; expose a read projection and immutable task binding. Do not keep old admission/advance logic scheduling new Hermes work. |
| `adapters/postgres/action_chain_effects.py`, effect/readback/result adapters, `operations/action_chain_effect_*.py` | Reuse the authority/readback/uncertainty contracts through a deterministic effect-tool facade. Existing plan-shaped dependencies must be adapted without constructing another model-generated action chain. |
| `adapters/postgres/connector_results.py`, effect results and original destination records | Preserve result artifacts and provenance. The Hermes completion is admitted once to the result/outbox service; tool success and delivered answer remain separate facts. |
| `application/telegram_ingress.py`, `bootstrap/telegram_ingress.py`, connector Telegram transport | Retain registration, admission and transport initially; replace only the call into the old conversation engine with the Hermes adapter. One poller/webhook owner per bot token. |
| `bootstrap/household_voice.py`, `household_digital_work.py`, HA API/automation connector modules | Preserve both speaker bindings and the general HA API, including automation create/edit/readback. Expose these as authorized tools; Hermes's stock HA service tools do not establish full parity. |
| `eva2/deploy/voice-session/` and the existing LiveKit route | Retain transport/audio behavior where useful; conversation/work connects to Hermes. Physical Voice PE behavior is still a separate required trial. |
| `application/household_initiative.py`, initiative delivery and stored consent | Reuse context/audience/destination/quiet-hours semantics. Replace old scheduling/execution ownership with Hermes events/jobs when the corresponding route migrates. |
| Eva1 `src/mind/persona.js`, `perceptiondecision.js`, `src/integrations/homeassistant.js` | Preserve valued character, doorbell/presence outcomes, camera/intercom capabilities as acceptance references; port only missing behavior to the common policy/tool boundary. |
| Eva1 `src/integrations/telegram.js`, `src/mind/agent.js`, `autonomy.js`, `curiosity.js`, `projectexecution.js` | Do not wrap these planners beneath Hermes. Drain/disable their ownership per migrated route; preserve journals/projects and any unrelated live service. |
| Eva1 learning/tools and Eva2 procedure artifacts | Import scoped data/examples as provenance-linked artifacts; do not automatically activate old executable skills. Validate compatibility and rights before reuse. |

For Bottie/Eva1 Telegram, first record which live process owns each configured bot route.
Move only the selected route: stop its old consumer before enabling the new consumer; preserve
pairing, update offsets/deduplication identities and pending outbound destinations.
Do not assume the old JavaScript file is currently the live listener, and do not disable unrelated
Bottie services. During staged coexistence, each route/task has exactly one owner and ownership epoch.

## 5. Long work, effects and cutover

Hermes [delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation)
persists completed deliveries but marks interrupted unfinished execution unknown after restart.
That is not crash-resumable task execution. Ordinary follow-ups preserve children; stop/reset
has different semantics. Eva must expose these distinctions rather than promise transparent replay.

Multi-day work uses persisted intent/progress references and Hermes scheduled continuation,
not an unbounded child attached to a disposable chat. Before continuation, the same Hermes owner
reads the last checkpoint and effect evidence; uncertain writes require observation/reconciliation.
Resume means a new authorized attempt from known progress, not replay of completed external actions.
Status changes, factual corrections and stops carry task IDs across channels within audience scope.
A stop blocks further dispatch and cancels the owner; an already completed effect remains recorded.

Cutover sequence: preserve source/image provenance and export manifests; snapshot canonical stores;
map identities, memories, task/effect/result IDs and source references; admit only explicitly mapped
unfinished work; verify one complete route; switch its ingress/event ownership; drain old execution;
then retire its replaced route. Old completed/uncertain actions are never queued as migration work.
Keep the old images/config and readable historical records for rollback. A rollback freezes new
admission, reconciles in-flight effects, transfers ownership once and retains new evidence.
It must not restore an old database snapshot over events created after cutover.
No platform choice authorizes deleting memories, uninstalling useful services or taking both speakers offline.

## 6. Pins, optional dependencies and update policy

| Dependency | Build basis and license evidence | Upgrade/migration rule |
|---|---|---|
| Hermes lead | [v2026.9.24 / 0.21.5](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24), commit `f97608f178d1ffeca59860195ab7da295f7c8e5f`; [MIT at commit](https://raw.githubusercontent.com/NousResearch/hermes-agent/f97608f178d1ffeca59860195ab7da295f7c8e5f/LICENSE) | Pin source and resolved dependency lock/image digest for the candidate; no floating main or automatic plugin installation. |
| OpenClaw, comparison only | [v2026.9.6](https://github.com/openclaw/openclaw/releases/tag/v2026.9.6), `eb377ac59e6c9fd6c7705028034812becf00271b`; release LICENSE reviewed as MIT in research | Not installed alongside the lead to keep a fallback warm. |
| C fallback | Existing `eva2/requirements-4d.lock`: Pydantic AI Slim 2.37.0, OpenAI SDK 3.6.0; [Pydantic project](https://github.com/pydantic/pydantic-ai) and [SDK](https://github.com/openai/openai-python), MIT | Lock proves current basis, not permission to copy every old package into Hermes's environment. Verify licenses/transitive advisories in the candidate manifest. |
| Memory | Hindsight MIT per [upstream](https://github.com/vectorize-io/hindsight); repository `hindsight.compose.yml` pins `sha256:aeb0831e2ec55e659d5e69baa064c1278113e523513b8f60a1f09c5cabe59b9b` | Managed deployment supplies its own sealed immutable image reference: resolve that actual digest before migration. Do not equate the repository default with the running service. Preserve embeddings/banks. |
| Voice, optional installation | Existing `eva2/deploy/voice-session/requirements.lock`: LiveKit Agents 1.8.0; [upstream](https://github.com/livekit/agents), Apache-2.0 | Keep its separate environment and verified deployed digest; no forced voice rewrite with the platform migration. |
| HA, browser, coding tools | Existing general HA connector first; browser/shared-tab and isolated-workspace modules remain separate capabilities | Select/record exact pins and licenses only for modules actually included. No Browser Use/ha-mcp/second coding agent by default. |

Research release checks are dated 27 September; official interface pages revisited 28 September
are moving documentation and may be ahead of the pinned release. They are evidence for the gate,
not a claim that every named API is present at that commit.
The coordinator owns the candidate version/license/advisory check before installation, next due
5 October if still pending. Record resolved dependency provenance and exercise upgrade/rollback
on the same adapter contract fixtures; an upstream update does not require changing family policy.

Providers remain replaceable. Reuse the existing authorized subscription/access-only credential
route; document whether Hermes can consume it without copying refresh secrets or creating an account.
Do not enable an OpenRouter/default metered fallback merely because upstream supports it.
Use configured contact/worker roles; no costly mandatory router for every greeting.
Measure complete inference, tools, memory, learning and delivery overhead separately from development.
Do not invent daily token/call ceilings or claim subscription credits are free/unlimited.
Finite development-probe bounds are a separate agreed implementation control.
Optional modules declare dependencies/access/data export/disable behavior; absent modules do not
break conversation or erase history. Stop dependent work visibly and preserve its result handles.

## 7. Compatibility gate only after replacement is justified

Only after the replacement case above is established, run one bounded adapter-contract
spike against the Hermes pin before live cutover. This spike is currently stopped.
It is one implementation slice plus one bundled independent review, not a platform bakeoff.
Use deterministic model/tool stubs and existing synthetic records; no private database copy,
new paid provider route or live household action is needed for this compatibility decision.
The gate produces a pass/fail receipt with source paths, mapped methods and remaining product trials.

| Required contract | Decisive evidence |
|---|---|
| Stateful ownership | Two admitted jobs plus an intervening contact turn have distinct Hermes handles; targeted steer/stop reaches the right owner, survives a client reconnect, and has one event/result association. |
| Scope propagation | Private and household contexts, child tools, history lookup and return delivery stay separated; missing identity never loads private context. A memory correction is visible before a subsequent relevant call. |
| Existing access/effect seam | The pinned resolver supports the existing credential mode without a new account; an effect stub goes through retained authority/readback once. Unknown restart state cannot redispatch it. |
| Continuation/delivery | Completed-undelivered work is deduplicated; unfinished work is unknown and reconciled. Independent work ownership survives contact-session reset, and scheduled continuation reads persisted progress. |
| Replaceability | Existing ingress calls a supported boundary; the old single-step runner/planner is absent from this traced path. Hooks can supply necessary context/results without forking the general Hermes loop. |

**GO** only if all five contracts can be demonstrated at the pin through configuration,
supported adapters/plugins and bounded translations of existing policy/data services.
**NO-GO** if a general-loop fork, parallel scheduler, unsafe private scope, unavailable credential
mode or unknown-effect replay is required. Record the exact blocker and assess the
narrower C change against the same demonstrated outcome, not as automatic new work.
A local fixture/adapter defect may be corrected once within this slice; an unresolved contract
does not trigger more candidate rounds. Missing live-access evidence remains explicitly open.

Passing selects an implementable basis, not owner acceptance or proof of better daily use.
Complete the original eight delivery steps and their actual channel/installation/rollback criteria;
keep EV-003's existing comparison protocol and thresholds. This gate does not replace those trials,
lower their standards or add a new universal benchmark requirement to every small delivery.
