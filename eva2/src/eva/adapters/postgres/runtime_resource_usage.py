"""Durable admission for installation-scoped background resource budgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
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
    """Durable per-person record/byte decision for one semantic document."""

    admitted: bool
    replayed: bool
    content_bytes: int
    person_records_used: int
    person_bytes_used: int
    retention_until: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class MemoryExpiryJob:
    """One leased Hindsight deletion whose retry is safe after a crash."""

    state: CheckpointState
    bank_id: str
    document_id: str
    document_sha256: str
    content_sha256: str
    retention_until: str


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
            or event_data.get("installation_limit")
            != expected_installation_limit
            or event_data.get("source_budget_sha256")
            != expected_source_budget_sha256
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


class PostgresMemoryStorageBudget:
    """Keep conversation-memory records and UTF-8 bytes inside owner limits.

    The append-only events contain hashes, byte counts and the provider deletion
    address, never the memory content.  A per-person advisory lock makes the
    aggregate check and reservation atomic across runtime processes.  Capacity
    is released only after Hindsight has acknowledged deletion, so an overdue
    provider cannot make the physical store silently exceed the configured cap.
    """

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
            installation_id=uuid5(
                NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
            ),
        )
        self._timeline = PostgresOperationalTimeline(engine, self._scope_value)
        self._record_limit = resource_budget.memory_records_per_person
        self._byte_limit = resource_budget.memory_bytes_per_person
        self._retention_days = resource_budget.retention_days
        self._source_budget_sha256 = resource_budget.source_budget_sha256
        self._removal_lease_seconds = removal_lease_seconds

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql("SET LOCAL ROLE eva_runtime")
        connection.execute(
            sa.text("SELECT set_config('eva.tenant_id', :value, true)"),
            {"value": str(self._scope_value.tenant_id)},
        )
        connection.execute(
            sa.text("SELECT set_config('eva.installation_id', :value, true)"),
            {"value": str(self._scope_value.installation_id)},
        )

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
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
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

    def _usage(
        self, connection: sa.Connection, *, principal_sha256: str
    ) -> tuple[int, int]:
        data = operational_events.c.payload["data"]
        row = connection.execute(
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
                data["principal_sha256"].astext == principal_sha256,
                operational_checkpoints.c.phase.notin_(
                    ("memory_storage_blocked", "memory_storage_removed")
                ),
            )
        ).one()
        return int(row[0]), int(row[1])

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
                        f"{self._scope_value.installation_id}:memory-storage:"
                        f"{principal_sha256}"
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
                    operational_checkpoints.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                    operational_events.c.sequence == 1,
                )
            ).one_or_none()
            if existing is not None:
                data = self._first_data(existing.payload)
                if (
                    data.get("contract") != "EvaMemoryStorageAdmission/v1"
                    or data.get("content_bytes") != content_bytes
                    or data.get("content_sha256") != content_sha256
                    or data.get("document_sha256") != document_sha256
                    or data.get("principal_sha256") != principal_sha256
                    or data.get("record_limit") != self._record_limit
                    or data.get("byte_limit") != self._byte_limit
                    or data.get("source_budget_sha256")
                    != self._source_budget_sha256
                ):
                    raise OperationalTimelineError(
                        "memory storage replay differs from its reservation"
                    )
                admitted = data.get("admitted") is True
                if data.get("admitted") not in {True, False}:
                    raise OperationalTimelineError(
                        "memory storage replay decision is malformed"
                    )
                records_used, bytes_used = self._usage(
                    connection, principal_sha256=principal_sha256
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
                    }
                )
                if reason not in allowed_reasons:
                    raise OperationalTimelineError(
                        "memory storage replay reason is malformed"
                    )
                return MemoryStorageAdmission(
                    admitted=admitted,
                    replayed=True,
                    content_bytes=content_bytes,
                    person_records_used=records_used,
                    person_bytes_used=bytes_used,
                    retention_until=(stored_retention if admitted else None),  # type: ignore[arg-type]
                    reason=reason,  # type: ignore[arg-type]
                )

            records_used, bytes_used = self._usage(
                connection, principal_sha256=principal_sha256
            )
            if records_used >= self._record_limit:
                admitted = False
                reason = "person_record_limit_reached"
            elif bytes_used + content_bytes > self._byte_limit:
                admitted = False
                reason = "person_byte_limit_reached"
            else:
                admitted = True
                reason = "reserved"
                records_used += 1
                bytes_used += content_bytes
            data = {
                "admitted": admitted,
                "byte_limit": self._byte_limit,
                "content_bytes": content_bytes,
                "content_sha256": content_sha256,
                "contract": "EvaMemoryStorageAdmission/v1",
                "document_sha256": document_sha256,
                "principal_sha256": principal_sha256,
                "reason": reason,
                "record_limit": self._record_limit,
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
                    "MEMORY_STORAGE_RESERVED"
                    if admitted
                    else "MEMORY_STORAGE_BLOCKED"
                ),
                phase=(
                    "memory_storage_reserved"
                    if admitted
                    else "memory_storage_blocked"
                ),
                status="active" if admitted else "completed",
                deadline=retention_until if admitted else None,
            )
        return MemoryStorageAdmission(
            admitted=admitted,
            replayed=False,
            content_bytes=content_bytes,
            person_records_used=records_used,
            person_bytes_used=bytes_used,
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
        if set(data) != {
            "bank_id",
            "content_sha256",
            "contract",
            "document_id",
            "document_sha256",
            "retention_until",
        } or data.get("contract") != "EvaMemoryStorageRetention/v1":
            raise OperationalTimelineError(
                "memory storage retention event is malformed"
            )
        return data

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
                    operational_checkpoints.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id.startswith(
                        "memory-storage:"
                    ),
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
                        operational_events.c.tenant_id
                        == self._scope_value.tenant_id,
                        operational_events.c.installation_id
                        == self._scope_value.installation_id,
                        operational_events.c.thread_id == state.thread_id,
                        operational_events.c.event_type
                        == "MEMORY_STORAGE_RETAINED",
                    )
                    .order_by(operational_events.c.sequence.desc())
                    .limit(1)
                ).one_or_none()
                data = self._first_data(
                    None if retained_row is None else retained_row.payload
                )
                data = self._validate_retention_data(data)
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
                    )
                )
        return tuple(claimed)

    def mark_removed(
        self, job: MemoryExpiryJob, *, now: datetime
    ) -> CheckpointState:
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
