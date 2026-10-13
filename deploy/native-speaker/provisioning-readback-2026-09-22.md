# Continuous Voice PE provisioning readback

Read-only inspection on 2026-09-22, initially against main
`1b508456527ab1c760b73ccdeced3016e1cc4be4`. The preparation branch starts at
`904da0c92347d52bac79db34714181cf96084561`. This is installation evidence and a
bounded handoff, not another workboard. No firmware, running configuration, or
product code was changed. No model requests were made.

## Current devices and access

| Device | HA device ID | IP | Installed firmware |
| --- | --- | --- | --- |
| Woonkamer, `home-assistant-voice-0a16e0` | `9e877ca00f57b1b75e0c2e3318abc704` | `10.0.20.202` | ESPHome 2026.6.2, 2026-06-21 20:56:28 +0200 |
| Overkapping, `home-assistant-voice-0a5035` | `f075b8ba2b711d74fff7d4756c620d64` | `10.0.20.53` | ESPHome 2026.6.2, 2026-06-21 21:08:16 +0200 |

Both corresponding `assist_satellite.<device-name-with-underscores>_assist_satellite`
entities are enabled and their ESPHome integrations are loaded. Both report
`esp32-s3-devkitc-1`. Device IDs, addresses, API port 6053 and firmware versions
were read from current HA registries and configuration entries.

The existing authorized management route works: Supervisor WebSocket API,
Terminal & SSH 10.5.0, File editor 6.1.0 and ESPHome Device Builder 2026.8.2.
The active builder reports empty `configured` and `importable` device lists;
`/config/esphome` contains no device YAML and its Git HEAD tracks only `.gitignore`.
This does **not** mean the original configurations are unavailable.

Complete original and `eva-patched` configurations, plus their referenced secrets,
are retained in the existing private provisioning directory
`/home/administrator/project/Bottie/data/private/voice-pe-devices/`.
This directory is a private configuration source, not the product source checkout.
Each patched configuration's API encryption key matches its current HA entry;
all two secret references per configuration resolve. Configuration hashes:

- Woonkamer: `87b9c4476dfee28c939b8f2578a6dc6f1fe3397368dd62f2268c50f753a1f1c1`.
- Overkapping: `94c281fca6a73172bee78e8b0d7df6258c764cc4689cf9d0191c2bdf5a8c744a`.

