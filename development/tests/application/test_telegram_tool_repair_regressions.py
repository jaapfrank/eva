"""Regression tests for real Telegram tool argument repair, strict nullable schemas, and human-safe replies."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID
import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres import delegated_work_actions as delegated_actions_module
from eva.adapters.postgres import scheduled_work as scheduled_work_module
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionInputRejected,
    ConversationActionReceipt,
    ConversationClarificationRequired,
    LivingConversation,
)
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.bootstrap.public_lookup import PUBLIC_LOOKUP, public_arguments
from eva.bootstrap.recent_activity import RECENT_ACTIVITY, RecentActivityActions
from tests.application.test_contact_model import Actions, IDENTITY, Model, turn
from tests.application.test_conversation import _Memory
from tests.model.test_conversation_model import _memory


def test_public_lookup_clock_time_rejection_repairs_without_do_not_repeat_contradiction(tmp_path):
    with pytest.raises(ConversationActionInputRejected) as caught:
        public_arguments(
            {
                "question": "Is er nieuws na 15:36 of het kabinet nog bestaat?",
                "public_review": "public_unlinked",
                "answer_directly": True,
            }
        )
    assert "nog niet gestart" in caught.value.prompt
    assert "kloktijden" in caught.value.prompt

    model = PydanticConversationModel(
        auth_file=tmp_path / "unused",
        conversation_tools=(PUBLIC_LOOKUP,),
    )
    invalid_receipt = ConversationActionReceipt(
        call=ConversationActionCall(
            "lookup-1",
            "search_public_info",
            {
                "question": "Is er nieuws na 15:36 of het kabinet nog bestaat?",
                "public_review": "public_unlinked",
                "answer_directly": True,
            },
        ),
        output={
            "status": "invalid_input",
            "action_admitted": False,
            "invalid_fields": ["question", "public_review"],
            "reason": caught.value.prompt,
            "instruction": "Call search_public_info again right now.",
        },
    )
    memory = _memory()
    instructions = model._instructions(
        IDENTITY,
        memory,
        (),
        available_actions=("search_public_info",),
        completed_action=invalid_receipt,
    )
    assert "do not automatically repeat it" not in instructions
    assert "search_public_info" in instructions
    assert "NIET gestart" in instructions

    admitted_questions: list[str] = []

    class LookupActions(Actions):
        action_names = ("search_public_info",)

        def execute(self, current_turn, call):
            parsed = public_arguments(call.arguments)
            admitted_questions.append(parsed["question"])
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "answer": "Vanmiddag meldde NOS om drie uur dat het overleg nog loopt.",
                    "cited_sources": [{"url": "https://nos.nl/artikel/1", "title": "Overleg loopt nog"}],
                },
            )

    def choose(current_turn, context):
        receipt = context["completed_action"]
        if receipt is None:
            return ConversationActionCall(
                "call-1",
                "search_public_info",
                {
                    "question": "Is er nieuws om 15:36 over het kabinet?",
                    "public_review": "public_unlinked",
                    "answer_directly": True,
                },
            )
        if receipt.output["status"] == "invalid_input":
            assert "kloktijden" in receipt.output["reason"]
            return ConversationActionCall(
                "call-2",
                "search_public_info",
                {
                    "question": "Wat is vanmiddag op 13 november 2026 de nieuwste stand van zaken rond het Nederlandse kabinet?",
                    "public_review": "public_unlinked",
                    "answer_directly": True,
                },
            )
        return "Vanmiddag meldde NOS dat het overleg nog steeds loopt."

    foreground = Model(choose)
    reply = LivingConversation(
        _Memory(),
        foreground,
        IDENTITY,
        LookupActions(),
        contact_model=foreground,
    ).respond(turn("msg-1536", "Maar het is 15:36"))
    assert admitted_questions == [
        "Wat is vanmiddag op 13 november 2026 de nieuwste stand van zaken rond het Nederlandse kabinet?"
    ]
    assert reply.text == "Vanmiddag meldde NOS dat het overleg nog steeds loopt."


def test_search_delegated_work_strict_schema_allows_null_cursor(monkeypatch):
    descriptors = {item.name: item for item in delegated_actions_module.PostgresDelegatedWorkActions.conversation_tools()}
    search_props = descriptors["search_delegated_work"].input_schema["properties"]["search"]["properties"]
    assert search_props["before_work_id"]["type"] == ["string", "null"]
    assert search_props["before_work_id"]["minLength"] == 1

    events: dict[str, list] = {}

    class Timeline:
        def __init__(self, *_args):
            pass

        def events(self, key):
            return tuple(events.get(key, ()))

        def create_thread(self, *, thread_id, data, event_type, **_kwargs):
            events[thread_id] = [SimpleNamespace(event_type=event_type, payload={"data": data})]

        def transition(self, *, thread_id, data, event_type, **_kwargs):
            events[thread_id].append(SimpleNamespace(event_type=event_type, payload={"data": data}))

    seen_Store_calls: list[dict] = []

    owner_id = UUID("00000000-0000-0000-0000-000000000001")
    binding_id = UUID("00000000-0000-0000-0000-000000000002")

    from eva.adapters.postgres.operational_timeline import external_timeline_scope

    class Store:
        scope = external_timeline_scope("tenant:test", "install:test", "test scope")

        def validate_admission_authority(self, **_kwargs):
            return None

        def list_owned(self, **kwargs):
            seen_Store_calls.append(kwargs)
            return ()

    monkeypatch.setattr(delegated_actions_module, "PostgresOperationalTimeline", Timeline)
    monkeypatch.setattr(scheduled_work_module, "ScheduledOwnerWork", lambda _service: SimpleNamespace(list=lambda: []))

    service = DelegatedWorkOwnerService(
        Store(),
        owner_principal_id=owner_id,
        principal_binding_id=binding_id,
    )
    actions = delegated_actions_module.PostgresDelegatedWorkActions(object(), service)
    owner_turn = turn("msg-search", "Waarom niet gestart?")
    owner_turn = owner_turn.__class__.private(
        tenant_id="tenant:test",
        installation_id="install:test",
        principal_id=str(owner_id),
        conversation_id="owner-chat",
        message_id="msg-search",
        text="Waarom niet gestart?",
        occurred_at="2026-11-13T14:38:13Z",
    )
    receipt = actions.execute(
        owner_turn,
        ConversationActionCall(
            "call-strict-null",
            "search_delegated_work",
            {"search": {"query": "kabinet", "before_work_id": None}},
        ),
    )
    assert receipt.output["status"] == "verified"
    assert len(seen_Store_calls) == 1
    assert seen_Store_calls[0]["before_work_id"] is None


def test_recent_activity_strict_schema_allows_null_optional_fields():
    props = RECENT_ACTIVITY.input_schema["properties"]
    assert props["local_date"]["type"] == ["string", "null"]
    assert props["source"]["type"] == ["string", "null"]
    assert None in props["source"]["enum"]

    seen: list[tuple[int, str, str | None]] = []

    class PrivateReader:
        def available_for(self, _turn):
            return True

        def read(self, _turn, *, offset, source, local_date):
            seen.append((offset, source, local_date))
            return {"status": "verified", "sources": {}}

    actions = RecentActivityActions(lambda: None, private_reader=PrivateReader())
    private_turn = turn("msg-activity", "Wat heb je gedaan?")
    receipt = actions.execute(
        private_turn,
        ConversationActionCall(
            "act-1",
            "read_recent_activity",
            {"offset": 0, "local_date": None, "source": None},
        ),
    )
    assert receipt.output["status"] == "verified"
    assert seen == [(0, "all", None)]


def test_unrepaired_input_rejection_never_exposes_developer_parameter_names_to_user():
    class FailingSearchActions(Actions):
        action_names = ("search_delegated_work", "toggle_light")

        def execute(self, _turn, call):
            if call.name == "toggle_light":
                raise ConversationClarificationRequired("Welke lamp bedoel je precies?")
            raise ConversationActionInputRejected(
                ("search.before_work_id",),
                prompt=(
                    "De zoekinvoer is ongeldig. Gebruik een zoekterm van 1 tot 200 tekens "
                    "en laat before_work_id weg bij de eerste zoekpagina."
                ),
            )

    def repeated_bad_calls(_turn, _context):
        return ConversationActionCall(
            f"bad-{len(foreground.calls)}",
            "search_delegated_work",
            {"search": {"query": "", "before_work_id": ""}},
        )

    foreground = Model(repeated_bad_calls)
    reply = LivingConversation(
        _Memory(),
        foreground,
        IDENTITY,
        FailingSearchActions(),
        contact_model=foreground,
    ).respond(turn("msg-leak-check", "Waarom is het niet gestart?"))
    assert "before_work_id" not in reply.text
    assert "1 tot 200 tekens" not in reply.text
    assert "nog niet gestart" in reply.text

    clarify_model = Model(lambda *_: ConversationActionCall("light-1", "toggle_light", {}))
    clarify_reply = LivingConversation(
        _Memory(),
        clarify_model,
        IDENTITY,
        FailingSearchActions(),
        contact_model=clarify_model,
    ).respond(turn("msg-clarify", "Zet de lamp aan"))
    assert clarify_reply.text == "Welke lamp bedoel je precies?"
