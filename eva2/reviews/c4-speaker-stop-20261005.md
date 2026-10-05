# C4.1 speaker interruption and session stop review — 5 October 2026

Decision: **GO** for the bounded candidate, with the server installed before the
speaker firmware. Physical owner acceptance remains open.

Reviewer: `/root/voice_stop_diagnosis`. Source basis: `63c46d56` plus the reviewed
C4.1 working-tree changes. The reviewer independently checked the coordinator's
Python changes; the coordinator independently reviewed the reviewer's firmware
header and actual-driver regression changes.

The Python review covered `speaker_wire.py`, `speaker_input.py`,
`speaker_livekit_session.py`, `speaker_session.py`, `speaker_livekit_service.py`
and the associated microphone/session tests. END_SESSION=150 ends capture and
playback; LOCAL_STOP retains active-wakeword interruption and fresh capture.
MUTED remains terminal. Admission closes synchronously before asynchronous
cleanup, late audio cannot reopen capture, and a terminal command can overtake
a queued or already-closing wakeword interruption. The reported legacy cleanup
race is resolved by checking `channel.closing` before renewing capture.
Reconnection still requires a fresh local WAKE. Ending media does not issue a
background-work cancellation.

Silero now requires 200 ms of speech at activation threshold 0.7. The existing
LiveKit session retains speech interruption and disables false-interruption
resume, which this physical STOP sink does not implement. Classifier-policy
fixtures establish the intended segmentation change, not room acoustics.

Verification available for this review:

- Reviewer executed the actual C++ input driver and microphone wrapper under
  UBSan: **2 passed in 3.10 s**. Coverage includes explicit end, active wakeword
  relistening, terminal promotion during cleanup, mute, stale audio and reconnect.
- Coordinator reported **31 passed** in the exact existing b800 speaker image
  with LiveKit 1.8, covering VAD, TLS sessions, stop/reconnect and microphone
  protocol; **9 passed** for the legacy service in the retained offline environment.
- No new full test cycle was required for this final source review.

Compatibility: the new server accepts existing LOCAL_STOP/MUTED firmware, so
server-first installation preserves operation while firmware is replaced. Old
firmware retains its old explicit-stop behavior until updated. New firmware
emits END_SESSION, which an old server rejects; do not deploy firmware first.
If restoring the old server, restore old firmware first or keep the compatible
new server until that restoration is complete. Existing receipt barriers and
microphone shutdown confirmation remain in use.

No unresolved source finding. This review does not certify flashed firmware,
installed-service health, acoustic sensitivity or physical owner acceptance;
those delivery observations must be recorded separately.
