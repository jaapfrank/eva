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


class _TaskResearchJournal:
    def __init__(self, usage):
        self.prior = usage
        self.admitted = []
        self.recorded = []

    def model_usage(self, turn):
        from eva.application.conversation import ConversationModelUsage
        return ConversationModelUsage.aggregate((self.prior, *(
            record[1] for record in self.recorded)))

    def begin_model_request(self, turn):
        request_id = "offline-research-" + str(len(self.admitted) + 1)
        self.admitted.append(request_id)
        return request_id

    def complete_model_request(self, turn, request_id, usage, *, outcome="succeeded"):
        assert request_id in self.admitted
        assert not any(record[0] == request_id for record in self.recorded)
        self.recorded.append((request_id, usage, outcome))


def _task_research_phase(monkeypatch, tmp_path, *, output_tokens=1211, terminal_usage=True):
    import json
    from types import SimpleNamespace
    from eva.adapters.model import pydantic_conversation as module
    from eva.application.conversation import ConversationModelUsage
    from eva.bootstrap.background_research import AccountedResearch
    from tests.model.test_conversation_model import _runtime_budget

    budget = _runtime_budget(max_input_tokens=64000, max_output_tokens=1200)
    model = PydanticConversationModel(auth_file=tmp_path / "unused", resource_budget=budget)
    monkeypatch.setattr(model, "_load_auth", lambda: ("offline-token", "offline-account"))
    searches = [{"id": "search-" + str(i), "type": "web_search_call", "status": "completed",
                 "action": {"type": "search", "url": "https://example.org/specification",
                            "sources": []}} for i in range(7)]
    message = {"type": "message", "content": [{"type": "output_text",
        "text": "The public specification describes the supported interface.",
        "annotations": [{"type": "url_citation", "url": "https://example.org/specification",
                         "title": "Public specification"}]}]}
    response = {"status": "completed", "output": [*searches, message]}
    if terminal_usage:
        response["usage"] = {"input_tokens": 38391, "output_tokens": output_tokens,
                             "total_tokens": 38391 + output_tokens}
    wire = ("data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n").encode()
    requests = []
    def open_response(*args, **kwargs):
        stream = io.BytesIO(wire)
        stream.submitted_body = args[0].data
        requests.append(stream)
        return stream
    monkeypatch.setattr(module, "open_research_response", open_response)
    monkeypatch.setattr(module.urllib.request, "urlopen", open_response)
    turn = ConversationTurn.private("tenant:test", "installation:test", "owner", "chat", "source",
        "Read the public specification.", "2026-10-24T13:13:57Z")
    journal = _TaskResearchJournal(ConversationModelUsage.measured(
        model_attempts=1, tool_calls=1, input_tokens=10, output_tokens=2))
    grant = SimpleNamespace(maximum_model_runs=32, maximum_input_tokens=200000,
                            maximum_output_tokens=38400)
    stopped = []
    def authorize(source):
        from eva.application.conversation import ConversationConflict
        assert source == turn
        if stopped:
            raise ConversationConflict("stopped")
    phase = AccountedResearch(model, turn=turn, journal=journal, authorize=authorize,
        grant=grant, input_bound=64000, output_bound=1200)
    args = {"question": "What interface is documented in the public specification?",
            "title": "Public interface", "options": ("https://example.org/specification",),
            "research_mode": "topic", "researched_at": turn.occurred_at}
    return model, phase, turn, journal, grant, stopped, requests, args


