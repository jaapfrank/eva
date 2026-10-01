> Update 1 October 2026 — C4.1: the existing `voice-pe-interruption.yaml`
> overlay is installed on Woonkamer's ordinary HA firmware. The exact reviewed
> OTA SHA256 is `81f70fae82f3ce6d631fe651511e85ce856fc8a0f1309d00362717c8140b18aa`.
> Authenticated device readback confirms the new event entity; Eva2/Hey Eva and
> mute-on remain selected. The second speaker is unchanged. One physical wakeword
> during speech followed by a new question still needs observation; this does
> not establish arbitrary-speech full duplex or complete C4.
>
> The template event is optional telemetry for this transport: pinned
> aioesphomeapi 46.2.0 maps `VoiceAssistantRequest(start=False)` to HA pipeline
> abort. No accepted background-task stop command is added. The retained ordinary
> recovery image is available without an automatic rollback instruction. See
> `../../evidence/product/c4-device-boundary-20261001.json` and
> `../../reviews/c4-wakeword-relisten-20261001.md` for source, installation and
> remaining proof. Candidate-only descriptions below are historical.

> Update 25 September 2026: the existing Eva conversation entity now explicitly
> advertises `supports_streaming` to Home Assistant. HA 2026.9.2 otherwise reports
> `stream_response: false` to the satellite even while ChatLog deltas arrive.
> The TTS engine must also support streaming input. HA still buffers short text
> (its normal threshold is more than 60 characters), so this is not proof of an
> instant spoken receipt. Existing firmware, request cancellation and final
> response binding remain unchanged. See the tagged upstream implementation:
> https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/assist_pipeline/pipeline.py

> Update 24 September 2026: the installed native conversation route now supports
> `Accept: application/x-ndjson`. Request-bound `delta` frames feed Home Assistant's
> existing `ChatLog.async_add_delta_content_stream`; only a matching `final` frame
> completes the client response. The JSON transport remains available for older
> callers. Cancellation keeps the existing request-specific interrupt protocol.
> HA 2026.9.2 source contract: https://raw.githubusercontent.com/home-assistant/core/2026.9.2/homeassistant/components/conversation/chat_log.py
> Physical latency, audible completion and interruption trials remain deferred.
> The older candidate descriptions below are historical, not current installation status.

# Voice PE interruption candidate

The `custom_components/eva2_voice` directory now contains a **native Home
Assistant conversation-agent candidate**. Its HTTP client is tested against a
local HTTP server and the new Eva2 HTTP service with a real PostgreSQL ledger.
The HA entry/configflow/pipeline and actual Eva conversation runtime have not
been integrated. Do not install or select this agent as the household pipeline
until household admission and runtime cancellation are connected and the complete
candidate passes its release checks.

The native client gives each invocation a random request ID. Cancellation or
supersession sends an interrupt for that exact request, while another speaker
can continue independently. The entity obtains the device ID from HA's registry
and forwards no speaker name, HA user ID, Eva principal or private chat history.
The server must still independently enforce household admission.

## Required core protocol

Both endpoints require an integration credential bound server-side to the
installation, permitted satellites/devices and a shared household audience.
Existing owner/dashboard/private-person routes are not substitutes.

- `POST /internal/v1/ha-voice/turns`: JSON fields `request_id`, `satellite_id`,
  `device_id`, `conversation_id`, `text`, `language`. A successful JSON response
  contains exactly `request_id`, `reply`, `continue_conversation`.
- `POST /internal/v1/ha-voice/turns/{request_id}/interrupt`: JSON fields
  `satellite_id`, `device_id`. This invalidates that answer generation, including
  when interruption arrives before admission of the corresponding request.

The server must scope idempotency and cancellation to the authenticated
installation/device/request, preserve a cancellation tombstone for reordered
requests, suppress superseded answers and enforce a deadline independently of
the HTTP client. Interrupting spoken delivery must not undo an external effect
or silently grant authority to cancel other work. A transport response is not
proof of audible delivery. The client uses the existing 32-second answer window
after STT and a bounded two-second interrupt attempt; a failed interrupt request
does not produce a false server-stop receipt.

