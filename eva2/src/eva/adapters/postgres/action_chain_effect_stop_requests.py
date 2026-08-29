"""Durably record an owner stop request after an effect call has started.

This does not claim to kill a provider process or undo an external effect.  The
call-start fence may already represent real external I/O.  The separate thread
therefore records the owner's request without changing the effect intent,
attempt or provider-result timeline.
"""

from __future__ import annotations
from eva.contracts.timestamps import (
    format_normalized_utc_timestamp as _format_timestamp,
)
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_effect_cancellations import (
    ActionChainEffectOwner,
    validate_action_chain_effect_owner,
)
from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainStore,
    StoredActionChain,
    plan_steps,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    insert_initial_timeline_thread,
    operational_checkpoints,
    operational_events,
)
from eva.application.action_chain import action_chain_step_operation_id
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectAttempt, EffectIntent


effect_intents = metadata.tables["eva_effects.effect_intents"]
effect_attempts = metadata.tables["eva_effects.effect_attempts"]
episodes = metadata.tables["eva_core.episodes"]
ingress_requests = metadata.tables["eva_core.ingress_requests"]
principal_bindings = metadata.tables["eva_core.principal_bindings"]


class ActionChainEffectStopRequestConflict(ActionChainEffectConflict):
    """A post-call stop request is absent, unauthorized or inconsistent."""


@dataclass(frozen=True, slots=True)
class RequestedActionChainEffectStop:
    """One idempotent owner request; external outcome remains undecided."""

    disposition: Literal["stop_requested", "already_requested", "already_terminal"]
    effect_intent_id: UUID
    effect_attempt_id: UUID
    thread_id: str


class PostgresActionChainEffectStopRequester(PostgresActionChainStore):
    _postgres_error = "action-chain effect stop requests require PostgreSQL"
    """Append one owner-bound stop request only after provider call-start."""

    def request_stop(
        self,
        effect_intent_id: UUID,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        now: datetime,
    ) -> RequestedActionChainEffectStop:
        """Record a request while preserving the provider result as authority."""

        _require_utc_second(now, "now")
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id
                    == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id == effect_intent_id,
                )
                .with_for_update()
            ).one_or_none()
            if intent_row is None:
                raise ActionChainEffectStopRequestConflict("effect intent is absent")
            step_row = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == intent_row.plan_step_id,
                    plan_steps.c.effect_intent_id == effect_intent_id,
                )
                .with_for_update()
            ).one_or_none()
            if step_row is None:
                raise ActionChainEffectStopRequestConflict("effect step is absent")
            attempt_row = connection.execute(
                sa.select(effect_attempts)
                .where(
                    effect_attempts.c.tenant_id == self._scope_value.tenant_id,
                    effect_attempts.c.installation_id
                    == self._scope_value.installation_id,
                    effect_attempts.c.effect_intent_id == effect_intent_id,
                    effect_attempts.c.ordinal == 1,
                )
                .with_for_update()
            ).one_or_none()
            if attempt_row is None:
                raise ActionChainEffectStopRequestConflict(
                    "effect provider call has not started"
                )
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            intent = _intent(intent_row)
            attempt = _attempt(attempt_row)
            thread_id = _stop_thread_id(attempt)
            existing = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                )
                .with_for_update()
            ).one_or_none()
            if existing is not None:
                _require_existing_request(
                    connection,
                    scope=self._scope_value,
                    thread_id=thread_id,
                    checkpoint=existing,
                    chain=chain,
                    step_row=step_row,
                    effect_intent_id=effect_intent_id,
                    effect_attempt_id=UUID(attempt.effect_attempt_id),
                    owner_principal_id=owner_principal_id,
                    principal_binding_id=principal_binding_id,
                )
                return RequestedActionChainEffectStop(
                    "already_requested",
                    effect_intent_id,
                    UUID(attempt.effect_attempt_id),
                    thread_id,
                )
            owner = validate_action_chain_effect_owner(
                connection,
                scope=self._scope_value,
                episode_id=chain.storage_episode_id,
                generation=chain.plan.generation,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
                conflict=ActionChainEffectStopRequestConflict,
            )
            if (
                intent.state != "dispatch_authorized"
                or attempt.state != "dispatch_authorized"
                or int(intent_row.revision) != 2
                or int(attempt_row.revision) != 1
            ):
                return RequestedActionChainEffectStop(
                    "already_terminal",
                    effect_intent_id,
                    UUID(attempt.effect_attempt_id),
                    thread_id,
                )
            call_start = _call_start_event(
                connection, self._scope_value, chain, step_row, intent, attempt
            )
            context = _stop_context(
                owner=owner,
                chain_plan_id=chain.plan_id,
                step_row=step_row,
                intent=intent,
                attempt=attempt,
                call_start=call_start,
                requested_at=now,
            )
            checkpoint_id = _stop_checkpoint_id(thread_id, context)
            insert_initial_timeline_thread(
                connection,
                self._scope_value,
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                occurred_at=now,
                data=context,
                event_type="ACTION_CHAIN_EFFECT_STOP_REQUESTED",
                phase="action_chain_effect_stop_requested",
                status="waiting_for_proof",
                operation_id=thread_id,
                deadline=None,
            )
            return RequestedActionChainEffectStop(
                "stop_requested",
                effect_intent_id,
                UUID(attempt.effect_attempt_id),
                thread_id,
            )


