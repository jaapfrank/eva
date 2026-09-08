# Voice PE interruption candidate

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
