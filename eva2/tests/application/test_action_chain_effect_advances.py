from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eva.application.action_chain_effect_advances import (
    ActionChainEffectProofError,
    verify_action_chain_effect_proof,
)
from eva.application.effect_verification import (
    EffectVerificationField,
    EffectVerificationProjection,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectReadback


NOW = datetime(2026, 8, 20, 12, 0, 16, tzinfo=UTC)


def _projection() -> EffectVerificationProjection:
    return EffectVerificationProjection(
        (
            EffectVerificationField(
                name="state",
                expected_source="arguments",
                expected_pointer="/state",
                observed_pointer="/state",
            ),
        )
    )


def _readback(**updates: object) -> EffectReadback:
    payload = {
        "schema_version": 1,
        "contract": "EffectReadback/v1",
        "readback_id": "readback:1",
        "effect_intent_id": "effect:1",
        "read_intent_id": "read:1",
        "read_attempt_id": "attempt:1",
        "provider_resource_id": "ha:entity:input_boolean.eva_test",
        "idempotency_key": "effect:key:1",
        "request_sha256": "1" * 64,
        "observed_at": "2026-08-20T12:00:14Z",
        "state_sha256": canonical_sha256({"state": "on"}),
        "match_status": "matches",
        "evidence_ref": "evidence:1",
    }
    payload.update(updates)
    return EffectReadback.model_validate(payload)


def _verify(*, actual_state: object = None, readback: EffectReadback | None = None):
    return verify_action_chain_effect_proof(
        projection=_projection(),
        resource="input_boolean.eva_test",
        arguments={"state": "on", "target": "input_boolean.eva_test"},
        actual_state={"state": "on"} if actual_state is None else actual_state,
        readback=readback or _readback(),
        provider_accepted_at=NOW - timedelta(seconds=6),
        verified_at=NOW,
        deadline=NOW + timedelta(minutes=1),
    )


def test_exact_fresh_state_earns_independent_verification() -> None:
    proof = _verify()
    assert proof.expected == {"state": "on"}
    assert proof.observed == proof.expected
    assert proof.expected_sha256 == proof.observed_sha256


@pytest.mark.parametrize(
    "status",
    ("not_observed", "conflict", "inconclusive"),
)
def test_provider_nonmatch_never_earns_success(status: str) -> None:
    payload = _readback().model_dump(mode="json")
    payload["match_status"] = status
    with pytest.raises(ActionChainEffectProofError, match="not fresh matching"):
        _verify(readback=EffectReadback.model_validate(payload))


def test_provider_match_claim_cannot_hide_actual_state_mismatch() -> None:
    state = {"state": "off"}
    with pytest.raises(ActionChainEffectProofError, match="independently expected"):
        _verify(
            actual_state=state,
            readback=_readback(state_sha256=canonical_sha256(state)),
        )


def test_state_hash_corruption_and_stale_observation_fail_closed() -> None:
    with pytest.raises(ActionChainEffectProofError, match="not fresh matching"):
        _verify(readback=_readback(state_sha256="0" * 64))
    with pytest.raises(ActionChainEffectProofError, match="not fresh matching"):
        _verify(readback=_readback(observed_at="2026-08-20T12:00:09Z"))


def test_boolean_and_number_lookalikes_do_not_match() -> None:
    projection = EffectVerificationProjection(
        (
            EffectVerificationField(
                name="enabled",
                expected_source="arguments",
                expected_pointer="/enabled",
                observed_pointer="/enabled",
            ),
        )
    )
    state = {"enabled": 1}
    readback = _readback(state_sha256=canonical_sha256(state))
    with pytest.raises(ActionChainEffectProofError, match="independently expected"):
        verify_action_chain_effect_proof(
            projection=projection,
            resource="switch.test",
            arguments={"enabled": True},
            actual_state=state,
            readback=readback,
            provider_accepted_at=NOW - timedelta(seconds=6),
            verified_at=NOW,
            deadline=NOW + timedelta(minutes=1),
        )
