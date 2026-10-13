# Independent review: current shared Home Assistant route

Date: 2026-10-02  
Decision: GO for T2.3's technical adapter connection and T2.4's existing automation outcome, using the combined evidence below.

## T2.4: general automation creation and modification

The current binding records and both verified logs agree: core and worker have
identical digital configuration profiles, execution-source lists and nine relevant
module hashes. Website and Telegram owner bindings and both configured household
bindings expose the general HA API and automation inventory; the unregistered
actor is rejected. These checks made no model request or HA request.

Reuse the actual installed create/edit/readback result and independent review:

- `evidence/product/t2-ha-delivered-20260929.json`
- `evidence/product/t2-ha-independent-20260929.json`
- `evidence/product/shared-ha-general-installed-20260925.json`

The earlier ordinary website task completed through the normal worker without
coordinator resume. It created a disabled harmless automation, changed its
description, obtained complete readbacks, and returned an accurate final result.
Independent inspection passed; only the trial object was subsequently removed.
The coordinator's source comparison reports the API/automation implementation
unchanged since the successful baseline. There is no evidence-based reason to
repeat that mutation trial merely to close the existing small task.

## T2.3: both speaker inputs connected to the same execution

The initial current-binding checker exercises the installed audience and tool
availability checks through the responder's `work_server=None` fallback. It
does not alone prove the production session-bound adapter path.

The additional checker ran in the actual speaker service using its current
configuration and authority. For each of the two configured bindings it:

- matched the current household policy and authenticated token;
- performed a real HTTP readiness check and opened a real core-issued work session;
- constructed the production request for
  `/internal/v1/ha-voice/session/respond`, retaining the original text and fixed
  satellite/device binding without a caller-selected principal or audience;
- closed that session through the actual endpoint in `finally`.

Both bindings match those independently checked in core and worker. The installed
`HouseholdCoreClient` source hash matches the repository source. Its inspected
implementation binds the request to the core-issued session and configured
device, and performs transport translation rather than separate house-task
interpretation.

Together with `evidence/product/household-digital-installed-20260925.json` and
`evidence/product/native-digital-continuity-20260925.json`, this covers the
original technical adapter-connection criterion. The latter includes four
installed-source checks for digital HA execution, session continuity, stop/resume
without duplicate effect, the originating return session and revocation.

## Evidence boundary

The new speaker checker did not send its constructed respond request. It proves
current authenticated transport/session and request-envelope binding, combined
with prior shared-core execution evidence; it is not a fresh model-driven HA
request through each speaker. No audio, device actuation or physical microphone
trial occurred. C3/C4 physical audio remains open. Historical T2.1 timeout causes
and T2.2 contextual selection from an ordinary house question remain separate.
No general reliability or owner acceptance is claimed.

The two earlier local binding-harness errors are not successful checks. The
review relies on the final verified records and the additional actual speaker
session check. This reviewer read source and artifacts only, ran no tests or
model calls, and made no runtime change.

## Artifact bindings

Private artifact directory: `t2-current-shared-ha-20261002`.

- `current-bindings.py`:
  `e406e47756ed5a3019d4c9ccab7a6dc97530999f351b3e099146b7e488896a78`
- `current-bindings.json`:
  `672592963b4421c992a48060249ce19c749ad19513aa49c778a83fc62f71b2d7`
- `speaker-binding.py`:
  `2ff472fc297adfaa15256cc395a7f8afa2f259beae8bd578f5453ff70ae684b6`
- `speaker-binding.log`:
  `b0da38f51b489e3b3a502bf9fa93fd6a789469d2a13c20b12fd38295cb8fde48`
- Installed and repository `voice_service/agent.py`:
  `2de5e47c993bd2094fcf735e0b3c71e18cda9199e8b5c2debb3864ced6194c53`
