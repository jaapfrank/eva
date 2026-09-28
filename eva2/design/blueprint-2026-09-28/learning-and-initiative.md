# Learning, personal development and useful initiative

Status: bounded design, 28 September 2026; source basis `e94f384551d78b8b7ae158d8688cd3a75fa81cda`.
No implementation, installation, model trial, measured advantage or novelty claim.
This document supplies the learning/initiative part of the complete family product; no company plan or business-development track.
Hermes is the provisional single execution owner, subject to the compatibility decision in [platform-decision.md](platform-decision.md).
The interfaces below are proposed semantic contracts, not claims about existing Hermes APIs or implemented wire schemas.

## 1. Product boundary and useful baseline

Eva remembers relevant experiences, develops a recognizable perspective, learns unfamiliar digital procedures, and notices worthwhile work without requiring constant prompting.
She remains warm, occasionally humorous, capable of dissent, interruptible and comfortable with silence; she does not claim subjective experience or make family members responsible for her feelings.
One authorized identity/context applies across Telegram, website, HA and Voice PE. Shared speakers receive household context and all authorized general HA capabilities; private content remains on the private Telegram route.
Apply audience checks before retrieval/model input and again before delivery. A device, spoken name or apparent familiarity is not person verification.
The ordinary release uses the selected platform's memory/skill/task mechanisms with a bounded Eva integration; it works when the experimental task-model module is absent.
Preserve accepted memory outcomes and useful EVA1 presence as acceptance references; do not require copying their implementation.

| Learning layer | Durable result | What may change automatically | What this does not prove |
|---|---|---|---|
| Personal/household memory | Source-linked facts, corrections, preferences, dates and authorized summaries | Relevant knowledge and its validity, through the existing memory authority | Identity verification, permission, or permanent truth from an inference |
| Personality and relationship development | Versioned interests, interaction preferences and a small evidence-linked state affecting attention/tone | Gradual, reversible adaptation within the identity's declared character and audience | A new persona per channel, consciousness, or entitlement to attention |
| Learned procedure | `SkillArtifact`: an upstream skill plus applicability, sources, outcome check and version | Save a useful method and retrieve it for a genuinely different input/task | General learning competence from one successful example |
| Optional executable task model | Bounded state/action/prediction artifact referenced by a skill | Eligibility/prediction revisions inside its declared domain | A universal household simulator, an invention, or permission to act |

## 2. One execution owner and explicit data ownership

Hermes owns admission, scheduling, foreground/background coordination, planning, cancellation and task status. No separate Eva initiative scheduler, sleep daemon, planner or worker-status database.
Use a thin adapter where upstream cannot express a necessary product contract; the compatibility gate must establish this before implementation.
Memory-provider internal extraction queues may remain internal implementation details; they do not choose new household tasks or own a competing user-visible task lifecycle.
Reuse canonical stores and stable identifiers. The records below are views/extensions of those owners, not an instruction to add one table or service per row.

| Contract | Minimum information used here | Authority |
|---|---|---|
| `InputEnvelope` | Input/event ID, authenticated actor or household device, audience, timestamp, source reference, modality | Existing ingress and household authority |
| `ContextSnapshot` | Identity revision, allowed memory references/revisions, relevant intentions and tasks, validity/expiry | Context adapter over authorized stores; never raw unrestricted family memory |
| `TaskHandle` / `TaskEvent` | Task/source IDs, owner/audience, outcome, stop/correction generation, progress and terminal disposition | Selected platform; Eva effect adapter may block an unsafe continuation |
| `EffectRecord` | Intent, operation ID, attempt state, uncertain outcome, observation/readback, result reference | Existing authoritative effect semantics behind tools; platform success alone cannot overwrite them |
| `ResultEnvelope` | Original task/question/destination, verified outcome, source/effect references, limitations, delivery disposition | Result adapter; recheck destination authority at dispatch |
| `MemoryRevision` | Stable item/source ID, old/new revision, scope, effective interval, correction/exception/deletion reason | Canonical memory authority; summaries and skills cannot manufacture a revision |
| `SkillArtifact` | Skill ID/version, source hashes, purpose, applicability, required capabilities, parameters, outcome check, evidence, lifecycle | Platform skill registry plus scoped references; execution grant remains external |
| `ModuleManifest` | Capability, version, dependencies, context access, state/export owner and disable behavior | Product composition; task-model extension is optional |

