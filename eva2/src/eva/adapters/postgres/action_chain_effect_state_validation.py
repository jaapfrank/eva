"""Reconstruct one effect's authorized state from current durable rows."""

from __future__ import annotations

import sqlalchemy as sa

from eva.adapters.postgres.action_chain_effect_result_validation import (
    ReconstructedActionChainEffectState,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import EffectAttempt, EffectIntent


def reconstruct_action_chain_effect_state(
    *,
    intent: EffectIntent,
    intent_row: sa.Row,
    stored_attempt: EffectAttempt,
    attempt_row: sa.Row,
    dispatch_permit_sha256: str,
    external_counts: tuple[int, int],
    corrupt: type[RuntimeError],
) -> ReconstructedActionChainEffectState:
    """Recover the signed pre-call state and validate its durable projection."""

    authorized_intent = EffectIntent.model_validate(
        {**intent.model_dump(mode="json"), "state": "dispatch_authorized"}
    )
    authorized_attempt = EffectAttempt.model_validate(
        {
            **stored_attempt.model_dump(mode="json"),
            "state": "dispatch_authorized",
            "dispatch_permit_sha256": dispatch_permit_sha256,
            "response_sha256": None,
            "error_class": "none",
        }
    )
    if not _effect_state_matches(
        intent=intent,
        intent_row=intent_row,
        stored_attempt=stored_attempt,
        attempt_row=attempt_row,
        authorized_attempt=authorized_attempt,
        external_counts=external_counts,
    ):
        raise corrupt("authorized effect storage state differs")
    return ReconstructedActionChainEffectState(
        intent=authorized_intent,
        attempt=authorized_attempt,
        terminal=stored_attempt.state != "dispatch_authorized",
    )


def _effect_state_matches(
    *,
    intent: EffectIntent,
    intent_row: sa.Row,
    stored_attempt: EffectAttempt,
    attempt_row: sa.Row,
    authorized_attempt: EffectAttempt,
    external_counts: tuple[int, int],
) -> bool:
    if stored_attempt.state == "dispatch_authorized":
        return (
            intent.state == "dispatch_authorized"
            and intent_row.state == "dispatch_authorized"
            and int(intent_row.revision) == 2
            and intent_row.updated_at == attempt_row.created_at
            and stored_attempt == authorized_attempt
            and attempt_row.state == "dispatch_authorized"
            and _optional_hash(attempt_row.dispatch_permit_sha256)
            == authorized_attempt.dispatch_permit_sha256
            and attempt_row.response_sha256 is None
            and attempt_row.error_class == "none"
            and attempt_row.transport_evidence_id is None
            and bytes(attempt_row.payload_sha256).hex()
            == canonical_sha256(authorized_attempt)
            and int(attempt_row.revision) == 1
            and attempt_row.created_at == attempt_row.updated_at
            and not any(external_counts)
        )
    expected_intent_state = {
        "provider_accepted": "provider_accepted",
        "definitive_reject": "provider_rejected",
        "ambiguous_transport": "ambiguous",
        "rate_limited": "waiting_breaker",
        "policy_denied": "blocked",
        "internal_failure": "blocked",
    }.get(stored_attempt.state)
    expected_receipts = (
        1 if stored_attempt.state in {"provider_accepted", "definitive_reject"} else 0
    )
    return (
        expected_intent_state is not None
        and intent.state == expected_intent_state
        and intent_row.state == expected_intent_state
        and int(intent_row.revision) == 3
        and bytes(intent_row.payload_sha256).hex() == canonical_sha256(intent)
        and attempt_row.state == stored_attempt.state
        and _optional_hash(attempt_row.dispatch_permit_sha256)
        == stored_attempt.dispatch_permit_sha256
        and _optional_hash(attempt_row.response_sha256)
        == stored_attempt.response_sha256
        and attempt_row.error_class == stored_attempt.error_class
        and attempt_row.transport_evidence_id is not None
        and bytes(attempt_row.payload_sha256).hex()
        == canonical_sha256(stored_attempt)
        and int(attempt_row.revision) == 2
        and attempt_row.updated_at == intent_row.updated_at
        and attempt_row.updated_at >= attempt_row.created_at
        and external_counts == (expected_receipts, 0)
    )


def _optional_hash(value: object) -> str | None:
    return None if value is None else bytes(value).hex()


__all__ = ["reconstruct_action_chain_effect_state"]