## Core cancellation ledger candidate

`src/eva/adapters/postgres/voice_requests.py` now uses Eva2's existing operational
PostgreSQL timeline to admit a request once, record an interruption before or
after admission, and claim at most one reply within 32 seconds. Its identity is
scoped to the authenticated installation and registry-derived device. Stops are
retained across connections/restarts, without storing transcripts or responses.
A crashed request is not re-executed on retry; a new question needs a new ID.

Five tests on an isolated PostgreSQL 16.13 database cover reordered requests,
new store instances and connections, concurrent admission/interruption and reply
claims, expiry after lock waiting, duplicate requests and device/installation isolation. This is
now connected to the optional native HTTP service below. Household admission
and the actual Eva conversation runtime remain unconnected. A reply claim is not proof of audible delivery; HA
must still discard a cancelled response already in flight.

## Native HTTP service candidate

`src/eva/adapters/api/native_voice.py` provides both protocol routes. `create_app`
installs them only when explicitly supplied a NativeVoiceService; default is off.
The service binds an integration token to a fixed satellite/device mapping and
rechecks its current grant before model work and before returning a reply. The
responder callback must be cancellation-aware and bind to a shared household
memory audience; no owner/private-person fallback is supplied.

Within one API process, a new question stops the earlier request from that
speaker. Admission-in-flight is tracked so a stop between database commit and
worker creation cannot start new model work. Disconnects, cancellation and failed
replies persist stops. Workers remain owned through cancellation cleanup; service
shutdown joins them even if durable stop registration fails, then reports failure.
A non-cooperative model adapter cannot be made cancellable by this wrapper.
Multiple API workers are unsupported until supersession is coordinated durably.

A real HTTP test uses the native aiohttp HA client, uvicorn/FastAPI and isolated
PostgreSQL: cancel a pending response, answer the next question, and separately
cancel a raw HTTP connection without sending an interrupt POST. The responder is
a controlled test coroutine, not Eva's model, HA STT/TTS or acoustic hardware.
Run `pytest -q tests/postgres/test_operational_timeline.py tests/api/test_native_voice.py
tests/operations/test_ha_voice_native_client.py` in the locked runtime test
environment plus HA's `aiohttp==3.14.3` test dependencies. No model call is needed.

## Household memory candidate

`MemoryAudience.household` now carries a distinct audience ID and exact owner
mandate reference. A speaker audit actor is not an authenticated human identity.
Conversation and document identifiers must stay in that household's namespace;
private projects, observation bindings and Telegram audience payloads cannot be
substituted. The core accepts this audience only with an explicitly supplied
current mandate resolver, and rechecks it before queuing semantic retention.
Bootstrap currently supplies no household resolver, so this remains disabled.

Hindsight uses a household tag, checks the current mandate of both the querying
actor and every selected source actor, and never loads a private style mirror for
a shared conversation. The existing durable outbox preserves the full audience
and mandate reference after reconnection. Derived observations in ALL audiences
now require complete, consistently bound source facts: mixed audiences, old
mandates, incomplete/truncated sources and injected source metadata are rejected.
Direct memories and valid source-bound observations remain supported. Older
observations lacking complete source provenance are intentionally not recalled;
real Hindsight compatibility must be verified before deployment.

`bootstrap/native_voice.py` now binds each registered device to its household
audit actor and the current exact mandate, namespaces HA continuity identifiers,
and invokes the ordinary conversation runtime through a cancellable worker.
The model receives explicit shared-channel context: the speaker device is not
an authenticated person. Reply conversation/message binding is checked before
retention. Interrupted replies are not queued for semantic retention.

The request-owned cancellation signal closes registered async model streams and
prevents later provider retries and tool dispatches. Worker cleanup remains
owned and joined; already-running synchronous effects may finish and readback
remains possible. This does not claim forced thread termination or rollback of
external effects. Real core/Pydantic/PostgreSQL tests use an injected FunctionModel
and empty memory fixture, plus the actual durable outbox; no paid model call or
hardware speech is included.

