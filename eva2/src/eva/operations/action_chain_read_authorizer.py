"""Authorize one provider-bound action-chain read without provider I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    StoredActionChain,
    capability_activations,
    connector_bindings,
    plan_steps,
    read_intents,
)
from eva.operations.action_chain_provider_registry import (
    ReadProviderProfile,
    SealedReadProviderRegistry,
)
from eva.adapters.postgres.action_chain_reads import (
    ActionChainReadConflict,
    PostgresActionChainReadPreparer,
    ReadStepAuthority,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    transition_timeline_thread,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    action_chain_step_operation_id,
    validate_action_chain_requests,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    ReadAttempt,
    ReadIntent,
)


read_attempts = metadata.tables["eva_effects.read_attempts"]


@dataclass(frozen=True, slots=True)
class AuthorizedActionChainRead:
    """One committed dispatch fence; deliberately exposes no RPC exchange."""

    disposition: Literal["authorized", "already_authorized"]
    attempt: ReadAttempt


class PostgresActionChainReadAuthorizer:
    """Resolve host authority and commit one fence before provider I/O."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedReadProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
    ) -> None:
        if engine.url.get_backend_name() != "postgresql":
            raise ValueError("action-chain read authorization requires PostgreSQL")
        self._engine = engine
        self._scope_value = scope
        self._role = role
        self._provider_registry = provider_registry
        self._ledger = PostgresActionChainLedger(engine, scope, role=role)
        self._preparer = PostgresActionChainReadPreparer(engine, scope, role=role)

    def authorize_read(
        self,
        read_intent_id: UUID,
        *,
        live_catalog: ActionToolCatalog,
        now: datetime,
    ) -> AuthorizedActionChainRead:
        """Persist a release-resolved fence without returning callable bytes."""

        _require_utc_second(now, "now")
        with self._engine.begin() as connection:
            self._scope(connection)
            intent_row = connection.execute(
                sa.select(read_intents).where(
                    read_intents.c.tenant_id == self._scope_value.tenant_id,
                    read_intents.c.installation_id == self._scope_value.installation_id,
                    read_intents.c.read_intent_id == read_intent_id,
                )
            ).one_or_none()
            if intent_row is None:
                raise ActionChainReadConflict("prepared read intent is absent in scope")
            step_row = connection.execute(
                sa.select(plan_steps)
                .where(
                    plan_steps.c.tenant_id == self._scope_value.tenant_id,
                    plan_steps.c.installation_id == self._scope_value.installation_id,
                    plan_steps.c.plan_step_id == intent_row.plan_step_id,
                    plan_steps.c.read_intent_id == read_intent_id,
                )
                .with_for_update()
            ).one_or_none()
            if step_row is None or step_row.state != "running":
                raise ActionChainReadConflict("prepared read step is no longer running")

            chain = self._ledger.load_in_transaction(connection, step_row.plan_id)
            stored_step = next(
                (
                    item
                    for item in chain.steps
                    if item.plan_step_id == step_row.plan_step_id
                    and item.read_intent_id == read_intent_id
                ),
                None,
            )
            if stored_step is None:
                raise ActionChainReadConflict("prepared read differs from its plan")
            try:
                intent = ReadIntent.model_validate(intent_row.payload)
                validate_action_chain_requests(chain.decision, live_catalog)
            except (ValueError, ActionChainValidationError) as error:
                raise ActionChainReadConflict(str(error)) from error
            if live_catalog.authority_snapshot() != chain.authority_snapshot:
                raise ActionChainReadConflict(
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
            if action is None or plan_step is None or action.kind != "request_read":
                raise ActionChainReadConflict("prepared action is not a read")
            activation_hint = connection.execute(
                sa.select(capability_activations)
                .where(
                    capability_activations.c.tenant_id == self._scope_value.tenant_id,
                    capability_activations.c.installation_id
                    == self._scope_value.installation_id,
                    capability_activations.c.activation_id
                    == intent_row.capability_activation_id,
                )
                .with_for_update(read=True)
            ).one_or_none()
            if activation_hint is None:
                raise ActionChainReadConflict("read capability activation is absent")
            descriptor = live_catalog.require(action.operation)
            activation, capability_manifest = self._preparer._validate_authority(
                connection,
                chain,
                action=action,
                plan_step=plan_step,
                descriptor=descriptor,
                authority=ReadStepAuthority(
                    capability_activation_id=intent_row.capability_activation_id,
                    connector_binding_id=activation_hint.connector_binding_id,
                    resource_scope=intent.resource_scope,
                ),
            )
            binding = ConnectorBinding.model_validate(
                connection.execute(
                    sa.select(connector_bindings.c.payload).where(
                        connector_bindings.c.tenant_id == self._scope_value.tenant_id,
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
                raise ActionChainReadConflict(str(error)) from error
            validate_action_chain_read_profile(
                profile,
                capability_manifest=capability_manifest,
                binding=binding,
                intent=intent,
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
            if existing is None and now >= _parse_timestamp(intent.deadline):
                raise ActionChainReadConflict("read dispatch deadline has passed")
            created_at = now if existing is None else existing.created_at
            attempt, capability_grant, credential_grant = (
                build_action_chain_read_authority(
                    scope=self._scope_value,
                    chain=chain,
                    action_arguments=action.arguments,
                    intent=intent,
                    profile=profile,
                    capability_manifest=capability_manifest,
                    binding=binding,
                    created_at=created_at,
                )
            )
            if existing is not None:
                require_existing_action_chain_read_attempt(
                    existing,
                    attempt=attempt,
                    capability_grant=capability_grant,
                    credential_grant=credential_grant,
                )
                return AuthorizedActionChainRead("already_authorized", attempt)

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
            operation_id = action_chain_step_operation_id(
                chain.plan, stored_step.logical_step_key
            )
            checkpoint = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    operational_checkpoints.c.tenant_id == self._scope_value.tenant_id,
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
                checkpoint_id=f"checkpoint:{operation_id}:read-dispatch-authorized",
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
            return AuthorizedActionChainRead("authorized", attempt)

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def validate_action_chain_read_profile(
    profile: ReadProviderProfile,
    *,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    intent: ReadIntent,
) -> None:
    connector_manifest = profile.connector_manifest
    operation = next(
        (
            item
            for item in connector_manifest.operations
            if item.operation == intent.operation
        ),
        None,
    )
    if (
        connector_manifest.connector_id != binding.provider_id
        or connector_manifest.executable_sha256 != capability_manifest.executable_sha256
        or connector_manifest.sandbox_profile_sha256
        != capability_manifest.sandbox_profile_sha256
        or operation is None
        or operation.input_schema_sha256 != capability_manifest.input_schema_sha256
        or operation.output_schema_sha256 != capability_manifest.output_schema_sha256
        or operation.writes_provider_state
        or operation.external_calls_per_invocation != 1
    ):
        raise ActionChainReadConflict("sealed read provider profile differs")


def build_action_chain_read_authority(
    *,
    scope: TimelineScope,
    chain: StoredActionChain,
    action_arguments: dict[str, object],
    intent: ReadIntent,
    profile: ReadProviderProfile,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    created_at: datetime,
) -> tuple[ReadAttempt, CapabilityGrant, CredentialGrant]:
    if _parse_timestamp(profile.connector_manifest.issued_at) > created_at:
        raise ActionChainReadConflict("connector manifest was issued in the future")
    attempt_id = uuid5(UUID(intent.read_intent_id), "read-attempt:1")
    capability_grant = CapabilityGrant.model_validate(
        {
            "schema_version": 1,
            "contract": "CapabilityGrant/v1",
            "grant_id": str(uuid5(attempt_id, "capability-grant:v1")),
            "tenant_id": str(scope.tenant_id),
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "capability_id": intent.capability_id,
            "connector_binding_id": binding.binding_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "authorized_payload_sha256": intent.query_sha256,
            "policy_sha256": chain.plan.policy_sha256,
            "nonce": str(uuid5(attempt_id, "capability-nonce:v1")),
            "issued_at": _format_timestamp(created_at),
            "deadline": intent.deadline,
        }
    )
    credential_grant = CredentialGrant.model_validate(
        {
            "schema_version": 1,
            "contract": "CredentialGrant/v1",
            "credential_grant_id": str(uuid5(attempt_id, "credential-grant:v1")),
            "connector_binding_id": binding.binding_id,
            "model_provider_profile_id": None,
            "worker_id": profile.worker_id,
            "executable_sha256": capability_manifest.executable_sha256,
            "provider_scopes": capability_manifest.required_provider_scopes,
            "secret_descriptor_id": binding.credential_reference,
            "nonce": str(uuid5(attempt_id, "credential-nonce:v1")),
            "issued_at": _format_timestamp(created_at),
            "deadline": intent.deadline,
        }
    )
    correlation_id = str(uuid5(attempt_id, "correlation:v1"))
    connector_manifest_sha256 = canonical_sha256(profile.connector_manifest)
    request_payload = {
        "schema_version": 1,
        "contract": "ConnectorEnvelope/v1",
        "installation_id": str(scope.installation_id),
        "tenant_id": str(scope.tenant_id),
        "request_id": str(uuid5(attempt_id, "connector-invoke:v1")),
        "correlation_id": correlation_id,
        "episode_id": chain.plan.episode_id,
        "generation": chain.plan.generation,
        "connector_id": binding.provider_id,
        "connector_binding_id": binding.binding_id,
        "capability_id": intent.capability_id,
        "operation": intent.operation,
        "resource_scope": intent.resource_scope.model_dump(mode="json"),
        "deadline": intent.deadline,
        "request_payload_sha256": intent.query_sha256,
        "connector_manifest_sha256": connector_manifest_sha256,
        "capability_grant_id": capability_grant.grant_id,
        "credential_grant_id": credential_grant.credential_grant_id,
        "request_payload": action_arguments,
    }
    attempt = ReadAttempt(
        schema_version=1,
        contract="ReadAttempt/v1",
        read_attempt_id=str(attempt_id),
        read_intent_id=intent.read_intent_id,
        ordinal=1,
        state="dispatch_authorized",
        capability_grant_id=capability_grant.grant_id,
        credential_grant_id=credential_grant.credential_grant_id,
        connector_manifest_sha256=connector_manifest_sha256,
        request_sha256=canonical_sha256(request_payload),
        response_sha256=None,
        deadline=intent.deadline,
        created_at=_format_timestamp(created_at),
    )
    return attempt, capability_grant, credential_grant


def require_existing_action_chain_read_attempt(
    row: sa.Row,
    *,
    attempt: ReadAttempt,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
) -> None:
    try:
        stored = ReadAttempt.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainReadConflict("authorized read attempt is invalid") from error
    if (
        stored != attempt
        or row.read_attempt_id != UUID(attempt.read_attempt_id)
        or row.state != "dispatch_authorized"
        or row.response_sha256 is not None
        or int(row.revision) != 1
        or bytes(row.payload_sha256).hex() != canonical_sha256(attempt)
        or row.capability_grant_payload != capability_grant.model_dump(mode="json")
        or bytes(row.capability_grant_sha256).hex()
        != canonical_sha256(capability_grant)
        or row.credential_grant_payload != credential_grant.model_dump(mode="json")
        or bytes(row.credential_grant_sha256).hex()
        != canonical_sha256(credential_grant)
    ):
        raise ActionChainReadConflict("authorized read attempt differs")


def read_dispatch_event_data(
    attempt: ReadAttempt,
    *,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
    profile: ReadProviderProfile,
    registry_sha256: str,
    release_manifest_sha256: str,
    provenance_sha256: str,
    release_verification_receipt_sha256: str,
) -> dict[str, object]:
    return {
        "capability_grant_sha256": canonical_sha256(capability_grant),
        "connector_manifest_sha256": attempt.connector_manifest_sha256,
        "credential_grant_sha256": canonical_sha256(credential_grant),
        "process_spec_sha256": profile.process_spec_sha256,
        "provider_profile_revision": profile.revision,
        "provider_profile_sha256": profile.profile_sha256,
        "provider_registry_sha256": registry_sha256,
        "release_manifest_sha256": release_manifest_sha256,
        "release_provenance_sha256": provenance_sha256,
        "release_verification_receipt_sha256": (release_verification_receipt_sha256),
        "read_attempt_id": attempt.read_attempt_id,
        "read_attempt_payload_sha256": canonical_sha256(attempt),
        "read_intent_id": attempt.read_intent_id,
        "request_sha256": attempt.request_sha256,
    }


def _require_utc_second(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{field} must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    _require_utc_second(value, "timestamp")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "AuthorizedActionChainRead",
    "PostgresActionChainReadAuthorizer",
    "build_action_chain_read_authority",
    "read_dispatch_event_data",
    "require_existing_action_chain_read_attempt",
    "validate_action_chain_read_profile",
]
