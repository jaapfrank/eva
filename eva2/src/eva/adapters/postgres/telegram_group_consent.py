"""Append-only per-person Telegram group-consent state."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    OperationalTimelineError,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.application.telegram_group_consent import TelegramGroupConsent
from eva.contracts.base import JsonValue


class PostgresTelegramGroupConsentStore:
    """Stores consent and withdrawal as immutable events, scoped to one installation."""

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("Telegram group consent scope is required")
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )

    @staticmethod
    def _thread_id(*, principal_id: str, group_chat_id: str) -> str:
        return f"telegram-group-consent:{principal_id}:{group_chat_id}"

    @staticmethod
    def _data(consent: TelegramGroupConsent) -> dict[str, JsonValue]:
        return {
            "expires_at": consent.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "granted_at": consent.granted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "group_chat_id": consent.group_chat_id,
            "principal_id": consent.principal_id,
        }

    def grant(
        self, consent: TelegramGroupConsent, *, operation_id: str
    ) -> TelegramGroupConsent:
        thread_id = self._thread_id(
            principal_id=consent.principal_id, group_chat_id=consent.group_chat_id
        )
        data = self._data(consent)
        prior = self._operation_data(thread_id, operation_id=operation_id)
        if prior is not None:
            return self._consent_from_data(prior)
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:granted:1",
                operation_id=operation_id,
                occurred_at=consent.granted_at,
                data=data,
                event_type="TELEGRAM_GROUP_CONSENT_GRANTED",
                phase="group_memory_shared",
                status="active",
            )
            return consent
        except CheckpointConflict:
            pass
        for _attempt in range(2):
            prior = self._operation_data(thread_id, operation_id=operation_id)
            if prior is not None:
                return self._consent_from_data(prior)
            state = self._timeline.load(thread_id)
            try:
                self._timeline.transition(
                    thread_id=thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{thread_id}:granted:{state.revision + 1}",
                    operation_id=operation_id,
                    occurred_at=consent.granted_at,
                    data=data,
                    event_type="TELEGRAM_GROUP_CONSENT_RENEWED",
                    phase="group_memory_shared",
                    status="active",
                )
                return consent
            except CheckpointConflict:
                continue
        raise RuntimeError("Telegram group consent changed concurrently")

    def revoke(
        self,
        *,
        principal_id: str,
        group_chat_id: str,
        operation_id: str,
        now: datetime,
    ) -> bool:
        thread_id = self._thread_id(
            principal_id=principal_id, group_chat_id=group_chat_id
        )
        prior = self._operation_data(thread_id, operation_id=operation_id)
        if prior is not None:
            return True
        try:
            state = self._timeline.load(thread_id)
        except OperationalTimelineError:
            return False
        if state.status != "active":
            return False
        try:
            self._timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:revoked:{state.revision + 1}",
                operation_id=operation_id,
                occurred_at=now,
                data={"group_chat_id": group_chat_id, "principal_id": principal_id},
                event_type="TELEGRAM_GROUP_CONSENT_REVOKED",
                phase="group_memory_not_shared",
                status="completed",
            )
            return True
        except CheckpointConflict:
            return False

    def permits(self, *, principal_id: str, group_chat_id: str, now: datetime) -> bool:
        thread_id = self._thread_id(
            principal_id=principal_id, group_chat_id=group_chat_id
        )
        try:
            state = self._timeline.load(thread_id)
        except OperationalTimelineError:
            return False
        if state.status != "active":
            return False
        events = self._timeline.events(thread_id)
        if not events:
            raise RuntimeError("Telegram group consent timeline is empty")
        data = events[-1].payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Telegram group consent payload is invalid")
        consent = self._consent_from_data(data)
        return consent.permits(
            principal_id=principal_id, group_chat_id=group_chat_id, now=now
        )

    def _operation_data(
        self, thread_id: str, *, operation_id: str
    ) -> dict[str, JsonValue] | None:
        for event in self._timeline.events(thread_id):
            if event.operation_id != operation_id:
                continue
            data = event.payload.get("data")
            if not isinstance(data, dict):
                raise RuntimeError("Telegram group consent payload is invalid")
            return data
        return None

    @staticmethod
    def _consent_from_data(data: dict[str, JsonValue]) -> TelegramGroupConsent:
        try:
            return TelegramGroupConsent(
                group_chat_id=str(data["group_chat_id"]),
                principal_id=str(data["principal_id"]),
                granted_at=datetime.strptime(
                    str(data["granted_at"]), "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=UTC),
                expires_at=datetime.strptime(
                    str(data["expires_at"]), "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=UTC),
            )
        except (KeyError, ValueError) as error:
            raise RuntimeError("Telegram group consent payload is invalid") from error


__all__ = ["PostgresTelegramGroupConsentStore"]
