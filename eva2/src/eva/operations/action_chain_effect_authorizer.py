"""Authorize one provider-bound action-chain effect without provider I/O."""

from __future__ import annotations
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_effects import (
    ActionChainEffectConflict,
    EffectStepAuthority,
    PostgresActionChainEffectPreparer,
)
from eva.adapters.postgres.catalog import metadata
from eva.application.action_chain import (
    ActionToolCatalog,
    action_chain_step_operation_id,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import (
    EffectAttempt,
)
from eva.operations.action_chain_dispatch_common import (
    PostgresActionChainCatalogAuthorizer,
    build_dispatch_attempt_values,
    commit_dispatch_authorized,
    resolve_dispatch_authorization,
)
from eva.operations.action_chain_effect_authority import (
    build_action_chain_effect_authority,
    effect_dispatch_event_data,
    require_existing_action_chain_effect_attempt,
    validate_action_chain_effect_profile,
)


effect_attempts = metadata.tables["eva_effects.effect_attempts"]
effect_intents = metadata.tables["eva_effects.effect_intents"]


@dataclass(frozen=True, slots=True)
class AuthorizedActionChainEffect:
    """One committed effect fence; deliberately exposes no callable bytes."""

    disposition: Literal["authorized", "already_authorized"]
    attempt: EffectAttempt


class PostgresActionChainEffectAuthorizer(PostgresActionChainCatalogAuthorizer):
    """Revalidate live authority and commit one effect dispatch permit."""

    _preparer_factory = PostgresActionChainEffectPreparer
    _postgres_error = "action-chain effect authorization requires PostgreSQL"

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
                kind="effect",
                intent_id=effect_intent_id,
                intent_table=effect_intents,
                attempt_table=effect_attempts,
                ledger=self._ledger,
                preparer=self._preparer,
                provider_registry=self._provider_registry,
                live_catalog=live_catalog,
                now=now,
                conflict_type=ActionChainEffectConflict,
                authority_factory=lambda row, activation, resolved_intent: (
                    EffectStepAuthority(
                        capability_activation_id=row.capability_activation_id,
                        connector_binding_id=activation.connector_binding_id,
                        resource_scope=resolved_intent.resource_scope,
                    )
                ),
                validate_profile=validate_action_chain_effect_profile,
            )
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
                    raise ActionChainEffectConflict("authorized effect intent differs")
                return AuthorizedActionChainEffect("already_authorized", attempt)
            if (
                intent.state != "prepared"
                or intent_row.state != "prepared"
                or int(intent_row.revision) != 1
            ):
                raise ActionChainEffectConflict("effect intent is not prepared")

            connection.execute(
                sa.insert(effect_attempts).values(
                    **build_dispatch_attempt_values(
                        scope=self._scope_value,
                        chain=chain,
                        attempt=attempt,
                        capability_grant=capability_grant,
                        credential_grant=credential_grant,
                        created_at=created_at,
                    ),
                    effect_attempt_id=UUID(attempt.effect_attempt_id),
                    effect_intent_id=effect_intent_id,
                    dispatch_permit_sha256=bytes.fromhex(
                        attempt.dispatch_permit_sha256 or ""
                    ),
                    error_class="none",
                    transport_evidence_id=None,
                    object_store_id=None,
                    object_candidate_id=None,
                    object_candidate_ciphertext_sha256=None,
                    object_candidate_envelope_byte_length=None,
                    object_candidate_temp_name_sha256=None,
                    object_candidate_state=None,
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
            commit_dispatch_authorized(
                connection,
                self._scope_value,
                kind="effect",
                operation_id=operation_id,
                occurred_at=created_at,
                event_data=effect_dispatch_event_data(
                    attempt,
                    capability_grant=capability_grant,
                    credential_grant=credential_grant,
                    permit=permit,
                    profile=profile,
                    registry=self._provider_registry,
                    authorized_intent=authorized_intent,
                ),
                deadline=_parse_timestamp(chain.plan.deadline),
            )
            self._ledger.load_in_transaction(connection, chain.plan_id)
            return AuthorizedActionChainEffect("authorized", attempt)


__all__ = [
    "AuthorizedActionChainEffect",
    "PostgresActionChainEffectAuthorizer",
]
