from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from eva.contracts import canonical_json_bytes, canonical_sha256, read_contract_bytes
from eva.contracts.base import CalendarDate, RelativeArtifactPath, Timestamp
from eva.contracts.connector import ConnectorEnvelope, ConnectorResult, ProbeResult
from eva.contracts.execution import (
    CredentialGrant,
    DeliveryIntent,
    DeliveryReceipt,
    EffectAttempt,
    ReadIntent,
)
from eva.contracts.identity import IdentityPack
from eva.contracts.ingress import DeliveryOffer, IngressOutcome
from eva.contracts.planning import (
    ModelDecision,
    OutcomeView,
    PlannerRequest,
    PresenceContext,
    PresenceReply,
)
from eva.contracts.platform import EvaPortableArchive, PlatformLock

H = "0" * 64
NOW = "2026-08-20T12:00:00Z"
EVA2_ROOT = Path(__file__).resolve().parents[2]


def _meta(contract: str) -> dict[str, object]:
    return {"contract": contract, "schema_version": 1}


def _resource() -> dict[str, object]:
    return {
        "resource_type": "calendar",
        "resource_id": "fixture:calendar:001",
        "scope_sha256": H,
    }


def test_strict_version_and_extra_field_guards() -> None:
    valid = {
        **_meta("ModelDecision/v1"),
        "episode_id": "episode:001",
        "generation": 1,
        "purpose": "plan",
        "decision": "wait",
        "actions": [],
        "clarification_question": None,
        "wait_reason": "provider tijdelijk niet beschikbaar",
        "uncertainty_basis_points": 500,
    }
    ModelDecision.model_validate(valid)
    for field, value in (
        ("contract", "ModelDecision/v2"),
        ("schema_version", 2),
        ("generation", "1"),
    ):
        invalid = dict(valid)
        invalid[field] = value
        with pytest.raises(ValidationError):
            ModelDecision.model_validate(invalid)
    invalid = dict(valid)
    invalid["effect_authority"] = True
    with pytest.raises(ValidationError):
        ModelDecision.model_validate(invalid)


def test_identity_pack_cannot_embed_relations_tools_authority_or_secrets() -> None:
    schema_properties = IdentityPack.model_json_schema()["properties"]
    forbidden = {
        "relations",
        "tools",
        "effect_authority",
        "consent_grants",
        "credentials",
        "secrets",
        "wakeword",
    }
    assert forbidden.isdisjoint(schema_properties)
    assert schema_properties["state"]["enum"] == [
        "staged",
        "current",
        "superseded",
        "revoked",
    ]


def test_read_intent_closes_reconcile_readback_and_probe_vocabulary() -> None:
    base = {
        **_meta("ReadIntent/v1"),
        "read_intent_id": "read:001",
        "episode_id": "episode:001",
        "generation": 1,
        "purpose": "effect_reconcile",
        "effect_intent_id": "effect:001",
        "operation": "effect.reconcile",
        "resource_scope": _resource(),
        "query_sha256": H,
        "maximum_freshness_ms": 0,
        "maximum_attempts": 1,
        "capability_id": "capability:reconcile",
        "deadline": "2026-08-20T12:01:00Z",
    }
    ReadIntent.model_validate(base)
    mismatch = dict(base)
    mismatch["operation"] = "effect.readback"
    with pytest.raises(ValidationError):
        ReadIntent.model_validate(mismatch)
    business_read = dict(base)
    business_read.update(purpose="business_read", operation="mail.message.read")
    with pytest.raises(ValidationError):
        ReadIntent.model_validate(business_read)


def test_connector_result_represents_every_closed_recovery_outcome() -> None:
    common = {
        **_meta("ConnectorResult/v1"),
        "connector_id": "connector:calendar",
        "request_id": "request:001",
        "correlation_id": "correlation:001",
        "operation": "effect.reconcile",
        "request_sha256": H,
        "connector_manifest_sha256": H,
        "completed_at": NOW,
    }
    for outcome in ("found_same", "not_observed", "conflict", "inconclusive"):
        value = {
            **common,
            "outcome": {
                "kind": "reconcile_result",
                "idempotency_key": "idempotency:001",
                "resource_scope": _resource(),
                "request_payload_sha256": H,
                "result": outcome,
                "provider_resource_id": "calendar:event:001"
                if outcome == "found_same"
                else None,
                "evidence_sha256": H,
            },
        }
        ConnectorResult.model_validate(value)
    invalid = deepcopy(value)
    invalid["outcome"]["result"] = "retry"
    with pytest.raises(ValidationError):
        ConnectorResult.model_validate(invalid)


