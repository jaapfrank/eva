# Voice PE interruption candidate

The `custom_components/eva2_voice` directory now contains a **native Home
Assistant conversation-agent candidate**. Its HTTP client is tested against a
local HTTP server. The HA entry/configflow/pipeline have not been integration
tested and the Eva2 core endpoints below do not exist yet. Do not install or
select this agent as the household pipeline until those pieces are implemented
and the complete candidate passes its release checks.

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
