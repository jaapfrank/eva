from dataclasses import replace
from types import SimpleNamespace

import pytest

from eva.adapters.postgres import delegated_work_actions as module
from eva.application.conversation import (
    ConversationActionCall,
    ConversationConflict,
    ConversationTurn,
)


def test_owner_action_receipt_survives_retry_without_starting_more_work(monkeypatch):
    events = {}
    calls = []

    class Timeline:
        def __init__(self, *_args):
            pass

        def events(self, key):
            return tuple(events.get(key, ()))

        def create_thread(self, *, thread_id, data, event_type, **_kwargs):
            events[thread_id] = [SimpleNamespace(event_type=event_type, payload={"data": data})]

        def transition(self, *, thread_id, data, event_type, **_kwargs):
            events[thread_id].append(SimpleNamespace(event_type=event_type, payload={"data": data}))

    class Service:
        def available_for(self, turn):
            return turn.principal_id == "owner"

        def validate_authority(self, turn):
            assert self.available_for(turn)

        def execute(self, operation, turn, **kwargs):
            calls.append((operation, turn, kwargs))
            return {"operation": operation, "work": {"work_id": "work:one", "state": "admitted"}}

    monkeypatch.setattr(module, "PostgresOperationalTimeline", Timeline)
    service = Service()
    actions = module.PostgresDelegatedWorkActions(object(), service)
    turn = ConversationTurn.private(
        tenant_id="tenant:test", installation_id="install:test", principal_id="owner",
        conversation_id="owner-chat", message_id="message-1", text="Schakel hulp in",
        occurred_at="2026-09-10T10:00:00Z",
    )
    call = ConversationActionCall("call-1", "start_delegated_work", {"task": "Schrijf drie ideeën"})
    receipt = actions.execute(turn, call)
    assert receipt.output["status"] == "verified"
    assert receipt.output["operation_result"]["work"]["state"] == "admitted"
    assert actions.pending(turn) == receipt
    assert actions.execute(turn, call) == receipt
    assert len(calls) == 1
    assert calls[0][2]["request_key"].startswith("conversation:delegated:")
    # A lost action response reuses the prepared identity, even before a final
    # receipt was recorded. The service performs its own durable admission check.
    key = next(iter(events))
    events[key].pop()
    assert actions.pending(turn) == receipt
    assert calls[0][2]["request_key"] == calls[1][2]["request_key"]
    with pytest.raises(ConversationConflict, match="changed after admission"):
        actions.execute(turn, replace(call, arguments={"task": "Andere opdracht"}))
    other_turn = ConversationTurn.private(
        tenant_id=turn.tenant_id, installation_id=turn.installation_id,
        principal_id="someone-else", conversation_id=turn.conversation_id,
        message_id=turn.message_id, text=turn.text, occurred_at=turn.occurred_at,
    )
    with pytest.raises(ConversationConflict, match="private owner"):
        actions.execute(other_turn, call)
    assert actions.pending(other_turn) is None
    assert len(calls) == 2
    assert actions.pending(turn, step_index=1) is None
    followup = ConversationActionCall("call-2", "get_delegated_work", {"work_id": "work:one"})
    later = actions.execute(turn, followup, step_index=1)
    assert actions.pending(turn, step_index=1) == later
    assert calls[-1][2]["request_key"] != calls[0][2]["request_key"]


def test_typed_delegation_catalog_rejects_unexpected_authority_fields():
    actions = module.PostgresDelegatedWorkActions(object(), object())
    descriptors = actions.conversation_tools()
    assert {item.name for item in descriptors} == set(actions.action_names)
    assert all(item.input_schema["additionalProperties"] is False for item in descriptors)
    turn = object()
    with pytest.raises(ConversationConflict, match="arguments differ"):
        actions.execute(turn, ConversationActionCall("call", "start_delegated_work", {
            "task": "Een taak", "owner_principal_id": "another-owner",
        }))
    with pytest.raises(ConversationConflict, match="arguments differ"):
        actions.execute(turn, ConversationActionCall("call", "get_delegated_work", {"work_id": ""}))