An intention is a platform-owned open task/reminder or retained user commitment, with desired outcome, audience, due/review condition, evidence and disposition.
It is not a second autonomous-goal store. Proposal IDs and source-event IDs support deduplication before admission.
A skill references authorized data or accepts parameters; it must not bake a private calendar, conversation or credential into a shareable script.
Executable artifacts inherit no tools, network, accounts or rights merely because a prior run succeeded.

| Interruption or failure | Required common behavior |
|---|---|
| Stop while learning/reflecting | Advance the platform cancellation generation, block further effects/publication, preserve evidence and return the actual disposition; never report an already committed effect as undone. |
| Correction while work runs | Publish the canonical revision and invalidate only affected assumptions; acquire current context before the next effect/result. A temporary exception does not rewrite the permanent preference. |
| Timeout or unknown effect outcome | Preserve the operation ID and uncertain state; use authoritative readback/reconciliation before retry. No automatic duplicate action merely because a model or network timed out. |
| Restart, provider failure or revoked scope | Resume from the same platform task/effect record only when its grant/context is current; otherwise defer or terminate honestly. Missing memory authority cannot justify unscoped retrieval or fabricated continuity. |
| Optional module disabled or skill suspended | Preserve the task/effect record and ordinary skill fallback; do not silently replay completed work or switch execution owner. |

## 3. Automatic memory and personality development

1. Ingress authenticates the source/audience, then builds the smallest relevant `ContextSnapshot` for the common conversation route.
2. Record a conversation/event once. Use the selected memory provider's extraction/retention path; do not run independent platform and Hindsight extractors over the same event by default.
3. Distinguish direct statements, observations and hypotheses. Keep their references, dates and scope. Do not silently promote a model's interpretation into a personal fact.
4. Apply an explicit correction through canonical revision semantics. A temporary exception has an interval and does not erase the durable preference; newer evidence alone does not prove supersession.
5. Recall current, relevant knowledge in a later conversation. Ambiguity is represented, not flattened into a confident profile. Forgetting invalidates retrieval and derivative eligibility through the same authority.
6. Develop tone/interests from repeated attributable experience and explicit feedback. Stable warmth, independence, honesty and privacy remain identity constraints; adaptation is inspectable and reversible.
7. Let a small durable state influence attention and expression only when relevant: unfinished curiosity, confidence after contradictory evidence, or recent interruption. Do not require a model call to update a timer or count.
8. Test observable consequences: changed advice, fewer repeated questions, a relevant later contribution and correction retention. A mood label or richer biography alone is no improvement.

Hindsight is the first memory-reuse candidate because its existing integration has accepted outcomes. Native platform memory must be assigned a complementary role or disabled for overlapping extraction/long-term truth.
The final storage choice is not fixed here. A switch requires data/correction/deletion mapping; do not silently dual-write and hope the stores converge.

## 4. Ordinary procedure learning: usable without research modules

A normal task may discover a tool, read its documentation, create a method and execute it through the platform's existing tools/skill facilities.
Use official/documented interfaces and maintained connectors first; synthesized code is justified by a concrete missing capability and runs only in the existing isolated execution facility.
Do not add a core branch for a service name, room, camera, bin colour or demonstration input.

`Task admitted -> discover/read -> propose or retrieve method -> check applicability -> act -> independently observe outcome -> return result -> retain eligible experience`.
A method records source/contract versions, required input, known limitations and outcome checks. Logs preserve failures and operator hints.
Success may make it a local candidate; reuse on a held-out variant makes it eligible within that tested scope. A failed reusable skill is quarantined or narrowed, not silently overwritten as if the old version never existed.
Distinguish producing a script, executing it, checking its semantic result and learning to select it later. Each is a separate event.
Before admitting a task, check that the applicable execution envelope can cover its necessary route. Do not repeat the historical six-action/eight-response mismatch for a path needing at least eight/nine.
On a changed contract, missing capability or relevant memory revision, re-evaluate applicability before another effect. Preserve completed effect IDs and read back an uncertain effect instead of repeating it.
General reasoning remains available for novel or unsupported work; a skill registry is not an exclusive catalogue of everything Eva may attempt.

