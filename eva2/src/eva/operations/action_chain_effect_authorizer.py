"""Authorize one provider-bound action-chain effect without provider I/O."""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effects import (
    ActionChainEffectConflict,
    EffectStepAuthority,
    PostgresActionChainEffectPreparer,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    capability_activations,
    connector_bindings,
    plan_steps,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    action_chain_step_operation_id,
    validate_action_chain_requests,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import (
    ConnectorBinding,
    EffectAttempt,
    EffectIntent,
)
from eva.operations.action_chain_effect_authority import (
    build_action_chain_effect_authority,
    effect_dispatch_event_data,
    require_existing_action_chain_effect_attempt,
    validate_action_chain_effect_profile,
)
from eva.operations.action_chain_provider_registry import (
    SealedActionProviderRegistry,
)


effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_intents = metadata.tables["eva_effects.effect_intents"]


@dataclass(frozen=True, slots=True)
class AuthorizedActionChainEffect:
    """One committed effect fence; deliberately exposes no callable bytes."""

    disposition: Literal["authorized", "already_authorized"]
    attempt: EffectAttempt


class PostgresActionChainEffectAuthorizer(PostgresScopedRole):
    """Revalidate live authority and commit one effect dispatch permit."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedActionProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain effect authorization requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._provider_registry = provider_registry
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)
        self._preparer = PostgresActionChainEffectPreparer(engine, scope, role=role)

    def authorize_effect(
        self,
        effect_intent_id: UUID,
        *,
        live_catalog: ActionToolCatalog,
        now: datetime,
    ) -> AuthorizedActionChainEffect:
        """Persist one exact dispatch fence without invoking its provider."""

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
                raise ActionChainEffectConflict(
                    "prepared effect intent is absent in scope"
                )
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
            if step_row is None or step_row.state != "running":
                raise ActionChainEffectConflict(
                    "prepared effect step is no longer running"
                )

            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            stored_step = next(
                (
                    item
                    for item in chain.steps
                    if item.plan_step_id == step_row.plan_step_id
                ),
                None,
            )
            if stored_step is None:
                raise ActionChainEffectConflict(
                    "prepared effect differs from its plan"
                )
            try:
                intent = EffectIntent.model_validate(intent_row.payload)
                validate_action_chain_requests(chain.decision, live_catalog)
            except (ValueError, ActionChainValidationError) as error:
                raise ActionChainEffectConflict(str(error)) from error
            if live_catalog.authority_snapshot() != chain.authority_snapshot:
                raise ActionChainEffectConflict(
                    "live action catalog differs from admission"
                )

            action = next(
                (
                    item
                    for item in chain.decision.actions
                    if item.action_id == stored_step.logical_step_key
                ),
                None,
            )
            plan_step = next(
                (
                    item
                    for item in chain.plan.steps
                    if item.step_id == stored_step.logical_step_key
                ),
                None,
            )
            if (
                action is None
                or plan_step is None
                or action.kind != "propose_effect"
                or plan_step.kind != "effect"
            ):
                raise ActionChainEffectConflict("prepared action is not an effect")
            activation_hint = connection.execute(
                sa.select(capability_activations)
                .where(
                    capability_activations.c.tenant_id
                    == self._scope_value.tenant_id,
                    capability_activations.c.installation_id
                    == self._scope_value.installation_id,
                    capability_activations.c.activation_id
                    == intent_row.capability_activation_id,
                )
                .with_for_update(read=True)
            ).one_or_none()
            if activation_hint is None:
                raise ActionChainEffectConflict(
                    "effect capability activation is absent"
                )
            descriptor = live_catalog.require(action.operation)
            activation, capability_manifest = self._preparer._validate_authority(
                connection,
                chain,
                action=action,
                plan_step=plan_step,
                descriptor=descriptor,
                authority=EffectStepAuthority(
                    capability_activation_id=intent_row.capability_activation_id,
                    connector_binding_id=activation_hint.connector_binding_id,
                    resource_scope=intent.resource_scope,
                ),
            )
            binding = ConnectorBinding.model_validate(
                connection.execute(
                    sa.select(connector_bindings.c.payload).where(
                        connector_bindings.c.tenant_id
                        == self._scope_value.tenant_id,
                        connector_bindings.c.installation_id
                        == self._scope_value.installation_id,
                        connector_bindings.c.binding_id
                        == activation.connector_binding_id,
                    )
                ).scalar_one()
            )
            try:
                profile = self._provider_registry.resolve(
                    activation.capability_manifest_id
                )
            except LookupError as error:
                raise ActionChainEffectConflict(str(error)) from error
            validate_action_chain_effect_profile(
                profile,
                capability_manifest=capability_manifest,
                binding=binding,
                intent=intent,
            )

            existing = connection.execute(
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
            if existing is None and now >= _parse_timestamp(intent.deadline):
                raise ActionChainEffectConflict("effect dispatch deadline has passed")
            created_at = now if existing is None else existing.created_at
            attempt, capability_grant, credential_grant, permit = (
                build_action_chain_effect_authority(
                    scope=self._scope_value,
                    chain=chain,
                    action_arguments=action.arguments,
                    intent=intent,
                    profile=profile,
                    capability_manifest=capability_manifest,
                    binding=binding,
                    created_at=created_at,
                    registry=self._provider_registry,
                )
            )
            if existing is not None:
                require_existing_action_chain_effect_attempt(
                    existing,
                    attempt=attempt,
                    capability_grant=capability_grant,
                    credential_grant=credential_grant,
                )
                if (
                    intent.state != "dispatch_authorized"
                    or intent_row.state != "dispatch_authorized"
                    or int(intent_row.revision) != 2
                ):
                    raise ActionChainEffectConflict(
                        "authorized effect intent differs"
                    )
                return AuthorizedActionChainEffect("already_authorized", attempt)
            if (
                intent.state != "prepared"
                or intent_row.state != "prepared"
                or int(intent_row.revision) != 1
            ):
                raise ActionChainEffectConflict("effect intent is not prepared")

            connection.execute(
                sa.insert(effect_attempts).values(
                    tenant_id=self._scope_value.tenant_id,
                    installation_id=self._scope_value.installation_id,
                    effect_attempt_id=UUID(attempt.effect_attempt_id),
                    effect_intent_id=effect_intent_id,
                    ordinal=attempt.ordinal,
                    state=attempt.state,
                    dispatch_permit_sha256=bytes.fromhex(
                        attempt.dispatch_permit_sha256 or ""
                    ),
                    capability_grant_id=UUID(capability_grant.grant_id),
                    capability_grant_payload=capability_grant.model_dump(mode="json"),
                    capability_grant_sha256=bytes.fromhex(
                        canonical_sha256(capability_grant)
                    ),
                    capability_grant_nonce=capability_grant.nonce,
                    capability_grant_deadline=_parse_timestamp(
                        capability_grant.deadline
                    ),
                    credential_grant_id=UUID(credential_grant.credential_grant_id),
                    credential_grant_payload=credential_grant.model_dump(mode="json"),
                    credential_grant_sha256=bytes.fromhex(
                        canonical_sha256(credential_grant)
                    ),
                    credential_grant_nonce=credential_grant.nonce,
                    credential_grant_deadline=_parse_timestamp(
                        credential_grant.deadline
                    ),
                    connector_manifest_sha256=bytes.fromhex(
                        attempt.connector_manifest_sha256
                    ),
                    request_sha256=bytes.fromhex(attempt.request_sha256),
                    response_sha256=None,
                    error_class="none",
                    transport_evidence_id=None,
                    object_store_id=None,
                    object_candidate_id=None,
                    object_candidate_ciphertext_sha256=None,
                    object_candidate_envelope_byte_length=None,
                    object_candidate_temp_name_sha256=None,
                    object_candidate_state=None,
                    deadline=_parse_timestamp(attempt.deadline),
                    payload=attempt.model_dump(mode="json"),
                    payload_sha256=bytes.fromhex(canonical_sha256(attempt)),
                    revision=1,
                    retention_policy_id=chain.retention_policy_id,
                    retention_until=chain.retention_until,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            authorized_intent = intent.model_copy(
                update={"state": "dispatch_authorized"}
            )
            intent_update = connection.execute(
                sa.update(effect_intents)
                .where(
                    effect_intents.c.tenant_id == self._scope_value.tenant_id,
                    effect_intents.c.installation_id
                    == self._scope_value.installation_id,
                    effect_intents.c.effect_intent_id == effect_intent_id,
                    effect_intents.c.state == "prepared",
                    effect_intents.c.revision == 1,
                )
                .values(
                    state="dispatch_authorized",
                    payload=authorized_intent.model_dump(mode="json"),
                    payload_sha256=bytes.fromhex(canonical_sha256(authorized_intent)),
                    revision=2,
                    updated_at=created_at,
                )
            )
            if intent_update.rowcount != 1:
                raise ActionChainEffectConflict(
                    "effect intent changed during authorization"
                )

            operation_id = action_chain_step_operation_id(
                chain.plan, stored_step.logical_step_key
            )
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id
                    == self._scope_value.tenant_id,
                    operational_checkpoints.c.installation_id
                    == self._scope_value.installation_id,
                    operational_checkpoints.c.thread_id == operation_id,
                )
                .with_for_update()
            ).one()
            transition_timeline_thread(
                connection,
                self._scope_value,
                thread_id=operation_id,
                expected_revision=int(checkpoint.revision),
                checkpoint_id=f"checkpoint:{operation_id}:effect-dispatch-authorized",
                phase="action_chain_effect_dispatch_authorized",
                status="waiting_for_proof",
                event_type="ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED",
                occurred_at=created_at,
                data=effect_dispatch_event_data(
                    attempt,
                    capability_grant=capability_grant,
                    credential_grant=credential_grant,
                    permit=permit,
                    profile=profile,
                    registry=self._provider_registry,
                    authorized_intent=authorized_intent,
                ),
                operation_id=operation_id,
                deadline=_parse_timestamp(chain.plan.deadline),
                retry_count=int(checkpoint.retry_count),
                last_error_fingerprint=(
                    None
                    if checkpoint.last_error_fingerprint is None
                    else bytes(checkpoint.last_error_fingerprint).hex()
                ),
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return AuthorizedActionChainEffect("authorized", attempt)





__all__ = [
    "AuthorizedActionChainEffect",
    "PostgresActionChainEffectAuthorizer",
]
