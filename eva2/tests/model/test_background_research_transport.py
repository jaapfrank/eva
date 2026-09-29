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


def test_background_research_deadline_and_fingerprint_match_host_phase(tmp_path, monkeypatch):
    model = PydanticConversationModel(auth_file=tmp_path / "unused", research_timeout_seconds=120)
    observed = []
    monkeypatch.setattr(model, "_request_using", lambda *args, **kwargs: observed.append(kwargs))
    assert model.native_research_timeout_seconds() == 55
    turn = ConversationTurn.private("t", "i", "p", "c", "m", "Research", "2026-09-29T06:00:00Z")
    with background_execution(BackgroundExecutionScope(turn, "work:test")):
        assert model.native_research_timeout_seconds() == 120
        model._request_research({})
    assert observed[0]["timeout_seconds"] == 120
    assert model.native_research_timeout_seconds() == 55


@pytest.mark.parametrize("name,seconds,allowed", [
    ("research.public_sources", 125, True),
    ("research.public_sources", 126, False),
    ("other.tool", 61, False),
    ("other.tool", 60, True),
])
def test_only_research_accepts_extended_tool_deadline(name, seconds, allowed):
    from eva.application.conversation import ConversationToolPreparation
    from eva.application.orchestration import ToolRequest
    def construct():
        ConversationToolPreparation(tool_name=name, arguments={}, expected_proof={}, receipt_output={},
            execution_timeout_seconds=seconds)
        ToolRequest(thread_id="t", operation_id="o", checkpoint_prefix="p", tool_name=name,
            arguments={}, expected_proof={}, execution_timeout_seconds=seconds)
    if allowed:
        construct()
    else:
        with pytest.raises(ValueError):
            construct()
