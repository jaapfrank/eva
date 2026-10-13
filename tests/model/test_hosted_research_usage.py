"""Providerless SSE usage evidence, independent of cited or guessed URLs."""
from io import BytesIO
import json

import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model import pydantic_conversation as module
from eva.application.conversation import ConversationActionCall


def search(item_id="search-1", status="completed", action="search"):
    return {"id": item_id, "type": "web_search_call", "status": status,
            "action": {"type": action, "url": "https://example.com", "sources": []}}


def done(item):
    return {"type": "response.output_item.done", "item": item}


def terminal(output=(), status="completed", error=None):
    return {"type": "response.completed", "response": {
        "status": status, "error": error, "output": list(output),
        "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}}}


def run(monkeypatch, tmp_path, events, *, parser=None):
    model = module.PydanticConversationModel(auth_file=tmp_path / "never-read")
    monkeypatch.setattr(model, "_load_auth", lambda: ("offline-token", "offline-account"))
    response = BytesIO("".join("data: " + json.dumps(event) + "\n\n" for event in events).encode())
    requests = []
    def open_response(*args, **kwargs):
        requests.append(args)
        return response
    monkeypatch.setattr(module.urllib.request, "urlopen", open_response)
    result = model._request_using({"input": [], "tools": []},
        parser or model._parse_research_sse, include_usage=True)
    assert response.closed
    assert len(requests) == 1
    return result


@pytest.mark.parametrize("action", ["search", "open_page"])
@pytest.mark.parametrize("proof", ["item_done", "completed_event", "terminal_output"])
def test_counts_completed_hosted_search_and_page_open(monkeypatch, tmp_path, action, proof):
    item = search(action=action)
    events = ([done(item), terminal()] if proof == "item_done" else
              [{"type": "response.web_search_call.completed", "item_id": item["id"]}, terminal()]
              if proof == "completed_event" else [terminal([item])])
    parsed, usage = run(monkeypatch, tmp_path, events)
    assert len(parsed) == 3  # Existing text/cited/consulted contract.
    assert usage.tool_calls == 1
    assert (usage.input_tokens, usage.output_tokens, usage.model_attempts) == (20, 5, 1)


def test_deduplicates_completed_event_done_and_terminal_echo(monkeypatch, tmp_path):
    item = search()
    events = [{"type": "response.output_item.added", "item": search(status="in_progress")},
        {"type": "response.web_search_call.searching", "item_id": item["id"]},
        {"type": "response.web_search_call.completed", "item_id": item["id"]},
        done(item), done(item), terminal([item, search("open-2", action="open_page")])]
    _, usage = run(monkeypatch, tmp_path, events)
    assert usage.tool_calls == 2


@pytest.mark.parametrize("items", [[], [search(status="in_progress")], [search(status="failed")]])
def test_no_completed_tool_is_zero_even_with_url_text(monkeypatch, tmp_path, items):
    events = [{"type": "response.output_text.delta", "delta": "https://example.com is a source."}]
    _, usage = run(monkeypatch, tmp_path, [*events, *(done(item) for item in items), terminal(items)])
    assert usage.tool_calls == 0


@pytest.mark.parametrize("kind", ["error", "response.failed", "response.incomplete", "response.cancelled"])
def test_failed_stream_never_returns_success_usage(monkeypatch, tmp_path, kind):
    with pytest.raises(ConversationModelError):
        run(monkeypatch, tmp_path, [done(search()), {"type": kind}, terminal()])


@pytest.mark.parametrize("ending", [[], [terminal(status="incomplete")], [terminal(error={"message": "failure"})]])
def test_missing_or_invalid_completion_rejects_usage(monkeypatch, tmp_path, ending):
    with pytest.raises(ConversationModelError):
        run(monkeypatch, tmp_path, [done(search()), *ending])


def test_completed_search_without_identity_is_not_counted_as_proven(monkeypatch, tmp_path):
    with pytest.raises(ConversationModelError, match="no item ID"):
        run(monkeypatch, tmp_path, [done(search(item_id=None)), terminal()])


@pytest.mark.parametrize("has_call", [False, True])
def test_ordinary_function_call_count_unchanged(monkeypatch, tmp_path, has_call):
    def ordinary_parser(stream):
        list(stream)
        return "", ConversationActionCall("call-1", "example", {}) if has_call else None
    _, usage = run(monkeypatch, tmp_path, [terminal([search(), search("search-2")])], parser=ordinary_parser)
    assert usage.tool_calls == int(has_call)