def _call_start_event(
    connection: sa.Connection,
    scope: TimelineScope,
    chain: StoredActionChain,
    step_row: sa.Row,
    intent: EffectIntent,
    attempt: EffectAttempt,
) -> sa.Row:
    operation_id = action_chain_step_operation_id(chain.plan, step_row.logical_step_key)
    event = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == operation_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one_or_none()
    data = None if event is None else event.payload.get("data")
    if (
        event is None
        or event.event_type != "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
        or not isinstance(data, dict)
        or data.get("effect_intent_id") != intent.effect_intent_id
        or data.get("effect_intent_payload_sha256") != canonical_sha256(intent)
        or data.get("effect_attempt_id") != attempt.effect_attempt_id
        or data.get("effect_attempt_payload_sha256") != canonical_sha256(attempt)
        or data.get("dispatch_permit_sha256") != attempt.dispatch_permit_sha256
        or data.get("request_sha256") != attempt.request_sha256
        or data.get("provider_call_started_at") != _format_timestamp(event.occurred_at)
        or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
    ):
        raise ActionChainEffectStopRequestConflict(
            "effect provider call has not started"
        )
    return event


def _stop_context(
    *,
    owner: ActionChainEffectOwner,
    chain_plan_id: UUID,
    step_row: sa.Row,
    intent: EffectIntent,
    attempt: EffectAttempt,
    call_start: sa.Row,
    requested_at: datetime,
) -> dict[str, object]:
    return {
        "binding_verification_evidence_id": str(owner.binding_verification_evidence_id),
        "binding_subject_sha256": owner.binding_subject_sha256,
        "binding_revision": owner.binding_revision,
        "binding_verified_at": _format_timestamp(owner.binding_verified_at),
        "call_start_event_id": str(call_start.event_id),
        "call_start_payload_sha256": bytes(call_start.payload_sha256).hex(),
        "call_start_sequence": int(call_start.sequence),
        "contract": "ActionChainEffectStopRequest/v1",
        "dispatch_permit_sha256": attempt.dispatch_permit_sha256,
        "effect_attempt_id": attempt.effect_attempt_id,
        "effect_attempt_payload_sha256": canonical_sha256(attempt),
        "effect_intent_id": intent.effect_intent_id,
        "effect_intent_payload_sha256": canonical_sha256(intent),
        "owner_principal_id": str(owner.principal_id),
        "plan_id": str(chain_plan_id),
        "plan_step_id": str(step_row.plan_step_id),
        "principal_binding_id": str(owner.principal_binding_id),
        "request_sha256": attempt.request_sha256,
        "requested_at": _format_timestamp(requested_at),
    }