## 5. Initiative from retained intentions and real events

Use platform event subscriptions and scheduled jobs; the same mechanisms also own ordinary user tasks.
An event can be a due intention, allowed calendar revision, HA observation, completed task, unanswered question or an explicitly retained curiosity. Mere elapsed time does not require a message.

1. Coalesce duplicate source events; reject stale/out-of-scope events using their IDs, validity and current authority.
2. Retrieve only potentially relevant intentions/context. Cheap eligibility checks precede any reflective model call.
3. Assess a concrete contribution: useful outcome, urgency, uncertainty, interruption cost, what is already underway and expected effort. No fixed initiative per room or example.
4. Choose `ignore`, `defer`, `quietly investigate`, `admit work`, or `ask for a genuinely missing decision`. Acknowledgment requires actual admission.
5. Admit work under existing, revocable authority. Already authorized household actions do not get another generic permission question; new spending, accounts, commitments or rights require a decision.
6. Verify the actual result and update the same intention/task. A proposal, a dispatched tool and a successful outcome are not interchangeable.
7. Deliver once to an eligible original destination, or retain for a better moment. Shared speech must not reveal a private task's existence, topic, sources or hints.
8. Learn from acceptance, correction, dismissal and appropriate silence. Record no-response as unknown, not positive feedback or consent.

Respect owner quiet settings, current occupancy/audience knowledge, recent interruption and duplicate-topic suppression. If audience knowledge is insufficient, use the already authorized private route or defer.
Urgency must come from a trustworthy event and existing policy, not a model upgrading its own importance. Any platform-native urgent bypass must remain within that policy.
Do not reproduce an independent EVA1 and EVA2 spontaneous announcer for the same event after cutover; preserve the useful behavior through one event/task/delivery owner.

## 6. Sleep/reflection as bounded background work

“Sleep” means an idle-time maintenance/reflection task, not consciousness or a second learning service.
The platform schedules it after relevant new evidence, task failure or owner feedback, or at an existing permitted quiet time; interaction and explicitly requested work take priority.
Read a bounded selection of changed records, not the whole family history. No new evidence means no speculative replay of unchanged work.
Potential outputs are a corrected hypothesis, linked intention, concise lesson, candidate skill revision or an eligibility change. Unchanged conclusions produce no user notification.
A reflection proposal does not directly rewrite user facts, alter identity boundaries, publish a skill or execute a household action. It uses the corresponding existing update/admission path.

| Resource/control | Ordinary runtime rule |
|---|---|
| Total allowance | Inherit the current owner/provider grant; reserve and account for parent, child, memory/reflect, retrieval and tool usage together |
| Foreground priority | Yield/cancel the background lease at a safe boundary; preserve progress and effects; no competition with live speech for the same job slot |
| Selection/termination | One coalesced revision window per admitted job; finish when its changed evidence is processed; stop on no progress or repeated identical proposal |
| Missing accounting | Mark total unknown and make no savings claim; do not hide memory-provider calls as free work |
| Repetition | Reschedule only for new evidence, a changed cause or an explicit resumption; do not keep reasoning until a preferred answer appears |

No new arbitrary daily, call or token quota is imposed on Eva's existing subscription. Concrete technical limits still reflect provider limits and the actual owner grant.
An experimental reflection or task-model run instead has the predeclared finite research envelope below; that is not a household usage cap.
Improvement must appear later: correct retrieval, an applicable reused skill, avoided stale action or a valued contribution. Self-reported insight and more stored text do not count.

## 7. Optional research: a small executable task model

Hypothesis: for a repeated bounded procedure, a learned model can recognize changed assumptions and choose informative reads better than ordinary skill preconditions/regression tests, at acceptable total cost.
The module returns evidence and eligibility/predictions to Hermes. Hermes still proposes/selects the plan, owns its task and dispatches all real tools.
Proposed artifact fields: state variables and units; observed transition references; supported action/contract versions; applicability predicates; predicted observable effects; explicit unknowns; scope; model version; validation cases.
References point to canonical memory/task/effect IDs. Do not replicate all personal information into a “world state”.

