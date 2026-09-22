# Household public work composition — 22 September 2026

## Scope and source

Owner outcome: accept bounded public research through the existing household
conversation, keep speaking during work, and return the saved result only to its
original authorized live household session. A provisioned speaker remains a
household device, never evidence of the owner's personal identity.

Integration base: `b07152067c440f138ac559ede8c5852404b1fd22`.
Branch: `codex/household-work-integration-20260922`.
The pre-existing receipt patch `c76b905e2a98b03fe5f9444b08dffe23ba47bb96`
was separately cherry-picked as `b96a7423`; do not integrate it twice.
The currently installed media parent is `sha256:daae43c50b211955aaf75a598be16246dca8bcf530f878991e572f8e1312a53d`.

## Bounded change

The core optionally composes the existing household work server with one shared
conversation-action provider. Its four household action names share the existing
descriptors. Private turns retain the original provider. Household selection
requires an admitted session and the actual recorded message ingress. Polling
and lifecycle control never manufacture message ingress. A relevant action
receipt cannot lose its reply guard merely because authority was revoked.

The worker receives the same explicit household authority through its existing
research composition. The v3 source identity includes the new composition and
provider modules. Existing private audio and history checks remain in the shared
agent. Household delivery adds offered/completed/interrupted acknowledgments;
a transport failure cannot authorize replay. Result audio stays outside SDK
history and retains the existing current-authority context checks.

The native speaker obtains its core-issued session after wake, before starting
the shared agent. It renews that lease while connected and polls saved work.
Local audio stop does not cancel durable work. Mute, revocation, disconnection,
and close stop the output sink first and close the session where possible;
otherwise the core lease expires. No firmware or listener changes are included.
The generic receipt remains `Een momentje.`, requires no model call, and remains
excluded from SDK history.

## Explicit configuration and installation boundary

Default remains OFF: omit `EVA_HOUSEHOLD_WORK_CONFIG_FILE` in both core and worker.
Enabling requires an absolute path to a private operator-owned JSON artifact:

```json
{
  "contract": "HouseholdWorkConfiguration/v1",
  "schema_version": 1,
  "research_policy_file": "/run/eva/household-research.json",
  "voice_policy_file": "/run/eva/household-voice.json",
  "voice_token_file": "/run/eva/household-token",
  "identity_file": "/run/eva/identity.json",
  "identity_trust_file": "/run/eva/identity-trust.json",
  "budget_file": "/run/eva/resource-budget.json"
}
```

These are references, not supplied grants or credentials. Current signed
identity, voice policy/token, explicit research policy, scoped resource budget,
and existing model lease must match. The core also requires its existing
household voice session and public research switches. The worker requires its
existing research switch. File references are fixed at startup; referenced
policy, budget, identity and lease authority are rechecked during use. Changing
composition paths requires a restart. No new provider or spending route exists.
The composed storage scope must equal the voice policy's external timeline scope;
the worker's configured storage scope must match it as well.

The coordinator must build paired core/worker source releases and the native
speaker runtime that imports the shared agent. Retain the installed receipt
media change. Recompute the release hashes from those actual images, validate
current policy and mounted files, preserve data and unrelated services, and
capture exact image/config rollback before installation. This handoff performs
no installation, provider call, credential change, or policy activation.

## Offline evidence

Disposable PostgreSQL plus existing core/voice images; deterministic research,
HTTP and media fixtures, no paid model calls:

- 15 passed: composed PostgreSQL path and configuration, worker opt-in/default-off,
  and existing delegated worker settings/lifecycle.
- 34 passed: existing household HTTP, voice authority, household responder and
  private return composition regressions.
- 22 passed: household protocol, private saved audio, and native speaker lifecycle.
- 4 passed after final envelope validation: household protocol and full saved
  audio/source bytes, explicit completion/interruption and no SDK history.
- Final 4-test PostgreSQL rerun covers the guarded receipt after session closure.

Two test-harness issues were retained and corrected: the existing media image
lacks openssl, so speaker certificate fixtures initially failed setup (18 passed,
2 errors); the harness then mounted the existing disposable PostgreSQL image's
openssl executable with matching loader/libraries read-only, without changing
product dependencies or fixtures. Two new test basenames initially collided
under pytest collection; the worker configuration test was renamed.

Independent review, paired installation, live verification, rollback verification
and a physical Voice PE conversation/work/stop trial remain open. These tests
prove the bounded software path, not speaker recognition, owner acceptance,
measured end-to-end latency, or a complete product release.
