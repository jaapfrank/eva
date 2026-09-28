# Platform decision for the Eva2 family blueprint

Status: implementation recommendation, 28 September 2026; compatibility gate pending.
Integration base supplied and checked by the coordinator: `e94f384551d78b8b7ae158d8688cd3a75fa81cda`.
Scope: the complete family housemate. No business edition, feature code, installation,
model trial or runtime migration is authorized by this document alone.
This decision uses the [platform research](../../research/2026-09-27-system-choice/platforms.md),
[building blocks](../../research/2026-09-27-system-choice/building-blocks.md) and
[actual baseline](../../research/2026-09-27-system-choice/baseline.md).
Code reads in this pass were targeted signatures/composition/lock excerpts, not a new audit.

## 1. Lead decision and bounded fallback

**Choose Hermes Agent as the lead general execution platform (direction B).**
Use its persistent gateway, native agent loop, sessions, delegation, skills and scheduling.
Eva contributes identity/audience policy, authorized context, effect/result evidence and
family channel adapters. These are services and transport boundaries, not another planner.

The reason is responsibility transfer: replace Eva's current single-step model invocations
and custom continuation runner with an upstream product that already coordinates conversation
and background work. Python integration and documented memory/tool/platform extension points
make Hermes the most direct candidate. This is an engineering judgment, not measured superiority,
a promise of fewer tokens, or proof that Hermes already meets the complete family requirement.

**Fallback is direction C:** Pydantic AI's native multi-step run/streaming loop on the existing
2.37.0 dependency basis, behind the same Eva contracts; the existing application owns durable
job admission and scheduling. Replace the single-step outer loop instead of merely enlarging it.
Do not run Hermes and the fallback as alternative planners for the same task.
Direction A is the live rollback, not the target recommendation.

| Alternative | Decision for this blueprint |
|---|---|
| OpenClaw | Do not start a parallel candidate build. It offers substantial lifecycle ownership, but its default Gateway trust boundary does not remove the family isolation work, and no measured advantage offsets a second integration path. |
| Letta | No first implementation: current local channel maturity and migration contracts are less established in the bounded research. |
| Agent Zero / OpenCode / Pi / OpenHands | Optional bounded executors only if a later concrete task needs them; none is an initial extra general planner. |
| Deep Agents / LangGraph / DBOS / Temporal | Not added to the Hermes path. Adding another general scheduler/checkpointer is not required merely because it exists. |

If the gate in section 7 fails, select C once and record the failed contract.
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

## 7. One compatibility gate, then implementation

Run one bounded adapter-contract spike against the Hermes pin, before live cutover.
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
mode or unknown-effect replay is required. Record the exact blocker and use fallback C.
A local fixture/adapter defect may be corrected once within this slice; an unresolved contract
does not trigger more candidate rounds. Missing live-access evidence remains explicitly open.

Passing selects an implementable basis, not owner acceptance or proof of better daily use.
Complete the original eight delivery steps and their actual channel/installation/rollback criteria;
keep EV-003's existing comparison protocol and thresholds. This gate does not replace those trials,
lower their standards or add a new universal benchmark requirement to every small delivery.
