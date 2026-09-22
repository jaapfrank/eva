# Household public work: disabled server candidate

Base: `5bfbdbd6b772f29a61b4ad3be5f20fbea9b5dbdd`. Branch:
`codex/household-public-work-20260922`. This implements the server portion of
[the household result contract](household-result-contract-20260922.md).
Independent review, production composition, HTTP/media transport and physical
Voice PE acceptance remain open. No production configuration, policy, credential,
provider route, budget or running service was changed. No live model calls.

## Concrete result

A trusted connector can compose `HouseholdWorkServer` with an explicit
`HouseholdResearchAuthority`, open a core-issued session, and bind
`HouseholdWorkActions` to that session. It admits public research and supports
exact scoped get/stop/resume, completion polling, one atomic result claim and
separate offered/completed/interrupted/ambiguous acknowledgements. Full saved
source-bearing output is returned unchanged. There is no new scheduler or model
router: v3 uses the existing store, queue, worker, research adapter, indexed tool
journal, usage accounting and call fences.

The immutable v3 spec contains the original device actor, voice/research policy
hashes, first canonical ingress, server session, dedicated episode and
generation-bound destination hash. Spec, embedded origin/destination and the
existing admission receipt commit together. A separate household ingress helper
stabilizes exact retries without broadening private voice admission. Sponsor
identity funds the work but never becomes its speaker or audience.

Private reads, lists, boards, latest-stop lookup, stop, result verification and
private delivery explicitly reject/filter household work. The household facade
rejects private rows even with the same sponsor, as well as other devices,
sessions, generic tool-free starts and unscoped owner listing. Repeated action
receipts revalidate the retained work before release.

Current voice/token/identity authority, explicit research policy, resource budget
and existing provider authority are intersected. The new profile pins the same
provider and bounded public grant. Household authorization is checked on
admission, claim, call start, dispatch, result release, reads and delivery.
Revocation blocks subsequent output and dispatch; a completed dispatch's measured
usage is recovered from its existing verified journal even when revocation blocks
observation. Session close/expiry blocks delivery but lets authorized work finish.
A claim is never replayed after interruption or uncertainty.

## Reproduction and evidence

Tests ran with the candidate source mounted read-only, PostgreSQL 16.13 on an
isolated network namespace and a deterministic research fixture. Every test
module with database migrations used its own disposable database. The provider
fixture made zero network calls; delivery adds no model calls.

- `tests/postgres/test_household_public_work.py`: 16 passing tests. Covers
  admission/completion/retry, public argument rejection, same-sponsor private
  isolation, other sessions/devices, concurrent claim, policy rotation after
  claim, revocation before dispatch and during a call, foreground responsiveness,
  explicit stop/resume, close/expiry, saved-receipt reauthorization, corrupted
  result/missing ingress, retention expiry, transaction rollback, and exact get
  and polling after 21 newer tasks. These implement the server portions of
  acceptance cases 1–9 and v3-unaware/disabled-worker rejection in case 10.
- Existing private regressions: research 9, automatic private return 13,
  Telegram delivery store 10, delegated actions 6 passed.
- Contract/bootstrap/provider/household voice authority tests: 41 passed.
- Broader delegated store module: 26 passed, 5 failed. All five failures were
  reproduced on unchanged earlier source `03a82a5ea05e0946d8044e0e8ccbe2cd25a2b0fd`:
  `test_owner_chat_spawns_reports_and_stops_durable_postgres_work`,
  `test_eva1_private_bridge_binds_owner_work_without_changing_chat_identity`,
  `test_private_telegram_owner_starts_follows_and_stops_postgres_work`,
  `test_bot_api_and_ingress_ledger_replay_owner_delegation_exactly_once`, and
  `test_owner_chat_stop_cancels_official_sdk_call_and_never_replays`.
  They expect the earlier direct chat-command behavior; this slice does not
  restore it or claim a completely green repository suite.

The scoped session is a 15-second renewable lease, with a 30-minute absolute
ceiling intersected with policy expiry. Session renewal and every server operation
revalidate authority. New sessions cannot acquire old results. A transport
acknowledgement does not prove the person heard the result.

## Integration contract and remaining work

The feature is OFF by default: no environment switch or implicit derivation from
voice consent was added. Trusted composition must supply current voice authority,
`read_household_research_policy(path)` (private operator artifact), the current
resource-budget loader, existing model-lease validator and
`HouseholdResearchProfile` using the existing research provider configuration.
`serve_research_enabled_work(..., household_authority=authority)` adds the third
explicit release to the existing worker; omission admits no household work.
The core facade uses the same authority. Model arguments cannot supply it.

Transport integration must authenticate the connector and choose its provisioned
device before exposing the scoped action service. Wire open/renew/close,
get/stop/resume, poll and claim authorization/acknowledgement through the existing
household API family and existing media delivery/session timers. Reauthorize
before every content chunk, preserve full source text, exclude unauthorized
cached output from chat context, and keep speech interruption separate from task
stop. This candidate exposes server operations; it does not advertise HTTP routes
or claim audio/firmware readiness.

Core and worker must be installed together after independent review and explicit
policy composition. Changed shared store/runner dependencies change the existing
program seal; v3 also has a distinct profile and hashes its household policy,
facade, session ledger, voice authority and audience implementation. Calculate
actual deployment source/release hashes from the paired candidate; fixture hashes
are not deployment authority. Keep all new modules in the worker image.

Rollback disables new household admission/delivery and preserves all v3 rows and
results. Drain or park v3 work before removing its decoder; never relabel or
replay it. Deployment, rollback/live checks and real registered-device trials
(contract cases 11–12) belong to the coordinator's next integration stage. Actual
speech cancellation/chunk behavior and transport spoofing tests remain open; the
server tests do not substitute for them.
