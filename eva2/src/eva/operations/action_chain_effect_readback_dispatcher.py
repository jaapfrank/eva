"""Claim one authorized effect readback for exactly one provider call."""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_now_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.connectors.process import ConnectorProcessSpec
from eva.adapters.postgres.action_chain_effect_readback_validation import (
    accepted_effect,
    effect_readback_thread_id,
    readback_authority,
)
from eva.adapters.postgres.action_chain_effect_readbacks import (
    ActionChainEffectReadbackConflict,
)
from eva.adapters.postgres.action_chain_ledger import (
    plan_steps,
    read_intents,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.contracts.connector import ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
)
from eva.contracts.execution import ReadAttempt, ReadIntent
from eva.operations.action_chain_effect_readback_authorizer import (
    PostgresActionChainEffectReadbackAuthorizer,
    prepared_effect_readback_request_payload,
)
from eva.operations.action_chain_dispatch_common import (
    PostgresActionChainProviderDispatcher,
)
from eva.operations.action_chain_read_authorizer import (
    build_action_chain_read_authority,
    require_existing_action_chain_read_attempt,
    validate_action_chain_read_profile,
)
from eva.operations.action_chain_read_dispatcher import (
    build_claimed_action_chain_read_exchange,
    require_action_chain_read_registry_event,
)


effect_intents = metadata.tables["eva_effects.effect_intents"]
read_attempts = metadata.tables["eva_effects.read_attempts"]


@dataclass(frozen=True, slots=True)
class ClaimedActionChainEffectReadbackCall:
    """Only the first claim carries an executable sealed provider exchange."""

    disposition: Literal["claimed", "already_started"]
    attempt: ReadAttempt
    process_spec: ConnectorProcessSpec | None
    connector_manifest: ConnectorManifest | None
    initialize: ConnectorInitializeRequest | None
    invoke: ConnectorInvokeRequest | None


class PostgresActionChainEffectReadbackDispatcher(
    PostgresActionChainProviderDispatcher
):
    """Commit one call-start marker before releasing readback call material."""

    _authorizer_factory = PostgresActionChainEffectReadbackAuthorizer
    _postgres_error = "effect readback dispatch requires PostgreSQL"

    def claim_provider_call(
        self,
        read_intent_id: UUID,
        *,
        now: datetime,
    ) -> ClaimedActionChainEffectReadbackCall:
        """Release call material only after the durable start claim commits."""

        _require_utc_second(now)
        self._authorizer.authorize_readback(read_intent_id, now=now)
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(read_intents).where(
                    read_intents.c.tenant_id == self._scope_value.tenant_id,
                    read_intents.c.installation_id == self._scope_value.installation_id,
                    read_intents.c.read_intent_id == read_intent_id,
                    read_intents.c.purpose == "effect_verify",
                )
            ).one_or_none()
            if intent_row is None or intent_row.effect_intent_id is None:
                raise ActionChainEffectReadbackConflict(
                    "authorized effect readback is absent in scope"
                )
            effect_row = connection.execute(
                sa.select(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id
                    == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id == intent_row.effect_intent_id,
                    effect_intents.c.plan_step_id == intent_row.plan_step_id,
                )
                .with_for_update(read=True)
            ).one_or_none()
            step_row = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == intent_row.plan_step_id,
                    plan_steps.c.effect_intent_id == intent_row.effect_intent_id,
                )
                .with_for_update(read=True)
            ).one_or_none()
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
            if (
                effect_row is None
                or step_row is None
                or step_row.state != "running"
                or attempt_row is None
            ):
                raise ActionChainEffectReadbackConflict(
                    "effect readback call is no longer runnable"
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
                    "authorized effect readback binding differs"
                )
            try:
                profile = self._provider_registry.resolve(
                    activation.capability_manifest_id
                )
                validate_action_chain_read_profile(
                    profile,
                    capability_manifest=manifest,
                    binding=binding,
                    intent=intent,
                )
            except (LookupError, RuntimeError) as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            request_payload = prepared_effect_readback_request_payload(
                connection, self._scope_value, intent
            )
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
                        created_at=attempt_row.created_at,
                    )
                )
                require_existing_action_chain_read_attempt(
                    attempt_row,
                    attempt=attempt,
                    capability_grant=capability_grant,
                    credential_grant=credential_grant,
                )
            except RuntimeError as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error

            thread_id = effect_readback_thread_id(read_intent_id)
            latest = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.thread_id == thread_id,
                )
                .order_by(operational_events.c.sequence.desc())
                .limit(1)
            ).one()
            latest_data = _event_data(latest.payload)
            try:
                require_action_chain_read_registry_event(
                    latest_data,
                    profile,
                    self._provider_registry,
                )
            except RuntimeError as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            if latest.event_type == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED":
                return ClaimedActionChainEffectReadbackCall(
                    "already_started", attempt, None, None, None, None
                )
            if latest.event_type != "ACTION_CHAIN_READ_DISPATCH_AUTHORIZED":
                raise ActionChainEffectReadbackConflict(
                    "effect readback provider-call state differs"
                )
            deadline = _parse_timestamp(intent.deadline)
            if now < attempt_row.created_at or now >= deadline:
                raise ActionChainEffectReadbackConflict(
                    "effect readback provider-call deadline has passed"
                )
            try:
                initialize, invoke = build_claimed_action_chain_read_exchange(
                    scope=self._scope_value,
                    chain=chain,
                    action_arguments=request_payload,
                    attempt=attempt,
                    intent=intent,
                    profile=profile,
                    capability_manifest=manifest,
                    binding=binding,
                    capability_grant=capability_grant,
                    credential_grant=credential_grant,
                )
            except RuntimeError as error:
                raise ActionChainEffectReadbackConflict(str(error)) from error
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == thread_id,
                )
                .with_for_update()
            ).one()
            event_data = dict(latest_data)
            event_data["provider_call_started_at"] = _format_timestamp(now)
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=thread_id,
                expected_revision=int(checkpoint.revision),
                checkpoint_id=f"checkpoint:{thread_id}:read-provider-call-started",
                phase="action_chain_read_provider_call_started",
                status="waiting_for_proof",
                event_type="ACTION_CHAIN_READ_PROVIDER_CALL_STARTED",
                occurred_at=now,
                data=event_data,
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
            return ClaimedActionChainEffectReadbackCall(
                "claimed",
                attempt,
                profile.process_spec,
                profile.connector_manifest,
                initialize,
                invoke,
            )


def _event_data(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ActionChainEffectReadbackConflict(
            "effect readback provider timeline payload differs"
        )
    return payload["data"]


def _format_timestamp(value: datetime) -> str:
    _require_utc_second(value)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "ClaimedActionChainEffectReadbackCall",
    "PostgresActionChainEffectReadbackDispatcher",
]
