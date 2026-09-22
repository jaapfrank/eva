# Durable private result destination and send fence

Base: `1b508456`; branch `codex/delegated-private-destination`. This implements the
bounded missing return-route binding for step 3 of the continuing product goal.
No provider requests, Telegram messages, production database changes, dependency
changes or installation were performed. Coordinator owns composition, transport,
independent review, integration and live verification.

## Problem and implementation

A saved task previously had no explicit authorized return destination. Its episode
and parent hashes cannot safely identify a Telegram recipient. Reusing that absence
as a routing guess could expose a private result or deliver it to a changed account.

`PrivateWorkDestination.from_telegram_turn` preserves the exact origin tenant,
installation, principal, conversation and message plus owner/binding UUIDs and
explicit Telegram sender/private chat. The current trusted Telegram binding must
match, the turn must have a private-person audience, and its conversation must be
exactly `telegram:<chat_id>`. Private IDs follow the existing transport's 16-digit
bound. A website turn, shared speaker, group, claimed name or changed binding is
not a substitute. The factory is for trusted connector composition, never model
arguments or a client-selectable route.

`PostgresDelegatedWorkStore.admit(..., delivery_destination=...)` records a separate
append-only operational-timeline event in the same transaction as task admission.
The envelope binds destination to the exact work ID and existing spec SHA-256.
No field in v1 or v2 task specifications changes; stored specs are not rewritten.
A matching retry is idempotent; a different destination, removing a saved destination,
or backfilling an originally unbound task fails. A resumed successor needs its own
explicit current origin; do not inherit an old destination by guessing. Repeating
resume from a different message cannot rewrite the first successor's origin.

## Executable API

```python
destination = PrivateWorkDestination.from_telegram_turn(
    turn, owner_principal_id=owner, principal_binding_id=binding,
    telegram_user_id=sender_id, chat_id=chat_id, bindings=current_bindings,
)
work_store.admit(..., delivery_destination=destination)
store = PostgresDelegatedWorkDeliveryStore(
    work_store, bindings_loader=load_current_bindings, clock=whole_utc_clock,
)
claim = store.claim_next(now=now, lease_seconds=120)
```

`load(work_id, owner_principal_id=..., principal_binding_id=...)` returns the exact
current destination or `None` for an unbound task. `authorize_delivery` uses the same
arguments and returns `AuthorizedWorkDelivery` only for a saved succeeded/verified
result. It supplies destination, work_id, spec_sha256, result_sha256, complete output
and stable operation_id. Its equality includes result and destination bytes.

Both reads validate current DB owner authority, retained result lifetime, and the
existing `PostgresPrivateTelegramRecipient` checks for effective verification,
active/private provider binding, principal/binding expiry and current Telegram
recipient. Clock defaults to current whole UTC seconds and is injectable for tests.
This is a current read, not a perpetual grant: reauthorize before each external send
and recheck the exact recipient at the sink. The delivery worker must compare the
claim operation_id to that authorization and check claim.deadline before each part.

`PrivateWorkDeliveryClaim` contains work_id, owner_principal_id,
principal_binding_id, claim_id, operation_id, deadline, revision. `acknowledge(claim,
receipt, now=...)` records the already verified transport receipt string;
`mark_ambiguous(claim, error_code, now=...)` records a bounded code such as
`TimeoutError`, never exception text or credentials. `status` exposes unbound,
awaiting_result, ready, delivery_claimed, delivery_acknowledged or delivery_ambiguous
subject to the same current private authority.

## No uncertain resend

The immutable destination and mutable delivery lifecycle use separate prefixes on
the existing timeline. Claiming uses a PostgreSQL work-row lock with SKIP LOCKED,
then commits a unique claim before an effect. Only successful retained tasks with a
destination enter the queue. An expired claim becomes needs_help/ambiguous; it never
becomes sendable again. A timeout or partial transmission is terminal ambiguous.
No claim retry, extra scheduler, fake sensory event or provider readback is added.

The same successful acknowledgement is idempotent; a different receipt or claim is
rejected. An acknowledgement after the deadline becomes ambiguous. A crash after
external acceptance and before acknowledgement preserves uncertainty and the stored
result, without sending again. The coordinator's existing idle loop owns run_once;
a stable operation ID alone is not treated as Telegram sendMessage idempotency.

## Verification

- **20 new tests passed in 7.49 s**: strict private origin/roundtrip, oversized private
  IDs, shared-device/website rejection, transaction rollback, immutable retry and
  no historical backfill, exact result and current binding, competing claims,
  forged/conflicting acknowledgement, restart/crash/timeout/late acknowledgement,
  unchanged stored result, and four authority/retention expiry cases.
- **7 existing owner-service tests passed in 4.85 s** with no destination configured,
  preserving prior admission, resume, result, stop and alias behavior.
- PostgreSQL 16.13 disposable databases and existing synthetic fixtures; no live
  records. Earlier development failures were test-fixture shape/import naming and a
  missing result-hash property; corrected code uses canonical result payload hashing.
  No existing assertion was relaxed.

New test files are `tests/application/test_private_work_destination.py` and
`tests/postgres/test_delegated_delivery_store.py`, avoiding duplicate pytest module
names with the coordinator's operation-worker tests. Receipts are included here.

## Integration and rollback

Coordinator wires the optional owner-service destination factory and existing idle
loop's completion worker. The older disabled path stays valid. Preserve both
sidecar and lifecycle timeline events through rollback: disabling new delivery is
safe, deleting send fences or backfilling historical destinations is not. A prior
binary ignores these sidecars but must not be used to invent delivery or replay an
uncertain result. No schema migration is required. The sealed research program hash
must follow normal coordinator release handling if included source bytes changed.

Source tests are not a live private-channel acceptance claim. Unknown speakers and
household devices still cannot receive private owner results. The approved existing
communication mandate must cover the explicitly bound destination.
