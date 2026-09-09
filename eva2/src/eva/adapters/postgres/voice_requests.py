"""Durable, request-specific cancellation for native household voice.

The caller must authenticate and resolve the device before using this store.
This ledger grants no person identity or tool permissions. It suppresses future
delivery; it cannot retract audio already sent or undo an external effect.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import re

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineNotFound,
    external_timeline_scope,
)
from eva.application.audio_playback import AudioPlaybackResult, PlaybackConflict


class PostgresVoiceRequests:
    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str):
        self._timeline = PostgresOperationalTimeline(
            engine, external_timeline_scope(tenant_id, installation_id,
                                           "Voice installation scope is required")
        )

    @staticmethod
    def _key(device_id: str, request_id: str) -> str:
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value)
               for value in (device_id, request_id)):
            raise ValueError("Invalid native voice request identity")
        digest = hashlib.sha256(f"{device_id}:{request_id}".encode()).hexdigest()
        return f"ha-voice:{digest}"

    def begin(self, *, device_id: str, request_id: str, now: datetime) -> bool:
        """True only for the first admission, never for retries or tombstones.

        A worker lost to a crash is deliberately not restarted. The next spoken
        question gets a new ID. No transcript or spoken answer is stored here.
        """
        key = self._key(device_id, request_id)
        try:
            self._timeline.create_thread(
                thread_id=key, checkpoint_id=f"{key}:1", occurred_at=now,
                data={}, event_type="VOICE_REQUEST_STARTED", phase="voice_running",
                deadline=now + timedelta(seconds=32),
            )
            return True
        except CheckpointConflict:
            return False

    def interrupt(self, *, device_id: str, request_id: str, now: datetime) -> None:
        """Commit a stop even when its corresponding request has not arrived."""
        key = self._key(device_id, request_id)
        try:
            self._timeline.create_thread(
                thread_id=key, checkpoint_id=f"{key}:1", occurred_at=now,
                data={}, event_type="VOICE_REQUEST_INTERRUPTED", phase="voice_stopped",
                status="completed",
            )
            return
        except CheckpointConflict:
            pass
        for _ in range(4):
            state = self._timeline.load(key)
            if state.phase == "voice_stopped":
                return
            try:
                self._timeline.transition(
                    thread_id=key, expected_revision=state.revision,
                    checkpoint_id=f"{key}:{state.revision + 1}", occurred_at=now,
                    data={}, event_type="VOICE_REQUEST_INTERRUPTED",
                    phase="voice_stopped", status="completed",
                )
                return
            except CheckpointConflict:
                continue
        raise CheckpointConflict("Voice stop could not be committed")

    def claim_reply(self, *, device_id: str, request_id: str, now: datetime,
                    reply_sha256: str | None = None) -> bool:
        """Claim one reply before expiry. This is not an audible-delivery receipt.

        HA must still suppress cancelled HTTP responses. A stop after this commit
        cannot retract a response in flight; it remains recorded by interrupt().
        """
        key = self._key(device_id, request_id)
        if reply_sha256 is not None and (not isinstance(reply_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", reply_sha256)):
            raise PlaybackConflict("Invalid reply digest")
        state = self._timeline.load(key)
        if (state.phase != "voice_running" or state.deadline is None
                or now >= state.deadline):
            return False
        try:
            self._timeline.transition(
                thread_id=key, expected_revision=state.revision,
                checkpoint_id=f"{key}:{state.revision + 1}", occurred_at=now,
                data={} if reply_sha256 is None else {"reply_sha256": reply_sha256},
                event_type="VOICE_REPLY_CLAIMED", phase="voice_reply_claimed",
                status="completed", require_unexpired=True,
            )
            return True
        except CheckpointConflict:
            return False

    def _playback_events(self, key: str, reply_sha256: str):
        if not isinstance(reply_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", reply_sha256):
            raise PlaybackConflict("Invalid reply digest")
        claims, reports = [], []
        for event in self._timeline.events(key):
            if event.event_type == "VOICE_REPLY_CLAIMED":
                claims.append(event)
            elif event.event_type == "VOICE_PLAYBACK_REPORTED":
                reports.append(event)
        if (len(claims) != 1 or claims[0].payload.get("data", {}).get("reply_sha256") != reply_sha256):
            raise PlaybackConflict("Output result has no matching admitted reply")
        results = []
        for event in reports:
            data = event.payload.get("data", {})
            if data.get("reply_sha256") != reply_sha256:
                raise PlaybackConflict("Stored output result crossed its reply binding")
            results.append(AudioPlaybackResult.from_wire(data.get("result")))
        return claims[0], results

    def record_playback(self, *, device_id: str, request_id: str, reply_sha256: str,
                        result: AudioPlaybackResult, now: datetime) -> None:
        key = self._key(device_id, request_id)
        if not isinstance(result, AudioPlaybackResult):
            raise PlaybackConflict("Invalid output result")
        for _ in range(4):
            # Snapshot revision BEFORE reading immutable events, so a concurrent
            # stop/report cannot be overwritten by a stale event projection.
            try:
                state = self._timeline.load(key)
            except TimelineNotFound:
                raise PlaybackConflict("Output result has no admitted request") from None
            claim, previous = self._playback_events(key, reply_sha256)
            if previous:
                old = previous[-1]
                if old == result:
                    return  # Idempotent readback remains valid after the window.
                if (old.status != "unconfirmed" or result.status == "unconfirmed"
                        or (old.session_id, old.generation, old.sample_rate)
                        != (result.session_id, result.generation, result.sample_rate)
                        or result.offered_frames < old.offered_frames):
                    raise PlaybackConflict("Output result conflicts with prior evidence")
            # The 32s reply deadline is not an audio deadline. Allow bounded
            # synthesis/playback/reporting without reopening the core request.
            report_deadline = claim.occurred_at + timedelta(minutes=10)
            if not claim.occurred_at <= now < report_deadline:
                raise PlaybackConflict("Output reporting window expired")
            try:
                self._timeline.transition(thread_id=key, expected_revision=state.revision,
                    checkpoint_id=f"{key}:{state.revision + 1}", occurred_at=now,
                    data={"reply_sha256": reply_sha256, "result": result.to_wire()},
                    event_type="VOICE_PLAYBACK_REPORTED", phase=state.phase,
                    status="completed", commit_before=report_deadline)
                return
            except CheckpointConflict:
                continue
        raise PlaybackConflict("Output result could not be committed")

    def playback_status(self, *, device_id: str, request_id: str, reply_sha256: str) -> str:
        try:
            _, results = self._playback_events(self._key(device_id, request_id), reply_sha256)
        except PlaybackConflict:
            return "unconfirmed"
        return results[-1].status if results else "unconfirmed"
