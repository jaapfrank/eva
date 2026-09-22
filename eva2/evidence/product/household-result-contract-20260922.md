# Household public research and session result contract

Status: executable design handoff, not implemented or installed. Reviewed source base: `637985d06a385e6bcbe1571e5a0f970913e56132`. This task owns only this file. The coordinator owns integration and runtime wiring; the other builder retains ownership of private result-return source. No model/provider calls, production edits, new tests, device actions or mandate changes were performed for this contract.

## Outcome and finish line

On a registered Voice PE, a person asks an ordinary public research question. Eva admits one bounded task through the existing delegated work lifecycle, stays available for conversation, and offers the saved result once in the still-authorized originating household session. The result and sources remain intact. Task stop and speech interruption remain separate. Neither a device identity nor the content's public subject allows private memory, private tasks or private results to enter this route.

Technical completion requires the bounded changes and acceptance cases below, independent review, compatible installation and rollback, and a server-route readback. A real Voice PE trial remains separate: the network rule, USB identification/backup/firmware and actual audio have not been proven by server readiness. Do not mark the complete eight-step project or owner acceptance finished.

## Existing authority and the precise gap

Read sources: [channel handoff](background-result-channel-handoff-20260922.md), [person/household boundaries](person-household-boundaries-20260922/handoff.md), the current project goal/workboard, the documentation entry point, and the implementation points below. This is a selected review, not a broad audit.

Read-only inspection on 22 September found `HouseholdVoicePolicy/v1`, enabled, version 1, two devices, effective `2026-09-08T23:37:17Z` through `2026-10-08T23:37:17Z`. Its fields bind tenant, installation, household, owner/binding, identity, token, consent and devices. There are no work, research, delivery, session or budget fields. No tokens, credential hashes or personal identifiers are reproduced here. `HouseholdVoiceAuthority.validate_current` rechecks the policy, identity, token and lifetime. This authorizes shared conversation/memory for registered device actors; it does not identify the human or privately authenticate Jaap.

The existing owner's request for one Eva, public background research and household-session completion supplies the product mandate for this bounded extension. Do not ask again for general task permission. What is missing is an explicit machine-enforced derivation of that mandate for work admission and delivery, plus its installation evidence. Register that derivation through the coordinator's existing owner-managed configuration route. A current household conversation policy alone must never implicitly activate it.

No further owner decision is needed for **public research, the existing provider and budget ceilings, and return only to the originating authorized session**. Different-room broadcast, private-to-household sharing, automatic speech in a newly opened session, higher expenditure, new services/credentials or a different retention policy need a separate explicit decision; they are excluded here. If the existing budget authority cannot derive this bounded household grant, report that exact missing authorization rather than treating the whole project as unapproved.

Concrete source gaps:

- `DelegatedWorkOwnerService.available_for` correctly requires `private_person`. Keep it strict.
- `DelegatedWorkSpec` and inherited `DelegatedResearchSpec` declare `audience = "private"`. `DelegatedResearchProfile.build_spec` and `DelegatedResearchRunner` construct private owner turns. A destination sidecar alone cannot honestly make those household work.
- `PostgresDelegatedWorkStore` validates the sponsoring owner/binding, but `load`, `list_owned`, `workboard`, stop/resume and latest-target lookup currently do not distinguish household work. Reusing those methods unchanged would confuse requester scope with funding/management authority.
- `HouseholdCoreClient` supplies fixed provisioned device identifiers and cannot use owner endpoints. `SessionLease` manages local media cleanup; neither a UUID supplied by a client nor this in-memory lease is a durable core authorization to deliver a result.
- `PostgresConversationJournal.admit_voice_ingress` specifically recognizes private owner voice IDs. Household retry timestamps must not be stabilized by weakening that private check.

## Normative contract

The names below are proposed new wire contracts, not claims that they already exist. Existing v1/v2 records and hashes remain unchanged.

### 1. Authority derived by the server

Add a narrowly scoped owner-managed `HouseholdResearchPolicy/v1` alongside the existing household authority. It references the exact household policy/consent and current owner/binding and has enabled/revoked state, effective/expiry times, the admitted device set, `research.public_sources` only, result scope `originating_household_session`, and references to the existing provider/resource-budget authority. Its effective rights and lifetime are the intersection of those authorities, never an expansion. Keep current research limits, deadline, tool count and seven-day maximum retention; use the shorter authority/retention bound when applicable. No new subscription or ambient network/filesystem/tool access.

