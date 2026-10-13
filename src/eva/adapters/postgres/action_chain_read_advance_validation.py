"""Reload validation for read-proof-driven action-chain step advances."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp

from datetime import datetime
from typing import Any, Sequence
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_events,
)
from eva.adapters.postgres.action_chain_validation_common import (
    validated_released_step_ids,
)
from eva.application.action_chain import action_chain_thread_id
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import ReadAttempt, ReadObservation
from eva.contracts.lifecycle import AttentionWake
from eva.contracts.planning import ValidatedPlan


read_attempts = metadata.tables["eva_effects.read_attempts"]
read_observations = metadata.tables["eva_effects.read_observations"]


def validate_action_chain_read_advances(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan_id: UUID,
    plan: ValidatedPlan,
    step_rows: Sequence[sa.Row],
    root_current: dict[str, Any],
    effect_released_step_ids: set[UUID] | None = None,
    corrupt: type[RuntimeError],
) -> dict[str, object] | None:
    """Require each succeeded read and released dependency to have one event."""

    by_id = {row.plan_step_id: row for row in step_rows}
    completed_reads = {
        row.plan_step_id: row
        for row in step_rows
        if row.effect_class == "read" and row.state == "succeeded"
    }
    root_thread_id = action_chain_thread_id(plan)
    events = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
            operational_events.c.event_type
            == "ACTION_CHAIN_READ_DEPENDENCIES_RELEASED",
        )
        .order_by(operational_events.c.sequence)
    ).all()
    if len(events) != len(completed_reads):
        raise corrupt("succeeded read advance cardinality differs")

    seen: set[UUID] = set()
    context_by_sequence: dict[int, dict[str, object]] = {}
    for event in events:
        context = _validated_event_context(
            event,
            plan_id=plan_id,
            plan=plan,
            root_thread_id=root_thread_id,
            corrupt=corrupt,
        )
        try:
            read_step_id = UUID(str(context["read_step_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise corrupt("read advance step identity is invalid") from error
        read_row = completed_reads.get(read_step_id)
        if read_row is None or read_step_id in seen:
            raise corrupt("read advance refers to another or duplicate step")
        seen.add(read_step_id)
        _validate_read_proof(
            connection,
            scope,
            read_row=read_row,
            context=context,
            event_time=event.occurred_at,
            corrupt=corrupt,
        )
        released_ids = validated_released_step_ids(
            source_step_id=read_row.plan_step_id,
            by_id=by_id,
            context=context,
            event_time=event.occurred_at,
            kind="read",
            corrupt=corrupt,
        )
        for released_id in released_ids:
            released = by_id[released_id]
            if released.state == "pending" or int(released.revision) < 2:
                raise corrupt("read advance dependency was not released")
        context_by_sequence[int(event.sequence)] = context

    progressed_dependencies = {
        row.plan_step_id
        for row in step_rows
        if row.dependency_step_ids and int(row.revision) >= 2
    }
    released_union = {
        UUID(item)
        for context in context_by_sequence.values()
        for item in context["released_step_ids"]
    }
    released_union.update(effect_released_step_ids or set())
    if progressed_dependencies != released_union:
        raise corrupt("dependent step progress lacks one read advance")

    latest = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one()
    current_context = root_current.get("transition_context")
    if latest.event_type == "ACTION_CHAIN_READ_DEPENDENCIES_RELEASED":
        expected = context_by_sequence.get(int(latest.sequence))
        if not isinstance(current_context, dict) or current_context != expected:
            raise corrupt("current read advance context differs")
        return expected
    if current_context is not None and latest.event_type not in {
        "ACTION_CHAIN_EFFECT_DEPENDENCIES_RELEASED",
        "ACTION_CHAIN_EFFECT_CANCELLED",
    }:
        raise corrupt("non-advance root event contains read advance context")
    return None


def _validated_event_context(
    event: sa.Row,
    *,
    plan_id: UUID,
    plan: ValidatedPlan,
    root_thread_id: str,
    corrupt: type[RuntimeError],
) -> dict[str, object]:
    payload = event.payload
    if (
        event.operation_id != root_thread_id
        or set(payload) != {"checkpoint", "data"}
        or canonical_sha256(payload) != bytes(event.payload_sha256).hex()
    ):
        raise corrupt("read advance event envelope differs")
    checkpoint = payload.get("checkpoint")
    data = payload.get("data")
    if not isinstance(checkpoint, dict) or not isinstance(data, dict):
        raise corrupt("read advance event payload is invalid")
    expected_checkpoint = {
        "checkpoint_id": str(event.checkpoint_id),
        "deadline": plan.deadline,
        "last_error_fingerprint": None,
        "operation_id": root_thread_id,
        "phase": "action_chain_continuation",
        "retry_count": 0,
        "revision": int(event.sequence),
        "status": "active",
        "updated_at": _format_timestamp(event.occurred_at),
    }
    attention_payload = data.get("attention")
    context = data.get("transition_context")
    try:
        attention = AttentionWake.model_validate(attention_payload)
    except ValueError as error:
        raise corrupt("read advance attention is invalid") from error
    if (
        checkpoint != expected_checkpoint
        or not isinstance(context, dict)
        or set(data)
        != {
            "attention",
            "attention_payload_sha256",
            "attention_revision",
            "plan_id",
            "transition_context",
        }
        or attention.state not in {"pending", "waiting"}
        or data.get("attention_payload_sha256") != canonical_sha256(attention)
        or data.get("attention_revision") != event.sequence
        or data.get("plan_id") != str(plan_id)
    ):
        raise corrupt("read advance attention binding differs")
    return dict(context)


def _validate_read_proof(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    read_row: sa.Row,
    context: dict[str, object],
    event_time: datetime,
    corrupt: type[RuntimeError],
) -> None:
    attempt_id = uuid5(read_row.read_intent_id, "read-attempt:1")
    observation_id = uuid5(attempt_id, "observation:v1")
    attempt_row = connection.execute(
        sa.select(read_attempts).where(
            read_attempts.c.tenant_id == scope.tenant_id,
            read_attempts.c.installation_id == scope.installation_id,
            read_attempts.c.read_attempt_id == attempt_id,
        )
    ).one_or_none()
    observation_row = connection.execute(
        sa.select(read_observations).where(
            read_observations.c.tenant_id == scope.tenant_id,
            read_observations.c.installation_id == scope.installation_id,
            read_observations.c.observation_id == observation_id,
            read_observations.c.read_attempt_id == attempt_id,
        )
    ).one_or_none()
    try:
        attempt = ReadAttempt.model_validate(
            None if attempt_row is None else attempt_row.payload
        )
        observation = ReadObservation.model_validate(
            None if observation_row is None else observation_row.payload
        )
    except ValueError as error:
        raise corrupt("read advance proof payload is invalid") from error
    outcome_ref = f"read-observation:{observation_id}"
    expected_context = {
        "contract": "ActionChainReadAdvance/v1",
        "read_attempt_id": str(attempt_id),
        "read_intent_id": str(read_row.read_intent_id),
        "read_observation_id": str(observation_id),
        "read_outcome_ref": outcome_ref,
        "read_step_id": str(read_row.plan_step_id),
        "released_step_ids": context.get("released_step_ids"),
    }
    if (
        context != expected_context
        or attempt.state != "observed"
        or observation.read_attempt_id != str(attempt_id)
        or observation.read_intent_id != str(read_row.read_intent_id)
        or read_row.outcome_ref != outcome_ref
        or read_row.updated_at != event_time
        or attempt_row.updated_at > event_time
    ):
        raise corrupt("read advance proof binding differs")


__all__ = ["validate_action_chain_read_advances"]
