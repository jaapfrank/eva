"""Durable admission for installation-scoped background resource budgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    OperationalTimelineError,
    PostgresOperationalTimeline,
    TimelineScope,
    insert_initial_timeline_thread,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.contracts.runtime_budget import RuntimeResourceBudget


@dataclass(frozen=True, slots=True)
class BackgroundReflectionAdmission:
    """One durable daily allow/deny decision without exposing a principal ID."""

    admitted: bool
    day: str
    person_used: int
    installation_used: int
    reason: str


@dataclass(frozen=True, slots=True)
class MemoryStorageAdmission:
    """Durable hierarchical record/byte decision for one semantic document."""

    admitted: bool
    replayed: bool
    content_bytes: int
    person_records_used: int
    person_bytes_used: int
    group_records_used: int | None
    group_bytes_used: int | None
    project_records_used: int | None
    project_bytes_used: int | None
    installation_records_used: int
    installation_bytes_used: int
    retention_until: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class _MemoryStorageUsage:
    person_records: int
    person_bytes: int
    group_records: int | None
    group_bytes: int | None
    project_records: int | None
    project_bytes: int | None
    installation_records: int
    installation_bytes: int


@dataclass(frozen=True, slots=True)
class MemoryExpiryJob:
    """One leased Hindsight deletion whose retry is safe after a crash."""

    state: CheckpointState
    bank_id: str
    document_id: str
    document_sha256: str
    content_sha256: str
    retention_until: str
    principal_sha256: str
    source_message_id: str | None = None


class PostgresDailyReflectionBudget:
    """Serialize daily reflection usage on Eva's existing append-only timeline."""

    def __init__(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        installation_id: str,
        resource_budget: RuntimeResourceBudget,
    ) -> None:
        if (
            resource_budget.tenant_id != tenant_id
            or resource_budget.installation_id != installation_id
        ):
            raise ValueError("reflection budget crossed its runtime scope")
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )
        self._person_limit = resource_budget.background_reflections_per_person_day
        self._installation_limit = (
            resource_budget.background_reflections_per_installation_day
        )
        self._source_budget_sha256 = resource_budget.source_budget_sha256

    @staticmethod
    def _day(now: datetime) -> str:
        if now.tzinfo is None or now.utcoffset() is None or now.microsecond:
            raise ValueError("reflection budget time must be whole-second UTC")
        return now.astimezone(UTC).date().isoformat()

    @staticmethod
    def _principal_sha256(principal_id: str) -> str:
        if not principal_id.strip() or len(principal_id) > 240:
            raise ValueError("reflection budget principal is invalid")
        return hashlib.sha256(principal_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _usage(
        event_data: object,
        *,
        expected_day: str,
        expected_person_limit: int,
        expected_installation_limit: int,
        expected_source_budget_sha256: str,
    ) -> tuple[int, dict[str, int]]:
        if not isinstance(event_data, dict):
            raise OperationalTimelineError("reflection budget event is malformed")
        if set(event_data) != {
            "admitted",
            "contract",
            "day",
            "installation_limit",
            "installation_used",
            "people",
            "person_limit",
            "principal_sha256",
            "reason",
            "source_budget_sha256",
        }:
            raise OperationalTimelineError("reflection budget event is malformed")
        people = event_data.get("people")
        if (
            event_data.get("contract") != "EvaDailyReflectionBudget/v1"
            or event_data.get("day") != expected_day
            or event_data.get("person_limit") != expected_person_limit
            or event_data.get("installation_limit") != expected_installation_limit
            or event_data.get("source_budget_sha256") != expected_source_budget_sha256
            or not isinstance(event_data.get("installation_used"), int)
            or not isinstance(people, dict)
            or any(
                not isinstance(key, str)
                or len(key) != 64
                or not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for key, value in people.items()
            )
        ):
            raise OperationalTimelineError("reflection budget event is malformed")
        installation_used = event_data["installation_used"]
        if (
            isinstance(installation_used, bool)
            or installation_used < 0
            or installation_used != sum(people.values())
        ):
            raise OperationalTimelineError("reflection budget totals differ")
        return installation_used, dict(people)

    def admit_background_reflection(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        now: datetime,
        reservation_id: str | None = None,
    ) -> BackgroundReflectionAdmission:
        """Consume at most one slot; revision conflicts are retried, never widened."""

        if tenant_id != self._tenant_id or installation_id != self._installation_id:
            raise ValueError("reflection admission crossed its runtime scope")
        day = self._day(now)
        principal_sha256 = self._principal_sha256(principal_id)
        thread_id = f"runtime-reflections:{day}"
        for _attempt in range(32):
            try:
                state = self._timeline.load(thread_id)
            except OperationalTimelineError as error:
                if str(error) == (
                    "checkpoint projection differs from append-only timeline"
                ):
                    continue
                if str(error) != "thread does not exist":
                    raise
                installation_used = 0
                people: dict[str, int] = {}
                state = None
            else:
                events = self._timeline.events(thread_id)
                if not events:
                    raise OperationalTimelineError(
                        "reflection budget timeline is empty"
                    )
                data = events[-1].payload.get("data")
                installation_used, people = self._usage(
                    data,
                    expected_day=day,
                    expected_person_limit=self._person_limit,
                    expected_installation_limit=self._installation_limit,
                    expected_source_budget_sha256=self._source_budget_sha256,
                )

            person_used = people.get(principal_sha256, 0)
            if person_used >= self._person_limit:
                admitted = False
                reason = "person_daily_limit_reached"
            elif installation_used >= self._installation_limit:
                admitted = False
                reason = "installation_daily_limit_reached"
            else:
                admitted = True
                reason = "reserved"
                person_used += 1
                installation_used += 1
                people[principal_sha256] = person_used

            data = {
                "admitted": admitted,
                "contract": "EvaDailyReflectionBudget/v1",
                "day": day,
                "installation_limit": self._installation_limit,
                "installation_used": installation_used,
                "people": people,
                "person_limit": self._person_limit,
                "principal_sha256": principal_sha256,
                "reason": reason,
                "source_budget_sha256": self._source_budget_sha256,
            }
            try:
                if state is None:
                    self._timeline.create_thread(
                        thread_id=thread_id,
                        checkpoint_id=f"{thread_id}:1",
                        occurred_at=now.astimezone(UTC),
                        operation_id=reservation_id,
                        data=data,
                        event_type=(
                            "RUNTIME_REFLECTION_RESERVED"
                            if admitted
                            else "RUNTIME_REFLECTION_BLOCKED"
                        ),
                        phase="reflection_budget_active",
                    )
                else:
                    self._timeline.transition(
                        thread_id=thread_id,
                        expected_revision=state.revision,
                        checkpoint_id=f"{thread_id}:{state.revision + 1}",
                        phase="reflection_budget_active",
                        status="active",
                        event_type=(
                            "RUNTIME_REFLECTION_RESERVED"
                            if admitted
                            else "RUNTIME_REFLECTION_BLOCKED"
                        ),
                        occurred_at=now.astimezone(UTC),
                        operation_id=reservation_id,
                        data=data,
                    )
            except CheckpointConflict:
                continue
            return BackgroundReflectionAdmission(
                admitted=admitted,
                day=day,
                person_used=person_used,
                installation_used=installation_used,
                reason=reason,
            )
        raise OperationalTimelineError("reflection budget admission remained busy")


class PostgresMemoryStorageBudget(PostgresScopedRole):
    """Keep conversation-memory records and UTF-8 bytes inside owner limits.

    The append-only events contain hashes, byte counts and the provider deletion
    address, never the memory content.  One installation advisory lock makes
    the person, group, project and installation checks one atomic reservation
    across runtime processes. Capacity is released only after Hindsight has
    acknowledged deletion, so an overdue provider cannot make the physical
    store silently exceed any configured cap.
    """

    _role = "eva_runtime"

    def __init__(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        installation_id: str,
        resource_budget: RuntimeResourceBudget,
        removal_lease_seconds: int = 60,
    ) -> None:
        if (
            resource_budget.tenant_id != tenant_id
            or resource_budget.installation_id != installation_id
        ):
            raise ValueError("memory storage budget crossed its runtime scope")
        if not 1 <= removal_lease_seconds <= 3_600:
            raise ValueError("memory removal lease is outside 1..3600 seconds")
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("memory storage budget requires PostgreSQL")
        self._engine = engine
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._scope_value = TimelineScope(
            tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
            installation_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
        )
        self._timeline = PostgresOperationalTimeline(engine, self._scope_value)
        self._person_record_limit = resource_budget.memory_records_per_person
        self._person_byte_limit = resource_budget.memory_bytes_per_person
        self._group_record_limit = resource_budget.memory_records_per_group
        self._group_byte_limit = resource_budget.memory_bytes_per_group
        self._project_record_limit = resource_budget.memory_records_per_project
        self._project_byte_limit = resource_budget.memory_bytes_per_project
        self._installation_record_limit = (
            resource_budget.memory_records_per_installation
        )
        self._installation_byte_limit = resource_budget.memory_bytes_per_installation
        self._retention_days = resource_budget.retention_days
        self._source_budget_sha256 = resource_budget.source_budget_sha256
        self._removal_lease_seconds = removal_lease_seconds

    @staticmethod
    def _require_now(now: datetime) -> datetime:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("memory budget time must be timezone-aware UTC")
        if now.microsecond:
            raise ValueError("memory budget time must use whole seconds")
        return now

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _parse_timestamp(value: object, *, label: str) -> datetime:
        if not isinstance(value, str):
            raise OperationalTimelineError(f"memory storage {label} is malformed")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError as error:
            raise OperationalTimelineError(
                f"memory storage {label} is malformed"
            ) from error
        return parsed

    @staticmethod
    def _principal_sha256(principal_id: str) -> str:
        if not principal_id.strip() or len(principal_id) > 240:
            raise ValueError("memory storage principal is invalid")
        return hashlib.sha256(principal_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _scope_sha256(value: str, *, label: str) -> str:
        if not value.strip() or len(value) > 240:
            raise ValueError(f"memory storage {label} is invalid")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _document_sha256(document_id: str) -> str:
        if not document_id.strip() or len(document_id) > 1_024:
            raise ValueError("memory storage document ID is invalid")
        return hashlib.sha256(document_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _first_data(payload: object) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise OperationalTimelineError("memory storage event is malformed")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OperationalTimelineError("memory storage event is malformed")
        return data

    def _usage_pair(
        self,
        connection: sa.Connection,
        *,
        scope_field: str | None = None,
        scope_sha256: str | None = None,
    ) -> tuple[int, int]:
        data = operational_events.c.payload["data"]
        statement = (
            sa.select(
                sa.func.count(),
                sa.func.coalesce(
                    sa.func.sum(sa.cast(data["content_bytes"].astext, sa.BigInteger)),
                    0,
                ),
            )
            .select_from(
                operational_events.join(
                    operational_checkpoints,
                    sa.and_(
                        operational_events.c.tenant_id
                        == operational_checkpoints.c.tenant_id,
                        operational_events.c.installation_id
                        == operational_checkpoints.c.installation_id,
                        operational_events.c.thread_id
                        == operational_checkpoints.c.thread_id,
                    ),
                )
            )
            .where(
                operational_events.c.tenant_id == self._scope_value.tenant_id,
                operational_events.c.installation_id
                == self._scope_value.installation_id,
                operational_events.c.sequence == 1,
                operational_events.c.event_type == "MEMORY_STORAGE_RESERVED",
                operational_checkpoints.c.phase.notin_(
                    ("memory_storage_blocked", "memory_storage_removed")
                ),
            )
        )
        if scope_field is not None:
            if scope_sha256 is None:
                raise ValueError("memory storage scope hash is missing")
            statement = statement.where(data[scope_field].astext == scope_sha256)
        row = connection.execute(statement).one()
        return int(row[0]), int(row[1])

    def _usage(
        self,
        connection: sa.Connection,
        *,
        principal_sha256: str,
        group_sha256: str | None,
        project_sha256: str | None,
    ) -> _MemoryStorageUsage:
        person_records, person_bytes = self._usage_pair(
            connection,
            scope_field="principal_sha256",
            scope_sha256=principal_sha256,
        )
        if group_sha256 is None:
            group_records = group_bytes = None
        else:
            group_records, group_bytes = self._usage_pair(
                connection,
                scope_field="group_sha256",
                scope_sha256=group_sha256,
            )
        if project_sha256 is None:
            project_records = project_bytes = None
        else:
            project_records, project_bytes = self._usage_pair(
                connection,
                scope_field="project_sha256",
                scope_sha256=project_sha256,
            )
        installation_records, installation_bytes = self._usage_pair(connection)
        return _MemoryStorageUsage(
            person_records=person_records,
            person_bytes=person_bytes,
            group_records=group_records,
            group_bytes=group_bytes,
            project_records=project_records,
            project_bytes=project_bytes,
            installation_records=installation_records,
            installation_bytes=installation_bytes,
        )

    def _active_legacy_reservations(self, connection: sa.Connection) -> int:
        data = operational_events.c.payload["data"]
        return int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(
                    operational_events.join(
                        operational_checkpoints,
                        sa.and_(
                            operational_events.c.tenant_id
                            == operational_checkpoints.c.tenant_id,
                            operational_events.c.installation_id
                            == operational_checkpoints.c.installation_id,
                            operational_events.c.thread_id
                            == operational_checkpoints.c.thread_id,
                        ),
                    )
                )
                .where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.sequence == 1,
                    operational_events.c.event_type == "MEMORY_STORAGE_RESERVED",
                    data["contract"].astext == "EvaMemoryStorageAdmission/v1",
                    operational_checkpoints.c.phase.notin_(
                        ("memory_storage_blocked", "memory_storage_removed")
                    ),
                )
            )
            or 0
        )

    def _admission(
        self,
        *,
        admitted: bool,
        replayed: bool,
        content_bytes: int,
        usage: _MemoryStorageUsage,
        retention_until: str | None,
        reason: str,
    ) -> MemoryStorageAdmission:
        return MemoryStorageAdmission(
            admitted=admitted,
            replayed=replayed,
            content_bytes=content_bytes,
            person_records_used=usage.person_records,
            person_bytes_used=usage.person_bytes,
            group_records_used=usage.group_records,
            group_bytes_used=usage.group_bytes,
            project_records_used=usage.project_records,
            project_bytes_used=usage.project_bytes,
            installation_records_used=usage.installation_records,
            installation_bytes_used=usage.installation_bytes,
            retention_until=retention_until,
            reason=reason,
        )

    def admit_memory(
        self, document: ConversationMemoryDocument, *, now: datetime
    ) -> MemoryStorageAdmission:
        """Atomically reserve one record and its exact UTF-8 byte length."""

        now = self._require_now(now)
        if (
            document.tenant_id != self._tenant_id
            or document.installation_id != self._installation_id
        ):
            raise ValueError("memory admission crossed its runtime scope")
        principal_sha256 = self._principal_sha256(document.principal_id)
        group_sha256 = (
            None
            if document.audience.kind != "telegram_group"
            else self._scope_sha256(document.audience.audience_id, label="group")
        )
        project_sha256 = (
            None
            if document.project_id is None
            else self._scope_sha256(document.project_id, label="project")
        )
        document_sha256 = self._document_sha256(document.document_id)
        content_sha256 = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        content_bytes = len(document.content.encode("utf-8"))
        retention_until = now + timedelta(days=self._retention_days)
        retention_text = self._timestamp(retention_until)
        thread_id = f"memory-storage:{document_sha256}"

        with self._engine.begin() as connection:
            self._scope(connection)
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"{self._scope_value.tenant_id}:"
                        f"{self._scope_value.installation_id}:"
                        "memory-storage:installation"
                    )
                },
            )
            existing = connection.execute(
                sa.select(
                    operational_checkpoints.c.phase,
                    operational_events.c.payload,
                )
                .select_from(
                    operational_checkpoints.join(
                        operational_events,
                        sa.and_(
                            operational_checkpoints.c.tenant_id
                            == operational_events.c.tenant_id,
                            operational_checkpoints.c.installation_id
                            == operational_events.c.installation_id,
                            operational_checkpoints.c.thread_id
                            == operational_events.c.thread_id,
                        ),
                    )
                )
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                    operational_events.c.sequence == 1,
                )
            ).one_or_none()
            if existing is not None:
                data = self._first_data(existing.payload)
                if (
                    data.get("contract") != "EvaMemoryStorageAdmission/v2"
                    or data.get("audience_kind") != document.audience.kind
                    or data.get("content_bytes") != content_bytes
                    or data.get("content_sha256") != content_sha256
                    or data.get("document_sha256") != document_sha256
                    or data.get("group_sha256") != group_sha256
                    or data.get("principal_sha256") != principal_sha256
                    or data.get("project_sha256") != project_sha256
                    or data.get("person_record_limit") != self._person_record_limit
                    or data.get("person_byte_limit") != self._person_byte_limit
                    or data.get("group_record_limit") != self._group_record_limit
                    or data.get("group_byte_limit") != self._group_byte_limit
                    or data.get("project_record_limit") != self._project_record_limit
                    or data.get("project_byte_limit") != self._project_byte_limit
                    or data.get("installation_record_limit")
                    != self._installation_record_limit
                    or data.get("installation_byte_limit")
                    != self._installation_byte_limit
                    or data.get("source_budget_sha256") != self._source_budget_sha256
                ):
                    raise OperationalTimelineError(
                        "memory storage replay differs from its reservation"
                    )
                admitted = data.get("admitted") is True
                if data.get("admitted") not in {True, False}:
                    raise OperationalTimelineError(
                        "memory storage replay decision is malformed"
                    )
                usage = self._usage(
                    connection,
                    principal_sha256=principal_sha256,
                    group_sha256=group_sha256,
                    project_sha256=project_sha256,
                )
                stored_retention = data.get("retention_until")
                if admitted:
                    self._parse_timestamp(stored_retention, label="retention deadline")
                elif stored_retention is not None:
                    raise OperationalTimelineError(
                        "blocked memory storage replay has a retention deadline"
                    )
                reason = data.get("reason")
                allowed_reasons = (
                    {"reserved"}
                    if admitted
                    else {
                        "person_record_limit_reached",
                        "person_byte_limit_reached",
                        "group_record_limit_reached",
                        "group_byte_limit_reached",
                        "project_record_limit_reached",
                        "project_byte_limit_reached",
                        "installation_record_limit_reached",
                        "installation_byte_limit_reached",
                        "legacy_scope_accounting_unavailable",
                    }
                )
                if reason not in allowed_reasons:
                    raise OperationalTimelineError(
                        "memory storage replay reason is malformed"
                    )
                return self._admission(
                    admitted=admitted,
                    replayed=True,
                    content_bytes=content_bytes,
                    usage=usage,
                    retention_until=(stored_retention if admitted else None),  # type: ignore[arg-type]
                    reason=reason,  # type: ignore[arg-type]
                )

            usage = self._usage(
                connection,
                principal_sha256=principal_sha256,
                group_sha256=group_sha256,
                project_sha256=project_sha256,
            )
            if (
                group_sha256 is not None or project_sha256 is not None
            ) and self._active_legacy_reservations(connection):
                admitted = False
                reason = "legacy_scope_accounting_unavailable"
            elif usage.person_records >= self._person_record_limit:
                admitted = False
                reason = "person_record_limit_reached"
            elif usage.person_bytes + content_bytes > self._person_byte_limit:
                admitted = False
                reason = "person_byte_limit_reached"
            elif (
                usage.group_records is not None
                and usage.group_records >= self._group_record_limit
            ):
                admitted = False
                reason = "group_record_limit_reached"
            elif (
                usage.group_bytes is not None
                and usage.group_bytes + content_bytes > self._group_byte_limit
            ):
                admitted = False
                reason = "group_byte_limit_reached"
            elif (
                usage.project_records is not None
                and usage.project_records >= self._project_record_limit
            ):
                admitted = False
                reason = "project_record_limit_reached"
            elif (
                usage.project_bytes is not None
                and usage.project_bytes + content_bytes > self._project_byte_limit
            ):
                admitted = False
                reason = "project_byte_limit_reached"
            elif usage.installation_records >= self._installation_record_limit:
                admitted = False
                reason = "installation_record_limit_reached"
            elif (
                usage.installation_bytes + content_bytes > self._installation_byte_limit
            ):
                admitted = False
                reason = "installation_byte_limit_reached"
            else:
                admitted = True
                reason = "reserved"
                usage = _MemoryStorageUsage(
                    person_records=usage.person_records + 1,
                    person_bytes=usage.person_bytes + content_bytes,
                    group_records=(
                        None if usage.group_records is None else usage.group_records + 1
                    ),
                    group_bytes=(
                        None
                        if usage.group_bytes is None
                        else usage.group_bytes + content_bytes
                    ),
                    project_records=(
                        None
                        if usage.project_records is None
                        else usage.project_records + 1
                    ),
                    project_bytes=(
                        None
                        if usage.project_bytes is None
                        else usage.project_bytes + content_bytes
                    ),
                    installation_records=usage.installation_records + 1,
                    installation_bytes=usage.installation_bytes + content_bytes,
                )
            data = {
                "admitted": admitted,
                "audience_kind": document.audience.kind,
                "content_bytes": content_bytes,
                "content_sha256": content_sha256,
                "contract": "EvaMemoryStorageAdmission/v2",
                "document_sha256": document_sha256,
                "group_byte_limit": self._group_byte_limit,
                "group_record_limit": self._group_record_limit,
                "group_sha256": group_sha256,
                "installation_byte_limit": self._installation_byte_limit,
                "installation_record_limit": self._installation_record_limit,
                "person_byte_limit": self._person_byte_limit,
                "person_record_limit": self._person_record_limit,
                "principal_sha256": principal_sha256,
                "project_byte_limit": self._project_byte_limit,
                "project_record_limit": self._project_record_limit,
                "project_sha256": project_sha256,
                "reason": reason,
                "retention_until": retention_text if admitted else None,
                "source_budget_sha256": self._source_budget_sha256,
            }
            insert_initial_timeline_thread(
                connection,
                self._scope_value,
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:admission",
                occurred_at=now,
                data=data,
                event_type=(
                    "MEMORY_STORAGE_RESERVED" if admitted else "MEMORY_STORAGE_BLOCKED"
                ),
                phase=(
                    "memory_storage_reserved" if admitted else "memory_storage_blocked"
                ),
                status="active" if admitted else "completed",
                deadline=retention_until if admitted else None,
            )
        return self._admission(
            admitted=admitted,
            replayed=False,
            content_bytes=content_bytes,
            usage=usage,
            retention_until=retention_text if admitted else None,
            reason=reason,
        )

    def mark_retained(
        self,
        document: ConversationMemoryDocument,
        receipt: MemoryRetentionReceipt,
        *,
        now: datetime,
    ) -> CheckpointState:
        """Bind provider success before the durable outbox is completed."""

        now = self._require_now(now)
        document_sha256 = self._document_sha256(document.document_id)
        thread_id = f"memory-storage:{document_sha256}"
        state = self._timeline.load(thread_id)
        if state.phase in {"memory_storage_retained", "memory_storage_removing"}:
            data = self._validate_retention_data(
                self._first_data(self._timeline.events(thread_id)[-1].payload)
                if state.phase == "memory_storage_retained"
                else self._retained_event_data(thread_id)
            )
            if (
                data["document_id"] != document.document_id
                or data["content_sha256"]
                != hashlib.sha256(document.content.encode("utf-8")).hexdigest()
                or data["bank_id"] != receipt.bank_id
            ):
                raise OperationalTimelineError("memory retention replay differs")
            return state
        if state.phase == "memory_storage_removed":
            raise OperationalTimelineError("removed memory cannot be retained again")
        if state.phase != "memory_storage_reserved" or state.deadline is None:
            raise OperationalTimelineError("memory was not admitted for retention")
        content_sha256 = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        if (
            receipt.document_id != document.document_id
            or receipt.content_sha256 != content_sha256
        ):
            raise OperationalTimelineError("memory retention receipt differs")
        retention_text = self._timestamp(state.deadline)
        return self._timeline.transition(
            thread_id=thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"{thread_id}:retained",
            phase="memory_storage_retained",
            status="active",
            event_type="MEMORY_STORAGE_RETAINED",
            occurred_at=now,
            deadline=state.deadline,
            data={
                "bank_id": receipt.bank_id,
                "content_sha256": content_sha256,
                "contract": "EvaMemoryStorageRetention/v1",
                "document_id": document.document_id,
                "document_sha256": document_sha256,
                "retention_until": retention_text,
            },
        )

    def _retained_event_data(self, thread_id: str) -> dict[str, object]:
        for event in reversed(self._timeline.events(thread_id)):
            if getattr(event, "event_type", None) != "MEMORY_STORAGE_RETAINED":
                continue
            payload = getattr(event, "payload", None)
            return self._validate_retention_data(self._first_data(payload))
        raise OperationalTimelineError("memory storage retention event is missing")

    @staticmethod
    def _validate_retention_data(data: dict[str, object]) -> dict[str, object]:
        if (
            set(data)
            != {
                "bank_id",
                "content_sha256",
                "contract",
                "document_id",
                "document_sha256",
                "retention_until",
            }
            or data.get("contract") != "EvaMemoryStorageRetention/v1"
        ):
            raise OperationalTimelineError(
                "memory storage retention event is malformed"
            )
        return data

    def request_erasure(
        self,
        *,
        principal_id: str,
        document_id: str,
        now: datetime,
        source_message_id: str | None = None,
    ) -> MemoryExpiryJob | None:
        """Lease one exact principal-scoped delete before calling Hindsight."""

        now = self._require_now(now)
        principal_sha256 = self._principal_sha256(principal_id)
        if source_message_id is not None and (
            not source_message_id.strip() or len(source_message_id) > 240
        ):
            raise ValueError("memory erasure source message is invalid")
        document_sha256 = self._document_sha256(document_id)
        thread_id = f"memory-storage:{document_sha256}"
        with self._engine.begin() as connection:
            self._scope(connection)
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"{self._scope_value.tenant_id}:"
                        f"{self._scope_value.installation_id}:"
                        "memory-storage:installation"
                    )
                },
            )
            row = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise OperationalTimelineError("memory erasure target does not exist")
            state = CheckpointState(
                thread_id=str(row.thread_id),
                checkpoint_id=str(row.checkpoint_id),
                operation_id=(
                    None if row.operation_id is None else str(row.operation_id)
                ),
                phase=str(row.phase),  # type: ignore[arg-type]
                status=str(row.status),  # type: ignore[arg-type]
                retry_count=int(row.retry_count),
                last_error_fingerprint=(
                    None
                    if row.last_error_fingerprint is None
                    else bytes(row.last_error_fingerprint).hex()
                ),
                deadline=row.deadline,
                revision=int(row.revision),
                updated_at=row.updated_at,
            )
            initial = connection.execute(
                sa.select(operational_events.c.payload).where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id == thread_id,
                    operational_events.c.sequence == 1,
                )
            ).one_or_none()
            initial_data = self._first_data(
                None if initial is None else initial.payload
            )
            if (
                initial_data.get("contract") != "EvaMemoryStorageAdmission/v2"
                or initial_data.get("document_sha256") != document_sha256
                or initial_data.get("principal_sha256") != principal_sha256
            ):
                raise OperationalTimelineError("memory erasure scope differs")
            if state.phase in {"memory_storage_removing", "memory_storage_removed"}:
                return None
            if state.phase != "memory_storage_retained":
                raise OperationalTimelineError(
                    "memory erasure target is not durably retained"
                )
            retained_data = self._validate_retention_data(
                self._retained_event_data(thread_id)
            )
            values = (
                retained_data.get("bank_id"),
                retained_data.get("document_id"),
                retained_data.get("document_sha256"),
                retained_data.get("content_sha256"),
                retained_data.get("retention_until"),
            )
            if (
                not all(isinstance(value, str) and value for value in values)
                or values[1] != document_id
                or values[2] != document_sha256
            ):
                raise OperationalTimelineError(
                    "memory erasure deletion address is malformed"
                )
            erasure_data = {
                "content_sha256": values[3],
                "contract": "EvaMemoryStorageErasure/v1",
                "document_sha256": document_sha256,
                "principal_sha256": principal_sha256,
                "reason": "explicit_request",
                "requested_at": self._timestamp(now),
            }
            if source_message_id is not None:
                erasure_data["source_message_id"] = source_message_id
            leased = transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:erase:1",
                phase="memory_storage_removing",
                status="waiting_for_proof",
                event_type="MEMORY_STORAGE_ERASURE_CLAIMED",
                occurred_at=now,
                deadline=now + timedelta(seconds=self._removal_lease_seconds),
                retry_count=1,
                data=erasure_data,
            )
        return MemoryExpiryJob(
            state=leased,
            bank_id=values[0],
            document_id=values[1],
            document_sha256=values[2],
            content_sha256=values[3],
            retention_until=values[4],
            principal_sha256=principal_sha256,
            source_message_id=source_message_id,
        )

    def _explicit_erasure_bindings(
        self, thread_id: str
    ) -> tuple[str | None, str | None]:
        for event in reversed(self._timeline.events(thread_id)):
            if event.event_type != "MEMORY_STORAGE_ERASURE_CLAIMED":
                continue
            data = self._first_data(event.payload)
            allowed = {
                "content_sha256",
                "contract",
                "document_sha256",
                "principal_sha256",
                "reason",
                "requested_at",
            }
            if frozenset(data) not in {
                frozenset(allowed),
                frozenset(allowed | {"source_message_id"}),
            }:
                raise OperationalTimelineError("memory erasure event is malformed")
            if (
                data.get("contract") != "EvaMemoryStorageErasure/v1"
                or data.get("reason") != "explicit_request"
                or not isinstance(data.get("principal_sha256"), str)
            ):
                raise OperationalTimelineError("memory erasure event is malformed")
            source_message_id = data.get("source_message_id")
            if source_message_id is not None and (
                not isinstance(source_message_id, str) or not source_message_id
            ):
                raise OperationalTimelineError("memory erasure event is malformed")
            return str(data["principal_sha256"]), source_message_id
        return None, None

    def claim_expired(
        self, *, now: datetime, limit: int = 64
    ) -> tuple[MemoryExpiryJob, ...]:
        """Lease expired deletes; a crash becomes an idempotent DELETE retry."""

        now = self._require_now(now)
        if not 1 <= limit <= 256:
            raise ValueError("memory expiry limit is outside 1..256")
        claimed: list[MemoryExpiryJob] = []
        with self._engine.begin() as connection:
            self._scope(connection)
            rows = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id.startswith("memory-storage:"),
                    operational_checkpoints.c.phase.in_(
                        ("memory_storage_retained", "memory_storage_removing")
                    ),
                    operational_checkpoints.c.status.in_(
                        ("active", "waiting_for_proof")
                    ),
                    operational_checkpoints.c.deadline <= now,
                )
                .order_by(
                    operational_checkpoints.c.deadline,
                    operational_checkpoints.c.thread_id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                state = CheckpointState(
                    thread_id=str(row.thread_id),
                    checkpoint_id=str(row.checkpoint_id),
                    operation_id=(
                        None if row.operation_id is None else str(row.operation_id)
                    ),
                    phase=str(row.phase),  # type: ignore[arg-type]
                    status=str(row.status),  # type: ignore[arg-type]
                    retry_count=int(row.retry_count),
                    last_error_fingerprint=(
                        None
                        if row.last_error_fingerprint is None
                        else bytes(row.last_error_fingerprint).hex()
                    ),
                    deadline=row.deadline,
                    revision=int(row.revision),
                    updated_at=row.updated_at,
                )
                retained_row = connection.execute(
                    sa.select(operational_events.c.payload)
                    .where(
                        operational_events.c.tenant_id == self._scope_value.tenant_id,
                        operational_events.c.installation_id
                        == self._scope_value.installation_id,
                        operational_events.c.thread_id == state.thread_id,
                        operational_events.c.event_type == "MEMORY_STORAGE_RETAINED",
                    )
                    .order_by(operational_events.c.sequence.desc())
                    .limit(1)
                ).one_or_none()
                data = self._first_data(
                    None if retained_row is None else retained_row.payload
                )
                data = self._validate_retention_data(data)
                explicit_principal_sha256, source_message_id = (
                    self._explicit_erasure_bindings(state.thread_id)
                )
                admission_row = connection.execute(
                    sa.select(operational_events.c.payload).where(
                        operational_events.c.tenant_id == self._scope_value.tenant_id,
                        operational_events.c.installation_id
                        == self._scope_value.installation_id,
                        operational_events.c.thread_id == state.thread_id,
                        operational_events.c.sequence == 1,
                    )
                ).one_or_none()
                admission_data = self._first_data(
                    None if admission_row is None else admission_row.payload
                )
                principal_sha256 = admission_data.get("principal_sha256")
                if (
                    admission_data.get("contract") != "EvaMemoryStorageAdmission/v2"
                    or not isinstance(principal_sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", principal_sha256)
                    or (
                        explicit_principal_sha256 is not None
                        and explicit_principal_sha256 != principal_sha256
                    )
                ):
                    raise OperationalTimelineError(
                        "memory storage erasure principal binding is malformed"
                    )
                retention_until = data.get("retention_until")
                self._parse_timestamp(retention_until, label="retention deadline")
                retry_count = state.retry_count + 1
                if retry_count > 3:
                    transition_timeline_thread(
                        connection,
                        self._scope_value,
                        thread_id=state.thread_id,
                        expected_revision=state.revision,
                        checkpoint_id=f"{state.thread_id}:remove-needs-help",
                        phase="memory_storage_remove_needs_help",
                        status="needs_help",
                        event_type="MEMORY_STORAGE_REMOVAL_NEEDS_HELP",
                        occurred_at=now,
                        retry_count=3,
                        data={
                            "content_sha256": data.get("content_sha256"),
                            "contract": "EvaMemoryStorageRemoval/v1",
                            "document_sha256": data.get("document_sha256"),
                            "reason": "retry_budget_exhausted",
                        },
                    )
                    continue
                leased = transition_timeline_thread(
                    connection,
                    self._scope_value,
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{state.thread_id}:remove:{retry_count}",
                    phase="memory_storage_removing",
                    status="waiting_for_proof",
                    event_type="MEMORY_STORAGE_REMOVAL_CLAIMED",
                    occurred_at=now,
                    deadline=now + timedelta(seconds=self._removal_lease_seconds),
                    retry_count=retry_count,
                    data={
                        "content_sha256": data.get("content_sha256"),
                        "contract": "EvaMemoryStorageRemoval/v1",
                        "document_sha256": data.get("document_sha256"),
                        "retention_until": retention_until,
                    },
                )
                values = (
                    data.get("bank_id"),
                    data.get("document_id"),
                    data.get("document_sha256"),
                    data.get("content_sha256"),
                    retention_until,
                )
                if not all(isinstance(value, str) and value for value in values):
                    raise OperationalTimelineError(
                        "memory storage retention deletion address is malformed"
                    )
                claimed.append(
                    MemoryExpiryJob(
                        state=leased,
                        bank_id=values[0],
                        document_id=values[1],
                        document_sha256=values[2],
                        content_sha256=values[3],
                        retention_until=values[4],
                        principal_sha256=principal_sha256,
                        source_message_id=source_message_id,
                    )
                )
        return tuple(claimed)

    def mark_removed(self, job: MemoryExpiryJob, *, now: datetime) -> CheckpointState:
        now = self._require_now(now)
        try:
            return self._timeline.transition(
                thread_id=job.state.thread_id,
                expected_revision=job.state.revision,
                checkpoint_id=f"{job.state.thread_id}:removed",
                phase="memory_storage_removed",
                status="completed",
                event_type="MEMORY_STORAGE_REMOVED",
                occurred_at=now,
                data={
                    "content_sha256": job.content_sha256,
                    "contract": "EvaMemoryStorageTombstone/v1",
                    "document_sha256": job.document_sha256,
                    "removed_at": self._timestamp(now),
                },
            )
        except CheckpointConflict:
            current = self._timeline.load(job.state.thread_id)
            if current.phase == "memory_storage_removed":
                return current
            raise


__all__ = [
    "BackgroundReflectionAdmission",
    "MemoryExpiryJob",
    "MemoryStorageAdmission",
    "PostgresDailyReflectionBudget",
    "PostgresMemoryStorageBudget",
]
