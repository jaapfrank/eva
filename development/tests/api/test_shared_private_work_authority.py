"""The core fences personal result exposure at the device session's expiry."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest

from eva.application.conversation import ConversationTurn
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime


@pytest.mark.parametrize("operation", ["poll", "authorize"])
def test_speaker_result_is_suppressed_when_confirmation_expires(operation):
    session_id = str(UUID(int=23))
    conversation_id = f"voice:{session_id}"
    binding = dict(session_id=session_id, tenant_id="tenant", installation_id="installation")
    owner = "fixture:owner"
    checks, committed = [], []

    def principal(value):
        assert value == binding
        checks.append(value)
        return None if committed else owner

    def private_return(turn, action, **identity):
        assert action == operation
        committed.append(action)
        return {"result": "PRIVATE-FIXTURE", "state": "claimed"}

    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._voice_identity = SimpleNamespace(data=lambda sid: {"session": binding},
        principal=principal)
    runtime._delegated_work_owner_service = SimpleNamespace(private_return=private_return)
    turn = ConversationTurn.private("tenant", "installation", owner, conversation_id,
        f"{conversation_id}:{session_id}", "Voice session control", "2026-11-06T10:00:00Z")
    with pytest.raises(ValueError, match="authority changed"):
        runtime.private_work_return(turn, operation)
    assert len(checks) == 2 and committed == [operation]

    # Authenticated dashboard voice carries its own separate grant/ID.
    checks.clear()
    dashboard = replace(turn, message_id=f"return:{session_id}")
    assert runtime.private_work_return(dashboard, operation)["result"] == "PRIVATE-FIXTURE"
    assert not checks


def test_speaker_without_confirmed_owner_cannot_reach_private_store():
    session_id = str(UUID(int=24))
    conversation_id = f"voice:{session_id}"
    turn = ConversationTurn.private("tenant", "installation", "fixture:owner", conversation_id,
        f"{conversation_id}:{session_id}", "Voice session control", "2026-11-06T10:00:00Z")
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._voice_identity = None
    runtime._delegated_work_owner_service = SimpleNamespace(
        private_return=lambda *args, **kwargs: pytest.fail("unconfirmed speaker reached the store"))
    with pytest.raises(ValueError, match="authority unavailable"):
        runtime.private_work_return(turn, "poll")
