"""Durable PostgreSQL handoff and at-most-once fence for bounded child work."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.current_catalog import delegated_work_runs, metadata
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import (
    DelegatedWorkResult,
    DelegatedWorkSpec,
    DelegatedWorkUsage,
)
from eva.contracts.lifecycle import AttentionWakeV2
from eva.contracts.workboard import WorkBoard

attention_items = metadata.tables["eva_core.attention_items"]
episode_generations = metadata.tables["eva_core.episode_generations"]
episodes = metadata.tables["eva_core.episodes"]
principal_bindings = metadata.tables["eva_core.principal_bindings"]
principals = metadata.tables["eva_core.principals"]

_TERMINAL_STATES = frozenset(
    {"succeeded", "verified", "failed", "ambiguous", "needs_help", "stopped"}
)


class DelegatedWorkStoreError(RuntimeError):
    """The requested durable transition is absent, stale or corrupt."""


class DelegatedWorkConflict(DelegatedWorkStoreError):
    """Existing authority differs from the requested immutable bytes."""


class DelegatedWorkNotFound(DelegatedWorkStoreError):
    """No work exists in the caller's fixed scope."""


@dataclass(frozen=True, slots=True)
class StoredDelegatedWork:
    spec: DelegatedWorkSpec
    attention: AttentionWakeV2
    attention_revision: int
    storage_episode_id: UUID
    title: str
    summary: str
    worker_role: str
    state: str
    claim_count: int
    call_id: str | None
    result: DelegatedWorkResult | None
    usage: DelegatedWorkUsage | None
    error_code: str | None
    error_fingerprint: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    call_started_at: datetime | None
    completed_at: datetime | None
    retention_policy_id: str
    retention_until: datetime


@dataclass(frozen=True, slots=True)
class DelegatedWorkAdmission:
    disposition: Literal["inserted", "already_present"]
    work: StoredDelegatedWork


@dataclass(frozen=True, slots=True)
class DelegatedWorkClaim:
    work_id: str
    attention_id: UUID
    lease_owner: str
    attention_revision: int
    work_revision: int
    spec: DelegatedWorkSpec


@dataclass(frozen=True, slots=True)
class DelegatedWorkCall:
    work_id: str
    attention_id: UUID
    call_id: str
    spec: DelegatedWorkSpec


