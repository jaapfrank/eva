"""Append-only PostgreSQL timeline and rebuildable checkpoint projection."""

from __future__ import annotations
from eva.contracts.timestamps import format_required_utc_timestamp as _canonical_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from typing import Any, Iterator, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from eva.application.episode_repository import TransactionRequired
from eva.application.conversation import ConversationModelUsage
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256


living_metadata = sa.MetaData()

operational_events = sa.Table(
    "operational_events",
    living_metadata,
    sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("thread_id", sa.Text(collation="C"), nullable=False),
    sa.Column("sequence", sa.BigInteger(), nullable=False),
    sa.Column("checkpoint_id", sa.Text(collation="C"), nullable=False),
    sa.Column("operation_id", sa.Text(collation="C"), nullable=True),
    sa.Column("event_type", sa.Text(collation="C"), nullable=False),
    sa.Column("payload", postgresql.JSONB(none_as_null=True), nullable=False),
    sa.Column("payload_sha256", postgresql.BYTEA(), nullable=False),
    sa.Column(
        "occurred_at", postgresql.TIMESTAMP(timezone=True, precision=0), nullable=False
    ),
    sa.PrimaryKeyConstraint(
        "tenant_id",
        "installation_id",
        "thread_id",
        "sequence",
        name="pk_operational_events",
    ),
    sa.UniqueConstraint(
        "tenant_id",
        "installation_id",
        "event_id",
        name="uq_operational_events_event_id",
    ),
    sa.CheckConstraint("sequence >= 1", name="ck_operational_events_positive_sequence"),
    sa.CheckConstraint(
        "octet_length(payload_sha256) = 32",
        name="ck_operational_events_payload_hash",
    ),
    sa.CheckConstraint(
        "length(thread_id) BETWEEN 1 AND 240 "
        "AND length(checkpoint_id) BETWEEN 1 AND 240",
        name="ck_operational_events_identifiers",
    ),
    schema="eva_core",
)

operational_checkpoints = sa.Table(
    "operational_checkpoints",
    living_metadata,
    sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("thread_id", sa.Text(collation="C"), nullable=False),
    sa.Column("checkpoint_id", sa.Text(collation="C"), nullable=False),
    sa.Column("operation_id", sa.Text(collation="C"), nullable=True),
    sa.Column("phase", sa.Text(collation="C"), nullable=False),
    sa.Column("status", sa.Text(collation="C"), nullable=False),
    sa.Column("retry_count", sa.SmallInteger(), nullable=False),
    sa.Column("last_error_fingerprint", postgresql.BYTEA(), nullable=True),
    sa.Column(
        "deadline", postgresql.TIMESTAMP(timezone=True, precision=0), nullable=True
    ),
    sa.Column("revision", sa.BigInteger(), nullable=False),
    sa.Column(
        "updated_at", postgresql.TIMESTAMP(timezone=True, precision=0), nullable=False
    ),
    sa.PrimaryKeyConstraint(
        "tenant_id",
        "installation_id",
        "thread_id",
        name="pk_operational_checkpoints",
    ),
    sa.UniqueConstraint(
        "tenant_id",
        "installation_id",
        "checkpoint_id",
        name="uq_operational_checkpoints_checkpoint_id",
    ),
    sa.CheckConstraint(
        "status IN ('active','waiting_for_proof','completed','failed','needs_help')",
        name="ck_operational_checkpoints_status",
    ),
    sa.CheckConstraint(
        "retry_count BETWEEN 0 AND 3 AND revision >= 1",
        name="ck_operational_checkpoints_counters",
    ),
    sa.CheckConstraint(
        "last_error_fingerprint IS NULL OR octet_length(last_error_fingerprint) = 32",
        name="ck_operational_checkpoints_error_hash",
    ),
    sa.CheckConstraint(
        "length(thread_id) BETWEEN 1 AND 240 "
        "AND length(checkpoint_id) BETWEEN 1 AND 240",
        name="ck_operational_checkpoints_identifiers",
    ),
    schema="eva_core",
)