@pytest.mark.parametrize("action", ["start_delegated_work", "resume_delegated_work"])
def test_reply_guard_uses_exact_turn_receipt_and_rechecks_current_authority(monkeypatch, action):
    from eva.application.conversation import ConversationActionReceipt
    turn = ConversationTurn.private(
        tenant_id="tenant:test", installation_id="install:test", principal_id="owner",
        conversation_id="owner-chat", message_id="message-1", text="Schakel hulp in",
        occurred_at="2026-09-10T10:00:00Z",
    )
    call = ConversationActionCall("call-1", action, {"task": "Een voorstel"} if action.startswith("start") else {"work_id": "work:previous"})
    receipt = ConversationActionReceipt(call, {"status": "verified", "operation_result": {"work": {"work_id": "work:one", "state": "admitted"}}})
    other = ConversationActionReceipt(ConversationActionCall("unrelated", "read_file", {}), {"status": "verified"})
    events = {}
    class Timeline:
        def __init__(self, *_args): pass
        def events(self, key): return tuple(events.get(key, ()))
    class Service:
        revoked = False
        stopped = False
        reads = []
        def available_for(self, actual): return actual == turn and not self.revoked
        def validate_authority(self, actual):
            if not self.available_for(actual): raise ConversationConflict("private owner")
        def execute(self, operation, actual, **values):
            assert operation == "get" and actual == turn and values["work_id"] == "work:one"
            self.reads.append(values["work_id"])
            return {"work": {"work_id": "work:one", "state": "stopped" if self.stopped else "call_started", "stop_requested": self.stopped}}
    monkeypatch.setattr(module, "PostgresOperationalTimeline", Timeline)
    service = Service()
    actions = module.PostgresDelegatedWorkActions(object(), service)
    key = actions._thread_id(turn, 1)
    events[key] = [SimpleNamespace(event_type="DELEGATED_ACTION_PREPARED", payload={"data": {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}}),
        SimpleNamespace(event_type="DELEGATED_ACTION_COMPLETED", payload={"data": {"output": receipt.output}})]
    assert actions.reply_guard(turn, (other,)) is None
    refused = ConversationActionReceipt(call, {"status": "verified", "operation_result": {"status": "not_resumable", "work": {"work_id": "work:previous"}}})
    assert actions.reply_guard(turn, (refused,)) is None
    guard = actions.reply_guard(turn, (other, receipt))
    assert guard("Normaal antwoord") == "Normaal antwoord"
    service.stopped = True
    corrected = guard("Oud startantwoord")
    assert "stopopdracht" in corrected and "work:one" in corrected
    assert guard("Oud startantwoord") == corrected  # Replays do not admit new work.
    assert service.reads == ["work:one"] * 3
    service.revoked = True
    with pytest.raises(ConversationConflict, match="private owner"):
        guard("Mag niet worden vrijgegeven")
    service.revoked = False
    with pytest.raises(ConversationConflict, match="exact action receipt"):
        actions.reply_guard(turn, (receipt,))("Wrong step index")
    with pytest.raises(ConversationConflict, match="private owner"):
        actions.reply_guard(replace(turn, installation_id="foreign-installation"), (other, receipt))("No access")
    forged = ConversationActionReceipt(call, {"status": "verified", "operation_result": {"work": {"work_id": "work:foreign", "state": "admitted"}}})
    with pytest.raises(ConversationConflict, match="exact action receipt"):
        actions.reply_guard(turn, (other, forged))("No forged work")


