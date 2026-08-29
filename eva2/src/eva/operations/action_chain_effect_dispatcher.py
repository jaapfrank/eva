"""Claim one authorized action-chain effect for exactly one provider call."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy.engine import Engine

from eva.adapters.connectors.process import ConnectorProcessSpec
from eva.adapters.postgres.action_chain_effects import (
    ActionChainEffectConflict,
    EffectStepAuthority,
    PostgresActionChainEffectPreparer,
)
from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    StoredActionChain,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    action_chain_step_operation_id,
    validate_action_chain_requests,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope, ConnectorManifest
from eva.contracts.connector_rpc import (
    ConnectorInitializeRequest,
    ConnectorInvokeRequest,
)
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    EffectAttempt,
    EffectIntent,
)
from eva.operations.action_chain_dispatch_common import (
    commit_provider_call_started,
    load_dispatch_authority_rows,
    load_latest_provider_event,
)
from eva.operations.action_chain_effect_authority import (
    build_action_chain_effect_authority,
    require_existing_action_chain_effect_attempt,
    validate_action_chain_effect_profile,
)
from eva.operations.action_chain_effect_authorizer import (
    PostgresActionChainEffectAuthorizer,
)
from eva.operations.action_chain_provider_registry import (
    ActionProviderProfile,
    SealedActionProviderRegistry,
)


effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_intents = metadata.tables["eva_effects.effect_intents"]


@dataclass(frozen=True, slots=True)
class ClaimedActionChainEffectCall:
    """Only a first committed claim exposes exact provider-call material."""

    disposition: Literal["claimed", "already_started"]
    attempt: EffectAttempt
    process_spec: ConnectorProcessSpec | None
    connector_manifest: ConnectorManifest | None
    initialize: ConnectorInitializeRequest | None
    invoke: ConnectorInvokeRequest | None


class PostgresActionChainEffectDispatcher(PostgresScopedRole):
    """Commit a one-way effect call-start marker before provider I/O."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedActionProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain effect dispatch requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._provider_registry = provider_registry
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)
        self._preparer = PostgresActionChainEffectPreparer(engine, scope, role=role)
        self._authorizer = PostgresActionChainEffectAuthorizer(
            engine,
            scope,
            provider_registry=provider_registry,
            role=role,
        )

    def claim_provider_call(
        self,
        effect_intent_id: UUID,
        *,
        live_catalog: ActionToolCatalog,
        now: datetime,
    ) -> ClaimedActionChainEffectCall:
        """Expose call bytes only after their irreversible marker commits."""

        _require_utc_second(now)
        self._authorizer.authorize_effect(
            effect_intent_id,
            live_catalog=live_catalog,
            now=now,
        )
        with self._engine.begin() as connection:
            self._scope(connection)
            rows = load_dispatch_authority_rows(
                connection,
                self._scope_value,
                intent_table=effect_intents,
                attempt_table=effect_attempts,
                intent_id_name="effect_intent_id",
                intent_id=effect_intent_id,
                lock_intent=True,
            )
            intent_row, step_row, attempt_row = rows.intent, rows.step, rows.attempt
            activation, manifest_row, binding_row = (
                rows.activation,
                rows.manifest,
                rows.binding,
            )

            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            try:
                validate_action_chain_requests(chain.decision, live_catalog)
                intent = EffectIntent.model_validate(intent_row.payload)
                capability_manifest = CapabilityManifest.model_validate(
                    manifest_row.payload
                )
                binding = ConnectorBinding.model_validate(binding_row.payload)
            except (ValueError, ActionChainValidationError) as error:
                raise ActionChainEffectConflict(str(error)) from error
            if live_catalog.authority_snapshot() != chain.authority_snapshot:
                raise ActionChainEffectConflict(
                    "live action catalog differs from admission"
                )
            stored_step = next(
                (
                    item
                    for item in chain.steps
                    if item.plan_step_id == step_row.plan_step_id
                ),
                None,
            )
            action = next(
                (
                    item
                    for item in chain.decision.actions
                    if stored_step is not None
                    and item.action_id == stored_step.logical_step_key
                ),
                None,
            )
            plan_step = next(
                (
                    item
                    for item in chain.plan.steps
                    if stored_step is not None
                    and item.step_id == stored_step.logical_step_key
                ),
                None,
            )
            if (
                stored_step is None
                or action is None
                or plan_step is None
                or action.kind != "propose_effect"
                or plan_step.kind != "effect"
            ):
                raise ActionChainEffectConflict(
                    "authorized effect differs from its plan"
                )
            descriptor = live_catalog.require(action.operation)
            self._preparer._validate_authority(
                connection,
                chain,
                action=action,
                plan_step=plan_step,
                descriptor=descriptor,
                authority=EffectStepAuthority(
                    capability_activation_id=intent_row.capability_activation_id,
                    connector_binding_id=activation.connector_binding_id,
                    resource_scope=intent.resource_scope,
                ),
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
            attempt, capability_grant, credential_grant, _permit = (
                build_action_chain_effect_authority(
                    scope=self._scope_value,
                    chain=chain,
                    action_arguments=action.arguments,
                    intent=intent,
                    profile=profile,
                    capability_manifest=capability_manifest,
                    binding=binding,
                    created_at=attempt_row.created_at,
                    registry=self._provider_registry,
                )
            )
            require_existing_action_chain_effect_attempt(
                attempt_row,
                attempt=attempt,
                capability_grant=capability_grant,
                credential_grant=credential_grant,
            )
            operation_id = action_chain_step_operation_id(
                chain.plan, stored_step.logical_step_key
            )
            latest = load_latest_provider_event(
                connection, self._scope_value, operation_id
            )
            latest_data = _event_data(latest.payload)
            _require_registry_event(
                latest_data,
                profile,
                self._provider_registry,
                intent,
            )
            if latest.event_type == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED":
                return ClaimedActionChainEffectCall(
                    "already_started", attempt, None, None, None, None
                )
            if latest.event_type != "ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED":
                raise ActionChainEffectConflict("effect provider-call state differs")
            deadline = _parse_timestamp(intent.deadline)
            if now < attempt_row.created_at or now >= deadline:
                raise ActionChainEffectConflict(
                    "effect provider-call deadline has passed"
                )
            initialize, invoke = _build_claimed_exchange(
                scope=self._scope_value,
                chain=chain,
                action_arguments=action.arguments,
                attempt=attempt,
                intent=intent,
                profile=profile,
                capability_manifest=capability_manifest,
                binding=binding,
                capability_grant=capability_grant,
                credential_grant=credential_grant,
            )
            commit_provider_call_started(
                connection,
                self._scope_value,
                kind="effect",
                operation_id=operation_id,
                checkpoint_data=latest_data,
                now=now,
                deadline=deadline,
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return ClaimedActionChainEffectCall(
                "claimed",
                attempt,
                profile.process_spec,
                profile.connector_manifest,
                initialize,
                invoke,
            )

def _require_registry_event(
    data: object,
    profile: ActionProviderProfile,
    registry: SealedActionProviderRegistry,
    intent: EffectIntent,
) -> None:
    expected = {
        "idempotency_key": intent.idempotency_key,
        "process_spec_sha256": profile.process_spec_sha256,
        "provider_profile_revision": profile.revision,
        "provider_profile_sha256": profile.profile_sha256,
        "provider_registry_sha256": registry.registry_sha256,
        "release_manifest_sha256": registry.release_manifest_sha256,
        "release_provenance_sha256": registry.provenance_sha256,
        "release_verification_receipt_sha256": (
            registry.release_verification_receipt_sha256
        ),
    }
    if not isinstance(data, dict) or any(
        data.get(key) != value for key, value in expected.items()
    ):
        raise ActionChainEffectConflict("effect provider release authority differs")


def _build_claimed_exchange(
    *,
    scope: TimelineScope,
    chain: StoredActionChain,
    action_arguments: dict[str, object],
    attempt: EffectAttempt,
    intent: EffectIntent,
    profile: ActionProviderProfile,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
) -> tuple[ConnectorInitializeRequest, ConnectorInvokeRequest]:
    initialize_id = str(
        uuid5(UUID(attempt.effect_attempt_id), "connector-initialize:v1")
    )
    correlation_id = str(uuid5(UUID(attempt.effect_attempt_id), "correlation:v1"))
    initialize = ConnectorInitializeRequest.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorInitializeRequest/v1",
            "jsonrpc": "2.0",
            "id": initialize_id,
            "method": "connector.initialize",
            "params": {
                "protocol_version": "EvaConnectorProtocol/v1",
                "request_id": initialize_id,
                "correlation_id": correlation_id,
                "connector_id": binding.provider_id,
                "connector_manifest_sha256": attempt.connector_manifest_sha256,
                "worker_id": profile.worker_id,
                "worker_executable_sha256": capability_manifest.executable_sha256,
                "connector_binding": binding.model_dump(mode="json"),
                "capability_manifest": capability_manifest.model_dump(mode="json"),
                "capability_grant": capability_grant.model_dump(mode="json"),
                "credential_grant": credential_grant.model_dump(mode="json"),
                "deadline": intent.deadline,
            },
        }
    )
    envelope = ConnectorEnvelope.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorEnvelope/v1",
            "installation_id": str(scope.installation_id),
            "tenant_id": str(scope.tenant_id),
            "request_id": intent.idempotency_key,
            "correlation_id": correlation_id,
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "connector_id": binding.provider_id,
            "connector_binding_id": binding.binding_id,
            "capability_id": intent.capability_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "deadline": intent.deadline,
            "request_payload_sha256": intent.frozen_payload_sha256,
            "connector_manifest_sha256": attempt.connector_manifest_sha256,
            "capability_grant_id": capability_grant.grant_id,
            "credential_grant_id": credential_grant.credential_grant_id,
            "request_payload": action_arguments,
        }
    )
    invoke = ConnectorInvokeRequest.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorInvokeRequest/v1",
            "jsonrpc": "2.0",
            "id": intent.idempotency_key,
            "method": "effect.execute",
            "params": envelope.model_dump(mode="json"),
        }
    )
    try:
        initialize.validate_against_manifest(profile.connector_manifest)
        invoke.validate_against_initialize(initialize, profile.connector_manifest)
    except ValueError as error:
        raise ActionChainEffectConflict(str(error)) from error
    if canonical_sha256(envelope) != attempt.request_sha256:
        raise ActionChainEffectConflict(
            "claimed effect request differs from authorization"
        )
    return initialize, invoke


def _event_data(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ActionChainEffectConflict("effect provider timeline payload differs")
    return payload["data"]


def _require_utc_second(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("now must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError("now must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "ClaimedActionChainEffectCall",
    "PostgresActionChainEffectDispatcher",
]
