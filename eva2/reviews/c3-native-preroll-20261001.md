# C3 native microphone pre-roll — independent review

## Decision

**GO for the narrow source correction, compiled candidate and recorded installed
delivery on Woonkamer. Physical conversation remains pending.**

Reviewer: `/root/open_questions_triage`, 1 October 2026. Scope: the working diff in
`eva_input_driver.cpp`, the delayed-CAPTURE case in
`test_native_duplex_input_firmware.py`, and
`eva2/evidence/product/c3-native-preroll-20261001.json`. No tests, builds, SSH,
provider requests or physical-device operations were performed by the reviewer.

## Findings

No blocking source regression found. The existing microphone queue can fill
while the input protocol is ARMED and waiting for CAPTURE, including during
silence. The correction discards only the oldest pre-roll bytes in that state
and retains the existing fixed storage. Queue changes remain under the existing
lock. Even-byte PCM alignment is preserved; the bounded chunk size prevents
underflow when calculating the discarded bytes.

Outside ARMED, overflow still invokes the existing failure path. The correction
does not alter capture tokens, epochs, sequence generation, control ordering,
microphone ownership, stopping, mute or reconnect handling. No PCM is sent before
CAPTURE. The new host case verifies the exact chronological retained suffix,
the unchanged capture token, and microphone sequences 1–26 after admission.
Existing admitted-overflow and stale-audio guards are retained.

## Candidate evidence

The recorded evidence reports two actual-driver/microphone-wrapper C++ host
checks with UBSan passing in **3.87 seconds**, and a firmware build succeeding in
**82.413 seconds**. These results were not independently rerun.

| Artifact | SHA256 |
| --- | --- |
| Reviewed input driver | `4c90e13ff636dd50d602ed1afc97f6dc86982fb632e24c4ea99d0290ed823b14` |
| Reviewed host test file | `4656ab5458f0b22d25442d8925ba2736101ffebb7d914def487663d83282d8de` |
| Reported compiled firmware, 2,936,032 bytes | `80c03833bb3108ad2651aa27cf6de82cfa81289def7f23d4d4df73ff0c6a8c14` |

The evidence source hash matches the reviewed file. It reports matching current
continuous-service source, household core bindings, work-return availability,
speaker credential, server CA and network-rule bindings. These are coordinator
readbacks, not a physical continuous-audio observation.

## Installed target review

Reviewed the updated evidence after installation. Before upload, the encrypted
API authenticated `home-assistant-voice-0a16e0` at `10.0.20.202`; its name and MAC
suffix `0A:16:E0` matched the intended Woonkamer target. The compiled driver hash
matched the reviewed source, the wrapper configuration remained unchanged, and
the actual OTA bytes contained the provisioned endpoint, credential and CA.

The generated-main hash differs from the historical receipt. This is explicitly
recorded, without claiming equality to the old build; current source/binary and
actual target bindings are the installation evidence.

One OTA upload of the binary hash above succeeded in **18.967 seconds**. The
post-update encrypted API authenticated the same name and MAC suffix, reported
ESPHome `2026.6.2` and compilation time `2026-10-01 13:42:40 +0200`, and the native
TCP peer was observed from `10.0.20.202` to `10.0.20.46:44715`. The previous binary
is retained, no automatic rollback occurred, and the other speaker firmware was
not changed. These coordinator readbacks were reviewed; no probe or upload was
repeated by the reviewer.

A TCP connection proves transport reachability only. The requested physical
trial—one initial wakeword, direct next speech during thinking, direct interruption
while speaking and the later original research return—remains explicitly open.

## Limits and remaining acceptance

Only the latest approximately **0.512 seconds** of pre-admission PCM are
retained. A sufficiently delayed CAPTURE may discard the beginning of an
utterance; this change is not a promise to preserve whole speech during an
arbitrary admission delay. Admitted-audio loss still fails closed.

This corrects a reproducible code hazard. The historical hardware fault's cause
has not been established. Compilation and host tests do not prove physical
continuous listening, acoustic echo cancellation, arbitrary-speech barge-in,
model latency, or family acceptance. C3.1 remains open until the actual required
speaker behavior is observed.

Development credit cost is unknown.
