"""Authorize one effect-verification read without provider I/O."""

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
    readback_authority,
)
from eva.adapters.postgres.action_chain_effect_readbacks import (
    ActionChainEffectReadbackConflict,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    plan_steps,
    read_intents,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import ReadAttempt, ReadIntent
from eva.operations.action_chain_provider_registry import SealedReadProviderRegistry
from eva.operations.action_chain_read_authorizer import (
    build_action_chain_read_authority,
    read_dispatch_event_data,
    require_existing_action_chain_read_attempt,
    validate_action_chain_read_profile,
)


effect_intents = metadata.tables["eva_effects.effect_intents"]
read_attempts = metadata.tables["eva_effects.read_attempts"]


@dataclass(frozen=True, slots=True)
class AuthorizedActionChainEffectReadback:
    """One committed readback fence with no callable provider material."""

    disposition: Literal["authorized", "already_authorized"]
    attempt: ReadAttempt


class PostgresActionChainEffectReadbackAuthorizer:
    """Revalidate independent read authority and persist one dispatch fence."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedReadProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("effect readback authorization requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._provider_registry = provider_registry
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)

    def authorize_readback(
        self,
        read_intent_id: UUID,
        *,
        now: datetime,
    ) -> AuthorizedActionChainEffectReadback:
        """Commit one release-bound read attempt without returning call bytes."""

        _require_utc_second(now)
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(read_intents)
                .where(
                    read_intents.c.tenant_id == self._scope_value.tenant_id,
                    read_intents.c.installation_id
                    == self._scope_value.installation_id,
                    read_intents.c.read_intent_id == read_intent_id,
                    read_intents.c.purpose == "effect_verify",
                )
            ).one_or_none()
            if intent_row is None or intent_row.effect_intent_id is None:
                raise ActionChainEffectReadbackConflict(
                    "prepared effect readback is absent in scope"
                )
            effect_row = connection.execute(
                sa.select(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id
                    == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id
                    == intent_row.effect_intent_id,
                    effect_intents.c.plan_step_id == intent_row.plan_step_id,
                )
                .with_for_update()
            ).one_or_none()
            step_row = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id
                    == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == intent_row.plan_step_id,
                    plan_steps.c.effect_intent_id == intent_row.effect_intent_id,
                )
                .with_for_update()
            ).one_or_none()
            if (
                effect_row is None
                or step_row is None
                or step_row.state != "running"
            ):
                raise ActionChainEffectReadbackConflict(
                    "effect readback step is no longer running"
                )

            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            try:
                intent = ReadIntent.model_validate(intent_row.payload)
                effect, _effect_attempt, _receipt = accepted_effect(
                    connection, self._scope_value, effect_row, lock=True
                )
                activation, manifest, binding = readback_authority(
                    connection,
                    self._scope_value,
                    activation_id=intent_row.capability_activation_id,
                    binding_id=UUID(effect.connector_binding_id),
                    resource_scope=intent.resource_scope,
                    effect=effect,
                    policy_sha256=chain.plan.policy_sha256,
                    lock=True,
                )
            except (TypeError, ValueError) as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            if (
                intent.purpose != "effect_verify"
                or intent.effect_intent_id != str(effect_row.effect_intent_id)
                or intent.operation != "effect.readback"
            ):
                raise ActionChainEffectReadbackConflict(
                    "prepared effect readback binding differs"
                )
            try:
                profile = self._provider_registry.resolve(
                    activation.capability_manifest_id
                )
            except LookupError as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            try:
                validate_action_chain_read_profile(
                    profile,
                    capability_manifest=manifest,
                    binding=binding,
                    intent=intent,
                )
            except RuntimeError as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            request_payload = prepared_effect_readback_request_payload(
                connection, self._scope_value, intent
            )

            existing = connection.execute(
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
            deadline = _parse_timestamp(intent.deadline)
            if existing is None and now >= deadline:
                raise ActionChainEffectReadbackConflict(
                    "effect readback dispatch deadline has passed"
                )
            created_at = now if existing is None else existing.created_at
            try:
                attempt, capability_grant, credential_grant = (
                    build_action_chain_read_authority(
                        scope=self._scope_value,
                        chain=chain,
                        action_arguments=request_payload,
                        intent=intent,
                        profile=profile,
                        capability_manifest=manifest,
                        binding=binding,
                        created_at=created_at,
                    )
                )
            except RuntimeError as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            if existing is not None:
                try:
                    require_existing_action_chain_read_attempt(
                        existing,
                        attempt=attempt,
                        capability_grant=capability_grant,
                        credential_grant=credential_grant,
                    )
                except RuntimeError as error:
                    raise ActionChainEffectReadbackConflict(str(error)) from error
                return AuthorizedActionChainEffectReadback(
                    "already_authorized", attempt
                )

            connection.execute(
                sa.insert(read_attempts).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    read_attempt_id=UUID(attempt.read_attempt_id),
                    read_intent_id=read_intent_id,
                    ordinal=attempt.ordinal,
                    state=attempt.state,
                    capability_grant_id=UUID(capability_grant.grant_id),
                    capability_grant_payload=capability_grant.model_dump(mode="json"),
                    capability_grant_sha256=bytes.fromhex(
                        canonical_sha256(capability_grant)
                    ),
                    capability_grant_nonce=capability_grant.nonce,
                    capability_grant_deadline=deadline,
                    credential_grant_id=UUID(
                        credential_grant.credential_grant_id
                    ),
                    credential_grant_payload=credential_grant.model_dump(mode="json"),
                    credential_grant_sha256=bytes.fromhex(
                        canonical_sha256(credential_grant)
                    ),
                    credential_grant_nonce=credential_grant.nonce,
                    credential_grant_deadline=deadline,
                    connector_manifest_sha256=bytes.fromhex(
                        attempt.connector_manifest_sha256
                    ),
                    request_sha256=bytes.fromhex(attempt.request_sha256),
                    response_sha256=None,
                    deadline=deadline,
                    payload=attempt.model_dump(mode="json"),
                    payload_sha256=bytes.fromhex(canonical_sha256(attempt)),
                    revision=1,
                    retention_policy_id=chain.retention_policy_id,
                    retention_until=chain.retention_until,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            thread_id = effect_readback_thread_id(read_intent_id)
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                )
                .with_for_update()
            ).one()
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=thread_id,
                expected_revision=int(checkpoint.revision),
                checkpoint_id=f"checkpoint:{thread_id}:read-dispatch-authorized",
                phase="action_chain_read_dispatch_authorized",
                status="waiting_for_proof",
                event_type="ACTION_CHAIN_READ_DISPATCH_AUTHORIZED",
                occurred_at=created_at,
                data=read_dispatch_event_data(
                    attempt,
                    capability_grant=capability_grant,
                    credential_grant=credential_grant,
                    profile=profile,
                    registry_sha256=self._provider_registry.registry_sha256,
                    release_manifest_sha256=(
                        self._provider_registry.release_manifest_sha256
                    ),
                    provenance_sha256=self._provider_registry.provenance_sha256,
                    release_verification_receipt_sha256=(
                        self._provider_registry.release_verification_receipt_sha256
                    ),
                ),
                operation_id=thread_id,
                deadline=deadline,
                retry_count=int(checkpoint.retry_count),
                last_error_fingerprint=(
                    None
                    if checkpoint.last_error_fingerprint is None
                    else bytes(checkpoint.last_error_fingerprint).hex()
                ),
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return AuthorizedActionChainEffectReadback("authorized", attempt)

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def prepared_effect_readback_request_payload(
    connection: sa.Connection,
    scope: TimelineScope,
    intent: ReadIntent,
) -> dict[str, object]:
    thread_id = effect_readback_thread_id(UUID(intent.read_intent_id))
    event = connection.execute(
        sa.select(operational_events).where(
            operational_events.c.tenant_id == scope.tenant_id,
            operational_events.c.installation_id == scope.installation_id,
            operational_events.c.thread_id == thread_id,
            operational_events.c.sequence == 1,
            operational_events.c.event_type
            == "ACTION_CHAIN_EFFECT_READBACK_INTENT_PREPARED",
        )
    ).one_or_none()
    data = None if event is None else event.payload.get("data")
    payload = None if not isinstance(data, dict) else data.get("request_payload")
    if (
        not isinstance(payload, dict)
        or canonical_sha256(payload) != intent.query_sha256
        or data.get("read_intent") != intent.model_dump(mode="json")
        or data.get("read_intent_payload_sha256") != canonical_sha256(intent)
    ):
        raise ActionChainEffectReadbackConflict(
            "prepared effect readback request differs"
        )
    return dict(payload)


def _require_utc_second(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("now must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError("now must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "AuthorizedActionChainEffectReadback",
    "PostgresActionChainEffectReadbackAuthorizer",
]
