"""Provider source shapes observed during a real overlapping voice task."""
from io import BytesIO
import json
import pytest
from eva.adapters.model.pydantic_conversation import PydanticConversationModel, _source_matches_named_option

@pytest.mark.parametrize("status,expected", [("completed", 1), ("failed", 0), ("in_progress", 0)])
def test_completed_open_page_is_preserved_as_consulted_source(status, expected):
    event = {"type": "response.output_item.done", "item": {"type": "web_search_call", "status": status,
        "action": {"type": "open_page", "url": "https://github.com/livekit/agents/releases"}}}
    payload = ('data: ' + json.dumps(event) + '\n\n').encode()
    text, cited, consulted = PydanticConversationModel._parse_research_sse(BytesIO(payload))
    assert not text and not cited
    assert len(consulted) == expected
    if expected: assert consulted[0].url == event["item"]["action"]["url"]

@pytest.mark.parametrize("url,allowed", [
    ("https://github.com/livekit/agents/releases", True),
    ("https://github.com/unrelated/agents/releases", False),
    ("https://github.com/livekit/agents-extra", False),
    ("https://github.com/another/livekit-agents", True),
])
def test_repository_label_accepts_exact_owner_and_repository(url, allowed):
    # Repository-only matching is existing behavior; owner+repo adds the compound name.
    assert _source_matches_named_option(url, "LiveKit Agents") is allowed


CITATION = {"type": "url_citation", "url": "https://example.com/reference", "title": "Reference"}
PART = {"type": "output_text", "text": "Answer.", "annotations": [CITATION]}
ITEM = {"type": "message", "id": "message-1", "content": [PART]}


def parse_events(events):
    raw = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    return PydanticConversationModel._parse_research_sse(BytesIO(raw.encode()))


@pytest.mark.parametrize("carrier", [
    {"type": "response.output_text.annotation.added", "annotation": CITATION},
    {"type": "response.content_part.done", "item_id": "message-1", "content_index": 0, "part": PART},
    {"type": "response.completed", "response": {"output": [ITEM]}},
])
def test_citations_survive_supported_carriers_without_output_item_done(carrier):
    text, cited, consulted = parse_events([
        {"type": "response.output_text.delta", "delta": "Answer."}, carrier])
    assert text == "Answer."
    assert [source.url for source in cited] == [CITATION["url"]]
    assert not consulted


@pytest.mark.parametrize("with_deltas", [False, True])
def test_duplicate_snapshots_do_not_duplicate_fallback_text_or_citations(with_deltas):
    events = [{"type": "response.output_text.delta", "delta": "Answer."}] if with_deltas else []
    events += [
        {"type": "response.output_text.annotation.added", "annotation": CITATION},
        {"type": "response.content_part.done", "item_id": "message-1", "content_index": 0, "part": PART},
        {"type": "response.output_item.done", "item": ITEM},
        {"type": "response.output_item.done", "item": ITEM},
        {"type": "response.completed", "response": {"output": [ITEM]}},
        {"type": "response.completed", "response": {"output": [ITEM]}},
    ]
    text, cited, consulted = parse_events(events)
    assert text == "Answer." and len(cited) == 1 and not consulted


def test_part_only_fallback_and_final_snapshot_preserve_distinct_identical_parts():
    part = {"type": "output_text", "text": "Again.", "annotations": []}
    events = [{"type": "response.content_part.done", "item_id": "message", "content_index": i, "part": part} for i in range(2)]
    assert parse_events(events)[0] == "Again.Again."
    events.append({"type": "response.completed", "response": {"output": [{"type": "message", "content": [part, part]}]}})
    assert parse_events(events)[0] == "Again.Again."


def test_consulted_search_and_markdown_urls_are_not_promoted_to_citations():
    text, cited, consulted = parse_events([
        {"type": "response.output_text.delta", "delta": "See https://example.com/reference"},
        {"type": "response.completed", "response": {"output": [{"type": "web_search_call", "status": "completed",
         "action": {"type": "search", "sources": [{"url": CITATION["url"]}]}}]}},
    ])
    assert text and not cited and len(consulted) == 1


def test_invalid_annotation_url_still_fails_and_provider_failure_is_not_hidden():
    from eva.adapters.model.pydantic_conversation import ConversationModelError
    with pytest.raises(ConversationModelError):
        parse_events([{"type": "response.output_text.annotation.added", "annotation": {**CITATION, "url": "file:///private"}}])
    with pytest.raises(ConversationModelError):
        parse_events([{"type": "response.completed", "response": {"output": [ITEM]}}, {"type": "response.failed"}])


def test_legacy_items_without_ids_preserve_identical_text():
    item = {"type": "message", "content": [{"text": "Again.", "annotations": []}]}
    event = {"type": "response.output_item.done", "item": item}
    assert parse_events([event, event])[0] == "Again.Again."
