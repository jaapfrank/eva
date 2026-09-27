# Technical differentiation: change-aware reuse of experience

Status: research proposal, 27 September 2026. No implementation, installation,
new Eva inference, measured advantage or global novelty claim. This extends the
system-choice research; it is not a second workboard or an approved architecture.

## The proposed contribution

Eva should become cheaper at familiar work while remaining responsive to changes
in a person's circumstances. We propose an optional module connecting the
assumptions behind memories, open intentions, task plans and reusable skills.
A relevant change would make only dependent decisions ineligible for reuse until
rechecked. Conversation, initiative and workers would observe the same revisions.
The selected platform would remain the sole execution owner; this is not another agent
framework, scheduler or replacement store of all family data.

This is a product and systems hypothesis. Dependency graphs, skill compilation,
validation and memory repair are established techniques. Their combination is not
automatically an invention. The proposed advantage must be measured against the
closest available alternatives, including their ordinary memory/skill mechanisms.

## Mechanism, expressed as behavior

1. Preserve a source-linked experience: intended outcome, observed starting state,
   relevant assumptions, action/result references, affected person/audience,
   provider versions and measured resource use. Observation is not proof of a
   causal effect. Retain source evidence when deriving a lesson.
2. Reuse upstream skill synthesis/compilation for bounded procedures. A skill has
   applicability conditions and a way to observe its outcome; successful prose
   alone does not establish success. General reasoning handles novel work.
3. Link selected assumptions to the plans and skills that actually depend on them.
   A revision triggers selective re-evaluation before the next relevant action.
   Newer evidence alone does not prove that an older preference is superseded.
   A temporary exception must not erase a durable preference.
4. Run eligible, verified steps with less repeated model reasoning. Revalidate at
   relevant action boundaries; a changing tool, preference or audience can revoke
   eligibility. Re-evaluation never automatically repeats an external effect.
5. Promote learned procedures only when they help held-out variants. Include the
   cost of learning, validation, retrieval and maintenance when judging savings.
   Improved learning must not block the live conversation or poll all context with
   an expensive model continuously.

Example: a changed working schedule invalidates the assumptions of affected
planning or house tasks while leaving unrelated knowledge and skills usable.
The experiment tests whether the system finds the affected dependencies; it must
not secretly encode a specific working-schedule or household script in the core.

The integration boundary needs revision notifications from authorized memory and
tool sources, references to existing task/effect records, and an eligibility result
with evidence. Exact wire schemas follow platform selection, not this proposal.
The source stores remain authoritative. Learned artifacts must not widen access
or carry private information into shared household contexts.

## Prior art checked, including counterevidence