Resolve this policy only in trusted core/worker composition. Model tool arguments contain only the existing `PublicResearchRequest`, or a work ID for an already authorized control operation. Reject model/client owner, audience, consent, policy, device, session destination or budget selectors. A model's `public_review` label is not authority: retain `validate_public_projection` and the existing public-query policy, and pass no recalled memory, private conversational history, credentials or workspace references into the researcher. Pattern checks are bounded disclosure checks, not proof that arbitrary text is anonymized.

### 2. Explicit household task envelope

Add `DelegatedHouseholdResearchWork/v3` and explicit decoder/runner dispatch for it. Reuse the existing research request, grant, result, usage and durable state machine. Leave the existing private literals and decoders' rejection behavior intact. New fields must be included in the immutable spec hash:

| Field group | Required binding |
|---|---|
| Existing accounting identity | Current sponsoring owner UUID and verified binding, tenant/install scope, exact worker/model releases and existing resource limits. These identify the resource sponsor, never the human speaker. |
| Audience | Literal `household`, exact household ID and consent reference. No private fallback. |
| Requester | `household-device:<device_id>`, provisioned satellite/device pair, owner-derived work-policy reference/hash and household policy reference/hash. |
| Origin | Server-admitted conversation ID, original message ID, first durable ingress timestamp and core-issued session generation/lease identity. |
| Request | Exact validated public question/title/options, without ambient context. |
| Storage/return | Household-specific episode namespace and originating-session destination hash, bound to this spec/work generation. |

Use a separate explicit v3 class/discriminator; do not coerce v3 into a private v2 task and merely relabel its result. Existing row owner/binding columns can continue to hold resource-sponsor IDs; the immutable JSON payload can carry the additional envelope without requiring a new work queue. All result reads and mutations must enforce the envelope before exposing even a title or work ID. Existing private owner service/store entry points must explicitly keep accepting only private v1/v2 work. If the owner later needs a household administration view, make that an explicit authorized view; do not infer it from a shared sponsor UUID.

Derive the new work ID from the admitted tenant/install/household/device/session/message/request key with a separate household namespace. Reuse the first stored ingress time on exact retries. Add a household-specific ingress admission helper using the existing journal transaction pattern: same authenticated device, audience, conversation, message and payload returns the same timestamp and receipt; conflicting content or destination fails. Do not widen `admit_voice_ingress`'s private-ID acceptance or bypass ordinary journal checks.

Commit the immutable spec, origin/destination and admission receipt in one existing database transaction. A retry must return the same binding; changing session/device/audience must not retarget an existing task. No backfill of old private or unbound records. Resume uses the existing explicit next-generation rule, same household/actor/origin authorization and bounded request. It cannot convert a private task, automatically replay ambiguity, or attach a completed task to a different session.

### 3. One lifecycle, household authorization at every boundary

Reuse `PostgresDelegatedWorkStore` admission/lease/call fence/result/usage/stop lifecycle and `DelegatedWorkWorker`; add explicit envelope-aware admission/read/control entry points and a bound household-authority validator. Keep current owner/binding validation as an additional resource-sponsor check, not the household authorization itself. The new validator is mandatory for v3 on claim, call start, immediately before each tool dispatch, result release and result-delivery claim. An old worker must not claim v3: use an explicit new release selector and decoder capability, preserving prior worker profiles.

Add a v3 branch to the existing research composition. It constructs an isolated `ConversationTurn` with the admitted household/device audience and its dedicated episode namespace, using only the public request. It never calls `ConversationTurn.private` with the sponsor identity for v3. Reuse the existing public research adapter, indexed external-tool journal, cancellation, measured usage and source/hash validation. Its authorize callback checks the exact household envelope and current mandate in addition to existing call/stop/deadline fences. The adapter's historical `OwnerResearchContinuationTool` name is not an owner impersonation requirement: inspect and reuse its ordinary requested-research behavior under the explicit v3 callback; do not grant initiative authority.

Policy/token/identity revocation or expiry cancels further authorized dispatch and suppresses content delivery. Preserve durable task/result/usage evidence under retention, without disclosing it through the now-invalid household route. Session close cancels delivery eligibility and polling, but does not itself cancel authorized background work; explicit task stop does. Tasks remain in the same lifecycle, not a second household scheduler. Old or unknown profile/spec combinations fail before a provider call.

