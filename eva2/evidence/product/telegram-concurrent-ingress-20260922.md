# Telegram canary concurrent ingress — review handoff

## Finding and source provenance

The actual `/eva2` ingress is `eva.service`, running `/usr/bin/node src/index.js`
in `/home/administrator/project/Bottie`, not the native Python Telegram worker.
The observed process was PID 56106, started 2026-09-21 12:56:28 CEST. Bottie HEAD
was `909bff513d7d7fd653d6a7f270d9e73facce430d`, with an existing modified
`src/integrations/eva2canary.js` and unrelated deleted files. None were modified.
File hashes and timestamps are recorded in the adjacent provenance JSON. Disk
timestamps predate process startup; this is not an in-process module attestation.

The installed grammY `Bot.loop()` awaits `handleUpdates()`, which serially awaits
each handler. The production text handler awaits `askEva2Canary()` through final
reply delivery. Therefore the next Telegram update is not admitted while the
previous response runs. The coordinator supplied read-only event metadata:
17:00:39Z reply completed in 26313 ms (first text 20538 ms), followed by a
17:00:44Z reply completed in 3503 ms (first text 2516 ms). This is consistent with
the queue but is not by itself a causal trace or a verified private-text match.

The core canary endpoint is synchronous FastAPI work dispatched independently.
Its journal execution fence is keyed by tenant, installation and the message's
thread identity, rather than the entire chat. The added API tests verify same-chat
overlap through both JSON and NDJSON routes with a controlled blocked runtime.
No production core scheduling change was necessary for the demonstrated ingress
blockage. These API tests do not measure live model latency or exercise an actual
provider and PostgreSQL journal together.

## Candidate and integration boundary

Isolated Bottie worktree: `/home/administrator/project/bottie-telegram-concurrent`.

- `e82f71db`: baseline capturing the existing live deadline overlay, unchanged.
- `77fdfc85`: candidate, four files; production changes are `telegram.js` and the
  new `telegrampolling.js`; the remaining two files are focused tests.
- `telegram-concurrent-ingress-bottie-20260922.patch`: exact candidate diff against
  the captured baseline. Apply at the Bottie repository root, not Eva2 root.
- Eva2 candidate base: `26ef4d7f994a541ed226ee5626f7581ab84d8210`; only API tests
  and these evidence files change in Eva2.

Polling uses public grammY initialization/API/handler methods. Up to eight updates
are active. Explicit `/eva2` commands may overlap; ordinary legacy updates retain
one serial lane. All updates still traverse the existing allowlist, immutable
archive, processing claim, route isolation and durable reply deduplication.
There is no greeting/content classifier, alternate model route or retry added.

The Telegram offset only passes a contiguous completed prefix. Pending updates
are therefore replayable after process loss. In-flight and completed entries in
the current window suppress repeated dispatch; persisted claims/outbox handle
replay across processes. Polling shutdown/fatal errors drain admitted work rather
than cancelling it. The live deadline overlay remains byte-identical.

Capacity is bounded: eight active handlers and Telegram's 100-update replay
window. A saturated window can still queue later work. This change is not a
guarantee of immediate response under overload or an exact-once guarantee for
arbitrary external providers. Reply order may differ from input order; every
reply remains attached to its own original message.

## Verification

No model/provider calls, Telegram messages, live source edits, service restarts,
deployment, or production database writes were performed. Node dependencies
were copied from the existing installation into the isolated checkout; no package
installation occurred. Python tests ran in a disposable container against local
controlled fixtures.

`node --test test/telegram-concurrent-ingress.test.js
test/telegram-concurrent-polling.test.js test/telegram-transport.test.js
test/telegram-message-archive.test.js test/telegram-dialogue-route.test.js`:
**32 passed**. The final stronger ingress test was also rerun successfully.

The actual `startTelegram()` registration, real grammY middleware and real isolated
SQLite archive/outbox are exercised. Telegram API and canary HTTP transport are
local controlled substitutes. First core request blocks; the second arrives in a
later polling batch, answers before first release, and leaves the first running.
Another principal remains isolated; an unauthorized sender never reaches core;
repeat polling and persisted completed-message replay produce no extra core call
or delivery. After release, the first reply arrives exactly once on its own
message. Tests also cover the concurrency cap, legacy lane, fatal polling errors,
handler failures, offset safety and shutdown draining.

A controlled serial-dispatch mutation (`await task` after scheduling) fails that
same ingress test: only the first request starts, no second reply arrives, and
polling never advances. This is a deterministic mechanism reproduction, not a
live before/after benchmark.

`tests/api/test_telegram_canary_api.py`: **12 passed**, including the two added
blocked-first JSON/stream cases. `git diff --check` is clean in both worktrees.

Existing `test/eva2-telegram-canary.test.js`: **10 passed, 2 failed**. Both failures
expect a 503 retry removed by the pre-existing live deadline overlay. That module
and test file are byte-identical between the candidate and captured baseline;
this patch does not reintroduce retries to satisfy stale expectations.

Coordinator owns independent review, integration, deployment and live verification.
