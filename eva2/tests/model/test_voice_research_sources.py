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