def _require_existing_request(
    connection: sa.Connection,
    *,
    scope: TimelineScope,
    thread_id: str,
    checkpoint: sa.Row,
    chain: StoredActionChain,
    step_row: sa.Row,
    effect_intent_id: UUID,
    effect_attempt_id: UUID,
    owner_principal_id: UUID,
    principal_binding_id: UUID,
) -> None:
    event = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == thread_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one()
    data = event.payload.get("data")
    required_keys = {
        "binding_verification_evidence_id",
        "binding_subject_sha256",
        "binding_revision",
        "binding_verified_at",
        "call_start_event_id",
        "call_start_payload_sha256",
        "call_start_sequence",
        "contract",
        "dispatch_permit_sha256",
        "effect_attempt_id",
        "effect_attempt_payload_sha256",
        "effect_intent_id",
        "effect_intent_payload_sha256",
        "owner_principal_id",
        "plan_id",
        "plan_step_id",
        "principal_binding_id",
        "request_sha256",
        "requested_at",
    }
    base_valid = not (
        event.sequence != 1
        or event.thread_id != thread_id
        or event.checkpoint_id != checkpoint.checkpoint_id
        or event.operation_id != checkpoint.operation_id
        or event.event_type != "ACTION_CHAIN_EFFECT_STOP_REQUESTED"
        or not isinstance(data, dict)
        or set(data) != required_keys
        or data.get("contract") != "ActionChainEffectStopRequest/v1"
        or data.get("effect_intent_id") != str(effect_intent_id)
        or data.get("effect_attempt_id") != str(effect_attempt_id)
        or data.get("owner_principal_id") != str(owner_principal_id)
        or data.get("principal_binding_id") != str(principal_binding_id)
        or data.get("plan_id") != str(chain.plan_id)
        or data.get("plan_step_id") != str(step_row.plan_step_id)
        or data.get("requested_at") != _format_timestamp(event.occurred_at)
        or not isinstance(data.get("call_start_sequence"), int)
        or not isinstance(data.get("binding_revision"), int)
        or int(data["binding_revision"]) < 1
        or any(
            not _is_sha256(data.get(key))
            for key in (
                "binding_subject_sha256",
                "call_start_payload_sha256",
                "dispatch_permit_sha256",
                "effect_attempt_payload_sha256",
                "effect_intent_payload_sha256",
                "request_sha256",
            )
        )
        or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
        or checkpoint.thread_id != thread_id
        or checkpoint.checkpoint_id != _stop_checkpoint_id(thread_id, data)
        or checkpoint.operation_id != thread_id
        or checkpoint.phase != "action_chain_effect_stop_requested"
        or checkpoint.status != "waiting_for_proof"
        or int(checkpoint.retry_count) != 0
        or checkpoint.last_error_fingerprint is not None
        or checkpoint.deadline is not None
        or int(checkpoint.revision) != 1
        or checkpoint.updated_at != event.occurred_at
        or event.payload != _checkpoint_payload(checkpoint, data)
    )
    if not base_valid:
        raise ActionChainEffectStopRequestConflict("stored stop request differs")
    assert isinstance(data, dict)
    try:
        call_start_event_id = UUID(str(data["call_start_event_id"]))
    except (TypeError, ValueError) as error:
        raise ActionChainEffectStopRequestConflict(
            "stored stop request differs"
        ) from error
    call_start = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.event_id == call_start_event_id,
        )
    ).one_or_none()
    call_data = None if call_start is None else call_start.payload.get("data")
    historical_owner = connection.execute(
        sa.select(
            episodes.c.principal_id.label("episode_principal_id"),
            ingress_requests.c.principal_id.label("ingress_principal_id"),
            ingress_requests.c.principal_binding_id,
        )
        .join(
            ingress_requests,
            sa.and_(
                ingress_requests.c.tenant_id == episodes.c.tenant_id,
                ingress_requests.c.installation_id == episodes.c.installation_id,
                ingress_requests.c.ingress_request_id == episodes.c.ingress_request_id,
            ),
        )
        .where(
            episodes.c.tenant_id == scope.tenant_id,
            episodes.c.installation_id == scope.installation_id,
            episodes.c.episode_id == chain.storage_episode_id,
            episodes.c.current_generation == chain.plan.generation,
        )
    ).one_or_none()
    binding = connection.execute(
        sa.select(
            principal_bindings.c.principal_id,
            principal_bindings.c.verification_evidence_id,
            principal_bindings.c.external_subject_sha256,
            principal_bindings.c.verified_at,
            principal_bindings.c.revision,
        ).where(
            principal_bindings.c.tenant_id == scope.tenant_id,
            principal_bindings.c.principal_binding_id == principal_binding_id,
        )
    ).one_or_none()
    if (
        call_start is None
        or call_start.event_type != "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
        or not isinstance(call_data, dict)
        or int(data["call_start_sequence"]) != int(call_start.sequence)
        or data["call_start_payload_sha256"] != bytes(call_start.payload_sha256).hex()
        or data["effect_intent_id"] != call_data.get("effect_intent_id")
        or data["effect_attempt_id"] != call_data.get("effect_attempt_id")
        or data["dispatch_permit_sha256"] != call_data.get("dispatch_permit_sha256")
        or data["request_sha256"] != call_data.get("request_sha256")
        or data["effect_intent_payload_sha256"]
        != call_data.get("effect_intent_payload_sha256")
        or data["effect_attempt_payload_sha256"]
        != call_data.get("effect_attempt_payload_sha256")
        or canonical_sha256(call_start.payload)
        != bytes(call_start.payload_sha256).hex()
        or call_start.thread_id
        != action_chain_step_operation_id(chain.plan, step_row.logical_step_key)
        or historical_owner is None
        or historical_owner.episode_principal_id != owner_principal_id
        or historical_owner.ingress_principal_id != owner_principal_id
        or historical_owner.principal_binding_id != principal_binding_id
        or binding is None
        or binding.principal_id != owner_principal_id
        or binding.verification_evidence_id is None
        or binding.external_subject_sha256 is None
        or binding.verified_at is None
        or data["binding_verification_evidence_id"]
        != str(binding.verification_evidence_id)
        or data["binding_subject_sha256"]
        != bytes(binding.external_subject_sha256).hex()
        or data["binding_verified_at"] != _format_timestamp(binding.verified_at)
        or int(data["binding_revision"]) > int(binding.revision)
    ):
        raise ActionChainEffectStopRequestConflict("stored stop request differs")


