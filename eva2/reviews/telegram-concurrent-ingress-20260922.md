# Independent review: Telegram concurrent ingress

Reviewer: coordinator, independent of implementation task. Date: 2026-09-22.
Candidate: Bottie 77fdfc85f945de0ece6a12e074da9cda9de2aae2; captured existing runtime overlay e82f71db. Eva2 evidence integrated as 9561d161.

Decision: GO for the bounded source change; installation still pending.

Reviewed the production polling replacement and both new tests. Explicit Eva2 commands traverse the existing middleware concurrently; legacy handling retains one lane. Offsets cannot pass unfinished updates, active replay is deduplicated, and fatal polling exits drain admitted handlers. Replies retain their original message reference. No new model selection, provider retry or greeting classifier is introduced.

Independently executed only the two changed Node test files: 3 tests passed (1.379 seconds runner duration). This exercises actual registration and isolated archive, later-batch overlap, other/unauthorized principals, durable replay, concurrency bounds and fatal-error draining. Telegram and core transport are controlled fixtures; no external messages or model calls were made. The builder's broader 32 Node and 12 API passes are attributed evidence, not repeated reviewer runs.

Limitations: eight active handlers and Telegram's 100-update window can still queue work under saturation. Process-loss recovery relies on the existing archive/outbox; this change does not prove arbitrary external effects execute exactly once. Two existing canary expectations conflict with the unchanged live deadline overlay, as documented in the handoff. No live latency or owner acceptance is claimed.

Install gate: preserve the current dirty Bottie checkout and deadline overlay; compare exact live file hashes to recorded provenance, back up only replaced files, avoid interrupting admitted work, and retain the original polling file for rollback. Review approval here is source-only, not a claim that runtime installation has happened.