Lifecycle: `candidate -> isolated validation -> held-out comparison -> eligible within scope -> suspended on relevant change -> revised or retired`.
Predictions are recorded before outcomes; discrepancies narrow eligibility or trigger an observation. A confidence score cannot compensate for absent state, concurrency or hidden effects.
Simulation executes only the bounded artifact in the existing isolated executor, without effectful tools, credentials or network. Predictions never count as real task success.
Use a small declared state/action domain. Set model size, transition-evaluation and synthesis limits in the experiment manifest; do not grow a general household simulator to rescue a failing case.
Dependency links identify which eligibility assumptions rely on a revision. Invalidate the affected decision before use; do not delete unrelated skills or automatically re-run completed work.
Fallback is ordinary upstream skills/general reasoning with the same authority. Missing/disabled research module must leave conversation, memory, HA and known procedures usable.

## 8. Concrete user situations through these same contracts

| Situation | Shared flow and expected useful outcome | Boundary |
|---|---|---|
| Bins, camera and calendar | An authorized collection-calendar event meets a permitted camera observation; Eva checks whether the relevant bin appears ready, accounts for uncertainty, and offers a timely reminder or stays quiet. A schedule correction updates the relevant intention. | Use existing observation/calendar tools; infer no person's identity from a camera. Private calendar details are not spoken; share only explicitly household-authorized content. No bin-specific core planner. |
| Helpful HA improvement | An existing comfort/quiet intention and allowed history suggest an automation behaves poorly. Eva investigates, makes an authorized reversible adjustment when justified, reads back the full configuration/outcome, and returns the actual change or an honest limit. | General HA API on both speakers remains available. Preserve original automation and rollback; no hardcoded device rule or repeated generic approval for already authorized work. |
| Unknown digital service, later reuse | Eva reads a previously unfamiliar service contract, chooses tools, completes a new task and records a method. Later different input and a changed contract test whether she selects, adapts and checks the method without developer-supplied code/IDs. | No per-service kernel function; external content is evidence, not authority. An account/access gap is a visible missing condition. |

Financial opportunities start as source-linked research and a clearly hypothetical paper portfolio using existing allowed information sources.
Paper entries retain decision time, observed price/source time, assumptions, fees/slippage or explicit unknowns; later data cannot be smuggled into the earlier decision.
No broker/order capability, real trade, payment, account creation or promised income follows from this design. Real trading needs a separate mandate. This is ordinary bounded research, not a business plan or a trading/self-training platform.

## 9. Finite evidence and promotion protocol

No runs are authorized by this document. The root plan's initial compatibility gate is source/stub inspection only; authorized small live comparisons belong alongside C1/L1, not a duplicate preliminary campaign. Complete-product release and EV-003 thresholds remain unchanged; this optional protocol sits beneath them.
Freeze platform/model/provider versions, tool access, source data, initial memory, grants, budgets, rubric and artifacts before generation. An independent curator supplies hidden inputs after the candidate is frozen.
Use the same model/effort and tools for a controlled comparison; a native-product comparison is separately labelled. Admission/harness failures are unknown comparisons, not competitor losses.

Only two combined cases, each twice, using isolated fixtures with no real house effects:
- **Case A:** two ordinary tasks alongside a conversation; an attributable correction/temporary exception, stop of one task and result return from the other. Include a misleading event and a shared-audience request that must not expose private context.
- **Case B:** unfamiliar documented service, first task and later different-input reuse; a changed API assumption requires a fresh observation/adaptation. One unrelated skill must remain usable. The examples above are not the hidden dataset.

Baseline: the selected platform's ordinary memory and skills, with normal preconditions, provenance and outcome checks. Do not deliberately cripple it.
Consider the closest usable prior-art mechanism before own synthesis; pin its source/license and interface. An unavailable/unlicensed candidate stays unassessed, not beaten.
Compare the candidate with and without its executable task model. If that ablation differs from the upstream baseline, permit one additional four-run baseline arm: at most twelve task runs in the entire experiment.
Proposed research-only ceiling, pending pre-run path and cost review: 600,000 aggregate input tokens and 48,000 aggregate output tokens across the complete experiment, 36,000 tool-result characters admitted per run, and 60 minutes elapsed overall. These numbers authorize no execution.
Each run has at most twelve model responses, twenty-four tool operations and five minutes; the aggregate ceiling includes artifact synthesis, validation, memory/reflection, helpers, repairs and all arms.
Before any run, use source/fixture inspection to ensure the necessary path fits. If it cannot, shrink the research question before freezing or keep the module parked; do not silently raise a limit after failure.
These are maximum experiment envelopes, not cost estimates, expected consumption or ordinary-user quotas. Credit cost remains unknown; no new paid provider route.
A simulation search is capped at six candidate plans and twelve predicted transitions per plan per run; the platform owns that bounded selection. No recursive simulator/agent growth.

