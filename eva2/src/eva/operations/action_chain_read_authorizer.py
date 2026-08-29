"""Authorize one provider-bound action-chain read without provider I/O."""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_ledger import (
    PostgresActionChainLedger,
    StoredActionChain,
    read_intents,
)
from eva.operations.action_chain_provider_registry import (
    ReadProviderProfile,
    SealedReadProviderRegistry,
)
from eva.operations.action_chain_dispatch_common import (
    build_dispatch_attempt_values,
    build_dispatch_authority_material,
    commit_dispatch_authorized,
    resolve_dispatch_authorization,
)
from eva.adapters.postgres.action_chain_reads import (
    ActionChainReadConflict,
    PostgresActionChainReadPreparer,
    ReadStepAuthority,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.action_chain import (
    ActionToolCatalog,
    action_chain_step_operation_id,
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


class PostgresActionChainReadAuthorizer(PostgresScopedRole):
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
            (
                intent_row,
                chain,
                stored_step,
                intent,
                action,
                profile,
                capability_manifest,
                binding,
                existing,
                created_at,
            ) = resolve_dispatch_authorization(
                connection,
                self._scope_value,
                kind="read",
                intent_id=read_intent_id,
                intent_table=read_intents,
                attempt_table=read_attempts,
                ledger=self._ledger,
                preparer=self._preparer,
                provider_registry=self._provider_registry,
                live_catalog=live_catalog,
                now=now,
                conflict_type=ActionChainReadConflict,
                authority_factory=lambda row, activation, resolved_intent: (
                    ReadStepAuthority(
                        capability_activation_id=row.capability_activation_id,
                        connector_binding_id=activation.connector_binding_id,
                        resource_scope=resolved_intent.resource_scope,
                    )
                ),
                validate_profile=validate_action_chain_read_profile,
            )
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
                    **build_dispatch_attempt_values(
                        scope=self._scope_value,
                        chain=chain,
                        attempt=attempt,
                        capability_grant=capability_grant,
                        credential_grant=credential_grant,
                        created_at=created_at,
                    ),
                    read_attempt_id=UUID(attempt.read_attempt_id),
                    read_intent_id=read_intent_id,
                )
            )
            operation_id = action_chain_step_operation_id(
                chain.plan, stored_step.logical_step_key
            )
            commit_dispatch_authorized(
                connection,
                self._scope_value,
                kind="read",
                operation_id=operation_id,
                occurred_at=created_at,
                event_data=read_dispatch_event_data(
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
                deadline=_parse_timestamp(chain.plan.deadline),
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return AuthorizedActionChainRead("authorized", attempt)


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
    (
        attempt_id,
        capability_grant,
        credential_grant,
        connector_manifest_sha256,
        envelope,
    ) = build_dispatch_authority_material(
        kind="read",
        scope=scope,
        chain=chain,
        action_arguments=action_arguments,
        intent=intent,
        profile=profile,
        capability_manifest=capability_manifest,
        binding=binding,
        created_at=created_at,
        conflict_type=ActionChainReadConflict,
    )
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
        request_sha256=canonical_sha256(envelope),
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
