#!/usr/bin/env python3
"""Generate or verify Eva contract JSON Schemas and the shared wire corpus."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EVA2_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = EVA2_ROOT / "src"
SCHEMA_ROOT = EVA2_ROOT / "schemas" / "contracts"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from eva.contracts import (  # noqa: E402
    CANONICAL_JSON_PROFILE,
    CONTRACT_REGISTRY,
    JSON_SAFE_INTEGER_MAX,
    MAX_CONTRACT_BYTES,
    canonical_json_bytes,
    schema_filename,
)

H = "0" * 64
H1 = "1" * 64
NOW = "2026-08-20T12:00:00Z"
LATER = "2026-08-20T12:05:00Z"
SIGNATURE = "A" * 86


def _meta(contract: str) -> dict[str, Any]:
    try:
        schema_version = int(contract.rsplit("/v", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"contract lacks a numeric major version: {contract}") from exc
    return {"contract": contract, "schema_version": schema_version}


def _resource(resource_type: str = "calendar") -> dict[str, Any]:
    return {
        "resource_type": resource_type,
        "resource_id": f"fixture:{resource_type}:001",
        "scope_sha256": H1,
    }


def valid_examples() -> dict[str, dict[str, Any]]:
    """Complete, meaningful examples for every registered contract."""

    installation_id = "installation:fixture:001"
    tenant_id = "tenant:fixture:001"
    principal_id = "fixture:principal:owner:001"
    conversation_id = "fixture:conversation:noor:001"
    episode_id = "episode:noor:001"
    request_id = "fixture:http-request:noor:001"
    retention = "2026-09-19T12:00:00Z"

    turn = {
        **_meta("TurnEnvelope/v1"),
        "installation_id": installation_id,
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "principal_binding_id": "principal-binding:001",
        "authentication_evidence_sha256": H1,
        "channel_id": "authenticated_http",
        "request_id": request_id,
        "message_id": "message:noor:001",
        "conversation_id": conversation_id,
        "content_type": "text/plain",
        "text": "Plan de afspraak met Noor en vertel wat werkelijk is gelukt.",
        "raw_bytes_sha256": H1,
        "received_at": NOW,
    }
    identity = {
        **_meta("IdentityPack/v1"),
        "identity_id": "identity:eva",
        "pack_version": 1,
        "state": "current",
        "display_name": "Eva",
        "pronouns": ["zij", "haar"],
        "language": "nl-NL",
        "voice_style": {
            "name": "warm en praktisch",
            "qualities": ["nieuwsgierig", "beknopt"],
            "preferred_wording": ["zeg wat werkelijk gelukt is"],
            "forbidden_wording": ["technisch intern jargon"],
            "maximum_paragraphs": 4,
        },
        "values": ["eerlijkheid", "zorgvuldigheid"],
        "temperament": ["warm", "licht eigenwijs"],
        "humour": "subtiel",
        "conversation_boundaries": ["geen onbewezen succesclaim"],
        "channel_presentations": [
            {
                "channel_class": "authenticated_http",
                "greeting": None,
                "signoff": None,
                "concise_by_default": True,
            }
        ],
        "privacy_preferences": ["deel minimaal"],
        "consent_preferences": ["vraag alleen wanneer nodig"],
        "initiative_preferences": ["meld relevante deelresultaten"],
        "unknown_relation_preferences": [],
        "issuer": installation_id,
        "source_sha256": H1,
        "signature_algorithm": "Ed25519",
        "signature": SIGNATURE,
        "issued_at": NOW,
    }
    identity_pack_sha256 = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    policy = {
        **_meta("PolicyPack/v1"),
        "policy_id": "policy:first-vertical:001",
        "policy_version": 1,
        "effect_policies": [
            {
                "operation": "calendar.event.create",
                "effect_class": "effect",
                "consent_exempt": False,
                "proof_floor": "provider receipt and independent readback",
                "maximum_attempts": 2,
            }
        ],
        "budgets": {
            "maximum_model_attempts": 4,
            "maximum_connector_calls": 12,
            "maximum_visible_deliveries": 2,
            "maximum_wall_clock_ms": 120_000,
            "maximum_input_tokens": 30_000,
            "maximum_output_tokens": 5_000,
            "maximum_provider_cost_euro_cents": 150,
        },
        "retry_policy_id": "retry:first-vertical:001",
        "retention_policy_id": "retention:first-vertical:001",
        "privacy_policy_id": "privacy:first-vertical:001",
        "source_sha256": H,
        "release_signature_sha256": H1,
        "effective_at": NOW,
    }
    consent = {
        **_meta("ConsentGrant/v1"),
        "consent_grant_id": "consent:calendar:001",
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "principal_binding_id": "principal-binding:001",
        "authentication_evidence_ref": "evidence:authentication:001",
        "source_message_ref": turn["message_id"],
        "operation": "calendar.event.create",
        "resource_scope": _resource("calendar"),
        "scoped_payload_sha256": H1,
        "episode_id": episode_id,
        "generation": 1,
        "reuse_policy": "single_use",
        "state": "active",
        "granted_at": NOW,
        "expires_at": LATER,
    }
    read_intent = {
        **_meta("ReadIntent/v1"),
        "read_intent_id": "read-intent:mail:001",
        "episode_id": episode_id,
        "generation": 1,
        "purpose": "business_read",
        "effect_intent_id": None,
        "operation": "mail.message.search",
        "resource_scope": _resource("mailbox"),
        "query_sha256": H1,
        "maximum_freshness_ms": 15_000,
        "maximum_attempts": 1,
        "capability_id": "capability:mail-search:001",
        "deadline": LATER,
    }
    read_attempt = {
        **_meta("ReadAttempt/v1"),
        "read_attempt_id": "read-attempt:mail:001",
        "read_intent_id": read_intent["read_intent_id"],
        "ordinal": 1,
        "state": "observed",
        "capability_grant_id": "capability-grant:mail:001",
        "credential_grant_id": "credential-grant:mail:001",
        "connector_manifest_sha256": H,
        "request_sha256": H1,
        "response_sha256": H,
        "deadline": LATER,
        "created_at": NOW,
    }
    read_observation = {
        **_meta("ReadObservation/v1"),
        "observation_id": "observation:mail:001",
        "read_intent_id": read_intent["read_intent_id"],
        "read_attempt_id": read_attempt["read_attempt_id"],
        "provider_resource_id": "mail:noor:017",
        "retrieved_at": NOW,
        "observed_at": NOW,
        "normalized_value": {"sender": "Noor BV", "subject": "Afspraak"},
        "response_sha256": H,
        "trust": "verified_external",
        "evidence_ref": "evidence:mail:001",
    }
    effect_intent = {
        **_meta("EffectIntent/v1"),
        "effect_intent_id": "effect:calendar:001",
        "episode_id": episode_id,
        "generation": 1,
        "plan_step_id": "step:calendar-create:001",
        "connector_binding_id": "fixture:binding:calendar:001",
        "capability_id": "capability:calendar-create:001",
        "operation": "calendar.event.create",
        "resource_scope": _resource("calendar"),
        "frozen_payload_sha256": H1,
        "idempotency_key": "idempotency:calendar:noor:001",
        "proof_floor": "provider receipt plus exact independent readback",
        "state": "prepared",
        "supersedes_effect_intent_id": None,
        "deadline": LATER,
    }
    effect_attempt = {
        **_meta("EffectAttempt/v1"),
        "effect_attempt_id": "effect-attempt:calendar:001",
        "effect_intent_id": effect_intent["effect_intent_id"],
        "ordinal": 1,
        "state": "reserved",
        "dispatch_permit_sha256": None,
        "capability_grant_id": "capability-grant:calendar:001",
        "credential_grant_id": "credential-grant:calendar:001",
        "connector_manifest_sha256": H,
        "request_sha256": H1,
        "response_sha256": None,
        "error_class": "none",
        "deadline": LATER,
        "created_at": NOW,
    }
    effect_receipt = {
        **_meta("EffectReceipt/v1"),
        "receipt_id": "receipt:calendar:001",
        "effect_intent_id": effect_intent["effect_intent_id"],
        "effect_attempt_id": effect_attempt["effect_attempt_id"],
        "decision": "accepted",
        "provider_resource_id": "calendar:event:001",
        "provider_receipt_sha256": H,
        "request_payload_sha256": H1,
        "provider_time": NOW,
        "evidence_ref": "evidence:calendar-receipt:001",
    }
    effect_readback = {
        **_meta("EffectReadback/v1"),
        "readback_id": "readback:calendar:001",
        "effect_intent_id": effect_intent["effect_intent_id"],
        "read_intent_id": "read-intent:calendar-verify:001",
        "read_attempt_id": "read-attempt:calendar-verify:001",
        "provider_resource_id": "calendar:event:001",
        "idempotency_key": effect_intent["idempotency_key"],
        "request_sha256": H1,
        "observed_at": NOW,
        "state_sha256": H,
        "match_status": "matches",
        "evidence_ref": "evidence:calendar-readback:001",
    }
    delivery_intent = {
        **_meta("DeliveryIntent/v1"),
        "delivery_id": "delivery:outcome:001",
        "effect_intent_id": "effect:outcome-delivery:001",
        "episode_id": episode_id,
        "generation": 1,
        "role": "outcome_delivery",
        "state": "prepared",
        "operation": "channel.result.deliver",
        "recipient": conversation_id,
        "channel": "authenticated_http",
        "frozen_content_sha256": H,
        "reply_to": turn["message_id"],
        "proof_floor": "policy-bound authenticated client acknowledgement",
    }
    delivery_offer_client = {
        **_meta("DeliveryOffer/v1"),
        "delivery_id": delivery_intent["delivery_id"],
        "installation_id": installation_id,
        "channel_id": "authenticated_http",
        "request_id": request_id,
        "conversation_id": conversation_id,
        "content_sha256": H,
        "wire_sha256": H1,
        "proof": {
            "mode": "client",
            "challenge": "challenge:001",
            "ack_algorithm": "p256-es256",
            "ack_key_sha256": H1,
        },
        "offered_at": NOW,
        "expires_at": LATER,
    }
    client_ack = {
        "algorithm": "p256-es256",
        "request_ref": request_id,
        "delivery_id": delivery_intent["delivery_id"],
        "content_sha256": delivery_offer_client["content_sha256"],
        "wire_sha256": H1,
        "challenge": "challenge:001",
        "signature": SIGNATURE,
        "public_key_sha256": H1,
    }
    delivery_receipt = {
        **_meta("DeliveryReceipt/v1"),
        "delivery_receipt_id": "delivery-receipt:outcome:001",
        "delivery_id": delivery_intent["delivery_id"],
        "delivery_offer_sha256": hashlib.sha256(
            canonical_json_bytes(delivery_offer_client)
        ).hexdigest(),
        "content_sha256": delivery_offer_client["content_sha256"],
        "wire_sha256": delivery_offer_client["wire_sha256"],
        "effect_attempt_id": "effect-attempt:outcome:001",
        "role": "outcome_delivery",
        "accepted_at": NOW,
        "proof": {"mode": "client", "client_ack": client_ack},
    }
    delivery_offer_v2 = {
        **_meta("DeliveryOffer/v2"),
        "delivery_id": delivery_intent["delivery_id"],
        "installation_id": installation_id,
        "channel_id": "authenticated_http",
        "request_id": request_id,
        "conversation_id": conversation_id,
        "content_sha256": H,
        "wire_sha256": H1,
        "proof": {
            "mode": "client",
            "challenge": "challenge:001",
            "ack_algorithm": "P-256 ES256",
            "ack_key_sha256": H1,
            "signature_format": "P1363",
        },
        "offered_at": NOW,
        "expires_at": LATER,
    }
    client_ack_v2 = {
        "algorithm": "P-256 ES256",
        "signature_format": "P1363",
        "request_ref": request_id,
        "delivery_id": delivery_intent["delivery_id"],
        "presence_reply_sha256": delivery_offer_v2["content_sha256"],
        "wire_sha256": delivery_offer_v2["wire_sha256"],
        "challenge": delivery_offer_v2["proof"]["challenge"],
        "signature": SIGNATURE,
        "public_key_sha256": H1,
    }
    delivery_receipt_v2 = {
        **_meta("DeliveryReceipt/v2"),
        "delivery_receipt_id": "delivery-receipt:outcome:v2:001",
        "delivery_id": delivery_intent["delivery_id"],
        "delivery_offer_sha256": hashlib.sha256(
            canonical_json_bytes(delivery_offer_v2)
        ).hexdigest(),
        "content_sha256": delivery_offer_v2["content_sha256"],
        "wire_sha256": delivery_offer_v2["wire_sha256"],
        "effect_attempt_id": "effect-attempt:outcome:001",
        "role": "outcome_delivery",
        "accepted_at": NOW,
        "proof": {"mode": "client", "client_ack": client_ack_v2},
    }
    connector_result = {
        **_meta("ConnectorResult/v1"),
        "connector_id": "connector:calendar:fixture",
        "request_id": "connector-request:001",
        "correlation_id": "correlation:001",
        "operation": "effect.reconcile",
        "request_sha256": H1,
        "connector_manifest_sha256": H,
        "outcome": {
            "kind": "reconcile_result",
            "idempotency_key": effect_intent["idempotency_key"],
            "resource_scope": _resource("calendar"),
            "request_payload_sha256": H1,
            "result": "found_same",
            "provider_resource_id": "calendar:event:001",
            "evidence_sha256": H,
        },
        "completed_at": NOW,
    }
    connector_inline_null = {
        **_meta("ConnectorResult/v1"),
        "connector_id": "connector:mail:fixture",
        "request_id": "connector-request:002",
        "correlation_id": "correlation:002",
        "operation": "mail.message.read",
        "request_sha256": H,
        "connector_manifest_sha256": H1,
        "outcome": {
            "kind": "read_ok",
            "provider_resource_id": "mail:noor:017",
            "retrieved_at": NOW,
            "response_sha256": H1,
            "payload": {
                "representation": "inline",
                "output_schema_sha256": H,
                "normalized_payload": None,
            },
        },
        "completed_at": NOW,
    }
    outcome_view = {
        **_meta("OutcomeView/v1"),
        "episode_id": episode_id,
        "generation": 1,
        "steps": [
            {
                "step_id": "step:calendar-create:001",
                "status": "completed",
                "human_summary": "Afspraak is bevestigd",
                "evidence_refs": [effect_readback["evidence_ref"]],
                "unresolved_refs": [],
            }
        ],
        "overall_status": "completed",
        "created_at": NOW,
    }
    relationship = {
        **_meta("RelationshipBinding/v1"),
        "relationship_binding_id": "relationship:owner:001",
        "installation_id": installation_id,
        "tenant_id": tenant_id,
        "identity_id": identity["identity_id"],
        "principal_id": principal_id,
        "related_principal_id": "fixture:principal:noor:001",
        "relationship_class": "business_contact",
        "preferred_address": "Noor",
        "source_event_id": "source-event:relationship:001",
        "trust": "direct_user_confirmed",
        "confirmation_status": "confirmed",
        "valid_from": NOW,
        "valid_until": None,
        "allowed_channel_ids": ["authenticated_http"],
        "channel_binding_evidence_refs": ["evidence:principal-binding:001"],
        "privacy_class": "confidential",
        "context_scopes": ["conversation:noor"],
        "retention_until": retention,
        "delete_sla_at": retention,
    }
    presence_context = {
        **_meta("PresenceContext/v1"),
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "conversation_id": conversation_id,
        "episode_id": episode_id,
        "generation": 1,
        "identity_pack": identity,
        "identity_pack_sha256": identity_pack_sha256,
        "relationships": [relationship],
        "current_utterance": {
            "turn_ref": request_id,
            "message_id": turn["message_id"],
            "text": turn["text"],
            "received_at": NOW,
        },
        "episode_goal": {
            "goal_id": "goal:noor:001",
            "source_message_ref": turn["message_id"],
            "description": "Plan de afspraak, maak de bevestiging en koppel eerlijk terug.",
        },
        "confirmed_facts": [
            {
                "fact_id": "fact:noor-company:001",
                "subject": "Noor",
                "predicate": "customer.company",
                "value": "Noor BV",
                "source_ref": "observation:erp:001",
                "evidence_ref": "evidence:erp:001",
                "observed_at": NOW,
                "trust": "verified_external",
            }
        ],
        "conversation": [
            {
                "message_id": turn["message_id"],
                "role": "user",
                "text": turn["text"],
                "created_at": NOW,
            }
        ],
        "valid_consent_grants": [consent],
        "connector_results": [connector_result],
        "read_observations": [read_observation],
        "effect_receipts": [effect_receipt],
        "effect_readbacks": [effect_readback],
        "delivery_intents": [delivery_intent],
        "delivery_receipts": [delivery_receipt],
        "unresolved_evidence_refs": [],
        "missing_human_choice": None,
        "token_budget": 8_000,
    }
    context_selections = {
        purpose: {
            **_meta("ContextSelection/v1"),
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "conversation_id": conversation_id,
            "purpose": purpose,
            "selected_source_refs": [
                turn["message_id"],
                effect_readback["evidence_ref"],
            ],
            "excluded_source_reasons": {
                "mail:untrusted:001": "onbetrouwbare instructietekst"
            },
            "context_sha256": hashlib.sha256(
                canonical_json_bytes(presence_context)
            ).hexdigest(),
            "selected_at": NOW,
        }
        for purpose in ("plan", "document_draft", "reply")
    }
    model_inputs = {
        "plan": {
            **_meta("PlanModelInput/v1"),
            "purpose": "plan",
            "output_contract": "ModelDecision/v1",
            "presence_context": presence_context,
            "context_selection": context_selections["plan"],
            "policy_pack_sha256": H1,
            "allowed_operations": ["mail.message.search", "calendar.event.create"],
            "maximum_steps": 8,
        },
        "document_draft": {
            **_meta("DocumentDraftModelInput/v1"),
            "purpose": "document_draft",
            "output_contract": "DocumentDraft/v1",
            "presence_context": presence_context,
            "context_selection": context_selections["document_draft"],
            "policy_pack_sha256": H1,
            "document_goal": "Maak een korte afspraakbevestiging voor Noor.",
            "target_mime_type": "text/plain",
            "source_refs": ["observation:erp:001", effect_readback["readback_id"]],
            "privacy_class": "confidential",
            "owner_principal_id": principal_id,
            "retention_until": retention,
        },
        "reply": {
            **_meta("ReplyModelInput/v1"),
            "purpose": "reply",
            "output_contract": "ReplyCandidate/v1",
            "presence_context": presence_context,
            "context_selection": context_selections["reply"],
            "policy_pack_sha256": H1,
            "outcome_view": outcome_view,
            "allowed_claim_refs": ["claim:calendar:001"],
        },
    }

    examples: dict[str, dict[str, Any]] = {}

    def add(case_id: str, payload: dict[str, Any]) -> None:
        examples[case_id] = payload

    add(
        "valid-attention-wake",
        {
            **_meta("AttentionWake/v1"),
            "attention_id": "attention:noor:001",
            "episode_id": episode_id,
            "generation": 1,
            "kind": "continuation",
            "state": "pending",
            "reason": "voer het gevalideerde plan verder uit",
            "not_before": NOW,
            "deadline": LATER,
            "lease_owner": None,
            "lease_expires_at": None,
            "ordinal": 1,
        },
    )
    add(
        "valid-capability-grant",
        {
            **_meta("CapabilityGrant/v1"),
            "grant_id": "capability-grant:calendar:001",
            "tenant_id": tenant_id,
            "episode_id": episode_id,
            "generation": 1,
            "capability_id": "capability:calendar-create:001",
            "connector_binding_id": "fixture:binding:calendar:001",
            "operation": "calendar.event.create",
            "resource_scope": _resource("calendar"),
            "authorized_payload_sha256": H1,
            "policy_sha256": H,
            "nonce": "nonce:capability:001",
            "issued_at": NOW,
            "deadline": LATER,
        },
    )
    add(
        "valid-capability-manifest",
        {
            **_meta("CapabilityManifest/v1"),
            "capability_id": "capability:calendar-create:001",
            "connector_id": "connector:calendar:fixture",
            "operation": "calendar.event.create",
            "input_schema_sha256": H,
            "output_schema_sha256": H1,
            "effect_class": "effect",
            "required_provider_scopes": ["calendar.write"],
            "proof_floor": "provider receipt and exact readback",
            "maximum_freshness_ms": None,
            "executable_sha256": H,
            "sandbox_profile_sha256": H1,
        },
    )
    add(
        "valid-claim-evidence-binding",
        {
            **_meta("ClaimEvidenceBinding/v1"),
            "binding_id": "claim-binding:calendar:001",
            "claim_ref": "claim:calendar:001",
            "outcome_view_id": "outcome-view:noor:001",
            "evidence_refs": [effect_readback["evidence_ref"]],
            "proof_floor": "exact independent readback",
            "verdict": "proven",
            "bound_at": NOW,
        },
    )
    add(
        "valid-commitment",
        {
            **_meta("Commitment/v1"),
            "commitment_id": "commitment:noor:001",
            "episode_id": episode_id,
            "generation": 1,
            "kind": "complete_request",
            "state": "open",
            "description": "Plan en bevestig de afspraak",
            "continuation_ref": "attention:noor:001",
            "due_at": LATER,
            "updated_at": NOW,
        },
    )
    add(
        "valid-connector-binding",
        {
            **_meta("ConnectorBinding/v1"),
            "binding_id": "fixture:binding:calendar:001",
            "installation_id": installation_id,
            "tenant_id": tenant_id,
            "owner_principal_id": principal_id,
            "provider_id": "provider:calendar:fixture",
            "provider_account_id": "account:calendar:001",
            "provider_resource_id": "calendar:primary",
            "confirmed_operations": ["calendar.event.create"],
            "confirmed_scopes": ["calendar.write"],
            "credential_reference": "credential-ref:calendar:001",
            "verification_evidence_ref": "evidence:binding:calendar:001",
            "verified_at": NOW,
            "status": "verified",
        },
    )
    add(
        "valid-connector-candidate",
        {
            **_meta("ConnectorCandidate/v1"),
            "candidate_id": "connector-candidate:calendar:001",
            "documentation_snapshot_sha256": H,
            "source_bundle_sha256": H1,
            "dependency_lock_sha256": H,
            "manifest_sha256": H1,
            "executable_sha256": H,
            "requested_operations": ["calendar.event.create"],
            "state": "quarantined",
            "created_at": NOW,
        },
    )
    request_payload = {"duration_minutes": 30, "starts_at": "2026-08-26T15:00:00Z"}
    add(
        "valid-connector-envelope",
        {
            **_meta("ConnectorEnvelope/v1"),
            "installation_id": installation_id,
            "tenant_id": tenant_id,
            "request_id": "connector-request:calendar:001",
            "correlation_id": "correlation:calendar:001",
            "episode_id": episode_id,
            "generation": 1,
            "connector_id": "connector:calendar:fixture",
            "connector_binding_id": "fixture:binding:calendar:001",
            "capability_id": "capability:calendar-create:001",
            "operation": "calendar.event.create",
            "resource_scope": _resource("calendar"),
            "deadline": LATER,
            "request_payload_sha256": hashlib.sha256(
                canonical_json_bytes(request_payload)
            ).hexdigest(),
            "connector_manifest_sha256": H,
            "capability_grant_id": "capability-grant:calendar:001",
            "credential_grant_id": "credential-grant:calendar:001",
            "request_payload": request_payload,
        },
    )
    add(
        "valid-connector-manifest",
        {
            **_meta("ConnectorManifest/v1"),
            "connector_id": "connector:calendar:fixture",
            "connector_version": "1.0.0",
            "protocol_version": "EvaConnectorProtocol/v1",
            "executable_sha256": H,
            "sandbox_profile_sha256": H1,
            "operations": [
                {
                    "operation": "calendar.event.create",
                    "input_schema_sha256": H,
                    "output_schema_sha256": H1,
                    "external_calls_per_invocation": 1,
                    "writes_provider_state": True,
                }
            ],
            "maximum_protocol_frame_bytes": 1_048_576,
            "maximum_protocol_frames_per_invocation": 2,
            "maximum_stdout_bytes": 2_101_248,
            "maximum_stderr_bytes": 65_536,
            "issued_at": NOW,
        },
    )
    add("valid-connector-result-reconcile", connector_result)
    add("valid-consent-grant", consent)
    add("valid-context-selection", context_selections["plan"])
    add(
        "valid-credential-grant",
        {
            **_meta("CredentialGrant/v1"),
            "credential_grant_id": "credential-grant:calendar:001",
            "connector_binding_id": "fixture:binding:calendar:001",
            "model_provider_profile_id": None,
            "worker_id": "worker:calendar:001",
            "executable_sha256": H,
            "provider_scopes": ["calendar.write"],
            "secret_descriptor_id": "secret-descriptor:calendar:001",
            "nonce": "nonce:credential:001",
            "issued_at": NOW,
            "deadline": LATER,
        },
    )
    rpc_manifest = deepcopy(examples["valid-connector-manifest"])
    rpc_manifest_sha256 = hashlib.sha256(canonical_json_bytes(rpc_manifest)).hexdigest()
    rpc_connector_binding = deepcopy(examples["valid-connector-binding"])
    rpc_capability_manifest = deepcopy(examples["valid-capability-manifest"])
    rpc_capability_grant = deepcopy(examples["valid-capability-grant"])
    rpc_credential_grant = deepcopy(examples["valid-credential-grant"])
    rpc_envelope = deepcopy(examples["valid-connector-envelope"])
    rpc_envelope["request_id"] = "connector-request:invoke:001"
    rpc_envelope["correlation_id"] = "correlation:connector-rpc:001"
    rpc_envelope["connector_manifest_sha256"] = rpc_manifest_sha256
    rpc_capability_grant["authorized_payload_sha256"] = rpc_envelope[
        "request_payload_sha256"
    ]
    initialize_request = {
        **_meta("ConnectorInitializeRequest/v1"),
        "jsonrpc": "2.0",
        "id": "connector-request:initialize:001",
        "method": "connector.initialize",
        "params": {
            "protocol_version": "EvaConnectorProtocol/v1",
            "request_id": "connector-request:initialize:001",
            "correlation_id": rpc_envelope["correlation_id"],
            "connector_id": rpc_manifest["connector_id"],
            "connector_manifest_sha256": rpc_manifest_sha256,
            "worker_id": rpc_credential_grant["worker_id"],
            "worker_executable_sha256": rpc_manifest["executable_sha256"],
            "connector_binding": rpc_connector_binding,
            "capability_manifest": rpc_capability_manifest,
            "capability_grant": rpc_capability_grant,
            "credential_grant": rpc_credential_grant,
            "deadline": LATER,
        },
    }
    add("valid-connector-initialize-request", initialize_request)
    add(
        "valid-connector-initialize-success",
        {
            **_meta("ConnectorInitializeSuccess/v1"),
            "jsonrpc": "2.0",
            "id": initialize_request["id"],
            "result": {
                "protocol_version": "EvaConnectorProtocol/v1",
                "request_id": initialize_request["id"],
                "correlation_id": rpc_envelope["correlation_id"],
                "connector_id": rpc_manifest["connector_id"],
                "connector_manifest_sha256": rpc_manifest_sha256,
                "worker_id": rpc_credential_grant["worker_id"],
                "worker_executable_sha256": rpc_manifest["executable_sha256"],
                "connector_binding_sha256": hashlib.sha256(
                    canonical_json_bytes(rpc_connector_binding)
                ).hexdigest(),
                "capability_manifest_sha256": hashlib.sha256(
                    canonical_json_bytes(rpc_capability_manifest)
                ).hexdigest(),
                "capability_grant_sha256": hashlib.sha256(
                    canonical_json_bytes(rpc_capability_grant)
                ).hexdigest(),
                "credential_grant_sha256": hashlib.sha256(
                    canonical_json_bytes(rpc_credential_grant)
                ).hexdigest(),
                "initialized_at": NOW,
                "external_io_performed": False,
                "secret_bytes_received": False,
            },
        },
    )
    invoke_request = {
        **_meta("ConnectorInvokeRequest/v1"),
        "jsonrpc": "2.0",
        "id": rpc_envelope["request_id"],
        "method": "effect.execute",
        "params": rpc_envelope,
    }
    add("valid-connector-invoke-request", invoke_request)
    add(
        "valid-connector-invoke-success",
        {
            **_meta("ConnectorInvokeSuccess/v1"),
            "jsonrpc": "2.0",
            "id": rpc_envelope["request_id"],
            "result": {
                **_meta("ConnectorResult/v1"),
                "connector_id": rpc_envelope["connector_id"],
                "request_id": rpc_envelope["request_id"],
                "correlation_id": rpc_envelope["correlation_id"],
                "operation": rpc_envelope["operation"],
                "request_sha256": hashlib.sha256(
                    canonical_json_bytes(rpc_envelope)
                ).hexdigest(),
                "connector_manifest_sha256": rpc_manifest_sha256,
                "outcome": {
                    "kind": "effect_response",
                    "provider_resource_id": "calendar:event:001",
                    "provider_receipt_sha256": H,
                    "provider_time": NOW,
                },
                "completed_at": NOW,
            },
        },
    )
    add(
        "valid-connector-error-response",
        {
            **_meta("ConnectorErrorResponse/v1"),
            "jsonrpc": "2.0",
            "id": rpc_envelope["request_id"],
            "request_contract": "ConnectorInvokeRequest/v1",
            "request_method": "effect.execute",
            "request_sha256": hashlib.sha256(
                canonical_json_bytes(invoke_request)
            ).hexdigest(),
            "error": {
                "code": -32602,
                "kind": "invalid_params",
                "message": "Invalid params",
                "failure_code": "connector_params_invalid",
                "external_io_possible": False,
                "failure_class": "internal_failure",
            },
        },
    )
    add(
        "valid-connector-protocol-failure",
        {
            **_meta("ConnectorProtocolFailure/v1"),
            "request_id": rpc_envelope["request_id"],
            "request_sha256": hashlib.sha256(
                canonical_json_bytes(invoke_request)
            ).hexdigest(),
            "phase": "invoke_external_io_possible",
            "violation": "unexpected_eof",
            "failure_class": "ambiguous_transport",
            "automatic_resend_permitted": False,
            "evidence_sha256": H,
            "observed_at": NOW,
        },
    )
    add("valid-delivery-intent", delivery_intent)
    add(
        "valid-delivery-offer-client",
        delivery_offer_client,
    )
    add("valid-delivery-offer-v2-client", delivery_offer_v2)
    add("valid-delivery-receipt-client", delivery_receipt)
    add("valid-delivery-receipt-v2-client", delivery_receipt_v2)
    add(
        "valid-document-draft",
        {
            **_meta("DocumentDraft/v1"),
            "episode_id": episode_id,
            "generation": 1,
            "purpose": "document_draft",
            "mime_type": "text/plain",
            "title": "Bevestiging afspraak Noor BV",
            "content": "Beste Noor, onze afspraak staat woensdag om 15.00 uur gepland.",
            "source_refs": ["observation:erp:001", effect_readback["readback_id"]],
            "privacy_class": "confidential",
            "owner_principal_id": principal_id,
            "retention_until": retention,
        },
    )
    add(
        "valid-documentation-snapshot",
        {
            **_meta("DocumentationSnapshot/v1"),
            "snapshot_id": "documentation:calendar:001",
            "requested_capability": "calendar.event.create",
            "sources": [
                {
                    "source_id": "source:calendar-vendor:001",
                    "canonical_url": "https://calendar.example.invalid/api",
                    "fetched_sha256": H,
                    "fetched_at": NOW,
                    "trust": "vendor_primary",
                }
            ],
            "normalized_documentation_sha256": H1,
            "prompt_injection_scan": "passed",
            "created_at": NOW,
        },
    )
    add("valid-effect-attempt", effect_attempt)
    add("valid-effect-intent", effect_intent)
    add("valid-effect-readback", effect_readback)
    add("valid-effect-receipt", effect_receipt)
    add(
        "valid-episode-state",
        {
            **_meta("EpisodeState/v1"),
            "episode_id": episode_id,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "conversation_id": conversation_id,
            "ingress_request_id": request_id,
            "state": "executing",
            "current_generation": 1,
            "supersedes_generation": None,
            "last_event_id": "event:episode:001",
            "updated_at": NOW,
        },
    )
    add(
        "valid-acceptance-evidence-bundle",
        {
            **_meta("EvaAcceptanceEvidenceBundle/v1"),
            "bundle_id": "evidence-bundle:fixture:001",
            "run_class": "sim",
            "release_manifest_sha256": H,
            "source_commit_sha256": H1,
            "dependency_lock_sha256": H,
            "schema_manifest_sha256": H1,
            "fixture_sha256": H,
            "episode_id": episode_id,
            "generation": 1,
            "identity_pack_sha256": H1,
            "policy_pack_sha256": H,
            "artifacts": [
                {
                    "artifact_id": "artifact:fixture-before:001",
                    "artifact_class": "fixture_before",
                    "content_sha256": H1,
                    "object_ref_id": None,
                    "captured_at": NOW,
                }
            ],
            "tests_only": True,
            "live_readback_complete": False,
            "created_at": NOW,
        },
    )
    add(
        "valid-portable-archive",
        {
            **_meta("EvaPortableArchive/v1"),
            "archive_id": "archive:fixture:001",
            "source_installation_id": installation_id,
            "export_sequence": 1,
            "canonical_profile": "EvaCanonicalJSON/v1",
            "record_sets": [
                {
                    "contract_type": "EpisodeState/v1",
                    "relative_path": "records/EpisodeState-v1.jsonl",
                    "contract_schema_sha256": H,
                    "jsonl_sha256": H1,
                    "record_count": 1,
                    "minimum_schema_version": 1,
                    "maximum_schema_version": 1,
                }
            ],
            "json_schema_manifest_sha256": H,
            "identity_pack_sha256s": [H1],
            "encrypted_object_manifest_chunks": [],
            "encrypted_object_count": 0,
            "pending_state_in_record_sets": True,
            "pending_episode_count": 1,
            "pending_effect_intent_count": 0,
            "pending_delivery_count": 0,
            "checksums_sha256": H1,
            "dbos_data_included": False,
            "credentials_included": False,
            "installation_master_key_included": False,
            "telemetry_included": False,
            "absolute_host_paths_included": False,
            "retention_until": retention,
            "created_at": NOW,
        },
    )
    add(
        "valid-evidence-envelope",
        {
            **_meta("EvidenceEnvelope/v1"),
            "evidence_id": "evidence:calendar:001",
            "producer": "connector:calendar:fixture",
            "subject_contract": "EffectReadback/v1",
            "subject_id": effect_readback["readback_id"],
            "subject_payload_sha256": H1,
            "object_ref_id": None,
            "object_sha256": None,
            "predecessor_sha256": None,
            "provenance_refs": [effect_intent["effect_intent_id"]],
            "captured_at": NOW,
            "privacy_class": "confidential",
            "retention_until": retention,
        },
    )
    add(
        "valid-evidence-envelope-v2",
        {
            **_meta("EvidenceEnvelope/v2"),
            "evidence_id": "evidence:calendar:v2:001",
            "tenant_id": tenant_id,
            "aggregate_type": "effect_intent",
            "aggregate_id": effect_intent["effect_intent_id"],
            "sequence": 1,
            "idempotency_key": "evidence-idempotency:calendar:001",
            "producer": "connector:calendar:fixture",
            "subject_contract": "EffectReadback/v1",
            "subject_id": effect_readback["readback_id"],
            "subject_payload_sha256": H1,
            "object_ref_id": None,
            "object_sha256": None,
            "predecessor_sha256": None,
            "provenance_refs": [effect_intent["effect_intent_id"]],
            "captured_at": NOW,
            "privacy_class": "confidential",
            "retention_until": retention,
        },
    )
    add(
        "valid-experience-source-event",
        {
            **_meta("ExperienceSourceEvent/v1"),
            "source_event_id": "source-event:preference:001",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "subject_id": principal_id,
            "event_type": "preference.confirmed",
            "source_kind": "direct_user_input",
            "source_ref": turn["message_id"],
            "content_sha256": H1,
            "provenance_refs": [turn["message_id"]],
            "observed_at": NOW,
            "valid_from": NOW,
            "valid_until": None,
            "ingested_at": NOW,
            "trust": "confirmed_direct_user",
            "privacy_class": "confidential",
            "purpose": "presence.context",
            "retention_until": retention,
            "delete_sla_at": retention,
            "corrects_source_event_id": None,
            "supersedes_source_event_id": None,
            "retracts_source_event_id": None,
            "erasure_request_id": None,
        },
    )
    add("valid-identity-pack", identity)
    add(
        "valid-improvement-candidate",
        {
            **_meta("ImprovementCandidate/v1"),
            "candidate_id": "improvement:reply:001",
            "problem_statement": "Reply benoemt een bewezen deelresultaat te laat",
            "source_evidence_refs": ["evidence:reply:001"],
            "bounded_hypothesis": "Een andere contextvolgorde maakt de reply duidelijker",
            "changed_artifact_sha256s": {"presence-template": H1},
            "benchmark_case_ids": ["case:noor:001"],
            "hidden_transfer_case_id": "case:hidden:001",
            "forbidden_regressions": ["geen extra modelcall"],
            "budgets": {
                "privacy_policy_sha256": H,
                "maximum_effect_attempts": 0,
                "maximum_connector_calls": 0,
                "maximum_provider_cost_euro_micros": 100_000,
            },
            "candidate_configuration_sha256": H1,
            "rollback_release_manifest_sha256": H,
            "scope_widening": False,
            "credential_change": False,
            "policy_widening": False,
            "retention_widening": False,
            "effect_class_widening": False,
            "benchmark_or_judge_changed": False,
            "production_mutation_authorized": False,
            "state": "proposed",
            "independent_review_status": "pending",
            "expires_at": retention,
            "created_at": NOW,
        },
    )
    add(
        "valid-ingress-claim",
        {
            **_meta("IngressClaim/v1"),
            "installation_id": installation_id,
            "channel_id": "authenticated_http",
            "request_id": request_id,
            "claimant_id": "core:worker:001",
            "claim_nonce": "claim-nonce:001",
            "claimed_at": NOW,
            "expires_at": LATER,
        },
    )
    add(
        "valid-ingress-outcome",
        {
            **_meta("IngressOutcome/v1"),
            "status": "admitted",
            "installation_id": installation_id,
            "channel_id": "authenticated_http",
            "request_id": request_id,
            "canonical_input_sha256": H,
            "episode_id": episode_id,
            "generation": 1,
            "pending_attention_id": "attention:noor:001",
            "rejection_code": None,
            "decided_at": NOW,
        },
    )
    add(
        "valid-ingress-request",
        {
            **_meta("IngressRequest/v1"),
            "installation_id": installation_id,
            "tenant_id": tenant_id,
            "channel_id": "authenticated_http",
            "request_id": request_id,
            "conversation_id": conversation_id,
            "turn_envelope_sha256": H1,
            "canonical_input_sha256": H,
            "client_ack_key_sha256": H1,
            "admitted_deadline": LATER,
        },
    )
    platform_target = {
        "operating_system": "Ubuntu Server 24.04.4 LTS",
        "architecture": "amd64",
        "execution_mode": "containerized",
        "container_engine": "Docker Engine 29.7.2",
        "target_class": "tier1_release_gate",
        "validation_state": "unvalidated",
        "support_status": "unsupported",
        "validation_receipt_sha256": None,
    }
    add(
        "valid-installation-manifest",
        {
            **_meta("InstallationManifest/v1"),
            "installation_id": installation_id,
            "release_manifest_sha256": H,
            "platform_lock_sha256": H1,
            "target": platform_target,
            "identity_pack_sha256": H,
            "policy_pack_sha256": H1,
            "postgres_schema_revision": "revision:0000",
            "configured_connector_binding_ids": [],
            "connector_bindings_requiring_authorization": [
                "fixture:binding:calendar:001"
            ],
            "support_status": "unsupported",
            "secrets_included": False,
            "initialized_at": NOW,
        },
    )
    add(
        "valid-installation-remap",
        {
            **_meta("InstallationRemap/v1"),
            "remap_id": "installation-remap:fixture:001",
            "source_archive_id": "archive:fixture:001",
            "source_installation_id": "installation:source:001",
            "target_installation_id": "installation:target:001",
            "active_foreign_key_mapping_sha256": H,
            "stable_domain_object_ids_preserved": True,
            "old_effects_resumable": False,
            "connector_binding_state": "reauthorization_required",
            "pending_episode_state": "paused_reconcile_required",
            "state": "prepared",
            "signer_id": "installation:target:signer",
            "signature_algorithm": "Ed25519",
            "signed_payload_sha256": H1,
            "signature": SIGNATURE,
            "created_at": NOW,
        },
    )
    add(
        "valid-memory-fact",
        {
            **_meta("MemoryFact/v1"),
            "fact_id": "fact:meeting-preference:001",
            "tenant_id": tenant_id,
            "subject_id": principal_id,
            "predicate": "meeting.preferred_time",
            "value": "15:00",
            "source_event_ids": ["source-event:preference:001"],
            "observed_at": NOW,
            "valid_from": NOW,
            "valid_until": None,
            "trust": "confirmed_direct_user",
            "content_sha256": H1,
            "assertion_status": "active",
            "superseded_by_fact_id": None,
            "retraction_source_event_id": None,
        },
    )
    add(
        "valid-memory-recall",
        {
            **_meta("MemoryRecall/v1"),
            "recall_id": "recall:noor:001",
            "query_purpose": "presence.context",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "conversation_id": conversation_id,
            "project_id": None,
            "requested_valid_at": NOW,
            "admitted_fact_ids_sha256": H,
            "selected": [
                {
                    "rank": 1,
                    "fact_id": "fact:meeting-preference:001",
                    "source_event_ids": ["source-event:preference:001"],
                    "trust": "confirmed_direct_user",
                    "ranking_reason": "relevant voor afspraakplanning",
                }
            ],
            "excluded_reasons": {},
            "projection_version": "projection:fts:001",
            "scope_sha256": H1,
            "created_at": NOW,
        },
    )
    add(
        "valid-model-attempt",
        {
            **_meta("ModelAttempt/v1"),
            "attempt_id": "model-attempt:plan:001",
            "episode_id": episode_id,
            "generation": 1,
            "purpose": "plan",
            "ordinal": 1,
            "state": "responded",
            "provider_profile_id": "model-profile:fixture:001",
            "request_sha256": H,
            "response_sha256": H1,
            "credential_grant_id": "credential-grant:model:001",
            "maximum_input_tokens": 8_000,
            "maximum_output_tokens": 1_500,
            "timeout_ms": 45_000,
            "input_tokens_used": 500,
            "output_tokens_used": 120,
            "provider_cost_euro_micros": 30_000,
            "created_at": NOW,
        },
    )
    add(
        "valid-model-decision",
        {
            **_meta("ModelDecision/v1"),
            "episode_id": episode_id,
            "generation": 1,
            "purpose": "plan",
            "decision": "request_read",
            "actions": [
                {
                    "action_id": "action:mail-search:001",
                    "kind": "request_read",
                    "operation": "mail.message.search",
                    "resource": "confirmed mailbox",
                    "arguments": {"sender": "Noor BV"},
                    "depends_on": [],
                    "uncertainty_basis_points": 0,
                }
            ],
            "clarification_question": None,
            "wait_reason": None,
            "uncertainty_basis_points": 500,
        },
    )
    add(
        "valid-object-ref",
        {
            **_meta("ObjectRef/v1"),
            "object_id": "object:document:001",
            "ciphertext_sha256": H,
            "plaintext_sha256": H1,
            "byte_length": 128,
            "mime_type": "text/plain",
            "encryption_algorithm": "XChaCha20-Poly1305",
            "wrapped_key_ref": "wrapped-key:document:001",
            "retention_until": retention,
        },
    )
    add(
        "valid-object-store",
        {
            **_meta("ObjectStore/v1"),
            "object_store_id": "object-store:fixture:001",
            "installation_id": installation_id,
            "backend": "encrypted_local_immutable_filesystem",
            "state": "design_only_not_runnable",
            "encryption_algorithm": "XChaCha20-Poly1305",
            "key_authority_ref": "credential-broker:slot:object-store",
            "maximum_object_bytes": 1_073_741_824,
            "integrity_root_sha256": None,
            "absolute_host_path_in_contract": False,
            "verified_at": None,
        },
    )
    add("valid-outcome-view", outcome_view)
    add(
        "valid-planner-request-plan",
        {
            **_meta("PlannerRequest/v1"),
            "request_id": "model-request:plan:001",
            "model_input": model_inputs["plan"],
            "deadline": LATER,
        },
    )
    platform_lock = {
        **_meta("PlatformLock/v1"),
        "lock_id": "platform-lock:4a:001",
        "claim_state": "selected_for_validation",
        "python_version": "3.12.14",
        "postgres_version": "16.13",
        "dbos_version": "2.30.0",
        "pydantic_version": "2.12.5",
        "pydantic_ai_slim_version": "2.32.1",
        "tier1_targets": [
            platform_target,
            {
                **platform_target,
                "architecture": "arm64",
            },
        ],
        "compatibility_targets": [
            {
                **platform_target,
                "operating_system": "macOS 15",
                "architecture": "arm64",
                "container_engine": "unselected_until_4e",
                "target_class": "compatibility_target",
            },
            {
                **platform_target,
                "operating_system": "Windows 11 24H2",
                "architecture": "x86_64",
                "execution_mode": "containerized_not_native",
                "container_engine": "unselected_until_4e",
                "target_class": "compatibility_target",
            },
            {
                **platform_target,
                "architecture": "amd64_or_arm64",
                "execution_mode": "rootless_podman",
                "container_engine": "unselected_until_4e",
                "target_class": "compatibility_target",
            },
        ],
        "oci_images": [
            {
                "component": "living_core",
                "architecture": "amd64",
                "validation_state": "selected_for_validation",
                "image_repository": None,
                "image_digest": None,
                "sbom_sha256": None,
                "runnable": False,
            }
        ],
        "mutable_image_tags_allowed": False,
        "generated_at": NOW,
    }
    add("valid-platform-lock", platform_lock)
    add("valid-policy-pack", policy)
    add("valid-presence-context", presence_context)
    add(
        "valid-presence-reply",
        {
            **_meta("PresenceReply/v1"),
            "message_id": "message:assistant:001",
            "episode_id": episode_id,
            "generation": 1,
            "outcome_view_sha256": H1,
            "reply_candidate_sha256": H,
            "fallback_reason": None,
            "host_validation_sha256": H1,
            "text": "De afspraak met Noor staat woensdag om 15.00 uur.",
            "claim_bindings": [
                {
                    "claim_ref": "claim:calendar:001",
                    "evidence_refs": [effect_readback["evidence_ref"]],
                }
            ],
            "identity_id": identity["identity_id"],
            "identity_pack_version": identity["pack_version"],
            "language": "nl-NL",
            "created_at": NOW,
        },
    )
    add(
        "valid-promotion-decision",
        {
            **_meta("PromotionDecision/v1"),
            "decision_id": "promotion:calendar:001",
            "candidate_id": "connector-candidate:calendar:001",
            "decision": "keep_quarantined",
            "conformance_evidence_sha256": H,
            "security_evidence_sha256": H1,
            "canary_evidence_sha256": None,
            "operator_principal_id": principal_id,
            "authorization_evidence_ref": "evidence:operator:001",
            "decided_at": NOW,
        },
    )
    add("valid-read-attempt", read_attempt)
    add("valid-read-intent", read_intent)
    add("valid-read-observation", read_observation)
    add("valid-relationship-binding", relationship)
    add(
        "valid-release-manifest",
        {
            **_meta("ReleaseManifest/v1"),
            "release_id": "release:4a:001",
            "release_version": "0.1.0",
            "source_tree_sha256": H,
            "dependency_lock_sha256": H1,
            "platform_lock_sha256": H,
            "schema_manifest_sha256": H1,
            "artifacts": [
                {
                    "path": "oci/core-amd64",
                    "media_type": "application/vnd.oci.image.manifest.v1+json",
                    "content_sha256": H,
                    "byte_length": 1,
                }
            ],
            "sbom_sha256": H1,
            "license_manifest_sha256": H,
            "revocation_manifest_sha256": H1,
            "signatures": [
                {
                    "signer_id": "release-signer:001",
                    "algorithm": "Ed25519",
                    "signed_payload_sha256": H,
                    "signature": SIGNATURE,
                }
            ],
            "created_at": NOW,
        },
    )
    add(
        "valid-reply-candidate",
        {
            **_meta("ReplyCandidate/v1"),
            "episode_id": episode_id,
            "generation": 1,
            "purpose": "reply",
            "text": "De afspraak met Noor staat woensdag om 15.00 uur.",
            "claim_refs": ["claim:calendar:001"],
            "unresolved_refs": [],
            "identity_id": identity["identity_id"],
            "identity_pack_version": identity["pack_version"],
            "style_check": "passed",
        },
    )
    add("valid-turn-envelope", turn)
    add(
        "valid-validated-plan",
        {
            **_meta("ValidatedPlan/v1"),
            "episode_id": episode_id,
            "generation": 1,
            "model_decision_sha256": H,
            "policy_sha256": H1,
            "steps": [
                {
                    "step_id": "step:mail-search:001",
                    "kind": "read",
                    "operation": "mail.message.search",
                    "resource": "confirmed mailbox",
                    "input_schema_sha256": H,
                    "output_schema_sha256": H1,
                    "depends_on": [],
                    "capability_manifest_id": "capability:mail-search:001",
                    "proof_floor": "fresh connector observation",
                }
            ],
            "maximum_connector_calls": 12,
            "maximum_effect_attempts": 3,
            "maximum_visible_deliveries": 2,
            "deadline": LATER,
        },
    )

    # Additional cases prove every purpose-scoped model input and every delivery
    # proof mode without expanding the closed top-level contract catalogue.
    for purpose in ("document_draft", "reply"):
        add(
            f"valid-planner-request-{purpose.replace('_', '-')}",
            {
                **_meta("PlannerRequest/v1"),
                "request_id": f"model-request:{purpose}:001",
                "model_input": model_inputs[purpose],
                "deadline": LATER,
            },
        )
    add("valid-connector-result-inline-null", connector_inline_null)
    add(
        "valid-connector-result-readback",
        {
            **_meta("ConnectorResult/v1"),
            "connector_id": "connector:calendar:fixture",
            "request_id": "connector-request:readback:001",
            "correlation_id": "correlation:readback:001",
            "operation": "effect.readback",
            "request_sha256": H1,
            "connector_manifest_sha256": H,
            "outcome": {
                "kind": "readback",
                "provider_resource_id": "calendar:event:001",
                "idempotency_key": effect_intent["idempotency_key"],
                "request_sha256": H1,
                "match_status": "matches",
                "payload": {
                    "representation": "inline",
                    "output_schema_sha256": H,
                    "normalized_payload": {"starts_at": "2026-08-26T15:00:00Z"},
                },
                "observed_at": NOW,
            },
            "completed_at": NOW,
        },
    )
    add(
        "valid-connector-result-probe",
        {
            **_meta("ConnectorResult/v1"),
            "connector_id": "connector:calendar:fixture",
            "request_id": "connector-request:probe:001",
            "correlation_id": "correlation:probe:001",
            "operation": "connector.probe",
            "request_sha256": H1,
            "connector_manifest_sha256": H,
            "outcome": {
                "kind": "probe_result",
                "result": "ready",
                "observed_at": NOW,
                "no_provider_mutation_evidence_sha256": H,
                "retry_after_ms": None,
            },
            "completed_at": NOW,
        },
    )
    partial_outcome = deepcopy(outcome_view)
    partial_outcome["overall_status"] = "partially_completed"
    partial_outcome["steps"].append(
        {
            "step_id": "step:pending:001",
            "status": "failed",
            "human_summary": "Niet gelukt",
            "evidence_refs": [],
            "unresolved_refs": ["failure:001"],
        }
    )
    add("valid-outcome-view-partial-mixed", partial_outcome)
    for mode, proof in (
        (
            "provider",
            {
                "mode": "provider",
                "provider_message_id": "provider-message:001",
                "provider_receipt_sha256": H,
            },
        ),
        (
            "server",
            {
                "mode": "server",
                "provider_message_id": "provider-message:001",
                "provider_receipt_sha256": H,
                "independent_readback_id": "readback:delivery:001",
            },
        ),
    ):
        delivery_offer = {
            **_meta("DeliveryOffer/v1"),
            "delivery_id": f"delivery:{mode}:001",
            "installation_id": installation_id,
            "channel_id": f"channel:{mode}:001",
            "request_id": f"request:{mode}:001",
            "conversation_id": conversation_id,
            "content_sha256": H,
            "wire_sha256": H1,
            "proof": {"mode": mode},
            "offered_at": NOW,
            "expires_at": LATER,
        }
        add(
            f"valid-delivery-receipt-{mode}",
            {
                **_meta("DeliveryReceipt/v1"),
                "delivery_receipt_id": f"delivery-receipt:{mode}:001",
                "delivery_id": delivery_offer["delivery_id"],
                "delivery_offer_sha256": hashlib.sha256(
                    canonical_json_bytes(delivery_offer)
                ).hexdigest(),
                "content_sha256": delivery_offer["content_sha256"],
                "wire_sha256": delivery_offer["wire_sha256"],
                "effect_attempt_id": f"effect-attempt:{mode}:001",
                "role": "confirmation_delivery",
                "accepted_at": NOW,
                "proof": proof,
            },
        )
        add(
            f"valid-delivery-offer-{mode}",
            delivery_offer,
        )

    covered = {payload["contract"] for payload in examples.values()}
    if covered != set(CONTRACT_REGISTRY):
        missing = sorted(set(CONTRACT_REGISTRY) - covered)
        extra = sorted(covered - set(CONTRACT_REGISTRY))
        raise RuntimeError(
            f"valid example coverage mismatch: missing={missing}, extra={extra}"
        )
    return examples


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_corpus(examples: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build complete examples plus explicitly layered negative vectors."""

    valid_cases = [
        {
            "caseId": case_id,
            "expect": "accept",
            "validationLayer": "json_schema_and_pydantic",
            "contract": payload["contract"],
            "encoding": "utf8",
            "wireUtf8": canonical_json_bytes(payload).decode("utf-8"),
        }
        for case_id, payload in sorted(examples.items())
    ]

    def example(contract: str, *, case_id: str | None = None) -> dict[str, Any]:
        if case_id is not None:
            return examples[case_id]
        return next(
            payload for payload in examples.values() if payload["contract"] == contract
        )

    turn = example("TurnEnvelope/v1")
    extra_security = dict(turn)
    extra_security["effectAuthority"] = True
    noncanonical_timestamp = dict(turn)
    noncanonical_timestamp["received_at"] = "2026-08-20T14:00:00+02:00"
    ambiguous_inline = deepcopy(examples["valid-connector-result-inline-null"])
    ambiguous_inline["outcome"]["payload"]["object_handle"] = {
        "object_id": "object:smuggled:001",
        "capability_scope_sha256": H,
        "byte_length": 0,
        "mime_type": "application/json",
        "content_sha256": H1,
    }
    fake_platform_receipt = deepcopy(example("PlatformLock/v1"))
    fake_platform_receipt["tier1_targets"][0]["support_status"] = "tier1"
    fake_platform_receipt["tier1_targets"][0]["validation_receipt_sha256"] = H
    oversized_document = deepcopy(example("DocumentDraft/v1"))
    oversized_document["content"] = "é" * 32_769
    invalid_identity_state = deepcopy(example("IdentityPack/v1"))
    invalid_identity_state["state"] = "enabled"
    missing_current_utterance = deepcopy(example("PresenceContext/v1"))
    del missing_current_utterance["current_utterance"]
    wrong_model_input = deepcopy(examples["valid-planner-request-plan"])
    wrong_model_input["model_input"]["contract"] = "DocumentDraftModelInput/v1"
    missing_client_ack_key = deepcopy(example("DeliveryOffer/v1"))
    del missing_client_ack_key["proof"]["ack_key_sha256"]
    provider_with_client_ack = deepcopy(examples["valid-delivery-receipt-provider"])
    provider_with_client_ack["proof"]["client_ack"] = deepcopy(
        example("DeliveryReceipt/v1")["proof"]["client_ack"]
    )
    mismatched_client_ack = deepcopy(example("DeliveryReceipt/v1"))
    mismatched_client_ack["proof"]["client_ack"]["delivery_id"] = "delivery:other:001"
    mismatched_connector_hash = deepcopy(example("ConnectorEnvelope/v1"))
    mismatched_connector_hash["request_payload_sha256"] = H1
    mismatched_identity_hash = deepcopy(example("PresenceContext/v1"))
    mismatched_identity_hash["identity_pack_sha256"] = H
    mismatched_context_hash = deepcopy(examples["valid-planner-request-plan"])
    mismatched_context_hash["model_input"]["context_selection"]["context_sha256"] = H1
    invalid_ingress = deepcopy(example("IngressOutcome/v1"))
    invalid_ingress["episode_id"] = None
    invalid_attention = deepcopy(example("AttentionWake/v1"))
    invalid_attention["state"] = "claimed"
    invalid_memory_fact = deepcopy(example("MemoryFact/v1"))
    invalid_memory_fact["assertion_status"] = "superseded"
    invalid_model_decision = deepcopy(example("ModelDecision/v1"))
    invalid_model_decision["decision"] = "clarify"
    invalid_model_attempt = deepcopy(example("ModelAttempt/v1"))
    invalid_model_attempt["response_sha256"] = None
    invalid_presence_reply = deepcopy(example("PresenceReply/v1"))
    invalid_presence_reply["fallback_reason"] = "candidate_rejected"
    invalid_credential = deepcopy(example("CredentialGrant/v1"))
    invalid_credential["model_provider_profile_id"] = "model-profile:001"
    invalid_read_intent = deepcopy(example("ReadIntent/v1"))
    invalid_read_intent.update(
        purpose="effect_reconcile",
        effect_intent_id="effect:calendar:001",
        operation="effect.readback",
    )
    invalid_read_attempt = deepcopy(example("ReadAttempt/v1"))
    invalid_read_attempt["response_sha256"] = None
    invalid_effect_attempt = deepcopy(example("EffectAttempt/v1"))
    invalid_effect_attempt["dispatch_permit_sha256"] = H
    invalid_effect_receipt = deepcopy(example("EffectReceipt/v1"))
    invalid_effect_receipt["provider_resource_id"] = None
    invalid_connector_operation = deepcopy(example("ConnectorResult/v1"))
    invalid_connector_operation["operation"] = "calendar.event.read"
    invalid_reconcile_kind = deepcopy(examples["valid-connector-result-inline-null"])
    invalid_reconcile_kind["operation"] = "effect.reconcile"
    invalid_readback_kind = deepcopy(examples["valid-connector-result-inline-null"])
    invalid_readback_kind["operation"] = "effect.readback"
    invalid_probe_kind = deepcopy(examples["valid-connector-result-inline-null"])
    invalid_probe_kind["operation"] = "connector.probe"
    invalid_readback_operation = deepcopy(examples["valid-connector-result-readback"])
    invalid_readback_operation["operation"] = "calendar.event.read"
    invalid_probe_operation = deepcopy(examples["valid-connector-result-probe"])
    invalid_probe_operation["operation"] = "calendar.availability.read"
    completed_with_failed_step = deepcopy(example("OutcomeView/v1"))
    completed_with_failed_step["steps"].append(
        {
            "step_id": "step:failed:001",
            "status": "failed",
            "human_summary": "Niet gelukt",
            "evidence_refs": [],
            "unresolved_refs": ["failure:001"],
        }
    )
    partial_with_only_completed_steps = deepcopy(example("OutcomeView/v1"))
    partial_with_only_completed_steps["overall_status"] = "partially_completed"
    partial_with_only_incomplete_steps = deepcopy(example("OutcomeView/v1"))
    partial_with_only_incomplete_steps["overall_status"] = "partially_completed"
    partial_with_only_incomplete_steps["steps"] = [
        {
            "step_id": "step:failed:001",
            "status": "failed",
            "human_summary": "Niet gelukt",
            "evidence_refs": [],
            "unresolved_refs": ["failure:001"],
        }
    ]
    nested_invalid_outcome = deepcopy(examples["valid-planner-request-reply"])
    nested_invalid_outcome["model_input"]["outcome_view"]["steps"].append(
        {
            "step_id": "step:failed:001",
            "status": "failed",
            "human_summary": "Niet gelukt",
            "evidence_refs": [],
            "unresolved_refs": ["failure:001"],
        }
    )
    nested_invalid_connector = deepcopy(example("PresenceContext/v1"))
    nested_invalid_connector["connector_results"][0]["operation"] = "effect.readback"
    traversal_release_path = deepcopy(example("ReleaseManifest/v1"))
    traversal_release_path["artifacts"][0]["path"] = "records/../escape.jsonl"
    line_terminated_release_path = deepcopy(example("ReleaseManifest/v1"))
    line_terminated_release_path["artifacts"][0]["path"] = "records/escape.jsonl\n"
    invalid_object_store = deepcopy(example("ObjectStore/v1"))
    invalid_object_store["state"] = "active"
    invalid_evidence = deepcopy(example("EvidenceEnvelope/v1"))
    invalid_evidence["object_ref_id"] = "object:orphan:001"
    invalid_evidence_v2 = deepcopy(example("EvidenceEnvelope/v2"))
    invalid_evidence_v2["object_ref_id"] = "object:orphan:v2:001"
    evidence_v1_smuggles_authority = deepcopy(example("EvidenceEnvelope/v1"))
    evidence_v1_smuggles_authority["tenant_id"] = "tenant:smuggled:001"
    evidence_v2_missing_tenant = deepcopy(example("EvidenceEnvelope/v2"))
    del evidence_v2_missing_tenant["tenant_id"]
    evidence_v2_zero_sequence = deepcopy(example("EvidenceEnvelope/v2"))
    evidence_v2_zero_sequence["sequence"] = 0
    evidence_v2_genesis_with_predecessor = deepcopy(example("EvidenceEnvelope/v2"))
    evidence_v2_genesis_with_predecessor["predecessor_sha256"] = H
    evidence_v2_later_without_predecessor = deepcopy(example("EvidenceEnvelope/v2"))
    evidence_v2_later_without_predecessor["sequence"] = 2
    ack_v2_padded = deepcopy(example("DeliveryReceipt/v2"))
    ack_v2_padded["proof"]["client_ack"]["signature"] = SIGNATURE + "="
    ack_v2_wrong_algorithm = deepcopy(example("DeliveryReceipt/v2"))
    ack_v2_wrong_algorithm["proof"]["client_ack"]["algorithm"] = "p256-es256"
    ack_v2_wrong_content = deepcopy(example("DeliveryReceipt/v2"))
    ack_v2_wrong_content["proof"]["client_ack"]["presence_reply_sha256"] = H1
    ack_v2_downgraded_offer = deepcopy(example("DeliveryOffer/v2"))
    ack_v2_downgraded_offer["proof"] = {"mode": "provider"}
    ack_v1_smuggles_v2_format = deepcopy(example("DeliveryReceipt/v1"))
    ack_v1_smuggles_v2_format["proof"]["client_ack"]["signature_format"] = "P1363"
    initialize_wrong_id = deepcopy(example("ConnectorInitializeRequest/v1"))
    initialize_wrong_id["id"] = "connector-request:other:001"
    initialize_wrong_provider_scope = deepcopy(example("ConnectorInitializeRequest/v1"))
    initialize_wrong_provider_scope["params"]["credential_grant"]["provider_scopes"] = [
        "calendar.read"
    ]
    invoke_missing_id = deepcopy(example("ConnectorInvokeRequest/v1"))
    del invoke_missing_id["id"]
    invoke_null_id = deepcopy(example("ConnectorInvokeRequest/v1"))
    invoke_null_id["id"] = None
    rpc_error_wrong_code = deepcopy(example("ConnectorErrorResponse/v1"))
    rpc_error_wrong_code["error"]["code"] = -32603
    rpc_error_untyped_message = deepcopy(example("ConnectorErrorResponse/v1"))
    rpc_error_untyped_message["error"]["message"] = "provider secret detail"
    rpc_error_understates_io = deepcopy(example("ConnectorErrorResponse/v1"))
    rpc_error_understates_io["error"]["external_io_possible"] = True
    protocol_failure_understates_io = deepcopy(example("ConnectorProtocolFailure/v1"))
    protocol_failure_understates_io["failure_class"] = "internal_failure"
    invoke_success_wrong_id = deepcopy(example("ConnectorInvokeSuccess/v1"))
    invoke_success_wrong_id["id"] = "connector-request:other:001"
    invalid_installation = deepcopy(example("InstallationManifest/v1"))
    invalid_installation["support_status"] = "tier1"
    invalid_promotion = deepcopy(example("PromotionDecision/v1"))
    invalid_promotion["decision"] = "promote"
    noncanonical = json.dumps(turn, ensure_ascii=False, sort_keys=False, indent=2)

    def invalid(
        case_id: str,
        reason: str,
        layer: str,
        *,
        payload: dict[str, Any] | None = None,
        wire: str | None = None,
        wire_hex: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "caseId": case_id,
            "expect": "reject",
            "reason": reason,
            "validationLayer": layer,
            "encoding": "hex" if wire_hex is not None else "utf8",
        }
        if payload is not None:
            result["wireUtf8"] = canonical_json_bytes(payload).decode("utf-8")
        elif wire is not None:
            result["wireUtf8"] = wire
        elif wire_hex is not None:
            result["wireHex"] = wire_hex
        else:
            raise ValueError("invalid corpus vector requires payload or wire bytes")
        return result

    invalid_cases = [
        invalid(
            "invalid-duplicate-key",
            "duplicate_key",
            "wire_profile",
            wire='{"contract":"TurnEnvelope/v1","contract":"TurnEnvelope/v1"}',
        ),
        invalid(
            "invalid-unknown-major",
            "unknown_contract_major",
            "registry_dispatch",
            wire='{"contract":"TurnEnvelope/v2","schema_version":2}',
        ),
        invalid(
            "invalid-extra-security-field",
            "extra_field",
            "json_schema",
            payload=extra_security,
        ),
        invalid(
            "invalid-inline-payload-smuggled-object-handle",
            "ambiguous_payload_representation",
            "json_schema",
            payload=ambiguous_inline,
        ),
        invalid(
            "invalid-platform-fake-receipt",
            "unearned_platform_claim",
            "json_schema",
            payload=fake_platform_receipt,
        ),
        invalid(
            "invalid-identity-lifecycle-state",
            "identity_lifecycle_state",
            "json_schema",
            payload=invalid_identity_state,
        ),
        invalid(
            "invalid-presence-context-missing-current-utterance",
            "presence_context_incomplete",
            "json_schema",
            payload=missing_current_utterance,
        ),
        invalid(
            "invalid-purpose-model-input-contract",
            "purpose_model_input_mismatch",
            "json_schema",
            payload=wrong_model_input,
        ),
        invalid(
            "invalid-client-offer-missing-ack-key",
            "client_proof_incomplete",
            "json_schema",
            payload=missing_client_ack_key,
        ),
        invalid(
            "invalid-provider-proof-smuggles-client-ack",
            "proof_mode_mismatch",
            "json_schema",
            payload=provider_with_client_ack,
        ),
        invalid(
            "invalid-client-ack-binds-other-delivery",
            "client_ack_delivery_binding",
            "host_conformance",
            payload=mismatched_client_ack,
        ),
        invalid(
            "invalid-connector-request-hash",
            "canonical_payload_hash_binding",
            "host_conformance",
            payload=mismatched_connector_hash,
        ),
        invalid(
            "invalid-presence-identity-pack-hash",
            "identity_pack_hash_binding",
            "host_conformance",
            payload=mismatched_identity_hash,
        ),
        invalid(
            "invalid-model-input-context-hash",
            "presence_context_hash_binding",
            "host_conformance",
            payload=mismatched_context_hash,
        ),
        invalid(
            "invalid-ingress-admission-missing-episode",
            "ingress_state_fields",
            "json_schema",
            payload=invalid_ingress,
        ),
        invalid(
            "invalid-claimed-attention-without-lease",
            "attention_lease_fields",
            "json_schema",
            payload=invalid_attention,
        ),
        invalid(
            "invalid-superseded-memory-without-successor",
            "memory_status_fields",
            "json_schema",
            payload=invalid_memory_fact,
        ),
        invalid(
            "invalid-clarify-decision-with-actions",
            "model_decision_fields",
            "json_schema",
            payload=invalid_model_decision,
        ),
        invalid(
            "invalid-responded-model-attempt-without-response",
            "model_attempt_fields",
            "json_schema",
            payload=invalid_model_attempt,
        ),
        invalid(
            "invalid-presence-reply-candidate-and-fallback",
            "presence_reply_source",
            "json_schema",
            payload=invalid_presence_reply,
        ),
        invalid(
            "invalid-credential-grant-two-targets",
            "credential_target",
            "json_schema",
            payload=invalid_credential,
        ),
        invalid(
            "invalid-reconcile-read-operation",
            "read_intent_operation",
            "json_schema",
            payload=invalid_read_intent,
        ),
        invalid(
            "invalid-observed-read-without-response",
            "read_attempt_response",
            "json_schema",
            payload=invalid_read_attempt,
        ),
        invalid(
            "invalid-reserved-effect-with-permit",
            "effect_attempt_authority",
            "json_schema",
            payload=invalid_effect_attempt,
        ),
        invalid(
            "invalid-accepted-effect-without-resource",
            "effect_receipt_resource",
            "json_schema",
            payload=invalid_effect_receipt,
        ),
        invalid(
            "invalid-reconcile-result-operation",
            "connector_result_operation",
            "json_schema",
            payload=invalid_connector_operation,
        ),
        invalid(
            "invalid-reconcile-operation-with-read-outcome",
            "connector_result_recovery_kind",
            "json_schema",
            payload=invalid_reconcile_kind,
        ),
        invalid(
            "invalid-readback-operation-with-read-outcome",
            "connector_result_recovery_kind",
            "json_schema",
            payload=invalid_readback_kind,
        ),
        invalid(
            "invalid-probe-operation-with-read-outcome",
            "connector_result_recovery_kind",
            "json_schema",
            payload=invalid_probe_kind,
        ),
        invalid(
            "invalid-readback-outcome-with-business-operation",
            "connector_result_recovery_operation",
            "json_schema",
            payload=invalid_readback_operation,
        ),
        invalid(
            "invalid-probe-outcome-with-business-operation",
            "connector_result_recovery_operation",
            "json_schema",
            payload=invalid_probe_operation,
        ),
        invalid(
            "invalid-completed-outcome-with-failed-step",
            "outcome_aggregate_status",
            "json_schema",
            payload=completed_with_failed_step,
        ),
        invalid(
            "invalid-partial-outcome-with-only-completed-steps",
            "outcome_aggregate_status",
            "json_schema",
            payload=partial_with_only_completed_steps,
        ),
        invalid(
            "invalid-partial-outcome-with-only-incomplete-steps",
            "outcome_aggregate_status",
            "json_schema",
            payload=partial_with_only_incomplete_steps,
        ),
        invalid(
            "invalid-nested-planner-outcome-aggregate",
            "nested_outcome_aggregate_status",
            "json_schema",
            payload=nested_invalid_outcome,
        ),
        invalid(
            "invalid-nested-presence-connector-recovery-pair",
            "nested_connector_result_recovery_pair",
            "json_schema",
            payload=nested_invalid_connector,
        ),
        invalid(
            "invalid-relative-artifact-traversal",
            "relative_artifact_path",
            "json_schema",
            payload=traversal_release_path,
        ),
        invalid(
            "invalid-relative-artifact-line-terminator",
            "relative_artifact_path",
            "json_schema",
            payload=line_terminated_release_path,
        ),
        invalid(
            "invalid-active-object-store-without-integrity",
            "object_store_state_fields",
            "json_schema",
            payload=invalid_object_store,
        ),
        invalid(
            "invalid-one-sided-evidence-object-reference",
            "evidence_object_pair",
            "json_schema",
            payload=invalid_evidence,
        ),
        invalid(
            "invalid-one-sided-evidence-v2-object-reference",
            "evidence_v2_object_pair",
            "json_schema",
            payload=invalid_evidence_v2,
        ),
        invalid(
            "invalid-evidence-v1-smuggles-v2-authority",
            "evidence_v1_closed_shape",
            "json_schema",
            payload=evidence_v1_smuggles_authority,
        ),
        invalid(
            "invalid-evidence-v2-missing-tenant",
            "evidence_v2_tenant_required",
            "json_schema",
            payload=evidence_v2_missing_tenant,
        ),
        invalid(
            "invalid-evidence-v2-zero-sequence",
            "evidence_v2_sequence_positive",
            "json_schema",
            payload=evidence_v2_zero_sequence,
        ),
        invalid(
            "invalid-evidence-v2-genesis-with-predecessor",
            "evidence_v2_genesis_chain",
            "json_schema",
            payload=evidence_v2_genesis_with_predecessor,
        ),
        invalid(
            "invalid-evidence-v2-later-without-predecessor",
            "evidence_v2_predecessor_required",
            "json_schema",
            payload=evidence_v2_later_without_predecessor,
        ),
        invalid(
            "invalid-client-ack-v2-padded-signature",
            "client_ack_v2_unpadded_base64url",
            "json_schema",
            payload=ack_v2_padded,
        ),
        invalid(
            "invalid-client-ack-v2-wrong-algorithm",
            "client_ack_v2_algorithm_profile",
            "json_schema",
            payload=ack_v2_wrong_algorithm,
        ),
        invalid(
            "invalid-client-ack-v2-other-presence-reply",
            "client_ack_v2_presence_reply_binding",
            "host_conformance",
            payload=ack_v2_wrong_content,
        ),
        invalid(
            "invalid-client-ack-v2-provider-downgrade",
            "client_ack_v2_client_mode_required",
            "json_schema",
            payload=ack_v2_downgraded_offer,
        ),
        invalid(
            "invalid-client-ack-v1-smuggles-v2-format",
            "delivery_receipt_v1_closed_shape",
            "json_schema",
            payload=ack_v1_smuggles_v2_format,
        ),
        invalid(
            "invalid-connector-initialize-id-binding",
            "connector_rpc_initialize_id_binding",
            "host_conformance",
            payload=initialize_wrong_id,
        ),
        invalid(
            "invalid-connector-initialize-provider-scope",
            "connector_rpc_provider_scope_binding",
            "host_conformance",
            payload=initialize_wrong_provider_scope,
        ),
        invalid(
            "invalid-connector-invoke-notification",
            "connector_rpc_id_required",
            "json_schema",
            payload=invoke_missing_id,
        ),
        invalid(
            "invalid-connector-invoke-null-id",
            "connector_rpc_string_id_required",
            "json_schema",
            payload=invoke_null_id,
        ),
        invalid(
            "invalid-connector-error-code-kind",
            "connector_rpc_error_taxonomy",
            "json_schema",
            payload=rpc_error_wrong_code,
        ),
        invalid(
            "invalid-connector-error-untyped-message",
            "connector_rpc_error_safe_message",
            "json_schema",
            payload=rpc_error_untyped_message,
        ),
        invalid(
            "invalid-connector-error-understates-io",
            "connector_rpc_error_ambiguity",
            "json_schema",
            payload=rpc_error_understates_io,
        ),
        invalid(
            "invalid-connector-protocol-failure-understates-io",
            "connector_protocol_failure_ambiguity",
            "json_schema",
            payload=protocol_failure_understates_io,
        ),
        invalid(
            "invalid-connector-invoke-success-id-binding",
            "connector_rpc_success_id_binding",
            "host_conformance",
            payload=invoke_success_wrong_id,
        ),
        invalid(
            "invalid-installation-inflates-support",
            "installation_support",
            "json_schema",
            payload=invalid_installation,
        ),
        invalid(
            "invalid-promotion-without-canary",
            "promotion_canary_evidence",
            "json_schema",
            payload=invalid_promotion,
        ),
        invalid(
            "invalid-document-byte-budget",
            "document_utf8_bytes_exceed_65536",
            "host_conformance",
            payload=oversized_document,
        ),
        invalid(
            "invalid-surrogate",
            "invalid_unicode",
            "wire_profile",
            wire='{"contract":"IdentityPack/v1","display_name":"\\ud800"}',
        ),
        invalid(
            "invalid-not-nfc",
            "invalid_unicode",
            "wire_profile",
            wire='{"contract":"IdentityPack/v1","display_name":"e\\u0301"}',
        ),
        invalid("invalid-utf8", "invalid_utf8", "wire_profile", wire_hex="ff"),
        invalid("invalid-bom", "bom_forbidden", "wire_profile", wire_hex="efbbbf7b7d"),
        invalid(
            "invalid-float",
            "floating_point_forbidden",
            "wire_profile",
            wire='{"contract":"TurnEnvelope/v1","schema_version":1,"value":1.5}',
        ),
        invalid(
            "invalid-unsafe-integer",
            "integer_outside_i_json_exact_range",
            "wire_profile",
            wire='{"contract":"TurnEnvelope/v1","schema_version":1,"value":9007199254740992}',
        ),
        invalid(
            "invalid-noncanonical-whitespace-and-order",
            "not_canonical",
            "wire_profile",
            wire=noncanonical,
        ),
        invalid(
            "invalid-noncanonical-timestamp",
            "timestamp_not_utc_z_seconds",
            "json_schema",
            payload=noncanonical_timestamp,
        ),
        {
            "caseId": "invalid-oversize",
            "expect": "reject",
            "reason": "payload_too_large",
            "validationLayer": "wire_profile",
            "encoding": "repeat_utf8",
            "wireRecipe": {
                "prefix": '{"padding":"',
                "repeatText": "a",
                "repeatCount": MAX_CONTRACT_BYTES,
                "suffix": '"}',
            },
        },
    ]

    def rpc_frame(payload: dict[str, Any]) -> bytes:
        body = canonical_json_bytes(payload)
        return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    initialize_request_frame = rpc_frame(example("ConnectorInitializeRequest/v1"))
    invoke_request_frame = rpc_frame(example("ConnectorInvokeRequest/v1"))
    initialize_success_frame = rpc_frame(example("ConnectorInitializeSuccess/v1"))
    invoke_success_frame = rpc_frame(example("ConnectorInvokeSuccess/v1"))
    invoke_error = example("ConnectorErrorResponse/v1")
    invoke_error_frame = rpc_frame(invoke_error)
    initialize_error = deepcopy(invoke_error)
    initialize_request = example("ConnectorInitializeRequest/v1")
    initialize_error.update(
        id=initialize_request["id"],
        request_contract="ConnectorInitializeRequest/v1",
        request_method="connector.initialize",
        request_sha256=hashlib.sha256(
            canonical_json_bytes(initialize_request)
        ).hexdigest(),
    )
    initialize_error_frame = rpc_frame(initialize_error)
    valid_body = canonical_json_bytes(example("ConnectorInvokeRequest/v1"))
    duplicate_id_body = (
        b'{"contract":"ConnectorInvokeRequest/v1","id":"request:001",'
        b'"id":"request:002","jsonrpc":"2.0","method":"resource.read",'
        b'"schema_version":1}'
    )
    connector_frame_cases = [
        {
            "caseId": "valid-connector-initialize-request-frame",
            "expect": "accept",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": initialize_request_frame.hex(),
        },
        {
            "caseId": "valid-connector-invoke-request-frame",
            "expect": "accept",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": invoke_request_frame.hex(),
        },
        {
            "caseId": "valid-connector-success-stdout-exchange",
            "expect": "accept",
            "profile": "stdout_exchange",
            "encoding": "hex",
            "wireHex": (initialize_success_frame + invoke_success_frame).hex(),
        },
        {
            "caseId": "valid-connector-init-error-stdout-exchange",
            "expect": "accept",
            "profile": "stdout_exchange",
            "encoding": "hex",
            "wireHex": initialize_error_frame.hex(),
        },
        {
            "caseId": "invalid-connector-frame-lowercase-header",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": (
                f"content-length: {len(valid_body)}\r\n\r\n".encode("ascii")
                + valid_body
            ).hex(),
        },
        {
            "caseId": "invalid-connector-frame-leading-zero",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": (
                f"Content-Length: 0{len(valid_body)}\r\n\r\n".encode("ascii")
                + valid_body
            ).hex(),
        },
        {
            "caseId": "invalid-connector-frame-lf-only",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": (
                f"Content-Length: {len(valid_body)}\n\n".encode("ascii") + valid_body
            ).hex(),
        },
        {
            "caseId": "invalid-connector-frame-extra-header",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": (
                f"Content-Length: {len(valid_body)}\r\nX-Eva: 1\r\n\r\n".encode("ascii")
                + valid_body
            ).hex(),
        },
        {
            "caseId": "invalid-connector-frame-truncated-body",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": (
                f"Content-Length: {len(valid_body) + 1}\r\n\r\n".encode("ascii")
                + valid_body
            ).hex(),
        },
        {
            "caseId": "invalid-connector-frame-oversize-declaration",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": b"Content-Length: 1048577\r\n\r\n".hex(),
        },
        {
            "caseId": "invalid-connector-frame-duplicate-json-rpc-id",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": (
                f"Content-Length: {len(duplicate_id_body)}\r\n\r\n".encode("ascii")
                + duplicate_id_body
            ).hex(),
        },
        {
            "caseId": "invalid-connector-single-frame-trailing-log",
            "expect": "reject",
            "profile": "single_frame",
            "encoding": "hex",
            "wireHex": (invoke_request_frame + b"worker log").hex(),
        },
        {
            "caseId": "invalid-connector-stdout-starts-with-request",
            "expect": "reject",
            "profile": "stdout_exchange",
            "encoding": "hex",
            "wireHex": (initialize_request_frame + invoke_success_frame).hex(),
        },
        {
            "caseId": "invalid-connector-stdout-two-init-responses",
            "expect": "reject",
            "profile": "stdout_exchange",
            "encoding": "hex",
            "wireHex": (initialize_success_frame + initialize_success_frame).hex(),
        },
        {
            "caseId": "invalid-connector-stdout-missing-operation-response",
            "expect": "reject",
            "profile": "stdout_exchange",
            "encoding": "hex",
            "wireHex": initialize_success_frame.hex(),
        },
        {
            "caseId": "invalid-connector-stdout-invoke-error-first",
            "expect": "reject",
            "profile": "stdout_exchange",
            "encoding": "hex",
            "wireHex": invoke_error_frame.hex(),
        },
        {
            "caseId": "invalid-connector-stdout-third-frame",
            "expect": "reject",
            "profile": "stdout_exchange",
            "encoding": "hex",
            "wireHex": (
                initialize_success_frame + invoke_success_frame + invoke_error_frame
            ).hex(),
        },
    ]
    return {
        "corpus": "EvaContractCorpus/v1",
        "authority": "language_neutral_validation_fixture_only",
        "productClaim": False,
        "canonicalProfile": CANONICAL_JSON_PROFILE,
        "maximumPayloadBytes": MAX_CONTRACT_BYTES,
        "validationLayers": {
            "json_schema": "Draft 2020-12 rejects the decoded payload",
            "host_conformance": "semantic equality, chronology, aggregate or canonical-hash rule checked by the host/conformance reader",
            "registry_dispatch": "closed contract/version registry rejects before schema selection",
            "wire_profile": "canonical UTF-8 JSON reader rejects before or around schema validation",
        },
        "primitiveHostConformanceCases": [
            {
                "caseId": "invalid-timestamp-gregorian-date",
                "primitive": "Timestamp",
                "value": "2026-02-30T12:00:00Z",
                "expect": "reject",
                "validationLayer": "host_conformance",
                "reason": "invalid_gregorian_date",
            },
            {
                "caseId": "invalid-calendar-date-gregorian-date",
                "primitive": "CalendarDate",
                "value": "2026-02-30",
                "expect": "reject",
                "validationLayer": "host_conformance",
                "reason": "invalid_gregorian_date",
            },
        ],
        "wireRecipeSemantics": "UTF-8 encode prefix + repeatText repeated repeatCount times + suffix",
        "connectorRpcFramingProfile": {
            "protocol": "JSON-RPC 2.0",
            "encoding": "UTF-8 EvaCanonicalJSON/v1",
            "header": "Content-Length: <canonical decimal>\\r\\n\\r\\n",
            "maximumFrameBodyBytes": 1_048_576,
            "maximumShortWorkerStdoutBytes": 2_101_248,
            "stdoutSequences": [
                "initialize-success then invoke-success-or-error",
                "initialize-error only",
            ],
        },
        "connectorFrameCases": connector_frame_cases,
        "cases": valid_cases + invalid_cases,
    }


