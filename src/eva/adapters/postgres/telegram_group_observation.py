"""Durable, expiring proof that Eva1 saw one sender inside one Telegram group."""

from __future__ import annotations

from datetime import UTC, datetime
import re

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    external_timeline_scope,
    OperationalTimelineError,
    PostgresOperationalTimeline,
)
from eva.contracts.base import JsonValue


_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_GROUP = re.compile(r"^-[0-9]{1,31}$")
_OPERATION = re.compile(r"^telegram-group-observation:telegram-canary:[A-Za-z0-9._:-]+$")


class PostgresTelegramGroupObservationStore:
    """Keep no message text: only sender, group, time, expiry and operation ID."""

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str) -> None:
        self._timeline = PostgresOperationalTimeline(
            engine,
            external_timeline_scope(
                tenant_id,
                installation_id,
                "Telegram group observation scope is required",
            ),
        )

    @staticmethod
    def _thread_id(*, principal_id: str, group_chat_id: str) -> str:
        return f"telegram-group-observation:{principal_id}:{group_chat_id}"

    def observe(
        self,
        *,
        principal_id: str,
        group_chat_id: str,
        operation_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        self._validate(principal_id, group_chat_id, operation_id, now, expires_at)
        thread_id = self._thread_id(
            principal_id=principal_id, group_chat_id=group_chat_id
        )
        data: dict[str, JsonValue] = {
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "group_chat_id": group_chat_id,
            "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "principal_id": principal_id,
        }
        if self._operation_exists(thread_id, operation_id):
            return
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:observed:1",
                operation_id=operation_id,
                occurred_at=now,
                data=data,
                event_type="TELEGRAM_GROUP_MEMBERSHIP_OBSERVED",
                phase="awaiting_private_group_consent",
                status="active",
            )
            return
        except CheckpointConflict:
            pass
        for _attempt in range(2):
            if self._operation_exists(thread_id, operation_id):
                return
            state = self._timeline.load(thread_id)
            try:
                self._timeline.transition(
                    thread_id=thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{thread_id}:observed:{state.revision + 1}",
                    operation_id=operation_id,
                    occurred_at=now,
                    data=data,
                    event_type="TELEGRAM_GROUP_MEMBERSHIP_OBSERVED",
                    phase="awaiting_private_group_consent",
                    status="active",
                )
                return
            except CheckpointConflict:
                continue
        raise RuntimeError("Telegram group observation changed concurrently")

    def recently_observed(
        self, *, principal_id: str, group_chat_id: str, now: datetime
    ) -> bool:
        if not _PRINCIPAL.fullmatch(principal_id) or not _GROUP.fullmatch(group_chat_id):
            return False
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now) or now.microsecond:
            return False
        thread_id = self._thread_id(
            principal_id=principal_id, group_chat_id=group_chat_id
        )
        try:
            events = self._timeline.events(thread_id)
        except OperationalTimelineError:
            return False
        if not events:
            return False
        data = events[-1].payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Telegram group observation payload is invalid")
        try:
            expires_at = datetime.strptime(
                str(data["expires_at"]), "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
        except (KeyError, ValueError) as error:
            raise RuntimeError("Telegram group observation payload is invalid") from error
        return (
            data.get("principal_id") == principal_id
            and data.get("group_chat_id") == group_chat_id
            and now <= expires_at
        )

    def _operation_exists(self, thread_id: str, operation_id: str) -> bool:
        try:
            return any(
                event.operation_id == operation_id
                for event in self._timeline.events(thread_id)
            )
        except OperationalTimelineError:
            return False

    @staticmethod
    def _validate(
        principal_id: str,
        group_chat_id: str,
        operation_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        if not _PRINCIPAL.fullmatch(principal_id) or not _GROUP.fullmatch(group_chat_id):
            raise ValueError("Telegram group observation identity is invalid")
        if not _OPERATION.fullmatch(operation_id):
            raise ValueError("Telegram group observation operation is invalid")
        for value in (now, expires_at):
            if (
                value.tzinfo is None
                or value.utcoffset() != UTC.utcoffset(value)
                or value.microsecond
            ):
                raise ValueError("Telegram group observation time is invalid")
        if not now < expires_at:
            raise ValueError("Telegram group observation expiry is invalid")


__all__ = ["PostgresTelegramGroupObservationStore"]
