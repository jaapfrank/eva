"""Persist one canonical effect readback from retained provider evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_readback_validation import (
    accepted_effect,
    effect_readback_thread_id,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    plan_steps,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_events,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector_rpc import ConnectorInvokeSuccess
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import (
    EffectReadback,
    ReadAttempt,
    ReadIntent,
    ReadObservation,
)
from eva.application.action_chain_effect_readback_results import (
    project_action_chain_effect_readback,
)


effect_intents = metadata.tables["eva_effects.effect_intents"]
effect_readbacks = metadata.tables["eva_effects.effect_readbacks"]
evidence_envelopes = metadata.tables["eva_evidence.evidence_envelopes"]
read_attempts = metadata.tables["eva_effects.read_attempts"]
read_intents = metadata.tables["eva_effects.read_intents"]
read_observations = metadata.tables["eva_effects.read_observations"]


class ActionChainEffectReadbackResultConflict(RuntimeError):
    """The stored verification is absent, non-terminal, or not physical proof."""


@dataclass(frozen=True, slots=True)
class StoredActionChainEffectReadback:
    """One physical proof is durable; replay returns the exact same contract."""

    disposition: Literal["recorded", "already_recorded"]
    readback: EffectReadback


class PostgresActionChainEffectReadbackResultStore(PostgresScopedRole):
    """Materialize immutable EffectReadback/v1 without another provider call."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("effect readback result storage requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def record(self, read_intent_id: UUID) -> StoredActionChainEffectReadback:
        """Project only retained bytes and insert exactly one immutable proof."""

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
                raise ActionChainEffectReadbackResultConflict(
                    "effect verification intent is absent"
                )
            try:
                intent = ReadIntent.model_validate(intent_row.payload)
            except ValueError as error:
                raise ActionChainEffectReadbackResultConflict(
                    "effect verification intent is invalid"
                ) from error
            if (
                intent.purpose != "effect_verify"
                or intent.operation != "effect.readback"
                or intent.effect_intent_id is None
                or intent.read_intent_id != str(read_intent_id)
            ):
                raise ActionChainEffectReadbackResultConflict(
                    "read intent is not effect verification"
                )

            attempt_row = connection.execute(
                sa.select(read_attempts)
                .where(
                    read_attempts.c.tenant_id == self._scope_value.tenant_id,
                    read_attempts.c.installation_id
                    == self._scope_value.installation_id,
                    read_attempts.c.read_intent_id == read_intent_id,
                    read_attempts.c.ordinal == 1,
                )
                .with_for_update()
            ).one_or_none()
            if attempt_row is None:
                raise ActionChainEffectReadbackResultConflict(
                    "effect verification attempt is absent"
                )
            step_row = connection.execute(
                sa.select(plan_steps).where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == intent_row.plan_step_id,
                    plan_steps.c.effect_intent_id == intent_row.effect_intent_id,
                )
            ).one()
            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)

            existing = connection.execute(
                sa.select(effect_readbacks).where(
                    effect_readbacks.c.tenant_id == self._scope_value.tenant_id,
                    effect_readbacks.c.installation_id
                    == self._scope_value.installation_id,
                    effect_readbacks.c.effect_intent_id
                    == intent_row.effect_intent_id,
                )
            ).all()
            if existing:
                if len(existing) != 1:
                    raise ActionChainEffectReadbackResultConflict(
                        "effect has multiple physical readbacks"
                    )
                return StoredActionChainEffectReadback(
                    "already_recorded",
                    EffectReadback.model_validate(existing[0].payload),
                )

            try:
                attempt = ReadAttempt.model_validate(attempt_row.payload)
            except ValueError as error:
                raise ActionChainEffectReadbackResultConflict(
                    "effect verification attempt is invalid"
                ) from error
            if attempt.state != "observed":
                raise ActionChainEffectReadbackResultConflict(
                    "effect verification has no observed provider state"
                )

            effect_row = connection.execute(
                sa.select(effect_intents).where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id
                    == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id
                    == intent_row.effect_intent_id,
                )
            ).one()
            observation_row = connection.execute(
                sa.select(read_observations).where(
                    read_observations.c.tenant_id == self._scope_value.tenant_id,
                    read_observations.c.installation_id
                    == self._scope_value.installation_id,
                    read_observations.c.read_attempt_id
                    == attempt_row.read_attempt_id,
                )
            ).one()
            latest = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id
                    == effect_readback_thread_id(read_intent_id),
                )
                .order_by(operational_events.c.sequence.desc())
                .limit(1)
            ).one()
            data = latest.payload.get("data")
            if latest.event_type != "ACTION_CHAIN_READ_OBSERVED" or not isinstance(
                data, dict
            ):
                raise ActionChainEffectReadbackResultConflict(
                    "effect verification timeline is not observed"
                )
            observation = ReadObservation.model_validate(observation_row.payload)
            evidence_row = connection.execute(
                sa.select(evidence_envelopes).where(
                    evidence_envelopes.c.tenant_id
                    == self._scope_value.tenant_id,
                    evidence_envelopes.c.installation_id
                    == self._scope_value.installation_id,
                    evidence_envelopes.c.evidence_id
                    == UUID(observation.evidence_ref),
                )
            ).one()
            try:
                effect, _effect_attempt, receipt = accepted_effect(
                    connection, self._scope_value, effect_row, lock=True
                )
                evidence = EvidenceEnvelopeV2.model_validate(evidence_row.payload)
                provider_result = ConnectorInvokeSuccess.model_validate(
                    data.get("provider_result")
                )
                projected = project_action_chain_effect_readback(
                    effect=effect,
                    receipt=receipt,
                    read_intent=intent,
                    read_attempt=attempt,
                    observation=observation,
                    evidence=evidence,
                    provider_result=provider_result,
                )
            except (TypeError, ValueError) as error:
                raise ActionChainEffectReadbackResultConflict(
                    "stored effect verification cannot become physical proof"
                ) from error

            readback = projected.readback
            connection.execute(
                sa.insert(effect_readbacks).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    readback_id=UUID(readback.readback_id),
                    effect_intent_id=UUID(readback.effect_intent_id),
                    read_intent_id=UUID(readback.read_intent_id),
                    read_attempt_id=UUID(readback.read_attempt_id),
                    provider_resource_id=readback.provider_resource_id,
                    idempotency_key=readback.idempotency_key,
                    request_sha256=bytes.fromhex(readback.request_sha256),
                    observed_at=_parse_timestamp(readback.observed_at),
                    state_sha256=bytes.fromhex(readback.state_sha256),
                    match_status=readback.match_status,
                    evidence_id=UUID(readback.evidence_ref),
                    payload=readback.model_dump(mode="json"),
                    payload_sha256=bytes.fromhex(canonical_sha256(readback)),
                    retention_policy_id=chain.retention_policy_id,
                    retention_until=chain.retention_until,
                )
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return StoredActionChainEffectReadback("recorded", readback)

def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "ActionChainEffectReadbackResultConflict",
    "PostgresActionChainEffectReadbackResultStore",
    "StoredActionChainEffectReadback",
]