Credentials stayed in memory/private files and were not printed. SSH used a
temporary first-use known-hosts file because no prior key was available in the
examined local known-hosts files. This was not an independent host-key identity
verification. No persistent SSH configuration was changed. The WebSocket request
type was checked against the official HA source:
[constants](https://raw.githubusercontent.com/home-assistant/core/2026.8.1/homeassistant/components/hassio/const.py),
[Supervisor handler](https://raw.githubusercontent.com/home-assistant/core/2026.8.1/homeassistant/components/hassio/websocket_api.py).

## Current service and package boundary

- Active core image:
  `sha256:c1f10ee31dd04dadc0f82c3ae2a29d0c81e0a1c505e76c42532e1a63bf33f89b`.
- `EVA_HOUSEHOLD_VOICE_SESSION_ENABLED=false`. Authenticated readiness requests
  for both device bindings return 404. No response/model endpoint was called.
- `eva2-native-speaker.service` is absent/inactive; no listener exists on 44715.
  Windows still has address `10.0.20.46`. The exact Woonkamer firewall query
  returned no rule; no firewall changes were made.
- Current household authority is enabled, contains both exact device bindings,
  and expires at `2026-10-08T23:37:17Z`.
- Retained continuous speaker image
  `sha256:8e29ceeee9b285987cd6234022a14159d0f0ba0821fdc3a6a74eb3700a8feb4a`
  has source revision `8c2e872e0184265f757f3ff5da9b70d16917dac2`.
  A disposable, read-only, network-disabled container confirmed its agent hash
  `d5d78d97bcc826b45d80d1bfbccd672d09e37f5f619dfb2ea4fbb05b1a861d97` and absence
  of `work_receipt_text` / `request_acknowledged`. It predates the working receipt
  implementation and must not be installed unchanged for this requirement.

The retained Woonkamer package is
`/home/administrator/project/.eva2-artifacts/woonkamer-speaker-install-hnd0_fwq`.
Its HA and household credentials still match current credentials. Its TLS leaf
is valid from 2026-09-09 to 2027-09-09, SAN `10.0.20.46`; the intended endpoint is
`wss://10.0.20.46:44715/eva-speaker/v1`. Its service configuration has one grant,
for Woonkamer only. Firmware configuration hash
`070f725dd32c916adfcf19f806a0f8288b03747e98c40ceca7ce4bfbf3eab75b` still matches,
including the current device API key. All retained binary hashes match receipts:

| Woonkamer artifact | SHA256 |
| --- | --- |
| Candidate OTA | `336faf8b0ca4de6883217011d0c456f3738b6f8aeaf69b959a5eb8114f99e961` |
| Candidate factory | `441482d07674f833ecc8f3fc5f372b00c01652719de9ec787db84317d1d25162` |
| Rebuilt recovery OTA | `222b87eefca70de27af8db210c06156c0f2dd34c82558ff2614d7173a93040bc` |
| Rebuilt recovery factory | `d4d7dbbc6646fa9fd3609fda8d3b836943551091fa65f94e33e63e7f5a6076bc` |

The recovery receipt explicitly says `prepared_not_hardware_verified`.
Rebuilding an old configuration does not prove restoration of the currently
installed full flash. Only Bluetooth COM3/COM4 were visible on Windows; no
Voice PE USB device was observed. No final Overkapping package or verified
hardware recovery was found; its original configuration and an older
interruption overlay are available.

## Bounded installation preparation and rollback

1. Compare the retained Woonkamer firmware inputs with current pinned native
   component sources. Reuse the existing binaries if unchanged. Generate the
   missing Overkapping package from its own private configuration using the
   existing `prepare_native_controls.py`, `prepare_speaker.py` and pinned
   ESPHome 2026.6.2 environment. Keep device names, API/OTA/Wi-Fi credentials,
   household bindings and existing house controls. Each device requires its
   own credential and configuration; never flash Woonkamer's binary on
   Overkapping. Do not upgrade to the current builder version implicitly.
2. Package the current receipt-capable source using the existing
   `deploy/voice-session/Dockerfile.speaker` and verified immutable media base.
   Reuse existing tests for receipt, second turn, speaker audio/wire and
   authorization. The old `eb6dfff...` systemd bundle describes the earlier
   native composition, not the continuous LiveKit entry point.
3. Prepare private service composition for `speaker_livekit_service.py`, with
   current household policy/token/identity, existing HA speech providers and
   TLS. No owner fallback. Prepare exact service/image/env rollback. Activating
   the existing core flag and checking both readiness responses is a separate
   coordinator installation action. The current preparation task does not
   change the running core, service or firewall.
4. Prepare narrowly scoped network bindings for the actual device IPs. The
   existing firewall script permits Woonkamer only; it does not already cover
   Overkapping. Retain rollback of only the newly added rules.
5. Before a physical installation, identify **one** actual device over USB,
   record its chip/MAC/flash size, read and hash its current complete flash, and
   verify the executable recovery route. Keep that backup device-specific.
   This needs physical owner hardware access, not new account credentials or
   configuration files. Prove one device before installing the second.
6. Rollback stops only the new speaker service, restores the exact preceding
   service/core flag/environment/firewall state, and restores that device's
   verified original flash. Confirm the former HA conversation route. Do not
   replace core data, the HA configuration tree or databases.

Software packaging and offline verification can proceed without owner input.
Immediate deployment on both devices is not established: server activation,
updated packaging, Overkapping provisioning and per-device physical recovery
remain necessary. Audio, wakeword, echo cancellation, concurrent second turn,
receipt, stop and mute require physical trials. Owner acceptance remains open.

Related historical evidence: `voice-household-livekit-candidate-2026-09-10.json`,
`VB-001-speaker-install-package-2026-09-09.json`,
`voice-polite-work-receipt-2026-09-22.json`, and
`voice-local-access-2026-09-22.json` under `development/evidence/product/`.