sa.Index(
    "ix_operational_events__operation",
    operational_events.c.tenant_id,
    operational_events.c.installation_id,
    operational_events.c.operation_id,
)
sa.Index(
    "ix_operational_events__type_time",
    operational_events.c.tenant_id,
    operational_events.c.installation_id,
    operational_events.c.event_type,
    operational_events.c.occurred_at,
)
sa.Index(
    "ix_operational_checkpoints__status",
    operational_checkpoints.c.tenant_id,
    operational_checkpoints.c.installation_id,
    operational_checkpoints.c.status,
    operational_checkpoints.c.updated_at,
)


CheckpointStatus = Literal[
    "active", "waiting_for_proof", "completed", "failed", "needs_help"
]


class OperationalTimelineError(RuntimeError):
    pass


class CheckpointConflict(OperationalTimelineError):
    pass


class TimelineNotFound(OperationalTimelineError):
    pass


@dataclass(frozen=True, slots=True)
class TimelineScope:
    tenant_id: UUID
    installation_id: UUID


def external_timeline_scope(
    tenant_id: str, installation_id: str, missing_error: str
) -> TimelineScope:
    if not tenant_id.strip() or not installation_id.strip():
        raise ValueError(missing_error)
    return TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
        installation_id=uuid5(
            NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
        ),
    )


@dataclass(frozen=True, slots=True)
class CheckpointState:
    thread_id: str
    checkpoint_id: str
    operation_id: str | None
    phase: str
    status: CheckpointStatus
    retry_count: int
    last_error_fingerprint: str | None
    deadline: datetime | None
    revision: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    event_id: UUID
    thread_id: str
    sequence: int
    checkpoint_id: str
    operation_id: str | None
    event_type: str
    payload: dict[str, JsonValue]
    payload_sha256: str
    occurred_at: datetime


