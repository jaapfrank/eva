from __future__ import annotations

import pytest

from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationClarificationRequired,
    ConversationConflict,
    ConversationModelResult,
    ConversationModelUsage,
    ConversationObservationBinding,
    ConversationToolPreparation,
    ConversationTurn,
    IdentityProfile,
    LivingConversation,
    MEMORY_SEARCH_ACTION,
    stream_conversation_text,
)
from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import ConfirmedFact


NOW = "2026-08-23T14:00:00Z"


def test_external_tool_observation_output_key_is_fail_closed() -> None:
    preparation = ConversationToolPreparation(
        tool_name="calendar.read",
        arguments={"day": "tomorrow"},
        expected_proof={"day": "tomorrow"},
        receipt_output={"calendar": "personal"},
        observation_output_key="appointments",
    )
    assert preparation.observation_output_key == "appointments"

    for invalid in ("status", "not-a-key", "calendar"):
        with pytest.raises(
            ValueError, match="prepared conversation observation output is invalid"
        ):
            ConversationToolPreparation(
                tool_name="calendar.read",
                arguments={"day": "tomorrow"},
                expected_proof={"day": "tomorrow"},
                receipt_output={"calendar": "personal"},
                observation_output_key=invalid,
            )


def test_external_tool_preparation_keeps_historical_positional_timeout_order() -> None:
    preparation = ConversationToolPreparation(
        "calendar.read",
        {"day": "tomorrow"},
        {"day": "tomorrow"},
        {"calendar": "personal"},
        11,
        12,
        "appointments",
    )

    assert preparation.execution_timeout_seconds == 11
    assert preparation.verification_timeout_seconds == 12
    assert preparation.observation_output_key == "appointments"
    assert preparation.observation_tool_name is None


def test_bundled_procedural_identity_is_the_exact_current_eva_pack() -> None:
    pack = load_current_identity_pack()

    assert pack.display_name == "Eva"
    assert pack.state == "current"
    assert pack.voice_style.name == "warm_practical_curious"
    assert pack.voice_style.qualities == [
        "warm",
        "practical",
        "curious",
        "concise",
        "slightly_stubborn",
    ]
    assert canonical_sha256(pack) == (
        "025984c019714d7107803258959fcbddd94d0700acaef15beeac26f05a645798"
    )


class _Memory:
    def __init__(self) -> None:
        self.query: RecallCurrentFacts | None = None

    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
        self.query = query
        fact = ConfirmedFact(
            fact_id="hindsight:memory-1",
            subject=query.principal_id,
            predicate="memory.observation",
            value={"text": "Jaap wil korte, directe antwoorden."},
            source_ref="telegram:history",
            evidence_ref="hindsight:memory-1",
            observed_at=NOW,
            trust="host_policy",
        )
        recall = MemoryRecall(
            schema_version=1,
            contract="MemoryRecall/v1",
            recall_id=query.recall_id,
            query_purpose=query.query_purpose,
            tenant_id=query.tenant_id,
            principal_id=query.principal_id,
            conversation_id=query.conversation_id,
            project_id=query.project_id,
            requested_valid_at=query.requested_valid_at,
            admitted_fact_ids_sha256=canonical_sha256([fact.fact_id]),
            selected=[
                RecalledFact(
                    rank=1,
                    fact_id=fact.fact_id,
                    source_event_ids=[fact.source_ref],
                    trust="model_inference_unconfirmed",
                    ranking_reason="hindsight_temporal_semantic_match",
                ).model_dump(mode="json")
            ],
            excluded_reasons={},
            projection_version="hindsight-0.9.1-v1",
            scope_sha256=canonical_sha256(query.scope),
            created_at=NOW,
        )
        return PresenceMemory(recall, (fact,))


class _Model:
    def __init__(self) -> None:
        self.memory: PresenceMemory | None = None

    def generate(  # type: ignore[no-untyped-def]
        self,
        turn,
        identity,
        memory,
        recent_messages,
        *,
        available_actions=(),
        completed_action=None,
        self_context=(),
    ):
        self.memory = memory
        return ConversationModelResult(
            "Dat klinkt als een rotdag. Wil je vertellen wat er gebeurde?",
            "direct-conversation-model",
        )


