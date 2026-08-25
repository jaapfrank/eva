from __future__ import annotations

from dataclasses import dataclass

import pytest

from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationObservationBinding,
    ConversationTurn,
    GroundedReply,
)
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)


@dataclass
class _ColdPath:
    documents: list[object]

    def submit(self, document: object) -> None:
        self.documents.append(document)


class _Journal:
    def load(self, _turn: ConversationTurn) -> object:
        return object()

    def memory_queued(
        self,
        _turn: ConversationTurn,
        _state: object,
        *,
        operation_document_id: str,
    ) -> None:
        assert operation_document_id == "conversation:1:exchange:message:1"


def test_semantic_retention_never_promotes_model_output_to_direct_user() -> None:
    turn = ConversationTurn.private(
        tenant_id="tenant:1",
        installation_id="installation:1",
        principal_id="person:1",
        conversation_id="conversation:1",
        message_id="message:1",
        text="Waarschuw me stil als ik geconcentreerd werk.",
        occurred_at="2026-08-24T12:00:00Z",
    )
    reply = GroundedReply(
        conversation_id=turn.conversation_id,
        message_id=turn.message_id,
        text="Dan laat ik voortaan een lamp knipperen.",
        evidence_memory_ids=(),
        generated_at="2026-08-24T12:00:01Z",
        provider="test",
    )
    cold_path = _ColdPath([])
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._cold_path = cold_path  # type: ignore[attr-defined]
    runtime._journal = lambda _turn: _Journal()  # type: ignore[attr-defined,method-assign]

    runtime.after_response(turn, reply)

    assert len(cold_path.documents) == 1
    document = cold_path.documents[0]
    assert document.trust == "direct_user"  # type: ignore[attr-defined]
    assert document.content == (  # type: ignore[attr-defined]
        "Gebruiker (directe bron): "
        "Waarschuw me stil als ik geconcentreerd werk."
    )
    assert reply.text not in document.content  # type: ignore[attr-defined]


def test_bound_identity_correction_retains_only_the_grounded_user_fact() -> None:
    binding = ConversationObservationBinding(
        "telegram-photo:mark-1",
        "a" * 64,
        "b" * 64,
        "image/jpeg",
        "telegram:file:mark-1",
    )
    turn = ConversationTurn.private(
        "tenant:1",
        "installation:1",
        "person:1",
        "conversation:1",
        "message:photo:1",
        "Nee, dat is Mark.",
        "2026-08-24T12:00:00Z",
        observation_binding=binding,
    )
    reply = GroundedReply(
        turn.conversation_id,
        turn.message_id,
        "Duidelijk, dat is Mark.",
        (),
        turn.occurred_at,
        "test",
    )
    receipt = ConversationActionReceipt(
        ConversationActionCall(
            "call:photo:1",
            "record_observation_identity_correction",
            {"corrected_name": "Mark"},
        ),
        {
            "binding_sha256": binding.binding_sha256,
            "contract": "EvaConversationActionReceipt/v1",
            "corrected_name": "Mark",
            "correction_id": "observation-identity:test",
            "durable": True,
            "observation_id": binding.observation_id,
            "status": "recorded",
            "subject_region_sha256": binding.subject_region_sha256,
        },
    )

    class Actions:
        def pending(self, _turn: ConversationTurn) -> ConversationActionReceipt:
            return receipt

    class Journal:
        def load(self, _turn: ConversationTurn) -> object:
            return object()

        def memory_queued(
            self,
            _turn: ConversationTurn,
            _state: object,
            *,
            operation_document_id: str,
        ) -> None:
            assert operation_document_id == (
                "conversation:1:observation-identity:message:photo:1"
            )

    cold_path = _ColdPath([])
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._cold_path = cold_path  # type: ignore[attr-defined]
    runtime._conversation_actions = Actions()  # type: ignore[attr-defined]
    runtime._journal = lambda _turn: Journal()  # type: ignore[attr-defined,method-assign]

    runtime.after_response(turn, reply)

    document = cold_path.documents[0]
    assert document.privacy_class == "confidential"  # type: ignore[attr-defined]
    assert document.evidence_ref == (  # type: ignore[attr-defined]
        f"observation-binding:{binding.binding_sha256}"
    )
    assert "Mark" in document.content  # type: ignore[attr-defined]
    assert binding.subject_region_sha256 in document.content  # type: ignore[attr-defined]
    assert reply.text not in document.content  # type: ignore[attr-defined]


