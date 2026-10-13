# Person and shared-device boundary verification

Base: `7845f4e0cc6f6e1099bdeca48a1dcb0124f39463`, clean WSL worktree on
`codex/person-household-boundaries`. This bounded step used existing source and
fixtures, zero provider calls, no live data, and no account, rights, hardware,
model, voice-service or accepted-memory changes. No exploitable leak was
established within the selected routes, so no production repair was invented.
This evidence does not close person recognition or private shared-speaker continuity.

## Routes inspected

- `bootstrap/household_voice.py`: a current owner policy admits registered
  `household-device:` audit actors to an exact household. The resolver rejects an
  owner UUID or the name Jaap as a device actor and rechecks mandate/token/identity,
  expiry and device mapping. This is device authorization, not human recognition.
- `bootstrap/native_voice.py`: creates a household audience and a conversation
  namespace derived from device plus supplied continuity ID. Supplied private
  conversation text/IDs do not select the owner's conversation. It rechecks the
  audience and rejects a reply with different conversation/message bindings.
- `adapters/api/household_voice_session.py`: checks its own household credential,
  current authority and exact device; client-selected principal/audience fields
  are forbidden. Its shared stream rechecks authority before delivery and retention.
- `application/memory_audience.py` and the existing Hindsight household projection:
  exact audience and consent references survive recall/retention. Household recall
  rejects private/other-household provenance and derived observations with missing,
  revoked or mixed source authority; it does not use private style-memory fallback.
- Read-only joins: `bootstrap/runtime.py` audience admission,
  `adapters/api/voice_turns.py` delivery checks,
  `bootstrap/delegated_work_owner_service.py` private-owner gate, and
  `adapters/postgres/delegated_work_actions.py` saved-receipt authority check.
  The owner service requires `private_person` before owner/bridge alias matching;
  household audiences cannot retrieve owner work through that service. Persistent
  reads and saved action receipts revalidate the current owner binding.

The internal trusted-connector HTTP routes were observed as a separate trust
boundary. This focused check does not establish network ingress isolation or audit
all internal endpoints, and makes no claim that every route was reviewed.

## Existing evidence exercised

`household-tests.xml`: **50 passed, 53 deselected, 13.34 seconds**, covering authority,
NativeHouseholdResponder, household session API, household contact scope and
Hindsight household-source validation. The fake Hindsight server is local only.

Command from `eva2` with its source on PYTHONPATH:

```sh
python -m pytest -q tests/bootstrap/test_household_voice_authority.py tests/bootstrap/test_native_household_responder.py tests/api/test_household_voice_session.py tests/application/test_contact_model.py tests/projection/test_hindsight.py -k 'household or native_binding or current_revocation or only_registered_device or other_owner_or_installation or inactive_channel or world_readable_authority'
```

`delegated-owner-tests.xml`: **4 passed, 3 deselected, 4.69 seconds**, using a fresh
PostgreSQL 16.13 disposable database and existing authority fixtures. They cover
stored results/resume and revocation, distinct conversation/storage scopes, the
trusted private bridge alias, and unreadable bindings. These tests include group
exclusion; household exclusion is also directly visible in the same audience-kind
gate. No additional claim of a new household-result end-to-end test is made.

```sh
python -m pytest -q tests/postgres/test_delegated_work_owner_service.py -k 'owner_commands or configured_conversation_scope or trusted_private_bridge or unreadable_bridge'
```

The first PostgreSQL run started before the new container was ready, so database
creation failed and three fixtures errored (one non-DB test passed). After explicit
readiness and database creation, the unchanged selection passed. That setup failure
receipt remains at `/tmp/eva2-person-delegated-setup-failure-20260922.xml`; no assertion
was relaxed. The dedicated test container was stopped and removed afterwards.

## Remaining verification route and decision

A shared speaker currently has household conversation and explicitly authorized
household memory only. A name, familiar phrasing, device registration, or trusted
local browser session must not become proof that Jaap is present alone. GE-01/GE-02
and accepted CO-004/CO-005 functionality remain closed; this is the separate open
requirement to bind cross-channel continuity to a verified person and audience.

Before private shared-speaker continuation can be accepted, an explicit supported
person/audience verification route is needed. It must establish the intended
person and permitted listeners before private recall/model input and again before
spoken private delivery, with current revocation/session boundaries. Acceptance
cases are the same authorized person returning days/weeks later, a different
household member asking for that context, an unknown speaker, a change of listeners,
and revocation during work. The latter cases must retain the private boundary.
This document neither selects biometrics nor grants additional person/account rights.
Until that route exists, shared audio must remain household-only; private continuity
can use an already authorized verified private channel.

## Completion notification boundary

A completed owner-private result may be delivered to the current verified private
Telegram owner destination only when that destination is already authorized by the
existing communication mandate and is durably bound to the task or explicit routing
policy. Revalidate the current person/binding and destination at dispatch. Completion
alone, a work ID, or a hashed episode ID does not prove which Telegram chat is the
right destination. The inspected delegated spec does not itself carry an explicit
origin channel destination; the coordinator must preserve that binding through an
existing reusable delivery route before autonomous notification is established.

A shared household speaker must not receive that private result based on device or
name recognition. Public research content stored as private owner work does not
silently become household-shared. Do not infer sharing consent from completion.

## Delivery status

Evidence-only handoff: no runtime installation or rollback is needed for this
commit. The coordinator owns workboard updates and any future routing integration.
No owner acceptance is claimed and no accepted memory work is reopened.
