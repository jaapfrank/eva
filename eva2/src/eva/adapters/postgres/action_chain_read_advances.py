"""Atomically turn one durable read observation into dependency progress."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from pydantic import ValidationError
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_leases import (
    _locked_attention,
    write_action_chain_attention,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    plan_steps,
    read_intents,
)
from eva.adapters.postgres.action_chain_reads import ActionChainReadConflict
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_events,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import action_chain_thread_id
from eva.contracts.execution import ReadAttempt, ReadObservation


read_attempts = metadata.tables["eva_effects.read_attempts"]
read_observations = metadata.tables["eva_effects.read_observations"]


@dataclass(frozen=True, slots=True)
class AdvancedActionChainRead:
    disposition: Literal["advanced", "already_advanced"]
    read_step_id: UUID
    released_step_ids: tuple[UUID, ...]
    outcome_ref: str


class PostgresActionChainReadAdvancer:
    """Release dependencies only after one reload-valid observed read."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain read advances require PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def advance(
        self,
        read_intent_id: UUID,
        *,
        now: datetime,
    ) -> AdvancedActionChainRead:
        _require_utc_second(now, "now")
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(read_intents).where(
                    read_intents.c.tenant_id == self._scope_value.tenant_id,
                    read_intents.c.installation_id
                    == self._scope_value.installation_id,
                    read_intents.c.read_intent_id == read_intent_id,
                )
            ).one_or_none()
            if intent_row is None:
                raise ActionChainReadConflict("read advance intent is absent in scope")

            seed_step = connection.execute(
                sa.select(plan_steps.c.plan_id).where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == intent_row.plan_step_id,
                    plan_steps.c.read_intent_id == read_intent_id,
                )
            ).one_or_none()
            if seed_step is None:
                raise ActionChainReadConflict("read advance step binding differs")
            plan_id = seed_step.plan_id

            attention_id = uuid5(plan_id, "continuation:1")
            attention_row = _locked_attention(
                connection,
                self._scope_value,
                attention_id,
            )
            rows = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_id == plan_id,
                )
                .order_by(plan_steps.c.plan_step_id)
                .with_for_update()
            ).all()
            if attention_row is None:
                raise ActionChainReadConflict("read advance attention is absent")
            by_id = {row.plan_step_id: row for row in rows}
            read_row = by_id.get(intent_row.plan_step_id)
            if read_row is None or read_row.read_intent_id != read_intent_id:
                raise ActionChainReadConflict("read advance step binding differs")

            chain = self._ledger.load_in_transaction(connection, plan_id)
            if read_row.state == "succeeded":
                if now < read_row.updated_at:
                    raise ActionChainReadConflict("read advance replay predates storage")
                return self._replay(
                    connection,
                    root_thread_id=action_chain_thread_id(chain.plan),
                    read_step_id=read_row.plan_step_id,
                    read_intent_id=read_intent_id,
                    outcome_ref=read_row.outcome_ref,
                )
            if (
                read_row.effect_class != "read"
                or read_row.state != "running"
                or read_row.effect_intent_id is not None
                or read_row.outcome_ref is not None
                or attention_row.state != "waiting"
                or attention_row.lease_owner is not None
                or attention_row.lease_expires_at is not None
            ):
                raise ActionChainReadConflict("read is not ready to advance")

            attempt_id = uuid5(read_intent_id, "read-attempt:1")
            observation_id = uuid5(attempt_id, "observation:v1")
            attempt_row = connection.execute(
                sa.select(read_attempts)
                .where(
                    read_attempts.c.tenant_id == self._scope_value.tenant_id,
                    read_attempts.c.installation_id
                    == self._scope_value.installation_id,
                    read_attempts.c.read_attempt_id == attempt_id,
                    read_attempts.c.read_intent_id == read_intent_id,
                    read_attempts.c.ordinal == 1,
                )
                .with_for_update(read=True)
            ).one_or_none()
            observation_row = connection.execute(
                sa.select(read_observations)
                .where(
                    read_observations.c.tenant_id == self._scope_value.tenant_id,
                    read_observations.c.installation_id
                    == self._scope_value.installation_id,
                    read_observations.c.observation_id == observation_id,
                    read_observations.c.read_attempt_id == attempt_id,
                    read_observations.c.read_intent_id == read_intent_id,
                )
            ).one_or_none()
            try:
                attempt = ReadAttempt.model_validate(
                    None if attempt_row is None else attempt_row.payload
                )
                observation = ReadObservation.model_validate(
                    None if observation_row is None else observation_row.payload
                )
            except ValidationError as error:
                raise ActionChainReadConflict(
                    "read advance proof payload is absent or invalid"
                ) from error
            if (
                attempt.state != "observed"
                or attempt.read_attempt_id != str(attempt_id)
                or observation.observation_id != str(observation_id)
                or observation.read_attempt_id != str(attempt_id)
                or observation.read_intent_id != str(read_intent_id)
                or attempt_row.updated_at > now
                or now >= _parse_timestamp(chain.plan.deadline)
            ):
                raise ActionChainReadConflict("read advance lacks timely observed proof")

            succeeded = {
                row.plan_step_id for row in rows if row.state == "succeeded"
            }
            succeeded.add(read_row.plan_step_id)
            released_ids = tuple(
                sorted(
                    (
                        row.plan_step_id
                        for row in rows
                        if row.state == "pending"
                        and row.dependency_step_ids
                        and set(row.dependency_step_ids).issubset(succeeded)
                    ),
                    key=str,
                )
            )
            outcome_ref = f"read-observation:{observation_id}"
            updated = connection.execute(
                sa.update(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == read_row.plan_step_id,
                    plan_steps.c.state == "running",
                    plan_steps.c.read_intent_id == read_intent_id,
                    plan_steps.c.effect_intent_id.is_(None),
                    plan_steps.c.outcome_ref.is_(None),
                    plan_steps.c.revision == read_row.revision,
                )
                .values(
                    state="succeeded",
                    outcome_ref=outcome_ref,
                    revision=int(read_row.revision) + 1,
                    updated_at=now,
                )
            )
            if updated.rowcount != 1:
                raise ActionChainReadConflict("read step changed during advance")
            for released_id in released_ids:
                released = by_id[released_id]
                result = connection.execute(
                    sa.update(plan_steps)
                    .where(
                        plan_steps.c.tenant_id == self._scope_value.tenant_id,
                        plan_steps.c.installation_id
                        == self._scope_value.installation_id,
                        plan_steps.c.plan_step_id == released_id,
                        plan_steps.c.state == "pending",
                        plan_steps.c.read_intent_id.is_(None),
                        plan_steps.c.effect_intent_id.is_(None),
                        plan_steps.c.outcome_ref.is_(None),
                        plan_steps.c.revision == released.revision,
                    )
                    .values(state="ready", revision=2, updated_at=now)
                )
                if result.rowcount != 1:
                    raise ActionChainReadConflict(
                        "dependent step changed during read advance"
                    )

            existing_ready = any(
                row.state == "ready" and row.plan_step_id != read_row.plan_step_id
                for row in rows
            )
            next_state: Literal["pending", "waiting"] = (
                "pending" if released_ids or existing_ready else "waiting"
            )
            context: dict[str, object] = {
                "contract": "ActionChainReadAdvance/v1",
                "read_attempt_id": str(attempt_id),
                "read_intent_id": str(read_intent_id),
                "read_observation_id": str(observation_id),
                "read_outcome_ref": outcome_ref,
                "read_step_id": str(read_row.plan_step_id),
                "released_step_ids": [str(item) for item in released_ids],
            }
            write_action_chain_attention(
                connection,
                self._scope_value,
                chain,
                attention_row,
                state=next_state,
                worker_id=None,
                lease_expires_at=None,
                not_before=now,
                now=now,
                root_event_type="ACTION_CHAIN_READ_DEPENDENCIES_RELEASED",
                root_status="active",
                reason="verified read proof advanced the action chain",
                transition_context=context,
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return AdvancedActionChainRead(
                "advanced",
                read_row.plan_step_id,
                released_ids,
                outcome_ref,
            )

    def _replay(
        self,
        connection: sa.Connection,
        *,
        root_thread_id: str,
        read_step_id: UUID,
        read_intent_id: UUID,
        outcome_ref: str | None,
    ) -> AdvancedActionChainRead:
        events = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.tenant_id == self._scope_value.tenant_id,
                operational_events.c.installation_id
                == self._scope_value.installation_id,
                operational_events.c.thread_id == root_thread_id,
                operational_events.c.event_type
                == "ACTION_CHAIN_READ_DEPENDENCIES_RELEASED",
            )
        ).all()
        matches = []
        for event in events:
            data = event.payload.get("data")
            context = None if not isinstance(data, dict) else data.get(
                "transition_context"
            )
            if isinstance(context, dict) and context.get("read_step_id") == str(
                read_step_id
            ):
                matches.append(context)
        if len(matches) != 1:
            raise ActionChainReadConflict("read advance replay event differs")
        context = matches[0]
        raw_released = context.get("released_step_ids")
        if (
            context.get("read_intent_id") != str(read_intent_id)
            or context.get("read_outcome_ref") != outcome_ref
            or not isinstance(outcome_ref, str)
            or not isinstance(raw_released, list)
        ):
            raise ActionChainReadConflict("read advance replay binding differs")
        try:
            released_ids = tuple(UUID(str(item)) for item in raw_released)
        except (TypeError, ValueError) as error:
            raise ActionChainReadConflict(
                "read advance replay dependency is invalid"
            ) from error
        return AdvancedActionChainRead(
            "already_advanced",
            read_step_id,
            released_ids,
            str(outcome_ref),
        )

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def _require_utc_second(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{field} must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "AdvancedActionChainRead",
    "PostgresActionChainReadAdvancer",
]