class _FastMemory(_Memory):
    def recall_for_presence_fast(self, query: RecallCurrentFacts) -> PresenceMemory:
        return super().recall_for_presence(query)

    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
        raise AssertionError("direct conversation waited for cold memory")


def test_direct_answer_uses_fast_memory_boundary_when_available() -> None:
    memory = _FastMemory()
    model = _Model()
    conversation = LivingConversation(
        memory,
        model,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
    )

    conversation.respond(
        ConversationTurn.private(
            "tenant", "installation", "jaap", "conversation", "message", "Hoi", NOW
        )
    )

    assert memory.query is not None
    assert model.memory is not None


def test_hot_path_recalls_then_lets_evas_model_answer() -> None:
    memory = _Memory()
    model = _Model()
    conversation = LivingConversation(
        memory,
        model,
        IdentityProfile(
            "Eva",
            "Je bent een warme, nuchtere digitale huisgenoot.",
            ("Wees eerlijk.",),
            maximum_reply_words=80,
        ),
    )
    turn = ConversationTurn.private(
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id="telegram:9001",
        text="Ik heb vandaag echt een rotdag.",
        occurred_at=NOW,
    )

    reply = conversation.respond(turn)

    assert memory.query is not None
    assert memory.query.maximum_facts == 12
    assert memory.query.query_text == turn.text
    assert model.memory is not None
    assert reply.text.startswith("Dat klinkt")
    assert reply.evidence_memory_ids == ("hindsight:memory-1",)
    assert reply.provider == "direct-conversation-model"