def test_task_research_uses_its_admitted_phase_budget_without_changing_chat(monkeypatch, tmp_path):
    import hashlib
    from eva.application.conversation import ConversationConflict
    from eva.contracts.canonical_json import canonical_json_bytes
    model, phase, turn, journal, grant, stopped, requests, args = _task_research_phase(monkeypatch, tmp_path)
    with background_execution(BackgroundExecutionScope(turn, "work:offline-research")):
        prepared_body = model.research_request_payload(question=args["question"],
            options=args["options"], research_mode=args["research_mode"])
        artifact = phase.research_current_options(**args)
    assert len(requests) == 1 and requests[0].closed
    assert requests[0].submitted_body == canonical_json_bytes(prepared_body)
    assert len(journal.admitted) == len(journal.recorded) == 1
    assert journal.recorded[0][2] == "succeeded"
    assert (artifact.usage.input_tokens, artifact.usage.output_tokens, artifact.usage.tool_calls) == (38391, 1211, 7)
    assert artifact.sources[0].url == "https://example.org/specification"
    assert journal.model_usage(turn).output_tokens == 1213
    assert model._resource_budget.max_output_tokens == 1200
    assert grant.maximum_output_tokens == 38400
    with pytest.raises(ConversationModelError) as caught:
        model.research_current_options(**args)
    fingerprint = hashlib.sha256((type(caught.value).__name__ + ": " + str(caught.value)).encode()).hexdigest()
    assert fingerprint == "3f731937657d3f9ba91d53f807608083a70fee5e650d607ec57a4115a2b3843d"
    assert caught.value.model_usage.output_tokens == 1211
    assert len(requests) == 2 and all(stream.closed for stream in requests)
    with pytest.raises((ConversationModelError, ConversationConflict, ValueError)):
        model.research_current_options(**args, output_tokens_limit=38398)
    assert len(requests) == 2  # A host override requires the admitted background context.


@pytest.mark.parametrize("blocker", ["stopped", "unknown_usage", "partial_usage", "remaining_input", "remaining_output", "requests"])
def test_task_research_phase_never_renews_a_stopped_or_exhausted_grant(monkeypatch, tmp_path, blocker):
    from eva.application.conversation import ConversationConflict, ConversationExecutionLimitReached, ConversationModelUsage
    _, phase, turn, journal, grant, stopped, requests, args = _task_research_phase(monkeypatch, tmp_path)
    if blocker == "stopped":
        stopped.append(True)
    elif blocker == "unknown_usage":
        journal.prior = ConversationModelUsage.unmeasured()
    elif blocker == "partial_usage":
        journal.prior = ConversationModelUsage.partial(
            model_attempts=1, tool_calls=1, input_tokens=10, output_tokens=2)
    elif blocker == "remaining_input":
        grant.maximum_input_tokens = 64009
    elif blocker == "remaining_output":
        grant.maximum_output_tokens = 1201
    else:
        grant.maximum_model_runs = 1
    with background_execution(BackgroundExecutionScope(turn, "work:offline-research")):
        with pytest.raises((ConversationConflict, ConversationExecutionLimitReached)):
            phase.research_current_options(**args)
    assert requests == journal.admitted == journal.recorded == []


@pytest.mark.parametrize("failure", ["remaining_task_output", "task_input", "unknown_response_usage"])
def test_task_research_preserves_actual_failure_usage_without_a_second_request(monkeypatch, tmp_path, failure):
    from eva.application.conversation import ConversationConflict, ConversationExecutionLimitReached
    _, phase, turn, journal, grant, stopped, requests, args = _task_research_phase(
        monkeypatch, tmp_path, output_tokens=38400 if failure == "remaining_task_output" else 1211,
        terminal_usage=failure != "unknown_response_usage")
    if failure == "task_input":
        # The submitted envelope fits; hosted retrieval can add actual input.
        phase.input_bound = 20000
        grant.maximum_input_tokens = 30000
    with background_execution(BackgroundExecutionScope(turn, "work:offline-research")):
        with pytest.raises((ConversationModelError, ConversationConflict, ConversationExecutionLimitReached)) as caught:
            phase.research_current_options(**args)
    assert len(requests) == 1 and requests[0].closed
    assert len(journal.admitted) == len(journal.recorded) == 1
    usage = journal.recorded[0][1]
    if failure == "unknown_response_usage":
        assert usage.measurement == "unmeasured"
        assert usage.input_tokens is usage.output_tokens is None
    else:
        assert usage.measurement == "measured"
        assert usage.input_tokens == 38391
        assert usage.output_tokens == (38400 if failure == "remaining_task_output" else 1211)
        if failure == "task_input":
            assert isinstance(caught.value, ConversationExecutionLimitReached)
            assert journal.recorded[0][2] == "succeeded"
        else:
            assert caught.value.model_usage == usage
            assert journal.recorded[0][2] == "failed"
