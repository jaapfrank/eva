"""Shared validation of the durable claim preceding a read or effect."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_events,
)
from eva.application.action_chain import (
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import (
    ConnectorErrorResponse,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
)
from eva.contracts.lifecycle import AttentionWake
from eva.contracts.planning import ValidatedPlan


def parse_provider_result(
    value: object, *, kind: Literal["read", "effect"]
) -> ConnectorInvokeSuccess | ConnectorErrorResponse | ConnectorProtocolFailure:
    if not isinstance(value, dict):
        detail = "absent" if kind == "read" else "not an object"
        raise ValueError(f"provider result is {detail}")
    result_type = {
        "ConnectorInvokeSuccess/v1": ConnectorInvokeSuccess,
        "ConnectorErrorResponse/v1": ConnectorErrorResponse,
        "ConnectorProtocolFailure/v1": ConnectorProtocolFailure,
    }.get(value.get("contract"))
    if result_type is None:
        detail = "not allowed" if kind == "read" else "unsupported"
        raise ValueError(f"provider result contract is {detail}")
    return result_type.model_validate(value)


def validate_prepared_claim(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    kind: Literal["read", "effect"],
    plan: ValidatedPlan,
    plan_id: UUID,
    plan_step_id: UUID,
    logical_step_key: str,
    corrupt: type[RuntimeError],
) -> tuple[str, datetime]:
    root_thread_id = action_chain_thread_id(plan)
    child_thread_id = action_chain_step_operation_id(plan, logical_step_key)
    event_type = f"ACTION_CHAIN_{kind.upper()}_INTENT_PREPARED"
    child = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == child_thread_id,
            operational_events.c.event_type == event_type,
        )
    ).one_or_none()
    if child is None:
        raise corrupt(f"prepared {kind} child event is absent")
    prepared = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == root_thread_id,
            operational_events.c.event_type == event_type,
            operational_events.c.occurred_at == child.occurred_at,
        )
    ).one_or_none()
    if prepared is None and kind == "read":
        raise corrupt("prepared read root event is absent")
    claimed = (
        None
        if prepared is None
        else connection.execute(
            sa.select(operational_events).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.thread_id == root_thread_id,
                operational_events.c.sequence == int(prepared.sequence) - 1,
            )
        ).one_or_none()
    )
    if (
        prepared is None
        or claimed is None
        or prepared.event_type != event_type
        or claimed.event_type != "ACTION_CHAIN_CONTINUATION_CLAIMED"
        or int(prepared.sequence) != int(claimed.sequence) + 1
        or prepared.occurred_at != child.occurred_at
    ):
        raise corrupt(f"prepared {kind} lease timeline differs")
    for event in (prepared, claimed):
        if (
            event.operation_id != root_thread_id
            or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
            or set(event.payload) != {"checkpoint", "data"}
        ):
            raise corrupt(f"prepared {kind} lease event differs")
    data = claimed.payload.get("data")
    if not isinstance(data, dict):
        if kind == "read":
            raise corrupt("prepared read claim data is absent")
        data = {}
    attention_payload = data.get("attention")
    if not isinstance(attention_payload, dict) and kind == "read":
        raise corrupt("prepared read claim attention is absent")
    try:
        attention = AttentionWake.model_validate(attention_payload)
    except ValueError as error:
        raise corrupt(f"prepared {kind} claim attention is invalid") from error
    attention_id = uuid5(plan_id, "continuation:1")
    if (
        set(data)
        != {"attention", "attention_payload_sha256", "attention_revision", "plan_id"}
        or attention.attention_id != str(attention_id)
        or attention.state != "claimed"
        or attention.lease_owner is None
        or attention.lease_expires_at is None
        or data.get("attention_payload_sha256") != canonical_sha256(attention)
        or data.get("attention_revision") != claimed.sequence
        or data.get("plan_id") != str(plan_id)
    ):
        raise corrupt(f"prepared {kind} claim binding differs")
    return (
        canonical_sha256(
            {
                "attention_id": str(attention_id),
                "attention_revision": data["attention_revision"],
                "lease_expires_at": attention.lease_expires_at,
                "logical_step_key": logical_step_key,
                "plan_id": str(plan_id),
                "plan_step_id": str(plan_step_id),
                "worker_id": attention.lease_owner,
            }
        ),
        prepared.occurred_at,
    )


def validated_released_step_ids(
    *,
    source_step_id: UUID,
    by_id: dict[UUID, sa.Row],
    context: dict[str, object],
    event_time: datetime,
    kind: Literal["read", "effect"],
    corrupt: type[RuntimeError],
) -> tuple[UUID, ...]:
    raw = context.get("released_step_ids")
    if not isinstance(raw, list):
        raise corrupt(f"{kind} advance released-step set is invalid")
    try:
        released = tuple(UUID(str(item)) for item in raw)
    except (TypeError, ValueError) as error:
        raise corrupt(f"{kind} advance released-step identity is invalid") from error
    if raw != [str(item) for item in sorted(released, key=str)] or len(
        set(released)
    ) != len(released):
        raise corrupt(f"{kind} advance released-step order differs")
    succeeded = {step_id for step_id, row in by_id.items() if row.state == "succeeded"}
    if any(
        step_id not in by_id
        or source_step_id not in by_id[step_id].dependency_step_ids
        or not set(by_id[step_id].dependency_step_ids).issubset(succeeded)
        or any(
            by_id[dependency].updated_at > event_time
            for dependency in by_id[step_id].dependency_step_ids
        )
        for step_id in released
    ):
        raise corrupt(f"{kind} advance released an ineligible dependency")
    return released


def latest_prepared_child_data(
    connection: sa.Connection,
    scope: TimelineScope,
    plan: ValidatedPlan,
    logical_step_key: str,
    *,
    kind: Literal["read", "effect"],
    corrupt: type[RuntimeError],
) -> dict[str, object]:
    operation_id = action_chain_step_operation_id(plan, logical_step_key)
    row = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == operation_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one()
    data = row.payload.get("data")
    if row.event_type != f"ACTION_CHAIN_{kind.upper()}_INTENT_PREPARED" or not isinstance(
        data, dict
    ):
        raise corrupt(f"prepared {kind} timeline differs")
    return data
