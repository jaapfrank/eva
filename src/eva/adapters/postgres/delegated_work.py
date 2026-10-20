"""Durable PostgreSQL handoff and at-most-once fence for bounded child work."""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp

from contextlib import nullcontext
import re
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.current_catalog import delegated_work_runs, metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope, PostgresOperationalTimeline, insert_initial_timeline_thread, operational_events, operational_checkpoints,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import (
    DelegatedWorkResult,
    DelegatedWorkSpec, DelegatedResearchSpec, DelegatedHouseholdResearchSpec, load_delegated_work_spec, load_delegated_work_result,
    DelegatedWorkUsage,
)
from eva.contracts.delegated_digital_work import DelegatedHouseholdDigitalWorkSpec, DelegatedInitiativeDigitalWorkSpec
from eva.contracts.workboard import WorkBoard
from eva.application.delegated_work_delivery import PrivateWorkDestination
from eva.application.initiative_execution import unstarted_profile_refresh
from eva.adapters.postgres.delegated_work_delivery import record_admission_destination

principal_bindings = metadata.tables["eva_core.principal_bindings"]
principals = metadata.tables["eva_core.principals"]

_HOUSEHOLD_WORK_TYPES = (DelegatedHouseholdResearchSpec, DelegatedHouseholdDigitalWorkSpec)

_TERMINAL_STATES = frozenset(
    {"succeeded", "verified", "failed", "ambiguous", "needs_help", "stopped"}
)


class DelegatedWorkStoreError(RuntimeError):
    """The requested durable transition is absent, stale or corrupt."""


class DelegatedWorkConflict(DelegatedWorkStoreError):
    """Existing authority differs from the requested immutable bytes."""


class DelegatedWorkNotResumable(DelegatedWorkConflict):
    """An authorized owner requested a successor of active or successful work."""

    def __init__(self, work: StoredDelegatedWork) -> None:
        super().__init__("previous delegated attempt is still active or already succeeded")
        self.work = work


class DelegatedWorkNotFound(DelegatedWorkStoreError):
    """No work exists in the caller's fixed scope."""


@dataclass(frozen=True, slots=True)
class DelegatedWorkReleaseSelector:
    """Exact worker release allowed to lease durable delegated work."""

    worker_profile_release_sha256: str
    model_profile_release_sha256: str
    worker_program_sha256: str
    engine_release_sha256: str
    output_schema_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.worker_profile_release_sha256,
            self.model_profile_release_sha256,
            self.worker_program_sha256,
            self.engine_release_sha256,
            self.output_schema_sha256,
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("delegated release selector hash is invalid")

    @classmethod
    def from_spec(cls, spec: DelegatedWorkSpec) -> DelegatedWorkReleaseSelector:
        return cls(
            worker_profile_release_sha256=spec.worker_profile_release_sha256,
            model_profile_release_sha256=spec.model_profile_release_sha256,
            worker_program_sha256=spec.worker_program_sha256,
            engine_release_sha256=spec.engine_release_sha256,
            output_schema_sha256=spec.output_schema_sha256,
        )


@dataclass(frozen=True, slots=True)
class StoredDelegatedWork:
    spec: DelegatedWorkSpec
    storage_episode_id: UUID
    title: str
    summary: str
    worker_role: str
    state: str
    claim_count: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    call_id: str | None
    result: DelegatedWorkResult | None
    usage: DelegatedWorkUsage | None
    error_code: str | None
    error_fingerprint: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    call_started_at: datetime | None
    stop_requested_at: datetime | None
    completed_at: datetime | None
    retention_policy_id: str
    retention_until: datetime
    failure_report: str | None = None


@dataclass(frozen=True, slots=True)
class DelegatedWorkAdmission:
    disposition: Literal["inserted", "already_present"]
    work: StoredDelegatedWork


@dataclass(frozen=True, slots=True)
class DelegatedWorkClaim:
    work_id: str
    attention_id: UUID
    lease_owner: str
    work_revision: int
    spec: DelegatedWorkSpec


@dataclass(frozen=True, slots=True)
class DelegatedWorkCall:
    work_id: str
    attention_id: UUID
    call_id: str
    spec: DelegatedWorkSpec


