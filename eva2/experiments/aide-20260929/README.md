# AIDE application in Eva — bounded work, queued

Owner priority, 29 September 2026: record this work in the project, then finish the easiest useful deliveries first. This is part of L3, not a new platform or an additional product milestone.

## Existing capability and proposed change

Eva already has `draft_component`, `ComponentEvaluator`, and `prepare_component_release`: atomic proposals, isolated baseline/candidate execution, and a candidate bound to its report. The historical SHA256 improvement from 71/90 to 90/90 was assisted; it does not establish autonomous self-improvement.

Original AIDE searches task programs. AIDE² also searches the agent harness with fixed underlying models. [Research and primary sources](research.md) explain the distinction and limitations. Eva has demonstrated neither AIDE²-style recursive improvement nor a conversational speed advantage.

The proposed bounded target is existing `PostgresDelegatedWorkActions.active_context`: question-blind selection can omit a named task beyond its eight active / three recent entries. Reuse the existing authorized metadata route; do not replace memory or introduce a second evaluator.

## Frozen trial and current outcome

[Criteria](criteria.json), [development cases](dev-cases.json), [development scorer](dev_score.py), [execution harness](context_suite.py), and [independent holdout commitment](holdout-commitment.json) were prepared before candidate generation. The optimizer must not read or edit held-out data or scoring rules. Choose a candidate from development results, freeze it, and allow one independent final comparison. Privacy, authority and stop semantics are gates. A tie or loss is valid; only measured improvement may be integrated.

Two already-started Astra/medium candidate requests ended with `CodexProviderError` after approximately 121.4 seconds each. Neither returned a valid candidate. Token usage and subscription credit cost were unavailable. No candidate evaluation, final comparison, product source change or installation occurred. The failure does not establish that AIDE is unsuitable.

Further calls are deferred under the owner's new priority. Preserve the bounded allowance rather than restarting it: two initial requests have been consumed; at most one refinement remains under the original ceiling. No blind retry. Before resuming, diagnose the concrete provider failure or obtain a revised experiment decision. Baseline/hash changes require revalidation before comparison, not silently using stale evidence.

## Next execution

Finish useful product outcomes first. Then resume L3 only with a concrete user-facing deficit, applicable baseline, fixed evaluation and remaining budget. Report useful answer quality, human help, speed and measured usage separately from context bytes and local scorer results. No claim of improvement exists yet.
