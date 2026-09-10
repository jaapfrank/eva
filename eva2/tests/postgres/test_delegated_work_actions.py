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