Startup now accepts `EVA_HOUSEHOLD_VOICE_POLICY_FILE` and
`EVA_HOUSEHOLD_VOICE_TOKEN_FILE`. Both must reference private regular files
inside the container. The policy follows `HouseholdVoicePolicy/v1` in
`src/eva/bootstrap/household_voice.py`: exact runtime tenant, installation,
owner and owner binding, current identity digest, integration-token SHA256,
household ID, explicit consent operation, satellite-to-device registry mapping,
and effective/expiry timestamps. The token is separate from the dashboard and
Home Assistant provider credentials. No environment token value is accepted.

The same current authority instance is supplied to Hindsight, the ordinary
runtime, and the native voice service. Token, policy or identity changes revoke
existing admissions. Missing, revoked or expired policy leaves this optional
channel off on restart. An authenticated device can still stop its admitted
request after revocation; new work remains denied. Existing resource budgets
continue to apply. A device audit actor never becomes the owner's identity.

Keep the consent operation ID unchanged only when explicitly continuing the
same household memory grant; a new operation intentionally invalidates source
references under the old grant. Policy renewal requires service reconstruction;
changing a file does not silently extend an existing service's authority.

These settings do not mint a household mandate or activate HA. The next release
must mount the actual owner-authorized files read-only on the reserve core,
include that configuration in release input authority, and test the native HA
pipeline before switching traffic. No production files have been created by
the source tests.

## Device candidate

`voice-pe-interruption.yaml` replaces the saved device's wake-word handler.
It requests local announcement/assistant stop before reporting a native API
event. A new wake word then starts listening after the old assistant stops;
`Stop`/`stop` only stops. Mute and ringing-timer handling are preserved. A second
detection cancels pending re-listening, and the re-listen timeout cannot force
a still-running assistant to start again.

This is a **candidate**, not a complete Eva2 voice route or an instruction to
flash either household device. It implements wake-word interruption, not
arbitrary-speech full duplex. The original delayed stop-word activation remains.

Build a private main YAML containing `packages.original: !include` pointing
to the saved device YAML, followed by the contents of the candidate. On
ESPHome 2026.6.2 the mapping-form `on_wake_word_detected.then` replaces the
original list-form handler. Do not include both configurations as sibling
packages: that can concatenate the old and new actions. Inspect the resolved
configuration for exactly one outer handler before compiling. Keep the device
configuration, secrets and generated firmware outside Git.

The `Eva voice interruption` event entity emits `stop_requested` or
`resume_requested`. This is an interrupt request, not proof of acoustic silence
or core cancellation. It needs no Home Assistant service-call permission.
The future Eva2 listener must bind the entity to the current device registry,
discard historical/replayed events, and cancel only the matching earlier voice
reply. An event does not authenticate the speaker or authorize tool effects.

Before deployment: connect that listener and the Eva2 conversation route,
verify late-answer suppression and mute/disconnection behavior, bind the saved
source to the installed firmware, and establish a tested device rollback.
Then measure acoustic interruption and continued conversation on actual hardware.

References: [ESPHome packages](https://esphome.io/components/packages/),
[template events](https://esphome.io/components/event/template/) and
[voice assistant](https://esphome.io/components/voice_assistant/).
Configuration validation is pinned to the devices' reported ESPHome 2026.6.2;
the current online documentation may describe newer behavior.


## Managed household release overlay

`deploy/living-core/household-voice.compose.yml` adds the private directory at
`/run/eva/household-voice` read-only, with `current.json` and `token`.
The managed cutover keeps both absent while staging, then verifies exact
container bindings and rechecks file digests after activation. It supports
coexistence with retained dashboard voice and connector/proactive overlays.
The household prefix is `EVA_HOUSEHOLD_VOICE_`; activation requires `ENABLED=true`,
`POLICY_DIR_HOST`, `POLICY_SHA256`, `TOKEN_SHA256`, and the overlay/base/delivery/
connector manifest SHA256 values, as checked by `_household_voice_settings`.
The directory and files require UID65532, modes0700/0600, canonical paths and no
unexpected contents. File digests cover exact bytes, including token newlines;
the runtime policy token digest covers the stripped token used on the wire.
This overlay is tested but still needs admission into a complete release input
set and actual HA/hardware verification before production use.