def test_connector_result_recovery_operations_are_bidirectionally_closed() -> None:
    corpus = json.loads(
        (EVA2_ROOT / "schemas" / "contracts" / "contract-corpus-v1.json").read_bytes()
    )
    read_result = next(
        json.loads(case["wireUtf8"])
        for case in corpus["cases"]
        if case["caseId"] == "valid-connector-result-inline-null"
    )
    accepted = {
        case["caseId"]: json.loads(case["wireUtf8"])
        for case in corpus["cases"]
        if case["expect"] == "accept"
    }
    exact_pairs = {
        "valid-connector-result-reconcile": (
            "effect.reconcile",
            "reconcile_result",
        ),
        "valid-connector-result-readback": ("effect.readback", "readback"),
        "valid-connector-result-probe": ("connector.probe", "probe_result"),
    }
    for case_id, (operation, kind) in exact_pairs.items():
        value = accepted[case_id]
        result = ConnectorResult.model_validate(value)
        assert (result.operation, result.outcome.kind) == (operation, kind)
        wrong_operation = deepcopy(value)
        wrong_operation["operation"] = "calendar.event.read"
        with pytest.raises(ValidationError):
            ConnectorResult.model_validate(wrong_operation)

    for operation in ("effect.reconcile", "effect.readback", "connector.probe"):
        invalid = deepcopy(read_result)
        invalid["operation"] = operation
        with pytest.raises(ValidationError):
            ConnectorResult.model_validate(invalid)


def test_inline_json_null_is_distinct_from_an_absent_wire_payload() -> None:
    corpus = json.loads(
        (EVA2_ROOT / "schemas" / "contracts" / "contract-corpus-v1.json").read_bytes()
    )
    case = next(
        item
        for item in corpus["cases"]
        if item["caseId"] == "valid-connector-result-inline-null"
    )
    parsed = read_contract_bytes(case["wireUtf8"].encode())
    assert isinstance(parsed, ConnectorResult)
    assert parsed.outcome.payload.normalized_payload is None
    invalid = json.loads(case["wireUtf8"])
    del invalid["outcome"]["payload"]["normalized_payload"]
    with pytest.raises(ValidationError):
        ConnectorResult.model_validate(invalid)


def test_probe_retry_after_is_present_only_when_rate_limited() -> None:
    common = {
        "kind": "probe_result",
        "observed_at": NOW,
        "no_provider_mutation_evidence_sha256": H,
    }
    ProbeResult.model_validate(
        {**common, "result": "rate_limited", "retry_after_ms": 4000}
    )
    ProbeResult.model_validate({**common, "result": "ready", "retry_after_ms": None})
    with pytest.raises(ValidationError):
        ProbeResult.model_validate(
            {**common, "result": "ready", "retry_after_ms": 4000}
        )


def test_connector_envelope_hash_binds_the_exact_inline_request() -> None:
    request_payload = {"day": "2026-08-26", "duration_minutes": 30}
    value = {
        **_meta("ConnectorEnvelope/v1"),
        "installation_id": "installation:001",
        "tenant_id": "tenant:001",
        "request_id": "request:001",
        "correlation_id": "correlation:001",
        "episode_id": "episode:001",
        "generation": 1,
        "connector_id": "connector:calendar",
        "connector_binding_id": "binding:calendar",
        "capability_id": "capability:availability",
        "operation": "calendar.availability.read",
        "resource_scope": _resource(),
        "deadline": NOW,
        "request_payload_sha256": hashlib.sha256(
            canonical_json_bytes(request_payload)
        ).hexdigest(),
        "connector_manifest_sha256": H,
        "capability_grant_id": "capability-grant:001",
        "credential_grant_id": "credential-grant:001",
        "request_payload": request_payload,
    }
    ConnectorEnvelope.model_validate(value)
    value["request_payload"] = {**request_payload, "duration_minutes": 45}
    with pytest.raises(ValidationError):
        ConnectorEnvelope.model_validate(value)


def test_credential_grant_has_exactly_one_authority_target() -> None:
    common = {
        **_meta("CredentialGrant/v1"),
        "credential_grant_id": "credential-grant:001",
        "connector_binding_id": "binding:001",
        "model_provider_profile_id": None,
        "worker_id": "worker:001",
        "executable_sha256": H,
        "provider_scopes": ["calendar.write"],
        "secret_descriptor_id": "descriptor:001",
        "nonce": "nonce:001",
        "issued_at": NOW,
        "deadline": "2026-08-20T12:01:00Z",
    }
    CredentialGrant.model_validate(common)
    both = dict(common)
    both["model_provider_profile_id"] = "model-profile:001"
    with pytest.raises(ValidationError):
        CredentialGrant.model_validate(both)