def _apply_schema_cross_field_rules(
    contract_name: str,
    schema: dict[str, Any],
    *,
    shared_definitions: dict[str, Any] | None = None,
) -> None:
    """Add only cross-field rules that Draft 2020-12 can express exactly."""

    definitions = (
        shared_definitions
        if shared_definitions is not None
        else schema.get("$defs", {})
    )

    def add_rules(target: dict[str, Any], *rules: dict[str, Any]) -> None:
        target.setdefault("allOf", []).extend(rules)

    def when(
        field: str,
        value: str | int | list[str],
        then: dict[str, Any],
        otherwise: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        condition = {"enum": value} if isinstance(value, list) else {"const": value}
        rule: dict[str, Any] = {
            "if": {"required": [field], "properties": {field: condition}},
            "then": then,
        }
        if otherwise is not None:
            rule["else"] = otherwise
        return rule

    def fields(**values: dict[str, Any]) -> dict[str, Any]:
        return {"properties": values}

    null = {"type": "null"}
    nonnull = {"not": {"type": "null"}}

    if contract_name == "IngressOutcome/v1":
        accepted = fields(
            episode_id=nonnull,
            generation=nonnull,
            pending_attention_id=nonnull,
            rejection_code=null,
        )
        unaccepted = fields(
            episode_id=null,
            generation=null,
            pending_attention_id=null,
        )
        add_rules(
            schema,
            when("status", ["admitted", "duplicate"], accepted, unaccepted),
            when(
                "status",
                "conflict",
                fields(rejection_code={"const": "request_hash_conflict"}),
            ),
            when(
                "status",
                "rejected",
                fields(
                    rejection_code={
                        "enum": [
                            "authentication_failed",
                            "authorization_failed",
                            "payload_invalid",
                            "payload_too_large",
                            "unsupported_contract",
                        ]
                    }
                ),
            ),
        )
    elif contract_name == "AttentionWake/v1":
        add_rules(
            schema,
            when(
                "state",
                "claimed",
                fields(lease_owner=nonnull, lease_expires_at=nonnull),
                fields(lease_owner=null, lease_expires_at=null),
            ),
        )
    elif contract_name == "MemoryFact/v1":
        add_rules(
            schema,
            when(
                "assertion_status",
                "superseded",
                fields(superseded_by_fact_id=nonnull),
                fields(superseded_by_fact_id=null),
            ),
            when(
                "assertion_status",
                "retracted",
                fields(retraction_source_event_id=nonnull),
                fields(retraction_source_event_id=null),
            ),
        )
    elif contract_name == "MemoryRecall/v1":
        schema.setdefault("allOf", []).append(
            {
                "anyOf": [
                    fields(conversation_id=nonnull),
                    fields(project_id=nonnull),
                ]
            }
        )
    elif contract_name == "ModelDecision/v1":
        no_actions = {"maxItems": 0}
        actions = {"minItems": 1}
        add_rules(
            schema,
            when(
                "decision",
                "clarify",
                fields(
                    actions=no_actions, clarification_question=nonnull, wait_reason=null
                ),
            ),
            when(
                "decision",
                "wait",
                fields(
                    actions=no_actions, clarification_question=null, wait_reason=nonnull
                ),
            ),
            when(
                "decision",
                "request_read",
                fields(
                    actions={
                        **actions,
                        "items": {"properties": {"kind": {"const": "request_read"}}},
                    },
                    clarification_question=null,
                    wait_reason=null,
                ),
            ),
            when(
                "decision",
                "propose_effect",
                fields(
                    actions={
                        **actions,
                        "items": {"properties": {"kind": {"const": "propose_effect"}}},
                    },
                    clarification_question=null,
                    wait_reason=null,
                ),
            ),
        )
    elif contract_name == "ModelAttempt/v1":
        no_usage = fields(
            input_tokens_used=null,
            output_tokens_used=null,
            provider_cost_euro_micros=null,
        )
        add_rules(
            schema,
            when("ordinal", 2, fields(purpose={"const": "plan"})),
            when(
                "state",
                ["reserved", "budget_exhausted"],
                fields(credential_grant_id=null),
                fields(credential_grant_id=nonnull),
            ),
            when(
                "state",
                ["responded", "provider_rejected", "rate_limited"],
                fields(response_sha256=nonnull),
                fields(response_sha256=null),
            ),
            when(
                "state",
                ["reserved", "dispatch_authorized", "budget_exhausted"],
                no_usage,
            ),
        )
    elif contract_name == "OutcomeView/v1":
        step = definitions.get("StepOutcome")
        if isinstance(step, dict):
            add_rules(
                step,
                when("status", "completed", fields(evidence_refs={"minItems": 1})),
                when(
                    "status",
                    ["ambiguous", "blocked", "failed"],
                    fields(unresolved_refs={"minItems": 1}),
                ),
            )
        completed_step = {
            "required": ["status"],
            "properties": {"status": {"const": "completed"}},
        }
        incomplete_step = {
            "required": ["status"],
            "properties": {
                "status": {"enum": ["partial", "ambiguous", "blocked", "failed"]}
            },
        }
        add_rules(
            schema,
            when(
                "overall_status",
                "completed",
                fields(steps={"items": completed_step}),
            ),
            when(
                "overall_status",
                "partially_completed",
                fields(
                    steps={
                        "allOf": [
                            {"contains": completed_step, "minContains": 1},
                            {"contains": incomplete_step, "minContains": 1},
                        ]
                    }
                ),
            ),
        )
    elif contract_name == "PresenceReply/v1":
        schema["properties"]["text"]["pattern"] = "^[^\u2013\u2014]*$"
        add_rules(
            schema,
            {
                "oneOf": [
                    fields(reply_candidate_sha256=nonnull, fallback_reason=null),
                    fields(reply_candidate_sha256=null, fallback_reason=nonnull),
                ]
            },
        )
    elif contract_name == "CredentialGrant/v1":
        add_rules(
            schema,
            {
                "oneOf": [
                    fields(
                        connector_binding_id=nonnull, model_provider_profile_id=null
                    ),
                    fields(
                        connector_binding_id=null, model_provider_profile_id=nonnull
                    ),
                ]
            },
        )
    elif contract_name == "ReadIntent/v1":
        add_rules(
            schema,
            when(
                "purpose",
                ["effect_reconcile", "effect_verify"],
                fields(effect_intent_id=nonnull),
                fields(effect_intent_id=null),
            ),
            when(
                "purpose",
                "effect_reconcile",
                fields(operation={"const": "effect.reconcile"}),
            ),
            when(
                "purpose",
                "effect_verify",
                fields(operation={"const": "effect.readback"}),
            ),
            when(
                "purpose",
                "breaker_probe",
                fields(operation={"const": "connector.probe"}),
            ),
        )
    elif contract_name == "ReadAttempt/v1":
        add_rules(
            schema,
            when(
                "state",
                ["observed", "rejected", "rate_limited"],
                fields(response_sha256=nonnull),
                fields(response_sha256=null),
            ),
        )
    elif contract_name == "EffectAttempt/v1":
        externally_dispatched = [
            "dispatch_authorized",
            "provider_accepted",
            "definitive_reject",
            "ambiguous_transport",
            "rate_limited",
        ]
        response_states = ["provider_accepted", "definitive_reject", "rate_limited"]
        add_rules(
            schema,
            when(
                "state",
                externally_dispatched,
                fields(dispatch_permit_sha256=nonnull),
                fields(dispatch_permit_sha256=null),
            ),
            when(
                "state",
                response_states,
                fields(response_sha256=nonnull),
                fields(response_sha256=null),
            ),
        )
        for state in (
            "reserved",
            "dispatch_authorized",
            "provider_accepted",
            "definitive_reject",
            "ambiguous_transport",
            "rate_limited",
            "policy_denied",
            "hash_conflict",
            "internal_failure",
        ):
            error_class = (
                "none"
                if state in {"reserved", "dispatch_authorized", "provider_accepted"}
                else state
            )
            add_rules(
                schema, when("state", state, fields(error_class={"const": error_class}))
            )
    elif contract_name == "EffectReceipt/v1":
        add_rules(
            schema,
            when(
                "decision",
                "accepted",
                fields(provider_resource_id=nonnull),
                fields(provider_resource_id=null),
            ),
        )
    elif contract_name == "ConnectorResult/v1":
        reconcile = definitions.get("ReconcileResult")
        if isinstance(reconcile, dict):
            add_rules(
                reconcile,
                when("result", "found_same", fields(provider_resource_id=nonnull)),
                when("result", "not_observed", fields(provider_resource_id=null)),
            )
        readback = definitions.get("ReadbackResult")
        if isinstance(readback, dict):
            add_rules(
                readback,
                when("match_status", "matches", fields(payload=nonnull)),
                when("match_status", "not_observed", fields(payload=null)),
            )
        probe = definitions.get("ProbeResult")
        if isinstance(probe, dict):
            add_rules(
                probe,
                when(
                    "result",
                    "rate_limited",
                    fields(retry_after_ms=nonnull),
                    fields(retry_after_ms=null),
                ),
            )
        outcome_kinds = {
            "reconcile_result": "effect.reconcile",
            "readback": "effect.readback",
            "probe_result": "connector.probe",
        }
        for kind, operation in outcome_kinds.items():
            add_rules(
                schema,
                {
                    "if": {
                        "properties": {
                            "outcome": {"properties": {"kind": {"const": kind}}}
                        }
                    },
                    "then": fields(operation={"const": operation}),
                },
                when(
                    "operation",
                    operation,
                    fields(
                        outcome={
                            "required": ["kind"],
                            "properties": {"kind": {"const": kind}},
                        }
                    ),
                ),
            )
    elif contract_name == "ObjectStore/v1":
        add_rules(
            schema,
            when(
                "state",
                "design_only_not_runnable",
                fields(integrity_root_sha256=null, verified_at=null),
            ),
            when(
                "state",
                ["active", "read_only"],
                fields(integrity_root_sha256=nonnull, verified_at=nonnull),
            ),
        )
    elif contract_name in {"EvidenceEnvelope/v1", "EvidenceEnvelope/v2"}:
        add_rules(
            schema,
            {
                "oneOf": [
                    fields(object_ref_id=null, object_sha256=null),
                    fields(object_ref_id=nonnull, object_sha256=nonnull),
                ]
            },
        )
        if contract_name == "EvidenceEnvelope/v2":
            add_rules(
                schema,
                when(
                    "sequence",
                    1,
                    fields(predecessor_sha256=null),
                    fields(predecessor_sha256=nonnull),
                ),
            )
    elif contract_name == "ConnectorErrorResponse/v1":
        error = definitions.get("ConnectorRpcError")
        if isinstance(error, dict):
            for kind, (code, message) in {
                "method_not_found": (-32601, "Method not found"),
                "invalid_params": (-32602, "Invalid params"),
                "internal_error": (-32603, "Internal error"),
                "worker_failure": (-32000, "Worker failure"),
            }.items():
                add_rules(
                    error,
                    when(
                        "kind",
                        kind,
                        fields(code={"const": code}, message={"const": message}),
                    ),
                )
                if kind in {"method_not_found", "invalid_params"}:
                    add_rules(
                        error,
                        when(
                            "kind",
                            kind,
                            fields(
                                external_io_possible={"const": False},
                                failure_class={"const": "internal_failure"},
                            ),
                        ),
                    )
            add_rules(
                error,
                when(
                    "external_io_possible",
                    True,
                    fields(failure_class={"const": "ambiguous_transport"}),
                    fields(failure_class={"const": "internal_failure"}),
                ),
            )
        add_rules(
            schema,
            when(
                "request_contract",
                "ConnectorInitializeRequest/v1",
                fields(
                    request_method={"const": "connector.initialize"},
                    error={
                        "properties": {
                            "external_io_possible": {"const": False},
                            "failure_class": {"const": "internal_failure"},
                        }
                    },
                ),
                fields(
                    request_method={
                        "enum": [
                            "resource.read",
                            "effect.execute",
                            "effect.reconcile",
                            "effect.readback",
                            "connector.probe",
                        ]
                    }
                ),
            ),
        )
    elif contract_name == "ConnectorProtocolFailure/v1":
        add_rules(
            schema,
            when(
                "phase",
                "invoke_external_io_possible",
                fields(failure_class={"const": "ambiguous_transport"}),
                fields(failure_class={"const": "internal_failure"}),
            ),
        )

    if contract_name not in {
        "PlatformLock/v1",
        "InstallationManifest/v1",
        "PromotionDecision/v1",
    }:
        return

    if contract_name == "InstallationManifest/v1":
        add_rules(
            schema,
            when(
                "support_status",
                "tier1",
                fields(target={"properties": {"support_status": {"const": "tier1"}}}),
            ),
        )
        return
    if contract_name == "PromotionDecision/v1":
        add_rules(
            schema,
            when("decision", "promote", fields(canary_evidence_sha256=nonnull)),
        )
        return

    platform_target = definitions.get("PlatformTarget")
    if isinstance(platform_target, dict):
        add_rules(
            platform_target,
            when(
                "architecture",
                "amd64_or_arm64",
                fields(execution_mode={"const": "rootless_podman"}),
                {"not": fields(execution_mode={"const": "rootless_podman"})},
            ),
        )
    oci_image = definitions.get("OciImageLock")
    if isinstance(oci_image, dict):
        add_rules(
            oci_image,
            when(
                "validation_state",
                "selected_for_validation",
                fields(
                    image_repository=null,
                    image_digest=null,
                    sbom_sha256=null,
                    runnable={"const": False},
                ),
                fields(
                    image_repository=nonnull,
                    image_digest=nonnull,
                    sbom_sha256=nonnull,
                    runnable={"const": True},
                ),
            ),
        )

    selected_target = {
        "properties": {
            "validation_state": {"const": "unvalidated"},
            "support_status": {"const": "unsupported"},
            "validation_receipt_sha256": {"type": "null"},
        }
    }
    selected_image = {
        "properties": {
            "validation_state": {"const": "selected_for_validation"},
            "image_repository": {"type": "null"},
            "image_digest": {"type": "null"},
            "sbom_sha256": {"type": "null"},
            "runnable": {"const": False},
        }
    }
    validated_tier1 = {
        "properties": {
            "validation_state": {"const": "validated"},
            "support_status": {"const": "tier1"},
            "validation_receipt_sha256": {"type": "string"},
        }
    }
    validated_image = {
        "properties": {
            "validation_state": {"const": "validated"},
            "image_repository": {"type": "string"},
            "image_digest": {"type": "string"},
            "sbom_sha256": {"type": "string"},
            "runnable": {"const": True},
        }
    }
    schema["allOf"] = [
        {
            "if": {"properties": {"claim_state": {"const": "selected_for_validation"}}},
            "then": {
                "properties": {
                    "tier1_targets": {"items": selected_target},
                    "compatibility_targets": {"items": selected_target},
                    "oci_images": {"items": selected_image},
                }
            },
        },
        {
            "if": {"properties": {"claim_state": {"const": "validated"}}},
            "then": {
                "properties": {
                    "tier1_targets": {"items": validated_tier1},
                    "compatibility_targets": {
                        "items": {
                            "properties": {"support_status": {"const": "unsupported"}}
                        }
                    },
                    "oci_images": {"items": validated_image},
                }
            },
        },
    ]


HOST_CONFORMANCE_RULES = {
    "AttentionWake/v1": ["deadline ordering and lease-time validity"],
    "CapabilityGrant/v1": ["deadline follows issuance"],
    "ConnectorEnvelope/v1": ["request hash equals canonical request payload bytes"],
    "ConnectorErrorResponse/v1": [
        "ID, request contract, method and canonical request hash bind one phase; failure class matches the supervisor external-I/O phase"
    ],
    "ConnectorInitializeRequest/v1": [
        "ID, connector binding, capability manifest, manifest, worker executable, CapabilityGrant, exact operation provider scopes, CredentialGrant and deadline bind exactly"
    ],
    "ConnectorInitializeSuccess/v1": [
        "ID and every connector-binding, capability-manifest, grant and executable authority hash bind the selected request"
    ],
    "ConnectorInvokeRequest/v1": [
        "phase-unique ID, verified connector binding, manifest operation effect class, exact provider scopes and both grants bind the exact ConnectorEnvelope"
    ],
    "ConnectorInvokeSuccess/v1": [
        "ID and ConnectorResult.request_sha256 bind canonical ConnectorEnvelope bytes; result deadline, outcome kind and payload output schema match the selected manifest operation"
    ],
    "ConnectorManifest/v1": [
        "operation IDs are unique and stdout carries all maximum frames; the short-worker handshake requires the exact sealed frame/count/stdout budgets"
    ],
    "ConnectorProtocolFailure/v1": [
        "supervisor phase maps every fatal protocol violation to internal_failure before possible external I/O or ambiguous_transport after it, with no automatic resend"
    ],
    "ConsentGrant/v1": ["expiry follows grant time"],
    "CredentialGrant/v1": ["deadline follows issuance"],
    "DeliveryOffer/v1": ["expiry follows offer time"],
    "DeliveryReceipt/v1": [
        "selected DeliveryOffer hash, delivery/content/wire, proof mode and client ACK request/challenge/algorithm/key match exactly"
    ],
    "DeliveryOffer/v2": [
        "expiry follows offer time; installation, channel, request, conversation and ACK key equal the authenticated IngressRequest/v1"
    ],
    "DeliveryReceipt/v2": [
        "authenticated ingress, v2 offer, delivery, PresenceReply, wire, P-256 profile, signature, pre-bound key and transaction-bound one-time challenge claim match exactly; v1 is never a fallback"
    ],
    "DocumentDraft/v1": ["content is at most 65536 UTF-8 bytes"],
    "EpisodeState/v1": ["generation supersedes exactly its arithmetic predecessor"],
    "EvaPortableArchive/v1": ["encrypted object count equals the sum of chunk counts"],
    "EvidenceEnvelope/v2": [
        "object reference pair is complete; tenant, aggregate, positive sequence and idempotency key are required"
    ],
    "MemoryRecall/v1": ["selected ranks are unique"],
    "PlannerRequest/v1": [
        "context selection hash equals the complete embedded PresenceContext"
    ],
    "PlatformLock/v1": ["target set equals the sealed platform matrix"],
    "PresenceContext/v1": [
        "embedded IdentityPack hash and relationship/consent scope equal the enclosing context"
    ],
    "ValidatedPlan/v1": [
        "step IDs are unique and dependencies form a topologically ordered DAG"
    ],
}

PRIMITIVE_HOST_CONFORMANCE_RULES = {
    "CalendarDate": ["lexical components form a valid proleptic Gregorian date"],
    "Timestamp": [
        "UTC second-precision lexical components form a valid proleptic Gregorian date and clock time"
    ],
}


def _apply_all_schema_cross_field_rules(
    contract_name: str, schema: dict[str, Any]
) -> None:
    """Apply the same rules to top-level and embedded versioned contracts."""

    definitions = schema.get("$defs", {})
    if not isinstance(definitions, dict):
        raise TypeError("generated $defs must be an object")
    _apply_schema_cross_field_rules(
        contract_name, schema, shared_definitions=definitions
    )
    for definition in definitions.values():
        if not isinstance(definition, dict):
            continue
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            continue
        contract_property = properties.get("contract")
        if not isinstance(contract_property, dict):
            continue
        nested_contract = contract_property.get("const")
        if (
            not isinstance(nested_contract, str)
            or nested_contract not in CONTRACT_REGISTRY
        ):
            continue
        _apply_schema_cross_field_rules(
            nested_contract, definition, shared_definitions=definitions
        )


def generated_files() -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    schema_digests: dict[str, str] = {}
    for contract_name, model in CONTRACT_REGISTRY.items():
        schema = model.model_json_schema(mode="validation")
        _apply_all_schema_cross_field_rules(contract_name, schema)
        schema["$id"] = (
            f"https://schemas.eva.local/contracts/{schema_filename(contract_name)}"
        )
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        encoded = _json_bytes(schema) + b"\n"
        filename = schema_filename(contract_name)
        result[filename] = encoded
        schema_digests[contract_name] = hashlib.sha256(encoded).hexdigest()

    profile = {
        "profile": CANONICAL_JSON_PROFILE,
        "encoding": "UTF-8 without BOM",
        "topLevel": "object",
        "objectKeys": "unique, NFC, lexicographically sorted by Unicode code point",
        "strings": "NFC; no NUL, surrogate or Unicode noncharacter",
        "numbers": "signed JSON integers only; no floats, NaN or infinities",
        "minimumInteger": -JSON_SAFE_INTEGER_MAX,
        "maximumInteger": JSON_SAFE_INTEGER_MAX,
        "whitespace": "none outside JSON strings",
        "maximumPayloadBytes": MAX_CONTRACT_BYTES,
        "maximumNestingDepth": 64,
        "maximumCollectionItems": 1024,
    }
    result["canonical-json-profile-v1.json"] = _json_bytes(profile) + b"\n"
    corpus = build_corpus(valid_examples())
    result["contract-corpus-v1.json"] = _json_bytes(corpus) + b"\n"
    manifest = {
        "manifest": "EvaContractSchemaManifest/v1",
        "canonicalProfile": CANONICAL_JSON_PROFILE,
        "contractCount": len(CONTRACT_REGISTRY),
        "schemas": schema_digests,
        "hostConformanceRules": HOST_CONFORMANCE_RULES,
        "primitiveHostConformanceRules": PRIMITIVE_HOST_CONFORMANCE_RULES,
        "corpusSha256": hashlib.sha256(result["contract-corpus-v1.json"]).hexdigest(),
        "profileSha256": hashlib.sha256(
            result["canonical-json-profile-v1.json"]
        ).hexdigest(),
    }
    result["manifest.json"] = _json_bytes(manifest) + b"\n"
    return result


def _check(files: dict[str, bytes]) -> int:
    expected_names = set(files)
    existing_names = (
        {path.name for path in SCHEMA_ROOT.glob("*.json")}
        if SCHEMA_ROOT.exists()
        else set()
    )
    failures: list[str] = []
    for name, expected in files.items():
        path = SCHEMA_ROOT / name
        if not path.is_file():
            failures.append(f"missing {path.relative_to(EVA2_ROOT)}")
        elif path.read_bytes() != expected:
            failures.append(f"stale {path.relative_to(EVA2_ROOT)}")
    for unexpected in sorted(existing_names - expected_names):
        failures.append(f"unexpected schemas/contracts/{unexpected}")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(
        f"CONTRACT-01 schemas current: {len(CONTRACT_REGISTRY)} contracts, {len(files)} generated files"
    )
    return 0


def _write(files: dict[str, bytes]) -> int:
    SCHEMA_ROOT.mkdir(parents=True, exist_ok=True)
    for old in SCHEMA_ROOT.glob("*.json"):
        if old.name not in files:
            old.unlink()
    for name, content in files.items():
        (SCHEMA_ROOT / name).write_bytes(content)
    print(
        f"generated {len(files)} files for {len(CONTRACT_REGISTRY)} contracts in {SCHEMA_ROOT}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail if generated files differ"
    )
    arguments = parser.parse_args()
    files = generated_files()
    return _check(files) if arguments.check else _write(files)


if __name__ == "__main__":
    raise SystemExit(main())
