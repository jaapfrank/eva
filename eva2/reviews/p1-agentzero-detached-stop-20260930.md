# P1.1 detached SDK stop — bounded review

Reviewer: `/root/review_telegram_draft_lane`. **GO for the bounded correction.**
No additional test, model request or native replay was performed by the reviewer.

The retained native full-monologue case shows a helper pending after parent kill.
Sample stop changes bound active SDK calls from one to zero and retains unknown
cancelled usage as null. This is local mocked SDK evidence, not cloud or whole-task
quiescence. Source hashes match the preceding native pin.

The initial queued callback could cancel a task after its SDK attempt settled and
it moved to unrelated work. The correction rechecks exact attempt binding and
pending status under the lock. The new regression covers later work in the same
task. Evidence preserves executed hashes and the subsequent corrected hashes;
replaying the native case is unnecessary.

Live admission, full task/cloud quiescence and P1.1 remain open.
See `eva2/evidence/product/p1-agentzero-detached-stop-20260930.json`.
