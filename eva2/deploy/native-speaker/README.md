# Prepared continuous Voice PE packages

The [readback](provisioning-readback-2026-09-22.md) records the current installation
boundary; the [package receipt](package-receipt-2026-09-22.json) identifies the
prepared artifacts. Nothing here installs firmware or changes a running service.
Keep private packages outside Git: firmware binaries embed credentials.

Woonkamer reuses its reviewed candidate and rebuilt recovery binaries. Overkapping
has its own newly compiled candidate and recovery under the receipt's private
package path, in `binaries/candidate/` and `binaries/recovery/`. Do not interchange
the two devices' files. Failed preparation directories without a successful
verification receipt are not installation candidates.

`prepare_device_package.py` uses the retained ESPHome 2026.6.2 environment and
the existing native-control and firmware helpers. It accepts a private source
configuration, reviewed template package, existing household policy, exact
satellite/device binding and **new private output directory outside the checkout**.
It creates inputs only. Compile each `candidate/device.yaml` and
`recovery/device.yaml` with that pinned environment's `python -m esphome compile`;
never substitute `run` or `upload`. Keep compiler output private because validation
errors may contain resolved configuration values. The resulting recovery is a
configuration rebuild, not a captured copy of the running device's flash.

The combined private service package contains two fixed device grants and the
existing TLS certificate/key. Its image and configuration load were checked
offline. The runtime entry point remains `speaker_livekit_service.py` with
`--config`, `--policy`, `--household-token` and `--identity`. During a separately
reviewed installation, provision runtime credentials for the service identity
with owner-only access; the private preparation directories are root-owned and
are not directly readable by image UID 65532. Mount the current authority
directory read-only with an appropriate service access arrangement so policy
rotation remains observable; do not silently freeze authority in a copied
snapshot. Preserve current household identity and the lack of owner fallback.

The coordinator must activate the existing household-session core flag, verify
both authenticated readiness endpoints, and install only the candidate service
and narrowly scoped network rule(s). The previous core image/environment and
absence of the speaker service are the server rollback state. The current
Woonkamer-only firewall script does not already permit Overkapping. No such
activation or rule change occurred during preparation.

## Physical recovery boundary

Before flashing, the owner must connect the intended Voice PE by USB and provide
physical access to its boot/recovery controls. The operator must then:

1. Identify the actual serial device, chip, MAC and flash size. Match its MAC to
   the intended HA device; a display name or assumed COM port is insufficient.
   Do not use the observed Bluetooth COM3/COM4 as a Voice PE port.
2. With the existing esptool environment, read the **entire detected flash** into
   a new device-specific private file, hash it and retain it with the device
   identity and tool version. The locally available esptool reports 5.3.0.
   Confirm bootloader access and the exact restore command for the observed
   device and flash size before any write; neither OTA reachability nor a rebuilt
   factory binary proves this recovery route.
3. Keep the captured flash and the rebuilt recovery files distinct. Check that
   the original HA route can be restored without depending on the new speaker
   service. Install and verify only one device first; retain a separate backup
   and recovery record before considering the other device.

Physical tests must cover wakeword, audible receipt, a second question while work
continues, interruption, stop, mute, echo, existing media/timer controls and return
to the prior HA route. Passing the offline package checks is not physical recovery
verification or owner acceptance. Do not claim either until recorded separately.
