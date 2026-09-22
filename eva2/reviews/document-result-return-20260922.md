# Document result return — independent source review

Date: 2026-09-22
Reviewer: document_private_identity
Author: document_maintenance
Decision: **Source GO** for the eight source files identified below. This is not an installation, test-pass, or owner-acceptance claim.

## Scope and findings

Reviewed the complete source delta and the new PostgreSQL test file. No source edits or tests were performed by this reviewer.

- New document admissions persist a return binding in the existing immutable claim. Original ingress, task identity, owner, goal, plan and existing event hashes remain unchanged. Historical claims without a binding are not backfilled, including admission replay.
- Completion requires a terminal stored goal, both expected artifacts, accepted local availability, matching plan/authority bindings and the actual GOAL_COMPLETED event. This projects existing verified stored results; it does not execute documents again.
- The original source is reloaded. Current private owner, audience, mandate, source integrity and turn-stop checks precede return authorization. Telegram destination and current recipient are checked again before each message part.
- Telegram uses the existing durable delivery claim and background loop. Concurrent claims serialize; expired or uncertain claims become ambiguous and are not automatically resent. No second queue, worker, Telegram client or fabricated research run is introduced.
- Website and voice returns require the original private conversation and episode. Voice additionally retains existing open-session, lease, close and expiry fences. Poll, authorization and acknowledgement reload the document authorization.
- Existing research behavior remains the default when no document source is configured. Runtime composes the document source into existing delivery/session routes; changed imports preserve layer direction.

The initial pairing-revocation test constructed an invalid empty bindings object, potentially proving a transport exception instead of changed pairing. The author replaced it with a valid private chat-99 binding and an explicit assertion. This review finding is resolved.

## Evidence limits

The new tests use real PostgreSQL delivery fences, synthetic terminal document projections and fake transports. They do not prove a newly executed native document task or real Telegram/Voice PE output. Test execution and packaged runtime verification are owned by the coordinator/author. Source GO does not replace those checks.

No generalized performance claim is made. Document candidates reuse existing paged claim reads; this review did not benchmark growth.

## Reviewed SHA-256

| File under eva2/src/eva | SHA-256 |
| --- | --- |
| providers/document_completion.py | dc5f7de7df0c4d5d8437a9e87a5c330dc4a654633e6645221e167245ba6b6631 |
| providers/document_task_actions.py | ae2ed79720bb764153c52d272abfc4caaf6873830f66aabb7d7bc5f908320ddd |
| providers/document_task_input.py | 021afd1b39557c3d3ae502e1098e79741850d4cd03fe552f453d4b33c7bc806a |
| adapters/postgres/delegated_work_delivery.py | 6374d4e31e9488706bc56b265e732b080c71f4e89aae63c8b20e2c423ba7a63c |
| adapters/postgres/private_work_return.py | dc5ec6636fb5bd129ad625a9d794f7e3ed463992cb09dc907ca207eaf33fc483 |
| bootstrap/delegated_work_delivery.py | d266064296d80656b5aca71a439de717b472a39383511ae10fe4216267b6f3d8 |
| bootstrap/runtime.py | 41111a1a2f2058b309e3e2b4158a8b6fab604c6b18a7e70676eea35aba1b221b |
| operations/delegated_work_delivery.py | eb66f1f500a10259fe8d421d772889cda902dc1ca745e00653ff6c8d5347a5e1 |

Reviewed test: eva2/tests/postgres/test_document_completion.py,
SHA-256 d462ee1f2f1a32ba6ed6ed3bbe3f3bee775d691ff0856a67413575b8956092b4.

## Deployment delta: retain the document worker

**Only the core needs this source update.** The running document worker, image prefix 8e620f3df65d, can remain unchanged for this feature. This conclusion was checked against its actual installed source using read-only container file reads.

- Installed bootstrap/document_goal_worker.py is byte-identical to current source: e7805f7803e731e7fdc6051d707d0ac54234a8bc24e4994da5fd96348ead3ac9.
- Installed adapters/postgres/durable_goals.py is byte-identical: b353dc249180958a318c0ae115ab6b83fe3075398c57b2b66439fa1ca74d9ea5.
- Installed providers/document_task_input.py is 19df63c8f12ddce138961cbce4b92f9d14bdb8298270232ed20f729dbd7244c2. Every DocumentTaskPool method except claim() is AST-identical to current source.
- The worker uses claims()/find()/validate_claim(), not claim(). These readers accept the additional return_binding field while retaining existing scope, event-digest, pool, slot, task and plan checks. The complete event payload still participates in its integrity hash.
- New admission binding generation and delivery composition run in core. No changed worker-side interface or document effect is required.

This is compatibility source evidence, not a new live document execution. No service was restarted or mutated during review.