| Source | Existing overlap | Consequence |
|---|---|---|
| [FlowEvo](https://arxiv.org/html/2607.21596v2) | Workflow-to-executable-skill compilation, compatibility checks, tests, feedback and suppressing harmful skills. | Compilation and cheap direct execution are not an Eva invention. Author code exists; its shortened license text needs clarification before reuse. |
| [SkillGen](https://arxiv.org/html/2605.10999v1) | Skill synthesis from successful/failed traces, paired comparisons and held-out evaluation. | Reuse candidate; learning plus verification is not itself distinctive. Author code has Apache-2.0. |
| [Anything2Skill](https://arxiv.org/html/2606.09316v3) | Structured conditions, contraindications, evidence and versioned skill lifecycle. | Adding this metadata is insufficient technical differentiation. |
| [SkillOps](https://arxiv.org/abs/2605.13716) | Typed contracts and dependency/compatibility maintenance with low library-time model use. | A dependency graph alone is not new. Abstract inspected; integration and license not established here. |
| [StateAuditor](https://arxiv.org/abs/2608.01619) | Repairing implicit stale dependencies from dated evidence; provenance checks do not prove semantic supersession. | Preference-change handling is prior art too. Abstract inspected; its harder lifecycle set reports no gain. |
| [TRACE](https://arxiv.org/abs/2606.13174) | Compiling user corrections into runtime checks for coding agents. | Applying remembered corrections operationally is also prior art. Abstract inspected. |
| [STALE](https://arxiv.org/abs/2605.06527) | Evaluation of outdated assumptions and downstream memory invalidation. | Useful evaluation pattern, not proof that Eva solves the problem. Abstract inspected. |
| [VASO](https://arxiv.org/abs/2606.05395) | Formal verification guiding evolution of robot skill contracts. | Formal skill checking is not novel either; specified robot models do not prove arbitrary household behavior. Abstract inspected. |
| [Artic](https://arxiv.org/abs/2608.21341) | Compilation of natural-language workflows into validated executable artifacts. | Reuse must be evaluated before developing a new compiler. Abstract and selected appendix inspected. |

FlowEvo's arXiv history displays v1 as 18 April and v2 as 20 August 2026 despite
its 2607 identifier; record the source as displayed rather than deriving the date
from the identifier. SkillGen is dated 9 May, Anything2Skill v3 19 June, SkillOps
13 May, StateAuditor 3 August, TRACE 11 June, STALE 7 May and VASO 3 June 2026.
Some papers have only abstracts/selected sections inspected as indicated; this is
not an exhaustive literature or patent search. No reported benchmark score is
used as an Eva speed, cost or quality prediction.

## Concrete research bet: executable experience with an explicit validity boundary

The owner requires a technically distinctive, intelligent and upgradeable product,
not novelty language applied to ordinary integration. The existing hypothesis can
be made concrete through a bounded learned task model. This is a refinement of the
same proposal, not an additional platform, approved invention or build assignment.

For repeated multi-step work, Eva could learn a small executable model from an
API contract and observed state transitions. The artifact would describe relevant
state, possible actions, predicted observable effects, unknowns and applicability
conditions. A skill could use that model to compare candidate plans before acting.
Predictions would be recorded before observing results; discrepancies would revise
only affected knowledge. Version and context changes would reopen the relevant
assumptions rather than silently replay a formerly successful procedure.

The difficult contribution is selecting a useful state representation and knowing
where its predictions cease to be supported. Neither successful historical replay
nor an LLM's confidence proves correctness on unseen states. Hidden state and
concurrent activity must remain explicit unknowns. When an uncertainty matters,
prefer an informative read or an already authorized isolated trial; learning does
not grant additional rights. Ordinary conversation and simple known actions do not
need to pay for model construction or simulation.

One demonstrator would learn an unfamiliar service procedure, complete a new input,
and then encounter a changed tool contract or temporary owner exception. It must
recognize the affected assumption, obtain the missing observation, adapt the
procedure and verify the actual result without a developer adding task-specific
core logic. This is a proposed demonstration, not a claim about current Eva.

Reuse existing synthesis, storage and execution components. An optional learned
artifact must declare its schema version, source references, dependencies and
outcome checks. Exportable artifacts and conformance checks would allow a different
reasoning model or provider to use the same experience. Compatibility with an
arbitrary future model cannot be guaranteed. Memory, planning and initiative could
consume the same evidence, subject to existing person and audience boundaries.

### Closest additional prior art and reasons for restraint

[Twin](https://arxiv.org/html/2608.14490v1), published 14 August 2026, already
constructs executable models, validates them against observed transitions, plans
inside them and repairs mismatches. This mechanism is therefore not an Eva
invention. Its evaluation concerns small grid games; deterministic dynamics and
hidden state limit transfer. The paper explicitly says replay does not certify
unseen situations. It also reports higher total processed-token use than its
no-twin ablation: fewer environment actions do not establish lower inference cost.
Abstract, method, limitations and compute sections were inspected; no result was
replicated or transferred to Eva. An independent bounded critique agreed that a
small procedure model is plausible research, while a general household simulator
would be an unjustified expansion.

The research question is whether a small model improves eligibility decisions over
ordinary skill preconditions and regression tests when context or tools change,
with fewer failed real actions and acceptable total learning-plus-use costs.
Measure that marginal benefit against the closest usable implementation. Include
construction, corrections, retrieval and maintenance within the same fixed budget.
Reject this addition if its benefit disappears against that baseline, if it needs
per-task developer scripting, or if learning cannot repay its costs within the
predeclared reuse horizon. Do not expand it into an open-ended simulator project.

## What would establish useful technical advantage

A bounded isolated comparison should use the same model, access, tools and
resource envelope, with held-out variants fixed before candidate generation:

- A previously unseen digital procedure, followed by transfer to different inputs.
- A changed personal fact and a temporary exception; adapt affected work without
  unnecessarily discarding unrelated useful experience.
- A changed tool contract; avoid blindly executing the old procedure.
- A concurrent short conversation, correction or stop during ongoing work;
  preserve original result destinations and completed effect identities.

Compare ordinary upstream memory/skills, the closest usable prior-art mechanism,
and the proposed coupling. Also disable the coupling as an ablation. Measure
complete outcomes, stale actions, unnecessary invalidations, human help, contact
latency and total learning-plus-use model/tool consumption. A lack of gain is a
valid result: reuse upstream, do not extend a failed idea indefinitely. This is a
comparison specification to bound in the next plan, not authorization to launch
an expensive benchmark sweep now.

No percentage improvement or claim of universal novelty is established. Current
read-only research incurred no Eva runtime calls; development credits are unknown.

Review: an independent bounded review checked the proposal and owner section.
No blocking findings; clarified that the execution platform is not selected yet.
The reviewer directly checked FlowEvo/SkillGen; other source coverage is marked
in the table above. This is research review, not a product acceptance test.
