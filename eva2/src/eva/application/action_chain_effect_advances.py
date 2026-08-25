"""Pure host verification before an action-chain effect may succeed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from eva.application.effect_verification import (
    EffectVerificationError,
    EffectVerificationProjection,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectReadback


class ActionChainEffectProofError(ValueError):
    """Stored readback bytes do not independently prove the desired state."""


@dataclass(frozen=True, slots=True)
class VerifiedActionChainEffectProof:
    """The exact selected values and hashes that earned success."""

    expected: dict[str, JsonValue]
    observed: dict[str, JsonValue]
    expected_sha256: str
    observed_sha256: str


def verify_action_chain_effect_proof(
    *,
    projection: EffectVerificationProjection,
    resource: str,
    arguments: dict[str, JsonValue],
    actual_state: JsonValue,
    readback: EffectReadback,
    provider_accepted_at: datetime,
    verified_at: datetime,
    deadline: datetime,
) -> VerifiedActionChainEffectProof:
    """Require fresh provider proof and Eva's own canonical field comparison."""

    for value, field in (
        (provider_accepted_at, "provider_accepted_at"),
        (verified_at, "verified_at"),
        (deadline, "deadline"),
    ):
        _require_utc_second(value, field)
    observed_at = _parse_timestamp(readback.observed_at)
    if not (
        readback.match_status == "matches"
        and provider_accepted_at <= observed_at <= verified_at < deadline
        and canonical_sha256(actual_state) == readback.state_sha256
    ):
        raise ActionChainEffectProofError(
            "readback is not fresh matching proof of its stored state"
        )
    try:
        expected = projection.project_expected(resource, arguments)
        observed = projection.project_observed(actual_state)
    except EffectVerificationError as error:
        raise ActionChainEffectProofError(
            "readback cannot satisfy the sealed verification projection"
        ) from error
    expected_sha256 = canonical_sha256(expected)
    observed_sha256 = canonical_sha256(observed)
    if expected_sha256 != observed_sha256:
        raise ActionChainEffectProofError(
            "observed state differs from the independently expected state"
        )
    return VerifiedActionChainEffectProof(
        expected=expected,
        observed=observed,
        expected_sha256=expected_sha256,
        observed_sha256=observed_sha256,
    )


def _require_utc_second(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{field} must use whole-second precision")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


__all__ = [
    "ActionChainEffectProofError",
    "VerifiedActionChainEffectProof",
    "verify_action_chain_effect_proof",
]