def test_delivery_contract_is_connector_neutral_but_roles_are_closed() -> None:
    value = {
        **_meta("DeliveryIntent/v1"),
        "delivery_id": "delivery:001",
        "effect_intent_id": "effect:001",
        "episode_id": "episode:001",
        "generation": 1,
        "role": "outcome_delivery",
        "state": "prepared",
        "operation": "telegram.message.send",
        "recipient": "fixture:conversation:001",
        "channel": "telegram",
        "frozen_content_sha256": H,
        "reply_to": None,
        "proof_floor": "valid client ACK",
    }
    DeliveryIntent.model_validate(value)
    value["role"] = "status_update"
    with pytest.raises(ValidationError):
        DeliveryIntent.model_validate(value)


def test_delivery_offer_and_receipt_support_three_policy_selected_proof_modes() -> None:
    corpus = json.loads(
        (EVA2_ROOT / "schemas" / "contracts" / "contract-corpus-v1.json").read_bytes()
    )
    accepted = {
        case["caseId"]: json.loads(case["wireUtf8"])
        for case in corpus["cases"]
        if case["expect"] == "accept"
    }

    offers = {
        mode: DeliveryOffer.model_validate(accepted[f"valid-delivery-offer-{mode}"])
        for mode in ("provider", "server", "client")
    }
    receipts = {
        mode: DeliveryReceipt.model_validate(accepted[f"valid-delivery-receipt-{mode}"])
        for mode in ("provider", "server", "client")
    }
    offer_modes = {offer.proof.mode for offer in offers.values()}
    receipt_modes = {receipt.proof.mode for receipt in receipts.values()}
    assert offer_modes == receipt_modes == {"provider", "server", "client"}
    for mode in offers:
        receipts[mode].validate_against_offer(offers[mode])

    alternative_client = deepcopy(accepted["valid-delivery-receipt-client"])
    alternative_offer = deepcopy(accepted["valid-delivery-offer-client"])
    alternative_client["proof"]["client_ack"]["algorithm"] = "ed25519-http-ack"
    alternative_offer["proof"]["ack_algorithm"] = "ed25519-http-ack"
    alternative_client["delivery_offer_sha256"] = canonical_sha256(alternative_offer)
    alternative_client["role"] = "confirmation_delivery"
    DeliveryReceipt.model_validate(alternative_client).validate_against_offer(
        DeliveryOffer.model_validate(alternative_offer)
    )

    missing_ack = deepcopy(accepted["valid-delivery-receipt-client"])
    del missing_ack["proof"]["client_ack"]
    with pytest.raises(ValidationError):
        DeliveryReceipt.model_validate(missing_ack)

    downgraded = deepcopy(accepted["valid-delivery-receipt-client"])
    downgraded["proof"] = deepcopy(accepted["valid-delivery-receipt-provider"]["proof"])
    structurally_valid_downgrade = DeliveryReceipt.model_validate(downgraded)
    with pytest.raises(ValueError, match="proof mode"):
        structurally_valid_downgrade.validate_against_offer(offers["client"])

    wrong_offer_hash = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_offer_hash["delivery_offer_sha256"] = "2" * 64
    structurally_valid_offer_hash = DeliveryReceipt.model_validate(wrong_offer_hash)
    with pytest.raises(ValueError, match="offer bytes"):
        structurally_valid_offer_hash.validate_against_offer(offers["client"])

    wrong_delivery = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_delivery["delivery_id"] = "delivery:other:001"
    wrong_delivery["proof"]["client_ack"]["delivery_id"] = "delivery:other:001"
    structurally_valid_delivery = DeliveryReceipt.model_validate(wrong_delivery)
    with pytest.raises(ValueError, match="another delivery"):
        structurally_valid_delivery.validate_against_offer(offers["client"])

    wrong_content = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_content["content_sha256"] = "2" * 64
    wrong_content["proof"]["client_ack"]["content_sha256"] = "2" * 64
    structurally_valid_content = DeliveryReceipt.model_validate(wrong_content)
    with pytest.raises(ValueError, match="content"):
        structurally_valid_content.validate_against_offer(offers["client"])

    wrong_wire = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_wire["wire_sha256"] = "2" * 64
    wrong_wire["proof"]["client_ack"]["wire_sha256"] = "2" * 64
    structurally_valid_wire = DeliveryReceipt.model_validate(wrong_wire)
    with pytest.raises(ValueError, match="wire bytes"):
        structurally_valid_wire.validate_against_offer(offers["client"])

    wrong_challenge = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_challenge["proof"]["client_ack"]["challenge"] = "challenge:other:001"
    structurally_valid_challenge = DeliveryReceipt.model_validate(wrong_challenge)
    with pytest.raises(ValueError, match="challenge"):
        structurally_valid_challenge.validate_against_offer(offers["client"])

    wrong_request = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_request["proof"]["client_ack"]["request_ref"] = "request:other:001"
    structurally_valid_request = DeliveryReceipt.model_validate(wrong_request)
    with pytest.raises(ValueError, match="request"):
        structurally_valid_request.validate_against_offer(offers["client"])

    wrong_algorithm = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_algorithm["proof"]["client_ack"]["algorithm"] = "ed25519-http-ack"
    structurally_valid_algorithm = DeliveryReceipt.model_validate(wrong_algorithm)
    with pytest.raises(ValueError, match="algorithm"):
        structurally_valid_algorithm.validate_against_offer(offers["client"])

    wrong_key = deepcopy(accepted["valid-delivery-receipt-client"])
    wrong_key["proof"]["client_ack"]["public_key_sha256"] = "2" * 64
    structurally_valid_key = DeliveryReceipt.model_validate(wrong_key)
    with pytest.raises(ValueError, match="key"):
        structurally_valid_key.validate_against_offer(offers["client"])