@pytest.mark.parametrize("with_other_result", [False, True])
def test_mixed_reply_names_only_stopped_helper_and_discloses_other_results(monkeypatch, with_other_result):
    from eva.application.conversation import ConversationActionReceipt
    turn = ConversationTurn.private(
        tenant_id="tenant:test", installation_id="install:test", principal_id="owner",
        conversation_id="chat", message_id="mixed", text="Twee helpers en een bestand",
        occurred_at="2026-09-10T10:00:00Z",
    )
    events = {}
    class Timeline:
        def __init__(self, *_args): pass
        def events(self, key): return tuple(events.get(key, ()))
    class Service:
        reads = []
        def available_for(self, actual): return actual == turn
        def validate_authority(self, actual): assert actual == turn
        def execute(self, operation, actual, *, work_id, **_):
            assert operation == "get" and actual == turn
            self.reads.append(work_id)
            return {"work": {"work_id": work_id, "state": "stopped" if work_id == "work:stopped" else "call_started", "stop_requested": work_id == "work:stopped"}}
    monkeypatch.setattr(module, "PostgresOperationalTimeline", Timeline)
    service = Service()
    actions = module.PostgresDelegatedWorkActions(object(), service)
    receipts = []
    for index, work_id in enumerate(("work:stopped", "work:other")):
        call = ConversationActionCall(f"call-{index}", "start_delegated_work", {"task": f"Voorstel {index}"})
        receipt = ConversationActionReceipt(call, {"status": "verified", "operation_result": {"work": {"work_id": work_id, "state": "admitted"}}})
        events[actions._thread_id(turn, index)] = [
            SimpleNamespace(event_type="DELEGATED_ACTION_PREPARED", payload={"data": {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}}),
            SimpleNamespace(event_type="DELEGATED_ACTION_COMPLETED", payload={"data": {"output": receipt.output}}),
        ]
        receipts.append(receipt)
    other_output = {"status": "verified", "content": "Een afzonderlijk behouden bestandsresultaat."}
    if with_other_result:
        receipts.append(ConversationActionReceipt(ConversationActionCall("file-call", "read_file", {}), other_output))
    original = "Beide helpers zijn bezig. Onbeoordeelde modeltekst over het bestand."
    corrected = actions.reply_guard(turn, tuple(receipts))(original)
    assert corrected.startswith("Je stopopdracht voor work:stopped is al bewaard.")
    assert "Andere hulpopdrachten in dit verzoek: work:other." in corrected
    assert "vraag" in corrected.lower() and "status apart" in corrected
    assert "beide" not in corrected.lower() and "onbeoordeelde" not in corrected.lower()
    assert ("terugkoppelingen blijven beschikbaar" in corrected) == with_other_result
    assert ("Deze melding trekt die handelingen niet in" in corrected) == with_other_result
    assert service.reads == ["work:stopped", "work:other"]
    assert other_output == {"status": "verified", "content": "Een afzonderlijk behouden bestandsresultaat."}


@pytest.fixture
def acceptance_actions(monkeypatch):
    events, calls = {}, []
    class Timeline:
        def __init__(self, *_): pass
        def events(self, key): return tuple(events.get(key, ()))
        def create_thread(self, *, thread_id, data, event_type, **_):
            events[thread_id] = [SimpleNamespace(event_type=event_type, payload={"data": data})]
        def transition(self, *, thread_id, data, event_type, **_):
            events[thread_id].append(SimpleNamespace(event_type=event_type, payload={"data": data}))
    class Service:
        research_profile = digital_profile = object()
        result = None
        def available_for(self, _): return True
        def validate_authority(self, _): pass
        def execute(self, operation, turn, **kwargs):
            calls.append(kwargs)
            return self.result or {"contract": "EvaDelegatedWorkOwnerResult/v1",
                "operation": operation, "created": True,
                "work": {"work_id": "work:one", "state": "admitted", "stop_requested": False}}
    monkeypatch.setattr(module, "PostgresOperationalTimeline", Timeline)
    service = Service()
    turn = ConversationTurn.private("tenant:test", "install:test", "owner", "chat", "message",
                                    "Please investigate", "2026-09-10T10:00:00Z")
    return module.PostgresDelegatedWorkActions(object(), service), service, turn, calls


def test_acceptance_is_optional_only_for_start_tools():
    for tool in module.PostgresDelegatedWorkActions.conversation_tools(include_research=True, include_digital=True):
        schema = tool.input_schema
        assert ("acceptance_message" in schema["properties"]) == tool.name.startswith("start_")
        assert "acceptance_message" not in schema["required"]


@pytest.mark.parametrize("name,key,value", [
    ("start_delegated_work", "task", "Write an outline"),
    ("start_digital_work", "task", "List available workspace tools"),
    ("start_background_research", "research", {
        "contract": "PublicResearchRequest/v1", "schema_version": 1,
        "question": "What does Home Assistant Cloud offer?", "title": "Cloud features",
        "options": ["Home Assistant Cloud"], "research_mode": "topic", "public_review": "public_unlinked"}),
])
def test_acceptance_is_journaled_exactly_and_not_forwarded_to_service(acceptance_actions, name, key, value):
    actions, service, turn, calls = acceptance_actions
    message = "Ik zoek het voor je uit; we kunnen ondertussen doorpraten."
    call = ConversationActionCall("call", name, {key: value, "acceptance_message": message})
    receipt = actions.execute(turn, call)
    assert receipt.output["acceptance_message"] == message
    assert calls == [{key: value, "request_key": actions._thread_id(turn, 0)}]
    assert actions.pending(turn) == receipt
    assert actions.execute(turn, call) == receipt
    assert len(calls) == 1
    with pytest.raises(ConversationConflict, match="changed after admission"):
        actions.execute(turn, replace(call, arguments={key: value, "acceptance_message": "Different"}))


