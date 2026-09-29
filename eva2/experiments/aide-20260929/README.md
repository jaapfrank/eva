# AIDE application in Eva — bounded trial closed without a candidate

Owner priority, 29 September 2026: record this work in the project, then finish the easiest useful deliveries first. This is part of L3, not a new platform or an additional product milestone.

## Existing capability and proposed change

Eva already has `draft_component`, `ComponentEvaluator`, and `prepare_component_release`: atomic proposals, isolated baseline/candidate execution, and a candidate bound to its report. The historical SHA256 improvement from 71/90 to 90/90 was assisted; it does not establish autonomous self-improvement.

Original AIDE searches task programs. AIDE² also searches the agent harness with fixed underlying models. [Research and primary sources](research.md) explain the distinction and limitations. Eva has demonstrated neither AIDE²-style recursive improvement nor a conversational speed advantage.

The proposed bounded target is existing `PostgresDelegatedWorkActions.active_context`: question-blind selection can omit a named task beyond its eight active / three recent entries. Reuse the existing authorized metadata route; do not replace memory or introduce a second evaluator.

## Frozen trial and current outcome

[Criteria](criteria.json), [development cases](dev-cases.json), [development scorer](dev_score.py), [execution harness](context_suite.py), and [independent holdout commitment](holdout-commitment.json) were prepared before candidate generation. The optimizer must not read or edit held-out data or scoring rules. Choose a candidate from development results, freeze it, and allow one independent final comparison. Privacy, authority and stop semantics are gates. A tie or loss is valid; only measured improvement may be integrated.

Two already-started Astra/medium candidate requests ended with `CodexProviderError` after approximately 121.4 seconds each. Neither returned a valid candidate. Token usage and subscription credit cost were unavailable. No candidate evaluation, final comparison, product source change or installation occurred. The failure does not establish that AIDE is unsuitable.

## Final outcome — 29 September 2026

The original trial is closed as unsuccessful. Both Astra requests were terminal failures, with no candidate. No unchanged third request was made. The current target still matches the frozen baseline SHA-256, so the existing `ComponentEvaluator` was used once to evaluate that baseline against an identical copy on the frozen development cases.

Both legs report zero detected hard-check failures, 3/5 required task references included, and 4,017 context bytes. The two omitted references confirm the development selection gap; these are metadata-selection scores, not user-answer quality or tokens. The leg named `candidate` by the evaluator is an identical baseline control, not a generated improvement. No held-out data was read or evaluated, and no answer-model comparison was run.

No product code, installation or promotion follows. Independent review approved this limited interpretation. Earlier candidate tokens and subscription credits remain unknown; this closing measurement used no model calls. See the [bounded outcome](../../evidence/product/aide-bounded-outcome-20260929.json).

## Further application

Keep the existing proposal/evaluation/release facilities. A future attempt needs a concrete changed cause or revised experiment decision and an explicit accounting of the original allowance; do not reset the budget or assume the unused refinement is a fresh draft allowance. The current failure does not establish that AIDE is unsuitable. Whole L3 and the technical-differentiation requirement remain open.

Original AIDE program search, AIDE² harness evolution, and Eva's demonstrated results remain separate: this trial produced no improved candidate and demonstrates no recursive self-improvement. Do not build a general optimizer to conceal a failed bounded trial.