def has_valid_post_call_stop_request(
    connection: sa.Connection,
    *,
    scope: TimelineScope,
    chain: StoredActionChain,
    step_row: sa.Row,
    effect_intent_id: UUID,
    attempt: EffectAttempt,
) -> bool:
    """Return whether a fully bound owner stop request blocks chain progress.

    A request after the call-start fence cannot erase the provider outcome or
    physical readback.  It must, however, prevent that outcome from silently
    unlocking another step in the same autonomous chain.
    """

    if attempt.effect_intent_id != str(effect_intent_id):
        raise ActionChainEffectStopRequestConflict("effect attempt differs")
    thread_id = _stop_thread_id(attempt)
    checkpoint = connection.execute(
        sa.select(operational_checkpoints)
        .where(
            operational_checkpoints.c.tenant_id == scope.tenant_id,
            operational_checkpoints.c.installation_id == scope.installation_id,
            operational_checkpoints.c.thread_id == thread_id,
        )
        .with_for_update()
    ).one_or_none()
    if checkpoint is None:
        return False
    event = connection.execute(
        sa.select(operational_events)
        .where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == thread_id,
        )
        .order_by(operational_events.c.sequence.desc())
        .limit(1)
    ).one_or_none()
    data = None if event is None else event.payload.get("data")
    try:
        owner_principal_id = UUID(str(data["owner_principal_id"]))
        principal_binding_id = UUID(str(data["principal_binding_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ActionChainEffectStopRequestConflict(
            "stored stop request differs"
        ) from error
    _require_existing_request(
        connection,
        scope=scope,
        thread_id=thread_id,
        checkpoint=checkpoint,
        chain=chain,
        step_row=step_row,
        effect_intent_id=effect_intent_id,
        effect_attempt_id=UUID(attempt.effect_attempt_id),
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
    )
    return True


def _intent(row: sa.Row) -> EffectIntent:
    try:
        return EffectIntent.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainEffectStopRequestConflict(
            "effect intent payload is invalid"
        ) from error


def _attempt(row: sa.Row) -> EffectAttempt:
    try:
        return EffectAttempt.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainEffectStopRequestConflict(
            "effect attempt payload is invalid"
        ) from error


def _stop_thread_id(attempt: EffectAttempt) -> str:
    return f"action-chain-effect-stop:{attempt.effect_attempt_id}"


def _stop_checkpoint_id(thread_id: str, context: dict[str, object]) -> str:
    return f"checkpoint:{thread_id}:{canonical_sha256(context)}"


def _checkpoint_payload(
    checkpoint: sa.Row, data: dict[str, object]
) -> dict[str, object]:
    return {
        "checkpoint": {
            "checkpoint_id": checkpoint.checkpoint_id,
            "deadline": (
                None
                if checkpoint.deadline is None
                else _format_timestamp(checkpoint.deadline)
            ),
            "last_error_fingerprint": (
                None
                if checkpoint.last_error_fingerprint is None
                else bytes(checkpoint.last_error_fingerprint).hex()
            ),
            "operation_id": checkpoint.operation_id,
            "phase": checkpoint.phase,
            "retry_count": int(checkpoint.retry_count),
            "revision": int(checkpoint.revision),
            "status": checkpoint.status,
            "updated_at": _format_timestamp(checkpoint.updated_at),
        },
        "data": data,
    }


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


__all__ = [
    "ActionChainEffectStopRequestConflict",
    "PostgresActionChainEffectStopRequester",
    "RequestedActionChainEffectStop",
    "has_valid_post_call_stop_request",
]
