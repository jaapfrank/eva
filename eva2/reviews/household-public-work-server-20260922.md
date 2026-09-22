# Independent review: household public work server

Decision: **GO for the disabled server source slice** at commit
`77640ad8b7cf6579d5a16b27ae2e8fab428e8db4`, based on
`5bfbdbd6b772f29a61b4ad3be5f20fbea9b5dbdd`. Reviewed on 22 September 2026.
Reviewer task: `01a0c915-205e-7c11-809a-674ae09e016c`.
No blocking findings in the reviewed scope. This decision does not cover later
HTTP/media changes, runtime activation or owner acceptance.

## Scope and findings

Read all 13 changed files and the builder's
[delivery evidence](../evidence/product/household-public-work-server-20260922.md).
The v3 envelope, original ingress, destination and admission receipt share the
existing admission transaction. Server-issued sessions bind the device actor,
household, policy and originating conversation; the sponsoring owner is not
represented as the speaker. Canonical ingress retries retain the first receipt.

Private reads, lists, boards, latest-stop selection and delivery reject or filter
household work. Household access rejects private work with the same sponsor and
work belonging to another session/device. Public research uses the existing
closed argument contract without recalled private context, arbitrary workspace
access or a new provider route.

Current voice/research policy, provider authority and resource bounds are checked
before admission, claim, call start, tool dispatch and result release. Session
close/expiry blocks exposure while still-authorized background work may finish.
Revocation prevents subsequent dispatch/output. Measured usage from a completed
dispatch is recovered from its existing bound journal when revocation prevents
observation; the call is not replayed.

Delivery uses one atomic claim and separate offered/completed/interrupted/ambiguous
acknowledgements. Interrupted or uncertain delivery does not create a new claim.
Acknowledgement does not prove human hearing. Retained results and replayed action
receipts remain subject to current session and authority checks.

The feature requires explicit trusted composition and is off by default. It
reuses the current store, queue, research adapter and worker. The distinct
household program seal includes its policy, facade, session ledger, voice
authority and audience dependencies. Changes to shared store/contract/worker
sources also change the existing v1/v2 program seals: later installation must
pair core and worker and include every new household module. This source review
is not evidence of deployed image compatibility.

## Independent validation

Used the existing disposable PostgreSQL fixture and candidate source mounted
readonly, with the deterministic research provider. No live provider/model calls
or production service changes were made.

```sh
python3 /tmp/eva2-household-public-tests.py core \
  tests/postgres/test_household_public_work.py \
  -k 'revocation_during_call or atomic_claims or private_task_id or missing_origin' \
  --tb=short --show-capture=no
```

Result: **4 passed, 12 deselected in 5.07 seconds**. These checks cover measured
usage retention during revocation, concurrent claims, same-sponsor private
isolation, and missing/corrupt origin/result evidence. The helper uses the source
at `/home/administrator/project/eva2-household-public-work/eva2`; another checkout
requires a separate helper pointing to that checkout.

The builder separately reports 16 new PostgreSQL chain tests and the private
regressions listed in its evidence. Five broader store tests failed and were
reproduced on unchanged earlier source; this review preserves those failures and
does not claim a fully green repository suite. The entire gate was not rerun.

## Next integration boundary

HTTP/media wiring remains open. Select the household or private action service
through the existing audience boundary before composing descriptors: their four
overlapping action names must not become duplicate providers. Authenticate the
connector and choose the provisioned device/session before accepting model
arguments.

Polling and delivery authorization must not call the journal-admitting `turn()`
every five seconds. Reuse the admitted originating turn or construct a pure,
server-bound control turn. Validate the saved task's original ingress separately.
The next delta must prove that repeated empty/result polling and authorization/
acknowledgement add no `INGRESS_COMMITTED` rows, while preserving current lease,
authority and cross-session rejection. Real user turns keep canonical admission.

Reauthorize before output chunks; exclude revoked cached results from model
context; keep speech interruption separate from task stop. Actual authenticated
HTTP transport, media chunk/cancellation behavior and registered physical-device
trials remain unverified by this server slice. Runtime installation, live checks,
rollback and owner acceptance remain separate statuses.
