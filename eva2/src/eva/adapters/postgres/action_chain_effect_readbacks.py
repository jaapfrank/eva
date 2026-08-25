"""Prepare one authority-separated effect verification read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_readback_validation import (
    accepted_effect,
    effect_readback_thread_id,
    readback_authority,
    verification_rows,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    plan_steps,
    read_intents,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    insert_initial_timeline_thread,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import ReadIntent, ResourceScope


effect_intents = metadata.tables["eva_effects.effect_intents"]


class ActionChainEffectReadbackConflict(RuntimeError):
    """The accepted effect or independent verification authority changed."""


@dataclass(frozen=True, slots=True)
class EffectReadbackAuthority:
    capability_activation_id: UUID
    connector_binding_id: UUID
    resource_scope: ResourceScope


@dataclass(frozen=True, slots=True)
class PreparedActionChainEffectReadback:
    disposition: Literal["prepared", "already_prepared"]
    intent: ReadIntent


class PostgresActionChainEffectReadbackPreparer:
    """Create one immutable read-only verification intent after provider ACK."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("effect readback preparation requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def prepare(
        self,
        effect_intent_id: UUID,
        *,
        authority: EffectReadbackAuthority,
        now: datetime,
    ) -> PreparedActionChainEffectReadback:
        _require_utc_second(now)
        with self._engine.begin() as connection:
            self._scope(connection)
            effect_row = connection.execute(
                sa.select(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id
                    == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id == effect_intent_id,
                )
                .with_for_update()
            ).one_or_none()
            if effect_row is None:
                raise ActionChainEffectReadbackConflict("effect intent is absent")
            step_row = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == effect_row.plan_step_id,
                    plan_steps.c.effect_intent_id == effect_intent_id,
                )
                .with_for_update(read=True)
            ).one()
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            existing = verification_rows(
                connection, self._scope_value, effect_intent_id
            )
            if existing:
                if len(existing) != 1:
                    raise ActionChainEffectReadbackConflict(
                        "effect has multiple verification intents"
                    )
                return PreparedActionChainEffectReadback(
                    "already_prepared", ReadIntent.model_validate(existing[0].payload)
                )

            try:
                effect, attempt, receipt = accepted_effect(
                    connection, self._scope_value, effect_row
                )
                activation, manifest, binding = readback_authority(
                    connection,
                    self._scope_value,
                    activation_id=authority.capability_activation_id,
                    binding_id=authority.connector_binding_id,
                    resource_scope=authority.resource_scope,
                    effect=effect,
                    policy_sha256=chain.plan.policy_sha256,
                )
            except (TypeError, ValueError) as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            deadline = _parse_timestamp(effect.deadline)
            if now < effect_row.updated_at or now >= deadline:
                raise ActionChainEffectReadbackConflict(
                    "effect readback deadline has passed"
                )
            request_payload = {
                "idempotencyKey": effect.idempotency_key,
                "providerResourceId": receipt.provider_resource_id,
                "requestSha256": effect.frozen_payload_sha256,
            }
            query_sha256 = canonical_sha256(request_payload)
            read_intent_id = uuid5(effect_intent_id, "effect-readback-intent:v1")
            intent = ReadIntent(
                schema_version=1,
                contract="ReadIntent/v1",
                read_intent_id=str(read_intent_id),
                episode_id=effect.episode_id,
                generation=effect.generation,
                purpose="effect_verify",
                effect_intent_id=effect.effect_intent_id,
                operation="effect.readback",
                resource_scope=authority.resource_scope.model_dump(mode="json"),
                query_sha256=query_sha256,
                maximum_freshness_ms=0,
                maximum_attempts=2,
                capability_id=manifest.capability_id,
                deadline=effect.deadline,
            )
            connection.execute(
                sa.insert(read_intents).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    read_intent_id=read_intent_id,
                    episode_id=effect_row.episode_id,
                    generation=effect.generation,
                    logical_read_key=f"action-chain:{effect_intent_id}:readback",
                    plan_step_id=effect_row.plan_step_id,
                    purpose=intent.purpose,
                    effect_intent_id=effect_intent_id,
                    circuit_breaker_id=None,
                    operation=intent.operation,
                    resource_scope=intent.resource_scope.model_dump(mode="json"),
                    resource_scope_sha256=bytes.fromhex(
                        intent.resource_scope.scope_sha256
                    ),
                    query_sha256=bytes.fromhex(query_sha256),
                    maximum_freshness_ms=0,
                    maximum_attempts=2,
                    capability_id=intent.capability_id,
                    capability_activation_id=activation.activation_id,
                    deadline=deadline,
                    payload=intent.model_dump(mode="json"),
                    payload_sha256=bytes.fromhex(canonical_sha256(intent)),
                    retention_policy_id=effect_row.retention_policy_id,
                    retention_until=effect_row.retention_until,
                    created_at=now,
                )
            )
            thread_id = effect_readback_thread_id(read_intent_id)
            insert_initial_timeline_thread(
                connection,
                self._scope_value,
                thread_id=thread_id,
                checkpoint_id=f"checkpoint:{thread_id}:prepared",
                occurred_at=now,
                data={
                    "capability_activation_id": str(activation.activation_id),
                    "capability_manifest_id": str(
                        activation.capability_manifest_id
                    ),
                    "connector_binding_id": binding.binding_id,
                    "effect_attempt_id": attempt.effect_attempt_id,
                    "effect_intent_id": effect.effect_intent_id,
                    "effect_receipt_id": receipt.receipt_id,
                    "effect_receipt_payload_sha256": canonical_sha256(receipt),
                    "plan_id": str(step_row.plan_id),
                    "plan_step_id": str(step_row.plan_step_id),
                    "policy_sha256": chain.plan.policy_sha256,
                    "read_intent": intent.model_dump(mode="json"),
                    "read_intent_payload_sha256": canonical_sha256(intent),
                    "request_payload": request_payload,
                    "request_payload_sha256": query_sha256,
                },
                event_type="ACTION_CHAIN_EFFECT_READBACK_INTENT_PREPARED",
                phase="action_chain_effect_readback_intent_prepared",
                status="waiting_for_proof",
                operation_id=thread_id,
                deadline=deadline,
            )
            self._ledger.load_in_transaction(connection, step_row.plan_id)
            return PreparedActionChainEffectReadback("prepared", intent)

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def _require_utc_second(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("now must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError("now must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "ActionChainEffectReadbackConflict",
    "EffectReadbackAuthority",
    "PostgresActionChainEffectReadbackPreparer",
    "PreparedActionChainEffectReadback",
]