@pytest.mark.parametrize("message", [None, 42, "", "   ", "x" * 241])
def test_invalid_acceptance_does_not_admit_work(acceptance_actions, message):
    actions, _, turn, calls = acceptance_actions
    with pytest.raises(ConversationConflict, match="arguments differ"):
        actions.execute(turn, ConversationActionCall("call", "start_delegated_work",
            {"task": "Outline", "acceptance_message": message}))
    assert not calls


@pytest.mark.parametrize("changes", [
    {"status": "not_found"}, {"status": "not_resumable"}, {"status": "failed"},
    {"work": None}, {"work": {"work_id": "work:one", "state": "stopped", "stop_requested": True}},
    {"work": {"work_id": "work:one", "state": "failed", "stop_requested": False}},
    {"work": {"work_id": "work:one", "state": "succeeded", "stop_requested": False}},
    {"work": {"work_id": "work:one", "state": "admitted", "stop_requested": True}},
])
def test_terminal_or_rejected_admission_does_not_publish_acceptance(acceptance_actions, changes):
    actions, service, turn, _ = acceptance_actions
    service.result = {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": "start",
        "created": False, "work": {"work_id": "work:one", "state": "admitted", "stop_requested": False}, **changes}
    receipt = actions.execute(turn, ConversationActionCall("call", "start_delegated_work",
        {"task": "Outline", "acceptance_message": "I will investigate."}))
    assert "acceptance_message" not in receipt.output


def test_household_admission_uses_same_acceptance_gate(acceptance_actions):
    actions, service, turn, _ = acceptance_actions
    service.result = {"created": True, "prior_outcome_unknown": False,
        "work": {"work_id": "work:household:one", "state": "call_started", "stop_requested": False}}
    receipt = actions.execute(turn, ConversationActionCall("call", "start_background_research",
        {"research": {"contract": "PublicResearchRequest/v1", "schema_version": 1,
            "question": "What does Home Assistant Cloud offer?", "title": "Cloud features",
            "options": ["Home Assistant Cloud"], "research_mode": "topic",
            "public_review": "public_unlinked"}, "acceptance_message": "I will investigate."}))
    assert receipt.output["acceptance_message"] == "I will investigate."


@pytest.mark.parametrize("arguments", [
    {}, {"task": ""},
    {"task": "Save draft", "acceptance_message": ""},
    {"task": "Save draft", "unexpected": "private-value"},
])
def test_invalid_project_admission_returns_repair_before_timeline_or_effect(arguments, monkeypatch):
    from eva.application.conversation import ConversationActionInputRejected
    actions = module.PostgresDelegatedWorkActions(object(), SimpleNamespace(digital_profile=object()))
    monkeypatch.setattr(actions, "_timeline", lambda *_: pytest.fail("Invalid input reached storage"))
    with pytest.raises(ConversationActionInputRejected) as caught:
        actions.execute(object(), ConversationActionCall("invalid", "start_digital_work", arguments))
    assert caught.value.fields == ("task", "acceptance_message")
    assert "private-value" not in str(caught.value)


def test_project_acceptance_can_be_null_in_strict_schema_and_reaches_admission(monkeypatch):
    from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
    actions = module.PostgresDelegatedWorkActions(object(), SimpleNamespace(digital_profile=object()))
    descriptor = next(d for d in actions.conversation_tools(include_digital=True) if d.name == "start_digital_work")
    schema = OpenAIJsonSchemaTransformer(descriptor.input_schema, strict=True).walk()
    assert "acceptance_message" in schema["required"]
    assert "null" in schema["properties"]["acceptance_message"]["type"]
    class ReachedAdmission(Exception):
        pass
    def reached(*_):
        raise ReachedAdmission
    monkeypatch.setattr(actions, "_timeline", reached)
    with pytest.raises(ReachedAdmission):
        actions.execute(object(), ConversationActionCall("valid", "start_digital_work", {
            "task": "Verify saved draft only", "acceptance_message": None,
        }))