### 4. Session authorization and delivery

Reuse the private-return builder's typed origin/event/claim pattern only after its interface is reviewed. Keep household audience authorization separate and preserve private negative guards. Do not consume `EvaPrivateWorkDestination/v1` Telegram claims.

On session admission, core issues a server-owned lease identity/generation bound to the authenticated provisioned connector, household, device, consent and exact conversation. A bounded renewable lease is closed by `SessionLease` cleanup and expires without renewal; renewals must revalidate current household authority. Reuse an equivalent server-side binding if the private-return implementation provides one. The existing payload `session_id` may be continuity input, never the whole authority. Do not accept a browser's arbitrary room/work/results list as authorization.

The originating session gets a bounded household result projection through the existing `/internal/v1/ha-voice/session` API family. An authenticated result poll cannot access private endpoints or select a different household/device; include only tasks whose immutable origin matches the current server lease. Revalidate before returning work metadata, result bytes or sources. Pagination cursors and guessed work IDs confer no rights. Keep individually followed IDs readable through the same scoped get path even after they fall outside the latest-twenty list.

A result event binds work ID, generation, origin message, result hash and destination/session generation. Preserve exact source-bearing result content; any bounded spoken projection is selected by core from that result and recorded with its own text hash. No channel LLM/summarizer, regex task detection or invented completion sentence as if it were the substantive result. If a spoken projection is shorter, identify it as such and retain access to the full result within the authorized session. Do not silently truncate away the saved sources.

Use an atomic delivery claim keyed by work/generation/result/destination, distinct from task execution and Telegram delivery. Claim only after current authorization and before audio side effects. Revalidate immediately before speech and during chunk delivery. Simultaneous pollers compete on that claim; reconnect, process restart, expired claims and uncertain playback do not automatically speak again. A claim/transport acknowledgment is not proof that a person heard it. Keep offered, completed, interrupted and ambiguous delivery states separate. New sessions may explicitly request retained results only through a later separately designed current-authorization route; this slice never silently transfers or broadcasts them.

Deliver through existing `EvaAgent.deliver`, `BridgeState`, speech handle, chat context and `SessionLease.timers`; keep the durable work ID distinct from the admission turn's message ID. A late result does not reopen or overwrite its already-final admission reply. Task stop targets the authorized durable work ID and suppresses future result speech. Barge-in interrupts audio only. Once bytes have already been heard, revocation cannot retract them; after revocation the system must emit no further content.

## Minimal source work and ownership boundaries

All paths below are under `eva2/`. These are concrete future integration points, not files changed by this handoff.

| Existing point | Smallest necessary change/reuse |
|---|---|
| `contracts/delegated_work.py` under `src/eva/` | Explicit immutable v3 envelope/decoder; reuse research grant/result/usage, retain private contracts. |
| `src/eva/bootstrap/household_voice.py` | Reuse current authority validation; compose a separate bounded household work-policy derivation, no private person recognition. |
| `src/eva/bootstrap/native_voice.py`, `adapters/api/household_voice_session.py` | Reuse `make_turn` and fixed device scope; core session binding + household admission/result/control adapter. Do not replace native conversation logic. |
| `src/eva/adapters/postgres/conversation_journal.py` | Separate household retry/first-ingress binding using existing journal machinery. |
| `src/eva/adapters/postgres/delegated_work.py` | Envelope-aware methods and transactional origin/claim records on existing tables/journal; preserve private-only read/control behavior and all existing call fences. |
| `src/eva/bootstrap/delegated_work_owner_service.py`, `application/delegated_work_owner.py`, `adapters/postgres/delegated_work_actions.py` | Preserve private facade. Add a small household service/facade using the same action receipts with server-selected scope and an explicit allowed operation set. Reuse public research/get/stop/resume tool semantics; do not advertise generic tool-free start or unscoped owner listing. Compose mutually exclusive services by admitted audience in the existing action chain. |
| `src/eva/bootstrap/delegated_research.py`, `bootstrap/delegated_work_service.py`, `operations/delegated_work_worker.py` | Add the explicit household profile/envelope branch and authority injection; reuse provider, tool journal, worker loop and usage. Include changed dependencies in source/release hashes on both core and worker. |
| `src/eva/application/delegated_work_delivery.py`, `adapters/postgres/delegated_work_delivery.py` and private-return candidate | Reuse transaction/claim/idempotency mechanisms with a distinct household route identity; do not broaden existing private destination validation or consume private claims. Coordinate shared-file edits after private builder delivery. |
| `src/eva/voice_service/agent.py`, `voice_service/lifecycle.py` | Household client reads only the household projection; bounded polling uses existing lease timers and delivery path. Preserve private client behavior and stop/replay rules. |
| Existing deployment composition | Mount current household/work authorities read-only for core and the existing worker, retain current model lease and budgets, install compatible core/worker profile together with rollback. Speaker changes only for the reviewed transport projection. |