def test_group_turn_retains_the_same_audience_and_group_bound_provenance() -> None:
    audience = MemoryAudience.telegram_group(
        principal_id="person:1",
        group_chat_id="-100123",
        consent_ref=TelegramGroupConsentReference(
            principal_id="person:1",
            group_chat_id="-100123",
            operation_id="telegram-group-consent:retention-test",
        ),
    )
    turn = ConversationTurn(
        tenant_id="tenant:1",
        installation_id="installation:1",
        principal_id="person:1",
        conversation_id="telegram:-100123",
        message_id="telegram:-100123:message:7",
        text="De barbecue begint om zes uur.",
        occurred_at="2026-08-24T12:00:00Z",
        audience=audience,
    )
    reply = GroundedReply(
        turn.conversation_id,
        turn.message_id,
        "Gezellig.",
        (),
        turn.occurred_at,
        "test",
    )

    class Journal:
        def load(self, _turn: ConversationTurn) -> object:
            return object()

        def memory_queued(
            self,
            _turn: ConversationTurn,
            _state: object,
            *,
            operation_document_id: str,
        ) -> None:
            assert operation_document_id.startswith("telegram:-100123:")

    cold_path = _ColdPath([])
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._cold_path = cold_path  # type: ignore[attr-defined]
    runtime._journal = lambda _turn: Journal()  # type: ignore[attr-defined,method-assign]

    runtime.after_response(turn, reply)

    document = cold_path.documents[0]
    assert document.audience is audience  # type: ignore[attr-defined]
    assert document.document_id.startswith("telegram:-100123:")  # type: ignore[attr-defined]
    assert document.source_ref.startswith("telegram:-100123:")  # type: ignore[attr-defined]
    assert document.evidence_ref.startswith("telegram:-100123:")  # type: ignore[attr-defined]


def test_conversation_rejects_a_cross_installation_scope_before_model_work() -> None:
    turn = ConversationTurn.private(
        tenant_id="other-tenant",
        installation_id="other-installation",
        principal_id="person:2",
        conversation_id="conversation:other",
        message_id="message:other",
        text="Onthoud dit.",
        occurred_at="2026-08-24T12:00:00Z",
    )
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._runtime_tenant_id = "tenant:1"  # type: ignore[attr-defined]
    runtime._runtime_installation_id = "installation:1"  # type: ignore[attr-defined]
    runtime._journal = lambda _turn: pytest.fail("journal must not be opened")  # type: ignore[attr-defined,method-assign]

    with pytest.raises(ValueError, match="crossed its runtime scope"):
        runtime.respond(turn)


def test_group_turn_revalidates_exact_consent_before_journal_or_model() -> None:
    reference = TelegramGroupConsentReference(
        principal_id="person:1",
        group_chat_id="-100123",
        operation_id="telegram-group-consent:current",
    )
    turn = ConversationTurn(
        tenant_id="tenant:1",
        installation_id="installation:1",
        principal_id="person:1",
        conversation_id="telegram:-100123",
        message_id="telegram:-100123:message:8",
        text="Dit is voor de groep.",
        occurred_at="2026-08-24T12:00:00Z",
        audience=MemoryAudience.telegram_group(
            principal_id="person:1",
            group_chat_id="-100123",
            consent_ref=reference,
        ),
    )

    class Consent:
        active = reference

        def active_reference(self, *, principal_id, group_chat_id, now):  # type: ignore[no-untyped-def]
            assert (principal_id, group_chat_id) == ("person:1", "-100123")
            assert now.tzinfo is not None
            return self.active

    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._runtime_tenant_id = "tenant:1"  # type: ignore[attr-defined]
    runtime._runtime_installation_id = "installation:1"  # type: ignore[attr-defined]
    runtime._group_consent = Consent()  # type: ignore[attr-defined]
    runtime._journal = lambda _turn: pytest.fail("journal must not be opened")  # type: ignore[attr-defined,method-assign]

    Consent.active = TelegramGroupConsentReference(
        principal_id="person:1",
        group_chat_id="-100123",
        operation_id="telegram-group-consent:replaced",
    )
    with pytest.raises(ValueError, match="not active and exact"):
        runtime.respond(turn)

    Consent.active = None
    with pytest.raises(ValueError, match="not active and exact"):
        runtime.respond(turn)
