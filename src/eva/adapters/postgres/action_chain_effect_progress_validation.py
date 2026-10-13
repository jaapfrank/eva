"""Reload validation for proof-driven effect completion and dependency release."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp

from typing import Any, Sequence
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import TimelineScope, operational_events
from eva.adapters.postgres.action_chain_validation_common import (
    validated_released_step_ids,
)
from eva.application.action_chain import (
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import AttentionWake
from eva.contracts.planning import ValidatedPlan


def validate_action_chain_effect_advances(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    plan_id: UUID,
    plan: ValidatedPlan,
    step_rows: Sequence[sa.Row],
    root_current: dict[str, Any],
    corrupt: type[RuntimeError],
) -> tuple[dict[str, object] | None, set[UUID]]:
    """Require each succeeded effect to own one exact root transition."""

    by_id = {row.plan_step_id: row for row in step_rows}
    logical_by_id = {row.plan_step_id: row.logical_step_key for row in step_rows}
    completed = {
        row.plan_step_id: row
        for row in step_rows
        if row.effect_class == "effect" and row.state == "succeeded"
    }
    root_thread_id = action_chain_thread_id(plan)
    events = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
            operational_events.c.event_type
            == "ACTION_CHAIN_EFFECT_DEPENDENCIES_RELEASED",
        )
        .order_by(operational_events.c.sequence)
    ).all()
    if len(events) != len(completed):
        raise corrupt("succeeded effect advance cardinality differs")

    released_union: set[UUID] = set()
    context_by_sequence: dict[int, dict[str, object]] = {}
    seen: set[UUID] = set()
    for event in events:
        context = _event_context(
            event,
            plan_id=plan_id,
            plan=plan,
            root_thread_id=root_thread_id,
            corrupt=corrupt,
        )
        try:
            effect_step_id = UUID(str(context["effect_step_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise corrupt("effect advance step identity is invalid") from error
        row = completed.get(effect_step_id)
        if row is None or effect_step_id in seen:
            raise corrupt("effect advance refers to another or duplicate step")
        seen.add(effect_step_id)
        child = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.thread_id
                == action_chain_step_operation_id(plan, logical_by_id[effect_step_id]),
                operational_events.c.event_type == "STATE_VERIFIED",
            )
        ).one_or_none()
        if (
            child is None
            or child.occurred_at != event.occurred_at
            or child.payload.get("data") != context
            or row.updated_at != event.occurred_at
            or context.get("effect_intent_id") != str(row.effect_intent_id)
            or context.get("effect_outcome_ref") != row.outcome_ref
        ):
            raise corrupt("effect advance proof timeline differs")
        released = validated_released_step_ids(
            source_step_id=row.plan_step_id,
            by_id=by_id,
            context=context,
            event_time=event.occurred_at,
            kind="effect",
            corrupt=corrupt,
        )
        released_union.update(released)
        context_by_sequence[int(event.sequence)] = context

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
    if latest.event_type != "ACTION_CHAIN_EFFECT_DEPENDENCIES_RELEASED":
        return None, released_union
    current = root_current.get("transition_context")
    expected = context_by_sequence.get(int(latest.sequence))
    if not isinstance(current, dict) or current != expected:
        raise corrupt("current effect advance context differs")
    return expected, released_union


def _event_context(
    event: sa.Row,
    *,
    plan_id: UUID,
    plan: ValidatedPlan,
    root_thread_id: str,
    corrupt: type[RuntimeError],
) -> dict[str, object]:
    payload = event.payload
    checkpoint = payload.get("checkpoint")
    data = payload.get("data")
    if (
        event.operation_id != root_thread_id
        or set(payload) != {"checkpoint", "data"}
        or canonical_sha256(payload) != bytes(event.payload_sha256).hex()
        or not isinstance(checkpoint, dict)
        or not isinstance(data, dict)
    ):
        raise corrupt("effect advance event envelope differs")
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
    try:
        attention = AttentionWake.model_validate(data.get("attention"))
    except ValueError as error:
        raise corrupt("effect advance attention is invalid") from error
    context = data.get("transition_context")
    if (
        checkpoint != expected_checkpoint
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
        or not isinstance(context, dict)
    ):
        raise corrupt("effect advance attention binding differs")
    return dict(context)


__all__ = ["validate_action_chain_effect_advances"]
