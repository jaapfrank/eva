# AIDE / AIDE2: bounded reuse assessment

Checked UTC: 2026-09-29T09:16:35Z. Research only: no installation, runtime model calls, or product changes. The coordinator owns experiment selection and admission.

## Distinction and evidence

**Original AIDE**, [2502.13138v1, 18 February 2025](https://arxiv.org/html/2502.13138v1), searches candidate task programs using draft/debug/improve operations and execution feedback. It targets ML engineering, not a household agent. On Weco-Kaggle Lite, its mean estimated human percentile was 51.38 versus H2O's 35.34; differing test splits and possible training contamination limit that comparison. Its cited MLE-Bench/RE-Bench findings belong to those external evaluations. This does not measure Eva quality, privacy, or conversational speed.

**AIDE2**, [2609.26457v1, submitted 22 September 2026 at 14:12:13 UTC](https://arxiv.org/abs/2609.26457v1), searches the research agent's harness. A fixed outer agent proposes rewrites; accepted versions become the next edit target. Models remain fixed. The reported eight-day, 99-proposal run accepted seven changes, lifting selection grade 0.703 to 0.778. External benchmarks test transfer separately; ALE-Bench rose from 1536 +/- 33 to 1790 +/- 9, versus human-engineered AIDE's 1511 +/- 35. Improvements include search diversification and context compression. The experiment promoting an evolved agent to outer-loop improver was inconclusive. This is not demonstrated runaway improvement. Selection feedback is not an untouched final test. Section 3.5 also reports a scoring-script repair, so not every accepted change was purely agent behavior. Whole-run spending is unspecified in the inspected paper; evaluation dominates proposal cost. Appendix A uses benchmark-specific dollar or step caps, with GPUs for some tasks. No base-model weight training is required for harness search. These findings establish no family-use advantage. [Methods, results and limitations](https://arxiv.org/html/2609.26457v1).

## Exact public implementation inspected

The supplied [aideml repository](https://github.com/WecoAI/aideml) describes itself as the original algorithm's reference build. GitHub's commit API resolved `main` to **60b3978ddf65b71f86eb7c64506965048a1398cf**, committed **2026-09-03T15:58:12Z**, subject `fix(metric): reject NaN validation scores (#96)`. This is a checked source pin, not a claim that it reproduces AIDE2. An AIDE2 source release and code license were not identified in the supplied paper/abstract/repository; their availability remains unverified.

| Pinned source | Verified behavior | SHA-256 |
| --- | --- | --- |
| [aide/agent.py](https://github.com/WecoAI/aideml/blob/60b3978ddf65b71f86eb7c64506965048a1398cf/aide/agent.py#L162) | Initial drafts, bounded debugging, greedy best-node improvement; lines 397-442 ask an LLM to extract the metric from execution output. | `c97033ea8c5cad618bcf4063fbd2a426598dfb2bee4f3a9e10d5ea8af258072c` |
| [aide/journal.py](https://github.com/WecoAI/aideml/blob/60b3978ddf65b71f86eb7c64506965048a1398cf/aide/journal.py#L24) | Parent/child candidates, execution evidence, metric ranking; lines 229-239 summarize all good nodes, not bounded AIDE2 memory. | `6539cac937ef65af1ee6f05c6434ec66c2d3c268460c74f13eba4fa7bf9b6020` |
| [aide/interpreter.py](https://github.com/WecoAI/aideml/blob/60b3978ddf65b71f86eb7c64506965048a1398cf/aide/interpreter.py#L118) | Child-process execution with ordinary Python builtins and timeout handling; this module is not an authority sandbox. | `a0b6a16302dd15bd189be79c604bf2300870e174370c59243114ad10a2b2b7b6` |
| [aide/utils/config.yaml](https://github.com/WecoAI/aideml/blob/60b3978ddf65b71f86eb7c64506965048a1398cf/aide/utils/config.yaml) | Defaults: 20 steps, five drafts, separate code/feedback/report models. Defaults are not an Eva cost estimate. | `7ae409fbb429cc5a7a07a902abbd7c97286c4a1614028af54a1e8783e522f8b7` |
| [LICENSE](https://github.com/WecoAI/aideml/blob/60b3978ddf65b71f86eb7c64506965048a1398cf/LICENSE) | MIT, Weco AI Ltd 2024; preserve copyright/license notice when reusing substantial code. | `723b16d1701dfb2d280d229ff84f3056df65b6b00ad8419ec6cb7da2fdd8f616` |

Also inspected [backend routing](https://github.com/WecoAI/aideml/blob/60b3978ddf65b71f86eb7c64506965048a1398cf/aide/backend/__init__.py#L10): model names select OpenAI/Anthropic/Gemini/OpenRouter, with a base-URL override. Compatibility with Eva's existing subscription transport was not tested and is unnecessary for methodological reuse.

## Eva experiment recommendation (our proposal)

Reuse Eva's existing component candidate/evaluator/release path; introduce no second task owner or AIDE runtime. The coordinator should establish the target's concrete gap before freezing the experiment. Context selection is suitable only if existing evidence identifies a measurable relevance or unnecessary-context problem.

1. Freeze baseline source, one editable component, evaluator, development cases, separate sealed cases, objective, and finite candidate/call allowance before generation. This experiment allowance is not a new daily user quota.
2. Let Eva propose a small number of atomic candidates from development feedback. Store each candidate's parent, source hash, stated hypothesis, execution result, and measured resources using existing artifacts. Do not count generated code or its self-reported score as success.
3. Use the existing isolated evaluator. Candidate execution must not read sealed expectations, edit tests/evaluator, access production memory, call household services, or install itself. Keep privacy/authority checks as mandatory constraints, not a score trade-off.
4. Select using development results, freeze one candidate, then compare baseline and candidate on the untouched set under identical inputs and resource constraints. For context selection, score necessary evidence retention and irrelevant evidence inclusion; reducing bytes alone is insufficient. If the final set informs another revision, it has become development data and a fresh final set is needed.
5. Record ties and losses. Measure all generation/evaluation calls, input/output/cache usage where available, elapsed time and local execution resources; unknown subscription credit cost stays unknown. A component result does not prove faster conversation, improved answers, household acceptance, or repeated recursive self-improvement.

No upstream installation, GPU training, broad evolutionary search, automatic production promotion, or rewrite of Eva's planner is justified by this research pass. A measured candidate can follow the existing independent review and release process; this note is not an experiment result or deployment decision.
