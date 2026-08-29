from __future__ import annotations

import pytest

from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationClarificationRequired,
    ConversationModelResult,
    ConversationObservationBinding,
    ConversationTurn,
    IdentityProfile,
    LivingConversation,
    MemorySearchPlan,
    load_current_identity_pack,
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


def test_memory_intent_is_model_planned_without_question_mark() -> None:
    class PlanningMemory(_Memory):
        def __init__(self) -> None:
            super().__init__()
            self.queries: list[str] = []

        def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
            self.queries.append(query.query_text)
            return super().recall_for_presence(query)

    class PlanningModel(_Model):
        def expand_memory_query(self, turn, recent_messages):  # type: ignore[no-untyped-def]
            assert turn.text == "Zeg nog eens hoe warm ik het bij de veranda wilde"
            assert recent_messages == ()
            return MemorySearchPlan(
                True,
                "veranda overkapping buitenruimte temperatuur warm koel voorkeur",
            )

    memory = PlanningMemory()
    conversation = LivingConversation(
        memory,
        PlanningModel(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
    )

    conversation.respond(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "telegram:jaap",
            "telegram:memory:no-punctuation",
            "Zeg nog eens hoe warm ik het bij de veranda wilde",
            NOW,
        )
    )

    assert memory.queries == [
        "Zeg nog eens hoe warm ik het bij de veranda wilde",
        (
            "Zeg nog eens hoe warm ik het bij de veranda wilde\n"
            "Semantische zoekconcepten: veranda overkapping buitenruimte "
            "temperatuur warm koel voorkeur"
        ),
    ]


def test_semantic_planner_offers_only_selected_available_actions() -> None:
    offered: list[tuple[str, ...]] = []

    class Actions:
        action_names = ("read_home_assistant_state", "research_current_options")

        def pending(self, turn):  # type: ignore[no-untyped-def]
            return None

        def active_context(self, turn):  # type: ignore[no-untyped-def]
            return ()

        def execute(self, turn, call):  # type: ignore[no-untyped-def]
            raise AssertionError((turn, call))

    class PlanningModel(_Model):
        def expand_memory_query(self, turn, recent_messages):  # type: ignore[no-untyped-def]
            return MemorySearchPlan(
                False,
                None,
                ("read_home_assistant_state", "unavailable_action"),
            )

        def generate(self, *args, available_actions=(), **kwargs):  # type: ignore[no-untyped-def]
            offered.append(available_actions)
            return super().generate(
                *args, available_actions=available_actions, **kwargs
            )

    conversation = LivingConversation(
        _Memory(),
        PlanningModel(),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        Actions(),
    )
    conversation.respond(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message:selected-tools",
            "Lees de lampstatus.",
            NOW,
        )
    )

    assert offered == [("read_home_assistant_state",)]


def test_memory_search_plan_limits_selected_actions() -> None:
    with pytest.raises(ValueError, match="selected conversation actions"):
        MemorySearchPlan(False, None, ("same", "same"))

    with pytest.raises(ValueError, match="selected conversation actions"):
        MemorySearchPlan(False, None, ("a", "b", "c", "d", "e"))


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
