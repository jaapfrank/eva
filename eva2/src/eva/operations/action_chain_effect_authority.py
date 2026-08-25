"""Pure construction and validation for one action-chain effect fence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_ledger import StoredActionChain
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import ConnectorEnvelope
from eva.contracts.execution import (
    CapabilityGrant,
    CapabilityManifest,
    ConnectorBinding,
    CredentialGrant,
    EffectAttempt,
    EffectIntent,
)
from eva.operations.action_chain_provider_registry import (
    ActionProviderProfile,
    SealedActionProviderRegistry,
)


def validate_action_chain_effect_profile(
    profile: ActionProviderProfile,
    *,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    intent: EffectIntent,
) -> None:
    operation = next(
        (
            item
            for item in profile.connector_manifest.operations
            if item.operation == intent.operation
        ),
        None,
    )
    if (
        profile.connector_manifest.connector_id != binding.provider_id
        or profile.connector_manifest.executable_sha256
        != capability_manifest.executable_sha256
        or profile.connector_manifest.sandbox_profile_sha256
        != capability_manifest.sandbox_profile_sha256
        or operation is None
        or operation.input_schema_sha256 != capability_manifest.input_schema_sha256
        or operation.output_schema_sha256
        != capability_manifest.output_schema_sha256
        or not operation.writes_provider_state
        or operation.external_calls_per_invocation != 1
    ):
        raise ActionChainEffectConflict("sealed effect provider profile differs")


def build_action_chain_effect_authority(
    *,
    scope: TimelineScope,
    chain: StoredActionChain,
    action_arguments: dict[str, object],
    intent: EffectIntent,
    profile: ActionProviderProfile,
    capability_manifest: CapabilityManifest,
    binding: ConnectorBinding,
    created_at: datetime,
    registry: SealedActionProviderRegistry,
) -> tuple[EffectAttempt, CapabilityGrant, CredentialGrant, dict[str, object]]:
    if _parse_timestamp(profile.connector_manifest.issued_at) > created_at:
        raise ActionChainEffectConflict(
            "connector manifest was issued in the future"
        )
    attempt_id = uuid5(UUID(intent.effect_intent_id), "effect-attempt:1")
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
            "authorized_payload_sha256": intent.frozen_payload_sha256,
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
    connector_manifest_sha256 = canonical_sha256(profile.connector_manifest)
    envelope = ConnectorEnvelope.model_validate(
        {
            "schema_version": 1,
            "contract": "ConnectorEnvelope/v1",
            "installation_id": str(scope.installation_id),
            "tenant_id": str(scope.tenant_id),
            "request_id": str(uuid5(attempt_id, "connector-invoke:v1")),
            "correlation_id": str(uuid5(attempt_id, "correlation:v1")),
            "episode_id": chain.plan.episode_id,
            "generation": chain.plan.generation,
            "connector_id": binding.provider_id,
            "connector_binding_id": binding.binding_id,
            "capability_id": intent.capability_id,
            "operation": intent.operation,
            "resource_scope": intent.resource_scope.model_dump(mode="json"),
            "deadline": intent.deadline,
            "request_payload_sha256": intent.frozen_payload_sha256,
            "connector_manifest_sha256": connector_manifest_sha256,
            "capability_grant_id": capability_grant.grant_id,
            "credential_grant_id": credential_grant.credential_grant_id,
            "request_payload": action_arguments,
        }
    )
    request_sha256 = canonical_sha256(envelope)
    permit = {
        "schema_version": 1,
        "contract": "EffectDispatchPermit/v1",
        "effect_attempt_id": str(attempt_id),
        "effect_intent_id": intent.effect_intent_id,
        "idempotency_key": intent.idempotency_key,
        "request_sha256": request_sha256,
        "capability_grant_sha256": canonical_sha256(capability_grant),
        "credential_grant_sha256": canonical_sha256(credential_grant),
        "connector_manifest_sha256": connector_manifest_sha256,
        "process_spec_sha256": profile.process_spec_sha256,
        "provider_profile_sha256": profile.profile_sha256,
        "provider_registry_sha256": registry.registry_sha256,
        "release_manifest_sha256": registry.release_manifest_sha256,
        "release_provenance_sha256": registry.provenance_sha256,
        "release_verification_receipt_sha256": (
            registry.release_verification_receipt_sha256
        ),
        "issued_at": _format_timestamp(created_at),
        "deadline": intent.deadline,
    }
    attempt = EffectAttempt(
        schema_version=1,
        contract="EffectAttempt/v1",
        effect_attempt_id=str(attempt_id),
        effect_intent_id=intent.effect_intent_id,
        ordinal=1,
        state="dispatch_authorized",
        dispatch_permit_sha256=canonical_sha256(permit),
        capability_grant_id=capability_grant.grant_id,
        credential_grant_id=credential_grant.credential_grant_id,
        connector_manifest_sha256=connector_manifest_sha256,
        request_sha256=request_sha256,
        response_sha256=None,
        error_class="none",
        deadline=intent.deadline,
        created_at=_format_timestamp(created_at),
    )
    return attempt, capability_grant, credential_grant, permit


def require_existing_action_chain_effect_attempt(
    row: sa.Row,
    *,
    attempt: EffectAttempt,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
) -> None:
    try:
        stored = EffectAttempt.model_validate(row.payload)
    except ValueError as error:
        raise ActionChainEffectConflict(
            "authorized effect attempt is invalid"
        ) from error
    if (
        stored != attempt
        or row.effect_attempt_id != UUID(attempt.effect_attempt_id)
        or row.state != "dispatch_authorized"
        or bytes(row.dispatch_permit_sha256).hex()
        != attempt.dispatch_permit_sha256
        or row.response_sha256 is not None
        or row.error_class != "none"
        or row.transport_evidence_id is not None
        or row.object_store_id is not None
        or row.object_candidate_id is not None
        or row.object_candidate_ciphertext_sha256 is not None
        or row.object_candidate_envelope_byte_length is not None
        or row.object_candidate_temp_name_sha256 is not None
        or row.object_candidate_state is not None
        or int(row.revision) != 1
        or row.created_at != row.updated_at
        or bytes(row.payload_sha256).hex() != canonical_sha256(attempt)
        or row.capability_grant_payload != capability_grant.model_dump(mode="json")
        or bytes(row.capability_grant_sha256).hex()
        != canonical_sha256(capability_grant)
        or row.capability_grant_nonce != capability_grant.nonce
        or row.capability_grant_deadline
        != _parse_timestamp(capability_grant.deadline)
        or row.credential_grant_payload != credential_grant.model_dump(mode="json")
        or bytes(row.credential_grant_sha256).hex()
        != canonical_sha256(credential_grant)
        or row.credential_grant_nonce != credential_grant.nonce
        or row.credential_grant_deadline
        != _parse_timestamp(credential_grant.deadline)
    ):
        raise ActionChainEffectConflict("authorized effect attempt differs")


def effect_dispatch_event_data(
    attempt: EffectAttempt,
    *,
    capability_grant: CapabilityGrant,
    credential_grant: CredentialGrant,
    permit: dict[str, object],
    profile: ActionProviderProfile,
    registry: SealedActionProviderRegistry,
    authorized_intent: EffectIntent,
) -> dict[str, object]:
    return {
        "capability_grant_sha256": canonical_sha256(capability_grant),
        "connector_manifest_sha256": attempt.connector_manifest_sha256,
        "credential_grant_sha256": canonical_sha256(credential_grant),
        "dispatch_permit_sha256": canonical_sha256(permit),
        "effect_attempt_id": attempt.effect_attempt_id,
        "effect_attempt_payload_sha256": canonical_sha256(attempt),
        "effect_intent_id": attempt.effect_intent_id,
        "effect_intent_payload_sha256": canonical_sha256(authorized_intent),
        "idempotency_key": authorized_intent.idempotency_key,
        "process_spec_sha256": profile.process_spec_sha256,
        "provider_profile_revision": profile.revision,
        "provider_profile_sha256": profile.profile_sha256,
        "provider_registry_sha256": registry.registry_sha256,
        "release_manifest_sha256": registry.release_manifest_sha256,
        "release_provenance_sha256": registry.provenance_sha256,
        "release_verification_receipt_sha256": (
            registry.release_verification_receipt_sha256
        ),
        "request_sha256": attempt.request_sha256,
    }


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "build_action_chain_effect_authority",
    "effect_dispatch_event_data",
    "require_existing_action_chain_effect_attempt",
    "validate_action_chain_effect_profile",
]
