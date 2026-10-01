# C4.1 wake-word interruption and re-listen review — 1 October 2026

Bundled pre-install review of the existing ordinary Home Assistant route and `voice-pe-interruption.yaml`. The scoped outcome is one “Hey Eva” during a spoken answer stopping the old announcement and starting a new listen. Arbitrary full duplex is outside this review. The overlay and relevant ESPHome 2026.6.2 component source were read, alongside the official Home Assistant 2026.9.2 and its pinned aioesphomeapi 46.2.0 cancellation path. No live device requests, audio, inference, house actions or test/fixture replay were performed. No credential contents were read; full private YAML and generated C++ contents were not printed. Only this review file was written.

## Disposition

**GO for the reviewed source, successful exact OTA candidate and planned ordinary-HA installation route.** No concrete stop/restart transition blocker was found for the stated speaking case. The coordinator reports a successful compile in 212.472 seconds. The reviewer independently checked the current and preserved candidate OTA hashes: both are `81f70fae82f3ce6d631fe651511e85ce856fc8a0f1309d00362717c8140b18aa`, each 2,911,760 bytes. Upload must use this exact current OTA file. Installation verification and the physical one-wake-word outcome remain open.

The overlay first stops the local announcement and voice assistant, then waits for VA idle before requesting a new listen. ESPHome's media-state callback advances a stopped playing announcement through response-finished to idle, and its start method admits a new request only from idle. The script checks mute, timer and connectivity after the wait and again after the optional chime delay. A second detection cancels pending resumption. Timer ringing keeps priority, and the Stop wake word stops without re-listening. No background-task stop command is added.

Relevant overlay references: `voice-pe-interruption.yaml:23` for the idle wait, `:48` for the post-chime checks, `:70` for pending-script cancellation, `:73` for timer handling and `:85` for local announcement/VA stop. Relevant pinned component references: `voice_assistant.cpp:57` for announcement completion tracking, `:472` and `:513` for return to idle, `:675` for start admission and `:690` for stop handling.

## Corrected native cancellation trace

When ESPHome sends `VoiceAssistantRequest(start=False)`, aioesphomeapi maps it directly to `handle_stop(True)`; it does not require a separate abort flag. Home Assistant then aborts its active pipeline task. An ordinary audio-end message instead maps to `handle_stop(False)`. The optional template event is therefore telemetry, not a cancellation prerequisite. The earlier flag-based inference made during review was incomplete and is superseded by this exact client trace. Sources: [aioesphomeapi 46.2.0 client, lines 1992–2020](https://raw.githubusercontent.com/esphome/aioesphomeapi/v46.2.0/aioesphomeapi/client.py), [Home Assistant 2026.9.2 satellite, lines 581–586 and 727–736](https://raw.githubusercontent.com/home-assistant/core/2026.9.2/homeassistant/components/esphome/assist_satellite.py).

This establishes a native pipeline abort path, not provider/cloud cancellation or cancellation of separately accepted background work. The unused event must not be treated as an instruction to cancel all accepted tasks. The speaking-state overlay reuses the original stop behavior and adds the missing local re-listen transition.

## Installation inputs and remaining proof

The coordinator's retained readbacks bind the target to Woonkamer's current name/MAC/build, primary Eva2/Hey Eva selection and existing mute-on state. The private wrapper contains the exact reviewed overlay and one outer wake-word action. The saved source differs from the original only in the existing WiFi fields. These are coordinator-observed installation inputs; this review did not inspect credential contents or repeat those live readbacks.

The intended versions remain ESPHome 2026.6.2, framework 5.5.4 and external voice-pe commit `7c6eb245b1d5253ee8672fbbdb604767e59a8ee9`. The stale duplicate bootloader-object failure was isolated to an old generated target, which was preserved and renamed before compiling the same source successfully. No package reinstall, overlay change, bootloader or partition change was involved. The resulting OTA bytes reproduce the previously compiled, uninstalled candidate. The planned upload uses the existing private wrapper and exact current OTA file for `10.0.20.202`, followed by authenticated API and HA firmware/mute/assistant readback. Mute remains unchanged. No automatic rollback or audio/model/house-action check is included.

Artifact identifiers:

- Independently checked overlay SHA256: `be88d9e2e995748de7fcbac9d8a5024e7c8749aae720addc6e018f1e5b7f15b8`.
- Independently checked current OTA SHA256: `81f70fae82f3ce6d631fe651511e85ce856fc8a0f1309d00362717c8140b18aa`; 2,911,760 bytes, at `/home/administrator/project/.eva2-artifacts/voice-pe-interruption-candidate/.esphome/build/home-assistant-voice-0a16e0/.pioenvs/home-assistant-voice-0a16e0/firmware.ota.bin`.
- Independently checked generated-source SHA256: `133dbf8b18290dbb514d5878be131c1aabc4f08a47f55b800cdc36440634c92c`. The coordinator reports changed opaque IDs and one outer wake-word action in the resolved configuration; no semantic generated-source comparison is claimed.
- Coordinator-retained saved source SHA256: `87b9c4476dfee28c939b8f2578a6dc6f1fe3397368dd62f2268c50f753a1f1c1`.
- Coordinator-retained ordinary recovery binary SHA256: `222b87eefca70de27af8db210c06156c0f2dd34c82558ff2614d7173a93040bc`.

The retained recovery artifact provides a known route back; it is not an automatic rollback instruction. A successful source build and authenticated post-upload metadata check still do not prove that one physical “Hey Eva” silences the old speech and captures the new question. That trial, actual audibility and broader C4 acceptance remain explicitly open.

## Second-speaker target transfer

**The existing source/install GO transfers to the second speaker's exact OTA candidate** `0ff67e201a1bb4693a29a6e2fd6b2f686c8a2516e8de375fa1322cf9224b8491` (2,911,760 bytes). This appendix uses only `secondSpeaker` and `wakeModelAvailabilitySourceCheck` in `c4-device-boundary-20261001.json`; no new source review, test round or live request was performed.

The coordinator's evidence records successful compilation in 203.332 seconds, the identical reviewed overlay body/hash, one outer handler action and the resume script. Framework 5.5.4 and external voice-pe pin `7c6eb245b1d5253ee8672fbbdb604767e59a8ee9` are unchanged. Authenticated pre-install metadata binds the target to `home-assistant-voice-0a5035`, MAC `20:F8:3B:0A:50:35`, ESPHome 2026.6.2 and build `2026-06-21 21:08:16 +0200`. Planned ordinary OTA to `10.0.20.53`, followed by API event/HA selection/mute readback, adds no bootloader, partition or core change.

The saved-source check reports that the regular wake model stays enabled during spoken playback; only the separate Stop model is toggled around announcements/timers. This supports reuse of the reviewed transition, but does not measure echo, microphone sampling or physical detection of “Hey Eva”. Installation and physical outcome remain unobserved for this target.

Original-source recovery `c51dbff95df7ce1ac619b5f26d99ad372b35dc0bc66b8b8125def571c37bfff8` compiled successfully and is retained. It was neither extracted from current device flash nor physically reflashed. It is a retained recovery candidate, not proof of recovery operation or an automatic rollback instruction. The physical outcome and cloud-cancellation limits of the original review continue to apply.
