"""Owner-bound cancellation before an action-chain effect is dispatched.

This is intentionally narrower than stopping a running browser.  It can only
win while no provider attempt exists, so a successful cancellation proves that
no provider-call-start marker or connector process could have been created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    attention_items,
    plan_steps,
)
from eva.adapters.postgres.action_chain_leases import write_action_chain_attention
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import action_chain_step_operation_id
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectIntent


effect_intents = metadata.tables["eva_effects.effect_intents"]
effect_attempts = metadata.tables["eva_effects.effect_attempts"]
episodes = metadata.tables["eva_core.episodes"]
episode_generations = metadata.tables["eva_core.episode_generations"]
principal_bindings = metadata.tables["eva_core.principal_bindings"]
principals = metadata.tables["eva_core.principals"]


class ActionChainEffectCancellationConflict(ActionChainEffectConflict):
    """Cancellation is absent, not owned by the caller, or has already dispatched."""


@dataclass(frozen=True, slots=True)
class CancelledActionChainEffect:
    disposition: Literal["cancelled", "already_cancelled", "already_dispatched"]
    intent: EffectIntent


@dataclass(frozen=True, slots=True)
class ActionChainEffectOwner:
    principal_id: UUID
    principal_binding_id: UUID
    binding_verification_evidence_id: UUID
    binding_subject_sha256: str
    binding_revision: int
    binding_verified_at: datetime


class PostgresActionChainEffectCanceller:
    """Cancel a prepared effect under the same locks as dispatch authorization."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain effect cancellation requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def cancel_pre_dispatch(
        self,
        effect_intent_id: UUID,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        now: datetime,
    ) -> CancelledActionChainEffect:
        """Cancel only before authorization creates the first provider attempt."""

        _require_utc_second(now, "now")
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id == effect_intent_id,
                )
                .with_for_update()
            ).one_or_none()
            if intent_row is None:
                raise ActionChainEffectCancellationConflict("effect intent is absent")
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
                raise ActionChainEffectCancellationConflict("effect step is absent")
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            intent = _intent(intent_row)
            cancellation_owner = self._validate_owner(
                connection,
                episode_id=chain.storage_episode_id,
                generation=chain.plan.generation,
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
            )
            if any(
                stored.plan_step_id != step_row.plan_step_id
                and stored.state != "succeeded"
                for stored in chain.steps
            ):
                raise ActionChainEffectCancellationConflict(
                    "pre-dispatch cancellation requires the last unfinished step"
                )
            if intent.state == "cancelled":
                return CancelledActionChainEffect("already_cancelled", intent)
            if (
                intent.state != "prepared"
                or intent_row.state != "prepared"
                or int(intent_row.revision) != 1
                or step_row.state != "running"
            ):
                return CancelledActionChainEffect("already_dispatched", intent)
            attempt_count = connection.scalar(
                sa.select(sa.func.count())
                .select_from(effect_attempts)
                .where(
                    effect_attempts.c.tenant_id == self._scope_value.tenant_id,
                    effect_attempts.c.installation_id == self._scope_value.installation_id,
                    effect_attempts.c.effect_intent_id == effect_intent_id,
                )
            )
            if attempt_count:
                return CancelledActionChainEffect("already_dispatched", intent)

            cancelled = intent.model_copy(update={"state": "cancelled"})
            intent_update = connection.execute(
                sa.update(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id == effect_intent_id,
                    effect_intents.c.state == "prepared",
                    effect_intents.c.revision == 1,
                )
                .values(
                    state="cancelled",
                    payload=cancelled.model_dump(mode="json"),
                    payload_sha256=bytes.fromhex(canonical_sha256(cancelled)),
                    revision=2,
                    updated_at=now,
                )
            )
            step_update = connection.execute(
                sa.update(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == step_row.plan_step_id,
                    plan_steps.c.effect_intent_id == effect_intent_id,
                    plan_steps.c.state == "running",
                    plan_steps.c.revision == step_row.revision,
                )
                .values(
                    state="cancelled",
                    revision=int(step_row.revision) + 1,
                    updated_at=now,
                )
            )
            if intent_update.rowcount != 1 or step_update.rowcount != 1:
                raise ActionChainEffectCancellationConflict("effect cancellation raced")

            operation_id = action_chain_step_operation_id(
                chain.plan, step_row.logical_step_key
            )
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == operation_id,
                )
                .with_for_update()
            ).one()
            cancellation_context = {
                "binding_verification_evidence_id": str(
                    cancellation_owner.binding_verification_evidence_id
                ),
                "binding_subject_sha256": cancellation_owner.binding_subject_sha256,
                "binding_revision": cancellation_owner.binding_revision,
                "binding_verified_at": _format_timestamp(
                    cancellation_owner.binding_verified_at
                ),
                "contract": "ActionChainEffectCancellation/v1",
                "effect_intent_id": str(effect_intent_id),
                "effect_intent_payload_sha256": canonical_sha256(cancelled),
                "logical_step_key": step_row.logical_step_key,
                "owner_principal_id": str(cancellation_owner.principal_id),
                "plan_id": str(chain.plan_id),
                "plan_step_id": str(step_row.plan_step_id),
                "principal_binding_id": str(
                    cancellation_owner.principal_binding_id
                ),
            }
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=operation_id,
                expected_revision=int(checkpoint.revision),
                checkpoint_id=f"checkpoint:{operation_id}:effect-cancelled",
                phase="action_chain_effect_cancelled",
                status="completed",
                event_type="ACTION_CHAIN_EFFECT_CANCELLED",
                occurred_at=now,
                data=cancellation_context,
                operation_id=operation_id,
                deadline=_parse_timestamp(chain.plan.deadline),
                retry_count=int(checkpoint.retry_count),
                last_error_fingerprint=None,
            )
            attention_row = connection.execute(
                sa.select(attention_items)
                .where(
                    attention_items.c.tenant_id == self._scope_value.tenant_id,
                    attention_items.c.installation_id == self._scope_value.installation_id,
                    attention_items.c.attention_id == UUID(chain.attention.attention_id),
                )
                .with_for_update()
            ).one()
            write_action_chain_attention(
                connection,
                self._scope_value,
                chain,
                attention_row,
                state="completed",
                worker_id=None,
                lease_expires_at=None,
                not_before=now,
                now=now,
                reason="Actie door eigenaar geannuleerd.",
                root_event_type="ACTION_CHAIN_EFFECT_CANCELLED",
                root_status="completed",
                transition_context=cancellation_context,
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return CancelledActionChainEffect("cancelled", cancelled)

    def _validate_owner(
        self,
        connection: sa.Connection,
        *,
        episode_id: UUID,
        generation: int,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
    ) -> ActionChainEffectOwner:
        return validate_action_chain_effect_owner(
            connection,
            scope=self._scope_value,
            episode_id=episode_id,
            generation=generation,
            owner_principal_id=owner_principal_id,
            principal_binding_id=principal_binding_id,
            conflict=ActionChainEffectCancellationConflict,
        )

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def validate_action_chain_effect_owner(
    connection: sa.Connection,
    *,
    scope: TimelineScope,
    episode_id: UUID,
    generation: int,
    owner_principal_id: UUID,
    principal_binding_id: UUID,
    conflict: type[ActionChainEffectConflict] = ActionChainEffectCancellationConflict,
) -> ActionChainEffectOwner:
    """Lock and validate the current owner for an effect-control request."""

    generation_exists = connection.scalar(
        sa.select(episode_generations.c.episode_id)
        .where(
            episode_generations.c.tenant_id == scope.tenant_id,
            episode_generations.c.installation_id == scope.installation_id,
            episode_generations.c.episode_id == episode_id,
            episode_generations.c.generation == generation,
            episode_generations.c.state == "current",
        )
        .with_for_update(read=True)
    )
    episode_owner = connection.scalar(
        sa.select(episodes.c.principal_id)
        .where(
            episodes.c.tenant_id == scope.tenant_id,
            episodes.c.installation_id == scope.installation_id,
            episodes.c.episode_id == episode_id,
            episodes.c.current_generation == generation,
            episodes.c.state.not_in(("completed", "cancelled")),
        )
        .with_for_update(read=True)
    )
    binding = connection.execute(
        sa.select(
            principal_bindings.c.principal_id,
            principal_bindings.c.verification_evidence_id,
            principal_bindings.c.external_subject_sha256,
            principal_bindings.c.revision,
            principal_bindings.c.verified_at,
        )
        .where(
            principal_bindings.c.tenant_id == scope.tenant_id,
            principal_bindings.c.principal_binding_id == principal_binding_id,
            principal_bindings.c.principal_id == owner_principal_id,
            principal_bindings.c.status == "verified",
        )
        .with_for_update(read=True)
    ).one_or_none()
    principal = connection.scalar(
        sa.select(principals.c.principal_id)
        .where(
            principals.c.tenant_id == scope.tenant_id,
            principals.c.principal_id == owner_principal_id,
            principals.c.status == "active",
        )
        .with_for_update(read=True)
    )
    if (
        not generation_exists
        or episode_owner != owner_principal_id
        or binding is None
        or binding.principal_id != owner_principal_id
        or binding.verification_evidence_id is None
        or binding.external_subject_sha256 is None
        or binding.verified_at is None
        or principal != owner_principal_id
    ):
        raise conflict("effect cancellation owner authority is not current")
    return ActionChainEffectOwner(
        principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        binding_verification_evidence_id=binding.verification_evidence_id,
        binding_subject_sha256=bytes(binding.external_subject_sha256).hex(),
        binding_revision=int(binding.revision),
        binding_verified_at=binding.verified_at,
    )

def _intent(row: sa.Row) -> EffectIntent:
    try:
        return EffectIntent.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainEffectCancellationConflict("effect intent payload is invalid") from error


def _require_utc_second(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{label} must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "ActionChainEffectOwner",
    "ActionChainEffectCancellationConflict",
    "CancelledActionChainEffect",
    "PostgresActionChainEffectCanceller",
    "validate_action_chain_effect_owner",
]
