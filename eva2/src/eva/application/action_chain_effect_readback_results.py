"""Project one stored provider verification into an EffectReadback contract."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.connector import InlinePayload, ReadbackResult
from eva.contracts.connector_rpc import ConnectorInvokeSuccess
from eva.contracts.evidence import EvidenceEnvelopeV2
from eva.contracts.execution import (
    EffectIntent,
    EffectReadback,
    EffectReceipt,
    ReadAttempt,
    ReadIntent,
    ReadObservation,
)


@dataclass(frozen=True, slots=True)
class ProjectedActionChainEffectReadback:
    """Canonical physical proof and the exact provider state that it hashes."""

    readback: EffectReadback
    state: JsonValue


def project_action_chain_effect_readback(
    *,
    effect: EffectIntent,
    receipt: EffectReceipt,
    read_intent: ReadIntent,
    read_attempt: ReadAttempt,
    observation: ReadObservation,
    evidence: EvidenceEnvelopeV2,
    provider_result: ConnectorInvokeSuccess,
) -> ProjectedActionChainEffectReadback:
    """Revalidate a retained readback result without I/O or state advancement."""

    outcome = provider_result.result.outcome
    if not isinstance(outcome, ReadbackResult):
        raise ValueError("stored provider result is not an effect readback")
    if (
        effect.state != "provider_accepted"
        or receipt.decision != "accepted"
        or receipt.provider_resource_id is None
        or receipt.effect_intent_id != effect.effect_intent_id
        or receipt.request_payload_sha256 != effect.frozen_payload_sha256
        or read_intent.purpose != "effect_verify"
        or read_intent.operation != "effect.readback"
        or read_intent.effect_intent_id != effect.effect_intent_id
        or read_attempt.state != "observed"
        or read_attempt.read_intent_id != read_intent.read_intent_id
        or read_attempt.response_sha256 != canonical_sha256(provider_result)
        or provider_result.result.operation != "effect.readback"
        or provider_result.result.request_id != provider_result.id
        or provider_result.result.request_sha256 != read_attempt.request_sha256
        or provider_result.result.connector_manifest_sha256
        != read_attempt.connector_manifest_sha256
        or outcome.provider_resource_id != receipt.provider_resource_id
        or outcome.provider_resource_id != observation.provider_resource_id
        or outcome.idempotency_key != effect.idempotency_key
        or outcome.request_sha256 != effect.frozen_payload_sha256
        or observation.read_intent_id != read_intent.read_intent_id
        or observation.read_attempt_id != read_attempt.read_attempt_id
        or observation.retrieved_at != outcome.observed_at
        or observation.response_sha256 != canonical_sha256(outcome)
        or observation.evidence_ref != evidence.evidence_id
        or evidence.aggregate_type != "action_chain_read_attempt"
        or evidence.aggregate_id != read_attempt.read_attempt_id
        or evidence.subject_contract != provider_result.contract
        or evidence.subject_id != provider_result.id
        or evidence.subject_payload_sha256 != canonical_sha256(provider_result)
    ):
        raise ValueError("stored effect readback proof binds another action")

    state = _normalized_state(outcome)
    expected_observation = {
        "idempotencyKey": outcome.idempotency_key,
        "matchStatus": outcome.match_status,
        "payload": state,
        "requestSha256": outcome.request_sha256,
    }
    if observation.normalized_value != expected_observation:
        raise ValueError("stored effect readback observation differs")

    readback = EffectReadback(
        schema_version=1,
        contract="EffectReadback/v1",
        readback_id=str(
            uuid5(UUID(read_attempt.read_attempt_id), "effect-readback:v1")
        ),
        effect_intent_id=effect.effect_intent_id,
        read_intent_id=read_intent.read_intent_id,
        read_attempt_id=read_attempt.read_attempt_id,
        provider_resource_id=outcome.provider_resource_id,
        idempotency_key=outcome.idempotency_key,
        request_sha256=outcome.request_sha256,
        observed_at=outcome.observed_at,
        state_sha256=canonical_sha256(state),
        match_status=outcome.match_status,
        evidence_ref=evidence.evidence_id,
    )
    return ProjectedActionChainEffectReadback(readback=readback, state=state)


def _normalized_state(outcome: ReadbackResult) -> JsonValue:
    payload = outcome.payload
    if payload is None:
        return None
    if isinstance(payload, InlinePayload):
        return payload.normalized_payload
    return payload.object_handle.model_dump(mode="json")


__all__ = [
    "ProjectedActionChainEffectReadback",
    "project_action_chain_effect_readback",
]