Measure complete verified outcomes; stale actions and unnecessary invalidations; human hints/minutes; contact/content latency separately; calls/input/output/cache and memory/tool use; construction/revision cost; interruptions, duplicate effects and wrong-destination results. Reconcile nested provider counters once; report cached input separately without counting it twice in total processed tokens.
Compare total learning-plus-use over a predeclared horizon of five eligible uses. If five uses cannot be represented inside the frozen cases/envelope, no amortized saving claim is available from this round.
Promotion requires all hard boundary/correctness cases to pass, no extra human help, and the same useful outcomes in both repeat blocks.
For a resource claim require at least 15% lower total processed model tokens over that horizon, including construction/maintenance, without worse outcome or more than 10% slower paired successful content latency.
Alternatively, a narrowly scoped reliability claim requires fewer stale/invalid actions in both changed-condition repeats with no higher total measured model use; do not relabel it a speed improvement.
This small decision gate is not statistical general superiority, a p95 estimate, or a substitute for EV-003/family acceptance. Unmeasured totals cannot support promotion on savings.
Stop at the first privacy/authority breach, duplicate effect or corrupted state; retain failure evidence. Do not repeat an unchanged failure or extend the round until it wins.
At most one revised candidate may be proposed after a concrete changed cause; its new evaluation requires a fresh frozen held-out set and a new explicit experiment envelope, not an automatic loop.

## 10. Core completion, optional R&D and exact gaps

Core technical completion: automatic scoped memory/correction, useful ordinary skill learning/reuse, one platform-owned initiative lifecycle with true outcomes/silence, observable stop/change handling and a usable result path; installed proof belongs to the root delivery plan.
Human acceptance separately checks warmth, remembered context, valued initiative, nuisance and physical speech. Previously accepted outcomes are retained; new full-family/Voice PE acceptance is not invented.
The executable task-model module may remain absent or rejected while the daily-use release is usable. Its adoption is a separate R&D disposition. The owner explicitly requires meaningful technical distinction: if this hypothesis loses or stays unmeasured, that requirement remains OPEN. Working ordinary skills do not complete the full owner ambition; they permit useful delivery while the distinction work remains visible.
Open gaps: exact Hermes hooks for event/revision propagation and safe cancellation; native/Hindsight correction and extraction ownership; complete combined accounting; camera/calendar audience mapping; actual held-out benefit; distribution/license fit of research implementations.
No assumption that a paper's reported score transfers to Eva, that reflective text changes behavior, or that model construction is cheaper than ordinary execution.
Implementation ownership follows the root architecture/roadmap; no second workboard, scheduler or benchmark framework is introduced here.

## Sources and prior art reused

[Technical differentiation research](../../research/2026-09-27-system-choice/technical-differentiation.md) supplies the prior-art boundaries: FlowEvo/Artic compilation, SkillGen synthesis and held-out comparisons, Anything2Skill/SkillOps conditions and lifecycle, StateAuditor/STALE correction dependencies, TRACE operational corrections, VASO constrained verification, and Twin executable models.
Twin already builds/repairs executable models and can use more total tokens than its no-model ablation; that is direct counterevidence to an automatic cost-saving claim. Abstract/selected-section coverage and license uncertainties remain as recorded, not upgraded to full review here.
[Building blocks](../../research/2026-09-27-system-choice/building-blocks.md) and [baseline](../../research/2026-09-27-system-choice/baseline.md) support reuse of platform skills, Hindsight, LiveKit, the general HA route, existing tools and effect evidence.
This synthesis reread those existing research documents and the current owner/workboard opening; it made no new literature search or runtime observation. Design effort estimate: one synthesis plus bundled review, 15–25 minutes; not a credit-price fact.
