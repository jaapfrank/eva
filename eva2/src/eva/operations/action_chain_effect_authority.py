"""Pure construction and validation for one action-chain effect fence."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_ledger import StoredActionChain
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.contracts.canonical_json import canonical_sha256
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
from eva.operations.action_chain_dispatch_common import (
    build_dispatch_authority_material,
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
    (
        attempt_id,
        capability_grant,
        credential_grant,
        connector_manifest_sha256,
        envelope,
    ) = build_dispatch_authority_material(
        kind="effect",
        scope=scope,
        chain=chain,
        action_arguments=action_arguments,
        intent=intent,
        profile=profile,
        capability_manifest=capability_manifest,
        binding=binding,
        created_at=created_at,
        conflict_type=ActionChainEffectConflict,
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






__all__ = [
    "build_action_chain_effect_authority",
    "effect_dispatch_event_data",
    "require_existing_action_chain_effect_attempt",
    "validate_action_chain_effect_profile",
]
