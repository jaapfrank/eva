from __future__ import annotations

import pytest

from eva.application.effect_verification import (
    EffectVerificationError,
    EffectVerificationField,
    EffectVerificationProjection,
)


def _projection() -> EffectVerificationProjection:
    return EffectVerificationProjection(
        (
            EffectVerificationField(
                name="entity",
                expected_source="resource",
                expected_pointer="",
                observed_pointer="/entity_id",
            ),
            EffectVerificationField(
                name="state",
                expected_source="arguments",
                expected_pointer="/desired/state",
                observed_pointer="/state",
            ),
        )
    )


def test_projection_selects_only_declared_expected_and_observed_fields() -> None:
    projection = _projection()
    arguments = {
        "desired": {"state": "on"},
        "provider_hint": "must-not-enter-proof",
    }
    observed = {
        "entity_id": "input_boolean.eva_test",
        "state": "on",
        "untrusted_extra": "ignored",
    }

    assert projection.project_expected(
        "input_boolean.eva_test", arguments
    ) == {"entity": "input_boolean.eva_test", "state": "on"}
    assert projection.project_observed(observed) == {
        "entity": "input_boolean.eva_test",
        "state": "on",
    }
    assert projection.matches(
        resource="input_boolean.eva_test",
        arguments=arguments,
        observed_state=observed,
    )


def test_projection_is_type_exact_and_does_not_trust_lookalikes() -> None:
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

    assert not projection.matches(
        resource="switch.test",
        arguments={"enabled": True},
        observed_state={"enabled": 1},
    )


def test_projection_authority_round_trips_canonically() -> None:
    projection = _projection()
    assert EffectVerificationProjection.from_authority_payload(
        projection.authority_payload()
    ) == projection


def test_projection_authority_rejects_unknown_expected_source() -> None:
    payload = _projection().authority_payload()
    payload["fields"][0]["expectedSource"] = "provider"
    with pytest.raises(EffectVerificationError, match="expected source"):
        EffectVerificationProjection.from_authority_payload(payload)


@pytest.mark.parametrize(
    "pointer",
    ("missing-leading-slash", "/bad~escape", "/array/01"),
)
def test_invalid_or_noncanonical_pointer_fails_closed(pointer: str) -> None:
    projection = EffectVerificationProjection(
        (
            EffectVerificationField(
                name="state",
                expected_source="arguments",
                expected_pointer="/state",
                observed_pointer=pointer,
            ),
        )
    ) if pointer == "/array/01" else None
    if projection is None:
        with pytest.raises(EffectVerificationError):
            EffectVerificationField(
                name="state",
                expected_source="arguments",
                expected_pointer="/state",
                observed_pointer=pointer,
            )
        return
    with pytest.raises(EffectVerificationError, match="noncanonical array index"):
        projection.project_observed({"array": ["zero", "one"]})


def test_missing_selected_state_fails_closed() -> None:
    with pytest.raises(EffectVerificationError, match="absent object field"):
        _projection().project_observed(
            {"entity_id": "input_boolean.eva_test"}
        )
