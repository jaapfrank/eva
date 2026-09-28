# Scheduled project correction reproduction

## Scope

L2, base `3c85f7aa`. Reuse the existing scheduler, correction journal and linked
work attempts. No replacement planner or model calls. The probe deliberately
asserts the observed broken behavior; a passing probe is **not** acceptance.

## User-visible gap

A project contains A (prepare a document) followed by B (summarize it). Correct A
before completion. Eva queues A's corrected continuation, but B becomes permanently
blocked against A's stopped original attempt. Restarting the scheduler does not
rebind B. The relevant installed source hashes match the inspected worktree.

## Execution

Original defect probe: `test_probe.py` at commit `788af92c`.
The current regression lives in `../../tests/postgres/test_scheduled_work_corrections.py`.
Run that regression with the existing isolated PostgreSQL test runtime and
`EVA2_IN002_TEST_ADMIN_DSN` pointing to a dedicated test server. The imported
fixture creates and removes its own disposable database. Never use the production
server. Dependencies and command convention match the existing scheduler tests.

One execution: reproduction assertions passed in 4.30 seconds. No models,
external actions, production changes, physical devices or document generation.
See `../../evidence/product/scheduled-correction-gap-20260928.json`.

## Smallest repair and completion conditions

Resolve an explicitly recorded correction continuation when evaluating an existing
dependency. This includes the interval after correction is saved but before the
successor is admitted. Keep the original schedule and immutable request provenance.
Do not follow arbitrary other tasks or treat an ordinary stop as permission to resume.

The regression must establish that B waits during correction, receives the final
corrected result after continuation success, and is admitted once across scheduler
recreation. Also preserve explicit chain cancellation and terminal failure without
a continuation. Reuse the existing lifecycle coverage for unrelated behavior.

Already blocked schedules must not be silently restarted. Any repair of historical
work requires distinguishing this exact correction case from deliberate cancellation
and preserving the original owner's mandate. No historical schedule is changed by
this reproduction. General autonomous planning and the actual multi-day user trial
remain separate from this scheduler repair.

## Implemented correction

Source `84cf3fb9` adds a shared per-execution advisory boundary around the existing
stop/save/schedule operations. Dependency evaluation tries that lock rather than
waiting under the scheduler's owner lock. It follows only an explicit recorded
correction and its hash-checked resume schedule, validating execution, source,
parent and generation before transferring a successor result. The original
schedule request stays unchanged.

The boundary prevents concurrent readers from observing a half-finished correction;
it is not a single crash-atomic transaction. An interrupted correction can still
require help. Existing blocked schedules are not reactivated. These limitations
must not be presented as completed general multi-day project recovery.

Five new regression cases and the existing lifecycle/household cases passed
(18 total, 35.62 seconds); independent source review approved the bounded change.
See `../../evidence/product/scheduled-correction-continuity-20260928.json` for
installation status and provenance.