class PostgresDelegatedWorkStore:
    """Keep handoff, lease, call fence and result in one scoped authority."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("delegated work requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role

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
    ) -> DelegatedWorkAdmission:
        # These three values are trusted operator-display labels, not task text.
        # The local cockpit remains unauthenticated until its own product gate.
        title = _bounded_text(title, maximum=120, label="work title")
        summary = _bounded_text(summary, maximum=240, label="work summary")
        worker_role = _bounded_text(worker_role, maximum=80, label="worker role")
        retention_policy_id = _bounded_text(
            retention_policy_id, maximum=192, label="retention policy"
        )
        occurred_at = _whole_second(occurred_at, label="occurred_at")
        retention_until = _whole_second(
            retention_until, label="retention_until"
        )
        deadline = _parse_timestamp(spec.deadline)
        if retention_until < deadline or occurred_at > deadline:
            raise DelegatedWorkConflict("delegated work retention or deadline is invalid")
        try:
            storage_episode_id = UUID(spec.episode_id)
            owner_principal_id = UUID(spec.owner_principal_id)
            principal_binding_id = UUID(spec.principal_binding_id)
        except ValueError as error:
            raise DelegatedWorkConflict("delegated work database identifiers are invalid") from error

        attention_id = uuid5(
            self._scope_value.installation_id, f"delegated-work:{spec.work_id}"
        )
        spec_payload = spec.model_dump(mode="json")
        spec_sha256 = canonical_sha256(spec_payload)
        with self._engine.begin() as connection:
            self._scope(connection)
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"delegated-work:{self._scope_value.tenant_id}:"
                        f"{self._scope_value.installation_id}:{spec.work_id}"
                    )
                },
            )
            existing = connection.execute(
                sa.select(delegated_work_runs).where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == spec.work_id,
                )
            ).mappings().one_or_none()
            if existing is not None:
                loaded = self._validated(connection, existing)
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

            self._validate_authority(
                connection,
                storage_episode_id=storage_episode_id,
                generation=spec.generation,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
            )
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"delegated-attention-ordinal:{self._scope_value.tenant_id}:"
                        f"{self._scope_value.installation_id}:{storage_episode_id}:"
                        f"{spec.generation}"
                    )
                },
            )
            ordinal = int(
                connection.scalar(
                    sa.select(sa.func.coalesce(sa.func.max(attention_items.c.ordinal), 0))
                    .where(
                        attention_items.c.tenant_id == self._scope_value.tenant_id,
                        attention_items.c.installation_id
                        == self._scope_value.installation_id,
                        attention_items.c.episode_id == storage_episode_id,
                        attention_items.c.generation == spec.generation,
                        attention_items.c.kind == "delegated_work",
                    )
                )
                or 0
            ) + 1
            if ordinal > 1_000:
                raise DelegatedWorkConflict("delegated work attention capacity is full")
            attention = AttentionWakeV2(
                contract="AttentionWake/v2",
                schema_version=2,
                attention_id=str(attention_id),
                episode_id=spec.episode_id,
                generation=spec.generation,
                kind="delegated_work",
                state="pending",
                reason="voer een begrensde subagenttaak uit",
                not_before=_format_timestamp(occurred_at),
                deadline=spec.deadline,
                lease_owner=None,
                lease_expires_at=None,
                ordinal=ordinal,
            )
            attention_payload = attention.model_dump(mode="json")
            connection.execute(
                sa.insert(attention_items).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    attention_id=attention_id,
                    episode_id=storage_episode_id,
                    generation=spec.generation,
                    commitment_id=None,
                    kind="delegated_work",
                    state="pending",
                    reason=attention.reason,
                    not_before=occurred_at,
                    deadline=deadline,
                    lease_owner=None,
                    lease_expires_at=None,
                    ordinal=ordinal,
                    payload=attention_payload,
                    payload_sha256=bytes.fromhex(canonical_sha256(attention_payload)),
                    revision=1,
                    retention_policy_id=retention_policy_id,
                    retention_until=retention_until,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
            )
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
            row = connection.execute(
                sa.select(delegated_work_runs).where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == spec.work_id,
                )
            ).mappings().one()
            loaded = self._validated(connection, row)
        return DelegatedWorkAdmission("inserted", loaded)

    def claim_next(
        self,
        *,
        worker_id: str,
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
            row = connection.execute(
                sa.select(delegated_work_runs)
                .join(
                    attention_items,
                    sa.and_(
                        attention_items.c.tenant_id == delegated_work_runs.c.tenant_id,
                        attention_items.c.installation_id
                        == delegated_work_runs.c.installation_id,
                        attention_items.c.attention_id
                        == delegated_work_runs.c.attention_id,
                    ),
                )
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.state == "admitted",
                    delegated_work_runs.c.deadline >= now,
                    sa.or_(
                        sa.and_(
                            attention_items.c.state == "pending",
                            attention_items.c.not_before <= now,
                        ),
                        sa.and_(
                            attention_items.c.state == "claimed",
                            attention_items.c.lease_expires_at < now,
                        ),
                    ),
                )
                .order_by(delegated_work_runs.c.created_at, delegated_work_runs.c.work_id)
                .with_for_update(
                    of=(delegated_work_runs, attention_items), skip_locked=True
                )
                .limit(1)
            ).mappings().one_or_none()
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
                self._validate_authority(
                    connection,
                    storage_episode_id=loaded.storage_episode_id,
                    generation=loaded.spec.generation,
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

            attention = loaded.attention.model_copy(
                update={
                    "state": "claimed",
                    "lease_owner": lease_owner,
                    "lease_expires_at": _format_timestamp(lease_expires_at),
                }
            )
            attention_payload = attention.model_dump(mode="json")
            attention_result = connection.execute(
                sa.update(attention_items)
                .where(
                    attention_items.c.tenant_id == self._scope_value.tenant_id,
                    attention_items.c.installation_id
                    == self._scope_value.installation_id,
                    attention_items.c.attention_id == UUID(loaded.attention.attention_id),
                    attention_items.c.revision == loaded.attention_revision,
                    attention_items.c.state.in_(("pending", "claimed")),
                )
                .values(
                    state="claimed",
                    lease_owner=lease_owner,
                    lease_expires_at=lease_expires_at,
                    payload=attention_payload,
                    payload_sha256=bytes.fromhex(canonical_sha256(attention_payload)),
                    revision=attention_items.c.revision + 1,
                    updated_at=now,
                )
            )
            if attention_result.rowcount != 1:
                raise DelegatedWorkConflict("delegated attention claim raced")
            work_result = connection.execute(
                sa.update(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == loaded.spec.work_id,
                    delegated_work_runs.c.revision == loaded.revision,
                    delegated_work_runs.c.state == "admitted",
                )
                .values(
                    claim_count=next_claim,
                    revision=loaded.revision + 1,
                    updated_at=now,
                )
            )
            if work_result.rowcount != 1:
                raise DelegatedWorkConflict("delegated work claim raced")
            attention_revision = int(
                connection.scalar(
                    sa.select(attention_items.c.revision).where(
                        attention_items.c.tenant_id == self._scope_value.tenant_id,
                        attention_items.c.installation_id
                        == self._scope_value.installation_id,
                        attention_items.c.attention_id
                        == UUID(loaded.attention.attention_id),
                    )
                )
            )
            return DelegatedWorkClaim(
                work_id=loaded.spec.work_id,
                attention_id=UUID(loaded.attention.attention_id),
                lease_owner=lease_owner,
                attention_revision=attention_revision,
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
            attention = connection.execute(
                sa.select(attention_items)
                .where(
                    attention_items.c.tenant_id == self._scope_value.tenant_id,
                    attention_items.c.installation_id
                    == self._scope_value.installation_id,
                    attention_items.c.attention_id == claim.attention_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            row = connection.execute(
                sa.select(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == claim.work_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if attention is None or row is None:
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
                self._validate_authority(
                    connection,
                    storage_episode_id=loaded.storage_episode_id,
                    generation=loaded.spec.generation,
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
                or int(attention.revision) != claim.attention_revision
                or attention.state != "claimed"
                or attention.lease_owner != claim.lease_owner
                or attention.lease_expires_at is None
                or attention.lease_expires_at < now
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
    ) -> StoredDelegatedWork:
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
        )

    def verify_result(
        self,
        work_id: str,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        now: datetime,
    ) -> StoredDelegatedWork:
        now = _whole_second(now, label="verification time")
        with self._engine.begin() as connection:
            self._scope(connection)
            self._validate_owner_binding(
                connection,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
            )
            row = connection.execute(
                sa.select(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == work_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise DelegatedWorkNotFound("delegated work is absent")
            loaded = self._validated(connection, row)
            if (
                loaded.spec.owner_principal_id != str(owner_principal_id)
                or loaded.spec.principal_binding_id != str(principal_binding_id)
            ):
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

    def load(
        self,
        work_id: str,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
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
                if (
                    loaded.spec.owner_principal_id != str(owner_principal_id)
                    or loaded.spec.principal_binding_id != str(principal_binding_id)
                ):
                    raise DelegatedWorkNotFound("delegated work is absent")
                return loaded
            finally:
                transaction.rollback()

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
                rows = connection.execute(
                    sa.select(delegated_work_runs)
                    .where(
                        delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                        delegated_work_runs.c.installation_id
                        == self._scope_value.installation_id,
                        delegated_work_runs.c.owner_principal_id == owner_principal_id,
                    )
                    .order_by(
                        delegated_work_runs.c.updated_at.desc(),
                        delegated_work_runs.c.work_id,
                    )
                    .limit(65)
                ).mappings().all()
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
        state: Literal["succeeded", "failed", "ambiguous"],
        result: DelegatedWorkResult | None,
        usage: DelegatedWorkUsage,
        error_code: str | None,
        error_fingerprint: str | None,
        now: datetime,
    ) -> StoredDelegatedWork:
        now = _whole_second(now, label="result time")
        result_payload = None if result is None else result.model_dump(mode="json")
        usage_payload = usage.model_dump(mode="json")
        with self._engine.begin() as connection:
            self._scope(connection)
            row = connection.execute(
                sa.select(delegated_work_runs)
                .where(
                    delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                    delegated_work_runs.c.installation_id
                    == self._scope_value.installation_id,
                    delegated_work_runs.c.work_id == call.work_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise DelegatedWorkNotFound("delegated work call is absent")
            loaded = self._validated(connection, row)
            if loaded.state != "call_started":
                if (
                    loaded.state == state
                    and loaded.call_id == call.call_id
                    and loaded.result == result
                    and loaded.usage == usage
                    and loaded.error_code == error_code
                    and loaded.error_fingerprint == error_fingerprint
                ):
                    return loaded
                raise DelegatedWorkConflict("delegated work already has another outcome")
            if loaded.call_id != call.call_id or loaded.spec != call.spec:
                raise DelegatedWorkConflict("delegated call authority differs")
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
                    completed_at=now,
                    revision=loaded.revision + 1,
                    updated_at=now,
                )
            )
            if work_update.rowcount != 1:
                raise DelegatedWorkConflict("delegated outcome commit raced")
            attention = loaded.attention.model_copy(
                update={"state": "completed", "lease_owner": None, "lease_expires_at": None}
            )
            payload = attention.model_dump(mode="json")
            attention_update = connection.execute(
                sa.update(attention_items)
                .where(
                    attention_items.c.tenant_id == self._scope_value.tenant_id,
                    attention_items.c.installation_id
                    == self._scope_value.installation_id,
                    attention_items.c.attention_id == call.attention_id,
                    attention_items.c.state == "claimed",
                    attention_items.c.lease_owner == loaded.attention.lease_owner,
                )
                .values(
                    state="completed",
                    lease_owner=None,
                    lease_expires_at=None,
                    payload=payload,
                    payload_sha256=bytes.fromhex(canonical_sha256(payload)),
                    revision=attention_items.c.revision + 1,
                    updated_at=now,
                )
            )
            if attention_update.rowcount != 1:
                raise DelegatedWorkConflict("delegated attention outcome raced")
            return self._load_locked(connection, call.work_id)

    def _mark_needs_help(
        self,
        connection: sa.Connection,
        work: StoredDelegatedWork,
        *,
        now: datetime,
        error_code: str,
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
                completed_at=now,
                revision=work.revision + 1,
                updated_at=now,
            )
        )
        attention = work.attention.model_copy(
            update={"state": "dead_letter", "lease_owner": None, "lease_expires_at": None}
        )
        payload = attention.model_dump(mode="json")
        if work_result.rowcount != 1:
            raise DelegatedWorkConflict("delegated work help transition raced")
        attention_result = connection.execute(
            sa.update(attention_items)
            .where(
                attention_items.c.tenant_id == self._scope_value.tenant_id,
                attention_items.c.installation_id == self._scope_value.installation_id,
                attention_items.c.attention_id == UUID(work.attention.attention_id),
            )
            .values(
                state="dead_letter",
                lease_owner=None,
                lease_expires_at=None,
                payload=payload,
                payload_sha256=bytes.fromhex(canonical_sha256(payload)),
                revision=attention_items.c.revision + 1,
                updated_at=now,
            )
        )
        if attention_result.rowcount != 1:
            raise DelegatedWorkConflict("delegated attention help transition raced")

    def _validate_authority(
        self,
        connection: sa.Connection,
        *,
        storage_episode_id: UUID,
        generation: int,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
    ) -> None:
        generation_exists = connection.scalar(
            sa.select(episode_generations.c.episode_id)
            .where(
                episode_generations.c.tenant_id == self._scope_value.tenant_id,
                episode_generations.c.installation_id
                == self._scope_value.installation_id,
                episode_generations.c.episode_id == storage_episode_id,
                episode_generations.c.generation == generation,
                episode_generations.c.state == "current",
            )
            .with_for_update(read=True)
        )
        episode_owner = connection.scalar(
            sa.select(episodes.c.principal_id)
            .where(
                episodes.c.tenant_id == self._scope_value.tenant_id,
                episodes.c.installation_id == self._scope_value.installation_id,
                episodes.c.episode_id == storage_episode_id,
                episodes.c.current_generation == generation,
                episodes.c.state.not_in(("completed", "cancelled")),
            )
            .with_for_update(read=True)
        )
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
        if (
            not generation_exists
            or episode_owner != owner_principal_id
            or binding_owner != owner_principal_id
            or principal_exists != owner_principal_id
        ):
            raise DelegatedWorkConflict("delegated work owner authority is not current")

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
                principal_bindings.c.principal_id == owner_principal_id,
                principal_bindings.c.status == "verified",
            )
            .with_for_update(read=True)
        )
        if binding_owner != owner_principal_id:
            raise DelegatedWorkStoreError("workboard owner binding is not current")

    def _load_locked(
        self, connection: sa.Connection, work_id: str
    ) -> StoredDelegatedWork:
        row = connection.execute(
            sa.select(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id == self._scope_value.tenant_id,
                delegated_work_runs.c.installation_id
                == self._scope_value.installation_id,
                delegated_work_runs.c.work_id == work_id,
            )
        ).mappings().one_or_none()
        if row is None:
            raise DelegatedWorkNotFound("delegated work is absent")
        return self._validated(connection, row)

    def _validated(
        self, connection: sa.Connection, row: sa.RowMapping
    ) -> StoredDelegatedWork:
        try:
            spec = DelegatedWorkSpec.model_validate(row.spec_payload)
            if bytes(row.spec_sha256).hex() != canonical_sha256(row.spec_payload):
                raise ValueError("spec hash differs")
            result = (
                None
                if row.result_payload is None
                else DelegatedWorkResult.model_validate(row.result_payload)
            )
            usage = (
                None
                if row.usage_payload is None
                else DelegatedWorkUsage.model_validate(row.usage_payload)
            )
            if (row.result_payload is None) != (row.result_sha256 is None):
                raise ValueError("result hash presence differs")
            if row.result_payload is not None and bytes(row.result_sha256).hex() != canonical_sha256(
                row.result_payload
            ):
                raise ValueError("result hash differs")
            if (row.usage_payload is None) != (row.usage_sha256 is None):
                raise ValueError("usage hash presence differs")
            if row.usage_payload is not None and bytes(row.usage_sha256).hex() != canonical_sha256(
                row.usage_payload
            ):
                raise ValueError("usage hash differs")
            attention_row = connection.execute(
                sa.select(attention_items).where(
                    attention_items.c.tenant_id == self._scope_value.tenant_id,
                    attention_items.c.installation_id
                    == self._scope_value.installation_id,
                    attention_items.c.attention_id == row.attention_id,
                )
            ).mappings().one()
            attention = AttentionWakeV2.model_validate(attention_row.payload)
            if bytes(attention_row.payload_sha256).hex() != canonical_sha256(
                attention_row.payload
            ):
                raise ValueError("attention hash differs")
            if (
                attention.attention_id != str(row.attention_id)
                or attention.kind != "delegated_work"
                or attention.episode_id != spec.episode_id
                or attention.generation != spec.generation
                or attention.state != attention_row.state
                or attention.lease_owner != attention_row.lease_owner
                or _parse_optional_timestamp(attention.lease_expires_at)
                != attention_row.lease_expires_at
                or row.work_id != spec.work_id
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
                or attention_row.retention_policy_id != row.retention_policy_id
                or attention_row.retention_until != row.retention_until
                or not _states_agree(row.state, attention_row.state)
            ):
                raise ValueError("delegated work bindings differ")
        except (KeyError, TypeError, ValueError, sa.exc.NoResultFound) as error:
            raise DelegatedWorkStoreError("stored delegated work is corrupt") from error
        return StoredDelegatedWork(
            spec=spec,
            attention=attention,
            attention_revision=int(attention_row.revision),
            storage_episode_id=row.storage_episode_id,
            title=row.title,
            summary=row.summary,
            worker_role=row.worker_role,
            state=row.state,
            claim_count=int(row.claim_count),
            call_id=row.call_id,
            result=result,
            usage=usage,
            error_code=row.error_code,
            error_fingerprint=(
                None if row.error_fingerprint is None else bytes(row.error_fingerprint).hex()
            ),
            revision=int(row.revision),
            created_at=row.created_at,
            updated_at=row.updated_at,
            call_started_at=row.call_started_at,
            completed_at=row.completed_at,
            retention_policy_id=row.retention_policy_id,
            retention_until=row.retention_until,
        )

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
            active = work.attention.state == "claimed" and _parse_optional_timestamp(
                work.attention.lease_expires_at
            ) >= now
            column = "doing" if active else "todo"
            health = "active" if active else "waiting"
            result_status = "not_received"
            reason = (
                "De worker heeft de taak opgepakt."
                if active
                else "De taak wacht op een beschikbare worker."
            )
        elif state == "call_started":
            lease = _parse_optional_timestamp(work.attention.lease_expires_at)
            active = lease is not None and lease >= now
            column = "doing" if active else "attention"
            health = "active" if active else "contact_lost"
            result_status = "not_received" if active else "ambiguous"
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
            column, health, result_status = "attention", "contact_lost", "ambiguous"
            reason = "De uitkomst is onzeker; Eva voert de modelcall niet opnieuw uit."
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

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def _bounded_text(value: str, *, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{label} is invalid")
    if len(value) > maximum:
        raise ValueError(f"{label} is too long")
    return value


def _validate_usage(
    spec: DelegatedWorkSpec,
    usage: DelegatedWorkUsage,
    *,
    successful: bool,
) -> None:
    grant = spec.grant
    if successful and usage.measurement != "measured":
        raise DelegatedWorkConflict("successful delegated usage must be measured")
    limits = (
        (
            usage.completed_model_responses,
            grant.maximum_completed_model_responses,
            "model response",
        ),
        (usage.tool_calls, 0, "tool call"),
        (usage.input_tokens, grant.maximum_input_tokens, "input token"),
        (usage.output_tokens, grant.maximum_output_tokens, "output token"),
        (usage.total_tokens, grant.maximum_total_tokens, "total token"),
        (usage.cost_usd_micros, grant.maximum_cost_usd_micros, "cost"),
    )
    for actual, maximum, label in limits:
        if actual is not None and actual > maximum:
            raise DelegatedWorkConflict(f"delegated {label} budget exceeded")


def _states_agree(work_state: str, attention_state: str) -> bool:
    allowed = {
        "admitted": {"pending", "claimed"},
        "call_started": {"claimed"},
        "succeeded": {"completed"},
        "verified": {"completed"},
        "failed": {"completed"},
        "ambiguous": {"completed"},
        "needs_help": {"dead_letter"},
        "stopped": {"completed"},
    }
    return attention_state in allowed.get(work_state, set())


def _whole_second(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
        raise ValueError(f"{label} must be an aware whole-second timestamp")
    return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    return None if value is None else _parse_timestamp(value)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "DelegatedWorkAdmission",
    "DelegatedWorkCall",
    "DelegatedWorkClaim",
    "DelegatedWorkConflict",
    "DelegatedWorkNotFound",
    "DelegatedWorkStoreError",
    "PostgresDelegatedWorkStore",
    "StoredDelegatedWork",
]
