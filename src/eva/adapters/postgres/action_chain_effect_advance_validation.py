"""Rebuild the exact physical proof that may complete one effect step."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.application.action_chain_effect_advances import (
    VerifiedActionChainEffectProof,
    verify_action_chain_effect_proof,
)
from eva.application.effect_verification import EffectVerificationProjection
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectReadback, ReadObservation


effect_readbacks = metadata.tables["eva_effects.effect_readbacks"]
read_observations = metadata.tables["eva_effects.read_observations"]


def project_stored_action_chain_effect_proof(
    connection: sa.Connection,
    scope: TimelineScope,
    *,
    effect_intent_id: UUID,
    projection: EffectVerificationProjection,
    resource: str,
    arguments: dict[str, JsonValue],
    provider_accepted_at: datetime,
    verified_at: datetime,
    deadline: datetime,
    corrupt: type[RuntimeError],
) -> tuple[VerifiedActionChainEffectProof, EffectReadback, ReadObservation]:
    """Require one immutable readback and independently recompare its state."""

    rows = connection.execute(
        sa.select(effect_readbacks).where(
            effect_readbacks.c.tenant_id == scope.tenant_id,
            effect_readbacks.c.installation_id == scope.installation_id,
            effect_readbacks.c.effect_intent_id == effect_intent_id,
        )
    ).all()
    if len(rows) != 1:
        raise corrupt("effect completion readback cardinality differs")
    row = rows[0]
    observation_row = connection.execute(
        sa.select(read_observations).where(
            read_observations.c.tenant_id == scope.tenant_id,
            read_observations.c.installation_id == scope.installation_id,
            read_observations.c.read_attempt_id == row.read_attempt_id,
        )
    ).one_or_none()
    try:
        readback = EffectReadback.model_validate(row.payload)
        observation = ReadObservation.model_validate(
            None if observation_row is None else observation_row.payload
        )
        normalized = observation.normalized_value
        if not isinstance(normalized, dict) or set(normalized) != {
            "idempotencyKey",
            "matchStatus",
            "payload",
            "requestSha256",
        }:
            raise ValueError("normalized effect state envelope differs")
        actual_state = normalized["payload"]
        proof = verify_action_chain_effect_proof(
            projection=projection,
            resource=resource,
            arguments=arguments,
            actual_state=actual_state,
            readback=readback,
            provider_accepted_at=provider_accepted_at,
            verified_at=verified_at,
            deadline=deadline,
        )
    except (TypeError, ValueError) as error:
        raise corrupt("effect completion proof is invalid") from error
    if (
        readback.effect_intent_id != str(effect_intent_id)
        or readback.read_attempt_id != observation.read_attempt_id
        or readback.evidence_ref != observation.evidence_ref
        or readback.state_sha256 != canonical_sha256(actual_state)
    ):
        raise corrupt("effect completion proof binding differs")
    return proof, readback, observation


def effect_completion_context(
    *,
    effect_step_id: UUID,
    projection: EffectVerificationProjection,
    proof: VerifiedActionChainEffectProof,
    readback: EffectReadback,
    observation: ReadObservation,
    released_step_ids: tuple[UUID, ...],
) -> dict[str, JsonValue]:
    """Build the sole event context accepted for effect completion."""

    return {
        "contract": "ActionChainEffectAdvance/v1",
        "effect_intent_id": readback.effect_intent_id,
        "effect_outcome_ref": f"effect-readback:{readback.readback_id}",
        "effect_readback_id": readback.readback_id,
        "effect_step_id": str(effect_step_id),
        "read_attempt_id": readback.read_attempt_id,
        "read_observation_id": observation.observation_id,
        "released_step_ids": [str(item) for item in released_step_ids],
        "verification_expected_sha256": proof.expected_sha256,
        "verification_observed_sha256": proof.observed_sha256,
        "verification_projection_sha256": canonical_sha256(
            projection.authority_payload()
        ),
    }


__all__ = [
    "effect_completion_context",
    "project_stored_action_chain_effect_proof",
]