def _checkpoint_payload(
    state: CheckpointState, data: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    validate_json_value(data)
    return {
        "checkpoint": {
            "checkpoint_id": state.checkpoint_id,
            "deadline": (
                None if state.deadline is None else _canonical_timestamp(state.deadline)
            ),
            "last_error_fingerprint": state.last_error_fingerprint,
            "operation_id": state.operation_id,
            "phase": state.phase,
            "retry_count": state.retry_count,
            "revision": state.revision,
            "status": state.status,
            "updated_at": _canonical_timestamp(state.updated_at),
        },
        "data": data,
    }


class PostgresOperationalTimeline:
    """One-transaction event+projection transitions; external calls stay outside."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("operational timeline requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        connection.execute(
            sa.text("SELECT set_config('eva.tenant_id', :value, true)"),
            {"value": str(self._scope_value.tenant_id)},
        )
        connection.execute(
            sa.text("SELECT set_config('eva.installation_id', :value, true)"),
            {"value": str(self._scope_value.installation_id)},
        )

    def create_thread(
        self,
        *,
        thread_id: str,
        checkpoint_id: str,
        occurred_at: datetime,
        data: dict[str, JsonValue],
        event_type: str = "INGRESS_COMMITTED",
        phase: str = "admitted",
        status: CheckpointStatus = "active",
        operation_id: str | None = None,
        deadline: datetime | None = None,
    ) -> CheckpointState:
        with self._engine.begin() as connection:
            self._scope(connection)
            try:
                return insert_initial_timeline_thread(
                    connection,
                    self._scope_value,
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    occurred_at=occurred_at,
                    data=data,
                    event_type=event_type,
                    phase=phase,
                    status=status,
                    operation_id=operation_id,
                    deadline=deadline,
                )
            except sa.exc.IntegrityError as error:
                raise CheckpointConflict("thread already exists") from error

    def transition(
        self,
        *,
        thread_id: str,
        expected_revision: int,
        checkpoint_id: str,
        phase: str,
        status: CheckpointStatus,
        event_type: str,
        occurred_at: datetime,
        data: dict[str, JsonValue],
        operation_id: str | None = None,
        deadline: datetime | None = None,
        retry_count: int | None = None,
        last_error_fingerprint: str | None = None,
        require_unexpired: bool = False,
        commit_before: datetime | None = None,
    ) -> CheckpointState:
        with self._engine.begin() as connection:
            self._scope(connection)
            return transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=thread_id,
                expected_revision=expected_revision,
                checkpoint_id=checkpoint_id,
                phase=phase,
                status=status,
                event_type=event_type,
                occurred_at=occurred_at,
                data=data,
                operation_id=operation_id,
                deadline=deadline,
                retry_count=retry_count,
                last_error_fingerprint=last_error_fingerprint,
                require_unexpired=require_unexpired,
                commit_before=commit_before,
            )

    def record_recovery_failure(
        self,
        *,
        thread_id: str,
        expected_revision: int,
        checkpoint_id: str,
        operation_id: str | None,
        error_text: str,
        occurred_at: datetime,
        model_usage: ConversationModelUsage | None = None,
    ) -> CheckpointState:
        data: dict[str, JsonValue] = {}
        if model_usage is not None:
            if not isinstance(model_usage, ConversationModelUsage):
                raise ValueError("model failure usage is invalid")
            data["model_usage"] = model_usage.to_wire()
        fingerprint = hashlib.sha256(error_text.encode("utf-8")).hexdigest()
        current = self.load(thread_id)
        if current.revision != expected_revision:
            raise CheckpointConflict("checkpoint revision changed")
        retry_count = (
            min(current.retry_count + 1, 3)
            if current.last_error_fingerprint == fingerprint
            else 1
        )
        needs_help = retry_count >= 3
        return self.transition(
            thread_id=thread_id,
            expected_revision=expected_revision,
            checkpoint_id=checkpoint_id,
            phase=current.phase,
            status="needs_help" if needs_help else "failed",
            event_type=("RECOVERY_NEEDS_HELP" if needs_help else "RECOVERY_FAILED"),
            occurred_at=occurred_at,
            data={"error_fingerprint": fingerprint, "retry_count": retry_count, **data},
            operation_id=operation_id,
            retry_count=retry_count,
            last_error_fingerprint=fingerprint,
        )

    def load(self, thread_id: str) -> CheckpointState:
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                last_payload = (
                    sa.select(operational_events.c.payload)
                    .where(
                        operational_events.c.tenant_id == self._scope_value.tenant_id,
                        operational_events.c.installation_id == self._scope_value.installation_id,
                        operational_events.c.thread_id == thread_id,
                    )
                    .order_by(operational_events.c.sequence.desc())
                    .limit(1)
                    .scalar_subquery()
                )
                row = connection.execute(
                    # One statement gives checkpoint and event the same MVCC
                    # snapshot. Two READ COMMITTED reads can straddle a valid
                    # concurrent transition and falsely report corruption.
                    sa.select(operational_checkpoints, last_payload.label("timeline_payload")).where(
                        operational_checkpoints.c.tenant_id
                        == self._scope_value.tenant_id,
                        operational_checkpoints.c.installation_id
                        == self._scope_value.installation_id,
                        operational_checkpoints.c.thread_id == thread_id,
                    )
                ).one_or_none()
                if row is None:
                    raise TimelineNotFound("thread does not exist")
                state = _state_from_row(row)
                last_payload = row.timeline_payload
                if (
                    not isinstance(last_payload, dict)
                    or last_payload.get("checkpoint")
                    != _checkpoint_payload(state, {})["checkpoint"]
                ):
                    raise OperationalTimelineError(
                        "checkpoint projection differs from append-only timeline"
                    )
                return state
            finally:
                transaction.rollback()

    def load_with_events(
        self, thread_id: str,
    ) -> tuple[CheckpointState, tuple[TimelineEvent, ...]]:
        """Read a checked projection and complete history from one statement snapshot.

        Source authorization needs both views. Joining them avoids a second
        scoped transaction and cannot straddle a concurrent append or erasure.
        This is a fresh read, not a cache or execution authorization.
        """
        checkpoint = operational_checkpoints.c
        history = operational_events.c
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                rows = connection.execute(sa.select(
                    operational_checkpoints,
                    *(column.label("history_" + column.name) for column in history),
                ).select_from(operational_checkpoints.outerjoin(
                    operational_events,
                    sa.and_(checkpoint.tenant_id == history.tenant_id,
                            checkpoint.installation_id == history.installation_id,
                            checkpoint.thread_id == history.thread_id),
                )).where(
                    checkpoint.tenant_id == self._scope_value.tenant_id,
                    checkpoint.installation_id == self._scope_value.installation_id,
                    checkpoint.thread_id == thread_id,
                ).order_by(history.sequence)).all()
                if not rows:
                    raise TimelineNotFound("thread does not exist")
                state = _state_from_row(rows[0])
                events = tuple(TimelineEvent(
                    event_id=row.history_event_id,
                    thread_id=row.history_thread_id,
                    sequence=row.history_sequence,
                    checkpoint_id=row.history_checkpoint_id,
                    operation_id=row.history_operation_id,
                    event_type=row.history_event_type,
                    payload=row.history_payload,
                    payload_sha256=bytes(row.history_payload_sha256).hex(),
                    occurred_at=row.history_occurred_at,
                ) for row in rows if row.history_event_id is not None)
                for event in events:
                    if canonical_sha256(event.payload) != event.payload_sha256:
                        raise OperationalTimelineError(
                            "append-only event payload hash differs from its payload"
                        )
                if (not events or len(events) != state.revision
                    or any(event.sequence != index for index, event in enumerate(events, 1))
                    or _state_from_event(events[-1]) != state):
                    raise OperationalTimelineError(
                        "checkpoint projection differs from append-only timeline"
                    )
                if any(later.occurred_at < earlier.occurred_at
                       for earlier, later in zip(events, events[1:])):
                    raise OperationalTimelineError("timeline time moved backwards")
                return state, events
            finally:
                transaction.rollback()

    def events(self, thread_id: str) -> tuple[TimelineEvent, ...]:
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                rows = connection.execute(
                    sa.select(operational_events)
                    .where(
                        operational_events.c.tenant_id == self._scope_value.tenant_id,
                        operational_events.c.installation_id
                        == self._scope_value.installation_id,
                        operational_events.c.thread_id == thread_id,
                    )
                    .order_by(operational_events.c.sequence)
                ).all()
                events = tuple(_event_from_row(row) for row in rows)
                for event in events:
                    if canonical_sha256(event.payload) != event.payload_sha256:
                        raise OperationalTimelineError(
                            "append-only event payload hash differs from its payload"
                        )
                return events
            finally:
                transaction.rollback()

    def iter_threads(
        self, *, thread_prefix: str, page_size: int = 256,
    ) -> Iterator[tuple[CheckpointState, tuple[TimelineEvent, ...]]]:
        """Read complete scoped history in bounded pages from one stable snapshot.

        Keyset pagination and one event query per page avoid per-thread round trips.
        A concurrent append cannot mix a newer projection with older events.
        """
        _require_identifier(thread_prefix, "thread_prefix")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 256:
            raise ValueError("timeline page size must contain 1..256 threads")
        checkpoints = operational_checkpoints.c
        event_columns = operational_events.c
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                self._scope(connection)
                after: str | None = None
                while True:
                    statement = sa.select(operational_checkpoints).where(
                        checkpoints.tenant_id == self._scope_value.tenant_id,
                        checkpoints.installation_id == self._scope_value.installation_id,
                        checkpoints.thread_id.startswith(thread_prefix),
                    )
                    if after is not None:
                        statement = statement.where(checkpoints.thread_id > after)
                    rows = connection.execute(
                        statement.order_by(checkpoints.thread_id).limit(page_size)
                    ).all()
                    if not rows:
                        return
                    states = tuple(_state_from_row(row) for row in rows)
                    by_thread: dict[str, list[TimelineEvent]] = {
                        state.thread_id: [] for state in states
                    }
                    event_rows = connection.execute(
                        sa.select(operational_events).where(
                            event_columns.tenant_id == self._scope_value.tenant_id,
                            event_columns.installation_id == self._scope_value.installation_id,
                            event_columns.thread_id.in_(tuple(by_thread)),
                        ).order_by(event_columns.thread_id, event_columns.sequence)
                    )
                    for row in event_rows:
                        event = _event_from_row(row)
                        if canonical_sha256(event.payload) != event.payload_sha256:
                            raise OperationalTimelineError(
                                "append-only event payload hash differs from its payload"
                            )
                        by_thread[event.thread_id].append(event)
                    for state in states:
                        events = tuple(by_thread[state.thread_id])
                        if (not events or len(events) != state.revision
                            or any(event.sequence != index for index, event in enumerate(events, 1))
                            or _state_from_event(events[-1]) != state):
                            raise OperationalTimelineError(
                                "checkpoint projection differs from append-only timeline"
                            )
                        if any(later.occurred_at < earlier.occurred_at
                               for earlier, later in zip(events, events[1:])):
                            raise OperationalTimelineError("timeline time moved backwards")
                        yield state, events
                    if len(states) < page_size:
                        return
                    after = states[-1].thread_id
            finally:
                transaction.rollback()

    def list_checkpoints(
        self,
        *,
        thread_prefix: str,
        phases: tuple[str, ...] = (),
        statuses: tuple[CheckpointStatus, ...] = (),
        updated_before: datetime | None = None,
        last_error_fingerprint: str | None = None,
        limit: int = 32,
        oldest_first: bool = False,
    ) -> tuple[CheckpointState, ...]:
        """List scoped work cursors for a background recovery worker."""

        if not thread_prefix or not 1 <= limit <= 256:
            raise ValueError("checkpoint listing parameters are invalid")
        if updated_before is not None:
            _require_utc_second(updated_before, "updated_before")
        if last_error_fingerprint is not None and (
            not isinstance(last_error_fingerprint, str)
            or len(last_error_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in last_error_fingerprint)
        ):
            raise ValueError("checkpoint error fingerprint is invalid")
        statement = sa.select(operational_checkpoints).where(
            operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
            operational_checkpoints.c.installation_id
            == self._scope_value.installation_id,
            operational_checkpoints.c.thread_id.startswith(thread_prefix),
        )
        if phases:
            statement = statement.where(operational_checkpoints.c.phase.in_(phases))
        if statuses:
            statement = statement.where(operational_checkpoints.c.status.in_(statuses))
        if last_error_fingerprint is not None:
            statement = statement.where(
                operational_checkpoints.c.last_error_fingerprint
                == bytes.fromhex(last_error_fingerprint)
            )
        if updated_before is not None:
            statement = statement.where(
                operational_checkpoints.c.updated_at <= updated_before
            )
        statement = statement.order_by(
            (operational_checkpoints.c.updated_at.asc() if oldest_first
             else operational_checkpoints.c.updated_at.desc()),
            operational_checkpoints.c.thread_id,
        ).limit(limit)
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                return tuple(
                    _state_from_row(row) for row in connection.execute(statement).all()
                )
            finally:
                transaction.rollback()

    def rebuild_projection(self, thread_id: str) -> CheckpointState:
        """Recreate the disposable cursor from the authoritative last event."""

        _require_identifier(thread_id, "thread_id")
        with self._engine.begin() as connection:
            self._scope(connection)
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"{self._scope_value.tenant_id}:"
                        f"{self._scope_value.installation_id}:{thread_id}"
                    )
                },
            )
            event_row = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id == thread_id,
                )
                .order_by(operational_events.c.sequence.desc())
                .limit(1)
            ).one_or_none()
            if event_row is None:
                raise OperationalTimelineError("thread does not exist")
            event = _event_from_row(event_row)
            if canonical_sha256(event.payload) != event.payload_sha256:
                raise OperationalTimelineError(
                    "append-only event payload hash differs from its payload"
                )
            state = _state_from_event(event)
            values = {
                "checkpoint_id": state.checkpoint_id,
                "operation_id": state.operation_id,
                "phase": state.phase,
                "status": state.status,
                "retry_count": state.retry_count,
                "last_error_fingerprint": (
                    None
                    if state.last_error_fingerprint is None
                    else bytes.fromhex(state.last_error_fingerprint)
                ),
                "deadline": state.deadline,
                "revision": state.revision,
                "updated_at": state.updated_at,
            }
            existing = connection.execute(
                sa.select(operational_checkpoints.c.thread_id)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                )
                .with_for_update()
            ).one_or_none()
            if existing is None:
                connection.execute(
                    sa.insert(operational_checkpoints).values(
                        tenant_id=self._scope_value.tenant_id,
                        installation_id=self._scope_value.installation_id,
                        thread_id=thread_id,
                        **values,
                    )
                )
            else:
                connection.execute(
                    sa.update(operational_checkpoints)
                    .where(
                        operational_checkpoints.c.tenant_id
                        == self._scope_value.tenant_id,
                        operational_checkpoints.c.installation_id
                        == self._scope_value.installation_id,
                        operational_checkpoints.c.thread_id == thread_id,
                    )
                    .values(**values)
                )
        return state


def insert_initial_timeline_thread(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    thread_id: str,
    checkpoint_id: str,
    occurred_at: datetime,
    data: dict[str, JsonValue],
    event_type: str = "INGRESS_COMMITTED",
    phase: str = "admitted",
    status: CheckpointStatus = "active",
    operation_id: str | None = None,
    deadline: datetime | None = None,
) -> CheckpointState:
    """Insert one event and checkpoint inside the caller's root transaction."""

    if not connection.in_transaction() or connection.in_nested_transaction():
        raise TransactionRequired()
    _require_identifier(thread_id, "thread_id")
    _require_identifier(checkpoint_id, "checkpoint_id")
    _require_identifier(event_type, "event_type")
    _require_identifier(phase, "phase")
    if operation_id is not None:
        _require_identifier(operation_id, "operation_id")
    _require_utc_second(occurred_at, "occurred_at")
    if deadline is not None:
        _require_utc_second(deadline, "deadline")
        if deadline < occurred_at:
            raise ValueError("checkpoint deadline cannot precede admission")
    state = CheckpointState(
        thread_id=thread_id,
        checkpoint_id=checkpoint_id,
        operation_id=operation_id,
        phase=phase,
        status=status,
        retry_count=0,
        last_error_fingerprint=None,
        deadline=deadline,
        revision=1,
        updated_at=occurred_at,
    )
    payload = _checkpoint_payload(state, data)
    connection.execute(
        sa.insert(operational_checkpoints).values(
            tenant_id=scope.tenant_id,
            installation_id=scope.installation_id,
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            operation_id=operation_id,
            phase=state.phase,
            status=state.status,
            retry_count=0,
            last_error_fingerprint=None,
            deadline=deadline,
            revision=1,
            updated_at=occurred_at,
        )
    )
    _insert_event_in_transaction(
        connection,
        scope,
        state=state,
        sequence=1,
        event_type=event_type,
        payload=payload,
        occurred_at=occurred_at,
    )
    return state


def transition_timeline_thread(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    thread_id: str,
    expected_revision: int,
    checkpoint_id: str,
    phase: str,
    status: CheckpointStatus,
    event_type: str,
    occurred_at: datetime,
    data: dict[str, JsonValue],
    operation_id: str | None = None,
    deadline: datetime | None = None,
    retry_count: int | None = None,
    last_error_fingerprint: str | None = None,
    require_unexpired: bool = False,
    commit_before: datetime | None = None,
) -> CheckpointState:
    """Append one transition inside the caller's root transaction."""

    if not connection.in_transaction() or connection.in_nested_transaction():
        raise TransactionRequired()
    _require_identifier(thread_id, "thread_id")
    _require_identifier(checkpoint_id, "checkpoint_id")
    _require_identifier(phase, "phase")
    _require_identifier(event_type, "event_type")
    if operation_id is not None:
        _require_identifier(operation_id, "operation_id")
    _require_utc_second(occurred_at, "occurred_at")
    if commit_before is not None:
        _require_utc_second(commit_before, "commit_before")
    if deadline is not None:
        _require_utc_second(deadline, "deadline")
    if last_error_fingerprint is not None:
        _require_sha256(last_error_fingerprint)

    row = connection.execute(
        sa.select(operational_checkpoints)
        .where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_checkpoints.c.thread_id == thread_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise TimelineNotFound("thread does not exist")
    if int(row.revision) != expected_revision:
        raise CheckpointConflict("checkpoint revision changed")
    if commit_before is not None:
        # Independent event deadline, checked after lock acquisition. This does
        # not renew or reuse the operation's own execution deadline.
        current_time = connection.scalar(sa.select(sa.func.clock_timestamp()))
        if occurred_at >= commit_before or current_time >= commit_before:
            raise CheckpointConflict("transition reporting deadline expired")
    if require_unexpired:
        # clock_timestamp(), unlike now(), advances while waiting for the lock.
        current_time = connection.scalar(sa.select(sa.func.clock_timestamp()))
        if row.deadline is None or current_time >= row.deadline:
            raise CheckpointConflict("checkpoint deadline expired")

    next_retry = int(row.retry_count) if retry_count is None else retry_count
    if not 0 <= next_retry <= 3:
        raise ValueError("checkpoint retry count is outside 0..3")
    state = CheckpointState(
        thread_id=thread_id,
        checkpoint_id=checkpoint_id,
        operation_id=operation_id,
        phase=phase,
        status=status,
        retry_count=next_retry,
        last_error_fingerprint=last_error_fingerprint,
        deadline=deadline,
        revision=expected_revision + 1,
        updated_at=occurred_at,
    )
    payload = _checkpoint_payload(state, data)
    sequence = (
        int(
            connection.scalar(
                sa.select(sa.func.max(operational_events.c.sequence)).where(
                    operational_events.c.tenant_id == scope.tenant_id,
                    operational_events.c.installation_id == scope.installation_id,
                    operational_events.c.thread_id == thread_id,
                )
            )
        )
        + 1
    )
    _insert_event_in_transaction(
        connection,
        scope,
        state=state,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
        occurred_at=occurred_at,
    )
    result = connection.execute(
        sa.update(operational_checkpoints)
        .where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_checkpoints.c.thread_id == thread_id,
            operational_checkpoints.c.revision == expected_revision,
        )
        .values(
            checkpoint_id=checkpoint_id,
            operation_id=operation_id,
            phase=phase,
            status=status,
            retry_count=next_retry,
            last_error_fingerprint=(
                None
                if last_error_fingerprint is None
                else bytes.fromhex(last_error_fingerprint)
            ),
            deadline=deadline,
            revision=expected_revision + 1,
            updated_at=occurred_at,
        )
    )
    if result.rowcount != 1:
        raise CheckpointConflict("checkpoint revision changed")
    return state


def _insert_event_in_transaction(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    state: CheckpointState,
    sequence: int,
    event_type: str,
    payload: dict[str, JsonValue],
    occurred_at: datetime,
) -> None:
    connection.execute(
        sa.insert(operational_events).values(
            tenant_id=scope.tenant_id,
            installation_id=scope.installation_id,
            event_id=uuid4(),
            thread_id=state.thread_id,
            sequence=sequence,
            checkpoint_id=state.checkpoint_id,
            operation_id=state.operation_id,
            event_type=event_type,
            payload=payload,
            payload_sha256=bytes.fromhex(canonical_sha256(payload)),
            occurred_at=occurred_at,
        )
    )


def _require_sha256(value: str) -> None:
    if len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise ValueError("error fingerprint must be lowercase SHA-256")


def _require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 240:
        raise ValueError(f"{field} must contain 1..240 characters")
    validate_json_value(value)


def _state_from_row(row: Any) -> CheckpointState:
    raw_hash = row.last_error_fingerprint
    return CheckpointState(
        thread_id=str(row.thread_id),
        checkpoint_id=str(row.checkpoint_id),
        operation_id=None if row.operation_id is None else str(row.operation_id),
        phase=str(row.phase),
        status=str(row.status),  # type: ignore[arg-type]
        retry_count=int(row.retry_count),
        last_error_fingerprint=(None if raw_hash is None else bytes(raw_hash).hex()),
        deadline=row.deadline,
        revision=int(row.revision),
        updated_at=row.updated_at,
    )


def _event_from_row(row: Any) -> TimelineEvent:
    return TimelineEvent(
        event_id=row.event_id,
        thread_id=str(row.thread_id),
        sequence=int(row.sequence),
        checkpoint_id=str(row.checkpoint_id),
        operation_id=None if row.operation_id is None else str(row.operation_id),
        event_type=str(row.event_type),
        payload=row.payload,
        payload_sha256=bytes(row.payload_sha256).hex(),
        occurred_at=row.occurred_at,
    )


def _state_from_event(event: TimelineEvent) -> CheckpointState:
    raw = event.payload.get("checkpoint")
    if not isinstance(raw, dict):
        raise OperationalTimelineError("event lacks a checkpoint snapshot")
    required = {
        "checkpoint_id",
        "deadline",
        "last_error_fingerprint",
        "operation_id",
        "phase",
        "retry_count",
        "revision",
        "status",
        "updated_at",
    }
    if set(raw) != required:
        raise OperationalTimelineError("event checkpoint snapshot has wrong fields")
    try:
        deadline_raw = raw["deadline"]
        updated_raw = raw["updated_at"]
        state = CheckpointState(
            thread_id=event.thread_id,
            checkpoint_id=str(raw["checkpoint_id"]),
            operation_id=(
                None if raw["operation_id"] is None else str(raw["operation_id"])
            ),
            phase=str(raw["phase"]),
            status=str(raw["status"]),  # type: ignore[arg-type]
            retry_count=int(raw["retry_count"]),
            last_error_fingerprint=(
                None
                if raw["last_error_fingerprint"] is None
                else str(raw["last_error_fingerprint"])
            ),
            deadline=(
                None
                if deadline_raw is None
                else datetime.strptime(str(deadline_raw), "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=UTC
                )
            ),
            revision=int(raw["revision"]),
            updated_at=datetime.strptime(
                str(updated_raw), "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC),
        )
    except (TypeError, ValueError) as error:
        raise OperationalTimelineError(
            "event checkpoint snapshot is malformed"
        ) from error
    if state.checkpoint_id != event.checkpoint_id:
        raise OperationalTimelineError("event/checkpoint ID binding differs")
    if state.operation_id != event.operation_id:
        raise OperationalTimelineError("event/operation ID binding differs")
    if state.revision != event.sequence:
        raise OperationalTimelineError("event revision/sequence binding differs")
    return state


__all__ = [
    "CheckpointConflict",
    "CheckpointState",
    "OperationalTimelineError",
    "PostgresOperationalTimeline",
    "TimelineEvent",
    "TimelineScope",
    "insert_initial_timeline_thread",
    "transition_timeline_thread",
    "living_metadata",
    "operational_checkpoints",
    "operational_events",
]