def test_global_primitives_and_relative_artifact_paths_are_unambiguous() -> None:
    timestamp = TypeAdapter(Timestamp)
    calendar_date = TypeAdapter(CalendarDate)
    relative_path = TypeAdapter(RelativeArtifactPath)

    timestamp.validate_python("2024-02-29T23:59:59Z")
    calendar_date.validate_python("2024-02-29")
    for value in (
        "records/_meta",
        "records/-meta",
        "records/.hidden/....",
        "records/.../artifact.jsonl",
    ):
        assert relative_path.validate_python(value) == value

    for adapter, value in (
        (timestamp, "2026-02-30T12:00:00Z"),
        (calendar_date, "2026-02-30"),
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)

    for value in (
        "/absolute.jsonl",
        ".hidden",
        "...",
        "..hidden",
        r"records\escape.jsonl",
        "records//escape.jsonl",
        "records/./escape.jsonl",
        "records/../escape.jsonl",
        "records/escape.jsonl\n",
    ):
        with pytest.raises(ValidationError):
            relative_path.validate_python(value)


def test_outcome_view_status_matches_the_complete_step_aggregate() -> None:
    completed = {
        **_meta("OutcomeView/v1"),
        "episode_id": "episode:001",
        "generation": 1,
        "steps": [
            {
                "step_id": "step:001",
                "status": "completed",
                "human_summary": "Klaar",
                "evidence_refs": ["evidence:001"],
                "unresolved_refs": [],
            }
        ],
        "overall_status": "completed",
        "created_at": NOW,
    }
    OutcomeView.model_validate(completed)

    mixed = deepcopy(completed)
    mixed["overall_status"] = "partially_completed"
    mixed["steps"].append(
        {
            "step_id": "step:002",
            "status": "failed",
            "human_summary": "Niet gelukt",
            "evidence_refs": [],
            "unresolved_refs": ["failure:001"],
        }
    )
    OutcomeView.model_validate(mixed)

    completed_with_failure = deepcopy(mixed)
    completed_with_failure["overall_status"] = "completed"
    with pytest.raises(ValidationError):
        OutcomeView.model_validate(completed_with_failure)

    partial_without_incomplete = deepcopy(completed)
    partial_without_incomplete["overall_status"] = "partially_completed"
    with pytest.raises(ValidationError):
        OutcomeView.model_validate(partial_without_incomplete)


def test_presence_context_and_each_model_purpose_are_complete_explicit_inputs() -> None:
    corpus = json.loads(
        (EVA2_ROOT / "schemas" / "contracts" / "contract-corpus-v1.json").read_bytes()
    )
    accepted = {
        case["caseId"]: json.loads(case["wireUtf8"])
        for case in corpus["cases"]
        if case["expect"] == "accept"
    }
    context = PresenceContext.model_validate(accepted["valid-presence-context"])
    assert context.identity_pack.state == "current"
    assert context.identity_pack.voice_style.name
    assert context.relationships
    assert context.current_utterance.text
    assert context.episode_goal.description
    assert context.valid_consent_grants
    assert context.connector_results
    assert context.read_observations
    assert context.effect_receipts
    assert context.effect_readbacks
    assert context.delivery_intents
    assert context.delivery_receipts
    assert context.missing_human_choice is None

    expected = {
        "plan": ("PlanModelInput/v1", "ModelDecision/v1"),
        "document-draft": ("DocumentDraftModelInput/v1", "DocumentDraft/v1"),
        "reply": ("ReplyModelInput/v1", "ReplyCandidate/v1"),
    }
    for case_suffix, (input_contract, output_contract) in expected.items():
        request = PlannerRequest.model_validate(
            accepted[f"valid-planner-request-{case_suffix}"]
        )
        assert request.model_input.contract == input_contract
        assert request.model_input.output_contract == output_contract
        assert request.model_input.presence_context.current_utterance.text
        assert (
            request.model_input.context_selection.purpose == request.model_input.purpose
        )

    schema_fields = PlannerRequest.model_json_schema()["properties"]
    assert "presence_context_sha256" not in schema_fields
    assert "context_selection_sha256" not in schema_fields


