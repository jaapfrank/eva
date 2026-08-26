"""Bounded, validated PostgreSQL projection for the people/memory cockpit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
)
from eva.application.people_memory_overview import (
    DynamicAccountState,
    GroupSharingState,
    HindsightState,
    MemoryFlowState,
    PeopleMemoryOverview,
    PeopleMemoryOverviewError,
)


_MAX_THREADS_PER_TRACK = 256
_EXPIRY_WARNING = timedelta(hours=24)


class PostgresPeopleMemoryOverview:
    """Read only the durable status cursors and discard every identifying value."""

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("people and memory overview scope is required")
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )

    def snapshot(
        self, *, now: datetime, hindsight_state: HindsightState
    ) -> PeopleMemoryOverview:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("people and memory snapshot time must be UTC")
        if now.microsecond:
            raise ValueError("people and memory snapshot time must use whole seconds")
        return PeopleMemoryOverview(
            dynamic_account=self._safe_dynamic_account(),
            group_sharing=self._safe_group_sharing(now),
            memory_flow=self._safe_memory_flow(),
            hindsight=hindsight_state,
        )

    def _validated_threads(
        self, prefix: str
    ) -> tuple[tuple[CheckpointState, tuple[TimelineEvent, ...]], ...]:
        states = self._timeline.list_checkpoints(
            thread_prefix=prefix,
            limit=_MAX_THREADS_PER_TRACK,
        )
        if len(states) == _MAX_THREADS_PER_TRACK:
            raise PeopleMemoryOverviewError(
                "people and memory overview track exceeded its closed bound"
            )
        validated = []
        for listed in states:
            current = self._timeline.load(listed.thread_id)
            if current != listed:
                raise PeopleMemoryOverviewError(
                    "people and memory checkpoint changed during projection"
                )
            events = self._timeline.events(listed.thread_id)
            if not events or events[-1].sequence != current.revision:
                raise PeopleMemoryOverviewError(
                    "people and memory timeline differs from its checkpoint"
                )
            validated.append((current, events))
        return tuple(validated)

    def _safe_dynamic_account(self) -> DynamicAccountState:
        try:
            threads = self._validated_threads("telegram-dynamic-binding:")
            for state, events in threads:
                first = events[0]
                data = first.payload.get("data")
                if (
                    state.phase != "bound"
                    or state.status != "completed"
                    or len(events) != 1
                    or first.event_type != "TELEGRAM_DYNAMIC_BINDING_CREATED"
                    or not isinstance(data, dict)
                    or set(data) != {"principal_id", "telegram_user_id"}
                    or not isinstance(data.get("principal_id"), str)
                    or not str(data["principal_id"]).strip()
                    or not isinstance(data.get("telegram_user_id"), int)
                    or state.thread_id
                    != f"telegram-dynamic-binding:{data['telegram_user_id']}"
                ):
                    raise PeopleMemoryOverviewError(
                        "dynamic Telegram binding status is malformed"
                    )
            return "extra_account_linked" if threads else "no_extra_account"
        except Exception:
            return "unavailable"

    def _safe_group_sharing(self, now: datetime) -> GroupSharingState:
        try:
            threads = self._validated_threads("telegram-group-consent:")
            active_expiries: list[datetime] = []
            for state, events in threads:
                latest = events[-1]
                data = latest.payload.get("data")
                if not isinstance(data, dict):
                    raise PeopleMemoryOverviewError(
                        "Telegram group consent status is malformed"
                    )
                principal_id = data.get("principal_id")
                group_chat_id = data.get("group_chat_id")
                if (
                    not isinstance(principal_id, str)
                    or not isinstance(group_chat_id, str)
                    or state.thread_id
                    != f"telegram-group-consent:{principal_id}:{group_chat_id}"
                ):
                    raise PeopleMemoryOverviewError(
                        "Telegram group consent binding is malformed"
                    )
                if state.status == "active" and state.phase == "group_memory_shared":
                    if latest.event_type not in {
                        "TELEGRAM_GROUP_CONSENT_GRANTED",
                        "TELEGRAM_GROUP_CONSENT_RENEWED",
                    } or set(data) != {
                        "expires_at",
                        "granted_at",
                        "group_chat_id",
                        "principal_id",
                    }:
                        raise PeopleMemoryOverviewError(
                            "active Telegram group consent status is malformed"
                        )
                    granted = self._parse_timestamp(data.get("granted_at"))
                    expiry = self._parse_timestamp(data.get("expires_at"))
                    if granted >= expiry:
                        raise PeopleMemoryOverviewError(
                            "Telegram group consent interval is malformed"
                        )
                    if granted <= now <= expiry:
                        active_expiries.append(expiry)
                elif not (
                    state.status == "completed"
                    and state.phase == "group_memory_not_shared"
                    and latest.event_type == "TELEGRAM_GROUP_CONSENT_REVOKED"
                    and set(data) == {"group_chat_id", "principal_id"}
                ):
                    raise PeopleMemoryOverviewError(
                        "Telegram group consent state is unknown"
                    )
            if not active_expiries:
                return "private_only"
            if any(expiry - now <= _EXPIRY_WARNING for expiry in active_expiries):
                return "expires_soon"
            return "group_active"
        except Exception:
            return "unavailable"

    def _safe_memory_flow(self) -> MemoryFlowState:
        try:
            threads = self._validated_threads("memory-retain:")
            observed: set[MemoryFlowState] = set()
            for state, events in threads:
                first = events[0]
                data = first.payload.get("data")
                if (
                    first.event_type != "MEMORY_RETAIN_QUEUED"
                    or not isinstance(data, dict)
                    or data.get("kind") != "hindsight_memory_document"
                ):
                    raise PeopleMemoryOverviewError(
                        "memory retention timeline is malformed"
                    )
                observed.add(self._memory_state(state))
            priorities: tuple[MemoryFlowState, ...] = (
                "needs_help",
                "retrying",
                "processing",
                "up_to_date",
            )
            for priority in priorities:
                if priority in observed:
                    return priority
            return "no_signals"
        except Exception:
            return "unavailable"

    @staticmethod
    def _memory_state(state: CheckpointState) -> MemoryFlowState:
        if state.status == "needs_help" and state.phase == "memory_needs_help":
            return "needs_help"
        if state.status == "failed" and state.phase == "memory_retry":
            return "retrying"
        if state.phase in {"memory_queued", "memory_retaining"} and state.status in {
            "active",
            "waiting_for_proof",
        }:
            return "processing"
        if state.status == "completed" and state.phase == "memory_retained":
            return "up_to_date"
        raise PeopleMemoryOverviewError("memory retention state is unknown")

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        if not isinstance(value, str):
            raise PeopleMemoryOverviewError("group consent expiry is malformed")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except ValueError as error:
            raise PeopleMemoryOverviewError(
                "group consent expiry is malformed"
            ) from error
        return parsed


__all__ = ["PostgresPeopleMemoryOverview"]
