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
    external_timeline_scope,
)


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

    def claim_reply(self, *, device_id: str, request_id: str, now: datetime) -> bool:
        """Claim one reply before expiry. This is not an audible-delivery receipt.

        HA must still suppress cancelled HTTP responses. A stop after this commit
        cannot retract a response in flight; it remains recorded by interrupt().
        """
        key = self._key(device_id, request_id)
        state = self._timeline.load(key)
        if (state.phase != "voice_running" or state.deadline is None
                or now >= state.deadline):
            return False
        try:
            self._timeline.transition(
                thread_id=key, expected_revision=state.revision,
                checkpoint_id=f"{key}:{state.revision + 1}", occurred_at=now,
                data={}, event_type="VOICE_REPLY_CLAIMED", phase="voice_reply_claimed",
                status="completed", require_unexpired=True,
            )
            return True
        except CheckpointConflict:
            return False