def test_reserved_effect_attempt_cannot_claim_dispatch_authority() -> None:
    value = {
        **_meta("EffectAttempt/v1"),
        "effect_attempt_id": "effect-attempt:001",
        "effect_intent_id": "effect:001",
        "ordinal": 1,
        "state": "reserved",
        "dispatch_permit_sha256": None,
        "capability_grant_id": "capability-grant:001",
        "credential_grant_id": "credential-grant:001",
        "connector_manifest_sha256": H,
        "request_sha256": H,
        "response_sha256": None,
        "error_class": "none",
        "deadline": NOW,
        "created_at": NOW,
    }
    EffectAttempt.model_validate(value)
    value["dispatch_permit_sha256"] = H
    with pytest.raises(ValidationError):
        EffectAttempt.model_validate(value)


def test_ingress_outcome_never_claims_work_for_a_conflict() -> None:
    value = {
        **_meta("IngressOutcome/v1"),
        "status": "conflict",
        "installation_id": "installation:001",
        "channel_id": "authenticated_http",
        "request_id": "request:001",
        "canonical_input_sha256": H,
        "episode_id": None,
        "generation": None,
        "pending_attention_id": None,
        "rejection_code": "request_hash_conflict",
        "decided_at": NOW,
    }
    IngressOutcome.model_validate(value)
    value["episode_id"] = "episode:invented"
    with pytest.raises(ValidationError):
        IngressOutcome.model_validate(value)


def test_presence_reply_is_candidate_or_host_fallback_and_forbids_long_dash() -> None:
    value = {
        **_meta("PresenceReply/v1"),
        "message_id": "message:001",
        "episode_id": "episode:001",
        "generation": 1,
        "outcome_view_sha256": H,
        "reply_candidate_sha256": H,
        "fallback_reason": None,
        "host_validation_sha256": H,
        "text": "De afspraak staat om 15.00 uur.",
        "claim_bindings": [],
        "identity_id": "identity:eva",
        "identity_pack_version": 1,
        "language": "nl-NL",
        "created_at": NOW,
    }
    PresenceReply.model_validate(value)
    invalid = dict(value)
    invalid["fallback_reason"] = "candidate_rejected"
    with pytest.raises(ValidationError):
        PresenceReply.model_validate(invalid)
    invalid = dict(value)
    invalid["text"] = "Gelukt \u2014 echt waar."
    with pytest.raises(ValidationError):
        PresenceReply.model_validate(invalid)


def test_platform_and_export_contracts_forbid_claim_inflation() -> None:
    platform_schema = PlatformLock.model_json_schema()
    assert platform_schema["additionalProperties"] is False
    archive_schema = EvaPortableArchive.model_json_schema()
    false_only = archive_schema["properties"]["credentials_included"]
    assert false_only["const"] is False
    assert archive_schema["properties"]["dbos_data_included"]["const"] is False


def test_4a_platform_fixture_is_design_only_and_contains_no_receipts_or_images() -> (
    None
):
    corpus = json.loads(
        (EVA2_ROOT / "schemas" / "contracts" / "contract-corpus-v1.json").read_bytes()
    )
    case = next(
        item for item in corpus["cases"] if item["caseId"] == "valid-platform-lock"
    )
    payload = json.loads(case["wireUtf8"])
    lock = PlatformLock.model_validate(payload)
    assert lock.claim_state == "selected_for_validation"
    assert all(
        target.support_status == "unsupported"
        for target in lock.tier1_targets + lock.compatibility_targets
    )
    assert all(
        target.validation_receipt_sha256 is None
        for target in lock.tier1_targets + lock.compatibility_targets
    )
    assert all(
        not image.runnable and image.image_digest is None for image in lock.oci_images
    )
    payload["tier1_targets"][0]["support_status"] = "tier1"
    payload["tier1_targets"][0]["validation_receipt_sha256"] = H
    with pytest.raises(ValidationError):
        PlatformLock.model_validate(payload)