class PostgresDelegatedWorkStore(PostgresScopedRole):
    """Keep handoff, lease, call fence and result in one scoped authority."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
        household_authorizer=None,
        household_clock=None,
        failure_report_clock=None,
        initiative_authority_loader=None,
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("delegated work requires PostgreSQL")
        self._engine = engine
        self._initiative_authority_loader = initiative_authority_loader
        self._failure_report_clock = failure_report_clock or (lambda: datetime.now(UTC))
        self._scope_value = scope
        self._role = role
        self._household_authorizer = household_authorizer
        self._household_clock = household_clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    def _native_guard(self, spec):
        if not isinstance(spec, DelegatedInitiativeDigitalWorkSpec):
            return None
        try:
            if self._initiative_authority_loader is None:
                raise RuntimeError("native digital authority is not composed")
            return self._initiative_authority_loader().bind_work(spec)[0]
        except (ValueError, RuntimeError) as error:
            raise DelegatedWorkConflict("native initiative authority unavailable") from error

    def _settle_native(self, connection, spec, *, state, result=None, usage=None, now, guard=None):
        if not isinstance(spec, DelegatedInitiativeDigitalWorkSpec):
            return
        if self._initiative_authority_loader is None:
            raise DelegatedWorkConflict("native outcome authority is not composed")
        self._initiative_authority_loader().settle_work(connection, spec,
            state=state, result=result, usage=usage, now=now, guard=guard)
        # The host-bound parent is in the existing external conversation
        # namespace. Restore only this queue's fixed canonical authority scope,
        # never a scope supplied by the task/model, before reading its result.
        PostgresOperationalTimeline(self._engine, self.scope)._scope(connection)
        self._scope(connection)

    def _authorize_household(self, spec, connection, *, require_open=False, read_only=False):
        if not isinstance(spec, _HOUSEHOLD_WORK_TYPES):
            return
        try:
            if self._household_authorizer is None:
                raise ValueError("household work is not explicitly composed")
            validator = self._household_authorizer
            if read_only:
                # Read/control follows validated stored data. Admission, claims,
                # dispatch and cancellation retain the exact executable release.
                authority = getattr(validator, "__self__", None)
                validator = getattr(authority, "validate_read_spec", validator)
            validator(spec)
            from eva.adapters.postgres.household_work_sessions import verify_origin
            verify_origin(connection, self.scope, spec.origin, require_open=require_open, now=self._household_clock())
        except (ValueError, RuntimeError, OSError) as error:
            raise DelegatedWorkConflict("household work authority unavailable") from error

    def _read_scope(self, spec, connection, household_origin=None):
        if household_origin is None:
            if spec.audience != "private":
                raise DelegatedWorkNotFound("delegated work is absent")
        else:
            if (not isinstance(spec, _HOUSEHOLD_WORK_TYPES)
                    or spec.origin.destination() != household_origin.destination()):
                raise DelegatedWorkNotFound("delegated work is absent")
            self._authorize_household(spec, connection, require_open=True, read_only=True)

    def _authorize_household_continuation(self, work, connection, *, origin, execution_id):
        """Validate a durable correction without extending its foreground session."""
        if (not isinstance(work.spec, DelegatedHouseholdDigitalWorkSpec)
                or origin is None or work.spec.origin != origin
                or work.spec.execution_id != execution_id
                or self._household_clock() >= work.retention_until):
            raise DelegatedWorkConflict("household continuation differs from its admitted execution")
        self._authorize_household(work.spec, connection, read_only=True)

    def _authorize_household_request(self, work, connection, *, origin, request_origin):
        """Bind a new native request to the sealed retained task on the same device."""
        from eva.adapters.postgres.household_work_sessions import verify_origin
        if (not isinstance(work.spec, _HOUSEHOLD_WORK_TYPES) or origin is None
                or work.spec.origin != origin or self._household_clock() >= work.retention_until):
            raise DelegatedWorkNotFound("retained household work is absent")
        self._authorize_household(work.spec, connection, read_only=True)
        original = verify_origin(connection, self.scope, work.spec.origin,
            require_open=False, now=self._household_clock())
        current = verify_origin(connection, self.scope, request_origin,
            require_open=True, now=self._household_clock())
        authority = getattr(self._household_authorizer, "__self__", None)
        retained_read = (
            callable(getattr(authority, "permits_retained_read", None))
            and authority.permits_retained_read(work.spec)
            and request_origin.research_policy_sha256 == authority.sha256
        )
        if (original.get("native_conversation_key") is None
                or current.get("native_conversation_key") is None
                or any(getattr(work.spec.origin, key) != getattr(request_origin, key) for key in (
                    "tenant_id", "installation_id", "household_id", "actor_id",
                    "satellite_id", "device_id", "consent_operation_id",
                    "household_policy_sha256"))
                or (not retained_read and work.spec.origin.research_policy_sha256
                    != request_origin.research_policy_sha256)):
            raise DelegatedWorkNotFound("retained household request differs from its device authority")

    def _authorize_household_delivery(self, work, connection, *, origin, binding):
        """Read only a retained outcome for its sealed original native destination."""
        from eva.adapters.postgres.household_work_sessions import verify_origin
        from eva.application.work_failure_notice import delivery_outcome
        if (not isinstance(work.spec, _HOUSEHOLD_WORK_TYPES) or origin is None
                or binding != work.spec.origin.destination() or origin.destination() != binding
                or self._household_clock() >= work.retention_until
                or delivery_outcome(work) is None):
            raise DelegatedWorkNotFound("retained household outcome is absent")
        self._authorize_household(work.spec, connection, read_only=True)
        original = verify_origin(connection, self.scope, work.spec.origin,
            require_open=False, now=self._household_clock())
        if original.get("native_conversation_key") is None:
            raise DelegatedWorkNotFound("retained household outcome requires its native destination")

    def _authorize_household_access(self, work, connection, *, origin,
                                    execution_id=None, request_origin=None, delivery_binding=None):
        if sum(value is not None for value in (execution_id, request_origin, delivery_binding)) > 1:
            raise DelegatedWorkConflict("household access paths cannot be combined")
        if request_origin is not None:
            self._authorize_household_request(work, connection, origin=origin, request_origin=request_origin)
        elif delivery_binding is not None:
            self._authorize_household_delivery(work, connection, origin=origin, binding=delivery_binding)
        elif execution_id is not None:
            self._authorize_household_continuation(work, connection, origin=origin, execution_id=execution_id)
        else:
            self._read_scope(work.spec, connection, origin)

    @property
    def scope(self) -> TimelineScope:
        return self._scope_value

    def validate_admission_authority(
        self,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
    ) -> None:
        """Fail startup before chat can advertise a stale delegated authority."""

        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                self._validate_owner_binding(
                    connection,
                    owner_principal_id=owner_principal_id,
                    principal_binding_id=principal_binding_id,
                )
            finally:
                transaction.rollback()

    def admit(
        self,
        *,
        spec: DelegatedWorkSpec,
        title: str,
        summary: str,
        worker_role: str,
        retention_policy_id: str,
        retention_until: datetime,
        occurred_at: datetime,
        resumes_work_id: str | None = None,
        delivery_destination: PrivateWorkDestination | None = None,
        household_continuation_execution_id: str | None = None,
        household_request_origin=None,
        _connection=None,
        _native_guard=None,
    ) -> DelegatedWorkAdmission:
        if isinstance(spec, DelegatedInitiativeDigitalWorkSpec):
            if (_connection is None or _native_guard is None
                    or delivery_destination is not None
                    or spec.origin.intent_id != _native_guard.claim.intent_id
                    or spec.origin.claim_revision != _native_guard.claim.revision
                    or (resumes_work_id is None and spec.origin.mandate_sha256 != _native_guard.mandate_sha)):
                raise DelegatedWorkConflict("native admission requires its atomic host handoff")
            if resumes_work_id is not None and (
                    _native_guard.resume_work is None
                    or _native_guard.resume_work.spec.work_id != resumes_work_id
                    or _native_guard.work_spec != spec):
                raise DelegatedWorkConflict("native resume requires its exact recovery admission guard")
            if _native_guard.authority.owner._authority != self.scope:
                raise DelegatedWorkConflict("native queue authority scope differs")
            _native_guard.check_locked(_connection)
            PostgresOperationalTimeline(self._engine, self.scope)._scope(_connection)
        elif _connection is not None or _native_guard is not None:
            raise DelegatedWorkConflict("owner admission cannot borrow a native transaction")
        # These three values are trusted operator-display labels, not task text.
        # The local cockpit remains unauthenticated until its own product gate.
        title = _bounded_text(title, maximum=120, label="work title")
        summary = _bounded_text(summary, maximum=240, label="work summary")
        worker_role = _bounded_text(worker_role, maximum=80, label="worker role")
        retention_policy_id = _bounded_text(
            retention_policy_id, maximum=192, label="retention policy"
        )
        occurred_at = _whole_second(occurred_at, label="occurred_at")
        retention_until = _whole_second(retention_until, label="retention_until")
        deadline = _parse_timestamp(spec.deadline)
        if retention_until < deadline or occurred_at > deadline:
            raise DelegatedWorkConflict(
                "delegated work retention or deadline is invalid"
            )
        try:
            storage_episode_id = UUID(spec.episode_id)
            owner_principal_id = UUID(spec.owner_principal_id)
            principal_binding_id = UUID(spec.principal_binding_id)
        except ValueError as error:
            raise DelegatedWorkConflict(
                "delegated work database identifiers are invalid"
            ) from error

        attention_id = uuid5(
            self._scope_value.installation_id, f"delegated-work:{spec.work_id}"
        )
        spec_payload = spec.model_dump(mode="json")
        spec_sha256 = canonical_sha256(spec_payload)
        with (self._engine.begin() if _connection is None else nullcontext(_connection)) as connection:
            self._scope(connection)
            if household_continuation_execution_id is not None and (
                    resumes_work_id is None
                    or not isinstance(spec, DelegatedHouseholdDigitalWorkSpec)
                    or spec.execution_id != household_continuation_execution_id):
                raise DelegatedWorkConflict("household continuation requires its original digital attempt")
            if household_request_origin is not None and (
                    resumes_work_id is None or not isinstance(spec, _HOUSEHOLD_WORK_TYPES)
                    or household_continuation_execution_id is not None):
                raise DelegatedWorkConflict("household requester requires one retained resume")
            self._authorize_household(spec, connection,
                require_open=household_continuation_execution_id is None and household_request_origin is None)
            if isinstance(spec, _HOUSEHOLD_WORK_TYPES) and retention_until > _parse_timestamp(spec.created_at) + timedelta(days=7):
                raise DelegatedWorkConflict("household retention exceeds its bounded policy")
            if isinstance(spec, _HOUSEHOLD_WORK_TYPES) and delivery_destination is not None:
                raise DelegatedWorkConflict("household work cannot bind a private destination")
            self._validate_owner_binding(
                connection, owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
            )
            previous = None
            if resumes_work_id is not None:
                previous = self._load_terminal_locked(
                    connection, work_id=resumes_work_id,
                    missing_message="previous delegated work is absent",
                )
                if household_continuation_execution_id is not None:
                    self._authorize_household_continuation(previous, connection,
                        origin=spec.origin, execution_id=household_continuation_execution_id)
                if household_request_origin is not None:
                    self._authorize_household_request(previous, connection,
                        origin=spec.origin, request_origin=household_request_origin)
                if (previous.spec.owner_principal_id != str(owner_principal_id)
                        or previous.spec.principal_binding_id != str(principal_binding_id)):
                    raise DelegatedWorkNotFound("previous delegated work is absent")
                if isinstance(previous.spec, DelegatedInitiativeDigitalWorkSpec):
                    if (not isinstance(spec, DelegatedInitiativeDigitalWorkSpec)
                            or spec.origin != previous.spec.origin
                            or (previous.state != "failed" and not unstarted_profile_refresh(previous))
                            or previous.stop_requested_at is not None
                            or previous.lease_owner is not None or previous.retention_until <= occurred_at
                            or _native_guard is None
                            or _native_guard.resume_work.spec.sha256 != previous.spec.sha256):
                        raise DelegatedWorkNotResumable(previous)
                elif isinstance(spec, DelegatedInitiativeDigitalWorkSpec):
                    raise DelegatedWorkConflict("native resume cannot borrow ordinary owner work")
                expected_id = "work:eva-resume:" + str(uuid5(
                    self._scope_value.installation_id,
                    f"{previous.spec.work_id}:{previous.spec.generation + 1}",
                ))
                if (spec.audience != previous.spec.audience
                        or (isinstance(spec, _HOUSEHOLD_WORK_TYPES)
                            and spec.origin != previous.spec.origin)
                        or spec.work_id != expected_id or spec.parent_task_id != previous.spec.work_id
                        or spec.generation != previous.spec.generation + 1
                        or spec.episode_id != previous.spec.episode_id
                        or spec.task != previous.spec.task or spec.grant != previous.spec.grant
                        or getattr(spec, "research", None) != getattr(previous.spec, "research", None)
                        or getattr(spec, "source", None) != getattr(previous.spec, "source", None)
                        or getattr(spec, "source_ingress_sha256", None) != getattr(previous.spec, "source_ingress_sha256", None)
                        or getattr(spec, "execution_id", None) != getattr(previous.spec, "execution_id", None)):
                    raise DelegatedWorkConflict("resumed work differs from its bounded previous attempt")
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"delegated-work:{self._scope_value.tenant_id}:"
                        f"{self._scope_value.installation_id}:{spec.work_id}"
                    )
                },
            )
            existing = (
                connection.execute(
                    sa.select(delegated_work_runs).where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.work_id == spec.work_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                loaded = self._validated(connection, existing)
                record_admission_destination(connection, self.scope, spec=loaded.spec,
                    destination=delivery_destination, occurred_at=occurred_at, existing=True)
                if previous is not None:
                    # The first explicit resume owns this successor. Later owner
                    # retries return it, including its original clock and release;
                    # a newer ingress never creates a parallel successor.
                    fields = ("audience", "episode_id", "generation", "parent_task_id", "owner_principal_id",
                              "principal_binding_id", "task", "grant")
                    if (any(getattr(loaded.spec, key) != getattr(spec, key) for key in fields)
                            or getattr(loaded.spec, "research", None) != getattr(spec, "research", None)
                            or getattr(loaded.spec, "origin", None) != getattr(spec, "origin", None)
                            or getattr(loaded.spec, "source", None) != getattr(spec, "source", None)
                            or getattr(loaded.spec, "source_ingress_sha256", None) != getattr(spec, "source_ingress_sha256", None)
                            or getattr(loaded.spec, "execution_id", None) != getattr(spec, "execution_id", None)):
                        raise DelegatedWorkConflict("resumed work id owns another attempt")
                    return DelegatedWorkAdmission("already_present", loaded)
                if (
                    loaded.spec != spec
                    or loaded.title != title
                    or loaded.summary != summary
                    or loaded.worker_role != worker_role
                    or loaded.retention_policy_id != retention_policy_id
                    or loaded.retention_until != retention_until
                ):
                    raise DelegatedWorkConflict(
                        "work id already owns different immutable bytes"
                    )
                return DelegatedWorkAdmission("already_present", loaded)

            if previous is not None and not (
                previous.state in {"failed", "ambiguous", "needs_help", "stopped"}
                or (previous.state in {"admitted", "call_started"}
                    and occurred_at > _parse_timestamp(previous.spec.deadline))
            ):
                raise DelegatedWorkNotResumable(previous)
            connection.execute(
                sa.insert(delegated_work_runs).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    work_id=spec.work_id,
                    attention_id=attention_id,
                    storage_episode_id=storage_episode_id,
                    generation=spec.generation,
                    owner_principal_id=owner_principal_id,
                    principal_binding_id=principal_binding_id,
                    parent_task_id=spec.parent_task_id,
                    title=title,
                    summary=summary,
                    worker_name=spec.worker_name,
                    worker_role=worker_role,
                    state="admitted",
                    claim_count=0,
                    lease_owner=None,
                    lease_expires_at=None,
                    call_id=None,
                    spec_payload=spec_payload,
                    spec_sha256=bytes.fromhex(spec_sha256),
                    result_payload=None,
                    result_sha256=None,
                    usage_payload=None,
                    usage_sha256=None,
                    error_code=None,
                    error_fingerprint=None,
                    revision=1,
                    deadline=deadline,
                    call_started_at=None,
                    completed_at=None,
                    retention_policy_id=retention_policy_id,
                    retention_until=retention_until,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
            )
            row = (
                connection.execute(
                    sa.select(delegated_work_runs).where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.work_id == spec.work_id,
                    )
                )
                .mappings()
                .one()
            )
            loaded = self._validated(connection, row)
            record_admission_destination(connection, self.scope, spec=spec,
                destination=delivery_destination, occurred_at=occurred_at, existing=False)
        return DelegatedWorkAdmission("inserted", loaded)

    def retire_one_unstarted_native(self, *, release, owner_principal_id,
            principal_binding_id, now):
        """Retire one obsolete native admission, retaining its immutable no-call proof.

        Selection is independent of the old deadline/release claim filter. A crash
        after retirement is recoverable through the same terminal row next time.
        Started work and uncertain leases are deliberately excluded.
        """
        c = delegated_work_runs.c
        successor = delegated_work_runs.alias("native_successor")
        mismatch = sa.and_(c.spec_payload["worker_program_sha256"].as_string() != release.worker_program_sha256,
            *(c.spec_payload[name].as_string() == getattr(release, name)
              for name in ("worker_profile_release_sha256", "model_profile_release_sha256",
                           "engine_release_sha256", "output_schema_sha256")))
        with self._engine.begin() as conn:
            self._scope(conn)
            row = conn.execute(sa.select(delegated_work_runs).where(
                c.tenant_id == self.scope.tenant_id, c.installation_id == self.scope.installation_id,
                c.spec_payload["contract"].as_string() == "DelegatedInitiativeDigitalWork/v1",
                c.owner_principal_id == owner_principal_id,
                c.principal_binding_id == principal_binding_id,
                sa.or_(c.state == "admitted", sa.and_(c.state == "needs_help",
                    c.error_code == "unstarted_profile_superseded")),
                c.claim_count == 0, c.call_id.is_(None), c.call_started_at.is_(None),
                c.lease_owner.is_(None), c.lease_expires_at.is_(None),
                c.stop_requested_at.is_(None), c.retention_until > now, mismatch,
                ~sa.exists(sa.select(1).where(
                    successor.c.tenant_id == c.tenant_id,
                    successor.c.installation_id == c.installation_id,
                    successor.c.parent_task_id == c.work_id)),
            ).order_by(c.created_at, c.work_id).with_for_update(
                of=delegated_work_runs, skip_locked=True).limit(1)).mappings().one_or_none()
            if row is None:
                return None
            work = self._validated(conn, row)
            self._validate_owner_binding(conn, owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id)
            authority = self._initiative_authority_loader()
            owner = authority.owner
            if (owner._owner != owner_principal_id or owner._binding != principal_binding_id
                    or owner._authority != self.scope):
                raise DelegatedWorkConflict("native refresh owner authority differs")
            owner._authorize(conn, now)
            parent_query = owner._query().where(
                operational_checkpoints.c.thread_id == work.spec.origin.thread_id)
            # Queue-to-parent lock order also exists in claim_next. Never wait on
            # a resume that already holds the parent and is waiting for this row.
            parent = conn.execute(parent_query.with_for_update(
                of=operational_checkpoints, skip_locked=True)).one_or_none()
            if parent is None and conn.execute(parent_query).one_or_none() is not None:
                return None  # Parent is busy; do not confuse a lock with deletion.
            events = authority.timeline.events(work.spec.origin.thread_id) if parent is not None else ()
            event = events[-1] if events else None
            data = {} if event is None else event.payload.get("data", {})
            expected_phase = "initiative_work_waiting" if work.state == "admitted" else "initiative_blocked"
            current = (parent is not None and parent.phase == expected_phase
                and event is not None and event.sequence == parent.revision
                and canonical_sha256(event.payload) == event.payload_sha256
                and data.get("work_id") == work.spec.work_id
                and data.get("spec_sha256") == work.spec.sha256)
            PostgresOperationalTimeline(self._engine, self.scope)._scope(conn)
            if not current:
                if work.state == "admitted":
                    self._mark_needs_help(conn, work, now=now,
                        error_code="unstarted_profile_refresh_withdrawn", settle_parent=False)
                else:
                    self._withdraw_unstarted_refresh(conn, work, now=now)
                return None
            if work.state == "admitted":
                self._mark_needs_help(conn, work, now=now,
                    error_code="unstarted_profile_superseded")
                work = self._load_locked(conn, work.spec.work_id)
            if not unstarted_profile_refresh(work):
                raise DelegatedWorkConflict("profile refresh lacks its unstarted evidence")
            return work

    def withdraw_unstarted_refresh(self, work, *, now):
        """A denied refresh must not starve ordinary initiative reviews forever."""
        with self._engine.begin() as conn:
            self._scope(conn)
            current = self._load_terminal_locked(conn, work_id=work.spec.work_id,
                missing_message="native refresh source absent")
            if current.spec.sha256 != work.spec.sha256:
                raise DelegatedWorkConflict("native refresh source changed")
            if current.state == "needs_help" and current.error_code == "unstarted_profile_refresh_withdrawn":
                return  # Concurrent refusal already recorded this same immutable source.
            self._withdraw_unstarted_refresh(conn, current, now=now)

    def _withdraw_unstarted_refresh(self, conn, work, *, now):
        if not unstarted_profile_refresh(work):
            raise DelegatedWorkConflict("only proven unstarted refresh may be withdrawn")
        c = delegated_work_runs.c
        successor = delegated_work_runs.alias("refresh_successor")
        conn.execute(sa.update(delegated_work_runs).where(
            c.tenant_id == self.scope.tenant_id, c.installation_id == self.scope.installation_id,
            c.work_id == work.spec.work_id, c.revision == work.revision,
            ~sa.exists(sa.select(1).where(successor.c.tenant_id == c.tenant_id,
                successor.c.installation_id == c.installation_id,
                successor.c.parent_task_id == c.work_id)),
        ).values(error_code="unstarted_profile_refresh_withdrawn",
            error_fingerprint=bytes.fromhex(canonical_sha256({"error": "unstarted_profile_refresh_withdrawn"})),
            revision=work.revision + 1, updated_at=now))

    def claim_next(
        self,
        *,
        worker_id: str,
        release: DelegatedWorkReleaseSelector,
        now: datetime,
        lease_seconds: int = 30,
    ) -> DelegatedWorkClaim | None:
        worker_id = _bounded_text(worker_id, maximum=120, label="worker id")
        now = _whole_second(now, label="claim time")
        if not 1 <= lease_seconds <= 300:
            raise ValueError("delegated work lease must be 1-300 seconds")
        lease_owner = f"{worker_id}:{uuid4()}"
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self._engine.begin() as connection:
            self._scope(connection)
            row = (
                connection.execute(
                    sa.select(delegated_work_runs)
                    .where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.state == "admitted",
                        delegated_work_runs.c.spec_payload[
                            "worker_profile_release_sha256"
                        ].as_string()
                        == release.worker_profile_release_sha256,
                        delegated_work_runs.c.spec_payload[
                            "model_profile_release_sha256"
                        ].as_string()
                        == release.model_profile_release_sha256,
                        delegated_work_runs.c.spec_payload[
                            "worker_program_sha256"
                        ].as_string()
                        == release.worker_program_sha256,
                        delegated_work_runs.c.spec_payload[
                            "engine_release_sha256"
                        ].as_string()
                        == release.engine_release_sha256,
                        delegated_work_runs.c.spec_payload[
                            "output_schema_sha256"
                        ].as_string()
                        == release.output_schema_sha256,
                        sa.or_(
                            delegated_work_runs.c.lease_owner.is_(None),
                            delegated_work_runs.c.lease_expires_at < now,
                        ),
                    )
                    .order_by(
                        delegated_work_runs.c.created_at, delegated_work_runs.c.work_id
                    )
                    .with_for_update(of=delegated_work_runs, skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            loaded = self._validated(connection, row)
            lease_expires_at = min(
                lease_expires_at,
                _parse_timestamp(loaded.spec.deadline),
            )
            if now > _parse_timestamp(loaded.spec.deadline):
                self._mark_needs_help(
                    connection, loaded, now=now, error_code="deadline_expired"
                )
                return None
            try:
                self._authorize_household(loaded.spec, connection)
                self._native_guard(loaded.spec)
                self._validate_owner_binding(
                    connection,
                    owner_principal_id=UUID(loaded.spec.owner_principal_id),
                    principal_binding_id=UUID(loaded.spec.principal_binding_id),
                )
            except DelegatedWorkConflict:
                self._mark_needs_help(
                    connection, loaded, now=now, error_code="authority_revoked"
                )
                return None
            maximum_claims = loaded.spec.grant.maximum_claims_before_call_start
            next_claim = loaded.claim_count + 1
            if next_claim > maximum_claims:
                self._mark_needs_help(
                    connection,
                    loaded,
                    now=now,
                    error_code="claim_budget_exhausted",
                )
                return None

            work_result = connection.execute(
                sa.update(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == loaded.spec.work_id,
                    delegated_work_runs.c.revision == loaded.revision,
                    delegated_work_runs.c.state == "admitted",
                    sa.or_(
                        delegated_work_runs.c.lease_owner.is_(None),
                        delegated_work_runs.c.lease_expires_at < now,
                    ),
                )
                .values(
                    claim_count=next_claim,
                    lease_owner=lease_owner,
                    lease_expires_at=lease_expires_at,
                    revision=loaded.revision + 1,
                    updated_at=now,
                )
            )
            if work_result.rowcount != 1:
                raise DelegatedWorkConflict("delegated work claim raced")
            return DelegatedWorkClaim(
                work_id=loaded.spec.work_id,
                attention_id=UUID(str(row.attention_id)),
                lease_owner=lease_owner,
                work_revision=loaded.revision + 1,
                spec=loaded.spec,
            )

    def start_call(
        self, claim: DelegatedWorkClaim, *, now: datetime
    ) -> DelegatedWorkCall | None:
        now = _whole_second(now, label="call start time")
        call_id = f"child-call:{uuid4()}"
        with self._engine.begin() as connection:
            self._scope(connection)
            row = (
                connection.execute(
                    sa.select(delegated_work_runs)
                    .where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.work_id == claim.work_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DelegatedWorkNotFound("delegated work claim is absent")
            if row.state != "admitted":
                return None
            loaded = self._validated(connection, row)
            if now > _parse_timestamp(loaded.spec.deadline):
                self._mark_needs_help(
                    connection, loaded, now=now, error_code="deadline_expired"
                )
                return None
            try:
                self._authorize_household(loaded.spec, connection)
                self._native_guard(loaded.spec)
                self._validate_owner_binding(
                    connection,
                    owner_principal_id=UUID(loaded.spec.owner_principal_id),
                    principal_binding_id=UUID(loaded.spec.principal_binding_id),
                )
            except DelegatedWorkConflict:
                self._mark_needs_help(
                    connection, loaded, now=now, error_code="authority_revoked"
                )
                return None
            if (
                loaded.spec != claim.spec
                or int(row.revision) != claim.work_revision
                or row.attention_id != claim.attention_id
                or row.lease_owner != claim.lease_owner
                or row.lease_expires_at is None
                or row.lease_expires_at < now
            ):
                raise DelegatedWorkConflict("delegated work claim is stale")
            result = connection.execute(
                sa.update(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == claim.work_id,
                    delegated_work_runs.c.revision == claim.work_revision,
                    delegated_work_runs.c.state == "admitted",
                    delegated_work_runs.c.call_id.is_(None),
                    delegated_work_runs.c.lease_owner == claim.lease_owner,
                )
                .values(
                    state="call_started",
                    call_id=call_id,
                    call_started_at=now,
                    revision=claim.work_revision + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
        return DelegatedWorkCall(
            work_id=claim.work_id,
            attention_id=claim.attention_id,
            call_id=call_id,
            spec=claim.spec,
        )

    def record_success(
        self, call: DelegatedWorkCall, result: DelegatedWorkResult, *, now: datetime
    ) -> StoredDelegatedWork:
        now = _whole_second(now, label="result time")
        if result.work_id != call.work_id or result.call_id != call.call_id:
            raise DelegatedWorkConflict("delegated result is bound to another call")
        if _parse_timestamp(result.completed_at) != now:
            raise DelegatedWorkConflict("delegated result time differs from its commit")
        if now > _parse_timestamp(call.spec.deadline):
            raise DelegatedWorkConflict("delegated result arrived after its deadline")
        _validate_result_version(call.spec, result)
        _validate_usage(call.spec, result.usage, successful=True)
        return self._record_terminal(
            call,
            state="succeeded",
            result=result,
            usage=result.usage,
            error_code=None,
            error_fingerprint=None,
            now=now,
        )

    def record_failure(
        self,
        call: DelegatedWorkCall,
        *,
        usage: DelegatedWorkUsage,
        error_code: str,
        error_fingerprint: str,
        ambiguous: bool,
        now: datetime,
        failure_report: str | None = None,
    ) -> StoredDelegatedWork:
        if failure_report is not None:
            from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
            if (not isinstance(call.spec, DelegatedDigitalWorkSpec) or ambiguous
                    or error_code not in {"budget_exceeded", "incomplete_work"} or not isinstance(failure_report, str)
                    or not failure_report.strip() or len(failure_report.encode("utf-8")) > 65_536):
                raise ValueError("failure report requires non-ambiguous private digital unfinished work")
        error_code = _bounded_text(error_code, maximum=80, label="error code")
        if re.fullmatch(r"[0-9a-f]{64}", error_fingerprint) is None:
            raise ValueError("error fingerprint is invalid")
        _validate_usage(call.spec, usage, successful=False)
        return self._record_terminal(
            call,
            state="ambiguous" if ambiguous else "failed",
            result=None,
            usage=usage,
            error_code=error_code,
            error_fingerprint=error_fingerprint,
            now=now,
            failure_report=failure_report,
        )

    def verify_result(
        self,
        work_id: str,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        now: datetime,
        household_origin=None,
    ) -> StoredDelegatedWork:
        now = _whole_second(now, label="verification time")
        with self._engine.begin() as connection:
            self._scope(connection)
            self._validate_owner_binding(
                connection,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
            )
            row = (
                connection.execute(
                    sa.select(delegated_work_runs)
                    .where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.work_id == work_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DelegatedWorkNotFound("delegated work is absent")
            loaded = self._validated(connection, row)
            self._read_scope(loaded.spec, connection, household_origin)
            if loaded.spec.owner_principal_id != str(
                owner_principal_id
            ) or loaded.spec.principal_binding_id != str(principal_binding_id):
                raise DelegatedWorkNotFound("delegated work is absent")
            if loaded.state == "verified":
                return loaded
            if loaded.state != "succeeded":
                raise DelegatedWorkConflict("only a received result can be verified")
            connection.execute(
                sa.update(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == work_id,
                    delegated_work_runs.c.revision == loaded.revision,
                    delegated_work_runs.c.state == "succeeded",
                )
                .values(state="verified", revision=loaded.revision + 1, updated_at=now)
            )
            return self._load_locked(connection, work_id)

    def request_stop(
        self,
        work_id: str,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        now: datetime,
        household_origin=None,
        household_continuation_execution_id: str | None = None,
        household_request_origin=None,
    ) -> StoredDelegatedWork:
        """Stop before a call or durably request cancellation of an active call."""

        now = _whole_second(now, label="stop time")
        with self._engine.begin() as connection:
            self._scope(connection)
            self._validate_owner_binding(
                connection,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
            )
            observed = connection.execute(
                sa.select(
                    delegated_work_runs.c.attention_id,
                    delegated_work_runs.c.owner_principal_id,
                    delegated_work_runs.c.principal_binding_id,
                ).where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == work_id,
                )
            ).one_or_none()
            if (
                observed is None
                or observed.owner_principal_id != owner_principal_id
                or observed.principal_binding_id != principal_binding_id
            ):
                raise DelegatedWorkNotFound("delegated work is absent")
            loaded = self._load_terminal_locked(
                connection, work_id=work_id, missing_message="delegated work is absent"
            )
            self._authorize_household_access(loaded, connection, origin=household_origin,
                execution_id=household_continuation_execution_id, request_origin=household_request_origin)
            if loaded.spec.owner_principal_id != str(
                owner_principal_id
            ) or loaded.spec.principal_binding_id != str(principal_binding_id):
                raise DelegatedWorkNotFound("delegated work is absent")
            if loaded.state == "stopped":
                return loaded
            if loaded.state == "call_started":
                if loaded.stop_requested_at is not None:
                    return loaded
                updated = connection.execute(
                    sa.update(delegated_work_runs)
                    .where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.work_id == work_id,
                        delegated_work_runs.c.revision == loaded.revision,
                        delegated_work_runs.c.state == "call_started",
                        delegated_work_runs.c.stop_requested_at.is_(None),
                    )
                    .values(
                        stop_requested_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        revision=loaded.revision + 1,
                        updated_at=now,
                    )
                )
                if updated.rowcount != 1:
                    raise DelegatedWorkConflict("delegated stop request raced")
                return self._load_locked(connection, work_id)
            if loaded.state != "admitted" or loaded.call_id is not None:
                return loaded
            work_update = connection.execute(
                sa.update(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == work_id,
                    delegated_work_runs.c.revision == loaded.revision,
                    delegated_work_runs.c.state == "admitted",
                    delegated_work_runs.c.call_id.is_(None),
                )
                .values(
                    state="stopped",
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=now,
                    revision=loaded.revision + 1,
                    updated_at=now,
                )
            )
            if work_update.rowcount != 1:
                raise DelegatedWorkConflict("delegated stop raced")
            self._settle_native(connection, loaded.spec, state="stopped", now=now)
            return self._load_locked(connection, work_id)

    def stop_requested(self, call: DelegatedWorkCall) -> bool:
        """Read the durable owner cancellation bit for exactly one fenced call."""

        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                row = connection.execute(
                    sa.select(
                        delegated_work_runs.c.call_id,
                        delegated_work_runs.c.spec_sha256,
                        delegated_work_runs.c.stop_requested_at,
                    ).where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.work_id == call.work_id,
                    )
                ).one_or_none()
                if (
                    row is None
                    or row.call_id != call.call_id
                    or bytes(row.spec_sha256).hex() != call.spec.sha256
                ):
                    raise DelegatedWorkConflict("delegated stop call authority differs")
                return row.stop_requested_at is not None
            finally:
                transaction.rollback()

    def execution_authority_current(self, call):
        try:
            self._native_guard(call.spec)
            return self.household_authority_current(call)
        except DelegatedWorkConflict:
            return False

    def household_authority_current(self, call):
        if not isinstance(call.spec, _HOUSEHOLD_WORK_TYPES):
            return True
        try:
            with self._engine.begin() as connection:
                self._scope(connection)
                self._authorize_household(call.spec, connection)
                self._validate_owner_binding(connection, owner_principal_id=UUID(call.spec.owner_principal_id),
                    principal_binding_id=UUID(call.spec.principal_binding_id))
            return True
        except DelegatedWorkConflict:
            return False

    def authorize_tool_dispatch(self, call: DelegatedWorkCall, *, _connection=None) -> None:
        """Recheck stop under an optional caller-owned atomic result transaction."""
        self._native_guard(call.spec)
        def check(connection):
            self._scope(connection)
            self._authorize_household(call.spec, connection)
            self._validate_owner_binding(connection,
                owner_principal_id=UUID(call.spec.owner_principal_id),
                principal_binding_id=UUID(call.spec.principal_binding_id))
            row = connection.execute(sa.select(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                delegated_work_runs.c.installation_id == self._scope_value.installation_id,
                delegated_work_runs.c.work_id == call.work_id,
            ).with_for_update()).mappings().one_or_none()
            if (row is None or row.call_id != call.call_id or row.state != "call_started"
                    or bytes(row.spec_sha256).hex() != call.spec.sha256
                    or row.stop_requested_at is not None):
                raise DelegatedWorkConflict("research call is stopped or no longer authorized")

        if _connection is not None:
            check(_connection)
        else:
            with self._engine.begin() as connection:
                check(connection)

    def record_stopped(
        self,
        call: DelegatedWorkCall,
        *,
        usage: DelegatedWorkUsage,
        now: datetime,
    ) -> StoredDelegatedWork:
        _validate_usage(call.spec, usage, successful=False)
        return self._record_terminal(
            call,
            state="stopped",
            result=None,
            usage=usage,
            error_code=None,
            error_fingerprint=None,
            now=now,
        )

    def latest_stoppable_work_id(
        self,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        request_key: str | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """Find the latest stoppable task, retaining stopped work for exact replay."""

        if request_key is not None:
            if not request_key or len(request_key) > 2048 or now is None:
                raise ValueError("stop target requires a bounded request and time")
            now = _whole_second(now, label="stop target time")
        with self._engine.begin() as connection:
            self._scope(connection)
            self._validate_owner_binding(
                connection,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
            )
            target_thread = None
            if request_key is not None:
                target_thread = "delegated:stop-target:" + canonical_sha256({
                    "owner": str(owner_principal_id), "binding": str(principal_binding_id),
                    "request": request_key,
                })
                connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                   {"key": target_thread})
                saved = connection.execute(sa.select(operational_events).where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id == self._scope_value.installation_id,
                    operational_events.c.thread_id == target_thread,
                    operational_events.c.sequence == 1,
                )).mappings().one_or_none()
                if saved is not None:
                    if (saved["event_type"] != "DELEGATED_STOP_TARGET_BOUND"
                            or canonical_sha256(saved["payload"]) != bytes(saved["payload_sha256"]).hex()):
                        raise DelegatedWorkConflict("saved stop target is corrupt")
                    data = saved["payload"]["data"]
                    if data["owner"] != str(owner_principal_id) or data["binding"] != str(principal_binding_id):
                        raise DelegatedWorkConflict("saved stop target authority differs")
                    if data["work_id"] is not None:
                        saved_work = self._load_locked(connection, data["work_id"])
                        self._read_scope(saved_work.spec, connection)
                    return data["work_id"]
            selected = connection.scalar(
                sa.select(delegated_work_runs.c.work_id)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.spec_payload["audience"].astext == "private",
                    delegated_work_runs.c.owner_principal_id == owner_principal_id,
                    delegated_work_runs.c.principal_binding_id
                    == principal_binding_id,
                    delegated_work_runs.c.state.in_(
                        ("admitted", "call_started", "stopped")
                    ),
                )
                .order_by(
                    delegated_work_runs.c.created_at.desc(),
                    delegated_work_runs.c.work_id.desc(),
                )
                .limit(1)
            )
            if target_thread is not None:
                insert_initial_timeline_thread(
                    connection, self._scope_value, thread_id=target_thread,
                    checkpoint_id=target_thread, occurred_at=now,
                    event_type="DELEGATED_STOP_TARGET_BOUND", phase="target_selected",
                    status="completed", data={"owner": str(owner_principal_id),
                                              "binding": str(principal_binding_id),
                                              "work_id": selected},
                )
            return selected

    def load(
        self,
        work_id: str,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        household_origin=None,
        household_continuation_execution_id: str | None = None,
        household_request_origin=None,
        household_delivery_binding=None,
    ) -> StoredDelegatedWork:
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                self._validate_owner_binding(
                    connection,
                    owner_principal_id=owner_principal_id,
                    principal_binding_id=principal_binding_id,
                )
                loaded = self._load_locked(connection, work_id)
                self._authorize_household_access(loaded, connection, origin=household_origin,
                    execution_id=household_continuation_execution_id, request_origin=household_request_origin,
                    delivery_binding=household_delivery_binding)
                if loaded.spec.owner_principal_id != str(
                    owner_principal_id
                ) or loaded.spec.principal_binding_id != str(principal_binding_id):
                    raise DelegatedWorkNotFound("delegated work is absent")
                return loaded
            finally:
                transaction.rollback()

    def next_memory_outcome(self, *, owner_principal_id: UUID,
                            principal_binding_id: UUID, now: datetime,
                            completed_since: datetime) -> StoredDelegatedWork | None:
        """One retained private digital outcome not yet projected into memory."""
        now = _whole_second(now, label="now")
        completed_since = _whole_second(completed_since, label="completed_since")
        with self._engine.begin() as connection:
            self._scope(connection)
            self._validate_owner_binding(connection, owner_principal_id=owner_principal_id,
                                         principal_binding_id=principal_binding_id)
            marker = "work-memory:" + sa.func.encode(
                sa.func.sha256(sa.func.convert_to(delegated_work_runs.c.work_id, "UTF8")), "hex")
            processed = sa.exists(sa.select(1).where(
                operational_checkpoints.c.tenant_id == self.scope.tenant_id,
                operational_checkpoints.c.installation_id == self.scope.installation_id,
                operational_checkpoints.c.thread_id == marker))
            row = connection.execute(sa.select(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id == self.scope.tenant_id,
                delegated_work_runs.c.installation_id == self.scope.installation_id,
                delegated_work_runs.c.owner_principal_id == owner_principal_id,
                delegated_work_runs.c.principal_binding_id == principal_binding_id,
                delegated_work_runs.c.spec_payload["contract"].astext.in_(
                    ("DelegatedDigitalWork/v4", "DelegatedInitiativeDigitalWork/v1")),
                delegated_work_runs.c.spec_payload["audience"].astext == "private",
                delegated_work_runs.c.state.in_(("succeeded", "verified", "failed", "stopped", "needs_help")),
                delegated_work_runs.c.completed_at >= completed_since,
                delegated_work_runs.c.retention_until > now, ~processed,
            ).order_by(delegated_work_runs.c.completed_at, delegated_work_runs.c.work_id)
              .limit(1)).mappings().one_or_none()
            return None if row is None else self._validated(connection, row)

    def list_owned(
        self, *, owner_principal_id: UUID, principal_binding_id: UUID,
        limit: int = 21, active_first: bool = False,
        query: str | None = None, before_work_id: str | None = None, parent_ids: tuple[str, ...] | None = None,
    ) -> tuple[StoredDelegatedWork, ...]:
        """Return bounded private job identifiers/results from this exact binding."""
        if type(limit) is not int or not 1 <= limit <= 65:
            raise ValueError("delegated work listing limit is invalid")
        if type(active_first) is not bool:
            raise ValueError("delegated work active-first flag must be boolean")
        if query is not None and (not isinstance(query, str) or not 1 <= len(query.strip()) <= 200):
            raise ValueError("work search requires 1..200 characters")
        if before_work_id is not None and (
                not isinstance(before_work_id, str) or not 1 <= len(before_work_id) <= 240
                or active_first):
            raise ValueError("work search cursor is invalid")
        if parent_ids is not None and (not isinstance(parent_ids, tuple) or not 1 <= len(parent_ids) <= 64
                or any(not isinstance(value, str) or not 1 <= len(value) <= 240 for value in parent_ids)):
            raise ValueError("work parent selection is invalid")
        ordering = (
            [sa.case((sa.and_(
                delegated_work_runs.c.state.in_(("admitted", "call_started")),
                delegated_work_runs.c.stop_requested_at.is_(None),
            ), 0), else_=1)] if active_first else []
        ) + [delegated_work_runs.c.created_at.desc(), delegated_work_runs.c.work_id.desc()]
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                self._validate_owner_binding(connection, owner_principal_id=owner_principal_id,
                                             principal_binding_id=principal_binding_id)
                selection = sa.select(delegated_work_runs).where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id == self._scope_value.installation_id,
                    delegated_work_runs.c.spec_payload["audience"].astext == "private",
                    delegated_work_runs.c.owner_principal_id == owner_principal_id,
                    delegated_work_runs.c.principal_binding_id == principal_binding_id,
                )
                if parent_ids is not None:
                    selection = selection.where(delegated_work_runs.c.parent_task_id.in_(parent_ids))
                if before_work_id is not None:
                    anchor = connection.execute(selection.where(
                        delegated_work_runs.c.work_id == before_work_id)).mappings().one_or_none()
                    if anchor is None:
                        raise ValueError("work search cursor is unavailable for this owner")
                    self._validated(connection, anchor)
                    selection = selection.where(sa.or_(
                        delegated_work_runs.c.created_at < anchor["created_at"],
                        sa.and_(delegated_work_runs.c.created_at == anchor["created_at"],
                                delegated_work_runs.c.work_id < before_work_id)))
                if query is not None:
                    # Literal substring search: user wildcards never widen the predicate.
                    escaped = query.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
                    pattern = "%" + escaped + "%"
                    selection = selection.where(sa.or_(
                        delegated_work_runs.c.title.ilike(pattern, escape="!"),
                        delegated_work_runs.c.spec_payload["task"].astext.ilike(pattern, escape="!"),
                        delegated_work_runs.c.result_payload["output"].astext.ilike(pattern, escape="!")))
                rows = connection.execute(selection.order_by(*ordering).limit(limit)).mappings().all()
                return tuple(self._validated(connection, row) for row in rows)
            finally:
                transaction.rollback()

    def list_household(
        self, *, owner_principal_id: UUID, principal_binding_id: UUID,
        household_origin, now: datetime, limit: int = 21, active_first: bool = False,
        parent_ids: tuple[str, ...] | None = None,
    ) -> tuple[StoredDelegatedWork, ...]:
        """List only retained work in the caller's currently open household session."""
        from eva.adapters.postgres.household_work_sessions import verify_origin
        from eva.contracts.delegated_work import HouseholdWorkOrigin

        if type(limit) is not int or not 1 <= limit <= 65:
            raise ValueError("delegated work listing limit is invalid")
        now = _whole_second(now, label="household listing time")
        if not isinstance(household_origin, HouseholdWorkOrigin) or self._household_authorizer is None:
            raise DelegatedWorkConflict("household work authority unavailable")
        if type(active_first) is not bool:
            raise ValueError("delegated work active-first flag must be boolean")
        if parent_ids is not None and (not isinstance(parent_ids, tuple) or not 1 <= len(parent_ids) <= 64
                or any(not isinstance(value, str) or not 1 <= len(value) <= 240 for value in parent_ids)):
            raise ValueError("work parent selection is invalid")
        ordering = (
            [sa.case((sa.and_(
                delegated_work_runs.c.state.in_(("admitted", "call_started")),
                delegated_work_runs.c.stop_requested_at.is_(None),
            ), 0), else_=1)] if active_first else []
        ) + [delegated_work_runs.c.created_at.desc(), delegated_work_runs.c.work_id.desc()]
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                self._validate_owner_binding(connection, owner_principal_id=owner_principal_id,
                                             principal_binding_id=principal_binding_id)
                # An empty list must not bypass canonical ingress/session checks.
                verify_origin(connection, self.scope, household_origin, require_open=True, now=now)
                rows = connection.execute(sa.select(delegated_work_runs).where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id == self._scope_value.installation_id,
                    delegated_work_runs.c.owner_principal_id == owner_principal_id,
                    delegated_work_runs.c.principal_binding_id == principal_binding_id,
                    delegated_work_runs.c.spec_payload["audience"].astext == "household",
                    delegated_work_runs.c.spec_payload["origin"].contains(household_origin.destination()),
                    delegated_work_runs.c.retention_until > now,
                    sa.true() if parent_ids is None else delegated_work_runs.c.parent_task_id.in_(parent_ids),
                ).order_by(*ordering).limit(limit)).mappings().all()
                result = []
                for row in rows:
                    work = self._validated(connection, row)
                    # Contract validation checks the full per-work destination hash;
                    # read authorization rechecks policy and the original open session.
                    self._read_scope(work.spec, connection, household_origin)
                    result.append(work)
                return tuple(result)
            finally:
                transaction.rollback()

    def has_pending_or_running_work(self) -> bool:
        """Return only installation activity, never private task contents."""
        with self._engine.connect() as connection:
            self._scope(connection)
            return bool(connection.scalar(sa.select(sa.exists().where(
                delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                delegated_work_runs.c.installation_id == self._scope_value.installation_id,
                sa.or_(
                    delegated_work_runs.c.state == "call_started",
                    sa.and_(delegated_work_runs.c.state == "admitted",
                            delegated_work_runs.c.stop_requested_at.is_(None)),
                ),
            ))))

    def workboard(
        self,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        now: datetime,
    ) -> WorkBoard:
        now = _whole_second(now, label="workboard time")
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                self._validate_owner_binding(
                    connection,
                    owner_principal_id=owner_principal_id,
                    principal_binding_id=principal_binding_id,
                )
                rows = (
                    connection.execute(
                        sa.select(delegated_work_runs)
                        .where(
                            delegated_work_runs.c.tenant_id
                            == self._scope_value.tenant_id,
                            delegated_work_runs.c.installation_id
                            == self._scope_value.installation_id,
                            delegated_work_runs.c.spec_payload["audience"].astext == "private",
                            delegated_work_runs.c.owner_principal_id
                            == owner_principal_id,
                        )
                        .order_by(
                            delegated_work_runs.c.updated_at.desc(),
                            delegated_work_runs.c.work_id,
                        )
                        .limit(65)
                    )
                    .mappings()
                    .all()
                )
                truncated = len(rows) > 64
                cards = [
                    self._card(connection, self._validated(connection, row), now=now)
                    for row in rows[:64]
                ]
            finally:
                transaction.rollback()
        return WorkBoard.model_validate(
            {
                "contract": "EvaWorkBoard/v1",
                "schema_version": 1,
                "read_only": True,
                "status": "ready",
                "cards": cards,
                "truncated": truncated,
                "message": (
                    "Nog geen duurzame subagenttaken."
                    if not cards
                    else "De laatste 64 taken zijn zichtbaar; er zijn meer taken beschikbaar."
                    if truncated
                    else f"{len(cards)} duurzame taak"
                    + (" zichtbaar." if len(cards) == 1 else "en zichtbaar.")
                ),
            }
        )

    def _record_terminal(
        self,
        call: DelegatedWorkCall,
        *,
        state: Literal["succeeded", "failed", "ambiguous", "stopped"],
        result: DelegatedWorkResult | None,
        usage: DelegatedWorkUsage,
        error_code: str | None,
        error_fingerprint: str | None,
        now: datetime,
        failure_report: str | None = None,
    ) -> StoredDelegatedWork:
        now = _whole_second(now, label="result time")
        guard = None
        if isinstance(call.spec, DelegatedInitiativeDigitalWorkSpec) and state == "succeeded":
            try:
                guard = self._native_guard(call.spec)
            except DelegatedWorkConflict:
                pass  # Preserve usage/result, but never complete a stopped/revoked parent.
        result_payload = None if result is None else result.model_dump(mode="json")
        usage_payload = usage.model_dump(mode="json")
        with self._engine.begin() as connection:
            self._scope(connection)
            loaded = self._load_terminal_locked(
                connection,
                work_id=call.work_id,
                missing_message="delegated work call is absent",
            )
            if loaded.state != "call_started":
                if (
                    loaded.state == state
                    and loaded.call_id == call.call_id
                    and loaded.result == result
                    and loaded.usage == usage
                    and loaded.error_code == error_code
                    and loaded.error_fingerprint == error_fingerprint
                    and self._read_failure_report(connection, loaded) == failure_report
                ):
                    return loaded
                raise DelegatedWorkConflict(
                    "delegated work already has another outcome"
                )
            if loaded.call_id != call.call_id or loaded.spec != call.spec:
                raise DelegatedWorkConflict("delegated call authority differs")
            if state == "stopped" and loaded.stop_requested_at is None:
                raise DelegatedWorkConflict("delegated stop was not requested")
            if state != "stopped" and loaded.stop_requested_at is not None:
                raise DelegatedWorkConflict("delegated owner stop already won")
            work_update = connection.execute(
                sa.update(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == call.work_id,
                    delegated_work_runs.c.revision == loaded.revision,
                    delegated_work_runs.c.state == "call_started",
                    delegated_work_runs.c.call_id == call.call_id,
                )
                .values(
                    state=state,
                    result_payload=result_payload,
                    result_sha256=(
                        None
                        if result_payload is None
                        else bytes.fromhex(canonical_sha256(result_payload))
                    ),
                    usage_payload=usage_payload,
                    usage_sha256=bytes.fromhex(canonical_sha256(usage_payload)),
                    error_code=error_code,
                    error_fingerprint=(
                        None
                        if error_fingerprint is None
                        else bytes.fromhex(error_fingerprint)
                    ),
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=now,
                    revision=loaded.revision + 1,
                    updated_at=now,
                )
            )
            if work_update.rowcount != 1:
                raise DelegatedWorkConflict("delegated outcome commit raced")
            if failure_report is not None:
                thread = "work-failure-report:" + canonical_sha256({"work_id": call.work_id})
                insert_initial_timeline_thread(connection, self.scope, thread_id=thread,
                    checkpoint_id=thread + ":saved", occurred_at=now,
                    event_type="WORK_FAILURE_REPORT_SAVED", phase="saved", status="completed",
                    data={"work_id": call.work_id, "call_id": call.call_id,
                          "spec_sha256": call.spec.sha256, "error_code": error_code,
                          "report": failure_report,
                          "report_sha256": hashlib.sha256(failure_report.encode()).hexdigest()})
            self._settle_native(connection, call.spec, state=state, result=result,
                usage=usage, now=now, guard=guard)
            return self._load_locked(connection, call.work_id)

    def _mark_needs_help(
        self,
        connection: sa.Connection,
        work: StoredDelegatedWork,
        *,
        now: datetime,
        error_code: str,
        settle_parent: bool = True,
    ) -> None:
        fingerprint = canonical_sha256({"error": error_code})
        work_result = connection.execute(
            sa.update(delegated_work_runs)
            .where(
                delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                delegated_work_runs.c.installation_id
                == self._scope_value.installation_id,
                delegated_work_runs.c.work_id == work.spec.work_id,
                delegated_work_runs.c.revision == work.revision,
                delegated_work_runs.c.state == "admitted",
            )
            .values(
                state="needs_help",
                error_code=error_code,
                error_fingerprint=bytes.fromhex(fingerprint),
                lease_owner=None,
                lease_expires_at=None,
                completed_at=now,
                revision=work.revision + 1,
                updated_at=now,
            )
        )
        if work_result.rowcount != 1:
            raise DelegatedWorkConflict("delegated work help transition raced")
        if settle_parent:
            self._settle_native(connection, work.spec, state="needs_help", now=now)

    def _validate_owner_binding(
        self,
        connection: sa.Connection,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
    ) -> None:
        binding_owner = connection.scalar(
            sa.select(principal_bindings.c.principal_id)
            .where(
                principal_bindings.c.tenant_id == self._scope_value.tenant_id,
                principal_bindings.c.principal_binding_id == principal_binding_id,
                principal_bindings.c.status == "verified",
            )
            .with_for_update(read=True)
        )
        principal_exists = connection.scalar(
            sa.select(principals.c.principal_id)
            .where(
                principals.c.tenant_id == self._scope_value.tenant_id,
                principals.c.principal_id == owner_principal_id,
                principals.c.status == "active",
            )
            .with_for_update(read=True)
        )
        if binding_owner != owner_principal_id or principal_exists != owner_principal_id:
            raise DelegatedWorkConflict("workboard owner binding is not current")

    def _load_locked(
        self, connection: sa.Connection, work_id: str
    ) -> StoredDelegatedWork:
        row = (
            connection.execute(
                sa.select(delegated_work_runs).where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == work_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DelegatedWorkNotFound("delegated work is absent")
        return self._validated(connection, row)

    def _load_terminal_locked(
        self,
        connection: sa.Connection,
        *,
        work_id: str,
        missing_message: str,
    ) -> StoredDelegatedWork:
        row = (
            connection.execute(
                sa.select(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == work_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DelegatedWorkNotFound(missing_message)
        return self._validated(connection, row)

    def _read_failure_report(self, connection, work):
        if work.state != "failed" or work.error_code not in {"budget_exceeded", "incomplete_work"}:
            return None
        thread = "work-failure-report:" + canonical_sha256({"work_id": work.spec.work_id})
        row = connection.execute(sa.select(operational_events).where(
            operational_events.c.tenant_id == self.scope.tenant_id,
            operational_events.c.installation_id == self.scope.installation_id,
            operational_events.c.thread_id == thread,
            operational_events.c.sequence == 1)).mappings().one_or_none()
        if row is None:
            return None
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
        data = row["payload"].get("data", {})
        report = data.get("report")
        if (not isinstance(work.spec, DelegatedDigitalWorkSpec)
                or row["occurred_at"] != work.completed_at
                or row["event_type"] != "WORK_FAILURE_REPORT_SAVED"
                or bytes(row["payload_sha256"]).hex() != canonical_sha256(row["payload"])
                or data.get("work_id") != work.spec.work_id or data.get("call_id") != work.call_id
                or data.get("spec_sha256") != work.spec.sha256 or data.get("error_code") != work.error_code
                or not isinstance(report, str) or not report.strip() or len(report.encode()) > 65_536
                or data.get("report_sha256") != hashlib.sha256(report.encode()).hexdigest()):
            raise DelegatedWorkStoreError("stored failure report binding differs")
        return report

    def _validated(
        self,
        connection: sa.Connection,
        row: sa.RowMapping,
    ) -> StoredDelegatedWork:
        work = self.validate_stored_row(row)
        report = self._read_failure_report(connection, work)
        if report is not None and self._failure_report_clock() < work.retention_until:
            from dataclasses import replace
            work = replace(work, failure_report=report)
        return work

    @staticmethod
    def validate_stored_row(row: sa.RowMapping) -> StoredDelegatedWork:
        """Validate persisted work without loading optional runtime reports."""
        try:
            spec = load_delegated_work_spec(row.spec_payload)
            if bytes(row.spec_sha256).hex() != canonical_sha256(row.spec_payload):
                raise ValueError("spec hash differs")
            result = (
                None
                if row.result_payload is None
                else load_delegated_work_result(row.result_payload)
            )
            if result is not None:
                _validate_result_version(spec, result)
            usage = (
                None
                if row.usage_payload is None
                else DelegatedWorkUsage.model_validate(row.usage_payload)
            )
            if (row.result_payload is None) != (row.result_sha256 is None):
                raise ValueError("result hash presence differs")
            if row.result_payload is not None and bytes(
                row.result_sha256
            ).hex() != canonical_sha256(row.result_payload):
                raise ValueError("result hash differs")
            if (row.usage_payload is None) != (row.usage_sha256 is None):
                raise ValueError("usage hash presence differs")
            if row.usage_payload is not None and bytes(
                row.usage_sha256
            ).hex() != canonical_sha256(row.usage_payload):
                raise ValueError("usage hash differs")
            if (
                row.work_id != spec.work_id
                or row.storage_episode_id != UUID(spec.episode_id)
                or row.generation != spec.generation
                or str(row.owner_principal_id) != spec.owner_principal_id
                or str(row.principal_binding_id) != spec.principal_binding_id
                or row.parent_task_id != spec.parent_task_id
                or row.worker_name != spec.worker_name
                or row.deadline != _parse_timestamp(spec.deadline)
                or (result is not None and result.spec_sha256 != spec.sha256)
                or (result is not None and result.call_id != row.call_id)
                or (result is not None and usage != result.usage)
                or (row.lease_owner is None) != (row.lease_expires_at is None)
                or (row.state not in {"admitted", "call_started"}
                    and row.lease_owner is not None)
            ):
                raise ValueError("delegated work bindings differ")
        except (KeyError, TypeError, ValueError, sa.exc.NoResultFound) as error:
            raise DelegatedWorkStoreError("stored delegated work is corrupt") from error
        work = StoredDelegatedWork(
            spec=spec,
            storage_episode_id=row.storage_episode_id,
            title=row.title,
            summary=row.summary,
            worker_role=row.worker_role,
            state=row.state,
            claim_count=int(row.claim_count),
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            call_id=row.call_id,
            result=result,
            usage=usage,
            error_code=row.error_code,
            error_fingerprint=(
                None
                if row.error_fingerprint is None
                else bytes(row.error_fingerprint).hex()
            ),
            revision=int(row.revision),
            created_at=row.created_at,
            updated_at=row.updated_at,
            call_started_at=row.call_started_at,
            completed_at=row.completed_at,
            stop_requested_at=row.stop_requested_at,
            retention_policy_id=row.retention_policy_id,
            retention_until=row.retention_until,
        )
        return work

    def _card(
        self, connection: sa.Connection, work: StoredDelegatedWork, *, now: datetime
    ) -> dict[str, object]:
        del connection
        terminal_at = work.completed_at or now
        duration_seconds = max(
            0, min(31_536_000, int((terminal_at - work.created_at).total_seconds()))
        )
        state = work.state
        if state == "admitted":
            active = work.lease_expires_at is not None and work.lease_expires_at >= now
            column = "doing" if active else "todo"
            health = "active" if active else "waiting"
            result_status = "not_received"
            reason = (
                "De worker heeft de taak opgepakt."
                if active
                else "De taak wacht op een beschikbare worker."
            )
        elif state == "call_started":
            active = work.lease_expires_at is not None and work.lease_expires_at >= now
            column = "doing" if active else "attention"
            health = "active" if active else "contact_lost"
            result_status = "not_received" if active else "ambiguous"
            if work.stop_requested_at is not None:
                if active:
                    reason = (
                        "De stopopdracht is bewaard en wordt door de actieve worker "
                        "uitgevoerd."
                    )
                else:
                    reason = (
                        "De stopopdracht is bewaard, maar het workercontact is verlopen. "
                        "De beëindiging is niet bevestigd en Eva start de modelcall niet "
                        "opnieuw."
                    )
            else:
                reason = (
                    "De subagent werkt aan de afgesproken taak."
                    if active
                    else "De modelcall begon, maar de worker heeft geen bevestigde uitkomst bewaard."
                )
        elif state == "succeeded":
            column, health, result_status = "review", "finished", "awaiting_review"
            reason = "Het resultaat is ontvangen en wacht op controle."
        elif state == "verified":
            column, health, result_status = "done", "finished", "approved"
            reason = "Het ontvangen resultaat is gecontroleerd."
        elif state in {"failed", "needs_help"}:
            column, health, result_status = "attention", "stopped", "failed"
            reason = "De taak is veilig gestopt en vraagt aandacht."
        elif state == "ambiguous":
            column, result_status = "attention", "ambiguous"
            if work.error_code == "cancelled":
                health = "stopped"
                reason = (
                    "De worker is tijdens de modelrun netjes gestopt. Er is geen "
                    "bevestigd resultaat; Eva start deze modelrun niet opnieuw."
                )
            else:
                health = "contact_lost"
                reason = (
                    "De uitkomst is onzeker; Eva voert de modelcall niet opnieuw uit."
                )
        elif state == "stopped":
            column, health, result_status = "done", "stopped", "stopped"
            reason = "De taak is gestopt voordat een resultaat is goedgekeurd."
        else:
            raise DelegatedWorkStoreError("stored delegated work state is unknown")
        usage = work.usage or DelegatedWorkUsage.unmeasured()
        age = max(0, int((now - work.updated_at).total_seconds()))
        changed = (
            "just_now"
            if age < 60
            else "minutes_ago"
            if age < 3_600
            else "hours_ago"
            if age < 86_400
            else "days_ago"
            if age < 31_536_000
            else "unknown"
        )
        return {
            "contract": "EvaWorkBoardCard/v1",
            "schema_version": 1,
            "title": work.title,
            "summary": work.summary,
            "column": column,
            "worker_name": work.spec.worker_name,
            "worker_role": work.worker_role,
            "worker_health": health,
            "attempt": work.claim_count,
            "maximum_attempts": work.spec.grant.maximum_claims_before_call_start,
            "deadline": work.spec.deadline,
            "duration_seconds": duration_seconds,
            "overdue": now > _parse_timestamp(work.spec.deadline)
            and state not in _TERMINAL_STATES,
            "usage": {
                "contract": "EvaWorkBoardUsage/v1",
                "schema_version": 1,
                "measurement": usage.measurement,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "cost_usd_micros": usage.cost_usd_micros,
            },
            "result_status": result_status,
            "reason": reason,
            "changed": changed,
        }


def _bounded_text(value: str, *, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{label} is invalid")
    if len(value) > maximum:
        raise ValueError(f"{label} is too long")
    return value


def _validate_result_version(spec, result):
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec, DelegatedDigitalWorkResult
    if isinstance(spec, DelegatedDigitalWorkSpec) != isinstance(result, DelegatedDigitalWorkResult):
        raise DelegatedWorkConflict("delegated result version differs from its work")
    if result.spec_sha256 != spec.sha256:
        raise DelegatedWorkConflict("delegated result specification differs")


def _validate_usage(
    spec: DelegatedWorkSpec,
    usage: DelegatedWorkUsage,
    *,
    successful: bool,
) -> None:
    grant = spec.grant
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
    digital = isinstance(spec, DelegatedDigitalWorkSpec)
    if successful and usage.measurement != "measured":
        # Existing model accounting measures token/action counters, not the
        # owner's subscription credits. Unknown cost must remain unknown.
        counters = (usage.completed_model_responses, usage.tool_calls,
                    usage.input_tokens, usage.output_tokens, usage.total_tokens)
        if not (digital and usage.measurement == "partial"
                and all(value is not None for value in counters)
                and usage.cost_usd_micros is None
                and grant.maximum_cost_usd_micros is None):
            raise DelegatedWorkConflict("successful delegated usage must be measured")
    limits = (
        (
            usage.completed_model_responses,
            grant.maximum_completed_model_responses,
            "model response",
        ),
        (usage.tool_calls, grant.maximum_tool_calls if isinstance(spec, DelegatedResearchSpec) or digital else 0, "tool call"),
        (usage.input_tokens, grant.maximum_input_tokens, "input token"),
        (usage.output_tokens, grant.maximum_output_tokens, "output token"),
        (usage.total_tokens, grant.maximum_total_tokens, "total token"),
        (usage.cost_usd_micros, grant.maximum_cost_usd_micros, "cost"),
    )
    for actual, maximum, label in limits:
        if actual is not None and maximum is not None and actual > maximum and (
                successful or not isinstance(spec, DelegatedResearchSpec) and not digital):
            raise DelegatedWorkConflict(f"delegated {label} budget exceeded")


def _whole_second(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
        raise ValueError(f"{label} must be an aware whole-second timestamp")
    return value.astimezone(UTC)


__all__ = [
    "DelegatedWorkAdmission",
    "DelegatedWorkCall",
    "DelegatedWorkClaim",
    "DelegatedWorkConflict",
    "DelegatedWorkNotFound",
    "DelegatedWorkNotResumable",
    "DelegatedWorkStoreError",
    "PostgresDelegatedWorkStore",
    "StoredDelegatedWork",
]