No generic new queue, memory subsystem, model router, conversation personality, firmware rewrite, third-party dependency or separate workboard is needed. The envelope and audience enforcement are necessary scope changes, not a reason for a wider refactor. Do not edit files held by the private-return builder concurrently; the coordinator integrates the seams.

## Acceptance cases fixed before implementation

Reuse existing evidence and targeted fixtures; do not rerun the prior 54 boundary tests solely for this document. Add only missing cases, with deterministic provider doubles before any separately authorized live proof.

1. **Admission and completion:** registered device + current derived work policy + live core session admits one public task, emits a truthful receipt, allows another question, and offers its verified result once in that session. Work/result/source hashes and measured usage are preserved; delivery adds zero model calls.
2. **Actual request boundary:** the model/client cannot choose owner, household, audience, device, consent, budget or destination. Spoofed fields, private-path/credential-bearing research projections and arbitrary `work_results` cannot authorize dispatch or reads. Provider input contains only the admitted public research request.
3. **Private isolation:** private task/result IDs, a spoken owner name, a sponsor UUID and a same-topic private result produce no household bytes or metadata. Existing private v1/v2 reads remain private-only; no household-to-private resume/retarget or reverse conversion.
4. **Other room/session:** second registered device, other household, stale session, guessed conversation ID or valid token with the wrong admitted session cannot receive or stop this task. Simultaneous pollers obtain at most one delivery claim.
5. **Authority timing:** revoke/rotate/expire policy, consent, token or identity before admission, after admission/before tool dispatch, after result commit, after poll and during speech. No newly unauthorized provider dispatch or further output; preserve auditable terminal/result state without private fallback.
6. **Stable retries:** identical admission retry at a later wall-clock time returns identical work ID/spec/origin and first timestamp with one provider dispatch. Changed text/audience/device/session under the same ingress ID is rejected. Crash between spec and origin writes leaves both committed or neither.
7. **Conversation and stop:** a second question remains usable while work runs. Explicit work stop is scoped to its real work ID and prevents later automatic result delivery. Barge-in interrupts speech without canceling task execution. Stop/completion races do not reopen a stopped delivery.
8. **Close/reconnect/ambiguity:** close/lease expiry stops polling and automatic speech; task may finish durably. Reconnect, fresh session and crash after claim do not replay uncertain speech or transfer to another room. Full retained result stays in its household scope; no global notification.
9. **Retention and integrity:** expired, tampered, wrong-spec, wrong-generation or missing-origin results return no content. A tracked task outside the latest-twenty list remains reachable only through its exact authorized get path. Source-bearing saved output is unchanged.
10. **Compatibility:** existing private Telegram delivery, private website/voice result return, owner panel, native household conversation and accepted memory remain unchanged. A v3-unaware worker cannot claim new household work. Record exact compatible core/worker/speaker releases and zero unexpected extra calls.
11. **Deployment and rollback:** install with preserved queues/results, source seals, current provider route and budgets; live server admission/readiness metadata is not a physical audio proof. Rollback disables new household admission/delivery, preserves v3 rows/results and prevents an older decoder from claiming them. Drain or park v3 work through the existing lifecycle before removing v3 support; never delete rows, relabel them private or replay unknown calls.
12. **Real owner-facing trial, separately open:** once network/USB/firmware prerequisites are actually satisfied, run one public request through each intended real entry point, speak another question, interrupt audio and explicitly stop a different task. Record admission latency, substantive result latency, source quality, usage and where/when audio played. A laptop or HA panel run does not close physical Voice PE acceptance.

The coordinator should register this as the missing household work/result binding within steps 1 and 3, with implementation, independent review, installation and physical acceptance recorded separately. This file neither grants extra authority by itself nor starts a new goal.
