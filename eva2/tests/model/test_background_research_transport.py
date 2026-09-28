"""Research under a durable task cannot make an unreserved credential retry."""
import io
import urllib.error
import pytest
from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationTurn


def test_background_research_makes_one_network_attempt_on_credential_rejection(tmp_path, monkeypatch):
    import eva.adapters.model.pydantic_conversation as module
    model = PydanticConversationModel(auth_file=tmp_path/"unused")
    monkeypatch.setattr(model, "_load_auth", lambda: ("opaque-test-access", "test-account"))
    calls = []
    def rejected(request, **kwargs):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 401, "rejected", {}, io.BytesIO(b""))
    monkeypatch.setattr(module, "open_research_response", rejected)
    turn = ConversationTurn.private("t", "i", "p", "c", "m", "Research", "2026-09-28T19:00:00Z")
    with background_execution(BackgroundExecutionScope(turn, "work:test")):
        with pytest.raises(ConversationModelError):
            model.research_current_options(question="Read the specification", title="Source",
                options=("https://docs.example/specification",), research_mode="topic",
                researched_at=turn.occurred_at)
    assert len(calls) == 1