def test_hot_path_binds_recall_to_the_explicit_person_and_project() -> None:
    memory = _Memory()
    conversation = LivingConversation(
        memory,
        _Model(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "project-red-chat",
        "project-red-message",
        "Wat was het codewoord voor dit project?",
        NOW,
        project_id="project-red",
    )

    conversation.respond(turn)

    assert memory.query is not None
    assert memory.query.project_id == "project-red"
    assert memory.query.scope == {
        "kind": "person_project",
        "principal_id": "jaap",
        "project_id": "project-red",
    }


def test_group_turn_passes_the_exact_audience_and_group_scope_to_recall() -> None:
    memory = _Memory()
    conversation = LivingConversation(
        memory,
        _Model(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
    )
    audience = MemoryAudience.telegram_group(
        principal_id="jaap",
        group_chat_id="-100123",
        consent_ref=TelegramGroupConsentReference(
            principal_id="jaap",
            group_chat_id="-100123",
            operation_id="telegram-group-consent:turn-test",
        ),
    )
    turn = ConversationTurn(
        "tenant",
        "installation",
        "jaap",
        "telegram:-100123",
        "telegram:-100123:9002",
        "Hoe laat begint de barbecue?",
        NOW,
        audience,
    )

    conversation.respond(turn)

    assert memory.query is not None
    assert memory.query.audience is audience
    assert memory.query.scope == {
        "kind": "telegram_group",
        "group_chat_id": "-100123",
    }


def test_bound_photo_region_is_carried_into_recall_and_action_availability() -> None:
    binding = ConversationObservationBinding(
        observation_id="telegram-photo:fixture-7",
        media_sha256="a" * 64,
        subject_region_sha256="b" * 64,
        media_type="image/jpeg",
        source_ref="telegram:file:fixture-7",
    )
    memory = _Memory()
    offered: list[tuple[str, ...]] = []

    class Actions:
        action_names = ("ordinary_action",)

        def pending(self, turn):  # type: ignore[no-untyped-def]
            return None

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def available_action_names(self, turn):  # type: ignore[no-untyped-def]
            assert turn.observation_binding == binding
            return ("record_observation_identity_correction",)

        def memory_query_hints(self, turn):  # type: ignore[no-untyped-def]
            assert turn.observation_binding == binding
            return ("Mark",)

        def execute(self, turn, call):  # type: ignore[no-untyped-def]
            raise AssertionError((turn, call))

    class Model(_Model):
        def generate(self, *args, available_actions=(), **kwargs):  # type: ignore[no-untyped-def]
            offered.append(available_actions)
            return super().generate(
                *args, available_actions=available_actions, **kwargs
            )

    conversation = LivingConversation(
        memory,
        Model(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        Actions(),  # type: ignore[arg-type]
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "telegram:jaap",
        "telegram:photo-correction",
        "Nee, dat is Mark.",
        NOW,
        observation_binding=binding,
    )

    conversation.respond(turn)

    assert memory.query is not None
    assert binding.binding_sha256 in memory.query.query_text
    assert "Gebonden retrieval-ankers: Mark" in (memory.query.query_text)
    assert offered == [("record_observation_identity_correction",)]


def test_photo_identity_memory_is_filtered_to_the_exact_bound_region() -> None:
    binding = ConversationObservationBinding(
        "photo:current",
        "a" * 64,
        "b" * 64,
        "image/jpeg",
        "object:current",
    )
    own = ConfirmedFact(
        fact_id="hindsight:own-region",
        subject="jaap",
        predicate="memory.observation",
        value={"text": "Deze persoon is Marieke."},
        source_ref="message:own",
        evidence_ref=f"observation-binding:{binding.binding_sha256}",
        observed_at=NOW,
        trust="direct_user",
    )
    foreign = ConfirmedFact(
        fact_id="hindsight:foreign-region",
        subject="jaap",
        predicate="memory.observation",
        value={"text": "Een andere persoon is Mark."},
        source_ref="message:foreign",
        evidence_ref=f"observation-binding:{'c' * 64}",
        observed_at=NOW,
        trust="direct_user",
    )
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="recall:photo",
        query_purpose="conversation_reply",
        tenant_id="tenant",
        principal_id="jaap",
        conversation_id="conversation",
        project_id=None,
        requested_valid_at=NOW,
        admitted_fact_ids_sha256=canonical_sha256([own.fact_id, foreign.fact_id]),
        selected=[
            RecalledFact(
                rank=1,
                fact_id=foreign.fact_id,
                source_event_ids=[foreign.source_ref],
                trust="confirmed_direct_user",
                ranking_reason="hindsight_temporal_semantic_match",
            ).model_dump(mode="json"),
            RecalledFact(
                rank=2,
                fact_id=own.fact_id,
                source_event_ids=[own.source_ref],
                trust="confirmed_direct_user",
                ranking_reason="hindsight_temporal_semantic_match",
            ).model_dump(mode="json"),
        ],
        excluded_reasons={},
        projection_version="hindsight-0.9.1-v1",
        scope_sha256=canonical_sha256({"kind": "person"}),
        created_at=NOW,
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "Wie is dit?",
        NOW,
        observation_binding=binding,
    )

    filtered = LivingConversation._bound_observation_memory(
        turn, PresenceMemory(recall, (foreign, own))
    )

    assert [fact.fact_id for fact in filtered.confirmed_facts] == [own.fact_id]
    assert [item.fact_id for item in filtered.recall.selected] == [own.fact_id]
    assert filtered.recall.selected[0].rank == 1
    assert filtered.recall.excluded_reasons == {
        foreign.fact_id: "observation_binding_mismatch"
    }


def test_model_requested_memory_search_recalls_then_answers() -> None:
    class SearchMemory(_Memory):
        def __init__(self) -> None:
            super().__init__()
            self.queries: list[str] = []
            self.fast_queries: list[str] = []

        def recall_for_presence_fast(self, query: RecallCurrentFacts) -> PresenceMemory:
            self.fast_queries.append(query.query_text)
            return _Memory.recall_for_presence(self, query)

        def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
            self.queries.append(query.query_text)
            return super().recall_for_presence(query)

    class SearchModel(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def generate(self, *args, completed_action=None, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return ConversationModelResult(
                    None,
                    "direct-conversation-model",
                    ConversationActionCall(
                        "memory-query-1",
                        MEMORY_SEARCH_ACTION,
                        {"query_text": "veranda overkapping temperatuur voorkeur"},
                    ),
                )
            assert completed_action.call.name == MEMORY_SEARCH_ACTION
            return super().generate(*args, completed_action=completed_action, **kwargs)

    memory = SearchMemory()
    model = SearchModel()
    conversation = LivingConversation(
        memory,
        model,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
    )
    text = "Zeg nog eens hoe warm ik het bij de veranda wilde"
    conversation.respond(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "telegram:jaap",
            "telegram:memory:no-punctuation",
            text,
            NOW,
        )
    )

    assert model.calls == 2
    assert memory.fast_queries == [text]
    assert memory.queries == [
        f"{text}\nSemantische zoekconcepten: veranda overkapping temperatuur voorkeur",
    ]


def test_memory_only_answer_is_not_replayed_as_a_fake_stream() -> None:
    class Actions:
        action_names = ("read_home_assistant_state",)

        def pending(self, turn):  # type: ignore[no-untyped-def]
            return None

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def execute(self, turn, call):  # type: ignore[no-untyped-def]
            raise AssertionError("memory-only answer executed an external action")

    class Model(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def generate(self, *args, completed_action=None, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return ConversationModelResult(
                    None,
                    "model",
                    ConversationActionCall(
                        "memory-call",
                        MEMORY_SEARCH_ACTION,
                        {"query_text": "favoriete kleur"},
                    ),
                )
            assert completed_action.call.name == MEMORY_SEARCH_ACTION
            return ConversationModelResult("Die kleur kan ik niet vinden.", "model")

    deltas: list[str] = []
    conversation = LivingConversation(
        _Memory(),
        Model(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        Actions(),
    )
    with stream_conversation_text(deltas.append):
        reply = conversation.respond(
            ConversationTurn.private(
                "tenant",
                "installation",
                "jaap",
                "conversation",
                "message:memory-stream",
                "Wat is mijn favoriete kleur?",
                NOW,
            )
        )

    assert reply.text == "Die kleur kan ik niet vinden."
    assert deltas == []


def test_direct_answer_uses_one_model_call_without_a_fake_posthoc_delta() -> None:
    class DirectModel(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().generate(*args, **kwargs)

    model = DirectModel()
    conversation = LivingConversation(
        _Memory(),
        model,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
    )
    deltas: list[str] = []
    with stream_conversation_text(deltas.append):
        reply = conversation.respond(
            ConversationTurn.private(
                "tenant",
                "installation",
                "jaap",
                "conversation",
                "message:dynamic",
                "Hoi Eva",
                NOW,
            )
        )

    assert model.calls == 1
    assert reply.text
    assert deltas == []


def test_memory_search_can_be_followed_by_one_available_action() -> None:
    calls: list[str] = []

    class Actions:
        action_names = ("read_home_assistant_state", "research_current_options")

        def pending(self, turn):  # type: ignore[no-untyped-def]
            return None

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def execute(self, turn, call):  # type: ignore[no-untyped-def]
            calls.append(call.name)
            return ConversationActionReceipt(call, {"state": "off"})

    class SearchThenActModel(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def generate(  # type: ignore[no-untyped-def]
            self, *args, available_actions=(), completed_action=None, **kwargs
        ):
            self.calls += 1
            if self.calls == 1:
                assert set(available_actions) == set(Actions.action_names)
                return ConversationModelResult(
                    None,
                    "model",
                    ConversationActionCall(
                        "memory-call",
                        MEMORY_SEARCH_ACTION,
                        {"query_text": "favoriete lamp overkapping veranda"},
                    ),
                )
            if self.calls == 2:
                assert completed_action.call.name == MEMORY_SEARCH_ACTION
                assert set(available_actions) == set(Actions.action_names)
                return ConversationModelResult(
                    None,
                    "model",
                    ConversationActionCall(
                        "home-call",
                        "read_home_assistant_state",
                        {"target_name": "spotjes overkapping"},
                    ),
                )
            assert completed_action.call.name == "read_home_assistant_state"
            assert available_actions == ()
            return ConversationModelResult("De spotjes zijn uit.", "model")

    model = SearchThenActModel()
    conversation = LivingConversation(
        _Memory(),
        model,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        Actions(),
    )
    reply = conversation.respond(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message:memory-then-action",
            "Staat mijn favoriete lamp uit?",
            NOW,
        )
    )

    assert reply.text == "De spotjes zijn uit."
    assert model.calls == 3
    assert calls == ["read_home_assistant_state"]


def test_invalid_model_requested_memory_query_is_rejected() -> None:
    class InvalidModel(_Model):
        def generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return ConversationModelResult(
                None,
                "model",
                ConversationActionCall(
                    "memory-call", MEMORY_SEARCH_ACTION, {"query_text": ""}
                ),
            )

    conversation = LivingConversation(
        _Memory(),
        InvalidModel(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
    )
    with pytest.raises(RuntimeError, match="memory search request"):
        conversation.respond(
            ConversationTurn.private(
                "tenant",
                "installation",
                "jaap",
                "conversation",
                "message:invalid-memory",
                "Wat weet je nog?",
                NOW,
            )
        )


def test_model_chosen_interest_is_committed_before_eva_claims_it() -> None:
    order: list[str] = []
    call = ConversationActionCall(
        "call:interest:1",
        "adopt_personal_interest",
        {
            "motivation": "Ik vind patronen en praktische verbeteringen interessant.",
            "next_exploration": "Welke restjes leveren de beste nieuwe maaltijd op?",
            "title": "Slim koken met wat er al is",
        },
    )
    receipt = ConversationActionReceipt(
        call,
        {
            "contract": "EvaConversationActionReceipt/v1",
            "disposition": "adopted",
            "durable": True,
            "intent_id": "personal-interest:1",
            "motivation": call.arguments["motivation"],
            "next_exploration": call.arguments["next_exploration"],
            "state": "active",
            "title": call.arguments["title"],
        },
    )

    class Actions:
        action_names = ("adopt_personal_interest",)

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def pending(self, turn):  # type: ignore[no-untyped-def]
            order.append("pending")
            return None

        def execute(self, turn, proposed):  # type: ignore[no-untyped-def]
            assert proposed == call
            order.append("committed")
            return receipt

    class ActionModel(_Model):
        def generate(  # type: ignore[no-untyped-def]
            self,
            turn,
            identity,
            memory,
            recent_messages,
            *,
            available_actions=(),
            completed_action=None,
            self_context=(),
        ):
            if completed_action is None:
                assert available_actions == ("adopt_personal_interest",)
                order.append("chosen")
                return ConversationModelResult(None, "model", call)
            assert available_actions == ()
            assert completed_action == receipt
            order.append("expressed")
            return ConversationModelResult(
                "Ik kies slim koken met wat er al is; dat staat nu echt als mijn interesse.",
                "model",
            )

    conversation = LivingConversation(
        _Memory(),
        ActionModel(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        Actions(),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "telegram:jaap",
        "telegram:interest:1",
        "Kies zelf een hobby.",
        NOW,
    )

    reply = conversation.respond(turn)

    assert order == ["pending", "chosen", "committed", "expressed"]
    assert reply.text.startswith("Ik kies slim koken")


def test_action_clarification_becomes_one_normal_reply_without_second_model_call() -> (
    None
):
    call = ConversationActionCall(
        "call:light:ambiguous",
        "read_home_assistant_state",
        {"entity_kind": "light", "target": "licht in de overkapping"},
    )
    model_calls = 0

    class Actions:
        action_names = ("read_home_assistant_state",)

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def pending(self, turn):  # type: ignore[no-untyped-def]
            return None

        def execute(self, turn, proposed):  # type: ignore[no-untyped-def]
            assert proposed == call
            raise ConversationClarificationRequired("Welke lamp bedoel je precies?")

    class ActionModel(_Model):
        def generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal model_calls
            model_calls += 1
            assert kwargs["available_actions"] == ("read_home_assistant_state",)
            return ConversationModelResult(None, "model", call)

    conversation = LivingConversation(
        _Memory(),
        ActionModel(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        Actions(),
    )
    reply = conversation.respond(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "telegram:jaap",
            "telegram:light:ambiguous",
            "Staat het licht in de overkapping aan?",
            NOW,
        )
    )

    assert reply.text == "Welke lamp bedoel je precies?"
    assert reply.provider == "model"
    assert model_calls == 1


def test_pending_durable_interest_resumes_without_choosing_again() -> None:
    call = ConversationActionCall(
        "call:interest:resume",
        "adopt_personal_interest",
        {
            "motivation": "Ik hou van ordenen.",
            "next_exploration": "Welke routine geeft thuis de meeste rust?",
            "title": "Rustige huisrituelen",
        },
    )
    receipt = ConversationActionReceipt(
        call,
        {
            "contract": "EvaConversationActionReceipt/v1",
            "disposition": "already_adopted",
            "durable": True,
            "intent_id": "personal-interest:resume",
            "motivation": call.arguments["motivation"],
            "next_exploration": call.arguments["next_exploration"],
            "state": "active",
            "title": call.arguments["title"],
        },
    )

    class Actions:
        action_names = ("adopt_personal_interest",)

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def pending(self, turn):  # type: ignore[no-untyped-def]
            return receipt

        def execute(self, turn, proposed):  # type: ignore[no-untyped-def]
            raise AssertionError("durable action must not execute twice")

    class ResumeModel(_Model):
        def generate(  # type: ignore[no-untyped-def]
            self,
            turn,
            identity,
            memory,
            recent_messages,
            *,
            available_actions=(),
            completed_action=None,
            self_context=(),
        ):
            assert available_actions == ()
            assert completed_action == receipt
            return ConversationModelResult(
                "Mijn gekozen interesse blijft rustige huisrituelen.", "model"
            )

    conversation = LivingConversation(
        _Memory(),
        ResumeModel(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        Actions(),
    )
    reply = conversation.respond(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "telegram:jaap",
            "telegram:interest:resume",
            "Kies zelf een hobby.",
            NOW,
        )
    )

    assert reply.text == "Mijn gekozen interesse blijft rustige huisrituelen."


@pytest.mark.parametrize("already_completed", [0, 1, 2])
@pytest.mark.parametrize("first_status", ["verified", "failed"])
def test_dependent_tool_chain_recovers_before_model_and_aggregates_usage(already_completed, first_status):
    first = ConversationActionReceipt(
        ConversationActionCall("step:0", "inspect_item", {"item": "root"}),
        {"status": first_status, "next": "child"},
    )
    second = ConversationActionReceipt(
        ConversationActionCall("step:1", "inspect_item", {"item": "child"}),
        {"status": "verified", "next": "leaf"},
    )
    durable = {i: value for i, value in enumerate((first, second)) if i < already_completed}
    executions, requests = [], []

    class Actions:
        action_names = ("inspect_item",)

        def pending(self, turn):
            return durable.get(0)

        def pending_step(self, turn, *, step_index):
            return durable.get(step_index)

        def can_continue_after(self, turn, receipt):
            return receipt == first

        def active_context(self, turn):
            return ()

        def continuation_action_names(self, turn):
            return self.action_names

        def execute(self, turn, call):
            return self.perform(call, 0)

        def execute_step(self, turn, call, *, step_index):
            return self.perform(call, step_index)

        def perform(self, call, index):
            assert index not in durable, "completed action must not execute again"
            receipt = (first, second)[index]
            assert call == receipt.call
            executions.append(index)
            durable[index] = receipt
            return receipt

    class Model:
        def generate(self, *args, completed_action=None, prior_actions=(), **kwargs):
            requests.append(completed_action)
            if completed_action is None:
                call = first.call
            elif completed_action == first:
                assert completed_action.output["next"] == second.call.arguments["item"]
                call = second.call
            else:
                assert completed_action == second
                assert prior_actions == (first,)
                call = None
            return ConversationModelResult(
                "Het kind is gecontroleerd." if call is None else None,
                "fixture", call,
                usage=ConversationModelUsage.measured(
                    input_tokens=10, output_tokens=2, tool_calls=int(call is not None),
                ),
            )

    turn = ConversationTurn.private(
        "tenant", "installation", "jaap", "conversation", "chain:1",
        "Inspecteer root en daarna het gevonden kind.", NOW,
    )
    result = LivingConversation(
        _Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions(),
    ).respond(turn)
    assert result.text == "Het kind is gecontroleerd."
    assert executions == list(range(already_completed, 2))
    assert len(requests) == 3 - already_completed
    assert result.model_usage.model_attempts == len(requests)
    assert result.model_usage.input_tokens == 10 * len(requests)
    assert result.model_usage.tool_calls == len(executions)


def test_chain_checkpoint_can_stop_after_model_before_tool_dispatch():
    stopped = False

    class Actions:
        action_names = ("inspect_item",)

        def pending(self, turn):
            return None

        def active_context(self, turn):
            return ()

        def execute(self, turn, call):
            raise AssertionError("stop must prevent dispatch")

    class Model:
        def generate(self, *args, **kwargs):
            nonlocal stopped
            stopped = True
            return ConversationModelResult(
                None, "fixture", ConversationActionCall("step:0", "inspect_item", {}),
            )

    def checkpoint(turn, usage):
        if stopped:
            raise RuntimeError("owner stopped execution")

    turn = ConversationTurn.private(
        "tenant", "installation", "jaap", "conversation", "chain:stop",
        "Inspecteer root.", NOW,
    )
    with pytest.raises(RuntimeError, match="owner stopped"):
        LivingConversation(
            _Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80),
            Actions(), before_step=checkpoint,
        ).respond(turn)



def test_memory_search_call_id_cannot_be_reused_for_an_external_action():
    class Actions:
        action_names = ("inspect_item",)

        def pending(self, turn):
            return None

        def active_context(self, turn):
            return ()

        def execute(self, turn, call):
            raise AssertionError("duplicate call ID must not reach dispatch")

    class Model:
        def generate(self, *args, completed_action=None, **kwargs):
            call = (
                ConversationActionCall("same-id", MEMORY_SEARCH_ACTION, {"query_text": "voorkeur"})
                if completed_action is None else
                ConversationActionCall("same-id", "inspect_item", {})
            )
            return ConversationModelResult(None, "fixture", call)

    with pytest.raises(ConversationConflict, match="reused a completed call ID"):
        LivingConversation(
            _Memory(), Model(), IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), Actions(),
        ).respond(ConversationTurn.private(
            "tenant", "installation", "jaap", "conversation", "same-id-test",
            "Gebruik mijn voorkeur om het item te bekijken.", NOW,
        ))


@pytest.mark.parametrize("long_text", [
    "Een volledig eerste antwoord.\n\n" + "toelichting " * 170,
    "Een volledig eerste antwoord. " + "toelichting " * 170,
    "toelichting " * 161,
    "```python\n" + "waarde " * 170 + "\n```",
])
def test_overlong_reply_finishes_once_with_explicit_bounded_text(long_text):
    calls = []
    class Model(_Model):
        def generate(self, *args, **kwargs):
            calls.append(1)
            return ConversationModelResult(long_text, "fixture-model")
    conversation = LivingConversation(_Memory(), Model(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 160))
    turn = ConversationTurn.private("tenant", "installation", "jaap", "telegram:jaap",
                                    "telegram:limit", "Geef een toelichting.", NOW)
    reply = conversation.respond(turn)
    assert calls == [1]
    assert reply.text.startswith("Dit antwoord is ingekort; de toelichting is onvolledig.")
    assert len(reply.text.split()) <= 160
    assert reply.text.count("```") % 2 == 0
    if long_text.startswith("Een volledig"):
        assert reply.text.endswith("Een volledig eerste antwoord.")
    assert reply.evidence_memory_ids == ("hindsight:memory-1",)


def test_reply_exactly_at_word_limit_keeps_text_and_reference():
    text = "woord " * 157 + "[Bekijk het resultaat](https://example.test/resultaat)."
    class Model(_Model):
        def generate(self, *args, **kwargs):
            return ConversationModelResult(text, "fixture-model")
    conversation = LivingConversation(_Memory(), Model(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 160))
    reply = conversation.respond(ConversationTurn.private("tenant", "installation", "jaap",
        "telegram:jaap", "telegram:exact", "Geef een toelichting.", NOW))
    assert reply.text == text
