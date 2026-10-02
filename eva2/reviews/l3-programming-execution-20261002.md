# L3 programming execution integration review — 2 October 2026

Decision: GO for the bounded optional execution dependency, not L3 completion.

Independent reviewer `/root/l3_application_seam` inspected host/client, admission,
shared delegated composition, completion and stop. Root independently inspected
the agent-owned host module. Review found and resolved missing capability admission,
launch-only completion, mandatory single-file guidance, disabled feature database
initialization, and replay-only process ownership. Trusted upstream idle status is
checked before task completion; starting/interacting alone cannot prove completion.

Verification: 103 focused service/runner checks, 22 composition checks, 20 host
checks, plus the real pinned MCP/image smoke. An earlier test environment lacked
tiktoken; the complete existing runtime test environment passed. No remote models
or private household data were used in these checks.

The actual source target in the programming image matches the frozen current
reference. Containers share no credentials, host source checkout, socket or held-out
criteria with the optimizer. Explicit stop kills the container; workspace files
survive. Restart reconciliation is scoped to the trusted root/image. Unknown effects
are not automatically replayed. Existing component evaluator/procedure service and
core/worker profiles remain the application boundary.

Limitations: isolated programming execution is not autonomous installation, semantic
correctness, independent measured improvement, a new integration host, or superiority
against Eva1/competitors. Those remain the existing L3 outcome, separately open.

Follow-up: root checked the actual `pending_step` replay route and found that it
calls `pending()` without `execute()`. Session ownership now starts in `pending()`
when a durable result is found; the regression uses that exact route. Ten service
checks passed and the independent reviewer confirmed GO for the minimal overlay.
The development installation was corrected directly; no rollback or data reset.
